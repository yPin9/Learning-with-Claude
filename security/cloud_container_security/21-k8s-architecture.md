# Ch 21 — K8s 架構：control plane / node / etcd / API server

> **目標**：理解 Kubernetes（K8s）的核心架構，能說清楚每個組件的職責、資料流如何流動，以及為什麼 api-server 與 etcd 是攻擊者的首要目標。
>
> **環境**：kubectl v1.30+、minikube v1.33+（driver=docker）或 kind v0.23+；本章範例以 minikube 為主，kind 指令會並列標注。

---

## 為什麼需要 K8s

從 `docker run` 出發。單台機器上跑三個 container，沒什麼問題：crash 了手動重啟，要升版就 `docker stop` 再 `docker run` 新 image，log 用 `docker logs` 看。

現在換個場景：50 個微服務，200 台機器，每個服務各有不同的版本、依賴、資源需求，需要：

- **自動重啟**：container 死掉要立刻在另一台機器拉起來
- **滾動升級（rolling update）**：不能一口氣把所有 replica 都砍掉換新版，要逐步替換、失敗要自動回滾
- **負載分配**：流量要根據可用的 container 數量動態分配
- **資源調度**：把 container 放到還有足夠 CPU/記憶體的機器上
- **服務發現**：A 服務要找 B 服務，不能寫死 IP，機器一換 IP 就全炸
- **密語管理**：DB 密碼不能硬寫在每台機器的設定檔裡

手動 `docker run` / `docker stop` 根本不可能應付這個規模。你需要一個**編排器（orchestrator）**。

Kubernetes 的核心承諾只有一句話：**你告訴我想要什麼狀態，我讓系統持續趨向那個狀態。**

你不說「啟動這個 container」，你說「我需要三個這個服務的副本一直跑著」。K8s 負責讓現實符合你的期望。

---

## 先建直覺

把 K8s cluster 想成一個工廠：

```
┌─────────────────────────────────────────────────────────────────┐
│                        Control Plane（廠長室）                    │
│                                                                  │
│  ┌─────────────┐   ┌──────────┐   ┌─────────────────────────┐  │
│  │  api-server  │   │   etcd   │   │  controller-manager      │  │
│  │（唯一入口）   │   │（帳本）  │   │（盯著帳本跟現場的人）    │  │
│  └──────┬──────┘   └──────────┘   └─────────────────────────┘  │
│         │                                                        │
│         │          ┌──────────┐                                  │
│         │          │ scheduler│（負責分配誰去哪條產線）           │
│         │          └──────────┘                                  │
└─────────┼────────────────────────────────────────────────────────┘
          │  (HTTPS/REST)
          │
┌─────────┼────────────────────────────────────────────────────────┐
│         │           Worker Nodes（產線）                          │
│    ┌────▼──────┐   ┌──────────┐   ┌─────────────────────────┐   │
│    │  kubelet  │   │kube-proxy│   │  containerd              │   │
│    │（現場領班）│   │（網路接線）│  │（實際開機器的人）        │   │
│    └───────────┘   └──────────┘   └─────────────────────────┘   │
│                                                                   │
│    Node 1: [Pod A] [Pod B]    Node 2: [Pod C]    Node 3: ...    │
└───────────────────────────────────────────────────────────────────┘
```

廠長室（control plane）負責決策，產線（worker node）負責執行。所有人只跟廠長室說話，廠長室的唯一對外窗口是 **api-server**。

---

## 底層機制

### Control Plane 組件

#### api-server

所有操作的唯一入口。`kubectl`、`kubelet`、`controller-manager`、`scheduler` 全部都打 api-server，沒有任何組件之間會直接互通。

api-server 暴露 RESTful API，每個 K8s 資源（Pod、Deployment、Service…）都是 REST 端點。你 `kubectl get pods` 本質上是 `GET /api/v1/namespaces/default/pods`。

每個請求進 api-server 都要過三關：

```
Request → Authentication（你是誰）
        → Authorization / RBAC（你能做什麼）
        → Admission Control（這個操作合法嗎，符合 policy 嗎）
        → etcd（寫入或讀取狀態）
```

Admission Control 這關特別重要，Webhook-based admission controller 讓你可以在這裡注入 policy（例如「所有 container 不能用 root 執行」）。

api-server 預設 listen 在 port **6443**（HTTPS）。

Authentication 支援多種機制並存，依序嘗試：

| 機制 | 典型用途 |
|---|---|
| TLS client certificate | kubelet、controller-manager、scheduler 打 api-server |
| Bearer token（JWT） | ServiceAccount token（Pod 內應用程式）、bootstrap token |
| kubeconfig 的 `user.token` 或 `user.client-certificate` | kubectl 操作 |
| Webhook token（OIDC）| 整合外部 IdP（例如 Dex、AWS IAM） |

沒有任何一個機制通過的請求，api-server 把它當作匿名請求（`system:anonymous`），通常 RBAC 不給匿名任何權限，直接 403。

#### etcd

分散式 key-value store（鍵值儲存庫），整個 cluster 的唯一真相來源（single source of truth）。所有的 cluster 狀態——Pod 在哪台 node、Deployment 的期望副本數、ConfigMap、Secret——全部存在這裡。

etcd 只跟 api-server 說話，其他組件不直接存取 etcd。

**安全警示**：Secret 在 etcd 裡預設只是 base64 編碼，不是加密。K8s 有 `--encryption-provider-config` 可以開啟 at-rest encryption，但這個功能預設**關閉**。etcd 預設 listen 在 port **2379**（client）和 **2380**（peer）。

etcd 通常用 Raft 共識演算法跑成奇數個節點的 cluster（3 或 5），避免 split-brain。

#### scheduler

scheduler 只做一件事：看有沒有還沒被分配到 node 的 Pod（`nodeName` 欄位為空），然後幫它選一台 node。

選 node 的邏輯：

1. **Filtering（篩選）**：哪些 node 能跑這個 Pod？考慮 CPU/記憶體 resource request、NodeSelector、Affinity、Taint/Toleration
2. **Scoring（評分）**：剩下的 node 哪個最適合？考慮資源剩餘量、spread 分布

scheduler 選完之後把結果寫回 api-server（更新 Pod 的 `nodeName`），自己不直接跟 node 溝通。

Taint/Toleration 是 scheduler filtering 階段的關鍵機制：你可以在 node 上設 taint（標記「這台不給普通 Pod 用」），只有 spec 裡有對應 toleration 的 Pod 才能被排到這台 node。常見用途：GPU node 打上 `gpu=true:NoSchedule`，只有需要 GPU 的 Pod 才容許被排到這台；control plane node 預設有 `node-role.kubernetes.io/control-plane:NoSchedule`，防止一般工作負載跑到 control plane。

```yaml
# Pod 加 toleration 才能排到 GPU node
spec:
  tolerations:
  - key: "gpu"
    operator: "Equal"
    value: "true"
    effect: "NoSchedule"
```

#### controller-manager

一個進程裡跑了幾十個 controller，每個 controller 負責一種資源：

- ReplicaSet controller：確保 Pod 副本數跟期望一致
- Deployment controller：管 rolling update 邏輯
- Node controller：偵測 node 是否 NotReady
- Service Account controller：為新 namespace 建立預設 ServiceAccount
- …

以 ReplicaSet controller 為例，它的運作是一個 **reconciliation loop（調和迴圈）**：

```
loop forever:
    desired  = etcd 裡 ReplicaSet 說要幾個 Pod
    actual   = etcd 裡實際存在、Running 的 Pod 數量
    if actual < desired:
        建立新 Pod（透過 api-server 寫入 etcd）
    if actual > desired:
        刪除多餘的 Pod（透過 api-server）
    sleep(short interval)
```

這個模式是整個 K8s 的設計核心，後面會再解釋。

### Node 組件

#### kubelet

每台 worker node 上跑一個 kubelet，是這台機器的代理人。

kubelet 向 api-server 註冊自己、定期回報機器狀態，並且監聽「有沒有新的 Pod 被排程到我這台機器」。一旦發現，kubelet 就叫 container runtime 把 container 拉起來。

kubelet 啟動的流程：

1. 用 bootstrap token 或 TLS client cert 向 api-server 認證
2. 呼叫 `POST /api/v1/nodes` 把自己的節點資訊（CPU 核數、記憶體大小、kubelet 版本）寫進 etcd
3. 建立 watch：監聽「有沒有 Pod 的 `spec.nodeName` 等於我這台機器的名字」
4. 收到 Pod spec 後，呼叫 containerd 的 CRI API 建立 sandbox 和 container

kubelet 也負責：

- 執行 Pod 的 liveness/readiness/startup probe
- 掛載 Volume（呼叫 CSI driver 或直接 mount hostPath）
- 把 container log 送到 `/var/log/pods/` 並透過 CRI log 讓 `kubectl logs` 可讀
- 定期回報 Pod 狀態、node 資源使用量給 api-server

kubelet 的 node 健康監控是透過 **Node Lease** 機制（K8s 1.17 後預設啟用）：kubelet 每 10 秒更新 `kube-node-lease` namespace 裡的一個 Lease 物件，Node controller 如果在 40 秒內沒看到 Lease 更新，就把這台 node 標記為 `NotReady`，再過 5 分鐘會觸發 Pod 驅逐（eviction）。這個數字（40 秒、5 分鐘）是 `--node-monitor-grace-period` 和 `--pod-eviction-timeout` 可以調的，在低延遲要求的場景通常調小、在網路抖動嚴重的場景調大避免誤驅逐。

#### kube-proxy

每台 node 上都有一個 kube-proxy，負責維護 iptables 或 ipvs 規則，讓 K8s Service 的 virtual IP（ClusterIP）能正確轉發到後端 Pod。

當你建立一個 Service，kube-proxy 會在每台 node 上寫入對應的 NAT 規則，讓任何打到這個 ClusterIP:port 的流量都轉發到其中一個 Pod。

iptables 模式下，每個 Service 對應一條 DNAT 規則鏈。Service endpoint 多的時候（幾千個 Pod），iptables 規則數量爆炸，更新時間也拉長——這是 kube-proxy 切換到 ipvs 模式的主要原因。ipvs 用 hash table 查表，更新是 O(1)，iptables 是 O(n)。

#### container runtime

實際執行容器的組件。現代 K8s 用 **containerd**，透過 CRI（Container Runtime Interface）跟 kubelet 溝通。Docker daemon 在 K8s 1.24 之後已經不再支援（Docker 底層其實也是 containerd，K8s 拿掉中間層）。

### `kubectl apply` 的完整資料流

```
kubectl apply -f pod.yaml
       │
       │  HTTPS POST /api/v1/namespaces/default/pods
       ▼
┌──────────────┐
│  api-server  │
│  1. Authn    │  驗你的 kubeconfig 憑證（TLS client cert 或 token）
│  2. Authz    │  RBAC 檢查：你有權限在 default namespace create pod 嗎
│  3. Admission│  ValidatingWebhook / MutatingWebhook 跑完
└──────┬───────┘
       │  寫入期望狀態
       ▼
┌──────────────┐
│    etcd      │  key: /registry/pods/default/my-pod
│              │  value: Pod spec（protobuf 編碼）
└──────┬───────┘
       │  api-server 透過 watch 機制通知 scheduler
       ▼
┌──────────────┐
│  scheduler   │  發現 nodeName="" 的 Pod，跑 filter/score，選出 node-1
│              │  PATCH /api/v1/.../my-pod 把 nodeName 設為 node-1
└──────┬───────┘
       │  etcd 更新，api-server 透過 watch 通知 node-1 的 kubelet
       ▼
┌──────────────┐
│   kubelet    │  （在 node-1 上）拿到 Pod spec
│  (node-1)   │  叫 containerd pull image、建 container、設 network
└──────┬───────┘
       │  container 起來後
       ▼
   kubelet PATCH Pod status → api-server → etcd
   Pod 狀態從 Pending 變 Running
```

整個過程裡，**etcd 是唯一的狀態存放點，api-server 是唯一的狀態入口**。沒有任何兩個組件直接互相說話，全部透過 api-server 的 watch/notify 機制協調。

---

## 宣告式 vs 命令式

**命令式（imperative）**：告訴系統「做什麼動作」。

```bash
docker run --name web nginx:1.25
docker stop web
docker rm web
docker run --name web nginx:1.26
```

你在手動管理狀態轉換。如果機器在中間掛了，你要自己知道現在在哪個狀態、下一步要做什麼。

**宣告式（declarative）**：告訴系統「我想要什麼結果」。

```yaml
# pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: web
spec:
  containers:
  - name: web
    image: nginx:1.26
```

```bash
kubectl apply -f pod.yaml
```

你只告訴 K8s「我要一個跑 nginx:1.26 的 Pod 叫做 web」。至於它現在跑不跑、要不要先停再重啟，K8s 自己算。

K8s 的 controller 永遠在問：**desired state（期望狀態）和 actual state（實際狀態）有差嗎？有的話怎麼讓它們收斂？**

這個模式讓整個系統有自癒能力（self-healing）。Pod 掛了？controller 發現 actual < desired，自動補起來。Node 掛了？scheduler 把 Pod 重新調度到其他 node。你不需要寫 if-else 腳本來處理各種失敗情況。

宣告式的另一個好處是**idempotent（冪等）**。同一份 `pod.yaml` 跑 `kubectl apply` 十次，結果是一樣的——K8s 會比對目前狀態，只在有差異時才做操作。命令式的 `docker run` 跑兩次就兩個 container。

這個設計對 GitOps 工作流來說很關鍵：把所有 K8s YAML 存在 git，CI/CD 每次 merge 就重新 `kubectl apply`，系統永遠趨向 git 裡描述的狀態，出錯回滾就是 `git revert` 再觸發一次 apply。

---

## 安全視角：皇冠上的珠寶

從攻擊者的角度看 K8s 架構，有兩個目標的價值遠超過其他一切。

### api-server：cluster 的指揮中心

api-server 是所有操作的入口，拿到能打 api-server 的 credential 就能控制整個 cluster。credential 來源有幾種：

1. **kubeconfig**：通常在 `~/.kube/config`，裡面有 TLS client certificate 或 bearer token。拿到 admin kubeconfig = 拿到整個 cluster 的 root。
2. **ServiceAccount token**：跑在 Pod 裡的應用程式用來跟 api-server 溝通的 token，自動掛載在 `/var/run/secrets/kubernetes.io/serviceaccount/token`。許多應用程式的 ServiceAccount 被賦予了過高的 RBAC 權限。
3. **Node bootstrap token**、**admission webhook credential** 等各種特殊用途 token。

攻擊路徑一（Pod 內橫向）：拿到一個有過高 RBAC 權限的 Pod 的 shell → 讀取 `/var/run/secrets/kubernetes.io/serviceaccount/token` → 打 api-server 建立特權 Pod（`hostPID: true`、`hostNetwork: true`、`privileged: true`）→ 從 privileged container 逃逸到 host。

```bash
# 在受害 Pod 裡執行，確認 token 有多少權限
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
APISERVER=https://kubernetes.default.svc

# 用 token 查詢 cluster 資源
curl -s -k -H "Authorization: Bearer $TOKEN" \
  "$APISERVER/api/v1/namespaces/kube-system/secrets" | python3 -m json.tool | head -40
```

如果 ServiceAccount 有 `cluster-admin` ClusterRoleBinding，這個 curl 就能讀出所有 namespace 的所有 Secret，等於 cluster 被端掉。

攻擊路徑二（node 橫向）：拿到 worker node 的 root shell → 讀取 `/var/lib/kubelet/kubeconfig` → 這個 kubeconfig 的 credential 對應 node 的 system:node 角色，有限但可進一步提升。

### etcd：未加密的 cluster 大腦

etcd 存了整個 cluster 的狀態，包含所有 Secret（預設只是 base64）。如果攻擊者能直接存取 etcd（port 2379），不需要過 api-server 的 RBAC：

```
# 直接用 etcdctl 讀 secret（如果 etcd 沒有認證或拿到 etcd 的 TLS 憑證）
etcdctl --endpoints=https://127.0.0.1:2379 \
        --cacert=/etc/kubernetes/pki/etcd/ca.crt \
        --cert=/etc/kubernetes/pki/etcd/server.crt \
        --key=/etc/kubernetes/pki/etcd/server.key \
        get /registry/secrets/default/my-secret
```

輸出是 protobuf，但用 `strings` 或對應工具可以直接讀出 secret 的值，完全不受 RBAC 限制。

輸出是 protobuf，但用 `strings` 或對應工具可以直接讀出 secret 的值，完全不受 RBAC 限制。更危險的操作是直接寫入 etcd 建立物件，繞過 admission control：

```bash
# 把一個高權限 ClusterRoleBinding 直接寫進 etcd（繞過 api-server 的 admission）
# 這種攻擊不會留在 K8s audit log 裡，因為根本沒過 api-server
etcdctl --endpoints=https://127.0.0.1:2379 \
        --cacert=/etc/kubernetes/pki/etcd/ca.crt \
        --cert=/etc/kubernetes/pki/etcd/server.crt \
        --key=/etc/kubernetes/pki/etcd/server.key \
        put /registry/clusterrolebindings/evil-binding \
        <protobuf-encoded-object>
```

etcd 的 TLS 憑證通常放在 control plane node 的 `/etc/kubernetes/pki/etcd/`。能進 control plane node = 能讀 etcd = 遊戲結束。

**攻擊者優先順序**：

1. 拿 admin kubeconfig（在 control plane 的 `~/.kube/config` 或 `/etc/kubernetes/admin.conf`）
2. 直接存取 etcd（拿 etcd 的 client 憑證，或 etcd 沒開 TLS）
3. 找 RBAC 過高的 ServiceAccount token（在各 Pod 裡）

---

## 具體範例

**本段未實測，為理論預期行為**。以下範例需要真實環境才能跑。用 `minikube start` 或 `kind create cluster` 自行建立 cluster 驗證。

### 範例一：建立 cluster

```bash
# minikube 建 cluster，指定 driver 和 K8s 版本
# --driver=docker 讓 minikube 在 Docker container 裡跑整個 cluster
# --kubernetes-version 鎖版避免行為差異
minikube start --driver=docker --kubernetes-version=v1.30.0

# kind 替代方案
# kind create cluster --name lab --image kindest/node:v1.30.0
```

minikube 會下載 VM 映像或 container image、設定 TLS 憑證、產生 kubeconfig 並自動合併到 `~/.kube/config`，整個過程約 2–5 分鐘，取決於網路速度。

### 範例二：確認 cluster 狀態

```bash
kubectl cluster-info
```

預期輸出：

```
Kubernetes control plane is running at https://127.0.0.1:49312
CoreDNS is running at https://127.0.0.1:49312/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
```

第一行的 URL 就是 api-server 的位址。Port 號在 minikube 裡是隨機分配的，kind 也是。`https://` 確認 api-server 要求 TLS，不是裸文字協定。

```bash
kubectl get nodes
```

預期輸出：

```
NAME       STATUS   ROLES           AGE   VERSION
minikube   Ready    control-plane   2m    v1.30.0
```

minikube 預設是 single-node cluster，這台 node 同時扮演 control plane 和 worker。`STATUS=Ready` 表示 kubelet 正常、網路插件（CNI）也 Ready。

### 範例三：觀察 control plane 組件

```bash
# kube-system namespace 裡跑的是 K8s 內建組件
kubectl get pods -n kube-system
```

預期輸出（minikube）：

```
NAME                               READY   STATUS    RESTARTS   AGE
coredns-76f75df574-xxxx            1/1     Running   0          3m
etcd-minikube                      1/1     Running   0          3m
kube-apiserver-minikube            1/1     Running   0          3m
kube-controller-manager-minikube   1/1     Running   0          3m
kube-proxy-xxxx                    1/1     Running   0          3m
kube-scheduler-minikube            1/1     Running   0          3m
storage-provisioner                1/1     Running   0          3m
```

對應關係：

| Pod 名稱 | 對應組件 | 說明 |
|---|---|---|
| `etcd-minikube` | etcd | cluster 狀態資料庫 |
| `kube-apiserver-minikube` | api-server | 所有操作的入口 |
| `kube-controller-manager-minikube` | controller-manager | N 個 controller 的集合 |
| `kube-scheduler-minikube` | scheduler | Pod 分配到 node |
| `kube-proxy-xxxx` | kube-proxy | iptables/ipvs 規則維護 |
| `coredns-*` | CoreDNS | cluster 內部 DNS 解析 |
| `storage-provisioner` | minikube 內建 | 非標準 K8s 組件，minikube 用來提供 PVC |

注意 control plane 組件（etcd、api-server、scheduler、controller-manager）的 Pod 名稱後面帶 `-minikube`，是因為它們是 **static Pod**，由 kubelet 直接從 `/etc/kubernetes/manifests/` 目錄讀取 YAML 啟動，不受 scheduler 管理，node 名稱被附在後面作為識別。

### 範例四：失敗邊界——cluster 還沒 Ready 就 apply

```bash
# 假設 cluster 剛在初始化，api-server 還沒起來
# 或者刻意用錯的 endpoint 測試
kubectl --server=https://127.0.0.1:9999 get pods
```

預期輸出：

```
The connection to the server 127.0.0.1:9999 was refused - did you specify the right host or port?
```

更常見的情況是 minikube 在跑 `minikube start` 期間就去 `kubectl apply`：

```bash
minikube start &
sleep 10
kubectl apply -f pod.yaml
# Error from server (ServiceUnavailable): the server is currently unable to handle the request
# 或者
# Unable to connect to the server: dial tcp 127.0.0.1:49312: connect: connection refused
```

K8s 有兩種層次的失敗：**連 api-server 都不通**（上面的例子），以及 **api-server 通但 cluster 還沒 Ready**（etcd 未完成 leader election，api-server 回 503）。

在腳本裡通常用 `kubectl wait --for=condition=Ready node/minikube --timeout=120s` 等 cluster 就緒再繼續。

---

## 對比取捨：單點故障 vs HA

| 組件 | 單點（預設 minikube/kind）| 風險 | HA 設定 |
|---|---|---|---|
| api-server | 1 個 | 掛了所有 kubectl/kubelet 操作全停，Pod 繼續跑但無法管理 | 多個 api-server 實例 + L4 Load Balancer |
| etcd | 1 個 | 掛了 api-server 無法讀寫狀態，整個 cluster 失去記憶 | 3 或 5 個 etcd 節點，Raft 共識 |
| scheduler | 1 個 | 掛了新 Pod 無法被排程，現有 Pod 繼續跑 | 多個 scheduler，leader election |
| controller-manager | 1 個 | 掛了 reconciliation 停止，Pod 死掉不會被補 | 多個，leader election |
| kubelet | 每 node 一個 | 掛了這台 node 的 Pod 不受管，node 會被標 NotReady | 無法 HA，靠 auto-repair 替換 node |
| kube-proxy | 每 node 一個 | 掛了這台 node 的 Service 網路規則不更新 | 同 kubelet |

生產環境的 HA control plane 至少要 3 台 control plane node（每台都跑 api-server + scheduler + controller-manager），etcd 也是 3 節點。api-server 前面放 load balancer（可以是 cloud LB 或 HAProxy/keepalived）。

---

## 踩雷集錦

**1. 忘記 namespace，找不到 Pod**

`kubectl get pods` 預設只看 `default` namespace。kube-system 的 Pod 在另一個 namespace，要加 `-n kube-system` 或 `-A`（all namespaces）。

**2. kubeconfig 的 context 沒切換**

多個 cluster（本機 minikube + 公司的 staging + production）的 kubeconfig 合併在一起，用 `kubectl config current-context` 確認你現在打的是哪個 cluster，`kubectl config use-context <name>` 切換。在 production 誤下 `kubectl delete deployment` 的代價非常高。

**3. control plane 組件是 static Pod，不能用 kubectl delete 刪**

`kubectl delete pod kube-apiserver-minikube -n kube-system` 會成功，但 kubelet 馬上從 `/etc/kubernetes/manifests/kube-apiserver.yaml` 重新建起來。要修改 control plane 組件的行為，要改那個目錄裡的 YAML，kubelet 會自動偵測變化重啟。

**4. etcd 的 base64 不是加密**

`kubectl get secret my-secret -o yaml` 看到的 `data:` 欄位是 base64，`echo <value> | base64 -d` 就是明文。真正的加密要設 `--encryption-provider-config`，這個沒有預設開啟，很多教學跳過這段。

**5. minikube 的網路和真實 cluster 不同**

minikube 用 NodePort 可以 `minikube service <name>` 直接開瀏覽器，但生產 cluster 的 LoadBalancer Service 需要 cloud provider 支援。用 minikube 學完之後到 EKS/GKE 會發現 Service 行為有差，要注意。

---

## 進階延伸

- **etcd 的 Raft 協議**：理解為什麼要奇數個 etcd 節點、quorum 怎麼計算、split-brain 怎麼避免。參考 etcd 官方文件的 "clustering guide"。

- **api-server 的 watch 機制**：組件之間不是 polling，而是 long-lived HTTP GET 的 watch stream（`?watch=true`）。理解這個機制才能解釋為什麼 K8s 的響應速度快、但 etcd compaction 調錯會炸掉 api-server 記憶體。

- **Admission Webhook 的攻防**：MutatingAdmissionWebhook 可以在 Pod 建立前修改它的 spec（例如自動注入 sidecar）。攻擊者控制 webhook server 可以在所有新 Pod 裡注入惡意 container。

- **CRI 與 containerd**：kubelet 透過 CRI（gRPC）跟 containerd 溝通。containerd 底層用 runc（OCI runtime）實際建立 namespace/cgroup。理解這條鏈路對 container escape 分析很重要。

- **kubeadm 的 PKI 架構**：`/etc/kubernetes/pki/` 目錄下的各個憑證分別給誰用、信任鏈怎麼建立。拿到 CA 私鑰可以偽造任何組件的憑證。

---

## 本章重點整理

- K8s 解決的問題：在 N 台機器上編排 M 個 container，讓系統自動維持期望狀態
- **Control plane**：api-server（唯一入口）、etcd（唯一狀態存放點）、scheduler（Pod 分配 node）、controller-manager（N 個 reconciliation loop）
- **Node 組件**：kubelet（node 代理人）、kube-proxy（Service 網路規則）、containerd（container runtime）
- `kubectl apply` 資料流：api-server → etcd → scheduler → kubelet
- **宣告式模型**：你描述期望狀態，controller 的 reconciliation loop 讓 actual 趨向 desired
- **安全關鍵**：api-server credential（kubeconfig/ServiceAccount token）和直接存取 etcd 是兩條最高優先的攻擊路徑；etcd 裡的 Secret 預設只是 base64

---

## 自我檢核

- [ ] 能說出 control plane 四個組件各自負責什麼，一個都不混
- [ ] 能畫出 `kubectl apply` 的資料流，標出每一步打的是哪個組件
- [ ] 能說清楚 scheduler 怎麼選 node（filter 再 score）
- [ ] 能解釋 reconciliation loop 的邏輯，用 ReplicaSet controller 舉例
- [ ] 知道 etcd 的 Secret 預設沒有加密，知道怎麼直接讀 etcd 的 key
- [ ] 成功用 minikube 或 kind 建起 cluster，`kubectl get pods -n kube-system` 能看到各組件 Pod
- [ ] 能分辨哪些 Pod 是 static Pod，知道為什麼刪了會自動重建

---

## 延伸閱讀

1. [Kubernetes Components — 官方文件](https://kubernetes.io/docs/concepts/overview/components/)：最權威的組件說明，每次版本升級後去這裡確認有沒有行為變化。

2. [etcd Documentation — Why etcd?](https://etcd.io/docs/v3.5/learning/why/)：etcd 自己解釋 Raft 和一致性保證，理解為什麼 K8s 選它而不是 Zookeeper。

3. [Kubernetes API Concepts — Watch and Informers](https://kubernetes.io/docs/reference/using-api/api-concepts/#efficient-detection-of-changes)：watch 機制的官方說明，理解 ResourceVersion 和 bookmark event。

4. [Securing etcd — CNCF](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#securing-etcd-clusters)：etcd TLS 設定和 encryption at rest 的官方操作指引。

5. [A Hacker's Guide to Kubernetes — CNCF Blog](https://www.cncf.io/blog/2021/11/29/securing-kubernetes-a-multi-cloud-approach/)：攻擊者視角的 K8s 安全分析，對照本章安全視角一起看。

---

本章建立了 K8s 的基礎架構直覺。下一章我們把焦點移到 K8s 的核心工作單元——Pod 是什麼、Deployment 怎麼管理 Pod 的生命週期、Service 如何讓 Pod 可以被找到、Namespace 如何做邏輯隔離。

→ [Ch 22 — 核心物件：Pod / Deployment / Service / Namespace](./22-k8s-core-objects.md)
