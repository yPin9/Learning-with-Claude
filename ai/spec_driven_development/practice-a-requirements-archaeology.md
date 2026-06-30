# 練習 A — 需求考古學——把模糊需求拆成各層產物

> **目標**：拿一段真實感的模糊需求，把它拆解成軟體開發生命週期（Software Development Life Cycle，SDLC）各層的標準產物——使用者故事（User Story）、驗收條件、EARS 記法、非功能需求——並找出其中互相矛盾的「多重真相（Multiple Truths）」，體會為什麼需求這麼容易出問題。

---

## 背景與動機

模糊需求是軟體專案最常見的地雷，但它很少長得像「系統要好用」這樣明顯爛的句子。真實世界的模糊需求通常有一個「看起來很完整」的外殼，裡面藏著複數個衝突的假設，讓不同的人讀完之後在腦子裡構建出截然不同的系統。

Fred Brooks 在 1986 年的〈No Silver Bullet〉裡說：

> "The hardest single part of building a software system is deciding precisely what to build... No other part of the work so cripples the resulting system if done wrong."

五十年後的 2025 年，Sean Grove 在〈The New Code〉（AI Engineer World's Fair）裡呼應了同一個診斷，但換了一個角度：當 LLM 把「寫程式」的成本壓到接近零，瓶頸就完全暴露在意圖層——也就是你能不能把「到底要建什麼」講清楚（引文來源：community transcript，lawwu.github.io/transcripts/8rABwKRsec4.html）。

這個練習的目的，是讓你對自己的「讀需求直覺」做一次 X 光掃描：你能發現多少種對同一段需求的合理詮釋？你能把一段話裡的功能性需求、非功能需求、限制、假設各自挑出來嗎？

> 如果你對 SDLC 各層產物的定位還不熟，先回看 [Ch 3 SDLC 到底是什麼](./03-sdlc.md)。
> 如果你對使用者故事（User Story）和 INVEST 原則還沒建立直覺，先讀 [Ch 9 User Story 與 INVEST](./09-user-stories-invest.md)。
> 如果你不清楚 EARS 的五種句型，先讀 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

---

## 任務規格

### 輸入：一段真實感的模糊需求

以下是一段偽裝成「產品需求書（Product Requirements Document，PRD）」的文字，來自一個假想的線上食品訂閱服務 **FreshBox**：

---

> **FreshBox 結帳流程改版需求 v0.3（2024-09-01）**
>
> 背景：目前結帳放棄率高，UX 團隊分析發現問題出在付款頁太複雜。主要改版目標是讓結帳更快、更安全。
>
> 需求：
>
> 1. 使用者可以儲存信用卡資料，方便下次使用。
> 2. 結帳頁面應在 3 秒內載入。
> 3. 新用戶首次下單享 20% 折扣，折扣代碼由行銷團隊提供。
> 4. 付款失敗時，系統要通知使用者。
> 5. 支援多種付款方式，包含信用卡、LINE Pay 和超商代碼。
> 6. 訂單確認後要寄確認信。
> 7. 高級會員（Premium Member）結帳免運費。
> 8. 系統要符合 PCI DSS 標準。
> 9. 管理後台可以看到當日訂單總覽。

---

### 你的任務

把上面那段需求拆解成以下五個層次的產物，並完成最後一個分析題。

#### 任務一：提取使用者故事（User Story）

從九點需求裡，找出至少 **四條** 有明確 `As a / I want / so that` 結構的使用者故事。每一條都要通過 INVEST 的 **T（Testable）** 測試——如果你自己測不出這條故事，就代表它還不夠具體。

#### 任務二：為其中一條故事寫驗收條件

選一條你認為「最需要釐清的故事」，為它寫出 Given-When-Then 格式的驗收條件，**至少三個情境**，包含 happy path 和一個失敗情境（unhappy path）。

#### 任務三：用 EARS 重寫功能性需求

從九點需求裡選出至少 **三個功能性需求**，把它們改寫成 EARS 句型（需標出你用的是哪一種：Event-driven、State-driven、Unwanted-behaviour 或 Ubiquitous）。

範本提醒：
```
[Event-driven]  When <trigger>, the <system> shall <response>.
[State-driven]  While <state>, the <system> shall <response>.
[Unwanted]      If <condition>, then the <system> shall <response>.
[Ubiquitous]    The <system> shall <response>.
```

#### 任務四：列出非功能需求（NFR）並標出 ISO/IEC 25010 類別

從九點需求中識別出 **所有** 非功能需求，為每一條標上它屬於 ISO/IEC 25010:2023 九大特性中的哪一項（Performance Efficiency、Security、Reliability 等），並說明理由。

#### 任務五：找出「多重真相」——矛盾分析

上面那九點需求裡藏著至少 **四個**「多重真相」：同一段文字，不同的人可以合理推導出互相矛盾的結論，或者這段需求有明顯的資訊缺口，導致技術決策無從下手。

列出這四個（或更多）矛盾/缺口，每一個用以下格式：
```
[矛盾 N]
需求原文：...
詮釋 A：...
詮釋 B：...
為什麼這個矛盾很貴：...（如果留到實作後才發現，成本是什麼）
```

### 限制與驗收標準

- 使用者故事不可以直接把需求翻譯成 `As a user, I want requirement 1, so that done`，這不算。角色（role）要具體（`FreshBox 新用戶`、`高級會員`、`結帳後台管理員`），受益（benefit）要說明商業動機，不是重述功能。
- EARS 句型必須使用正確的關鍵字（`When`、`While`、`If...then`、`Where`）；「shall」不可省略。
- 矛盾分析要說明「為什麼這個矛盾很貴」：到實作後才發現這個矛盾，大約在哪個 SDLC 階段被迫返工？

---

## 期望輸出範例

以下是任務一、三、五各一條的示範，供你對齊深度。**這不是完整答案**，你的答案裡要有更多條目。

### 任務一示範（一條使用者故事）

```
As a FreshBox 新用戶（首次在平台下單的帳號），
I want 在結帳時輸入行銷團隊提供的折扣碼，
so that 我的首筆訂單能享有 20% 的折扣，降低第一次嘗試的心理門檻。

INVEST 快速審查：
- Independent：可以獨立開發折扣碼驗證邏輯，不依賴信用卡儲存功能。
- Negotiable：折扣碼格式（大小寫？位數？）可以和行銷團隊再協商。
- Valuable：對獲客率直接有幫助，有清楚的商業理由。
- Estimable：需要澄清折扣碼由系統產生還是行銷手動輸入才能估。← 注意：這裡有一個缺口。
- Small：範圍限定在折扣碼驗證＋折扣計算；不包含折扣碼後台管理系統。
- Testable：可以寫測試：輸入有效碼 → 折扣出現；輸入無效碼 → 顯示錯誤；二次使用 → 被拒絕。
```

### 任務三示範（一條 EARS）

```
需求原文（第 4 條）：「付款失敗時，系統要通知使用者。」

[Unwanted-behaviour]
If a payment transaction fails,
then the FreshBox checkout system shall display an error message
and retain the user's cart contents without requiring re-entry.

說明：
- 原文「通知」沒說通知方式（頁面、email、推播？），這裡先對齊最低限度（頁面錯誤訊息）。
- 原文沒說失敗後購物車怎麼辦，加了「不強迫用戶重新填寫」，這是一個需要確認的合理詮釋假設。
```

### 任務五示範（一個矛盾）

```
[矛盾 1]
需求原文（第 1 條）：「使用者可以儲存信用卡資料，方便下次使用。」
詮釋 A：FreshBox 系統直接在自己的資料庫儲存加密的信用卡號碼（PAN）。
詮釋 B：FreshBox 使用 Stripe / 藍新等金流的 token 機制，只儲存 token，不碰實際卡號。
為什麼這個矛盾很貴：第 8 條同時要求 PCI DSS 合規。詮釋 A 意味著 FreshBox 必須取得
PCI DSS SAQ D（最嚴格等級），需要大量安全審計、加密架構和年度滲透測試；
詮釋 B 把合規責任外包給金流服務商，FreshBox 取得 SAQ A 即可合規。
如果在系統設計完成後才發現選錯路，資料庫 schema、API 設計、安全架構都要大規模返工，
大約在設計→實作的交界點被迫退回需求層重新決策。
```

---

## 如果你卡住了

1. **卡在使用者故事的「角色」**：回頭看需求，問自己「誰在這個功能上有利害關係？」——結帳的是顧客，看後台的是管理員，管折扣碼的是行銷。角色不同，故事就不同。

2. **卡在 EARS 句型選擇**：先問「這個需求有觸發條件嗎？」有 → 考慮 Event-driven（When）或 Unwanted-behaviour（If...then）。「它描述的是一個持續的狀態嗎？」是 → State-driven（While）。「它是一直都要成立的屬性嗎？」是 → Ubiquitous。不確定時，Unwanted-behaviour 和 Event-driven 最常被混淆，差別在於：`When` 描述正常流程的觸發點，`If...then` 描述不好的事情發生後要怎麼回應。

3. **卡在非功能需求分類**：如果一個需求說的是「系統要做什麼」，它是功能性需求；如果說的是「做到什麼程度」或「在什麼條件下」，它是非功能需求。ISO/IEC 25010:2023 把這些歸為 Performance Efficiency、Security、Reliability、Maintainability、Flexibility 等九大類（注意：2023 版把 Usability 改名為 Interaction Capability，新增了 Safety 特性）。

4. **卡在矛盾分析**：試著對自己問「如果兩個工程師各自讀完這份需求，他們的 design doc 會不一樣嗎？」如果會，找出差異點，那就是矛盾或缺口。

5. **矛盾不知道貴在哪**：回頭想 Boehm 的變更成本曲線——需求階段發現的矛盾，修正成本最低；到了程式碼上線後才發現，意味著設計、實作、測試全部要重走。具體問自己：這個矛盾在哪個 SDLC 階段最可能浮出水面？

---

## 實作步驟建議

### Step 1：先做一次無結構的「需求閱讀」

把九條需求讀一遍，不要馬上開始拆解。在腦子裡想像你是一個剛加入 FreshBox 的工程師，你收到這份文件，你會問什麼問題？先把問題列出來，再回頭對齊到任務一到五。

### Step 2：識別角色（Actor），建立 actor 清單

把九條需求掃一遍，把所有隱含的角色（誰在使用這個系統？）列成一張表。這是寫出好的使用者故事的前提，也是找矛盾的索引——同一個功能對不同角色可能有不同的隱含假設。

### Step 3：先做任務四（非功能需求分類）

非功能需求比較容易辨認，從它開始建立信心。找到之後，把剩下的自然就是功能性需求，然後你才能把任務一（使用者故事）做得更乾淨。

### Step 4：把每一條功能性需求用 EARS 改寫（任務三）

先別管它好不好，先忠實翻譯原文。翻完之後你會發現一些 EARS 句子裡有空格（要填入什麼？）——那些空格就是你的矛盾清單的素材。

### Step 5：回頭做矛盾分析（任務五）

把你在 Step 4 發現的空格，加上 Step 1 列出的問題，整理成矛盾清單，計算「如果留到實作後才發現這個矛盾，要重做什麼」，來決定哪幾個矛盾最需要在衝刺（sprint）開始前釐清。

---

## 完整參考解答

**寫完所有五個任務之後再打開，否則你就是在做自欺欺人的閱讀理解，不是在做需求工程練習。**

<details>
<summary>點開參考解答</summary>

### 任務一：使用者故事（六條，供參考）

```
Story 1 — 儲存付款方式
As a FreshBox 回購顧客，
I want 在結帳後選擇將這張信用卡儲存到帳戶，
so that 下次訂購時不需要重新輸入卡號，縮短結帳時間。

INVEST 快速審查：
- Testable：可測試「儲存後下次結帳是否自動帶入卡末四碼」、「刪除儲存卡後再結帳是否退回手動輸入」。
- 注意缺口：「儲存」到底是存加密的 PAN 還是金流 token？這個技術決策影響 PCI DSS 等級，
  需要在 sprint planning 前釐清（見任務五矛盾 1）。
```

```
Story 2 — 折扣碼結帳
As a FreshBox 新用戶（系統判定為首次下單帳號），
I want 在結帳頁輸入一組行銷提供的折扣碼，
so that 我的首筆訂單自動套用 20% 折扣，減少第一次試用的猶豫。

INVEST 快速審查：
- Estimable：需先確認折扣碼是行銷手動產生並直接告知用戶，還是系統自動發送。
  若後者，需要連帶估算碼發送邏輯的工作量。
- Testable：有效碼 → 折扣出現在訂單摘要；已過期或已使用碼 → 顯示明確錯誤訊息；
  非新用戶使用 → 被拒絕且說明原因。
```

```
Story 3 — 付款失敗通知
As a FreshBox 顧客，
I want 付款失敗時在頁面看到具體錯誤提示，
so that 我知道是卡片餘額不足、網路問題還是其他原因，並能採取對應行動。

INVEST 快速審查：
- Testable：觸發各種失敗情境（餘額不足、卡片過期、三D驗證失敗）→ 分別看到不同或相同的錯誤訊息。
- 注意缺口：原文沒說購物車在失敗後怎麼辦，需要確認是否保留商品與數量。
```

```
Story 4 — 免運費結帳
As a FreshBox 高級會員（Premium Member），
I want 在結帳摘要中看到運費自動歸零，
so that 我確認我的會員優惠已正確套用，不需要打電話詢問客服。

INVEST 快速審查：
- Independent：免運邏輯可以獨立於折扣碼邏輯，不需要同時開發。
- 注意缺口：「高級會員」的認定標準（會員等級表、升等門檻）沒有在需求文件中定義；
  免運是對所有訂單都免，還是只限某些商品組合？
```

```
Story 5 — 訂單確認信
As a FreshBox 顧客，
I want 在成功下單後幾分鐘內收到一封訂單確認信，
so that 我有書面記錄，知道訂單被正確接收，並可以追蹤後續出貨狀態。

INVEST 快速審查：
- Estimable：「幾分鐘內」不夠精確，需要定義 SLA（例：5 分鐘內）才能估工。
- Testable：下單後 X 分鐘內收到信；信件包含訂單編號、商品清單、總金額、預計到貨日。
```

```
Story 6 — 後台當日訂單總覽
As a FreshBox 後台管理員，
I want 在管理後台看到當日所有訂單的彙總資料，
so that 我能即時掌握今日業績，並在高峰時段做調度決策。

INVEST 快速審查：
- Small：「總覽」範圍需要定義——是一個摘要數字（筆數＋總金額），還是包含逐筆清單？
  需要設定邊界，否則這條故事太大，應該拆分。
- Testable：進入後台後能看到「今日訂單筆數」和「今日訂單金額」，且數字與資料庫一致。
```

---

### 任務二：驗收條件（以 Story 3 付款失敗為例）

```gherkin
Feature: 付款失敗通知
  讓顧客在付款失敗時得到明確回饋，能採取正確的後續動作。

  Background:
    Given 顧客 Alice 已登入 FreshBox 帳號
    And Alice 的購物車有 2 件商品：「有機草莓 500g」和「燕麥奶 1L」

  Scenario: 信用卡餘額不足導致付款失敗
    Given Alice 在結帳頁選擇信用卡付款
    And 該信用卡目前可用餘額不足以支付訂單金額
    When Alice 按下「確認付款」
    Then 系統應在 3 秒內於結帳頁顯示錯誤訊息「付款失敗：您的信用卡餘額不足，請確認可用額度或改用其他付款方式。」
    And 購物車商品應保持不變（2 件商品均未移除）
    And 系統不應建立訂單記錄

  Scenario: 超商代碼付款逾期未繳
    Given Alice 選擇「超商代碼」付款並成功取得代碼
    And 代碼有效期限為 24 小時
    When Alice 在 24 小時後前往超商繳費
    Then 超商繳費系統應顯示「代碼已過期」
    And FreshBox 系統應在 Alice 下次登入時顯示提示「您的訂單 #XXXXX 因逾期未付款已自動取消，請重新下單。」
    And 原訂單狀態應更新為「已取消」

  Scenario: 網路中斷導致付款請求逾時
    Given Alice 在結帳頁按下「確認付款」
    And 付款 API 請求逾時（超過 30 秒未收到回應）
    When 系統偵測到逾時
    Then 系統應顯示「付款處理中斷，請稍後重試或聯絡客服確認訂單狀態。」
    And 系統不應重複扣款
    And 購物車應保持原狀

  Scenario: 三D驗證（3D Secure）失敗
    Given Alice 使用一張啟用 3D 驗證的信用卡結帳
    When Alice 在銀行的驗證頁面輸入錯誤的 OTP 三次
    Then 信用卡驗證應失敗
    And FreshBox 頁面應顯示「信用卡驗證失敗，請重試或改用其他付款方式。」
    And 不應扣除任何款項
```

---

### 任務三：EARS 重寫（五條）

```
需求 1：「結帳頁面應在 3 秒內載入」

[Ubiquitous]
The FreshBox checkout page shall render a fully interactive state
within 3 seconds of the user navigating to the checkout URL,
measured under standard broadband conditions (≥ 10 Mbps download).

說明：Ubiquitous 句型，因為這是一個不需要特定觸發條件的恆常屬性。
注意加了「under standard broadband conditions」——原文沒有指定測量環境，
這是一個假設，需要和產品確認。
```

```
需求 4：「付款失敗時，系統要通知使用者」

[Unwanted-behaviour]
If a payment transaction returns a failure response from the payment gateway,
then the FreshBox checkout system shall:
  (a) display an error message explaining the failure reason in the user's language, and
  (b) retain the user's cart contents without requiring re-entry, and
  (c) not create an order record.

說明：Unwanted-behaviour 句型（If...then）。原文「通知」只說要通知，
沒說通知方式，這裡選最低限度（頁面訊息）並加了購物車保留的假設。
```

```
需求 7：「高級會員結帳免運費」

[State-driven]
While the authenticated user holds an active Premium Member subscription,
the FreshBox checkout system shall apply a shipping fee of zero
to all eligible orders before displaying the order summary.

說明：State-driven（While）句型，「高級會員」是一個持續的狀態，
而不是一個觸發事件。加了「active」來排除「過期高級會員」的歧義。
加了「eligible orders」但需要確認 eligible 的定義（所有訂單？某些品類除外？）。
```

```
需求 5（部分）：「支援多種付款方式，包含信用卡、LINE Pay 和超商代碼」

[Ubiquitous]
The FreshBox checkout system shall accept payment via
  (a) credit and debit cards (Visa, Mastercard, JCB),
  (b) LINE Pay, and
  (c) convenience store payment codes (7-Eleven ibon / FamiPort).

說明：原文沒有定義信用卡種類（Visa？美國運通？），超商沒說哪幾家。
這裡做了一個合理猜測，但所有括號內容都需要業務確認。
```

```
需求 6：「訂單確認後要寄確認信」

[Event-driven]
When a FreshBox order transitions to the "Confirmed" status,
the system shall send an order confirmation email to the customer's
registered email address within 5 minutes.

說明：Event-driven（When）句型，「訂單確認後」是一個明確的狀態轉移事件。
原文沒有時間 SLA，這裡加了「5 分鐘內」作為合理假設，需要和技術與產品確認。
```

---

### 任務四：非功能需求識別與 ISO/IEC 25010:2023 分類

| # | 原文 | ISO 25010:2023 特性 | 說明 |
|---|------|---------------------|------|
| 2 | 結帳頁面應在 3 秒內載入 | **Performance Efficiency**（時間行為 Time Behaviour） | 衡量系統在正常工作負載下的回應時間，屬於效能效率中的時間行為子特性 |
| 8 | 系統要符合 PCI DSS 標準 | **Security** | PCI DSS 是支付卡行業的安全合規標準，直接對應安全性特性（含機密性 Confidentiality、完整性 Integrity、可靠性 Accountability）|
| 1（部分）| 儲存信用卡資料 | **Security** | 持卡人資料的儲存保護屬於安全性特性，與 PCI DSS 緊密相關 |

注意：九條需求裡只有 2、8 是純非功能需求；第 1 條兼含功能性（能否儲存）和非功能性（如何安全地儲存）。第 9 條（管理後台訂單總覽）看起來像功能性需求，但如果限定「當日」隱含了資料更新頻率的限制，也可以衍生出 Performance Efficiency 的隱性要求。

---

### 任務五：矛盾分析（六個）

```
[矛盾 1] 信用卡「儲存」的技術路線
需求原文：「使用者可以儲存信用卡資料。」（第 1 條）+ 「系統要符合 PCI DSS 標準。」（第 8 條）
詮釋 A：FreshBox 在自己的資料庫儲存加密的 PAN（Primary Account Number，主帳號碼），
        需取得 PCI DSS SAQ D（最嚴格等級，含滲透測試、安全掃描、QSA 審計）。
詮釋 B：FreshBox 透過金流商（Stripe、藍新）的 token 化機制儲存，不碰實際卡號，
        只需 PCI DSS SAQ A（最輕量等級）。
為什麼這個矛盾很貴：SAQ A vs SAQ D 的合規成本差距可能是數十萬台幣到數百萬台幣的年度差異。
如果在資料庫設計完成後才發現選錯路線，schema、API 設計、金流整合、安全測試都要重來，
最晚在設計階段結束前就必須拍板。
```

```
[矛盾 2] 折扣碼的來源與管理
需求原文：「折扣代碼由行銷團隊提供。」（第 3 條）
詮釋 A：折扣碼由行銷人員手動建立（例如：一個 Google Sheet），工程師實作驗證邏輯即可，
        不需要建後台。技術工作量：1-2 天。
詮釋 B：行銷「提供」代碼，意味著系統要提供後台讓行銷人員自己產生、設定有效期、查看使用狀況。
        技術工作量：2-3 週。
為什麼這個矛盾很貴：工作量差了 10 倍以上。如果工程師按詮釋 A 估完 sprint，
實作後行銷說「我以為有後台」，就要臨時加開一個 sprint，打亂整個排程。
```

```
[矛盾 3] 「新用戶」的定義
需求原文：「新用戶首次下單享 20% 折扣。」（第 3 條）
詮釋 A：「新用戶」= 帳號第一次在 FreshBox 下單，系統以訂單記錄判斷。
詮釋 B：「新用戶」= 信用卡第一次在 FreshBox 被使用，防止同一人用多個帳號重複享折扣。
詮釋 C：「新用戶」= 帳號建立後 X 天內，無論有沒有下單記錄。
為什麼這個矛盾很貴：不同定義需要不同的後端驗證邏輯（帳號綁定 vs 信用卡指紋 vs 建立時間），
也影響到被薅羊毛的商業風險。行銷可能想要詮釋 A（寬鬆，獲客最大化），
財務可能想要詮釋 B（嚴格，防濫用）。留到上線後才發現，需要資料庫 migration 加欄位。
```

```
[矛盾 4] 結帳頁「3 秒載入」的測量基準
需求原文：「結帳頁面應在 3 秒內載入。」（第 2 條）
詮釋 A：3 秒是 Time to Interactive（TTI），用 Lighthouse 在標準環境量測。
詮釋 B：3 秒是 Time to First Byte（TTFB），即伺服器回應時間。
詮釋 C：3 秒是 First Contentful Paint（FCP），頁面有內容出現就算。
詮釋 D：3 秒是「在行動網路（4G）下的真實設備測試」結果，比桌機寬頻嚴格 3-5 倍。
為什麼這個矛盾很貴：前端工程師可能按詮釋 B 實作並通過測試，
但 UX 用 Lighthouse 量到 TTI 是 5.5 秒，兩邊都說自己對。
這個矛盾最晚在 QA 測試階段爆發，但爭議可以拖到上線。
```

```
[矛盾 5] 「訂單確認」的定義
需求原文：「訂單確認後要寄確認信。」（第 6 條）
詮釋 A：確認 = 用戶按下「確認付款」按鈕，不管付款是否成功，就觸發寄信。
詮釋 B：確認 = 付款成功，金流回傳成功回應後，才算訂單確認。
詮釋 C：確認 = 商家人工確認備貨，訂單進入「備貨中」狀態才寄信。
為什麼這個矛盾很貴：詮釋 A 在付款失敗時也會寄出「恭喜您完成訂單」的信，
造成顧客困惑並增加客服量。詮釋 C 需要額外的人工流程觸發機制。
這個矛盾會在整合測試或 UAT 時爆發，但如果 email 服務是外部廠商，
整合成本加上測試成本不低。
```

```
[矛盾 6] 「當日訂單總覽」的更新頻率
需求原文：「管理後台可以看到當日訂單總覽。」（第 9 條）
詮釋 A：即時（real-time）數據，每次載入頁面都從資料庫即時查詢。
詮釋 B：每小時批次更新的報表，可能有 up to 60 分鐘的延遲。
詮釋 C：T+1 日前一天的數據，類似每日財務對帳報告。
為什麼這個矛盾很貴：即時查詢在訂單量高峰時可能造成 DB 效能問題（跟第 2 條的 3 秒要求互相拉扯）；
批次報表需要額外的排程基礎設施。這兩種架構的工程複雜度和成本差異顯著。
```

</details>

---

## 測試用例表

完成練習後，用下表自我檢驗你的答案品質：

| 驗收項目 | 標準 | 你的答案通過嗎？ |
|---------|------|----------------|
| 使用者故事角色具體 | 角色不是「user」，是「新用戶」「高級會員」「後台管理員」等 | ☐ |
| 使用者故事 benefit 非贅述 | `so that` 說明商業動機，不是重複 `I want` | ☐ |
| INVEST Testable 通過 | 每條故事能說出至少一個具體測試情境 | ☐ |
| Given-When-Then 包含 unhappy path | 至少一個失敗情境（卡片失敗、過期、逾時其中之一） | ☐ |
| EARS 使用正確關鍵字 | `When`、`While`、`If...then`、`Where` 或不帶關鍵字的 Ubiquitous | ☐ |
| EARS 保留 `shall` | 每條 EARS 句子都有 `shall`，沒有用 `should`、`must`、`will` 代替 | ☐ |
| EARS 有標注句型名稱 | 每條都標了 `[Event-driven]`、`[Ubiquitous]` 等 | ☐ |
| 非功能需求對應 ISO 25010:2023 | 分類用 2023 版特性名（注意 Usability 已改名 Interaction Capability） | ☐ |
| 矛盾至少四個 | 每個矛盾有兩種以上的詮釋，且說明了「為什麼很貴」 | ☐ |
| 矛盾說明成本 | 每個矛盾說明了在哪個 SDLC 階段爆發、重做什麼 | ☐ |

---

## 延伸挑戰

如果九條需求對你來說不夠燒腦，繼續挑戰：

1. **自動化矛盾偵測**：寫一個 Python 腳本，把九條需求逐條送給 LLM（Claude API 或 OpenAI API），要它列出每條需求缺少的資訊（missing information）。觀察 LLM 找到的和你手動找到的有何差異——這就是為什麼「讓 LLM 幫你找矛盾」本身也需要你先理解矛盾的形狀。

2. **補全 EARS 清單**：把九條需求全部用 EARS 改寫（不只三條），然後把你在改寫過程中加的「假設」全部列出來，那就是你在下 sprint 前必須和 PM 確認的問題清單。

3. **寫一份完整的 `requirements.md`**：參考 AWS Kiro 的格式（requirements.md 用 EARS 記法），把 FreshBox 結帳改版的九個需求整合成一份給 LLM coding agent 使用的規格文件，要能讓 agent 直接從這份文件生成實作計畫而不需要猜測。

4. **測試 LLM 詮釋差異**：把九條原始需求貼給兩個不同的 LLM（例如 Claude 和 GPT-4o），請它們各自「寫一份 API 設計文件」，然後對比兩份文件的差異——每一個差異點背後都對應一個需求裡沒說清楚的地方。

---

## 自我檢核

完成這個練習後，用自己的話回答以下問題。**不要翻答案**，如果你說不出來，代表你需要再讀一遍。

- [ ] 用自己的話說明：為什麼「使用者可以儲存信用卡資料」這一句話，可以衍生出 PCI DSS 合規等級的巨大差異？
- [ ] 面試被問「EARS 和 Given-When-Then 有什麼不同，什麼時候用哪個？」你會怎麼回答？
- [ ] 「非功能需求」和「功能性需求」的本質區別是什麼？能不能從 FreshBox 的例子各舉一條，說明為什麼它屬於那一類？
- [ ] Boehm 的變更成本曲線（Ch 6）和這個練習的矛盾分析有什麼關係？你能用一句話把它們連起來嗎？
- [ ] ISO/IEC 25010:2023 把舊版的「Usability」改名為「Interaction Capability」，還新增了「Safety」——這兩個改動對需求工程師的工作有什麼實際影響？

---

## 延伸閱讀

1. **EARS: Easy Approach to Requirements Syntax — 官方指南（Alistair Mavin）**
   - 網址：https://alistairmavin.com/ears/
   - 讀什麼、從哪裡開始：直接看「EARS Patterns」段落，對照你在任務三寫的句子，確認你用的關鍵字和 clause 順序是否正確。
   - 和本練習的關聯：本練習任務三的句型規範直接來自這裡。

2. **Easy Approach to Requirements Syntax（原始 RE'09 論文）**— Mavin, Wilkinson, Harwood, Novak（Rolls-Royce）
   - 網址：https://dl.acm.org/doi/10.1109/RE.2009.9（公開複本：https://ccy05327.github.io/SDD/08-PDF/Easy%20Approach%20to%20Requirements%20Syntax%20(EARS).pdf）
   - 讀什麼：第 2 節「Problems with Natural Language Requirements」，這就是需求八大病的原始整理，也是本練習「找矛盾」的理論基礎。
   - 和本練習的關聯：你在任務五找到的每一個矛盾，幾乎都能對應到這八大病中的某一條（歧義、模糊、遺漏、不可測試……）。

3. **INVEST in Good Stories, and SMART Tasks** — Bill Wake
   - 網址：https://xp123.com/invest-in-good-stories-and-smart-tasks/
   - 讀什麼：這篇文章本身就是完整的 INVEST 定義，一篇讀完。特別看 Testable 那一段——Wake 說「Testable 隱含了一個承諾：我對我想要的東西了解到足以寫測試的程度」，這正是本練習任務一的驗收標準。
   - 和本練習的關聯：你在任務一寫的每一條故事，都應該通過這裡的六個問題。

4. **Introducing BDD** — Dan North
   - 網址：https://dannorth.net/blog/introducing-bdd/
   - 讀什麼：看「Acceptance Criteria Should Be Executable」這一節，以及 ATM 現金提領的完整 Given-When-Then 範例——這是你任務二驗收條件的範本來源。
   - 和本練習的關聯：任務二的 unhappy path 場景（超商逾期、網路逾時）正是 North 所說的「當你寫 Given-When-Then 時，你被迫思考所有會出錯的地方」。

5. **No Silver Bullet — Essence and Accident in Software Engineering** — Frederick P. Brooks Jr.（1986）
   - 網址：https://www.cin.ufpe.br/~phmb/ip/MaterialDeEnsino/BrooksNoSilverBullet.html
   - 讀什麼：中段的「Essence」一節，特別是「deciding precisely what to build」那段。五分鐘就能讀完相關部分。
   - 和本練習的關聯：你在任務五發現的每一個矛盾，都是 Brooks 所說的「essence difficulty」——不是工具不好、不是人不夠努力，而是「要建什麼」本身就很難說清楚。

6. **ISO/IEC 25010:2023 — 軟體產品品質模型**
   - 網址：https://www.iso.org/standard/78176.html（摘要），https://iso25000.com/index.php/en/iso-25000-standards/iso-25010 有免費的特性說明
   - 讀什麼：九大特性的定義和子特性，特別確認 2023 版和 2011 版的差異（Usability → Interaction Capability；新增 Safety；新增 Flexibility）。
   - 和本練習的關聯：任務四的分類依據。版本注意：ISO/IEC 25010 的特性名稱在 2023 年更新（Edition 2），如果你看到的資料還在用 2011 版的命名，可能和本練習的答案有出入。

---

這個練習讓你親身體驗了「模糊需求有多貴」。九條看起來合理的需求，藏著六個以上的矛盾，每一個都可能在 SDLC 的不同階段引爆返工。下一章會從自然語言的本質出發，系統整理這些問題的成因。

→ [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)
