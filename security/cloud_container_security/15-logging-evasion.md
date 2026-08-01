# Ch 15 — 日誌與偵測規避（紅隊 OPSEC）：CloudTrail 怎麼抓你

> **目標**：理解 CloudTrail 記錄什麼、GuardDuty 如何產生告警、攻擊者會嘗試哪些規避手法——讓藍隊能設計覆蓋這些手法的偵測規則，讓紅隊理解每個動作都留下什麼痕跡。

---

**防禦教育免責聲明**

本章從藍隊視角出發，詳細描述攻擊者的 OPSEC（行動安全）手法，目的是讓防禦者理解攻擊面，進而設計有效的偵測規則。本章內容是建立 detection rule 的素材，不是攻擊指南。章節中的「攻擊者角度」段落，每一條結尾都對應「藍隊啟示」——這才是重點。

在你自己的帳號或授權的滲透測試環境之外執行任何雲端攻擊操作，在多數司法管轄區屬於刑事犯罪。

---

## 為什麼需要

你在 EC2 上拿到了 SSRF，把 instance metadata 的 credential 讀出來了。接下來的每一個 AWS API call，都在 CloudTrail 裡留下一筆不可竄改的日誌。

很多從傳統 pentest 背景進入雲端的人，直覺是「拿到 shell 就能清 log」。在 Linux 上你可以刪 `/var/log/auth.log`，但 CloudTrail 的日誌不住在你能碰的地方——它住在一個 S3 bucket 裡，通常是另一個帳號管理的，而且 GuardDuty 的告警是即時的，不需要等日誌分析。

這一章把 CloudTrail 的機制和 GuardDuty 的 finding 說清楚，讓你知道攻擊者每一步踩到的是什麼，藍隊該在哪裡設防線。

## 先建直覺

把 AWS 帳號想成一棟有完整 CCTV 系統的建築：

```
  你的 API call
       │
       ▼
  AWS Control Plane
  ┌─────────────────────────────────────────────────┐
  │  IAM / EC2 / S3 / Lambda ... API endpoints      │
  └──────────────┬──────────────────────────────────┘
                 │  每個 request 都被截錄
                 ▼
  ┌─────────────────────────────────────────────────┐
  │              CloudTrail（管理事件）              │
  │  eventTime, userIdentity, sourceIP, eventName   │
  │  requestParameters, responseElements             │
  └──────────────┬──────────────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
  S3 bucket          CloudWatch Logs
  （長期存檔）        （即時搜尋 / alarm）
        │
        ▼
  Athena / SIEM
  （retrospective hunt）
                          │
                          ▼
                    GuardDuty
                    （即時 ML 告警）
```

CCTV 比喻的關鍵點：
- 你進建築的那一刻就被錄了（不需要「開始錄影」）
- 鏡頭不在你能控制的房間裡
- 有一台 AI 即時看鏡頭（GuardDuty），不需要等事後人工審閱
- 影帶存在防火保險箱裡（Object Lock S3），你找不到火燒掉它

## 底層機制

### CloudTrail 記錄什麼

CloudTrail 把事件分兩類：

**管理事件（Management Events / Control Plane）**：對 AWS 資源的操作——建立、刪除、修改、設定。預設開啟，不需要額外設定。

```
範例操作：
  iam:CreateUser
  iam:AttachUserPolicy
  ec2:RunInstances
  s3:PutBucketPolicy
  lambda:CreateFunction
  cloudtrail:StopLogging    ← 這也會被記錄，下面詳說
```

**資料事件（Data Events / Data Plane）**：對資源「裡面的資料」的操作。**預設不開**，需要在 Trail 設定裡明確勾選。

```
範例操作：
  s3:GetObject          ← 讀 S3 物件，預設不記
  s3:PutObject          ← 寫 S3 物件，預設不記
  lambda:InvokeFunction ← 呼叫 Lambda，預設不記
  dynamodb:GetItem      ← 讀 DynamoDB，預設不記
  secretsmanager:GetSecretValue ← 讀 Secret，預設不記
```

這個「預設不記」的設計，是防禦最大的盲點之一。

### 每筆日誌長什麼樣

一筆 CloudTrail 事件是 JSON，關鍵欄位：

```json
{
  "eventTime": "2026-08-01T14:32:17Z",
  "eventName": "GetSecretValue",
  "eventSource": "secretsmanager.amazonaws.com",
  "sourceIPAddress": "203.0.113.42",
  "userAgent": "aws-cli/2.13.0 Python/3.11.4",
  "userIdentity": {
    "type": "AssumedRole",
    "principalId": "AROA...EXAMPLE:i-0123456789abcdef0",
    "arn": "arn:aws:sts::123456789012:assumed-role/ec2-app-role/i-0123456789abcdef0",
    "accountId": "123456789012",
    "sessionContext": {
      "sessionIssuer": {
        "type": "Role",
        "principalId": "AROA...EXAMPLE",
        "arn": "arn:aws:iam::123456789012:role/ec2-app-role"
      }
    }
  },
  "requestParameters": {
    "secretId": "prod/db/password"
  },
  "responseElements": null,
  "errorCode": null,
  "errorMessage": null
}
```

**藍隊看什麼**：
- `sourceIPAddress`：是不是 EC2 的 VPC IP？如果是 EC2 instance credential 卻從外部 IP 呼叫，GuardDuty 會立刻觸發告警
- `userIdentity.arn`：`assumed-role` 可以追到是哪個 role、哪個 session
- `principalId` 裡的 EC2 instance ID：可以對應到是哪台機器
- `userAgent`：`aws-cli` vs SDK vs 掃描工具有時候特徵不同

### Trail 設定類型

```
Single-Region Trail
  └── 只記錄建立該 trail 的 region 的事件
  └── 如果攻擊者在別的 region 操作，這條 trail 看不到

Multi-Region Trail
  └── 記錄所有 region 的事件，集中到一個 S3 bucket

Organization Trail（最強）
  └── 在管理帳號（Management Account）建立
  └── 自動覆蓋所有 member 帳號的所有 region
  └── Member 帳號無法刪除或停用這條 trail
  └── 這是最難被攻擊者規避的配置
```

把 trail 的目的地 S3 bucket 設在**獨立的 log archive 帳號**，攻擊者就算拿到 member 帳號的最高權限，也碰不到日誌。

### GuardDuty Finding 類型

GuardDuty 是獨立的 ML 偵測服務，不依賴你的 CloudTrail 設定是否完整。它有自己的資料來源：CloudTrail 事件流、VPC Flow Logs、DNS query log，以及從 AWS 全球威脅情報。

幾個常見且重要的 finding：

**偵查類（Recon）**：
```
Recon:IAMUser/MaliciousIPCaller
  └── 已知惡意 IP 呼叫 IAM 枚舉 API

Recon:IAMUser/TorIPCaller
  └── Tor exit node IP 呼叫枚舉 API
  └── 幾乎沒有合法業務需求從 Tor 操作 AWS

Recon:EC2/PortProbeUnprotectedPort
  └── 外部 IP 掃描 EC2 的未保護 port
```

**憑證存取類（CredentialAccess）**：
```
CredentialAccess:IAMUser/AnomalousBehavior
  └── 不常見的 credential 存取模式（ML 判斷）

UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.OutsideAWS
  └── EC2 instance 的臨時 credential，被從 AWS 網路外部的 IP 使用
  └── 這是 SSRF → credential 外洩的標準特徵，即時觸發
```

**持久化類（Persistence）**：
```
Persistence:IAMUser/AnomalousBehavior
  └── 不尋常的 IAM 操作（建帳號、建 key、改 policy）

Stealth:IAMUser/CloudTrailLoggingDisabled
  └── CloudTrail 被停用
  └── 這是最高告警等級的 finding 之一
```

**外洩類（Exfiltration）**：
```
Exfiltration:S3/MaliciousIPCaller
  └── 已知惡意 IP 對 S3 做 GetObject 操作

Exfiltration:S3/AnomalousBehavior
  └── 異常大量的 S3 下載（需要開啟 S3 Protection）
```

### 哪些操作最容易觸發告警

攻擊者剛拿到 credential，通常做的第一件事是枚舉（enumeration）。這些 API 每一個都在 CloudTrail 裡留記錄，批量呼叫是 CloudTrail Insights 的標準告警特徵：

```bash
# 典型初始枚舉序列——每一行都是一筆 Management Event
aws sts get-caller-identity           # 確認自己是誰
aws iam list-users                    # 列所有 IAM user
aws iam list-roles                    # 列所有 role
aws iam list-attached-user-policies   # 列 policy
aws s3 ls                             # 列所有 bucket（全帳號）
aws ec2 describe-instances            # 列所有 EC2
aws secretsmanager list-secrets       # 列所有 secret
```

**期望輸出（正常帳號的 get-caller-identity）**：
```json
{
    "UserId": "AROA...EXAMPLE:i-0123456789abcdef0",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/ec2-app-role/i-0123456789abcdef0"
}
```

這串操作在三分鐘內出現，`sourceIPAddress` 是同一個 IP，`userAgent` 是 `aws-cli`——任何有 CloudTrail Insights 或 SIEM detection rule 的帳號都會在五分鐘內產生告警。

## 攻擊者 OPSEC vs 藍隊偵測框架

這一節以「藍隊需要知道攻擊者嘗試什麼，才能建偵測規則」為框架。每個 OPSEC 手法後面緊接藍隊的對應偵測。

### 手法一：StopLogging / DeleteTrail

**攻擊者的想法**：停掉 CloudTrail，後續操作就不被記錄。

**現實**：

```bash
# 嘗試停止 logging
aws cloudtrail stop-logging --name my-trail
# 或直接刪 trail
aws cloudtrail delete-trail --name my-trail
```

這個操作本身就是一筆 Management Event，在執行的當下，CloudTrail **還在運作**，所以 `StopLogging` 這個 API call 本身一定被記錄。

如果帳號有 GuardDuty，`Stealth:IAMUser/CloudTrailLoggingDisabled` finding 在幾秒內產生，不需要等人工審閱。

對 **Organization Trail** 無效：member 帳號沒有 stop-logging 或 delete-trail 的權限，因為 trail 是在管理帳號建立的。

**藍隊啟示**：
- 使用 Organization Trail，member 帳號無法停用
- CloudWatch Alarm 對 `StopLogging` / `DeleteTrail` event 即時告警，延遲在一分鐘以內
- GuardDuty 自動偵測，不需要額外設定 alarm

CloudWatch alarm 的 filter pattern：

```json
{
  "source": ["aws.cloudtrail"],
  "detail": {
    "eventName": ["StopLogging", "DeleteTrail", "UpdateTrail"]
  }
}
```

**本段關於 GuardDuty 告警延遲未在受控環境實測，為理論預期行為。自驗方法：在測試帳號（非生產）執行 `aws cloudtrail stop-logging`，觀察 GuardDuty console 何時出現 finding，並驗證 CloudTrail 裡 `StopLogging` event 是否存在。**

### 手法二：跨 Region 操作

**攻擊者的想法**：如果 trail 只覆蓋 us-east-1，在 eu-west-1 操作就不被記錄。

**現實**：
- 這個手法只對 **single-region trail** 有效
- Multi-region trail 和 Organization Trail 覆蓋所有 region，跨 region 沒有用
- 在 `eu-west-1` 做操作但 identity 來自 `us-east-1` 的 EC2 role，本身就是異常信號

```bash
# 在非預期 region 操作
aws ec2 describe-instances --region eu-west-1
aws s3 ls --region ap-northeast-1
```

**藍隊啟示**：
- 強制使用 Organization Trail 或 Multi-Region Trail
- 在 SCP（服務控制策略）裡限制只能在核准的 region 操作，未核准的 region 的 API call 直接被拒絕，根本不需要偵測

SCP 範例（deny 非核准 region，但放行全域服務如 IAM、STS、CloudFront）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyNonApprovedRegions",
      "Effect": "Deny",
      "NotAction": [
        "iam:*",
        "sts:*",
        "cloudfront:*",
        "support:*"
      ],
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:RequestedRegion": ["us-east-1", "ap-northeast-1"]
        }
      }
    }
  ]
}
```

### 手法三：利用 Data Events 的盲點

**攻擊者的想法**：如果 S3 data events 沒開，`GetObject` 不被記錄，可以大量讀資料不留痕跡。

**現實**：

如果帳號的 Trail 沒有開 S3 data events：

```bash
# 這些操作完全不在 CloudTrail 裡——如果 data events 沒開
aws s3 cp s3://prod-sensitive-bucket/customer-data.csv ./
aws s3 sync s3://prod-sensitive-bucket/ ./local-copy/
```

同樣：
- Lambda `Invoke` 不記錄（如果沒開 Lambda data events）
- DynamoDB `GetItem` / `Query` 不記錄
- `secretsmanager:GetSecretValue` **例外**：這個 API 屬於 management event（Secrets Manager API），預設**會**被記錄，即使沒開 data events

**藍隊啟示**：
- 敏感 S3 bucket 必須開 data events，這是非談判性的要求
- 使用 S3 Server Access Logging 作為第二層（獨立於 CloudTrail）
- GuardDuty S3 Protection 即使沒有完整 data events 也能偵測部分異常行為
- Macie 對敏感資料掃描不依賴 CloudTrail data events

開啟特定 bucket 的 data events：

```bash
aws cloudtrail put-event-selectors \
  --trail-name my-org-trail \
  --event-selectors '[
    {
      "ReadWriteType": "All",
      "IncludeManagementEvents": true,
      "DataResources": [
        {
          "Type": "AWS::S3::Object",
          "Values": ["arn:aws:s3:::prod-sensitive-bucket/"]
        }
      ]
    }
  ]'
```

### 手法四：慢速枚舉（Low and Slow）

**攻擊者的想法**：把枚舉操作拉長到幾小時或幾天，每次呼叫之間有隨機延遲，希望不觸發頻率異常告警。

**藍隊啟示**：
- CloudTrail Insights 偵測的是相對於歷史基線的異常，慢速枚舉如果超過這個帳號的 `ListBuckets` 正常頻率，仍然觸發
- 有些操作本身就不該出現：一個 EC2 instance role 不應該呼叫 `iam:ListUsers`，不管頻率多低
- 建 detection rule 應該包含「不尋常的 API by identity」，不只是「頻率異常」

## 強防禦框架

這一節是本章的核心，把前面所有知識整合成可執行的防禦架構。

### 層次一：Organization Trail（最高優先）

在管理帳號建立，覆蓋所有 region、所有 member 帳號。這是讓攻擊者的 StopLogging / DeleteTrail 完全失效的基礎。

```bash
# 在管理帳號執行
aws cloudtrail create-trail \
  --name org-trail \
  --s3-bucket-name my-org-cloudtrail-logs-123456789012 \
  --is-multi-region-trail \
  --include-global-service-events \
  --enable-log-file-validation \
  --is-organization-trail

aws cloudtrail start-logging --name org-trail
```

`--enable-log-file-validation` 讓 CloudTrail 為每份日誌檔建立 SHA-256 hash chain，即使 S3 物件被刪除或竄改，也能偵測到。

### 層次二：S3 Object Lock（日誌不可竄改）

把 trail 的 S3 bucket 設 Object Lock（WORM）。即使攻擊者拿到管理帳號權限並刪掉 trail 設定，歷史日誌也無法刪除。

```bash
# 建 bucket 時開啟 Object Lock（必須在建立時設定）
aws s3api create-bucket \
  --bucket my-org-cloudtrail-logs-123456789012 \
  --object-lock-enabled-for-bucket

# 設定預設保留規則（90 天 Compliance 模式）
aws s3api put-object-lock-configuration \
  --bucket my-org-cloudtrail-logs-123456789012 \
  --object-lock-configuration '{
    "ObjectLockEnabled": "Enabled",
    "Rule": {
      "DefaultRetention": {
        "Mode": "COMPLIANCE",
        "Days": 90
      }
    }
  }'
```

`COMPLIANCE` 模式：在保留期內，**任何人**（包括 root 帳號）都無法刪除物件。這是最強的保護，也是最有意義的設定。

### 層次三：GuardDuty 全 Region + 所有 Protection Plan

```bash
# 在每個使用的 region 啟用 GuardDuty
aws guardduty create-detector --enable --region us-east-1
aws guardduty create-detector --enable --region ap-northeast-1

# 啟用 S3 Protection
aws guardduty update-detector \
  --detector-id <detector-id> \
  --data-sources '{"S3Logs":{"Enable":true}}'
```

用 AWS Organizations 的 GuardDuty 委派管理員功能，在一個帳號集中管理所有 member 帳號的 GuardDuty，確保沒有 region 被遺漏。

### 層次四：即時告警（CloudWatch + SNS）

對最高優先的操作建立即時告警：

```bash
# 1. 建 CloudTrail → CloudWatch Logs 的整合（如果還沒做）
# 2. 建 metric filter 偵測 StopLogging / DeleteTrail
aws logs put-metric-filter \
  --log-group-name CloudTrail/DefaultLogGroup \
  --filter-name CloudTrailChanges \
  --filter-pattern '{ ($.eventName = "StopLogging") || ($.eventName = "DeleteTrail") || ($.eventName = "UpdateTrail") }' \
  --metric-transformations \
    metricName=CloudTrailChanges,metricNamespace=CloudTrailMetrics,metricValue=1

# 3. 建 alarm
aws cloudwatch put-metric-alarm \
  --alarm-name CloudTrail-Changes \
  --alarm-description "CloudTrail was modified or stopped" \
  --metric-name CloudTrailChanges \
  --namespace CloudTrailMetrics \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:security-alerts
```

### 層次五：CloudTrail Insights

偵測 API 呼叫頻率的統計異常（枚舉的特徵是短時間大量 List\* / Describe\*）：

```bash
aws cloudtrail put-insight-selectors \
  --trail-name org-trail \
  --insight-selectors '[{"InsightType":"ApiCallRateInsight"},{"InsightType":"ApiErrorRateInsight"}]'
```

Insights 需要至少七天的基線學習期，之後對偏離基線的 API 呼叫頻率產生 Insight event，這些 event 同樣寫入 CloudTrail。

### 層次六：SIEM 整合與 Athena 查詢

把 CloudTrail 日誌送進可以做 ad-hoc 查詢的系統，用來 threat hunting：

```
CloudTrail Logs (S3)
      │
      ▼
Kinesis Data Firehose     ← 即時串流到 SIEM（可選）
      │
      ▼
S3 (集中 log bucket)
      │
      ▼
AWS Glue (schema catalog)
      │
      ▼
Athena (SQL 查詢)
```

Athena 查詢範例：找出過去 24 小時所有 IAM 枚舉操作：

```sql
SELECT
  eventTime,
  userIdentity.arn,
  sourceIPAddress,
  eventName,
  awsRegion
FROM cloudtrail_logs
WHERE
  eventTime > to_iso8601(now() - interval '24' hour)
  AND eventName IN (
    'ListUsers', 'ListRoles', 'ListGroups',
    'ListBuckets', 'DescribeInstances',
    'ListSecrets', 'ListFunctions'
  )
ORDER BY eventTime DESC
LIMIT 1000;
```

## 對比取捨表

| 機制 | 覆蓋面 | 無法防禦的情況 | 成本 |
|------|--------|--------------|------|
| Single-Region Trail | 一個 region | 跨 region 操作 | 低 |
| Multi-Region Trail | 所有 region | member 帳號可停用 | 中 |
| Organization Trail | 所有帳號所有 region | 需要管理帳號存在 | 中 |
| S3 Object Lock | 日誌不可刪 | Trail 設定本身可被刪（但日誌留存） | 低（儲存費） |
| GuardDuty | 即時 ML 偵測 | 誤報、需要人員回應 | 按使用量 |
| CloudTrail Insights | 頻率異常偵測 | 需要 7 天基線，慢速攻擊 | 按 Insight event 計費 |
| S3 Data Events | 資料操作可見性 | 高流量 bucket 成本極高 | 按事件計費 |

## 踩雷集錦

**1. 以為刪 trail 就能清 log**

攻擊者拿到帳號後執行 `delete-trail`。這個操作本身留在 CloudTrail（執行時 trail 還在），GuardDuty 產生 `Stealth:IAMUser/CloudTrailLoggingDisabled`，SOC 五分鐘內收到通知。歷史日誌在 Object Lock 的 S3 bucket 裡，動不了。

**2. S3 data events 沒開，以為 GetObject 不被記錄所以安全**

GuardDuty S3 Protection 和 VPC Flow Logs 仍然記錄網路層的行為。大量 GetObject 操作雖然不在 CloudTrail 裡，但 GuardDuty 還是可以透過其他信號偵測到異常的 S3 流量模式。不是完美的盲點。

**3. 只在 us-east-1 開 GuardDuty**

攻擊者在 eu-west-1 建立後門 EC2、開新 access key，us-east-1 的 GuardDuty 完全看不到。GuardDuty 必須在每個 region 獨立開啟，或透過 Organizations 委派管理員自動開啟所有 region。

**4. CloudTrail 送 CloudWatch Logs 但沒設 Retention**

CloudWatch Logs 預設**永久保留**，費用會累積。生產帳號建議設 90 天 retention，長期存檔在 S3（成本低一個數量級）。

**5. 把 log archive bucket 放在同一個帳號**

如果攻擊者拿到帳號的 AdminAccess，同帳號的 log bucket 也在攻擊範圍內。把 log archive bucket 放在獨立的 Security/Audit 帳號，即使 member 帳號完全淪陷，日誌也安全。

## 進階延伸

**CloudTrail Lake**：CloudTrail 的新一代服務，把日誌存成不可變的 event data store，可以直接 SQL 查詢，不需要 Athena + Glue 的設定成本。對於需要跨帳號、跨 region 關聯查詢的場景，比傳統 S3 + Athena 架構更簡單。

**Security Hub 整合**：GuardDuty finding 可以自動送進 Security Hub，統一管理所有帳號、所有服務的告警（GuardDuty + Inspector + Macie + Config），做優先排序和 suppression rule。

**EventBridge + Lambda 自動化回應**：GuardDuty finding 觸發 EventBridge rule，呼叫 Lambda 自動執行回應——比如偵測到 `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration` 時，自動對該 instance 的 role 加 deny all 的 inline policy，阻斷進一步的橫向移動，等人工確認。這是 SOAR（安全協調自動化回應）的最簡實作。

**VPC Flow Logs**：CloudTrail 只看控制平面，VPC Flow Logs 看網路層。把兩者結合，可以做「API call 的 source IP」和「同時間的 VPC 連線」的交叉關聯，識別 C2 通道。

**AWS Config**：不是日誌系統，是設定狀態的持續審計。可以偵測「trail 被停用」、「GuardDuty 被關閉」、「S3 bucket 變成 public」等設定漂移（configuration drift），並強制合規。

## 本章重點整理

- CloudTrail Management Events 預設開，記錄控制平面的所有 API call
- Data Events 預設不開，S3 GetObject / Lambda Invoke 不被記錄，這是防禦盲點
- 每筆 event 記錄 eventTime、userIdentity（包括 role chain）、sourceIPAddress、eventName
- GuardDuty 即時偵測，告警不依賴 CloudTrail 設定完整性
- `StopLogging` 本身會被記錄，且觸發 GuardDuty finding，對 Organization Trail 根本無效
- 防禦框架：Organization Trail → S3 Object Lock → GuardDuty 全 region → 即時 alarm → Insights → SIEM 整合
- 攻擊者的每個 OPSEC 手法都有對應的偵測機制，沒有「安全的」API call 路徑

## 自我檢核

- [ ] 我能解釋 Management Events 和 Data Events 的差異，以及預設行為
- [ ] 我能描述一筆 CloudTrail event 的關鍵欄位，並說明藍隊從 userIdentity 能追溯什麼
- [ ] 我知道 Organization Trail 和 Single-Region Trail 的覆蓋範圍差異
- [ ] 我能說出 `StopLogging` 為什麼不是有效的規避手法
- [ ] 我能列出三個 GuardDuty finding 名稱，並說明各自對應的攻擊行為
- [ ] 我知道 S3 data events 不開的情況下，哪些操作不被記錄
- [ ] 我能描述 S3 Object Lock Compliance 模式的保護強度
- [ ] 我能解釋 CloudTrail Insights 偵測的是什麼，以及它的限制
- [ ] 我知道 log archive bucket 應該放在哪個帳號，以及原因
- [ ] 我能設計一個包含六個層次的 CloudTrail 防禦框架

## 延伸閱讀

1. **AWS CloudTrail User Guide — Logging Data Events**
   Search: `site:docs.aws.amazon.com cloudtrail logging data events`
   讀這個搞清楚 data events 和 advanced event selectors 的設定語法，以及哪些 AWS 服務支援 data events。

2. **GuardDuty Finding Types**
   URL: `https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html`
   完整的 finding type 目錄，每個都有觸發條件、嚴重度、建議回應。遇到不熟悉的 finding 時的第一個查詢點。

3. **CloudTrail Log File Integrity Validation**
   Search: `site:docs.aws.amazon.com cloudtrail log file integrity validation`
   理解 hash chain 的驗證機制，以及如何用 `aws cloudtrail validate-logs` 驗證日誌沒被竄改。

4. **AWS Security Hub — Foundational Security Best Practices**
   Search: `site:docs.aws.amazon.com securityhub aws-foundational-security-best-practices`
   包含 CloudTrail、GuardDuty、Config 的基線合規檢查，可以用來審計現有帳號缺了哪些防禦層。

5. **Threat Hunting with CloudTrail and Athena — Stratus Red Team 文件**
   URL: `https://stratus-red-team.cloud/`
   Stratus Red Team 是雲端攻擊模擬框架，每個攻擊 technique 都附對應的偵測建議，是建立 detection rule 的實務參考。

---

本章建立了偵測基礎框架。下一章把整個殺傷鏈串起來：從初始存取、橫向移動、持久化到外洩，在受控環境裡走一遍完整的攻擊路徑，對照每個步驟在 CloudTrail 和 GuardDuty 裡留下的痕跡。

繼續：[練習 B — 雲端殺傷鏈模擬](practice-b-cloud-killchain.md)
