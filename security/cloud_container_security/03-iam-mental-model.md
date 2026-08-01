# Ch 3 — IAM 心智模型：principal / policy / role / trust

> **目標**：把雲端的權限系統在腦中建成一張「誰、能對什麼、做什麼」的圖，並理解為什麼雲端提權的本質就是在這張圖上找一條通往 admin 的路。

你打過記憶體、逆過 binary，習慣的攻擊面是「程式的邏輯錯誤」。雲端不一樣。雲端這台巨大機器的「程式」就是 IAM（Identity and Access Management，身分與存取管理），而它的「漏洞」多半不是 bug，是**設定**——一條寫得太寬的 policy、一個信任了不該信任對象的 role。這一章不談任何攻擊技術，先把 IAM 的心智模型建起來。後面每一章的提權、橫向移動、持久化，全都是在操弄這一章講的四個元素。

## 先建直覺：IAM 是一套門禁系統

把整個雲帳號想成一棟大樓，IAM 就是它的門禁系統。四個核心元素對應到門禁的四個部分：

```
      ┌─────────────────────────────────────────────────────┐
      │                  雲帳號 = 一棟大樓                    │
      │                                                     │
      │   principal          policy           permission    │
      │  ┌────────┐        ┌────────┐         ┌──────────┐   │
      │  │ 誰     │ ─持有→ │ 門禁卡 │ ─授予→  │ 能開哪扇門│   │
      │  │(員工/  │        │(規則)  │         │(具體動作) │   │
      │  │ 訪客/  │        └────────┘         └──────────┘   │
      │  │ 承包商)│                                          │
      │  └───┬────┘                                          │
      │      │                                               │
      │      │  trust                                        │
      │      │ ┌──────────────────────────────┐              │
      │      └▶│ 誰可以「借用」這張臨時工作證? │              │
      │        │ (role 的信任關係)             │              │
      │        └──────────────────────────────┘              │
      └─────────────────────────────────────────────────────┘
```

- **principal（主體）**＝要進門的「誰」。可能是正職員工（IAM user）、臨時訪客（federated identity），或一張「誰刷都行、但只在特定條件下發放」的臨時工作證（role）。
- **policy（政策）**＝門禁卡上寫的規則。「這張卡在平日 9–18 點能開 3 樓到 5 樓」。
- **permission（權限）**＝規則展開後的實際能力——「能開 305 室的門」。permission 不是你直接設定的東西，它是 policy 評估後的結果。
- **trust（信任）**＝role 特有的機制。臨時工作證不屬於任何人，牆上貼著「誰有資格來櫃檯領這張證」，那張告示就是 trust policy。

攻擊者要幹的事，用門禁比喻就是：拿到一張低權限卡（初始 credential）→ 看清楚牆上所有告示（枚舉）→ 找到一張「我這張爛卡剛好有資格去領」的高權限臨時證（trust 設錯）→ 領出來，變成能開金庫的人（提權）。

## 四個核心元素逐一拆解

### principal：誰在發出請求

principal 是「發出這次 API 請求的身分」。在 AWS 裡，你會遇到三種：

- **IAM user**：長期存在的身分，屬於某個人或某支程式。它可以掛一組**長期憑證**（access key，Ch 5 會拆）。這是最直觀、也最危險的——長期 key 一旦外洩就一直有效。
- **IAM role**：**沒有長期憑證、可以被「assume（承接）」的臨時身分**。這句話是整門課的核心，先劃起來。role 本身不代表任何人，它是一組權限的容器，加上一份「誰能來承接我」的 trust policy。當某個 principal 成功 assume 一個 role，AWS 的 STS（Security Token Service）發給它一組**臨時憑證**，短期有效。
- **federated identity（聯合身分）**：來自外部 IdP（identity provider，如公司的 SSO、Google、GitHub OIDC）的身分。它們自己沒有 IAM user，而是透過信任關係「換」成一個 role 的臨時憑證進來。

為什麼 role 是雲端攻擊的核心？因為 role 是**可移動的權限**。user 是「你是誰」，role 是「你現在扮演誰」。攻擊者很少能直接偷到 admin user 的密碼；但只要能 assume 一個高權限 role，效果一樣，而且更隱蔽（臨時憑證、CloudTrail 裡看起來像正常的服務行為）。整個 Part 1 的提權，八成都圍繞著「我這個低權限身分，能不能 assume 到某個高權限 role」。

### policy：規則寫在哪、怎麼綁

policy 是一份 JSON 文件，描述「允許或拒絕什麼」。關鍵在於**同一份 JSON 綁在不同地方，意義天差地別**。AWS 有五種 policy 掛法，先建立分類，Ch 4 再談它們怎麼交互評估：

| policy 種類 | 綁在哪 | 回答的問題 | 攻擊者關注點 |
|---|---|---|---|
| **identity-based** | 掛在 user / group / role 上 | 「這個身分能做什麼」 | 最常見的過度授權來源 |
| **resource-based** | 掛在資源上（S3 bucket、SQS、KMS key、role 的 trust） | 「誰能對我這個資源做什麼」 | 跨帳號存取、bucket 公開的根源 |
| **SCP**（Service Control Policy） | 掛在 AWS Organizations 的 OU/帳號 | 「這個帳號**最多**能做什麼」 | 收斂上限，不授權 |
| **permission boundary** | 掛在 user / role 上 | 「這個身分**最多**能被授予什麼」 | 常被用來擋提權 |
| **session policy** | assume role 當下傳入 | 「這次 session **最多**能做什麼」 | 進一步臨時收斂 |

記住一個結構：**前兩種（identity-based、resource-based）是「授權」，後三種（SCP、boundary、session）是「設上限」**。授權的相加，上限的相交——這是 Ch 4 整章的骨架，這裡先種下這顆種子。

### permission：policy 展開後的實際能力

permission 不是你直接寫的東西。你寫的是 policy，AWS 拿所有適用的 policy 一起評估，算出「這次請求到底准不准」。所以問「這個 user 有什麼 permission」其實是問「把它身上所有 identity-based policy、它能碰到的 resource-based policy、頭上的 SCP 和 boundary 全部評估完，剩下什麼」。這也是為什麼枚舉（Ch 6）在雲端這麼重要——**你幾乎不可能光看一份 policy 就知道實際權限**，得靠工具把整張圖攤開。

### trust：role 最危險的一面

trust policy 是 role 專屬的 resource-based policy，回答「**誰有資格 assume 我**」。它長這樣（一份最小的 EC2 服務信任）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2AssumeRole",
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

這份 trust 說：「EC2 服務可以承接我。」於是掛了這個 role 的 EC2 instance，就能透過 metadata（Ch 5）拿到這個 role 的臨時憑證。

trust 設錯是雲端最常見的重大缺陷之一。想像一份 trust 寫成 `"Principal": {"AWS": "*"}` ——那代表**任何 AWS 帳號的任何身分都能來承接這個 role**。如果這個 role 又掛了高權限，攻擊者只要有自己的 AWS 帳號，就能一步登天。Ch 8 整章都在打 trust。

trust 的 `Principal` 可以是四種對象，每一種都對應一類攻擊面，先建分類：

| Principal 類型 | 寫法範例 | 誰能承接 | 攻擊者關注點 |
|---|---|---|---|
| AWS 服務 | `"Service": "ec2.amazonaws.com"` | 該服務（掛此 role 的資源） | 拿下該服務的資源（EC2/Lambda）就等於拿到 role |
| 同/跨帳號身分 | `"AWS": "arn:aws:iam::111...:user/x"` 或帳號號碼 | 指定的 user/role/帳號 | 寫成整個帳號或 `*` 就是大洞 |
| 聯合身分（SAML/OIDC） | `"Federated": "arn:aws:iam::111...:oidc-provider/..."` | 通過該 IdP 認證的身分 | condition 沒綁好 subject／audience 就能被冒充 |
| 匿名/公開 | `"AWS": "*"`（無 condition） | 任何人 | 幾乎必為重大缺陷 |

聯合身分那一列特別要留意，因為它把「信任」延伸到 AWS 外部。舉例：GitHub Actions 用 OIDC 換 AWS role（Ch 31 的主題），trust 裡會信任 GitHub 的 OIDC provider，並用 condition 綁定「只有 `repo:acme/deploy:ref:refs/heads/main` 這個 workflow 能來換」。如果那條 condition 用了鬆散的 `StringLike` 加 wildcard（例如 `repo:acme/*`），任何 acme 組織下的 repo——包括一個被 fork 或被入侵的——都能換到這個 role。trust 的 condition 寫鬆，等於把外部世界的一整片信任邊界拆掉。

## user vs role：一張表講清楚

這是初學者最容易糊掉的地方，講死：

| 面向 | IAM user | IAM role |
|---|---|---|
| 代表 | 一個固定的人或程式 | 一個可被承接的臨時身分 |
| 長期憑證 | 有（access key，AKIA…） | **沒有** |
| 怎麼用 | 直接拿 key 呼叫 API | 先 assume，拿臨時憑證（ASIA…）再呼叫 |
| 憑證壽命 | 你不轉就永遠有效 | 幾分鐘到幾小時（可設，上限 12h） |
| 誰能用 | 持有 key 的人 | trust policy 允許的任何 principal |
| 攻擊價值 | 偷到就長期有效，但難偷 | 能 assume 到高權限 role＝提權 |

一句話：**user 是身分，role 是可以被穿上的權限衣服。** 雲端提權多半不是偷 user，是想辦法穿上更高權限的衣服。

## 三雲對照：換個雲，名詞變了但骨架一樣

你將來一定會碰到 Azure 和 GCP。好消息是三雲的 IAM 骨架幾乎同構，只是名詞不同。這張表存起來，Ch 38 會用到：

| 概念 | AWS | Azure（Entra ID / Azure RBAC） | GCP（Cloud IAM） |
|---|---|---|---|
| 身分 | IAM user / role | user / service principal / managed identity | user / service account |
| 群組 | IAM group | group | group |
| 權限規則 | policy（JSON） | role definition（含 actions 列表） | role（含 permissions 列表） |
| 「授予」的動作 | attach policy | **role assignment**（把 role 指派給 principal 在某 scope） | **IAM binding**（member ↔ role 綁在某 resource） |
| 授權範圍 | resource ARN / account | scope（management group → subscription → RG → resource） | resource hierarchy（org → folder → project → resource） |
| 「借用臨時身分」 | AssumeRole → STS 臨時憑證 | managed identity 拿 token；或 `az login` service principal | service account impersonation / attach 到 VM |
| 帳號級上限 | SCP | Azure Policy / management group | Organization Policy |

三雲共通的攻擊直覺：**找出「member/principal → role/policy → 高權限」的一條邊**。AWS 叫這條邊「能 assume 的 role」，GCP 叫「能 impersonate 的 service account」，Azure 叫「能拿到 token 的 managed identity 或能被指派的 role」。名詞不同，找路徑的方法一樣。

一個具體差異值得先記：**AWS 的授權綁在「身分」上（policy attach 到 user/role）；GCP 的授權綁在「資源」上（binding 綁在 project/resource，把 member 拉進來）**。所以在 GCP，你常常要問「這個 project 上有哪些 binding」，而在 AWS 你常問「這個 role 掛了哪些 policy」。方向相反，習慣要調過來。

## 最小 policy JSON：逐欄拆給你看

這是你會反覆看到的 identity-based policy 骨架。每一欄都講清楚：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowReadOneBucket",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::acme-reports",
        "arn:aws:s3:::acme-reports/*"
      ],
      "Condition": {
        "IpAddress": {
          "aws:SourceIp": "203.0.113.0/24"
        }
      }
    }
  ]
}
```

> 上面這份 JSON 已用 `python -c "import json; json.load(open('p1.json'))"` 驗證格式無誤。

逐欄：

- **`Version`**：policy 語言版本。永遠寫 `"2012-10-17"`——這是最新版，不是日期任你填。寫別的（或漏掉）會讓某些語法（如 policy 變數）失效。這是個 magic string，記死它。
- **`Statement`**：一或多條規則的陣列。多條之間的合併規則見 Ch 4。
- **`Sid`**（Statement ID）：純標籤，給人看的，方便在一大坨 policy 裡定位這條。不影響評估。
- **`Effect`**：只有兩個值——`Allow` 或 `Deny`。這是評估的核心，`Deny` 永遠壓過 `Allow`（Ch 4 主題）。
- **`Action`**：允許/拒絕的操作，格式是 `服務前綴:動作`。`s3:GetObject` 是讀物件，`s3:ListBucket` 是列出 bucket 內容。可以用 wildcard，如 `s3:Get*`。`Action: "*"` 代表所有動作——看到這個就該警覺。
- **`Resource`**：這條規則作用在哪些資源，用 ARN（Amazon Resource Name）指定。注意這裡**兩條 ARN 的差別**：`arn:aws:s3:::acme-reports` 是 bucket 本身（`ListBucket` 作用在它上面），`arn:aws:s3:::acme-reports/*` 是 bucket 內的物件（`GetObject` 作用在它上面）。少寫 `/*` 是常見錯誤，會讓 `GetObject` 拒絕。
- **`Condition`**：附加條件，全部滿足才生效。這裡是 `IpAddress` 配 `aws:SourceIp`，意思是「只有從 `203.0.113.0/24` 這個網段來的請求才允許」。condition 是雙面刃——設對是防線，設錯或漏設是缺口。Ch 4 會講 condition 的評估陷阱。

## policy 怎麼「綁」到身分：managed vs inline

同樣是 identity-based policy，掛法有兩種，枚舉時要分得出來，因為它們的可見度和隱蔽性不同：

| 掛法 | 存在形式 | 能被多少身分共用 | 攻防意義 |
|---|---|---|---|
| **AWS managed policy** | AWS 預先定義（如 `AdministratorAccess`、`ReadOnlyAccess`） | 全世界共用 | 名字一看就懂權限大小，枚舉快 |
| **customer managed policy** | 你帳號自建、可 attach 到多個身分 | 帳號內多個身分 | 改一份影響多個身分，是持久化的好目標 |
| **inline policy** | 直接內嵌在單一 user/role/group 上，沒有獨立 ARN | 只屬於那一個身分 | **較隱蔽**——不列在「managed policies」清單裡，容易被枚舉漏掉；攻擊者塞後門常用 inline |

實戰重點：枚舉一個身分的權限時，`list-attached-user-policies`（列 managed）和 `list-user-policies`（列 inline）是**兩支不同的 API**，只跑前者會漏掉 inline 後門。這也是為什麼防守方稽核時，inline policy 是最容易被忽略的角落。group 也能掛 policy，一個 user 的實際權限＝它自己的 policy ∪ 它所有 group 的 policy——枚舉別忘了往 group 追。

## 對比取捨表：user、role、federated 三種 principal 怎麼選、怎麼被打

| 面向 | IAM user | IAM role | federated identity |
|---|---|---|---|
| 該用在哪 | 極少數需長期程式化存取（漸被淘汰） | 服務身分、跨帳號、臨時提升 | 人員 SSO、CI/CD（GitHub OIDC）、跨組織 |
| 憑證壽命 | 長期 | 臨時 | 臨時（換成 role 憑證） |
| 主要攻擊面 | 長期 key 洩漏 | trust 設太寬、能被 assume | IdP 信任 condition 寫鬆、token 冒充 |
| 現代最佳實踐 | 用 IAM Identity Center 取代 | 綁最小 trust + condition | 綁死 subject/audience condition |
| 偵測難度 | 較易（同一 key 行為異常） | 中（像正常服務） | 中高（來源是外部 IdP） |

一個貫穿的判斷：**AWS 官方與現代 hardening 都在把「長期 IAM user」往「role + 聯合身分」推**。對攻擊者這意味著戰場正從「偷 key」轉向「濫用信任關係」——這也是為什麼本課 Part 1 花這麼多篇幅在 role 和 trust，而不是密碼破解。

## 為什麼雲端提權＝在權限圖上找一條路

把前面的元素連起來，一個雲帳號的權限本質是一張**有向圖**：

```
  [你的初始身分]
        │  policy 允許 sts:AssumeRole
        ▼
   [role: deploy]  ── trust 允許你 ──┐
        │                            │
        │  role 掛了 iam:PassRole    │
        ▼                            │
   [role: ci-runner]  ── trust 允許 deploy ──┐
        │                                     │
        │  掛了 AdministratorAccess           │
        ▼                                     │
   [ADMIN]  ◀───────────────────────────────┘
```

節點是身分（user / role），邊是「A 能變成 B 的手段」——可能是 `sts:AssumeRole`、`iam:PassRole`、`iam:CreateAccessKey`、`lambda` 掛 role 等等（這些具體邊 Ch 7 逐一講）。**提權就是圖上的最短路徑搜尋**：從你手上的低權限節點，找一條到 admin 的路。

這就是為什麼雲端攻擊被稱為 identity-first。你在 pentest 學的是「拿 shell → 本機提權 → 橫向」；雲端是「拿到一組 credential → 枚舉權限圖 → 沿邊走到 admin」。工具（Ch 2 的 Pacu、CloudFox，之後的 PMapper）幫你把這張圖畫出來、自動找路。你要建立的直覺是：**看到任何身分，先問「它能變成誰」，而不只是「它能做什麼」。**

### 讀一份真實的錯誤 trust：三秒看出問題

把上面的圖具體化。假設枚舉時撈到某個高權限 role 的 trust policy 長這樣（示意）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::444455556666:root" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

`"AWS": "arn:aws:iam::444455556666:root"` 這裡的 `:root` **不是**指對方帳號的 root user，而是「**帳號 `444455556666` 裡的任何身分**」（這是 AWS 的一個容易誤讀的寫法）。意思是：只要你在那個帳號有任何一個能呼叫 `sts:AssumeRole` 的身分，你就能承接這個高權限 role。若 `444455556666` 是一個第三方 SaaS 廠商、或一個你已經拿下的帳號，這就是一條現成的跨帳號提權邊。看 trust 的直覺順序：**先看 `Principal` 信任誰 → 再看有沒有 `Condition` 收斂 → 沒有 condition 又信任整個帳號/`*`，就是一條可走的邊。**

這個「先讀 Principal、再讀 Condition」的動作，你在 Ch 8 會對每一個撈到的 role 重複做上百次。現在先把它變成反射。

## 踩雷集錦

- **錯誤直覺：「role 就是另一種 user，只是名字不同。」** → 正確認識：role **沒有長期憑證、必須被 assume**，而且**任何符合 trust 的 principal 都能穿上它**。這個「可被承接」的特性是 user 完全沒有的，也是提權的主戰場。把 role 當 user 看，你會完全看不到攻擊路徑。
- **錯誤直覺：「policy 寫了 Allow，這個身分就一定能做。」** → 正確認識：Allow 只是必要條件。頭上可能有 SCP、permission boundary、或另一條 explicit Deny 把它擋掉。實際權限是**所有 policy 一起評估**的結果，光看一條 Allow 會誤判。Ch 4 專治這個。
- **錯誤直覺：「trust policy 和 identity policy 差不多，都是控權限。」** → 正確認識：identity policy 管「這個身分能做什麼」，trust policy 管「**誰能變成這個身分**」。一個是「能開哪些門」，一個是「誰能領這張卡」。trust 設太寬（`Principal: *` 或信任外部帳號）等於把高權限卡放在門口任人拿。
- **錯誤直覺：「`Resource` 寫了 bucket 名，物件讀寫就都涵蓋了。」** → 正確認識：bucket ARN（`...:acme-reports`）和物件 ARN（`...:acme-reports/*`）是**兩個不同資源**。`ListBucket` 作用在前者，`GetObject`/`PutObject` 作用在後者。漏寫 `/*` 會讓物件操作被拒；反過來，只想給列目錄卻寫了 `/*`，達不到目的。
- **錯誤直覺：「Azure/GCP 的 IAM 跟 AWS 差太多，得重學。」** → 正確認識：骨架同構——都是「principal ↔ 權限規則 ↔ scope」。真正要重新適應的是**授權綁的方向**（AWS 綁身分、GCP 綁資源）和「借用臨時身分」的機制名稱。認出同構，學第二個雲會快很多。

## 進階延伸

- **ABAC（attribute-based access control）**：除了直接授權，AWS 支援用 tag 當條件（`Condition` 裡比對 `aws:ResourceTag` vs `aws:PrincipalTag`）。這讓權限「動態」化——攻擊者若能改 tag，可能繞過或觸發授權。Ch 7 會碰到。
- **service-linked role**：AWS 服務自動建立、掛在自己身上的 role，trust 只信任該服務。它們通常權限不小，且容易被忽略。枚舉時別漏。
- **role chaining**：assume 一個 role 後，再用它的臨時憑證去 assume 下一個 role。上面的權限圖就是靠 chaining 走完的。注意 chaining 有壽命限制（每跳最多 1 小時），且 CloudTrail 會留下一連串 `AssumeRole`——攻擊與偵測都要知道。
- **`aws:PrincipalArn` 與 policy 變數**：condition 裡能引用發起者的 ARN、tag 等，做出「只能操作自己名下資源」這種自我限定 policy。寫得巧是防線，寫錯是提權缺口（例如變數展開後意外放寬）。

## 延伸閱讀

- **[AWS — Identities (users, groups, roles)](https://docs.aws.amazon.com/IAM/latest/UserGuide/id.html)**：官方對 user/role/group 的定義。**讀哪裡**：重點看 role 與 temporary credentials 的段落，把「role 沒有長期憑證」這件事從官方角度確認一次。**關聯**：這章 user vs role 表的權威來源，也是 Ch 5 憑證章的前置。
- **[AWS — Policies and permissions in IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies.html)**：五種 policy 種類的官方總覽。**讀哪裡**：identity-based / resource-based / SCP / boundary / session policy 各自的定義表格。**關聯**：直接餵給 Ch 4 的評估邏輯。
- **[HackTricks Cloud — AWS IAM](https://cloud.hacktricks.wiki/en/pentesting-cloud/aws-security/aws-basic-information/aws-iam-and-sts-enum.html)**：攻擊者視角的 IAM/STS 整理。**讀哪裡**：先看 IAM 與 STS 的基本概念段，之後每一章的提權技巧都可回這裡找變體。**關聯**：本課 Part 1 的實戰對照百科。
- **[Rhino Security Labs — AWS IAM Privilege Escalation](https://rhinosecuritylabs.com/aws/aws-privilege-escalation-methods-mitigation/)**：把「權限圖找路」具體化成 21 條提權路徑。**讀哪裡**：先看它怎麼把每條路徑描述成「你有 X 權限 → 能拿到 admin」，感受權限圖的邊長什麼樣。**關聯**：Ch 7 的骨架來源，這章先建圖的直覺，那章填邊。
- **[NCC Group — AWS 三雲 IAM 對照與 PMapper 介紹](https://www.nccgroup.com/us/research-blog/)**：搜尋 PMapper / IAM 相關文章。**讀哪裡**：PMapper 如何把帳號的 IAM 關係建成圖並自動找提權路徑。**關聯**：把本章「權限圖」從比喻變成能跑的工具，Ch 6 枚舉會用到。

---

## 本章重點整理

- IAM 是雲帳號真正的「程式」，它的攻擊面主要是**設定錯誤**，不是傳統 bug。用門禁系統建直覺：principal（誰）/ policy（規則）/ permission（實際能力）/ trust（誰能領臨時證）。
- **role 是雲端攻擊的核心**：沒有長期憑證、可被 assume 的臨時身分，任何符合 trust 的 principal 都能穿上它。user 是身分，role 是可穿上的權限衣服。
- policy 有五種掛法：identity-based / resource-based（**授權**，相加）與 SCP / permission boundary / session policy（**設上限**，相交）。實際 permission 是全部一起評估的結果。
- 三雲 IAM 骨架同構（principal ↔ 權限規則 ↔ scope），差別在授權綁的方向與「借用臨時身分」的機制名稱。
- 雲端提權＝在「身分為節點、變身手段為邊」的權限圖上，找一條到 admin 的路。看到任何身分先問「它能變成誰」。

## 自我檢核

- [ ] 我能用門禁比喻說出 principal / policy / permission / trust 各對應什麼
- [ ] 我能講清楚 IAM user 和 IAM role 的四個關鍵差異，並解釋為何 role 是提權核心
- [ ] 我能列出五種 policy 種類，並分辨哪些是「授權」哪些是「設上限」
- [ ] 我能把一份最小 policy JSON 的 Effect / Action / Resource / Condition 逐欄講出用途，並說明 bucket ARN 與物件 ARN 的差別
- [ ] 我能在三雲對照表上，把 AWS 的「AssumeRole」對應到 GCP 和 Azure 的等價機制
- [ ] 我能解釋「提權＝權限圖找路」，並說出至少一種圖上的「邊」

policy 種類我們列了，但「一堆 policy 疊在一起到底准不准」還沒講——explicit Deny 為什麼永遠贏？跨帳號為什麼兩邊都要 Allow？下一章把評估邏輯這台仲裁機器拆開。

→ [Ch 4 AWS policy evaluation 深入：Deny 優先與 condition](./04-aws-policy-evaluation.md)
