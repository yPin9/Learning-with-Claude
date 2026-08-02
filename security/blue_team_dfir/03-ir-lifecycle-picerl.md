# Ch 3 — IR 生命週期：PICERL 框架

> 目標：掌握 PICERL 事件應變（Incident Response）生命週期的六個階段，理解它與 NIST SP 800-61r2 的關係，並且從紅隊視角看清楚每個階段在現實中為何失敗、紅隊知識如何讓你比傳統 IR 分析師做得更準。

---

## 為什麼需要一個框架？

2017 年，NotPetya 感染烏克蘭後在數小時內橫掃全球企業網路。Maersk 在事後訪談中承認：應變團隊花了數小時爭論「現在該關機還是繼續收集證據」——因為沒有人事先決定誰有權做哪個決定。

這是沒有框架的代價。不是沒有技術能力，是沒有決策結構。

IR 生命週期框架的功能不是告訴你具體的技術操作（那是 DFIR 技術細節的工作），而是回答三個問題：

1. **現在在哪個階段？** 當鑑識、IT、法務同時開會，所有人必須用同一個語言。
2. **這個階段的退出條件是什麼？** 沒有退出條件，階段就會無限蔓延。
3. **下一步是什麼？** 尤其在凌晨三點、大腦轉不動的時候。

業界主流有三套框架：PICERL、NIST SP 800-61r2 四階段，和 SANS IR Process。它們是同一個過程的不同切法，不是競爭關係。

---

## 心智模型：PICERL 不是線性的

很多教材把 PICERL 畫成一條直線，這是誤導。現實的 IR 是有回饋迴圈的：

```
┌─────────────────────────────────────────────────────────────────┐
│                     PICERL 生命週期                              │
│                                                                  │
│   P             I              C              E         R    L   │
│   Preparation   Identification Containment   Eradication │    │  │
│      │              │              │              │      │    │  │
│      │              ▼              │              │      │    │  │
│      │         ┌─────────┐        │              │    Recovery  │
│      │         │ 發現新  │◄───────┘◄────────────┘      │    Lessons│
│      │         │ 持久化  │   重新                       │    Learned│
│      │         │ 機制    │   評估範圍                   │    │  │
│      │         └─────────┘                              │    │  │
│      │              │                                   │    │  │
│      │              ▼              ▼              ▼      ▼    ▼  │
│      └──────────────────────────────────────────────────────►   │
│      Preparation 吸收 Lessons Learned，下次事件重新開始            │
│                                                                  │
│  ▲ 「封鎖前先確認範圍」這個回饋迴圈是最常被跳過的一步            │
└─────────────────────────────────────────────────────────────────┘
```

關鍵回饋迴圈有兩條：

- **I → I**：Identification 中途發現新的攻擊向量或新的受害主機，必須回到 Identification 繼續範圍確認，不能直接跳 Containment。
- **L → P**：Lessons Learned 的輸出必須回饋進 Preparation，否則下次遇到同類攻擊仍然是從零開始。

---

## PICERL 六個階段詳解

### Phase 1：Preparation（準備）

**任務**

在事件發生之前完成的所有基礎建設：

- 建立 IR runbook（針對不同事件類型：勒索軟體、APT、BEC、insider threat）
- 確認各角色授權邊界（誰有權下線主機？誰有權啟動取證？）
- 部署與維護工具：EDR、SIEM、網路流量留存（PCAP 或 NetFlow）、SOAR
- 定義法律保全流程（如需移送司法）
- 建立與外部的聯絡管道：法律顧問、IR retainer 廠商、主管機關報告管道（如 CERT/CC、金管會）

**常見失敗**

- Runbook 三年沒更新，還在寫「聯絡 John，他負責 AV」——John 兩年前離職了。
- 工具買了但從沒測試過回放能力。EDR telemetry 的保留期設 30 天，但攻擊者在 60 天前就進來了。
- 沒有「靜默期（quiet period）」協議——IR 發生時公關部門同時對外發聲，讓攻擊者知道你已經注意到他。

**紅隊知識的貢獻**

Preparation 是紅隊最能貢獻的階段。你知道哪些攻擊路徑存在：

- Kerberoasting 後的橫向移動會留什麼 event ID（4769）？
- 用 Cobalt Strike Beacon 的 sleep jitter 能不能躲過你現有的 EDR？
- Pass-the-Hash 路徑在你的網路拓樸裡走哪幾跳？

基於這些知識寫出的 runbook，比從 NIST 文件直接翻譯的版本有效得多。

---

### Phase 2：Identification（識別）

**任務**

確認「這到底是不是真的事件」，以及「範圍有多大」：

- 分析初始告警，排除誤報（False Positive）
- 確認 IOC（Indicators of Compromise）：IP、雜湊值、domain、registry key
- 確認受影響的主機、帳號、資料範圍
- 建立事件時間軸（Timeline）——這是後面所有決策的基礎
- 判斷攻擊者目前是否仍在網路中（Active vs. Dormant）

**常見失敗：告警疲勞（Alert Fatigue）**

這是現實中最致命的 Identification 失敗。攻擊者的初始告警往往存在，只是沒人在意：

一個真實的模式：SIEM 在事件發生前三天就產生了「異常登入嘗試」的中優先級告警，但 SOC 分析師每天要看 3,000 個告警，這個被標記為「已確認誤報」後關閉。三天後攻擊者完成橫向移動並部署勒索軟體。

如何避免：**不是多買工具，而是讓高信心告警有更短的回應 SLA**。與其 3,000 個告警全部設相同優先級，不如讓 10 個高信心告警在 15 分鐘內強制人工確認。

**常見失敗：過早認定範圍**

分析師看到一台被 compromise 的主機，立刻做決定：「就這一台，把它下線」。

實際上攻擊者已經在四台主機上建了持久化。下線那一台之後，攻擊者切換到另一台繼續，而你剛才的動作讓他知道你已經發現他了。

**紅隊知識的貢獻**

你知道攻擊者在完成 initial access 後的標準操作流程：

- 通常先做本機提權，再做 credential dumping（LSASS、SAM、DPAPI）
- Credential 到手之後會做網路偵察（net view、BloodHound 偵察、LDAP 查詢）
- 再依據偵察結果選定下一個橫向移動目標

這個模式讓你在分析一台受害主機時，能預判攻擊者接下來可能去哪——不是靠運氣，是靠戰術知識。

---

### Phase 3：Containment（圍堵）

**任務**

阻止事件繼續擴大：

- 短期圍堵：網路隔離受感染主機（但**保留記憶體和磁碟的完整性**，先取證再封鎖，或同時進行）
- 長期圍堵：修改防火牆規則封鎖 C2 domain/IP、停用受害帳號、撤銷 Kerberos ticket（Windows 環境需重置 KRBTGT 密碼兩次）
- 防止攻擊者在「被發現」的反應時間內完成破壞（如部署勒索軟體、刪除備份）

**關鍵錯誤直覺：下線就等於安全**

```
錯誤直覺 → 發現 C2 連線主機，立刻下線，事件圍堵完畢。

正確認識 → 攻擊者在其他五台主機上都有持久化，
           你下線的那台只是讓他失去了一個立足點。
           他現在知道你發現他了，會加速行動（如立刻部署 payload）。
```

Containment 的順序必須是：**先完成 Identification 範圍確認，再執行同步圍堵**。單點封鎖等同於通知攻擊者。

**記憶體取證的時機**

記憶體（RAM dump）必須在斷網之前或斷網同時完成，否則：

- 記憶體中的加密金鑰（如 BitLocker FVEK、C2 session key）消失
- 攻擊者的 in-memory implant 無法被捕捉（Cobalt Strike 的 reflective DLL 在 Windows Event Log 中幾乎無痕）
- 活躍的網路連線狀態（`netstat` 對應的 socket 結構）消失

工具：`winpmem`（Windows）、`LiME`（Linux kernel module）、EDR 平台的 live memory acquisition。

**紅隊知識的貢獻**

你知道攻擊者會部署多少條持久化：有經驗的紅隊不會只設一條。通常的模式是：

- 排程工作（Scheduled Task）作主要持久化
- Registry Run Key 作備用
- WMI Event Subscription 作備備用

Containment 時，你的 checklist 必須覆蓋全部三個，而不只是「找到一個就停」。

---

### Phase 4：Eradication（根除）

**任務**

從環境中完全移除攻擊者的存在：

- 移除所有惡意軟體和工具（implant、dropper、persistence mechanism）
- 清除攻擊者建立的後門帳號
- 修補被利用的漏洞
- 重置所有受害帳號的憑證（密碼+MFA token）
- 若攻擊者曾接觸 AD，考慮重置 KRBTGT（防 Golden Ticket）

**Incomplete Eradication：最常見也最昂貴的失敗**

場景：分析師找到了 C:\Windows\Temp\svhost.exe（注意：故意拼錯，不是 svchost）這個惡意程式，刪除它、移除對應的 Scheduled Task，宣告根除完成。

七天後，攻擊者又回來了。

原因：他同時設了 HKCU\Software\Microsoft\Windows\CurrentVersion\Run 下的 Registry Run Key，而分析師沒有在根除 checklist 裡包含這個路徑。

**根除 checklist 必須覆蓋的持久化機制**

| 類型 | 位置 |
|------|------|
| Scheduled Task | `schtasks /query` 或 `C:\Windows\System32\Tasks\` |
| Registry Run | `HKLM/HKCU\...\Run`, `RunOnce`, `RunServices` |
| Services | `sc query` 或 `HKLM\SYSTEM\CurrentControlSet\Services` |
| WMI Subscription | `Get-WMIObject -Namespace root\subscription` |
| Startup Folder | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` |
| DLL Hijacking | 應用程式目錄下的非預期 DLL |
| Boot/Pre-OS | Bootkit：MBR、VBR，需要 offline 分析 |
| 後門帳號 | AD 中異常的 Domain Admin 或 Service Account |

**紅隊知識的貢獻**

你自己在攻擊中會設哪些持久化，就知道防守時要找哪些。這不是比喻——直接把你的攻擊 playbook 轉成 Eradication checklist。

---

### Phase 5：Recovery（恢復）

**任務**

讓系統安全地回到正常運作：

- 從已知良好（known-good）的備份或 golden image 還原系統
- 驗證還原後的系統沒有殘留惡意程式（EDR clean scan + IOC comparison）
- 分階段恢復服務，監控異常
- 確認業務連續性（Business Continuity）計畫是否觸發

**常見失敗**

- 從未測試過的備份還原，發現備份損壞或過期。
- 還原後忘記把系統加回監控，攻擊者再次入侵時沒有告警。
- 急於恢復業務，在確認根除完成前就把系統上線——攻擊者立刻利用殘留持久化回來。

Recovery 的退出條件：**不是「系統跑起來了」，而是「系統在強化後的監控下穩定運作 X 小時，且無異常告警」**。

---

### Phase 6：Lessons Learned（經驗彙整）

**任務**

事後（通常在 72 小時到兩週內）召開 post-incident review：

- 記錄完整時間軸（Timeline）
- 確認各階段的決策是否正確
- 找出「如果 X 不同，結果會不同」的改進點
- 更新 runbook、工具、監控規則
- 若有法律義務，完成監管機關報告

**為什麼這個階段永遠被跳過**

現實原因有兩個：

1. **時間壓力**：事件結束後，所有人都要回去處理積累的日常工作。誰有時間開三小時的 review 會議？
2. **怪罪文化（Blame Culture）**：Lessons Learned 很容易變成「找誰的錯」。有自保意識的人會用模糊語言，讓報告失去任何實際價值。

跳過的代價：同類攻擊六個月後再次發生，整個 IR 過程原地複製一遍，包括同樣的錯誤。

**讓 Lessons Learned 實際發生作用的方法**

- 會議前發出 blameless postmortem 框架（借鑑 SRE 文化）：問題是「系統為何失敗」，不是「誰出了錯」。
- 每個改進項目要有 owner 和 deadline，否則它永遠停在白板上。
- 量化：「這次事件的 dwell time（從入侵到發現的時間）是 X 天，目標是下次降到 Y 天」。

**紅隊知識的貢獻**

這是紅隊能帶來最高密度貢獻的地方。你能回答：「哪個控制措施，如果存在，本來可以在哪個攻擊步驟阻擋攻擊者？」

- Initial access 用的是 spearphishing 附件 → 如果有 MFA 強制推行到 Outlook Web Access，這步還有效嗎？
- 橫向移動用的是 Pass-the-Hash → 如果有啟用 Local Administrator Password Solution（LAPS），會增加多少攻擊成本？
- 資料外洩走的是 DNS tunneling → 如果有 DNS query 的 DLP 監控，能在什麼時間點抓到？

---

## 具體案例：BEC + 橫向移動事件的 PICERL 走法

**背景**

攻擊者透過 spearphishing 取得財務部主管 Lisa 的 Microsoft 365 帳號存取。他沒有立刻發動 BEC（Business Email Compromise，商業電子郵件詐騙），而是先靜默觀察三週，學習 Lisa 的電子郵件模式、正在進行的交易，然後：

1. 設定 inbox 規則，把 IT 安全通知自動移到已刪除
2. 從 Lisa 的帳號發信給財務部同事 Mark，要求緊急匯款 USD 280,000
3. 在 Mark 確認後，用 Lisa 的帳號存取了公司的 VPN，並橫向移動到會計系統主機

---

**Phase 1 — Preparation（事前）**

組織理論上應該具備：

- Microsoft 365 Unified Audit Log 已啟用（預設 90 天保留）
- SIEM 接收 Azure AD Sign-in Logs，含 IP 地理位置異常告警
- 財務部大額匯款有第二管道口頭確認的 SOP
- IR runbook 包含「帳號 compromise + BEC」場景

現實：第三項 SOP 存在，但沒人執行，因為「Mark 認識 Lisa，以為是真的」。

---

**Phase 2 — Identification**

觸發點：銀行通知匯款被攔截，Mark 回報給 IT。

分析師的工作：

```
1. 調閱 Lisa 帳號的 Azure AD Sign-in Logs
   → 發現三週前開始出現來自 Bucharest 的成功登入
   → 與 Lisa 本人確認：她從未到過羅馬尼亞

2. 調閱 Unified Audit Log 的 InboxRule 操作
   → 找到 "MessageToForwardTo" 規則建立記錄（攻擊者建的）
   → 還找到「收件人包含 'IT Security Alert' → 刪除」的規則

3. 調閱 VPN 連線記錄
   → 攻擊者從 Lisa 帳號登入 VPN，連線到 10.0.5.0/24 內網段
   → 10.0.5.12 是會計系統主機（ERP server）

4. 在 ERP server 的 Windows Event Log 找到：
   → Event ID 4624 (Successful Logon) with Lisa's credentials
   → Event ID 4648 (Explicit Credential Logon) — 攻擊者 PtH 嘗試
   → 在 C:\ProgramData\ 找到不明 PowerShell 腳本
```

範圍確認：Lisa 帳號、Mark 帳號（可能的社交工程對象）、ERP server（10.0.5.12）。攻擊者目前可能仍在網路中（最後 VPN 連線是 4 小時前）。

---

**Phase 3 — Containment**

同步執行，不分先後（避免通知攻擊者）：

- 撤銷 Lisa 和 Mark 的 Azure AD session token（Revoke-AzureADUserAllRefreshToken）
- 對 ERP server 做記憶體取證（winpmem），保留 PowerShell 腳本
- 封鎖攻擊者使用的 Bucharest IP 段
- 在 VPN 閘道封鎖 Lisa 和 Mark 的帳號
- 通知財務部：此事件發生期間的所有匯款請求需重新驗證

**沒有做的錯誤示範**：立刻重置 Lisa 密碼而不先撤銷 session token——攻擊者的 refresh token 仍然有效，密碼重置不會讓他下線。

---

**Phase 4 — Eradication**

- 移除 ERP server 上的惡意 PowerShell 腳本
- 刪除 Lisa 帳號上的惡意 inbox 規則
- 確認 ERP server 沒有新建立的後門帳號（net user /domain）
- 確認 AD 中沒有新建立的 Domain Admin（Get-ADGroupMember "Domain Admins"）
- 重置 Lisa、Mark、以及所有曾在 ERP server 登入的服務帳號密碼
- 為 Lisa 帳號啟用 MFA（此前豁免）

---

**Phase 5 — Recovery**

- ERP server 從 Golden Image 重建（不是修復，而是重建）
- 驗證重建後系統的 EDR clean scan
- 恢復 Lisa 帳號，強制完成 MFA 設定後才能使用
- 七天強化監控期：對 Lisa 帳號的所有操作設定即時告警

---

**Phase 6 — Lessons Learned**

關鍵發現（紅隊思維的貢獻）：

| 攻擊步驟 | 如果存在這個控制 | 效果 |
|---------|---------------|------|
| 帳號 compromise | 財務部 MFA 強制 | 攻擊者無法登入 |
| 三週靜默觀察 | Azure AD Identity Protection（Impossible Travel 告警）| 第一天就觸發告警 |
| 匯款詐騙 | 財務部大額匯款雙通道確認（電話）SOP 落實 | 詐騙失敗 |
| VPN 橫向移動 | ERP server 網路分段，財務帳號無 VPN 到後端的路由 | 橫向移動失敗 |

---

## 框架比較

| 維度 | PICERL | NIST SP 800-61r2 | SANS IR Process |
|------|--------|-------------------|-----------------|
| 階段數 | 6 | 4 | 6 |
| 根除與恢復 | 分開 | 合并為 CER | 分開 |
| 準備階段重量 | 獨立 Phase 1 | 獨立 Phase 1 | 獨立 Phase 1 |
| 識別細節 | Detection + Scoping 合一 | Detection & Analysis 獨立 | 同 PICERL |
| 事後回顧 | Lessons Learned | Post-Incident Activity | Lessons Learned |
| 常見使用場景 | 企業 IR 團隊、SOC | 政府機關、合規場景 | SANS GIAC 認證訓練 |
| 框架來源 | SANS 衍生 | NIST（美國政府） | SANS Institute |
| 回饋迴圈明確性 | 隱含，需要團隊自行實踐 | 明確提到 Detection 到 Preparation 的回饋 | 隱含 |

三個框架沒有優劣之分，只有「你的團隊用哪個」之分。選一個、訓練全員、然後嚴格執行——比三個框架都懂但沒有一個真正運作要有用。

---

## 錯誤直覺 → 正確認識

**1. 「告警太多，先處理高嚴重性的，中低等的之後再說。」**

錯誤直覺 → 嚴重性標籤反映的是工具廠商的預設設定，不反映你的環境。

正確認識 → 一個來自財務部的「中等嚴重性」異常登入，在你的環境裡可能是比任何「高嚴重性」惡意軟體更需要立刻回應的訊號。嚴重性標籤需要根據業務脈絡（Business Context）調整。

---

**2. 「找到惡意程式刪掉就根除了。」**

錯誤直覺 → 惡意程式是問題本身，刪掉就好。

正確認識 → 惡意程式是症狀。問題是攻擊者的整條持久化機制。刪掉一個 implant 但沒找到所有持久化路徑，攻擊者下週重新佈署，你重新從 Identification 開始。

---

**3. 「KRBTGT 密碼重置一次就夠了。」**

錯誤直覺 → 重置密碼等於撤銷所有 ticket。

正確認識 → Kerberos 的 Golden Ticket 使用的是 KRBTGT 的前一個密碼雜湊值，必須重置**兩次**（間隔至少 10 小時，等待 Active Directory 同步）才能讓現有的 Golden Ticket 失效。重置一次只讓攻擊者的 ticket 在下次更新時才失效。

---

**4. 「事件應變就是技術問題，不需要法律和公關介入。」**

錯誤直覺 → IR 是 IT/資安部門的事。

正確認識 → 重大事件幾乎必然觸及法律義務（GDPR 72 小時通報、金管會規定）、客戶通知、媒體危機管理。沒有事先準備法律和公關的介入流程，你的技術應變做得再完美，公司也可能在合規或公關層面付出巨大代價。

---

**5. 「Lessons Learned 之後發個報告存檔就完成了。」**

錯誤直覺 → 文件存在代表過程完成。

正確認識 → Lessons Learned 的產出必須是帶有 owner 和 deadline 的行動項目，並且在後續追蹤。報告進了文件系統卻沒有人執行改進，等同於沒有做 Lessons Learned。

---

## 進階延伸

### SOAR 自動化與 PICERL

Security Orchestration, Automation and Response（SOAR）平台（如 Splunk SOAR、Palo Alto XSOAR）可以把 PICERL 部分階段自動化：

- **Identification**：自動 enrichment（IP 查 VirusTotal、域名查 WHOIS）
- **Containment**：自動觸發 firewall block、EDR isolation
- **Eradication**：自動執行 endpoint cleanup playbook

但 SOAR 不能替代 Identification 的範圍判斷——那仍然需要人的戰術知識。過度信任自動化圍堵會造成「Premature Containment」的問題。

### Dwell Time 作為 IR 成熟度的指標

Dwell Time（停留時間）= 攻擊者入侵到被發現的時間。

Mandiant 2024 年報告的全球中位數是 10 天（從 2020 年的 24 天大幅下降），但 APAC 地區仍偏高。

降低 Dwell Time 的方式：
- 強化 Preparation（更好的偵測覆蓋）
- 縮短 Identification 到 Containment 的決策時間
- 引進威脅獵捕（Threat Hunting）——主動尋找攻擊者，而不是等告警

### Purple Team 演習與 PICERL 驗證

最有效的 Preparation 改進方法：做 Purple Team 演習。

紅隊執行攻擊步驟，藍隊實時監控並說出他們觀察到的內容，雙方對照差距。這讓你在真實事件前就知道：你的 SIEM 在 Phase 2 的覆蓋缺口在哪裡。

---

## 本章重點整理

- PICERL 六個階段：**Preparation / Identification / Containment / Eradication / Recovery / Lessons Learned**，有回饋迴圈，不是線性流程。
- NIST SP 800-61r2 四個階段是不同切法，核心過程相同；選一個框架並嚴格執行比同時懂三個更有價值。
- **Premature Containment** 是最危險的操作失誤：在範圍確認完成前封鎖單一節點，等同通知攻擊者且燒毀證據軌跡。
- **Incomplete Eradication** 讓攻擊者一週後重返：持久化機制 checklist 必須覆蓋 Scheduled Task、Registry Run、WMI Subscription、Service、DLL Hijacking、後門帳號等全部向量。
- **Lessons Learned** 是最常被跳過但最有長期價值的階段：必須產出帶 owner + deadline 的行動項目，而非只有存檔的報告。
- 紅隊知識在 **Identification** 和 **Lessons Learned** 貢獻最高：前者加速範圍判斷，後者能精確指出哪個控制措施本來能在哪個步驟阻斷攻擊。
- BEC 案例演示了在每個 PICERL 階段的具體行動與錯誤示範。

---

## 自我檢核

- [ ] 我能不看筆記說出 PICERL 六個階段的名稱和各階段的主要任務。
- [ ] 我能解釋 PICERL 與 NIST SP 800-61r2 的主要差異（階段切法）。
- [ ] 我能說出「Premature Containment」的定義和為什麼有害。
- [ ] 我能列出至少五種 Windows 持久化機制及對應的 Eradication 查找位置。
- [ ] 我能解釋 KRBTGT 密碼為何需要重置兩次。
- [ ] 我能說出 Lessons Learned 在現實中被跳過的原因，以及讓它實際有效的條件。
- [ ] 我能從一個真實攻擊場景出發，指出每個 PICERL 階段的具體行動。

---

## 延伸閱讀

1. **NIST SP 800-61r2 — Computer Security Incident Handling Guide**  
   https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-61r2.pdf  
   原始規範文件。第三章（Handling an Incident）是核心，直接讀這章而不是看二手摘要，細節遠比摘要豐富。

2. **SANS Institute — Incident Handler's Handbook**（Paul Hewlett）  
   https://www.sans.org/white-papers/33901/  
   SANS 版本的 IR 生命週期，是 PICERL 框架的主要出處之一，可以對照本章理解兩者差異。

3. **Mandiant M-Trends 2024 Report**  
   https://www.mandiant.com/m-trends  
   年度 IR 統計報告，包含 Dwell Time 趨勢、初始入侵向量分布、攻擊者行為模式。用真實數據校準你對「實際事件」的認識。

4. **The DFIR Report — Real Intrusion Case Studies**  
   https://thedfirreport.com/  
   真實事件的詳細 PICERL 分析，含完整 TTPs 對照 MITRE ATT&CK。比任何教材都貼近真實應變工作的樣貌。

5. **MITRE ATT&CK — Incident Response Use Case**  
   https://attack.mitre.org/resources/getting-started/  
   如何把 ATT&CK 矩陣整合進 IR 流程：Identification 階段用 TTP 分類取代 IOC 分類，讓 Eradication checklist 更系統化。

---

PICERL 是 IR 的骨架，但骨架上需要肉：你在 Identification 階段能否快速判斷範圍，取決於你對數位證據本身的理解——哪些證據可信、哪些已被竄改、哪些從未存在。

→ [Ch 4 證據可信度與鑑識報告](./04-evidence-forensic-soundness.md)
