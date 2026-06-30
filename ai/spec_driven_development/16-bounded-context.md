# Ch 16 — Bounded Context：模型在哪裡為真

> **目標**：理解為何一個巨大統一模型注定崩潰，以及為何同一個詞（例如「Customer」）在不同 Bounded Context 代表截然不同的模型——並且這樣做是對的。

> 如果你對通用語言（Ubiquitous Language）還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)

---

## 一張圖，先建直覺

想像一家連鎖書店的資訊系統：

```
  ┌─────────────────────────────────────────────────────────┐
  │                  "統一 Customer 模型"（夢境）             │
  │                                                         │
  │   CustomerId, name, email, shippingAddress,             │
  │   billingAddress, loyaltyPoints, creditLimit,           │
  │   preferredPaymentMethod, returnHistory,                │
  │   fraudScore, newsletterPreferences,                    │
  │   supportTicketCount, lastContactAgent,                 │
  │   taxId, invoiceLanguage, ...                           │
  └─────────────────────────────────────────────────────────┘
          ↑             ↑             ↑
     銷售部門        客服部門       財務部門
   「只關心有沒有   「只關心誰打   「只關心開發票
     會員點數」      電話來過」     給誰」
```

每個部門都在這張大表上戳洞，要不同的欄位，寫入不同的欄位，用不同的規則判斷「Customer 合不合法」。結果：

- 財務部門改了 `taxId`，觸發了銷售的 loyalty point 計算邏輯（完全無關）。
- 客服部門需要快速查詢 `lastContactAgent`，但整個巨型物件要跟著 ORM join 八張表才能載入。
- 「Customer 的 email 是什麼」在銷售部門指的是行銷用的主郵件，在客服部門指的是目前這張工單的聯絡信箱。

這不是設計問題，是**語言問題**：同一個字「Customer」在不同的業務脈絡裡根本是不同的東西，卻被強行塞進同一個模型。

解法不是「把這個模型設計得更好」，而是**承認邊界**：劃定 Bounded Context（有界限的脈絡），讓每個脈絡都有自己完整、一致、正確的模型，然後明確地管理脈絡之間的翻譯。

---

## 在 Bounded Context 出現之前，人們怎麼做

2003 年以前，業界主流有兩條路：

**路線 A：單一正規化資料模型（One Canonical Data Model）**

企業架構師畫一張「企業資料模型」（Enterprise Data Model），要求全公司所有系統共用。1990 年代的 ERP 導入失敗有相當比例是這個原因——強行讓 10 個業務部門使用同一套術語，結果術語對誰都不精確，代碼爛成什麼都有的大雜燴。

**路線 B：每個系統各自為政，用 ETL 同步**

每個系統有自己的資料庫，定期 ETL（Extract-Transform-Load）同步資料。同步常常壞、常常延遲、資料格式互相不一致，除錯要同時看兩個系統的 log。

Eric Evans 在 2003 年的《Domain-Driven Design》（Addison-Wesley）裡指出了這兩條路共同的根本問題：**沒有人明確地承認邊界的存在**。人們要嘛幻想統一，要嘛讓邊界隱式地存在於 ETL 腳本的轉換邏輯裡。Bounded Context 的貢獻是把邊界變成**顯式的、被命名的、被管理的**一等公民。

Martin Fowler 把「把大型領域組織成一個 Bounded Context 網路」稱為「Strategic Design 的核心工作」，並且明確指出這是 Evans 在 DDD 之前業界普遍缺失的一塊。

---

## Bounded Context 的正式定義

**Bounded Context（有界限的脈絡）** 是一個顯式邊界，邊界之內：

1. **一個領域模型**是完整且一致的
2. **一種通用語言（Ubiquitous Language）** 的每個詞都有精確、無歧義的含義
3. **模型的規則和不變量（invariants）** 在此邊界內有效

邊界之外，同一個詞可以有完全不同的意義——這不是問題，這是預期的設計。

從 area-5-ddd.md 的定義：
> A Bounded Context is the explicit boundary inside which one domain model and one dialect of the Ubiquitous Language are consistent and valid.

注意「dialect」（方言）：Bounded Context 不是說每個脈絡用的語言互不相干，而是整體通用語言對同一個字有各個脈絡的「方言」解讀。

---

## 同一個字，三種模型：具體範例

繼續用電商書店。「Customer」這個字在三個 Bounded Context 裡是什麼：

### 銷售脈絡（Sales Context）

```python
# 銷售脈絡關心：這個人能不能下訂單、有多少點數、要送到哪裡
@dataclass(frozen=False)
class Customer:
    customer_id: CustomerId
    name: str
    email: str  # 行銷主郵件
    shipping_address: Address
    loyalty_tier: LoyaltyTier  # BRONZE / SILVER / GOLD
    loyalty_points: int

    def can_place_order(self, total_amount: Money) -> bool:
        # 規則：GOLD 客戶享受無限制；其餘客戶每天上限 NT$50,000
        if self.loyalty_tier == LoyaltyTier.GOLD:
            return True
        return total_amount.amount <= 50_000
```

### 客服脈絡（Support Context）

```python
# 客服脈絡關心：誰在這張工單的另一頭、上次是哪個客服處理
@dataclass(frozen=False)
class Customer:
    customer_id: CustomerId   # 同一個 ID，但這是唯一共用的東西
    display_name: str
    contact_email: str        # 這張工單的聯絡信箱，可能跟銷售的不同
    phone: str | None
    last_agent_id: AgentId | None
    open_ticket_count: int

    def is_repeat_caller(self) -> bool:
        return self.open_ticket_count > 3
```

### 財務脈絡（Finance Context）

```python
# 財務脈絡關心：開發票給誰、稅號是什麼、用什麼語言出帳
@dataclass(frozen=True)  # 財務記錄不可更改
class Customer:
    customer_id: CustomerId
    legal_name: str           # 法定名稱，跟銷售的 display name 可能不同
    tax_id: str
    billing_address: Address
    invoice_language: str     # 'zh-TW', 'en'
    credit_limit: Money

    def invoice_recipient(self) -> str:
        return f"{self.legal_name} ({self.tax_id})"
```

這三個 `Customer` 類別：

| 屬性 | 銷售脈絡 | 客服脈絡 | 財務脈絡 |
|---|---|---|---|
| `customer_id` | 有（共同識別子） | 有（共同識別子） | 有（共同識別子） |
| `email` | 行銷主郵件 | 當前工單聯絡信箱 | 不存在 |
| `address` | `shipping_address` | 不存在 | `billing_address` |
| `name` | 顯示名稱 | 顯示名稱 | 法定名稱 |
| 可變性 | 可變 | 可變 | 不可變（財務記錄） |
| 核心業務規則 | 能否下單、點數折扣 | 是否重複來電 | 開立發票 |

三個 `Customer`，三種模型，三套規則。它們都是對的，因為它們在各自的 Bounded Context 裡為真。

---

## 底層機制：為什麼巨大模型會崩

要理解 Bounded Context 解決了什麼，要先看清楚統一模型崩潰的機制。

### 機制一：不變量衝突（Invariant Conflict）

每個業務領域都有自己的不變量：

- 銷售脈絡：「Customer 的 `loyalty_points` 不能為負數」
- 財務脈絡：「Customer 的 `credit_limit` 必須由財務審核核准後才能修改」

在統一模型裡，這兩條規則要寫在同一個類別的同一套驗證邏輯裡。問題來了：

```python
# 統一模型試圖保護所有人——結果保護了誰都不對
class UnifiedCustomer:
    def update_credit_limit(self, new_limit: Money, approved_by: str | None):
        # 財務的規則
        if approved_by is None:
            raise ValueError("需要財務審核")
        self.credit_limit = new_limit

    def add_loyalty_points(self, points: int):
        # 銷售的規則
        if self.loyalty_points + points < 0:
            raise ValueError("點數不能為負")
        self.loyalty_points += points

    def update_contact_email(self, email: str, ticket_id: str | None):
        # 客服的規則：要有工單才能更新聯絡信箱
        # 但這個規則跟銷售的 email 更新規則衝突！
        # 銷售：客戶自己在帳號設定改就行了
        # 客服：要有工單號才能改
        ???
```

當三個脈絡的規則互相衝突，統一模型的開發者只有三條路：
1. 把規則做成分支判斷（`if caller == "sales": ...`）——業務邏輯變成隱式的 context switch
2. 讓規則互相妥協，結果對誰都不精確
3. 放棄保護，變成 anemic domain model（貧血模型），業務規則散落在 service 層

每一條路都是腐化的開始。

### 機制二：語義漂移（Semantic Drift）

統一模型裡，`Customer.email` 欄位最初是「行銷郵件」。後來客服系統被接入，開始把「當前聯絡信箱」也寫進去。六個月後，沒有人記得這個欄位的「真正意義」了——兩種含義混在一起，任何修改都可能破壞某個下游使用方。

這正是通用語言崩潰的方式：不是突然爆炸，而是慢慢地，每個詞的意義開始變模糊。

> 如果你想了解語義漂移如何破壞通用語言，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)

### 機制三：協調成本爆炸（Coordination Explosion）

統一模型意味著任何一個脈絡要修改共用物件的結構，都需要所有脈絡同意。這在組織層面的代價是：

- 跨部門會議增加
- 發布週期拉長（要等所有方確認不破壞）
- 最終演化成「誰也不敢改」的 Big Ball of Mud（大泥球）

Bounded Context 讓各個脈絡**獨立演化**。財務部門想把 `Customer` 的 `credit_limit` 從單幣別改為多幣別，只要在財務脈絡內改，不影響銷售脈絡。

---

## Bounded Context 的邊界在哪裡

理論上的答案是「業務能力的邊界」，但實務上怎麼找？

**信號 1：語言分歧**

當兩個團隊用同一個詞，卻需要在句子裡加修飾詞才能區分（「業務的 Customer」vs「客服的 Customer」），這就是邊界存在的信號。通用語言開始出現方言，就是 Bounded Context 的天然輪廓。

**信號 2：不同的演化速率**

銷售系統可能每週更新促銷邏輯，財務系統可能每季更新一次稅率規則。演化速率相差懸殊的兩個部分，強行放在一起只會讓快的那個被慢的拖累。

**信號 3：不同的一致性需求**

銷售下訂單需要即時一致性（庫存必須立即扣減），財務對帳可以接受最終一致性（隔日批次計算）。一致性需求不同的部分，共享一個事務邊界會造成不必要的鎖競爭或強制補嘗試交易。

**信號 4：組織邊界**

Conway 定律（Conway's Law）告訴我們：系統的結構往往映射組織的溝通結構。如果銷售團隊和財務團隊有不同的主管、不同的上線節奏、開不同的站立會議——那麼它們的程式碼往往應該也在不同的 Bounded Context 裡。

---

## Bounded Context 的三種物理形態

找到邊界之後，Bounded Context 可以以不同的物理形態存在：

| 形態 | 適用時機 | 好處 | 代價 |
|---|---|---|---|
| **模組（Module）** | 同一個程式庫，但有明確的 package 邊界 | 部署最簡單，可以直接呼叫 | 邊界容易被穿透（import 就破防了） |
| **服務（Service）** | 獨立部署的微服務或服務 | 邊界強制，獨立擴展，技術異構 | 網路延遲、分散式事務複雜度 |
| **子系統（Subsystem）** | 同一個組織，但不同程式庫（mono-repo 的一個 package） | 邊界比模組強，比服務便宜 | 需要嚴格的建置規則防止循環依賴 |

Bounded Context 本身是概念層的邊界，不是部署層的邊界。一個 Bounded Context 可以是一個微服務，也可以是一個模組。把「Bounded Context 就是微服務」當成公理是常見的誤解。

---

## Bounded Context 與 Ubiquitous Language 的關係

Bounded Context 和 Ubiquitous Language 是一體兩面：

```
Bounded Context
  ├── 定義「語言生效的範圍」
  │       └── Ubiquitous Language 在此邊界內有精確含義
  └── 邊界的形狀由「語言的一致性」決定
          └── 當同一個詞需要消歧義，就是邊界存在的地方
```

沒有 Bounded Context，Ubiquitous Language 會因為範圍無邊而無法做到精確。沒有 Ubiquitous Language，Bounded Context 就只是一個物理上的邊界，裡面的模型仍然是混亂的。兩者缺一不可。

---

## 跨 Bounded Context 的溝通

兩個 Bounded Context 需要溝通時，有幾種方式。這裡只給直覺，下一章會深入：

**翻譯（Translation）**

最常見。每個 Bounded Context 保有自己的模型，邊界上有一個翻譯層負責轉換：

```python
# 客服脈絡需要知道某個顧客的 loyalty tier，
# 但它不應該直接依賴銷售脈絡的模型

# 翻譯層（Anticorruption Layer）
class SalesContextAdapter:
    def __init__(self, sales_api: SalesAPI):
        self._sales_api = sales_api

    def get_customer_tier(self, customer_id: str) -> str:
        # 呼叫銷售脈絡的 API
        sales_customer = self._sales_api.find_customer(customer_id)
        # 翻譯成客服脈絡的術語
        return self._map_tier(sales_customer["loyaltyTier"])

    def _map_tier(self, sales_tier: str) -> str:
        mapping = {"GOLD": "vip", "SILVER": "regular", "BRONZE": "regular"}
        return mapping.get(sales_tier, "regular")
```

**共用識別子（Shared Identity）**

各個脈絡保有自己的 `Customer` 模型，但共用同一個 `CustomerId`。這是允許的——只要 ID 的語義（「這個 ID 唯一識別一個顧客」）在所有脈絡裡是一致的。

```python
# 共用的識別子：一個簡單的 Value Object
@dataclass(frozen=True)
class CustomerId:
    value: str

    def __post_init__(self):
        if not self.value.startswith("CUST-"):
            raise ValueError(f"Invalid CustomerId format: {self.value}")
```

三個 Bounded Context 都使用 `CustomerId("CUST-10042")`，但各自持有完全不同的 `Customer` 物件。

---

## 對比取捨：有界 vs 統一

| | 統一模型 | Bounded Context |
|---|---|---|
| **初期複雜度** | 低（一個類別） | 中（需要設計邊界） |
| **長期維護** | 指數上升（任何改動都影響全局） | 線性增長（各自演化） |
| **語言精確性** | 隨規模下降（消歧義修飾詞激增） | 邊界內始終精確 |
| **跨脈絡查詢** | 天然容易（一個 join） | 需要翻譯或 API 呼叫 | 
| **部署獨立性** | 無（全部綁在一起） | 可以（各自部署）| 
| **不變量強度** | 弱（各方規則互相妥協） | 強（邊界內完整執行）|
| **Conway 定律對齊** | 差 | 好（可以對齊組織邊界）|
| **AI 輔助精確度** | 差（LLM 會把不同脈絡的同名詞混用） | 好（每個脈絡有明確詞彙表）|

---

## Bounded Context Canvas：讓邊界可見

設計 Bounded Context 的一個實用工具是 **Bounded Context Canvas**（由 DDD Crew 社群推廣）。它強迫你明確回答：

```
┌────────────────────────────────────────────────────────┐
│  Bounded Context：銷售脈絡（Sales Context）             │
├───────────────────────┬────────────────────────────────┤
│ 目的（Purpose）       │ 讓顧客能夠瀏覽商品、下訂單、    │
│                       │ 享用忠誠度計畫                 │
├───────────────────────┼────────────────────────────────┤
│ 核心概念              │ Customer, Order, Product,      │
│ （Ubiquitous Language)│ CartItem, LoyaltyTier          │
├───────────────────────┼────────────────────────────────┤
│ 對外提供              │ 查詢顧客忠誠等級                 │
│ （Outbound）          │ 通知訂單已成立（Domain Event）   │
├───────────────────────┼────────────────────────────────┤
│ 對外依賴              │ 財務脈絡：核准退款               │
│ （Inbound）           │ 庫存脈絡：確認商品庫存           │
├───────────────────────┼────────────────────────────────┤
│ 不變量（Invariants）  │ loyalty_points >= 0            │
│                       │ 訂單總額 = 各 OrderLine 小計之和 │
└───────────────────────┴────────────────────────────────┘
```

這個 Canvas 不需要特殊工具，一張 A3 紙或白板就能畫。它的價值在於**強制顯式化**：當你填不出「目的」這格，就代表你還沒真正理解這個脈絡的邊界為何存在。

---

## 踩雷集錦

### 雷 1：把「資料庫的 Table 邊界」當 Bounded Context 邊界

**錯誤直覺**：`customers` 表是一張表，所以 `Customer` 只有一個模型，Bounded Context 只是不同的 Service 層查它。

**正確認識**：Bounded Context 是**概念模型的邊界**，不是資料庫架構的邊界。三個 Bounded Context 可以各自有一個 `customers` 表（各自包含自己需要的欄位），也可以共用一張表但透過不同的 View 或 Repository 存取。資料庫怎麼切是實作細節，Bounded Context 的邊界先於它。

---

### 雷 2：Bounded Context = 微服務

**錯誤直覺**：「我們要做 Bounded Context，所以要把系統拆成微服務。」

**正確認識**：Bounded Context 是**概念層**的設計決策，微服務是**部署層**的技術決策。一個 Bounded Context 可以以模組形式存在於 monolith 裡，也可以是一個微服務。很多團隊在 Bounded Context 的概念還沒清楚之前就急著拆微服務，結果是：邊界畫錯，拆出一堆「nano-service」，每個業務操作都要跨服務呼叫，分散式系統的複雜度全來了，但 Bounded Context 的好處一個也沒有。

先畫清楚 Context Map（下一章），再決定部署拓撲。

---

### 雷 3：Bounded Context 的邊界定了就不能動

**錯誤直覺**：Bounded Context 是前期的架構決策，一旦定了就是約束，以後改動成本很高。

**正確認識**：Bounded Context 的邊界**應該隨業務演化**。初期的電商系統可能只有「銷售」和「財務」兩個脈絡，成長後可能要把「會員忠誠度計畫」從銷售脈絡分離出來，成為一個獨立的 Context。這是正常的、預期的演化。Evans 的 DDD 本身就是迭代的——Context Map 是活文件，不是石板。

---

### 雷 4：邊界越細越好

**錯誤直覺**：既然 Bounded Context 能解決統一模型的問題，那就盡量多分，每個 entity 都是一個 Context。

**正確認識**：Bounded Context 分得太細，跨 Context 的協調成本反而超過了邊界帶來的好處。判斷的標準是**語言的一致性**和**業務能力的完整性**，而不是「越小越好」。如果兩個概念在同一個業務能力裡用同一套語言描述，強行分開只會製造翻譯的噪音。

---

### 雷 5：共用 id 就是共用模型

**錯誤直覺**：「客服和銷售都用 `customer_id`，所以它們用的是同一個 `Customer` 模型，不需要分開。」

**正確認識**：共用識別子是允許的、正確的——它是跨 Bounded Context 的「關聯線」。但識別子相同不代表模型相同。`customer_id` 讓客服能去問銷售「這個顧客是 VIP 嗎？」，但客服持有的 `Customer` 物件和銷售持有的 `Customer` 物件是完全不同的兩個類別，各自承載各自的業務意義。

---

## 進階延伸：Bounded Context 作為 AI Agent 的作用域

2024-2026 年間，DDD 社群開始把 Bounded Context 重新框架為 AI coding agent 的**詞彙表邊界**（vocabulary scope）。

當你讓 LLM 協助你在「銷售脈絡」裡設計功能，你給它的 context 應該包含：

- 銷售脈絡的 Ubiquitous Language 詞彙表
- 銷售脈絡的核心概念和不變量
- 銷售脈絡與其他 Context 的整合點（有什麼事件、有什麼 API）

而**不是**整個系統的所有概念。這有兩個好處：

1. **防止語義污染（Semantic Pollution）**：LLM 不會把客服脈絡的「Customer.contact_email」和銷售脈絡的「Customer.email」混用，因為在你給的 context 裡根本沒有前者。

2. **縮小作用域，提升精確度（Reduced Hallucination Surface）**：context 越小，LLM 越不容易捏造不存在的概念。Bounded Context 天然地幫你切出適合當 LLM context 的「意義單元」。

這個方向的具體應用在 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md) 會深入展開。

DDD.academy 的 Thomas Coopman 記錄了一個 2026 年的工作坊實踐（見延伸閱讀），說明如何用 LLM 協助起草 Bounded Context Canvas——但他明確警告：LLM 在這裡的角色是「加速初稿」，邊界的最終判斷必須來自和領域專家的對話，不能外包給 LLM。

> 注意：DDD + LLM 的具體實踐是 2024-2026 年間快速演化的領域，相關工具和最佳實踐仍在變動中。

---

## 動手練習

**情境**：你的團隊在做一個線上課程平台，有以下業務流程：

1. 學員瀏覽課程目錄，購買課程
2. 學員觀看課程影片，完成作業
3. 講師上傳課程內容，收取分潤
4. 財務人員確認匯款，計算稅務

**練習步驟**：

1. 列出你在上述流程中看到的「語言分歧點」——哪些詞在不同業務流程裡需要消歧義？
2. 草擬出 3-4 個可能的 Bounded Context，每個寫下：名稱、核心概念（3-5 個詞）、主要不變量（1-2 條）。
3. 選其中一個你最不確定的 Bounded Context，用 Bounded Context Canvas 的格式（目的 / 核心概念 / 對外提供 / 對外依賴 / 不變量）把它展開。
4. 找一個你認為兩個 Bounded Context 之間需要溝通的場景（例如「購買完成後，學習紀錄需要更新」），描述你會用什麼方式讓它們溝通，以及各自的 `User` 或 `Customer` 模型長什麼樣子。

這個練習沒有唯一正解，邊界的劃法可以有多種合理方案。重點是能說清楚**你的劃法背後的理由**——這正是領域建模的核心能力。

---

## 本章重點整理

- **Bounded Context** 是一個顯式邊界，邊界內只有一個一致的領域模型和一套精確的通用語言。
- 同一個詞（如「Customer」）在不同 Bounded Context 裡可以代表完全不同的模型——這是設計，不是問題。
- 巨大統一模型崩潰的三個機制：**不變量衝突**、**語義漂移**、**協調成本爆炸**。
- 找 Bounded Context 邊界的四個信號：語言分歧、演化速率差異、一致性需求不同、組織邊界。
- Bounded Context 是概念層邊界，**不等於**微服務、資料庫分割、或部署單元。
- 跨 Bounded Context 溝通需要翻譯，**共用識別子**（shared identity）是合法的、推薦的關聯方式。
- Bounded Context Canvas 是讓邊界「可見、可討論、可演化」的輕量工具。
- 在 AI 輔助開發中，Bounded Context 天然地切出精確的詞彙作用域，減少 LLM 的語義污染。

---

## 自我檢核

- [ ] 用自己的話解釋：為什麼「同一個 `Customer` 類別，被三個部門用」會帶來問題？如果面試官問你這個問題，你會怎麼回答？
- [ ] 你能不靠書，說出找 Bounded Context 邊界的至少兩個具體信號嗎？
- [ ] 你能區分「Bounded Context = 微服務」和「Bounded Context 可以是微服務」的差異嗎？
- [ ] 如果有人說「直接共用一個 `Customer` 類別，加 `context` 欄位來區分」，你如何反駁？
- [ ] Bounded Context 和 Ubiquitous Language 的關係是什麼？哪個先、哪個後？

---

## 延伸閱讀

- **Domain-Driven Design: Tackling Complexity in the Heart of Software**（Eric Evans，2003，Addison-Wesley）  
  [https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215](https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215)  
  Part IV「Strategic Design」是 Bounded Context 的原始出處。Evans 在這裡第一次系統地說明「為什麼統一模型行不通」以及如何劃定邊界。是本章所有概念的第一手來源。

- **bliki: Domain Driven Design**（Martin Fowler，更新 2020-04-22）  
  [https://martinfowler.com/bliki/DomainDrivenDesign.html](https://martinfowler.com/bliki/DomainDrivenDesign.html)  
  Fowler 把 Bounded Context 稱為 Strategic Design 的核心工作，並且解釋了它在 Evans 之前業界缺失的原因。篇幅短，適合作為讀 Blue Book 前的暖身。

- **Domain-Driven Design Distilled**（Vaughn Vernon，2016，Addison-Wesley）  
  [https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420](https://www.amazon.com/Domain-Driven-Design-Distilled-Vaughn-Vernon/dp/0134434420)  
  Vernon 用更平易的語言重述了 Bounded Context 和 Context Mapping，並且整合了 Subdomain 的分類（Core/Supporting/Generic）。如果 Blue Book 太硬，這本是最好的入門替代。

- **ddd-crew/context-mapping**（DDD Crew，GitHub）  
  [https://github.com/ddd-crew/context-mapping](https://github.com/ddd-crew/context-mapping)  
  有清楚的圖示說明九種 Context Mapping 模式（ACL、OHS、Shared Kernel 等）和選擇指南。本章提到翻譯層（ACL）的具體模式在這裡有最清楚的圖解，是下一章的預習材料。

- **Accelerate your Strategic Design with LLMs**（Thomas Coopman，DDD.academy）  
  [https://ddd.academy/accelerate-your-strategic-design-with-llms/](https://ddd.academy/accelerate-your-strategic-design-with-llms/)  
  2026 年工作坊記錄，說明如何讓 LLM 協助起草 Bounded Context Canvas，同時明確區分「LLM 加速初稿」和「人與領域專家確認邊界」的角色。直接對應本章進階延伸段落的應用。

---

Bounded Context 給了我們邊界，但邊界本身不夠——我們還需要明確地描繪各個 Context 之間如何互動。那些互動，正是下一章的主題。

→ [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md)
