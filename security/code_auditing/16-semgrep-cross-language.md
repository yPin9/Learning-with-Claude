# Ch 16 — 跨語言 Semgrep

> **目標**：把 Ch 13-15 學到的 pattern／taint 思路，套到 C、Java、JS/Node、Python 四種語言上。你會親手為 Java（`Runtime.exec`）、Node（`child_process`）、Python（`os.system`／`pickle`）各寫一條 taint 規則、自建靶檔真跑，看它抓到有漏洞版、放過修好版。重點不是「多學幾種語言的語法」，而是搞懂**同一套 source/sink/sanitizer 骨架跨語言時哪裡通、哪裡斷**——為什麼一條規則不可能跨語言通用、framework 怎麼把 sink 藏起來、動態語言為什麼讓 taint 更容易斷。
>
> **環境**：WSL Ubuntu、Semgrep 1.172.0。靶檔在 `~/audit-lab/ch16/`（`Cmd.java`、`cmd.js`、`app.py`、`mixed.txt`），規則同目錄。所有輸出照貼真跑結果。source/sink 定義對回 [Ch 11 跨語言 sink 目錄](./11-cross-language-sink-catalog.md)。

前三章你在 C 上把 Semgrep 玩熟了：語法 pattern（Ch 13）、taint mode（Ch 14）、規則工程（Ch 15）。但真實審計很少只有一種語言——一個 web 應用後端 Java、前端 Node、腳本 Python、底層 native C，同一類漏洞（命令注入）在四種語言裡長得完全不同。這章教你把那套骨架**平移**到其他語言，並且誠實面對平移時會斷的地方。

Semgrep 的賣點正是**多語言**：同一個 `mode: taint`、同樣的 `pattern-sources`／`pattern-sinks`／`pattern-sanitizers` 三段結構，跨 30+ 語言通用。**思路通用，pattern 不通用**——這是本章第一句也是最後一句話。

## 核心概念：骨架跨語言通用，pattern 綁死語言

Semgrep 的 taint 規則有兩個層次：

```
┌─────────────────────────────────────────────────────────┐
│ 骨架（跨語言通用）                                        │
│   mode: taint                                            │
│   pattern-sources:   ← 攻擊者輸入從哪進來                 │
│   pattern-sinks:     ← 危險操作在哪                       │
│   pattern-sanitizers:← 什麼切斷污染                       │
├─────────────────────────────────────────────────────────┤
│ pattern 內容（綁死某語言）                                │
│   Java:   Runtime.getRuntime().exec($CMD)               │
│   Node:   exec(...)  (from child_process)               │
│   Python: os.system(...)                                │
│   同一個「命令注入」概念，三種完全不同的 sink 寫法        │
└─────────────────────────────────────────────────────────┘
```

**`languages:` 欄位綁死語言**：規則頂上寫 `languages: [java]`，Semgrep 就只拿 Java parser 去解析 `.java` 檔，其他檔案**直接跳過不掃**（後面會真跑證明）。這代表：你不是「寫一條規則掃所有語言」，而是「每種語言各寫一條規則、共用同一套 source/sink/sanitizer 思路」。

對回 Ch 11 sink 目錄的命令注入表：C 的 `system()`、Java 的 `Runtime.exec(String)`、Node 的 `child_process.exec`、Python 的 `os.system`——同一個 CWE-78，四個完全不同的 sink 函式。這章就是把那張表**變成可執行的 taint 規則**。

## 靶檔一：Java 命令注入（`Runtime.exec`）

`~/audit-lab/ch16/Cmd.java`——一個有漏洞版與安全版並存的靶（審計規則的黃金標準是「抓髒的、放過乾淨的」）：

```java
import java.io.BufferedReader;
import java.io.InputStreamReader;

public class Cmd {
    // 模擬 web handler：host 是攻擊者可控輸入
    String pingVuln(String host) throws Exception {
        String cmd = "ping -c 1 " + host;            // 拼進 shell 字串
        Process p = Runtime.getRuntime().exec(cmd);  // sink: 走 shell 的 exec(String)
        return read(p);
    }

    String pingSafe(String host) throws Exception {
        // 給 argv 陣列，不走 shell（無 metachar 注入面）
        ProcessBuilder pb = new ProcessBuilder("ping", "-c", "1", host);
        Process p = pb.start();
        return read(p);
    }
    // ... read() 略
}
```

分界線跟 Ch 11 說的一樣：`exec(String)` 走 `/bin/sh -c`（攻擊者用 `; rm -rf` 注入）；`ProcessBuilder` 給 argv 陣列不走 shell（無 metacharacter 注入面）。規則要抓前者、放過後者。

規則 `java-cmdi.yml`：

```yaml
rules:
  - id: java-runtime-exec-taint
    languages: [java]
    severity: ERROR
    message: >-
      攻擊者可控字串流入 Runtime.exec(String)（走 shell），可能命令注入 (CWE-78)。
      改用 ProcessBuilder 傳 argv 陣列。
    mode: taint
    pattern-sources:
      - pattern: (String $H)          # 示意：把 String 表達式當 source
    pattern-sinks:
      - pattern: (Runtime $R).exec($CMD)
      - pattern: Runtime.getRuntime().exec($CMD)
    pattern-sanitizers:
      - pattern: $X.matches(...)       # allowlist 正規驗證當 sanitizer
```

`(String $H)` 是 Semgrep 的 **typed metavariable**（帶型別的萬用變數）語法——「型別是 `String` 的表達式」，這裡拿來把字串當 source（真實情境會用 Spring 的 `@RequestParam` 之類當精準 source，這裡簡化）。真跑：

```
$ cd ~/audit-lab/ch16 && semgrep --config java-cmdi.yml Cmd.java

   ❯❯❱ java-runtime-exec-taint
          攻擊者可控字串流入 Runtime.exec(String)（走 shell），可能命令注入 (CWE-78)。 改用 ProcessBuilder 傳 argv 陣列。
            8┆ Process p = Runtime.getRuntime().exec(cmd);

Ran 1 rule on 1 file: 1 finding.
```

**只報第 8 行（vuln），第 15 行的 `ProcessBuilder`（safe）完全沒被碰**。這就是我們要的：taint 從字串流到 `exec(String)` → 報；`ProcessBuilder` 不是 sink → 不報。

## 靶檔二：Node 命令注入（`child_process.exec`）

`~/audit-lab/ch16/cmd.js`——Express handler，同樣 vuln／safe 並存：

```javascript
const { exec, execFile } = require("child_process");
const app = require("express")();

app.get("/ping-vuln", (req, res) => {
  const host = req.query.host;            // source: 攻擊者可控
  exec("ping -c 1 " + host, (e, out) => { // sink: child_process.exec 走 shell
    res.send(out);
  });
});

app.get("/ping-safe", (req, res) => {
  const host = req.query.host;
  execFile("ping", ["-c", "1", host], (e, out) => { res.send(out); }); // arg 陣列、不走 shell
});
```

Node 的分界：`exec` 走 shell（危險）、`execFile`／`spawn` 給 arg 陣列不走 shell（安全）——差一個字，天壤之別（Ch 11 踩雷第一條）。規則 `js-cmdi.yml`：

```yaml
rules:
  - id: js-child-process-exec-taint
    languages: [javascript]
    severity: ERROR
    message: >-
      req 輸入流入 child_process.exec()（走 shell），命令注入 (CWE-78)。改用 execFile/spawn。
    mode: taint
    pattern-sources:
      - pattern: $REQ.query
      - pattern: $REQ.body
      - pattern: $REQ.params
    pattern-sinks:
      - patterns:
          - pattern: $F(...)
          - pattern-either:
              - pattern: exec(...)
              - pattern: execSync(...)
    pattern-sanitizers:
      - pattern: encodeURIComponent(...)
```

真跑：

```
$ semgrep --config js-cmdi.yml cmd.js

   ❯❯❱ js-child-process-exec-taint
            7┆ exec("ping -c 1 " + host, (e, out) => {
            8┆   res.send(out);
            9┆ });

Ran 1 rule on 1 file: 1 finding.
```

**只報第 7 行的 `exec`（vuln），`execFile`（safe）沒被碰**（第 8-9 行是 Semgrep 把整個含 callback 的呼叫表達式一起顯示，命中的 sink 是第 7 行的 `exec`）。sink 寫成 `exec(...)` 只匹配裸的 `exec` 呼叫，`execFile` 名字不同不匹配——這正是我們要的分界。

## 靶檔三：Python 命令注入 + 反序列化

`~/audit-lab/ch16/app.py`——Flask 應用，塞了四個路由測不同情境：

```python
import os, pickle, subprocess, shlex
from flask import Flask, request
app = Flask(__name__)

@app.route("/ping-vuln")
def ping_vuln():
    host = request.args.get("host")      # source
    os.system("ping -c 1 " + host)       # sink: 走 shell (CWE-78)

@app.route("/ping-safe")
def ping_safe():
    host = request.args.get("host")
    subprocess.run(["ping", "-c", "1", host])   # list 形式、不走 shell → 安全

@app.route("/ping-sanitized")
def ping_sanitized():
    host = request.args.get("host")
    os.system("ping -c 1 " + shlex.quote(host)) # shlex.quote 當 sanitizer → 安全

@app.route("/load-vuln", methods=["POST"])
def load_vuln():
    blob = request.get_data()            # source
    obj = pickle.loads(blob)             # sink: 反序列化 untrusted (CWE-502)
```

四個路由分別考：純 vuln、安全寫法（list）、被 sanitize（`shlex.quote`）、另一類漏洞（pickle）。規則 `py-cmdi.yml` 放兩條規則（命令注入 + pickle）：

```yaml
rules:
  - id: py-os-system-taint
    languages: [python]
    severity: ERROR
    message: "Flask 輸入流入 os.system（走 shell），命令注入 (CWE-78)。"
    mode: taint
    pattern-sources:
      - pattern: request.args.get(...)
      - pattern: request.form.get(...)
      - pattern: request.args[...]
    pattern-sinks:
      - pattern: os.system(...)
      - pattern: os.popen(...)
      - patterns:
          - pattern: subprocess.$M(..., shell=True, ...)
    pattern-sanitizers:
      - pattern: shlex.quote(...)

  - id: py-pickle-loads-taint
    languages: [python]
    severity: ERROR
    message: "untrusted 資料流入 pickle.loads，反序列化 RCE (CWE-502)。"
    mode: taint
    pattern-sources:
      - pattern: request.get_data(...)
      - pattern: request.data
    pattern-sinks:
      - pattern: pickle.loads(...)
      - pattern: pickle.load(...)
```

真跑：

```
$ semgrep --config py-cmdi.yml app.py

   ❯❯❱ py-os-system-taint
           12┆ os.system("ping -c 1 " + host)
   ❯❯❱ py-pickle-loads-taint
           30┆ obj = pickle.loads(blob)

Ran 2 rules on 1 file: 2 findings.
```

看清楚**四個路由只報了兩個**：

- `/ping-vuln`（第 12 行）→ **報**（`os.system` 收到 taint）。
- `/ping-safe`（`subprocess.run([...])` list 形式，非 `shell=True`）→ **不報**（sink 不匹配）。
- `/ping-sanitized`（`shlex.quote`）→ **不報**（sanitizer 切斷 taint）。
- `/load-vuln`（第 30 行）→ **報**（pickle 反序列化）。

`subprocess.run(["ping",...])` 不匹配 `subprocess.$M(..., shell=True, ...)`（沒有 `shell=True`），所以放過；`shlex.quote(host)` 被列為 sanitizer，切斷污染。四個情境全部判對——這就是「抓髒的、放過乾淨的」。

## `generic` mode：語言無關 pattern

前面每條規則都綁 `languages:`。但有一種**語言無關**模式：`languages: [generic]`。它不解析語法樹，把檔案當**純文字**做結構化比對，適合：設定檔（YAML/INI/Dockerfile）、模板、沒有專屬 parser 的 DSL、或跨檔案掃硬編密鑰這種「不需要語意、只需要字面」的任務。

`generic-todo.yml`：

```yaml
rules:
  - id: generic-todo-secret
    languages: [generic]
    severity: WARNING
    message: "疑似硬編密鑰（語言無關 generic 掃描）"
    patterns:
      - pattern-either:
          - pattern: password = "..."
          - pattern: api_key = "..."
```

對一個混語言 `mixed.txt` 真跑：

```
$ semgrep --config generic-todo.yml mixed.txt

            2┆ password = "hunter2"
            4┆ another api_key = "sk-1234"

Ran 1 rule on 1 file: 2 findings.
```

兩行都抓到，不管它在什麼「語言」裡。

**generic mode 的限制很硬**：

- 沒有 AST，就**沒有 taint**——`mode: taint` 不能配 `languages: [generic]`。你只能做語法 pattern，不能追資料流。
- 沒有語意等價——`generic` 雖然容忍空白差異，但分不清 `x.foo()` 和 `foo(x)` 這種語意重排，也不懂變數 scope、type、alias。
- 容易誤報——因為它只看字面結構。

generic 是「native parser 幫不上忙時的降級選項」，不是主力。有專屬語言 parser 就用專屬的（能吃到 taint、type、scope）。

## 對比：同概念、四語言、四種 sink

把上面串起來，一張表看「命令注入」這一個概念在四語言的落差（詳表見 Ch 11）：

| 語言 | source pattern | sink pattern | 安全寫法（不匹配） |
|---|---|---|---|
| C | `read()`/argv | `system(...)` `popen(...)` | `execve` 給 argv 陣列 |
| Java | `String` 參數／`@RequestParam` | `Runtime...exec($CMD)` | `ProcessBuilder(argv...)` |
| Node | `req.query`／`req.body` | `exec(...)` `execSync(...)` | `execFile`／`spawn`（arg 陣列） |
| Python | `request.args.get(...)` | `os.system(...)` `subprocess...(shell=True)` | `subprocess.run([...])` |

**source 差最多**：C 從 syscall 進來、Java 從框架 annotation、Node 從 `req` 物件、Python 從 `request`。**sink 也全不同名**。同一套三段骨架，填四組不同的 pattern。這就是「思路通用、pattern 不通用」的具體長相。

## 踩雷集錦

**錯誤直覺：「寫一條規則就能掃所有語言，反正 Semgrep 是多語言工具。」**
正確認識：一條規則只綁**一種**語言（`languages:` 欄位）。拿 Java 規則掃 JS 檔會發生什麼？真跑給你看：

```
$ semgrep --config java-cmdi.yml cmd.js
  Nothing to scan.
 • Targets scanned: 0
Ran 1 rule on 0 files: 0 findings.
```

**`Targets scanned: 0`**——Semgrep 看規則是 `languages: [java]`，`cmd.js` 是 JS，**直接跳過整個檔案**，連掃都沒掃。所以不是「一條規則跨語言誤報」，而是「一條規則對其他語言檔案完全無效」。多語言的正確做法是**每語言各一條規則放進同一個 rule 檔／同一個 ruleset**，Semgrep 掃描時各語言檔案各自匹配對應規則。

**錯誤直覺：「抓 `exec` 就抓到命令注入了，函式名對就行。」**
正確認識：同名函式跨語言語意天差地別（Ch 11 第一條踩雷）。Node 的 `exec`（走 shell，危險）vs `execFile`（arg 陣列，安全）差一個字；Python `subprocess.run` 危不危險完全看 `shell=True`；Java `Runtime.exec(String)` 危險、`ProcessBuilder(argv)` 安全。你的 sink pattern 必須把**呼叫形態**編進去（`exec(...)` 不含 `execFile`、`subprocess.$M(..., shell=True, ...)` 才匹配），光靠函式名會把安全寫法也一起誤報。

**錯誤直覺：「framework 用了就一定安全／sink 就那幾個內建函式。」**
正確認識：framework 常把 sink**包裝**起來——Spring 的 `@RequestParam` 是 source，但 sink 可能藏在自訂的 `CommandService.run()` 裡；Express 中介層可能把 `req.query` 換個名字傳下去。內建 sink 只是起點，真實專案有大量**自訂 wrapper**（自己包的 `runShell()`、`db.rawQuery()`）。跨語言審計要先掃內建 sink 找到一批，再從命中反推「這專案自己的危險 wrapper 是哪些」，把它們也加進 `pattern-sinks`。這正是 Ch 15 規則工程與 Ch 23（CodeQL models-as-data）的動機。

**錯誤直覺：「Python/JS 這種動態語言，taint 追起來跟 Java 一樣準。」**
正確認識：動態語言讓 taint **更容易斷**。JS 的 `obj[key]`（key 是變數）、`eval`、`Function` 建構、prototype 動態改屬性；Python 的 `getattr`、`**kwargs` 展開、monkey-patching——這些讓 Semgrep 的靜態 taint 追不過去（它不知道 runtime 時 `obj[key]` 到底存取哪個屬性）。表現就是**漏報**：明明有 flow，但中間經過一次動態存取，taint 就斷了。動態語言的規則要更保守（source/sink 都放寬一點抓 candidate），並且心裡清楚「沒命中」不等於「安全」。

**錯誤直覺：「generic mode 是通用萬能的，語言無關最好用。」**
正確認識：generic 沒有 AST，**不能 taint、不懂 scope/type、易誤報**。它只在「沒有專屬 parser 的檔案格式」（自訂 DSL、某些設定檔）或「純字面掃描」（硬編密鑰）時才是好選擇。有原生 parser 的語言用原生的——你要 taint、type、alias 這些語意能力，generic 全都沒有。

## 進階延伸

- **框架感知 source/sink**：真實 Java/Python 審計不會拿「所有 String 參數」當 source，而是精準用框架的入口——Spring 的 `@RequestParam`/`@PathVariable`、Flask 的 `request.*`、Express 的 `req.*`。Semgrep registry 的官方規則就是這樣寫的，去讀 `p/java` `p/python` ruleset 看它們的 `pattern-sources` 怎麼精準綁框架入口。
- **跨檔案／跨語言 flow 的天花板**：Semgrep 的 taint 主要在**單檔案內**（Pro 引擎有跨檔案 interprocedural，社群版有限）。而「Java 後端把污染寫進 DB、Python 批次讀回來用」這種**跨語言、跨進程**的二階 flow，任何單一工具都追不動——這是 Ch 11 講的 second-order injection，得靠人工建模或多工具接力（Ch 35 漏斗）。
- **多語言 monorepo 的規則組織**：一個 repo 混四種語言時，規則檔怎麼組織？用 Semgrep 的 ruleset（一個 YAML 放多條規則、各綁不同 `languages:`），或用 registry 的 `p/default` 一次跑全部。CI 裡怎麼一次掃全語言、怎麼按語言分報告——下一章 CI 整合會碰到。

## 本章重點整理

- Semgrep 的 taint 骨架（`mode: taint` + source/sink/sanitizer 三段）**跨語言通用**，但每段的 **pattern 內容綁死語言**（`languages:` 欄位）。
- 四語言的命令注入實跑：Java `Runtime.exec` / Node `child_process.exec` / Python `os.system` + pickle 反序列化，各寫一條規則、各抓 vuln 放過 safe，全部真跑判對。
- source 跨語言差最多（syscall / 框架 annotation / `req` / `request`），sink 全不同名，安全寫法靠「呼叫形態」區分（`exec` vs `execFile`、`shell=True` 與否、字串 vs argv 陣列）。
- `generic` mode 語言無關但**沒 AST → 不能 taint、易誤報**，只在無專屬 parser 的格式或純字面掃描時用。
- 一條規則綁一種語言，拿去掃別的語言檔案會 `Targets scanned: 0`（直接跳過）——多語言靠「每語言各一條規則」，不是「一條打天下」。

## 自我檢核

- 為什麼說「Semgrep 的 taint 思路跨語言通用、但 pattern 不通用」？`languages:` 欄位在其中扮演什麼角色？
- 不看上文，寫出命令注入在 Java／Node／Python 各自的「危險 sink」與「安全寫法」的分界線，並說你的 sink pattern 要怎麼把兩者區分開。
- 拿 `languages: [java]` 的規則去掃一個 `.js` 檔，會發生什麼？（主動回憶真跑輸出的關鍵數字）為什麼這代表「一條規則不能跨語言」？
- `generic` mode 為什麼不能配 `mode: taint`？它適合掃什麼、不適合掃什麼，各舉一例。
- 為什麼動態語言（JS/Python）的 taint 比 Java 更容易斷？舉兩個會讓 Semgrep taint 斷掉的動態特性，並說這在結果上表現為漏報還是誤報。
- 你的 Python `os.system` 規則掃 Flask app，四個路由只報了兩個。說出每個路由被報／放過的**具體原因**（對到規則的哪一段：source／sink／sanitizer）。

## 延伸閱讀

- **Semgrep 官方 registry（semgrep.dev/r，各語言 `p/java`、`p/javascript`、`p/python` ruleset）**——上千條現成的多語言規則，等於「別人整理好、真實框架感知的 source/sink 目錄」。用法：搜某語言某類漏洞，讀它的 `pattern-sources`/`pattern-sinks`/`pattern-sanitizers` 怎麼精準綁框架入口，跟你這章的簡化版對照。前提：本章 + Ch 14。
- **Semgrep 官方文件 "Generic pattern matching"（semgrep.dev/docs）**——generic mode 的完整能力與限制、`generic_ellipsis_max_span` 等調參。用法：要掃設定檔／DSL 前先讀清楚它能做什麼不能做什麼，避免誤用在該用原生 parser 的場合。前提：本章 generic 段落。
- **Ch 11 跨語言 sink 目錄（./11-cross-language-sink-catalog.md）**——本章每條規則的 sink 從哪來的權威查表。用法：寫新語言／新漏洞類別的規則前，先翻 Ch 11 對應類別，把「這語言這類漏洞的 sink 有哪幾個」抄進 `pattern-sinks`。前提：無，這是本章的參考手冊。
- **OWASP Cheat Sheet：Command Injection / Deserialization Prevention**——每類漏洞的正確防法（哪些寫法算 sanitizer、哪些是假 sanitizer）。用法：反推你的 `pattern-sanitizers` 該列什麼、哪些「看起來安全其實沒切斷」的寫法不能當 sanitizer。前提：本章 + Ch 9。

四語言規則會寫了，但都是在本機手動一條條跑。真實團隊要的是**自動化把關**：每次 commit、每個 PR 自動掃，掃出高風險就擋下。下一章把 Semgrep 從本機推進 CI——`semgrep ci`、baseline、diff-aware、SARIF 輸出、抑制機制，以及「CI 一上來全開規則淹沒團隊」這類真實踩雷。

→ [Ch 17 Semgrep 進 CI](./17-semgrep-ci.md)
