# Ch 13 — Release automation

> 目標：讓 `git tag vX.Y.Z && git push` 自動觸發 workflow，產出 GitHub Release、對應 semver image tag、生 changelog。

## 為什麼 release 要自動化

兩個層次：

1. **實用層**：每次手動點 GitHub UI 建 Release 會忘、會打錯版本、會漏上傳 artifact
2. **心態層**：這是回應你核心動機的工具 — **「打 tag」本身就是外部強制的 milestone**

> 打不下 `v0.1.0` 的 tag = 這版還不能交。
> 打得下 = 把現況釘死，下一版從這裡 fork 新任務。

這章教你把 release 這件事變成「一條命令」。

## 觸發：`on: push: tags`

Workflow 監聽 tag push：

```yaml
on:
  push:
    tags:
      - 'v*'             # v0.1.0、v1.0、v1.2.3-rc.1 都會觸發
```

幾件事：

- Tag push 的 `github.ref` 是 `refs/tags/v0.1.0`
- `github.ref_name` 是 `v0.1.0`
- `on: push` 可以 **branch 和 tag 同時列**，但 tag 只會在 tag push 觸發，不會在 branch push 觸發

```yaml
on:
  push:
    branches: [main]       # main merge
    tags: ['v*']           # tag push
```

## Semver 策略

**Semantic Versioning** 是慣例，長這樣：`MAJOR.MINOR.PATCH`。

- **MAJOR**：break compatibility
- **MINOR**：加新功能，向下相容
- **PATCH**：bug fix

**怎麼決定下一版**：

| 變更 | 版號動作 |
|---|---|
| API 破壞性（endpoint 改名、欄位改型別） | MAJOR +1，其他歸零 |
| 新 endpoint、新選項、新功能 | MINOR +1，PATCH 歸零 |
| Bug fix、文件、內部重構 | PATCH +1 |

pre-1.0 時放寬：有人主張「<1.0 什麼都可以破」，也有人還是守。選一個風格、團隊內一致即可。

### 打 tag 的實務

```bash
git tag -a v0.1.0 -m "first release"      # annotated tag 附訊息
git push origin v0.1.0

# 或省事
git tag v0.1.0
git push origin v0.1.0
```

**annotated** vs **lightweight** 差別：

- Lightweight（`git tag v0.1.0`）：只是個指針
- Annotated（`-a`）：像小 commit，有作者、時間、訊息

大部分 release 工具兩個都接受，但 annotated 比較 formal。

### 想避免手打 tag？

用 `gh release create v0.1.0 --generate-notes` 一條命令打 tag + 建 Release。進階玩法：`release-please`、`semantic-release` 從 commit message 自動算版號，但這課先不上。

## GitHub Release

GitHub 的 Release 頁面 = tag + 一篇描述 + 可選的 artifact 附件。`softprops/action-gh-release` 幫你自動化：

```yaml
- uses: softprops/action-gh-release@v2
  with:
    tag_name: ${{ github.ref_name }}
    name: Release ${{ github.ref_name }}
    generate_release_notes: true         # GitHub 幫你產 changelog
    files: |
      dist/*.whl
      dist/*.tar.gz
```

`generate_release_notes: true` 會自動從 commits 組 release notes（分類 PR、標作者）。很方便，前提是你 commit message 寫得不要太糟。

## 實作：完整 release workflow

新增 `.github/workflows/release.yml`：

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write           # ← 建 GitHub Release 要 write
  packages: write           # ← push image 要 write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0                 # ← release notes 要 git history

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
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest

      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha

      - uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ github.ref_name }}
          name: Release ${{ github.ref_name }}
          generate_release_notes: true
          prerelease: ${{ contains(github.ref_name, '-rc') || contains(github.ref_name, '-beta') }}
```

### 這份 workflow 幹了這些事

1. Checkout（`fetch-depth: 0` 是因為 release notes 要看整個 history）
2. Login GHCR
3. 用 `metadata-action` 從 tag `v0.1.0` 自動產生 `:0.1.0`、`:0.1`、`:latest` 三個 image tag
4. `docker/build-push-action` push 三個 tag 的 image（多平台）
5. `softprops/action-gh-release` 建 GitHub Release 頁面 + 自動 changelog

### 觸發

```bash
git tag -a v0.1.0 -m "first release"
git push origin v0.1.0
```

等個 3–5 分鐘（multi-platform build 慢），去看：

- Actions 有 `Release` run
- Packages → `tasktrack` 有 `:0.1.0`、`:0.1`、`:latest` 三個 tag
- GitHub repo 首頁 → Releases 看到 `v0.1.0`

## 版號卡在 changelog 的兩個做法

### A. 手寫 CHANGELOG.md

repo 根目錄有 `CHANGELOG.md`，每次 release 前你手動更新。release workflow 從中截取當前版號的段落。這派相信：changelog 是給人讀的，應該人寫。

### B. 全自動（`generate_release_notes: true`）

GitHub 抓 PR title 組 release notes。分類（feature / fix / chore）靠 PR label 或 `.github/release.yml` 配置：

```yaml
# .github/release.yml
changelog:
  categories:
    - title: Features
      labels: ["enhancement", "feature"]
    - title: Bug Fixes
      labels: ["bug", "fix"]
    - title: Other
      labels: ["*"]
```

**這課用 B**。入門、成本低。

## 發 pre-release

pre-release（`v1.0.0-rc.1`、`v1.0.0-beta`）用 `-` 加後綴：

```bash
git tag v1.0.0-rc.1
git push origin v1.0.0-rc.1
```

workflow 裡 `prerelease: ${{ contains(github.ref_name, '-rc') }}` 會讓 Release 頁標為「pre-release」（不會顯示在 repo 首頁的 release 卡片，除非展開 All releases）。

## 要不要 `:latest`？

三個立場：

- **一律打 `:latest`**：方便 dev、demo、README 例子。缺點：生產不可追蹤
- **從不打 `:latest`**：嚴格派。所有 pull 強制帶版號
- **只在 stable release 打**：pre-release 不更新 `:latest`

我推薦最後那個。上面 workflow 的 `type=raw,value=latest` 無條件打了 — 如果你嚴格，可以加條件：

```yaml
type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}
```

## 動手練習

1. 新建 `.github/workflows/release.yml`（如上）
2. Push 到 main（這個 workflow 不會 trigger）
3. 打 tag `v0.1.0` 並推
4. 到 Actions 看 `Release` run，跑完看 Packages 和 Releases
5. 打個 pre-release `v0.2.0-rc.1` 試試看 prerelease 標記
6. 本地 `docker pull ghcr.io/<you>/tasktrack:0.1.0`、`:latest`，確認都拉得到

## 常見誤解

- 「**tag push 也會 trigger `on: push: branches`**」 — 不會。tag push 只符合 `on.push.tags`
- 「**`generate_release_notes` 只看最新 commits**」 — 它看「上一個 release tag」到「這個 tag」的區間
- 「**`:latest` 會自動指向最新 tag**」 — 沒那回事。你 push 時要自己帶 `:latest`
- 「**pre-release 的 image 不該 push 到 registry**」 — 要。只是 Release 頁面標 prerelease，image 還是要有（給 beta tester 拉）
- 「**`--generate-notes` 可以改內容**」 — 可以，release 建好後去 Edit。或者用 `body:` input 完全自寫

## 驗收標準

- [ ] `.github/workflows/release.yml` 存在
- [ ] `git tag v0.1.0 && git push origin v0.1.0` 會自動 trigger workflow
- [ ] Workflow 跑完後：
  - [ ] GHCR 有 `:0.1.0`、`:0.1`、`:latest` 三個 tag
  - [ ] GitHub Releases 有 `v0.1.0` 頁面，內容有自動產的 changelog
- [ ] 打 pre-release tag（`v0.2.0-rc.1`），Release 被標 prerelease

## 自我檢核

- [ ] 我知道 semver 三位版號各自意義
- [ ] 我會用 `on: push: tags: 'v*'` 監聽 tag push
- [ ] 我會用 `docker/metadata-action` 的 `type=semver,pattern={{version}}` 自動產 image tag
- [ ] 我會用 `softprops/action-gh-release` 建 Release 頁面
- [ ] 我認同「打 tag 是把這版釘死的 milestone」這個思維

下一章處理一個常被問但實務上建議少碰的主題：self-hosted runner，以及 deploy 本身的那一哩路。

→ [Ch 14 Self-hosted runner 與部署](./14-self-hosted-runner.md)
