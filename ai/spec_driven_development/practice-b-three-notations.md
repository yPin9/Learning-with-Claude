# 練習 B — 同一功能用三種記法各寫一遍

> **目標**：把同一個功能分別用 User Story、Gherkin、EARS 三種記法寫出來，透過親手對照，理解三者在含糊度、可測性、可讀性上的真實取捨——而不是靠背定義。

---

## 背景與動機

在 Ch 9、Ch 10、Ch 11 裡，我們分別介紹了 User Story（使用者故事）、BDD/Gherkin（Given-When-Then）、EARS（Easy Approach to Requirements Syntax，簡易需求語法）。三種記法的目標都是「讓需求變得清楚」，但出發點截然不同：

- **User Story** 刻意保持模糊、可協商，用對話補足細節。它是 XP 的產物，假設團隊隨時能找到領域專家。
- **Gherkin / Given-When-Then** 讓驗收條件可以直接驅動自動化測試，是 BDD（Behaviour-Driven Development，行為驅動開發）的語言媒介，由 Dan North 在 2006 年提出。
- **EARS** 是工程師為工程師設計的——Alistair Mavin 和同事在 Rolls-Royce 分析噴射引擎適航法規時，歸納出這套有固定句型的記法，2009 年首次在 IEEE RE'09 發表。它解決的是自然語言需求裡反覆出現的八種病（含糊、模糊、複合、遺漏、重複、冗長、實作滲透、不可測）。

在 2025 年的 SDD（Spec-Driven Development，規格驅動開發）工具中，這個差異的重要性被放大了。AWS Kiro 預設用 EARS 語法生成 `requirements.md`；GitHub Spec Kit 的 `/speckit.specify` 輸出的規格要給 AI 代理執行，不是讓人類在白板前討論。人類讀者遇到模糊敘述，靠常識補空白；AI 代理遇到模糊敘述，靠猜，而且不告訴你。

這個練習的核心問題是：**同一份功能，三種記法寫出來，你得到什麼、付出什麼、留下什麼漏洞？**

> 如果你對三種記法還不熟，先回看 [Ch 9 User Story 與 INVEST](./09-user-stories-invest.md)、[Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)、[Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

---

## 任務規格

### 情境

你在為一個電商平台的「購物車」模組撰寫需求。具體功能是：

**加入購物車**

- 使用者可以把商品加入購物車
- 同一商品加入多次，數量累加，不開新項目
- 商品庫存為零時，不允許加入
- 每個使用者的購物車最多 50 個品項
- 操作結果要即時反映在購物車圖示的徽章數字上

### 你的任務

針對上述功能，分別用三種記法各寫一套完整的需求描述：

1. **User Story 記法**（含 INVEST 自評、驗收條件概覽）
2. **Gherkin 記法**（`.feature` 檔格式，至少 4 個 Scenario，包含邊界與錯誤情況）
3. **EARS 記法**（至少覆蓋 5 條獨立需求，正確選用 EARS 的各種句型）

### 驗收條件

- 每種記法都要能描述同一套行為（三者對齊，沒有遺漏的分岐）
- EARS 的每一條都必須使用正確的句型關鍵字（`While` / `When` / `Where` / `If...then` / `The`）
- Gherkin 的 `Then` 步驟必須可以被「寫成斷言」——不能只說「系統處理」這種無法驗證的話
- User Story 旁邊要附 INVEST 自評（對每個字母打勾或說明不足之處）
- 完成後填寫三元對照表，誠實說出每種記法的優點和你看到的含糊點

### 輸入／輸出界定

**輸入**（你必須在需求中涵蓋）：
- 動作觸發：使用者點擊「加入購物車」按鈕
- 商品資料：商品 ID、當前庫存量
- 情境狀態：使用者是否已登入、購物車目前的品項數與各品項數量

**輸出（系統回應）**：
- 購物車數量徽章更新
- 購物車項目清單（品項 + 數量）變化
- 成功或失敗的提示訊息

**本練習不要求你實作程式碼。** 你要交出的是三份需求文件。

---

## 期望輸出範例

這是「加入購物車成功，且商品已在購物車中（累加）」這個場景的三種寫法對照，供你校準格式：

### User Story 片段

```
作為一個已登入的購物者，
我想要把商品加入購物車，
以便我能在結帳時統一購買。

驗收條件摘要：
- 商品加入成功後，購物車徽章數字立即更新
- 同一商品再次加入，數量 +1，不新增品項
- 庫存為零時，「加入購物車」按鈕不可點擊或顯示錯誤
```

### Gherkin 片段

```gherkin
Feature: 加入購物車

  Scenario: 首次加入商品
    Given 使用者已登入
    And 商品 "SKU-001" 庫存量為 10
    And 購物車目前有 0 個品項
    When 使用者點擊商品 "SKU-001" 的「加入購物車」按鈕
    Then 購物車品項數應為 1
    And 商品 "SKU-001" 的數量應為 1
    And 購物車徽章顯示 "1"

  Scenario: 同一商品再次加入（累加）
    Given 使用者已登入
    And 購物車已包含商品 "SKU-001" 數量 2
    When 使用者再次點擊商品 "SKU-001" 的「加入購物車」按鈕
    Then 購物車品項數仍為 1
    And 商品 "SKU-001" 的數量應為 3
```

### EARS 片段

```
[Ubiquitous] 購物車系統（Cart System）應在每次加入商品後，
立即更新購物車徽章的顯示數量。

[Event-driven] 當使用者點擊「加入購物車」按鈕且購物車中
已存在相同商品時，購物車系統應將該商品的數量加一，
而非新增品項。
```

你的最終完成品會比這個片段更完整——這裡只是校準格式用。

---

## 如果你卡住了

1. **不知道 EARS 句型怎麼選**：先問自己「這個需求有前提狀態嗎？」有 → 用 `While`；「有觸發事件嗎？」有 → 用 `When`；「是可選功能嗎？」有 → 用 `Where`；「是錯誤處理嗎？」有 → 用 `If...then`；都沒有 → 用 Ubiquitous（`The system shall`）。

2. **Gherkin 的 `Then` 寫不出可驗證的東西**：把自己想像成寫 `assert` 的工程師。你能寫 `assert(cart.badge_count == 1)` 嗎？能 → `Then` 可以這樣寫。不能 → 你的 `Then` 太模糊了。

3. **User Story 寫完覺得太空洞**：試著把驗收條件拆成具體的「何時算成功、何時算失敗」清單，再看 INVEST 的 **T（Testable）**，問自己：我現在有辦法驗收這張 Story 嗎？

4. **三種記法覆蓋的場景對不齊**：建議先用 Gherkin 把所有邊界情況列出來（因為 Gherkin 的 Scenario 結構逼你逐一思考），再反推 User Story 的範圍和 EARS 的覆蓋。

5. **庫存為零那條不知道用哪種 EARS 句型**：這是「如果 X 發生，則拒絕」的模式，屬於「不想要的行為（unwanted behaviour）」，用 `If...then` 句型。

---

## 實作步驟建議

### Step 1：列出需求點清單（10 分鐘）

在正式下筆前，先把功能拆成獨立的需求點，例如：
- 加入新商品
- 累加已有商品
- 庫存為零時拒絕
- 超過 50 品項限制時拒絕
- 更新徽章

這份清單是三種記法的「共同底稿」，確保你不會漏掉任何場景。

### Step 2：寫 Gherkin（推薦先寫）

從上面的清單出發，每個需求點至少寫一個 `Scenario`。邊界情況要各自獨立（不要把「庫存為零」和「超過50品項」擠進同一個 Scenario）。

Scenario Outline + Examples 表格適合同一邏輯、多組資料的情況（例如數量邊界值）。

### Step 3：寫 EARS

對照你在 Step 2 寫出的 Scenario，把每個行為翻譯成 EARS 句子，選擇正確句型。注意：一條 EARS 只寫一個「shall」，不要把多個行為擠進同一句。

### Step 4：寫 User Story

把整個功能聚合成 1–3 張 Story（依 INVEST 的 Small 原則，如果一張太大就拆）。為每張 Story 寫驗收條件的摘要（不需要完整的 Gherkin，但要比「系統正常運作」更具體）。

### Step 5：填三元對照表並做 INVEST 自評

完成三份需求後，填下方「對照表」的空格，誠實列出每種記法留下了哪些含糊點。

---

## 完整參考解答

**先嘗試自己完成，再打開參考解答。** 照著範例改不是練習，是抄作業。

<details>
<summary>點開參考解答（含說明）</summary>

### 一、User Story 記法

```
Story 1：加入商品

作為一個已登入的購物者，
我想要把商品加入購物車，
以便我能稍後統一結帳。

驗收條件摘要：
1. 成功加入商品後，購物車徽章的數字立即增加
2. 若商品已在購物車中，加入後數量遞增 1，品項總數不變
3. 商品庫存為零時，系統拒絕加入並顯示提示訊息
4. 購物車已達 50 個品項時，系統拒絕再加入並顯示提示訊息

---

Story 2：查看購物車即時狀態

作為一個已登入的購物者，
我想要在任何頁面都能看到購物車目前的品項數，
以便我知道有哪些東西等待結帳。

驗收條件摘要：
1. 購物車徽章數字等於目前購物車內所有品項的總件數
2. 加入商品的動作完成後，徽章數字在同一頁面無須重新整理即更新
```

**INVEST 自評（Story 1）**

| 字母 | 評估 |
|------|------|
| I (Independent) | 可以獨立排程，不依賴結帳流程 |
| N (Negotiable) | 驗收條件是摘要，細節可在 Sprint Planning 中協商 |
| V (Valuable) | 直接關係到核心購物流程，對使用者有明確價值 |
| E (Estimable) | 邊界已說明（庫存、50 品項），估點可行 |
| S (Small) | 範圍限定在「加入」這一個動作，不含結帳或刪除 |
| T (Testable) | 驗收條件可轉為測試，但「顯示提示訊息」的文字和位置未定義——留給協商 |

**說明**：User Story 刻意不寫「購物車徽章數字的 HTML element ID」或「API 回應時間需在 200ms 內」——這些細節在對話（Conversation）中補足，或在 Gherkin 和 EARS 裡寫清楚。

---

### 二、Gherkin 記法

```gherkin
Feature: 加入購物車
  作為已登入的購物者，我想把商品加入購物車，以便稍後統一結帳。

  Background:
    Given 使用者已登入
    And 購物車服務（CartService）正常運作

  Scenario: 首次加入商品
    Given 商品 "SKU-001"（名稱：「無線鍵盤」）庫存量為 15
    And 購物車目前有 0 個品項
    When 使用者點擊商品 "SKU-001" 的「加入購物車」按鈕
    Then 購物車品項總數應為 1
    And 商品 "SKU-001" 的購物車數量應為 1
    And 導覽列購物車徽章應顯示 "1"
    And 頁面應顯示成功提示 "已加入購物車"

  Scenario: 同一商品再次加入（累加數量）
    Given 商品 "SKU-001"（名稱：「無線鍵盤」）庫存量為 15
    And 購物車已包含商品 "SKU-001" 數量為 3
    When 使用者點擊商品 "SKU-001" 的「加入購物車」按鈕
    Then 購物車品項總數仍為 1
    And 商品 "SKU-001" 的購物車數量應為 4
    And 導覽列購物車徽章應顯示 "4"

  Scenario: 商品庫存為零時拒絕加入
    Given 商品 "SKU-002"（名稱：「藍牙耳機」）庫存量為 0
    When 使用者點擊商品 "SKU-002" 的「加入購物車」按鈕
    Then 系統應拒絕加入
    And 頁面應顯示錯誤訊息 "商品已售完，無法加入購物車"
    And 購物車品項總數保持不變
    And 導覽列購物車徽章數字不應改變

  Scenario: 購物車已達 50 品項上限時拒絕加入
    Given 購物車已包含 50 個不同品項
    And 商品 "SKU-099"（名稱：「滑鼠墊」）庫存量為 5
    When 使用者點擊商品 "SKU-099" 的「加入購物車」按鈕
    Then 系統應拒絕加入
    And 頁面應顯示錯誤訊息 "購物車已達上限（50 個品項），請先移除部分商品"
    And 購物車品項總數仍為 50

  Scenario Outline: 加入不同數量的商品（邊界值）
    Given 商品 "SKU-010" 庫存量為 <庫存>
    And 購物車已包含商品 "SKU-010" 數量為 <購物車現有數量>
    When 使用者點擊商品 "SKU-010" 的「加入購物車」按鈕
    Then 購物車中商品 "SKU-010" 的數量應為 <預期數量>

    Examples:
      | 庫存 | 購物車現有數量 | 預期數量 |
      | 10   | 0              | 1        |
      | 10   | 9              | 10       |
      | 1    | 0              | 1        |
```

**說明**：

- `Background` 把每個 Scenario 都需要的前提（已登入、服務正常）提出來，避免重複。
- 每個 `Then` 步驟都以可斷言的具體值描述（數字、字串），而不是「系統正常回應」。
- `Scenario Outline` 展示了如何用同一邏輯測試多組邊界值，不需要複製貼上整個 Scenario。
- Gherkin 不說明「背後怎麼實作」——不提 API、不提資料庫——這是它遵守「WHAT，不是 HOW」原則的方式。

---

### 三、EARS 記法

```
# 加入購物車功能 — EARS 需求規格

REQ-C01 [Ubiquitous]
購物車系統（Cart System）應記錄每個已登入使用者的購物車狀態，
包含品項清單及各品項數量。

REQ-C02 [Event-driven]
當已登入使用者觸發「加入購物車」動作且購物車中不存在相同商品時，
購物車系統應在購物車品項清單中新增該商品，數量設為 1。

REQ-C03 [Event-driven]
當已登入使用者觸發「加入購物車」動作且購物車中已存在相同商品時，
購物車系統應將該商品的購物車數量加一，
且不應新增額外的購物車品項。

REQ-C04 [State-driven]
當購物車系統成功完成加入商品操作後，
購物車系統應在使用者界面的購物車徽章（badge）上更新顯示目前的總品項件數，
此更新應在同一頁面不重新載入的情況下生效。

REQ-C05 [Unwanted behaviour]
如果被加入的商品庫存量為零，
則購物車系統應拒絕該加入操作，
並向使用者顯示錯誤訊息，說明商品已售完。

REQ-C06 [State-driven]
當購物車已包含 50 個不同品項時，
購物車系統應拒絕所有新增品項的加入操作，
並向使用者顯示說明已達上限的提示訊息。

REQ-C07 [Optional feature]
若系統部署環境啟用「即時庫存同步（Real-time inventory sync）」功能，
購物車系統應在加入商品前向庫存服務（Inventory Service）查詢最新庫存量，
而不使用快取值。
```

**說明**：

- 每一條 EARS 只包含一個 `shall`，對應一個獨立可驗證的行為。
- REQ-C07 使用 `Where`（optional feature）句型，因為即時庫存同步並非所有部署都需要，屬於可配置特性。
- EARS 沒有明確說「API 呼叫超時怎麼辦」——這類品質需求（Quality Requirements）應另外用 EARS Unwanted Behaviour 句型補充，或以 ISO/IEC 25010 的效能效率（Performance Efficiency）需求描述。

---

### 三元對照表（填完再看）

| 維度 | User Story | Gherkin | EARS |
|------|-----------|---------|------|
| **含糊度** | 高——刻意保留討論空間，「顯示提示訊息」沒說文字與位置 | 低——每個 Given/When/Then 都是具體狀態 | 低——固定句型強制你填入系統名稱和具體回應 |
| **可測性** | 弱——驗收條件是摘要，需要進一步轉換 | 強——可直接驅動 Cucumber 等 BDD 框架 | 中——每條需求可測，但需自行設計測試策略 |
| **可讀性（非技術人員）** | 強——敘事風格，利害關係人一眼看懂 | 中——Given/When/Then 結構稍有學習曲線 | 弱——shall 句型有工程文件感，需要背景知識 |
| **AI 代理友善度** | 弱——AI 遇到模糊驗收條件只能猜 | 中——可執行規格，但 AI 仍需理解上下文 | 強——句型固定，關鍵字明確，最不易誤解 |
| **修改成本** | 低——改一行就夠 | 高——每個 Scenario 都要同步更新 | 中——各條獨立，但覆蓋範圍要自己確認 |
| **明顯留下的含糊點** | 徽章更新時機、錯誤訊息文字、50 品項的計算單位 | 「庫存量」是實時還是快取？並發加入怎麼處理？ | 沒有說明 UI 元素的位置，也沒有說明錯誤訊息的格式 |

</details>

---

## 測試用例表

下表列出你的三份需求文件應覆蓋的驗證項目。完成後逐一勾選：

| # | 場景 | User Story 有涵蓋 | Gherkin 有 Scenario | EARS 有對應條 |
|---|------|:---:|:---:|:---:|
| T1 | 已登入使用者首次加入商品 | | | |
| T2 | 同一商品加入第二次（累加數量） | | | |
| T3 | 庫存為零時拒絕加入 | | | |
| T4 | 購物車品項達 50 個上限時拒絕 | | | |
| T5 | 加入成功後徽章即時更新 | | | |
| T6 | 不同商品的品項數各自獨立計算 | | | |

如果某一格打不了勾，代表你的某種記法漏了這個場景。

---

## 踩雷集錦

**錯誤直覺 1：User Story 就是「需求的草稿」，之後再補細節**
→ 正確認識：User Story 是「對話的提示（prompt）」，細節不是「之後」補，是「在 Sprint Planning 的對話中當場」補。如果到了寫程式時才發現沒細節，代表對話環節跳過去了，不是 User Story 的問題。

**錯誤直覺 2：Gherkin 的 Given 要把所有前置條件全寫進去（越完整越好）**
→ 正確認識：過度詳細的 Given 讓測試維護成本爆炸。Background 機制正是為了提取共用前提；一個 Scenario 的 Given 只寫「與這個場景直接相關的差異狀態」。「使用者有網路」這種廢話 Given 要刪掉。

**錯誤直覺 3：EARS 的 `shall` 等於「應該盡量做到」**
→ 正確認識：在需求工程規範（包括 IEEE 830）中，`shall` 是強制要求（mandatory），`should` 才是建議。EARS 使用 `shall` 就是宣告這是硬性需求。如果你想表達「建議但非強制」，不要用 EARS 的標準句型，要額外標注。

**錯誤直覺 4：三種記法只要選一種就夠了**
→ 正確認識：這三種記法服務不同的受眾和目的。User Story 服務利害關係人的對話；Gherkin 服務開發者和 QA 的可執行驗收；EARS 服務 AI 代理或工程合規審查。在一個 SDD 流程裡，它們往往同時存在：先寫 User Story 釐清價值，再寫 Gherkin 定義驗收，再寫 EARS 餵給代理。

**錯誤直覺 5：EARS 不適合用在 UI 相關需求，只適合嵌入式系統**
→ 正確認識：EARS 起源於噴射引擎適航法規，但其句型是通用的英文結構約束，對 Web UI、API、後端服務同樣適用。AWS Kiro 對普通 Web 應用程式的 `requirements.md` 就是用 EARS 格式生成的（查證日期：2026-06-30）。

---

## 延伸挑戰

完成基本任務後，可以繼續：

1. **並發場景**：兩個使用者同時把同一商品（庫存只剩 1 個）加入各自的購物車，誰成功？用 Gherkin 和 EARS 各自描述這個競態條件（race condition），比較兩者的表達能力。

2. **EARS 品質需求**：用 EARS 的 Unwanted Behaviour 或 State-driven 句型，補充一條效能需求：「加入購物車的 API 回應時間在 95th percentile 應低於 X 毫秒」。X 要怎麼決定？

3. **跨記法一致性測試**：把你的 Gherkin Scenario 清單作為 ground truth，逐條確認 EARS 和 User Story 是否各自覆蓋同樣的範圍。如果有缺漏，補齊並說明為什麼你一開始漏掉。

4. **餵給 AI 代理**：把你的 EARS 需求（REQ-C01 到 REQ-C07）和你的 Gherkin feature 分別貼給一個 AI coding 代理（例如 GitHub Copilot 或 Kiro），觀察它生成的骨架代碼有什麼差異。那種記法讓代理少問問題？

---

## 自我檢核

完成後，試著不看你寫的東西，回答以下問題（主動回憶，不是翻書）：

- [ ] 用自己的話說：為什麼 EARS 的 `shall` 不能換成 `should`？這對 AI 代理有什麼影響？
- [ ] 面試被問「User Story 和 EARS 最大的差別是什麼」，你會怎麼在 30 秒內答出有觀點的答案？
- [ ] 你在練習中找到了幾個三種記法都沒說清楚的含糊點？那些含糊點裡，哪些會讓 AI 代理猜錯？
- [ ] EARS 的 Optional feature 句型（`Where`）和 Event-driven 句型（`When`）差別是什麼？你寫的 REQ-C07 用哪個？為什麼？
- [ ] Gherkin 的 `Background` 和 `Scenario Outline` 各解決了什麼問題？

---

## 延伸閱讀

- **EARS: Easy Approach to Requirements Syntax — 官方指南（Alistair Mavin）**
  URL：https://alistairmavin.com/ears/
  讀哪裡、學什麼：直接閱讀「EARS Patterns」段落，對照你在本練習中使用的各句型，確認關鍵字和子句順序是否正確。EARS 創始人的第一手文件，是校正你自己寫出來的 EARS 句子的最佳參照。

- **EARS 原始論文（RE'09）— Mavin, Wilkinson, Harwood, Novak（Rolls-Royce）**
  URL：https://dl.acm.org/doi/10.1109/RE.2009.9（公開 PDF 鏡像：https://ccy05327.github.io/SDD/08-PDF/Easy%20Approach%20to%20Requirements%20Syntax%20(EARS).pdf）
  讀哪裡、學什麼：第 2 節「Problems with Natural Language Requirements」，就是那八種自然語言需求的病。讀完再回頭看你寫的 User Story，數一數你自己犯了幾條。

- **Introducing BDD — Dan North**
  URL：https://dannorth.net/blog/introducing-bdd/
  讀哪裡、學什麼：「Acceptance criteria should be executable」段落。North 說明為什麼把 Given-When-Then 設計成可執行的格式，而不只是人類讀的文件。直接關聯到你在本練習中寫的 `Then` 步驟為何要能對應到斷言。

- **Gherkin Reference（Cucumber 官方文件）**
  URL：https://cucumber.io/docs/gherkin/reference/
  讀哪裡、學什麼：「Scenario Outline」和「Background」兩個段落。你在本練習裡用到了兩者，文件裡有完整的語法規則和不該踩的格式地雷（例如 `<placeholder>` 的大小寫敏感性）。

- **INVEST in Good Stories, and SMART Tasks — Bill Wake**
  URL：https://xp123.com/invest-in-good-stories-and-smart-tasks/
  讀哪裡、學什麼：完整讀完（不長）。這是 2003 年 Wake 自己寫的原始文章。對照你在練習中做的 INVEST 自評，看看你的理解和原始定義是否對齊，特別是 N（Negotiable）和 T（Testable）。

- **Spec-driven development: Unpacking 2025's key new AI-assisted engineering practices — Liu Shangqi（Thoughtworks）**
  URL：https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
  讀哪裡、學什麼：「Why structured input/output reduces hallucination」相關段落。本練習的核心問題之一是「哪種記法 AI 代理最不容易誤解」，這篇文章直接從工程師的實踐角度給出答案，連接 Ch 11 的 EARS 和 2025 年 SDD 工具的現實。

→ [Ch 14 為什麼 DDD：複雜性在領域，不在技術](./14-why-ddd.md)
