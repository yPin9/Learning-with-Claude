# Ch 6 — IAM 枚舉與偵察：拿到憑證後第一步做什麼

> **目標**：拿到 AWS 憑證之後，系統性地搞清楚「我是誰、我能做什麼、帳號裡有什麼」——從單一 API 呼叫到自動化工具全覆蓋，並理解哪些動作會在 CloudTrail 裡留下明顯痕跡、哪些相對安靜。

上一章拿到了憑證——可能是從 IMDS 搶來的 role credential，可能是 `.aws/credentials` 裡挖出的長期 access key。現在進入**偵察階段（reconnaissance）**：在打下一步之前先把地圖畫出來。這一章的核心問題是：拿到憑證，接下來具體跑什麼指令？

## 先建直覺：憑證偵察的四層漏斗

攻擊者拿到憑證後的偵察流程，可以想成一個由窄到寬的漏斗：

```
[取得憑證]
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 1：確認身分                   │   "我是誰？"
│  aws sts get-caller-identity        │   user? role? 哪個帳號?
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 2：確認我有什麼權限            │   "我能做什麼？"
│  IAM Get*/List* + enumerate-iam     │   attached policies / inline policies
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 3：帳號資源清點               │   "帳號裡有什麼？"
│  CloudFox / Pacu / 手動 List*       │   S3 / EC2 / Lambda / Secrets
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 4：找提權路徑                 │   "我怎麼變更大？"
│  → Ch 7 IAM 提權                   │   PassRole / policy:CreatePolicyVersion
└─────────────────────────────────────┘
```

前三層是本章的範疇。每往下一層，API 呼叫量增加，在 CloudTrail 裡的痕跡也愈來愈明顯。

## Layer 1：確認身分

Ch 5 已介紹過，這裡快速回顧。拿到憑證的第一個呼叫永遠是：

```bash
export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

aws sts get-caller-identity
```

預期回傳：

```json
{
    "UserId": "AIDAIOSFODNN7EXAMPLE",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/dev-bot"
}
```

三個欄位讀法：

- `Account`：帳號 ID，後續所有 ARN 會帶這個數字。`123456789012` 是示範假值。
- `Arn`：精確身分。`:user/dev-bot` 代表這是 IAM user；若是 `assumed-role/名稱/session名稱` 則是 role。
- `UserId`：`AIDA` 開頭代表 IAM user；`AROA` 開頭代表 role；`ASIA` 開頭代表 assumed-role session。

這個呼叫本身**幾乎不會觸發防守方告警**——它是最基本的「查自己身分」動作，CloudTrail 會記錄但沒有 GuardDuty rule 針對單次 `GetCallerIdentity` 告警。

## Layer 2a：手動 IAM 枚舉

確認身分後，最直接的做法是手動跑 IAM 的 Get*/List* 系列。這些是**只讀操作**，不會改變任何資源，但能揭露帳號的 IAM 結構。

### 列出帳號內所有 user

```bash
aws iam list-users --output table
```

```
--------------------------------------------------------------
|                         ListUsers                          |
+-----------+--------------+------------------+--------------+
|  UserId   |  UserName    |  CreateDate      |  Path        |
+-----------+--------------+------------------+--------------+
|  AIDA001  |  admin       |  2024-01-10T...  |  /           |
|  AIDA002  |  dev-bot     |  2024-03-15T...  |  /service/   |
|  AIDA003  |  ci-pipeline |  2024-05-20T...  |  /ci/        |
+-----------+--------------+------------------+--------------+
```

### 列出帳號內所有 role

```bash
aws iam list-roles --query 'Roles[*].[RoleName,Arn]' --output table
```

```
--------------------------------------------------------------
|                        ListRoles                           |
+-------------------------+----------------------------------+
|  ec2-ssm-role           |  arn:aws:iam::123456789012:role/ |
|  lambda-exec-role       |  arn:aws:iam::123456789012:role/ |
|  crossaccount-readonly  |  arn:aws:iam::123456789012:role/ |
+-------------------------+----------------------------------+
```

role 列表特別有用：`crossaccount-*` 這類名稱暗示可能有跨帳號信任關係，後面是提權的切入點（Ch 7）。

### 列出自己的 attached policies

```bash
aws iam list-attached-user-policies --user-name dev-bot
```

```json
{
    "AttachedPolicies": [
        {
            "PolicyName": "S3ReadOnly",
            "PolicyArn": "arn:aws:iam::123456789012:policy/S3ReadOnly"
        },
        {
            "PolicyName": "IAMReadOnlyAccess",
            "PolicyArn": "arn:aws:iam::aws:policy/IAMReadOnlyAccess"
        }
    ]
}
```

接著把每個 policy 的實際內容拉出來：

```bash
# 先取得最新版本號
aws iam get-policy --policy-arn arn:aws:iam::aws:policy/IAMReadOnlyAccess \
  --query 'Policy.DefaultVersionId' --output text
# 回傳: v1

aws iam get-policy-version \
  --policy-arn arn:aws:iam::aws:policy/IAMReadOnlyAccess \
  --version-id v1 \
  --query 'PolicyVersion.Document'
```

這三步（list-attached → get-policy → get-policy-version）是手動枚舉的標準路徑。
對 group 和 inline policy 也要跑對應指令：`list-groups-for-user`、`list-user-policies`、`get-user-policy`。

**本段未實測，為理論預期行為**（需要真實 AWS 帳號）。自驗方法：建一個 test IAM user，attach `IAMReadOnlyAccess`，用該 user 的 key 跑上述指令，確認輸出格式符合預期。

## Layer 2b：enumerate-iam — 暴力確認實際有效權限

手動 IAM 枚舉有個盲點：**policy 寫的跟實際有效的不一定吻合**。Resource 限制、condition、permission boundary、SCP——任何一層都可能讓 policy 看起來有但實際沒效。

[enumerate-iam](https://github.com/andresriancho/enumerate-iam) 的思路是**直接呼叫每個 AWS API**，看哪些不回 `AccessDenied`。有效的比看 policy 文件更可靠。

### 安裝與執行

```bash
git clone https://github.com/andresriancho/enumerate-iam
cd enumerate-iam
pip install -r requirements.txt

python enumerate-iam.py \
  --access-key AKIAIOSFODNN7EXAMPLE \
  --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
  --region ap-northeast-1
```

實際跑起來的輸出片段（fake but realistic）：

```
2026-08-01 12:34:56,123 - 180 remaining targets
2026-08-01 12:34:57,456 - Run as: arn:aws:iam::123456789012:user/dev-bot
2026-08-01 12:34:58,789 - Confirmed permissions -> iam:GetAccountSummary
2026-08-01 12:35:01,012 - Confirmed permissions -> iam:ListRoles
2026-08-01 12:35:03,234 - Confirmed permissions -> iam:GetPolicyVersion
2026-08-01 12:35:05,456 - Confirmed permissions -> sts:AssumeRole (lambda-exec-role)
2026-08-01 12:35:12,678 - Confirmed permissions -> s3:ListAllMyBuckets
2026-08-01 12:35:14,900 - Confirmed permissions -> secretsmanager:ListSecrets
```

`180 remaining targets` 代表工具內建了約 180 個 API 呼叫要試。完成後你會有一份確認有效的 action 清單——**不是 policy 說有，是實際 API 回了 200 的那些**。

### enumerate-iam 的盲點

- 只試**無 resource 限制**的 list/get 類呼叫。帶 resource ARN 的 action（如 `s3:GetObject` 對特定 bucket）要另外測。
- 部分 API 呼叫本身就有副作用（罕見但存在）。enumerate-iam 設計上只選無副作用的，但版本不同可能有差。
- **它的呼叫量很大，集中在短時間內**——這個特徵非常顯眼，後面細說。

## Layer 2c：CloudFox — 結構化資源清點

[CloudFox](https://github.com/BishopFox/cloudfox) 是 Bishop Fox 出的工具，定位是「pentest 用的雲端枚舉工具」，輸出以結構化 findings 為主，比 enumerate-iam 的原始 log 更好消化。

```bash
# 安裝（Go binary）
go install github.com/BishopFox/cloudfox@latest

# 枚舉當前憑證的有效權限
cloudfox aws permissions --profile default --region ap-northeast-1
```

預期輸出風格（fake but realistic）：

```
[i] AWS Account: 123456789012
[i] Principal: arn:aws:iam::123456789012:user/dev-bot

[+] IAM Permissions for dev-bot
    Action                        Resource
    iam:GetAccountSummary         *
    iam:ListRoles                 *
    iam:GetPolicyVersion          *
    s3:ListAllMyBuckets           *
    s3:GetObject                  arn:aws:s3:::dev-artifacts/*
    secretsmanager:ListSecrets    *
    sts:AssumeRole                arn:aws:iam::123456789012:role/lambda-exec-role

[!] High-value findings:
    - sts:AssumeRole -> lambda-exec-role (check role's permissions next)
    - secretsmanager:ListSecrets -> may expose secret names
```

CloudFox 的優勢在於它不只枚舉，還會標記 **high-value findings**——`sts:AssumeRole` 和 `secretsmanager:*` 這類對攻擊者有價值的組合會被特別提示。還有其他 subcommand：`cloudfox aws principals`（列所有 principal）、`cloudfox aws secrets`（找 Secrets Manager 和 SSM Parameter Store）。

**本段未實測，為理論預期行為**。自驗方法：在有 `IAMReadOnlyAccess` 的環境跑 `cloudfox aws permissions`，比對輸出 action 清單與 policy 文件是否一致。

## Layer 2d：Pacu — iam__enum_permissions

[Pacu](https://github.com/RhinoSecurityLabs/pacu) 是 Rhino Security Labs 的 AWS 攻擊框架，模組化設計。`iam__enum_permissions` 模組做的事類似 enumerate-iam，但整合進 Pacu 的 session 管理，方便後續利用模組直接銜接。

```bash
# 啟動 Pacu
python3 pacu.py

# 在 Pacu shell 內
Pacu (session) > import_keys  # 或 set_keys
Pacu (session) > run iam__enum_permissions
```

對滲透測試場景，Pacu 的好處是 session 會記下所有 confirmed permissions，後續的提權模組（`iam__privesc_scan`）可以直接拿這份清單來比對已知提權路徑。三工具定位對比：

| 工具             | 定位                         | 輸出形式       | 適合場景                   |
|------------------|------------------------------|----------------|----------------------------|
| enumerate-iam    | 暴力 API 試探，輕量           | raw log        | 快速確認有效 action         |
| CloudFox         | 結構化枚舉 + high-value 標記  | table/findings | pentest 報告，清點資產      |
| Pacu             | 完整攻擊框架，模組串接        | 框架內 session | 長期 engagement，提權接續   |

## 枚舉會不會觸發告警？

這是實戰中最常被忽略的問題。手動與自動枚舉在 CloudTrail 裡的特徵差很多。

### 什麼會被記錄

AWS 所有 API 呼叫都進 CloudTrail，包含 `iam:ListUsers`、`iam:GetPolicy` 這類讀操作。CloudTrail 本身不告警，但它是 GuardDuty 和 SIEM 的資料來源。

### 安靜 vs 吵雜的做法

```
手動枚舉（安靜）                  自動工具（吵雜）
─────────────────────────────     ────────────────────────────────
每次呼叫之間有間隔                  180 個呼叫在 60 秒內噴完
呼叫種類集中在幾個 service          橫跨幾十個 service
符合人類操作節奏                    pattern 非常機械化
無 AccessDenied 爆量               大量 AccessDenied event
```

GuardDuty 有一個專門的 finding type：**`Recon:IAMUser/UserPermissions`**——當它偵測到某個 IAM principal 在短時間內發出大量枚舉呼叫（特別是伴隨大量 `AccessDenied`），會觸發這個 finding。enumerate-iam 就是典型的觸發源。

### 相對安全的做法

- `sts:GetCallerIdentity`：極安靜，幾乎不會單獨觸發任何 finding。
- `iam:Get*` / `iam:List*`：讀操作，少量手動呼叫通常不告警。
- `iam:SimulatePrincipalPolicy`：**這個很特別**——它能讓你「模擬」自己有沒有某個權限，但不實際呼叫 API。不過呼叫 `SimulatePrincipalPolicy` 本身也會進 CloudTrail，防守方如果在看 log 會發現你在測權限。
- 用 `--region` 限定單一 region，避免 multi-region 枚舉的明顯特徵。

**結論**：如果在 engagement 中需要低調，優先手動呼叫 5-10 個關鍵 API；自動工具留給不在意噪音的場景（或確認 GuardDuty 未啟用之後）。

## Principal 權限測繪：從原始輸出到攻擊地圖

把上面各步驟的輸出整合起來，才能得到一張可操作的權限地圖（permission map）。以 `dev-bot` 為例：

```
dev-bot (IAM user, AKIAIOSFODNN7EXAMPLE)
│
├── 直接附加 policy
│     IAMReadOnlyAccess  → iam:Get*, iam:List* (resource: *)
│     S3ReadOnly         → s3:GetObject (resource: arn:aws:s3:::dev-artifacts/*)
│
├── 所屬 group: dev-team
│     group policy: SecretsManagerReadOnly → secretsmanager:List*, secretsmanager:Get*
│
└── 可 assume 的 role
      lambda-exec-role → trust policy 允許 dev-bot assume
            role 本身的 permission:
              AWSLambdaFullAccess  (lambda:*, logs:*)
              iam:PassRole         (resource: *) ← 提權警報！
```

看到 `iam:PassRole` on resource `*`——這是 Ch 7 的起點。在你畫出這張圖之前，你不會知道 `dev-bot` 的 assume-role 鏈最後能走到有 `PassRole` 的位置。

### 自動化測繪的現實

上面這張圖要靠多個指令組合：`list-attached-user-policies` + `list-groups-for-user` + `list-attached-group-policies` + `list-roles` + 每個 role 的 `get-role`（看 trust policy）+ `list-attached-role-policies` + `get-policy-version`。

手動跑完至少需要 15-20 個 API 呼叫。CloudFox 的 `permissions` subcommand 把這些串起來，是最快速得到完整圖的方法。

## 踩雷集錦

**1. 只看 user 的直接 policy，忘了 group**
IAM user 的有效權限 = 直接附加 + 所屬 group 的附加 + inline。`enumerate-iam` 試的是有效結果，但如果你手動看 policy，`list-groups-for-user` 那步跳掉就會漏掉 group 來的權限。

**2. 以為 AccessDenied 代表「沒有」**
`AccessDenied` 只代表「這個呼叫在這個資源上沒有」。permission boundary 或 SCP 可能讓你在某個 resource 上被擋，但換個 resource 就通。不要只試一個 ARN 就放棄。

**3. enumerate-iam 的 region 問題**
預設跑 `us-east-1`，但很多資源建在其他 region。跑 `--region ap-northeast-1` 補跑；S3 是 global service，`s3:ListAllMyBuckets` 不受 region 影響。

**4. Assume role 之後忘記重新枚舉**
assume 進 `lambda-exec-role` 之後，你的有效身分完全不同了——原來的 `dev-bot` 的 policy 失效，換成 role 的 policy。進 role 之後要重跑 `get-caller-identity` + `list-attached-role-policies`，不能沿用之前的枚舉結果。

**5. 混淆 region-specific 和 global 的 IAM**
IAM 本身是 global service（不分 region），但你要注意 CloudTrail 的 region 設定——如果帳號只在 `us-east-1` 開 CloudTrail，你在 `ap-northeast-1` 的 API 呼叫可能沒被記錄。這對攻擊者是好消息，對防守者是大漏洞。

## 進階延伸

**IAM Access Analyzer**
AWS 官方工具，可以分析 IAM policy 並指出過度開放的權限。從防守視角，理解它的邏輯可以幫你預測防守方看到的告警長什麼樣。

**Permission Boundaries 的枚舉**
`get-user` 和 `get-role` 的輸出裡有 `PermissionsBoundary` 欄位。如果存在，要另外拉 boundary policy 文件，因為它會上限你的有效權限。enumerate-iam 的結果已反映 boundary 效果，但手動看 policy 文件時要記得加這一層。

**跨帳號 role 枚舉**
`aws iam list-roles` 拿到的 role 中，trust policy 裡 `Principal.AWS` 如果指向另一個帳號，代表那個帳號可以 assume 這個 role。這是跨帳號橫向移動的線索，要記下來帶進 Ch 7。

## 本章重點整理

- 拿到憑證後分四層偵察：身分確認 → 權限枚舉 → 資源清點 → 找提權路徑。
- `sts get-caller-identity` 是永遠的第一步；安靜且必要。
- 手動 IAM 枚舉：`list-attached-user-policies` → `get-policy` → `get-policy-version`，別忘記 group 和 inline policy。
- enumerate-iam 試呼叫所有 AWS API，得到「實際有效」的 action 清單，比看 policy 文件可靠，但會觸發 GuardDuty `Recon:IAMUser/UserPermissions`。
- CloudFox `permissions` 輸出結構化 findings，適合 pentest 場景；Pacu 適合框架式 engagement 接續利用。
- 自動工具的呼叫量特徵（短時間大量 AccessDenied）是 GuardDuty 的偵測目標，低調場景要慢慢手動枚舉。
- 權限測繪的終點：畫出「哪個 principal 透過哪條 assume 鏈能走到哪個高權限 action」的圖，`iam:PassRole` 和 `iam:CreatePolicyVersion` 是重點目標。

## 自我檢核

- [ ] 我能說出 `sts get-caller-identity` 回傳的三個欄位各代表什麼。
- [ ] 我知道 IAM user 的有效權限由哪三個來源組合（直接附加、group、inline）。
- [ ] 我理解 enumerate-iam 的枚舉邏輯：試 API 呼叫而非讀 policy 文件。
- [ ] 我能解釋為什麼 enumerate-iam 的結果比手動讀 policy 更可靠（permission boundary / SCP 都已反映）。
- [ ] 我知道 GuardDuty 的 `Recon:IAMUser/UserPermissions` 是什麼行為觸發的。
- [ ] 我能畫出一條「user → assume role → 高權限 action」的測繪路徑。
- [ ] 我知道進入新 role 後要重新枚舉，不能沿用前一個身分的結果。

## 延伸閱讀

1. **[HackTricks — AWS IAM Privilege Escalation](https://cloud.hacktricks.xyz/pentesting-cloud/aws-security/aws-privilege-escalation/aws-iam-privilege-escalation)** — 把本章的枚舉結果和下一章提權路徑直接串起來，`PassRole`、`CreatePolicyVersion` 等經典手法在這裡都有詳解和實際指令，是最值得在讀完本章後馬上翻的資料。

2. **[enumerate-iam source code](https://github.com/andresriancho/enumerate-iam/blob/master/enumerate_iam/main.py)** — 直接看它的 API 清單和呼叫邏輯，你會理解它「用 AccessDenied 當過濾器」的設計，以及哪些 API 它刻意排除（有副作用的）。工具行為要看 code 才信得過。

3. **[CloudFox README — permissions subcommand](https://github.com/BishopFox/cloudfox)** — 看官方說明理解 CloudFox 怎麼整合多個 IAM API 呼叫、輸出格式是什麼，以及它跟 enumerate-iam 的設計差異（一個以 findings 為中心，一個以 raw action 清單為中心）。

4. **[AWS GuardDuty Finding Types — Recon:IAMUser/UserPermissions](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-iam.html)** — AWS 官方說明這個 finding 的觸發條件和嚴重性。防守方看這份文件建 alert，攻擊方看這份文件理解自己什麼行為會被看見。雙方都應該把這頁背熟。

5. **[Rhino Security Labs — AWS IAM Enumeration Blog](https://rhinosecuritylabs.com/aws/aws-iam-enumeration/)** — Rhino 寫的 IAM 枚舉方法論，從零開始到完整測繪，思路跟本章一致但有更多邊角案例（cross-account trust、inline policy 陷阱），適合確認自己有沒有漏掉什麼。

---

枚舉完權限地圖，下一步是問：「這些權限能讓我變更大嗎？」

→ [Ch 7 IAM 提權技術：PassRole 等經典 privesc 路徑](./07-iam-privilege-escalation.md)
