# Ch 17 — Context Mapping 與整合模式

> **目標**：能畫出 Bounded Context 之間的依賴地圖，選對整合模式——Partnership、Shared Kernel、Customer/Supplier、Conformist、Anticorruption Layer、Open-Host Service/Published Language、Separate Ways、Big Ball of Mud——並解釋每個模式為什麼存在、代價是什麼。

> 如果你對 Bounded Context 還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

---

## 在我們有 Context Map 之前

2003 年以前，大部分團隊面對「兩套系統要溝通」的問題，用的是兩招：

1. **共用資料庫**：兩邊都寫同一張表，靠 schema 當合約。結果是誰都不能改 schema，資料庫變成耦合的磁鐵。
2. **直接 API 呼叫，字典對齊靠口頭約定**：A 團隊的 `customerId` 就是 B 團隊的 `clientId`，靠記憶和 wiki 維持一致性。

這兩招的共同問題：**整合關係是隱形的**。你不知道哪個團隊對哪個團隊有多強的依賴，不知道誰是上游、誰是下游，也不知道一旦 A 改了某個欄位，B 會不會爆炸。

Eric Evans 在《Domain-Driven Design》（2003，Addison-Wesley）的第四部份「Strategic Design」引入了 **Context Map（情境地圖）**：把每個 Bounded Context 畫成一個節點，把它們之間的關係用九種有名字的模式標注出來，讓整合策略從口頭協議變成可以討論、可以稽核、可以版控的明文設計決策。

---

## 一張圖先有直覺

以一個電商平台為例，有四個 Bounded Context：

```
                ┌──────────────────────────────────────────────────┐
                │              Context Map：電商平台              │
                └──────────────────────────────────────────────────┘

  ┌──────────────┐  Customer/Supplier   ┌───────────────┐
  │   Catalog    │ ──────────────────▶  │   Ordering    │
  │ (上游/U)     │                      │  (下游/D)     │
  └──────────────┘                      └───────────────┘
                                               │
                                          ACL  │  (隔離第三方物流)
                                               ▼
                                        ┌─────────────┐
                                        │  Shipping   │
                                        │  (外部系統) │
                                        └─────────────┘

  ┌───────────────┐  Conformist  ┌─────────────────────┐
  │   Payment     │ ────────────▶│ 金流閘道 (Stripe)   │
  │  (下游/D)     │              │ OHS + Published Lang │
  └───────────────┘              └─────────────────────┘
```

箭頭方向是**影響流向**（上游影響下游）。每條邊上的標籤就是整合模式。接下來我們逐一拆開。

---

## 九種整合模式

Evans 定義九個模式，ddd-crew 維護的 [ddd-crew/context-mapping](https://github.com/ddd-crew/context-mapping) 是目前最完整的社群整理，含決策樹。以下按「關係緊密 → 完全隔離」排序。

### 1. Partnership（夥伴關係）

兩個上下游 context 的**成功相互依存**，任何一方的失敗都傷害兩方。兩個團隊同步規劃介面，有任何不相容的改變就一起協調。

**適用時機**：同一公司兩個緊密合作的團隊，共同交付一個功能。  
**代價**：高協調成本。一旦其中一個團隊節奏不同（外包、組織調整），Partnership 立刻變脆弱。  
**不選的理由**：如果兩邊有任何組織壁壘（不同部門、外包廠商），溝通成本會讓 Partnership 變成一場持續的救火行動。

---

### 2. Shared Kernel（共享核心）

兩個 context 明確同意**共享一小部分模型**（程式碼、資料庫 schema 的子集），這部分的改動需要雙方協商並一起測試。

```python
# shared_kernel/money.py  ← 兩個 context 共用這個套件
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str  # ISO 4217

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"幣別不同：{self.currency} vs {other.currency}")
        return Money(self.amount + other.amount, self.currency)
```

**適用時機**：兩個 context 需要共用一個值物件（Value Object）或列舉，複製貼上會導致語意漂移。  
**代價**：共享核心是**雙方必須共同治理的模組**。任何修改都需要兩邊的測試套件都通過。核心一大，就變成共用資料庫的翻版。  
**不選的理由**：如果組織上不能保持緊密的技術治理（例如外包供應商、不同公司），不要用 Shared Kernel。

> 嚴禁把 Shared Kernel 當成「懶得想清楚邊界就先共用」的借口。Evans 說得直接：Shared Kernel **很特殊，必須很小**。

---

### 3. Customer/Supplier Development（客戶/供應商）

上游（Supplier）提供功能，下游（Customer）有**明確的影響力**：下游可以提需求，上游有義務在規劃中優先考慮下游的需求，但最終決定權在上游。

```
Catalog (U/Supplier) ──────────────────▶ Ordering (D/Customer)
    │                                         │
    │  Ordering 可以開 ticket 要求 Catalog    │
    │  暴露新的欄位（e.g. inventory count）   │
    │  Catalog 承諾在 sprint N 前交付         │
    └─────────────────────────────────────────┘
```

**適用時機**：同一個組織內，上游團隊有動機服務下游（因為整個系統的成功取決於兩者）。  
**代價**：需要正式的協商機制（ticket、roadmap 對齊）；如果上游團隊的 KPI 不包含「服務下游」，這個模式就名存實亡。  
**不選的理由**：跨公司邊界、或上游根本沒有動機配合下游，就不應該假裝有 Customer/Supplier 關係——那其實是 Conformist。

---

### 4. Conformist（順從者）

上游根本不關心下游的需求（或沒有能力配合）。下游**決定直接採用上游的模型**，不翻譯、不轉換。

**典型案例**：接入第三方 SaaS 的 API 且對方不提供客製化。

```python
# payment/stripe_client.py
# 我們直接用 Stripe 的資料結構，不做任何包裝
import stripe

def charge(amount_cents: int, currency: str, payment_method: str) -> dict:
    return stripe.PaymentIntent.create(
        amount=amount_cents,
        currency=currency,
        payment_method=payment_method,
        confirm=True,
    )
# 回傳的 dict 是 Stripe 的結構，PaymentIntent object
# 我們的 Payment context 程式碼直接用 Stripe 的欄位名稱
```

**適用時機**：對方是業界標準（Stripe、AWS S3），或上游的模型已經很好地對應你的問題。  
**代價**：你的 domain model 直接暴露在外部模型的每一次變化下。上游改了欄位名，你就改；上游廢棄了 API，你就坑。  
**何時用 ACL 而不是 Conformist**：如果外部模型的術語和概念**會汙染你的 Ubiquitous Language**，就改用 ACL。

---

### 5. Anticorruption Layer（防腐層，ACL）

下游自行建立一個**翻譯層**，把外部（上游）的模型轉換成自己的 domain model，讓自己的 context 完全不知道外部的存在。

這是 Context Map 裡**最強大的防禦武器**。

```python
# shipping/acl/logistics_translator.py
# 外部物流 API 的語言（有歷史包袱的欄位命名）
class ExternalShipmentDTO:
    ship_to_addr_line1: str
    ship_to_addr_line2: str
    pkg_weight_lbs: float
    est_dlv_date: str          # "MM/DD/YYYY" 格式
    carrier_code: str          # "FX" = FedEx, "UP" = UPS

# 我們自己的 Shipping context domain model
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class DeliveryAddress:
    street: str
    city: str
    postal_code: str
    country: str

@dataclass
class Shipment:
    address: DeliveryAddress
    weight_kg: float
    estimated_delivery: date
    carrier: str  # "FedEx" | "UPS" 等可讀名稱

# ACL：把外部 DTO 翻成我們的 Shipment
def translate(dto: ExternalShipmentDTO) -> Shipment:
    weight_kg = dto.pkg_weight_lbs * 0.453592
    delivery = date.strptime(dto.est_dlv_date, "%m/%d/%Y")
    carrier_map = {"FX": "FedEx", "UP": "UPS"}
    return Shipment(
        address=DeliveryAddress(
            street=f"{dto.ship_to_addr_line1} {dto.ship_to_addr_line2}".strip(),
            city="",        # 外部 DTO 沒給，需要另外處理
            postal_code="", # 同上
            country="",
        ),
        weight_kg=weight_kg,
        estimated_delivery=delivery,
        carrier=carrier_map.get(dto.carrier_code, dto.carrier_code),
    )
```

**你的 context 裡任何地方都不應該出現 `pkg_weight_lbs` 或 `"FX"`**——那些是外部語言，ACL 是語言的邊界。

**適用時機**：
- 外部模型的概念與你的 domain model 有**語義不匹配**（同一個詞不同意思）
- 遺留系統（Legacy System）有混亂的資料結構
- 外部模型的概念會**汙染**你的 Ubiquitous Language

**代價**：需要維護翻譯層的程式碼；外部介面有多複雜，翻譯層就有多複雜。  
**不選的理由**：如果外部模型本身就和你的 domain 概念高度對齊，用 Conformist 更省力。

---

### 6. Open-Host Service（開放主機服務，OHS）+ Published Language（發布語言）

上游定義一個**正式的、穩定的協定（Protocol）**，讓多個下游可以接入，不需要為每個下游客製化。Published Language 是這個協定的正式規格（如 JSON Schema、Protobuf、OpenAPI 文件）。

這兩個模式幾乎總是一起出現。

```yaml
# catalog/openapi.yaml（Published Language）
openapi: "3.1.0"
info:
  title: Catalog API
  version: "2.1.0"
paths:
  /products/{id}:
    get:
      summary: 取得商品
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Product"
components:
  schemas:
    Product:
      type: object
      required: [id, name, price]
      properties:
        id:
          type: string
          format: uuid
        name:
          type: string
        price:
          type: object
          required: [amount, currency]
          properties:
            amount:
              type: number
              format: decimal
            currency:
              type: string
              description: "ISO 4217 幣別代碼"
```

**適用時機**：一個上游要服務多個下游，不可能為每個下游客製介面。  
**代價**：Published Language 的**向後相容性**成為硬性約束——一旦發布，破壞性修改要走版號或廢棄流程。這是有意義的代價：它讓上游「對自己的 API 負責」。  
**OHS 和 Customer/Supplier 的差別**：Customer/Supplier 是一對一且上游可以配合下游需求；OHS 是一對多且上游定義協定、下游遵從。

---

### 7. Separate Ways（各走各路）

兩個 context **決定不整合**。各自在自己的問題範圍內解決問題，哪怕這意味著有一些功能被重複實作。

**適用時機**：兩個 context 之間的整合成本高於各自實作相似功能的成本。  
**典型案例**：訂單系統和 HR 系統都需要「計算工作日」，但把它們整合起來的成本遠大於各自實作一個簡單的工作日計算。  
**不選的理由**：如果兩邊共用的邏輯是核心業務規則（如定價邏輯），Separate Ways 會導致語意漂移——兩邊各自演化，慢慢出現「為什麼你算的跟我算的不一樣？」

---

### 8. Big Ball of Mud（泥球）

這不是一個你**選擇**的模式，而是**描述現實**的模式。很多遺留系統的某些區域根本沒有清晰的邊界，到處是全域狀態和意義不明的耦合。

Evans 建議的做法：**在 Big Ball of Mud 的周圍畫一個邊界**，不要試圖在裡面整理，而是用 ACL 把它封起來，讓新系統和它隔離。

---

### 九種模式對比表

| 模式 | 上游主動性 | 下游影響力 | 整合緊密度 | 典型使用者 |
|------|-----------|-----------|-----------|-----------|
| Partnership | 雙向 | 高 | 最高 | 同一團隊的兩個 context |
| Shared Kernel | 雙向 | 高 | 高 | 同公司緊密合作的團隊 |
| Customer/Supplier | 上游 | 中 | 中高 | 同組織，上游有服務動機 |
| Conformist | 上游，不在意下游 | 無 | 中 | 接入第三方 SaaS |
| ACL | 上游 | 無（但自我保護） | 低（有屏蔽層） | 整合遺留系統、不良外部 API |
| OHS + Published Language | 上游定義協定 | 弱（循協定） | 低 | 公開 API 平台 |
| Separate Ways | — | — | 無 | 整合成本 > 複製成本 |
| Big Ball of Mud | 無 | 無 | 混亂 | 遺留系統現實描述 |

---

## 方向與上下游（Upstream / Downstream）

Context Map 的每條邊都有**方向**：上游（U）影響下游（D）的模型，反過來不成立。

上游不一定是「技術依賴方向」，而是**誰的決策影響誰的模型**。Payment context 呼叫 Stripe API——技術上是 Payment 發起呼叫——但 Stripe 是上游，因為 Stripe 的模型決定了 Payment 怎麼寫程式碼（Conformist 或 ACL 的選擇因此很重要）。

---

## 踩雷集錦

### 雷 1：把 ACL 當成萬能解
**錯誤直覺**：「反正都加一層 ACL，永遠安全。」  
**正確認識**：ACL 需要維護翻譯邏輯。如果外部模型本身就和你的 domain model 高度對齊（Conformist 適合的情境），加 ACL 只是增加了無意義的程式碼量。評估點在於：外部概念進入你的 context **後會不會帶來語義汙染**？

### 雷 2：把 Shared Kernel 當「共用函式庫」
**錯誤直覺**：「我們把所有共用的程式碼都放進 shared_kernel，很方便。」  
**正確認識**：Shared Kernel 的邊界必須非常小，且必須有雙方共同治理的機制（共同的測試、雙方都要 review 的 PR）。一旦 shared_kernel 開始放大量業務邏輯，它就變成了一個跨 context 共享的大型單體，破壞了 Bounded Context 的隔離性。

### 雷 3：把 Customer/Supplier 誤認為上游「有義務立刻回應」
**錯誤直覺**：「我是下游客戶，上游就應該照我說的改。」  
**正確認識**：Customer/Supplier 是說下游有**需求提出的管道**和**合理的優先權影響力**，不是說上游必須立刻配合。如果發現上游完全忽視下游需求，這個關係實際上是 Conformist——應該改變整合策略（用 ACL 保護自己）而不是持續誤判關係性質。

### 雷 4：不在 Context Map 上標注方向
**錯誤直覺**：「畫個線連起來就好，模式名稱在上面看得懂。」  
**正確認識**：方向（U/D）決定了誰是決策者。Catalog → Ordering 和 Ordering → Catalog 的整合策略完全不同。沒有方向的 Context Map 是不完整的地圖，會讓討論陷入「到底誰要配合誰？」的無意義爭論。

### 雷 5：Big Ball of Mud 是「等等再處理」
**錯誤直覺**：「那塊舊系統很亂，之後再整理吧，先連線就好。」  
**正確認識**：一旦你的新系統直接和 Big Ball of Mud 深度整合（共用表、直接呼叫內部函式），你的新系統也開始被汙染。正確做法是**現在就畫邊界**，用 ACL 封住它，即使 ACL 裡面先很陽春也無妨。

---

## 如何畫一張 Context Map

以下是一個最小可行的工作流：

1. **列出所有 Bounded Context 的名字**——從 [Ch 16 的邊界識別](./16-bounded-context.md) 繼承。
2. **識別整合點**——哪些 context 之間有資料或事件的流動？
3. **為每條邊判斷方向（U/D）**——問「誰的模型決定了另一邊怎麼寫程式碼？」
4. **選擇模式**——對照上方表格和問題：組織關係是什麼？外部還是內部？上游有多配合？
5. **用工具或手繪輸出**——可以用 PlantUML、draw.io、Miro，或就用紙。
6. **把 Context Map 放進版控**——這是架構決策，應該和程式碼一起演化。

ddd-crew/context-mapping 提供了一個決策樹，建議在選擇模式時跑一遍：從「是否是外部系統？」開始，到「上游是否有配合動機？」，最終收斂到一個模式。

---

## 進階延伸

### Context Map 與微服務架構

在微服務架構中，每個服務通常對應一個 Bounded Context，Context Map 的模式直接對應到服務間通訊的設計：

- **OHS + Published Language** → 事件匯流排（Kafka/SNS）上的事件 schema + OpenAPI 規格
- **ACL** → API Gateway 後面的 Anti-Corruption Layer 微服務
- **Partnership** → 兩個服務共享一個 Git monorepo 且有強制整合測試

### Context Map 與 AI Agent 的邊界設計

> 如果你後面會讀 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)，會看到 Context Map 的模式直接對應到多 Agent 系統的通訊合約。

一個 ACL 在 AI 流水線中可能長得像：

```python
# 把 LLM 輸出（外部模型的「語言」）翻譯成 domain model
class LLMOutputACL:
    def translate_order_extraction(self, raw: dict) -> Order:
        # LLM 可能回傳 {"product_name": "...", "qty": 2}
        # 我們的 domain 要的是 Order(lines=[OrderLine(ProductId(...), Quantity(2))])
        ...
```

LLM 的輸出格式會變、版本會變——ACL 讓 domain model 不受這些變化汙染。

### Context Map 的動態版本

組織成長時，Context Map 應該一起演化。常見演化路徑：

- **Big Ball of Mud → ACL 封裝 → 逐步 Strangler Fig**：先用 ACL 把舊系統包起來，再把它的功能逐塊搬進新的 Bounded Context。
- **Conformist → ACL**：當外部 API 的概念越來越和自己的 domain 衝突，值得花力氣加 ACL。
- **Customer/Supplier → OHS**：當下游從一個變成多個，上游應該把介面正式化成 OHS + Published Language。

---

## 動手練習

拿以下場景畫出一張 Context Map：

**場景**：線上訂餐平台  
- `Menu` context：管理餐廳菜單和定價  
- `Ordering` context：接受顧客下單  
- `Payment` context：呼叫 Stripe API 收款  
- `Delivery` context：呼叫外部物流 API 派送  
- `Notification` context：發 Email/SMS 通知

**練習步驟**：
1. 在每條整合線上標出方向（U/D）
2. 為每條線選擇一個整合模式，寫出理由（一句話）
3. 找出至少一個「如果改成另一個模式，代價是什麼？」的比較

**一個可能的部分答案**（不是唯一正解）：

```
Menu (U) ──Customer/Supplier──▶ Ordering (D)
    理由：Menu 有動機讓 Ordering 能取到正確定價，同一公司內協商可行

Ordering (U) ──OHS/Published Language──▶ Notification (D)
    理由：未來可能有多個訂閱通知的 context（行銷、Analytics），OHS 讓 Ordering 不需為每個 context 客製

Payment (D) ──Conformist──▶ Stripe (U, external)
    理由：Stripe 的模型已足夠好，且我們完全沒有影響力；若 Stripe 的術語和我們 domain 衝突再改 ACL

Delivery (D) ──ACL──▶ 外部物流 API (U, external)
    理由：物流 API 有歷史包袱的欄位命名，不能讓它汙染 Ordering 的 Ubiquitous Language
```

---

## 本章重點整理

- Context Map 把 Bounded Context 之間的整合關係**從口頭約定變成明文設計決策**，應納入版控。
- 九種模式的核心維度是：**組織關係**（上下游的影響力平衡）和**外部/內部**（是否跨公司邊界）。
- ACL 是下游保護自己 domain model 的主要武器；OHS + Published Language 是上游服務多個下游的主要工具。
- 方向（U/D）不是技術呼叫方向，而是「誰的決策影響誰的模型」。
- Big Ball of Mud 不是選擇，是現實描述——正確回應是用 ACL 封住它。
- Context Map 會隨組織成長而演化；畫出來的那一刻是快照，不是永久真相。

---

## 自我檢核

- [ ] 不看筆記，用自己的話說出 ACL 和 Conformist 的差別，以及分別在什麼情況下選哪個。
- [ ] 如果面試官問「你們系統的 Context Map 長什麼樣？」，你能在 5 分鐘內白板畫出一張合理的圖嗎？
- [ ] 解釋為什麼 Shared Kernel 必須很小。
- [ ] Partnership 和 Customer/Supplier 的組織前提各是什麼？當前提不成立時，應該換成什麼？
- [ ] OHS 中的 Published Language 為什麼會讓向後相容性成為硬性約束？這是 bug 還是 feature？
- [ ] Separate Ways 看起來像「承認失敗」，但在什麼條件下它其實是最理性的決定？

---

## 延伸閱讀

**ddd-crew/context-mapping（GitHub）**  
https://github.com/ddd-crew/context-mapping  
最完整的社群整理，含九種模式的定義、圖例、和一個選模式的決策樹。從 README 的圖開始讀，再看各模式的描述。本章的模式定義以此為基礎。

**Domain-Driven Design: Tackling Complexity in the Heart of Software（Blue Book）— Eric Evans**  
https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215  
出版：Addison-Wesley, 2003（ISBN 0321125215）。Part IV「Strategic Design」是 Context Map 和整合模式的原始來源。Evans 對 ACL 的論述（特別是為何要防止外部模型「腐蝕」內部語言）比任何二手資料都更有說服力。

**Domain-Driven Design Distilled — Vaughn Vernon**  
https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420  
出版：Addison-Wesley, 2016。第 4 章專門講 Context Map 和整合策略，是進入 Evans Blue Book 之前最平易近人的起點。Vernon 用電商等現實案例說明每個模式的取捨。

**Domain-Driven Design Reference — Eric Evans**  
https://www.domainlanguage.com/ddd/reference/  
Evans 自己整理的免費精華摘要（CC BY 4.0，2015 edition），每個模式一頁、定義精確。適合查詢具體定義；不適合作為初學入門。

**bliki: Domain Driven Design — Martin Fowler**  
https://martinfowler.com/bliki/DomainDrivenDesign.html  
最好的單頁概覽，5 分鐘讀完能建立足夠的框架感，幫助你在讀 Evans 前知道自己在哪裡。包含 Bounded Context、Context Map 的位置說明。

---

Bounded Context 告訴我們模型在哪裡有效；Context Map 告訴我們不同有效模型之間怎麼共存。下一章我們從另一個維度切入——在**問題空間**裡，不同部份的業務對公司的戰略價值不同，這個分類決定了你該用多少力氣建模型。

→ [Ch 18 子領域：Core / Supporting / Generic](./18-subdomains.md)
