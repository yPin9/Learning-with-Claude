# Ch 12 — 映像最小化：攻擊面就是 image size

> 目標：理解 image size 和安全性的關係，知道各種 base image 的取捨，能用 `docker history` 和 `dive` 找到胖 layer 的原因，並寫出修法。

---

## 為什麼 Image 要小

不只是省磁碟和網路頻寬，image 小的真正好處是：

**拉取快**：CI/CD pipeline 每次 pull image 的時間，在大型系統裡每天累積好幾小時。一個 100MB 的 image 比 1GB 的 image 快 10 倍。

**CVE 少**：每個裝進去的套件都是潛在漏洞。`ubuntu:22.04` 裡有幾百個系統套件，每一個都可能在 Trivy/Snyk 掃描裡跳出 CVE。`scratch` 是空的，CVE 數量是 0。

**攻擊面小**：如果容器被入侵，攻擊者可以用的工具（curl、wget、python、bash）越少，橫向移動越難。`distroless` 連 shell 都沒有，攻擊者進去只能跑你的 binary。

---

## Base Image 選型

| Base Image | 未壓縮大小 | 有 shell | 有 apt/apk | CVE 量 | 適用場景 |
|------------|-----------|---------|-----------|--------|---------|
| `ubuntu:22.04` | ~80MB | bash | apt | 多（200+） | 開發、需要完整工具鏈 |
| `debian:bookworm-slim` | ~75MB | bash | apt | 中 | 需要 apt 但比 ubuntu 乾淨一點 |
| `alpine:3.19` | ~7MB | ash | apk | 少 | 大多數服務，注意 musl 相容性 |
| `gcr.io/distroless/base` | ~20MB | 無 | 無 | 極少 | 動態連結的 binary |
| `gcr.io/distroless/python3` | ~55MB | 無 | 無 | 極少 | Python 服務 |
| `gcr.io/distroless/java17` | ~200MB | 無 | 無 | 少 | JVM 服務 |
| `scratch` | 0MB | 無 | 無 | 0 | 靜態連結 binary（Go、C static） |

**Alpine 的注意事項**：Alpine 用 musl libc 而非 glibc。大多數情況下沒問題，但有些 C extension（某些 Python 套件、特定 glibc 功能）在 Alpine 上會出現相容性問題，這時改用 `debian:bookworm-slim`。

**distroless 的注意事項**：沒有 shell，所以 `docker exec <container> bash` 進不去，debug 只能靠 `docker logs` 或 sidecar container。這是刻意設計的，你必須接受這個代價。

---

## docker history：找哪一層最肥

```bash
docker history python:3.11
# IMAGE          CREATED        CREATED BY                                      SIZE
# abc123         2 weeks ago    CMD ["python3"]                                  0B
# def456         2 weeks ago    RUN ldconfig                                     0B
# ghi789         2 weeks ago    COPY ... # buildkit                             28.5MB
# jkl012         2 weeks ago    RUN apt-get install -y --no-install-recommends  73.1MB
# mno345         2 weeks ago    RUN apt-get update && apt-get install -y ...    46.2MB
# ...

# 找你自己的 image 裡最大的 layer
docker history myapp:latest | sort -k5 -h | tail -10
```

`docker history` 只能看到每個 layer 的大小和建立指令，但看不到 layer 裡面有什麼檔案。這時候需要 `dive`。

---

## dive：互動式看每層內容

`dive` 是一個 TUI 工具，讓你看每個 layer 增加/刪除了哪些檔案，幫你找到「誰吃掉了那幾百 MB」。

```bash
# 安裝（Linux）
wget https://github.com/wagoodman/dive/releases/download/v0.12.0/dive_0.12.0_linux_amd64.deb
dpkg -i dive_0.12.0_linux_amd64.deb

# macOS
brew install dive

# 使用
dive myapp:latest
```

介面操作：

```
左半邊：layer 列表，選取一個 layer
右半邊：該 layer 新增/修改的檔案樹
  橘色 = modified
  綠色 = added
  紅色 = removed

Tab：切換左右面板
Ctrl+F：過濾檔案
Ctrl+A：切換只顯示修改過的
Space：展開/收合目錄
```

在 CI 裡自動檢查 image 效率：

```bash
# CI 模式，image 效率低於 0.9 就失敗
CI=true dive --ci-config .dive-ci.yaml myapp:latest
```

`.dive-ci.yaml`：

```yaml
rules:
  lowestEfficiency: 0.9        # layer 效率不能低於 90%
  highestWastedBytes: 20MB     # 浪費的空間不能超過 20MB
  highestUserWastedPercent: 0.2 # 使用者層浪費不能超過 20%
```

---

## 常見胖 Layer 原因與修法

### 問題一：apt install 沒清 cache

```dockerfile
# 壞（apt cache 進了 layer，多出 30-100MB）
RUN apt-get update
RUN apt-get install -y gcc make

# 好（全部一個 RUN，最後清 cache）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    make \
    && rm -rf /var/lib/apt/lists/*
```

`--no-install-recommends` 跳過不必要的推薦套件，通常可以省 20-40%。

**關鍵：清 cache 必須在同一個 `RUN` 裡**。如果分開兩個 `RUN`，即使第二個 `RUN rm -rf`，第一個 `RUN` 的 layer 已經固化了，cache 檔案永遠在那裡。

```dockerfile
# 這樣無效！layer 1 裡的 apt cache 永遠移不掉
RUN apt-get update && apt-get install -y gcc
RUN rm -rf /var/lib/apt/lists/*   # 這只是在 layer 2 刪，layer 1 的 tar 不變
```

### 問題二：把 .git / node_modules 複製進去

```bash
# 看你的 build context 有多大
docker build . 2>&1 | head -1
# Sending build context to Docker daemon  847.3MB   <- node_modules 全進去了
```

解法是 `.dockerignore`（詳見 Ch 13），但有時候是 `COPY . .` 直接把 node_modules 裝進 image 裡：

```dockerfile
# 壞：node_modules 全進 image
COPY . .

# 好：讓 npm ci 自己裝，或用 multi-stage
COPY package.json package-lock.json ./
RUN npm ci --production
COPY src/ ./src/
```

### 問題三：多個 RUN 建多層，中間產生暫存檔

```dockerfile
# 壞：download.tar 存在 layer 1，即使 layer 2 刪了，layer 1 的 tar 還在
RUN wget https://example.com/bigfile.tar.gz
RUN tar -xzf bigfile.tar.gz && rm bigfile.tar.gz

# 好：一個 RUN 搞定，下載和刪除在同一層
RUN wget https://example.com/bigfile.tar.gz \
    && tar -xzf bigfile.tar.gz \
    && rm bigfile.tar.gz

# 更好：用 BuildKit 的 tmpfs mount（暫存完全不進任何 layer）
RUN --mount=type=tmpfs,target=/tmp/dl \
    wget -P /tmp/dl https://example.com/bigfile.tar.gz \
    && tar -C /app -xzf /tmp/dl/bigfile.tar.gz
```

---

## 完整範例：FastAPI 服務，四種 base image 對比

同一個 FastAPI 服務，四種 base image 的 size 和取捨：

```dockerfile
# 1. ubuntu:22.04（最大，不推薦生產）
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip3 install -r requirements.txt
COPY . .
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

```dockerfile
# 2. python:3.11-slim（均衡，CI/CD 常見）
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER nobody
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

```dockerfile
# 3. python:3.11-alpine（最小，注意 musl 相容性）
FROM python:3.11-alpine
WORKDIR /app
RUN apk add --no-cache libpq
COPY requirements.txt .
# 有些套件要加 build deps，裝完再移除
RUN apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps
COPY . .
USER nobody
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

```dockerfile
# 4. distroless/python3（最安全，無 shell）
FROM python:3.11-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
ENV PATH="/opt/venv/bin:$PATH"
# distroless 用 ENTRYPOINT，不能用 CMD 的 shell form
ENTRYPOINT ["/opt/venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

Size 對比（實測，FastAPI + SQLAlchemy + psycopg2 的典型依賴）：

| Base Image | Image Size | CVE (高危) | 有 shell |
|------------|-----------|-----------|---------|
| ubuntu:22.04 | ~650MB | 15+ | bash |
| python:3.11-slim | ~280MB | 5-10 | bash |
| python:3.11-alpine | ~130MB | 1-3 | ash |
| distroless/python3 | ~120MB | 0-1 | 無 |

選型建議：一般服務用 `python:3.11-slim`，有安全需求的生產環境用 `distroless`，需要 debug 工具的開發環境用 `slim` 或 `ubuntu`。

---

## 自我檢核

- [ ] 能說清楚 image size 和安全性的三個關係（拉取速度、CVE 數、攻擊面）
- [ ] 知道 alpine 和 debian-slim 的取捨點（musl vs glibc）
- [ ] 知道 distroless 的特性和代價（無 shell = debug 困難）
- [ ] 能用 `docker history` 找出最大的 layer
- [ ] 安裝並使用 `dive` 看 layer 內容
- [ ] 知道「分開兩個 RUN 刪 cache 無效」的原因（layer 已固化）
- [ ] 能改寫一個典型的「apt install 沒清 cache」和「分多層 RUN」的問題 Dockerfile

Image 小了，但每次 build 還是把幾百 MB 的目錄全部打包送給 daemon？這是 build context 的問題。

→ [Ch 13 .dockerignore 與 Build Context](./13-dockerignore-build-context.md)
