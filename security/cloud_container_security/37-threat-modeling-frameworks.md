# Ch 37 — 威脅建模與框架：MITRE ATT&CK for Cloud、CIS Benchmark、STRIDE

> **目標**：把前 36 章學過的攻擊技術系統化對應到業界框架，掌握 MITRE ATT&CK for Cloud / Containers、STRIDE 設計階段威脅建模、CIS Benchmark 合規基準三種工具的使用時機與侷限，並能用框架語言與非技術利害關係人溝通資安風險。

---

## 為什麼要有框架，而不是隨手想想

讀完前 36 章，你知道 PassRole 可以提權、SSRF 可以偷 IMDS token、hostPath 可以逃逸到節點。但如果今天有人問你「這個新雲端架構有哪些威脅？」，你怎麼回答？

靠「我想到什麼就說什麼」的做法有幾個根本問題：

**遺漏率高**。人類的注意力是有偏好的。你最近剛打過容器逃逸，就會集中想逃逸；最近在搞 IAM，就會集中想提權。你不記得的攻擊技術，就消失在你的分析裡。

**說服力低**。你跟客戶說「我覺得這個 S3 bucket 很危險」，他問「你是怎麼評估的？」如果你答不出來系統性的方法，這個 finding 的可信度就是問號。相反地，你說「根據 CIS AWS Foundations Benchmark 3.0 的 2.1.5 控制點，這個 bucket 未開啟伺服器端加密」，他知道這是有出處的標準，不是你個人感覺。

**無法重複**。每次評估都靠個人記憶，換個工程師就換了個結果。框架讓評估可以被文件化、被複現、被外部審查。

三種框架的定位是不同的，要搞清楚在哪個場景用哪個：

```
┌─────────────────────────────────────────────────────────────────┐
│                  三種框架的定位                                    │
│                                                                   │
│  STRIDE                ATT&CK                CIS Benchmark        │
│  ─────────             ──────                ─────────────        │
│  設計時間              攻擊分析               配置檢查             │
│  問：可能被做什麼       問：攻擊者實際怎麼做   問：設定對不對        │
│  產出：威脅清單         產出：TTP 對應         產出：pass/fail 清單  │
│  使用者：架構師         使用者：紅藍隊          使用者：DevSecOps     │
│  時機：設計 review      時機：red team report  時機：CI/CD 掃描      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 先建直覺：框架是攻擊的地圖，不是攻擊本身

想像一個圖書館管理員在幫書分類。攻擊技術就是那些書，而 ATT&CK 是杜威十進位分類法。分類法本身不能告訴你書的內容，但它讓你能說「這本書在 T1078.004，你去那個書架」——比「有本書放在 IAM 那邊」精確得多。

用了框架之後，你的分析從「這個環境有 IAM 問題、容器逃逸問題、日誌問題」變成「環境暴露面包含 T1078.004、T1611、T1562.008，對應到 Credential Access、Execution、Defense Evasion 三個 Tactic，需要優先處理 Persistence 鏈」。

---

## MITRE ATT&CK for Cloud（IaaS Matrix）

MITRE ATT&CK（Adversarial Tactics, Techniques, and Common Knowledge，對抗性戰術、技術與通識知識）是一個基於真實觀測攻擊行為的知識庫，由 MITRE 組織維護。

ATT&CK 有多個 Matrix，對雲端工程師最相關的是：

- **Cloud Matrix**（IaaS）：AWS / Azure / GCP 的 API 攻擊面
- **Containers Matrix**：Docker / Kubernetes 特定技術
- **Enterprise Matrix**：通用企業，部分 technique 與上兩者重疊

Cloud Matrix 有 14 個 Tactic（戰術），每個 Tactic 下有多個 Technique（技術），部分 Technique 有 Sub-technique（細分技術）。Tactic 是「目的」，Technique 是「手段」。

### 本課攻擊技術 × ATT&CK Technique ID 對照表

下表把前 36 章出現過的攻擊技術對應到 ATT&CK Technique ID。Tactic 欄是攻擊者用這個手段的目的。

| 本課章節 | 攻擊技術 | ATT&CK ID | Technique 名稱 | Tactic |
|---------|---------|-----------|----------------|--------|
| Ch 6 | enumerate-iam 掃描 IAM 權限 | T1526 | Cloud Service Discovery | Discovery |
| Ch 7 | PassRole 提權到更高權限 role | T1548.005 | Abuse Elevation Control Mechanism: Temporary Elevated Cloud Access | Privilege Escalation |
| Ch 7 | CreatePolicyVersion 替換 policy | T1484.001 | Domain or Tenant Policy Modification: Group Policy Modification | Defense Evasion |
| Ch 8 | AssumeRole 跨帳號存取 | T1199 | Trusted Relationship | Initial Access |
| Ch 10 | SSRF 打 IMDS 偷 EC2 credential | T1552.005 | Unsecured Credentials: Cloud Instance Metadata API | Credential Access |
| Ch 12 | 從程式碼或環境變數撈 secret | T1552.001 | Unsecured Credentials: Credentials In Files | Credential Access |
| Ch 14 | 新增 shadow IAM user / access key | T1098.001 | Account Manipulation: Additional Cloud Credentials | Persistence |
| Ch 14 | 新增惡意 trust policy 後門 role | T1098.003 | Account Manipulation: Additional Cloud Roles | Persistence |
| Ch 15 | StopLogging / DeleteTrail | T1562.008 | Impair Defenses: Disable Cloud Logs | Defense Evasion |
| Ch 15 | 繞過 CloudTrail 用不記 log 的 API | T1562.008 | Impair Defenses: Disable Cloud Logs | Defense Evasion |
| Ch 17 | privileged container 逃逸 | T1611 | Escape to Host | Privilege Escalation |
| Ch 17 | hostPath 掛 /etc 逃逸 | T1611 | Escape to Host | Privilege Escalation |
| Ch 25 | 打開 anonymous API server 存取 | T1613 | Container and Resource Discovery | Discovery |
| Ch 27 | hostPath + hostPID Pod 逃逸 | T1611 | Escape to Host | Privilege Escalation |
| Ch 29 | 惡意 admission webhook 後門 | T1505.003 | Server Software Component: Web Shell（近似）| Persistence |
| Ch 31 | CI/CD OIDC 信任濫用 | T1195.002 | Supply Chain Compromise: Compromise Software Supply Chain | Initial Access |
| Ch 8 | Confused Deputy 跨服務 | T1078.004 | Valid Accounts: Cloud Accounts | Defense Evasion |
| Ch 13 | 修改 Security Group 開後門 | T1562.007 | Impair Defenses: Disable or Modify Cloud Firewall | Defense Evasion |
| Ch 9 | S3 bucket 公開存取外洩資料 | T1530 | Data from Cloud Storage | Collection |
| Ch 9 | S3 Replication 複製資料到攻擊者 bucket | T1537 | Transfer Data to Cloud Account | Exfiltration |

幾個常用 Tactic 的說明，幫助你記住這個 taxonomy 的邏輯：

- **Initial Access**（初始存取）：攻擊者怎麼第一次進來。雲端最常見路徑是偷到的 access key、公開的 S3 物件、或 CI/CD 的 OIDC token。
- **Credential Access**（憑證存取）：專門搶憑證。IMDS 偷 token、從程式碼掃 secret 都在這。
- **Persistence**（持久化）：確保踢出去之後還能回來。shadow IAM user、後門 role trust policy。
- **Defense Evasion**（規避防禦）：讓防守者看不到。刪 trail、關 GuardDuty、修改 SCP。
- **Exfiltration**（資料外洩）：把資料帶走。S3 Replication 到外部帳號是雲端特有手法。

### 查詢 ATT&CK Navigator

ATT&CK Navigator 是 MITRE 提供的視覺化工具，可以在 technique 上塗色，製作出你的 coverage map（覆蓋圖）。

**本段未實測，為理論預期行為**

```bash
# ATT&CK Navigator 是 Web UI，在 https://mitre-attack.github.io/attack-navigator/
# 也可以本地起：
git clone https://github.com/mitre-attack/attack-navigator.git
cd attack-navigator/nav-app
npm install && npm start
# 然後打開 localhost:4200，選 Cloud Matrix，在本課對應的 technique 上塗色
# 匯出 JSON 可以附在 pentest 報告裡當 heatmap
```

自驗方法：ATT&CK Navigator UI 是純靜態，不需要後端，本地執行後選 "Create New Layer" → "Cloud"，手動標記上表的 Technique ID，確認 heatmap 生成正確。

---

## MITRE ATT&CK for Containers Matrix

Containers Matrix 是專門針對容器技術的，與 Cloud（IaaS）Matrix 有交集但不完全重疊。最核心的差異是三個容器特有 Technique：

| Technique ID | 名稱 | 說明 |
|-------------|------|------|
| T1610 | Deploy Container | 攻擊者部署惡意 container 執行 payload |
| T1611 | Escape to Host | 從容器逃出到 host，這是 Part 3+5 的核心 |
| T1613 | Container and Resource Discovery | 探索 cluster 內有哪些資源、哪些 service account |

本課 Part 3+5 攻擊對應 Container Technique：

| 本課章節 | 攻擊技術 | ATT&CK ID | Tactic |
|---------|---------|-----------|--------|
| Ch 17 | privileged container 拿 /dev 裝置 | T1611 | Privilege Escalation |
| Ch 17 | capabilities（CAP_SYS_ADMIN）逃逸 | T1611 | Privilege Escalation |
| Ch 18 | runc CVE（CVE-2019-5736）overwrite | T1611 | Privilege Escalation |
| Ch 19 | 映像層內藏 secret | T1552.001 | Credential Access |
| Ch 25 | anonymous API server Discovery | T1613 | Discovery |
| Ch 26 | RBAC verb 提權（get secrets）| T1078.001 | Valid Accounts: Default Accounts |
| Ch 27 | hostPath 掛 host /etc | T1611 | Privilege Escalation |
| Ch 28 | 節點 token 換 cloud IAM role | T1078.004 | Valid Accounts: Cloud Accounts |
| Ch 29 | 部署後門 DaemonSet | T1610 | Execution |
| Ch 29 | 惡意 MutatingWebhook | T1610 | Execution |

Cloud Matrix 和 Containers Matrix 並不互斥。同一個攻擊鏈可能同時涉及兩個 Matrix 的 Technique。例如 Ch 28 的「從 Pod 打到 EKS → 換到 AWS IAM」這條鏈，T1611 在 Containers Matrix，T1078.004 在 Cloud Matrix——你的報告裡兩個都要引用。

---

## STRIDE 威脅建模：設計時就開始防

STRIDE 是 Microsoft 在 1990 年代末提出的威脅建模方法，六個字母代表六類威脅：

| 字母 | 威脅類型 | 說明 | 雲端例子 |
|-----|---------|------|---------|
| S | Spoofing（偽造）| 假冒他人身份 | 偷到 access key 假冒合法 IAM user |
| T | Tampering（竄改）| 未授權修改資料或設定 | 攻擊者修改 S3 物件 / Terraform state |
| R | Repudiation（抵賴）| 否認做過某件事 | 關掉 CloudTrail 讓攻擊無跡可查 |
| I | Information Disclosure（資訊洩漏）| 未授權存取資料 | S3 公開 bucket 外洩資料 |
| D | Denial of Service（拒絕服務）| 讓服務不可用 | 塞爆 SQS queue / Lambda timeout |
| E | Elevation of Privilege（提權）| 取得超過授權的權限 | PassRole 提權到 admin |

STRIDE 最大的價值是在**設計階段**使用，不是等系統上線之後才做。你對著一張架構圖，對每個元件和每條資料流問 STRIDE 的六個問題，強迫自己不遺漏。

### 具體範例：Internet → ALB → EC2 → RDS → S3

假設有一個典型三層架構：

```
Internet
   │
   ▼
 ┌─────┐    HTTPS :443
 │ ALB │ ──────────────────────────────────────────┐
 └─────┘                                           │
   │ HTTP :8080                                    │
   ▼                                               │
 ┌─────────────────┐   SQL :5432    ┌─────┐        │
 │  EC2 App Server │ ──────────────▶│ RDS │        │
 │  (IAM Role 附)  │                └─────┘        │
 │                 │   AWS SDK      ┌─────┐        │
 │                 │ ──────────────▶│ S3  │◀───────┘
 └─────────────────┘                └─────┘
```

對這個架構做 STRIDE 分析。我們對「箭頭」（資料流）和「框框」（元件）分別過一遍：

**箭頭 1：Internet → ALB**

| 威脅類型 | 問題 | 找到的威脅 | 對應控制 |
|---------|------|----------|---------|
| S | 攻擊者能假冒合法使用者嗎？ | JWT token 被竊，session hijacking | 短效 token + IP binding |
| T | 請求內容能被竄改嗎？ | HTTP downgrade 中間人 | 強制 HTTPS + HSTS |
| R | 有哪些請求沒有日誌？ | ALB access log 預設關閉 | 啟用 ALB access logging |
| I | 請求裡有哪些敏感資料？ | Authorization header 進 log | log 遮蔽 sensitive header |
| D | 攻擊者能讓 ALB 過載嗎？ | L7 DDoS | WAF + rate limiting |
| E | 攻擊者能繞過 ALB 嗎？ | 直連 EC2 public IP | EC2 SG 只允許 ALB SG |

**元件：EC2 App Server**

| 威脅類型 | 問題 | 找到的威脅 | 對應控制 |
|---------|------|----------|---------|
| S | EC2 的 IAM Role 能被偷嗎？ | SSRF → IMDS 偷 role credential | IMDSv2 強制 + 封鎖 SSRF |
| T | EC2 上的程式碼能被竄改嗎？ | 攻擊者上傳 webshell | 唯讀 filesystem / IDS |
| R | EC2 的操作有被記錄嗎？ | OS level 的 command 沒有 trail | CloudWatch Agent + auditd |
| I | RDS 連線字串存在哪？ | 寫死在程式碼或環境變數 | Secrets Manager + rotation |
| D | EC2 能被打掛嗎？ | OOM / disk full | Auto Scaling + alarm |
| E | EC2 的 IAM Role 有哪些過度權限？ | Role 有 s3:* → 可以讀其他 bucket | IAM Access Analyzer + least privilege |

**元件：RDS**

| 威脅類型 | 問題 | 找到的威脅 |
|---------|------|----------|
| I | RDS 資料有加密嗎？ | 未開啟 encryption at rest |
| I | RDS 有備份嗎？備份有加密嗎？ | snapshot 是 public → 外洩 |
| E | RDS user 有過度權限嗎？ | app user 有 DROP TABLE 權限 |

**元件：S3**

| 威脅類型 | 問題 | 找到的威脅 |
|---------|------|----------|
| I | bucket policy 是否允許公開讀取？ | ACL 或 bucket policy 設錯 |
| T | 物件上傳前有簽名驗證嗎？ | 攻擊者可以替換 S3 裡的靜態檔 |
| R | S3 access log 有開嗎？ | 預設關閉，外洩無紀錄 |

這個過程找到了 15+ 個具體威脅，每個都有明確的控制措施。如果只靠「直覺」，你可能只想到 3-4 個。

**STRIDE 最重要的觀念：建模的是整個系統，不只是 API 端點**。人工流程也是攻擊面——例如「IAM access key rotation 的 SOP」如果沒有人做，就算有 STRIDE 分析找到了 key rotation 需求，沒人執行也等於零。

---

## CIS Benchmark（網路安全中心 / Centre for Internet Security）

CIS Benchmark 是 CIS（網路安全中心）針對各種平台發布的安全配置基準。它的定位和 ATT&CK 完全不同：ATT&CK 問「攻擊者會怎麼打」，CIS 問「這個設定是否符合最佳實踐」。

CIS Benchmark 有 Level 1 和 Level 2：

| Level | 定位 | 說明 |
|-------|------|------|
| Level 1 | 基本衛生，普遍適用 | 幾乎沒有副作用，任何環境都應該過 |
| Level 2 | 高安全性環境 | 可能影響功能或效能，需要評估業務影響再套用 |

舉一個 CIS AWS Foundations Benchmark 3.0 的例子說明差異：

- Level 1 控制 1.4：「確保沒有 root access key 存在」— 任何環境都應該遵守，沒有爭議
- Level 2 控制 2.1.5.1：「確保 S3 bucket 的 MFA Delete 已啟用」— 啟用 MFA Delete 會讓自動化備份刪除變得複雜，需要評估

### CIS AWS Foundations Benchmark 五大 Section

**1. IAM**：root account 不使用、MFA 強制、access key rotation、強密碼政策、IAM credential report 定期審查

**2. Logging**：CloudTrail 在所有 region 啟用、log file validation 開啟、CloudTrail logs 送 S3 且有 access logging、CloudWatch Logs 整合

**3. Monitoring**：建立特定 CloudWatch Metric Filter + Alarm 組合，包括：root account 使用、未授權 API 呼叫、MFA 沒開的 console 登入、CloudTrail 配置變更、S3 bucket policy 變更、IAM policy 變更等共 14 個 alarm

**4. Networking**：VPC flow log 啟用、沒有允許 0.0.0.0/0 的 security group 開放 SSH/RDP、預設 VPC 的 Security Group 封閉

**5. Storage**：S3 Block Public Access 在帳號層級啟用、S3 encryption at rest、EBS 預設加密

### CIS Kubernetes Benchmark

分三大塊：

**Control Plane Security**：API server 匿名請求禁用（--anonymous-auth=false）、RBAC 啟用（--authorization-mode 包含 RBAC）、audit log 啟用

**Node Security**：kubelet 匿名認證關閉（--anonymous-auth=false）、只允許 HTTPS、rotate certificate 啟用

**Policy**：Pod Security Admission 啟用（privileged namespace 必須明確設定）、Network Policy 定義、Service Account token automount 預設關閉

### CIS Docker Benchmark 重要控制點

- 主機配置：docker daemon 不用 root 執行、TLS 加密 daemon socket
- Container Images：不用 root user 執行 container、不用有漏洞的 base image
- Container Runtime：不開 privileged、唯讀 filesystem（--read-only）、限制 capabilities

### 用 Prowler 掃 CIS 合規

Prowler 是 open source 的 AWS security scanner，可以直接對應到 CIS Benchmark 的控制點。

**本段未實測，為理論預期行為**

```bash
# 安裝
pip install prowler

# 掃 CIS AWS Foundations Benchmark 3.0
prowler aws --compliance cis_aws_foundations_benchmark_3_0

# 輸出到 HTML 報告
prowler aws --compliance cis_aws_foundations_benchmark_3_0 \
  --output-formats html \
  --output-directory ./prowler-reports

# 只掃 IAM section（節省時間）
prowler aws --compliance cis_aws_foundations_benchmark_3_0 \
  --service iam

# 把結果送 Security Hub（要先在 Security Hub 啟用 Prowler integration）
prowler aws --compliance cis_aws_foundations_benchmark_3_0 \
  --security-hub
```

自驗方法：在 AWS Lab 帳號執行 Prowler，確認報告列出 pass/fail 的控制點數量，以及 HTML 報告可以按 Section 過濾。

---

## 合規概觀：SOC 2、ISO 27001、PCI DSS

這三個是市場上最常見的合規框架。資安工程師不需要把它們背起來，但要知道每個的核心訴求，以及如何把你的技術發現翻譯成合規語言。

### SOC 2（System and Organization Controls 2）

由 AICPA（美國會計師協會）制定，針對 SaaS 服務商。分五個 Trust Service Criteria（信任服務標準）：

- **Security**（安全）：最核心，幾乎所有 SaaS 都要這個
- Availability（可用性）
- Processing Integrity（處理完整性）
- Confidentiality（機密性）
- Privacy（隱私）

在雲端環境，SOC 2 Type II 稽核員最在意的控制：加密 at rest + in transit、存取控制與最小權限、日誌與監控、變更管理（IaC + PR review）、incident response 流程。

把你本課學到的語言翻譯成 SOC 2：「我們有 CloudTrail + Security Hub + PagerDuty 告警鏈，所有 API 呼叫都有記錄和告警覆蓋」→ 對應 SOC 2 CC7（System Operations）。

### ISO 27001

國際標準，比 SOC 2 更廣泛（不只 SaaS，製造業、政府也用）。核心是 ISMS（Information Security Management System，資訊安全管理系統）。

最關鍵的 Annex A 控制（2022 版有 93 個控制）：A.5.7（威脅情報）、A.5.23（雲端服務的資訊安全）、A.8.7（惡意軟體防護）。

雲端對應：AWS Config Rules + Prowler 提供持續合規驗證（A.8.8）；CloudTrail 提供稽核軌跡（A.8.15）。

### PCI DSS 4.0（Payment Card Industry Data Security Standard）

針對處理信用卡的環境，12 個 Requirement。最嚴格的合規框架之一：

- Req 1：安裝並維護網路安全控制（VPC、Security Group、WAF）
- Req 7：限制對系統元件的存取（IAM 最小權限）
- Req 10：記錄並監控對系統元件的所有存取（CloudTrail + SIEM）
- Req 11：定期測試安全系統和流程（滲透測試必要）

在 K8s 環境跑信用卡系統，PCI DSS 要求把持卡人資料環境（CDE）放進獨立 namespace + NetworkPolicy 隔離，而且稽核員會要看你的 network segmentation 證明。

### 合規和 ATT&CK 的關係

```
合規（CIS/SOC2/ISO/PCI）── 底線
  ↑
  比底線更完整的視角
  ↓
ATT&CK（攻擊者視角的完整性）── 天花板方向
```

合規告訴你「最低要做什麼」，ATT&CK 告訴你「攻擊者有多少手段你沒蓋到」。通過 SOC 2 不代表你不會被打穿——PCI DSS 要求的滲透測試就是在承認這一點。

資安工程師的角色是橋接兩個語言：對紅隊說 ATT&CK Technique ID，對 CISO 和稽核員說 SOC 2 / CIS 控制點。同一個 finding，兩種描述都要會寫。

---

## 踩雷集錦

**ATT&CK for Cloud 和 ATT&CK for Enterprise 的 overlap 讓人搞混**。部分 Technique ID 在兩個 Matrix 都出現，例如 T1078.004（Valid Accounts: Cloud Accounts）在 Cloud Matrix 是 Initial Access，在 Enterprise Matrix 的 context 則可能是 Lateral Movement。報告裡要明確寫「Cloud Matrix」還是「Enterprise Matrix」，不然讀者看 Technique ID 去查，找到的 Tactic 可能跟你寫的不一樣。

**STRIDE 威脅建模常只建模 API，忘記建模人工流程**。IAM access key 的 rotation 流程本身就是攻擊面：如果 SOP 是「工程師手動申請 → 主管批准 → 貼在 Slack 傳給對方」，那 Slack 頻道裡的那條訊息就是 Information Disclosure，social engineering 也可以讓主管批准一個惡意申請（Elevation of Privilege）。把人工流程也畫在 data flow diagram 裡，才是完整的 STRIDE 分析。

**CIS Benchmark 掃出 failing control 不等於有漏洞**。Prowler 跑完可能給你 200 個 FAIL，但你要看風險 context。例如「S3 bucket 沒有 MFA Delete」在一個只存非敏感靜態網站資源的 bucket 上，實際風險接近零。把所有 FAIL 不加評估地報出去，會讓客戶的工程團隊失去信任，因為他們知道你沒有思考。每個 finding 都要評估 impact + exploitability，才能給出 risk rating。

**Prowler compliance 報告很長，單看 CSV 沒有人讀得完**。Prowler 的 output 在中大型帳號可能幾千行。正確做法是把 Prowler 結果送進 AWS Security Hub，再從 Security Hub 的 Findings 頁面做 aggregation 和過濾，或是匯出到 Splunk / OpenSearch。你不是要看每一行，而是要看「按 Control 分組後，哪些 Control 的 fail rate 最高」。

**STRIDE 容易停在威脅清單，沒有接到 control**。威脅建模報告如果只有「找到威脅 T1, T2, T3...」沒有每個威脅對應的控制措施，那它的實際價值接近零。每個威脅都要接到：（1）減輕威脅的技術控制；（2）這個控制目前有沒有；（3）沒有的話優先級是什麼。這才是能推動改善的報告。

---

## 進階延伸

**PASTA（Process for Attack Simulation and Threat Analysis，攻擊模擬與威脅分析流程）**：比 STRIDE 更重攻擊者視角的七步方法論，把業務目標、技術範圍、威脅情報、攻擊樹模型全部串在一起。STRIDE 是設計時快速過一遍，PASTA 是需要完整 risk quantification 時的選擇，常見在 fintech 和醫療行業的完整 risk assessment 流程。

**DREAD 評分**（Damage / Reproducibility / Exploitability / Affected users / Discoverability）：Microsoft 提出的威脅評分模型，每個維度 0-10，加總得分排優先級。現在業界用得沒有 CVSS 普遍，但在某些做威脅建模的舊式組織裡還是標配。了解它的作用，不用死背。

**CSA CCM（Cloud Security Alliance，雲端安全聯盟 / Cloud Controls Matrix，雲端控制矩陣）**：CSA 是專注於雲端資安的非盈利組織，CCM v4 有 197 個控制規格，按 17 個 Domain 分類（Application & Interface Security / Audit & Assurance / Governance, Risk & Compliance 等）。它比 CIS 更廣，包含了治理、供應商管理、業務連續性的面向。想做多雲合規對應（同時符合 AWS/Azure/GCP 的最佳實踐），CCM 是比 CIS AWS Benchmark 更合適的框架，因為它是 cloud-agnostic。

**OWASP Cloud-Native Application Security Top 10**：專門針對 cloud-native 環境的 Top 10 清單，與 OWASP Web Top 10 是平行的，但聚焦在 API gateway / service mesh / serverless / container 等現代 cloud-native 場景。

---

## 本章重點整理

- 框架的核心價值不是清單，而是強迫你**系統性遍歷**攻擊面，避免靠個人記憶決定覆蓋範圍
- 三種框架定位不同：STRIDE 在設計階段找威脅、ATT&CK 在評估時對應真實攻擊行為、CIS 在運維時驗證配置是否正確
- ATT&CK Technique ID 提供攻擊行為的精確座標，同一個 ID 出現在 Cloud Matrix 和 Enterprise Matrix 時，Tactic context 可能不同，報告要寫清楚
- STRIDE 必須建模人工流程，不只是技術元件；找到威脅之後必須接到控制措施，否則是半成品
- CIS Benchmark FAIL 不等於漏洞，每個 finding 都要評估 risk context；Prowler 報告要接 Security Hub 才好用
- 合規（SOC 2 / ISO 27001 / PCI DSS）是底線，ATT&CK 是攻擊視角的完整性上限；資安工程師要同時會說這兩種語言

---

## 自我檢核

1. 在 Ch 7 的 PassRole 攻擊鏈中，Initial Access 到 Privilege Escalation 分別對應哪些 Technique ID？
2. STRIDE 的 Repudiation（抵賴）在雲端環境最常見的技術控制是什麼？為什麼它跟 CloudTrail 強制開啟直接相關？
3. CIS Level 1 和 Level 2 的差異是什麼？給一個 S3 相關的例子說明。
4. 一個 Prowler 報告顯示「CloudTrail log file validation 未啟用」是 FAIL，這個 finding 的實際 risk 是什麼？攻擊者利用這個 FAIL 能做什麼？
5. SOC 2 Type II 和 SOC 2 Type I 的差異是什麼？為什麼客戶更信任 Type II？

---

## 延伸閱讀

1. **MITRE ATT&CK for Cloud — 官方 Matrix**
   `https://attack.mitre.org/matrices/enterprise/cloud/`
   最權威的來源。每個 Technique 頁面都有 Procedure Examples（真實案例）和 Mitigations。翻一遍 Credential Access tactic 的所有 sub-technique，你會找到幾個本課沒提到的雲端特有手法。

2. **CIS AWS Foundations Benchmark 3.0（官方 PDF，需免費註冊下載）**
   `https://www.cisecurity.org/benchmark/amazon_web_services`
   看完 Section 1（IAM）的所有控制的 Rationale 欄位，你會理解為什麼每個控制存在，而不只是知道「要做什麼」。

3. **Microsoft Threat Modeling Tool 與 STRIDE 官方文件**
   `https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool`
   Microsoft 本家的 STRIDE 工具鏈，包含 data flow diagram 建模和自動威脅建議。即使你主要做 AWS，用這個工具練習 STRIDE 思維是有效的。

4. **Prowler 官方文件 — Compliance 功能**
   `https://docs.prowler.com/projects/prowler-open-source/en/latest/compliance/`
   列出所有支援的 compliance framework 和每個控制的對應關係。注意 Prowler 的 CIS 覆蓋範圍（不是 100% 的 CIS 控制都可以自動化掃描，有些需要人工驗證）。

5. **CSA Cloud Controls Matrix v4**
   `https://cloudsecurityalliance.org/research/cloud-controls-matrix/`
   197 個控制、17 個 Domain、包含和 ISO 27001 / SOC 2 / PCI DSS 的 cross-reference。如果需要同時滿足多個合規框架，CCM 的對應表可以大幅節省 gap analysis 的時間。

---

這一章是全課的方法論整合。前 36 章的每一個攻擊技術，現在都有了它在知識體系中的座標——ATT&CK Technique ID 是精確的地址，STRIDE 是設計前的防禦思維，CIS 是日常運維的配置驗證。下一章把視角從 AWS 拉開到其他雲平台。

→ [Ch 38 Azure / GCP 攻擊速成：把 AWS 學的映射過去](./38-azure-gcp-attacks.md)
