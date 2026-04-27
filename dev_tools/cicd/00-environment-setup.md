# Ch 0 — 環境搭建

> 目標：把 Docker、Buildx、GitHub CLI 都裝好驗證過，拿到 `tasktrack` 初始專案並跑起來。

這章不講原理，純粹把工具就位。**驗收標準勾滿就直接走 Ch 1，別在這裡折騰超過一個下午**。這本身就是這門課的第一個 milestone 練習。

## 要裝的三個工具

| 工具 | 為什麼 | 驗證指令 |
|---|---|---|
| Docker Engine + Buildx | 建 image、跑 container | `docker version` / `docker buildx version` |
| GitHub CLI (`gh`) | 之後管 secret、開 PR、看 Actions log | `gh auth status` |
| Python 3.12 | `tasktrack` 用它 | `python --version` |

## Docker

Mac / Windows 直接裝 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。Linux 裝 Docker Engine 即可（Docker Desktop 在 Linux 是選配）。

### 驗證

```bash
docker version
docker run --rm hello-world
```

看到 `Hello from Docker!` 就通了。這一步失敗，多半是 daemon 沒啟動（Desktop 要打開那隻鯨魚）或你的 user 不在 `docker` group（Linux）。

### Buildx

新版 Docker Desktop / Engine 內建 Buildx：

```bash
docker buildx version
# github.com/docker/buildx v0.12.1 ...
```

版本 0.10+ 都能跑，**不要糾結小版本**。

### 踩雷提醒

- **Windows 一定要用 WSL2 後端**：Docker Desktop 跑在 WSL2 裡效能才正常。WSL2 的終端跟 Docker Desktop 共享 daemon，不要在 cmd.exe 跟 WSL2 之間切來切去混用路徑，會痛。
- **Apple Silicon (M1/M2/M3)**：預設 build 的是 `linux/arm64`。如果你 pull 的 image 只有 `linux/amd64`，Docker 會用 QEMU 模擬，慢。Ch 5 會教怎麼處理，現在別擔心。

## GitHub CLI

```bash
# Mac
brew install gh

# Windows
winget install --id GitHub.cli

# Linux（Debian/Ubuntu，其他發行版見官方文件）
# https://github.com/cli/cli/blob/trunk/docs/install_linux.md
```

登入：

```bash
gh auth login
# 選 GitHub.com → HTTPS → 瀏覽器登入
```

驗證：

```bash
gh auth status
# ✓ Logged in to github.com as <你>
```

## Python 3.12

用你熟的方式（`pyenv`、`asdf`、`uv`、conda、系統套件管理器）都行。驗證：

```bash
python --version
# Python 3.12.x
```

如果你系統 `python` 是 Python 2（舊 Mac、某些 Linux），自己心裡換成 `python3`。

## 拿到 `tasktrack` 初始專案

起始碼放在本 repo 的 `cicd/tasktrack/`。**複製一份到另一個獨立目錄當你的 repo**，因為後面要 `git init`、推到 GitHub、跑 Actions — 你不會想讓整套流程跑在 `cicd` 的子目錄裡。

```bash
# 放哪都行，這裡假設 ~/projects
cp -r cicd/tasktrack ~/projects/tasktrack
cd ~/projects/tasktrack
```

### 專案結構

```
tasktrack/
├── README.md
├── requirements.txt         # runtime 依賴
├── requirements-dev.txt     # 開發/測試依賴
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口、3 個 endpoint
│   ├── db.py                # SQLAlchemy 引擎（先用 SQLite，Ch 4 換 Postgres）
│   └── models.py            # Task 資料模型
└── tests/
    ├── __init__.py
    ├── conftest.py          # 測試用 DB fixture
    └── test_tasks.py        # 4 個測試
```

**注意**：Ch 0 故意讓 `tasktrack` 用 SQLite，**不是** PostgreSQL。Ch 4 學 docker-compose 時才會升級。先讓它能在 laptop 上直接跑最重要。

### 本地跑起來

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

uvicorn app.main:app --reload
# INFO:     Uvicorn running on http://127.0.0.1:8000
```

另開一個終端：

```bash
curl -X POST localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"title": "學 Docker", "milestone": "week-1"}'
# {"id":1,"title":"學 Docker","milestone":"week-1","done":false}

curl localhost:8000/tasks
# [{"id":1,"title":"學 Docker","milestone":"week-1","done":false}]

curl -X PATCH localhost:8000/tasks/1/complete
# {"id":1,"title":"學 Docker","milestone":"week-1","done":true}
```

### 跑測試

```bash
pytest -v
# tests/test_tasks.py::test_create_task PASSED
# tests/test_tasks.py::test_list_tasks PASSED
# tests/test_tasks.py::test_complete_task PASSED
# tests/test_tasks.py::test_complete_missing_task PASSED
```

## 驗收標準

這是每章都會有的東西。**勾滿就往下走，別停**。

- [ ] `docker run --rm hello-world` 能輸出 `Hello from Docker!`
- [ ] `docker buildx version` 顯示版本（≥ 0.10）
- [ ] `gh auth status` 顯示已登入
- [ ] `python --version` 是 3.12.x
- [ ] `tasktrack` 複製到獨立目錄、venv 裝好、`pytest` 四個測試全綠
- [ ] `uvicorn app.main:app --reload` 起得來、`curl` POST + GET + PATCH 三組都有正確回應

## 這章刻意不做的事

**這份清單比你想像中重要**。Ch 0 的範圍已經被壓到最小，你如果發現自己在做以下事情，立刻停手：

- ~~**不寫 Dockerfile**~~：Ch 2 才開始。
- ~~**不 `git init`**~~：Ch 6 真正碰 GitHub Actions 前再處理。
- ~~**不追求 Python 依賴精確 pin**~~：`>=` 能跑就好，Ch 2 會談 reproducibility。
- ~~**不接 PostgreSQL**~~：Ch 4 的事。
- ~~**不補 mypy / ruff 設定檔**~~：Ch 6 Part 2 再說。

## 自我檢核

- [ ] 我知道這門課會用哪三個核心工具、各自角色
- [ ] `tasktrack` 的範圍我大致看得懂（3 個 endpoint + 4 個測試）
- [ ] 我接受這章 scope 被刻意壓縮 — Docker 原理、CI 機制都是後面的事
- [ ] **最重要**：我驗收勾完就直接往下走，沒偷偷開始補 Ch 2 的東西

工具都就位了。下一章談容器到底在解什麼問題 — 不是「輕量 VM」那個爛比喻。

→ [Ch 1 容器到底在解什麼問題？CI/CD 全貌](./01-why-containerize.md)
