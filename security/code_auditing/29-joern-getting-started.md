# Ch 29 — Joern 上手

> **目標**：把 Ch 3 的 CPG 理論變成手上真跑的工具。你會裝好並啟動 Joern（一個開源的 **CPG（Code Property Graph，程式碼屬性圖）平台**）、用 `importCode` 把 `vuln.c` 匯進去、用它的 **Scala-based 查詢 DSL（Domain-Specific Language，領域特定語言）** 跑出「列所有 method、找 memcpy call、看某函式的參數」這幾條基礎查詢，全部貼真跑輸出。重點不是學 Scala（你只需要 DSL 的一小塊子集），而是搞懂 **Joern 為什麼不需要 build 就能查**——這正是它跟 CodeQL（Ch 18-28）最根本的分界，也是後面 Part 存在的理由。
>
> **環境**：Joern（bundle 版，4.0.594），WSL Ubuntu 22.04。二進位在 `~/audit-tools/joern/joern-cli/`（`joern`、`joern-parse`、`joern-scan` 已在 PATH）。共用靶 `~/audit-lab/vuln.c`。所有輸出照貼真跑結果。CPG 理論對回 [Ch 3 程式表示與 CPG](./03-program-representations-cpg.md)。

前面十章你在 CodeQL 上把 CPG 玩到很深：QL 語言、database、local/global dataflow、models-as-data。但整個 CodeQL 有一個你可能沒特別在意、卻決定它適用範圍的前提——**它必須先 build 你的 code**。`codeql database create` 底層要 hook 進編譯過程，看著編譯器怎麼解析每個 translation unit，才能建出精準的 database。這在能編譯的專案上是它精度的來源；在**編不起來的 target**（閉源殘缺 SDK、韌體抽出的片段、只有幾個檔案沒有 build 系統）上，它直接卡死——連 database 都建不出來，後面的查詢無從談起。

Joern 走另一條路。它是 **fuzzy parser（模糊解析器）**：不需要能編譯、不需要 header 齊全、不需要 build 系統，甚至語法有殘缺也照吃，盡力解析出一份**近似的** CPG。這份 CPG 沒 CodeQL 那麼精準（後面會誠實面對它漏了什麼），但「有一份能查的圖」對編不起來的 target 來說，是從 0 到 1 的差別。這一章先把它跑起來、把基礎查詢摸熟；下一章上 dataflow；Ch 32 正面對照兩者；練習 E 讓你親手驗證 Joern 的殺手級場景。

## 核心概念：Joern 是什麼、憑什麼不用 build

先把定位講死：

```
┌──────────────────────────────────────────────────────────────┐
│ Joern                                                          │
│  - 開源（Apache 2.0）的 CPG 平台，源自 Yamaguchi S&P'14 論文   │
│  - fuzzy parser：不 build、不需 header 齊全，殘缺 code 也解析    │
│  - Scala-based 互動 shell + 查詢 DSL（CPGQL）                   │
│  - 一份 CPG = AST + CFG + DDG + CDG 疊在同一張圖上              │
└──────────────────────────────────────────────────────────────┘
```

**憑什麼不用 build？** CodeQL 靠 hook 編譯器拿到「編譯器眼中的程式」（type 全解出、macro 展開、include 併入）。Joern 不 hook 編譯器，它自己有一個容錯的 C/C++ 前端（`c2cpg`），看到 `uint32_t` 沒定義它不會像 gcc 那樣報 error 停下來，而是當成一個未知型別的 identifier 繼續往下解析。看到呼叫一個沒宣告的函式，它就建一個 `CALL` 節點、名字記下來，不管那函式的定義在不在。**代價是近似**：它不知道 `uint32_t` 到底幾 byte、不知道那個未定義函式真正怎麼傳遞資料——但它給你一張能查的圖。

這是一種明確的 trade-off，不是「Joern 比較爛」也不是「Joern 比較強」：

- **能 build 的 target**：CodeQL 精度贏（type/macro/alias 都解得準），該用 CodeQL。
- **build 不了的 target**：Joern 是唯一還能跑 dataflow 的選擇，CodeQL 出局。

記住這條線，整個 Joern Part 都圍著它轉。

## CPG 的節點與邊：查詢查的是什麼

Joern 的 CPG 就是 Ch 3 那張「AST + CFG + PDG 疊起來」的圖，只是現在它是**可查詢的資料庫**。你查詢時操作的是這些**節點類型（node type）**：

| 節點類型 | 是什麼 | 例 |
|---|---|---|
| `METHOD` | 一個函式/方法 | `handle`、`main` |
| `CALL` | 一次函式呼叫 | `memcpy(...)`、`malloc(...)`、還有 `<operator>.assignment`（`=` 也是 call） |
| `IDENTIFIER` | 一個變數引用 | `len`、`buf`、`data` |
| `LITERAL` | 字面常數 | `64`、`0` |
| `METHOD_PARAMETER_IN` | 函式的形參 | `handle` 的 `fd` |
| `RETURN` | return 語句 | `return 0;` |
| `BLOCK` | 一個 `{...}` 區塊 | 函式體 |

節點之間有**邊（edge）**，對回 Ch 3 的三種圖：

- **AST 邊**：語法結構（`memcpy` call 的子節點是三個 argument）。
- **CFG 邊（Control Flow Graph）**：執行順序（第 7 行 read 之後走到第 8 行 malloc）。
- **DDG 邊（Data Dependency Graph，資料依賴圖）**：某變數的值從哪來（`memcpy` 的 `len` 依賴第 7 行 read 寫進去的 `len`）——**下一章的 dataflow 就是走這種邊**。
- **CDG 邊（Control Dependency Graph，控制依賴圖）**：某語句被哪個條件控制。

一個關鍵細節先埋下：**在 Joern 眼中，`=`、`&`（取址）、`sizeof` 這些運算子也是 CALL 節點**，名字叫 `<operator>.assignment`、`<operator>.addressOf`、`<operator>.sizeOf`。等下真跑列 call 名稱時你會看到它們，別被嚇到——這是 CPG 把所有「運算」統一成 call 的設計。

## Scala/CPGQL 查詢 DSL：你只需要這一小塊

Joern 的 shell 是一個 Scala REPL，查詢語言（俗稱 **CPGQL**）就是一串 Scala 方法鏈。你**不需要學整個 Scala**——只要會這幾個組件：

```
cpg.method.name("handle").parameter.l
└┬┘ └──┬─┘ └────┬──────┘ └───┬───┘ └┘
 │     │        │            │      └ .l = toList，求值印出結果（漏了它只印型別）
 │     │        │            └ traversal step：從 method 走到它的 parameter
 │     │        └ filter：只留名字叫 handle 的
 │     └ 起點：所有 METHOD 節點
 └ CPG 物件（匯入 code 後就有）
```

最常用的一把：

- **起點**：`cpg.method`（所有函式）、`cpg.call`（所有呼叫）、`cpg.identifier`、`cpg.literal`、`cpg.parameter`。
- **filter**：`.name("memcpy")`（精確）、`.name("mem.*")`（regex）、`.lineNumber(10)`。
- **traversal step**：`.parameter`、`.argument`、`.caller`、`.callee`、`.method`（從 call 回到它所在的 method）。
- **取屬性**：`.code`（原始碼字串）、`.name`、`.lineNumber`、`.typeFullName`。
- **求值**：`.l`（toList，**必寫**，否則只回一個惰性 traversal 型別，不印結果）、`.size`（數量）、`.p`（pretty-print，dataflow 常用）。

就這些。你會發現 90% 的查詢是「起點 → filter → 走幾步 → 取屬性 → `.l`」。

## 真跑一：匯入 code 並列出所有 method

啟動 Joern（第一次會慢，JVM + 載入 overlay），或直接用 `--script` 跑一個 `.sc` 檔。這裡用 script 方式（可重現、輸出乾淨）。`~/audit-lab/vuln.c`：

```c
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
void handle(int fd) {
    char buf[64];
    int len;
    read(fd, &len, sizeof(len));      // source: attacker-controlled len
    char *data = malloc(len);
    read(fd, data, len);
    memcpy(buf, data, len);           // sink: OOB write, len unchecked
    free(data);
}
int main(){ handle(0); return 0; }
```

script `ch29.sc`：

```scala
importCode(inputPath="vuln.c", projectName="ch29")
println("=== ALL METHODS ===")
cpg.method.name.l.foreach(println)
println("=== memcpy CALLS ===")
cpg.call.name("memcpy").map(c => s"line ${c.lineNumber.getOrElse(-1)}: ${c.code}").l.foreach(println)
println("=== handle PARAMS ===")
cpg.method.name("handle").parameter.map(p => s"${p.index}: ${p.typeFullName} ${p.name}").l.foreach(println)
println("=== all CALL names ===")
cpg.call.name.l.distinct.foreach(println)
```

跑：

```
$ cd ~/audit-lab && joern --script ch29.sc
```

真跑輸出（照貼，`[INFO]` 日誌略去）：

```
=== ALL METHODS ===
handle
<global>
main
<global>
malloc
<operator>.alloc
read
<operator>.addressOf
memcpy
<operator>.sizeOf
free
<operator>.assignment

=== memcpy CALLS ===
line 10: memcpy(buf, data, len)

=== handle PARAMS ===
1: int fd

=== all CALL names ===
<operator>.assignment
<operator>.alloc
read
<operator>.addressOf
<operator>.sizeOf
malloc
memcpy
free
handle
```

盯著看三件事：

1. **method 列表不只有 `handle`、`main`**。`malloc`、`read`、`memcpy`、`free` 也是 method——它們是**外部函式**，Joern 為每個被呼叫但沒定義的函式建一個 stub method 節點（這正是 fuzzy parse 的能力：呼叫的函式沒源碼也沒關係）。`<global>` 是每個檔案的全域 scope、`<operator>.*` 是運算子的合成 method。

2. **`memcpy` call 只有一個**（第 10 行），`c.code` 直接把原始碼 `memcpy(buf, data, len)` 印出來，`c.lineNumber` 給行號。這就是最基礎的 sink 定位——找危險函式在哪被呼叫。

3. **call 名稱裡有 `<operator>.assignment`（`=`）、`<operator>.addressOf`（`&`）、`<operator>.sizeOf`（`sizeof`）**。前面埋的伏筆兌現了：在 CPG 裡運算子就是 call。你查 `cpg.call.name("memcpy")` 拿到的是「真的函式呼叫」，但 `cpg.call` 全集包含這些運算子 call。

## 真跑二：看一個 method 的內部結構

進一步看 `handle` 裡有哪些 call、哪些 identifier，感受 CPG 怎麼拆解一個函式：

```scala
importCode(inputPath="vuln.c", projectName="ch29b")
println("=== calls inside handle (real functions only) ===")
cpg.method.name("handle").call
   .nameNot("<operator>.*")
   .map(c => s"line ${c.lineNumber.getOrElse(-1)}: ${c.name}  |  ${c.code}").l
   .foreach(println)
println("=== identifiers named len ===")
cpg.method.name("handle").ast.isIdentifier.name("len")
   .map(i => s"line ${i.lineNumber.getOrElse(-1)}: ${i.code}").l.foreach(println)
```

輸出：

```
=== calls inside handle (real functions only) ===
line 7: read  |  read(fd, &len, sizeof(len))
line 8: malloc  |  malloc(len)
line 9: read  |  read(fd, data, len)
line 10: memcpy  |  memcpy(buf, data, len)

=== identifiers named len ===
line 6: len
line 7: len
line 8: len
line 9: len
line 10: len
```

`.nameNot("<operator>.*")` 濾掉運算子 call，只剩四個真呼叫——這就是這個函式的「危險操作清單」的雛形。`.ast` 是「這個 method 的整棵 AST 子樹」，`.isIdentifier` 濾出 identifier 節點——`len` 這個變數在 6~10 行各出現一次（宣告 + 四次使用）。**這幾個 `len` 之間的 DDG 邊，就是下一章 dataflow 要走的路**。

## 邊界：fuzzy parse 會漏什麼

Joern 不 build 的代價是近似，這裡用一個具體例子看它「近似」在哪。試著問 `buf` 的緩衝區大小（`char buf[64]`）：

```scala
importCode(inputPath="vuln.c", projectName="ch29c")
cpg.method.name("handle").local.map(l => s"${l.name} : ${l.typeFullName}").l.foreach(println)
```

輸出：

```
buf : char[64]
len : int
data : char*
```

型別字串裡確實有 `char[64]`、`int`——但這是**字面文字**，不是「Joern 真的算出 buf 有 64 byte、int 有 4 byte」。CodeQL 因為 build 過，能精準拿到每個 type 的 size、alignment、layout；Joern 只有 parse 到的字串。要在 Joern 裡做「memcpy 大小超過 buf 容量」這種**需要算 buffer size 的**檢查，你得自己從 `char[64]` 這個字串把 64 摳出來（字串處理），而不是像 CodeQL 那樣有現成的 type-size API。這就是「近似」的具體長相：**結構在、精確語意常常不在**。

## 踩雷集錦

**錯誤直覺：「用 Joern 要先學會 Scala。」**
正確認識：你只需要 DSL 的一小塊子集——`cpg.method`/`cpg.call` 當起點、`.name()`/`.lineNumber()` 當 filter、`.parameter`/`.argument` 當 traversal step、`.code`/`.l` 取值求值。整個 Joern Part 用到的 Scala 語法不超過本章「查詢 DSL」那一節列的那幾個。真的要寫複雜自訂 pass 時（Ch 31）才會多碰一點 Scala，但那也是查文件照抄的程度。把它當「一種查詢語言」而不是「一門程式語言」。

**錯誤直覺：「fuzzy parse 出來的 CPG 跟 build 過的一樣完整、可信。」**
正確認識：fuzzy 是有代價的**近似**。它會為未定義函式建 stub、把未知型別當 identifier 硬解——結構大致對，但 macro 沒展開、type size 不知道、複雜的 template/預處理它可能解錯或漏節點。表現就是：某些 dataflow 可能漏（DDG 沒連上）、某些查詢命中不到（節點被解成別的形狀）。用 Joern 時心裡要有一條線：**「沒命中」不等於「安全」，只等於「這張近似圖上沒找到」**。這跟 Ch 12 講的漏報一樣，只是成因換成了 parser 的近似。

**錯誤直覺：「查詢寫完就有結果了。」**
正確認識：漏了 `.l`（或 `.toList`/`.size`/`.p`）的話，你在 shell 裡看到的是一個惰性 traversal 的**型別描述**，不是實際結果——查詢根本沒被求值。`cpg.call.name("memcpy")` 回一個 `Traversal`，`cpg.call.name("memcpy").l` 才真的跑出 list。這是新手最常見的「怎麼沒輸出」原因。

**錯誤直覺：「`cpg.call` 就是所有函式呼叫。」**
正確認識：`cpg.call` 包含**運算子 call**——`=` 是 `<operator>.assignment`、`&` 是 `<operator>.addressOf`、`sizeof` 是 `<operator>.sizeOf`、陣列索引、成員存取全都是 call 節點。你想找「真的函式呼叫」時要嘛精確指名（`cpg.call.name("memcpy")`），要嘛濾掉運算子（`.nameNot("<operator>.*")`）。忘了這點，你的「所有 call」統計會被一堆運算子灌爆。

**錯誤直覺：「CPG 沒建完就能查。」**
正確認識：`importCode` 之後 Joern 會跑一連串 overlay pass（建 CFG、DDG 等，日誌裡的 `ReachingDefPass` 等）。這些跑完 CPG 才完整。用 `--script` 時 Joern 會自動等它跑完再執行你的查詢，所以 script 模式不會踩到；但在互動 shell 裡如果你 `importCode` 後**立刻**在 overlay 還沒跑完時查 dataflow，可能拿到不完整結果。看到 `Code successfully imported` 和 overlay pass 的 completed 日誌再查。

## 進階延伸

- **`joern-parse` 與 CPG 的落地格式**：本章用 `importCode` 在 shell 裡一步到位，但生產流程常把「建 CPG」和「查 CPG」分開——`joern-parse target/ -o cpg.bin` 先把 CPG 存成 `.bin`，之後 `importCpg("cpg.bin")` 反覆查而不重 parse。大 codebase 上這省很多時間。去讀 `joern-parse --help`。
- **CPG schema 全貌**：本章只列了最常用的節點類型。完整的 schema（所有節點類型、所有邊類型、每個節點有哪些屬性）在 Joern 官方文件的 "CPG Specification"。當你的查詢「找不到某種東西」時，多半是你不知道它在 schema 裡叫什麼——查 schema 是解法。
- **`.dump` / `joern-export` 把圖畫出來**：`cpg.method.name("handle").dotCfg.l` 印出 Graphviz DOT，貼進 Graphviz 就能看到 `handle` 的 CFG 長什麼樣。第一次上手時把 AST/CFG/DDG 各畫一張，肉眼看「三張圖疊在一起」是最快建立直覺的方式，對回 Ch 3 的圖。

## 本章重點整理

- Joern 是**開源（Apache 2.0）的 CPG 平台**，源自 Yamaguchi S&P'14 論文，核心價值是 **fuzzy parser：不 build、不需 header 齊全、殘缺 code 也能解析出近似 CPG**——這是它跟 CodeQL 最根本的分界。
- 不 build 的代價是**近似**：未定義函式建 stub、未知型別當 identifier、type size/macro 這類精確語意常常拿不到。結構在、精確語意常不在。
- CPG 由節點（`METHOD`/`CALL`/`IDENTIFIER`/`LITERAL`/...）與邊（AST/CFG/DDG/CDG）組成；**運算子（`=`/`&`/`sizeof`）在 CPG 裡也是 CALL 節點**（`<operator>.*`）。
- 查詢 DSL 你只需一小塊子集：起點（`cpg.method`/`cpg.call`）→ filter（`.name`/`.lineNumber`）→ traversal（`.parameter`/`.argument`）→ 取屬性（`.code`/`.lineNumber`）→ **`.l` 求值**（漏了就只印型別）。
- 真跑 `vuln.c`：列出所有 method（含外部函式 stub）、定位 `memcpy` call（第 10 行）、看 `handle` 的參數與內部 call——這是後面 dataflow 的地基。

## 自我檢核

- Joern「不需要 build 就能查」，它憑什麼做到？這個能力的**代價**是什麼？用一個具體例子（如 type size 或 macro）說明它的近似在哪。
- 不看上文：寫出「列出所有 method」「找所有 `memcpy` 呼叫並印出行號與原始碼」「看 `handle` 函式的參數」這三條查詢。（主動回憶 DSL 的起點/filter/取值三段）
- 為什麼 `cpg.call.name.l.distinct` 的結果裡會有 `<operator>.assignment`？如果你只想統計「真的函式呼叫」有幾個，查詢要怎麼改？
- 你在 shell 裡打 `cpg.method.name("handle").parameter`（沒加 `.l`），會看到什麼、為什麼？
- Joern 對 `vuln.c` 的 method 列表裡出現了 `malloc`、`read`、`memcpy`——這些函式的定義並不在 `vuln.c` 裡，Joern 為什麼還列出它們？這說明了 fuzzy parse 的什麼特性？

## 延伸閱讀

- **Yamaguchi et al., "Modeling and Discovering Vulnerabilities with Code Property Graphs", IEEE S&P 2014**——Joern 的原始論文，CPG 這個概念就是這篇提出的。讀哪裡：Section III（CPG 怎麼把 AST+CFG+PDG 併成一張圖）與 Section IV（怎麼用 graph traversal 表達漏洞模式）。學什麼：你在 Joern 裡打的每條 `cpg.xxx` 查詢，理論根據都在這。前提：Ch 3。
- **Joern 官方文件（docs.joern.io）"Quickstart" 與 "Code Property Graph" 章節**——安裝、`importCode`、CPG schema（所有節點/邊類型）的權威來源。讀哪裡：CPG Specification 那頁當查表用（查某節點/屬性叫什麼名）。學什麼：本章沒列全的節點類型與 traversal step。前提：本章。
- **Ch 3 程式表示與 CPG（./03-program-representations-cpg.md）**——CPG 是什麼、AST/CFG/PDG 怎麼疊起來的理論。讀哪裡：CPG 那幾節。學什麼：Joern 查詢背後那張圖的結構，回頭讀會更有感。前提：無，這是 Joern 的理論底。
- **Fabian Yamaguchi 的 Joern 相關演講（各家安全會議的 "Joern" talk）**——作者本人講 Joern 的設計動機與用法，比文件更有「為什麼這樣設計」的脈絡。學什麼：fuzzy parse 的取捨、Joern 適合什麼場景。前提：本章。

基礎查詢會跑了，但這些都還是**語法查詢**——「哪裡有 memcpy」是語法問題。真正的漏洞審計要問的是**語意問題**：「攻擊者控制的 len 有沒有一條路徑流到 memcpy 的 size？」下一章把查詢從語法升到 dataflow 語意，`reachableByFlows` 一條查詢把那條 taint 路徑印出來——這是 Joern 的真本事，也是它跟 CodeQL global taint（Ch 22）做同一件事的圖版本。

→ [Ch 30 Joern 語意查詢](./30-joern-semantic-queries.md)
