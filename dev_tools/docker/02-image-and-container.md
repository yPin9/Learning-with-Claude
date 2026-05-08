# Ch 2 — Image 與 Container

> 目標：理解 image layer 結構，熟練 docker 日常操作指令，知道 Image ID / Tag / Digest 的差異，以及 `docker inspect` 哪些欄位實際有用。

---

## Image vs Container：class vs instance

Image 是唯讀的模板，Container 是從 image 跑起來的執行實例。

```
Image (read-only)
+----------------------------------+
|  layer N: COPY app /app          |
|  layer N-1: RUN pip install ...  |
|  layer 1: FROM python:3.11-slim  |
+----------------------------------+
         |
         | docker run
         v
Container
+----------------------------------+
|  Writable Layer (container layer)|  <- 所有寫入都在這裡
+----------------------------------+
|  layer N: COPY app /app          |  <- read-only
|  layer N-1: RUN pip install ...  |  <- read-only
|  layer 1: FROM python:3.11-slim  |  <- read-only
+----------------------------------+
```

同一個 image 可以同時跑 100 個容器，底層的 read-only layer 共用，每個容器只多一個薄薄的 writable layer。容器停掉並 `docker rm` 後，writable layer 消失，image 不受影響。

---

## 常用指令全覽

### 拉 image

```bash
docker pull nginx:1.25-alpine
```

```
1.25-alpine: Pulling from library/nginx
96526aa774ef: Pull complete
...
Digest: sha256:a0902f5901d0a70e5...
Status: Downloaded newer image for nginx:1.25-alpine
docker.io/library/nginx:1.25-alpine
```

`alpine` 版本基於 Alpine Linux，image 只有幾 MB，適合生產。`latest` tag 通常是 Debian 版，大很多。

### 查看本機 image

```bash
docker images
```

```
REPOSITORY   TAG            IMAGE ID       CREATED        SIZE
nginx        1.25-alpine    e0c9858e10ed   2 weeks ago    41.1MB
python       3.11-slim      c02f6e1b2a50   3 weeks ago    125MB
hello-world  latest         d2c94e258dcb   13 months ago  13.3kB
```

`IMAGE ID` 是 sha256 前 12 位，`SIZE` 是解壓縮後的大小（不是 tarball）。

### 跑容器

```bash
docker run -d -p 8080:80 --name mynginx nginx:1.25-alpine
```

| flag | 意義 |
|------|------|
| `-d` | detached，在背景跑 |
| `-p 8080:80` | host port 8080 → container port 80 |
| `--name mynginx` | 給容器取名，不取就隨機 |

```bash
# 驗證：打開 nginx 歡迎頁
curl http://localhost:8080
# 應該看到 <html>...<h1>Welcome to nginx!</h1>...
```

### 查看容器狀態

```bash
docker ps          # 只看跑中的容器
docker ps -a       # 所有容器，包含已停止的
```

```
CONTAINER ID   IMAGE               COMMAND                  CREATED         STATUS         PORTS                  NAMES
a3f1d8e92c10   nginx:1.25-alpine   "/docker-entrypoint.…"   2 minutes ago   Up 2 minutes   0.0.0.0:8080->80/tcp   mynginx
```

`STATUS` 欄位：`Up 2 minutes`（跑中）、`Exited (0) 1 hour ago`（正常結束）、`Exited (1) ...`（異常結束）。

### 進入容器

```bash
docker exec -it mynginx sh
```

`-it` = `-i`（保持 stdin）+ `-t`（分配 pseudo-TTY）。`sh` 是 alpine 上的 shell（沒有 bash）。  
進去之後是容器的 filesystem，看得到 `/etc/nginx/nginx.conf`，改完離開不影響 image。

### 查看 log

```bash
docker logs mynginx          # 一次性輸出
docker logs -f mynginx       # follow，持續輸出（像 tail -f）
docker logs --tail 50 mynginx  # 最後 50 行
```

```
/docker-entrypoint.sh: /docker-entrypoint.d/ is not empty, will attempt to perform configuration
...
2024/01/15 10:23:45 [notice] 1#1: start worker processes
```

### 停止與移除

```bash
docker stop mynginx       # 發 SIGTERM，等 10 秒後還沒停才 SIGKILL
docker kill mynginx       # 直接 SIGKILL
docker rm mynginx         # 移除容器（要先停止）
docker rm -f mynginx      # 強制：先 kill 再 rm

docker rmi nginx:1.25-alpine   # 移除 image（要先 rm 所有用到它的容器）
```

`docker rm` 和 `docker rmi` 是兩件不同的事，新手常搞混：`rm` 刪容器，`rmi` 刪 image。

---

## Image ID vs Tag vs Digest

這三個東西都可以指涉一個 image，但語義不同：

| 識別符 | 範例 | 特性 |
|--------|------|------|
| Tag | `nginx:1.25-alpine` | 可變，同一個 tag 可以重新 push 指向新 image |
| Image ID | `e0c9858e10ed` | 本機 sha256 truncation，本機唯一 |
| Digest | `sha256:a0902f...` | registry 上的內容雜湊，全域唯一且不可變 |

同一個 Image ID 可以同時有多個 tag：

```bash
docker tag nginx:1.25-alpine mynginx:prod
docker images
```

```
REPOSITORY   TAG            IMAGE ID
nginx        1.25-alpine    e0c9858e10ed
mynginx      prod           e0c9858e10ed   <- 同一個 ID，兩個 tag
```

生產環境 pull image 應該用 digest 固定版本：

```bash
docker pull nginx@sha256:a0902f5901d0a70e5...
```

這樣才能保證拿到的 image 內容永遠一樣，tag 可能被 overwrite。

---

## Dangling Image

```bash
docker images -a
```

有時會看到：

```
REPOSITORY   TAG       IMAGE ID       CREATED        SIZE
<none>       <none>    5f3d6d1b9a2c   2 hours ago    125MB
```

`<none>:<none>` 稱為 **dangling image（懸空映像）**，通常是 build 過程的中間層，或是同一個 tag 被重新 build 後，舊的 image 失去 tag。

```bash
docker image prune        # 移除所有 dangling image
docker image prune -a     # 移除所有沒有容器在用的 image（謹慎用）
```

---

## docker inspect：深入容器細節

```bash
docker inspect mynginx
```

輸出是一個 JSON array，關鍵欄位：

```json
[{
  "Id": "a3f1d8e92c10...",
  "State": {
    "Status": "running",
    "Pid": 12345,
    "ExitCode": 0
  },
  "NetworkSettings": {
    "IPAddress": "172.17.0.2",
    "Ports": {
      "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
    }
  },
  "Mounts": [],
  "HostConfig": {
    "Memory": 0,
    "NanoCpus": 0,
    "Binds": null
  },
  "Config": {
    "Image": "nginx:1.25-alpine",
    "Env": ["PATH=/usr/local/sbin:..."],
    "Cmd": ["nginx", "-g", "daemon off;"]
  }
}]
```

`State.Pid` 是 host 上的 PID，之後 Ch 5 會用它去 `/proc/<pid>/ns/` 看 namespace。

用 `--format` 抓特定欄位：

```bash
# 只看 IP
docker inspect --format '{{.NetworkSettings.IPAddress}}' mynginx
# 172.17.0.2

# 只看 host PID
docker inspect --format '{{.State.Pid}}' mynginx
# 12345
```

---

## 清理指令彙整

```bash
docker system df           # 看 disk 用量（image / container / volume / build cache）
docker system prune        # 移除所有停止的容器 + dangling image + unused network
docker system prune -a     # 再加上所有沒在用的 image（謹慎）
```

---

## 自我檢核

- [ ] 能說明 image layer 結構，writable layer 在哪裡
- [ ] 跑起 `nginx:1.25-alpine`，用 `curl` 驗證 port mapping
- [ ] 知道 `docker ps` 和 `docker ps -a` 的差異
- [ ] 能解釋 Image ID / Tag / Digest 三者差異
- [ ] 用 `docker inspect` 找出容器的 IP 和 host PID
- [ ] 清楚 `docker rm` vs `docker rmi` 的區別

下一章開始寫 Dockerfile，把「怎麼建自己的 image」講清楚。

→ [Ch 3 Dockerfile 入門](./03-dockerfile-basics.md)
