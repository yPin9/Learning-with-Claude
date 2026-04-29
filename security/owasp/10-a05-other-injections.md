# Ch 10 — A05 其他注入：Command / NoSQL / LDAP / SSTI / XXE

> 目標：認識 SQL / XSS 之外的常見注入類別 — 各自原理 + payload + 防禦。

> **2025 變動**：2021 在 A03，2025 在 A05（Injection 大類沒變，編號改）。

## 1. Command Injection (OS Command Injection)

把 user input 拼到 OS 命令：

```python
# 爛 code
filename = request.args.get('file')
os.system(f"cat {filename}")
```

攻擊：`?file=foo; cat /etc/passwd`

```bash
cat foo; cat /etc/passwd
```

`;` 讓 shell 跑兩條命令。

### 變形

```bash
cat foo && id            # AND
cat foo | id             # pipe
cat foo `whoami`         # backtick 替換
cat foo $(whoami)        # $() 替換
cat foo &                # background
cat foo \n id            # newline
```

### 防禦

**不要拼 string**。用：

```python
# 對：subprocess + list
import subprocess
subprocess.run(["cat", filename], check=True)
# shell 不執行，filename 不會被解析成 command
```

或：

- 嚴格 validate input（白名單）
- 用 library API 而非 shell（如 `shutil.copy` 取代 `cp`）

## 2. NoSQL Injection

MongoDB / Redis / Couchbase 等 NoSQL 也有 injection。

### MongoDB 例

```javascript
// 爛 code (Node + MongoDB)
db.users.findOne({
  username: req.body.username,
  password: req.body.password
});
```

如果 body 是 JSON：

```json
{
  "username": "admin",
  "password": {"$ne": null}
}
```

`{$ne: null}` 是 MongoDB operator「**not equal null**」 → 任何 password 都 match → bypass auth。

### 變形

```json
{"username": {"$gt": ""}, "password": {"$gt": ""}}    // 第一個 user
{"username": "admin", "password": {"$regex": "^a"}}   // 試密碼開頭
```

### 防禦

```javascript
// 對：強制 type
db.users.findOne({
  username: String(req.body.username),
  password: String(req.body.password)
});
```

或用 ODM library（mongoose）的 schema validation。

## 3. LDAP Injection

LDAP query 也有 injection。

```python
# 爛
filter = f"(uid={username})"
ldap.search(base, filter)
```

攻擊：`username = *` → `(uid=*)` → 列所有 user。

或：`username = admin)(|(password=*` → 改 LDAP filter 邏輯。

### 防禦

```python
# escape LDAP special chars
import ldap.filter
safe_filter = ldap.filter.escape_filter_chars(username)
```

或用 LDAP API 的 parameter binding。

## 4. SSTI (Server-Side Template Injection)

template engine 把 user input 當 template code 執行：

```python
# 爛
@app.route('/hello/<name>')
def hello(name):
    template = f"Hello, {name}!"
    return render_template_string(template)
```

攻擊：`/hello/{{7*7}}` → `Hello, 49!` → SSTI 確認。

接著：

```
{{config}}                              ← 看 Flask config
{{config.items()}}
{{''.__class__.__mro__[1].__subclasses__()}}    ← Python sandbox escape
```

最終 RCE：

```
{{ ''.__class__.__mro__[1].__subclasses__()[401](['id'], stdout=-1).communicate() }}
```

### 不同 template engine

| Engine | 確認 payload |
|---|---|
| Jinja2 (Python) | `{{7*7}}` → 49 |
| Twig (PHP) | `{{7*7}}` → 49 |
| Freemarker (Java) | `${7*7}` → 49 |
| ERB (Ruby) | `<%= 7*7 %>` → 49 |
| Velocity (Java) | `#set($x=7*7)$x` → 49 |

### 防禦

**永遠不要把 user input 當 template**：

```python
# 錯
return render_template_string(user_input)

# 對：template 是固定的，user input 是 variable
return render_template_string("Hello, {{ name }}!", name=user_input)
```

第二種 user_input 只是字串值，不會被當 template code。

## 5. XXE (XML External Entity)

XML parser 解析 user-supplied XML 時，**外部 entity** 攻擊：

```xml
<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<data>&xxe;</data>
```

server 解析 XML → entity 展開 → 讀 `/etc/passwd` → 內容塞 `<data>` 回應。

### 變形

```xml
<!-- SSRF -->
<!ENTITY xxe SYSTEM "http://internal-server/admin">

<!-- 拒絕服務（billion laughs） -->
<!ENTITY lol "lol">
<!ENTITY lol2 "&lol;&lol;&lol;&lol;...">
<!ENTITY lol3 "&lol2;&lol2;...">
... (entity 嵌套展開到 GB 級)
```

### 防禦

**Disable external entities**：

```python
# Python lxml
from lxml import etree
parser = etree.XMLParser(resolve_entities=False, no_network=True)
```

```java
// Java
DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
```

或**用 JSON 取代 XML**（多數場景）。

## 6. CSV Injection (Formula Injection)

user input 進 CSV，被 Excel / LibreOffice 當 formula 執行：

```csv
name,email
Alice,alice@a.com
=cmd|'/c calc'!A1,evil@evil.com
```

開 csv → Excel 跑 calculator（或更壞 — payload 跑任意 cmd）。

### 防禦

CSV cell 開頭如果是 `=` `+` `-` `@`，前面加 `'`：

```csv
'=cmd|'/c calc'!A1,evil@evil.com
```

「**output sanitization for CSV**」是常被忽略的。

## 7. Header Injection

把 user input 拼到 HTTP header：

```python
# 爛
response.headers['Location'] = '/redirect?to=' + user_input
```

攻擊：`user_input = "/safe\r\nSet-Cookie: hijack=1"`

```
HTTP/1.1 302 Found
Location: /redirect?to=/safe
Set-Cookie: hijack=1
```

`\r\n` 注入新 header → 設 attacker 的 cookie。

### 防禦

```python
# 過濾 \r \n
if '\r' in user_input or '\n' in user_input:
    abort(400)
```

或用 framework API 的 setter（會自動 reject newline）。

## 真實案例：Equifax（2017）

Apache Struts SSTI / OGNL injection：

- Struts 2 的 OGNL expression 注入
- 攻擊者送特製 Content-Type header → server 執行任意 code
- **1.43 億美國人個資外洩**
- Equifax 賠 $700M+

教訓：

- framework 的 expression injection 也是 RCE
- patch 沒及時上（CVE 公開後 Equifax 還拖 2 個月才修）

## 動手練習

**1. Command injection 練習**

```python
# vulnerable.py
from flask import Flask, request
import os

app = Flask(__name__)

@app.route('/ping')
def ping():
    host = request.args.get('host', 'localhost')
    result = os.popen(f"ping -c 1 {host}").read()
    return f'<pre>{result}</pre>'
```

```bash
curl 'http://localhost:5000/ping?host=8.8.8.8'                    # 正常
curl 'http://localhost:5000/ping?host=8.8.8.8;id'                 # injection
curl 'http://localhost:5000/ping?host=8.8.8.8|cat /etc/passwd'    # injection
```

**2. NoSQL injection on Juice Shop**

Juice Shop 用 SQLite，但有 NoSQL 課題（在某些 endpoint）。

或 build 個 vulnerable Node + MongoDB app。

**3. SSTI 練習**

```python
# vulnerable
@app.route('/hello')
def hello():
    name = request.args.get('name', 'world')
    return render_template_string(f"<h1>Hello, {name}!</h1>")
```

```bash
curl 'http://localhost:5000/hello?name={{7*7}}'                          # 49 → SSTI 確認
curl 'http://localhost:5000/hello?name={{config}}'                        # leak config
```

**4. XXE on PortSwigger Academy**

https://portswigger.net/web-security/xxe

XXE labs 完整 set。

**5. 寫一個包含這些 vuln 的 Vulnerable Flask app**

每個 endpoint 一個 vuln，self-test 寫 attack script。

## 自我檢核

- [ ] 6+ 種注入類型講得出
- [ ] Command injection 用 subprocess + list 防禦
- [ ] NoSQL injection 用 type force 防禦
- [ ] SSTI confirm payload 知道
- [ ] XXE 防禦：disable external entity
- [ ] 知道 CSV / Header injection 等少見類型

下一章看 A06 Insecure Design — framework 救不了的問題（2025 編號 A06）。

→ [Ch 11 A06 Insecure Design](./11-a06-insecure-design.md)
