# Ch 35 — 事件分級、Alert Triage 與 SOAR

> 目標：掌握告警進來之後的完整處理流程——從 triage 到分級升級到調查，理解 SOAR 自動化的合理邊界，以及為什麼假陽性成本比多數人以為的高出兩倍。

## 為什麼 triage 是 SOC 的核心問題

現代 SIEM 環境每天產幾十萬條告警，而有意義的事件可能只有幾十件。這個落差不是規則寫爛了，而是偵測系統的本質：提高靈敏度必然拉高假陽性；降低假陽性就必然提高漏報。沒有免費的午餐。

問題在於，告警爆量之後，分析師的注意力被稀釋，真正有威脅的告警就淹沒在噪音裡。**Alert fatigue（告警疲勞）**不是形容詞，是可量化的現象——研究指出 SOC 分析師平均只深入調查 50% 的告警，另外 50% 要不就是被略過，要不就是機械性地關掉。

告警疲勞的根源：

1. **低品質偵測規則**：門檻設太低，大量良性行為觸發
2. **沒有 context enrichment**：光一條「Process executed」沒意義
3. **沒有分級**：所有告警都是「高」就等於沒有「高」
4. **沒有 playbook**：分析師每次都從頭想，耗時又不一致

triage 的目的是在最短時間內判斷「這值不值得深入調查」，並且把決策標準化，讓新手也能做出跟老手差不多的初步判斷。

## 建立直覺：嚴重度不是單維度

分析師最常犯的錯：看到「高嚴重度」規則觸發就拉警報，看到「低」就忽略。這是把偵測規則的設定嚴重度直接等同於事件的實際嚴重度，兩者根本不同。

**實際嚴重度 = 影響範圍 × 可信度**

- **影響範圍（Impact）**：如果是真的，損害有多大？資料外洩、服務中斷、橫向移動的可能性？
- **可信度（Confidence）**：這條告警是真的惡意行為的機率有多高？FP 率高的規則降低可信度。

一條「可能是 Mimikatz 的 LSASS 存取」影響範圍高，但如果在一台開發機上、由已知的 ProcMon 工具觸發，可信度就低——最終優先順序是中，不是高。相反地，一條「異常時段登入失敗」影響範圍看起來低，但來自已知惡意 IP、針對特權帳號，可信度高，就該優先處理。

這個矩陣決定了你的 triage 順序，也決定了哪些東西應該自動化。

## Triage 流程：從告警到決策

### 四步驟分流

```
告警進入
   │
   ▼
[Step 1] 初步驗傷
         - 關聯哪個主機/用戶/IP？
         - 規則觸發的原始事件是什麼？
         - 這條規則的歷史 FP 率？
         │
         ▼
[Step 2] Context Enrichment
         - IP 查 VirusTotal/AbuseIPDB
         - 帳號查 AD 群組、最近登入地點
         - Hash 查 EDR 的 process tree
         - 主機的風險等級（tier 1 server vs 一般工作站）
         │
         ▼
[Step 3] 嚴重度評估（影響 x 可信度矩陣）
         - 定出 Low / Medium / High / Critical
         - 對應 SLA：Critical = 15 分鐘、High = 1 小時、Medium = 4 小時
         │
         ▼
[Step 4] 分流決定
         ├─ 關閉（FP）：記錄原因，回饋規則調整
         ├─ 監控：標記觀察，設 trip-wire
         ├─ 分配調查：指派分析師，開 case
         └─ 升級：立即呼叫 IR lead
```

### 分流決策樹範例

以「PowerShell 執行 Base64 encoded command」為例：

```
Q1: 來源主機是否在 exception list（已知自動化系統）？
    是 → 關閉 FP，記錄
    否 → 繼續

Q2: 命令解碼後是否包含已知惡意 pattern（download cradle、反向連線IP）？
    是 → Critical，立即升級
    否 → 繼續

Q3: 執行帳號是否有特權？
    是（Domain Admin、SYSTEM）→ High，開案
    否 → 繼續

Q4: 同一主機過去 24 小時有其他異常告警？
    是 → High，開案
    否 → Medium，監控 24 小時
```

決策樹強制讓 triage 可重現，不靠分析師的個人感覺。

## SOAR 自動化的合理邊界

SOAR（Security Orchestration, Automation and Response）的核心是 **playbook**：把重複性的 triage 步驟自動執行。

### 適合自動化的工作

| 動作 | 說明 |
|------|------|
| IP enrichment | 自動查 VirusTotal、Shodan、內部 TIP |
| Hash lookup | 送到 MalwareBazaar、VirusTotal |
| AD 帳號資訊拉取 | 群組、最後登入、密碼年齡 |
| 歷史告警關聯 | 這個 IP/主機過去 N 天的告警 |
| 建立 case | 自動在 TheHive 開 case，填入初始資訊 |
| 通知 | Slack/Teams 通知值班分析師 |

### 謹慎自動化的動作

**自動封鎖**是最大的陷阱。SOAR 看到惡意 IP 自動封防火牆聽起來很美，但：

- FP 的代價從「分析師浪費 5 分鐘」變成「生產服務被切斷 2 小時」
- 聰明的攻擊者用合法服務（GitHub、Dropbox）當 C2，自動封鎖會打掉業務
- 攻擊者知道你有 SOAR 時，故意讓你封鎖關鍵 IP 當 DoS 手段（防禦投毒）

原則：**自動 enrichment，人工 containment**。除非你對那個 playbook 的 FP 率有高度信心（< 1%），並且 containment action 是可逆的（隔離後可以快速恢復），才考慮自動執行。

### TheHive：Case Management

TheHive 是開源的事件管理平台，與 SOAR 整合的方式：

1. SOAR playbook 觸發 → 自動在 TheHive 建 case
2. Case 包含：嚴重度、觸發告警、enrichment 結果、指派分析師
3. 分析師在 TheHive 記錄調查步驟，Timeline 自動累積
4. Cortex（TheHive 的 analyzer 引擎）做深度 enrichment：Shodan、MISP IOC 查詢、沙箱提交
5. 結案時輸出 IR 報告草稿

TheHive + Cortex + MISP 是目前最常見的開源藍隊三件套，商業替代是 Splunk SOAR（原 Phantom）、Palo Alto XSOAR、Rapid7 InsightConnect。

## 具體範例

### 範例 1：成功分流的案例

SIEM 告警：「Lateral Movement — SMB to 20+ hosts in 5 minutes」

- Enrichment：來源是正常業務帳號，但登入時間是 03:17，平時最晚 22:00
- 目標主機：包含 3 台 tier-1 財務系統
- 帳號最近 7 天沒有異常登入

判斷：High，立即升級給 on-call IR。不等人工確認就通知，因為 SLA 是 1 小時，而橫向移動的破壞是指數級的。

後來查明是新上線的 backup agent 設定錯誤，掃了整個 /24。這是 FP，但處理正確——**在確認 FP 之前，永遠按真的處理**。

### 範例 2：SOAR 自動封鎖踩雷

某 SOC 設定 SOAR playbook：IP 在 VirusTotal 評分 > 5/75 自動加防火牆 block list。

第一週封鎖了 127 個 IP。第三天早上 CEO 打電話說他無法存取供應商的 ERP 系統，供應商的 IP 被封掉了，因為有幾個共享 CDN 上的舊報告把那個 IP range 標為惡意。

教訓：VirusTotal 評分高不代表對你的環境有威脅。自動化 block 需要更嚴格的閾值，而且要有 whitelist 邏輯。

### 範例 3：假陽性的隱藏成本

一條低品質 Sigma 規則每天觸發 200 次，分析師每次花 2 分鐘確認是 FP。

每天成本：400 分鐘 = 6.7 小時  
每月：200 小時 = 5 個全職工作週

而且每天的 200 次告警讓分析師學會「這個告警不用管」，當真正的攻擊用同樣手法進來，就被略過了。這才是最貴的代價：**假陽性消耗對特定告警的信任**。

## 關鍵對比

| 項目 | 人工 Triage | SOAR 自動化 |
|------|------------|-------------|
| 速度 | 分鐘到小時 | 秒到分鐘 |
| 一致性 | 因人而異 | 高度一致 |
| 複雜判斷 | 強 | 弱（規則外的情況無法處理） |
| 誤動作風險 | 低（人會猶豫） | 高（執行快，FP 代價大） |
| Enrichment | 適合自動化 | 最有價值的用途 |
| Containment | 人工為主 | 謹慎、可逆時才考慮自動化 |

## 踩雷

1. **所有規則都設「高」嚴重度**：等於沒有優先順序，分析師很快就失去判斷能力。嚴重度是 triage 的輸入，要花時間校準。

2. **SOAR playbook 沒有 dry-run 機制**：上線前要先用 shadow mode 跑幾週，觀察它會做什麼決定，確認沒有誤封關鍵 IP 或帳號，再開啟實際執行。

3. **只靠 SOAR 做 enrichment，忘記更新 playbook**：IP 信譽 feed 的品質會劣化，舊的 enrichment 邏輯可能已經不準確。Playbook 需要定期審查。

4. **FP 關掉就算了，不記錄原因**：每次關閉 FP 的原因如果不回饋到規則調整，下個月還是同樣的 200 次假陽性。FP 是偵測改善的輸入，不是廢紙。

5. **Case management 和 SIEM 告警脫鉤**：如果 TheHive 的 case 沒有直接連結到 SIEM 的告警記錄，調查時要手動複製貼上，timeline 就不完整。整合要在一開始就設計好。

## 進階延伸

- **SOAR enrichment graph**：把 enrichment 步驟設計成 DAG（有向無環圖），並行跑多個 enrichment 降低延遲，關鍵路徑上的 enrichment 決定 SLA。
- **Alert deduplication**：同一個攻擊波次觸發的多條告警要去重合併成一個 case，否則分析師要面對 10 個「同一件事」的 case。SIEM 的 correlation rule 和 SOAR 的合併邏輯是不同層次的解法。
- **Tier-based SOC**：Tier 1 做 triage 和 enrichment，Tier 2 做深度調查，Tier 3 做 threat hunting。設計 SOAR playbook 時要考慮哪些步驟在哪個 tier 執行。

## 本章重點整理

- Alert triage 的本質是決策問題：有限注意力分配到最有可能是真實威脅的告警
- 嚴重度 = 影響範圍 × 可信度，不是偵測規則的 severity 欄位
- SOAR 最有價值的用途是 enrichment，自動 containment 需要極高信心才能啟用
- 假陽性的真實成本包含分析師時間和對特定告警喪失信任
- TheHive + Cortex 是開源 case management 的標準組合
- FP 必須記錄原因並回饋規則調整，否則問題永遠不解決

## 自我檢核

- [ ] 我能解釋「嚴重度 = 影響 × 可信度」，並且舉出一個影響高但可信度低的例子
- [ ] 我能說出 SOAR 適合自動化哪些步驟、為什麼自動封鎖是高風險動作
- [ ] 我能描述 TheHive 在 IR 流程中扮演的角色
- [ ] 我知道假陽性除了浪費時間之外，還有什麼更深的危害
- [ ] 我能畫出一個 triage 決策樹，涵蓋 context enrichment 的哪些維度

## 延伸閱讀

1. **NIST SP 800-61r3（Computer Security Incident Handling Guide）**
   - 讀第 3 節「Handling an Incident」的 Detection and Analysis 部分
   - 關聯：PICERL 框架中 Identify 階段的政策基礎，本章 triage 流程的正式對應
   - [https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-61r3.pdf](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-61r3.pdf)

2. **TheHive Project 官方文件**
   - 讀 Cortex analyzer 的整合說明和 TheHive API
   - 關聯：本章 case management 實作，直接連到 Ch 36 的 TIP 整合（MISP↔TheHive）
   - [https://docs.strangebee.com/](https://docs.strangebee.com/)

3. **Palo Alto Unit 42 SOAR Playbook Viewer**
   - 看真實的 SOAR playbook 長什麼樣子，學習 enrichment DAG 設計
   - 關聯：理解 playbook 設計的具體形態，補充本章的抽象流程描述

4. **SANS FOR508 課程材料（Threat Hunting and IR）**
   - 讀 Alert Triage 和 Detection Metrics 相關章節
   - 關聯：SOC 指標設計（MTTD/MTTR）延伸到 Ch 37 的報告指標

5. **Atomic Red Team + Caldera 文件**
   - 讀 Atomic Red Team 的 test case 結構，了解如何用它產生「已知的告警」來測試 playbook
   - 關聯：Ch 38 purple team 演練的工具基礎，先熟悉工具的輸出格式

→ [Ch 36 威脅情報整合](./36-threat-intelligence.md)
