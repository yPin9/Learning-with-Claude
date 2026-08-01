# Ch 12 — 密鑰與 Secrets：KMS / Secrets Manager / 洩漏面

> **目標**：理解 AWS 的密鑰管理體系（KMS、Secrets Manager、SSM Parameter Store），掌握 secret 的各種洩漏路徑，以及攻擊者拿到解密權限後能做什麼。

---

## 為什麼需要

密鑰（secret）是雲端環境裡攻擊者最想要的東西——拿到 DB 密碼就能讀整張資料表，拿到 API key 就能呼叫第三方服務，拿到加密金鑰（encryption key）就能解密所有靜態資料（data at rest）。

問題不在「要不要加密」，在「密鑰本身放哪裡、誰能拿、怎麼審計」。傳統做法是把密碼寫進設定檔、環境變數、甚至原始碼，這在雲端規模下幾乎必漏。AWS 提供三層方案：

- **KMS（Key Management Service）**：管理加密金鑰本身，不管應用層 secret
- **Secrets Manager**：管理應用層 secret（DB 密碼、API key），底層用 KMS 加密
- **SSM Parameter Store（Systems Manager Parameter Store）**：輕量版 secret store，兼管設定值

---

## 先建直覺

先把三者的定位弄清楚，再看機制：

```
┌─────────────────────────────────────────────────────────────┐
│                   你的應用想存一個 DB 密碼                   │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────▼──────────────┐
          │   Secrets Manager / SSM     │  ← 你操作這層
          │   把 secret 存進來          │
          │   (明文 secret 由你提供)    │
          └──────────────┬──────────────┘
                         │  要加密，呼叫 KMS
          ┌──────────────▼──────────────┐
          │          KMS CMK            │  ← AWS 幫你管金鑰
          │   (Customer Managed Key)    │
          │   加密 secret 後存 AWS 後端 │
          └─────────────────────────────┘

讀取時反向：
  應用 → secretsmanager:GetSecretValue
       → Secrets Manager 呼叫 kms:Decrypt
       → 拿回明文 secret → 應用拿到 DB 密碼
```

關鍵點：**應用程式不直接呼叫 KMS 解密 secret，Secrets Manager 代呼叫**。但應用的 IAM role 必須同時有 `secretsmanager:GetSecretValue` 和 `kms:Decrypt`（在那把 CMK 上），兩個缺一不可。

---

## 底層機制

### KMS 金鑰類型

AWS KMS 有兩種主要金鑰：

**AWS Managed Key（AWS 託管金鑰）**
- 格式：`aws/s3`、`aws/secretsmanager`、`aws/ebs` 等
- 由 AWS 自動建立、自動輪替（每年）
- 你**無法修改** key policy，也看不到細節
- 攻擊者角度：只要有對應服務的 IAM 權限，自動能用，不需額外 KMS 授權

**CMK（Customer Managed Key，客戶自管金鑰）**
- 你自己建立：`aws kms create-key`
- 有完整的 **key policy（金鑰政策）**，你全控
- 可設定輪替、可封存（disable）、可刪除（schedule deletion，最快 7 天）
- 攻擊者拿到 CMK 的 `kms:Decrypt` 就能解密所有用它加密的資料

```bash
# 建立 CMK，注意 key policy 是 JSON
aws kms create-key \
  --description "prod-db-key" \
  --key-usage ENCRYPT_DECRYPT \
  --region ap-northeast-1

# 輸出（節錄）
# {
#   "KeyMetadata": {
#     "KeyId": "aaaa1111-bbbb-2222-cccc-333344445555",
#     "Arn": "arn:aws:kms:ap-northeast-1:123456789012:key/aaaa1111-...",
#     "KeyState": "Enabled"
#   }
# }
```

### KMS Key Policy：雙層授權

KMS 的授權和一般 IAM policy 不同，它是 **resource-based policy（資源型政策）** 直接附在金鑰上。

**雙層規則**：要能用一把 CMK，需要同時通過：
1. **Key policy** 允許這個 principal（主體）執行該 action
2. **IAM policy** 也允許該 action（如果 key policy 有 `Enable IAM User Permissions` 那段，才讓 IAM policy 介入）

```json
// key policy 最小範例（允許 admin 管理，允許 app role 使用）
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Enable IAM User Permissions",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:root"
      },
      "Action": "kms:*",
      "Resource": "*"
    },
    {
      "Sid": "Allow key administration",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:role/KeyAdminRole"
      },
      "Action": [
        "kms:Create*", "kms:Describe*", "kms:Enable*",
        "kms:List*", "kms:Put*", "kms:Update*", "kms:Revoke*",
        "kms:Disable*", "kms:Delete*", "kms:ScheduleKeyDeletion"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Allow key usage",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:role/AppRole"
      },
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    }
  ]
}
```

**加解密權限分離**要點：`KeyAdminRole` 只能管金鑰（enable/disable/delete），**不能** decrypt；`AppRole` 只能 decrypt，**不能**改 key policy。這樣就算應用被打穿，攻擊者也無法刪金鑰或轉移控制權。

### Secrets Manager：存讀輪替

**存 secret**

```bash
aws secretsmanager create-secret \
  --name "prod/myapp/db-password" \
  --secret-string '{"username":"admin","password":"S3cr3t!"}' \
  --kms-key-id "aaaa1111-bbbb-2222-cccc-333344445555" \
  --region ap-northeast-1

# 輸出
# {
#   "ARN": "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:prod/myapp/db-password-AbCdEf",
#   "Name": "prod/myapp/db-password",
#   "VersionId": "f3b1a2c3-..."
# }
```

**讀 secret（應用端）**

```bash
aws secretsmanager get-secret-value \
  --secret-id "prod/myapp/db-password" \
  --region ap-northeast-1

# SecretString 欄位是明文 JSON
# {"username":"admin","password":"S3cr3t!"}
```

**版本管理（版本標籤）**

每次更新 secret 產生新版本，預設標籤是 `AWSCURRENT`（當前版本）和 `AWSPREVIOUS`（前版）。輪替中有臨時標籤 `AWSPENDING`。

```bash
# 讀取特定版本
aws secretsmanager get-secret-value \
  --secret-id "prod/myapp/db-password" \
  --version-stage AWSPREVIOUS
```

**自動輪替（Rotation Lambda）**

Secrets Manager 可以掛一個 Lambda（輪替函式，rotation function）自動更新 secret：

```
定時觸發（EventBridge）
    → Secrets Manager 呼叫 Rotation Lambda
    → Lambda 1) 在 DB 建新密碼  2) 把新密碼寫進 AWSPENDING
    → 測試新密碼能連 DB
    → 把 AWSPENDING 換成 AWSCURRENT
```

啟用輪替：

```bash
aws secretsmanager rotate-secret \
  --secret-id "prod/myapp/db-password" \
  --rotation-lambda-arn "arn:aws:lambda:ap-northeast-1:123456789012:function:MyRotationLambda" \
  --rotation-rules AutomaticallyAfterDays=30
```

### SSM Parameter Store：輕量 Secret

Parameter Store 分兩種類型：

| 類型 | 用途 | 加密 |
|------|------|------|
| String | 普通設定值、不敏感資料 | 無 |
| SecureString | 密碼、token 等敏感值 | KMS 加密 |

```bash
# 存 SecureString
aws ssm put-parameter \
  --name "/prod/myapp/db-password" \
  --value "S3cr3t!" \
  --type SecureString \
  --key-id "aaaa1111-bbbb-2222-cccc-333344445555"

# 讀取（--with-decryption 才會解密）
aws ssm get-parameter \
  --name "/prod/myapp/db-password" \
  --with-decryption

# 批次讀取（注意是複數 get-parameters）
aws ssm get-parameters \
  --names "/prod/myapp/db-password" "/prod/myapp/api-key" \
  --with-decryption
```

`--with-decryption` 不加就拿到 ciphertext（密文），加了才是明文。IAM policy 需要 `ssm:GetParameter` 和對應 KMS key 的 `kms:Decrypt`。

---

## 對比取捨表

| 面向 | KMS CMK | Secrets Manager | SSM SecureString |
|------|---------|-----------------|-----------------|
| 用途 | 金鑰本身 | 應用層 secret | 輕量 secret/設定 |
| 費用 | $1/月/key + API 費 | $0.40/月/secret | 免費（標準層） |
| 自動輪替 | 金鑰輪替（envelope） | 支援（需 Lambda） | 不支援 |
| 版本控制 | 不適用 | 有（label 機制） | 有（version） |
| 跨帳號存取 | key policy 可授權 | resource policy | 不支援 |
| 適用場景 | 底層加密 | DB 密碼、API key | 環境設定、flag |

---

## 踩雷集錦

**1. Key policy 沒有 root principal，把自己鎖出去**

建立 CMK 時如果 key policy 完全自訂，忘記加 `"Principal": {"AWS": "arn:aws:iam::123456789012:root"}` 這條，結果 KeyAdminRole 被刪除後，整把 key 永久無人能管。AWS 這時只能透過 support case 救援，且只在特定條件下才救得了。

**2. Secrets Manager ARN 帶隨機後綴，wildcard 要小心**

secret ARN 格式是 `...secret:prod/myapp/db-password-AbCdEf`，那六字元後綴是隨機的。IAM policy 寫 `arn:aws:secretsmanager:*:123456789012:secret:prod/myapp/db-password` 會比對失敗，要加 `-*`：

```json
"Resource": "arn:aws:secretsmanager:*:123456789012:secret:prod/myapp/db-password-*"
```

**3. SSM GetParameter vs GetParameters 是不同 IAM action**

`ssm:GetParameter`（單數）和 `ssm:GetParameters`（複數）是兩個獨立的 IAM action。只給一個不給另一個，應用就會報 AccessDenied，而且錯誤訊息不會說是哪個 action 缺，新手常卡在這裡。

**4. Secrets Manager 輪替失敗後 AWSCURRENT 仍是舊密碼**

輪替 Lambda 執行失敗（DB 連線錯、權限不夠）時，Secrets Manager 會把 `AWSPENDING` 回退，`AWSCURRENT` 保持不變。問題是 Lambda 可能已經在 DB 改了密碼卻沒寫回 secret，造成 secret 和 DB 密碼不一致，所有應用同時斷線。要在 Lambda 裡做好 rollback 邏輯。

**5. CMK disable 不等於刪除，但 schedule deletion 後 7 天才生效**

`kms:DisableKey` 後加密的資料立刻無法解密，但 key 還在，可以 re-enable。`ScheduleKeyDeletion` 最短等待期是 7 天，期間可取消。正式環境刪 key 前要確認沒有資料還在用它，否則資料永久無法解密。

---

## 進階延伸

### 攻擊者拿到 kms:Decrypt 能做什麼

`kms:Decrypt` 是 KMS 裡破壞力最大的單一權限。以 CMK 加密的資源為例：

```
攻擊者拿到 AppRole（有 kms:Decrypt on prod-cmk）

prod-cmk 加密的所有資料：
  ├── Secrets Manager secrets → 直接 GetSecretValue 拿 DB 密碼
  ├── S3 物件（SSE-KMS）     → GetObject 下載後自動解密
  ├── EBS 卷（encrypted）    → attach 到自己的 EC2 後 mount 讀取
  └── RDS 快照（encrypted）  → restore 到自己帳號內的 RDS
```

**本段 EBS/RDS 跨帳號 restore 部分未實測，為理論預期行為。** 自驗方法：建立測試帳號，分享加密 EBS snapshot，確認目標帳號能否 restore。

實際攻擊演示（在有 kms:Decrypt 的 role 下）：

```bash
# 直接拿 Secrets Manager 的 secret
aws secretsmanager get-secret-value \
  --secret-id "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:prod/db-password-XxXxXx"

# 列出所有 secret（需要 secretsmanager:ListSecrets）
aws secretsmanager list-secrets --region ap-northeast-1

# 解密 S3 SSE-KMS 物件（下載時自動解密）
aws s3 cp s3://prod-bucket-123456789012/config/database.conf ./
```

### CloudTrail 監控 Decrypt 操作

KMS 的每一次 `Decrypt` 呼叫都會記錄在 CloudTrail（雲端審計日誌）。防禦方要建 metric filter 和 alarm：

```bash
# 找出誰在 decrypt（CloudTrail log insight query）
# 在 CloudWatch Log Insights 執行：
# fields @timestamp, userIdentity.arn, requestParameters.keyId, sourceIPAddress
# | filter eventName = "Decrypt"
# | filter requestParameters.keyId like "aaaa1111-bbbb-2222"
# | sort @timestamp desc
# | limit 50
```

異常訊號：非工作時間大量 Decrypt、來自非預期 IP、非預期的 principal。

---

## Secret 洩漏面全景

這是攻擊者偵察（reconnaissance）階段最先翻的地方：

**1. 程式碼 / Git 硬編碼**
最常見，`git log -p` 能翻出歷史提交，刪掉的密碼在歷史裡還在。工具：`trufflehog`、`gitleaks`。

**2. S3 公開 bucket 裡的設定檔**
CI/CD 把 `.env`、`application.yml` 推進 S3，bucket 設成公開就全洩漏。`aws s3 ls s3://bucket/ --no-sign-request` 能列出無需認證的 bucket。

**3. CloudFormation Output（堆疊輸出）**
Output 欄位常被拿來傳值，例如 `DBPassword: !Ref DatabasePassword`。任何有 `cloudformation:DescribeStacks` 的 IAM principal 都能讀到：

```bash
aws cloudformation describe-stacks \
  --stack-name MyProdStack \
  --query "Stacks[0].Outputs"
# 輸出可能含明文密碼
```

防禦：Output 只放 ARN，不放 secret 值本身。

**4. Terraform State（狀態檔）**

Terraform 的 `terraform.tfstate` 是明文 JSON，即使 output 標記了 `sensitive = true`，state 檔裡仍是明文：

```json
// terraform.tfstate 節錄
"outputs": {
  "db_password": {
    "value": "S3cr3t!",     // sensitive=true 也是明文
    "type": "string",
    "sensitive": true
  }
}
```

State 檔存 S3 時要啟用 server-side encryption，並嚴格限制 bucket policy。

**5. Lambda 環境變數**

承 Ch 11：環境變數明文存在 Lambda 設定，`GetFunctionConfiguration` 可讀出。應改用 Secrets Manager。

**6. EC2 User Data（啟動腳本）**

Bootstrap 腳本（啟動資料）裡常見：

```bash
#!/bin/bash
# 這是 EC2 user-data，任何能呼叫 ec2:DescribeInstanceAttribute 的人都能看
export DB_PASSWORD="S3cr3t!"
mysql -h prod-db.example.com -u admin -pS3cr3t! -e "..."
```

讀取方式（不需登入 EC2）：

```bash
aws ec2 describe-instance-attribute \
  --instance-id i-0123456789abcdef0 \
  --attribute userData \
  --query "UserData.Value" \
  --output text | base64 -d
```

EC2 內部也能讀（metadata 端點）：

```bash
# 在 EC2 內執行
curl http://169.254.169.254/latest/user-data
```

---

## 本章重點整理

- KMS 分 AWS Managed Key 和 CMK；CMK 有完整 key policy，攻擊/防禦都以 CMK 為主
- Key policy 是 resource-based policy，和 IAM policy 是**雙層AND**關係，缺一不可
- 加解密權限分離：admin role 管金鑰生命週期，app role 只能 decrypt
- Secrets Manager 適合 DB 密碼/API key，支援自動輪替；SSM SecureString 適合設定值
- `secretsmanager:GetSecretValue` + `kms:Decrypt` 是完整讀取 secret 所需的最小權限組合
- `kms:Decrypt` 一旦被拿走，對應 CMK 加密的 S3/EBS/RDS/Secrets 全部暴露
- Secret 洩漏六大面：git 歷史、S3 公開設定檔、CloudFormation Output、Terraform state、Lambda 環境變數、EC2 user-data
- CloudTrail 記錄每次 KMS Decrypt，是偵測異常解密的主要來源

---

## 自我檢核

- [ ] 我能說清楚 KMS key policy 和 IAM policy 的雙層授權如何互動
- [ ] 我知道 AWS Managed Key 和 CMK 的差異，以及攻擊者在意哪個
- [ ] 我能說出 Secrets Manager 讀取 secret 需要哪兩個 IAM action
- [ ] 我知道 Terraform state 的 `sensitive = true` 無法真正遮蔽 secret
- [ ] 我能說出 EC2 user-data 如何在不登入機器的情況下被讀取
- [ ] 我知道拿到 `kms:Decrypt` 後能繞過哪些加密保護
- [ ] 我能說出 Secrets Manager secret ARN 帶隨機後綴對 IAM policy 的影響

---

## 延伸閱讀

1. **AWS KMS Key Policy 官方文件**
   搜尋：`AWS KMS key policy best practices`
   URL：https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
   為什麼讀：key policy 語法和 IAM policy 看起來像但有細節差異，這份文件有所有 condition key 和 SID 範例。

2. **Hacking the Cloud - AWS KMS**
   URL：https://hackingthe.cloud/aws/exploitation/kms_key/
   為什麼讀：攻擊者視角，描述拿到 Decrypt 後的橫向移動路徑，配合本章看效果最好。

3. **truffleHog GitHub**
   URL：https://github.com/trufflesecurity/trufflehog
   為什麼讀：掃 git 歷史的主流工具，了解它能找什麼就知道開發者最常犯哪些錯。

4. **Terraform Sensitive Values in State**
   搜尋：`terraform sensitive output state file plaintext`
   URL：https://developer.hashicorp.com/terraform/language/values/outputs#sensitive-suppressing-values-in-cli-output
   為什麼讀：官方承認 sensitive 只遮 CLI 輸出，state 還是明文，這是很多人不知道的事實。

5. **CloudTrail KMS Event Reference**
   URL：https://docs.aws.amazon.com/kms/latest/developerguide/logging-using-cloudtrail.html
   為什麼讀：知道 Decrypt 事件長什麼樣，才能寫出有效的 CloudWatch alert rule。

---

Ch 13 進入網路層——我們會看 VPC（虛擬私有雲）、security group（安全群組）的實際工作方式，以及攻擊者在雲端內網如何橫向移動。
→ [Ch 13 — 雲端網路：VPC / Security Group / 橫向移動](13-cloud-networking.md)
