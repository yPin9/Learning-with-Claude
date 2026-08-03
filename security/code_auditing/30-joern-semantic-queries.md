# Ch 30 — Joern 語意查詢

> **目標**：把 Ch 29 的語法查詢升到 **dataflow 語意查詢**。你會用 `reachableBy`（sink 能不能被 source 到達，回布林/集合）與 `reachableByFlows`（把整條 flow path 印出來），對 `vuln.c` 真跑一條 taint 查詢——攻擊者控制的 `len`（第 7 行 read 進來）→ `memcpy` 的 size 參數（第 10 行），把那條路徑逐節點印出來。這是本章**必成的核心 demo**。你會搞懂：Joern 的 dataflow 走的是 DDG（Ch 29 埋的資料依賴邊）、source/sink 要選對節點（argument vs call）、`reachableBy` 的方向、以及它跟 CodeQL global taint（Ch 22）是**同一件事的圖版本**——只是一個用 QL、一個用 Scala traversal，且 Joern 的 fuzzy 讓它更寬鬆。
>
> **環境**：Joern 4.0.594，WSL Ubuntu 22.04。靶 `~/audit-lab/vuln.c`。所有 flow path 照貼真跑輸出。對回 [Ch 22 CodeQL global taint](./22-codeql-global-taint.md)。

上一章你會找「哪裡有 memcpy」——那是**語法**問題。但漏洞不是「有沒有 memcpy」，而是「**攻擊者控制的值有沒有一條路徑流到 memcpy 的大小參數而且沒被檢查**」。這是**語意**問題，要追資料流。CodeQL 用 `TaintTracking::Global`（Ch 22）解它；Joern 用一個更直白的機制：**在 DDG 上做可達性查詢**。source 是圖上一組節點、sink 是另一組節點，問「從 source 出發沿著資料依賴邊，能不能走到 sink」。能，就有 flow。

這一章的核心就一條查詢，但要把它拆到骨子裡——因為 source/sink 選錯節點是 Joern taint 最常見的坑。

## 核心概念：dataflow = DDG 上的可達性

Ch 29 說 CPG 裡有 DDG（Data Dependency Graph）邊：某個變數用到的值是從哪個定義來的。Joern 的 taint 查詢就是在這張 DDG 上走：

```
source 節點集合 ──沿 DDG 邊(誰的值流到誰)──> ... ──> sink 節點集合
                                                          │
     snk.reachableBy(src)  問：src 能不能沿邊走到 snk？────┘
     snk.reachableByFlows(src) 問：能的話，路徑長怎樣？把每一步印出來
```

兩個核心 step：

- **`snk.reachableBy(src)`**：回傳「`src` 裡能到達 `snk` 的那些 source 節點」（一個集合）。你通常拿 `.size` 看有沒有（>0 就有 flow）。
- **`snk.reachableByFlows(src)`**：回傳每一條**具體路徑**（`Path` 物件），`.p` 印出來是一張逐節點的表——這是報告漏洞時你要貼的東西，因為它證明「不只是有 flow，而且 flow 長這樣」。

**方向很重要，且反直覺**：呼叫寫成 `sink.reachableByFlows(source)`——**接收者是 sink，參數是 source**。中文讀是「sink 能被 source 到達的那些 flow」。寫反成 `source.reachableByFlows(sink)`（問 source 能不能被 sink 到達）在正向 flow 的漏洞裡通常回空——這是新手第一個踩的坑。

## source/sink 選節點：argument vs call

這是 Joern taint 最關鍵、最容易錯的一步。source 和 sink 都是**一組 CPG 節點**，你怎麼選決定了查得對不對。

以 `vuln.c` 為例，我們要追的是「`read` 讀進來的 `len`」流到「`memcpy` 的第三個參數（size）」：

```c
read(fd, &len, sizeof(len));   // len 這裡被寫入（攻擊者控制）
...
memcpy(buf, data, len);        // len 這裡當 size 用（sink）
```

- **source 要選什麼？** 「`read` 呼叫的參數」——`cpg.call.name("read").argument`。`read` 把資料寫進它的 buffer 參數，那個 buffer（這裡是 `&len`）之後承載了攻擊者的值。用 `.argument` 拿到 call 的引數節點當汙染起點。
- **sink 要選什麼？** 「`memcpy` 呼叫的參數」——`cpg.call.name("memcpy").argument`。要更精準可以只選第三個參數 `.argument(3)`（size）。

**為什麼不是選 call 本身？** 選 `cpg.call.name("memcpy")`（call 節點）和 `cpg.call.name("memcpy").argument`（引數節點）是不同的東西。taint 流的是**值**——值在 argument（identifier/expression）上，不在 call 節點上。選錯（拿 call 當 source/sink）常常查不到，因為 DDG 邊連的是 argument 之間的資料依賴。**先寬（`.argument` 全選）跑通，再窄（`.argument(3)` 指定位置）收斂**是實務做法。

## 真跑一：核心 demo — read 的 len → memcpy 的 size

這是本章必成的 demo。script `ch30.sc`：

```scala
importCode(inputPath="vuln.c", projectName="ch30")
def src = cpg.call.name("read").argument
def snk = cpg.call.name("memcpy").argument
val flows = snk.reachableByFlows(src)
println("num flows: " + flows.size)
flows.p.foreach(println)
println("=== reachableBy count ===")
println(snk.reachableBy(src).size)
```

跑 `joern --script ch30.sc`。真跑輸出裡最關鍵的一條 flow（照貼）：

```
┌──────────┬───────────────────────────┬────┬──────┬──────┐
│nodeType  │tracked                    │line│method│file  │
├──────────┼───────────────────────────┼────┼──────┼──────┤
│Identifier│read(fd, &len, sizeof(len))│7   │handle│vuln.c│
│Call      │read(fd, &len, sizeof(len))│7   │handle│vuln.c│
│Identifier│malloc(len)                │8   │handle│vuln.c│
│Call      │malloc(len)                │8   │handle│vuln.c│
│Identifier│data = malloc(len)         │8   │handle│vuln.c│
│Identifier│read(fd, data, len)        │9   │handle│vuln.c│
│Identifier│memcpy(buf, data, len)     │10  │handle│vuln.c│
│Identifier│memcpy(buf, data, len)     │10  │handle│vuln.c│
└──────────┴───────────────────────────┴────┴──────┴──────┘
```

**這就是漏洞的完整資料流證據**：攻擊者控制的 `len` 從第 7 行 `read(fd, &len, ...)` 進來 → 第 8 行流進 `malloc(len)`（決定 heap buffer 大小）→ 第 9 行 `read(fd, data, len)` → 第 10 行 `memcpy(buf, data, len)`（`buf` 只有 64 byte，`len` 攻擊者可控 → OOB write）。這條路徑就是 Ch 24 那類 CodeQL C/C++ memory-safety 查詢要抓的東西，Joern 用一條 `reachableByFlows` 印了出來。

因為 `src`/`snk` 我們選得寬（`.argument` 全選，`read` 有兩處、`memcpy` 三個參數），`flows.size` 會回好幾條（不同 argument 組合的路徑），`reachableBy` count 這次是 15。這是「先寬跑通」的正常現象——路徑有雜訊。下一個 demo 收窄它。

## 真跑二：收窄到精準的單一 flow

把 source 鎖在「第 7 行 read 的第 2 個參數（`&len`）」、sink 鎖在「memcpy 的第 3 個參數（size）」：

```scala
importCode(inputPath="vuln.c", projectName="ch30b")
def src = cpg.call.name("read").lineNumber(7).argument(2)   // &len
def snk = cpg.call.name("memcpy").argument(3)               // size 參數
val flows = snk.reachableByFlows(src)
println("PRECISE num flows: " + flows.size)
flows.p.foreach(println)
```

真跑輸出：

```
PRECISE num flows: 1

┌──────────┬───────────────────────────┬────┬──────────────┬──────┐
│nodeType  │tracked                    │line│method        │file  │
├──────────┼───────────────────────────┼────┼──────────────┼──────┤
│Call      │read(fd, &len, sizeof(len))│7   │handle        │vuln.c│
│Identifier│malloc(len)                │8   │handle        │vuln.c│
│Call      │malloc(len)                │8   │handle        │vuln.c│
│Identifier│data = malloc(len)         │8   │handle        │vuln.c│
│Identifier│read(fd, data, len)        │9   │handle        │vuln.c│
│Identifier│read(fd, data, len)        │9   │handle        │vuln.c│
│Identifier│read(fd, data, len)        │9   │handle        │vuln.c│
│Identifier│memcpy(buf, data, len)     │10  │handle        │vuln.c│
└──────────┴───────────────────────────┴────┴──────┴──────┘
```

`PRECISE num flows: 1`——**恰好一條**。收窄 source/sink 到具體引數位置，雜訊路徑消失，只留下那條 `len` 從 read 到 memcpy size 的乾淨 flow。這就是報告時你要貼的東西：一條路徑、每一步在哪一行。**寬選（`.argument`）用來偵察「有沒有 flow」，窄選（`.argument(n)` + `.lineNumber(n)`）用來產出乾淨的 PoC 級證據**——兩種都要會。

## CPGQL dataflow 進階：where / filter / 組合

`reachableByFlows` 之外，你常要對 source/sink/path 加條件：

- **`.where(...)`**：對 traversal 加子查詢過濾。例：只要 sink 是 `memcpy` 且它所在 method 名叫 `handle`——`cpg.call.name("memcpy").where(_.method.name("handle")).argument`。
- **`.filter(...)`**：對每個節點跑一個布林條件。例：只留 argument 是 identifier 的——`.argument.filter(_.isIdentifier)`。
- **path 上加條件**：`reachableByFlows` 回的 `Path` 可以再過濾，例如「flow 中間有沒有經過某個 sanitizer 函式」——把「經過 `check_len(...)` 的 flow」濾掉，就是在 Joern 裡建模 sanitizer（比 CodeQL 的 `isBarrier` 手動，但同一個概念）。
- **跨 method 流**：`reachableByFlows` **預設就跨 method**（inter-procedural）。上面的 flow 全在 `handle` 內，但如果 `len` 是傳給另一個函式再用，Joern 也會沿 call 邊追過去（前提：那個函式的定義在 CPG 裡；未定義函式的 taning 傳遞要靠 Ch 31 的自訂 semantic）。

一個實用組合——「source 是任何 `read`/`recv`，sink 是任何 `mem*` 家族且在 argument 位置」：

```scala
def src = cpg.call.name("read|recv|fread").argument
def snk = cpg.call.name("memcpy|memmove|strcpy|strncpy").argument
snk.reachableByFlows(src).size
```

這是「一把撒網找 buffer 相關 taint」的偵察查詢，跟 Ch 24 CodeQL 的 memory-safety 查詢做同一件事。

## 對比 CodeQL：同一件事的兩種長相

| 面向 | CodeQL global taint（Ch 22） | Joern dataflow（本章） |
|---|---|---|
| 底層 | 在 build 過的精準 IR 上追 taint | 在 fuzzy CPG 的 DDG 上做可達性 |
| 表達 | QL class：`isSource`/`isSink`/`isBarrier` | Scala traversal：`snk.reachableByFlows(src)` |
| source/sink | override predicate（宣告式） | 選一組節點（`.argument` 等，程序式） |
| sanitizer | `isBarrier`（一級公民） | path 過濾（手動、非一級公民） |
| 精度 | 高（type/alias/build 資訊） | 較寬鬆（fuzzy，近似） |
| 前提 | **必須 build** | **不用 build** |

**「同一件事的圖版本」**是本章的一句話：兩者都在解「source 到 sink 的可達性」，只是 CodeQL 把它包成 QL 的 dataflow 函式庫（宣告式、精準、要 build），Joern 把它攤成 DDG 上的 traversal（程序式、寬鬆、不用 build）。**fuzzy 讓 Joern 更寬鬆**：它會查到 CodeQL 因為型別/別名分析而放過的 flow（更多**誤報**候選），也可能因為 DDG 沒連上而漏（**漏報**）——但這份「更寬鬆」正是它在編不起來的 target 上還能給你東西的原因。哪個更好取決於你 build 得了 build 不了（Ch 32 詳論）。

## 踩雷集錦

**錯誤直覺：「source/sink 選 call 節點就好（`cpg.call.name("memcpy")`）。」**
正確認識：taint 流的是**值**，值在 **argument**（identifier/expression）上，不在 call 節點上。DDG 邊連的是 argument 之間的資料依賴，所以 source/sink 要選 `.argument`（或指定 `.argument(n)`）。拿 call 節點當 source/sink，`reachableByFlows` 常常回空，你會誤以為「沒漏洞」，其實是節點選錯。先 `.argument` 全選跑通，再收窄。

**錯誤直覺：「`reachableByFlows` 誰放前面都一樣。」**
正確認識：方向是 `sink.reachableByFlows(source)`——**接收者是 sink、參數是 source**。寫反成 `source.reachableByFlows(sink)`（問 source 能否被 sink 到達）在正向 flow 漏洞裡通常回空。記法：中文讀「sink 能被 source 到達」，接收者永遠是你要保護的那個危險點（sink）。

**錯誤直覺：「Joern 的 taint 跟 CodeQL 一樣精、結果可以直接信。」**
正確認識：Joern 在 fuzzy CPG 上跑，**比 CodeQL 寬鬆**——更多誤報候選、也可能因 DDG 沒連上而漏報。它的 flow path 是「這張近似圖上的一條路徑」，不是「編譯器語意保證的真 flow」。你要拿它當**偵察與線索**（快速在編不起來的 target 上找到候選），而不是當「精準判定」。真要定案，能 build 就再用 CodeQL 複查（Ch 35 漏斗），不能 build 就人工 review 那條 path。

**錯誤直覺：「查不到 flow = 沒漏洞。」**
正確認識：查不到只代表「這張 fuzzy DDG 上沒連起來」。常見斷點：taint 經過一個**未定義函式**（Joern 不知道它怎麼傳遞 taint，DDG 就斷了——這正是 Ch 31 自訂 semantic 要補的）、經過複雜的 alias（fuzzy 沒精準指標分析）、或節點被解成非預期形狀。「沒命中」是「這張圖上沒找到」，不是「安全」——跟 Ch 12 的漏報同一個道理。

**錯誤直覺：「dataflow 查詢跑一下就好，跟語法查詢差不多快。」**
正確認識：`reachableByFlows` 在大 CPG 上**可能很慢**——它要在 DDG 上做路徑搜尋，source/sink 選太寬（例如 `cpg.identifier` 全選當 source）會爆炸。小靶如 `vuln.c` 秒回，但真實大 codebase 上要**先把 source/sink 收窄**（指名函式、限定 method、指定 argument 位置）再跑，否則等到天荒地老。這跟 Ch 28 CodeQL 查詢效能是同類問題：dataflow 貴，範圍要圈小。

## 進階延伸

- **`ossdataflow` 與 semantic 的來源**：Joern 的 dataflow engine 內建一套 semantics（哪些函式怎麼傳遞 taint，如 `memcpy` 把 arg2 傳到 arg1）。`reachableByFlows` 背後就靠這套。當你的 target 用了引擎不認識的函式（自訂 wrapper、未定義函式），taint 會斷——下一章教你**自訂 semantic** 補上，對回 CodeQL 的 models-as-data（Ch 23）。
- **flow path 上做 sanitizer 建模**：CodeQL 的 `isBarrier` 在 Joern 裡沒有一級對應，但你可以對 `reachableByFlows` 回的 path 做 `.filterNot(_.elements.isCall.name("check_len").nonEmpty)` 之類，把「經過檢查函式」的 flow 濾掉。這是把 Ch 9 的 sanitizer 概念用 path 過濾實作。
- **`reachableByFlows` 的變體與去重**：真實查詢常配 `.dedup`、`.groupBy` 去掉重複路徑，或用 `.passes`/`.passesNot` 限制 flow 必須/禁止經過某類節點。去讀 Joern 文件的 "Data Flow" 那節，把這幾個組合起來能表達相當複雜的 flow policy。

## 本章重點整理

- Joern 的 dataflow = **在 DDG 上做可達性查詢**：`snk.reachableBy(src)`（有沒有，回集合）與 `snk.reachableByFlows(src)`（把每條路徑逐節點印出來）。
- **source/sink 選 `.argument`（值所在的節點），不是 call 節點**——taint 流的是值，DDG 邊連 argument。先寬（`.argument`）偵察、再窄（`.argument(n)`+`.lineNumber`）產乾淨證據。
- **方向是 `sink.reachableByFlows(source)`**（接收者是 sink），寫反回空。
- 真跑 `vuln.c`：`len` 從第 7 行 `read` → `malloc` → 第 10 行 `memcpy` size，`reachableByFlows` 印出完整路徑；收窄後恰好一條乾淨 flow——這就是漏洞的資料流證據。
- Joern dataflow 與 CodeQL global taint 是**同一件事的兩種長相**：DDG traversal（程序式、寬鬆、不用 build）vs QL dataflow 函式庫（宣告式、精準、要 build）。fuzzy 讓 Joern 更寬鬆（更多誤報候選、也可能漏），「查不到 ≠ 安全」。

## 自我檢核

- 用一句話說 Joern 的 dataflow 查詢底層在做什麼（提示：哪張圖、什麼運算）。
- 不看上文：寫出「`read` 的參數流到 `memcpy` 的參數」的 `reachableByFlows` 查詢。source/sink 你選 `.argument` 還是 call 節點，為什麼？
- `snk.reachableByFlows(src)` 和 `src.reachableByFlows(snk)` 差在哪？在「攻擊者輸入流到危險 sink」的正向漏洞裡，哪個會回空？
- 本章 demo 裡 `flows.size` 一開始是好幾條、收窄後變 1。這兩種選法（寬 vs 窄）各自的用途是什麼？
- Joern 的 taint「比 CodeQL 寬鬆」，這在結果上表現為什麼（誤報/漏報）？為什麼這份「寬鬆」反而是它在編不起來的 target 上的價值？
- 你對一個大 codebase 跑 `reachableByFlows` 跑到卡死，第一件該做的事是什麼？（對回 Ch 28 的思路）

## 延伸閱讀

- **Ch 22 CodeQL global taint tracking（./22-codeql-global-taint.md）**——同一件事的 QL 版本，`isSource`/`isSink`/`isBarrier` 宣告式建模。讀哪裡：global taint 的三段結構。學什麼：跟本章 `reachableByFlows` 對照，體會「宣告式 + 精準 + 要 build」vs「程序式 + 寬鬆 + 不用 build」的差別。前提：本章。
- **Joern 官方文件（docs.joern.io）"Data Flow Analysis" / "Reachability" 章節**——`reachableBy`/`reachableByFlows`、path 操作、semantics 的權威說明。讀哪裡：Reachability 與 Data Flow Semantics 兩節。學什麼：本章沒展開的 path 過濾、`.passes`/`.passesNot`、去重。前提：本章。
- **Yamaguchi et al., S&P 2014（第 Ch 29 已列）Section IV**——CPG 上用 graph traversal 表達漏洞（含 taint-style）的理論原型。學什麼：`reachableByFlows` 這種可達性查詢的學術根。前提：Ch 3、本章。
- **Ch 24 CodeQL C/C++ 記憶體安全（./24-codeql-cpp-memory-safety.md）**——本章 demo 抓的正是這類 memory-safety flow 的 CodeQL 版。學什麼：同一個 `read→memcpy` OOB 在精準工具上怎麼建模，跟 Joern 版對比。前提：本章。

flow 查得到了，但你也看到兩個限制：taint 經過**未定義函式**會斷、預設的 scan 規則不一定覆蓋你的 target。下一章補這兩塊——自訂 CPG pass 與自訂 dataflow semantic（告訴 Joern「這個函式怎麼傳遞 taint」，對回 CodeQL models-as-data）、用 `.sc` script 批次查詢並匯出 JSON、以及 `joern-scan` 一鍵掃常見漏洞。

→ [Ch 31 Joern 自訂 pass 與 semantic](./31-joern-custom-passes.md)
