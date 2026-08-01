# Ch 36 — 雲端偵測工程：CloudTrail 到 SIEM、GuardDuty、雲端 IR

> **目標**：以攻擊者視角反轉為防守者視角，掌握 CloudTrail 管線架構、GuardDuty finding 調查、EventBridge 偵測規則、Athena SQL 威脅獵殺，與雲端 IR 遏制流程，讓每一個惡意 API 呼叫都無所遁形。

---

## 為什麼需要雲端偵測工程

Ch15 教了規避手法：刪 trail、關 logging、換 region 偷偷行動、插 filter 讓特定 API 呼叫不被記錄。但那一章也暗示了偵測的切入點——規避手法越多，代表攻擊者越怕被看見。

核心原則只有一句話：**攻擊者要行動，就一定要呼叫 API；API 呼叫就一定留 CloudTrail**（前提是 trail 還活著）。所以防守方的第一個任務，是確保 trail 本身不可被破壞，第二個任務是把 trail 產生的資料變成可以告警、可以查詢、可以自動回應的管線。

傳統資安強調「邊界防護」，但在雲端環境裡，邊界消失了。EC2 可以直接打 AWS API，Container 可以拿到 IMDS token，Lambda 可以跨帳號 assume role。攻擊面是所有 AWS API 的集合，大約有幾千個 action。防守的核心從「封閉邊界」變成「偵測異常行為」，從阻擋流量變成分析日誌。

---

## 先建直覺：偵測就是把日誌變成問題的答案

想像你是一個銀行保全，監視器錄下了所有進出門的人（CloudTrail）。你可以：

1. 把錄影帶傳到總部的監控中心集中分析（SIEM）
2. 安裝一套智慧攝影機，遇到可疑人物自動亮紅燈（GuardDuty）
3. 設定規則：只要有人刷卡進禁區就發警報（EventBridge Alarm）
4. 事後查案：翻錄影帶找「昨天下午三點進過 B 棟的人」（Athena SQL）

CloudTrail 是那個「錄影帶」，後面三個都依賴它。如果有人把攝影機關掉（StopLogging），你一定要先知道攝影機被關了，才能做其他事。這就是為什麼「偵測 StopLogging」是所有偵測規則裡優先級最高的一條。

```
                     ┌──────────────────────────────────────────────┐
                     │               AWS 帳號                        │
                     │                                              │
  使用者/服務 ──────► │  AWS API 呼叫                                 │
                     │       │                                       │
                     │       ▼                                       │
                     │  CloudTrail (Management/Data/Insights)        │
                     │       │                                       │
                     └───────┼───────────────────────────────────────┘
                             │
               ┌─────────────┼──────────────────┐
               │             │                  │
               ▼             ▼                  ▼
          S3 Bucket     EventBridge         GuardDuty
          (長期存檔)     Rule Engine        (ML 分析)
               │             │                  │
        ┌──────┤       ┌─────┤             ┌────┤
        │      │       │     │             │    │
        ▼      ▼       ▼     ▼             ▼    ▼
    Athena  Firehose  SNS  Lambda       Security  Findings
    (SQL)     │      Alert  自動化       Hub     Console
              │
              ▼
        OpenSearch /
        Splunk / SIEM
```

上圖是兩條主要管線：

- **即時告警管線**：CloudTrail → EventBridge → SNS/Lambda → PagerDuty/Slack。延遲約 1-3 分鐘。
- **長期分析管線**：CloudTrail → S3 → Athena/OpenSearch。延遲可接受數分鐘到數小時，但可以回查歷史。

GuardDuty 是獨立的 ML-based 偵測引擎，它消費 CloudTrail（以及 VPC Flow Logs、DNS logs）自己做分析，finding 通常有 5-15 分鐘延遲。

---

## CloudTrail Event 三種類型

| Event 類型 | 涵蓋內容 | 預設開啟 | 費用 |
|-----------|---------|--------|------|
| **Management Events** | CreateUser、AttachPolicy、DeleteTrail、StopLogging 等控制面 API | 是（每帳號免費一份） | 免費（第一份）|
| **Data Events** | S3 GetObject/PutObject、Lambda Invoke、DynamoDB GetItem 等資料面 API | 否 | 約 $0.10 / 100K events |
| **Insights Events** | 偵測 API 呼叫頻率異常（例如突然大量 GetSecretValue） | 否 | 約 $0.35 / 100K events |

**Management Events 是基礎**，必須開；Data Events 根據需求選擇性開（開全部費用很高）；Insights Events 是偵測枚舉攻擊的好工具，建議至少對 Write API 開啟。

### Multi-region trail 為什麼是必要的

攻擊者知道一件事：很多組織只在主要 region（例如 ap-northeast-1）開 trail，因此他們故意在 us-east-2 或 eu-west-1 建 IAM user、建 S3 bucket、開 EC2。Multi-region trail 讓所有 region 的 event 都送進同一個 S3 bucket，消除這個死角。

開啟方式：

```bash
# 建立一條覆蓋所有 region 的 trail
aws cloudtrail create-trail \
  --name global-audit-trail \
  --s3-bucket-name my-cloudtrail-audit-logs \
  --is-multi-region-trail \
  --include-global-service-events \
  --enable-log-file-validation

aws cloudtrail start-logging --name global-audit-trail
```

`--enable-log-file-validation` 讓 CloudTrail 對每個 log 檔產生 SHA-256 digest，攻擊者若篡改 S3 裡的 log，`validate-logs` 指令就能偵測到。

### CloudTrail Lake

CloudTrail Lake 是 2022 年推出的功能，讓你直接用 SQL 查詢 CloudTrail events，不需要自己建 S3 + Glue + Athena 的資料湖管線。events 預設保留 7 年。

```sql
-- CloudTrail Lake SQL 範例：找過去 1 小時的 StopLogging 呼叫
SELECT
  eventTime,
  userIdentity.arn,
  sourceIPAddress,
  requestParameters
FROM $EDS_ID
WHERE
  eventName = 'StopLogging'
  AND eventTime > DATE_ADD('hour', -1, NOW())
```

適合不想維護 Athena + Glue 基礎架構的小型團隊。費用約 $0.005 / GB ingested + $0.005 / GB scanned。

---

## GuardDuty Finding 分類與調查

GuardDuty（守衛職責）是 AWS 托管的威脅偵測服務，輸入來源是 CloudTrail、VPC Flow Logs、Route 53 DNS logs，輸出是 finding（偵測結果）。

Finding 按威脅類型分七大類：

| 類別 | 意義 | 範例 |
|------|------|------|
| **Policy** | 違反最佳實踐 | 對外公開 S3 bucket |
| **Recon** | 偵查/探測階段 | 枚舉 IAM user |
| **Discovery** | 發現資源 | 掃描 S3 bucket 清單 |
| **PrivilegeEscalation** | 提權 | passRole 建立管理員 role |
| **Persistence** | 持久化 | 建新 IAM user 或 access key |
| **Stealth** | 隱匿操作 | 關掉 CloudTrail |
| **Exfiltration** | 資料外洩 | 大量 S3 GetObject |

### 5 個高優先 Finding 詳解

**a. UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B**

異地登入成功。GuardDuty 用 ML 對每個 IAM user 建立正常登入地理位置模型，如果突然從一個從未出現過的國家登入，就觸發。Finding 裡的 `location` 欄位會標出異常位置。

調查重點：確認是不是 VPN、出差、新人第一次登入。若無法解釋，立即 revoke session。

**b. PrivilegeEscalation:IAMUser/AdministrativePermissions**

某個 IAM principal 呼叫 `iam:PassRole` 或 `iam:AttachUserPolicy`，把一個具有管理員權限的 role 傳給服務或 Lambda，達到間接提權。Ch25 介紹過這個手法。

Finding 的 `additionalInfo.recentApiCalls` 欄位會列出觸發前幾分鐘的相關 API 呼叫，有助於重建攻擊鏈。

**c. Stealth:IAMUser/CloudTrailLoggingDisabled**

有人呼叫 `StopLogging` 或 `DeleteTrail`。這條 finding 的嚴重性是 HIGH，因為這是攻擊者在清除證據或開始正式行動前的準備動作。

Ch15 提過：攻擊者會先確認自己不被記錄再行動。所以「關 trail」本身就是攻擊的一部分，不要誤以為是管理員誤操作就放過。

**d. Discovery:S3/MaliciousIPCaller**

GuardDuty 維護一份已知惡意 IP 清單（含 Tor exit node、已知攻擊基礎設施），如果這些 IP 呼叫 S3 的 ListBuckets / ListObjects，就觸發。

`service.action.s3BucketAction.remoteIpDetails` 欄位包含 IP、自治系統號（ASN）、country。

**e. Persistence:IAMUser/UserPermissions**

某個 IAM principal 建立了新的 IAM user、為現有 user 加了新的 access key、或附加了新的 policy。這是攻擊者建立後門帳號的標準手法（Ch14 介紹過）。

Finding 的 `resource.accessKeyDetails.userName` 是觸發動作的 principal，`additionalInfo.recentApiCalls` 裡可以看到具體呼叫了哪些 IAM API。

### Finding 欄位結構

每個 GuardDuty finding 都是 JSON，關鍵欄位：

```json
{
  "id": "abc123...",
  "type": "Stealth:IAMUser/CloudTrailLoggingDisabled",
  "severity": 7.0,
  "createdAt": "2024-01-15T08:23:11Z",
  "updatedAt": "2024-01-15T08:23:11Z",
  "region": "ap-northeast-1",
  "accountId": "123456789012",
  "service": {
    "action": {
      "actionType": "AWS_API_CALL",
      "awsApiCallAction": {
        "api": "StopLogging",
        "serviceName": "cloudtrail.amazonaws.com",
        "callerType": "IAMUser",
        "remoteIpDetails": {
          "ipAddressV4": "203.0.113.42",
          "country": { "countryName": "Unknown" },
          "organization": { "asn": "12345", "asnOrg": "SuspiciousASN" }
        }
      }
    },
    "eventFirstSeen": "2024-01-15T08:22:50Z",
    "eventLastSeen": "2024-01-15T08:23:05Z",
    "count": 1
  },
  "resource": {
    "resourceType": "AccessKey",
    "accessKeyDetails": {
      "accessKeyId": "AKIAIOSFODNN7EXAMPLE",
      "principalId": "AIDACKCEVSQ6C2EXAMPLE",
      "userType": "IAMUser",
      "userName": "compromised-user"
    }
  }
}
```

調查時看 `resource.accessKeyDetails.userName`（誰做的）、`service.action.awsApiCallAction.remoteIpDetails`（從哪裡）、`createdAt`（什麼時候），三個問題回答完就有基本的攻擊輪廓。

---

## CloudWatch Alarm + EventBridge 偵測規則

GuardDuty 負責 ML-based 偵測，但有些規則是確定性的（任何人呼叫 StopLogging 都要告警），這種情況用 EventBridge + CloudWatch 更適合，因為延遲更低（通常 1-3 分鐘），且規則邏輯完全在你掌控之中。

### 高風險 API 清單

以下 API 呼叫應該觸發即時告警：

| API | 危險原因 | 對應 MITRE |
|-----|---------|-----------|
| `CreateUser` | 建後門帳號 | T1136 |
| `AttachUserPolicy` / `AttachRolePolicy` | 提權 | T1098 |
| `StopLogging` / `DeleteTrail` | 消滅證據 | T1562.008 |
| `PutEventSelectors` | 縮小 trail 範圍，隱藏特定 API | T1562.008 |
| `GetSecretValue`（大量/快速） | 枚舉 secrets | T1528 |
| `AssumeRole`（跨帳號） | 橫向移動 | T1078 |
| `CreateAccessKey`（非本人） | 建憑證後門 | T1098.001 |

### 範例 1：EventBridge Rule 偵測 StopLogging

**本段未實測，為理論預期行為。**自驗方法：在測試帳號建立以下 rule，然後執行 `aws cloudtrail stop-logging --name test-trail`，驗證 SNS 通知是否在 3 分鐘內送達。

```json
{
  "source": ["aws.cloudtrail"],
  "detail-type": ["AWS API Call via CloudTrail"],
  "detail": {
    "eventSource": ["cloudtrail.amazonaws.com"],
    "eventName": [
      "StopLogging",
      "DeleteTrail",
      "PutEventSelectors",
      "UpdateTrail"
    ]
  }
}
```

部署方式：

```bash
# 建立 EventBridge rule
aws events put-rule \
  --name detect-cloudtrail-tampering \
  --event-pattern '{
    "source": ["aws.cloudtrail"],
    "detail-type": ["AWS API Call via CloudTrail"],
    "detail": {
      "eventSource": ["cloudtrail.amazonaws.com"],
      "eventName": ["StopLogging","DeleteTrail","PutEventSelectors","UpdateTrail"]
    }
  }' \
  --state ENABLED \
  --region ap-northeast-1

# 設定 target：送到 SNS topic
aws events put-targets \
  --rule detect-cloudtrail-tampering \
  --targets '[{
    "Id": "SendToSNS",
    "Arn": "arn:aws:sns:ap-northeast-1:123456789012:security-alerts"
  }]' \
  --region ap-northeast-1
```

### 範例 2：CloudWatch Metric Filter + Alarm 偵測異常 Console 登入

**本段未實測，為理論預期行為。**自驗方法：把 CloudTrail log 送到 CloudWatch Logs group，再建立以下 metric filter，從另一個地區登入 console 觸發告警。

```bash
# Step 1：建立 metric filter，計算 ConsoleLogin 次數
aws logs put-metric-filter \
  --log-group-name CloudTrail/DefaultLogGroup \
  --filter-name ConsoleLoginCount \
  --filter-pattern '{ ($.eventName = "ConsoleLogin") && ($.additionalEventData.MFAUsed != "Yes") }' \
  --metric-transformations '[{
    "metricName": "ConsoleLoginWithoutMFA",
    "metricNamespace": "SecurityMetrics",
    "metricValue": "1",
    "defaultValue": 0
  }]'

# Step 2：建立 CloudWatch Alarm，1 分鐘內超過 3 次登入就告警
aws cloudwatch put-metric-alarm \
  --alarm-name ConsoleLoginWithoutMFA \
  --alarm-description "Console login without MFA detected" \
  --metric-name ConsoleLoginWithoutMFA \
  --namespace SecurityMetrics \
  --statistic Sum \
  --period 60 \
  --threshold 3 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:ap-northeast-1:123456789012:security-alerts \
  --treat-missing-data notBreaching
```

Filter pattern 的邏輯：找 `ConsoleLogin` 事件，且 `MFAUsed != Yes`。MFA 未啟用的 console 登入更危險，因為憑證被偷就直接能進。

### 範例 3：偵測大量 GetSecretValue（Secret 枚舉）- 邊界案例

這是一個邊界情境：Lambda function 做健康檢查每 30 秒呼叫一次 `GetSecretValue`，但 GetSecretValue Insights 把它誤判為枚舉攻擊。

```bash
# 建立 Metric Filter，只計算來自人類操作的 GetSecretValue
# errorCode 不存在代表成功，userAgent 排除 Lambda runtime
aws logs put-metric-filter \
  --log-group-name CloudTrail/DefaultLogGroup \
  --filter-name SecretEnumeration \
  --filter-pattern '{
    ($.eventName = "GetSecretValue") &&
    ($.userAgent != "lambda") &&
    ($.errorCode NOT EXISTS)
  }' \
  --metric-transformations '[{
    "metricName": "GetSecretValueCount",
    "metricNamespace": "SecurityMetrics",
    "metricValue": "1",
    "defaultValue": 0
  }]'
```

這個過濾條件排除 Lambda runtime 的正常呼叫，但有個問題：攻擊者如果把 user agent 偽裝成 `aws-sdk-python/1.26.0 lambda`，就能繞過這條規則。正確的做法是同時看 `userIdentity.type`（是否為 Lambda Execution Role）而不是只看 user agent 字串。

---

## Athena SQL 查 CloudTrail

CloudTrail 把 event 存成 S3 上的 gzip JSON 檔，路徑格式為：

```
s3://bucket/AWSLogs/{account-id}/CloudTrail/{region}/{year}/{month}/{day}/
```

Athena 可以直接對 S3 上的 JSON 執行 SQL，不需要 ETL，適合：

- 即時威脅獵殺（Threat Hunting）
- 事後取證，找攻擊鏈
- 不想維護 SIEM 的小型環境

**本段未實測，為理論預期行為。**自驗方法：用 AWS Glue Crawler 或 CloudTrail Lake 建立 table，然後執行以下 SQL。確認查詢結果的 `eventTime` 欄位格式正確，partition 欄位（year/month/day）有被正確過濾。

### 建立 Athena Table（一次性設定）

```sql
CREATE EXTERNAL TABLE cloudtrail_logs (
  eventversion STRING,
  useridentity STRUCT<
    type: STRING,
    principalid: STRING,
    arn: STRING,
    accountid: STRING,
    sessioncontext: STRUCT<
      sessionissuer: STRUCT<
        type: STRING,
        principalid: STRING,
        arn: STRING,
        accountid: STRING,
        username: STRING
      >
    >
  >,
  eventtime STRING,
  eventsource STRING,
  eventname STRING,
  awsregion STRING,
  sourceipaddress STRING,
  useragent STRING,
  requestparameters STRING,
  responseelements STRING,
  errorcode STRING,
  errormessage STRING,
  requestid STRING,
  eventid STRING,
  eventtype STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING, region STRING)
ROW FORMAT SERDE 'com.amazon.emr.hive.serde.CloudTrailSerde'
STORED AS INPUTFORMAT 'com.amazon.emr.cloudtrail.CloudTrailInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://my-cloudtrail-bucket/AWSLogs/123456789012/CloudTrail/';
```

### SQL 1：找過去 24 小時的 CreateUser 呼叫

```sql
SELECT
  eventtime,
  useridentity.arn AS caller_arn,
  sourceipaddress,
  json_extract_scalar(requestparameters, '$.userName') AS new_username
FROM cloudtrail_logs
WHERE
  year = '2024'
  AND month = '01'
  AND day = '15'
  AND eventname = 'CreateUser'
  AND errorcode IS NULL
ORDER BY eventtime DESC;
```

`errorcode IS NULL` 篩選成功呼叫（失敗的 CreateUser 也值得查，代表有人嘗試但沒權限）。`json_extract_scalar` 從 `requestParameters` JSON 裡抓出新建的 username。

### SQL 2：找 AssumeRole 呼叫鏈

```sql
SELECT
  eventtime,
  useridentity.arn AS caller_arn,
  useridentity.type AS caller_type,
  json_extract_scalar(requestparameters, '$.roleArn') AS assumed_role,
  json_extract_scalar(requestparameters, '$.roleSessionName') AS session_name,
  sourceipaddress
FROM cloudtrail_logs
WHERE
  year = '2024'
  AND month = '01'
  AND day = '15'
  AND eventname = 'AssumeRole'
  AND errorcode IS NULL
ORDER BY eventtime ASC;
```

攻擊者通常會串多個 AssumeRole（role chaining）來模糊追蹤路徑。把結果按 `eventtime` ASC 排序，就能重建「誰 assume 了哪個 role」的完整鏈條。把 `assumed_role` 和 `caller_arn` 做 self-join 可以畫出 role 關係圖。

### SQL 3：找特定 IP 的所有 API 呼叫

```sql
SELECT
  eventtime,
  eventsource,
  eventname,
  useridentity.arn AS caller_arn,
  awsregion,
  errorcode,
  json_extract_scalar(requestparameters, '$') AS params_preview
FROM cloudtrail_logs
WHERE
  year = '2024'
  AND month = '01'
  AND day = '15'
  AND sourceipaddress = '203.0.113.42'
ORDER BY eventtime ASC
LIMIT 1000;
```

在 IR 過程中，如果 GuardDuty finding 裡出現可疑 IP，這條 SQL 能找出那個 IP 在那一天做了什麼。`LIMIT 1000` 是安全措施，避免結果過多。`params_preview` 只取前幾個字元，不需要看完整 JSON（完整 JSON 可能很大）。

---

## 雲端 IR 流程

發現攻擊後的標準回應五步驟：

```
偵測 → 分類 → 遏制 → 根因分析 → 復原
  │       │       │        │         │
GuardDuty  P0/P1  隔離    Athena   換 key
EventBridge 分級  封憑證   SQL     修 policy
  Alarm         取證快照  CloudTrail 關漏洞
                         Lake
```

### 步驟 1：偵測

GuardDuty finding 或 EventBridge alarm 觸發 PagerDuty/Slack 通知。SOC 工程師收到通知，開始調查。

### 步驟 2：分類

確認 finding 的嚴重性和範圍：

- 被攻陷的 principal 是 IAM user 還是 role？
- role 被哪些 EC2/Lambda/ECS task 使用？
- 攻擊範圍是單一帳號還是跨帳號？
- 資料有無外洩跡象（Exfiltration finding）？

P0：確認攻陷 + 有資料外洩跡象 → 立即遏制
P1：確認攻陷 + 無外洩 → 1 小時內遏制
P2：可疑但未確認 → 4 小時內調查

### 步驟 3：遏制

**a. 隔離憑證：掛 DenyAll inline policy**

最快的辦法，不需要刪除 user，不影響其他 user，可逆：

```bash
# 對可疑 IAM user 附加 DenyAll inline policy
aws iam put-user-policy \
  --user-name compromised-user \
  --policy-name DenyAll-Quarantine \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Deny",
      "Action": "*",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": ["*"]
        }
      }
    }]
  }'
```

這條 Deny policy 會覆蓋所有 Allow，即使 user 有 AdministratorAccess 也一樣無效（Deny 優先）。

**b. Disable access key**

```bash
# 找出該 user 所有 access key
aws iam list-access-keys --user-name compromised-user

# Disable 所有 key
aws iam update-access-key \
  --user-name compromised-user \
  --access-key-id AKIAIOSFODNN7EXAMPLE \
  --status Inactive
```

**c. Revoke role active sessions（STS）**

如果被攻陷的是 role，需要讓現有的 STS token 失效。STS token 有 TTL，直接改 role 的 assume-role policy 不影響已發出的 token。正確做法是在 role 的 trust policy 裡加一個 condition，拒絕 `sts:RevokeSession` 之前發出的 token：

```bash
# Step 1：記錄現在時間（UTC ISO 8601）
REVOKE_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Step 2：取出現有 trust policy
aws iam get-role --role-name compromised-role \
  --query 'Role.AssumeRolePolicyDocument' > current-trust-policy.json

# Step 3：在 inline policy 裡加 revoke condition
# 這個 policy 拒絕所有 aws:TokenIssueTime 早於 REVOKE_TIME 的請求
aws iam put-role-policy \
  --role-name compromised-role \
  --policy-name RevokeOldSessions \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Deny\",
      \"Action\": \"*\",
      \"Resource\": \"*\",
      \"Condition\": {
        \"DateLessThan\": {
          \"aws:TokenIssueTime\": \"${REVOKE_TIME}\"
        }
      }
    }]
  }"
```

**注意**：AWS 有個對應的 `iam:RevokeMFADevice` 和 `sts:RevokeMFADevice`，但對 programmatic access 的 STS token 沒有直接 revoke 的 API。上面的 Deny policy 是目前最實用的做法。

**d. 取證快照**

```bash
# EC2：建立 EBS snapshot 保留記憶體狀態（記憶體需要另外 dump）
aws ec2 create-snapshot \
  --volume-id vol-1234567890abcdef0 \
  --description "IR-forensics-$(date +%Y%m%d)"

# 匯出 CloudTrail events 到 S3（指定時間範圍）
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=Username,AttributeValue=compromised-user \
  --start-time "2024-01-15T00:00:00Z" \
  --end-time "2024-01-15T23:59:59Z" \
  --query 'Events[*]' > ir-events-export.json
```

### 步驟 4：根因分析

用 Athena SQL 重建完整攻擊鏈。關鍵問題：

- 憑證什麼時候首次被使用？首次來自哪個 IP？
- 攻擊者做了哪些 AssumeRole？
- 有沒有建立新的 IAM entity（user/role/access key）？
- 有沒有修改過 CloudTrail 設定？
- 有沒有存取過 S3 / Secrets Manager / SSM Parameter Store？

### 步驟 5：復原

- 建新的 access key，取代被攻陷的舊 key
- 修正讓攻擊成立的 IAM policy 漏洞（例如過度寬鬆的 passRole 權限）
- 確認 CloudTrail 仍然正常運作（`aws cloudtrail get-trail-status`）
- 解除 DenyAll quarantine policy（前提是確認漏洞已修）
- 把 IR 過程寫成事後報告（Post-Incident Review），更新 runbook

---

## MITRE ATT&CK for Cloud 對應

把偵測規則對應到 MITRE technique，讓 finding 從「某個告警」升到「攻擊戰術」層面，方便跨組織溝通和趨勢分析：

| CloudTrail 偵測規則 | MITRE Technique | 戰術（Tactic） |
|--------------------|----------------|---------------|
| AssumeRole 從未知 IP | T1078 Valid Accounts | Initial Access / Defense Evasion |
| StopLogging / DeleteTrail | T1562.008 Disable Cloud Logs | Defense Evasion |
| CreateUser / CreateAccessKey | T1136.003 Cloud Account | Persistence |
| GetSecretValue 大量呼叫 | T1528 Steal Application Access Token | Credential Access |
| AttachRolePolicy / PassRole | T1098.003 Additional Cloud Credentials | Privilege Escalation |
| PutBucketPolicy（公開） | T1530 Data from Cloud Storage | Collection |
| DescribeInstances / ListBuckets 大量 | T1580 Cloud Infrastructure Discovery | Discovery |

對應 MITRE 的實際好處：

1. **偵測覆蓋率可量化**：可以問「我們對 T1562 有幾條偵測規則？」而不是「我們對 CloudTrail 有幾條規則？」
2. **攻擊模擬可對焦**：Red team 演練可以針對特定 technique，驗證藍隊的偵測有沒有覆蓋到
3. **跨工具整合**：GuardDuty finding、Splunk alert、Falco rule 都可以用同一個 technique ID 關聯起來

---

## 踩雷集錦

**1. CloudTrail 每個帳號最多 5 條 trail**

`aws cloudtrail create-trail` 第 6 次會報錯 `MaximumNumberOfTrailsExceededException`。如果帳號裡已有 4 條 trail（有些是自動建的，例如 AWS Config、Security Hub 也會用 trail），就只剩 1 條可以自己建。解法：先 `aws cloudtrail describe-trails` 看清楚再動手。

**2. GuardDuty finding 有 5-15 分鐘延遲**

GuardDuty 不是即時的。攻擊者在 08:00 呼叫 StopLogging，GuardDuty 的 finding 可能在 08:10 才出現。這個延遲窗口裡攻擊者可以做很多事。所以「即時」偵測不能只靠 GuardDuty，要搭配 EventBridge rule（延遲 1-3 分鐘）。

**3. EventBridge 每個帳號每個 region 最多 2000 條 rule**

多數情況夠用，但如果用 CDK/Terraform 自動生成大量 rule（例如每個服務一條），很容易撞到上限。這個上限可以申請提高，但需要提前規劃，不能等到 `LimitExceededException` 才發現。

**4. Athena 查 CloudTrail 一定要指定 partition，否則全表掃描費用爆表**

S3 上的 CloudTrail log 按 `year/month/day/region` 分 partition。如果 SQL 的 WHERE 條件沒有包含 partition 欄位，Athena 會掃描所有歷史資料。一個活躍帳號一年的 CloudTrail 可能有數十 GB，全掃一次費用可能超過 $5-10 美元，跑幾十次分析費用就很可觀。正確做法是 WHERE 條件永遠包含 `year`, `month`, `day`。

**5. Revoke role session 不能立即讓 STS token 失效**

這是最常見的 IR 誤解。攻擊者拿到 STS temporary token 後，即使你：

- 刪除了 IAM role
- 改了 trust policy
- 呼叫了 `iam:RevokeMFADevice`

已發出的 STS token 在 TTL 到期前（最長 12 小時）仍然有效。正確遏制方式是用 `DateLessThan: aws:TokenIssueTime` 條件的 Deny policy（上面 IR 步驟 c 的做法），這樣才能讓 STS token 即時失效。很多工程師遮蔽了 IAM role 以為安全，結果攻擊者繼續用 STS token 操作了 8 小時。

---

## 進階延伸

### Amazon Security Lake（OCSF 統一格式）

Security Lake 把來自不同來源的安全日誌（CloudTrail、VPC Flow Logs、GuardDuty、Route 53、Security Hub、以及第三方工具）統一轉換成 OCSF（Open Cybersecurity Schema Framework）格式，存進你帳號的 S3，再用 Athena 或 OpenSearch 查詢。

好處是不用手動處理每個工具的 schema 差異。壞處是費用不低，而且 OCSF 的 schema 還在演進中，某些欄位的映射不夠直觀。

### Amazon Detective（調查視覺化）

Detective 自動把 GuardDuty finding、CloudTrail events、VPC Flow Logs 組成關係圖，讓你視覺化地追蹤「某個 IP 連過哪些 EC2」、「某個 role 被哪些 principal assume 過」。特別適合 IR 的根因分析階段，不需要手寫 SQL 就能看到資源關係。Detective 依 GuardDuty member account 計費，大帳號費用不低。

### CloudTrail Insights

Insights 分析 Management event 的呼叫頻率，自動學習正常基線，當某個 API 的呼叫頻率突破閾值就建立 Insights event。例如正常情況下每小時 5 次 `GetSecretValue`，某小時突然有 500 次，就觸發 Insights event。

Insights event 存在和 management event 同一個 S3 bucket，路徑裡有 `CloudTrail-Insight/` 前綴，可以用 Athena 查。也可以設 EventBridge rule 訂閱 `detail-type: "AWS Insight via CloudTrail"` 來即時告警。

---

## 本章重點整理

- CloudTrail 是雲端偵測的資料來源基礎；Multi-region trail + log file validation 是最低要求
- CloudTrail event 分三類：Management（免費）、Data（資料面 API，需要才開）、Insights（異常頻率偵測）
- CloudTrail Lake 讓你直接用 SQL 查詢 events，省去 Glue + Athena 的建置成本
- GuardDuty 有 5-15 分鐘延遲，finding 按 Policy/Recon/Discovery/PrivEsc/Persistence/Stealth/Exfiltration 分類；調查 finding 時看 principal、IP、時間三個欄位
- 即時告警用 EventBridge rule；偵測 StopLogging 是優先級最高的規則
- Athena SQL 查 CloudTrail 一定要帶 partition 條件（year/month/day）
- IR 遏制最快手法：掛 DenyAll inline policy；Revoke STS session 要用 `DateLessThan: aws:TokenIssueTime` Deny condition，不是改 trust policy
- MITRE ATT&CK for Cloud 讓 finding 從單一告警升到攻擊戰術層面，量化偵測覆蓋率

---

## 自我檢核

1. Management Events 和 Data Events 的區別是什麼？為什麼不建議預設開啟所有 Data Events？
2. GuardDuty 找到 `Stealth:IAMUser/CloudTrailLoggingDisabled` finding，你第一個動作是什麼？如何確認 trail 目前狀態？
3. 攻擊者拿到一個 IAM role 的 STS token，你立刻把那個 role 刪掉。攻擊者還能繼續操作嗎？為什麼？
4. Athena 查 CloudTrail 時，如果忘記指定 year/month/day partition，會發生什麼？費用從哪裡來？
5. 如何用一條 EventBridge rule 同時偵測 `StopLogging` 和 `DeleteTrail` 這兩個 API 呼叫？
6. MITRE T1562.008 對應哪些 CloudTrail 偵測規則？為什麼要把告警對應到 MITRE technique？

---

## 延伸閱讀

1. **AWS CloudTrail Best Practices** — AWS 官方文件，涵蓋 trail 設定、log 保護、multi-region 建議。https://docs.aws.amazon.com/awscloudtrail/latest/userguide/best-practices-security.html
2. **Amazon GuardDuty Finding Types** — 完整的 finding 類型清單，每個 finding 都有觸發條件說明和建議處置。https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html
3. **AWS Security Incident Response Guide** — AWS 官方 IR 指南，含遏制、取證、復原各階段的 AWS 服務對應。https://docs.aws.amazon.com/whitepapers/latest/aws-security-incident-response-guide/aws-security-incident-response-guide.html
4. **MITRE ATT&CK for Cloud** — 雲端攻擊技術完整矩陣，含 AWS/Azure/GCP 對應。https://attack.mitre.org/matrices/enterprise/cloud/
5. **Stratus Red Team** — DataDog 開源的雲端攻擊模擬工具，可以在自己帳號跑各種 ATT&CK technique，驗證偵測規則是否有效。https://github.com/DataDog/stratus-red-team

---

攻擊者在雲端的每一步都留下 API 足跡，偵測工程師的工作是讓這些足跡在被清除之前變成告警。下一章把視角再拉高一層，從個別偵測規則到系統性的威脅建模框架。

→ [Ch 37 威脅建模與框架：MITRE ATT&CK for Cloud / CIS Benchmark](./37-threat-modeling-frameworks.md)
