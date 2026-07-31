# Ch 29 — Shadowsocks

> **目標**：理解 Shadowsocks 的技術原理——它怎麼用「加密的 SOCKS proxy」（Ch 28）對抗網路審查、為什麼它比 VPN/SSH tunnel 更難被識別、它的設計演進（從早期到 AEAD 加密）、以及它的優勢和限制。這章是理解「審查 vs 反審查」技術攻防的第一站，把 TCP/加密/流量特徵的知識推到實戰。

> **環境**：概念與技術原理為主。**本章以理解網路審查的技術原理為教育目的**，實際使用須遵守你所在地的法律。

## 本章的定位與倫理

網路審查（network censorship）和反審查（circumvention）是真實的技術領域，被學術界（如 USENIX/IEEE 的審查研究）、人權組織、和安全研究者廣泛研究。理解這個攻防——審查者怎麼識別和封鎖流量、工具怎麼對抗——能讓你深刻理解 TCP/TLS/流量分析的實際應用。

**本章是技術原理的教育**，不是「怎麼翻牆的操作手冊」。我們關注「為什麼 Shadowsocks 難被識別」這類**技術機制**，而非提供規避特定管制的操作步驟。實際使用任何工具須遵守你所在地的法律——不同地區對 VPN/proxy 的規範差異很大。理解技術 ≠ 鼓勵違法。這也是資安/網路工程師應有的素養：理解審查技術才能設計更好的隱私保護，理解反審查才能理解流量分析的極限。

## 為什麼需要 Shadowsocks？

Ch 28 說「翻牆工具 = SOCKS5 + 加密 + 偽裝」。VPN（WireGuard/OpenVPN）和 SSH tunnel 雖然加密，但**有明顯的流量特徵**——審查系統（如 DPI，深度封包檢測）能識別「這是 VPN/SSH 流量」並封鎖。Shadowsocks 的設計目標是：**讓代理流量「看起來像隨機的 bytes」，沒有可識別的特徵**。

Shadowsocks 是 2012 年中國程式設計師 clowwindy 開發的，專為對抗 GFW（Ch 31）的識別。它的核心洞察：不要試圖偽裝成「某種已知協定」（那會被針對），而是讓流量**沒有任何特徵**（看起來就是隨機加密的 bytes）。理解 Shadowsocks 讓你看到「流量識別 vs 流量隱藏」的攻防本質。

## 先建立直覺:沒有特徵就無法識別

```
Shadowsocks 的核心思想：讓流量「沒有特徵」

  審查系統（DPI）怎麼識別並封鎖流量：
    看流量的「特徵」（協定握手、固定的 bytes 模式、port...）
    認出「這是 VPN/SSH/某協定」→ 封鎖
        │
  VPN/SSH 的問題：有明顯特徵
    OpenVPN 有 OpenVPN 的握手特徵
    SSH 有 "SSH-2.0" 的明文開頭
    WireGuard 有 WireGuard 的封包格式
    → DPI 一看就認出 → 封鎖
        │
  Shadowsocks 的解法：沒有特徵
    流量加密成「看起來完全隨機的 bytes」
    沒有握手、沒有固定模式、沒有協定標識
    → DPI 看到的是「一堆隨機資料」，認不出是什麼
    → 難以用「特徵」來封鎖
        │
  → Shadowsocks = 加密的 SOCKS proxy，但「無特徵」
    審查者要封它，只能靠別的手段（IP 封鎖、主動探測）
```

關鍵心智：Shadowsocks 的核心思想是「**沒有特徵就無法用特徵識別**」。VPN/SSH 有明顯的協定特徵（DPI 一看就認出並封鎖），Shadowsocks 把流量加密成「看起來完全隨機的 bytes」——沒有握手、沒有固定模式、沒有協定標識，DPI 認不出它是什麼。它是「加密的 SOCKS proxy」（Ch 28），但設計成無特徵。

> Shadowsocks 是 Ch 28 的 SOCKS proxy + 加密的具體實現，專為對抗流量識別。它解決 VPN/SSH「有特徵易被封」的問題。如果對 SOCKS proxy、流量特徵不熟，回看 [Ch 28](./28-http-proxy-socks5.md)。Ch 31（GFW）會講審查者怎麼反制。

## Shadowsocks 的運作

```
Shadowsocks 的架構（client + server）：

  你的裝置（牆內）              Shadowsocks server（牆外）
    │                              │
  ss-local（本地 client）       ss-server
    提供 SOCKS5 介面給你的 app     │
    │                              │
  你的 app ──SOCKS5──▶ ss-local   │
                        │ 加密     │
                        ╞══加密流量══╡（看起來隨機的 bytes）
                        │          │ 解密
                        │       ss-server ──▶ 目的網站
        │
  流程：
    1. app 把流量交給 ss-local（透過 SOCKS5）
    2. ss-local 用「預共享密碼」加密
    3. 加密流量送到 ss-server（看起來隨機，無特徵）
    4. ss-server 解密 → 連目的網站
    5. 回應加密回來 → ss-local 解密 → 給 app
        │
  關鍵：client 和 server 共享一個「密碼」（對稱加密，Ch 11）
    比 VPN 簡單（沒有複雜握手/憑證）
```

```
Shadowsocks vs VPN/SSH 的識別難度：

  特徵            VPN/SSH         Shadowsocks
  協定握手        有（可識別）     無
  固定 bytes 模式 有              無（隨機）
  明文標識        SSH有"SSH-2.0"  無
  封包大小模式    可能有          可混淆
        │
  → Shadowsocks 的「無握手、無固定模式、全加密」
    讓 DPI 難以用特徵識別
    （但不是完全無法——後述限制和 Ch 31 的攻防）
```

> **Shadowsocks 用「預共享密碼 + 無握手的加密」做到無特徵——比 VPN 簡單卻更難識別**。架構很簡單：你的裝置跑 **ss-local**（提供 SOCKS5 介面給 app，Ch 28），牆外跑 **ss-server**。app 的流量交給 ss-local，用**預共享密碼**（對稱加密，Ch 11）加密成「看起來隨機的 bytes」送到 ss-server，server 解密後連目的網站。關鍵是它**沒有 VPN/TLS 那種複雜握手**——client 和 server 早就共享密碼，直接開始傳加密資料，所以**沒有可識別的握手特徵**。對比 VPN/SSH：它們有協定握手（可識別）、SSH 甚至有明文的 "SSH-2.0" 開頭（一眼認出）。Shadowsocks 的「無握手、無固定模式、全加密」讓 DPI 看到的只是「一堆隨機資料」，難以判斷「這是 Shadowsocks」。這個設計的巧妙在於——它不試圖偽裝成某個已知協定（那會被針對性破解），而是**追求「什麼都不像」**（隨機性）。代價是「隨機流量」本身在「大部分流量都是 HTTPS」的網路裡也可能顯得異常（後述限制）——這是 Ch 31 審查者反制的切入點。但 Shadowsocks 比 VPN 簡單（一個密碼，無憑證/握手）又更難識別，是反審查工具的重要里程碑。

## Shadowsocks 的演進:從早期到 AEAD

```
Shadowsocks 的加密演進（安全攻防的縮影）：

  早期（stream cipher，如 RC4/AES-CFB）：
    問題：沒有「完整性驗證」
    → 審查者能「主動探測」：改一個 bit 看 server 反應
    → 從反應推斷「這是 Shadowsocks server」（主動探測攻擊）
        │
  現代（AEAD，如 AES-GCM/ChaCha20-Poly1305）：
    AEAD = 認證加密（Authenticated Encryption with Associated Data）
    加密 + 完整性驗證一體（Ch 11 的現代密碼學）
    → 改任何 bit 都會驗證失敗 → 主動探測無效
    → 大幅提升抗探測能力
        │
  → Shadowsocks 從 stream cipher 升級到 AEAD
    是「審查者用主動探測攻擊 → 工具用 AEAD 防禦」的攻防結果
    現代 Shadowsocks 一律用 AEAD（舊的 stream cipher 已不安全）
```

> **Shadowsocks 從 stream cipher 升級到 AEAD，是「主動探測攻擊 vs 防禦」攻防的縮影——這展示了反審查是持續的軍備競賽**。早期 Shadowsocks 用 **stream cipher**（如 AES-CFB）——只加密、沒有完整性驗證。審查者發現了**主動探測攻擊**（active probing）：他們懷疑某 server 是 Shadowsocks，就主動連它、故意改封包的某個 bit，觀察 server 的反應——因為沒有完整性驗證，server 會「嘗試解密被改過的資料」並有特定反應（如解出亂碼後的行為），審查者從反應**確認「這是 Shadowsocks server」**，然後封鎖。這是 GFW（Ch 31）用過的真實手段。**解法是 AEAD**（認證加密，如 AES-GCM/ChaCha20-Poly1305，Ch 11 的現代密碼學）——它把加密和完整性驗證**一體化**，任何對封包的篡改都會導致驗證失敗，server 直接丟棄（不給任何可觀察的反應），主動探測就無效了。所以現代 Shadowsocks **一律用 AEAD**（舊的 stream cipher 已被認為不安全）。這個演進完美展示了**反審查是持續的軍備競賽**——審查者開發新的識別/攻擊手段（主動探測），工具開發新的防禦（AEAD），來回攻防。理解這個攻防，你會明白為什麼這領域的工具不斷演進（Ch 30 的 V2Ray 是下一步），以及密碼學的完整性驗證（AEAD）為什麼重要。這也是資安研究的真實縮影。

## Shadowsocks 的限制（為什麼有了 V2Ray）

```
Shadowsocks 的限制（推動 V2Ray/Xray 的誕生，Ch 30）：

  1. 「隨機流量」本身可能異常：
     大部分網路流量是 HTTPS（有 TLS 特徵）
     一堆「完全隨機」的流量反而顯眼（統計上異常）
     → 審查者能用「流量看起來太隨機」當線索
        │
  2. 主動探測的進階版：
     即使 AEAD，審查者能用流量模式、時序分析
        │
  3. 沒有「主動偽裝」：
     Shadowsocks 是「無特徵」，不是「偽裝成 HTTPS」
     無特徵 vs 有正常特徵 → 後者在某些情況更安全
        │
  4. server IP 一旦被識別就封鎖：
     不管多無特徵，IP 被封了就沒用（要換 IP）
        │
  → V2Ray/Xray（Ch 30）的改進：
    不只「無特徵」，而是「主動偽裝成正常 HTTPS」
    （混在真實 HTTPS 流量裡，更難封鎖）
```

> **Shadowsocks 的「無特徵」在「滿是 HTTPS 的網路」裡反而可能顯眼——這推動了 V2Ray「主動偽裝成 HTTPS」的演進**。Shadowsocks 追求「無特徵」（看起來隨機），但有個微妙問題：**現代網路的大部分流量是 HTTPS**（有 TLS 的特徵），如果你的流量是「完全隨機的 bytes」（既不是 HTTPS 也不是任何已知協定），這種「太隨機」本身在**統計上是異常的**——審查者能用「這個連線的流量看起來不像任何正常協定」當線索（雖然不能 100% 確定，但能標記為可疑）。加上進階的流量分析（封包大小模式、時序），「無特徵」不等於「完全隱形」。還有根本限制：**server IP 被識別就封鎖**（不管流量多無特徵，IP 封了就沒用）。這些限制推動了下一代工具 **V2Ray/Xray**（Ch 30）的核心改進——不只追求「無特徵」，而是**主動偽裝成正常 HTTPS**（把代理流量包在真實的 TLS 裡，混在大量正常 HTTPS 流量中，讓審查者難以區分「這是翻牆還是正常上網」）。這是策略的轉變：從「什麼都不像」（Shadowsocks）到「像最普通的 HTTPS」（V2Ray）。理解 Shadowsocks 的限制，你就懂了 Ch 30 V2Ray 為什麼那樣設計——它是攻防升級的下一步。Shadowsocks 仍廣泛使用（簡單、夠用於很多情況），但在最嚴格的審查下，V2Ray 的偽裝更穩。

## 故意弄壞:理解流量分析（從防禦者視角）

```
從「審查者/防禦者」視角理解流量識別（教育目的）：

  審查系統怎麼嘗試識別代理流量（理解攻防）：
        │
  1. 特徵匹配（signature）：
     找已知協定的特徵（OpenVPN 握手、SSH 開頭）
     → Shadowsocks 用「無特徵」對抗
        │
  2. 統計分析（statistical）：
     流量的熵（隨機程度）、封包大小分布、時序
     → 「太隨機」或「異常模式」可疑
        │
  3. 主動探測（active probing）：
     懷疑某 server，主動連它看反應
     → AEAD 加密對抗（改 bit 就驗證失敗，無反應）
        │
  4. IP 封鎖：
     識別出 server IP 就封
     → 換 IP / 用 CDN 隱藏（Ch 30）
        │
  → 理解這些識別手段，你才理解工具為什麼那樣設計
    這也是「設計隱私保護系統」需要的知識
    （知道怎麼被識別，才知道怎麼防）
```

> **理解審查者的識別手段（特徵/統計/主動探測/IP 封鎖），才能理解反審查工具的設計——這是隱私系統設計的必備知識**。從防禦者/研究者視角理解流量識別（教育目的，不是教你攻擊）：(1) **特徵匹配**——找已知協定的指紋（OpenVPN 握手、SSH 的明文開頭），Shadowsocks 用「無特徵」對抗；(2) **統計分析**——流量的熵（隨機程度）、封包大小分布、時序模式，「太隨機」或「規律的代理模式」可疑；(3) **主動探測**——懷疑某 server 就主動連它觀察反應，AEAD 加密用「篡改即驗證失敗、無反應」對抗；(4) **IP 封鎖**——識別出 server IP 直接封，工具用換 IP/CDN 隱藏對抗（Ch 30）。理解這些識別手段是**雙向有用**的：對反審查工具，知道「怎麼被識別」才知道「怎麼防」；對**設計隱私系統**（這才是工程師該關注的），理解流量分析的能力和極限，才能設計真正保護隱私的系統。這也是學術界（如普林斯頓、密西根的審查研究）和安全社群研究這領域的原因——不是為了規避特定管制，而是理解「流量在多大程度上能被分析和識別」這個基礎問題。Ch 31 會講 GFW 怎麼綜合運用這些手段，以及這場攻防的歷史。記住本章的倫理框架：理解技術原理，遵守當地法律。

## 動手練習

1. 理解架構：畫出 Shadowsocks 的 client（ss-local）+ server 架構，標出 SOCKS5 介面和加密流量在哪

2. 理解「無特徵」：說明為什麼 Shadowsocks 比 VPN/SSH 難用特徵識別

3. 理解 AEAD 演進：解釋主動探測攻擊，以及 AEAD 怎麼防禦它

4. 理解限制：說出 Shadowsocks 的限制，以及為什麼推動了 V2Ray

5. 防禦者視角：列出審查者識別代理流量的 4 種手段，思考設計隱私系統時怎麼防

## 本章重點整理

- Shadowsocks 核心思想：「沒有特徵就無法用特徵識別」——把流量加密成看似隨機的 bytes，無握手無固定模式
- 架構：ss-local（SOCKS5 介面 + 加密）+ ss-server（解密 + 連目的地），共享預共享密碼（對稱加密）
- 比 VPN/SSH 難識別：它們有協定握手特徵，Shadowsocks 無握手、全加密、無標識
- 演進到 AEAD（認證加密）對抗「主動探測攻擊」——展示反審查是持續的軍備競賽
- 限制：「太隨機」在滿是 HTTPS 的網路反而異常、IP 被封就沒用——推動 V2Ray「主動偽裝成 HTTPS」（Ch 30）

## 自我檢核

- [ ] 能解釋 Shadowsocks 的「無特徵」思想，以及為什麼比 VPN 難識別
- [ ] 理解 ss-local/ss-server 架構和它和 SOCKS proxy 的關係
- [ ] 知道主動探測攻擊和 AEAD 怎麼防禦它（攻防演進）
- [ ] 理解 Shadowsocks 的限制和為什麼推動了 V2Ray
- [ ] 從防禦者視角理解流量識別的手段（特徵/統計/主動探測/IP）

## 延伸閱讀

### 學術 / 技術

- **[How China Detects and Blocks Shadowsocks](https://gfw.report/publications/usenixsecurity23/en/)** — USENIX Security 2023
  - **核心貢獻**：學術研究 GFW 怎麼用主動探測+流量分析識別 Shadowsocks，第一手的攻防分析
  - **讀哪裡**：摘要 + 識別手段那節
  - **為什麼值得讀**：本章「審查者識別手段」的學術權威，理解真實的攻防

- **[Shadowsocks 官方文件](https://shadowsocks.org/doc/what-is-shadowsocks.html)** — Shadowsocks
  - **讀哪裡**：協定設計、AEAD 那部分
  - **為什麼值得讀**：Shadowsocks 設計的權威說明

### 文章

- **[Shadowsocks 的密碼學演進](https://github.com/shadowsocks/shadowsocks-org/issues/27)** — Shadowsocks 社群
  - **這篇說什麼**：為什麼從 stream cipher 換到 AEAD（主動探測攻擊的討論）
  - **為什麼值得讀**：本章「AEAD 演進」的第一手討論

### 研究領域

- **[Censored Planet](https://censoredplanet.org/)** — 密西根大學的全球審查觀測
  - **為什麼值得讀**：學術界怎麼研究網路審查，理解這是嚴肅的研究領域

下一章看更進階的 V2Ray/Xray——它怎麼用「主動偽裝成 HTTPS」把反審查推到新高度，理解流量偽裝的攻防巔峰。

→ [Ch 30 V2Ray / Xray](./30-v2ray-xray.md)
