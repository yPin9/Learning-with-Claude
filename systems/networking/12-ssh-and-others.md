# Ch 12 — SSH 與其他協定

> **目標**：理解 SSH（安全 shell）的原理——它怎麼用類似 TLS 的加密做安全遠端登入、金鑰認證 vs 密碼認證、SSH 的「不只登入」（port forwarding/tunnel）、以及其他常見應用層協定速覽（SMTP/IMAP、FTP/SFTP、WebSocket）。SSH 是 Part 8 管理 VPS 的核心工具（Ch 34 深入），這章先建立原理，並補完應用層的全貌。

> **環境**：Linux（ssh / openssh）。

## 為什麼 SSH 是工程師的命脈？

只要你管過遠端伺服器，就用 SSH。它是「安全地登入並操作遠端機器」的標準——你在自己的電腦敲命令，實際在遠端伺服器執行，所有通訊都加密。沒有 SSH（或它的前身），遠端管理就得用 telnet（明文，密碼直接在網路上裸奔）。

SSH 和 TLS（Ch 11）有共通的密碼學原理（非對稱+對稱加密），但用途不同——TLS 主要驗證「伺服器身分」（網站），SSH 還重視驗證「客戶端身分」（你是不是有權登入的人）。理解 SSH 的金鑰認證，是 Part 8（VPS 管理）的核心——你會用金鑰登入 VPS、用 SSH tunnel 做各種事。這章先打底原理，Ch 34 會深入實戰。最後速覽其他應用層協定，補完你對「應用層有哪些協定」的全貌。

## 先建立直覺:SSH 是加密的遠端終端機

```
SSH = 加密的遠端 shell（在遠端機器執行命令）

  你的電腦                          遠端伺服器
    │                                  │
    │── ssh user@server ─────────────▶│  建立加密連線（類似 TLS）
    │                                  │
    │  [認證：你是有權登入的人嗎？]      │  驗證身分（金鑰或密碼）
    │                                  │
    │═══════ 加密通道 ═══════════════════│
    │  你敲的命令 → 加密送到遠端 → 執行  │
    │  遠端的輸出 → 加密送回 → 顯示給你  │
        │
  → 你彷彿坐在遠端機器前操作，但實際隔著網路
    所有通訊加密（別人看不到你的命令和密碼）
        │
  和 TLS 的關係：
    都用「非對稱換鑰匙 + 對稱傳資料」（Ch 11）
    差別：SSH 還重視「驗證客戶端」（你有權登入嗎）
         TLS 主要驗證「伺服器」（這是真的網站嗎）
```

關鍵心智：SSH 是「加密的遠端終端機」——你在本機敲命令，加密送到遠端執行，輸出加密送回。它和 TLS（Ch 11）共用密碼學原理（非對稱換鑰匙+對稱傳資料），但更重視**驗證客戶端身分**（你是不是有權登入的人）。

> SSH 的加密原理建立在 Ch 11 的非對稱/對稱加密上。如果對「公鑰/私鑰、為什麼用非對稱換鑰匙」不熟，回看 [Ch 11](./11-tls-https.md)。SSH 是 Part 8（Ch 34）的核心工具，這章打原理基礎。

## SSH 的認證:金鑰 vs 密碼

```
SSH 兩種認證方式：

  密碼認證：
    你輸入密碼 → 加密送到伺服器 → 伺服器驗證
    問題：密碼可能被猜（暴力破解）、被釣魚、被重複使用
        │
  金鑰認證（公鑰認證，推薦）：
    你有一對金鑰：私鑰（自己留）+ 公鑰（放伺服器）
    登入時：
      伺服器用你的「公鑰」出一道題（只有私鑰能解）
      你用「私鑰」解開 → 證明「你就是擁有私鑰的人」
      → 不用傳密碼！私鑰從不離開你的電腦
        │
  金鑰認證的優勢：
    - 不傳密碼（防竊聽、防釣魚）
    - 私鑰可加密碼保護（雙因素）
    - 能自動化（腳本/CI 不用存密碼）
    - 能輕易撤銷（刪掉伺服器上的公鑰）
        │
  → 生產環境一律用金鑰認證，關閉密碼登入（Ch 35 安全加固）
```

```bash
# 產生 SSH 金鑰對（現代用 ed25519，比 RSA 更好）
ssh-keygen -t ed25519 -C "your_email@example.com"
# 產生：~/.ssh/id_ed25519（私鑰，保密！）
#       ~/.ssh/id_ed25519.pub（公鑰，放伺服器）

# 把公鑰複製到伺服器（之後用金鑰登入）
ssh-copy-id user@server
# 它把你的公鑰加到伺服器的 ~/.ssh/authorized_keys

# 用金鑰登入（不用密碼）
ssh user@server

# 看 SSH 連線的細節（-v 詳細，debug 認證問題）
ssh -v user@server 2>&1 | grep -i 'auth\|key\|offering'
# debug1: Offering public key: ...
# debug1: Authentication succeeded (publickey)
```

> **SSH 金鑰認證的核心是「私鑰從不離開你的電腦」——這比密碼安全得多，是生產環境的標準**。密碼認證有諸多弱點（可暴力破解、可釣魚、重複使用一個密碼洩漏全部）。**金鑰認證**用一對金鑰——**私鑰**（你自己留，絕不外傳）和**公鑰**（放在伺服器的 `~/.ssh/authorized_keys`）。登入時，伺服器用你的公鑰出一道「只有對應私鑰能解」的題，你用私鑰解開證明身分——**全程不傳密碼，私鑰從不離開你的電腦**。這帶來：防竊聽/釣魚（沒有密碼可偷）、可加密碼保護私鑰（雙因素：有私鑰檔 + 知道它的密碼）、可自動化（CI/腳本用金鑰不用存明文密碼）、可輕易撤銷（刪伺服器上的公鑰）。現代推薦 **ed25519** 金鑰（比 RSA 更短更安全更快）。`ssh-copy-id` 自動把公鑰部署到伺服器。生產環境的標準做法是**金鑰認證 + 關閉密碼登入**（Ch 35 安全加固）——這擋掉了絕大多數 SSH 暴力破解攻擊（公網上的 VPS 每天被掃描嘗試密碼登入，關掉密碼登入它們就沒轍）。Ch 34 會深入 SSH 的進階用法。

## SSH 不只登入:port forwarding 與 tunnel

SSH 能做的遠不止登入——它能建立加密隧道轉發任意流量：

```
SSH 的三種 port forwarding（隧道）：

  1. 本地轉發（-L）：把「本地 port」轉到「遠端能到的地方」
     ssh -L 8080:localhost:80 user@server
     → 連你本機的 8080 = 透過 server 連 server 的 80
     用途：訪問遠端內網的服務（如遠端的資料庫）
        │
  2. 遠端轉發（-R）：把「遠端 port」轉到「本地能到的地方」
     ssh -R 8080:localhost:3000 user@server
     → 連 server 的 8080 = 透過你本機連你的 3000
     用途：把本地服務暴露給遠端（如 demo 本地開發的網站）
        │
  3. 動態轉發（-D）：建一個 SOCKS proxy
     ssh -D 1080 user@server
     → 本機 1080 變成 SOCKS5 proxy，流量經 server 出去
     用途：把 server 當跳板，所有流量經它（簡易 VPN/翻牆！Ch 28）
        │
  → SSH tunnel = 用加密的 SSH 連線「夾帶」其他流量
    這是「窮人的 VPN」，也是翻牆的早期手段（Ch 28）
```

```bash
# 本地轉發：訪問遠端內網的資料庫（遠端的 5432 映射到本機 5432）
ssh -L 5432:db-internal:5432 user@jump-server
# 之後連本機 localhost:5432 = 連到遠端內網的 db-internal:5432

# 動態轉發：把 SSH 伺服器當 SOCKS proxy（簡易翻牆）
ssh -D 1080 user@server
# 設瀏覽器用 SOCKS5 proxy 127.0.0.1:1080
# → 所有流量經過 server 出去（server 在哪，你看起來就在哪）

# 只建隧道不開 shell（-N 不執行命令，-f 背景）
ssh -fN -D 1080 user@server
```

> **SSH tunnel 是「窮人的 VPN」——用加密的 SSH 連線夾帶其他流量，也是翻牆的早期手段**。SSH 不只能登入，還能建立加密隧道轉發任意流量。**動態轉發 `-D`** 最強大——它把 SSH 伺服器變成一個 **SOCKS5 proxy**（Ch 28），你的流量經過 SSH 加密通道、從伺服器出去。如果伺服器在國外，你的流量就「看起來從國外發出」——這是最簡單的翻牆/跳板方式（`ssh -D 1080 user@海外server` + 瀏覽器設 SOCKS proxy）。**本地轉發 `-L`** 讓你訪問遠端內網的服務（如透過跳板機連內網資料庫）——這是 DevOps 日常（連雲端 VPC 內的資料庫）。**遠端轉發 `-R`** 把本地服務暴露給遠端（demo 本地開發成果）。這些隧道都建立在「SSH 連線本來就是加密的」之上——夾帶的流量自動受保護。SSH tunnel 的優勢是「幾乎到處有 SSH」（不用裝額外 VPN 軟體），缺點是只走 TCP、效能不如專門的 VPN（Ch 24 WireGuard）。理解 SSH tunnel，你就有了一個隨手可用的加密跳板工具，也理解了 Ch 28 翻牆生態的起點（早期翻牆就是 SSH -D）。

## 其他應用層協定速覽

```
常見應用層協定（補完全貌）：

  郵件相關：
    SMTP（25/587）：寄信（把信送到郵件伺服器）
    IMAP（143/993）：收信（在伺服器上管理信件，多設備同步）
    POP3（110/995）：收信（下載到本地，較舊）
        │
  檔案傳輸：
    FTP（21）：老協定，明文，分控制和資料兩個連線（NAT 不友善）
    SFTP：基於 SSH 的檔案傳輸（加密，推薦）
    SCP：基於 SSH 的檔案複製（簡單）
        │
  即時 / 雙向：
    WebSocket（ws/wss）：在 HTTP 上升級成「全雙工」連線
      （瀏覽器和伺服器能互相主動推送，用於聊天/即時更新）
    MQTT：輕量訊息協定（IoT 常用）
        │
  其他：
    DNS（53）：域名解析（Ch 9）
    NTP（123）：時間同步
    DHCP（67/68）：自動分配 IP
```

```bash
# SFTP/SCP（基於 SSH 的檔案傳輸，加密）
scp file.txt user@server:/path/        # 複製檔案到遠端
scp user@server:/path/file.txt .       # 從遠端複製回來
sftp user@server                        # 互動式檔案傳輸

# 看一個郵件伺服器（SMTP，純文字協定能手打）
# nc smtp.example.com 25
# 220 smtp.example.com ESMTP    ← SMTP 也是文字協定

# WebSocket：HTTP 升級成雙向連線
# 請求帶 Upgrade: websocket 標頭 → 從 HTTP 切換成 WebSocket
```

> **這些應用層協定大多建立在 TCP/TLS 之上，且現代版本都走加密——「明文協定」正在淘汰**。郵件的 **SMTP**（寄）、**IMAP**（收，多設備同步）現代都有 TLS 版本（993/995 是加密埠）。檔案傳輸的 **FTP**（老、明文、分兩個連線對 NAT 不友善）正被 **SFTP/SCP**（基於 SSH，加密）取代——你管 VPS 傳檔案就用 scp/sftp。**WebSocket**（wss）解決了 HTTP「只能客戶端發起」的限制——它在 HTTP 連線上「升級」成全雙工（伺服器能主動推送），用於即時聊天、通知、協作編輯（Google Docs 那種即時同步）。**MQTT** 是 IoT 的輕量訊息協定。注意一個趨勢：**明文協定正在淘汰**——telnet（被 SSH 取代）、明文 FTP（被 SFTP 取代）、明文 HTTP（被 HTTPS 取代）、明文郵件（被 TLS 版取代）。這呼應 Ch 11 的核心思想：底層網路不可信，所有協定都該加密。理解這些協定的存在和它們走哪個 port，你看 `ss -tlnp`（看伺服器開了哪些服務，Ch 13）時就認得出來，也能在防火牆規則（Ch 18）裡正確開放它們。

## 故意弄壞:SSH 認證問題排查

```bash
# SSH 連不上的常見問題排查（Part 8 會常遇到）

# 1. 用 -v 看認證過程（debug 金鑰問題）
ssh -v user@server 2>&1 | grep -i 'offer\|accept\|deny\|permission'
# 看它嘗試哪些金鑰、哪個被接受/拒絕

# 2. 權限問題（SSH 對檔案權限很嚴格！）
ls -l ~/.ssh/
# 私鑰必須 600（只有你能讀），~/.ssh 必須 700
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
#   權限太開放 → SSH 拒絕用這個金鑰（安全機制）
#   "Permissions 0644 for 'id_ed25519' are too open" 是經典錯誤

# 3. 伺服器端的 authorized_keys 權限
# 伺服器的 ~/.ssh/authorized_keys 也要對的權限（600）

# 4. host key 改變警告（可能是伺服器重裝，也可能是中間人！）
# "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED"
# → 伺服器的 host key 變了。確認是預期的（重裝）才清掉舊的：
# ssh-keygen -R server_hostname

# 5. 連線被拒 vs timeout（Ch 6 的 RST vs timeout）
ssh -v user@server   # Connection refused = SSH 服務沒開/port 錯
                     # timeout = 防火牆擋/主機不通
```

> **SSH 的「權限太開放」和「host key 改變」是兩個經典問題，分別關乎安全機制和中間人警告**。**權限問題**：SSH 對金鑰檔案權限**非常嚴格**——私鑰必須 `600`（只有你能讀）、`~/.ssh` 必須 `700`，否則 SSH **拒絕使用**（報 "Permissions 0644 for 'id_ed25519' are too open"）。這是刻意的安全機制（防止其他使用者讀到你的私鑰）。新手常因為複製金鑰後權限不對而登入失敗——`chmod 600 私鑰` 解決。**host key 改變警告**（"REMOTE HOST IDENTIFICATION HAS CHANGED"）更重要——SSH 記住每個伺服器的 host key（`~/.ssh/known_hosts`），如果變了會大聲警告。這可能是**正常的**（伺服器重裝了）也可能是**中間人攻擊**（有人冒充伺服器）——所以要**確認原因**再決定是否 `ssh-keygen -R` 清掉舊記錄。這是 SSH 的「TOFU」（首次使用信任）模型——第一次連線記住 host key，之後變了就警告。debug SSH 用 `ssh -v`（看認證過程哪步失敗），區分 "Connection refused"（SSH 服務沒開，Ch 6 的 RST）和 timeout（防火牆擋/主機不通）。這些是 Part 8 管理 VPS 時天天遇到的，Ch 34 會深入。

## 動手練習

1. 產生金鑰：`ssh-keygen -t ed25519`，看產生的私鑰和公鑰，理解哪個能公開哪個要保密

2. 金鑰登入（有 VPS/另一台機器的話）：`ssh-copy-id` 部署公鑰，用金鑰登入，`ssh -v` 看認證過程

3. SSH tunnel：`ssh -D 1080 user@server` 建 SOCKS proxy，設瀏覽器用它，看流量經過 server

4. SFTP 傳檔：用 scp/sftp 傳檔案，對比明文 FTP，理解為什麼用加密的

5. 跑「故意弄壞」：故意把私鑰權限設成 644，看 SSH 拒絕；理解 host key 警告的意義

## 本章重點整理

- SSH 是加密的遠端終端機，和 TLS 共用密碼學原理（非對稱換鑰匙+對稱傳資料），但更重視驗證客戶端
- 金鑰認證（私鑰從不離開你的電腦）比密碼安全——生產環境用金鑰 + 關閉密碼登入（Ch 35）
- SSH tunnel（-L/-R/-D）能轉發任意流量：-L 訪問遠端內網、-D 當 SOCKS proxy（窮人的 VPN/翻牆）
- 其他協定：SMTP/IMAP（郵件）、SFTP/SCP（加密檔案傳輸）、WebSocket（雙向即時）；明文協定正被加密版淘汰
- SSH 排查：權限太開放（私鑰要 600）、host key 改變警告（重裝 or 中間人）、refused vs timeout

## 自我檢核

- [ ] 能解釋 SSH 金鑰認證的原理，以及為什麼比密碼安全
- [ ] 知道 SSH tunnel 的三種轉發，特別是 -D（SOCKS proxy）的用途
- [ ] 認得常見應用層協定（SMTP/IMAP/SFTP/WebSocket）和它們的 port
- [ ] 知道為什麼明文協定（telnet/FTP/HTTP）正被加密版取代
- [ ] 能排查 SSH 常見問題（權限、host key、refused vs timeout）

## 延伸閱讀

### 書籍

- **《SSH, The Secure Shell: The Definitive Guide》— Ch 2-3, 6** — Barrett, Silverman, Byrnes（O'Reilly）
  - **讀哪幾章**：Ch 2-3（SSH 基礎與認證）、Ch 6（金鑰管理）、Ch 9（port forwarding）
  - **這本書的定位**：SSH 的權威巨著；金鑰認證和 tunnel 的完整版
  - **前提**：Ch 11

### 文章

- **[SSH tunneling explained](https://goteleport.com/blog/ssh-tunneling-explained/)** — Teleport
  - **這篇說什麼**：SSH 三種 port forwarding 的圖解和實例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 SSH tunnel 那節的視覺化深入版

- **[SSH host key 與 TOFU](https://www.ssh.com/academy/ssh/host-key)** — SSH.com
  - **這篇說什麼**：host key 的作用、TOFU 模型、host key 改變的意義
  - **為什麼值得讀**：理解「故意弄壞」那節的 host key 警告

### 官方文件

- **[OpenSSH manual](https://www.openssh.com/manual.html)** — OpenSSH
  - **讀哪裡**：ssh(1) 的 -L/-R/-D 選項、ssh_config(5)
  - **為什麼值得讀**：SSH 所有選項的權威；Ch 34 會深入 ssh_config

Part 3（應用層）的章節到此完成。接下來是練習 A——用 Wireshark 完整解剖一次 HTTPS 連線，把 Ch 6（TCP）、Ch 9（DNS）、Ch 11（TLS）、Ch 10（HTTP）的知識綜合應用，親眼看完整的封包旅程。

→ [練習 A：用 Wireshark 解剖一次 HTTPS](./practice-a-https-wireshark.md)
