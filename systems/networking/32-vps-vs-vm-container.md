# Ch 32 — VPS vs VM vs 容器 vs dedicated

> 目標：搞清楚 hosting 4 個層級的差異，知道何時選 VPS。

## 4 個層級

```
   實體設備               (你買 / 租機房)
       │
       │ ←─────  Dedicated server (整台租)
       ▼
   Hypervisor             (虛擬化層，如 KVM / Xen)
       │
       │ ←─────  VPS (Virtual Private Server，租其中 1 個 VM)
       ▼
   Linux kernel
       │
       │ ←─────  Container (LXC / Docker)
       ▼
   Application
```

每層更輕量、隔離更弱。

## 各層級對比

| 項目 | Dedicated | VPS / VM | Container |
|---|---|---|---|
| 隔離 | 完全（不同硬體） | 強（不同 kernel） | 弱（共 kernel） |
| 性能 | 最強（無 overhead） | 強（少 overhead） | 強（幾乎無 overhead） |
| 開機時間 | 分鐘 | 秒 | 毫秒 |
| 資源 | 整台 | 配額 | 配額 |
| 成本 | 高（$100-1000/月） | 中（$5-100/月） | 低（共享主機） |
| Self-managed | 完全 | 完全 | 視 host |
| 適合 | 高 throughput / DB | 一般 web / VPN | 微服務 / dev |

## VPS 的本質

**1 台物理 server 跑 N 個 VM，賣給 N 個用戶**。

```
 ┌────────────────────────────────────┐
 │ 物理 server: 32 cores, 128GB RAM   │
 │                                    │
 │  ┌──────┬──────┬──────┬──────┐    │
 │  │ VPS A│ VPS B│ VPS C│ VPS D│    │
 │  │ 1 c  │ 2 c  │ 4 c  │ 8 c  │    │
 │  │ 1 G  │ 2 G  │ 8 G  │ 16 G │    │
 │  └──────┴──────┴──────┴──────┘    │
 │       ↑                            │
 │   Hypervisor (KVM / Xen)           │
 └────────────────────────────────────┘
```

每個 VPS：

- 獨立 Linux kernel
- 獨立 IP（公網）
- 獨立 root access
- 配額限制（CPU / RAM / disk / 頻寬）

## VPS 種類

### Shared VPS

主流個人用。多用戶共享物理機，**配額限制嚴**。

廠商：Vultr, Linode, DigitalOcean, Hetzner（最便宜）。

### Dedicated CPU VPS

CPU 不共享（vs shared 是「**burst**」CPU）。**穩定 high CPU 場景**。

貴 2-3x。

### High-Memory VPS

RAM 大、CPU 少。資料庫 / cache 用。

### Spot / Preemptible VPS

雲廠商出（AWS Spot, GCP Preemptible）。**便宜 70-90%**，但**隨時可能被收回**。

適合：批次運算、CI、非關鍵服務。

## VPS vs 雲（AWS / GCP / Azure）

| 項目 | 純 VPS（Vultr / Linode） | 大雲（AWS / GCP） |
|---|---|---|
| 計費 | 月計、容易預測 | 按使用量、複雜 |
| 入門 | 簡單 | 複雜（IAM / VPC...） |
| 服務 | 純機 | 機 + 100+ 服務 |
| 全球 | 20-30 機房 | 30+ region |
| 適合 | 個人 / 小專案 | 企業 / 大規模 |

「**個人 / 小團隊用 VPS、企業用大雲**」是普遍指南。

## 容器跟 VPS

容器**不能取代 VPS**。容器需要一個 host，host 通常是 VM / VPS。

「**VPS 上跑 docker**」常見部署模式：

```
 VPS (Linux host)
   ├── nginx container
   ├── postgres container
   ├── app container
   └── monitoring container
```

容器負責「**應用打包 + 隔離**」，VPS 提供「**底層 OS + IP**」。

## 一個常見誤解：「VPS 跟 VM 不同」

**錯**。VPS = VM 給你用。**「Virtual Private Server」就是「私人 VM」**。

廠商賣 VPS 服務 = 給你 VM + 公網 IP + Linux 預裝。

## 一個常見誤解：「VPS 比 dedicated 便宜總是好」

**部分對**。但：

- VPS 共享物理 → **noisy neighbor**（鄰居 CPU 爆滿影響你）
- 配額限制 → 突發 burst 可能慢
- 跑 IO heavy 工作 → 可能卡

**真正性能要求**用 dedicated。**多數場景 VPS 夠**。

## 一個常見誤解：「容器比 VM 慢」

**錯**。容器**幾乎沒 overhead**（共享 kernel）。

「VM 比容器強」常指**隔離強度** — 一個 VM 崩不影響其他 VM；container 共 kernel，kernel bug 影響全部。

## 一個常見誤解：「VPS 能跑任何東西」

**部分對**。VPS 限制：

- 不能跑非 Linux kernel module（除非廠商允許）
- 某些 VPS 禁 BT / VPN / 翻牆 / 挖礦
- 頻寬有限制（流量超就限速 / 收費）
- 公網 IP 可能被某些 site 列黑名單（如果其他 VPS 用戶被 abuse）

買前看廠商 ToS。

## 動手練習

**1. 列你目前用的 hosting**

寫下：

- 用什麼？（VPS / 雲 / 主機）
- 每月成本？
- 跑什麼？

**2. 比較 5 家 VPS provider**

去看 5 家定價（Vultr, Linode, DigitalOcean, Hetzner, AWS Lightsail）：

| 廠商 | 1G RAM 月費 | 機房 | 頻寬 |
|---|---|---|---|
| Vultr | ? | ? | ? |
| ...

選最適合你的。

**3. benchmark 自己 VPS**

```bash
# CPU
sysbench cpu --threads=4 run

# Disk
dd if=/dev/zero of=test.bin bs=1M count=1000 oflag=direct

# Network
speedtest-cli   # apt install speedtest-cli
```

跟 advertised spec 對比。

**4. 看 VPS 的「噪音鄰居」**

```bash
# CPU steal time（hypervisor 偷的 CPU 時間）
top
# 或
vmstat 1
# %st 欄位高 = noisy neighbor
```

如果 `%st` > 5% 持續，換 dedicated CPU VPS。

**5. 比較 VPS / 容器啟動時間**

```bash
# VPS：從廠商 dashboard 看 deploy 時間
# Container：
time docker run --rm alpine echo hi
# 通常 0.5 秒
```

## 自我檢核

- [ ] 講得出 dedicated / VPS / container 三層差異
- [ ] 知道 VPS 是 VM
- [ ] 知道 VPS 跟雲（AWS / GCP）的區別
- [ ] 列得出 5+ 家 VPS provider
- [ ] benchmark 過自己的 VPS

下一章看怎麼買 VPS — 機房選擇 / 規格 / 廠商。

→ [Ch 33 買 VPS](./33-buying-vps.md)
