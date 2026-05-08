# Docker 學習筆記：從容器操作到生產部署

> 給想真正搞懂 Docker 的工程師——不只會跑，還要知道底下在幹嘛。

從 `docker run` 出發，往下挖 namespace / cgroup / OverlayFS，往上走 Compose 多服務、registry 管理、資安 hardening、Swarm 編排。最後用一條完整的 CI pipeline 收尾。

## 為什麼學這個？

- **底層清楚，除錯才快**：container 出問題時，知道 namespace / cgroup 的人比只懂指令的人快十倍找到根因。
- **Dockerfile 寫好很難**：隨便寫出來的 image 可以比需要的大 10 倍、有 50 個 CVE、用 root 跑——這門課三個問題都會解決。
- **每個領域都用得到**：AI 服務、嵌入式交叉編譯、CTF 環境、Web 後端，容器是通用的隔離單元。

## 課程地圖

### Part 1 — 容器基礎
- [Ch 0 環境安裝](./00-environment-setup.md)
- [Ch 1 Docker 架構全貌](./01-docker-architecture.md)
- [Ch 2 Image 與 Container](./02-image-and-container.md)
- [Ch 3 Dockerfile 入門](./03-dockerfile-basics.md)
- [Ch 4 網路基礎](./04-networking-basics.md)

### Part 2 — 底層原理
- [Ch 5 Linux Namespace](./05-linux-namespace.md)
- [Ch 6 cgroups](./06-cgroups.md)
- [Ch 7 OverlayFS](./07-overlayfs.md)
- [Ch 8 containerd 與 runc](./08-containerd-runc.md)
- [Ch 9 Capabilities 與 seccomp](./09-capabilities-seccomp.md)
- [練習 A：從零手刻最小容器](./practice-a-minimal-container.md)

### Part 3 — Dockerfile 進階
- [Ch 10 Multi-stage Build](./10-multi-stage-build.md)
- [Ch 11 BuildKit 與 Cache](./11-buildkit-cache.md)
- [Ch 12 映像最小化](./12-image-minimization.md)
- [Ch 13 .dockerignore 與 Build Context](./13-dockerignore-build-context.md)

### Part 4 — Docker Compose
- [Ch 14 Compose 基礎](./14-compose-basics.md)
- [Ch 15 環境變數與 Secrets](./15-env-secrets.md)
- [Ch 16 Health Check 與 depends_on](./16-healthcheck-depends.md)
- [Ch 17 Compose Override 與 Profiles](./17-compose-override-profiles.md)
- [練習 B：FastAPI + PostgreSQL + Redis + Nginx](./practice-b-compose-stack.md)

### Part 5 — Registry 與映像管理
- [Ch 18 Registry 自架](./18-private-registry.md)
- [Ch 19 映像掃描](./19-image-scanning.md)
- [Ch 20 映像簽名](./20-image-signing.md)

### Part 6 — 資安 Hardening
- [Ch 21 非 root 與 Read-only](./21-non-root-readonly.md)
- [Ch 22 Capabilities 限制](./22-capabilities-drop.md)
- [Ch 23 Docker Socket 與 Rootless](./23-docker-socket-rootless.md)
- [練習 C：Dockerfile 資安審查](./practice-c-security-audit.md)

### Part 7 — 生產實務
- [Ch 24 日誌管理](./24-logging.md)
- [Ch 25 監控與指標](./25-monitoring.md)
- [Ch 26 Docker Swarm 入門](./26-swarm.md)
- [Ch 27 Docker → Kubernetes 銜接](./27-docker-to-k8s.md)

### Final Project
- [Final Project：完整 CI Pipeline](./final-project-ci-pipeline.md)

## 學習方式建議

1. **底層章節要動手跑**：Ch 5 的 `unshare` 手刻容器、Ch 7 的 OverlayFS 掛載，自己跑過比讀十遍有效。
2. **故意打壞**：Dockerfile 不寫 `.dockerignore` 看 context 多大、不用 multi-stage 看 image 多肥、用 root 跑然後看 trivy 怎麼罵你。
3. **練習 C 先別看解答**：那份有問題的 Dockerfile 你能找出幾個問題，反映你對前面 Part 吸收了多少。

## 參考資料

- [Docker 官方文件](https://docs.docker.com/) — 指令和 Compose 規格的第一手來源
- [OCI Runtime Spec](https://github.com/opencontainers/runtime-spec) — runc 實作依據
- [Linux man pages: namespaces(7), cgroups(7)](https://man7.org/linux/man-pages/) — 底層原理根源
- 《Container Security》— Liz Rice（O'Reilly）— namespace / capability / seccomp 深入
