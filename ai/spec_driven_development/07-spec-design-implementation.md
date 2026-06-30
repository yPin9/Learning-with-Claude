# Ch 7 — 規格 vs 設計 vs 實作

> **目標**：理解規格、設計、實作三層各自做什麼、為何多數價值與風險住在前兩層、什麼是「單一真相來源（Single Source of Truth）」、以及文件腐化如何讓這三層默默分家。

## 三層的直覺圖像

在動手分析每一層之前，先看一張蓋房子的類比：

```
┌────────────────────────────────────────┐
│  規格 (Specification)                  │
│  WHAT + WHY                            │
│  「這棟房子要住 4 口人、有兩間臥室、  │
│   廚房必須有自然採光、最大預算 X」     │
│  ← 業主與建築師溝通的語言              │
└──────────────────┬─────────────────────┘
                   │ 滿足
┌──────────────────▼─────────────────────┐
│  設計 (Design)                         │
│  HOW at architecture level             │
│  平面圖、結構計算、材料選擇、          │
│  承重牆放哪裡、冷熱水管路徑           │
│  ← 建築師與承包商溝通的語言            │
└──────────────────┬─────────────────────┘
                   │ 實現
┌──────────────────▼─────────────────────┐
│  實作 (Implementation)                 │
│  HOW in a specific language/tool       │
│  RC 澆灌、磁磚施工、水電配線          │
│  ← 現場工人做的事                      │
└────────────────────────────────────────┘
```

三層對應關係很直覺，但在軟體業這張圖長期被忽略——因為過去三層都由同一批工程師完成，很少有人需要精確區分「我現在在哪一層工作」。AI 驅動開發改變了這個假設：當 LLM 把實作速度壓低到接近零，三層的分野就從學術概念變成日常工作的基礎設施。

## 在這之前人們怎麼做，為何不夠好

1970 年代，大型軟體專案的標準答案是「先寫完整規格文件，再開始設計，再開始編碼」。Royce 1970 年的論文（見 [Ch 4 瀑布的真相](./04-waterfall-myth.md)）描述了這種線性流程，但他本人明確指出純粹的單次循環「有風險且會招致失敗」——他建議至少跑兩輪。後來被稱為「瀑布」的東西，是業界把 Royce 的警告刪掉之後留下來的那個骨架。

瀑布派的問題不是「三層」本身，而是「三層之間的回饋被切斷了」：規格寫完就封存，設計完成就歸檔，後期發現規格有誤，修改成本早已高到難以承受。Boehm 1981 年的變更成本曲線（詳見 [Ch 6 變更成本曲線](./06-cost-of-change-curve.md)）捕捉了這個趨勢——越晚發現問題，代價越高，雖然那個「100:1」的精確倍數是無法查證的民間傳說。

敏捷運動（2001 年宣言）的反應是「寧可要能跑的軟體，不要龐大的文件」，把規格的重要性降到最低。這解決了「文件寫完沒人更新」的問題，卻製造了另一個問題：意圖只存在人的頭腦裡，沒有任何可讀的產物。當團隊規模擴大、人員流動、或 AI agent 進來接手時，沒有規格幾乎等於沒有地基。

現在的問題是在兩個極端之間找到正確平衡——而找到這個平衡的前提，是先把三層說清楚。

## 正式定義：三層各是什麼

### 規格（Specification）— WHAT + WHY

規格回答兩個問題：
- **WHAT**：這個系統要做什麼行為？（功能需求）
- **WHY**：為什麼需要這個行為？服務哪個使用者目標？（意圖）

規格不應該說 HOW。一份好的規格讀起來像法律條文或合約：清晰、可驗證、可爭辯。

```
[BAD — 混入了 HOW]
系統應該用 Redis 快取使用者 session，TTL 設為 30 分鐘。

[GOOD — 只有 WHAT/WHY]
使用者登入後，系統應在 30 分鐘無操作後要求重新驗證，
以保護未離開電腦的使用者帳戶。
```

BAD 版本把一個設計決策（Redis、TTL 實作）寫進了規格層，日後任何技術選型的改變都變成了「違反規格」——而那個變更可能根本不影響業務目標。

### 設計（Design）— HOW at architecture level

設計把規格翻譯成結構上的決策：
- 系統切成哪些元件（服務、模組、函式庫）？
- 元件之間的介面是什麼？
- 在多個可行方案中，選了哪個，為何捨棄其他？

設計是「用來溝通取捨」的語言，而不是「給機器執行」的指令。好的設計文件保留了決策過程中被拒絕的選項及拒絕原因——這些「為何不選 X」的紀錄，往往比最終選擇本身更有長期價值。

```
[設計文件片段範例]

## 使用者 Session 管理

選定方案：伺服器端 session，token 存於 HTTP-only cookie

考慮過的替代方案：
- JWT（自包含、stateless）：拒絕，因為無法即時撤銷，
  不符合規格中「帳號被盜後 15 分鐘內應能強制登出全部裝置」的需求。
- 純前端 localStorage：拒絕，XSS 風險不可接受。

Session store：Redis（in-memory，可水平擴展），
fallback 到 PostgreSQL 以持久化要求重設密碼的 token。
```

注意這裡 Redis 才出現——在設計層，而不是規格層。

### 實作（Implementation）— HOW in a specific language/runtime

實作是把設計決策用特定語言、框架、函式庫表達出來的行為。它包含：
- 程式碼本身
- 設定檔
- 資料庫 migration
- 測試（可視為實作的一部分，也可視為驗證規格的工具——兩種看法都有人支持）

實作是「給機器執行」的，因此它是三層中最接近機器的層——也是最容易過時、最需要重寫的層。

## Brooks 的「essence vs accident」框架

Fred Brooks 在 1986 年的〈No Silver Bullet〉把軟體的難度拆成兩類：

- **本質難度（Essence）**：「軟體本質內在的困難」——概念建模、規格制定、設計取捨。這些困難無法被工具消除，因為它們就是問題本身。
- **偶發難度（Accident）**：「今天生產軟體時附帶的困難，但非本質所在」——選擇語言、調試 IDE、管理編譯時間。這些困難可以被工具大幅壓縮。

Brooks 的斷言（逐字引用，來自原始論文）：

> The hardest single part of building a software system is deciding precisely what to build... No other part of the work so cripples the resulting system if done wrong. No other part is more difficult to rectify later.

換成我們的三層語言：**規格屬於 Essence；大部分實作屬於 Accident**。過去半世紀軟體生產力的進步（高階語言、IDE、框架、套件管理）幾乎全部攻擊的是 Accident，而 Essence 幾乎沒有被動過。

這就是為什麼 AI 的角色是根本性的。LLM 把實作的成本壓到接近零——它繼續攻擊 Accident，但 Accident 已所剩無幾。接下來的瓶頸毫無疑問地落在 Essence：你能不能清楚說出「這個系統要做什麼」。

Sean Grove 在 AI Engineer World's Fair 2025 的演講〈The New Code〉（逐字引用來自社群轉錄稿）更直接：

> Code is sort of 10% to 20% of the value... The other 80% to 90% is in structured communication.

> Code itself is actually a lossy projection from the specification.

最後這句話尤其值得停下來想一想：從規格到程式碼是單向的「有損壓縮」——你看著一份程式碼，通常無法復原「當初為什麼這樣寫」。我們長期把這個「有損結果」當成版本控制的對象，而把「原始訊號」（規格）扔掉。Grove 的比喻是：「就好像你把原始碼銷毀，然後非常仔細地版本控制那個 binary。」

## 三層的取捨全景

| 維度 | 規格 | 設計 | 實作 |
|------|------|------|------|
| 回答的問題 | WHAT + WHY | HOW（架構層） | HOW（語言/工具層） |
| 主要讀者 | 業務、PM、QA、法遵 | 架構師、資深工程師、AI agent | 編譯器/直譯器（機器） |
| 腐化速度 | 慢（業務需求不常劇烈改變） | 中（架構會演進） | 快（每次 refactor 都動） |
| 錯誤的代價 | 最高（越早進規格的錯，修起來越貴） | 中高（架構錯誤傳播廣） | 較低（可重寫） |
| 對 LLM 的價值 | 提供意圖，LLM 最需要 | 提供約束，防止 LLM 發散 | LLM 最容易生成的部分 |
| Brooks 分類 | Essence | Essence | Accident（大部分） |

這張表有一個關鍵推論：**規格的錯是最貴的**，但規格的腐化也是最隱形的——因為沒有編譯器會在規格和程式碼不一致時報錯。

## 單一真相來源（Single Source of Truth）與文件腐化

「單一真相來源（Single Source of Truth，SSOT）」是一個資訊架構的原則：每份資料只有一個權威來源；其他地方的副本都從這個來源衍生，當來源更新時，副本應該跟著更新。

在軟體規格的脈絡下，問題是這樣出現的：

```
週一：
  規格文件說「訂單金額上限 NT$ 99,999」
  程式碼：MAX_ORDER = 99999
  API 文件：max_amount: 99999

週三（PM 在 Slack 說「上限改成十萬」，沒有更新文件）：
  規格文件說「訂單金額上限 NT$ 99,999」  ← 過期
  程式碼：MAX_ORDER = 100000             ← 已更新
  API 文件：max_amount: 99999            ← 過期

三個月後：
  新進工程師看規格文件開發前端驗證 → 以 99,999 為上限
  QA 跑測試發現金額 100,000 也能過 → 困惑
  前端開發者確信自己依規格行事 → 互相指責
```

這就是**文件腐化（Documentation Rot）**：程式碼更新了，但規格和文件沒有跟著更新，導致三層逐漸說著不同的故事。

敏捷運動對文件腐化的答案是「不要寫大量文件，只信任執行中的程式碼」。這在某種程度上確實有效——程式碼不會撒謊，但程式碼也不會解釋「為什麼」。

SDD 的答案是另一個方向：把規格設計成「不能靜默腐化」的樣子。GitHub Spec Kit 的官方文件說（逐字引用）：

> The specification becomes the primary artifact. Code becomes its expression... maintaining software means evolving specifications.

這是把 SSOT 的地位從「程式碼」搬到「規格」。程式碼從原始產物變成衍生產物。當 AI 可以從規格生成程式碼時，讓規格當 SSOT 的成本降低了——因為重新生成程式碼的成本幾乎為零。

不過這個願景有個前提：你必須有紀律在每次規格改變時同步更新，而且你必須信任 LLM 生成的程式碼忠實反映規格——而 LLM 恰恰是不確定性的來源之一。我們在 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 會回頭深挖這個問題。

## 一個具體的三層分拆練習

下面用「電商購物車結帳」這個功能，示範同一件事在三層的描述有何不同：

**規格層（WHAT/WHY）**
```
需求 CART-001：
  當已認證使用者確認結帳時，
  系統應在 3 秒內回應訂單是否建立成功，
  以避免使用者重複提交或放棄購買。

需求 CART-002：
  若庫存在結帳期間不足，
  系統應回傳具體缺貨品項名稱，
  使使用者可調整訂單而非遭遇不明錯誤。
```

**設計層（HOW，架構）**
```
結帳流程架構決策：

元件：CartService, InventoryService, OrderService, PaymentGateway

樂觀鎖策略：先預扣庫存 (reserve)，
  支付成功後確認 (commit)，
  支付失敗後釋放 (rollback)。

拒絕的替代方案：
  - 直接扣庫存後付款：若付款失敗需人工退庫存，不符合 CART-002
  - Saga 補償模式：複雜度過高，目前規模不值得

回應時間目標 3 秒的拆分：
  inventory reserve ≤ 100ms, payment ≤ 2000ms, order creation ≤ 500ms
```

**實作層（HOW，程式碼）**
```python
# checkout_service.py

from dataclasses import dataclass
from typing import List
from decimal import Decimal

@dataclass
class CheckoutResult:
    order_id: str | None
    success: bool
    insufficient_items: List[str]  # 缺貨品項名稱，對應 CART-002
    error_message: str | None

def checkout(cart_id: str, user_id: str) -> CheckoutResult:
    """
    實作 CART-001, CART-002。
    使用樂觀鎖：reserve → pay → commit/rollback。
    """
    items = inventory_service.reserve(cart_id)  # 預扣

    if items.insufficient:
        inventory_service.release(cart_id)
        return CheckoutResult(
            order_id=None,
            success=False,
            insufficient_items=[i.name for i in items.insufficient],
            error_message=None
        )

    payment_result = payment_gateway.charge(user_id, items.total)

    if not payment_result.success:
        inventory_service.release(cart_id)
        return CheckoutResult(
            order_id=None,
            success=False,
            insufficient_items=[],
            error_message=payment_result.decline_reason
        )

    order = order_service.create(user_id, cart_id, items)
    inventory_service.commit(cart_id)
    return CheckoutResult(
        order_id=order.id,
        success=True,
        insufficient_items=[],
        error_message=None
    )
```

這個範例有幾個地方值得注意：

1. `insufficient_items` 這個欄位可以直接追溯到規格 CART-002——規格改，你知道要改這裡。
2. 設計層的「樂觀鎖」決策反映在 `reserve → pay → commit/rollback` 的呼叫順序上，但設計層的「為何不用 Saga」沒有辦法從程式碼裡讀出來——這個意圖只存在設計文件裡。
3. 3 秒的回應時間目標（CART-001）**完全不見了**——沒有任何一行程式碼表達這個約束。它必須靠效能測試或監控來驗證。

第三點說明了一個重要的現實：**實作無法完整攜帶規格的全部資訊**。這也是 Brooks 說程式碼是「有損投影」的佐證。

## 踩雷集錦

**錯誤直覺 1：「設計文件就是 UML 圖」**

正確認識：UML 只是設計的其中一種表達形式，且是最容易腐化的那種。設計的核心是「決策與取捨的紀錄」，不是「圖表的格式」。一份用 Markdown 寫的 ADR（架構決策記錄，Architecture Decision Record）通常比一張兩年沒更新的 UML 類別圖有用得多。

**錯誤直覺 2：「規格越詳細越好」**

正確認識：規格的密度要匹配任務的複雜度。過度詳細的規格有幾個問題：(a) 它傾向於把設計決策混入規格層；(b) LLM 遵從率隨著規格長度增加而下降（Addy Osmani，2026 年 1 月指出「你堆越多指令，模型遵從每一條的表現就越差」）；(c) 維護成本呈指數增長。匹配複雜度，不要一律求最詳盡。

**錯誤直覺 3：「程式碼是真相，文件是輔助說明」**

正確認識：程式碼是真相，但是「行為的真相」，不是「意圖的真相」。你可以從程式碼知道系統做了什麼，但通常無法知道「為什麼這樣做而非那樣做」、「什麼情況下這個邏輯可以刪掉」、「這個 hardcoded 數字 30 代表什麼業務含義」。缺少規格和設計文件，程式碼就是一份沒有說明書的機器，只有原作者（如果還記得的話）能安全操作。

**錯誤直覺 4：「把規格當 SSOT 就不會腐化」**

正確認識：SSOT 是一種原則，不是自動化機制。把規格設為 SSOT，只是說「這裡是權威」，但如果團隊沒有流程確保每次程式碼改動都同步更新規格，腐化照樣發生——只是方向反過來（程式碼是新的，規格是舊的）。Kinde 工程部落格（2025 年 8 月）定義「規格漂移（Spec Drift）」為「程式碼行為不再匹配文件或設計規格」，並指出後果是「開發者必須讀原始碼，不能信任文件，認知負擔大幅提升」。

**錯誤直覺 5：「三層分清楚是學術練習，實務上沒差」**

正確認識：在純人類開發的小團隊裡，三層混在一起的成本可以被「問一下隔壁工程師」吸收掉。但當 AI agent 進來後，agent 沒有辦法問人——它只能讀它拿到的那份文件。如果你給的是「設計文件」但 agent 需要的是「規格」，它會把設計約束當成業務需求，把技術選型的考量當成不可改變的事實。錯誤的層次餵給 agent，得到錯誤的實作。

## 進階延伸：為什麼設計層的「為何不選 X」特別重要

Michael Nygard 在他的 ADR（架構決策記錄）提案（大約 2011 年開始普及）裡強調，決策文件最有價值的部分是「後果（Consequences）」段——包括不選某方案的原因。這與 Brooks 的 Essence 概念直接相連：設計過程中捨棄的路徑，本身就是系統的一部分意圖。

當 LLM 在後續幫你改架構時，如果它讀到「我們試過 Saga 但當時認為複雜度不值得」，它就知道這個決定可以被質疑；如果沒有這段記錄，它只能從現有程式碼猜測「你們沒有用 Saga，一定有原因，我就維持現狀」——或者毫無歷史感地把架構改掉。

ADR 的標準格式（如 Nygard 提議的）通常包含：狀態、脈絡、決策、後果。把這四個欄位填好，你就在設計層做了一份對人與 AI 都有用的記錄。

> 如果你想在後面看到三層結合 DDD 通用語言的應用，可以跳到 [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md)，那裡會把規格層的術語和領域建模明確連結起來。

## 動手練習

取一個你最熟悉的功能（可以是工作中的，也可以是開源專案的），做以下三件事：

1. 找出（或寫出）這個功能的規格是什麼——只包含 WHAT 和 WHY，去掉所有 HOW。如果你發現你不知道 WHY，那就是問題所在。
2. 找出（或寫出）當初的設計決策是什麼——特別是「我們考慮過哪些替代方案，為什麼不選」。
3. 把現有的程式碼和你寫出的規格對照：哪些規格無法從程式碼裡直接讀出來？

這個練習是練習 A 的前置熱身。練習 A 會帶你把一份真實的模糊需求，系統性地拆解成各層的具體產物。

## 本章重點整理

- **規格（Spec）**回答 WHAT + WHY，設計（Design）回答架構層 HOW，實作（Implementation）回答語言/工具層 HOW。三層混淆是最常見、代價最高的錯誤之一。
- **Brooks（1986）**：規格與設計是 Essence（本質難度），大部分實作是 Accident（偶發難度）。AI 攻克了 Accident，把瓶頸推到了 Essence 上。
- **程式碼是規格的有損投影**：你無法從程式碼完整復原意圖——這是 SSOT 必須放在規格層而非程式碼層的根本原因。
- **文件腐化**是三層分開存在的必然後果；SSOT 是原則，紀律才是機制。規格設為 SSOT 不等於自動同步。
- **規格層混入設計決策**（最常見的錯誤）會讓日後的技術重構變成「違反需求」，增加阻力。
- **設計層的「為何不選 X」**比最終選擇本身更有長期保存價值——對未來的人和 AI agent 都是如此。

## 自我檢核

不要翻書，用自己的話回答：

- [ ] 用一句話說清楚規格與設計的邊界在哪裡——可以舉例。
- [ ] Brooks 說的 Essence 和 Accident 各包含什麼？如果被問到「AI 怎麼改變這個框架」，你會怎麼回答？
- [ ] 「程式碼是有損投影」這句話什麼意思？可以舉一個具體例子說明哪些資訊在投影過程中消失了。
- [ ] 文件腐化是怎麼發生的？SSOT 原則能解決它嗎？為什麼還不夠？
- [ ] 如果有人把「用 PostgreSQL 儲存使用者 session」寫進了規格文件，這個問題出在哪一層？應該怎麼修？
- [ ] 面試官問你「為什麼要把規格、設計、實作分開寫」，你的答案是什麼？

## 延伸閱讀

**No Silver Bullet — Essence and Accident in Software Engineering**
Fred Brooks Jr.（1986 IFIP 演講，IEEE Computer 1987 年 4 月重印）
<https://www.cin.ufpe.br/~phmb/ip/MaterialDeEnsino/BrooksNoSilverBullet.html>
從「Essence」段開始讀。這篇文章把「為什麼仔細決定要做什麼比如何做更難」說得比任何 SDD 文章都清楚。和本章直接相連。

**The New Code — Sean Grove（社群轉錄稿）**
AI Engineer World's Fair 2025；Grove 當時在 OpenAI
<https://lawwu.github.io/transcripts/8rABwKRsec4.html>
讀「code as lossy projection」和「structured communication is the bottleneck」這兩段。這份是社群轉錄稿而非官方稿，引用時應標注。和本章「有損投影」的討論直接相連。

**github/spec-kit — spec-driven.md（官方方法論文件）**
GitHub（開源，2025 年 9 月）
<https://github.com/github/spec-kit/blob/main/spec-driven.md>
讀「The specification becomes the primary artifact」和「lingua franca」兩段。這是 SSOT 的實際操作應用，看 Spec Kit 如何把三層對應到 Constitution / Spec / Plan / Tasks 四份產物。

**Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl**
Birgitta Böckeler（Thoughtworks），2025 年 10 月 15 日，發表於 martinfowler.com
<https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html>
讀 spec-first / spec-anchored / spec-as-source 的三種分類，以及「MDD 的影子」那一段的批評。Böckeler 提供了最清晰的工具分類框架，也是最值得認真對待的懷疑論之一。

**Everyone cites that 'bugs are 100x more expensive' research, but the study might not even exist**
Tim Anderson，The Register，2021 年 7 月 22 日（報導 Laurent Bossavit 與 Hillel Wayne 的調查）
<https://www.theregister.com/2021/07/22/bugs_expense_bs/>
讀 Hillel Wayne 的引言段。了解為什麼「規格錯誤修起來最貴」的方向是對的，但那個 1:100 的具體數字是無法查證的民間傳說。和 [Ch 6 變更成本曲線](./06-cost-of-change-curve.md) 一起讀效果最好。

下一章我們要挖深規格層本身的難題：即使你知道規格要放什麼，自然語言本身就是一個充滿陷阱的表達媒介，有八種系統性的病症讓需求在寫下來的那一刻就開始出錯。

→ [練習 A 需求考古學——把模糊需求拆成各層產物](./practice-a-requirements-archaeology.md)
