# Ch 34 — 通用語言作為 LLM 的詞彙表

> **目標**：理解如何把 DDD 通用語言（Ubiquitous Language）落地成一份 glossary，注入 agent 的 context（constitution、steering、AGENTS.md、CLAUDE.md），用以降低幻覺與命名漂移，並能在自己的專案中動手實作這套機制。

> 如果你對 DDD 通用語言的基本概念還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)。
> 如果你對 SDD 與 DDD 的整體關係還有疑問，先回看 [Ch 33 一個問題，兩個時代：DDD 與 SDD 是同一場仗](./33-ddd-sdd-same-fight.md)。

---

## 問題從哪裡來

2024 年以前，大多數團隊對「餵 AI 詞彙」這件事沒有感覺。反正 prompt 寫一寫，AI 就生出東西來了——它那麼聰明，應該懂「訂單」是什麼。

但 LLM 不懂「你的訂單」。它懂的是語料庫裡所有人的「訂單」：電商訂單、工廠採購單、餐廳桌次單、律師委任書。它會在這些語義之間隨機滑動，每次生成都可能滑到不同位置。

Martin Fowler 在 2006 年替 Eric Evans 定義通用語言時留下的那句話放到 2026 年仍然精確：**「software doesn't cope well with ambiguity」**（軟體對歧義的容忍度很低）。LLM 是軟體，而且是歧義的放大器：模糊的 prompt 進去，它不是輸出錯誤，而是輸出一個「表面正確、語義漂移」的結果，讓你當下察覺不到問題。

這就是「命名漂移（naming drift）」和「幻覺（hallucination）」的交叉地帶——agent 沒有產生胡說八道的事實，而是用了一個看起來合理、但在你的領域裡意義不同的詞彙，把整個設計引向錯誤方向。

---

## 心智圖像：詞彙表作為對齊工具

把 LLM 想成一個剛入職的顧問，英文很好、見過很多專案、但對你們公司一無所知。你把他拉進每一個設計會議前，有兩種做法：

```
做法 A：「你就直接給我出設計稿吧，應該懂的。」
         ↓
         他會把你的「Shipment」設計成電商包裹，
         但你們是醫療物流，Shipment 是冷鏈管制單位。

做法 B：先給他一份公司詞彙表：
         Shipment = 一次冷鏈運輸委託，包含溫度閾值和品項清單
         Item     = 藥品 SKU，不是電商商品
         Leg      = 一段承運區間（不是旅遊行程的腳）
         ↓
         他看到 Shipment 時就不會亂類比。
```

「詞彙表作為對齊工具」就是做法 B 的系統化版本——把通用語言從白板便利貼或 Confluence 頁面，轉成機器可讀的 glossary，塞進 agent 的 context 最前面。

---

## 歷史脈絡：在這之前人們怎麼做

### 第一代：什麼都不做

早期 AI coding 的預設做法是「你懂我的意思吧」——隱含假設 GPT-3/4 對領域術語的理解與開發者一致。短期看起來不錯，因為 LLM 確實能生出「看起來對的東西」；真正的問題在 code review 時才浮現：reviewer 說「這個 Ticket 不是指客服工單，是指音樂會門票啊」。

### 第二代：系統 prompt 裡寫一段話

像是：「你是一個電商後端工程師，這個系統是賣票的……」。比什麼都不做好一點，但：
- 定義散落在 prompt 中間，沒有結構
- 同一個詞在不同 prompt 中可能有不同說法
- 沒有機制確保「下一個月 Claude 還是拿到同一份定義」

### 第三代：結構化 glossary 進 context

2025 年前後，GitHub Spec Kit 的 constitution、AWS Kiro 的 steering、以及 AGENTS.md / CLAUDE.md 這些慣例逐漸成型，提供了把詞彙定義「鉚釘」在 context 起始位置的機制。

Annegret Junker 在 codecentric 的案例研究（2026 年 3 月）提供了迄今最具體的量化印記：在 recipe 平台上，先跑 EventStorming 再提取 glossary 後餵給 LLM，OpenAPI spec 的 schema 從 3 個增加到 9 個（ShoppingList、Meal enums、Diet fields 在沒有 glossary 的版本中完全缺失），而且抓到了一條業務規則（自我評分機制），naive prompt 完全沒有提到這條規則。這是目前「glossary 改善 LLM 輸出」最清楚的可復現演示。

---

## 一份 Glossary 長什麼樣

我們用一個「線上音樂會售票平台」來示範。

### 原始狀態：沒有 glossary 的 prompt

```
我們有一個 Ticket 系統，Ticket 可以 Transfer，
請設計 API。
```

LLM 可能輸出：
```json
POST /tickets/{id}/transfer
{
  "to_user_id": "string",
  "reason": "string"
}
```

在這裡 `Ticket` 被理解為「客服工單轉接」。但我們的業務是「音樂會門票轉讓」，Transfer 有票務法規限制、有二次入場碼失效機制、有限轉次數。這些業務規則不存在於 LLM 的類比裡。

### 結構化 glossary：YAML 格式

```yaml
# glossary.yaml
# 本檔案是本系統通用語言的機器可讀版本。
# 所有 agent 在生成任何設計或程式碼前，必須先讀本檔，
# 並確保輸出使用此處定義的確切術語與語義。

terms:
  Ticket:
    zh: 門票
    definition: >
      一張對應到特定 Event 某個 Seat 的入場憑證。
      具有唯一的 barcode，在 Event 結束前有效。
      不是客服工單（Support Ticket）。
    fields:
      - barcode: string   # 128-bit UUID，一次掃描後 invalidated
      - seat_id: FK → Seat
      - holder_id: FK → User
      - transfer_count: int  # 累積轉讓次數，不得超過 max_transfers
    invariants:
      - barcode 掃描成功後立即標記 used，不可重複使用
      - 若 Event.status = CANCELLED，Ticket 自動進入 REFUNDABLE 狀態

  Transfer:
    zh: 票務轉讓
    definition: >
      Ticket 從一個 holder 轉移到另一個 User 的法律行為。
      每張 Ticket 的 Transfer 次數受 Event.max_transfers 限制。
      Transfer 不是客服工單轉接，不是物流轉運。
    invariants:
      - Ticket.transfer_count < Event.max_transfers 才允許 Transfer
      - Transfer 完成後，舊 holder 的入場碼立即失效，新 holder 收到新 barcode

  Event:
    zh: 演出場次
    definition: >
      一場特定日期、特定場館的音樂會場次。
      與 DomainEvent（領域事件）不同——Event 是業務實體，
      DomainEvent 是系統事件，兩者不可混用。
    fields:
      - max_transfers: int   # 0 = 禁止轉讓
      - status: ENUM(ON_SALE, SOLD_OUT, CANCELLED)

  Seat:
    zh: 座位
    definition: >
      場館中的一個物理座位，由 (section, row, number) 唯一識別。
      與 Ticket 是 1:N 的歷史關係（同一個 Seat 在不同 Event 可以有不同 Ticket），
      但在同一個 Event 中 1:1。
```

這份 YAML 有幾個重要設計決策：
1. `definition` 中明確說「不是 X」——這是最省字的消歧義方式。
2. `invariants` 直接進 glossary——讓 agent 生成 API 時不會漏掉業務規則。
3. 每個 term 有中英對應——防止 LLM 在中文 context 中突然切換語義。

---

## 把 Glossary 注入 Agent Context

三種主流機制，針對不同工具：

### 機制一：GitHub Spec Kit — constitution

> 如果你還不熟 Spec Kit 的 constitution 機制，先回看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)。

在 `.specify/constitution.md` 裡加一個 Glossary 段落：

```markdown
# Constitution

## Project Principles
<!-- ... 其他原則 ... -->

## Ubiquitous Language Glossary
All agents MUST use the following terms with their exact definitions below.
When in doubt about a business term, refer here first.

### Ticket
A single entry credential tied to a specific Seat at a specific Event.
NOT a support ticket. NOT a work order.
Key invariant: barcode is single-use; invalidated on first successful scan.

### Transfer
The legal act of moving a Ticket's holder from one User to another.
Constrained by Event.max_transfers.

### Event
A concert show instance (date + venue). DIFFERENT from DomainEvent (system event).

### Seat
A physical seat identified by (section, row, number).
<!-- ... 其他 terms ... -->
```

這樣每次跑 `/speckit.specify` 或 `/speckit.plan` 時，constitution 都在 context window 的最前面——LLM 看到 Ticket 時，已經讀過「不是客服工單」了。

### 機制二：AWS Kiro — steering files

> 如果你還不熟 Kiro 的 steering 機制，先回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)。

Kiro 支援在 `.kiro/steering/` 放 Markdown 檔，agent 執行前自動讀取：

```bash
.kiro/
  steering/
    glossary.md        ← 通用語言詞彙表
    style-guide.md
    architecture.md
```

`glossary.md` 的格式與 constitution 的 glossary 段落相同。差異在於 Kiro 的 steering 可以按 task 選擇性注入（version-dependent，以官方最新文件為準；查證日期 2026-06-30），而 Spec Kit 的 constitution 是全局的。

### 機制三：AGENTS.md / CLAUDE.md

這是工具無關的通用慣例，在 monorepo 的各層目錄放置 `AGENTS.md`（OpenAI Codex 慣例）或 `CLAUDE.md`（Claude Code 慣例），離哪個 context 近就讀哪個：

```
repo/
├── AGENTS.md              ← 全局 glossary（所有 agent 適用）
├── ticketing/
│   ├── AGENTS.md          ← ticketing context 的術語
│   └── src/
├── venue-mgmt/
│   ├── AGENTS.md          ← venue-mgmt context 的術語（Seat 在這裡有不同欄位）
│   └── src/
```

全局 `AGENTS.md` 範例（節錄）：

```markdown
# Agent Instructions

## Ubiquitous Language
Before generating any code, API design, or documentation, read and apply
the following glossary. These definitions override any general knowledge
you have about these terms.

**Ticket** — Entry credential for a Seat at an Event. Single-use barcode.
Not a support ticket.

**Transfer** — Legal handover of a Ticket between Users.
Constrained by Event.max_transfers (can be 0 = no transfer allowed).

**Event** — A concert show. NEVER confuse with DomainEvent (system event).

## Naming Conventions
- Class names: PascalCase, match glossary terms exactly (Ticket, not TicketEntity)
- Database tables: snake_case plural (tickets, transfers, events)
- REST endpoints: /tickets, /tickets/{id}/transfers
```

子目錄的 `AGENTS.md` 繼承全局並可擴充或覆蓋（確切的繼承語義依工具而定，以官方文件為準；查證日期 2026-06-30）。

---

## 底層機制：為什麼 Glossary 有效

LLM 的行為可以用一個粗糙但有用的模型來理解：它在每個 token 位置，從 context window 中的所有 token 計算注意力分佈，決定下一個 token。Glossary 注入 context 起始位置的效果在於：

1. **增加精確語義的 token 比重**：「Ticket = 入場憑證，非客服工單」的 token 在後續生成中有更高的出現概率，壓低了「客服工單」語義的 token 路徑。
2. **提供明確的消歧義錨點**：當 LLM 遇到「Ticket.transfer()」時，它不需要在語料庫裡找最常見的詮釋，context 裡已經有明確的業務定義。
3. **強制命名一致性**：glossary 裡定義的 `Ticket`（大寫 T）讓 LLM 傾向在生成的 code 中也用 `Ticket` 而非 `ticket_obj`、`t`、`tkt`。

這不是魔法，也不是保證——而是概率工程。Daniel Schleicher（2026 年 1 月）的說法直接：「當我們給 AI agent 模糊的指令，其中 order 可能意指十幾種不同東西時，它放大了混亂；共享語言讓整類錯誤不可能發生。」（此為意譯；原文見 danielschleicher.com，查證日期 2026-06-30）

**重要誠實標注**：「讓整類錯誤不可能發生」是作者的定性論述，非受控實驗結論。Junker 的案例研究（schema 3 → 9）是目前最具體的量化資料，但樣本是一個 case study，不是大規模複現。此領域的量化研究仍在快速累積中（查證日期 2026-06-30）。

---

## Glossary 品質的四個維度

| 維度 | 好的 glossary | 糟糕的 glossary |
|------|---------------|-----------------|
| **精確性** | 定義說「是什麼」且說「不是什麼」 | 只說「訂單就是訂單」 |
| **完整性** | 涵蓋領域核心術語的 invariants 與關係 | 只列名詞，不列規則 |
| **維護性** | 一份 SSOT，所有 context 引同一份 | 散落在 20 個 Confluence 頁面 |
| **可及性** | 機器可讀（YAML/Markdown），放在 repo 裡 | 只存在人腦或 Notion 裡 |

---

## 對比取捨

| 注入方式 | 優點 | 缺點 | 適用場景 |
|----------|------|------|----------|
| Constitution（Spec Kit） | 每次 workflow 自動注入，不會漏 | 全局生效，大型 monorepo 可能攜帶不相關術語 | 單一 bounded context 的專案 |
| Steering（Kiro） | 可按 task 選擇性注入 | 機制 version-dependent，需持續關注 Kiro 文件（查證日期 2026-06-30） | 需要細粒度 context 控制的專案 |
| AGENTS.md / CLAUDE.md | 工具無關、可分層 | 需要在每個相關目錄手動維護 | Monorepo，多個 bounded context |
| System prompt 中的自由文字 | 最彈性 | 沒有結構，難維護，容易隨版本漂移 | 快速原型，不建議長期用 |
| 不注入（隱含假設 LLM 懂） | 零成本 | 命名漂移，幻覺，每次生成可能用不同術語 | 請不要選這個 |

---

## 踩雷集錦

### 雷一：Glossary 只列名詞，忘了 invariants

**錯誤直覺**：「我告訴 LLM 什麼是 Ticket，它自然就知道 Ticket 的規則。」

**正確認識**：LLM 對業務 invariant（轉讓次數上限、barcode 一次性、取消時自動退款）沒有先驗知識。必須在 glossary 的 invariants 欄位明確列出，否則生成的 API 和 service layer 會遺漏這些檢查。Junker 案例中，自我評分業務規則就是因為沒有在 v1 的 context 中出現而被遺漏的。

---

### 雷二：中英文 glossary 各寫各的，不同步

**錯誤直覺**：「英文 code 裡用 Transfer，中文需求文件裡說「轉讓」，大家都懂，LLM 也懂。」

**正確認識**：LLM 在中文和英文 context 中對「轉讓」和 Transfer 的語義映射不一定一致，特別是跨越 prompt 邊界時。最穩的做法是在 glossary 的每個 term 裡同時標 `zh` 和 `en` 欄位，並在 CLAUDE.md / constitution 裡明確說「中英文 term 對應關係見 glossary，不要自行推斷」。

---

### 雷三：Glossary 沒有進 version control，在 Notion 裡更新但 agent 讀舊的

**錯誤直覺**：「Notion 有協作功能，大家在那邊更新就好。」

**正確認識**：agent 讀的是 repo 裡的 glossary 檔案，Notion 的更新對 agent 完全不可見。正確的做法是以 `glossary.yaml`（或等效 Markdown）放在 repo 中作為 SSOT，Notion 頁面如有需要可以是 rendered view，但不是 source。PR 審閱 glossary 變更，就像審閱 API schema 變更一樣——這些都是合約（contract），不是說明文件。

---

### 雷四：一份 Glossary 試圖涵蓋所有 Bounded Context，導致術語衝突

**錯誤直覺**：「全公司一份 glossary 最省事。」

**正確認識**：在 DDD 裡，同一個詞在不同 Bounded Context 中可以有不同語義（`Customer` 在 CRM 和 Billing 裡意義不同）。全局一份 glossary 反而會把跨 context 的語義衝突「硬合並」成一個曖昧定義，比沒有 glossary 更糟。正確做法是每個 bounded context 有自己的 glossary，共用的 shared kernel 術語才在全局 glossary 中定義，並明確標示「這是 Shared Kernel，不是 Billing Context 專屬定義」。

> 關於 Bounded Context 的詳細討論，見 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)。

---

### 雷五：寫了 glossary 就以為 agent 一定會遵守

**錯誤直覺**：「我在 constitution 裡說了 Ticket 不是客服工單，agent 一定不會犯這個錯。」

**正確認識**：Addy Osmani（2026 年 1 月）稱之為「指令詛咒（curse of instructions）」——指令堆得越多，模型對每一條的遵守率就越低（查證日期 2026-06-30）。Glossary 是概率錨點，不是硬性約束。因此必須搭配驗收條件（acceptance criteria）和自動化測試：生成的 schema 裡有沒有用 `ticket`（小寫）而非 `Ticket`？API 有沒有漏掉 `transfer_count < max_transfers` 的邊界檢查？這些驗證需要測試，不能只靠人讀輸出。

---

## 一個可跑的完整範例

環境假設：Claude Code（或任何支援 CLAUDE.md 的工具）。

### 目錄結構

```
concert-platform/
├── CLAUDE.md              ← 全局 glossary + naming conventions
├── glossary.yaml          ← 機器可讀版本（SSOT）
├── ticketing/
│   ├── CLAUDE.md          ← ticketing context 的補充規則
│   └── src/
└── venue-mgmt/
    ├── CLAUDE.md
    └── src/
```

### `glossary.yaml`（完整可用版）

```yaml
# concert-platform/glossary.yaml
# Ubiquitous Language — Single Source of Truth
# 版本控制在 git，Notion / Confluence 頁面是此檔的 rendered view

context: concert-platform
version: "1.3"
last_updated: "2026-06-01"

terms:
  Ticket:
    en: Ticket
    zh: 門票
    definition: >
      Entry credential for a specific Seat at a specific Event.
      Has a single-use barcode (UUID). NOT a support ticket or work order.
    invariants:
      - barcode is invalidated upon first successful scan
      - Ticket.status transitions: ISSUED → USED | CANCELLED | TRANSFERRED_OUT
      - A cancelled Event renders all its Tickets REFUNDABLE automatically
    related: [Seat, Event, Transfer]

  Transfer:
    en: Transfer
    zh: 票務轉讓
    definition: >
      The legal act of reassigning a Ticket's holder from one User to another.
      NOT a support-ticket reassignment. NOT a logistics shipment transfer.
    invariants:
      - Ticket.transfer_count must be < Event.max_transfers
      - On successful Transfer, the original holder's barcode is invalidated
        and the new holder receives a freshly generated barcode
    related: [Ticket, User, Event]

  Event:
    en: Event
    zh: 演出場次
    definition: >
      A concert show instance with a specific date, time, and Venue.
      DIFFERENT from DomainEvent (a system/architecture event).
      Never use "Event" to mean a system event in this codebase.
    invariants:
      - max_transfers >= 0; 0 means transfers are forbidden
      - status: ON_SALE | SOLD_OUT | CANCELLED
    related: [Seat, Ticket, Venue]

  Seat:
    en: Seat
    zh: 座位
    definition: >
      A physical seat identified by (section, row, number) within a Venue.
      In the context of one Event, one Seat maps to exactly one Ticket (1:1).
      Across different Events, the same Seat can have multiple historical Tickets (1:N).
    related: [Venue, Event, Ticket]

  Venue:
    en: Venue
    zh: 場館
    definition: >
      A physical performance location with a defined seating layout.
      Owns the master list of Seats.
    related: [Seat, Event]

naming_conventions:
  classes: "PascalCase matching glossary terms (Ticket, not TicketEntity)"
  database_tables: "snake_case plural (tickets, events, seats)"
  rest_endpoints: "/tickets, /tickets/{id}/transfers"
  domain_events: "Suffix with 'Occurred' or 'Completed' (TicketTransferred, EventCancelled)"
```

### `CLAUDE.md`（全局）

```markdown
# Concert Platform — Agent Instructions

## Priority Order
1. Glossary definitions in this file (and glossary.yaml) override your general knowledge
2. Architecture constraints in CLAUDE.md files closer to the code you're editing
3. The spec files in .specify/ or equivalent

## Ubiquitous Language
**ALWAYS read glossary.yaml before generating any code, API schema, or design document.**

Key disambiguation rules:
- **Ticket** = concert entry credential. NEVER = support ticket.
- **Event** = concert show. NEVER = system/domain event.
- **Transfer** = ticket handover between users. NEVER = logistics or support reassignment.

When you're unsure whether a term you want to use is in the glossary, check glossary.yaml first.

## Naming Conventions (from glossary.yaml)
- Classes: PascalCase matching glossary (Ticket, Transfer, Event, Seat, Venue)
- Tables: snake_case plural (tickets, transfers, events, seats, venues)
- REST: /tickets, /events, /seats — nested: /tickets/{id}/transfers

## What You Must NOT Do
- Do not invent new domain terms not in glossary.yaml without updating the glossary first
- Do not use "Event" for DomainEvent; use "DomainEvent" explicitly
- Do not add columns or fields to domain entities without updating glossary.yaml
```

### 驗證：Prompt 前後的差異

**沒有 glossary 的 prompt**：
```
設計一個 Ticket Transfer API
```

可能輸出（命名漂移範例）：
```python
# 生成的 code 可能這樣寫
class TicketAssignment:  # 不是 Transfer
    def reassign(self, ticket_id, new_assignee):  # 不是 transfer
        # 沒有 transfer_count 檢查
        # 沒有 barcode 重新生成
        pass
```

**有 glossary 的 prompt（透過 CLAUDE.md 注入）**：
```
設計一個 Ticket Transfer API
```

輸出傾向（注意術語對齊）：
```python
class Transfer:  # 對齊 glossary
    def execute(self, ticket: Ticket, new_holder: User) -> Transfer:
        if ticket.transfer_count >= ticket.event.max_transfers:
            raise TransferLimitExceeded(
                f"Ticket {ticket.id} has reached max transfers "
                f"({ticket.event.max_transfers})"
            )
        # invalidate old barcode, generate new one
        ticket.barcode = generate_barcode()
        ticket.holder = new_holder
        ticket.transfer_count += 1
        return Transfer(ticket=ticket, from_user=ticket.previous_holder,
                        to_user=new_holder)
```

這個輸出不保證完全正確，但它：
1. 用了 `Transfer` 而非 `TicketAssignment`
2. 包含了 `transfer_count` 的 invariant 檢查
3. 包含了 barcode 重新生成的步驟
4. 命名與 glossary 對齊

---

## 進階延伸

### Glossary-first prompting 的 pipeline 化

Junker 的 codecentric pipeline（Domain Storytelling → EventStorming → Glossary → OpenAPI）是一個可復現的範本：

```
EventStorming 產物（orange stickies）
  ↓ 提取 domain events 與 commands
Glossary v1（人工）
  ↓ 注入 LLM
OpenAPI draft（機器）
  ↓ 人工審閱，補 invariants
Glossary v2（更新）
  ↓ 重新生成
OpenAPI final（更接近業務實際）
```

每次 LLM 輸出與預期不符，首先問的問題應該是：「這個偏差是 glossary 沒有定義清楚，還是 invariant 沒有列進去？」——而不是「LLM 怎麼這麼笨」。

### Tessl 的 spec-as-source 極端版本

Tessl 走得更激進：spec 是 SSOT，code 帶有 `// GENERATED FROM SPEC - DO NOT EDIT` 的 marker。在這個模型下，glossary 是 spec 的前言，任何術語定義的改變都會觸發相關 spec 段落的重新生成，進而觸發 code 重新生成。這是「glossary 作為 LLM 詞彙表」的極端形式——詞彙表改變，代碼也跟著變。（Tessl 目前為 private beta，以官方最新狀態為準；查證日期 2026-06-30）

### DICE：把 domain objects 作為 agent 的輸入輸出型別

Russ Miles 提出（基於 Rod Johnson 的 Domain-Integrated Context Engineering，DICE）：不要把 domain objects 當 JSON blobs 傳給 agent，而要讓 agent 的輸入和輸出型別本身就是 domain objects，帶著 invariants 和業務語義。這讓 glossary 從「上下文文件」升格為「型別約束」。

**重要標注**：DICE 的 Rod Johnson 原始資料只能透過 Russ Miles 的文章間接取得；Rod Johnson 的第一手定義未能核實，以官方來源為準（查證日期 2026-06-30）。此概念目前偏向理念層面，實作細節輕。

---

## 動手練習

拿你手邊一個真實（或假設）的領域，完成以下三個步驟：

**步驟一**：找出三到五個你們領域最容易讓 LLM 搞錯的術語（通常是「看起來很普通但在你們業務裡有特殊語義」的詞）。

示例思路：
- 你們說的「訂單」到底是建立後的訂單、付款後的訂單、還是履約後的訂單？
- 「用戶」和「會員」是同一個 entity 嗎？
- 「審核」是人工審核還是系統自動審核，還是都有？

**步驟二**：用本章的 YAML 格式，為這三到五個術語各寫一個 glossary entry，包含：
- `definition`（說「是什麼」且說「不是什麼」）
- `invariants`（至少一條業務規則）
- `related`（關聯 term）

**步驟三**：把 glossary 注入你慣用的 AI coding 工具（CLAUDE.md / AGENTS.md / system prompt），然後用以下兩個 prompt 各生成一次 API 設計，對比差異：

```
Prompt A：設計一個 [你的核心 entity] 的 API（無 glossary）
Prompt B：（有 glossary 注入）設計一個 [你的核心 entity] 的 API
```

記錄哪些術語出現了漂移、哪條 invariant 在沒有 glossary 的版本中被遺漏。

---

## 本章重點整理

- LLM 是歧義的放大器，通用語言（Ubiquitous Language）是對抗命名漂移和業務幻覺的主要工具。
- Glossary 的核心價值不在「列名詞」，而在「消歧義」（說不是什麼）和「嵌 invariants」（讓業務規則進 context）。
- 三種注入機制：Spec Kit constitution、Kiro steering、AGENTS.md / CLAUDE.md——各有適用場景，但都以 repo 中的文件為 SSOT。
- Glossary 是概率錨點，不是硬性約束，必須搭配自動化驗收測試。
- 每個 Bounded Context 有自己的 glossary；全局 glossary 只放 Shared Kernel 術語。
- Annegret Junker 的 codecentric 案例研究是目前最具體的量化佐證（schema 3 → 9，業務規則覆盤）。

---

## 自我檢核

- [ ] 我能用自己的話解釋「命名漂移（naming drift）」和「業務幻覺」有什麼不同，以及 glossary 分別如何對抗這兩個問題。
- [ ] 面試被問「你怎麼讓 LLM 生成的 code 與業務術語對齊」，我能給出一個具體的技術方案，包含 glossary 的格式、放置位置、和注入機制。
- [ ] 我知道為什麼 glossary 裡需要 `invariants` 欄位，而不是只列名詞定義。
- [ ] 我能解釋「全公司一份 glossary」為什麼可能比沒有 glossary 更糟糕。
- [ ] 我知道「glossary 注入 = 保證遵守」這個假設是錯的，以及正確的補救措施是什麼。
- [ ] 我能說出 Spec Kit、Kiro、AGENTS.md 三種注入機制的核心差異，並能根據專案類型選擇適合的方式。

---

## 延伸閱讀

1. **Ubiquitous Language（bliki）** — Martin Fowler（引述 Eric Evans）
   - https://martinfowler.com/bliki/UbiquitousLanguage.html
   - 讀整篇（很短）。「software doesn't cope well with ambiguity」是本章所有論證的地基。這句話在 2006 年是 DDD 的理由，在 2026 年是 LLM glossary 的理由。與本章的關聯：你必須引用這個源頭才能理解 glossary 不是個「最佳實踐 tip」，而是解決一個根本矛盾。

2. **From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker（codecentric）
   - https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need
   - 從 Larder recipe 案例研究開始讀，聚焦 v1 vs v2 的 schema 比較。這是「glossary 改善 LLM 輸出」目前最具體的 reproducible evidence，其餘文章都是論點，這篇是資料。

3. **How Creating a Ubiquitous Language Ensures AI Builds What You Actually Want** — Daniel Schleicher（2026-01-04）
   - https://www.danielschleicher.com/software/engineering,/ai,/spec-driven/development/2026/01/04/removing-ambiguity-with-spec-driven-development.html
   - 讀「order」歧義那個段落。它是把 Fowler 的抽象理由翻譯成 LLM-era 日常場景的最清楚範例：一個詞十幾種語義，LLM 放大混亂。與本章的關聯：直接對應本章的「心智圖像」段落。

4. **Spec-Driven Development is Domain-Driven Design's Impatient Cousin** — Daniel Westheide（INNOQ，2026-03）
   - https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/
   - 讀「impatient cousin」段落和「upfront interview vs iterative discovery」的對比。這篇是本課 Part 6 整個論述的思想底座。與本章的關聯：解釋為什麼 glossary 是必要但不充分的——如果你的組織無法接觸真正的 domain experts，再好的 glossary 也是空殼。

5. **Agentic Code Workflows with Nick Tune** — Nick Tune（PayFit Sr Staff Engineer；DDD 作者）via Techworld with Milan
   - https://newsletter.techworld-with-milan.com/p/agentic-code-workflows-with-nick
   - 讀 state-machine 和 deterministic enforcement 段落。Tune 用 dependency-cruiser lint rules 強制 bounded context 邊界——這是「glossary + 工具強制執行」的實戰版本，避免只依賴 LLM 自覺遵守。

6. **Domain Driven Agent Design** — Russ Miles（Engineering Agents Substack，2025-10-08）
   - https://engineeringagents.substack.com/p/domain-driven-agent-design
   - 讀 bounded-context-per-business-unit 段落。Miles 提出把 domain objects 作為 agent 的 I/O 型別（DICE 框架），是 glossary 概念的進階延伸：從「文字描述」到「型別約束」。注意：概念性為主，實作細節薄；DICE 的 Rod Johnson 原始資料未能核實（以官方來源為準；查證日期 2026-06-30）。

7. **Eric Evans,《Domain-Driven Design: Tackling Complexity in the Heart of Software》**（Addison-Wesley，2003）
   - 讀第一部分第 2 章「Communication and the Use of Language」。這是通用語言最完整的一手論述。雖然 2003 年沒有 LLM，但 Evans 對「模糊語言如何在開發過程中傳播錯誤」的分析直接對應本章的問題。

8. **Spec-Driven Development | Technology Radar Vol 34** — Thoughtworks（2025-11）
   - https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development
   - 讀 Assess 環的理由和 Tessl 的「bitter lesson」警告。這是避免過度神話 glossary 效果的誠實錨點：工具仍在 Assess 環，輸出難以 review，過度規格化本身是風險。

---

下一章我們把「每個術語只在一個 context 裡為真」這個約束推進一步：一個 Bounded Context 不只是 glossary 的邊界，也是 agent task 的邊界——scope 的概念。

→ [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)
