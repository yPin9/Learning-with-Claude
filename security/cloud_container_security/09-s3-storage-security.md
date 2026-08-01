# Ch 9 — S3 與儲存體安全：bucket misconfig 與 presigned URL

> **目標**：理解 S3（Simple Storage Service）的存取控制四層模型，掌握常見 misconfig 的成因與攻擊面，能夠枚舉公開 bucket、分析 presigned URL 的濫用路徑，並寫出合格的 bucket policy。
> **環境**：aws-cli v2（`aws --version` 確認）、`--no-sign-request` 的命令本機可跑；需要真實帳號的段落逐一標注 **本段未實測，為理論預期行為** 並附自驗步驟。

---

## 為什麼需要

S3 bucket 的錯誤設定（bucket misconfiguration）是雲端滲透測試報告出現頻率最高的發現。原因很直白：S3 的存取控制有四個獨立的層次，每一層都有自己的開關邏輯，而 AWS 在不同年代多次改變預設值，留下了大量「建立於舊設定時代」的 bucket。

從攻擊者的角度，S3 misconfig 的吸引力在於：不需要 exploit，不需要 CVE，`aws s3 ls s3://target` 一行指令就能拿到敏感資料。bug bounty 報告裡充斥 `backup.sql`、`.env`、`credentials.json` 直接可讀的案例。某些場景下，一個公開的 bucket 比 RCE 的影響更直接——裡面的資料就是最終目標。

此外，bucket 不像 EC2 instance 有 security group，bucket 預設就面向整個網際網路。攻擊者不需要先進入 VPC，從外部就能發起存取測試。這讓 S3 成為外部偵察（external recon）的第一站。

---

## 先建直覺

把 S3 的存取控制想成一個由外而內的四層過濾器。請求從最外層往內穿，只要有一層阻擋，請求就不通；所有層都放行，才算通過。

```
請求進來
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 1: Block Public Access (BPA)  │  ← 帳號級或 bucket 級旗標
│  四個旗標，可以強制壓制下層設定        │
└──────────────────┬──────────────────┘
                   │ BPA 沒擋住
                   ▼
┌─────────────────────────────────────┐
│  Layer 2: Bucket Policy              │  ← resource-based policy JSON
│  可以拒絕、允許特定 principal/action   │
└──────────────────┬──────────────────┘
                   │ policy 沒拒絕
                   ▼
┌─────────────────────────────────────┐
│  Layer 3: ACL（存取控制清單）          │  ← 物件或 bucket 層級
│  AllUsers / AuthenticatedUsers       │
└──────────────────┬──────────────────┘
                   │ ACL 允許
                   ▼
┌─────────────────────────────────────┐
│  Layer 4: IAM Policy（呼叫者身分）     │  ← 呼叫者自己的 IAM 權限
│  無憑證呼叫不走這層                   │
└──────────────────┬──────────────────┘
                   │ 全部通過
                   ▼
             存取成功
```

BPA 是最外層且最強的，它設計的目的就是讓你不用去記各個 bucket policy 和 ACL 的細節，直接在帳號層級喊「我的 bucket 一個都不對外公開」。

---

## 底層機制

### S3 基礎速覽

Bucket 名稱是**全域唯一**的，所有 AWS 帳號共用同一個命名空間。這意味著你無法建立一個已存在的 bucket 名稱，同時也代表攻擊者可以從公開資訊（公司名、product name、domain）猜測 bucket 名稱去驗證存在性。

URL 結構有兩種：

```
# Path-style（舊，部分 region 已棄用）
https://s3.amazonaws.com/<bucket>/<key>

# Virtual-hosted-style（現行標準）
https://<bucket>.s3.<region>.amazonaws.com/<key>

# 範例
https://mycompany-backup.s3.ap-northeast-1.amazonaws.com/db/dump.sql
```

Virtual-hosted-style 中 bucket 名稱變成子域名，這讓 DNS 解析就能驗證 bucket 是否存在——有 DNS A record 代表 bucket 存在，`NXDOMAIN` 代表不存在（不需要任何 AWS 憑證）。

### Block Public Access（四個旗標詳解）

BPA（Block Public Access，公開存取封鎖）是 2018 年 AWS 推出的機制，用來從根本上阻止「我以為設定對了但其實公開」的問題。

| 旗標 | 作用 |
|------|------|
| `BlockPublicAcls` | 阻止新增公開 ACL，已存在的公開 ACL 操作被拒 |
| `IgnorePublicAcls` | 忽略現有公開 ACL（舊的 `AllUsers` ACL 失效） |
| `BlockPublicPolicy` | 阻止新增讓 bucket 公開的 policy |
| `RestrictPublicBuckets` | 即使有公開 policy，也限制只有 AWS service 和帳號內授權用戶可存取 |

BPA 可以設在**帳號層級**（套用到帳號內所有 bucket）或**個別 bucket 層級**。帳號層級的設定優先，如果帳號層級開了，個別 bucket 無法覆蓋關閉。

重要陷阱：`IgnorePublicAcls` 和 `RestrictPublicBuckets` 是「壓制執行」（suppress effect），即使舊的 ACL 還在，效果被壓制；`BlockPublicAcls` 和 `BlockPublicPolicy` 是「阻止新增」，不影響已存在的設定。四個全開才是完整保護。

### ACL：AllUsers 與 AuthenticatedUsers 的陷阱

ACL 是 S3 的舊版存取控制機制（比 bucket policy 早）。它的粒度粗糙，兩個常見的受體（grantee）很容易被誤解：

- `AllUsers`（Group URI: `http://acs.amazonaws.com/groups/global/AllUsers`）：字面意思，所有人，不需要任何 AWS 帳號。任何能上網的人都算。
- `AuthenticatedUsers`（Group URI: `http://acs.amazonaws.com/groups/global/AuthenticatedUsers`）：任何持有有效 AWS 帳號的人。AWS 帳號免費申請，這等同於「所有人」，只是多了一道形式障礙。

AWS 自己在文件裡警告：`AuthenticatedUsers` 並不等於你公司的員工或授權用戶。實務上應視為等同公開。

### Bucket Policy 的爛 Statement

Bucket policy（儲存貯體政策）是 resource-based policy，直接附加在 bucket 上。爛的 policy 長這樣：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::mycompany-data/*"
    }
  ]
}
```

`"Principal": "*"` 代表任何人，不需要憑證。這個 statement 讓整個 bucket 的所有物件可以直接被任何人用 HTTP GET 讀取。結合 `s3:ListBucket` 的公開權限，攻擊者可以先列舉再下載。

### Presigned URL 原理與攻擊面

Presigned URL（預簽名 URL）是 S3 讓你把「一次性的臨時存取權」包進 URL 的機制。使用流程：

1. 應用程式後端用自己的 AWS credentials 對某個物件 URL 做 HMAC 簽名
2. 把這個帶有簽名的 URL 傳給使用者（前端、email、API 回應）
3. 使用者帶著這個 URL 直接向 S3 發請求，S3 驗證簽名和 TTL

使用者不需要自己有 AWS credentials，但請求的有效性綁定在**生成時使用的 credentials**上。

攻擊面來自 URL 外洩和 TTL 設定：

- **Log 洩漏**：presigned URL 帶在 query string，HTTP access log、WAF log、CDN log 都會完整記錄下來。
- **Referrer 洩漏**：如果前端把 presigned URL 做成圖片 src，使用者從那頁點外部連結，Referer header 會帶出完整 URL。
- **前端原始碼**：SPA 應用有時把 presigned URL 硬編碼或在 API 回應裡直接暴露在 JavaScript bundle 裡。
- **TTL 設太長**：最長可設 7 天（604800 秒）。credentials 轉移或撤銷後，已簽名的 URL 在 TTL 內仍然有效（因為 S3 用簽名驗，不需要即時查 IAM 狀態）。

---

## 具體範例

### 範例 1：驗證 bucket 公開性（本機可跑，不需憑證）

```bash
# 不帶憑證，直接列舉 bucket 內容
# 200/200-ish：bucket 存在且公開列舉
# 403 AccessDenied：bucket 存在但不允許列舉
# 404 NoSuchBucket：bucket 不存在
aws s3 ls s3://target-bucket-name --no-sign-request

# 用 curl 測試 bucket 根路徑（virtual-hosted-style）
curl -I "https://target-bucket-name.s3.us-east-1.amazonaws.com/"

# 嘗試列舉（XML 回應）
curl "https://target-bucket-name.s3.us-east-1.amazonaws.com/?list-type=2"
```

回應判讀：
- `<ListBucketResult>` XML：bucket 公開且你有列舉權限
- `<Error><Code>AccessDenied</Code>`：bucket 存在，但不允許你這個動作
- `<Error><Code>NoSuchBucket</Code>`：bucket 不存在

### 範例 2：查詢 bucket 的存取控制設定

**本段未實測，為理論預期行為**。自驗方法：在自己的 AWS 帳號建一個 bucket，執行以下指令，對照 AWS console 的設定確認輸出正確性。

```bash
# 查帳號層級的 Block Public Access（需要 s3:GetAccountPublicAccessBlock 權限）
aws s3api get-public-access-block --account-id 123456789012

# 查單一 bucket 的 Block Public Access
aws s3api get-public-access-block --bucket your-bucket-name

# 查 bucket policy
aws s3api get-bucket-policy --bucket your-bucket-name --output text

# 查 bucket ACL（注意 Grants 欄位裡是否有 AllUsers 或 AuthenticatedUsers）
aws s3api get-bucket-acl --bucket your-bucket-name

# 查所有 bucket 列表（需要 s3:ListAllMyBuckets）
aws s3 ls
```

帳號層級 BPA 全開的正確輸出應該是：

```json
{
    "PublicAccessBlockConfiguration": {
        "BlockPublicAcls": true,
        "IgnorePublicAcls": true,
        "BlockPublicPolicy": true,
        "RestrictPublicBuckets": true
    }
}
```

### 範例 3：Presigned URL 生成與邊界情況

**本段未實測，為理論預期行為**。自驗方法：用自己帳號的 bucket 跑下列指令，拿到 URL 後用 curl 測試存取，再等 TTL 過期後再測一次。

```bash
# 生成 presigned URL，TTL 3600 秒（1 小時）
aws s3 presign s3://your-bucket/sensitive.pdf --expires-in 3600

# 輸出範例（URL 帶著簽名 query string）：
# https://your-bucket.s3.amazonaws.com/sensitive.pdf
#   ?X-Amz-Algorithm=AWS4-HMAC-SHA256
#   &X-Amz-Credential=AKIA...%2Fus-east-1%2Fs3%2Faws4_request
#   &X-Amz-Date=20260801T000000Z
#   &X-Amz-Expires=3600
#   &X-Amz-Signature=abc123...

# 邊界情況：超過最大 TTL（604800 秒 = 7 天）會報錯
aws s3 presign s3://your-bucket/file.txt --expires-in 999999
# An error occurred (InvalidParameterValue): The lifetime of the URL is too long.
```

失敗案例——`AuthenticatedUsers` 陷阱：

假設 bucket ACL 設了 `AuthenticatedUsers:READ`，BPA 關閉。你以為只有公司員工能讀，但實際上：

```bash
# 任何人申請免費 AWS 帳號拿到憑證後就能讀
aws configure  # 填入任意 AWS 帳號的 access key
aws s3 ls s3://target-bucket-name   # 成功
aws s3 cp s3://target-bucket-name/secret.txt .  # 成功
```

這個攻擊面在 2015-2017 年造成多起大型資料洩漏事件，AWS 後來在主控台加了警告，但舊 bucket 的設定可能還在。

---

## 好壞 Policy 對照

壞的 policy（避免）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::mycompany-data",
        "arn:aws:s3:::mycompany-data/*"
      ]
    }
  ]
}
```

問題：`Principal: *` 讓任何人（無憑證）執行 `s3:*`（所有 S3 動作），包含 ListBucket、GetObject、PutObject、DeleteObject。

好的 policy（最小權限）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAppRoleReadOnly",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::123456789012:role/app-production-role"
      },
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion"
      ],
      "Resource": "arn:aws:s3:::mycompany-data/app-data/*"
    }
  ]
}
```

差異：Principal 是具體的 role ARN（不是 `*`）；Action 只有讀取（不含 List、Put、Delete）；Resource 限制到特定子路徑。

---

## 對比取捨

| 機制 | 粒度 | 優點 | 缺點 |
|------|------|------|------|
| Block Public Access | 帳號/bucket | 最強的公開保護，一鍵全擋 | 只管「公開」，跨帳號存取要靠 policy |
| Bucket Policy | 請求層級 | 可以精確控制 principal、action、condition | JSON 寫錯容易成 `Principal: *` |
| ACL | 物件/bucket | 舊系統相容 | 粒度粗、難審計、`AuthenticatedUsers` 語意誤導 |
| IAM Policy | 呼叫者層級 | 集中管理身分權限 | 不管無憑證的匿名請求 |
| Presigned URL | 單一物件 + TTL | 讓無憑證用戶存取特定物件 | URL 洩漏 = 存取洩漏 |

---

## 踩雷集錦

**錯誤直覺**：BPA 全開，bucket 一定安全。  
**正確認識**：BPA 只保護「公開存取」，跨帳號存取（cross-account）不受 BPA 管。另一個 AWS 帳號被攻陷後，如果 bucket policy 允許那個帳號存取，BPA 擋不住。

**錯誤直覺**：`AuthenticatedUsers` ACL 代表「我組織內的用戶」。  
**正確認識**：`AuthenticatedUsers` 是 AWS 全球所有帳號的持有者，免費申請就能成為其中一員，實務上等同公開。

**錯誤直覺**：Presigned URL 有 TTL，TTL 過了就安全了。  
**正確認識**：TTL 內泄出去的 URL，在 TTL 到期前都能存取，且 S3 不提供「撤銷特定 presigned URL」的機制，只能撤銷生成它的 credentials（但這會讓應用程式其他功能也跟著爛）。

**錯誤直覺**：只要 bucket 名稱夠隨機就沒有枚舉問題。  
**正確認識**：S3 bucket 名稱出現在 CloudTrail log、SSL certificate transparency log（如果有用 HTTPS）、HTML 原始碼、JavaScript bundle 等地方。名稱隨機只是增加猜測難度，不是安全控制。

**錯誤直覺**：`s3:ListBucket` 拒絕了，`s3:GetObject` 也就沒有意義。  
**正確認識**：即使無法列舉，只要知道物件的 key，直接 GET 仍然可能成功。攻擊者會用常見路徑字典（`.env`、`backup.sql`、`credentials`）直接嘗試，不需要先列舉。

---

## Bucket 枚舉技術

名稱猜測是最基礎的技術。從目標公司的 domain、product name、GitHub 組織名出發，加上常見後綴：

```
target-backup
target-logs
target-data
target-prod
target-dev
target-staging
target-assets
target-uploads
target-static
```

工具鏈：

```bash
# s3scanner：批次測試 bucket 開放性
# 安裝：pip install s3scanner
s3scanner scan --bucket target-company-backup
s3scanner scan --bucket-file bucket-list.txt

# CloudFox：有帳號內憑證時枚舉所有 bucket
cloudfox aws -p pentest-profile inventory

# DNS 查詢驗證 bucket 存在性（有 DNS record 代表存在）
nslookup target-company-backup.s3.amazonaws.com
# 回傳 52.x.x.x (S3 IP)：bucket 存在
# NXDOMAIN：bucket 不存在
```

找到 bucket 後鎖定敏感路徑：

```bash
# 嘗試常見敏感路徑（不需列舉）
for key in .env credentials.json config.yml backup.sql dump.sql id_rsa; do
    aws s3 cp "s3://target-bucket/$key" . --no-sign-request 2>/dev/null \
        && echo "FOUND: $key"
done
```

---

## 防禦建議

1. **帳號層級 BPA 全開**：所有新帳號應在 Organizations SCP 層面強制要求。
2. **Bucket policy 最小權限**：Principal 永遠指定具體 ARN，不用 `*`；Action 列出最小必要集合。
3. **定期用 S3 Access Analyzer 掃描**：AWS 原生工具，偵測 public 和 cross-account bucket，整合到 Security Hub。
4. **Presigned URL 短 TTL**：業務允許下設 15 分鐘以內；需要長期存取用 IAM 授權而非 presigned URL。
5. **啟用 S3 server access logging 或 CloudTrail data events**：沒有 log 就不知道被誰撈過。

---

## Azure Blob 與 GCP Cloud Storage 對照

Azure Blob Storage 的 Container access level 對應 S3 ACL：`Private`（無公開）、`Blob`（物件可公開讀）、`Container`（可公開列舉+讀）。Shared Access Signature（SAS，共用存取簽章）對應 presigned URL，同樣有 TTL 和存取範圍控制，洩漏的攻擊面邏輯完全一樣。

GCP Cloud Storage 用 IAM 的 `allUsers` binding 控制公開存取，Signed URL（簽署網址）對應 presigned URL。GCP 沒有像 BPA 這樣的帳號層級開關，依賴 Org Policy `constraints/storage.uniformBucketLevelAccess` 強制統一存取控制。

---

## 本章重點整理

- S3 存取控制有四層：BPA > bucket policy > ACL > IAM policy，BPA 是最外層最強的保護。
- `AuthenticatedUsers` ACL 等同公開，任何 AWS 帳號都算，不是你組織的員工。
- Block Public Access 四個旗標功能不同，全開才是完整保護；帳號層級設定優先於 bucket 層級。
- Bucket policy 用 `"Principal": "*"` 是高危設定，等同對整個網際網路開放。
- Presigned URL 的攻擊面在 URL 洩漏（log、referrer、前端），和 TTL 設太長。
- Bucket 枚舉從名稱猜測開始，DNS 可以無憑證驗證 bucket 存在性。
- S3 Access Analyzer 是偵測公開 bucket 的原生工具，應整合進 CI/CD 或定期掃描。

---

## 自我檢核

- [ ] 我能說出 BPA 四個旗標各自的作用，以及「阻止新增」和「壓制執行」的差異。
- [ ] 我能解釋為什麼 `AuthenticatedUsers` ACL 在實務上等同公開存取。
- [ ] 我能用 `--no-sign-request` 驗證一個 bucket 是否允許匿名 List。
- [ ] 我能寫出一個正確的 bucket policy，指定具體 principal 和最小 action。
- [ ] 我能說出 presigned URL 的三種洩漏路徑，以及為什麼 TTL 到期前無法主動撤銷。
- [ ] 我知道 bucket 名稱猜測的基本字典策略，以及如何用 DNS 無憑證驗證存在性。

---

## 延伸閱讀

1. **AWS 官方文件 — Blocking public access to your Amazon S3 storage**（[docs.aws.amazon.com](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)）：BPA 四個旗標的完整行為定義，包含「現有設定」vs「新設定」的互動邏輯，是本章 BPA 節的一手來源。

2. **CloudGoat — s3_security_misconfiguration scenario**（Rhino Security Labs，GitHub）：可在本機用 Terraform 建起來的故意有漏洞的 AWS 環境，內含 S3 misconfig 練習場。跑一遍比讀文件印象深三倍。

3. **HackTricks — Pentesting AWS S3**（book.hacktricks.xyz）：攻擊者視角整理的 S3 攻擊清單，包含 bucket policy 分析和 misconfig 利用路徑，跟本章內容互補，用來做偵察清單的 checklist。

4. **truffleHog / gitleaks**（GitHub 工具）：掃 git 歷史和程式碼裡的 presigned URL、AWS credentials 洩漏的靜態分析工具。本章提到 presigned URL 會洩漏在前端原始碼，這兩個工具是對應的偵測方法。

5. **AWS Security Blog — Identifying and remediating publicly accessible Amazon S3 buckets using Amazon Macie**：說明 Macie（資料分類服務）如何搭配 S3 Access Analyzer 做更細粒度的敏感資料偵測，是本章防禦節的進階延伸。

---

S3 bucket 的公開性是靜態的，攻擊者打開門之後掃一次就走。但有一種憑證攻擊面是動態的——只要目標 EC2 instance 還活著，每隔一段時間就會換新一組 role 憑證放在那裡等著被拿。下一章把這條最經典的雲端 SSRF 鏈完整走一遍。

→ [Ch 10 EC2 與 metadata SSRF：偷憑證的經典鏈](./10-ec2-metadata-ssrf.md)
