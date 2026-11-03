# AI x BDD 學習筆記：從 Agent Coding 到可執行規格的開發流水線

> 給已經天天用 Claude Code、讀過（或至少翻過）這個 repo 的 SDD 與 pstack 課，想把「agent 寫程式」變成「agent 照著可執行的規格寫程式，而且每一步都能驗證」的工程師。

這門課照水球軟體學院「AI x BDD：規格驅動全自動開發術」的五段課綱（Agent Coding → Skill 模組化 → SDD → TDD 與 E2E → BDD），把這個 repo 裡散在四門課的教材重新排成一條路線，再把盤點出來的缺口補上。缺口集中在動手的那一半：讓 agent 跑 BDD 迴圈、用 Playwright 驅動前端、3A 結構、規則與模板提取器、Spec Kit 1.x 客製化、測試先行的 SDD 流程。全課用同一個「會議室預約」領域，最後讓同一份 Gherkin 同時驗後端 API 和瀏覽器 UI。

## 為什麼學這個？

- **把 agent 的產出變成可檢查的鏈**：從 example map、feature 檔、spec、tasks、測試計畫到測試，每一環用 ID 串起來，再用一支小腳本檢查有沒有接上。agent 說「做完了」之前，腳本先說每一環都通過了。
- **補上既有教材沒有的動手部分**：repo 已經有 147 章講 agent、skill 與規格的概念，但沒有一段可執行的 BDD 迴圈，也沒有一行 Playwright。本課每一段程式都在本機實際跑過。
- **團隊協作的那一半**：BDD 的價值有一半在 PM、RD、QA 之間。本課把 Example Mapping、Three Amigos、Truth vs Plan 這些協作做法，接到 agent 能執行的產物上。

## 先修知識

- **Claude Code 日常使用**（程度：會用 slash command、知道 skill 與 hook 是什麼；不熟先讀 [AI 應用 Ch 5](../ai_applications/05-claude-code-basics.md)）
- **Python 與 pytest**（程度：寫過 fixture、看得懂 FastAPI 的路由）
- **規格與 BDD 的概念**（程度：讀過 [SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md) 與 [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)）
- 沒有也沒關係的：[pstack](../pstack/README.md) 與 [harness 工程](../harness_engineering/README.md)；本課用到時會連回對應章節

## 課程地圖

### Part 0 — 起點（Ch 0–1）
- [Ch 0 環境搭建：uv、pytest-bdd、Playwright、Spec Kit 與 Claude Code](./00-environment-setup.md)
- [Ch 1 全貌：從 Agent Coding 到可執行規格的五段路線](./01-course-map.md)（55 個單元與既有教材的對照表）

### Part 1 — Agent Coding 的原理與流程（Ch 2–6）
- [Ch 2 Vibe coding 與 AI 輔助工程：同一個功能看兩次](./02-vibe-coding-vs-engineering.md)
- [Ch 3 最小的 AI coding 迴圈：提案、約束、回饋](./03-propose-constrain-feedback.md)
- [Ch 4 Prompt files 的天花板：為什麼會有 Skill](./04-prompt-files-to-skills.md)
- [Ch 5 用 skill-creator 做出第一個 skill](./05-skill-creator.md)
- [Ch 6 工作流是長出來的：團隊怎麼養一套 agent 工作流](./06-workflows-are-grown.md)
- [練習 A：跑一次完整的提案、約束、回饋迴圈](./practice-a-first-loop.md)
- 搭配既有教材：[harness Ch 1–4](../harness_engineering/01-what-is-agent-harness.md)、[harness Ch 29 skills](../harness_engineering/29-skills.md)、[harness Ch 30 hooks](../harness_engineering/30-hooks.md)、[AI 應用 Ch 15](../ai_applications/15-skills.md)

### Part 2 — Skill 模組化工程（Ch 7–11）
- [Ch 7 混亂的起點：單檔 BDD skill 與一口氣做完的 autopilot](./07-messy-baseline.md)
- [Ch 8 需求翻譯三明治：Sales、PM、RD 之間掉了什麼](./08-translation-sandwich.md)
- [Ch 9 帶範例的 Rule File 與規則提取器](./09-rule-files.md)
- [Ch 10 Template 與 Script：把重複的形狀和步驟抽出來](./10-templates-and-scripts.md)
- [Ch 11 skill-engineering：用 skill 改造 skill](./11-skill-engineering-skill.md)
- [練習 B：把單檔 skill 拆成模組化 skill](./practice-b-modular-bdd-skill.md)
- 搭配既有教材：[pstack Ch 5 路由器設計](../pstack/05-router-design.md)、[pstack Ch 11 原則是詞彙](../pstack/11-principles-as-vocabulary.md)、[pstack Ch 28 skill 寫作學](../pstack/28-skill-authoring.md)、[pstack Ch 29 eval](../pstack/29-eval-playbook.md)

### Part 3 — SDD：Spec Kit 4+2 與客製化（Ch 12–18）
- [Ch 12 Spec Kit 1.x 重訪：指令變成 skill 之後](./12-spec-kit-1x.md)
- [Ch 13 解剖 /plan：plan.md、research.md 與怎麼 review](./13-plan-anatomy-review.md)
- [Ch 14 Artifact-first：先定黃金產物，再反推 skill](./14-artifact-first.md)
- [Ch 15 把 /plan 拆開：technical-research 與 system-analysis skill](./15-research-and-analysis-skills.md)
- [Ch 16 tasks 綁定器：把多份分析文件綁進 tasks.md](./16-tasks-binder.md)
- [Ch 17 Constitution 參數層與改寫 /specify](./17-constitution-and-presets.md)
- [Ch 18 clarify 訪談 skill：一次一題、有上限、寫回 spec](./18-clarify-skill.md)
- [練習 C：用自訂 SDD 流程做一個全端小功能](./practice-c-custom-sdd-flow.md)
- 搭配既有教材：[SDD Ch 22–29（SDD 的兩種意義到 Spec Kit 內部機制）](../spec_driven_development/22-two-meanings-of-spec-driven.md)、[SDD Ch 38 自建 pipeline](../spec_driven_development/38-build-your-own-pipeline.md)、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md)

### Part 4 — TDD 與前後端 E2E（Ch 19–25）
- [Ch 19 手打一遍 TDD：紅綠重構的會議室 kata](./19-tdd-by-hand.md)
- [Ch 20 AI 時代的 TDD：把紅、綠、重構分給 agent，用 hook 守門](./20-tdd-with-agents.md)
- [Ch 21 tdd skill 與 3A：把 Arrange、Act、Assert 寫成規則與 lint](./21-tdd-skill-aaa.md)
- [Ch 22 後端 E2E 先行：outside-in 雙迴圈](./22-backend-e2e-first.md)
- [Ch 23 前端 E2E 先行：用 Playwright 驅動 UI 開發](./23-frontend-e2e-playwright.md)
- [Ch 24 testplan skill：測試計畫是最可靠的計畫](./24-testplan-skill.md)
- [Ch 25 把 testplan 與 TDD 接進 SDD：TESTPLAN 階段與 RED 閘門](./25-tdd-in-sdd-pipeline.md)
- [練習 D：testplan → TDD → E2E 做出「取消預約」](./practice-d-cancel-booking.md)
- 搭配既有教材：[SDD Ch 25 TDD、BDD 與 MDA 的系譜](../spec_driven_development/25-tdd-bdd-mda-lineage.md)、[pstack Ch 8 Bug fix playbook](../pstack/08-bug-fix-playbook.md)、[pstack Ch 14 驗證原則](../pstack/14-verification-principles.md)

### Part 5 — BDD 與 PM/RD 協作（Ch 26–33）
- [Ch 26 BDD 到底在做什麼：從產物看 TDD 與 BDD 的差別](./26-bdd-vs-tdd-artifacts.md)
- [Ch 27 AI x BDD 迴圈與 bdd skill：先寫可執行規格，再寫 step definitions](./27-ai-bdd-loop.md)
- [Ch 28 Gherkin 與 DSL 寫作標準：做成 rule file 與 lint](./28-gherkin-dsl-style.md)
- [Ch 29 Example Mapping 與 Three Amigos：Journey 型與 Rule 型情境](./29-example-mapping.md)
- [Ch 30 一份 Gherkin、兩個 driver：前後端共用驗收測試](./30-one-gherkin-two-drivers.md)
- [Ch 31 把 BDD 放進 SDD：以 feature file 為粒度的 tasks](./31-bdd-in-sdd.md)
- [Ch 32 既有專案導入 BDD：從測試反推，重構成 Gherkin + StepDef](./32-brownfield-bdd.md)
- [Ch 33 Truth 與 Plan：兩種 artifact 的生命週期](./33-truth-vs-plan.md)
- [Final Project：會議室預約：從 example map 到雙 driver 驗收的完整流水線](./final-project-booking-pipeline.md)
- 搭配既有教材：[SDD Ch 10 驗收條件與 BDD](../spec_driven_development/10-acceptance-criteria-bdd.md)、[SDD 練習 B 三種記法](../spec_driven_development/practice-b-three-notations.md)、[SDD Ch 39 規格漂移](../spec_driven_development/39-spec-drift-rot.md)

## 學習方式建議

1. **先讀 Ch 1 的對照表**：每個主題都標了 repo 裡已有的章節。已經熟的主題，讀既有章節就好，本課的對應章可以快讀。
2. **每一段程式都自己跑一次，再故意弄壞**：本課的輸出都是實際執行的結果，agent 對話一律標「示意」或「實際執行紀錄」。你跑出不一樣的結果，先對照章首的版本釘定。
3. **讓腳本先說話**：每一段都有一支小檢查腳本。養成「改完先跑檢查、再請 agent 收工」的習慣，比背下任何一條規則都有用。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[pytest-bdd 官方文件](https://pytest-bdd.readthedocs.io/)**
  - 本課 BDD 部分的執行工具；`scenarios()`、`target_fixture`、`--generate-missing`、`--cucumberjson` 都以這裡為準
- **[Cucumber — Gherkin Reference](https://cucumber.io/docs/gherkin/reference/)**
  - Gherkin 關鍵字（Feature、Rule、Example、Background、Scenario Outline）的權威定義
- **[GitHub Spec Kit](https://github.com/github/spec-kit)**
  - Part 3 的工具；本課釘在 1.2.5，CHANGELOG 是追版本差異的第一手資料
- **[Playwright for Python](https://playwright.dev/python/docs/intro)**
  - Part 4 前端 E2E 與 Part 5 UI driver 的工具

### 推薦文章與書籍

- **[Dan North — Introducing BDD](https://dannorth.net/blog/introducing-bdd/)**：BDD 的起點，Part 5 的思想源頭
- **[Birgitta Böckeler — Understanding Spec-Driven-Development](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**：SDD 三個層次與實際使用的批評，Part 3 的對照組
- **《Specification by Example》— Gojko Adzic（Manning，2011）**：用例子寫規格的團隊實踐，Ch 29 的主要參考
- **《Growing Object-Oriented Software, Guided by Tests》— Steve Freeman、Nat Pryce（Addison-Wesley，2010）**：outside-in 雙迴圈的出處，Ch 22 與 Ch 30 的方法來源

### 讀完本課之後

- **[ai/pstack](../pstack/README.md)**：把本課的 skill 放進一套完整的 agent 工作流（router、playbook、驗證、自我改進）
- **[ai/spec_driven_development](../spec_driven_development/README.md)** 的 Part 7–8：SDD 的實證研究、安全、團隊導入與信任階梯

## 給 AI 協作者的備註

本課以「先講直覺 → 可跑範例 → 底層機制 → 踩雷 → 延伸閱讀」為固定節奏，繁體中文、口吻直接有觀點。版本釘定：Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、Playwright 1.64.1、Spec Kit 1.2.5、Claude Code 2.1.295、macOS 26.6（arm64）。課程結構參考水球軟體學院的公開課綱，但內容全為針對本 repo 的原創；新增或修訂章節時，請對照根目錄 `.claude/skills/learn/SKILL.md` 的深度 checklist。
