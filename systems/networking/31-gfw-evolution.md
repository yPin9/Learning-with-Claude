# Ch 31 — GFW 對抗演進史

> 目標：搞懂中國防火長城（GFW）的發展、各代翻牆工具如何回應、現代局勢。

## 為什麼要學這個

不是教你翻牆 — 是因為「**翻牆生態**」是現實網路安全 / 隱私技術最前沿的實驗場：

- 流量混淆
- 主動探測對抗
- 認證 / 加密 protocol 設計
- DPI（Deep Packet Inspection）
- 機器學習流量分類

學會這些原理 → 對網路安全 / privacy 整個領域有扎實基礎。

## GFW 是什麼

**Great Firewall of China** — 1998 年起的中國國家級防火牆系統：

- DNS 污染
- IP 黑名單
- 關鍵字過濾
- DPI 流量識別
- 主動探測（active probing）

監控所有跨境流量，封鎖被認定為「**有害**」的 site / protocol。

## 演進階段

### 階段 1：簡單封鎖（2003-2010）

GFW 早期只做：

- DNS 污染（查 facebook.com 給假 IP）
- IP 黑名單（直接 block）
- 關鍵字過濾（HTTP 明文 GET 含敏感字）

對抗：

- 改 DNS server（用 8.8.8.8）
- 用 IP 而不是 domain
- HTTPS（GFW 看不到 URL）

### 階段 2：DPI 識別（2010-2014）

GFW 加 Deep Packet Inspection — 看 packet 內容識別 protocol：

- OpenVPN handshake 有特徵 → 識別 → block
- IPSec 流量有特徵 → 識別 → 限速

對抗：

- **Shadowsocks** 出現（2012）— 流量像隨機 byte
- VPN 隱藏在 HTTPS（OpenVPN over TLS）

### 階段 3：主動探測（2015-2018）

GFW 對「可疑流量」**主動連回去測**：

```
 GFW 看到 client X 連 server Y
 GFW 自己也連 Y，看回應像不像 SS server / VPN server
 是 → block
```

對抗：

- SS 加 **simple-obfs**（偽裝 HTTP）
- ShadowsocksR (SSR) — 加 protocol obfs
- V2Ray + WebSocket + TLS — 偽裝成 HTTPS

### 階段 4：機器學習 + Trojan 出現（2018-2021）

GFW 用 ML 分類流量。**任何「異常」HTTPS 連線**被標記。

特徵：

- 連線時間長
- 流量大
- 沒對應網頁

對抗：

- **Trojan**（2018）— 完全偽裝成 HTTPS server，**跟真 HTTPS 共存**
- V2Ray VLESS over TLS

### 階段 5：Reality 與當前（2022-）

GFW 開始對「**真實 HTTPS**」也有限速 — 連 Github 都慢。

對抗：

- **Reality** — 用真實大公司 cert，看起來「**就是連 Apple**」
- 多種 transport：QUIC、HTTP/2、gRPC
- 「**裸奔策略**」：不偽裝，直接快速建連快速關（少被識別）

## 主動探測的對策

GFW 主動探測：「**這個 IP:port 是不是 SS / V2Ray server？**」

對策：

### 1. 偽裝完整 HTTPS server

server 同時跑 nginx web + V2Ray。GFW 探測時看到正常網頁（nginx 回 default page）→ 不像 proxy。

### 2. 認證才回應

VLESS UUID / Trojan password — **沒對的密碼，看起來像普通 server**。GFW 探測時 server 回 403 / nginx 預設頁，不暴露身份。

### 3. fallback 設定

V2Ray / Xray 配 `fallbacks`：

```json
"fallbacks": [
  {
    "dest": "127.0.0.1:80"   // GFW 探測時 forward 到 nginx
  }
]
```

「**對的人來，給 proxy；錯的人來，給網站**」。

## CDN 對抗

進階：把 V2Ray 流量走 Cloudflare CDN：

```
 client → Cloudflare → V2Ray server
```

GFW 看到的是「**連 Cloudflare**」 → 不能 block（Cloudflare 太多正當網站）。

但成本：

- Cloudflare 有頻寬限制
- 速度比直連慢
- 需要 Cloudflare 配合（用 WebSocket / gRPC）

## 一個常見誤解：「翻牆永遠安全 / 永遠快」

**錯**。GFW 跟翻牆工具是「**貓抓老鼠**」遊戲。

- 工具流行 → GFW 加強識別
- 工具被識別 → 用戶轉新工具

**沒有永久解決方案**。

## 一個常見誤解：「VPN 是翻牆唯一辦法」

**錯**。VPN 是其中一種，且不是最強：

- WireGuard / OpenVPN 流量易識別
- 翻牆主流是 V2Ray / Trojan / Shadowsocks（**proxy 不是 VPN**）

混淆 VPN / proxy → 配置失敗常見原因。

## 一個常見誤解：「翻牆工具越複雜越強」

**部分對**。複雜工具確實更難被識別。但：

- 配置容易出錯
- bug 多
- maintain 累

「**簡單工具配置正確**」往往比「**複雜工具半生不熟**」強。

## 一個常見誤解：「我用 VPN 連美國 server，GFW 看不到我訪問什麼」

**部分對**。

- 加密內容 GFW 看不到
- 但「**你連了美國 IP**」GFW 知道
- 連線時間 / 流量 pattern 也露餡
- DNS 沒走 VPN → leak domain

「**真正不被監控**」極難。

## 一個常見誤解：「Tor 是 GFW 終極解」

**錯**。Tor 跟翻牆設計目的不同：

- Tor：匿名性（多跳）、慢
- 翻牆工具：速度 / 偽裝、跨境

Tor 在中國也被 block，要走 **bridge** 才能用。**Tor + bridge 慢且不穩**，少有人用 Tor 做日常翻牆。

## 對抗審查：通用原則

不只 GFW，全球各國都有審查 — 俄羅斯、伊朗、北韓...

**通用原則**：

1. **流量混淆**：看起來像正常 traffic
2. **多協定備援**：一個 block 用另一個
3. **去中心化**：別依賴單一 server
4. **不洩漏 metadata**：DNS / 連線特徵
5. **assume 一切被監控**：應用層加密（HTTPS / Signal）才是底線

## 動手練習

**1. 看 GFW 從歷史角度的演進**

讀 Wikipedia 「Great Firewall」、「Shadowsocks」、「V2Ray」條目。

**2. 看現代翻牆社群討論**

GitHub `XTLS/Xray-core`、`v2fly/v2ray-core`、`shadowsocks` repos。Issue / PR 看現代問題。

Reddit r/dumbclub（中國翻牆社群）。

**3. 為自己的場景選工具**

寫下：

- 你需要翻牆嗎？目的是？
- 是否在中國 / 高審查地區？
- 信任的 VPS provider？
- 預算？

對應推薦：

| 場景 | 推薦 |
|---|---|
| 純隱私（非審查地區）| WireGuard / Mullvad |
| 中國日常 | Trojan + CDN |
| 中國工作（穩） | Reality |
| 純翻看西方影音 | 商業 VPN |

**4. 對你的 VPS 跑 GFW 探測 simulation**

從香港 / 中國的 VPS（如果有）連你的 server，看連線質量。

**5. 寫個翻牆 server 設計文件**

500 字，假設你要設計給 100 用戶用：

- 選什麼 protocol？為什麼？
- 怎麼防主動探測？
- 怎麼處理 IP 被 block？
- 監控用戶用量？

## 自我檢核

- [ ] 知道 GFW 5 個演進階段
- [ ] 知道每階段對應的翻牆工具
- [ ] 主動探測對策（fallback、偽裝）
- [ ] 知道 CDN + V2Ray 的組合
- [ ] 知道翻牆是「貓抓老鼠」永久遊戲

Part 7 結束。下個 Part 進 VPS 實務。

→ [Ch 32 VPS vs VM vs 容器 vs dedicated](./32-vps-vs-vm-container.md)
