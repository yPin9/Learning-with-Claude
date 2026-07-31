# Ch 28 — HTTP proxy 與 SOCKS5

> **目標**：理解 proxy（代理）——它和 VPN 的差別（應用層 vs 網路層）、HTTP proxy（轉發 HTTP 請求）和 SOCKS5（轉發任意 TCP/UDP）的運作、forward proxy vs reverse proxy（兩種完全不同的東西）、以及 proxy 在「換 IP/跳板/翻牆」的角色。proxy 是比 VPN 更輕量的「換出口」方案，也是 Part 7 翻牆生態的基礎——Shadowsocks/V2Ray 本質都是「加密的 SOCKS proxy」。

> **環境**：Linux（ssh -D / curl --proxy）。

## 為什麼 proxy 是 VPN 之外的選擇？

Ch 27 說「VPN 不是萬靈丹，有時 proxy 就夠」。proxy 是「代理」——你不直接連目的地，而是請一個中間人（proxy）代替你連。它比 VPN 輕量（不用建隧道、不用改路由、可針對單一應用）。

理解 proxy 回答了幾個問題：proxy 和 VPN 到底差在哪？為什麼瀏覽器有「proxy 設定」？SSH 的 `-D`（Ch 12）建的 SOCKS proxy 是什麼？最重要的——Part 7 的翻牆工具（Shadowsocks/V2Ray）本質上都是「加密的 SOCKS proxy」，理解 proxy 是理解它們的基礎。這章也釐清一個常見混淆：forward proxy（你用來連外面）和 reverse proxy（伺服器前面擋著的，如 nginx，Ch 36）是兩種完全不同的東西。

## 先建立直覺:proxy 是代購

```
proxy = 代購（你不直接買，請人代買）

  直連：你 ──────▶ 目的地（網站）
        │
  proxy：你 ──▶ proxy ──▶ 目的地
              （代理替你連，再把結果轉給你）
        │
  目的地看到的是「proxy 的 IP」，不是你的
    → 換 IP / 隱藏真實位置 / 跳板
        │
  proxy vs VPN 的關鍵差別：
    VPN：在「網路層」攔截「所有」流量（改路由，Ch 23）
         系統級，所有 app 的流量都走 VPN
        │
    proxy：在「應用層」，「特定 app」設定用它
         瀏覽器設 proxy → 只有瀏覽器走 proxy
         其他 app 不受影響（除非各自設定）
        │
  → VPN 是「全車改道」，proxy 是「指定某些乘客搭專車」
    proxy 更輕量、更精準，但要 app 支援/設定
```

關鍵心智：proxy 是「代購」——你請中間人代替你連目的地，目的地看到 proxy 的 IP。和 VPN 的關鍵差別：**VPN 在網路層攔截所有流量（系統級，改路由）、proxy 在應用層由特定 app 設定使用**。VPN 是「全車改道」，proxy 是「指定某些乘客搭專車」。proxy 更輕量精準，但要 app 支援。

> proxy 和 VPN（Ch 23）都做「換出口」，但層次不同。SSH 的 `-D`（Ch 12）建的就是 SOCKS proxy。Part 7 的翻牆工具是「加密的 proxy」。如果對 VPN、SSH tunnel 不熟，回看 [Ch 23](./23-vpn-overview.md) 和 [Ch 12](./12-ssh-and-others.md)。

## HTTP proxy vs SOCKS5

```
兩種 proxy（轉發的層次不同）：

  HTTP proxy：專門轉發 HTTP/HTTPS
    理解 HTTP 協定（看得到/能改 HTTP 請求）
    用途：網頁快取、過濾、企業上網管控
    限制：只能 HTTP（其他協定不行）
    HTTPS 用 CONNECT 方法（建隧道，proxy 看不到加密內容）
        │
  SOCKS5 proxy：轉發「任意 TCP/UDP」
    不理解應用協定（只轉發 bytes）
    用途：任何 TCP/UDP 流量（HTTP、SSH、遊戲、任何東西）
    更通用（這就是為什麼翻牆工具用 SOCKS）
        │
  → HTTP proxy：懂 HTTP，只能 HTTP
    SOCKS5：不懂協定，但能轉發任何東西（更通用）
    翻牆工具（Shadowsocks/V2Ray）都提供 SOCKS5 介面
```

```bash
# === SOCKS5 proxy（用 SSH -D，Ch 12）===
ssh -D 1080 user@server          # 在本機 1080 建 SOCKS5 proxy
# 用 curl 透過它
curl --socks5 127.0.0.1:1080 https://ifconfig.me
# → 顯示 server 的 IP（流量經過 server 出去）

# === HTTP proxy ===
# 用 curl 透過 HTTP proxy
curl --proxy http://proxy-server:8080 https://example.com
# 設環境變數（很多工具會讀）
export http_proxy=http://proxy-server:8080
export https_proxy=http://proxy-server:8080
curl https://ifconfig.me         # 透過 proxy

# === 瀏覽器設 proxy ===
# 設定 → 網路 → proxy → SOCKS5 127.0.0.1:1080（搭配 ssh -D）
# → 只有瀏覽器的流量走 proxy
```

> **SOCKS5（轉發任意 TCP/UDP）比 HTTP proxy（只懂 HTTP）通用——這是翻牆工具都用 SOCKS5 的原因**。**HTTP proxy** 專門轉發 HTTP/HTTPS——它**理解** HTTP 協定（能看請求、做快取、過濾），用於企業上網管控、網頁快取。但它只能 HTTP（其他協定如 SSH、遊戲不行），且 HTTPS 時用 CONNECT 方法建隧道（proxy 看不到加密內容，只轉發）。**SOCKS5** 是「協定無關」的——它不理解應用協定，只**轉發 bytes**（任意 TCP/UDP），所以能代理**任何**流量（HTTP、SSH、遊戲、BT…）。這個通用性是 SOCKS5 的價值，也是為什麼**翻牆工具（Shadowsocks/V2Ray）都提供 SOCKS5 介面**——它們要代理你所有的流量，不只網頁。`ssh -D`（Ch 12）建的就是 SOCKS5 proxy（最簡單的「自架 proxy」）。實務上你設一個 SOCKS5 proxy（如 `ssh -D 1080`），讓瀏覽器或特定 app 用它——流量就經過 proxy 出去。理解 HTTP proxy（懂協定、只 HTTP）vs SOCKS5（不懂協定、通用）的差別，你就知道為什麼翻牆要 SOCKS5、企業上網管控用 HTTP proxy。

## Forward proxy vs Reverse proxy（重要的混淆）

這是 proxy 最常被混淆的概念——兩種完全不同的東西：

```
Forward proxy vs Reverse proxy（方向完全相反）：

  Forward proxy（正向代理）—— 替「客戶端」工作：
    客戶端 ──▶ [forward proxy] ──▶ 各種網站
    你（客戶端）用它連外面
    目的：換 IP、跳板、翻牆、企業上網管控
    「代理你去連別人」
        │
  Reverse proxy（反向代理）—— 替「伺服器」工作：
    各種客戶端 ──▶ [reverse proxy] ──▶ 後端伺服器們
    伺服器用它擋在前面（如 nginx，Ch 36）
    目的：負載平衡、TLS 終止、快取、隱藏後端
    「代理別人來連你」
        │
  → forward proxy 在「客戶端側」（你用它出去）
    reverse proxy 在「伺服器側」（擋在伺服器前面）
    兩者都叫 proxy 但方向和用途完全相反！
        │
  本章（翻牆相關）講 forward proxy
  Ch 36（nginx 部署）講 reverse proxy
```

> **forward proxy（替客戶端連外）和 reverse proxy（替伺服器擋前面）是兩種方向相反的東西——別混淆**。這是 proxy 最大的混淆源。**Forward proxy**（正向代理）站在**客戶端側**——你用它連外面的網站（換 IP、跳板、翻牆、企業管控上網）。它「代理你去連別人」，外面的網站看到 proxy 的 IP。SSH `-D`、Shadowsocks、企業上網 proxy 都是 forward proxy（本章和 Part 7 的主題）。**Reverse proxy**（反向代理）站在**伺服器側**——它擋在後端伺服器前面，接收外面來的請求再轉給後端（nginx 就是典型，Ch 36）。它「代理別人來連你」，外面的客戶端看到 reverse proxy 的 IP（不知道後端在哪），用於負載平衡、TLS 終止（在這裡解密 HTTPS）、快取、隱藏/保護後端。**方向完全相反**：forward proxy 你（客戶端）主動用它出去、reverse proxy 是伺服器擺在前面被動接收。但兩者都叫「proxy」（都是「中間轉發」），所以常混淆。記住：**forward = 客戶端側出去、reverse = 伺服器側進來**。本章講 forward proxy（連外/翻牆），Ch 36（部署）講 reverse proxy（nginx 擋在你的服務前面）。理解這個區別，你看到「proxy」時就知道是哪種。

## proxy 在翻牆的角色（預告 Part 7）

```
proxy 怎麼用於翻牆（Part 7 的基礎）：

  基本原理：
    你（被審查的網路內）──▶ proxy（牆外伺服器）──▶ 被封的網站
    proxy 在牆外，替你訪問被封的網站
        │
  但「裸 proxy」會被封：
    審查者能識別 proxy 流量（SOCKS5/HTTP proxy 有特徵）
    或封鎖 proxy 伺服器的 IP
        │
  所以翻牆需要「加密 + 偽裝的 proxy」：
    Shadowsocks（Ch 29）：加密的 SOCKS5（流量看起來像隨機 bytes）
    V2Ray/Xray（Ch 30）：更進階，能偽裝成正常 HTTPS 流量
        │
  → 翻牆工具 = SOCKS5 proxy + 加密 + 偽裝
    本章的 SOCKS5 是它們的「介面」（你的 app 連 SOCKS5）
    加密/偽裝是它們對抗審查的部分（Ch 29-31）
        │
  ssh -D 為什麼能翻牆又容易被封：
    它是加密的（SSH 加密）→ 能翻牆
    但 SSH 流量特徵明顯 → 易被識別封鎖（Ch 31）
```

> **翻牆工具本質是「SOCKS5 proxy + 加密 + 偽裝」——理解這個分解，Part 7 就有了框架**。翻牆的基本原理是 forward proxy——你連一個牆外的 proxy，它替你訪問被封的網站。但**裸 proxy 會被封**：審查者能識別 SOCKS5/HTTP proxy 的流量特徵，或封鎖 proxy 伺服器的 IP。所以翻牆需要在 proxy 上加兩層：**加密**（讓流量內容看不出是什麼）+ **偽裝**（讓流量「看起來像正常流量」，不被識別為 proxy）。**Shadowsocks**（Ch 29）是「加密的 SOCKS5」——流量加密成看似隨機的 bytes。**V2Ray/Xray**（Ch 30）更進階——能把流量偽裝成正常的 HTTPS（審查者難以區分）。所以這些工具的架構是：**SOCKS5 proxy 介面**（你的 app 連它，本章的內容）+ **加密層**（Ch 29）+ **偽裝層**（Ch 30）。`ssh -D` 為什麼能翻牆又易被封？它是加密的（SSH 加密 → 能翻牆），但 SSH 流量**特徵明顯**（審查者一看就知道是 SSH，易封鎖，Ch 31）——它有加密沒偽裝。理解這個分解（proxy + 加密 + 偽裝），你就懂了 Part 7 翻牆工具在解決什麼、各自的演進方向。本章的 SOCKS5 是基礎介面，Ch 29-31 是加密和偽裝的攻防。

## 故意弄壞:理解 proxy 的洩漏與限制

```bash
# proxy 的常見問題（理解它的限制）

# 1. DNS 洩漏（和 VPN 一樣的問題，Ch 9/23）
# 用 SOCKS5 proxy 但 DNS 沒走 proxy → 洩漏你查什麼
# curl --socks5 vs --socks5-hostname 的差別：
ssh -D 1080 user@server &
curl --socks5 127.0.0.1:1080 https://example.com         # DNS 可能在本地解析（洩漏）
curl --socks5-hostname 127.0.0.1:1080 https://example.com # DNS 也走 proxy（不洩漏）
#   → --socks5-hostname 讓域名解析也經過 proxy（重要！）

# 2. 只有「設定了 proxy 的 app」走 proxy
# 瀏覽器設了 proxy，但其他 app（系統更新、其他程式）沒走 → 流量分流
# → 這是 proxy vs VPN 的關鍵差別（VPN 全部走，proxy 只有設定的走）

# 3. proxy 本身能看到你的流量（除非端到端加密）
# proxy 是中間人 → 如果你連的是 HTTP（明文），proxy 看得到內容
# → 所以要嘛信任 proxy、要嘛用 HTTPS（端到端加密，proxy 只看到加密的）

# 4. 環境變數 proxy 不是所有工具都讀
echo $http_proxy                 # 設了，但不是所有程式都讀這個變數
```

> **`--socks5-hostname`（DNS 也走 proxy）vs `--socks5`（DNS 本地解析）是 proxy 防洩漏的關鍵差別**。proxy 和 VPN 一樣有 **DNS 洩漏**問題（Ch 9/23）——如果你的流量走 proxy 但**域名解析在本地做**，就洩漏了「你要訪問哪個域名」（即使連線內容走 proxy）。curl 的 `--socks5`（在本地解析 DNS 再連 proxy）vs `--socks5-hostname`（把域名交給 proxy 解析）——後者讓 **DNS 也走 proxy**，不洩漏。這是翻牆時的重要細節（DNS 洩漏會暴露你訪問被封網站，且本地 DNS 可能被污染，Ch 9/31）。其他限制：(1) **只有設定 proxy 的 app 走 proxy**（其他 app 流量分流，這是 proxy vs VPN 的核心差別——VPN 全走、proxy 選擇性走，各有好處）；(2) **proxy 能看到你的流量**（它是中間人，連明文 HTTP 時看得到內容——所以要信任 proxy 或用 HTTPS 端到端加密）；(3) **環境變數 proxy 不是所有工具都讀**（`http_proxy` 是慣例但非強制）。理解這些限制，你用 proxy 時就知道怎麼避免洩漏（用 `--socks5-hostname`、確認所有要保護的 app 都設了 proxy、用 HTTPS）。這些細節在翻牆場景（Part 7）特別重要——一個 DNS 洩漏可能暴露你的真實行為。

## 動手練習

1. 建 SOCKS proxy：用 `ssh -D 1080` 建一個，用 `curl --socks5` 透過它，看 IP 變成 server 的

2. HTTP vs SOCKS：理解 HTTP proxy（只 HTTP）和 SOCKS5（任意 TCP/UDP）的差別

3. forward vs reverse：畫出兩者的方向圖，說出各自的用途（連外 vs 擋在伺服器前）

4. DNS 洩漏：對比 `--socks5` 和 `--socks5-hostname`，理解 DNS 走不走 proxy 的差別

5. 思考翻牆：理解「翻牆工具 = SOCKS5 + 加密 + 偽裝」的分解，預告 Ch 29-31

## 本章重點整理

- proxy 是「代購」——中間人替你連目的地；vs VPN：proxy 在應用層（特定 app 設定）、VPN 在網路層（系統級全流量）
- HTTP proxy（懂 HTTP、只 HTTP、能快取過濾）vs SOCKS5（不懂協定、轉發任意 TCP/UDP、更通用）
- forward proxy（客戶端側，你用它連外/翻牆）vs reverse proxy（伺服器側，nginx 擋前面，Ch 36）——方向相反，別混淆
- 翻牆工具 = SOCKS5 proxy（介面）+ 加密（Ch 29）+ 偽裝（Ch 30）；ssh -D 有加密無偽裝（易被封）
- proxy 限制：DNS 洩漏（用 --socks5-hostname 防）、只有設定的 app 走、proxy 看得到明文流量

## 自我檢核

- [ ] 能說出 proxy 和 VPN 的核心差別（應用層 vs 網路層）
- [ ] 知道 HTTP proxy 和 SOCKS5 的差別，為什麼翻牆用 SOCKS5
- [ ] 能區分 forward proxy 和 reverse proxy（方向和用途）
- [ ] 理解「翻牆工具 = SOCKS5 + 加密 + 偽裝」的分解
- [ ] 知道 proxy 的 DNS 洩漏問題和怎麼防

## 延伸閱讀

### 文章

- **[Forward vs Reverse proxy](https://www.cloudflare.com/learning/cdn/glossary/reverse-proxy/)** — Cloudflare
  - **這篇說什麼**：清楚對比兩種 proxy 的方向和用途
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章「forward vs reverse」混淆的權威澄清

- **[SOCKS5 協定詳解](https://www.rfc-editor.org/rfc/rfc1928)** — RFC 1928
  - **讀哪裡**：協定流程那節
  - **為什麼值得讀**：SOCKS5 的權威定義，理解它怎麼轉發

### 工具

- **[SSH dynamic forwarding 完整指南](https://www.ssh.com/academy/ssh/tunneling/example)** — SSH.com
  - **這篇說什麼**：ssh -D 建 SOCKS proxy 的完整用法
  - **為什麼值得讀**：連接 Ch 12，理解最簡單的自架 proxy

下一章進入翻牆生態的第一個專門工具——Shadowsocks，理解它怎麼用「加密的 SOCKS proxy」對抗審查，以及它的設計和演進。

→ [Ch 29 Shadowsocks](./29-shadowsocks.md)
