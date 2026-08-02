# Ch 11 — SIEM 架構與 Detection Pipeline

> 目標：理解 log 從 endpoint 到 SIEM 的完整資料流、每個階段能做什麼、在哪一層寫什麼樣的偵測邏輯，以及 alert fatigue 是怎麼殺死 SOC 的。

## 為什麼需要 SIEM？

你有 Sigma 規則、YARA 規則、ATT&CK 涵蓋度地圖——但這些東西都要有個地方跑。

SIEM（Security Information and Event Management，安全資訊與事件管理）是整個 detection pipeline 的核心節點：把散落在數十或數千台機器的 log 彙整起來、正規化成統一格式、讓你的規則邏輯在上面跑、產出告警。

沒有 SIEM，你的偵測規則跟沒有一樣：每台機器的 log 各自為政，沒有 correlation，攻擊者的橫向移動在你眼前展開，你只能逐台 SSH 進去查。

## Detection Pipeline 資料流全景

```
Endpoint / Cloud / Network
         │
         │ raw events
         ▼
 ┌───────────────────┐
 │   Log 收集層      │  Sysmon / ETW / auditd / CloudTrail / Zeek
 │   Collection      │  local agent（Winlogbeat / Fluentd / NXLog）
 └────────┬──────────┘
          │ raw log（JSON / syslog / CEF）
          ▼
 ┌───────────────────┐
 │   傳輸層          │  Kafka / Logstash pipeline / Cribl
 │   Transport       │  buffering、routing、sampling
 └────────┬──────────┘
          │
          ▼
 ┌───────────────────┐
 │   正規化層        │  ECS（Elastic Common Schema）/ OSSEM
 │   Normalization   │  field name 統一：process.name、src_ip、user.name
 └────────┬──────────┘
          │ normalized events
          ▼
 ┌───────────────────┐
 │   Enrichment      │  GeoIP、Threat Intel、Asset Context、User Context
 │   豐富化          │  src_ip → 國家/ASN；hash → VT score；user → 部門
 └────────┬──────────┘
          │ enriched events
          ▼
 ┌───────────────────┐
 │   偵測引擎        │  Sigma rules、correlation rules、ML anomaly
 │   Detection       │  Splunk SPL / ELK ESQL / Wazuh rules
 └────────┬──────────┘
          │ alerts
          ▼
 ┌───────────────────┐
 │   Alert Triage    │  優先分級、dedup、SOAR 自動化回應
 │   & Response      │  → 人工分析 / 自動 containment
 └───────────────────┘
```

每一層都是可以失敗的地方。我們逐層看。

## Log 收集層

收集層決定了你**能看到什麼**。前一章的 data source 概念在這裡實體化。

**Windows Endpoint 標準收集棧**：
```
Sysmon（Event ID 1/3/7/10/11/12/13/22/...）
    +
Windows Security Event Log（4624/4625/4688/4697/...）
    +
PowerShell ScriptBlock Logging（Event 4104）
    ↓
Winlogbeat（agent）→ Logstash / Elastic 收集節點
```

**Linux Endpoint**：
```
auditd（syscall 級別）+ syslog + auth.log
    或
eBPF-based agent（Falco / Tetragon）
    ↓
Fluentd / Filebeat
```

**網路層**：
```
Zeek（連線元資料）+ Suricata（IDS 告警）+ DNS logs
    ↓
收集節點
```

**雲端**：
```
CloudTrail（AWS API 呼叫）+ GuardDuty（AWS 託管偵測）
+ Azure Monitor / GCP Cloud Logging
    ↓
SIEM connector
```

**收集層常見問題**：
- **Agent 部署不完整**：50% 的 endpoint 有 Sysmon，50% 沒有，攻擊者選後者。
- **過濾過頭**：為了節省帶寬，在 agent 端把「吵」的事件 drop 掉，結果把偵測用的事件也 drop 了。
- **時鐘不同步**：endpoint 時間跑偏 5 分鐘，correlation rule 的時間窗口全部失效。

## 正規化層：ECS 與 OSSEM

你從 Windows 收到的欄位叫 `EventData.ProcessGuid`，從 Sysmon 叫 `Sysmon.Image`，從 Linux auditd 叫 `syscall.exe`。同一個概念，三種欄位名稱，correlation rule 沒辦法跨資料來源對比。

正規化解決這個問題：把所有資料來源的欄位轉換成統一的 schema。

**ECS（Elastic Common Schema）**：

| 概念 | Sysmon 原始欄位 | ECS 標準欄位 |
|---|---|---|
| 程序名稱 | `Image` | `process.executable` |
| 程序 PID | `ProcessId` | `process.pid` |
| 父程序 | `ParentImage` | `process.parent.executable` |
| 命令列 | `CommandLine` | `process.command_line` |
| 來源 IP | `SourceIp` | `source.ip` |
| 使用者 | `User` | `user.name` |
| 檔案路徑 | `TargetFilename` | `file.path` |

**OSSEM（Open Source Security Events Metadata）**：Cyb3rWard0g 開發，更全面地對映 ATT&CK data source 到各種 log source 的欄位，[https://github.com/OTRF/OSSEM](https://github.com/OTRF/OSSEM)。

Sigma 規則的欄位是寫 ECS 或 OSSEM 的標準名稱，`sigma convert` 工具在把 Sigma 轉成 SPL / KQL 時會做欄位對映，但對映表要你維護正確。

## Enrichment：讓資料說更多話

enrichment 在偵測命中之前發生，讓原本只有 IP 的 event 變成「IP 是俄羅斯 Tor exit node、VT score 50/95、目標是你的 Domain Controller」——這個 event 的告警優先級就從 Medium 直接跳成 Critical。

**三種常見 enrichment**：

**1. GeoIP / ASN enrichment**
```
src_ip: 185.220.101.34
→ enriched:
  src_ip.geo.country_code: "RU"
  src_ip.as.organization.name: "Tor Project"
```

**2. Threat Intelligence 對映**
```
dns.question.name: "update.windows-defender-secure.com"
→ enriched:
  threat.indicator.type: "domain"
  threat.indicator.confidence: "High"
  threat.indicator.source: "AlienVault OTX"
```

**3. Asset Context**
```
host.hostname: "FINANCE-WORKSTATION-07"
→ enriched:
  asset.tier: "Tier-1"
  asset.department: "Finance"
  asset.crown_jewel: true
```

Crown jewel asset 上的任何告警，優先級自動提升。

## 偵測引擎：三種偵測邏輯

在 SIEM 上跑的偵測邏輯分三類：

**1. 單事件規則（single-event rule）**

對單一 event 欄位比對，Sigma 規則直接對映到這裡：

```splunk
# Splunk SPL 範例（Sigma 轉換後）
index=sysmon EventCode=1
| where process_name="mimikatz.exe" OR command_line LIKE "%sekurlsa%"
| table _time, host, user, process_name, command_line
```

**2. Correlation rule（關聯規則）**

跨多個 event 的時間窗口聚合：

```splunk
# Splunk SPL：偵測 5 分鐘內同一個 source IP 對 10 個以上帳號登入失敗
index=windows EventCode=4625
| bucket _time span=5m
| stats count by _time, src_ip, TargetUserName
| stats count(TargetUserName) as unique_users, sum(count) as total_attempts by _time, src_ip
| where unique_users >= 10
| sort - total_attempts
```

這條規則抓的是 password spraying：攻擊者對很多帳號試同一個密碼，而不是對同一個帳號試很多密碼（後者被帳號鎖定擋住了）。

**3. ML / 異常偵測**

不寫規則，靠機器學習建 baseline 然後抓偏差：
- User behavior analytics（UBA）：這個 user 平常 9-6 在台北上班，今天凌晨 3 點從 VPN 登入 → 異常
- Rare process / command line：這台 server 第一次執行 `bitsadmin.exe` → 異常

ML 偵測假陽性高、難以解釋，通常做輔助告警而非主要告警。

## Alert Fatigue：SIEM 的最大敵人

一個部署不好的 SIEM 每天產生 10,000 條告警，SOC 分析師的實際處理能力是 50-100 條。剩下的 9,900 條告警沒有人看，這表示攻擊者可以在其中自由行動。

alert fatigue（告警疲勞）是 SIEM 殺死防守能力的核心機制。

**假陽性的根本原因**：

| 原因 | 舉例 | 對策 |
|---|---|---|
| 規則太寬鬆 | 偵測任何 `powershell.exe`，包含合法腳本 | 加 command line 條件、allowlist 已知路徑 |
| 沒有 asset context | 把 IT admin 的日常操作當攻擊 | 加 asset tier / user role 例外 |
| 規則未調校就上線 | 新規則直接推到 production | 先跑 detection gap 分析，review 歷史 7 天資料 |
| 重複告警沒有 dedup | 同一台機器連打 100 條相同規則 | alert dedup（相同規則 + 相同 host，10 分鐘合一） |

**告警分級框架**：

```
Critical  — 攻擊確認，需立刻回應（5 分鐘內）
High      — 高度可疑，需要在 4 小時內人工分析
Medium    — 可疑但有合法解釋，需在 24 小時內回顧
Low       — 資訊性，可批次分析
Informational — 不告警，只記錄在 hunting queue
```

把 90% 的假陽性規則調降到 Low / Informational，不讓它們出現在 SOC 主 dashboard，是務實的生存策略——但一定要確保規則本身還在跑、資料還在收。

## 三大 SIEM 平台比較

| 面向 | Splunk Enterprise | ELK Stack（Elastic SIEM） | Wazuh |
|---|---|---|---|
| 授權 | 商業，按日流量計費 | 商業（部分功能）+ 開源 | 完全開源 |
| 規則語言 | SPL（Search Processing Language） | ESQL / KQL / Lucene | Wazuh XML + Sigma |
| 擴展性 | 極高，百 TB/day 成熟案例 | 高，Elasticsearch 水平擴展 | 中，適合中小型 |
| Sigma 支援 | sigma convert -t splunk | sigma convert -t es-ql | 原生 Sigma 支援（實驗性） |
| 學習門檻 | 高（SPL 有自己的思維模型） | 中 | 低（適合入門） |
| 適合場景 | 大型 SOC、MSSPs | 中型 SOC、研究用 | 小型組織、MSSP 入門 |

三個平台的偵測邏輯概念相同，差別在查詢語言。Sigma 的價值就是讓你只寫一次，透過 `sigma convert` 轉成各平台的語法。

## 踩雷

1. **timestamp 對不齊**：Logstash 的 `@timestamp` 是收到 log 的時間，不是 event 真正發生的時間。應該在 parsing 階段把 `EventTime` 對映成 `@timestamp`。時鐘偏差 > 1 分鐘，correlation rule 的時間窗口就會出問題。

2. **field 名稱 case 敏感**：ECS 的 `process.executable` 跟 Sysmon 原始的 `Image` 是不同欄位。Sigma 轉換後的查詢要在你的 SIEM 上驗證欄位名稱，不能只靠 sigma convert 就上線。

3. **alert dedup 做過頭**：把相同 host + 相同 rule 在 1 小時內合成一條，結果連攻擊者打了 50 次的 brute force 也只顯示 1 條告警，讓分析師低估嚴重性。Dedup 邏輯要跟 alert 細節一起顯示（命中次數）。

4. **Enrichment pipeline 延遲**：GeoIP / TI lookup 增加延遲，如果 real-time alert 需要 enrichment 資料，要確保 enrichment 在 alert 觸發前完成，不然分析師看到的告警沒有 context。

5. **Log 收集器資源競爭**：endpoint agent（Winlogbeat）在高 CPU 負載時會掉 log，安全的配置是 agent 把 log 暫存到本地磁碟，非同步傳送，而不是直接 TCP forward。

## 進階延伸

- **Cribl Stream**：log routing 與 transformation 平台，在 log 進 SIEM 之前做 field enrichment、sampling、replay，減少 SIEM 費用。
- **OpenTelemetry**：雲端原生的可觀測性標準，開始滲入 security log 領域，追蹤它的動向。
- **SOAR（Security Orchestration, Automation and Response）**：接在 SIEM 下游，自動執行 alert triage 和初步回應，Ch 35 會深挖。
- **Detection Lab**（Chris Long）：在本機快速搭起完整 Windows AD + ELK SIEM 實驗環境，[https://github.com/clong/DetectionLab](https://github.com/clong/DetectionLab)。

## 本章重點整理

- Detection pipeline 六層：收集 → 傳輸 → 正規化 → 豐富化 → 偵測 → Triage
- 正規化（ECS/OSSEM）讓跨 source 的 correlation rule 成為可能
- Enrichment（GeoIP/TI/Asset）在 alert 觸發前增加 context，讓分級更準確
- 三種偵測邏輯：單事件規則、correlation rule（跨時間/host）、ML 異常
- Alert fatigue 是 SOC 最大威脅：dedup、分級、調校假陽性規則
- Splunk / ELK / Wazuh 偵測邏輯相同，差別在查詢語言；Sigma 做跨平台橋樑

## 自我檢核

- [ ] 能畫出 detection pipeline 的六層資料流
- [ ] 知道 ECS 的目的，能說出 3 個 Sysmon 欄位對應的 ECS 欄位
- [ ] 能解釋 correlation rule 與單事件規則的差異，各適合什麼場景
- [ ] 知道 alert fatigue 的 4 個根本原因與對策
- [ ] 能說出 Splunk / ELK / Wazuh 各自的適用場景
- [ ] 能解釋為什麼 timestamp 對齊是 correlation rule 的前提

## 延伸閱讀

1. **Elastic Common Schema（ECS）參考** [https://www.elastic.co/guide/en/ecs/current/](https://www.elastic.co/guide/en/ecs/current/)
   — 所有 ECS 欄位定義與範例；寫 Sigma 規則前先確認欄位名稱。

2. **OSSEM** [https://github.com/OTRF/OSSEM](https://github.com/OTRF/OSSEM)
   — ATT&CK data source 到 SIEM 欄位的完整對映表；規劃收集架構的必備參考。

3. **Detection Lab** [https://github.com/clong/DetectionLab](https://github.com/clong/DetectionLab)
   — 可快速部署的完整實驗 SOC（AD + Sysmon + ELK + Fleet），本章概念最好的動手驗證環境。

4. **"The SIEM Mistake"**（Anton Chuvakin, Gartner, 2022）
   — 分析 SIEM 部署失敗的常見模式；alert fatigue 那一節是業界最誠實的診斷之一。

5. **Sigma HQ Converters** [https://github.com/SigmaHQ/sigma/tree/master/tools](https://github.com/SigmaHQ/sigma/tree/master/tools)
   — 各 SIEM backend 的轉換範例與欄位對映 YAML；在自己環境裡驗證 Sigma 規則的起點。

---

你現在知道 detection pipeline 怎麼運作了。下一章把偵測規則本身當程式碼來管理——版控、測試、CI/CD 部署，讓規則品質可以被量測和持續改善。

→ [Ch 12 Detection-as-Code](./12-detection-as-code.md)
