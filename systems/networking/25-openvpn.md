# Ch 25 — OpenVPN 原理 + 自架

> 目標：認識 OpenVPN 的設計、PKI 體系、自架流程，理解為什麼比 WireGuard 複雜但仍有人用。

## OpenVPN 為什麼還在

WireGuard 各方面更好，但 OpenVPN 仍廣用：

1. **歷史長**（2001） — 老設備 / 客戶端都支援
2. **TCP / UDP 雙模** — TCP mode 能穿透嚴防火牆
3. **port 443 偽裝** — 看起來像 HTTPS
4. **PKI 體系** — 適合大企業集中管理 cert
5. **plugin / script** — 高度可擴充

**用 OpenVPN 的場景**：

- 企業環境（PKI / Active Directory 整合）
- 嚴防火牆（只開 TCP 443）
- 老 client 支援

**新個人專案**多用 WireGuard。

## 兩種運作模式

| 項目 | TCP mode | UDP mode |
|---|---|---|
| 速度 | 慢（TCP overhead） | 快 |
| 穿透 | 強（看起來像 HTTPS） | 中（UDP 易擋） |
| 預設 port | 443 / 1194 | 1194 |

對抗 GFW 或嚴企業防火牆 → TCP 443 + TLS。
家用 / 一般場景 → UDP。

## OpenVPN 的 PKI 結構

OpenVPN 用 SSL/TLS PKI：

```
 ┌────────────────┐
 │  Root CA       │  自己的 CA
 │  (root.crt)    │  簽其他所有 cert
 └────────┬───────┘
          │
   ┌──────┴──────┐
   ▼             ▼
┌────────┐  ┌────────┐
│ Server │  │ Client │
│  cert  │  │  cert  │
└────────┘  └────────┘
```

每個參與者要：

- private key
- 由 CA 簽的 cert（含 public key）
- 信任的 root CA cert

**比 WireGuard 複雜很多** — 但對企業是優點（cert 可吊銷）。

## 自架 OpenVPN：Server

### Step 1：安裝 + easy-rsa（PKI 工具）

```bash
sudo apt install openvpn easy-rsa

# 建 PKI 目錄
mkdir -p ~/openvpn-ca
cp -r /usr/share/easy-rsa/* ~/openvpn-ca/
cd ~/openvpn-ca
```

### Step 2：建 CA

```bash
./easyrsa init-pki
./easyrsa build-ca nopass     # CA 不加密（生產建議加 password）
# 給 CA name（如 "myvpn-ca"）
```

產生：

- `pki/ca.crt`（公開的 CA cert）
- `pki/private/ca.key`（**祕密**，CA 私鑰）

### Step 3：生 server cert

```bash
./easyrsa build-server-full server nopass
```

產生：

- `pki/issued/server.crt`
- `pki/private/server.key`

### Step 4：生 DH params + TLS auth key

```bash
./easyrsa gen-dh
openvpn --genkey --secret pki/ta.key
```

DH params 慢（幾分鐘），用於 perfect forward secrecy。

### Step 5：寫 server config

```bash
sudo cp pki/{ca.crt,issued/server.crt,private/server.key,dh.pem,ta.key} /etc/openvpn/server/
```

`/etc/openvpn/server/server.conf`：

```
port 1194
proto udp
dev tun

ca ca.crt
cert server.crt
key server.key
dh dh.pem

server 10.8.0.0 255.255.255.0
ifconfig-pool-persist ipp.txt

push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"

keepalive 10 120
tls-auth ta.key 0

cipher AES-256-GCM
auth SHA256

user nobody
group nogroup

persist-key
persist-tun

status openvpn-status.log
verb 3
```

關鍵：

- `port 1194` / `proto udp` — 預設 port + protocol
- `server 10.8.0.0 255.255.255.0` — VPN 子網
- `push "redirect-gateway"` — client 全 traffic 走 VPN
- `push "dhcp-option DNS"` — 推 DNS 給 client
- `cipher AES-256-GCM` — 加密

### Step 6：開 IP forward + NAT

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE
```

### Step 7：啟動

```bash
sudo systemctl enable openvpn-server@server
sudo systemctl start openvpn-server@server

# 看狀態
sudo systemctl status openvpn-server@server
sudo journalctl -u openvpn-server@server -f
```

確認 `tun0` 起來：

```bash
ip a show tun0
```

## 自架 OpenVPN：Client

### Step 1：在 server 端 build client cert

```bash
cd ~/openvpn-ca
./easyrsa build-client-full client1 nopass
```

產生：

- `pki/issued/client1.crt`
- `pki/private/client1.key`

### Step 2：建單一 .ovpn 檔（給 client）

OpenVPN client 通常用 `.ovpn` 整合檔（含 cert / key 內嵌）。

`make-ovpn.sh`：

```bash
#!/bin/bash
NAME=$1
DIR=~/openvpn-ca

cat <<EOF
client
dev tun
proto udp
remote 1.2.3.4 1194    # 換成你的 VPS IP
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
verb 3
key-direction 1

<ca>
$(cat $DIR/pki/ca.crt)
</ca>
<cert>
$(cat $DIR/pki/issued/$NAME.crt)
</cert>
<key>
$(cat $DIR/pki/private/$NAME.key)
</key>
<tls-auth>
$(cat $DIR/pki/ta.key)
</tls-auth>
EOF
```

```bash
chmod +x make-ovpn.sh
./make-ovpn.sh client1 > client1.ovpn
```

### Step 3：傳 .ovpn 到 client，連線

```bash
# 在 client 上
sudo openvpn --config client1.ovpn

# 或用 systemd
sudo cp client1.ovpn /etc/openvpn/client/client1.conf
sudo systemctl start openvpn-client@client1
```

確認：

```bash
ip a show tun0
curl ifconfig.me   # VPS IP
```

## 一個常見踩雷：fwd / NAT 沒設

ping VPS IP OK，但連 8.8.8.8 不通。

確認：

```bash
sudo cat /proc/sys/net/ipv4/ip_forward
sudo iptables -t nat -L POSTROUTING -n -v
```

## 一個常見踩雷：client cert 過期 / 撤銷

OpenVPN cert 預設有效期。CA 過期 / cert revocation list (CRL) 也影響。

```bash
# 在 server 端 revoke
./easyrsa revoke client1
./easyrsa gen-crl
```

## 一個常見誤解：「OpenVPN 一定要 TCP 才能穿透」

**錯**。多數場景 UDP OK。**只有極嚴 firewall 才需要 TCP**。

UDP 比 TCP 快得多，能用 UDP 就用。

## 一個常見誤解：「OpenVPN 比 WireGuard 安全」

**部分對**。OpenVPN cert 體系可吊銷，企業管理性強。但**加密強度沒比 WireGuard 強**。

WireGuard 的 default cipher 跟 OpenVPN 自選 AES-256-GCM 等價。

## 動手練習

**1. 完整自架 OpenVPN**

按本章流程在 VPS 上架。

**2. 對比連線時間**

```bash
time openvpn --config client.ovpn   # 等到「Initialization Sequence Completed」
```

跟 WireGuard 比啟動時間（OpenVPN 通常慢 1-2 秒）。

**3. 對比 throughput**

```bash
# OpenVPN 跟 WireGuard 各架，比較 iperf3 結果
```

WireGuard 通常勝。

**4. 試 TCP mode**

把 server config 改：

```
proto tcp
port 443
```

Client config 同樣改。試從嚴 firewall 環境連。

**5. 撤銷 client**

```bash
./easyrsa revoke client1
./easyrsa gen-crl
```

確認該 client 連不上。

## 自我檢核

- [ ] 知道 OpenVPN PKI 體系
- [ ] 自架過 OpenVPN server + client
- [ ] TCP vs UDP mode 知道何時選
- [ ] 跟 WireGuard 比較知道優劣
- [ ] 知道企業環境為什麼還用 OpenVPN

下一章看 IPSec — 企業 site-to-site VPN 主流。

→ [Ch 26 IPSec](./26-ipsec.md)
