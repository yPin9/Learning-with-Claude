# Ch 0 — 環境搭建

> 目標：把整套課的工具一次裝齊，並確認你有「真機 / VM 練 + VPS 練」兩個環境。

## 兩個環境

整套課需要兩個環境：

| 環境 | 用途 | 取得方式 |
|---|---|---|
| **本機 / VM** | 跑 tcpdump、wireshark、iptables、自架 VPN client 端 | Linux 主機，或 VirtualBox / WSL2 |
| **VPS** | 公網 IP、架 VPN server、部署 nginx | 買一台（每月 5 USD）或免費 trial |

**沒有公網 IP 不能完整學 VPN / VPS 部分**。Part 8 之後一定要 VPS。

## 工具清單

### 網路核心（多數內建，少數需裝）

```bash
# Ubuntu / Debian
sudo apt install -y \
  iproute2 net-tools \
  tcpdump tshark \
  dnsutils \
  iputils-ping traceroute mtr-tiny \
  nmap netcat-openbsd \
  curl wget \
  iptables nftables \
  openssl \
  wireguard-tools openvpn \
  openssh-client openssh-server
```

### Wireshark GUI（在你本機）

```bash
sudo apt install wireshark
# 設你的 user 能用（不必 sudo 才能跑）
sudo usermod -aG wireshark $USER
# 登出登入
```

### Mac：

```bash
brew install wget tcpdump nmap mtr wireguard-tools dnsutils
brew install --cask wireshark
```

### Windows：用 WSL2 跑 Linux + Wireshark Windows 版本

## VPS 取得

### 付費 VPS（推薦）

| 廠商 | 入門價（USD/月） | 機房選擇 |
|---|---|---|
| **Vultr** | 2.50-5 | 全球 30+ |
| **Linode (Akamai)** | 5 | 全球 20+ |
| **DigitalOcean** | 5 | 全球 14 |
| **Hetzner** | 4 (歐) | 歐洲為主 |
| **AWS Lightsail** | 3.5 | 全球 |
| **OVH** | 4 | 歐洲 |

入門選 Vultr / Linode，全球機房選擇好、價格合理。

### 免費 VPS（可用但限制多）

| 廠商 | 提供 |
|---|---|
| **Oracle Cloud Free Tier** | 永久免費 2 台 ARM VM (4 core / 24GB)！|
| **AWS Free Tier** | 12 個月免費 t2.micro |
| **GCP Free Tier** | 永久免費 e2-micro (US 區) |
| **Azure Free Tier** | 12 個月免費 B1S |

**Oracle 那台 ARM VM 是真的猛**（4 核 24G），但要等審核通過。

如果只是想跑完課程的 lab，免費 tier 已經夠。**真正的 production 還是付費好**。

### VPS 規格建議（學習用）

```
CPU: 1 核
RAM: 1 GB
Disk: 25 GB
頻寬: 1 TB / 月
作業系統: Ubuntu 22.04 LTS 或 Debian 12
```

最低規格能跑 nginx + WireGuard + 小型網站。

## 註冊 + 開機 VPS（以 Vultr 為例）

1. 註冊 vultr.com
2. 加信用卡
3. Deploy Server → Cloud Compute → Regular Performance
4. 選機房（東京 / 新加坡 / 矽谷）
5. 選 Ubuntu 22.04
6. Plan: $6/月 (1 CPU / 1GB RAM)
7. SSH Keys: 上傳你的 public key（不會就先用 password）
8. Deploy

5 分鐘後你拿到 IP + root password。

```bash
# 連線測試
ssh root@<IP>
```

進去後：

```bash
apt update && apt upgrade -y
```

完成。**這台 VPS 就是接下來幾週的玩具**。

## SSH key 設定（強烈建議）

如果還沒生 SSH key：

```bash
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)"
# 一路 enter，預設位置 OK
```

把 public key 加到 VPS：

```bash
ssh-copy-id root@<VPS-IP>
# 或手動：cat ~/.ssh/id_ed25519.pub | ssh root@<VPS-IP> 'cat >> .ssh/authorized_keys'
```

之後 `ssh root@<VPS-IP>` 不需密碼。

Ch 34 會詳細展開 SSH 進階。

## Sanity check

跑這些，每個都該成功：

```bash
# 本機
ip a                               # 看你的網卡
ss -tnlp 2>/dev/null               # 看 listen 的 port
sudo tcpdump -i any -c 3 -nn       # 抓 3 個封包
dig google.com                     # DNS 查詢
ping -c 3 1.1.1.1                  # ping cloudflare
traceroute -n 1.1.1.1              # 看路由
curl -I https://example.com        # HTTP HEAD
openssl version                    # OpenSSL 版本
sudo iptables -L                   # firewall 規則

# VPS
ssh root@<IP> 'uname -a; ip a'     # 確認 VPS 連得上 + 看 VPS 網卡
```

每行都該有合理輸出。

## 一個常見踩雷：WSL2 / Docker / VM 抓不到完整網路

WSL2 / Docker container 有自己的虛擬網路 → tcpdump 看到的不是 host 真實流量。

對策：

- 玩 tcpdump / packet 分析在**真機 Linux 或完整 VM**
- WSL2 跑 application 跟 VPN 沒問題，但網路層細節不準

Mac 也類似 — Docker for Mac 內部是個 VM，網路層看到的不一定是 Mac 真實流量。

## 一個常見踩雷：Wireshark 沒權限

```bash
$ wireshark
# 看不到任何 interface
```

- Linux：把自己加 `wireshark` group（前面 install 的指令）
- Mac：第一次跑會提示授權
- 純 sudo wireshark 也行但不推薦（GUI 程式不該 sudo）

## 一個常見踩雷：apt 沒這個 package

某些 distro / 版本 package name 不同：

- Ubuntu: `dnsutils` (含 dig)
- Fedora / RHEL: `bind-utils`
- Arch: `bind`

裝不到先 `apt-cache search dig` 看實際名稱。

## 自我檢核

- [ ] 本機所有工具都裝完（tcpdump / wireshark / dig / iptables / wireguard-tools 等）
- [ ] 有一台 VPS 能 ssh 進去
- [ ] SSH key 認證設定好
- [ ] sanity check 全過
- [ ] 知道 WSL2 / Docker 抓 packet 的限制

下一章看「按 enter 後發生什麼」 — 整個網路課的全景圖。

→ [Ch 1 你按 enter 後發生什麼](./01-internet-journey.md)
