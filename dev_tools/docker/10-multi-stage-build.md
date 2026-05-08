# Ch 10 — Multi-stage Build：把編譯器關在門外

> 目標：理解 multi-stage build 的設計邏輯，能寫出 C、Go、Python 三種語言的 before/after Dockerfile，並用 `docker history` 驗證哪些 layer 被消除了。

---

## 為什麼需要 Multi-stage Build

最直覺的 Dockerfile 寫法：在同一個 image 裡裝 gcc、跑編譯、然後執行。

```dockerfile
# 錯誤示範
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y gcc make
COPY . /app
WORKDIR /app
RUN gcc -O2 -o server server.c
CMD ["./server"]
```

這個 image 大概是：

```
ubuntu:22.04 base    ~80MB
gcc + make + deps   ~300MB
你的 binary          ~500KB
---------
總計：               ~380MB

但執行時真正需要的：~500KB
```

編譯器、標頭檔、make、所有 build dependency 全都進了 production image，多出來的 300MB 不只是浪費磁碟，更是攻擊面（apt、ld、gcc 這些工具讓攻擊者在容器裡的操作空間大很多）。

Multi-stage build（多階段建置）讓你在同一個 Dockerfile 裡用多個 `FROM`，前面的 stage 只負責編譯，後面的 stage 只 copy 需要的產物。最終 image 只包含最後一個 stage。

---

## 語法基礎

```dockerfile
# stage 0：builder
FROM gcc:12 AS builder
WORKDIR /src
COPY . .
RUN gcc -O2 -static -o myapp main.c

# stage 1：runner（最終 image）
FROM scratch
COPY --from=builder /src/myapp /myapp
ENTRYPOINT ["/myapp"]
```

關鍵語法：

- `FROM <image> AS <name>`：給 stage 命名
- `COPY --from=<name>`：從指定 stage 複製檔案
- `COPY --from=0`：用 index 指定（0 是第一個 stage），不建議，名稱比較清楚
- 最後一個 `FROM` 決定最終 image 的 base

---

## 範例一：C 語言，~1GB 到 ~200KB

```
before（單一 stage）
+--------------------+
| gcc:12             |  ~1.4GB
| + 你的 source      |
| + 你的 binary      |
+--------------------+

after（multi-stage）
+--------------------+     +--------------------+
| gcc:12（builder）  | --> | scratch（runner）  |  ~200KB
| + build 過程       |     | + 靜態 binary      |
+--------------------+     +--------------------+
                                最終 image 只有這個
```

**Dockerfile（before）**：

```dockerfile
FROM gcc:12
WORKDIR /src
COPY main.c .
RUN gcc -O2 -o server main.c
CMD ["./server"]
# docker build -t demo-c-before .
# docker images demo-c-before -> 約 1.4GB
```

**Dockerfile（after）**：

```dockerfile
FROM gcc:12 AS builder
WORKDIR /src
COPY main.c .
# -static 讓 binary 不依賴 shared library，才能在 scratch 上跑
RUN gcc -O2 -static -o server main.c && strip server

FROM scratch
COPY --from=builder /src/server /server
ENTRYPOINT ["/server"]
# docker build -t demo-c-after .
# docker images demo-c-after -> 約 100-300KB（取決於你的 binary）
```

`strip` 移掉 debug symbol，通常可以再縮 30-50%。

---

## 範例二：Go，靜態連結最乾淨

Go 天生適合 multi-stage：預設靜態連結，binary 可以直接放進 `scratch`。

**Dockerfile（before）**：

```dockerfile
FROM golang:1.22
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o api ./cmd/api
CMD ["./api"]
# 大小：約 1GB（golang base image 本身就很大）
```

**Dockerfile（after）**：

```dockerfile
FROM golang:1.22 AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
# CGO_ENABLED=0 確保靜態連結，GOOS=linux 確保在 Linux 上跑
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o api ./cmd/api

FROM scratch
# 如果你的程式需要 TLS，要帶 CA certificates
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /app/api /api
ENTRYPOINT ["/api"]
# 大小：通常 5-15MB（取決於程式碼量和 dependency）
```

`-ldflags="-s -w"` 移掉 symbol table 和 DWARF debug info，binary 更小。

---

## 範例三：Python FastAPI，不能用 scratch

Python 有 interpreter，不能放進 scratch，但可以用 slim base 並只 copy 安裝好的虛擬環境。

**Dockerfile（before）**：

```dockerfile
FROM python:3.11
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
# 大小：約 1.1GB（完整 python image + 所有依賴）
```

**Dockerfile（after）**：

```dockerfile
# stage 1：安裝依賴（需要編譯器裝有 C extension 的套件）
FROM python:3.11 AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# 有些套件需要 build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# stage 2：只 copy venv + 程式碼
FROM python:3.11-slim AS runner
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
# 非 root user（安全最佳實踐）
RUN useradd -m appuser && chown -R appuser /app
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
# 大小：約 200-400MB（比 before 縮小 60-70%）
```

三個範例的 size 對比（概估）：

| 語言 | Before | After | 縮減 |
|------|--------|-------|------|
| C | ~1.4GB | ~200KB | 99.9% |
| Go | ~1.0GB | ~10MB | 99% |
| Python FastAPI | ~1.1GB | ~300MB | 72% |

---

## 指定 Build Target：只跑到某個 stage

```bash
# 只 build 到 builder stage（CI 跑測試用）
docker build --target builder -t myapp:test .

# 完整 build（production）
docker build -t myapp:prod .
```

實際應用：在 CI pipeline 裡，先 `--target builder` 跑 unit test，通過後才 build 完整的 production image。

```dockerfile
FROM golang:1.22 AS builder
WORKDIR /app
COPY . .
RUN go build -o api ./cmd/api

FROM builder AS tester
RUN go test ./...          # 這層只在 --target tester 時執行

FROM scratch AS production
COPY --from=builder /app/api /api
ENTRYPOINT ["/api"]
```

---

## docker history：驗證 layer 消失了

```bash
# 比較 before 和 after 的 layer 數量和大小
docker history demo-c-before
# IMAGE          CREATED       CREATED BY                                      SIZE
# abc123         1 min ago     CMD ["./server"]                                0B
# def456         1 min ago     RUN gcc -O2 -o server main.c                   208kB
# ghi789         1 min ago     COPY main.c .                                   1.2kB
# jkl012         ...           /bin/sh -c apt-get update && ...               350MB
# ...            ...           base gcc:12 layers                             1.1GB

docker history demo-c-after
# IMAGE          CREATED       CREATED BY                                      SIZE
# xyz789         1 min ago     ENTRYPOINT ["/server"]                         0B
# abc123         1 min ago     COPY --from=builder /src/server /server        180kB
# 只有兩層！gcc 的那一大堆全都不見了
```

`docker history` 讓你清楚看到哪些 layer 進了最終 image，哪些留在中間 stage 被丟掉。

---

## 自我檢核

- [ ] 能解釋 multi-stage build 解決的是什麼問題（不只是「縮小 image」，要說出攻擊面和可重現性）
- [ ] 能獨立寫出 C 語言的 multi-stage Dockerfile，包含 `-static` 和 `strip`
- [ ] 能解釋 Go 需要 `CGO_ENABLED=0` 的原因
- [ ] 知道 Python 為什麼不能用 `scratch`，以及 venv copy 的技巧
- [ ] 能用 `COPY --from=builder` 而非 `COPY --from=0`（用名稱）
- [ ] 能用 `docker build --target` 在 CI 只執行到特定 stage
- [ ] 能用 `docker history` 驗證 before/after 的 layer 差異

Multi-stage 解決了「哪些東西不該進 image」的問題，但 build 本身的速度怎麼優化？cache 沒命中每次重新裝依賴，在 CI 上浪費大量時間。

→ [Ch 11 BuildKit 與 Cache](./11-buildkit-cache.md)
