# Final Project — 把 tasktrack 完整生產化

> 目標：把 `tasktrack` 從 Ch 0 的起始狀態，推成一個有完整 CI/CD、可 `docker pull + run` 的生產級服務。**限時一個週末**。勾滿 checklist 就算完成，不追求完美。

## 為什麼這個 final project 這樣設計

這門課的動機是：對抗「在技術細節打轉導致拖延」。這個 final project 本身就是最終考驗：**一個週末，不多不少**。

- 時間到就交。checklist 沒勾滿 → 把未勾項記在 `TODO.md`，之後版本處理
- 「差一點點想再優化」是陷阱。完美主義在這裡是 bug，不是 feature
- 交付的版本 = 你的 `v0.1.0` Release

讀完這個檔前先買好這週末的食物。

## 專案範圍

從 Ch 0 的 `tasktrack` 起始版（FastAPI + SQLite + 4 tests），把它推到：

- 完整 CI：PR 觸發 lint、typecheck、unit、integration 四個平行 job、cache 夠快、`all-green` gate
- 完整 CD：merge main → build 並 push 到 GHCR、tag `:sha-xxx` + `:main`
- 完整 Release：打 tag → semver image + GitHub Release 頁
- 安全：Trivy 掃、Dependabot 配置、CODEOWNERS
- 可用：GHCR pull 下來能跑

## 驗收 checklist（勾滿即完成）

### A. 程式碼與結構

- [ ] `tasktrack` 在自己的 GitHub repo、不在 `cicd/` 裡面
- [ ] `app/` 有 `main.py` + `db.py` + `models.py`，加了 `/healthz` endpoint
- [ ] `tests/` 分成 `unit/` 與 `integration/` 兩個子目錄
- [ ] `requirements.txt` + `requirements-dev.txt`，runtime 與 dev 分開
- [ ] 可選：`pyproject.toml` 集中 ruff / mypy / pytest 設定
- [ ] `.gitignore` + `.dockerignore` 都有、排除該排的

### B. Docker

- [ ] `Dockerfile` multi-stage（builder + runtime）
- [ ] Base image 是 `python:3.12-slim`
- [ ] `USER 1000`（non-root）
- [ ] `HEALTHCHECK` 指令在
- [ ] `CMD` 用 exec form（JSON 陣列）
- [ ] Image 大小 < 200MB
- [ ] 改一行 code rebuild < 5 秒（cache 命中）
- [ ] `docker-compose.yml` 能跑 app + Postgres，本地 `docker compose up` 起得來
- [ ] 可選：multi-platform build 能跑 `amd64 + arm64`

### C. CI（`ci.yml`）

- [ ] `on: pull_request + push: main`
- [ ] `concurrency` cancel-in-progress
- [ ] `permissions: contents: read` 最小化
- [ ] `lint`、`typecheck`、`unit`（matrix 3.11+3.12）、`integration`（service container）四個 job 平行
- [ ] `integration` 用 Postgres service container
- [ ] 有 composite action `setup-python-env` 把環境設置抽出來
- [ ] `all-green` 匯總 gate，`if: always()` + `contains` 判斷
- [ ] 第二次 run（cache warm）**5 分鐘內完成**
- [ ] Coverage 上傳 artifact

### D. CD（`cd.yml`）

- [ ] `on: push: branches: main`
- [ ] `permissions: packages: write`
- [ ] 用 `docker/metadata-action` 產 tag（`:sha-xxx` + `:main`）
- [ ] 用 `docker/build-push-action` + `cache-from/to: gha`
- [ ] Push 後 GHCR Packages 頁看得到新 image

### E. Release（`release.yml`）

- [ ] `on: push: tags: 'v*'`
- [ ] `permissions: contents: write + packages: write`
- [ ] 用 `metadata-action` 的 `type=semver` 產 `:0.1.0` + `:0.1` + `:latest`
- [ ] 用 `softprops/action-gh-release` 自動建 Release 頁面
- [ ] 打了至少 **一次** `v0.1.0` tag、GitHub Releases 頁面有這個版本

### F. 安全

- [ ] `security.yml` Trivy 掃 image + fs，上傳 SARIF
- [ ] `.github/dependabot.yml` 配 pip + docker + github-actions 三個 ecosystem
- [ ] `.github/CODEOWNERS` 寫了自己（或 team）
- [ ] Repo Settings → Branches → `main` 的 branch protection：
  - [ ] Require PR
  - [ ] Require status: `all-green`
  - [ ] Require review（可選，小專案選配）

### G. 可用性驗證

- [ ] 本地從零：`docker pull ghcr.io/<you>/tasktrack:0.1.0 && docker run` 能跑
- [ ] `curl` 三個 endpoint + `/healthz` 都通
- [ ] Repo README 更新（至少：專案 pitch、怎麼 pull + run、一張 pipeline 圖）

### H. 文件（10 分鐘搞定）

- [ ] Repo 首頁 README 寫：這專案做什麼（3 句）、最快怎麼用（1 條 docker run 指令）
- [ ] `TODO.md` 寫：checklist 裡未勾的項目、已知問題、下個版本想做什麼

## 建議週末時程

### 週六上午（3–4 小時）

- Docker 那邊收尾：`Dockerfile` + `docker-compose.yml` + `USER` + `HEALTHCHECK`
- 測試重整：切 `unit/` 和 `integration/`
- `/healthz` endpoint 加好

→ Milestone：本地 `docker compose up` 起來 + 所有 pytest 綠

### 週六下午（3–4 小時）

- 寫 `ci.yml` + composite action
- 寫 `cd.yml`
- Push 到 GitHub、反覆修 YAML 直到綠

→ Milestone：main push 後 GHCR 有 image

### 週日上午（3–4 小時）

- 寫 `release.yml`
- 打第一個 tag `v0.1.0`
- 修 release workflow

→ Milestone：GitHub Releases 有 v0.1.0 頁面 + GHCR 有 `:0.1.0` `:latest`

### 週日下午（2–3 小時）

- 寫 `security.yml` + Dependabot + CODEOWNERS
- 配 branch protection
- 寫 README + TODO.md
- **停**，交付版本

→ Milestone：`docker pull` 能用、README 完整

## 如果時間爆了怎麼辦

按優先順序砍。以下是「**這週末放棄可接受**」清單：

- CODEOWNERS（可後續補）
- multi-platform build（只 amd64 也能用）
- SBOM、image signing（企業級才要）
- Trivy fs scan（image scan 保留即可）
- matrix 3.11（只跑 3.12）
- coverage 上傳 artifact（不影響功能）

**不能砍**：

- 至少一個綠的 CI run
- GHCR 上要有一個跑得起來的 image
- 能 docker pull + run

## 交付後

**寫個 post-mortem**（給自己看），包含：

1. 花了幾小時（預期 vs 實際）
2. 哪三件事卡最久
3. 哪些 Ch 0–15 的章節事後發現沒吸收好
4. 下次做類似專案會先做什麼

這是 **這門課最寶貴的產出**之一。

## 繳交與通關

這門課沒考試。但如果你要證明自己「學會 CI/CD + 容器化」，交付物是：

- 一個 `tasktrack` GitHub repo URL
- 一個 GHCR image pull 指令（人家能跑）
- 一個 `v0.1.0` Release URL
- 一張你的 pipeline 拓樸圖（貼 README）

這就是你可以放簡歷的 portfolio item。

## 結尾話

這門課的設計意圖：你碰過了 Docker、GitHub Actions 的所有核心機制，而且你用一個完整 project 把它串起來。關鍵不是「每個細節都熟」，是「整張圖你看得懂」。

未來遇到新需求（換 registry、部署到 K8s、加 observability），你不會從零想 — 你知道 pipeline 的骨架、知道該補哪一塊。

接下來 `tasktrack` 就交給你了。下禮拜開始的第五門課見。
