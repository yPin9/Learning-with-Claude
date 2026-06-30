# Ch 15 — 通用語言 Ubiquitous Language

> **目標**：理解通用語言（Ubiquitous Language）是什麼、為什麼它是 DDD 最重要的實踐，以及如何在日常開發中鍛造並維護它——因為命名漂移就是模型漂移，而模型漂移就是缺陷的溫床。

---

## 在這之前，人們怎麼做

2000 年代初期，一個典型的軟體開發現場看起來像這樣：

```
業務分析師 (BA)
    ↓  寫需求文件（Word）
專案經理
    ↓  轉換成工作票
開發人員
    ↓  寫成程式碼
測試人員
    ↓  比對需求文件測試
```

每一層轉譯都在悄悄改變詞彙。

業務端說「理賠申請（Claim）」，BA 寫成「事故報告（Incident Report）」，資料庫設計師叫它 `accident_tbl`，Java 工程師把它命名成 `InsuranceForm`，測試人員的測試案例則叫「填表流程驗證」。

同一件事，五個名字。開完需求會議，回頭看程式碼，根本不知道在說哪個功能。

Eric Evans 在 2003 年的 《Domain-Driven Design: Tackling Complexity in the Heart of Software》（通稱「Blue Book」，Addison-Wesley）裡給這個問題命名，並給出解方：**通用語言（Ubiquitous Language）**。

---

## 心智圖像：語言是模型的鏡子

想像一個法庭。在法庭上，「原告」「被告」「證據」「庭期」這些詞彙對所有人——法官、律師、書記官、旁聽者——都有相同的意思，而且這些詞彙直接出現在所有文件、表單、判決書裡。法律系統之所以能夠運作，部分原因就是語言的精確性。

DDD 的通用語言做的是同一件事：

```
領域專家              開發團隊
    \                  /
     \  共同鍛造詞彙  /
      \              /
       ┌────────────┐
       │ 通用語言   │   ← 同一套詞彙
       └─────┬──────┘
             │
    ┌─────────┼─────────┐
    ↓         ↓         ↓
  對話      文件      程式碼
(會議/口語) (規格/設計) (class/method/DB)
```

如果這三個地方的詞彙對齊，那當業務說「退款被拒絕了」，你打開程式碼搜尋 `refundRejected`，就找得到。如果不對齊，你只能猜。

---

## 正式定義

Evans 的定義（轉述）：通用語言是一套由領域專家和開發人員共同建立、並被用於所有溝通和程式碼命名的詞彙體系。它的特性有三：

1. **共同建立**：不是 BA 單方面決定的，也不是工程師自己取的。是兩方坐在一起協商出來的。
2. **一致使用**：從口語、白板、設計文件到 class 名稱、method 名稱、欄位名稱，全部用同一個詞。
3. **有邊界**：通用語言是有 Bounded Context（後面 Ch 16 會深入）的——同一個詞在不同情境可能有不同定義，但在一個 context 內必須唯一且一致。

Evans 說：「通用語言的詞彙包含 class 的名字和重要操作的名字。」這句話很具體——**命名不是後期才想的，它就是建模的一部分**。

> 如果你對「為什麼 DDD 把焦點放在領域複雜性」還不熟，先回看 [Ch 14 — 為什麼 DDD：複雜性在領域，不在技術](./14-why-ddd.md)。

---

## 具體範例：電商訂單流程

### 不用通用語言的版本

假設一個電商系統，程式碼長這樣：

```python
class Transaction:
    def __init__(self, uid, items):
        self.user_id = uid
        self.line_items = items
        self.state = "new"

    def process(self):
        # 確認付款
        self.state = "processed"

    def cancel_transaction(self):
        self.state = "cancelled"
        self._create_reverse_entry()

    def _create_reverse_entry(self):
        # 退款邏輯
        ...
```

業務人員說「訂單（Order）」，工程師說「Transaction」；業務說「取消訂單」，程式碼叫 `cancel_transaction`；業務說「退款」，程式碼叫 `_create_reverse_entry`。

開個除錯會議，業務說「OrderID 12345 的訂單已確認付款，怎麼顯示還沒出貨？」，工程師花了三分鐘才搞清楚「確認付款」對應的是 `state = "processed"`。這三分鐘每天乘以幾十次對話，就是長期的認知損耗。

### 用通用語言的版本

先跟業務人員一起把詞彙表建出來（下面這個是過程的產物，不是先有表才動手）：

| 業務說 | 通用語言（確定的詞） | 程式碼 |
|--------|---------------------|--------|
| 訂單 | Order | `Order` class |
| 確認付款 | PaymentConfirmed | `order.confirm_payment()` 或 Domain Event `PaymentConfirmed` |
| 取消訂單 | CancelOrder | `order.cancel()` |
| 退款 | Refund | `Refund` Value Object 或 `RefundIssued` Event |
| 訂單品項 | OrderLine | `OrderLine` class |

然後程式碼：

```python
class Order:
    def __init__(self, order_id: OrderId, customer_id: CustomerId):
        self.order_id = order_id
        self.customer_id = customer_id
        self._lines: list[OrderLine] = []
        self._status = OrderStatus.PENDING

    def add_line(self, product_id: ProductId, quantity: Quantity, unit_price: Money) -> None:
        if self._status != OrderStatus.PENDING:
            raise OrderNotModifiableError(self.order_id)
        self._lines.append(OrderLine(product_id, quantity, unit_price))

    def confirm_payment(self) -> list[DomainEvent]:
        if self._status != OrderStatus.PENDING:
            raise InvalidOrderTransitionError(self._status, "confirm_payment")
        self._status = OrderStatus.PAYMENT_CONFIRMED
        return [PaymentConfirmed(order_id=self.order_id)]

    def cancel(self) -> list[DomainEvent]:
        if self._status == OrderStatus.SHIPPED:
            raise OrderAlreadyShippedError(self.order_id)
        self._status = OrderStatus.CANCELLED
        return [OrderCancelled(order_id=self.order_id)]

    def total(self) -> Money:
        return sum((line.subtotal() for line in self._lines), Money.ZERO)
```

現在業務說「OrderID 12345 的訂單已確認付款」，工程師搜尋 `confirm_payment` 或 `PaymentConfirmed`，立刻找到。沒有翻譯層，沒有認知負荷。

這段程式碼可以執行（需要配合相應的 Value Object 定義），輸出符合預期的行為：

```python
# 基本使用範例
order = Order(OrderId("ORD-1001"), CustomerId("CUST-99"))
order.add_line(ProductId("P-555"), Quantity(2), Money(150, "TWD"))
events = order.confirm_payment()
print(events)  # [PaymentConfirmed(order_id=OrderId("ORD-1001"))]
print(order.total())  # Money(300, "TWD")
```

---

## 如何實際建立通用語言

通用語言不是一次性的產物。它是一個**持續演化的協作過程**。以下是一個可操作的流程：

### 步驟一：詞彙發掘會議

帶著白板或便利貼，和領域專家坐在一起，問：

- 「你們在描述這個流程時，用什麼詞？」
- 「這個詞和那個詞有什麼不同？」
- 「什麼情況下會用 X 而不是 Y？」

把聽到的詞都寫下來，不要過濾。這個會議的目的是**聆聽**，不是「解釋技術」。

### 步驟二：詞彙澄清與統一

你會發現業務人員自己也會用不同的詞指同一件事（例如「顧客」vs「客戶」vs「用戶」）。這時候的工作是：

1. 挑一個詞，問業務「如果只能用一個詞，是哪個？」
2. 記錄下來，告訴所有人「從現在起我們說 X」
3. 把決定寫進**詞彙表（Glossary）**

詞彙表不需要是大型 Wiki，一個 `GLOSSARY.md` 放在 repo 根目錄就夠了。

### 步驟三：把詞彙寫進程式碼

這是最關鍵的一步，也是最常被跳過的。

每次新增功能：
- Class 名稱用詞彙表的詞
- Method 名稱用詞彙表的動詞
- 欄位名稱用詞彙表的名詞
- 例外（Exception）的名稱也用詞彙表的詞

**避免技術詞彙侵入**。`UserManager`、`DataProcessor`、`ServiceHandler` 這類名字在詞彙表裡找不到，就是警訊。

### 步驟四：持續演化

業務理解會深化，語言會改變。重要的是：**語言變了，程式碼也要跟著改**。

如果業務把「確認訂單（Confirm Order）」改名叫「批核訂單（Approve Order）」，這不只是文件更新——`confirm_payment()` 也應該重新命名成 `approve()`。

這不是額外工作，這是**維護領域模型與程式碼一致性的工作**，本來就該做。

---

## 底層機制：為什麼命名漂移是模型漂移

命名不只是可讀性問題。命名的背後是**概念的邊界**。

當業務說「Claim（理賠申請）」，他們的腦子裡有一套規則：一個 Claim 有理賠人、理賠日期、理賠金額、附件；Claim 可以被受理、拒絕、補件要求。

當工程師把它叫做 `InsuranceForm`，他們的腦子裡開始用不同的框架去思考：表單有欄位、有驗證規則、有送出動作。

這兩個框架在很多地方會衝突——某些業務規則在「Claim」框架下顯而易見，在「InsuranceForm」框架下就需要額外解釋。

**命名錯了，模型就建錯了**。錯的模型會產生錯的邊界，錯的邊界會讓不相干的邏輯耦合在一起，讓相干的邏輯散落四處。最終的結果不是「程式碼難讀」，而是「需求變更的時候不知道要改哪裡」。

這是 Evans 的核心洞察：語言和模型不是兩件事。語言就是模型的外在呈現。

---

## 對比取捨

| 做法 | 優點 | 代價 | 適用場景 |
|------|------|------|----------|
| **通用語言（Ubiquitous Language）** | 零翻譯層；需求直達程式碼；新人靠詞彙表就能理解業務 | 前期需要和業務密集協作；重構成本（改名要改很多地方） | 複雜領域；長期維護的系統 |
| **技術命名慣例**（`Manager`、`Service`、`Handler`）| 工程師之間溝通快；有現成模式 | 業務人員看不懂；系統逐漸脫離業務現實 | 簡單 CRUD；純技術工具 |
| **資料庫驅動命名**（`user_tbl`、`trans_log`）| 資料庫設計師熟悉 | 最遠離業務語言；換 DB 還得改名稱含義 | 資料管理工具；BI 系統（有自己的語言） |
| **先 API 再命名**（REST 端點命名優先）| 前後端溝通快 | API 命名通常更技術化；領域概念消失 | 微服務 API 層（但內部仍應用通用語言） |

通用語言的代價主要是**前期投資**。對於生命週期短的系統（例如一次性腳本、探索性 POC），這個投資不值得。但對於任何需要維護超過六個月、涉及多個業務概念的系統，跳過通用語言通常會在之後付出更高的成本。

---

## 踩雷集錦

### 錯誤直覺 1：「通用語言就是把中文翻成英文」

**錯誤直覺**：我們中文討論業務，程式碼用英文，所以翻譯是難免的。通用語言只是「翻譯表」。

**正確認識**：通用語言的重點不在語言（中/英），而在**概念的統一**。如果業務說「訂單取消」，程式碼叫 `cancelOrder`，這是對齊的。如果程式碼叫 `deleteTransaction`，這就是漂移。翻譯本身不是問題，概念錯位才是問題。有些團隊直接用中文命名（Python 支援 Unicode 識別碼，Go 也支援），那也完全可以——只要和業務人員的詞彙一致。

---

### 錯誤直覺 2：「詞彙表定了就不會改，不用花太多時間」

**錯誤直覺**：我們在專案初期開一次詞彙會議，建一個詞彙表，之後照著跑就好了。

**正確認識**：詞彙表是活文件。業務理解深化之後，會發現之前的詞彙太模糊或有歧義。Evans 把這個過程叫做「提煉（Distillation）」——隨著你更理解領域，語言會變得更精確。更精確的語言反映更精確的模型。**拒絕更新詞彙表就是拒絕學習**，最終的代價是模型和現實漸行漸遠。

---

### 錯誤直覺 3：「我的業務邏輯很複雜，先讓程式跑起來再說，後面再來統一命名」

**錯誤直覺**：命名是可以之後 refactor 的，先解決功能問題更重要。

**正確認識**：問題在於「後面」通常不會來。而且，命名混亂不只影響可讀性——它影響你**思考問題的方式**。當你把 `Refund` 叫成 `ReverseEntry`，你會開始用「帳務分錄」的框架去思考這個功能，而不是「退款」的框架。兩個框架對「需不需要通知顧客」「需不需要審核」「可不可以部分退款」可能有完全不同的答案。命名早一步對，後面少付十倍的代價。

---

### 錯誤直覺 4：「整個系統用一套通用語言就夠了」

**錯誤直覺**：通用語言是全局的，整個公司只要建一個詞彙表。

**正確認識**：通用語言是**有邊界的**。在電商系統裡，「客戶（Customer）」在銷售情境（Sales Context）是「有購買意願的潛在買家」，在客服情境（Support Context）是「已購買並可能有問題需要解決的人」。兩個情境的 Customer 要處理的屬性和行為差異很大，強行統一反而會造成模型混亂。這就是為什麼 Bounded Context 必須和通用語言一起討論——每個 Bounded Context 有自己的通用語言方言。

> 關於 Bounded Context 的詳細討論，見下一章 [Ch 16 — Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

---

### 錯誤直覺 5：「讓工程師自己決定命名，業務不懂技術」

**錯誤直覺**：業務人員不懂物件導向，不應該干涉 class 的命名。

**正確認識**：業務人員不需要懂物件導向，但他們是**領域知識的來源**。你不需要把 class 的結構拿給他們看，但你需要確認命名。問：「我們把這個功能叫做 X，你們業務上怎麼說？」就夠了。這個對話通常不超過五分鐘，但能省下日後幾個小時的溝通誤差。

---

## 進階延伸

### 命名模式：動詞驅動設計

Method 的命名可以直接反映業務動詞。業務流程是一系列的動作，這些動作應該在程式碼裡找得到。

```python
# 業務說：顧客提交退款申請，客服人員審核，財務部核發退款
# 程式碼：
class RefundApplication:
    def submit(self, reason: RefundReason) -> DomainEvent: ...
    def approve(self, reviewer: AgentId) -> DomainEvent: ...
    def reject(self, reason: RejectionReason) -> DomainEvent: ...
    def issue_refund(self, amount: Money) -> DomainEvent: ...
```

每個 method 名都是從業務對話直接借來的。當業務說「這張申請被駁回了」，你搜 `reject`，找到了。

### 失敗邊界例子：詞彙表太大導致沒人維護

一個反例：某個金融系統的詞彙表長達 400 條，放在 Confluence 上，每次更新需要三個人審批。結果是：沒人維護，詞彙表和程式碼漸漸脫節，最後只是一份歷史文件。

通用語言要有效，詞彙表**必須夠小、夠聚焦**。每個 Bounded Context 的詞彙表應該只包含那個 context 裡重要的術語，通常 30–60 條就足夠了。寧可少而精確，不要多而混亂。

### 通用語言與 AI 工具

這個面向在課程後半會深入討論（見 [Ch 34 — 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md)），這裡先點出核心概念：

當你把需求或規格交給 LLM 生成程式碼，LLM 使用的是它訓練資料裡的統計語言模式。如果你的規格使用「Transaction」，LLM 可能生成帶有金融交易語意的程式碼。如果你用「Order」，生成的程式碼就會往電商方向走。

**精確的通用語言就是給 LLM 的精確詞彙表**。詞彙不準，即使是最好的 AI 也會在概念層面出錯——而這種錯誤很難在程式碼審查時發現。

---

## 動手練習

### 練習一：詞彙考古

找一個你熟悉的現有系統（或者你正在開發的系統），列出以下兩個清單：

1. **業務人員/需求文件裡用的詞**（從會議記錄、Slack 訊息、需求票找）
2. **程式碼裡用的詞**（class 名、method 名、DB 欄位名）

比較兩個清單，找出漂移點（程式碼用的詞在業務語言裡找不到，或者業務詞在程式碼裡找不到）。

算出**漂移比率** = 漂移的詞數 / 總詞數，這是你系統的「語言對齊程度」粗略指標。

### 練習二：重命名一個模組

挑一個漂移最嚴重的 class 或 module，做以下步驟：

1. 訪談業務人員（或者假設訪談——如果你是獨立開發者，寫下你預期業務會怎麼說）
2. 確定通用語言中的正確名字
3. 重新命名 class、method、相關測試
4. 確認所有地方的命名統一

記錄：重命名之後，有沒有在任何地方發現之前沒注意到的邏輯錯誤？（這種情況比你想的更常見——錯誤的命名往往掩蓋了錯誤的邏輯。）

### 練習三：建立一個 GLOSSARY.md

為你的（或假設的）電商系統建立一個詞彙表，包含以下欄位：

```markdown
# 詞彙表 GLOSSARY

## Order（訂單）
定義：顧客提交購買意圖後產生的業務實體，包含一或多個 OrderLine。
狀態流：PENDING → PAYMENT_CONFIRMED → SHIPPED → DELIVERED / CANCELLED
區別於：Cart（購物車，Order 確認前的暫存狀態）

## OrderLine（訂單品項）
定義：Order 內一筆具體的商品項目，含 ProductId、Quantity、UnitPrice。
...
```

試著涵蓋至少 8 個詞彙，為每個詞彙寫「區別於」欄位——這一欄最能逼你把模糊的邊界說清楚。

---

## 本章重點整理

- **通用語言**是 Eric Evans 在 2003 年《Blue Book》提出的 DDD 核心實踐：一套由領域專家和開發人員共同建立、貫穿對話與程式碼的詞彙體系。
- 通用語言的作用是消除轉譯層：從業務需求到 class 命名，詞彙一致，模型就不會在層層轉手中走形。
- **命名漂移就是模型漂移**：當程式碼的詞彙和業務語言脫節，你的模型已經在描述一個和現實不同的世界。
- 建立通用語言的流程：詞彙發掘 → 統一與選定 → 寫進程式碼 → 持續演化。
- 通用語言有邊界——它屬於特定的 Bounded Context，不同 context 的同一個詞可以有不同定義。
- 對 LLM 輔助開發而言，精確的通用語言就是精確的詞彙表，是讓 AI 生成正確程式碼的前提條件之一。

---

## 自我檢核

用自己的話（不查本章）回答以下問題。面試時被問到，你會怎麼說？

- [ ] 不用「DDD」這個詞，向一個業務人員解釋通用語言是什麼、為什麼重要
- [ ] 描述一個「命名漂移造成 bug」的具體場景——要能說清楚為什麼錯誤命名讓 bug 更難發現
- [ ] 解釋通用語言和資料庫欄位命名的關係：如果業務說「理賠申請」，DB 欄位是不是一定要叫 `claim`？
- [ ] 通用語言的詞彙表應該多大？維護它的成本和收益如何權衡？
- [ ] 為什麼說「整個公司只能有一套通用語言」是錯的？
- [ ] 如果你今天接手一個遺留系統，如何評估它的語言對齊程度？第一步你會做什麼？

---

## 延伸閱讀

**Eric Evans — Domain-Driven Design: Tackling Complexity in the Heart of Software**（Blue Book，2003，Addison-Wesley）
- 連結：https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215
- 讀什麼：Part I（Putting the Domain Model to Work）的前三章——Evans 在這裡詳細論述通用語言的必要性、建立方式、和模型的關係。這是本章所有概念的原始出處。
- 和本章的關聯：本章所有核心概念均來自此書。

**Eric Evans — DDD Reference: Definitions and Pattern Summaries**（免費，2015 年版，CC BY 4.0）
- 連結：https://www.domainlanguage.com/ddd/reference/
- 讀什麼：Ubiquitous Language 和 Model-Driven Design 兩個條目的精簡定義。Evans 自己的一句話版本。
- 和本章的關聯：最快速的一手查閱來源；也是本章引用定義的出處。

**Martin Fowler — bliki: Domain Driven Design**
- 連結：https://martinfowler.com/bliki/DomainDrivenDesign.html
- 讀什麼：全文（很短）。Fowler 把 Evans 的貢獻濃縮成一頁，特別強調「developing a vocabulary」這個洞察。
- 和本章的關聯：如果 Blue Book 太厚，這是最好的第一步；Fowler 也是 Evans 長期的合作者，有額外的詮釋視角。

**Vaughn Vernon — Domain-Driven Design Distilled**（2016，Addison-Wesley）
- 連結：https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420
- 讀什麼：第二章（Strategic Design with Bounded Contexts and the Ubiquitous Language）。Vernon 用更現代的筆觸解釋通用語言和 Bounded Context 的關係，配有具體例子。
- 和本章的關聯：本書是 Blue Book 之後最好的入門讀物；第二章直接對應本章到 Ch 16 的內容。

**Thomas Coopman — Accelerate your Strategic Design with LLMs**（DDD.academy，2026）
- 連結：https://ddd.academy/accelerate-your-strategic-design-with-llms/
- 讀什麼：議程條目中關於「Refine Ubiquitous Language with LLMs」的部分，以及對應的注意事項（hallucination risk、人工判斷的不可取代性）。
- 和本章的關聯：本章末尾提到 LLM 與通用語言的關係，這份資料是 2026 年 DDD 社群對此議題的實踐回應；version-dependent，以官方最新資料為準。

---

下一章我們要問一個更深的問題：當「顧客」在銷售和客服的意思不同，你要怎麼辦？通用語言不可能真的「通用」——Bounded Context 才是讓矛盾消失的工具。

→ [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)
