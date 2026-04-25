# Ch 12 — Container registry 與 tag 策略

> 目標：搞懂 GHCR / Docker Hub / ECR 的差別、設計一套 image tag 策略、讓 main 的 merge 自動 build 並 push 到 GHCR。

## 什麼是 container registry

Registry 是 image 的倉庫 — 像 package manager 的 PyPI / npm，但放的是 Docker image。

`docker pull python:3.12` 這條命令幕後：

```
docker pull python:3.12
   ↓
預設 registry = registry-1.docker.io
   ↓
拉 library/python:3.12（library 是 Docker Hub 的官方 namespace）
   ↓
本地存成 python:3.12
```

指定別的 registry：

```bash
docker pull ghcr.io/owner/image:tag
docker pull 123456789.dkr.ecr.ap-northeast-1.amazonaws.com/myapp:tag
docker pull gcr.io/project/image:tag
```

## 常見 registry 比較

| Registry | 用途 | 免費額度 | 特點 |
|---|---|---|---|
| **Docker Hub** | 公開 image 的事實標準（python、node、postgres 等 base） | Public 無限、private 1 個免費 | 拉的時候可能 rate limit |
| **GHCR**（GitHub） | 跟 GitHub repo 綁定的 image | 公開無限、私有給 repo 500MB | 跟 Actions 整合好、`GITHUB_TOKEN` 可直接用 |
| **AWS ECR** | 雲端部署到 AWS 時 | 500MB/月 | IAM 權限控制、同 region 不吃外網流量 |
| **Google GCR / Artifact Registry** | 部署到 GCP | 免費額度看 region | 同上，跟 GCP 綁定 |
| **Azure Container Registry** | 部署到 Azure | 免費方案有 10GB | 同上 |

**這門課用 GHCR**。理由：

1. 跟 Actions 整合零摩擦（`GITHUB_TOKEN` 直接用）
2. 公開 image 免費、私有 image 小專案也夠
3. Pull 沒 Docker Hub 那種 rate limit

## GHCR 的 image 路徑

格式：`ghcr.io/<owner>/<image>:<tag>`

例：

- `ghcr.io/octocat/tasktrack:latest`
- `ghcr.io/octocat/tasktrack:v0.1.0`
- `ghcr.io/octocat/tasktrack:sha-a1b2c3d`

**owner 是 user 或 org 名**，不是 repo 名。image 名你自己決定（慣例跟 repo 名相同）。

## Tag 策略：三套主流方案

你 push 到 registry 時 tag 要怎麼取？三個策略可以混用：

### 1. Git SHA tag

```
ghcr.io/you/tasktrack:sha-a1b2c3d
```

**每個 commit 都是一個 tag**。好處：

- **100% 可追蹤**：看 image 就知道是哪個 commit build 的
- **rollback 簡單**：前一版 sha 重新部署就好
- 不會撞名

**這是生產的基本盤**。

### 2. Branch tag

```
ghcr.io/you/tasktrack:main
ghcr.io/you/tasktrack:feature-xyz
```

**每個 branch 覆蓋同一 tag**。用來：

- 開發環境一直拉 `:feature-xyz` 看最新
- staging 拉 `:main` 看最新 pre-release

缺點：不可追蹤（tag 會被覆蓋），不適合生產。

### 3. Semver tag

```
ghcr.io/you/tasktrack:v0.1.0
ghcr.io/you/tasktrack:v0.1
ghcr.io/you/tasktrack:latest
```

**release 時打**。`v0.1.0` 跟對應 sha 永遠綁在一起，跟 GitHub Release 對應。Ch 13 處理。

`:latest` 爭議很多 — 有人覺得方便，有人覺得鬼影幢幢（不知道 latest 是哪版）。**生產請不要用 `:latest`**，dev 無所謂。

### 建議組合

| 場景 | tag |
|---|---|
| main merge | `:sha-<7chars>` + `:main` |
| tag push `vX.Y.Z` | `:vX.Y.Z` + `:vX.Y` + `:latest`（選） |
| feature branch | `:sha-<7chars>` + `:feature-xyz`（選） |

## `docker/build-push-action`

這是 GitHub Actions 裡 build + push image 的事實標準：

```yaml
- uses: docker/setup-buildx-action@v3

- uses: docker/login-action@v3
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

- uses: docker/build-push-action@v6
  with:
    context: .
    push: true
    tags: |
      ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
      ghcr.io/${{ github.repository }}:main
    cache-from: type=gha
    cache-to: type=gha,mode=max
    platforms: linux/amd64,linux/arm64
```

幾個 key input：

| 參數 | 用途 |
|---|---|
| `context` | Dockerfile 的位置（通常 `.`） |
| `push` | 是否真 push（PR 階段 `false`、merge 後 `true`） |
| `tags` | 支援多行，每行一個 tag |
| `cache-from/to` | Ch 8 教過的 GHA cache |
| `platforms` | 多平台（不要每次都跑 arm64，build 時間乘以 2） |

### `docker/metadata-action`：自動產 tag

手寫 tag 邏輯煩。`metadata-action` 幫你：

```yaml
- id: meta
  uses: docker/metadata-action@v5
  with:
    images: ghcr.io/${{ github.repository }}
    tags: |
      type=ref,event=branch         # 產 :branch-name
      type=ref,event=pr             # 產 :pr-123
      type=sha,prefix=sha-          # 產 :sha-a1b2c3d
      type=semver,pattern={{version}}        # tag push 時產 :1.2.3
      type=semver,pattern={{major}}.{{minor}}  # :1.2
      type=raw,value=latest,enable={{is_default_branch}}

- uses: docker/build-push-action@v6
  with:
    tags: ${{ steps.meta.outputs.tags }}
    labels: ${{ steps.meta.outputs.labels }}
```

`steps.meta.outputs.tags` 會根據觸發事件自動產多行 tag。**強烈建議用**。

## 實作：把 `tasktrack` push 到 GHCR

### Step 1：在 workflow 加 `build-push` job

拿 Ch 10 那個 reusable workflow 擴充（或者直接寫在 `ci.yml` 裡也行，這章先直接寫）：

```yaml
# .github/workflows/cd.yml
name: CD

on:
  push:
    branches: [main]

permissions:
  contents: read
  packages: write           # ← 推 GHCR 必需

concurrency:
  group: cd-${{ github.ref }}
  cancel-in-progress: false  # deploy 不 cancel

jobs:
  build-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=sha,prefix=sha-
            type=ref,event=branch
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

把這份存在 `.github/workflows/cd.yml`（分開 CI 和 CD 的 workflow，概念上更清晰）。

### Step 2：push main 觸發

```bash
git add .github/workflows/cd.yml
git commit -m "cd: build and push to ghcr"
git push origin main
```

到 Actions 看 `CD` workflow run。等它綠。

### Step 3：驗證 image

到你的 GitHub Profile 或 repo 旁邊的 **Packages** 頁，會看到 `tasktrack` package。

local pull 試試看：

```bash
# 先登入（用 fine-grained PAT 或 gh token）
echo $GHCR_TOKEN | docker login ghcr.io -u <your-user> --password-stdin

# Pull 你剛 push 的
docker pull ghcr.io/<your-user>/tasktrack:sha-<7chars>
docker run --rm -p 8000:8000 ghcr.io/<your-user>/tasktrack:sha-<7chars>
curl localhost:8000/healthz
```

### Step 4：Image 可見性

剛 push 的 package 在 GHCR 預設 **private**。你可以到 package 設定：

- Visibility：Public 或 Private
- 連結到 repo（管理方便）

本課先設成 **Public**（免去 auth 麻煩），正式專案看情況。

## PR 也要 build，但不 push

pull_request 時 build 是為了「驗證 Dockerfile 沒改壞」，但不該 push（fork PR 沒權限 + 垃圾 image）。

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: ${{ github.event_name == 'push' }}   # ← PR 時只 build
          tags: tasktrack:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

**但要注意**：PR 階段要 push 的話，還要另外做 login + permissions 處理。最乾淨是 CI 只 build 驗證，CD（main push）才 login + push — 也就是我們上面那個 `cd.yml` 的設計。

## image size 監控

每次 build 後看看 image 有沒有失控：

```yaml
- name: Image size
  run: |
    docker image ls ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
```

或加個 action：

```yaml
- uses: ghcr.io/someorg/image-size-check@v1
  with:
    image: ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
    max-size: 150MB
```

超過就紅。避免不小心把依賴加回 runtime stage。

## 動手練習

1. 新建 `.github/workflows/cd.yml`（如上）
2. 推到 main，Actions 跑完後去 Packages 頁確認 image 存在
3. 本地 `docker pull`，跑起來打 healthz
4. 故意把 Dockerfile 改壞（比如 CMD 寫錯），push，看 CD job 會紅
5. 修正、再 push，看新 image 有 `sha-<新 sha>` tag

## 常見誤解

- 「**`push: true` 會 push 所有 tag**」 — 對。所以 tag 要想清楚
- 「**`permissions: packages: write` 是 repo-level**」 — 是 workflow/job level。不寫就沒權限
- 「**PR 能 push 到 GHCR**」 — 從 fork 的 PR 拿不到 token。這是安全設計
- 「**多平台 build 一定要 QEMU 很慢**」 — 不一定，用 native ARM runner 就快。GitHub 有 `ubuntu-24.04-arm` runner
- 「**`:latest` 永遠是最新**」 — 不一定。要你 push 時帶 `:latest` tag。別忘了或別期待 registry 自動搞

## 驗收標準

- [ ] `.github/workflows/cd.yml` 有且能跑
- [ ] main push 後，GHCR 看得到 tag 為 `sha-<7chars>` + `main` 的 image
- [ ] 本地能 `docker pull` 並 run 起來
- [ ] Package 可見性設對（public 或 private）
- [ ] `permissions: packages: write` 有寫

## 自我檢核

- [ ] 我分得清 Docker Hub / GHCR / ECR 用途
- [ ] 我設計得出合理 tag 策略（sha 必備、branch 選用、semver 給 release）
- [ ] 我會用 `docker/metadata-action` 自動產 tag
- [ ] 我懂 CI 用 build 驗證、CD 才 push 的分工
- [ ] 我知道 PR 從 fork 來拿不到 packages write token

下一章處理 release：怎麼打 tag 觸發一個正式版本，連動 GitHub Release 頁面和 image semver tag。

→ [Ch 13 Release automation](./13-release-automation.md)
