# 練習 C — Semgrep taint 規則抓 CVE

> **目標**：把 Ch 13-17 全部拼起來。你要針對**一類真實漏洞**（這裡選命令注入 CWE-78，因為它好在本機構造靶、對應大量真實 CVE），寫一組完整的 Semgrep taint 規則——含 source、sink、**sanitizer**——抓到有漏洞版、放過修好版與被 sanitize 版，用 Semgrep 的 `--test` 機制自動驗證「抓對放對」，最後輸出 SARIF（對接 Ch 39）。做完你手上就有一套能直接上 CI（Ch 17）的自製規則，而且是用 Semgrep 官方的測試框架**證明過**它抓髒放乾淨，不是「跑起來看起來對」。
>
> **環境**：WSL Ubuntu、Semgrep 1.172.0（OSS 版）。工作目錄 `~/audit-lab/practice-c/`。參考解答全部真跑貼輸出。

這是 Semgrep 部分（Ch 13-17）的實戰總驗收。前面你分開學了語法 pattern、taint mode、規則工程、跨語言、CI；這個練習要你把它們**一次串起來**產出一個可交付的成品：一組經過測試的 taint 規則。

命令注入是刻意的選擇——它 sink 清楚（`os.system`、`subprocess ... shell=True`）、sanitizer 明確（`shlex.quote`、argv 陣列）、source 好造（Flask `request`），而且對應真實世界一大票 CVE（無數 web 應用把 user 輸入拼進 shell）。你也可以改選 path traversal 或 deserialization（延伸挑戰有提），但先用命令注入把流程走通。

## 任務規格

你要產出**三個檔案**放在 `~/audit-lab/practice-c/`：

1. **靶 code**（`cmdi.py`）：一個含漏洞的 Python 小專案，**同時含**有漏洞寫法與安全寫法。每一處要標 Semgrep 測試註解（`# ruleid: <id>` 標「這行該報」、`# ok: <id>` 標「這行不該報」）。至少要涵蓋這五種情境：
   - 直接 sink（`os.system(拼污染)`）→ 該報。
   - `subprocess ... shell=True`（拼污染）→ 該報。
   - 安全寫法：`subprocess.run([argv 陣列])`（不走 shell）→ 不該報。
   - 被 sanitize：`os.system(... shlex.quote(host))`→ 不該報。
   - 常數（非污染）進 sink → 不該報（避免「看到 `os.system` 就報」的誤報）。
   - （加分）間接流：污染經一次賦值傳遞後才進 sink → 該報。

2. **規則**（`cmdi.yml`）：一條 `mode: taint` 規則，`id` 與靶檔對得上，含 `pattern-sources` / `pattern-sinks` / `pattern-sanitizers` 三段。**檔名要與靶檔同 stem**（`cmdi.yml` ↔ `cmdi.py`）——Semgrep 的 `--test` 靠這個配對。

3. **SARIF 輸出**（`cmdi.sarif`）：`semgrep --config cmdi.yml --sarif -o cmdi.sarif cmdi.py` 產出。

### 驗收標準

- **`semgrep --test --config cmdi.yml cmdi.py` 回 `All tests passed`**——這是硬驗收。它逐一比對你的 `# ruleid:` / `# ok:` 註解與規則實際命中：該報的報了、不該報的沒報，才算通過。任一處錯（漏報標了 ruleid 的行、或誤報標了 ok 的行）都會 fail 並指出哪行。
- 直接跑 `semgrep --config cmdi.yml cmdi.py` 的 findings 數與行號，要與你標的 `ruleid` 行完全吻合。
- `cmdi.sarif` 是合法 SARIF，`results` 數 = 該報的漏洞數。

## 分五步

1. **列 source/sink/sanitizer**（翻 Ch 11 命令注入表 + Ch 16 Python 規則）：source = `request.args.get(...)` 等；sink = `os.system(...)`、`os.popen(...)`、`subprocess.$M(..., shell=True, ...)`；sanitizer = `shlex.quote(...)`。這步是紙上作業，先想清楚三段各放什麼。
2. **寫靶 code `cmdi.py`**：把上面五（六）種情境各寫一個小 function，每個 sink 上面標 `# ruleid:` 或 `# ok:`。標註是你對規則的「期望答案」，`--test` 會拿它對答案。
3. **寫規則 `cmdi.yml`**：`mode: taint` + 三段 pattern。`subprocess ... shell=True` 用 `patterns:` 包 `pattern: subprocess.$M(..., shell=True, ...)` 精準抓「走 shell 的那種」，別把 `subprocess.run([...])` 也抓進來。
4. **先直跑對答案**：`semgrep --config cmdi.yml cmdi.py`，看 findings 行號是否 = 你標 ruleid 的行。不對就回去調 pattern（多半是 sink 太寬誤報 ok 行、或 sanitizer 沒生效）。
5. **跑 `--test` + 出 SARIF**：`semgrep --test --config cmdi.yml cmdi.py` 綠了，再 `--sarif -o cmdi.sarif` 出檔。

## 如果你卡住了

- **`--test` 直接崩（`IndexError: tuple index out of range`）**：你八成把 target 傳成目錄（`semgrep --test --config cmdi.yml .`）。傳**檔案**：`semgrep --test --config cmdi.yml cmdi.py`。目錄形式在某些版本的檔名配對會炸，指到檔案就好。
- **`--test` 報某個 `ok:` 行 fail（誤報）**：你的 sink 太寬。最常見是 `subprocess.run([...])`（安全的 argv 陣列）被你的 sink 抓到了——sink 要寫 `subprocess.$M(..., shell=True, ...)` 只抓 `shell=True`，不是抓所有 `subprocess.run`。
- **`--test` 報某個 `ruleid:` 行 fail（漏報）**：taint 沒連上。檢查 source 的 pattern 有沒有涵蓋你靶 code 用的形式（`request.args.get("host")` 對 `request.args.get(...)`）、sink pattern 對不對。間接流那題若漏報，確認 source→sink 中間只有單純賦值（Semgrep OSS 的 taint 能跟一段賦值傳遞）。
- **sanitizer 沒生效（`shlex.quote` 那行還是被報）**：`pattern-sanitizers` 的 pattern 要能匹配到「污染值被 quote 的那個表達式」——`pattern: shlex.quote(...)` 讓「經過 `shlex.quote` 的值」變乾淨，於是它流進 `os.system` 不再算 taint。
- **檔名沒配對，`--test` 說沒 test**：規則檔 stem 要等於靶檔 stem（`cmdi.yml` ↔ `cmdi.py`）。這是 Semgrep `--test` 的配對慣例。

## 參考解答

全部真跑（Semgrep 1.172.0 OSS，WSL Ubuntu）。

<details>
<summary>點開看完整靶 code + 規則 + test + 真實輸出</summary>

**靶 code `cmdi.py`**（含 `ruleid`/`ok` 測試標註）：

```python
import os
import subprocess
import shlex
from flask import request

def vuln_os_system():
    host = request.args.get("host")
    # ruleid: py-command-injection
    os.system("ping -c 1 " + host)

def vuln_subprocess_shell():
    host = request.args.get("host")
    # ruleid: py-command-injection
    subprocess.run("ping -c 1 " + host, shell=True)

def safe_argv_list():
    host = request.args.get("host")
    # ok: py-command-injection
    subprocess.run(["ping", "-c", "1", host])

def safe_sanitized():
    host = request.args.get("host")
    # ok: py-command-injection
    os.system("ping -c 1 " + shlex.quote(host))

def indirect_flow():
    raw = request.args.get("host")
    target = raw            # taint 經一次賦值傳遞
    cmd = "ping " + target
    # ruleid: py-command-injection
    os.system(cmd)

def constant_no_taint():
    # ok: py-command-injection
    os.system("ping -c 1 localhost")   # 常數，非污染 → 不該報
```

**規則 `cmdi.yml`**：

```yaml
rules:
  - id: py-command-injection
    languages: [python]
    severity: ERROR
    message: >-
      攻擊者可控輸入流入 shell 命令執行（os.system / subprocess shell=True），
      命令注入 (CWE-78)。改用 argv 陣列（subprocess.run([...])）或 shlex.quote。
    metadata:
      cwe: "CWE-78"
      category: security
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
```

**Step 4——直跑對答案**（findings 行號 = 標 ruleid 的行）：

```
$ semgrep --config cmdi.yml cmdi.py
Ran 1 rule on 1 file: 3 findings.
            9┆ os.system("ping -c 1 " + host)
           14┆ subprocess.run("ping -c 1 " + host, shell=True)
           31┆ os.system(cmd)
```

三個 finding 落在第 9、14、31 行——正是三個 `# ruleid:` 標的行。三個 `# ok:` 的行（第 19 的 argv 陣列、第 24 的 `shlex.quote`、第 34 的常數）**都沒被報**。

**Step 5——`--test` 硬驗收**：

```
$ semgrep --test --config cmdi.yml cmdi.py
  Scanning 1 file with 1 Code rule:
1/1: ✓ All tests passed
No tests for fixes found.
```

`All tests passed`——Semgrep 逐行比對 `ruleid`/`ok` 註解與實際命中，全對。這比「肉眼看行號」強，因為它把「期望」寫進 code、自動對答案，規則之後改動 regression 一跑就知。

**出 SARIF**：

```
$ semgrep --config cmdi.yml --sarif -o cmdi.sarif cmdi.py
 • Findings: 3 (3 blocking)

$ python3 -c 'import json; r=json.load(open("cmdi.sarif"))["runs"][0]; \
  print("tool:", r["tool"]["driver"]["name"], "| results:", len(r["results"])); \
  [print(" line", x["locations"][0]["physicalLocation"]["region"]["startLine"], x["ruleId"]) for x in r["results"]]'
tool: Semgrep OSS | results: 3
 line 9 py-command-injection
 line 14 py-command-injection
 line 31 py-command-injection
```

SARIF 三筆 result，行號 9/14/31 與 finding 一致，可直接上傳 GitHub Code Scanning（Ch 17／Ch 39）。

</details>

## 測試用例表

這張表是你「該覆蓋哪些情境」的檢查清單，也是 `--test` 在驗的東西：

| # | 情境 | 靶 code 長相 | 標註 | 為什麼要測 |
|---|---|---|---|---|
| 1 | 真陽·直接流 | `os.system("..."+host)` | `ruleid` | 最基本：污染直接進 sink，必須抓到 |
| 2 | 真陽·shell=True | `subprocess.run("..."+host, shell=True)` | `ruleid` | sink 的另一形態，`shell=True` 才危險 |
| 3 | 真陰·argv 陣列 | `subprocess.run(["ping",...,host])` | `ok` | 安全寫法，測 sink 不過寬（不抓 list 形式） |
| 4 | 真陰·被 sanitize | `os.system("..."+shlex.quote(host))` | `ok` | 測 sanitizer 真的切斷 taint |
| 5 | 真陰·常數 | `os.system("ping localhost")` | `ok` | 測「非污染不報」，避免看到 sink 就報的誤報 |
| 6 | 真陽·間接流 | `t=raw; cmd="ping "+t; os.system(cmd)` | `ruleid` | 測 taint 跟得過一段賦值傳遞 |

真陽（1/2/6）驗**不漏報**，真陰（3/4/5）驗**不誤報**。一個好規則兩邊都要顧——只顧抓（真陽全過）但真陰亂報，上 CI 就是誤報淹沒團隊（Ch 17 頭號踩雷）。

## 延伸挑戰

- **加 propagator 追跨函式**（接 Ch 14/16）：把污染包進一個 helper function（`def wrap(x): return "ping "+x`），再 `os.system(wrap(host))`。Semgrep OSS 的 taint 已能跟簡單的 return 傳遞（親測這種情況不加 propagator 也抓得到）；`pattern-propagators` 真正派上用場的是「污染流經物件欄位、collection、或 Semgrep 預設不跟的傳遞路徑」——構造一個那種情境，體會 propagator 是補「工具預設不追的邊」。
- **對真實開源專案跑**：找一個小型 Python web 專案（或 Ch 43 的 case study 靶），拿你的規則掃，看真陽/誤報比。真實 code 的 source（不只 Flask，還有 Django/FastAPI/CLI argv）、sink（自訂 shell wrapper）會逼你擴充規則——這是從「玩具規則」到「能用規則」的跨越。
- **對回 CodeQL 版本**（接 Ch 22 練習 D）：同一個命令注入，用 CodeQL 的 global taint 寫一遍。對比兩者：Semgrep 快、規則短、單檔為主；CodeQL 慢、要 build DB、但跨檔案 flow 更強。同一個漏洞兩套工具寫過，你才真懂各自的能力邊界（Ch 32 工具對比的動手版）。

## 本練習你該帶走的

- 一組**經過 `--test` 證明**的 taint 規則，比「跑起來看起來對」強一個量級——`ruleid`/`ok` 註解把期望寫進 code，改規則時 regression 一跑就知。
- 好規則要**兩邊都顧**：真陽（不漏報）+ 真陰（不誤報，尤其安全寫法/sanitize/常數不能亂報）。只顧抓不顧放，上 CI 就是誤報災難。
- sanitizer 是 taint 規則的靈魂——`shlex.quote`／argv 陣列讓「看起來危險的 sink」變安全，規則不建 sanitizer 就會把安全寫法一起誤報。
- 你現在有一套能上 CI（Ch 17）+ 出 SARIF（Ch 39）的成品規則，且走過了「列 source/sink/sanitizer → 寫靶 → 寫規則 → 測 → 出報告」的完整流程——這正是真實審計裡寫自訂規則的 SOP。

Semgrep 這條線（快、免 build、規則好寫、單檔為主）到此收齊。接下來 Part 4 換更重的工具：CodeQL——要 build 資料庫、用 QL 語言查詢，但換來跨檔案、跨函式的深度 flow 分析。下一章從 CodeQL 的核心模型講起：它怎麼把 code 變成可查詢的資料庫。

→ [Ch 18 CodeQL 模型](./18-codeql-model.md)
