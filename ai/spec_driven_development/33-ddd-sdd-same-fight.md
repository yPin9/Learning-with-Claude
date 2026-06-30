# Ch 33 — 一個問題，兩個時代：DDD 與 SDD 是同一場仗

> **目標**：論證領域驅動設計（Domain-Driven Design，DDD，2003）與規格驅動開發（Spec-Driven Development，SDD，2024-26）在解同一個根本問題——讓人與機器共享一份不含糊的真相；理解 Daniel Westheide 所稱「DDD 的不耐煩表親」的意涵，以及為何這個標籤精準而非貶義。

---

## 心智圖像：同一堵牆，兩種攻法

想像一間工廠。一邊是產品設計師，腦子裡有完整的規格；另一邊是生產線工人，只看到零件和手冊。**中間隔著一堵牆，叫做「語意落差」**。

設計師說「承載板」，生產線叫「底座」，文件寫「支撐件」——三個詞指同一個物件。一旦出貨量放大，每次跨越這堵牆都會引入歧義、錯誤、需要昂貴的返工。

2003 年，Eric Evans 在《領域驅動設計》（Domain-Driven Design，俗稱「藍皮書」）裡給這堵牆命名，並提出一套工具拆牆：通用語言（Ubiquitous Language）、限界上下文（Bounded Context）、領域模型（Domain Model）。目標是讓業務專家和開發者共用同一份不含糊的概念地圖。

二十一年後，GitHub Spec Kit（2025）、AWS Kiro（2025）、Tessl（2025-26）等工具出現，提出「規格才是真相來源，程式碼只是派生物」。牆還是那堵牆；這次躲在牆另一邊的，換成了 LLM（大型語言模型）。

```
             語意落差（那堵牆）
    人類理解                    機器執行
  ─────────────────────────────────────────
  2003 DDD：  業務專家  │  開發者
              └─ 通用語言消除同義詞 ─┘

  2025 SDD：  人類規格師  │  LLM + AI Agent
              └─ 結構化規格消除不精確性 ─┘
```

同一場仗，換了對手。

---

## 歷史脈絡：在 DDD 和 SDD 之前，人們怎麼做的？

### DDD 之前（1990 年代）

資料庫模式直接驅動物件設計，業務概念被碾碎進「user」、「item」、「record」等資料表欄位。開發者從不坐下來和業務專家對齊詞彙；需求文件用自然語言寫，充斥著「應可能」「一般情況下」「適當時」。

結果是：客戶說「訂單」，資料庫有三張表叫 `orders`、`purchase_orders`、`order_headers`，各自有微妙不同的 `status` 欄位，沒有人知道哪個是真的。

> 如果你對自然語言需求的病症還不熟，先回看 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)。

### SDD 之前（2022-2024 年初）

LLM 進入開發工作流的第一波方式是「振動（vibe）式對話」——開發者口頭描述想要什麼，LLM 吐出程式碼，再口頭反饋，無限循環。Sean Grove 在 2025 年 AI Engineer World's Fair 的演講中把這個模式稱為「把 AI 當搜尋引擎而非字面思維的協作者」。

Den Delimarsky（GitHub，Spec Kit 創建者）在 GitHub Blog 2025-09-02 也寫道：

> "We treat coding agents like search engines when we should be treating them more like literal-minded pair programmers."

字面思維（literal-minded）：LLM 沒有背景知識、沒有在業務會議中旁聽五年的記憶。你說「訂單」，它選一個語意；下一輪對話它可能選另一個。**不含糊的規格是讓 LLM 保持一致的唯一手段**。

---

## DDD 的核心武器與根本訴求

Evans 的工具箱（詳見 Ch 14-21）解決的問題，換到 SDD 場景後有精確的對應物：

| DDD 概念 | 解決的問題 | SDD 場景的對應物 |
|---|---|---|
| **通用語言（Ubiquitous Language）** | 業務詞彙多義、隊間詞彙分裂 | 規格中的術語表（glossary）；放進 AGENTS.md |
| **限界上下文（Bounded Context）** | 一個模型在大系統中膨脹至自相矛盾 | 一個 AI agent 的任務邊界；monorepo 的一個目錄 |
| **領域模型（Domain Model）** | 業務規則散落在程式碼四處，無可查閱 | 規格的骨架；例如 OpenAPI / JSON Schema |
| **聚合（Aggregate）** | 跨多個實體的一致性難以保證 | 界定 agent 單次可修改的交易邊界 |
| **Event Storming** | 業務流程對開發者不透明 | LLM 輔助需求探索（如 Qlerify）；AI 事前建模 |

Martin Fowler 在 UbiquitousLanguage 條目中（2006-10-31）有一句話現在讀起來像是替 LLM 時代先寫好的：

> "software doesn't cope well with ambiguity"

換到 2026 年：LLM 不只是「不應付歧義」，它是**歧義放大器**——給一個模糊的詞，它會一致地選擇同一個（可能錯的）解釋，並在整個 codebase 裡蔓延。通用語言消歧義的價值因此加倍。

---

## Westheide 的論證：「不耐煩的表親」

Daniel Westheide（Senior Consultant, INNOQ）在 2026-03 的文章
〈Spec-Driven Development is Domain-Driven Design's Impatient Cousin〉裡做了迄今最清楚的比較分析。

他的核心論點（原文脈絡，非逐字引言）：

**相同的根本問題**：DDD 和 SDD 都需要真正能說清楚業務邏輯的領域專家。兩者都在組織的同一堵牆上撞頭——如果業務專家和開發者被中間層隔開，兩者都失效。

**差異在探索時序**：Evans 的 DDD 主張領域模型應從**實作中迭代浮現**——沒有任何一次前期訪談能完整捕捉業務的複雜性。SDD（尤其是 BMAD、Spec Kit 這類工具）假設可以用結構化訪談和規格撰寫把探索**前置**。Westheide 稱 SDD 是「不耐煩的」（impatient），因為它想跳過迭代探索直達正確規格。

**共同的失敗牆**：如果你的組織無法讓業務專家真正參與規格撰寫，SDD 的規格層就是一疊漂亮的 Markdown；正如組織無法讓業務專家直接協作，DDD 的通用語言就是一疊漂亮的術語表。

這個「不耐煩表親」的標籤精準在哪裡：

- DDD 耐心地說「先把模型跑起來，讓複雜性透過實作浮現」
- SDD 說「我要先把所有需求寫清楚，再讓 AI 一次生成」
- 兩者都承認**人類的業務智識是無法繞過的**

---

## codecentric 的實驗：橋接的最強經驗證據

Annegret Junker（codecentric，2026-03-04）在一篇有具體數字的文章中，用一個食譜平台（Larder 假想產品）跑了三輪原型：

**輪次一（天真 prompt）**：直接告訴 LLM「設計一個食譜平台的 API」。LLM 生成了一份 OpenAPI 規格，其中只有 3 個 schema（Recipe、Ingredient、User）。一個關鍵業務規則——**使用者可以對食譜自評難度**——完全不見了。

這個失敗不是 LLM 能力不足。食譜平台的「自評難度」是一個業務決策：「我們想讓使用者有感被聆聽、並用此數據個人化推薦。」這個意圖從來沒有進到 prompt，所以 LLM 跳過了它。

**輪次二（加入 EventStorming）**：先跑 Event Storming 工作坊，讓業務人員在白板上用便利貼把「使用者瀏覽食譜 → 加入購物清單 → 自評料理難度 → 分享餐計畫」這條流程事件化。這個工作坊讓自評業務規則浮現，也拆出 ShoppingList、Meal enums、Diet 等新概念。把這些概念寫進規格再讓 LLM 生成——新規格有 **9 個 schema**，自評業務規則被正確實作。

EventStorming 工作坊的具體產物是一條事件時間線，例如：

```
[UserViewedRecipe]  →  [UserAddedToShoppingList]
   →  [MealCooked]  →  [UserRatedDifficulty]  →  [RecommendationUpdated]
```

這條時間線讓 `UserRatedDifficulty` 事件出現了——那是 LLM 從一句話 prompt 永遠推斷不出來的。把事件時間線轉成術語表再轉成規格，LLM 就有了完整的上下文。

**輪次三（加入 Bounded Context 分割）**：把大系統拆成三個限界上下文（食譜瀏覽、購物計畫、個人偏好），各自生成一份 OpenAPI 規格，三份規格**共享 ID、不共享 schema**（share IDs, not schemas）。

這個步驟防止了一個常見的 LLM 失效：「全域統一 schema 的爆炸」。讓一個 LLM agent 負責一個限界上下文，它看到的規格邊界清晰，不會把「食譜在購物計畫語境中的意義」和「食譜在搜尋語境中的意義」混為一談。

Junker 的結論：「生成輸出的品質直接由語言的品質決定。」（The quality of that language directly determines the quality of the generated output.）

這是 Part 6 最重要的一條經驗驗證：**DDD 工作坊不只是讓人理解領域——它是讓 LLM 生出正確規格的前置必要工作**。

---

## 兩者共享的根本洞察

把 Evans 2003 和 SDD 2024-26 的核心主張並排：

```
Evans (2003):
  問題：自然語言中的歧義在軟體裡無法存活
  解法：通用語言 + 限界上下文 + 領域模型
  主體：人類開發者 vs. 人類業務專家

SDD (2025):
  問題：自然語言中的歧義在 LLM 生成時被放大
  解法：結構化規格 + 術語表 + 模型作為規格骨架
  主體：人類規格師 vs. LLM
```

兩個解法的核心都是：**讓一份不含糊的真相成為共享起點，其他一切從那裡派生**。

Evans 叫它「通用語言」；SDD 叫它「規格作為真相來源」（spec as source of truth）。Sean Grove 在 AI Engineer World's Fair 2025 談到「規格一次寫好，到處執行」（per community transcript of the talk），這句話的結構和 DDD「一份模型，全隊遵從」如出一轍。

Deepak Babu Piskala 在 arXiv 預印本（arXiv:2602.00180，2026-01-30）更明確地寫：「DDD 透過通用語言與 SDD 高度吻合——以領域術語書寫的規格，讓開發者和利害關係人都能讀懂。」（注意：這是個人預印本，機構背景未確認，50% 錯誤減少的數字引用了未能找到的一手研究，需謹慎對待。）

另一個視角：為什麼是**現在**？DDD 從 2003 年就存在，為何 SDD 要到 2024-25 年才出現？

答案在工具的改變。DDD 的通用語言消歧義後，人類開發者還是要把理解轉成程式碼——那個「轉譯」步驟本身是有損耗的，通用語言可以降低損耗但不能消除它。LLM 改變了這個等式：**如果 AI 可以直接從規格生成程式碼，那規格的精確度直接決定程式碼的品質**。中間的人類翻譯層被跳過了，語意落差的代價因此從「每次翻譯的小損耗」放大成「規格一字之差、程式碼一個邏輯分支之錯」。DDD 一直在告訴我們要消除語意落差；SDD 是在說「現在消除語意落差的急迫性更高了」。

---

## 對比取捨

| 面向 | DDD | SDD |
|---|---|---|
| **探索時序** | 模型從迭代實作中浮現 | 規格前置，再派生程式碼 |
| **主要受益者** | 複雜業務領域的長期維護 | AI agent 的一致性生成 |
| **核心文物** | 通用語言術語表、限界上下文圖 | requirements.md / design.md / spec 檔 |
| **失敗點** | 無法讓業務專家深度參與時 | 規格一旦漂移，AI 生成的就是「上版規格的程式碼」 |
| **對 ambiguity 的態度** | 主動消除，建立術語表 | 結構化規格格式強迫消歧（EARS、BDD、JSON Schema）|
| **工具成熟度** | 成熟（Evans 藍皮書 2003；Event Storming 2013）| 快速演進（Thoughtworks Radar：Assess，Nov 2025） |
| **最大批評** | 學習曲線陡、需長期組織投入 | 規格漂移、Markdown 過載、難以 review |

> Thoughtworks Technology Radar Vol. 34（Nov 2025）將 SDD 置於 "Assess" 環，而非 "Adopt"：工作流「精緻但武斷（elaborate and opinionated）」，產物「難以 review」。這是誠實的現況定位，不是否定。

---

## 踩雷集錦

### 雷 1：把通用語言當詞彙表一次性產出

**錯誤直覺**：開一個工作坊，讓業務和開發對齊術語，輸出一個 glossary.md，完工。

**正確認識**：Evans 明確說通用語言是**在實作中持續演化**的。第一份術語表只是起點。當程式碼開始反映出業務上沒有詞的概念，那就是要更新語言的訊號。SDD 的規格也是：寫完之後不是鎖起來，而是隨每次需求變更而共同演化——否則就是規格漂移（spec drift）。

> 規格漂移的詳細討論見 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

### 雷 2：以為 SDD 的規格能替代業務知識的獲取

**錯誤直覺**：有了 Spec Kit 的模板和 AI 的輔助，就可以跳過和業務專家的深度訪談——反正 AI 能推斷。

**正確認識**：Westheide 的論證在這裡最犀利：SDD 的「規格前置」隱含假設業務知識**已經存在且可以被提取**。如果你的組織裡業務專家和開發中間有三層轉述，或業務專家從未把規則說清楚過，那 SDD 的規格就是在噪音的基礎上加結構。Junker 的實驗說明，EventStorming 工作坊的存在不是為了讓 AI 省力，而是讓**業務規則浮現**——那個自評難度的業務規則，沒有人工作坊，LLM 永遠不會想到。

### 雷 3：認為 DDD 和 SDD 是兩套競爭方案，要選一個

**錯誤直覺**：公司要走 SDD 就不用搞 DDD，那是舊時代的東西。

**正確認識**：兩者在不同層次工作。DDD 是**問題空間的建模方法論**；SDD 是**AI 時代的交付工作流**。最好的做法是先用 DDD 工具（Domain Storytelling、EventStorming、限界上下文圖）弄清楚業務領域，再把那個模型編碼成 SDD 的規格（requirements.md、OpenAPI、JSON Schema），讓 LLM 從結構清晰的規格生成程式碼。Junker 的三輪原型就是這個流程的示範。

### 雷 4：用同一份通用語言跨越限界上下文

**錯誤直覺**：全公司統一一份術語表，所有 agent 共用同一個 glossary。

**正確認識**：Fowler 在 BoundedContext 條目（2014-01-15）舉「meter」的例子——在能源計費語境是「電表」，在施工語境是「公尺」。「在對話中可以含混過去，但在電腦的精確世界中不行。」跨越限界上下文的術語表反而重新引入歧義。正確做法是每個限界上下文有自己的術語表，Context Map 管理跨境轉換。

### 雷 5：把「SDD 的失敗」歸因於工具，而非組織

**錯誤直覺**：Spec Kit 跑起來寫了一堆 Markdown 但最後沒用，換 Kiro 試試看。

**正確認識**：Westheide 的診斷在這裡同樣適用：「如果你的組織無法做好 DDD，它也無法從 BMAD 的規格層受益。」工具換一輪，同樣的組織問題原地等你。工具是放大器，不是業務理解的替代品。François Zaninotto（Marmelab CEO，2025-11-12）在實測中記錄了一個 AI agent 把「verify implementation」任務標記為完成，卻沒有寫半行測試——工具沒有抵抗力對抗「規格寫得太模糊」。

---

## 進階延伸

### 當 DDD 遇上 Agent 架構本身

有一個更激進的主張：不只是用 DDD 建模**被開發的系統**，而是用 DDD 建模**agent 本身的職責邊界**。Russ Miles（2025-10-08）稱之為 Domain Driven Agent Design——讓限界上下文決定哪個 agent 處理哪個業務單元，「Risk 部門的 agent 不會滲入 Customer Service 的地盤」。

Nick Tune（Sr Staff Engineer, PayFit，2026-03-26 訪談）更進一步，把 AI 工作流本身建模成一個 DDD Aggregate 的狀態機（state machine with invariants），用 dependency-cruiser lint 規則在建置時**確定性地**阻止 agent 跨越限界上下文——而非期待 LLM 自我克制。

這個方向（version-dependent，查證日期 2026-06-30）是概念性的，實作細節尚輕薄，但方向值得追蹤。

> 限界上下文對應 Agent Scope 的更深討論，見 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)。

### 形式化規格是第三條路嗎？

TLA+、Alloy 等形式化規格語言（見 [Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)）的訴求更早：用數學精確消除歧義。和 DDD 的通用語言（自然語言精確化）、SDD 的結構化規格（格式化自然語言）比較，形式化規格在表達力上最強，但工具鏈和人才門檻是其主要障礙。三種方案不互斥；核心系統（如分散式協議、支付核心）可以形式化規格加上 DDD 通用語言加上 SDD 工作流三層疊加。

---

## 動手練習

以下練習不需要特定工具，紙筆或文字編輯器即可完成。

**練習 33-A：找出一個組織裡的「語意衝突」**

選一個你熟悉的系統（工作專案或開源項目）。找到至少一個詞在不同模組、文件、或代碼庫中指不同的東西。常見候選詞：`account`、`user`、`order`、`product`、`status`、`event`。

1. 寫出這個詞的三個用法，各自的確切語意。
2. 如果你把這三個用法都丟給 LLM 作為背景，它會怎樣選擇？（提示：LLM 通常選最常見的語意，或根據上下文最近的一個。）
3. 設計一個 glossary 條目解決這個衝突，格式：

```markdown
## 術語：訂單（Order）

| 語境 | 精確定義 | 英文術語 |
|---|---|---|
| 購物車結帳流程 | 使用者點擊確認後、付款完成前的待付款請求 | PendingOrder |
| 倉庫揀貨流程 | 付款已確認、等待出貨的揀貨任務 | FulfillmentOrder |
| 財務報表 | 已完成交易的會計記錄 | CompletedTransaction |
```

**練習 33-B：比較一份「振動式 prompt」和一份「DDD 建模後的規格」**

用一個具體功能（例如「使用者可以追蹤一部電影的觀看進度」）：

1. 寫一個一句話振動式 prompt：「幫我實作追蹤電影觀看進度的功能」
2. 用 EventStorming 思路列出這個功能的 domain events（至少 5 個）。範例起點：
   - `WatchSessionStarted`
   - `PlaybackProgressUpdated`
   - `WatchSessionPaused`
   - `WatchSessionCompleted`
   - `ProgressResumed`
3. 寫出一份 50-100 字的 requirements.md 片段，包含術語定義

比較這三件東西：哪一個最不含糊？LLM 從哪個起點會生出更正確的程式碼？你預期 schema 數量會有什麼差異（對照 Junker 的 3→9）？

**練習 33-C：給一份已有的規格補上 DDD 限界上下文**

如果你完成了 [練習 D — 把需求＋領域模型寫成一份完整的 spec](./practice-d-write-a-spec.md)，回頭看那份 spec：

1. 找出其中至少兩個可能存在語意衝突的術語
2. 判斷它們是否應該屬於同一個限界上下文
3. 如果不是，設計一個 Context Map 說明如何在邊界交換資訊（至少說明：ID 共享策略、哪一方是 Upstream / Downstream）

---

## 本章重點整理

- DDD（2003）和 SDD（2024-26）解同一個問題：讓人與機器共享一份不含糊的真相。前者的「機器」是程式碼和維護它的開發者；後者的「機器」是 LLM 和 AI agent。
- Westheide 的「不耐煩表親」標籤精準：SDD 想把探索前置（upfront），DDD 讓模型從迭代中浮現；但兩者都需要業務專家的真正參與，也都在組織官僚主義面前失效。
- Junker 的 codecentric 實驗是目前最強的橋接實證：EventStorming 工作坊讓 OpenAPI schema 從 3 個增加到 9 個，且捕捉了純 LLM 看不到的業務規則。
- 通用語言幫 LLM 的原理：Fowler 說「軟體不應付歧義」，LLM 是歧義放大器——一份精確的術語表把整個類別的幻覺切掉。
- 兩者不競爭：用 DDD 工具探索領域，用 SDD 工作流（Spec Kit、Kiro）把模型交付成程式碼。
- 共同失敗牆：組織若無法把業務專家拉進房間，兩者都只是漂亮的文件。

---

## 自我檢核

- [ ] 我能用自己的話解釋「為什麼說 DDD 和 SDD 在解同一問題」——如果面試官問，我怎麼答？
- [ ] 我能說明 Westheide 用「不耐煩表親」這個標籤指的是什麼具體差異（探索時序）
- [ ] 我能解釋為什麼 LLM 是「歧義放大器」，以及通用語言如何對抗這個問題
- [ ] 我能描述 Junker 的實驗流程，以及「3 個 schema → 9 個 schema」的機制（EventStorming 讓什麼浮現了？）
- [ ] 我能說出 DDD 和 SDD 的最大共同失敗點是什麼，而且不把責任歸給工具

---

## 延伸閱讀

1. **Spec-Driven Development is Domain-Driven Design's Impatient Cousin** — Daniel Westheide (INNOQ, 2026-03)
   - URL: https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/
   - 從這裡開始：「impatient cousin」的比較分析段落，以及「如果組織無法做好 DDD，也無法受益於 BMAD 的規格層」的論述
   - 本章關聯：本章核心論點的主要一手來源；提供本章無法在一章內覆蓋的組織動態細節

2. **From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker (codecentric, 2026-03-04)
   - URL: https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need
   - 從這裡開始：Larder 食譜平台的三輪原型對比，以及 schema 數量從 3 到 9 的具體機制
   - 本章關聯：本章「最強橋接實證」段落的直接來源；有具體數字，不是觀點文

3. **UbiquitousLanguage (bliki)** — Martin Fowler (2006-10-31，引用 Eric Evans)
   - URL: https://martinfowler.com/bliki/UbiquitousLanguage.html
   - 從這裡開始：第一段「software doesn't cope well with ambiguity」的完整脈絡
   - 本章關聯：本章「LLM 是歧義放大器」論證的基礎定義；整頁很短，值得全讀

4. **BoundedContext (bliki)** — Martin Fowler (2014-01-15)
   - URL: https://martinfowler.com/bliki/BoundedContext.html
   - 從這裡開始：「meter」的多義詞範例段落——在對話中可以含混過去，但在電腦的精確世界中不行
   - 本章關聯：解釋為什麼不能有跨越限界上下文的通用術語表；這個失效模式在 LLM 場景中完全複現

5. **Spec-Driven Development | Technology Radar Vol. 34** — Thoughtworks (Nov 2025)
   - URL: https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development
   - 從這裡開始：Assess 環的定位理由，以及 Tessl 的「bitter lesson」警告
   - 本章關聯：提供誠實的現況錨點——SDD 是 Assess，不是 Adopt；本章不宜過度樂觀

6. **Agentic Code Workflows with Nick Tune** — Nick Tune (訪談，2026-03-26) via Techworld with Milan
   - URL: https://newsletter.techworld-with-milan.com/p/agentic-code-workflows-with-nick
   - 從這裡開始：dependency-cruiser 確定性邊界執行的段落，以及把 AI 工作流建模為 aggregate 狀態機
   - 本章關聯：本章「進階延伸：當 DDD 遇上 Agent 架構本身」段落的主要實務案例

7. **Domain-Driven Design: Tackling Complexity in the Heart of Software** — Eric Evans (Addison-Wesley, 2003)
   - 本章關聯：DDD 的一手來源；第一章「The Utility of a Model」和「The Ubiquitous Language」章節直接奠定本章的 DDD 基礎。所有後繼討論（包括 Westheide、Junker）都假設讀者知道 Evans 在說什麼。

8. **How Creating a Ubiquitous Language Ensures AI Builds What You Actually Want** — Daniel Schleicher (2026-01-04)
   - URL: https://www.danielschleicher.com/software/engineering,/ai,/spec-driven/development/2026/01/04/removing-ambiguity-with-spec-driven-development.html
   - 從這裡開始：「order」多義詞的具體例子，以及「amplify the chaos」的表述
   - 本章關聯：把 Fowler 的抽象論點（軟體不應付歧義）直接落地到 LLM 場景，補足本章缺少的具體歧義案例

---

下一章把通用語言帶進 LLM 的實際工作流：如何設計術語表讓 agent 的詞彙固定下來、要放在哪裡、以什麼格式。

→ [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md)
