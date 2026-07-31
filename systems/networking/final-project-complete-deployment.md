# Final Project — 完整部署一台生產 VPS

> **目標**：整合整門課（Ch 0–39）的知識，把一台 VPS 從零打造成一個**完整的、多服務的、安全的生產伺服器**——加固安全、部署 HTTPS 網站、架 WireGuard VPN、配防火牆、並用 tcpdump debug 整個過程。完成後你擁有一台真正屬於自己、提供多種服務、能安全運行在公網的伺服器，並能用本課的所有工具和知識維護、debug、擴展它。這是把整門課從「知識」變成「一台真正在運行的伺服器」的終極整合。

## 專案總覽

你要把一台 VPS 打造成這樣：

```
你的生產 VPS（一個 IP，多種服務）：

  公網
    │
  ┌──────────────────────────────────────────┐
  │  防火牆（Ch 18/35）：只開 SSH/HTTP/HTTPS/WG │
  ├──────────────────────────────────────────┤
  │  SSH（Ch 34/35）：金鑰登入、加固           │ ← 你管理用
  ├──────────────────────────────────────────┤
  │  nginx（Ch 36）：HTTPS reverse proxy       │
  │    → 後端網站/API（只聽 127.0.0.1, Ch 13） │ ← 對外服務
  │    → Let's Encrypt 憑證（Ch 11）           │
  ├──────────────────────────────────────────┤
  │  WireGuard（Ch 24）：VPN 伺服器            │ ← 你的私人 VPN
  │    → NAT 讓 VPN 客戶端上網（Ch 8/18）       │
  ├──────────────────────────────────────────┤
  │  fail2ban（Ch 35）：自動封鎖攻擊者          │
  │  自動更新（Ch 35）、監控、備份             │
  └──────────────────────────────────────────┘
        │
  → 一台 VPS = HTTPS 網站 + 私人 VPN + 安全加固
    全部用本課的知識建構和維護
```

這台伺服器整合了：安全（Part 8）、服務部署（Ch 36）、VPN（Ch 24）、防火牆（Ch 18）、以及用全課的工具（tcpdump/ss/nmap/curl）來建構和 debug。它是整門課的具體成果。

## 為什麼做這個專案？

這是 DevOps/SRE 的核心能力——把一台裸機變成一個安全、多服務、可維護的生產伺服器。前面的練習各做了一塊（練習 C 架 VPN、練習 D 部署網站），這個 Final 把它們整合成「一台真正的生產伺服器」，並加上完整的安全、監控、和 debug。

完成它，你獲得：一台真正屬於自己、提供多種服務的伺服器（可以實際使用——你的網站、你的 VPN）、把整門課知識整合應用的經驗、以及「能獨立建構和維護生產伺服器」的能力。這是能寫進履歷、能向人展示、能實際使用的成果。最重要的——你會發現整門課的知識怎麼**協同**運作，從一個封包的旅程（Ch 1）到一台運行的伺服器。

## 整合的課程概念

| 元件 | 整合的章節 |
|---|---|
| VPS 選擇與初始設定 | Ch 32, 33 |
| SSH 加固與管理 | Ch 12, 34, 35 |
| 防火牆 | Ch 18, 19, 35 |
| HTTPS 網站部署 | Ch 9, 11, 28, 36 |
| WireGuard VPN | Ch 8, 18, 21, 23, 24 |
| 後端服務隔離 | Ch 13, 31 |
| 用工具 debug | Ch 14, 16, 17（tcpdump/mtr/nmap/curl）|
| 網路基礎理解 | Part 2-3（貫穿）|
| 監控與維護 | Ch 31, 35 |

整門課至少 70% 的核心概念都用上了——這是 Final Project 的標準。

## 任務規格

把一台 VPS 打造成完整的生產伺服器：

### 必做（核心）

1. **VPS 與初始設定**（Ch 33）：買/用一台 KVM VPS、非 root 使用者、SSH 金鑰、更新系統
2. **SSH 加固**（Ch 35）：金鑰登入、關閉密碼/root 登入、改 port、fail2ban
3. **防火牆**（Ch 18/35）：白名單（只開 SSH/HTTP/HTTPS/WireGuard），持久化
4. **HTTPS 網站**（Ch 36）：DNS 指向、nginx reverse proxy、Let's Encrypt、後端只聽本機、systemd 自啟
5. **WireGuard VPN**（Ch 24）：伺服器端、至少一個客戶端、全流量走 VPN、NAT 出網
6. **自動更新 + 監控**（Ch 35）：unattended-upgrades、定期檢查 log

### 驗證（必做）

7. **安全驗證**（Ch 17/35）：nmap 從外部掃描，確認攻擊面正確（只該開的 port 開）
8. **服務驗證**：HTTPS 網站正常（SSL Labs A）、VPN 連得上且 IP 變了
9. **debug 演練**（Ch 14）：用 tcpdump 抓並分析至少一個服務的封包（驗證理解）

### 驗收標準

- `https://你的域名` 正常訪問，SSL Labs A 級
- WireGuard 客戶端連上，`curl ifconfig.me` 顯示 VPS IP，無 DNS 洩漏
- `nmap` 外部掃描：只有預期的 port 開放，後端/敏感服務不對外
- 重開機後所有服務自動恢復
- fail2ban 在運作（看得到封鎖記錄）
- 能用 tcpdump 解釋任一服務的封包流
- 能說出每個元件用到課程的哪些概念

## 如果你卡住了

1. 分階段做：先加固（必須最先，Ch 35）→ 網站 → VPN，每個都驗證再下一個
2. 加固先行：VPS 一上線就被攻擊（Ch 33），先做 SSH 加固和防火牆，別急著部署
3. 防火牆別鎖死：先開 SSH 再 enable，改 port 要開新 port（Ch 18/35）
4. WireGuard 和 nginx 不衝突：WireGuard 用 UDP 51820、nginx 用 TCP 80/443，各自的 port
5. 用前面的練習：練習 C（VPN）、練習 D（網站）是這個的組件，直接拿來整合
6. 出問題就抓封包：tcpdump 是你的終極 debug 工具（Ch 14）
7. 每個服務都驗證「從外部」能用（Ch 17 的 nmap/curl），別只在本機測

## 實作步驟建議

### Step 1：VPS 初始設定 + 安全加固（最先！Ch 33/35）
### Step 2：防火牆白名單（Ch 18/35）
### Step 3：HTTPS 網站部署（Ch 36，整合練習 D）
### Step 4：WireGuard VPN（Ch 24，整合練習 C）
### Step 5：監控/自動更新 + 全面驗證（nmap/tcpdump/SSL Labs）

## 完整參考解答

**這是 Final Project，務必自己整合！** 下面是整合的架構和關鍵點，細節參考各章和練習 C/D。

<details>
<summary>整合部署的架構與檢查清單</summary>

```bash
# ========== Step 1：初始設定 + SSH 加固（Ch 33/35）==========
# （參考 Ch 33：建非 root 使用者、金鑰、更新）
# （參考 Ch 35：關閉密碼/root 登入、改 SSH port）
# /etc/ssh/sshd_config:
#   PasswordAuthentication no
#   PermitRootLogin no
#   Port 2222
#   AllowUsers deploy
sudo systemctl restart sshd          # （先測試能登入！）

# fail2ban
sudo apt install -y fail2ban
# /etc/fail2ban/jail.local: [sshd] enabled, port=2222

# 自動更新
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

# ========== Step 2：防火牆白名單（Ch 18/35）==========
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 2222/tcp              # SSH（改的 port！）
sudo ufw allow 80/tcp                # HTTP（certbot 驗證 + 跳轉）
sudo ufw allow 443/tcp               # HTTPS
sudo ufw allow 51820/udp             # WireGuard
sudo ufw enable                      # （確認 SSH port 開了！）

# ========== Step 3：HTTPS 網站（Ch 36，整合練習 D）==========
# DNS A 記錄指向 VPS（Ch 9）→ dig 驗證
# 後端服務（聽 127.0.0.1:3000）+ systemd service（Ch 31）
# nginx reverse proxy → 後端
# certbot --nginx -d example.com --redirect   （Let's Encrypt + 跳轉）

# ========== Step 4：WireGuard VPN（Ch 24，整合練習 C）==========
# /etc/wireguard/wg0.conf:
#   [Interface] Address=10.66.66.1/24, ListenPort=51820, PrivateKey
#   PostUp MASQUERADE（NAT 出網，Ch 18）
#   [Peer] 客戶端公鑰, AllowedIPs=10.66.66.2/32
# sysctl ip_forward=1
# wg-quick up wg0 + systemctl enable wg-quick@wg0

# ========== Step 5：全面驗證 ==========
# 1. 安全：nmap 從外部掃描（Ch 17/35）
nmap -p 22,2222,80,443,3000,51820 your-vps-ip
# 2222 open(SSH), 80/443 open(web), 51820 open(WG)
# 22 filtered(改了), 3000 filtered(後端不對外) ✓

# 2. 網站：HTTPS + SSL Labs
curl -I https://example.com          # 200
# ssllabs.com 測試 → A

# 3. VPN：連上 + IP 變了 + 無洩漏
# （客戶端連 WireGuard）→ curl ifconfig.me 顯示 VPS IP

# 4. debug 演練：用 tcpdump 看服務封包（Ch 14）
sudo tcpdump -i any -n 'tcp port 443' -c 10    # 看 HTTPS 流量
sudo tcpdump -i wg0 -n -c 10                    # 看 VPN 內部（明文）
sudo tcpdump -i eth0 -n udp port 51820 -c 10    # 看 VPN 隧道（加密）

# 5. 重開機驗證
sudo reboot
# 重連後確認所有服務恢復（nginx/wireguard/後端 都 systemd enable）
```

**整合的關鍵點**：

- **加固最先**（Ch 33/35）：VPS 一上線就被攻擊，安全是部署的前提不是後續
- **防火牆協調**：SSH(2222)/HTTP(80)/HTTPS(443)/WireGuard(51820) 各自的 port，互不衝突
- **服務隔離**（Ch 13）：後端只聽 127.0.0.1（nginx proxy）、VPN 在自己的網段——敏感的不對外
- **全部 systemd enable**（Ch 31）：nginx/wireguard/後端 都要開機自啟，重開機才會恢復
- **NAT 協調**：WireGuard 的 MASQUERADE（Ch 18）讓 VPN 客戶端出網，和 nginx 不衝突
- **驗證從外部**（Ch 17）：nmap/curl 從外部測，確認「真的對外能用且安全」，不只本機測
- **tcpdump 貫穿**（Ch 14）：用它驗證每個服務的封包流，理解「實際發生什麼」

</details>

## 測試用案例

| 操作 | 預期 | 驗證的整合 |
|---|---|---|
| `https://域名` + SSL Labs | A 級 | Ch 11/36 HTTPS |
| WireGuard 連上 + ifconfig.me | VPS IP | Ch 24 VPN |
| `nmap` 外部掃描 | 只該開的 port | Ch 17/35 安全 |
| `ss -tlnp` | 後端 127.0.0.1 | Ch 13 隔離 |
| 重開機 | 全服務恢復 | Ch 31 systemd |
| fail2ban status | 有封鎖記錄 | Ch 35 防護 |
| tcpdump 分析 | 能解釋封包流 | Ch 14 理解 |
| SSH 密碼登入 | 被拒 | Ch 35 加固 |

## 延伸挑戰（加分）

- **挑戰一**：多網站——用多個 server block 部署多個域名/服務（虛擬主機），每個獨立 HTTPS

- **挑戰二**：監控儀表板——自架 Uptime Kuma 或 Grafana+Prometheus，監控服務狀態和系統指標（接 observability 課）

- **挑戰三**：CI/CD 自動部署——GitHub Actions，push 程式碼自動 rsync/ssh 部署到 VPS（Ch 34）

- **挑戰四**：容器化——把後端服務用 Docker 跑（接 docker 課），nginx proxy 到容器，理解容器網路（Ch 37）

- **挑戰五**：自動備份——寫 backup 腳本（接 linux_commands 課的練習 D），定期備份設定和資料，cron/systemd timer 排程

- **挑戰六**：完整 debug 報告——對你的伺服器做一次完整的「健康檢查」：抓封包分析每個服務、用 mtr 測網路品質、用 nmap 稽核攻擊面、檢查 log，寫成一份報告（整合 Part 4 所有工具）

- **挑戰七**：IPv6——讓所有服務也支援 IPv6（Ch 38），設 AAAA 記錄、IPv6 防火牆，dual-stack

## 自我檢核

完成這個專案後，你應該能回答：

- [ ] 我能把一台裸 VPS 打造成安全、多服務的生產伺服器
- [ ] 我理解每個服務（HTTPS/VPN）底層怎麼運作，能用 tcpdump 解釋它的封包流
- [ ] 我能從外部驗證伺服器的安全（攻擊面）和服務（可用性）
- [ ] 面試被問「你怎麼部署和保護一台伺服器」，我能展示這台伺服器和背後的思路
- [ ] 我理解這台伺服器怎麼整合了從「一個封包」（Ch 1）到「全球網路」（BGP）的所有知識

## 結語：你現在站在哪裡

完成這門課和這個專案，你已經從「網路的使用者」變成「網路的理解者和建構者」。你知道：

- 一個封包從你的瀏覽器到伺服器的完整旅程（Ch 1），以及每一層怎麼運作（Part 2-3）
- 怎麼用工具「看見」網路（Part 4：tcpdump/dig/mtr/nmap），debug 任何網路問題
- Linux 怎麼處理封包（Part 5：防火牆/虛擬網路），以及容器網路的底層
- VPN 怎麼運作（Part 6），審查與反審查的攻防（Part 7）——TCP/TLS/流量分析的極致應用
- 怎麼買一台 VPS、加固它、部署服務、架 VPN（Part 8）——成為網路的提供者
- 傳輸層的未來（QUIC）和全球網路的骨架（BGP）（Part 9）

這些不是「會設定」，是**理解**。你能在任何陌生的網路問題前，沿著「一個封包的旅程」逐層推理出解法——這正是資深網路工程師和「只會照教學抄」的人的根本差異。

接下來往哪去？這門課的「精選資料庫」（見 [README](./README.md)）列了進階方向：《TCP/IP Illustrated》把協定推到極致、High Performance Browser Networking 講效能、Brendan Gregg 的 eBPF 觀測網路（接 bpf 課）。但更重要的是——**去用它、去維護它、去 debug 它**。你的 VPS 在公網運行，會遇到真實的問題；用 tcpdump 挖底層、用學到的知識解決它們。網路的功力是在真實問題裡磨出來的。

你現在擁有一台真正屬於自己的伺服器，和理解它每一個位元的能力。恭喜你走到這裡。
