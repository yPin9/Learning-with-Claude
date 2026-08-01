# Ch 0 — Lab 環境：隔離帳號、成本煞車、合法邊界、工具鏈

> **目標**：開一個能放心動手的 AWS lab 帳號，裝好工具，建立「只在自己控制的環境」這條紀律線，讓後面每章能直接跑範例而不用擔心噴錢或出事。
>
> **環境**：Ubuntu 22.04 / WSL2 on Windows 11；aws-cli v2.x（`aws --version`）；Python 3.10+

## 為什麼需要獨立的 lab 帳號？

雲端資安課最大的陷阱不是技術難，而是**不小心在生產帳號裡做了什麼**。你用公司帳號測一個 bucket policy 寫錯，你可能讓幾百萬筆資料暴露三分鐘。你在學習 IAM 提權時不小心刪了一個 role，CI/CD pipeline 就全掛了。

開一個專屬 lab 帳號解決這個問題的成本是：約 5 分鐘。不開的成本是：你會一直看著那條「--dry-run」猶豫，永遠跑不起來。

更重要的是法律面：本課的所有技術是教育導向，所有操作**只能在你自己擁有或明確獲得書面授權的環境執行**。未經授權對他人 AWS 帳號、S3 bucket、EC2 instance 進行任何形式的枚舉、掃描、存取，在台灣屬《刑法》妨害電腦使用罪（第 358–363 條），在美國屬 CFAA 違反，後果是刑事責任。這條線不是建議，是紀律。

---

## 建直覺：帳號結構長什麼樣

```
AWS Organizations (選用，但推薦)
└── Root (management) account
    ├── Security OU
    │    └── 你的 lab account  ← 這裡動手
    └── Production OU
         └── (公司用，碰都別碰)
```

即使不用 Organizations，光是開一個**全新的獨立帳號**已經能隔離 99% 的風險。同一個帳號裡的不同 IAM user 隔離不了——IAM user A 炸了 IAM，IAM user B 一樣受影響。帳號才是隔離邊界。

---

## Step 1：開免費 AWS 帳號

1. 前往 [https://aws.amazon.com/free/](https://aws.amazon.com/free/)，用一個專屬 email 註冊（強烈建議用 Gmail 的 `+` alias，例如 `yourname+awslab@gmail.com`，這樣收件統一但帳號隔離）。
2. 需要信用卡——免費方案每月給你一定的 EC2/S3/Lambda 免費額度，但超過就收費。
3. 開完立刻啟用 **MFA（Multi-Factor Authentication）** 給 root 帳號。root 帳號之後不用，但要保護好。

---

## Step 2：成本煞車——billing alarm 與 budget（必做）

**不設成本煞車就開始練習是玩火**。學 EC2 忘記關 instance、跑 Athena 查詢打到 TB 級資料、開了 NAT Gateway 放兩天——這些意外在學習過程中都會發生。

### 設定 Billing Alarm

Billing alarm 透過 CloudWatch 在花費超過閾值時發 email 通知。

**本段未實測，為理論預期行為。** 在你的帳號裡執行以下步驟驗證：

```
主控台路徑：
  CloudWatch → Alarms → Create alarm
  → Select metric → Billing → Total Estimated Charge
  → Threshold: $5 USD（學習階段設低，出事早知道）
  → SNS topic → 輸入你的 email
```

用 aws-cli 等效操作（需先確認 us-east-1 region，billing 資料只在這裡）：

```bash
# 先切到 us-east-1，billing metrics 只有這個 region 有
aws cloudwatch put-metric-alarm \
  --region us-east-1 \
  --alarm-name "lab-billing-5usd" \
  --alarm-description "Alert when estimated charges exceed 5 USD" \
  --metric-name EstimatedCharges \
  --namespace AWS/Billing \
  --statistic Maximum \
  --period 86400 \
  --threshold 5 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --dimensions Name=Currency,Value=USD \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:billing-alert \
  --ok-actions arn:aws:sns:us-east-1:123456789012:billing-alert
```

注意 `123456789012` 是假帳號 ID，`billing-alert` 是你事先建好的 SNS topic ARN。`--period 86400` 是 86400 秒 = 24 小時，billing metric 每天更新一次，短於此無意義。

### 設定 AWS Budget

Budget 比 alarm 更主動——可以設「預測要超過閾值」就提前通知，alarm 只看當下實際值。

```
主控台路徑：
  AWS Cost Management → Budgets → Create budget
  → Budget type: Cost budget
  → Amount: $10/month
  → Alert threshold: 80% actual, 100% forecasted
  → Email: 你的 email
```

**每次練習完，習慣去 Cost Explorer 確認當天花了多少**。一個忘記 terminate 的 t3.small 一個月約 $15 USD，一個 NAT Gateway 一個月約 $32 USD（流量另計）——這些都能在 24 小時內的 billing alarm 抓到。

---

## Step 3：給自己一個受限的管理者 IAM user

**不要用 root 帳號操作**。root 有無限權限且無法限縮，用它練習是危險習慣。

建一個 IAM user，賦予 `AdministratorAccess` managed policy——這在 lab 帳號是可接受的，因為帳號本身已經是隔離的。後面 Ch 3、Ch 6 會教你怎麼建更細緻的受限 user 來模擬低權限攻擊者。

```bash
# 建 IAM user
aws iam create-user --user-name lab-admin

# 賦予 AdministratorAccess
aws iam attach-user-policy \
  --user-name lab-admin \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

# 建 access key（之後給 aws-cli 用）
aws iam create-access-key --user-name lab-admin
```

這段指令要用 root 帳號或已有 IAM 管理權的 user 跑一次。輸出的 `AccessKeyId` 和 `SecretAccessKey` 立刻存好，`SecretAccessKey` 只顯示一次。

---

## Step 4：選擇 Region

課程主線用 **us-east-1（N. Virginia）**——免費方案在這裡涵蓋最廣、新服務最先在這裡出現、大多數公開靶場（如 flaws.cloud）也部署在這。

```bash
# 在 ~/.aws/config 裡設預設 region
aws configure set default.region us-east-1
```

---

## Step 5：安裝 aws-cli v2 並驗身分

### 安裝（Ubuntu / WSL2）

```bash
# 下載並安裝 aws-cli v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# 確認版本
aws --version
```

實測輸出：

```
aws-cli/2.17.20 Python/3.11.8 Linux/5.15.153.1-microsoft-standard-WSL2 exe/x86_64.ubuntu.22
```

版本號會隨時間變，`2.x.x` 即可。aws-cli v1 和 v2 在某些 flag 上有差異，本課統一用 v2。

### 設定憑證

```bash
aws configure
```

互動式填入：

```
AWS Access Key ID [None]: AKIAIOSFODNN7EXAMPLE
AWS Secret Access Key [None]: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
Default region name [None]: us-east-1
Default output format [None]: json
```

`AKIAIOSFODNN7EXAMPLE` 是 AWS 文件的標準假 key 格式，你要填你自己的。`AKIA` 開頭是長期 access key；`ASIA` 開頭是 STS 臨時 key（Ch 5 會深入）。

設定寫到 `~/.aws/credentials` 和 `~/.aws/config`：

```bash
cat ~/.aws/credentials
```

```
[default]
aws_access_key_id = AKIAIOSFODNN7EXAMPLE
aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

**不要把 credentials 檔案 commit 進 git**。`.gitignore` 加上 `~/.aws/` 之外，也要養成習慣不在 repo 目錄下存 key。

### 驗身分：`aws sts get-caller-identity`

這是每次開始操作前都要跑的「我是誰」指令：

```bash
aws sts get-caller-identity
```

真實輸出格式（帳號 ID 是假的）：

```json
{
    "UserId": "AIDAIOSFODNN7EXAMPLE",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/lab-admin"
}
```

三個欄位含義：
- `UserId`：IAM entity 的內部 ID（`AIDA` 開頭是 IAM user，`AROA` 開頭是 role）
- `Account`：12 位數帳號 ID
- `Arn`：Amazon Resource Name，格式 `arn:aws:iam::<account-id>:<type>/<name>`

如果看到 Arn 裡有 `:user/` 是 long-term key；如果是 `:assumed-role/` 是 STS 臨時憑證。養成操作前跑這個指令確認身分的習慣——Ch 6 的枚舉攻擊第一步就是這個。

---

## CloudShell vs 本機 aws-cli

| | AWS CloudShell | 本機 aws-cli |
|---|---|---|
| 免安裝 | 是 | 否，需自行安裝 |
| 憑證管理 | 自動繼承登入身分 | 需手動 `aws configure` |
| 網路位置 | AWS 內部 IP | 你的本機出口 IP |
| 工具預裝 | aws-cli、Python、git | 自行安裝 |
| 持久化 | $HOME 1GB 持久，程式不持久 | 完全持久 |
| 本課選擇 | 快速測試可用 | 主要環境 |

CloudShell 的最大優點是身分繼承——用主控台登入哪個 IAM user，CloudShell 就是那個 user。做快速測試很方便。但它無法安裝 Pacu、ScoutSuite 等工具，本課主線用本機環境。

---

## 合法靶場：flaws.cloud、CloudGoat、IAM Vulnerable

除了自己帳號，以下三個靶場是業界公認的合法練習環境：

### flaws.cloud

```
網址：http://flaws.cloud/
```

Scott Piper 建的免費雲端 CTF，七個關卡從公開 S3 bucket 打到 metadata service 偷 key，每關都有 hint。完全不需要自己開帳號，在他授權的環境裡練。**是這門課最推薦的第一個課外實作**。

### CloudGoat（Rhino Security Labs）

```bash
# CloudGoat 需要 Python 3.6+ 和 Terraform
pip install cloudgoat
cloudgoat config profile <你的 aws-cli profile>
```

**本段未實測，為理論預期行為。** CloudGoat 會在你自己的 AWS 帳號裡部署刻意有漏洞的架構（vulnerable IAM roles、exposed EC2 等），讓你在完全合法的環境打 IAM 提權。Practice A 的環境建議用這個。

注意它會在你帳號建真實資源，練完要跑 `cloudgoat destroy` 清掉，否則會持續扣費。

### IAM Vulnerable

```
GitHub：https://github.com/BishopFox/iam-vulnerable
```

BishopFox 的 Terraform 模組，在你自己帳號建 31 個有漏洞的 IAM 設定，專門練 IAM 提權路徑（PassRole、CreatePolicy、AssumeRole 等）。Ch 7 的練習搭配這個最高效。

---

## 工具機與網段隔離（進階）

如果你之後要模擬攻擊者行為（從外部 IP 掃描自己的服務），建議把「攻擊者工具」和「防禦者觀測」分開：

```
你的本機（防禦視角，看 CloudTrail log）
   │
   ├── EC2 攻擊機（t3.micro，跑 Pacu、ScoutSuite，攻擊視角）
   │    └── 用完 stop，不要 terminate（保留 IP 供 log 對照）
   │
   └── Target resources（S3 bucket、IAM roles、Lambda）
```

EC2 攻擊機建議：
- AMI 用 Kali Linux（AWS Marketplace 免費）或 Ubuntu 22.04
- Instance type `t3.micro` 在免費方案內（前 750 小時/月）
- **用完立刻 Stop**（stop 不收算力費，只收 EBS 儲存費約 $0.08/GB/月）
- 開 Security Group 只允許你的 IP SSH 進去（`0.0.0.0/0:22` 是恥辱）

---

## 踩雷集錦

**root 帳號的 MFA 不設因為「反正是練習帳號」** → 帳號 email/password 一旦洩漏（釣魚、密碼重複使用），沒有 MFA 的 root 是全毀。lab 帳號也要設。

**Billing alarm 設在 ap-northeast-1（東京）** → billing metric 只存在 `us-east-1`，設在其他 region 看不到資料，alarm 永遠不觸發。一定要指定 `--region us-east-1`。

**用 `aws configure` 設完憑證，忘記設 region，指令一直跑 `us-east-1` 以外的 region** → 每次 aws-cli 操作都可以加 `--region` 覆蓋，或確認 `~/.aws/config` 裡的 `region` 欄位設好。

**access key 寫在 .env 或 script 裡 commit 進 repo** → GitHub 的 secret scanning 會抓到並通知 AWS 吊銷 key，但在那之前 key 已經對全世界可讀。養成習慣：key 只放 `~/.aws/credentials`，或用 `aws-vault` 管理。

**CloudGoat 或 IAM Vulnerable 練完沒 destroy** → 這些工具建的資源是真實 AWS 資源，持續計費。每次練完必跑 destroy 指令，然後去 Cost Explorer 確認次日費用歸零。

---

## 進階延伸

**多帳號管理（AWS Organizations）**：如果你之後要練跨帳號攻擊（Ch 8），建議用 Organizations 建一個 management account + 兩個 member account，模擬企業環境。Organizations 本身不收費。

**aws-vault**：比 `~/.aws/credentials` 更安全的 key 管理工具，把 key 存在 OS keychain，每次用前解密。`brew install aws-vault`（macOS）或 `snap install aws-vault`（Linux）。

**多 profile 切換**：一個機器管多個帳號時，`~/.aws/credentials` 用 `[profile-name]` 區分，aws-cli 加 `--profile profile-name` 切換，或設 `AWS_PROFILE` 環境變數。

---

## 本章重點整理

- 雲端資安練習**必須在自己控制的隔離帳號**，未授權存取他人雲端資源在台灣是刑事犯罪。
- 新帳號開完立刻做三件事：root MFA、billing alarm（$5 USD）、AWS budget（$10/月）。
- 操作帳號用 IAM user，不用 root；`aws sts get-caller-identity` 是每次開工前的確認指令。
- billing metric 只在 `us-east-1`，alarm 要指定這個 region。
- 合法靶場：flaws.cloud（免費 CTF）、CloudGoat（自帳號部署）、IAM Vulnerable（IAM 提權專練）。
- 工具機和目標資源分開，用完 Stop 不要 Terminate。

---

## 自我檢核

- [ ] 我能說出為什麼要用獨立帳號而不是公司帳號或 IAM user 隔離
- [ ] 我設好了 billing alarm 並確認它在 us-east-1
- [ ] 我能解釋 `aws sts get-caller-identity` 輸出的三個欄位
- [ ] 我知道 `AKIA` 開頭和 `ASIA` 開頭的 key 差在哪
- [ ] 我能說出 flaws.cloud、CloudGoat、IAM Vulnerable 各自的定位
- [ ] 我把 access key 放對位置（不在 git repo 裡）

---

## 延伸閱讀

1. **[AWS Free Tier 服務清單](https://aws.amazon.com/free/)**
   讀「Always Free」和「12 Months Free」的區別；哪些服務超過免費額度會直接計費，哪些完全免費。關聯：避免 lab 意外超支。

2. **[AWS IAM Security Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)**
   官方文件，特別看「Require MFA」和「Use roles instead of long-term access keys」兩節。學 IAM 前先知道 AWS 自己怎麼說安全。

3. **[flaws.cloud](http://flaws.cloud/)**
   直接從 Level 1 開始打，看看 hint 之前能自己想多遠。七個關卡完整覆蓋 Ch 1–6 的核心概念（S3 misconfig、metadata、IAM 枚舉），是這門課最好的第一個課外實作。

4. **[CloudGoat README — Scenarios 清單](https://github.com/RhinoSecurityLabs/cloudgoat)**
   看有哪些 scenario、各需要什麼 AWS 服務。`vulnerable_cognito` 和 `iam_privesc_by_attachment` 是和本課最相關的兩個，Practice A 會用到。

5. **[aws-vault README](https://github.com/99designs/aws-vault)**
   如果你在一台機器上管多個帳號，這個工具能讓你在 terminal 裡安全切換 profile，不用每次手動改 `~/.aws/credentials`。

---

雲端資安真正的武器是身分，不是 port。Ch 1 我們先建整體的攻擊面地圖——理解為什麼雲端的戰場完全不同於你過去熟悉的那套 kill chain。

→ [Ch 1 雲端資安全貌：Shared Responsibility 與攻擊面重構](./01-cloud-security-overview.md)
