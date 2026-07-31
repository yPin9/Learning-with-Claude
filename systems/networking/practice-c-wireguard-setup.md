# 練習 C — 架一個 WireGuard VPN

> **目標**：整合 Part 5-6 的知識，完整架一個能用的 WireGuard VPN——伺服器端（VPS 或一台機器）+ 客戶端，讓客戶端的流量透過 VPN 加密出網。你要處理所有實務問題：金鑰、AllowedIPs、NAT（Ch 18）、ip_forward、DNS（防洩漏）、MTU、防火牆。完成後你有一個真正屬於自己的 VPN，並徹底理解它每一部分為什麼這樣設定。這是 Final Project（完整 VPS 部署）的核心元件。

## 背景與動機

你學完了 VPN 原理（Ch 23）和 WireGuard（Ch 24）。現在親手架一個。這不是「跟著抄設定」——而是理解每一行為什麼存在，並在出問題時能 debug。

這正是真實的 VPN 部署：你買一台 VPS（或用一台有公網 IP 的機器），架 WireGuard，讓你的手機/電腦透過它加密上網。完成後你獲得：一個自己掌控的 VPN（沒有第三方看你流量，Ch 23）、徹底理解 VPN 的每個環節（金鑰/路由/NAT/DNS）、debug VPN 問題的能力。這個練習把前面所學（tun/加密/NAT/路由/防火牆）全部用上，是 Part 5-6 的綜合驗收，也是 Final Project 的關鍵部分。

## 任務規格

架一個完整的 WireGuard VPN，達成以下目標：

| 目標 | 涉及 |
|---|---|
| 伺服器端 WireGuard 設定 + 啟動 | Ch 24 |
| 客戶端設定 + 連線 | Ch 24 |
| 客戶端全流量走 VPN（AllowedIPs=0.0.0.0/0）| Ch 24 |
| NAT 讓客戶端流量出外網 | Ch 8/18 |
| 開啟 ip_forward | Ch 0/18 |
| 防火牆開 WireGuard port（UDP 51820）| Ch 18 |
| DNS 走 VPN（防 DNS 洩漏）| Ch 9/23 |
| 驗證：客戶端公網 IP = 伺服器 IP | Ch 8 |
| 驗證：無 DNS 洩漏 | Ch 9 |

**驗收標準**：
- 客戶端連上 WireGuard（`wg` 顯示握手）
- 客戶端能正常上網（透過 VPN）
- 客戶端 `curl ifconfig.me` 顯示**伺服器的公網 IP**（證明流量走 VPN）
- DNS leak test 通過（DNS 也走 VPN）
- 重開機後 VPN 自動恢復（systemd enable）
- 能說出每個設定的作用

## 環境準備

```
你需要：
  1. 伺服器：一台有「公網 IP」的機器
     - 最好是 VPS（Part 8 會教買，~$5/月）
     - 或：你能設 port forwarding 的家用網路（Ch 8）
     - 不能是純 NAT 後面（客戶端連不進來，Ch 8）
        │
  2. 客戶端：你的電腦/手機
     - Linux/Mac/Windows/iOS/Android 都有 WireGuard app
        │
  沒有 VPS 也能練：用兩個 netns（Ch 20-22）模擬「伺服器」和「客戶端」
  （學設定的邏輯，雖然不是真的跨網路）
```

## 如果你卡住了

1. 先確認伺服器有公網 IP 且能被連到（`curl ifconfig.me` 看公網 IP，`nc -zvu` 測 UDP port）
2. 金鑰配對：伺服器的 [Peer] PublicKey 是「客戶端的公鑰」，客戶端的 [Peer] PublicKey 是「伺服器的公鑰」（最常配錯）
3. 連不上（沒握手）：檢查伺服器防火牆有沒有開 UDP 51820（Ch 18，超常忘）、Endpoint IP 對不對
4. 連上但沒網：檢查 ip_forward（=1）和 MASQUERADE（Ch 18/23）
5. DNS 不通：客戶端設 DNS = 1.1.1.1，或檢查伺服器的 DNS 轉發
6. 傳大檔案卡：MTU 問題（Ch 4），客戶端 MTU 設 1380 試試
7. 用 `sudo wg` 看握手狀態，是 debug 的第一步

## 實作步驟建議

### Step 1：產生伺服器和客戶端的金鑰對
### Step 2：伺服器端設定（Interface + Peer + NAT + forward）
### Step 3：開防火牆 + 啟動伺服器端
### Step 4：客戶端設定 + 連線
### Step 5：驗證（IP 變了 + 無 DNS 洩漏）+ 持久化

## 完整參考解答

**自己架一次再看！** 親手踩坑才學得到 debug。

<details>
<summary>完整設定（伺服器 + 客戶端）</summary>

### 伺服器端（在 VPS 上）

```bash
# === Step 1：安裝 + 產生金鑰 ===
sudo apt update && sudo apt install -y wireguard
cd /etc/wireguard
umask 077    # 確保金鑰檔權限安全（Ch 28 的權限）

# 伺服器金鑰
wg genkey | sudo tee server_private.key | wg pubkey | sudo tee server_public.key
# 客戶端金鑰（也可在客戶端產生，這裡集中產生方便）
wg genkey | sudo tee client_private.key | wg pubkey | sudo tee client_public.key

SERVER_PRIV=$(sudo cat server_private.key)
CLIENT_PUB=$(sudo cat client_public.key)
# 找出對外網卡名（通常 eth0/ens3）
WAN_IF=$(ip route get 1.1.1.1 | grep -oP 'dev \K\S+')

# === Step 2：伺服器設定 ===
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = $SERVER_PRIV
# NAT：讓客戶端流量出外網（Ch 18 MASQUERADE）
PostUp = iptables -t nat -A POSTROUTING -s 10.66.66.0/24 -o $WAN_IF -j MASQUERADE
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -A FORWARD -o wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s 10.66.66.0/24 -o $WAN_IF -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -o wg0 -j ACCEPT

[Peer]
PublicKey = $CLIENT_PUB
AllowedIPs = 10.66.66.2/32
EOF

# === Step 3：開 forward + 防火牆 + 啟動 ===
# 開啟 IP 轉發（永久，Ch 0/18）
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-wireguard.conf
sudo sysctl -p /etc/sysctl.d/99-wireguard.conf

# 防火牆開 WireGuard port（Ch 18，依你的防火牆工具）
sudo ufw allow 51820/udp 2>/dev/null || \
    sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT

# 啟動 + 開機自啟
sudo wg-quick up wg0
sudo systemctl enable wg-quick@wg0

# 確認運作
sudo wg                          # 看 interface
ss -ulnp | grep 51820            # 確認在監聽 UDP 51820
```

### 客戶端

```bash
# 在客戶端取得：伺服器公鑰、伺服器公網 IP、客戶端私鑰
# （從伺服器複製 client_private.key 和 server_public.key 過來，安全傳輸）

# 客戶端設定（Linux：/etc/wireguard/wg0.conf；手機：用 app 掃 QR）
cat > wg0-client.conf <<EOF
[Interface]
Address = 10.66.66.2/24
PrivateKey = <客戶端私鑰>
DNS = 1.1.1.1                    # DNS 走 VPN（防洩漏，Ch 9/23）

[Peer]
PublicKey = <伺服器公鑰>
Endpoint = <伺服器公網IP>:51820
AllowedIPs = 0.0.0.0/0          # 全流量走 VPN（Ch 24）
PersistentKeepalive = 25        # NAT 後防斷（Ch 8）
EOF

# 連線
sudo wg-quick up ./wg0-client.conf

# 手機：用 wireguard app，掃 QR code（伺服器端生成 QR）：
# qrencode -t ansiutf8 < wg0-client.conf
```

### Step 5：驗證

```bash
# 1. 握手成功？
sudo wg                          # latest handshake 有時間 = 連上了

# 2. 公網 IP 變成伺服器的了？（證明流量走 VPN）
curl -s ifconfig.me              # 應該顯示「伺服器的公網 IP」，不是你原本的！

# 3. DNS 沒洩漏？
# 連 https://dnsleaktest.com 做測試
# 或：curl -s https://1.1.1.1/cdn-cgi/trace | grep ip
# DNS 查詢應該都經過 VPN

# 4. 能正常上網？
curl -sI https://example.com | head -1   # HTTP/2 200

# 5. 驗證雙層封包（Ch 21/24）
# 在 wg0 抓 → 明文；在實體網卡抓 → 加密 UDP
```

**解答說明**：

- **金鑰配對**（Ch 24）：伺服器 [Peer] 放客戶端公鑰、客戶端 [Peer] 放伺服器公鑰——配錯就連不上（最常見錯誤）
- **AllowedIPs**（Ch 24 的雙重意義）：伺服器端 `10.66.66.2/32`（這個客戶端的 VPN IP，存取控制）；客戶端端 `0.0.0.0/0`（全流量走 VPN，路由）
- **NAT/MASQUERADE**（Ch 18/8）：讓客戶端的私有 VPN IP（10.66.66.x）能出外網——漏了就「連上沒網」（Ch 23 通用問題）
- **FORWARD 規則**：如果伺服器防火牆 FORWARD 預設 DROP，要明確允許 wg0 的轉發（Ch 18）
- **ip_forward**（Ch 0/18）：伺服器要當路由器轉發封包——漏了也是「連上沒網」
- **防火牆開 UDP 51820**（Ch 18）：VPS 防火牆預設擋——漏了就「連不上」（沒握手）
- **DNS = 1.1.1.1**（Ch 9/23）：讓 DNS 查詢也走 VPN，防止 DNS 洩漏（流量走 VPN 但 DNS 沒走 = 洩漏你訪問什麼）
- **PersistentKeepalive**（Ch 8）：客戶端在 NAT 後面時，定期發包餵活 NAT 表，防閒置斷線
- **驗證 ifconfig.me**（Ch 8）：顯示伺服器 IP = 流量確實走 VPN 出去（NAT 把來源換成伺服器）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `sudo wg` | latest handshake 有時間 | 連線成功 |
| `curl ifconfig.me` | 顯示伺服器 IP | 流量走 VPN |
| dnsleaktest.com | 只看到 VPN 的 DNS | 無 DNS 洩漏 |
| 故意漏 MASQUERADE | 連上但沒網 | NAT 的作用 |
| 故意漏防火牆 | 連不上（沒握手）| 防火牆 port |
| 金鑰配錯 | 連不上 | 公私鑰配對 |
| 重開機 | VPN 自動恢復 | systemd enable |

## 延伸挑戰（加分）

- **挑戰一**：多客戶端——加第二、三個客戶端（手機+電腦+平板），每個獨立金鑰和 VPN IP，理解 [Peer] 的擴展

- **挑戰二**：split tunnel——把客戶端 AllowedIPs 改成只有特定網段（如只有公司內網走 VPN，其他走原本網路），對比全流量模式

- **挑戰三**：kill switch（Ch 23）——設定「VPN 斷線時擋掉所有流量」（用 iptables），防止 VPN 掛掉時流量裸奔洩漏

- **挑戰四**：QR code + 手機——用 `qrencode` 把客戶端設定生成 QR，手機 WireGuard app 掃描連線

- **挑戰五**：wg-easy——裝 wg-easy（Web UI）管理 WireGuard，對比手動設定，理解工具自動化了什麼

- **挑戰六**：debug 演練——故意製造每個常見問題（漏 NAT/漏防火牆/金鑰錯/DNS 洩漏），用 Ch 24 的排查流程修復，練 debug

## 自我檢核

- [ ] 能從零架一個能用的 WireGuard VPN（伺服器+客戶端）
- [ ] 理解每個設定的作用（金鑰/AllowedIPs/NAT/forward/DNS/keepalive）
- [ ] 能驗證 VPN 真的生效（IP 變了 + 無 DNS 洩漏）
- [ ] 能 debug 常見問題（連不上/連上沒網/DNS 洩漏/卡住）
- [ ] 理解這個 VPN 怎麼綜合了 Part 5-6 的知識（tun/加密/NAT/路由/防火牆）

這個練習做出了一個真正屬於你的 VPN。接下來 Part 7 進入翻牆生態——當「裸 VPN」被審查封鎖時，專門的工具（Shadowsocks/V2Ray）怎麼對抗，這是把 TCP/TLS/DPI 知識推到極致的攻防。

→ [Ch 28 HTTP proxy 與 SOCKS5](./28-http-proxy-socks5.md)
