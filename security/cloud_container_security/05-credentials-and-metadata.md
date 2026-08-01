# Ch 5 — 認證與臨時憑證：access key / STS / IMDSv1 vs v2

> **目標**：搞清楚 AWS 憑證的兩大類型（長期 vs 臨時）、它們從哪來、怎麼驗、怎麼洩，並把整門課最關鍵的攻擊地基——Instance Metadata Service——鋪清楚。
>
> **環境**：AWS CLI v2（示範以 `aws` 指令為主）；示範主機為一台 EC2 instance（Amazon Linux 2023）。凡需真實帳號才能觀察的段落會明確標註。

前兩章講 policy 怎麼寫、怎麼評估。但 policy 綁在身分上，身分透過**憑證**證明「我是誰」。這一章拆開憑證本身——它長什麼樣、AWS CLI 從哪裡撈它、怎麼流出去被偷。最重要的是後半段的 **Instance Metadata Service（IMDS）**：這是 Ch 10 metadata SSRF 攻擊鏈的正中心，理解它 v1/v2 的差異，你才看得懂那條經典的「SSRF → 偷 EC2 role 憑證」是怎麼成立、又怎麼被擋的。

## 先建直覺：憑證是「通行證」，有兩種發法

把憑證想成進雲端 API 大門的通行證：

```
   長期憑證 (access key)                臨時憑證 (STS)
   ┌───────────────────┐              ┌────────────────────────┐
   │  AKIA...          │              │  ASIA...               │
   │  + secret         │              │  + secret              │
   │                   │              │  + session token  ◀── 多這個！
   │  發了就一直有效    │              │  幾分鐘~幾小時就過期    │
   │  像「員工正職證」   │              │  像「訪客臨時證」        │
   │  掉了＝長期災難     │              │  掉了＝短期麻煩          │
   └───────────────────┘              └────────────────────────┘
        屬於 IAM user                     assume role / 服務身分 換來的
```

兩把鑰匙的差別，用開頭就能認：

- **長期憑證**：`access key id`（`AKIA` 開頭）+ `secret access key`。屬於某個 IAM user，你不主動轉它就永遠有效。**兩個欄位**，沒有 session token。
- **臨時憑證**：`access key id`（`ASIA` 開頭）+ `secret access key` + **`session token`**。由 STS（Security Token Service）發放，有明確過期時間。**三個欄位**，多一個 session token。

一個攻防直覺先種下：**看到 credential 先看開頭前四碼**。`AKIA` 是長期 key（偷到長期有效，但下面會說也有代價）；`ASIA` 是臨時憑證（會過期，得趁鮮用，但常伴隨更高權限的 role）。這個前綴判斷你會用一輩子。

> 補充：`ASIA` 之外還有 `AKIA` 以外的少數前綴（如某些服務內部用的），但攻防場上最常見的就是這兩個。`ABIA`/`ACCA` 等罕見，先記住 `AKIA`＝長期、`ASIA`＝臨時。

## 憑證從哪裡來：五種來源

AWS CLI/SDK 呼叫 API 前，得先找到憑證。它按固定順序找（credential provider chain），攻擊者則反過來——這五個地方都是翻找憑證的目標：

### 1. `aws configure`（寫進設定檔）

最直觀。跑 `aws configure` 會把 key 寫進 `~/.aws/credentials`：

```ini
[default]
aws_access_key_id = AKIAIOSFODNN7EXAMPLE
aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

> 上面是 AWS 官方文件用的示範假 key（`AKIAIOSFODNN7EXAMPLE`），不是真憑證。攻擊者拿到一台機器第一件事之一，就是翻 `~/.aws/credentials` 和 `~/.aws/config`。

### 2. 環境變數

CLI 也認這幾個環境變數，優先序高於設定檔：

```bash
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...        # 臨時憑證才有這個
```

CI/CD、容器、Lambda 常把憑證塞進環境變數——所以 `env` / `/proc/<pid>/environ` 是攻擊者必翻的地方（Ch 11 Lambda、Ch 14 持久化都會回來）。

### 3. `~/.aws/credentials` 檔（同上，設定檔持久化）

`aws configure` 寫的就是這裡，但也可能是人手動貼的、或別的工具寫的。多個 `[profile]` 段落各存一組——翻到一台跳板機時，這裡可能藏著通往其他帳號的 key。

### 4. instance profile（EC2 自動拿 role 憑證）

這是雲端獨有、也最重要的一種。你**不需要**在 EC2 上放任何 key。做法是：建一個 IAM role → 包成 **instance profile** → 掛到 EC2 instance。之後 instance 上的程式呼叫 AWS API 時，SDK 自動去 **metadata service**（下一節主角）撈這個 role 的**臨時憑證**。

好處：EC2 上沒有長期 key 可偷。壞處（對防守）/ 好處（對攻擊）：**任何能在這台 EC2 上發出 HTTP 請求到 metadata endpoint 的東西，都能拿到這組憑證**——包括一個 SSRF 漏洞。這就是 Ch 10 的核心。

### 5. AssumeRole（主動換臨時憑證）

一個身分呼叫 `sts:AssumeRole`，指定要承接的 role ARN，STS 回一組該 role 的臨時憑證（`ASIA` + session token）。這是 Ch 3 講的「穿上權限衣服」的實際 API。提權、跨帳號、role chaining 全靠它。

```bash
aws sts assume-role \
  --role-arn arn:aws:iam::111122223333:role/deploy \
  --role-session-name pentest
# 回傳 JSON 裡有 AccessKeyId(ASIA...)/SecretAccessKey/SessionToken/Expiration
```

## Instance Metadata Service：重點鋪陳

這是本章的重頭戲，也是 Ch 10 的地基。**IMDS 是每台 EC2 instance 內部都能存取的一個特殊 HTTP 端點**，位址固定是 **`169.254.169.254`**（一個 link-local 位址，只在 instance 內部可達，不對外）。instance 上的程式打這個位址，能拿到自己的中繼資料——instance id、region、**以及最要命的：掛在身上的 role 的臨時憑證**。

```
   ┌──────────── EC2 instance 內部 ────────────┐
   │                                            │
   │   你的程式 / SDK / (或一個 SSRF 漏洞)       │
   │        │                                   │
   │        │  HTTP GET 169.254.169.254/...     │
   │        ▼                                   │
   │   ┌─────────────────────────────┐          │
   │   │  Instance Metadata Service  │          │
   │   │  169.254.169.254 (link-local)│         │
   │   │   → instance-id             │          │
   │   │   → region                  │          │
   │   │   → iam/security-credentials│ ◀── 憑證!│
   │   └─────────────────────────────┘          │
   └────────────────────────────────────────────┘
```

### metadata endpoint 路徑

拿 EC2 role 憑證的關鍵路徑，記死：

```
http://169.254.169.254/latest/meta-data/iam/security-credentials/
```

打這個路徑會先回傳掛在 instance 上的 **role 名稱**（例如 `deploy-role`）。再把名稱接上去：

```
http://169.254.169.254/latest/meta-data/iam/security-credentials/deploy-role
```

這一個會回傳一坨 JSON，裡面就是那個 role 的臨時憑證：

```json
{
  "Code": "Success",
  "Type": "AWS-HMAC",
  "AccessKeyId": "ASIAEXAMPLE1234567890",
  "SecretAccessKey": "wJalrXUtnFEMI/EXAMPLEKEY",
  "Token": "IQoJb3JpZ2luX2VjEXAMPLE...(很長)...",
  "Expiration": "2026-08-01T18:00:00Z"
}
```

> 上面是**示範用假憑證**（`ASIA` 前綴 + 假 base64 token），不是真的。拿到這坨 JSON，攻擊者把 `AccessKeyId`/`SecretAccessKey`/`Token` 塞進自己的環境變數，就完全變成那個 role。**這正是 metadata SSRF 攻擊的終點**：找到一個能讓伺服器代你發請求的漏洞（SSRF），讓它去打 `169.254.169.254/latest/meta-data/iam/security-credentials/`，把憑證吐回來給你。

### IMDSv1 vs IMDSv2：這是整章的攻防分水嶺

同一個 endpoint，兩種存取協定，安全性天差地別：

**IMDSv1（舊，可被 SSRF 打）**：一個單純的 GET 就給你資料。

```bash
# IMDSv1：直接 GET，沒有任何門檻
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/deploy-role
```

問題：**只要能發出一個 GET 到這個位址就能拿憑證**。一個 SSRF 漏洞（`?url=http://169.254.169.254/...`）就滿足這條件——伺服器代你 GET，憑證回來。這是雲端史上最經典的一類事故（Capital One 2019 就是這條鏈）。

**IMDSv2（新，token-based）**：改成兩步，先 PUT 拿 token，再 GET 帶 token。

```bash
# 第一步：PUT 拿一個 session token（注意是 PUT，不是 GET）
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

# 第二步：GET 時帶上 token
curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/deploy-role
```

為什麼這樣就擋掉多數 SSRF？三個設計互相配合：

1. **要求 `PUT` 方法**：多數 SSRF 漏洞只能讓伺服器發 `GET`（例如「把這個 URL 的圖片抓回來」）。IMDSv2 拿 token 那步必須是 `PUT`，普通 SSRF 發不出來。
2. **要求自訂 header**：拿 token 要帶 `X-aws-ec2-metadata-token-ttl-seconds`，用 token 要帶 `X-aws-ec2-metadata-token`。多數 SSRF 無法讓伺服器加自訂 header。
3. **hop limit（TTL）**：token 回應的 IP TTL 預設是 **1**。這代表回應**只能在本機處理，過一個路由跳點就死**。所以就算攻擊者想透過一個會轉發的 SSRF（例如打到某個 proxy 容器）拿 token，封包過一跳就被丟棄。hop limit 可調（`--http-put-response-hop-limit`），容器場景常需調成 2——調太大反而削弱防護，這是設定陷阱。

**分水嶺結論**：IMDSv1 對 SSRF 幾乎不設防；IMDSv2 用「PUT + 自訂 header + hop limit」三道門把絕大多數 SSRF 擋在外面。所以 Ch 10 那條攻擊鏈，成不成立高度取決於目標 EC2 是不是還開著 IMDSv1（或設成 v1/v2 皆可的 `optional` 模式）。現代 hardening 的標準動作就是強制 IMDSv2（`--http-tokens required`）。

> **本段大部分為 IMDS 協定行為的理論描述**：curl 指令與回應格式來自 AWS 官方文件與公開研究，未在真實付費 instance 上逐條實跑。要自己驗證：開一台 t3.micro（免費額度內）掛一個測試 role，SSH 進去分別用 v1/v2 指令打 metadata，觀察 v1 直接回、v2 沒 token 時回 `401`。再把 instance 設成 `--http-tokens required`，確認 v1 的 GET 從此被拒。

## 憑證有效性驗證：`aws sts get-caller-identity`

拿到一組來路不明的憑證（偷的、翻到的、SSRF 撈的），第一件事是確認它**還有效、是誰**。這支 API 幾乎不需要任何權限就能呼叫，最適合當「探路」：

```bash
aws sts get-caller-identity
```

輸出格式：

```json
{
    "UserId": "AIDAEXAMPLE1234567890",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/deploy-bot"
}
```

> `Account` 這裡用**假的** `123456789012`（AWS 文件慣用的範例帳號號碼）。三個欄位的意義：
> - **`UserId`**：這個 principal 的唯一 ID。`AIDA` 開頭是 IAM user，`AROA` 開頭是 role。
> - **`Account`**：憑證所屬的 12 位數 AWS 帳號號碼——立刻知道你落在哪個帳號。
> - **`Arn`**：最有用的一欄，直接告訴你「你現在是誰」。上面是 `user/deploy-bot`，若是臨時憑證會顯示 `assumed-role/role-name/session-name`。

為什麼這支特別重要？它**不觸發任何資源存取告警**（不碰 S3、不碰 EC2），CloudTrail 裡是一筆很不起眼的 `GetCallerIdentity`，是攻擊者確認立足點最安靜的第一步。防守方反過來——大量 `GetCallerIdentity` 或非預期身分呼叫它，可能就是有人在探路。

## 憑證洩漏面：憑證都從哪流出去

前面講怎麼用憑證，這裡講它們怎麼「不小心」流到攻擊者手上——這是憑證攻防最現實的一面：

- **git**：把 `~/.aws/credentials`、`.env`、含 hardcoded key 的原始碼 commit 上去，尤其 push 到公開 GitHub。GitHub 的 secret scanning 會抓、攻擊者的爬蟲也會抓。`AKIA` 開頭的字串在公開 repo 裡等於裸奔。（Ch 19 image 供應鏈、Ch 31 CI/CD 會深入。）
- **S3**：把備份、log、設定檔（含憑證）丟進一個設成公開的 bucket。（Ch 9 主題。）
- **log**：應用把憑證寫進 debug log、把整個 request（含 `Authorization` header 或環境變數）記下來。log 又常被集中收集、權限鬆散。
- **環境變數**：容器/CI 把 key 塞進 env，任何能讀 `/proc/<pid>/environ`、能下 `env`、或觸發把 env 印出來的錯誤頁的人都能撈。
- **metadata service（本章主角）**：透過 SSRF 從 IMDS 撈 EC2 role 憑證——這是「洩漏」裡最技術性的一種，也是 Ch 10 整章。

一個殘酷事實：雲端被打穿，最常見的起點不是精巧的 exploit，是**一組洩漏的憑證**。所以枚舉（Ch 6）之前，先學會在拿到的環境裡把上述每個角落翻一遍。

## 邊界案例：憑證明明在，CLI 卻用錯組

一個很常見、也很能考驗你對 provider chain 理解的情境。假設一台跳板機上同時有：環境變數裡設了一組低權限 `ASIA` 臨時憑證，而 `~/.aws/credentials` 的 `[default]` 段裡放了一組高權限 `AKIA` 長期 key。你跑 `aws s3 ls`，結果權限不足。你以為 default profile 那組高權限 key 生效了——**其實沒有**。

原因：**環境變數的優先序高於設定檔**。CLI 依 provider chain 由上而下找，先撞到環境變數那組低權限臨時憑證就用它，根本沒讀到 `[default]`。這解釋了無數「我明明設了 key 為什麼權限不對」的困惑。攻防上的兩個用法：

- **除錯/確認身分**：拿到憑證後永遠先 `aws sts get-caller-identity`，用回傳的 `Arn` 確認「CLI 現在實際用的是哪個身分」，而不是假設你設的那組生效了。
- **攻擊**：如果你能寫入目標的環境變數（例如注入到一個服務的啟動環境），你可以用一組你控制的憑證**覆蓋**它原本要用的憑證，劫持它後續所有 AWS 呼叫——因為環境變數排在鏈的前面。這是 Ch 14 持久化的一種手法雛形。

要指定用哪個 profile，用 `--profile` 或 `AWS_PROFILE`：

```bash
# 明確指定 profile，繞過環境變數混淆
aws --profile admin sts get-caller-identity
```

## 對比取捨表：長期 vs 臨時憑證

| 面向 | 長期憑證（AKIA） | 臨時憑證（ASIA） |
|---|---|---|
| 欄位 | key id + secret（2 個） | key id + secret + **session token**（3 個） |
| 壽命 | 不轉就永遠有效 | 幾分鐘~12 小時 |
| 綁定 | IAM user | assume role / instance profile / 服務 |
| 偷到的價值 | 長期有效，但有 MFA/SCP 限制時可能受阻（Ch 4 題二） | 會過期要趁鮮，但常伴隨高權限 role、且可能已帶 MFA context |
| 常見來源 | `~/.aws/credentials`、hardcode、CI secret | metadata service、AssumeRole、Lambda 執行環境 |
| 偵測 | key 一直用，行為異常較好抓 | 臨時、像正常服務行為，較隱蔽 |
| 防守優先動作 | 減少長期 key、強制輪替、用 IAM Identity Center 取代 | 縮短 TTL、綁 session policy、強制 IMDSv2 |

## 踩雷集錦

- **錯誤直覺：「憑證有 access key 和 secret 兩欄就對了。」** → 正確認識：臨時憑證**必須**帶第三欄 `session token`，少了它 API 會回 `InvalidClientTokenId` 之類的錯。從 metadata / AssumeRole 撈到憑證，記得三欄一起帶（環境變數 `AWS_SESSION_TOKEN` 別漏）。
- **錯誤直覺：「目標開了 IMDSv2，SSRF 就完全打不到憑證了。」** → 正確認識：IMDSv2 擋掉**多數**只能發 GET 的 SSRF，但不是萬能。若 SSRF 能發 PUT、能帶自訂 header（例如某些 full-request-control 的 SSRF），或 hop limit 被調高、或有能跑任意程式的 RCE，v2 一樣被繞。v2 是提高門檻，不是免疫。
- **錯誤直覺：「EC2 沒放任何 access key，就沒有憑證可偷。」** → 正確認識：只要掛了 instance profile，metadata service 隨時能吐出 role 的臨時憑證。「機器上沒有 key 檔」不等於「拿不到憑證」——IMDS 就是那把隨身鑰匙。
- **錯誤直覺：「`get-caller-identity` 要有權限才能跑，跑不動代表憑證沒用。」** → 正確認識：它幾乎不需要任何 IAM 權限，任何有效憑證都能呼叫。跑不動通常代表憑證**本身無效或過期**（尤其臨時憑證），而不是權限不足。這正是它適合當有效性探針的原因。
- **錯誤直覺：「`AKIA`／`ASIA` 只是隨機字串，前綴沒意義。」** → 正確認識：前綴是 AWS 定義的**類型標記**。`AKIA`＝長期 user key，`ASIA`＝STS 臨時憑證，`AROA`＝role 的 unique id，`AIDA`＝user 的 unique id。看前綴就能快速分類手上撈到的字串是什麼、該怎麼用。

## 進階延伸

- **credential provider chain 的完整順序**：SDK 找憑證有明確優先序（環境變數 → 設定檔 → 容器 credential endpoint → instance profile …）。理解順序能解釋「為什麼我設了 profile 卻用到別組 key」，也讓攻擊者知道覆蓋哪個來源能劫持憑證（Ch 14 持久化會用）。
- **ECS/EKS 的 credential endpoint**：容器裡不是打 `169.254.169.254`，而是打 `169.254.170.2` 或環境變數 `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` 指的路徑。容器場景翻憑證要找這個，別只盯著 IMDS。
- **IMDSv2 hop limit 與容器的張力**：容器多一層網路，預設 hop limit 1 會讓容器內拿不到憑證，於是常被調成 2——但這也讓「從容器 SSRF 打宿主 IMDS」重新可能。這是 Ch 27 pod 逃逸與 metadata 的交會點。
- **STS regional endpoint 與 session token 大小**：臨時憑證的 session token 很長（上 KB），塞 header 或 URL 時可能撞長度限制；且 STS 有 global/regional endpoint 之分，被封某個 region 時可換 endpoint。實戰細節，撞到才會有感。

## 延伸閱讀

- **[AWS — Retrieve instance metadata (IMDS) / IMDSv2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html)**：IMDS 的官方權威，含 v2 的 PUT/token/hop-limit 完整規格。**讀哪裡**：一定看 `iam/security-credentials/` 路徑和 IMDSv2 的 token 流程那兩節，把本章的 curl 指令跟官方對照。**關聯**：Ch 10 metadata SSRF 的 spec 來源。
- **[AWS — Temporary security credentials in IAM (STS)](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp.html)**：STS 臨時憑證與 AssumeRole 官方說明。**讀哪裡**：AssumeRole 回傳結構、憑證壽命上限、`ASIA` 憑證的組成。**關聯**：接 Ch 3 role、Ch 7 提權、Ch 8 跨帳號的 API 基礎。
- **[HackTricks Cloud — AWS metadata SSRF / 憑證竊取](https://cloud.hacktricks.wiki/en/pentesting-cloud/aws-security/aws-services/aws-ec2-ebs-elb-ssm-vpc-and-vpn-enum.html)**：攻擊者怎麼從 metadata 撈憑證、繞 IMDSv2 的各種 payload。**讀哪裡**：找 IMDSv2 bypass 與 SSRF-to-credentials 的段落。**關聯**：Ch 10 攻擊鏈的實戰對照百科。
- **[Capital One 2019 事故技術覆盤（多篇公開分析）](https://www.nccgroup.com/us/research-blog/)**：搜尋 SSRF / IMDS / Capital One。**讀哪裡**：SSRF → IMDSv1 → EC2 role 憑證 → S3 外洩這條完整鏈的還原。**關聯**：本章「為什麼 IMDSv1 危險」的真實案例，Ch 10 會完整重建這條鏈。
- **[AWS — sts get-caller-identity CLI 參考](https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html)**：驗證憑證身分的指令參考。**讀哪裡**：輸出欄位 `UserId`/`Account`/`Arn` 的定義。**關聯**：Ch 6 枚舉一律從這支開始，確認立足點。

---

## 本章重點整理

- 憑證分兩類，看前綴就能認：**長期憑證（`AKIA`，2 欄）** vs **臨時憑證（`ASIA`，3 欄含 session token）**。臨時憑證會過期但常伴隨高權限 role。
- 憑證五大來源：`aws configure`/設定檔、環境變數、`~/.aws/credentials`、**instance profile（EC2 自動從 metadata 拿）**、AssumeRole。攻擊者反過來逐一翻找。
- **IMDS（`169.254.169.254`）是本課關鍵地基**：`/latest/meta-data/iam/security-credentials/<role>` 吐出 EC2 role 的臨時憑證。**IMDSv1** 一個 GET 就給（SSRF 可打）；**IMDSv2** 靠 PUT + 自訂 header + hop limit 三道門擋掉多數 SSRF。
- `aws sts get-caller-identity` 是最安靜的憑證有效性/身分探針，幾乎不需權限，回傳 `UserId`/`Account`/`Arn`。
- 雲端被打穿最常見的起點是**洩漏的憑證**（git / S3 / log / 環境變數 / metadata），不是精巧 exploit。

## 自我檢核

- [ ] 我能只看 credential 前綴分辨長期 vs 臨時憑證，並說出臨時憑證多的那一欄是什麼
- [ ] 我能列出至少四種憑證來源，並解釋 instance profile 為何讓「機器上沒有 key」也能拿到憑證
- [ ] 我能寫出從 IMDS 撈 EC2 role 憑證的路徑，並說明 IMDSv1 為何一個 GET 就被 SSRF 打穿
- [ ] 我能講清楚 IMDSv2 的三道門（PUT / 自訂 header / hop limit）各擋掉哪種 SSRF，以及它為何不是萬能
- [ ] 我能用 `aws sts get-caller-identity` 的三個輸出欄位判斷「我現在是誰、在哪個帳號」，並說明它為何安靜
- [ ] 我能列出至少四個憑證洩漏面，並知道拿到一個環境後該去翻哪些角落

我們手上現在假設有一組驗證過的憑證了——但它到底能做什麼？下一章開始把權限攤開：怎麼在幾乎沒有讀權限、又不想觸發告警的情況下，測繪出這個身分的完整權限地圖。

→ [Ch 6 IAM 枚舉與偵察：enumerate-iam 與權限測繪](./06-iam-enumeration.md)
