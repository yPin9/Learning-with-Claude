# Ch 8 — Sigma 規則工程

> 目標：掌握 Sigma 規則的完整語法與工程實踐——從結構拆解、欄位對映、modifier 使用，到用 pySigma/sigma-cli 轉換到各 SIEM backend，以及假陽性調校和規則測試方法。本章給出三條可直接使用的 Sigma YAML 規則。
>
> 環境：`sigma-cli`（`pip install sigma-cli`），python 3.10+。pySigma backends 視需求安裝（`pip install pySigma-backend-splunk` 等）。

## 為什麼需要 Sigma？偵測邏輯的方言問題

SOC 的現實是：你今天用 Splunk，明年換 Elastic，後年換 Microsoft Sentinel。每次遷移，所有偵測規則都要重寫一遍。

更糟的是：你從社群拿到一條針對 Cobalt Strike 的偵測規則，但它是 Splunk SPL 格式，你要用 KQL（Sentinel）。你要手動翻譯，而且不確定語意是否完全等價。

Sigma 解決這個問題：它是**偵測邏輯的中間語言**。

```
                  ┌──────────────────────────────────┐
                  │         Sigma 規則（YAML）         │
                  │   描述偵測邏輯，不綁定任何平台      │
                  └──────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ┌────────┐     ┌──────────┐   ┌────────────┐
         │ Splunk │     │Elasticsearch│ │  Sentinel  │
         │  SPL   │     │  KQL/EQL  │  │  (KQL)     │
         └────────┘     └──────────┘   └────────────┘
              ▼               ▼               ▼
         ┌────────┐     ┌──────────┐   ┌────────────┐
         │Chronicle│    │  Devo   │   │ QRadar AQL │
         └────────┘     └──────────┘   └────────────┘
```

寫一條 Sigma 規則，透過 pySigma 轉換成你需要的任何 backend。社群規則庫（SigmaHQ）有數千條，直接拿來用。

## Sigma 規則結構全解

一條完整的 Sigma 規則的所有必要和可選欄位：

```yaml
title: 可疑的 PowerShell EncodedCommand 使用
id: 6c9b4e1f-1234-5678-abcd-ef0123456789       # UUID，唯一識別
status: experimental                             # test/experimental/stable/deprecated
description: |
  偵測 PowerShell 使用 -EncodedCommand 或其縮寫（-enc, -en, -e）
  執行 Base64 編碼的指令。這是常見的 payload 遞送手法，
  用於繞過腳本封鎖政策或混淆惡意指令。
references:
  - https://attack.mitre.org/techniques/T1059/001/
  - https://lolbas-project.github.io/lolbas/Binaries/Powershell/
author: Blue Team DFIR Course
date: 2026-08-01
modified: 2026-08-01
tags:
  - attack.execution
  - attack.t1059.001
  - attack.defense_evasion
  - attack.t1027

logsource:
  category: process_creation          # 對應到 Sysmon Event ID 1 / Event ID 4688
  product: windows

detection:
  selection:
    Image|endswith:
      - '\powershell.exe'
      - '\pwsh.exe'
    CommandLine|contains|all:        # 「contains all」= 所有條件都要成立（AND）
      - '-'
    CommandLine|re: '(?i)[\s\-]e(n(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?[\s]'
  filter_legitimate:
    CommandLine|contains:
      - '-EncodedCommand AAAA'       # 已知良性腳本（示意，實際要填真實白名單）
  condition: selection and not filter_legitimate

falsepositives:
  - 合法的自動化腳本使用 -EncodedCommand 傳遞複雜指令
  - 某些 SCCM/PDQ 部署工具

level: medium
```

### logsource 欄位

`logsource` 決定這條規則針對哪種 log 類型。Sigma 用抽象分類，讓 backend 知道如何對映到具體的 log 來源：

| category | 對應資料 |
|----------|----------|
| `process_creation` | Sysmon EID 1 / Windows EID 4688 |
| `network_connection` | Sysmon EID 3 |
| `image_load` | Sysmon EID 7 |
| `file_creation` | Sysmon EID 11 |
| `registry_set` | Sysmon EID 13 |
| `dns_query` | Sysmon EID 22 |
| `ps_script` | PowerShell EID 4104 |

`product: windows` 表示這是 Windows 平台的 log。部分規則有 `product: linux` 或 `service: apache`（指 Apache web server log）。

### detection 區塊：核心語法

detection 區塊是規則的心臟，由 **selection 區段** 和 **condition** 組成。

#### Selection 區段

```yaml
detection:
  selection_main:
    Image|endswith: '\powershell.exe'    # 單一值
    CommandLine|contains:
      - '-enc'         # list = OR（符合任一即可）
      - '-encoded'
      - '-EncodedCommand'
```

#### Modifiers（修飾符）

Modifiers 加在欄位名稱後面，用 `|` 分隔，改變比對邏輯：

| Modifier | 語意 |
|----------|------|
| `contains` | 字串包含（substring match） |
| `startswith` | 字串開頭 |
| `endswith` | 字串結尾 |
| `re` | 正規表達式（regex） |
| `all` | 後接 list，所有元素都要符合（AND，預設 list 是 OR） |
| `base64offset` | 在 base64 編碼的字串中搜尋原始字串的位移變體 |
| `windash` | 同時比對 Windows 的 `-` 和 `/` 開關前綴 |
| `cidr` | CIDR 網路範圍比對（用於 IP 欄位） |
| `lt` / `lte` / `gt` / `gte` | 數值比較 |

**`all` modifier 的重要性**：

```yaml
# 以下 CommandLine 要同時包含「-enc」和「MiniDump」才觸發
CommandLine|contains|all:
  - '-enc'
  - 'MiniDump'

# 而以下是 OR（含任一即觸發）
CommandLine|contains:
  - '-enc'
  - 'MiniDump'
```

#### Condition 語法

```yaml
condition: selection                         # 單一 selection
condition: selection and not filter          # selection 且非 filter
condition: 1 of selection_*                  # 所有以 selection_ 開頭的 selection，符合其中一個即可
condition: all of them                       # 所有定義的 selection 都要符合
condition: selection_cmd and (sel_a or sel_b) # 複合邏輯
```

`condition` 支援 `and`、`or`、`not` 和括號分組。

## 三條完整的 Sigma 規則

### 規則一：可疑 PowerShell EncodedCommand

```yaml
title: Suspicious PowerShell EncodedCommand Usage
id: 6c9b4e1f-a4b2-4f3d-8e1c-0d2f3a4b5c6d
status: stable
description: |
  偵測 PowerShell 使用 -EncodedCommand 參數（含常見縮寫：-enc, -en, -e）
  執行 Base64 編碼的指令。正常系統管理少有合法理由使用此參數。
  攻擊者常用此技術傳遞混淆 payload。
references:
  - https://attack.mitre.org/techniques/T1059/001/
  - https://lolbas-project.github.io/lolbas/Binaries/Powershell/
author: Blue Team DFIR Course
date: 2026-08-01
tags:
  - attack.execution
  - attack.t1059.001
  - attack.defense_evasion
  - attack.t1027

logsource:
  category: process_creation
  product: windows

detection:
  selection_img:
    Image|endswith:
      - '\powershell.exe'
      - '\pwsh.exe'
  selection_flags:
    CommandLine|re: '(?i)[\s\-](e(n(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?|ec)[\s]'
  filter_known_tools:
    # 已知使用 -enc 的合法工具（依實際環境調整）
    ParentImage|endswith:
      - '\vscode\code.exe'
  condition: all of selection_* and not filter_known_tools

falsepositives:
  - 正當的自動化指令碼使用 EncodedCommand 傳遞含特殊字元的參數
  - Visual Studio Code 的 PowerShell 延伸模組
level: medium
```

### 規則二：rundll32 載入非標準 DLL

```yaml
title: Rundll32 Loading DLL from Suspicious Path
id: a2b3c4d5-e6f7-8901-abcd-ef1234567890
status: experimental
description: |
  偵測 rundll32.exe 從非標準目錄載入 DLL。正常情況下 rundll32 只應載入
  System32/SysWow64 或已知應用程式目錄中的 DLL。
  攻擊者使用 rundll32 執行 shellcode 或繞過應用程式白名單（T1218.011）。
references:
  - https://attack.mitre.org/techniques/T1218/011/
  - https://lolbas-project.github.io/lolbas/Binaries/Rundll32/
author: Blue Team DFIR Course
date: 2026-08-01
tags:
  - attack.defense_evasion
  - attack.t1218.011
  - attack.execution

logsource:
  category: process_creation
  product: windows

detection:
  selection:
    Image|endswith: '\rundll32.exe'
  filter_legitimate_paths:
    CommandLine|contains:
      - 'C:\Windows\System32\'
      - 'C:\Windows\SysWow64\'
      - 'C:\Windows\SysNative\'
      - 'C:\Program Files\'
      - 'C:\Program Files (x86)\'
  filter_no_args:
    CommandLine|endswith:
      - 'rundll32.exe'
      - 'rundll32.exe '
  suspicious_patterns:
    CommandLine|contains:
      - '.dll,'          # rundll32 的標準呼叫格式：.dll,FunctionName
      - 'javascript:'    # rundll32 執行 JavaScript
      - 'shell32.dll,ShellExec_RunDLL'  # 濫用 shell32 執行任意命令
  condition: selection and suspicious_patterns and not filter_legitimate_paths and not filter_no_args

falsepositives:
  - 某些舊版應用程式在非標準路徑安裝 DLL
  - 使用者個人目錄下的合法 COM Surrogate 呼叫
level: high
```

### 規則三：LSASS 記憶體存取（Credential Dumping）

```yaml
title: LSASS Memory Access Indicating Credential Dumping
id: f1a2b3c4-d5e6-7890-abcd-ef0987654321
status: stable
description: |
  偵測進程對 lsass.exe 開啟帶有讀取記憶體權限的 handle。
  這是 Mimikatz、ProcDump、Task Manager dump 等工具竊取憑證的
  必要行為（T1003.001）。非系統進程幾乎沒有合法理由以此存取 LSASS。
references:
  - https://attack.mitre.org/techniques/T1003/001/
  - https://www.sans.org/blog/protecting-privileged-domain-accounts-lsass-memory/
author: Blue Team DFIR Course
date: 2026-08-01
tags:
  - attack.credential_access
  - attack.t1003.001

logsource:
  category: process_access
  product: windows

detection:
  selection:
    TargetImage|endswith: '\lsass.exe'
    GrantedAccess|contains:
      - '0x1010'    # PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION
      - '0x1410'    # 同上加 PROCESS_QUERY_INFORMATION
      - '0x40'      # PROCESS_DUP_HANDLE
      - '0x1fffff'  # PROCESS_ALL_ACCESS
  filter_system_processes:
    SourceImage|startswith:
      - 'C:\Windows\System32\'
      - 'C:\Windows\SysWow64\'
    SourceImage|endswith:
      - '\lsass.exe'       # LSASS 自身
      - '\werfault.exe'    # Windows Error Reporting
      - '\MsMpEng.exe'     # Windows Defender
  filter_av:
    SourceImage|contains:
      - 'CrowdStrike'      # EDR 自身需要存取 LSASS
      - 'SentinelOne'
      - 'CylanceSvc'
  condition: selection and not filter_system_processes and not filter_av

falsepositives:
  - AV/EDR 解決方案的自我保護或掃描行為（需加入 filter_av）
  - WER（Windows Error Reporting）在 LSASS 崩潰時建立 dump
  - 合法的記憶體分析工具在授權的安全測試環境
level: high
```

## 欄位對映到不同 Backend

Sigma 規則的 `logsource` 和欄位名稱是抽象的，各個 SIEM 的欄位名稱不同。pySigma 的 backend 負責這個翻譯。

### pySigma / sigma-cli 的工作流

```bash
# 安裝 sigma-cli 和所需 backend
pip install sigma-cli pySigma-backend-splunk pySigma-backend-elasticsearch

# 將 Sigma 規則轉換為 Splunk SPL
sigma convert -t splunk rule.yml

# 轉換為 Elasticsearch Query DSL
sigma convert -t elasticsearch rule.yml

# 轉換為 Microsoft Sentinel KQL
sigma convert -t kusto rule.yml

# 加上 pipeline（欄位對映設定）
sigma convert -t splunk -p sysmon rule.yml

# 批次轉換整個目錄
sigma convert -t splunk rules/
```

### 同一條規則在不同 Backend 的輸出對比

以規則一（PowerShell EncodedCommand）為例：

**Splunk SPL（示意，依版本/樣本而異）**：
```
index=* sourcetype="WinEventLog:Microsoft-Windows-Sysmon/Operational" EventCode=1
(Image="*\powershell.exe" OR Image="*\pwsh.exe")
CommandLine=~ "(?i)[\s\-](e(n(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?|ec)[\s]"
NOT ParentImage="*\code.exe"
```

**Elasticsearch DSL（示意，依版本/樣本而異）**：
```json
{
  "query": {
    "bool": {
      "filter": [
        {"terms": {"winlog.event_data.Image.keyword": ["*\\powershell.exe", "*\\pwsh.exe"]}},
        {"regexp": {"winlog.event_data.CommandLine": "(?i)[\\s\\-](e(n(c.*"}}
      ],
      "must_not": [
        {"wildcard": {"winlog.event_data.ParentImage.keyword": "*\\code.exe"}}
      ]
    }
  }
}
```

**Microsoft Sentinel KQL（示意，依版本/樣本而異）**：
```kql
SecurityEvent
| where EventID == 4688
| where (NewProcessName endswith "\\powershell.exe" or NewProcessName endswith "\\pwsh.exe")
| where CommandLine matches regex @"(?i)[\s\-](e(n(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?|ec)[\s]"
| where not(ParentProcessName endswith "\\code.exe")
```

### Backend 欄位對映說明

不同環境的 Sysmon log 欄位名稱可能不同，pySigma pipeline 處理這個對映：

| Sigma 抽象欄位 | Splunk（Sysmon App） | Elasticsearch（Winlogbeat） |
|---------------|---------------------|---------------------------|
| `Image` | `Image` | `winlog.event_data.Image` |
| `CommandLine` | `CommandLine` | `winlog.event_data.CommandLine` |
| `ParentImage` | `ParentImage` | `winlog.event_data.ParentImage` |
| `TargetImage` | `TargetImage` | `winlog.event_data.TargetImage` |
| `GrantedAccess` | `GrantedAccess` | `winlog.event_data.GrantedAccess` |

自訂 pipeline（YAML 格式）可以處理你的環境的欄位命名慣例。

## 假陽性調校策略

FP 調校是 Sigma 規則工程最花時間的部分。系統化的做法：

### 步驟一：在測試環境建立基線

用你的 Sigma 規則在正常（無攻擊）環境跑 7 天，收集所有觸發的事件。

### 步驟二：分類觸發原因

| FP 類型 | 範例 | 調校方式 |
|---------|------|---------|
| 已知工具 | SCCM 用 `-enc` 執行部署腳本 | 加 `ParentImage` filter |
| 合法路徑 | 系統更新程序從 `%TEMP%` 執行 | 加路徑 filter |
| 帳號例外 | 系統帳號（`NT AUTHORITY\SYSTEM`） | 加 `User` filter |
| 特定時間 | 每天凌晨 2 點的排程任務 | 考慮時間窗口 filter（部分 backend 支援）|

### 步驟三：在規則加入 filter 區段

```yaml
detection:
  selection:
    ...  # 主要偵測邏輯
  filter_sccm:
    ParentImage|endswith: '\CcmExec.exe'    # SCCM 用戶端
  filter_system:
    User: 'NT AUTHORITY\SYSTEM'
  condition: selection and not 1 of filter_*
```

`not 1 of filter_*` 會排除所有以 `filter_` 開頭的 selection 所定義的情況。

### FP 調校的邊界

調校有個臨界點：當你的 filter 太多，你可能把真實攻擊也過濾掉了。攻擊者如果知道你的 filter 是「SCCM parent process 就略過」，就會透過 SCCM 部署惡意腳本。

原則：**filter 應該基於可信的屬性（已知良性工具的路徑），而非行為本身**。

## 規則測試

光靠眼睛看規則不夠，要實際測試它能不能觸發。

### 用 Atomic Red Team 觸發

Atomic Red Team（[https://github.com/redcanaryco/atomic-red-team](https://github.com/redcanaryco/atomic-red-team)）提供每個 ATT&CK technique 的模擬執行指令：

```powershell
# 安裝 Invoke-AtomicRedTeam
Install-Module -Name invoke-atomicredteam -Scope CurrentUser

# 執行 T1059.001（PowerShell EncodedCommand）的原子測試
Invoke-AtomicTest T1059.001 -TestNumbers 1

# 清理
Invoke-AtomicTest T1059.001 -TestNumbers 1 -Cleanup
```

執行後，確認你的 SIEM 有捕捉到對應事件，且規則有觸發告警。

### 用 sigma-cli 測試邏輯

sigma-cli 的 `check` 子命令可以驗證規則語法，但不能做完整的功能測試：

```bash
# 檢查規則語法
sigma check rule.yml

# 如果有 JSON 格式的事件測試向量（部分工具支援）
sigma test --events events.json rule.yml
```

### sigma 規則的 falsepositives 欄位

`falsepositives` 欄位是文件性質的，讓使用這條規則的分析師知道哪些情況可能是誤報，不是過濾邏輯。要把 FP 邏輯放在 `detection` 的 filter 區段。

## 規則品質清單

寫完一條 Sigma 規則，對照這個清單：

- [ ] `id` 欄位有唯一的 UUID？
- [ ] `status` 設定正確（experimental/stable）？
- [ ] `tags` 有 ATT&CK technique 對映（`attack.t1059.001` 格式）？
- [ ] `logsource` 指定了正確的 `category` 和 `product`？
- [ ] `detection` 的 condition 語法正確（用 `sigma check` 驗證）？
- [ ] `falsepositives` 有列出已知誤報情境？
- [ ] `level` 設定合理（low/medium/high/critical）？
- [ ] 在測試環境確認過觸發（Atomic Red Team 或等效）？
- [ ] 加入版本控制（git commit）？
- [ ] 在 `references` 連結了 ATT&CK technique 或其他參考資料？

## Sigma 規則組織與維護

### SigmaHQ 社群規則庫

不需要從零寫所有規則。SigmaHQ（[https://github.com/SigmaHQ/sigma](https://github.com/SigmaHQ/sigma)）是社群維護的規則庫，覆蓋數千個 ATT&CK technique：

```bash
# clone 社群規則庫
git clone https://github.com/SigmaHQ/sigma.git

# 查看 Windows process creation 相關規則
ls sigma/rules/windows/process_creation/

# 轉換整個 Windows 目錄到 Splunk
sigma convert -t splunk sigma/rules/windows/
```

從社群規則開始，根據你的環境調校 filter，而不是從零寫。

### 版本控制與 Detection-as-Code

每條規則都應該在 git 管理：
- 誰改了什麼、為什麼改（commit message）
- 改動是否影響 FP/FN 率（PR review）
- 生產環境只部署通過測試的規則（CI pipeline）

這是 Ch 12 Detection-as-Code 的具體實踐。

## 踩雷：錯誤直覺 → 正確認識

**1. 「Sigma 規則轉換出來的查詢就可以直接用」**
→ pySigma 的轉換負責語法翻譯，但欄位對映需要 pipeline 設定，而 pipeline 要配合你的環境（Winlogbeat 的欄位和 NXLog 的欄位不同）。直接貼轉換後的查詢到 SIEM 可能什麼都查不到，因為欄位名稱不對。

**2. 「`contains` modifier 很安全，不會有 regex injection」**
→ `contains` 會被轉成各 backend 的 substring match，通常沒有 injection 問題。但 `re` modifier 用 regex 時，不同 SIEM 支援的 regex 語法不同（Splunk 是 PCRE，Elasticsearch 是 Lucene regex 有限制）。一條用 lookahead 的 regex 在 Splunk 能跑，在 ES 可能直接語法錯誤。

**3. 「我的 filter 把所有 FP 都排除了，這條規則很完美」**
→ FP 全部排掉，FN 可能很高。如果你的 filter 是「系統管理員帳號的所有 PowerShell 行為都略過」，攻擊者只要用系統管理員帳號執行就完全隱形。filter 要針對具體的可信屬性，而非整個帳號或行為類別。

**4. 「Sigma `level: high` 就是嚴重威脅，要立刻處理」**
→ `level` 是規則作者的主觀評估，在你的環境可能是 FP。`level: high` 只是給 SIEM 和 SOAR 一個優先序參考，不代表一定是真實威脅。每個告警還是要分析師判斷。

**5. 「我直接用 SigmaHQ 的規則就好，不需要理解細節」**
→ SigmaHQ 的規則是針對通用情況設計的，你的環境有特定軟體、特定路徑、特定帳號，直接用社群規則幾乎必定產生大量 FP（或因欄位對映錯誤而完全不觸發）。用社群規則當起點，但一定要理解每個 filter，依環境調校。

## 進階延伸

### Sigma 的 `aggregate` 語法（Counted Events）

部分偵測需要「X 分鐘內發生 N 次」的邏輯，這是 computed indicator：

```yaml
detection:
  selection:
    EventID: 4625             # 登入失敗
    TargetUserName|contains: 'admin'
  condition: selection | count() > 10
  timeframe: 5m               # 5 分鐘內超過 10 次

# 或按欄位分組計數
  condition: selection | count() by TargetUserName > 5
```

注意：aggregate 語法的 backend 支援程度不一，轉換前確認你的 SIEM backend 支援。

### Sigma 相關性規則（Correlation Rules）

pySigma v0.10+ 引入了 correlation rule 格式，可以跨事件建立關聯（如「A 事件後 5 分鐘內發生 B 事件」），這讓 Sigma 能表達更複雜的行為鏈偵測。目前（2026）仍在發展中，各 backend 支援程度有限，但代表 Sigma 的演進方向。

## 本章重點整理

- Sigma 是偵測邏輯的中間語言，一次寫好多個 backend 可用。
- 規則結構：`logsource`（資料來源）、`detection`（邏輯）、`condition`（組合）、`falsepositives`（文件）、`level`（優先序）。
- Modifier：`contains`/`endswith`/`startswith`/`re`/`all`/`base64offset`，其中 `all` modifier 把 list 的 OR 邏輯改成 AND。
- condition 支援 `and`/`or`/`not`，以及 `1 of selection_*`（任一）和 `all of them`（全部）的模式。
- pySigma/sigma-cli 負責轉換，要搭配正確的 pipeline 處理欄位對映。
- FP 調校要系統化：建基線、分類 FP 原因、加 filter 區段，注意不能過度過濾。
- 用 Atomic Red Team 測試規則實際觸發，不能只靠眼睛審閱。
- SigmaHQ 社群規則庫是起點，但必須依環境調校。

## 自我檢核

1. `CommandLine|contains|all: ['-enc', 'MiniDump']` 的語義是什麼？和 `CommandLine|contains: ['-enc', 'MiniDump']` 有何不同？
2. `condition: 1 of selection_* and not 1 of filter_*` 是什麼邏輯？用文字描述。
3. 你寫了一條偵測 PowerShell EncodedCommand 的規則，測試後發現 SCCM 每天觸發 500 條 FP。應該怎麼調校？調校時要注意什麼風險？
4. 為什麼 pySigma 的轉換不能直接用，還需要 pipeline？pipeline 解決了什麼問題？
5. Sigma 的 `status: experimental` 和 `status: stable` 的實際含義是什麼？在部署策略上應有何差異？

## 延伸閱讀

1. **SigmaHQ 官方文件** — [https://github.com/SigmaHQ/sigma/wiki](https://github.com/SigmaHQ/sigma/wiki)
   讀哪：Specification 頁面（規則語法完整定義）；Modifiers 清單
   學什麼：所有 modifier 的語意定義與 backend 支援狀況，是寫規則時的參考手冊
   關聯：本章所有語法的權威來源

2. **pySigma 文件** — [https://sigmahq-pysigma.readthedocs.io/](https://sigmahq-pysigma.readthedocs.io/)
   讀哪：Pipeline 設定與 backend 選項
   學什麼：如何寫自訂 pipeline 讓欄位對映符合你的環境；如何擴充 backend
   關聯：實際部署 Sigma 到你的 SIEM 時必讀

3. **"Sigma: Generic Signatures for SIEM Systems" — Florian Roth, Thomas Patzke（原始論文）** — 在 VirusBulletin 2017 年會議論文集可找到
   讀哪：Introduction 和 Design Goals 段落
   學什麼：Sigma 的設計哲學與初衷，理解為何它選擇這個抽象層次而非更高或更低
   關聯：Ch 12 Detection-as-Code 的思想脈絡

4. **Atomic Red Team** — [https://github.com/redcanaryco/atomic-red-team](https://github.com/redcanaryco/atomic-red-team)
   讀哪：atomics/ 目錄下對應你正在寫規則的 ATT&CK technique
   學什麼：攻擊者執行這個 technique 的具體方式，幫助你理解規則需要覆蓋哪些變體
   關聯：Ch 10 ATT&CK 對映與偵測涵蓋度

5. **SANS, "Sigma Rules for Threat Hunting"** — 在 SANS 部落格搜尋；Marcus Bakker 等人的實務文章
   讀哪：選擇 2022 年後的文章，涵蓋 pySigma 新版本
   學什麼：在真實 SOC 中組織、維護和部署 Sigma 規則的流程，以及 detection pipeline 的設計
   關聯：Ch 11 SIEM 架構與 detection pipeline、Ch 12 Detection-as-Code

---

Sigma 把偵測邏輯從 SIEM 語言中解放出來。但偵測不只有 log 事件：惡意程式的二進位特徵怎麼偵測？那是 YARA 的地盤。

→ [Ch 9 YARA 規則工程](./09-yara-rule-engineering.md)
