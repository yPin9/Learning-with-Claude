# Ch 33 — 買 VPS

> 目標：知道怎麼挑 VPS — 機房 / 規格 / 廠商 / 用途匹配。

## 4 個關鍵考量

| 考量 | 看什麼 |
|---|---|
| **機房位置** | 地理 vs 用戶位置 vs 用途（翻牆 / web） |
| **規格** | CPU / RAM / disk / 頻寬 |
| **廠商** | 信譽 / 價格 / 服務 |
| **用途** | 用什麼決定其他 |

## 機房位置

### 看用途決定

| 用途 | 機房 |
|---|---|
| 給亞洲用戶服務 | 東京 / 新加坡 / 香港 / 首爾 |
| 給歐美用戶服務 | 矽谷 / 法蘭克福 / 倫敦 |
| 給中國翻牆用 | 日本 / 韓國 / 香港（近）/ 美西（遠） |
| 跑 LLM API（OpenAI / Anthropic） | 美國 |
| 純隱私（被 EU 法律保護） | 法蘭克福 / 阿姆斯特丹 / 蘇黎世 |

### 機房延遲速覽（對台灣）

```
 台灣 → 香港:        ~30 ms
 台灣 → 東京:        ~50 ms
 台灣 → 新加坡:      ~50 ms
 台灣 → 美國西岸:    ~150 ms
 台灣 → 美國東岸:    ~200 ms
 台灣 → 歐洲:        ~250 ms
```

ping 一下廠商 demo IP 確認。

### 翻牆特殊

中國 → 海外：

- 日本 / 香港最近，但 GFW 對這些路徑限速嚴
- 美西速度中等，較不限速
- 廠商選**有 CN2 GIA 線路**的（如 Bandwagon）— 中國優化路由

## 規格選擇

### 個人 / 小型場景（VPN + 1-2 service）

```
 1 CPU
 1-2 GB RAM
 25-50 GB disk
 1-2 TB 頻寬 / 月
 Linux Ubuntu 22.04
 月費 5-10 USD
```

足夠跑 WireGuard + nginx + 個人小站。

### 中型場景（多服務）

```
 2-4 CPU
 4-8 GB RAM
 80-160 GB disk
 3-5 TB 頻寬 / 月
 月費 20-40 USD
```

跑 docker / postgres / N 個 web app / monitoring。

### 大型 / 企業

直接用大雲（AWS / GCP）— 純 VPS 撐不起來。

### IPv4 vs IPv6

多數 VPS 預設給 1 個 IPv4。**部分廠商不再給** IPv4（IP 用光），只給 IPv6 + 共享 IPv4。

買前確認。

## 廠商比較

### Vultr

- 32+ 機房（最廣）
- $2.50/月起（有 IPv6-only 方案）
- 每小時計費
- 介面友善

**新手首推**。

### Linode（現 Akamai）

- 全球 20+ 機房
- $5/月起
- 老牌、穩定
- 介面清楚

**新手次推**。

### DigitalOcean

- 14 機房
- $5/月起
- 文件最棒（學習新人友善）
- 略貴

**新手不熟 Linux 的推**。

### Hetzner

- 歐洲 + 美國機房
- 4-8 EUR/月
- **CP 值最高**（同錢給雙倍 CPU/RAM）
- 介面老

**歐洲用戶 / 預算敏感推**。

### Bandwagon

- 美國 + 加拿大
- 中國優化路由（CN2 GIA）
- 翻牆用戶愛用

**翻牆用推**。

### Oracle Cloud Free Tier

- **永久免費** 2 ARM VM (4 core / 24 GB)
- 美 / 歐 / 亞機房
- 申請審核嚴

**0 預算用**。

### AWS / GCP / Azure

- 全球機房最廣
- **入門複雜**（IAM / VPC / 計費）
- 適合企業
- Free tier 1 年

**個人小場景不推**。

## 計費模式

### 按月

固定每月付。多數 VPS 預設。

### 按小時 / 按使用

開幾小時收幾小時錢。**測試 / 短期任務**好。

Vultr 等廠商 hourly billing，刪除立刻停收費。

### 預付折扣

年付 / 三年付有折扣（10-30% off）。**長期用穩定服務**買年付。

## 「Snapshot / Backup」

- **Snapshot**：手動備份磁碟（你存）
- **Backup**：自動定期備份（廠商存）

通常需付費（每月 1-3 USD）。**production 一定要開**。

## 防火牆 / 安全配置

廠商通常提供：

- **Cloud firewall**（不是 VPS 內部 iptables，而是廠商 control plane 的 firewall）
- **DDoS protection**（基本級免費 / 進階付費）
- **Private network**（多 VPS 之間用內網通信，不算流量）

## 一個常見誤解：「便宜 VPS = 賺到」

**部分對**。1-2 USD / 月的 VPS 常常：

- 規格虛標（advertised 1 GB 實際 0.5 GB usable）
- 頻寬限速嚴
- 客服差
- 不穩定（單點故障）

**穩定 production 用 5+ USD / 月**。

## 一個常見誤解：「機房越近越快」

**部分對**。機房近 → ping 低。但**頻寬 / 路由質量**也重要。

「**離你 100ms 但路由質量好**」可能比「**離你 30ms 但 ISP 路由繞道**」快。

## 一個常見誤解：「無限頻寬隨便用」

**錯**。「無限」通常指**no daily cap**，但有 fair use policy：

- 持續高頻寬 → 限速
- 異常 traffic → 警告 / 暫停
- DDoS / abuse → ban

「**Unmetered**」跟「**unlimited**」是不同的詞。

## 一個常見誤解：「VPS 安全是廠商的事」

**錯**。VPS = 你的 server，**所有安全你負責**：

- OS 更新
- 帳號密碼
- 服務配置
- firewall

廠商只負責「**機房 / 物理硬體 / 虛擬化**」。

## 動手練習

**1. 對比 5 家 VPS**

挑 5 家，做表：

| 廠商 | 1 GB RAM 月費 | 機房選擇 | IPv4 / IPv6 | 評價 |
|---|---|---|---|---|

**2. 實際買一台**

選擇後付費 deploy。建議配置：

- Vultr / Linode 5-10 USD / 月
- 機房選你最近的
- Ubuntu 22.04 LTS
- SSH key 登入

5 分鐘搞定。

**3. ping 不同機房**

```bash
for dc in tyo sgp lax ams; do
    echo "=== $dc ==="
    ping -c 3 vultr-$dc-demo-ip
done
```

對比延遲。

**4. benchmark 你的 VPS**

```bash
# Network
speedtest-cli

# Disk
dd if=/dev/zero of=test.bin bs=1M count=1000 oflag=direct
sync

# CPU
sysbench cpu --threads=$(nproc) run | grep "events per second"
```

跟 advertised 對比。

**5. 開 backup**

廠商 dashboard 開 backup（通常 +20% 月費）。**production 必開**。

## 自我檢核

- [ ] 知道 4 個機房選擇考量
- [ ] 至少對比 3 家 VPS
- [ ] 實際買過一台
- [ ] benchmark 過跟 advertised 比
- [ ] 知道計費模式 / backup / firewall 等基本服務

下一章看 SSH 完整指南 — 連 VPS 的核心工具。

→ [Ch 34 SSH 完整指南](./34-ssh-complete.md)
