# Ch 3 — 程式表示：從 AST/CFG/SSA/PDG 到 CPG

> **目標**：把你已經懂的 AST/CFG/SSA/PDG 從「編譯器優化的中介表示」切換到「審計查詢的載體」視角，然後理解為什麼 Yamaguchi 2014 的 **CPG（Code Property Graph，程式屬性圖）** 要把它們合成一張圖——這是 Joern 與（換個存法）CodeQL 能一次表達語法模式與資料流查詢的根。

你在 `ssa_optimizations` 學過 AST/CFG/SSA/PDG，那時候的問題是「怎麼把程式變快、變小」。這裡問題換了：**「怎麼把一類漏洞寫成一個對這些表示的查詢，一次掃出所有變體」**。同一個資料結構，換個問題就換個看法。這章不重教它們是什麼——重疊處我直接連 [`../../compilers/ssa_optimizations/README.md`](../../compilers/ssa_optimizations/README.md)——而是把篇幅花在「每種表示能表達哪類漏洞查詢，哪類表達不了」，最後推導 CPG 為什麼要合一。

## 先對齊：四種表示各自答什麼問題

先給一段小 C 函式當全章的跑例。它有一個很典型的 bug：長度沒檢查對就 `memcpy`。

```c
void handle(char *pkt, int n) {
    char buf[64];
    int len = n;              // len 來自外部
    if (len > 128)            // 檢查上界 128，但 buf 只有 64
        return;
    memcpy(buf, pkt, len);    // stack overflow：len 最大 128 > 64
}
```

這是一個 **CWE-787（out-of-bounds write，越界寫入）**。手讀你一眼看穿，但要寫成「掃出整個 repo 所有這種 pattern」的查詢，你得先想清楚「這個 bug 存在於哪種程式表示的哪個結構裡」。

### AST（Abstract Syntax Tree，抽象語法樹）：語法長相

AST 是 parser 直接吐出來的樹，忠實記錄「程式碼寫成什麼樣」。`memcpy(buf, pkt, len)` 在 AST 裡是一個 `CallExpr` 節點，底下掛三個 argument 子樹。

- **AST 能答的查詢**：「有沒有呼叫 `memcpy` / `strcpy` / `system`？」「有沒有 `strcpy` 的第三參數是常數？」「有沒有 `if (x = y)`（該用 `==` 卻寫成 `=`）？」——**純語法模式**。
- **AST 答不了的**：「這個 `memcpy` 的 `len` 是不是來自外部輸入且沒檢查？」AST 只知道 `len` 這個 token 出現在這裡，不知道 `len` 的值**從哪來**。

weggli 與 Semgrep 的 syntactic pattern（見 Ch 13、Ch 33）主力吃的就是 AST。它們快、好寫、零設定，但天花板就是「語法長相」。

### CFG（Control Flow Graph，控制流圖）：執行順序

CFG 把函式切成 basic block，用邊表達「執行可能怎麼從一塊走到下一塊」。上面那段函式的 CFG：

```
   [entry: len = n]
        │
   [if len > 128]
      │      │ true
false │      └──► [return]
      ▼
   [memcpy(buf, pkt, len)]
        │
      [exit]
```

- **CFG 能答**：「這條 `memcpy` 是不是在某個 `if` 檢查之後才執行？」「有沒有 path 從 `malloc` 到 `free` 到 `use`（UAF 的控制流雛形）？」——**執行順序、可達性**。
- **CFG 答不了**：「`memcpy` 用的 `len` 跟 `if` 檢查的 `len` 是不是同一個值、值域是什麼？」CFG 只有「先後」，沒有「值的傳遞」。

**踩雷預告**：CFG 不是 call graph。CFG 是**函式內**的 block 之間的邊；call graph 是**函式之間**「誰呼叫誰」的邊。`memcpy(...)` 在 CFG 裡是一個 block 裡的一條 statement，不是一條跨函式的邊。這兩個東西初學者最愛混，踩雷集錦再打一次。

### SSA（Static Single Assignment，靜態單賦值）：每個值一個明確定義

SSA 給每次賦值一個獨立版本名（`len₁`、`len₂`…），並在 control flow 匯流處插 φ 函式。它的價值是**「每個 use 都能直接指回唯一的 def」**，這正是 dataflow 的地基（Ch 4 詳談）。審計視角下，SSA 讓「這個 `len` 是哪個 `len`」變成查一條 def-use 邊，而不用重跑 reaching definitions。

SSA 本身很少是審計工具「對外」的查詢介面（你不會叫 analyst 手寫 φ），但它是**底層引擎算 dataflow 時的內部表示**。記住這句就好：SSA 是給機器算 def-use 用的，不是給人查的。

### PDG（Program Dependence Graph，程式依賴圖）：誰依賴誰

PDG 把兩種依賴畫成邊：

- **data dependence（資料依賴）**：`memcpy` 用了 `len`，而 `len` 由 `len = n` 定義 → 一條 data-dep 邊從定義指到使用。
- **control dependence（控制依賴）**：`memcpy` 這條 statement 是否執行，取決於 `if (len > 128)` 的結果 → 一條 control-dep 邊。

```
   len = n ──data──► memcpy(buf,pkt,len)
                          ▲
   if len>128 ──control───┘
```

- **PDG 能答**：「這個值從哪個定義流過來？」「這個 sink 的執行受哪些條件保護？」——**這就是 taint 查詢要的東西**。source→sink 的污染路徑，本質是 PDG 上的一條 data-dependence 路徑。
- **PDG 的痛點**：光有 PDG，你丟了「語法長相」。PDG 節點通常抽象成「statement / 運算」，你要問「這條 sink 是不是 `memcpy` 而不是 `printf`」時，PDG 節點上不一定帶得夠細的語法資訊。

## 核心矛盾：語法查詢與語意查詢要的表示不同

把上面攤成一張表，矛盾就浮出來了：

| 查詢類型 | 例子 | 最順手的表示 | 缺什麼 |
|---|---|---|---|
| 純語法 | 「呼叫了 `strcpy`？」 | AST | 不知值從哪來 |
| 控制流 | 「free 後還有 path 到 use？」 | CFG | 不知是不是同一指標 |
| 資料流 / taint | 「外部輸入流到 `memcpy` 的 len？」 | PDG | 丟了語法細節 |

真實漏洞查詢**同時**要語法與語意。以跑例為證，我們真正想寫的查詢是：

> 「找一個 `memcpy` 呼叫（**AST**：這是 memcpy），它的第三參數的值來自函式參數（**PDG**：data dependence 到 parameter），且在到達這個呼叫的路徑上（**CFG**：可達），對這個值的檢查上界大於目標 buffer 的大小（**AST + 常數 + PDG**）。」

一條查詢橫跨三種表示。如果三種表示是三個分離的資料結構，你得在它們之間手動 join——這正是 CPG 要解決的工程問題。

## CPG：把 AST + CFG + PDG 疊成一張帶屬性的圖

Yamaguchi 等人在 IEEE S&P 2014 的 *Modeling and Discovering Vulnerabilities with Code Property Graphs* 提出的做法直白到近乎粗暴：**把三種圖的節點對齊，共用同一批節點，各自的邊全部疊上去，節點與邊都帶屬性（property）**。

「合一」的關鍵洞見是：這三種表示其實**共享同一批底層程式元素**。`memcpy(buf, pkt, len)` 這個呼叫：

- 在 AST 裡是一個 `Call` 節點（帶屬性 `name="memcpy"`、`arity=3`）；
- 在 CFG 裡是控制流某個位置的一個節點；
- 在 PDG 裡是 data/control dependence 的一個端點。

**它們指的是同一件事**。所以不需要三份節點，用**一份節點**、掛上三種邊就好。合一後，一條查詢可以沿 AST 邊確認「這是 memcpy」，沿 data-dep 邊追「len 從哪來」，沿 CFG 邊確認「可達」——全在一張圖上遍歷，不用跨結構 join。

### 跑例的 CPG 疊合圖

把前面那段 `handle` 的三種邊疊到同一批節點上（省略部分節點以求清楚）：

```
節點（同一批，AST 產生）          疊上的三種邊
──────────────────────────────────────────────────
 (param n) ───────────────┐
     │ AST                 │ DATA-DEP
 (assign: len = n) ◄───────┘
     │ AST                       │ DATA-DEP
 (pred: len > 128) ─CFG─► ...    │
     │ CONTROL-DEP               ▼
 (call: memcpy) ◄───────────────(arg len)
   name="memcpy"            ▲
   arity=3            CONTROL-DEP（受 if 保護）
     │ AST
 (arg buf)(arg pkt)(arg len)

圖例：
  AST         —— 語法父子（Call → 三個 arg）
  CFG         —— 執行順序（entry → if → memcpy → exit）
  DATA-DEP    —— 值傳遞（n → len → memcpy 的 len arg）
  CONTROL-DEP —— 執行條件（if len>128 → memcpy）
```

同一個 `(call: memcpy)` 節點，同時是 AST 的子樹根、CFG 的一個位置、PDG 的一個端點。**這就是 CPG 的全部魔法**：不是發明新資訊，是把既有三種資訊掛到同一批節點上，讓一次遍歷同時看見語法與語意。

### 這個漏洞在 CPG 上是一條怎樣的路徑

回到查詢。在 CPG 上，我們要找的 pattern 是一條混合邊的路徑：

```
(param / 外部來源)
   │  沿 DATA-DEP 邊往下
   ▼
(某個值 len)
   │  沿 DATA-DEP 邊
   ▼
(call 節點，AST 屬性 name="memcpy")   ← 用 AST 屬性過濾出 memcpy
   且第一參數（AST 邊）指向一個 stack buffer（AST：局部陣列宣告，size=64）
   且沿路的 CONTROL-DEP 檢查（if）的上界常數 > 64
```

用 Joern 的查詢語言（Scala DSL，Ch 29 正式教，這裡先看形狀）大致是：

```scala
cpg.call("memcpy")
   .filter(_.argument(3).reachableBy(cpg.parameter).nonEmpty)  // 第三參數 taint 自參數
   .l
```

`call("memcpy")` 吃的是 **AST 屬性**；`reachableBy(...)` 走的是 **data-dependence 邊**。一行 query，兩種表示，這是分離結構做不到的簡潔。

## 兩種存法，同一件事：Joern 的圖 vs CodeQL 的關聯式資料庫

CPG 是「概念」——「把語法/控制/資料依賴掛到同一批帶屬性的節點上」。**這個概念可以用不只一種資料庫存**：

- **Joern**：真的存成一張 property graph（節點+邊+屬性），底層是圖資料庫（早期 Neo4j，現在自家格式），查詢語言是 Scala DSL 的圖遍歷（`.call(...).reachableBy(...)`）。**Ch 29–32**。
- **CodeQL**：存成一組**關聯式資料表**（relational database），每種程式元素、每種邊都是一張 table，查詢語言 QL 是 Datalog 風格的邏輯查詢，dataflow 靠 library 建在這些 table 上。**Ch 18–28**。

圖遍歷與關聯式查詢是**可互轉的**——一條邊就是一張兩欄的關係表，圖上「沿邊走一步」就是關係表的一次 join。所以「Joern 用圖、CodeQL 用關聯式」不是本質差異，是**同一個 CPG 概念的兩種存法與兩種查詢語言**。你在 Part 4 學 CodeQL 的 dataflow library、在 Part 5 學 Joern 的 `reachableBy`，底層都是在同一種疊合資訊上做可達性——這條橋接後面兩個 Part 會反覆回來踩實。

## 踩雷集錦

**錯誤直覺：「AST 搜尋就能抓資料流 bug。」**
正確認識：AST 只有語法長相。「`memcpy` 的 len 來自外部且沒檢查」需要 data-dependence，那是 PDG 的邊，AST 上根本沒有。你能用 AST 抓「有沒有呼叫 `strcpy`」（一律可疑），但抓不到「這個 `strcpy` 的 source 是否可控」。誤把純語法工具（weggli/Semgrep syntactic）當 taint 引擎用，結果是海量誤報或漏報。要 taint 就得上 taint mode 或 CPG（Ch 14、Ch 30）。

**錯誤直覺：「CFG 就是 call graph。」**
正確認識：CFG 是**函式內** basic block 的執行順序邊；call graph 是**函式間**「誰呼叫誰」的邊。跨函式 taint（Ch 5 IFDS）要的是把 CFG（每個函式一張）用 call graph 的 call/return 邊縫成 **supergraph（超級圖）**。把兩者混為一談，你會以為「函式內 CFG 可達」等於「整個程式可達」，漏掉跨函式的傳播。

**錯誤直覺：「CPG 合一了，所以它萬能、sound。」**
正確認識：CPG 只是**把三種既有資訊放同一張圖**，它不會憑空生出它沒建的資訊。CPG 上的 data-dependence 邊，精度完全取決於**底層的 points-to / alias 分析**有多準（Ch 6）。指標 alias 沒算對，data-dep 邊就連錯或漏連，CPG 上的 taint 查詢照樣漏報。CPG 解決的是「表達力」與「查詢方便」，不是「分析精度」——精度是另一條軸。

**錯誤直覺：「PDG 有 data-dependence，就能直接跨函式追污染。」**
正確認識：單一函式的 PDG 在函式邊界就斷了。`len` 傳進 `handle` 之前在 caller 怎麼來的，函式內 PDG 看不到。跨函式要 interprocedural 的做法（把 summary 縫進來，Ch 5）。以為函式內 PDG 就是全域 taint，是漏報大戶。

**錯誤直覺：「SSA 是給人查的一種表示。」**
正確認識：SSA 幾乎永遠是**引擎內部**用來算精確 def-use 的表示，不是對 analyst 的查詢介面。你在 CodeQL/Joern 寫查詢面對的是 AST 屬性與 dataflow API，底層引擎才用 SSA 幫你把 def-use 算好。別想「我來寫一條查 φ 節點的規則」——那不是這層該碰的。

## 進階延伸

- **CPG 之外的邊**：原始 CPG 論文只合 AST+CFG+PDG。現代 Joern 的 CPG 還疊了 **call graph 邊、type hierarchy、dominance**，把跨函式與型別資訊也放進同一張圖——本質是「同一批節點、更多種邊」的自然延伸。
- **CPG schema 是可擴充的**：因為節點/邊都帶 property，你可以自己加 pass 產生新的邊（例如自訂的 sanitizer 標記），這是 Ch 31 Joern custom pass 的地基。
- **overlay 概念**：Joern 把「CFG」「dataflow」等當成一層層 overlay，逐層計算疊到 base AST 上——這解釋了為什麼 Joern 可以「無 build」先給你 AST+CFG，dataflow overlay 之後才算（練習 E 會踩到）。

## 本章重點整理

- AST 答語法、CFG 答執行順序、PDG 答依賴（data/control）、SSA 是引擎內部算 def-use 的表示。**真實漏洞查詢同時要語法與語意**，任一單獨表示都不夠。
- **CPG（Yamaguchi 2014）** 把 AST+CFG+PDG 疊到**同一批帶屬性的節點**上，讓一條查詢能一次沿多種邊遍歷，同時吃語法（AST 屬性）與語意（data/control-dep）。
- 漏洞在 CPG 上是一條**混合邊的路徑**：AST 屬性過濾出 sink，data-dep 邊往上追 source，control-dep/CFG 確認可達與保護條件。
- **Joern（圖）與 CodeQL（關聯式 table）是同一個 CPG 概念的兩種存法**；圖遍歷等價於關聯式 join。
- CPG 解決「表達力與查詢方便」，**不解決精度**——data-dep 邊準不準，取決於底層 points-to（Ch 6）。

## 自我檢核

- 不看上文，說出 AST、CFG、PDG 各自能答哪類漏洞查詢、各缺什麼。跑例的 `memcpy` bug 分別「存在於」哪種表示的哪個結構？
- 為什麼「合一」不是發明新資訊？CPG 的哪個工程性質（節點/邊帶什麼）讓一條查詢能同時吃語法與語意？
- 用一句話說「CFG 不是 call graph」的差別，並解釋跨函式 taint 為什麼因此需要 supergraph。
- 如果底層 points-to 分析把兩個其實 alias 的指標判成不 alias，CPG 上的 taint 查詢會漏報還是誤報？為什麼這證明「CPG 不等於 sound」？
- Joern 的 `reachableBy` 與 CodeQL 的 dataflow library，為什麼說它們底層是同一件事？

## 延伸閱讀

- **Yamaguchi et al., *Modeling and Discovering Vulnerabilities with Code Property Graphs*, IEEE S&P 2014**（[連結](https://ieeexplore.ieee.org/document/6956589)）——CPG 原始論文。讀 Section III（CPG 定義與三種圖的合成）與 Section IV（用 graph traversal 表達漏洞 pattern）。前提：本章 + 你已有的 AST/CFG/PDG 基礎。這是全 Part 5 的理論根。
- **Ferrante, Ottenstein, Warren, *The Program Dependence Graph and Its Use in Optimization*, TOPLAS 1987**——PDG 的原始論文。讀 data/control dependence 的定義那節，補實你對「control dependence」的精確理解（比直覺的「受 if 保護」嚴謹）。前提：CFG 與 dominance。
- **Joern 官方文件的 *Code Property Graph* 章節**（[docs.joern.io](https://docs.joern.io/)）——看現代 CPG 的實際 schema（節點型別、邊型別），對照論文的「理想 CPG」與工程實作的差異。前提：本章。銜接 Ch 29。
- **CodeQL 文件 *About the CodeQL database***（[codeql.github.com/docs](https://codeql.github.com/docs/)）——看 CodeQL 怎麼把程式存成關聯式 table，親眼確認「圖的另一種存法」這句話。前提：本章對 CPG 的概念。銜接 Ch 20。

我們現在知道漏洞查詢的載體長怎樣（CPG），也知道 data-dependence 邊是 taint 的核心。但那些邊怎麼**算出來**？下一章從 lattice 與 fixpoint 把 dataflow 的引擎拆開，帶一個能跑的迷你 dataflow engine。

→ [Ch 4 資料流分析](./04-dataflow-analysis.md)
