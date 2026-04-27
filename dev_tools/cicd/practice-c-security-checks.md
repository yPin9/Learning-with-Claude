# 練習 C — 給 pipeline 加安全檢查

> 目標：在現有 `tasktrack` pipeline 加上三道安全檢查 — Trivy 掃 image 弱點、Dependabot 自動追依賴更新、CODEOWNERS 自動指派 reviewer。

## 任務規格

### 必備產出

1. **`.github/workflows/security.yml`**：在 PR 與排程時 run Trivy 掃 image，發現 HIGH 以上弱點就紅
2. **`.github/dependabot.yml`**：配置依賴自動追蹤（pip、docker base image、GitHub Actions 版本）
3. **`.github/CODEOWNERS`**：定義哪些檔案改動要誰 review（小專案是你自己，組織有意義）

### 驗收標準

- [ ] PR 時 Trivy 跑、有 report 上傳（artifact）
- [ ] 有排程掃（至少每週一次）
- [ ] Dependabot 會自動開 PR（測試：改壞一個版本、等 Dependabot 開 PR）
- [ ] CODEOWNERS 生效（新 PR 自動 request review 給指定 owner）

## Step-by-step

### Step 1：Trivy image scan

Trivy 是開源 image 弱點掃描工具。掃 image：OS 套件、語言依賴、設定錯誤、secret leak。

```yaml
# .github/workflows/security.yml
name: Security

on:
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 3 * * 1'              # 每週一 UTC 03:00
  workflow_dispatch:

permissions:
  contents: read
  security-events: write              # ← 上傳 SARIF 給 Security tab

jobs:
  trivy-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          load: true
          tags: tasktrack:scan
          cache-from: type=gha

      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: tasktrack:scan
          format: sarif
          output: trivy-results.sarif
          severity: HIGH,CRITICAL
          exit-code: '1'                # ← 發現 HIGH+ 就紅
          ignore-unfixed: true          # ← 沒 fix 的不擋（避免 noise）

      - uses: github/codeql-action/upload-sarif@v3
        if: always()                    # 成功或失敗都上傳
        with:
          sarif_file: trivy-results.sarif
```

`github/codeql-action/upload-sarif` 把 SARIF（Static Analysis Results Interchange Format）上傳到 repo 的 **Security** → **Code scanning alerts** 頁，你能在 UI 看所有掃到的問題。

**常見卡點**：

- Trivy DB 有時首次抓很慢（pull DB 從 GitHub Container Registry）
- `severity: HIGH,CRITICAL` 太嚴格會卡住開發，一開始可放寬到只 `CRITICAL`、慢慢收緊
- `ignore-unfixed: true` 是實務的妥協 — 上游沒 fix 你擋了也沒用

### Step 2：Trivy filesystem scan（補充）

除了掃 image，Trivy 也能掃原始碼（`fs` mode）找 secret leak、設定問題：

```yaml
  trivy-fs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          scan-ref: .
          format: sarif
          output: trivy-fs-results.sarif
          severity: HIGH,CRITICAL

      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: trivy-fs-results.sarif
          category: trivy-fs
```

兩個 SARIF 上傳要 `category:` 分開，不然會互相覆蓋。

### Step 3：Dependabot

`.github/dependabot.yml`：

```yaml
version: 2
updates:
  # Python 依賴
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 10
    groups:
      fastapi-stack:
        patterns: ["fastapi", "uvicorn", "pydantic*"]
      dev-tools:
        patterns: ["ruff", "mypy", "pytest*"]

  # Docker base image
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "weekly"

  # GitHub Actions 版本（actions/checkout@v4 → v5 這種）
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

幾個重點：

- **`groups:`** 把相關套件打包成一個 PR（不然 10 個 fastapi 相關套件各開一個 PR，淹沒 PR 列表）
- **`interval: weekly`** 是起手平衡點，`daily` 通常太吵
- **`open-pull-requests-limit`** 避免一次堆 20 個 PR

Dependabot 會 push 到 `dependabot/xxx` branch 開 PR，你的 CI workflow 也會 trigger（記得 Ch 6 的 `on: pull_request`）。**Dependabot 開的 PR 預設拿不到 `secrets`**（安全設計），這通常沒影響 CI，有影響時可透過 `secrets: inherit` 於 reusable workflow 調整。

### Step 4：CODEOWNERS

`.github/CODEOWNERS`：

```
# 預設
*               @你的 GitHub username

# 關鍵資料夾
/app/           @你的 username
/.github/       @你的 username

# Dockerfile / infra
Dockerfile      @你的 username
docker-compose.yml @你的 username
```

小專案你就是唯一的 owner。多人專案：

```
# backend team 負責 app/
/app/           @org/backend-team

# platform team 負責 CI/CD
/.github/workflows/  @org/platform-team

# DB migration 要 DBA 看
/migrations/    @org/dba-team
```

CODEOWNERS 的生效需要：

- Settings → Branches → Branch protection rule → **Require review from Code Owners**

加了之後，改 `app/` 的 PR 自動 request review 給 `@org/backend-team`、必須他們 approve 才能 merge。

## 進階：要不要加 SBOM 生成

SBOM（Software Bill of Materials）是你 image 裡所有 package 清單的正式格式。合規場景（政府、金融）會要求。工具：`anchore/syft`、`aquasecurity/trivy` 也能生。

```yaml
- uses: anchore/sbom-action@v0
  with:
    image: ghcr.io/${{ github.repository }}:${{ github.sha }}
    format: spdx-json
    artifact-name: sbom.spdx.json
```

**這課不要求**。小專案沒必要。企業環境才碰。

## 進階：image signing（cosign）

`cosign` 可以為你 push 的 image 簽名，消費方驗證簽名才 pull。關鍵工具：

```yaml
- uses: sigstore/cosign-installer@v3
- run: |
    cosign sign --yes ghcr.io/${{ github.repository }}@${{ steps.push.outputs.digest }}
```

Sigstore 機制跟 OIDC 一樣，不需長期 key。**企業合規值得**，小專案可延後。

## 完整參考解答

<details>
<summary>點開 security.yml 完整版</summary>

```yaml
name: Security

on:
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 3 * * 1'
  workflow_dispatch:

permissions:
  contents: read
  security-events: write

concurrency:
  group: security-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  trivy-fs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          scan-ref: .
          format: sarif
          output: trivy-fs.sarif
          severity: HIGH,CRITICAL
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: trivy-fs.sarif
          category: trivy-fs

  trivy-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          load: true
          tags: tasktrack:scan
          cache-from: type=gha
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: tasktrack:scan
          format: sarif
          output: trivy-image.sarif
          severity: CRITICAL
          exit-code: '1'
          ignore-unfixed: true
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: trivy-image.sarif
          category: trivy-image
```

</details>

## 測試用例

```bash
# 1. 故意讓 Trivy 紅：把 requirements.txt 加個舊版有已知 CVE 的套件
# 例如 requests==2.25.0（有 CVE-2023-32681）
echo "requests==2.25.0" >> requirements.txt
git commit -am "test: trigger trivy"
git push
# security workflow 的 trivy-image 會紅

# 2. Dependabot：
# 把 requirements.txt 裡一個套件降版
# 等一天 Dependabot 會開 PR 建議升回
# 或到 Insights → Dependency graph → Dependabot 手動觸發

# 3. CODEOWNERS：
# 改 app/main.py 開 PR
# 預期：PR 自動 request review 給 CODEOWNERS 裡寫的 user/team
```

## 常見誤解

- 「**Trivy 紅就不能 merge**」 — 看嚴格度。預設設成 `severity: CRITICAL` 比較實際
- 「**Dependabot 自動 merge 安全**」 — 視情況。有 `dependabot/fetch-metadata` + auto-merge 可配，但 breaking change 你會想先看
- 「**CODEOWNERS 強制 review 來自 owner 本人才算**」 — 要 `Require review from Code Owners` 開了才強制
- 「**SBOM 就是安全**」 — SBOM 只是清單。真正的安全是掃 + 簽 + 監控 + 修
- 「**scheduled workflow 一定會按時跑**」 — 免費方案不保證準時（GitHub 負載高會延遲幾分鐘到半小時）

## 自我檢核

- [ ] 我會在 PR 加 image 弱點掃描、紅了能 block
- [ ] 我會配 Dependabot 三個 ecosystem（pip、docker、github-actions）
- [ ] 我懂 `groups:` 讓 Dependabot PR 不爆量
- [ ] 我會寫 CODEOWNERS、知道要配 branch protection 才生效
- [ ] 我知道 SBOM / cosign 是企業級工具、不強求

最後一關：final project 把 `tasktrack` 送上線。

→ [Final Project：把 tasktrack 完整生產化](./final-project-tasktrack-production.md)
