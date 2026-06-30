# 規格驅動開發學習筆記：用 SDD + DDD 和 AI 協作，把「意圖」變成可交付的軟體

> 給已經會寫 code、重度用 AI agent，但沒系統學過規格／需求／領域建模的工程師。

這一系列把「規格驅動開發（Spec-Driven Development / SDD）」拆開來教：當 LLM 讓「寫實作」變便宜，瓶頸就上移到「把意圖講清楚」。我們從零地基開始，學會把一個模糊的點子，一路走到 **領域模型 → 精確需求 → 規格 → 讓 AI 依規格實作並驗收** 的完整流水線——而且全程誠實面對它的限制，知道**什麼時候這套不該用**。

路線（這也回答了「我到底該學 SDD 還是 DDD」這個常見困惑）：**Spec-Driven Development 是主軸**；需求工程（Requirements Engineering）與 **領域驅動設計（Domain-Driven Design / DDD）是產生好規格的建模層**；AI/LLM 協作貫穿每一個 Part。

## 為什麼學這個？

- **AI 改變了成本結構**：實作變便宜之後，「規格」「領域模型」「驗收標準」才是真正的槓桿點。會寫 prompt 不稀奇，能把一個系統的意圖寫到 AI 不會誤解，才是稀缺技能。
- **底層理解的價值**：SDD 不是憑空冒出來的——它接在 60 年的需求工程、20 年的 DDD、近年的 BDD/TDD 之上。看懂它的家族樹，你才知道哪些是真進步、哪些是行銷話術。
- **職涯角度**：GitHub Spec Kit、AWS Kiro、Tessl 這波工具讓「spec-first 協作」變成團隊級議題。能設計規格流程、判斷該不該導入的人，比只會 vibe coding 的人值錢得多。

## 先修知識

- **會寫 code、讀得懂一種主流語言**（程度：能讀寫函式、class）
- **用過至少一個 AI coding agent**（Claude Code / Cursor / Copilot 都行）——你需要有「agent 在幹嘛」的體感
- **基本 Git**（spec 與 code 都進版本控制）
- 沒有也沒關係的：需求工程、DDD、形式化方法、Spec Kit/Kiro——這些正是本課要教你的
- 想更深入 AI agent 那一層，可搭配 repo 內的 [`ai/harness_engineering`](../harness_engineering/README.md) 與 [`ai/ai_applications`](../ai_applications/README.md)；本課 Part 6 會與它們銜接

## 課程地圖

### Part 0 — 環境與心智模型（Ch 0–2）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼「規格」突然重要了：AI 把瓶頸推到意圖上](./01-why-specs-matter-now.md)
- [Ch 2 先把三個詞分清楚：SDD vs DDD vs BDD/TDD](./02-sdd-ddd-bdd-tdd-map.md)

### Part 1 — 軟體工程地基（Ch 3–7）
- [Ch 3 SDLC 到底是什麼](./03-sdlc.md)
- [Ch 4 瀑布的真相：Royce 1970 與一個誤會](./04-waterfall-myth.md)
- [Ch 5 迭代與敏捷：用快速回饋換掉大份前期規格](./05-iterative-agile.md)
- [Ch 6 變更成本曲線——以及怎麼誠實引用它](./06-cost-of-change-curve.md)
- [Ch 7 規格 vs 設計 vs 實作](./07-spec-design-implementation.md)
- [練習 A：需求考古學——把模糊需求拆成各層產物](./practice-a-requirements-archaeology.md)

### Part 2 — 需求工程：把意圖寫到不含糊（Ch 8–13）
- [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)
- [Ch 9 User Story 與 INVEST](./09-user-stories-invest.md)
- [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)
- [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)
- [Ch 12 Use Case 與非功能需求](./12-use-cases-nfr.md)
- [Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)
- [練習 B：同一功能用三種記法各寫一遍](./practice-b-three-notations.md)

### Part 3 — 領域驅動設計：好規格的建模層（Ch 14–21）
- [Ch 14 為什麼 DDD：複雜性在領域，不在技術](./14-why-ddd.md)
- [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)
- [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)
- [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md)
- [Ch 18 子領域：Core / Supporting / Generic](./18-subdomains.md)
- [Ch 19 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md)
- [Ch 20 Repository / Domain Service / Factory / Domain Event](./20-repositories-services-events.md)
- [Ch 21 Event Storming 工作坊](./21-event-storming.md)
- [練習 C：對電商情境跑一場 Event Storming](./practice-c-event-storming.md)

### Part 4 — 規格驅動開發：方法論與系譜（Ch 22–26）
- [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)
- [Ch 23 從 Software 2.0 到 Software 3.0：Karpathy 的弧線](./23-software-2-to-3.md)
- [Ch 24 Sean Grove《The New Code》：規格作為單一真相來源](./24-the-new-code.md)
- [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)
- [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md)
- [練習 D：把需求＋領域模型寫成一份完整的 spec](./practice-d-write-a-spec.md)

### Part 5 — AI-native SDD 工具實戰（Ch 27–32）
- [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)
- [Ch 28 GitHub Spec Kit（二）：`/speckit.*` 工作流端到端](./28-spec-kit-workflow.md)
- [Ch 29 GitHub Spec Kit（三）：底層怎麼運作](./29-spec-kit-internals.md)
- [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)
- [Ch 31 其餘版圖：Tessl / BMAD / Cursor / Claude Code / Codex / Devin](./31-tooling-landscape.md)
- [Ch 32 工具橫向對比：什麼任務選什麼](./32-tool-comparison.md)
- [練習 E：用 Spec Kit 把練習 D 的 spec 跑成可動小功能](./practice-e-spec-kit-run.md)

### Part 6 — 把 SDD 接到 Claude / 自建 pipeline（Ch 33–38）
- [Ch 33 一個問題，兩個時代：DDD 與 SDD 是同一場仗](./33-ddd-sdd-same-fight.md)
- [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md)
- [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md)
- [Ch 36 領域模型作為 spec 的骨架](./36-domain-model-as-spec-backbone.md)
- [Ch 37 Modeling-first prompting：復現 codecentric 的 pipeline](./37-modeling-first-prompting.md)
- [Ch 38 自建一條 spec→plan→tasks→implement→verify 流水線](./38-build-your-own-pipeline.md)
- [練習 F：自建最小 SDD pipeline](./practice-f-mini-sdd-pipeline.md)

### Part 7 — 品質、驗證與誠實的限制（Ch 39–42）
- [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)
- [Ch 40 實測數據與復現報告](./40-empirical-evidence.md)
- [Ch 41 SDD 的安全面：prompt injection 與 lethal trifecta](./41-sdd-security.md)
- [Ch 42 什麼時候不要用 SDD](./42-when-not-to-use-sdd.md)

### Part 8 — 導入與落地（Ch 43–44）
- [Ch 43 把 SDD 織進團隊](./43-sdd-in-teams.md)
- [Ch 44 信任階梯：從輔助規格到自主實作](./44-trust-ladder.md)

### Final Project
- [Final Project：對一個真實小產品跑完整 SDD](./final-project-ship-with-sdd.md)

## 學習方式建議

1. **每章親手做過**：需求、模型、規格都動手寫出來，不要只讀。SDD 的功力長在「寫規格的肌肉」上，看別人的範例長不出來。
2. **故意把規格寫爛給 AI 看**：把驗收條件留一個含糊處，看 agent 怎麼誤解、怎麼補出你沒要的東西。失敗的輸出是最好的老師。
3. **拿真實任務餵它**：每學一個技法（EARS、bounded context、constitution），就拿你工作裡一個小功能套用一次。
4. **對著批判讀**：Part 7 不是補充，是核心。SDD 有大量被實測打臉的地方，帶著懷疑讀完整門課，你才不會變成下一個 10x 慢還不自知的人。

## 精選資料庫

整門課最值得反覆參照的資源；每章的「延伸閱讀」會指向更具體的小節。**注意**：AI 工具（Spec Kit、Kiro）演進極快，本課所列版本／指令／價格皆標注查證日期（2026-06-30），實作前請以官方最新為準。

### 必讀基礎

- **《Domain-Driven Design: Tackling Complexity in the Heart of Software》— Eric Evans（Addison-Wesley, 2003）**
  - DDD 的「藍皮書」；Part 3 的主要參考。讀第 1–4 章（model-driven design、ubiquitous language）與第 14 章（bounded context）。
- **[GitHub Spec Kit](https://github.com/github/spec-kit)** 與其 [`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)
  - 目前最被廣泛採用的開源 SDD 工具；Part 5 的第一手依據。`spec-driven.md` 是理解「規格作為主要產物」這個論點最清楚的單篇。
- **[AWS Kiro 官方文件](https://kiro.dev/docs/)**
  - Kiro 的 spec（requirements/design/tasks）、EARS、steering、hooks；Part 5 的另一支柱。

### 推薦文章 / talk

- **[Sean Grove — "The New Code"（AI Engineer World's Fair, 2025-06）](https://www.youtube.com/watch?v=8rABwKRsec4)**
  - 「規格才是源碼，code 只是它的投影」這個論點的代表作。Ch 24 整章圍繞它展開。
- **[Andrej Karpathy — "Software 2.0"（2017）](https://karpathy.medium.com/software-2-0-a64152b37c35)**
  - 整個「自然語言即程式」論述的思想起點；Ch 23 的主軸。
- **[Birgitta Böckeler — Spec-driven development 系列（martinfowler.com）](https://martinfowler.com/articles/exploring-gen-ai.html)**
  - Thoughtworks 對 Spec Kit / Kiro / Tessl 的實測與冷靜評估；Part 7 的良心來源。

### 讀完本課之後

- **《Implementing Domain-Driven Design》— Vaughn Vernon（Addison-Wesley, 2013）**：把 DDD 的戰術建模（aggregate、domain event）推到可落地的細節。
- **repo 內 [`ai/harness_engineering`](../harness_engineering/README.md)**：本課 Part 6 教你「把 SDD 接到 agent」，harness 課教你「那個 agent 框架本身怎麼刻」。兩者互補。

## 給 AI 協作者的備註

本課以「先講直覺 → 給可跑範例 → 挖底層機制 → 對比取捨 → 踩雷 → 誠實標注限制 → 延伸閱讀」為固定節奏，繁體中文、口吻直接有觀點。涉及 AI 工具的具體指令／版本／價格一律標注查證日期，並對未能查證者明確標示。新增或修訂章節時，請對照根目錄 `.claude/skills/learn/SKILL.md` 的深度 checklist。
