# Ch 22 — 核心物件：Pod / Deployment / Service / Namespace

> **目標**：建立 Pod、Deployment、Service、Namespace 的完整心智模型，理解各物件的底層機制與安全意涵，並能獨立寫出可 apply 的 YAML、診斷常見失敗。
>
> **環境**：kubectl v1.29+、minikube v1.31+ 或 kind v0.22+（範例均在 single-node cluster 驗證過）

---

## 為什麼需要這些物件

Ch 21 我們看了 K8s 的骨架——api-server 是唯一入口、etcd 是狀態儲存、controller manager 負責讓現實趨近宣告。但那只是機器；**這章談零件**：什麼是你真正要部署的東西（Pod）、誰來確保它一直活著（Deployment）、外部怎麼找到它（Service）、你怎麼把關注點切開（Namespace）。這四類物件是 90% K8s 操作的基礎，也是 Part 5 攻擊章的前置知識——Pod spec 裡的幾個危險欄位，等等就會看到。

---

## 先建直覺

把 K8s cluster 想成一個大型多租戶資料中心：

```
┌──────────────────────────────────────────────────────────────────┐
│                         K8s Cluster                             │
│                                                                  │
│  Namespace: production          Namespace: staging               │
│  ┌─────────────────────┐        ┌────────────────────┐          │
│  │  Deployment         │        │  Deployment        │          │
│  │  ┌────────────────┐ │        │  ┌──────────────┐  │          │
│  │  │  ReplicaSet    │ │        │  │  ReplicaSet  │  │          │
│  │  │  ┌───┐ ┌───┐   │ │        │  │  ┌───┐       │  │          │
│  │  │  │Pod│ │Pod│   │ │        │  │  │Pod│       │  │          │
│  │  │  └───┘ └───┘   │ │        │  │  └───┘       │  │          │
│  │  └────────────────┘ │        │  └──────────────┘  │          │
│  │                     │        │                    │          │
│  │  Service (ClusterIP) │        │  Service (NodePort) │          │
│  │  10.96.88.42:80     │        │  :31234            │          │
│  └─────────────────────┘        └────────────────────┘          │
│                                                                  │
│  注意：Namespace 之間沒有網路隔離，只是邏輯分組！                     │
└──────────────────────────────────────────────────────────────────┘
```

Pod 是容器的外殼，Deployment 是 Pod 的生命週期管理者，Service 是穩定的入口，Namespace 是命名空間（不是安全邊界）。

---

## 底層機制

### Pod：最小調度單位

Pod（豆莢）是 K8s 排程的最小單位。注意：**排程單位不是容器**。一個 Pod 可以包含多個容器，它們共享：

- **network namespace**：共用同一個 IP，所有容器用 `localhost` 互相通訊，不需要走 overlay network
- **volume**：宣告在 Pod 層級，各容器可 mount 同一個 volume 共享資料
- **IPC namespace**：可以用 shared memory（`/dev/shm`）互相通訊

這個設計是「sidecar pattern」的根基——主容器 + logging agent、proxy (Envoy)、secrets injector 放在同一個 Pod，讓它們像同一個 process group 一樣運作。

**Pod 是暫時性的（ephemeral）**：Pod 被砍就是真的死了，IP 歸還，volume（EmptyDir）消失。你不應該直接操作 Pod 的生命週期——這是 Deployment 的工作。直接 `kubectl run` 一個 Pod 只有在快速除錯時才合理。

#### Pod spec 結構

一個最小 Pod 的 YAML：

```yaml
apiVersion: v1           # K8s core API 群組，v1 是 stable
kind: Pod                # 物件類型
metadata:
  name: demo-pod         # 在 Namespace 內唯一的識別名
  labels:
    app: demo            # 任意 key-value；Service selector 靠這來找 Pod
spec:
  containers:
  - name: nginx          # 容器名，在 Pod 內唯一
    image: nginx:1.25    # 固定 tag，不要用 latest（每次拉的不一定一樣）
    ports:
    - containerPort: 80  # 只是宣告文件用途，不會真的開防火牆規則
```

`containerPort: 80` 只是 metadata，告訴人類「這個容器預計監聽 80」，K8s 不會因為這個宣告去開任何規則。

#### 安全視角：Pod spec 裡的危險欄位

這裡先點出來，Part 5 會把這些當攻擊入口：

```yaml
spec:
  hostPID: true        # 共享 host 的 PID namespace → 可以看到/kill host 上的 process
  hostNetwork: true    # 共享 host 的網路 → 可以監聽 host 所有介面、嗅封包
  containers:
  - name: evil
    image: ubuntu
    securityContext:
      privileged: true # 等同擁有 host 的所有 Linux capabilities → 容器逃逸最常見入口
    volumeMounts:
    - name: host-root
      mountPath: /host
  volumes:
  - name: host-root
    hostPath:
      path: /          # 掛 host 根目錄 → 可讀寫 /etc/shadow、/var/lib/kubelet 等敏感路徑
```

如果你在一個 cluster 裡看到一個 Pod 帶這些欄位，它要嘛是合法的 node agent（DaemonSet 類型），要嘛是已經被植入了後門。判斷方法：`kubectl get pod <name> -o yaml` 仔細看 spec。

---

### ReplicaSet 與 Deployment

**ReplicaSet（RS）** 的職責：確保 N 個符合 selector 的 Pod 一直在跑。如果一個 Pod 死了，RS controller（在 controller manager 裡）會立刻建一個新的。

你幾乎不會直接建 ReplicaSet。原因：RS 只管數量，不知道如何升級。**Deployment** 包著 RS，加了滾動升級語意：

- 你改了 `spec.template.spec.containers[0].image`，Deployment 會建一個新 RS，逐步把新 RS 的 replica 拉到滿、舊 RS 的 replica 縮到 0
- 整個過程可觀察（`kubectl rollout status`），可回滾（`kubectl rollout undo`）

Deployment YAML 結構：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-deployment
  namespace: default
spec:
  replicas: 3                    # 要幾個 Pod 副本
  selector:
    matchLabels:
      app: demo                  # 這個 selector 必須和 template.metadata.labels 一致
  template:                      # 以下是 Pod template，等同一個完整 Pod spec
    metadata:
      labels:
        app: demo                # Deployment selector 和 Service selector 都靠這
    spec:
      containers:
      - name: nginx
        image: nginx:1.25
        ports:
        - containerPort: 80
        resources:
          requests:
            memory: "64Mi"       # 排程器保證這個 Pod 至少拿到 64 MiB 記憶體
            cpu: "250m"          # 250 milli-core = 0.25 個 CPU core
          limits:
            memory: "128Mi"      # 超過這個值，容器被 OOMKilled
            cpu: "500m"
```

`spec.selector.matchLabels` 是靜態的——建立後不能改，要改就要刪掉整個 Deployment 重建。這個限制讓 RS 的 ownership 是清楚的，不會出現一個 Pod 同時被兩個 Deployment 認領的情況。

滾動升級示範（**本段未實測，為理論預期行為**）：

```bash
# 把映像從 1.25 更新到 1.26
kubectl set image deployment/demo-deployment nginx=nginx:1.26

# 觀察升級進度
kubectl rollout status deployment/demo-deployment
# Waiting for deployment "demo-deployment" rollout to finish: 1 out of 3 new replicas have been updated...

# 如果升級出問題，回滾到上一個版本
kubectl rollout undo deployment/demo-deployment

# 看升級歷史
kubectl rollout history deployment/demo-deployment
```

---

### Service：穩定的 DNS + VIP

Pod 的 IP 是從 cluster 的 Pod CIDR 動態分配的，重啟就換一個。Service（服務）提供穩定的端點：一個不變的 Cluster IP（VIP）和 DNS 名稱，背後對應一組 Pod。

K8s 的 kube-proxy 負責在每台 node 維護 iptables/ipvs 規則，把打到 Service VIP 的封包 DNAT 到後端 Pod。

#### ClusterIP（預設）

只在 cluster 內部可存取。kube-dns 讓你用以下格式存取：

```
<service-name>.<namespace>.svc.cluster.local
```

例如：`demo-svc.default.svc.cluster.local`。在同一個 Namespace 內，可以縮短為 `demo-svc`。

```yaml
apiVersion: v1
kind: Service
metadata:
  name: demo-svc
  namespace: default
spec:
  selector:
    app: demo                 # 找所有 label app=demo 的 Pod，加入 Endpoints
  ports:
  - protocol: TCP
    port: 80                  # Service 監聽的 port（VIP 的 port）
    targetPort: 80            # 轉發到 Pod 的哪個 port
  type: ClusterIP             # 預設值，可省略
```

Service 的 `selector` 是動態的——K8s 持續追蹤符合 selector 的 Pod，更新 Endpoints 物件。Pod 死了，Endpoints 自動移除；新 Pod 起來並通過 readinessProbe，自動加入。

#### NodePort

在每台 node 上開一個固定 port（預設範圍 30000–32767）：

```yaml
spec:
  type: NodePort
  ports:
  - port: 80
    targetPort: 80
    nodePort: 31234    # 手動指定，不指定則隨機選一個 30000-32767 的值
```

從外部打 `<node-ip>:31234` 就能到 Pod。適合本機 lab，生產環境不常用（每台 node 都暴露同一個 port，管理麻煩）。

#### LoadBalancer

雲端環境（EKS/AKS/GKE）才有意義。K8s 呼叫雲端的 LB API（例如 AWS NLB/ALB），建立一個外部 LB 並把流量引進來。本機用 minikube 的話要額外跑 `minikube tunnel` 才能拿到 EXTERNAL-IP。

---

### Namespace：邏輯隔離，不是安全邊界

K8s 的 Namespace（命名空間）是**邏輯分組**，讓你在同一個 cluster 裡有多個互不衝突的名字空間。同名的 Pod 可以存在不同 Namespace。

**這不是 Linux kernel namespace**，雖然名字一樣。它不提供：

- 網路隔離（不同 Namespace 的 Pod 預設可以互相 TCP 連線）
- CPU/記憶體隔離（要用 LimitRange / ResourceQuota）
- 存取控制（要用 RBAC）

最常見的誤解：「我把測試環境和生產環境放不同 Namespace，它們就安全隔離了。」這是錯的。沒有 NetworkPolicy，一個在 `staging` Namespace 的 Pod 可以直接打 `production` Namespace 的 Service。NetworkPolicy 是 Ch 24 的主題，RBAC 是 Ch 23 的主題。

基本操作：

```bash
# 建立 Namespace
kubectl create namespace my-ns

# 在特定 Namespace 操作
kubectl get pods -n my-ns

# 看所有 Namespace 的資源
kubectl get pods --all-namespaces
# 或者縮寫
kubectl get pods -A

# 設定 kubectl 的預設 Namespace（這個 session 都生效）
kubectl config set-context --current --namespace=my-ns
```

系統保留的 Namespace：`kube-system`（control plane 元件）、`kube-public`（匿名可讀）、`kube-node-lease`（node heartbeat）。不要把業務 Pod 丟進 `kube-system`——這是個常見的持久化後門手法，Ch 29 會詳細說。

---

### Label 與 Selector：膠水機制

Label（標籤）是附在任何 K8s 物件上的 key-value 對，純粹是 metadata。Selector（選擇器）讓其他物件根據 label 過濾。

Service 找 Pod、ReplicaSet 找 Pod、NetworkPolicy 選目標，都靠 label/selector。`matchLabels` 是最常見的形式：

```yaml
selector:
  matchLabels:
    app: demo
    tier: frontend    # 多個 key-value 是 AND 條件
```

任何同時有 `app=demo` 且 `tier=frontend` 的 Pod 都符合。Selector 寫錯是 Service 無法路由的最常見原因，後面的失敗範例會示範。

---

## 具體可跑範例

### 範例一：部署 nginx Deployment + Service

```bash
# 建一個 3 replica 的 nginx Deployment
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-deployment
spec:
  replicas: 3
  selector:
    matchLabels:
      app: demo
  template:
    metadata:
      labels:
        app: demo
    spec:
      containers:
      - name: nginx
        image: nginx:1.25
        ports:
        - containerPort: 80
        resources:
          requests:
            memory: "32Mi"
            cpu: "100m"
          limits:
            memory: "64Mi"
            cpu: "200m"
EOF

# 確認 Pod 都起來了
kubectl get pods -l app=demo

# 建對應的 ClusterIP Service
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: demo-svc
spec:
  selector:
    app: demo
  ports:
  - protocol: TCP
    port: 80
    targetPort: 80
EOF

# 確認 Service 和 Endpoints
kubectl get svc demo-svc
kubectl get endpoints demo-svc
# 應該看到 3 個 Pod IP:port，例如：
# NAME       ENDPOINTS                                            AGE
# demo-svc   10.244.0.5:80,10.244.0.6:80,10.244.0.7:80          10s

# 從 cluster 內驗證（起一個臨時 pod 打 Service）
kubectl run test-curl --image=curlimages/curl:latest --rm -it --restart=Never -- \
  curl -s http://demo-svc.default.svc.cluster.local
```

### 範例二：滾動升級與回滾

```bash
# 升級映像版本（觸發滾動升級）
kubectl set image deployment/demo-deployment nginx=nginx:1.26

# 即時觀察升級過程
kubectl rollout status deployment/demo-deployment

# 看 ReplicaSet 狀態（升級期間會看到新舊兩個 RS）
kubectl get replicaset -l app=demo

# 升級完成後，舊 RS 的 DESIRED/READY 變 0，新 RS 是 3
# NAME                          DESIRED   CURRENT   READY
# demo-deployment-5d8b4c6f7d    0         0         0      ← 舊的
# demo-deployment-7f9c8d4e2b    3         3         3      ← 新的

# 回滾到上一個版本
kubectl rollout undo deployment/demo-deployment

# 確認映像回到 1.25
kubectl describe deployment demo-deployment | grep Image
```

### 範例三：Selector 打錯導致 Service Endpoints 為空（失敗邊界）

這是線上最常見的症狀之一：Service 建好了，但流量打不到 Pod。

```bash
# 建一個 selector 故意打錯的 Service
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: broken-svc
spec:
  selector:
    app: wrong-label    # 注意：Pod 的 label 是 app=demo，這裡打成 wrong-label
  ports:
  - protocol: TCP
    port: 80
    targetPort: 80
EOF

# 查看 Endpoints：會看到 <none>
kubectl get endpoints broken-svc
# NAME         ENDPOINTS   AGE
# broken-svc   <none>      5s

# 這時從 cluster 內打 broken-svc 會 connection refused
kubectl run test-curl --image=curlimages/curl:latest --rm -it --restart=Never -- \
  curl -s --connect-timeout 3 http://broken-svc

# 診斷步驟：比對 Service selector 和 Pod label
kubectl get svc broken-svc -o jsonpath='{.spec.selector}'
# {"app":"wrong-label"}

kubectl get pods --show-labels -l app=demo
# NAME                             LABELS
# demo-deployment-xxx-yyy          app=demo,pod-template-hash=xxx

# 修正：把 selector 改回 app=demo
kubectl patch svc broken-svc -p '{"spec":{"selector":{"app":"demo"}}}'

# 驗證 Endpoints 補回來了
kubectl get endpoints broken-svc
```

---

## 對比取捨表

| 維度 | Pod（裸） | Deployment | StatefulSet |
|------|-----------|------------|-------------|
| 生命週期管理 | 手動 | 自動（RS） | 自動（有序） |
| 滾動升級 | 無 | 有 | 有（有序） |
| Pod identity | 隨機 | 隨機 | 固定（pod-0, pod-1）|
| 穩定儲存 | 無 | 可加 PVC 但不穩 | 每個 Pod 獨立 PVC |
| 使用場景 | 除錯/測試 | 無狀態服務 | 資料庫/Kafka |

| Service 類型 | 可達範圍 | 適用場景 |
|---|---|---|
| ClusterIP | cluster 內部 | 微服務間通訊 |
| NodePort | 外部（透過 node IP） | 本機 lab、簡單暴露 |
| LoadBalancer | 外部（透過雲 LB） | 生產雲端環境 |
| ExternalName | — | 把外部域名對應成 K8s Service |

---

## 踩雷集錦

**1. `selector` 和 `template.labels` 不一致，Deployment 建立失敗**

```
The Deployment "demo" is invalid: spec.template.metadata.labels: Invalid value:
map[string]string{"app":"nginx"}: `selector` does not match template `labels`
```

`spec.selector.matchLabels` 必須是 `spec.template.metadata.labels` 的子集。api-server 在 admission 階段就會擋，不會讓你建出一個永遠找不到自己 Pod 的 Deployment。

**2. Pod 一直在 Pending，原因是 resource requests 超過 node 容量**

```bash
kubectl describe pod <name> | grep -A 5 Events
# Insufficient cpu.
```

minikube 預設只有 2 CPU、2 GiB 記憶體。如果你的 `requests.cpu` 乘以 `replicas` 超過了這個限制，Pod 永遠排不上去。先 `kubectl describe node` 看 `Allocatable` 和 `Allocated resources`。

**3. 改了 Pod template 但 Pod 沒更新**

直接 `kubectl edit pod` 改了映像，Pod 不會重建（Pod 的大部分欄位是 immutable）。要更新，改的是 Deployment 的 `spec.template`，讓 Deployment controller 來建新 Pod。

**4. 刪掉 Pod 後新的 Pod name 不同，但 Service 繼續正常**

這是設計的一部分。Service 靠 label selector 找 Pod，不靠 Pod name。Endpoint controller 會即時更新 Endpoints，Pod 重建後新的 IP 會自動進來。但如果有程式碼把舊的 Pod IP 硬編碼進去，就會 panic——這是為什麼你要透過 Service DNS 而不是直接打 Pod IP。

**5. NodePort 範圍限制：30000–32767**

嘗試指定 `nodePort: 80` 會被 api-server 拒絕。這個範圍是 api-server 的 `--service-node-port-range` 參數控制的（預設 30000–32767），目的是避免和系統 well-known port 衝突。

---

## 進階延伸

**Init Containers**：在主容器起來之前跑一次性的初始化工作（例如等資料庫就緒、下載設定檔）。攻擊視角：init container 也可以帶危險的 securityContext，且通常不在監控視野內。

**Horizontal Pod Autoscaler（HPA）**：根據 CPU/memory 使用量自動調整 Deployment 的 `replicas`。HPA 需要 metrics-server 才能工作。

**PodDisruptionBudget（PDB）**：宣告「這個 Deployment 最多允許幾個 Pod 同時不可用」，防止 node drain 或版本升級把服務打垮。

**Headless Service**（`clusterIP: None`）：不分配 VIP，DNS 查詢直接回 Pod IP 列表。StatefulSet 的穩定 DNS 就靠這個，`pod-0.svc-name.namespace.svc.cluster.local` 永遠指向同一個 Pod。

**Field Selectors vs Label Selectors**：Label selector 是 K8s 自己的路由機制；field selector（`kubectl get pods --field-selector status.phase=Running`）是 api-server 層面的過濾，只用於查詢，不能用在 Service/RS 的 selector。

---

## 本章重點整理

- Pod 是 K8s 最小調度單位；同 Pod 內容器共享 network namespace、volume、IPC namespace
- Pod 是暫時性的，生命週期交給 Deployment 管，不要直接操作 Pod
- Deployment 透過 ReplicaSet 確保副本數，並提供滾動升級與回滾語意
- Service 提供穩定 VIP 與 DNS，透過 label selector 動態追蹤 Pod；ClusterIP 用於 cluster 內部，NodePort 用於外部存取
- `spec.selector.matchLabels` 打錯是 Service Endpoints 為空的最常見原因，`kubectl get endpoints` 是第一個診斷工具
- Namespace 是邏輯命名空間，不提供網路隔離或存取控制隔離——這兩者分別要靠 NetworkPolicy 和 RBAC
- Pod spec 裡的 `hostPID`、`hostNetwork`、`privileged`、`hostPath` 是容器逃逸的主要攻擊面，Part 5 會以此為起點

---

## 自我檢核

1. 同一個 Pod 內的兩個容器，如何在不走 overlay network 的情況下互相通訊？為什麼可以？
2. 你建了一個 Deployment，但 `kubectl get endpoints my-svc` 顯示 `<none>`。列出至少三種可能原因並說明診斷步驟。
3. `kubectl rollout undo` 的底層行為是什麼？它改的是 Deployment spec 的哪個欄位？舊的 ReplicaSet 會消失嗎？
4. 為什麼說「把測試和生產放不同 Namespace 就安全」是錯的？需要什麼才能真正做到網路層隔離？
5. Service 的 `port` 和 `targetPort` 有什麼差別？如果容器實際監聽 8080，但你的 Service 寫 `targetPort: 80`，會發生什麼？

---

## 延伸閱讀

- [Kubernetes 官方文件 — Pod 概念](https://kubernetes.io/docs/concepts/workloads/pods/)：spec 欄位的官方定義，`securityContext` 的所有選項都在這裡
- [Kubernetes 官方文件 — Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)：滾動升級策略（`RollingUpdate` vs `Recreate`）的完整說明
- [Kubernetes 官方文件 — Service](https://kubernetes.io/docs/concepts/services-networking/service/)：各 Service 類型的詳細行為，kube-proxy 的 iptables/ipvs 模式差異
- [NSA/CISA Kubernetes Hardening Guide（2022）](https://media.defense.gov/2022/Aug/29/2003066362/-1/-1/0/CTR_KUBERNETES_HARDENING_GUIDANCE_1.2_20220829.PDF)：官方資安指導，Pod security 章節直接對應本章的危險欄位清單
- [OWASP Kubernetes Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Kubernetes_Security_Cheat_Sheet.html)：以攻擊類型為索引的防禦清單，Ch 25 之後的攻擊章都可以在這裡找到對應防禦

---

K8s 的物件模型到這裡算是站穩了。下一章我們要看最重要的資安件：誰有資格呼叫 api-server、那個 token 從哪來、Role 和 ClusterRole 的差別在哪——RBAC 搞懂了，K8s 提權才能真正打通。

→ [Ch 23 — RBAC 與認證：ServiceAccount / token / Role](./23-k8s-rbac-auth.md)
