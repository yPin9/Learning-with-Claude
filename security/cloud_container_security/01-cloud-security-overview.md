# Ch 1 — 雲端資安全貌：Shared Responsibility 與攻擊面重構

> **目標**：建立「雲端安全完全不是傳統 pentest 的延伸」這個核心認知——了解 Shared Responsibility Model、控制平面 vs 資料平面的差異、雲端 kill chain 的每個階段，並用 Capital One 2019 真實案例理解這套攻擊模型如何落地。

## 為什麼需要重構攻擊直覺？

你會 nmap、會寫 ROP chain、會用 Metasploit 打服務——這些技能在雲端不是沒用，而是**用不上的場景更多**。

傳統 pentest 的第一步是 port scan：找哪個 port 開著，哪個服務有已知 CVE，從那裡打進去拿 shell，再從 shell 往上提權。這套流程假設攻擊入口是網路可達的服務漏洞。

雲端不是這樣的。一個攻擊者拿到一個外洩的 AWS access key，他根本不需要 port scan——他直接呼叫 AWS API，列出 IAM roles、S3 buckets、Lambda functions，找出配置錯誤的信任關係，兩小時內把整個帳號的資料外洩，整個過程沒有碰到任何 port，沒有觸發任何 IDS 告警，因為這些 API call 在 AWS 眼中都是「合法授權的操作」。

**雲端是 identity-first，不是 network/shell-first**。戰場在 API 與 IAM，不在 TCP port。

---

## 先建直覺：誰的責任？

雲端最容易誤解的概念是：「我把東西放上雲，AWS 負責安全。」錯。

AWS 有個正式的框架叫 **Shared Responsibility Model（共同責任模型）**，把安全責任切成兩塊：

```
┌─────────────────────────────────────────────────┐
│              客戶（你）的責任                    │
│  • IAM users/roles/policies 設定                │
│  • 應用程式安全（你寫的程式碼）                  │
│  • 資料加密（你選不選、你設不設）                │
│  • 網路設定（Security Group、NACLs、VPC 配置）   │
│  • OS 與應用程式的 patch（EC2 的話）             │
│  • S3 bucket 存取控制（你開不開 public）         │
├─────────────────────────────────────────────────┤
│              AWS 的責任                          │
│  • 實體資料中心安全（門禁、保全）                │
│  • 硬體基礎設施（server、network hardware）      │
│  • Hypervisor 安全（確保 VM 間隔離）             │
│  • 核心服務可用性（S3、DynamoDB 本身不掛）       │
│  • 全球網路骨幹安全                              │
└─────────────────────────────────────────────────┘
```

用一句話記：**AWS 負責「雲的安全」（security of the cloud），你負責「雲上的安全」（security in the cloud）**。

這個模型根據服務類型有三種版本：

---

## Shared Responsibility Model：AWS vs Azure vs GCP 對照

三大雲的模型框架相同，但細節劃線位置有差異：

| 安全責任 | AWS | Azure | GCP |
|---|---|---|---|
| 實體機房 | AWS | Microsoft | Google |
| Hypervisor | AWS | Microsoft | Google |
| OS（IaaS VM） | **你** | **你** | **你** |
| OS（PaaS） | 共享 / 雲商 | 共享 / 雲商 | 共享 / 雲商 |
| 網路設定 | **你** | **你** | **你** |
| 應用程式 | **你** | **你** | **你** |
| 資料加密（靜態） | **你選** | **你選** | **你選** |
| IAM / 身分管理 | **你** | **你** | **你** |
| 合規認證（SOC 2） | AWS 認證基礎設施，你認證應用 | 同 | 同 |

**關鍵認知**：身分管理（IAM）永遠是客戶的責任。AWS IAM 本身不會壞，但你寫錯的 IAM policy 是你的問題，不是 AWS 的問題。這就是為什麼幾乎所有雲端安全事件的根因都是 IAM misconfiguration，而不是 AWS 服務本身的漏洞。

---

## 控制平面 vs 資料平面

這對概念在雲端安全裡極重要，理解它才能看清楚攻擊路徑：

**控制平面（Control Plane）**：管理資源本身的 API——建立 EC2 instance、設定 IAM role、刪除 S3 bucket。這些操作透過 AWS API（`api.ec2.amazonaws.com` 等）發出，走 HTTPS，被 CloudTrail 記錄。

**資料平面（Data Plane）**：操作資源裡的資料——上傳/下載 S3 物件、往 DynamoDB 寫一筆資料、呼叫 Lambda function 的 HTTP endpoint。這些操作走各自服務的 endpoint，部分不被 CloudTrail 記錄（S3 data plane 預設不開 logging）。

```
控制平面                        資料平面
aws ec2 describe-instances  vs  ssh ubuntu@<ec2-ip>
aws s3api create-bucket     vs  aws s3 cp file.txt s3://bucket/
aws iam create-role         vs  （IAM 沒有資料平面）
aws lambda update-function  vs  curl https://api.example.com/lambda-url
```

攻擊者的路徑通常是：**先入侵資料平面（SSRF 拿到 key）→ 轉往控制平面（IAM 提權）→ 再回到資料平面（撈 S3 資料）**。防禦者必須兩個平面的 logging 都開，且理解某些事件只在其中一個平面可見。

---

## 雲端 Kill Chain

把傳統 Lockheed Martin Kill Chain 映射到雲端環境：

```
傳統 Kill Chain              雲端對應
Reconnaissance           →   OSINT 找外洩 key、公開 S3、GitHub secrets
Weaponization            →   準備 aws-cli、Pacu、CloudFox
Delivery                 →   SSRF 打 metadata / 釣魚拿 IAM key / 公開 bucket 讀取
Exploitation             →   呼叫 AWS API（不是 exploit code，是 API call）
Installation             →   建後門 IAM user / role / Lambda backdoor
C2                       →   透過 AWS 服務（SQS、S3、SSM）做 C2
Actions on Objectives    →   外洩 S3 資料 / 用 KMS 解密 secrets / 橫向到其他帳號
```

更精細的雲端特定 kill chain（六個階段）：

**1. 初始存取（Initial Access）**

常見手法：
- 外洩的 access key（GitHub、S3 public、env var）
- SSRF 打 EC2/ECS metadata endpoint 拿 IAM role 的 temporary credential
- 釣魚拿 IAM console 登入帳密（然後繞 MFA，或目標沒開 MFA）
- 公開的 S3 bucket 有 credential 文件
- Supply chain（被污染的 Lambda layer、ECR image）

**2. 憑證存取（Credential Access）**

進到環境後找更多 key：
- EC2 Instance Metadata Service（IMDS）：`http://169.254.169.254/latest/meta-data/iam/security-credentials/`
- Secrets Manager、SSM Parameter Store 裡存的 DB 密碼
- Lambda 的 environment variable
- ECS task metadata endpoint

**3. 枚舉（Discovery）**

搞清楚這個帳號有什麼：
- 列所有 IAM user/role/policy
- 列 S3 buckets、EC2 instances、Lambda functions
- 找出每個 role 的 trust relationship
- 用 enumerate-iam 測試當前 credential 有哪些權限

**4. 提權（Privilege Escalation）**

從低權限到高權限：
- `iam:PassRole` + `ec2:RunInstances`：啟動帶高權限 role 的 EC2，再從裡面取 credential
- `iam:CreatePolicyVersion`：把現有 policy 的新版本設成 AdministratorAccess
- `iam:AttachUserPolicy`：直接把 AdministratorAccess 貼到自己身上
- AssumeRole 找有沒有設過寬的 trust policy

**5. 橫向移動（Lateral Movement）**

從一個帳號或服務打到另一個：
- AssumeRole 跨帳號
- 從 EC2 偷 Instance Profile credential 後 AssumeRole
- 從 Lambda 讀到 RDS 連線字串後直連 DB
- 透過 VPC peering 或 Transit Gateway 觸達其他 VPC 裡的服務

**6. 持久化與外洩（Persistence / Exfiltration）**

持久化：
- 建一個藏匿的 IAM user 或 role（shadow admin）
- 在 Lambda 加 backdoor layer
- 修改 CloudTrail 設定讓 log 不全（Ch 15 攻防）

外洩：
- `aws s3 sync s3://target-bucket/ ./local/`
- 透過 Lambda 把資料 POST 到外部
- 用 DNS exfiltration 繞出流量

---

## 真實案例：Capital One 2019 資料外洩

這個案例是雲端 kill chain 的教科書示範。

**背景**：2019 年 7 月，Capital One 被攻擊者竊取了約 1 億條信用卡申請資料。攻擊者是前 AWS 員工，在自己的 EC2 instance 上跑 Tor 出口後打 SSRF。

**攻擊路徑重建**：

```
Step 1: 偵察
   發現 Capital One 在 AWS 上有一個 WAF（ModSecurity on EC2）
   這個 EC2 instance 掛了一個 IAM role（過度授權）

Step 2: SSRF 觸發
   對 WAF 送出一個精心構造的請求，利用 SSRF 漏洞
   讓 WAF（EC2）去呼叫：
   http://169.254.169.254/latest/meta-data/iam/security-credentials/

Step 3: 拿到 Temporary Credential
   IMDS（Instance Metadata Service）回傳：
   {
     "Type" : "AWS-HMAC",
     "AccessKeyId" : "ASIAQNZMDIO...",     ← 臨時 key（ASIA 開頭）
     "SecretAccessKey" : "...",
     "Token" : "...",                       ← 必須帶上的 session token
     "Expiration" : "2019-07-19T..."
   }

Step 4: 枚舉權限
   用這組 temporary credential 呼叫 aws-cli
   aws sts get-caller-identity → 確認是 Capital One 的 WAF role
   aws s3 ls → 列出帳號下所有 bucket

Step 5: 外洩
   這個 WAF role 有 S3 讀取權（過度授權，設計上不需要讀 S3）
   攻擊者下載了包含信用卡申請資料的 S3 bucket
   約 1 億筆資料，外洩估計花費幾小時
```

**為什麼這麼成功？**

1. **SSRF 漏洞**：WAF 應該只做過濾，不應該能被誘導去呼叫 IMDS。
2. **IMDSv1 沒有認證**：任何在 EC2 上跑的程式都能直接呼叫 `169.254.169.254` 不需要任何 token。（IMDSv2 改善了這點，需要先 PUT 拿 token——Ch 5 深入）
3. **過度授權的 IAM role**：WAF 的 instance profile role 不應該有 S3 讀取權，但它有。
4. **沒有異常偵測**：大量 S3 GetObject call 沒有觸發告警。

**防禦啟示**：

- 把 WAF 後的 EC2 換到 IMDSv2（防 SSRF 直接打到 IMDS）
- 最小權限原則：WAF 的 role 不需要任何 S3 permission
- S3 data plane logging：開啟後能看到異常大量下載
- GuardDuty 的 IAM 異常偵測：能抓到從新 IP 用臨時憑證大量操作

**本段未實測，為理論預期行為。** 可以在自己帳號建一個過度授權的 EC2 IAM role，然後從 EC2 內部呼叫 IMDS 觀察輸出，驗證 IMDSv1 vs IMDSv2 的差異。Ch 5 和 Ch 10 會帶你完整跑這個流程。

---

## 雲端 vs 傳統 Pentest：核心差異對照

| 維度 | 傳統 Pentest | 雲端 Pentest |
|---|---|---|
| 入口 | 網路可達的 port / 服務 | 外洩的 API key / SSRF / 公開資源 |
| 首要武器 | nmap / Metasploit / exploit | aws-cli / Pacu / enumerate-iam |
| 提權路徑 | kernel exploit / SUID / sudo 誤配 | IAM policy 誤配 / PassRole / AssumeRole |
| 橫向移動 | Pass-the-hash / 網段掃描 | AssumeRole 跨帳號 / credential 共享 |
| 持久化 | crontab / SSH key / rootkit | 後門 IAM user / Lambda backdoor |
| 偵測規避 | kill log、換 shell | CloudTrail filter / 用合法 API |
| 「漏洞」定義 | CVE，程式碼 bug | IAM policy 過寬，設定錯誤 |
| 技術指紋 | 服務 banner / OS fingerprint | AWS API error message / IAM error code |
| Shell 需要嗎 | 幾乎必須 | 通常不需要——API call 就夠 |

這個表格的最後一行是最關鍵的：雲端攻擊者**不需要 shell**。他只需要一組有足夠權限的 credential，就能透過 AWS API 做到傳統攻擊者需要 root shell 才能做到的事——讀取所有資料、修改所有設定、建立後門。

---

## 踩雷集錦

**錯誤直覺：把雲端資安當作網路安全的延伸，用 nmap 掃 AWS IP 範圍**
正確認知：AWS IP 是共享的，你掃到的可能是別人的 instance。雲端攻擊入口不是 port，是 API key 和配置錯誤。Shodan 找公開 S3 bucket 比 nmap 掃更有效。

**錯誤直覺：「AWS 幫我做安全」，IAM 不用管**
正確認知：IAM misconfiguration 是 90% 以上雲端入侵的根因。AWS 提供 IAM 服務，但你寫什麼 policy 是你的責任。Shared Responsibility Model 把身分管理明確列為客戶責任。

**錯誤直覺：臨時憑證（ASIA 開頭）比長期 key 更危險，因為可以透過 SSRF 拿到**
正確認知：臨時憑證有到期時間（預設 1 小時），到期後自動失效，比長期 key 更安全。長期 key 一旦洩漏可以無限期使用直到手動 revoke。SSRF 能拿到臨時 key 是架構問題（IMDSv1），不是臨時 key 的問題。

**錯誤直覺：CloudTrail 有開就偵測到所有攻擊**
正確認知：CloudTrail 預設記錄管理事件（Control Plane），S3 資料存取（Data Plane）需要額外開 S3 data event logging，否則攻擊者下載多少筆資料你根本看不到。

**錯誤直覺：「我的 S3 bucket 有 IAM policy 保護，不會被公開」**
正確認知：S3 bucket 有兩層存取控制——bucket policy 和 ACL。兩個都要正確設定。Block Public Access 是覆蓋所有設定的保護傘，應該永遠開啟（除非你明確需要公開 bucket）。

---

## 進階延伸

**AWS Security Reference Architecture（SRA）**：AWS 官方的多帳號安全架構參考，展示怎麼用 Organizations、Control Tower、Security Hub 建企業級雲端安全基線。理解它才能看懂你打進去的組織的架構。

**MITRE ATT&CK for Cloud**：把 ATT&CK 的 TTP 分類延伸到雲端環境，定義了雲端特定的戰術（TA0001 Initial Access 在雲端的 sub-techniques 包括 Valid Accounts: Cloud Accounts、Exploit Public-Facing Application 等）。Ch 37 會深入，現在先知道有這個框架。

**GCP Privilege Escalation Research**：Google Cloud 的 IAM 提權路徑比 AWS 研究少，但同樣危險。Dylan Ayrey、Dov Rubin 的研究（2022 年「IAM Conditions bypass」）值得一讀。雲端之間的概念可以互相映射。

---

## 本章重點整理

- Shared Responsibility Model：AWS 負責「雲的安全」，你負責「雲上的安全」——IAM 永遠是你的責任。
- 控制平面（API 操作資源）vs 資料平面（操作資源裡的資料）：攻擊路徑常常兩者都走，防禦要兩者都記 log。
- 雲端是 identity-first——拿到 IAM credential 比拿到 shell 更有價值，攻擊者不需要 shell。
- 雲端 kill chain 六階段：初始存取 → 憑證存取 → 枚舉 → 提權 → 橫向移動 → 持久化/外洩。
- Capital One 2019 案例：SSRF + IMDSv1 + 過度授權 IAM role = 1 億筆資料外洩，三個防禦措施任一到位都能阻斷。

---

## 自我檢核

- [ ] 我能說出 Shared Responsibility Model 的划線原則，以及為什麼 IAM 永遠是客戶的責任
- [ ] 我能解釋控制平面和資料平面的差異，並各舉一個例子
- [ ] 我能描述雲端 kill chain 的六個階段，不看筆記
- [ ] 我能解釋 Capital One 2019 案例裡三個關鍵防禦措施如何各自阻斷攻擊
- [ ] 我能說出傳統 pentest 和雲端 pentest 最本質的差異（不需要 shell、identity-first）

---

## 延伸閱讀

1. **[AWS Shared Responsibility Model 官方頁面](https://aws.amazon.com/compliance/shared-responsibility-model/)**
   看三種服務模型（IaaS/PaaS/SaaS）下責任如何分配的官方圖表。關鍵是理解 EC2（IaaS）和 Lambda（PaaS）的責任边界有何不同。

2. **[Capital One Data Breach — 官方 CISA 分析](https://www.capitalone.com/digital/facts2019/)**
   Capital One 自己的事後公告和 Paige Thompson 的起訴書（公開資料）。和坊間的二手分析比，一手資料說的攻擊細節更精確。起訴書描述的攻擊步驟非常具體。

3. **[MITRE ATT&CK for Cloud — IaaS matrix](https://attack.mitre.org/matrices/enterprise/cloud/iaas/)**
   雲端 kill chain 每個階段的具體 TTP 分類。特別看 Credential Access（T1552 Unsecured Credentials 的雲端 sub-technique）和 Discovery（T1580 Cloud Infrastructure Discovery）。

4. **[HackTricks Cloud — AWS Pentesting 總覽](https://cloud.hacktricks.wiki/pentesting-cloud/aws-security)**
   攻擊視角的雲端 AWS 總覽，和本章的防禦角度互為補充。先看 Initial Access 一節，感受攻擊者的視角和思考路徑。

5. **[Datadog 2023 State of Cloud Security](https://www.datadoghq.com/state-of-cloud-security/)**
   用真實遙測數據說明 IAM misconfiguration 的比例、哪類服務最常出問題。特別看 long-lived access key 的比例，佐證為什麼 IAM 是最大的攻擊面。

---

工具的介紹放在 Ch 2。這裡建立的心智模型——identity-first、kill chain、控制/資料平面——是後面每一章攻防分析的框架，值得現在就記住。

→ [Ch 2 雲端攻擊工具鏈總覽：Pacu / ScoutSuite / CloudFox / Prowler](./02-attack-tooling.md)
