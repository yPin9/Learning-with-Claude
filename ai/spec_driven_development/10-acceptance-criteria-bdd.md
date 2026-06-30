# Ch 10 — 從驗收條件到 BDD：Given-When-Then

> **目標**：理解 Dan North 在 2006 年把「測試」改稱「行為」這個轉向的深意；掌握驗收條件（Acceptance Criteria）和 Gherkin/Cucumber 的 Given-When-Then 語法；能判斷什麼時候用 BDD 場景、什麼時候不值得。

## 從一個讓人抓狂的測試名稱說起

2003 年，Dan North 在教一群 Java 開發者寫 JUnit 測試時，有人問：「第一個測試該叫什麼名字？」

這看似小事，卻折磨了整個房間的人。`testDeposit`？`testBalance`？`test1`？

North 後來寫道，他意識到問題出在「測試」這個詞本身。「測試」（test）暗示你已經有某個東西要驗證——但在 TDD 的 Red-Green-Refactor 循環中，測試是在程式碼之前寫的，那時根本沒什麼可測。這個概念錯位讓人不知道該從哪裡下手。

解法很根本：把「測試」改成「行為」（behaviour）。

測試名稱從 `testWithdrawSufficientFunds` 變成 `should_allow_withdrawal_when_account_has_sufficient_funds`。這不只是換個詞，而是把整個思維框架倒過來：**你不是在驗證實作，你是在描述系統應有的行為**。

這就是行為驅動開發（Behaviour-Driven Development，BDD）的起點，Dan North 在 *Better Software* 雜誌 2006 年 3 月號正式發表了這個想法。

## BDD 之前，人們怎麼寫驗收條件

在 BDD 出現之前，驗收條件通常長這樣：

```
功能：提款
- 當帳戶餘額足夠時，允許提款
- 當帳戶餘額不足時，拒絕提款並顯示錯誤
- 當帳戶被凍結時，拒絕提款
```

這是 checklist 風格。它讀起來像需求，但：

1. 「足夠」是多少？是等於？還是嚴格大於？包含手續費嗎？
2. 「拒絕」之後要怎樣？頁面停在哪裡？餘額變嗎？
3. 「顯示錯誤」是哪種錯誤？錯誤碼？中英文？彈窗還是 inline？

換三個開發者來讀，可能得到三種實作。這就是自然語言的模糊性問題——我們在 [Ch 8 — 為什麼需求這麼難](./08-why-requirements-hard.md) 裡細數了八種病症。

BDD 的 Given-When-Then 格式不是要「更正式」，而是要**把隱藏的假設強迫顯現出來**。

## Given-When-Then：把驗收條件機械化

North 提出的格式非常直白：

```
Given（前置情境）：系統處於某個狀態
When（觸發事件）：某件事發生了
Then（預期結果）：確保某些結果成立
```

把上面的提款需求改寫：

```gherkin
Scenario: 帳戶餘額足夠時成功提款
  Given 帳戶餘額為 1000 元
    And 提款機有足夠現金
  When 客戶要求提款 200 元
  Then 帳戶餘額應為 800 元
    And 現金應被取出
    And 提款卡應被歸還
```

對比一下差異：

| 舊的 checklist 風格 | Given-When-Then 風格 |
|---|---|
| 「餘額足夠」 | 具體初始金額 1000 元 |
| 「允許提款」 | 餘額變為 800 元、現金取出、卡歸還 |
| 隱含「現金機有錢」 | 明確寫在 Given |
| 單一條件 | 多個 And 分拆複合結果 |

North 在他的文章裡特別提到，他和 Chris Matts 在設計這套格式時，意識到他們其實在建立一套「分析過程本身的通用語言（Ubiquitous Language）」——這個詞直接借自 Eric Evans 的領域驅動設計（Domain-Driven Design，DDD）。

> 如果你對通用語言的概念還不熟，先回看 [Ch 15 — 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)。

## Gherkin：把 Given-When-Then 變成可執行的格式

Given-When-Then 是一個概念框架。Gherkin 是實現它的**結構化語言**，由 Aslak Hellesoy 創建，被 Cucumber 工具用來執行測試。

一個完整的 `.feature` 檔長這樣：

```gherkin
Feature: ATM 現金提款

  Background:
    Given 提款機已開機且有網路連線

  Scenario: 帳戶有足夠餘額時成功提款
    Given 帳戶餘額為 1000 元
      And 卡片有效
      And 提款機有現金
    When 客戶要求提款 200 元
    Then 帳戶應被扣款 200 元
      And 現金應被取出
      And 卡片應被歸還

  Scenario: 帳戶餘額不足時拒絕提款
    Given 帳戶餘額為 100 元
    When 客戶要求提款 200 元
    Then 應顯示「餘額不足」的訊息
      And 帳戶餘額應仍為 100 元
      And 提款機不應出鈔

  Scenario Outline: 不同金額的提款
    Given 帳戶餘額為 <initial_balance> 元
    When 客戶要求提款 <withdrawal_amount> 元
    Then 帳戶餘額應為 <final_balance> 元

    Examples:
      | initial_balance | withdrawal_amount | final_balance |
      | 1000            | 200               | 800           |
      | 500             | 500               | 0             |
      | 300             | 100               | 200           |
```

Gherkin 的關鍵字：

| 關鍵字 | 作用 |
|---|---|
| `Feature` | 功能模組標題，說明這份檔案涵蓋什麼 |
| `Background` | 每個 Scenario 共用的前置步驟 |
| `Scenario` / `Example` | 一個具體的行為場景（同義詞） |
| `Given` | 前置情境（系統在什麼狀態） |
| `When` | 觸發的行動或事件 |
| `Then` | 預期的可觀察結果 |
| `And` / `But` | 接續同類步驟，保持可讀性 |
| `Scenario Outline` | 參數化場景，搭配 `Examples` 表格跑多筆資料 |
| `Rule`（Gherkin 6+） | 業務規則分組，包在 Feature 和 Scenario 之間 |
| `*` | 中立步驟，不強調 Given/When/Then 語意 |

Gherkin 是 **line-oriented**：每行都以關鍵字開頭，縮排只是視覺習慣，語法本身靠關鍵字驅動。

## 工具鏈：從 .feature 到可執行測試

```
product-owner 寫
   ├── requirements.md（業務需求）
   │
   ▼
BA/Dev 共同討論，寫出
   ├── atm.feature（Gherkin 場景）
   │
   ▼
   Cucumber（Ruby / Java / JavaScript 等）
   │   ├── 讀 .feature 檔
   │   ├── 根據步驟文字找對應的 step definition
   │   └── 執行測試、輸出結果
   │
   ▼
   Green / Red（通過 / 失敗）
```

Step definition（以 Python/pytest-bdd 為例）：

```python
# step_definitions/atm_steps.py
from pytest_bdd import given, when, then, parsers

@given(parsers.parse("帳戶餘額為 {balance:d} 元"))
def account_balance(balance):
    return {"balance": balance}

@when(parsers.parse("客戶要求提款 {amount:d} 元"))
def request_withdrawal(account_balance, amount):
    account_balance["withdrawal_amount"] = amount

@then(parsers.parse("帳戶應被扣款 {amount:d} 元"))
def check_debit(account_balance, amount):
    initial = account_balance["balance"]
    account_balance["balance"] -= amount
    assert account_balance["balance"] == initial - amount
```

執行：

```bash
pytest --bdd features/atm.feature
```

輸出（成功時）：

```
PASSED features/atm.feature::ATM現金提款::帳戶有足夠餘額時成功提款
PASSED features/atm.feature::ATM現金提款::帳戶餘額不足時拒絕提款
PASSED features/atm.feature::ATM現金提款::不同金額的提款[1000-200-800]
PASSED features/atm.feature::ATM現金提款::不同金額的提款[500-500-0]
PASSED features/atm.feature::ATM現金提款::不同金額的提款[300-100-200]
```

> 注意：`pytest-bdd` 的 API 在版本間有變化（如 `parsers` 模組的用法），以上範例基於 pytest-bdd 6.x；以官方文件當前版本為準。

## BDD 的歷史脈絡：JBehave → RSpec → Cucumber

```
2003  Dan North 創建 JBehave（Java 的第一個 BDD 框架）
      │
2005  Dave Astels、Steven Baker、Aslak Hellesoy、David Chelimsky 創建 RSpec
      │
2006  Dan North 發表《Introducing BDD》於 Better Software 雜誌 3 月號
      │
~2008  Aslak Hellesoy 創建 Cucumber
        （名稱是他未婚妻的建議）
        Gherkin 成為跨語言的通用場景描述格式
      │
2010s  Cucumber 生態系擴展到 Java（Cucumber-JVM）、
        JavaScript（Cucumber-js）、.NET、PHP 等
      │
2025  AWS Kiro、GitHub Spec Kit 在 AI 場景下
        借用 Given-When-Then 概念（特別是 EARS 的 WHEN...SHALL 句型）
        作為規格文件的標準格式
```

BDD 的根在 TDD，但它往「前」推了一步：**不只讓測試驅動實作，讓場景描述驅動測試**。這使得非技術的產品負責人（Product Owner）可以讀懂、甚至撰寫場景，讓測試成為活的需求文件。

## 驗收條件的位置：在 User Story 裡

> 如果你對 User Story 的三個 C（Card / Conversation / Confirmation）還不熟，先回看 [Ch 9 — User Story 與 INVEST](./09-user-stories-invest.md)。

User Story 的 **Confirmation** 就是驗收條件。INVEST 的 **T（Testable）**說的就是：一個好的 Story 要隱含「你能為它寫出測試」。

一個完整的 Story + BDD 驗收條件：

```
User Story:
  As a registered customer
  I want to withdraw cash from an ATM
  So that I can access my funds without visiting a branch

Acceptance Criteria:
  Scenario 1 — Sufficient balance
    Given 帳戶餘額 ≥ 提款金額
    When 提款請求成立
    Then 款項取出、餘額更新、卡歸還

  Scenario 2 — Insufficient balance
    Given 帳戶餘額 < 提款金額
    When 提款請求送出
    Then 拒絕、餘額不變、顯示錯誤訊息

  Scenario 3 — Frozen account
    Given 帳戶被凍結
    When 任意提款請求
    Then 一律拒絕、顯示「帳戶異常請聯繫客服」
```

**驗收條件 = Given-When-Then 場景清單**。它回答的問題不是「這個功能怎麼做」，而是「什麼算完成」。

## 對比取捨

BDD 不是唯一的驗收條件格式，也不適用於所有情況：

| 格式 | 優點 | 缺點 | 適合場景 |
|---|---|---|---|
| Checklist 清單 | 快速、彈性 | 模糊、不可執行 | 早期 Discovery 討論 |
| Given-When-Then（文字） | 結構清楚、可讀 | 仍需人工執行 | Sprint 內的驗收標準 |
| Gherkin + Cucumber | 可執行、自動化、活文件 | 維護成本高，step definition 容易腐化 | 需要長期回歸測試的核心業務流程 |
| EARS 句型 | 正式、精確、LLM 友善 | 不夠人類可讀 | 規格文件、AI Agent 輸入 |
| 形式化規格（TLA+/Alloy） | 可機器驗證 | 學習曲線高、非業務人員看不懂 | 安全關鍵、並發系統 |

BDD 的甜蜜點在中間：比 checklist 嚴謹，比形式化規格親切。但它有個軟肋——**step definition 是技術資產，會腐化**。`.feature` 檔說「帳戶應被扣款」，但 step definition 連不上正確的 domain object，就靜默地通過了錯誤的測試。

## 踩雷集錦

**錯誤直覺 1：「Given 是前置步驟，所以可以在 Given 裡做任何設定」**

正確認識：Given 應描述**系統的初始狀態**，不應包含業務邏輯或觸發。常見的錯法是把 API 呼叫或 UI 操作塞進 Given：

```gherkin
# 錯：Given 裡混入操作
Given 我打開結帳頁面
  And 我輸入信用卡號碼 4111-1111-1111-1111
  And 我點擊「確認」

# 對：Given 是狀態，When 是操作
Given 購物車有 3 件商品，總金額 900 元
  And 使用者已登入
When 使用者以信用卡 4111-1111-1111-1111 結帳
Then 訂單應被建立，狀態為「待出貨」
```

---

**錯誤直覺 2：「Scenario 越多越好，覆蓋越完整」**

正確認識：Scenario 的數量應該被商業價值驅動，而不是排列組合驅動。把所有邊界條件都寫成 Scenario 只會製造維護地獄。邊界條件測試適合放在 unit test，Given-When-Then 的定位是**關鍵業務路徑**和**重要的失敗路徑**。一個功能有 3-7 個有意義的 Scenario 是正常的；30 個通常是反模式。

---

**錯誤直覺 3：「Then 只需要寫主要結果，次要的不用管」**

正確認識：Then 必須窮舉所有**可觀察的結果**。常被遺漏的包括：資料庫狀態的變化、副作用（如發 email、觸發事件）、不應改變的狀態。ATM 例子裡「卡片應被歸還」容易被忽略，但如果不寫，測試就不會驗它。

```gherkin
# 遺漏副作用
Then 帳戶應被扣款 200 元

# 完整的 Then
Then 帳戶應被扣款 200 元
  And 提款記錄應被寫入交易日誌
  And 客戶應收到 SMS 通知
  And 卡片應被歸還
```

---

**錯誤直覺 4：「BDD 就是先寫 .feature 再寫程式，跟 TDD 一樣」**

正確認識：BDD 最大的價值不在自動化測試的順序，而在**三方對話**（Three Amigos）：業務、開發、測試坐在一起討論 Scenario，在動手寫程式前就把理解對齊。如果只是把現有的單元測試翻譯成 Gherkin，你得到的是語法，不是 BDD 的實質。

---

**錯誤直覺 5：「step definition 可以共用，越共用越好」**

正確認識：step definition 高度共用會讓每個步驟都帶著大量隱藏假設，最後一個 step 的行為取決於前面 step 設置的全域狀態。這是測試糾纏（test coupling）的溫床。推薦每個 Scenario 有獨立清晰的 step，必要時用 Background 抽取真正的共用前提。

## BDD 在 AI 開發時代的角色

2025 年，AWS Kiro 和 GitHub Spec Kit 都把結構化的行為描述作為核心輸入。Kiro 的 `requirements.md` 裡使用的是 EARS 的 `WHEN...THE SYSTEM SHALL...` 句型，而不是直接採用 Gherkin，但背後的哲學是一脈相承的：**AI 編程代理（Coding Agent）不像人類協作者，它無法在理解不清時自然地追問，而是靜默地填補空白**（Thoughtworks，2025 年 12 月 4 日）。

Given-When-Then 強迫寫作者把三件事分離：

- **狀態**（Given）：系統是什麼樣的
- **事件**（When）：什麼事情觸發了
- **結果**（Then）：什麼事情必須成真

這個三元組對 LLM 非常友善，因為它們對應到程式語言裡的：

```
Given  →  test setup / precondition
When   →  function call / action
Then   →  assertion / postcondition
```

當你把 Gherkin 場景餵給 AI 代理時，它知道要從哪裡讀入狀態、要做什麼、要驗證什麼。這比一段散文需求的指令性要強得多。

> 這個連結在 [Ch 22 — 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md) 會更深入探討。

## 進階延伸

### Specification by Example（實例化需求）

Gojko Adzic 在《Specification by Example》（2011）中把 BDD 的概念推廣成一套更完整的實踐，稱為 **ATDD（Acceptance Test-Driven Development，驗收測試驅動開發）**。核心思想：**用具體的例子代替抽象的規則**。

比較：

```
# 抽象規則（容易有歧義）
系統應拒絕無效日期

# 具體例子（消除歧義）
Given 使用者輸入出生日期 2030-01-01（未來日期）
Then 系統應顯示「出生日期不能是未來日期」
```

### Living Documentation（活文件）

當 Gherkin 場景持續被執行並通過，它就成了一份永遠和程式碼同步的文件。Cucumber 可以生成 HTML 報告，讓非技術的利害關係人（Stakeholder）看到「現在哪些行為是有效的」。這就是「活文件」的意義：不是靜態的 Wiki，而是可執行、可驗證、不會過時的規格。

### BDD + DDD 的交集

North 明確借用了 Evans 的「通用語言」概念。在 DDD 的語境裡，Given-When-Then 可以直接表達領域事件（Domain Event）：

```gherkin
Scenario: 訂單確認後觸發庫存扣減
  Given 訂單 #12345 已提交，包含商品 SKU-001 數量 2
  When 訂單確認事件（OrderConfirmed）發生
  Then 倉庫庫存系統應收到 InventoryReserved 事件
    And SKU-001 可用庫存應減少 2
```

這個場景同時是驗收條件、也是領域事件的文件。

## 動手練習

**練習 1：改寫模糊驗收條件**

把以下模糊條件改寫成 Gherkin 場景（至少兩個 Scenario，包含一個失敗路徑）：

```
功能：使用者登入
- 輸入正確的帳號密碼後，使用者可以登入系統
- 輸入錯誤時，顯示錯誤訊息
```

寫完後問自己：Given 裡是否有任何隱含假設？Then 裡是否有被遺漏的可觀察結果？

---

**練習 2：找出壞掉的 Given**

以下場景哪個步驟放錯位置了？為什麼？

```gherkin
Scenario: 購物車結帳
  Given 我打開購物車頁面
  And 我點擊「加入商品」按鈕
  And 我選擇商品 A 並確認
  When 我點擊「結帳」
  Then 訂單應被建立
```

---

**練習 3：補完 Then**

這個場景的 Then 缺了什麼可觀察的結果？

```gherkin
Scenario: 使用者取消訂單
  Given 訂單 #99 狀態為「待出貨」
    And 訂單在 24 小時以內建立
  When 使用者申請取消訂單
  Then 訂單狀態應變為「已取消」
```

（提示：付款退款了嗎？使用者會收到通知嗎？倉庫系統需要知道嗎？）

---

**練習 4：Scenario Outline**

把下面三個場景合併成一個 Scenario Outline：

```gherkin
Scenario: 免費會員每日下載上限 3 次
  Given 使用者是免費會員，今日已下載 3 次
  When 嘗試第 4 次下載
  Then 應拒絕並提示升級

Scenario: 付費會員每日下載上限 20 次
  Given 使用者是付費會員，今日已下載 20 次
  When 嘗試第 21 次下載
  Then 應拒絕並提示聯繫客服

Scenario: 免費會員在上限內可以下載
  Given 使用者是免費會員，今日已下載 2 次
  When 嘗試第 3 次下載
  Then 應允許下載
```

## 本章重點整理

1. Dan North 在 2006 年把「測試」改成「行為」，不只是換詞，是把規格描述從「驗證實作」移到「定義期望」。

2. Given-When-Then 把驗收條件結構化成三個強制分離的部分：前置狀態（Given）、觸發事件（When）、可觀察結果（Then）。

3. Gherkin 是 Given-When-Then 的可執行語言，被 Cucumber 讀取後透過 step definition 連到真實的測試程式碼。

4. `.feature` 檔 + step definition = 活文件（Living Documentation），是永遠和程式碼同步的規格。

5. BDD 的核心價值不在自動化，在於**三方對話前移**——在寫程式之前就對齊理解。

6. Scenario Outline + Examples 表格讓同一行為模式可以跑多筆資料，避免重複撰寫。

7. 在 AI 編程代理時代，Given-When-Then 的結構（狀態、事件、斷言）對 LLM 特別友善，因為它直接對應 test setup → action → assertion 的測試模式。

## 自我檢核

- [ ] 我能用自己的話解釋：為什麼 Dan North 要把「測試」改成「行為」？這個改變帶來什麼不同？
- [ ] 不看書，我能寫出一個完整的 Gherkin `.feature` 檔，包含 Feature、Background、Scenario、Scenario Outline 和 Examples。
- [ ] 我能說出 Gherkin 的至少 8 個關鍵字及其用途。
- [ ] 如果面試官問「BDD 的三方對話是什麼？為什麼重要？」，我能清楚回答。
- [ ] 我能判斷一個 Scenario 的 Given 是否混入了 When 應在的操作，並能解釋為什麼那樣不好。
- [ ] 我能解釋 BDD 的「活文件」是什麼，以及它跟靜態 Wiki 的差別在哪裡。
- [ ] 我能說出 BDD 在 AI 代理時代為何再次被重視（Kiro、Spec Kit 的語境）。

## 延伸閱讀

- **[Introducing BDD — Dan North](https://dannorth.net/blog/introducing-bdd/)**
  BDD 的原始論文，North 的 dannorth.net 重新託管版，首刊於 *Better Software* 2006 年 3 月號。從「What to call your test」和「acceptance criteria should be executable」兩節讀起。這是理解 Given-When-Then 設計意圖的第一手文獻。

- **[History of BDD — Cucumber 官方文件](https://cucumber.io/docs/bdd/history/)**
  JBehave（2003）→ RSpec（2005）→ Cucumber 的完整譜系，以及 Connextra 的 user story 格式如何餵進 BDD。理解工具演進的最短路徑。

- **[Gherkin Reference — Cucumber 官方文件](https://cucumber.io/docs/gherkin/reference/)**
  每個 Gherkin 關鍵字的正式定義、`.feature` 檔結構、Scenario Outline 的 `<placeholder>` 語法，以及 Background 的使用規則。是查語法細節的規範性文件。

- **《Specification by Example》 — Gojko Adzic（Manning，2011）**
  把 BDD 場景的實踐推廣成一套完整的 ATDD 流程：從「實例化需求」到「活文件」到「可執行規格」。特別適合想把 Given-When-Then 推廣到整個團隊的讀者。第 1-3 章解釋為何傳統驗收條件會失敗，第 5-8 章講如何設計好的 Scenario。

- **[INVEST in Good Stories, and SMART Tasks — Bill Wake](https://xp123.com/invest-in-good-stories-and-smart-tasks/)**
  INVEST 的 T（Testable）和驗收條件直接連結，2003 年的原始文章。與本章對照閱讀，理解「可測試性」在 User Story 層次的意義。

- **[Spec-driven development: Unpacking 2025's key new AI-assisted engineering practices — Liu Shangqi, Thoughtworks（2025 年 12 月 4 日）](https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices)**
  直接連結 BDD/EARS 的結構化需求描述和 2025 年 AI 代理工具的關係：為什麼 AI 無法容忍模糊（不像人類可以追問），以及結構化輸入如何減少幻覺。閱讀第三節關於「structured prompts」的論述。

下一章深入 EARS——這套來自 Rolls-Royce 的航空電子需求語法，在 AWS Kiro 的 `requirements.md` 裡扮演核心角色，也是把 Given-When-Then 精確化成單一句型的另一種思路。

→ [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)
