# Ch 26 — IPSec

> **目標**：理解 IPSec——網路層的 VPN 標準協定族，它怎麼在 IP 層加密（不是用 tun，而是直接改 IP 封包）、它的組成（IKE 握手 + ESP/AH 封裝）、為什麼企業和 site-to-site 場景常用它、以及為什麼它設定複雜。IPSec 是第三條 VPN 路線（vs WireGuard 的 Noise、OpenVPN 的 TLS），理解它你就掌握了 VPN 的三大技術路線。這章重在原理和定位，不要求精通設定。

> **環境**：Linux（strongSwan/Libreswan）。概念為主，IPSec 設定複雜。

## 為什麼要懂 IPSec？

IPSec 是最老、最標準化的 VPN 技術——它是 IETF 的標準協定族（不是某個產品），被內建在幾乎所有作業系統和網路設備（路由器、防火牆）。企業 VPN、site-to-site VPN（連接兩個辦公室/雲端 VPC）、行動裝置 VPN（iOS/Android 內建支援）大量用它。

理解 IPSec 補完了 VPN 的三大技術路線：**WireGuard**（Noise，新穎極簡）、**OpenVPN**（TLS，用戶空間）、**IPSec**（網路層標準，企業級）。IPSec 的獨特之處是它工作在**網路層**（直接加密 IP 封包，不像 OpenVPN 用 tun）——這讓它高效但也複雜。理解它，你能應付企業環境的 VPN、理解為什麼它複雜、知道何時它是對的選擇。這章重在原理和定位，不深入設定（IPSec 設定的複雜是出了名的）。

## 先建立直覺:在 IP 層動手術

```
IPSec 的獨特之處：直接在「IP 層」加密（不用 tun）

  WireGuard/OpenVPN：用 tun 介面（Ch 21）
    封包進 tun → 用戶/kernel 程式加密 → 包成新封包送出
        │
  IPSec：直接「改造 IP 封包」（在 kernel 的 IP 層）
    不需要 tun 介面
    封包經過 IP 層時，kernel 的 IPSec 直接加密/封裝它
        │
  兩種模式：
    傳輸模式（transport）：只加密 IP 封包的「內容」（payload）
      保留原 IP 標頭 → 用於「主機到主機」
        │
    隧道模式（tunnel）：把「整個 IP 封包」加密 + 包進新 IP 封包
      → 用於「網路到網路」（site-to-site）、remote access
        │
  → IPSec 在網路層運作（高效），是 IP 協定的「官方加密擴充」
    這也是為什麼它複雜——它是 IP 層的標準，要相容各種情況
```

關鍵心智：IPSec 工作在**網路層**——它直接加密/封裝 IP 封包（不用 tun 介面，不像 WireGuard/OpenVPN）。兩種模式：傳輸模式（只加密 payload，主機到主機）、隧道模式（加密整個 IP 封包再包一層，網路到網路）。它是 IP 協定的「官方加密擴充」，由 IETF 標準化。

> IPSec 工作在 Ch 4 的網路層（IP）。它和 WireGuard（Ch 24）、OpenVPN（Ch 25）都做 VPN，但路線不同——IPSec 在 IP 層、那兩個用 tun。如果對 IP 層、封裝不熟，回看 [Ch 4](./04-network-layer-ip-icmp.md) 和 [Ch 2](./02-osi-tcpip-models.md)。

## IPSec 的組成:IKE + ESP/AH

```
IPSec 不是「一個協定」，是一族協定的組合：

  1. IKE（Internet Key Exchange）—— 握手與金鑰交換：
     IKEv1（舊）/ IKEv2（新，推薦）
     負責：認證雙方、協商加密參數、交換金鑰
     （像 TLS 握手的角色，但在 IP 層）
        │
  2. ESP（Encapsulating Security Payload）—— 加密封裝：
     加密 + 認證封包內容
     這是「實際加密資料」的部分（最常用）
        │
  3. AH（Authentication Header）—— 只認證不加密：
     只驗證完整性，不加密（很少單獨用）
        │
  組合運作：
    IKE 先建立「安全關聯（SA）」（協商好的加密參數+金鑰）
    然後 ESP 用這些參數加密實際流量
        │
  → IPSec = IKE（握手/金鑰）+ ESP（加密資料）
    這個「多協定組合」是它複雜的根源之一
```

```bash
# Linux 的 IPSec 實作（strongSwan 是常用的）
# sudo apt install strongswan

# IPSec 的設定涉及（理解複雜度）：
# - IKE 版本和參數（加密/雜湊/DH group...）
# - 認證方式（PSK 預共享金鑰 / 憑證 / EAP）
# - SA（安全關聯）的參數
# - 兩端的子網（site-to-site）
# - 一堆 phase1/phase2 的協商參數
#
# 看 IPSec 的狀態
# sudo ipsec status
# sudo swanctl --list-sas    # 看安全關聯（SA）
```

> **IPSec 是「一族協定的組合」（IKE + ESP/AH），這個多協定架構是它強大也是它複雜的根源**。IPSec 不是單一協定，而是：**IKE**（網際金鑰交換，做握手/認證/金鑰協商，角色像 TLS 握手但在 IP 層，IKEv2 是現代版）+ **ESP**（封裝安全載荷，做實際的加密封裝，最常用）+ **AH**（認證標頭，只驗證不加密，少用）。運作流程：IKE 先建立「**安全關聯（SA）**」（雙方協商好的加密參數和金鑰），ESP 再用這些參數加密實際流量。這個「分階段、多協定」的設計讓 IPSec **靈活且標準化**（能適應各種場景、各種設備互通），但也**複雜**——設定要協商一堆參數（IKE 版本、加密演算法、DH group、認證方式、SA 生命週期、phase1/phase2…），兩端的參數必須匹配（不匹配就協商失敗，且錯誤訊息常很隱晦）。這是 IPSec「難設定、難 debug」的名聲來源。對比 WireGuard 的「固定一套、無協商」（Ch 24），IPSec 的可協商性是雙刃劍——相容性強但複雜。理解這個組成，你看 IPSec 設定（strongSwan 的 conf）和 debug 協商失敗時就有方向。

## VPN 三大路線對比

| 面向 | WireGuard | OpenVPN | IPSec |
|---|---|---|---|
| 工作層 | tun（L3 流量）| tun（用戶空間）| 網路層（直接改 IP）|
| 協定 | Noise | TLS | IKE + ESP |
| 位置 | kernel | 用戶空間 | kernel |
| 標準化 | 事實標準 | 開源產品 | IETF 標準 |
| 設定 | 極簡 | 複雜 | 最複雜 |
| 速度 | 最快 | 較慢 | 快 |
| 設備支援 | 漸增 | 廣 | 最廣（內建）|
| 主要場景 | 現代個人/小型 | 偽裝/相容 | 企業/site-to-site |
| 行動內建 | 需 app | 需 app | iOS/Android 內建 |

```
三大路線的定位：

  WireGuard：現代首選（個人、新專案、追求簡單快速）
        │
  OpenVPN：偽裝需求、TLS 生態、相容老系統
        │
  IPSec：企業級、site-to-site、設備互通、標準化需求
    （路由器/防火牆內建、iOS/Android 原生支援）
        │
  → 沒有「最好」，看場景：
    個人自架 → WireGuard
    繞審查 → OpenVPN/TCP 或專門工具（Ch 29-30）
    企業/連網路/設備互通 → IPSec
```

> **IPSec 的定位是「企業級、site-to-site、設備互通」——它的標準化和廣泛內建是個人 VPN 不需要但企業需要的**。三大路線各有定位：**WireGuard**（現代個人首選，Ch 24）、**OpenVPN**（偽裝/相容，Ch 25）、**IPSec**（企業/site-to-site/設備互通）。IPSec 的獨特優勢是**標準化和廣泛內建**——它是 IETF 標準，所以**各廠牌的路由器、防火牆、雲端 VPN 閘道都內建支援且能互通**（Cisco 設備 ↔ AWS VPN ↔ 你的防火牆，都能用 IPSec 連），而 WireGuard/OpenVPN 要兩端都裝對應軟體。**iOS/Android 原生支援 IPSec**（不用裝 app），這對企業的 BYOD（自帶設備）方便。所以企業的 site-to-site（連總部和分公司、連雲端 VPC 和地端）大量用 IPSec——它的標準化解決了「不同廠牌設備互通」的需求。對**個人自架**（本課重點），IPSec 是過度的——它的複雜（協商一堆參數）和標準化（個人不需要設備互通）都是負擔，WireGuard 簡單得多。所以本課的練習 C 和 Final 用 WireGuard。理解 IPSec 的定位，你在企業環境遇到它時知道「為什麼用它」（標準化/互通），在個人場景知道「為什麼不用它」（太複雜）。VPN 三大路線的理解讓你能在任何場景選對工具。

## 故意弄壞:理解 IPSec 的複雜性陷阱

```
IPSec 常見的設定/debug 困難（理解為什麼它難）：

  1. 兩端參數不匹配 → 協商失敗：
     IKE 版本、加密演算法、DH group、PFS 設定...
     任一個不匹配 → phase1/phase2 協商失敗
        │
  2. 錯誤訊息隱晦：
     "no proposal chosen" —— 但不告訴你「哪個參數」不對
     → 要對照兩端的設定逐項檢查
        │
  3. NAT 穿透問題（Ch 8）：
     IPSec 的 ESP 不是 TCP/UDP（是 IP 協定 50）
     → NAT 不知道怎麼處理 → 要 NAT-T（把 ESP 包進 UDP 4500）
        │
  4. 防火牆要開對的東西：
     UDP 500（IKE）、UDP 4500（NAT-T）、IP 協定 50（ESP）
        │
  5. MTU 問題（Ch 4）：
     ESP 封裝增加開銷 → 同樣的 MTU 黑洞問題
        │
  → IPSec 的複雜性是真實的痛
    這也是 WireGuard 流行的原因（受夠了 IPSec 的人）
```

> **IPSec 的「協商失敗 + 隱晦錯誤 + NAT 穿透」三重複雜，正是 WireGuard 誕生的動機——「受夠了 IPSec」**。IPSec 的 debug 惡名昭彰：(1) **參數不匹配協商失敗**——兩端的 IKE 版本、加密演算法、DH group、PFS 等任一不匹配，協商就失敗，而你要設對的參數有十幾個；(2) **錯誤訊息隱晦**——經典的 "no proposal chosen" 不告訴你「哪個參數」不對，要對照兩端逐項排查；(3) **NAT 穿透複雜**——IPSec 的 ESP 是「IP 協定 50」（不是 TCP/UDP），NAT（Ch 8）不知道怎麼處理它，所以要 **NAT-T**（把 ESP 包進 UDP 4500 才能穿 NAT），這又多一層複雜；(4) **防火牆要開對**（UDP 500 的 IKE、UDP 4500 的 NAT-T、IP 協定 50 的 ESP——少開一個就不通）；(5) MTU 問題（Ch 4，同樣的封裝開銷）。這些複雜性是真實的運維痛苦——很多工程師對 IPSec 又愛又恨（標準但難搞）。**WireGuard 的誕生很大程度是「受夠了 IPSec/OpenVPN 的複雜」**——它的極簡（Ch 24）就是對這種複雜的反動。理解 IPSec 的複雜，你會更深刻理解 WireGuard「少即是多」的價值，也在不得不用 IPSec（企業環境）時有心理準備和 debug 方向。本課不要求你精通 IPSec 設定——理解它的原理、定位、和為什麼複雜，就達到目的了。

## 動手練習

1. 理解三路線：畫出 WireGuard/OpenVPN/IPSec 的對比表，說出各自的工作層、協定、場景

2. 理解 IPSec 組成：說明 IKE 和 ESP 各做什麼，為什麼 IPSec 是「協定族」

3. 理解傳輸 vs 隧道模式：說出兩種模式的差別和用途（主機到主機 vs 網路到網路）

4. 思考複雜性：列出 IPSec 設定/debug 的困難點，理解為什麼 WireGuard 是反動

5. 場景判斷：對「個人翻牆、連兩個辦公室、行動裝置連公司、設備互通」判斷該用哪種 VPN

## 本章重點整理

- IPSec 工作在網路層，直接加密/封裝 IP 封包（不用 tun）；傳輸模式（加密 payload）vs 隧道模式（加密整個封包）
- IPSec 是協定族：IKE（握手/金鑰交換，IKEv2 現代）+ ESP（加密封裝，常用）+ AH（只認證，少用）
- VPN 三大路線：WireGuard（Noise/kernel/極簡）、OpenVPN（TLS/用戶空間）、IPSec（網路層/標準/企業）
- IPSec 定位：企業/site-to-site/設備互通——標準化和廣泛內建（路由器/防火牆/iOS/Android）是它的優勢
- IPSec 複雜（參數協商/隱晦錯誤/NAT-T/防火牆）是 WireGuard 誕生的動機；個人用 WireGuard，企業互通用 IPSec

## 自我檢核

- [ ] 能解釋 IPSec 怎麼在網路層加密，和 tun-based VPN 的差別
- [ ] 知道 IPSec 的組成（IKE + ESP），傳輸 vs 隧道模式
- [ ] 能對比 VPN 三大路線（WireGuard/OpenVPN/IPSec）的取捨和場景
- [ ] 理解 IPSec 為什麼用於企業/site-to-site（標準化/互通）
- [ ] 知道 IPSec 為什麼複雜（協商/NAT-T），以及這和 WireGuard 的關係

## 延伸閱讀

### 官方文件

- **[RFC 4301 — Security Architecture for IP](https://www.rfc-editor.org/rfc/rfc4301)** + **[RFC 7296 — IKEv2](https://www.rfc-editor.org/rfc/rfc7296)** — IETF
  - **讀哪裡**：RFC 4301 的架構概覽、RFC 7296 的 IKEv2 流程
  - **為什麼值得讀**：IPSec 的權威標準（理解它是「標準協定族」）

### 文章

- **[IPSec 詳解](https://www.cloudflare.com/learning/network-layer/what-is-ipsec/)** — Cloudflare
  - **這篇說什麼**：IPSec 的組成、模式、運作的清楚解釋
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章原理的視覺化版

- **[VPN 協定大比較](https://www.comparitech.com/vpn/protocols/)** — Comparitech
  - **這篇說什麼**：WireGuard/OpenVPN/IPSec/L2TP 等各協定的對比
  - **為什麼值得讀**：本章三路線對比的擴充版

### 工具

- **[strongSwan 文件](https://docs.strongswan.org/)** — strongSwan
  - **讀哪裡**：getting started + 設定範例
  - **為什麼值得讀**：Linux 上 IPSec 的主流實作；如果要架 IPSec 的權威參考（也體會其複雜）

下一章是 Part 6 的總結——三家 VPN 的完整比較和選擇指南，把 WireGuard/OpenVPN/IPSec 放在一起，給你清楚的決策框架。

→ [Ch 27 三家 VPN 比較](./27-vpn-comparison.md)
