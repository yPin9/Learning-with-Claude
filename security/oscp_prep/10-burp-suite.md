# Ch 10 — Burp Suite 精通：攔截、Repeater、Intruder

> 目標：設定好 Burp Proxy，能攔截和修改 HTTP 請求，用 Repeater 測試參數，用 Intruder 做暴力破解和 fuzzing。

## Burp Suite 是什麼

Burp Suite 是 Web 滲透測試的瑞士刀——一個 HTTP Proxy，讓你能：

- **攔截**瀏覽器發出的每個請求，看到原始 HTTP
- **修改**請求後再送出，測試各種注入
- **重放**特定請求（Repeater），不用一直用瀏覽器點
- **自動化**重複請求（Intruder），暴力破解或 fuzzing

Community Edition（免費版）夠考試用。

## 設定 Burp Proxy

### Step 1：設定監聽

```
Burp → Proxy → Options → Proxy Listeners
確認有 127.0.0.1:8080 在 Running
```

### Step 2：設定瀏覽器

**推薦：用 Firefox + FoxyProxy 插件**

1. 安裝 FoxyProxy Standard（Firefox 插件）
2. 新增一個 proxy：HTTP、127.0.0.1、8080
3. 需要攔截時切換到這個 proxy

或者用 Kali 內建的 Chromium，直接設 proxy：
```
Settings → Advanced → System → Open proxy settings
→ Manual proxy: 127.0.0.1:8080
```

### Step 3：安裝 CA 憑證（HTTPS 必要）

```
1. 瀏覽器連 http://burp（Burp 要在跑）
2. 點右上角 CA Certificate 下載
3. Firefox: Settings → Privacy → View Certificates → Import
4. 選 Trust this CA to identify websites
```

安裝完才能攔截 HTTPS。

## Proxy 攔截

Burp → Proxy → Intercept → **Intercept is on**

開啟後，每個瀏覽器請求都會暫停在 Burp，等你：
- `Forward`：放行
- `Drop`：丟棄
- `Action → Send to Repeater`：送到 Repeater 測試

**一般瀏覽時先關 Intercept**，需要攔截特定請求時再開。

## HTTP History

Proxy → HTTP History

這裡記錄了所有通過 Burp 的請求，即使沒有開 Intercept。可以：

- 右鍵 → Send to Repeater（測試特定請求）
- 右鍵 → Send to Intruder（暴力破解/fuzzing）
- 右鍵 → Send to Scanner（Pro 版）

## Repeater（最常用）

Repeater 讓你重複修改和發送單一請求：

```
把請求送到 Repeater → 改參數 → 按 Send → 看回應 → 改 → Send → ...
```

實際場景：

```
# 你在 HTTP History 看到一個登入請求：
POST /login.php HTTP/1.1
Host: 10.10.10.x
Content-Type: application/x-www-form-urlencoded

username=admin&password=admin123

# 送到 Repeater，改 username：
username=admin'--&password=anything
# 送出，看回應有沒有 SQL 錯誤

# 改另一個 payload：
username=admin' OR 1=1--&password=anything
```

### Repeater 快捷鍵

```
Ctrl+R  → 把請求送到 Repeater
Ctrl+G  → 在 Repeater 發送請求
Ctrl+S  → Repeater → 可以儲存請求
```

## Intruder（Fuzzing / 暴力破解）

把請求送到 Intruder（Ctrl+I 或右鍵）：

### 設定攻擊位置

Positions 頁面：

```
POST /login HTTP/1.1
...

username=§admin§&password=§password123§
```

`§` 符號標記要替換的位置。用 `Add §` 按鈕或手動加。

### 攻擊類型

```
Sniper    → 一個位置，輪流試 wordlist（最常用）
Battering Ram → 同一個值同時套用所有位置
Pitchfork → 多個 wordlist，各自一對一對應
Cluster Bomb → 所有組合（小心，量可能很大）
```

### 設定 Payload

Payloads 頁面：

```
Payload type: Simple list
→ 加入密碼列表（Load 從檔案載入）
   /usr/share/wordlists/rockyou.txt
   /usr/share/seclists/Passwords/Common-Credentials/best110.txt
```

### 識別成功的回應

啟動攻擊後，看：
- **Status**（HTTP 狀態碼）：成功登入通常是 302 Redirect
- **Length**：成功和失敗的回應大小不同
- **Response**：直接看回應內容

**注意**：Community Edition 的 Intruder 有速度限制（每秒 1 個請求），暴力破解很慢。對大型密碼本用 Hydra 更快。

## 實用功能

### Decoder

Burp → Decoder：編解碼工具

```
Base64 encode/decode
URL encode/decode
HTML encode/decode
Hash（MD5, SHA1, etc.）
```

Web 測試時常需要處理 Base64 或 URL 編碼的 token。

### Comparer

比較兩個回應的差異，找細微的變化：

```
Burp → Comparer → 貼入兩段文字 → Words/Bytes 比較
```

在 Blind SQLi 或 timing-based 攻擊中很有用。

### Search（Ctrl+F）

在所有 HTTP History 裡搜尋字串：

```
搜 "password" → 找有回傳密碼的 response
搜 "error"    → 找有錯誤訊息的 response（可能洩漏資訊）
```

## 實戰流程

### 測試 Web 應用的標準流程

```
1. 開 Burp，設好瀏覽器 proxy
2. 手動瀏覽網站，讓所有請求進 HTTP History
3. 看 HTTP History，找有趣的請求：
   - POST 表單（登入、搜尋、留言）
   - 帶參數的 GET（?id=1, ?file=about.php）
   - API 呼叫（/api/user/1）
4. 把值得測試的請求送到 Repeater
5. 開始手動測試（注入、越界存取等）
```

### 找到 SQLi 的典型 Repeater 流程

```
原始請求：
GET /item?id=1 HTTP/1.1

測試一（語法錯誤）：
GET /item?id=1' HTTP/1.1
→ 看回應有沒有 SQL 錯誤

測試二（True 條件）：
GET /item?id=1 OR 1=1-- HTTP/1.1
→ 看資料有沒有多出來

測試三（False 條件）：
GET /item?id=1 AND 1=2-- HTTP/1.1
→ 看資料有沒有消失

有差異 → 確認有 SQLi → 用 sqlmap 或手注深入（Ch 11）
```

## 本章對應靶機

- **HTB Beep**：有 Web 應用，用 Burp 看完整請求
- **THM OWASP Top 10**：練習各種 Web 漏洞，Burp 是必備
- **THM WebFundamentals**：基礎 Web 滲透，適合 Burp 入門

## 自我檢核

- [ ] Burp Proxy 設定完成，能攔截 HTTP 和 HTTPS
- [ ] 能把請求送到 Repeater 並修改參數
- [ ] 能用 Intruder 對一個參數跑 wordlist
- [ ] 能在 HTTP History 搜尋關鍵字

→ [Ch 11 SQL Injection：手注 + sqlmap](./11-sqli.md)
