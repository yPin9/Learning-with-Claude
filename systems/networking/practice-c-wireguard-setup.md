# 練習 C — 自架 WireGuard 雙端配置

> 目標：完整跑 WireGuard 自架 — server (VPS) + client (本機 / 手機) + 測試 + debug。

## 任務規格

| # | 任務 | 驗收 |
|---|---|---|
| 1 | 在 VPS 上架 WireGuard server | `sudo wg` 看到 listen 51820 |
| 2 | 從本機連線 | `curl ifconfig.me` 顯示 VPS IP |
| 3 | 從手機連線 | 手機看到的 IP 是 VPS IP |
| 4 | 配 firewall + NAT | 能上網 |
| 5 | 寫文件 | 別人能照你的文件複製這個 setup |

## 環境

- 1 台 VPS（公網 IP，假設 1.2.3.4）
- 1 台本機 Linux（或 Mac）
- 1 個手機（可選，但極推薦）

## Step-by-step

### Server 端（在 VPS 上）

```bash
sudo apt update
sudo apt install -y wireguard wireguard-tools

# 1. 開 IP forward
sudo sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
sudo sysctl -p

# 2. 生 server key
sudo -i
mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077
wg genkey | tee server_private.key | wg pubkey > server_public.key

# 3. 寫 config
cat > wg0.conf <<EOF
[Interface]
Address = 10.10.10.1/24
ListenPort = 51820
PrivateKey = $(cat server_private.key)

PostUp   = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF

chmod 600 wg0.conf

# 4. 啟動
sudo wg-quick up wg0
sudo systemctl enable wg-quick@wg0
sudo wg

# 5. 開 firewall
sudo ufw allow 51820/udp
# 或
sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT
```

確認 server 起來：

```bash
sudo wg
ip a show wg0
sudo ss -unlp | grep 51820
```

### Client 1：本機 Linux

```bash
sudo apt install wireguard-tools

# 生 client key
sudo -i
mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077
wg genkey | tee client1_private.key | wg pubkey > client1_public.key

cat client1_public.key
# 把這個 public key 複製，加到 server config
```

回 server，加 peer：

```bash
sudo -i
cat >> /etc/wireguard/wg0.conf <<EOF

[Peer]
# Client 1
PublicKey = <貼 client1_public.key 內容>
AllowedIPs = 10.10.10.2/32
EOF

# Reload
sudo systemctl restart wg-quick@wg0
```

回 client，寫 config：

```bash
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.10.10.2/24
PrivateKey = $(sudo cat /etc/wireguard/client1_private.key)
DNS = 1.1.1.1

[Peer]
PublicKey = <貼 server public key>
Endpoint = 1.2.3.4:51820   # VPS public IP
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

# 啟動
sudo wg-quick up wg0

# 測試
sudo wg              # latest handshake 顯示
curl ifconfig.me     # 應該是 VPS IP
```

### Client 2：手機

```bash
# 在 server 端生 client 2
sudo -i
cd /etc/wireguard
wg genkey | tee client2_private.key | wg pubkey > client2_public.key

# 加到 server config
cat >> wg0.conf <<EOF

[Peer]
# Client 2 (手機)
PublicKey = $(cat client2_public.key)
AllowedIPs = 10.10.10.3/32
EOF

sudo systemctl restart wg-quick@wg0

# 寫手機 config
cat > /tmp/client2.conf <<EOF
[Interface]
Address = 10.10.10.3/24
PrivateKey = $(cat client2_private.key)
DNS = 1.1.1.1

[Peer]
PublicKey = $(cat server_public.key)
Endpoint = 1.2.3.4:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

# 產 QR code
sudo apt install qrencode
qrencode -t ansiutf8 < /tmp/client2.conf

# 清乾淨（把 client 私鑰留 client 用，server 上應該刪）
sudo rm /etc/wireguard/client2_private.key /tmp/client2.conf
```

手機裝 WireGuard app（iOS App Store / Play Store）→ 加新 tunnel → 掃 QR → 連線。

手機關 WiFi 用 4G，連 VPN，看 IP 變 VPS IP。

## 驗證 checklist

- [ ] server `sudo wg` 看到 2 個 peer，都有最近 handshake
- [ ] 本機 `curl ifconfig.me` = VPS IP
- [ ] 本機 `dig example.com` 走 VPN DNS
- [ ] 本機 `ping 10.10.10.1` (server) 通
- [ ] 本機 `ping 10.10.10.3` (其他 peer)：**這需要 server 開 forward** — 預設會通
- [ ] 手機在 4G 上連線：IP = VPS IP
- [ ] tcpdump on `wg0` 看到明文 traffic
- [ ] tcpdump on `eth0` 看到加密 UDP 51820 traffic

## debug

### 1. server 顯示 latest handshake = "never"

→ client 連不上。check：

- VPS firewall 開了 UDP 51820 嗎？
- server / client public/private key 對不對？
- Endpoint 寫對了嗎（VPS public IP）？

### 2. handshake 成功但 client 不通

→ NAT / forward 沒設好。

```bash
# server
sudo cat /proc/sys/net/ipv4/ip_forward    # 應該 1
sudo iptables -t nat -L POSTROUTING -n -v # 應該有 MASQUERADE
sudo iptables -L FORWARD -n -v            # 應該 ACCEPT
```

### 3. ping 通但 DNS 不通

→ DNS 設定問題。

- client config 有 `DNS = 1.1.1.1` 嗎？
- 手機 / 本機重啟 VPN

### 4. 速度慢

- VPS 頻寬限制？
- server CPU 滿？(`top`)
- 對 server `iperf3 -s` + client `iperf3 -c` 測

## 寫文件

完成後，寫一份「**從零開始 WireGuard 自架手冊**」，500-1000 字，含：

- 你的 VPS 規格
- 完整 step-by-step 命令
- 你遇到的問題 + 解法
- 安全注意事項

放 GitHub 或 blog。**這份文件本身就是你工程能力的證明**。

## 進階挑戰

**A. 雙 stack (IPv4 + IPv6)**：server 加 IPv6 prefix。client `AllowedIPs = 0.0.0.0/0, ::/0`。

**B. Split tunnel**：只有公司內網 traffic 走 VPN，其他直接走 ISP。`AllowedIPs` 改 `192.168.1.0/24` 之類。

**C. 多 server failover**：兩個 VPS 都跑 WireGuard，client 切換 endpoint。

**D. 混 netns 用**：把 WireGuard 跑在獨立 netns，讓「**只有特定程式走 VPN**」（Ch 20 技術）。

**E. Tailscale 對比**：用 Tailscale 做同樣的事，比較複雜度。

## 自我檢核

- [ ] WireGuard server 在 VPS 跑得起來
- [ ] 本機 + 手機都連得上
- [ ] 全部 traffic 走 VPN
- [ ] 知道 NAT / forward 怎麼配
- [ ] 寫了完整文件
- [ ] 知道 debug 流程

下個 Part 進 Proxy / 翻牆 — 跟 VPN 相關但不同的領域。

→ [Ch 28 HTTP Proxy / SOCKS5](./28-http-proxy-socks5.md)
