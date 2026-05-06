# Ch 24 — Docker 安全最佳實踐

> 目標：掌握 AI workload 的 Docker 安全配置，理解五個核心原則並能寫出符合安全規範的 Dockerfile，學會用工具掃描映像檔漏洞。

---

## AI Workload 的 Docker 特殊性

一般 Web 服務容器化已經有成熟套路，但 AI workload 有幾個地方讓安全配置更複雜：

```
一般 Web 服務               AI Workload
─────────────────────────  ─────────────────────────
映像檔 < 500MB             映像檔 2-10GB（含模型）
無 GPU 依賴                GPU pass-through（--gpus all）
外部 API 呼叫少             高頻呼叫 OpenAI / Anthropic API
環境變數 1-3 個            API key 多（模型、向量DB、監控）
靜態推論流程               LangChain Agent 可動態執行 shell
```

這些差異直接影響安全配置的重點：
- 模型檔案大 → 多階段 build 更重要，否則 image layer 留存訓練資料
- GPU pass-through → `--gpus` 不等於 `--privileged`，不要混用
- 外部 API 呼叫多 → API key 洩漏面更廣，secret 管理更嚴格
- Agent 可能執行工具 → 容器內不能有多餘的 binary 或系統呼叫權限

---

## 五個核心原則

### 原則 1：Non-root User

預設 Docker 容器以 root 跑。如果容器被入侵，攻擊者等同拿到 root 權限，可以讀任何環境變數、任何掛載的 volume。

```dockerfile
# 錯誤：什麼都不設，預設 root
FROM python:3.11-slim
COPY . /app
RUN pip install -r requirements.txt
CMD ["python", "main.py"]

# 正確：建立非特權使用者
FROM python:3.11-slim

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home nonroot

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=nonroot:appgroup . .

USER nonroot

CMD ["python", "main.py"]
```

`--no-create-home` 避免多出 `/home/nonroot` 目錄。`--chown` 在 COPY 時一起設權限，比跑完再 `chown -R` 效率高（少一個 layer）。

---

### 原則 2：Read-only Filesystem

```bash
# 執行時加 --read-only
docker run --read-only \
  --tmpfs /tmp:size=100m \
  --tmpfs /app/cache:size=500m \
  my-llm-service
```

`--read-only` 讓容器根目錄唯讀，攻擊者即使進來也無法寫 persistence。`tmpfs` 給真正需要寫的目錄（暫存、cache），重啟就清空，不會留存。

AI 服務常見的寫入需求：
- `/tmp`：FastAPI 暫存上傳檔案
- `/app/cache`：HuggingFace 模型 cache（如果在 container 內下載）
- `/var/log/app`：應用程式日誌（建議改成 stdout/stderr，讓 Docker 管）

```dockerfile
# Dockerfile 裡也可以宣告
VOLUME ["/tmp", "/app/cache"]
```

---

### 原則 3：最小映像檔與多階段 Build

```dockerfile
# 階段一：build stage，安裝所有 build 工具
FROM python:3.11 AS builder

WORKDIR /build
COPY requirements.txt .

# 安裝到 /install 目錄，不污染系統
RUN pip install --no-cache-dir \
    --prefix=/install \
    -r requirements.txt

# 階段二：runtime stage，只複製需要的東西
FROM python:3.11-slim AS runtime

# 複製編譯好的套件
COPY --from=builder /install /usr/local

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home nonroot

WORKDIR /app
COPY --chown=nonroot:appgroup src/ .

USER nonroot
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`python:3.11` vs `python:3.11-slim` 的差異：
- `python:3.11`：約 1.0GB，包含 gcc、make、各種開發工具
- `python:3.11-slim`：約 150MB，只有 Python runtime
- `python:3.11-alpine`：約 50MB，但 musl libc 可能造成套件相容性問題

AI 服務通常不要用 alpine，因為 numpy/torch 的 C extension 在 musl libc 下有相容性地雷。

---

### 原則 4：環境變數與 Secret 管理

這是 AI workload 最常出問題的地方。

```dockerfile
# 絕對錯誤：key 寫進 Dockerfile
ENV OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
ENV ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx

# 也是錯誤：COPY .env 進映像檔
COPY .env /app/.env
```

這樣做的後果：key 被燒進 image layer，`docker history` 或 push 到 registry 就洩漏。

```bash
# 正確做法一：執行時傳入
docker run --env-file .env my-llm-service

# 正確做法二：Docker Secret（Swarm 或 Compose v3）
docker secret create openai_key ./openai_key.txt

# compose 裡引用
services:
  llm-api:
    image: my-llm-service
    secrets:
      - openai_key
    environment:
      - OPENAI_API_KEY_FILE=/run/secrets/openai_key

secrets:
  openai_key:
    external: true
```

```bash
# .dockerignore 必寫
.env
*.env
.env.*
secrets/
*.key
*.pem
```

---

### 原則 5：Seccomp / AppArmor Profile

Seccomp（Secure Computing Mode）在系統呼叫層面限制容器能做什麼。

Docker 預設已有一個 seccomp profile，但允許約 300+ 個 syscall。AI 服務其實只需要一小部分。

```bash
# 查看 Docker 預設 seccomp profile
docker run --rm -it alpine cat /proc/self/status | grep Seccomp

# 用嚴格的 seccomp profile 執行
docker run --security-opt seccomp=./seccomp-ai.json my-llm-service
```

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64"],
  "syscalls": [
    {
      "names": [
        "read", "write", "open", "close", "stat", "fstat",
        "mmap", "mprotect", "munmap", "brk", "rt_sigaction",
        "rt_sigprocmask", "ioctl", "pread64", "pwrite64",
        "readv", "writev", "access", "pipe", "select",
        "sched_yield", "mremap", "msync", "mincore", "madvise",
        "dup", "dup2", "nanosleep", "getpid", "socket",
        "connect", "accept", "sendto", "recvfrom", "sendmsg",
        "recvmsg", "shutdown", "bind", "listen", "getsockname",
        "getpeername", "socketpair", "setsockopt", "getsockopt",
        "clone", "fork", "execve", "exit", "wait4", "kill",
        "uname", "fcntl", "fsync", "getcwd", "chdir",
        "getdents64", "lseek", "openat", "newfstatat"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

AI 服務明確不需要的危險 syscall：
- `ptrace`：除錯用，正常服務不需要，但惡意程式碼很愛用
- `kexec_load`：載入新 kernel
- `mount`：掛載檔案系統
- `setns`：切換 namespace（容器逃逸常用）

---

## 常見失誤盤點

### 失誤一：把 .env 複製進映像檔

```dockerfile
# 錯誤
COPY . /app          # 如果沒有 .dockerignore，.env 也進去了
COPY .env /app/.env  # 明顯錯誤
```

驗證方式：`docker history <image>` 或 `docker run --rm <image> env`

### 失誤二：用 --privileged

```bash
# 有人為了解決 GPU 問題直接這樣
docker run --privileged --gpus all my-llm-service
```

`--privileged` 讓容器幾乎等同 host root，可以載入 kernel module、存取所有設備。GPU pass-through 正確寫法：

```bash
# 正確：只給 GPU 存取
docker run --gpus all my-llm-service
# 或限定特定 GPU
docker run --gpus '"device=0"' my-llm-service
```

### 失誤三：沒有限制 network

```bash
# 預設 bridge network，容器可以任意對外連線
docker run my-llm-service

# 內部服務不需要對外：用 internal network
docker network create --internal ai-internal

docker run --network ai-internal my-vector-db
docker run --network ai-internal --network ai-external my-llm-api
```

---

## 完整安全 Dockerfile 範例

FastAPI + LangChain AI 服務：

```dockerfile
# ── Stage 1: builder ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.11-slim AS runtime

# 建立非特權使用者
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup \
            --no-create-home --shell /bin/false nonroot

# 複製已安裝的套件
COPY --from=builder /install /usr/local

WORKDIR /app

# 只複製程式碼，不複製設定檔
COPY --chown=nonroot:appgroup src/ ./src/
COPY --chown=nonroot:appgroup main.py .

# 切換使用者
USER nonroot

# 宣告 runtime 需要寫入的目錄（配合 --tmpfs 使用）
VOLUME ["/tmp", "/app/chroma_cache"]

EXPOSE 8000

# 健康檢查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--no-access-log"]
```

對應的 `.dockerignore`：

```
.env
*.env
.env.*
__pycache__/
*.pyc
*.pyo
.git/
.github/
tests/
*.md
secrets/
*.key
*.pem
.DS_Store
```

執行指令：

```bash
docker run \
  --read-only \
  --tmpfs /tmp:size=100m \
  --tmpfs /app/chroma_cache:size=2g \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=./seccomp-ai.json \
  --cap-drop ALL \
  --gpus '"device=0"' \
  --env-file .env \
  --network ai-internal \
  --memory 4g \
  --cpus 2 \
  my-llm-service:latest
```

---

## Image Scanning

### docker scout

```bash
# 掃描本地 image
docker scout cves my-llm-service:latest

# 掃描並輸出報告
docker scout cves --format sarif my-llm-service:latest > cves.sarif

# 快速看摘要
docker scout quickview my-llm-service:latest
```

### trivy

```bash
# 安裝
brew install trivy  # macOS
# 或
docker run aquasec/trivy image my-llm-service:latest

# 掃描高嚴重度漏洞
trivy image --severity HIGH,CRITICAL my-llm-service:latest

# 掃描設定檔（Dockerfile）
trivy config ./Dockerfile

# CI 整合：發現漏洞就失敗
trivy image --exit-code 1 --severity CRITICAL my-llm-service:latest
```

重點看的欄位：套件名稱、CVE 編號、Fix Version。如果有 Fix Version 就升版。如果沒有，評估是否需要換替代套件。

---

## 自我檢核

- [ ] 我能解釋為什麼 AI workload 的 Docker 安全比一般服務更複雜
- [ ] 我能寫出包含 non-root user 的 Dockerfile
- [ ] 我知道 `--read-only` 搭配 `tmpfs` 的用途和寫法
- [ ] 我能用多階段 build 縮小 image 大小
- [ ] 我知道 API key 不能出現在 Dockerfile 或被 COPY 進 image
- [ ] 我能解釋 seccomp profile 是什麼，以及 `ptrace` 為何要禁
- [ ] 我能列出三個常見的 Docker 安全失誤
- [ ] 我會用 trivy 掃描 image 並看懂輸出

容器安全打好底之後，下一層是編排層——多個容器怎麼在 Kubernetes 裡安全地互動。

→ [Ch 25 Kubernetes 入門（AI 導向）](./25-kubernetes-basics.md)
