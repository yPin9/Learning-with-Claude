# Ch 11 — 跨語言 sink 目錄

> **目標**：這章是一份**可查的參考手冊**——按漏洞類別組織的跨語言 sink 大表，涵蓋 C/C++、Java、JS/Node、Python 的代表性 source、sink、sanitizer 長相，每類對到 CWE 編號。之後你寫 Semgrep/CodeQL query 時，「這語言這類漏洞的 sink 是哪幾個函式」直接翻這裡。手冊歸手冊，這章一樣有踩雷，因為跨語言最坑的正是「同名函式語意不同」與「framework 把 sink 藏起來」。

前兩章你學會列舉 source/sink、選 target。但真動手時你會卡在細節：「Java 的命令注入 sink 到底是 `Runtime.exec` 還是 `ProcessBuilder`？兩者差在哪？」「Python 的 `subprocess` 什麼時候安全什麼時候不安全？」這章把這些答案表格化。**用法是查，不是背**——但每一類的「sanitizer 長相」與「常見誤判」值得讀進腦子，因為那決定你的 query 會不會漏報誤報。

CWE 編號我盡量給準；把握不足的地方會標示或用上位類別。以 MITRE CWE 官網為最終依據。

## 記憶體安全（C/C++）

CWE：**CWE-787**（Out-of-bounds Write）、**CWE-125**（Out-of-bounds Read）、**CWE-120**（Buffer Copy without Checking Size / classic overflow）、**CWE-416**（Use After Free）、**CWE-190**（Integer Overflow）、**CWE-134**（Format String）。

| 類別 | source | sink | sanitizer 長相 |
|---|---|---|---|
| buffer overflow | `recv`/`read`/`fread`、argv、parse 出的長度 | `memcpy` `memmove` `strcpy` `strcat` `sprintf` `alloca`、`dst[i]=` 用污染 index | memcpy 前 `if (len > sizeof(dst)) fail`；用 `strlcpy`/`snprintf` 帶界 |
| 整數溢位→配置 | 污染的 count/size | `malloc(n*sz)` `a[idx]` 前的算術 | overflow-checked 乘法（`__builtin_mul_overflow`）、上界檢查 |
| format string | 污染字串當格式 | `printf(userstr)` `syslog(userstr)` `snprintf(buf, n, userstr)` | 用 `printf("%s", userstr)`——常數格式串 |
| UAF | free 後仍持有的指標 | free 後的 `*p`、double `free(p)` | free 後 `p=NULL`；所有權清晰化 |

記憶體安全 sink 的重點**不是函式名，是「長度/索引/指標是否被污染」**。`memcpy` 本身無罪，`memcpy(dst, src, tainted_len)` 才是 sink。這是為什麼純語法 grep（Ch 13）抓 memcpy 命中滿地誤報，要 taint（Ch 14/22）才準。

## 命令注入（OS Command Injection）

CWE：**CWE-78**（OS Command Injection）。

| 語言 | source | sink | sanitizer / 安全寫法 |
|---|---|---|---|
| C/C++ | argv、網路輸入 | `system()` `popen()` `execl`(經 shell) | `execve` 直接給 argv 陣列（不經 shell）；allowlist |
| Java | request 參數 | `Runtime.getRuntime().exec(str)`、`ProcessBuilder` 用單一 shell 字串 | `ProcessBuilder` 用**參數陣列**（不拼 shell）；避免 `sh -c` |
| Node.js | `req.body`/`req.query` | `child_process.exec(cmd)` `execSync` | 用 `execFile`/`spawn` 給 **arg 陣列**、`shell:false` |
| Python | Flask/Django 參數 | `os.system` `subprocess.run(cmd, shell=True)` `os.popen` | `subprocess.run([...], shell=False)`（list 形式）；`shlex.quote` |

**核心分界線：走不走 shell。** 只要走 `/bin/sh -c "..."`（`system`、`shell=True`、`Runtime.exec` 傳字串），攻擊者用 `;` `|` `` ` `` `$()` 就能注入。給 argv 陣列直接 `execve`/`spawn` 不經 shell，就沒有 shell metacharacter 注入面（但仍可能有 argument injection——污染值變成危險的 flag，如 `--output=/etc/...`）。

## SQL 注入

CWE：**CWE-89**（SQL Injection）。

| 語言 | sink | sanitizer / 安全寫法 |
|---|---|---|
| Java | `Statement.execute(sql字串拼接)`、JPQL/HQL 字串拼接 | `PreparedStatement` + `?` 佔位符、參數化 |
| Node.js | `db.query("... " + userInput)`、template literal 拼 SQL | 參數化 `db.query(sql, [params])`、ORM 的 parameterized API |
| Python | `cursor.execute("... %s" % userInput)`、f-string 拼 SQL | `cursor.execute(sql, params)`（**逗號傳參，不是 % 格式化**） |
| C/C++ | 手拼 SQL 傳給 DB library | 參數化 API |

**常見誤判**：看到 `cursor.execute(query, params)` 就以為安全——**要看 query 本身有沒有先被 f-string/`%`/`+` 拼進污染值**。`cursor.execute(f"SELECT ... {name}")` 即使有第二參數也是注入。參數化的重點是「值透過 driver 的佔位符機制傳」，不是「有沒有用到第二參數」。

## 路徑穿越（Path Traversal）

CWE：**CWE-22**（Path Traversal，`../` 逃出目錄）。相關 **CWE-73**（External Control of File Name/Path）。

| 語言 | source | sink | sanitizer 長相 |
|---|---|---|---|
| C/C++ | argv、網路檔名 | `open` `fopen` `unlink` `rename` 的路徑參數 | `realpath` 後檢查前綴；拒 `..`/絕對路徑 |
| Java | request 參數 | `new File(userPath)` `Files.newInputStream` `FileInputStream` | `getCanonicalPath().startsWith(base)`；`Path.normalize` 後驗 |
| Node.js | `req.params` | `fs.readFile(userPath)` `fs.createReadStream` | `path.resolve` 後 `startsWith(base)`；拒 `..` |
| Python | Flask 參數 | `open(userPath)` `os.path.join(base, user)` | `os.path.realpath` 後 `startswith(base)` |

**最大誤判**：以為 `os.path.join(base, user)` / `path.join` 會把你關在 `base` 裡。**不會**。`user` 含 `..` 會往上跳；含絕對路徑（`/etc/passwd`）在 Python 會直接**丟掉 base**。join 不是 sanitizer，正規化後驗前綴才是。

## 反序列化（Deserialization）

CWE：**CWE-502**（Deserialization of Untrusted Data）。

| 語言 | 危險 sink（整個呼叫即 sink） | 安全替代 |
|---|---|---|
| Java | `ObjectInputStream.readObject()`、危險的 XStream/Kryo 設定、部分 Jackson polymorphic 設定 | 只反序列化到已知型別；用 JSON/Protobuf、關掉 polymorphic type |
| Python | `pickle.loads` `pickle.load`、`yaml.load`（非 `safe_load`）、`shelve`、`jsonpickle` | `yaml.safe_load`、JSON、白名單反序列化 |
| PHP | `unserialize($untrusted)`（觸發 magic method → POP chain） | JSON、`unserialize` 帶 `allowed_classes=false` |
| Node.js | `node-serialize`、部分 `eval` 型反序列化 library | JSON.parse（純資料，別接 `eval`） |

反序列化 sink 的特性：**沒有「危險參數」，整個呼叫就是危險的**，只要輸入不可信。真正的 exploit 靠 **gadget chain**（利用既有類別的 magic method/`readObject` 串成任意執行），但從審計角度，命中就是「untrusted 資料進了這些函式」。這也是為什麼這類 sink 的 source 判定（資料真的不可信嗎）比 sink 判定更關鍵。

## SSRF（Server-Side Request Forgery）

CWE：**CWE-918**（SSRF）。

| 語言 | sink | sanitizer 長相 |
|---|---|---|
| Java | `URL.openConnection` `HttpClient.send`、Apache `HttpGet(userUrl)` | URL 解析後驗 host allowlist、擋內網 IP、DNS rebinding 防護 |
| Node.js | `http.get(userUrl)` `fetch(userUrl)` `axios.get(userUrl)` | 同上；擋 `169.254.169.254`（雲 metadata） |
| Python | `requests.get(userUrl)` `urllib.request.urlopen` | 同上 |
| C/C++ | 把 userUrl 交給 libcurl 等 | 限制協議（禁 `file://` `gopher://`）、host 白名單 |

**常見誤判**：以為「檢查了 URL 開頭是 http」就安全。SSRF 的 sanitizer 難在——攻擊者能用 redirect、DNS rebinding、`[::]`/十進位 IP/`@` 混淆繞過簡單的字串檢查。有效 sanitizer 要在**解析後、發請求前**針對解析出的實際 IP 驗白名單，且處理 redirect。

## SSTI（Server-Side Template Injection）

CWE：**CWE-1336**（Improper Neutralization of Special Elements Used in a Template Engine），上位類別 **CWE-94**（Code Injection）。

| 語言/引擎 | sink | 誤判/安全寫法 |
|---|---|---|
| Python/Jinja2 | `render_template_string(userInput)`、把污染拼進 template 字串 | 把 user 資料當**變數傳入**（`render(tpl, name=user)`），不是拼進 template 原文 |
| Java/Freemarker/Velocity | 把 user 資料拼成 template 來源 | 同上；沙箱化引擎 |
| Node/多引擎 | 動態組 template 字串 | context 資料與 template 分離 |

分界線：**user 資料當「資料」傳，還是當「模板原文」拼**。前者安全，後者是 SSTI（可到 RCE，因為模板引擎能執行程式碼）。`render_template("x.html", name=user)` 安全；`render_template_string("Hi " + user)` 是 SSTI。

## XXE（XML External Entity）

CWE：**CWE-611**（Improper Restriction of XML External Entity Reference）。

| 語言 | sink | sanitizer 長相 |
|---|---|---|
| Java | `DocumentBuilderFactory` `SAXParserFactory` `XMLInputFactory` 預設設定解析 untrusted XML | 關掉 `external-general-entities`、`disallow-doctype-decl` |
| Python | `lxml.etree` `xml.etree`（舊版）解析 untrusted | 用 `defusedxml`；關 entity resolution |
| .NET/PHP | 各自 XML parser 預設允許 DTD/entity | 關 DTD 處理 |

XXE 的坑是**預設不安全**：很多 XML parser 出廠就允許外部 entity，sink 是「用預設設定解析了 untrusted XML」，sanitizer 是明確關掉 DTD/external entity。看到 XML parse 而沒看到 hardening 設定，就是 candidate。

## Prototype Pollution（JS 專屬）

CWE：**CWE-1321**（Improperly Controlled Modification of Object Prototype Attributes）。

| source | sink | sanitizer 長相 |
|---|---|---|
| `req.body`/`req.query`（JSON、query string） | 遞迴 merge/clone/`set(obj, path, val)`、`obj[key1][key2]=val` 用污染 key | 過濾 `__proto__`/`constructor`/`prototype` key；用 `Map`；`Object.create(null)` |

JS 獨有：污染 `Object.prototype` 會影響**所有物件**，可導致 DoS、屬性注入、甚至 RCE（配合後續 gadget）。sink 是「用攻擊者控制的 key 遞迴賦值」，典型出現在 config merge、query parser、老版 lodash `merge`/`set`。

## 其他常見注入速查

| 類別 | CWE | 代表 sink |
|---|---|---|
| XSS | CWE-79 | 前端 `innerHTML`、後端把 user 資料未 escape 塞進 HTML 回應 |
| LDAP injection | CWE-90 | 拼 LDAP filter 字串（`(uid=userInput)`） |
| XPath injection | CWE-643 | 拼 XPath query 字串 |
| Log injection | CWE-117 | 未淨化 user 資料寫入 log（可偽造記錄；配合可解析後端→Log4Shell） |
| ReDoS | CWE-1333 | 用污染字串當 regex，或污染輸入餵給有災難回溯的 regex |

## 跨語言踩雷集錦

**錯誤直覺：「`exec` 就是危險 sink，看到就標。」**
正確認識：同名函式跨語言語意天差地別。Node 的 `child_process.execFile`（給 arg 陣列，安全）和 `exec`（走 shell，危險）差一個字但天壤之別；Python `subprocess.run` 的危險完全取決於 `shell=True` 與否；Java `ProcessBuilder` 給陣列安全、給 `sh -c` 字串危險。**光看函式名不夠，要看怎麼呼叫**。你的 query 必須把「呼叫形態」也編進去，否則誤報漫天。

**錯誤直覺：「用了參數化 API / 有第二參數就沒 SQL 注入。」**
正確認識：`cursor.execute(f"...{name}", params)`——即使有 `params`，query 字串本身已經被 f-string 拼進污染值，照樣注入。參數化的本質是「值透過 driver 佔位符傳」，不是「呼叫時多給一個參數」。審計要盯 **query 字串是怎麼組出來的**，不是有沒有第二參數。

**錯誤直覺：「framework/ORM 包過了就安全，不用列它的 sink。」**
正確認識：ORM 底層還是拼 SQL，raw query API、`.raw()`、字串條件拼接照樣注入；logging 框架若後端會解析（Log4Shell）就是 sink；模板引擎的 `render_string` 是 SSTI sink。framework 把 sink **包裝**起來，不代表**消除**了它。你要建模 framework 包裝過的間接 sink（Ch 23 models-as-data 就是幹這個）。

**錯誤直覺：「`path.join`/`os.path.join` 會把我關在 base 目錄裡。」**
正確認識：不會。含 `..` 會往上跳；Python 的 `os.path.join` 遇到絕對路徑會**丟棄前面所有部分**。join 是路徑組合，不是安全邊界。唯一有效的路徑 sanitizer 是「正規化（`realpath`/`getCanonicalPath`）後檢查前綴」。

**錯誤直覺：「這表列的 sink 就是全部，照著抓就完整。」**
正確認識：這是**起點不是終點**。每個專案有自訂的 wrapper（自己包的 `db_query()`、自己的 `safe_open()`），間接 sink 藏在專案特有的抽象層裡。用這表建立「危險語意的類別」，再到目標 codebase 找它**本地的** sink 別名——這正是 Ch 20 建 DB、Ch 23 加自訂 model 的動機。

## 進階延伸

- **sink 的「污染型別」對齊**：同一份污染流到 SQL sink 要 SQL sanitizer、流到 shell sink 要 shell sanitizer。進階 taint（Ch 23 flow state）讓你為污染打標籤，精準匹配「這個 sink 需要哪種 sanitizer」，避免「用 HTML escape 當 SQL sanitizer」的錯配。
- **argument injection**：命令注入用 argv 陣列（不走 shell）擋掉了 metacharacter，但污染值變成 flag（`--config=evil`、`-o /etc/cron.d/x`）仍可能出事。這是 shell-injection 已死時代的新面，CWE 上多歸 CWE-88（Argument Injection）。
- **second-order / stored injection**：source 不是即時輸入而是「之前存進 DB 的污染資料被讀回來用」。這類 flow 跨了持久化邊界，taint 工具預設不追（DB 讀回不被當 source），要手動把「DB 回讀」建模成 source。

## 本章重點整理

- 這章是**按漏洞類別 × 語言**的 sink 查表：記憶體安全（CWE-787/125/120/416/190/134）、命令注入（CWE-78）、SQLi（CWE-89）、路徑穿越（CWE-22）、反序列化（CWE-502）、SSRF（CWE-918）、SSTI（CWE-1336/94）、XXE（CWE-611）、prototype pollution（CWE-1321）、XSS（CWE-79）等。
- 記憶體安全 sink 看的是**長度/索引/指標是否被污染**，不是函式名。
- 命令注入的分界是**走不走 shell**；SQLi 的分界是 **query 字串怎麼組出來**；路徑穿越的分界是**有沒有正規化後驗前綴**（`join` 不算）。
- 反序列化/XXE/SSTI 常是「整個呼叫即 sink」或「預設不安全」，判定重心在 source 可信度與設定。
- 這表是**起點**：真實專案有自訂 wrapper 與間接 sink，要用它建立「危險語意類別」再找本地別名。

## 自我檢核

- 不查表，寫出命令注入在 C/Java/Node/Python 各自的危險 sink 與「安全寫法」的分界線。
- `cursor.execute(f"SELECT * FROM u WHERE n='{name}'", ())` 有沒有 SQL 注入？為什麼「有第二參數」不代表安全？
- 為什麼 `os.path.join(base, user)` 不是路徑穿越的 sanitizer？有效的 sanitizer 長什麼樣？
- 反序列化 sink 為什麼說「沒有危險參數、整個呼叫即 sink」？它的 candidate 判定重心在哪一端（source 還 sink）？
- 給 SSRF 一個「檢查 URL 開頭是 http」會被繞過的具體手法，並說有效 sanitizer 該在哪一步做。
- 為什麼這份 sink 表只是起點？舉一個「專案自訂 wrapper 導致這表抓不到」的情境，並說怎麼補（連到哪一章）。

## 延伸閱讀

- **MITRE CWE 官網（cwe.mitre.org），CWE-79/89/78/22/502/918/611/1321 各條**——每個 sink 類別的權威定義、例子、緩解。用法：寫某類 query 前先讀對應 CWE 的 "Demonstrative Examples" 與 "Potential Mitigations"。前提：本章當索引。這是全課引用 CWE 的最終依據。
- **OWASP Cheat Sheet Series（SQL Injection Prevention、Command Injection、Deserialization、SSRF Prevention 等）**——每類漏洞的 sanitizer/安全寫法權威指引。用法：查「這語言這類漏洞的正確防法長怎樣」，反推「無效 sanitizer」怎麼認。前提：本章。
- **Semgrep 官方 rule registry（semgrep.dev/r）**——上千條現成規則，等於「別人整理好的 sink 目錄 + query」。用法：搜某語言某類漏洞，看它的 `pattern-sinks`/`pattern-sanitizers` 怎麼寫，是本章表格的可執行版。前提：Ch 13–14。
- **CodeQL 標準庫的 `Concepts`/`Sinks` 文件（各語言 dataflow library）**——CodeQL 官方對每類 sink 的建模。用法：Part 4 寫 query 時對照，看官方把哪些函式建成 sink、哪些當 sanitizer。前提：Ch 18 起。是本章表格在 CodeQL 裡的落地。

有了 source/sink/sanitizer 思維、攻擊面地圖、跨語言 sink 表，你已經能大量產出 candidate。但真實工具會給你成百上千個命中，其中絕大多數是誤報。下一章教你 triage 方法論——怎麼從一堆命中裡快速篩出真的，這是審計裡最難也最值錢的技能。

→ [Ch 12 誤報三角與可信度分級](./12-false-positive-triage.md)
