# Ch 9 — secrets、環境變數、OIDC

> 目標：分清 repo / organization / environment secret 的層級；掌握 `${{ secrets.X }}` 的安全邊界；知道 OIDC 為什麼取代長期 credentials。

## secret 不是環境變數

先分開兩種東西：

- **環境變數（env）**：workflow 或 step 層宣告，公開的設定（`DEBUG=1`、`NODE_ENV=production`）
- **secret**：GitHub 幫你存的加密字串（DB 密碼、API token、registry credentials）

差別：

| 面向 | env | secret |
|---|---|---|
| 誰能看 | 所有人（寫在 YAML） | 只 repo admin 能設、workflow 能引用 |
| Log 顯示 | 直接印 | **自動 mask** 成 `***` |
| 用法 | `${{ env.X }}` 或直接 shell | `${{ secrets.X }}` |

**secret 會被自動從 log mask**。但這不是安全保證：Action 裡你可以手滑 `echo $SECRET | base64` 繞過 mask。

## 三個層級的 secret

從小到大：

### 1. Environment secret（最細）

```yaml
jobs:
  deploy:
    environment: production           # ← 指定 environment
    runs-on: ubuntu-latest
    steps:
      - run: ./deploy.sh
        env:
          API_KEY: ${{ secrets.API_KEY }}    # 從 'production' environment 抓
```

GitHub UI：Settings → Environments → 建 `production` → 加 secret。

好處：

- **可以配 required reviewer**：job 要等人按「approve」才跑
- **限定 branch**：只允許 `main` 觸發的 deployment 用這個 secret
- **wait timer**：強制延遲（防止誤觸發部署）

生產 deployment 一定用 environment secret。

### 2. Repository secret

Settings → Secrets and variables → Actions → Repository secrets。

同一 repo 的所有 workflow 都能抓。**多數 CI 用這層**（build 的 token、pytest 的 DB password 等）。

### 3. Organization secret

Settings（org 層級）→ Secrets。

一次設定、多個 repo 共用（例如 AWS 根 credential、共用 Slack webhook）。可以限定 **哪些 repo 能用**（白名單）。

## `${{ secrets.X }}` 能在哪用

可以：

```yaml
steps:
  - run: deploy.sh
    env:
      TOKEN: ${{ secrets.DEPLOY_TOKEN }}

  - uses: some/action@v1
    with:
      api-key: ${{ secrets.API_KEY }}
```

**不可以**：

```yaml
# workflow 的 `on:` / `if:` 層級用不到 secret
on:
  workflow_dispatch:
    inputs:
      token:
        default: ${{ secrets.X }}    # ← 不行
```

這是設計：`if:` 在 workflow parse 就會展開，那時 secret 未注入。

## Mask 的邊界

GitHub 會：

1. 把 secret **完整字串** 在 log 裡 replace 成 `***`
2. 但 **不會對變形後的 secret** mask（base64、hex、substring）

例子：

```yaml
- run: echo "${{ secrets.TOKEN }}" | base64
  # log: d29vb3BvcG1raw==   ← 沒 mask，原文被秀了
```

這是 CI 史上最常見的 secret leak 模式。別做變形 echo。

另個坑：如果 secret 只有 3 字、剛好也是英文常見字（像 `abc`），mask 會過度 mask — 正常 log 裡的 `abc` 全變 `***`，debug 很痛。永遠用夠長、夠亂的 secret。

## 實作：把 GHCR token 存成 secret

Ch 12 要把 image push 到 GHCR。先把這個 secret 設好：

### GHCR 的 token 選項

你有兩種選：

1. **用預設的 `GITHUB_TOKEN`**：每 job 自動有，權限可調。**推薦**，不需要你自己管 PAT
2. **用 personal access token (PAT)**：比較麻煩但可跨 repo，少數情境需要

用 `GITHUB_TOKEN` 很簡單：

```yaml
permissions:
  contents: read
  packages: write       # ← 關鍵，預設沒開

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - run: docker pull ghcr.io/you/somewhere:latest
```

`GITHUB_TOKEN` 是 GitHub 在每個 job 開始時自動產生的短期 token，**job 結束就失效**。這是比 PAT 更安全的做法。

### 要用 PAT 的場景

- 你的 push target 在別的 repo 或 org
- 需要更多 scope（例如觸發其他 repo 的 workflow）

那時才建：Settings（個人）→ Developer settings → Personal access tokens → Fine-grained tokens。**一定用 fine-grained**，不要用 classic（權限過大、無 expiry）。

## 不該做的事

- ❌ **不要把 secret 寫在 YAML 裡**：`AWS_KEY: AKIA...` 一旦 merge，git history 永遠有
- ❌ **不要 echo secret**，即使 mask 了也不要養成習慣
- ❌ **不要把 secret 寫進 artifact 上傳**：`upload-artifact` 上傳的內容會在 UI 顯示
- ❌ **不要把 fork 的 PR 信任 secret**：`pull_request` 觸發的 PR 從 fork 進來時 **拿不到 secret**（GitHub 故意的，防 fork 提 PR 偷 secret）
- ❌ **不要 hard-code token 到 Dockerfile**：`ENV TOKEN=xyz` 會烙在 image 裡，pull 下來看得到

## OIDC：終結長期 credentials

長期 credentials 的問題：

- AWS_KEY / AWS_SECRET 一旦 leak，攻擊者用到你換新的之前
- 手動輪換麻煩
- Scope 常被設太大

OIDC 的想法：**讓 GitHub Actions 動態向 AWS/GCP/Azure 換一次性 credential**。

怎麼運作（以 AWS 為例）：

```
┌─────────────────┐          ┌──────────────┐        ┌────────────┐
│ GitHub Actions  │─(1) JWT─→│     AWS      │        │            │
│                 │          │  (STS IAM)   │       │            │
│                 │←(2)temp──│              │       │            │
│                 │  cred    │              │       │            │
└────────┬────────┘          └──────────────┘       │            │
         │                                           │            │
         │        (3) do stuff                       │            │
         └──────────────────────────────────────────→│   S3/..    │
                                                     │            │
                                                     └────────────┘
```

1. GitHub 簽一個短期 JWT，內含 `repo`、`branch`、`job` 等 claim
2. 你 IAM 設定信任：「只接受 GitHub 簽的、來自 repo X 且 branch main 的 token」
3. 換成 AWS 短期 credential（通常 1 小時）
4. workflow 用這個 credential

**沒有任何 long-lived secret 存在 GitHub**。

### AWS OIDC workflow 範例

```yaml
permissions:
  id-token: write                   # ← 允許 job 拿 OIDC token
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789:role/github-actions-tasktrack
          aws-region: ap-northeast-1
      - run: aws s3 cp foo s3://bucket/
```

AWS 那端要先：建 IAM OIDC provider + IAM role + trust policy 指定接受哪個 repo / branch。**這部分不是這課的 scope**（跟雲端設定綁太深），但知道有這個機制，看到別人 YAML 這樣寫就不會傻眼。

GCP、Azure 都有類似機制（`google-github-actions/auth`、`azure/login`）。

## 動手練習

1. 到你的 GitHub repo Settings → Secrets，建一個 test secret `HELLO_SECRET` = `world`
2. 寫一個 workflow，印 `Hello ${{ secrets.HELLO_SECRET }}`，確認 log 顯示 `Hello ***`
3. 把 workflow 加 `permissions: packages: write`、push，Actions 跑會通
4. （選做）故意試 `echo "${{ secrets.HELLO_SECRET }}" | base64`，看 log 真的會漏
5. 在 repo 建一個 `production` environment，加個 secret，配 required reviewer。寫個 job 指定 `environment: production`，觸發看要等 approve

## 常見誤解

- 「**`echo ${{ secrets.X }}` 被 mask 就安全**」 — mask 只對完整字串、容易繞。**不 echo 是紀律問題**
- 「**secret 存 GitHub 很危險**」 — GitHub 的 secret 是加密儲存、每次讀寫有稽核。比存 `.env` 檔上傳到 S3 安全多
- 「**`GITHUB_TOKEN` 權限小**」 — 預設不小。`permissions:` 要顯式最小化，不然它有寫 repo 的權限
- 「**OIDC 很複雜不值得**」 — AWS 那端 30 分鐘設定，之後永遠不用輪換 credential。規模化時值爆
- 「**fork PR 能拿到 secret**」 — 不能。`pull_request` event 從 fork 來的根本拿不到 secrets（這是安全設計，不是 bug）

## 驗收標準

- [ ] 你知道怎麼去 repo settings 建 secret
- [ ] 你寫過一個 workflow 用 `${{ secrets.X }}`，log 顯示 mask
- [ ] 你知道 `permissions:` 要顯式最小化
- [ ] 你理解為什麼 `GITHUB_TOKEN` 比 PAT 安全
- [ ] 你概念上懂 OIDC 在幹嘛（不需要實作完整 AWS 設定）

## 自我檢核

- [ ] 我能分 env / secret、知道各自用法和邊界
- [ ] 我懂三層 secret scope（env / repo / org）與各自用途
- [ ] 我絕對不在 CI 裡 `echo` secret 或變形後 echo
- [ ] 我知道 fork PR 故意拿不到 secret 這件事
- [ ] 我知道 OIDC 存在、它解什麼問題

下一章處理一個重複代碼問題：`build + push image` 的 workflow 會在多個 repo 一模一樣，怎麼抽出來重用。

→ [Ch 10 reusable workflow 與 composite action](./10-reusable-workflow.md)
