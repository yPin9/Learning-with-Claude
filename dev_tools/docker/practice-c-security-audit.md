# 練習 C：Dockerfile 資安審查

整合章節：Ch 11（BuildKit secret）、Ch 12（image 最小化）、Ch 13（.dockerignore / build context）、Ch 19（image 掃描）、Ch 21（non-root / read-only）、Ch 22（capabilities drop）

---

## 背景

一個「能跑」的 Dockerfile 和一個「能上線」的 Dockerfile 差很多。這個練習給你一個真實世界常見的壞 Dockerfile，你的任務是：找出問題、說明原因、修好它。

目標讀者：有 CTF binary / 嵌入式底子的人。你已經知道 privilege escalation（提權）和 secret leak（機密洩漏）是什麼，這個練習就是把這些概念對應到 container 的語境。

---

## 題目

### 有問題的 Dockerfile（原版）

```dockerfile
FROM ubuntu:latest

RUN apt-get update && apt-get install -y python3 python3-pip curl wget git vim

WORKDIR /app

COPY . .

RUN pip3 install -r requirements.txt

ARG API_KEY
RUN curl -H "Authorization: $API_KEY" https://api.example.com/config > config.json

EXPOSE 8080

CMD python3 app.py
```

---

### 任務

1. 找出至少 8 個問題
2. 每個問題說明：**是什麼問題**、**為什麼危險**、**怎麼修**
3. 寫出修復後的完整 Dockerfile
4. 補一個對應的 `.dockerignore`

不用看答案，先自己分析。

---

## 實作步驟建議

先回答這幾個問題，答案有了問題就找到了：

1. 用 `trivy image` 或 `docker scout cves` 掃 `ubuntu:latest`，看有多少 CVE
2. 執行 `docker build --build-arg API_KEY=mysecret .` 之後，`docker history <image>` 看什麼
3. `docker run --rm <image> id`，看是什麼 user 在跑
4. `docker run --rm <image> kill -SIGTERM 1`，看 process 能不能收到信號
5. 在 `COPY . .` 之前，想想你的工作目錄裡有什麼（`.env`、`.git`、`__pycache__`...）

---

## 問題清單與修法

（先自己想，再看下面。**能找到 10 個以上才算真的懂了。**）

<details>
<summary>點開參考實作</summary>

### 問題 1：`ubuntu:latest` 不 pin tag

**問題**：`latest` 每次 build 可能拉到不同版本，導致「今天 build 沒問題，一個月後 CI 爆了」的情況。

**危險**：reproducibility（可重現性）爛掉，debug 困難；新版 ubuntu 可能帶來 breaking change。

**修法**：
```dockerfile
# 改用具體版本 + digest（SHA256 固定）
FROM ubuntu:24.04
# 更好：
FROM ubuntu:24.04@sha256:具體的digest值
```

---

### 問題 2：用 ubuntu 而非 slim / alpine

**問題**：`ubuntu:latest` 約 70–80 MB，包含大量系統工具（`bash`、`ls`、`curl`、`tar`...）和對應的 CVE 攻擊面（攻擊面積）。

**危險**：攻擊者進到 container 後有一堆工具可用；`trivy scan` 會噴出幾百個 CVE。

**修法**：
```dockerfile
# 使用 python:3.11-slim（基於 debian bookworm slim）
FROM python:3.11-slim
# 或 distroless（更極端，連 shell 都沒有）
FROM gcr.io/distroless/python3-debian12
```

---

### 問題 3：`apt-get` 沒清 cache

**問題**：
```dockerfile
# 問題版：每個 RUN 都是獨立 layer
RUN apt-get update
RUN apt-get install -y python3 python3-pip curl wget git vim
```

**危險**：apt cache（`/var/lib/apt/lists/`）留在 layer 裡，image 胖幾十 MB，又沒有安全更新。

**修法**：
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*
```

關鍵：`--no-install-recommends` 不裝推薦但不必要的包；最後 `rm -rf /var/lib/apt/lists/*` 在同一個 RUN 清掉。

---

### 問題 4：安裝開發工具進 production image

**問題**：`vim`、`wget`、`git`、`curl` 這些工具不需要在 production image 裡。

**危險**：
- `curl` 讓攻擊者能對外發 request（data exfiltration，資料滲漏）
- `git` 讓攻擊者能 clone 惡意 repo 並執行
- `wget` 同 curl

**修法**：production image 只裝 app 需要的 runtime dependency。用 multi-stage 把 build 工具留在 builder stage：

```dockerfile
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*
# ... pip install ...

FROM python:3.11-slim AS production
# 不安裝 gcc / curl / git / vim / wget
```

---

### 問題 5：`COPY . .` 把不應該進去的東西全 COPY 進去

**問題**：沒有 `.dockerignore`，`.git/`、`.env`、`__pycache__/`、`*.pyc`、`node_modules/`、`secrets/` 等全部進 image。

**危險**：
- `.env` 裡的密碼直接進 image layer，`docker history` 可見
- `.git/` 洩漏 commit history（可能有舊版的 secret）
- `__pycache__` 讓 build cache 失效更頻繁

**修法**：建立 `.dockerignore`：

```
.git
.env
.env.*
__pycache__
*.pyc
*.pyo
*.egg-info
.pytest_cache
.mypy_cache
.venv
venv
node_modules
*.log
Dockerfile
docker-compose*.yml
compose*.yml
README.md
```

---

### 問題 6：`ARG API_KEY` + `RUN curl ... $API_KEY`，secret 殘留在 layer history

**問題**：
```dockerfile
ARG API_KEY
RUN curl -H "Authorization: $API_KEY" https://api.example.com/config > config.json
```

**危險**：`ARG` 的值會被記錄在 image layer metadata 裡，任何人執行 `docker history <image>` 或 `docker inspect` 都能看到：

```
$ docker build --build-arg API_KEY=supersecret .
$ docker history myimage
IMAGE          CREATED BY                                      SIZE
...            |1 API_KEY=supersecret /bin/sh -c curl -H ...   234B
```

**修法**：用 BuildKit secret mount（秘密掛載），secret 不進 layer：

```dockerfile
# syntax=docker/dockerfile:1

RUN --mount=type=secret,id=api_key \
    curl -H "Authorization: $(cat /run/secrets/api_key)" \
         https://api.example.com/config > config.json
```

```bash
# build 時：
docker build --secret id=api_key,env=API_KEY .
# 或從檔案：
docker build --secret id=api_key,src=./secrets/api_key.txt .
```

secret 只在 `RUN` 執行時存在於 `/run/secrets/api_key`，不進 image layer。

---

### 問題 7：沒有指定 USER，用 root 跑

**問題**：沒有 `USER` 指令，預設以 root（UID 0）執行 process。

**危險**：如果 app 被攻陷，攻擊者在 container 內是 root，能：
- 讀 `/proc`、修改 filesystem
- 搭配 container escape（容器逃脫）取得 host root
- 與 `docker.sock` 配合直接控制 daemon

**修法**：
```dockerfile
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

USER appuser
```

---

### 問題 8：`CMD python3 app.py` 是 shell form，PID 1 不是 Python

**問題**：shell form（`CMD command arg`）實際執行的是 `/bin/sh -c "python3 app.py"`。

**危險**：PID 1 是 `sh`，不是 Python。`docker stop` 送 `SIGTERM` 給 PID 1（`sh`），`sh` 不轉發給子 process，Python 收不到信號，最後被 `SIGKILL` 強制殺掉，導致：
- graceful shutdown（優雅關閉）失敗
- in-flight request 被截斷
- DB connection 沒有正常關閉

**修法**：用 exec form（`CMD ["python3", "app.py"]`）：

```dockerfile
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

exec form 不經過 shell，Python 直接是 PID 1，能收到 `SIGTERM`。

---

### 問題 9：沒有 `--no-new-privileges` / `security_opt`

**問題**：沒有在 Compose 或 run 時加 `--no-new-privileges`。

**危險**：container 內的 process 可以透過 setuid binary（例如 `su`、`sudo`）提升權限，即使你已經用 non-root user 跑，setuid binary 仍然能讓它變成 root。

**修法**：

Compose：
```yaml
security_opt:
  - no-new-privileges:true
```

Dockerfile（runc config level）：等同於在 OCI spec 加 `noNewPrivileges: true`，但在 Dockerfile 層面沒有直接指令，要靠 runtime 設定。

---

### 問題 10：沒有 HEALTHCHECK

**問題**：Docker 不知道 app 是否真的 ready，只知道 process 有沒有掛掉（`running` 不等於 `healthy`）。

**危險**：Compose 的 `depends_on: condition: service_healthy` 永遠等不到；load balancer 把流量導到還沒 ready 的 container。

**修法**：
```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
```

---

### 修復後的完整 Dockerfile

```dockerfile
# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: production ───────────────────────────────────────────
FROM python:3.11-slim

# 建 non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

WORKDIR /app

# 從 builder 複製安裝好的 packages
COPY --from=builder /install /usr/local

# 只 COPY 需要的 source（配合 .dockerignore）
COPY --chown=appuser:appuser app.py .

# 在 builder stage 抓外部 config（如果真的需要）
# 用 BuildKit secret，不讓 key 進 layer
RUN --mount=type=secret,id=api_key,required=false \
    if [ -f /run/secrets/api_key ]; then \
        curl -sf -H "Authorization: $(cat /run/secrets/api_key)" \
            https://api.example.com/config > config.json; \
    fi

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python3", "-c", \
         "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]

# exec form：Python 是 PID 1，能收到 SIGTERM
CMD ["python3", "app.py"]
```

### 對應的 `.dockerignore`

```
.git
.env
.env.*
.env.local
secrets/
__pycache__
*.pyc
*.pyo
*.pyd
*.egg-info
.pytest_cache
.mypy_cache
.coverage
htmlcov/
.venv
venv
env/
dist/
build/
*.log
Dockerfile
Dockerfile.*
docker-compose*.yml
compose*.yml
.dockerignore
README.md
```

### 配合 compose.yml 的 hardening 設定

```yaml
services:
  app:
    build: .
    security_opt:
      - no-new-privileges:true
    read_only: true          # root filesystem 唯讀
    tmpfs:
      - /tmp                 # app 如果需要寫暫存，只能寫 /tmp
    cap_drop:
      - ALL                  # 先全部 drop
    cap_add:
      - NET_BIND_SERVICE     # 如果 port < 1024 才需要，8080 不需要
    user: "1000:1000"
```

### 問題總覽

| # | 問題 | 危險等級 | 影響 |
|---|---|---|---|
| 1 | `ubuntu:latest` 不 pin tag | 中 | reproducibility 爛掉 |
| 2 | ubuntu 而非 slim | 高 | image 胖，CVE 多 |
| 3 | apt cache 沒清 | 低 | image 胖 |
| 4 | 開發工具進 production | 高 | 攻擊工具送進去 |
| 5 | `COPY . .` 沒 `.dockerignore` | 高 | `.env` 進 image |
| 6 | `ARG` secret 殘留 history | 嚴重 | key 洩漏到 registry |
| 7 | root user | 高 | 被打穿後是 root |
| 8 | shell form CMD | 中 | graceful shutdown 壞掉 |
| 9 | 無 `no-new-privileges` | 中 | setuid 提權 |
| 10 | 無 HEALTHCHECK | 低 | orchestration 看不到健康狀態 |

</details>

---

## 測試用例

以下用 shell 腳本驗證修復後的 Dockerfile 是否達標：

```bash
#!/bin/bash
# audit_check.sh
IMAGE=myapp-audited

echo "=== 建置 image ==="
docker build -t "$IMAGE" .

echo ""
echo "=== T1：確認非 root ==="
UID_IN=$(docker run --rm "$IMAGE" id -u)
[ "$UID_IN" != "0" ] \
  && echo "PASS T1（uid=$UID_IN，非 root）" \
  || echo "FAIL T1（uid=$UID_IN，是 root！）"

echo ""
echo "=== T2：確認無 curl/wget/git/vim ==="
for BIN in curl wget git vim; do
  docker run --rm "$IMAGE" which "$BIN" > /dev/null 2>&1 \
    && echo "FAIL T2（$BIN 在 image 裡）" \
    || echo "PASS T2（$BIN 不存在）"
done

echo ""
echo "=== T3：API_KEY 不在 docker history ==="
docker history --no-trunc "$IMAGE" | grep -i "api_key" \
  && echo "FAIL T3（history 裡有 api_key！）" \
  || echo "PASS T3（history 沒有 api_key）"

echo ""
echo "=== T4：PID 1 不是 sh ==="
PID1_CMD=$(docker run --rm --entrypoint cat "$IMAGE" /proc/1/cmdline 2>/dev/null \
           | tr '\0' ' ' | head -c 50 || echo "無法判斷")
echo "PID1 cmdline: $PID1_CMD"
echo "$PID1_CMD" | grep -qv "^/bin/sh" \
  && echo "PASS T4（PID 1 不是 sh）" \
  || echo "FAIL T4（PID 1 是 sh，shell form CMD）"

echo ""
echo "=== T5：HEALTHCHECK 存在 ==="
docker inspect "$IMAGE" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
hc = d[0].get('Config', {}).get('Healthcheck', {})
if hc and hc.get('Test'):
    print('PASS T5（HEALTHCHECK:', hc['Test'], '）')
else:
    print('FAIL T5（沒有 HEALTHCHECK）')
"

echo ""
echo "=== T6：.env 沒有進 image ==="
docker run --rm "$IMAGE" test -f /app/.env 2>/dev/null \
  && echo "FAIL T6（.env 在 image 裡！）" \
  || echo "PASS T6（.env 不在 image 裡）"

echo ""
echo "=== T7：trivy 掃描（需要安裝 trivy）==="
if command -v trivy > /dev/null; then
  trivy image --exit-code 1 --severity CRITICAL "$IMAGE" \
    && echo "PASS T7（無 CRITICAL CVE）" \
    || echo "FAIL T7（有 CRITICAL CVE，需要更換 base image 或更新版本）"
else
  echo "SKIP T7（trivy 未安裝）"
fi
```

---

## 自我檢核

- [ ] 能說出 `ARG` 和 `--secret` 的本質差異：前者進 layer metadata，後者只存在 build 時記憶體
- [ ] 修復後的 image `docker history` 看不到任何 secret
- [ ] `docker run --rm myimage id` 輸出非 root（uid != 0）
- [ ] `CMD` 是 exec form（JSON array），不是 shell form（字串）
- [ ] 有 `.dockerignore`，`docker build` 的 context 不包含 `.env` 和 `.git`
- [ ] 有 `HEALTHCHECK`，`docker inspect` 能看到
- [ ] 能用 `trivy image` 掃描並解讀輸出
- [ ] 知道 `no-new-privileges` 要在哪個層面設定（compose.yml 的 `security_opt`）

---

## 延伸閱讀

如果你做完這個練習，想更進一步：

- **Ch 22（capabilities drop）**：試著在 compose.yml 加 `cap_drop: [ALL]`，看看哪些 API 會爛掉
- **Ch 23（rootless Docker）**：在 rootless 模式下，container 的 root 映射到 host 的哪個 UID？
- **Ch 19（image 掃描）**：用 `trivy image --format sarif` 產出 SARIF 格式，整合進 GitHub Actions

---

上一個練習：[練習 B：FastAPI + PostgreSQL + Redis + Nginx](./practice-b-compose-stack.md)

Final Project：[完整 CI Pipeline](./final-project-ci-pipeline.md)
