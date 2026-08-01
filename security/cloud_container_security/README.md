# 雲端與容器資安學習筆記：把 pwn 的攻擊直覺搬進雲端

> 給有 binary / pentest / RE 底子、但雲端與容器安全完全空白、想補齊這塊現代最大攻擊面的工程師。

你已經會打記憶體、會逆向、會找 web 洞。這門課把那套攻擊直覺搬進雲端——這裡的「漏洞」不是 stack overflow，是**一條寫錯的 IAM policy、一個沒關的 metadata endpoint、一個 privileged container、一條危險的 RBAC verb**。課程以 **AWS 為主線**（每個核心概念附 Azure / GCP 對照），紅隊視角一路打穿：IAM 提權 → 服務攻擊面 → 容器逃逸 → Kubernetes 淪陷 → 供應鏈 → 最後補回防禦與偵測（攻擊者也得知道自己會被 CloudTrail / GuardDuty / Falco 怎麼抓）。讀完能對一個雲環境做完整 kill chain、打穿 K8s cluster、並知道每一步怎麼被偵測與修補。

## 為什麼學這個？

- **雲端是現在最大的攻擊面，也是最缺人的方向**：企業全上雲，但懂雲端攻防的人遠少於懂傳統 pentest 的。這是最好變現的資安基本功。
- **攻擊模型完全不同，值得從頭建**：雲端沒有你熟悉的 shell-first kill chain。這裡是 identity-first——搞懂 IAM 提權比會 ROP 更能拿下一個雲帳號。
- **接得上你既有的攻擊技能**：SSRF（你在 owasp 學過）在雲端會變成偷 credential 的鑰匙；容器逃逸吃你 kernel 的底子；K8s RBAC 提權是另一種 privesc graph。技能是接續的，不是重來。
- **補上你最缺的防禦側基本功**：Part 7 教 CSPM、Pod Security、Falco、雲端 IR——你全是攻擊視角，這是你版圖裡最大的洞。

## 先修知識

- **Linux 與容器基礎**（程度：能操作，懂 namespace/cgroup 更好）：本課容器部分**只打安全**，不重教 Docker。沒讀過 [dev_tools/docker](../../dev_tools/docker/README.md) 也行，Ch 16 會從安全視角快速複習隔離模型。
- **HTTP 與 web 攻擊基礎**（程度：懂 SSRF/token）：Ch 10 的 metadata SSRF 鏈假設你知道 SSRF 是什麼。沒把握先看 [security/owasp](../owasp/README.md)。
- **基本網路概念**（程度：懂 TCP/port/DNS）。
- 沒有也沒關係的：**Kubernetes**——Part 4 從零教；**雲端經驗**——Ch 0 帶你開隔離帳號。

## 課程地圖

### Part 0 — 地基與心法（Ch 0–2）
- [Ch 0 Lab 環境：隔離帳號、成本煞車、合法邊界、工具鏈](./00-lab-environment.md)
- [Ch 1 雲端資安全貌：Shared Responsibility 與攻擊面重構](./01-cloud-security-overview.md)
- [Ch 2 雲端攻擊工具鏈總覽：Pacu / ScoutSuite / CloudFox / Prowler](./02-attack-tooling.md)

### Part 1 — IAM：雲端真正的戰場（Ch 3–8）
- [Ch 3 IAM 心智模型：principal / policy / role / trust（AWS↔Azure↔GCP）](./03-iam-mental-model.md)
- [Ch 4 AWS policy evaluation 深入：Deny 優先與 condition](./04-aws-policy-evaluation.md)
- [Ch 5 認證與臨時憑證：access key / STS / IMDSv1 vs v2](./05-credentials-and-metadata.md)
- [Ch 6 IAM 枚舉與偵察：enumerate-iam 與權限測繪](./06-iam-enumeration.md)
- [Ch 7 IAM 提權技術：PassRole 等經典 privesc 路徑](./07-iam-privilege-escalation.md)
- [Ch 8 跨帳號與信任攻擊：AssumeRole 與 confused deputy](./08-cross-account-trust.md)
- [練習 A：低權限 credential → 枚舉 → 提權到 admin](./practice-a-iam-privesc.md)

### Part 2 — 雲端服務攻擊面（Ch 9–15）
- [Ch 9 S3 與儲存體安全：bucket misconfig 與 presigned URL](./09-s3-storage-security.md)
- [Ch 10 EC2 與 metadata SSRF：偷憑證的經典鏈](./10-ec2-metadata-ssrf.md)
- [Ch 11 Serverless（Lambda）：事件注入與過度權限](./11-serverless-lambda.md)
- [Ch 12 密鑰與 secrets：KMS / Secrets Manager / 洩漏面](./12-secrets-kms.md)
- [Ch 13 雲端網路：VPC / security group / 橫向移動](./13-cloud-networking.md)
- [Ch 14 雲端持久化與後門：shadow IAM 與隱蔽技巧](./14-cloud-persistence.md)
- [Ch 15 日誌與偵測規避（紅隊 OPSEC）：CloudTrail 怎麼抓你](./15-logging-evasion.md)
- [練習 B：完整 cloud kill chain（SSRF→metadata→IAM→橫move→外洩）](./practice-b-cloud-killchain.md)

### Part 3 — 容器安全：只打安全（Ch 16–20）
- [Ch 16 容器隔離的安全模型：從攻擊視角看 namespace/cap](./16-container-isolation-model.md)
- [Ch 17 容器逃逸（一）：privileged / 掛載 / capabilities / device](./17-container-escape-1.md)
- [Ch 18 容器逃逸（二）：runc/containerd CVE 與 kernel 逃逸](./18-container-escape-2.md)
- [Ch 19 映像與供應鏈：image 掃描與 layer 裡的 secrets](./19-image-supply-chain.md)
- [Ch 20 Runtime 防護：seccomp / AppArmor / SELinux 怎麼被繞](./20-runtime-protection.md)
- [練習 C：從容器內逃逸到 host](./practice-c-container-escape.md)

### Part 4 — Kubernetes 基礎：從零建心智模型（Ch 21–24）
- [Ch 21 K8s 架構：control plane / node / etcd / API server](./21-k8s-architecture.md)
- [Ch 22 核心物件：Pod / Deployment / Service / Namespace](./22-k8s-core-objects.md)
- [Ch 23 RBAC 與認證：ServiceAccount / token / Role](./23-k8s-rbac-auth.md)
- [Ch 24 網路與機密：CNI / NetworkPolicy / Secret / ConfigMap](./24-k8s-networking-secrets.md)

### Part 5 — Kubernetes 攻擊（Ch 25–30）
- [Ch 25 K8s 偵察與暴露面：anonymous API / kubelet / etcd](./25-k8s-recon.md)
- [Ch 26 RBAC 提權：危險 verb 與 token 竊取](./26-k8s-rbac-privesc.md)
- [Ch 27 Pod 逃逸到節點：hostPath / hostPID / privileged](./27-pod-escape-to-node.md)
- [Ch 28 節點 → cluster-admin：與 cloud IAM 交會（IRSA）](./28-node-to-cluster-admin.md)
- [Ch 29 K8s 持久化：shadow admin 與惡意 admission webhook](./29-k8s-persistence.md)
- [Ch 30 託管 K8s 差異：EKS / AKS / GKE 各自的坑](./30-managed-k8s.md)
- [練習 D：從受害 Pod 打到 cluster-admin + 打穿 cloud account](./practice-d-k8s-takeover.md)

### Part 6 — 供應鏈與 CI/CD（Ch 31–33）
- [Ch 31 CI/CD 攻擊面：pipeline poisoning 與 OIDC 信任濫用](./31-cicd-attacks.md)
- [Ch 32 供應鏈防護：SBOM / cosign / SLSA / admission 驗簽](./32-supply-chain-defense.md)
- [Ch 33 IaC 安全：Terraform/CloudFormation misconfig 與掃描](./33-iac-security.md)

### Part 7 — 防禦、偵測、合規：補回基本功缺口（Ch 34–38）
- [Ch 34 雲端防禦基本功：least privilege / SCP / CSPM](./34-cloud-defense-basics.md)
- [Ch 35 K8s hardening：Pod Security / OPA-Kyverno / Falco](./35-k8s-hardening.md)
- [Ch 36 雲端偵測工程：CloudTrail→SIEM / GuardDuty / 雲端 IR](./36-cloud-detection.md)
- [Ch 37 威脅建模與框架：MITRE ATT&CK for Cloud / CIS Benchmark](./37-threat-modeling-frameworks.md)
- [Ch 38 Azure / GCP 攻擊速成：把 AWS 學的映射過去](./38-azure-gcp-attacks.md)
- [Final Project：自架 vulnerable 雲+K8s lab，完整紅隊 engagement + 報告](./final-project-red-team-engagement.md)

## 學習方式建議

1. **每章開一個隔離帳號動手**：雲端資安只讀不練等於沒學。Ch 0 教你怎麼開一個花不到錢、炸不到別人的 lab。
2. **在你自己的帳號故意設錯**：把 bucket 開公開、給 role 過度權限，再用工具掃出來——你會永遠記得那個 finding 長怎樣。
3. **攻防對照著讀**：每學一個攻擊，翻到 Part 7 對應的防禦章看它怎麼被擋。攻擊者不懂防禦是瞎子。
4. **合法邊界是紀律不是建議**：這門課所有技術都是雙面刃。只在你**自己擁有或明確授權**的環境練。未授權攻擊他人雲端資源在多數國家是刑事犯罪，Ch 0 會把這條線劃清楚。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[HackTricks Cloud](https://cloud.hacktricks.wiki/)**
  - 雲端攻擊技術最完整的公開整理；本課多個攻擊章的技術細節都可在此對照更多變體
- **[AWS IAM 官方文件 — Policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)**
  - IAM 行為的最終仲裁；Ch 4 的判斷規則就是根據這頁

### 推薦論文 / 白皮書

- **[NCC Group — Cloud Container Attack Tool 與相關研究](https://www.nccgroup.com/us/research-blog/)**
  - 容器逃逸與雲端攻擊的一手研究；Ch 17–18 的多個 CVE 分析源頭
- **[Datadog — State of Cloud Security](https://www.datadoghq.com/state-of-cloud-security/)**
  - 用真實遙測數據說明雲端 misconfig 的實際分布；佐證本課為什麼把 IAM 排在最前

### 推薦部落格 / 文章

- **[Rhino Security Labs — AWS privesc 系列](https://rhinosecuritylabs.com/aws/aws-privilege-escalation-methods-mitigation/)**
  - Pacu 作者團隊；Ch 7 的 21 種 IAM 提權路徑源自這篇經典
- **[Aqua Security — Cloud Native Security 部落格](https://www.aquasec.com/blog/)**
  - 容器與 K8s 攻防一手情報；Part 3 與 Part 5 多處引用

### 讀完本課之後

- **[PortSwigger / PentesterLab 之外的雲端靶場：flaws.cloud、CloudGoat、IAM Vulnerable、kube-hunter demo](https://cloudgoat.readthedocs.io/)**（自架 CloudGoat 打完整場景，是 Final Project 之後最好的續戰）
- **CKS（Certified Kubernetes Security Specialist）與 AWS Security Specialty 考綱**（本課涵蓋兩者大半內容，想拿證可對照補齊）
