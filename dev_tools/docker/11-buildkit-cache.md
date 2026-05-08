# Ch 11 — BuildKit 與 Cache：讓 Build 快 10 倍

> 目標：理解 BuildKit 的 layer cache 原理，掌握 cache 最佳化的 Dockerfile 寫法，並學會 `--mount=type=cache` 和 `--mount=type=secret` 這兩個 BuildKit 專有語法。

---

## BuildKit 是什麼

Docker 18.09（2018 年）引入 BuildKit 作為新的 build engine，解決舊版 builder 的幾個根本問題：

| 特性 | 舊版 builder | BuildKit |
|------|-------------|---------|
| 指令執行 | 嚴格串行 | 可並行執行無依賴的指令 |
| Cache 邏輯 | 簡單 hash | 更精確，支援 cache mount |
| Secret 處理 | 只能放進 layer（危險） | `--mount=type=secret`，不殘留 |
| 跨平台 build | 需要 qemu 手動設定 | `docker buildx` 原生支援 |
| Build 輸出 | 必須是 Docker image | 可以輸出到目錄、OCI tar 等 |

啟用 BuildKit：

```bash
# 單次啟用
DOCKER_BUILDKIT=1 docker build .

# 永久啟用（加到 /etc/docker/daemon.json）
{
  "features": {"buildkit": true}
}

# Docker Desktop 和現代 Docker Engine（23.0+）預設已啟用
docker buildx version
# github.com/docker/buildx v0.x.x ...
```

---

## Layer Cache 原理

每個 Dockerfile 指令建立一個 layer。BuildKit 對每個指令計算一個 cache key：

```
cache key = hash(
  指令文字,
  前一層的 cache key,   <- 依賴鏈
  COPY/ADD 的檔案內容  <- 只有 COPY/ADD 才 hash 檔案
)
```

這個設計的結果是：**COPY 或 ADD 之後的所有指令，只要 COPY 的檔案有任何變動，cache 就全部 invalidate（失效）**。

```
Dockerfile 執行順序與 cache 關係：

FROM python:3.11              <- cache key A
RUN pip install build-tools   <- cache key B（依賴 A）
COPY . .                      <- cache key C（依賴 B + 檔案 hash）
RUN pip install -r req.txt    <- cache key D（依賴 C）

當你改了 main.py（在 COPY . . 裡）：
  - A: HIT
  - B: HIT
  - C: MISS（檔案變了）
  - D: MISS（C 變了，D 自動 invalidate）
  -> pip install 全部重跑，浪費幾分鐘
```

---

## Cache 最佳化：先 COPY 依賴檔，再 COPY 程式碼

**錯誤寫法**（每次改 code 都重裝依賴）：

```dockerfile
FROM python:3.11
WORKDIR /app
# COPY . . 把所有檔案（包含 main.py）一起 COPY
COPY . .
# 任何 .py 檔改動都會讓這行 cache 失效
RUN pip install -r requirements.txt
CMD ["python", "main.py"]
```

**正確寫法**（只有 requirements.txt 改動才重裝）：

```dockerfile
FROM python:3.11
WORKDIR /app
# 先只 COPY 依賴描述檔
COPY requirements.txt .
# 只要 requirements.txt 不改，這行永遠 cache HIT
RUN pip install --no-cache-dir -r requirements.txt
# 最後才 COPY 程式碼（改 code 不會讓上面的 pip 失效）
COPY . .
CMD ["python", "main.py"]
```

同樣的原則適用於所有語言：

```dockerfile
# Node.js：先 COPY package.json + package-lock.json
COPY package.json package-lock.json ./
RUN npm ci

# Go：先 COPY go.mod + go.sum
COPY go.mod go.sum ./
RUN go mod download

# Rust：先 COPY Cargo.toml + Cargo.lock
COPY Cargo.toml Cargo.lock ./
RUN cargo fetch
```

---

## --mount=type=cache：跨 Build 保留 cache

舊版 builder 的每次 `RUN` 都在一個全新的臨時容器裡執行，`pip install` 下載的 wheel 檔案不會保留到下次 build。BuildKit 的 cache mount 解決這個問題：

```dockerfile
# pip cache：下載過的 wheel 不用重新下載
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# apt cache：下載過的 .deb 不用重新下載
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y gcc libpq-dev

# npm cache
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Go module cache
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

# Cargo registry cache
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cargo build --release
```

cache mount 的特性：

- cache 資料不會進 image layer，只存在 build host 上
- 多個 build 之間共用，不受 `--no-cache` 影響（cache mount 是獨立的）
- 可以設 `sharing=locked`（串行）或 `sharing=shared`（並行，預設），或 `sharing=private`（每次 build 獨立）

```dockerfile
# 並行 build 時避免 cache 衝突
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt
```

---

## --mount=type=secret：Build-time Secret

build 時有時需要 credentials，例如 pip install 私有 PyPI、npm install 私有 registry、git clone 私有 repo。舊做法是：

```dockerfile
# 危險！secret 殘留在 layer 裡
ARG GITHUB_TOKEN
RUN git clone https://token:${GITHUB_TOKEN}@github.com/private/repo.git
```

就算後面 `RUN rm -rf token`，token 仍然存在那個 layer 的 tar 裡，`docker history` 可以看到。

BuildKit 的 secret mount：

```dockerfile
# Dockerfile
RUN --mount=type=secret,id=pip_config \
    pip install --no-cache-dir \
      --extra-index-url $(cat /run/secrets/pip_config | grep index-url | cut -d= -f2) \
      -r requirements.txt

# 更常見的用法：整個 pip.conf
RUN --mount=type=secret,id=pip_conf,target=/root/.config/pip/pip.conf \
    pip install --no-cache-dir -r requirements.txt
```

build 時傳入 secret：

```bash
# 從檔案傳
docker build \
  --secret id=pip_conf,src=/home/user/.config/pip/pip.conf \
  -t myapp .

# 從環境變數傳
docker build \
  --secret id=github_token,env=GITHUB_TOKEN \
  -t myapp .
```

secret 只在 `RUN` 執行時掛載，執行完畢後立即移除，不會進任何 layer。

---

## --mount=type=bind 與 --mount=type=tmpfs

還有兩個常用的 mount type：

```dockerfile
# bind：在 build 時掛載 host 目錄，不 COPY 進 image
# 用途：讀取 build-time 設定、測試資料
RUN --mount=type=bind,source=./tests,target=/app/tests \
    pytest /app/tests

# tmpfs：掛載臨時記憶體 filesystem
# 用途：需要寫暫存檔但不想進 layer
RUN --mount=type=tmpfs,target=/tmp/scratch \
    ./build.sh  # build.sh 產生的暫存檔放在 /tmp/scratch，不進 image
```

---

## docker build --no-cache 和 cache 相關選項

```bash
# 完全不用 cache，強制全部重新 build
docker build --no-cache -t myapp .

# 用不同的 cache source（CI 常見，從 registry 拉 cache）
docker buildx build \
  --cache-from type=registry,ref=myregistry/myapp:cache \
  --cache-to type=registry,ref=myregistry/myapp:cache,mode=max \
  -t myregistry/myapp:latest .

# 只讓某個 stage 的 cache 失效（用 ARG 的技巧）
ARG CACHEBUST=1
RUN git clone https://github.com/some/repo.git
# 執行時：docker build --build-arg CACHEBUST=$(date +%s) .
```

---

## docker buildx：多平台 Build

```bash
# 建立支援多平台的 builder
docker buildx create --name multiplatform --use

# 同時 build amd64 和 arm64
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t myregistry/myapp:latest \
  --push \
  .

# 查看 build 的詳細過程（BuildKit 的並行執行很明顯）
docker buildx build --progress=plain .
```

---

## 完整 Dockerfile 範例：Python API，最佳化 cache

```dockerfile
# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS builder

WORKDIR /app

# 系統依賴（不常改，放最前面）
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev

# Python 依賴（requirements.txt 才會讓這層 invalidate）
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# 程式碼（最後 COPY，改 code 不影響上面的 cache）
COPY src/ ./src/

FROM python:3.11-slim AS runner
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /app/src ./src
RUN useradd -m appuser && chown -R appuser /app
USER appuser
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0"]
```

第一行 `# syntax=docker/dockerfile:1.6` 告訴 BuildKit 用哪個 Dockerfile 語法版本，解鎖新語法（`--mount` 等），但要求 BuildKit 啟用。

---

## 自我檢核

- [ ] 能說清楚 BuildKit cache key 的計算方式，以及為什麼 COPY 之後的指令 cache 會失效
- [ ] 能改寫一個「先 COPY . . 再 pip install」的 Dockerfile，讓 cache 不被程式碼改動破壞
- [ ] 能寫出 `--mount=type=cache` 的語法，並說明它和 layer cache 的區別（跨 build 保留 vs 單次 build 內）
- [ ] 能說清楚為什麼 `ARG` 傳 secret 是危險的，以及 `--mount=type=secret` 如何解決這個問題
- [ ] 知道 `docker build --no-cache` 不影響 `--mount=type=cache` 的 cache（兩個是獨立的）
- [ ] 能用 `docker buildx build --platform` 做多平台 build

Build 快了，但 image 還是太大？下一章直接對 image size 動手。

→ [Ch 12 映像最小化](./12-image-minimization.md)
