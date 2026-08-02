# Ch 10 — ATT&CK 對映與偵測涵蓋度

> 目標：把你手頭的 Sigma/YARA 規則對映到 ATT&CK technique，用 DeTT&CT 量化涵蓋度，找出真正的盲點——而不是讓 Navigator 的綠色方塊給你虛假的安全感。

## 為什麼需要涵蓋度分析？

你有 300 條 Sigma 規則，你能說出「我對 T1059.001 PowerShell 的偵測有多深？」嗎？

大多數組織的答案是：不知道。規則庫是歷史疊加的，每次 IR 之後加一條，沒有人系統性追蹤「哪些 technique 有偵測、哪些沒有、偵測的品質如何」。

ATT&CK 涵蓋度分析做的就是這件事：**把分散的規則庫轉換成可以看懂的作戰地圖**。

這個分析直接回答三個問題：
1. 哪個 tactic/technique 是我的盲點（完全沒有遙測、更別說規則）？
2. 哪個 technique 有遙測但沒有規則（data source 到位、detection 缺席）？
3. 哪個 technique 有規則但規則品質差（假陽性過高、實際命中率低）？

## 先建立直覺：三層涵蓋度模型

ATT&CK 涵蓋度不是二元的「有 / 沒有」，分成三層：

```
Layer 0 — Visibility（能見度）
    └─ 我有 data source 嗎？
       → 沒有 Sysmon，你連 process creation 都看不到，
         所有 PowerShell 偵測規則等於零

Layer 1 — Detection（偵測）
    └─ 我有規則命中這個 technique 嗎？
       → 有 data source 但沒規則 = 有眼睛沒在看

Layer 2 — Quality（品質）
    └─ 規則是否有效？假陽性率？有沒有實際測試過？
       → Navigator 塗滿綠色，但從沒跑過 atomic test = 紙上涵蓋度
```

大多數組織只做 Layer 0-1，然後宣稱「我們有 80% ATT&CK 涵蓋率」。Layer 2 才是真正有意義的指標，也是最少人做的。

## ATT&CK 結構快速回顧

ATT&CK 矩陣由三層組成：
- **Tactic（戰術）**：攻擊者的目標，14 個（Initial Access / Execution / Persistence / ...）
- **Technique（技術）**：達成目標的具體方法，T1059（Command and Scripting Interpreter）
- **Sub-technique（子技術）**：技術的變體，T1059.001（PowerShell）/ T1059.003（Windows Command Shell）

每個 technique 下有 **Data Sources**：MITRE 整理好的「你需要什麼遙測才能偵測到這個」，例如 T1059.001 需要：
- Command: Command Execution
- Process: Process Creation
- Module: Module Load
- Script: Script Execution

如果你的環境連 Process Creation（Sysmon Event ID 1 / Windows Security Event 4688）都沒收，你的 PowerShell 偵測規則是空炮。

## DeTT&CT：量化涵蓋度的工具

**DeTT&CT**（Detect Tactics, Techniques & Combat Threats）是 Ruben Bouman 開發的 Python 工具，把 data source 與偵測規則整理成 YAML，然後產出 ATT&CK Navigator layer 檔。

工具鏈：
```
你自己的 YAML（data sources + detections）
            │
      DeTT&CT CLI
            │
    ATT&CK Navigator layer (.json)
            │
    Navigator 網頁視覺化
```

### data source YAML 範例

```yaml
version: 1
technique_id: T1059.001
technique_name: PowerShell
visibility:
  score_logbook:
    - date: 2025-01-15
      score: 3          # 0=none, 1=minimal, 2=medium, 3=good, 4=excellent
      comment: "Sysmon Event 1, 7, 10 + PowerShell ScriptBlock Logging (4104)"
  data_sources:
    - Process: Process Creation
    - Command: Command Execution
    - Script: Script Execution
```

Score 的定義由你的組織自訂，但常見慣例：
- **0**：完全沒有這個 data source
- **1**：有 data source 但 sampling / 覆蓋率低（例如只收部分 Windows host）
- **2**：有但不完整（例如有 Process Creation 但沒有 Script Block Logging）
- **3**：完整且可信
- **4**：完整、可信、且有實測驗證

### detection YAML 範例

```yaml
technique_id: T1059.001
detection:
  - applicable_to:
      - Windows Endpoint
    location:
      - Splunk rule: windows_suspicious_powershell_cmdline.yml
    rule_name: Suspicious PowerShell Command Line
    score_logbook:
      - date: 2025-01-15
        score: 1    # 偵測品質，同樣 0-4
        comment: "誤報率高，僅限管理員 IP 例外名單外"
```

跑 DeTT&CT 產出 layer：

```bash
# 安裝
pip install dettect

# 產出 data source layer
dettect.py ds -fd data_sources.yaml -l

# 產出 detection layer
dettect.py d -fd detections.yaml -l

# 在 ATT&CK Navigator 載入 .json layer 檔視覺化
```

（示意，依版本/YAML schema 版號而異）

## ATT&CK Navigator 視覺化

Navigator（[https://mitre-attack.github.io/attack-navigator/](https://mitre-attack.github.io/attack-navigator/)）讓你把多個 layer 疊加比較。

幾個實用操作：
- **疊加 threat actor layer**：從 ATT&CK 下載已知 APT group 的 technique 清單（例如 APT29），疊上你的偵測涵蓋 layer，立刻看出你對這個 group 的能見度缺口
- **顏色編碼**：用深淺表示涵蓋分數，而不是只有有/沒有
- **Comments**：每格可以備注「這條規則的假陽性率是 X%」，查核時有據可查

實戰範例——對 APT29 的涵蓋度分析：

| Technique | APT29 使用 | 我的 Detection Score | 缺口 |
|---|---|---|---|
| T1059.001 PowerShell | 是 | 2 | 有規則但 ScriptBlock 覆蓋不全 |
| T1078.002 Valid Accounts: Domain Accounts | 是 | 0 | 完全沒有偵測 |
| T1021.002 SMB/Windows Admin Shares | 是 | 1 | 規則存在但誤報過高 |
| T1055.001 Process Injection: DLL Injection | 是 | 3 | 有效 |
| T1003.001 LSASS Memory | 是 | 2 | 偵測 procdump 但漏 comsvcs.dll |

這張表給你明確的工作優先順序：先補 T1078.002，再處理 T1003.001 的缺口。

## 資料來源對映：有哪些遙測才能看到哪些 technique

這是最常被忽略的前置問題：在買 detection rule 之前，先確認你有沒有對應的資料。

| Technique | 需要的 Data Source | 沒有會怎樣 |
|---|---|---|
| T1059.001 PowerShell | Process Creation + ScriptBlock Logging（Event 4104） | 只能偵測到 process spawn，看不到腳本內容 |
| T1055 Process Injection | Process Access（Sysmon Event 10） + Image Load | 完全看不到注入行為 |
| T1021.001 RDP | Windows Security Event 4624（Logon Type 10） + Network | 看不到 lateral movement |
| T1003.001 LSASS | Process Access（Sysmon Event 10） + Security Event 4656 | 看不到 credential dumping |
| T1071.001 HTTP C2 | DNS logs + Proxy logs + NetFlow | 看不到 C2 beacon |
| T1547.001 Registry Run Key | Registry Events（Sysmon Event 12/13） | 看不到 persistence |

這張表的實際意義：如果你沒有 Sysmon Event 10，你對 T1055 Process Injection 的偵測能力是零，無論你有多少規則。規則是空炮。

## 不要被「綠色滿版」騙

這是 ATT&CK Navigator 最常被誤用的地方。有人把「有 Sigma 規則對應到這個 technique」等同於「這個 technique 我偵測得到」，這是錯的。

**紙上涵蓋度的四個坑**：

**坑 1：規則只在 lab 裡跑過**
Atomic Red Team 提供現成的攻擊模擬腳本，但大部分組織的 Sigma 規則從來沒跑過真實的 atomic test。規則可能邏輯錯誤，可能 field name 在你的 SIEM 裡對不上。

**坑 2：偵測僅覆蓋最簡單的變體**
T1059.001 有規則，但只抓 `powershell.exe -enc <base64>`，對 `pwsh.exe`、`powershell_ise.exe`、bypass execution policy 的 one-liner、AMSI bypass 全部看不到。

**坑 3：false positive rate 太高導致告警被關掉**
技術上有偵測能力，但因為誤報爆炸，SOC 把那條規則的告警關掉了，或調成低優先級從來沒人看。

**坑 4：data source 只覆蓋部分 endpoint**
Sysmon 只部署在 50% 的 Windows 主機，但 Navigator 上顯示「有偵測」。攻擊者在沒裝 Sysmon 的那台 server 上打，全程無聲。

**真正的涵蓋度 = 有遙測 × 有規則 × 規則有效 × 規則持續監控**

## 涵蓋度盲點分析流程

我們建議的分析流程：

```
Step 1：清點 data sources
  → 哪些 Event IDs / Sysmon events 你真的有在收？
  → 覆蓋多少 endpoint？

Step 2：把現有規則對映到 technique
  → 每條 Sigma 規則的 tags 裡應該有 attack.t1234.567
  → 用 sigma convert 或手動建 mapping 表

Step 3：找三類缺口
  a. 沒有 data source 的 technique（Layer 0 缺口）
  b. 有 data source 但沒有規則（Layer 1 缺口）
  c. 有規則但從未實測（Layer 2 缺口）

Step 4：對照你組織面對的威脅
  → 你的產業最常被哪些 APT 打？
  → 針對這些 group 的 technique 清單優先補缺口

Step 5：輸出優先改善清單
  → 以 CVSS 類似的 risk score 排序（威脅頻率 × 缺口深度）
```

## 踩雷

1. **Sigma rule tag 格式不對**：正確格式是 `attack.t1059.001`（全小寫，點分隔），不是 `ATT&CK:T1059`。格式錯誤 DeTT&CT 解析不到，自動對映會漏掉。

2. **一條規則對映到多個 technique 沒有標全**：一條 Sigma 規則可能同時命中 T1059.001 和 T1204.002，如果只標其中一個，涵蓋度地圖會低估實際能力——反而會在盲點分析時產生不必要的告警。

3. **Sub-technique 太細造成分散焦點**：ATT&CK v14 有超過 400 個 sub-technique。不是每個都值得逐一覆蓋，先把 parent technique 層次搞清楚，再往下展開高風險的 sub-technique。

4. **把 threat intelligence 報告的 TTPs 直接複製進 Navigator 當作「我的威脅模型」**：CTI 報告描述的是 APT 在某次行動的 TTPs，不等於你的環境一定會被相同方式攻擊。需要結合你的產業、暴露面、歷史事件客製化。

5. **忘記定期更新對映**：ATT&CK 每年更新 2 次，technique 會被新增、合併、重新編號。上次做涵蓋度分析是 v13，現在已經 v15，你的地圖已經過時了。

## 進階延伸

- **MITRE Caldera**：自動化 ATT&CK 模擬框架，可以跑指定的 technique 鏈，比 Atomic Red Team 更接近真實 APT 行為。涵蓋度分析後接 Caldera 驗證是標準流程。
- **ATT&CK Evaluations**：MITRE 對主流 EDR 產品做 ATT&CK 標準化測試，結果公開在 [https://attackevals.mitre-engenuity.org/](https://attackevals.mitre-engenuity.org/)。評估你的 EDR 的實際涵蓋率可以參考這裡。
- **Sigma rule-to-technique mapping automation**：Sigma HQ 的規則都有 `tags` 欄位，可以寫 Python 批次解析，自動建立規則庫的涵蓋度地圖，不需要手動對映。

## 本章重點整理

- 涵蓋度分 3 層：Visibility（data source）、Detection（規則）、Quality（有效性）
- DeTT&CT 把 data source 與規則對映整理成 YAML，產出 Navigator layer 做視覺化
- Data source 是前提：沒有遙測，規則是空炮
- Navigator 綠色滿版不等於真正有偵測能力——4 個坑：未實測、只抓基本變體、假陽性關掉、data source 覆蓋率不足
- 涵蓋度分析流程：清點 data source → 對映規則 → 找三類缺口 → 對照威脅模型 → 排優先順序

## 自我檢核

- [ ] 能說出三層涵蓋度模型（Visibility / Detection / Quality）的差別
- [ ] 知道 T1059.001 至少需要哪兩種 data source 才能有效偵測
- [ ] 能說出「Navigator 塗滿綠色但實際沒偵測到」的四個原因
- [ ] 知道 DeTT&CT 的 YAML 有哪兩種（data sources / detections）
- [ ] 能描述涵蓋度盲點分析的五步流程

## 延伸閱讀

1. **DeTT&CT GitHub** [https://github.com/rabobank-cdc/DeTTECT](https://github.com/rabobank-cdc/DeTTECT)
   — 工具本身加詳細文件，YAML schema 說明最完整；先讀 wiki 再看範例 YAML。

2. **MITRE ATT&CK Data Sources** [https://attack.mitre.org/datasources/](https://attack.mitre.org/datasources/)
   — 官方的 data source 清單與 technique 對映；規劃遙測架構時的必查資料。

3. **"A Measurement Study of ATT&CK Coverage"** — USENIX Security 2022
   — 學術研究分析 46 個開源偵測規則庫的實際 ATT&CK 涵蓋率，發現大多數只覆蓋不到 20% 的 technique；給你對「涵蓋率」的現實校準。

4. **ATT&CK Evaluations（MITRE Engenuity）** [https://attackevals.mitre-engenuity.org/](https://attackevals.mitre-engenuity.org/)
   — EDR 產品的標準化 ATT&CK 測試結果；評估你的 EDR 實際能力時的參考基準。

5. **"Prioritizing ATT&CK Techniques" — Center for Threat-Informed Defense**
   — 用 threat intelligence 頻率資料計算各 technique 的優先級；幫你決定有限資源先補哪個缺口。

---

偵測涵蓋度地圖有了，下一步要讓這些 Sigma/YARA 規則真的跑起來——這就是 SIEM 與 detection pipeline 的工作。

→ [Ch 11 SIEM 架構與 detection pipeline](./11-siem-detection-pipeline.md)
