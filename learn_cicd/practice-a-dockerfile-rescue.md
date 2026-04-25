# 練習 A — 把爛 Dockerfile 優化到生產等級

> 目標：給你一個故意寫壞的 Dockerfile（1.2GB、build 慢、root、單平台），把它優化到 <100MB、cache hit <30 秒、non-root、多平台。

這練習把 Ch 2–5 全部綜合。動手前先看一次 Ch 3、Ch 5 的最終 Dockerfile 提示自己。

## 任務規格

### 原始（糟糕）Dockerfile

下面這份 Dockerfile 假設寫給一個 Flask + SQLAlchemy + Redis 的小 API `ordermgr`。複製到一個新目錄開始改：

```dockerfile
# Dockerfile.bad — 這是起點，你要改成優化版
FROM python:3.12

ADD https://files.pythonhosted.org/packages/xx/pip-24.0.tar.gz /tmp/pip.tar.gz

RUN apt-get update
RUN apt-get install -y curl wget vim git build-essential libpq-dev
RUN apt-get install -y postgresql-client
RUN apt-get install -y default-jdk     # 根本用不到但複製貼上留下的

WORKDIR /app

COPY . /app

RUN pip install flask flask-sqlalchemy psycopg2-binary redis gunicorn
RUN pip install pytest pytest-cov mypy ruff black ipython    # dev deps 不該進 runtime

RUN python -c "print('dummy build step')"

EXPOSE 5000

CMD python -m flask run --host=0.0.0.0 --port=5000
```

問題清單（你應該能一眼看出幾個）：

1. Base image 是 `python:3.12`（1GB+），應該用 slim
2. 每個 `apt-get install` 是獨立 layer、沒清 lists、裝了不需要的 Java
3. `COPY . /app` 把 `.git`、`.venv`、測試全複製進去（沒 `.dockerignore`）
4. dev 依賴（pytest、mypy、ruff）不該在 runtime
5. dependency 沒 pin 版本（`pip install flask` 而不是 `flask==X.Y`）
6. 沒 multi-stage
7. 以 root 跑
8. 沒 HEALTHCHECK
9. `CMD` 是 shell form（SIGTERM 會被吃掉）
10. `ADD` 下載遠端 URL（可能且通常危險）

### 你的產出：`Dockerfile`（優化版）+ `.dockerignore`

**假設** `ordermgr` 的實際目錄結構：

```
ordermgr/
├── app/
│   ├── __init__.py
│   ├── main.py
│   └── models.py
├── tests/
│   ├── conftest.py
│   └── test_api.py
├── requirements.txt         # runtime 依賴
├── requirements-dev.txt     # 測試、lint 工具
└── .gitignore
```

`requirements.txt`（你要 pin）：

```
flask==3.0.3
flask-sqlalchemy==3.1.1
psycopg[binary]==3.2.3
redis==5.2.1
gunicorn==23.0.0
```

`requirements-dev.txt`：

```
-r requirements.txt
pytest==8.3.4
pytest-cov==6.0.0
mypy==1.13.0
ruff==0.8.1
```

### 驗收標準

| 項目 | 目標 |
|---|---|
| Image 大小 | < 100MB |
| 完整 build 時間 | < 60 秒（冷 cache） |
| 改 `app/main.py` 一行 rebuild 時間 | < 5 秒 |
| 容器 user | UID 不是 0 |
| HEALTHCHECK | 有、能跑 |
| 平台 | amd64 + arm64 都能 build |
| `.dockerignore` | 有，排除 `.git`、`.venv`、tests、`__pycache__` 等 |
| runtime 依賴 | pin 精確版本 |
| dev 依賴 | 不在 final image 裡 |
| `CMD` | exec form（JSON 陣列） |

## 實作步驟建議

### Step 1：先 build 一次糟糕版，記錄基線

```bash
docker build -f Dockerfile.bad -t ordermgr:bad .
docker images ordermgr
# 預期：~1.3GB
```

記錄時間。改完才有參考。

### Step 2：加 `.dockerignore`

先處理 build context。排除所有不該進 image 的東西。

### Step 3：改 base image + 合併 apt

用 `python:3.12-slim`，apt-get 指令合併成一條、加 `rm -rf /var/lib/apt/lists/*`。砍掉不需要的套件。

### Step 4：拆 multi-stage

- builder stage：裝 build-time deps（psycopg 的 build essentials、libpq-dev）、pip install requirements
- runtime stage：只複製 venv

### Step 5：COPY 順序 + pin 版本

用 `requirements.txt` 當 cache 層的 anchor。pin 精確版本。

### Step 6：non-root + HEALTHCHECK + exec form

加 `USER`、HEALTHCHECK、改 CMD 為 JSON 陣列。

### Step 7：驗證 multi-platform

```bash
docker buildx create --name multi --use 2>/dev/null || docker buildx use multi
docker buildx build --platform linux/amd64,linux/arm64 -t ordermgr:v1 .
```

### Step 8：對比

```bash
docker images ordermgr
# ordermgr  v1    ~90MB
# ordermgr  bad   ~1.3GB
```

## 完整參考解答

**寫完再看！** 自己卡住 30 分鐘以上再偷看。

<details>
<summary>點開參考 Dockerfile</summary>

```dockerfile
# syntax=docker/dockerfile:1.7

# ========== builder stage ==========
FROM python:3.12-slim AS builder

# 裝編譯需要的套件（psycopg 以 binary wheel 給的話也可省掉 libpq-dev）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 先 COPY requirements — cache 友善
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ========== runtime stage ==========
FROM python:3.12-slim

# runtime 需要的 shared lib（psycopg 連 Postgres 時用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=1000:1000 app/ app/

USER 1000

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz').read()" || exit 1

# gunicorn exec form
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app.main:app"]
```

</details>

<details>
<summary>點開參考 .dockerignore</summary>

```
.git
.gitignore
.venv
venv
__pycache__
*.py[cod]
.pytest_cache
.mypy_cache
.ruff_cache
.coverage
htmlcov
*.db
*.db-journal
.env
.env.*
tests/
Dockerfile*
.dockerignore
README.md
.vscode
.idea
```

</details>

### 解答要點

- `libpq5` vs `libpq-dev`：前者是 runtime shared lib（~1MB），後者是 build header（~8MB）。runtime stage 只要前者
- `--mount=type=cache` 可以再加速（BuildKit 功能），但這章沒教，參考解暫不用
- `gunicorn --workers 2` 是常規 Python WSGI 部署（Flask 本身只是開發伺服器，不能上生產）
- HEALTHCHECK 用 Python 而不是 curl，省 ~10MB

## 測試用例

用這些驗收你的解答：

```bash
# 1. Image 大小
docker images ordermgr:v1 --format "{{.Size}}"
# 應 < 100MB

# 2. 冷 cache 時間
docker buildx prune -f
time docker build -t ordermgr:v1 .
# 應 < 60s

# 3. 熱 cache（改 app/main.py 一行）時間
echo "# touched" >> app/main.py
time docker build -t ordermgr:v1 .
# 應 < 5s

# 4. User 不是 root
docker run --rm ordermgr:v1 id
# uid=1000(...)

# 5. HEALTHCHECK
docker run -d --name check -p 5000:5000 ordermgr:v1
sleep 10
docker inspect --format='{{.State.Health.Status}}' check
# healthy
docker rm -f check

# 6. Multi-platform
docker buildx build --platform linux/amd64,linux/arm64 -t ordermgr:v1 .
# 不報錯
```

## 自我檢核

- [ ] 我知道每項優化**為什麼**要做（不是照抄）
- [ ] 我能一眼看出新 Dockerfile 哪幾層會 cache miss、哪幾層會 hit
- [ ] 我理解 build stage 跟 runtime stage 的分界：**編譯 / 打包相關** 留 builder、**跑起來需要** 留 runtime
- [ ] 我知道 `libpq-dev` 和 `libpq5` 差別、什麼時候要 runtime 也裝 shared lib

Part 1 結束。接下來進 GitHub Actions — 我們會發現前面學的 Docker 知識在 CI 裡到處要用。

→ [Ch 6 workflow 檔案結構](./06-workflow-structure.md)
