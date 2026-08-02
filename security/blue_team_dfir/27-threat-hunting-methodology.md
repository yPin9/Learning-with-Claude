# Ch 27 — Threat Hunting 方法論

> 目標：理解主動狩獵（Threat Hunting）和被動告警（Alerting）的本質差異，掌握 hypothesis-driven hunting 的完整思路，能從 ATT&CK/TI/異常出發形成可執行的狩獵假設，並把狩獵成果轉回偵測規則。

## 為什麼需要 Threat Hunting？

告警系統的根本假設是：我知道壞事長什麼樣子，所以我寫規則讓它觸發。這個假設在對抗熟練攻擊者時站不住腳。

攻擊者在你更新規則之前已經知道你有什麼規則。他們：

- 用 LOLBins 規避「新程序生成」的規則
- 用加密 C2 繞過網路簽名
- 用 living-off-the-land 讓行為和正常管理流量混在一起
- 用慢速橫向移動讓閾值型告警永遠不觸發

這就是為什麼 **Mandiant M-Trends** 年報每年都在說平均 dwell time（攻擊者在你網路裡藏了多久才被發現）是以周或月計算，而不是小時。

Threat Hunting 的核心是：**不等告警，主動帶著假設去資料裡找尚未被偵測的入侵行為**。

## 先建立直覺：Hunting vs Alerting

這兩件事在認識論上的差異比工具差異更根本：

| 維度 | Alerting（告警） | Threat Hunting（狩獵） |
|---|---|---|
| 驅動力 | 已知 IOC/簽名觸發 | 人主動提出假設 |
| 時機 | 事件發生時即時通知 | 定期或按需求執行 |
| 前提 | 已知攻擊長什麼樣 | 假設攻擊存在並去尋找 |
| 結果 | 告警（真陽、假陽） | 發現或排除（假設被驗證或否決） |
| 成果轉換 | 本身就是產出 | 好的 hunt 成果要寫成告警規則 |
| 技能核心 | 規則工程、調整閾值 | 資料分析、攻擊者思維、統計直覺 |

Hunting 和 SIEM alerting 不是競爭關係，而是互補：**Hunting 的成果要回饋到告警體系**，讓下次同樣的手法能被自動抓到。

## Hunting 方法論的三條路

### 1. Hypothesis-Driven Hunting（假設驅動）

最系統化也最常見的方式。從「攻擊者如果用 X 手法，會在資料裡留下 Y 痕跡」這個假設出發，然後去資料裡驗證或否決。

假設的品質決定 hunt 的質量。好假設的條件：

- **具體**：「有人用 regsvr32 載入遠端 scriptlet」比「有人在做橫向移動」好
- **可查詢**：你能把假設轉成具體的欄位查詢條件
- **有根據**：來自 ATT&CK、TI 報告、或你自己的攻擊知識

### 2. TTP-Based Hunting（基於 TTP）

從 MITRE ATT&CK 的特定 TTP 出發，列出這個 TTP 的行為指標，然後逐一查詢。比純 IOC hunting 更耐用，因為即使攻擊者換了工具，只要用同樣的 TTP 手法就還查得到。

例：T1055（Process Injection）的行為指標包含：
- 非常見程序呼叫 `VirtualAllocEx`/`WriteProcessMemory`/`CreateRemoteThread`
- 程序記憶體空間出現不對齊的可執行區段
- 跨程序的 handle 開啟（Event ID 10）

### 3. Crown Jewel Analysis（皇冠珠寶分析）

從「什麼資產是攻擊者最想要的」反推。識別組織的 crown jewel（ERP 系統、AD、source code repo、財務資料庫），然後看誰在存取、怎麼存取、這些行為是否合理。

這條路特別適合內部威脅和 APT 長期潛伏的場景。

## Diamond Model 在 Hunting 的用法

Diamond Model（鑽石模型）是分析攻擊者行為的框架，四個頂點：

```
          攻擊者 (Adversary)
               / \
              /   \
    能力 ←──────────→ 基礎設施
  (Capability)      (Infrastructure)
              \   /
               \ /
            目標 (Victim)
```

Hunting 時用它來思考：我現在偵測到的東西，屬於哪個頂點？一個角更換不影響其他角，但攻擊者的能力（TTP）是最難換的。所以把偵測聚焦在能力層，而不是 IP/domain（基礎設施層）是更耐用的策略。

## Hunting Maturity Model（HMM）

組織的 hunting 能力可以按成熟度分級：

| Level | 名稱 | 特徵 |
|---|---|---|
| HMM0 | 初始（Initial） | 純靠自動化告警，無主動狩獵 |
| HMM1 | 最小化（Minimal） | 用 IOC 搜資料，靠外部 TI feed |
| HMM2 | 程序化（Procedural） | 能跑別人寫的 hunting playbook |
| HMM3 | 創新（Innovative） | 自己形成假設、寫查詢、分析 |
| HMM4 | 領先（Leading） | 資料蒐集主動配合 hunting 需求，自動化反覆循環 |

大多數組織在 HMM1-2 之間。真正有效的 hunt 至少要到 HMM3。

## Hunt Loop：完整循環

```
         ① 形成假設
        (Hypothesis)
              │
              ▼
         ② 定義資料需求
       (Data Collection)
              │
              ▼
         ③ 分析資料
          (Analysis)
              │
        ┌─────┴─────┐
        ▼           ▼
   ④a 發現惡意    ④b 假設否決
    行為/入侵      (無發現)
        │               │
        ▼               ▼
   ⑤a IR 流程     ⑤b 精煉假設
    + 轉偵測規則    或換假設
        │
        ▼
   ⑥ 知識回饋
   (文件化/規則)
```

關鍵點：**否決一個假設也是有價值的結果**。它代表你已經排除了一個攻擊路徑（在你的資料範圍內），或者告訴你資料蒐集有盲點。

## 如何形成好的假設

形成假設有三個主要來源：

### 從 ATT&CK 出發

選一個尚未有偵測覆蓋的 TTP，問：「如果攻擊者用這個手法，我的日誌裡應該看到什麼？」

例：T1218.010（Regsvr32 Abuse）：
- 假設：「攻擊者用 regsvr32.exe 從網路載入 COM scriptlet（scrobj.dll）以繞過 AppLocker」
- 資料需求：Sysmon Event 1（process creation）、Event 3（network connection）、Event 7（image load）
- 查詢邏輯：parent=regsvr32.exe AND（command line 含 /s /i AND URL）OR（scrobj.dll 被載入）

### 從 Threat Intelligence 出發

看特定 threat actor 的 TI 報告，提取他們用過的 TTP，形成「如果這個 actor 攻擊我們會用什麼」的假設。

### 從資料異常出發

先看 baseline，找統計離群值，然後問「這個異常有沒有惡意解釋」。這是最探索性的路，需要對正常行為有深入了解。

## 範例：完整假設形成過程

**情境**：組織使用 Windows 環境，最近 TI feed 顯示某 APT 組織在同行業有活躍活動，他們已知使用 PowerShell 混淆與 WMI 持久化。

**假設 1**：
> 「攻擊者用 PowerShell 的 encoded command（-EncodedCommand 參數）執行下載器，payload 來自外部 URL，以規避 command line 監控。」

拆解成可查詢的資料需求：
- Sysmon Event 1：process = powershell.exe，commandline 含 `-enc` 或 `-EncodedCommand`
- Sysmon Event 3：parent = powershell.exe，目標 IP 是外部
- Windows Event 4104：script block logging 捕捉到 Invoke-WebRequest/IEX

這個假設清晰、可執行，且直接對應你身為攻擊者會怎麼做。

**假設 2**：
> 「攻擊者在 WMI 建立 event subscription 作為持久化機制，繫結到系統啟動事件。」

資料需求：
- Sysmon Event 20/21（WMI Event 過濾器和 Consumer 的建立）
- Windows Event 5861（WMI activity log）

## 踩雷

1. **假設太廣**：「有人在做橫向移動」不是假設，是話題。假設必須指定具體手法（T1021.002 SMB/Windows Admin Shares），才能轉成查詢。

2. **沒有定義「否決條件」**：出發前要知道「什麼情況代表假設被否決」。如果所有結果都能被解釋成「可能是攻擊」，你只是在確認偏誤，不是在狩獵。

3. **忽略 dwell time**：查詢時間範圍不夠長。APT 可能在環境裡潛伏好幾個月，只查最近一週的資料會錯過他們。一般建議最少 90 天，理想 180 天。

4. **只看 endpoint 不看網路**：攻擊者可以擦掉 endpoint 的痕跡，但網路日誌（特別是防火牆、DNS、proxy）往往保留更久且更難竄改。Hunt 要跨資料來源。

5. **Hunt 沒有文件化**：每一次 hunt 的假設、查詢、結果都要記錄，即使是「沒找到」也要記。下次做類似 hunt 的人才能站在你的肩膀上，而不是重複你的工作。

## 進階延伸

- **Hunt 排程化**：把成熟的 hunt playbook 轉成定期自動執行的查詢，搭配 Velociraptor 的 VQL artifact 或 Elastic 的 scheduled search，做到半自動化的持續狩獵。
- **行為基線學習**：用機器學習對「親子程序關係」、「登入時間/地點」建立統計 baseline，讓資料異常更容易浮現，補強假設驅動 hunt 的盲點。
- **Hunt 協作平台**：有些組織用 Jupyter Notebook 或 MISP 分享 hunt playbook 和 TI，讓不同分析師共用假設庫，避免重複勞動。

## 本章重點整理

- Threat Hunting 是**主動**帶假設去資料裡找尚未被偵測的攻擊，告警是被動等規則觸發
- 三條狩獵路：hypothesis-driven、TTP-based、crown jewel analysis
- Hunt Loop：假設 → 資料 → 分析 → 發現/否決 → 規則/精煉 → 循環
- 好假設的條件：具體、可查詢、有根據
- Diamond Model 提醒我們把偵測鎖定在能力層（TTP），比鎖定在 IOC 更耐用
- 每次 hunt 結果必須文件化，且成功的 hunt 要回饋成告警規則

## 自我檢核

- [ ] 我能解釋為什麼 alerting 對抗熟練攻擊者會有盲點
- [ ] 我能列出 hypothesis-driven、TTP-based、crown jewel 三種 hunting 路的差異
- [ ] 給我一個 ATT&CK TTP，我能把它拆成假設、資料需求、查詢邏輯
- [ ] 我能說出 Hunt Loop 的每個步驟，包含「假設被否決」時的正確處理
- [ ] 我知道 Hunting Maturity Model 各層級的差異，能評估自己組織在哪一層

## 延伸閱讀

1. **[SANS FOR508: Advanced Incident Response, Threat Hunting, and Digital Forensics](https://www.sans.org/cyber-security-courses/advanced-incident-response-threat-hunting-training/)** — 工業標準的 threat hunting 課程，課程大綱公開可參考；特別是 Hunt Hypothesis Framework 部分。

2. **[Sqrrl 的 A Framework for Cyber Threat Hunting](https://www.threathunting.net/files/framework-for-threat-hunting-whitepaper.pdf)** — 最早系統化定義 threat hunting 方法論的白皮書，Hunt Loop 和 HMM 概念來源。

3. **[MITRE ATT&CK Navigator](https://mitre-attack.github.io/attack-navigator/)** — 視覺化你的偵測涵蓋度，識別哪些 TTP 還沒有 hunt 或偵測規則，優先化 hunting 方向。

4. **[The DFIR Report](https://thedfirreport.com/)** — 真實入侵事件拆解，每篇報告都有攻擊者用的 TTP，直接當 hunt 假設的來源。

5. **[Red Canary Threat Detection Report](https://redcanary.com/threat-detection-report/)** — 年度最常見的 TTP 統計，告訴你業界哪些手法最活躍，應該優先 hunt。

---

→ [Ch 28 用資料狩獵：KQL/SPL 查詢思維](./28-hunting-with-data.md)
