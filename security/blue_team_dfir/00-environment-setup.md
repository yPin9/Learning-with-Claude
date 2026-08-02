# Ch 0 — 環境搭建：隔離實驗室與工具鏈

> 目標：在分析任何一個樣本之前，先建好一個不會反咬自己、不會污染生產環境、重建成本低的隔離實驗室（isolated lab），並且把本課程會用到的全部工具鏈安裝到可以立刻跑的狀態。
>
> 環境：分析機 REMnux 或 Ubuntu 22.04 LTS（x86-64）；受害機 Windows 10/11 或 Ubuntu 22.04；VMware Workstation 17 或 VirtualBox 7.x；Host 網路隔離為 Host-only 或 Internal Network；Volatility3 2.7.x、YARA 4.5.x、sigma-cli 1.x / pySigma 0.10.x、Zeek 6.x、Suricata 7.x、Velociraptor 0.7.x、Wazuh 4.8.x、Elastic 8.x。

---

## 為什麼環境搭建是第一章，不是附錄

你做過 pwn 和逆向。你知道壞環境會浪費幾倍的時間——gdb 版本不對、libc 符號載不到、動態連結器路徑亂掉，半天就沒了。DFIR 的壞環境成本更高：分析機被惡意樣本感染、snapshot 沒開、記憶體鏡像（memory image）在 Windows 宿主上觸動 Defender 自動刪除、PCAP 裡的惡意流量從未隔離的 VM 逃出去打到外網。

更根本的問題是**法律面**。你從 MalwareBazaar 拉一個勒索軟體樣本，放在沒隔離的機器上——如果那台機器連到公司網路，你已經在很多司法管轄區違反了電腦犯罪相關法規。本章把「安全下載→隔離分析→快照回滾」整個工作流建起來，之後的每一章才能放心地動手做。

---

## 實驗室架構心智模型

先把整體架構看清楚，再動手裝軟體。

```
┌─────────────────────────────────────────────────────────┐
│                     實體主機 (Host)                      │
│   VMware Workstation / VirtualBox                       │
│                                                         │
│  ┌──────────────────┐   host-only/internal network      │
│  │   分析機          │◄────────────────────────────────┐ │
│  │ REMnux /         │                                  │ │
│  │ Ubuntu 22.04     │   只有這條虛擬網路，              │ │
│  │                  │   對外沒有 route                  │ │
│  │ Volatility3      │                                  │ │
│  │ YARA             │                                  │ │
│  │ sigma-cli        │                                  │ │
│  │ Zeek / Suricata  │                                  │ │
│  │ Velociraptor srv │                                  │ │
│  └──────────────────┘                                  │ │
│                                                         │ │
│  ┌──────────────────┐                                  │ │
│  │   受害機          │◄─────────────────────────────────┘ │
│  │ Windows 10/11    │                                    │
│  │   或              │   Velociraptor agent               │
│  │ Ubuntu 22.04     │   Wazuh agent                      │
│  │                  │   Sysmon（Windows 版）              │
│  └──────────────────┘                                    │
│                                                         │
│  ┌──────────────────┐                                    │
│  │  下載隔離機        │◄──── NAT / 橋接，有外網            │
│  │ （Quarantine VM） │                                    │
│  │                  │  唯一用途：                         │
│  │                  │  下載樣本/PCAP，                    │
│  │                  │  驗證 hash，                        │
│  │                  │  傳入分析機後立刻斷網               │
│  └──────────────────┘                                    │
└─────────────────────────────────────────────────────────┘
```

三個核心原則：

1. **分析機不出網**。分析機的虛擬網路卡只連 host-only 或 internal network，沒有到外部的 default route。`ip route` 輸出裡不該有 `0.0.0.0/0 via <外部閘道>` 這行。
2. **快照先於分析**。每次拿到新樣本，先對分析機和受害機各打一個快照（snapshot），分析完滾回。不信任 VM 工具自動快照，手動打並命名。
3. **樣本走隔離傳輸**。樣本從下載隔離機傳到分析機，只用兩種方式：VMware/VirtualBox 共享資料夾（關閉後立刻移除）或 base64 貼到剪貼簿。不要掛實體 USB，不要開 Samba 共享到 Host。

---

## 分析機：REMnux vs 自建 Ubuntu

REMnux 是由 Lenny Zeltser 維護的 Ubuntu-based 發行版，預裝了幾十個 DFIR 和惡意軟體分析工具。如果你只是跟著本課程做，REMnux 是最省力的起點——許多工具已裝好，版本也測試過共存。

但 REMnux 有個問題：更新週期比上游工具慢，某些章節用到的版本（Volatility3 2.7.x、Suricata 7.x）在 REMnux 預裝版可能偏舊。本章的安裝步驟以「Ubuntu 22.04 LTS 淨裝」為主線，並標注在 REMnux 上需要額外升級的地方。

---

## 建立 Host-only 網路

### VMware Workstation

開啟 Edit → Virtual Network Editor，選一個空的 VMnet（例如 VMnet2），設定為 Host-only，**關閉 DHCP**（我們手動設靜態 IP，避免日後 PCAP 分析時 IP 位址漂移）。

把分析機和受害機的網路卡都接到同一個 VMnet2。Host 本身會有一張對應的虛擬介面，但不會替這個 VMnet 建任何到外部的 NAT 規則。

確認隔離：

```bash
# 在分析機上
ip route show
# 只應該看到類似：
# 192.168.200.0/24 dev ens33 proto kernel scope link src 192.168.200.10
# 沒有 default route

ping 8.8.8.8
# PING 8.8.8.8 ... 100% packet loss  ← 正確
```

### VirtualBox

Host-only Adapter 在 File → Host Network Manager 建立，Internal Network 在 VM 設定頁直接輸入 network 名稱即可。Internal Network 連 Host 也不通，只有 VM 之間互通，隔離程度更高，但你就無法從 Host 用 SSH 連進分析機。依個人習慣選擇，本課程兩種都可以。

---

## 工具安裝：分析機（Ubuntu 22.04）

### 基礎依賴

```bash
sudo apt update && sudo apt install -y \
    python3 python3-pip python3-venv \
    git curl wget unzip \
    libssl-dev libffi-dev build-essential \
    jq net-tools tcpdump tshark \
    libmagic1 libmagic-dev
```

### Volatility3 2.7.x

Volatility3 是本課程記憶體鑑識（memory forensics）的核心工具。它用純 Python 寫成，不依賴特定平台，透過 symbol table（符號表）解析記憶體鏡像。

```bash
python3 -m venv ~/venvs/vol3
source ~/venvs/vol3/bin/activate

pip install volatility3==2.7.0
# 如果 PyPI 上的 2.7.x 還不是 2.7.0，用：
# pip install "volatility3>=2.7,<2.8"

vol -h   # 確認可以跑
```

Windows 記憶體分析需要對應版本的 symbol table，從 Volatility 官方 GitHub 下載壓縮包：

```bash
# 以 Windows 10 20H2 x64 為例
mkdir -p ~/symbols/volatility3
wget -P ~/symbols/volatility3 \
    https://downloads.volatilityfoundation.org/volatility3/symbols/windows.zip
unzip ~/symbols/volatility3/windows.zip -d ~/symbols/volatility3/

# 告訴 Volatility3 去哪找 symbols
echo 'export VOLATILITY_SYMBOL_DIRS="$HOME/symbols/volatility3"' >> ~/.bashrc
source ~/.bashrc
```

快速驗證（用 Volatility 官方測試鏡像）：

```bash
vol -f ~/samples/memory/win10.mem windows.info
# （示意，實際依版本/樣本而異）
# Volatility 3 Framework 2.7.0
# Variable        Value
# Kernel Base     0xf80002a49000
# DTB             0x187000
# ...
```

### YARA 4.5.x

YARA 是靜態特徵掃描（static signature scanning）的事實標準，語法簡單但彈性大，本課程會用它寫獵捕規則（hunting rules）。

```bash
sudo apt install -y libpcre3 libpcre3-dev

pip install yara-python==4.5.0
# 或直接裝 CLI
sudo apt install -y yara   # Ubuntu 22.04 apt 提供的版本可能偏舊
# 如果需要 4.5.x，從原始碼編：
wget https://github.com/VirusTotal/yara/releases/download/v4.5.0/yara-4.5.0.tar.gz
tar xzf yara-4.5.0.tar.gz && cd yara-4.5.0
./bootstrap.sh && ./configure --with-crypto --enable-magic --enable-cuckoo
make -j$(nproc) && sudo make install
yara --version   # 4.5.0
```

### sigma-cli 1.x 與 pySigma 0.10.x

Sigma 是偵測規則（detection rule）的共通語言，本課程第二部分大量使用。sigma-cli 是把 Sigma 規則轉換成各種 SIEM 查詢語言的命令列工具，pySigma 是它的後端函式庫。

```bash
pip install sigma-cli==1.0.4
pip install pySigma==0.10.4
pip install pySigma-backend-elasticsearch
pip install pySigma-backend-splunk
pip install pySigma-pipeline-sysmon

sigma --version   # 應該輸出 1.0.x
```

轉換測試：

```bash
# 把一條 Sigma 規則轉成 Elasticsearch EQL
sigma convert -t elasticsearch-eql \
    /usr/share/sigma/rules/windows/process_creation/proc_creation_win_mimikatz_command_line.yml
# （示意，實際依版本/樣本而異）
# process where process.name == "mimikatz.exe" or
# process.command_line like~ ("*sekurlsa::*", "*lsadump::*", ...)
```

### Zeek 6.x

Zeek（前身 Bro）是網路流量分析（network traffic analysis）的標準平台，它把 PCAP 解析成結構化的 log 檔（conn.log、http.log、dns.log、ssl.log 等），遠比直接看 tshark 輸出容易處理。

```bash
# Ubuntu 22.04 官方 repo 的版本偏舊，用 Zeek 官方 repo
echo 'deb http://download.opensuse.org/repositories/security:/zeek/xUbuntu_22.04/ /' | \
    sudo tee /etc/apt/sources.list.d/zeek.list
curl -fsSL https://download.opensuse.org/repositories/security:zeek/xUbuntu_22.04/Release.key | \
    gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/security_zeek.gpg > /dev/null
sudo apt update && sudo apt install -y zeek-6.0
zeek --version   # Zeek version 6.0.x
```

離線分析 PCAP：

```bash
cd /tmp/analysis
zeek -C -r ~/samples/pcap/lateral_movement.pcap
ls *.log
# conn.log  dns.log  http.log  ssl.log  weird.log  ...
```

### Suricata 7.x

Suricata 是入侵偵測系統（Intrusion Detection System，IDS）引擎，用規則匹配（signature matching）抓出已知惡意流量，搭配 Zeek 用——Zeek 給你高層語義，Suricata 給你告警（alert）。

```bash
sudo add-apt-repository ppa:oisf/suricata-stable
sudo apt update && sudo apt install -y suricata
suricata --version   # Suricata 7.x.x

# 下載 Emerging Threats 開源規則集
sudo suricata-update
sudo suricata-update list-sources   # 看哪些 feed 可用

# 離線掃 PCAP
sudo suricata -c /etc/suricata/suricata.yaml \
    -r ~/samples/pcap/lateral_movement.pcap \
    -l /tmp/suricata-out/
cat /tmp/suricata-out/fast.log
# （示意，實際依版本/樣本而異）
# 08/01/2026-14:23:01.123456  [**] [1:2013031:5] ET POLICY Mimikatz ...
```

### Velociraptor 0.7.x

Velociraptor 是端點（endpoint）蒐證與獵捕（hunting）平台，架構是 server-client。分析機跑 server，受害機跑 agent（client）。它的查詢語言叫 VQL（Velociraptor Query Language），概念上像 SQL 但針對端點鑑識物件（artifact）做查詢。

```bash
# 下載 server binary（Linux x86-64）
wget https://github.com/Velocidex/velociraptor/releases/download/v0.7.1/\
velociraptor-v0.7.1-linux-amd64 -O ~/bin/velociraptor
chmod +x ~/bin/velociraptor

# 產生 self-signed 設定
cd ~/velociraptor
~/bin/velociraptor config generate -i
# 互動式精靈：選 Self Signed SSL，server 位址填分析機 IP（如 192.168.200.10）
# 產生 server.config.yaml 和 client.config.yaml

# 啟動 server（前景跑，測試用）
~/bin/velociraptor --config server.config.yaml frontend -v
# 瀏覽器開 https://192.168.200.10:8889 確認 GUI 可存取
```

受害機（Windows）安裝 agent：

```powershell
# 把 client.config.yaml 複製到受害機後
velociraptor-v0.7.1-windows-amd64.exe --config client.config.yaml service install
```

### Wazuh 4.8.x 與 Elastic 8.x（概覽，不在本章全裝）

Wazuh 是 SIEM（Security Information and Event Management）+ HIDS（Host-based IDS）平台，Elastic 是它的資料後端。本課程 Part 4 會用到完整 stack，但那是重量級部署（記憶體需求 8 GB+），本章只裝 agent 端，server 端在 Ch 16 用 Docker Compose 一鍵起。

```bash
# 先裝 Wazuh agent（分析機或受害機上）
curl -s https://packages.wazuh.com/key/GPG-KEY-WAZUH | gpg --no-default-keyring \
    --keyring gnupg-ring:/usr/share/keyrings/wazuh.gpg --import
chmod 644 /usr/share/keyrings/wazuh.gpg
echo "deb [signed-by=/usr/share/keyrings/wazuh.gpg] https://packages.wazuh.com/4.x/apt/ \
    stable main" | sudo tee /etc/apt/sources.list.d/wazuh.list
sudo apt update && sudo apt install -y wazuh-agent=4.8.0-1
```

Elastic 8.x 的完整安裝在 Ch 16 處理；這裡知道它的角色就夠了：Wazuh 把各端點的事件（event）送到 Wazuh Manager，Manager 把索引化後的資料存到 Elastic，你用 Kibana 的 Wazuh dashboard 查詢。

---

## 受害機設置

### Windows 10/11 受害機

**Sysmon** 是 Windows 端點監控的標準配備，記錄 process 建立（Event ID 1）、網路連線（Event ID 3）、驅動程式載入（Event ID 6）、registry 變更（Event ID 13）等共 30 種事件類型。

```powershell
# 下載 Sysmon
Invoke-WebRequest -Uri https://download.sysinternals.com/files/Sysmon.zip -OutFile Sysmon.zip
Expand-Archive Sysmon.zip

# 用 SwiftOnSecurity 或 Olaf Hartong 的 config（擇一）
Invoke-WebRequest -Uri https://raw.githubusercontent.com/SwiftOnSecurity/sysmon-config/master/sysmonconfig-export.xml `
    -OutFile sysmon-config.xml

.\Sysmon\Sysmon64.exe -accepteula -i sysmon-config.xml
# 確認 service 在跑
Get-Service Sysmon64
```

受害機同樣裝 Velociraptor agent（用上面的 `client.config.yaml`）和 Wazuh agent。這樣分析機的 Velociraptor server 就能即時查詢受害機，Wazuh 也能持續蒐集事件。

### Linux 受害機（Ubuntu 22.04）

```bash
# auditd 是 Linux 端的 event logging 核心
sudo apt install -y auditd audispd-plugins
# 載入 Neo23x0 audit rules（大量覆蓋常見攻擊手法）
wget https://raw.githubusercontent.com/Neo23x0/auditd/master/audit.rules \
    -O /etc/audit/rules.d/audit.rules
sudo augenrules --load && sudo systemctl restart auditd
```

---

## 合法樣本來源與下載隔離工作流

**這是本章最重要的操作規範。** 把它當作 checklist 執行，不要嫌麻煩。

### 合法來源清單

| 來源 | 類型 | 特色 |
|------|------|------|
| MalwareBazaar（abuse.ch） | 惡意軟體二進位 | 有 tag、hash、家族標籤，API 可搜尋 |
| Malware-Traffic-Analysis.net | PCAP + 二進位 | 附場景說明，適合初學者 |
| Volatility Foundation GitHub | 記憶體鏡像 | 官方測試用，已知 ground truth |
| ANY.RUN 公開沙箱 | PCAP、行為報告 | 可下載公開分析的 PCAP |
| Atomic Red Team | 模擬攻擊腳本 | 用來在受害機跑，產出你自己的鑑識材料 |
| CIRCL MISP | 惡意軟體樣本 | 需要申請帳號，適合進階使用 |

### 下載隔離工作流

```
下載隔離機（有外網）
        │
        │ 1. 下載樣本/PCAP
        │    驗證 SHA-256 hash
        │    壓縮（zip 加密，密碼 infected）
        │
        ▼
   複製到共享資料夾
   或 base64 剪貼簿
        │
        ▼
   分析機（無外網）
        │
        │ 2. 打快照（分析機 + 受害機）
        │    解壓縮到 ~/samples/
        │    開始分析
        │
        ▼
   分析完畢
        │
        │ 3. 匯出報告
        │    滾回快照
        │    （或直接保留以備後用）
        ▼
        Done
```

MalwareBazaar 下載範例（在下載隔離機上執行）：

```bash
# 用 API 下載特定 hash 的樣本
curl -X POST https://mb-api.abuse.ch/api/v1/ \
    -d 'query=get_file&sha256_hash=<你想要的hash>' \
    --output sample.zip
# 密碼是 infected
unzip -P infected sample.zip
sha256sum <解壓出的檔案>   # 與查詢的 hash 對比
```

---

## 三個具體範例

### 範例一：用 Volatility3 分析公開記憶體鏡像

```bash
# 從 Volatility Foundation 下載 WannaCry 相關測試鏡像
# 假設已複製到分析機的 ~/samples/memory/wannacry.mem

source ~/venvs/vol3/bin/activate

# 先確認這是什麼 OS
vol -f ~/samples/memory/wannacry.mem windows.info

# 列出所有 process
vol -f ~/samples/memory/wannacry.mem windows.pslist
# （示意，實際依版本/樣本而異）
# PID   PPID  ImageFileName   Offset(V)          Threads Handles
# 4     0     System          0x82a4d020         102     567
# 348   4     smss.exe        0x87a4e020         3       29
# ...
# 2384  1532  tasksche.exe    0x8fa12020         12      85    ← 可疑

# 把可疑 process 的 VAD dump 出來
vol -f ~/samples/memory/wannacry.mem windows.vadump --pid 2384 \
    --dump-dir ~/samples/vaddump/
```

### 範例二：Zeek 解析 C2 通訊 PCAP

從 Malware-Traffic-Analysis.net 下載一個包含 Cobalt Strike beacon 的 PCAP：

```bash
cd /tmp/cobalt-strike-analysis
zeek -C -r ~/samples/pcap/cobalt-strike-beacon.pcap

# 看 SSL log，找非標準 JA3 fingerprint
cat ssl.log | zeek-cut ts uid id.orig_h id.resp_h id.resp_p ja3 ja3s server_name
# （示意，實際依版本/樣本而異）
# 1722480000.0  C3D...  192.168.1.100  203.0.113.5  443
#     72a7c...  b386c...  (空白)
# JA3 是已知 CS default fingerprint，server_name 空白 → SNI 沒設，可疑

# 看連線時間間隔，beacon 的 beacon interval 會很規律
cat conn.log | zeek-cut ts id.orig_h id.resp_h id.resp_p duration | \
    awk '$4 == "443" {print}' | sort -k1
```

### 範例三：失敗案例——Volatility3 找不到 symbol table

這是初學者最常踩的雷。你拿到一個 Windows 11 22H2 的記憶體鏡像，跑：

```bash
vol -f win11-22h2.mem windows.pslist
# ERROR: Kernel not loaded, Cannot find a valid kernel PDB scanning
```

**原因**：Volatility3 的 symbol table 是針對特定 kernel build 的，Windows 更新後 kernel PDB GUID 就不同了。官方下載包不可能包含所有 build。

**解法**：用 `dwarf2json` 搭配 `PDBDownloader` 自動抓對應的 PDB 並轉換：

```bash
pip install pdbparse
# 先用 strings 找鏡像裡的 PDB GUID
strings win11-22h2.mem | grep -A1 "ntkrnlmp.pdb"
# RSDS....  <GUID>  ntkrnlmp.pdb

# 用 Volatility3 內建的 pdbscan 找
vol -f win11-22h2.mem windows.pdbscan
# 拿到 GUID 後去下載對應的 symbol table（或用 symbol server 自動抓）
```

這個失敗案例在實際 IR 案件中很常見：受害機打了 patch，但你的 symbol table 沒更新。記住這個流程。

---

## 方案對比：分析機 OS 選擇

| | REMnux | Ubuntu 22.04 自建 |
|-|--------|-----------------|
| 工具預裝完整度 | 高（數十種） | 零，全手動 |
| 工具版本時效性 | 慢（週期性更新） | 快（直接裝最新） |
| 客製化彈性 | 低（改了可能衝突） | 高 |
| 磁碟空間 | ~20 GB 起 | ~10 GB 起 |
| 適合對象 | 快速入門、工具探索 | 有特定版本需求的進階使用 |
| 本課程相容性 | 部分工具需手動升級 | 完全符合 |

---

## 錯誤直覺 → 正確認識

**錯誤直覺**：「我的 Host 防毒夠強，樣本在 VM 裡跑就算逃出來也沒事。」
**正確認識**：VM escape 是真實存在的攻擊面（我們整個課程就有一門 vm_escape），特別是 QEMU 的 device emulation 漏洞。分析環境要假設 VM escape 是可能的，所以分析機本身也要無外網，Host 上不存任何敏感資料。

**錯誤直覺**：「我用 Volatility3 跑 windows.pslist 看到所有 process，就知道有沒有惡意程式了。」
**正確認識**：DKOM（Direct Kernel Object Manipulation）rootkit 會把 `EPROCESS` 從鏈結串列摘掉，讓 pslist 看不到它。這就是為什麼要同時跑 `windows.psscan`（掃描整個記憶體找 pool tag `_EPROCESS`）和 `windows.cmdline`，再交叉比對差異。Ch 4 會詳細展開。

**錯誤直覺**：「YARA 掃沒匹配，代表這個樣本是乾淨的。」
**正確認識**：YARA 規則的覆蓋率取決於規則庫的新鮮度。新樣本、多型態（polymorphic）變種、加殼（packed）二進位都可能規避規則。沒命中只代表「沒命中已知規則」，不代表沒威脅。

**錯誤直覺**：「Suricata 沒有告警，代表這段流量是正常的。」
**正確認識**：Suricata 的 Emerging Threats 規則主要抓已知 C2 和已知 exploit traffic，Living-off-the-Land（LotL）攻擊、加密 C2、DNS-over-HTTPS tunnel 不會觸發大部分規則。Suricata 是告警用的，Zeek 的 conn.log 才是做行為基線（behavioral baseline）分析的材料。

**錯誤直覺**：「快照打了就安全，分析完不用管了。」
**正確認識**：VMware 的快照會吃磁碟，快照鏈（snapshot chain）太長會讓 VM 效能崩潰。每次分析循環結束後：確認報告已匯出 → 決定是要保留這個分析狀態備查，還是直接滾回 → 如果保留，給快照加描述（樣本名稱 + 日期），不要留空名快照。

---

## 進階擴展

### 自動化快照工作流

用 VMware 的 `vmrun` CLI 把快照操作腳本化：

```bash
VMRUN=/usr/bin/vmrun
VMX=~/VMs/analysis/analysis.vmx

# 分析前打快照
$VMRUN -T ws snapshot "$VMX" "pre-analysis-$(date +%Y%m%d-%H%M%S)"

# 分析完滾回最近一個乾淨快照
$VMRUN -T ws revertToSnapshot "$VMX" "clean-baseline"
```

### 用 Docker 容器補充工具

部分工具（如 Capa、FLOSS、dnSpy）在 Ubuntu 22.04 可能有依賴衝突。用 Docker 隔離：

```bash
# Capa：FLARE team 的靜態能力辨識工具
docker run --rm -v ~/samples:/samples mandiant/capa /samples/suspect.exe
```

### Atomic Red Team 產生你自己的鑑識材料

與其只分析別人公開的鏡像，更好的練習是：用 Atomic Red Team 在受害機上跑一段攻擊模擬，同時讓 Sysmon + Velociraptor 蒐集，然後拿到分析機上做 IR。這樣你對 ground truth（真實發生了什麼）有完整掌握，是最好的 purple team 練習方式。

```powershell
# 在受害機（Windows）上
IEX (IWR 'https://raw.githubusercontent.com/redcanaryco/invoke-atomicredteam/master/install-atomicredteam.ps1' -UseBasicParsing)
Install-AtomicRedTeam -getAtomics

# 跑 T1003.001（LSASS dump）模擬
Invoke-AtomicTest T1003.001
```

---

## 本章重點整理

- 實驗室架構：三層隔離（分析機無外網、受害機只連內網、下載隔離機有外網），快照先於分析。
- 分析機工具鏈：Volatility3 記憶體分析、YARA 靜態特徵、sigma-cli 規則轉換、Zeek 流量語義解析、Suricata IDS 告警、Velociraptor 端點獵捕、Wazuh/Elastic SIEM 後端。
- 樣本來源：MalwareBazaar、Malware-Traffic-Analysis.net、Volatility 官方鏡像、ANY.RUN 公開沙箱、Atomic Red Team；下載後驗 hash、加密壓縮、再傳入分析機。
- 常見失敗點：Volatility3 找不到 symbol table（需對應 PDB）、YARA 沒命中不等於乾淨、Suricata 沒告警不等於沒威脅。

---

## 自我檢核

- [ ] 分析機的 `ip route` 輸出中沒有 default route，`ping 8.8.8.8` 100% 丟包。
- [ ] Volatility3 可以跑 `windows.info` 並且正確識別 OS 版本。
- [ ] YARA 4.5.x 安裝完成，`yara --version` 輸出正確。
- [ ] sigma-cli 可以把一條 Windows process creation 規則轉換成 Elasticsearch EQL。
- [ ] Zeek 對著本機環回介面或一個測試 PCAP 跑完，生出至少 `conn.log`。
- [ ] Suricata 離線掃一個 PCAP，`fast.log` 有輸出（或確認規則集已更新）。
- [ ] Velociraptor server 在分析機上可以存取 GUI，受害機 agent 已連上。
- [ ] 受害機 Windows 版安裝了 Sysmon 並且 service 在跑。
- [ ] 已知道至少兩個合法樣本來源，並走過完整的「下載→驗 hash→加密→傳輸→快照→分析」流程。

---

## 延伸閱讀

1. **FOR508: Advanced Incident Response, Threat Hunting, and Digital Forensics**（SANS Institute）— SANS 的旗艦 DFIR 課程大綱，即使不付費參加，其課程描述和公開的 cheat sheet 就能給你整個 DFIR 流程的框架；本課程的章節順序參考了 FOR508 的架構。

2. **The DFIR Report**（thedfirreport.com）— 真實 IR 案件的詳細技術報告，從初始入侵到橫向移動到 exfiltration，每篇都附 IOC、MITRE ATT&CK mapping、Sigma 規則。訂閱 RSS，每一篇都值得精讀。

3. **Volatility3 Documentation**（volatility3.readthedocs.io）— 官方文件，特別是 symbol table 和 plugin 開發部分；本課程第一部分（記憶體鑑識）的參考基準。

4. **Zeek Documentation: Log Reference**（docs.zeek.org）— 每個 log 欄位的定義。conn.log、http.log、ssl.log 的欄位語義要熟到不用查，這是本課程 Part 3 的地基。

5. **MITRE ATT&CK Framework**（attack.mitre.org）— 本課程每一章的攻擊技術都對應到 ATT&CK 的 Technique ID，建議把 Enterprise Matrix 的 Execution、Persistence、Defense Evasion、Credential Access 四個 Tactic 先瀏覽一遍，知道攻擊技術的全貌，再來看每章的偵測方式。

---

環境搭好之後，我們要先退一步，從高度看清楚：藍隊（blue team）在整個攻防生態裡是什麼角色，purple team 框架怎麼把攻擊知識轉換成偵測知識，以及本課程的學習路徑。

→ [Ch 1 藍隊全貌與 purple team](./01-blue-team-landscape.md)
