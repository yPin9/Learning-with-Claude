# 藍隊與 DFIR 學習筆記：把你的攻擊武器庫反轉成偵測、鑑識與溯源

> 給已經會攻擊、但不會查案的紅隊/資安工程師。學完你能站在防守方，把一場入侵從記憶體、磁碟、網路、日誌裡完整還原，並補上抓得住它的偵測規則。

這系列用 **purple team** 框架教藍隊：你會的每一招攻擊，反過來學它在防守方留下什麼痕跡、怎麼被偵測、怎麼被鑑識、怎麼被溯源。工具涵蓋 Volatility3 / YARA / Sigma / Zeek / Suricata / Velociraptor / Wazuh，跨 Windows / Linux / 雲 / K8s / 網路四個平台。學完你補上的是「另一半視角」——不再只會打，也會查、會防、會寫報告。

## 為什麼學這個？

- **視角補完**：能寫 exploit 卻不知道它怎麼被抓，等於只看得見半個戰場。學會偵測與鑑識，你的攻擊功力會再上一層——因為你知道防守方在看什麼。
- **底層理解的價值**：DFIR 逼你搞懂 OS 在被攻擊時到底留下什麼——記憶體結構、檔案系統 journal、Registry hive、Event Log、封包指紋。這些是攻防雙方共用的地基。
- **職涯**：SOC / IR / Detection Engineering / Threat Hunting 是資安需求最大的職缺群，而「懂攻擊的藍隊」稀缺且值錢。

## 先修知識

- 作業系統概念：process / memory / 檔案系統 / syscall（程度：能讀 kernel_internals 那種深度最好，但不強求）
- 攻擊面基本認識：至少知道 persistence、lateral movement、C2、credential dumping 是什麼（你既有的 pwn/pentest/AD 課全部用得上）
- 命令列與基本 scripting（Python / PowerShell / bash 任一）
- 沒有也沒關係的：完整的鑑識背景、法律訓練——課程會補

## 課程地圖

### Part 0 — 地基與心法（Ch 0–4）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 藍隊全貌與 purple team](./01-blue-team-landscape.md)
- [Ch 2 攻擊者視角轉防守：MITRE ATT&CK](./02-attacker-to-defender-attack.md)
- [Ch 3 IR 生命週期 PICERL](./03-ir-lifecycle-picerl.md)
- [Ch 4 證據可信度與鑑識報告](./04-evidence-forensic-soundness.md)

### Part 1 — Detection Engineering 偵測工程（Ch 5–12）
- [Ch 5 遙測從哪來：log source 全景](./05-telemetry-log-sources.md)
- [Ch 6 Windows 遙測地基：Sysmon 與 ETW](./06-windows-telemetry-sysmon-etw.md)
- [Ch 7 偵測邏輯：IOC vs IOA vs 行為偵測](./07-detection-logic-ioc-ioa.md)
- [Ch 8 Sigma 規則工程](./08-sigma-rule-engineering.md)
- [Ch 9 YARA 規則工程](./09-yara-rule-engineering.md)
- [Ch 10 ATT&CK 對映與偵測涵蓋度](./10-attack-mapping-coverage.md)
- [Ch 11 SIEM 架構與 detection pipeline](./11-siem-detection-pipeline.md)
- [Ch 12 Detection-as-Code](./12-detection-as-code.md)
- [練習 A：對已知攻擊技術寫 Sigma + YARA 偵測](./practice-a-write-detections.md)

### Part 2 — Windows Endpoint DFIR（Ch 13–20）
- [Ch 13 Windows 記憶體鑑識入門](./13-windows-memory-forensics-intro.md)
- [Ch 14 記憶體鑑識進階：注入與 hollowing](./14-windows-memory-forensics-advanced.md)
- [Ch 15 檔案系統 artifacts：$MFT/$UsnJrnl/$LogFile](./15-windows-filesystem-artifacts.md)
- [Ch 16 執行痕跡：Prefetch/AMCache/ShimCache/SRUM](./16-windows-execution-artifacts.md)
- [Ch 17 Registry 鑑識](./17-windows-registry-forensics.md)
- [Ch 18 Event Log 鑑識](./18-windows-event-log-forensics.md)
- [Ch 19 持久化偵測全景](./19-persistence-detection.md)
- [Ch 20 憑證竊取與橫向移動鑑識](./20-credential-lateral-movement-forensics.md)
- [練習 B：Windows 被入侵主機完整 triage + timeline](./practice-b-windows-triage-timeline.md)

### Part 3 — Linux / 網路 / 雲 DFIR（Ch 21–26）
- [Ch 21 Linux IR triage](./21-linux-ir-triage.md)
- [Ch 22 Linux 記憶體鑑識 + auditd/eBPF 偵測](./22-linux-memory-auditd-ebpf.md)
- [Ch 23 Linux 檔案系統鑑識與 rootkit 偵測](./23-linux-filesystem-rootkit.md)
- [Ch 24 網路鑑識：Zeek/Suricata/PCAP/C2](./24-network-forensics.md)
- [Ch 25 雲 IR：CloudTrail/GuardDuty](./25-cloud-ir.md)
- [Ch 26 容器 / K8s IR](./26-container-k8s-ir.md)
- [練習 C：網路 + 主機關聯追 C2 beacon](./practice-c-network-host-correlation.md)

### Part 4 — Threat Hunting 主動狩獵（Ch 27–30）
- [Ch 27 Hunting 方法論](./27-threat-hunting-methodology.md)
- [Ch 28 用資料狩獵：KQL/SPL 查詢思維](./28-hunting-with-data.md)
- [Ch 29 狩獵常見 TTP：LOLBins/PowerShell/WMI](./29-hunting-common-ttps.md)
- [Ch 30 對抗規避：偵測 AMSI bypass/unhooking](./30-detecting-evasion.md)
- [練習 D：hypothesis-driven 狩獵](./practice-d-threat-hunt.md)

### Part 5 — 惡意程式與反鑑識對抗（Ch 31–34）
- [Ch 31 惡意程式鑑識分類與行為分析](./31-malware-forensics-triage.md)
- [Ch 32 Fileless / in-memory 威脅偵測](./32-fileless-inmemory-detection.md)
- [Ch 33 反鑑識對抗](./33-anti-forensics-detection.md)
- [Ch 34 偵測工程的盲點](./34-detection-blind-spots.md)

### Part 6 — 營運、情報與整合（Ch 35–38）
- [Ch 35 事件分級、alert triage、SOAR](./35-alert-triage-soar.md)
- [Ch 36 威脅情報整合](./36-threat-intelligence.md)
- [Ch 37 事後報告與 MTTD/MTTR 指標](./37-post-incident-reporting.md)
- [Ch 38 建立 purple team 演練循環](./38-purple-team-exercise-loop.md)
- [Final Project：完整入侵事件調查](./final-project-full-incident-investigation.md)

## 學習方式建議

1. **讀完一章就對照攻擊**：每學一個偵測/鑑識點，回想你在哪門攻擊課做過對應的招，想「我這樣打，防守方會看到什麼」。
2. **故意攻擊自己再查**：能跑的地方，在 VM 裡執行一次攻擊（Atomic Red Team 現成），再用本章工具去抓——這是 purple team 的精髓。
3. **讀底層資料**：記憶體鑑識、檔案系統 artifact 這些，官方文件與原始研究比二手教程可靠得多，每章延伸閱讀都指了路。

## 精選資料庫

### 必讀基礎

- **《The Art of Memory Forensics》** — Ligh, Case, Levy, Walters（Wiley, 2014）
  - 記憶體鑑識的聖經；Part 2 記憶體章節的主要參考，Windows/Linux/Mac 都涵蓋
- **[MITRE ATT&CK](https://attack.mitre.org/)**
  - 攻防共同語言；整門課的骨架，偵測涵蓋度、狩獵假設、報告對映全靠它
- **[Sigma HQ](https://github.com/SigmaHQ/sigma)** 與 **[YARA 官方文件](https://yara.readthedocs.io/)**
  - 偵測規則的權威來源與規則庫

### 推薦論文與資源

- **[SANS DFIR Posters & Cheat Sheets](https://www.sans.org/posters/)**
  - Windows/Linux forensic artifact 的速查表，實務工作台必備
- **[The DFIR Report](https://thedfirreport.com/)**
  - 真實入侵事件的完整拆解，看職業藍隊怎麼從 artifact 還原攻擊鏈

### 讀完本課之後

- **《Practical Malware Analysis》**（接 Part 5 與你既有的 malware_analysis 課，把惡意程式鑑識推更深）
- **[Atomic Red Team](https://github.com/redcanaryco/atomic-red-team)** 與 **[MITRE Caldera](https://caldera.mitre.org/)**（purple team 自動化演練，Final Project 之後持續練功用）
