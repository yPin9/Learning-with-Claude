# Ch 26 — Docker Swarm 入門

> 目標：理解 Swarm 的核心概念，能初始化單節點或多節點 cluster，部署 service、做 rolling update，並用 Stack 把 Compose file 部署到 Swarm 上。

## Swarm 是什麼

Docker Swarm 是 Docker 內建的 container orchestration（容器編排）系統。它讓你：

- 把多台機器組成一個 cluster，統一管理
- 部署 service（不是單一容器，是「幾個副本的某個容器」）
- 自動重啟掛掉的容器
- rolling update：不停機更新到新版本
- 內建 load balancer：把請求分散到多個副本

對比 Kubernetes：Swarm 輕量太多，適合小團隊或中型部署。K8s 的概念複雜度大概是 Swarm 的 5 倍。

## 核心概念

| 概念 | 說明 | 對應 Compose 概念 |
|------|------|-------------------|
| Manager Node | 管理 cluster 狀態、排程 task，接受 API 請求 | 無對應（Compose 是單機） |
| Worker Node | 執行 task（container），不接受管理 API | 無對應 |
| Service | 部署單位：「跑 3 個 nginx:alpine 副本」 | `services:` 裡的一個 service |
| Task | Service 的一個 container 實例 | 單一 container |
| Stack | 一組相關 service 的集合，用 Compose file 定義 | 整個 `compose.yml` |
| Overlay Network | 跨 node 的虛擬網路，讓不同機器上的容器互通 | bridge network |
| Secret | 加密儲存的機密，只在需要的 service 上解密 | `secrets:` 區塊 |

## 初始化 Swarm

### 單節點（開發 / 測試用）

```bash
# 初始化，自己既是 manager 也是 worker
docker swarm init

# 如果機器有多個 IP，需要指定 advertise addr
docker swarm init --advertise-addr 192.168.1.10

# 確認狀態
docker node ls
# ID                            HOSTNAME   STATUS    AVAILABILITY   MANAGER STATUS
# abc123 *                      myhost     Ready     Active         Leader
```

### 多節點 Cluster

在 manager 上初始化後，輸出一個 join token：

```bash
# Manager 機器
docker swarm init --advertise-addr 10.0.0.1
# 輸出：
# To add a worker to this swarm, run the following command:
#   docker swarm join --token SWMTKN-1-xxxxx 10.0.0.1:2377

# 查 token（如果忘了）
docker swarm join-token worker   # worker token
docker swarm join-token manager  # manager token（加更多 manager）
```

在 worker 機器上：

```bash
docker swarm join --token SWMTKN-1-xxxxx 10.0.0.1:2377
# This node joined a swarm as a worker.
```

確認 cluster 狀態：

```bash
# 在 manager 上執行
docker node ls
# ID          HOSTNAME    STATUS    AVAILABILITY   MANAGER STATUS
# abc123 *    manager-1   Ready     Active         Leader
# def456      worker-1    Ready     Active
# ghi789      worker-2    Ready     Active
```

## Service：Swarm 的部署單位

Service 和 Compose 的 service 概念相近，但管理方式不同：

```bash
# 建立 service：跑 3 個 nginx:alpine 副本
docker service create \
  --name web \
  --replicas 3 \
  --publish published=80,target=80 \
  nginx:alpine

# 查看 service 列表
docker service ls
# ID             NAME   MODE         REPLICAS   IMAGE          PORTS
# abc123         web    replicated   3/3        nginx:alpine   *:80->80/tcp

# 查看每個 task（container）跑在哪個 node
docker service ps web
# ID         NAME      IMAGE          NODE        DESIRED STATE   CURRENT STATE
# xxx        web.1     nginx:alpine   manager-1   Running         Running 2 min
# yyy        web.2     nginx:alpine   worker-1    Running         Running 2 min
# zzz        web.3     nginx:alpine   worker-2    Running         Running 2 min

# 動態縮放
docker service scale web=5
docker service scale web=1

# 查看 service 詳細設定
docker service inspect web --pretty
```

### 內建 Load Balancer

Swarm 內建一個 routing mesh（路由網格）：

```
外部請求 :80 -> 任意 node -> Swarm routing mesh -> 輪流分發到 3 個副本
```

不管請求打到 cluster 裡的哪個 node（就算那個 node 上沒有跑 web service 的副本），routing mesh 都會正確轉發。

## Rolling Update：不停機更新

```bash
# 模擬：把 nginx 從 alpine 更新到 1.25-alpine
# --update-parallelism 1：每次更新 1 個副本
# --update-delay 10s：每個副本更新後等 10 秒再更新下一個
docker service update \
  --image nginx:1.25-alpine \
  --update-parallelism 1 \
  --update-delay 10s \
  web

# 觀察更新過程
watch docker service ps web
```

更新過程：

```
t=0s:  web.1 停止舊容器，啟動新容器
t=10s: web.1 健康後，web.2 開始更新
t=20s: web.2 健康後，web.3 開始更新
t=30s: 全部更新完成
```

如果新版本有問題，rollback：

```bash
docker service rollback web
```

預設設定（可在 service create 時指定）：

```bash
docker service create \
  --name web \
  --replicas 3 \
  --update-parallelism 1 \
  --update-delay 10s \
  --update-failure-action rollback \   # 更新失敗自動 rollback
  --rollback-parallelism 2 \
  nginx:alpine
```

## Stack：Compose 在 Swarm 上跑

Stack 讓你用 Compose file 格式定義整個應用，但部署到 Swarm cluster 上：

```yaml
# stack.yml
version: "3.8"

services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
    deploy:
      replicas: 3
      update_config:
        parallelism: 1
        delay: 10s
        failure_action: rollback
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
      resources:
        limits:
          cpus: "0.5"
          memory: 128M
        reservations:
          cpus: "0.25"
          memory: 64M
    networks:
      - frontend

  api:
    image: myapp:latest
    deploy:
      replicas: 2
    networks:
      - frontend
      - backend

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: mydb
      POSTGRES_USER: user
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.storage == ssd  # 只跑在有 ssd 標籤的 node 上
    volumes:
      - db_data:/var/lib/postgresql/data
    networks:
      - backend

secrets:
  db_password:
    external: true  # 已用 docker secret create 建立

volumes:
  db_data:

networks:
  frontend:
    driver: overlay
  backend:
    driver: overlay
    internal: true  # 不接外網
```

部署：

```bash
# 先建立 secret
echo "super_secret_password" | docker secret create db_password -

# 部署 stack
docker stack deploy -c stack.yml myapp

# 查看 stack
docker stack ls
docker stack services myapp
docker stack ps myapp

# 更新（修改 stack.yml 後重新 deploy，Swarm 自動計算 diff）
docker stack deploy -c stack.yml myapp

# 刪除 stack
docker stack rm myapp
```

## Swarm Secrets

Swarm secrets 比 Compose secrets 更安全：

```bash
# 建立 secret（從 stdin）
echo "mysecretpassword" | docker secret create db_password -

# 從檔案建立
docker secret create tls_cert ./server.crt

# 列出 secrets
docker secret ls

# 查看 secret metadata（看不到內容）
docker secret inspect db_password
```

secret 在容器裡掛載在 `/run/secrets/<name>`：

```bash
# 容器裡
cat /run/secrets/db_password
# mysecretpassword
```

傳輸過程加密（TLS），只在需要的 node 上解密，不存在 container 的環境變數裡（防 `docker inspect` 洩漏）。

## Swarm vs Kubernetes 選型

| 考量 | Swarm | Kubernetes |
|------|-------|------------|
| 學習曲線 | 低，2-3 天上手 | 高，2-3 週上手 |
| 安裝複雜度 | 一行指令 | kubeadm / k3s / managed |
| 適合規模 | 幾台到幾十台 | 幾十台到幾千台 |
| 生態系 | 較小 | 龐大（Helm、Operator 等） |
| 自動縮放 | 需手動 or 外部工具 | HPA、KEDA 原生支援 |
| 儲存管理 | 基本（volume） | PVC、StorageClass 完整 |
| 企業採用 | 下降中 | 主流 |

結論：**小團隊、簡單服務用 Swarm，省下大量維運時間；需要複雜排程、大量服務、或已有 K8s 平台，用 K8s**。

## 自我檢核

- [ ] 能初始化 single-node Swarm 並解釋 Manager / Worker 的角色
- [ ] 能用 `docker service create --replicas 3` 部署服務並驗證副本數
- [ ] 能執行 rolling update 並觀察 `docker service ps` 的更新過程
- [ ] 知道 `docker service rollback` 的作用
- [ ] 能把一個 Compose file 加上 `deploy:` 區塊後用 `docker stack deploy` 部署
- [ ] 能建立 Swarm secret 並在 stack.yml 裡引用
- [ ] 能說明 Swarm 和 K8s 各自的適用場景

最後一章：從 Docker Compose 到 Kubernetes 的概念對照與遷移路徑。

→ [Ch 27 Docker → Kubernetes 銜接](./27-docker-to-k8s.md)
