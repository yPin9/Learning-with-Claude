# Ch 2 — 先把三個詞分清楚：SDD vs DDD vs BDD/TDD

> **目標**：一次釐清 Spec-Driven Development（SDD）、Domain-Driven Design（DDD）、Behaviour-Driven Development（BDD）、Test-Driven Development（TDD）各自解決什麼問題、彼此的關係為何，並建立本課的學習路線圖——SDD 是主軸，DDD 與需求工程是建模層，BDD/TDD 是品質保證層。

---

## 四個詞，四個戰場

軟體開發有一個長期的抱怨：「你寫的不是我要的。」

這句話暗藏著三個不同的問題，而 TDD、BDD、DDD、SDD 各自解決的，是這三個問題的不同切面：

```
問題層次                對應方法            核心問題
─────────────────────────────────────────────────────
「程式正確嗎？」         TDD                 紅-綠-重構：用測試驅動實作
「行為符合預期嗎？」     BDD / 驗收條件       Given-When-Then：把行為寫成可執行規格
「模型對了嗎？」         DDD                 通用語言 + 限界上下文：業務複雜性住在領域裡
「意圖傳達對了嗎？」     SDD（2025 新義）     規格是單一真相來源，AI 從規格產生程式
─────────────────────────────────────────────────────
```

這四層不是互斥的，而是疊加的。同一個功能，你可能同時用到全部四種。但混淆它們，才是最常讓人迷路的地方。

---

## 在這之前，人們怎麼做

2000 年代的主流做法是這樣：產品經理寫 Word 文件，工程師把文件讀完（或沒讀完），憑理解寫程式，交付之後發現「不對」，再改。

問題不在於人的能力，而在於自然語言無法消除歧義：「系統要能處理大量流量」這句話，後端工程師、SRE、產品經理各自的理解可以相差十倍。Alistair Mavin 在 Rolls-Royce 分析噴射引機控制系統的適航法規時，系統性地整理出自然語言需求的八種病症（模糊、含糊、複雜、遺漏、重複、冗長、不當實作、不可測試），並在 2009 年發表了 EARS（Easy Approach to Requirements Syntax，輕鬆需求語法）。

TDD 和 BDD 是對「不可測試」這個病症的直接反應：Kent Beck 的 Extreme Programming 把測試拉到前面，Dan North 在 2006 年把測試改名為「行為」，讓開發者用 Given-When-Then 寫出業務人員也看得懂的規格。

DDD 是對更深層問題的反應：Eric Evans 在 2003 年的《藍皮書》（*Domain-Driven Design: Tackling Complexity in the Heart of Software*）指出，軟體最難的部分不是技術，是業務邏輯的複雜性。光靠測試沒辦法解決「這個模型根本搞錯了業務意圖」的問題。

SDD 的 2025 新義是對 AI 時代的反應：當 LLM 可以產生程式，瓶頸從「寫程式」移動到「把意圖說清楚」。Sean Grove 在 2025 年 AI Engineer World's Fair 的演講「The New Code」提出：規格——不是程式、不是 prompt——才應該是版本控制的主角。這個論斷從 Andrej Karpathy 2017 年的 Software 2.0 論文一路鋪陳過來。

---

## TDD：用測試驅動實作

測試驅動開發（Test-Driven Development，TDD）的三步循環是：

```
紅（Red）    ── 先寫一個會失敗的測試
綠（Green）  ── 寫最少的程式碼讓測試過
重構         ── 在測試通過後整理程式碼
```

一個具體例子：

```python
# Step 1: 紅 — 先寫測試，這段現在會失敗
def test_cart_total_with_single_item():
    cart = ShoppingCart()
    cart.add_item("apple", price=30, qty=2)
    assert cart.total() == 60

# Step 2: 綠 — 寫最少實作讓它過
class ShoppingCart:
    def __init__(self):
        self.items = []

    def add_item(self, name, price, qty):
        self.items.append({"name": name, "price": price, "qty": qty})

    def total(self):
        return sum(i["price"] * i["qty"] for i in self.items)

# Step 3: 重構 — 若有重複、命名問題，在這裡整理
```

TDD 的邊界很清楚：它管的是「這段程式碼的行為正不正確」。它不管需求對不對、業務邏輯有沒有遺漏、領域模型有沒有把業務複雜性建模進去。

一個邊界例子：如果一開始的需求就寫錯了（「運費免費」但業務規則其實是「滿 500 才免運」），TDD 會讓你把錯誤的需求實作得非常正確。

---

## BDD：把行為寫成可執行規格

行為驅動開發（Behaviour-Driven Development，BDD）是 Dan North 在 2006 年提出的。他觀察到，「test」這個詞讓開發者聚焦在技術層面，而「behaviour」讓大家聚焦在業務價值。

BDD 的核心語法是 Given-When-Then，通常用 Gherkin 語言寫在 `.feature` 檔裡：

```gherkin
Feature: 購物車結帳

  Scenario: 單件商品正確計算總金額
    Given 購物車裡有 2 件蘋果，每件 30 元
    When 使用者查看結帳金額
    Then 總金額應為 60 元

  Scenario: 未滿免運門檻仍收運費
    Given 購物車商品總計 400 元
    And 免運門檻為 500 元
    When 使用者查看結帳金額
    Then 運費應為 80 元
    And 總計應為 480 元
```

這個規格可以直接用 Cucumber（JVM 生態）或 Behave（Python）執行，與真實程式碼掛鉤。這是 BDD 的「可執行規格（executable specification）」含義——也是「SDD 舊義」的源頭。

BDD 的邊界：它讓業務人員與開發者共同定義「什麼叫做完成」。但它不處理「這個 Feature 是不是對的商業決策」，也不處理系統層次的架構問題。

---

## DDD：把業務複雜性建模進去

領域驅動設計（Domain-Driven Design，DDD）是 Eric Evans 在 2003 年《藍皮書》提出的。它的核心主張：業務複雜性，不是技術複雜性，才是大多數軟體失敗的根本原因。

DDD 分兩個層次：

**戰略設計（Strategic Design）**，處理「系統邊界」問題：

```
通用語言（Ubiquitous Language）     ── 業務專家與工程師共用的詞彙表
限界上下文（Bounded Context）        ── 一個模型與語言在其中一致的邊界
情境地圖（Context Map）             ── 各限界上下文之間的關係與整合模式
子領域分類（Subdomain）             ── 核心 / 支援 / 通用，指引投資方向
```

**戰術設計（Tactical Design）**，處理「模型建構積木」問題：

```
實體（Entity）          ── 以身份（identity）貫穿時間的物件
值物件（Value Object）  ── 以屬性定義、不可變的物件
聚合（Aggregate）       ── 一組物件的一致性邊界，外部只能碰到根（Root）
儲存庫（Repository）    ── 像集合一樣存取聚合的介面
領域服務（Domain Service）── 橫跨多個聚合的無狀態操作
領域事件（Domain Event）── 某件業務上有意義的事發生了（過去式命名）
```

一個電商的迷你範例：

```python
# 通用語言的一致性：程式裡的名稱與業務溝通的名稱相同

# Value Object：Money（以屬性定義，不可變）
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("貨幣不同，無法相加")
        return Money(self.amount + other.amount, self.currency)

# Entity：Order（以 OrderId 識別，狀態可改變）
class Order:
    def __init__(self, order_id: str, customer_id: str):
        self.order_id = order_id          # 身份
        self.customer_id = customer_id    # 參照其他聚合：用 ID，不用物件參照
        self._lines: list["OrderLine"] = []
        self._status: str = "DRAFT"

    def add_line(self, product_id: str, price: Money, qty: int) -> None:
        if self._status != "DRAFT":
            raise ValueError("已確認的訂單不能再加品項")  # 不變式（invariant）
        self._lines.append(OrderLine(product_id, price, qty))

    def total(self) -> Money:
        if not self._lines:
            return Money(Decimal("0"), "TWD")
        return sum((line.subtotal() for line in self._lines[1:]),
                   start=self._lines[0].subtotal())

    def confirm(self) -> "OrderConfirmed":  # 回傳領域事件
        self._status = "CONFIRMED"
        return OrderConfirmed(self.order_id)

# Domain Event：OrderConfirmed（Shipping、Billing 收到後各自處理）
@dataclass
class OrderConfirmed:
    order_id: str
```

DDD 的邊界：它不告訴你「怎麼寫測試」，也不告訴你「用什麼框架」。它處理的問題是：程式裡的概念，有沒有忠實地反映業務的概念？

---

## SDD 的兩個含義：必須先分清楚

「規格驅動開發（Spec-Driven Development，SDD）」這個詞在 2025 年之前就存在，但意思和現在用的不完全一樣。兩個意思常被混在一起，要分開：

**舊義：可執行規格（executable specification）**

這是 BDD/ATDD 的延伸。用可執行的規格（Gherkin Given-When-Then、FitNesse 表格）作為系統行為的真相來源。代表工具：Cucumber、SpecFlow。規格「驅動」的是測試與驗收。

**新義（2025）：規格作為 AI 生成程式的真相來源**

這是從 Andrej Karpathy 的框架延伸出來的：如果 LLM 能產生程式，那麼輸入 LLM 的規格就變成了最重要的工件。Sean Grove（OpenAI）在 2025 年 AI Engineer World's Fair 把這個論點說得最清晰：把生成的程式碼留下來、把寫出它的 prompt 丟掉，就像「把原始碼丟掉、把 binary 版控」一樣本末倒置。工具代表：GitHub Spec Kit（2025 年 9 月，116k+ stars，查證日期 2026-06-30）、AWS Kiro（2025 年 7 月）、Tessl。

本課程主要討論的是新義的 SDD，但舊義的技術（Given-When-Then、EARS）在新義的 SDD 工作流裡仍然被直接使用（Kiro 的 `requirements.md` 就採用 EARS 語法寫驗收條件）。

> 如果你對 Ch 1 的背景——為什麼 LLM 把瓶頸推到意圖上——還不確定，先回看 [Ch 1 為什麼「規格」突然重要了](./01-why-specs-matter-now.md)。

---

## 四個方法的對比表

| 維度 | TDD | BDD | DDD | SDD（新義） |
|------|-----|-----|-----|------------|
| **核心問題** | 程式行為正確嗎？ | 功能行為符合預期嗎？ | 業務複雜性有沒有建入模型？ | 意圖有沒有被精確傳達給 AI？ |
| **主要受眾** | 工程師 | 工程師 + 業務人員 | 工程師 + 領域專家 | 工程師（＋ AI agent 讀規格） |
| **核心產物** | 單元測試、紅綠循環 | .feature 檔、Given-When-Then | 領域模型、限界上下文地圖 | 規格文件（constitution / requirements / design / tasks） |
| **粒度** | 方法 / 類別層次 | 功能行為層次 | 系統架構 / 業務語意層次 | 功能 / 系統全局（spec 覆蓋多層） |
| **對 AI 時代的作用** | 驗證 AI 產生的程式碼 | 提供 AI 可讀的驗收標準 | 給 AI agent 不可模糊的詞彙表與邊界 | 主軸：規格是 AI 的工作指令 |
| **能不能獨立使用？** | 是 | 是 | 是 | 是，但品質上限取決於其他層 |
| **歷史年代** | 1990 年代末（Beck / XP） | 2006（Dan North） | 2003（Evans 藍皮書） | 2025 新意義（Kiro / Spec Kit） |

---

## 它們的關係：一個套疊圖

```
┌─────────────────────────────────────────────────────────┐
│  SDD（新義）：AI 規格驅動工作流                           │
│  spec → plan → tasks → implement → verify               │
│                                                         │
│  ┌───────────────────────────────┐                      │
│  │  DDD：業務語義層               │                      │
│  │  通用語言 / 限界上下文 / 聚合   │                      │
│  │  ← 給 spec 提供詞彙與邊界      │                      │
│  └───────────────────────────────┘                      │
│                                                         │
│  ┌───────────────────────────────┐                      │
│  │  BDD / 需求工程               │                      │
│  │  EARS / Given-When-Then       │                      │
│  │  ← 給 spec 提供驗收條件格式   │                      │
│  └───────────────────────────────┘                      │
│                                                         │
│  ┌───────────────────────────────┐                      │
│  │  TDD                          │                      │
│  │  紅-綠-重構                   │                      │
│  │  ← 驗證 AI 產出的程式碼       │                      │
│  └───────────────────────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

DDD 和需求工程不是 SDD 的對立面，而是 SDD 的建模層：它們讓規格有料可寫，而且是精確的料。TDD/BDD 不是被 SDD 取代的，而是 SDD 工作流的驗證出口——AI 產出程式後，BDD 的驗收條件和 TDD 的測試才能確認「產出是對的」。

---

## 本課的學習路線圖

這門課分五個 Part，對應不同問題層次：

**Part 0（Ch 1-7）：基礎地圖**
先有歷史脈絡——SDLC 是什麼、瀑布的真相、敏捷怎麼來的——再有這章的四詞對比。不補這個底，後面的工具討論會浮在空中。

**Part 1（Ch 8-13）：需求工程**
自然語言的病症、User Story、EARS、Given-When-Then、Use Case、形式化規格。這些是「寫好規格」的技法，也是 AI 規格工作流的輸入品質保證。

**Part 2（Ch 14-21）：DDD 建模**
DDD 是這門課的建模骨架。通用語言、限界上下文、Event Storming——不是因為「DDD 是 SDD 的前置」，而是因為一份好的 AI 規格需要明確的詞彙表和邊界，這正是 DDD 給的。

**Part 3（Ch 22-26）：SDD 理念**
可執行規格 vs 規格再生成、Karpathy 的 Software 1.0/2.0/3.0 框架、Grove 的「The New Code」論證、懷疑論的最強反駁。建立完整的世界觀，才不會成為工具的人質。

**Part 4（Ch 27-32）：SDD 工具**
GitHub Spec Kit（安裝 → 工作流 → 底層機制）、AWS Kiro（三檔規格、EARS、steering、hooks）、工具全景與橫向對比。

**Part 5（Ch 33-44）：DDD × SDD 整合、維護、團隊**
兩個方法是同一場仗的不同武器。通用語言如何變成 LLM 的詞彙表、Bounded Context 如何對應 Agent Scope、自建 pipeline、規格漂移與腐化、安全面、懷疑論者論證與採用策略。

---

## 踩雷集錦

### 雷一：「SDD 是 TDD 的升級版，學了 SDD 就不用 TDD 了」

**錯誤直覺**：SDD 比 TDD 更高層次，所以替代了 TDD。

**正確認識**：SDD 管的是「意圖傳達」，TDD 管的是「程式正確性」——兩者在不同層次工作，不存在替代關係。在 SDD 工作流中，`implement` 階段結束後，TDD 的測試是最直接的驗證手段。GitHub Spec Kit 的 tasks.md 本身就預期任務包含測試。把 TDD 丟掉只會讓你無法知道 AI 有沒有把規格實作對。

### 雷二：「BDD 就是 SDD，Given-When-Then 就是規格」

**錯誤直覺**：BDD 的 .feature 檔就是 SDD 所說的規格，兩個詞可以互換。

**正確認識**：BDD 是 SDD 舊義的一個實現，但 SDD 新義的規格範疇比一個 .feature 檔大得多——它包含 constitution（原則）、requirements（需求 + EARS 驗收條件）、design（架構設計）、tasks（任務拆解）。BDD 的 Given-When-Then 是 SDD 規格裡「驗收條件」那個欄位的格式，不是全部。

### 雷三：「DDD 是重量級方法，小專案不適用，SDD 才是輕量的替代」

**錯誤直覺**：DDD 複雜又難學，SDD 工具（Kiro、Spec Kit）足夠讓 AI 直接產出程式，不需要 DDD。

**正確認識**：DDD 的戰略設計（通用語言、限界上下文）是「告訴 AI 你說的詞是什麼意思、這個 Agent 管什麼範圍」的核心機制。如果你的 spec 用模糊、充滿歧義的詞彙寫成，AI agent 會用它的訓練資料猜測——而那個猜測不一定與你的業務吻合。DDD 的戰術積木也不是「全部要用」，你可以只拿通用語言和聚合邊界這兩個概念，就已經讓你的規格品質提升一個檔次。

### 雷四：「SDD 就是 spec 寫清楚一點，沒什麼新東西」

**錯誤直覺**：「規格寫清楚」是個老生常談，SDD 只是換了個名字，本質上跟以前的文件驅動開發一樣。

**正確認識**：這個直覺部分正確：SDD 確實復甦了「規格優先」的主張，批評者也確實指出這有向瀑布倒退的風險。但差異在於：SDD 的規格是 AI agent 的直接工作指令，不是給人類看的文件——這個受眾的轉換改變了格式、粒度、版本控制策略的所有設計決策。同時，SDD 的工具（Kiro、Spec Kit）讓規格本身可以迭代，不是大前期規格固定下來才動工。更誠實的問題是：SDD 會不會在規格本身就寫錯的情況下讓你更快地走錯方向？是的，這正是 Ch 26 懷疑論者的核心論點。

---

## 進階延伸

SDD、DDD、BDD 的血緣關係比本章呈現的更深。Cucumber 的歷史頁面明確提到，Dan North 在開發 BDD 時直接借鑑了 Eric Evans 的「通用語言」概念——他把 Given-When-Then 語法視為「分析過程本身的通用語言」。這說明 BDD 一誕生就嘗試解決 DDD 解決的同一個問題：業務人員和開發者的語言鴻溝。

2025 年的 SDD 工具在命名和工作流設計上也借鑑了 BDD 的可執行規格語義：Kiro 的 `requirements.md` 裡的 EARS 驗收條件和 GitHub Spec Kit 的 `[NEEDS CLARIFICATION: ...]` 標記，都是把 BDD「讓規格可執行、可驗證」這個核心理念搬進 LLM 工作流。

如果你想追這條血脈，Ch 25 會系統地梳理 TDD / BDD / MDA（模型驅動架構）/ 文學編程的歷史譜系，以及 SDD 從各條線借了什麼。

---

## 動手練習

不需要工具，用紙筆或任何文字編輯器完成：

**練習 2-A：四詞歸類**

下面四個描述，各自對應 TDD / BDD / DDD / SDD 哪一個（可能有重疊）？說出理由。

1. 「我們在 sprint 開始前，讓產品和工程一起把每個 story 的 Given-When-Then 寫完，才開始動手。」
2. 「訂單確認後要發一個 OrderConfirmed 事件，讓 Shipping context 訂閱，不要讓 Order 直接呼叫 shipping service。」
3. 「在寫 `discount_service.apply()` 之前，我先寫一個測試確認 VIP 客戶打九折。」
4. 「把功能需求、設計決策、任務拆解都寫進三個 Markdown 檔，讓 AI agent 依照這份規格產出程式碼。」

**練習 2-B：找出問題層次**

下面這個失敗案例，根本原因在哪一層？

> 工程師用 TDD 把購物車折扣邏輯寫得測試全過，但交付後發現：業務規則是「VIP 折扣與滿額折扣不能疊加」，這個規則從來沒有出現在任何 User Story 或測試裡。

說出：哪一種方法如果用了，比較可能在早期抓到這個問題？為什麼？

---

## 本章重點整理

- TDD 管「程式正確性」，紅-綠-重構循環；BDD 管「行為符合預期」，Given-When-Then；DDD 管「業務複雜性有沒有進入模型」，通用語言 + 限界上下文；SDD（新義）管「意圖有沒有精確傳達給 AI」，規格作為單一真相來源。
- 這四個方法套疊而非互斥：DDD 和需求工程提供「寫好規格」的建模素材，BDD 提供驗收條件格式，TDD 驗證 AI 的產出，SDD 把整個流程串起來。
- SDD 有兩個含義：舊義是可執行規格（BDD/ATDD 的延伸），新義是規格作為 AI 工作指令的真相來源。本課主要討論新義，但舊義的技法（EARS、Given-When-Then）在新義的工作流裡直接被使用。
- 本課學習路線：Part 0 建立背景脈絡 → Part 1 需求工程技法 → Part 2 DDD 建模 → Part 3 SDD 理念論辯 → Part 4 工具實作 → Part 5 整合、維護、團隊。

---

## 自我檢核

- [ ] 用自己的話解釋：TDD 和 BDD 的差異不在「有沒有測試」，而在什麼？
- [ ] 用自己的話解釋：DDD 和 SDD 各自解決的是哪一層的問題？為什麼 DDD 是 SDD 的建模層而非替代品？
- [ ] 如果面試官問「SDD 是什麼」，你會先說哪個含義？為什麼需要先說清楚是哪個含義？
- [ ] 本課的 SDD 工作流（spec → plan → tasks → implement → verify）中，BDD 和 TDD 分別在哪個環節發揮作用？
- [ ] 「可執行規格」和「規格再生成」的差異在哪裡？（提示：見 Ch 22，但先試著用直覺回答）

---

## 延伸閱讀

1. **Martin Fowler — bliki: Domain Driven Design**
   https://martinfowler.com/bliki/DomainDrivenDesign.html
   一頁讀懂 DDD 的核心主張：模型驅動、通用語言、戰略設計。本章 DDD 段落的起點。Fowler 同時是 Evans 的長期合作者，解釋 DDD 如何填補他之前「模式書籍未覆蓋的空白」。

2. **Dan North — Introducing BDD**
   https://dannorth.net/blog/introducing-bdd/
   BDD 的原始論文（2006），首次定義 Given-When-Then 的 ATM 範例和「行為」概念轉換的動機。讀「What's the story?」和「acceptance criteria should be executable」兩節，看清楚 BDD 怎麼試圖連接業務與開發。

3. **Cucumber — History of BDD**
   https://cucumber.io/docs/bdd/history/
   BDD 血脈從 JBehave（2003）到 RSpec（2005）到 Cucumber 的演進，以及 Connextra 的 user story 格式與 Eric Evans 通用語言概念如何共同塑造了 Given-When-Then 的設計。本章「血脈」延伸段落的一手資料。

4. **Sean Grove — The New Code（AI Engineer World's Fair 2025）**
   https://www.youtube.com/watch?v=8rABwKRsec4
   SDD 新義最有力的論述：規格是版本控制的主角，code 是規格的表達式。看「shred the source, version-control the binary」類比和 OpenAI Model Spec 的案例（約 22 分鐘）。引用時注意：直接引言應核對官方影片，社群逐字稿（lawwu.github.io/transcripts/8rABwKRsec4.html）非官方文字紀錄。

5. **GitHub Spec Kit — spec-driven.md**
   https://github.com/github/spec-kit/blob/main/spec-driven.md
   Spec Kit 的宣言文件，最清楚地寫出「Power Inversion」：「Specifications don't serve code—code serves specifications.」讀「Power Inversion」段落，然後比對它如何把 BDD 的「可執行規格」概念融入 AI 代理工作流。指令名稱可能隨版本更動，以官方 repo 最新為準。

6. **Eric Evans — Domain-Driven Design: Tackling Complexity in the Heart of Software（藍皮書）**
   https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Software/dp/0321125215
   Evans，Addison-Wesley，2003。從 Part I（把領域模型放進工作）和 Part IV（戰略設計）開始讀。書很厚，但 Part I 三章加 Part IV 兩章，已經給你通用語言 + 限界上下文的完整輪廓。這是 Ch 14-21 整個 DDD Part 的根源文本。

7. **Andrej Karpathy — Software 2.0**
   https://karpathy.medium.com/software-2-0-a64152b37c35
   2017 年 11 月 11 日。SDD 新義在概念上的最遠源頭：「神經網路不只是另一種分類器，它代表著我們開發軟體方式的根本轉變。」讀開頭的 Software 1.0 vs 2.0 對比，和「訓練資料集被編譯成 binary」這段，再回看本章的 SDD 定位會更清晰。

---

下一章進入歷史：SDLC（Software Development Life Cycle，軟體開發生命週期）是怎麼來的，「開發流程」這個概念在人們開始寫大型軟體的年代意味著什麼，以及它和你現在讀的 SDD 課程有什麼關係。

→ [Ch 3 SDLC 到底是什麼](./03-sdlc.md)
