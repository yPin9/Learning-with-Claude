# Ch 24 — WireGuard

> **目標**：把 WireGuard 講透並能自架——它為什麼是現代最推薦的 VPN（極簡、在 kernel、現代密碼學、無握手狀態）、它的核心概念（peer/公私鑰/AllowedIPs/endpoint）、完整的設定、以及實務問題（NAT 穿透/MTU/DNS/全流量路由）。WireGuard 是 Ch 21（tun）+ Ch 11（加密）的最佳實現。完成這章你能架一個真正能用的 WireGuard VPN（練習 C 會完整做一遍）。

> **環境**：Linux（WireGuard，kernel 5.6+ 內建）。需 root。建議有 VPS（Part 8）做伺服器端。

## 為什麼 WireGuard 是現代 VPN 的首選？

VPN 領域長期被 OpenVPN（Ch 25）和 IPSec（Ch 26）主導，但它們複雜、龐大、難設定。**WireGuard**（2020 年併入 Linux kernel）改變了這一切——它只有約 4000 行程式碼（OpenVPN 是它的數十倍），跑在 kernel（快），用現代密碼學（固定一套，不像 OpenVPN 要選），設定極簡（一個 peer 幾行）。

WireGuard 的哲學是「**極簡**」——少即是多。少程式碼 = 少 bug = 易稽核（安全）。固定密碼學 = 不會選錯。無協商握手狀態 = 簡單可靠。它是 Linus Torvalds 都稱讚的設計。理解 WireGuard，你不只學會一個工具，還學到「好的安全軟體該怎麼設計」。這章把它的原理和設定講透，你會架出一個真正能用的 VPN。

## 先建立直覺:每個對端是一把公鑰

```
WireGuard 的核心模型：peer（對端）+ 公私鑰

  WireGuard 的世界裡，每一方都有一對金鑰（像 SSH，Ch 12）：
    私鑰（自己留）+ 公鑰（給對方）
        │
  設定一個連線 = 互換公鑰 + 告訴對方「你的 IP 範圍」
    我的設定裡：[Peer] 對方的公鑰 + 對方的 AllowedIPs + endpoint
    對方的設定裡：[Peer] 我的公鑰 + 我的 AllowedIPs
        │
  通訊時：
    用「對方的公鑰」加密、「我的私鑰」簽名（Ch 11 的非對稱）
    封包的目標 IP 在哪個 peer 的 AllowedIPs 裡 → 加密送給那個 peer
        │
  → WireGuard = 一組 peer，每個 peer 一把公鑰 + 一個 IP 範圍
    像 SSH 的金鑰認證，但用於建立加密隧道
    沒有「使用者/密碼」「憑證/CA」—— 就是公鑰，極簡
```

關鍵心智：WireGuard 的世界由 **peer（對端）** 組成，每個 peer 有一對金鑰（私鑰自己留、公鑰給對方，像 SSH，Ch 12）。設定一個連線就是「互換公鑰 + 告訴對方你的 IP 範圍（AllowedIPs）」。通訊時用對方公鑰加密。沒有使用者/密碼、沒有憑證/CA——就是公鑰，極簡。

> WireGuard 是 Ch 21（tun）+ Ch 11（加密）+ Ch 12（公鑰認證概念）的綜合。它建立一個 `wg0` 介面（是 tun 類型），用公私鑰做認證和加密。如果對這些不熟，回看對應章節。

## WireGuard 的設計哲學

```
WireGuard 為什麼這麼好（對比 OpenVPN/IPSec）：

  1. 極簡（~4000 行 vs OpenVPN ~100k 行）：
     少程式碼 = 少 bug = 易稽核 = 更安全
        │
  2. 在 kernel（Linux 5.6+ 內建）：
     不用在用戶空間和 kernel 之間複製封包 → 快
        │
  3. 固定的現代密碼學（不可選）：
     Curve25519, ChaCha20, Poly1305, BLAKE2s
     → 不像 OpenVPN 要選一堆套件（選錯就不安全）
        │
  4. 無連線狀態的握手（stateless）：
     不像 TLS 的複雜握手狀態機
     → 簡單、可靠、抗 DoS
        │
  5. 「Cryptokey Routing」：
     公鑰直接對應 IP 範圍（AllowedIPs）
     → 認證和路由合一，優雅
        │
  6. 漫遊（roaming）：
     換網路（WiFi→4G）連線不斷（記住 peer 的公鑰，endpoint 自動更新）
        │
  → WireGuard 的「少即是多」哲學是現代安全軟體的典範
```

> **WireGuard 的「極簡」不只是少打字，而是「少程式碼=少 bug=易稽核=更安全」的深刻設計哲學**。WireGuard 約 4000 行（OpenVPN 數十萬行）——這意味著安全研究者能**完整稽核**整個程式碼（找出所有潛在漏洞），而龐大的 OpenVPN/IPSec 幾乎不可能完整稽核。**固定密碼學**（Curve25519/ChaCha20/Poly1305）是另一個關鍵決定——不像 OpenVPN 讓你選一堆加密套件（選錯就不安全，或為了相容保留弱套件），WireGuard 直接定死一套現代的，沒有選擇就沒有選錯。**在 kernel**（Linux 5.6+ 內建）讓它快（不用用戶空間/kernel 之間複製封包，對比 Ch 21 的 OpenVPN 用戶空間模型）。**Cryptokey Routing**（公鑰直接對應 AllowedIPs）優雅地把「認證」和「路由」合一——一個 peer 的公鑰決定了「它能用哪些 IP」。**漫遊**（換網路不斷線）讓它在行動裝置上體驗極佳。這些設計讓 WireGuard 成為現代 VPN 的首選，連 Linus Torvalds 都罕見地公開稱讚。理解這個「少即是多」哲學，不只幫你選 VPN，也是學習「好的安全軟體該怎麼設計」的範例。

## WireGuard 設定:伺服器 + 客戶端

```bash
# === 安裝 ===
sudo apt install wireguard

# === 1. 產生金鑰對（伺服器和客戶端各一對）===
wg genkey | tee server_private.key | wg pubkey > server_public.key
wg genkey | tee client_private.key | wg pubkey > client_public.key
# genkey 產私鑰，pubkey 從私鑰算出公鑰（像 SSH，Ch 12）

# === 2. 伺服器設定 /etc/wireguard/wg0.conf ===
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.66.66.1/24                 # VPN 內網的伺服器 IP
ListenPort = 51820                      # WireGuard 監聽的 UDP port
PrivateKey = $(cat server_private.key)  # 伺服器私鑰
# NAT：讓客戶端的流量能出外網（Ch 18 的 MASQUERADE）
PostUp = iptables -t nat -A POSTROUTING -s 10.66.66.0/24 -o eth0 -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -s 10.66.66.0/24 -o eth0 -j MASQUERADE

[Peer]
PublicKey = $(cat client_public.key)    # 客戶端的公鑰
AllowedIPs = 10.66.66.2/32              # 這個客戶端在 VPN 的 IP
EOF

# 開啟轉發（Ch 0/18）+ 啟動
sudo sysctl -w net.ipv4.ip_forward=1
sudo wg-quick up wg0                     # 啟動 VPN（建 wg0 介面）
sudo systemctl enable wg-quick@wg0       # 開機自啟（Ch 31）

# === 3. 客戶端設定 ===
# [Interface]
# Address = 10.66.66.2/24                # 客戶端在 VPN 的 IP
# PrivateKey = <client_private.key>
# DNS = 1.1.1.1                          # 用 VPN 的 DNS（防 DNS 洩漏，Ch 9/23）
#
# [Peer]
# PublicKey = <server_public.key>        # 伺服器公鑰
# Endpoint = <伺服器公網IP>:51820         # 伺服器在哪
# AllowedIPs = 0.0.0.0/0                 # 全流量走 VPN（或只走特定網段）
# PersistentKeepalive = 25               # NAT 後面要開（餵活 NAT 表，Ch 8）

# === 查看狀態 ===
sudo wg                                  # 看 peer、握手時間、傳輸量
sudo wg show wg0
```

> **WireGuard 設定的核心是「AllowedIPs」——它同時是「路由」和「存取控制」，是最易誤解的設定**。`AllowedIPs` 有雙重意義：(1) **出向（路由）**——「目標 IP 在這個範圍的封包，加密送給這個 peer」。客戶端設 `AllowedIPs = 0.0.0.0/0` = 「所有流量都走 VPN」（全流量 VPN）；設 `AllowedIPs = 10.66.66.0/24` = 「只有 VPN 內網流量走 VPN」（split tunnel，其他走原本網路）。(2) **入向（存取控制）**——「只接受來自這個 peer、且來源 IP 在這範圍的封包」。伺服器為每個客戶端設 `AllowedIPs = 10.66.66.X/32`（那個客戶端的 VPN IP）。這個「Cryptokey Routing」把路由和認證合一是 WireGuard 的優雅之處，但也是新手最困惑的——「為什麼我設了 VPN 卻只有部分流量走」往往是 AllowedIPs 設太窄。其他關鍵設定：伺服器的 **PostUp MASQUERADE**（Ch 18，讓客戶端流量出外網——漏了就「連上但沒網」，Ch 23 的通用問題）、客戶端的 **DNS**（防 DNS 洩漏，Ch 9/23）、**PersistentKeepalive**（NAT 後面要開，餵活 NAT 表防斷線，Ch 8）。`wg-quick up` 是方便的封裝（讀 conf 自動建介面、設路由、跑 PostUp）。

## 抓封包看 WireGuard 的雙層（Ch 21 的驗證）

```bash
# WireGuard 連上後，驗證 Ch 21 的「雙層封包」
# 在 wg0 介面抓 → 看明文（VPN 內部）
sudo tcpdump -i wg0 -n &
curl -s https://example.com > /dev/null   # 透過 VPN 訪問
# 看到：明文的 IP 封包（10.66.66.2 → example.com）—— VPN 內層
sudo pkill tcpdump

# 在實體介面抓 → 看加密的 UDP（隧道）
sudo tcpdump -i eth0 -n udp port 51820 &
curl -s https://example.com > /dev/null
# 看到：你 ↔ 伺服器的加密 UDP（看不到內容）—— VPN 外層
sudo pkill tcpdump

# → 同一個流量，wg0 看明文、eth0 看加密 UDP
#   這正是 Ch 21 的「雙層封包」，VPN 隱私的原理

# 看握手和傳輸統計
sudo wg
# peer: <公鑰>
#   endpoint: <對方IP>:51820
#   latest handshake: 30 seconds ago    ← 有握手 = 連線正常
#   transfer: 1.2 MiB received, 800 KiB sent
```

> **WireGuard 的握手是「無狀態」且每 ~2 分鐘自動換金鑰——這是它簡單又安全的關鍵**。對比 TLS（Ch 11）的複雜握手狀態機，WireGuard 用 **Noise 協定框架**做一個極簡的 1-RTT 握手——交換臨時金鑰、建立加密 session，每約 2 分鐘自動重新握手（換新的臨時金鑰，提供前向保密，Ch 11）。`wg` 命令的 `latest handshake` 告訴你連線健康（有近期握手 = 正常；很久沒握手 = 連線可能斷了）。WireGuard 的另一個巧妙設計是**「沉默」**——沒有流量時不發任何封包（不像有些 VPN 一直 keepalive），這讓它「隱形」（沒在用時偵測不到，對隱私和省電好）。但在 NAT 後面這造成問題——NAT 表會因沒流量而過期（Ch 8），所以要 `PersistentKeepalive`（定期發小封包餵活 NAT）。前面驗證了 Ch 21 的雙層封包（wg0 看明文、eth0 看加密 UDP）——這是理解 VPN 隱私的關鍵實驗，建議親手抓一次。WireGuard 的這些設計（無狀態握手、自動換金鑰、沉默、漫遊）讓它既簡單又安全又好用，是現代密碼工程的典範。

## 故意弄壞:WireGuard 的常見問題

```bash
# WireGuard 連不上/沒網的排查（綜合前面所學）

# 問題 1：連上但沒網（最常見）—— 忘了 NAT/forward（Ch 18/23）
sudo wg                                  # 有握手嗎？（有 = VPN 通，問題在出網）
cat /proc/sys/net/ipv4/ip_forward        # 1 嗎？（0 = 沒開轉發）
sudo iptables -t nat -L POSTROUTING -n   # 有 MASQUERADE 嗎？
# → 沒網 = 通常是 ip_forward 沒開 或 MASQUERADE 沒設

# 問題 2：握手失敗（連不上）
# latest handshake 一直沒有 → 排查：
# - Endpoint IP/port 對嗎？（伺服器公網 IP + 51820）
# - 伺服器防火牆開了 UDP 51820 嗎？（Ch 18，常忘）
sudo ufw allow 51820/udp                 # 或 iptables 開 UDP 51820
# - 公私鑰配對嗎？（伺服器的 [Peer] 是客戶端公鑰，反之亦然）

# 問題 3：傳大檔案卡住（MTU，Ch 4/23）
# WireGuard 封裝多一層，有效 MTU 變小（預設 wg 介面 MTU 1420）
# → 如果還卡，調更小：客戶端 [Interface] MTU = 1380
# 或伺服器做 MSS clamping（Ch 18）

# 問題 4：DNS 洩漏（Ch 9/23）
# 流量走 VPN 但 DNS 沒走 → 設客戶端 [Interface] DNS = 1.1.1.1
# 驗證：連 VPN 後 curl ifconfig.me（看是不是 VPN 的 IP）+ dnsleaktest.com

# 問題 5：NAT 後面斷線（Ch 8）
# 沒有 PersistentKeepalive → 閒置後 NAT 表過期 → 斷線
# → 客戶端 [Peer] PersistentKeepalive = 25
```

> **WireGuard 的問題排查是 Ch 23 通用問題的具體化——「有握手但沒網」最常見，根因是 NAT/forward**。系統排查：先 `sudo wg` 看 **latest handshake**——**有握手** = VPN 隧道通了，問題在「出網」（檢查 `ip_forward` 和 MASQUERADE，Ch 18/23，這是「連上但沒網」的頭號原因）；**沒握手** = 連不上，檢查 Endpoint（伺服器 IP/port 對嗎）、**伺服器防火牆有沒有開 UDP 51820**（超常忘！VPS 預設防火牆會擋，Ch 18/35）、公私鑰配對（伺服器的 [Peer] 放客戶端公鑰，反之亦然——放錯是新手常錯）。**傳大檔案卡住** = MTU（Ch 4，WireGuard 預設 MTU 1420，還卡就調更小如 1380）。**DNS 洩漏** = 設客戶端 DNS（用 dnsleaktest.com 驗證）。**閒置斷線** = NAT 後面要 PersistentKeepalive（Ch 8）。驗證 VPN 真的生效：連上後 `curl ifconfig.me` 應顯示**伺服器的 IP**（不是你的）。這些排查把前面 Part 2-5 的知識全用上了——這正是「VPN 是綜合應用」的體現。練習 C 會讓你完整架一遍並解決這些問題。掌握這個排查清單，你架 WireGuard 不會卡死在某個問題上。

## 動手練習

1. 產生金鑰：用 `wg genkey`/`wg pubkey` 產生金鑰對，理解公私鑰（像 SSH，Ch 12）

2. 看設定結構：理解 [Interface]（自己）和 [Peer]（對方）的每個欄位，特別是 AllowedIPs 的雙重意義

3. 架一個（練習 C 完整版）：有 VPS 的話，跟著設定架一個 WireGuard，用 `wg` 看握手

4. 驗證雙層：連上後在 wg0 和 eth0 各抓封包，看明文 vs 加密 UDP（Ch 21 的驗證）

5. 跑「故意弄壞」：故意漏 MASQUERADE（連上沒網）、漏防火牆規則（連不上），體驗排查

## 本章重點整理

- WireGuard 是現代首選 VPN：極簡（~4000 行，易稽核）、在 kernel（快）、固定現代密碼學（不會選錯）、無狀態握手
- 模型：peer + 公私鑰（像 SSH）；設定 = 互換公鑰 + AllowedIPs；沒有使用者/密碼/憑證
- AllowedIPs 雙重意義：出向是路由（0.0.0.0/0=全流量，特定網段=split tunnel）、入向是存取控制
- 關鍵設定：伺服器 MASQUERADE（出網）、客戶端 DNS（防洩漏）、PersistentKeepalive（NAT 後防斷）
- 排查：有握手但沒網=NAT/forward；沒握手=Endpoint/防火牆/金鑰；卡住=MTU；洩漏=DNS——全是 Part 2-5 知識

## 自我檢核

- [ ] 能解釋 WireGuard 為什麼比 OpenVPN/IPSec 好（極簡哲學）
- [ ] 理解 peer/公私鑰模型，AllowedIPs 的雙重意義
- [ ] 能設定一個基本的 WireGuard 伺服器+客戶端
- [ ] 會驗證雙層封包（wg0 明文 vs eth0 加密）
- [ ] 能排查「連上沒網」「連不上」「卡住」「DNS 洩漏」等常見問題

## 延伸閱讀

### 必讀資源

- **[WireGuard 官方白皮書](https://www.wireguard.com/papers/wireguard.pdf)** — Jason Donenfeld
  - **核心貢獻**：WireGuard 作者親述設計理念、Cryptokey Routing、Noise 握手
  - **讀哪裡**：Section 1-2（設計哲學）、Section 5（Cryptokey Routing）
  - **為什麼值得讀**：理解 WireGuard「為什麼這樣設計」的第一手資料

- **[WireGuard 官網 Quick Start](https://www.wireguard.com/quickstart/)** — WireGuard
  - **讀哪裡**：整個 quickstart
  - **為什麼值得讀**：官方的設定教學，最權威

### 文章

- **[Unofficial WireGuard 文件](https://github.com/pirate/wireguard-docs)** — Nick Sweeting
  - **這篇說什麼**：把 WireGuard 的每個設定欄位（特別是 AllowedIPs）講到極致
  - **讀哪裡**：AllowedIPs、PersistentKeepalive 那幾節
  - **為什麼值得讀**：本章設定的完整版，AllowedIPs 雙重意義的權威解釋

### 工具

- **[wg-easy](https://github.com/wg-easy/wg-easy)** — WireGuard 的 Web UI
  - **為什麼值得讀**：自架 WireGuard 的圖形化管理工具，練習 C 的延伸

下一章看 OpenVPN——較舊但仍廣用的 VPN，理解它和 WireGuard 的差異，以及為什麼有些場景還用它。

→ [Ch 25 OpenVPN](./25-openvpn.md)
