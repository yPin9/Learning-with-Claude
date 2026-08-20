# Ch 25 — 企業導入 SBOM 計畫

> **目標**：把「我們有在跑 syft」和「我們有 SBOM 計畫」之間的差距說清楚。讀完這章，你知道一個能撐住生產環境的 SBOM 計畫需要哪些組件、誰負責什麼、阻力會在哪裡爆發，以及怎麼務實地設計才不會三個月後計畫靜悄悄地死掉。

## 為什麼需要這個？

大多數導入 SBOM 的企業都踩同一個坑：工具跑起來了，CI 也有產 SBOM，但六個月後沒有人在看它，沒有人知道存在哪裡，掃描報告堆在某個 S3 bucket 無人聞問。這不是技術問題，是計畫問題。

「有工具」不等於「有計畫」。工具只解決生成的問題；計畫要回答以下四個問題：

1. 誰的產品要產？什麼觸發要更新？
2. 產出的 SBOM 存在哪、保存多久、誰能查？
3. 掃描結果怎麼接到真正的決策（release 門禁）？
4. 供應商進來的 SBOM 誰負責消化？

這章從政策層開始，往下進到 CI 實作，再談門禁、供應商、組織分工，最後正面迎擊阻力。

## 先建立直覺

一個 SBOM 計畫的生命週期，不是線性的，是一個循環：

```
政策制定
    │
    ▼
CI 產 SBOM（build-time）
    │
    ▼
簽章 + 上傳 artifact
    │
    ▼
掃描 + VEX 標記
    │
    ▼
Release 門禁（通過才放行）
    │
    ▼
Dependency-Track 長期監控
    │         ▲
    │         │
    ▼         │
收供應商 SBOM → 匯入 → 關聯到自家產品
    │
    ▼
有新 CVE → 觸發告警 → 走 incident 流程
    │
    ▼
VEX 更新 / 修版本 / 接受風險（回到門禁）
```

這個循環中，任何一個環節斷掉，整個計畫就退化成「有在跑 syft 但沒人看結果」的狀態。

## SBOM Policy：計畫的骨架

政策文件不是為了好看，是為了讓不同團隊對「我們要做什麼」的預期一致。一份 SBOM policy 最少要回答六件事。

### 1. Scope：哪些產品要納入

不要一開始就說「所有軟體」——這是讓計畫死亡最快的方式，因為範圍太大導致優先順序混亂、人力不夠、工具錯誤堆積。

實際做法是分三個圈：

```
圈一：必須（Day 1 就要有）
  - 對外發布的產品（SaaS、可下載軟體、API 服務）
  - 賣給政府或需要合規的產品

圈二：應該（6 個月內）
  - 內部核心基礎設施（CI/CD 系統、身份服務）
  - 含個資的內部系統

圈三：最好有（1 年內）
  - 開發者內部工具
  - 短期用的腳本和自動化工具
```

把範圍寫進 policy，並標明時間表。沒有時間表的 scope 等於沒有 scope。

### 2. 格式與深度

格式選擇的務實標準：

| 情境 | 推薦格式 | 理由 |
|------|---------|------|
| 需要交給美國政府 / NTIA 相容 | SPDX 2.3 | NTIA 最初以 SPDX 為主要參考 |
| 需要 VEX 嵌入 / 安全導向 | CycloneDX 1.6 | VEX 原生支援、工具生態成熟 |
| 兩個都要 | CycloneDX 主格式，SPDX 轉換輸出 | syft 可以同時輸出兩種 |

深度的選擇比格式更重要，也更容易出錯：

- **僅直接依賴（direct only）**：產起來快，但對 Log4Shell 這類藏在傳遞依賴的漏洞沒有效果。基本上不推薦，除非你的系統真的沒有傳遞依賴。
- **傳遞依賴全部（transitive）**：這才是有意義的 SBOM。用 syft 預設就是這個行為。
- **含 dev dependency**：視情況。如果你的 CI 在 build 時會把 dev deps 也打進 image，那它們就必須進 SBOM。

Policy 要寫明：「傳遞依賴必須包含，dev dependency 在不進入最終 artifact 的情況下可以排除。」

### 3. 更新觸發條件

SBOM 是時間點的快照，過期了比沒有更危險（因為你以為你知道，但其實是舊的）。

觸發條件要在 policy 裡明確定義：

- **每次 release build**：必要條件，不容商量。
- **任何依賴版本變更**：在 CI 裡自動觸發，不需要手動判斷。
- **base image 更新**：`FROM alpine:3.19` 換成 `alpine:3.20` 就是觸發點。
- **定期重新掃描**（不更新 SBOM 本身，但要重新比對漏洞）：建議每週，因為漏洞資料庫每天都在更新，你的 SBOM 不動但新 CVE 可能剛進來。

### 4. 儲存策略

SBOM 的儲存要有三個屬性：**可查**、**可信**、**可追溯**。

```
每個 release artifact 對應一份 SBOM
    ├── 儲存位置：OCI registry（attach 到 image）或 artifact storage（S3/Nexus）
    ├── 命名格式：<product>-<version>-<git-sha>.sbom.cdx.json
    ├── 簽章：cosign sign-blob 或 cosign attest
    └── 保存期限：與 release 同壽命，最少 5 年（合規要求）
```

用 OCI registry 存 SBOM 的優點是天然和 image 綁在一起（用 cosign attest 把 SBOM 綁成 attestation；舊寫法 `cosign attach sbom` 已於 2024 廢棄，見 Ch 26），缺點是你要確保 registry 的 retention policy 不會把舊版砍掉。

保存期限不要寫「無限期」，那是推給後人的問題，但也不要寫「一年」，因為法規（FDA、EU CRA）的要求通常在 5–10 年。折衷：5 年或產品 EOL 後 2 年，取較長者。

## 在 CI 產 SBOM

最重要的原則：**SBOM 必須在 build-time 產，不能在 scan-time 產**。

Build-time SBOM 和 scan-time SBOM 的差距：

```
build-time SBOM（正確做法）：
  你 build 了什麼 → syft 在同一個步驟產 SBOM
  結果：SBOM 描述的是「這個 artifact 實際包含什麼」
  準確度：高（因為是看 artifact 本身）

scan-time SBOM（常見錯誤）：
  你 build 好了 → 事後去 scan source code / repo
  結果：SBOM 描述的是「repo 裡宣告了什麼依賴」
  準確度：低（不含 vendor、不含 copy 進去的 code、
             不含 build script 動態加入的東西）
```

build-time 的另一個好處：它迫使你把 SBOM 步驟放進 CI，而不是讓它成為「有空再說」的手動工作。

### GitHub Actions 範例

以下是一個完整的 GitHub Actions 流程，涵蓋 build → 產 SBOM → 簽章 → 上傳 artifact：

```yaml
# .github/workflows/release.yml
name: Build and Release

on:
  push:
    tags: ['v*']

permissions:
  contents: write
  packages: write
  id-token: write   # 讓 cosign 用 OIDC 簽章（不需要手動管 key）

jobs:
  build-and-sbom:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      # 1. Build container image
      - name: Build image
        run: |
          docker build -t my-app:${{ github.ref_name }} .
          docker save my-app:${{ github.ref_name }} -o my-app.tar

      # 2. 產 SBOM（build-time，在 image 存在後立刻跑）
      - name: Install syft
        uses: anchore/sbom-action/download-syft@v0

      - name: Generate SBOM (CycloneDX)
        run: |
          syft my-app:${{ github.ref_name }} \
            -o cyclonedx-json=my-app-${{ github.ref_name }}.sbom.cdx.json

      - name: Generate SBOM (SPDX, for compliance)
        run: |
          syft my-app:${{ github.ref_name }} \
            -o spdx-json=my-app-${{ github.ref_name }}.sbom.spdx.json

      # 3. 用 cosign 簽章（keyless，用 GitHub OIDC）
      - name: Install cosign
        uses: sigstore/cosign-installer@v3

      - name: Sign SBOM
        run: |
          cosign sign-blob \
            --yes \
            --output-signature my-app-${{ github.ref_name }}.sbom.cdx.json.sig \
            my-app-${{ github.ref_name }}.sbom.cdx.json

      # 4. Push image + attach SBOM 到 OCI registry
      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Push image
        run: |
          docker tag my-app:${{ github.ref_name }} \
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          docker push ghcr.io/${{ github.repository }}:${{ github.ref_name }}

      - name: Attest SBOM to image
        run: |
          cosign attest --yes \
            --predicate my-app-${{ github.ref_name }}.sbom.cdx.json \
            --type cyclonedx \
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}

      # 5. 上傳 SBOM 作為 CI artifact（GitHub Actions 原生）
      - name: Upload SBOM as artifact
        uses: actions/upload-artifact@v4
        with:
          name: sbom-${{ github.ref_name }}
          path: |
            my-app-${{ github.ref_name }}.sbom.cdx.json
            my-app-${{ github.ref_name }}.sbom.spdx.json
            my-app-${{ github.ref_name }}.sbom.cdx.json.sig
          retention-days: 1825   # 5 年 = 5 * 365

      # 6. 上傳到 GitHub Release（讓下載者拿得到）
      - name: Upload SBOM to Release
        uses: softprops/action-gh-release@v1
        with:
          files: |
            my-app-${{ github.ref_name }}.sbom.cdx.json
            my-app-${{ github.ref_name }}.sbom.spdx.json
```

幾個要點：

- `id-token: write` 是讓 cosign 用 GitHub OIDC 做 keyless 簽章的關鍵。沒有這個 permission，cosign 就得用 key file，那就要管 secret，複雜度倍增。
- `retention-days: 1825` 對應 5 年保存要求。GitHub Actions 預設 90 天，免費版更短——記得改。
- SPDX 和 CycloneDX 都產：前者給合規，後者給工具鏈（grype、Dependency-Track 對 CycloneDX 支援更好）。

## Gating：把 SBOM 掃描接到 Release 門禁

「產了 SBOM 但沒有接到任何決策點」是最常見的白費力氣形式。SBOM 要有用，掃描結果必須影響到 release 是否能走出去。

### 門禁設計

在前面的 CI workflow 加上漏洞掃描和門禁步驟：

```yaml
      # 在 sign + upload 之前插入門禁
      - name: Scan SBOM for vulnerabilities
        run: |
          grype sbom:my-app-${{ github.ref_name }}.sbom.cdx.json \
            --output json \
            --file grype-report.json

      - name: Check vulnerability gate
        run: |
          python3 - <<'EOF'
          import json, sys

          with open('grype-report.json') as f:
              report = json.load(f)

          matches = report.get('matches', [])

          criticals = [m for m in matches
                       if m['vulnerability']['severity'] == 'Critical']

          highs = [m for m in matches
                   if m['vulnerability']['severity'] == 'High']

          print(f"Critical: {len(criticals)}")
          print(f"High: {len(highs)}")

          if criticals:
              print("GATE FAILED: Critical vulnerabilities found")
              sys.exit(1)

          if len(highs) > 5:
              print("GATE FAILED: Too many High vulnerabilities")
              sys.exit(1)

          print("GATE PASSED")
          EOF
```

實際上，grype 支援直接傳 VEX 文件過濾：

```bash
grype sbom:my-app.sbom.cdx.json \
  --vex my-vex.cdx.json \
  --fail-on critical \
  --output json
```

`--fail-on critical` 讓 grype 在有 Critical 時直接以 exit code 1 退出，CI 自動 fail。

### VEX 例外機制

硬門禁最常見的失敗場景：上線第一天，掃出 200 個 CVE，全部都是 Critical，整個 release pipeline 立刻紅燈，工程師紛紛把門禁關掉或設定 `continue-on-error: true`。

這不是工具的問題，是沒有 VEX 降噪機制。VEX（Vulnerability Exploitability eXchange，Ch 16）讓你標記「這個 CVE 在這個產品裡不可被利用」，標了之後掃描工具就跳過它，門禁只看沒有被 VEX 解釋過的漏洞。

一個可操作的 VEX 工作流：

```
新 CVE 進入掃描報告
    │
    ▼
資安或開發者評估：
  ├── 這個 component 在我的 image 裡有嗎？（有時是 false positive）
  ├── 那段有漏洞的 code 我有用到嗎？
  └── 有路徑可以被觸發嗎？
    │
    ├── 確認不影響 → 更新 VEX 文件，標 not_affected + 理由
    ├── 有影響但有 workaround → 標 workaround + 說明
    └── 有影響且沒有解法 → 接受風險 + 票追蹤 + 計畫升版
```

VEX 文件本身也要版本控制、簽章，跟 SBOM 一起保存。否則「哪些 CVE 被接受了、誰決定的、什麼時候決定的」沒有稽核軌跡。

### 門禁政策的現實

100% 乾淨的 release 不存在，但很多組織在設計門禁時預設「必須零 Critical」然後三個月後大家都在繞過規則。

務實的門禁設計：

| 條件 | 對應行動 |
|------|---------|
| 無 VEX 的 Critical | 強制阻擋，必須修或出 VEX |
| 無 VEX 的 High > N 個 | 警告 + 要求主管核准 release |
| 有 VEX 的 Critical/High | 允許通過，但 VEX 要有時間限制（最長 90 天重評） |
| Medium/Low | 不阻擋，追蹤到下個 sprint |

「N 個」的閾值要根據你的組織現況設定，不是從天上掉下來的數字。剛開始可能要設高一點（比如 High > 20 才阻擋），然後每季調低，給團隊時間消化技術債。

## 要求供應商交 SBOM

你自己做好的 SBOM 只覆蓋你自己寫的部分。你採購的商業軟體、外包廠商交付的系統，如果沒有 SBOM，你的供應鏈視野就有一個洞。

### 合約條款

要求供應商交 SBOM 必須寫進採購合約，不寫就沒有執行力。最低要求的條款範本：

```
第 X 條：軟體物料清單（SBOM）

供應商應於每次軟體交付時同時提供符合以下規格的 SBOM：
  a. 格式：CycloneDX 1.6 或 SPDX 2.3，JSON 編碼
  b. 範圍：所有直接及傳遞依賴，含版本號、package URL（PURL）
  c. 簽章：供應商以其發布憑證對 SBOM 文件進行數位簽章
  d. 更新頻率：每次 release 交付一份，重大漏洞（CVSS >= 9.0）
     被發現時應於 72 小時內提供更新 SBOM 及 VEX
  e. 保存：甲方有權保存 SBOM 至合約終止後 5 年
```

這個條款的關鍵字：**每次交付**（不是「有要求時」）、**傳遞依賴**（不是只有頂層）、**簽章**（可驗真偽）、**72 小時更新**（應急情境）。

### 驗收進來的 SBOM

供應商交了 SBOM，你要驗收。不驗收等於沒有要求。

驗收程序分三層：

**格式驗證**（自動化）：

```bash
# 用 cyclonedx-cli 驗證 CycloneDX 格式
cyclonedx validate --input-file vendor.sbom.cdx.json

# 用 ntia-checker 驗 NTIA 最小要素
ntia-checker -i vendor.sbom.spdx.json
```

**簽章驗證**（自動化）：

```bash
# 驗 cosign keyless 簽章
cosign verify-blob \
  --certificate vendor.sbom.cdx.json.crt \
  --signature vendor.sbom.cdx.json.sig \
  vendor.sbom.cdx.json

# 或驗 GPG 簽章（傳統供應商）
gpg --verify vendor.sbom.cdx.json.sig vendor.sbom.cdx.json
```

**品質評分**（半自動）：

用 SBOM Scorecard（CISA 推薦工具）或自建腳本，評估：
- PURL 覆蓋率（每個 component 有沒有 PURL？沒有就對不到 NVD）
- 版本號完整性（有沒有用 "unknown" 當版本號）
- 傳遞深度（只有一層頂層依賴的 SBOM 幾乎沒有意義）
- License 資訊（有沒有 NOASSERTION 太多）

定一個最低分數門檻（比如 Scorecard 0.6 以上才接受），拒絕品質不夠的 SBOM，退回要求重交。

## 消費進來的 SBOM

收進來的 SBOM（自己產的 + 供應商交的）最終要匯進 Dependency-Track，才能做長期追蹤和告警。

### 匯入 Dependency-Track

Dependency-Track 的 API 讓自動化匯入很容易：

```bash
# 匯入 SBOM 到 Dependency-Track 的特定 project
curl -X PUT "https://your-dtrack.example.com/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -F "projectName=my-app" \
  -F "projectVersion=$VERSION" \
  -F "autoCreate=true" \
  -F "bom=@my-app-$VERSION.sbom.cdx.json"
```

在 CI 的最後一步加上這個 call，每次 release 自動更新 Dependency-Track 裡的狀態。

### 供應鏈關聯

Dependency-Track 支援「這個產品的依賴包含另一個 project 的 SBOM」——這讓你建立真正的供應鏈視圖：

```
你的產品 A
  ├── 自己寫的 code（你的 SBOM）
  ├── 供應商 B 的元件（B 的 SBOM 匯進來後，關聯到 A）
  └── 開源 lib C（出現在你的 SBOM 裡）
```

當 B 的某個元件有新 CVE，Dependency-Track 會自動告警「你的 A 產品依賴的 B 有問題」，不需要人工追蹤。

### naming 對不齊的問題

這是實作上最臭的問題，沒有之一。

供應商在 SBOM 裡寫的 package 名稱是他們自己取的，可能是：
- `openssl-libs-1.1.1q`
- `OpenSSL_1.1.1q`
- `lib64openssl-devel`

而 NVD 的 CPE 是：
- `cpe:2.3:a:openssl:openssl:1.1.1q:*:*:*:*:*:*:*`

如果 PURL 不完整，工具無法自動對齊，告警就漏掉。

解法：
1. 要求供應商 SBOM 必須含 PURL（`pkg:rpm/openssl@1.1.1q` 這種格式）——這是合約條款的一部分。
2. 在 Dependency-Track 的 component lookup 設定 CPE 的 alias mapping（手動補，只需要做一次）。
3. 對沒有 PURL 的 component，在 Dependency-Track 手動編輯補上，並 flag 讓下次更新要求供應商修正。

不要期待這個問題自動解決，它需要人工維護，但是一次性的工作，做完就穩定了。

## 組織責任分工

SBOM 計畫跨越多個團隊，最容易失敗的原因是「我以為你在管」。把責任明確分工寫進 policy：

| 角色 | 負責什麼 | 對誰負責 |
|------|---------|---------|
| 資安團隊 | 維護 SBOM policy、設計門禁條件、審核 VEX 決策、收供應商 SBOM 驗收 | CISO |
| 開發 / DevOps | CI pipeline 整合 syft/cosign、確保每個 release 都產 SBOM | 工程主管 |
| 安全操作（SOC） | 監看 Dependency-Track 告警、分類、觸發 incident 流程 | 資安主管 |
| 採購 / 法務 | 在合約加入 SBOM 條款、確保供應商合規 | 法務長 |
| 開發者個人 | VEX 評估（我的 code 有沒有用到有問題的 function）、修版本 | 自己的 tech lead |

這個表格的關鍵是「對誰負責」那欄——沒有 accountability 的責任分工等於沒有分工。

## 對比與取捨

### SBOM 計畫成熟度矩陣

| Level | 狀態 | 特徵 | 缺點 |
|-------|------|------|------|
| 0 | 沒有 SBOM | 靠人工記憶或 `npm ls` 臨時跑 | CVE 爆了靠考古，幾天起跳 |
| 1 | 手動產、沒有儲存 | 有需要才跑 syft，結果放本機 | 沒有版本對應，過期就廢 |
| 2 | CI 自動產、存 artifact | 每次 build 都產，存在 CI storage | 沒有消費端，沒有告警，產了等於沒產 |
| 3 | CI + 門禁 + Dependency-Track | 掃描接門禁，匯入 D-Track 長期監控 | 供應商 SBOM 缺，視野還是有洞 |
| 4 | 全鏈：自產 + 供應商 + VEX + 簽章 | 合約要求供應商、VEX 降噪、簽章可驗、關聯視圖完整 | 需要有人維護 naming 對齊和 VEX 過期重評 |

大部分宣稱「有 SBOM 計畫」的企業實際上在 Level 2，以為自己在 Level 3。差在有沒有接消費端。

### 工具選型對比

| 維度 | syft + grype | Trivy | FOSSA |
|------|-------------|-------|-------|
| SBOM 生成品質 | 高，多格式 | 高，但 SBOM 輸出是副產品 | 高，商業版 |
| 漏洞掃描 | grype 分開跑 | 一體整合 | 商業功能 |
| License 分析 | 基本 | 基本 | 強，這是它的主場 |
| 長期追蹤 | 需另接 Dependency-Track | 需另接 | 內建 |
| 開源 / 費用 | 完全開源免費 | 完全開源免費 | 商業收費 |
| 適合誰 | 有 DevOps 資源自建的團隊 | 想要一體化的團隊 | 授權合規是主要需求 |

## 踩雷集錦

**1. 「CI 產了 SBOM，但它存在 GitHub Actions 的 artifact，90 天後自動刪」**

GitHub Actions artifact 的預設 retention 是 90 天，免費帳號更短。很多團隊設定好 CI 上傳 SBOM，結果一年後發現三個月前的 release 的 SBOM 已經不見了。遇到 audit 或事故要查歷史，空手。

解法：明確設 `retention-days: 1825`，或者把 SBOM 另外存到 S3 / GCS 或 attach 到 OCI registry（後者跟 image 同壽命）。policy 要寫清楚保存期限，上線前確認 CI 設定符合 policy。

**2. 「門禁設了 fail-on critical，第一週就被繞過」**

場景是這樣的：設了 `grype --fail-on critical`，結果第一次跑掃出 50 個 Critical（很多是 base image 裡的）。deadline 到了，有人把門禁那個 step 改成 `continue-on-error: true`，或者新增 `if: false` 跳過。然後一直沒人改回去。

根本原因是沒有 VEX 機制，門禁一設就全紅，工程師唯一的選擇是繞過。

解法：門禁和 VEX 要同時上線，不能只上門禁。第一週先讓掃描跑起來但不阻擋（`--fail-on` 拿掉），花一週把大量的 false positive 和 not_affected 都加進 VEX，然後再開門禁。這個順序跳過就會死。

**3. 「供應商交了一份 SBOM，只有 3 個 top-level component，沒有傳遞依賴」**

這是最常見的「假 SBOM」形式：供應商用了最偷懶的方式生成，只列出他們在程式碼裡顯式引用的幾個函式庫，底下的傳遞依賴全部沒有。這份 SBOM 通過了格式驗證（CycloneDX 合法），但對你毫無意義。

解法：在驗收流程加傳遞深度檢查（component 數量 < 某個閾值就直接退回），或用 ntia-checker 的 `--depth` 選項。合約條款要明確寫「必須包含所有傳遞依賴」，並給出「不夠」的定義（比如：component 數量少於 50 的對外發布商業軟體不可能是完整的）。

**4. 「SBOM 產了、Dependency-Track 也設了，但 naming 對不齊導致告警從不響」**

資安主管看了三個月的 Dependency-Track，沒有告警，以為環境很乾淨。結果一查才發現：供應商交的 SBOM 裡所有 component 都沒有 PURL，Dependency-Track 無法把它們和 NVD 的 CPE 對上，等於白掃。

解法：任何 SBOM 匯入 Dependency-Track 後，都要有一個自動驗證步驟：拉 API 看匯進去的 component 裡有多少有 PURL、有多少有 CPE，跟一個最低比例門檻比對。比例不夠就發告警，不是「靜悄悄地容忍」。

**5. 「VEX 標了 not_affected，但三個月後有新研究改變了可利用性評估，沒有人重評」**

VEX 是有時效性的判斷，不是永久赦免。標了 not_affected 的理由可能是「這個元件沒有暴露給外部 input」，但如果之後系統架構改了、或有新的利用路徑被研究出來，舊的 VEX 就失效了。

解法：每個 VEX 條目都要設過期日（建議 90 天），過期後自動退回「需重評」狀態。Dependency-Track 本身支援 VEX import，但過期重評要靠自建邏輯或 CI 排程腳本提醒。

## 進階：再往深一層

**SBOM 計畫的 KPI**

一個 SBOM 計畫要怎麼知道自己有沒有在發揮效果？用以下幾個指標追蹤：

- **SBOM 覆蓋率**：對外發布的產品裡，有多少 % 有對應的簽章 SBOM？目標：100%。
- **SBOM 新鮮度**：每個 SBOM 和對應的 release 時間差多少？超過 24 小時就是問題。
- **門禁通過率**：每週有多少 release 在第一次掃描就通過門禁（不需要 VEX 例外）？這個數字應該隨著技術債清除而上升。
- **CVE 反應時間（MTTI）**：從 CVE 公開到你知道自己有沒有受影響，平均多久？這是 SBOM 最直接的 value 指標。
- **VEX 例外積壓**：有多少個 VEX 條目已超過 90 天沒有重評？這個數字應該趨近於零。

**與 SLSA 的關係**

SBOM 計畫和 SLSA 框架（Ch 22-23）不是競爭關係，是互補的：SLSA 的 provenance attestation 告訴你「這個 artifact 是怎麼 build 出來的、用了什麼 source、在哪個 CI 環境」，SBOM 告訴你「這個 artifact 裡面有什麼元件」。兩者都在，你對一個 artifact 的描述才是完整的。

成熟的 SBOM 計畫，SLSA L2 以上的 provenance 和 SBOM 會一起被 cosign attest 到 OCI image，用 `cosign verify-attestation` 拿到。

**規模化：多產品、多 team 的挑戰**

單一產品的 SBOM 計畫相對好管，難在規模化。100 個 repo、20 個 team，每個 team 的 build system 不一樣（有 Maven 有 npm 有 pip 有 cargo），CI 設定不統一，Dependency-Track 裡的 project 命名沒有規範，一個 CVE 爆了不知道哪些 team 的哪些版本受影響。

解法是 **SBOM 的 central ingestion service**：所有 CI 產好的 SBOM 都推到一個 central service（可以是 Dependency-Track 的集中部署，也可以是自建的 aggregator），由它做 naming normalization、版本追蹤、告警路由。這樣 CVE 爆了，一個 query 就知道所有受影響的產品和 team。

## 動手練習

**SBOM Policy Draft**

為你的一個真實（或假設）產品寫一份 SBOM policy，限 1-2 頁，包含以下六個部分：

1. **Scope**：這份 policy 覆蓋哪些產品？用三個圈區分必須/應該/最好有，各列出 2-3 個具體例子。

2. **格式選擇與理由**：選 CycloneDX 還是 SPDX 或兩者都要？選傳遞依賴全部還是只有直接？用一段話說明理由（不是「因為工具預設」，是因為你的需求）。

3. **CI 觸發點**：列出所有觸發 SBOM 更新的事件（release / 依賴變更 / base image 更新 / 定期重掃等），說明每個觸發點是自動化的還是需要人工發起。

4. **Gating 條件**：Critical / High / Medium 各要怎麼處理？VEX 例外的流程是什麼？誰有權核准 release 在有未解決 High 的情況下放行？

5. **保存策略**：SBOM 存在哪裡（CI artifact / OCI registry / S3）？保存多久？誰有讀取權限？

6. **責任分工表**：參考本章的分工表，填入你的組織的實際角色名稱，確認每個責任有一個實名的人或團隊。

寫完後問自己：如果今天 Log4Shell 爆了，按照這份 policy，從 CVE 公開到你知道自己所有產品的暴露狀況，要花多久？如果答案超過一小時，找出 policy 裡的瓶頸，修掉它。

## 本章重點整理

- SBOM 計畫 = 政策（scope / 格式 / 觸發 / 保存）+ CI 自動產 + 門禁（VEX 降噪）+ 供應商管理 + 消費端（Dependency-Track）。五個環節，缺一個就是 Level 2 偽計畫。
- Build-time 產 SBOM，不是 scan-time。Scan-time 的 SBOM 描述的是 repo 宣告，不是 artifact 實際內容。
- 門禁和 VEX 要同時上線。只有門禁沒有 VEX，第一週就被繞過。
- 供應商 SBOM 要合約條款 + 驗收程序。格式驗證、簽章驗證、品質評分，三層缺一都是假驗收。
- Naming 對不齊（package 名 vs CPE）是最容易讓 Dependency-Track 告警失效的坑，要主動在匯入後做覆蓋率驗證。
- 成熟度矩陣：Level 0-1 是沒有計畫，Level 2 是有假象，Level 3-4 才是真正有效。

## 自我檢核

- [ ] 我能說出一個 SBOM policy 要回答的六件事，不看筆記
- [ ] 我知道 build-time SBOM 和 scan-time SBOM 的差異，以及為什麼 build-time 更準確
- [ ] 我能解釋為什麼「只有門禁沒有 VEX」會讓門禁在一週內被繞過
- [ ] 我知道供應商 SBOM 驗收的三層（格式 / 簽章 / 品質評分）
- [ ] 我理解 Dependency-Track 的 naming 對不齊問題，以及如何偵測它
- [ ] 我能列出 SBOM 計畫的五個 KPI 指標
- [ ] 我完成了 SBOM policy draft 練習，並且驗證了「CVE 爆了到知道暴露狀況要多久」

## 延伸閱讀

- **[CISA「How-To Guide for SBOM」（2023）](https://www.cisa.gov/resources-tools/resources/sbom-tooling-and-implementation-how-guide)**（CISA）
  - **讀哪裡**：Section 3「Producing SBOM」和 Section 4「Consuming SBOM」——這是這章兩個主軸的官方指南
  - **和本章的關聯**：CI 產 SBOM 和 Dependency-Track 消費的實作細節，CISA 的推薦做法和這章的設計基本吻合

- **[CISA「SBOM Sharing Roles and Considerations」（2023）](https://www.cisa.gov/resources-tools/resources/sbom-sharing-roles-and-considerations)**（CISA）
  - **讀哪裡**：責任分工那一節，CISA 對 producer / transformer / consumer 三個角色的定義
  - **和本章的關聯**：本章的組織責任分工表是把 CISA 的框架對應到企業內部角色

- **[OWASP Dependency-Track 文件](https://docs.dependencytrack.org/)**（OWASP）
  - **讀哪裡**：「Project Setup」和「Integrations / API」——這兩章是 CI 自動匯入的實作基礎
  - **和本章的關聯**：Dependency-Track 是這章「消費端」的核心工具，官方文件的 API 章節對應 CI 匯入那段

- **[NTIA「SBOM How-To: Creating and Sharing Software Bill of Materials」](https://www.ntia.gov/page/software-bill-materials)**（NTIA）
  - **讀哪裡**：「Sharing SBOM」那節，包含供應鏈場景的 SBOM 傳遞模式
  - **和本章的關聯**：供應商要求交 SBOM 那節的理論背景，NTIA 對「消費者有權要求 SBOM」的論述

- **[CISA「SBOM Minimum Element Vendor Survey」](https://www.cisa.gov/resources-tools/resources/sbom-minimum-element-vendor-survey)**（CISA）
  - **讀哪裡**：全文不長，重點看現有工具覆蓋 NTIA 最小元素的程度
  - **和本章的關聯**：供應商 SBOM 品質評分的現實依據——大部分工具還是達不到全覆蓋，這解釋了為什麼驗收不能只做格式驗證

---

SBOM 計畫建立起來後，下一個問題是：這些 SBOM 怎麼傳給需要它的人——你的客戶、你的合規稽核員、你的供應鏈夥伴？格式、傳輸方式、存取控制，下一章展開。

→ [Ch 26 SBOM 分發與交換](./26-sbom-distribution.md)
