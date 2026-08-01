# Ch 2 — 雲端攻擊工具鏈總覽：Pacu / ScoutSuite / CloudFox / Prowler

> **目標**：認識雲端資安評估的主要工具，理解每個工具評估什麼、什麼階段用、輸出長怎樣、跟其他工具差在哪——讓你拿到一組 credential 時知道先跑哪個、後跑哪個。
>
> **環境**：Ubuntu 22.04 / WSL2；Python 3.10+；pipx 1.x；Docker 27.x；aws-cli v2（已設定 lab 帳號 credential，見 Ch 0）

## 為什麼需要工具，而不是徒手打 aws-cli？

你可以只用 `aws iam list-roles`、`aws s3 ls` 一條一條跑——但 AWS 有超過 350 個服務，每個服務有數十到數百個 API，徒手枚舉要幾天。

工具的價值是**自動化廣度**和**知識庫**：Pacu 知道哪些 API 組合能產生提權路徑；ScoutSuite 知道哪些配置違反了 CIS Benchmark；Prowler 知道哪些設定觸發了 GDPR 或 PCI-DSS 的規定。工具讓你在幾十分鐘內掃出人工要幾天才能找到的 misconfiguration。

但工具有它的限制：它們掃出「是否存在這個配置問題」，**不能判斷這個問題在這個業務場景下是否真的可利用**。那個判斷是你的工作。

**所有工具只能對你有授權的目標使用。** 在別人的 AWS 帳號跑 Pacu 或 ScoutSuite 是未授權存取，無論你有沒有拿到那個帳號的 credential。授權的定義是書面的 ROE（Rules of Engagement），不是「我猜他應該不介意」。

---

## 先建直覺：工具分層

```
Kill Chain 階段          工具                       功能定位
─────────────────────────────────────────────────────────────
初始存取前               OSINT / 外部掃描            （本課不涵蓋）
                        ↓
拿到 credential 後
  枚舉 & 偵察           enumerate-iam              「我能呼叫哪些 API？」
                        CloudFox                   「環境裡有哪些攻擊路徑？」
                        ScoutSuite / Prowler       「有哪些 misconfig？」
                        ↓
  提權 & 攻擊           Pacu                       「執行特定攻擊模組」
                        ↓
K8s 環境               kube-hunter / kube-bench    「K8s cluster 安全狀態」
```

---

## 工具一：enumerate-iam

**評估什麼**：當前 credential 有哪些 IAM 權限——不是看 policy 文件，而是**暴力試每個 API call**，看哪個回 403、哪個真正執行成功。

**什麼階段用**：拿到任何一組 credential 之後的第一步——在你完全不知道這組 key 能做什麼的時候。

**為什麼要試而不是看 policy**：
1. IAM policy 可能有 10 層繼承（inline、managed、group、boundary、SCP），人工算清楚很難。
2. SCPs（Service Control Policy）可能從 Organizations 層面再限縮。
3. 有時候你拿到 key 但沒有 `iam:GetPolicy` 權限，根本看不到 policy。

### 安裝

```bash
# 用 pipx 安裝（隔離環境，不污染系統 Python）
pipx install enumerate-iam
```

實測輸出：

```
  installed package enumerate-iam 0.1.0, installed using Python 3.10.12
  These apps are now globally available
    - enumerate-iam
```

### 使用

```bash
# 最基本用法：用當前 aws-cli 設定的 credential 枚舉
enumerate-iam --access-key AKIAIOSFODNN7EXAMPLE \
              --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
              --region us-east-1
```

**本段未實測，為理論預期行為。** 輸出格式大致如下（括號是說明，不是實際輸出）：

```
2024-01-01 10:00:01,234 - 21439 - [INFO] -- s3.list_buckets() worked!
2024-01-01 10:00:01,891 - 21439 - [INFO] -- iam.get_account_authorization_details() worked!
2024-01-01 10:00:02,103 - 21439 - [INFO] -- ec2.describe_instances() worked!
2024-01-01 10:00:02,340 - 21439 - [INFO] -- iam.list_roles() worked!
2024-01-01 10:00:02,891 - 21439 - [INFO] -- lambda.list_functions() worked!
...
2024-01-01 10:02:14,023 - 21439 - [INFO] -- Confirmed permissions via brute force:
{
  "iam": ["get_account_authorization_details", "list_roles", "list_users"],
  "s3": ["list_buckets"],
  "ec2": ["describe_instances"],
  "lambda": ["list_functions"]
}
```

每一行代表一個 API call 的結果。工具跑完後給你一份「確認有效的 API」列表，這就是你的**攻擊面地圖第一版**。

**邊界情況**：有些 API 回傳 HTTP 200 但內容是空的（例如 `iam.list_users()` 回傳空 list），enumerate-iam 標記為有權限——但你實際能讀到的資料是零。這不是 bug，是設計——它只測試 API 是否被拒絕，不測試結果有沒有意義。

### 和其他工具的差異

enumerate-iam 是最「原始」的工具，只告訴你「能不能呼叫」，不告訴你「應不應該呼叫」或「這組能力能做什麼壞事」。它是後續所有工具的先備資訊。

---

## 工具二：Pacu

**評估什麼**：Pacu 不只枚舉——它是**完整的 AWS 攻擊框架**，內建 70+ 個攻擊模組，從枚舉到實際執行提權都有。

**什麼階段用**：枚舉完之後，想自動化執行特定攻擊路徑——例如「幫我試所有 IAM 提權路徑」。

**誰做的**：Rhino Security Labs（也是 23 種 AWS IAM privesc 路徑的研究者）。

### 安裝

```bash
# 方法一：pipx（推薦，隔離環境）
pipx install pacu

# 方法二：Docker（不想裝 Python 依賴）
docker pull rhinosecuritylabs/pacu:latest
```

安裝後啟動：

```bash
pacu
```

實測輸出（首次啟動建新 session）：

```

Pacu (v1.6.1) - Main Menu

What would you like to do?
[1] Start a new session
[2] List sessions/switch session
[3] Exit

> 1
Name this session: lab-demo
```

Pacu 用 session 管理不同目標的資料，不同 engagement 要建不同 session，避免混淆。

### 設定 Credential

```
Pacu Main Menu
> set_keys
Setting keys for session: lab-demo
Access Key ID: AKIAIOSFODNN7EXAMPLE
Secret Access Key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
Session Token (Optional): （空白，長期 key 不需要）
Keys imported.
```

### 使用主要模組

```
# 列所有可用模組
> list

# 搜尋 IAM 相關模組
> search iam

# 跑 IAM 枚舉（枚舉所有 user/role/policy/group）
> run iam__enum_users_roles_policies_groups

# 跑 IAM 提權檢測（找出所有可利用的 privesc 路徑）
> run iam__privesc_scan
```

**本段未實測，為理論預期行為。** `iam__privesc_scan` 的輸出格式大致如下：

```
[privesc_scan] Checking 21 privesc methods...
[privesc_scan] [FOUND] iam:CreatePolicyVersion - Can create new version of existing policy
[privesc_scan] [FOUND] iam:PassRole + ec2:RunInstances - Can launch EC2 with high-priv role
[privesc_scan] No direct path to AdministratorAccess found, but found 2 paths.
[privesc_scan] Run "run iam__privesc_scan --method PassRole_EC2" to exploit.
```

每個 FOUND 就是一條可利用的提權路徑。Ch 7 會深入每條路徑的機制。

### 輸出在哪

Pacu 把所有蒐集到的資料存在 `~/.local/share/pacu/sessions/<session-name>/` 下的 SQLite 資料庫。用 `data` 指令查看：

```
> data IAM
```

**和其他工具的差異**：Pacu 是**主動攻擊框架**——它不只告訴你有問題，它能實際執行攻擊（建後門 user、嘗試提權）。ScoutSuite/Prowler 是純讀取評估工具，不會修改任何設定。在紅隊 engagement 外用 Pacu 的破壞性模組，確認你有明確授權。

---

## 工具三：ScoutSuite

**評估什麼**：從**防禦者/合規角度**掃描整個雲帳號的配置安全，對照 CIS Benchmark 和業界最佳實踐，產生 HTML 報告。

**什麼階段用**：帳號整體安全評估、compliance 掃描、找 misconfig——紅隊用它快速找 misconfiguration 作為攻擊入口，藍隊用它找修補優先順序。

**誰做的**：NCC Group。

**雲平台支援**：AWS、Azure、GCP、Alibaba Cloud、Oracle Cloud——是本課工具裡多雲支援最廣的。

### 安裝

```bash
pipx install scoutsuite
```

### 使用

```bash
# 掃描 AWS（用當前 aws-cli profile）
scout aws

# 指定 profile
scout aws --profile lab-admin

# 只掃特定服務（加快速度）
scout aws --services s3 iam ec2
```

**本段未實測，為理論預期行為。** 掃完後在當前目錄建立報告：

```
2024-01-01 10:00:00 ScoutSuite 5.13.0 by NCC Group
...
Saving report to scoutsuite-report/
Report saved: scoutsuite-report/scoutsuite-results.html
```

用瀏覽器開 `scoutsuite-results.html`，看到分服務的 findings，每個 finding 有：
- 嚴重度（Danger / Warning / Good）
- 具體描述（「IAM user without MFA」「S3 bucket public read」）
- 受影響的資源清單（哪個 user、哪個 bucket）

**和其他工具的差異**：ScoutSuite 是**廣度掃描工具**，一次看整個帳號所有服務的配置。但它只讀不寫，不執行攻擊。輸出是給人看的 HTML 報告，適合呈現給客戶或 management。

---

## 工具四：Prowler

**評估什麼**：比 ScoutSuite 更強調**合規框架**——支援 CIS、SOC 2、PCI-DSS、HIPAA、NIST 800-53、ISO 27001 等 20+ 個框架的自動化 check。

**什麼階段用**：合規評估、安全基線建立、CI/CD 裡的自動化安全掃描。

**和 ScoutSuite 的差異**：

| | ScoutSuite | Prowler |
|---|---|---|
| 定位 | 安全配置評估 | 合規框架對齊 |
| 輸出格式 | HTML 互動報告 | JSON / CSV / HTML，適合自動化 |
| 多雲 | AWS/Azure/GCP/Alibaba/OCI | AWS/Azure/GCP（3.x 版） |
| 合規框架 | CIS 等基本框架 | 20+ 框架，含 PCI-DSS、HIPAA |
| 安裝 | Python / pipx | Python / Docker |
| 適合場景 | 一次性評估 | CI/CD 持續監控 |

### 安裝

```bash
# Docker（推薦，依賴最乾淨）
docker pull prowlercloud/prowler:latest

# 或 pipx
pipx install prowler
```

### 使用

```bash
# 掃描 AWS，輸出所有 findings
docker run --rm \
  -e AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE \
  -e AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
  -e AWS_DEFAULT_REGION=us-east-1 \
  prowlercloud/prowler:latest aws

# 只跑 CIS AWS Foundations Benchmark v1.5
docker run --rm \
  -e AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE \
  -e AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
  prowlercloud/prowler:latest aws --compliance cis_aws_foundations_benchmark_1.5
```

**本段未實測，為理論預期行為。** 輸出範例：

```
2024-01-01 [PASS] iam.1: Ensure MFA is enabled for the "root" account [critical]
2024-01-01 [FAIL] iam.3: Ensure credentials unused for 90 days or greater are disabled [high]
           AccountID: 123456789012
           Region: us-east-1
           Resource: arn:aws:iam::123456789012:user/old-deploy-key
2024-01-01 [FAIL] s3.1: Ensure that S3 buckets are configured with "Block public access" [critical]
           Resource: arn:aws:s3:::my-lab-bucket-public
```

`PASS` / `FAIL` 後面是 check ID，可以用 `--checks iam.3` 只跑特定 check。

---

## 工具五：CloudFox

**評估什麼**：攻擊路徑映射——找出當前 credential 能到達哪些資源、有哪些橫向移動路徑，特別強調**把 misconfig 連結成完整的攻擊鏈**。

**什麼階段用**：枚舉之後、提權之前——用來規劃「從我現在的位置，最省力的攻擊路徑是什麼」。

**誰做的**：Bishop Fox。

**和 ScoutSuite/Prowler 的差異**：ScoutSuite 告訴你「IAM user 沒有 MFA」；CloudFox 告訴你「這個沒有 MFA 的 user 如果被入侵，能 AssumeRole 到 prod-admin role，那個 role 有 S3:GetObject 能讀 secrets bucket」——它把點連成線。

### 安裝

```bash
# CloudFox 是 Go 寫的，下載預編譯 binary
# Linux x86_64
wget https://github.com/BishopFox/cloudfox/releases/download/v1.12.0/cloudfox-linux-amd64.zip
unzip cloudfox-linux-amd64.zip
chmod +x cloudfox
sudo mv cloudfox /usr/local/bin/
```

實測輸出（確認安裝）：

```bash
cloudfox --version
```

```
cloudfox v1.12.0
```

### 使用

```bash
# 整體帳號枚舉（輸出攻擊路徑摘要）
cloudfox aws --profile lab-admin all-checks

# 找 IAM 可以做什麼
cloudfox aws --profile lab-admin iam-simulator

# 找可被假設的 role（AssumeRole 攻擊路徑）
cloudfox aws --profile lab-admin role-trusts

# 找哪些 Lambda 有敏感環境變數
cloudfox aws --profile lab-admin lambda
```

**本段未實測，為理論預期行為。** `role-trusts` 輸出範例：

```
[i] Enumerating role trusts in 123456789012...
[i] Found 3 roles that can be assumed:

Role ARN: arn:aws:iam::123456789012:role/ec2-deploy-role
  Trust Policy: Principal: {"Service": "ec2.amazonaws.com"}
  Permissions: AmazonS3FullAccess, AmazonEC2FullAccess

Role ARN: arn:aws:iam::123456789012:role/cross-account-reader
  Trust Policy: Principal: {"AWS": "arn:aws:iam::999999999999:root"}
  Permissions: ReadOnlyAccess
  ← 這個從另一個帳號 (999999999999) 可以 AssumeRole
```

從這個輸出你能直接看出攻擊路徑：哪個 role 對哪個帳號開放 trust，有哪些權限。

---

## 工具六：kube-hunter 與 kube-bench

這兩個是 K8s 專用工具，Ch 21–30 才是主戰場，這裡先知道定位，後面用到時直接上手。

### kube-hunter（Aqua Security）

**評估什麼**：從**外部攻擊者角度**掃描 K8s cluster 的暴露面——anonymous API server access、etcd 是否公開、kubelet 是否有未認證 API 等。

**什麼階段用**：拿到一個 K8s cluster 的網路可達性之後，評估攻擊入口。

```bash
# Docker 跑（主動掃描模式，指定目標 IP）
docker run --rm aquasec/kube-hunter --remote <cluster-ip>

# Passive 模式（在 cluster 內部 Pod 跑，模擬 Pod 逃逸後的攻擊面）
docker run --rm --network host aquasec/kube-hunter
```

**本段未實測，為理論預期行為。** 輸出範例：

```
Vulnerabilities:
+------------------+--------------------+------------------+-------------------+
| LOCATION         | VULNERABILITY      | DESCRIPTION      | EVIDENCE          |
+------------------+--------------------+------------------+-------------------+
| 10.0.0.1:8080    | Anonymous API      | API server allows| allowed           |
|                  | Server Access      | anonymous access |                   |
| 10.0.0.1:2379    | Exposed ETCD      | etcd accessible  | etcd version 3.5  |
|                  |                    | without auth     |                   |
+------------------+--------------------+------------------+-------------------+
```

### kube-bench（Aqua Security）

**評估什麼**：對照 **CIS Kubernetes Benchmark** 檢查 cluster 的安全配置——scheduler 的 flag 有沒有設對、API server 有沒有開正確的 audit log、node 上的 kubelet 配置是否符合最佳實踐。

**什麼階段用**：防禦方用於合規稽核；紅隊用於找配置弱點。

```bash
# 在 K8s node 上跑（需要 node 存取）
docker run --rm \
  -v /etc:/etc:ro \
  -v /var:/var:ro \
  -v /usr/bin/kubectl:/usr/bin/kubectl:ro \
  --pid=host \
  aquasec/kube-bench
```

**本段未實測，為理論預期行為。** 輸出範例：

```
[INFO] 1 Master Node Security Configuration
[INFO] 1.1 Master Node Configuration Files
[PASS] 1.1.1 Ensure that the API server pod specification file permissions are set to 644 or more restrictive
[FAIL] 1.1.2 Ensure that the API server pod specification file ownership is set to root:root
...
[WARN] 1.2.1 Ensure that the --anonymous-auth argument is set to false (Manual)

== Summary master ==
41 checks PASS
15 checks FAIL
11 checks WARN
```

`PASS` / `FAIL` / `WARN` 每一條都對應 CIS Benchmark 的具體 check ID，可以直接對照文件找修補方式。

---

## 工具 vs 用途 vs 雲平台對照表

| 工具 | 主要用途 | 攻防定位 | AWS | Azure | GCP | K8s | 輸出格式 |
|---|---|---|---|---|---|---|---|
| enumerate-iam | 當前 credential 能做什麼 | 攻擊（偵察） | ✅ | ✗ | ✗ | ✗ | JSON / log |
| Pacu | IAM 提權 + 廣泛攻擊 | 攻擊（執行） | ✅ | ✗ | ✗ | ✗ | CLI / SQLite |
| ScoutSuite | 整帳號配置安全 | 防禦（評估） | ✅ | ✅ | ✅ | ✗ | HTML 報告 |
| Prowler | 合規框架對齊 | 防禦（合規） | ✅ | ✅ | ✅ | ✗ | JSON/CSV/HTML |
| CloudFox | 攻擊路徑映射 | 攻擊（規劃） | ✅ | ✅ | ✗ | ✗ | CLI / JSON |
| kube-hunter | K8s 暴露面 | 攻擊（偵察） | - | - | - | ✅ | CLI 表格 |
| kube-bench | K8s 合規 | 防禦（合規） | - | - | - | ✅ | CLI 報告 |

「攻防定位」不是說防禦者不能用 Pacu 或 CloudFox——紅隊和藍隊都要理解這些工具，差別在用的場景和目的。

---

## 工具選擇決策樹

```
拿到一組 credential，接下來用哪個工具？

我不知道這組 key 能做什麼
         ↓
    enumerate-iam
    （先知道能呼叫哪些 API）
         ↓
我想看整個帳號有哪些 misconfig
    ├── 我要 HTML 報告給客戶 → ScoutSuite
    ├── 我要合規框架對齊 → Prowler
    └── 我要攻擊路徑 → CloudFox
         ↓
我想執行具體的攻擊（IAM 提權、建後門）
         ↓
    Pacu（攻擊框架，確認有授權）
         ↓
環境有 K8s？
    ├── 外部掃攻擊入口 → kube-hunter
    └── 內部合規掃描 → kube-bench
```

---

## 邊界情況：enumerate-iam 的 rate limiting

enumerate-iam 暴力打大量 API，AWS 可能觸發 rate limiting：

```bash
# 如果出現大量 TooManyRequestsException，加 --max-rate 限速
enumerate-iam --access-key AKIAIOSFODNN7EXAMPLE \
              --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
              --region us-east-1 \
              --max-rate 0.5
```

`--max-rate 0.5` 代表每秒最多 0.5 個請求（即 2 秒一個請求）。Rate limiting 同時也是一個 OPSEC 考量——真實 engagement 裡，大量 API call 可能觸發 GuardDuty 的異常行為偵測。

---

## 踩雷集錦

**安裝 Pacu 到系統 Python 導致依賴衝突** → 用 pipx 或 Python venv 隔離安裝。Pacu 的依賴比較重，和其他工具衝突的可能性高。

**enumerate-iam 跑完說什麼都不能做，實際上 credential 有一些權限** → 確認你傳的 key 是正確的格式，且沒有 session token（STS 臨時 key 需要 `--session-token`）。另一種可能是 SCP 從 Organizations 層面限制了，enumerate-iam 看到的是「允許」但 SCP 說「不行」。

**ScoutSuite 掃完 HTML 報告打不開（localhost 問題）** → 部分瀏覽器會拒絕載入本地 file:// 的 JavaScript。用 `python3 -m http.server 8080` 在報告目錄開一個 HTTP server 再用瀏覽器開。

**CloudFox `all-checks` 跑很久然後 timeout** → CloudFox 預設平行查詢，帳號裡資源很多時會很慢。用 `--max-concurrent-goroutines 5` 限制並發數，或用 `--services s3,iam` 只查關心的服務。

**Pacu 的攻擊模組實際修改了帳號設定** → Pacu 的部分模組（如 `iam__backdoor_users_keys`）是**主動攻擊模組**，執行後會在帳號留下修改。在不確定某個模組做什麼之前，用 `help <模組名>` 確認，並記錄你執行的每個模組以便事後清理。

---

## 進階延伸

**Cartography**：Lyft 開源的工具，把 AWS 帳號的資源關係圖入 Neo4j，然後用 Cypher query 找攻擊路徑。比 CloudFox 更適合大型環境的長期監控，但設定比較重。

**Steampipe**：用 SQL 語法查 AWS 資源和配置，Prowler 部分功能的底層也用它。`select * from aws_iam_role where assume_role_policy_document like '%:root%'` 這樣的 query 能直接找 trust policy 寫太寬的 role。

**truffleHog / gitleaks**：在 GitHub repo 或 git history 找外洩的 credential。雖然這是「初始存取前」的工具，但在 engagement 裡先掃一下 target 的 public repo 常常有意外收獲。

---

## 本章重點整理

- enumerate-iam：拿到 credential 的第一步，暴力測試哪些 API 能呼叫。
- Pacu：主動攻擊框架，70+ 模組，能實際執行 IAM 提權——有授權才能動破壞性模組。
- ScoutSuite：廣度配置掃描，HTML 報告，多雲支援，給評估結果用。
- Prowler：合規框架對齊（PCI/HIPAA/CIS），適合 CI/CD 自動化或合規報告。
- CloudFox：攻擊路徑映射，把 misconfig 連成鏈，幫你規劃最省力的路徑。
- kube-hunter / kube-bench：K8s 環境的外部攻擊面掃描 / 內部 CIS 合規掃描。
- 工具掃出「存在問題」，你判斷「是否可利用」——工具替代不了攻擊者的判斷。
- 所有工具只對有授權的目標使用，未授權使用是刑事犯罪。

---

## 自我檢核

- [ ] 我能說出 enumerate-iam 和直接讀 IAM policy 的差異，以及為什麼要用暴力測試
- [ ] 我能說出 Pacu 和 ScoutSuite 的定位差異（攻擊框架 vs 評估掃描）
- [ ] 我能說出 ScoutSuite 和 Prowler 的定位差異（安全評估 vs 合規框架）
- [ ] 我能說出 CloudFox 在整個工具鏈裡的位置（枚舉後、提權前）
- [ ] 我知道 kube-hunter 和 kube-bench 各自在什麼場景用
- [ ] 我能依照「拿到 credential 後」的決策樹選擇工具

---

## 延伸閱讀

1. **[enumerate-iam GitHub README](https://github.com/andresriancho/enumerate-iam)**
   看 `--help` 輸出和 `limitations` 一節。理解哪些 API 它不測試（例如 write API），以及 IAM condition key 可能導致 false positive 的情況。

2. **[Pacu Wiki — Modules 清單](https://github.com/RhinoSecurityLabs/pacu/wiki/Module-Details)**
   把所有模組按 `enum`、`privesc`、`exfil`、`persist` 分類。找出和 Ch 7（IAM privesc）直接相關的模組名稱，記下來——那些章節用 Pacu 做實作時會需要。

3. **[CloudFox GitHub — AWS Checks 清單](https://github.com/BishopFox/cloudfox)**
   看 `all-checks` 背後實際跑了哪些 check，以及每個 check 輸出存在哪個目錄。了解這個能讓你在 engagement 後更快找到有用的資訊。

4. **[Prowler 文件 — Compliance Frameworks](https://docs.prowler.cloud/en/latest/tutorials/compliance/)**
   看 CIS AWS Foundations Benchmark 的結構——每個 check 的 ID、描述、修補建議。這個和 Ch 34（防禦基本功）直接對應，先看一遍有助於攻防對照。

5. **[Aqua Security — kube-bench GitHub](https://github.com/aquasecurity/kube-bench)**
   看 README 裡的「Running kube-bench inside a Pod」一節——在 K8s Pod 內部跑 kube-bench 是 Part 5（K8s 攻擊）的重要偵察步驟，理解怎麼在 Pod 裡拿到 CIS check 結果。

---

工具認識完了，接下來進入課程最核心的部分：IAM 的心智模型。所有你剛才看的工具——enumerate-iam、Pacu、CloudFox——最終都在圍繞 IAM 轉。

→ [Ch 3 IAM 心智模型：principal / policy / role / trust（AWS↔Azure↔GCP）](./03-iam-mental-model.md)
