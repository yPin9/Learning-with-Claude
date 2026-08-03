# Ch 23 — flow state 與 models-as-data

> **目標**：把 CodeQL taint 建模推到進階。三件事：（1）**flow state（FlowState）**——讓污染帶狀態，區分同一 node 的不同污染語意（如「已開檔尚未檢查」vs「已檢查」），用 `StateConfigSig` + `GlobalWithState`；（2）**`additionalTaintStep`**——自訂傳播邊，把 CodeQL 看不穿的函式（無源碼的第三方庫）接起來；（3）**models-as-data（MaD）**——用 YAML data extension 宣告 source/sink/summary model，不寫一行 QL 就擴充函式庫模型，這是大型 framework 幾千個 sink 規模化建模的關鍵。全部對回 Ch 5 的 summary edge。
> **環境**：CodeQL 2.26.2，靶 `~/audit-lab/vuln.c`、`vuln2.c`（bound check），及 `vuln3.c`（呼叫無源碼的 `extern unsigned transform(unsigned)`），對應 db `vuln-db` / `vuln2-db` / `vuln3-db`。

Ch 22 的 taint config 有兩個天花板。第一：barrier 只能「切」或「不切」，無法表達「這個污染值**現在處於什麼狀態**」——例如「檔案已開但路徑未檢查」與「路徑已檢查」是同一個值的兩種語意，barrier 表達不了。第二：碰到沒源碼的庫函式（`transform`、OpenSSL、framework API），taint 到函式邊界就斷，你不可能為每個庫函式手寫一條 `additionalTaintStep`。這章給兩把鑰匙解這兩個天花板。

## flow state：給污染帶狀態

**flow state（FlowState）**是掛在 taint 上的一個標籤，讓「同一個值」在 flow 的不同階段帶不同狀態。config 從 `ConfigSig` 升級成 **`StateConfigSig`**，source/sink 都可以要求特定 state，`isAdditionalFlowStep` 可以在傳播時**轉換 state**：

```ql
module Cfg implements DataFlow::StateConfigSig {
  class FlowState = string;                              // 狀態型別，常用 string enum

  predicate isSource(DataFlow::Node s, FlowState state) { ... }   // source 帶初始 state
  predicate isSink(DataFlow::Node s, FlowState state)   { ... }   // sink 要求特定 state
  predicate isAdditionalFlowStep(                                 // 傳播 + 轉狀態
    DataFlow::Node n1, FlowState s1, DataFlow::Node n2, FlowState s2) { ... }
}
module Flow = TaintTracking::GlobalWithState<Cfg>;      // 注意：WithState
```

典型用途是**狀態機**：source 產生 state A，某個操作把 state A → state B，sink 只在 state 是 A（或 B）時才算命中。經典例子：

- **格式字串**：`state = "tainted"`，碰到 sanitize 轉 `"safe"`，sink（`printf`）只報 `"tainted"`。
- **開檔未檢查**：`open()` 產生 `"opened-unchecked"`，`access()`/檢查後轉 `"checked"`，危險操作只在 `"opened-unchecked"` 報 TOCTOU。
- **加密未認證**：`"encrypted-unauthenticated"` → 驗 MAC → `"authenticated"`。

### 真跑：unchecked length 的 flow state

我們用 flow state 表達「未經 bound check 的長度」。source（`read` 寫回）產生 state `"unchecked"`，sink（`memcpy` size）只在 `"unchecked"` 時命中；一旦值被拿去跟界限比較，轉成 `"checked"`：

```ql
/**
 * @name Flow-state demo: unchecked length reaches memcpy
 * @kind path-problem
 * @id audit/fstate
 * @problem.severity error
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import semmle.code.cpp.dataflow.new.DataFlow
import semmle.code.cpp.controlflow.Guards
import F::PathGraph

module Cfg implements DataFlow::StateConfigSig {
  class FlowState = string;

  predicate isSource(DataFlow::Node s, FlowState state) {
    state = "unchecked" and
    exists(FunctionCall c | c.getTarget().getName()="read" and s.asDefiningArgument()=c.getArgument(1))
  }
  predicate isSink(DataFlow::Node s, FlowState state) {
    state = "unchecked" and
    exists(FunctionCall c | c.getTarget().getName()="memcpy" and s.asExpr()=c.getArgument(2))
  }
  // 轉態：被拿去做 < 比較的值，state 從 unchecked → checked
  predicate isAdditionalFlowStep(
    DataFlow::Node n1, FlowState s1, DataFlow::Node n2, FlowState s2) {
    s1 = "unchecked" and s2 = "checked" and
    exists(GuardCondition g, Expr e | g.comparesLt(e, _, _, _, _) | n1.asExpr()=e and n2.asExpr()=e)
  }
}
module F = TaintTracking::GlobalWithState<Cfg>;
from F::PathNode a, F::PathNode b where F::flowPath(a, b)
select b.getNode(), a, b, "unchecked length reaches memcpy"
```

真跑對 `vuln.c`（無 bound check，state 一路 `"unchecked"` 到底）：

```
$ codeql query run --database=/home/ypp/audit-lab/vuln-db --additional-packs=. fstate.ql
Result set: #select
| col0 |          a           |  b  |              col3               |
+------+----------------------+-----+---------------------------------+
| len  | read output argument | len | unchecked length reaches memcpy |
```

命中——`StateConfigSig` + `GlobalWithState` 編譯與求解都正常（2.26.2 確認可用），`"unchecked"` state 從 source 一路帶到 sink。

**誠實的坑（真跑觀察）**：把同一條 query 跑 `vuln2.c`（有 `if (n > 64) return;`），**仍會命中**——因為 `n > 64` 裡被比較的 `n` 是 memcpy 那個 `n` 的**另一個 access**，state 轉換發生在「被比較的那個 access」上，而 memcpy 用的是**別的** access node，沒吃到 `"checked"` 轉換。**這正是 flow state 的精髓與陷阱**：狀態綁在**具體的 dataflow node** 上，不是綁在「變數」上。要正確表達「這個變數整體已檢查」，你得讓轉態覆蓋所有後續 access，或改用 barrier-guard（`isBarrier` 配 guard 判斷「在被 guard 支配的區域」）。這說明 flow state **不是萬用的**——狀態語意對不對，取決於你把轉態掛在哪個 node 上。上一章 barrier 版之所以能正確擋住，就是因為它切的是「被比較的值」本身，語意剛好對得上。

flow state 適合「同一路徑上狀態單向遷移」的場景（tainted→sanitized、opened→checked）；不適合硬拿來當「整個變數是否已檢查」的旗標——那是 barrier + guard dominance 的活。**用對場景**是這裡的判斷。

## additionalTaintStep：接上 CodeQL 看不穿的函式

`isAdditionalFlowStep`（在 `ConfigSig` 是四參數的 state 版，在無 state 時是兩參數 `isAdditionalFlowStep(n1, n2)`）讓你手動加一條傳播邊。最常見的用途：**函式沒源碼**。靶 `vuln3.c`：

```c
#include "ext.h"                       // 宣告 unsigned transform(unsigned x); 無定義
void handle(int fd){
    char buf[64];
    unsigned len;
    read(fd, &len, sizeof(len));       // source
    unsigned n = transform(len);       // 第三方函式，body 不在 db
    char *data = malloc(n);
    read(fd, data, n);
    memcpy(buf, data, n);              // sink
    free(data);
}
```

`transform` 只有宣告、沒有定義（模擬第三方庫）。CodeQL 看不到它的 body，**不知道 `transform` 會把引數污染傳到回傳值**，taint 在此斷掉。先驗證這個漏報：

**base（不加 step）** 真跑：

```
Result set: #select
| col0 | a | b | col3 |
+------+---+---+------+
                            ← 空！taint 卡在 transform，漏報
```

現在用 `isAdditionalFlowStep` 手動宣告 `transform` 的摘要「Argument[0] → ReturnValue」：

```ql
module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node s){
    exists(FunctionCall c| c.getTarget().getName()="read" and s.asDefiningArgument()=c.getArgument(1)
      and c.getArgument(1).(AddressOfExpr).getOperand().(VariableAccess).getTarget().getName()="len") }
  predicate isSink(DataFlow::Node s){
    exists(FunctionCall c| c.getTarget().getName()="memcpy" and s.asExpr()=c.getArgument(2)) }
  // 摘要：transform(x) 的 arg0 污染流到回傳值
  predicate isAdditionalFlowStep(DataFlow::Node n1, DataFlow::Node n2){
    exists(FunctionCall c | c.getTarget().getName()="transform" |
      n1.asExpr() = c.getArgument(0) and n2.asExpr() = c)
  }
}
module F = TaintTracking::Global<Cfg>;
```

**fixed（加 step）** 真跑：

```
Result set: #select
| col0 |          a           | b |          col3           |
+------+----------------------+---+-------------------------+
| n    | read output argument | n | via transform (modeled) |
                            ← 命中！step 把 transform 接起來
```

一條 `isAdditionalFlowStep` 就補回了漏報。`n1.asExpr() = c.getArgument(0)`（引數）→ `n2.asExpr() = c`（call 本身 = 回傳值）——這就是你手寫的一條 **summary edge**，正是 Ch 5 IFDS 的函式摘要。**這條 step 就是 Ch 22 說的「summary edge 手動版」**：CodeQL 對有源碼的函式自動算摘要，沒源碼的你手動宣告。

## models-as-data（MaD）：用 YAML 取代 QL 建模

`isAdditionalFlowStep` 一條一條寫，一個大型 framework 幾千個函式你寫不完，而且改一次要重編 query。**models-as-data（MaD）**把「函式摘要」從 QL 抽成 **data extension（資料擴充，YAML）**——宣告式、不重編、可維護。CodeQL 官方的庫模型（thousands of sinks/summaries for stdlib、框架）幾乎全是 MaD YAML。

同一個 `transform` 摘要，改用 MaD 表達。先建一個 **model pack**：

```yaml
# models/qlpack.yml
name: audit/transform-models
version: 0.0.1
library: true
extensionTargets:
  codeql/cpp-all: "*"
dataExtensions:
  - "models/**/*.yml"
```

```yaml
# models/models/transform.model.yml
extensions:
  - addsTo:
      pack: codeql/cpp-all
      extensible: summaryModel
    data:
      - ["", "", false, "transform", "", "", "Argument[0]", "ReturnValue", "taint", "manual"]
```

那一列 tuple 就是摘要，欄位（C/C++ 的 `summaryModel` 格式）大致是：

```
[ namespace, type, subtypes, name, signature, ext, input,        output,        kind,  provenance ]
[ "",        "",   false,    "transform","",   "",  "Argument[0]","ReturnValue", "taint","manual" ]
```

- `name = "transform"`：目標函式。
- `input = "Argument[0]"` → `output = "ReturnValue"`：污染從第 0 引數流到回傳值（access path 語法）。
- `kind = "taint"`：taint 傳播（相對於 `value` 為原值傳遞）。
- `provenance = "manual"`：人工模型（相對於 `ai-generated` 等）。

query 端**不再需要** `isAdditionalFlowStep`——用 Ch 22 那個乾淨的 `ConfigSig`（只有 source/sink），MaD 的 summary 由 library 自動吃進 taint 傳播。跑的時候用 `--model-packs` 掛上模型：

**with MaD** 真跑（`database analyze` + `--model-packs`）：

```
$ codeql database analyze vuln3-db mad.ql \
    --additional-packs=. --model-packs=audit/transform-models --search-path=. \
    --format=csv --output=mad.csv --rerun
$ cat mad.csv
"len to memcpy via MaD summary",,"error","via transform (MaD)","/vuln3.c","12","23","12","23"
                            ← 命中！YAML 摘要生效，路徑落在 vuln3.c:12（memcpy）
```

**without MaD**（同 query，不掛 model pack）真跑：

```
$ codeql database analyze vuln3-db mad.ql --additional-packs=. --format=csv --output=nomad.csv --rerun
$ cat nomad.csv
                            ← 空：沒掛模型，transform 斷流，漏報
```

**一個 YAML tuple = 一條 summary edge，不寫一行 QL**。這就是 MaD 的威力：模型與 query 解耦，庫模型可以獨立維護、獨立分發（CodeQL 的 stdlib/framework 模型就是這樣一包一包更新的），改模型不用碰 query、不用重編。

### MaD 也能宣告 source / sink

除了 `summaryModel`，還有 `sourceModel`、`sinkModel`：

- **`sourceModel`**：宣告某函式的某輸出是 source（如 `getenv` 回傳是 taint source）。
- **`sinkModel`**：宣告某函式的某引數是 sink（如 `system` 的 arg0 是 command-injection sink）。

大型框架的「幾千個 sink」——每個 ORM 的 raw query 入口、每個模板引擎的 render、每個反序列化 API——全部靠 `sinkModel` YAML 條列，而非幾千條 QL。**這是規模化建模的本體**：QL 寫「分析邏輯」（一條 taint config），MaD 寫「事實資料」（哪些函式是 source/sink/summary），兩者分工。

## field flow 與 access path

MaD 的 `Argument[0]` / `ReturnValue` 是 **access path** 語法，能表達比「整個引數」更細的位置：

- `Argument[0]`：第 0 引數。
- `Argument[0].Field[foo]`：第 0 引數的 `foo` 欄位（**field flow**——污染流進/流出結構欄位）。
- `ReturnValue`：回傳值。
- `Argument[*]`：任一引數。

**field flow** 讓 taint 能穿過 struct：`s.data` 被污染，讀 `s.data` 拿到污染。CodeQL 對 field flow 有支援但**有深度限制**（access path 太深會截斷，換取效能與 termination——對回 Ch 4「格無限高會不收斂」）。踩雷：以為 field flow 無限深，結果深層巢狀結構的污染被截斷漏報。

## 踩雷集錦

**錯誤直覺：「該用 flow state 的地方，用 barrier 硬湊也行。」**
正確認識：barrier 只有「切/不切」二元，表達不了「狀態遷移」。「開檔→未檢查→檢查」這種**單向多態**要 flow state；硬用 barrier 你只能切掉一種，區分不了「未檢查」與「已檢查」的兩條路徑。反過來（見下一條），該用 barrier 卻用 flow state 也會出錯——兩者語意不同，別互換。

**錯誤直覺：「flow state 綁在變數上，設一次整個變數都變狀態。」**
正確認識：flow state 綁在**具體的 dataflow node** 上，不是變數。本章真跑實測：`if (n > 64)` 裡轉 `"checked"` 的是「被比較的那個 `n` access」，memcpy 用的是**另一個** `n` access，沒吃到轉換，於是仍命中。要「整個變數已檢查」的語意，用 barrier + guard dominance（判斷「在被 guard 支配的區域」），別硬用 flow state。用錯場景 = 誤報或漏報。

**錯誤直覺：「summary model 隨便寫，反正多一條邊。」**
正確認識：summary model 寫**錯**（input/output 標反、access path 錯、`taint` 寫成 `value`）不是多一條無害邊，而是**整條路徑漏報**——taint 從錯的位置進出，接不上真實 flow，你以為建了模其實斷了。`value` 與 `taint` 尤其致命：庫函式做的是衍生（如解碼）卻標 `value`，一個算術就斷。改完 MaD 一定要跑 with/without 對比驗證它真的生效（本章 demo 就是這樣驗的）。

**錯誤直覺：「MaD 能取代所有 QL，以後不用寫 query 了。」**
正確認識：MaD 只宣告**事實資料**（哪些函式是 source/sink/summary），**分析邏輯**（怎麼把 source/sink 串成一條 taint config、barrier 怎麼判、path 怎麼輸出）還是 QL 的活。複雜條件（「只有當第 2 引數是常數 0 時才是 sink」）YAML 表達不了，得回 QL。MaD 是「規模化條列事實」的工具，不是 QL 的替代品——兩者分工。

**錯誤直覺：「field flow 想追多深就多深。」**
正確認識：access path 有**深度限制**，巢狀太深的結構欄位污染會被截斷（換效能與收斂性，對回 Ch 4 格高度）。深層 struct/物件圖的污染漏報，先懷疑 access path 深度被截。需要時可調 library 的深度旋鈕，但那會變慢——精度與規模的老權衡。

## 進階延伸

- **MaD 的 provenance 與 AI 生成模型**：`provenance` 欄位區分 `manual`（人工）、`generated`（工具抽取）等。CodeQL 有從程式碼**自動抽 MaD 模型**的機制（model editor、自動 candidate 生成），大型 codebase 建模的加速器。人工審過的標 `manual`，AI/工具產的另標，混用時可過濾。
- **`neutralModel`**：明確宣告「某函式**沒有** flow」（不是 source/sink/summary），抑制其他啟發式誤加的邊。負向模型，收窄誤報用。
- **多狀態 flow state**：`FlowState` 不必是 string，可自訂 class（如 `newtype`）帶結構化狀態。複雜狀態機（多個正交狀態維度）用 class 比 string enum 清楚。
- **model pack 的分發**：MaD model pack 可用 `codeql pack publish` 發到 registry，團隊共用一套庫模型。「幾千個 sink」的模型當作獨立 artifact 維護、版本化、CI 更新——這是企業級 CodeQL 建模的實務形態。

## 本章重點整理

- **flow state（`StateConfigSig` + `GlobalWithState`）**讓污染帶狀態，表達「單向狀態遷移」（tainted→safe、opened→checked）。狀態綁在 **node** 上，不是變數——本章實測 `vuln2` 仍命中就是這個坑。
- **`isAdditionalFlowStep`** 手動加傳播邊，接上 CodeQL 看不穿的函式（無源碼的第三方庫）。實測：opaque `transform` base 漏報、加 step 後命中。這是**手寫的 summary edge**（Ch 5）。
- **models-as-data（MaD）**用 YAML data extension 宣告 `sourceModel`/`sinkModel`/`summaryModel`，不寫 QL 就擴充庫模型。實測：掛 `--model-packs` 命中、不掛漏報。**一個 tuple = 一條 summary edge**。
- MaD 是**規模化建模**的本體——大型 framework 幾千個 sink 靠 YAML 條列，QL 只寫分析邏輯。**MaD 寫事實、QL 寫邏輯**，不能互相取代。
- **access path**（`Argument[0].Field[foo]`/`ReturnValue`）支援 **field flow**，但有**深度限制**（對回 Ch 4 格高度 / 收斂）。

## 自我檢核

- flow state 與 barrier 各適合什麼場景？「開檔→未檢查→已檢查」為什麼要 flow state 而非 barrier？
- 本章 flow-state query 為什麼在 `vuln2.c`（有 bound check）仍命中？這說明 flow state 綁在什麼上，不是綁在什麼上？
- 不加 `isAdditionalFlowStep` 時 `vuln3.c` 為什麼漏報？加一條 `transform` 的 arg0→return step 為什麼補回來？這條 step 對回 Ch 5 的什麼概念？
- MaD YAML 那列 tuple 的 `input`/`output`/`kind`/`provenance` 各是什麼意思？把 `kind` 從 `taint` 寫成 `value`，對「函式做解碼」的情形會怎樣？
- 為什麼說「MaD 寫事實、QL 寫邏輯」？舉一個 YAML 表達不了、必須回 QL 的 sink 條件。
- field flow 的 access path 深度限制對回 Ch 4 的哪個概念？深層 struct 污染漏報先懷疑什麼？

## 延伸閱讀

- **CodeQL 官方文件《Customizing library models for C and C++》（models-as-data）**——MaD YAML 的欄位定義（`sourceModel`/`sinkModel`/`summaryModel`、access path 語法、provenance）權威來源。讀 summary model 欄位表那節，逐欄對回本章 `transform` 的 tuple。前提：本章。
- **CodeQL 官方文件《Using flow state》/《Data flow analysis》的 FlowState 章節**——`StateConfigSig`、`GlobalWithState`、state 轉換的正式用法與範例（格式字串狀態機是官方範例）。讀完把本章 demo 的坑（node vs 變數）想通。前提：Ch 22。
- **本課 Ch 5 IFDS/IDE**（[連結](./05-ifds-ide.md)）——summary edge 的理論根。`additionalTaintStep` 與 MaD 的 `summaryModel` 都是「手動宣告一條 summary edge」，回頭看 IFDS 怎麼自動算摘要、為什麼摘要能讓分析規模化。前提：無。
- **CodeQL 官方 `cpp-all` 的 `.model.yml` 檔案**（library 內建模型）——真實世界的 MaD 長怎樣。用 `codeql resolve library-path` 找到 cpp-all，翻它的 `*.model.yml`，看官方怎麼為 `memcpy`/`strcpy`/`getenv` 建 source/sink/summary。前提：本章。這是抄範本最快的地方。
- **本課 Ch 14 Semgrep taint mode**（[連結](./14-semgrep-taint-mode.md)）——Semgrep 的 `pattern-propagators` 相當於 `additionalTaintStep`/summary model。對照兩套工具怎麼建「函式摘要」，理解建模是所有 taint 工具的共同課題，不是 CodeQL 獨有。前提：Ch 7。

我們把 CodeQL 的 dataflow/taint 建模能力學透了——local、global、flow state、MaD 摘要。接下來要把這套武器對準具體漏洞類別：C/C++ 的記憶體安全（OOB、UAF、double-free、整數溢位餵給 alloc）。下一章用本 Part 的 dataflow 骨架，寫實戰的 C/C++ memory-safety query。

→ [Ch 24 C/C++ 記憶體安全 query](./24-codeql-cpp-memory-safety.md)
