# Final Project — 對一個真實小產品跑完整 SDD

> **目標**：整合全課 ≥70% 核心概念，對一個你自己選定的真實小產品，跑完「領域建模 → EARS/Gherkin 需求 → spec → 工具實作（或自建 pipeline）→ 驗收 → 誠實復盤」的完整 SDD 循環，並輸出一份對照基準，誠實評估 SDD 帶來的差異與侷限。

---

## 0. 在你開始之前：一場誠實的對話

這門課走了 44 章、6 個練習，談了 DDD、EARS、Gherkin、Spec Kit、Kiro、信任階梯、規格漂移。最後一關的問題只有一個：

**你能把這些整合起來，對一個真實的東西做有效？**

「真實」有兩層意思。第一，產品必須解決你或你的使用者真正遇到的問題——不是課程範例、不是你不打算用的 demo。第二，你必須願意在復盤階段面對「SDD 沒有讓這個更好」的可能性，並寫下來。

在 2025-2026 年，Thoughtworks 對 SDD 的評級是 **Assess**（評估），不是 Adopt。原因是：工作流「elaborate and opinionated」，產物「hard to review」。這不是反對你做這個 Final Project——是要你帶著這份誠實進入它。

---

## 1. 產品情境選擇

### 什麼樣的產品適合

```
最佳甜蜜點：
  問題空間：有真實的領域規則（不是純 CRUD）
  程式規模：1-3 個 Bounded Context，< 2,000 行可跑的程式碼
  時間預算：你願意花 2-4 週完整跑過

太小：只有一個 API endpoint，沒有值得建模的領域
太大：多個子系統、10+ 人協作——SDD 對大型現有 codebase 的效益尚未驗證
```

### 建議題目（選一個或自選）

| # | 題目 | 核心領域規則 | 預估 Bounded Context 數 |
|---|------|------------|----------------------|
| A | 個人帳務追蹤（支出/收入/分類/月報表） | 交易不可刪除（只能抵消）；預算超支警示 | 2：帳務、報表 |
| B | 食譜平均費用計算器（食材市價+份量+替換規則） | 替換食材需重算過敏原；費用以採購單位計 | 2：食譜、食材庫 |
| C | 讀書筆記 + 閃卡複習系統（SM-2 間隔重複） | 複習間隔由上次評分動態計算；卡片有「退休」狀態 | 2：筆記、複習排程 |
| D | 小型訂閱制收費追蹤（訂閱、續費、取消、退款） | 退款需在 30 天內；取消不刪記錄 | 3：訂閱、付款、通知 |

> 如果你對 Event Storming 還不熟，先回看 [Ch 21 Event Storming 工作坊](./21-event-storming.md)，再決定題目——選題的直覺很大程度來自看懂事件流的能力。

---

## 2. 全課整合地圖

這個 Final Project 要求你在一個連貫的產品裡觸碰以下核心概念：

```
領域建模（Ch 14-21）
  └─ 通用語言  ──────────────────────► spec 詞彙表（Ch 34）
  └─ Bounded Context  ──────────────► agent scope（Ch 35）
  └─ Entity / Value Object / Aggregate  ─► spec 骨架（Ch 36）
  └─ Domain Event  ──────────────────► Event Storming（Ch 21）

需求工程（Ch 8-13）
  └─ EARS 五型句型  ─────────────────► requirements.md
  └─ Gherkin Given-When-Then  ───────► 驗收測試

SDD 工具（Ch 27-32）
  └─ Spec Kit /speckit.* 或 Kiro  ──► 實作 pipeline
  └─ 自建 pipeline（Ch 38）  ────────► 備選方案

治理與誠實（Ch 39-44）
  └─ 規格漂移偵測  ──────────────────► 復盤
  └─ 信任階梯  ──────────────────────► 復盤
  └─ 對照基準  ──────────────────────► 本 Final 強制要求
```

---

## 3. 分階段任務

### 階段 0：環境與對照基準（第 1 天）

**這是最多人跳過、最後最後悔的步驟。**

在做任何 SDD 之前，先用「直接 prompt」做一份基準版本：

1. 把你要做的功能用一段白話文（100-200 字）描述，直接貼給 Claude Code / GitHub Copilot / 任何你習慣的 coding agent。
2. 讓它生成程式碼，記錄：
   - 你總共發了幾輪 prompt？
   - 哪些地方它猜錯了？
   - 最後產出的程式碼能跑嗎？測試覆蓋率大概是多少？
3. 把這份基準存到 `baseline/` 目錄。

不做基準，你就無法知道 SDD 帶來的差異是真的改善，還是你這次花更多時間的自然結果。

> 如果你對工具環境還沒準備好，先回看 [Ch 0 環境搭建](./00-environment-setup.md)。

---

### 階段 1：領域建模（第 2-3 天）

#### 1a. 通用語言（Ubiquitous Language）詞彙表

開一個 `glossary.md`，定義你的領域術語。每個詞條格式：

```markdown
### Transaction（交易）
一筆金錢的流入或流出。永遠附帶發生時間、金額、幣別、分類。
**不是** Invoice（請款單）也不是 Receipt（收據）。
Transaction 一旦建立，只能用 Reversal（沖銷）取消，不能刪除。

- 同義詞（禁止在程式碼中使用）：payment, expense, record
- 跨 context 的多義詞：在 Reporting context 中，Transaction 是只讀的聚合快照
```

目標：5-10 個核心概念，每個有「不是什麼」和「跨 context 時的語義差異」。

Fowler 對通用語言的基礎描述：「software doesn't cope well with ambiguity」——這句話放到 LLM coding agent 上加倍成立：人還能問，agent 只能猜。

> 如果你對通用語言還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)。

#### 1b. 一場輕量 Event Storming（60-90 分鐘）

用便利貼（實體或 Miro/FigJam）跑一場 Big-Picture Event Storming：

1. **橘色**：Domain Events（過去式，例：`TransactionRecorded`、`BudgetBreached`）
2. **藍色**：Commands（觸發 event 的動作，例：`RecordTransaction`）
3. **黃色**：Actor（誰發出 command）
4. **紫色/淡紫**：External System 或 Timer
5. 把 event 按時間軸排列，找出「哪裡有爭議」（熱點）

Event Storming 不是圖表練習。Brandolini 的原始定義：「a workshop format for quickly exploring complex business domains」——關鍵詞是 quickly 和 exploring，不是 documenting。

拍一張工作坊的照片或截圖，放進 `modeling/event-storm.png`。

> 如果你對 Event Storming 還不熟，先回看 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

#### 1c. 戰術建模

從 Event Storming 的結果，辨識：

```
Aggregate Roots（從 Event 往左推 Command，命令落在哪個物件上）
Value Objects（沒有獨立 identity 的屬性群，例：Money(amount, currency)）
Domain Events（已經確認）
Bounded Contexts（哪些 Event 屬於不同的模型邊界）
```

用這個範本，寫進 `modeling/domain-model.md`：

```markdown
## Aggregate: Transaction
- Identity: TransactionId (UUID)
- Attributes:
  - amount: Money (Value Object)
  - occurredAt: datetime
  - category: Category (Value Object, 有效值集合)
  - note: string (可空)
- Invariants:
  - amount.value > 0（金額必須正值；借貸由 type 決定）
  - occurredAt 不可早於系統上線日
- Commands: RecordTransaction, ReverseTransaction
- Events: TransactionRecorded, TransactionReversed
- Repository: TransactionRepository（只暴露 add() 和 ofId()）
```

Vernon 給 Aggregate 的四條設計規則（《Implementing DDD》，2013）：
1. 在一致性邊界內建模真實不變量（invariant）
2. Aggregate 設計要小
3. 跨 Aggregate 只用 ID 引用
4. 跨邊界用 Domain Event 達到最終一致性

> 如果你對 Entity / Value Object / Aggregate 還不熟，先回看 [Ch 19](./19-entities-value-objects-aggregates.md) 和 [Ch 20](./20-repositories-services-events.md)。

---

### 階段 2：需求撰寫（第 3-4 天）

#### 2a. 使用者故事 + INVEST 過濾

為每一個核心 use case 寫一張使用者故事（Connextra 模板）：

```
作為 <角色>，我想要 <功能>，以便 <商業價值>
```

然後用 INVEST 過濾（Bill Wake，2003）：
- **I**ndependent：這張故事可以獨立實作、不依賴另一張未完成的故事嗎？
- **N**egotiable：細節是和 stakeholder 討論出來的，不是寫死的嗎？
- **V**aluable：對使用者或業務有直接價值嗎？
- **E**stimable：夠小、夠清楚，能估算工作量嗎？
- **S**mall：一個 sprint 內（或個人專案的一週內）能完成嗎？
- **T**estable：有辦法寫出自動化測試來驗證嗎？

如果一張故事在 I、T 上不過，要拆或重寫，否則不進 spec。

#### 2b. EARS 驗收條件

每張故事至少兩條 EARS 句型（Mavin et al., Rolls-Royce, RE'09）。EARS 泛用模板：

```
While <前提狀態>, when <觸發事件>, the <系統名稱> shall <系統回應>
```

五種句型範例（以帳務追蹤為例）：

| 句型 | 範例 |
|------|------|
| Ubiquitous（無條件） | The system shall display all transactions in reverse chronological order. |
| State-driven（While） | While the monthly budget is active, the system shall track remaining balance in real time. |
| Event-driven（When） | When a transaction is recorded, the system shall update the category monthly summary. |
| Optional-feature（Where） | Where the user has enabled budget alerts, the system shall send a notification when budget utilisation reaches 80%. |
| Unwanted-behaviour（If…then） | If the transaction amount is zero or negative, then the system shall reject the input and display an error. |

EARS 存在的理由：Mavin 等人分析 Rolls-Royce 噴射引擎控制系統的需求文件，歸納出自然語言需求的八種缺陷（模糊、含糊、複雜、遺漏、重複、累贅、實作導向、不可測）。EARS 用固定的關鍵字順序「輕柔地約束」（gently constrain）自然語言，保留可讀性，同時強迫作者給出完整的系統回應。

> 如果你對 EARS 還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

#### 2c. Gherkin 場景（至少 2 個 happy path + 1 個 sad path）

每個 Aggregate 的核心行為各寫一個 Scenario：

```gherkin
Feature: 記錄交易

  Background:
    Given 使用者已登入，且本月預算設定為 5,000 元

  Scenario: 成功記錄一筆支出
    Given 使用者在支出記錄頁面
    When 使用者輸入金額 350，分類「餐飲」，日期「今天」
    And 使用者按下「儲存」
    Then 系統顯示成功訊息
    And 本月「餐飲」分類的消費總計增加 350 元
    And 剩餘預算減少 350 元

  Scenario: 金額為負數時被拒絕
    Given 使用者在支出記錄頁面
    When 使用者輸入金額 -100，分類「餐飲」
    And 使用者按下「儲存」
    Then 系統顯示錯誤「金額必須大於零」
    And 系統不建立任何交易記錄

  Scenario Outline: 不同分類的支出都正確更新月報表
    Given 使用者已有以下交易記錄：<already_spent> 元在「<category>」
    When 使用者新增一筆 <new_amount> 元的「<category>」支出
    Then 「<category>」的月累計金額為 <total> 元

    Examples:
      | category | already_spent | new_amount | total |
      | 餐飲     | 1000          | 350        | 1350  |
      | 交通     | 0             | 200        | 200   |
      | 娛樂     | 800           | 500        | 1300  |
```

> 如果你對 Gherkin 還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

---

### 階段 3：撰寫 spec（第 4-5 天）

把前面的材料組裝成一份完整的 spec 文件 `specs/001-<feature-name>/spec.md`。

**spec 的結構**（對齊 Spec Kit 的 spec-template.md 精神）：

```markdown
# Spec: Transaction Recording

## Problem Statement
（1 段。問題是什麼、誰有這個問題、現在怎麼解決、為什麼不夠好）

## Goals
（3-5 條 bullet，每條是可驗證的成果，不是步驟）
- 使用者能在 3 秒內記錄一筆交易，不需要離開目前頁面
- 系統在交易儲存後立即更新月度摘要，誤差 < 1 元

## Non-Goals
（明確說不做什麼。這比 Goals 更重要，因為它限制 scope）
- 不支援多幣別（本版本只有台幣）
- 不提供匯入 CSV 功能

## Domain Glossary
（從 glossary.md 抄來的相關詞條）

## User Stories
（從階段 2 過來的故事，帶 INVEST 評分）

## Acceptance Criteria
（EARS 句型，帶 Gherkin 場景）

## Data Model
（從 domain-model.md 抄來的相關 Aggregate）

## Out-of-scope Edge Cases
（明確列出不處理的 edge case，以及為什麼）
```

Spec Kit 的 spec-driven.md 對 spec 的定位：「Specifications don't serve code—code serves specifications... The specification becomes the primary artifact. Code becomes its expression in a particular language and framework.」

這句話說起來容易。你寫 spec 的時候，會發現自己不斷想跳進去寫怎麼實作——那個衝動就是問題所在。spec 只問 WHAT 和 WHY，HOW 留給 plan 階段。

> 如果你對如何把需求寫成 spec 還不熟，先回看 [練習 D — 把需求＋領域模型寫成一份完整的 spec](./practice-d-write-a-spec.md)。

---

### 階段 4：實作 pipeline（第 5-10 天）

#### 選項 A：使用 GitHub Spec Kit（推薦新手）

> **注意**：命令名稱 version-dependent，以下以查證日期 2026-06-30 的 v0.11.10 為準，未來版本可能變動。請執行 `specify --version` 確認。

```bash
# 安裝（需要 Python 3.11+、uv）
uv tool install specify-cli \
  --from git+https://github.com/github/spec-kit.git@v0.11.10

# 初始化（以 Claude Code 為例；或換成 copilot/cursor/gemini 等）
specify init my-project --integration claude

# 在你的 coding agent 裡，依序執行：
/speckit.constitution   # 寫下產品的治理原則
/speckit.specify        # 從你的 spec.md 生成完整功能說明
/speckit.clarify        # （推薦在 plan 前跑）解決模糊點
/speckit.plan           # 生成技術計劃（含 data model、contracts/）
/speckit.analyze        # 跑 spec/plan/tasks 一致性檢查
/speckit.tasks          # 拆成可執行 task list
/speckit.implement      # 驅動 agent 實作
/speckit.converge       # 對照 spec 確認還缺什麼
```

**把你的 spec.md 內容貼進 `/speckit.specify` 的 prompt 之前**，先把 `glossary.md` 和 `domain-model.md` 也放進 agent 的 context（通常是 CLAUDE.md 或 .github/copilot-instructions.md）。這是 DDD 通用語言對 LLM 最直接的效用。

> 如果你對 Spec Kit 工作流還不熟，先回看 [Ch 27](./27-spec-kit-install.md) 和 [Ch 28](./28-spec-kit-workflow.md)。

#### 選項 B：使用 AWS Kiro

> **注意**：Kiro 定價與模型版本 version-dependent（查證日期 2026-06-30）。目前 Auto 模式和 Sonnet 模式都有收費，1.3x 倍率；免費期已於 2025-09-30 結束。請查 https://kiro.dev/pricing/ 確認最新方案。

Kiro 把一個 spec 分為三個產物：

- `requirements.md`：使用者故事 + EARS 驗收條件（Kiro 會自動生成 EARS 格式）
- `design.md`：資料流圖、TypeScript interfaces、API endpoints
- `tasks.md`：帶依賴順序的 task list，支援「波浪（waves）」並行執行

你的 Stage 2 已經產出了 EARS 和 Gherkin——可以直接餵進 Kiro 的 spec session，跳過它的需求生成，直接進 design 階段。

> 如果你對 Kiro 還不熟，先回看 [Ch 30 AWS Kiro](./30-kiro.md)。

#### 選項 C：自建最小 pipeline

如果你在練習 F 已經建了一條 pipeline，在這裡延伸它：

```
glossary.md + domain-model.md
        │
        ▼
  spec.md（你在 Stage 3 寫的）
        │
        ▼
  [LLM] 生成 plan.md（技術架構 + 介面定義）
        │
        ▼
  [LLM] 拆 tasks.md（帶優先序）
        │
        ▼
  [LLM 實作 + 你 review] 生成程式碼
        │
        ▼
  [LLM 或 自動測試工具] 跑 Gherkin scenarios
```

> 如果你對自建 pipeline 還不熟，先回看 [Ch 38 自建一條 spec→plan→tasks→implement→verify 流水線](./38-build-your-own-pipeline.md) 和 [練習 F](./practice-f-mini-sdd-pipeline.md)。

---

### 階段 5：驗收（第 10-12 天）

驗收的目標不是「程式跑起來了」，而是「spec 裡的每一條 acceptance criteria 都有證據說它被滿足了」。

#### 5a. 自動化驗收測試

每一條 Gherkin Scenario 都要有對應的自動化測試。用任何你熟悉的語言：

- Python + pytest-bdd（`pip install pytest-bdd`）
- JavaScript + Cucumber.js
- TypeScript + Vitest + 手寫的 Given/When/Then helpers

以帳務追蹤為例（Python + pytest-bdd）：

```python
# tests/test_transaction.py
import pytest
from pytest_bdd import scenario, given, when, then, parsers
from your_app import create_app, db, Transaction

@pytest.fixture
def app():
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()

@scenario("features/transaction.feature", "成功記錄一筆支出")
def test_record_transaction():
    pass

@given("使用者已登入，且本月預算設定為 5,000 元")
def setup_user_and_budget(app):
    # 設定測試使用者與預算
    ...

@when(parsers.parse("使用者輸入金額 {amount}，分類「{category}」，日期「今天」"))
def input_transaction(amount, category):
    ...

@when("使用者按下「儲存」")
def submit_transaction(client):
    response = client.post("/api/transactions", json={...})
    assert response.status_code == 201

@then("本月「餐飲」分類的消費總計增加 350 元")
def verify_category_total():
    ...
```

#### 5b. 驗收矩陣

建立一張表格，逐條核對 spec 裡的 acceptance criteria：

| # | EARS 需求 | 對應 Gherkin Scenario | 測試通過 | 備注 |
|---|-----------|----------------------|---------|------|
| AC-1 | When a transaction is recorded, the system shall update the category monthly summary. | 成功記錄一筆支出 | ✓ | |
| AC-2 | If the transaction amount is zero or negative, then the system shall reject the input and display an error. | 金額為負數時被拒絕 | ✓ | |
| AC-3 | While the monthly budget is active, the system shall track remaining balance in real time. | （手動測試） | ? | 需要 WebSocket 或 polling，目前版本未實作 |

對 AC-3 這類「未完成」的需求，**不要假裝它通過了**。記錄下來，進復盤。

---

### 階段 6：誠實復盤（第 12-14 天）

這是全課最重要的一個階段，也是 SDD 作為實踐能否成熟的關鍵所在。

把你的觀察寫進 `retrospective.md`，包含以下四個維度：

#### 6a. 對照基準比較

| 維度 | 直接 prompt（基準） | SDD 流程 |
|------|--------------------|---------|
| 完成時間 | X 小時 | Y 小時 |
| Prompt 來回次數 | N 次 | M 次 |
| 領域規則遺漏數 | X 條（列舉） | Y 條（列舉） |
| 測試覆蓋率 | X% | Y% |
| 程式碼可讀性（主觀，1-5） | X | Y |
| spec 與最終程式碼一致性 | N/A | 你自己評估 |

這不是「SDD 一定贏」的表格。如果基準版本在某個維度更好，照實填。

> 如果你對為什麼要有實測數據還有疑問，先回看 [Ch 40 實測數據與復現報告](./40-empirical-evidence.md)。

#### 6b. 領域建模的投資回報

回答這幾個問題（不要一句話帶過）：

1. Event Storming 發現了哪些需求，是你一開始的直覺 prompt 裡沒有的？
2. 通用語言詞彙表有沒有幫助 agent 少犯命名歧義錯誤？能舉具體例子嗎？
3. Bounded Context 的邊界劃在哪裡？現在回頭看，這個劃法讓 agent 的任務更清楚了，還是更麻煩了？

#### 6c. SDD 帶來的真實摩擦

Zaninotto（Marmelab CEO，2025 年 11 月）批評 SDD 有「Markdown Madness」——一個 spec-kit 範例（顯示目前日期）產生了 8 個檔案、1,300 行 Markdown。HN 使用者 yoaviram 跑了 10 天，最後大多數測試失敗、build 沒過。HN 使用者 ctxc 說 spec-kit「總是把事情做得過度複雜」。

你的體驗如何？具體說：

- 哪些步驟的產出你花最多時間 review？
- spec 和最終程式碼有沒有開始漂移（spec drift）？從什麼時候開始？
- 如果要再做一次，你會在哪個 Bounded Context 上少花時間建模，哪個多花？

> 如果你對規格漂移還不熟，先回看 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

#### 6d. 信任階梯評估

《Ch 44 信任階梯》把人類對 agent 的信任分成幾個層級：從「每個 task 手動觸發 + 人工 review」到「全自動 + 事後抽查」。

這次你站在哪一層？為什麼？下一次做同類專案你打算往上還是往下移一級？

---

## 4. 驗收標準

你的 Final Project 達到「完成」需要滿足以下條件：

### 必要條件（全部要有）

- [ ] `glossary.md`：5 個以上核心領域概念，每個有「不是什麼」的說明
- [ ] `modeling/event-storm.png`（或等效圖）：至少 8 個 Domain Events，標出 Command 和 Actor
- [ ] `modeling/domain-model.md`：至少 2 個 Aggregate，每個帶 invariant 和 Domain Event
- [ ] `specs/001-xxx/spec.md`：包含 Problem Statement、Goals、Non-Goals、Glossary、EARS AC、Gherkin Scenario
- [ ] 至少 5 條 EARS 驗收條件（覆蓋 5 種句型中的至少 3 種）
- [ ] 至少 3 個 Gherkin Scenario（2 個 happy path + 1 個 sad path）
- [ ] 可跑的程式碼，至少 2 個 Aggregate 有對應實作
- [ ] 自動化驗收測試：Gherkin Scenario 覆蓋率 ≥ 80%
- [ ] `baseline/` 目錄：紀錄直接 prompt 的基準版本
- [ ] `retrospective.md`：包含對照表、領域建模評估、摩擦點、信任階梯定位

### 加分條件（有加分，沒有不扣分）

- [ ] 用 `/speckit.converge` 或等效手段做了一次「spec vs 最終程式碼」的 gap analysis
- [ ] 偵測到至少一個 spec drift 並記錄如何修正
- [ ] Kiro 的 `design.md` 有對應的 domain model，或 Spec Kit 的 `plan.md` 和你的 `domain-model.md` 能對齊
- [ ] 試過用 Alloy 或 TLA+ 驗證至少一條 invariant（例：Transaction 不可刪除這條規則）

---

## 5. 評分 Rubric

| 維度 | 1（缺席） | 2（部分） | 3（達標） | 4（優秀） |
|------|---------|---------|---------|---------|
| 領域建模深度 | 沒有 domain model 或 event storm | 有 model 但沒有 invariant，event 少於 5 個 | 2 個 Aggregate，有 invariant，Event Storming 有 8+ events | 3 個以上 Aggregate，invariant 都有自動測試，Event Storming 找出了一條 baseline 版本遺漏的規則 |
| 需求品質 | EARS/Gherkin 句型錯誤或缺席 | 有嘗試但混用句型，或沒有 sad path | 5 條正確 EARS，3 個 Gherkin 場景含 sad path | EARS 覆蓋 5 種句型，Gherkin 用 Scenario Outline 處理了邊界值，所有 AC 可追溯到 User Story |
| spec 完整性 | 沒有 spec 或只有 todo list | 有 spec 但缺 Non-Goals 或 Out-of-scope edge cases | 有完整結構，Non-Goals 和 Out-of-scope 清楚 | spec 讀起來像一個人可以依它實作，不需要問任何問題 |
| 實作與測試 | 程式碼不能跑或沒有測試 | 能跑但測試覆蓋率 < 50% | 覆蓋率 ≥ 80%，Gherkin 自動化 | 有 gap analysis（converge 或等效），spec drift 有被偵測並記錄 |
| 復盤誠實性 | 沒有對照基準或復盤是鄉愿的 | 有基準但沒有具體數字，或只寫好的地方 | 有對照表、有具體 domain rule 遺漏案例、有摩擦點 | 敢說「SDD 在 X 上沒有改善基準」並解釋為什麼，對信任階梯有明確判斷 |

---

## 6. 踩雷集錦

這裡的每一條都是真實踩過的坑，不是理論上可能出錯的地方。

### 雷 1：把「領域建模」當成「畫 UML 圖的前置作業」跳過

**錯誤直覺**：spec 不就是用來餵 agent 的 prompt？我直接寫 spec 就好，domain model 是過時的老東西。

**正確認識**：Annegret Junker 在 codecentric 的食譜平台案例裡，v1（直接 prompt）產生 3 個 schema；做了 Domain Storytelling + Event Storming 後的 v2 產生 9 個 schema，而且找出了一條自我評分業務規則，是 v1 完全沒碰到的。領域建模不是文件儀式，是需求發現過程。

### 雷 2：通用語言只寫在 glossary.md，沒有放進 agent context

**錯誤直覺**：我定義了詞彙表，agent 應該從 spec 裡自然學到這些詞。

**正確認識**：LLM 有它自己的「transaction」語義（一般為資料庫事務），會和你的業務語義衝突。你必須明確把 glossary.md 的內容放進 CLAUDE.md 或 copilot-instructions.md，讓它在每次 agent 呼叫時都在 context 裡。Daniel Schleicher 說：「When we give an AI agent ambiguous instructions where order could mean a dozen different things, it amplifies the chaos.」agent 不會主動問，它只會猜。

### 雷 3：把 spec 寫成實作指令

**錯誤直覺**：spec 就是比較詳細的 prompt，我把技術細節都寫進去，agent 才知道怎麼做。

**正確認識**：spec 只問 WHAT 和 WHY。一旦 spec 開始寫「用 PostgreSQL 的 JSONB 欄位儲存 category metadata」，你就進入 plan 的地盤了，而且你鎖死了技術選擇，讓 agent 的 plan 步驟失去意義。Spec Kit 的 spec-template.md 明確標注「[NEEDS CLARIFICATION]」給 agent，不是因為你不夠聰明，是因為有些事應該留在對話裡討論，不是 spec 的工作。

### 雷 4：驗收矩陣只填「✓」，不填條件

**錯誤直覺**：測試過了就是過了，不用記錄條件。

**正確認識**：「While the monthly budget is active」這個前提如果你的測試從來沒有驗到「budget not active」的狀態，這條 EARS 只算半通過。spec drift 的發現路徑通常是：再看驗收矩陣 → 發現測試的前提和 EARS 的前提不一致 → 發現程式碼和 spec 已經悄悄分叉了。

### 雷 5：復盤只寫「SDD 很好，以後都要用」

**錯誤直覺**：我投入了這麼多時間在 SDD，復盤當然要說它有效，否則豈不是白費？

**正確認識**：Thoughtworks Technology Radar（Vol 34，2025 年 11 月）對 SDD 的評語是 Assess，不是 Adopt。它的工作流「elaborate and opinionated」，輸出「hard to review」。你的復盤如果沒有承認這些，它就不是真正的復盤，它是行銷文案。最有價值的學習來自「在 X 情況下 SDD 沒有改善基準，因為 Y」。

### 雷 6：把 Bounded Context 邊界畫得太細

**錯誤直覺**：DDD 說要多個 Bounded Context，所以我把每個功能都拆成一個 context。

**正確認識**：Vernon 在《DDD Distilled》（2016）提醒：Bounded Context 的邊界要反映真實的模型差異，不是組織圖或功能清單。一個帳務追蹤 app 如果「記帳」和「看報表」共用完全相同的 Transaction 模型，就不需要兩個 context。過度切割讓 agent 的 scope 碎片化，反而讓你在 `/speckit.plan` 生成的 contracts/ 裡出現大量跨 context 的 ID mapping，這是技術複雜性，不是業務複雜性。

---

## 7. 進階延伸

完成基本的 Final Project 後，如果你想繼續挖：

**形式化驗證 invariant**：用 Alloy 把「Transaction 不可刪除」寫成一個 fact，讓 Alloy Analyzer 找反例。如果模型正確，analyzer 找不到反例——這是比測試更強的保證，但代價是需要學一門新語言。先看 [Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)。

**spec 作為 CI/CD gate**：在 PR pipeline 裡加一個步驟，掃描 spec 和程式碼之間的 keyword 差異（例：spec 裡出現 `ReverseTransaction` 但程式碼裡用的是 `cancelTransaction`），自動開 issue。這是 spec drift detection 的最小可行實作。

**信任階梯的邊界實驗**：把 `/speckit.implement` 設成 fully autonomous mode（不逐 task review），看它在你的 spec 上跑完的結果和你逐 task review 的版本差在哪。這個對比是 [Ch 44 信任階梯](./44-trust-ladder.md) 的實體化驗證。

**換工具重跑同一個 spec**：用 Spec Kit 跑完後，把同一份 `spec.md` 放進 Kiro 跑一次，比較兩個工具的 `design.md` 和 `tasks.md` 差異。這是 [Ch 32 工具橫向對比](./32-tool-comparison.md) 的個人實測版本。

---

## 8. 常見卡點 Q&A

**Q：我的產品沒有複雜的領域規則，感覺 DDD 對我過重了？**

A：如果你的產品確實是純 CRUD，那 SDD 的收益本來就有限——這本身是一個重要的發現，可以直接寫進復盤。Vernon 在《DDD Distilled》裡說過，Generic Subdomain（可以買現成的）根本不需要 DDD 戰術建模。這個 Final Project 的目的是讓你親身體驗 SDD 的邊界，不是強迫你在不需要的地方用它。

**Q：Spec Kit 的命令我跑不動，或者產出和 spec 完全對不上？**

A：兩個最常見的原因：(1) agent context 裡沒有放 glossary 和 domain model；(2) spec 寫得太模糊，agent 填空填出一個你不認識的設計。先回看 spec，問自己：「如果我是一個完全不認識這個產品的工程師，我能依這份 spec 實作嗎？」如果答案是否，spec 需要先修。HN 使用者 yoaviram 跑了 10 天、大多數測試失敗——根本原因之一就是 spec 裡有太多模糊空間讓 agent 填，而每次 implement 填的方式不一樣。

**Q：復盤說 SDD 沒有改善基準，這樣算失敗嗎？**

A：不算。Daniel Westheide（INNOQ，2026 年 3 月）說得直接：SDD 和 DDD 撞到同一道牆——如果組織沒有能溝通的領域專家，再好的 spec 工具都救不了你。François Zaninotto（Marmelab CEO，2025 年 11 月）認為 SDD「大型現有 codebase 基本無用」，他的論點有具體案例支撐。你的復盤如果能說「在 X 條件下 SDD 沒有效，因為 Y」，這比「SDD 超好用」更有價值——對你自己、對讀這份復盤的人都是。

---

## 9. 本課整合回顧

做完這個 Final Project，你應該對以下問題有自己的答案：

- SDD 的「spec 是第一公民」和 DDD 的「領域模型是第一公民」，兩者是什麼關係？（提示：[Ch 33](./33-ddd-sdd-same-fight.md)、[Ch 36](./36-domain-model-as-spec-backbone.md)）
- EARS 的五種句型，你在實際寫需求時哪一種最難用準確？為什麼？
- Event Storming 找到的 Domain Event，在 spec 的 Acceptance Criteria 裡扮演什麼角色？
- spec drift 最容易在什麼時候發生？你設計了什麼機制來偵測它？
- 你在信任階梯的哪一層停下來？這個決定的成本和收益各是什麼？

---

## 自我檢核

這些問題要用自己的話回答，不是回去翻 spec 找答案：

- [ ] 不看 glossary.md，你能用一句話解釋你產品裡最重要的兩個 Aggregate，以及它們的 invariant？
- [ ] 面試被問「你在這個專案裡怎麼用 EARS 寫需求？」，你能舉一個具體的例子，說明 EARS 比純自然語言好在哪？
- [ ] 你的 spec 和最終程式碼，在 Domain Event 的命名上是否完全一致？如果不是，為什麼會漂移？
- [ ] 如果你的 PM 要加一個新功能，你的 SDD 流程會從哪一步重新開始？為什麼不是從頭？
- [ ] 復盤裡你認為 SDD 最大的摩擦點是什麼？這個摩擦點在更大或更小的團隊裡會放大還是縮小？

---

## 延伸閱讀

**[From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need](https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need)** — Annegret Junker（codecentric，2026 年 3 月）。這是本 Final Project 的最強實務基礎：一個具體的食譜平台案例，v1 vs v2 的 schema 數量比較（3 vs 9），以及「建模品質直接決定生成品質」的示範。從 Larder 案例的 v1/v2 對比讀起。

**[Spec-Driven Development is Domain-Driven Design's Impatient Cousin](https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/)** — Daniel Westheide（INNOQ，2026 年 3 月）。SDD 和 DDD 共享同一道牆的最清楚論述：沒有真正能溝通的領域專家，兩者都救不了你。讀「impatient cousin」那段和「upfront interview vs iterative discovery」的對比。本 Final Project 復盤維度 6b 和 6c 的思考框架直接來自這篇。

**[Spec-Driven Development | Thoughtworks Technology Radar Vol 34](https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development)** — Thoughtworks（2025 年 11 月）。SDD 目前的評級（Assess，非 Adopt）和具體警告。讀這篇讓你的復盤有一個可以校對的外部基準。

**[Waterfall Strikes Back](https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html)** — François Zaninotto（Marmelab，2025 年 11 月）。最有條理的 SDD 批評之一：Context Blindness、Markdown Madness、Double Code Review 三個失敗模式，以及「大型現有 codebase 基本無用」的論點。讀你的 spec 和工具的 Markdown 輸出時對比這篇的描述。

**[How Amazon Web Services Uses Formal Methods](https://cacm.acm.org/research/how-amazon-web-services-uses-formal-methods/)** — Newcombe et al.（ACM CACM，2015 年）。AWS 工程師親筆記錄 TLA+ 在 S3、DynamoDB、EBS 找到測試沒找到的 design bug 的案例。如果你考慮進階延伸裡的「形式化驗證 invariant」方向，這篇是最強的動機說明。免費 PDF 也在 https://lamport.azurewebsites.net/tla/formal-methods-amazon.pdf。

**[INVEST in Good Stories, and SMART Tasks](https://xp123.com/invest-in-good-stories-and-smart-tasks/)** — Bill Wake（2003 年 8 月）。六個字母背後的真正意思：Testable 不是「可以測」，是「我對自己的需求夠清楚，才能寫出測試」。一頁讀完，重讀你在階段 2a 寫的 User Story，看哪些 T 沒有達到。

**[EARS: Easy Approach to Requirements Syntax（官方指南）](https://alistairmavin.com/ears/)** — Alistair Mavin（EARS 發明人）。五種句型的典型範例和通用模板，從噴射引擎控制系統的真實 requirements 抽象而來。你在 Stage 2b 遇到任何句型疑問，這是第一查閱點，不是 Google。

**[Ubiquitous Language（bliki）](https://martinfowler.com/bliki/UbiquitousLanguage.html)** — Martin Fowler（引用 Eric Evans，2006 年 10 月）。一頁，說清楚通用語言的根本理由：「software doesn't cope well with ambiguity」。在你把 glossary.md 放進 agent context 之前讀一遍，感受這句話的重量。

---

這門課的核心命題是：在 AI 把實作瓶頸推向意圖的時代，清楚表達你要什麼，比知道怎麼寫程式碼更重要——而領域建模和需求工程正是「清楚表達」的技術工具。這個 Final Project 是你把這個命題從理論落地到一個真實小產品的地方。

做完之後，你對 SDD 的判斷應該比課程開始時更有根據——無論那個判斷是「這在我的情境下有效」，還是「這在我的情境下不值得投入」，都是這門課想給你的東西。

[回到課程地圖](./README.md)
