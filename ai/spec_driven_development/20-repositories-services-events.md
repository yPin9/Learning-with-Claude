# Ch 20 — Repository / Domain Service / Factory / Domain Event

> **目標**：搞清楚戰術建模（tactical modeling）的後半段——持久化層（Repository）怎麼對領域透明、無狀態領域邏輯（Domain Service）到底住哪才對、複雜物件建構（Factory）為何不該塞進 constructor、以及 Domain Event 如何讓跨 Bounded Context 的最終一致性（eventual consistency）變成可讀的領域語言。

> 如果你對 Entity、Value Object、Aggregate 還不熟，先回看 [Ch 19 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md)。

---

## 這四個東西為什麼放在同一章？

前一章的 Entity、Value Object、Aggregate 處理的是**結構**問題：我的領域概念長什麼形狀？

這一章接著回答四個問題：

1. **存儲**：aggregate 怎麼寫進資料庫又讀回來，而不讓 SQL 汙染領域模型？ → Repository
2. **邏輯安置**：某個操作天然就不屬於任何 entity，要放哪裡？ → Domain Service
3. **建構**：aggregate 的初始狀態需要複雜邏輯，constructor 不夠用時怎麼辦？ → Factory
4. **通訊**：某件事在這個 Bounded Context 發生了，鄰居 context 怎麼知道，而不必直接互相呼叫？ → Domain Event

這四個模式合在一起，才讓 aggregate 真正做到「強一致、對外黑盒、跨邊界最終一致」。

---

## 歷史脈絡：在這之前人們怎麼做？

1990 年代的三層式架構讓 business layer 充斥 `SqlCommand`、`DataReader`。你寫的是**以資料庫為中心**的程式：先想 schema、再想 class。業務規則散在 stored procedure 和 UI 之間，換資料庫代價極高，單元測試幾乎不可能。

2003 年 Eric Evans 在 *Domain-Driven Design*（下稱「藍皮書」）裡明確說：領域模型應該不知道自己被怎麼存儲。Repository 模式把這個想法具體化——domain code 面對的是一個**集合（collection）的抽象**，不是 `INSERT INTO orders`。同一本書也給 Domain Service、Factory、Domain Event 立了名分，讓「邏輯去哪裡安置」有了可以在團隊內溝通的語彙。

---

## Repository：讓 aggregate 以為它住在記憶體裡

把 Repository 想成一個**魔法箱子**：你把 aggregate 丟進去，它幫你記住；你把 ID 傳進去，它把 aggregate 還給你。箱子裡是 PostgreSQL 還是 in-memory map，aggregate 完全不在乎。

```
Application Layer
        │
        │  order_repo.add(order)    order_repo.of_id(order_id)
        ▼
┌──────────────────────┐
│  OrderRepository     │  ← 介面（interface），住在 domain layer
│  (ABC)               │
└──────────┬───────────┘
           │ 依賴注入
           ▼
┌──────────────────────┐
│  SqlOrderRepository  │  ← 實作，住在 infrastructure layer
└──────────────────────┘
           │
           ▼
        PostgreSQL
```

Evans 原文：Repository「represents all objects of a certain type as a conceptual set」（把某種類型的所有物件表達為一個概念上的集合）。

### 三條規則

- **一個 aggregate root 對應一個 Repository**。不要替非 root entity 開 Repository，那會繞過 aggregate 的一致性邊界。
- Repository 的**介面**屬於 domain layer，**實作**屬於 infrastructure layer。依賴倒置（Dependency Inversion）讓領域不依賴基礎設施。
- 方法名稱來自通用語言（Ubiquitous Language），不要反映 SQL 動詞。`of_id()` 比 `select_by_id()` 更領域化；`with_status()` 比 `select_where_status()` 更自然。

### 範例：OrderRepository 介面與雙實作

```python
# domain/repositories.py — 屬於 domain layer
from abc import ABC, abstractmethod
from typing import Optional
from domain.order import Order, OrderId

class OrderRepository(ABC):
    @abstractmethod
    def add(self, order: Order) -> None: ...
    @abstractmethod
    def of_id(self, order_id: OrderId) -> Optional[Order]: ...
    @abstractmethod
    def save(self, order: Order) -> None: ...

# 測試用：不需要真實 DB
class InMemoryOrderRepository(OrderRepository):
    def __init__(self): self._store: dict[str, Order] = {}
    def add(self, order): self._store[str(order.id)] = order
    def of_id(self, order_id): return self._store.get(str(order_id))
    def save(self, order): self._store[str(order.id)] = order
```

```python
# infrastructure/sql_order_repository.py — 屬於 infra layer
import sqlite3
from domain.repositories import OrderRepository
from domain.order import Order, OrderId, OrderStatus
from domain.value_objects import Money
from typing import Optional

class SqlOrderRepository(OrderRepository):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        conn.execute("""CREATE TABLE IF NOT EXISTS orders
            (id TEXT PRIMARY KEY, customer_id TEXT,
             status TEXT, total_amount REAL, currency TEXT)""")
        conn.commit()

    def add(self, order: Order) -> None:
        self._conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?)",
            (str(order.id), str(order.customer_id),
             order.status.value, order.total.amount, order.total.currency))
        self._conn.commit()

    def of_id(self, order_id: OrderId) -> Optional[Order]:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE id=?", (str(order_id),)).fetchone()
        if not row: return None
        return Order.reconstitute(
            order_id=OrderId(row[0]), customer_id=row[1],
            status=OrderStatus(row[2]),
            total=Money(amount=row[3], currency=row[4]))

    def save(self, order: Order) -> None:
        self._conn.execute(
            "UPDATE orders SET status=?,total_amount=?,currency=? WHERE id=?",
            (order.status.value, order.total.amount, order.total.currency, str(order.id)))
        self._conn.commit()
```

這個設計讓 domain 單元測試完全不需要資料庫。

---

## Domain Service：無狀態邏輯的安身之所

有時候業務邏輯「天然」跨越多個 aggregate，硬要放進其中一個感覺都不對。Evans 說這種情形，把邏輯提煉成**無狀態（stateless）的服務**，用通用語言裡的**動詞**命名。

Evans 原文：「an operation offered as an interface that stands alone in the model, without encapsulating state」。

### 三種 Service 的分野

| | Domain Service | Application Service | Infrastructure Service |
|---|---|---|---|
| **住在** | Domain layer | Application layer | Infrastructure layer |
| **包含** | 純領域邏輯 | 用例編排（orchestration） | 技術細節（email、DB、queue） |
| **有無狀態** | 無 | 無（偏）| 可能有 |
| **能呼叫 repo？** | 不應該 | 可以 | N/A |
| **典型名稱** | `PricingService` | `PlaceOrderUseCase` | `SmtpEmailSender` |

Domain Service 不呼叫 Repository——那是 Application Service 的協調工作。它只做純粹的計算或規則判斷。

### 範例：跨 aggregate 的折扣計算

```python
# domain/pricing_service.py
from domain.order import Order
from domain.customer import Customer, CustomerTier
from domain.value_objects import Discount

class PricingService:
    """無狀態：同樣的輸入永遠得到同樣的輸出，無副作用。"""

    def calculate_discount(
        self, customer: Customer, order: Order, is_promo: bool
    ) -> Discount:
        rate = 0.0
        if customer.tier == CustomerTier.PLATINUM:
            rate += 0.15
        elif customer.tier == CustomerTier.GOLD:
            rate += 0.10
        if order.total_item_count >= 10:
            rate += 0.05
        if is_promo:
            rate += 0.03
        return Discount(rate=min(rate, 0.25), original_total=order.total)

# 在測試裡，不需要 mock 任何 infra：
svc = PricingService()
result = svc.calculate_discount(platinum_customer, order_12_items, is_promo=True)
# → Discount(rate=0.23, ...)   # 15% + 5% + 3% = 23%
```

---

## Factory：把複雜建構從 aggregate 中解放出來

Evans：「encapsulates the knowledge needed to create a complex object or AGGREGATE」（封裝建立複雜物件或 aggregate 所需的知識）。

Factory 解決的問題：建立 aggregate 時需要多個預設值、跨欄位驗證、甚至依賴外部資源（如 UUID 生成器）。把這些邏輯塞進 constructor 會讓 aggregate 自身責任模糊，讓客戶端也需要了解太多內部細節。

常見的兩種形式：**工廠方法（factory method）** 掛在 aggregate 上，**獨立工廠類別（factory class）** 則封裝需要注入的依賴。

```python
class Order:
    def __init__(self, id, customer_id, status, total, total_item_count):
        # 這個 constructor 留給 reconstitute（從 DB 還原）用
        self.id = id; self.customer_id = customer_id
        self.status = status; self.total = total
        self.total_item_count = total_item_count
        self._events: list = []

    @classmethod
    def place(cls, order_id, customer_id, items) -> "Order":
        """工廠方法：封裝下單的建立語義，同時觸發 OrderPlaced 事件。"""
        if not items:
            raise ValueError("訂單至少要有一個商品")
        total = sum((i.subtotal for i in items), Money(0.0, "TWD"))
        order = cls(order_id, customer_id, OrderStatus.PENDING, total,
                    sum(i.quantity for i in items))
        order._events.append(
            OrderPlaced(order_id=str(order_id), customer_id=str(customer_id),
                        total_amount=total.amount))
        return order

    @classmethod
    def reconstitute(cls, **kwargs) -> "Order":
        """從持久化層重建，不重新觸發建立事件。"""
        return cls(**kwargs)

    def pop_events(self) -> list:
        events, self._events = self._events, []
        return events
```

`Order.place()` 和 `Order.reconstitute()` 分開的意義：建立時觸發業務事件，重建時不觸發（資料庫裡的 Order 早就「已經」被下單了，不需要重新宣告）。

---

## Domain Event：讓「發生了什麼事」成為一等公民

### 命令式 vs 事件式

傳統 procedure call 是**命令式**：「去做 X」。Domain Event 是**事件式**：「X 已經發生了」。

```
命令式：ShipOrder(orderId)  → 你主動告訴 Shipping Context 去做
事件式：OrderPlaced(...)   → Shipping Context 自己訂閱、自己回應
```

這個轉換讓 Ordering Context 不需要知道 Shipping Context 的存在。這是跨 Bounded Context 的**最終一致性**的核心機制。

> 如果你對 Bounded Context 和 Context Mapping 還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md) 和 [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md)。

### 命名規則：過去式

Evans 和 Vernon 都強調：Domain Event 用**過去式**命名，反映已完成的事實。

```
OrderPlaced  PaymentReceived  OrderShipped  InventoryReserved
```

「已發生」的語義很重要：你不能取消已發生的事，只能再發另一個事件（`OrderCancelled`）來抵消。

### OrderPlaced 如何串聯多個 context

```
[Ordering Context]
  Order.place() 建立 Order，積累 OrderPlaced 事件
       │
       │ application layer commit 後發佈
       ▼
  OrderPlaced { order_id, customer_id, total: 5000 TWD, occurred_at }
       │
       ├────────────────────────┐
       ▼                        ▼
[Shipping Context]       [Billing Context]
  建立 Shipment(Pending)   建立 Invoice(Draft)
```

如果明天要加一個 Loyalty Context 來累點，只需訂閱 `OrderPlaced`，不需改動 Ordering Context 的任何一行程式碼。

### 範例：Domain Event + in-process EventBus

```python
# domain/events.py
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass(frozen=True)
class DomainEvent:
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str = ""
    customer_id: str = ""
    total_amount: float = 0.0
    currency: str = "TWD"

# infrastructure/event_bus.py
from collections import defaultdict
from typing import Callable, Type

class EventBus:
    def __init__(self): self._handlers = defaultdict(list)
    def subscribe(self, event_type: Type[DomainEvent], handler: Callable):
        self._handlers[event_type].append(handler)
    def publish(self, event: DomainEvent):
        for h in self._handlers[type(event)]: h(event)

# 使用：
bus = EventBus()
bus.subscribe(OrderPlaced,
    lambda e: print(f"[Shipping] 訂單 {e.order_id} 準備出貨"))
bus.subscribe(OrderPlaced,
    lambda e: print(f"[Billing] 建立發票，金額 {e.total_amount} {e.currency}"))

order = Order.place(order_id=OrderId("ord-001"),
                    customer_id=CustomerId("cust-001"), items=items)
repo.add(order)
for event in order.pop_events():   # application layer 在 commit 後發佈
    bus.publish(event)
# 輸出：
# [Shipping] 訂單 ord-001 準備出貨
# [Billing] 建立發票，金額 5000.0 TWD
```

### 為何 aggregate 不直接呼叫 event bus？

Vernon（Red Book）的建議：**aggregate 把事件存在 `_events` 列表，application layer 在 transaction commit 後統一發佈**。若 aggregate 直接呼叫 event bus：一、aggregate 依賴了 infrastructure 概念；二、DB transaction 回滾時事件已發出，造成不一致。`pop_events()` 正是這個模式的實現。

---

## 四個模式定位對比

| 模式 | 解決什麼問題 | 住在 | 有無狀態 | 典型方法 |
|---|---|---|---|---|
| Repository | 對領域隱藏持久化 | 介面 domain，實作 infra | 有（管集合） | `add()`, `of_id()`, `save()` |
| Domain Service | 安置無狀態跨 entity 邏輯 | Domain layer | 無 | `calculate_discount()` |
| Factory | 封裝複雜 aggregate 建構 | Domain layer | 無 | `Order.place()`, `reconstitute()` |
| Domain Event | 表達已發生的領域事實，跨 context 通訊 | 定義在 domain，傳輸在 infra | 不可變 | 過去式命名，frozen dataclass |

---

## 踩雷集錦

**雷 1：把 SQL 查詢邏輯寫進 aggregate**
錯誤直覺：「讓 `Order.load(order_id, db_conn)` 自己去 DB 撈最方便。」
正確認識：aggregate 不應該知道持久化細節。一旦持有 db connection，infrastructure 概念就洩漏到領域層，測試也無從下手。

**雷 2：把 Application Service 的用例編排放進 Domain Service**
錯誤直覺：「`PlaceOrderService` 要查 repo、建 Order、發事件，放進 Domain Service 就好。」
正確認識：那是 Application Service 的工作。Domain Service 不碰 Repository、不呼叫 event bus，只做純領域計算。把編排邏輯放進 Domain Service 會讓它變成沒有約束的垃圾桶。

**雷 3：Domain Event 用命令式或現在式命名**
錯誤直覺：「`PlaceOrder`、`ShipOrder` 比較清楚。」
正確認識：`PlaceOrder` 是命令（command），表示「去做」；`OrderPlaced` 是事件，表示「已完成」。命令可以失敗、可以拒絕；事件是既成事實，不可撤回。語義混淆，跨 context 的協調就會崩潰。

**雷 4：Factory 呼叫 Repository**
錯誤直覺：「Factory 建完物件就順手存進 DB 最省事。」
正確認識：Factory 的職責是**建立**，Repository 的職責是**存取**，由 Application layer 在兩者之間協調。Factory 不知道 Repository 的存在。

**雷 5：aggregate 直接呼叫 event bus 發事件**
錯誤直覺：「事件就在 aggregate 裡發生，aggregate 自己發最直接。」
正確認識：如果 DB transaction 失敗回滾，事件已發出——這是嚴重的不一致。正確模式是 aggregate 存起來，commit 後 Application layer 統一發佈。

---

## 進階延伸

**Outbox Pattern**：在生產環境，「寫 DB + 發事件到 Kafka」不在同一個 transaction 裡。Outbox Pattern 的解法：把事件寫入同一個 DB 的 `outbox` 表（同一 transaction），再由背景進程（或 Debezium 之類的 CDC 工具）轉發到 message queue。這是把 Domain Event 搬進真實分散式系統時幾乎必要的補丁。

**CQRS**：Repository 的 `of_id()` 適合取單一 aggregate，但「過去 30 天最高金額的前 10 筆訂單」不應該把 100 萬個 Order aggregate 撈進 memory 再排序。CQRS（Command Query Responsibility Segregation，命令查詢責任分離）把命令路徑和讀模型路徑分開；Domain Event 可以作為讀模型的更新來源。

---

## 動手練習

在電商情境（訂單、顧客、庫存）中：

1. 設計 `InventoryRepository` 介面，方法名稱必須使用通用語言（不要 `findById`，要用領域動詞）。
2. 思考：`InventoryAllocationService` 是否應該接受 `InventoryRepository` 作為參數？按本章的 Domain Service 定義，這樣設計有何矛盾？寫下你的決策與理由。
3. 讓 `Order.place()` 額外發出 `InventoryReserved` 事件，在 Inventory Context 端寫訂閱者，列印被鎖定的商品與數量。
4. （進階）在 `SqlOrderRepository.add()` 中，同時把 event 寫入 `outbox` 表，再寫一個 `process_outbox(conn, bus)` 函式模擬背景 poller。

---

## 本章重點整理

- **Repository**：aggregate 的集合抽象，對領域隱藏存儲細節；介面屬於 domain layer，實作屬於 infra layer；一個 aggregate root 對應一個 Repository。
- **Domain Service**：安置無狀態的跨 entity 領域邏輯；不呼叫 Repository、不依賴 infra；以通用語言動詞命名。
- **Factory**：封裝複雜的 aggregate 建構；工廠方法（`Order.place()`）和 `reconstitute()` 分開——建立時觸發事件，重建時不觸發。
- **Domain Event**：過去式命名，描述不可撤回的已發生事實；aggregate 內存、application layer 在 transaction commit 後發佈；是跨 Bounded Context 最終一致性的核心。
- 四個模式的協作流程：Factory 建立 aggregate → aggregate 積累 events → application layer 呼叫 Repository 存檔 → 發佈 events → 其他 context 的 handlers 回應。

---

## 自我檢核

- [ ] 用自己的話解釋：為什麼 Repository 介面要住在 domain layer 而不是 infra layer？面試被問到「你們怎麼做持久化」，你會怎麼答？
- [ ] 舉一個具體例子，說明什麼邏輯應該是 Domain Service 而不是放進某個 entity 的方法，並說明你的判斷標準。
- [ ] `Order.place()` 和 `Order.reconstitute()` 的差別是什麼？為什麼需要兩條分開的建立路徑？
- [ ] 解釋「aggregate 存 event，application layer 在 commit 後才發佈」解決了什麼問題。不這樣做會發生什麼？
- [ ] 如果 Loyalty Context 想在每次 `OrderPlaced` 後幫顧客累積點數，你需要改動 Ordering Context 的哪些程式碼？（正確答案：零行。）

---

## 延伸閱讀

- **Domain-Driven Design: Tackling Complexity in the Heart of Software — Eric Evans（2003）**
  直接讀 Part II Ch 6（Aggregates）和 Ch 7（Factory, Repository）。Repository 和 Factory 的原始定義與動機在這裡，Evans 的論述比任何二手介紹都精準。
  https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215

- **DDD Reference: Definitions and Pattern Summaries — Eric Evans（2015, CC BY 4.0）**
  每個模式一頁的濃縮定義，含 Domain Event（這在 2003 藍皮書裡是薄弱環節，2015 版才補上）。
  https://www.domainlanguage.com/ddd/reference/

- **Implementing Domain-Driven Design（紅皮書）— Vaughn Vernon（2013）**
  看 Ch 7（Domain Services）和 Ch 8（Domain Events）。Vernon 對「aggregate 儲存 events + application layer 發佈」和 Domain Service vs Application Service 的判斷標準有最清晰的論述。
  https://www.informit.com/articles/article.aspx?p=2020371

- **Domain-Driven Design Distilled — Vaughn Vernon（2016）**
  最快上手的 DDD 入門書。Ch 6 覆蓋 Domain Events 和 Event Sourcing，是理解 Domain Event 在系統中角色的最佳起點。
  https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420

- **bliki: Domain Driven Design — Martin Fowler**
  一頁總覽，尤其是 Fowler 對「哪些邏輯屬於 Domain Service」的反例說明（不要把銀行轉帳放進 Account entity）。
  https://martinfowler.com/bliki/DomainDrivenDesign.html

下一章我們把這些戰術模式推到工作坊裡：透過 Event Storming 的橙色便利貼，找出哪些 Domain Event 應該存在、哪些是 aggregate，以及 Bounded Context 的邊界要畫在哪裡。

→ [Ch 21 Event Storming 工作坊](./21-event-storming.md)
