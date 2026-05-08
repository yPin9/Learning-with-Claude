# Ch 0 — 環境安裝

> 目標：在你的機器上裝好可用的 Docker 環境，理解 `docker run hello-world` 的每一行輸出到底發生了什麼，並知道常見的坑在哪裡。

---

## Linux 安裝（Ubuntu / Debian）

不要用 `apt install docker.io`——那個是老舊的社群包，版本落後且結構不同。直接裝 Docker 官方的 **Docker Engine**。

```bash
# 1. 移除舊版殘留
sudo apt remove docker docker-engine docker.io containerd runc

# 2. 加官方 GPG key 與 repo
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3. 安裝
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

裝完之後確認 daemon 有在跑：

```bash
sudo systemctl status docker
# 應該看到 active (running)
```

### 加入 docker group（重要）

預設只有 root 才能操作 Docker socket。每次都 `sudo docker` 很煩，把自己加進 `docker` group：

```bash
sudo usermod -aG docker $USER
# 登出再登入，或直接 newgrp docker
newgrp docker
```

**警告**：加入 docker group 等同於給了這個用戶隱性的 root 能力，因為可以用容器掛載 host 根目錄。CTF / 滲透測試常見的提權路徑之一。知道自己在做什麼再加。

---

## Windows 安裝（Docker Desktop + WSL2）

Windows 上最直接的方式是 Docker Desktop，後端用 WSL2（Windows Subsystem for Linux 2）而不是舊的 Hyper-V VM。

**前置條件：**

1. Windows 10 21H2 或 Windows 11
2. 開啟 WSL2：

```powershell
# 以系統管理員身份執行 PowerShell
wsl --install
# 或只啟用功能
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

3. 從 [https://www.docker.com/products/docker-desktop/](https://www.docker.com/products/docker-desktop/) 下載安裝檔
4. 安裝時勾選 **Use WSL 2 instead of Hyper-V**

Docker Desktop 裝完之後，在 WSL2 的 Ubuntu 終端裡可以直接跑 `docker`——Docker Desktop 會把 socket 橋接進 WSL2。

---

## 驗證安裝

```bash
docker version
```

預期輸出（節錄重要欄位）：

```
Client: Docker Engine - Community
 Version:           26.x.x
 API version:       1.45
 OS/Arch:           linux/amd64

Server: Docker Engine - Community
 Engine:
  Version:          26.x.x
  API version:      1.45 (minimum version 1.24)
  OS/Arch:          linux/amd64
  Experimental:     false
 containerd:
  Version:          1.7.x
 runc:
  Version:          1.1.x
```

如果只看到 Client 沒看到 Server，daemon 沒跑起來。用 `sudo systemctl start docker` 啟動。

```bash
docker info
```

`docker info` 的 Server 欄位會顯示 storage driver、cgroup driver、kernel 版本等，之後分析問題時很有用。現在先確認能看到 `Server:` 區塊不噴錯即可。

---

## docker run hello-world 解剖

```bash
docker run hello-world
```

```
Unable to find image 'hello-world:latest' locally    # (1)
latest: Pulling from library/hello-world             # (2)
c1ec31eb5944: Pull complete                          # (3)
Digest: sha256:1408...                               # (4)
Status: Downloaded newer image for hello-world:latest # (5)

Hello from Docker!                                   # (6)
This message shows that your installation appears to be working correctly.
...
```

| 行 | 發生了什麼 |
|----|-----------|
| (1) | 本機沒有這個 image，觸發 pull |
| (2) | 從 Docker Hub 的 `library/hello-world` repo 拉 |
| (3) | 下載 layer（這個 image 只有一層） |
| (4) | 內容定址的 sha256 digest，確保 image 沒被竄改 |
| (5) | pull 完成，image 存到本機 `/var/lib/docker/` |
| (6) | 容器啟動，印出訊息，程式結束，容器停止 |

完整的動作序列：

```
docker run hello-world
       |
       v
Docker Client ----REST API----> Docker Daemon (dockerd)
                                       |
                            image 本機有嗎？ --No--> pull from registry
                                       |
                              建立 container (containerd)
                                       |
                              啟動 container (runc)
                                       |
                              /hello 程式跑完，容器 exit 0
```

---

## Rootless Docker

一般 Docker daemon 是以 root 跑的，這有安全風險。**Rootless Docker** 讓整個 daemon 在非特權用戶的 namespace 裡跑，daemon 本身不是 root。

```bash
# 安裝 rootless 所需工具
sudo apt install -y uidmap dbus-user-session

# 以一般用戶身份跑安裝腳本
dockerd-rootless-setuptool.sh install

# 設定環境變數
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock
```

**什麼時候需要 rootless：**
- 多租戶系統（shared server）
- CI 環境不想給 root
- 安全要求高的生產部署

代價是部分功能受限（如 `--privileged`、某些 network mode）。Ch 23 會完整介紹。

---

## 常見問題

**`permission denied while trying to connect to the Docker daemon socket`**

```bash
Got permission denied while trying to connect to the Docker daemon socket at
unix:///var/run/docker.sock
```

原因：當前用戶不在 `docker` group 裡。
解法：`sudo usermod -aG docker $USER` 然後重新登入（或 `newgrp docker`）。

**WSL2 裡跑 docker 但 Docker Desktop 沒開**

Docker Desktop 的後端服務沒啟動，WSL2 找不到 socket。啟動 Docker Desktop 或設定 `DOCKER_HOST` 指向正確的 socket。

**`Cannot connect to the Docker daemon. Is the docker daemon running?`**

```bash
sudo systemctl start docker
sudo systemctl enable docker  # 設開機自啟
```

---

## 自我檢核

- [ ] `docker version` 能看到 Client 和 Server 兩個區塊
- [ ] `docker info` 不噴錯，能看到 `Server:` 區塊
- [ ] `docker run hello-world` 跑成功，能解釋每一行輸出
- [ ] 了解 docker group 的安全含義
- [ ] 知道 rootless Docker 是什麼、什麼情境需要

下一章看 Docker 整體架構，從 Client 到 Daemon 到 Registry，還有 containerd / runc 這條層次鏈。

→ [Ch 1 Docker 架構全貌](./01-docker-architecture.md)
