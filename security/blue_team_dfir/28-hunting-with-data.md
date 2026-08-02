# Ch 28 — 用資料狩獵：KQL/SPL 查詢思維

> 目標：建立用查詢語言做 threat hunting 的核心思維，掌握 KQL（Kusto Query Language，Microsoft Sentinel/Defender 使用）與 SPL（Splunk Processing Language）的 hunting 慣用語法，用統計分析（stacking、LFO）找資料裡的離群值，知道如何建立 baseline 和做 enrichment。

> 環境：本章查詢示範以 Microsoft Sentinel（KQL）和 Splunk（SPL）為主。查詢語法合法，但「結果」欄位標（示意）——學習目標是查詢思維，而非在特定環境跑出特定數字。

## 為什麼需要查詢思維？

工具永遠在換，查詢思維不換。Splunk、Elastic、Sentinel、Chronicle、QRadar 語法各異，但背後的邏輯是一樣的：

1. 縮小資料範圍（filter）
2. 計算統計量（aggregate）
3. 找離群值（anomaly）
4. 用背景知識解釋（enrich + interpret）

一個會寫 SQL 的人，學新的 SIEM 查詢語言的成本主要在語法，不在思維。先學對思維，工具不是問題。

## 先建立直覺：三種 Hunting 查詢模式

### 模式 1：條件過濾（Filter-Based）

直接比對特定條件，找到符合的事件。這是最直觀的方式，適合假設非常明確的情況。

問題：「有沒有程序執行 Base64 encoded PowerShell？」

```kql
// KQL - Microsoft Sentinel / Defender
DeviceProcessEvents
| where FileName =~ "powershell.exe"
| where ProcessCommandLine has_any ("-enc", "-EncodedCommand", "-ec")
| project Timestamp, DeviceName, AccountName, ProcessCommandLine
| order by Timestamp desc
```

```spl
// SPL - Splunk（示意）
index=sysmon EventCode=1 Image="*\\powershell.exe"
  (CommandLine="*-enc*" OR CommandLine="*-EncodedCommand*")
| table _time, ComputerName, User, CommandLine
| sort - _time
```

**限制**：Filter-based 的查詢取決於你知道在找什麼。攻擊者知道你在找 `-enc`，所以他們可能用縮寫 `-e` 或者把 encoding 動作藏在更前面的腳本裡。

### 模式 2：統計聚合（Statistical/Stacking）

計算某個欄位的頻率分佈，然後看兩端——特別高頻的（噪音）和特別低頻的（可能是惡意的離群值）。

**Stacking**（堆疊分析）：把同類事件的某個屬性計數，看哪些只出現一次或極少次。

**LFO（Least-Frequency-of-Occurrence）**：Long Tail 分析。頻繁的是正常，罕見的值得看。

### 模式 3：時序比對（Temporal/Baseline）

建立行為的時間 baseline，然後找偏離的時間點或區間。適合偵測「平常不做但突然做了一件事」這類行為。

## Stacking 實戰：親子程序關係分析

這是 hunting 最有效的技術之一。**正常環境裡，親子程序關係的種類是有限且穩定的**。攻擊者執行惡意程序時，往往製造罕見的親子關係。

### KQL 版本

```kql
// 統計所有親子程序組合的出現次數（過去 7 天）
DeviceProcessEvents
| where Timestamp > ago(7d)
| summarize count() by ParentProcessName = tolower(InitiatingProcessFileName),
                       ChildProcessName  = tolower(FileName)
| where count_ < 5   // 只看罕見組合
| order by count_ asc
// 結果（示意）：
// word.exe -> powershell.exe    出現 1 次  ← 可疑
// excel.exe -> cmd.exe          出現 2 次  ← 可疑
// svchost.exe -> notepad.exe   出現 1 次  ← 可疑
```

### SPL 版本

```spl
// Splunk - 同樣邏輯（示意）
index=sysmon EventCode=1 earliest=-7d
| stats count by ParentImage, Image
| where count < 5
| sort count
```

**解讀**：
- `svchost.exe` 生出 `powershell.exe` 在你的環境出現 1 次 → 強烈可疑，`svchost` 不應該直接啟動 PowerShell
- `explorer.exe` 生出 `cmd.exe` 在你的環境出現 3000 次 → 是噪音，使用者習慣開命令列

踩雷：stacking 的結果必須對照環境背景。一個大量使用 RPA 工具的公司，`excel.exe` -> `cmd.exe` 可能是正常的自動化流程。

## Long Tail 分析：找罕見 User-Agent

網路日誌裡的 User-Agent 是攻擊者常見的 C2 指紋：

```kql
// 找出現次數極少的 User-Agent（示意）
let common_uas = CommonSecurityLog
| where TimeGenerated > ago(30d)
| summarize count() by RequestClientApplication
| where count_ > 100;   // 定義「常見」閾值
// 反過來找不常見的
CommonSecurityLog
| where TimeGenerated > ago(30d)
| where isnotempty(RequestClientApplication)
| summarize count() by RequestClientApplication, DestinationIP
| join kind=leftanti common_uas on RequestClientApplication
| order by count_ asc
```

```spl
// SPL 版本（示意）
index=proxy earliest=-30d
| stats count by cs_UserAgent, dest_ip
| where count < 10
| sort count
```

這個查詢能找到：
- C2 框架的預設 User-Agent（如舊版 Cobalt Strike 的 `Mozilla/4.0 (compatible; MSIE 7.0;...)`）
- 不常見的工具發出的 HTTP 請求
- 員工機器上的非標準程式在打外網

## Baseline 建立

Baseline 的本質是：用足夠長的歷史資料定義「正常」，然後讓查詢只回傳「不正常」。

### 動態 Baseline（滑動視窗）

```kql
// 用過去 30 天的平均，找今天異常高的程序執行次數（示意）
let historical_baseline = DeviceProcessEvents
| where Timestamp between(ago(30d) .. ago(1d))
| summarize avg_count = count() by FileName, bin(Timestamp, 1d)
| summarize baseline_avg = avg(avg_count),
            baseline_stddev = stdev(avg_count)
  by FileName;

DeviceProcessEvents
| where Timestamp > ago(1d)
| summarize today_count = count() by FileName
| join kind=inner historical_baseline on FileName
| where today_count > (baseline_avg + 3 * baseline_stddev)  // 3 sigma
| project FileName, today_count, baseline_avg, baseline_stddev
| order by today_count desc
```

**注意**：3-sigma 閾值在大量執行的程序上很難觸發（因為標準差也大），需要根據每個程序的特性調整。這不是一刀切的解法，是起點。

### 靜態 Baseline（已知好名單）

更簡單但有效的方式：建立已知合法程序的白名單，查詢時直接排除：

```kql
let known_admin_tools = datatable(ToolName: string)
[ "psexec.exe", "wmic.exe", "net.exe", "sc.exe", "reg.exe" ];

DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName in~ (known_admin_tools)
// 現在只看這些工具是從哪裡被呼叫的
| summarize count() by InitiatingProcessFileName, FileName, AccountName
| where count_ < 3
| order by count_ asc
```

這個查詢的邏輯：我知道這些工具偶爾合法使用，但我要看**罕見的使用情境**，也就是很少見的那個人在很少見的情況下執行它。

## Enrichment：讓資料說更多

找到候選事件後，單靠一個 event 幾乎無法確定是不是惡意。Enrichment（豐富化）是把多個資料來源的資訊疊加到同一筆事件上。

常見的 enrichment 操作：

```kql
// 把程序事件和 TI（威脅情報）IP 清單關聯（示意）
let ti_ips = externaldata(IPAddress: string)
    [@"https://your-ti-feed/malicious_ips.csv"]
    with (format="csv");

DeviceNetworkEvents
| where Timestamp > ago(7d)
| where RemoteIPType == "Public"
| join kind=inner ti_ips on $left.RemoteIP == $right.IPAddress
| project Timestamp, DeviceName, AccountName, RemoteIP, RemotePort, InitiatingProcessFileName
```

另一種 enrichment：把 IP 對到地理位置，找異常的國家連線：

```kql
DeviceNetworkEvents
| where Timestamp > ago(7d)
| where RemoteIPType == "Public"
| extend GeoInfo = geo_info_from_ip_address(RemoteIP)
| where GeoInfo.country != "Taiwan"   // 你的組織主要在台灣
| summarize count() by RemoteIP, tostring(GeoInfo.country), DeviceName
| order by count_ asc
```

## 視覺化：讓人腦做它擅長的事

查詢返回的表格數據，人腦在視覺化後更能找到模式：

- **時序圖**：事件數量對時間，找突增/突降（beaconing 的周期性信號在時序圖上很明顯）
- **熱圖**：工作時間 vs 非工作時間的行為，找夜晚/週末的異常活躍
- **桑基圖（Sankey diagram）**：親子程序關係的流向，罕見路徑一眼看出
- **網路圖**：主機間的橫向移動路徑

```kql
// KQL：畫出 beaconing 的時序分佈（示意）
DeviceNetworkEvents
| where RemoteIP == "203.0.113.1"   // 可疑 IP
| summarize count() by bin(Timestamp, 10m)
| render timechart
// 如果 10 分鐘 bin 的計數非常穩定（如每次 1-3 個），這是 beaconing 特徵
```

## 完整 Hunt 查詢範例：找罕見的網路連線程序

這個查詢把三種模式（過濾、stacking、enrichment）組合起來：

```kql
// 找非常規程序發起的外部 TCP 連線，排除已知合法程序（示意）
let known_network_procs = dynamic([
    "chrome.exe", "firefox.exe", "msedge.exe", "outlook.exe",
    "onedrive.exe", "teams.exe", "svchost.exe", "lsass.exe",
    "services.exe", "wininit.exe", "spoolsv.exe", "msiexec.exe"
]);

DeviceNetworkEvents
| where Timestamp > ago(7d)
| where RemoteIPType == "Public"
| where ActionType == "ConnectionSuccess"
| where InitiatingProcessFileName !in~ (known_network_procs)
| summarize
    connection_count = count(),
    unique_ips = dcount(RemoteIP),
    unique_ports = dcount(RemotePort),
    sample_cmdline = any(InitiatingProcessCommandLine)
  by InitiatingProcessFileName, DeviceName, AccountName
| where connection_count < 10    // 只看罕見的
| order by connection_count asc
// 結果（示意）：
// mshta.exe 發起 3 次外部連線，目標 198.51.100.5:443 → 高可疑
// certutil.exe 發起 1 次外部連線 → 高可疑（certutil 不應自己打外網）
```

## 踩雷

1. **查詢範圍太大導致超時**：不加時間過濾或資料量過大時，SIEM 查詢會超時或費用暴增。養成習慣：先用小時間窗口（1d）測試查詢邏輯，確認後再拉長到 7d/30d。

2. **Stacking 沒有環境背景就下判斷**：「這個程序只出現 2 次所以可疑」需要配合環境知識。IT 部門剛部署的新工具可能本來就只執行了 2 次。狩獵者需要和 IT/運維溝通。

3. **忽略 case-sensitivity**：Windows 的程序名稱在日誌裡大小寫可能混用（`PowerShell.exe`、`powershell.exe`、`POWERSHELL.EXE` 都可能出現）。KQL 用 `=~`（case-insensitive），SPL 用 `lower()` 或加 `case_sensitive=false`。

4. **只看計數，不看實際 commandline**：count() 告訴你頻率，但確認是否惡意還是要看原始的命令列、網路目標、父程序。stacking 找到的候選必須人工回去看詳細記錄。

5. **Enrichment 資料品質問題**：TI feed 有 false positive，地理 IP 資料庫精度有限，WHOIS 資訊過時。Enrichment 提高信心，但不是確定判斷的唯一依據。

## 進階延伸

- **Jupyter Notebook + Pandas + KQL/SPL API**：把 SIEM 查詢結果拉到 Python 環境做更複雜的統計分析（clustering、isolation forest 異常偵測），Sentinel 有官方的 MSTICPy 函式庫。
- **Elastic EQL（Event Query Language）**：專為時序事件關聯設計的查詢語言，原生支援「A 發生後 B 在 5 秒內發生」這類時序查詢，對 process chain 分析特別強。
- **Graph Analysis**：用網路圖分析橫向移動路徑，工具有 BloodHound（AD 關係圖）和 Neo4j 配合日誌匯入。

## 本章重點整理

- 三種 hunting 查詢模式：filter、stacking（LFO/long tail）、temporal baseline
- Stacking 的核心：計頻率，看兩端，特別是低頻的罕見值
- Baseline = 定義「正常」，才能找「不正常」
- Enrichment 讓單筆事件得到更多背景，提高或降低可疑程度
- 查詢找到候選後，必須人工看原始記錄確認；工具輔助判斷，人才下結論

## 自我檢核

- [ ] 我能用 KQL 或 SPL 寫出一個 stacking 查詢，找罕見的親子程序關係
- [ ] 我能解釋 LFO（Least-Frequency-of-Occurrence）的直覺和使用場景
- [ ] 我知道建立 baseline 的兩種方式（動態和靜態）各適合什麼情境
- [ ] 我能說出至少三種 enrichment 的資料來源和它們能增加什麼資訊
- [ ] 我能識別自己查詢裡可能的 case-sensitivity 問題

## 延伸閱讀

1. **[KQL 官方文件 - Azure Monitor / Sentinel](https://learn.microsoft.com/en-us/azure/data-explorer/kusto/query/)** — 語法全覽；hunting 最常用的是 `summarize`、`join`、`datatable`、`let` 這幾節，重點讀。

2. **[Splunk Search Reference](https://docs.splunk.com/Documentation/Splunk/latest/SearchReference/WhatsInThisManual)** — SPL 官方文件；hunting 重點：`stats`、`eventstats`、`streamstats`、`inputlookup`。

3. **[MSTICPy - Microsoft Threat Intelligence Python Library](https://msticpy.readthedocs.io/)** — Sentinel 官方 Python 分析函式庫，把 KQL 結果帶進 Jupyter 做進階統計；GitHub 上有大量範例 notebook。

4. **[Elastic EQL Reference](https://www.elastic.co/guide/en/elasticsearch/reference/current/eql.html)** — EQL 的時序事件查詢語法，對 process chain 和 beaconing 偵測特別有用；和 KQL/SPL 比較著讀。

5. **[SANS Threat Hunting Summit 講稿](https://www.sans.org/cyber-security-summit/archives/)** — 每年都有大量 hunting 查詢分享，實際案例最多，搜「stacking」「LFO」「baseline」找相關演講。

---

→ [Ch 29 狩獵常見 TTP：LOLBins/PowerShell/WMI](./29-hunting-common-ttps.md)
