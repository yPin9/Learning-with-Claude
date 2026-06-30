# 練習 C — 對電商情境跑一場 Event Storming

> **目標**：紙上跑一場 big-picture Event Storming，對一個電商情境產出完整的事件流（Domain Events）、命令（Commands）、aggregate 邊界，以及 bounded context 切分建議。

---

## 背景與動機

Event Storming 不是「整理需求的那個會議」，而是一種讓業務邏輯從牆上長出來的探索工具。Alberto Brandolini 在 2013 年的原始貼文把它定義為「快速探索複雜業務領域的工作坊格式」，核心動作只有一個：把「發生了什麼事」（Domain Event，領域事件）寫在橘色便利貼上，貼到時間軸。

> 如果你對 Domain Event、Aggregate 或 Bounded Context 的定義還不熟，先回看 [Ch 19 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md) 與 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

在現實工作坊裡，一群人圍著一面白牆，四十五分鐘後牆上會貼滿橘色的「OrderPlaced」「PaymentFailed」「ShipmentDelayed」——比任何 PRD 文件都更快暴露出業務的真實複雜度。這個練習把工作坊搬到紙面上，用文字代替便利貼，讓你獨立跑完整個流程。

**為什麼要在學 SDD 時做這個練習？**

Annegret Junker 在 codecentric 的案例研究（2026 年 3 月）展示：在對 LLM 下 prompt 之前先跑 EventStorming，最終 OpenAPI 規格的 schema 數量從 3 個成長到 9 個，而且發現了一條「用戶自我評分」的業務規則，根本沒出現在原始需求文字裡。Ubiquitous Language（通用語言）的精確度直接決定生成品質。

---

## 便利貼顏色速查

在紙面或數位工具上用顏色區分：

```
[橘色]  Domain Event      — 已發生的重要事件（過去式動詞）
[藍色]  Command           — 觸發事件的命令（祈使句）
[黃色]  Actor / Persona   — 誰發出這個命令
[紫/灰] External System   — 第三方或外部觸發（計時器、金流、簡訊）
[粉色]  Hotspot           — 問題、衝突、不確定之處（先貼再討論）
[綠色]  Read Model        — 決策前需要讀取的資料視圖
[草黃]  Aggregate         — 接受命令、發出事件的責任邊界
```

> 本練習只強制要求橘、藍、草黃三種；其餘顏色視你的深度而加。

---

## 任務規格

### 電商情境：Ecova 平台

Ecova 是一個多賣家電商平台，以下是從業務訪談中整理出來的原始描述，**刻意模糊**：

> 用戶可以瀏覽商品、加入購物車、下訂單、付款。賣家可以上架商品、管理庫存、出貨。系統會通知用戶訂單狀態。如果付款失敗，訂單應該留著讓用戶重試。訂單完成後可以評價商品。有時候賣家會開折扣活動，購物車裡要顯示優惠價格。系統也需要對帳，每個月結算給賣家。

### 精確輸入

上面那段業務描述。

### 精確輸出（必交）

1. **事件流時間軸**：至少 20 個 Domain Events，以時間順序排列，每個事件格式：
   ```
   [橘] EventName — 一句話說明觸發條件
   ```

2. **命令對照表**：對每個重要事件標出對應的 Command 和 Actor：
   ```
   Command         → Actor      → Event
   PlaceOrder      → Customer   → OrderPlaced
   ```

3. **Aggregate 邊界標注**：把事件分組，說明每組由哪個 aggregate 負責，以及它的不變條件（invariant）是什麼。

4. **Bounded Context 切分圖**：ASCII 圖，標出至少 3 個 bounded context，以及它們之間的整合關係（參考 Ch 17 的 context mapping 模式，至少命名一種模式）。

5. **Hotspot 清單**：至少列出 3 個你在建模過程中遇到的不確定點，說明為什麼這是問題、以及你暫時的解法或決策。

### 限制

- 不能使用 UML 工具或繪圖軟體，只用文字和 ASCII 圖。
- 每個 aggregate 最多管轄 5 個 Domain Events（強迫你正視邊界的切分）。
- Bounded context 之間必須用整合模式命名（Customer/Supplier、ACL、Published Language 等），不能只畫箭頭。
- Hotspot 不能是技術問題（「用什麼 DB」不算），必須是業務語義問題（「折扣和庫存誰先扣」才算）。

### 驗收標準

- [ ] 事件名稱全部是過去式動名詞（Past Tense），例如 `OrderPlaced` 而非 `PlaceOrder`
- [ ] 沒有任何事件名稱含有「Info」「Data」「Record」這類技術詞（這代表你在描述資料結構而非業務事件）
- [ ] 每個 aggregate 都有明確的不變條件（一句話），且該不變條件在現有事件流中能被驗證
- [ ] Bounded context 之間的整合模式有理由說明，不是隨便取的名字
- [ ] Hotspot 清單裡至少有一條在「折扣/促銷」和「庫存」的交叉點上

---

## 期望輸出範例

以下是一個**片段**示範，不是完整解答：

```
=== 事件流（部分） ===

[橘] ProductBrowsed         — 用戶瀏覽商品詳情頁
[橘] CartItemAdded          — 用戶把商品加入購物車
[橘] PromotionApplied       — 購物車自動套用賣家促銷規則
[橘] OrderPlaced            — 用戶確認送出訂單
[橘] PaymentInitiated       — 系統呼叫金流閘道
[橘] PaymentSucceeded       — 金流回調：付款成功
[橘] PaymentFailed          — 金流回調：付款失敗，訂單待重試

=== 命令對照（片段） ===

AddToCart         → Customer       → CartItemAdded
PlaceOrder        → Customer       → OrderPlaced
InitiatePayment   → OrderService   → PaymentInitiated
ConfirmPayment    → PaymentGateway → PaymentSucceeded / PaymentFailed

=== Aggregate 片段 ===

Aggregate: Cart
  不變條件：購物車裡同一個 SKU 的數量 ≤ 賣家設定的購買上限
  負責事件：CartItemAdded, CartItemRemoved, PromotionApplied, CartCheckedOut

=== Bounded Context（ASCII 片段） ===

┌─────────────────────────────────┐   Customer/Supplier   ┌──────────────────────┐
│      Catalog Context            │──────────────────────▶│   Cart Context       │
│  (upstream: 商品定義、定價、庫存) │    Published Language  │  (downstream: 使用   │
│                                 │                        │  ProductSnapshot)    │
└─────────────────────────────────┘                        └──────────────────────┘

=== Hotspot ===

⚠ Hotspot #1：折扣套用時機
  問題：促銷折扣在「加入購物車時」套用，還是「下單確認時」套用？
  如果是加入購物車時，用戶可能把東西放很久，促銷過期後訂單金額對不上。
  暫時決策：折扣在 PlaceOrder 時重新計算；購物車只顯示預估價格（Read Model）。
```

---

## 如果你卡住了

1. **卡在「事件到底算不算業務事件」**：用「業務人員聽了這個名字知道發生什麼事嗎？」來測試。`PaymentRecordInserted` 是技術操作，`PaymentSucceeded` 才是業務事件。

2. **卡在「一個流程到底是一個還是多個 aggregate」**：問自己「這些狀態變更必須在同一個資料庫交易裡保持一致嗎？」。如果必須，塞進同一個 aggregate；如果可以接受最終一致（eventual consistency），就切開，用 Domain Event 連接。

3. **卡在「bounded context 到底怎麼切」**：先找「同一個詞在不同地方意思不同」的地方。「Product」在 Catalog 裡是賣家定義的商品，在 Cart 裡是用戶看到的快照（帶當下定價），在 Order 裡是不可更改的歷史紀錄——這就是三個不同的模型，意味著三個 context。

4. **卡在命令和事件的關係**：命令代表意圖，事件代表事實。命令可以被拒絕（`PlaceOrder` 可能因為庫存不足而失敗），但事件永遠是已發生的事（`OrderPlaced` 發出後，訂單就存在了）。一個命令通常對應一個「成功事件」加上一或多個「失敗事件」。

5. **卡在 Hotspot 怎麼找**：把你覺得「這件事業務沒講清楚但我假設了某個答案」的地方全部貼成 Hotspot。折扣計算順序、庫存扣減時機、退款流程的責任歸屬、跨賣家購物車的行為——這些都是經典 Hotspot 所在地。

---

## 實作步驟建議

### Step 1：第一輪事件風暴（15 分鐘）

不要管對不對，把所有你能想到的業務事件都寫下來，過去式，一行一個。預期數量：25–40 個，很多是重複或相似的，沒關係。順序也不重要，先衝量。

### Step 2：梳理時間軸（10 分鐘）

把事件排成時間順序。這時候你會發現有些事件「位置飄移」——它在兩個地方都可能發生（例如 `InventoryReserved` 是在下單時還是付款後？）。這些就是你的 Hotspot。

### Step 3：標上命令與 Actor（10 分鐘）

對每個重要的事件，往前追問：「誰做了什麼動作導致這件事發生？」。系統自動觸發的（計時器、回調）標成 External System。

### Step 4：圈出 Aggregate（10 分鐘）

把時間軸上「責任相近」的事件圈起來，問自己「哪些事件的不變條件需要同一個物件來強制保證？」。給每個圈起來的群組一個名字和一條不變條件。

### Step 5：切 Bounded Context，畫整合圖（15 分鐘）

把 aggregate 群組進更大的 context，畫出 ASCII 圖，並在 context 之間的箭頭旁標上整合模式名稱與方向（upstream/downstream）。最後整理 Hotspot 清單，每條附上你的暫時決策。

---

## 完整參考解答

**強烈建議先獨立完成，再對照。** 對照時不要找「正確答案」，而是找「你的切法和這裡有什麼不同，各自有什麼取捨」。

<details>
<summary>點開參考解答</summary>

### 事件流時間軸（Ecova 電商，按流程順序）

```
=== 商品瀏覽與購物車 ===
[橘] ProductSearched         — 用戶輸入關鍵字或瀏覽分類
[橘] ProductViewed           — 用戶開啟商品詳情頁
[橘] InventoryChecked        — 系統回傳商品當前庫存狀態（Read Model 觸發）
[橘] CartItemAdded           — 用戶把商品加入購物車（數量 ≤ 購買上限）
[橘] CartItemRemoved         — 用戶從購物車移除商品
[橘] CartItemQuantityChanged — 用戶修改購物車數量
[橘] PromotionApplied        — 購物車套用賣家促銷（優惠碼或自動活動）
[橘] PromotionExpired        — 購物車裡某促銷活動到期失效

=== 結帳與支付 ===
[橘] OrderPlaced             — 用戶確認下單，系統鎖定庫存
[橘] InventoryReserved       — 訂單確立後系統預留庫存
[橘] PaymentInitiated        — 系統呼叫金流閘道請求付款
[橘] PaymentSucceeded        — 金流閘道回調：付款成功
[橘] PaymentFailed           — 金流閘道回調：付款失敗，訂單進入待重試狀態
[橘] PaymentRetried          — 用戶在待重試窗口內再次發起付款
[橘] OrderPaymentExpired     — 超過重試窗口，訂單自動取消，庫存釋放

=== 履約與出貨 ===
[橘] InventoryConfirmed      — 賣家確認庫存實際可出（備貨確認）
[橘] OrderReadyForShipment   — 賣家標記訂單已打包備妥
[橘] ShipmentDispatched      — 賣家輸入物流單號，包裹出貨
[橘] ShipmentTracked         — 物流系統回傳追蹤更新
[橘] OrderDelivered          — 物流確認送達（或買家簽收確認）

=== 退款與評價 ===
[橘] ReturnRequested         — 買家申請退貨退款
[橘] ReturnApproved          — 賣家核准退貨申請
[橘] RefundIssued            — 金流閘道退款成功
[橘] ProductReviewed         — 買家提交商品評價（僅允許在 OrderDelivered 後）

=== 賣家管理 ===
[橘] ProductListed           — 賣家上架新商品
[橘] ProductUpdated          — 賣家修改商品資訊或定價
[橘] InventoryAdjusted       — 賣家手動調整庫存數量
[橘] PromotionCreated        — 賣家建立折扣活動
[橘] PromotionDeactivated    — 賣家或系統停用折扣活動

=== 對帳結算 ===
[橘] SettlementCycleStarted  — 月結週期由系統排程觸發
[橘] SellerPayoutCalculated  — 系統計算本週期賣家應收金額
[橘] SellerPayoutDispatched  — 系統轉帳至賣家銀行帳戶
```

### 命令對照表

| Command                | Actor                  | 主要事件結果                                       |
|------------------------|------------------------|----------------------------------------------------|
| SearchProducts         | Customer               | ProductSearched                                    |
| ViewProduct            | Customer               | ProductViewed                                      |
| AddToCart              | Customer               | CartItemAdded / (拒絕：超過購買上限)               |
| RemoveFromCart         | Customer               | CartItemRemoved                                    |
| ChangeCartQuantity     | Customer               | CartItemQuantityChanged                            |
| ApplyCoupon            | Customer               | PromotionApplied / (拒絕：優惠碼無效)              |
| PlaceOrder             | Customer               | OrderPlaced + InventoryReserved                    |
| InitiatePayment        | OrderService (系統)    | PaymentInitiated                                   |
| ConfirmPayment         | PaymentGateway (外部)  | PaymentSucceeded / PaymentFailed                   |
| RetryPayment           | Customer               | PaymentRetried                                     |
| ExpireOrder            | Scheduler (計時器)     | OrderPaymentExpired                                |
| ConfirmInventory       | Seller                 | InventoryConfirmed                                 |
| MarkReadyForShipment   | Seller                 | OrderReadyForShipment                              |
| DispatchShipment       | Seller                 | ShipmentDispatched                                 |
| RequestReturn          | Customer               | ReturnRequested                                    |
| ApproveReturn          | Seller                 | ReturnApproved                                     |
| IssueRefund            | RefundService (系統)   | RefundIssued                                       |
| SubmitReview           | Customer               | ProductReviewed                                    |
| ListProduct            | Seller                 | ProductListed                                      |
| AdjustInventory        | Seller                 | InventoryAdjusted                                  |
| CreatePromotion        | Seller                 | PromotionCreated                                   |
| DeactivatePromotion    | Seller / Scheduler     | PromotionDeactivated                               |
| RunSettlementCycle     | Scheduler (計時器)     | SettlementCycleStarted → SellerPayoutCalculated    |
| DispatchPayout         | FinanceService (系統)  | SellerPayoutDispatched                             |

### Aggregate 邊界

**Aggregate: Cart**
- 不變條件：同一 CustomerID 同一 SKU 的累積購買數量不超過賣家設定的 `maxPerOrder` 上限；促銷優惠只有在活動有效期內才能被套用。
- 負責事件：`CartItemAdded`, `CartItemRemoved`, `CartItemQuantityChanged`, `PromotionApplied`, `PromotionExpired`
- 說明：Cart 是短暫的（用戶放棄不結帳，Cart 最終廢棄），不是持久訂單，需要獨立管理。

**Aggregate: Order**
- 不變條件：同一筆訂單只能有一種最終狀態（PaymentSucceeded 後不能再 PaymentFailed）；退款只能在 OrderDelivered 後申請。
- 負責事件：`OrderPlaced`, `PaymentInitiated`, `PaymentSucceeded`, `PaymentFailed`, `PaymentRetried`, `OrderPaymentExpired`
- 說明：Payment 狀態是 Order 的一部分，不單獨抽出，因為「付款失敗需要保留訂單讓用戶重試」這個業務規則需要 Order 持有付款狀態。

**Aggregate: Fulfillment**
- 不變條件：只有 PaymentSucceeded 的 Order 才能進入 Fulfillment；出貨單號一旦輸入即不可更改（但可補充追蹤更新）。
- 負責事件：`InventoryReserved`, `InventoryConfirmed`, `OrderReadyForShipment`, `ShipmentDispatched`, `ShipmentTracked`, `OrderDelivered`
- 說明：Fulfillment 代表「賣家的履約責任」，從 InventoryReserved 一路到 OrderDelivered。

**Aggregate: Return**
- 不變條件：退貨申請只能在 OrderDelivered 後 7 天內提出；一筆訂單最多一次退款申請。
- 負責事件：`ReturnRequested`, `ReturnApproved`, `RefundIssued`

**Aggregate: Catalog / Product**
- 不變條件：上架商品必須有有效的 SKU、至少一個庫存數量 ≥ 0；定價不能為負。
- 負責事件：`ProductListed`, `ProductUpdated`, `InventoryAdjusted`, `PromotionCreated`, `PromotionDeactivated`

**Aggregate: Review**
- 不變條件：同一 Customer 對同一 OrderLine 只能留一則評價；評價只能在 OrderDelivered 後提交。
- 負責事件：`ProductReviewed`

**Aggregate: SellerSettlement**
- 不變條件：同一賣家同一結算週期只計算一次；已 Dispatched 的 Payout 不可重算。
- 負責事件：`SettlementCycleStarted`, `SellerPayoutCalculated`, `SellerPayoutDispatched`

### Bounded Context 切分圖

```
  ┌────────────────────────────────────────────────────────────────────────────┐
  │                     Ecova 電商平台  — Context Map                          │
  └────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────┐   Published Language    ┌──────────────────────────┐
  │   Catalog Context    │ ───────────────────────▶ │    Cart Context          │
  │                      │  (ProductSnapshot DTO：  │                          │
  │  aggregates:         │   id, name, price,       │  aggregates:             │
  │  - Product/Catalog   │   promotions, stockHint) │  - Cart                  │
  │  - Promotion         │                          │                          │
  │                      │                          │  Customer 建立、操作、   │
  │  Seller 上架、管庫   │                          │  結帳前的購物車          │
  └──────────────────────┘                          └──────────────┬───────────┘
         ▲ Open-Host Service                                        │
         │ (Catalog 對 Settlement                                   │ Customer/Supplier
         │  提供 OrderLine 定價快照)                                │ (Cart 是 upstream，
         │                                                          │  Order 依賴
  ┌──────┴───────────────┐                                          │  CartCheckedOut)
  │  Settlement Context  │                                          ▼
  │                      │  Anticorruption Layer   ┌──────────────────────────┐
  │  aggregates:         │ ◀────────────────────── │    Order Context         │
  │  - SellerSettlement  │  (Order 事件翻譯為       │                          │
  │                      │   Settlement 語言)       │  aggregates:             │
  │  每月結算賣家應收    │                          │  - Order                 │
  └──────────────────────┘                          │  - Fulfillment           │
                                                    │  - Return                │
  ┌──────────────────────┐  Published Language      │                          │
  │  Identity &          │ ───────────────────────▶ │  OrderPlaced,            │
  │  Customer Context    │  (CustomerId, SellerID)  │  PaymentSucceeded,       │
  │  （通用子領域）      │                          │  OrderDelivered…         │
  └──────────────────────┘                          └──────────────┬───────────┘
                                                                   │
  ┌──────────────────────┐   Conformist                            │ Domain Event
  │  Payment Gateway     │ ◀────────────────────────────────────── │ (Order 對外
  │  （外部系統 / ACL）  │   Order Context 遵從 Gateway API，      │ 發布事件)
  │                      │   本地翻譯層隔離變動                    │
  └──────────────────────┘                          ┌──────────────▼───────────┐
                                                    │  Review Context          │
  ┌──────────────────────┐  Customer/Supplier       │                          │
  │  Notification        │ ◀────────────────────── │  aggregates:             │
  │  Context             │  (Order 是 upstream，    │  - Review                │
  │  （通知用戶/賣家）   │   訂閱 Domain Events 後  │                          │
  └──────────────────────┘   發送 Email/SMS)        └──────────────────────────┘

  圖例：
  ──────▶  downstream 讀取 upstream 的 Published Language / DTO
  ◀──────  Anticorruption Layer 保護 downstream 免受 upstream 模型侵蝕
  Conformist：downstream 直接遵從 upstream 的模型（外部 API 沒談判空間）
```

**整合模式說明：**

| 關係                          | 模式                   | 理由                                                              |
|-------------------------------|------------------------|-------------------------------------------------------------------|
| Catalog → Cart                | Published Language     | Catalog 定義 ProductSnapshot 契約，Cart 不可修改商品定義          |
| Cart → Order                  | Customer/Supplier      | Cart 是 upstream，Order 消費 CartCheckedOut 事件後才建立訂單      |
| Order → Settlement            | Anticorruption Layer   | Settlement 不想讓 Order 的內部模型污染財務計算語義                |
| Order → Payment Gateway       | Conformist             | 外部金流 API 由第三方定義，Order Context 只能遵從                 |
| Order → Notification          | Customer/Supplier      | Order 發布事件，Notification 訂閱但不影響 Order 行為              |
| Catalog → Settlement          | Open-Host Service      | Settlement 需要商品定價快照，Catalog 提供穩定的查詢介面           |
| Identity/Customer → Order     | Published Language     | CustomerID 跨 context 共用，只傳 ID 不傳整個 Customer 物件       |

### Hotspot 清單

**⚠ Hotspot #1：折扣套用時機 vs 庫存扣減順序**

- **問題**：促銷折扣在「加入購物車時計算」還是「下單確認時重算」？如果用戶把東西放在購物車三天，促銷在這期間過期，訂單金額要怎麼算？
- **更深的問題**：折扣是依附在 Cart 上（Catalog Context 傳來），還是在 Order 上重新計算（Order Context 自己有促銷邏輯）？兩種選擇對 bounded context 的劃分有根本影響。
- **暫時決策**：`PromotionApplied` 發生在購物車（預估優惠，僅顯示用），`OrderPlaced` 時 Order Context 從 Catalog 取得當下的 `PromotionSnapshot` 重新計算，這才是法律上的成交價格。Cart 顯示的是「預估價」，明確標注。

**⚠ Hotspot #2：庫存預留（InventoryReserved）的時機**

- **問題**：庫存在「下單時」預留，還是「付款成功後」才正式扣？下單預留可防止超賣但讓庫存被鎖死；付款後扣可讓庫存更靈活但可能超賣。
- **業務衝突**：賣家投訴「明明有庫存卻賣不出去」（因為被 unpaid 訂單鎖住）vs 用戶投訴「明明付錢了卻說沒庫存」。
- **暫時決策**：`InventoryReserved` 發生在 `OrderPlaced` 之後（soft reserve，有效期 30 分鐘），`PaymentSucceeded` 後轉為 `InventoryConfirmed`（hard commit）。超過 30 分鐘未付款觸發 `OrderPaymentExpired`，庫存釋放。這是常見的電商做法（版本依賴：這個時間窗應由業務決定，不是技術決定）。

**⚠ Hotspot #3：多賣家購物車的訂單切分**

- **問題**：Ecova 是多賣家平台。用戶購物車裡有三家賣家的商品，送出一個「下單」動作，系統應產出一張訂單還是三張？
- **業務影響**：一張訂單：付款失敗全部取消，用戶體驗差；退款和履約也複雜。三張訂單：用戶 UI 要顯示三個物流進度，結帳介面複雜。
- **暫時決策**：`PlaceOrder` 命令在系統層面拆成每賣家一個 `OrderPlaced` 事件（即每賣家一張子訂單）。購物車層面仍是一次結帳，但 Order Context 內部是多個 Order aggregate 實例。這個決策需要業務確認，標記為待討論。

**⚠ Hotspot #4（加分）：評價與退貨的衝突**

- **問題**：用戶已提交 `ProductReviewed`，後來申請退貨並核准。評價要不要自動撤回或標記？
- **暫時決策**：評價在 Review Context 維護自己的狀態；`ReturnApproved` 事件可觸發 Review Context 把評價標記為「已退貨購買」，但不刪除。這個決策需要法務確認（顯示不實體驗評價的責任問題）。

</details>

---

## 測試用例表

跑完練習後，對照以下測試案例確認你的建模是否涵蓋了這些邊界情境：

| # | 情境描述 | 你的建模必須能回答的問題 |
|---|----------|--------------------------|
| T1 | 用戶下單後 30 分鐘未付款 | 哪個 aggregate 觸發庫存釋放？是命令還是外部計時器？ |
| T2 | 付款成功後賣家說庫存不足 | `InventoryConfirmed` 失敗時 Order 應進入什麼狀態？哪個 bounded context 負責補償？ |
| T3 | 用戶用 10 張優惠券全部同時結帳 | Cart aggregate 的不變條件能防止無效組合嗎？ |
| T4 | 賣家在用戶購物車有商品時下架該商品 | `ProductUpdated`/`ProductUnlisted` 應不應該通知 Cart？透過什麼機制？ |
| T5 | 月結時某訂單正在退款處理中 | Settlement Context 是否應等 `RefundIssued` 後才計算？還是先算再調整？ |
| T6 | 同一商品跨賣家價格不同 | Catalog Context 的 `ProductSnapshot` 裡「商品」和「賣家定價」是同一個模型嗎？ |
| T7 | 用戶對已退款的訂單留評價 | Review Context 的不變條件是否阻止這個操作？ |

---

## 延伸挑戰

完成基本要求後，試試以下加深練習：

**挑戰 1：Design-Level Event Storming**

選出 `PlaceOrder → PaymentSucceeded → InventoryReserved` 這一段，從 big-picture 細化到 Design-Level：補上 Read Model（結帳前用戶看到什麼資料視圖？）、補上每個 Command 的前置條件（pre-condition）。

**挑戰 2：加入 Notification 流**

完整追蹤「用戶在整個購買旅程中會收到哪些通知」，把 Notification 做成獨立 bounded context，標出它訂閱哪些 Domain Events、用什麼整合模式。

**挑戰 3：用 Ubiquitous Language 寫 EARS 需求**

從你的事件流中挑出 5 個最重要的業務規則（例如「庫存預留時限」「退貨申請窗口」），用 EARS 句型（見 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)）寫成精確需求。然後把這些需求和你的 aggregate 不變條件對比——有沒有哪個不變條件在 EARS 裡找不到對應的需求句？

**挑戰 4：用 LLM 生成初稿，再 EventStorming 修正**

把練習情境的業務描述直接貼給 LLM，請它輸出 OpenAPI 規格。對比 LLM 直接生成的版本和你 EventStorming 後補充的版本，記錄 LLM 遺漏了哪些業務規則（參考 Annegret Junker 的食譜平台案例：v1 只有 3 個 schema，v2 有 9 個）。

---

## 自我檢核

完成後，用自己的話回答以下問題（目標：能在面試或白板討論中直接講出來，不翻筆記）：

- [ ] 不查資料，用一句話解釋 Domain Event 和 Command 的本質區別——命令可以被拒絕，但 ____。
- [ ] 你切出了幾個 bounded context？說出每個 context 的核心模型（2–3 個詞的 Ubiquitous Language）。
- [ ] 面試官問「你怎麼決定一個概念放在 A context 還是 B context？」你會怎麼回答？（提示：同一個詞在兩個地方的意思是否相同？）
- [ ] 你的 Hotspot #1 最後做了什麼決策？說出決策的取捨（做了什麼，放棄了什麼）。
- [ ] 為什麼 `PaymentFailed` 之後訂單不直接刪掉，而是保留在「待重試」狀態？這個決策反映在你的哪個 aggregate 不變條件上？
- [ ] 如果現在把你的 EventStorming 產出交給 LLM 作為 spec 前置文件，哪一個產物對 LLM 最有幫助？（Ubiquitous Language 術語表、aggregate 不變條件、bounded context 邊界、還是事件時間軸？）

---

## 延伸閱讀

- **Introducing EventStorming（原始 Blog）** — Alberto Brandolini  
  http://ziobrando.blogspot.com/2013/11/introducing-event-storming.html  
  Event Storming 的發明人自己寫的原始說明，附上便利貼顏色文法和「事件驅動模型」的起源故事。本練習的核心理論來源，讀完才知道為什麼是橘色。

- **EventStorming（官方書籍與資源站）** — Alberto Brandolini  
  https://www.eventstorming.com/  
  Brandolini 持續更新的 Leanpub 書（進行中）和配套資源，Big-Picture vs Design-Level 的正式區分在這裡。練習完 big-picture 後可進 Design-Level 繼續深化。

- **Domain-Driven Design Distilled** — Vaughn Vernon（2016）  
  https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420  
  最短的路徑通覽 bounded context、subdomain 分類、context map 整合模式，以及 EventStorming 入門一章。本練習的 Aggregate 設計規則（小 aggregate、by-ID 引用、eventual consistency）都出自 Vernon 的四條原則。

- **ddd-crew/context-mapping（GitHub）** — DDD Crew  
  https://github.com/ddd-crew/context-mapping  
  九種 context mapping 整合模式的速查表與決策指引。本練習標注整合模式時可以這裡對照：Customer/Supplier 什麼時候選？ACL 什麼時候必要？

- **From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker（codecentric，2026 年 3 月）  
  https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need  
  唯一有具體數字的「EventStorming 前後對比」案例研究：食譜平台從 3 個 schema 到 9 個，自我評分規則被建模捕捉。本練習挑戰 4 的直接來源；也是為什麼說「先 EventStorming 再 prompt」有實證意義的最佳佐證。

- **Bounded Context（bliki）** — Martin Fowler  
  https://martinfowler.com/bliki/BoundedContext.html  
  「meter」這個多義詞為什麼在商業對話裡可以模糊對話，但在電腦系統裡就必須明確切分——這個論點直接解釋了為什麼 bounded context 存在，也直接解釋了為什麼 LLM 會「放大歧義」。三分鐘短文，讀完對整合模式的選擇會更有感覺。

---

這個練習把 Event Storming 的產出（事件流、命令、aggregate、bounded context）全部落地成可以交給 LLM 或寫進 spec 的具體文字。下一章要探討「spec-driven」這個詞本身其實有兩種用法——一種是讓機器執行的規格，另一種是讓機器再生成規格——這兩種路徑在工具選擇和工作流設計上有根本的差異。

→ [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)
