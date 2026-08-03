# Ch 17 — Semgrep 進 CI

> **目標**：把 Semgrep 從「本機手動一條條跑」推進到「CI/CD 自動把關」。你會搞懂 `semgrep ci` 與本機 `semgrep --config` 的差別、**baseline**（`--baseline-commit` 只報新增問題，讓舊債不擋 PR）、**diff-aware**（只掃 PR 改動）、**SARIF** 輸出（對接 Ch 39 生態）、以及 `# nosemgrep`／per-path 抑制。在 `~/audit-lab` 真建一個 git repo，實跑 baseline 與 SARIF，貼真實輸出。最後講「CI 一上來全開規則淹沒團隊」「沒 baseline 舊債擋 PR」「把 CI gate 當唯一防線」這些讓資安門禁失敗的真實踩雷。
>
> **環境**：WSL Ubuntu、Semgrep 1.172.0（OSS 版）、git。靶在 `~/audit-lab/ch17/repo`（真建的 git repo，含 baseline 舊債 + 新增漏洞兩個 commit）。GitHub Actions 段落是設定範例（本機無法真跑 Actions runner，標「未實測，理論預期」附完整步驟）；baseline / SARIF / nosemgrep 全部本機真跑貼輸出。

前四章你會寫規則、跨語言掃了。但審計工具的價值不在「你偶爾手動跑一次」，而在「每個 PR 自動掃、掃到高風險就擋下」——把安全左移（shift left）進開發流程。這章講怎麼把 Semgrep 接進 CI，以及一堆團隊真的踩過的坑：規則太多淹沒團隊、舊債擋住新 PR、gate 變成唯一防線然後被繞過。

工具接 CI 的技術部分很快就會，**難的是「治理」**——什麼規則該擋 PR、舊債怎麼辦、誤報怎麼收斂。技術與治理這章都碰，治理的深水區留 Ch 36。

## 從本機掃到 CI：`semgrep ci` vs `semgrep scan`

你前幾章跑的是 `semgrep --config <rule>`（`semgrep scan` 的形式）——**全量掃**，掃什麼報什麼。CI 場景有個專用子命令：

```
semgrep ci
```

`semgrep ci` 與 `semgrep scan` 的差別：

| | `semgrep scan --config X` | `semgrep ci` |
|---|---|---|
| 用途 | 本機、一次性、指定規則 | CI 環境專用 |
| 規則來源 | 你 `--config` 指定 | 自動讀 `.semgrep.yml`／`semgrep.dev` 專案設定 |
| diff 感知 | 預設全量 | **自動偵測 CI 環境的 PR/base**，只報新增 |
| 退出碼 | 有 finding 回非 0 | 依 blocking/non-blocking 政策決定 exit code（擋不擋 PR） |
| 上報 | 無 | 可上報 Semgrep AppSec Platform（選用） |

**關鍵：`semgrep ci` 在 CI 裡會自動 diff-aware**——它認得 GitHub Actions／GitLab CI 的環境變數，抓出 PR 的 base commit，只報「這個 PR 新增的」問題。這正是團隊要的：舊債不擋 PR，只擋你這次改壞的。

不過 `semgrep ci` 的完整威力（自動讀平台設定、自動 diff）依賴 CI 環境變數與（選用的）Semgrep 平台。本機要**手動驗證** diff/baseline 邏輯，用底層旗標 `--baseline-commit`，下面真跑它。

## 真跑：baseline（`--baseline-commit` 只報新增）

在 `~/audit-lab/ch17/repo` 真建了一個 git repo：一個 baseline commit 帶「舊債」漏洞，再一個 commit 加「新漏洞」。規則是 Ch 16 的 Python `os.system` taint 規則。

**專案結構**（`app.py` 兩次 commit）：

```python
# baseline commit（舊債）
def old_handler():
    host = request.args.get("host")
    os.system("ping " + host)   # 第 5 行：baseline 已存在的舊債

# 第二個 commit 加的
def new_handler():
    host = request.args.get("host")
    os.system("rm " + host)     # 第 9 行：新增漏洞
```

**A. 全量掃**（`semgrep scan` 語意，掃什麼報什麼）：

```
$ semgrep --config ../ci-rule.yml app.py
Ran 1 rule on 1 file: 2 findings.
            5┆ os.system("ping " + host)   # 舊債：baseline 已存在
            9┆ os.system("rm " + host)     # 新增漏洞：diff 才有
```

兩個都報——新舊漏洞一視同仁。**如果 CI 這樣配，第一天上線就會噴出所有歷史舊債，把 PR 全擋死。**

**B. baseline 掃**（`--baseline-commit <舊 commit>`，只報新增）：

```
$ semgrep --config ../ci-rule.yml --baseline-commit 3022435... app.py

  Current version has 2 findings.
Creating git worktree from '3022435...' to scan baseline.
  Will report findings introduced by these commits ...
 • Scan was limited to files changed since baseline commit.
Ran 1 rule on 1 file: 1 finding.
            9┆ os.system("rm " + host)     # 新增漏洞：diff 才有
```

**只報第 9 行的新漏洞，第 5 行的舊債被壓下去了。** 機制是：Semgrep 從 baseline commit 建一個 git worktree，在**舊版本上也跑一遍**，把「舊版本已存在的 finding」從當前 finding 裡扣掉，只留「這次新引入的」。這就是「舊債不擋 PR、只擋你改壞的」的落地——團隊能一邊清舊債一邊不讓新洞漏進去。

## diff-aware 掃描：只掃 PR 改動

baseline 是「只**報**新增 finding」；還有一層是「只**掃**改動的檔案」——PR 改了 3 個檔就只掃這 3 個，1000 個舊檔不碰。好處是**快**（大 repo 全量掃可能幾分鐘，diff 掃幾秒）。

`semgrep ci` 在 GitHub Actions 的 PR 觸發下**自動**做 diff-aware（抓 `GITHUB_BASE_REF` 算 base）。手動等價是配合 `--baseline-commit` + git diff 限定 target。本機上面 B 的輸出已經印了 `Scan was limited to files changed since baseline commit`——它連掃描範圍都縮到改動檔了。

diff-aware 與 baseline 常一起用，對回 [Ch 38 diff-based auditing](./38-diff-based-auditing.md)——那章從審計方法論角度講「只看 diff 的攻防」，這章從工具/CI 角度講「怎麼讓 CI 只掃 diff」。

## 真跑：SARIF 輸出（對接 Ch 39 生態）

CI 掃完的結果要能被別的工具吃——GitHub Code Scanning、SIEM、缺陷追蹤系統。標準格式是 **SARIF**（Static Analysis Results Interchange Format，靜態分析結果交換格式，OASIS 標準的 JSON）。Semgrep 直接輸出：

```
$ semgrep --config ../ci-rule.yml --sarif -o out.sarif app.py
```

真跑產出的 `out.sarif` 關鍵欄位（用 Python 挖出來）：

```
tool.driver.name: Semgrep OSS
num results: 2
ruleId: py-os-system-taint
fingerprint keys: ['matchBasedId/v1']
```

一條 result 的實際結構（照貼 SARIF JSON 片段）：

```json
{
  "ruleId": "py-os-system-taint",
  "message": { "text": "Flask 輸入流入 os.system，命令注入 (CWE-78)。" },
  "locations": [{
    "physicalLocation": {
      "artifactLocation": { "uri": "app.py", "uriBaseId": "%SRCROOT%" },
      "region": {
        "startLine": 5, "startColumn": 5,
        "endLine": 5, "endColumn": 30,
        "snippet": { "text": "    os.system(\"ping \" + host) ..." }
      }
    }
  }],
  "fingerprints": { "matchBasedId/v1": "..." }
}
```

SARIF 的價值：

- **`ruleId` + `locations.region`**：哪條規則、哪個檔哪行哪欄——GitHub Code Scanning 就靠這個把 finding 標在 PR diff 的對應行。
- **`fingerprints`（`matchBasedId/v1`）**：finding 的穩定指紋。程式碼小改（行號位移）時，同一個 finding 指紋不變，追蹤系統就不會把它當「新問題」重複開單——這是 SARIF 做去重／狀態追蹤的關鍵。
- **跨工具通用**：CodeQL、Joern、其他掃描器都能輸出 SARIF，統一格式讓你把多工具結果匯進同一個 dashboard。

SARIF 生態（怎麼被 GitHub/GitLab/SIEM 消費、多工具結果合併、去重）是 [Ch 39 SARIF 生態](./39-sarif-ecosystem.md) 的主題，這章只示範 Semgrep 怎麼吐出來。

## 真跑：抑制（`# nosemgrep`）

不是每個 finding 都要修——有些是誤報、有些是「知道但接受的風險」。Semgrep 的行內抑制是註解 `# nosemgrep`（或指定規則 `# nosemgrep: <rule-id>`）：

```python
def h():
    host = request.args.get("host")
    os.system("ping " + host)  # nosemgrep: py-os-system-taint
    os.system("rm " + host)
```

真跑：

```
$ semgrep --config ../ci-rule.yml sup.py
Ran 1 rule on 1 file: 1 finding.
            6┆ os.system("rm " + host)
```

**第 5 行（帶 `# nosemgrep`）被抑制，只報第 6 行。** 用 `# nosemgrep: py-os-system-taint` 指定規則 ID 是好習慣——裸 `# nosemgrep` 會抑制**該行所有規則**，太廣，可能連你沒想抑制的別條規則一起關掉。

抑制的另外兩層：

- **per-path 設定**：`.semgrepignore` 檔（語法像 `.gitignore`）排除整個目錄——`test/`、`vendor/`、`node_modules/` 這種不該掃的。也可在規則層用 `paths: exclude:` 限定。
- **rule 排除**：CI 設定裡挑選 ruleset，把某些規則整條關掉。

## GitHub Actions 整合（設定範例，未實測）

> 以下是 CI 設定範例。本機無 Actions runner，**未實跑**，標「理論預期」；語法依 Semgrep 官方文件。

`.github/workflows/semgrep.yml`：

```yaml
name: Semgrep
on:
  pull_request: {}          # PR 觸發 → semgrep ci 自動 diff-aware
  push:
    branches: [main]
jobs:
  semgrep:
    runs-on: ubuntu-latest
    container: semgrep/semgrep   # 官方映像，已裝 semgrep
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0         # 關鍵：要完整 git 歷史，diff/baseline 才算得出 base
      - run: semgrep ci --sarif -o results.sarif || true
      - uses: github/codeql-action/upload-sarif@v3   # SARIF 上傳到 Code Scanning
        with: { sarif_file: results.sarif }
```

理論預期行為：PR 觸發時 `semgrep ci` 認得 `GITHUB_BASE_REF` 自動做 diff-aware（只報 PR 新增問題）；`--sarif` 輸出上傳到 GitHub Code Scanning，finding 直接標在 PR diff 對應行。要真跑就是把這檔放進 repo 開個 PR，我在 WSL 本機用 `--baseline-commit` 手動驗過的正是 `semgrep ci` 在 Actions 裡自動做的那件事（上面 B 段真輸出）。

**pre-commit hook**（提交前本機擋，更左移一層）：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/semgrep/semgrep
    rev: v1.172.0
    hooks:
      - id: semgrep
        args: ['--config', 'auto', '--error']
```

pre-commit 在**本機提交時**跑，快、只掃 staged 檔，把明顯問題擋在推上遠端前。但它可被 `--no-verify` 繞過，所以只是第一道、不是唯一一道——CI 的 gate 才是強制的。

## 大規模跑的實務

真實大 repo 跑 Semgrep 的工程考量：

- **效能**：全量掃大 repo 慢。用 diff-aware 只掃改動、`--jobs N` 開多核、快取（Semgrep 平台有 baseline 快取）。CI 裡優先 diff 掃、定期（nightly）才全量。
- **false positive 治理**：規則太多、太寬 → 誤報淹沒 → 團隊開始無視所有告警（alert fatigue，告警疲勞）。這是資安門禁最常見的死法。收斂方法（哪些規則設 blocking、哪些 non-blocking、誤報怎麼系統性下修）是 [Ch 36 誤報治理](./36-false-positive-governance.md) 的主題。
- **blocking vs non-blocking 分層**：不是所有規則都該擋 PR。高信度、高危害（命令注入、硬編私鑰）設 blocking（擋 PR）；低信度、風格類設 non-blocking（只提示不擋）。一上來全設 blocking 是災難。

## 踩雷集錦

**錯誤直覺：「CI 上 Semgrep 就把 registry 全部規則開起來、全設 blocking，越多越安全。」**
正確認識：這是團隊放棄 Semgrep 的頭號原因。全開規則 = 幾百上千條、大量誤報 + 一堆低危風格告警，第一個 PR 就被幾十個告警淹沒。人的反應是**無視所有告警**（alert fatigue），於是真正的高危漏洞也被一起無視——比不裝還糟。正確做法：一小撮**高信度高危害**規則設 blocking，其餘 non-blocking 或先不開，觀察誤報率再慢慢加（Ch 36）。

**錯誤直覺：「CI 掃出東西就擋 PR，不管新舊。」**
正確認識：第一天上線就會噴出所有歷史舊債（上面 A 段真跑：全量報 2 個，含一個舊債），把每個 PR 都擋死，開發者根本推不了任何 code。必須用 **baseline**（`--baseline-commit`／`semgrep ci` 的自動 diff）只報**新增**問題（B 段真跑：只報 1 個新的），讓團隊「舊債慢慢清、新洞立刻擋」。沒 baseline 的 CI gate 活不過第一週。

**錯誤直覺：「CI gate 過了就代表 code 安全，這是最終防線。」**
正確認識：Semgrep（任何 SAST）會**漏報**——動態語言 taint 斷（Ch 16）、跨檔案 flow 追不動、自訂 wrapper sink 沒建模、規則沒覆蓋的漏洞類別。CI gate 綠燈只代表「規則沒抓到」，不代表「沒漏洞」。它是**縱深防禦的一層**，不是唯一防線——後面還要 code review、動態測試（Ch 37）、人工審計。把綠燈當「安全認證」是危險的錯覺。

**錯誤直覺：「`# nosemgrep` 很方便，看到誤報就加一個關掉。」**
正確認識：`nosemgrep` 濫用會**掏空整個 gate**。開發者遇到告警不想修就加 `# nosemgrep`，久了滿 repo 都是抑制註解，gate 形同虛設。而且裸 `# nosemgrep`（不帶 rule ID）會抑制該行**所有**規則，可能連未來新增的、真正重要的規則一起關掉。治理上要：抑制必須帶 rule ID + 理由註解、code review 時審抑制、定期盤點抑制清單（誰、為什麼、還成立嗎）。抑制是逃生口，不是常態。

**錯誤直覺：「Actions 裡 `checkout` 完 semgrep 就能算 diff 了。」**
正確認識：`actions/checkout` 預設**淺 clone**（`fetch-depth: 1`，只抓最新一個 commit），Semgrep 算 baseline/diff 需要 base commit 的歷史，淺 clone 下算不出來（或退化成全量）。要設 `fetch-depth: 0`（完整歷史）。這是 CI 裡 diff-aware「莫名失效變全量」的頭號原因，光看 workflow 不容易發現。

## 進階延伸

- **`semgrep ci` 的自動 CI 偵測**：它認得 GitHub Actions / GitLab CI / CircleCI 等環境變數自動抓 base ref、自動 diff-aware、自動決定 exit code 政策。讀 Semgrep 官方 "Continuous Integration" 文件看它在每個平台認哪些環境變數——理解這個就懂為什麼本機要手動 `--baseline-commit` 而 CI 裡不用。
- **exit code 與 gate 策略**：`--error`（有 finding 回非 0 擋 PR）vs 預設（回 0 不擋）、blocking/non-blocking 規則分層（`semgrep ci` 依規則 metadata 的 severity 決定）。設計 gate 時，exit code 政策就是「什麼擋、什麼放」的實作。
- **與 CodeQL CI 的對比**：GitHub 原生的 CodeQL Action 也走 SARIF + Code Scanning 同一套生態（Ch 39），但 CodeQL 要先 build DB（編譯型語言慢）、規則是 QL。Semgrep 快、免 build、規則好寫但分析淺；CodeQL 慢、要 build、分析深。CI 裡常兩者並用（Semgrep 快掃每 PR、CodeQL nightly 深掃）——多工具漏斗是 Ch 35 的主題。

## 本章重點整理

- `semgrep ci`（CI 專用，自動 diff-aware、自動讀平台設定、依政策決定 exit code）vs `semgrep scan`（本機全量、指定規則）。
- **baseline**（`--baseline-commit`）只報**新增** finding：真跑驗證全量報 2 個（含舊債）、baseline 只報 1 個新的——讓舊債不擋 PR。機制是在 baseline commit 的 worktree 也跑一遍再扣掉。
- **diff-aware** 只**掃**改動檔（快），與 baseline 常一起用，對接 Ch 38。
- **SARIF**（`--sarif`）是跨工具標準 JSON：`ruleId` + `locations.region` 定位、`fingerprints` 做去重追蹤，被 GitHub Code Scanning 等消費，對接 Ch 39。
- 抑制：行內 `# nosemgrep: <rule-id>`（真跑驗證抑制該行）、`.semgrepignore` per-path、規則排除——都是逃生口不是常態。
- 治理才是難點：規則分層（高危 blocking、其餘 non-blocking）、baseline 處理舊債、誤報收斂（Ch 36）、CI gate 只是縱深一層不是唯一防線。

## 自我檢核

- `semgrep ci` 和 `semgrep scan --config X` 差在哪？為什麼 CI 裡用前者、本機驗證 baseline 邏輯用後者 + `--baseline-commit`？
- baseline 掃描的機制是什麼？（主動回憶：全量報幾個、baseline 報幾個、差在哪一行、Semgrep 內部怎麼算出「新增」的）沒有 baseline 的 CI gate 為什麼活不過第一週？
- SARIF 裡 `fingerprints`（`matchBasedId/v1`）解決什麼問題？沒有它，code 小改（行號位移）會怎樣？
- 「CI 一上來全開所有 registry 規則且全設 blocking」為什麼是團隊放棄 Semgrep 的頭號原因？正確的規則分層策略長怎樣？
- 為什麼「CI gate 綠燈」不等於「code 安全」？舉兩個 Semgrep 會漏報的具體情境。
- Actions workflow 裡 `fetch-depth: 0` 為什麼是 diff-aware 能運作的關鍵？漏了會怎樣？

## 延伸閱讀

- **Semgrep 官方文件 "Continuous Integration" / "Semgrep CI"（semgrep.dev/docs）**——`semgrep ci` 在各 CI 平台的自動偵測、diff-aware 設定、exit code 政策、baseline 的權威說明。用法：把本章手動 `--baseline-commit` 驗過的邏輯，對照文件看 `semgrep ci` 在 Actions/GitLab 裡怎麼自動做同一件事。前提：本章。
- **Semgrep 官方文件 "Ignoring findings"（nosemgrep / .semgrepignore / paths）**——所有抑制機制的完整語法與優先序。用法：建立團隊抑制規範前先讀全，避免裸 `# nosemgrep` 這種過廣抑制。前提：本章抑制段。
- **SARIF 2.1.0 規格（OASIS）+ GitHub "Uploading a SARIF file"**——SARIF 欄位的權威定義與 GitHub Code Scanning 怎麼消費。用法：Ch 39 深入前先讀 `results`/`locations`/`fingerprints`/`rules` 這幾個核心物件，理解本章印出的 JSON 每個欄位幹嘛。前提：本章 SARIF 段，接 Ch 39。
- **Ch 36 誤報治理（./36-false-positive-governance.md）**——CI 規模下誤報怎麼系統性收斂、blocking/non-blocking 怎麼分、alert fatigue 怎麼防。用法：這章講「怎麼把 Semgrep 接進 CI」，那章講「接進去之後怎麼讓它不被團隊唾棄」，是本章治理踩雷的正解。前提：本章。

Semgrep 三章（規則工程 + 跨語言 + CI）到此收齊。你已經能寫規則、跨語言掃、進 CI 自動把關。下一個練習把 Ch 13-17 全部拼起來：針對一類真實漏洞寫一組完整的 taint 規則（含 sanitizer）、寫 `.test` 標記驗證抓髒放乾淨、輸出 SARIF——這是 Semgrep 部分的實戰總驗收，做完你就有一套能上 CI 的自製規則。

→ [練習 C：Semgrep taint 規則抓 CVE](./practice-c-semgrep-taint-rules.md)
