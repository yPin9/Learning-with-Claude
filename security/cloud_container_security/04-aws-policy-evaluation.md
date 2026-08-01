# Ch 4 — AWS policy evaluation 深入：Deny 優先與 condition

> **目標**：把 AWS 決定「這次請求准不准」的仲裁機器完整拆開。讀完你能拿一組疊在一起的 policy，自己推出結果，不再被「明明 Allow 了為什麼被拒」搞死。

上一章我們知道 policy 有五種掛法，但「一堆 policy 疊在一起到底准不准」還沒講。這是雲端最多人搞錯、也最影響攻防判斷的地方。搞不清評估邏輯，你會誤判自己有沒有某個權限——枚舉時漏掉真正能走的路，或浪費時間打一條其實被 Deny 死鎖的路。這一章把 AWS 的評估流程當成一台狀態機來讀。

## 先建直覺：這是一場「預設拒絕」的層層過關

把每次 API 請求想成一個人要進一棟高度戒備的大樓，門口有一串關卡。**預設是「不准進」**，你得一路過關才能進去，而且**任何一關喊停就是出局，後面的關卡救不了你**：

```
   請求進來 ─────────────────────────────────────────────┐
                                                          │
   ┌─── 關卡 0：預設 ───┐                                  │
   │   一律 DENY        │  除非後面有人明確放行            │
   └──────┬────────────┘                                  │
          ▼                                                │
   ┌─── 關卡 1：有沒有任何 explicit Deny? ───┐             │
   │   有 ─────────────────────────────────▶ 出局(DENY)   │
   │   沒有 ↓                                │             │
   └─────────────────────────────────────────┘             │
          ▼                                                │
   ┌─── 關卡 2：SCP 允許嗎? (Org 帳號才有) ──┐             │
   │   不允許 ─────────────────────────────▶ 出局(DENY)   │
   │   允許 ↓                                │             │
   └─────────────────────────────────────────┘             │
          ▼                                                │
   ┌─── 關卡 3：permission boundary 允許嗎? ─┐             │
   │   不允許 ─────────────────────────────▶ 出局(DENY)   │
   │   允許 ↓                                │             │
   └─────────────────────────────────────────┘             │
          ▼                                                │
   ┌─── 關卡 4：session policy 允許嗎? ──────┐             │
   │   不允許 ─────────────────────────────▶ 出局(DENY)   │
   │   允許 ↓                                │             │
   └─────────────────────────────────────────┘             │
          ▼                                                │
   ┌─── 關卡 5：有沒有 explicit Allow? ──────┐             │
   │   有 ────────────────────────────────▶ 放行(ALLOW)   │
   │   沒有 ──────────────────────────────▶ 出局(DENY)◀───┘
   └─────────────────────────────────────────┘
```

兩個直覺先種下：

1. **Deny 永遠贏。** 只要任何一份適用的 policy 有一條 explicit `Deny` 命中，整個請求就死，後面有多少 Allow 都沒用。
2. **上限型 policy（SCP / boundary / session）不授權，只砍。** 它們的功能是「就算你有 Allow，超出我這個範圍的部分我也砍掉」。真正給你權限的只有 identity-based 和 resource-based policy 的 Allow。

## 評估的完整流程：一步步走

AWS 官方把評估拆成明確步驟，我們按攻防需要重排：

**第一步：預設 deny。** 沒有任何 policy 提到這個 action/resource？拒絕。這是基準。

**第二步：蒐集所有適用的 policy。** AWS 把跟這次請求相關的所有 policy 全撈出來——身分掛的 identity-based、目標資源掛的 resource-based、頭上的 SCP、boundary、這次 session 傳的 session policy。

**第三步：找 explicit Deny。** 掃過全部 policy，只要有**一條** `Effect: Deny` 命中（action + resource + condition 都符合），立刻拒絕，結束。這一步優先於一切。

**第四步：檢查每一層上限。** SCP、permission boundary、session policy 逐層檢查——**每一層都必須「允許」這個 action**，缺一層不允許就拒絕。注意它們的「允許」是**這一層的範圍有沒有涵蓋**，不是幫你授權。

**第五步：找 explicit Allow。** 前面都過了，最後看 identity-based 或 resource-based policy 裡有沒有一條 `Allow` 命中。有就放行；沒有——回到預設 deny，拒絕。

一句話濃縮：**explicit Deny > 每層上限都要涵蓋 > 至少一條 explicit Allow > 否則預設拒絕。**

把這五步當成一次可以「短路」的評估——像程式裡的 `&&`/早退。任何一步判定拒絕，後面就不再看。用偽碼寫出來，攻防判斷會更精準：

```
def is_allowed(request):
    if any_explicit_deny(request):          # 第三步
        return DENY                          # 最硬，直接結束
    if not scp_allows(request):              # 第四步（Org 帳號才有）
        return DENY
    if not boundary_allows(request):         # 第四步
        return DENY
    if not session_policy_allows(request):   # 第四步
        return DENY
    if any_explicit_allow(request):          # 第五步
        return ALLOW
    return DENY                              # 第一步：預設 deny 兜底
```

讀這段偽碼要抓住兩件事：`any_explicit_deny` 一命中就 return，這是「Deny 永遠贏」；而四個 `if not ... : return DENY` 是四道獨立的天花板檢查，**全部要通過**才輪得到最後那個 `any_explicit_allow`。攻擊時你要問的是「我卡在哪一個 return」；防守時你要確認「我以為的限制是靠哪一個 return 生效」。

## identity-based 與 resource-based 的交互：同帳號 OR，跨帳號 AND

這是最容易踩的坑，單獨拉出來講。當請求的**發起身分**和**目標資源**的關係不同，需要的 Allow 條件不同：

```
  同一個 AWS 帳號內：
     身分的 identity policy 有 Allow   ┐
              ── OR ──                  ├─▶ 放行（任一即可，沒 Deny 前提下）
     資源的 resource policy 有 Allow    ┘

  跨帳號（身分在 A 帳號，資源在 B 帳號）：
     A 身分的 identity policy 有 Allow  ┐
              ── AND ──                 ├─▶ 放行（兩邊都要，缺一不可）
     B 資源的 resource policy 有 Allow   ┘
```

- **同帳號**：identity-based 或 resource-based **任一** Allow 就夠（前提是沒 Deny、沒被上限砍）。所以同帳號內，bucket policy 給了 Allow，即使 user 自己的 policy 沒寫，也能存取。
- **跨帳號**：**兩邊都要 Allow**。A 帳號的身分要有 identity policy 允許它去碰 B 的資源，**且** B 帳號的資源 policy 要明確允許 A 的身分。少一邊就拒。

這條規則是 Ch 8 跨帳號攻擊的地基。攻擊者最愛的情境：B 帳號某個資源的 resource policy 寫太寬（`Principal: "*"` 或信任了整個 A 帳號），這時 A 帳號裡任何有 identity Allow 的身分都能碰它。

> **例外提醒**：這條「跨帳號兩邊都要」的規則對大多數服務成立，但少數服務（如 KMS、部分 resource-based 情境）在細節上有差異。真正動手時以 IAM policy simulator 或實測為準——本課後面碰到 KMS（Ch 12）會再點。

## SCP / boundary / session 怎麼進一步收斂

三種上限型 policy，功能都是「砍」，但作用對象不同：

| 上限型 policy | 作用範圍 | 典型用途 | 對攻擊者的意義 |
|---|---|---|---|
| **SCP** | 整個 AWS 帳號（透過 Organizations OU） | 「這個帳號連 root 都不能關 CloudTrail」 | 就算你提權到 admin，SCP 擋的事你還是做不了 |
| **permission boundary** | 單一 user / role | 「這個 role 最多只能碰 S3 和 CloudWatch」 | 常被拿來擋 IAM 提權——boundary 沒放行的 action，就算 identity policy 給了也沒用 |
| **session policy** | 一次 assume role 的 session | 「這次臨時憑證只能讀，不能寫」 | assume 別人 role 時，對方可能傳入 session policy 縮小你拿到的權限 |

關鍵心智：**最終權限 = identity/resource 的 Allow ∩ SCP ∩ boundary ∩ session，再減去所有 Deny**。是**交集**，不是聯集。任何一層沒涵蓋，那個 action 就被砍掉。

permission boundary 對攻防特別重要。舉例：一個 role 的 identity policy 給了 `AdministratorAccess`（等於 `*:*`），但它掛了一個 boundary 只允許 `s3:*` 和 `cloudwatch:*`。這個 role 的實際權限就只有 S3 和 CloudWatch——boundary 把 admin 砍到剩兩個服務。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BoundaryOnlyS3AndCloudWatch",
      "Effect": "Allow",
      "Action": [
        "s3:*",
        "cloudwatch:*"
      ],
      "Resource": "*"
    }
  ]
}
```

> 上面這份 boundary JSON 已用 `python -c "import json; json.load(...)"` 驗證格式無誤。注意：boundary 裡的 `Allow` 意思是「這個範圍**允許被授予**」，不是「授予」。它自己不給任何權限，只設天花板。

## 判斷題：拿真實 policy 練推理

理論講完，動手。以下三題，每題給你 policy，你先自己推結果再看解答。JSON 都經 python 驗證。

### 題目一：Deny 壓過 Allow（送分但要看清）

某 user 掛了這份 identity policy：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAllS3",
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": "*"
    },
    {
      "Sid": "DenyProdBucket",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::acme-prod-secrets",
        "arn:aws:s3:::acme-prod-secrets/*"
      ]
    }
  ]
}
```

問：這個 user 能不能讀 `acme-prod-secrets` bucket 裡的物件？能不能讀其他 bucket？

**解答**：讀其他 bucket——**能**（`AllowAllS3` 命中，無 Deny）。讀 `acme-prod-secrets`——**不能**。雖然 `AllowAllS3` 也涵蓋它，但 `DenyProdBucket` 命中，explicit Deny 永遠贏。這就是「Deny 護欄」的標準寫法：先開全部，再挖洞禁掉敏感資源。

### 題目二：以為會過，其實被 Deny（失敗例，重點題）

某 org 在**帳號層掛了一條 SCP**，要求所有請求都必須帶 MFA，否則全 Deny：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyIfNoMFA",
      "Effect": "Deny",
      "Action": "*",
      "Resource": "*",
      "Condition": {
        "BoolIfExists": {
          "aws:MultiFactorAuthPresent": "false"
        }
      }
    }
  ]
}
```

同時這個 user 的 identity policy 給了它 `AdministratorAccess`。你用偷來的 **access key（長期憑證，沒有 MFA）** 呼叫 `s3:ListBucket`。你想：「我有 admin，穩了。」

**結果：被 Deny。** 為什麼？這條 SCP 的 condition `aws:MultiFactorAuthPresent: "false"` 命中了——你用長期 access key，沒帶 MFA。explicit Deny 在第三步就把你砍掉，`AdministratorAccess` 在第五步才輪到，根本走不到。

**這裡有兩個魔鬼細節：**

- **為什麼用 `BoolIfExists` 不用 `Bool`？** 因為某些請求情境（如某些服務對服務的呼叫）根本不帶 `aws:MultiFactorAuthPresent` 這個 key。用 `Bool` 時 key 不存在會讓 condition **不匹配**（於是 Deny 不生效，破洞）；`BoolIfExists` 在 key 不存在時把它當「符合條件」處理，把洞補上。這是 condition 寫防護時的經典陷阱，記死。
- **攻擊者的啟示**：偷到長期 access key ≠ 有 admin。先看有沒有 MFA-enforcing 的 SCP/policy。這也是為什麼**臨時憑證（assume role 拿的，Ch 5）常比長期 key 值錢**——assume role 時若原始身分帶了 MFA，臨時憑證的 `aws:MultiFactorAuthPresent` 會是 `true`，反而繞過這種 Deny。

### 題目三：NotAction 的反直覺（wildcard 陷阱）

某 role 掛了這份 boundary：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyEverythingExceptEC2",
      "Effect": "Deny",
      "NotAction": "ec2:*",
      "Resource": "*"
    }
  ]
}
```

問：這個 role 能做 `s3:GetObject` 嗎？能做 `ec2:RunInstances` 嗎？

**解答**：`s3:GetObject`——**被 Deny**。`ec2:RunInstances`——**不被這條 Deny 擋**（但要不要放行還得看有沒有 identity Allow）。

`NotAction: "ec2:*"` 配 `Effect: Deny` 的意思是：「**除了** `ec2:*` 以外的所有 action，一律 Deny」。所以 S3 的動作全中 Deny，EC2 的動作不中。`NotAction` 是「補集」——它匹配的是「列出的動作**以外**的一切」。

**陷阱**：很多人把 `NotAction` 讀成「允許 EC2」，錯了。這條根本沒 Allow 任何東西，它是一條 Deny。要真正能跑 EC2，還得有另一條 `Allow ec2:*`。`NotAction` + `Allow` 更危險：`Allow` / `NotAction: "iam:*"` / `Resource: "*"` 意思是「允許除了 IAM 以外的一切」——這幾乎是 admin，卻常被誤以為「很限縮」。看到 `NotAction` 一律放慢，先想清楚它是 Allow 還是 Deny 語境。

### 題目四：跨帳號的「兩邊都要」（AND 的實戰）

B 帳號（`222222222222`）的一個 bucket 掛了這份 resource-based policy，允許 A 帳號（`111111111111`）的 `analytics` role 讀：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCrossAccountRead",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::222222222222:role/analytics"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::acme-shared-data/*"
    }
  ]
}
```

（注意：這裡 `Principal` 的 ARN 帳號號碼是 bucket 所在帳號自己在示範，真實跨帳號情境下 `Principal` 會是**對方**帳號的 role ARN——為了聚焦「兩邊都要」的規則，重點看下面的問題。）

假設現在情境是：A 帳號的 `analytics` role 想讀 B 帳號的 `acme-shared-data` bucket。B 帳號的 bucket policy 已如上明確 Allow 了 A 的 `analytics` role。問：這樣就能讀了嗎？

**解答：不一定。** 跨帳號是 **AND**：B 這邊給了 resource Allow 是**必要條件之一**，但 A 帳號的 `analytics` role 自己的 **identity policy 也必須有一條 Allow** 讓它去 `s3:GetObject` 那個 ARN。少了 A 側的 identity Allow，即使 B 敞開大門，請求照樣被拒（回到預設 deny）。

**攻擊者的實戰啟示**：當你在 A 帳號拿到一個身分，看到 B 帳號某資源「信任了 A」，別急著高興——先確認你手上的身分在 A 側有沒有對應的 identity Allow。反過來，防守方最常見的誤區是「我 bucket policy 沒對外開，就安全」，卻忘了同帳號內是 OR——同帳號某個 user 只要 identity policy 有 Allow，不看 bucket policy 也能存取。方向記清楚：**跨帳號 AND、同帳號 OR**。

## 用 IAM policy simulator 的概念（而非硬推）

上面三題你可以硬推，但真實帳號 policy 動輒幾十條疊在一起，硬推會出錯。AWS 提供 **IAM Policy Simulator**（`iam:SimulatePrincipalPolicy` API / 主控台工具）：你餵它「哪個 principal、哪個 action、哪個 resource、什麼 condition」，它跑一遍完整評估邏輯，告訴你 allowed / denied，還會標出是**哪一條 statement**下的判決。

攻防上兩種用法：

- **防守/稽核**：驗證你以為的限制真的生效——「這個 role 真的不能碰 prod bucket 嗎」，別靠肉眼讀 JSON。
- **攻擊（在授權環境）**：如果你手上的身分剛好有 `iam:SimulatePrincipalPolicy` 權限，可以用它**乾跑**別人的權限而不真的觸發那些 API（更安靜）。但注意 simulator **不評估 resource-based policy 的跨帳號完整交互**、也不總是覆蓋所有 condition key，結果是「參考」不是「保證」。

> **本段未實測，為理論預期行為**：simulator 的邊界（哪些 condition/resource policy 它不完全模擬）會隨 AWS 更新變動。要確認，請在自己的 lab 帳號建一個受限 role，用 simulator 跑一次、再用真憑證實跑一次，比對兩者差異——這是驗證你對評估邏輯理解的最好練習。

## condition 鍵的陷阱補充

condition 是評估裡最細、最容易寫錯的部分，補幾條攻防都要知道的：

- **key 不存在時的預設行為**：如題目二，`Bool` vs `BoolIfExists`、`StringEquals` vs `StringEqualsIfExists` 差在「請求裡沒這個 key」時要不要匹配。防護型 Deny 幾乎都該用 `IfExists` 版，否則有洞。
- **`aws:SourceIp` 對上 NAT/服務呼叫**：用 IP 限制看似安全，但如果請求是透過 AWS 服務（如 VPC endpoint、Lambda）發出的，`aws:SourceIp` 可能是 AWS 內部 IP，你的 CIDR 白名單反而擋不到或誤放。
- **`aws:SourceArn` / `aws:SourceAccount` 防 confused deputy**：resource policy 只寫「信任某服務」不夠，得加這兩個 condition 綁定「是哪個帳號/資源觸發的」，否則有 confused deputy 風險（Ch 8 主題）。少了它是常見缺口。
- **wildcard 在 condition value**：`StringLike` 配 `*` 可以模糊比對，但寫太鬆（如 `"arn:aws:iam::*:role/*"`）等於沒限制。

## 對比取捨表：三種「拒絕」的差別

| 拒絕來源 | 誰能覆蓋它 | 攻擊者能不能繞 | 典型出現位置 |
|---|---|---|---|
| **預設 deny**（沒 Allow） | 補一條 Allow 即可 | 提權拿到能加 policy 的權限就能補 Allow | 到處，這是基準 |
| **explicit Deny** | **沒有東西能覆蓋** | 幾乎不能繞，除非改掉那條 Deny 本身 | 護欄 policy、MFA/IP 限制 |
| **上限沒涵蓋**（SCP/boundary/session 沒放行） | 改上限型 policy | 要有改 SCP/boundary 的權限（很高） | Org 護欄、防提權 boundary |

一句話：**explicit Deny 是最硬的牆**，遇到它別硬打，繞路（找沒被 Deny 覆蓋的 action/resource/身分）。**預設 deny 是最軟的**，補個 Allow 就開。

## 踩雷集錦

- **錯誤直覺：「我的 policy 明明有 Allow，怎麼被拒？一定是 bug。」** → 正確認識：先找 explicit Deny，再檢查 SCP / boundary / session 有沒有漏放行這個 action。Allow 是第五步才輪到的必要條件，前面任何一關都能先砍掉你。九成「Allow 卻被拒」是上限或 Deny 造成，不是 bug。
- **錯誤直覺：「跨帳號存取，只要對方 bucket policy 給我 Allow 就行。」** → 正確認識：跨帳號是 **AND**——你自己帳號的 identity policy 也得允許你去碰對方資源，兩邊缺一不可。只有同帳號才是 OR。搞反方向會誤判整條攻擊路徑通不通。
- **錯誤直覺：「permission boundary 是一種授權，掛了就有那些權限。」** → 正確認識：boundary 只設**天花板**，自己不授權。實際權限是 identity policy 的 Allow 與 boundary 的**交集**。boundary 寫了 `s3:*` 不代表這個 role 能碰 S3，還得 identity policy 也給 Allow。
- **錯誤直覺：「`NotAction: "iam:*"` 很限縮，只是不讓碰 IAM。」** → 正確認識：在 `Allow` 語境下，`NotAction: "iam:*"` 是「允許**除了** IAM 以外的一切」——那幾乎是 admin。`NotAction` 是補集運算，方向和直覺相反，看到務必先確認是 Allow 還是 Deny 語境。
- **錯誤直覺：「用 `Bool` 檢查 MFA 就能強制 MFA。」** → 正確認識：請求不帶 `aws:MultiFactorAuthPresent` 時，`Bool` 會判定「不匹配」→ Deny 不生效 → 破洞。防護型 condition 幾乎都該用 `BoolIfExists` / `StringEqualsIfExists`。這個 `IfExists` 的差別是無數雲端事故的根因。

## 進階延伸

- **評估中的 organization SCP 與 RCP**：AWS 近年新增 **resource control policy（RCP）**，是「掛在資源側的 org 層上限」，跟 SCP（身分側上限）對稱。評估時 RCP 也是一道會砍的關卡，稽核跨帳號時別漏。
- **`iam:PermissionsBoundary` 這個 condition key**：可以在 policy 裡強制「你建立的任何新 user/role 都必須掛某個 boundary」。這是防提權的關鍵護欄——沒有它，一個有 `iam:CreateRole` 的身分能建一個無上限的 admin role（Ch 7）。
- **policy 大小與數量上限**：identity policy 有字元上限、attach 數量上限。攻擊者塞後門 policy、或防守者寫超長 Deny，都可能撞到上限導致行為異常。稽核時值得注意。
- **evaluation 的 caching 與最終一致性**：改了 policy 不是瞬間全球生效，STS/IAM 有傳播延遲。攻擊者剛加的後門 policy 可能要幾秒才生效，防守者撤權也一樣有窗口。做時間敏感的判斷要考慮這個。

## 延伸閱讀

- **[AWS — Policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)**：本章的權威來源，整個評估流程官方定義。**讀哪裡**：一定要看「Determining whether a request is allowed or denied within an account」和跨帳號那兩張流程圖，跟本章的 ASCII 圖對照。**關聯**：這頁是 Ch 4 的 spec，判斷有歧義時以它為準。
- **[AWS — IAM policy elements: NotAction / NotResource / condition](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements.html)**：policy 語法元素逐一定義。**讀哪裡**：`NotAction` 的補集語義、condition operator（`Bool` vs `BoolIfExists` 等）的表格。**關聯**：判斷題二、三的語法根據。
- **[AWS — Using the IAM Policy Simulator](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)**：simulator 官方用法與限制。**讀哪裡**：特別看「limitations」段，知道它不模擬哪些東西，才不會盡信它。**關聯**：驗證本章判斷、Ch 6 枚舉時的乾跑工具。
- **[HackTricks Cloud — AWS IAM privesc / policy 濫用](https://cloud.hacktricks.wiki/en/pentesting-cloud/aws-security/aws-privilege-escalation/index.html)**：攻擊者怎麼利用評估邏輯的縫隙。**讀哪裡**：找 `NotAction`、boundary bypass、condition 繞過的段落。**關聯**：把本章的規則反過來當攻擊面看，接 Ch 7。
- **[Rhino Security Labs — Assume the Worst: Enumerating AWS Roles / policy 分析文章](https://rhinosecuritylabs.com/aws/)**：他們的 AWS 研究列表。**讀哪裡**：任何講 policy misconfiguration 導致提權的文章，觀察他們如何從一條 Allow 推到 admin。**關聯**：把評估邏輯的理解變成實際的提權推理。

---

## 本章重點整理

- 評估是一台「預設拒絕、層層過關」的狀態機：**explicit Deny > 每層上限（SCP/boundary/session）都要涵蓋 > 至少一條 explicit Allow > 否則預設 deny**。
- **explicit Deny 永遠贏**，在第三步就結束評估，後面多少 Allow 都無效。它是最硬的牆，遇到繞路別硬打。
- identity-based 與 resource-based 交互：**同帳號 OR（任一 Allow）、跨帳號 AND（兩邊都要）**。這是跨帳號攻擊的地基。
- SCP / boundary / session 是**上限**不是授權，最終權限是所有 Allow 與這些上限的**交集**再減去 Deny。
- condition 的 `IfExists` 陷阱、`NotAction` 的補集反直覺、跨帳號方向搞反，是三大最常見誤判來源。真實帳號用 IAM Policy Simulator 或實測驗證，別純肉眼推。

## 自我檢核

- [ ] 我能不看圖，按順序說出評估的五個關卡，並解釋為何 explicit Deny 在最前面就決勝
- [ ] 我能講清楚同帳號（OR）和跨帳號（AND）在 identity/resource policy 上的差別
- [ ] 我能解釋 SCP、permission boundary、session policy 各作用在什麼範圍，且它們是「砍」不是「授權」
- [ ] 我能推出判斷題二的結果，並說明為什麼偷到長期 access key ≠ 有 admin，以及 `BoolIfExists` 為何重要
- [ ] 我能解釋 `NotAction` 是補集運算，並看出 `Allow` + `NotAction: "iam:*"` 幾乎等於 admin
- [ ] 我知道 IAM Policy Simulator 能做什麼、不能做什麼

我們一直在講「身分」「憑證」，但憑證到底長什麼樣、從哪來、怎麼被偷？下一章拆開 access key 與 STS 臨時憑證，並鋪陳整門課最關鍵的攻擊地基——metadata service。

→ [Ch 5 認證與臨時憑證：access key / STS / IMDSv1 vs v2](./05-credentials-and-metadata.md)
