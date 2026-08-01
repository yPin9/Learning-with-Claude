# Ch 24 — 網路與機密：CNI / NetworkPolicy / Secret / ConfigMap

> **目標**：理解 K8s 的扁平網路模型與它帶來的橫向移動風險、掌握 NetworkPolicy 的防火牆語義、搞清楚 Secret 的 base64 陷阱與真正的靜態加密做法。
>
> **環境**：minikube（本機測試）或任意有 kubectl 的 cluster；NetworkPolicy 相關範例需要 Calico 或 Cilium CNI，minikube 預設不支援（下文說明）。

---

## 為什麼需要這一章

上一章（Ch 23）介紹了 RBAC，但 RBAC 管的是 API Server 的存取控制，管不到 Pod 與 Pod 之間的網路通道。攻擊者拿下一個 Pod 之後，下一步通常是橫向移動——這一章就是在講那條路有多寬，以及 Secret 為什麼不如你想的那樣安全。

---

## 先建直覺

K8s 網路的核心概念是「扁平網路（flat network）」。把整個 cluster 想成一個同一個 L3 廣播域，所有 Pod 拿到的是可路由的虛擬 IP，不做 NAT：

```
Node A (192.168.1.10)            Node B (192.168.1.11)
┌──────────────────────┐         ┌──────────────────────┐
│  Pod-1  10.244.0.5   │────────▶│  Pod-3  10.244.1.8   │
│  Pod-2  10.244.0.6   │         │  Pod-4  10.244.1.9   │
└──────────────────────┘         └──────────────────────┘
        CNI 橋接 / overlay tunnel（vxlan / BGP / eBPF）

Pod-1 → Pod-3：直接路由，不經 NAT，10.244.0.5 → 10.244.1.8
```

這個設計讓 Pod 通信簡單，代價是：**預設任何 Pod 都能打任何 Pod，跨 Namespace 也一樣**。

---

## 底層機制

### CNI（Container Network Interface，容器網路介面）

CNI 是一個插件規格，定義 container runtime 呼叫插件的 JSON 介面：

```
kubelet 啟動 Pod
  └─▶ 呼叫 CNI 插件（/opt/cni/bin/xxx）
        ├─ ADD：分配 IP、設置 veth pair、建路由
        └─ DEL：Pod 停止時清理
```

主流插件的定位：

| 插件       | 底層技術           | NetworkPolicy | 特點                      |
|------------|-------------------|---------------|---------------------------|
| Flannel    | vxlan overlay     | 不支援        | 最簡單，常見於測試環境    |
| Calico     | BGP / eBPF        | 支援          | 生產主流，效能好          |
| Cilium     | eBPF              | 支援（L7）    | 可做 HTTP/gRPC 層過濾     |
| Weave      | vxlan + gossip    | 支援          | 設定簡單，效能略差        |

K8s 的三條網路保證（必須同時滿足，否則不符合 spec）：

1. Pod 與 Pod 之間可以不通過 NAT 直接通信。
2. Node 與 Pod 之間可以不通過 NAT 直接通信。
3. Pod 看到自己的 IP 和別人看到它的 IP 是同一個。

第三條是關鍵——這意味著沒有「外部 IP / 內部 IP」的概念，Pod IP 就是真實路由 IP。這個設計讓服務發現簡單，但攻擊者在橫向移動時也不需要處理任何 NAT 層。

### Service DNS（服務域名）

kube-dns 或 CoreDNS 讓我們用名稱找到 Service，而不用硬編 ClusterIP：

```
<service-name>.<namespace>.svc.cluster.local
```

縮寫規則：
- 同 namespace：直接用 `<service-name>` 即可。
- 跨 namespace：`<service-name>.<namespace>`。

**攻擊視角**：拿下一個 Pod 後，直接查詢 DNS 就能枚舉 Service：

```bash
# 在被攻陷的 Pod 內
cat /etc/resolv.conf
nslookup kubernetes.default.svc.cluster.local
# 或用 curl 打其他 Service 的 DNS 名稱
curl http://api-service.production.svc.cluster.local:8080/admin
```

DNS 不受 RBAC 管控，任何 Pod 預設都能查詢 cluster 內的所有 Service 名稱。

### NetworkPolicy（網路策略）

NetworkPolicy 是 K8s 的 L3/L4 防火牆規則，但有幾個關鍵點：

1. **CNI 插件必須支援**：Flannel 預設不支援，apply 了也沒效果（後面有範例）。
2. **預設行為是全通**：沒有 NetworkPolicy 的 namespace，Pod 間流量完全不受限。
3. **規則是疊加的**：一個 Pod 可以被多個 NetworkPolicy 選中，取聯集。

規則結構：

```
podSelector → 選哪些 Pod 受這條規則管
  ├─ ingress：進來的流量（from + ports）
  └─ egress：出去的流量（to + ports）
```

selector 的維度：

- `podSelector`：用 label 選 Pod。
- `namespaceSelector`：選整個 namespace。
- `ipBlock`：CIDR 範圍（用於 egress 到外部）。

### Secret

Secret（機密物件）是 K8s 用來存放敏感資訊的物件類型：API key、資料庫密碼、TLS 憑證等。

**最重要的事：base64 不是加密**。

```bash
echo -n "supersecret" | base64
# 輸出：c3VwZXJzZWNyZXQ=

echo -n "c3VwZXJzZWNyZXQ=" | base64 -d
# 輸出：supersecret
```

etcd 預設把 Secret 以 base64 展開後的原文存入資料庫。任何能讀 etcd 的人都能拿到所有 Secret 的明文。

要啟用靜態加密（Encryption at Rest），需要在 api-server 加 `--encryption-provider-config` 並設定加密提供者（AES-CBC、AES-GCM、KMS 等）。這個設定在大多數 managed cluster（EKS、GKE、AKS）上需要額外開啟，預設不一定啟用。

**安全視角**：Ch 23 提到 `list secrets` 是危險 verb 的原因就在這——有這個權限就等於拿到 namespace 內的全部密碼。

### ConfigMap

ConfigMap（設定映射）存非敏感設定：設定檔內容、環境變數值、命令列參數。結構和 Secret 幾乎一樣，差別是：

- 值是明文存入，沒有 base64。
- 從設計意圖上就是公開的設定。

常見錯誤：把 API key 或密碼放進 ConfigMap，因為「Secret 太麻煩了」。這讓敏感資料直接暴露在 `kubectl get configmap -o yaml` 的輸出裡。

---

## 具體可跑範例

### 範例一：Secret 的兩種掛載方式

先建 Secret 物件：

```yaml
# secret-db.yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-credentials
  namespace: default
type: Opaque
data:
  username: YWRtaW4=          # base64("admin")
  password: c3VwZXJzZWNyZXQ=  # base64("supersecret")
```

```bash
kubectl apply -f secret-db.yaml
kubectl get secret db-credentials -o yaml
# data 欄位看到的是 base64，不是加密
```

**掛載方式一：環境變數（env var）**

```yaml
# pod-env.yaml
apiVersion: v1
kind: Pod
metadata:
  name: app-env
  namespace: default
spec:
  containers:
  - name: app
    image: busybox:1.36
    command: ["sh", "-c", "echo DB_USER=$DB_USER && sleep 3600"]
    env:
    - name: DB_USER
      valueFrom:
        secretKeyRef:
          name: db-credentials
          key: username
    - name: DB_PASS
      valueFrom:
        secretKeyRef:
          name: db-credentials
          key: password
```

環境變數的問題：Secret 值在 `/proc/1/environ` 裡以明文存在，子 process 會繼承，crash dump 會包含，任何能 `kubectl exec` 進這個 Pod 的人都能 `printenv` 直接讀出來。

**掛載方式二：Volume（推薦）**

```yaml
# pod-volume.yaml
apiVersion: v1
kind: Pod
metadata:
  name: app-volume
  namespace: default
spec:
  containers:
  - name: app
    image: busybox:1.36
    command: ["sh", "-c", "cat /etc/secret/password && sleep 3600"]
    volumeMounts:
    - name: secret-vol
      mountPath: /etc/secret
      readOnly: true
  volumes:
  - name: secret-vol
    secret:
      secretName: db-credentials
      defaultMode: 0400  # 只有 owner 可讀
```

```bash
kubectl apply -f pod-volume.yaml
kubectl exec app-volume -- ls -la /etc/secret/
# username  password  （各是一個檔案）
kubectl exec app-volume -- cat /etc/secret/password
# supersecret
```

Volume 掛載的好處：Secret 值透過 tmpfs 掛入，不進磁碟，Pod 重啟重新拿。壞處：`kubectl exec` 進去一樣能讀，本質上 exec 權限 = 讀 Secret 的能力。

### 範例二：NetworkPolicy default-deny

正確的起點是先封全部，再開放需要的：

```yaml
# netpol-default-deny.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: default
spec:
  podSelector: {}    # 空 selector = 選 namespace 內所有 Pod
  policyTypes:
  - Ingress
  # 沒有 ingress rules 段落 = ingress 全部封鎖
```

```bash
kubectl apply -f netpol-default-deny.yaml
kubectl get networkpolicy -n default
```

在這之後，若要讓 frontend Pod 能打 backend Pod：

```yaml
# netpol-allow-frontend.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
  namespace: default
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: frontend
    ports:
    - protocol: TCP
      port: 8080
```

**本段未實測，為理論預期行為**：apply 後 frontend Pod 能打 backend 的 8080，其他 Pod 嘗試連 backend 會被 RST/timeout。

### 範例三：沒有 CNI 支援時 NetworkPolicy 的靜默失敗

這是一個邊界/失敗範例，說明最容易踩的陷阱：

```bash
# 在 minikube 預設（Flannel CNI）環境
kubectl apply -f netpol-default-deny.yaml
# 輸出：networkpolicy.networking.k8s.io/default-deny-ingress created
# 建立成功！

kubectl get networkpolicy
# NAME                   POD-SELECTOR   AGE
# default-deny-ingress   <none>         5s
# 看得到物件！

# 但實際測試流量
kubectl run test-pod --image=busybox:1.36 --rm -it -- \
  wget -qO- http://<any-other-pod-ip>:8080
# 仍然成功！NetworkPolicy 完全無效。
```

原因：NetworkPolicy 物件是 K8s API 層的資源，建立不報錯。但實際的封包過濾由 CNI 插件執行——Flannel 的實作根本不讀 NetworkPolicy，它只負責路由，不負責過濾。你必須換用 Calico 或 Cilium，NetworkPolicy 才會生效。

判斷當前 CNI 是否支援 NetworkPolicy：

```bash
kubectl get pods -n kube-system
# 看有沒有 calico-node 或 cilium 的 DaemonSet

kubectl get daemonset -n kube-system
```

---

## 對比取捨表

### Secret 儲存方式比較

| 面向               | K8s Secret（預設）         | K8s Secret + Encryption at Rest | HashiCorp Vault / AWS Secrets Manager |
|--------------------|---------------------------|----------------------------------|----------------------------------------|
| etcd 儲存形式      | base64（=明文）            | AES-GCM / KMS 加密               | 不進 etcd，外部系統管理                |
| 金鑰管理           | 無（base64 不是加密）      | 金鑰放 api-server 設定或 KMS     | 完整 KMS 生命週期，自動輪換            |
| 存取審計           | K8s audit log             | K8s audit log                   | 完整存取日誌，支援細粒度策略           |
| 輪換機制           | 手動更新 Secret 物件       | 同左                             | 自動輪換，應用程式無感知               |
| 被 etcd 洩漏影響   | 全部明文曝光               | 加密保護（視 KMS 實作）          | 不影響（Secret 不在 etcd）            |
| 部署複雜度         | 零額外元件                 | 需要設定 api-server flag         | 需要部署 Vault 或整合雲端服務          |
| 動態 Secret        | 不支援                     | 不支援                           | 支援（如每次連線產生臨時 DB 密碼）     |
| 適合情境           | 開發測試                   | 生產最低標準                     | 高安全性生產環境                       |

結論：K8s Secret 的安全性完全取決於 etcd 的保護和 RBAC 的嚴格程度。外部密鑰管理系統（External Secrets Operator + Vault）是生產環境的正確做法。

---

## 踩雷集錦

**1. NetworkPolicy apply 成功 ≠ 生效**
最容易踩的坑。物件建立成功是 API Server 的行為，實際過濾是 CNI 的行為，兩者完全獨立。部署前先確認 CNI 是否支援，部署後用真流量測試，不要只看 `kubectl get networkpolicy`。

**2. default-deny 沒有設 egress，攻擊者仍可外連**
只設 `policyTypes: [Ingress]` 的 default-deny 不限制 egress。被攻陷的 Pod 仍可以連外部 C2 server、連 AWS metadata endpoint（169.254.169.254）、連 api-server。完整隔離需要同時設 default-deny egress，然後只開放必要的出站規則。

**3. Secret 掛成 env var，被 /proc 或 log 洩漏**
環境變數在許多場景會意外洩漏：logging library 把 env dump 到日誌、exception handler 印出 `os.environ`、某些語言的 goroutine dump 包含 env。優先用 Volume 掛載，且設 `defaultMode: 0400`。

**4. 沒有 Encryption at Rest，etcd 備份 = 所有 Secret 明文備份**
很多團隊定期備份 etcd 快照到 S3，但忘記啟用 Encryption at Rest。etcd 快照一旦洩漏，裡面所有 Secret 的值都是 base64 decode 一下就出來的。備份加密和 Encryption at Rest 是兩件不同的事。

**5. ConfigMap 裡放了敏感值**
`kubectl get configmap -o yaml` 直接印明文，沒有任何保護。除了存取控制之外，ConfigMap 在設計上就是公開的設定。常見錯誤模式：developer 圖方便把 SMTP 密碼或第三方 API key 塞進 ConfigMap 的 env 設定段落。

---

## 進階延伸

**Cilium Network Policy（L7 過濾）**：Cilium 基於 eBPF，可以設定到 HTTP 層的規則，例如「只允許 GET /api/public，禁止 POST /admin」，普通的 K8s NetworkPolicy 做不到這一點。

**External Secrets Operator**：一個 K8s operator，讓你把 Secret 的實際值存放在 Vault / AWS Secrets Manager / GCP Secret Manager，然後在 cluster 內自動同步成 K8s Secret 物件。應用程式端不需要改動，但真正的值不在 etcd 裡。

**Pod 對 etcd 的直接攻擊路徑**：預設情況下 etcd 只監聽 localhost 或特定 IP，但如果 api-server Pod 用 `hostNetwork: true` 且 etcd 綁 0.0.0.0，或者 etcd 的 TLS 憑證管控不嚴，從 Node 上的特權 Pod 有機會直接打到 etcd。

**Service Account Token 投射與 NetworkPolicy 的交叉點**：Pod 預設掛載 Service Account Token，可以打 api-server 拿 RBAC 允許的資源，包括 Secret。NetworkPolicy 通常不限制 Pod 連 api-server（10.96.0.1:443），這是一個容易被忽略的出站通道。

---

## 本章重點整理

- K8s 網路模型是扁平的，三條保證決定了「Pod IP 即路由 IP，不做 NAT」的設計；預設全通。
- CNI 插件負責實作這個網路模型，不同插件的功能集不同；Flannel 不支援 NetworkPolicy，Calico/Cilium 支援。
- NetworkPolicy 是 K8s 的 L3/L4 防火牆，但預設不存在；大多數 cluster 沒有設，攻擊者橫向移動無網路阻礙。
- NetworkPolicy 物件建立成功不代表生效，需要 CNI 配合，需要真流量測試驗證。
- Secret 的 base64 不是加密；etcd 預設明文存放；Encryption at Rest 需要額外設定。
- Secret 的兩種掛載方式：env var 方便但洩漏面大，Volume 掛載（tmpfs）是生產首選。
- 有 `get/list secrets` RBAC 權限的人等同於能讀所有 Secret 明文。
- 生產環境的正確做法：Encryption at Rest + 外部密鑰管理（Vault / Secrets Manager）+ NetworkPolicy default-deny。

---

## 自我檢核

1. K8s 網路的三條保證是哪三條？它們對攻擊面的含義是什麼？
2. 在 minikube（Flannel）上 apply NetworkPolicy 會發生什麼？如何驗證 NetworkPolicy 是否真正生效？
3. `echo -n "mysecret" | base64` 的輸出是加密還是編碼？差別是什麼？
4. Secret 的 env var 掛載和 Volume 掛載各有什麼洩漏風險？
5. 一個攻擊者拿下 namespace 內的任意 Pod 後，要怎麼用 DNS 找到其他 Service？
6. 為什麼 etcd 備份加密和 Encryption at Rest 是兩件不同的事？

---

## 延伸閱讀

1. [Kubernetes Network Policies — 官方文件](https://kubernetes.io/docs/concepts/services-networking/network-policies/)：最完整的 NetworkPolicy spec 說明，包含 selector 組合的邊界行為。
2. [Encrypting Confidential Data at Rest — 官方文件](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)：逐步設定 `--encryption-provider-config`，包含 KMS provider 的整合方式。
3. [Cilium Network Policy 文件](https://docs.cilium.io/en/stable/network/kubernetes/policy/)：L7 過濾規則的實際寫法，以及 Cilium 相對於 NetworkPolicy spec 的擴充能力。
4. [External Secrets Operator](https://external-secrets.io/latest/)：把 K8s Secret 後端換成 Vault/AWS/GCP 的 operator，含完整的安裝與設定範例。
5. [CNCF Security Whitepaper — Secrets Management](https://github.com/cncf/tag-security/blob/main/security-whitepaper/v2/cloud-native-security-whitepaper.md)：從雲原生整體視角看機密管理的威脅模型與最佳實踐。

---

網路和機密是 K8s 橫向移動與提權的主要通道——扁平網路讓移動沒有阻力，base64 Secret 讓憑證唾手可得。下一章要把視角切換到攻擊者，系統性地找出 cluster 的暴露面：未受保護的 API Server anonymous 存取、kubelet 的 `/exec` endpoint、以及 etcd 直接可達的情境。

→ [Ch 25 — K8s 偵察與暴露面：anonymous API / kubelet / etcd](./25-k8s-recon.md)
