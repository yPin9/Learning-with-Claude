# Ch 3 — Multi-stage build：把 image 砍小

> 目標：搞懂 Ch 2 那張 1GB image 的脂肪在哪、怎麼用 multi-stage build 切出乾淨的 runtime，把 `tasktrack` 從 1GB+ 砍到 < 100MB。

## Ch 2 留的 1GB 是什麼

回看 `docker history tasktrack:v1`：

```
<missing>   RUN pip install ...          45MB
<missing>   COPY requirements.txt        < 1KB
<missing>   WORKDIR /app                 0B
<missing>   python:3.12 base             1.02GB     ← 這個
```

`python:3.12` 本身就 1GB。它是什麼？**一個 Debian bookworm 系統 + 完整 Python 工具鏈 + pip + setuptools + 你沒用到的一堆東西**。包括 gcc、make、git、vim、文件、locales⋯⋯。runtime 上只要有 Python 解釋器 + 你的依賴，其他全是脂肪。

兩個砍法，疊加用：

1. **換 base image**（簡單、大部分人第一招）
2. **Multi-stage build**（把 build-time 和 runtime 分開）

## 第一招：換 base image

常見 Python base：

| Tag | 大小 | 特點 |
|---|---|---|
| `python:3.12` | ~1 GB | 全功能 Debian + 所有工具 |
| `python:3.12-slim` | ~150 MB | 精簡 Debian，拿掉文件與非必要工具 |
| `python:3.12-alpine` | ~50 MB | Alpine（musl libc），**小但有地雷** |
| `gcr.io/distroless/python3` | ~50 MB | 只有 Python + libs，**沒 shell** |

**只改一行就賺 80%**：

```dockerfile
FROM python:3.12-slim                    # 從 python:3.12 改成這個
```

### Alpine 的坑：musl vs glibc

Alpine 用 musl libc（不是 glibc），這件事會咬你：

- 很多 Python wheel 只編譯 `manylinux`（glibc）版本，在 Alpine 上會 fallback 到 sdist 編譯 — **pip install 從 3 秒變 3 分鐘**，還要裝 `build-essential`
- 某些 C extension 有 musl 相容性 bug（`asyncpg`、`grpcio` 歷史都出過事）
- Debug 時 `strace` 行為不太一樣

**推薦**：預設用 `slim`。除非你精確知道自己要什麼，別上 Alpine。

### Distroless 是什麼

Google 做的一系列「沒 shell 的 image」：只有 runtime 需要的 lib。優點是安全性強（沒 `sh` 就沒人能 exec 進來亂搞），缺點是 `docker exec -it container /bin/sh` 永遠進不去，debug 痛。Ch 5 會再談，這章先不碰。

## 第二招：Multi-stage build

觀察一個事實：**build 時需要的東西，runtime 不需要**。

以 Python 為例，build 可能需要：
- `build-essential`（gcc + make，編譯 C extension）
- 某些 header（`libpq-dev` for psycopg）
- pip 的 wheel cache

Runtime 只需要：
- Python 解釋器
- 最終裝好的 packages
- 你的 code

Multi-stage build 的招：**用 A image 裝東西，裝完把裝好的東西 `COPY` 到 B image，丟掉 A**。

### 語法

```dockerfile
FROM python:3.12-slim AS builder         # ← AS <name> 給這 stage 取名
# ... 在這裡做各種髒事 ...

FROM python:3.12-slim                    # ← 第二個 FROM，開新 stage（前面的丟了）
COPY --from=builder /path /path          # ← 從 builder 複製過來
```

一個 Dockerfile **可以有多個 `FROM`**。最後一個 stage 是最終 image，前面的是「暫存工廠」。

## 實作：`tasktrack` 的 multi-stage 版本

```dockerfile
# ========== builder stage ==========
FROM python:3.12-slim AS builder

# 如果有需要編譯的 C extension，在這裡裝
# 對 tasktrack 來說不需要，示範用
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     build-essential \
#     && rm -rf /var/lib/apt/lists/*

# 建 venv，把依賴裝進去
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ========== runtime stage ==========
FROM python:3.12-slim

# 只拷 venv，不拷 builder 其他東西（如 apt cache、wheel cache）
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY app/ app/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build：

```bash
docker build -t tasktrack:v2 .
docker images tasktrack
```

你應該看到：

```
REPOSITORY    TAG    SIZE
tasktrack     v2     ~180MB       ← multi-stage + slim
tasktrack     v1     ~1.05GB      ← Ch 2 版本
tasktrack     v0     ~1.06GB      ← 天真版
```

從 1GB 砍到 180MB，**不到 20%**。Ch 5 會再用 distroless 砍到 80MB 左右，這章先停在這。

## 為什麼要 venv

有人會問：為什麼不直接 `pip install`？

```dockerfile
# 沒 venv 的版本
FROM python:3.12-slim AS builder
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
COPY --from=builder /install /usr/local
```

這樣也可以，而且 image 更小一點點。但 venv 有兩個好處：

1. **隔離乾淨**：venv 是一個目錄，`COPY /opt/venv` 一條就全搞定
2. **debug 方便**：runtime container 裡 `which python` 會是 `/opt/venv/bin/python`，跟 host / 其他 image 不會混

一般我推薦 venv 寫法。`--prefix` 寫法在極致瘦身時才會用。

## 依賴 pin：Ch 2 留的洞

Ch 2 提過：`RUN pip install -r requirements.txt` 的 cache key 只是字串，PyPI 上版本變了它不知道。

我們現在的 `requirements.txt`：

```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0
pydantic>=2.5
```

`>=` 意味著每次 cache miss 都可能裝到新版 — **不是 reproducible build**。生產環境絕對不能這樣。

### 解法：生 `requirements.lock`

最簡單的做法，用 `pip freeze` 或 `pip-compile`（pip-tools）生一個鎖檔：

```bash
pip install pip-tools
pip-compile requirements.txt -o requirements.lock
```

`requirements.lock` 會長：

```
fastapi==0.115.6
pydantic==2.10.3
pydantic-core==2.27.2
sqlalchemy==2.0.36
# ... 包括 transitive deps
```

Dockerfile 裡用：

```dockerfile
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
```

**這章不強求你做**（課程進度優先），但產品化時（Final Project）一定要做。或者你也可以用 `uv`、`poetry.lock`、`pdm.lock` — 任何鎖檔都行，重點是 `==`。

## BuildKit 與 `docker buildx`

Docker 20.10+ 預設 build 引擎是 **BuildKit**，輸出比舊的漂亮（平行化、快取管理更好）。`docker buildx build` 是它的 CLI 前端。

你平常 `docker build` 其實已經在用 BuildKit。差別只有：

```bash
docker build --progress=plain -t tasktrack:v2 .    # 看詳細 log
```

每條指令的實際執行輸出都會印出來，debug 用這招。Ch 5 的多平台 build 一定會用 `docker buildx`，這章先混著。

## 動手練習

1. 把 tasktrack Dockerfile 改成 multi-stage + slim base
2. Build，記錄 image 大小（`docker images tasktrack`）
3. 跑 `docker run --rm -p 8000:8000 tasktrack:v2`，`curl` 三個 endpoint 都確認能跑
4. 跑 `docker history tasktrack:v2`，看看 layer 現在長什麼樣
5. **Bonus**：試用 `pip-compile` 生 lockfile，換成 pin 版本再 build 一次

## 常見誤解

- 「**Multi-stage = 自動更小**」 — 不會自動，**你沒 `COPY --from=`** 的東西根本沒進 final image，但 builder 裡的那些 GB 一樣浪費 build 時間。想快就 builder 也用 slim
- 「**Alpine 一定比 slim 小**」 — image layer 是小，但 `pip install` 要編譯時的耗時讓整體變慢且大。多數情況 slim 勝出
- 「**Multi-stage 只能 2 層**」 — 可以很多層。例：`FROM node AS frontend-builder`、`FROM golang AS backend-builder`、`FROM distroless` 最後組起來
- 「**每層都要 `--no-cache-dir`**」 — 對 pip 是（避免留 wheel cache），對 apt 是（`rm -rf /var/lib/apt/lists/*`）。忘了會讓 image 多 50–200MB

## 驗收標準

- [ ] `tasktrack` Dockerfile 是 multi-stage（有 `AS builder` + 第二個 `FROM`）
- [ ] `docker images tasktrack:v2` 的 SIZE < 200MB
- [ ] Container 跑起來 API 打得通（POST、GET、PATCH）
- [ ] 改 `app/main.py` 一行 rebuild，只有 runtime stage 的 COPY 那層變，< 3 秒完成
- [ ] 你知道 slim 和 alpine 差在哪、自己這次為什麼選 slim

## 自我檢核

- [ ] 我能解釋 1GB image 的脂肪來自哪（base image + build-time tools + pip cache）
- [ ] 我能寫一個 2-stage Dockerfile，用 `COPY --from=builder` 拉 venv
- [ ] 我知道 alpine 的 musl 坑、預設選 slim 的理由
- [ ] 我理解 pin 依賴版本為什麼是 reproducible build 的前提

下一章把 `tasktrack` 從 SQLite 升級到 PostgreSQL — 這需要兩個容器協作，進入 `docker-compose` 的世界。

→ [Ch 4 docker-compose — 服務從來不是單機](./04-docker-compose.md)
