# Ch 34 — 雲端防禦基本功：least privilege 落地、SCP、CSPM

> **目標**：從攻擊者視角反推哪些防禦能真正斷鏈；掌握 IAM Access Analyzer、CloudTrail-driven 縮權、SCP 封鎖危險 action 的具體操作；理解 CSPM 工具的定位與取捨；建立一套可落地的防禦優先序。

---

## 為什麼需要

前 33 章一路打下來，攻擊鏈的每一步都用了「正常的 AWS 功能」：API 呼叫合法、憑證格式正確、沒有任何傳統意義上的「漏洞」。這是雲端防禦最讓人頭疼的地方——傳統 IDS 看不到「攻擊」，看到的是 `iam:CreateUser` 和 `sts:AssumeRole`，和正常管理員操作一模一樣。

要擋住這條鏈，根本方法只有一個：**讓攻擊者拿到的權限不夠用**。把它做到位，你不需要完美的偵測，攻擊者自然無法前進。

---

## 先建直覺：攻擊鏈最脆弱的三個節點

從已學的攻擊章反推，找出「一個防禦能同時封住哪些鏈」。

```
攻擊鏈（簡化版）

 外部                SSRF          IMDS          IAM           後門
─────────────────────────────────────────────────────────────────────
 惡意請求 ──► web app ──► 打 169.254.169.254 ──► 拿 role key ──► CreateUser
                                                     │              │
                防禦節點 A ────────────────────────►│              │
                （IMDSv2 + 最小化 role 權限）         │              │
                                                     │              │
                防禦節點 B ──────────────────────────────────────►│
                （SCP deny CreateUser）                             │
                                                                    │
                防禦節點 C ──────────────────────────────────────►│
                （CSPM 持續掃 + Config rules 即時告警）             │
```

**節點 A（Ch 10 SSRF 鏈）**：role 的 IAM policy 如果只有 `s3:GetObject` 一個權限，攻擊者拿到 key 也只能讀那個 bucket，什麼都做不了。

**節點 B（Ch 14 持久化）**：SCP 在組織層封掉 `iam:CreateUser`，即使攻擊者已拿到帳號內的 admin，也無法建新 IAM user 留後門。

**節點 C（Ch 7 提權）**：CSPM 掃到某個 role 有 `iam:PassRole` + `lambda:CreateFunction`，標記為高風險 finding，在攻擊者發現前先修掉。

三個節點對應三個工具：**最小權限（Access Analyzer + CloudTrail）**、**SCP**、**CSPM**。這章逐一拆開。

---

## 最小權限怎麼落地

說「給最小必要權限」很容易，難的是**知道哪些權限是必要的**。沒人能記清楚一個服務到底用了哪些 API，直接從 CloudTrail 事實出發才是務實做法。

### IAM Access Analyzer

AWS IAM Access Analyzer（存取分析器）做兩件事，兩件都有用但常被混為一談：

1. **外部存取分析**：掃描哪些資源（S3 bucket、KMS key、IAM role trust policy）允許帳號 **外部** 的 principal 存取。這就是 Ch 8 confused deputy 的偵測手段。

2. **IAM policy 驗證**：送一份 policy JSON 進去，它用 automated reasoning 分析是否過於寬鬆、是否有語法錯誤、是否符合 best practice。

Access Analyzer 有個關鍵限制：它看的是**能不能從外部進來**，對於帳號內部的過度權限（一個 Lambda role 有 `iam:*`）它預設不看。要覆蓋內部過度權限，得靠接下來的 lastAccessedTime 流程。

**啟用 Access Analyzer（CLI）：**

```bash
# 每個 region 要分別啟用，以 ap-northeast-1 為例
aws accessanalyzer create-analyzer \
  --analyzer-name prod-external-analyzer \
  --type ACCOUNT \
  --region ap-northeast-1

# 列出現有 analyzer
aws accessanalyzer list-analyzers --region ap-northeast-1

# 列出目前的 findings（外部存取）
aws accessanalyzer list-findings \
  --analyzer-arn arn:aws:access-analyzer:ap-northeast-1:123456789012:analyzer/prod-external-analyzer \
  --region ap-northeast-1 \
  --query 'findings[*].{ID:id,Resource:resource,Status:status}' \
  --output table
```

**本段未實測，為理論預期行為**。自驗方法：在測試帳號建一個 S3 bucket，設 policy 允許另一帳號讀取，等 15 分鐘後執行 `list-findings`，確認出現該 bucket 的 finding。

### 用 lastAccessedTime 縮權

縮減 IAM 權限的正確流程是：**先看實際用了什麼，再縮到那個範圍**，而不是猜。

```bash
# 步驟 1：產生 service last accessed report
# 傳入要分析的 IAM entity ARN（user, role, group, policy 都行）
aws iam generate-service-last-accessed-details \
  --arn arn:aws:iam::123456789012:role/my-lambda-role

# 回傳一個 JobId，例如：
# { "JobId": "a1b2c3d4-5678-90ab-cdef-example11111" }

# 步驟 2：查詢結果（可能要等幾秒到幾分鐘）
aws iam get-service-last-accessed-details \
  --job-id a1b2c3d4-5678-90ab-cdef-example11111 \
  --query 'ServicesLastAccessed[*].{Service:ServiceName,LastAccess:LastAuthenticated,Region:Region}' \
  --output table
```

輸出範例（部分）：

```
----------------------------------------------------------------------
|                     GetServiceLastAccessedDetails                  |
+---------------------------+--------------------+-------------------+
|         Service           |     LastAccess     |      Region       |
+---------------------------+--------------------+-------------------+
|  Amazon EC2               |  2025-12-01T08:23  |  ap-northeast-1   |
|  AWS IAM                  |  None              |  N/A              |
|  Amazon S3                |  2025-11-30T14:10  |  N/A              |
|  AWS Lambda               |  None              |  N/A              |
+---------------------------+--------------------+-------------------+
```

IAM 和 Lambda 的 `LastAccess` 是 `None`——這個 role 從來沒用過這兩個服務的 API，但 policy 裡卻開了，就是該砍的候選。

### Before / After：AdministratorAccess 縮到 least-privilege

**之前（典型的懶人做法）：**

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

這是 `AdministratorAccess` 的內容，它掛在 Lambda role 上是一個高風險配置（Ch 7 說的 PassRole 提權的完美目標）。

**之後（根據 lastAccessedTime 縮減）：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3ReadOnly",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::my-data-bucket",
        "arn:aws:s3:::my-data-bucket/*"
      ]
    },
    {
      "Sid": "EC2DescribeOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LogsWrite",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:ap-northeast-1:123456789012:log-group:/aws/lambda/*"
    }
  ]
}
```

差別在哪：
- `iam:*`、`lambda:*`、`sts:*` 全砍——拿到這個 key，Ch 7 的任何提權路徑都走不通
- S3 限定到具體 bucket ARN，不是 `*`
- EC2 只有 Describe，不能建立或修改
- CloudWatch Logs 資源限定到這個 Lambda 的 log group prefix

---

## SCP：組織層防護欄

SCP（Service Control Policy，服務控制策略）不是給帳號設的——它是給 **AWS Organizations OU（組織單元）** 設的，在 IAM policy 之外加一層「天花板」。

### 架構圖

```
AWS Organizations
│
├── Management Account（根帳號，不建議跑業務）
│       │
│       └── 套用 SCP（生效範圍：所有 member account）
│
├── OU: Production
│       │
│       ├── 套用 SCP（生效範圍：Production OU 下所有帳號）
│       │
│       ├── Account: prod-web
│       └── Account: prod-data
│
└── OU: Development
        │
        ├── 套用 SCP（生效範圍：Dev OU 下所有帳號）
        │
        ├── Account: dev-alice
        └── Account: dev-bob
```

SCP 可以在多個層套用，效果是 AND（交集）：member account 能做的事 = IAM policy 允許 AND SCP 允許。只要任何一層的 SCP Deny，就算 IAM policy Allow 也過不了。

**關鍵例外**：Management Account（根帳號）的動作**不受 SCP 限制**。這是 AWS 設計，後面踩雷集錦詳述。

### SCP 評估順序

```
請求進來
    │
    ▼
SCP 有 Explicit Deny？ ──Yes──► 拒絕（結束）
    │ No
    ▼
IAM policy 有 Explicit Deny？ ──Yes──► 拒絕（結束）
    │ No
    ▼
SCP 有 Allow？ ──No──► 拒絕（結束，SCP 預設 Deny Everything 除了明確 Allow）
    │ Yes
    ▼
IAM policy 有 Allow？ ──No──► 拒絕（結束）
    │ Yes
    ▼
允許
```

如果你的 Organizations 用的是 **deny list 策略**（預設掛一個 `FullAWSAccess` SCP，再加 deny 規則），記得不是「SCP 沒 Deny 就行」——你還是得確認帳號層 IAM policy 有對應的 Allow。

### 三個實用 SCP 範例

**1. 封鎖關掉 CloudTrail（防 Ch 15 的日誌規避）：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyStopLogging",
      "Effect": "Deny",
      "Action": [
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail",
        "cloudtrail:UpdateTrail",
        "cloudtrail:PutEventSelectors"
      ],
      "Resource": "*"
    }
  ]
}
```

Ch 15 說攻擊者第一件事就是想關掉 CloudTrail，這個 SCP 讓他做不到——即使他已拿到帳號內的 admin 權限。`PutEventSelectors` 也一起封，否則攻擊者可以縮小 trail 的記錄範圍來規避。

**2. 封鎖直接建立 IAM user（防 Ch 14 持久化）：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyCreateIAMUser",
      "Effect": "Deny",
      "Action": [
        "iam:CreateUser",
        "iam:CreateAccessKey"
      ],
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:PrincipalArn": [
            "arn:aws:iam::123456789012:role/BreakGlassAdmin"
          ]
        }
      }
    }
  ]
}
```

`Condition` 加了白名單：`BreakGlassAdmin` 這個緊急 role 例外，其他所有人都不能建 IAM user。注意 `iam:CreateAccessKey` 也封了，否則攻擊者可以拿現有 user 多建一組 key 留後門（Ch 14 的手法之一）。

**3. 鎖定允許的 region（縮小爆炸半徑）：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyNonApprovedRegions",
      "Effect": "Deny",
      "NotAction": [
        "cloudfront:*",
        "iam:*",
        "route53:*",
        "support:*",
        "budgets:*",
        "waf:*",
        "organizations:*",
        "sts:*"
      ],
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:RequestedRegion": [
            "ap-northeast-1",
            "us-east-1"
          ]
        }
      }
    }
  ]
}
```

注意 `NotAction` 而非 `Action`：全球性服務（IAM、CloudFront、Route 53、STS 等）不受 region 過濾，只有 region-based 服務才封。`us-east-1` 通常要開，因為很多 AWS 服務的控制面跑在那裡。

攻擊者在你帳號裡建資源時，如果他選了 eu-west-1，SCP 直接擋掉——即使他有完整的 IAM 權限。

---

## CSPM：持續掃態勢的防線

CSPM（Cloud Security Posture Management，雲端安全態勢管理）做的事是：**持續監控你的雲端資源設定，對比 security best practice，把偏差找出來**。

它不是入侵偵測（那是 GuardDuty / Falco 的事），它管的是「你的設定本身有沒有問題」。

### 核心能力

- 掃公開 S3 bucket（Ch 9 的攻擊面）
- 找過度寬鬆的 IAM policy（`Action: *`、`Resource: *`）
- 找安全群組的 `0.0.0.0/0` 開口
- 掃未加密的 RDS snapshot
- 找沒開 MFA 的 IAM user
- 追蹤 CIS AWS Foundations Benchmark 合規狀態

### 工具對比

| 工具 | 類型 | 授權 | 優點 | 缺點 |
|------|------|------|------|------|
| Prowler | 開源 CLI | Apache 2.0 | 覆蓋最廣（500+ check）、支援多雲、輸出多格式 | 無 GUI、finding 數量龐大難消化 |
| ScoutSuite | 開源 CLI | GPL 2.0 | HTML 報告清晰、多雲支援 | 更新頻率不如 Prowler |
| AWS Security Hub | AWS 原生 | 付費（依 finding 計費）| 與 GuardDuty/Inspector 整合、集中 dashboard | 只有 AWS、需額外整合才好用 |
| Wiz | 商用 SaaS | 付費 | 圖形化攻擊路徑、連 K8s + 雲 IAM | 貴、小團隊難 justify |
| Orca Security | 商用 SaaS | 付費 | 無 agent、快速部署 | 同上 |

個人/小團隊：先跑 Prowler，免費、立刻上手。企業：Security Hub 聚合多服務 finding，再考慮 Wiz 看攻擊路徑。

### Prowler 快速一跑

```bash
# 安裝（Python 3.9+）
pip install prowler

# 確認 AWS 憑證已設定（假設已 aws configure 或有 IAM role）
aws sts get-caller-identity

# 跑 AWS 全套 check，輸出 JSON 到 output/ 目錄
# -M json 指定 JSON 格式，-o output/ 指定輸出目錄
prowler aws -M json -o output/

# 只跑特定 category（快很多）
# category: iam / s3 / ec2 / logging / encryption
prowler aws --categories iam s3 -M json -o output/

# 針對特定 region
prowler aws --region ap-northeast-1 --categories iam -M json -o output/

# 只看 FAIL 的 finding（過濾掉 PASS）
prowler aws --status FAIL -M json -o output/
```

**本段未實測，為理論預期行為**。自驗方法：在 AWS 沙盒帳號建一個沒有 MFA 的 IAM user，跑 `prowler aws --categories iam --status FAIL`，確認出現 `iam_user_mfa_enabled_console_access` check 的 FAIL 項目。

Prowler 一次可能輸出幾百個 finding，不是要你全修。找 severity CRITICAL 和 HIGH 先處理，LOW 可以紀錄後續。

### AWS Config Rules

AWS Config 的機制是：每次資源**建立或修改**，Config 都記錄這個變更，並觸發 Config rule 評估。

```
資源變更事件
      │
      ▼
 AWS Config 記錄變更快照
      │
      ▼
 觸發 Config Rule 評估
      │
      ├── COMPLIANT  ─► 記錄結果，沒事
      └── NON_COMPLIANT ─► 記錄結果 + 可選觸發 SNS 告警 / Auto Remediation
```

兩種 rule：
- **Managed rule**：AWS 預建的，直接用。例如 `s3-bucket-public-read-prohibited`、`iam-root-access-key-check`、`cloudtrail-enabled`。
- **Custom rule**：你寫 Lambda 函數，AWS Config 把資源設定傳進去，Lambda 回傳合規與否。

**啟用 `s3-bucket-public-read-prohibited` managed rule：**

```bash
# 啟用一條 managed Config rule
aws configservice put-config-rule \
  --config-rule '{
    "ConfigRuleName": "s3-bucket-public-read-prohibited",
    "Source": {
      "Owner": "AWS",
      "SourceIdentifier": "S3_BUCKET_PUBLIC_READ_PROHIBITED"
    },
    "Scope": {
      "ComplianceResourceTypes": ["AWS::S3::Bucket"]
    }
  }' \
  --region ap-northeast-1

# 手動觸發評估
aws configservice start-config-rules-evaluation \
  --config-rule-names s3-bucket-public-read-prohibited \
  --region ap-northeast-1

# 查詢評估結果
aws configservice get-compliance-details-by-config-rule \
  --config-rule-name s3-bucket-public-read-prohibited \
  --compliance-types NON_COMPLIANT \
  --region ap-northeast-1 \
  --query 'EvaluationResults[*].{Resource:EvaluationResultIdentifier.EvaluationResultQualifier.ResourceId,Annotation:Annotation}'
```

**本段未實測，為理論預期行為**。自驗方法：建一個 S3 bucket 設 `Block Public Access: Off`，啟用這條 rule 後手動觸發評估，確認該 bucket 出現在 NON_COMPLIANT 結果中。

---

## 防禦優先序：給自己一個決策框架

資源有限的情況下，要有排序。根據 ROI（投入 / 攻擊面縮減）：

### 優先序與攻擊章映射表

| 優先 | 防禦措施 | 斷掉哪些攻擊鏈 | 成本 |
|------|----------|--------------|------|
| 1 | IAM least privilege（縮到最小） | Ch 7 提權、Ch 10 SSRF 橫向、Ch 14 持久化 | 低（時間成本）|
| 2 | SCP deny 危險 action | Ch 14 持久化、Ch 15 日誌規避 | 低（JSON 設定）|
| 3 | 網路隔離（VPC endpoint、SG 精確化） | Ch 10 SSRF、Ch 13 橫向移動 | 中 |
| 4 | CSPM 持續掃（Prowler / Security Hub） | 所有 misconfig 類攻擊 | 低-中 |
| 5 | Runtime detection（GuardDuty / Falco） | 異常行為偵測，覆蓋攻擊者已在帳號內的情境 | 中-高 |
| 6 | MFA everywhere + IAM Identity Center | 憑證竊取後的橫向移動 | 低 |

**最高 ROI 永遠是 IAM**。修一個 over-permissive role 的成本是 5 分鐘，換來的是讓攻擊者在拿到 key 後什麼都做不了。網路隔離和 runtime detection 是第二層，但如果 IAM 沒修好，偵測到異常行為也只是「慢動作看著被打穿」。

**斷鏈 vs 偵測的選擇**：防禦 > 偵測。能用 SCP 直接擋掉 `iam:CreateUser` 就不要靠 CloudTrail alert 說「有人建了 user」——告警永遠有延遲，有延遲就有損失窗口。

---

## 踩雷集錦

**1. SCP 不擋 Management Account**

AWS 文件明確說：SCP 不適用於 Management Account 本身。你在根層套了一個封鎖 `cloudtrail:StopLogging` 的 SCP，Management Account 的 root user 或 admin 依然可以執行這個動作。解法：Management Account 裡什麼業務都不跑，只做 AWS Organizations 管理，且對它的操作要有額外的 out-of-band 監控。

**2. Access Analyzer 只看外部存取**

Access Analyzer 標準版掃的是「帳號外部」的存取（另一個帳號的 principal、匿名存取）。帳號內一個 Lambda role 有 `iam:*`，它看不到這個問題。要找帳號內部的 over-permission，得靠 `generate-service-last-accessed-details` 或 IAM Access Analyzer 的「unused access」功能（需要升級到 IAA 付費版）。把 Access Analyzer 和 least privilege 流程混為一談是很常見的誤解。

**3. Prowler 幾百個 finding 不代表要全修**

跑完 Prowler 看到 600 個 FAIL 不用崩潰。AWS 提供的 managed rule 有些對小環境完全不 relevant（例如某些 FedRAMP 要求）。正確做法：按 severity 過濾，CRITICAL 先動（通常 5-20 條），LOW 可以接受 suppress（Prowler 支援設定 `allowlist.yaml` 排除誤報）。把「全修完」當目標會讓團隊精疲力竭，然後放棄維護。

**4. Config rules 評估有延遲**

AWS Config rule 不是即時的。資源建立後到 Config 記錄、到 rule 觸發評估，可能有 1-5 分鐘的延遲（高負載時更長）。這段窗口內，攻擊者可以建一個公開 S3 bucket、撈完資料、再刪掉，Config 的 NON_COMPLIANT 出現時木已成舟。Config rule 適合發現**持續存在的 misconfig**，不適合當即時 IDS 用。真正的即時告警要靠 CloudTrail + EventBridge rule（下一章主題）。

**5. SCP Deny 不會繼承 IAM 的 Condition**

如果你在 SCP 裡寫了一個沒有 Condition 的 Deny，它對 OU 下所有帳號的所有 principal 生效，包括你的 CI/CD role、Terraform service account。上線新的 Deny SCP 之前，要先在 dev OU 測試，確認不會打斷現有的自動化流程。推薦用 AWS Organizations 的 policy validation 功能，或先在 dev member account 手動確認影響範圍。

---

## 進階延伸

### Permission Boundaries：帳號內的縮減器

SCP 是跨帳號的防護欄，Permission Boundaries（權限邊界）是帳號**內部**的縮減器，讓你安全地授權給開發者建 IAM role，但限制他們最多能給 role 什麼權限。

使用場景：你允許開發者為自己的 Lambda 建 IAM role，但你加了一個 permission boundary，讓他們建出來的 role 最多只能讀特定 S3 bucket，就算 policy 寫了 `iam:*` 也沒用。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket",
        "logs:*"
      ],
      "Resource": [
        "arn:aws:s3:::allowed-bucket/*",
        "arn:aws:logs:*:*:*"
      ]
    }
  ]
}
```

這份 JSON 設為 boundary 後，任何掛了這個 boundary 的 role，能生效的動作 = IAM policy 允許 AND boundary 允許。它是 Ch 7 提權防禦的重要補充：即使攻擊者有辦法建 role，也建不出超越 boundary 範圍的 role。

### IAM Identity Center：替換 long-lived access key

Long-lived access key（長效存取金鑰）是攻擊者最愛的目標——一旦洩漏，不需要 MFA 就能操作整個帳號。IAM Identity Center（前身 AWS SSO）讓開發者用 SSO 登入，拿到時效短的暫時憑證（預設 1-8 小時），登出後憑證自動過期。

切換到 IAM Identity Center 後，你可以在 SCP 層加規則，拒絕沒帶 `aws:ViaAWSService` 的直接 API 呼叫，或至少監控所有直接使用 long-term access key 的操作。

### AWS Security Hub：finding 聚合中心

Security Hub 本身不掃描——它是**聚合器**。GuardDuty 的 threat finding、Inspector 的 CVE finding、Config 的 compliance finding、Prowler 的 check 結果，全都可以推進 Security Hub，統一在一個 dashboard 看。

它支援 ASFF（Amazon Security Finding Format）標準格式，也能把 finding 推到 Splunk、Datadog 等 SIEM。對於跨帳號的多帳號環境（Ch 8 那種架構），Security Hub 的「delegated administrator」模式可以讓一個管理帳號看到所有 member account 的 finding。

---

## 本章重點整理

- 雲端防禦的根本是「讓攻擊者拿到的東西不夠用」，而不是「偵測到再反應」
- Least privilege 落地流程：CloudTrail `generate-service-last-accessed-details` → 找沒用過的服務 → 縮 policy → 推上去
- IAM Access Analyzer 發現**外部存取**問題，對帳號內過度權限無感——這是最常見的誤解
- SCP 在 Organizations 層加防護欄，和 IAM policy 是 AND 關係，但 Management Account 不受 SCP 限制
- 三個實用 SCP：deny StopLogging（防日誌規避）、deny CreateUser（防持久化後門）、restrict regions（縮爆炸半徑）
- CSPM 工具做持續 misconfig 掃描，開源選 Prowler，原生整合選 Security Hub
- Config rules 有延遲，適合發現持久 misconfig，不適合當即時 IDS
- 防禦優先序：IAM 最小權限 > SCP > 網路隔離 > CSPM > Runtime detection

---

## 自我檢核

1. `generate-service-last-accessed-details` 回傳的 `LastAuthenticated: None` 代表什麼？對縮權決策的意義是什麼？
2. SCP deny 一個 action，Management Account 的 root user 能不能執行它？
3. `DenyCreateIAMUser` SCP 裡加 `Condition StringNotEquals BreakGlassAdmin` 的目的是什麼？如果不加會發生什麼事？
4. Prowler 掃出 600 個 FAIL，你的第一個動作是什麼？為什麼不能全部立刻修？
5. Config rule `s3-bucket-public-read-prohibited` 評估「NON_COMPLIANT」後，攻擊者有沒有機會已完成攻擊？為什麼？
6. Permission Boundary 和 SCP 都能限制 IAM 有效權限，差異在哪一層生效？

---

## 延伸閱讀

- [AWS IAM Access Analyzer — policy validation 官方文件](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-policy-validation.html)：Access Analyzer 的 automated reasoning 背後是 Zelkova 定理證明器，官方文件說明了它能發現哪些 policy 問題。
- [Implementing Least Privilege IAM Policies — AWS Security Blog](https://aws.amazon.com/blogs/security/techniques-for-writing-least-privilege-iam-policies/)：AWS 官方逐步說明 lastAccessedTime + CloudTrail 組合縮權的實務流程。
- [Prowler GitHub — AWS/GCP/Azure Security Tool](https://github.com/prowler-cloud/prowler)：Prowler 文件、check 清單、allowlist 設定方式。
- [Service Control Policies — AWS Organizations 官方文件](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)：SCP 評估邏輯、繼承規則、Condition 支援清單，必讀原文。
- [CIS AWS Foundations Benchmark v3.0](https://www.cisecurity.org/benchmark/amazon_web_services)：業界標準的 AWS 安全基準，Prowler 和 Security Hub 的 check 很大比例對應這份文件的 control。

---

Part 7 的地基打好了。最小權限讓攻擊者拿到東西沒用，SCP 讓攻擊者的操作空間被硬限縮，CSPM 持續監控讓 misconfig 沒機會長期潛伏。下一章進 Kubernetes 側，把同樣的防禦邏輯套到 Pod 和 cluster 層級。

→ [Ch 35 K8s hardening：Pod Security / OPA-Kyverno / Falco](./35-k8s-hardening.md)
