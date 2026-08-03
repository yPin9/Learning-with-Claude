# Ch 39 — SARIF 與生態整合

> **目標**：搞懂 SARIF 這個「靜態分析結果的通用格式」——它的結構、四工具怎麼輸出它、怎麼用 jq/python 合併去重多工具的 SARIF、以及它接進的生態（GitHub code scanning、SARIF viewer、缺陷管理）。你會真跑 Semgrep 與 CodeQL 的 SARIF 輸出，把兩者合併找出跨工具佐證的命中，並從 SARIF 生成人讀報告。
> **環境**：WSL、semgrep 1.172.0、codeql 2.26.2、jq、python3。靶在 `~/audit-lab/ch39/`。GitHub 上傳部分標「未實測，需 repo」附步驟。

前幾章我們用了 Semgrep 的命中、CodeQL 的結果、動態驗證的輸出——每個工具吐的格式都不一樣：Semgrep 有自己的 JSON、CodeQL 有 BQRS、weggli/joern 各有輸出。如果每整合一個工具就要寫一個 parser，四個工具就是四份膠水碼，加一個工具全部重寫。這不可持續。

**SARIF（Static Analysis Results Interchange Format）** 就是為解決這件事而生的：一個由 OASIS 標準化的 JSON 格式，讓**所有**靜態分析工具用同一種結構描述結果。四大工具都能輸出 SARIF，於是你只要學會讀一種格式，就能匯流、合併、去重、上傳、生成報告——工具在下游變成可插拔的。這一章講的就是「怎麼把 SARIF 當成整個審計流程的公共匯流排」。

---

## 為什麼要有一個通用格式

想像沒有 SARIF 的世界：

```
Semgrep JSON ─┐
CodeQL BQRS  ─┼─→ 每個都要專屬 parser ─→ GitHub / DefectDojo / 報告
Joern 輸出   ─┤     （N 工具 × M 消費端 = N×M 份膠水碼）
weggli 輸出  ─┘
```

有了 SARIF：

```
Semgrep ─┐
CodeQL  ─┼─→ 全部輸出 SARIF ─→ 一個 SARIF 消費端 ─→ GitHub / DefectDojo / 報告
Joern   ─┤     （N 工具 + M 消費端 = N+M，不是 N×M）
weggli  ─┘
```

從 N×M 降到 N+M，這就是通用格式的價值。每個工具只需輸出一次 SARIF，每個消費端只需讀一次 SARIF。GitHub code scanning、VS Code viewer、DefectDojo、你自己的合併腳本，全部吃同一種輸入。

---

## 底層機制：SARIF 的結構

SARIF 是巢狀 JSON，你要熟悉的核心層級由外到內是 **runs → results → locations**，加上一個平行的 **rules** 表和可選的 **codeFlows**：

```
sarif
└─ runs[]                     一次「工具執行」= 一個 run（合併多工具時多個 run）
   ├─ tool.driver
   │  ├─ name                 工具名（"Semgrep OSS" / "CodeQL"）
   │  └─ rules[]              這次跑的規則定義（含 defaultConfiguration.level 嚴重度）
   └─ results[]               命中清單
      ├─ ruleId               觸發哪條規則
      ├─ level                嚴重度（注意：semgrep 放在 rule，不放這，見下）
      ├─ message.text         人讀訊息
      ├─ locations[]
      │  └─ physicalLocation
      │     ├─ artifactLocation.uri   檔案路徑（相對！見踩雷）
      │     └─ region.startLine       行號
      ├─ codeFlows[]          taint 路徑（source→...→sink 的每一步，若有）
      └─ partialFingerprints  穩定指紋（去重/baseline 用，接 Ch 36）
```

幾個實務上會踩到的細節：

- **嚴重度放哪不一致**：CodeQL 常把 level 放在 result；Semgrep 把它放在 `tool.driver.rules[].defaultConfiguration.level`，result 的 `level` 是 null。合併腳本要**同時查兩處**（先看 result.level，沒有就回退查 rules 表）——這是 Ch 36 的 `triage.py` 特意處理的坑。
- **路徑是相對的**：`artifactLocation.uri` 是相對於某個 base（`uriBaseId`，如 `%SRCROOT%`）。若合併不同工具、不同 base 的 SARIF，路徑對不上就會「明明同一行卻被當兩個不同位置」（見踩雷）。
- **partialFingerprints 是去重的黃金 key**：它基於程式碼上下文算穩定 hash（我們真跑的 CodeQL SARIF 裡就有 `primaryLocationLineHash`），跨掃描相同，是 Ch 36 去重與 Ch 38 baseline 的實作基礎。

看一段**真跑**的 CodeQL SARIF result（`~/audit-lab/ch39/codeql.sarif`）：

```json
{
  "ruleId": "audit/memcpy-nonconst-len",
  "message": { "text": "memcpy with non-constant length" },
  "locations": [{
    "physicalLocation": {
      "artifactLocation": { "uri": "vuln.c", "uriBaseId": "%SRCROOT%" },
      "region": { "startLine": 12, "startColumn": 5, "endColumn": 11 }
    }
  }],
  "partialFingerprints": {
    "primaryLocationLineHash": "d2dc1614d24fcc90:1",
    "primaryLocationStartColumnFingerprint": "0"
  }
}
```

`ruleId` + `location`（`vuln.c:12`）+ `partialFingerprints` 就是你在 Ch 36/38 反覆用到的三個欄位。

---

## 範例一：四工具輸出 SARIF（真跑）

四工具都能輸出 SARIF，flag 各不同：

```bash
# Semgrep
semgrep --config rules.yaml . --sarif -o out.sarif -q

# CodeQL（分析階段指定格式）
codeql database analyze db query.ql --format=sarif-latest --output=out.sarif

# Joern：scan 有 --store，或用 SARIF 匯出腳本（見延伸閱讀）
# weggli：無原生 SARIF，需自寫轉換（它輸出純文字位置，可腳本包成 SARIF）
```

我們真跑 Semgrep（規則 `unbounded-memcpy`）和 CodeQL（自寫 query `audit/memcpy-nonconst-len`）兩者對同一個 `vuln.c`：

```bash
cd ~/audit-lab/ch39
semgrep --config a.yaml vuln.c --sarif -o toolA.sarif -q
codeql database analyze db qlpack/memcpy.ql --format=sarif-latest --output=codeql.sarif --rerun

jq -r '.runs[].results[] | "\(.ruleId) L\(.locations[0].physicalLocation.region.startLine)"' toolA.sarif
# unbounded-memcpy L12
jq -r '.runs[].results[] | "\(.ruleId) L\(.locations[0].physicalLocation.region.startLine)"' codeql.sarif
# audit/memcpy-nonconst-len L12
```

兩個不同工具、不同引擎、不同規則名，**都指向 `vuln.c:12` 的同一個 memcpy**。這正是合併與去重的用武之地：兩工具在同一位置的命中，是強烈的「這很可能是真 bug」訊號。

---

## 範例二：合併多工具 SARIF + 找跨工具佐證（真跑）

合併不是把兩個 JSON 拼在一起就好——要**去重**（同位置同 rule 只留一筆）並**標出跨工具佐證**（同位置被多工具命中的，優先看）。腳本 `merge_sarif.py` 的邏輯：對每個 `(uri, line)` 記錄「哪些工具/規則命中它」，用這個集合大小判斷佐證強度。我們跑三份 SARIF（Semgrep 兩條規則 + CodeQL 一條）：

```bash
python3 merge_sarif.py toolA.sarif toolB.sarif codeql.sarif
```

真實輸出：

```
== merged, de-duplicated findings ==
vuln.c:8   [Semgrep OSS] tainted-read
vuln.c:11  [Semgrep OSS] tainted-read
vuln.c:12  [CodeQL] audit/memcpy-nonconst-len      <== 3 tools agree
vuln.c:12  [Semgrep OSS] raw-memcpy-audit          <== 3 tools agree
vuln.c:12  [Semgrep OSS] unbounded-memcpy          <== 3 tools agree

== corroborated locations (rank these first) ==
vuln.c:12  <- CodeQL/audit/memcpy-nonconst-len, Semgrep OSS/raw-memcpy-audit, Semgrep OSS/unbounded-memcpy
```

**解讀**：`vuln.c:12` 被三條規則（跨 Semgrep 與 CodeQL 兩個引擎）同時命中——這是 Ch 37 說的「跨工具佐證」的資料形態。合併腳本把它排到「corroborated locations」，告訴你：這個位置的可疑度最高，先驗證它（接 Ch 37 就是對它跑 ASan）。`vuln.c:8`、`vuln.c:11` 只有單一工具的 read source 命中，佐證弱，排後面。

這就是 SARIF 當匯流排的實際好處：**多工具結果一合併，共識自動浮現，排序自動有了依據**——不用你手動比對兩份不同格式的輸出。

### 邊界失敗：路徑對不上導致假去重失敗

若 `toolA.sarif` 的 uri 是 `vuln.c`、`codeql.sarif` 的是 `src/vuln.c`（不同 base 或不同工作目錄），去重的 key `(uri, line)` 就對不上，同一行的兩個命中被當成兩個不同位置，佐證浮不出來。**合併前必須正規化路徑**（統一相對 base、去掉 `./`、大小寫）。這是跨工具合併最常見的翻車，比想像中頻繁——不同工具對「同一個檔」的路徑寫法五花八門。

---

## 範例三：從 SARIF 生成人讀報告（真跑）

SARIF 是機器格式，交付給人（維護者、主管）要人讀報告。從 SARIF 生成 Markdown 很直接——`report.py` 按 ruleId 分組、統計、輸出表格：

```bash
python3 report.py toolA.sarif codeql.sarif
```

真實輸出（節錄）：

```markdown
# 靜態分析報告

命中總數：**2**，規則數：**2**

## `unbounded-memcpy` （1 個命中）
memcpy with non-constant length into fixed buffer.
| 工具 | 位置 |
|------|------|
| Semgrep OSS | `vuln.c:12` |

## `audit/memcpy-nonconst-len` （1 個命中）
memcpy with non-constant length
| 工具 | 位置 |
|------|------|
| CodeQL | `vuln.c:12` |
```

因為 SARIF 是結構化的，這份報告生成器**與工具無關**——同一支腳本吃 Semgrep、CodeQL、任何輸出 SARIF 的工具都行。你甚至可以在報告裡加上 Ch 36 的排序、Ch 39 的跨工具佐證標記，變成一份「已排序、已去重、已標佐證」的交付報告。

---

## 生態：SARIF 接到哪裡去

SARIF 的價值在它接進的整個生態，這裡點出主要的幾個接口：

### GitHub code scanning（上傳 SARIF）

GitHub 的 code scanning 直接吃 SARIF：你在 CI 產出 SARIF，用 `github/codeql-action/upload-sarif` action（或 API）上傳，GitHub 就把命中顯示在 **PR 的 diff 上**（哪一行有什麼問題）、Security 頁籤、並自動做 Ch 38 的 PR diff（只在 PR 標新增的）。

```yaml
# .github/workflows/scan.yml（未實測，需真 repo + Actions）
- name: Run Semgrep
  run: semgrep --config auto --sarif -o results.sarif .
- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

> 這段標**未實測**——需要一個真實 GitHub repo 且開啟 Actions/code scanning。步驟：把 `results.sarif` 產出後用上面的 action 上傳；GitHub 會在 Security > Code scanning 顯示命中，並在 PR 上把命中標在對應行、自動只 block 新增的（承接 Ch 38 的 baseline 概念，但由平台做）。要點：SARIF 的 `artifactLocation.uri` 必須是**相對於 repo 根**的路徑，否則 GitHub 對不上原始碼行（見踩雷）。

### VS Code SARIF viewer

微軟的 SARIF Viewer 擴充讓你在編輯器裡開任何 SARIF 檔，點命中直接跳到對應碼、看 codeFlow 的每一步。本地 triage 時比讀 JSON 舒服得多——尤其看 taint 路徑（codeFlows）時，viewer 會把 source→sink 的每一跳畫出來。

### DefectDojo / 缺陷管理

DefectDojo 這類漏洞管理平台吃 SARIF（import scan），把命中變成可指派、可追蹤狀態（open/verified/false positive/mitigated）的 finding，並做跨掃描去重（用指紋）、跨工具彙整。這是把 Ch 36 的「抑制紀錄/triage 紀錄」從腳本升級成平台的形態——finding 狀態持久化、有人負責、能出趨勢報表。

> DefectDojo 部分標**未實測**——需部署 DefectDojo 實例。步驟：`Import Scan` 選 SARIF 格式上傳，它自動建 finding、依指紋去重；後續掃描用 `Reimport` 保留 triage 狀態。

---

## 對比：直接讀工具原生輸出 vs 走 SARIF

```
                    直接讀原生輸出           走 SARIF
加一個工具          寫一份新 parser          工具輸出 SARIF 即可，消費端不動
跨工具合併          要對齊 N 種格式          統一結構，一支腳本
上傳 GitHub         每工具各自整合           統一 upload-sarif
去重/baseline       各工具指紋機制不同        統一 partialFingerprints
代價                N×M 膠水碼               學一種格式 + 處理路徑/嚴重度不一致
```

走 SARIF 的代價不是零——你得處理「嚴重度放哪不一致」「路徑 base 不一致」這些跨工具的細節（前面範例都踩過）。但這個代價是**一次性**的（處理好合併腳本就一勞永逸），而原生 parser 的代價是**每加一個工具/消費端就再來一次**。

---

## 踩雷集錦

**錯誤直覺一：兩個工具的 SARIF 直接 jq 合併就好，反正格式一樣。**
→ 正確認識：格式一樣不代表路徑一樣。不同工具/工作目錄下 `artifactLocation.uri` 寫法不同（`vuln.c` vs `src/vuln.c` vs `./vuln.c`），去重 key 對不上，同一行被當兩個位置、佐證浮不出來。合併前**必須正規化路徑**（統一 base、去 `./`）。這是跨工具合併最高頻的翻車。

**錯誤直覺二：SARIF 的 level 一定在 result 裡讀就好。**
→ 正確認識：嚴重度放哪各工具不一致——CodeQL 常放 result.level，Semgrep 放在 `rules[].defaultConfiguration.level`、result.level 是 null。只讀 result.level 會把 Semgrep 命中全當成預設 warning，排序全錯。要**先看 result.level，沒有再回退查 rules 表**（Ch 36 的 triage.py 就是這樣寫的）。

**錯誤直覺三：SARIF 版本不重要，能 parse 就行。**
→ 正確認識：SARIF 有版本（2.1.0 是主流，也有更舊的）。CodeQL 的 `sarif-latest` 會隨 CodeQL 版本變，`sarifv2.1.0` 才是固定版。上傳 GitHub 或餵某個消費端時，若它只吃特定版本，用 `latest` 可能哪天工具升級就格式微變、消費端解析失敗。跨系統交付時**釘死版本**（`--format=sarifv2.1.0`）比用 latest 穩。

**錯誤直覺四：SARIF 的路徑是絕對路徑，直接用就對得上碼。**
→ 正確認識：SARIF 路徑通常是**相對**於某個 `uriBaseId`（如 `%SRCROOT%`）的。上傳 GitHub 時，這個相對路徑必須相對於 **repo 根**，否則 GitHub 把命中標在錯的檔/行、或整個對不上顯示不出來。產 SARIF 時要確保 base 設對（在 repo 根跑掃描、或明確設 `--sarif-base-path` 之類的選項）。

**錯誤直覺五：把 SARIF 上傳到 GitHub/DefectDojo 就等於做完 triage 了。**
→ 正確認識：上傳只是**把命中顯示出來/存起來**，命中還是那一堆命中——排序、去重、真偽判定（Ch 12/36）一樣都得做。平台幫你做的是「呈現、diff（只標新增）、狀態持久化」，不是「幫你判哪個是真 bug」。以為上傳完就 triage 好了，等於把一堆未判的命中丟給平台當擺設。

---

## 進階延伸

- **codeFlows 才是 SARIF 對 taint 的殺手鐧**：前面範例只用了 location，但 taint 命中的 SARIF 帶 `codeFlows`——source 到 sink 的**每一跳**（每個中間變數、每次賦值）都在裡面。VS Code SARIF viewer 能把這條路徑畫出來讓你逐步走，這對 triage「這 flow 真的可達嗎」（Ch 12 四問之一）幫助巨大。合併/報告腳本進階版可以把 codeFlows 也帶進報告，讓 reviewer 看得到完整污染路徑而非只有 sink 位置。
- **SARIF 當「審計狀態的單一事實來源」**：把每次掃描的 SARIF（含指紋）存進版控或 artifact store，你就有了審計歷史——可以 diff 兩次掃描的 SARIF（Ch 38 的 baseline 就是這件事）、算規則 precision 隨時間的變化（Ch 36 的度量）、重現「三個月前那次掃描報了什麼」。SARIF 不只是一次性輸出，它是可累積、可 diff 的審計帳本。
- **自寫工具的 SARIF 輸出**：weggli 沒有原生 SARIF，你自己的檢查腳本也沒有——但 SARIF 是開放 schema，你可以**手工組**一份合規的 SARIF JSON（最小結構：`version` + `runs[].tool.driver.name` + `runs[].results[]` 帶 ruleId/message/locations）。這樣你的自製檢查就能無縫接進上面整個生態（GitHub 上傳、viewer、合併腳本）。把「輸出 SARIF」當成任何自製分析工具的標準交付格式，是讓它融入團隊流程最省力的方式。

---

## 本章重點整理

- **SARIF** 是靜態分析結果的通用 JSON 格式，把整合成本從 N×M（每工具×每消費端各寫膠水）降到 N+M（每工具輸出一次、每消費端讀一次）。
- 核心結構：**runs → results → locations**，加平行的 **rules 表**（嚴重度常在這）與可選的 **codeFlows**（taint 路徑）、**partialFingerprints**（去重/baseline 用）。
- 真跑合併：Semgrep 與 CodeQL 對 `~/audit-lab/ch39/vuln.c` 都命中 `vuln.c:12`，合併腳本自動標「3 tools agree」，把跨工具佐證的位置排最前——共識自動浮現。
- 兩個必處理的跨工具不一致：**路徑 base 不同**（合併前正規化，否則假去重失敗）、**嚴重度放哪不同**（result.level 或 rules 表，要都查）。
- 生態接口：GitHub code scanning（上傳 SARIF、PR 上顯示、自動 diff）、VS Code SARIF viewer（看 codeFlows）、DefectDojo（finding 狀態持久化）。SARIF 是這一切的公共匯流排。

## 自我檢核

- SARIF 把整合成本從什麼降到什麼？用 N、M 說明，並舉一個「加第五個工具」的例子說明省在哪。
- 畫出 SARIF 的核心層級（runs/results/locations/rules），指出嚴重度、行號、去重指紋各在哪個欄位。
- 合併兩工具 SARIF 前為什麼**必須**正規化路徑？不做會出現什麼假象？舉一個 uri 寫法不一致的具體例子。
- 為什麼讀 SARIF 嚴重度不能只看 result.level？Semgrep 和 CodeQL 各把它放哪？triage 腳本該怎麼寫才穩？
- 跨工具佐證（同位置多工具命中）為什麼是排序的好訊號？它跟 Ch 37 的動態驗證怎麼接？
- 「上傳 SARIF 到 GitHub」平台幫你做了什麼、沒幫你做什麼？把它當成 triage 完成會出什麼錯？

## 延伸閱讀

- **SARIF 2.1.0 官方規格（OASIS）**——權威定義，尤其 `run`、`result`、`location`、`codeFlow`、`partialFingerprints` 幾節。用法：合併腳本遇到搞不懂的欄位時查它；想手工組 SARIF（進階延伸）時照最小結構抄。前提：本章給你地圖，規格給你細節。偏工具書，不必通讀。
- **GitHub 官方 "Uploading a SARIF file to GitHub" / code scanning 文件**——本章 GitHub 部分標未實測的實作依據：upload-sarif action 怎麼用、路徑 base 怎麼設對、PR diff 怎麼運作。用法：有真 repo 時照它把本章的 SARIF 上傳，補上未實測那段。前提：本章 + Ch 38（PR diff 概念）。
- **微軟 SARIF Viewer for VS Code 擴充文件**——本地 triage 神器，尤其看 codeFlows。用法：把本章真跑產的 `codeql.sarif` 用它開，體會「點命中跳到碼、逐步走 taint 路徑」比讀 JSON 舒服多少。前提：本章；配 Ch 12 triage 用最好。
- **DefectDojo 官方 "Import scan" 文件與 SARIF parser 說明**——把 SARIF 升級成有狀態、可追蹤的 finding 管理。用法：想把 Ch 36 的「triage 紀錄」從腳本升級成平台時讀，理解 reimport 怎麼保留 triage 狀態。前提：Ch 36 + 本章。偏平台部署。

你現在能把多工具、多階段的結果匯流成一張總圖了。到這裡，整個規模化與整合的技術骨架——治理、動態驗證、diff 審計、SARIF 匯流——都齊了。最後一塊是當下最熱、也最容易被濫用的：**AI/LLM 輔助審計**。下一章我們冷靜拆解它的正確用法與陷阱：哪些事 LLM 真能加速（產 query 初稿、解釋陌生碼、triage 輔助），哪些事它會坑你（幻覺 API/CVE、把似是而非說成真），以及一條「LLM 說的怎麼驗」的紀律。

→ [Ch 40 AI 輔助審計](./40-ai-assisted-auditing.md)
