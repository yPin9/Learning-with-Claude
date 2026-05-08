# Ch 27 — Docker → Kubernetes 銜接

> 目標：建立 Docker Compose / Swarm 到 Kubernetes 的概念對照，用 kompose 把現有 Compose stack 轉換成 K8s manifest，在 minikube 上實際跑起來，並知道下一步該往哪裡學。

## 概念對照表

學 K8s 最快的方式是從已知的 Docker 概念出發：

| 概念 | Docker Compose | Docker Swarm | Kubernetes |
|------|---------------|--------------|------------|
| 定義應用 | `compose.yml` | `stack.yml` | YAML manifest（多個檔案） |
| 部署單位 | container | task | Pod |
| 多副本管理 | `scale:` | `deploy.replicas:` | Deployment |
| 網路入口 | `ports:` | routing mesh | Service（type=NodePort/LoadBalancer） |
| 設定 | `environment:` | `configs:` | ConfigMap |
| 機密 | `.env` / `secrets:` | `secrets:` | Secret |
| 持久化儲存 | `volumes:` | `volumes:` | PersistentVolumeClaim |
| 服務間通訊 | service name（DNS） | service name（DNS） | service name（ClusterIP） |
| Health check | `healthcheck:` | `healthcheck:` | `livenessProbe` / `readinessProbe` |
| 滾動更新 | Compose 沒有 | `update_config:` | Deployment `strategy:` |

## K8s 最小概念集（Docker 用戶需要知道的）

### Pod

Pod 是 K8s 的最小部署單位。一個 Pod 裡可以有多個 container（通常一個 main container + 幾個 sidecar）。

```
Docker Compose:
  service "app" -> 1 個 container

K8s:
  Pod "app-7d8f9" -> 1 個 container（通常）
                    + 可選的 sidecar（log agent、service mesh proxy）
```

你不直接管理 Pod，而是透過 Deployment 管。

### Deployment

管理 Pod 的副本數量和滾動更新：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 3                  # 對應 Compose 的 scale / Swarm 的 replicas
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: myapp
        image: myapp:latest
        ports:
        - containerPort: 8000
        resources:
          requests:
            memory: "64Mi"
            cpu: "250m"
          limits:
            memory: "128Mi"
            cpu: "500m"
```

### Service（K8s 的 Service != Compose 的 service）

K8s 的 Service 是網路入口，提供穩定的 IP / DNS 給一組 Pod：

```
Compose 的 service: 定義一個應用（container spec + 網路 + volumes）
K8s 的 Service:     只是網路入口，不包含任何 container spec
```

```yaml
apiVersion: v1
kind: Service
metadata:
  name: myapp
spec:
  selector:
    app: myapp           # 找 label app=myapp 的所有 Pod
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP        # 只在 cluster 內可達（對應 Compose 的 expose）
  # type: NodePort       # 在每個 node 的某個 port 暴露（測試用）
  # type: LoadBalancer   # 請雲端建一個 LB（AWS ALB、GCP LB 等）
```

### ConfigMap 和 Secret

```yaml
# ConfigMap：非機密設定
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  DATABASE_HOST: postgres
  LOG_LEVEL: info

# Secret：機密（base64 編碼，不是加密！）
apiVersion: v1
kind: Secret
metadata:
  name: app-secrets
type: Opaque
data:
  DATABASE_PASSWORD: c3VwZXJzZWNyZXQ=   # base64("supersecret")
```

注意：K8s Secret 只是 base64，不是真正的加密。生產環境要搭配 Sealed Secrets 或 External Secrets Operator。

## kompose：Compose 轉 K8s Manifest

kompose 是官方工具，把 `compose.yml` 轉換成 K8s manifest 的起點：

```bash
# 安裝（Linux）
curl -L https://github.com/kubernetes/kompose/releases/download/v1.32.0/kompose-linux-amd64 \
  -o /usr/local/bin/kompose
chmod +x /usr/local/bin/kompose

# 安裝（macOS）
brew install kompose

# 轉換
kompose convert -f compose.yml

# 指定輸出目錄
kompose convert -f compose.yml -o k8s/
```

### 轉換範例

以下是 Practice B 的 Compose 片段：

```yaml
# compose.yml（片段）
services:
  api:
    image: myapp:latest
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://user:pass@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    volumes:
      - db_data:/var/lib/postgresql/data
    environment:
      POSTGRES_PASSWORD: mysecret
```

執行 kompose：

```bash
kompose convert -f compose.yml -o k8s/
ls k8s/
# api-deployment.yaml
# api-service.yaml
# db-deployment.yaml
# db-service.yaml
# db-data-persistentvolumeclaim.yaml
```

產生的 `api-deployment.yaml`（簡化）：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 1
  selector:
    matchLabels:
      io.kompose.service: api
  template:
    metadata:
      labels:
        io.kompose.service: api
    spec:
      containers:
      - image: myapp:latest
        name: api
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          value: postgresql://user:pass@db:5432/mydb
```

kompose 是**起點不是終點**。轉出來的 manifest 通常需要手動調整：

- 把 env 裡的機密移到 Secret
- 加 `livenessProbe` / `readinessProbe`
- 設定 resource requests/limits
- 把 `depends_on` 的邏輯改用 init container 或 readinessProbe 處理
- 設定 PVC 的 storageClass

## 本地 K8s 選型

| 工具 | 特色 | 適合場景 |
|------|------|----------|
| minikube | 最老牌，功能最全，支援多種 driver | 學習、測試 K8s 功能 |
| kind | 用 Docker container 模擬 K8s node | CI 環境、多節點測試 |
| k3d | k3s（輕量 K8s）跑在 Docker 裡 | 快速、資源少 |
| Docker Desktop K8s | 一鍵開啟，但版本可能舊 | Mac/Windows 開發 |

學習建議：用 **minikube**，文件最完整。

## 實際範例：把 Compose Stack 跑到 minikube

```bash
# 安裝 minikube（Linux）
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# 啟動 minikube
minikube start --driver=docker --cpus=2 --memory=4096

# 確認
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# minikube   Ready    control-plane   1m    v1.28.x

# 讓 minikube 使用 local Docker image（不需要推到 registry）
eval $(minikube docker-env)

# build image 到 minikube 的 Docker daemon 裡
docker build -t myapp:latest .

# 部署 k8s manifest
kubectl apply -f k8s/

# 查看狀態
kubectl get pods
kubectl get services

# 等所有 pod 都 Running
kubectl wait --for=condition=ready pod -l io.kompose.service=api --timeout=120s

# 存取服務（minikube 的 NodePort）
minikube service api --url
# http://192.168.49.2:31234

# 查 log
kubectl logs deployment/api -f

# 進容器 debug
kubectl exec -it deployment/api -- sh
```

### 常用 kubectl 指令對照

| 目的 | Docker Compose | kubectl |
|------|---------------|---------|
| 查看容器狀態 | `docker compose ps` | `kubectl get pods` |
| 看 log | `docker compose logs -f api` | `kubectl logs -f deployment/api` |
| 進容器 | `docker compose exec api sh` | `kubectl exec -it pod/api-xxx -- sh` |
| 重啟 | `docker compose restart api` | `kubectl rollout restart deployment/api` |
| 縮放 | `docker compose scale api=3` | `kubectl scale deployment/api --replicas=3` |
| 查設定 | `docker compose config` | `kubectl describe deployment/api` |

## 下一步建議

這章只是 K8s 的門口。真正的 K8s 課程需要涵蓋：

```
基礎（這章已涵蓋）
  Pod / Deployment / Service / ConfigMap / Secret

進階工作負載
  StatefulSet（有狀態服務，DB）
  DaemonSet（每個 node 跑一個，例如 log agent）
  CronJob（定時任務）

網路
  Ingress / IngressController（HTTP routing，取代 Nginx 反向代理）
  NetworkPolicy（Pod 間防火牆）
  CoreDNS（cluster 內 DNS）

儲存
  PersistentVolume / PersistentVolumeClaim / StorageClass
  CSI driver

安全
  RBAC（Role-Based Access Control）
  ServiceAccount
  PodSecurityAdmission（取代 PodSecurityPolicy）

Package 管理
  Helm（K8s 的 apt/pip）

可觀測性
  Prometheus Operator
  Loki + Grafana
```

學習路徑：`dev_tools/kubernetes/`（待建）。

## 自我檢核

- [ ] 能把 Compose 的 service / volumes / secrets 對應到 K8s 的 Deployment / PVC / Secret
- [ ] 能說明 K8s 的 Service 和 Compose 的 service 的根本差異
- [ ] 能用 kompose 把 compose.yml 轉換成 K8s manifest
- [ ] 知道 kompose 輸出哪些地方通常需要手動修改
- [ ] 能在 minikube 上 apply manifest 並用 kubectl 驗證狀態
- [ ] 能對照 docker compose 和 kubectl 的常用指令

整個 Docker 課程到這裡結束基礎教學。最後一關是 Final Project：從 Dockerfile 到 CI pipeline，把所有東西串起來。

→ [Final Project：完整 CI Pipeline](./final-project-ci-pipeline.md)
