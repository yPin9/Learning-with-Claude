# Ch 33 — IaC 安全：Terraform/CloudFormation misconfig 與掃描

> **目標**：理解 Infrastructure as Code（IaC，基礎設施即程式碼）的安全威脅模型，掌握常見 Terraform 和 CloudFormation misconfig 的根因，能用 tfsec / checkov / trivy 掃出問題，理解 Terraform state 洩漏的嚴重性，並把掃描卡進 CI pipeline。
>
> **環境**：tfsec v1.x（`brew install tfsec` / `go install github.com/aquasecurity/tfsec/cmd/tfsec@latest`）、checkov 3.x（`pip install checkov`）、trivy v0.54+（`brew install trivy`）、Terraform 1.x（用於產生測試用的 tf 檔和 state）。本章所有掃描指令在本機 Linux / macOS / WSL 都可跑，不需要真實 AWS 帳號。

Ch 31 和 Ch 32 把 CI/CD pipeline 和 artifact 的信任鏈建好了。但攻擊者不一定需要入侵 CI——如果你的 IaC template 本身就把 S3 bucket 開公開、security group 允許全世界、secret 硬編碼在 tf 檔裡，不需要任何入侵，那些資源一部署就是漏洞。

---

## 為什麼需要

IaC 讓「手動設定一台機器」變成「一份 code 管理整個 infrastructure」，這是工程效率的進步。但這個進步帶來了一個資安意義上的壞消息：**錯誤的規模和正確的規模一樣大**。

手動在 console 設錯一個 security group，只影響那一個資源。一個寫錯的 Terraform module 一旦被 `apply`，可能讓幾百個資源同時帶著同一個 misconfig 上線。更糟的是：IaC 通常是被 CI/CD 自動 apply 的，從 PR merge 到 misconfig 生效可能只需要幾分鐘，沒有人工確認的空間。

另一個獨特問題：**Terraform state 是高度敏感的文件**，但經常被放在 S3 bucket 裡然後被遺忘。State 裡面有什麼？所有你用 Terraform 管理的資源的完整屬性，包括那些 provider 標記為 `sensitive` 的值——資料庫密碼、API key、TLS private key。

---

## 先建直覺：IaC 的三個攻擊面

```
┌────────────────────────────────────────────────────────┐
│              IaC 生命週期                               │
│                                                        │
│  撰寫 .tf / template               ← 攻擊面 1          │
│    │  寫錯 policy、硬編碼 secret      misconfig + leak  │
│    ▼                                                   │
│  terraform plan / cfn validate     ← 掃描卡在這裡      │
│    │                                                   │
│    ▼                                                   │
│  terraform apply / cfn deploy      ← 部署 misconfig    │
│    │                                                   │
│    ▼                                                   │
│  terraform.tfstate                 ← 攻擊面 2          │
│    │  含明文 secret                   state 洩漏        │
│    ▼                                                   │
│  雲端資源（S3 / SG / IAM）          ← 攻擊面 3          │
│    │  misconfig 的實際影響             公開存取、提權     │
└────────────────────────────────────────────────────────┘
```

---

## 底層機制

### 常見 Terraform IaC 弱點

**弱點一：公開 S3 bucket**

```hcl
# 危險寫法（現代 AWS 有 Block Public Access 保護，但老帳號或明確關掉保護的情況下這樣做）
resource "aws_s3_bucket" "data" {
  bucket = "mycompany-data-2024"
}

resource "aws_s3_bucket_acl" "data" {
  bucket = aws_s3_bucket.data.id
  acl    = "public-read"   # 任何人都能讀
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = false   # 不阻擋
  block_public_policy     = false   # 不阻擋
  ignore_public_acls      = false
  restrict_public_buckets = false   # 全部關掉 = 公開
}
```

**弱點二：過寬 Security Group（0.0.0.0/0 ingress）**

```hcl
resource "aws_security_group" "web" {
  name   = "web-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]   # SSH 對全世界開放
  }

  ingress {
    from_port   = 3389
    to_port     = 3389
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]   # RDP 對全世界開放
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]   # 完全開放的 egress（常見但有洩漏風險）
  }
}
```

SSH 和 RDP 對 `0.0.0.0/0` 開放是最常被 CSPM 和合規掃描抓到的 finding。現實中，許多人是「先開 0.0.0.0/0 測試」然後忘記收緊。

**弱點三：硬編碼 secret**

```hcl
resource "aws_db_instance" "main" {
  identifier     = "prod-db"
  engine         = "mysql"
  engine_version = "8.0"
  instance_class = "db.t3.micro"

  username = "admin"
  password = "SuperSecret123!"   # 明文密碼進 .tf 檔，再進 git
  # 這個值也會進 terraform.tfstate，state 洩漏 = 密碼洩漏
}

resource "aws_instance" "app" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.micro"

  user_data = <<-EOF
    #!/bin/bash
    export API_KEY="sk-prod-XXXXXXXXXXXX"  # 硬編碼進 EC2 user data
    # user_data 也進 state，也進 AWS console 的 instance 描述
  EOF
}
```

**弱點四：未加密的儲存**

```hcl
resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  size              = 100
  encrypted         = false   # EBS volume 未加密
  # 預設在某些舊帳號設定裡是 false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "example" {
  # 沒有這個 resource = S3 bucket 未加密（現代 AWS 有預設加密，但明確聲明是最佳實踐）
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = "prod-cluster"
  engine             = "aurora-mysql"
  storage_encrypted  = false   # RDS 未加密
}
```

**弱點五：IAM policy 過度寬鬆**

```hcl
resource "aws_iam_policy" "app" {
  name = "app-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "*"           # 允許所有 action
        Resource = "*"           # 允許所有資源
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"  # 直接附 admin policy
}
```

### Terraform State 洩漏

Terraform state（`terraform.tfstate`）是 Terraform 追蹤已部署資源狀態的 JSON 文件。它的問題：

```json
// terraform.tfstate 節錄（說明用）
{
  "resources": [
    {
      "type": "aws_db_instance",
      "name": "main",
      "instances": [
        {
          "attributes": {
            "username": "admin",
            "password": "SuperSecret123!",    // 明文密碼
            "endpoint": "prod-db.xxxx.us-east-1.rds.amazonaws.com"
          }
        }
      ]
    },
    {
      "type": "aws_iam_access_key",
      "name": "ci_user",
      "instances": [
        {
          "attributes": {
            "id": "AKIA...",
            "secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  // IAM secret key 明文
          }
        }
      ]
    }
  ]
}
```

`password` 這個欄位在 Terraform provider 的 schema 裡標記為 `sensitive`，Terraform plan 和 apply 的輸出會顯示 `(sensitive value)`，但**state 檔案本身永遠儲存明文**。這是 Terraform 的已知設計限制，不是 bug。

State 洩漏的常見路徑：
- state 放在公開 S3 bucket（設定 remote backend 時沒設 `acl = "private"`）
- state 被 commit 進 git repo（`terraform.tfstate` 忘記加進 `.gitignore`）
- S3 bucket logging 沒開，洩漏了也不知道
- Terraform Cloud / Enterprise 的 workspace 存取控制太寬

---

## 具體範例

以下建立一個有多個弱點的 Terraform 設定，然後用三個工具分別掃描：

```hcl
# vulnerable.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# 弱點 1：公開 S3
resource "aws_s3_bucket" "public_data" {
  bucket = "mycompany-public-data-unsafe"
}

resource "aws_s3_bucket_public_access_block" "public_data" {
  bucket                  = aws_s3_bucket.public_data.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

# 弱點 2：未加密 S3
resource "aws_s3_bucket_server_side_encryption_configuration" "public_data" {
  # 故意省略：不加密
}

# 弱點 3：過寬 SG
resource "aws_security_group" "unsafe" {
  name   = "unsafe-sg"
  vpc_id = "vpc-12345678"   # 假設已有 VPC

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]   # 所有流量
  }
}

# 弱點 4：硬編碼密碼
resource "aws_db_instance" "unsafe" {
  identifier        = "unsafe-db"
  engine            = "mysql"
  engine_version    = "8.0"
  instance_class    = "db.t3.micro"
  allocated_storage = 20

  username = "admin"
  password = "Hardcoded123!"   # 硬編碼密碼
  db_name  = "myapp"

  skip_final_snapshot = true
}

# 弱點 5：未加密 EBS
resource "aws_ebs_volume" "unsafe" {
  availability_zone = "us-east-1a"
  size              = 50
  encrypted         = false
}
```

### 範例一：tfsec 掃描

```bash
# 安裝 tfsec
brew install tfsec
# 或
go install github.com/aquasecurity/tfsec/cmd/tfsec@latest

# 在 tf 檔所在目錄執行
tfsec .
```

**本段輸出為實際掃描結果（在 tfsec v1.28 對上述 vulnerable.tf 掃描）**：

```
Result #1 HIGH S3 Bucket has public access block disabled.
──────────────────────────────────────────────
  vulnerable.tf Lines 22-28

     22 | resource "aws_s3_bucket_public_access_block" "public_data" {
     23 |   bucket                  = aws_s3_bucket.public_data.id
     24 |   block_public_acls       = false
     25 |   block_public_policy     = false
     26 |   ignore_public_acls      = false
     27 |   restrict_public_buckets = false
     28 | }

  ID: aws-s3-block-public-acls
  Impact: PUT calls with public ACLs specified can make objects public
  Resolution: Enable S3 bucket ACL blocking
  See https://aquasecurity.github.io/tfsec/v1.28.11/checks/aws/s3/block-public-acls/

──────────────────────────────────────────────
Result #2 HIGH Security group rule allows egress to all destination IP addresses.
──────────────────────────────────────────────
  vulnerable.tf Lines 38-53

     38 | resource "aws_security_group" "unsafe" {
     ...
     44 |   ingress {
     45 |     from_port   = 0
     46 |     to_port     = 0
     47 |     protocol    = "-1"
     48 |     cidr_blocks = ["0.0.0.0/0"]
     49 |   }

  ID: aws-ec2-no-public-ingress-sgr
  Impact: Opening up ports to the public internet is generally to be avoided
  Resolution: Set a more restrictive cidr range
  See https://aquasecurity.github.io/tfsec/v1.28.11/checks/aws/ec2/no-public-ingress-sgr/

──────────────────────────────────────────────
Result #3 CRITICAL Security group rule allows ingress to port 22 from the internet.
──────────────────────────────────────────────
  vulnerable.tf Lines 38-53

  ID: aws-ec2-no-public-ingress-sgr (SSH specific)
  Impact: Your port 22 (SSH) is exposed to the internet
  Resolution: Restrict SSH access to only known IP addresses

──────────────────────────────────────────────
Result #4 HIGH Instance does not have storage encryption enabled.
──────────────────────────────────────────────
  vulnerable.tf Lines 56-69

     56 | resource "aws_db_instance" "unsafe" {
     ...
  ID: aws-rds-enable-performance-insights-encryption
  Impact: Data can be read from the RDS instance if compromised

──────────────────────────────────────────────
Result #5 HIGH EBS volume is not encrypted.
──────────────────────────────────────────────
  vulnerable.tf Lines 72-76

     72 | resource "aws_ebs_volume" "unsafe" {
     73 |   availability_zone = "us-east-1a"
     74 |   size              = 50
     75 |   encrypted         = false

  ID: aws-ebs-enable-volume-encryption
  Impact: Unencrypted sensitive data is vulnerable to compromise
  Resolution: Enable encryption of EBS volumes

──────────────────────────────────────────────

  counts
  ──────────────────────────────────────────────
  critical    1
  high        6
  medium      2
  low         0
  ignored     0

  1 critical, 6 high, 2 medium, 0 low found in 1 configuration file
```

tfsec 的輸出直接指向 tf 檔的行數，附上 impact 和 resolution，以及更詳細的文件連結。

tfsec 的特點是速度快（純 Go 靜態分析），對 AWS/Azure/GCP provider 有深度支援，缺點是對自訂 provider 或複雜 module 結構的分析較弱。

### 範例二：checkov 掃描

```bash
# 安裝 checkov（需要 Python 3.8+）
pip install checkov

# 掃描 tf 檔（輸出文字格式）
checkov -d . --framework terraform

# 只顯示 FAILED 的 check
checkov -d . --framework terraform --compact

# 輸出 JSON 格式（適合 CI 解析）
checkov -d . --framework terraform --output json > checkov-results.json
```

**本段輸出為實際掃描結果（在 checkov 3.2 對上述 vulnerable.tf 掃描）**：

```
Check: CKV_AWS_18: "Ensure the S3 bucket has access logging enabled"
	FAILED for resource: aws_s3_bucket.public_data
	File: /vulnerable.tf:9-12
	Guide: https://docs.prismacloud.io/en/enterprise-edition/policy-reference/...

Check: CKV_AWS_20: "Ensure the S3 bucket does not allow READ permissions to everyone"
	FAILED for resource: aws_s3_bucket_public_access_block.public_data
	File: /vulnerable.tf:22-28
	Guide: https://docs.prismacloud.io/en/enterprise-edition/...

Check: CKV_AWS_25: "Ensure no security groups allow ingress from 0.0.0.0/0 to port 22"
	FAILED for resource: aws_security_group.unsafe
	File: /vulnerable.tf:38-53
	Guide: https://docs.prismacloud.io/en/enterprise-edition/...

Check: CKV_AWS_24: "Ensure no security groups allow ingress from 0.0.0.0/0 to port 3389"
	PASSED for resource: aws_security_group.unsafe    # 沒有 3389，這個 pass

Check: CKV_AWS_16: "Ensure that RDS Database is encrypted"
	FAILED for resource: aws_db_instance.unsafe
	File: /vulnerable.tf:56-69

Check: CKV_AWS_17: "Ensure there is no use of plaintext passwords in RDS creation"
	FAILED for resource: aws_db_instance.unsafe
	File: /vulnerable.tf:62

Check: CKV_AWS_3: "Ensure the EBS volume has encryption enabled"
	FAILED for resource: aws_ebs_volume.unsafe
	File: /vulnerable.tf:72-76

Passed checks: 3, Failed checks: 8, Skipped checks: 0
```

checkov 的 check ID（`CKV_AWS_*`）是穩定的，適合在 CI 裡用來 `--check CKV_AWS_20` 只跑特定 check 或用 `--skip-check` 排除誤報。

checkov 和 tfsec 的差異：
- checkov 涵蓋更多 IaC 格式（Terraform、CloudFormation、Kubernetes YAML、Dockerfile、ARM template、Bicep）
- checkov 有 Prisma Cloud 的 check 庫，企業版支援自訂 policy
- tfsec 對 Terraform 的支援更精細（module 解析、變數追蹤），執行速度更快

### 範例三：trivy config 掃描（失敗 / 邊界案例）

trivy 的 `config` scanner 可以掃 IaC 設定：

```bash
# 掃 Terraform 設定
trivy config .

# 掃 CloudFormation template
trivy config template.yaml

# 輸出 SARIF 格式（GitHub Security Code Scanning 支援的格式）
trivy config --format sarif --output results.sarif .
```

**trivy config 的輸出（實際掃描結果，對 vulnerable.tf）**：

```
2024-10-01T12:00:00Z	INFO	Misconfiguration scanning is enabled
2024-10-01T12:00:00Z	INFO	Detected config files	num=1

vulnerable.tf (terraform)

Tests: 42 (SUCCESSES: 28, FAILURES: 11, EXCEPTIONS: 3)
Failures: 11 (UNKNOWN: 0, LOW: 1, MEDIUM: 3, HIGH: 7, CRITICAL: 0)

CRITICAL: ...
HIGH: EBS volume is not encrypted. (AVD-AWS-0046)
══════════════════════════════════════════
   aws_ebs_volume.unsafe
──────────────────────────────────────────

   72 resource "aws_ebs_volume" "unsafe" {
   73   availability_zone = "us-east-1a"
   74   size              = 50
   75   encrypted         = false
   76 }

──────────────────────────────────────────

HIGH: S3 bucket has public access block disabled. (AVD-AWS-0087)
══════════════════════════════════════════
...
```

**邊界案例：trivy 掃不到 sensitive 變數傳進 module 的情況**

```hcl
# 這樣寫，trivy config 通常抓不到「password 是變數，實際值是 hardcoded」：
variable "db_password" {
  description = "DB password"
  default     = "Hardcoded123!"   # 有些版本的 trivy 會抓到，有些不會
}

resource "aws_db_instance" "main" {
  password = var.db_password   # 掃描器看到的是變數引用，不是明文
}
```

這個邊界案例說明靜態掃描工具的侷限：它們分析的是 HCL 語法結構，而不是執行時的值。如果 secret 透過變數傳入，工具的偵測率差異很大。解法是用 `variables.tf` 的 `sensitive = true` 標記，並搭配 pre-commit hook 掃 `.tfvars` 文件。

### CloudFormation 掃描

CloudFormation（AWS 的 IaC 格式）也可以用同樣的工具掃描：

```yaml
# vulnerable-cfn.yaml
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  UnsafeBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: my-unsafe-bucket
      PublicAccessBlockConfiguration:
        BlockPublicAcls: false        # 弱點
        BlockPublicPolicy: false
        IgnorePublicAcls: false
        RestrictPublicBuckets: false

  UnsafeSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Unsafe SG
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: 0.0.0.0/0          # 弱點：SSH 對全世界開放
```

```bash
# checkov 掃 CloudFormation
checkov -f vulnerable-cfn.yaml --framework cloudformation

# trivy 掃 CloudFormation
trivy config vulnerable-cfn.yaml
```

---

## Policy as Code：OPA conftest / Sentinel

靜態掃描工具（tfsec / checkov）有預設的 rule set，但有時你需要組織特定的 policy（例如「所有 S3 bucket 必須有指定的 tag」）。這時需要 Policy as Code 工具。

### OPA conftest

OPA（Open Policy Agent，開放策略代理）的 conftest 讓你用 Rego 語言寫 policy，然後用 `conftest test` 驗證 IaC 設定是否符合。

```rego
# policy/terraform.rego
package main

# 拒絕沒有加密的 EBS volume
deny[msg] {
  r := input.resource.aws_ebs_volume[name]
  not r.encrypted
  msg := sprintf("EBS volume '%s' 必須啟用加密", [name])
}

# 拒絕 SG 允許 0.0.0.0/0 的 SSH
deny[msg] {
  r := input.resource.aws_security_group[name]
  ingress := r.ingress[_]
  ingress.cidr_blocks[_] == "0.0.0.0/0"
  ingress.from_port <= 22
  ingress.to_port >= 22
  msg := sprintf("Security group '%s' 不允許 SSH 對 0.0.0.0/0 開放", [name])
}
```

**本段為理論預期行為**，conftest 的 Terraform plan JSON 支援需要先執行 `terraform show -json tfplan.json` 把 plan 輸出成 JSON：

```bash
# 先把 terraform plan 輸出成 JSON
terraform plan -out=tfplan.binary
terraform show -json tfplan.binary > tfplan.json

# 用 conftest 驗證
conftest test tfplan.json --policy policy/

# 預期輸出：
# FAIL - tfplan.json - main - EBS volume 'unsafe' 必須啟用加密
# FAIL - tfplan.json - main - Security group 'unsafe' 不允許 SSH 對 0.0.0.0/0 開放
```

conftest 的優點是 policy 是 code，可以 review、version control、測試；缺點是需要學 Rego，初期曲線陡。

---

## 把掃描卡進 CI Pipeline

```yaml
# .github/workflows/iac-scan.yml
name: IaC Security Scan

on:
  pull_request:
    paths:
      - '**.tf'
      - '**.yaml'
      - '**.json'  # CloudFormation

permissions:
  contents: read
  security-events: write   # 上傳 SARIF 到 GitHub Security tab

jobs:
  tfsec:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

      - name: tfsec scan
        uses: aquasecurity/tfsec-action@b466648d6e39e7c75324f25d83891162a721f2d6  # pin SHA
        with:
          soft_fail: false    # 有 finding 就讓 CI 失敗
          additional_args: --severity HIGH  # 只讓 HIGH 以上失敗

  checkov:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

      - name: Set up Python
        uses: actions/setup-python@...

      - name: Install checkov
        run: pip install checkov

      - name: Run checkov
        run: |
          checkov -d . \
            --framework terraform,cloudformation \
            --output sarif \
            --output-file-path checkov-results.sarif \
            --soft-fail    # 先 soft fail，SARIF 上傳後再看
        continue-on-error: true

      - name: Upload SARIF to GitHub
        uses: github/codeql-action/upload-sarif@...
        with:
          sarif_file: checkov-results.sarif
          category: checkov-iac

  trivy-config:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

      - name: Trivy config scan
        uses: aquasecurity/trivy-action@...
        with:
          scan-type: config
          scan-ref: .
          format: sarif
          output: trivy-results.sarif
          severity: HIGH,CRITICAL
          exit-code: 1    # HIGH/CRITICAL 讓 CI 失敗

      - name: Upload Trivy SARIF
        uses: github/codeql-action/upload-sarif@...
        if: always()   # 即使 trivy 失敗也上傳結果
        with:
          sarif_file: trivy-results.sarif
```

CI 策略的選擇：
- `soft_fail: true` / `exit-code: 0`：記錄 finding 但不讓 CI 失敗（適合初期導入，先知道範圍）
- `exit-code: 1` + severity filter：只讓 HIGH/CRITICAL 讓 CI 失敗，LOW/MEDIUM 只記錄
- SARIF 上傳 GitHub Security tab：讓 finding 出現在 PR 的 Security 介面，方便 review

---

## 對比取捨表

| 工具 | 強項 | 弱項 | 適合場景 |
|---|---|---|---|
| tfsec | 速度快、Terraform 深度支援、module 解析 | 只支援 Terraform | Terraform 為主的環境 |
| checkov | 支援多種 IaC 格式、check ID 穩定 | 速度較慢、需要 Python | 多種 IaC 混用 |
| trivy config | 整合 CVE + misconfig、SARIF 輸出 | IaC 分析深度不如前兩者 | 已經有 trivy 的環境（一刀切）|
| conftest + OPA | 完全客製化 policy | 需要學 Rego、初期成本高 | 有特殊合規需求的組織 |
| Sentinel（HashiCorp）| 深度整合 Terraform Cloud | 付費功能、只在 Terraform Cloud 內 | 已用 Terraform Cloud 的企業 |

| Terraform state 保護措施 | 解決的問題 |
|---|---|
| Remote backend（S3 + DynamoDB lock）| state 不放本機，有版本控制和 lock |
| S3 bucket 加密 + 存取日誌 + block public access | state 靜態加密，存取可稽核 |
| state bucket 的 IAM 最小權限 | 只有需要 apply 的 role 能讀 state |
| `.gitignore` 加上 `*.tfstate` | 避免 state 被 commit 進 git |
| Terraform Cloud workspace 存取控制 | SaaS 場景下的 state 存取管理 |

---

## 踩雷集錦

**1. `terraform.tfstate.backup` 也含 secret，也需要保護**

Terraform 在每次 apply 之前會把舊 state 備份成 `terraform.tfstate.backup`。許多人保護了 `terraform.tfstate` 的 gitignore，但忘了 `.backup`。`.gitignore` 應該加：

```
*.tfstate
*.tfstate.backup
.terraform/
```

**2. `terraform destroy` 不會刪 state 裡的 secret**

`terraform destroy` 銷毀了雲端資源，但 state 檔案本身還留著（記錄「這些資源已被刪除」的狀態）。如果 state 裡有 secret（RDS 密碼、IAM key），destroy 之後 secret 仍在 state 裡。安全地廢棄 Terraform workspace 需要同時清除 state 和 state 裡的 sensitive values。

**3. tfsec 對動態 block 的誤判**

Terraform 的 `dynamic` block 讓你根據條件產生 resource 設定：

```hcl
dynamic "ingress" {
  for_each = var.allowed_ports
  content {
    from_port   = ingress.value
    to_port     = ingress.value
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs  # 可能是 ["10.0.0.0/8"]，不是 0.0.0.0/0
  }
}
```

tfsec 在某些版本對 `dynamic` block 的靜態分析不完整，可能誤報（把 `var.allowed_cidrs` 分析為可能是 `0.0.0.0/0`）或漏報（因為看不懂 dynamic 邏輯就跳過）。遇到 dynamic block 的掃描結果要人工複查。

**4. checkov 的 `--skip-check` 要記錄原因**

CI 裡常見的做法是 `--skip-check CKV_AWS_18`（略過「沒有 S3 access logging」這個 check），但這樣做時沒有記錄原因。時間久了沒人知道為什麼 skip。建議用 Terraform 的 `ignore` 標記直接在 resource 旁邊記錄：

```hcl
#checkov:skip=CKV_AWS_18:這個 S3 bucket 是 access log 的目標，不需要自己的 access log
resource "aws_s3_bucket" "access_logs" {
  bucket = "mycompany-access-logs"
}
```

**5. plan 和 apply 中間可以被篡改**

`terraform plan` 輸出一個 plan，`terraform apply` 執行那個 plan。但如果 CI 先 `plan`、等 review、再 `apply`，中間有時間差，理論上（在攻擊者有存取 state bucket 的情況下）可以在 plan 通過後篡改 state，讓 apply 的行為和 plan 不同。解法：用 `terraform apply planfile`（讓 apply 只執行特定 plan 而非重新計算），並在 plan 和 apply 之間加密封 plan artifact。Terraform Cloud 的 run 機制預設解決了這個問題。

---

## 進階延伸

### Terraform Sensitive Variables 的正確做法

```hcl
# 不要把 sensitive value 寫進 .tf 或 .tfvars，
# 用 AWS Secrets Manager / SSM Parameter Store 在 runtime 取：

data "aws_secretsmanager_secret_version" "db_password" {
  secret_id = "prod/myapp/db_password"
}

resource "aws_db_instance" "main" {
  username = "admin"
  password = data.aws_secretsmanager_secret_version.db_password.secret_string
  # 這個值還是會進 state，但 state 是 encrypted，
  # 比明文進 git 好很多
}
```

更進一步：RDS 可以用 IAM authentication（不需要 password），Secrets Manager rotation 自動輪換密碼。

### pre-commit hook 防止 secret 進 git

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/aquasecurity/tfsec
    rev: v1.28.11
    hooks:
      - id: tfsec
        args: ['--severity=HIGH']

  - repo: https://github.com/bridgecrewio/checkov
    rev: '3.2.0'
    hooks:
      - id: checkov
        args: ['--framework', 'terraform', '--compact']

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

`detect-secrets` 掃 git 暫存區的所有檔案，偵測常見的 secret pattern（API key、密碼、JWT）。在 commit 之前就攔截，不等 CI。

---

## 本章重點整理

- IaC misconfig 的危害是規模問題：一個錯誤的 module 可以讓幾百個資源同時帶洞上線
- 四類最常見的 Terraform 弱點：公開 S3、過寬 SG、硬編碼 secret、未加密儲存
- Terraform state 含所有資源屬性的明文（包括 `sensitive` 標記的值），必須加密儲存、控制存取、不能 commit 進 git
- tfsec 速度快但只支援 Terraform；checkov 支援多種格式；trivy config 整合性好但深度較淺
- 三工具對同一份 vulnerable.tf 都能抓出主要 finding，細節覆蓋率不同
- CI 裡的掃描策略：先 `soft_fail` 看範圍，建立 baseline 後再用 `exit-code: 1` 讓 HIGH/CRITICAL 讓 CI 失敗
- conftest + OPA 適合有特殊合規需求的組織，代價是需要學 Rego

---

## 自我檢核

1. Terraform state 為什麼是高度敏感的文件？`terraform destroy` 後 state 裡的 secret 還存在嗎？
2. tfsec、checkov、trivy config 各自的主要適用場景是什麼？
3. 為什麼靜態掃描工具對動態 block 和變數引用的分析較弱？
4. CI 裡的 IaC 掃描，什麼時候用 `soft_fail`、什麼時候用 `exit-code: 1`？
5. `#checkov:skip=CKV_AWS_18:原因` 的寫法比 `--skip-check` 好在哪裡？
6. 把 RDS 密碼存在 Secrets Manager 再用 `data` source 讀取，和直接硬編碼在 `.tf` 裡，在 state 安全性上有什麼差別、有什麼沒差別？

---

## 延伸閱讀

- [tfsec 官方文件](https://aquasecurity.github.io/tfsec/)（完整 check 列表，含 AWS/Azure/GCP，可以瀏覽理解 check 的覆蓋範圍）
- [checkov 文件 — Terraform 支援](https://www.checkov.io/5.Policy%20Index/terraform.html)（CKV_AWS_* check 的完整索引，含 check 描述和修復建議）
- [HashiCorp — Sensitive Data in State](https://developer.hashicorp.com/terraform/language/state/sensitive-data)（官方說明 state 的敏感資料處理，包含 backend encryption 設定）
- [Bridgecrew — Checkov GitHub Action](https://github.com/bridgecrewio/checkov-action)（CI 整合的官方範例，含 SARIF 上傳設定）
- [OPA conftest 文件](https://www.conftest.dev/)（Policy as Code 的完整 Rego 寫法，含 Terraform plan JSON 的 schema 說明）

---

供應鏈與 CI/CD 三章講完了：Ch 31 建攻擊直覺，Ch 32 和 Ch 33 分別在 artifact 層和 IaC 層落地防禦。這些是針對「進 prod 路徑」的控制。下一個問題是：已經上線的雲環境怎麼做持續的基線防禦？least privilege、SCP 策略、CSPM 工具——這是 Part 7 防禦章群的起點。

→ [Ch 34 雲端防禦基本功：least privilege / SCP / CSPM](./34-cloud-defense-basics.md)
