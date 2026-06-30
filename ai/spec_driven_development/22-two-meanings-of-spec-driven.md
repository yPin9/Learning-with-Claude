# Ch 22 — 兩種「規格驅動」：可執行規格 vs 規格再生成

> **目標**：把老的 BDD/Specification-by-Example 脈絡下的「可執行規格（Executable Specification）」與 2025 年 AI 工具脈絡下的「規格再生成（Spec-then-Generate）」拆開來看，理解兩者的設計哲學、工具鏈、適用範圍，以及為什麼在同一場對話裡混用這兩個意思會造成根本性的雞同鴨講。

---

## 一個名詞，兩個世界

想像你在一場工程師討論會裡聽到「我們要推行 spec-driven development」。

說這話的人可能是其中一種：

**第一種**：一位 2012 年讀過 Gojko Adzic《Specification by Example》的老派 TDD 推廣者，他的意思是「讓測試案例本身成為活文件，讓 Cucumber 跑的 Gherkin 場景就是規格，規格和測試要一致」。

**第二種**：一位剛看完 Sean Grove 在 AI Engineer World's Fair 2025 演講的人，他的意思是「先寫 Markdown 規格，讓 AI agent 根據規格自動生成實作」。

這兩個人說的是同一個詞，但所指的工作流、工具、產物、驗證方式幾乎沒有交集。

這不是細節問題。把兩個意思混在一起談，會導致：

- 評估 Spec Kit 或 Kiro 時用 BDD 的標準去打分，結論是錯的
- 跟老闆或客戶解釋時，對方以為你要做的是他已知的 BDD，結果驗收時大吃一驚
- 選錯工具、選錯培訓對象、選錯 ROI 估算模型

本章的任務就是把這個區分徹底講清楚。

---

## 心智模型：兩條不同的因果箭頭

在動手看細節之前，先用一張圖固定直覺。

```
【意義一：可執行規格（BDD / Specification-by-Example）】

 產品需求
     │  協作翻譯
     ▼
 Gherkin 場景 ─── 這就是「規格」，也是「測試」
     │  工具（Cucumber / SpecFlow）
     ▼
 自動化測試執行
     │  紅燈時
     ▼
 開發者寫 / 修 實作程式碼
     │  綠燈
     ▼
 規格 ≡ 測試 ≡ 文件（三合一，持續維護）

 因果方向：規格 ──驗證──▶ 程式碼
```

```
【意義二：規格再生成（Spec-then-Generate / AI-native SDD）】

 人類意圖
     │  需求捕捉
     ▼
 Markdown 規格（spec.md / requirements.md / design.md）
     │  LLM / AI agent 讀取
     ▼
 自動生成實作程式碼（+ 測試 + 設定檔 ...）
     │  人工 review 與驗收
     ▼
 規格是「源碼」；程式碼是「產物」（如同 binary）

 因果方向：規格 ──生成──▶ 程式碼
```

兩條箭頭的方向不同：

- 意義一的規格「驗證」程式碼，程式碼還是人寫的
- 意義二的規格「生成」程式碼，規格才是真正的源頭

這一個字的差距，在實作上有天壤之別。

---

## 意義一詳解：可執行規格的歷史脈絡

### 從哪裡來

2003 年，Eric Evans 出版《Domain-Driven Design》，強調領域專家和開發者要共用一套語言（通用語言，Ubiquitous Language）。這個想法的問題是：共用語言停在白板討論層面，沒有辦法自動確認「我們說的跟程式跑的一致」。

> 如果你對通用語言還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)

2006 年，Dan North 發表〈Introducing BDD〉，把 TDD 的 `test` 改名成 `behaviour`，把測試寫法改成 Given-When-Then 場景，目的是讓業務人員能讀懂。2011 年，Gojko Adzic 的《Specification by Example》（Manning 出版）把這個實踐系統化，引入「活文件（Living Documentation）」概念：如果規格和測試是同一份東西，文件就永遠不會過期。

> 如果你對 BDD 的 Given-When-Then 記法還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)

這套實踐的工具鏈是：

- **Cucumber**（Ruby/Java/JS）＋ **Gherkin** 語法
- **SpecFlow**（.NET）
- **Behave**（Python）
- **FitNesse** / **Fit**（更早的前輩，用 wiki table 格式）

### 一個具體的 Gherkin 場景

```gherkin
# features/checkout.feature

Feature: 結帳計算折扣

  Scenario: 購買金額超過 1000 元享 9 折
    Given 購物車中有商品，小計 1200 元
    And 會員等級是「一般會員」
    When 點擊「結帳」
    Then 最終金額顯示 1080 元
    And 折扣明細顯示「-120 元（九折優惠）」

  Scenario: 購買金額未達門檻不打折
    Given 購物車中有商品，小計 800 元
    And 會員等級是「一般會員」
    When 點擊「結帳」
    Then 最終金額顯示 800 元
    And 折扣明細不顯示
```

Cucumber 讀取這個 `.feature` 檔，把每一行 Given/When/Then 對應到 Step Definition（開發者寫的膠水程式碼），然後驅動真實程式執行。

這個場景**本身就是規格**：產品經理和開發者可以一起審閱它；它**也是測試**：CI pipeline 跑它，失敗了就代表實作壞掉了或需求理解有誤。

### 可執行規格的「可執行」是什麼意思

「可執行」的意思是「機器可以直接把規格跑起來，產生 pass/fail 結果」。

規格裡的每一個場景，都有對應的測試步驟來驗證程式行為。如果有人改了打折邏輯，忘記更新場景，或場景描述的行為和程式行為不符，CI 就會失敗。

這套系統的核心假設：**程式碼是人寫的，規格負責約束它。**

---

## 意義二詳解：規格再生成的 AI 原生脈絡

### 從哪裡來

2017 年，Andrej Karpathy 在 Medium 發表〈Software 2.0〉，提出神經網路的 weights 是一種新型態的程式：「Software 2.0 是用更抽象、對人類不友善的語言寫的，像是神經網路的 weights。」他把訓練資料集描述為「源碼」，把模型 weights 描述為「編譯後的 binary」。

2023 年 1 月 24 日，Karpathy 在 X（Twitter）發文：「The hottest new programming language is English.」（來源：Quote Investigator 2024-10-20 考查文章，原推文因存取限制需透過二手來源確認）

2025 年 6 月，兩件事同時發生：

1. Sean Grove（OpenAI）在 AI Engineer World's Fair 2025 演講〈The New Code〉，論證規格——不是程式碼，也不是 prompt——才應該是版本控制的主角。他的類比（根據社群謄本，非官方 OpenAI 謄本）：把生成的程式碼留著、把 prompt 丟掉，就像「把源碼切碎、卻非常仔細地版本控制 binary」。

2. AWS 發布 Kiro（2025 年 7 月 14 日），GitHub 開源 Spec Kit（2025 年 9 月 2 日）——兩個工具都把 Markdown 規格當作工作流的起點，讓 AI agent 根據規格生成實作。

這條脈絡的核心假設：**規格是源頭，程式碼是衍生物。**

### 一個具體的規格再生成工作流（以 Spec Kit 為例）

> 如果你對 Spec Kit 的安裝還不熟，先回看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)

Spec Kit 的工作流大致是：

```
/speckit.specify   → 產出 spec.md（功能描述、使用者故事、驗收標準）
/speckit.plan      → 產出 plan.md（實作路徑、技術決策）
/speckit.tasks     → 產出 tasks.md（任務清單，部分任務標 [P] 可平行）
/speckit.implement → AI agent 根據 tasks.md 一步一步生成程式碼
```

（注意：命令現在使用 `/speckit.*` 命名空間，查證日期 2026-06-30；舊的未加前綴版本已在 2025 年推出後演進）

Kiro（AWS）的三份規格檔案更清晰地體現分層：

- `requirements.md` — 使用者故事 ＋ EARS 格式驗收標準
- `design.md` — 資料流圖、TypeScript interface、API endpoint
- `tasks.md` — 依序排列、可追溯的實作任務（Waves 機制：同一 Wave 內的任務平行執行，不同 Wave 依序執行）

> 如果你想深入了解 Kiro 的三份規格，先回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)

這套系統的核心：**規格是你版本控制、稽核、重用的東西；程式碼是可以隨時從規格重新生成的產物。**

---

## 兩種意義的正面對照

| 維度 | 可執行規格（意義一） | 規格再生成（意義二） |
|---|---|---|
| **核心主張** | 規格＝測試，驗證人寫的程式碼 | 規格＝源頭，生成機器寫的程式碼 |
| **程式碼由誰產生** | 人類開發者 | LLM / AI agent |
| **規格語言** | Gherkin（Given-When-Then）| 自然語言 Markdown |
| **「可執行」的意思** | 規格可以被 Cucumber 等工具直接跑 | 規格可以被 LLM 解讀並生成實作 |
| **驗證機制** | 場景 pass/fail | Code review ＋人工驗收 ＋（選配）自動測試 |
| **主要受眾** | 業務人員 ＋ QA ＋ 開發者協作 | 開發者 ＋ AI agent |
| **成熟年份** | ~2006–2011（北 BDD / Adzic） | 2025 開始（Grove / Spec Kit / Kiro）|
| **代表工具** | Cucumber, SpecFlow, Behave, FitNesse | GitHub Spec Kit, AWS Kiro, Tessl |
| **最大假設** | 開發者還是寫程式碼的人 | AI agent 可以從規格可靠地生成程式碼 |
| **和 TDD 的關係** | 是 TDD 的自然延伸 | 和 TDD 並行，但不依賴 TDD |

---

## 混用兩個意思時發生什麼事

### 案例一：評估工具時錯用標準

有人推薦 Kiro，說它「可以讓 spec 直接跑起來驗證程式」。聽者以為這是意義一的可執行規格，期待類似 Cucumber 的場景執行報告。

結果 Kiro 給的是意義二的工作流：Markdown 規格生成程式碼，程式碼要自己寫測試才能驗證。兩邊對期待，結論是「這工具不符合我們的需求」——但其實根本就是在比蘋果和橘子。

### 案例二：批評「spec-driven 就是回到瀑布」

這個批評在意義二的語境下有其依據（大量前期規格，容易過度規格化）。但如果聽者以為你在說意義一（BDD），他們會疑惑：「BDD 明明強調迭代，哪裡像瀑布？」

兩邊說的不是同一件事，討論就會卡死。

### 案例三：Spec Kit 借用了意義一的語言

Spec Kit 的 spec-driven.md 說規格「成為可執行的（executable）」、「代碼服務規格，而非規格服務代碼」。這是意義二的工具，卻借用了意義一的「可執行」修辭。

這不是謊言——Spec Kit 的意思是規格「驅動」agent 執行任務，但用「executable」這個詞讓 BDD 背景的人容易誤讀。讀 Spec Kit 文件時要記住這個語境。

---

## 兩種意義可以共存嗎？

可以，但要明確宣告。

一個成熟的 SDD（意義二）工作流裡，生成出來的程式碼仍然需要測試。這些測試可以用 BDD（意義一）的方式寫：先寫 Gherkin 場景作為驗收標準，再讓 AI agent 生成通過這些場景的實作。

這時候，兩個意思在工作流的不同層次各司其職：

```
意義二（Spec-then-Generate）的規格
    │  LLM 生成
    ▼
意義一（Executable Spec / BDD）的 Gherkin 場景
    │  Cucumber 跑
    ▼
意義二生成的實作程式碼
    │  pass/fail
    ▼
重新生成或修正
```

這種組合是可行的，但你在說話的時候必須清楚指出「現在我說的是哪一層的規格」。

---

## 踩雷集錦

### 雷一：「規格驅動就是先寫測試」

**錯誤直覺**：聽到 spec-driven，聯想到 TDD，結論是「先寫測試再寫程式碼」。

**正確認識**：意義一（BDD）確實先寫場景再寫實作，但意義二（Spec-then-Generate）的「先」不是先寫測試，而是先寫自然語言的功能描述與設計文件，然後讓 AI 生成實作和測試。這是完全不同的順序和目的。

---

### 雷二：「只要夠精確，自然語言規格就等於可執行規格」

**錯誤直覺**：把意義二的 Markdown 規格寫得夠嚴謹（例如用 EARS 格式），它就跟意義一的 Gherkin 場景一樣「可以被機器直接驗證」。

**正確認識**：這是類比，不是等式。Gherkin 場景是語法固定的 DSL，Cucumber 有一套嚴格的解析規則；EARS 格式的 Markdown 則是給 LLM 讀的，LLM 的解讀本質上有不確定性。「精確的自然語言」和「機器可確定性驗證的 DSL」之間有原則上的差距，在 2026 年中期這個差距仍然存在。

---

### 雷三：「Spec-then-Generate 讓 BDD 過時了」

**錯誤直覺**：有了 AI 能從 Markdown 生成一切，BDD 的 Given-When-Then 就是歷史遺物了。

**正確認識**：兩者解決不同問題。BDD 的核心貢獻是「讓業務人員、QA、開發者能對一份活文件達成共識，並且這份文件能自動驗證實作」。這個需求在 AI 時代仍然存在——即使程式碼是 AI 生成的，你還是需要一個機器可確定性跑的標準來確認生成結果正確。BDD 和意義二的 SDD 是可以疊加的，不是替代關係。

---

### 雷四：「用 Spec Kit 就等於有了 BDD 的活文件好處」

**錯誤直覺**：Spec Kit 把規格版本控制起來，這樣規格就永遠和程式碼同步了。

**正確認識**：Spec Kit 確實把規格版本控制起來，但規格和程式碼的同步性不是自動保證的——那需要工作流紀律和審查流程，否則就會出現「規格漂移（Spec Drift）」：生成出來的程式碼演進了，但規格還停在原處。意義一的 BDD 有 CI 自動跑場景來確保同步；意義二需要另外設計這個機制。

> 規格漂移是一個重要的維護失敗模式，[Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 會深入討論。

---

### 雷五：「這兩個概念都叫 spec-driven，一定有人故意借用術語來行銷」

**錯誤直覺**：2025 年的 Spec Kit / Kiro 刻意借用 BDD 的「規格驅動」名聲來行銷自己。

**正確認識**：不是蓄意借用，而是術語自然演進的結果。「Spec-Driven Development」作為一個 2025 年代的詞彙，是在 Grove 的演講、Spec Kit、Kiro、Tessl 等工具的生態中有機形成的，沒有單一創始人（corrections.md 的 REFUTED 項明確指出：不可把這個詞的創造歸功給任何一個人）。BDD 那邊本來就有自己的語彙（「可執行規格」、「Specification by Example」），不是被 2025 年的工具搶走了什麼名字。問題在於英文詞彙有限，「spec-driven」這個短語在自然語言裡本來就可以涵蓋兩個意思。

---

## 歷史脈絡小結：為何兩個意思都活著

這兩個意義的共同祖先是一個 1970 年代以來的老問題：**如何讓對軟體的描述和軟體本身保持一致？**

- 1960s–80s：需求文件寫完就歸檔，和程式碼的同步靠「更新文件」的人工紀律，這個紀律通常在壓力下失守
- 1984 年：Knuth 的文學編程（Literate Programming）把解釋性散文和程式碼交織在一起，至少讓它們在同一個文件裡
- 2001–2006：XP 和 TDD 選擇把測試當作規格，用程式碼本身作為「活文件」
- 2006–2011：BDD / Specification-by-Example 走了另一個方向：把規格寫成業務人員能讀懂的場景，再讓這個場景自動連到測試
- 2017–2025：LLM 出現，提供了第三條路——讓規格直接生成實作，規格的精確性責任從「DSL ＋工具解析」轉移到「自然語言 ＋ LLM 理解」

兩個意思都是對同一個老問題的不同解法，而不是其中一個更對。

---

## 進階延伸

### 意義一的理論根基：為何場景能「替代」需求文件

BDD 的隱含論點是「場景的形式強制了精確性」。用自然語言描述「系統要能計算折扣」很模糊；用 Given-When-Then 場景描述同一件事，必須把前置狀態、觸發動作、預期結果全部講清楚。這個強制精確性的效果，和 EARS 記法要求「WHEN trigger, system SHALL response」有相同的動機。

> 如果你對 EARS 記法還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)

### 意義二的邊界條件：什麼樣的規格生成效果最好

根據 2025-2026 年的實踐報告（包含 HN 討論串 item 45610996），規格再生成在以下條件下效果較好：

- 功能邊界清晰，和其他功能的耦合少（bounded context 概念的轉移）
- 規格描述的層次適中——太抽象 AI 自由發揮太多，太細 AI 成為打字機
- 新建功能比修改既有大型 codebase 的成功率高得多（既有 codebase 的脈絡和規格通常不完整）

「太精確的規格變成另一種程式碼」這個批評是有據的：François Zaninotto（Marmelab CEO，2025 年 11 月）記錄了一個為顯示當前日期需求生成 8 個檔案、約 1,300 行規格文件的案例。規格的粒度是真實的設計決策，不是自動帶來好處的。

### 意義一＋意義二的結合點：ATDD-SDD 混合

在意義二的工作流末端加入意義一的驗收測試，是 2026 年中期最成熟的實踐模式之一：

```
Markdown spec (意義二)
   ↓ AI 生成
Gherkin 場景草稿 (意義一)
   ↓ 人工審核
最終 Gherkin 場景
   ↓ Cucumber 跑
實作程式碼 (AI 生成 + 人工修正)
```

這個模式把 AI 的生成力和 BDD 的確定性驗證結合起來。它的代價是工作流更複雜，需要團隊同時理解兩個意義的工具鏈。

---

## 動手練習

以下練習用一個具體的電商情境完成：「會員結帳時，持有折扣碼可抵扣固定金額」。

**練習 22-A：寫意義一的規格**

用 Gherkin 格式寫至少三個場景，涵蓋：
1. 折扣碼有效、金額足夠抵扣
2. 折扣碼已過期
3. 折扣碼抵扣金額超過訂單總額（邊界情況）

要求：每個場景要能被 Cucumber（或同類工具）解析，即所有步驟都可以對應到 Step Definition。

**練習 22-B：寫意義二的規格**

用自然語言（或 EARS 格式）為同一個功能寫一份 `requirements.md`，包含：
- 功能概述（2-3 句話）
- 至少 3 個使用者故事（User Story 格式）
- 每個使用者故事的 EARS 驗收標準

**練習 22-C：對照分析**

把 22-A 和 22-B 的產物放在一起，用自己的話回答：
1. 22-A 的場景和 22-B 的 EARS 標準，哪些資訊重疊，哪些只在一邊出現？
2. 如果要讓 AI agent 生成實作，22-A 的格式或 22-B 的格式對 LLM 更友善？為什麼？
3. 如果要讓 QA 工程師快速理解驗收標準，哪個格式更清楚？

這個三步練習沒有標準答案，但做完你應該能感受到兩個格式的設計目標和適用場景的差異。

---

## 本章重點整理

1. **「規格驅動開發」有兩個根本不同的意思**，混用會造成工具選型錯誤、期待落差和討論失焦。

2. **意義一（可執行規格）**：規格 ＝ Gherkin 場景 ≡ 自動化測試。程式碼由人寫，規格負責驗證它。工具：Cucumber / SpecFlow / Behave。成熟脈絡：BDD / ATDD / Specification-by-Example（Dan North 2006，Gojko Adzic 2011）。

3. **意義二（規格再生成）**：規格 ＝ Markdown 文件 ＝ 源碼。AI agent 從規格生成程式碼。工具：GitHub Spec Kit / AWS Kiro / Tessl。新興脈絡：Grove〈The New Code〉2025、Karpathy Software 3.0 框架。

4. **因果箭頭方向不同**：意義一的規格「驗證」程式碼；意義二的規格「生成」程式碼。

5. **「spec-driven development」沒有單一創始人**，它是 2025 年 AI 工具生態中有機形成的術語，不可歸功給特定個人。

6. **兩個意思可以組合**，但必須明確宣告現在說的是哪一層。意義二生成的程式碼仍然需要意義一的驗收測試來確保正確性。

7. **規格的粒度是真實設計決策**：太細的規格可能比程式碼更難維護；太粗的規格讓 AI 自由發揮太多。沒有一個粒度是「自然正確的」。

---

## 自我檢核

- [ ] 我能用自己的話向一個有 BDD 背景的人解釋「為什麼 Kiro 說的 spec-driven 和你說的 spec-driven 不一樣」
- [ ] 我能畫出兩種意義的因果箭頭圖，並說明箭頭方向的不同
- [ ] 被問到「可執行規格和規格再生成哪個更好」時，我能回答這是錯誤的問題框架，並說出為什麼
- [ ] 我能說出至少兩個場景，在這兩個場景下混淆兩個意義會造成實際傷害
- [ ] 面試被問「SDD 是不是只是 BDD 換個名字」，我能在 60 秒內給出清楚且有說服力的答案
- [ ] 我能解釋「spec-driven development」這個詞為何沒有單一創始人，2025 年前後這個詞的含義怎麼演進的

---

## 延伸閱讀

- **Specification by example（Wikipedia）** — https://en.wikipedia.org/wiki/Specification_by_example
  - **讀什麼**：看同義詞列表（ATDD、BDD、Example-Driven Development）和工具歷史（Fit、FitNesse、Cucumber），確立意義一的脈絡與時間線。
  - **和本章的關聯**：這是意義一最完整的起點地圖，讀完你對 BDD 淵源會有清楚的座標。

- **Gojko Adzic〈Specification by Example〉（Manning, 2011）**
  - **讀什麼**：第一部分（Why Specification by Example?）和第五部分（Living Documentation），前者說動機、後者說維護。
  - **和本章的關聯**：意義一的系統性理論來源，讀這本你才知道「活文件」這個承諾建立在什麼前提上，以及它在意義二的工作流裡是否仍然成立。

- **Sean Grove〈The New Code〉（AI Engineer World's Fair 2025）** — https://www.youtube.com/watch?v=8rABwKRsec4
  - **讀什麼**：直接看影片（約 22 分鐘），重點是「shred the source, version-control the binary」類比和 OpenAI Model Spec 的例子。社群謄本在 lawwu.github.io/transcripts/8rABwKRsec4.html（非官方）。
  - **和本章的關聯**：意義二的規格再生成最清晰的原始陳述，是理解 2025 年後 SDD 術語的必看。

- **spec-kit/spec-driven.md（GitHub Spec Kit）** — https://github.com/github/spec-kit/blob/main/spec-driven.md
  - **讀什麼**：Power Inversion 段落（「Specifications don't serve code—code serves specifications」）和「How It Works」段落，看 Spec Kit 怎麼用「executable」這個詞，並對照本章的分析。命令名稱和行為可能隨版本演進，以最新 commit 為準（查證日期 2026-06-30）。
  - **和本章的關聯**：直接示範了意義二的工具如何借用意義一的語言，讀了你對這個術語張力有第一手感受。

- **Gojko Adzic〈Introducing BDD〉（Dan North，2006）** — https://dannorth.net/introducing-bdd/
  - **讀什麼**：看「Story as a unit of functionality」和「Acceptance criteria」兩節，感受 Given-When-Then 作為規格語言的設計動機。
  - **和本章的關聯**：意義一的原始論文，讀了你知道「讓業務人員能讀懂規格」這個訴求在 2006 年是怎麼提出來的，以及它和 2025 年的訴求有多少重疊、多少分歧。

- **Spec Driven Development: When Architecture Becomes Executable（InfoQ）** — https://www.infoq.com/articles/spec-driven-development/
  - **讀什麼**：Framing 和 Conclusion 兩節，重點是「SDD 更接近架構模式而非測試方法論」這個論點。
  - **和本章的關聯**：提供一個從架構視角看待意義二的框架，有助於回答「這和 TDD 的關係到底是什麼」。

---

上一章我們在 Event Storming 工作坊中學會如何讓領域事件浮現；這一章我們把「規格驅動」這個詞的兩個截然不同的意思分清楚了。下一章要往更深的理論基礎走：Karpathy 的 Software 1.0/2.0/3.0 框架究竟在說什麼，以及這個框架如何替意義二的規格再生成奠定概念基礎。

→ [Ch 23 從 Software 2.0 到 Software 3.0：Karpathy 的弧線](./23-software-2-to-3.md)
