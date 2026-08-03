# Ch 2 — 從 source 到 binary：編譯器做了什麼

> **目標**：走一遍編譯器把 source 變成 binary 的完整流程，因為**你逆的就是這些變換的產物**。你會親眼看到前端保留了什麼、優化階段摧毀了什麼、後端怎麼把抽象落地成 ABI，並得到本章的核心論證：**逆向難度直接正比於編譯器動了多少手腳**。全程用真跑的 `-O0` vs `-O2` objdump 對照佐證。

> **環境**：WSL2 / Linux x86-64，gcc 11.4.0 + objdump。所有 asm 皆真跑貼出，`gcc -Ox -c` 產生 `.o` 後 `objdump -d`。

## 為什麼需要這個？

Ch 0 給了你逆向第一原則：「你逆的不是你寫的 code，是編譯器改寫過的 code。」Ch 1 把它量化成資訊落差表。但「編譯器改寫過」到底改了什麼、怎麼改的？你如果不知道編譯器**動手的清單**，逆向時就會反覆被同一件事嚇到——看到 `x/7` 變成一個詭異的乘法就懵、看到 `switch` 變成一張表就懵、看到你明明寫的函式在 binary 裡消失就懵。

這門課接你的 compiler 課群（[`compiler_frontend`](../../compilers/compiler_frontend/README.md)、[`compiler_backend`](../../compilers/compiler_backend/README.md)、[`ssa_optimizations`](../../compilers/ssa_optimizations/01-why-ssa.md)）。那些課教你**怎麼寫**一個編譯器；這章反過來——站在逆向者的位置，看編譯器的每個階段**在 binary 上留下什麼痕跡、抹掉什麼線索**。逆向是編譯的逆運算，你越懂編譯器怎麼變換，就越能把變換**逆回去**。

一個核心體悟先擺這：**編譯器每做一個優化，就是替逆向者製造一道障礙。** inline 抹掉函式邊界、常數摺疊抹掉計算過程、strength reduction 把可讀的乘除變成魔數運算。所以本章的主軸就是——**逆向難度 ∝ 編譯器動了多少手腳**。`-O0` 好逆，`-O3` 難逆，中間的差距全是這些變換堆出來的。

## 先建立直覺：一條四段流水線

編譯器不是一步到位，是一條流水線。逆向者要知道每一段對「意圖」做了什麼：

```
  你的 C source
      │
   ┌──▼─────────────┐  前端 (frontend)
   │ parse → AST    │  名字、型別、高階結構此時「都還在」
   │ 語意分析       │  → 你逆向想要的意圖，全在這一層
   └──┬─────────────┘
      │ AST（帶型別與名字）
   ┌──▼─────────────┐  中端 (middle-end) ← 逆向者的頭號敵人
   │ 降成 IR        │  inline / 常數摺疊 / 死碼消除
   │ 優化 passes    │  strength reduction / 迴圈優化 / 向量化
   └──┬─────────────┘  → 意圖在這裡被大規模改寫、甚至消滅
      │ 優化後的 IR
   ┌──▼─────────────┐  後端 (backend)
   │ 指令選擇       │  IR → x86-64 指令（一個 IR 運算 → 一或多條指令）
   │ 暫存器配置     │  變數 → 暫存器/stack 位移（名字最終死於此）
   │ ABI 落地       │  參數進 rdi/rsi...、回傳進 rax
   └──┬─────────────┘
      │ asm
   ┌──▼─────────────┐  組譯 + 連結
   │ 組譯成機器碼   │  符號解析、重定位
   │ 連結、strip    │  → strip 刪掉 .symtab，名字徹底消失
   └──▼─────────────┘
     你逆的 binary
```

讀這張圖的方式：**你逆向想要的東西（意圖、名字、型別、結構）在最上面前端最完整，然後一路往下被剝蝕**。中端優化是主要的破壞者，後端把抽象壓平成 ABI，strip 補最後一刀刪掉名字。逆向就是逆著這條流水線往上爬。

## 前端：名字與型別此時還在（然後就沒了）

前端做 parse（source → AST）與語意分析。這階段 `password`、`validate`、`struct Config`、`int` vs `long` **全部都在**——AST 的每個節點帶著名字與型別。

關鍵的逆向含義：**這些資訊前端有，但編譯器不會把它們放進最終 binary（除非你開 `-g` debug info）。** 名字和型別是給編譯器自己做檢查用的中間產物，任務完成就丟。到了後端配暫存器時，`password` 這個名字對 CPU 毫無意義，被換成 `%rdi` 或 `-0x8(%rbp)`。

這解釋了資訊落差表的一整排「被刪掉」：不是編譯器故意藏你，是這些資訊**在流水線早期就用完丟棄了**。`-g` 產生的 debug info（DWARF）就是「把前端的名字/型別留一份副本進 binary」——所以有 `-g` 的 binary 好逆得多，而 release binary 幾乎都沒有 `-g`，還會 strip。

> 這也是為什麼 Ch 0 教你做 ground-truth 時要留一份 `-g` 沒 strip 的當答案：那份保留了前端資訊，是你的標準答案。

## 中端：優化——逆向者的頭號敵人

前端之後，AST 降成中介碼（IR），然後跑一連串優化 pass。**這是編譯器對你的 code 動手最狠的地方，也是逆向難度的主要來源。** 逐個看，每個都配真跑對照。

### 內聯（inline）：函式邊界消失

`static` 小函式常被直接抄進呼叫處，原函式不再單獨存在。source：

```c
static int square(int x){ return x*x; }
int use_square(int x){ return square(x) + 1; }
```

`gcc -O2 -c` 後（真跑）：

```asm
0000000000000000 <use_square>:
   0:	endbr64
   4:	imul   %edi,%edi          ; x*x —— square 被抄進來了
   7:	lea    0x1(%rdi),%eax     ; +1
   a:	ret
```

`square` 這個函式**在 `.o` 裡完全不存在**——`objdump -d ... | grep -c '<square>:'` 回傳 `0`。它的 body（`x*x`）直接長在 `use_square` 裡。逆向含義：**你看到的一大段 asm 可能是好幾個原始函式被 inline 拼起來的**，函式邊界是逆向者要自己重建的假設。Ch 0 那個「`-O2` 下 `secret` 消失」就是 inline + 常數摺疊聯手的結果。

### 常數摺疊（constant folding）：計算過程消失

編譯期能算出的，直接換成結果。source：

```c
int fold(void){ return 3*7 + 100/4; }
```

`gcc -O2` 後（真跑）：

```asm
0000000000000010 <fold>:
  10:	endbr64
  14:	mov    $0x2e,%eax          ; 0x2e = 46，整個算式在編譯期就算完了
  19:	ret
```

`3*7 + 100/4` = 46 = `0x2e`。binary 裡**沒有乘法、沒有除法、沒有加法**，只有一個常數。逆向含義：**你看到一個突兀的魔術常數，它可能是一整段計算的結果**——你逆不出「這 46 怎麼來的」，因為過程被摺掉了。Ch 0 的 `secret` 迴圈加總變成 `mov $0x16`（22）是同一回事。

### 死碼消除（dead code elimination, DCE）：沒用的東西消失

沒有副作用、結果沒被用到的 code，直接刪。逆向含義相對溫和但要知道：**你在 binary 裡看不到的東西，不代表 source 沒寫**——編譯器可能判斷它是死碼刪了。反過來，debug 版有的檢查，release 版可能被優化掉。

### 強度削減（strength reduction）：昂貴運算變便宜的魔數運算

這是逆向者**最常被騙**的一類，值得重點看。編譯器把慢的運算（乘、除）換成快的（加、位移、`lea`），代價是可讀性歸零。

**乘以常數 → `lea` / 加法。** source `return x * 10;`，`gcc -O2`（真跑）：

```asm
0000000000000000 <mul>:
   0:	endbr64
   4:	lea    (%rdi,%rdi,4),%eax   ; eax = x + x*4 = 5x
   7:	add    %eax,%eax            ; eax = 5x + 5x = 10x
   9:	ret
```

`x * 10` 沒有 `imul`，是 `lea (%rdi,%rdi,4)`（算 `x + 4x = 5x`）再 `add` 自己（`10x`）。`lea`（Load Effective Address）本來是算位址的，編譯器拿它當「乘加計算器」——因為它一條指令能算 `a + b*{1,2,4,8}`，比 `imul` 快。**逆向時看到 `lea (%rdi,%rdi,4)` 別以為在算指標，它就是 `×5`。** 這是最重要的 idiom 之一。

**無號除以常數 → 乘魔數 + 位移。** 這是最嚇人的。source `unsigned udiv(unsigned x){ return x / 3; }`，`gcc -O2`（真跑）：

```asm
0000000000000010 <udiv>:
  10:	endbr64
  14:	mov    %edi,%eax
  16:	mov    $0xaaaaaaab,%edx     ; ← 魔數！
  1b:	imul   %rdx,%rax            ; x * 0xaaaaaaab
  1f:	shr    $0x21,%rax           ; >> 33
  23:	ret
```

**binary 裡沒有除法指令**（`div` 慢）。`x / 3` 變成「乘以魔數 `0xaaaaaaab` 再右移 33 位」。這個魔數是 `2^33 / 3` 取整加補償——編譯器用「乘以倒數的定點近似」取代除法。你不用背推導，但要**一眼認出這個 pattern**：`mov 一個怪常數; imul; shr` = 「除以某個常數」。`0xaaaaaaab` ≈ 2^32 × (1/3)，看到它心裡就該喊「這在除以 3」。

**有號除法更複雜**（要處理負數向零取整）。source `int sdiv(int x){ return x / 7; }`，`gcc -O2`（真跑）：

```asm
0000000000000030 <sdiv>:
  30:	endbr64
  34:	movslq %edi,%rax
  37:	imul   $0xffffffff92492493,%rax,%rax  ; 乘 signed 魔數
  3e:	shr    $0x20,%rax                      ; 取高 32 位
  42:	add    %edi,%eax
  44:	sar    $0x1f,%edi                      ; ┐ 取符號位
  47:	sar    $0x2,%eax                        ; │ 額外的位移
  4a:	sub    %edi,%eax                        ; ┘ 對負數補償
  4c:	ret
```

七條指令做一個 `x / 7`。`sar $0x1f; sub` 那段是「負數補償」（跟 Ch 0 的 signed /2 用 `shr $0x1f; add` 補償是同一個道理）。逆向含義：**看到一坨 `imul 魔數 + shr + sar + 符號位補償`，那是有號除法，別逐條硬推，認 pattern 標「/常數」就對了。** 想反查魔數對應哪個除數，Compiler Explorer 貼 `x / N` 一試便知。

### 迴圈優化 / 向量化：結構被改頭換面

編譯器會展開迴圈（unroll）、把純量迴圈改成 SIMD（一次處理多個元素，用 `xmm`/`ymm` 暫存器）。逆向含義：**一個簡單的 `for` 迴圈在 `-O3` 下可能變成塞滿 `movdqu`/`paddd`/`pxor` 的 SIMD block**，長度暴增、結構面目全非。你在 [Ch 5](./05-recognizing-control-flow.md) 會學怎麼把展開/向量化的迴圈認回原形。這是「逆向難度 ∝ 手腳」最戲劇性的例子——同一個迴圈 `-O0` 你三秒看懂，`-O3` 你得辨識半天。

## 後端：指令選擇 + ABI 落地

優化後的 IR 進後端，做兩件逆向者天天要用的事：

**指令選擇（instruction selection）。** 一個 IR 運算對應到一或多條真實 x86-64 指令。同一個「乘以 10」，後端選了 `lea + add` 而不是 `imul`——這選擇造就了 idiom 的長相。不同編譯器（gcc vs clang）指令選擇策略不同，asm 風格就有指紋，[Ch 10](./10-compiler-idioms.md) 會教你從風格反推編譯器。

**calling convention / ABI 落地。** 這是逆向的立足點，必背。x86-64 Linux 用 **System V AMD64 ABI**：

- 整數/指標參數依序放 `rdi, rsi, rdx, rcx, r8, r9`，多的進 stack
- 回傳值在 `rax`（小的在 `eax`/`al`）
- `rbx, rbp, r12–r15` 是 callee-saved（被呼叫者要保存）

逆向含義極大：**沒有名字，但 ABI 是鐵律。** 看到函式開頭 `mov %rdi,...` 你就知道「這在用第一個參數」；看到結尾 `mov $..,%eax; ret` 你就知道回傳值。Ch 1 那個 `check` 你能認出「`p` 在 `rdi`」，靠的就是這條 ABI。ABI 是逆向者把「無名的暫存器」對回「有名的參數」的唯一橋樑。[Ch 4](./04-x86-64-for-reversers.md)/[Ch 7](./07-recognizing-functions.md) 會把它講透。

## 組譯 + 連結 + strip：名字的最後一刀

asm 組譯成機器碼，連結器解析符號（把 `call strcmp` 接到真正的 strcmp）、做重定位。這階段產生 `.symtab`（符號表，含你所有函式/全域變數名）。

然後 `strip` 來補刀：**刪掉 `.symtab`，你的函式名 `check`/`main` 全部消失**，`sub_1169` 取代它們。這是 Ch 0 那個 `nm: no symbols` 的來源。但注意——strip **刪不掉動態符號**（`strcmp`/`puts` 這些 import），因為動態連結器執行期需要它們。「為什麼 strip 刪不掉 import」是下一章 ELF 解剖的重頭戲。

## 對比與取捨：`-O0` vs `-O2` 的逆向難度

把整章的變換收斂成一張逆向者最實用的表：

| 變換 | `-O0`（好逆） | `-O2`/`-O3`（難逆） | 逆向者要做的事 |
|---|---|---|---|
| 函式邊界 | 每個函式獨立、有 `call` | 小函式被 inline，邊界消失 | 重建函式邊界，別假設一段 asm = 一個原函式 |
| 乘法 | `imul $10` 直白 | `lea` + `add` 湊出來 | 認 `lea` 當乘加器 |
| 除法 | `idiv` 直白（或仍是庫呼叫） | 乘魔數 + 位移 | 認「怪常數 imul + shr」= 除以常數 |
| 常數計算 | 保留運算指令 | 摺疊成單一常數 | 接受過程消失，突兀常數 = 一段計算的結果 |
| 迴圈 | 直白的 `cmp`/`jmp` | 展開 / 向量化（SIMD） | 把 SIMD block 認回迴圈 |
| 變數 | 多半在 stack（`-0x8(%rbp)`），好追 | 多半在暫存器，生命週期交錯 | 追暫存器的資料流，更燒腦 |
| 死碼 | 保留 | 消除 | 接受「binary 沒有 ≠ source 沒寫」 |

取捨的實務含義：**教材用 `-O0` 讓你看懂對應關係，但真實 release binary 幾乎都 `-O2`/`-O3`。** 你得兩者都會——用 `-O0` 學 idiom 的「原形」，再練著在 `-O2` 的「變形」裡認出它。這正是「逆向難度 ∝ 編譯器動了多少手腳」的操作定義：優化等級每往上一階，編譯器多動的手腳就是你多要拆的障礙。

## 踩雷集錦

1. **以為 binary 的指令數 ≈ source 的行數**。錯誤直覺：source 三行，asm 應該也差不多。正確認知：一個 `x/7` 能爆成七條指令，一整個迴圈能被摺成一個常數。指令數和 source 行數**沒有**穩定關係，優化把它徹底打亂。
2. **看到 `lea` 就以為在算位址**。錯誤直覺：`lea` = Load Effective Address = 一定跟指標/陣列有關。正確認知：`-O2` 下 `lea` 大量被當**乘加計算器**（`lea (%rdi,%rdi,4)` = ×5）。要看它算完存去哪、怎麼用，才能判斷是位址還是算術。
3. **看到怪常數就想硬算它的意義**。錯誤直覺：`0xaaaaaaab` 一定是某個 magic value、某個 key。正確認知：它極可能是**除法魔數**（≈2^32/3）。看到「怪常數 + imul + shr」先假設「除以常數」，用 godbolt 反查除數，別逆推魔數本身。
4. **假設「一段 asm = 一個原始函式」**。錯誤直覺：函式邊界在 binary 裡是清楚的。正確認知：inline 讓一段 asm 可能是多個原函式拼的，`static` 小函式可能整個消失。函式邊界是你要**重建的假設**。
5. **拿 `-O0` 教材例子對照真實 `-O2` binary 就慌**。錯誤直覺：怎麼跟課本完全不一樣，是不是我學錯了。正確認知：課本用 `-O0` 教原形，真實世界是 `-O2` 變形。差異不是你的錯，是優化。先確認你在看哪個等級。

## 進階：再往深一層

- **同一份 source 掃過各優化等級**。拿本章任一例子，`gcc -O0/-O1/-O2/-O3 -c` 各編一份，`objdump -d` 並排看。你會親眼看到障礙一階一階疊上去——這是把「難度 ∝ 手腳」變成肌肉記憶的最好練法，也是 Ch 0 ground-truth 迴圈的延伸。
- **gcc vs clang 的 idiom 指紋**。同一個 `x/3`，兩個編譯器選的魔數、指令順序可能不同。逆向老手能從風格猜編譯器，這在判斷「這 binary 誰編的、可能有什麼已知模式」時有用。[Ch 10](./10-compiler-idioms.md) 深入。
- **`-O3` 的向量化是另一個世界**。SIMD 展開會讓迴圈完全變形，還可能有 remainder loop（處理不整除的尾巴）。看不懂純靜態就上 gdb 動態觀察它一次處理幾個元素（[Part 2](./12-dynamic-reversing-mindset.md)）——觀察勝於瞪 asm。
- **接回 compiler 課群**：本章每個優化，[`ssa_optimizations`](../../compilers/ssa_optimizations/17-strength-reduction.md) 都從「怎麼實作」的角度教過（strength reduction、[常數摺疊](../../compilers/ssa_optimizations/13-constant-folding.md)、[DCE](../../compilers/ssa_optimizations/14-dce.md)）。從那門課回頭看本章，你會懂編譯器**為什麼**這樣變換——懂了原理，逆回去就快。

## 本章重點整理

- 編譯是四段流水線（前端 → 中端優化 → 後端 → 組譯連結 strip），**你逆向想要的意圖在前端最全，一路往下被剝蝕**。
- **中端優化是頭號敵人**：inline 抹函式邊界、常數摺疊抹計算過程、DCE 刪死碼、strength reduction 把乘除變魔數運算、向量化改造迴圈結構。
- **後端給你逆向立足點**：指令選擇造就 idiom 長相；ABI（參數進 `rdi/rsi/...`、回傳在 `rax`）是把無名暫存器對回有名參數的鐵律。
- **strip 補最後一刀**刪 `.symtab`，函式名消失，但刪不掉動態符號（import）。
- **核心論證**：逆向難度直接正比於編譯器動了多少手腳。`-O0` 好逆、`-O3` 難逆，差距全是這些變換。用 `-O0` 學 idiom 原形，練著在 `-O2` 認出變形。

## 自我檢核

- [ ] 我能畫出編譯四段流水線，並說出每段對「意圖/名字/型別」做了什麼
- [ ] 我能解釋為什麼名字和型別「前端有、binary 沒有」（除非 `-g`）
- [ ] 我看到 `lea (%rdi,%rdi,4)` 知道它是 ×5，不是在算位址
- [ ] 我看到「怪常數 + imul + shr」知道那是除以常數，不是某個 key
- [ ] 我能說出 x86-64 System V ABI 的參數暫存器順序與回傳暫存器
- [ ] 我能用「逆向難度 ∝ 編譯器動了多少手腳」解釋為什麼 release binary 比教材例子難逆

## 延伸閱讀

- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：把每個 compiler idiom 的 source↔asm 對照講到字典級，本章每個優化它都有專節。
  - **讀哪裡**：查「division」看除法魔數、「multiplication」看 `lea` 乘法；當字典跳著讀。
- **[`compilers/ssa_optimizations`](../../compilers/ssa_optimizations/17-strength-reduction.md)**
  - **定位**：從「怎麼實作優化」的角度講常數摺疊/strength reduction/DCE，是本章的正對面。懂實作，逆回去更快。
  - **讀哪裡**：[Ch 17 strength reduction](../../compilers/ssa_optimizations/17-strength-reduction.md)、[Ch 13 常數摺疊](../../compilers/ssa_optimizations/13-constant-folding.md)、[Ch 14 DCE](../../compilers/ssa_optimizations/14-dce.md)；帶著「這在 binary 留下什麼痕跡」的問題讀。
- **《Hacker's Delight》** — Henry S. Warren Jr.（Addison-Wesley）
  - **定位**：除法魔數、位元 trick 的權威來源。想真正搞懂 `0xaaaaaaab` 怎麼來的，看這本。
  - **讀哪裡**：Ch 10「Integer Division by Constants」——編譯器就是照這裡的演算法生魔數的。
  - **前提**：不排斥一點數學推導；只想認 pattern 的話跳過也行。
- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：本章所有對照的即時重現器。想確認某魔數對應哪個除數、某 idiom 對應什麼 source，貼進去選 gcc 11 -O2 即得。
  - **怎麼用**：把本章 `mul`/`udiv`/`sdiv`/`fold` 貼進去，選 x86-64 gcc + `-O2`，右邊 asm 應與本章一致（gcc 版本相近時）。

你現在知道編譯器對 code 做了什麼變換。但 binary 不只是一坨 `.text` 指令——它有骨架：header、段、符號表、動態連結機制。下一章解剖 ELF，教你在載入之前和之後，這團 bytes 到底怎麼組織，逆向者該先看哪幾塊。

→ [Ch 3 ELF 解剖與載入](./03-elf-anatomy-and-loading.md)
