# Ch 5 — 迭代與敏捷：用快速回饋換掉大份前期規格

> **目標**：理解 2001 敏捷宣言的四個價值觀原文與它們的真正立場（不是反文件）、敏捷與瀑布的歷史張力、以及「working software over comprehensive documentation」這句話對規格驅動開發（SDD）帶來的哪些真實矛盾。

---

## 直覺：一個建築師的比喻

想像兩種蓋房子的方法。

**第一種**：花六個月畫完所有藍圖，驗收後才動第一根釘。業主在月五說「廚房我想換位置」——你的應答是「合約上沒有這條，變更費用 120 萬」。

**第二種**：先蓋一個可住的小房間（一個迭代），讓業主真的搬進去住兩週，然後問：「這裡夠嗎？通風怎樣？」收到意見後，再蓋下一間。

第二種的代價是：你在蓋第一間的時候，不知道整棟最後長什麼樣。而且如果第三間的牆承重設計跟第一間衝突，你可能需要局部拆掉重來。

這就是敏捷的核心賭注——**用早期的真實回饋換掉前期的大份假設規格**。

代價不是「不需要規格」，而是「規格必須能夠被修改，而且修改的代價要夠低」。

---

## 歷史脈絡：人們之前怎麼做，為什麼不夠好

在 1960–1990 年代，主流的開發方式是**文件驅動的順序流程**。每個 SDLC 階段產出一份文件，下個階段依賴上一份文件，像流水線一樣。

> 如果你對 SDLC 的各階段還不熟，先回看 [Ch 3 SDLC 到底是什麼](./03-sdlc.md)。

> 如果你對「瀑布」這個詞背後的歷史還有誤解，先回看 [Ch 4 瀑布的真相：Royce 1970 與一個誤會](./04-waterfall-myth.md)。

這種做法在受監管的領域（航太、國防）有真實的價值：可追溯性強、文件完整、合約清楚。問題在於，軟體的需求天生不穩定：

- 業主在看到第一個可動的版本之前，通常**不知道自己真正要什麼**。
- 技術環境變化速度遠快於一份前期規格能預料的範圍。
- 「分析癱瘓（Analysis Paralysis）」成為真實的組織症狀：團隊花幾個月決策，卻不交付任何可驗證的產出。

1990 年代，一批有經驗的軟體工作者開始各自實驗替代方案：Kent Beck 的極限編程（eXtreme Programming，XP）、Jeff Sutherland 與 Ken Schwaber 的 Scrum、Alistair Cockburn 的水晶方法（Crystal）等。這些方法各自有差異，但共享一個直覺：**讓可動的軟體承擔溝通的重責，而不是文件。**

---

## 敏捷宣言的原文與正確立場

2001 年 2 月，17 位軟體從業者在猶他州雪鳥（Snowbird）滑雪勝地聚會，寫出了《敏捷軟體開發宣言（Manifesto for Agile Software Development）》。

四個核心價值觀，逐字引用：

```
Individuals and interactions   over  processes and tools
Working software               over  comprehensive documentation
Customer collaboration         over  contract negotiation
Responding to change           over  following a plan
```

這四行後面接著一句至關重要、卻常被忽略的話：

> *"That is, while there is value in the items on the right,  
> we value the items on the left more."*

這句話是整個宣言的認識論底座。宣言**沒有說**右邊的東西沒有價值，它說的是：在兩者衝突時，優先左邊。

### 「working software over comprehensive documentation」的正確解讀

這句話被誤讀成「敏捷不需要文件」。實際上，它說的是：

- 如果你的文件很完整，但軟體跑不起來——你沒有交付任何東西。
- 如果你的軟體可動，文件稍薄——你至少有了一個可以溝通和驗證的基礎。

**不是**反對寫文件。**是**反對拿文件替代可動的軟體作為進度衡量標準。

這個區分在 SDD 的脈絡下非常重要，後面會仔細拆開。

---

## 迭代開發的機制：快速回饋環

敏捷方法的核心工程機制是縮短回饋循環（Feedback Loop）。

```
         ┌─────────────────────────────────┐
         │            一個 Sprint           │
         │                                 │
 需求 ──▶│ 設計 ──▶ 實作 ──▶ 測試 ──▶ 展示│──▶ 調整下一輪需求
         │                                 │         ▲
         └─────────────────────────────────┘         │
                                              業主回饋 ┘
```

傳統瀑布的回饋環：長達數月甚至數年（整個專案跑完才知道需求是否正確）。

敏捷的回饋環：1–4 週一個 Sprint，每個 Sprint 結束都有可展示的可動軟體。

這個縮短背後的賭注是：**需求錯誤的發現成本，遠低於長時間的錯誤累積成本**。這個賭注跟 Boehm 的變更成本曲線（Barry Boehm, *Software Engineering Economics*, 1981）直接對話——變更越早發現，代價越低。

> Boehm 曲線的具體數字有爭議，但方向性是有共識的。更詳細的分析在 [Ch 6 變更成本曲線——以及怎麼誠實引用它](./06-cost-of-change-curve.md)。

---

## 敏捷的十二原則：規格相關的部分

宣言的官網同時列了 12 條原則，與規格最相關的幾條：

**第一條**：「Our highest priority is to satisfy the customer through early and continuous delivery of valuable software.」

**第二條**：「Welcome changing requirements, even late in development. Agile processes harness change for the customer's competitive advantage.」

**第四條**：「Business people and developers must work together daily throughout the project.」

**第六條**：「The most efficient and effective method of conveying information to and within a development team is face-to-face conversation.」

注意第六條：**最高效的資訊傳遞是面對面對話，不是文件**。這在人與人協作時有合理的效率基礎——人可以及時澄清、問問題、理解語境。

但這個假設在 AI 代理出現後遇到根本性挑戰。

---

## 敏捷對 SDD 的張力：兩種不同的壓力

敏捷精神和規格驅動開發（SDD）之間存在一個**結構性張力**，不是簡單的對立，而是兩種壓力。

### 壓力一：敏捷壓縮前期規格，SDD 要求前期規格

敏捷的核心操作是：與其把需求說清楚，不如快速做一個版本讓業主反應。這假設人類開發者能從「做到一半的軟體」和「業主的抱怨」中，快速理解並調整。

SDD 的核心操作是：**在執行之前，把意圖說清楚**。因為 AI 代理不能像人類開發者一樣讀懂潛台詞和上下文，你必須明確說出來。

### 壓力二：敏捷信任面對面對話，SDD 信任文字規格

當 Kent Beck 說「面對面對話比文件高效」，他假設對話的另一端是能理解模糊語言的人類。

當 GitHub Spec Kit 的 spec-driven.md 說「The specification becomes the primary artifact」，它假設執行端是字面解讀指令的 LLM。字面解讀的系統**需要明確的規格**，無法依賴對話中的暗示和語氣。

### 壓力三：不是一場非此即彼的選擇

這兩個壓力不代表「敏捷錯了」或「SDD 就是回到瀑布」。

正確的讀法是：**SDD 繼承了敏捷的迭代精神，但把「快速回饋」的機制從人對人的對話，移到了人對規格的修訂**。

你仍然迭代。仍然歡迎變更。只是你的工作產出不再是「讓開發者理解的暗示」，而是「讓 AI 代理能精確執行的規格文件」——而這份規格本身是可以版本控制、可以迭代修改的。

---

## 對比：兩種模式的工作流差異

| 維度 | 經典敏捷（人 → 人） | SDD（人 → 規格 → AI） |
|------|-------------------|----------------------|
| 需求傳遞方式 | User Story + 面對面澄清 | 結構化規格文件（requirements.md / spec） |
| 迭代單位 | Sprint（1–4 週） | 規格版本 + 任務週期 |
| 變更機制 | 下個 Sprint 加入 backlog | 修改規格，重新驅動 AI 任務 |
| 對模糊性的處理 | 人類開發者自行推斷填補 | 必須在規格中明確，否則 AI 填錯或失敗 |
| 文件定位 | 支援 working software，非主角 | 規格是主角，code 是衍生物 |
| 知識保存 | 部分留在人腦和對話記錄中 | 應留在規格中（防止意圖遺失） |
| 對 AI 代理的依賴 | 無或低 | 高（AI 執行者讀規格生成程式碼） |

---

## 一個具體的迭代例子：訂單通知功能

假設你要做一個「訂單成立時發 email 通知」的功能。

**敏捷做法（人對人）**：

```
Sprint 1 規劃會議：
  PM 說：「訂單成立後通知用戶。」
  開發者說：「用 email 還是 SMS？」
  PM 說：「先 email 就好。」
  開發者說：「模板誰給？」
  PM 說：「我下午傳給你。」

Sprint 1 結束：有可動的 email 通知，PM 看到後說：
  「Subject 應該要有訂單號。」
  「應該要有退貨連結。」

Sprint 2 改。
```

**SDD 做法（人對規格對 AI）**：

```markdown
# Feature: 訂單確認通知

## 需求（EARS 格式）

WHEN 訂單狀態變更為 "confirmed"
THE SYSTEM SHALL 發送確認 email 至訂單關聯的用戶 email 地址
WITH SUBJECT "訂單確認 #[order_id] — [store_name]"
WITHIN 30 秒

WHEN email 發送失敗（SMTP 錯誤或 timeout）
THE SYSTEM SHALL 重試最多 3 次，間隔 5 分鐘
AND 在第 3 次失敗後 記錄至 error_log，狀態標為 "notification_failed"

## 不在範圍內（Out of Scope）

- SMS 通知
- 推播通知
- 用戶自訂模板（Phase 2 考慮）
```

這份規格在 Sprint 1 就確立了「訂單號在 Subject」「重試機制」「30 秒 SLA」等細節，AI 代理可以從這份規格生成測試和實作，而不是在對話中一點一點澄清。

差異不是「SDD 一次就對」，而是「SDD 把澄清的工作，從執行階段前移到規格撰寫階段」。

迭代仍然存在——只是你迭代的是規格，然後重新跑 AI 任務。

---

## 踩雷集錦

### 雷 1：「敏捷說不要文件，所以我不需要寫規格」

**錯誤直覺**：宣言說 working software over comprehensive documentation，代表文件是敵人，能省就省。

**正確認識**：宣言說的是「在兩者衝突時優先 working software」，不是「文件沒有價值」。宣言本身有 12 條原則，沒有一條說「不要文件」。缺乏任何規格的開發，在 AI 代理出現後，代表你的意圖完全依賴 LLM 的猜測——而 LLM 的猜測在複雜邏輯上不穩定。

---

### 雷 2：「SDD 就是回到瀑布，要前期把所有需求寫清楚才能動」

**錯誤直覺**：SDD 要寫規格，瀑布也要寫規格，所以 SDD 就是瀑布。

**正確認識**：SDD 的規格是活文件（living document），是迭代修改的起點，不是一次定稿的合約。你可以在第一個 Sprint 只有一份薄薄的 requirements.md，跑完第一輪後更新它，再進下一輪。差別在於：你**主動維護**規格作為意圖的來源，而不是只看程式碼和對話記錄。

---

### 雷 3：「敏捷十二原則說面對面溝通最高效，所以 AI 代理不適合敏捷」

**錯誤直覺**：面對面溝通的假設說明 AI 根本不在敏捷的設計範圍內，兩者不相容。

**正確認識**：敏捷原則是針對人對人協作優化的，這沒有錯。但 AI 代理是執行端，不是溝通端——你仍然和業主面對面溝通，只是把溝通的產出明確化為規格，再讓 AI 執行規格。面對面溝通的價值（快速澄清、理解語境）並沒有消失，只是它的輸出需要進一步被結構化。

---

### 雷 4：「只要我迭代夠快，不需要規格，AI 自己就能猜到我要什麼」

**錯誤直覺**：迭代快 + 給 AI 更多 prompt = 不需要規格，AI 會學到我的意圖。

**正確認識**：LLM 沒有持續的記憶——每次對話結束，context 就清空。沒有明確的規格，AI 代理每次任務都是從頭猜意圖。在複雜功能（有業務規則、邊界情況、非功能需求）上，猜測成功率會急速下降。正是因為你的意圖無法被 AI 記住，**規格成為唯一可靠的意圖保存機制**。

---

### 雷 5：「敏捷宣言說歡迎變更，所以 SDD 的規格只要有需求就改」

**錯誤直覺**：歡迎變更 = 規格可以隨時大改，AI 重跑一次就好，沒有成本。

**正確認識**：規格變更有連鎖成本。如果你在 requirements.md 改了核心行為定義，但沒有更新 design.md 和 tasks.md，AI 代理執行的任務會基於過時的設計，產出衝突的程式碼。SDD 的歡迎變更，是歡迎**有紀律的變更**——改一個地方，相關規格同步更新。這正是 spec drift（規格漂移）問題的根源。

---

## 進階延伸：敏捷宣言的歷史侷限與 AI 時代的重新詮釋

敏捷宣言的 17 位簽署者，包括 Kent Beck（XP 創始人）、Martin Fowler（ThoughtWorks）、Ward Cunningham、Ron Jeffries、Jim Highsmith、Alistair Cockburn、Jeff Sutherland 等，都是在 1990 年代末到 2001 年的環境下思考問題的。

當時的執行端完全是人類開發者。人類開發者的特性：
- 能從片段資訊推斷意圖
- 能在對話中即時澄清
- 能從程式碼和測試中反向理解設計

AI 代理的特性（2025–2026 年的現實）：
- 對模糊指令的推斷有時正確，有時系統性偏差
- 沒有持續記憶，每個任務都是全新的 context
- 不能主動發現潛台詞、不能主動要求澄清（或者澄清效果有限）

Martin Fowler 的網站上，Birgitta Böckeler（ThoughtWorks）在 2025 年 10 月的分析直接點出這個張力：SDD 的工具「can combine the downsides of both MDD and LLMs: Inflexibility and non-determinism」。

**Model-Driven Development（MDD）**是 1990 年代到 2000 年代的另一個嘗試：把設計模型（UML 等）作為主要產出，程式碼從模型自動生成。MDD 失敗的主要原因是：模型和程式碼一旦開始分叉，同步成本極高；工具生成的程式碼品質不可控；開發者只能改生成碼，反向同步模型幾乎不可能。

SDD 面臨相同的歷史警示。差異是：2025 年的 LLM 生成程式碼的品質遠高於 2000 年代的 MDD 工具，**但相同的規格漂移問題依然存在**。

這不意味著 SDD 沒有前途，而是意味著「規格同步」需要被視為第一等公民的工程問題，而不是事後補救。

---

## 動手練習

**練習目標**：把一個模糊的敏捷 User Story 轉化為 SDD 可用的規格，並識別出轉化過程中需要明確的決策點。

**材料**：以下是一個典型敏捷 Sprint 規劃中的 User Story：

```
作為一個電商用戶，
我想要在結帳時看到預估配送時間，
以便我能決定是否值得等待。
```

**步驟**：

1. 列出這個 User Story 中**隱含但未說明的假設**（至少找出 5 個）。
   例：「預估配送時間」——誰來計算？基於什麼資料？精確到分鐘還是天？

2. 對每個假設，寫出你需要從業主/PM 那裡確認的問題。

3. 假設你得到了以下回答，把這個 User Story 改寫成 EARS 格式的需求規格：
   - 配送時間由第三方物流 API 提供
   - 在商品頁和購物車頁都要顯示
   - API 超時（> 2 秒）時顯示「配送時間請聯繫客服」
   - 精確到「工作天」（不是精確小時）

4. 識別哪些決策你仍然需要澄清，哪些你選擇先做一個版本再看業主反應（敏捷的「剛好夠的規格」）。

**反思問題**：如果你直接把原始 User Story 餵給一個 AI 代理，哪些假設最可能被猜錯？為什麼？

---

## 本章重點整理

- 2001 敏捷宣言有四個核心價值觀，每個都是「左 over 右」，不是「右邊沒有價值」。
- 「working software over comprehensive documentation」的正確立場：反對用文件代替可動軟體作為進度衡量，不是反對文件本身。
- 敏捷的核心機制是縮短回饋環——讓真實的可動軟體儘早接受業主驗證。
- SDD 繼承了敏捷的迭代精神，但把執行端從人類開發者換成了 AI 代理——而 AI 代理需要明確的規格，無法依賴面對面對話的暗示。
- 兩者的張力不是非此即彼：你可以在敏捷迭代的框架內，用 SDD 的方式結構化每個迭代的規格產出。
- 歷史警示：MDD（Model-Driven Development）嘗試過類似的路，規格與程式碼的同步是最終的失敗點。SDD 需要把「規格漂移」當作第一等工程問題處理。

---

## 自我檢核

- [ ] 不看書，用自己的話說出敏捷宣言四個價值觀的原文（英文），以及那句常被省略的結語。
- [ ] 如果面試官問「敏捷不是說不需要文件嗎？那 SDD 不是回到瀑布了？」你會怎麼回答？試著用三句話以內說清楚。
- [ ] 用自己的話解釋「為什麼敏捷的面對面溝通原則，在 AI 代理出現後需要被重新詮釋」。
- [ ] 識別出敏捷迭代模式（Sprint 回饋環）和 SDD 迭代模式（規格修訂環）的對應關係，以及它們的差異點在哪裡。
- [ ] 解釋「MDD（Model-Driven Development）」的歷史失敗，以及它對 SDD 的哪個警示最相關。

---

## 延伸閱讀

**1. Manifesto for Agile Software Development（官方原文）**
- URL：https://agilemanifesto.org/
- 讀什麼：四個價值觀的逐字原文，加上那句常被省略的結語。然後點進去讀 12 條原則。
- 為什麼要讀：你對宣言的一切理解都應該從原文出發，而不是二手摘要——包括本章的摘要。
- 本章關聯：本章引用的四個價值觀和結語，就是從這裡來的。

**2. A Brief History of the Waterfall Model: Past, Present, and Future（arXiv 2025）**
- URL：https://arxiv.org/html/2510.03894v3
- 讀什麼：「Royce's 1970 Formalization」一節，理解迭代開發的歷史起點。
- 為什麼要讀：敏捷不是無中生有——它的迭代精神其實在 Royce 1970 就已經有了雛形。
- 本章關聯：本章的「歷史脈絡」段落的學術依據。

**3. No Silver Bullet — Essence and Accident in Software Engineering**
- URL：https://www.cin.ufpe.br/~phmb/ip/MaterialDeEnsino/BrooksNoSilverBullet.html
- 讀什麼：「Essence」一節，特別是「The hardest single part of building a software system is deciding precisely what to build」這段。
- 為什麼要讀：理解為什麼需求（規格）比程式碼更難——這是整個 SDD 課程的哲學基礎，也是敏捷試圖「讓業主快速確認需求」的背後動機。
- 本章關聯：本章的「SDD 繼承敏捷精神但強化規格」論點的思想淵源。

**4. Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl**
- URL：https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- 讀什麼：Birgitta Böckeler（ThoughtWorks）對三種工具的分類（spec-first / spec-anchored / spec-as-source），以及她對 MDD 歷史警示的分析。
- 為什麼要讀：這篇文章是 2025 年對 SDD 最平衡的批判性分析，直接點名 MDD 的歷史連結。
- 本章關聯：本章「進階延伸」段落的 MDD 警示和 Böckeler 引用的出處。

**5. github/spec-kit — spec-driven.md（官方方法論文件）**
- URL：https://github.com/github/spec-kit/blob/main/spec-driven.md
- 讀什麼：「The specification becomes the primary artifact」段落，以及它如何重新定義「maintaining software means evolving specifications」。
- 為什麼要讀：理解 SDD 的旗艦工具如何把「規格作為主要產出、程式碼作為最後一哩」的理念，落實成工作流。
- 本章關聯：本章對比表格中「文件定位」那一行的依據。

---

下一章我們要正面處理本章埋下的一個懸念——變更成本曲線。Boehm 的曲線說「越早修越便宜」，這是敏捷（快速回饋）和 SDD（前期規格）兩者都引用的基礎，但這個曲線的數字到底可不可信？怎麼誠實地引用它？

→ [Ch 6 變更成本曲線——以及怎麼誠實引用它](./06-cost-of-change-curve.md)
