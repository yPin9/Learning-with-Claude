# Ch 25 — 雲 IR：CloudTrail / GuardDuty / 身分鑑識

> 目標：掌握 AWS 主線雲端鑑識的核心思路——哪些 log 是你的眼睛、如何從 IAM 事件重建攻擊者路徑、在 ephemeral 的雲環境裡用正確方法保全證據、並把自己熟悉的攻擊手法反過來用防守視角解讀。

---

## 為什麼雲 IR 跟地端鑑識根本不同

地端鑑識的核心假設是「有機器可以拿」：你可以拔磁碟、做 bit-by-bit image、跑 Volatility 拉記憶體。雲端把這個假設整個推翻。

**三個根本差異：**

1. **沒有你能掌控的實體硬體**。EC2 跑在 AWS 的 hypervisor 上；你拿不到 host 記憶體、看不到鄰居 VM、無法插 USB 到 physical host。
2. **Ephemeral 是設計不是意外**。Auto Scaling Group 可能在事件後幾分鐘就把那台 instance terminate 掉，日誌可能在 CloudWatch 上有 retention policy，S3 bucket 可能有 lifecycle 幾天就刪。你必須比 orchestrator 更快。
3. **身分（IAM）是最重要的攻擊面**。地端 IR 問「哪個進程執行了什麼」；雲端 IR 問「哪個 IAM 身分呼叫了什麼 API」。憑證（Access Key、短期 STS token）才是攻擊者真正需要的，磁碟上的 payload 反而是次要的。

正因為缺少傳統 artifact，雲 IR 的主要眼睛是**控制平面 log（API 呼叫記錄）**。你能還原的事件鏈，幾乎全靠這些 log。

---

## 核心 Log 源全景

### CloudTrail：控制平面的黑匣子

CloudTrail 是 AWS 的核心審計 log，記錄所有對 AWS 服務的 API 呼叫。每一筆 CloudTrail 事件回答的是：**誰（who）、在什麼時間（when）、從哪裡（where）、對什麼資源（what）、呼叫了什麼 API（which API）、結果是成功還是失敗（outcome）**。

CloudTrail 分兩大類：

| 類型 | 說明 | 預設開啟 |
|------|------|---------|
| **Management events（管理事件）** | 對 AWS 服務本身的操作：建立/刪除 IAM user、啟動 EC2、修改 Security Group | 預設開啟，保留 90 天在 Event History |
| **Data events（資料事件）** | 對資源「裡面」的存取：S3 GetObject/PutObject、Lambda invoke、DynamoDB GetItem | **預設關閉**，需額外開啟，費用較高 |
| **Insights events** | 異常 API 呼叫量偵測（需開啟） | 預設關閉 |

**IR 前最重要的確認**：S3 data events 有沒有開？如果攻擊者做了大量 GetObject（資料外洩），沒有 data events 你只能看到管理操作，看不見資料被拿走。

### CloudTrail 事件欄位解析

```json
// 示意 JSON，欄位依 CloudTrail 版本而異
{
  "eventTime": "2024-03-15T10:23:45Z",
  "eventSource": "iam.amazonaws.com",
  "eventName": "CreateAccessKey",
  "awsRegion": "us-east-1",
  "sourceIPAddress": "198.51.100.42",
  "userAgent": "aws-cli/2.13.0 Python/3.11.4",
  "requestParameters": {
    "userName": "dev-pipeline"
  },
  "responseElements": {
    "accessKey": {
      "userName": "dev-pipeline",
      "accessKeyId": "AKIAIOSFODNN7EXAMPLE",
      "status": "Active"
    }
  },
  "userIdentity": {
    "type": "AssumedRole",
    "principalId": "AROAIOSFODNN7EXAMPLE:attacker-session",
    "arn": "arn:aws:sts::123456789012:assumed-role/DevOps-Role/attacker-session",
    "sessionContext": {
      "sessionIssuer": {
        "type": "Role",
        "arn": "arn:aws:iam::123456789012:role/DevOps-Role"
      },
      "webIdFederationData": {},
      "attributes": {
        "mfaAuthenticated": "false",
        "creationTime": "2024-03-15T09:00:00Z"
      }
    }
  },
  "errorCode": null,
  "errorMessage": null
}
```

**最重要的欄位：**
- `userIdentity`：誰打的。`type` 可能是 `IAMUser`、`AssumedRole`、`Root`、`AWSService`。AssumedRole 型的要追 `sessionIssuer` 才知道原始身分。
- `eventName`：呼叫了什麼 API。
- `sourceIPAddress`：來源 IP。如果是 AWS 服務代為呼叫，會看到 `AWS Internal`。
- `errorCode` / `errorMessage`：失敗事件。大量失敗呼叫通常是 enumeration 或暴力測試 policy。

### VPC Flow Logs：網路層視角

VPC Flow Logs 記錄進出 VPC 的網路流量後設資料（srcaddr、dstaddr、srcport、dstport、protocol、action、bytes、packets）。注意它**不記錄 payload 內容**，只記錄後設資料，類似 NetFlow。

IR 場景下，VPC Flow Logs 能回答：
- 這個 instance 有沒有對外部 C2 建立連線？
- 資料外洩的量級（bytes）
- 哪個 IP 掃了哪些 port

### GuardDuty：AWS 託管的威脅偵測

GuardDuty 是 AWS 的 managed threat detection 服務，持續分析 CloudTrail、VPC Flow Logs、DNS query logs，產生結構化的 finding。

GuardDuty finding 有命名規則：`ThreatPurpose:ResourceTypeAffected/DetectionMechanism`

常見 finding 類型（直接從 GuardDuty 文件命名）：

| Finding Type | 意義 |
|---|---|
| `UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B` | 從不尋常地點登入 Console |
| `Recon:IAMUser/UserPermissions` | 大量 IAM 描述/列舉 |
| `Persistence:IAMUser/UserCreated` | 新 IAM user 建立 |
| `PrivilegeEscalation:IAMUser/AdministrativePermissions` | 嘗試給予 Admin 權限 |
| `Exfiltration:S3/MaliciousIPCaller` | 從已知惡意 IP 存取 S3 |
| `CryptoCurrency:EC2/BitcoinTool.B!DNS` | EC2 解析礦池 DNS |
| `Trojan:EC2/BlackholeTraffic` | EC2 流量打向 blackhole IP |

GuardDuty **不是 log，是 finding**。每個 finding 背後已經做了部分相關性分析。IR 時把 GuardDuty finding 當作**入口**，再往 CloudTrail 裡挖細節。

### CloudWatch Logs：應用層與 OS 層 log

EC2 instance 上的 `/var/log/auth.log`、應用程式 log、VPC Flow Logs 都可以送進 CloudWatch Logs。IR 時，如果有裝 CloudWatch Agent，這是你找 OS 層動作的地方。

---

## 身分鑑識：IAM 事件才是主戰場

### AssumeRole 鏈追蹤

雲端攻擊幾乎都涉及身分提權或橫向移動，而 AWS 的身分系統是 role chaining。你看到的 API 呼叫者未必是「源頭」——要沿著 AssumeRole 鏈一路追。

**典型攻擊路徑（你在 cloud_container_security 課打過的）在 CloudTrail 的樣子：**

```
1. 攻擊者取得洩漏的 IAM access key (AKIA...)
2. 呼叫 GetCallerIdentity → 確認身分
3. 呼叫 ListRoles / ListPolicies / GetPolicy → enumeration
4. 呼叫 AssumeRole 取得高權限 role
5. 用 STS token 呼叫 CreateAccessKey 建立持久化 key
6. 呼叫 ListBuckets / ListObjects / GetObject → 資料外洩
```

每一步都留下 CloudTrail 事件。防守方要做的是把這些事件串成一條時間線。

**追蹤方式：**
- 從 `sourceIPAddress` 或 `userIdentity.principalId` 出發，找同一來源的所有事件
- 注意 `AssumeRole` 事件的 `requestParameters.roleArn` 和 `responseElements.credentials`，下一個事件會用剛產生的 session token
- STS session name（`sessionContext.sessionIssuer`）在 IAM user assume role 時可以自訂，攻擊者有時會偽裝成看起來正常的名字

### 重要 IAM 相關 CloudTrail 事件

| 事件名稱 | 意義 | IR 關注點 |
|---|---|---|
| `ConsoleLogin` | AWS Console 登入 | `additionalEventData.MFAUsed`：沒 MFA 就警惕 |
| `AssumeRole` | 取得 role 臨時憑證 | 來源 IP、目標 role ARN |
| `AssumeRoleWithWebIdentity` | OIDC/federated identity | SaaS supply chain 攻擊常見路徑 |
| `CreateAccessKey` | 建立永久 access key | 建立對象是誰、呼叫者是誰 |
| `AttachUserPolicy` / `AttachRolePolicy` | 賦予 policy | 是否附加了 AdministratorAccess |
| `PutUserPolicy` / `PutRolePolicy` | 直接 inline policy | 更難追蹤的 policy 提權 |
| `CreateLoginProfile` | 給 IAM user 設 console 密碼 | 原本只有 key-based 的 user 突然能登 Console |
| `GetSecretValue` | 存取 Secrets Manager | 有沒有在正常的 service account 以外存取 |
| `GetPasswordData` | 取得 Windows EC2 密碼 | 幾乎不會在正常流程出現 |

### 範例：憑證外洩後的 enumeration

```json
// 時序：同一 source IP，短時間內大量 List/Describe 呼叫（示意，欄位依 CloudTrail 版本而異）
// T+0s
{ "eventName": "GetCallerIdentity", "sourceIPAddress": "203.0.113.77" }
// T+5s
{ "eventName": "ListUsers", "sourceIPAddress": "203.0.113.77" }
// T+7s
{ "eventName": "ListRoles", "sourceIPAddress": "203.0.113.77" }
// T+9s
{ "eventName": "ListPolicies", "sourceIPAddress": "203.0.113.77" }
// T+12s
{ "eventName": "GetAccountAuthorizationDetails", "sourceIPAddress": "203.0.113.77" }
// T+15s
{ "eventName": "ListBuckets", "sourceIPAddress": "203.0.113.77" }
```

`GetAccountAuthorizationDetails` 是 IR 的警示燈——這個 API 一次回傳整個帳號的 IAM 結構，是攻擊者做全帳號 enumeration 最常用的捷徑，正常應用程式幾乎不呼叫。

### 範例：S3 資料外洩偵測

```json
// 需要 S3 data events 開啟才看得到（示意 JSON，欄位依服務版本而異）
{
  "eventName": "GetObject",
  "eventSource": "s3.amazonaws.com",
  "requestParameters": {
    "bucketName": "company-confidential-2024",
    "key": "financial/Q4-report.xlsx"
  },
  "userIdentity": {
    "type": "AssumedRole",
    "principalId": "AROAIOSFODNN7EXAMPLE:exfil-session",
    "sessionContext": {
      "sessionIssuer": {
        "arn": "arn:aws:iam::123456789012:role/DevOps-Role"
      }
    }
  },
  "sourceIPAddress": "203.0.113.77"
}
```

如果在短時間內看到幾百到幾千筆同一 `principalId` 的 `GetObject`，且 `sourceIPAddress` 不是 AWS 服務 IP（`AWS Internal`），這就是典型的 S3 exfil pattern。配合 `bytesTransferredOut` 計算洩漏量級。

### 範例：持久化——建立後門 IAM user

```json
// 示意 JSON，欄位依 CloudTrail 版本而異
{
  "eventTime": "2024-03-15T11:45:00Z",
  "eventName": "CreateUser",
  "requestParameters": { "userName": "backup-svc-prod" },
  "userIdentity": {
    "type": "AssumedRole",
    "arn": "arn:aws:sts::123456789012:assumed-role/DevOps-Role/attacker-session"
  }
}
// 緊接著
{
  "eventTime": "2024-03-15T11:45:05Z",
  "eventName": "CreateAccessKey",
  "requestParameters": { "userName": "backup-svc-prod" }
}
// 再接著
{
  "eventTime": "2024-03-15T11:45:10Z",
  "eventName": "AttachUserPolicy",
  "requestParameters": {
    "userName": "backup-svc-prod",
    "policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"
  }
}
```

三個事件的連鎖是教科書級的後門建立：建 user → 建 key → 附 Admin policy。即使攻擊者之後失去對原始 role 的存取，backup-svc-prod 的 key 依然有效。

---

## 雲 IR 流程：隔離、快照、保全

### 1. 不要急著 terminate

第一反應是把被入侵的 EC2 砍掉，這是錯的。先做：

- **隔離**：修改 Security Group，把所有 inbound/outbound 規則移除，只保留你分析用的 IP。EC2 還活著但網路切斷。
- **EBS 快照**：對所有 EBS volume 做快照（`CreateSnapshot` API）。這是雲端版的磁碟 image。快照完成後你才能安全地 terminate。
- **記憶體**（如果有需要）：連進去用 `avml` 或 `LiME` 做記憶體 dump，存到 S3。這個視窗很短，instance terminate 後就沒了。

### 2. 保全 log

CloudTrail 預設 90 天，但你要的是原始 JSON 檔案。確認 CloudTrail trail 有設 S3 bucket 接收，然後立刻把相關時間範圍的 log 複製到另一個隔離的 S3 bucket（設 Object Lock，防止被竄改）。

如果攻擊者有足夠的 IAM 權限，他可能已經嘗試停掉 CloudTrail 或刪除 S3 log——這動作本身也是 CloudTrail 事件（`StopLogging`、`DeleteTrail`），所以要先確認 trail 是否完整。

### 3. 身分相關的緊急處置

- 立刻停用（不要刪除）所有可疑的 access key：`UpdateAccessKey` 設 `Inactive`。刪掉會毀掉部分證據鏈。
- 撤銷可疑 role 的所有 active session：在 role 上加一個 deny-all inline policy，搭配時間條件（`aws:TokenIssueTime`），讓所有在某個時間點前發的 STS token 全部失效。
- 輪換所有「可能被看到」的 secret（Secrets Manager、Parameter Store、硬寫在 Lambda 環境變數裡的）。

### 4. 時間線建立

把 CloudTrail JSON 倒進 Athena 或 OpenSearch，用 SQL 查詢還原事件序列。典型的起點查詢：

```sql
-- 示意，非真實可執行 SQL
SELECT eventTime, eventName, userIdentity.arn, sourceIPAddress, errorCode
FROM cloudtrail_logs
WHERE sourceIPAddress = '203.0.113.77'
   OR userIdentity.principalId LIKE '%attacker-session%'
ORDER BY eventTime;
```

---

## Azure / GCP 對照

你在 cloud_container_security 課學的攻擊面跨三大雲，防守視角也要對照。

| 面向 | AWS | Azure | GCP |
|---|---|---|---|
| 控制平面 log | CloudTrail | Azure Activity Log | Cloud Audit Logs（Admin Activity） |
| 資料平面 log | S3/Lambda data events | Azure Resource Logs（診斷設定） | Cloud Audit Logs（Data Access，需開啟） |
| 身分系統 | IAM + STS | Azure AD（Entra ID） | IAM + Workload Identity |
| 身分 log | CloudTrail IAM 事件 | Azure AD Sign-in Logs / Audit Logs | Cloud Identity audit logs |
| 託管威脅偵測 | GuardDuty | Microsoft Defender for Cloud | Security Command Center |
| 網路 log | VPC Flow Logs | NSG Flow Logs | VPC Flow Logs |

Azure 的 IR 常從 **Azure AD Audit Logs** 和 **Sign-in Logs** 下手，對應 AWS 的 IAM + ConsoleLogin 事件。GCP 的 **Cloud Audit Logs** 架構與 AWS CloudTrail 最相似，但 Data Access log 預設也是關閉的（計費考量），這是攻擊者喜歡利用的盲點。

---

## 踩雷紀錄

1. **誤以為 90 天就夠**：CloudTrail Event History 只有 90 天，但如果沒有設 trail 把 log 送到 S3，多區域事件、data events、Insights events 都看不到。IR 時第一件事先確認 trail 設定，別等需要時才發現沒有。

2. **AssumeRole 鏈追到一半斷了**：STS 短期 token 的呼叫者在 CloudTrail 裡顯示的是 `AssumedRole` 型，session name 可以被呼叫者自訂，不能輕信。要沿著 `sessionContext.sessionIssuer` 一路追到最底層的 IAM entity。

3. **誤刪 access key**：急著清理時直接刪 access key，結果丟失了 key 的 metadata（建立時間、最後使用時間）。正確做法是先設 `Inactive`，確認 IR 完成後再刪。

4. **S3 data events 沒開**：事後查 S3 exfil 時才發現 data events 一直沒開，根本看不到 `GetObject`。只能靠 VPC Flow Logs 推算流量量級，無法知道拿了哪些具體檔案。補救：立刻開，但歷史 log 補不回來。

5. **GuardDuty finding 當 log 用**：GuardDuty finding 有最短 1 分鐘的延遲，且不是每個 API 呼叫都會觸發 finding。不能只看 GuardDuty，要把它當「提示」，主要分析還是要在 CloudTrail 原始 log 裡做。

---

## 進階延伸

- **AWS Security Hub**：把 GuardDuty、Inspector、Macie 等 findings 整合到單一面板，也可以接 Sigma 轉成的 detection rules。
- **Macie**：S3 PII 資料偵測，能幫你評估 exfiltrated data 的嚴重程度。
- **CloudTrail Lake**：AWS 託管的 CloudTrail 查詢環境，直接用 SQL 查詢，比自建 Athena 省設定。
- **ScoutSuite / Prowler**：雲端設定稽核工具，IR 後期評估帳號整體安全姿態。
- **IR Runbook 自動化**：用 AWS Systems Manager Automation 做隔離動作（移除 Security Group rule、建快照），縮短 MTTC（Mean Time to Contain）。

---

## 本章重點整理

- 雲 IR 沒有實體磁碟，主要眼睛是 CloudTrail（控制平面）和 VPC Flow Logs（網路）。
- Management events 預設開，S3/Lambda data events 預設關，沒開就看不到資料外洩細節。
- 身分鑑識的核心：追 AssumeRole 鏈、看 `userIdentity.sessionIssuer`、找可疑 IAM user/access key 建立事件。
- `GetAccountAuthorizationDetails`、`CreateAccessKey`、`AttachUserPolicy` 接 `AdministratorAccess` 是高 signal 的 IOA。
- 隔離順序：改 Security Group → EBS 快照 → 保全 CloudTrail log → 停用 key/撤銷 token。
- GuardDuty 是入口，不是終點；真正的分析在 CloudTrail 原始 JSON。

## 自我檢核

不看筆記，回答：

1. CloudTrail management events 和 data events 的差別是什麼？S3 GetObject 屬於哪種？
2. `userIdentity.type` 是 `AssumedRole` 時，怎麼找到真正的「呼叫源頭」身分？
3. 攻擊者建立後門 IAM user 的三步操作，在 CloudTrail 會留下哪三個 `eventName`？
4. 為什麼 IR 時要先改 Security Group 而不是直接 terminate EC2？
5. GuardDuty finding `Recon:IAMUser/UserPermissions` 代表什麼？你要去哪裡找細節？

## 延伸閱讀

1. **AWS Security Incident Response Guide**（[docs.aws.amazon.com/whitepapers/latest/aws-security-incident-response-guide](https://docs.aws.amazon.com/whitepapers/latest/aws-security-incident-response-guide/welcome.html)）——AWS 官方 IR 白皮書，涵蓋從準備到事後的完整框架，CloudTrail 保全、隔離步驟都有官方建議，值得完整通讀一遍。
2. **CloudTrail 事件參考**（[docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference.html](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference.html)）——每個 `eventName` 的欄位定義，IR 查 log 時遇到不確定的欄位就翻這裡。
3. **GuardDuty Finding Types**（[docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html)）——完整 finding 類型列表，每個都有觸發條件說明，對映 ATT&CK 技術。
4. **The DFIR Report — Cloud Cases**（[thedfirreport.com](https://thedfirreport.com/)，搜尋 "AWS" 或 "cloud"）——真實入侵案例的完整拆解，看職業藍隊怎麼從 CloudTrail 還原攻擊鏈，細節遠比教科書有說服力。
5. **Hacking the Cloud**（[hackingthe.cloud](https://hackingthe.cloud/)）——站在攻擊者角度的雲端技術細節，但每個技術都能反推防守偵測點。你在 cloud_container_security 課學的攻擊，這裡有更多細節，翻回來對照防守 log 很有用。

---

→ [下一章：Ch 26 容器 / K8s IR](./26-container-k8s-ir.md)
