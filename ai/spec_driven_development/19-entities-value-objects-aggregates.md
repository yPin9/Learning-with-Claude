# Ch 19 — 戰術建模：Entity / Value Object / Aggregate

> **目標**：掌握 DDD 戰術建模（Tactical Modeling）三個核心積木——Entity、Value Object、Aggregate——以及 Vaughn Vernon 的四條 Aggregate 設計規則，能夠對一個真實領域做出有邊界、有一致性保證的物件模型。

## 從混亂到結構：2003 年前人們怎麼做

Evans 的藍皮書（Blue Book，Addison-Wesley，2003）出版之前，主流實務是**貧血模型（Anemic Domain Model）**：把資料庫 table 直接映射成有 getter/setter 的 POJO，所有邏輯集中在 service 層的 procedural code 裡。代價是領域邏輯散落各處，「訂單被修改」可以從十個地方觸發，不一致性蔓延。

Evans 的診斷：問題在於我們沒有語言描述「哪些物件需要 identity、哪些只是值、哪幾個要作為原子單位保護」。Entity、Value Object、Aggregate 就是那套語言。

> 如果對「為什麼 DDD 把複雜性放在領域而非技術」還不熟，先回看 [Ch 14 為什麼 DDD：複雜性在領域，不在技術](./14-why-ddd.md)。

---

## 三個積木的心智圖像

想像你在管一家倉庫：

```
┌──────────────────────────────────────────────────────────────┐
│  倉庫 (Aggregate 邊界)                                        │
│  ┌────────────────────────────────┐                          │
│  │  貨架 W-001 (Aggregate Root)   │ ← 唯一對外入口             │
│  │  Entity：identity = 貨架編號   │                          │
│  │   格子 A1: SKU-999, 數量 3     │ ← Value Object (數量)     │
│  │   格子 A2: SKU-007, 數量 1     │                          │
│  └────────────────────────────────┘                          │
│  外部只能說「請移動 W-001 裡的 SKU-999」                       │
│  外部不能直接戳到「格子 A1 的數量欄位」                         │
└──────────────────────────────────────────────────────────────┘
```

- **Entity**：問的是「這是哪一個」。W-001 搬到另一個倉庫仍是 W-001。
- **Value Object**：問的是「這是什麼」。數量 3 就是 3，沒有「哪一個 3」的問題。
- **Aggregate**：W-001 加上所有格子，整體作為一致性單位——你不能繞過 W-001 偷改格子數量，W-001 負責確保「總庫存不超過承重」這類不變條件（invariant）。

---

## Entity：identity over time

Evans：「An object defined primarily by its identity is called an ENTITY.」

核心問題：**時間流動之後，它還是同一個東西嗎？** 一張訂單剛建立是 `PENDING`，付款後是 `PAID`，出貨後是 `SHIPPED`——屬性全部在改，但它還是同一張訂單。這個「同一性」靠 identity 維持，通常是一個 ID。

### Python 範例：訂單 Entity

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import NewType

OrderId = NewType("OrderId", str)


class OrderStatus(Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    id: OrderId
    customer_id: str          # 另一個 Aggregate 的 ID（稍後解釋）
    status: OrderStatus = OrderStatus.PENDING
    lines: list[OrderLine] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Order):
            return NotImplemented
        return self.id == other.id  # identity 決定相等，屬性無關

    def __hash__(self) -> int:
        return hash(self.id)

    def mark_paid(self) -> None:
        if self.status != OrderStatus.PENDING:
            raise ValueError(f"無法付款：訂單狀態為 {self.status.value}")
        self.status = OrderStatus.PAID
```

測試：

```python
order_a = Order(id=OrderId("ORD-1001"), customer_id="C-42")
order_b = Order(id=OrderId("ORD-1001"), customer_id="C-99")  # 同 ID，不同屬性

print(order_a == order_b)  # True：同 identity，屬性無關
```

Entity 的相等由 identity 定義，不由屬性集決定。用 `==` 比較屬性，得到的是 Value Object 語義。

### 何時用 Entity

| 問題 | 答案傾向 |
|------|---------|
| 追蹤「哪一個」比「是什麼」更重要 | Entity |
| 物件有生命週期、狀態會演變 | Entity |
| 需要審計日誌（Audit Log） | Entity |
| 「一旦改變屬性就變成另一個東西」 | Value Object |

---

## Value Object：immutable, attribute-only

Evans：「VALUE OBJECTS are instantiated to represent elements of the design that we care about only for what they are, not who or which they are.」

核心特性：(1) 沒有 identity，屬性完全相同即可互換；(2) immutable，修改就是新建；(3) 相等由屬性決定（structural equality），不是引用相等。

### Python 範例：Money Value Object

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)   # frozen=True 讓它不可變
class Money:
    amount: int           # 以最小貨幣單位儲存（分），避免浮點誤差
    currency: str         # ISO 4217，例如 "TWD"、"USD"

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError(f"金額不得為負：{self.amount}")
        if len(self.currency) != 3:
            raise ValueError(f"貨幣碼格式錯誤：{self.currency}")

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("不同幣別無法直接相加")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __str__(self) -> str:
        return f"{self.amount / 100:.2f} {self.currency}"
```

測試：

```python
a = Money(amount=15000, currency="TWD")
b = Money(amount=15000, currency="TWD")
print(a == b)                       # True：屬性相同即相等
print(a.add(Money(5000, "TWD")))    # 200.00 TWD

try:
    Money(-100, "TWD")              # 金額不得為負：-100
except ValueError as e:
    print(e)
try:
    a.add(Money(1000, "USD"))       # 不同幣別無法直接相加
except ValueError as e:
    print(e)
```

### 常見的 Value Object

| 例子 | 屬性 | 為什麼不是 Entity |
|------|------|-----------------|
| `Money(150, "TWD")` | amount, currency | 沒有「哪一個 150 元」的問題 |
| `Address("台北市信義區...","10048")` | street, postcode | 地址本身是描述，沒有生命週期 |
| `DateRange(start, end)` | start, end | 時間段是純描述，不需 ID |
| `Quantity(3, "件")` | count, unit | 3 件就是 3 件，可互換 |

### 為什麼要 immutable

如果 `Money` 是 mutable 的，兩條 OrderLine 共用同一個 `price` 物件，改動 `price.amount` 會靜默改掉兩條 line 的價格——這是 mutable shared state 的經典病。Immutable Value Object 讓共用無害：兩條 line 「持有相同值」不等於「持有同一個物件」，修改就是建立新的，原物件不動。

---

## Aggregate：一致性邊界

### 問題來源

Entity 和 Value Object 解決了「物件怎麼比較」，但還沒解決「怎麼保持資料一致性」。

如果外部程式碼可以直接操作 `OrderLine`，誰來保護「total = sum(lines)」？答案是沒人。Aggregate 的答案：**建立邊界，外部只能透過 Aggregate Root 存取邊界內物件，Root 負責確保所有不變條件恆成立**。

### Aggregate Root 是什麼

Evans：「The root is the only member of the AGGREGATE that outside objects are allowed to hold references to.」

```
外部世界
    │  只能持有 Order 的引用
    ▼
┌──────────────────────────────────────┐
│ Order (Aggregate Root, Entity)       │
│  id: OrderId                         │
│  status: OrderStatus                 │
│  total: Money                        │
│  ┌─────────────────────────────┐     │
│  │ OrderLine (內部 Entity)     │     │
│  │  quantity: Quantity (VO)   │     │
│  │  unit_price: Money (VO)    │     │
│  └─────────────────────────────┘     │
│  rule: total == Σ(line.subtotal)     │
└──────────────────────────────────────┘
```

### 完整 Python 範例

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import NewType
import uuid

OrderId = NewType("OrderId", str)
ProductId = NewType("ProductId", str)

@dataclass(frozen=True)
class Money:
    amount: int   # 以「分」儲存，避免浮點誤差
    currency: str
    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("幣別不符")
        return Money(self.amount + other.amount, self.currency)
    def multiply(self, factor: int) -> Money:
        return Money(self.amount * factor, self.currency)
    def __str__(self) -> str:
        return f"{self.amount / 100:.2f} {self.currency}"

@dataclass(frozen=True)
class Quantity:
    count: int
    unit: str
    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError(f"數量必須大於零：{self.count}")

@dataclass
class OrderLine:            # Aggregate 內部 Entity，不對外暴露
    line_id: str
    product_id: ProductId
    quantity: Quantity
    unit_price: Money
    @property
    def subtotal(self) -> Money:
        return self.unit_price.multiply(self.quantity.count)

class OrderStatus(Enum):
    PENDING = "PENDING"
    PAID    = "PAID"
    SHIPPED = "SHIPPED"

class Order:                # Aggregate Root
    def __init__(self, order_id: OrderId, customer_id: str) -> None:
        self._id = order_id
        self._customer_id = customer_id
        self._status = OrderStatus.PENDING
        self._lines: list[OrderLine] = []

    @property
    def id(self) -> OrderId: return self._id
    @property
    def status(self) -> OrderStatus: return self._status
    @property
    def total(self) -> Money:
        return sum((l.subtotal for l in self._lines), start=Money(0, "TWD"))

    def add_line(self, product_id: ProductId, qty: Quantity, price: Money) -> None:
        if self._status != OrderStatus.PENDING:
            raise ValueError(f"只有 PENDING 可加 line，目前：{self._status.value}")
        self._lines.append(OrderLine(str(uuid.uuid4()), product_id, qty, price))

    def mark_paid(self) -> None:
        if self._status != OrderStatus.PENDING:
            raise ValueError(f"無法付款：{self._status.value}")
        if not self._lines:
            raise ValueError("空訂單無法付款")
        self._status = OrderStatus.PAID

    def line_count(self) -> int: return len(self._lines)
    def __eq__(self, o: object) -> bool:
        return self._id == o._id if isinstance(o, Order) else NotImplemented
    def __hash__(self) -> int: return hash(self._id)
```

執行：

```python
order = Order(OrderId("ORD-2024-001"), "CUST-42")
order.add_line(ProductId("SKU-999"), Quantity(2, "件"), Money(30000, "TWD"))
order.add_line(ProductId("SKU-007"), Quantity(1, "件"), Money(15000, "TWD"))
print(order.total)   # 750.00 TWD  (300*2 + 150*1)
order.mark_paid()
print(order.status)  # OrderStatus.PAID

# 邊界：已付款後不能加 line
try:
    order.add_line(ProductId("SKU-X"), Quantity(1, "件"), Money(1000, "TWD"))
except ValueError as e:
    print(e)  # 只有 PENDING 可加 line，目前：PAID
```

不變條件（`total == Σ(line.subtotal)`）永遠在 `total` property 裡算，外部從未接觸 `_lines`，無法繞過。

---

## 底層機制：為什麼 Aggregate 是「一致性邊界」

這裡的「一致性」指**事務一致性（transactional consistency）**，不是 CAP 那個。

一個 Aggregate 的所有物件，在一筆資料庫交易（transaction）裡要麼全部成功、要麼全部回滾：

```
一筆 transaction 內：
  UPDATE orders SET status='PAID' WHERE id='ORD-2024-001'
  UPDATE order_lines SET ...     WHERE order_id='ORD-2024-001'
  → COMMIT 或 ROLLBACK

Customer Aggregate 的更新 → 另一筆 transaction，透過 Domain Event 觸發
```

跨 Aggregate 不走同一筆 transaction，而是透過 Domain Event 達成最終一致性（eventual consistency）。分散式系統裡跨服務的強事務代價高昂，最終一致在業務上通常完全可接受：

```
OrderPlaced (Domain Event)
  ├─→ Inventory Aggregate 扣庫存（異步，自己的 transaction）
  └─→ Billing Aggregate 建帳單（異步，自己的 transaction）
```

> 如果你對 Bounded Context 與整合模式還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

---

## Vernon 的四條 Aggregate 設計規則

Vaughn Vernon 在《Implementing Domain-Driven Design》（2013，紅皮書，InformIT/Pearson）裡給了四條具體規則。這是 Evans 之後最重要的 Aggregate 實務指南。

### Rule 1：用 Aggregate 保護真正的不變條件

**邊界劃在真正需要被一起保護的不變條件之外**，不是劃在「感覺相關」的物件之外。

問：`Customer` 和 `Order` 要放在同一個 Aggregate 嗎？

錯誤直覺：「訂單屬於顧客，當然放在一起。」

正確做法：問「有沒有不變條件要求它們一起修改？」——通常沒有。`Customer` 的地址改了，訂單不需要同一個 transaction 更新。把它們放在一起只會讓 Aggregate 變得龐大、鎖競爭變嚴重。

### Rule 2：設計小 Aggregate

Vernon 引用 Niclas Hedhman 的金融衍生商品專案數據：**約 70% 的 Aggregate 只有 Root Entity 加幾個 Value Object 屬性**，另 30% 有 2-3 個 Entity。大 Aggregate 鎖更多資料（並發競爭加劇）、測試更難（大型 object graph）、一致性邊界越大越容易把不需要強一致的東西塞進來。Aggregate 有 10 個 Entity，設計幾乎必然有問題。

### Rule 3：透過 ID 引用其他 Aggregate

```python
# 錯誤：直接持有 Customer 物件引用
class Order:
    customer: Customer        # ← 可以繞過 Customer 的邊界直接改

# 正確：只持有 CustomerId（Value Object）
class Order:
    customer_id: CustomerId   # ← 明確表達「不同一致性單位」
```

好處：防止跨邊界直接修改、降低記憶體佔用（不 eager load 整個 Customer）、讓邊界意圖明確。

### Rule 4：邊界之外用最終一致性

同一 Aggregate 內部：強一致（一個 transaction）。跨 Aggregate：透過 Domain Event 達成最終一致：

```python
@dataclass
class OrderPaid:          # Domain Event，過去式命名
    order_id: OrderId
    paid_at: datetime
    total: Money

# Inventory Service 監聽 OrderPaid → 在自己的 transaction 裡扣庫存
# Billing Service   監聽 OrderPaid → 在自己的 transaction 裡建帳單
```

不要為了強一致而把不相關的 Aggregate 合併。分散式環境裡跨服務強事務代價高昂，最終一致在業務上通常完全可接受。

---

## 對比取捨

| 面向 | Entity | Value Object |
|------|--------|-------------|
| 相等判斷 | identity（ID） | 屬性值（structural） |
| 可變性 | mutable | immutable |
| 生命週期 | 有（生成、修改、刪除） | 無（替換而非修改） |
| 記憶體共用 | 危險（別人改了你看到） | 安全（改了就是新物件） |
| 例子 | Order, Customer, Product | Money, Address, DateRange |

| 面向 | 小 Aggregate | 大 Aggregate |
|------|-------------|-------------|
| 並發競爭 | 低 | 高（更多 row-level lock） |
| 測試難度 | 低 | 高（object graph 複雜） |
| 一致性保證 | 精確 | 過度（保護了不需要一起的東西） |
| Domain Event 數量 | 多（跨邊界） | 少 | 
| 推薦 | 優先選這個 | 只在真有需要時 |

---

## 踩雷集錦

### 錯誤一：把 Aggregate 當成 ER Diagram 的 table 群

**錯誤直覺**：`order` 和 `order_line` 有外鍵關係，所以放同一 Aggregate。

**正確認識**：邊界由不變條件的作用範圍決定，不由資料庫關係決定。`OrderLine` 屬於 `Order` Aggregate 是因為「訂單總額 = sum(lines)」是 Order 的不變條件，不是因為外鍵。

---

### 錯誤二：Value Object 也要給 ID

**錯誤直覺**：「`Money` 是資料庫 row，我應該給它一個 primary key。」

**正確認識**：序列化到資料庫時確實可能需要 surrogate key 讓 ORM 正常運作，但那是持久化層的技術問題，不是領域模型問題。領域層的 `Money` 沒有 identity，兩個 `Money(100, "TWD")` 完全可以互換。不要讓 ORM 需求污染領域模型。

---

### 錯誤三：Aggregate Root 暴露內部集合的引用

**錯誤直覺**：`lines: list[OrderLine]` 作為 public attribute，外部可做 `order.lines.append(raw_line)` 或 `order.lines[0].unit_price = Money(0, "TWD")`——跳過所有不變條件檢查。

**正確認識**：內部集合應是私有的（`_lines`）。外部只能透過根的方法修改。需要讀取時返回只讀 copy 或 tuple，不要返回可變 list 引用。

---

### 錯誤四：一個 Aggregate 跨兩個 Bounded Context

**錯誤直覺**：「`Product` 在倉庫管理和商品目錄都會用到，設計一個大的 `Product` Aggregate，兩邊共用。」

**正確認識**：`Product` 在「商品目錄」Context 是有定價、描述、圖片的展示物件；在「倉庫管理」Context 是有庫存數量、儲位的庫存物件。兩個 Context 各自有自己的 `Product` 模型，透過整合模式（如 ACL）溝通。讓一個 Aggregate 服務兩個 Bounded Context 等於打破了 Context 的邊界。

> 關於 Bounded Context 與整合模式，參見 [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md)。

---

### 錯誤五：把所有操作都丟給 Domain Service，讓 Entity 成為空殼

**錯誤直覺**：「`OrderService.markPaid(order)` 看起來比 `order.markPaid()` 更整潔，邏輯集中在一個地方。」

**正確認識**：這就是 Evans 批評的「貧血模型」——Entity 只是資料容器，邏輯全在 Service 裡。問題是「誰負責確保不變條件？」如果答案是 Service，那換另一個 Service 呼叫時，不變條件就可能被繞過。不變條件的執行者應該是 Entity 或 Aggregate Root 本身。Domain Service 只用在「跨多個 Aggregate、無法歸屬於單一 Entity」的操作。

---

## 進階延伸

### Specification Pattern（規格模式）

當「某個 Entity 是否滿足某條件」的判斷需要在多處重用，可以引入 Specification：一個 immutable Value Object，封裝一個布林謂詞：

```python
@dataclass(frozen=True)
class PaidOrderSpec:
    def is_satisfied_by(self, order: Order) -> bool:
        return order.status == OrderStatus.PAID
```

讓篩選邏輯可組合、可測試、留在領域層（而非散在 SQL where 子句裡）。

### Optimistic Locking

高並發下，多個請求可能同時讀取同一 Aggregate 並嘗試修改。常見做法是在 Aggregate 加版本號：`version: int = 0`，持久化時 `WHERE id=:id AND version=:expected_version`，衝突時拋異常讓上層重試。

### 與 Event Sourcing 的關係

Event Sourcing 不儲存 Aggregate 的當前狀態，而是儲存它收到的 Command 所產生的 Domain Event 序列，重建時重放。這與 Aggregate 設計正交——你可以用傳統 RDBMS 或 Event Sourcing 儲存同一個 Aggregate，後者對 Audit Trail 和時間旅行查詢有天然優勢。

> Domain Event 的詳細設計以及 Event Storming 工作坊見 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

---

## 戰術積木與規格驅動開發的交叉點

Entity、Value Object、Aggregate 在 Spec-Driven Development 裡有另一個角色：它們是規格的語彙骨架。

當你在 [Ch 15 通用語言](./15-ubiquitous-language.md) 裡確立了「Order、OrderLine、Money」，這些詞要一致出現在 spec 文件、LLM prompt、測試、程式碼裡。具體實作：

- **Value Object 的不變條件**（金額不得為負、幣別必須是 ISO 4217）直接寫進 spec 的 Constraint 欄位，LLM 生成的程式碼就必然帶有驗證。
- **Aggregate 邊界決定 spec 的範疇**——每個功能規格只修改一個 Aggregate，讓 LLM 的工作範圍清楚、不越界。
- **Vernon Rule 3（ID 引用）**在規格層就要明確標出跨邊界整合方式，避免 LLM 把異步事件協調寫成同步方法呼叫。

更完整的討論見 [Ch 36 領域模型作為 spec 的骨架](./36-domain-model-as-spec-backbone.md)。

---

## 動手練習

電商退貨（Return）情境，業務規則：退貨申請有唯一編號；包含多個 ReturnLine，每個對應一個原始 OrderLine；退貨數量不可超過原始購買數量；退款金額 = 退貨數量 × 原始單價；狀態 REQUESTED → APPROVED / REJECTED → REFUNDED，只有 APPROVED 可標記為 REFUNDED。

1. 辨識 Entity 與 Value Object，寫出理由。
2. 決定 Aggregate 邊界：`Return` 和 `ReturnLine` 同一個嗎？什麼不變條件支持你的決定？
3. `Return` 和原始 `Order` 的關係依 Vernon Rule 3 怎麼表達？
4. 寫出 `Return` Aggregate Root 的核心方法簽名（不需完整實作，重點是不變條件的執行位置）。
5. 挑戰：`Return` 被 APPROVED 後，庫存 Aggregate 要增加庫存，怎麼跨邊界協調？

---

## 本章重點整理

- **Entity**：相等由 identity 決定，有生命週期與狀態演變。「是不是同一個」不等於「屬性是不是一樣」。
- **Value Object**：相等由屬性決定，無 identity，設計為 immutable。修改就是建立新物件。
- **Aggregate**：一致性邊界，外部只持有 Root 引用，Root 保護所有不變條件，一個 transaction 完整更新邊界內所有物件。
- **Vernon 四規則**：(1) 只保護真正需要一起強一致的不變條件；(2) 保持 Aggregate 小（多數只有 Root + Value Objects）；(3) 跨邊界只持有 ID；(4) 跨邊界用 Domain Event + 最終一致性。
- **邊界由不變條件作用範圍決定，不由資料庫外鍵決定**。
- **貧血模型的反面不是邏輯全塞 Entity**：跨 Aggregate 操作給 Domain Service；單一 Aggregate 的不變條件由 Root 自己保護。

---

## 自我檢核

- [ ] 不翻書，用自己的話解釋：Entity 和 Value Object 的相等判斷為什麼不同？如果面試官問你，你會怎麼答？

- [ ] 給一個 `Address` Value Object 加上一條業務規則（例如「郵遞區號必須是 5 位數字」），並且確保它在建構時被驗證。寫出程式碼。

- [ ] 你有一個 `BlogPost` Aggregate，內部有 `Comment` Entity。外部程式碼可以做 `post.comments[0].body = "spam"` 嗎？如果不行，怎麼防止？

- [ ] Vernon Rule 3 說要透過 ID 引用其他 Aggregate。如果你需要展示訂單清單，同時顯示顧客姓名，你在哪一層做 join？Domain Layer 還是 Application Layer？

- [ ] 一個銀行帳戶轉帳（從帳戶 A 扣款、帳戶 B 入款）跨了兩個 Aggregate，依 Vernon Rule 4 應該怎麼設計？（提示：哪個 Aggregate 先完成，然後發什麼 Event？）

---

## 延伸閱讀

**[Domain-Driven Design: Tackling Complexity in the Heart of Software]** — Eric Evans（Addison-Wesley，2003）
- https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215
- 讀 Part II（Entity、Value Object、Aggregate、Repository）——本章所有概念的一手來源。「Model-Driven Design」一章解釋為什麼戰術積木必須從領域語言長出來。

**[Implementing Domain-Driven Design (Chapter 10: Aggregates)]** — Vaughn Vernon（InformIT / Pearson，2013）
- https://www.informit.com/articles/article.aspx?p=2020371&seqNum=3
- Pearson 官方節錄，直接包含 Vernon 四條規則與 Hedhman 數據。從「Rule: Design Small Aggregates」讀起，五頁建立直覺。

**[Domain-Driven Design Reference: Definitions and Pattern Summaries]** — Eric Evans（CC BY 4.0，2015 版）
- https://www.domainlanguage.com/ddd/reference/
- 每個戰術積木一頁紙，本章所有 Evans 引文的官方來源。確認精確定義用這份。（PDF 有時對自動抓取回傳 HTTP 403，請在瀏覽器直接開啟。）

**[bliki: Domain Driven Design]** — Martin Fowler（更新 2020-04-22）
- https://martinfowler.com/bliki/DomainDrivenDesign.html
- 整篇一頁，五分鐘。二次驗證 Evans 定義的最可靠工具，也是「DDD 不只是 coding pattern」最佳入口。

**[Domain-Driven Design Distilled]** — Vaughn Vernon（Pearson，2016）
- https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420
- 第 5 章（Tactical Design with Aggregates）+ 第 6 章（Domain Events）。200 頁，紅皮書太長時的正確捷徑。

**[Accelerate your Strategic Design with LLMs]** — Thomas Coopman（DDD.academy，2026，版本相依，查證日期 2026-06-30）
- https://ddd.academy/accelerate-your-strategic-design-with-llms/
- 展示如何把戰術積木詞彙餵給 LLM 做 spec 語彙，以及 human-in-the-loop 在戰術建模上不可省略的邊界。

---

下一章把戰術積木家族補完：Repository 給 Aggregate 一個「類似集合」的持久化抽象，Domain Service 處理跨 Aggregate 的無狀態操作，Factory 封裝複雜建構邏輯，Domain Event 讓邊界之間的協調有明確的語言。

→ [Ch 20 Repository / Domain Service / Factory / Domain Event](./20-repositories-services-events.md)
