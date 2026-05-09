# Ch 2 — Kali Linux 環境建置：工具鏈 + VPN

> 目標：建好一個能連上 HTB / THM / OffSec 靶機的 Kali 環境，確認核心工具能跑。

## 為什麼用 Kali

Kali 是 Debian 衍生的滲透測試發行版，內建幾百個安全工具。它不是唯一選擇（Parrot OS 也行），但它是業界標準，OSCP 考試環境預設你用 Kali。

你可以：
- 在 VirtualBox / VMware 裝 Kali VM（**推薦**，隔離、快照）
- 用 Kali WSL2（Windows 上可以，但部分工具受限）
- 用裸機安裝（不需要，浪費時間）

## 安裝 Kali VM

### 下載

從官方取得：`kali.org/get-kali/` → 選 Virtual Machines → VirtualBox 或 VMware

官方預建 VM 比自己安裝快，推薦直接用。

### VirtualBox 設定

```
VM 規格建議（OSCP 備考用）：
  RAM：4 GB 以上（8 GB 更好，跑 Burp Suite 時有感）
  CPU：2 核心以上
  硬碟：50 GB 以上（工具 + 靶機截圖）
  網路：NAT（日常用）+ Host-Only（連接本地靶機）
```

匯入 OVA：
```
VirtualBox → File → Import Appliance → 選 .ova 檔
→ 記得改 RAM / CPU 到你的硬體能負擔的量
→ Import
```

### 初始設定

```bash
# 更新系統（第一次進去先跑這個）
sudo apt update && sudo apt upgrade -y

# 預設帳密：kali / kali
# 正式使用前改密碼
passwd
```

## 必裝工具確認

Kali 預建版已內建大多數工具，先確認這些能跑：

```bash
# 網路掃描
nmap --version         # 應該要有 7.x

# Web 代理
burpsuite &            # 會開 GUI，確認能啟動

# 漏洞利用框架
msfconsole             # 初始化要等一下，正常

# 密碼破解
hashcat --version
john --version

# 目錄爆破
gobuster version
ffuf -V

# 反彈 shell 輔助
nc -h 2>&1 | head -3   # netcat

# 提權輔助
find / -name linpeas.sh 2>/dev/null   # 若沒有就下載
```

### 下載常用腳本

```bash
# 建立工具目錄
mkdir -p ~/tools

# linPEAS（Linux 提權枚舉）
cd ~/tools
curl -L https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh -o linpeas.sh
chmod +x linpeas.sh

# winPEAS（Windows 提權枚舉）
curl -L https://github.com/carlospolop/PEASS-ng/releases/latest/download/winPEASany.exe -o winPEASany.exe

# PowerUp（Windows 提權）
curl -L https://raw.githubusercontent.com/PowerShellMafia/PowerSploit/master/Privesc/PowerUp.ps1 -o PowerUp.ps1
```

## 連接 HackTheBox VPN

HTB 和 OSCP 考試都用 OpenVPN 的方式把你的 Kali 連進靶機網段。

### 設定 HTB VPN

1. 在 HTB 帳號下載 `.ovpn` 檔（dashboard → Connect to HTB → OpenVPN）
2. 把 ovpn 放進 Kali

```bash
# 啟動 VPN（保持這個終端窗口開著）
sudo openvpn ~/Downloads/your-name.ovpn

# 確認連線成功：看到 Initialization Sequence Completed
# 另開終端確認 tun0 介面
ip addr show tun0
```

成功後你會拿到一個 `10.10.x.x` 的 IP，這是你在 HTB 網段的位址。

### TryHackMe VPN（同理）

THM 也用 OpenVPN，流程一樣。或者用 THM 的網頁版 AttackBox（付費功能，但不用設定 VPN）。

## 常用目錄結構

養成習慣，每個靶機建一個目錄：

```bash
mkdir -p ~/htb/lame/{nmap,exploit,loot}
# 往後你的工作流：
# ~/htb/<機器名>/nmap/   → 存 nmap 輸出
# ~/htb/<機器名>/exploit/→ 存修改過的 exploit 腳本
# ~/htb/<機器名>/loot/   → 存拿到的 flag、憑證、截圖
```

## 設定一個順手的 Terminal

```bash
# 安裝 tmux（分割終端，同時跑多個工作）
sudo apt install tmux

# 基礎 tmux 操作：
# Ctrl+B c      → 新建視窗
# Ctrl+B "      → 上下分割
# Ctrl+B %      → 左右分割
# Ctrl+B 方向鍵  → 切換窗格
```

考試時你會同時跑：nmap 掃描、Burp、exploit 腳本、netcat 監聽——tmux 讓你不用開十個視窗。

## 驗收：第一次連線 HTB

1. 開啟 HTB VPN
2. 在 HTB 啟動 Lame 機器（Easy Linux，永遠在線）
3. 確認能 ping 到機器 IP
4. 跑一次 nmap：

```bash
# 確認環境能用
nmap -sV 10.10.10.3

# 應該要能看到開放的 port 和服務版本
```

能跑到這步，你的環境就 OK 了。

## 自我檢核

- [ ] Kali VM 能開，解析度正常
- [ ] `nmap`, `msfconsole`, `burpsuite` 都能啟動
- [ ] linPEAS / winPEAS 下載到 `~/tools/`
- [ ] HTB VPN 連線成功，能看到 tun0 介面
- [ ] 能 ping 到 Lame 的 IP（10.10.10.3）

→ [Ch 3 Linux 滲透必備：指令、檔案系統、權限](./03-linux-basics.md)
