# Ch 27 — OLLVM 與 native 混淆的去混淆

> **目標**：把 native 層最主流的混淆器 **OLLVM（Obfuscator-LLVM）** 的三招——**控制流平坦化（control-flow flattening, `-fla`）、虛假控制流（bogus control flow, `-bcf`）、指令替換（instruction substitution, `-sub`）**——從「編譯器怎麼生出來的」講到「逆向者怎麼還原回去」。重點深挖**去平坦化**：怎麼在一堆 `.so` 反編譯輸出裡認出 dispatcher、用 **angr 符號執行**還原真實控制流、以及 **D810**（Hex-Rays 反編譯期去混淆外掛）這類工具的原理。這章是 Ch 23（native 演算法識別）之後、對付「值錢邏輯藏在混淆 `.so`」的關鍵一環。

> **環境**：OLLVM 的去混淆示範建立在 IDA/Ghidra（Ch 22）與 angr 之上。本 repo 沙箱有 `python3` 但**沒有 angr、沒有 IDA、沒有實際的 OLLVM 編譯產物**，所以本章所有「angr 跑出結果」「D810 還原後的反編譯」都標「**未實測，理論預期行為**」並附上你在自己環境驗證的步驟；控制流圖與狀態機的邏輯示範用 Python 實跑，標「**實際輸出**」。OLLVM 產生 ARM64/x86 native 碼，這是真機 `.so` 的主流形態（AVD 是 x86_64，逆真機 `.so` 時架構會是 ARM64，Ch 20 提過）。

## 為什麼需要這個？

前一章的 DEX 層混淆再兇，jadx 還是給你近似 Java；OLLVM 不一樣，它動的是 **LLVM IR**，在編譯 native 程式碼時就把控制流攪爛，IDA/Ghidra 反編譯出來是一個幾百個 `case` 的巨型 `switch`、變數莫名其妙、真正的邏輯淹沒在虛假分支裡。

開發者把「值錢的東西」——簽名演算法、加密金鑰派生、風控、License 校驗——搬進 `.so` 已經是為了擋只會 Java 層的人（Ch 1 說過）；再套 OLLVM，是為了連逆 native 的人也拖到崩潰。你在 Ch 23 學會識別 native 演算法，但如果那個 AES 的控制流被平坦化了，你連「這是 AES」都認不出來，因為輪函式的結構被打散了。

所以這章要教的不是「怎麼讀更努力」，是**還原**：把 OLLVM 加上去的變換**逆掉**，讓 `.so` 回到接近未混淆的樣子，再套 Ch 23 的識別功夫。理解 OLLVM 怎麼生成，是還原的前提——你得先知道它加了什麼，才知道怎麼減回去。

## 先建立直覺：OLLVM 是一組 LLVM pass

OLLVM 是在 LLVM 編譯管線裡插入的混淆 pass。原始碼 → Clang 前端 → **LLVM IR** → （OLLVM 的混淆 pass 在這裡動手）→ 後端生成機器碼。關鍵：**它在 IR 層混淆，所以與語言、目標架構無關**——同一套混淆對 ARM64、x86 都適用，因為它作用在後端之前。

```
   C/C++ 原始碼
       │ Clang 前端
       ▼
   LLVM IR  ──┬── -sub  指令替換：a+b 變成 a-(-b) 等等價形式
              ├── -bcf  虛假控制流：插入不透明謂詞守護的假分支
              └── -fla  控制流平坦化：把 CFG 壓成狀態機
       │ LLVM 後端
       ▼
   ARM64 / x86 機器碼（就是你在 IDA 看到的那團東西）
```

三招的殺傷力遞增：

- **`-sub`（指令替換）**：最輕，把單條算術/邏輯運算換成一串等價運算。純粹增加閱讀量，不改控制流。逆向影響：小，模式化，好還原。
- **`-bcf`（虛假控制流）**：中等，靠**不透明謂詞（opaque predicate）**——結果編譯期恆定但看起來像動態的條件——掛上永遠不執行的死程式碼。逆向影響：撐大 CFG、誤導，但假分支可識別可剪。
- **`-fla`（控制流平坦化）**：最兇，也是本章主戲。把函式所有基本區塊攤平成一個 `while(1) switch(state)` 的**大 dispatcher**，真實執行順序藏在 `state` 變數的更新裡。逆向影響：**摧毀 CFG 結構**，IDA 的反編譯幾乎沒法看，是去混淆的主攻目標。

實務上三招疊用（`-fla -bcf -sub`），所以你看到的是「平坦化的骨架 + 虛假分支的雜訊 + 指令替換的膨脹」三層疊加。去混淆要分層剝：先剪虛假分支、還原指令替換，再攻平坦化。

## 招式一：指令替換（`-sub`）——最好還原的一招

`-sub` 把一條運算換成語意相同的一串。例如 `a + b`，OLLVM 可能生成 `a - (-b)`，或用一堆 XOR/AND/OR 湊出加法（bit-level 的等價變換）。以加法為例，一個經典替換：

```
a + b   ──▶   r = a ^ b; c = a & b; c = c << 1; ... （模擬全加器的進位傳播）
```

它是**局部、模式化**的：每種運算的替換模板固定，就那幾套。還原方法就是**模式匹配 + 代數化簡**——認出模板，套回原運算。我們用 Python 演示「`a - (-b)` 恆等於 `a + b`」，以及一個 XOR/AND 型加法替換確實等價（**實際輸出**）：

```python
import random
def sub_add(a, b):                     # OLLVM 風格的加法替換（bit 級全加器）
    while b != 0:
        carry = a & b
        a = a ^ b
        b = carry << 1
    return a

ok = all(sub_add(x, y) == x + y and (x - (-y)) == x + y
         for x, y in [(random.randint(0, 9999), random.randint(0, 9999)) for _ in range(10000)])
print("sub_add(a,b) == a+b  且  a-(-b) == a+b ，10000 組隨機測試全通過:", ok)
```

```
sub_add(a,b) == a+b  且  a-(-b) == a+b ，10000 組隨機測試全通過: True
```

在 IDA 裡，`-sub` 的表現是「一個簡單運算被拆成一長串位元操作」。**D810（下面會講）這類反編譯期外掛能自動把這些模板化簡回原運算**——這是 `-sub` 好對付的原因：它有結構、可規則化還原。

## 招式二：虛假控制流（`-bcf`）——認出不透明謂詞

`-bcf` 的核心是**不透明謂詞**：一個「編譯期你知道結果、但寫成看起來要執行期才知道」的條件。經典模板：

```
if ( (x*x + x) % 2 == 0 )   // 對任意整數 x，x²+x = x(x+1) 必為偶數 → 恆真
    <真正的程式碼>
else
    <垃圾程式碼／永不執行的死路>
```

`x*(x+1)` 是連續兩整數之積，必含一個偶數，所以 `%2==0` **恆真**。編譯器（和 IDA）不做這種數論推理，就把兩條分支都保留、還把垃圾分支的死程式碼也編進去，撐大函式、製造雜訊。另一個常見模板用全域變數 `y`：`if (y*y >= 0)`（平方恆非負，恆真）。

驗證這類不透明謂詞的恆定性（**實際輸出**）：

```python
opaque_true  = all(((x*x + x) % 2 == 0) for x in range(-5000, 5000))   # x²+x 恆偶
square_nonneg = all((y*y >= 0) for y in range(-5000, 5000))            # y² 恆非負
print("(x*x+x) % 2 == 0 恆真:", opaque_true)
print("y*y >= 0 恆真        :", square_nonneg)
```

```
(x*x+x) % 2 == 0 恆真: True
y*y >= 0 恆非負        : True
```

**還原方法**：認出不透明謂詞 → 判定它恆真/恆假 → 剪掉永不執行的分支、把恆真分支直接接上。難點在於 OLLVM 會用各式數論恆等式，你得有一個「已知不透明謂詞樣式庫」，或用符號執行/SMT solver 自動證明「這條件恆定」。D810 內建了常見不透明謂詞的模式，能自動剪。

## 招式三：控制流平坦化（`-fla`）——本章主戲

這是 OLLVM 最具代表性、最難還原的一招。它把函式原本的控制流圖（有 if/loop 的樹狀/圖狀結構）改寫成**單層 dispatcher**：

```
原始 CFG（結構清晰）           平坦化後（單層狀態機）
   ┌───┐                        ┌──────────────────────┐
   │ A │                        │  state = INIT         │
   └─┬─┘                        │  while (1) {          │
     ▼                          │    switch (state) {   │◀── dispatcher（分發器）
   ┌───┐   cond   ┌───┐         │      case INIT:  A;   │
   │ B │────────▶ │ C │         │        state = 1;     │
   └─┬─┘          └─┬─┘         │      case 1:     B;   │
     ▼              ▼           │        state = cond?2:3│
   ┌───┐          ┌───┐         │      case 2:     C; …  │
   │ D │◀─────────┤ … │         │      case 3:     D; …  │
   └───┘          └───┘         │    }                  │
                                │  }                    │
                                └──────────────────────┘
```

底層機制：每個原始基本區塊（A、B、C、D）變成 switch 的一個 case，區塊執行完不是直接跳下一塊，而是**更新 `state` 變數再跳回 dispatcher**，由 dispatcher 根據 `state` 分發到下一塊。原本「A 後面接 B」這個結構資訊，被藏進「A 這個 case 把 state 設成 1，而 state==1 對應 B」這條間接關係——**控制流的邊，變成了資料流的值**。

這對逆向的毀滅性在於：IDA 的反編譯器靠 CFG 重建 `if/for/while`，CFG 被壓平成一個大 switch 後，它重建不出結構，吐給你一個幾百 case 的巨型 `while(1) switch`，每個 case 一小段，你完全看不出誰接誰。

### 去平坦化的思路：找回「誰接誰」

去平坦化的本質就一句話：**還原每個 case 執行後 `state` 會變成什麼，從而還原「真實後繼」**。步驟：

```
1. 識別 dispatcher：找那個「所有 case 都跳回它、由它分發」的中央區塊
                   （特徵：一個對 state 變數做比較/查表的區塊，入度極高）
2. 識別 state 變數：dispatcher 拿來 switch 的那個變數
3. 枚舉真實區塊（real blocks）：dispatcher 分發到的那些「幹真事」的 case
4. 對每個 real block，求出它執行後 state 的值 → 得到後繼
   ├─ state 是常數賦值 → 直接讀出（好情況）
   └─ state 依賴計算/條件（cond ? s1 : s2）→ 需要符號執行求解
5. 用 1–4 重建原始 CFG，patch 掉 dispatcher，讓區塊直接跳後繼
```

第 4 步是關鍵分岔。如果 `state` 更新是簡單常數（`state = 5`），靜態讀出即可；但 OLLVM 常把 `state` 更新做成依賴輸入的計算，或用一個「後繼 = f(當前 state, 條件)」的查表——這時你需要**符號執行**：讓 `state` 是符號值，符號執行每個 block，求出「離開這個 block 時 state 的可能取值」，就是它的後繼。

### 用 angr 符號執行去平坦化

angr 是 Python 的二進位分析框架，能對 `.so` 做符號執行。去平坦化的 angr 思路：以每個 real block 為起點，把它符號執行到「下一次回到 dispatcher」，觀察 `state` 變數變成什麼符號表達式，`solver` 解出具體後繼。

一段**概念性**的 angr 去平坦化骨架（**未實測，理論預期行為**——本 repo 沙箱無 angr/目標 `.so`）：

```python
# deflatten.py —— 概念骨架，真正的實作要處理 relocation、call、記憶體
import angr, claripy

proj = angr.Project("libtarget.so", auto_load_libs=False)

DISPATCHER = 0x1234          # 你在 IDA 認出的 dispatcher 位址
REAL_BLOCKS = [0x1300, 0x1360, 0x13a0, ...]   # dispatcher 分發到的真實區塊

successors = {}
for blk in REAL_BLOCKS:
    state = proj.factory.blank_state(addr=blk)          # 從 real block 起步
    simgr = proj.factory.simulation_manager(state)
    # 執行到「再次抵達 dispatcher」，讀當時的 state 變數值 → 後繼
    simgr.explore(find=DISPATCHER)
    for found in simgr.found:
        sv = found.solver.eval(found.regs.<state_reg>)  # state 變數所在暫存器
        successors.setdefault(blk, []).append(sv)
# successors 裡就是還原出的「真實控制流邊」，據此 patch 掉平坦化
print(successors)
```

**你在自己環境的驗證步驟**：(1) 用 OLLVM（GitHub 上的 fork，如 `heroims/obfuscator` 或 `o-llvm` 相關 repo）以 `-mllvm -fla` 編一個小函式的 `.so`；(2) IDA/Ghidra 打開，親眼確認出現巨型 `switch` 與 dispatcher；(3) 找一個公開的 angr 去平坦化腳本（見延伸閱讀的 angr 範例）跑跑看，比對還原前後的 CFG。因為 dispatcher 位址、state 暫存器都是逐目標而定，上面的骨架不是拿來直接跑的，是給你**理解流程**用的。

### D810 與 pattern-based 去混淆

符號執行威力大但慢、且要逐目標調參。另一條路是 **pattern-based**——在**反編譯期**用規則匹配把混淆模式改寫回去。代表是 **D810**（Hex-Rays IDA Pro 的反編譯期去混淆外掛）：

D810 掛在 IDA 的 **microcode**（Hex-Rays 反編譯的中間表示）層，在反編譯生成 pseudocode 前，用一組規則把 OLLVM 的模式化改寫回去——`-sub` 的指令替換化簡回原運算、`-bcf` 的不透明謂詞剪掉、部分平坦化模式還原。它的優勢是**整合在反編譯流程裡、即時生效**：你按 F5 反編譯，看到的已經是去混淆後的乾淨 pseudocode。

D810 的定位（**未實測，理論預期行為**，基於其公開文件）：

- 強項：`-sub` 化簡、常見不透明謂詞剪除——這些是**規則化**的，pattern 匹配很準。
- 弱項：複雜的、依賴輸入的平坦化 dispatcher——規則庫覆蓋不到的變體，還是得回頭用 angr 符號執行。

所以實務組合是：**D810 先掃一遍**（清掉 `-sub`/`-bcf` 的雜訊，還原大部分結構）→ **剩下硬核平坦化用 angr 補**。兩者互補，不是二選一。

> **對照工具鏈**：Ghidra 側有 `gooMBA`、各種 script 化的去平坦化 plugin；Binary Ninja 有自己的 MLIL 去混淆生態。原理都一樣（在中間表示層做模式改寫或符號執行），選你熟的反編譯器對應的工具即可。核心能力是「認出 OLLVM 模式」，工具是載體。

## 三個範例：從識別到還原

**範例一（好情況，`-sub` 為主）**：一個字串解密函式被 `-sub` 膨脹，IDA 反編譯出一長串 XOR/AND/SHL。D810 一掃，化簡回 `result = a + key` 這類原運算，Ch 23 的識別功夫直接能用。**這是最順的情況**——`-sub` 純粹是閱讀量，還原後幾乎恢復原貌。

**範例二（中等，`-bcf` + `-sub`）**：函式裡穿插著 `if ((x*x+x)%2==0)` 這類分支，垃圾程式碼混在真邏輯裡。D810 剪不透明謂詞、化簡替換後，CFG 大致乾淨，但你得人工確認「被剪的真的是死路」——**邊界情況**：如果混淆器用了 D810 規則庫沒有的謂詞變體，它剪不掉，你會在反編譯裡看到殘留的假分支，得手動判定。

**範例三（硬核，全 `-fla -bcf -sub`）**：巨型 dispatcher，D810 只能清掉表層雜訊，核心平坦化還在。你 IDA 認 dispatcher 與 state 變數 → 寫 angr 腳本符號執行求後繼 → 重建 CFG。**失敗模式**：angr 符號執行遇到 `.so` 呼叫外部函式（如 `malloc`、JNI 回呼）會發散或超時，得為這些呼叫寫 SimProcedure 或 hook 掉——這是 angr 去平坦化最耗時的部分，不是演算法難，是**工程細節多**（relocation、記憶體模型、外部呼叫）。

## 對比與取捨

| 去混淆手段 | 對 `-sub` | 對 `-bcf` | 對 `-fla` | 成本 | 何時用 |
|---|---|---|---|---|---|
| **人工讀 + IDA** | 慢但可 | 慢，需認謂詞 | 幾乎不可行 | 高（時間） | 函式很小、只此一個 |
| **D810 / pattern plugin** | 很強 | 強（常見謂詞） | 部分變體 | 低（裝好即用） | 首選，先掃一遍 |
| **angr 符號執行** | 可（overkill） | 可 | **強（主力）** | 高（逐目標調參） | D810 搞不定的平坦化 |
| **動態 trace（Frida Stalker）** | — | 繞過（只看真實路徑） | 繞過（不還原、直接看流程） | 中 | 只想知道「這次執行走哪」而非還原全結構 |

最後一列點出一個常被忽略的替代路：**你不一定要「還原」平坦化**。如果目標只是搞懂某次輸入下的執行流程（例如某個金鑰怎麼算的），用 Frida Stalker（Ch 15）trace 這一次的真實執行路徑，直接看它跑過哪些 real block——**繞過**平坦化而非還原它。呼應 Ch 1 的鐵律：混淆保行為，動態看真實行為，平坦化再兇也擋不住「跟著它跑一遍」。

## 踩雷集錦

1. **一上來就對巨型 switch 硬讀**：平坦化的 pseudocode 幾百個 case，人工按順序讀是徒勞——case 的順序跟執行順序**無關**。先識別 dispatcher/state，用工具還原邊，別線性硬啃。
2. **以為 D810 是萬能**：D810 對 `-sub`/常見 `-bcf` 很強，但複雜平坦化變體會殘留。看到 D810 之後還是有大 switch，別以為它壞了——那部分本來就要 angr 補。
3. **angr 不寫 SimProcedure 就跑**：目標 `.so` 一呼叫 `malloc`/`memcpy`/JNI 函式，符號執行就發散或超時。對外部呼叫要 hook 或用 angr 內建 SimProcedure，否則卡死——這是新手 angr 去平坦化最常見的翻車點。
4. **架構搞錯**：真機 `.so` 是 ARM64，你在 x86_64 AVD 撈到的（若有）是 x86。angr/IDA 的分析要對準正確架構，unicorn 引擎、SimProcedure 都跟架構綁定。
5. **忘了「動態繞過」這條路**：卡在還原平坦化好幾天——其實你只是想知道某個值怎麼算的。Frida hook 輸入輸出、或 Stalker trace 一次執行，可能十分鐘解決，根本不用還原整個 CFG。先問自己「我到底要不要完整還原」。
6. **把 Kotlin/C++ 的正常複雜當混淆**：C++ 的模板展開、內聯、STL 也會讓反編譯很亂。看到亂不等於有 OLLVM——先確認有沒有平坦化的**特徵**（巨型 dispatcher + state 變數 + case 順序與執行無關），再下「這被混淆了」的結論。

## 進階：再往深一層

- **MBA（Mixed Boolean-Arithmetic）混淆**：比 `-sub` 更狠的算術混淆，把運算表達成 boolean 與算術混合的複雜恆等式（如把 `x+y` 寫成一串 `&`/`|`/`^`/`+` 的組合），用一般代數化簡剪不掉。要用專門的 MBA 化簡器（如 `gooMBA`、SSA-based 的 MBA solver）。這是算術混淆的軍備競賽前沿。
- **VM-based 混淆（VMProtect 風格）**：比 OLLVM 更高一級——把原始碼編成**自訂虛擬機的 bytecode**，`.so` 裡只有一個 VM 解釋器在跑那套私有指令。這時連「還原 CFG」都無從談起，你得先逆出那台 VM 的 dispatch 迴圈與指令語意（devirtualization），工程量巨大。Ch 28 談 VMP 殼時會再碰到這個概念——native 混淆的 VMP 與加固的 VMP 殼是同一個思想的不同載體。
- **OLLVM 的變體與商用版**：開源 OLLVM 停在 LLVM 4.0 時代，但被大量 fork 和商用化（Snapchat、各家加固廠商都有自研的 LLVM-based 混淆器）。它們在三招之上加 string 加密、間接跳轉、反調試檢查。認出「這是 OLLVM 系」靠平坦化特徵，但具體參數與加料是逐廠商而定——一般而言，商用版的 dispatcher 會更難識別、state 更新更繞。
- **符號執行的可擴展性極限**：angr 對單一函式的去平坦化可行，但整個 `.so` 幾千個函式全平坦化，符號執行逐個跑成本爆炸。實務上你**只還原你關心的那幾個函式**（簽名、金鑰派生），不追求全 `.so` 還原——精準打擊，不做無用功。

## 動手練習

1. 裝一個 OLLVM fork（延伸閱讀有指路），寫一個含 `if/for` 的小函式，分別用 `-mllvm -sub`、`-mllvm -bcf`、`-mllvm -fla` 各編一版 `.so`，再編一版三個都開。用 Ghidra 開這四版，肉眼比對：`-sub` 讓函式變長多少、`-bcf` 多出哪些假分支、`-fla` 怎麼把 CFG 壓成大 switch。這一步讓你**認得出**每一招的視覺特徵。
2. 對 `-fla` 那版，在 Ghidra/IDA 手動找出 dispatcher（入度最高、對一個變數做比較分發的區塊）與 state 變數。先不還原，只要求你能指著螢幕說「這是 dispatcher、這是 state」。
3. 用本章的 Python 片段，自己設計一個新的不透明謂詞（例如某個你查到的數論恆等式），寫程式在大範圍輸入上驗證它恆真/恆假。理解「不透明謂詞為什麼騙得過編譯器」——因為它需要編譯器不做的推理。
4. 進階：找一個公開的 angr 去平坦化腳本（如 CTF writeup 附的），對你在練習 1 編的 `-fla` `.so` 跑跑看，比對還原出的後繼關係與你手動分析的 CFG 是否一致。

## 本章重點整理

- OLLVM 是一組 **LLVM IR 層**的混淆 pass，與語言/架構無關，三招殺傷力遞增：`-sub`（指令替換，好還原）< `-bcf`（虛假控制流/不透明謂詞，可剪）< `-fla`（控制流平坦化，最難）。
- **平坦化的本質**：把 CFG 的「邊」變成 state 變數的「值」，用一個 dispatcher 分發——控制流資訊藏進資料流。去平坦化就是**還原每個 real block 執行後的 state → 重建後繼邊**。
- 去平坦化兩條路：**D810/pattern plugin** 在反編譯期規則化還原（首選，先掃）+ **angr 符號執行** 補硬核平坦化（求 state 後繼）。
- **動態繞過**是常被忽略的第三路：只想知道某次執行流程，用 Frida Stalker trace 真實路徑，不必還原整個 CFG——混淆保行為，動態看行為。
- angr 去平坦化的難點不在演算法，在**工程細節**（外部呼叫要寫 SimProcedure、relocation、記憶體模型、超時發散）。

## 自我檢核

- [ ] 能說出 OLLVM 三招各自動什麼、殺傷力排序，以及為什麼它在 IR 層混淆使其跨架構通用
- [ ] 能解釋控制流平坦化「把控制流的邊變成資料流的值」是什麼意思，以及它為什麼摧毀 IDA 的 CFG 重建
- [ ] 拿到一個平坦化函式，能講出去平坦化的五個步驟，並說明第 4 步為什麼可能需要符號執行
- [ ] 能說出 D810 與 angr 各自的強項/弱項，以及為什麼實務上兩者互補
- [ ] 能講出「不還原、直接動態 trace」這條路何時比去平坦化更划算
- [ ] 知道 angr 去平坦化最常在哪裡翻車（外部呼叫發散/超時）

## 延伸閱讀

- **[Obfuscator-LLVM（OLLVM）原始 repo 與論文](https://github.com/obfuscator-llvm/obfuscator)**
  - **讀哪裡**：README 對 `-fla`/`-bcf`/`-sub` 三個 pass 的說明；以及原論文對平坦化與不透明謂詞的定義
  - **和本章的關聯**：這是三招的一手來源，讀完你會懂「編譯器這端加了什麼」，去混淆才有的放矢。動手練習 1 也需要一個 OLLVM fork 來編混淆 `.so`
- **[D810 —— IDA Pro 反編譯期去混淆外掛（GitHub）](https://github.com/joydo/d810)**
  - **讀哪裡**：README 的支援混淆類型清單、以及 microcode-level 規則的運作說明
  - **為什麼值得讀**：本章 pattern-based 去混淆的具體工具；理解它掛在 Hex-Rays microcode 層做規則改寫，你就懂「反編譯期去混淆」跟「符號執行去混淆」的分工
- **[angr 官方文件與去混淆範例](https://docs.angr.io/)**
  - **讀哪裡**：`SimulationManager`（explore/find）、`SimProcedure`（處理外部呼叫）、以及 examples 裡的 deobfuscation/CTF 案例
  - **和本章的關聯**：去平坦化第 4 步的符號執行主力工具；SimProcedure 那節直接對應「踩雷集錦」第 3 點的翻車點
- **[Quarkslab / 看雪 —— OLLVM 去混淆技術文章](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜 "OLLVM 去平坦化" / "control flow flattening deobfuscation" 的實戰 writeup，多附完整 IDA/angr 腳本
  - **為什麼值得讀**：這些文章把「識別 dispatcher → 求 state 後繼 → patch CFG」走成可複製的完整流程，比抽象講解更能上手；Quarkslab 的部落格則有更嚴謹的符號執行去混淆方法論
- **[Tigress / MBA 混淆與化簡研究](https://tigress.wtf/)**
  - **讀哪裡**：Tigress 的 flattening/virtualization 選項說明，理解比 OLLVM 更進階的混淆
  - **和本章的關聯**：進階一節的 MBA 與 VM-based 混淆的延伸；看完你會知道 OLLVM 只是混淆光譜的起點

下一章我們從「混淆」跨進「加固」——前兩章的混淆讓 DEX/`.so` 難讀但**還在**，加固則是把真正的 DEX **藏起來**，執行期才釋放。我們會拆解殼的分代（一代整包加密 → 二代函式抽取 → VMP）、主流廠商（梆梆/愛加密/360/騰訊樂固）的特徵、以及殼怎麼在 Application 載入時完成偷天換日。

→ [Ch 28 加固加殼原理與分代](./28-packers-overview.md)
