# Ch 25 — Java/JS/Python query

> **目標**：把 taint 從 native 跨到 web 語言。逐類給你注入型漏洞的 query 思路與各語言的**現成 source/sink 建模**：反序列化（Python `pickle` / Java `readObject`）、命令注入、SQL 注入、SSRF、路徑穿越、prototype pollution（JS）。核心觀念是 **`RemoteFlowSource`**——web 語言把「request 進來的資料」統一建模成 source，這是 native（要你手列 `recv`/`read`）跟 web 最大的分野。真跑一個 Flask 小專案：自建 db、寫 taint query 抓命令注入與 pickle，再跑內建 python security suite 對照。對回 [Ch 11 sink 目錄](./11-cross-language-sink-catalog.md)。
>
> **環境**：CodeQL 2.26.2

[Ch 24](./24-codeql-cpp-memory-safety.md) 的四類記憶體安全 bug，source 都要你**手動列舉**（`read`、`recv`、`fread`…）——因為 C 沒有「框架」概念，攻擊者輸入從哪進來全看這個專案怎麼寫。web 語言相反：**框架定義了輸入的入口**（Flask 的 `request.args`、Express 的 `req.query`、Spring 的 `@RequestParam`），CodeQL 的各語言標準庫**已經把這些框架的 request 入口建模成 source 了**。這件事改變了你寫 query 的方式——你幾乎不用自己寫 source，用內建的 `RemoteFlowSource` 就涵蓋一大片框架。

這一章我真跑一個 Flask（Python）小專案，把注入六大類塞進去，示範 command injection 與 pickle 兩條完整 taint query，再跑官方 suite 印證。JS 我給對照的 query 骨架（概念相同，時間所限標「未實測，理論預期」附步驟）。

## 核心分野：`RemoteFlowSource` 是 web taint 的起點

native 你寫：

```ql
predicate isSource(DataFlow::Node n) {
  exists(FunctionCall fc | fc.getTarget().getName() = "read" and n.asDefiningArgument() = fc.getArgument(1))
}
```

web 你寫：

```ql
import semmle.python.dataflow.new.RemoteFlowSources
predicate isSource(DataFlow::Node n) { n instanceof RemoteFlowSource }
```

`RemoteFlowSource` 是各 web 語言標準庫提供的抽象 class（Python、JS、Java、Ruby 都有各自對應），它**內建把所有已知框架的 request 入口都算進去**：Flask 的 `request.args`/`request.form`/`request.get_data()`、Django 的 `request.GET`、Express 的 `req.query`/`req.body`/`req.params`、Spring 的 `@RequestParam`/`@PathVariable`……你用一個 class 就白拿了整個框架生態的 source 建模。這是 web query 遠比 native 好寫的根本原因——**source 這半邊框架幫你建好了，你只需要專注在 sink**。

下面用一個 Flask 專案 `app.py` 逐類走。這個檔我自建、`codeql database create --language=python`（Python 免 build），把六類注入擺一起：

```python
import os, subprocess, sqlite3, pickle, requests
from flask import Flask, request
app = Flask(__name__)

@app.route("/ping")
def ping():
    host = request.args.get("host")       # RemoteFlowSource
    os.system("ping -c1 " + host)          # CWE-78 命令注入
    return "ok"

@app.route("/run")
def run():
    cmd = request.args.get("cmd")
    subprocess.run(cmd, shell=True)        # CWE-78（shell=True）
    return "ok"

@app.route("/safe")
def safe():
    host = request.args.get("host")
    subprocess.run(["ping", "-c1", host], shell=False)  # 安全：arg list、不走 shell
    return "ok"

@app.route("/user")
def user():
    name = request.args.get("name")
    con = sqlite3.connect("x.db")
    con.execute("SELECT * FROM users WHERE name = '" + name + "'")  # CWE-89 SQLi
    return "ok"

@app.route("/load", methods=["POST"])
def load():
    data = request.get_data()
    return str(pickle.loads(data))         # CWE-502 反序列化即 RCE

@app.route("/fetch")
def fetch():
    url = request.args.get("url")
    return requests.get(url).text          # CWE-918 SSRF

@app.route("/read")
def readf():
    fn = request.args.get("file")
    return open("/data/" + fn).read()      # CWE-22 路徑穿越
```

## 類別一：命令注入（CWE-78）—— source 是 request，sink 分走不走 shell

[Ch 11](./11-cross-language-sink-catalog.md) 的核心分界線：**走不走 shell**。`os.system`、`subprocess.run(..., shell=True)`、`os.popen` 走 `/bin/sh -c`，攻擊者用 `;`、`|`、`` ` ``、`$()` 就能注入；給 arg 陣列（`shell=False`）不經 shell，沒有 metacharacter 注入面。所以 sink 建模**不能只看函式名，要看呼叫形態**——這正是 web sink 比 native 微妙的地方。

query：source 用 `RemoteFlowSource`，sink 建模「`os.system` 的參數」與「`subprocess.run` 且 `shell=True` 的第一參數」。這裡用 CodeQL Python 的 **API graph**（`semmle.python.ApiGraphs`）——`API::moduleImport("os").getMember("system").getACall()` 是精準定位「`os.system` 的呼叫」的慣用寫法，比字串比對函式名穩得多（它認得 import alias、`from os import system` 等）：

```ql
/**
 * @name Command injection from request to os.system
 * @kind path-problem
 * @id audit/py-cmdi
 * @problem.severity error
 */
import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.dataflow.new.RemoteFlowSources
import semmle.python.ApiGraphs
import Flow::PathGraph

module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node n) { n instanceof RemoteFlowSource }
  predicate isSink(DataFlow::Node n) {
    // os.system(...) 的參數
    n = API::moduleImport("os").getMember("system").getACall().getArg(0)
    or
    // subprocess.run/call/Popen 且 shell=True 的第一參數
    exists(API::CallNode c |
      c = API::moduleImport("subprocess").getMember(["run", "call", "Popen"]).getACall() and
      c.getKeywordParameter("shell").getAValueReachingSink().asExpr().(ImmutableLiteral).booleanValue() = true and
      n = c.getArg(0))
  }
}
module Flow = TaintTracking::Global<Cfg>;

from Flow::PathNode src, Flow::PathNode sink
where Flow::flowPath(src, sink)
select sink.getNode(), src, sink, "request data flows to shell command (CWE-78)"
```

**真跑**（`codeql query run --database=~/audit-lab/ch25-py-db ./cmdi.ql`）命中：

```
#select
|              col0              |               src                |              sink              |                     col3                     |
+--------------------------------+----------------------------------+--------------------------------+----------------------------------------------+
| ControlFlowNode for BinaryExpr | ControlFlowNode for ImportMember | ControlFlowNode for BinaryExpr | request data flows to shell command (CWE-78) |
| ControlFlowNode for cmd        | ControlFlowNode for ImportMember | ControlFlowNode for cmd        | request data flows to shell command (CWE-78) |
```

兩列命中：`/ping` 的 `os.system("ping -c1 " + host)`（那個 `BinaryExpr` 是字串拼接）與 `/run` 的 `subprocess.run(cmd, shell=True)`。**關鍵：`/safe` 路由沒有命中**——因為它 `shell=False` 且傳 arg 陣列，我的 sink 條件（`shell=True`）把它排除了。這正是「sink 要看呼叫形態不只看函式名」的價值：同一個 `subprocess.run`，`shell=True` 是 sink、`shell=False` + list 不是。

（旁註：path 的 source 端顯示成 `ImportMember` 是 CodeQL Python 內部把 flask `request` 這個 module member 當 taint 起點的呈現方式，flow 本身正確——它從 `request.args.get(...)` 一路追到 sink。）

## 類別二：反序列化 — 整個呼叫即 sink（CWE-502）

[Ch 11](./11-cross-language-sink-catalog.md) 講過反序列化 sink 的特性：**沒有「危險參數」，整個呼叫就是危險的**，只要輸入不可信。`pickle.loads(untrusted)` 一命中就是「untrusted 資料進了會觸發任意程式碼執行的反序列化」——pickle 的語意是「反序列化即可 RCE」，不需要 gadget 也不需要特定參數，這是它比 Java `readObject`（要 gadget chain）更直接致命的地方。

query 幾乎跟命令注入同構，只是 sink 換成 `pickle.loads`/`pickle.load`：

```ql
/**
 * @name Unsafe deserialization via pickle.loads
 * @kind path-problem
 * @id audit/py-pickle
 * @problem.severity error
 */
import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.dataflow.new.TaintTracking
import semmle.python.dataflow.new.RemoteFlowSources
import semmle.python.ApiGraphs
import Flow::PathGraph

module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node n) { n instanceof RemoteFlowSource }
  predicate isSink(DataFlow::Node n) {
    n = API::moduleImport("pickle").getMember(["loads", "load"]).getACall().getArg(0)
  }
}
module Flow = TaintTracking::Global<Cfg>;
from Flow::PathNode src, Flow::PathNode sink
where Flow::flowPath(src, sink)
select sink.getNode(), src, sink, "untrusted data to pickle.loads (CWE-502, RCE)"
```

**真跑**命中：

```
#select
|           col0           |               src                |           col3            |
+--------------------------+----------------------------------+---------------------------+
| ControlFlowNode for data | ControlFlowNode for ImportMember | ... pickle.loads (RCE)    |
```

命中 `/load` 的 `pickle.loads(data)`，`data` 來自 `request.get_data()`。這條 query 的重心全在 **source 判定（資料真的不可信嗎）**——sink 判定極簡（就是那個呼叫），因為反序列化沒有「安全的參數形態」。Java 的對應 sink 是 `ObjectInputStream.readObject()`，query 結構一樣：source = `RemoteFlowSource`、sink = 那個 `readObject` 呼叫，差別只在 Java 的實際 exploit 需要 classpath 上有可用 gadget（審計時仍當命中處理，因為「untrusted 進 readObject」本身就是漏洞）。

## 類別三～五：SQLi / SSRF / 路徑穿越 —— 同一個模板換 sink

上面兩條你已經看出模式：**web taint query = `RemoteFlowSource` 當 source + 一個語言/框架特定 sink**。剩下三類全是換 sink：

| 類別 | sink（Python API graph 大致長相） | 建模重點 |
|---|---|---|
| SQLi（CWE-89） | `sqlite3`/`cursor` 的 `execute(...)` 第一參數，且該字串是拼接出來的 | 重點是 **query 字串怎麼組**——`execute(f"...{name}")` 即使有第二參數也注入；參數化（`execute(sql, params)`）才安全 |
| SSRF（CWE-918） | `requests.get`/`urllib.request.urlopen`/`http.client` 的 URL 參數 | sink 是 URL 參數；有效 sanitizer 難建模（要解析後驗 IP 白名單），多半只當 candidate |
| 路徑穿越（CWE-22） | `open`/`os.path.join` 的路徑參數 | `os.path.join(base, user)` **不是** sanitizer（[Ch 11](./11-cross-language-sink-catalog.md)）；有效 sanitizer 是 `realpath` 後驗前綴 |

這三類我不再逐條貼手寫 query（結構同上，換 sink 的 API graph 定位而已），而是直接**用官方 suite 一次跑出來驗證**——這也順帶示範實務上你不必每類都手寫。

## 跑內建 suite：一次涵蓋六類

```bash
codeql database analyze ~/audit-lab/ch25-py-db \
  python-security-and-quality.qls \
  --format=sarif-latest --output=/tmp/ch25.sarif --rerun
```

**真跑 SARIF 摘要**：

```
total: 9
  2 py/reflective-xss
  2 py/command-line-injection
  1 py/sql-injection
  1 py/full-ssrf
  1 py/path-injection
  1 py/unsafe-deserialization
  1 py/file-not-closed
```

官方 suite 對 `app.py` 獨立命中了**我埋的每一類**：`command-line-injection`（x2，對應 `/ping` 與 `/run`）、`sql-injection`（`/user`）、`full-ssrf`（`/fetch`）、`path-injection`（`/read`）、`unsafe-deserialization`（`/load`）。這就是印證——我手寫的 cmdi/pickle query 抓到的，官方版也各自獨立抓到；而 SQLi/SSRF/path 三類我沒手寫，官方 suite 直接補齊。

**兩個實務判斷**（跟 [Ch 24](./24-codeql-cpp-memory-safety.md) 同一句話但值得重申）：web 語言的官方 suite 對主流框架涵蓋極好，**先跑它打底**；你手寫 query 的價值在**官方沒建模的框架 / 自訂 wrapper**（公司內部的 `@internal_api`、自製 ORM 的 `.raw_query()`）——那些要你用 models-as-data（[Ch 23](./23-codeql-flow-state-models.md)）補建模。

## JS 對照：prototype pollution 與注入（未實測，理論預期）

JS 的 query 結構與 Python 同構，只是 source/sink class 換成 JS 標準庫的。我時間所限沒對 JS 專案真跑，以下標**「未實測，理論預期」**並附你自己驗的步驟。

**JS 的 `RemoteFlowSource`** 涵蓋 Express 的 `req.query`/`req.body`/`req.params`、`process.argv` 等。命令注入 sink 是 `child_process.exec`/`execSync`（走 shell，危險），對照的安全 API 是 `execFile`/`spawn` 給 arg 陣列。骨架：

```ql
// JS 命令注入（骨架，未實測）
import javascript
import semmle.javascript.dataflow.TaintTracking
import DataFlow::PathGraph

class Cfg extends TaintTracking::Configuration {
  Cfg() { this = "JsCmdi" }
  override predicate isSource(DataFlow::Node n) { n instanceof RemoteFlowSource }
  override predicate isSink(DataFlow::Node n) {
    exists(DataFlow::CallNode c |
      c = DataFlow::moduleMember("child_process", "exec").getACall() and
      n = c.getArgument(0))
  }
}
```

**prototype pollution（CWE-1321，JS 專屬）** 是 native 完全沒有的類別：source 是 request，sink 是「用攻擊者控制的 key 遞迴賦值」（config merge、`obj[key1][key2]=val`）。CodeQL JS 標準庫有專門的 `js/prototype-polluting-assignment` query 與對應的 dataflow 建模。這也是為什麼 [Ch 11](./11-cross-language-sink-catalog.md) 把它單列——它的 sink 語意（污染 `__proto__` 影響所有物件）是 JS 動態物件模型獨有的。

**要把上面變成「真做過」**，你需要：`npm init` 一個 Express 小專案放上述 `child_process.exec(req.query.cmd)` 與一個 lodash `merge(req.body)` 型 prototype pollution，`codeql database create --language=javascript --source-root=.`（JS 也免 build），跑 `javascript-security-and-quality.qls`。API graph 的 JS 版寫法（`DataFlow::moduleMember`）與 Python 的 `API::moduleImport` 不同名但概念相同——**以你 bundle 的 JS 標準庫為準**確認實際 class。

## web vs native 的 taint 三個關鍵差異

把兩章接起來，整理成三條可遷移的判斷：

1. **source 的來源不同**：native source 是 syscall / libc（`recv`/`read`/`fread`/argv），要你手列；web source 是**框架的 request 入口**，用 `RemoteFlowSource` 一把抓。這是 web query 好寫的根本。

2. **sink 常「整個呼叫即危險」而非「某個參數被污染」**：native 記憶體 sink 看的是「某個參數（長度/index）被污染」；web 的反序列化、SSTI、XXE 這些常是「輸入不可信 + 呼叫了這個危險函式」整個就是 sink，判定重心移到 source 可信度與**呼叫形態**（`shell=True` 與否、`execute` 的字串怎麼組）。

3. **framework 隱性建模的雙面刃**：框架自動建模 source 幫了你，但也讓 source **看不見**——你在 code 裡看不到 `read`，輸入是框架注入進 handler 的參數的。好處是 `RemoteFlowSource` 幫你認得；壞處是**自訂框架 / 自製 wrapper 的隱性 source 官方認不得**，要你補建模（見踩雷）。

## 踩雷集錦

**錯誤直覺：「native 的記憶體安全 query 思路直接搬到 web 就行。」**
正確認識：source 的建模方式根本不同。native 你手列 `read`/`recv`；web 你**不該**手列 `request.args`——那會漏掉框架的其他入口（`form`、`cookies`、`headers`、`json`）且綁死單一框架。用 `RemoteFlowSource` 讓標準庫替你涵蓋整個框架生態。把 native 的「手列 source」習慣搬到 web，你的 query 會漏掉一大半入口。反過來，web 的「整個呼叫即 sink」思路搬到 native 記憶體 bug 也錯——native sink 幾乎都要看「哪個參數被污染」。

**錯誤直覺：「framework / ORM 包過了就安全，不用建模它的 sink。」**
正確認識：ORM 底層還是拼 SQL，`.raw()`/`.extra()`/字串條件拼接照樣注入；logging 框架後端會解析就是 sink（Log4Shell）；模板引擎的 `render_string` 是 SSTI sink。**框架把 sink 包裝起來不代表消除了它**。而且你司內部的自製 wrapper（`db_query()`、`internal_fetch()`）官方標準庫**完全不認得**——這是 web query 最大的隱性漏報來源。要用 models-as-data（[Ch 23](./23-codeql-flow-state-models.md)）把這些專案特有的間接 source/sink 補建模，query 才在真 codebase 上有用。

**錯誤直覺：「動態語言的 taint 跟 C 一樣可靠，追不到就是沒有 flow。」**
正確認識：Python/JS 的**動態特性造成 alias 盲區**——`getattr(obj, name)()` 動態呼叫、`**kwargs` 展開、`dict` 動態 key、monkey-patching，這些讓靜態 taint 追蹤有本質性的斷點。CodeQL 對常見框架 pattern 建模得不錯，但一旦 code 用了高度動態的間接呼叫，flow 可能斷在那裡而**你看不到 warning**。動態語言的「query 0 命中」比 C 更不能當成「沒有漏洞」——它可能只是 taint 追蹤被動態性擋住了。這類要靠人工讀碼 + 動態驗證補。

**錯誤直覺：「pickle.loads 命中要看它後面怎麼用那個物件，才判斷危不危險。」**
正確認識：**pickle 的語意是「反序列化即執行」**——`pickle.loads(untrusted)` 一旦輸入不可信，反序列化過程本身（透過 `__reduce__`）就能執行任意程式碼，跟你後面怎麼用那個物件無關。所以這類 sink 的判定重心 100% 在 source（資料真的不可信嗎），命中即漏洞，不需要「看後續用途」。這跟 SQLi（要看 query 字串怎麼組）、SSRF（要看有沒有 host 白名單）不同——反序列化沒有「安全的用法」，只有「別把 untrusted 餵進去」。同理 `yaml.load`（非 `safe_load`）、Java `readObject`。

**錯誤直覺：「`subprocess.run` 就是命令注入 sink，看到就標。」**
正確認識：`subprocess.run` 危不危險**完全取決於 `shell` 參數與第一參數形態**。`run(cmd, shell=True)` 走 shell 危險；`run(["ping", host], shell=False)`（預設）給 arg 陣列不走 shell，`host` 就算含 `;` 也只是被當成一個字面 argument 傳給 `ping`，沒有 shell 注入面。上面真跑就證明了：我的 sink 條件加了 `shell=True`，`/safe` 那條 `shell=False` + list 的呼叫**沒被命中**。光看函式名 `subprocess.run` 標命中，會把大量安全的 arg-list 呼叫誤報。（但注意 arg-list 仍可能有 **argument injection**——污染值變成危險 flag，見 [Ch 11 進階延伸](./11-cross-language-sink-catalog.md)。）

## 進階延伸

- **models-as-data 補框架 / wrapper 的 source-sink**：本章所有 query 依賴 `RemoteFlowSource` 與官方 sink 建模，但真專案有大量官方不認得的自訂 wrapper。[Ch 23](./23-codeql-flow-state-models.md) 的 models-as-data 讓你用 YAML 宣告式地把「我司的 `internal_request()` 是 SSRF sink」「`get_user_input()` 是 source」加進去，不用改 query 本體。這是把 web query 從「跑官方 suite」升級到「涵蓋這個專案的真實攻擊面」的關鍵。
- **second-order / stored injection**：本章 source 都是即時 request。但「之前存進 DB 的污染資料被讀回來用」（stored XSS、stored SQLi）跨了持久化邊界，taint 工具**預設不把 DB 回讀當 source**。要手動把「DB query 的回傳」建模成 source，才追得到 second-order 注入。這是 web 審計常被漏掉的一整類。
- **sanitizer 的正確建模決定誤報率**：SSRF 的「檢查 URL 開頭是 http」是無效 sanitizer（可被 redirect / DNS rebinding 繞過），路徑穿越的 `os.path.join` 不是 sanitizer——把**無效** sanitizer 誤建模成 barrier 會漏報真漏洞。花時間搞懂每類的「有效 sanitizer 長怎樣」（[Ch 11](./11-cross-language-sink-catalog.md) 每類都給了），才不會把繞得過的檢查當成安全。
- **跨語言的同一漏洞形狀**：同一個 SSRF / 命令注入邏輯，在 Python/JS/Java/Go 的 CodeQL 建模概念一致（`RemoteFlowSource` + 語言特定 sink），只有 class 名與 API 不同。掌握這個「同構」後，你換語言寫 query 的成本很低——這正是 [Ch 11 sink 目錄](./11-cross-language-sink-catalog.md)按「類別 × 語言」組織的用意。

## 本章重點整理

- web taint query 的核心分野：**source 用 `RemoteFlowSource`**（標準庫已把框架的 request 入口建模好），你只需專注 sink——這是 web 比 native（要手列 source）好寫的根本。
- 命令注入 sink 要看**呼叫形態**不只函式名：`os.system`/`subprocess.run(shell=True)` 是 sink，`shell=False` + arg 陣列不是。真跑證明 `/safe` 未命中。
- 反序列化（`pickle.loads`/`readObject`）**整個呼叫即 sink**，判定重心 100% 在 source 可信度——pickle 反序列化即 RCE，跟後續用途無關。
- SQLi/SSRF/path 三類是同一模板換 sink；官方 `python-security-and-quality` suite 對 `app.py` 獨立命中全六類（command-line-injection x2 / sql-injection / full-ssrf / path-injection / unsafe-deserialization），印證手寫版且補齊沒手寫的。
- web vs native 三差異：source 來源（框架 vs syscall）、sink 常「整個呼叫即危險」、framework 隱性建模是雙面刃（幫你認 source，但自訂 wrapper 認不得）。
- JS 額外有 prototype pollution（CWE-1321）這個 native 沒有的類別；JS query 結構同構，class 名不同（本章 JS 部分標未實測，附驗證步驟）。

## 自我檢核

- [ ]（主動回憶）不看內文，寫出 web taint query 的通用模板（source 用什麼、sink 怎麼定位）。它跟 [Ch 24](./24-codeql-cpp-memory-safety.md) native query 最大的結構差異是什麼？
- [ ]（理解）為什麼 `/safe` 路由的 `subprocess.run(["ping", host], shell=False)` 沒被命令注入 query 命中？sink 條件裡是哪一句排除了它？
- [ ]（理解）為什麼說反序列化 sink 的判定重心在 source 而非 sink？pickle 的「反序列化即 RCE」跟 SQLi「要看 query 字串怎麼組」在判定邏輯上差在哪？
- [ ]（應用）你司有個自製函式 `safe_shell(cmd)` 其實內部走 `os.system`。本章的命令注入 query 抓得到經由它的注入嗎？為什麼？要怎麼補（連到哪章）？
- [ ]（理解）動態語言的「query 0 命中」為什麼比 C 更不能當成「沒漏洞」？舉一個會讓 Python taint 斷掉的動態寫法。
- [ ]（綜合）挑 SSRF 與路徑穿越各一，說明它們的「無效 sanitizer」長怎樣、有效的該怎麼寫——若把無效 sanitizer 誤建模成 barrier 會怎樣？

## 延伸閱讀

- **CodeQL 標準庫各語言的 `Concepts`/`RemoteFlowSources` 與 `Security/CWE-0xx` query 原始碼**（bundle 內 `qlpacks/codeql/python-queries`、`javascript-queries`，或 `github/codeql` repo）——`py/command-line-injection`、`py/unsafe-deserialization`、`js/prototype-polluting-assignment` 的官方實作與框架建模。用法：看官方把哪些框架入口建成 `RemoteFlowSource`、哪些呼叫建成各類 sink，這是本章表格在 CodeQL 裡的可執行版。前提：本章 + [Ch 22](./22-codeql-global-taint.md)。
- **本課 [Ch 11 跨語言 sink 目錄](./11-cross-language-sink-catalog.md)**——每類注入在各語言的 source/sink/sanitizer 長相與 CWE 對照。用法：寫某語言某類 query 前先翻它，確認 sink 有哪些別名、有效 sanitizer 長怎樣（決定 barrier 怎麼建）。前提：無。是本章的參考手冊母章。
- **OWASP Cheat Sheet Series（Deserialization、Command Injection、SSRF Prevention、SQL Injection Prevention）**——每類漏洞的權威防法指引。用法：查「這類漏洞的正確 sanitizer 長怎樣」，反推「無效 sanitizer」怎麼認，避免把繞得過的檢查誤建模成 barrier。前提：本章。
- **CodeQL models-as-data 文件（各語言 `.model.yml` / extensible predicates）**——把框架未涵蓋的自訂 wrapper 建模成 source/sink 的官方機制。用法：真專案跑 query 前，先用它把公司內部的 request wrapper、自製 ORM 補進 `RemoteFlowSource`/sink，否則官方 suite 會大量漏報。前提：[Ch 23](./23-codeql-flow-state-models.md)。

web 語言的注入六大類你都能建模，也知道跟 native 的三個關鍵差異了。但到目前為止，我們寫 query 都是「先知道要抓哪類 bug」。下一章是全課方法論的核心動作——**從一個真 CVE 的 patch 出發**，讀 fix 抽 root cause、寫成 query、掃原專案找同型變體。這是把「會寫 query」變成「會獵變體」的那一步。

→ [Ch 26 從 CVE 到 query](./26-codeql-cve-to-query.md)
