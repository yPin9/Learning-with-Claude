# Ch 42 — 什麼時候不要用 SDD

> **目標**：建立任務選型直覺——對探索性工作、一次性腳本、極小改動，知道為什麼 SDD 是過剩開銷；對高風險、多人協作、長壽系統，知道為什麼 SDD 是最划算的前期投資。同時釐清「vibe coding」不是 SDD 的對立面，而是互補的工具，學會在兩種模式之間有意識地切換。

---

## 直覺：工具的成本結構

SDD 的核心主張是「花在規格上的時間，在後期可以數倍回收」。這個主張不是謊言，但它有一個隱藏前提：**你需要有「後期」**。

如果一個任務沒有後期——不會被維護、不會被別人接手、一跑完就扔掉——那麼 SDD 的回收期永遠不會到來，你付出的前期成本就是純粹損耗。

用成本結構想這件事，比用「SDD 適不適合」想更清楚：

```
成本
 │
 │  ← SDD 前期規格成本（固定）
 │
 │         ← 規格讓後期維護/協作成本壓低（SDD 優勢區）
 │ ·····················──────────────────────
 │ ·····················
 │ ······ (無規格，快速迭代)
 │·····················
 └─────────────────────────────────────────── 時間
      ↑              ↑
   短命/一次性     長壽/多人協作

SDD 損益兩平點（break-even point）
```

問題永遠是：**這個任務的生命週期，有沒有長到讓 SDD 過了損益兩平點？**

---

## 歷史脈絡：為什麼我們需要「不要用 SDD」這一章

2025 年 SDD 工具浪潮（GitHub Spec Kit、AWS Kiro、Tessl）帶來了一個常見的思維陷阱：把「規格驅動」等同於「認真、專業、對的」，把「vibe coding」等同於「草率、業餘、不對的」。

這個非此即彼的框架，是 2001 年敏捷宣言之前的僵化瀑布思維的另一個版本，只是方向反過來了。

敏捷宣言說：「*我們更重視* 回應變化 *而非* 遵循計畫」，但它明確補了一句：「右側的項目仍有其價值」。同樣地，SDD 不否定快速迭代的價值，快速迭代也不否定結構規格的價值。問題只是：**現在這個任務，在這兩個量尺上各落在哪裡？**

> 如果你對敏捷宣言的完整脈絡還不熟，先回看 [Ch 5 迭代與敏捷：用快速回饋換掉大份前期規格](./05-iterative-agile.md)。

---

## 何時不要用 SDD：五個類別

### 類別一：純探索性工作

**特徵**：你不知道問題是什麼，更不知道解法是什麼。目標是「看看這個方向行不行」。

**例子**：
- 驗証某個機器學習假設（這個特徵組合有沒有預測力？）
- 評估一個第三方 API 能不能滿足你的用法
- 快速原型（proof of concept）看 UX 方向對不對

**為什麼 SDD 是錯的**：探索性工作的輸出是「知識」，不是「可維護的系統」。你根本不知道要規格化什麼，因為你正在發現問題本身。如果你在探索開始前寫了詳盡規格，你規格化的是你的假設，而不是問題。當探索的第一步就推翻了假設，規格變成了阻力而不是助力。

**正確做法**：用 vibe coding 快速跑假設，產出結論（不是程式碼），再決定要不要「把這個原型升格成真實系統」。升格那一刻，才是 SDD 進場的時機。

---

### 類別二：真正的一次性腳本

**特徵**：寫完跑一次，跑完就刪。

**例子**：
- 把舊資料庫的資料格式轉換到新格式（一次性 migration）
- 從 S3 桶裡批量下載特定前綴的檔案做分析
- 生成一份一次性的 CSV 報表

**為什麼 SDD 是錯的**：這類任務的「存活期」是一次執行。Colin Eberhardt（Scott Logic）的測量結果——用 Spec Kit 跑一個功能約需 4 小時，其中 3.5 小時是 review 規格文件——在這個情境下意義顛倒了：你花 3.5 小時 review 文件，但腳本只會跑一次，review 的價值幾乎為零。

**正確做法**：直接寫腳本，加上最低限度的行內注解解釋「為什麼這樣做」，跑完封存或刪除。

---

### 類別三：極小的、範圍清晰的改動

**特徵**：改一個 config 值、修一個錯字、換一個 API endpoint URL。

**例子**：
- 把 timeout 從 30 秒改成 60 秒
- 修正一個函式名的 typo
- 更新一個已廢棄的套件到新版本，API 完全相容

**為什麼 SDD 是錯的**：Birgitta Böckeler（Thoughtworks）記錄了 Kiro 把一個小 bug fix 膨脹成「4 個 user stories、共 16 條驗收條件」的真實案例。SDD 工具的設計目標是複雜功能，對極小改動它會「過度規格化」（over-speccing）。Addy Osmani（Google）的「指令詛咒（curse of instructions）」觀察也指出：規格越厚，LLM 遵循每一條的機率越低——對簡單改動，堆規格只是在降低信號雜訊比。

**正確做法**：直接用一句清楚的 prompt 指定改動，review diff，完成。

---

### 類別四：你自己是唯一讀者的個人工具

**特徵**：只有你會用，你隨時知道它在幹嘛，而且你願意在它壞的時候去讀 code。

**例子**：
- 個人 dotfiles 腳本
- 你自己用的 Obsidian 插件
- 自動化你個人工作流的小工具

**為什麼 SDD 是錯的**：規格的核心價值有兩個——讓 AI agent 有可依循的意圖、讓其他人（或未來的你）理解系統。如果只有你，現在、馬上在用，你就是那份活的規格。寫給自己的規格有很高機率成為 Zaninotto（Marmelab）所說的「Markdown Madness」——一堆你不會 review 的文件。

**但有一個邊界**：如果這個「個人工具」有可能成長為團隊工具，或者你估計六個月後你自己也會忘光，那就要算長壽系統了，SDD 值得投入。

---

### 類別五：時效性極強、競速窗口極窄的任務

**特徵**：在一個固定的短時間窗口內要看到結果，之後不管結果如何，這個方向就不再追求了。

**例子**：
- 駭客松（hackathon）48 小時作品
- 為一場演講準備的 live demo
- 測試一個市場假設的最小可行產品（MVP），目標是「這週投放，看有沒有轉換」

**為什麼 SDD 是錯的**：當商業/活動的時間窗口比規格的 break-even 期短，SDD 前期成本就是在一個不會到來的未來上做投資。這不是工程紀律的問題，是財務決策的問題。

**重要補充**：如果 MVP 驗證成功，要把它升格為真實產品，那就是 SDD 的進場時機。不要把「驗證原型」的程式碼當作產品的基礎——這是另一個問題，但 SDD 無法在 MVP 階段替你解決它。

---

## 什麼時候 SDD 是最划算的投資

對比上面的反例，SDD 的真正甜蜜點在這些交集上：

| 維度 | SDD 有優勢 | SDD 無優勢 |
|------|-----------|-----------|
| 生命週期 | 長壽（月以上） | 短命（一次性、幾天） |
| 協作人數 | 多人（≥2 位工程師） | 只有自己 |
| 變更頻率 | 需求會持續演化 | 需求已固定 |
| 風險等級 | 高（資安、合規、資料安全） | 低（影響範圍限縮） |
| 規模 | 跨 bounded context、多 service | 單一函式、單一腳本 |
| 交接需求 | 需要文件讓人接手 | 不需要接手 |

只要在表格右側佔多數，就要認真考慮跳過 SDD。

---

## vibe coding 不是壞習慣

「Vibe coding」這個詞在 SDD 社群裡有時帶著輕蔑的意味，好像是「沒有紀律的亂寫」。這個解讀是錯的。

AWS Kiro 本身就提供兩種 session 模式：**Vibe session**（互動問答、快速探索）和 **Spec session**（結構化開發）——而且設計上允許你在兩者之間切換。這不是意外的設計，而是工具作者認知到：探索期和構建期需要不同的認知模式。

Vibe coding 的價值在於：
- **低阻力進入**：不需要先定義你不知道的東西
- **快速反饋循環**：改一行看一下，比寫完規格再交給 agent 跑快得多
- **人在迴路（human-in-the-loop）**：你看著輸出，當場修正，不需要等 agent 解讀你寫的 Markdown

> 如果你對 Kiro 的兩種 session 模式想更深入了解，先回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)。

**實際問題不是「用不用 vibe coding」，而是「vibe coding 結束後要不要把結果升格為有規格的系統」**。

---

## 決策流程圖

在決定要不要用 SDD 之前，問自己這四個問題：

```
問題 1: 這個任務的生命週期超過一個月嗎？
   否 → 大概不需要 SDD
   是 → 往下

問題 2: 會有兩人以上維護這個 codebase 嗎（包括三個月後的你）？
   否 → 在邊界區，看問題 3
   是 → 往下

問題 3: 出錯的代價高嗎（資安、資料遺失、顧客影響、合規風險）？
   否 → vibe coding 可能就夠了
   是 → 往下

問題 4: 你現在清楚知道需求，還是需要先探索？
   探索期 → 先 vibe coding，探索完再決定
   需求清楚 → SDD 是值得的前期投資
```

這四個問題不是嚴格的演算法，是強迫你說清楚「我為什麼選擇這個模式」的工具。

---

## 踩雷集錦

**錯誤直覺 1：「SDD 等於認真，不用 SDD 等於偷懶」**

正確認識：SDD 是一種工具，使用工具的原則是「對的情境用對的工具」。在不需要長期維護的情境下強推 SDD，其實是把工程紀律和工具混淆了。Eberhardt 的案例記錄——用 Spec Kit 約 4 小時 vs. 直接迭代約 23 分鐘——說明的不是「SDD 不好」，而是「對他那個任務，那個時間點，SDD 的 break-even 期太長了」（查證日期 2026-06-30，數據來源：Scott Logic 部落格）。

**錯誤直覺 2：「有了 SDD，agent 就會自動做對」**

正確認識：Spec Kit HN 討論串（item 45610996）裡，用戶 yoaviram 記錄了 implement 指令「不遵循流程、忘記建立或執行測試」；用戶 hatmanstack 記錄 Kiro「不可預期地刪除程式碼且不 revert」。規格只是意圖的表達，agent 是否忠實執行規格是另一個問題。SDD 不能取代測試、不能取代人工 review。

**錯誤直覺 3：「探索期寫的規格，可以直接用來驅動 agent 建真實系統」**

正確認識：探索期的規格充滿了「我以為需求是這樣」的假設，其中大部分會被探索結果推翻。把探索期的 Spec Kit constitution 或 Kiro requirements.md 當作生產規格是危險的——它們記錄的是你的問題猜想，不是你對問題的理解。SDD 適合在探索結束、問題已收斂之後開始。

**錯誤直覺 4：「規格越詳細，結果越好」**

正確認識：Addy Osmani（Google）的「指令詛咒」：隨著規格條目增加，LLM 遵循每一條的機率*降低*。Osmani 的建議是「將規格深度調整到任務複雜度」（查證日期 2026-06-30）。對簡單任務，薄規格比厚規格更有效，因為 LLM 不需要在一堆條目裡決定哪些優先。

**錯誤直覺 5：「跑過 SDD 流程就有了文件」**

正確認識：Gojko Adzic（《Specification by Example》作者）觀察到，SDD 工具生成的很多 Markdown「似乎是為了讓工具追蹤自身進度，不見得是給人讀的」。規格如果沒有持續更新、如果 agent 的實作和規格悄悄分歧，就會發生「規格腐化（spec rot）」——一份讓人以為有文件但實際上說謊的文件，比沒有文件更危險。

> 關於規格腐化的機制與對策，參見 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

---

## harness 導入決策的呼應

這份課程本身就是 SDD 使用時機的活教材。harness 工程課程（[Ch 41–44](./41-sdd-security.md)）討論了把 SDD 織進既有工程流程時的成本：CI 整合、團隊校準、工具維護、規格同步。

這些成本不是理由不用 SDD，而是提醒你：**SDD 的成本不只是寫規格的時間，還包括維持規格為真的長期工程成本**。

對個人小工具或短期探索，這個長期成本比起收益毫無意義。對一個多人協作、需要在 CI 失敗時明確知道是「規格說這樣但 code 做那樣」的系統，這個成本完全值得。

導入 harness 的決策邏輯和選擇 SDD 的邏輯是同一套：問「我需要這個系統跑多久、被多少人維護、出錯代價是什麼」。

---

## 進階延伸：本章提到的三個邊界議題

### 「升格」時機

當 vibe coding 做出來的東西被決定要推進生產，這是 SDD 的最佳進場點，但也是最難的一步——因為 vibe coding 的程式碼通常沒有清晰的 bounded context 邊界，沒有明確的 invariant，把它當成「規格驅動的第一版實作」反而會固化壞結構。

更好的做法：用 vibe coding 做出來的系統作為「需求驗證的實物（working prototype）」，然後從頭以 SDD 重新實作，把 prototype 的行為當作「使用者故事的驗證集」。

> 關於如何從 DDD 領域模型作為規格骨架的出發點，參見 [Ch 36 領域模型作為 spec 的骨架](./36-domain-model-as-spec-backbone.md)。

### 規格深度的分層策略

Böckeler 的三層分類（spec-first / spec-anchored / spec-as-source）不只是工具分類，也是你對每個功能選擇的深度：

- **spec-first**（規格指導，code 是主要產物）：適合大多數「存活期中等、有清楚需求」的功能
- **spec-anchored**（規格是活的合約，與 code 並行維護）：適合多人協作、API contract、core domain
- **spec-as-source**（規格是唯一真相，code 由規格再生成）：目前仍是前沿實驗，Tessl 在探索這個方向；非確定環境（non-determinism）問題尚未解決

你不需要對整個系統選一個深度。可以對核心領域用 spec-anchored，對輔助功能用 spec-first，對一次性腳本完全不用 SDD。

### Marc Brooker 的「往上拉（pulling up）」論點

Marc Brooker（AWS VP/Distinguished Engineer）在「Spec Driven Development isn't Waterfall」（2026-04-09）裡提出：SDD 和瀑布的根本差異是「迭代的對象」。瀑布迭代實作，然後把分歧推回規格（代價高昂）；SDD 迭代規格本身，快速，在 AI 加速下每一圈都很快。

這個論點很強，但它假設你的「規格迭代週期」比你的「問題探索週期」短。對探索性工作，這個假設不成立：你還不知道規格要長什麼樣，你無法有效迭代一個你不知道對不對的規格。所以「SDD 不是瀑布」不等於「SDD 在探索期有用」。

---

## 動手練習

以下三個任務，各自分析並說明為什麼選擇或不選擇 SDD：

**任務 A**：你的公司要做一個新的 multi-tenant SaaS 授權系統，預計 5 位工程師在 6 個月內完成，之後需要長期維護。

**任務 B**：你需要把一批共 10,000 筆的舊訂單紀錄，從 CSV 轉換成新資料庫 schema 並匯入。這個 migration 跑一次就完成了。

**任務 C**：你想驗證「用戶願不願意為報表匯出功能付費」。你打算做一個假按鈕，點了顯示「即將推出」，記錄點擊數，下週看數據決定要不要做。

---

對每個任務，填寫這份分析表：

| 維度 | 任務 A | 任務 B | 任務 C |
|------|-------|-------|-------|
| 生命週期 | ? | ? | ? |
| 協作人數 | ? | ? | ? |
| 風險等級 | ? | ? | ? |
| 需求清晰度 | ? | ? | ? |
| **建議** | ? | ? | ? |

（參考答案：A → SDD 是最划算的前期投資；B → 完全不需要 SDD；C → 先 vibe coding，驗證後若決定實作再考慮 SDD）

---

## 本章重點整理

1. SDD 的成本不是「有沒有紀律」的問題，是「有沒有足夠長的回收期」的財務問題。

2. 五個不適合 SDD 的類別：純探索性工作、真正的一次性腳本、極小範圍改動、個人獨用工具、時效性極強的競速任務。

3. SDD 甜蜜點的四個交集：長壽（月以上）× 多人協作 × 需求已收斂 × 出錯代價高。

4. vibe coding 不是 SDD 的對立面，而是互補模式。探索期用 vibe coding，收斂後升格才用 SDD。

5. 規格深度可以分層：核心 domain 用 spec-anchored，輔助功能用 spec-first，一次性腳本完全跳過。

6. 規格越詳細不等於越好：指令詛咒（curse of instructions）讓過厚的規格反而降低 LLM 遵循品質。

7. 「升格」是最危險的時機：不要把 vibe coding 做出的 prototype code 當作 SDD 的基礎，應該重新從領域模型出發設計。

---

## 自我檢核

- [ ] 我能用自己的話解釋「SDD 的 break-even 點」是什麼意思，以及它為什麼決定了要不要用 SDD。
- [ ] 面試官問我「你在什麼情況下不會用規格驅動開發？」，我能舉出至少三個有說服力的類別，並說明原因。
- [ ] 我能解釋 Böckeler 的三層（spec-first / spec-anchored / spec-as-source）不只是工具分類，也可以是**同一個系統裡不同功能**的不同深度選擇。
- [ ] 我能說明為什麼「vibe coding 探索期寫的規格」不能直接當作生產 SDD 的起點。
- [ ] 我知道「指令詛咒（curse of instructions）」的意思，以及它為什麼讓「規格越厚越好」是個錯誤直覺。
- [ ] 我能解釋 Marc Brooker 的「往上拉（pulling up）」論點，以及它的前提假設在什麼情況下失效。

---

## 延伸閱讀

**Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall? — Colin Eberhardt, Scott Logic**
- URL: https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html
- 讀哪裡：先看「Plan」和「Implementation」兩節，拿到每個階段的時間數字和行數；再看「Reinvented Waterfall？」結論節。
- 和本章的關聯：本章「類別一到五」的「為什麼 SDD 是錯的」都以成本換算為基礎；Eberhardt 的 4h vs 23min 是目前最嚴謹的實測數字，讓成本不只是直覺。
- 警語：這是單一工程師、單一功能、特定版本（2025 年底）的實測，不是受控實驗，不能直接推廣。

**Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl — Birgitta Böckeler, martinfowler.com**
- URL: https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- 讀哪裡：先讀三個工具的「failure」段落（scope inflation、duplicate Markdown、non-determinism），再讀「Three levels of rigor」的分類框架。
- 和本章的關聯：本章的三層深度選擇直接來自 Böckeler 的 spec-first/spec-anchored/spec-as-source 分類；她的「小 bug 變 16 條 AC」案例支撐了本章「類別三」的論證。

**Spec-Driven Development: The Waterfall Strikes Back — François Zaninotto, Marmelab**
- URL: https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html
- 讀哪裡：讀「Failure modes」一節（Context Blindness、Markdown Madness、Double Code Review、Spec Non-Compliance）和結論的「Natural Language Development」提案。
- 和本章的關聯：本章踩雷集錦第 5 條（Markdown 不等於文件）和第 2 條（agent 不自動遵循規格）都有 Zaninotto 的例子作為背景；注意：他沒有親自跑這些工具，是二手觀察。

**Spec Driven Development isn't Waterfall — Marc Brooker**
- URL: https://brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html
- 讀哪裡：全篇不長，從頭讀；特別注意「pulling designs up, not up-front」和他對「人仍然必須擁有需求」的讓步。
- 和本章的關聯：本章進階延伸節討論的「Brooker 論點的前提假設失效時機」直接來自這篇；這是最強的 SDD 辯護，讀完才能完整理解本章的「有條件不用」而不是「全面否定」。

**Spec-Driven Development: the revenge of Waterfall or BDD taken to a new level? — Gojko Adzic**
- URL: https://www.linkedin.com/pulse/spec-driven-development-revenge-waterfall-bdd-taken-gojko-adzic-imquf
- 讀哪裡：讀中段「It does not, really」節（為什麼 SDD 目前沒有達到真正的 BDD living documentation 標準）和結尾的條件樂觀。
- 和本章的關聯：本章踩雷第 5 條（規格是工具追蹤用的不是給人讀的）直接引用 Adzic 的觀察；他作為 BDD/Specification by Example 的代表人物，對「什麼是真正的規格」有最嚴格的標準，讀完讓你知道現在的 SDD 工具距離那個標準還有多遠。

**How to write a good spec for AI agents — Addy Osmani, addyosmani.com**
- URL: https://addyosmani.com/blog/good-spec/
- 讀哪裡：讀「curse of instructions」和「you remain the filter」兩節。
- 和本章的關聯：本章「規格越詳細越好」踩雷的核心論據就是 Osmani 的「指令詛咒」；即使是規格的支持者也承認有這個效應，讓這個踩雷格外有說服力。

---

下一章討論在已確定要用 SDD 的情境下，如何把 SDD 真正織進一個多人工程團隊——從引進方式、規格 review 流程，到讓規格不腐化的工程設計。

→ [Ch 43 把 SDD 織進團隊](./43-sdd-in-teams.md)
