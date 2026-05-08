# Ch 1 — Docker 架構全貌

> 目標：看懂 Docker 從 CLI 到容器進程的完整調用鏈，搞清楚 dockerd / containerd / runc 各自管什麼，以及 OCI 標準為什麼存在。

---

## 三角架構

```
+-----------------+        REST API         +------------------+
|  Docker Client  | ----------------------> |  Docker Daemon   |
|  (docker CLI)   | <---------------------- |   (dockerd)      |
+-----------------+   /var/run/docker.sock  +------------------+
                                                     |
                                              pull / push
                                                     |
                                            +------------------+
                                            |    Registry      |
                                            | (Docker Hub /    |
                                            |  GHCR / private) |
                                            +------------------+
```

三個角色：

| 角色 | 是什麼 | 做什麼 |
|------|--------|--------|
| Docker Client | `docker` 這支 CLI 程式 | 解析指令，發 REST 請求給 daemon |
| Docker Daemon (dockerd) | 背景常駐服務 | 管理 image、container、network、volume |
| Registry | image 倉庫 | 存放和分發 image，類比 GitHub 但存 image |

Client 和 Daemon 的溝通預設走 Unix socket `/var/run/docker.sock`，也可以走 TCP（遠端管理用，要配 TLS）。

---

## Daemon（dockerd）是什麼

`dockerd` 是一個 REST API server。你跑 `docker ps`，本質上是 Client 送了一個 `GET /containers/json` 到 daemon。

```bash
# 直接 curl socket 驗證這件事
curl --unix-socket /var/run/docker.sock http://localhost/version
# 回傳 JSON，和 docker version 的資訊一樣
```

Daemon 監聽在 `/var/run/docker.sock`（Unix socket），或設定後可加開 `tcp://0.0.0.0:2376`（TLS）。  
暴露 TCP socket 不加 TLS = 所有人都能控制你的 Docker = 等於給 root 權限，這是一個經典的 CVE 和 CTF 題目。

---

## 層次鏈：dockerd → containerd → runc

現代 Docker 不是 dockerd 直接管容器進程，中間多了幾層：

```
docker CLI
    |
    | REST API (Unix socket)
    v
 dockerd
    |
    | gRPC
    v
 containerd                  <- 管容器生命週期
    |
    | 建立 container 時
    v
 containerd-shim-runc-v2     <- 每個容器一個 shim 進程
    |
    v
 runc                        <- OCI runtime，真正 fork+exec 容器進程
    |
    v
 container process           <- 你的應用程式（PID 1 in container）
```

**為什麼要這樣分層？**

- **containerd**（CNCF 專案）：負責 image pull、snapshot 管理、容器生命週期。設計上是獨立的，Kubernetes 也直接用 containerd，不需要 dockerd。
- **containerd-shim**（containerd-shim-runc-v2）：每個容器各跑一個 shim 進程。作用是讓 containerd 可以重啟而不殺死所有容器（shim 繼續持有容器進程），同時也負責 stdio 橋接。
- **runc**（OCI runtime）：真正做 namespace + cgroup 設定、fork/exec 容器 init 進程的那一層。跑完 exec 就退出，它不是常駐進程。

```bash
# 確認 containerd 在跑
systemctl status containerd

# 看 shim 進程
ps aux | grep containerd-shim
```

---

## OCI（Open Container Initiative）標準

OCI 是 Linux Foundation 下的標準組織，2015 年 Docker 把 runc 捐出來後成立。

| OCI 規範 | 說明 |
|---------|------|
| Image Spec | 定義 image 的格式：layer tarball + config JSON + manifest |
| Runtime Spec | 定義 `config.json` 的格式，告訴 runtime 怎麼啟動容器（namespace、cgroup、rootfs 路徑等） |
| Distribution Spec | 定義 registry 的 HTTP API（pull/push 協議） |

**為什麼存在？** 沒有標準之前，Docker image 格式和 runtime 是 Docker Inc. 私有的。有了 OCI，任何 runtime（runc、crun、kata-containers）都可以跑符合規範的 image，任何工具（Podman、buildah、nerdctl）都可以建符合規範的 image。生態不被單一廠商鎖死。

---

## 容器 vs 虛擬機

這個比較要清楚，因為「容器就是輕量 VM」是最常見的誤解。

```
虛擬機 (VM)                          容器 (Container)
+------------------------+           +------------------------+
|  App A  |  App B       |           |  App A  |  App B       |
+------------------------+           +------------------------+
|  Guest OS (full)       |           |  (沒有 Guest OS)       |
+------------------------+           +------------------------+
|  Hypervisor            |           |  Container Runtime     |
+------------------------+           | (namespace + cgroup)   |
|  Host OS / Hardware    |           +------------------------+
+------------------------+           |  Host OS / Kernel      |
                                     +------------------------+
                                     |  Hardware              |
                                     +------------------------+
```

| 維度 | VM | Container |
|------|----|---------:|
| 隔離層級 | Hypervisor，硬體虛擬化 | kernel namespace，OS 層軟隔離 |
| 啟動時間 | 數十秒 | 毫秒～秒 |
| 鏡像大小 | GB 級（含完整 OS） | MB 級（只有應用層） |
| 記憶體開銷 | 高（每個 VM 獨立 kernel） | 低（共用 host kernel） |
| 安全隔離 | 強（hypervisor 邊界） | 弱（共用 kernel，一個 kernel exploit 打穿所有容器） |
| 適用情境 | 強隔離需求、不同 OS | 快速部署、微服務、CI |

**容器共用 host kernel 對安全的含義：**

容器裡的程式仍然在用 host 的 kernel syscall 介面。如果 kernel 有漏洞，容器裡的攻擊者可以嘗試 kernel exploit 逃逸。`--privileged` 容器幾乎等同於直接在 host 上跑。這就是為什麼 Ch 9（capabilities + seccomp）和 Ch 21（non-root / readonly）很重要。

---

## Registry

| Registry | 說明 |
|---------|------|
| Docker Hub | 預設 registry，`nginx` = `docker.io/library/nginx` |
| GHCR | GitHub Container Registry，`ghcr.io/<user>/<repo>` |
| ECR | AWS Elastic Container Registry |
| 私有 registry | 自架，用 `registry:2` image 或 Harbor |

Image 名稱完整格式：`<registry>/<namespace>/<name>:<tag>@<digest>`  
省略 registry 時預設 `docker.io`，省略 namespace 時預設 `library`（官方 image）。

```bash
# 以下三個等價
docker pull nginx
docker pull nginx:latest
docker pull docker.io/library/nginx:latest
```

---

## 自我檢核

- [ ] 能畫出 docker CLI → dockerd → containerd → runc → container process 的層次
- [ ] 知道 `/var/run/docker.sock` 是什麼、為什麼暴露 TCP 要謹慎
- [ ] 能解釋 OCI 標準為什麼存在，Image Spec / Runtime Spec 各管什麼
- [ ] 能說明容器和 VM 最根本的差異在哪裡
- [ ] 知道 `docker.io/library/nginx:latest` 的完整意義

下一章進入 image 和 container 的具體操作，從 layer 結構到常用指令全部跑一遍。

→ [Ch 2 Image 與 Container](./02-image-and-container.md)
