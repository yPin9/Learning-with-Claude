# Ch 36 — 領域模型作為 spec 的骨架

> **目標**：把協作得出的領域模型轉成機器可讀契約（OpenAPI / JSON Schema），讓規格有可驗證的骨架而非一堆散文；理解這個轉換的完整流程、取捨，以及踩雷的位置。

## 問題的起點：散文 spec 對 LLM 的毒性

回想 [Ch 8 為什麼需求這麼難](./08-why-requirements-hard.md) 裡自然語言的八種病。現在把那個問題乘以十：你把一份充滿「系統應該支援使用者管理食譜」的 Markdown 丟給 LLM，它會做什麼？它會猜。不是因為它笨，而是因為那句話裡每個詞都有多個意思，LLM 的本質是在模糊空間裡取機率最高的那條路。

這就是「散文 spec 的毒性」：它讓 LLM 的不確定性有地方落腳，而那個不確定性會沿著生成鏈放大，最後落在你讀不太懂為什麼 bug 就在那裡的程式碼裡。

領域模型是解藥的第一步。把「管理食譜」拆解成：

```
Recipe（食譜）
  ├─ RecipeId       # 識別子，不可更改
  ├─ Title          # 不可空白
  ├─ Ingredients[]  # 至少一個
  └─ DietTag[]      # 枚舉：Vegan / Vegetarian / GlutenFree

ShoppingList（購物清單）
  ├─ ShoppingListId
  ├─ OwnerId        # → User
  └─ Items[]        # → Ingredient ref
```

這些名稱就是通用語言（Ubiquitous Language）。把它們進一步編碼成 OpenAPI Schema，LLM 就從猜測「食譜有哪些欄位」，變成照著契約生成，可驗證、可測試、可 diff。

## 歷史脈絡：在這之前人們怎麼做

**2000 年代中期以前**：需求文件是 Word 檔，設計師手畫 ER 圖，開發人員各自解讀，出入在測試階段才浮現。

**2003–2013 年**：Eric Evans 的領域驅動設計（Domain-Driven Design, DDD）提出以領域模型為中心的開發方式。模型存在哪裡？有時是 UML 圖，有時是白板照片，最好的情況是直接體現在程式碼的類別命名上。但「模型在程式碼裡」有個隱患：LLM 在你有程式碼之前就要開始工作了。

**2014–2023 年**：OpenAPI（前身 Swagger）成為 REST API 的事實標準契約，JSON Schema 成為資料結構描述的通用格式。這兩個格式都是機器可讀的，有工具鏈可以做驗證、測試、文件生成。

**2025–2026 年**：規格驅動開發（Spec-Driven Development, SDD）的工具浪潮（GitHub Spec Kit、AWS Kiro、Tessl）讓一個問題變得迫切：你的 spec 裡的領域模型，和機器可讀的 OpenAPI/JSON Schema，是同一份還是兩份？如果是兩份，誰是主？

codecentric 的建築師 Annegret Junker 在 2026 年 3 月的文章直接示範了答案：先做 Domain Storytelling（領域故事講述）和 Event Storming，再把得出的模型編碼成 OpenAPI，LLM 生成的規格從 3 個 schema 增長到 9 個，並且抓到了一條「食譜自評分」的業務規則，那條規則在直接提示 LLM 時完全沒被發現。

## 管道全景

協作建模到可驗證 spec 的完整管道長這樣：

```
[工作坊]              [建模層]             [機器可讀層]
Domain Storytelling
       +          →  領域模型          →   OpenAPI 3.x
  Event Storming      (Entity / VO /        (schemas +
                       Aggregate /           paths)
                       Domain Event)
                          │                    │
                          ↓                    ↓
                     通用語言辭彙表          JSON Schema
                     (Ubiquitous            ($defs / 共用
                      Language)              定義)
```

最終的 OpenAPI 規格不是「先做 API 再補文件」的產物，而是「協作建模 → 結構化語言 → 機器契約」這條線的輸出。

> 如果你對 Event Storming 工作坊的細節還不熟，先回看 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

## 具體案例：食譜平台三輪迭代

我們用 Junker 的 Larder 食譜平台案例，重現三個版本的 spec 差距，讓數字說話。

### v1：直接提示，沒有建模

Prompt 丟給 LLM：「幫我設計一個食譜管理 API」。

LLM 產出 schema（大致）：

```yaml
# openapi: 3.1.0
components:
  schemas:
    Recipe:
      type: object
      properties:
        id:
          type: string
        title:
          type: string
        ingredients:
          type: array
          items:
            type: string     # ← 注意：只是字串
        instructions:
          type: string
```

結果：3 個 schema（Recipe、User、ShoppingList），食材是字串陣列，沒有「飲食標籤」、沒有自評分規則。

### v2：Event Storming 後再提示

Event Storming 工作坊貼出的橘色便利貼（領域事件）：

```
RecipeCreated / RecipePublished / RecipeRatedBySelf /
IngredientAdded / ShoppingListGenerated / DietTagApplied
```

工作坊也釐清了一條業務規則：食譜作者可以對自己的食譜自評，但評分只對未公開食譜有效，讓作者在發布前微調。

把這個業務規則寫進 spec 提示：

```yaml
components:
  schemas:
    Recipe:
      type: object
      required: [id, title, ingredients]
      properties:
        id:
          type: string
          format: uuid
        title:
          type: string
          minLength: 1
        ingredients:
          type: array
          minItems: 1      # ← 業務規則：至少一種食材
          items:
            $ref: '#/components/schemas/Ingredient'
        dietTags:
          type: array
          items:
            $ref: '#/components/schemas/DietTag'
        selfRating:
          $ref: '#/components/schemas/SelfRating'
          description: "只在 status=draft 時有效"

    Ingredient:
      type: object
      required: [name, quantity, unit]
      properties:
        name:
          type: string
        quantity:
          type: number
          minimum: 0
          exclusiveMinimum: true    # 不能是 0
        unit:
          type: string
          enum: [g, kg, ml, l, pcs, tbsp, tsp]

    DietTag:
      type: string
      enum: [Vegan, Vegetarian, GlutenFree, DairyFree, NutFree]

    SelfRating:
      type: object
      required: [score, ratedAt]
      properties:
        score:
          type: integer
          minimum: 1
          maximum: 5
        ratedAt:
          type: string
          format: date-time
```

結果：9 個 schema，`selfRating` 約束在 draft 狀態，食材有獨立型別，飲食標籤是有界枚舉。

### 差距在哪裡

| 指標 | v1（直接提示） | v2（Event Storming 後） |
|---|---|---|
| schema 數量 | 3 | 9 |
| 食材結構 | `string[]` | `Ingredient` 物件含單位枚舉 |
| 飲食標籤 | 無 | `DietTag` 枚舉 5 項 |
| 自評分業務規則 | 無 | `SelfRating` + draft 約束 |
| 可由 OpenAPI validator 驗證 | 部分 | 全部 |
| LLM 對「食材是什麼」的猜測空間 | 完全開放 | 被 schema 關閉 |

Junker 的結論直接而精確：「你給 LLM 的語言品質，直接決定它產出的品質。」

## 把領域模型對應到 OpenAPI

### Entity → Schema + required

領域實體（Entity）有識別子，有生命週期：

```yaml
# DDD Entity → OpenAPI schema
components:
  schemas:
    Recipe:
      type: object
      required: [id, title, status]    # 不可省略的屬性
      properties:
        id:
          type: string
          format: uuid
          readOnly: true               # 不允許客戶端設定 id
        title:
          type: string
          minLength: 1
        status:
          type: string
          enum: [draft, published, archived]
```

### Value Object → inline 或 $def

值物件（Value Object）沒有識別子，靠值相等。可以 inline，但跨 schema 重複用的應該放 `$defs`（JSON Schema 術語）或 `components/schemas`（OpenAPI 術語）：

```yaml
components:
  schemas:
    Money:                      # Value Object
      type: object
      required: [amount, currency]
      properties:
        amount:
          type: number
          minimum: 0
        currency:
          type: string
          pattern: '^[A-Z]{3}$'   # ISO 4217，正則約束
      additionalProperties: false  # VO 是封閉的
```

### Aggregate 邊界 → 一個 OpenAPI spec 檔

這是 Junker 案例裡最關鍵的架構決策：

> 「Bounded contexts 產生了 3 個鬆耦合的 OpenAPI spec，它們共享 ID，而非共享 schema。」

意思是：食譜服務、購物清單服務、使用者服務各有一個 `openapi.yaml`，跨服務只傳遞 ID（如 `recipeId: string format: uuid`），不把對方的完整 schema import 進來。

```
recipe-service/openapi.yaml         ← Aggregate: Recipe
shopping-list-service/openapi.yaml  ← Aggregate: ShoppingList
user-service/openapi.yaml           ← Aggregate: User

跨服務引用：
shopping-list-service 只知道 recipeId (uuid)，
不 import Recipe schema 本體。
```

這直接對應 DDD 中 Bounded Context 的核心原則：模型在邊界內為真，跨邊界用輕量的 Anti-Corruption Layer（防腐層）。

> 如果你對 Bounded Context 的定義和邊界劃法還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md) 和 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)。

### Domain Event → Async API 或 Webhook schema

領域事件（Domain Event）不是 REST endpoint，是非同步訊息。OpenAPI 3.1 的 webhooks 欄位，或獨立的 AsyncAPI 規格，是對應位置：

```yaml
# 在 openapi.yaml 的頂層加入 webhooks
webhooks:
  recipePublished:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [eventType, recipeId, publishedAt]
              properties:
                eventType:
                  type: string
                  const: "RecipePublished"    # const 鎖定值
                recipeId:
                  type: string
                  format: uuid
                publishedAt:
                  type: string
                  format: date-time
```

`const` 是 JSON Schema Draft 7+ 的關鍵字，用來表達「這個欄位只能是這個值」，對領域事件類型判別很有用。

## 從 spec 回頭餵給 LLM

OpenAPI spec 寫好之後，它變成 LLM 的語境輸入，不是靜態文件。給 LLM 的提示結構大概是：

```
[spec 前置]
你是 recipe-service 的實作者。
以下是 OpenAPI 3.1 規格，這是唯一的真相來源。

<openapi.yaml 的內容貼在這裡>

[任務指令]
請根據上述規格，實作 POST /recipes endpoint 的 handler，
使用 Python + FastAPI，並加入 Pydantic 驗證。
```

LLM 看到的 `Ingredient` 是有 `quantity > 0` 約束的物件，而不是字串。它產生的 Pydantic 模型大機率會忠實反映這個約束：

```python
from pydantic import BaseModel, Field
from enum import Enum
from typing import List

class Unit(str, Enum):
    g = "g"
    kg = "kg"
    ml = "ml"
    l = "l"
    pcs = "pcs"
    tbsp = "tbsp"
    tsp = "tsp"

class Ingredient(BaseModel):
    name: str
    quantity: float = Field(gt=0)   # exclusiveMinimum: true → gt=0
    unit: Unit

class DietTag(str, Enum):
    Vegan = "Vegan"
    Vegetarian = "Vegetarian"
    GlutenFree = "GlutenFree"
    DairyFree = "DairyFree"
    NutFree = "NutFree"

class Recipe(BaseModel):
    title: str = Field(min_length=1)
    ingredients: List[Ingredient] = Field(min_length=1)
    diet_tags: List[DietTag] = []
```

這個映射是可以驗證的，跑一下 `pydantic` 的 `.model_json_schema()` 輸出，和原始 OpenAPI schema 對比，差異清晰可見。

## 可驗證性：spec 的「骨架」到底硬在哪裡

「骨架」的比喻意味著它要承重。承重的方式是工具驗證：

| 驗證層 | 工具 | 驗證什麼 |
|---|---|---|
| Schema 格式本身 | `openapi-spec-validator` | YAML 是否合法 OpenAPI 3.x |
| Request/Response | `schemathesis` | 打真實 API，測回傳是否符合 schema |
| Code 和 schema 一致性 | `datamodel-code-generator` | schema → Pydantic model 的差異 |
| 跨服務 contract | `pact` | Consumer/Provider 雙向 contract test |

安裝與快速驗證（需要 Python 3.11+）：

```bash
pip install openapi-spec-validator schemathesis

# 驗證 schema 格式
openapi-spec-validator recipe-service/openapi.yaml

# 對跑起來的 API 做 schema fuzz test
schemathesis run recipe-service/openapi.yaml \
  --url http://localhost:8000 \
  --checks all
```

`schemathesis` 會自動從 OpenAPI 生成測試案例，覆蓋 edge case（空陣列、負數、enum 外的值），並且驗證 API 回傳是否符合 response schema。這是「骨架可驗證」最直接的體現。

## 踩雷集錦

### 雷 1：把 OpenAPI 當 ER 圖，把每個資料庫欄位都放進去

**錯誤直覺**：領域模型越詳盡越好，把資料庫的 `created_at`、`updated_at`、`deleted_at`、`version` 都塞進 API schema。

**正確認識**：OpenAPI schema 描述的是 API 契約（外部可見介面），不是資料庫結構。`created_at` 可能是 `readOnly: true` 的 response 欄位；`deleted_at` 在軟刪除模型裡根本不應該暴露給外部。把儲存層的細節滲漏到 API schema，是把兩個不同的模型混在一起，Bounded Context 的邊界從裡面破掉。

### 雷 2：一個 OpenAPI 檔 import 另一個服務的 schema

**錯誤直覺**：`ShoppingList` 需要知道 `Recipe` 的完整資訊，所以在 `shopping-list-service/openapi.yaml` 裡用 `$ref` 指向 `recipe-service/openapi.yaml#/components/schemas/Recipe`。

**正確認識**：這把兩個服務的 schema 耦合死了。`Recipe` 一旦改動（加欄位、改格式），`ShoppingList` 的 spec 就需要跟著更新。正確做法是跨服務只傳 ID（`recipeId: string, format: uuid`），`ShoppingList` 服務需要 Recipe 細節時自己去查，或透過 Anti-Corruption Layer 轉換。Junker 的三份規格「共享 ID，不共享 schema」就是這個原則的具體落地。

### 雷 3：先寫 code，再反向生成 OpenAPI，以為這樣就有「spec」

**錯誤直覺**：FastAPI 可以自動生成 `/openapi.json`，那就讓程式碼決定 schema，spec 自動跟上。

**正確認識**：反向生成的 OpenAPI 是程式碼的影子，不是領域模型的表達。如果程式碼忘記了 `minimum: 1` 的約束，生成出來的 schema 也不會有。更根本的問題是：這讓程式碼成為單一真相來源，把 SDD 的核心倒置。「code 是 spec 的實作細節，不是反過來」是 SDD 的根本命題。反向生成可以拿來做驗證（比對手寫 spec 和生成 spec 的差異），但不能拿來做唯一來源。

### 雷 4：在 schema 裡用 description 代替 enum 約束

**錯誤直覺**：`dietTags` 是字串，description 裡寫「可能的值：Vegan, Vegetarian, GlutenFree」，LLM 應該看得懂。

**正確認識**：description 是給人讀的，enum 是給機器讀的。`schemathesis` 和 Pydantic 會把 `enum: [Vegan, Vegetarian, GlutenFree]` 轉成驗證約束；`description` 裡的自然語言它們看不到。LLM 也是機器，在 zero-shot 情況下它更傾向遵守 schema 的 enum 列表，而不是 description 裡的自然語言描述。把約束放進 schema 結構而非散文裡，才是「骨架硬」的意思。

### 雷 5：以為一次工作坊就能把領域模型做完

**錯誤直覺**：跑完 Event Storming，把便利貼翻譯成 OpenAPI，然後這份 spec 就穩定了。

**正確認識**：Evans 的原著就明確說，領域模型是透過實作和反覆協作浮現的，不是一次訪談就能鎖住的。Daniel Westheide（INNOQ）把這個問題說得很直：「SDD 假設發現過程能在前期結構化訪談中完成，而 DDD（依 Evans 的理解）把發現視為透過實作的迭代過程。」第一份 OpenAPI 是起點，不是終點。Spec 漂移（spec drift）是真實的威脅，我們在 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 裡專門討論。

## 對比取捨

| 方式 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 純散文 spec | 寫起來快，容易修改 | LLM 猜測空間大，無法機器驗證 | 早期發散探索 |
| OpenAPI（手寫）| 機器可讀，有工具鏈，可做 contract test | 需要有建模基礎才不會變 ER 圖 | 有協作建模產物後 |
| 程式碼反向生成 OpenAPI | 零額外工作，永遠和程式碼一致 | 散文 spec 完全缺失，SDD 倒置 | 遺留系統文件化 |
| JSON Schema（獨立）| 比 OpenAPI 輕量，不綁 HTTP | 缺少 paths/operations，不適合 REST API 完整描述 | 資料格式驗證、config schema |
| AsyncAPI | 適合事件/訊息驅動架構 | 工具鏈比 OpenAPI 小，學習成本較高 | Domain Event 的規格化 |

不存在「最好的格式」——存在「最符合你領域模型結構的格式」。大多數系統混用：REST 介面用 OpenAPI，內部事件用 AsyncAPI 或 JSON Schema，config 用 JSON Schema。

## 進階延伸

### 1. JSON Schema `$defs` 和 OpenAPI `$ref` 的差異

OpenAPI 3.0 使用 JSON Schema Draft 4 的子集，`$ref` 只能引用 `components/schemas`。OpenAPI 3.1 完全對齊 JSON Schema Draft 2020-12，支援 `$defs`（local definitions），兩者可以混用。如果你在寫複雜的嵌套 schema，這個版本差異會影響工具支援度。（版本依存，查證日期 2026-06-30）

### 2. 用 CUE 或 TypeSpec 替代手寫 YAML

CUE（cuelang.org）和 Microsoft TypeSpec 都是「schema 的 schema」：用更簡潔的語言描述結構，然後生成 OpenAPI YAML。TypeSpec 在 2025–2026 年被 Microsoft 廣泛採用於其 REST API 規格（API Management、Azure SDK）。如果你的 schema 有大量重複結構，值得評估。

### 3. 形式化約束：超出 JSON Schema 的邊界

JSON Schema 能表達「score 在 1–5」，但無法表達「selfRating 只在 status=draft 時合法」這種跨欄位業務規則。這類規則只能在 description 裡寫散文，或者挪到更強的形式化語言（TLA+、Alloy）裡描述。

> 如果你對 TLA+/Alloy 還不熟，先回看 [Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)。

這是 OpenAPI 作為「spec 骨架」的真實邊界：它能約束結構和簡單值域，但業務不變量（invariants）需要另外描述。好的 spec 把可機器驗證的部分放進 schema，把不可機器驗證的業務規則放進清晰的 description 或獨立的規格文件，標明驗證責任在哪一層。

## 動手練習

這個練習延伸自 [練習 C：對電商情境跑一場 Event Storming](./practice-c-event-storming.md)，假設你已經有 Event Storming 的便利貼產物。

**情境**：線上書店，有三個核心 Aggregate：`Catalog`（書目）、`Order`（訂單）、`Inventory`（庫存）。

1. **識別 Value Object**：`Price`（金額 + 幣別）、`ISBN`（13 位數字，正則 `^\d{13}$`）、`Address`（寄送地址）。用 JSON Schema `additionalProperties: false` 寫出這三個 Value Object 的 schema，確保它們是封閉的。

2. **劃定 Aggregate 邊界**：`Order` 只能包含 `bookId`（UUID），不能嵌套 `Book` 的完整 schema。寫出 `Order` 的 schema，確保跨 Bounded Context 只傳 ID。

3. **把一條業務規則編碼進 schema**：`Order` 的 `items` 至少一本書，且每本書的 `quantity` 必須大於 0。用 `minItems` 和 `minimum`/`exclusiveMinimum` 實現。

4. **驗證**：安裝 `openapi-spec-validator`，把你的三個 OpenAPI 檔驗證過一遍，修到沒有錯誤為止。

5. **邊界案例**：試著在 `Order` 的 `items` 裡放一個空陣列，用 `schemathesis` 或手動 `curl` 打你自己實作的 endpoint（如果有的話），確認 API 返回 400 而非 200。

## 本章重點整理

- 領域模型是 spec 的骨架，不是文件的附錄。把協作建模的產物（Entity、Value Object、Aggregate、Domain Event）直接對應到 OpenAPI schema，把散文 spec 的模糊空間壓縮掉。
- 協作建模在先：Junker 的案例數字是具體的——Event Storming 讓 schema 從 3 個增長到 9 個，並且抓到純提示 LLM 時遺漏的業務規則。
- Bounded Context = 一份 OpenAPI 檔；跨 Context 只共享 ID，不共享 schema 本體。
- `enum`、`minimum`、`minItems`、`pattern`、`const`、`additionalProperties: false` 是把業務規則從散文遷移到機器可讀的核心 JSON Schema 關鍵字。
- OpenAPI 有邊界：跨欄位業務不變量（如「selfRating 只在 draft 時合法」）需要另外在 description 或形式化規格中描述。
- Spec 是起點，不是終點。Evans 的核心洞見是「領域模型透過迭代浮現」；第一份 OpenAPI 是那個浮現過程的快照，不是終態。

## 自我檢核

- [ ] 用自己的話解釋：為什麼散文 spec 對 LLM 有毒性？OpenAPI schema 的哪些特性可以壓縮 LLM 的猜測空間？（假設面試官問你，你的答案是什麼）
- [ ] 在 Junker 的案例裡，v1 和 v2 之間 schema 從 3 個增長到 9 個，多出來的 6 個 schema 是哪些類型的建模產物帶來的？
- [ ] 你能說出 DDD Entity 和 Value Object 分別對應到 OpenAPI 的哪種結構，以及為什麼 Value Object 應該用 `additionalProperties: false`？
- [ ] 為什麼「跨 Bounded Context 只共享 ID，不共享 schema」？如果違反這條原則，長期會出現什麼問題？
- [ ] `description` 裡的約束和 `enum` / `minimum` 的約束，對 `schemathesis` 的行為有什麼不同影響？對 Pydantic 呢？
- [ ] OpenAPI schema 無法表達的業務不變量，你會把它放在哪裡？這個決策涉及哪個章節的概念？

## 延伸閱讀

- **From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker（codecentric，2026 年 3 月）
  - 網址：https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need
  - 本章直接引用的主要案例來源。從 Larder 食譜平台的三輪迭代讀起，重點是 v1 vs v2 的 schema 數量對比，和「自評分」業務規則被工作坊而非純提示發現的具體過程。這是本主題最強的一手實踐資料。

- **Bounded Context（Bliki）** — Martin Fowler（2014 年 1 月）
  - 網址：https://martinfowler.com/bliki/BoundedContext.html
  - 「meter 的多義性在對話中可以被平滑帶過，但在電腦的精確世界裡不行」——這句話正好描述了 LLM 對多義詞的處理方式。讀多義詞段落，理解為什麼一個 OpenAPI 檔對應一個 Bounded Context 不只是架構品味，而是消除同名異義的唯一手段。

- **Ubiquitous Language（Bliki）** — Martin Fowler（引用 Eric Evans，2006 年 10 月）
  - 網址：https://martinfowler.com/bliki/UbiquitousLanguage.html
  - OpenAPI schema 的欄位名就是通用語言的編碼。這篇是通用語言的規範定義，「軟體無法應對模糊性」的根本論據在這裡，讀完後你會明白為什麼 schema 的欄位命名不是工程細節，而是領域溝通。

- **Spec-Driven Development | Technology Radar Vol 34** — Thoughtworks（2025 年 11 月）
  - 網址：https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development
  - 「評估（Assess）」環的定位和「輸出難以審閱」的警告，是本章方法論的誠實錨點。讀 ring rationale 那段，對照你自己設計的 OpenAPI 問自己：你的 schema 是否也有「難以審閱」的問題？

- **Spec-Driven Development: From Code to Contract in the Age of AI Coding Assistants** — Deepak Babu Piskala（arXiv 2602.00180，2026 年 1 月）
  - 網址：https://arxiv.org/html/2602.00180v1
  - 提供 SDD 最完整的工具分類表（Table I）和 DDD/通用語言對齊的明確引文。注意：論文引用的「50% 錯誤減少」數字所指的具體研究未在論文中列出，作為個人預印本應謹慎引用，以官方同行評審版本為準。

- **Agentic Code Workflows with Nick Tune** — Nick Tune（Techworld with Milan，2026 年 3 月）
  - 網址：https://newsletter.techworld-with-milan.com/p/agentic-code-workflows-with-nick
  - Tune 展示了如何用 `dependency-cruiser` 在 lint 層面強制 Bounded Context 邊界，讓「agent 不能跨 context 修改」成為可自動驗證的規則，而不是 prompt 裡的口頭約定。讀 deterministic enforcement 那段，和本章「跨 Context 只共享 ID」的原則配合閱讀。

- **Domain-Driven Design（Blue Book）** — Eric Evans（Addison-Wesley，2003 年）
  - 第四章（Isolating the Domain）和第五章（A Model Expressed in Software）。Entity vs Value Object 的定義和 Aggregate 根（Aggregate Root）的不變量，是本章 OpenAPI 對應的理論基礎。如果你在 schema 設計上反覆遇到「要不要用 $ref」的問題，答案幾乎都能在 Evans 對 Aggregate 邊界的討論裡找到線索。

---

下一章，我們把這份由領域模型驅動的 OpenAPI spec 放進一條實際的 prompting pipeline，復現 codecentric 案例裡「建模在先、生成在後」的完整操作流程。

→ [Ch 37 Modeling-first prompting：復現 codecentric 的 pipeline](./37-modeling-first-prompting.md)
