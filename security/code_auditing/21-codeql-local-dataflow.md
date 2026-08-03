# Ch 21 — CodeQL local dataflow

> **目標**：把 Ch 4 學的 dataflow 從紙上數學搬進 CodeQL。這章只講**單一函式內**的 value flow——`DataFlow::Node`、`localFlowStep`、`localFlow` 是什麼、怎麼查、輸出長怎樣。先把 value flow 與 taint flow 的界線劃死（value = 值原封傳遞，taint = 值可能被衍生污染，Ch 22 展開），把 barrier 的概念點到，把「local 不跨函式、dataflow 不自動含 alias」這兩個最常見的誤解釘死。跨函式與污染語意留到 Ch 22-23。
> **環境**：CodeQL 2.26.2（`~/audit-tools/codeql/codeql`），共用靶 `~/audit-lab/vuln.c`，cpp database 於 `~/audit-lab/vuln-db`。

你在 Ch 4 手寫過一個 worklist dataflow 引擎，看清了「taint 只是換了 lattice/transfer 的 dataflow」。CodeQL 把那套引擎工業化了：你不再自己寫 transfer function，而是宣告「哪些 node、哪條 step」，剩下的 fixpoint 求解由 library 代勞。這章教你怎麼跟這套 library 對話——先從最小的 local dataflow 起手。

## DataFlow::Node：dataflow 圖的頂點

CodeQL 的 dataflow 不直接在 AST（`Expr`/`Stmt`）上跑，而是在一張獨立的 **data flow graph（資料流圖）**上跑。這張圖的頂點型別是 `DataFlow::Node`，邊是 flow step。為什麼要另建一層而不直接用 `Expr`？因為 dataflow 要表達的東西比 AST 節點多：函式參數傳入的值、`read(fd, &x)` 這種「透過 output argument 寫回去的值」、SSA 定義……這些不是單純一個 expression 能代表的。

`DataFlow::Node` 底下有數種子類（sub-class），審計最常碰到這幾種：

```
DataFlow::Node
 ├─ ExprNode          ── 一個 Expr 的求值結果（最常見）；node.asExpr() 取回那個 Expr
 ├─ ParameterNode     ── 函式形參流入點；node.asParameter() 取回 Parameter
 ├─ DefinitionByReferenceNode ── output argument，如 read(fd,&x) 寫回 x
 │                        node.asDefiningArgument() 取回那個 &x 引數
 └─ ...（PostUpdateNode、Uninitialized 等，先不管）
```

在 C/C++ 的 new dataflow library 裡，這些型別都來自 `semmle.code.cpp.dataflow.new.DataFlow`。**注意 `.new.`**——CodeQL 有 old（`dataflow.DataFlow`）與 new（`dataflow.new.DataFlow`）兩套 library，2.26.2 主線走 new，本課全程用 new。舊 API 你在老教材會看到，Ch 22 會標清差異。

Node 與 Expr 的橋接靠三個成員 predicate：

- `node.asExpr()` — 若這個 node 對應一個 expression，取回它；否則不成立（fails）。
- `node.asParameter()` — 若是參數 node，取回 `Parameter`。
- `node.asDefiningArgument()` — 若是「透過指標引數寫回」的 node，取回那個引數 expr。

反過來，`DataFlow::exprNode(e)` 把一個 `Expr e` 升成對應的 `ExprNode`。這兩個方向你會一直用。

## localFlowStep 與 localFlow：單步 vs 遞移閉包

CodeQL local dataflow 給你兩層 API：

- **`DataFlow::localFlowStep(Node a, Node b)`**——**一步** value flow：`a` 的值直接流到 `b`（賦值、參數綁定、`(x)` 括號、簡單傳遞等）。這是圖的**邊**。
- **`DataFlow::localFlow(Node a, Node b)`**——`a` 到 `b` 有**任意多步** local value flow，即 `localFlowStep` 的**反身遞移閉包（reflexive transitive closure）**。這是圖上的**可達性**。

對回 Ch 4：`localFlowStep` 就是 transfer function 走一格，`localFlow` 就是 fixpoint 收斂後的「誰能到誰」。你不用自己迭代，`localFlow` 已經是求解完的結果。定義上 `localFlow(a,b)` 等價於 `a = b or localFlowStep+(a,b)`——反身（自己到自己）加正遞移閉包。

**「local」的精確意義：不跨函式呼叫**。`localFlow` 只在同一個函式體內傳播；碰到 `foo(x)` 呼叫，值進了 `foo` 就斷了（除非 `foo` 被 inline，一般不）。跨函式是 global flow 的事，Ch 22 才處理。這是本章第一個要釘死的界線。

## value flow vs taint flow：先劃死界線

這是整個 CodeQL dataflow 最容易搞混、也最致命的一組概念，現在講清楚：

| | value flow | taint flow |
|---|---|---|
| 語意 | 值**原封不動**傳遞 | 值**可能被衍生污染** |
| `y = x` | ✅ `x` flows to `y` | ✅ |
| `y = x + 1` | ❌（值變了，不是同一個值） | ✅（`y` 被 `x` 污染） |
| `y = x[3]` | ❌ | ✅（污染從容器流到元素） |
| `memcpy(y, x, n)` | ❌（`y` 是新 buffer） | ✅（`x` 的污染流到 `y`） |
| library | `DataFlow::localFlow` | `TaintTracking::localTaint` |

**value flow 問「這是不是同一個值」，taint flow 問「這個值有沒有被污染源沾到」**。審計絕大多數時候要的是 taint flow——攻擊者控制的 `len` 經過 `len * 2`、`len + header_size` 還是危險的，value flow 會在第一個算術運算就斷掉，漏報。

那為什麼這章先教 value flow？因為：（1）它是 taint flow 的**基礎**——taint flow = value flow 的所有 step 再加上「污染衍生 step」（算術、陣列存取、字串拼接等）；（2）某些審計問題**就是要** value flow 的精確性，例如「這個 `free` 的指標跟那個 `use` 的指標是不是**同一個**值」（use-after-free 判定要值相等，不是污染沾邊）。搞懂 value flow 是 raw 骨架，Ch 22 的 taint 只是往上加邊。

## 真跑 1：len 從 read 流到 memcpy（local value flow）

靶 `~/audit-lab/vuln.c`：

```c
void handle(int fd) {
    char buf[64];
    int len;
    read(fd, &len, sizeof(len));      // len 被寫入（source）
    char *data = malloc(len);
    read(fd, data, len);
    memcpy(buf, data, len);           // len 當 size（sink）
    free(data);
}
```

`handle` 一個函式全包，正好適合 local flow。我們問：函式內，`len` 的存取能不能 local-flow 到 `memcpy` 的第三個引數（size）？

```ql
/**
 * @name Local flow of len from read to memcpy
 * @kind problem
 * @id audit/local-len
 * @problem.severity warning
 */
import cpp
import semmle.code.cpp.dataflow.new.DataFlow

from DataFlow::Node src, DataFlow::Node snk
where
  src.asExpr().(VariableAccess).getTarget().getName() = "len" and
  DataFlow::localFlow(src, snk) and
  snk.asExpr() = any(FunctionCall c | c.getTarget().getName() = "memcpy").getArgument(2)
select snk, "len flows locally into memcpy size argument, from $@", src, src.toString()
```

真跑（照貼，`query run`）：

```
$ codeql query run --database=/home/ypp/audit-lab/vuln-db --additional-packs=. local_len.ql
Starting evaluation of audit/ch21-23/local_len.ql.
Evaluation completed (5.7s).
| snk |                         col1                         | src | col3 |
+-----+------------------------------------------------------+-----+------+
| len | len flows locally into memcpy size argument, from $@ | len | len  |
| len | len flows locally into memcpy size argument, from $@ | len | len  |
| len | len flows locally into memcpy size argument, from $@ | len | len  |
```

命中三次——`len` 在函式內有多個 `VariableAccess`（`malloc(len)`、`read(...,len)`、`memcpy(...,len)`），每個都能 local-flow 到 memcpy 的 size 引數（因為它們是同一個 SSA 值的不同讀取點）。**這正是 value flow：`len` 一路原封傳到 `memcpy`，沒有算術改動它**。多重命中是常態，之後我們用更精確的 source（`read` 的 output argument）收斂它，Ch 22 會示範。

## 真跑 2：malloc 的回傳值在函式內流到哪

換個 source：`malloc` 的回傳值（`data`），追它 local-flow 到的所有 node。這示範「從一個 call 的結果出發追值」：

```ql
/**
 * @name Local flow from malloc return value
 * @kind problem
 * @id audit/local-malloc
 * @problem.severity warning
 */
import cpp
import semmle.code.cpp.dataflow.new.DataFlow

from DataFlow::Node src, DataFlow::Node snk
where
  src.asExpr() = any(FunctionCall c | c.getTarget().getName() = "malloc") and
  DataFlow::localFlow(src, snk) and
  src != snk
select snk, "malloc result reaches this node (line " + snk.getLocation().getStartLine() + ")"
```

真跑輸出：

```
| call to malloc | malloc result reaches this node (line 8)  |
| data           | malloc result reaches this node (line 9)  |
| data           | malloc result reaches this node (line 9)  |
| data           | malloc result reaches this node (line 10) |
| data           | malloc result reaches this node (line 10) |
| data           | malloc result reaches this node (line 11) |
| data           | malloc result reaches this node (line 11) |
```

`malloc` 的結果（line 8）流到 `data` 的每個使用點：line 9（`read(fd, data, len)`）、line 10（`memcpy(buf, data, len)`）、line 11（`free(data)`）。**這就是 value flow 的典型用途：追一個指標值到它所有被用到的地方**——UAF/double-free 分析的骨架正是「`free` 的那個值，之後有沒有再流到某個 use」。注意 `data` 是同一個指標值原封傳遞，所以 value flow（不是 taint）就抓得到。

## 真跑 3：單步 localFlowStep 看清「一格」

把 `localFlow` 換成 `localFlowStep`，只看**直接相鄰**的邊，感受「一步」的粒度：

```ql
import cpp
import semmle.code.cpp.dataflow.new.DataFlow
from DataFlow::Node a, DataFlow::Node b
where
  DataFlow::localFlowStep(a, b) and
  a.asExpr().(FunctionCall).getTarget().getName() = "malloc"
select a, b, "one step from malloc"
```

這只會命中 `malloc()` → `data`（賦值那一步），不會一路追到 `memcpy`。`localFlowStep` 是邊，`localFlow` 是路徑——想手動控制「只走幾步」時用前者，想問「到得了嗎」用後者。**踩雷**：初學者常誤用 `localFlowStep` 期待它像 `localFlow` 一樣傳遞，結果只拿到一格就斷——記住 step 不是閉包。

## barrier：先點到，Ch 22 展開

`barrier`（屏障）是 dataflow 的第三個要角，對應 Ch 7 的 **sanitizer**：一個被標成 barrier 的 node，會**切斷**經過它的 flow。例如 `if (len < 64)` 檢查後的 `len`，可以標成 barrier，讓污染不再往下傳，避免對「已檢查」的值誤報。

local dataflow 的 `localFlow`/`localFlowStep` 是**沒有 barrier 概念的原始可達性**——它不認識 sanitizer，見值就傳。barrier 是 **global taint tracking 的 config**（`isBarrier`）才有的旋鈕。所以本章只點到：**你在 local flow 看到的「傳到底」是無屏障的下界**，真實查詢要不要屏障，是 Ch 22 配置 flow config 時的事。這裡先建立「barrier = 切邊 = sanitizer 在 QL 的樣子」這個對應。

## 踩雷集錦

**錯誤直覺：「`localFlow` 會自動跨函式，只要值傳到別的函式就跟得上。」**
正確認識：`local` 字面意思就是**不跨函式呼叫**。碰到 `foo(x)`，值進了 `foo` 的形參就斷。跨函式要 `TaintTracking::Global` / `DataFlow::Global`（Ch 22）。把 local flow 當 global 用，會漏掉所有跨函式的真 bug——這是新手第一個坑。

**錯誤直覺：「value flow 跟 taint flow 差不多，反正都是追值。」**
正確認識：差在**衍生**。`y = x + 1`，value flow 說 `x` **不** flow 到 `y`（值變了），taint flow 說 `y` 被 `x` 污染。攻擊者控制的 `len` 過一個 `len * 2` 就讓 value flow 斷掉——用 `localFlow` 追污染會大量漏報。追「同一個值」用 value flow（如 UAF 的指標相等），追「污染沾邊」用 taint（`localTaint` / `TaintTracking`）。用錯 library 是誤報/漏報的常見根因。

**錯誤直覺：「dataflow 會自動處理 alias／指標，`*p` 和 `*q` 指同一塊它會知道。」**
正確認識：dataflow 本身**不含完整 alias analysis**。CodeQL 對指標/欄位有**有限的**建模（field flow、部分 indirection），但不是完整 points-to（Ch 6）。你在 Ch 21 之後常見的漏報，很多是「兩個指標其實 alias 但 CodeQL 沒連起來」。碰到指標間接寫回（如 `read(fd, &x)`）要用 `asDefiningArgument()` 這種專門的 node，不能指望 `asExpr()` 自動涵蓋。alias 的精度上限就是 Ch 6 講的那組取捨，CodeQL 選了偏保守的一側。

**錯誤直覺：「`localFlowStep` 跟 `localFlow` 可以互換。」**
正確認識：`localFlowStep` 是**一步**（圖的邊），`localFlow` 是**任意步**（反身遞移閉包）。想問可達性一律用 `localFlow`；`localFlowStep` 拿來自訂傳播規則或除錯單步時才用。誤用 step 期待閉包行為，會只拿到直接相鄰的一格。

**錯誤直覺：「`node.asExpr()` 對任何 node 都成立。」**
正確認識：`asExpr()` 只對 `ExprNode` 成立。`ParameterNode`、`DefinitionByReferenceNode` 用 `asExpr()` 會 fail（回空）。追 `read(fd, &len)` 寫回的 `len` 要用 `asDefiningArgument()`，追形參要用 `asParameter()`。用錯取值 predicate，query 靜默回空，你會以為「沒 flow」其實是取錯 node——這在 Ch 22 寫 source 時是頭號坑。

## 進階延伸

- **`localTaint` 是 local flow 的 taint 版**：`TaintTracking::localTaint(a, b)`（來自 `dataflow.new.TaintTracking`）= `localFlow` 加上「污染衍生 step」（算術、陣列、字串等）。把真跑 1 的 `DataFlow::localFlow` 換成 `TaintTracking::localTaint`，就會多抓到經過 `len * 2` 之類的路徑。這是 Ch 22 global taint 的 local 前身，先知道它存在。
- **flow 圖 vs SSA**：CodeQL 的 local value flow 底層大量依賴 SSA（Ch 3）——`localFlowStep` 的一大來源就是 SSA use-def 邊。真跑 1 的三重命中，本質是 `len` 的 SSA 定義流到三個 use。理解 SSA 幫你預測 flow 圖長怎樣。
- **`getALocalSource()`**：新 library 常用 `node.getALocalSource()` 反向問「這個 node 的 local 值最初從哪來」，是 `localFlow` 的 backward 便利版。追一個 sink 引數「往回 local 追到哪個 source」時比正向 `localFlow` 好寫。

## 本章重點整理

- CodeQL dataflow 跑在**獨立的 data flow graph** 上，頂點是 `DataFlow::Node`（`ExprNode`/`ParameterNode`/`DefinitionByReferenceNode`…），用 `asExpr()`/`asParameter()`/`asDefiningArgument()` 與 AST 橋接。
- **`localFlowStep` = 一步（邊），`localFlow` = 任意步（反身遞移閉包 = 可達性）**。local = **不跨函式**。
- **value flow（同一個值，`localFlow`）vs taint flow（污染衍生，`localTaint`/`TaintTracking`）**——追污染用 taint，追指標相等（UAF）用 value。用錯 library = 漏報或誤報。
- dataflow **不含完整 alias analysis**；CodeQL 對指標/欄位是有限建模，指標寫回要用 `asDefiningArgument()`。
- barrier = sanitizer 在 QL 的樣子（切邊），但那是 **global config（`isBarrier`）** 的旋鈕，local flow 沒有它。
- 全程用 **new library（`dataflow.new.DataFlow`）**，2.26.2 主線。

## 自我檢核

- 不看表，說出 `y = x` 與 `y = x + 1` 在 value flow 與 taint flow 下各成不成立，以及為什麼追攻擊者控制的長度要用 taint。
- `localFlowStep` 與 `localFlow` 差在哪？如果我只想要「直接賦值那一步」該用哪個？想問「到得了 memcpy 嗎」用哪個？
- 真跑 1 為什麼命中三次？這跟 `len` 的 SSA use-def 有什麼關係？
- 追 `read(fd, &len, ...)` 寫回的 `len` 值，該用 `asExpr()` 還是 `asDefiningArgument()`？用錯會發生什麼？
- 為什麼 `localFlow` 追不到跨函式的 bug？下一章用什麼補？
- 給一段 `p = malloc(); free(p); use(p);`，要判 UAF 你會用 value flow 還是 taint flow？為什麼要「同一個值」而非「污染沾邊」？

## 延伸閱讀

- **CodeQL 官方文件《Analyzing data flow in C and C++》**（codeql.github.com → CodeQL for C/C++ → Analyzing data flow）——本章 API 的權威來源。讀 "Local data flow" 與 "Using local data flow" 兩節，把 `Node`/`localFlow`/`asExpr` 的正式簽名對過一遍。前提：本章。
- **CodeQL 官方文件《About data flow analysis》**——講 CodeQL 為什麼要獨立的 data flow graph、node 種類的設計動機。讀 "Data flow graph" 那節，補齊本章「為什麼不直接用 Expr」的背景。前提：Ch 4。
- **本課 Ch 4 資料流分析**（[連結](./04-dataflow-analysis.md)）——你手寫的 worklist 引擎就是 `localFlow` 底層在做的事。回頭對照 transfer/fixpoint 與 `localFlowStep`/`localFlow` 的對應，兩個視角互補。前提：無。
- **`cpp-all` library 原始碼 `semmle/code/cpp/dataflow/new/DataFlow.qll`**——想知道 `localFlowStep` 到底涵蓋哪些 step（SSA、賦值、括號…），直接讀 library。用 `codeql resolve library-path` 找到路徑後翻 `localFlowStep` 的定義。前提：Ch 19 QL 語言。難度中，但一勞永逸。

我們現在能在**一個函式內**追值了。但 `vuln.c` 的真實危險是「攻擊者控制的 `len` 跨函式（甚至跨檔案）流到 `memcpy`」——local flow 到函式邊界就斷。下一章把 flow 升到 **global、跨函式的 taint tracking**，這是 CodeQL 變體獵殺的核心武器，也是本課技術最硬的一章。

→ [Ch 22 CodeQL global taint tracking](./22-codeql-global-taint.md)
