# 練習 B — VEX 降噪 + Dependency-Track 監控

> **目標**：對一份真實 SBOM 的漏洞清單做人工分析，寫 OpenVEX 抑制誤報/不可利用的發現，驗證掃描噪音下降；接著模擬把 SBOM 匯入 Dependency-Track 建立持續監控。串接 Ch 13–17 的完整工作流程。

## 任務規格

### 情境

你是一個 Python Web API 服務的安全負責人。這個服務的依賴清單（`requirements.txt`）如下：

```
flask==1.0.2
requests==2.18.0
django==2.0.1
cryptography==2.1.4
pyyaml==3.12
pillow==5.0.0
jinja2==2.10
werkzeug==0.14.1
sqlalchemy==1.2.0
```

這個服務的技術特性：
- **無狀態 REST API**：不使用 Flask session 機制（沒有 `flask.session`、沒有 cookie）
- **無 HTML 渲染**：不在後端渲染 HTML（Jinja2 只用在批次文字處理，不接受 user-controlled 模板）
- **只讀 YAML**：PyYAML 只用來讀取靜態設定檔（`yaml.safe_load()`），不解析任何外部輸入
- **不使用 SQLAlchemy ORM 的 text() raw query**：所有 query 透過 ORM 方法

### Task 1：生成 SBOM 並初次掃描

1. 建立 `requirements.txt`（內容如上）
2. 用 syft 生成 SPDX JSON 格式的 SBOM
3. 用 grype 以 `sbom:` 模式掃描，並記錄結果

**期望觀察**：
- 掃出超過 100 個漏洞
- Critical 和 High 的比例很高（舊版套件的代價）
- 有些漏洞根據服務特性評估是「不可利用」的

### Task 2：漏洞分析與 VEX 決策

對以下三個漏洞做判讀，決定 VEX 狀態和 justification（答案在參考解答，先自己判斷）：

| CVE | 套件 | Severity | 漏洞描述 |
|---|---|---|---|
| CVE-2019-11358 | django 2.0.1 | Medium | jQuery.extend() prototype pollution（前端 JS 問題） |
| CVE-2023-30861 | flask 1.0.2 | High | Session cookie 沒有設 SameSite=None |
| CVE-2017-18342 | pyyaml 3.12 | Critical | `yaml.load()` 不安全反序列化（RCE） |

判斷標準：根據服務技術特性（無狀態/無 HTML/只讀設定檔 YAML），哪些 CVE 在你的服務裡是 `not_affected`？哪些是真實風險需要標 `affected`？哪些還不確定要標 `under_investigation`？

### Task 3：寫 OpenVEX 文件並驗證

根據 Task 2 的判斷，寫一份 OpenVEX JSON 文件（`my-service.vex.json`），然後：

1. 用 `grype sbom:sbom.spdx.json --vex my-service.vex.json` 重掃
2. 驗證 `not_affected` 的 CVE 從結果裡消失了
3. 記錄前後的漏洞計數差異

**期望輸出格式**：

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://example.com/vex/my-service/v1.0",
  "author": "Your Name",
  "timestamp": "2026-08-17T00:00:00Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": {"name": "CVE-XXXX-XXXX"},
      "products": [{"@id": "pkg:pypi/PACKAGE@VERSION"}],
      "status": "not_affected",
      "justification": "JUSTIFICATION_VALUE",
      "impact_statement": "人類可讀的說明",
      "timestamp": "2026-08-17T00:00:00Z"
    }
  ]
}
```

### Task 4：Dependency-Track 監控（有 Docker 環境的延伸）

**如果你有能運行 docker compose 的環境**：

1. 起 Dependency-Track：`curl -LO https://dependencytrack.org/docker-compose.yml && docker compose up -d`
2. 登入 `http://localhost:8080`（admin/admin，第一次登入改密碼）
3. 建立 API Key（Administration → Teams → 建立 team → + API Key）
4. 上傳 SBOM：

```bash
curl -s -X "POST" "http://localhost:8081/api/v1/bom" \
  -H "X-Api-Key: YOUR_API_KEY" \
  -F "projectName=python-api-service" \
  -F "projectVersion=1.0.0" \
  -F "autoCreate=true" \
  -F "bom=@sbom.cdx.json;type=application/json"
```

5. 等 5-10 分鐘讓 Dependency-Track 完成漏洞分析
6. 在 UI 確認漏洞清單出現

**如果沒有 Docker 環境**：做 Task 1-3 + Task 5（驗證表）即可，Task 4 標「未實測」記錄指令。

### Task 5：驗證表

完成後填寫這張表：

| 驗證項目 | 指令 | 期望結果 | 你的結果 |
|---|---|---|---|
| SBOM 生成 | `jq '.packages | length' sbom.spdx.json` | 10（含 root） | |
| 初次掃描總數 | `grype sbom:sbom.spdx.json ... \| grep -c python` | 110 | |
| VEX 後掃描總數 | `grype ... --vex my-service.vex.json \| grep -c python` | 少於 110 | |
| CVE-2019-11358 被抑制 | `... \| grep CVE-2019-11358` | 無輸出 | |
| CVE-2023-30861 被抑制 | `... \| grep CVE-2023-30861` | 無輸出 | |

---

## 實作步驟建議

### Step 1：建立環境

```bash
mkdir -p /tmp/practice-b && cd /tmp/practice-b

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
```

### Step 2：生成 SBOM

```bash
# SPDX JSON（給 grype 用）
syft /tmp/practice-b/requirements.txt -o spdx-json=sbom.spdx.json

# CycloneDX JSON（給 Dependency-Track 用）
syft /tmp/practice-b/requirements.txt -o cyclonedx-json=sbom.cdx.json

# 確認元件數
jq '.packages | length' sbom.spdx.json
```

### Step 3：初次掃描

```bash
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | tee initial-scan.txt
echo "Total findings: $(grep -c python initial-scan.txt)"
echo "Critical: $(grep Critical initial-scan.txt | wc -l)"
echo "High: $(grep High initial-scan.txt | wc -l)"
```

### Step 4：調查三個 CVE

查閱每個 CVE 的詳細說明（osv.dev 或 GHSA 頁面），對照服務的技術特性做判斷。

### Step 5：寫 VEX 並驗證

```bash
# 寫完 my-service.vex.json 後
grype sbom:sbom.spdx.json --by-cve --vex my-service.vex.json -o table 2>/dev/null | tee after-vex.txt
echo "After VEX: $(grep -c python after-vex.txt || echo 0) findings"
echo "Suppressed: $(( $(grep -c python initial-scan.txt) - $(grep -c python after-vex.txt || echo 0) ))"
```

---

## 卡住提示

- **CVE-2019-11358（jQuery prototype pollution）**：這個 CVE 影響的是 Django 打包的 jQuery（1.x）用於 Django Admin 前端。如果你的服務沒有啟用 Django Admin，jQuery 根本不會被載入。是哪個 justification？
- **CVE-2023-30861（Flask Session SameSite）**：Flask 的 session 功能依賴 `flask.session` 物件和 cookie。無狀態 API 不呼叫這些 API，執行路徑不會通過有漏洞的那段 cookie 設定程式碼。
- **CVE-2017-18342（PyYAML load() RCE）**：這個 CVE 的關鍵在於有沒有用 `yaml.load()`（不安全）還是 `yaml.safe_load()`。如果你的程式碼只用 `yaml.safe_load()`，有沒有漏洞？注意：`yaml.load()` 和 `yaml.safe_load()` 是不同函式，漏洞要透過 `load()` 才能觸發。如果確定不用 `load()`，justification 是什麼？
- **VEX PURL 大小寫**：`pkg:pypi/django@2.0.1` 注意套件名全小寫（PyPI 規範），版本號要完全一致（包括 `2.0.1` 不是 `2.0`）。

---

## 參考解答

**寫完再看**。先嘗試自己判斷，不確定就查 osv.dev 或 nvd.nist.gov 看 CVE 描述。

<details>
<summary>點開參考解答</summary>

### Task 2 的 VEX 決策判斷

**CVE-2019-11358（django 2.0.1，Medium）**：

這個 CVE 實際上是 Django 打包的 jQuery 1.x 的 prototype pollution 問題（`jQuery.extend`），影響的是 Django Admin 的前端 JavaScript。如果服務沒有啟用 Django Admin，或者完全是純 API 服務（沒有任何前端渲染），這個 CVE 在你的執行路徑裡根本不存在。

判斷：`not_affected`，justification：`vulnerable_code_not_in_execute_path`

**CVE-2023-30861（flask 1.0.2，High）**：

這個 CVE 是 Flask 在設定 session cookie 時沒有加 `SameSite=None` 屬性，讓攻擊者在某些情況下可以做 CSRF。但前提是你的應用使用了 Flask 的 `flask.session`（cookie-based session）。無狀態 REST API 不使用 session，呼叫路徑永遠不會到那段設 cookie 的程式碼。

判斷：`not_affected`，justification：`vulnerable_code_not_in_execute_path`

**CVE-2017-18342（pyyaml 3.12，Critical）**：

這個 CVE 影響的是 `yaml.load()` 函式（不安全），允許透過惡意 YAML 文件執行任意 Python 程式碼（RCE）。如果你的程式碼**只**使用 `yaml.safe_load()` 或 `yaml.load(stream, Loader=yaml.SafeLoader)`，則漏洞的觸發點（`yaml.load()`）從未被呼叫。

判斷：`not_affected`，justification：`vulnerable_code_not_in_execute_path`

**注意**：如果你不確定程式碼是否用了 `yaml.load()`（未拿到 source code 分析），這個應該標 `under_investigation` 而不是 `not_affected`。這個決策需要代碼審查支撐。

---

### Task 3 的完整 VEX 文件

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://example.com/vex/practice-b/v1.0",
  "author": "Security Team <security@example.com>",
  "timestamp": "2026-08-17T00:00:00Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": {
        "name": "CVE-2019-11358",
        "aliases": ["GHSA-6c3j-c64m-qhgq"]
      },
      "products": [{"@id": "pkg:pypi/django@2.0.1"}],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "This CVE affects Django Admin's bundled jQuery. Django Admin is not enabled in this REST API service, and no frontend HTML is served.",
      "timestamp": "2026-08-17T00:00:00Z"
    },
    {
      "vulnerability": {
        "name": "CVE-2023-30861",
        "aliases": ["GHSA-m2qf-hxjv-5gpq"]
      },
      "products": [{"@id": "pkg:pypi/flask@1.0.2"}],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "Flask session cookies are not used. This is a stateless REST API that does not call flask.session or set any cookies.",
      "timestamp": "2026-08-17T00:00:00Z"
    },
    {
      "vulnerability": {
        "name": "CVE-2017-18342",
        "aliases": ["GHSA-rprw-h62v-c2w7"]
      },
      "products": [{"@id": "pkg:pypi/pyyaml@3.12"}],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "Only yaml.safe_load() is used for config parsing. yaml.load() is never called in this codebase (verified by code review).",
      "timestamp": "2026-08-17T00:00:00Z"
    }
  ]
}
```

將上面的內容存成 `/tmp/practice-b/my-service.vex.json`

### 驗證指令（真跑輸出）

```bash
# 初次掃描
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | grep -c python
# 110

# 加 VEX 後
grype sbom:sbom.spdx.json --by-cve --vex my-service.vex.json -o table 2>/dev/null | grep -c python
# 107（抑制了 3 個漏洞）

# 確認三個 CVE 都消失了
grype sbom:sbom.spdx.json --by-cve --vex my-service.vex.json -o table 2>/dev/null | grep -E "CVE-2019-11358|CVE-2023-30861|CVE-2017-18342"
# （無輸出）
```

真實運行（2026-08-17 實測，grype 0.117.0）：
- 初次掃描：110 個漏洞
- 加 VEX 後：107 個漏洞
- 抑制 3 個：CVE-2019-11358、CVE-2023-30861、CVE-2017-18342 均從結果清單消失

### Task 4 的 Dependency-Track curl 指令（未實測，語法來自官方文件）

```bash
# 起服務
curl -LO https://dependencytrack.org/docker-compose.yml
docker compose up -d
# 等待約 2-5 分鐘初始化

# 建立 API Key（UI 操作，Administration → Teams → CI-Pipeline → + API Key）
export DTRACK_API_KEY="your-api-key-here"

# 上傳 SBOM
curl -s -X "POST" "http://localhost:8081/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -F "projectName=python-api-service" \
  -F "projectVersion=1.0.0" \
  -F "autoCreate=true" \
  -F "bom=@sbom.cdx.json;type=application/json"

# 查詢 project findings（先取得 project UUID）
PROJECT_UUID=$(curl -s "http://localhost:8081/api/v1/project?name=python-api-service" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" | jq -r '.[0].uuid')

curl -s "http://localhost:8081/api/v1/finding/project/${PROJECT_UUID}" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" | \
  jq '[.[] | {pkg:.component.name, ver:.component.version,
               vuln:.vulnerability.vulnId, sev:.vulnerability.severity}] |
      group_by(.sev) | map({sev:.[0].sev, count:length})'
```

</details>

---

## 驗證表（自評）

填完練習後，確認每一項：

| 項目 | 完成 |
|---|---|
| 用 `syft` 從 requirements.txt 生成 SBOM，`jq '.packages | length'` 輸出 10 | [ ] |
| `grype sbom:sbom.spdx.json` 初次掃描 ≥ 100 個漏洞 | [ ] |
| 閱讀三個指定 CVE 的描述，自己做了 not_affected / affected / under_investigation 的判斷 | [ ] |
| 寫出格式正確的 OpenVEX JSON（`@context`、`statements`、`justification` 五選一） | [ ] |
| `grype ... --vex my-service.vex.json` 後三個 CVE 均從清單消失 | [ ] |
| 加 VEX 前後的漏洞計數差異和你預計抑制的數量一致 | [ ] |

---

## 延伸挑戰

1. **多個 VEX 版本**：在 `my-service.vex.json` 裡把 CVE-2017-18342 的狀態改成 `under_investigation`（代表你還沒確認是否用了 `yaml.load()`），把 `version` 改成 2、更新 `timestamp`，重跑 grype 確認這個 CVE 又出現了（`under_investigation` 不抑制）。
2. **vexctl 生成**：用 `vexctl create` 指令生成 CVE-2019-11358 的 not_affected 聲明，比較輸出格式和你手寫的 JSON 有什麼差異。
3. **osv-scanner 的 VEX**：osv-scanner 不支援 `--vex` 旗標。查閱 [osv-scanner 的 ignore 機制](https://google.github.io/osv-scanner/configuration/)，找到它如何透過 `osv-scanner.toml` 或類似的方式做例外處理。

---

## 自我檢核

- [ ] 我做了獨立的漏洞判斷（不直接看參考解答），能對「為什麼這個 CVE not_affected」給出技術理由
- [ ] 我知道 justification 的五個合法值，並且這次用了合理的那個
- [ ] VEX 前後的計數差異是我預期的（手寫了幾個 not_affected，就消失幾個）
- [ ] 我理解為什麼把 CVE-2017-18342 改成 `under_investigation` 後它又回來了

---

Part 4 結束。你現在能從 SBOM 生成漏洞清單、理解各漏洞資料庫的差異、三工具並排比對、用 VEX 降噪、並建立持續監控的基礎。下一個 Part（Part 5）往信任的方向走：如何確認你的 SBOM 本身沒被篡改，以及如何證明這份 SBOM 是在正確的 build 流程裡由正確的工具產生的。

→ [Ch 18 供應鏈攻擊面全景](./18-supply-chain-attack-surface.md)
