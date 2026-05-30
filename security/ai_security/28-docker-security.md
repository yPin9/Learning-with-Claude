# Ch 28 — Docker 安全

> **目標**：能為 LLM 服務寫安全的 Dockerfile，理解 container escape 的原理，知道 AI workload 特有的 Docker 安全考量。
>
> **環境**：Docker 24+, Ubuntu 22.04, Ollama + llama3.2:3b, Trivy

---

## 為什麼需要這個？

Ch 0 裝 Ollama 的時候，你大概跑了 `docker run -d --gpus all -p 11434:11434 ollama/ollama`。一行搞定，模型跑起來了。但這行指令做了三件危險的事：

1. **`--gpus all`**：把 host 的所有 GPU 直通進 container，GPU memory 沒有 container 隔離
2. **`-p 11434:11434`**：Ollama API 對外暴露，沒有 auth
3. **預設用 root 跑**：container 裡的 process 是 root——如果有 container escape 漏洞，攻擊者直接拿到 host 的 root

傳統 web 服務的 Docker 安全經驗你可能有：non-root user、minimal base image、read-only filesystem。但 AI workload 多了幾個傳統服務沒有的挑戰：GPU passthrough 打破 namespace 隔離、model 檔案動輒幾十 GB 需要 volume mount、inference server 需要大量 shared memory（`--shm-size`）。這些需求每一個都在擴大攻擊面。

---

## 先建立直覺

把 Docker container 想成一間出租套房。房東（host kernel）把房子隔成幾間，每間房客（container）只看到自己的房間。但 GPU passthrough 等於在隔間牆上開了一個大洞——房客可以透過這個洞看到隔壁房間的東西（GPU memory）。

```
傳統 container 隔離：
┌───────────────────────────────────────────┐
│  Host Kernel                              │
│  ┌──────────┐  ┌──────────┐               │
│  │ Container│  │ Container│  完整隔離     │
│  │ A (web)  │  │ B (db)   │  ← namespace  │
│  │          │  │          │    cgroup      │
│  └──────────┘  └──────────┘               │
└───────────────────────────────────────────┘

AI workload 的 container「隔離」：
┌───────────────────────────────────────────┐
│  Host Kernel                              │
│  ┌──────────┐  ┌──────────┐               │
│  │ Container│  │ Container│               │
│  │ A (LLM)  │  │ B (LLM)  │              │
│  │  ↕ GPU ↕ │  │  ↕ GPU ↕ │              │
│  └────┬─────┘  └────┬─────┘              │
│       │    ┌────────┐│                    │
│       └────┤  GPU   ├┘  ← 共享 GPU memory │
│            │  VRAM  │     沒有隔離！       │
│            └────────┘                     │
└───────────────────────────────────────────┘
```

---

## 核心概念：LLM 服務的 Docker 加固

### 範例一：安全的 Ollama Dockerfile

先看一個不安全的啟動方式（多數教程教的）：

```bash
# 不安全：root 執行、全部 GPU、綁 0.0.0.0、無 resource limit
docker run -d \
  --gpus all \
  -p 11434:11434 \
  -v ~/.ollama:/root/.ollama \
  ollama/ollama
```

加固版本：

```dockerfile
# Dockerfile.ollama-hardened
FROM ollama/ollama:latest AS base

# Stage 2: 用 non-root user 執行
FROM base
RUN groupadd -r ollama && useradd -r -g ollama -m -d /home/ollama ollama
RUN mkdir -p /home/ollama/.ollama/models && \
    chown -R ollama:ollama /home/ollama/.ollama

USER ollama
WORKDIR /home/ollama

# Health check 不依賴 shell（某些 hardened image 沒有 /bin/sh）
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["ollama", "list"]

ENV OLLAMA_HOST=127.0.0.1:11434
ENV OLLAMA_MODELS=/home/ollama/.ollama/models

EXPOSE 11434
ENTRYPOINT ["ollama", "serve"]
```

```bash
# 加固啟動
docker build -f Dockerfile.ollama-hardened -t ollama-hardened .

docker run -d \
  --name ollama-secure \
  --gpus '"device=0"' \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=default \
  --memory=16g \
  --shm-size=2g \
  --pids-limit=256 \
  -p 127.0.0.1:11434:11434 \
  -v ollama-models:/home/ollama/.ollama/models:rw \
  ollama-hardened
```

逐行解釋加固措施：

| Flag | 作用 | 為什麼需要 |
|------|------|-----------|
| `--gpus '"device=0"'` | 只給一張特定 GPU | 限制 blast radius，不把所有 GPU 暴露進去 |
| `--read-only` | rootfs 唯讀 | 防止攻擊者在 container 裡寫入惡意檔案 |
| `--tmpfs /tmp:rw,noexec,nosuid` | /tmp 可寫但不可執行 | 給程式暫存空間但禁止執行檔案 |
| `--cap-drop ALL` | 丟棄所有 Linux capabilities | root 的細粒度權限全部移除 |
| `--security-opt no-new-privileges` | 禁止提權 | 阻止 setuid binary 的利用 |
| `--memory=16g` | 記憶體上限 | 防止 model loading 吃光 host 記憶體 |
| `--shm-size=2g` | 共享記憶體上限 | PyTorch/CUDA 需要 shared memory |
| `--pids-limit=256` | process 數上限 | 防止 fork bomb |
| `-p 127.0.0.1:11434:11434` | 只綁 loopback | 不暴露給外部網路 |

---

## 底層機制：Namespace / Cgroup 隔離如何被 GPU Passthrough 打破

Docker 的隔離靠兩個 Linux kernel 機制：namespace（隔離可見性）和 cgroup（限制資源用量）。

```
Linux Namespace 隔離：
┌─────────────────────────────────────────────────┐
│  PID namespace   → container 看不到 host 的 PID │
│  NET namespace   → container 有自己的網路棧      │
│  MNT namespace   → container 有自己的 filesystem │
│  USER namespace  → container 的 root ≠ host root │
│  IPC namespace   → container 間 IPC 隔離         │
│  UTS namespace   → container 有自己的 hostname   │
└─────────────────────────────────────────────────┘

Cgroup 限制：
┌─────────────────────────────────────────────────┐
│  CPU    → 限制 CPU 時間                          │
│  Memory → 限制記憶體用量                          │
│  PIDs   → 限制 process 數量                      │
│  Block I/O → 限制磁碟 I/O                        │
│  GPU    → ???  ← 沒有原生的 GPU cgroup！          │
└─────────────────────────────────────────────────┘
```

問題核心：Linux kernel 的 cgroup v1/v2 都沒有原生的 GPU 資源隔離。NVIDIA Container Toolkit（`nvidia-container-toolkit`）透過 `nvidia-container-runtime` 在 container 啟動時把 GPU device（`/dev/nvidia0`、`/dev/nvidiactl`、`/dev/nvidia-uvm`）mount 進 container。這是 **device passthrough**——container 直接存取 host 的 GPU 硬體。

後果：

- **GPU memory 沒有隔離**：Container A 分配的 GPU memory，Container B 可能透過 CUDA API 讀取殘留資料
- **GPU 計算資源沒有硬性隔離**：MPS（Multi-Process Service）或 MIG（Multi-Instance GPU）可以做軟隔離，但不是所有 GPU 都支援
- **Side-channel attack**：攻擊者可以透過 GPU 使用率變化推斷隔壁 container 在跑什麼模型

NVIDIA MIG（Multi-Instance GPU，多實例 GPU）是 A100/H100 才有的功能，可以把一張 GPU 切成最多 7 個硬體隔離的 instance。這是目前唯一真正的 GPU container 隔離方案，但你的 consumer GPU 沒有這個功能。

---

## 進一步用法：用 Trivy 掃描 LLM Image 漏洞

### 範例二：掃描 Ollama 和 Python LLM Image

安裝 Trivy：

```bash
sudo apt-get install -y wget apt-transport-https gnupg lsb-release
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | \
  sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] \
  https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | \
  sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update && sudo apt-get install -y trivy
```

掃描 Ollama 官方 image：

```bash
# 掃描 vulnerability
trivy image ollama/ollama:latest

# 只看 HIGH 和 CRITICAL
trivy image --severity HIGH,CRITICAL ollama/ollama:latest

# 掃描你自己的 hardened image
trivy image ollama-hardened:latest
```

對比 Python base image 的攻擊面：

```bash
# python:3.11 — 完整版，900+ MB，大量不必要的套件
trivy image python:3.11

# python:3.11-slim — 精簡版，約 150 MB
trivy image python:3.11-slim

# python:3.11-alpine — 最小，約 50 MB，但 musl libc 可能有相容性問題
trivy image python:3.11-alpine
```

用 multi-stage build 縮小 LLM 服務 image：

```dockerfile
# Dockerfile.llm-service — 多階段建構
# Stage 1: 安裝依賴
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: 執行環境
FROM python:3.11-slim
RUN groupadd -r llm && useradd -r -g llm -m llm
COPY --from=builder /install /usr/local
COPY --chown=llm:llm app/ /app/
USER llm
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
```

---

## 對比與取捨

| 面向 | Docker 預設啟動 | 加固後 |
|------|----------------|--------|
| **攻擊面** | 大：root + 全 GPU + 無 resource limit | 小：non-root + 單 GPU + 嚴格限制 |
| **Container escape 風險** | 高：root + all capabilities | 低：non-root + cap-drop ALL |
| **GPU 資料洩漏** | 高：所有 GPU memory 可存取 | 中：限制單張 GPU，但仍無硬體隔離 |
| **效能影響** | 無 | 極小：read-only fs 和 cap-drop 幾乎不影響 inference 效能 |
| **操作複雜度** | 低：一行 `docker run` | 中：需要寫 Dockerfile + 長啟動指令 |
| **Network 暴露** | 高：`0.0.0.0:11434` 對外開放 | 低：只綁 `127.0.0.1` |
| **Image 大小** | 大：python:3.11 約 900 MB | 小：python:3.11-slim 約 150 MB |
| **合規性** | 不符合 CIS Benchmark | 符合大部分 CIS Docker Benchmark |

---

## 踩雷集錦

**1. `--gpus all` 讓 container 能讀其他 container 的 GPU memory**

兩個 container 都用 `--gpus all` 時，它們共享同一張 GPU 的 VRAM。Container A 釋放的 GPU memory，Container B 可能透過 `cudaMalloc` + 讀取未初始化記憶體拿到殘留資料。這意味著 LLM 的 inference 結果（包含使用者的 prompt 和回答）可能被隔壁 container 讀到。用 `--gpus '"device=0"'` 至少限制到特定 GPU，但在同一張 GPU 上的隔離仍然不存在。

**2. Ollama 的 model 目錄 mount 要注意權限**

Ollama 把 model 存在 `~/.ollama/models/`。很多人為了方便直接 `-v /home/user:/root`——把整個 home 目錄 mount 進去。container 裡的 root 可以讀你的 SSH key、GPG key、`.bash_history`。正確做法是只 mount model 目錄：`-v ollama-models:/home/ollama/.ollama/models:rw`。用 named volume 而非 bind mount 更安全。

**3. Python base image 的選擇直接影響攻擊面**

`python:3.11`（完整版）包含 gcc、make、perl 等編譯工具——你的 inference server 不需要這些東西，但攻擊者會很感謝你留了它們。一旦取得 RCE（Remote Code Execution，遠端程式碼執行），這些工具讓攻擊者可以在 container 裡編譯 exploit。用 `python:3.11-slim` 或 multi-stage build 去掉編譯工具。

**4. Health check 不要依賴 `/bin/sh`**

很多人寫 `HEALTHCHECK CMD curl -f http://localhost:11434/`——這依賴 container 裡有 `curl` 和 `/bin/sh`。hardened image（如 distroless）沒有 shell。用 binary 直接執行：`HEALTHCHECK CMD ["ollama", "list"]`，或在 application 裡內建 health endpoint。

**5. `--shm-size` 預設只有 64 MB**

Docker 預設給 `/dev/shm` 64 MB。PyTorch DataLoader 用 shared memory 做 IPC，CUDA runtime 也依賴 `/dev/shm`。64 MB 不夠的時候，inference 直接 crash 但錯誤訊息不明顯（`Bus error` 或 segfault）。設 `--shm-size=2g` 或用 `--ipc=host`（但 `--ipc=host` 會破壞 IPC namespace 隔離）。

---

## 進階

### Seccomp Profile 自訂

Docker 預設的 seccomp profile 禁止了約 44 個 syscall（如 `reboot`、`kexec_load`、`mount`）。你可以進一步收緊——LLM inference server 不需要 `ptrace`、`personality`、`keyctl` 等 syscall。

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {
      "names": [
        "read", "write", "open", "close", "stat", "fstat",
        "mmap", "mprotect", "munmap", "brk", "ioctl",
        "socket", "connect", "bind", "listen", "accept",
        "clone", "execve", "wait4", "kill", "getpid",
        "futex", "epoll_wait", "epoll_ctl", "eventfd2"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

```bash
docker run --security-opt seccomp=llm-seccomp.json ...
```

注意：CUDA runtime 需要一些不常見的 syscall（如 `ioctl` 操作 `/dev/nvidia*`）。自訂 seccomp profile 前，先用 `strace` 記錄你的 inference server 實際用了哪些 syscall，再據此開放。

### Docker Content Trust

啟用 Docker Content Trust（DCT）確保你 pull 的 image 有簽章：

```bash
export DOCKER_CONTENT_TRUST=1
docker pull ollama/ollama:latest
# 如果 image 沒有簽章，pull 會失敗
```

---

## 動手練習

1. **建構加固版 Ollama image**：用上面的 `Dockerfile.ollama-hardened` 建構 image，用加固的 `docker run` 指令啟動。驗證 Ollama 正常運作（`curl http://127.0.0.1:11434/api/tags`），然後嘗試在 container 裡寫檔案（`docker exec ollama-secure touch /test`）——應該失敗。

2. **Trivy 掃描對比**：分別掃描 `python:3.11`、`python:3.11-slim`、`ollama/ollama:latest` 三個 image 的 HIGH/CRITICAL 漏洞數量。記錄差異。

3. **GPU memory 殘留實驗**（需要 GPU）：啟動兩個用 `--gpus all` 的 container。在 Container A 裡用 CUDA 分配記憶體並寫入可辨識的 pattern，然後釋放。在 Container B 裡分配同樣大小的記憶體，檢查是否能讀到殘留資料。

4. **Network 暴露測試**：分別用 `-p 11434:11434` 和 `-p 127.0.0.1:11434:11434` 啟動 Ollama。從另一台機器（或不同 network namespace）嘗試存取 Ollama API，驗證後者無法從外部存取。

---

## 重點整理

- AI workload 的 Docker 安全比傳統 web 服務更難，核心原因是 GPU passthrough 打破 namespace/cgroup 隔離。
- `--gpus all` 讓所有 GPU 暴露進 container，且同一張 GPU 上的多個 container 之間沒有 memory 隔離。
- Ollama 預設用 root 執行、綁 `0.0.0.0`、沒有 auth——三個問題都要處理。
- Docker 加固的基本動作：non-root user、`--cap-drop ALL`、`--read-only`、`--security-opt no-new-privileges`、限制 port binding。
- Python base image 的選擇直接影響攻擊面——用 `python:3.11-slim` 加 multi-stage build。
- Trivy 可以掃描 image 的 CVE 漏洞，養成 build 完就掃的習慣。
- `--shm-size` 預設 64 MB 對 AI workload 不夠——設 2g 以上。

---

## 自我檢核

- 解釋為什麼 `--gpus all` 是安全風險。GPU container 隔離和 CPU/memory 的 cgroup 隔離有什麼根本差異？
- 列出 5 個 Docker 加固 flag 並解釋各自的作用。
- 為什麼 `python:3.11` 比 `python:3.11-slim` 的攻擊面大？這和 LLM 服務有什麼關係？
- Seccomp profile 和 capabilities 分別限制什麼？為什麼 AI workload 自訂 seccomp 要特別注意？
- 如果你的 LLM 服務必須用 `--ipc=host`，你會用什麼其他措施來補償 IPC namespace 隔離的喪失？

---

## 延伸閱讀

### 官方文件

- **[Docker Security Best Practices](https://docs.docker.com/develop/security-best-practices/)**
  - **讀哪裡**：Dockerfile best practices 和 runtime security 段落
  - **學什麼**：Docker 官方認可的安全基線

- **[CIS Docker Benchmark](https://www.cisecurity.org/benchmark/docker)**
  - **讀哪裡**：Section 4（Container Runtime）和 Section 5（Container Security）
  - **學什麼**：業界標準的 Docker 安全檢查清單

### 書籍

- **"Container Security"**（Liz Rice, O'Reilly）
  - **讀哪裡**：Chapter 4（Container Isolation）和 Chapter 6（Linux Security Features）
  - **學什麼**：namespace、cgroup、seccomp、capabilities 的底層原理

### NVIDIA GPU 安全

- **[NVIDIA Container Toolkit Security](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/security-best-practices.html)**
  - **讀哪裡**：Security best practices 段落
  - **學什麼**：GPU passthrough 的安全限制和 MIG 隔離

---

→ [Ch 29 — Kubernetes 入門](./29-kubernetes-basics.md)
