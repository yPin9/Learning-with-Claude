# Ch 37 — Modeling-first prompting：復現 codecentric 的 pipeline

> **目標**：復現 Annegret Junker（codecentric）的 Domain Storytelling → EventStorming → OpenAPI → LLM 流程於一個可操作的小例子，理解它比直接 prompt 好在哪、貴在哪，並對每個決策點做出有根據的取捨判斷。
> **環境**：本章範例使用文字工具（Domain Storytelling 的 DOML 格式、EventStorming 卡片文字表示法）和任何支援長 system prompt 的 LLM。無需 Qlerify 帳號也能完成練習。（查證日期 2026-06-30）

---

## 心智圖像：三段過濾器

在動手之前，先確認一個比喻：直接 prompt LLM 寫 API，像是用自來水管直接沖掉沙礫——水壓夠強，但沙礫全留在管路裡，遲早堵塞。modeling-first 的作法是先讓水通過三層過濾：

```
模糊需求（散文/會議紀錄/腦子裡的想法）
          │
          ▼
┌─────────────────────────┐
│  Domain Storytelling    │  → 誰在做什麼、用什麼、目的為何
│  （Actor-Work Object    │    產出：DOML 故事（結構化自然語言）
│   語言，WIP 記法）      │
└─────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│  EventStorming          │  → 領域事件、指令、Aggregate、邊界
│  （橘色/紫色/黃色貼紙） │    產出：事件時間軸 + Bounded Context 草圖
└─────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│  Domain Model 編碼      │  → 術語表 + OpenAPI schema
└─────────────────────────┘
          │
          ▼
   LLM（獲得高信噪比的 context）
          │
          ▼
   實作程式碼（較少幻覺、較少遺漏）
```

每層過濾都做一件事：把隱性知識變成顯性文字，讓後一層少猜一次。

---

## 1. 歷史脈絡：人們以前怎麼做、為何不夠好

在 LLM 進入開發流程之前，業界有兩種極端：

**極端 A：口頭需求直接給工程師。**
工程師腦補領域知識，寫出「自己理解的」API，上線後才發現和 PM 的想像差了一層。這就是為什麼 Eric Evans 在 2003 年的《Domain-Driven Design》裡要強調通用語言（Ubiquitous Language）：同一個詞在不同人嘴裡可能是完全不同的概念。

**極端 B：瀑布式 Big Design Upfront。**
寫完 200 頁需求文件，工程師照著實作，但文件和現實已經不一致。變更成本在後期急劇攀升。

2025 年以後，「直接 prompt LLM」成了第三種極端：把口頭需求貼給 GPT，叫它「幫我寫一個菜譜平台的 API」。LLM 很聰明，確實能產出能跑的程式碼——但它對「菜譜平台」的理解是從大量語料歸納的統計平均，不是你們公司特定的業務邏輯。

Annegret Junker 在 codecentric 的文章（2026-03-04）記錄了她做的三輪實驗，直接用數字說話：

- **v1（直接 prompt）**：給 LLM「一段描述菜譜平台的自然語言」，產出 OpenAPI spec，只有 **3 個 schema**。
- **v2（EventStorming 之後 prompt）**：先跑 Domain Storytelling + EventStorming，把發現的領域知識注入 prompt，產出 **9 個 schema**。多出來的包括 ShoppingList、Meal 的 enum 型別、使用者自我評分（self-rating）的 business rule——這條規則在 v1 的 prompt 裡根本沒出現，因為沒人記得說出來。
- **v3（Bounded Context 邊界確定之後）**：三個鬆耦合的 OpenAPI spec，「share IDs, not schemas」——意思是不同 context 之間只共享識別碼，不共享 schema 結構，避免跨邊界耦合。

這個實驗的結論不是「LLM 不好」，而是「LLM 的輸出品質，由它收到的 context 的信噪比決定」。

> 如果你對 Bounded Context 的概念還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

> 如果你對 EventStorming 工作坊的流程還不熟，先回看 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

---

## 2. Domain Storytelling：把口語故事結構化

### 什麼是 Domain Storytelling

Domain Storytelling 是由 Stefan Hofer 和 Henning Schwentner 開發的一種工作坊技術，用 Actor-Work Object 語言（WIP 記法）把業務流程拍成一幕一幕的「故事」。每個句子的格式是：

```
Actor [做動詞] Work Object (→ 另一個 Actor 或 系統)
```

它的優點是：

1. **領域專家能直接審閱。** 沒有 UML 符號需要學，句子就是普通的主謂賓。
2. **強迫顯現 Actor 和 Work Object。** 這兩樣東西後來會直接變成 DDD 的 Entity / Value Object 候選。
3. **可以用文字格式（DOML）來取代圖形工具**，讓 LLM 也能消化。

### 小例子：Larder 食材管理平台

我們用一個縮小版的菜譜平台——「Larder」，一個協助使用者管理冰箱食材、計畫餐點的服務——來走整個 pipeline。

**第一輪 Domain Storytelling（文字版 DOML 格式）：**

```doml
story: 計畫本週餐點
---
1. 使用者(User) 瀏覽(browses) 食譜庫(RecipeCatalog)
2. 使用者(User) 選取(selects) 食譜(Recipe) → 餐點計畫(MealPlan)
3. Larder系統(Larder) 計算(calculates) 缺少食材(MissingIngredients) 來自 餐點計畫(MealPlan)
4. 使用者(User) 加入(adds) 缺少食材(MissingIngredients) → 購物清單(ShoppingList)
5. 使用者(User) 勾選(checks off) 購物項目(ShoppingItem) 購物後(after shopping)
6. 使用者(User) 評分(rates) 食譜(Recipe) 完成後(after cooking)
```

這六句話產出了幾個立刻有意義的詞彙：`Recipe`、`MealPlan`、`MissingIngredients`、`ShoppingList`、`ShoppingItem`。注意第 6 句——「評分食譜」——這條 business rule 極有可能在口頭描述中被省略，因為大家「想當然耳」。但現在它白紙黑字出現了。

**把這份 DOML 當作通用語言（Ubiquitous Language）的詞彙表初稿**，後面所有 prompt 都要對齊這些術語。

---

## 3. EventStorming：從故事到領域事件

Domain Storytelling 告訴我們「誰做什麼」，EventStorming 告訴我們「系統在什麼時間點改變狀態」。

> 如果你對 EventStorming 的橘色/紫色/黃色貼紙分類還不清楚，回看 [Ch 21](./21-event-storming.md)。

### 文字版 EventStorming（無法進行實體工作坊時的替代品）

我們用一個簡化的文字記法模擬 EventStorming，格式如下：

```
[EVENT]       — 已發生的領域事件（橘色）
[COMMAND]     — 觸發事件的指令（藍色）
[ACTOR]       — 執行指令的人（黃色小人）
[AGGREGATE]   — 承接指令、發出事件的業務物件（黃色框）
[POLICY]      — 「當 X 發生時，系統自動做 Y」的規則（紫丁香色）
[BOUNDARY]    — 我們推測的 Bounded Context 邊界
```

**Larder 的文字版 EventStorming：**

```
--- Bounded Context: Recipe Management ---

[ACTOR]     使用者
[COMMAND]   搜尋食譜
[AGGREGATE] RecipeCatalog
[EVENT]     RecipeSearched

[ACTOR]     使用者
[COMMAND]   查看食譜詳情
[AGGREGATE] Recipe
[EVENT]     RecipeViewed

[ACTOR]     使用者
[COMMAND]   評分食譜
[AGGREGATE] Recipe
[EVENT]     RecipeRated
[POLICY]    當 RecipeRated 發生時，
            若評分 >= 4，系統自動推薦此食譜給相似偏好使用者

--- Bounded Context: Meal Planning ---

[ACTOR]     使用者
[COMMAND]   新增食譜到本週計畫
[AGGREGATE] MealPlan
[EVENT]     RecipeAddedToMealPlan

[POLICY]    當 RecipeAddedToMealPlan 發生時，
            自動計算缺少食材並更新 ShoppingList

[AGGREGATE] ShoppingList
[EVENT]     MissingIngredientsCalculated

--- Bounded Context: Shopping ---

[ACTOR]     使用者
[COMMAND]   勾選購物項目
[AGGREGATE] ShoppingList
[EVENT]     ShoppingItemChecked

[POLICY]    當所有 ShoppingItem 都 Checked 時，
            標記 ShoppingList 為 Completed
[EVENT]     ShoppingListCompleted
```

### EventStorming 揭示了什麼

這個過程揭示了三件在直接 prompt 裡不會出現的東西：

1. **自我評分的業務規則**：「評分 >= 4 時自動推薦」，這是隱性的 domain policy，現在變成了 `[POLICY]` 節點。
2. **三個 Bounded Context 的自然分界**：Recipe Management / Meal Planning / Shopping。這三個邊界後來會產出三個鬆耦合的 OpenAPI spec。
3. **MealPlan 和 ShoppingList 之間的自動連結**：`RecipeAddedToMealPlan` 觸發 `MissingIngredientsCalculated`，這是一條跨 Context 的 Domain Event 流，必須在 OpenAPI 設計中明確建模。

---

## 4. 從建模產物到 OpenAPI：把術語表注入 LLM

### 組裝 System Prompt

現在我們把兩個建模產物組裝成 LLM 的 system prompt。關鍵設計原則：**不是告訴 LLM「請幫我設計 API」，而是告訴 LLM「你已經知道了以下 domain model，請依據它生成 OpenAPI」**。

```text
# SYSTEM PROMPT — Larder API Spec Generator

## Ubiquitous Language（通用語言術語表）
本專案使用以下精確術語，AI 生成的所有程式碼、schema 名稱、欄位名稱
必須與此對齊，不得自行創造同義詞：

| 術語 (zh)    | 術語 (en)         | 說明                                 |
|--------------|-------------------|--------------------------------------|
| 食譜         | Recipe            | 含步驟與食材清單的烹飪指引           |
| 食材         | Ingredient        | 食譜中的原料，有名稱與單位           |
| 餐點計畫     | MealPlan          | 使用者本週預計烹飪的食譜集合         |
| 缺少食材     | MissingIngredients| 用戶已有食材與 MealPlan 需求的差集   |
| 購物清單     | ShoppingList      | 本次採購所需品項                     |
| 購物項目     | ShoppingItem      | ShoppingList 中的單一採購項目        |
| 評分         | Rating            | 1-5 分整數，使用者對 Recipe 的評價   |

## Bounded Contexts
系統分為三個獨立的 Bounded Context，各自產出獨立的 OpenAPI spec：
1. recipe-management — 管理 Recipe 主資料與 Rating
2. meal-planning     — 管理 MealPlan 與 MissingIngredients 計算
3. shopping          — 管理 ShoppingList 與 ShoppingItem

**Context 之間只傳遞 ID（recipe_id, meal_plan_id），不共用 schema。**

## Business Rules（業務規則）
- 一個 MealPlan 對應一個自然週（Monday–Sunday）
- Rating 為 1–5 的整數，缺少時為 null（未評分）
- 當 Rating >= 4 時，Recipe 的 `recommended` 欄位自動設為 true
- ShoppingList 在所有 ShoppingItem 都標記 checked=true 後，狀態變為 COMPLETED

## 你的任務
為 `recipe-management` Bounded Context 生成 OpenAPI 3.1 spec。
只包含此 context 範圍內的 endpoint 與 schema。
```

### 執行 LLM 並觀察差異

**直接 prompt（無建模產物）的輸入：**

```text
請幫我設計一個菜譜平台的 REST API，用 OpenAPI 3.1 格式。
```

**modeling-first 的輸入：**

上面的 system prompt + 「請生成 `recipe-management` 的 OpenAPI 3.1 spec。」

**v1 直接 prompt 的典型輸出（摘要）：**

```yaml
components:
  schemas:
    Recipe:
      type: object
      properties:
        id:      { type: string }
        name:    { type: string }
        steps:   { type: array, items: { type: string } }
```

3 個 schema，沒有 `Rating`，沒有 `recommended`，`steps` 是 `string[]` 而非結構化的 Step 物件。

**v2 modeling-first 的典型輸出（摘要）：**

```yaml
components:
  schemas:
    Recipe:
      type: object
      required: [id, name, ingredients, steps]
      properties:
        id:          { type: string, format: uuid }
        name:        { type: string }
        ingredients: { type: array, items: { $ref: '#/components/schemas/Ingredient' } }
        steps:       { type: array, items: { $ref: '#/components/schemas/Step' } }
        rating:      { $ref: '#/components/schemas/Rating', nullable: true }
        recommended: { type: boolean, readOnly: true }

    Ingredient:
      type: object
      required: [name, quantity, unit]
      properties:
        name:     { type: string }
        quantity: { type: number }
        unit:     { type: string, enum: [g, kg, ml, l, piece, tbsp, tsp] }

    Rating:
      type: integer
      minimum: 1
      maximum: 5

    Step:
      type: object
      required: [order, description]
      properties:
        order:       { type: integer }
        description: { type: string }
```

9 個 schema（計入 Ingredient、Step、Rating、Error response 等），業務規則 `recommended: readOnly: true` 出現了，`unit` 有 enum 約束，`id` 有 `format: uuid`。這些差異全部來自建模過程中顯化的領域知識。

---

## 5. 底層機制：為什麼建模能提升 LLM 輸出品質

### LLM 是統計壓縮機

LLM 在訓練時壓縮了大量程式碼與文件，它的輸出是「訓練資料中最常見的菜譜 API 長什麼樣子」的加權平均。這個平均通常能產出通用的 CRUD API，但不包含你的 domain 裡特定的 business rule——那些從未在網路上公開過。

### 三種 Context 注入的品質差異

| 注入方式 | LLM 拿到什麼 | 典型問題 |
|----------|-------------|----------|
| 無（直接 prompt） | 「菜譜平台」這幾個字 | 統計平均輸出，缺少隱性規則 |
| 口語描述 | 散文，含歧義 | LLM 自行解讀歧義，產出對它合理但對你不對的版本 |
| 術語表 + 邊界 + 規則 | 精確詞彙 + 結構約束 | 顯著減少術語歧義與 schema 遺漏 |

Martin Fowler 引述 Eric Evans 的那句話——「software doesn't cope well with ambiguity」——在 LLM 時代有了新的詮釋：LLM 不是不能處理歧義，它處理歧義的方式是**猜測並填補**，而那個猜測結果不一定是你要的。

### 建模的三個信號傳遞作用

1. **詞彙固定（Vocabulary Pinning）**：告訴 LLM「在本系統裡，`Rating` 是 1-5 整數，不是星號圖示，不是文字評論」。LLM 不必猜。
2. **邊界固定（Boundary Enforcement）**：告訴 LLM「這個 spec 只包含 recipe-management context，不要把 ShoppingList 塞進來」。LLM 不會越界。
3. **規則顯化（Policy Materialization）**：把 `當 Rating >= 4 時，recommended=true` 寫進 system prompt，LLM 就能在 schema 中加 `readOnly: true` 並在 description 裡說明派生邏輯，而不是讓這條規則躲在某個程式設計師的腦子裡。

> 如果你對「通用語言作為 LLM 的詞彙表」這個議題想更深入，回看 [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md)。

---

## 6. 對比取捨

### 直接 prompt vs Modeling-first：全面對比

| 面向 | 直接 prompt | Modeling-first |
|------|------------|----------------|
| **速度（第一次）** | 5 分鐘內出結果 | 需要 1–3 天工作坊 |
| **輸出完整度** | 低（缺隱性規則） | 高（顯化 domain knowledge）|
| **術語一致性** | 不穩定（LLM 自由發揮） | 受術語表約束 |
| **業務規則覆蓋** | 常遺漏 | 明確編碼進 prompt |
| **可稽核性** | 難（不知道 LLM 假設了什麼）| 高（建模產物是顯性文件）|
| **跨 Context 耦合風險** | 高（LLM 傾向合併 schema） | 低（邊界明確劃定）|
| **適合的場景** | Greenfield 小型 / PoC | 複雜 domain / 長期維護系統 |
| **額外成本** | 無 | 工作坊時間 + 建模技能 |

### 什麼時候不值得做 Modeling-first

- **PoC 或探索性 Spike**：你需要在 2 小時內驗證一個想法，直接 prompt 生成 throwaway code 即可。
- **Domain 極度單純**：CRUD 的待辦清單（Todo），沒有隱性業務規則，modeling 帶來的信號很少。
- **團隊沒有領域專家可以訪談**：Domain Storytelling 的前提是有真實的 domain expert 在場；如果找不到，你做的不是建模而是想像，反而可能固化錯誤假設。

---

## 7. 踩雷集錦

**雷 1：建模產物寫完就「存檔」，沒有注入 prompt**

錯誤直覺：「我們已經做了 EventStorming，所以 LLM 會產出比較好的結果。」
正確認識：EventStorming 的產物需要被**翻譯成 LLM 能讀的 context**——術語表、邊界聲明、business rule 列表——然後明確放進 system prompt 或 spec preamble。做了工作坊但沒把結論注入 prompt，等同於沒做。

---

**雷 2：把三個 Bounded Context 的術語全部混進同一個 prompt**

錯誤直覺：「把所有術語表都告訴 LLM，它會更聰明。」
正確認識：上下文過多反而讓 LLM 難以判斷當前任務的邊界，傾向生成把 ShoppingList 和 Recipe 合在一起的大型 schema。分批注入：每次生成一個 context 的 spec，只給該 context 的術語。

---

**雷 3：把 Domain Storytelling 故事直接丟給 LLM，不做二次整理**

錯誤直覺：「工作坊的白板照片或 DOML 文字就是 spec，直接附上去。」
正確認識：Domain Storytelling 是**給人類看的探索工具**，不是給 LLM 看的 spec。原始故事裡充滿隱喻、轉折、重複，LLM 需要整理後的結構：術語表、邊界、規則。把整理步驟省掉，輸出品質大幅下降。

---

**雷 4：只做一輪建模，以為終版**

錯誤直覺：「做完 EventStorming 就知道全部了。」
正確認識：Junker 的實驗做了三輪原型。每次 LLM 產出的 OpenAPI spec 反過來成為下一輪 domain 討論的素材，讓領域專家看到「系統現在長什麼樣」而產生新的更正。這是一個迭代循環，不是線性流程。

---

**雷 5：以為 modeling-first 解決了「不知道要什麼」的問題**

錯誤直覺：「只要做好建模，需求就完整了。」
正確認識：Daniel Westheide（INNOQ）明確指出，SDD 和 DDD 擊中同一面牆：「如果你的組織無法讓領域專家真正參與，做什麼方法論都救不了你。」建模只能把已知的知識結構化，無法憑空產生不存在的領域理解。

---

## 8. 進階延伸：自動化這條 pipeline

### 用 LLM 輔助 EventStorming（現況與限制）

Qlerify 是目前少數有出貨 AI 輔助 EventStorming 的產品：使用者以自然語言描述流程，AI（使用 ChatGPT-4o 等模型，版本依產品更新而變，查證日期 2026-06-30）在空白畫布上生成事件序列、泳道、aggregate 建議。但 Qlerify 明確限制：AI 產生只適用於空白畫布，且需要人工審閱修正。它是輔助，不是替代工作坊。

更雄心勃勃的「persona agents 持續監測 spec drift」提案（例如 Alireza Rahmani Khalili 的文章，2026-06-22）目前仍是概念性的，沒有經過規模化驗證。把這類方案視為「研究方向」而非「可立即採用的實踐」。

### 把 pipeline 接到 Spec Kit 或 Kiro

如果你的團隊已在使用 GitHub Spec Kit，建模產物可以直接作為 `/speckit.specify` 的輸入——把術語表和 business rule 放進 spec template 的對應欄位，讓 Spec Kit 的後續 `/speckit.plan` 和 `/speckit.tasks` 在這個語境下運作。

> 如果你對 Spec Kit 工作流不熟，先看 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)。

AWS Kiro 的三份規格文件（requirements.md / design.md / tasks.md）對應得很自然：Domain Storytelling 的故事和 EventStorming 的事件時間軸可以整理進 requirements.md；OpenAPI schema 進 design.md；LLM 生成的任務列表進 tasks.md。

> 如果你對 Kiro 的三文件流程還不清楚，回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)。

### 失敗邊界案例：domain 過度複雜時

Junker 的範例是一個邊界清晰的菜譜平台。當 domain 變得複雜——例如金融監管系統，Context 之間有複雜的政策相依——EventStorming 的輸出本身就可能有爭議，無法在一兩次工作坊裡收斂。這種情況下，modeling-first 的成本倍增，而 LLM 拿到的「建模產物」本身可能就是有爭議的。這是這條 pipeline 最脆弱的地方。

Thoughtworks Technology Radar（Vol 34，Nov 2025）把 SDD 置於「Assess」環，明確指出工作流「elaborate and opinionated」，輸出「hard to review」。在複雜 domain 採用此方案前，建議先在一個中小型 Bounded Context 試跑，驗證效益後再擴大。

---

## 9. 動手練習

選一個你熟悉的小型 domain（不要用菜譜，要用你真正理解的業務），完成下面四步：

**Step 1：寫一個 DOML 格式的 Domain Story（至少 5 個句子）**

格式參考本章第 2 節。必須包含至少一條你覺得「大家會以為不用說」的隱性步驟。

**Step 2：把 Domain Story 轉成文字版 EventStorming**

格式參考本章第 3 節。至少識別出兩個 Bounded Context 和兩條 `[POLICY]`。

**Step 3：組裝 System Prompt 並呼叫 LLM**

把術語表、邊界聲明、business rule 寫成 system prompt，請 LLM 為其中一個 Context 生成 OpenAPI 3.1 spec。

**Step 4：對比**

清空 LLM 的 context，改用一句話直接 prompt（「請幫我設計 [你的 domain] 的 API」），觀察 schema 數量和業務規則覆蓋的差異，寫下觀察。

---

## 本章重點整理

- Modeling-first prompting 是一條兩步過濾器：Domain Storytelling 把口語需求變成結構化故事，EventStorming 把故事變成事件時間軸和 Bounded Context 邊界，再把這些產物整理成 LLM 的 system context。
- Annegret Junker 的 Larder 案例是目前這條 pipeline 最具體的演示：v1 直接 prompt 產出 3 個 schema，v2 建模後產出 9 個 schema，多出的 schema 來自工作坊中顯化的隱性業務規則。
- LLM 輸出品質由輸入 context 的信噪比決定。建模的三個作用是：詞彙固定、邊界固定、規則顯化。
- 代價是真實的：需要 1–3 天工作坊和有建模技能的人。PoC、單純 domain、缺乏領域專家的場景不值得做。
- 建模不解決「沒有領域專家可以訪談」的問題——那是一個組織問題，不是方法論問題。
- 這條 pipeline 可以接到 GitHub Spec Kit（作為 `/speckit.specify` 的高質量輸入）或 AWS Kiro（requirements.md / design.md）。

---

## 自我檢核

- [ ] 我能用自己的話解釋，為什麼直接 prompt 和 modeling-first prompt 的輸出 schema 數量會不同（面試被問，能回答嗎？）
- [ ] 我能說出 Domain Storytelling 和 EventStorming 各自貢獻了什麼，以及為什麼要分兩步而不是直接跳到 EventStorming
- [ ] 我理解「LLM 是統計壓縮機」這個比喻，以及它為什麼解釋了建模前置的必要性
- [ ] 我知道三個 Bounded Context 之間「share IDs, not schemas」是什麼意思，以及違反這個原則會發生什麼
- [ ] 我能說出 modeling-first pipeline 的至少兩個實際失敗情境（domain 過度複雜 / 缺乏領域專家 / 建模產物未整理進 prompt）
- [ ] 我知道 Thoughtworks 把 SDD 放在哪個 Ring，以及他們的主要保留意見是什麼

---

## 延伸閱讀

**1. From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need — Annegret Junker（codecentric，2026-03-04）**
本章的第一手來源。直接讀 Larder 案例的三輪原型對比，以及她整理的「context 之間 share IDs, not schemas」設計決策。
URL：https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need

**2. Ubiquitous Language（bliki）— Martin Fowler（citing Eric Evans）**
「software doesn't cope well with ambiguity」這句話的原始脈絡。讀完整頁（很短），掌握為什麼術語嚴謹性對 LLM 特別重要的基礎論證。
URL：https://martinfowler.com/bliki/UbiquitousLanguage.html

**3. Spec-Driven Development is Domain-Driven Design's Impatient Cousin — Daniel Westheide（INNOQ，2026-03）**
最清晰地說明 SDD 和 DDD 為什麼在同一面牆撞壁的文章——都需要真實的領域專家參與，兩者都不是純方法論就能解決的問題。讀「impatient cousin」那一節和失敗模式的部分。
URL：https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/

**4. Spec-Driven Development | Technology Radar Vol 34 — Thoughtworks（Nov 2025）**
確認這條 pipeline 目前的市場定位是「Assess」不是「Adopt」，以及他們列出的具體保留意見。讀 Tessl 的「bitter lesson」段落——它解釋了為什麼過度手工規則不 scale。
URL：https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development

**5. EventStorming（官方網站 / 書籍）— Alberto Brandolini**
本章使用的 EventStorming 記法來自此處的標準定義。讀 intro 章節，確認你對橘色 Domain Event、藍色 Command、Policy 等符號的理解和原始定義對齊。
URL：https://www.eventstorming.com/book/

**6. How Creating a Ubiquitous Language Ensures AI Builds What You Actually Want — Daniel Schleicher（2026-01-04）**
用「order」這個詞的多義性舉例——相同詞在不同脈絡裡可以是採購單、外賣訂單、命令——說明為什麼歧義詞彙讓 LLM「amplify the chaos」。和本章第 5 節的機制解釋互補。
URL：https://www.danielschleicher.com/software/engineering,/ai,/spec-driven/development/2026/01/04/removing-ambiguity-with-spec-driven-development.html

**7. Domain-Driven Design: Tackling Complexity in the Heart of Software — Eric Evans（2003，Addison-Wesley）**
通用語言、Bounded Context、Aggregate 這些概念的原始出處。本章只用到了這些概念的表面，若想理解為什麼這些概念能支撐 LLM 輸出品質，需要從第 II 部「The Building Blocks of a Model-Driven Design」開始讀，尤其是 Chapter 2（Ubiquitous Language）和 Chapter 4（Isolating the Domain）。

---

本章的 pipeline 到此走通了一個小例子——從散文需求到有術語約束的 OpenAPI。下一章把這個想法推到更完整的機械化流水線：如何把 spec 轉換成 plan、再轉成 tasks、再驅動實作、最後做驗證，而不是手動控制每一步。

→ [Ch 38 自建一條 spec→plan→tasks→implement→verify 流水線](./38-build-your-own-pipeline.md)
