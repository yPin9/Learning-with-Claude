# Ch 13 — SBOM 怎麼變成價值：component → vulnerability

> **目標**：理解 SBOM 從「靜態清單」變成「可操作安全情報」的完整路徑，掌握 grype 吃 SBOM 檔案（而非 image）的用法，以及這種「離線可重掃」機制帶來的核心價值。

## 為什麼需要這個？

一份 SBOM 本身什麼都不做。它只是個描述「這個軟體裡有哪些元件、版本是什麼」的清單——就像一張食材清單，你拿著它什麼也吃不到。

SBOM 的第一個真實價值，來自把這份清單送進漏洞資料庫查詢：「這些元件的這些版本，有沒有已知的安全漏洞？」

這個動作有個關鍵屬性，是 ad-hoc 掃描（直接掃 image 或目錄）做不到的：**SBOM 可以離線傳遞，事後重掃**。你今天把 image 的 SBOM 存下來，明天漏洞資料庫新增了 3 個 CVE，你拿同一份 SBOM 重掃，不需要那個 image 還在。掃描工具查的是資料庫，輸入只需要元件清單。

## 先建立直覺：三個角色，一條流水線

```
  元件清單（SBOM）          漏洞資料庫           可操作情報
  ┌───────────────┐       ┌──────────────┐     ┌─────────────────┐
  │ name: django  │       │ django 2.0.1 │     │ django 2.0.1    │
  │ ver: 2.0.1    │──────▶│ → CVE-2020-  │────▶│ Critical        │
  │ purl: pkg:pypi│  查詢  │   7471 Crit  │ 比中 │ SQL injection   │
  │  /django@2.0.1│       │ → CVE-2019-  │     │ Fix: 2.2.10     │
  └───────────────┘       │   11358 Med  │     └─────────────────┘
  ┌───────────────┐       │              │
  │ name: pillow  │       │ pillow 5.0.0 │     ┌─────────────────┐
  │ ver: 5.0.0    │──────▶│ → GHSA-j7hp- │────▶│ pillow 5.0.0    │
  │ purl: pkg:pypi│       │   h8jx-5ppr  │     │ High (kev)      │
  │  /pillow@5.0.0│       │   High       │     │ Fix: 10.0.1     │
  └───────────────┘       └──────────────┘     └─────────────────┘
         ▲                       ▲                      ▲
    syft 生成              grype/trivy              工程師決策
    (Ch 9-12)              比對邏輯               (修復/接受/VEX)
```

比對的關鍵是**元件識別（component identity）**：掃描工具需要把 SBOM 裡的 `(name, version)` 對應到漏洞資料庫裡的記錄。這個對應有兩種機制：

- **PURL**（Package URL）：`pkg:pypi/django@2.0.1`，精確指定生態系（pypi）、名稱（django）、版本（2.0.1）。GHSA、OSV 都用 PURL，比對精準。
- **CPE**（Common Platform Enumeration）：`cpe:2.3:a:djangoproject:django:2.0.1:...`，NVD 用的格式，比對邏輯複雜且容易錯配（Ch 14 深挖）。

syft 生成的 SBOM 會同時帶兩種，讓 grype 用最準的那個去比。

## 實戰：grype 吃 SBOM 檔案

先建立一個含有舊版套件的情境，生成 SBOM：

```bash
mkdir -p /tmp/vuln-demo && cd /tmp/vuln-demo
cat > requirements.txt << 'EOF'
flask==1.0.2
requests==2.18.0
django==2.0.1
cryptography==2.1.4
pyyaml==3.12
pillow==5.0.0
jinja2==2.10
werkzeug==0.14.1
sqlalchemy==1.2.0
EOF

syft /tmp/vuln-demo/requirements.txt -o spdx-json=sbom.spdx.json
```

syft 識別出 9 個套件（requirements.txt 本身 + 8 個 Python 套件），生成 SPDX JSON 格式的 SBOM：

```
$ jq '.packages | length' sbom.spdx.json
10
```

現在用 grype 吃這份 SBOM 檔案，而不是直接掃 requirements.txt：

```bash
# 注意：sbom: 前綴告訴 grype「這是一份 SBOM 檔案，不是 image 或目錄」
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | head -20
```

真實輸出（2026-08-17 跑，grype 0.117.0 + DB 2026-08-17）：

```
NAME          INSTALLED  FIXED IN  TYPE    VULNERABILITY        SEVERITY  EPSS          RISK
pillow        5.0.0      10.0.1    python  CVE-2023-4863        High      99.7% (99th)  85.6 (kev)
django        2.0.1      2.2.10    python  CVE-2020-7471        Critical  65.3% (99th)  60.6
django        2.0.1      2.1.9     python  CVE-2019-11358       Medium    87.2% (99th)  48.4
cryptography  2.1.4      39.0.1    python  CVE-2023-0286        High      59.5% (99th)  44.3
werkzeug      0.14.1     0.15.5    python  CVE-2019-14322       High      55.5% (98th)  41.6
django        2.0.1      2.2.9     python  CVE-2019-19844       Critical  35.1% (98th)  32.5
django        2.0.1      4.2.26    python  CVE-2025-64459       Critical  19.4% (97th)  17.6
requests      2.18.0     2.20.0    python  CVE-2018-18074       High      7.4% (93rd)   5.6
pyyaml        3.12       5.4       python  CVE-2020-14343       Critical  6.0% (92nd)   5.6
pyyaml        3.12       5.1       python  CVE-2017-18342       Critical  5.6% (92nd)   5.3
...
```

這份 requirements.txt 裡 9 個套件，掃出 **110 個漏洞**（Critical: 15、High: 53、Medium: 31、Low: 11）。這不是刷量——這些都是真實的已知漏洞，因為這些版本是 2017-2018 年的舊版本。

### `sbom:` 前綴的意義

```bash
# 直接掃目錄（grype 自己找 requirements.txt）
grype dir:/tmp/vuln-demo

# 吃 SBOM 檔案（不接觸原始來源）
grype sbom:sbom.spdx.json

# 也可以吃 CycloneDX
syft /tmp/vuln-demo/requirements.txt -o cyclonedx-json=sbom.cdx.json
grype sbom:sbom.cdx.json
```

這三種方式最終結果應該相同——但 `sbom:` 模式的差異在於：

1. **不需要原始 image 或目錄**：SBOM 是自包含的，可以跨環境傳遞
2. **掃描可重現**：同一份 SBOM + 同版本 grype + 同版本 DB → 保證得到相同結果
3. **可稽核**：你傳出去的 SBOM 和你掃出來的結果是有直接關聯的

## 底層機制：component → vulnerability 的對應

grype 拿到 SBOM 後，內部的比對流程：

```
SBOM 裡的 package
  name: "django"
  version: "2.0.1"
  externalRefs:
    - type: PURL: pkg:pypi/django@2.0.1
    - type: CPE:  cpe:2.3:a:django:django:2.0.1:...

         │
         │  grype 按優先序嘗試每種 matcher
         ▼
┌─────────────────────────────────────────────────────────┐
│  1. python-package matcher（PURL pypi 生態）             │
│     → 查 grype DB 的 python advisory 表                 │
│     → 找到 django 的 GHSA advisory（FIXED IN 版本範圍）  │
│     → 2.0.1 在 "< 2.2.10" 的範圍內 → 命中              │
│                                                         │
│  2. CPE matcher（備用，NVD 資料）                         │
│     → 把 package 的所有 CPE 拿去比 NVD 的 CPE 欄位      │
│     → 比中 → 補充 NVD 的 CVE ID                         │
└─────────────────────────────────────────────────────────┘
         │
         ▼
  輸出：name / installed / fixedIn / vulnerability / severity
```

PURL matcher 比 CPE matcher 準確得多，因為 PURL 精確指定了生態系（`pypi`）和名稱，不存在 CPE 的 vendor 欄位歧義問題（Ch 14 會深入）。

## 讀懂 grype 的輸出欄位

拿到這份 110 個漏洞的清單，怎麼讀？幾個關鍵欄位：

```
NAME          INSTALLED  FIXED IN  TYPE    VULNERABILITY     SEVERITY  EPSS          RISK
pillow        5.0.0      10.0.1    python  CVE-2023-4863     High      99.7% (99th)  85.6 (kev)
django        2.0.1      2.2.10    python  CVE-2020-7471     Critical  65.3% (99th)  60.6
pyyaml        3.12       5.1       python  CVE-2017-18342    Critical  5.6% (92nd)   5.3
```

- **FIXED IN**：第一個修好這個漏洞的版本。`10.0.1` 代表你要升到這個版本才完全沒事（也可以接受中間的修補版本，但要看 advisory 說明）。**`FIXED IN` 為空代表上游還沒修好**——你現在能做的只有暫時緩解或接受風險，升版本不是選項。
- **EPSS**：0–100% 的機率值，代表這個漏洞在未來 30 天內被野外利用的預測機率（FIRST 組織計算）。`99.7% (99th)` 代表這個漏洞比 99.7% 的 CVE 更可能被利用。CVSS 10.0 但 EPSS 1% 的漏洞，現實威脅可能遠低於 CVSS 7.0 但 EPSS 90% 的漏洞。
- **RISK**：grype 內部的加權分數，結合 CVSS + EPSS + KEV 標記計算。
- **(kev)**：`CVE-2023-4863` 後面的 `(kev)` 代表它在 CISA 的 **Known Exploited Vulnerabilities** 清單中——真實世界已有攻擊者利用這個漏洞的記錄。KEV 的優先度應高於純 CVSS 分數。

**優先順序排法**：不要從上到下一個一個處理 110 個漏洞。合理的優先順序：

1. `(kev)` 標記的漏洞，不管 severity 幾分，優先處理
2. EPSS 90th percentile 以上的 High/Critical（最可能被利用）
3. `FIXED IN` 有值的 Critical/High（有修復路徑）
4. `FIXED IN` 空白的先記錄，等上游修好再說

這就是「把 110 個漏洞變成 3 個本週必做」的實際工作流程。

## 「明天再掃可能多幾個 CVE」：DB 更新的時間維度

這是 SBOM 比 ad-hoc 掃描更強大的地方，也是一個很容易被忽略的關鍵屬性：

```
今天（2026-08-17）掃的結果：
  sbom.spdx.json → 110 個 CVE

明天漏洞資料庫更新了：
  新的 CVE 被披露，影響 werkzeug < 3.1.6
  新的 GHSA 被加進來

明天（2026-08-18）拿同一份 SBOM 重掃：
  sbom.spdx.json → 113 個 CVE（增加 3 個）
```

這個屬性代表：**SBOM 的生成時間和掃描時間可以分離**。你可以在 build time 生成 SBOM，把 SBOM 存入 artifact store；之後每天、每週定期拿這些 SBOM 重掃最新的漏洞 DB，不需要保留原始 image。這正是 Dependency-Track（Ch 17）做的事。

反過來說，「我三個月前掃了沒事」是沒意義的。SBOM 讓「持續掃描」這件事的基礎設施成本變低：你只要保存 SBOM 檔案（KB 等級），不需要保存整個 image（GB 等級）。

## 對比與取捨：grype 兩種輸入模式

| 面向 | `grype dir:<path>` / `grype <image>` | `grype sbom:<file>` |
|---|---|---|
| 需要原始來源 | 是 | 否（SBOM 自包含） |
| 適用場景 | 開發環境、CI 第一次掃 | 離線、事後稽核、定期重掃 |
| SBOM 可稽核 | 否（臨時生成） | 是（SBOM 可簽章） |
| 跨環境可重現 | 不一定（syft 版本差異） | 是（SBOM 固定） |
| 空間需求 | image 或 source（大） | 只需 SBOM 檔（KB） |
| 適合 air-gapped | 否（需 image） | 是（只需 SBOM + DB） |

結論：開發環境用直接掃方便；**生產環境應該在 build time 生成並保存 SBOM，之後永遠用 `sbom:` 模式重掃**。這才是 SBOM 的設計意圖。

## 踩雷集錦

1. **SBOM 生成時間 ≠ 安全狀態**：一份 SBOM 代表「那一刻的元件清單」，不代表「那一刻沒有漏洞」。這是很多人對 SBOM 最大的誤解。SBOM 本身不做安全判斷，判斷是後來掃描工具做的。
2. **`grype sbom:file.json` 和 `grype file.json` 差很多**：沒有 `sbom:` 前綴，grype 會把 JSON 當不認識的格式報錯，或嘗試用 image 模式讀。前綴是明確告訴 grype「這是 SBOM，用 SBOM matcher」。
3. **SBOM 格式混用**：syft 生成的 SPDX JSON 和 CycloneDX JSON 裡，package 的欄位名稱不同（SPDX 用 `versionInfo`，CycloneDX 用 `version`）。grype 都認得，但如果你自己寫 jq 腳本解析，要注意格式差異。
4. **grype 第一次跑慢是正常的**：它在下載漏洞 DB（~300 MB），快取在 `~/.cache/grype/`。之後快取命中就很快了。
5. **EPSS 欄位和 `(kev)` 標記不是 severity**：`EPSS` 是該漏洞在 30 天內被利用的機率（FIRST 組織算出的），`kev` 代表這個漏洞已在 CISA 的 Known Exploited Vulnerabilities 清單中（有真實利用案例）。CVE-2023-4863（pillow 那行）EPSS 99.7%、kev 標記，代表「幾乎必被利用且已有真實攻擊」，這比 severity 本身更重要。

## 進階：再往深一層

### JSON 輸出與機器可讀

grype 支援 `-o json` 輸出結構化結果，適合 CI 腳本解析：

```bash
grype sbom:sbom.spdx.json -o json 2>/dev/null | \
  jq '[.matches[] | select(.vulnerability.severity=="Critical") |
       {pkg:.artifact.name, ver:.artifact.version,
        vuln:.vulnerability.id, fix:(.vulnerability.fix.versions[0])}]'
```

輸出（節錄）：
```json
[
  {"pkg":"django","ver":"2.0.1","vuln":"GHSA-hmr4-m2h5-33qx","fix":"2.2.10"},
  {"pkg":"pyyaml","ver":"3.12","vuln":"GHSA-8q59-q68h-6hv4","fix":"5.4"},
  ...
]
```

### SARIF：接進 GitHub Security 的標準格式

```bash
grype sbom:sbom.spdx.json -o sarif > results.sarif
# 上傳給 GitHub Code Scanning（gh workflow 用）
```

SARIF（Static Analysis Results Interchange Format）是 GitHub、Azure DevOps 等 CI 平台的標準輸入格式，讓你的掃描結果直接顯示在 PR 的 Security 頁面。

### `--fail-on` 做 CI gating

```bash
# 有 High 以上漏洞就讓 CI 失敗（exit code 非 0）
grype sbom:sbom.spdx.json --fail-on high
```

實際測試：grype 發現 High 以上漏洞時會印錯誤訊息並以非零 exit code 退出，讓 CI pipeline 停下來。這是「CI gating」的核心——在 deploy 之前強制阻擋有已知高危漏洞的版本。Ch 15 會深入討論合理的 gating 策略（不是所有 High 都要擋）。

## 動手練習

1. 用你自己的任何一個有 `requirements.txt` 或 `package.json` 的專案，跑 `syft <path> -o spdx-json=my.sbom.json`，然後 `grype sbom:my.sbom.json`，看看掃出幾個 CVE。
2. 用 `grype sbom:sbom.spdx.json -o json 2>/dev/null | jq '[.matches[] | .vulnerability.severity] | group_by(.) | map({sev:.[0], count:length})'` 統計每個 severity 的數量。
3. 把 requirements.txt 裡的 `django==2.0.1` 改成 `django==4.2.26`，重新生成 SBOM 重掃，觀察 django 的 CVE 數量有什麼變化。

## 本章重點整理

- SBOM 的價值來自「拿元件清單去比對漏洞資料庫」，光有清單沒有比對，SBOM 就只是文件。
- `grype sbom:<file>` 讓掃描在不需要原始 image 的情況下進行——這是 SBOM 可離線傳遞、事後稽核、定期重掃的技術基礎。
- 比對的關鍵是 PURL 和 CPE，PURL 更精準（生態系原生），CPE 是 NVD 的格式（Ch 14 深挖它的問題）。
- 同一份 SBOM 今天掃和明天掃可能結果不同——因為漏洞 DB 每天更新。SBOM 讓「持續監控」成為可能。

## 自我檢核

- [ ] 我能解釋「grype 吃 SBOM 檔案」和「grype 直接掃 image」在實際操作上的差別
- [ ] 我知道 `sbom:` 前綴的作用，以及沒加它會發生什麼
- [ ] 我能解釋為什麼同一份 SBOM 今天和明天掃出的 CVE 數可能不同
- [ ] 我跑了動手練習 1，看到了自己專案的 CVE 清單

## 延伸閱讀

- **[grype README — SBOM Scanning](https://github.com/anchore/grype#supported-sources)** — 列出 grype 支援的輸入來源（image / dir / sbom / archive 等），讀「Supported Sources」那節了解 `sbom:` 前綴的完整語法
- **[CISA VEX Use Cases](https://www.cisa.gov/resources-tools/resources/vex-use-cases-and-sharing-guidance)** — 說明 SBOM + VEX 的配合方式，很具體地描述「為什麼光有漏洞清單不夠，還需要可利用性評估」
- **[FIRST EPSS](https://www.first.org/epss/)** — 說明 EPSS（Exploit Prediction Scoring System）的方法論；了解這個分數能讓你更合理地排優先順序，而不是一律看 CVSS severity
- **[Anchore: Scanning SBOM with Grype](https://anchore.com/sbom/)** — 工具作者的實務部落格，含 CI 整合範例

---

SBOM 讓你知道「有沒有」。下一章我們往漏洞資料庫本身更深挖：那些 CVE 從哪來、NVD 的 CPE 比對為什麼會出錯、OSV 為什麼出現、以及「不同工具掃同一份 SBOM 結果不一樣」這件讓人抓狂的事背後的根本原因。

→ [Ch 14 漏洞資料庫：NVD/CVE/CPE 的痛與 OSV](./14-vulnerability-databases.md)
