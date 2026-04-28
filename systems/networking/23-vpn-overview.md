# Ch 23 — VPN 全景

> 目標：搞清楚 VPN 是什麼、3 大主流（WireGuard / OpenVPN / IPSec）的本質差異、跟 Proxy / SD-WAN 的對比。

## VPN 是什麼

**Virtual Private Network** — 在公網上建立「**虛擬私有網路**」。

3 個關鍵：

1. **Tunneling**：把 packet 包進另一個 packet
2. **Encryption**：加密 inner packet（外面看不到內容）
3. **Authentication**：驗證對方身份

達成：兩端網路像「直接連在同一個 LAN」（雖然中間隔著 Internet）。

## 為什麼用 VPN

| 場景 | 用法 |
|---|---|
| **遠端工作** | 連回公司內網存取資源 |
| **跨機房連接** | site-to-site VPN，兩 office 像同 LAN |
| **保護隱私** | 加密所有 traffic，ISP 看不到內容 |
| **繞過地理限制** | exit node 在別國，看起來像當地用戶 |
| **翻牆** | （在中國等地）穿越防火牆 |
| **公共 WiFi 安全** | 咖啡店 WiFi 不可信 → VPN 加密 |

不同場景用不同 VPN 設定。

## VPN 跟 Proxy 的差別

| 項目 | VPN | Proxy |
|---|---|---|
| 範圍 | **整台裝置流量** | 通常單一應用（如瀏覽器） |
| 層級 | L3 (IP) | L4-7 (TCP / app) |
| 加密 | 是（一般） | 視 proxy 而定 |
| 設定複雜度 | 中-高 | 低 |
| 速度 overhead | 中 | 低（HTTP proxy）/ 中（SOCKS）|
| 應用感知 | 透明（app 不知） | 應用需設 |

簡單版：**VPN 包整台、Proxy 包單一程式**。

Part 7 詳細展開 Proxy。

## 3 大 VPN 對比

| 維度 | OpenVPN | IPSec | WireGuard |
|---|---|---|---|
| 出生 | 2001 | 1995 | 2018 |
| 程式碼量 | ~70k 行 | 數百 k | ~4k 行 |
| 速度 | 慢 (user-space) | 快 (kernel) | **最快** (kernel) |
| 加密 | 多選擇 | 多選擇 | 固定（現代強算法）|
| 設定 | 複雜 (PKI) | 超複雜 | **簡單** |
| 跨平台 | ✅ 全 | ✅ 全 | ✅ 全（新版） |
| 防火牆穿透 | ✅ TCP 443 偽裝 | △ 易被擋 | △ 較易被擋 |
| 行動友善 | 中 | 差（連線斷重連慢）| **好**（roaming） |
| 社群成熟度 | 高 | 高（企業級）| 高（增長快） |

**現代推薦**：

- **個人 / 小型場景** → WireGuard（簡單、快、安全）
- **企業 site-to-site** → IPSec（成熟、相容性好）
- **要 TCP / 防火牆繞過** → OpenVPN（能偽裝 HTTPS）

## VPN 的 2 種 topology

### 1. Remote Access（遠端接入）

```
 Client (你)             VPN Server          Internal LAN
   ┌──┐                    ┌──┐              ┌──┐
   │PC│ ──── tunnel ──────►│  │ ────────────►│  │
   └──┘                    └──┘              └──┘
                              │
                              └─►  10.0.0.0/8
```

公司常用：員工從家裡連回公司。

### 2. Site-to-Site

```
 Office A LAN          VPN A      VPN B        Office B LAN
 192.168.1.0/24                                10.0.0.0/24
        │                │ ──tunnel──► │              │
   ┌────┴────┐    ┌─────┴┐         ┌──┴───┐     ┌───┴────┐
   │ devices │ ──►│router│ ───────►│router│ ───►│ devices│
   └─────────┘    └──────┘         └──────┘     └────────┘
```

兩 office 互通像同公司網路。

## VPN 跟 SD-WAN

**SD-WAN** (Software-Defined WAN) — 企業級「**多 ISP 智能切換**」：

- 同時連 4G + 光纖 + MPLS
- 動態選最快路徑
- 應用感知（影片走 4G、檔案走光纖）
- 集中管理

VPN 是 SD-WAN 的子集。**SD-WAN > VPN > 純 routing**。

新手別擔心 SD-WAN，企業 IT 才用。

## VPN 的 trust model

3 個信任問題：

### 1. Server 端

VPN server 看到所有你的 traffic（解密後）。**信任 server provider 嗎？**

商業 VPN（NordVPN / ExpressVPN）：信任 provider 不 log
自架 VPN：你**自己就是 provider**，最可信

### 2. 端到端

VPN 只加密 client ↔ VPN server。**VPN server → real server** 還是普通網路。

如果 real server 是 HTTP（不是 HTTPS），VPN server 後**仍明文**。

「**VPN 不替代 HTTPS**」。

### 3. DNS 洩漏

如果 DNS 不走 VPN，**你查的 domain 對 ISP 可見**。

對策：VPN 設定強制走 VPN 的 DNS。

## 一個常見誤解：「VPN 全方位匿名」

**錯**。VPN provider 看得到你；你 login 的 site 看得到你。

「**真匿名**」要 Tor + 多層 + 行為控制。VPN 只解決「**ISP 跟公網能看什麼**」。

## 一個常見誤解：「免費 VPN 安全」

**錯**。免費 VPN 多數靠賣 user data 賺錢。「**免費 = 你是商品**」。

要 VPN：

- 自架（最佳）
- 信譽好的付費（次選）
- 公司 / 學校的（信任 administrator）

## 一個常見誤解：「VPN 一定能解鎖 Netflix」

**部分對**。串流公司有反 VPN 機制，IP 列表常被 block。

每個 VPN provider 跟串流公司「**貓抓老鼠**」遊戲不停。

## 一個常見誤解：「VPN 100% 防中間人」

**部分對**。VPN 防「**ISP 跟公網中間設備**」MITM。但**用 VPN 連時，VPN provider 自己就能 MITM**。

「**真正端到端安全**」需要 application-level 加密（HTTPS / Signal / PGP）。

## 動手練習

**1. 看你機器有沒有 VPN interface**

```bash
ip a | grep -E "tun|wg|tap|ppp"
```

**2. 用瀏覽器試 VPN（不裝）**

```bash
# 看你公網 IP（沒 VPN）
curl ifconfig.me
```

如果有 VPN（個人用 / 公司），開了再跑：

```bash
curl ifconfig.me
```

兩個 IP 不同。

**3. 看 traffic 是否走 VPN**

```bash
ip route | head
# 如果有 default via X dev tun0/wg0 → 走 VPN
```

**4. DNS 洩漏測試**

```bash
# 看 DNS query 走哪
dig example.com
# 看 server 是不是 VPN 的 DNS

# 線上工具
curl ipleak.net   # 完整 leak test
```

**5. 思考你的 VPN 用例**

寫下：

- 你目前 VPN 用什麼？
- 為什麼用？
- 信任哪個 provider？
- DNS 洩漏嗎？

## 自我檢核

- [ ] 講得出 VPN 三要素（tunnel / encrypt / auth）
- [ ] 知道 VPN vs Proxy 差別
- [ ] WireGuard / OpenVPN / IPSec 各自定位
- [ ] Remote Access vs Site-to-Site topology 清楚
- [ ] 知道 VPN 的 3 個信任問題
- [ ] 「VPN ≠ 匿名」概念清楚

下一章看 WireGuard — 最現代的 VPN。

→ [Ch 24 WireGuard 原理 + 自架](./24-wireguard.md)
