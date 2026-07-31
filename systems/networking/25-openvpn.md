# Ch 25 — OpenVPN

> **目標**：理解 OpenVPN——較舊但仍廣用的 VPN，它怎麼用 TLS（Ch 11）做 VPN、和 WireGuard 的根本差異（用戶空間 vs kernel、TLS vs Noise、可設定 vs 固定）、為什麼有些場景還用它（成熟、靈活、能偽裝成 HTTPS）。理解 OpenVPN 讓你懂「VPN 的另一條技術路線」，也理解為什麼 WireGuard 是進步。這章不要求你精通 OpenVPN 設定（它複雜），重在理解原理和取捨。

> **環境**：Linux（openvpn）。概念為主，設定較 WireGuard 複雜。

## 為什麼還要學 OpenVPN？

WireGuard 這麼好（Ch 24），為什麼還要學 OpenVPN？因為 OpenVPN 仍廣泛存在——很多公司 VPN、商業 VPN、舊系統用它，它有 WireGuard 沒有的能力（能偽裝成 HTTPS 流量繞過封鎖、TLS 的成熟生態、更靈活的認證）。理解它，你才能在遇到時懂它、設定它、debug 它。

更重要的是，OpenVPN 代表「VPN 的另一條技術路線」——用 TLS（Ch 11）而非自訂協定、在用戶空間（Ch 21 的 tun 模型）而非 kernel。對比它和 WireGuard，你會深刻理解兩種設計哲學的取捨，以及「為什麼 WireGuard 是進步」。這章重在原理和對比，不要求精通設定（OpenVPN 設定確實複雜——這本身就是它和 WireGuard 的差異之一）。

## 先建立直覺:用 TLS 建 VPN

```
OpenVPN 的核心：用 TLS（Ch 11）建立 VPN 隧道

  WireGuard：自訂的 Noise 握手 + 固定密碼學（Ch 24）
        │
  OpenVPN：用「TLS」（就是 HTTPS 的那個 TLS，Ch 11）
    1. 用 TLS 握手建立加密通道（憑證認證，像 HTTPS）
    2. 在 TLS 通道裡傳 VPN 流量
    3. 跑在用戶空間（用 tun/tap，Ch 21）
        │
  好處（用 TLS 的優勢）：
    - 成熟（TLS 是久經考驗的協定）
    - 能用憑證/CA 認證（企業 PKI 整合）
    - 能跑在 TCP 443（偽裝成 HTTPS，繞過封鎖！Ch 31）
        │
  代價：
    - 複雜（TLS 握手 + 一堆設定選項）
    - 慢（用戶空間，封包要在 kernel/用戶空間之間複製）
    - 設定繁瑣（憑證、CA、一堆參數）
        │
  → OpenVPN = TLS + tun + 用戶空間
    用成熟的 TLS 換取靈活性，代價是複雜和慢
```

關鍵心智：OpenVPN 用 **TLS**（Ch 11，HTTPS 的那個 TLS）建立 VPN 隧道——TLS 握手（憑證認證）建加密通道，在通道裡傳 VPN 流量，跑在用戶空間（tun/tap，Ch 21）。好處是成熟、能用憑證/CA、能偽裝成 HTTPS（跑 TCP 443）。代價是複雜、慢（用戶空間）、設定繁瑣。

> OpenVPN 是 Ch 11（TLS）+ Ch 21（tun，用戶空間 VPN）的組合。對比 Ch 24（WireGuard）理解兩種路線。如果對 TLS 或 tun 不熟，回看 [Ch 11](./11-tls-https.md) 和 [Ch 21](./21-tun-tap.md)。

## OpenVPN vs WireGuard:根本差異

```
OpenVPN vs WireGuard 的根本對比：

  面向          OpenVPN              WireGuard
  協定基礎      TLS（成熟複雜）       Noise（簡單現代）
  執行位置      用戶空間             kernel
  程式碼量      ~100k 行             ~4000 行
  密碼學        可選（一堆套件）      固定（一套現代的）
  速度          較慢                 快
  設定          複雜（憑證/CA/參數）  簡單（幾行）
  認證          憑證/帳密/多種        公鑰
  偽裝          能（TCP 443 像HTTPS） 較難（UDP 特徵明顯）
  成熟度        20 年，久經考驗       2020 才主流
  漫遊          較弱                 強（換網路不斷）
        │
  → WireGuard 在「簡單/快/安全」全面領先
    OpenVPN 在「偽裝/成熟/靈活認證」還有優勢
```

```bash
# OpenVPN 的兩種傳輸模式（影響偽裝能力）
# UDP 模式：快，但 UDP 特徵明顯（易被封）
# TCP 443 模式：慢（TCP over TCP 問題），但「看起來像 HTTPS」（難被封）
#   → 翻牆場景常用 TCP 443（偽裝），Ch 31

# 看 OpenVPN 用 TLS（憑證認證）
# 它需要：CA 憑證、伺服器憑證+私鑰、客戶端憑證+私鑰、TLS 參數...
# （對比 WireGuard 只要公私鑰，複雜很多）
```

> **OpenVPN 在「簡單/快/安全」上全面輸給 WireGuard，但在「偽裝成 HTTPS」上仍有優勢——這是它在翻牆場景的價值**。對比清楚顯示 WireGuard 的進步：更簡單（4000 vs 100k 行）、更快（kernel vs 用戶空間）、更安全（固定現代密碼學 vs 可選一堆套件）、設定更易（公鑰 vs 憑證/CA）、漫遊更好。所以**新專案幾乎都該用 WireGuard**。但 OpenVPN 還有一個 WireGuard 難做的：**偽裝成 HTTPS**——OpenVPN 能跑在 **TCP port 443**，流量「看起來像正常 HTTPS」（都是 TCP 443 的 TLS），審查者難以區分「這是 OpenVPN 還是正常網頁」（Ch 31）。而 WireGuard 是 UDP、有明顯的協定特徵，容易被 DPI 識別和封鎖（Ch 31）。所以在**嚴格審查環境**（GFW 會封 WireGuard 的 UDP），OpenVPN 的 TCP 443 偽裝、或專門的翻牆工具（Ch 29-30）更有用。注意 OpenVPN 跑 TCP 有「**TCP over TCP**」問題——VPN 的 TCP 裡再跑應用的 TCP，兩層 TCP 的重傳/壅塞控制互相干擾，效能差（所以 OpenVPN 預設用 UDP，只在需要偽裝時用 TCP）。這個對比讓你理解：技術選擇看場景——一般用途 WireGuard，需要偽裝的審查環境可能要 OpenVPN/TCP 443 或專門工具。

## OpenVPN 的設定複雜性（為什麼 WireGuard 是解脫）

```
OpenVPN 設定需要的東西（對比 WireGuard 的幾行）：

  1. PKI（公鑰基礎設施）：
     - 建一個 CA（憑證頒發機構）
     - 用 CA 簽伺服器憑證
     - 用 CA 簽每個客戶端憑證
     （用 easy-rsa 工具，一堆步驟）
        │
  2. 伺服器設定（server.conf）：
     - 一堆參數：port, proto, dev, ca, cert, key, dh,
       cipher, auth, tls-auth, topology, server, push...
     （幾十行設定）
        │
  3. 客戶端設定（client.ovpn）：
     - 嵌入 CA 憑證、客戶端憑證+私鑰、TLS 參數...
        │
  → OpenVPN 的設定複雜度是 WireGuard 的數倍
    這不是缺點清單，是「為什麼 WireGuard 的極簡是進步」的證明
    （但複雜也帶來靈活——能做更多客製）
```

```bash
# OpenVPN 設定的大致流程（不展開，理解複雜度即可）
# sudo apt install openvpn easy-rsa
# 1. 建 PKI（easy-rsa）：建 CA、簽憑證...（多步驟）
# 2. 寫 server.conf（幾十個參數）
# 3. 為每個客戶端簽憑證、生成 .ovpn
# 4. 啟動：systemctl start openvpn@server
#
# 實務上多用自動化腳本（如 angristan/openvpn-install）
# 因為手動設定太繁瑣 —— 這本身說明了問題
```

> **OpenVPN 的設定複雜度（PKI/CA/憑證/幾十個參數）正是「為什麼 WireGuard 的極簡是進步」的活教材**。設定 OpenVPN 要：建立完整的 **PKI**（用 easy-rsa 建 CA、簽伺服器憑證、簽每個客戶端憑證——一堆步驟）、寫有幾十個參數的伺服器設定（cipher/auth/tls-auth/topology…，每個都可能設錯）、為每個客戶端生成嵌入憑證的 .ovpn。對比 WireGuard 的「產一對金鑰、寫幾行 conf」（Ch 24），差距巨大。實務上大家都用**自動化腳本**（如 angristan/openvpn-install）來架 OpenVPN——「需要腳本才能設定」本身就說明了它的複雜。這個複雜度有兩面：壞處是易出錯、難維護、學習曲線陡；好處是**靈活**（能做更精細的客製——多種認證方式、複雜的路由推送、企業 PKI 整合）。但對大多數用途，這種靈活是過度的——WireGuard 的「夠用的簡單」更好。理解 OpenVPN 的複雜，你會更欣賞 WireGuard 的設計，也理解「簡單是一種美德」（呼應 Ch 24 的極簡哲學）。如果你遇到要維護的 OpenVPN（公司舊系統），知道它的複雜結構（PKI + 參數）能幫你 debug。本課不要求你精通 OpenVPN 設定——理解它的原理、和 WireGuard 的對比、何時還用它，就夠了。

## 何時用 OpenVPN

```
什麼場景還該用 OpenVPN（而非 WireGuard）：

  1. 需要偽裝成 HTTPS 繞過封鎖（TCP 443）：
     嚴格審查環境，WireGuard 的 UDP 被封 → OpenVPN/TCP 443
        │
  2. 公司已有 OpenVPN 基礎設施 / PKI：
     已經建好 CA 和憑證系統 → 沿用
        │
  3. 需要複雜的認證（憑證 + 帳密 + MFA）：
     OpenVPN 支援多種認證組合，WireGuard 只有公鑰
        │
  4. 相容性需求（老舊客戶端/系統）：
     OpenVPN 20 年生態，幾乎所有平台都支援
        │
  5. 維護現有的 OpenVPN（不是新建）：
     已經在跑就繼續，不一定要遷移
        │
  → 新建一般 VPN：WireGuard
    需要偽裝/複雜認證/相容老系統/維護現有：OpenVPN
```

> **新建一般 VPN 用 WireGuard，但「偽裝需求、複雜認證、現有系統」這些場景 OpenVPN 仍有位置**。決策很清楚：**預設選 WireGuard**（簡單、快、安全，Ch 24）。但這些情況考慮 OpenVPN：(1) **嚴格審查環境需要偽裝**——WireGuard 的 UDP 易被 DPI 識別封鎖（Ch 31），OpenVPN 能跑 TCP 443 偽裝成 HTTPS（雖然更好的選擇是專門的翻牆工具，Ch 29-30）；(2) **公司已有 OpenVPN/PKI 基礎設施**——已經建好 CA 和憑證系統，沿用比遷移省事；(3) **需要複雜認證**——OpenVPN 支援憑證+帳密+MFA 等組合，WireGuard 只有公鑰（要額外搭配其他工具做進階認證）；(4) **相容老系統**——OpenVPN 20 年生態，幾乎所有平台都支援；(5) **維護現有的**——已經在跑就繼續。理解這些，你不會盲目地「WireGuard 就是最好，別的都不用」——技術選擇看場景。但對個人自架（本課重點，Part 8）和大多數新專案，WireGuard 是對的選擇。OpenVPN 是「過去的標準、現在的特定用途」——學它是為了理解 VPN 的演進和遇到時能應付，不是為了把它當主力。

## 動手練習

1. 理解對比：列出 OpenVPN 和 WireGuard 的 5 個關鍵差異，說出各自的優勢場景

2. 理解 TLS-based VPN：思考 OpenVPN 怎麼用 Ch 11 的 TLS 建隧道，和 HTTPS 的關係

3. 看設定複雜度：瀏覽一個 OpenVPN 的 server.conf 範例，數有多少參數，對比 WireGuard 的幾行

4.（選做）架一個：用自動化腳本（angristan/openvpn-install）架一個 OpenVPN，體會 vs WireGuard 的複雜度

5. 場景判斷：對幾個場景（個人翻牆、公司 VPN、嚴格審查環境、維護舊系統）判斷該用哪個

## 本章重點整理

- OpenVPN 用 TLS（Ch 11）建 VPN 隧道，跑在用戶空間（tun，Ch 21）——成熟但複雜慢
- vs WireGuard：OpenVPN 在簡單/快/安全全面落後，但在偽裝成 HTTPS（TCP 443）、複雜認證、成熟度有優勢
- OpenVPN 跑 TCP 443 能偽裝成 HTTPS 繞過封鎖（Ch 31），但有 TCP over TCP 效能問題
- 設定複雜（PKI/CA/憑證/幾十參數）是「WireGuard 極簡為何是進步」的活教材；實務多用自動化腳本
- 選擇：新建一般 VPN 用 WireGuard；偽裝/複雜認證/相容老系統/維護現有用 OpenVPN

## 自我檢核

- [ ] 能解釋 OpenVPN 怎麼用 TLS 建 VPN，和 WireGuard 的技術路線差異
- [ ] 知道 OpenVPN vs WireGuard 的關鍵取捨（簡單/快 vs 偽裝/靈活）
- [ ] 理解為什麼 OpenVPN 能偽裝成 HTTPS，以及 TCP over TCP 問題
- [ ] 知道 OpenVPN 的設定為什麼複雜，這說明了什麼
- [ ] 能判斷何時用 OpenVPN 而非 WireGuard

## 延伸閱讀

### 官方文件

- **[OpenVPN 官方文件](https://openvpn.net/community-resources/)** — OpenVPN
  - **讀哪裡**：How To 那篇（設定流程）
  - **為什麼值得讀**：OpenVPN 設定的權威，體會它的複雜度

### 文章

- **[WireGuard vs OpenVPN](https://www.wireguard.com/known-limitations/) + 各種對比文**
  - **這篇說什麼**：兩者的技術對比，WireGuard 的限制（誠實面）
  - **為什麼值得讀**：理解兩者取捨的平衡觀點

- **[OpenVPN 偽裝與翻牆](https://github.com/StreisandEffect/streisand)** — Streisand（多協定翻牆架設）
  - **這篇說什麼**：OpenVPN 在翻牆場景的偽裝設定
  - **為什麼值得讀**：連接 Ch 31（翻牆），理解 OpenVPN 的偽裝用途

### 工具

- **[angristan/openvpn-install](https://github.com/angristan/openvpn-install)** — 自動化 OpenVPN 安裝腳本
  - **為什麼值得讀**：如果要架 OpenVPN，這腳本省去手動 PKI 的繁瑣（也說明手動有多繁瑣）

下一章看 IPSec——另一種 VPN 路線（在 kernel、L3、標準協定族），理解它的用途（企業/site-to-site）和為什麼設定也複雜。

→ [Ch 26 IPSec](./26-ipsec.md)
