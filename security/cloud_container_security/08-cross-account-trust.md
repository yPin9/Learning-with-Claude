# Ch 8 — 跨帳號與信任攻擊

> **目標**：理解 AssumeRole 跨帳號信任鏈的機制，掌握 Confused Deputy、Wildcard Principal、Role Chaining 等攻擊路徑，並知道如何用 External ID 和具體 Principal 縮小攻擊面。

---

## 為什麼需要跨帳號信任

AWS 多帳號架構（Multi-Account）是業界標準實踐：組織會把生產環境、測試環境、日誌帳號、安全工具帳號分開。這些帳號之間要互通，不可能每個帳號都開一組 IAM User 憑證——那樣金鑰管理會是惡夢。

解法是 **AssumeRole（角色承擔）**：帳號 A 的身分，透過 STS（Security Token Service）臨時「借用」帳號 B 的角色，取得短期憑證（ASIA 開頭，最長 12 小時）。整個機制靠 **Trust Policy（信任政策）** 控制誰有資格借用。

問題在於：Trust Policy 寫錯，攻擊者就能借用你帳號裡的高權限角色——完全合法，AWS 不會擋，因為你自己開了門。

---

## 先建直覺

```
帳號 A (123456789012)              帳號 B (999888777666)
┌──────────────────────────┐      ┌──────────────────────────┐
│  你的身分 dev-bot         │      │  role: cross-acct-admin  │
│  (低權限)                 │      │                          │
│                          │      │  Trust Policy:           │
│  aws sts assume-role     │─────►│  Principal:              │
│  --role-arn arn:aws:iam  │      │    arn:aws:iam::         │
│  ::999888777666:role/... │      │    123456789012:root      │
│                          │◄─────│                          │
│  拿回臨時憑證 (ASIA...)   │      │  ← 帳號 A 的 root 可信任  │
└──────────────────────────┘      └──────────────────────────┘

流程拆解：
  1. A 的 dev-bot 發出 AssumeRole 請求給 STS
  2. STS 查 B 帳號的 role cross-acct-admin 的 Trust Policy
  3. Trust Policy 說「信任 123456789012:root」→ A 帳號任何身分都符合
  4. STS 核發臨時憑證：AccessKeyId (ASIA...) + SecretAccessKey + SessionToken
  5. dev-bot 用這組臨時憑證操作帳號 B 的資源
```

重點：信任關係的授權放在**被假扮的角色（帳號 B）**那邊，不在帳號 A。帳號 A 的身分只要有 `sts:AssumeRole` 權限，就能發起請求；成不成功看帳號 B 的 Trust Policy 是否放行。

---

## 底層機制

### Trust Policy 結構

每個 IAM Role 有兩個政策：

1. **Permission Policy**：這個 role 能做什麼（操作哪些資源）
2. **Trust Policy**：誰能 assume 這個 role

Trust Policy 是一個 Resource-Based Policy（資源型政策），綁在 role 本身，格式與 IAM Policy 相同，但 `Principal` 是必填欄位。

**最小權限的好 Trust Policy：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:role/ci-deploy-role"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

這只允許帳號 A 的 `ci-deploy-role` 這一個具體角色發起 AssumeRole。

**開太寬的壞 Trust Policy：**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:root"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

`:root` 代表整個帳號——帳號 A 的**任何** IAM 身分（只要自己有 `sts:AssumeRole` 權限）都能 assume 這個 role。這在 SaaS 廠商整合場景裡幾乎是標配，也是最常見的過寬信任。

---

## 具體範例

### 範例一：跨帳號 AssumeRole，含 External ID（成功）

**本段未實測，為理論預期行為**。自驗方法：在帳號 B 建立一個測試 role，Trust Policy 加上 ExternalId Condition，從帳號 A 的 shell 執行下列指令，確認回傳 `Credentials` 物件。

```bash
# 從帳號 A（123456789012）assume 帳號 B（999888777666）的 role
aws sts assume-role \
  --role-arn arn:aws:iam::999888777666:role/monitoring-role \
  --role-session-name audit-$(date +%s) \
  --external-id "CUSTOMER-SECRET-ABC123"
```

預期輸出（成功）：

```json
{
  "Credentials": {
    "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
    "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "SessionToken": "AQoXnyc4lcK4w4EXAMPLE...",
    "Expiration": "2026-08-01T12:00:00+00:00"
  },
  "AssumedRoleUser": {
    "AssumedRoleId": "AROAIOSFODNN7EXAMPLE:audit-1754006400",
    "Arn": "arn:aws:iam::999888777666:assumed-role/monitoring-role/audit-1754006400"
  }
}
```

取得憑證後，匯出成環境變數或寫入 profile：

```bash
# 把臨時憑證存到 profile
aws configure set aws_access_key_id ASIAIOSFODNN7EXAMPLE --profile assumed
aws configure set aws_secret_access_key wJalrXUtnFEMI/... --profile assumed
aws configure set aws_session_token AQoXnyc4lcK4w4EXAMPLE... --profile assumed

# 確認現在的身分是帳號 B 的 assumed-role
aws sts get-caller-identity --profile assumed
```

回傳的 ARN 會是 `arn:aws:iam::999888777666:assumed-role/monitoring-role/audit-XXXXX`，確認跨帳號成功。

---

### 範例二：缺少 External ID，AssumeRole 失敗（邊界情境）

**本段未實測，為理論預期行為**。自驗方法：同一個 role，不帶 `--external-id` 參數執行：

```bash
aws sts assume-role \
  --role-arn arn:aws:iam::999888777666:role/monitoring-role \
  --role-session-name audit-test
```

預期輸出（失敗）：

```
An error occurred (AccessDenied) when calling the AssumeRole operation:
User: arn:aws:iam::123456789012:user/dev-bot is not authorized to perform:
sts:AssumeRole on resource:
arn:aws:iam::999888777666:role/monitoring-role
```

AccessDenied 的原因：Trust Policy 的 Condition 要求 `sts:ExternalId` 必須等於 `CUSTOMER-SECRET-ABC123`，但請求沒有帶這個條件，AWS STS 直接拒絕。錯誤訊息刻意設計得模糊——不會告訴你是「少了 ExternalId」還是「Principal 不符」，兩種情況都回傳一樣的 AccessDenied，增加攻擊難度。

---

### 範例三：Wildcard Principal 在 KMS Key Policy（危險範例）

以下是一個會讓安全工程師噩夢的 KMS Key Policy（金鑰政策）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAllDecrypt",
      "Effect": "Allow",
      "Principal": "*",
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    }
  ]
}
```

`"Principal": "*"` 代表全世界任何 AWS 身分（包括匿名請求，取決於其他條件）都能呼叫 `kms:Decrypt`。這個 key 保護的所有加密資料實質上是公開可解密的。

KMS Key Policy 是 Resource-Based Policy 的典型代表——它獨立於 IAM Policy 運作，只要 Key Policy 放行，即使 IAM Policy 沒有授權，解密請求仍可通過（Key Policy 優先於 IAM Policy for KMS）。S3 Bucket Policy、SQS Queue Policy、SNS Topic Policy 都有同樣的 `*` Principal 陷阱。

---

## Confused Deputy 問題

Confused Deputy（混淆代理人）是跨帳號信任設計裡最微妙的漏洞，和 CSRF 的本質相同——不是憑證被偷，而是合法身分被混淆。

**情境：SaaS 廠商整合**

```
SaaS 廠商帳號 (111111111111)
       │
       │ AssumeRole（廠商身分）
       ▼
客戶 X 帳號：role/saas-integration  ← Trust Policy: 信任 111111111111:root
客戶 Y 帳號：role/saas-integration  ← Trust Policy: 信任 111111111111:root
```

客戶 X 告訴 SaaS 廠商「幫我備份這個 S3 bucket」，廠商的服務用自己的身分去 assume 客戶 X 的 role。

問題：**客戶 Y 如果也是同一個 SaaS 的客戶，他可以偽造一個請求**，讓廠商的服務誤以為在操作「客戶 Y 自己的資源」，但實際上 assume 的是客戶 X 的 role。廠商的身分（111111111111:*）對兩個客戶的 role 都有信任，AWS 無法分辨「這個 AssumeRole 是客戶 X 授權的還是客戶 Y 偽造的」。

**External ID 是解法：**

信任政策裡加上只有客戶 X 和廠商知道的秘密（External ID）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::111111111111:root"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "CUSTOMER-SECRET-ABC123"
        }
      }
    }
  ]
}
```

廠商在 AssumeRole 請求時必須帶上這個 ExternalId。客戶 Y 不知道客戶 X 的 ExternalId，所以無法偽造讓廠商去操作客戶 X 的帳號。

---

## External ID 的攻擊角度

External ID 本身不是加密保護，只是一個條件字串。能不能繞過，取決於 External ID 的品質：

**可被攻擊的 External ID：**

- 等於帳號 ID（`"sts:ExternalId": "123456789012"`）：帳號 ID 是半公開資訊，掃 S3 bucket 或 CloudTrail log 很容易拿到
- 用遞增序號（`CUSTOMER-001`、`CUSTOMER-002`）：暴力枚舉
- UUID v4 但從弱亂數生成（特定 SaaS 平台歷史上有過）：可預測
- SaaS 管理介面直接顯示 External ID：同一個 SaaS 的其他客戶（內部人員）能看到

**真正安全的 External ID：**

- 高熵亂數，128 bit 以上（標準 UUID v4 是 122 bit 亂數，勉強夠）
- 不在任何前端 UI 明文顯示
- 定期輪換（廠商和客戶協調更新）

---

## `:root` 陷阱：過寬的廠商信任

許多 SaaS 廠商的整合文件直接要求客戶建立這樣的 Trust Policy：

```json
{
  "Principal": {
    "AWS": "arn:aws:iam::111111111111:root"
  }
}
```

**`:root` 不代表 root user**，代表帳號下**所有身分**。廠商帳號（111111111111）的任何 IAM User、任何 Role，都能 assume 你的 role——包括廠商的工讀生、被攻陷的測試帳號、或廠商自己不知道的服務帳號。

**正確做法：信任具體 role**

```json
{
  "Principal": {
    "AWS": "arn:aws:iam::111111111111:role/saas-service-role"
  }
}
```

只有廠商的 `saas-service-role` 這一個 role 能 assume。廠商帳號的其他身分無法使用這個信任。

---

## Role Chaining

AssumeRole 之後，拿到的臨時憑證還能再 AssumeRole，形成**角色鏈（Role Chain）**。常見於多帳號跳板：

```
身分（帳號 A）
  → AssumeRole → 跳板 role（帳號 B）
                    → AssumeRole → 目標 role（帳號 C）
```

每一跳都是獨立的 STS API 呼叫，每一組憑證都有獨立的有效期。

**重要限制**：Role Chaining 發生時（用臨時憑證去 assume 另一個 role），最大 session 時長**強制限制為 1 小時**，不論個別 role 的 `MaxSessionDuration` 設多長（最長 12 小時也沒用）。這是 AWS 的硬性限制，用來控制信任傳遞的時間窗口。

從滲透測試角度，Role Chaining 讓攻擊者能跨帳號橫移，而 CloudTrail 會在每個帳號各自記錄 AssumeRole 事件，需要聚合多帳號的 log 才能重建完整路徑。

---

## 「IAM 雙重門」：跨帳號資源存取的兩道授權

跨帳號操作成功 AssumeRole 只是第一道門。存取目標帳號的具體資源（如 S3、KMS）時，還需要通過第二道門：

```
第一道：Trust Policy 允許 AssumeRole          ← 誰能進來
第二道：Resource-Based Policy 允許跨帳號存取  ← 來了能做什麼
```

以 S3 為例：assume 了帳號 B 的 role 之後，要讀帳號 B 的 bucket，需要：

1. Role 的 Permission Policy 有 `s3:GetObject` 授權，**且**
2. S3 Bucket Policy 沒有明確拒絕跨帳號存取（或有明確 Allow）

對 KMS 更嚴格：KMS Key Policy 必須明確允許，否則即使 IAM Role 有 `kms:Decrypt`，也會被 Key Policy 擋下。許多人測試跨帳號 role assume 成功後，以為就有全部權限，碰到 KMS 才發現還有另一層。

---

## 對比取捨表

| Principal 類型 | 信任範圍 | 推薦程度 | 說明 |
|---|---|---|---|
| `:root`（帳號 ID） | 整個帳號所有身分 | 避免 | 廠商帳號任何人都能 assume |
| 特定 role ARN | 單一 role | 推薦 | 最小權限，主流做法 |
| 特定 user ARN | 單一 IAM User | 可用 | 人員異動需手動維護 |
| OIDC Provider | 動態身分（GitHub Actions、EKS SA） | 推薦 | CI/CD 首選，不用長期憑證 |
| `*`（wildcard） | 全世界 | 危險 | 除非有嚴格 Condition，否則禁用 |
| AWS Service（如 `lambda.amazonaws.com`） | 特定 AWS 服務 | 推薦 | 服務角色標準寫法 |

---

## 踩雷集錦

**1. External ID 被 SaaS UI 明文顯示**

某些早期 SaaS 整合平台把 External ID 印在設定頁面，同一個 SaaS 的其他客戶（或離職員工）能看到。建立 Trust Policy 時，External ID 的保密性和 secret key 同等重要——它一旦洩漏，等同於沒有 External ID 保護。

**2. 信任 `:root` 以為方便，實際上洞開後門**

廠商文件說「設定 Principal 為 `arn:aws:iam::VENDOR_ACCOUNT:root` 並加上 ExternalId」，許多工程師照做了，忘記 `:root` 代表廠商帳號全員。ExternalId 確實能防 Confused Deputy，但防不了廠商帳號內部的惡意身分（供應鏈攻擊）。

**3. Role Chaining 後忘記 1 小時上限，長時間作業中途失敗**

在多帳號架構裡，CI/CD pipeline 用跳板 role 再 assume 目標 role，因為是 Role Chaining，有效期強制 1 小時。部署腳本跑到 70 分鐘就會收到 `ExpiredTokenException`，在凌晨上線時突然失敗，排查半小時才發現是這個。設計時就要把 1 小時上限考慮進去，或拆成多個階段任務。

**4. External ID 設成帳號 ID，以為夠隨機**

AWS 帳號 ID 是 12 位數字，在 ARN 裡到處都有，不算秘密。把 External ID 設成 `"123456789012"` 等同於沒有 External ID，只要知道你的帳號 ID，任何人都能提供正確的 ExternalId 通過驗證。

**5. 跨帳號 AssumeRole 成功，但 KMS 解密失敗，誤以為是 IAM 問題**

進入帳號 B 的 role 之後，嘗試用帳號 B 的 KMS key 解密資料，收到 `AccessDeniedException`。花了幾個小時加 IAM 權限都沒用，因為根本原因是 KMS Key Policy 沒有明確允許這個 role（或這個跨帳號 role），而 KMS 的授權模型和 S3 不同，Key Policy 必須主動授權。先查 Key Policy，再查 IAM Policy。

---

## 進階延伸

**AWS IAM Access Analyzer**：自動掃描你帳號內所有 Resource-Based Policy（包括 Trust Policy、S3 Bucket Policy、KMS Key Policy），標記「外部可存取」的資源。Trust Policy 信任外部帳號的 role 都會被列為 finding，要定期清理或標記為預期行為。

**SCP（Service Control Policy）限制 AssumeRole**：在 AWS Organizations 層面，可以用 SCP 限制「組織內的帳號只能 assume 同樣在組織內的 role」，防止被攻陷的帳號跳到組織外部的惡意帳號。條件用 `aws:PrincipalOrgID`。

**Assume Role 的 CloudTrail 痕跡**：每次 AssumeRole 都會在**目標帳號**的 CloudTrail 留下 `AssumeRole` 事件，記錄 `principalId`（來源身分）、`sourceIPAddress`、`userAgent`。攻擊者習慣用 `--role-session-name` 偽裝成看起來合法的名稱（如 `aws-sdk-java`），偵測時注意這個欄位的異常值。

**Passrole 配合 AssumeRole 的提權路徑**：如果攻擊者有 `iam:PassRole` 權限，能把高權限 role 傳給 Lambda、EC2 等服務，間接取得高權限執行環境，不需要直接 AssumeRole。這條路徑在 Ch 7 有詳細討論，跨帳號版本的邏輯相同。

---

## 本章重點整理

- Trust Policy 的 `Principal` 決定誰能 AssumeRole；`:root` 信任整個帳號，應改用具體 role ARN
- External ID 防 Confused Deputy，但 External ID 本身必須高熵且保密，設成帳號 ID 或顯示在 UI 上等同無效
- `"Principal": "*"` 在 Trust Policy 或 Resource-Based Policy 裡幾乎都是災難，KMS Key Policy 尤其危險
- Role Chaining 允許跨帳號多跳，但有效期強制 1 小時，不論單一 role 設定多長
- 跨帳號資源存取需要「IAM 雙重門」：Trust Policy（誰能進來）+ Resource-Based Policy（來了能做什麼）

---

## 自我檢核

- [ ] 能說明 Trust Policy 和 Permission Policy 的差異，以及各自放在哪裡
- [ ] 能解釋 `arn:aws:iam::123456789012:root` 的確切含義，以及為何不應用於 Principal
- [ ] 能描述 Confused Deputy 攻擊的完整流程，以及 External ID 如何防禦
- [ ] 知道 External ID 本身在哪些情況下會失效（品質差、洩漏）
- [ ] 能說明 Role Chaining 的 1 小時限制從何而來，以及對系統設計的影響
- [ ] 知道跨帳號存取 S3 和 KMS 各自需要哪些額外授權
- [ ] 能看一份 Trust Policy JSON，指出其中的安全問題

---

## 延伸閱讀

1. **AWS Documentation — Confused Deputy Problem**
   `https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html`
   AWS 官方對 Confused Deputy 的說明，含 External ID 的設計原理和建議用法。讀完能把直覺轉成正式定義。

2. **AWS Documentation — IAM role trust policy best practices**
   `https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html#delegate-using-roles`
   整理 Principal 設計的官方建議，包含 OIDC、Service Role、跨帳號 role 的正確寫法。對比本章踩雷集錦看效果更好。

3. **Rhino Security Labs — AWS IAM Privilege Escalation Methods**
   `https://rhinosecuritylabs.com/aws/aws-privilege-escalation-methods-mitigation/`
   把 AssumeRole 放在更大的提權攻擊鏈裡討論，列舉 25+ 種方法，跨帳號信任是其中重要一節。從攻擊者視角看防禦盲點。

4. **AWS re:Invent — Become an IAM Policy Ninja**（影片）
   搜尋 `SEC302 IAM Policy Ninja` 找到對應年份的 re:Invent 影片。深入剖析 Policy Evaluation Logic（IAM 決策流程），對理解「IAM 雙重門」的完整邏輯最有幫助。

5. **Wiz Research — The Dark Side of Trust: Cross-Account Attacks in AWS**
   `https://www.wiz.io/blog/the-dark-side-of-aws-trust-cross-account-attacks`
   真實案例分析，從 SaaS 整合的過寬信任出發，示範攻擊者如何串接多個信任關係橫移。配合本章的 Role Chaining 部分讀。

---

→ [練習 A：低權限 credential → 枚舉 → 提權到 admin](./practice-a-iam-privesc.md)
