# Ch 24 — WireGuard 原理 + 自架

> 目標：搞懂 WireGuard 怎麼運作、跟其他 VPN 設計差別、在 VPS 上自架完整 client + server。

## WireGuard 是什麼

2018 年釋出的 VPN，**徹底重新設計**：

- **~4000 行 code**（vs OpenVPN 70k+ / IPSec 數百 k）
- **kernel module**（Linux 5.6+ 內建，不需要 user-space process）
- **固定加密算法**（沒選擇困擾，安全 default）
- **UDP only**
- **無連線狀態**（Stateless handshake）
- **快**（接近 wire speed）
- **roaming**（IP 改變不斷線）

**現代 VPN 首選**，特別是個人 / 小型部署。

## 加密選擇

WireGuard 不讓你選 cipher，**全用現代強算法**：

- **Key exchange**: Curve25519 (ECDH)
- **Encryption**: ChaCha20
- **Authentication**: Poly1305 (MAC)
- **Hashing**: BLAKE2s
- **Identity**: Curve25519 keys

這 5 個是 2020s 加密界共識的「**最強組合**」。少 bug、快、安全。

## 設計哲學

OpenVPN：「**支援 N 種設定，使用者選最好的**」 → 容易選錯。

WireGuard：「**只有一種設定，永遠是最好的**」 → 不會選錯。

## WireGuard 的「peer」概念

WireGuard 沒「server / client」分別 — 雙方都是 **peer**。

每個 peer 有：

- **private key**（祕密）
- **public key**（公開，給對方）
- **AllowedIPs**（哪些 IP 走這 peer）
- **Endpoint**（對方公網 IP:port）

「server」只是「**對外 listen + 大家連的對方**」的 peer。

## 安裝

```bash
# Ubuntu / Debian
sudo apt install wireguard wireguard-tools

# 確認
wg --version
```

WireGuard kernel module 在 Linux 5.6+ 內建。舊版需要 install module。

## 生 key

```bash
# 私鑰
wg genkey > server_private.key

# 公鑰（從私鑰 derive）
wg pubkey < server_private.key > server_public.key

# 看
cat server_private.key   # 不能洩漏！
cat server_public.key    # 公開 OK
```

長度都是 base64 編碼的 32 byte（44 字元）。

## 自架 WireGuard：Server 端

假設你 VPS IP = `1.2.3.4`，內部 VPN 網段 `10.0.0.0/24`。

### Step 1：生 server keys

```bash
sudo -i
mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077

wg genkey | tee server_private.key | wg pubkey > server_public.key
chmod 600 *
```

### Step 2：寫 server config

```bash
# /etc/wireguard/wg0.conf
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat server_private.key)

# 開 IP forward + NAT (讓 client 能上網)
PostUp   = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Peer (client) 之後加
EOF

chmod 600 wg0.conf
```

### Step 3：啟動

```bash
# 啟用
sudo wg-quick up wg0

# 看狀態
sudo wg

# 開機自動啟動
sudo systemctl enable wg-quick@wg0
```

確認 `wg0` interface up：

```bash
ip a show wg0
```

### Step 4：開 firewall

```bash
sudo ufw allow 51820/udp
# 或
sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT
```

server 完成。

## 自架 WireGuard：Client 端

在你本機（Linux）：

### Step 1：生 client keys

```bash
sudo mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077

wg genkey | sudo tee client_private.key | wg pubkey | sudo tee client_public.key
sudo chmod 600 *
```

### Step 2：把 client public key 加到 server config

回 server，編輯 `/etc/wireguard/wg0.conf`，加：

```
[Peer]
PublicKey = <client_public_key>
AllowedIPs = 10.0.0.2/32
```

reload server：

```bash
sudo wg-quick down wg0
sudo wg-quick up wg0
# 或
sudo systemctl restart wg-quick@wg0
```

### Step 3：寫 client config

回本機：

```bash
# /etc/wireguard/wg0.conf
sudo tee /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.0.0.2/24
PrivateKey = $(sudo cat /etc/wireguard/client_private.key)
DNS = 1.1.1.1

[Peer]
PublicKey = <server_public_key>
Endpoint = 1.2.3.4:51820     # 你的 VPS public IP
AllowedIPs = 0.0.0.0/0       # 全部 traffic 走 VPN
PersistentKeepalive = 25     # NAT keepalive
EOF
```

「`0.0.0.0/0`」表示「**所有 traffic 走 VPN**」。改成 `10.0.0.0/8` 等只走特定子網。

### Step 4：連線

```bash
sudo wg-quick up wg0

# 確認
sudo wg
ip a show wg0

# 確認 traffic 走 VPN
curl ifconfig.me   # 應該顯示 VPS 的公網 IP
```

成功！

## 多 client

每個 client 重複「Step 1-3」用不同 IP（10.0.0.3, 10.0.0.4...）。

server config 加多個 `[Peer]` section。

## Mobile client

WireGuard 手機 app（iOS / Android）非常好用。

server 端產生 config 後，用 `qrencode` 出 QR code：

```bash
sudo apt install qrencode
qrencode -t ansiutf8 < /etc/wireguard/client.conf
```

手機 app 掃 QR → 一鍵 import。

## WireGuard 觀察

```bash
# 看當前狀態
sudo wg

# 輸出範例：
# interface: wg0
#   public key: ...
#   private key: (hidden)
#   listening port: 51820
#
# peer: <client_pubkey>
#   endpoint: 5.6.7.8:54321
#   allowed ips: 10.0.0.2/32
#   latest handshake: 2 minutes ago
#   transfer: 1.23 MiB received, 5.67 MiB sent
#   persistent keepalive: every 25 seconds
```

`latest handshake` 過久（> 5 min）= 連線可能斷。

## 常用 client 設定

### 全部 traffic 走 VPN

```
AllowedIPs = 0.0.0.0/0
```

連 VPN 就所有上網經 VPN。隱私 / 翻牆用。

### 只內網走 VPN

```
AllowedIPs = 192.168.1.0/24, 10.0.0.0/24
```

連公司內網，其他 traffic 走本地。**省 VPN 頻寬**、避免 VPN provider 看你個人 traffic。

### 雙 stack

```
AllowedIPs = 0.0.0.0/0, ::/0
```

含 IPv4 + IPv6。

## 一個常見踩雷：手機進房間 WiFi 連不上

可能 NAT 太嚴 / firewall 擋 UDP 51820。

對策：

- 換 port（443 / 53 — 看起來像 HTTPS / DNS）
- 用 OpenVPN（TCP 模式能偽裝）
- 用 V2Ray（更隱蔽）

## 一個常見踩雷：路由壞了上不了網

```bash
# 連 VPN 後 ping 8.8.8.8 不通
sudo wg
sudo ip route   # 看路由表
```

可能：

- AllowedIPs = 0.0.0.0/0 但 server 沒 NAT
- DNS 設錯

debug：

```bash
# 在 server 看 forward 規則
sudo iptables -L FORWARD -n -v
sudo iptables -L -t nat -n -v
sudo cat /proc/sys/net/ipv4/ip_forward   # 應該是 1
```

## 一個常見踩雷：CPU 100%

WireGuard kernel module 通常很省 CPU。如果 100% → 可能用 user-space WireGuard（wireguard-go）— 慢很多。

確認：

```bash
lsmod | grep wireguard   # 應該有
```

沒有 → 用 user-space → 升級 kernel 或裝 kernel module。

## 一個常見踩雷：「server 的 wg-quick up 後沒 listen」

看 `/etc/wireguard/wg0.conf` 有 `ListenPort = 51820` 嗎？沒設不 listen。

```bash
sudo ss -unlp | grep wireguard
```

## 動手練習

**1. 自架完整 WireGuard server + client**

按本章流程，在 VPS 上架，本機連。

驗證：

- `sudo wg` 看 latest handshake
- `curl ifconfig.me` 看到 VPS IP

**2. 看 WireGuard 流量**

```bash
# 內部明文（VPN tunnel 內）
sudo tcpdump -nn -i wg0

# 外部加密
sudo tcpdump -nn -i eth0 'udp port 51820'
```

對比兩邊。

**3. 測 throughput**

```bash
# server
iperf3 -s

# client (透過 VPN 連)
iperf3 -c 10.0.0.1
```

跟直連 VPS（不過 VPN）比較。

**4. mobile client**

裝 WireGuard app，用 QR code import config。試從 4G / 別人 WiFi 連。

**5. 把 AllowedIPs 改成不同範圍**

試：

- `0.0.0.0/0` — 全走 VPN
- `10.0.0.0/24` — 只 VPN 內網
- `0.0.0.0/0, ::/0` — 含 IPv6

對比 routing table 變化。

## 自我檢核

- [ ] 知道 WireGuard 的「peer」概念
- [ ] 自己生過 key pair
- [ ] 在 VPS 上架過 WireGuard server
- [ ] 從本機連得上、`curl ifconfig.me` 顯示 VPS IP
- [ ] 會用 `sudo wg` 看狀態
- [ ] 知道 AllowedIPs 怎麼控制 routing

下一章看 OpenVPN — 老牌 VPN 王者。

→ [Ch 25 OpenVPN 原理 + 自架](./25-openvpn.md)
