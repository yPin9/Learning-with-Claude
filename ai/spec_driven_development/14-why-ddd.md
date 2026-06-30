# Ch 14 — 為什麼 DDD：複雜性在領域，不在技術

> **目標**：理解 Eric Evans 藍皮書的核心論點——多數軟體的真正難處是業務領域的複雜性，而不是技術本身；並掌握 model-driven design（模型驅動設計）的精神，作為進入後續 DDD 戰略/戰術章節的心智基礎。

---

## 先給一個直覺：軟體為什麼老是爛？

在一個電商公司，工程師花了三個月用最新的微服務架構、Kubernetes 叢集、GraphQL API 重寫了訂單系統。技術選型無可挑剔。上線之後，業務團隊說：「還是不對，我們的『訂單』根本不是你們寫的那樣。」

問題不在 Kubernetes，不在 GraphQL。問題在於工程師腦中的「訂單」和業務員腦中的「訂單」，是兩個不同的東西。

這正是 Eric Evans 在 2003 年《Domain-Driven Design: Tackling Complexity in the Heart of Software》（領域驅動設計：軟體核心複雜性的對抗策略，Addison-Wesley）裡說的：**多數軟體的難處是業務領域的複雜性，不是技術**。

---

## 在 DDD 之前，人們怎麼做？

2003 年以前，軟體開發圈主流的思路大致是這樣的：

1. **資料庫中心**（Database-centric）：先設計 ER 圖，再用 CRUD 包一層。領域邏輯散落在 stored procedure 或 service layer 的 if/else 叢林裡。
2. **事務腳本**（Transaction Script，Fowler 的命名）：每個業務操作寫成一個大方法，把所有步驟硬塞進去。容易入門，但稍有複雜度就變成無法維護的義大利麵。
3. **貧血模型**（Anemic Domain Model）：有物件，但物件沒有行為，只有 getter/setter。所有邏輯丟給 service 類別，物件只是 struct 的偽裝。

這三種做法在系統複雜度低的時候勉強管用。一旦業務規則開始積累——折扣算法有七種例外、退款流程跨三個部門、一個「客戶」在不同業務線有不同含義——技術債就以指數速度爆炸。

為什麼？因為**沒有一個地方能讓你清楚看到領域長什麼樣**。領域知識分散在資料庫欄位名稱、service 方法、Excel 文件、口頭慣例、還有某個離職員工的腦袋裡。

一個典型的失敗模式長這樣：

```
第一年：OrderService.processOrder() 100 行，業務邏輯清楚
第二年：加了退款，OrderService.refund() 80 行，有些邏輯重複
第三年：加了分期付款，OrderService 500 行，method 之間互相呼叫，
        哪些組合是合法的沒有人說得清楚
第四年：沒有人敢動 OrderService，改一個地方不知道會壞幾個地方
        → 新功能繞過去寫，又一個 PaymentService.handleSpecialOrders()
第五年：「我們重寫吧」
```

這個循環在各種規模的公司反覆發生，和用什麼技術無關。

---

## Evans 的核心論點：複雜性的來源

Evans 在藍皮書裡開宗明義：

> 為什麼大多數軟體專案失敗，或成本遠超預期？不是因為程式語言不夠好，不是因為基礎設施不夠強，而是因為我們沒有能夠忠實反映業務本質的**模型（model）**。

他觀察到一個現象：技術複雜性是**人造的**（accidental complexity），通常有工具和模式可以解決。但業務領域的複雜性是**本質的**（essential complexity）——你沒辦法用更好的框架讓保險核保規則變簡單；規則本身就很複雜，你只能想辦法讓它在程式碼裡**可見、可管理**。

這個「本質複雜性 vs 人造複雜性」的區分，其實借用自 Fred Brooks 1986 年的論文〈No Silver Bullet〉。Brooks 說軟體的本質困難（essential difficulties）包含複雜性（complexity）、一致性（conformity）、可變性（changeability）、不可見性（invisibility）。Evans 繼承了這個框架，但把焦點收窄到「業務領域的複雜性」，並給了一套具體的應對策略。

用一張圖來對比兩種世界觀：

```
舊世界觀                          Evans 的世界觀
────────────────────────────      ────────────────────────────
技術問題 = 核心難題              領域知識 = 核心難題
        ↓                                ↓
資料庫設計先行                  領域模型先行
        ↓                                ↓
業務邏輯 = 糊在 service 裡      業務邏輯 = 活在 domain objects 裡
        ↓                                ↓
工程師與業務各說各話            通用語言橋接雙方
        ↓                                ↓
重構時沒有北極星                模型是設計的北極星
```

---

## Model-Driven Design 的精神

DDD 的核心實踐叫做**模型驅動設計（Model-Driven Design）**，它的精神可以用一句話說清楚：

**讓程式碼直接說出業務是什麼，而不是說出資料怎麼存。**

這意味著：
- 程式碼裡的類別名稱、方法名稱，應該是業務專家會說出口的詞
- 業務規則寫在 domain object 的方法裡，不是散在 service 的條件判斷
- 當業務專家說「這個邏輯不對」，你能直接找到對應的程式碼區塊

一個小型具體例子。假設我們做的是一個訂單系統。

**沒有模型的做法（Transaction Script）：**

```python
# 這段程式碼在說「怎麼操作資料」，不是在說「業務是什麼」
def process_order(order_id, user_id, db):
    order = db.query("SELECT * FROM orders WHERE id = ?", order_id)
    user = db.query("SELECT * FROM users WHERE id = ?", user_id)
    
    if order["status"] != "pending":
        raise Exception("Invalid status")
    
    if user["credit_limit"] < order["total"]:
        raise Exception("Credit exceeded")
    
    # 更多 if/else...
    db.execute("UPDATE orders SET status='confirmed' WHERE id=?", order_id)
    db.execute("INSERT INTO audit_log ...")
```

**有領域模型的做法：**

```python
# 這段程式碼在說「業務是什麼」
class Order:
    def __init__(self, order_id: OrderId, lines: list[OrderLine]):
        self.id = order_id
        self.lines = lines
        self.status = OrderStatus.PENDING

    def confirm(self, customer: Customer) -> "OrderConfirmed":
        """
        業務規則：只有 PENDING 的訂單可以確認；客戶信用額度必須足夠。
        回傳 Domain Event，不直接寫資料庫。
        """
        if self.status != OrderStatus.PENDING:
            raise OrderAlreadyProcessedError(self.id)
        
        if not customer.has_sufficient_credit(self.total()):
            raise InsufficientCreditError(customer.id, self.total())
        
        self.status = OrderStatus.CONFIRMED
        return OrderConfirmed(order_id=self.id, customer_id=customer.id)

    def total(self) -> Money:
        return sum(line.subtotal() for line in self.lines)


# 應用層只是編排，領域邏輯不在這裡
class ConfirmOrderUseCase:
    def execute(self, order_id: OrderId, customer_id: CustomerId):
        order = self.order_repo.of_id(order_id)
        customer = self.customer_repo.of_id(customer_id)
        
        event = order.confirm(customer)  # 業務規則在 Order 裡
        
        self.order_repo.save(order)
        self.event_bus.publish(event)
```

注意幾件事：
- `Order.confirm()` 的方法名稱是業務術語，不是 `updateOrderStatus()`
- 違規規則拋出具名例外（`InsufficientCreditError`），不是通用 `Exception("Credit exceeded")`
- `Money` 是一個 Value Object，不是裸的 `float`——這讓「加法跨幣種」這類錯誤無法在型別層級發生
- `OrderConfirmed` 是 Domain Event（領域事件），紀錄「有意義的事情發生了」
- 應用層的 `ConfirmOrderUseCase` 只做編排，沒有任何業務判斷——業務規則集中在 `Order` 裡

這個差距在系統小的時候不明顯。但當「訂單」有七種狀態、四種取消規則、三種退款路徑，**能不能在程式碼裡清楚讀出業務**，就是可維護與不可維護的分界線。

你也可以從測試角度感受差距。貧血模型的測試長這樣：

```python
# 測試 OrderService，需要 mock 資料庫，需要設定整個環境
def test_confirm_order_credit_exceeded(mock_db):
    mock_db.query.side_effect = [
        {"id": 1, "status": "pending", "total": 1000},
        {"id": 42, "credit_limit": 500}
    ]
    with pytest.raises(Exception, match="Credit exceeded"):
        process_order(order_id=1, user_id=42, db=mock_db)
```

Rich Domain Model 的測試長這樣：

```python
# 測試 Order，不需要任何 mock——業務邏輯不依賴基礎設施
def test_confirm_order_credit_exceeded():
    order = Order(OrderId(1), [OrderLine(ProductId(99), Quantity(1), Money(1000, "TWD"))])
    customer = Customer(CustomerId(42), credit_limit=Money(500, "TWD"))
    
    with pytest.raises(InsufficientCreditError):
        order.confirm(customer)
```

測試速度快十倍，測試意圖一眼看懂，不需要理解資料庫結構。

---

## 為什麼不選替代方案？

### 為什麼不用資料庫中心設計？

資料庫是持久化細節，業務規則的表達能力受資料庫結構限制。當業務需要「同一個概念在不同脈絡下有不同行為」，資料庫欄位無法表達這種語義差異。更現實的問題：schema 改動成本高，而業務規則變化頻繁。

### 為什麼不用事務腳本？

對於 CRUD 型的簡單系統，事務腳本夠用。Evans 本人沒有說事務腳本一定錯；問題是**它不能隨業務複雜度線性擴展**。一旦有了互相影響的業務規則，你就必須在所有的方法裡重複防禦，最終沒有人能確定「改了這段會不會壞掉那段」。

### 為什麼不純靠微服務拆分？

微服務是部署/擴展單元的劃分，不是業務語義的劃分。你可以有一百個微服務，但每個微服務裡面仍然是爛掉的貧血模型。DDD 的 Bounded Context 是業務語義邊界，微服務可以對應到它，但不能取代它。

---

## DDD 的兩個半部：戰略與戰術

Evans 把 DDD 分成兩個層次，這是理解後續章節的關鍵框架：

| 層次 | 問題 | 主要工具 |
|------|------|---------|
| **戰略設計（Strategic Design）** | 整個系統怎麼劃分？各部分怎麼互動？ | 通用語言、Bounded Context、Context Map、子領域分類 |
| **戰術設計（Tactical Design）** | 單一 Bounded Context 內部怎麼建模？ | Entity、Value Object、Aggregate、Repository、Domain Service、Factory、Domain Event |

很多團隊只學戰術——學了 Entity 和 Repository，然後說「我們在做 DDD」。Evans 的本意是**戰略優先**：先搞清楚邊界和語言，再決定裡面的結構。

本章之後，接下來幾章（Ch 15–21）會逐一深入這些工具。這裡先建立全景。

```
整個問題空間（Problem Space）
├── 子領域（Subdomain）
│   ├── Core Domain：競爭優勢所在，最值得投入
│   ├── Supporting：需要但非差異化
│   └── Generic：買現成的（auth、email、支付）
│
解決方案空間（Solution Space）
├── Bounded Context A（有自己的通用語言 + 模型）
├── Bounded Context B
└── Bounded Context C
    Context Map 描述三者的關係
```

---

## 對比取捨

| 方案 | 優點 | 缺點 | 適合情境 |
|------|------|------|---------|
| 事務腳本 | 容易入門、直覺 | 業務規則增長後無法管理 | 簡單 CRUD、原型 |
| 貧血模型 + 胖 Service | 熟悉的 MVC 感覺 | 領域邏輯分散、難測試 | 過渡期、遺留系統 |
| Active Record | 模型與持久化合一，快 | 業務與 ORM 耦合死、難換儲存 | 早期 Rails 應用 |
| **DDD（Rich Domain Model）** | 業務意圖可讀、易測試、可演化 | 學習曲線、初期設計成本高 | 業務邏輯複雜、長期維護 |
| CQRS + Event Sourcing | 讀寫分離、完整歷史 | 概念複雜、事件版本遷移難 | 高稽核需求、高吞吐讀取 |

DDD 不是萬靈丹。Evans 在藍皮書裡明確說：對於「資料輸入/輸出為主、幾乎沒有業務規則」的系統，使用 DDD 是過度設計。問題是判斷何時「複雜度夠高」，往往在遺留債累積之後才後知後覺。

---

## 踩雷集錦

**錯誤直覺 1：DDD 就是學一堆設計模式（Entity、Repository…），把它們套進去就對了。**

正確認識：設計模式是戰術工具。Evans 說過他最大的遺憾之一，是書名裡的「設計」讓人以為 DDD 是一套設計模式集。DDD 的核心是**持續的知識提煉（Knowledge Crunching）**——工程師和領域專家反覆對話，把隱藏在腦袋和文件裡的知識，萃取成清晰的模型。沒有這個過程，只是把 class 改名叫 Entity，什麼都沒有改變。

**錯誤直覺 2：先把領域模型設計好，再開始實作。**

正確認識：DDD 的模型是**演化的**，不是預先設計好的。通用語言會隨著你對領域理解加深而改變；Aggregate 邊界畫錯了就重畫；Bounded Context 發現邊界不對就調整。藍皮書裡 Evans 明確說：「精緻的模型是深度理解之後的產物，不是前置條件。」期待一次設計到位是瀑布思維的殘留。

**錯誤直覺 3：DDD 只是「把 class 命名得更像業務術語」的風格指南。**

正確認識：命名是表層現象。DDD 真正的要求是**行為要對齊業務語義**。一個 `Order` class 如果只有 `getStatus()`/`setStatus()` 而沒有 `confirm()`、`cancel()` 等業務方法，命名再像業務也沒用——領域邏輯還是散在別處。更深一層：Aggregate 的設計要能**強制執行業務不變量（invariants）**，這是 Repository 和事務邊界的設計依據，跟風格無關。

**錯誤直覺 4：一個大型系統應該有一個統一的全域領域模型。**

正確認識：統一模型（Unified Model）在大型系統裡是反模式。「客戶（Customer）」在銷售部門、客服部門、財務部門，有不同的屬性、不同的規則、不同的生命週期。強行統一只會讓模型變成一個誰都不滿意的最大公約數，而且任何人的需求改動都可能影響所有人。DDD 的答案是 Bounded Context：讓「客戶」在不同脈絡下有不同模型，明確管理它們的關係。

**錯誤直覺 5：領域專家不懂技術，讓他們看程式碼沒有意義。**

正確認識：通用語言（Ubiquitous Language）的目的，不是要領域專家看懂 Python，而是要讓程式碼裡的詞彙和業務談話裡的詞彙一致。當業務說「這筆訂單需要人工審核」，工程師說「我們需要把 status 設為 3」——這個詞彙斷裂就是 bug 的溫床。工程師需要學業務的語言，然後把它帶進程式碼，而不是反過來。

---

## 歷史脈絡：Evans 藍皮書的出版背景

2003 年是個有趣的時間點。Enterprise JavaBeans（EJB 2.x）正在讓整個 Java 企業界痛苦——一個業務方法要繞過七層抽象。Martin Fowler 的《Patterns of Enterprise Application Architecture》（2002）剛出版，提出了 Domain Model、Transaction Script、Active Record 等模式，並明確說明什麼情況下用什麼。

Evans 的藍皮書建立在這個基礎上，但走得更遠：它不只給模式，它給**一套完整的思維框架**，說明「為什麼領域知識是設計的核心，以及如何持續提煉它」。

藍皮書有個著名的聲譽：難讀。Fowler 自己說「這本書值得付出的努力」（repays the effort）。前幾章是概念密集的論述，後面才是模式。很多人半途而廢，只帶走了 Entity/Repository 這些表層概念。Part 1（Putting the Domain Model to Work）和 Part 4（Strategic Design）是兩個最值得優先讀的部分。

Evans 後來出版了《Domain-Driven Design Reference》（2015，CC BY 4.0，免費），是每個模式的精簡定義集，適合當快速查閱手冊。Vaughn Vernon 的《Domain-Driven Design Distilled》（2016）則是最適合入門的短篇入口。

---

## 與本課程的連結

> 如果你對「規格 vs 設計 vs 實作」的分層還不熟，先回看 [Ch 7 規格 vs 設計 vs 實作](./07-spec-design-implementation.md)。

> 如果你想複習「為什麼需求這麼難」的根本原因，可以先看 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)。

DDD 和規格驅動開發（SDD）共享同一個信念：**意圖必須先被清楚表達，實作才有正確的北極星**。在 AI 加速實作的時代，這個信念更加重要——因為 LLM 會把模糊的意圖解讀成它認為合理的程式碼，而不是你真正想要的業務行為。我們在 Part 5（Ch 33–37）會回來深挖 DDD 和 SDD 的融合。

---

## 進階延伸

**大泥球（Big Ball of Mud）問題**：Foote and Yoder 在 1999 年的論文〈Big Ball of Mud〉描述了最常見的軟體架構：沒有架構。系統從小需求一路堆砌，結構逐漸消融。理解這個反模式，才能感受 DDD 試圖對抗的是什麼。

**Knowledge Crunching（知識提煉）**：藍皮書第一章就是這個概念。Evans 描述他和一個衍生性商品交易員一起工作的過程——每次對話都讓模型更精確。他們在白板上畫模型、用術語討論，模型裡沒有的概念就不討論，直到雙方的模型對齊。這個過程無法跳過，也無法外包給工具。

**從 DDD 到事件溯源（Event Sourcing）**：Domain Event 是 DDD 戰術建模的一部分，但事件溯源是一個更強的承諾——把事件序列作為系統的唯一真相來源，而不是當前狀態。Greg Young 和 Udi Dahan 是這個方向的重要思想家。不是每個 DDD 系統都需要事件溯源；這是一個可選的進階選擇，有它自己的代價（事件 schema 版本遷移、投影維護）。

**DDD 和微服務的關係**：2014 年之後，隨著微服務風潮興起，DDD 的 Bounded Context 被大量拿來作為服務劃分的依據（「一個 BC 一個微服務」是常見的啟發法）。但這個對應不是 Evans 的主張，而是後來社群的延伸。Sam Newman 的《Building Microservices》和 Chris Richardson 的《Microservices Patterns》是這個方向的代表著作；它們與 DDD 互補但不等同，使用時要注意區分。

---

## 動手練習

選一個你熟悉的業務場景（電商、租屋、餐廳訂位、圖書館借閱……任何一個），完成以下三件事：

1. **列出你聽到的詞彙**：跟一個「真實的業務人員」（或假裝你是業務人員）對話 10 分鐘，寫下他們用的每一個名詞和動詞。
2. **找出詞彙衝突**：有沒有同一個詞在不同脈絡下意思不同？有沒有技術人員用的詞業務人員根本不說？
3. **寫出一個 class**：根據你的觀察，寫一個最核心概念的 class（20–40 行），讓方法名稱全部來自業務詞彙，並在一個方法裡強制執行一條業務規則。

不需要跑起來——重點是把腦中的業務知識，試著轉換成程式碼的語言。

---

## 本章重點整理

- Evans 藍皮書（2003）的核心論點：多數軟體的根本難處是**業務領域複雜性**，不是技術複雜性。
- **模型驅動設計（Model-Driven Design）**：讓程式碼直接反映業務概念與規則，模型是設計的唯一北極星。
- DDD 有兩個層次：**戰略設計**（系統怎麼劃分）和**戰術設計**（單一脈絡內部怎麼建模）；多數人只學了戰術，忽略了更重要的戰略。
- 在 DDD 之前，主流是資料庫中心、事務腳本、貧血模型——它們在低複雜度時夠用，但無法隨業務複雜度擴展。
- DDD 不適合所有情境：對業務規則稀少的 CRUD 系統是過度設計。判斷時機需要誠實評估領域複雜度。
- 通用語言（Ubiquitous Language）、Bounded Context、Context Map 是戰略設計的核心工具；Entity、Value Object、Aggregate 是戰術設計的核心建構塊——後續章節會逐一展開。

---

## 自我檢核

- [ ] 不看書，用自己的話說出 Evans 藍皮書的核心論點（一句話版本）
- [ ] 能解釋「技術複雜性」和「領域複雜性」的差異，並各給一個例子
- [ ] 如果面試官問「什麼是模型驅動設計」，你會怎麼回答？試著說 60 秒
- [ ] 能說出 DDD 為什麼反對「一個大型系統用一個統一的全域模型」
- [ ] 能指出本章訂單範例裡，貧血模型版和 Rich Domain Model 版的具體差異在哪裡
- [ ] 能說出 DDD 戰略設計和戰術設計分別在解決什麼層次的問題

---

## 延伸閱讀

1. **Eric Evans，《Domain-Driven Design: Tackling Complexity in the Heart of Software》（2003，Addison-Wesley）**
   - URL：https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215
   - 優先讀 Part I（Putting the Domain Model to Work）和 Part IV（Strategic Design）。Part I 建立「為什麼」，Part IV 說清楚 Bounded Context 和 Context Map——這兩部分是理解 DDD 精髓最直接的路徑。
   - 與本章的關聯：本章所有核心論點都來自這本書的前幾章；讀原著能讓你感受 Evans 的推論節奏，不只是記住結論。

2. **Martin Fowler，〈Domain Driven Design〉bliki 條目**
   - URL：https://martinfowler.com/bliki/DomainDrivenDesign.html（更新日期 2020-04-22）
   - 最短的一頁式 DDD 導覽：模型中心論點、通用語言、戰略設計（Bounded Context）、戰術建構塊（Entity/VO/Service/Aggregate）。Fowler 是 Evans 的長期合作者，這個條目是最被引用的白話定義。
   - 與本章的關聯：本章 DDD 定義和分類框架與這個條目一致，適合交叉對照。

3. **Vaughn Vernon，《Domain-Driven Design Distilled》（2016，Pearson）**
   - URL：https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420
   - 最適合在讀藍皮書之前入門的短書：從零覆蓋 Bounded Context、子領域（Core/Supporting/Generic）、Context Map、Aggregate、Domain Event 和 Event Storming。是本章之後各章的實作預覽。
   - 與本章的關聯：本章介紹的兩層框架（戰略/戰術）在 Vernon 這本書裡有具體可操作的實作指引。

4. **Eric Evans，《Domain-Driven Design Reference: Definitions and Pattern Summaries》（2015，CC BY 4.0）**
   - URL：https://www.domainlanguage.com/ddd/reference/
   - Evans 本人整理的每個模式精簡定義，免費。包含 2003 年藍皮書沒有的 Domain Event 等模式。適合作為快速查閱手冊，在你對基礎有感後使用。
   - 與本章的關聯：本章提到的所有 DDD 術語，都可以在這個 Reference 找到 Evans 的原版定義。

5. **Thomas Coopman（DDD.academy），〈Accelerate your Strategic Design with LLMs〉**
   - URL：https://ddd.academy/accelerate-your-strategic-design-with-llms/
   - 一個 2024–2026 DDD 從業者的工作坊頁面：如何用 LLM 輔助草擬子領域、精煉通用語言、建立 Bounded Context Canvas——同時明確指出哪些地方 LLM 容易幻覺，哪些決策仍需人工判斷。（注意：workshop 內容屬於快速演化的實務領域，以官方最新內容為準。）
   - 與本章的關聯：本章建立的 DDD 基礎，在 AI 輔助建模的脈絡下會用到；這個資源是 Part 5 章節（Ch 33–37）的先行預習材料。

---

下一章，我們進入 DDD 最重要的單一概念——通用語言（Ubiquitous Language）。它不是命名風格指南，而是工程師和業務之間消除誤解的協定層，以及它如何成為 LLM 的詞彙表。

→ [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)
