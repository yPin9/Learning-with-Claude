# Ch 33 — 消費平台的架構：漏洞關聯與可達性

> **目標**：理解「消費平台」（consumption platform）在設計層面的核心挑戰——從元件清單到漏洞告警的資料管線有哪些精確語意問題、CPE ↔ PURL 對齊為何是系統性難題、規模化持續重掃如何做；並搞清楚「名義上受影響」和「真的受影響」之間的鴻溝，這個鴻溝就是 reachability 研究存在的理由。

## 為什麼需要這個？

Ch 17 介紹了 Dependency-Track 的操作：上傳 SBOM、設定 policy、收告警。你知道按哪個按鈕。但如果有人問：「這個平台的 component → vulnerability 關聯引擎是怎麼工作的？它會漏報嗎？會誤報嗎？精確度上限在哪裡？」——Ch 17 沒有回答這些。

這個問題在生產環境會以具體方式爆開：

- 掃描工具報 3,000 個漏洞，安全團隊不知道要從哪裡下手。
- 同一個元件，trivy 報漏洞，Dependency-Track 不報（或反過來）。
- 修完了被告警的版本，下個星期又有新的漏洞冒出來——不是同一個元件，是傳遞依賴。
- 法務要求回答「這個 CVE 有沒有影響我們的產品」，你說「有」，但實際上那段脆弱程式碼根本沒被你們呼叫。

這些問題都是消費平台的架構問題，不是使用問題。本章從設計者的角度拆解它。

## 先建立直覺

把消費平台想像成一個三層的問題：

```
輸入                         推理                          輸出
─────                        ────                          ────

SBOM                    ┌──────────────────┐
(元件清單)   ─────────▶ │  1. 識別：        │
                        │  這個元件是誰？   │
漏洞資料庫              │  PURL ↔ CPE 對齊  │──▶ component-vuln 關聯
(NVD/GHSA   ─────────▶ │                  │
/OSV/...)               │  2. 版本推理：    │
                        │  這個版本在受影   │──▶ 過濾：版本不在範圍
                        │  響範圍內嗎？     │    → 不關聯
                        │                  │
                        │  3. 可達性：      │
                        │  脆弱程式碼有被   │──▶ 過濾：不可達
                        │  我們的程式碼呼   │    → 降優先級 / VEX
                        │  叫到嗎？         │
                        └──────────────────┘
```

第一層是**命名問題**（識別）：把 SBOM 裡的元件和漏洞資料庫裡的記錄對上。
第二層是**版本推理問題**：確認你用的版本確實在受影響範圍。
第三層是**可達性問題**：確認脆弱程式碼路徑在你的應用中是否可被觸發。

目前大多數生產系統只做前兩層，第三層是研究前沿。但研究告訴我們，不做第三層的代價是：**大量的假陽性（false positive）告警**，讓安全團隊疲於奔命，最後選擇忽略告警——這正是「警報疲勞」的根源。

## 資料模型：component → vulnerability 關聯

消費平台的核心資料結構不複雜，但細節決定一切：

```
Component
├── purl          (pkg:maven/org.apache.logging.log4j:log4j-core@2.14.1)
├── cpe           (cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*)
├── version       (2.14.1)
├── ecosystem     (maven)
└── name          (log4j-core)

Vulnerability
├── id            (CVE-2021-44228)
├── severity      (CRITICAL / CVSS 10.0)
├── affected[]
│   ├── purl_pattern  (pkg:maven/org.apache.logging.log4j:log4j-core)
│   ├── version_ranges[]
│   │   ├── introduced  (>= 2.0-beta9)
│   │   └── fixed       (< 2.15.0)
│   └── cpe_pattern   (cpe:2.3:a:apache:log4j:*:*:*:*:*:*:*:*)
└── references[]

ComponentVulnerability (關聯表)
├── component_id
├── vulnerability_id
├── matched_via   (purl | cpe | both)
├── version_match (true | false | uncertain)
├── analysis      (null | not_affected | false_positive | ...)
└── suppressed    (bool)
```

「matched\_via」這欄很關鍵。CPE 比對和 PURL 比對是兩條並行的管線，匹配到的結果集不一樣，合理的設計是取**聯集**（任一管線匹配就記錄），然後讓分析師判斷哪些是真正有效的。

這裡有一個細微的設計決策：你是要「寬進嚴出」（先納入所有可能，後過濾），還是「嚴進」（只在高信心時才納入）？生產系統幾乎都走寬進嚴出——因為漏掉一個真實漏洞（false negative）的代價，遠高於多報一個假警報（false positive）的代價。

## CPE ↔ PURL 對齊的系統性難題

這是消費平台最麻煩的識別問題，值得用一整節拆解。

### 命名空間不相容

PURL 的設計邏輯是「生態系 + 套件名 + 版本」：

```
pkg:maven/org.apache.logging.log4j:log4j-core@2.14.1
     ↑         ↑                     ↑          ↑
   類型      group ID           artifact ID   版本
```

CPE 的設計邏輯是「廠商 + 產品 + 版本」：

```
cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*
          ↑       ↑       ↑
        廠商    產品名    版本
```

問題在於：**廠商和產品名是自由文字，由 NVD 分析師人工填寫**。`apache` 和 `apache_software_foundation` 都出現過。`log4j` 和 `log4j2` 都出現過。這些不一致讓自動比對變成猜謎遊戲。

### 粒度不對齊

更根本的問題：CPE 的產品粒度往往和套件粒度不對齊。

以 `log4j-core` 為例：Maven 生態中它是一個獨立的 artifact，有自己的版本。但 NVD 的 CPE 記錄的是 `log4j`（整個專案），版本是整個專案的版本號。

你的 SBOM 說 `log4j-core@2.14.1`，NVD 的 CPE 說 `log4j:2.14.1`。它們指的是同一個東西，但沒有機器可以自動確認這件事——因為 `log4j-core` 這個字串沒有出現在 CPE 裡。

這個問題在 C/C++ 生態更嚴重。C 沒有統一的套件管理器，CPE 是描述 C library 的主要方式，但 CPE 的廠商/產品欄位和任何標準的 PURL type 都不對應，導致大量漏洞在 C 生態中無法用 PURL 比對，只能靠 CPE——而 CPE 的準確率又偏低。

### 現有緩解策略

消費平台的工程師通常採用以下策略組合：

1. **人工維護映射表**：社群維護 CPE ↔ PURL 的對照表（OSV 和 GHSA 格式的 advisory 通常同時提供兩種），Dependency-Track 引入了 component intelligence 查詢 Sonatype OSS Index，後者有完整的 Maven GAV → CVE 映射。

2. **模糊比對 + 人工確認**：用字串相似度（Levenshtein distance）找候選，再人工確認。這在大型組織（成千上萬元件）不可擴展，但在中小型組織是可行的補充。

3. **生態原生 advisory 優先**：對 Maven 用 GHSA（精確到 GAV）、對 PyPI 用 PyPA advisory（精確到 PyPI 套件名）、對 Rust 用 RustSec。這些 advisory 的作者本身就是生態系的人，命名是對的。NVD 的 CPE 作為**補充**，不是主力。

4. **PURL Evidence Linking**：OSV 格式的 advisory 在 `affected[].package` 欄位同時提供 `ecosystem` + `name`，這些欄位可以直接和 PURL 的 type + name 對應。掃描工具優先用 OSV 格式的 advisory 可以大幅減少 CPE 比對的依賴。

現實的商業平台（Snyk、GitHub Advanced Security）會建立私有的映射資料庫，這也是它們能比 Dependency-Track 這類開源工具少漏報的原因之一。

## 版本範圍推理：semver 的精確語意

「這個版本在不在受影響範圍」聽起來很簡單，做起來有很多角落案例。

### semver range 的多種語法

不同生態系的版本範圍語法不一樣：

```
OSV 格式（統一語意）：
  introduced: "2.0-beta9"
  fixed: "2.15.0"
  → 語意：[2.0-beta9, 2.15.0)，即 >= 2.0-beta9 AND < 2.15.0

npm semver：
  ">=2.0.0-beta.9 <2.15.0"

Maven version range（不是 semver）：
  "[2.0-beta9,2.15.0)"

Python（PEP 440）：
  ">=2.0.0b9,<2.15.0"
```

消費平台需要對每個生態系實作各自的版本解析器，然後統一到一個內部表示，才能做比對。

### pre-release 版本的陷阱

`2.14.1` 明確在 `[2.0-beta9, 2.15.0)` 範圍內。但 `2.15.0-rc1` 呢？

在 semver 語意中，`2.15.0-rc1 < 2.15.0`，所以 `2.15.0-rc1` **在受影響範圍內**（小於 fixed 版本 `2.15.0`）。但很多工具在 pre-release 的處理上是錯的——它們可能把 `rc1` 當作「已修復版本」的一個變體，不報告漏洞。

這是真實的 false negative 來源之一。

### epoch 和分支版本

Debian/Ubuntu 的版本格式有 epoch（`1:2.14.1-3`），這和 upstream 的 `2.14.1` 是同一個軟體但版本字串完全不同，必須有 epoch 感知的比對邏輯。

Linux distro 的 backported patches 更複雜：distro 可能在 `2.14.1` 版本上自己打了修復 CVE-2021-44228 的 patch，但版本號還是 `2.14.1`。這意味著光看版本號會判斷「受影響」，實際上 distro 版本是安全的。消費平台必須引入 distro-specific 的 vulnerability 資料（如 Red Hat CSAF、Ubuntu USN）來覆蓋這種情況。

## 規模化持續重掃的工程

Ch 17 的操作層描述了「上傳 SBOM 後 Dependency-Track 持續監控」。但在設計層，「持續監控」的代價是巨大的：

```
規模估算：
  組織有 500 個 project
  每個 project 平均 200 個元件
  = 100,000 個需要持續監控的 (project, component) 對

  漏洞資料庫每天新增約 50-200 個 CVE（NVD 統計）
  每個新 CVE 可能影響 N 個元件

  最壞情況：每天需要重掃 100,000 × K 次比對
```

暴力重掃（每天對所有元件跑完整比對）在小規模可行，在企業規模不行。

### 差異更新策略

高效的消費平台用差異更新，不做全量重掃：

```
漏洞資料庫更新時：
  1. 找出新增的 CVE / 修改的受影響版本範圍
  2. 從 affected package 資訊中提取「可能受影響的 PURL 前綴」
     （例如 pkg:maven/org.apache.logging.log4j:log4j-core）
  3. 在資料庫中查詢持有該 PURL 前綴的 component
  4. 只對這些 component 重跑版本比對
  5. 新增 / 更新 ComponentVulnerability 記錄
  6. 對有變化的 project 發告警

時間複雜度：
  暴力法：O(|components| × |vulns|) 每天
  差異法：O(|affected_packages| × |components_with_prefix|) 每次更新
  實務中差異法快 2-3 個數量級
```

Dependency-Track 的 portfolio 重新分析（Portfolio Re-analysis）就是這個邏輯的生產實作：它用 PURL 前綴建索引，漏洞資料庫更新時只重掃相關元件。

### 告警風暴控制

另一個工程問題：當 NVD 批量更新（如危機後的積壓清除）時，可能在幾分鐘內新增幾百個 CVE，對幾千個 project 觸發告警。這個「告警風暴」如果直接送到 Slack/PagerDuty，會讓安全團隊在凌晨被叫醒 300 次。

緩解策略：

1. **告警聚合**（alert aggregation）：同一個 project 在一個時間窗口內的多個新漏洞，合成一份摘要通知。
2. **嚴重度過濾**：只有 CVSS >= 8.0 才立刻通知，其餘進 weekly digest。
3. **告警排程**（notification scheduling）：批次更新的告警延遲到正常工作時間發送。

## 核心轉折：從「名義上受影響」到「真的受影響」

到目前為止描述的管線——PURL 比對 + 版本範圍推理——輸出的是「名義上受影響」的判斷：你用的版本號落在受影響範圍內。

但「名義上受影響」和「真的受影響」之間有一個研究者已經用數據量化的鴻溝。

這個鴻溝來自三個不同層次的問題：

**第一層：依賴是否真的被部署？**

你的 `package.json` 裡有 200 個依賴，其中有些是 `devDependencies`（只在開發時用），不打包進 production build。如果漏洞在 devDependency 裡，production 環境根本不受影響。同樣的，如果你做了 tree-shaking 或靜態連結後只取了一個函式庫的一部分，受影響的那段程式碼可能根本不在最終 artifact 裡。

**第二層：脆弱程式碼是否在程式碼路徑上可達？**

一個函式庫可能有一個脆弱函式 `parseXML()`，但你的程式碼只用了它的 `parseJSON()`，從來不呼叫 `parseXML()`。版本號在受影響範圍，但脆弱程式碼從來不執行。

**第三層：攻擊者能否提供觸發脆弱程式碼的輸入？**

即使脆弱函式在程式碼路徑上，攻擊者能否從外部提供觸發它的輸入，還取決於應用層的邏輯和邊界條件。這一層的分析是 taint analysis（污點分析），超出消費平台的一般設計範疇。

Ch 34 深入處理第二層（reachability analysis）和第三層的自動化。本章先把第一層量化清楚。

## 底層機制：差異更新的資料流

```
外部資料源                  消費平台內部                   輸出
─────────────────────────────────────────────────────────────

NVD feed ─────────┐
GHSA feed ─────── ▶ [鏡像/快取層]
OSV feed ──────── ▶ (vulnerability_store)
                  │
                  │ 變化偵測
                  ▼
             ┌─────────────────────────────┐
             │  Diff Engine                │
             │                             │
             │  1. 解析 CVE / advisory      │
             │  2. 提取 affected PURL 前綴  │
             │  3. 提取版本範圍 (parsed)    │
             │                             │
             └──────────────┬──────────────┘
                            │
                    [component index]
                            │ 前綴查詢
                            ▼
             ┌─────────────────────────────┐
             │  Version Range Evaluator    │
             │                             │
             │  input:                     │
             │    component.version        │
             │    vuln.affected_ranges[]   │
             │                             │
             │  output:                    │
             │    AFFECTED | NOT_AFFECTED  │
             │    | UNCERTAIN              │
             └──────────────┬──────────────┘
                            │
             ┌──────────────▼──────────────┐
             │  ComponentVulnerability     │
             │  (upsert)                   │
             │                             │
             │  status: ACTIVE | RESOLVED  │
             │  analysis: null | suppressed│
             │  vex_status: (Ch 34)        │
             └──────────────┬──────────────┘
                            │
                     告警判斷
                     (severity filter
                      + schedule
                      + aggregation)
                            │
                            ▼
                    通知輸出（Slack/email/
                    JIRA ticket/webhook）
```

`UNCERTAIN` 這個狀態很重要。當版本範圍解析失敗（例如 epoch 問題或 distro 分支版本），比對引擎應該輸出 `UNCERTAIN` 而不是 `NOT_AFFECTED`，避免靜默的 false negative。`UNCERTAIN` 的告警可以路由到人工審查隊列，而不是觸發緊急通知。

## 研究實證：假陽性有多嚴重？

### Pashchenko et al. ESEM 2018

Pashchenko、Plate、Ponta、Sabetta 和 Massacci 在 ESEM 2018 的研究，系統性地量化了「名義上受影響」和「真的危險」之間的距離。

他們分析了 SAP 使用的 200 個最熱門 Java 開源軟體，共 10,905 個不同的 GAV（Group-Artifact-Version）。研究方法結合了 patch 的程式碼分析、build metadata 和 test/update 歷史，區分：

- **deployed**：依賴確實被打包進去並在 runtime 可用
- **non-deployed**：依賴在 POM 裡有記錄，但不在最終 artifact 或 runtime classpath 上
- **halted**：依賴因為升級、替換或棄用，實際上已停止維護

關鍵發現：**約 20% 受影響的依賴實際上未被部署**，對使用者不構成實際危險。這個數字意味著在不做可達性分析的情況下，五分之一的「必修漏洞」根本不需要修——至少從部署角度看是安全的。

這個研究建立了一個嚴格的方法論框架：在宣稱一個元件「有漏洞」之前，先確認它確實出現在 deployed artifact 裡。這個洞察現在被 VEX（Vulnerability Exploitability eXchange）的 `not_affected` 語意所吸收：其中的原因碼 `component_not_present` 和 `code_not_reachable` 正是對應這兩層過濾。

### Ponta/Eclipse Steady ICSME 2018

Ponta、Plate 和 Sabetta 在 ICSME 2018 發表了 Eclipse Steady（前身是 SAP 內部的 Vulas），這篇論文獲得了 IEEE TCSE Distinguished Paper Award。

Eclipse Steady 的核心方法是**以 method signature 偵測脆弱程式碼**：它不只看版本號，而是直接比對 source code 和 bytecode 中是否存在已知的脆弱方法（vulnerable method）或已修復版本的方法（fixed method）。

具體做法：
1. 分析 CVE 對應的 commit，提取「修改了哪些 method」（脆弱 method 集合 V，修復 method 集合 F）。
2. 對待分析的 JAR/class，比對它的 method signature 和方法體是否和 V 或 F 相似。
3. 用靜態分析 + 測試追蹤確認脆弱方法是否在 call graph 中可達（usage/reachability）。

這個方法的意義在於：它可以處理「版本號不準確」的情況。比如 distro 的 backport patch、vendor 的私下修復，即使版本號還是舊的，只要方法體已改，就判定為已修復。反過來，即使升級了版本，如果使用了自定義 shade 或還在用舊版 API，也能偵測到仍在使用脆弱程式碼。

Eclipse Steady 的設計揭示了消費平台的方向：真正的解法不是更好的版本匹配，而是**方法層級的 reachability 分析**。

### EU FASTEN 專案的生態級數據

EU FASTEN 專案（Horizon 2020，ICT-2018-2020）嘗試在生態系規模上做細粒度（函式層）的 call graph 分析，涵蓋 Java（Maven）、Python（PyPI）和 C 三個生態。

他們的核心發現是一個讓安全從業者難以忽視的數字：

在 Maven 生態中：
- **有脆弱傳遞依賴的套件佔約 1/3**——這個數字和大多數消費平台告警的感受吻合，傳遞依賴的漏洞幾乎是必然的。
- **但其中只有 1% 有 reachable call 到脆弱方法**——意味著在 1/3 中有問題的套件裡，99% 的漏洞告警是假陽性（至少在 call graph 可達性這個維度上）。

這個 1% 的數字說明了什麼？它說明**傳遞依賴的漏洞告警，在沒有 reachability 分析的情況下，假陽性率極高**。一個有 100 個傳遞依賴漏洞的告警，真正需要處理的可能只有 1 個。

FASTEN 的方法論是生態級靜態 call graph 建構：對每個套件版本預計算 API 暴露（exported function）和內部呼叫圖，再在依賴解析時組合成完整的跨套件 call graph。這個方法的限制是靜態分析的天花板——動態分派、反射、JNI 都無法靜態追蹤。但對 Java Maven 生態中的純 Java 套件，準確率已足夠說明問題。

## 對比與取捨

| 比對策略 | 精確度 | 召回率 | 工程成本 | 假陽性率 | 假陰性率 |
|----------|--------|--------|----------|----------|----------|
| CPE 版本比對 | 低（命名不一致） | 中 | 低 | 高 | 中 |
| PURL + OSV 版本範圍 | 高（對有 OSV advisory 的生態） | 中（無 OSV 的漏洞不覆蓋） | 低-中 | 中 | 低-中 |
| PURL + distro advisory | 高（對目標 distro） | 高 | 中（需整合多個 advisory 來源） | 低 | 低 |
| Method signature 比對（Eclipse Steady 方式） | 非常高 | 高 | 高（需要分析所有版本的 bytecode） | 非常低 | 低 |
| 靜態 call graph + reachability（FASTEN 方式） | 高（靜態） | 高（靜態可達的部分） | 非常高（生態級基礎設施） | 非常低 | 中（動態分派不可追蹤） |

**直接依賴 vs 傳遞依賴的不同策略**：

對**直接依賴**，版本比對的準確率已足夠高，假陽性率可接受，修復成本也低（你控制 version bump）。

對**傳遞依賴**，版本比對的假陽性率很高（FASTEN 數據：約 99%），但修復成本也高（需要上游升級或 override 依賴樹）。理想的消費平台應該對傳遞依賴的漏洞告警做額外的 reachability 過濾或降低優先級，再交給人工判斷。

## 踩雷集錦

**1. 版本「0」和「0.0.0」的語意**

許多套件的初始版本或 placeholder 版本是 `0` 或 `0.0.0`。有些 OSV advisory 的 `introduced` 欄位會填 `0`，表示「從一開始就有這個漏洞」。消費平台的版本比對引擎如果把 `0` 當成字串比較而不是語意版本比較，會得到奇怪的結果——`0.9.8` 可能被判斷為「小於 0」。

修法：版本解析層必須識別 `0` 這個特殊值，語意為「earliest known version」，而不是版本 `0.0.0`。

**2. non-canonical PURL 導致比對失敗**

PURL 規格要求 group ID 和 artifact ID 用 `/` 分隔，且 namespace 部分（Maven 的 groupId）**不區分大小寫**但**規格化後要小寫**。如果你的 SBOM 生成器輸出 `pkg:Maven/Apache/Log4j@2.14.1`，而漏洞資料庫裡是 `pkg:maven/org.apache.logging.log4j:log4j-core@*`，這兩個不只是大小寫不同，namespace 完全不一樣——比對根本不會命中。

這種問題在來源不同的 SBOM 合併時尤其常見：A 系統輸出的 PURL 格式和 B 系統的 PURL 格式微妙不同，導致跨系統的元件去重和漏洞關聯都出問題。

修法：在 ingestion 層對所有 PURL 做規範化（normalization）：lowercase namespace、標準分隔符、移除多餘的 qualifiers。

**3. 多 pom.xml 的 Maven 多模組專案**

Maven 的多模組專案（multi-module project）有一個 parent POM 和多個子模組 POM，每個子模組是獨立的 artifact。如果 SBOM 生成器只讀 parent POM 而沒有展開子模組，會漏掉子模組的依賴。如果 SBOM 生成器正確展開了子模組，但沒有正確處理 `<dependencyManagement>` 繼承（版本在 parent 定義、子模組不寫版本號），版本會顯示為空。

消費平台收到版本為空的元件，版本比對只能輸出 `UNCERTAIN`——沒有版本就無法判斷是否在受影響範圍。這個 `UNCERTAIN` 需要被記錄並路由到人工審查，而不是靜默忽略。

**4. CPE 的「部分版本」比對陷阱**

NVD 的 CPE 版本欄位有時填的是「*」（任意版本）或「-」（不適用），配合 `versionStartIncluding` / `versionEndExcluding` 額外欄位表示範圍。如果你的比對邏輯只看 CPE 裡的版本欄位，忽略了 `configurations` 裡的額外版本範圍欄位，你會把「*」解讀成「所有版本都受影響」，導致嚴重的假陽性爆炸。

Dependency-Track 的 CPE 比對引擎就曾有這個 bug，導致某段時期對大量 artifact 報出所有 CPE 漏洞。

**5. 告警去重失效後的重複風暴**

消費平台在重啟或資料庫遷移後，有時會因為告警去重 key 不一致，對已發送的告警重新發送一遍。如果沒有冪等的告警傳遞機制（例如 webhook 接收端缺少去重邏輯），這個場景會在 production 觸發幾千個重複 JIRA ticket 或 Slack 通知。

修法：webhook payload 裡帶上一個 deterministic 的告警 ID（例如 `SHA256(project_id + vuln_id + timestamp_of_first_occurrence)`），接收端做 idempotent ingestion。

## 進階：再往深一層

### 連續版本推理的形式化

消費平台的版本範圍比對本質上是一個**區間查詢問題**。如果把每個受影響的版本範圍看作數線上的一個區間，把你的元件版本看作數線上的一個點，「這個版本是否受影響」就是「這個點是否落在某個區間裡」。

對每個生態系，「版本數線」的定義不同（semver、PEP 440、Maven 版本排序各有差異）。形式化的處理方式是為每個生態系定義一個**全序關係（total order）**，然後把版本范範圍查詢編譯成這個全序上的區間比較。

難點在於邊界：`fixed: "2.15.0"` 表示 `< 2.15.0`（不包含），這個語意在 OSV 是明確的，但在 NVD 的 CPE configurations 中是模糊的（`versionEndExcluding` vs `versionEndIncluding` 欄位）。歷史 NVD 資料中這兩個欄位的填法有不一致，必須有 case-by-case 的修正。

### 動態依賴解析 vs 靜態 lockfile

消費平台通常依賴 SBOM 中已解析好的版本，不重做依賴解析。但有些語言（Python 的 `requirements.txt` 沒有 `==` pin、Go 的 module proxy 環境差異）的依賴版本在不同 build 環境可能解析到不同版本。

這個問題對消費平台的影響：SBOM 中的版本記錄的是「這次 build 解析到的版本」，不是「下次 build 一定會得到的版本」。如果你的 CI 不做 lockfile 固定，今天的 SBOM 和明天的 SBOM 可能不同，消費平台的「持續監控」實際上監控的是一個會漂移的快照。

這個問題的根治方案在 SBOM 生成側（要求 lockfile），不在消費平台側，但消費平台可以用 SBOM 的 timestamp 和 hash 去追蹤「相同元件、不同 build 快照」之間的版本漂移。

### Purl-Based Call Graph Query

FASTEN 的研究方向提示了一個有意思的設計：如果你有每個套件版本的 API call graph（以 PURL 作 key），消費平台在關聯漏洞時可以額外查詢：「從我的 application 代碼，是否有 call path 到達 CVE 的脆弱 method？」若天真地枚舉所有 call path，路徑數是 O(K^D)（K 是每層的 API 數量、D 是依賴深度）——對深度 5、每層 100 個 API 的應用，就是 100^5 ≈ 10^10 量級的路徑，指數爆炸。實務上必須把它轉成「可達性」的集合查詢（從 entry point 做一次 BFS/DFS 標記 reachable 集合，複雜度降到與圖大小 O(V+E) 線性），而不是枚舉路徑。這也是為什麼下一章把 reachability 當成一個獨立的程式分析問題處理。

這就是為什麼 Ch 34 單獨處理 reachability：它不是一個版本匹配問題，而是一個完整的程式分析問題。

## 動手練習

**研究章風格：讀論文 + 動手思考**

1. 閱讀 Pashchenko et al. ESEM 2018 的 Section III（Research Design）和 Section IV（Results）。
   - 他們如何定義「deployed」vs「non-deployed」依賴？這個定義對 Maven 特有嗎？移植到 npm 生態需要改什麼？
   - 他們的 20% non-deployed 數字，在哪些類型的專案中可能更高或更低？（提示：考慮 fat jar vs 微服務 vs monorepo）

2. 閱讀 Ponta et al. ICSME 2018 的 Section II（Background：Eclipse Steady）和 Section V（Evaluation）。
   - Eclipse Steady 如何處理「CVE 對應的修復 commit 橫跨多個方法」的情況？
   - bytecode 比對比 source code 比對有什麼優勢？有什麼盲點？

3. 設計題：假設你要在 Dependency-Track 之上加一層「傳遞依賴漏洞過濾」，只對有 reachable call 的漏洞發告警。根據 FASTEN 的設計（生態級靜態 call graph），描述你需要的三個基礎設施元件，以及每個元件最難解決的一個工程問題。（預期答案長度：三個段落，每段 3-5 句）

## 本章重點整理

- 消費平台的 component → vulnerability 關聯有三層：命名對齊（PURL ↔ CPE）、版本範圍推理（semver 精確語意）、可達性分析（脆弱程式碼是否可達）。
- CPE ↔ PURL 對齊是系統性難題，根源在於 CPE 的命名空間和套件生態的命名空間設計時沒有對應。生態原生 advisory（GHSA、OSV 格式）是更可靠的對齊基礎。
- 版本範圍推理的陷阱集中在：pre-release 版本的語意、epoch 和 distro backport、以及 NVD 的 `versionEndExcluding` vs `versionEndIncluding` 歷史不一致。
- 規模化持續重掃需要差異更新（Diff Engine + PURL 前綴索引），而不是全量重掃；告警風暴控制需要聚合 + 排程 + 嚴重度過濾。
- Pashchenko et al. ESEM 2018 量化：約 20% 受影響依賴未被部署，不構成實際危險。
- Eclipse Steady（Ponta et al. ICSME 2018）的方法論：method signature 比對 + 靜態 call graph，可以偵測 backport 修復和版本號不準確的情況。
- FASTEN 專案的生態級數據：Maven 生態中有脆弱傳遞依賴的套件佔 1/3，但只有 1% 有 reachable call 到脆弱方法——這個數字說明傳遞依賴漏洞的假陽性率極高，reachability 過濾是必要的。
- 消費平台的設計方向：對直接依賴用精確版本比對，對傳遞依賴用 reachability 過濾降低假陽性；長期方向是方法層級的靜態分析。

## 自我檢核

讀完本章，你應該能回答：

1. 為什麼 CPE 和 PURL 在 Maven 生態中的命名不能自動對齊？各自的命名 space 邏輯是什麼？
2. 消費平台的 Diff Engine 是什麼？它比暴力全量重掃快在哪裡？時間複雜度差幾個量級？
3. Pashchenko et al. 的「約 20% non-deployed」這個數字是怎麼測量出來的？研究對象是什麼？
4. Eclipse Steady 的 method signature 比對，解決了純版本比對的哪個根本缺陷？
5. FASTEN 的「1% reachable」數字，是在什麼條件下（哪個生態、什麼分析方法）得到的？它的限制是什麼（靜態分析不能追蹤什麼）？
6. `UNCERTAIN` 在版本範圍推理中的語意是什麼？它為什麼比靜默輸出 `NOT_AFFECTED` 更安全？

## 精讀論文

**1. Pashchenko I., Plate H., Ponta S. E., Sabetta A., Massacci F.**
"Vulnerable Open Source Dependencies: Counting Those That Matter"
ACM/IEEE ESEM 2018

核心方法：結合 patch 的程式碼分析、build metadata 和 dependency 部署狀態，區分 deployed / non-deployed / halted 依賴；分析 SAP 用的 200 個最熱門 Java OSS（10,905 distinct GAVs）。

關鍵數字：約 20% 受影響依賴未被部署，不構成實際危險；研究建立了「受影響」到「危險」之間多層次的精確化框架。

建議讀哪節：Section II（Approach，看方法論設計）、Section IV（Results，看 20% 從哪裡來）。

和本章的關聯：這個研究是「版本比對 → 真實危險」鴻溝的第一份系統性量化，是理解假陽性問題來源的必讀基礎。

---

**2. Ponta S. E., Plate H., Sabetta A.**
"Beyond Metadata: Code-Centric and Usage-Based Analysis of Known Vulnerabilities in Open-Source Software"
IEEE ICSME 2018（IEEE TCSE Distinguished Paper Award）

核心方法：Eclipse Steady 以 method signature 偵測脆弱程式碼，比對 source code 與 bytecode 的 vulnerable 和 fixed 版本；用靜態分析 + 測試判斷程式碼是否實際被用到（usage / reachability）。

關鍵數字：方法層級比對可偵測版本號不準確的情況（backport / vendor fix），假陰性率低於純版本比對。

建議讀哪節：Section II（Eclipse Steady 架構）、Section III（Methodology，看 method signature 如何提取）、Section V（Evaluation）。

和本章的關聯：提供了消費平台「超越版本比對」的具體工程路徑；也是 VEX `code_not_reachable` 語意的技術基礎。

---

**3. EU FASTEN Project（H2020-ICT-2018-2020，grant 825328，TU Delft）**
"FASTEN: Fine-Grained Analysis of Software Ecosystems as Networks"
相關出版物見 fasten-project.eu；Maven 生態的 reachability 數據見 Mir 等 "On the Effect of Transitivity and Granularity on Vulnerability Propagation in the Maven Ecosystem"（arXiv 2301.07972）

核心方法：生態系規模的細粒度（函式層）call graph 分析，預計算每個套件版本的 API 暴露和內部呼叫圖；涵蓋 Java（Maven）、Python（PyPI）和 C 生態。

關鍵數字：Maven 生態中有脆弱傳遞依賴的套件佔 1/3，但只有 1% 有 reachable call 到脆弱方法。

建議讀哪節：專案白皮書中的 call graph 建構章節，以及 Maven 生態分析的統計部分。

和本章的關聯：提供了「傳遞依賴假陽性率極高」的生態級量化數據，直接論證了 reachability 分析為何必要。

---

**4. Woo S., Choi E., Lee H., Oh H.**
"V1SCAN: Discovering 1-day Vulnerabilities in Reused C/C++ Open-source Software Components Using Code Classification Techniques"
USENIX Security 2023

核心方法：在 C/C++ 生態中，以程式碼分類技術找出重用的 OSS 元件中的 1-day 漏洞，不依賴版本號或套件管理器記錄。

關鍵貢獻：對應了沒有套件管理器的 C/C++ 生態的識別問題，是 Ch 34 reachability 和 vendor code 偵測方向的前置工作。

建議讀哪節：Section III（Approach）理解程式碼分類的機制，Section V（Evaluation）看在真實 firmware 中的命中率。

和本章的關聯：補充了「PURL/CPE 雙雙失效」的場景（C/C++ 無套件管理器生態），說明消費平台對 C/C++ 的識別需要完全不同的路徑。

## 延伸閱讀

- Dependency-Track 原始碼：`src/main/java/org/dependencytrack/tasks/NistMirrorTask.java` 和 `VulnerabilityAnalysisTask.java`——看生產系統如何實作差異更新和 CPE 比對邏輯
- OSV 格式規格：`https://ossf.github.io/osv-schema/`——理解 `affected[].ranges` 的語意設計，看版本範圍如何跨生態系統一表達
- Google OSV-Scanner 原始碼：`pkg/osvscanner/` 目錄——看 PURL → OSV query 的實際邏輯，以及如何處理 lockfile vs SBOM 作為輸入
- NIST NVD API v2.0 文件：`https://nvd.nist.gov/developers/vulnerabilities`——理解 `configurations` 欄位的結構，看 `versionStartIncluding` / `versionEndExcluding` 的準確語意
- Plate H. 的部落格（SAP Security Research）：有多篇關於 Eclipse Steady 設計決策和 reachability 方法論的非正式描述，適合在讀 ICSME 論文前熱身

---

本章討論的消費平台核心問題——命名對齊、版本推理、假陽性——在三個研究結果的映照下，清楚地指向同一個結論：告警品質的下一個突破在 reachability，而不在命名或版本比對的邊際改進。Ch 34 接著處理 reachability 分析的具體方法，以及如何把它的輸出自動化成 VEX 聲明。

→ [Ch 34 — 可達性分析與 VEX 自動化](./34-reachability-vex-automation.md)
