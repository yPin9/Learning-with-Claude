# Ch 22 — CodeQL global taint tracking

> **目標**：從 local 升到 **global、跨函式的 taint tracking**——CodeQL 變體獵殺的核心武器。學會用現代 `module ... implements DataFlow::ConfigSig` + `TaintTracking::Global<Cfg>` 定義 source/sink/barrier，寫一條 `path-problem` query 對 `vuln.c` 真跑出從 `read` 到 `memcpy` 的完整 flow path，並看清 taint flow 與 value flow 的實測差異（一個算術運算就決定漏不漏）。順帶把舊 `TaintTracking::Configuration` class 標清、把 path query 的 metadata 講對——不然你看不到路徑。
> **環境**：CodeQL 2.26.2，靶 `~/audit-lab/vuln.c`（單函式）與 `~/audit-lab/vuln2.c`（跨函式 + 衍生長度 + bound check），對應 db `vuln-db` / `vuln2-db`。

Ch 21 的 `localFlow` 到函式邊界就斷。但真實漏洞幾乎都跨函式：`recv` 在 parser、`system` 在 handler，中間隔三層呼叫。這章把 flow 升到 global——CodeQL 底層就是 Ch 5 的 **IFDS**：把跨函式 taint 化約成一張 exploded supergraph 上的可達性，用 **summary edge**（函式摘要）避免對每個 call site 重算。你不用碰底層，只要宣告一個 config，library 就跑 IFDS 給你。

## 現代 API：ConfigSig + Global module

2.26.2 的 dataflow 用 **module 參數化（parameterized module）**。你寫一個實作 `DataFlow::ConfigSig`（signature）的 module，填三個 predicate，再把它餵給 `TaintTracking::Global` 或 `DataFlow::Global` 實體化出一個 flow module：

```ql
module MyCfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { ... }   // 攻擊者入口
  predicate isSink(DataFlow::Node sink)     { ... }   // 危險操作
  predicate isBarrier(DataFlow::Node node)  { ... }   // sanitizer（可選，預設無）
}
module MyFlow = TaintTracking::Global<MyCfg>;         // 實體化：taint flow
```

- `isSource` / `isSink` 對應 Ch 7 的 source / sink，`isBarrier` 對應 sanitizer。這就是 taint policy 的四要素在 QL 的直接映射。
- **`TaintTracking::Global<Cfg>` 給 taint flow**（含污染衍生 step），**`DataFlow::Global<Cfg>` 給 value flow**（只傳原值）。選哪個 = Ch 21 那條界線，選錯就漏報，本章下面實測給你看。
- 實體化後，`MyFlow` module 提供 `flow(a, b)`（布林可達）、`flowPath(source, sink)`（帶路徑）、`PathNode`、`PathGraph` 等。

**版本差異（務必記牢）**：你會在舊教材/舊 query 看到這種寫法——

```ql
// 舊 API（deprecated，但滿地都是）
class MyConfig extends TaintTracking::Configuration {
  MyConfig() { this = "MyConfig" }
  override predicate isSource(DataFlow::Node n) { ... }
  override predicate isSink(DataFlow::Node n) { ... }
}
from MyConfig cfg, DataFlow::Node s, DataFlow::Node t
where cfg.hasFlowPath(...) ...
```

`extends TaintTracking::Configuration` 的 **class-based** 寫法是舊 API。2.26.2 仍能編（有 deprecation 警告），但新程式一律用 **module + `ConfigSig`**。差別不只語法：新 API 效能更好、支援 flow state（Ch 23）更乾淨、是官方 query 現在的標準。看到 class-based config 就知道那是舊碼，能讀但別新寫。

## path-problem：讓路徑看得見

要輸出「從哪流到哪、中間經過哪些點」，query 必須是 **path-problem** 型，缺一不可的三件事：

1. **metadata `@kind path-problem`**——告訴 CodeQL 這是路徑查詢，`select` 的欄位規則不同。
2. **`import MyFlow::PathGraph`**——把 flow module 的路徑圖引進來，SARIF/IDE 才知道怎麼畫路徑。
3. **`select` 五欄格式**：`select element, source, sink, message, ...`——`source` 與 `sink` 必須是 `PathNode`，中間兩欄是路徑端點，CodeQL 靠它們渲染 code flow。

漏掉任一個，路徑就不顯示，你只會看到孤零零的 sink，甚至 `database analyze` 直接報 `NO_KIND_SPECIFIED` 錯誤。這是新手第一個踩的坑。

## 真跑（本章必成 demo）：read → memcpy 的完整 flow path

靶 `vuln.c`：`read(fd, &len, sizeof(len))` 寫入攻擊者控制的 `len`，`memcpy(buf, data, len)` 拿它當 size，中間沒有 bound check。**source = `read` 的第二個引數（output argument，寫回 `len`）**、**sink = `memcpy` 的 size 引數**。

```ql
/**
 * @name Attacker-controlled length reaches memcpy without bound check
 * @kind path-problem
 * @id audit/global-taint-len
 * @problem.severity error
 * @tags security
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import semmle.code.cpp.dataflow.new.DataFlow
import LenFlow::PathGraph

module LenCfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    // read(fd, &len, sizeof(len)): 第 2 個引數（&len）被寫入攻擊者位元組
    exists(FunctionCall c |
      c.getTarget().getName() = "read" and
      source.asDefiningArgument() = c.getArgument(1)
    )
  }
  predicate isSink(DataFlow::Node sink) {
    // memcpy(dst, src, n): size 引數
    exists(FunctionCall c |
      c.getTarget().getName() = "memcpy" and
      sink.asExpr() = c.getArgument(2)
    )
  }
}

module LenFlow = TaintTracking::Global<LenCfg>;

from LenFlow::PathNode source, LenFlow::PathNode sink
where LenFlow::flowPath(source, sink)
select sink.getNode(), source, sink,
  "attacker-controlled length from $@ reaches memcpy size without bound check",
  source.getNode(), "read()"
```

注意 source 用 **`asDefiningArgument()`**——`read` 透過指標 `&len` 把資料寫回 `len`，這不是普通 expression node，是 `DefinitionByReferenceNode`（Ch 21 講過的坑）。用 `asExpr()` 抓不到，query 會靜默回空。

真跑（`query run`，照貼）：

```
$ codeql query run --database=/home/ypp/audit-lab/vuln-db --additional-packs=. global_taint.ql
...
Result set: nodes
|          n           |     key      |         val          |
+----------------------+--------------+----------------------+
| read output argument | semmle.label | read output argument |
| len                  | semmle.label | len                  |

Result set: edges
|          a           |  b  |    key     |
+----------------------+-----+------------+
| read output argument | len | provenance |

Result set: #select
| col0 |        source        | sink |                                    col3                                    |
+------+----------------------+------+----------------------------------------------------------------------------+
| len  | read output argument | len  | attacker-controlled length from $@ reaches memcpy size without bound check |
```

**命中**。路徑兩個節點：`read output argument`（source）→ `len`（memcpy 的 size 引數）。`query run` 的表格輸出把 PathGraph 拆成 nodes/edges 兩個 result set——這就是 flow path 的原始資料。

要看**渲染好的路徑**（帶行號、SARIF 的 `codeFlows`），改用 `database analyze` 產 SARIF：

```
$ codeql database analyze /home/ypp/audit-lab/vuln-db global_taint.ql \
    --additional-packs=. --format=sarifv2.1.0 --output=out.sarif --rerun
...
results: 1
message: attacker-controlled length from [read()](1) reaches memcpy size without bound check
path steps: [7, 10]
```

SARIF 裡有 `codeFlows` 陣列，路徑步驟落在 **line 7（`read`）→ line 10（`memcpy`）**——這才是你在 GitHub code scanning / VS Code 裡看到的那條可點擊的高亮路徑。**`@kind path-problem` + `PathGraph` import 的價值就在這裡**：沒有它們，SARIF 不會有 `codeFlows`，審計者看不到「怎麼流過來的」。

## 真跑對比：taint flow vs value flow，一個運算決定漏不漏

現在把 source/sink 邏輯不變，只換 `TaintTracking::Global` ↔ `DataFlow::Global`，跑在**跨函式 + 衍生長度**的靶 `vuln2.c`：

```c
static unsigned parse_len(int fd){
    unsigned len;
    read(fd, &len, sizeof(len));
    return len * 2;               // 衍生：value flow 在這裡斷，taint 存活
}
void handle(int fd){
    char buf[64];
    unsigned n = parse_len(fd);   // 跨函式
    if (n > 64) return;           // bound check（barrier 候選）
    char *data = malloc(n);
    read(fd, data, n);
    memcpy(buf, data, n);
    free(data);
}
```

**taint 版**（`module LenFlow = TaintTracking::Global<LenCfg>;`）真跑：

```
Result set: #select
| col0 |        source        | sink |
+------+----------------------+------+
| n    | read output argument | n    |   ← 命中！跨 parse_len、穿過 len*2
```

**value 版**（同一個 config，只把實體化改成 `module LenFlow = DataFlow::Global<LenCfg>;`）真跑：

```
Result set: #select
| col0 | a | b |
+------+---+---+
                    ← 空！什麼都沒抓到
```

**這就是 Ch 21 那條界線的實測**：`return len * 2` 是衍生運算，value flow 認定「`len*2` 不是 `len` 這個值」，於是在函式回傳處斷掉——**漏報**。taint flow 認定「`len*2` 被 `len` 污染」，一路穿過 `parse_len` 的跨函式邊界（IFDS summary edge 幫它跨過去）到 `memcpy`——**命中**。

一個 `* 2` 就是漏報的分水嶺。**審計追污染，永遠用 `TaintTracking::Global`；只有問「同一個值」（如 UAF 指標相等）才用 `DataFlow::Global`**。這是本章第二個必須內化的判斷。

## isBarrier：sanitizer 切斷誤報

`vuln2.c` 有 `if (n > 64) return;`——若 `n` 通過這道 bound check，繼續往下的 `n` 其實已被約束，不該報。我們用 `isBarrier` 把「被拿去跟界限比較過的值」標成屏障：

```ql
import semmle.code.cpp.controlflow.Guards
...
module LenCfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node s) { ... }   // 同上
  predicate isSink(DataFlow::Node s)   { ... }   // 同上
  predicate isBarrier(DataFlow::Node node) {
    // 被拿去做 < 或 == 比較的值，視為已檢查
    exists(GuardCondition g, Expr checked |
      g.comparesLt(checked, _, _, _, _) or g.comparesEq(checked, _, _, _, _)
    | node.asExpr() = checked)
  }
}
```

真跑（barrier 版對 `vuln2.c`）：

```
Result set: #select
| col0 | a | b |
+------+---+---+
                    ← 空：n 被 (n > 64) 比較，barrier 切斷 flow
```

barrier 切掉了這條路徑——因為 `n` 被 `n > 64` 比較（`comparesLt` 命中），標為已檢查，taint 不再往下傳。**對比**：沒 barrier 時（前面 taint 版）命中，有 barrier 時不命中。這就是 sanitizer 建模：把「檢查點」標成 barrier = Ch 4 講的「must-sanitize 切斷 taint 邊」在 QL 的實作。

**警語**：這個 barrier 寫得很粗（任何 `< / ==` 比較都算檢查），真實世界會**過度 sanitize** 造成漏報——`if (n > 64) log(n);` 這種沒真正擋住的比較也會被當屏障。精準的 barrier 要判「比較之後有沒有真的 return/約束」，那是 Ch 23 flow state 的活。這裡先示範 barrier 的機制與「切邊」效果。

## 底層：這就是 IFDS 在跑

對回 Ch 5：global taint 的底層是 **IFDS**——把每個函式的 taint 效果預先算成 **summary edge**（「引數 X 污染 ⇒ 回傳/output Y 污染」），然後在 exploded supergraph 上做可達性。`parse_len` 那個「`len` 污染 ⇒ 回傳污染」就是一條 summary edge，`handle` 呼叫它時直接套用摘要，不重算函式體。這是為什麼 global taint 能規模化（避免對每個 call site 展開整個 callee）。**Ch 23 的 models-as-data 就是手動宣告這種 summary edge**——當函式沒源碼（第三方庫）時，你用 YAML 把摘要寫給 CodeQL。IFDS 的理論、summary edge 的直覺，這裡全用上了。

## 踩雷集錦

**錯誤直覺：「source/sink 定寬一點才不會漏。」**
正確認識：定太寬會**爆炸**。`isSource` 寫成「所有 `read`/`recv`/`getenv`」× `isSink` 寫成「所有 `memcpy`/`strcpy`/`system`」，在大型 codebase 會產生成千上萬條路徑，全是雜訊，你根本 triage 不完，真 bug 淹沒在誤報裡。**先窄後寬**：從一個具體 source × 一個具體 sink 起手，命中後再逐步放寬。精準的 source/sink 是 CodeQL 好用的前提，不是可有可無。

**錯誤直覺：「taint 跟 value 用哪個都行，反正都追到值。」**
正確認識：實測給你看了——`len * 2` 讓 value 版**全空**、taint 版命中。追攻擊者可控值一律 `TaintTracking::Global`。用 `DataFlow::Global` 追污染，會在第一個算術/陣列/字串運算漏報，而且是**靜默**漏報（query 沒錯、就是回空），最難察覺。

**錯誤直覺：「沒設 barrier 沒關係，頂多多幾條誤報。」**
正確認識：對，但那些誤報會淹死你。真實 codebase 到處是「檢查後才用」的合法路徑，不設 barrier 你會對每個「已 sanitize」的值報一次。反過來，barrier **設太粗**（如上面警語）又會漏報。barrier 是精度旋鈕，太鬆誤報、太緊漏報，要跟著具體 sanitizer 語意調。

**錯誤直覺：「`@kind problem` 就能看到路徑。」**
正確認識：`problem` 只給你孤立的 sink 點，**看不到 flow path**。路徑要 `@kind path-problem` + `import MyFlow::PathGraph` + `select` 用 `PathNode` 的五欄格式，三者缺一。少了 `@kind path-problem`，`database analyze` 會直接報 `NO_KIND_SPECIFIED`；少了 PathGraph import，SARIF 沒 `codeFlows`，路徑消失。變體獵殺的價值一半在「路徑」——它告訴你 bug 怎麼觸發，別把它寫丟。

**錯誤直覺：「source 用 `asExpr()` 抓 `read` 的引數就好。」**
正確認識：`read(fd, &len, ...)` 是透過**指標間接寫回**，那個 node 是 `DefinitionByReferenceNode`，要用 **`asDefiningArgument()`**。用 `asExpr()` 抓 `&len` 這個 expression 本身，不是「被寫入的值」，taint 起點錯了，query 靜默回空。這是 C/C++ output argument source 的頭號坑，本章 demo 特意示範正確寫法。

## 進階延伸

- **`flow` vs `flowPath`**：`MyFlow::flow(a, b)` 只回布林可達（快、無路徑），`MyFlow::flowPath(sourcePathNode, sinkPathNode)` 回帶路徑的 `PathNode` 對。要輸出路徑用後者並配 PathGraph；只想數「有幾條 source-sink 對」用前者更省。
- **`isBarrierIn` / `isBarrierOut`**：除了節點級 `isBarrier`，還有「進某 node 之前切」與「出某 node 之後切」的細粒度屏障，處理「這個值進了某函式就安全/出了某函式才危險」的語意。sanitizer 位置刁鑽時用得上。
- **`DataFlow::Global` 也能配 barrier**：value flow 一樣吃 `isBarrier`。UAF 分析（value flow 追指標相等）常配 barrier 排除「重新賦值後」的路徑。taint 不是 barrier 的專利。
- **path explosion 與深度限制**：巨大 codebase 上 global taint 可能路徑爆炸或超時。CodeQL 有 field flow 深度、access path 限制等旋鈕（Ch 23 access path），以及 `--threads`、記憶體設定。跑不動時先收窄 source/sink，再調深度。

## 本章重點整理

- global taint = **跨函式**，用 `module Cfg implements DataFlow::ConfigSig`（`isSource`/`isSink`/`isBarrier`）+ **`TaintTracking::Global<Cfg>`** 實體化。底層是 **IFDS + summary edge**（Ch 5）。
- **`TaintTracking::Global` = taint（含衍生）；`DataFlow::Global` = value（原值）**。實測 `len*2` 讓 value 版全空、taint 版命中——追污染永遠用 taint。
- **path 要三件套**：`@kind path-problem` + `import Flow::PathGraph` + `select` 五欄用 `PathNode`。缺了就沒 `codeFlows`、看不到路徑。
- source/sink **先窄後寬**，太寬爆炸。`isBarrier` 是 sanitizer（切邊），太鬆誤報、太緊漏報。
- C/C++ output argument（`read(fd,&x)`）source 用 **`asDefiningArgument()`**，不是 `asExpr()`。
- **舊 `TaintTracking::Configuration` class** 是 deprecated，能讀別新寫；2.26.2 主線用 **module + ConfigSig**。

## 自我檢核

- 不看範本，寫出 `ConfigSig` module 的三個 predicate 簽名，並說出各對應 taint 四要素的哪個。
- 為什麼 `vuln.c` 的 source 要用 `asDefiningArgument()` 而非 `asExpr()`？用錯會怎樣？
- 把 `TaintTracking::Global` 換成 `DataFlow::Global` 跑 `vuln2.c`，為什麼全空？是哪一行程式讓 value flow 斷掉？
- path-problem query 缺 `import Flow::PathGraph` 會發生什麼？缺 `@kind path-problem` 呢？
- 上面的 barrier 為什麼可能過度 sanitize 造成漏報？舉一個 `< 比較但沒真正擋住` 的例子。
- global taint 底層的 summary edge 是什麼（對回 Ch 5）？這跟下一章 models-as-data 有什麼關係？

## 延伸閱讀

- **CodeQL 官方文件《Analyzing data flow in C and C++》→ "Global data flow" 與 "Using global data flow"**——`ConfigSig` module、`Global<>`、`PathGraph`、`flowPath` 的正式用法與範本。讀完把本章 demo 的每個 predicate 對回文件簽名。前提：本章。
- **CodeQL 官方文件《Creating path queries》**——path-problem 的 metadata、`PathGraph` import、`select` 格式的權威說明。專治「路徑看不到」。讀 "Defining the query predicate" 與 "select clause" 兩節。前提：本章 path 一節。
- **本課 Ch 5 IFDS/IDE**（[連結](./05-ifds-ide.md)）——global taint 的理論根。回頭看 exploded supergraph 與 summary edge，你會懂為什麼 `parse_len` 能被「摘要」跨過去、為什麼 global taint 規模化靠它。前提：無。
- **本課 Ch 14 Semgrep taint mode**（[連結](./14-semgrep-taint-mode.md)）——同一個 taint 框架，換 Semgrep 的 YAML 表達（`pattern-sources`/`pattern-sinks`/`pattern-sanitizers`）。對照 CodeQL 的 `isSource`/`isSink`/`isBarrier`，看兩套工具怎麼表達同一件事、各自的精度取捨。前提：Ch 7。
- **GitHub Security Lab 部落格的 CodeQL 變體分析文章**（多篇 CVE writeup）——看研究者怎麼從一個已知 CVE 抽 source/sink 寫 global taint query 掃出同類 bug。挑一篇 memory-safety 的，對照本章 demo 結構。前提：本章 + Ch 26（CVE→query）。

我們現在能寫跨函式 taint query 了，但兩個限制還在：（1）barrier 只能「切/不切」，無法表達「這個值處於什麼狀態」（如「已開檔尚未檢查」）；（2）第三方庫函式沒源碼，taint 就斷。下一章用 **flow state** 給污染帶狀態、用 **models-as-data（MaD）**YAML 把函式摘要餵給 CodeQL——規模化建模的兩把關鍵鑰匙。

→ [Ch 23 flow state 與 models-as-data](./23-codeql-flow-state-models.md)
