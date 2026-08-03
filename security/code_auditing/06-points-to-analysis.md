# Ch 6 — 指標分析：Andersen、Steensgaard 與精度

> **目標**：Ch 5 反覆說「IFDS 不處理 aliasing，taint 邊的完整性靠 points-to」。這章把這塊補上。C/C++ 審計繞不開 pointer alias——`*p = tainted; use(*q);` 若 `p` 可能 alias `q`，這就是一條真實的污染流；taint/dataflow 若不知道這層 alias 關係，就會**漏掉整條 flow**。我們看兩個經典演算法 **Andersen（inclusion-based，較精準較慢）** 與 **Steensgaard（unification-based，較快較粗）** 的直覺、複雜度、精度差異，並看審計工具實務上怎麼在兩者之間妥協。

先把問題講死。**alias analysis（別名分析）**問的是「兩個指標會不會指向同一塊記憶體」；**points-to analysis（指向分析）**問的是更基礎的「每個指標可能指向哪些抽象位置」——算出 points-to set 就能回答 alias（兩個指標的 points-to set 有交集 ⟹ 可能 alias）。審計裡我們幾乎總是要 points-to，因為 taint 要靠它決定「透過 `*p` 的寫，會不會被 `*q` 讀到」。

## 為什麼 taint 沒有 points-to 就不 sound

Ch 4/5 的 taint 都在追「變數名」層級的流動：`b = a` 讓 taint 從 `a` 傳到 `b`。但一旦出現間接（indirection），變數名就不夠了：

```c
p = &buf;
q = p;              // q 與 p 都指向 buf（alias）
*p = source();      // 透過 p 寫入 buf → buf 被污染
x = *q;             // 透過 q 讀 buf → x 應該 tainted
sink(x);            // 漏洞
```

`*p` 的寫與 `*q` 的讀，在變數名層級**看起來毫無關聯**——一個叫 `p`、一個叫 `q`。只有知道 `pts(p) = pts(q) = {buf}`，才能把「經 `*p` 寫入 `buf`」連到「經 `*q` 從 `buf` 讀出」，於是 `x` 被污染、`sink(x)` 該報。**points-to 就是那把把間接寫入接回間接讀出的鑰匙**。沒有它，這條 flow 直接消失（漏報）。

我們用一支小 demo 把「有沒有 points-to」的差別跑出來（Python 3，真跑）：

```python
def taint_no_alias():
    # 幼稚版：*p / *q 當成無關的獨立位置
    tainted = set()
    tainted.add("*p")           # *p = source()  -> 只標 "*p"
    x_tainted = "*q" in tainted # x = *q  -> 查 "*q"，沒標到
    return x_tainted

def taint_with_alias():
    # 有 points-to：pts(p)={buf}, pts(q)={buf}
    pts = {"p": {"buf"}, "q": {"buf"}}
    tainted_locs = set()
    for loc in pts["p"]:        # *p = source() -> 污染 pts(p) 裡每個位置
        tainted_locs.add(loc)
    x_tainted = any(loc in tainted_locs for loc in pts["q"])  # x = *q
    return x_tainted, tainted_locs

print("=== 不做 points-to（*p 與 *q 當獨立位置）===")
print("  sink(x) 收到 tainted？", taint_no_alias(), " <- 漏報！")
print("=== 做 points-to（知道 p,q 都指向 buf）===")
xt, locs = taint_with_alias()
print(f"  tainted 位置 = {sorted(locs)}")
print("  sink(x) 收到 tainted？", xt, " <- 正確抓到")
```

真跑輸出（照貼）：

```
=== 不做 points-to（*p 與 *q 當獨立位置）===
  sink(x) 收到 tainted？ False  <- 漏報！
=== 做 points-to（知道 p,q 都指向 buf）===
  tainted 位置 = ['buf']
  sink(x) 收到 tainted？ True  <- 正確抓到
```

第一個函式把 `*p`、`*q` 當成兩個字串常數，井水不犯河水，flow 斷掉。第二個函式因為知道兩者都指向 `buf`，透過共享的抽象位置把 taint 接起來。**這就是為什麼 alias 不 sound 會讓 taint 漏報**——邊斷了，可達性當然走不過去。

## 抽象位置：無限的 heap 怎麼裝進有限的格

points-to 要算的是「指標指向哪些位置」，但程式的位置可能無限（`malloc` 在 loop 裡跑一萬次就有一萬塊記憶體）。static analysis 沒法追無限個位置，於是引入 **abstract location（抽象位置，又稱 allocation-site abstraction）**：

> **同一個 allocation site（`malloc` 呼叫點、變數宣告）產生的所有具體記憶體，摘要成一個抽象位置**。loop 裡的 `malloc` 不管跑幾次，都算同一個抽象位置 `heap@line42`。

這一步是所有 points-to 精度取捨的源頭。它讓格變有限（抽象位置數 = 程式裡 alloc site 數，有限），代價是**同一 site 的不同物件被混為一談**——這正是後面「heap abstraction 太粗導致誤報」踩雷的根。記住這個抽象，points-to 的一切近似都從這裡長出來。

## Andersen：inclusion-based，解約束到 fixpoint

Andersen（1994）把 points-to 看成一組**子集約束（subset / inclusion constraints）**，再求 fixpoint。四種基本語句各對應一條約束：

| 語句 | 意思 | Andersen 約束 |
|---|---|---|
| `p = &x` | p 指向 x 的位置 | `x ∈ pts(p)` |
| `p = q` | copy | `pts(q) ⊆ pts(p)` |
| `*p = q` | store（間接寫） | `∀a ∈ pts(p): pts(q) ⊆ pts(a)` |
| `p = *q` | load（間接讀） | `∀a ∈ pts(q): pts(a) ⊆ pts(p)` |

`⊆` 是關鍵：Andersen 用**單向包含**。`p = q` 只讓 `pts(q)` 流進 `pts(p)`，**不反過來**——`p` 學到 `q` 指向的東西，但 `q` 不會學到 `p` 的。這保住了方向性，也是它精準的來源。store/load 的約束是**動態的**：`pts(p)` 每加一個位置，就多一條約束要處理，所以要反覆迭代到 fixpoint（跟 Ch 4 的 worklist 同一個味道）。

複雜度 **~O(n³)**（n 是變數/位置數）：最壞情況約束傳播要在一張 n 節點的圖上做遞移閉包（transitive closure）。對大 codebase，這個三次方就是它擴展性的天花板。

## Steensgaard：unification-based，一次掃描搞定

Steensgaard（1996）用完全不同的路子：**unification（合一）**。它不追「誰包含誰」，而是「凡是可能相關的位置，直接合併成同一個等價類」。用 **union-find** 資料結構：

| 語句 | Steensgaard 動作 |
|---|---|
| `p = &x` | `p` 的指向 = `x`（若已有指向，union 兩者） |
| `p = q` | **union(pointee(p), pointee(q))**——把 p 與 q 指向的東西合併 |
| `*p = q` | union(p 指到的東西, q 指到的東西) |
| `p = *q` | 同上 |

差別在 `p = q`：Andersen 是單向 `pts(q) ⊆ pts(p)`，Steensgaard 是**雙向合併**——p 與 q 從此指向同一個（合併後的）等價類。這一合併就**丟失方向性**：`q` 也被迫「學到」`p` 指向的東西，即使程式從沒讓 `q` 指過去。

代價換來速度：union-find 的操作近乎 O(1)（α(n) 反 Ackermann），整個分析**近線性 O(n·α(n))**，一次掃描過所有語句就收斂，不用迭代到 fixpoint。這就是為什麼超大 codebase（LLVM 的 CFL-steens、gcc 早期的 alias）會選它。

## 同一段 code，兩者的 points-to 對照

我把兩個演算法都實作了（Python 3，真跑）。跑例：

```c
p = &a;
q = &b;
r = &c;
p = q;      // copy
```

`p = q` 是關鍵那步。Andersen 應該讓 `pts(p) = {a, b}`（p 原本指 a，又收了 q 指的 b），但 **`pts(q)` 維持 `{b}`**（單向，q 不學 p 的 a）。Steensgaard 會**把 a 與 b 合併**，於是 `pts(p) = pts(q) = {a, b}`——q 被迫吃進了它從沒指過的 `a`。

核心是 Andersen 的約束求解迴圈（完整含 Steensgaard union-find 版見延伸閱讀的思路，這裡貼會跑的主體）：

```python
def andersen(stmts, vars_):
    pts = {v: set() for v in vars_}
    changed = True
    while changed:
        changed = False
        for s in stmts:
            k = s[0]
            if k == "addr":                      # p = &x
                _, p, x = s
                if x not in pts[p]:
                    pts[p].add(x); changed = True
            elif k == "copy":                    # p = q : pts(q) ⊆ pts(p)
                _, p, q = s
                if not pts[q] <= pts[p]:
                    pts[p] |= pts[q]; changed = True
            elif k == "store":                   # *p = q : ∀a∈pts(p): pts(q)⊆pts(a)
                _, p, q = s
                for a in list(pts[p]):
                    if a in pts and not pts[q] <= pts[a]:
                        pts[a] |= pts[q]; changed = True
            elif k == "load":                    # p = *q : ∀a∈pts(q): pts(a)⊆pts(p)
                _, p, q = s
                for a in list(pts[q]):
                    if a in pts and not pts[a] <= pts[p]:
                        pts[p] |= pts[a]; changed = True
    return pts
```

Steensgaard 版用 union-find，`p = q` 直接 `union(pointee(p), pointee(q))`。兩者跑同一段 code，真跑輸出（照貼）：

```
=== Andersen (inclusion-based) ===
  pts(p) = {a, b}
  pts(q) = {b}
  pts(r) = {c}
=== Steensgaard (unification-based) ===
  pts(p) = {a, b}
  pts(q) = {a, b}
  pts(r) = {c}

--- alias(p,q)? ---
  Andersen:    True
  Steensgaard: True
--- alias(p,r)?  (p→a,b ; r→c，不該 alias) ---
  Andersen:    False
  Steensgaard: False
```

盯著 `pts(q)` 那行：**Andersen `{b}`、Steensgaard `{a, b}`**。這就是精度差。這個例子裡兩者對 `alias(p,q)`、`alias(p,r)` 的判斷剛好一致（都對），但只要再多幾條約束，Steensgaard 的過度合併就會製造 Andersen 不會有的假 alias → 假的 taint flow → 誤報。**Steensgaard 快，但它的 alias 圖偏大偏糊**。

## 疊在 points-to 上的三個 sensitivity 旋鈕

points-to 的精度不只 Andersen/Steensgaard 這一軸，還有三個正交的 sensitivity 可以疊上去（跟 Ch 4 埋的 flow-sensitive 是同一族概念）：

| sensitivity | 區分什麼 | 不開的後果 | 成本 |
|---|---|---|---|
| **flow-sensitive** | 語句順序（同一指標在不同 program point 指向不同） | `p=&a; p=&b;` 之後仍認為 p 可能指 a | 高（每個 point 一份 pts） |
| **context-sensitive** | 不同 caller 呼叫同函式的 context | 兩個 caller 的參數污染互相污染 | 很高（call-string / heap cloning） |
| **field-sensitive** | struct 的不同 field 分開追 | 整個 struct 當一個位置，一個 field 髒全部髒 | 中 |

**field-sensitivity 對 C 審計特別要命**。`struct { char *safe; char *tainted; }`——field-insensitive 分析把整個 struct 當一個抽象位置，於是 `s.tainted = source()` 會讓 `s.safe` 也被當成 tainted，`use(s.safe)` 誤報。反過來，如果工具為了省事把 array 的所有 element 當一個位置（**array 通常是 field-insensitive 的**，因為 index 是動態值），`a[0] = source(); use(a[1]);` 也會誤報。這些都是「abstraction 太粗」的具體長相。

實務上大部分擴展性優先的工具，points-to 是 **flow-insensitive + field-sensitive + 有限 context**——flow-sensitive 太貴通常砍掉，field-sensitive 因為對誤報影響太大通常保留。

## 審計視角：工具怎麼在精度與擴展性間妥協

- **CodeQL**：C/C++ 的 dataflow library 底層有一套 alias/points-to 推理，但它**不是全程式 Andersen**——為了規模化，它用**局部、按需求（demand-driven）**的 alias 推理，配合手寫的 flow model（Ch 23 的 models-as-data）補上函式邊界的 summary。你會發現 CodeQL 有時漏掉「經過複雜指標運算」的 flow，根因常常就是它的 alias 近似沒接上那條間接。
- **Joern**：CPG（Ch 3）上的 dataflow 更偏語法/結構，points-to 更粗。它的強項是「no-build、快速掃大量 code」，代價是 alias 精度較低——你得靠自己寫 query 補結構條件。
- **共同真相**：**沒有一個實用工具跑全程式、flow+context+field 全開的 Andersen**——那對真實規模的 codebase 會跑不完。所有工具都在這張精度表上砍掉某些軸。**你身為審計者的價值，就是知道它砍了哪根軸，於是知道它會在哪類 alias 上漏**。

橫向連結：Ch 5 說「IFDS 的邊完整性靠 points-to 餵」，這章就是那個「餵」的來源。Ch 8 會把 points-to 的取捨連同 flow/context/taint 一起攤成四工具對照表。

## 踩雷集錦

**錯誤直覺：「taint 工具會自動處理所有 alias。」**
正確認識：不會。alias 是**另一個**分析（points-to），taint 只是消費它的結果。工具的 points-to 近似有多粗，taint 的 alias 覆蓋就有多不完整。`*p = tainted; use(*q);` 這種經由間接的 flow，能不能抓到，取決於工具算 alias 的精度——而幾乎所有工具都為了擴展性砍過精度。看到「明明有 alias 卻沒報」，先懷疑工具的 points-to，而不是你的 query 寫錯。

**錯誤直覺：「field-insensitive 把整個 struct 污染，只是保守一點，不影響結論。」**
正確認識：它同時製造**誤報與漏報**。誤報：`s.tainted` 髒導致 `s.safe` 被當髒，`use(s.safe)` 假警報。漏報則出現在另一種混淆——如果工具為省事把不同 struct 的同名 field 合併，反而可能算出根本不存在的 flow，把真的蓋掉。把「保守」等同「只多不少的誤報」是誤解，粗 abstraction 兩頭都會出錯。

**錯誤直覺：「heap 物件就是 `malloc` 回傳的那塊，一個 `malloc` 一個物件。」**
正確認識：static analysis 用 **allocation-site abstraction**——同一個 `malloc` 呼叫點在 loop 裡跑一萬次，全部摘要成**一個**抽象位置。於是「第一次 malloc 的物件乾淨、第二次的髒」這種區分，預設分析看不到（兩個都是同一個抽象 heap 位置，一髒全髒）。要區分得上 heap cloning / context-sensitive heap，很貴，多數工具不開。

**錯誤直覺：「Andersen 比 Steensgaard 精準，所以永遠選 Andersen。」**
正確認識：精準的代價是 O(n³) vs 近線性。對百萬行 codebase，Andersen 可能跑不完或吃爆記憶體，這時 Steensgaard 的粗結果**至少能跑完**，粗的 alias 圖總比沒有強。工具選型是精度 × 規模的權衡，不是「精準就贏」。理解兩者，你才知道手上工具的結果是哪一檔精度。

**錯誤直覺：「points-to 算出來的 alias 是精確的 yes/no。」**
正確認識：static points-to 給的是 **may-alias（可能別名）**——`pts(p) ∩ pts(q) ≠ ∅` 只代表「**可能**指向同一處」，不是「一定」。這是 over-approximation（過近似），本來就會有「算出可能 alias 但實際不會」的情況。想要 **must-alias（一定別名）** 得另一套更貴的分析，且結論更弱。審計用 may-alias 是對的（寧可多算 flow），但別把它當精確事實。

## 進階延伸

- **CFL-reachability 形式化 points-to**：Reps 把 field-sensitive points-to 表述成 **context-free language reachability（上下文無關語言可達性）**問題——field 的 load/store 對應括號配對（balanced parentheses）。這跟 Ch 5 的 IFDS（也是圖可達性）是同一個「把分析化約成圖上走路」的大家族，值得把兩者放一起理解。
- **Datalog 做 points-to（Doop 框架）**：現代做法是把 Andersen 約束寫成 Datalog rule，交給 Souffle 這種高效引擎跑。宣告式、好加 sensitivity（多加幾條 rule 就從 context-insensitive 變 sensitive）。這也是為什麼 CodeQL 選 Datalog 風格的 QL——points-to 這類固定點問題天生適合 Datalog。
- **on-demand / demand-driven points-to**：審計時你通常只關心「餵給這個 sink 的指標，可能 alias 到哪個被污染的位置」，不需要全程式的 pts。demand-driven 只從你的 query 反向按需求算，這也是 CodeQL 能對大 codebase 只算你要的那部分 alias 的關鍵。

## 本章重點整理

- taint 一旦碰到間接（`*p = x; y = *q;`），沒有 **points-to** 就把「經 p 的寫」與「經 q 的讀」斷開 → **漏報**。points-to 是 taint 邊 sound 與否的地基（接 Ch 5）。
- **abstract location（allocation-site abstraction）**把無限的 heap 摘要成有限的抽象位置，讓格有限；代價是同 site 的物件混為一談。
- **Andersen（inclusion-based，`⊆` 單向，O(n³)）** 較精準；**Steensgaard（unification-based，union 雙向合併，近線性）** 較快較粗。同一段 `p=q`：Andersen `pts(q)` 不學 p 的東西、Steensgaard 把兩者合併。
- 三個正交 sensitivity：**flow / context / field**。field-sensitivity 對 C struct 審計最要命（不開 → 一個 field 髒全 struct 髒 → 誤報）。
- 沒有實用工具跑全開的 Andersen；每個工具都砍掉某些軸。**知道它砍哪根軸 = 知道它在哪類 alias 上漏**。static points-to 給的是 **may-alias（過近似）**，不是精確 yes/no。

## 自我檢核

- 用 `*p = source(); x = *q;` 的例子說明：不知道 `pts(p)=pts(q)` 時，taint 為什麼會漏掉 `x` 被污染。這條 flow 斷在哪一步？
- 對 `p = q` 這條 copy，Andersen 與 Steensgaard 分別怎麼做？為什麼 Steensgaard 會讓 `pts(q)` 多出 p 指向的東西，Andersen 不會？
- Andersen 的複雜度為什麼是 O(n³)、Steensgaard 為什麼近線性？各犧牲/換來什麼？
- field-insensitive 對 `struct { char *safe; char *tainted; }` 會造成誤報還是漏報？舉出兩頭都出錯的可能。
- allocation-site abstraction 為什麼讓「loop 裡第一次 malloc 乾淨、第二次髒」這種區分預設看不到？要救得開什麼（很貴的）東西？
- static points-to 給的是 may-alias 還是 must-alias？審計為什麼用前者是對的，但又不能把它當精確事實？

## 延伸閱讀

- **Andersen, *Program Analysis and Specialization for the C Programming Language*, PhD thesis 1994**——inclusion-based points-to 的原始出處。讀約束系統那章，把本章的 `⊆` 約束推導看嚴謹。前提：本章 + 基本序理論。
- **Steensgaard, *Points-to Analysis in Almost Linear Time*, POPL 1996**——unification-based 的原始論文，短且清楚，直接看它怎麼用 union-find 換到近線性。前提：懂 union-find + 本章。與 Andersen 對照著讀，精度/速度取捨一目了然。
- **Smaragdakis & Balatsouras, *Pointer Analysis*, Foundations and Trends in PL 2015**——現代 points-to 的權威 survey，Andersen/Steensgaard/sensitivity/Datalog(Doop) 一條龍。想真懂 field/context-sensitive 怎麼疊，讀這本的 sensitivity 章。前提：本章。頂級 survey，優先讀。
- **Sridharan & Bodík, *Refinement-Based Context-Sensitive Points-To Analysis for Java*, PLDI 2006**——demand-driven / on-demand points-to 的代表作，解釋審計工具怎麼「只算 query 要的那部分 alias」。前提：本章 + Ch 5 的 demand-driven 概念。銜接「工具為什麼能對大 codebase 只算局部 alias」。

points-to 補齊了 taint 邊的完整性。理論零件到這裡湊齊了：lattice（Ch 4）、IFDS 圖可達性（Ch 5）、points-to（本章）。下一章把它們全收斂到 taint 這個審計最核心的框架，精確定義 source/sink/sanitizer/propagation 四要素——這是後面 Semgrep taint mode、CodeQL TaintTracking 的共同骨架。

→ [Ch 7 Taint 分析原理](./07-taint-analysis-theory.md)
