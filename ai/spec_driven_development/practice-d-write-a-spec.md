# 練習 D — 把需求＋領域模型寫成一份完整的 spec

> **目標**：整合 Part 2/3：把一個功能的 EARS 需求 + 領域模型寫成一份結構完整、AI 可實作的 `spec.md`。

## 背景與動機

到目前為止，我們已經分別學過兩套工具。Part 2 的需求工程教我們把模糊的意圖轉成有結構的句子，特別是 EARS（Easy Approach to Requirements Syntax）的五種句型；Part 3 的 DDD（Domain-Driven Design）教我們識別領域中的實體（Entity）、值物件（Value Object）與聚合（Aggregate），以及如何在有界脈絡（Bounded Context）內維護一套通用語言（Ubiquitous Language）。

問題是：這兩套工具在絕大多數團隊裡活在平行世界。需求分析師寫需求文件；架構師畫領域模型；工程師只看 Jira 票；AI coding agent 拿到的不知是哪個版本的描述。最後三份「真相」互相矛盾，spec 比 code 更早腐化。

> 如果你對 EARS 五種句型還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。  
> 如果你對 Entity / Aggregate 還不確定，先回看 [Ch 19 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md)。

這個練習要你做的事就一件：把需求與領域模型**合而為一**，輸出一份 AI agent 能拿著直接動手的 `spec.md`。這是 Spec Kit 的 `/speckit.specify` 命令在背後自動生成的文件格式的手工版本——你先手寫一遍，才能真正理解自動生成版本哪裡在幫你、哪裡在欺騙你。

```
模糊意圖                 本練習的任務
────────                 ──────────────────────────────────────────
PM 的 Slack 訊息        → EARS 需求（When / While / If...then）
Event Storming 貼紙     → 領域物件清單（Entity / VO / Aggregate）
                         ↓
                   一份 spec.md
                   ─────────────
                   §1 功能目標（What & Why）
                   §2 通用語言詞彙表
                   §3 領域模型（聚合 + 關係）
                   §4 EARS 需求（按情境分組）
                   §5 驗收條件（Given-When-Then）
                   §6 非功能需求
                   §7 明確排除項
```

這個結構不是某個工具的專利，而是你在閱讀 GitHub Spec Kit 的 `spec-template.md`、AWS Kiro 生成的 `requirements.md + design.md` 之後，會發現它們收斂到同一套骨架。

---

## 任務規格

### 情境：線上圖書館借閱系統的「借書」功能

你的 PM 在 Slack 裡留了這段話：

> 「會員可以借書。每本書有庫存數量，借光就不能再借。一次最多借五本，逾期未還要收罰金。記得也要能看借閱紀錄。」

這段話觸犯了 EARS 論文所歸納的自然語言八大病症中的至少五種：模糊性（ambiguity）、不完整性（omission）、詞義不清（vagueness）、過度簡化（complexity 的反面）、缺少異常處理（omission of unwanted behaviour）。

你的任務是把這段話擴展成一份 `spec.md`，滿足以下所有要求：

### 精確輸入

- 上面那段 PM 的原始需求描述（唯一的原始來源，不可自行補充「顯然有的」功能）
- 你自己在 Event Storming 中整理出來的領域事件清單（參考下方「如果你卡住了」一節）

### 精確輸出

一個 Markdown 檔案 `spec.md`，包含以下七個章節，每章節都有對應的驗收標準：

| 章節 | 最低要求 |
|------|----------|
| §1 功能目標 | 用一段話說明 What（做什麼）與 Why（對誰有價值） |
| §2 通用語言詞彙表 | 至少 6 個術語，每個附一句非技術解釋 |
| §3 領域模型 | ASCII 方塊圖或表格，標示 Entity / VO / Aggregate Root |
| §4 EARS 需求 | 至少 8 條，涵蓋全部 5 種句型（Ubiquitous / Event-driven / State-driven / Optional / Unwanted-behaviour）|
| §5 驗收條件 | 至少 4 個 Given-When-Then 場景，含正常路徑與至少 2 個異常路徑 |
| §6 非功能需求 | 至少 2 條，需具體可量測（不可以只寫「系統應快速回應」）|
| §7 明確排除項 | 至少 2 條，明確寫出「本版本不包含 X」 |

### 限制

- 所有需求句子使用 EARS 格式，不可用純散文描述行為
- §3 領域模型裡的名詞必須和 §2 詞彙表及 §4 需求中的名詞**完全一致**（這就是通用語言）
- §6 非功能需求不得引用 ISO/IEC 25010 的術語，而要翻譯成可測量的指標（例：「P99 回應時間 < 500ms，測試環境 100 並發」）
- 不得在 spec 裡寫任何技術實作細節（不要提 React、PostgreSQL、REST API 等）
- 整份 spec 字數不做下限，但「罰金計算」必須有明確的計算規則（這是 PM 沒有說清楚的，你必須做出選擇並明確標注）

### 驗收標準

你的 `spec.md` 被認為合格，當且僅當：

1. 把它丟給一個沒有看過這個練習的同學，對方能從 §4 推導出 §5，不需要問你任何問題
2. §3 的聚合邊界沒有任何物件「跨越」兩個聚合（借閱紀錄和庫存屬於哪個聚合是這題的核心判斷）
3. `[NEEDS CLARIFICATION]` 這個標籤恰好用在你**無法從現有資訊得出答案**的地方，不多不少
4. §7 至少排除了一項「你覺得 PM 想要但沒說的功能」

---

## 期望輸出範例

下面是一個**局部**範例，用來校準格式，不是完整解答：

### §1 功能目標（範例片段）

```markdown
## §1 功能目標

借書（Borrow Book）功能讓圖書館會員（Member）能夠在線上借閱實體書籍。
核心價值：會員無需親臨圖書館即可確認某書是否有庫存可借。
```

### §2 通用語言詞彙表（範例片段）

```markdown
## §2 通用語言詞彙表

| 術語 | 定義 |
|------|------|
| 會員（Member） | 已完成帳號驗證的圖書館用戶；具有唯一的會員編號 |
| 借閱（Borrowing） | 一筆將一本書的一冊與一位會員關聯的紀錄；有借出日、應還日 |
| 書目（BookTitle） | 描述一本書的元資料（書名、ISBN、作者）；一個書目可以有多冊 |
| 冊（BookCopy） | 一本書的一本實體，有唯一序號；狀態為「在庫」或「借出」|
| 逾期（Overdue） | 借閱的應還日早於今日且書尚未歸還的狀態 |
| 罰金（Fine） | [NEEDS CLARIFICATION] PM 未說明計算方式；本 spec 暫定逾期每日 NT$5，待確認 |
```

### §3 領域模型（範例片段）

```markdown
## §3 領域模型

聚合 A：BorrowingAggregate
  根：Borrowing（Entity，identity = BorrowingId）
  內部：Fine（Value Object，amount + currency，僅在逾期後存在）

聚合 B：BookInventoryAggregate
  根：BookTitle（Entity，identity = ISBN）
  內部：BookCopy（Entity，identity = CopyId，state: Available | CheckedOut）

引用關係（跨聚合，by ID）：
  Borrowing → MemberId（Member 是另一個 Bounded Context 的聚合）
  Borrowing → CopyId
  Borrowing → ISBN

限制：Borrowing 不持有 BookCopy 物件，只持有 CopyId（Vernon 規則三：跨聚合以 ID 引用）
```

### §4 EARS 需求（範例 3 條）

```markdown
## §4 EARS 需求

### REQ-001（Ubiquitous）
The system shall display the number of available copies for each BookTitle on the search results page.

### REQ-002（Event-driven）
When a Member submits a borrow request for a BookCopy, the system shall create a Borrowing record with a DueDate set to 14 days from today.

### REQ-007（Unwanted-behaviour）
If a Member's active Borrowing count is equal to or greater than 5, then the system shall reject the borrow request and display the message "Borrowing limit reached (5/5)."
```

---

## 如果你卡住了

### 方向提示一：先跑一場迷你 Event Storming

用 10 分鐘，在紙上把所有「發生了某件事」的句子寫成過去式貼紙：
- 書被搜尋到（BookSearched）
- 借閱請求被提交（BorrowRequestSubmitted）
- 借閱紀錄被建立（BorrowingCreated）
- 書冊狀態被更新為「借出」（BookCopyStatusUpdated）
- 借閱到期（BorrowingExpired）
- 罰金被計算（FineCalculated）
- 書被歸還（BookReturned）

這些事件會告訴你哪些物件**一定要存在**，以及哪個物件是「核心」（需要 ID 追蹤的）、哪個是「描述」（可以是 Value Object）。

> 如果你對 Event Storming 的流程還不熟，先回看 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

### 方向提示二：聚合邊界是最難的決定

`Borrowing` 和 `BookCopy` 要放在同一個聚合，還是不同聚合？

考慮這個情境：同一本書的兩個冊同時被兩個會員借走。若把它們放進同一個聚合，就需要分散式鎖或樂觀鎖；若放在不同聚合，就需要接受最終一致性。兩個選擇都可以，但必須在 spec 裡**明確說清楚**，包括你選了哪個以及理由。

> 如果你對 Vernon 的四個聚合設計規則還不熟，先回看 [Ch 19 戰術建模](./19-entities-value-objects-aggregates.md)。

### 方向提示三：EARS 的五種句型映射到五種情境

| EARS 句型 | 用在哪裡 |
|-----------|----------|
| Ubiquitous | 系統的永久屬性（「系統應顯示...」）|
| Event-driven（When） | 使用者動作觸發系統反應 |
| State-driven（While） | 系統在某狀態下的持續行為 |
| Optional-feature（Where） | 只有特定設定或角色才有的行為 |
| Unwanted-behaviour（If...then） | 錯誤、邊界、拒絕路徑 |

「每本書一次最多借五本」用哪種句型？是 Unwanted-behaviour（If 超過 5 本 then 拒絕）。「會員可以看借閱紀錄」用哪種句型？是 Event-driven（When 會員請求紀錄頁面）。

### 方向提示四：`[NEEDS CLARIFICATION]` 是你的誠實標記

EARS 句型要求你明確化每一個參數（罰金多少？應還期限幾天？），但 PM 沒有給這些數字。正確做法不是猜測後裝作知道，而是：

1. 做一個**合理假設**（例：14 天借閱期、逾期每日 NT$5）
2. 在當下的需求旁邊加上 `[NEEDS CLARIFICATION: 待 PM 確認逾期費率]`

這個標記在 GitHub Spec Kit 的 `spec-template.md` 裡是一個正式的記號，讓 AI agent 知道「這裡我不確定，請不要直接實作」。

### 方向提示五：非功能需求要讓你的測試工程師能寫測試

「系統應快速回應」—— 你的測試工程師怎麼寫測試？他不知道「快」是 200ms 還是 2 秒。改成：「在測試環境 50 個並發借閱請求下，P95 回應時間應低於 800ms」——現在可以測了。

---

## 實作步驟建議

### Step 1：Event Storming（10–15 分鐘）

在紙上或白板上把借書流程的所有 Domain Event 列出來。確認哪些事件改變了哪些物件的狀態。

### Step 2：識別聚合邊界

根據 Step 1 的事件，決定哪些物件必須在同一個事務邊界內保持一致。寫下你的決策理由（一到兩句即可），這就是 spec 的架構核心。

### Step 3：寫 §2 詞彙表和 §3 領域模型

先寫詞彙表再畫模型，因為詞彙表逼你把每個術語說清楚，你會發現自己其實還不確定某些術語的邊界。例：「借閱」指的是一個請求（request），還是一段關係（relationship）？在 Event Storming 裡，它是「BorrowingCreated」這個事件後才存在的一筆紀錄。

### Step 4：把需求翻譯成 EARS 句型

逐條把 PM 的描述展開成 EARS 格式。目標是每一個「行為分支」至少對應一條 EARS 需求，包括正常路徑、邊界條件、錯誤路徑。PM 的原文有多少個「應該」「可以」「不能」，你至少要有等量的 EARS 句子。

### Step 5：把每個核心 EARS 需求翻成 Given-When-Then

不是每一條 EARS 都需要 Given-When-Then，但有副作用的需求（借書、還書、計算罰金）都應該有。Given 描述前置狀態，When 描述觸發事件，Then 描述後果——與 §3 的聚合狀態變化對應。

---

## 完整參考解答

**請先獨立完成你的 spec.md，再來看這裡。** 參考解答給的是一種合理的選擇，不是唯一正確答案。聚合邊界和罰金計算規則都有多種合理方案。

<details>
<summary>展開參考解答（spec.md 完整版）</summary>

```markdown
# spec.md — 借書功能（Borrow Book Feature）

**版本：** 0.1 草稿  
**最後更新：** 2026-06-30  
**作者：** [你的名字]  
**狀態：** 待 PM 確認 [NEEDS CLARIFICATION] 項目

---

## §1 功能目標

**What（做什麼）：**  
借書（Borrow Book）功能讓已驗證的會員（Member）能夠線上提交借閱請求，
由系統確認庫存並建立借閱紀錄（Borrowing），同時更新對應書冊（BookCopy）
的狀態為「借出（CheckedOut）」。

**Why（對誰有價值）：**  
會員可在不親臨圖書館的情況下，確認書是否有庫存可借，並直接在線上完成借閱登記。
圖書館員工可即時掌握目前的借閱狀態，無需手動核對實體台帳。

**本版本範圍：**  
僅涵蓋「借書」的主要流程（borrow）及其直接相關的狀態變化，不包含歸還流程、
催繳通知或罰金繳納，這些列於 §7 排除項。

---

## §2 通用語言詞彙表

本詞彙表中的術語在 §3、§4、§5 中的使用必須保持一致。

| 術語 | 定義 |
|------|------|
| 會員（Member） | 已完成帳號驗證的圖書館用戶，具有唯一的 MemberId。 |
| 書目（BookTitle） | 描述一本書的元資料：書名、ISBN（唯一）、作者、出版年。一個書目可對應多冊。 |
| 書冊（BookCopy） | 一本書目的一本實體，具有唯一的 CopyId。同一時刻狀態為「在庫（Available）」或「借出（CheckedOut）」之一。 |
| 借閱（Borrowing） | 一筆將一冊（BookCopy）與一位會員（Member）關聯的紀錄；包含借出日（BorrowedAt）、應還日（DueDate）。 |
| 應還日（DueDate） | 借閱建立日加上借閱天數（[NEEDS CLARIFICATION: 暫定 14 天，待 PM 確認]），會員應於此日前歸還。 |
| 逾期（Overdue） | 當今日日期晚於某 Borrowing 的 DueDate 且該 Borrowing 尚未標記為「已還（Returned）」時，該 Borrowing 即為逾期。 |
| 罰金（Fine） | 逾期時按日累計的費用。[NEEDS CLARIFICATION: 暫定每逾期一日計 NT$5，待 PM 確認費率及上限] |
| 借閱上限（Borrowing Limit） | 一位會員同時可持有的最大 Borrowing 數量，本 spec 定為 5 筆。[NEEDS CLARIFICATION: 待 PM 確認是否可設定為 0] |

---

## §3 領域模型

### 聚合劃分

```
聚合 A：BorrowingAggregate
────────────────────────────────────────────────
  根（Aggregate Root）：
    Borrowing（Entity）
      - BorrowingId   : 唯一識別碼
      - MemberId      : 對 Member 的跨聚合 ID 引用
      - CopyId        : 對 BookCopy 的跨聚合 ID 引用
      - BorrowedAt    : 借出時間戳（Value Object）
      - DueDate       : 應還日期（Value Object）
      - Status        : BorrowingStatus（Value Object，enum: Active | Returned）
      - Fine          : Fine（Value Object，僅 Overdue 狀態下有值）
          └─ amount   : Decimal
          └─ currency : String（預設 TWD）

聚合 B：BookInventoryAggregate
────────────────────────────────────────────────
  根（Aggregate Root）：
    BookTitle（Entity）
      - ISBN          : 唯一識別碼（字串）
      - title         : String
      - author        : String
      - copies[]      : BookCopy（Entity，在聚合內持有物件引用）
          └─ CopyId   : 唯一識別碼
          └─ status   : CopyStatus（Value Object，enum: Available | CheckedOut）
```

**設計決策：** `Borrowing` 與 `BookCopy` 分屬兩個聚合，以 ID 引用而非物件引用（依據 Vernon 四規則之三）。後果：當兩位會員同時對最後一冊提交借閱請求時，需以樂觀鎖（Optimistic Lock）在 `BookInventoryAggregate` 層處理競態。這比把它們塞進同一聚合更可維護，但需要在 §6 明確標記一致性需求。[NEEDS CLARIFICATION: 請架構師確認樂觀鎖 vs 訊息佇列方案]

### 物件關係圖（ASCII）

```
  Member ──────────────────────────────────────────────────────────────┐
  (另一 Context)                                                       │ MemberId
                                                                       ↓
  BookInventoryAggregate        BorrowingAggregate
  ┌────────────────────────┐    ┌──────────────────────────────────┐
  │ BookTitle (root)       │    │ Borrowing (root)                 │
  │  - ISBN ←──────────────┼────┼── ISBN (引用)                    │
  │  - title               │    │  - BorrowingId                   │
  │  - copies[]            │    │  - CopyId (引用) ───────────────┐│
  │    BookCopy            │    │  - MemberId (引用)               ││
  │    - CopyId ───────────┼────┼──────────────────────────────── ┘│
  │    - status            │    │  - BorrowedAt                    │
  └────────────────────────┘    │  - DueDate                       │
                                │  - Status                        │
                                │  - Fine (VO, nullable)           │
                                └──────────────────────────────────┘
```

---

## §4 EARS 需求

### 可用性（Ubiquitous）

**REQ-001**  
The system shall display the total number of Available BookCopies for each BookTitle on every page where a BookTitle is listed.

**REQ-002**  
The system shall display the Member's current active Borrowing count alongside the Borrowing Limit (e.g., "3 / 5") on the Member's account page.

### 事件驅動（Event-driven）

**REQ-003**  
When a Member submits a borrow request for a BookTitle, the system shall select one Available BookCopy of that BookTitle and create a Borrowing record with BorrowedAt set to the current timestamp and DueDate set to BorrowedAt plus 14 days. [NEEDS CLARIFICATION: 14 天是暫定，待 PM 確認]

**REQ-004**  
When a Borrowing record is successfully created, the system shall update the corresponding BookCopy's status from Available to CheckedOut.

**REQ-005**  
When a Member requests to view their borrowing history, the system shall return all Borrowing records associated with that MemberId, ordered by BorrowedAt descending.

### 狀態驅動（State-driven）

**REQ-006**  
While a Borrowing's Status is Active and today's date is later than its DueDate, the system shall calculate and attach a Fine to that Borrowing equal to the number of overdue days multiplied by NT$5. [NEEDS CLARIFICATION: NT$5/日 是暫定費率，待 PM 確認；並確認是否有罰金上限]

### 選項功能（Optional-feature）

**REQ-007**  
Where the Member holds a "Premium" membership tier, the system shall allow a Borrowing Limit of 10 instead of 5. [NEEDS CLARIFICATION: PM 未提到 Premium 等級；本條需 PM 確認是否納入]

### 非預期行為（Unwanted-behaviour）

**REQ-008**  
If a Member's current active Borrowing count is equal to or greater than 5, then the system shall reject the borrow request and return the error message "已達借閱上限（5/5）。"

**REQ-009**  
If a borrow request is submitted for a BookTitle with zero Available BookCopies, then the system shall reject the request and return the message "此書目目前無在庫書冊可借。"

**REQ-010**  
If two concurrent borrow requests attempt to reserve the last Available BookCopy of the same BookTitle, then the system shall fulfil exactly one request and return a "無在庫書冊" error to the other.

---

## §5 驗收條件（Given-When-Then）

### AC-01：正常借書路徑

```gherkin
Scenario: 會員成功借書
  Given 會員 M001 目前有 2 筆 Active Borrowings
  And 書目 ISBN-9789 有 3 冊狀態為 Available
  When M001 提交對 ISBN-9789 的借閱請求
  Then 系統建立一筆 Borrowing，BorrowedAt 為今日，DueDate 為 14 天後
  And ISBN-9789 的 Available 書冊數量減為 2
  And M001 的 Active Borrowing 數量變為 3
```

### AC-02：借閱上限拒絕

```gherkin
Scenario: 已借 5 本時提交借閱請求被拒絕
  Given 會員 M002 目前有 5 筆 Active Borrowings
  And 書目 ISBN-1234 有 10 冊狀態為 Available
  When M002 提交對 ISBN-1234 的借閱請求
  Then 系統回傳錯誤「已達借閱上限（5/5）。」
  And 未建立任何新的 Borrowing 紀錄
  And ISBN-1234 的 Available 書冊數量不變
```

### AC-03：無庫存拒絕

```gherkin
Scenario: 書目無在庫書冊時提交借閱請求被拒絕
  Given 會員 M003 目前有 0 筆 Active Borrowings
  And 書目 ISBN-5678 的所有書冊狀態均為 CheckedOut（共 2 冊）
  When M003 提交對 ISBN-5678 的借閱請求
  Then 系統回傳錯誤「此書目目前無在庫書冊可借。」
  And 未建立任何新的 Borrowing 紀錄
```

### AC-04：查看借閱紀錄（含逾期）

```gherkin
Scenario: 會員查看借閱紀錄，其中含有逾期項目
  Given 會員 M004 有一筆 Borrowing，DueDate 為 5 天前，Status 為 Active
  When M004 請求查看個人借閱歷史
  Then 系統回傳的借閱清單中包含該筆 Borrowing
  And 該 Borrowing 附帶 Fine，金額為 NT$25（5 天 × NT$5）
  And 該 Borrowing 的顯示狀態為「逾期（Overdue）」
```

---

## §6 非功能需求

**NFR-001（效能）：**  
在測試環境（單一服務實例，4 vCPU / 8 GB RAM）下，同時 50 個並發借閱請求的 P95 回應時間應低於 800ms，P99 應低於 1500ms。

**NFR-002（一致性）：**  
兩個並發請求競爭同一書冊（REQ-010 的場景）時，恰好一個請求成功，另一個收到明確錯誤，不得出現「雙重借出」（double booking）的情況。系統可以接受最多一次重試，但不得靜默失敗。

---

## §7 明確排除項

本版本（v0.1）**不包含**以下功能，後續版本再議：

1. **歸還流程（Return Book）：** 包含書冊狀態從 CheckedOut 改回 Available、Borrowing 標記為 Returned、Fine 清算。
2. **罰金繳納：** 逾期罰金的收款、免罰申請、豁免機制。
3. **預約系統（Reservation）：** 當書目無在庫時讓會員排隊等候。
4. **推薦功能：** 根據借閱紀錄推薦類似書目。
5. **電子書借閱：** 本 spec 僅涵蓋實體書冊（BookCopy）。
```

---

### 解說：幾個關鍵選擇

**為什麼 `BookCopy` 在 `BookInventoryAggregate` 裡用物件引用，而 `Borrowing` 只用 `CopyId`？**

因為「哪幾冊書目前在庫」是 `BookTitle` 這個根實體需要一次性維護的不變式（invariant）：可用冊數 = copies.filter(Available).count()。如果把 `BookCopy` 放到外面，這個計算就需要跨聚合查詢，破壞了聚合作為一致性邊界的意義。而 `Borrowing` 不需要即時知道書冊的當前狀態，它只記錄「哪本書被誰借走了」這個過去事實，所以只持有 `CopyId` 就夠了。

**`[NEEDS CLARIFICATION]` 用了幾次，用在哪裡？**

共 5 處：借閱天數（14 天）、罰金費率（NT$5/日）、罰金上限、Premium 等級、樂觀鎖方案。這些全部是 PM 的原文中沒有給出數字或決策的地方。其他地方（如「最多借五本」）PM 有明確說，就不加這個標記。

</details>

---

## 測試用例表

用下表對照你寫完的 spec 是否覆蓋這些情境：

| 情境 | 期望 EARS 句型 | 期望 AC | 常見遺漏 |
|------|----------------|---------|----------|
| 正常借書 | Event-driven (REQ-003) | AC-01 | 未更新 BookCopy 狀態 |
| 超過 5 本 | Unwanted-behaviour (REQ-008) | AC-02 | 未確認「5」是否 inclusive |
| 零庫存 | Unwanted-behaviour (REQ-009) | AC-03 | 未處理「剛好最後一冊」競態 |
| 併發最後一冊 | Unwanted-behaviour (REQ-010) | 需另加 | 最常被忽略的場景 |
| 查看借閱紀錄 | Event-driven (REQ-005) | AC-04 | 未說明排序規則 |
| 逾期罰金計算 | State-driven (REQ-006) | AC-04 | 未定義計算起始日（借出日？應還日的次日？）|
| 罰金費率未定義 | — | — | 未加 [NEEDS CLARIFICATION] 標記 |

---

## 延伸挑戰

完成基礎版本後，可以挑戰以下進階任務：

1. **邊界探索：** 如果一位會員的第 4 本書快到期，他應不應該能在歸還前預先借第 6 本？把這個邊界情況加進 §4（一條新的 Unwanted-behaviour）和 §5（一個新的 AC）。

2. **事件發布：** DDD 建議重要的領域動作應發布 Domain Event（例：`BorrowingCreated`）。在 §3 加一欄「發布的 Domain Events」，並在每條 Given-When-Then 的 Then 子句裡補充「And 系統發布 BorrowingCreated 事件」。

3. **多語言詞彙衝突：** 如果系統同時對接一個外部的「讀者服務系統」，那個系統把「Borrowing」稱為「借用合約（LoanContract）」。在 §3 加一個 Context Mapping 說明，標示兩個 Context 之間用哪個整合模式（Anti-Corruption Layer？Conformist？）處理術語衝突。

4. **把你的 spec 喂給 AI：** 把你的 `spec.md`（不包含參考解答）直接貼給你使用的 AI coding agent，請它根據 spec 列出實作計劃。觀察 AI 在哪些地方「自作主張」填補了你沒有說清楚的地方，這些就是你的 spec 還需要補強的地方。

---

## 自我檢核

完成本練習後，用自己的話（不翻筆記）回答這些問題：

- [ ] 用一句話解釋：為什麼「借閱（Borrowing）」需要是一個 Entity 而不是 Value Object？（提示：想想兩位會員可能各自借了同一書目的不同冊）
- [ ] EARS 的 Unwanted-behaviour 句型和 Given-When-Then 的 AC 有什麼不一樣的用途？（它們不是同一件事的重複）
- [ ] 你的 spec 裡有沒有任何一句話，是你在「猜 PM 的意思」卻沒有加 `[NEEDS CLARIFICATION]` 的？如果有，補上去。
- [ ] 如果有人問你「為什麼 `BookCopy` 不放在 `BorrowingAggregate` 裡」，你能用兩句話回答嗎？面試被問會怎麼答？
- [ ] 你的 §6 非功能需求，測試工程師能不能直接用它寫一個 k6 或 JMeter 測試腳本？如果不能，重寫。

---

## 延伸閱讀

1. **EARS: Easy Approach to Requirements Syntax — Alistair Mavin（官方指南）**  
   URL: https://alistairmavin.com/ears/  
   從哪裡開始：直接看「EARS Patterns」一節，逐一比對你 §4 裡的每一條需求是否符合句型規則。  
   與本練習的關係：你 §4 的每一條 EARS 需求都應該能在這頁找到對應的模板。

2. **Domain-Driven Design Reference — Eric Evans（免費）**  
   URL: https://www.domainlanguage.com/ddd/reference/  
   從哪裡開始：Entity、Value Object、Aggregate 的定義章節；把 Evans 的原文定義和你 §2 的詞彙表對照，確認你有沒有用錯術語。  
   （注意：PDF 連結有時需在瀏覽器手動開啟，查證日期 2026-06-30）  
   與本練習的關係：Evans 的定義是這個練習裡所有領域物件分類的最終裁定標準。

3. **Implementing Domain-Driven Design — Vaughn Vernon，InformIT 節錄**  
   URL: https://www.informit.com/articles/article.aspx?p=2020371&seqNum=3  
   從哪裡開始：「Rule: Design Small Aggregates」和「Rule: Reference Other Aggregates by Identity」這兩節。  
   與本練習的關係：直接支撐你在 §3 對「跨聚合用 ID 引用而非物件引用」的設計決策。

4. **GitHub Spec Kit — spec-driven.md（官方方法論文件）**  
   URL: https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md  
   從哪裡開始：「Power Inversion」和 `[NEEDS CLARIFICATION]` 的說明段落。  
   與本練習的關係：這個練習你手工完成的 `spec.md` 就是 `/speckit.specify` 自動生成的文件結構，對照一下你的版本和 Spec Kit 模板的差異。（查證日期 2026-06-30，命令名稱版本相依）

5. **Spec-driven development: Unpacking one of 2025's key new AI-assisted engineering practices — Liu Shangqi（Thoughtworks）**  
   URL: https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices  
   從哪裡開始：「Why structured input reduces hallucination」段落。  
   與本練習的關係：解釋了為什麼你花時間手寫一份結構化的 spec，會讓 AI coding agent 的輸出品質明顯不同於直接把 PM 的 Slack 訊息貼給它。

→ [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)
