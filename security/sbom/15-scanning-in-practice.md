# Ch 15 — 掃描實戰：grype / trivy / osv-scanner

> **目標**：三工具並排真跑，親眼看見結果的差異從哪裡來；理解誤報漏報的根本原因（版本範圍判斷、distro backport、CPE 錯配）；掌握不同輸出格式與 CI gating 策略。

## 為什麼需要這個？

理論說完了。你知道 NVD 的 CPE 有問題、知道工具採集的資料庫組合不同。但「不同」到底是多少？差在哪裡？為什麼同一份 SBOM 用三個工具掃，結果各不一樣？

這章讓三個工具對同一份 SBOM 真跑，用數字和輸出格式回答這些問題。同時講清楚幾個讓掃描結果「看似有漏洞但其實沒事」、或「看似沒問題但實際有洞」的坑。

## 先建立直覺：三工具的定位

```
grype                       trivy                    osv-scanner
(Anchore)                   (Aqua Security)          (Google)
─────────────────────────   ─────────────────────    ────────────────────
輸入：image / dir / SBOM    輸入：image / dir / SBOM  輸入：SBOM / lockfile
DB：GHSA + NVD + distro     DB：NVD + distro + GHSA   DB：OSV.dev API
                            + go-vulndb/PyPA/...
特色：SBOM 原生支援          特色：多目標（misconfig、   特色：pure OSV
      EPSS/KEV 整合               secret 也能掃）          最精準 PURL 比對
      VEX 支援（--vex）          自帶 sbom 生成
      SARIF 輸出             SARIF 輸出               無 SARIF（JSON/table）
CI gating：--fail-on       CI gating：--exit-code    CI gating：有漏洞即 exit 1
```

沒有一個工具是「最準」的——它們有互補關係。最佳實踐是跑兩個以上，交叉確認 Critical/High 的發現。

## 實戰：三工具並排掃同一份 SBOM

用 Ch 13 建立的 Python 舊版 requirements SBOM：

```bash
cd /tmp/vuln-demo
# SBOM 已在 sbom.spdx.json
# 確認有 9 個 Python 套件
jq '.packages | length' sbom.spdx.json
# 10（含 requirements.txt 本身那筆 root package）
```

### grype

```bash
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | head -12
```

真實輸出（2026-08-17，grype 0.117.0，DB 2026-08-17）：

```
NAME          INSTALLED  FIXED IN  TYPE    VULNERABILITY     SEVERITY  EPSS          RISK
pillow        5.0.0      10.0.1    python  CVE-2023-4863     High      99.7% (99th)  85.6 (kev)
django        2.0.1      2.2.10    python  CVE-2020-7471     Critical  65.3% (99th)  60.6
django        2.0.1      2.1.9     python  CVE-2019-11358    Medium    87.2% (99th)  48.4
cryptography  2.1.4      39.0.1    python  CVE-2023-0286     High      59.5% (99th)  44.3
werkzeug      0.14.1     0.15.5    python  CVE-2019-14322    High      55.5% (98th)  41.6
django        2.0.1      2.2.9     python  CVE-2019-19844    Critical  35.1% (98th)  32.5
pyyaml        3.12       5.4       python  CVE-2020-14343    Critical  6.0% (92nd)   5.6
requests      2.18.0     2.20.0    python  CVE-2018-18074    High      7.4% (93rd)   5.6
```

**grype 計數（真跑）**：110 筆（Critical: 15、High: 53、Medium: 31、Low: 11）

grype 的亮點：**EPSS** 和 **KEV** 標記。`(kev)` 是 CISA Known Exploited Vulnerabilities 清單，代表這個漏洞在野外有真實利用案例；EPSS 99.7th percentile 代表「前 0.3% 最可能被利用的漏洞」。這讓你在 110 個漏洞裡知道從哪裡開始。

### trivy

```bash
DOCKER_CONFIG=/tmp/fake-docker-config \
  trivy sbom sbom.spdx.json --no-progress 2>/dev/null
```

真實輸出（節錄）：

```
Python (python-pkg)
===================
Total: 110 (UNKNOWN: 0, LOW: 11, MEDIUM: 31, HIGH: 53, CRITICAL: 15)

Library       Vulnerability    Severity  Status  Installed  Fixed    Title
──────────────────────────────────────────────────────────────────────────
cryptography  CVE-2018-10903   HIGH      fixed   2.1.4      2.3      python-cryptography: GCM tag forgery...
cryptography  CVE-2020-25659   HIGH      fixed   2.1.4      3.2      python-cryptography: Bleichenbacher...
cryptography  CVE-2023-0286    HIGH      fixed   2.1.4      39.0.1   openssl: X.400 address type confusion...
```

**trivy 計數（真跑）**：110 筆（Critical: 15、High: 53、Medium: 31、Low: 11）

trivy 的亮點：**Status 欄位**（`fixed` / `will_not_fix` / `affected`）和每行的漏洞 **Title**，更一眼知道這是什麼類型的問題。

### osv-scanner

```bash
osv-scanner --sbom sbom.spdx.json 2>/dev/null | head -20
```

真實輸出（節錄）：

```
+--------------------------------------+------+-----------+--------------+---------+
| OSV URL                              | CVSS | ECOSYSTEM | PACKAGE      | VERSION |
+--------------------------------------+------+-----------+--------------+---------+
| https://osv.dev/PYSEC-2026-1283      | 8.7  | PyPI      | cryptography | 2.1.4   |
| https://osv.dev/GHSA-3ww4-gg4f-jr7f  |      |           |              |         |
| https://osv.dev/PYSEC-2018-52        | 8.7  | PyPI      | cryptography | 2.1.4   |
| https://osv.dev/GHSA-fcf9-3qw3-gxmj  |      |           |              |         |
| https://osv.dev/PYSEC-2019-124       | 9.3  | PyPI      | sqlalchemy   | 1.2.0   |
| https://osv.dev/GHSA-887w-45rq-vxgf  |      |           |              |         |
```

**osv-scanner 計數（真跑）**：209 個不重複 ID（包含 PYSEC-* 和 GHSA-*，一個漏洞可能同時有兩個 ID）

osv-scanner 用 PYSEC（PyPI 生態的 OSV ID）和 GHSA 雙 ID 顯示，一個漏洞對應一組 URL。209 個 ID ≠ 209 個獨立漏洞，因為一個漏洞往往同時有 PYSEC 和 GHSA 兩個 ID。

### 比較總覽

| 工具 | 輸入 | 漏洞計數（同一份 SBOM） | ID 格式 |
|---|---|---|---|
| grype 0.117.0 | sbom:sbom.spdx.json | **110**（C:15/H:53/M:31/L:11） | CVE-*/GHSA-* |
| trivy 0.74.0 | sbom.spdx.json | **110**（C:15/H:53/M:31/L:11） | CVE-*/GHSA-* |
| osv-scanner 1.9.2 | --sbom sbom.spdx.json | **209 個 ID** | PYSEC-*/GHSA-* |

這個案例 grype 和 trivy 結果一致，是因為兩者在 Python 生態上都以 GHSA 為主要來源。osv-scanner 的 ID 數目多，是因為一個漏洞被列兩個 ID（PYSEC + GHSA），分開顯示。

> 提醒：這不代表三個工具永遠一致。掃 container image（含系統套件）時，distro advisory 的差異會讓結果明顯分岔（見下方「distro backport」一節）。

## 底層機制：誤報漏報從哪裡來

### 1. Distro backport：最常見的「誤報」來源

問題設定：Debian 12（Bookworm）安裝了 `openssl 1.1.1n-0+deb11u4`，NVD 說「openssl 1.1.1n 有 CVE-XXXX，修復在 1.1.1o」。

掃描工具看到 `1.1.1n`，比對 NVD：「版本號 < 1.1.1o，這是受影響的版本！」→ **誤報**。

實際上 `1.1.1n-0+deb11u4` 的 Debian suffix `-0+deb11u4` 代表 Debian 已經把修補程式 **backport**（移植）進來了，雖然版本號還是 `1.1.1n`，但漏洞已修復。

```
上游 openssl 版本線：
  1.1.1n (vulnerable)  →  1.1.1o (fixed)

Debian 做的事：
  把 1.1.1o 的 patch 移植回 1.1.1n
  打包版號：1.1.1n-0+deb11u4
                ↑ 版本號沒變！但 patch 已合入

純看版本號的工具（用 NVD/上游 range）：誤報
知道 distro advisory 的工具（知道 1.1.1n-0+deb11u4 已有 patch）：正確
```

trivy 內建各主流 distro 的 advisory（Debian Security Advisory、Red Hat RHSA 等），能正確辨識這種 backport 情境。grype 的 distro advisory 也有涵蓋，但細節上可能有差異。osv-scanner 主要用 OSV 格式的 advisory，部分 distro 的 backport 資訊可能不完整。

這正是「掃 container image（含系統套件）時三工具結果最容易分叉」的根本原因：系統套件的 backport 處理各工具功力不一。

### 2. CPE 錯配：誤報的另一個來源

```bash
# 看 syft 為 django 生成了幾個 CPE 變體
jq '[.packages[] | select(.name=="django") | .externalRefs[].referenceLocator]' sbom.spdx.json
```

輸出（節錄）：

```json
[
  "cpe:2.3:a:python-django:python-django:2.0.1:...",
  "cpe:2.3:a:python:django:2.0.1:...",
  "cpe:2.3:a:djangoproject:django:2.0.1:...",
  "cpe:2.3:a:django:django:2.0.1:...",
  "pkg:pypi/django@2.0.1"
]
```

syft 生成了 12 個 CPE 變體，試圖覆蓋 NVD 裡所有可能的 vendor 寫法。如果掃描工具純靠 CPE 比對，在 vendor 字串沒對上的情況下，就算版本完全符合也比不中（漏報）。反之，如果 CPE 字串湊巧和不相關產品的 CPE 相符，就是誤報。

PURL（`pkg:pypi/django@2.0.1`）是唯一確定的：`pypi` 是生態系的枚舉值，不是自由文字。用 PURL 比對的工具（grype 的 python-package matcher、osv-scanner）在這個維度誤報率遠低於純 CPE 比對。

### 3. 版本範圍邊界判斷

OSV format 的版本範圍：

```json
"ranges": [{
  "type": "ECOSYSTEM",
  "events": [
    {"introduced": "2.0.0"},
    {"fixed": "2.0.11"},
    {"introduced": "2.1.0"},
    {"fixed": "2.1.14"}
  ]
}]
```

這代表 `2.0.0 <= ver < 2.0.11` 或 `2.1.0 <= ver < 2.1.14` 是受影響的。邊界處的版本（`2.0.11`、`2.1.14`）是第一個修好的版本，本身不受影響。

如果工具在邊界判斷上有 off-by-one 或語意理解偏差（`<` 還是 `<=`），同樣的版本號可能得到「有漏洞」或「沒漏洞」兩種結果。

## 輸出格式與 CI gating

### grype 的輸出格式

```bash
# 表格（人看）
grype sbom:sbom.spdx.json -o table

# JSON（機器讀）
grype sbom:sbom.spdx.json -o json 2>/dev/null | \
  jq '[.matches[] | select(.vulnerability.severity=="Critical") |
       {pkg:.artifact.name, ver:.artifact.version,
        vuln:.vulnerability.id, fix:(.vulnerability.fix.versions[0])}]'

# SARIF（CI/Code Scanning）
grype sbom:sbom.spdx.json -o sarif > results.sarif

# CI gating
grype sbom:sbom.spdx.json --fail-on high
# 有 High 以上漏洞時：exit code 2，讓 CI pipeline 停下來
```

真實測試（這份 SBOM 有 High 漏洞）：

```
$ grype sbom:sbom.spdx.json --fail-on high -o json 2>/dev/null > /dev/null; echo $?
2
```

exit code 2 代表「找到超過 threshold 的漏洞」。在 CI 腳本裡：

```bash
grype sbom:sbom.spdx.json --fail-on critical || {
  echo "CRITICAL vulnerabilities found. Blocking deploy."
  exit 1
}
```

### trivy 的 CI gating

```bash
# 有 HIGH/CRITICAL 就讓 CI 失敗
DOCKER_CONFIG=/tmp/fake-docker-config \
  trivy sbom sbom.spdx.json --exit-code 1 --severity HIGH,CRITICAL

# 真跑結果
$ ... ; echo $?
1
```

trivy 也支援 `--ignore-unfixed`：

```bash
# 只在「有修復版本」的漏洞上 gating（忽略還沒修好的）
trivy sbom sbom.spdx.json --exit-code 1 \
  --severity HIGH,CRITICAL --ignore-unfixed
```

`--ignore-unfixed` 是非常實用的 CI gating 選項：如果漏洞還沒有修復版本（`FIXED IN` 為空），你報錯讓 CI 失敗但開發者什麼都做不了——這只是在製造噪音。只在「有修好的版本、可以升級」的漏洞上做 gating 才有意義。

### osv-scanner 的 CI gating

```bash
# 有漏洞就失敗（非零 exit code）
osv-scanner --sbom sbom.spdx.json

# 指定 output format
osv-scanner --sbom sbom.spdx.json --format json 2>/dev/null | \
  jq '[.results[].packages[].groups[] | {ids:.ids, packages:.packages[].name}]'
```

osv-scanner 目前沒有 `--fail-on severity` 功能（因為 OSV 的 severity 資訊不如 CVSS 統一），它的 CLI 也**沒有專用的失敗旗標**——找到任何漏洞就直接以 exit code 1 結束（exit 127 是一般錯誤、128 是沒找到 package，CI 裡別把這三個混成同一個「安全門失敗」）。常被誤用的 `--fail-on-vuln` 其實是 osv-scanner **GitHub Action** 的輸入參數，不是 CLI 旗標。

### 完整工具對比

| 面向 | grype | trivy | osv-scanner |
|---|---|---|---|
| SBOM 輸入 | `sbom:<file>` | `sbom <file>` | `--sbom <file>` |
| Lockfile 輸入 | `--file` | `fs .` | `--lockfile` |
| 輸出格式 | table/json/sarif | table/json/sarif/cyclonedx | table/json |
| CI gating | `--fail-on <sev>` | `--exit-code 1 --severity ...` | 有漏洞即 exit 1（無專用旗標） |
| VEX 支援 | `--vex <file>` | `--vex <file>` | 無 |
| EPSS/KEV | 是（DB 整合） | 否 | 否 |
| Distro backport | 部分 | 較完整（內建各主流 distro advisory） | 依 OSV 涵蓋 |
| Air-gapped DB | `--db` 旗標 | `--cache-dir` + 預先下載 DB | 需網路（查 API） |

## Air-gapped 環境的 DB 問題

掃描工具需要定期更新漏洞 DB。在無網路的 air-gapped 環境裡，需要預先把 DB 帶進去。

**grype**：

```bash
# 在有網路的機器把 DB 下載為 tar
grype db update
grype db export > grype-db-backup.tar

# 在 air-gapped 機器還原
grype db import grype-db-backup.tar
```

**trivy**：

```bash
# 預先下載 DB（需要有 OCI registry 或 oci-db-image）
trivy image --download-db-only --cache-dir /path/to/db-cache

# air-gapped 掃描（指向本地 cache）
trivy sbom sbom.spdx.json --skip-db-update --cache-dir /path/to/db-cache
```

**osv-scanner**：依賴網路呼叫 OSV.dev API，air-gapped 環境支援較差（可以用 `--offline-vulnerabilities` 但需要本地 OSV DB 快取）。

DB 更新頻率的重要性：grype 的 DB 每天更新一次（你可以從 DB status 看到 `Built: 2026-08-17T06:19:33Z`）。如果你三個月沒更新 DB，掃出來的結果可能少了幾百個新 CVE。CI 環境要確保每次掃描都是最新 DB，或至少每週更新一次。

## 踩雷集錦

1. **「三個工具數字都一樣」代表都準**：在這個純 Python 套件的範例裡，grype 和 trivy 的數字剛好相同，因為都以 GHSA 為主要來源。掃 Alpine/Debian image 時（包含 OS 套件），各工具對 distro backport 的處理差異會讓數字明顯分叉。用純語言套件作為三工具一致的基準，是個好的 sanity check，但不代表萬能。
2. **`--ignore-unfixed` 不等於忽略嚴重問題**：它只是把「還沒有修復版本、你現在什麼都做不了」的漏洞從 gating 排除。你還是應該追蹤這些漏洞，等上游修好再升級。把它們記在 backlog 或用 VEX 的 `under_investigation` 狀態標記。
3. **osv-scanner 的 ID 計數不等於漏洞數**：osv-scanner 的一個漏洞往往有兩個 ID（PYSEC-* + GHSA-*），並排顯示。209 個 ID ≠ 209 個獨立漏洞，實際上對應的是約 110 個獨立漏洞（和 grype/trivy 一致）。
4. **SARIF 上傳 GitHub 的格式限制**：GitHub Code Scanning 對 SARIF 有大小和格式限制。大型 image 掃出幾千個漏洞的 SARIF 可能上傳失敗。建議用 `--severity HIGH,CRITICAL` 過濾後再上傳，而不是全量。
5. **DB 快取是工具自管的**：grype 的 DB 快取在 `~/.cache/grype/`，trivy 在 `~/.cache/trivy/`。在 CI 的容器化環境裡，每次重建 runner 都會從頭下載。設定 cache volume/layer 讓 CI 不要每次都重新下載幾百 MB 的 DB。

## 進階：再往深一層

### 工具版本釘定的必要性

grype 的漏洞比對邏輯本身也在演化（比對演算法、DB schema 版本都會改）。同一份 SBOM、同版本 DB、但不同版本的 grype，結果可能不同。

生產級的安全管線要釘工具版本：

```bash
# 不要這樣（抓 latest）
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b ~/bin

# 要這樣（釘版本）
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b ~/bin v0.117.0
```

這樣「同一份 SBOM + 同版本工具 + 同版本 DB = 完全可重現的掃描報告」才能成立。這對安全稽核的可重現性是硬需求。

### 誰先掃、誰補誰

實務建議的組合：

```bash
# 主力：grype（EPSS/KEV 整合，快，支援 VEX）
grype sbom:sbom.spdx.json --fail-on critical --vex my.vex.json

# 交叉驗證：trivy（更好的 distro backport 支援）
DOCKER_CONFIG=/tmp/fake-docker-config \
  trivy sbom sbom.spdx.json --ignore-unfixed --severity HIGH,CRITICAL

# 精確比對：osv-scanner（OSV PURL 最乾淨）
osv-scanner --sbom sbom.spdx.json --format json | \
  jq '[.results[].packages[].groups[]] | length'
```

三個都跑，Critical 漏洞三個都出現才信心最高；只有一個出現的就值得仔細查是誤報還是工具差異。

## 動手練習

1. 對 `/tmp/vuln-demo/sbom.spdx.json` 分別跑三個工具，記錄各自的 Critical/High 數量，確認它們在 Python 套件這個場景下是否一致。
2. 用 `grype sbom:sbom.spdx.json --fail-on high -o json 2>/dev/null > /dev/null; echo "exit: $?"` 看 exit code，理解為什麼這個對 CI gating 重要。
3. 加上 `--ignore-unfixed`（trivy）或用 jq 過濾掉 `FIXED IN` 為空的漏洞（grype），看看「有修復路徑的 High 以上漏洞」實際上有多少個。

## 本章重點整理

- 三工具在純語言套件場景結果高度一致（都以 GHSA 為主要來源），但在 container image 的系統套件上，distro backport 處理的差異會讓結果分叉。
- 誤報的根本原因：CPE vendor 欄位不標準化（上游 range 和 distro backport 版本號不一致）。漏報的根本原因：工具沒有覆蓋到某個 advisory 來源、或版本範圍邊界判斷不一致。
- `--fail-on` / `--exit-code` 做 CI gating，但搭配 `--ignore-unfixed` 才合理：阻擋「有修好版本但你還沒升級」的漏洞，不阻擋「上游還沒修好、你什麼都做不了」的漏洞。
- 工具和 DB 版本要同時釘定，掃描結果才能重現。

## 自我檢核

- [ ] 我能解釋 distro backport 為什麼造成誤報，以及哪種工具更能正確處理它
- [ ] 我知道 osv-scanner 的「209 個 ID」和 grype 的「110 個漏洞」為什麼不矛盾
- [ ] 我能在一個 CI yaml 裡寫出「掃 SBOM，有 Critical 以上有修復路徑的漏洞就阻擋」的指令
- [ ] 我理解 air-gapped 環境下為什麼 osv-scanner 是最麻煩的工具

## 延伸閱讀

- **[grype: Supported Sources and Output Formats](https://github.com/anchore/grype#supported-sources)** — 完整的輸入源和輸出格式清單，以及 `--fail-on` 的 severity 等級說明
- **[trivy: SBOM Scanning](https://aquasecurity.github.io/trivy/latest/docs/target/sbom/)** — trivy 對 SBOM 輸入的具體說明，包含 SPDX/CycloneDX 的支援狀態
- **[osv-scanner: Using SBOM](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/#sbom)** — osv-scanner 的 SBOM 輸入支援，以及它用 OSV API 查詢的機制
- **[FIRST EPSS](https://www.first.org/epss/model)** — EPSS 模型的技術說明；了解這個分數怎麼算出來，能讓你更清楚它的局限（不是每個 99th percentile EPSS 都代表「你一定會被打」）
- **[Debian Security Tracker](https://security-tracker.debian.org/tracker/)** — 查任何 CVE 在 Debian 各版本的狀態（包含是否 backport）；這是理解 distro backport 最直接的第一手來源

---

你知道掃描能找到什麼，也知道誤報從哪裡來。問題是：很多漏洞雖然「技術上存在」，但在你的具體產品裡根本無法被利用。下一章是這個 Part 的核心：VEX，讓你用機器可讀的方式說清楚「這個 CVE 對我的產品到底有沒有影響」。

→ [Ch 16 VEX：有漏洞不等於可被利用](./16-vex.md)
