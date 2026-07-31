# Ch 17 — nmap / netcat / curl

> **目標**：掌握三個「探測與測試服務」的利器——nmap（端口掃描，看一台機器開了哪些服務）、netcat（萬用 TCP/UDP 工具，「網路的瑞士刀」）、curl（HTTP 客戶端，測試 web 服務）。這些讓你主動探測「對方開了什麼、能不能連、回什麼」，是測試服務、debug 連線、資安偵察的核心工具。Part 4 工具章收尾，這些工具會貫穿 Part 8 的 VPS 部署與測試。

> **環境**：Linux（nmap/ncat/curl）。nmap 掃描他人主機需授權（資安倫理）。

## 為什麼需要這三個工具？

前面的工具（ss/tcpdump/ping）主要是「觀察」。這章的三個工具讓你**主動探測和測試**——nmap 問「這台機器開了哪些 port/服務」、netcat 是「能連到任何 TCP/UDP port 並收發資料」的萬用工具、curl 是「發任意 HTTP 請求」測試 web 服務。

它們是測試和 debug 的主力：部署服務後用 nmap/nc 確認 port 真的開了、用 curl 測 API 回應對不對、用 nc 手動測試協定。它們也是資安偵察的基礎（nmap 是滲透測試的起手式）。掌握它們，你能主動驗證「服務到底有沒有正常運作」，而非被動猜測。

> **資安倫理提醒**：nmap 掃描**他人**的主機可能違法（未授權的掃描在很多地區是違規的）。本章的掃描範例都針對**你自己的機器/VPS**或明確授權的目標。學習掃描技術是為了理解和防護，不是攻擊。

## netcat:網路的瑞士刀

netcat（nc）是「萬用 TCP/UDP 工具」——能連、能聽、能傳資料：

```bash
# === 測試 port 通不通（最常用！）===
nc -zv example.com 443           # -z 只掃描不傳資料，-v 詳細
# Connection to example.com 443 port [tcp/https] succeeded!   ← 通
nc -zv example.com 12345         # 沒開的 port
# nc: connect to example.com port 12345 (tcp) failed: Connection refused

# === 掃多個 port ===
nc -zv example.com 80 443 22     # 掃幾個
nc -zv example.com 20-100        # 掃範圍（80-100）

# === 當伺服器（監聽）===
nc -l 9999                       # 監聽 9999（簡易伺服器）
# 另一端：echo "hi" | nc 127.0.0.1 9999  → 監聽端收到 "hi"

# === 傳檔案（簡易）===
# 接收端：nc -l 9999 > received.txt
# 傳送端：nc target 9999 < file.txt

# === 手動測試協定（HTTP）===
printf 'GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n' | nc example.com 80
# 手打 HTTP 請求看回應（Ch 10）

# === UDP 模式 ===
nc -u -zv example.com 53         # 測 UDP port（DNS）
```

> **`nc -zv host port` 是測試「port 通不通」的標準命令——比 ping 更準（測真實的 TCP 服務）**。回到 Ch 2/4 的重點：ping 測 ICMP（常被擋），而 `nc -zv` 測**真實的 TCP 連線**——它嘗試建立 TCP 握手（Ch 6）到目標 port，成功就是「服務在聽且通」，"Connection refused" 是「主機在但 port 沒服務」（Ch 6 的 RST），timeout 是「到不了或被 DROP」。這直接對應 Ch 6 的「refused vs timeout」診斷。`nc -zv` 是 debug「服務連不上」的核心——在客戶端 `nc -zv server 443` 測「我能連到服務嗎」，配合伺服器端 `ss -tlnp`（Ch 13，服務有在聽嗎），就能定位問題在哪端。netcat 還能當**簡易伺服器**（`nc -l port`）和**手打協定**（`printf '...' | nc`，測試 HTTP/SMTP 等文字協定，Ch 10/12）——這在沒有專門工具時很方便。注意 Linux 有幾個 netcat 變體（傳統 nc、ncat、OpenBSD nc），選項略有不同，`ncat`（nmap 的）功能最全。netcat 是「網路瑞士刀」——能連能聽能傳，是測試和 debug 的萬用工具。

## nmap:端口掃描

nmap 探測「一台機器開了哪些 port、跑什麼服務」：

```bash
# === 基本掃描（掃你自己的機器/VPS）===
nmap localhost                   # 掃本機常見 port
nmap 192.168.1.1                 # 掃一個 IP（你的路由器）
nmap example.com                 # 掃一台主機（只掃你有權的！）

# === 常用選項 ===
nmap -p 80,443,22 target         # 只掃特定 port
nmap -p 1-1000 target            # 掃 port 範圍
nmap -p- target                  # 掃所有 65535 個 port（慢）
nmap -sV target                  # 偵測服務版本（-sV，跑什麼軟體什麼版本）
nmap -sV -p 22 target            # 看 SSH 是哪個版本
nmap -O target                   # 偵測作業系統（-O，需 root）
nmap -A target                   # 全面（版本+OS+腳本，最詳細也最吵）

# === 掃描類型 ===
nmap -sS target                  # SYN 掃描（半開，較隱蔽，需 root）
nmap -sT target                  # TCP connect（完整握手，不需 root）
nmap -sU target                  # UDP 掃描（慢）
nmap -Pn target                  # 跳過 ping（目標擋 ICMP 時用）

# === 掃網段（找活著的主機）===
nmap -sn 192.168.1.0/24          # ping 掃描整個網段（看哪些 IP 活著）
```

```
nmap 端口狀態：
  open       port 開著，有服務在聽
  closed     port 關著（主機在，但這 port 沒服務）
  filtered   被防火牆過濾（連不到，可能 DROP）
        │
  → open = 有服務（可能是攻擊面）
    filtered = 防火牆擋著（看不出 open/closed）
    closed = 主機在但這 port 沒開
```

> **nmap 的 `-sV`（版本偵測）和端口狀態（open/filtered/closed）是它最有價值的功能——但掃描他人主機要授權**。nmap 掃描告訴你「目標開了哪些 port」，`-sV`（版本偵測）進一步告訴你「每個 port 跑什麼軟體什麼版本」（如 `22/tcp open ssh OpenSSH 8.9`）——這對**資安稽核**很重要（過時版本可能有已知漏洞）。端口狀態：**open**（有服務在聽，是攻擊面也是你要確認的服務）、**closed**（主機在但這 port 沒服務）、**filtered**（防火牆擋著，連不到，看不出 open/closed——這是防火牆 DROP 的效果，Ch 18）。掃描類型：`-sS`（SYN 半開掃描，較隱蔽，送 SYN 收到 SYN-ACK 就知道 open 但不完成握手，需 root）vs `-sT`（完整 TCP connect，不需 root 但較明顯）。`-Pn`（跳過 ping）在目標擋 ICMP 時必須。**用途**：部署服務後 `nmap your-vps` 確認「只開了該開的 port」（Ch 35 安全——多餘的 open port 是風險）、`nmap -sn 網段` 找活主機。**但務必只掃你有權的目標**——未授權掃描在很多地區違法，且會觸發對方的入侵偵測。nmap 是滲透測試的起手式，學它是為了理解攻擊面和做好防護（Ch 35）。

## curl:HTTP 客戶端

curl 是測試 web 服務的標準工具，前面用過很多，這裡系統化：

```bash
# === 基本 ===
curl https://example.com         # GET，印出 body
curl -I https://example.com      # 只要 header（-I = HEAD 請求）
curl -i https://example.com      # header + body
curl -v https://example.com      # 詳細（看請求/回應的完整交握，Ch 10）
curl -s https://example.com      # 安靜（不顯示進度，腳本用）

# === HTTP 方法與資料 ===
curl -X POST https://api.example.com/users \
     -H 'Content-Type: application/json' \
     -d '{"name":"alice"}'       # POST JSON（測 API）
curl -X PUT ... / -X DELETE ...  # 其他方法

# === 認證與標頭 ===
curl -H 'Authorization: Bearer TOKEN' https://api.example.com
curl -u user:pass https://example.com    # Basic auth
curl -b 'session=abc' https://example.com # 帶 cookie

# === 重導向、TLS、超時 ===
curl -L https://example.com      # 跟隨重導向（Ch 10 的 3xx）
curl -k https://self-signed.site # 跳過 TLS 驗證（危險！僅測試，Ch 11）
curl --max-time 10 https://slow.site     # 超時
curl --resolve example.com:443:1.2.3.4 https://example.com  # 自訂 DNS 解析（Ch 9 debug）

# === 下載 ===
curl -O https://example.com/file.zip     # 存成原檔名
curl -o myfile.zip https://example.com/file.zip   # 指定檔名

# === debug：看連線細節（時序分析）===
curl -w '@-' -o /dev/null -s https://example.com <<'EOF'
DNS:    %{time_namelookup}s
TCP:    %{time_connect}s
TLS:    %{time_appconnect}s
Total:  %{time_total}s
EOF
# → 看 DNS/TCP/TLS 各花多久（定位「慢在哪階段」，Ch 14）
```

> **`curl -v`（看完整交握）和 `curl -w`（時序分析）讓 curl 從「抓網頁」變成「debug HTTP 的利器」**。`curl -v` 顯示完整的請求-回應交握（`>` 你送的、`<` 伺服器回的、TLS 握手細節）——debug API/web 問題的核心（看到實際送了什麼標頭、收到什麼狀態碼，Ch 10）。`curl -w`（write-out）能輸出**時序分解**——`time_namelookup`（DNS 花多久）、`time_connect`（TCP 握手）、`time_appconnect`（TLS 握手）、`time_total`（總共）——這讓你定位「網站慢是慢在 DNS、TCP、TLS、還是伺服器處理」（對應 Ch 14 的「定位延遲在哪階段」，但不用抓封包）。`--resolve`（自訂 DNS 解析，Ch 9）讓你「繞過 DNS 直接連某 IP」——debug「是不是 DNS 問題」（域名失敗但 --resolve 成功=DNS 問題）。`-X POST -d`（送資料）測 REST API、`-H`（自訂標頭）帶認證 token、`-L`（跟隨重導向）。curl 是測試 web 服務、API、和 debug HTTP 的萬用工具，貫穿 Part 8（測試你部署的服務）。記住 `-v`（看交握）、`-w`（時序）、`--resolve`（繞過 DNS）這三個 debug 利器。

## 故意弄壞:綜合運用測試一個服務

```bash
# 部署服務後的完整驗證流程（Part 8 會這樣做）

# 啟動一個測試服務
python3 -m http.server 8080 --bind 0.0.0.0 &
SERVER_PID=$!
sleep 1

# 1. 伺服器端：服務在聽嗎？（Ch 13）
ss -tlnp | grep 8080
# LISTEN 0.0.0.0:8080 ...   ← 在聽，且是 0.0.0.0（對外）

# 2. 本機測 port 通不通（nc）
nc -zv localhost 8080
# Connection to localhost 8080 port succeeded!

# 3. nmap 確認（從「外部」視角看開了什麼）
nmap -p 8080 localhost
# 8080/tcp open  http-proxy

# 4. curl 測 HTTP 回應對不對
curl -sI http://localhost:8080 | head -1
# HTTP/1.0 200 OK   ← 服務正常回應

# 5. curl 看時序（健康的服務應該快）
curl -w 'Total: %{time_total}s\n' -o /dev/null -s http://localhost:8080

kill $SERVER_PID

# debug 流程：如果第 4 步失敗但第 2 步成功
#   → port 通（TCP OK）但 HTTP 錯 → 應用層問題（Ch 2 分層！）
# 如果第 2 步就失敗
#   → port 不通 → 服務沒在聽 或 防火牆擋（Ch 18）
```

> **「ss → nc → nmap → curl」是驗證一個服務的完整流程，對應 Ch 2 的分層排查**。部署服務後，這套流程確認服務真的正常：(1) `ss -tlnp`（伺服器端，服務在聽嗎？聽對位址嗎？Ch 13）；(2) `nc -zv`（TCP 層通嗎？Ch 6）；(3) `nmap`（從外部視角看開了什麼 port）；(4) `curl`（應用層 HTTP 回應對嗎？Ch 10）；(5) `curl -w`（夠快嗎？）。這完美對應 Ch 2 的「自底向上分層排查」——每一步驗證一層。關鍵的 debug 邏輯：如果 **nc 通（TCP OK）但 curl 錯（HTTP 失敗）** → 問題在應用層（服務跑著但回應錯誤，查應用 log）；如果 **nc 就不通** → TCP 層問題（服務沒在聽，或防火牆擋了，Ch 18）。這個流程是 Part 8 部署服務（Ch 36）後的標準驗證，也是練習 B（debug 五個問題）的核心方法。這些工具加上前面的 ss/tcpdump/ping/traceroute，組成了完整的網路 debug 武器庫——觀察（ss/tcpdump）、連通（ping/traceroute/mtr）、探測（nmap/nc/curl）。

## 動手練習

1. nc 測 port：用 `nc -zv` 測幾個網站的 80/443/22，理解 succeeded/refused/timeout 對應什麼

2. nc 當伺服器：一端 `nc -l 9999`、另一端連並傳資料，理解 netcat 的雙向通訊

3. nmap 掃自己：`nmap localhost` 和 `nmap -sV localhost` 看本機開了哪些服務、什麼版本

4. curl 測 API：用 `curl -X POST -d` 測 httpbin.org/post，用 `-v` 看完整交握

5. 跑「故意弄壞」：完整跑「ss→nc→nmap→curl」驗證流程，理解每步驗證哪一層

## 本章重點整理

- netcat（nc）是網路瑞士刀：`nc -zv host port` 測 TCP 連通（比 ping 準）、能當伺服器、能手打協定
- nmap 掃端口/服務版本：open（有服務）/closed（沒服務）/filtered（防火牆擋）；-sV 看版本；只掃有權的目標
- curl 是 HTTP 利器：`-v`（看交握）、`-w`（時序分析，定位慢在哪階段）、`--resolve`（繞過 DNS debug）、`-X/-d/-H`（測 API）
- 「ss→nc→nmap→curl」驗證流程對應 Ch 2 分層排查：nc 通但 curl 錯=應用層問題，nc 不通=TCP/防火牆問題
- 這些「探測工具」+ 前面的「觀察工具」（ss/tcpdump）+「連通工具」（ping/traceroute）= 完整 debug 武器庫

## 自我檢核

- [ ] 會用 `nc -zv` 測 port，理解結果對應 Ch 6 的 refused/timeout
- [ ] 知道 nmap 的端口狀態（open/closed/filtered）和版本偵測，以及掃描的倫理界線
- [ ] 會用 curl 的 -v/-w/--resolve/-X 測試和 debug web 服務
- [ ] 能用「ss→nc→nmap→curl」流程驗證一個服務，並定位問題在哪層
- [ ] 知道這些工具在整個 debug 武器庫中的角色

## 延伸閱讀

### 官方文件

- **[nmap 官方文件](https://nmap.org/book/man.html)** — nmap reference guide
  - **讀哪裡**：Port Scanning Techniques、Service Detection 那幾節
  - **為什麼值得讀**：nmap 所有功能的權威；理解各種掃描類型的原理

- **[curl 官方教學](https://curl.se/docs/tutorial.html)** — Daniel Stenberg
  - **讀哪裡**：整篇 + `curl --manual`
  - **為什麼值得讀**：curl 作者寫的，所有選項的權威

### 書籍 / 文章

- **[Everything curl](https://everything.curl.dev/)** — Daniel Stenberg（免費線上書）
  - **讀哪幾章**：HTTP、debug 那幾章
  - **這本書的定位**：curl 的完整指南，把 curl 的每個功能講透
  - **前提**：Ch 10-11

- **《Nmap Network Scanning》— Gordon Lyon（nmap 作者）**
  - **讀哪幾章**：Ch 3-5（掃描技術）、Ch 7（版本偵測）
  - **這本書的定位**：nmap 的權威巨著，資安偵察的經典

下一個是練習 B——綜合 Part 4 的所有工具，debug 五個真實的網路問題，把工具串成系統化的 debug 能力。

→ [練習 B：debug 五個網路問題](./practice-b-debug-5-problems.md)
