# Ch 21 — Event Storming 工作坊

> **目標**：掌握 Alberto Brandolini 的便利貼法，能獨立主持一場工作坊，從 domain event（領域事件）倒推出 command、aggregate、bounded context 與熱點，並把產出轉化為可直接餵給 LLM 的規格素材。

## 在便利貼發明之前，人們怎麼做

2013 年以前的「需求探索」會議通常長這樣：一份預先準備的 Word 文件，一個說話的人，其他人邊點頭邊神遊。業務分析師（BA）把訪談記錄翻譯成 UML，開發者再把 UML 翻譯成程式碼——兩次轉譯，兩次語義流失。

當時流行的替代方案是 Use Case 工作坊：在白板上畫橢圓，花三個小時討論 Actor 名稱，結果是一份讓所有人都「沒有反對」卻沒有人真正理解的文件。

問題不在格式，而在方向：人們習慣**從功能清單倒退到系統行為**，卻沒有問「這個系統裡到底**發生了什麼事**？」

Alberto Brandolini 在 2013 年參加 Vaughn Vernon 的 IDDD Tour 時，帶著一卷橘色便利貼走進一間屋子，讓所有人把他們認為「系統裡發生的事」貼上牆。兩個小時後，那面牆比任何 UML 圖都更清楚。他在 2013 年 11 月 18 日把這個做法寫成部落格文章，命名為 EventStorming。

> 如果你對 domain event 的定義還不熟，先回看 [Ch 20 Repository / Domain Service / Factory / Domain Event](./20-repositories-services-events.md)。

## 便利貼的顏色語法

EventStorming 有一套約定俗成的顏色語法（Brandolini 的 2013 年原始文章給了核心顏色，完整色盤在後續社群實踐中逐步穩定，各文件的細節有微小差異，以 eventstorming.com 上的現行版本為準）：

```
顏色          代表的語義
────────────────────────────────────────────────────
橘色  (orange)   Domain Event    ← 核心，永遠先放
藍色  (blue)     Command         ← 觸發 event 的指令
黃色  (yellow)   Actor / Persona ← 誰發出 command
紫色  (purple)   Policy          ← 「當 X 發生時，做 Y」的業務規則
粉紅色 (pink)    External System ← 外部系統或時間觸發
淡黃  (pale)     Aggregate       ← 接受 command、發出 event 的一致性邊界
紅色  (red)      Hotspot         ← 爭議、未解決問題、需要深入討論
────────────────────────────────────────────────────
```

記法原則：**Domain Event 一律過去式動詞**。`OrderPlaced`（訂單已建立）、`PaymentReceived`（付款已收到）、`OrderShipped`（訂單已出貨）——不是「下訂單」，而是「訂單已下」。這個時態強迫所有人把系統當成歷史記錄來思考，而不是任務清單。

## 三個格局：大圖、流程、設計

EventStorming 不是單一格式，而是三個解析度的同一手法：

| 格局 | 英文名 | 目標 | 持續時間 |
|---|---|---|---|
| **大圖** | Big Picture | 探索整個業務領域，找到 bounded context 邊界 | 半天 |
| **流程** | Process Level | 深入一條業務流程，補齊 actor、command、policy | 2–4 小時 |
| **設計** | Design Level | 聚焦一個 aggregate 的狀態機，規格粒度到可以寫程式 | 1–2 小時 |

本章的重心是 **流程格局**，因為它是最常用的切入點，也是從訪談到可執行規格的關鍵橋樑。

## 端到端流程：以電商結帳為例

以下是一場 2 小時工作坊的完整流程示範。參與者：產品經理 Alice、後端工程師 Bob、客服主管 Carol。

### 步驟一：傾洩 Domain Event（15 分鐘）

規則只有一條：把你認為「這個系統裡曾經發生過的事」貼在牆上，橘色便利貼，過去式。沒有順序，沒有正確答案，不要評論別人的貼紙。

牆上可能出現：

```
OrderPlaced  PaymentReceived  OrderShipped  ItemOutOfStock
PaymentFailed  CustomerRegistered  CouponApplied  OrderCancelled
RefundIssued  ReviewSubmitted  CartAbandoned  InventoryUpdated
```

### 步驟二：按時間軸排列（10 分鐘）

把橘色貼紙從左到右排成時間線。這一步會立刻暴露三件事：

- 哪些事件的順序沒有共識（衝突）
- 哪些事件之間有缺口（流失的事件）
- 哪些事件同時發生（並行）

```
─────────────────────────────────────────────────── time ──→

[CustomerRegistered] → [CartAbandoned]? → [OrderPlaced] → [CouponApplied]?
                                                        ↓
                                            [PaymentReceived] or [PaymentFailed]
                                                        ↓
                                            [InventoryUpdated] → [OrderShipped]
                                                                        ↓
                                                            [ReviewSubmitted]
```

Bob 立刻注意到：`CouponApplied` 在 `OrderPlaced` 前還是後？Alice 和 Carol 看法不同。這就是第一個 **Hotspot**，貼一張紅色便利貼標記它。

### 步驟三：加入 Command 與 Actor（20 分鐘）

每個 Domain Event 都由某個動作觸發。在 event 左邊貼藍色 Command，在 Command 左邊貼黃色 Actor：

```
[Customer] → [PlaceOrder] → [OrderPlaced]
[Customer] → [PayWithCard] → [PaymentReceived]
[System/Timer] → [CheckInventory] → [InventoryUpdated]
[Warehouse Staff] → [ShipOrder] → [OrderShipped]
```

這一步常常揭露**誰觸發了什麼**的假設分歧——Alice 以為 `InventoryUpdated` 是倉管手動觸發，Bob 以為是系統自動在 `PaymentReceived` 後跑。又是一個 Hotspot。

### 步驟四：找出 Policy（10 分鐘）

Policy 是「當 X 事件發生時，自動觸發 Y command」的業務規則。用紫色便利貼，通常寫成「Whenever ___ → ___」：

```
[Policy: Whenever PaymentReceived → CheckInventory]
[Policy: Whenever ItemOutOfStock → NotifyCustomer + CancelOrder]
[Policy: Whenever OrderShipped → SendTrackingEmail]
```

Policy 是真正的業務邏輯所在，也是最常被遺漏在需求文件外的部分。

### 步驟五：畫 Aggregate 邊界（15 分鐘）

把接受相同 command 並維護相同不變條件（invariant）的物件圈在一起，用淡黃便利貼標記 Aggregate 名稱：

```
┌─────────────────── Order Aggregate ───────────────────┐
│  PlaceOrder → OrderPlaced                             │
│  ApplyCoupon → CouponApplied                          │
│  CancelOrder → OrderCancelled                         │
└───────────────────────────────────────────────────────┘

┌──────────── Payment Aggregate ────────────────────────┐
│  PayWithCard → PaymentReceived / PaymentFailed         │
│  RefundPayment → RefundIssued                         │
└───────────────────────────────────────────────────────┘

┌────── Inventory Aggregate ────────────────────────────┐
│  ReserveItem → InventoryUpdated                       │
│  ReleaseItem → ItemOutOfStock (if reserve fails)     │
└───────────────────────────────────────────────────────┘
```

Aggregate 邊界同時也暗示了 **Bounded Context** 的候選邊界。

### 步驟六：標記 Bounded Context（10 分鐘）

退後一步，看哪些 Aggregate 屬於同一個業務子域：

```
╔═══════════════ Ordering Context ═══════════════╗
║  Order Aggregate                                ║
║  Cart (如果有)                                  ║
╚═════════════════════════════════════════════════╝

╔═══════════════ Payments Context ════════════════╗
║  Payment Aggregate                              ║
╚═════════════════════════════════════════════════╝

╔═══════════════ Fulfillment Context ═════════════╗
║  Inventory Aggregate                            ║
║  Shipment (如果有)                              ║
╚═════════════════════════════════════════════════╝
```

> 如果你對 Bounded Context 的劃分標準還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

### 步驟七：回到 Hotspot，決議或記錄（20 分鐘）

回到所有紅色貼紙，每一個都要有結論：是決議了，還是轉成待辦事項？不決議的 Hotspot 是最昂貴的技術債——它會在實作的第 37 天以一個 production bug 的形式回來找你。

## 從工作坊產物到規格素材

工作坊結束後，你手上有：

1. **事件時間線**：可以直接成為 BDD 場景的 Given-When-Then 骨架
2. **Aggregate 清單**：直接對應戰術建模的 Aggregate Root + Command + Event
3. **Policy 清單**：對應業務規則，也對應 Domain Service 或 Saga 的職責
4. **Hotspot 清單**：未解決的問題，在 spec 裡標記為 `[NEEDS CLARIFICATION]`
5. **Bounded Context 邊界**：對應 OpenAPI spec 的切分單位，或微服務邊界

> 如果你對 BDD 的 Given-When-Then 格式還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

把 `OrderPlaced` 這個 domain event 翻成 BDD 場景：

```gherkin
Feature: 結帳下訂
  Background:
    Given 顧客已登入
    And 購物車有商品 [商品 A, 數量 2]

  Scenario: 成功下訂並付款
    When 顧客送出訂單
    Then 系統發出 OrderPlaced 事件
    And 庫存扣除 [商品 A, 數量 2]
    When 付款成功
    Then 系統發出 PaymentReceived 事件
    And 顧客收到訂單確認信

  Scenario: 付款失敗後庫存恢復
    When 顧客送出訂單
    Then 系統發出 OrderPlaced 事件
    When 付款失敗
    Then 系統發出 PaymentFailed 事件
    And 庫存恢復 [商品 A, 數量 2]
    And 顧客收到付款失敗通知
```

這是 EventStorming 與 BDD 最直接的對接點：橘色 event 成為 `Then`，藍色 command 成為 `When`，actor 成為角色或 `Background`。

## 底層機制：為什麼這個方法有效

EventStorming 的效力來自三個設計選擇：

**1. 時態強制（Past Tense Enforcement）**

強迫用過去式命名 event，讓所有人從「我們應該做什麼」切換到「系統裡到底發生了什麼」。這個切換把討論從意見（opinion）推向事實（fact），大幅減少抽象爭論。

**2. 實體化（Materialization）**

便利貼是實體的、可以移動的物件。當 Carol 把 `CouponApplied` 從 `OrderPlaced` 左邊挪到右邊，她同時在發表一個業務主張——這比在 Zoom 裡說「我覺得優惠券應該...」更有後果感，也更容易被挑戰和記錄。

**3. 衝突前置（Front-Loading Conflict）**

Hotspot 機制讓工作坊主動尋找分歧，而不是假裝沒有分歧。那個 `CouponApplied` 時序問題，如果不在這裡解決，就會在程式碼審查時爆發，到那時改動成本已經高出十倍。

> 如果你對「變更成本曲線」的概念想複習，先回看 [Ch 6 變更成本曲線——以及怎麼誠實引用它](./06-cost-of-change-curve.md)。

## 對比取捨

| | EventStorming | Use Case 工作坊 | 用戶故事地圖 (Story Map) |
|---|---|---|---|
| **起點** | Domain Event（發生的事） | Actor + 功能 | 使用者活動 |
| **揭露技術** | 衝突可視化 (Hotspot) | 協商 | 優先排序 |
| **擅長** | 發現邊界、流程缺口、Policy | 功能完整性 | 發布範圍規劃 |
| **弱點** | 非技術人員需要引導 | 容易變成功能清單 | 對業務規則不敏感 |
| **產出規格化程度** | 中（需要整理） | 低（UML 圖） | 低（Excel） |
| **時間成本** | 半天到一天 | 一天以上 | 半天到一天 |
| **最適用時機** | 複雜業務域探索 | 功能清單對齊 | Sprint 規劃 |

EventStorming **不是** UML 的替代品。它是探索工具，不是設計文件格式。工作坊之後通常還需要把產物整理成正式的類別圖或 API 規格。

## AI 輔助 EventStorming：現況

在 2026 年，AI 輔助 EventStorming 有兩個層次：

**已落地（shipping）：** Qlerify 等工具可以從自然語言描述自動生成事件序列和 aggregate 建議，但 AI 生成目前只在空白畫布上有效，還需要人工精修（版本/功能依賴具體產品版本，以官方最新說明為準）。

**仍在提案（aspirational）：** 使用 AI persona agent 模擬業務角色、持續偵測「spec 漂移（spec drift）」等做法在實踐者文章中出現，但尚無大規模生產驗證（此評估日期為 2026-06-30，這個領域變動快速）。

Annegret Junker（codecentric，2026 年 3 月）的案例研究顯示：先跑 EventStorming，再把產物餵給 LLM，能讓生成的 OpenAPI spec 從 3 個 schema 增長到 9 個，並且捕捉到純粹 LLM 提示無法發現的業務規則（自評星級機制）。這是目前最具說服力的 DDD 工作坊 + LLM 組合的證據（原始素材的品質直接決定 LLM 輸出的品質）。

> 如果你想看 DDD 與 SDD 如何在更大格局下銜接，先讀 [Ch 33 一個問題，兩個時代：DDD 與 SDD 是同一場仗](./33-ddd-sdd-same-fight.md)。

## 踩雷集錦

**錯誤直覺 1：Event 越多越好，把所有 UI 操作都列進去**

正確認識：Domain Event 代表「對業務有意義的事」，不是每個按鈕點擊。`ButtonClicked` 不是 domain event。`OrderPlaced` 才是。過多的 UI 層 event 會讓時間線變成互動流程圖，喪失業務語義。篩選標準：「如果這件事沒發生，業務會在乎嗎？」

**錯誤直覺 2：Command 和 Event 可以相同詞、不同時態**

正確認識：`PlaceOrder`（命令）和 `OrderPlaced`（事件）是不同的東西。命令可以失敗，事件是已經發生的事實。把兩者混淆會讓 Policy 和 Aggregate 的邊界模糊，日後的程式碼也很難對齊。

**錯誤直覺 3：工作坊結束後立刻拍照收工**

正確認識：拍照是保存工具，不是整理手段。真正的產出是把牆上的資訊轉化為結構化文件：Aggregate 清單、Policy 列表、Bounded Context 邊界、Hotspot 決議紀錄。沒有整理的工作坊成果，兩週後就會因為遺忘而失效。

**錯誤直覺 4：Bounded Context 邊界在工作坊當下就要確定**

正確認識：EventStorming 給你的是 Bounded Context 的候選邊界，不是最終答案。Aggregate 群集只是邊界的一個訊號。最終邊界還需要考慮團隊結構、部署獨立性、變更頻率差異。把工作坊產物視為「第一個可反駁的假設」，不是結案文件。

**錯誤直覺 5：沒有領域專家參加，開發者之間先跑一遍也行**

正確認識：開發者之間的 EventStorming 只能發現技術層的事件，無法發現業務規則的缺口。那個「客戶退款後優先退回原支付方式，超過 30 天改為轉帳」的 Policy，只有客服主管 Carol 知道。沒有 Carol，工作坊只是一場高級的設計討論，不是需求探索。

## 進階延伸

**Design-Level EventStorming**：當你要深入一個 Aggregate 的狀態機，Design Level 格局會在 Command-Event 對上加入「Read Model」（UI 呈現的資料）和「View」，讓設計粒度細到可以直接對應資料庫結構。

**Domain Storytelling**：另一種協作建模工具，用圖形化的業務流程敘事（actor 發送 work object 給 actor）來探索域。Junker 的案例研究顯示，Domain Storytelling 和 EventStorming 組合使用效果優於單獨使用其中一種——Domain Storytelling 好在捕捉誰對誰做什麼，EventStorming 好在發現 Policy 和 Hotspot。

**CQRS 的對應關係**：EventStorming 的 Command-Event 模型天然對應命令查詢職責分離（Command Query Responsibility Segregation, CQRS）架構。Command 對應 write side，Event 是 write side 的輸出，Read Model 對應 query side。如果你最終的系統採用 CQRS，工作坊產物可以幾乎無縫轉成系統架構圖。

## 動手練習

不需要便利貼，用文字版便利貼完成以下練習：

以「線上課程購買」為情境（學員購買課程 → 付款 → 取得存取權 → 觀看課程 → 完成課程），完成以下步驟：

1. 列出至少 8 個 Domain Event（橘色）
2. 為每個 Event 配對一個 Command（藍色）和 Actor（黃色）
3. 找出至少 2 個 Policy（紫色）
4. 找出至少 1 個 Hotspot（紅色），說明爭議點在哪裡
5. 把 Event 分配到至少 2 個 Aggregate
6. 說明你會把這些 Aggregate 切成幾個 Bounded Context，理由是什麼

完整的電商情境練習在 Practice C，那裡有更詳細的評分標準和參考答案。

## 本章重點整理

- EventStorming 是 Alberto Brandolini 2013 年發明的便利貼工作坊，核心是把 Domain Event（橘色，過去式）按時間線排列，再倒推 Command、Actor、Policy、Aggregate 和 Bounded Context。
- 三個格局（Big Picture、Process Level、Design Level）對應三個探索深度，本章重心在 Process Level。
- 工作坊的真正價值在於**前置衝突**：Hotspot 機制讓業務分歧在便利貼階段爆發，而不是在 production 爆發。
- 工作坊產物可以直接轉化為 BDD 場景、Aggregate 設計、OpenAPI spec 切分、Bounded Context 邊界候選。
- AI 輔助 EventStorming 目前有落地工具（Qlerify）和提案概念的區別；先跑工作坊再餵 LLM，比直接 prompt LLM 生成的規格品質更高，有 codecentric 案例研究為證。
- 沒有真正的領域專家參加，工作坊無效。

## 自我檢核

- [ ] 不查資料，用自己的話說明橘、藍、紫、紅、淡黃五種便利貼各代表什麼
- [ ] 為什麼 Domain Event 一定要用過去式？用自己的話解釋這個設計選擇的理由
- [ ] Hotspot 的作用是什麼？如果不用 Hotspot 會有什麼後果？
- [ ] Command 和 Domain Event 的差別是什麼？面試被問到「EventStorming 裡的 Command 和 Event 有什麼不同」，你會怎麼回答？
- [ ] 工作坊結束後必須產出哪些文件或記錄，才算有效轉化了成果？
- [ ] 為什麼沒有領域專家參加的 EventStorming 工作坊是危險的？

## 延伸閱讀

- **Introducing Event Storming** — Alberto Brandolini（2013）
  - URL：http://ziobrando.blogspot.com/2013/11/introducing-event-storming.html
  - 讀哪裡：完整閱讀，文章不長。這是 EventStorming 的原始文件，顏色語法、起源故事、與 DDD 的關係都在這裡。
  - 和本章的關聯：本章所有顏色約定的一手來源。

- **EventStorming（官方網站與書籍）** — Alberto Brandolini
  - URL：https://www.eventstorming.com/
  - 讀哪裡：從 /book/ 和 /resources/ 開始，特別是 Big Picture vs Design Level 的比較。
  - 和本章的關聯：三個格局的深度說明，以及工作坊引導技巧。

- **Domain-Driven Design Distilled** — Vaughn Vernon（2016）
  - 出版年：2016，Addison-Wesley
  - 讀哪裡：第 7 章 Accelerate Your Journey with Event Storming，是最友善的 EventStorming 入門讀本，與本章完全對應。
  - 和本章的關聯：EventStorming 到 Aggregate 設計的橋接說明，比 Brandolini 原文更系統化。

- **From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker（codecentric，2026）
  - URL：https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need
  - 讀哪裡：重點看 v1-v2-v3 三個 prototype 的 schema 比較，以及自評業務規則被發現的段落。
  - 和本章的關聯：本章「工作坊產物轉化為 LLM 輸入」論點的最直接實證，3 schema → 9 schema 的具體數字來自此文。

- **Ubiquitous Language（bliki）** — Martin Fowler（2006）
  - URL：https://martinfowler.com/bliki/UbiquitousLanguage.html
  - 讀哪裡：全文，很短，核心論點是「software doesn't cope well with ambiguity」。
  - 和本章的關聯：解釋了為什麼 EventStorming 強調命名精確——共同語言是 Aggregate 命名和 Bounded Context 命名的基礎。

- **Bounded Context（bliki）** — Martin Fowler（2014）
  - URL：https://martinfowler.com/bliki/BoundedContext.html
  - 讀哪裡：重點讀 polyseme（一詞多義）例子，這正是 EventStorming 步驟六要解決的問題。
  - 和本章的關聯：從 EventStorming 畫出的 Aggregate 群集，到最終 Bounded Context 邊界決策，需要這裡的判斷標準。

- **Spec-Driven Development is Domain-Driven Design's Impatient Cousin** — Daniel Westheide（INNOQ，2026）
  - URL：https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/
  - 讀哪裡：「impatient cousin」段落和「同一面牆」的比喻。
  - 和本章的關聯：說明了為什麼 EventStorming 是 SDD 的前置動作，而不是可以跳過的環節。

下一章我們把視角轉向「規格驅動」這個詞本身的兩種含義——可執行規格（BDD 的傳統）與規格再生成（AI 時代的新涵義），這兩條路線在哲學上的分歧比表面上看起來更深。

→ [練習 C 對電商情境跑一場 Event Storming](./practice-c-event-storming.md)
