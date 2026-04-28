# Ch 27 — 三家對比與選擇

> 目標：對比 WireGuard / OpenVPN / IPSec，知道在不同場景該選哪個。

## 完整對照表

| 維度 | WireGuard | OpenVPN | IPSec |
|---|---|---|---|
| 出生 | 2018 | 2001 | 1995 |
| 程式碼 | ~4k 行 | ~70k 行 | 數百 k |
| 預設位置 | kernel | user-space | kernel |
| Protocol | UDP only | UDP / TCP | ESP (50) + IKE (UDP 500/4500) |
| Port 偽裝 | 任意 UDP | TCP 443 (好) | 不能 |
| 加密選擇 | 固定（強） | 多選 | 多選 |
| 設定複雜度 | **低** | 中 | 高 |
| 連線速度 | **最快** | 中 | 快 |
| 行動 roaming | **好** | 中 | 差 |
| NAT 穿透 | 好 | 好 | 中 |
| 跨平台 | 全 | 全 | 全（OS 內建） |
| 企業 admin | 弱 | 中 | **強** |
| Cert revocation | 手動 | **PKI 完整** | **PKI 完整** |
| 防火牆對抗 | 弱-中 | **強** (TCP 443) | 弱 |
| 社群成熟度 | 高 | 高 | 高 |

## 場景推薦

### 個人 VPN（自架）

**WireGuard** — 沒競爭。簡單、快、安全。

### 翻牆（中國 GFW）

**OpenVPN over TCP 443** > **V2Ray / Trojan**（Ch 30）> **WireGuard**

GFW 對 WireGuard UDP 流量越來越敏感。OpenVPN TCP 443 偽裝成 HTTPS 較強，但**真的對抗 GFW 用 V2Ray / Trojan / Shadowsocks**。

### 企業 site-to-site

**IPSec** — 跟硬體 router / 廠商整合好。

如果**雙方都你管的 Linux**，**WireGuard 也很好**且簡單。

### 企業 remote access（員工連回辦公室）

歷史用 **OpenVPN** 或 **IPSec**（含 cert + AD 整合）。

新公司開始改 **Tailscale / Twingate**（基於 WireGuard 的 zero-trust 平台）— 大幅簡化管理。

### 行動裝置友善

**WireGuard**（roaming 好）= **IKEv2** > OpenVPN

iPhone / Android 切換 WiFi / 4G 時不斷線 → WireGuard / IKEv2 更穩。

### Production server-to-server

**WireGuard** — 簡單、快、好維護。

### 跨大洲低延遲

**WireGuard** — 加密 overhead 最低。

## Tailscale / Headscale

值得單獨提的「**現代 VPN 解決方案**」：

**Tailscale**：

- 基於 WireGuard
- 自動 NAT 穿透 + relay
- 不需要 public IP server
- 集中管理（webconsole）
- 免費 < 100 device
- 商業 / 企業付費

**Headscale**：

- Tailscale 的開源 self-hosted control server
- 你自己跑，不依賴 Tailscale 公司

**現代 「VPN as service」標配**。家庭 / 小團隊極推。

## 性能對比（典型）

| VPN | Throughput (1Gbps link) | CPU 用 |
|---|---|---|
| WireGuard | 950 Mbps | 5-10% |
| IPSec (kernel) | 900 Mbps | 10-15% |
| OpenVPN | 200-500 Mbps | 30-50% |

WireGuard / IPSec 接近 wire speed。OpenVPN 因 user-space 限制較慢。

## 安全成熟度

3 家都「**安全**」（用最新 cipher）。差別：

- **WireGuard**：code 量小、audit 容易、cipher 固定（少 misconfiguration）
- **OpenVPN**：歷史長、社群大、bug 都被找過。**選錯 cipher 不安全**
- **IPSec**：歷史長、企業生態成熟。但**RFC 多到不可能全 audit**

「**WireGuard 因為簡單而安全**」是設計哲學。

## 「我想自架，怎麼選」決策樹

```
 你要 VPN
    │
    ▼
 個人 / 小團體？─── 是 ──► WireGuard（或 Tailscale 簡化版）
    │
   否
    │
    ▼
 企業 site-to-site？──── 是 ──► IPSec（廠商互通）+ WireGuard（單一 vendor）
    │
   否
    │
    ▼
 企業遠端接入？──── 是 ──► OpenVPN + AD（傳統）/ Tailscale（現代）
    │
   否
    │
    ▼
 翻牆 / 強防火牆？──── 是 ──► V2Ray / Shadowsocks（不是 VPN，是 proxy，Ch 29-30）
                              或 OpenVPN TCP 443
```

## 多家混用

實際 production 常常多種混用：

- WireGuard：server-to-server
- OpenVPN：員工 remote access
- IPSec：跟客戶 / 合作方 site-to-site
- Tailscale：開發環境快速建網

不同 use case 用不同工具，沒有「**單一最佳**」。

## 一個常見誤解：「VPN 速度由 VPN 決定」

**部分對**。**瓶頸**通常在：

1. server 跟 client 之間的物理網路（最大）
2. server CPU（如果加密重）
3. VPN protocol overhead（次要）
4. server 頻寬（VPS 限額）

WireGuard 在「相同 server」下贏 OpenVPN。但「不同 server」WireGuard 可能輸（廉價 VPS vs 高頻寬 server）。

## 一個常見誤解：「OpenVPN 已過時，沒人用」

**錯**。OpenVPN 仍是企業主流之一。**老設備 / 老用戶**繼續用 OpenVPN，新部署慢慢遷移。

不要「最新就最好」，**穩定性 / 相容性**對企業更重要。

## 一個常見誤解：「IPSec 跟 OpenVPN 互通」

**錯**。3 家**完全不互通**。WireGuard 客戶端不能連 OpenVPN server。

廠商 / OS 通常**多家都支援** — 但每個連線**選定一家用**。

## 動手練習

**1. 寫一份「我的 VPN 選擇」決策**

對你的具體 use case（個人翻牆？跟同事連？跨機房？）：

- 選哪個？為什麼？
- 第二選擇是什麼？
- 不選的理由？

**2. 對比 WireGuard 跟 OpenVPN throughput**

兩家都架，跑 iperf3：

```bash
iperf3 -s    # server
iperf3 -c <vpn-IP>    # client
```

對比結果。

**3. 試 Tailscale**

```bash
# https://tailscale.com 註冊免費
# 在兩台機器裝 client
sudo apt install tailscale
sudo tailscale up

# 兩台互 ping
```

5 分鐘建好 VPN。對比手架 WireGuard 的工程量。

**4. 看大公司用什麼**

研究：Cloudflare、Tailscale、Mullvad（VPN provider）各用什麼 protocol？為什麼？

**5. 寫個 dev journal**

對 4 家 VPN（WireGuard / OpenVPN / IPSec / Tailscale）各用一次，寫下 100 字感受。

## 自我檢核

- [ ] 3 家 VPN 對照表理解
- [ ] 知道每個場景該選哪家
- [ ] 知道 Tailscale 的價值
- [ ] 跑過 2+ 家 VPN
- [ ] 對「**選哪家**」有自己判斷
- [ ] 體會「無單一最佳，看場景」

Part 6 結束。練習 C 完整自架 WireGuard。

→ [練習 C：自架 WireGuard 雙端配置](./practice-c-wireguard-setup.md)
