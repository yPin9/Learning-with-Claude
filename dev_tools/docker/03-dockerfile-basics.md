# Ch 3 — Dockerfile 入門

> 目標：搞懂 Dockerfile 每條常用指令的語義和陷阱，能寫出一個真實的 Python FastAPI 服務 image，並知道 layer 快取的運作方式。

---

## Dockerfile 是什麼

Dockerfile 是一份建 image 的腳本，每條指令會產生一個新的 layer（唯讀的 filesystem snapshot）。最終 image 就是所有 layer 疊起來的結果。

```
Dockerfile 指令          Image Layer
--------------          -----------
FROM python:3.11-slim   layer 0: base
RUN pip install ...     layer 1: +packages
COPY . /app             layer 2: +source code
CMD ["uvicorn", ...]    layer 3: metadata (no fs change)
```

重要：不是每條指令都真的改 filesystem。`CMD`、`EXPOSE`、`ENV`（有些情況）、`LABEL` 只改 image metadata，layer 很薄。

---

## 指令逐一說明

### FROM — 指定 base image

```dockerfile
FROM python:3.11-slim
```

所有 Dockerfile 的第一條指令（除非你要 multi-stage build）。  
`FROM scratch` 是真正的空 image，用來建純靜態二進位（如 Go、C）：

```dockerfile
FROM scratch
COPY ./myapp /myapp
CMD ["/myapp"]
```

選 base image 原則：能用 `slim` / `alpine` 就不要用完整版，攻擊面小、image 小。

### RUN — 執行指令

```dockerfile
# Shell form（透過 /bin/sh -c 跑）
RUN apt-get update && apt-get install -y curl

# Exec form（直接 exec，不過 shell）
RUN ["apt-get", "install", "-y", "curl"]
```

**Shell form vs Exec form 差異：**

| | Shell form | Exec form |
|--|------------|-----------|
| 格式 | `RUN cmd arg` | `RUN ["cmd", "arg"]` |
| 執行方式 | `/bin/sh -c "cmd arg"` | 直接 exec |
| Shell 特性 | 支援 `&&`、`$VAR`、管道 | 不支援 |
| 陷阱 | `SIGTERM` 打到 sh，不一定轉發給子進程 | 無此問題 |

多個 `RUN` 產生多個 layer，用 `&&` 串成一條是常見做法，減少 layer 數量：

```dockerfile
# 差：兩層
RUN apt-get update
RUN apt-get install -y curl

# 好：一層，且裝完清快取
RUN apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/*
```

### COPY vs ADD

```dockerfile
COPY requirements.txt /app/
COPY . /app/

ADD https://example.com/file.tar.gz /tmp/   # ADD 能抓 URL（不推薦）
ADD archive.tar.gz /app/                     # ADD 能自動解壓縮
```

**實務判斷：優先用 `COPY`**。`ADD` 的自動解壓縮和 URL 抓取讓行為不夠明確。只有在確實需要解壓縮本地 tar 時才用 `ADD`。

### WORKDIR — 設工作目錄

```dockerfile
WORKDIR /app
```

等同於 `mkdir -p /app && cd /app`，後續的 `RUN`、`COPY`、`CMD` 都以此為相對路徑基準。  
不要用 `RUN cd /app`——那只對那一條 RUN 有效，下一條 RUN 又回到根目錄。

### ENV — 環境變數

```dockerfile
ENV APP_ENV=production
ENV PORT=8000
```

`ENV` 設的變數在 build time 和 runtime 都有效，也殘留在最終 image。

```bash
# 可以在 docker run 覆蓋
docker run -e APP_ENV=staging myapp:v1
```

### ARG — Build-only 變數

```dockerfile
ARG VERSION=1.0.0
RUN echo "Building version $VERSION"
```

`ARG` 只在 build time 有效，**不殘留在最終 image**（`docker inspect` 看不到）。  
用來傳 build 參數（版本號、git commit hash）而不污染 runtime 環境。

```bash
docker build --build-arg VERSION=2.0.0 -t myapp:v2 .
```

**警告**：`ARG` 傳密碼是常見的安全錯誤——雖然不殘留在 image 環境變數，但 `docker history` 能看到 build arg 值。密碼要用 BuildKit secrets（Ch 11）。

### EXPOSE — 文件用途

```dockerfile
EXPOSE 8000
```

`EXPOSE` **不實際開放 port**，只是告訴使用者「這個 image 的服務跑在 8000」。實際 port mapping 還是靠 `docker run -p`。  
把它當文件而不是功能，但仍然值得寫清楚。

### CMD vs ENTRYPOINT

這兩個指令的關係最容易搞混，用表格釐清：

| | CMD | ENTRYPOINT |
|--|-----|------------|
| 作用 | 預設指令 / 預設參數 | 容器入口點，固定不變 |
| `docker run` 可覆蓋？ | 可以（直接在指令後面加） | 不行（要用 `--entrypoint`） |
| 搭配使用 | 當 ENTRYPOINT 的預設參數 | |

```dockerfile
# 只有 CMD：可以完全被 docker run 後面的指令覆蓋
CMD ["python", "app.py"]

# 只有 ENTRYPOINT：固定入口，docker run 後面的參數是傳給它的 args
ENTRYPOINT ["python", "app.py"]

# 組合使用：ENTRYPOINT 固定執行哪個程式，CMD 是預設參數
ENTRYPOINT ["python"]
CMD ["app.py"]
# docker run myapp            -> python app.py
# docker run myapp other.py   -> python other.py  (CMD 被覆蓋)
```

實務上：服務型 image 用 `ENTRYPOINT` 固定服務程式，`CMD` 給預設參數；工具型 image 常只用 `CMD`。

### USER — 切換執行用戶

```dockerfile
RUN useradd -m -u 1001 appuser
USER appuser
```

容器裡預設是 root，這不好。用 `USER` 切換到非特權用戶，限制萬一被攻破後的影響範圍。Ch 21 會完整討論。

---

## 完整範例：Python FastAPI 服務

目錄結構：

```
myapp/
├── Dockerfile
├── requirements.txt
└── main.py
```

**main.py**

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "Hello from Docker"}
```

**requirements.txt**

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
```

**Dockerfile**

```dockerfile
FROM python:3.11-slim

# 設工作目錄
WORKDIR /app

# 先 COPY requirements，利用 layer 快取
# 只有 requirements.txt 改了才重跑 pip install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再 COPY 應用程式碼
COPY main.py .

# 建非特權用戶
RUN useradd -m -u 1001 appuser
USER appuser

# 文件說明 port
EXPOSE 8000

# 啟動服務
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Build 並跑起來：**

```bash
# 在 myapp/ 目錄下
docker build -t myapp:v1 .
```

```
[+] Building 23.5s (10/10) FINISHED
 => [internal] load build definition from Dockerfile
 => [1/6] FROM python:3.11-slim
 => [2/6] WORKDIR /app
 => [3/6] COPY requirements.txt .
 => [4/6] RUN pip install --no-cache-dir -r requirements.txt
 => [5/6] COPY main.py .
 => [6/6] RUN useradd -m -u 1001 appuser
 => exporting to image
```

```bash
docker run -d -p 8000:8000 --name myapp myapp:v1
curl http://localhost:8000/health
# {"status":"ok"}
```

**`-t myapp:v1`**：`-t` 給 image 加 tag，格式 `name:tag`。  
**`.`（build context）**：`.` 表示把當前目錄傳給 Docker daemon 作為 build context，Dockerfile 裡的 `COPY` 相對路徑就是相對於這個目錄。build context 越大，傳輸越慢，Ch 13 的 `.dockerignore` 會處理這個問題。

---

## docker history：查看每層大小

```bash
docker history myapp:v1
```

```
IMAGE          CREATED          CREATED BY                                      SIZE
a2f3b1c9d0e4   2 minutes ago    CMD ["uvicorn" "main:app" "--host" "0.0.0.0"…   0B
<missing>      2 minutes ago    USER appuser                                    0B
<missing>      2 minutes ago    RUN useradd -m -u 1001 appuser                  335kB
<missing>      2 minutes ago    COPY main.py .                                  521B
<missing>      2 minutes ago    RUN pip install --no-cache-dir -r requiremen…   52.3MB
<missing>      2 minutes ago    COPY requirements.txt .                         68B
<missing>      2 minutes ago    WORKDIR /app                                    0B
<missing>      11 days ago      FROM python:3.11-slim                           125MB
```

pip install 那層最大（52 MB），這就是 multi-stage build 要優化的對象（Ch 10）。

---

## Layer 快取的邏輯

Docker build 時，每個步驟如果輸入沒變就直接用快取，不重跑。  
`COPY requirements.txt .` 在 `COPY main.py .` 之前，正是為了利用快取：改程式碼不重跑 pip install，省時間。

```
# 改了 main.py 之後重 build
docker build -t myapp:v2 .

[+] Building 1.8s (10/10) FINISHED   <- 快很多
 => CACHED [3/6] COPY requirements.txt .
 => CACHED [4/6] RUN pip install ...   <- 快取命中，不重跑
 => [5/6] COPY main.py .               <- 這裡開始重跑
```

---

## 自我檢核

- [ ] 能解釋 shell form 和 exec form 的差異及各自的陷阱
- [ ] 知道為什麼 `COPY` 比 `ADD` 優先
- [ ] 能說明 `CMD` 和 `ENTRYPOINT` 組合使用的行為
- [ ] 寫出並跑起 FastAPI 服務的 Dockerfile
- [ ] 用 `docker history` 找出哪一層最肥
- [ ] 知道 layer 快取的命中條件，並刻意安排 `COPY` 順序利用快取

下一章進入網路，從 bridge / host / none 到容器間互通的 DNS。

→ [Ch 4 網路基礎](./04-networking-basics.md)
