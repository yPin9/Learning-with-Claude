# Ch 26 — 懷疑論者的最強論證

> **目標**：以 steelman 的方式呈現 SDD 最強的三條批評——自然語言不可化約的含糊、回到瀑布的 Big Design Up Front、以及「夠精確的規格其實就是 code」這個歸謬——理解它們的真實力道，以及支持者目前能給出什麼程度的回應。

---

## 先建立一個心智圖像

想像三個在辯論的聲音：

```
┌──────────────────────────────────────────────────────────────┐
│ 聲音 A（語言哲學家）                                          │
│   「你的規格說『優雅地處理高流量』。                           │
│    那是什麼意思？問 PM、問 SRE、問前端工程師，                 │
│    你會得到三個不同的答案。」                                  │
├──────────────────────────────────────────────────────────────┤
│ 聲音 B（老敏捷教練）                                          │
│   「我們花了二十年把文件從流程中心驅逐出去，                   │
│    現在你要我先寫一千行 Markdown 再開始寫 code？               │
│    這是 1970 年代的瀑布，換了個新名字。」                      │
├──────────────────────────────────────────────────────────────┤
│ 聲音 C（程式語言理論家）                                       │
│   「等等，你說規格要夠精確才能驅動 AI 產出正確的 code。        │
│    精確到那種程度，規格本身就已經是 code 了。                  │
│    你只是在 code 和 Markdown 之間加了一層廢話。」              │
└──────────────────────────────────────────────────────────────┘
```

這三條批評不是稻草人。它們都有正當的哲學與工程根據。在你決定要不要採用 SDD 之前，你需要先能讓這三個聲音在你腦子裡說話。

---

## 一、自然語言不可化約的含糊

### 歷史脈絡：需求工程四十年的傷

1970 年代需求工程（Requirements Engineering）作為一個子領域誕生，就是因為人們發現自然語言需求是軟體失敗的頭號根源。IEEE 802 需求品質標準列出了八種需求病症（我們在 [Ch 8 為什麼需求這麼難](./08-why-requirements-hard.md) 討論過），包括含糊（ambiguity）、不一致（inconsistency）、不完整（incompleteness）。這些病症在自然語言中是**結構性的**，不是偶發的。

四十年後的 2025 年，SDD 工具把需求寫進 Markdown，用 LLM 解讀。問題沒有消失，只是換了一層包裝。

### 具體例子：一條規格，三種實作

假設規格這樣寫：

```markdown
## Feature: 購物車

當使用者加入超過 10 件相同商品時，系統應顯示警告並防止超量。
```

把這條規格丟給三個工程師（或三次 LLM 呼叫）：

| 解讀者 | 實作決策 |
|--------|----------|
| 工程師 A | 數量 > 10 時 block，顯示 modal |
| 工程師 B | 數量 = 10 時顯示警告，允許繼續；= 11 時 block |
| LLM 第一次 | 警告用 toast，block 發生在後端 API 層 |
| LLM 第二次 | 警告用 inline 訊息，block 只在前端 |

「超量」到底是 > 10 還是 ≥ 10？「警告」和「防止」是同時發生還是序列？「防止」是 UI 層還是 API 層？

這不是規格寫得不好——這就是自然語言的本質。Wittgenstein 在《哲學研究》裡說「意義即使用」（meaning is use），而「使用」在不同的工程脈絡裡就是不同的。

### EARS 能解決多少？

> 如果你還沒看過 EARS 記法，先回看 [Ch 11 EARS 深入](./11-ears-notation.md)。

EARS（Easy Approach to Requirements Syntax，簡易需求語法）的 `WHEN <trigger>, the system SHALL <response>` 句型確實能消除一些含糊。Kiro 就用 EARS 寫接受條件。

但 EARS 解決的是**句法**含糊，不是**語義**含糊。你仍然可以寫出這樣的 EARS 句子：

```
WHEN the cart quantity exceeds the threshold,
the system SHALL display an appropriate warning.
```

「threshold」是多少？「appropriate」是什麼？EARS 讓這些問題更*顯眼*，但不能替你回答它們。

### 形式化方法：兔子洞的底部

> 如果你對 TLA+ / Alloy 有興趣，先回看 [Ch 13 嚴謹的另一端](./13-formal-specs-tla-alloy.md)。

含糊問題的終極解法是形式化規格（Formal Specification）——TLA+、Alloy、Z notation。這些語言在數學上是精確的，沒有自然語言的含糊。

**但這不是 SDD 工具在做的事。**

Spec Kit 的 `.specify/` 資料夾裡是 Markdown。Kiro 的 `requirements.md` 是 EARS 句子。這些都是自然語言加上結構化範本，不是數學符號。把「形式化規格的嚴謹性」投射到「SDD Markdown」上，是一種分類錯誤。

Birgitta Böckeler（Thoughtworks）在 martinfowler.com 上明確提出這個警告：不要把 TLA+ 那種可以用工具*驗證屬性*的 spec，跟 Kiro/Spec Kit 的自然語言 Markdown 混淆。兩者帶來的是不同量級的「控制感」，而混淆會造成一種**虛假的確定感**（false sense of control）。

**批評的力道**：中等到高。EARS 幫助有限；形式化方法有效但不是 SDD 在做的；剩下的含糊只能靠人工澄清或靠測試抓到。

---

## 二、回到瀑布：Big Design Up Front 的詛咒

### 歷史脈絡：Agile 為什麼要殺死文件

瀑布（Waterfall）的故事比大多數人知道的更複雜——Winston Royce 的 1970 年論文其實是在描述瀑布的**失敗**並提出迭代修正（我們在 [Ch 4 瀑布的真相](./04-waterfall-myth.md) 詳細討論了這個誤會）。但不管原意為何，1970-2000 年代的業界實踐確實把「大份前期規格」當成標準流程。

結果呢？Standish Group 的 CHAOS Report（值得注意：此報告的方法論有爭議，研究社群對其數字的解讀不一致）年復一年指出項目失敗；Martin Fowler 等人觀察到大量「需求文件寫了三個月，code 起來第一天發現文件跟現實脫節」的案例。

2001 年 Agile Manifesto 用一句話回應：「Working software over comprehensive documentation」。

2003 年 Kent Beck 的 XP（Extreme Programming）更極端：先寫測試（TDD）而不是先寫文件，讓可跑的 code 作為唯一真相來源。

二十年後，SDD 要工程師「先寫 Markdown 文件，再讓 AI 生 code」。老敏捷教練的反應是可以預期的。

### Böckeler 的實測：scope inflation（範疇膨脹）

Birgitta Böckeler 在 2025 年 10 月的 martinfowler.com 文章裡測試了三個主要 SDD 工具。她的發現之一：

> Kiro 把一個小 bug fix 自動展開成「4 個 user story、共 16 條接受條件」。

這不是個人失誤，這是工具的**傾向**：SDD 工具被設計來*產生完整規格*，而「完整」很容易被解讀為「多」。

François Zaninotto（Marmelab CEO）把這個現象命名為「Markdown Madness（Markdown 瘋狂）」：一個「referred by（被誰引用）」欄位，最終觸發了一個涉及 8 個檔案、超過 1,300 行 Markdown 的規格連鎖。注意：根據更正資料（查證日期 2026-06-30），這 8 個檔案 / 1,300 行是另一個 Spec Kit 範例（工程師想在時間追蹤應用程式顯示當前日期）的統計，不是 Kiro 的 "referred by" 欄位範例；但 Zaninotto 描述的問題模式是真實的。

Colin Eberhardt（Scott Logic CTO）做了更完整的計時：用 Spec Kit 重建一個 ~1,000 行的 go-kart PWA 功能——Constitution（161 行）→ Specify（230 行）→ Plan（5 份文件共 2,067 行）→ Tasks（66 步清單）→ 最終生出約 700 行 code——總計 33 分 30 秒 agent 時間 + 約 3.5 小時人工審核 ≈ **4 小時**。相比之下，他用一般 iterative prompting 做同樣的功能只花了約 **23 分鐘**。

這是單一工程師、單一功能、特定 Spec Kit 版本（2025 年底）的數字，**不是受控實驗**，但這是目前最完整的公開再現測量。

### Marc Brooker 的反駁：規格是迭代的對象，不是交付物

Marc Brooker（AWS VP / Distinguished Engineer）在 2026 年 4 月發表了目前最有力的辯護：

> 「在規格驅動開發中，被迭代的對象是規格，不是實作。」

他的核心論點是：SDD 和瀑布的區別不在於「有沒有文件」，而在於**什麼東西被迭代**。

瀑布：需求凍結 → 設計凍結 → 開始 code（發現錯誤太貴）。

SDD（按 Brooker 的詮釋）：寫規格 → AI 快速生出 code → 你看到結果 → 修改規格 → 再生一次。規格不是一份交出去就不能動的文件，而是一個*版本控制、快速迭代*的 artifact。他稱這個模式為「pulling designs up, not up-front」。

這個論點有說服力，但它也預設了一個*目前工具普遍做不到*的東西：**規格修改後，code 能被快速、低成本地重新生成**。Tessl 的願景（spec-as-source，code 是規格的*衍生物*）在技術上是這個方向，但 Böckeler 觀察到 Tessl 用同一份規格兩次生出不同的 code（非確定性）。

**批評的力道**：高，尤其針對目前的工具實作。Brooker 的反駁在概念上成立，但在工具成熟度上還有落差。

---

## 三、「夠精確的規格其實就是 code」——歸謬論證

### 論證結構

這是三條批評裡最乾淨的邏輯論證：

```
前提 1：LLM 輸出正確 code 的機率，跟輸入規格的精確度正相關。
前提 2：要讓 LLM 可靠地輸出正確 code，規格需要精確到「無歧義」。
前提 3：「無歧義的自然語言描述」在實踐中等價於「形式語言的規格」。
前提 4：形式語言的規格就是 code（或比 code 更難寫的東西）。
結論：SDD 要求你先寫一份「實質上是 code」的文件，然後用 AI 把它翻譯成 code。
      你只是在 code 和 Markdown 之間加了一層沒有附加價值的翻譯。
```

### 為什麼這個論證有力

看一個實際的 Spec Kit 規格片段——這是 spec-driven.md 範本裡的典型結構：

```markdown
## User Stories

### Story 1: Display current user name
As a logged-in user,
I want to see my name in the top navigation bar,
So that I can confirm I am logged in as the correct account.

### Acceptance Criteria
- WHEN the user is logged in AND the navigation bar is rendered,
  the system SHALL display the authenticated user's display_name
  from the User entity.
- IF display_name is null OR empty,
  the system SHALL display the user's email address instead.
- WHEN the user logs out, the system SHALL remove the name display
  within 500ms of session termination.
```

這段規格已經非常精確。它說了欄位名稱（`display_name`）、fallback 邏輯（email）、時序要求（500ms）。

現在問題來了：

1. 這段 Markdown 幾乎可以被一個初級工程師在 15 分鐘內翻譯成正確的 code。
2. 一個能讀懂這段規格的 PM，已經做了大部分的「設計工作」。
3. 那麼 AI 的附加價值在哪裡？把「SHALL display display_name」翻譯成 `return user.display_name` 嗎？

### 反駁：精確的規格在不同層次有不同價值

支持者的回應有三個方向：

**方向一：規格作為溝通工具，不只是 code 生成的輸入**

規格讓 PM、QA、設計師、工程師能用同一份文件對齊理解，而且在沒有 code 的時候就能審查邏輯。這個價值跟「用 AI 生 code」是獨立的。

**方向二：規格可以被重用在多個目標**

同一份規格可以：生成 TypeScript 前端 code、生成 Python API code、生成測試案例、生成 API 文件。翻譯成多個目標的成本，比「每次都從頭 prompt AI 重新想設計」要低。

**方向三：模糊的 spec 和詳細的 spec 之間，有很大的空間**

批評者的歸謬論證預設了「要讓 AI 可靠，規格必須精確到等同 code」。但現實中，SDD 工具能從相對模糊的規格產出有用的 code，只是品質不如精確規格穩定。Addy Osmani（Google）的 "curse of instructions（指令詛咒）" 觀察指出：一份規格裡塞太多指令，LLM 對每條指令的遵從度**反而下降**。這意味著「最佳規格精確度」不是「越精確越好」，而是有一個最適點。

**這條批評的力道**：中到高，取決於你的使用情境。對於精確需求的核心功能，批評較有力；對於需要跨角色溝通的早期探索，批評較弱。

---

## 正反對比表

| 維度 | 批評者的論點 | 支持者的回應 | 目前狀態 |
|------|------------|------------|---------|
| **自然語言含糊** | 含糊是結構性的，EARS 只解決句法層次 | 澄清循環（AI 問、人回答）可以收斂 | 批評有效；SDD 工具需要人工澄清輔助 |
| **Big Design Up Front** | 大份前期 Markdown = 現代瀑布 | 規格是被快速迭代的 artifact，不是凍結文件 | Brooker 的概念框架成立，但工具成熟度尚未跟上 |
| **規格即 code** | 夠精確的規格 ≈ 偽裝的 code | 規格跨角色、跨目標，附加價值不只在 code 生成 | 依使用情境而定；初探期規格 vs 實作期規格有別 |
| **維護成本** | 雙重 artifact（spec + code）= 雙重腐化風險 | 讓 AI 強制同步；人只審合約層級的 diff | spec drift 是真實問題，目前沒有成熟解法 |
| **大型既有 codebase** | SDD 幾乎無法用在有歷史包袱的大型系統 | 可以只在新功能、新服務上套用 | 批評者如 Zaninotto 的觀察有據 |

---

## 踩雷集錦

**錯誤直覺 1**：「批評者都是守舊的老敏捷迷，不懂 AI 的潛力。」
**正確認識**：批評陣營包含 Birgitta Böckeler（Thoughtworks Distinguished Engineer、Gojko Adzic（《Specification by Example》作者）、Colin Eberhardt（Scott Logic CTO）。這些都是親自動手測試工具、提出具體數據的技術領袖，不是在理論上抗拒新事物。

---

**錯誤直覺 2**：「SDD 的 Markdown 規格就像 TLA+ 那樣嚴謹。」
**正確認識**：這是分類錯誤。TLA+ / Alloy 的規格可以被機器*驗證屬性*（model checking）；Spec Kit / Kiro 的 Markdown 是*被 LLM 解讀的自然語言*，解讀結果是非確定性的。兩者帶來的保證量級完全不同。Böckeler 明確指出這種混淆會產生虛假的確定感。

---

**錯誤直覺 3**：「Colin Eberhardt 的 4 小時 vs 23 分鐘證明 SDD 一定比迭代慢。」
**正確認識**：這是一個工程師、一個功能、2025 年底版本的 Spec Kit 的單次測量。它是目前最完整的公開再現，但不是受控實驗。Farrag（University of East London）的 N=14 欄位研究（arXiv:2605.01160）在加入 Spec Kit 治理後報告了 lead time 改善，但樣本小、單一組織、無對照組，作者自稱數字「只是指示性的，沒有統計控制」。兩份數據都不足以做出普遍性結論；你需要在自己的情境裡測量。

---

**錯誤直覺 4**：「批評者說 SDD 是瀑布，那就是瀑布。」
**正確認識**：這個類比成立的條件是「規格在寫完後就被凍結」。Brooker 的論點是：如果你把*規格本身*當作可以快速修改的迭代對象，SDD 和瀑布在根本模式上不同。批評是否成立，取決於你實際上怎麼用工具——如果你的團隊把規格當作需求書交出去，你確實是在跑瀑布。

---

**錯誤直覺 5**：「SDD 工具沒有人用，只是炒作。」
**正確認識**：GitHub Spec Kit 在 2026 年 6 月有約 116,000 stars（查證日期 2026-06-30）。它吸引了大量真實使用者，批評者的 Hacker News 討論串（item 45610996）也充滿了真實部署的故事——包括正面和負面。這不是無人問津的炒作，而是有足夠實際使用資料可以客觀評估的工具。

---

## 進階延伸

### Gojko Adzic 的中間立場

Gojko Adzic 是《Specification by Example》（2011）的作者，是 BDD 傳統的重要人物。他在 2025 年 9 月對 SDD 的評估是：SDD 既不是「真正的 BDD」（因為 spec 沒有被當作人類可讀的活文件維護、沒有 scoping phase），也不是純瀑布（因為工具確實有互動性）。

他的核心觀察：大量 SDD 工具產出的 Markdown「似乎是給工具追蹤自己進度用的，不一定是為人類消費而設計的」——這也許解釋了為什麼 spec 的*溝通價值*在實踐中常常被誇大。

### 「詛咒指令」與最適規格精確度

Addy Osmani（Google）的 "curse of instructions" 觀察在這裡有深層含義：LLM 無法線性地遵從任意多條指令。這意味著：

- 超規格（over-speccing）不只浪費時間，它會*降低*模型的整體遵從度。
- 最適規格長度取決於模型的 context 容量和你的 task 複雜度。
- 「規格越精確越好」是一個在某個閾值後反轉的函數。

這是批評「規格即 code」論點的一個有趣反例：如果你把規格精確度推到最高，你反而會降低 AI 的輸出品質。

### Spec drift 作為雙重 artifact 的必然後果

如果規格和 code 是兩份獨立維護的 artifact，它們*必然*會隨時間出現漂移（spec drift）。這不是人性的弱點，這是資訊系統的基本規律——任何有冗餘的系統都會出現不一致。

> 如果你想更深入 spec drift 的機制與解法，先看 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

目前的「解法」（讓 AI 在同一個 commit 裡同步更新 spec）在理論上可行，但實踐中還沒有成熟的工具生態。

---

## 動手練習

拿一條你最近在工作中寫過（或收到的）需求，做以下三件事：

1. **含糊測試**：找出這條需求裡所有可以有兩種以上合理解讀的詞語。數一數有幾個。
2. **精確度代價測試**：試著把這條需求改寫到「沒有任何歧義」。需要寫多長？
3. **批評者的帽子**：扮演 Böckeler 或 Eberhardt，列出如果把這條需求丟給 Spec Kit，可能出現哪些 scope inflation 的地方。

不需要 code，不需要 SDD 工具，用筆記本就可以做。

---

## 本章重點整理

- 自然語言的含糊是結構性的，不是偶發的；EARS 改善句法層次，但不解決語義層次。把 Spec Kit/Kiro 的 Markdown 和 TLA+ 的嚴謹性混淆，會造成虛假的確定感。
- 「回到瀑布」的批評在工具實踐層面有明確的實測依據（Eberhardt 的 4h vs 23min、Böckeler 的 scope inflation）；Brooker 的概念反駁（規格是迭代對象）在邏輯上成立，但工具成熟度還沒能兌現這個承諾。
- 「規格即 code」的歸謬有真實力道，但依使用情境而定：早期探索、跨角色溝通的情境，規格的價值不只在 code 生成；核心精確實作的情境，批評最有力。
- Addy Osmani 的 "curse of instructions" 提示：規格精確度不是線性越高越好，過度規格化會降低 LLM 遵從度。
- 理性的立場不是「SDD 萬歲」或「SDD 是廢話」，而是理解這三條批評的適用條件，在你自己的情境裡測量。

---

## 自我檢核

- [ ] 我能用自己的話解釋，為什麼 EARS 只解決了「句法含糊」卻不能解決「語義含糊」，並舉一個例子。
- [ ] 面試被問「SDD 跟瀑布有什麼區別？」我能說出 Brooker 的反駁邏輯，並指出這個反駁的前提條件。
- [ ] 我能解釋「規格即 code」這個歸謬的邏輯結構，以及支持者在哪些情境下能有效反駁它。
- [ ] 我能區分 Colin Eberhardt 的計時數字和 Farrag 的 N=14 欄位研究，解釋為什麼兩者都不能做普遍性結論。
- [ ] 我知道「curse of instructions」的含義，以及它如何反過來限制「規格越精確越好」這個直覺。

---

## 延伸閱讀

- **[Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)** — Birgitta Böckeler（Thoughtworks），發表於 martinfowler.com，2025 年 10 月。本章三條批評的最佳一手來源：scope inflation、Markdown 冗余、非確定性、"false sense of control"、以及 spec-first/spec-anchored/spec-as-source 三層分類法都在這裡。先讀各工具的測試段落，再讀「false sense of control」小節。

- **[Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall?](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)** — Colin Eberhardt（Scott Logic CTO），2025 年 11 月。目前最完整的公開 Spec Kit 端到端再現，含各階段行數與計時。本章「4 小時 vs 23 分鐘」數字的來源。讀 Plan / Implementation 段落以及最後的結論。

- **[Spec-Driven Development: The Waterfall Strikes Back](https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html)** — François Zaninotto（Marmelab CEO），2025 年 11 月。「瀑布重生」批評的代表文章，羅列了 Context Blindness、Markdown Madness、Double Code Review、Spec Non-Compliance 四類失敗模式。注意：作者依據他人的再現，未親自運行工具。

- **[Spec Driven Development isn't Waterfall](https://brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html)** — Marc Brooker（AWS VP / Distinguished Engineer），2026 年 4 月。目前最強的反駁：「規格是迭代的對象，不是交付物」。篇幅短，全文都值得讀。與 SE-Radio 710 podcast 搭配聆聽效果更好。

- **[Spec-Driven Development: the revenge of Waterfall or BDD taken to a new level?](https://www.linkedin.com/pulse/spec-driven-development-revenge-waterfall-bdd-taken-gojko-adzic-imquf)** — Gojko Adzic（《Specification by Example》作者），2025 年 9 月。從 BDD / living documentation 傳統評估 SDD：為什麼它目前不是「真正的 BDD」，以及需要什麼條件才能成立。讀中間 "It does not, really" 段落。

- **[How to write a good spec for AI agents](https://addyosmani.com/blog/good-spec/)** — Addy Osmani（Google Chrome Engineering），2026 年 1 月。本章 "curse of instructions" 概念的出處；包含五條核心規格品質原則和 context bloat 警告。讀「curse of instructions」和「you remain the filter」段落。

- **《Specification by Example》** — Gojko Adzic，Manning，2011。BDD / ATDD 傳統的標準教材，確立「可執行規格」的「老」定義，以及它與 2025 年 SDD 工具之間的差異。本課程 [Ch 10](./10-acceptance-criteria-bdd.md) 和 [Ch 25](./25-tdd-bdd-mda-lineage.md) 都建立在這個脈絡上。

---

下一章我們從批評轉向工具實作——練習 D 讓你在批評的視野下，把你自己的需求和領域模型寫成一份完整的規格，親身體驗這三條批評在實際操作中意味著什麼。

→ [練習 D 把需求＋領域模型寫成一份完整的 spec](./practice-d-write-a-spec.md)
