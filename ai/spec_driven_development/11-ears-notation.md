# Ch 11 — EARS 深入：五種句型馴服英文

> **目標**：徹底掌握 EARS（Easy Approach to Requirements Syntax）的五種核心句型與 complex 組合，能夠辨認每種句型對應的時態邏輯語義，並直接在 Kiro 的 `requirements.md` 裡寫出機器可解析、人也讀得懂的需求。

> 如果你對「為什麼自然語言需求這麼容易出錯」還不熟，先回看 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)。
> 如果你對 Given-When-Then 與 BDD 還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

---

## 需求的「語法病」和一個飛機引擎的解法

2009 年，Rolls-Royce 的工程師 Alistair Mavin 帶著同事 Philip Wilkinson、Adrian Harwood、Mark Novak 在分析一套噴射引擎飛行控制系統的適航性法規時，發現了一個讓每位需求工程師都頭痛的老問題：明明大家說的是英文，讀出來卻是十幾個不同的意思。

他們把問題系統化後，整理出自然語言需求的八種反覆出現的缺陷：**歧義**（ambiguity，一個詞有多個有效解讀）、**模糊**（vagueness，缺乏精確度或細節）、**複雜**（complexity，子句互相糾纏）、**遺漏**（omission，漏掉不想要的行為）、**重複**（duplication）、**冗詞**（wordiness）、**不當實作**（inappropriate implementation，說了 HOW 而不是 WHAT）、**不可測試**（untestability）。

他們的解法不是發明一套新的形式語言，而是問：**有沒有辦法輕輕限制英文，讓它保留可讀性，但只留下那幾種有效的句子結構？**

答案就是 EARS（Easy Approach to Requirements Syntax），在 RE'09 會議發表（Mavin A., Wilkinson P., Harwood A., Novak M., "Easy Approach to Requirements Syntax (EARS)", Proceedings of the 2009 17th IEEE International Requirements Engineering Conference, pp. 317-322, DOI 10.1109/RE.2009.9）。

十年後的 2019 年，Mavin 和 Wilkinson 回顧這十年（"Ten Years of EARS", IEEE Software, vol. 36(5):10-14, DOI 10.1109/MS.2019.2921164）：Airbus、Bosch、Dyson、Honeywell、Intel、NASA、Siemens 都採用了 EARS，而他們最大的感想是——**EARS 的簡單就是它最大的力量**。

2025 年，EARS 被 AWS Kiro 直接內建進 `requirements.md` 的格式規範裡。從飛機引擎到 AI 編碼代理，EARS 跨越了十六年還在擴散，原因只有一個：它解決的問題從未消失。

---

## 心智圖像：EARS 是一個填空題框架

把 EARS 想成一份「需求填空題」。英文需求句有五個可選的語法槽：

```
[ While <前置狀態> ]  [ When <觸發事件> ]
    the <系統名稱>  shall  <系統回應>
[ Where <可選功能> ]
[ If <不想要的觸發>, then ]
```

規則很嚴格：
- `shall` 永遠標記回應，是唯一強制的動詞
- 五個槽各對應一種句型
- 可以組合（complex 模式），但組合有一個上限

下面這張圖把五種句型和關鍵字對應起來：

```
句型                 關鍵字         適用場景
─────────────────────────────────────────────────────
Ubiquitous          (無)           永遠成立的限制或能力
State-driven        While          系統處於某狀態時持續行為
Event-driven        When           偵測到事件時的一次性回應
Optional-feature    Where          只有當某功能存在時
Unwanted-behaviour  If ... then    錯誤或不想要的輸入
Complex             While + When   狀態與事件同時成立
─────────────────────────────────────────────────────
```

記憶口訣：**沒有修飾 → Ubiquitous；While 開頭 → 狀態；When 開頭 → 事件；Where 開頭 → 功能選配；If...then → 錯誤處理；While + When → Complex**。

---

## 正式定義：泛型模板與五種句型

EARS 的泛型模板（generic template）是：

```
While <前置狀態>, when <觸發>, the <系統名稱> shall <系統回應>.
```

這個模板包含了所有可能的槽；每種句型只使用其中一部分。

### 句型一：Ubiquitous（通用型）

**結構**：`The <system> shall <response>.`

沒有任何前置條件或觸發器。描述的是系統**永遠**必須滿足的不變式（invariant）或能力。

```
# 需求範例（電商後台）
The product catalogue service shall return search results within 500 ms
for any query against an index of up to 1,000,000 SKUs.
```

```
# 需求範例（手機硬體，Mavin 原文風格）
The mobile phone shall have a mass of less than 200 grams.
```

**何時使用**：描述跨越所有狀態都成立的約束——效能上限、重量、法規要求、安全不變式。

**常見陷阱**：把 Ubiquitous 寫成帶有隱含條件的句子。例如「系統應能在 500 ms 內回應」——在什麼負載下？這已經需要 While 或 When。

---

### 句型二：State-driven（狀態驅動型）

**結構**：`While <precondition state>, the <system> shall <response>.`

系統**進入某狀態後**，只要維持在該狀態中，回應就**持續成立**。這不是一次性的動作，而是一個持續的義務。

```
# 需求範例（ATM，Mavin 原文風格）
While there is no card in the ATM, the ATM shall display
"Insert card to begin".
```

```
# 需求範例（電商購物車）
While the user's cart is empty, the checkout service shall
display the "Your cart is empty" message and disable the
"Proceed to checkout" button.
```

**底層語義**：State-driven 對應到時態邏輯（Temporal Logic）裡的「**G** (在狀態 S 期間，P 恆為真)」——Globally in state S, property P holds。這也是為什麼 Mavin 在推導 EARS 時說他觀察到好需求本身就帶有時態邏輯結構。

**何時使用**：描述當系統處於某個模式或狀態時的持續行為——離線模式、登入狀態、維護視窗、購物車非空等。

---

### 句型三：Event-driven（事件驅動型）

**結構**：`When <trigger event>, the <system> shall <response>.`

偵測到特定事件後，系統執行**一次性**的回應動作。

```
# 需求範例（靜音按鈕，Mavin 原文風格）
When "Mute" is selected, the laptop shall suppress all audio output.
```

```
# 需求範例（電商下單）
When the user clicks "Confirm Order" and all required fields are
valid, the order service shall create an order record, deduct
inventory, and send an order confirmation email to the user.
```

**底層語義**：Event-driven 對應「**G** (事件 E 發生 → **X** 回應 R 在下一個狀態成立)」。觸發是邊緣（edge），不是電位（level）——這和 State-driven 的本質差異。

**與 Given-When-Then 的關係**：BDD 的 `When` 和 EARS 的 `When` 在概念上一致，但 EARS 去掉了 `Given` 的顯式位置（前置狀態進 `While`），也去掉了 `Then` 的關鍵字（直接 `shall`）。兩者可以互相對應，不互斥。

```
# 同一需求的 Given-When-Then 版本
Given the cart has at least one item and payment details are filled in
When the user clicks "Confirm Order"
Then the order service shall create an order record
And shall deduct inventory
And shall send a confirmation email
```

> 如果你對 Given-When-Then 還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

---

### 句型四：Optional-feature（可選功能型）

**結構**：`Where <feature is included>, the <system> shall <response>.`

描述**只有在特定硬體功能、授權功能或組態選項存在時**才需要滿足的需求。

```
# 需求範例（汽車天窗，Mavin 原文風格）
Where the car has a sunroof, the car shall have a sunroof
control panel on the driver door.
```

```
# 需求範例（電商—進階分析功能）
Where the merchant has subscribed to the Analytics Pro add-on,
the dashboard service shall provide a daily sales trend chart
with a 90-day lookback window.
```

**為什麼不用 If**：`If` 保留給不想要的行為（unwanted behaviour，下一句型）。`Where` 標記的是**功能是否存在**，而不是**功能執行時發生了錯誤**。這是 EARS 最容易混淆的一對。

**典型使用場景**：
- 產品變體（旗艦版 vs 基本版）
- 硬體選配（GPS 模組、指紋感測器）
- SaaS 授權層（Free vs Pro vs Enterprise 功能）

---

### 句型五：Unwanted-behaviour（非預期行為型）

**結構**：`If <unwanted condition/trigger>, then the <system> shall <response>.`

描述**錯誤輸入、故障、邊界情境**下系統必須做的事。這是最常被遺漏的需求類別，Mavin 特別強調 omission（遺漏）就是 EARS 要解決的核心問題之一。

```
# 需求範例（信用卡，Mavin 原文風格）
If an invalid credit card number is entered, then the website
shall display "Please re-enter credit card details".
```

```
# 需求範例（電商下單）
If the inventory service is unavailable when the order service
attempts to deduct stock, then the order service shall respond
with HTTP 503 and shall not create an order record.
```

```
# 需求範例（登入）
If the user enters an incorrect password three consecutive times,
then the authentication service shall lock the account for 15 minutes
and shall send a security notification email to the registered address.
```

**認識論提醒**：「15 分鐘鎖定」這個數字不是魔術——它應來自安全策略文件或威脅模型，不應直接硬編在需求句裡而不說明來源。在實際需求裡應參照對應的安全策略（例如：`as defined in Security Policy SP-AUTH-002`）。這是 EARS 「不說 HOW」的原則——不要把鎖定機制的實作細節混入需求。

---

### 組合模式：Complex（複合型）

**結構**：`While <precondition state>, when <trigger event>, the <system> shall <response>.`

同時有狀態前提和事件觸發。這是 EARS 最強大也最需要小心的句型。

```
# 需求範例（飛機反推力，Mavin 原文風格）
While the aircraft is on the ground, when reverse thrust is
commanded, the engine control system shall enable reverse thrust.
```

```
# 需求範例（電商—限時促銷）
While a flash sale event is active, when a user adds a discounted
product to their cart, the pricing service shall apply the flash
sale price and shall display a countdown timer showing the
remaining sale duration.
```

**複合的邊界**：EARS 規範允許多個 `While` 子句疊加（多個前置狀態），但**只允許一個 `When` 觸發**。每個需求句應只描述一個系統回應；若回應有多個步驟，可用多個 `shall` 子句（如上例），但若步驟間有因果依賴，考慮拆成多句。

```
# 多重 While 的合法範例
While the aircraft is on the ground
and while the landing gear is deployed,
when touchdown is detected,
the braking system shall apply maximum braking force.
```

---

## EARS 在 Kiro 裡的實際樣貌

AWS Kiro 把 EARS 直接內建在 `requirements.md` 的格式要求裡（kiro.dev/docs/specs/feature-specs/，查證日期 2026-06-30）。Kiro 的主力模式是：

```
WHEN [condition/event] THE SYSTEM SHALL [expected behavior]
```

這對應 EARS 的 Event-driven 句型，但大寫關鍵字。Kiro 文件給的逐字範例是：

```
WHEN a user submits a form with invalid data
THE SYSTEM SHALL display validation errors next to the relevant fields
```

對於 bugfix spec，Kiro 的回歸保護句型是：

```
WHEN [condition] THEN the system SHALL CONTINUE TO [existing behavior]
```

**注意**：Kiro 的文件目前（查證日期 2026-06-30）主力展示的是 Event-driven 句型；其他四種 EARS 句型（While/Where/If...then/Ubiquitous）是否在 Kiro 的 requirements.md 產生器裡有明確的模板支援，尚未在 Kiro 官方文件找到逐字確認。Kiro 對 EARS 的採用以 WHEN/THE SYSTEM SHALL 為核心，其餘句型請參照 Mavin 的原始 EARS 規格補充。

### 實際的 Kiro requirements.md 片段

以一個電商「商品搜尋」功能為例，展示 Kiro 工作流程中會產出的 `requirements.md` 樣本：

```markdown
# Requirements

## User Stories

### Product Search

**As a** shopper
**I want to** search for products by keyword
**So that** I can find what I need without browsing all categories

### Acceptance Criteria

1. WHEN the user enters a keyword in the search box and submits the query
   THE SYSTEM SHALL return a list of matching products within 500 ms
   for a catalogue of up to 1,000,000 SKUs.

2. WHEN the search query returns zero results
   THE SYSTEM SHALL display a "No products found" message and SHALL
   suggest up to 5 related search terms.

3. WHEN the user submits an empty search query
   THE SYSTEM SHALL display an inline validation message:
   "Please enter at least one character to search."

4. WHEN the inventory service is unavailable during a search request
   THE SYSTEM SHALL return cached results from the last successful
   index update and SHALL display a "Results may not reflect current
   availability" notice.
```

這段 `requirements.md` 把 EARS 的 Event-driven、Unwanted-behaviour 語義都覆蓋到了，但使用 Kiro 的 WHEN/THE SYSTEM SHALL 慣例書寫。如果你想加上 State-driven 需求：

```markdown
5. While search is in progress, the search service shall display
   a loading indicator and shall disable the search submit button
   to prevent duplicate requests.
```

---

## 底層機制：為什麼 EARS 能消除歧義

回到 Ch 8 整理的八種自然語言病。EARS 對每一種的處理方式：

| 自然語言病         | EARS 的對策                                                                 |
|--------------------|-----------------------------------------------------------------------------|
| 歧義               | 關鍵字（While/When/Where/If）強制鎖定語義槽，讀者不需要猜                  |
| 模糊               | `shall` 是強制用語，排除「應該」「可能」「會」等模糊情態動詞                |
| 複雜               | 每句只有一個觸發、一個回應群；複雜邏輯必須拆多句                            |
| 遺漏               | `If...then` 句型逼開發者**顯式列出**不想要的行為，不讓錯誤情境成為黑洞     |
| 重複               | 句型結構一致，重複內容一看就出現（自動觸發 review）                         |
| 冗詞               | 模板沒有空間給裝飾性語言；你只能填槽                                        |
| 不當實作           | 槽的語義是「狀態/事件/回應」，不是「呼叫哪個 API 或走哪個 if」              |
| 不可測試           | 每個句子都能直接對應一個測試案例（給定狀態+觸發 → 驗證回應）               |

**LLM 代理的特殊脆弱性**：人類讀者面對歧義時能用共同背景知識填空（有時候對，有時候錯）。LLM 代理在解讀需求時也會「填空」，但填空的依據是訓練分布，和你的真實意圖可能完全無關。EARS 的強制句型讓代理的解讀空間大幅縮小——`While X, when Y, shall Z` 在語法上已經確定了 X 是狀態、Y 是邊緣觸發、Z 是即時回應，代理不需要推測是「狀態進入時做一次」還是「狀態維持中持續做」。

---

## 對比取捨：EARS vs 替代方案

| 維度             | EARS                               | 純自然語言需求               | Given-When-Then (Gherkin)         | 形式化規格（TLA+）              |
|------------------|------------------------------------|------------------------------|-----------------------------------|---------------------------------|
| 學習門檻         | 低（五個關鍵字）                    | 無（但問題在這）              | 低-中（Gherkin 有固定格式）        | 高（需要時態邏輯/集合論）        |
| 模糊消除         | 中高                               | 低                            | 中高                              | 極高                            |
| 可執行性         | 不直接可執行                        | 不可執行                      | 可執行（Cucumber 跑 .feature 檔） | 可機器驗證（TLC model checker） |
| 與 LLM 相容      | 高（結構化但仍是英文）              | 中（LLM 能讀但會亂猜）        | 高                                | 低（LLM 不擅長 TLA+ 語義）      |
| 適合文件化       | 高                                 | 高（直覺，但有品質風險）      | 中（偏測試，不偏文件）            | 低（只有工程師能讀）            |
| 覆蓋錯誤路徑     | 強制（If...then 專屬句型）          | 取決於作者習慣               | 需要 Scenario Outline 才完整       | 最強                            |
| 工業採用         | Airbus/Bosch/NASA/Kiro             | 普遍但品質不穩               | 敏捷團隊廣泛採用                   | AWS/Intel（高價值系統）         |

**為什麼不直接用 Given-When-Then 就好**：BDD 的 Given-When-Then 設計為「可執行」——它最終對應到 Cucumber step definitions。EARS 設計為「需求文件」——它是對系統的義務聲明，不預設有框架跑它。兩者可以並存：EARS 寫需求義務，Given-When-Then 寫驗收測試。Kiro 的 `requirements.md` 正是這樣設計的——EARS 句型表達需求，同一份文件旁邊可以有 acceptance criteria 的 Given-When-Then 測試案例。

**為什麼不從一開始就用 TLA+**：形式化規格能找到 EARS 找不到的深層設計問題（例如分散式系統的 liveness violation），但代價是全隊都必須讀得懂 TLA+。EARS 是「夠用且人人能用」的權衡點。

> 如果你對形式化規格感興趣，Ch 13 會深入 TLA+ 和 Alloy：[Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)。

---

## 踩雷集錦

### 踩雷一：把 While 和 When 搞反

**錯誤直覺**：「使用者登入後系統就顯示儀表板，這不就是一個事件觸發的行為嗎？用 `When` 就好。」

**正確認識**：登入後「顯示儀表板」可能有兩層語義：（1）登入事件觸發一次性導航動作（Event-driven）；（2）在「已登入」狀態期間，系統持續維持儀表板可見（State-driven）。這是兩個不同的需求，必須分清楚再寫，否則測試案例無從設計。

```
# 錯誤：混淆了事件和狀態
When the user is logged in, the dashboard service shall display
the user's dashboard.

# 正確拆開：
# 事件：登入觸發導航
When the user successfully authenticates, the session service
shall redirect the user to their personalised dashboard.

# 狀態：在登入期間保護資源
While the user session is active, the dashboard service shall
serve dashboard content only to the authenticated user.
```

---

### 踩雷二：把 Where 和 If 搞反

**錯誤直覺**：「如果有 GPS 模組，系統就顯示地圖，這不就是 `If` 嗎？」

**正確認識**：`If` 保留給**不想要的/錯誤的**觸發情境；**功能是否存在**應該用 `Where`。`If the device has a GPS module` 暗示「有 GPS 是一種不想要的狀況」，語義錯誤。

```
# 錯誤：
If the device has a GPS module, then the navigation app shall
display real-time location on the map.

# 正確：
Where the device includes a GPS module, the navigation app shall
display the user's real-time location on the map.

# If 的正確用途：
If the GPS signal is lost for more than 10 seconds, then the
navigation app shall display "GPS signal lost" and shall switch
to dead-reckoning mode.
```

---

### 踩雷三：一句需求塞了兩個觸發

**錯誤直覺**：「把相關情境寫在一起比較完整，省得分開看。」

**正確認識**：每個 EARS 句子只能有一個觸發（`When` 子句）。兩個觸發代表兩個不同的行為義務，應拆成兩句。混在一起的後果是測試設計困難（哪個觸發測哪個回應？）以及 LLM 解析時可能只處理其中一個。

```
# 錯誤：兩個觸發
When the user clicks "Save" or when the auto-save timer fires,
the document service shall persist the current document state.

# 正確：拆成兩句
When the user clicks "Save", the document service shall persist
the current document state and shall display a "Saved" confirmation.

When the auto-save timer fires every 60 seconds, the document
service shall persist the current document state silently without
displaying a confirmation.
```

注意：拆開後還能加入差異（第一句有確認訊息，第二句靜默）——這是從前一個模糊需求裡挖出來的隱含規格，值得討論。

---

### 踩雷四：Ubiquitous 包含了隱藏的前提條件

**錯誤直覺**：「這個效能需求永遠都要成立，所以直接寫 Ubiquitous。」

**正確認識**：「500 ms 以內回應」這種效能需求幾乎不可能在所有條件下都成立（網路斷線、資料庫備份中……）。真正的 Ubiquitous 需求極少——通常是物理規格（質量、尺寸）或安全不變式。效能需求應該加上操作條件（`While`）或正常操作範圍聲明。

```
# 過度廣泛的 Ubiquitous：
The search service shall return results within 500 ms.

# 加入操作前提的 State-driven：
While the search index contains fewer than 1,000,000 products and
concurrent query load is below 1,000 requests per second,
the search service shall return results within 500 ms
at the 95th percentile.
```

---

### 踩雷五：遺漏 Unwanted-behaviour 導致黑洞需求

**錯誤直覺**：「正常流程寫清楚就好，錯誤情況開發者自己判斷。」

**正確認識**：這正是 Mavin 說的「omission」病——不想要的行為是最常被遺漏的需求，卻往往是最重要的。LLM 代理在 omission 的情況下會選擇「看起來合理」的預設行為，這個預設可能完全不符合你的業務邏輯（例如：庫存扣款失敗時，預設繼續建立訂單——對某些系統是合理的，對某些系統是災難）。

```
# 遺漏 unwanted-behaviour 的需求集合（有黑洞）：
When the user clicks "Pay", the payment service shall charge
the user's card and shall create an order record.

# 補全 unwanted-behaviour：
When the user clicks "Pay", the payment service shall charge
the user's card and shall create an order record.

If the payment gateway returns a decline response, then the
payment service shall NOT create an order record and shall
display the decline reason to the user.

If the payment gateway is unreachable within 5 seconds, then
the payment service shall return HTTP 504 and shall NOT create
an order record or charge the user.
```

---

## 進階延伸：EARS 的邊界與延伸

### EARS + 測試矩陣：系統化覆蓋邊界

EARS 句型可以直接對應測試結構。對每一個 EARS 句子，你可以機械式地產生測試案例：

```
EARS 句型           測試軸
─────────────────────────────────────────────
Ubiquitous          邊界值 + 邊緣輸入
State-driven        狀態進入 / 狀態維持 / 狀態離開
Event-driven        事件正常發生 / 事件缺失 / 事件重複
Optional-feature    功能存在時 / 功能不存在時
Unwanted-behaviour  觸發不想要條件 / 剛好未達觸發閾值
Complex             狀態+事件組合 / 只有狀態 / 只有事件
```

這個矩陣可以交給 Kiro 的 tasks.md 生成階段直接使用，讓代理產生對應的測試任務。

### EARS 的 "8 Lessons Learned"

2016 年 Mavin 和共同作者在 RE 2016 發表了 "8 Lessons Learned Applying EARS"，整理十年實戰後的心得。其中最值得記住的幾點：

1. **不要試圖把所有需求塞進一個句子**：複雜條件應拆分，保持每句可獨立測試。
2. **EARS 不是替代需求討論的工具，而是記錄討論結果的格式**：寫 EARS 之前的對話同樣重要。
3. **可選功能（Where）最容易被工程師忽略**：因為工程師傾向假設「基本配置」，沒有顯式標注的功能差異就成了隱形炸彈。

### EARS 和 IEEE 830 的差異

IEEE Std 830（Software Requirements Specifications，SRS 標準）給出了需求的品質屬性（正確、完整、一致……），但沒有給句子層次的句法規範。EARS 補的正是這一層：在 IEEE 830 的品質框架下，提供一套達成「無歧義」的句法工具。兩者不衝突，很多組織在 IEEE 830 格式的 SRS 文件裡用 EARS 句型書寫每條需求。

---

## 動手練習

以下練習都用「線上考試系統」這個情境，每題要求你寫出符合 EARS 格式的需求句：

**練習 1 — Ubiquitous**
系統必須支援同時在線最多 10,000 名考生。請寫出 Ubiquitous 需求，並確認這個數字來源應被參照（提示：加上「as defined in Capacity Planning Document CP-001」之類的來源指引）。

**練習 2 — State-driven**
當考試進行中（in_progress 狀態），計時器必須持續顯示剩餘時間。試著寫出一個 State-driven 需求，包含剩餘時間更新的頻率。

**練習 3 — Event-driven**
考生點擊「提交試卷」時系統該做什麼。試著列出至少三個需要同時發生的動作（提交答案、記錄時間戳、發送確認）。

**練習 4 — Optional-feature**
只有高階授權學校才有的「防作弊螢幕監控」功能。試用 `Where` 句型寫出這個需求。

**練習 5 — Unwanted-behaviour**
網路在答題過程中斷線（超過 30 秒無回應）時系統必須做什麼。試著寫出不少於兩條 `If...then` 需求（一條保護考生的答題進度，一條通知考生）。

**練習 6 — Complex**
只有在「監考員已啟動本場考試」狀態下，考生點擊「開始考試」才能進入題目作答介面。試用 While + When 句型表達。

**對比練習**：把練習 3 的需求改寫成 Given-When-Then 格式，對比兩種格式在文字量、可執行性、可讀性上的差異。試著回答：哪種更適合放在 Kiro 的 `requirements.md`？哪種更適合放在 Gherkin `.feature` 檔裡讓 Cucumber 執行？

---

## 本章重點整理

- EARS 由 Alistair Mavin 等人於 Rolls-Royce 開發，首發於 IEEE RE'09（2009），解決自然語言需求的八種反覆出現的缺陷。
- 五種核心句型：Ubiquitous（無關鍵字）、State-driven（While）、Event-driven（When）、Optional-feature（Where）、Unwanted-behaviour（If...then），加上 Complex（While+When）組合。
- `shall` 是唯一強制動詞，標記系統義務；其他情態動詞（should/may/might）在 EARS 裡無效。
- State-driven（While）描述持續義務；Event-driven（When）描述一次性回應——這是最重要的語義分界。
- `Where` 用於功能選配（feature exists），`If...then` 用於錯誤/不想要情境——這兩者的混用是最常見錯誤。
- Kiro 的 `requirements.md` 採用 EARS 的 WHEN/THE SYSTEM SHALL 形式作為主力句型（查證日期 2026-06-30）。
- EARS 不是替代需求討論的工具，而是記錄討論結果、消除書面歧義的句法格式；LLM 代理對歧義格外脆弱，這是 2025 年 EARS 重獲重視的主因。

---

## 自我檢核

請用自己的話（不翻書）回答以下問題：

- [ ] 用一句話解釋 EARS 為什麼要設計成「填空題」而不是「自由書寫」。
- [ ] 如果面試官問你「State-driven 和 Event-driven 的本質差異是什麼」，你會怎麼回答？（提示：邊緣 vs 電位，一次性 vs 持續）
- [ ] `Where` 和 `If` 分別用在什麼場景？舉一個你自己業務域裡的例子。
- [ ] 說明 EARS 如何讓 LLM 代理（例如 Kiro）比面對純自然語言需求時更可靠地實作功能。
- [ ] Complex 句型的上限是什麼？（可以有幾個 `While`？幾個 `When`？）
- [ ] 如果你拿到一份沒有 `If...then` 需求的 EARS 需求集，你的第一反應應該是什麼？

---

## 延伸閱讀

**1. EARS 官方指南（Alistair Mavin）**
— https://alistairmavin.com/ears/
— 讀哪裡：「EARS Patterns」區，看五種句型的泛型模板與逐句範例（ATM、手機、汽車天窗、反推力引擎）。
— 與本章的關聯：這是 EARS 的第一手資料，比任何教科書或部落格都更精確；本章所有句型定義都源自此處。

**2. 原始 RE'09 論文（DOI: 10.1109/RE.2009.9）**
— https://dl.acm.org/doi/10.1109/RE.2009.9
— 公開 PDF 鏡像：https://ccy05327.github.io/SDD/08-PDF/Easy%20Approach%20to%20Requirements%20Syntax%20(EARS).pdf
— 讀哪裡：Section 2（八種自然語言問題的定義）和 Section 3（五種句型的推導過程）。
— 與本章的關聯：你能看到 Mavin 是如何從實際工程缺陷**歸納**出句型，而不是從理論**演繹**下來的——這個方向對理解 EARS 的設計意圖至關重要。

**3. Ten Years of EARS（IEEE Software, 2019）**
— https://dl.acm.org/doi/10.1109/MS.2019.2921164
— 讀哪裡：工業採用案例（Airbus、NASA 等）和「簡單性是最大優點」的論證。
— 與本章的關聯：回答「為什麼不加更多句型讓 EARS 更強大」這個問題——設計者自己的答案。

**4. Kiro Feature Specs 文件**
— https://kiro.dev/docs/specs/feature-specs/
— 讀哪裡：EARS 格式的逐字範例（`WHEN a user submits a form with invalid data THE SYSTEM SHALL...`）和 Requirements-First vs Design-First 兩種工作流程。
— 與本章的關聯：看 EARS 在現代 AI IDE 中的實際落地形式（查證日期 2026-06-30，版本相依）。

**5. Spec-driven development: Unpacking 2025's key new AI-assisted engineering practices（Thoughtworks，Liu Shangqi，2025-12-04）**
— https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
— 讀哪裡：「為什麼結構化需求能降低 LLM 幻覺」這一段，以及 SDD 工具全景（Kiro、Spec Kit）。
— 與本章的關聯：把 EARS 的「消除歧義」功能連接到 LLM 代理的實際工程挑戰，是本章「底層機制」一節的理論背景。

**6. Writing Effective Use Cases（Alistair Cockburn，2000）**
— Addison-Wesley，ISBN 978-0201702255
— 讀哪裡：第三章（Use Case 格式與欄位）和附錄 A（需求品質討論），對照 EARS 的精簡哲學。
— 與本章的關聯：Use Case 和 EARS 是兩種不同粒度的需求表達——Use Case 描述完整互動流程，EARS 描述單一義務句。兩者在 [Ch 12 Use Case 與非功能需求](./12-use-cases-nfr.md) 會放在一起對比。

---

下一章把 EARS 擴展到更大的圖景：Use Case 如何把多條 EARS 需求串成完整的互動流程，以及非功能需求（ISO/IEC 25010）怎麼用 EARS 表達效能、安全、可靠性義務。

→ [Ch 12 Use Case 與非功能需求](./12-use-cases-nfr.md)
