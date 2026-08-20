# Ch 12 — SBOM 品質與完整度

> **目標**：能判斷一份 SBOM 的品質——哪些維度決定它是否可靠、如何用 sbomqs 量化評分、如何從輸出裡一眼讀出「這份靠不靠譜」。讀完你不只知道怎麼生 SBOM，還知道怎麼評 SBOM。

## 為什麼需要這個？

我們花了三章學生成——source-based、syft cataloger、build-time plugin。現在假設你拿到一份 SBOM，不管是你自己生的還是廠商給的，你怎麼判斷它的品質？

「SBOM 有 12 個 package」——這個數字有意義嗎？如果這個 project 有 50 個傳遞依賴，那這份 SBOM 漏了 38 個。如果每個 package 都沒有 PURL，你拿它去 grype 掃漏洞，比對率是零。如果版本欄位是 `UNKNOWN`，你無從知道是否有 CVE。

**一份 SBOM 的品質決定它的可用性**。爛的 SBOM 比沒有更糟——你以為你知道你的依賴，但實際上看到的只是冰山一角，而你不知道看到的是冰山一角。

## 先建立直覺：SBOM 的品質維度

把 SBOM 品質想成一張評分卡，從幾個正交的維度評：

```
                   品質維度
  ──────────────────────────────────────────────────
  深度       │ 只有直接依賴？還是完整傳遞依賴？
  識別符      │ 有 PURL？有 CPE？能讓掃描工具比對？
  版本        │ 精確版本？還是範圍（^4.0）或 UNKNOWN？
  完整性      │ 有 hash/checksum？能驗證 package 沒被篡改？
  授權        │ 授權欄位有填？是 SPDX 標準格式？
  關係圖      │ 只有 package 清單？還是有誰依賴誰的關係？
  生成資訊    │ 什麼工具、什麼版本、什麼時間生成的？
  ──────────────────────────────────────────────────
```

這些維度彼此獨立——一份 SBOM 可能深度很好（傳遞依賴齊全）但識別符很差（沒有 PURL），也可能識別符完整但深度不夠。每個維度爛掉對應的下游工作就受影響。

## SBOM 可能爛在哪裡

### 問題 1：深度不足（Depth）

最常見的品質問題。只列直接依賴，沒有傳遞依賴：

```
你的 app
  ├── flask 3.0.0           ← 直接依賴（requirements.txt 裡有）
  └── requests 2.31.0       ← 直接依賴

  （以下在爛的 SBOM 裡看不到）
  ├── werkzeug 3.0.1        ← flask 的傳遞依賴
  ├── jinja2 3.1.2          ← flask 的傳遞依賴
  ├── certifi 2023.11.17    ← requests 的傳遞依賴
  └── ... (其他 8 個)
```

如果 werkzeug 有 CVE，你的 SBOM 說「我沒有 werkzeug」，你就沒辦法被通知。Log4Shell 危機就是這樣——很多 SBOM 裡沒有 log4j，因為它是第三方框架的傳遞依賴，不在直接依賴清單裡。

### 問題 2：缺乏識別符（Missing Identifiers）

Package 有名稱和版本，但沒有 PURL（Package URL）或 CPE（Common Platform Enumeration）：

```json
{
  "name": "requests",
  "version": "2.31.0"
  // 沒有 purl，沒有 cpe
}
```

grype 掃漏洞靠 PURL 比對。沒有 `pkg:pypi/requests@2.31.0` 這個 PURL，grype 不確定「requests」是 Python 的那個 requests 還是別的生態同名 package，比對率大降。NVD/CPE 資料庫用 CPE 索引，沒有 CPE 的 package 比對 NVD 命中率低。

### 問題 3：版本不精確（Imprecise Version）

```
github.com/google/uuid  UNKNOWN   go-module   ← 這行沒用
werkzeug               >=2.0      python       ← 範圍不是版本
```

`UNKNOWN` 版本的 component，你無法比對任何 CVE——漏洞資料庫靠版本範圍匹配，沒有版本等於永遠比不到。範圍版本（`>=2.0`）也一樣，不知道實際裝的是 2.0 還是 3.1，漏洞比對結果不確定。

### 問題 4：缺少 checksum / hash（Missing Integrity）

SBOM 記錄了「用了 requests 2.31.0」，但沒有記錄這個 requests 的 SHA256 hash。問題是：「requests 2.31.0」這個版本號指向的 wheel file 有沒有被篡改過？沒有 hash 就無從驗證。這在供應鏈攻擊的場景下是嚴重缺失（Ch 19 會深入）。

### 問題 5：幽靈元件（Ghost / Phantom Components）

這個問題比較隱微：SBOM 裡列了一個 package，但它實際上沒有被用到。

兩種常見原因：

1. **Source-based 掃描列了 `requirements.txt` 裡的所有東西**，但某些 package 在 build 時被優化掉或根本沒有被 import
2. **Shaded JAR 的反面**：JAR 包含了某個 class 路徑，生成工具把它計算成一個 package，但它只是個 utility class，不是真正的第三方依賴

幽靈元件的問題是製造假陽性漏洞警報——你的掃描工具說「你有 X 的 CVE」，但 X 根本沒有被用到。

### 問題 6：已知未知（Known Unknowns）

工具知道自己沒有完整掃到，但 SBOM 沒有明說這件事。CycloneDX 的 `compositions` 欄位和 SPDX 3.0 的 `completeness` 欄位就是設計來聲明「我這份 SBOM 只有部分完整」——例如「OS 套件部分完整，language package 部分不完整」。

不聲明這件事的 SBOM，消費者會以為它是完整的，帶來虛假的安全感。

---

## SBOM 品質評分：sbomqs 2.0

[sbomqs](https://github.com/interlynk-io/sbomqs) 是 Interlynk 開發的開源 SBOM 品質評分工具，能量化上面這些品質維度，並對照 NTIA、BSI、OpenChain 等標準。

安裝：

```bash
# 下載最新 release（以 v2.0.12 為例）
curl -sL https://github.com/interlynk-io/sbomqs/releases/download/v2.0.12/sbomqs_2.0.12_amd64.deb \
  -o /tmp/sbomqs.deb
sudo dpkg -i /tmp/sbomqs.deb
sbomqs version
```

真實輸出：

```
  ____    ____     ___    __  __    ___    ____
 / ___|  | __ )   / _ \  |  \/  |  / _ \  / ___|
...
sbomqs: sbomqs application provides sbom quality scores.

GitVersion:    v2.0.12
GitCommit:     3d2003760a2d6457cd07e63bf30b0fdd87b48e08
BuildDate:     2026-08-13T21:04:55Z
```

---

## 真實示範：兩份 SBOM 的品質對比

我們用兩份真實生成的 SBOM 來對比：

**SBOM A（淺層）**：只掃 `flask==3.0.0 / requests==2.31.0`（2 個直接依賴）

```bash
mkdir -p /tmp/sbom-demo/shallow
echo -e "flask==3.0.0\nrequests==2.31.0" > /tmp/sbom-demo/shallow/requirements.txt
syft scan dir:/tmp/sbom-demo/shallow -o spdx-json > /tmp/sbom-demo/sbom-shallow.spdx.json
jq ".packages | length" /tmp/sbom-demo/sbom-shallow.spdx.json
```

輸出：`3`（2 個 python package + 1 個描述掃描目錄的 root package）

**SBOM B（深層）**：掃完整 lockfile（12 個包含傳遞依賴的套件）

```bash
syft scan dir:/tmp/sbom-demo/pyapp -o spdx-json > /tmp/sbom-demo/sbom-deep.spdx.json
jq ".packages | length" /tmp/sbom-demo/sbom-deep.spdx.json
```

輸出：`13`

### sbomqs 評分對比

```bash
sbomqs score /tmp/sbom-demo/sbom-shallow.spdx.json
```

真實輸出（節錄關鍵部分）：

```
SBOM Quality Score: 5.3/10.0   Grade: D   Components: 5   EngineVersion: 8

Industry Profile Overviews:
+--------------------------------+----------+-------+
|            PROFILE             |  SCORE   | GRADE |
+--------------------------------+----------+-------+
| NTIA Minimum Elements (2021)   | 8.0/10.0 | B     |
| NTIA Minimum Elements (2025)   | 7.5/10.0 | C     |
| BSI TR-03183-2 v1.1            | 4.8/10.0 | F     |
+--------------------------------+----------+-------+

Category Breakdown:
+-------------------+--------+-----------+-------+
|     CATEGORY      | WEIGHT |   SCORE   | GRADE |
+-------------------+--------+-----------+-------+
| Integrity         | 18.3%  | 0.0/10.0  | F     |  ← 沒有 hash
| Licensing         | 18.3%  | 0.8/10.0  | F     |  ← 沒有授權資訊
| Completeness      | 14.6%  | 6.5/10.0  | D     |
| Vulnerability     | 12.2%  | 8.0/10.0  | B     |  ← PURL 存在
| Identification    | 12.2%  | 9.3/10.0  | A     |
| Structural        | 9.8%   | 10.0/10.0 | A     |
+-------------------+--------+-----------+-------+
```

```bash
sbomqs score /tmp/sbom-demo/sbom-deep.spdx.json
```

真實輸出（節錄）：

```
SBOM Quality Score: 5.5/10.0   Grade: D   Components: 13   EngineVersion: 8

Category Breakdown:
+-------------------+--------+-----------+-------+
|     CATEGORY      | WEIGHT |   SCORE   | GRADE |
+-------------------+--------+-----------+-------+
| Integrity         | 18.3%  | 0.0/10.0  | F     |  ← 同樣沒有 hash
| Licensing         | 18.3%  | 0.8/10.0  | F     |  ← 同樣沒有授權
| Vulnerability     | 12.2%  | 9.2/10.0  | A     |  ← 更多 PURL
| Identification    | 12.2%  | 9.7/10.0  | A     |
+-------------------+--------+-----------+-------+
```

### 讀懂評分結果

兩份 SBOM 的整體分數都在 D 附近（5.3 vs 5.5），差距不大。為什麼？

因為**兩份 SBOM 有共同的結構性問題**：
- `Integrity = 0.0/10.0`：syft 從 requirements.txt 生成的 SBOM 沒有 component 的 hash（沒有下載過 wheel file，所以沒有 checksum 可記）
- `Licensing = 0.8/10.0`：requirements.txt 沒有授權資訊，syft 不查 PyPI API，所以授權欄位是空的

這兩個問題是**工具的局限，不是輸入的問題**——換工具（cyclonedx-py 掃已安裝的環境）或換掃描對象（掃 image 裡的 .dist-info）才能解決。

深淺兩份 SBOM 的**真正差距在哪裡**？

```
Vulnerability score:  淺 = 8.0 / 深 = 9.2
```

深層 SBOM 的 Vulnerability score 更高，因為更多 package 有 PURL（去比對 CVE 的能力更強）。但如果 werkzeug 有 CVE，淺層 SBOM 根本沒有 werkzeug 這個 package，不是分數低，是**根本找不到**——這個差異不在分數裡體現，在實際的 CVE 掃描結果裡體現。

---

## 底層機制：NTIA Minimum Elements

NTIA（美國國家電信暨資訊局）在 2021 年定義了 [SBOM 的最低必要元素](https://www.ntia.doc.gov/report/2021/minimum-elements-software-bill-materials)：

```
每個 component 必須有：
  1. 供應商名稱 (Supplier Name)
  2. 元件名稱 (Component Name)
  3. 元件版本 (Version of the Component)
  4. 其他唯一識別符 (Other Unique Identifiers)  ← PURL/CPE
  5. 依賴關係 (Dependency Relationship)          ← 誰依賴誰
  6. SBOM 作者 (Author of SBOM Data)
  7. 時間戳記 (Timestamp)
```

sbomqs 的 NTIA profile 就是對照這 7 條評分。我們兩份 SBOM 的 NTIA 2021 分數都在 B 附近——因為 syft 能填上大部分欄位，只有供應商名稱（Supplier Name）欄位在 SPDX 格式裡不被直接支援（SPDX 用不同方式處理），導致扣分。

**NTIA Minimum Elements 是合規的底線，不是品質的上限**。拿到 10/10 的 NTIA 分數，你的 SBOM 可能仍然缺少 hash、缺少授權資訊、深度不夠（NTIA 的 7 條是最低門檻）。

---

## 如何一眼看出一份 SBOM 靠不靠譜

收到一份 SBOM（不管是自己生的還是廠商給的），快速檢查清單：

**第一眼（1 分鐘）**：

```bash
# 有幾個 package？
jq ".packages | length" sbom.spdx.json
# 或 CycloneDX
jq ".components | length" sbom.cdx.json

# 有多少 package 有 PURL？
jq "[.packages[] | select(.externalRefs != null and
    (.externalRefs | map(select(.referenceType == \"purl\")) | length) > 0)] | length" sbom.spdx.json
```

**第二眼（PURL 覆蓋率）**：

如果 100 個 package 裡只有 20 個有 PURL，那 80% 的 package 在 grype/trivy 掃漏洞時幾乎看不到任何比對。

**第三眼（版本欄位）**：

```bash
# 有多少 package 版本是 UNKNOWN？
jq "[.packages[] | select(.versionInfo == \"UNKNOWN\")] | length" sbom.spdx.json
```

版本是 UNKNOWN 的 component，對漏洞掃描完全沒用。

**第四眼（生成工具和時間）**：

```bash
# 誰生成的？什麼時候？
jq ".creationInfo" sbom.spdx.json
```

如果 `created` 是六個月前，這份 SBOM 可能已經過時。如果 `creators` 只有 `Tool: unknown`，品質存疑。

**快速 sbomqs 評分**：

```bash
sbomqs score sbom.spdx.json 2>&1 | head -20
```

看 Overall score 和 Category breakdown，`Integrity F` 意味著沒有 hash，`Licensing F` 意味著授權欄位空白，`Vulnerability` 低意味著 PURL 覆蓋不足。

---

## 對比與取捨

| 品質維度 | 什麼生成方法能改善 | 最低需求標準 |
|---|---|---|
| 深度（傳遞依賴） | lockfile / build-time plugin | NTIA：有依賴關係記錄 |
| PURL / CPE | syft 通常能填；build-time plugin 更完整 | NTIA：其他唯一識別符 |
| 版本精確 | lockfile 來源；避免 manifest-only scan | NTIA：元件版本 |
| Hash/checksum | 需要安裝後掃描或 build-time recording | BSI TR-03183-2 要求 |
| 授權資訊 | 安裝後掃 .dist-info，或 build-time plugin | OpenChain、BSI 要求 |
| 關係圖（誰依賴誰） | build-time plugin（Maven/Gradle graph） | NTIA：依賴關係 |
| Known unknowns 聲明 | CycloneDX compositions / SPDX 3.0 completeness | 進階，非最低要求 |

---

## 踩雷集錦

**1. 「sbomqs 5.3 分是很差的分數，代表這份 SBOM 沒用」**

錯誤直覺：分數低 = 爛 = 丟掉重來。

正確認識：分數是**多維度的加權平均**。syft 從 requirements.txt 生成的 SBOM 在 Integrity（hash）和 Licensing 這兩個高權重維度得零分，拉低整體分數，但它的 Structural（格式正確）、Identification（PURL 存在）、Vulnerability（能比對 CVE）都不差。這份 SBOM 拿去掃漏洞仍然有用，只是有些進階功能（完整性驗證、授權 audit）無法支援。**看 category breakdown，不只看總分**。

**2. 「我的 SBOM 通過了 NTIA Minimum Elements，就算完整的 SBOM」**

錯誤直覺：滿足 NTIA 7 條 = 完整。

正確認識：NTIA 的 7 條是**最低門檻**，是 2021 年政策層面的要求，不是品質的上限。滿足 NTIA 的 SBOM 可能完全沒有 hash（Integrity 零分）、沒有授權資訊、傳遞依賴不完整。NTIA 分數 B 的 SBOM，在供應鏈安全實際操作上可能遠遠不夠。

**3. 「package 數量多代表 SBOM 品質好」**

錯誤直覺：300 個 package 的 SBOM 比 50 個的品質好。

正確認識：數量多不代表完整，也不代表準確。數量多可能代表：把 test 依賴也列進去（幽靈元件）、掃了多個 source 有重複、算進了 build tool 本身。真正的品質指標是**覆蓋率**（是否覆蓋所有 runtime 用到的依賴）和**準確性**（每個 component 的版本、識別符是否正確），不是絕對數量。

**4. 「廠商給的 SBOM 一定比自己生的準」**

錯誤直覺：廠商是軟體的作者，他們最清楚裡面有什麼。

正確認識：廠商給的 SBOM 品質差異極大。沒有標準規範廠商必須用什麼工具、達到什麼品質。實際看到的情況包括：一份手動維護的 Excel 轉成 SPDX（幾個月沒更新）、用了最簡單的 manifest-only scan 的結果（只有直接依賴）、甚至格式錯誤的 JSON。收到廠商 SBOM 第一件事：`sbomqs score` 它，驗證基本品質。

---

## 進階：再往深一層

### OWASP SCVS（Software Component Verification Standard）

[OWASP SCVS](https://owasp.org/www-project-software-component-verification-standard/) 把 SBOM 的驗證分成三個 Level：

- **Level 1**：SBOM 存在，格式有效，有基本的 component 識別符
- **Level 2**：加上完整性驗證（hash）、授權資訊、完整傳遞依賴
- **Level 3**：加上 SBOM 本身的簽章、來源證明（provenance）、與 CI/CD 的整合

大多數自動工具生成的 SBOM 大約在 Level 1.5 到 Level 2 之間。Level 3 需要 Part 5（sigstore、in-toto、SLSA）的完整實作。

### 改善 SBOM 品質的實際手段

| 問題 | 改善方法 |
|---|---|
| Integrity 分數為零 | 用 build-time plugin（它能讀 registry 上的 hash）；或加 `syft --file-metadata-digest` 選項 |
| Licensing 分數低 | 用 cyclonedx-py 掃已安裝環境（有 .dist-info/METADATA）；或用 `licensee` 工具後處理 |
| PURL 缺失 | syft 通常會填，但某些 cataloger 覆蓋不全；可以後處理補 PURL |
| 傳遞依賴不完整 | 確認有 lockfile（go.sum、package-lock.json、Cargo.lock、Poetry.lock）再掃 |
| 版本 UNKNOWN | 這通常代表你的 module 本身沒有版本（如本地 Go module）；在 build pipeline 裡注入版本號 |

### CycloneDX compositions：聲明已知未知

CycloneDX 的 `compositions` 欄位讓你明確聲明哪些部分是完整的、哪些不完整：

```json
{
  "compositions": [{
    "aggregate": "incomplete",
    "assemblies": ["comp-uuid-of-your-app"],
    "dependencies": ["dep-uuid-of-flask"]
  }]
}
```

`"aggregate": "incomplete"` 告訴消費者「我知道我沒有完整列出所有依賴」。相比沒有任何聲明（消費者誤以為完整），這是更誠實的 SBOM。

---

## 動手練習

1. 生成兩份品質不同的 SBOM 並用 sbomqs 評分：

   ```bash
   # 淺層：只有直接依賴
   mkdir -p /tmp/sbom-q-test/shallow
   echo -e "flask==3.0.0\nrequests==2.31.0" > /tmp/sbom-q-test/shallow/requirements.txt
   syft scan dir:/tmp/sbom-q-test/shallow -o spdx-json > /tmp/sbom-q-test/shallow.spdx.json

   # 深層：完整 lockfile
   syft scan dir:/tmp/sbom-demo/pyapp -o spdx-json > /tmp/sbom-q-test/deep.spdx.json

   # 比較
   sbomqs score /tmp/sbom-q-test/shallow.spdx.json 2>&1 | grep "SBOM Quality Score"
   sbomqs score /tmp/sbom-q-test/deep.spdx.json 2>&1 | grep "SBOM Quality Score"
   ```

   確認深層 SBOM 的 Vulnerability 分數比淺層高，且 components 數量差距（5 vs 13）。

2. 數出 PURL 覆蓋率（不只看分數，直接數）：

   ```bash
   # 總 package 數
   TOTAL=$(jq ".packages | length" /tmp/sbom-q-test/deep.spdx.json)
   # 有 PURL 的 package 數
   HAS_PURL=$(jq "[.packages[] | select(.externalRefs != null and
     (.externalRefs | map(select(.referenceType == \"purl\")) | length) > 0)] | length" \
     /tmp/sbom-q-test/deep.spdx.json)
   echo "Total: $TOTAL, Has PURL: $HAS_PURL, Coverage: $(echo "scale=0; $HAS_PURL*100/$TOTAL" | bc)%"
   ```

3. 確認 Integrity 為零的原因——沒有 hash：

   ```bash
   # 確認沒有 packageVerificationCode 或 checksums
   jq ".packages[] | {name: .name, checksums: .checksums}" /tmp/sbom-q-test/deep.spdx.json | head -30
   ```

   你應該看到所有 package 的 `"checksums": null` 或空陣列——因為 syft 從 requirements.txt 掃，沒有下載 wheel 所以沒有 hash。

---

## 本章重點整理

- SBOM 品質有多個正交維度：**深度（傳遞依賴）、識別符（PURL/CPE）、版本精確度、完整性（hash）、授權資訊、依賴關係圖**
- 常見的品質問題：只有直接依賴、缺 PURL/CPE、版本是 UNKNOWN、無 hash、無授權、幽靈元件
- **sbomqs** 能量化評分，對照 NTIA、BSI、OpenChain 等標準的 profile；安裝：`dpkg -i sbomqs_<ver>_amd64.deb`，使用：`sbomqs score <sbom-file>`
- 淺層 SBOM（2 package）vs 深層 SBOM（12 package）的品質差距：分數接近（5.3 vs 5.5），但 Vulnerability 掃描能力差距顯著（漏了 werkzeug，就看不到 werkzeug 的 CVE）
- **看 category breakdown 比看總分更重要**：Integrity F 代表沒有 hash，Licensing F 代表沒有授權資訊，各有不同的修復方向
- NTIA Minimum Elements（2021）是合規底線，不是品質上限；OWASP SCVS Level 3 才是完整的供應鏈安全 SBOM

## 自我檢核

- [ ] 我能說出至少 4 個 SBOM 品質維度，以及各維度爛掉對下游工作的影響
- [ ] 我知道 `sbomqs score` 輸出裡的哪幾個 Category 最關鍵，以及它們分別代表什麼
- [ ] 我能用 `jq` 從 SPDX JSON 裡算出 PURL 覆蓋率
- [ ] 我知道 NTIA Minimum Elements 的 7 條是什麼，以及為什麼它只是底線
- [ ] 我能解釋為什麼兩份整體分數接近的 SBOM，在實際漏洞掃描時可能表現差距極大

## 延伸閱讀

- **[sbomqs GitHub](https://github.com/interlynk-io/sbomqs)**（Interlynk）
  - **讀哪裡**：README 的 scoring criteria 說明，以及 `docs/` 目錄裡各 profile 的規則清單
  - **和本章的關聯**：直接對應本章的品質維度；看每個 feature 的計算邏輯，能理解「為什麼我的 SBOM 在 X 項失分」

- **[NTIA Minimum Elements for an SBOM](https://www.ntia.doc.gov/report/2021/minimum-elements-software-bill-materials)**（NTIA）
  - **讀哪裡**：整份文件不長（20 頁），特別是第 3 節「Minimum Elements for an SBOM Data Field」
  - **為什麼值得讀**：這是美國法規引用的基準，Ch 24 法規章會反覆參照；提早讀懂比到時候再啃容易

- **[OWASP SCVS](https://owasp.org/www-project-software-component-verification-standard/)**（OWASP）
  - **讀哪裡**：Level 1 ~ Level 3 的要求清單，特別是 L3 和 L2 的差距（簽章、provenance）
  - **和本章的關聯**：SCVS 是 Ch 18-23（信任 Part）的合規框架，本章讓你先在腦中定位 L1/L2 在哪

- **[CISA Known Unknowns in SBOM](https://www.cisa.gov/resources-tools/resources/software-bill-materials-sbom)**（CISA）
  - **讀哪裡**：CISA SBOM 頁面下的「Guidance on SBOM generation and consumption」文件
  - **為什麼值得讀**：CISA 官方承認 SBOM 生成有盲點，強調「聲明 completeness」比假裝完整更重要

Part 3 的四章到這裡結束。我們學了三種生成策略、syft 的 cataloger 機制、各語言的 build-time 工具、以及怎麼評估 SBOM 品質。

接下來 **練習 A** 把這些整合成一個動手任務：對同一個目標用三種方法生 SBOM，比對差異，找出誰漏了什麼。

→ [練習 A 三種來源 SBOM 比對](./practice-a-three-source-sbom.md)
