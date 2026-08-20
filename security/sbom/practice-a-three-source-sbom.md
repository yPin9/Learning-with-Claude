# 練習 A — 三種來源 SBOM 比對

> **目標**：對同一個 Python 應用用三種方法各生成一份 SBOM，比對三份的 component 集合，找出誰多了什麼、誰漏了什麼，理解每種方法的覆蓋邊界。

## 任務規格

### 輸入

一個 Python Flask app 的目錄，包含：
- `requirements-direct.txt`：只有直接依賴（2 個 package，模擬「開發者手寫的清單」）
- `requirements-full.txt`：完整 lockfile（12 個 package，含所有傳遞依賴，模擬 `pip freeze` 輸出）
- `app.py`：簡單的 Flask app（讓目錄看起來像真的 project）

### 任務

分別用以下三種方法生成 SBOM，輸出為 SPDX JSON 格式：

| 方法 | 說明 | 輸出檔名 |
|---|---|---|
| 方法 A | `syft scan dir:` 掃只有直接依賴的 `requirements-direct.txt` | `sbom-a-direct.spdx.json` |
| 方法 B | `syft scan dir:` 掃完整 lockfile `requirements-full.txt` | `sbom-b-full.spdx.json` |
| 方法 C | `syft scan dir:` 掃 Python 安裝後的 site-packages（或模擬）| `sbom-c-installed.spdx.json` |

然後用 `jq` 比對三份的差異。

### 驗收標準

- [ ] 三份 SBOM 都是有效的 SPDX 2.3 JSON 格式（`jq . <file>` 不報錯）
- [ ] 方法 A 的 Python package 數 = 2
- [ ] 方法 B 的 Python package 數 = 12
- [ ] 能列出方法 A 比方法 B 少了哪些 package（至少 9 個）
- [ ] 能用 `sbomqs score` 對三份評分，並解釋分數差異

---

## 期望輸出範例

方法 A 的 syft 輸出（table 格式）：

```
NAME      VERSION  TYPE
flask     3.0.0    python
requests  2.31.0   python
```

方法 B 的 syft 輸出（table 格式）：

```
NAME                VERSION     TYPE
blinker             1.7.0       python
certifi             2023.11.17  python
charset-normalizer  3.3.2       python
click               8.1.7       python
flask               3.0.0       python
idna                3.6         python
itsdangerous        2.1.2       python
jinja2              3.1.2       python
markupsafe          2.1.3       python
requests            2.31.0      python
urllib3             2.1.0       python
werkzeug            3.0.1       python
```

差異比對輸出（jq 計算）：

```
方法 A 有 2 個 Python package
方法 B 有 12 個 Python package
方法 B 比方法 A 多了 10 個 package：
  blinker 1.7.0
  certifi 2023.11.17
  charset-normalizer 3.3.2
  click 8.1.7
  idna 3.6
  itsdangerous 2.1.2
  jinja2 3.1.2
  markupsafe 2.1.3
  urllib3 2.1.0
  werkzeug 3.0.1
```

---

## 實作步驟建議

### Step 1：建立測試環境

```bash
# 建立工作目錄
mkdir -p /tmp/practice-a
cd /tmp/practice-a

# 方法 A 的 source：只有直接依賴
mkdir -p /tmp/practice-a/direct
cat > /tmp/practice-a/direct/requirements.txt << 'EOF'
flask==3.0.0
requests==2.31.0
EOF

# 一個假的 app.py（讓它看起來是真的 project）
cat > /tmp/practice-a/direct/app.py << 'EOF'
from flask import Flask
import requests

app = Flask(__name__)

@app.route("/")
def hello():
    r = requests.get("https://httpbin.org/get")
    return f"status: {r.status_code}"
EOF

echo "Direct deps directory created"
ls /tmp/practice-a/direct/
```

### Step 2：建立完整 lockfile

```bash
# 方法 B 的 source：完整 lockfile（pip freeze 等級）
mkdir -p /tmp/practice-a/full
cat > /tmp/practice-a/full/requirements.txt << 'EOF'
blinker==1.7.0
certifi==2023.11.17
charset-normalizer==3.3.2
click==8.1.7
flask==3.0.0
idna==3.6
itsdangerous==2.1.2
jinja2==3.1.2
markupsafe==2.1.3
requests==2.31.0
urllib3==2.1.0
werkzeug==3.0.1
EOF

echo "Full lockfile directory created"
wc -l /tmp/practice-a/full/requirements.txt
```

### Step 3：生成三份 SBOM

```bash
# 方法 A：掃只有直接依賴的目錄
syft scan dir:/tmp/practice-a/direct -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-a-direct.spdx.json
echo "SBOM A generated"

# 方法 B：掃完整 lockfile 的目錄
syft scan dir:/tmp/practice-a/full -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-b-full.spdx.json
echo "SBOM B generated"

# 方法 C：掃系統 Python 的 site-packages（若 Python 已安裝套件）
# 這代表「安裝後掃描」，能取得更豐富的 metadata
syft scan dir:/usr/lib/python3 -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-c-installed.spdx.json 2>&1 || \
  echo "No Python site-packages found, using pyapp directory instead"
# 如果 /usr/lib/python3 沒有 site-packages，改掃之前建好的 pyapp 目錄
syft scan dir:/tmp/sbom-demo/pyapp -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-c-installed.spdx.json
echo "SBOM C generated"
```

### Step 4：基本驗證

```bash
# 確認三份都是有效 JSON
for f in /tmp/practice-a/sbom-{a,b,c}*.spdx.json; do
  echo -n "$f: "
  jq . "$f" > /dev/null && echo "OK" || echo "INVALID JSON"
done

# 各自的 package 數量
echo "SBOM A (direct only) packages:"
jq "[.packages[] | select(.packageVersion != null and .packageVersion != \"\")] | length" \
  /tmp/practice-a/sbom-a-direct.spdx.json

echo "SBOM B (full lockfile) packages:"
jq "[.packages[] | select(.packageVersion != null and .packageVersion != \"\")] | length" \
  /tmp/practice-a/sbom-b-full.spdx.json

# Python-only packages
echo "SBOM A Python packages:"
jq "[.packages[] | select(.packageVersion != null)] | length" \
  /tmp/practice-a/sbom-a-direct.spdx.json

echo "SBOM B Python packages:"
jq "[.packages[] | select(.packageVersion != null)] | length" \
  /tmp/practice-a/sbom-b-full.spdx.json
```

### Step 5：差異比對

```bash
# 提取兩份 SBOM 的 package 名稱清單
A_PKGS=$(jq -r ".packages[] | select(.SPDXID != \"SPDXRef-DOCUMENT\") | .name" \
  /tmp/practice-a/sbom-a-direct.spdx.json | sort)
B_PKGS=$(jq -r ".packages[] | select(.SPDXID != \"SPDXRef-DOCUMENT\") | .name" \
  /tmp/practice-a/sbom-b-full.spdx.json | sort)

echo "=== 方法 B 比方法 A 多了哪些 package ==="
comm -13 <(echo "$A_PKGS") <(echo "$B_PKGS")

echo ""
echo "=== 方法 A 比方法 B 少了幾個 ==="
A_COUNT=$(echo "$A_PKGS" | wc -l)
B_COUNT=$(echo "$B_PKGS" | wc -l)
echo "A: $A_COUNT, B: $B_COUNT, 差距: $((B_COUNT - A_COUNT))"
```

### Step 6：品質評分

```bash
# 安裝 sbomqs（如果尚未安裝）
# curl -sL https://github.com/interlynk-io/sbomqs/releases/download/v2.0.12/sbomqs_2.0.12_amd64.deb \
#   -o /tmp/sbomqs.deb && sudo dpkg -i /tmp/sbomqs.deb

echo "=== SBOM A 品質評分 ==="
sbomqs score /tmp/practice-a/sbom-a-direct.spdx.json 2>&1 | head -15

echo ""
echo "=== SBOM B 品質評分 ==="
sbomqs score /tmp/practice-a/sbom-b-full.spdx.json 2>&1 | head -15
```

---

## 卡住了怎麼辦

**Q：syft 輸出有 `[WARN] no explicit name and version provided for`**

這是正常的。syft 掃目錄時找不到這個 project 本身的名稱和版本（沒有 `setup.py` 或 `pyproject.toml`），所以對 root package 輸出 UNKNOWN version。這個 WARN 不影響 Python package 的掃描結果，忽略它。

**Q：jq 過濾 Python package 的數量跑出來跟預期不一樣**

SPDX JSON 裡有一個 SPDX-DOCUMENT package（描述 SBOM 本身）和一個描述你掃的 directory 的 root package，這兩個都算進 `.packages` 陣列。所以 `jq ".packages | length"` 會比實際 Python package 多 1-2 個。用 `.primaryPackagePurpose` 或 `.name` 過濾掉 root package：

```bash
jq "[.packages[] | select(.primaryPackagePurpose == \"LIBRARY\" or
  (.primaryPackagePurpose == null and .name != \".\"))] | length" sbom.spdx.json
```

或直接用名稱比對：`select(.name | test("^(flask|requests|blinker|...)"))` 。

**Q：sbomqs 指令找不到**

sbomqs 安裝在 `/usr/bin/sbomqs`（dpkg 安裝位置），確認用完整路徑：

```bash
/usr/bin/sbomqs score /tmp/practice-a/sbom-a-direct.spdx.json
```

**Q：方法 C 不知道要掃什麼**

方法 C 的重點是「安裝後掃描」——掃 Python 真的把套件裝到的 `site-packages` 目錄，這裡有 `.dist-info/METADATA` 帶有授權資訊，是 requirements.txt 掃描取得不了的。如果你的 WSL 環境裡沒有安裝 flask，用 `/tmp/sbom-demo/pyapp` 目錄（前面章節建的）作為替代。如果想真的測，在 virtualenv 裡 `pip install flask requests` 然後掃 `venv/lib/python3.*/site-packages/`。

---

## 完整參考解答

**先做完再看，否則練習的學習效果大打折扣。**

<details>
<summary>點開參考指令與預期輸出</summary>

### 建立環境

```bash
# 建立目錄
mkdir -p /tmp/practice-a/{direct,full}

# 方法 A：direct deps only
cat > /tmp/practice-a/direct/requirements.txt << 'EOF'
flask==3.0.0
requests==2.31.0
EOF

# 方法 B：full lockfile
cat > /tmp/practice-a/full/requirements.txt << 'EOF'
blinker==1.7.0
certifi==2023.11.17
charset-normalizer==3.3.2
click==8.1.7
flask==3.0.0
idna==3.6
itsdangerous==2.1.2
jinja2==3.1.2
markupsafe==2.1.3
requests==2.31.0
urllib3==2.1.0
werkzeug==3.0.1
EOF
```

### 生成 SBOM

```bash
# 方法 A
syft scan dir:/tmp/practice-a/direct -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-a-direct.spdx.json

# 方法 B
syft scan dir:/tmp/practice-a/full -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-b-full.spdx.json

# 方法 C（用 pyapp 作為替代，代表「已安裝的環境」）
syft scan dir:/tmp/sbom-demo/pyapp -o spdx-json 2>/dev/null \
  > /tmp/practice-a/sbom-c-installed.spdx.json
```

### 預期 package 數量

```bash
# 方法 A：應該有 2 個 Python package + 1 root
jq ".packages | length" /tmp/practice-a/sbom-a-direct.spdx.json
# → 3

# 方法 B：應該有 12 個 Python package + 1 root
jq ".packages | length" /tmp/practice-a/sbom-b-full.spdx.json
# → 13

# 方法 C：與方法 B 相同（同樣是 requirements.txt 來源）
jq ".packages | length" /tmp/practice-a/sbom-c-installed.spdx.json
# → 13
```

### 差異比對

```bash
# 方法 B 比方法 A 多了哪些 package
comm -13 \
  <(jq -r ".packages[].name" /tmp/practice-a/sbom-a-direct.spdx.json | sort) \
  <(jq -r ".packages[].name" /tmp/practice-a/sbom-b-full.spdx.json | sort)
```

預期輸出：

```
blinker
certifi
charset-normalizer
click
idna
itsdangerous
jinja2
markupsafe
urllib3
werkzeug
```

這 10 個都是 flask 和 requests 的傳遞依賴，但不在 direct 的 requirements.txt 裡。

### PURL 對比

```bash
# 方法 A 有多少 package 有 PURL
jq "[.packages[] |
  select(.externalRefs != null and
         (.externalRefs | map(select(.referenceType == \"purl\")) | length) > 0)
] | length" /tmp/practice-a/sbom-a-direct.spdx.json
# → 2（flask 和 requests 都有 PURL）

# 方法 B
jq "[.packages[] |
  select(.externalRefs != null and
         (.externalRefs | map(select(.referenceType == \"purl\")) | length) > 0)
] | length" /tmp/practice-a/sbom-b-full.spdx.json
# → 12（所有 Python package 都有 PURL）
```

### sbomqs 評分對比

```bash
# 方法 A 評分（節錄）
/usr/bin/sbomqs score /tmp/practice-a/sbom-a-direct.spdx.json 2>&1 | \
  grep -E "(SBOM Quality|Vulnerability|Completeness|Integrity)"
```

預期：

```
SBOM Quality Score: 5.3/10.0   Grade: D
│ Integrity         │ 0.0/10.0  │ F  │  ← 沒有 hash
│ Completeness      │ 6.5/10.0  │ D  │
│ Vulnerability     │ 8.0/10.0  │ B  │  ← 有 PURL，能比對 CVE
```

```bash
/usr/bin/sbomqs score /tmp/practice-a/sbom-b-full.spdx.json 2>&1 | \
  grep -E "(SBOM Quality|Vulnerability|Completeness|Integrity)"
```

預期：

```
SBOM Quality Score: 5.5/10.0   Grade: D
│ Integrity         │ 0.0/10.0  │ F  │  ← 仍然沒有 hash（同樣問題）
│ Completeness      │ 6.6/10.0  │ D  │
│ Vulnerability     │ 9.2/10.0  │ A  │  ← 更多 package 有 PURL
```

兩份 SBOM 的 Integrity 都是 F——不是因為我們做錯了，而是因為 syft 掃 requirements.txt 沒有下載 wheel 檔，所以沒有 hash 可記。**這是工具的已知局限，不是品質問題**。

要解決 Integrity 問題，需要掃已安裝的 `.dist-info`（會有 `RECORD` 檔的 hash）或用 build-time plugin（記錄從 registry 下載時的 hash）。

### 為什麼方法 A 的漏報很危險

假設明天 werkzeug 出了 CVE-2026-99999（高危）：

```bash
# 用方法 A 的 SBOM 掃漏洞（用 grype）
grype sbom:/tmp/practice-a/sbom-a-direct.spdx.json 2>/dev/null | grep werkzeug
# → 沒有輸出（werkzeug 不在 SBOM A 裡）

# 用方法 B 的 SBOM 掃漏洞
grype sbom:/tmp/practice-a/sbom-b-full.spdx.json 2>/dev/null | grep werkzeug
# → 如果 werkzeug 3.0.1 有 CVE，這裡會出現
```

方法 A 的使用者會認為「沒有 werkzeug CVE」，但實際上他們的 app 用了 werkzeug（flask 依賴它），且可能暴露在那個 CVE 下。**這就是為什麼 SBOM 的深度（傳遞依賴覆蓋率）決定漏洞掃描的有效性**。

</details>

---

## 測試與驗證表

完成後用這張表自我驗收：

| 測試項目 | 期望值 | 你的結果 | 通過？ |
|---|---|---|---|
| `jq . sbom-a-direct.spdx.json` 不報錯 | 有效 JSON | | |
| `jq . sbom-b-full.spdx.json` 不報錯 | 有效 JSON | | |
| SBOM A 的 Python package 數 | 2 | | |
| SBOM B 的 Python package 數 | 12 | | |
| SBOM B 比 SBOM A 多的 package 數 | 10 | | |
| SBOM A 有 PURL 的 package 數 | 2 | | |
| SBOM B 有 PURL 的 package 數 | 12 | | |
| sbomqs A 的 Integrity 分數 | 0.0/10.0 | | |
| sbomqs B 的 Vulnerability 分數高於 A | 是 | | |
| 能說出 werkzeug 為什麼在 A 看不到 | 口頭/書面 | | |

---

## 延伸挑戰

如果你覺得練習太輕鬆，試試這些：

**挑戰 1**：生成 CycloneDX 格式的 SBOM 並用 sbomqs 評分。比較同一份 source，SPDX vs CycloneDX 格式的評分差異，試著解釋差距的原因。

```bash
syft scan dir:/tmp/practice-a/full -o cyclonedx-json > sbom-b-full.cdx.json
sbomqs score sbom-b-full.cdx.json 2>&1 | head -20
```

**挑戰 2**：用 Go 的 go.mod + binary 做三源比對。建一個有外部依賴的 Go 程式，分別生成：
- Source SBOM（掃 go.mod）
- Binary SBOM（掃 compiled binary）
- 兩份的差異（`stdlib go1.x.y` 只在 binary SBOM 裡）

**挑戰 3**：驗證「SBOM 漏報等於 CVE 盲點」。用 grype 對方法 A 和方法 B 的 SBOM 掃漏洞（`grype sbom:sbom-a.spdx.json`），比對結果差異。如果 werkzeug 有 CVE，方法 A 應該看不到，方法 B 看得到。

**挑戰 4**：模擬「安裝後掃描 vs lockfile 掃描」的授權資訊差距。用 `cyclonedx-py` 掃一個真正安裝了 flask 的 virtualenv，然後比較 `"licenses"` 欄位是否比 syft 掃 requirements.txt 更豐富。

---

## 自我檢核

做完練習，你應該能回答：

- [ ] 為什麼方法 A 只看到 2 個 package，而 flask 在 runtime 實際上依賴 10+ 個 package？
- [ ] 如果 werkzeug 3.0.1 明天爆出 Critical CVE，方法 A 的使用者會不會被通知？為什麼？
- [ ] 為什麼兩份 SBOM 的 Integrity 都是 F？這是可以修的嗎？怎麼修？
- [ ] 「SBOM 的 package 數量多就代表品質好」這句話哪裡錯了？
- [ ] 方法 C（安裝後掃描）和方法 B（lockfile 掃描）在這個練習裡結果接近，那它們的差異在哪個情況下才會顯現？

Part 3 到這裡結束。你已經知道 SBOM 怎麼生、怎麼評、各種方法看到什麼看不到什麼。Part 4 進入消費端——有了 SBOM，怎麼把它轉化成具體的安全價值：漏洞比對、掃描實戰、VEX 降噪、持續監控。

→ [Ch 13 SBOM 怎麼變成價值：component → vulnerability](./13-sbom-to-value.md)
