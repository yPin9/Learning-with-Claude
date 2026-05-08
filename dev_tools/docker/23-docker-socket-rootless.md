# Ch 23 — Docker Socket 與 Rootless

> 目標：理解 Docker socket 掛入容器等於給出 host root 存取權的根本原因，學會評估替代方案，並能設定 Rootless Docker 作為架構層面的防線。

## Docker socket 是什麼

Docker daemon 監聽一個 Unix socket：`/var/run/docker.sock`。

所有 `docker` CLI 指令，背後都是對這個 socket 發 HTTP 請求：

```
docker run alpine
    |
    v
POST /containers/create  (HTTP over Unix socket)
    |
    v
dockerd（root 跑的 daemon）
    |
    v
containerd -> runc -> container
```

能存取這個 socket = 能呼叫所有 Docker API = 能讓 daemon（以 root 執行）做任何事。

## 把 socket 掛進容器等於給出 host root

這是 Docker 環境裡最常見的嚴重錯誤之一：

```bash
# 這樣做的容器，裡面的 process 等於是 host root
docker run -v /var/run/docker.sock:/var/run/docker.sock alpine sh
```

進去之後，裝個 docker CLI：

```bash
apk add docker-cli
docker ps     # 看到 host 上的所有容器
docker images # 看到 host 上的所有 image
```

完整的攻擊範例（取得 host root shell）：

```bash
# 在掛了 socket 的容器裡執行
docker run -v /:/host --privileged alpine chroot /host /bin/sh
# 現在你在 host 的 root shell 裡
# /host 是 host 的整個 filesystem
```

三行指令，從「沒有特權的 Alpine 容器」變成「host root shell」。

### CI/CD 環境的常見錯誤

Jenkins、GitLab Runner 常見的做法：

```yaml
# GitLab CI 錯誤範例
docker:
  image: docker:latest
  services:
    - docker:dind
  variables:
    DOCKER_HOST: tcp://docker:2375  # 沒有 TLS！
  script:
    - docker build -t myapp .
```

或者：

```groovy
// Jenkinsfile 錯誤範例
agent {
  docker {
    image 'docker:latest'
    args '-v /var/run/docker.sock:/var/run/docker.sock'  // 直接掛 socket
  }
}
```

任何能在這個 CI job 裡執行程式碼的人（包括 supply chain attack 進來的 npm package），都能拿到 host root。

## TCP socket 的危險：沒有 TLS

Docker daemon 也可以監聽 TCP：

```bash
# /etc/docker/daemon.json
{
  "hosts": ["tcp://0.0.0.0:2375"]
}
```

`2375` 是無 TLS 的 Docker API port。任何能連到這個 port 的人，直接拿到 Docker API：

```bash
# 從遠端機器攻擊
curl http://target:2375/v1.41/containers/json
# 列出所有容器

docker -H tcp://target:2375 run -v /:/host alpine chroot /host sh
# 拿到 host root
```

CVE-2019-5736（runc container escape）之所以影響這麼大，部分原因是很多生產環境開著裸 TCP socket。

掃描你的 host：

```bash
# 看 Docker daemon 在監聽什麼
ss -tlnp | grep dockerd
netstat -tlnp | grep 2375
```

如果一定要 TCP，要加 TLS：

```json
{
  "hosts": ["tcp://0.0.0.0:2376"],
  "tls": true,
  "tlscacert": "/etc/docker/ca.pem",
  "tlscert": "/etc/docker/server-cert.pem",
  "tlskey": "/etc/docker/server-key.pem",
  "tlsverify": true
}
```

`2376` 是有 TLS 的 Docker API 慣用 port。

## 替代方案

### Docker-in-Docker（DinD）

在容器裡跑一個完整的 Docker daemon，不共享 host 的 socket：

```yaml
# GitLab CI 正確的 DinD 設定（有 TLS）
variables:
  DOCKER_HOST: tcp://docker:2376
  DOCKER_TLS_CERTDIR: /certs
  DOCKER_TLS_VERIFY: 1
  DOCKER_CERT_PATH: $DOCKER_TLS_CERTDIR/client

services:
  - name: docker:dind
    alias: docker
    variables:
      DOCKER_TLS_CERTDIR: /certs
```

DinD 容器需要 `--privileged`，但它的 Docker daemon 和 host 的 Docker daemon 是分開的，爆了只影響自己的隔離環境。缺點：較重、image layer cache 不共享。

### Kaniko

不需要 Docker daemon，直接在 container 裡 build image，讀 Dockerfile 產出 OCI image 推到 registry：

```yaml
# Kubernetes Job
apiVersion: batch/v1
kind: Job
spec:
  template:
    spec:
      containers:
      - name: kaniko
        image: gcr.io/kaniko-project/executor:latest
        args:
          - "--context=git://github.com/myorg/myapp"
          - "--destination=registry.example.com/myapp:latest"
        volumeMounts:
          - name: docker-config
            mountPath: /kaniko/.docker
```

不需要 privileged，不需要 socket，Kubernetes 環境的主流選擇。

### Buildah

Red Hat 出品，rootless image build：

```bash
# 不需要 Docker daemon，以當前用戶身份 build
buildah bud -t myapp:latest .

# 推到 registry
buildah push myapp:latest docker://registry.example.com/myapp:latest
```

Buildah 可以完全在非 root 環境下跑，沒有 daemon，適合在 CI 或用戶目錄下 build。

## Rootless Docker

整個 Docker daemon 跑在非 root 用戶的 user namespace 裡。容器裡的 UID 0 對應的是 host 的非特權 UID，不是真正的 root。

### User Namespace UID Mapping

```
容器裡的 UID 0  ──映射──>  host 的 UID 100000
容器裡的 UID 1  ──映射──>  host 的 UID 100001
...
容器裡的 UID 65535 -> host 的 UID 165535
```

就算 container escape，拿到的是 host 的 UID 100000，不是真正的 root。

### 安裝 Rootless Docker

```bash
# 前置條件
sudo apt install uidmap

# 安裝
dockerd-rootless-setuptool.sh install

# 設定環境變數（加到 ~/.bashrc）
export PATH=/usr/bin:$PATH
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock

# 啟動
systemctl --user start docker
systemctl --user enable docker
```

驗證：

```bash
docker info | grep -i rootless
# Rootless: true
```

### Rootless 的限制

| 功能 | 狀態 | 原因 |
|------|------|------|
| `--network host` | 受限 | 非 root 無法操作 host network stack |
| overlay network（Swarm） | 需額外設定 | 需要 `newuidmap`/`newgidmap` |
| bind port < 1024 | 需要 `sysctl` 調整 | 非 root 無法 bind privileged port |
| `--privileged` | 仍然危險 | 但爆了也只有 subUID 範圍的權限 |
| volume mount 效能 | 較慢 | UID mapping 有 overhead |

對絕大多數 web app，這些限制都不是問題。

## Docker socket 安全使用的場景

有時候確實需要在容器裡控制 Docker（例如 CI 系統、Portainer）。如果非用 socket 不可：

```bash
# 1. 只 bind mount，不要 privileged
docker run -v /var/run/docker.sock:/var/run/docker.sock portainer/portainer

# 2. 用 socat proxy，限制可用的 API endpoint
# 例如只允許 GET /containers/json，不允許 POST /containers/create
```

或者用 Rootless Docker 的 socket（UID 只有 subUID range 的權限）：

```bash
# rootless docker socket 位置
/run/user/$(id -u)/docker.sock

# 掛進容器時，爆了也只有當前用戶的 subUID 範圍
docker run -v /run/user/1000/docker.sock:/var/run/docker.sock myapp
```

## 架構決策總結

```
CI/CD 場景：
  不需要 Docker API  -> 用 Kaniko 或 Buildah
  需要 Docker API    -> DinD（--privileged 隔離環境）
  絕對不要           -> 掛 host /var/run/docker.sock

生產環境：
  單機              -> Rootless Docker
  多機              -> Rootless Docker + TLS TCP（只對內網開）
  絕對不要           -> 裸 TCP 2375 暴露在公網
```

## 自我檢核

- [ ] 能解釋為什麼掛 `/var/run/docker.sock` 等於給出 host root
- [ ] 能說出攻擊者從掛了 socket 的容器取得 host shell 的完整步驟
- [ ] 知道 CVE-2019-5736 與裸 TCP socket 的關係
- [ ] 能比較 DinD / Kaniko / Buildah 三種替代方案的適用場景
- [ ] 能安裝 Rootless Docker 並說明 UID mapping 的工作原理
- [ ] 知道 Rootless Docker 的主要限制

Part 6 的三章資安 hardening 到這裡結束。接下來的練習 C 要你審查一份有問題的 Dockerfile——做之前別翻答案。

→ [練習 C：Dockerfile 資安審查](./practice-c-security-audit.md)
