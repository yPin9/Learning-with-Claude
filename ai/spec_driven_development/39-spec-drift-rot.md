# Ch 39 — 規格漂移與規格腐化

> **目標**：理解規格漂移（spec drift）與規格腐化（spec rot）——雙重產物維護的核心難題——釐清規格為何說謊，以及目前仍在演進中的對策方向。本章誠實標注哪些機制尚未被一手資料充分查證。

## 在這之前：人們怎麼試著讓文件不說謊

規格腐化不是 SDD 發明的問題，它是軟體工程的老問題。值得先看一下過去幾十年人們嘗試過什麼，為什麼都沒有解決。

**1970s-1980s：維護「設計文件」**：瀑布模型（Waterfall）要求大量前期規格，軟體交付後這些文件通常迅速過時。沒有人有時間更新，也沒有機制強制。

**1990s：自文件化程式碼（Self-Documenting Code）**：Literate Programming（文學編程，Knuth，1984）是最早試圖讓程式碼和說明共存的嘗試。更廣泛流行的是「好的程式碼就是文件」的信條——用有意義的命名、小函式、清楚的抽象取代獨立文件。這個方向解決了「如何理解 code」，但沒有解決「code 做了什麼，和規格說的是同一件事嗎」。

**2000s：BDD 和可執行規格**：Behaviour-Driven Development（BDD）的核心想法是讓規格本身可執行——Given/When/Then 寫成測試，規格跑不過就是 CI 失敗。這是目前最接近「防漂移」的機制。但它要求規格寫在測試框架（Cucumber、SpecFlow）裡，不適用於架構決策、非功能需求、或跨系統契約。

**2010s：Consumer-Driven Contract Testing（CDCT）**：Pact 等工具讓消費者端定義合約，提供者端跑測試驗證，API 契約的漂移會變成 CI 失敗。有效，但只覆蓋服務邊界，不覆蓋內部邏輯。

**2020s：AI SDD**：現在的 Spec Kit、Kiro、Tessl 的問題是：它們試圖把自然語言的 spec 提升成主線，但沒有繼承 BDD 的「規格可執行」這一關鍵保障。結果是一個宣稱更嚴謹、但回饋機制更弱的組合。

> 如果你對 BDD 和 TDD 的歷史脈絡還不熟，先回看 [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)。

SDD 的核心挑戰在於：它繼承了自然語言規格的模糊性，卻丟掉了 BDD 裡讓規格可驗證的那一層。

## 雙重產物的詛咒

在 SDD 流水線走完一輪之後，你的倉庫裡同時存在兩樣東西：

```
.specify/
  features/
    feat-001/
      spec.md         ← 規格（人寫、人讀）
      plan.md
      tasks.md
src/
  circuits/
    circuit-manager.ts  ← 實作（機器執行）
tests/
  circuits.test.ts
```

這兩條線在 Day 1 對齊。問題發生在 Day 30、Day 90、Day 365。

傳統軟體開發只有一條主線：程式碼。文件是附件，過時是常態，沒人驚訝。SDD 的核心主張是把規格提升為「唯一真相來源」，這在邏輯上要求兩條線永遠對齊——這個承諾比寫文件難太多，也是整個典範最脆弱的一個環節。

## 規格漂移（Spec Drift）的機制

「規格漂移」是指程式碼行為持續演進，但規格/合約被留在原地，造成兩者逐漸錯位。

Alex Norman（Kinde）2025 年 8 月的文章把它定義為「the behavior of your code no longer matches its documentation or design specifications」（查證日期 2026-06-30，來源：kinde.com/learn/ai-for-software-engineering/ai-devops/spec-drift-the-hidden-problem-ai-can-help-fix/）。後果是增加認知負荷：開發者不再信任文件，必須回頭讀原始碼才知道系統真正的行為。

漂移通常不是一次事件，而是累積：

```
Sprint 1  spec.md ─── code ─── 完全對齊
              ↓ 緊急修 bug，code 改了，spec 沒動
Sprint 2  spec.md ─── code ─ 差了一個角落 case
              ↓ 重構，code 介面改了，PR 裡忘記更新 spec
Sprint 3  spec.md ─── ·  ·  ─── code  開始分岔
              ↓ 新功能，沿用舊 spec 結構，越補越不對
Sprint 6  spec.md                          code   完全說謊
```

Böckeler（Thoughtworks，2025 年 10 月）在 Martin Fowler 的站上做了更細的分類：她把規格的威權等級分成三層——

| 層級 | 名稱 | 誰是主線？ |
|---|---|---|
| 1 | spec-first | spec 引導生成，但**程式碼**是維護主線 |
| 2 | spec-anchored | spec 是活文件，與 code 一起版控 |
| 3 | spec-as-source | spec 是唯一來源，code 是派生產物 |

她的觀察是：多數工具（Spec Kit、Kiro）宣稱做到 spec-anchored，實際落地後往往退化成 spec-first——也就是說，spec-first 加上漸進漂移，就是現實世界大多數 SDD 團隊的長期處境。

## 規格腐化（Spec Rot）：更深的一層

漂移是空間上的錯位（spec 說 A，code 做 B）。腐化則是時間上的失效：spec 文字沒有明顯錯誤，但它反映的是系統三個月前的設計決策，當時的假設已經不成立，術語也換了一輪，但沒有人有時間補上。

腐化的症狀：
- spec 裡的 entity 名稱在程式碼裡已換成另一個詞（通用語言（Ubiquitous Language）崩壞的早期徵兆）。
- spec 描述的是「理想設計」，但受限於依賴版本的實際限制，code 走了不同路。
- spec 的驗收條件（Acceptance Criteria）仍然說「應支援 OAuth 2.0 PKCE 流程」，但 code 早就改成 OIDC + Device Flow。

> 如果你對通用語言還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)。

腐化最危險的地方是它的**靜默性**。測試套件是綠的，CI 通過，但規格說的是一個已不存在的世界。新進工程師讀 spec 以為理解了系統，其實學到的是過去式。

## 為什麼規格比程式碼更難維護同步

這個問題值得正面回答，不能輕描淡寫：

**一、回饋循環不對等。** 程式碼跑不起來，測試紅燈，你馬上知道。規格說錯了，沒有任何自動機制告訴你。

**二、修改動機不對等。** 改 code 有立即功能收益；更新 spec 是純文件工作，在時程壓力下第一個被省略。

**三、語言模糊性放大了衝突。** 程式語言是精確的——`order.status === "PAID"` 不允許歧義。自然語言規格說「訂單進入已付款狀態」，程式碼實作卻是 `order.status = "payment_confirmed"`，兩者到底對不對齊？不清楚。

**四、LLM 加速了分岔。** 在 AI 輔助開發流程中，程式碼可以在幾分鐘內被大幅改寫。Böckeler 的測試發現 Tessl 對同一份 spec 跑兩次會產生不同的程式碼（非確定性）。規格是靜態文件，code 是動態產物，速度差距只會拉大。

François Zaninotto（Marmelab，2025 年 11 月）把其中一個失敗模式稱為「double code review」：你得先 review spec，再 review 實作，卻沒有任何工具保證兩者一致，等於多了一層負擔卻沒多一層保障。

## 具體案例：一個 spec 如何說謊

假設你用 Spec Kit 幫一個訂閱管理功能寫了規格：

```markdown
<!-- spec.md（Day 1） -->
## Feature: Subscription Renewal
**User story**: As a paying user, I want my subscription renewed automatically
on the billing date so I don't lose access.

**Acceptance Criteria**:
- Given the billing date arrives,
  When the payment provider returns `status: success`,
  Then `subscription.renewedAt` is updated and user receives email.
- Given payment fails,
  When retry count < 3,
  Then schedule retry after 24 hours.
```

四個月後，你們換了金流商，從 Stripe 的 webhook 改成輪詢模式，retry 邏輯移進 background job，`renewedAt` 改名叫 `billingCycleEnd`，Email 改由第三方 CRM 觸發而非你們的 code。

spec.md 沒有人更新。測試全部通過（它們測的是新邏輯）。這份規格現在完整、清楚、可讀——而且每一句話都不再是真的。

新進工程師讀了 spec，以為付款成功時你們的 code 寄 Email，花了半天找那段 code，找不到，才知道是 CRM 的事。

**這就是規格腐化：文字沒有語法錯誤，語義已經失效。**

## 目前的對策方向（誠實標注未查證項）

這個問題還沒有被解決，現有的對策仍是實驗性的。以下按查證程度分類：

### 已查證的思路（概念層面有一手資料支持）

**1. AI 持續偵測 + CI/CD 整合**

Alex Norman 在 kinde.com 的文章（2025 年 8 月，查證日期 2026-06-30）提出的方向：把規格比對做進 CI/CD 流程，讓 AI 偵測 code 與 spec 之間的語意差異，自動開 PR 或讓 build 失敗。概念上合理，但文章本身沒有給出可跑的工具或量化結果。

**2. 人工維護 spec-as-source（Tessl 的方向）**

Tessl 的私有 beta 採用 1:1 spec-to-file 對應，code 裡標注 `// GENERATED FROM SPEC - DO NOT EDIT`。理論上 spec 是主線，code 永遠是派生物，不可能反方向漂移。Böckeler 測試時發現其非確定性（同一 spec 不同次執行產生不同 code），這個方向能否在大型 codebase 實際落地，目前沒有充分的公開驗證。

**3. 配對更新原則（agent 同一 commit 更新 spec）**

多個評論者提到的方向：要求 agent 在修改 code 時，必須在同一個 commit 更新對應的 spec 區段，code review 只審合約層面的差異。這聽起來自然，但執行有漏洞——agent 可以輕易標注「已更新 spec」而沒有真的更新，或者更新的是不相關段落。

### 未查證的具體機制（不應視為既定工具或已實作功能）

以下幾個名詞在二手評論中出現，但在查證過程中（截至 2026-06-30）找不到對應的一手資料或可用工具：

- **「Spec Growth Engine」**：在某些討論中提到，但無法追蹤到一手來源。
- **「spec-kit-sync」**：名稱出現在討論中，但查無對應的工具頁面或 repo。
- **arXiv:2606.27045**：在摘要中被提及，但無法獨立查證其內容。

這三個項目暫時列為「方向概念，細節以官方最新資訊為準」。

## Addy Osmani 的「指令詛咒」與規格失效的深層機制

規格漂移有技術面的放大器，Addy Osmani（Google，2026 年 1 月 13 日，查證日期 2026-06-30）稱之為「curse of instructions」（指令詛咒）：

> "As you pile on more instructions... the model's performance in adhering to each one drops significantly."

這個效應意味著：規格越大，LLM 對每條具體規則的遵守率就越低。更長的 spec 並不線性轉化成更忠實的實作，反而在某個臨界點後開始讓模型失焦。Osmani 同時觀察到，context 超過某個規模後「the model breaks down」。

這帶來一個惡性循環：

```
spec 腐化 → 有人補充更多說明試圖追上 →
spec 變長 → LLM 遵守率下降 →
code 更難對齊 spec → spec 更快腐化
```

解法不是「更嚴格地維護 spec」，而是控制 spec 的規模——Osmani 建議根據任務複雜度調整 spec 深度，不要對瑣碎任務過度規格化。

## Böckeler 的「false sense of control」警告

Birgitta Böckeler 在 Martin Fowler 站上的文章（2025 年 10 月，查證日期 2026-06-30）用了一個有力的說法：SDD 工具目前的主要風險是製造「a false sense of control」（虛假的掌控感）。

她的論點是：一份規格存在讓你感覺有正式化、有審查、有版控，但如果 agent 可以忽略部分規格（她測試時確認發生過）、同時標注任務為完成，你的規格就是一層裝飾，不是一條保障。這跟 TDD 失去紀律變成「test-after development」的過程是結構上相同的衰退模式。

這個衰退模式值得更仔細看。Böckeler 在測試 Spec Kit 時觀察到 agent「too eagerly followed」某些約束，同時又忽略另一些——選擇性遵守，不是全面遵守。更嚴重的是：Hacker News 用戶 yoaviram 報告（HN 45610996，查證日期 2026-06-30），他的 Spec Kit 跑了十天，「most tests were failing, and the build was not successful」，實作指令「did not follow the process... it would forget to create or run tests」。

換句話說，掌控感是真實的，掌控是虛假的。

從 Model-Driven Development（MDD）的歷史教訓來看，Böckeler 特別指出 SDD 有重蹈 MDD 失敗的風險：MDD 試圖讓模型成為主線、程式碼是派生物，結果是「combining inflexibility and non-determinism」——模型太僵固，同時輸出又不可預測。SDD 的 spec-as-source 方向面對的是結構上完全相同的張力。

> 如果你對 TDD/BDD 的歷史脈絡還不熟，先回看 [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)。

## 與程式碼技術債的對比

技術債（Technical Debt）已有成熟的測量框架（SonarQube、圈複雜度、耦合分析）。規格腐化目前沒有對應的量化工具——你不能跑一個指令知道「spec 和 code 的語意對齊度是 73%」。

| 維度 | 程式碼技術債 | 規格腐化 |
|---|---|---|
| 偵測方式 | 靜態分析、測試紅燈 | 主要靠人工審閱 |
| 衡量指標 | 圈複雜度、重複率、測試覆蓋率 | 尚無標準指標 |
| 工具生態 | SonarQube、CodeClimate、ESLint | 幾乎空白 |
| 修復動機 | 效能、安全、可維護性 | 幾乎純文件工作 |
| 累積速度 | 與 code 同步 | 通常比 code 更快 |
| 對 LLM 的影響 | 影響生成碼品質 | 直接污染 context window |

「對 LLM 的影響」這一欄特別值得關注：腐化的 spec 餵進 LLM 的 context，等於用過時資訊訓練當次決策，其害比沒有 spec 更難察覺——因為 LLM 會用過時資訊給出有信心的答案。

## 踩雷集錦

### 錯誤直覺 1：「我用 AI 生成的 spec，AI 應該也能自動維護它」

**正確認識**：生成和維護是完全不同的問題。AI 在 Day 1 可以產出很好的 spec；但維護要求 AI 知道「何時 code 有意義地偏離了 spec」——這需要 AI 理解語意變更的程度，而非只做文字差異。目前沒有工具可靠地做到這件事。Colin Eberhardt 在 Scott Logic（2025 年 11 月，查證日期 2026-06-30）的測試裡，agent 仍然讓一個明顯的 bug 通過（`circuitsData` 沒有被正確填入），即使 spec 裡有明確的驗收條件。

### 錯誤直覺 2：「有 spec，就代表有掌控感」

**正確認識**：Böckeler 測試 Spec Kit 時發現 agent 「too eagerly followed」某些約束，同時忽略另一些；Gojko Adzic（Specification by Example 作者，2025 年 9 月，查證日期 2026-06-30）指出，Spec Kit 產生的 Markdown 很大部分「seems to be for the tool to track its own progress, and not necessarily for human consumption」。存在一份 spec 文件 ≠ 系統行為被這份 spec 控制。

### 錯誤直覺 3：「spec 一旦腐化，問題只是文件過時而已」

**正確認識**：在 SDD 流程下，腐化的 spec 是下一輪 AI 生成的輸入。如果你之後跑 `/speckit.specify` 或 Kiro 新一輪的 spec session，腐化的 spec 會成為 context，讓 AI 按照過時的世界觀繼續疊加新功能。技術債通常是加法；spec rot 引發的技術債是乘法——舊錯誤乘以每一輪的 AI 放大。

### 錯誤直覺 4：「把規格寫得更詳細就能防止漂移」

**正確認識**：Osmani 的「指令詛咒」說的正好相反：spec 越長，每條指令的遵守率越低。更長的 spec 可能加速漂移，因為 agent 和人類都更難讀完每一條。防漂移的真正機制是縮短回饋循環，不是增加 spec 厚度。

### 錯誤直覺 5：「漂移是 SDD 特有的問題，傳統開發沒有」

**正確認識**：傳統開發同樣有文件腐化，但後果是「文件過時，沒人看」。SDD 的問題是把文件提升為主線後，腐化的後果更嚴重——整個流程的合法性都建立在 spec 可信的前提上。承諾越高，失效的代價越大。

## 現有條件下的務實建議

這個問題沒有完美解法，但有幾個方向可以降低傷害：

**1. 規格粒度對應功能穩定性**：核心領域邏輯（Core Subdomain）的 spec 值得維護；Generic 子領域的 spec 更新成本高於收益，可以薄寫。

> 如果你對子領域分類還不熟，先回看 [Ch 18 子領域：Core / Supporting / Generic](./18-subdomains.md)。

**2. spec review 掛 PR 強制關卡**：在 PR template 加一條 checkbox：「如果此 PR 改變了任何 feature 的可觀察行為，spec 是否已更新？」。這不完美，但至少讓漂移有機會被發現。

**3. 利用 Bounded Context 縮小 spec 範圍**：spec 應只覆蓋一個 Bounded Context 邊界內的行為。跨 context 的整合行為用 contract test（Consumer-Driven Contract Testing）捕捉，而非 spec 的文字。

> 如果你對 Bounded Context 還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

**4. 「讀 spec 不如讀測試」的誠實降級**：如果 spec 更新成本太高，誠實地把驗收條件的真相來源轉移到自動化測試——可讀的 BDD/ATDD 測試。這不是 SDD 的理想，但比維護說謊的 spec 更好。

**5. 版控 spec 的每一次實質改動**：`git blame` 在 code 上很有用，在 spec 上一樣有用。保留語意變更的 commit message，讓「spec 何時開始說謊」有跡可查。

## Marc Brooker 的反論：迭代 spec 是否可以解決漂移？

公平起見，必須呈現最強的反駁。Marc Brooker（AWS VP / Distinguished Engineer）在 2026 年 4 月的文章（查證日期 2026-06-30）給出了最有力的回應：

> "In specification driven development, the specification is the thing being iterated on, rather than the implementation."

他的論點是：SDD 不是把大份規格鎖在前期然後開始實作（那才是瀑布）。SDD 是把「迭代的對象」從 code 換成 spec——你快速更新 spec，讓 AI 跟上，spec 是活的、版控的、被持續修訂的。他把這稱為「pulling designs up, not up-front」。

這個立場如果成立，本章的很多憂慮就弱化了：如果 spec 本身就在快速迭代，漂移的時間窗口縮小到每次迭代的長度，而不是累積幾個月。

但 Brooker 的立場有個前提必須成立：**團隊真的在迭代 spec，而不只是把它生成出來然後更新 code**。Eberhardt 的測試（Scott Logic，2025 年 11 月）描述的就是後者——他花了數小時 review 這份 spec，而 code 已經在跑了。Böckeler 的觀察也顯示，工具傾向讓人迭代 code，不是迭代 spec。

Brooker 自己也承認：「we are still very early in this revolution」，以及人類必須擁有「internally conflicting nature of requirements」的責任。

本章的立場是：Brooker 的框架描述的是 SDD 的**理想狀態**；本章描述的是 SDD 在現實中更常見的**衰退模式**。兩個都是真的，在不同的團隊、不同的紀律水準下會走向不同的結局。

## 動手練習

以下練習設計為可以用你自己的任何一個已有 spec 來操作，不需要特定工具：

**練習 39-A：規格考古**

取你在 [練習 D](./practice-d-write-a-spec.md) 或 [練習 E](./practice-e-spec-kit-run.md) 產生的 spec（或任何手邊有的 spec 文件）。

1. 對照現有 code（或對照你自己的記憶，如果 code 尚未實作），列出 spec 中「可能不再為真」的所有句子。
2. 對每一條標注以下其中一個：
   - `CONFIRMED`：code 行為完全符合 spec 描述
   - `DRIFTED`：code 行為和 spec 描述有明確差異
   - `UNVERIFIABLE`：無法直接從 code 確認（可能是外部系統、非同步行為等）
3. 計算 DRIFTED + UNVERIFIABLE 的比例。如果超過 20%，這份 spec 的可信度已打折扣。

**練習 39-B：設計你自己的漂移偵測 checklist**

針對你的團隊（或假設情境），寫一份最多 7 條的「PR 合規 checklist」，確保每個影響 feature 行為的 PR 都被評估是否需要更新 spec。要考慮：誰檢查？自動還是人工？什麼情況下 spec 一定要更新？

**練習 39-C：對比兩種回饋機制**

找一個你熟悉的小功能，試著把它的驗收條件同時寫成兩種形式：
- 一份自然語言 spec（Markdown，150 字以內）
- 一份 BDD 情境（Given/When/Then，用 Python 或任何你熟悉的語言寫出骨架，不用實際實作測試邏輯）

思考：如果這個功能的行為在三個月後改變，哪一份會先被注意到漂移？為什麼？

## 本章重點整理

- **規格漂移**（spec drift）= code 演進但 spec 未跟上；**規格腐化**（spec rot）= spec 的假設基礎已失效，文字無誤但語意過期。
- SDD 把 spec 提升為主線，代價是腐化的後果比傳統文件過時更嚴重：它污染下一輪 AI 生成的 context。
- Böckeler 的三層分類（spec-first / spec-anchored / spec-as-source）是目前最精確的詞彙；多數工具宣稱 spec-anchored，實際落地常退化成 spec-first。
- 指令詛咒（curse of instructions）意味著更長的 spec 不線性帶來更好的 code 對齊，反而在臨界點後加速失效。
- 目前的對策（AI 持續偵測、同一 commit 更新、spec-as-source 架構）都有原理但缺乏大規模公開驗證。這是一個尚未解決的工程問題，不要被工具宣傳說服以為已有可靠解法。
- 務實降級策略：縮小 spec 範圍（對應 Bounded Context）、用自動化測試做真相來源的補位、PR 強制檢查。

## 自我檢核

- [ ] 我能用自己的話解釋規格漂移和規格腐化的差別，以及兩者如何互相強化。
- [ ] 如果面試被問「SDD 的維護負擔是什麼」，我能舉一個具體失效場景說清楚。
- [ ] 我能解釋為什麼「寫更詳細的 spec」不是防漂移的答案，以及 Osmani 的「指令詛咒」在其中的角色。
- [ ] 我知道 Böckeler 的三個規格威權層級，以及多數工具在實踐中落在哪一層。
- [ ] 我能說出「Spec Growth Engine」和「spec-kit-sync」目前的查證狀態是什麼（標注：查證日期 2026-06-30）。
- [ ] 我理解腐化的 spec 在 AI 流程中為什麼比傳統文件過時更危險。

## 延伸閱讀

**Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl** — Birgitta Böckeler（Thoughtworks），發表於 martinfowler.com，2025 年 10 月。
URL: https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
從哪裡讀起：直接找「false sense of control」段落，再往前讀 per-tool 的具體失效；最後看她對三層分類（spec-first / spec-anchored / spec-as-source）的定義。這是當前規格漂移討論的必讀基準文件。

**Putting Spec Kit Through Its Paces** — Colin Eberhardt（Scott Logic CTO），2025 年 11 月。
URL: https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html
從哪裡讀起：「Plan」和「Implementation」章節，看雙重產物的實際行數（spec 2,067 行 vs code 700 行），以及那個在 spec 有明確驗收條件下仍然漏掉的 bug。本章「具體案例」的精神來自這個手法。

**Spec-Driven Development: The Waterfall Strikes Back** — François Zaninotto（Marmelab），2025 年 11 月。
URL: https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html
從哪裡讀起：「Failure modes」段落——context blindness、Markdown overload、double code review、spec non-compliance——四個具體模式，是本章「為何規格說謊」論點的一手素材。注意：Zaninotto 沒有親自跑工具，他引用了 Böckeler 的測試。

**How to write a good spec for AI agents** — Addy Osmani（Google），2026 年 1 月。
URL: https://addyosmani.com/blog/good-spec/
從哪裡讀起：「curse of instructions」和「you remain the filter」兩段。這是「指令詛咒」機制最清晰的一手闡述，讀完你會理解為什麼「更長的 spec」不是解法。

**Spec Driven Development isn't Waterfall** — Marc Brooker（AWS VP / Distinguished Engineer），2026 年 4 月。
URL: https://brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html
從哪裡讀起：全文都短，直接從頭讀。重點是「the specification is the thing being iterated on」這個立場——理解這個論點才能公平評估規格漂移問題的嚴重性：如果 spec 是真正被快速迭代的東西，漂移可能比我們想的更可控；如果只是理論上的，就照本章的悲觀版本來。

**Spec-Driven Development: the revenge of Waterfall or BDD taken to a new level?** — Gojko Adzic（Specification by Example 作者），LinkedIn，2025 年 9 月。
URL: https://www.linkedin.com/pulse/spec-driven-development-revenge-waterfall-bdd-taken-gojko-adzic-imquf
從哪裡讀起：中段「It does not, really」——他解釋為何目前的 SDD spec 不算 BDD 意義上的「活文件（living documentation）」，因為它們不是人類可核准的、會自動驗證的規格，而是工具追蹤自己進度的 Markdown。這個觀點直接挑戰「spec = living document」的核心主張。

**Spec drift: the hidden problem AI can help fix** — Alex Norman（Kinde），2025 年 8 月。
URL: https://www.kinde.com/learn/ai-for-software-engineering/ai-devops/spec-drift-the-hidden-problem-ai-can-help-fix/
從哪裡讀起：全文。它給出了「spec drift」最簡潔的定義和認知負荷後果。注意：文章提出 AI CI/CD 偵測方向但沒有給出可用工具；把它當「問題定義」讀，不要當「解決方案手冊」讀。

下一章我們離開「理論框架」，正面面對「實測數據」：SDD 是否在受控條件下被驗證有效，哪些數字是真的，哪些是被誇大的傳言？

→ [Ch 40 實測數據與復現報告](./40-empirical-evidence.md)
