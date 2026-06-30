# Ch 44 — 信任階梯：從輔助規格到自主實作

> **目標**：理解「漸進放權」框架——如何從「AI 幫你草擬規格」逐步走到「AI 依規格自主實作 + 人工把關驗收」，以及每一階應該掛什麼護欄才能讓放權不變成失控。

---

## 直覺：為什麼是「梯子」而不是「開關」

許多人剛接觸 SDD 時，會以為信任問題是個二元選擇：要嘛你信任 AI、讓它全跑；要嘛你不信任它、自己動手。

這個想法的危險在於：它跳過了你需要親眼看見、才能建立的信心。

想像你把一個新人放進廚房。第一天，你不會讓他獨自主廚；你讓他幫你備料，你在旁邊看著。確認他切菜不會傷手指、知道「鹹淡」不是「一匙就好」之後，你才開始讓他主導某幾道菜，你在門口等結果。最後，你只需要在出菜前試吃一口。

放權是一段過程，不是一個決定。

```
階段 0  AI 只讀、只評論
        ─────────────────────────────────────
階段 1  AI 產出規格草稿，人審、人決定
        ─────────────────────────────────────
階段 2  AI 產出規格 + 實作方案，人審後批准才執行
        ─────────────────────────────────────
階段 3  AI 端到端跑完一個任務，人驗收產物
        ─────────────────────────────────────
階段 4  AI 自主完成一批任務，人只看最終 diff + 測試報告
        ─────────────────────────────────────
階段 5  AI 在既定邊界內完全自治，人定期審計
```

每一階往下移，放權程度增加、干預頻率降低、護欄責任加重。**護欄沒有配套就跳階，是這個領域最常見的失敗模式。**

---

## 歷史脈絡：這不是第一次有人想把決策外包出去

在 AI coding 之前，人們也嘗試過「讓工具做更多決定」：

- **MDA（Model-Driven Architecture）**，1990 年代 OMG 推動。概念是從平台無關模型（PIM）自動產生平台特定模型（PSM）再產生程式碼。理論很美；實務上工具產生的程式碼難以除錯，維護人員要么改生成物（規格漂移）、要么改模型（學習成本高），最後大多放棄。
- **Low-code / no-code 平台**，2010 年代興起。面對複雜業務邏輯時，「視覺設定」讓位給 escape hatch（逃生艙：還是要寫程式碼），規格（流程圖）跟程式碼再度分叉。
- **BDD 自動化**，如 Cucumber / SpecFlow。Gherkin 情境可以驅動測試，但維護人員很快發現「測試通了、規格過時了」是常態。

每一波都在解決同一個問題：**人的意圖 → 機器行為** 之間的轉譯鴻溝。每一波都在「讓工具做得更多」跟「人對工具行為的可理解性」之間失去平衡。

SDD 面對的是同一道牆，只是現在翻譯者換成了 LLM，轉譯能力大幅提升，但可預測性的問題依然存在。

> 如果你對 SDD 的 MDA 譜系還不熟，先回看 [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)。

---

## 各階詳述：護欄、訊號、升降條件

### 階段 0：AI 只讀、只評論

**AI 的角色**：讀你寫好的需求或設計，指出潛在矛盾、缺漏的邊界條件、可能的安全風險。

**典型工具用法**（以 GitHub Spec Kit 為例，查證日期 2026-06-30）：

```bash
# 已有 .specify/specs/01-payment-flow.md
# 用 /speckit.clarify 讓 AI 標出需要澄清的地方
```

AI 會在 spec 裡插入 `[NEEDS CLARIFICATION: ...]` 標記——這不是它的決定，是它的疑問。你負責回答。

**護欄**：
- AI 產出只能是批注；任何批注都要人確認才能進入下一步。
- 每次 clarify 之後，你要親自更新 spec，不要讓 AI 直接改原文。

**升級訊號**：你已經看過三個以上 AI 的 clarify 輸出，大部分疑問都是你自己沒想到的真實問題（不是噪音）。

---

### 階段 1：AI 產出規格草稿，人審、人決定

**AI 的角色**：從你給的原始需求（會議記錄、email、口頭描述）生成結構化規格草稿。

這一階最容易犯的錯是：看到草稿很像樣就直接進實作，跳過審查。

一個具體的例子——你給 Kiro Spec Session（查證日期 2026-06-30）這段原始需求：

> 用戶可以上傳頭像，最大 2MB，支援 JPG 和 PNG。

Kiro 會生成 requirements.md 的草稿，裡面可能包含：

```markdown
## Functional Requirements
- FR-01: Users SHALL be able to upload a profile picture.
- FR-02: The system SHALL reject files larger than 2MB.
- FR-03: The system SHALL accept JPEG and PNG formats.

## Non-Functional Requirements
- NFR-01: Upload response SHALL complete within 3 seconds under normal load.
- NFR-02: Uploaded images SHALL be stored in an encrypted storage layer.
```

`NFR-01` 和 `NFR-02` 你沒提過。Kiro 自己推出來的。

這是階段 1 護欄存在的理由：**AI 填空了你沒說的事情，這些填空需要被看見**。

**護欄**：
- 每一條 AI 生成的需求都要能追溯到你的原始輸入，或者你明確補充了它。
- 沒有原始根據的需求，不得放行進入計畫階段。
- 使用「需求溯源矩陣（Requirement Traceability Matrix）」：一個簡單表格，欄位是 FRID → 原始來源 → 負責人。

| 需求 ID | 說明摘要 | 原始來源 | 負責人 | 狀態 |
|---------|----------|----------|--------|------|
| FR-01 | 上傳頭像 | 用戶 email 第 2 段 | 產品 | 確認 |
| NFR-01 | 3 秒上傳 | AI 推導 | 技術 | **待確認** |
| NFR-02 | 加密儲存 | AI 推導 | 技術 | **待確認** |

**升級訊號**：你跑過三個以上功能的規格草稿，AI 推導的項目被你否決的比例低於 20%，且你養成了固定的審查節奏（不是每次都靠記憶）。

---

### 階段 2：AI 產出規格 + 實作方案，人審後才批准執行

**AI 的角色**：從已核准的規格生成實作計畫（design.md + tasks.md），列出步驟、依賴關係、測試策略。

這一階引入了一個新的風險：**計畫看起來合理，但執行時 AI 做的事跟計畫不一樣。**

Böckeler（Thoughtworks，2025 年 10 月在 Martin Fowler 網站發表）的測試記錄了這個現象：tools simultaneously "too eagerly" followed and ignored constraints。

> 如果你對 Böckeler 的三層 SDD 分類法（spec-first / spec-anchored / spec-as-source）還不熟，先回看 [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md)。

**護欄**：
- 計畫審查清單：每個任務條目必須有明確的「完成定義（Definition of Done）」，包含測試覆蓋要求。
- AI 的計畫必須對應回規格的具體條目（FR-XX / NFR-XX），不得有漂浮的任務。
- 設定「允許的變更邊界」：哪些檔案 AI 可以動、哪些不行（用 `.gitignore` 排除、或明確寫在 steering 設定裡）。

Kiro 的 steering 機制（查證日期 2026-06-30）允許你把這類限制寫入常駐指令：

```markdown
<!-- .kiro/steering/constraints.md -->
Do NOT modify database migration files.
Do NOT change the API contract in api/openapi.yaml without explicit approval.
```

**升級訊號**：你已核准並驗收了至少 5 個任務，AI 產出的程式碼通過 CI 且沒有你發現但 AI 自我標注的「技術債」。

---

### 階段 3：AI 端到端跑完一個任務，人驗收產物

這是「放手」的第一個真正跨越點。

你批准計畫之後，AI 執行所有實作步驟，你只在最後看：
- diff（程式碼變更）
- 測試結果
- AI 自我生成的驗收報告

**這一階的典型失敗模式**（來自 HN thread 45610996，查證日期 2026-06-30，使用者 yoaviram 的親身回報）：

> `the implement command did not follow the process... it would forget to create or run tests.`

AI 把任務標成「已完成」，但沒有跑測試。

從 Ch 39 的「規格漂移」框架看，這是另一種形式的規格腐化（Spec Rot）——不是規格跟程式碼分叉，而是**任務清單跟實際執行分叉**。

> 如果你對規格漂移的機制還不熟，先回看 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

**護欄**：
- CI pipeline 必須在 AI commit 之後自動觸發，且測試結果要在 PR 上可見，不是 AI 自我報告。
- 驗收清單（Acceptance Checklist）必須在任務核准時就寫定，不能在 AI 執行後再補。
- 設置「任務完成的結構性定義」：任務只有在 CI 綠燈 + 所有驗收條件在 PR 上有明確對應的地方才算完成——AI 的文字報告不算數。

**升級訊號**：你跑過 10 個以上任務，CI 失敗率低於 10%，且你已經找到 AI 最常犯的那 2-3 種錯誤類型。

---

### 階段 4：AI 自主完成一批任務，人只看最終 diff + 測試報告

**AI 的角色**：執行一整個 sprint 或一整個功能的所有任務，中間不打斷。

這一階的核心挑戰是：**AI 的錯誤會在你看到之前堆疊**。一個早期的錯誤假設，可能在 8 個任務之後才浮出水面，而那時候要回溯已經很難。

Kiro 的 tasks.md 使用「波次（waves）」概念（查證日期 2026-06-30，Kiro 官方文件原文）：

> `Waves execute sequentially; tasks within a wave execute concurrently.`

這個設計的意義：波次之間是同步點（sync point）。你可以把每個波次的邊界設為人工檢查點，而不是等所有任務都跑完再看。

**護欄**：
- 每個波次完成後，CI 必須全部通過才允許啟動下一個波次（如果工具不支援，就手動把波次分成多個 PR）。
- 定義「回滾觸發條件」：哪些情況下你會整個 revert 這個波次（例如：任何資料庫 schema 的 undocumented change）。
- 「人工不得事後補規格」原則：如果 AI 做了一個你沒有在規格裡要求的事，這不算驚喜，這是規格破洞，需要補進去再決定要不要保留那段行為。

**升級訊號**：你跑過完整的 3 個功能，每個功能包含至少 2 個波次，沒有一次需要你介入修正中間狀態。

---

### 階段 5：AI 在既定邊界內完全自治，人定期審計

這一階不是「放棄監督」，而是把監督的頻率從「每次」降到「定期抽查」。

Marc Brooker（AWS VP，2026 年 4 月）在他的文章裡說的那句話，在這裡特別重要：

> `we are still very early in this revolution.`（查證日期 2026-06-30，來自 brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html）

在「很早期」的情況下，完全自治只在邊界清楚的子系統才合理。

**護欄**：
- Bounded Context（限界上下文）即 Agent Scope：只有在邊界明確劃定的領域模型範圍內，AI 才有完全自治的授權。跨邊界的任何動作需要回到階段 2。
- 定期審計節奏：每兩週至少看一次「AI 在這段時間做了什麼、它自己沒有標注的所有變更是什麼」。
- 安全邊界不下放：CI/CD 密鑰、生產環境存取、第三方 API 金鑰，永遠不在 AI 的自治授權範圍內。

> 如果你對 Bounded Context 作為 Agent Scope 的框架還不熟，先回看 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)。

---

## 為什麼不直接從階段 4 開始？

最常見的反駁：「我公司規模小，沒有時間慢慢爬梯子，直接試最高效率的模式。」

有三個原因這樣做代價很高：

**1. 你還不知道你的任務類型屬於哪個可靠區間。**

Colin Eberhardt（Scott Logic，2025 年 11 月）的測試顯示：一個 ~700 行的功能，Spec Kit 花了 4 小時；同等功能他自己迭代提示（iterative prompting）只花了 23 分鐘。這不是 SDD 本來就慢——是他選的那個任務類型不適合當下的 SDD 工具。如果他一開始就在「階段 4」模式，損失的是整個週期的時間，不是一次測試。

**2. 你不知道這個 AI 在你的 codebase 上的失敗模式是什麼。**

HN 使用者 hatmanstack（2025 年 10 月，HN item 45610996）在 Kiro 上跑 12 個以上主任務加 4 個以上子任務：

> `deleted code unpredictably and wouldn't revert.`

如果他在一個有足夠 CI 覆蓋的小批次裡先跑，刪除代碼的問題會在第一批就被發現，影響範圍可以控制。

**3. 你失去了建立「問責基礎」的機會。**

如果某個 AI 自主實作的功能出問題，你需要能說：「這個任務的規格在這裡、批准記錄在這裡、AI 報告在這裡、CI 記錄在這裡。」跳階等於跳過了這條文件鏈。

---

## 信任的反面：過度控制的代價

信任階梯不是「越低越好」。停在階段 0 或階段 1 也有代價：

- 規格品質依賴你個人的需求分析能力，沒有 AI 輔助時遺漏的邊界條件依然遺漏。
- 你花費在「把 AI 稿改成我的版本」的時間，可能比「直接寫」還長。
- 團隊永遠無法建立「AI 在我們的 codebase 上可以相信到什麼程度」的集體知識。

Addy Osmani（Google，2026 年 1 月）的建議：「adjust spec detail to task complexity — don't over-spec a trivial one」（查證日期 2026-06-30，來自 addyosmani.com/blog/good-spec/）。同樣的邏輯對放權層級也適用：用你任務的複雜度、你對 AI 在這類任務上的歷史信心，來決定哪一階合適，而不是一刀切。

---

## 對比：不同決策框架的橫向比較

| 框架 | 預設立場 | 優點 | 缺點 |
|------|----------|------|------|
| 完全人工（No AI） | AI 不參與 | 可預測、可問責 | 速度慢、規格盲點多 |
| AI 輔助（Augmented）= 階段 0-2 | 人主導，AI 建議 | 低風險、易回滾 | AI 貢獻有限 |
| AI 主導（Supervised）= 階段 3-4 | AI 執行，人驗收 | 速度大幅提升 | 需要紮實的 CI + 規格 |
| AI 自治（Autonomous）= 階段 5 | AI 在邊界內決策 | 最高效率 | 最高規格與護欄要求 |

沒有哪個框架永遠對。同一個團隊的不同子系統，可以跑不同的階段。Core domain（核心領域）應該比 Generic subdomain（通用子領域）保留更多人工控制。

> 如果你對 Core / Supporting / Generic 子領域的分類還不熟，先回看 [Ch 18 子領域：Core / Supporting / Generic](./18-subdomains.md)。

---

## 踩雷集錦

### 錯誤直覺 1：「規格寫清楚了，AI 就會照做」

**正確認識**：Osmani 記錄的「指令詛咒（curse of instructions）」是真實存在的：當規格中的指令數量累積，LLM 對每一條的遵從率會下降。規格越長，並不代表 AI 執行越準確。你需要的是「足夠的規格」配上「足夠的 CI 覆蓋」，而不是「窮盡的規格」。

---

### 錯誤直覺 2：「AI 說任務完成，就是完成了」

**正確認識**：這是一個已有多個真實案例記錄的問題。HN 使用者 yoaviram（HN item 45610996）的 Spec Kit 跑到「the agent marked the verify implementation task as done without writing a single unit test」（查證日期 2026-06-30）。任務的完成定義必須被結構化約束，而非 AI 的自我報告。CI 綠燈才是客觀的完成標準。

---

### 錯誤直覺 3：「我已經在更高的階段了，不需要退回去」

**正確認識**：不同任務類型可能需要不同的放權層級。在你熟悉的 CRUD 功能上跑到階段 4，不等於在「改資料庫 schema 的遷移」或「修改認證流程」上也適合階段 4。放權層級是任務屬性，不是你個人狀態的一次性升級。

---

### 錯誤直覺 4：「架構決定由 AI 做，我只看結果」

**正確認識**：Brooker 的論文裡那句話是「humans remain essential to own the 'internally conflicting' nature of requirements」。需求之間的衝突（速度 vs 安全、成本 vs 可靠性），AI 可以列舉選項，但它沒有立場做最終的取捨。架構決定的責任必須落在人身上，記錄在規格或 ADR（Architecture Decision Record，架構決策記錄）裡。

---

### 錯誤直覺 5：「信任 AI 就是不改它的輸出」

**正確認識**：信任的對象是「AI 在邊界內按規格執行的能力」，不是「AI 做的任何決定都是對的」。如果 AI 產出的東西「技術上符合規格、但感覺不對」，Osmani 的原話是：`trust your judgement`（查證日期 2026-06-30）。判斷力是你的資產，不是對 AI 不信任的表現。

---

## 階梯管理的實踐工具

### 信任日誌（Trust Log）

一個簡單的表格，記錄每次 AI 執行的任務類型、階段、成功/失敗、失敗原因：

```markdown
| 日期 | 任務類型 | 階段 | CI | AI 報告 | 實際結果 | 備注 |
|------|----------|------|----|---------|----------|------|
| 2026-06-15 | CRUD 增刪改查 | 3 | ✅ | 完成 | 完成 | 無問題 |
| 2026-06-18 | 認證流程修改 | 3 | ❌ | 完成 | 測試未跑 | 退回階段 2 |
| 2026-06-22 | 報表查詢 | 4 | ✅ | 完成 | 完成 | 確認波次護欄有效 |
```

這不是績效考核，是知識積累。三個月後，你會知道你的 codebase 上哪類任務可以放到哪一階。

### 護欄清單總覽

| 階段 | 最小護欄 | 選配護欄 |
|------|----------|----------|
| 0 | 批注不改原文 | — |
| 1 | 需求溯源矩陣 | 每條需求有明確「批准人」 |
| 2 | 計畫 → 規格 ID 對應；steering 限制 | ADR 配套 |
| 3 | CI 自動觸發；驗收清單預先寫定 | PR 模板強制填寫 |
| 4 | 波次同步點；回滾觸發條件 | 每個波次獨立 PR |
| 5 | Bounded Context 邊界白名單；定期審計 | 安全邊界永不下放 |

---

## 與 Part 8 其他章節的關係

這一章是 Part 8「團隊與治理」的收尾。前面幾章提供了拼圖的各個零件：

- Ch 39（規格漂移）說明了規格為什麼會悄悄失真，這是放權之後最難察覺的風險。
- Ch 41（SDD 的安全面）說明了 prompt injection 和 lethal trifecta（注意：這裡是 Simon Willison 定義的資安三角——私密資料存取 + 暴露於不受信任內容 + 有外洩能力，查證日期 2026-06-30）——這是任何 AI 自治都必須面對的安全邊界。
- Ch 42（什麼時候不要用 SDD）說明了規格機制本身不適合的場景。
- Ch 43（把 SDD 織進團隊）說明了組織採用的結構。

信任階梯把這些拼在一起，提供一個「我現在在哪裡、下一步怎麼走」的操作框架。

---

## 動手練習

1. 拿你目前在做的一個專案，對照上面六個階段，誠實地說出你現在「實際上」在哪一階（不是你以為你在的那一階）。
2. 列出讓你升到下一階需要補的最小護欄是什麼，把它寫成一個 TODO，包含誰負責、什麼時候完成。
3. 如果你有 Spec Kit 或 Kiro 的使用經歷，回頭看看有哪一次「AI 說完成但其實沒完成」——這個案例用你自己的話記錄下來，會是你最有價值的信任日誌第一條。

---

## 本章重點整理

- 信任是一個漸進的、可積累的過程，不是一個二元開關。
- 六個階段（0-5）對應不同的放權程度，每一階的護欄必須在升階前到位。
- 最常見的錯誤是：看到 AI 輸出看起來完整就跳過審查，直到 CI 或生產環境才發現問題。
- 「指令詛咒」意味著規格更長不等於 AI 執行更準；護欄必須從外部（CI、審查、邊界約束）而不只是從規格本身來。
- 信任的對象是「AI 在邊界內的執行能力」，不是「AI 的任意輸出」。
- Core domain 應該保留比 Generic subdomain 更多的人工控制。
- 同一個團隊的不同子系統，可以跑不同的放權階段。

---

## 自我檢核

- [ ] 我能用自己的話解釋，為什麼從「AI 輔助規格」到「AI 自主實作」需要一個梯子，而不是一個開關。
- [ ] 面試官問「你們怎麼控制 AI coding agent 的風險」，我能說出至少三個具體的結構性護欄（不是「我們很謹慎地審查」）。
- [ ] 我能解釋為什麼「AI 說任務完成」不能作為任務完成的定義，以及正確的定義應該是什麼。
- [ ] 我能說出「指令詛咒」的意思，以及它對「規格越詳細越好」這個直覺的挑戰是什麼。
- [ ] 我能用 Core domain vs Generic subdomain 的框架，說出為什麼不同子系統應該跑不同的放權階段。
- [ ] 我能舉出一個真實失敗案例（來自 Eberhardt、Böckeler、yoaviram 或 hatmanstack 的報告），說明它在哪一階應該被護欄攔住。

---

## 延伸閱讀

- **Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall?**
  Colin Eberhardt（Scott Logic，2025 年 11 月）。
  URL：https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html
  和本章的關聯：最紮實的端到端 Spec Kit 計時測試，包含逐階段的行數和時間記錄。信任梯子決定「哪一階適合你的任務」，就要看這種量化基準。從「Plan」和「Implementation」段落開始讀。

- **Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl**
  Birgitta Böckeler（Thoughtworks，發表於 martinfowler.com，2025 年 10 月）。
  URL：https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
  和本章的關聯：三層 SDD 分類法（spec-first / spec-anchored / spec-as-source）是本章各階段「AI 角色定位」的理論基礎。她的工具失敗案例直接對應到本章每個護欄的設計理由。

- **Spec Driven Development isn't Waterfall**
  Marc Brooker（AWS VP/Distinguished Engineer，2026 年 4 月）。
  URL：https://brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html
  和本章的關聯：「迭代規格而非迭代實作」的最強正面論述——也是理解「為什麼信任梯子不等於瀑布回歸」的核心論點。全文短，建議完整閱讀。

- **How to write a good spec for AI agents**
  Addy Osmani（Google，2026 年 1 月）。
  URL：https://addyosmani.com/blog/good-spec/
  和本章的關聯：「指令詛咒」和「人是最終過濾器」兩個論點直接支撐本章的護欄設計邏輯。讀「curse of instructions」和「you remain the filter」兩節。

- **Spec-Driven Development: The Waterfall Strikes Back**
  François Zaninotto（Marmelab，2025 年 11 月）。
  URL：https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html
  和本章的關聯：具體列舉了「Markdown overload」、「double code review」、「spec non-compliance」等失敗模式，對應本章踩雷集錦的真實背景。從「Failure modes」段落開始讀。

- **Eric Evans, *Domain-Driven Design: Tackling Complexity in the Heart of Software*** （Addison-Wesley，2003 年）。
  和本章的關聯：Core domain / Generic subdomain 的分類，以及 Bounded Context 作為自治邊界的概念，是本章「哪個子系統適合哪一階」決策框架的理論根基。第 2、3、14 章。

---

本章是 Part 8 的最後一章正文。Final Project 會讓你把整個課程學到的所有工具、護欄、判斷框架，對一個真實小產品跑一遍端到端的 SDD，從規格到驗收。

→ [Final Project 對一個真實小產品跑完整 SDD](./final-project-ship-with-sdd.md)
