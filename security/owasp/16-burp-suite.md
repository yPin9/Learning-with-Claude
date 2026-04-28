# Ch 16 — Burp Suite 完整

> 目標：精通 Burp Suite 的 Proxy / Repeater / Intruder / Decoder / Extender — web pentest 命脈工具。

## Burp Suite 是什麼

PortSwigger 公司的 web pentest 平台。**業界 80% pentester 用它**。

兩版：

- **Community**（免費）：基本功能
- **Professional**（每年 ~$450）：含 scanner、強化 Intruder、extensions

新手 / 學習用 Community 夠。

## 啟動 + 設 Proxy

1. 開 Burp Suite
2. **Temporary project** (Community 限制)
3. **Use Burp defaults**
4. Proxy → Options → Proxy listener: 127.0.0.1:8080（預設）

### Browser 設 proxy

選一：

#### a) Burp 內建 Browser

Proxy → Open Browser。Chromium-based，已經配好 proxy + cert。**最簡單**。

#### b) 系統 browser + FoxyProxy

Firefox 裝 FoxyProxy → 加 entry：

```
Title: Burp
Proxy: HTTP
Host: 127.0.0.1
Port: 8080
```

切換 proxy 1 click。

#### c) 系統 browser + 系統 proxy

Settings → Network → Manual proxy。**常常忘記關，請別這樣**。

## CA Cert 安裝（HTTPS）

Burp 是「**MITM**」HTTPS — 需要 browser 信任 Burp CA：

1. browser proxied through Burp
2. 訪問 `http://burp` → download `cacert.der`
3. browser 設 import → 信任此 CA for "websites"

之後 HTTPS 沒 cert error。

## 5 大 tab

### 1. Proxy（核心）

「**Intercept + History**」。

#### Intercept

「**攔每個 request 讓你改 / 看**」。

```
Proxy → Intercept → "Intercept is on"
↓
訪問 site → request 暫停在 Burp
↓
你可以 read / modify / drop / forward
```

debugging 必用。但 normal browsing 開 intercept 太煩，**通常關**，只在需要時開。

#### HTTP history

所有經過 Burp 的 request/response 都記。

filter / search / send to Repeater / Intruder / 等。

### 2. Repeater

「**重發 + 修改 request**」。最常用 tab。

```
Right-click on request → Send to Repeater
```

Repeater 視窗：

- 左：Request（可改）
- 右：Response

按 Send → 看 response。改 → Send → 看新 response。

**所有手動 fuzzing / 試 payload 都在 Repeater 做**。

### 3. Intruder

「**自動化 fuzz**」。

```
Send to Intruder → Positions tab → 標記要 fuzz 的位置
↓
Payloads tab → 給 wordlist
↓
Start attack
```

4 種 attack mode：

- **Sniper**：1 個 position，1 個 payload list
- **Battering ram**：N 個 position，1 個 payload（同樣 value 塞所有 position）
- **Pitchfork**：N 個 position，N 個 payload（並行 iterate）
- **Cluster bomb**：N 個 position，N 個 payload（笛卡爾乘積）

Community 版 Intruder **很慢**（throttle）。Pro 版 / sqlmap / ffuf 替代。

### 4. Decoder

URL encode / base64 / hex / hash / 等轉換。

```
Decoder → 輸入 → 選 encode/decode/hash → 結果
```

或快捷：選文字 → Ctrl-Shift-B（decode）。

### 5. Extender (BApp Store)

裝 plugins：

- **Authorize**：自動測 access control
- **Burp Bounty**：scan rules
- **Logger++**：強化 log
- **Param Miner**：找 hidden parameters
- **JWT Editor**：解 / 改 JWT

「**功能不夠 → 找 BApp**」。

## 常用工作流

### 1. 找 hidden parameter

裝 Param Miner → 對 endpoint 跑 → 找出 server 接受但 doc 沒寫的 parameter。

### 2. 攻 SQL injection

```
Repeater → 在參數塞 ' → 看 response error
→ 確認 → 用 sqlmap 自動 dump
```

### 3. 偷 / 改 JWT

```
HTTP history → 找帶 JWT 的 request
→ Send to Decoder → base64 decode payload
→ 修改 → re-encode
→ 用 JWT Editor 重簽
→ Repeater 送看 server 反應
```

### 4. IDOR 測試

```
登入 user A，用 Burp 抓正常 request
→ Send to Repeater
→ 改 ID 為 user B
→ Send → 看 response
```

### 5. CSRF PoC

```
Burp Engagement Tools → Generate CSRF PoC
→ 自動產 HTML form
```

## Match and Replace

「**自動改每個 request**」：

```
Proxy → Options → Match and Replace
+ Add → 規則
```

例：每個 response 把 `IsAdmin: false` 改 `IsAdmin: true`。

## Macros

「**先跑某些 request 才能跑主 request**」 — login flow 用。

```
Project options → Sessions → Macros
→ Record sequence (login)
→ 關聯到 scanner / repeater
```

## Scanner（Pro only）

「**Active scan**」自動找 OWASP Top 10。

```
Right-click → Scan → Active scan
```

非常強，但 Community 沒有。

替代：OWASP ZAP（Ch 17）。

## 一個常見踩雷：Intercept 開著沒關

Browse 100 個 request，每個都暫停在 Intercept → 慢死。

**開 intercept 只在需要時，預設關**。

## 一個常見踩雷：HTTPS cert 沒裝

每個 HTTPS request 都跳 cert warning → 瀏覽不順。

**裝 Burp CA**。

## 一個常見踩雷：對 production 跑 Intruder / Scanner

Intruder 大量 request 可能：

- 觸發 WAF / IPS
- 鎖定 user account
- 爆量 DDoS
- 違反 service ToS

**只對自己 lab / bug bounty scope target / 簽合約 client**。

## 一個常見踩雷：Burp 上次的 project state

Community 不能存 project（每次重開 fresh）。Pro 能存。

如果重要工作必存 → 用 Pro 或定期 export Logger++ data。

## 動手練習

**1. 設 Burp + browser proxy + CA**

按本章流程設好。能訪問 https://example.com 沒 cert error。

**2. Intercept + Repeater 玩 Juice Shop**

```
1. 訪問 Juice Shop login
2. Burp 攔到 POST /rest/user/login
3. Send to Repeater
4. 改 email 為 ' or 1=1-- 
5. Send → 看 response
```

確認 SQL injection。

**3. Intruder brute force**

對 Juice Shop login，用 Intruder 試 100 個常見密碼。看哪個成功（response 不同）。

Community 慢，至少 30 分鐘。

**4. Decoder 解 JWT**

Login 後從 Authorization header 取 JWT，放 Decoder：

```
Decode as → Base64
```

看 payload 內容。

**5. 裝 BApp**

Extender → BApp Store → 裝：

- Logger++
- JWT Editor
- Param Miner

跑一次每個 plugin 看功能。

## 自我檢核

- [ ] Burp + browser proxy 設好
- [ ] CA cert 安裝完成
- [ ] Repeater 改 request 玩過
- [ ] Intruder 4 種 mode 知道
- [ ] Decoder 用過
- [ ] 至少 1 個 BApp extension 裝
- [ ] 對 Juice Shop 用 Burp 完成 1 個 challenge

下一章看 OWASP ZAP — 免費替代品。

→ [Ch 17 OWASP ZAP](./17-owasp-zap.md)
