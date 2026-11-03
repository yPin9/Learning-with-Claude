# Ch 1 — 全貌：從 Agent Coding 到可執行規格的五段路線

> **目標**：說得出這門課五段路線的順序邏輯，也就是每一段補上前一段留下的哪個洞；能用本章的 55 個單元對照表，替任何一個主題找到 repo 裡已有的章節和本課新寫的章節；能畫出「可執行規格」這條產物鏈：從 example map 一路到兩個 driver 跑同一份驗收測試，中間每一環靠什麼 ID 串起來、由哪個腳本檢查。

> **環境**：概念章，只有「動手練習」需要 [Ch 0](./00-environment-setup.md) 建好的環境。覆蓋數字來自 2026-11-02 對這個 repo 的盤點；外部課綱以同一天讀到的課程頁為準。

## 為什麼需要這個？

這個 repo 跟 agent coding 與規格有關的課已經有四門：[規格驅動開發](../spec_driven_development/README.md)（Ch 0–44）、[pstack](../pstack/README.md)（Ch 0–31）、[agent harness 工程](../harness_engineering/README.md)（Ch 0–43）、[AI 應用](../ai_applications/README.md)（Ch 0–25）。加起來 147 章，再加 20 個練習和 4 個 final project。每一門自成一體，但如果你的問題是「怎麼讓 coding agent 照著可執行的規格把軟體做出來」，答案散在四門課裡，而且沒有一門把它從頭串到尾。

水球軟體學院的「AI x BDD：規格驅動全自動開發術」給了一個很好的閱讀順序。它的公開課綱分五章：

```
CH1 Agent Coding 的原理與流程
CH2 Skill 模組化工程
CH3 SDD 規格驅動開發（Spec Kit 4+2 流程與客製化）
CH4 TDD 測試驅動開發與前後端 E2E
CH5 BDD 行為驅動開發與 PM/RD 協作
```

我們把它的 55 個教學單元（不含 CH3 開頭那個介紹學習路徑的 3-0）逐一拿來對照 repo 的現有內容，結果是：**9 個完整、41 個部分、5 個完全沒有**。「部分」的那 41 個有一個共同模式：概念講過，動手的那一半沒有。具體缺的是：

- 沒有任何一段讓 coding agent 跑 BDD 迴圈的可執行教材
- 整個 repo 沒有一行 Playwright 程式
- 沒有 Arrange-Act-Assert（3A）
- 沒有從既有內容提取規則、模板、腳本的工具
- 沒有實際跑過 skill-creator
- Spec Kit 釘在 0.11.10（2026-06），而且照舊文件把 Claude Code 的指令寫成 `.claude/commands/`；實跑才發現它從 0.4.5 起就裝成 skill。本課改用 1.2.5（2026-10）重新實跑
- SDD 的範例幾乎都把測試排在最後

這門課做兩件事。第一，**照那五章的邏輯把 repo 現有的教材重新排一次**，就是本章的對照表。第二，**把缺的部分補上**，就是 Ch 2 之後的 32 章、4 個練習和 final project。補的時候盡量連回既有章節，不重講一遍。

> 我們只讀得到那門課的公開課綱，讀不到它的教材。本課所有內容都是針對這個 repo 原創的，對照表裡的單元主題也是我們的濃縮改寫，不是原文標題。

## 先建立直覺：從口頭交代到可執行的驗收條件

想像你要找人裝修浴室。

- 最隨性的做法是打電話說「浴室弄好一點」。師傅會做出東西，但做出什麼全看他怎麼理解。
- 好一點的做法是給師傅一本**施工手冊**：防水怎麼做、磁磚怎麼貼。他每次都會照同樣的方式做事。
- 再進一步是給**施工圖和工單**：這次要做什麼、尺寸多少、先做哪個再做哪個。
- 再進一步是附一張**驗收檢查表**：排水坡度幾度、試水幾小時不漏。師傅做完自己先驗一遍。
- 最後是屋主、設計師、師傅**三方一起簽的驗收條件**，每一條都有具體例子，現場可以逐條試，誰都不能各自解讀。

這門課的五段就是這五步：

```
  意圖（只在聊天裡）
     │  Part 1  agent 迴圈：提案、約束、回饋
     ▼
  skill：agent 怎麼做事               ← Part 2  模組化 skill（施工手冊）
     │
     ▼
  spec / plan / tasks：要做什麼        ← Part 3  SDD 與 Spec Kit 客製化（施工圖、工單）
     │
     ▼
  test plan / tests：怎麼知道做對了    ← Part 4  TDD 與前後端 E2E（驗收檢查表）
     │
     ▼
  example map / .feature：PM、RD、QA   ← Part 5  BDD（三方簽名的驗收條件）
  共用、每次改動都重跑的規格
```

每往下一步，就有一件原本只在人腦或聊天紀錄裡的東西變成檔案，而且越下面的檔案越能被機器執行。

這個類比有一處不成立：裝修只驗收一次，軟體的驗收條件每次改動都要重跑。正是「每次都重跑」讓可執行規格值得它的成本。

順序也不是隨便排的。後一段會用到前一段的工具：Part 5 的 BDD 迴圈需要 Part 1 的 hook 迴圈去擋 agent 改 feature 檔，需要 Part 2 的模組化 skill 來打包流程，需要 Part 3 的 SDD 流程來放 feature 檔，也需要 Part 4 的紅綠紀律和 driver 分層。

> 如果你對 agent 迴圈本身（模型怎麼呼叫工具、harness 怎麼執行）還不熟，先讀 [harness Ch 1](../harness_engineering/01-what-is-agent-harness.md) 和 [harness Ch 3](../harness_engineering/03-mental-model.md)。本課預設你知道 agent = 模型 + harness。

## 五段路線，每一段補哪個洞

| Part | 對應外部課綱 | 這一段要回答的問題 | 主要產物 | 本課章節 |
|---|---|---|---|---|
| 1 | CH1 Agent Coding | agent 每一步受什麼約束、拿到什麼回饋？ | CLAUDE.md、`settings.json`、hook、第一個 skill | Ch 2–6、練習 A |
| 2 | CH2 Skill 模組化 | 一個 skill 長大以後，怎麼不變成一團亂？ | 脊椎 + rule file + template + script | Ch 7–11、練習 B |
| 3 | CH3 SDD | 要做什麼？怎麼寫成 agent 能照做的文件？ | constitution、spec、plan、research、tasks、preset | Ch 12–18、練習 C |
| 4 | CH4 TDD + E2E | 怎麼證明做對了，而且 agent 沒有作弊？ | test plan、紅燈閘門、3A lint、Playwright spec | Ch 19–25、練習 D |
| 5 | CH5 BDD | PM、RD、QA 怎麼對同一份規格有同一個理解？ | example map、feature 檔、step definitions、driver | Ch 26–33、Final |

**Part 1** 處理最底層的問題：agent 不是一次對話就做完事，它在一個「提案 → 動手 → 看結果」的迴圈裡跑。這一段讓你能控制迴圈的三個接口：它提什麼方案（提案）、它不准碰什麼（約束）、它從哪裡知道自己錯了（回饋）。Part 1 留下的洞是：規則越寫越多，全塞在 CLAUDE.md 裡會互相干擾。

**Part 2** 把規則從一個大檔拆成模組：主幹只寫流程，規則各自帶範例，重複的形狀變成 template，重複的步驟變成 script。留下的洞是：skill 管得了「怎麼做」，管不了「做什麼」。

**Part 3** 用 SDD 把「做什麼」寫成文件，再把 Spec Kit 的大步驟拆成單一職責的 skill，各自有檢查腳本。留下的洞是：SDD 的產物描述了意圖，但沒有證明實作符合意圖，而且 Spec Kit 預設把測試排在後面。

**Part 4** 用 TDD 和 E2E 補上證明：先寫測試計畫，再讓測試先紅，紅的原因要對，才准實作。留下的洞是：測試是開發者寫的，測的是開發者理解的需求，PM 讀不懂。

**Part 5** 用 BDD 把需求的例子變成三方都讀得懂、機器也跑得動的 feature 檔，再把它接回 SDD 的 tasks 粒度，最後分清楚哪些產物代表「現在的事實」、哪些只是「這次改動的計畫」。

## 55 個單元對照表

怎麼讀這張表：

- **覆蓋**是盤點時的判斷。「完整」代表 repo 裡有可動手的教材涵蓋這個主題的主要內容；「部分」代表概念有但動手缺，或只涵蓋一部分；「缺」代表 repo 裡找不到。
- **repo 既有教材**最多列三個最相關的章節。主題相同不代表深度相同，點進去讀一段再決定要不要跳過本課的對應章。
- **本課補上**是 Ch 2 之後新寫的章節；「—」代表 repo 既有教材已經夠，本課不另寫。
- 外部課綱的編號從 3-8 直接跳到 3-10，我們照原編號列。CH3 另有一個 3-0，是該章的學習路徑導覽，不是教學主題，沒有列入，所以課程頁上的 56 個單元在這裡是 55 個。

#### CH1: Agent Coding 的原理和流程

| 單元 | 主題 | repo 既有教材 | 覆蓋 | 本課補上 |
|---|---|---|---|---|
| 1-1 | Vibe coding 與 AI coding 的正確認知 | [SDD Ch 23](../spec_driven_development/23-software-2-to-3.md)、[SDD Ch 42](../spec_driven_development/42-when-not-to-use-sdd.md)、[SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) | 部分 | [Ch 2](./02-vibe-coding-vs-engineering.md) |
| 1-2 | Agent 怎麼運作 | [harness Ch 1](../harness_engineering/01-what-is-agent-harness.md)、[harness Ch 2](../harness_engineering/02-anatomy-of-agent-run.md)、[harness Ch 4](../harness_engineering/04-minimal-agent-loop.md) | 完整 | — |
| 1-3 | 企業級工作流是迭代出來的 | [pstack Ch 30](../pstack/30-self-improvement-loop.md)、[harness Ch 11](../harness_engineering/11-system-prompt-design.md)、[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) | 部分 | [Ch 6](./06-workflows-are-grown.md) |
| 1-4 | 最基礎的工作流：提案、約束、回饋 | [AI 應用 Ch 5](../ai_applications/05-claude-code-basics.md)、[pstack Ch 6](../pstack/06-prompting-poteto-mode.md)、[pstack Ch 3](../pstack/03-run-lifecycle.md) | 部分 | [Ch 3](./03-propose-constrain-feedback.md)、[練習 A](./practice-a-first-loop.md) |
| 1-5 | Prompt files 的限制與 Skill 的由來 | [harness Ch 29](../harness_engineering/29-skills.md)、[AI 應用 Ch 15](../ai_applications/15-skills.md)、[SDD Ch 31](../spec_driven_development/31-tooling-landscape.md) | 部分 | [Ch 4](./04-prompt-files-to-skills.md) |
| 1-6 | Skill 怎麼處理 context 注意力問題 | [harness Ch 29](../harness_engineering/29-skills.md)、[harness Ch 10](../harness_engineering/10-context-window-budget.md)、[pstack Ch 0](../pstack/00-environment-setup.md) | 部分 | [Ch 4](./04-prompt-files-to-skills.md) |
| 1-7 | 用 Skill Creator 建 skill | [pstack Ch 28](../pstack/28-skill-authoring.md)、[AI 應用 Ch 15](../ai_applications/15-skills.md)、[pstack Ch 29](../pstack/29-eval-playbook.md) | 部分 | [Ch 5](./05-skill-creator.md) |

#### CH2: Skill 模組化工程

| 單元 | 主題 | repo 既有教材 | 覆蓋 | 本課補上 |
|---|---|---|---|---|
| 2-1 | 用 Skill Creator 建 BDD skill | [pstack Ch 28](../pstack/28-skill-authoring.md)、[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)、[harness Ch 29](../harness_engineering/29-skills.md) | 部分 | [Ch 7](./07-messy-baseline.md) |
| 2-2 | Autopilot 式 skill 一次做完 app | [SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md)、[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)、[SDD Ch 30](../spec_driven_development/30-kiro.md) | 部分 | [Ch 7](./07-messy-baseline.md) |
| 2-3 | Sales、PM、RD 的需求翻譯三明治 | [SDD Ch 15](../spec_driven_development/15-ubiquitous-language.md)、[SDD Ch 33](../spec_driven_development/33-ddd-sdd-same-fight.md)、[SDD Ch 21](../spec_driven_development/21-event-storming.md) | 部分 | [Ch 8](./08-translation-sandwich.md) |
| 2-4 | Autopilot skill 為什麼失靈 | [pstack Ch 25](../pstack/25-autonomous-run.md)、[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)、[AI 應用 Ch 15](../ai_applications/15-skills.md) | 部分 | [Ch 7](./07-messy-baseline.md) |
| 2-5 | Skill 模組化概論 | [pstack Ch 5](../pstack/05-router-design.md)、[pstack Ch 11](../pstack/11-principles-as-vocabulary.md)、[pstack Ch 28](../pstack/28-skill-authoring.md) | 完整 | [Ch 11](./11-skill-engineering-skill.md) |
| 2-6 | Skill 的脊椎（SKILL.md 主幹） | [pstack Ch 2](../pstack/02-anatomy-of-poteto-mode.md)、[pstack Ch 28](../pstack/28-skill-authoring.md)、[pstack Final](../pstack/final-project-course-mode.md) | 完整 | [Ch 11](./11-skill-engineering-skill.md) |
| 2-7 | 帶範例的 Rule File | [AI 應用 Ch 15](../ai_applications/15-skills.md)、[pstack Ch 11](../pstack/11-principles-as-vocabulary.md)、[pstack Ch 23](../pstack/23-unslop-no-comments.md) | 部分 | [Ch 9](./09-rule-files.md) |
| 2-8 | Rule File 提取器 | [pstack Ch 30](../pstack/30-self-improvement-loop.md)、[pstack Ch 21](../pstack/21-verification-skills.md)、[pstack Final](../pstack/final-project-course-mode.md) | 部分 | [Ch 9](./09-rule-files.md) |
| 2-9 | Template File 與提取器 | [SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md)、[pstack 練習 D](../pstack/practice-d-plan-and-log.md) | 部分 | [Ch 10](./10-templates-and-scripts.md) |
| 2-10 | Script 與提取器 | [pstack Ch 15](../pstack/15-delegation-meta-autonomy.md)、[pstack Ch 21](../pstack/21-verification-skills.md)、[pstack Final](../pstack/final-project-course-mode.md) | 部分 | [Ch 10](./10-templates-and-scripts.md) |
| 2-11 | skill-engineering skill | [pstack Ch 28](../pstack/28-skill-authoring.md)、[pstack Ch 29](../pstack/29-eval-playbook.md)、[pstack Ch 30](../pstack/30-self-improvement-loop.md) | 部分 | [Ch 11](./11-skill-engineering-skill.md)、[練習 B](./practice-b-modular-bdd-skill.md) |

#### CH3: SDD 規格驅動開發（Spec Kit 4+2 流程與客製化）

| 單元 | 主題 | repo 既有教材 | 覆蓋 | 本課補上 |
|---|---|---|---|---|
| 3-1 | 提示工程 vs 上下文工程 | [harness Ch 3](../harness_engineering/03-mental-model.md)、[harness Ch 10](../harness_engineering/10-context-window-budget.md)、[AI 應用 Ch 4](../ai_applications/04-prompting.md) | 完整 | — |
| 3-2 | SDD 是什麼、是不是 buzzword | [SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md)、[SDD Ch 26](../spec_driven_development/26-skeptics-case.md)、[SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) | 完整 | — |
| 3-3 | Spec Kit 的 4+2 流程 | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) | 完整 | [Ch 12](./12-spec-kit-1x.md) |
| 3-4 | constitution 與 specify | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) | 完整 | [Ch 12](./12-spec-kit-1x.md) |
| 3-5 | /clarify 澄清 | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md)、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) | 完整 | [Ch 12](./12-spec-kit-1x.md)、[Ch 18](./18-clarify-skill.md) |
| 3-6 | /plan：plan.md、research.md 與 review | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md)、[SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md) | 部分 | [Ch 13](./13-plan-anatomy-review.md) |
| 3-7 | /plan：系統分析產物 | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md) | 部分 | [Ch 13](./13-plan-anatomy-review.md) |
| 3-8 | /tasks 與 /implement | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md) | 完整 | [Ch 12](./12-spec-kit-1x.md)、[Ch 16](./16-tasks-binder.md) |
| 3-10 | 客製化 /specify | [SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md)、[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) | 部分 | [Ch 17](./17-constitution-and-presets.md) |
| 3-11 | Artifact-first：從產物反推 skill | [pstack Ch 27](../pstack/27-multi-phase-autopilot.md)、[pstack Ch 28](../pstack/28-skill-authoring.md)、[pstack Ch 30](../pstack/30-self-improvement-loop.md) | 部分 | [Ch 14](./14-artifact-first.md) |
| 3-12 | 技術研究 skill | [pstack Ch 18](../pstack/18-architect-arena.md)、[harness 練習 D](../harness_engineering/practice-d-multi-agent-research.md)、[SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md) | 部分 | [Ch 15](./15-research-and-analysis-skills.md) |
| 3-13 | 系統分析 skill：多端點盤點 | [SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md)、[pstack Ch 21](../pstack/21-verification-skills.md)、[pstack Ch 7](../pstack/07-investigation-how-why.md) | 部分 | [Ch 15](./15-research-and-analysis-skills.md) |
| 3-14 | 單一職責的 planner skills | [SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[AI 應用 Ch 15](../ai_applications/15-skills.md)、[pstack Ch 5](../pstack/05-router-design.md) | 部分 | [Ch 15](./15-research-and-analysis-skills.md) |
| 3-15 | 把分析文件綁進 tasks.md | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[pstack Ch 27](../pstack/27-multi-phase-autopilot.md) | 部分 | [Ch 16](./16-tasks-binder.md) |
| 3-16 | 用客製化流程做全端應用 | [SDD Final](../spec_driven_development/final-project-ship-with-sdd.md)、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md)、[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) | 部分 | [練習 C](./practice-c-custom-sdd-flow.md) |
| 3-17 | clarify 訪談 skill | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 8](../spec_driven_development/08-why-requirements-hard.md)、[pstack Ch 28](../pstack/28-skill-authoring.md) | 部分 | [Ch 18](./18-clarify-skill.md) |
| 3-18 | Constitution 作為客製化空間 | [SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md)、[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[SDD Ch 34](../spec_driven_development/34-ubiquitous-language-as-glossary.md) | 部分 | [Ch 17](./17-constitution-and-presets.md) |

#### CH4: TDD 測試驅動開發與前後端 E2E

| 單元 | 主題 | repo 既有教材 | 覆蓋 | 本課補上 |
|---|---|---|---|---|
| 4-1 | TDD 概念 | [SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md)、[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md)、[pstack Ch 14](../pstack/14-verification-principles.md) | 部分 | [Ch 19](./19-tdd-by-hand.md) |
| 4-2 | AI 之前的 TDD：手打紅綠重構 | [pstack Ch 8](../pstack/08-bug-fix-playbook.md)、[pstack Ch 9](../pstack/09-feature-refactoring.md)、[open_source Final](../../dev_tools/open_source/final-project-real-contribution.md) | 部分 | [Ch 19](./19-tdd-by-hand.md) |
| 4-3 | AI 之後的 TDD：每階段交給 agent | [pstack Ch 3](../pstack/03-run-lifecycle.md)、[pstack Ch 8](../pstack/08-bug-fix-playbook.md)、[pstack Ch 9](../pstack/09-feature-refactoring.md) | 部分 | [Ch 20](./20-tdd-with-agents.md) |
| 4-4 | TDD skill 加入 3A | [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)、[pstack Ch 28](../pstack/28-skill-authoring.md)、[pstack Ch 8](../pstack/08-bug-fix-playbook.md) | 缺 | [Ch 21](./21-tdd-skill-aaa.md) |
| 4-5 | E2E 驅動後端開發 | [pstack Ch 21](../pstack/21-verification-skills.md)、[pstack Ch 3](../pstack/03-run-lifecycle.md)、[cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md) | 部分 | [Ch 22](./22-backend-e2e-first.md) |
| 4-6 | E2E 驅動前端開發 | [pstack Ch 21](../pstack/21-verification-skills.md)、[pstack Ch 31](../pstack/31-critique-tradeoffs.md)、[AI 應用 Ch 12](../ai_applications/12-using-mcp.md) | 缺 | [Ch 23](./23-frontend-e2e-playwright.md) |
| 4-7 | testplan skill | [pstack Ch 27](../pstack/27-multi-phase-autopilot.md)、[pstack 練習 D](../pstack/practice-d-plan-and-log.md)、[SDD Ch 11](../spec_driven_development/11-ears-notation.md) | 部分 | [Ch 24](./24-testplan-skill.md) |
| 4-8 | testplan 與 TDD 接進 SDD | [SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md)、[SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md) | 部分 | [Ch 25](./25-tdd-in-sdd-pipeline.md)、[練習 D](./practice-d-cancel-booking.md) |

#### CH5: BDD 行為驅動開發與 PM/RD 協作

| 單元 | 主題 | repo 既有教材 | 覆蓋 | 本課補上 |
|---|---|---|---|---|
| 5-1 | 從產物看 TDD 與 BDD 的差別 | [SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md)、[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md)、[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) | 部分 | [Ch 26](./26-bdd-vs-tdd-artifacts.md) |
| 5-2 | BDD skill：先規格、後 step definitions | [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)、[SDD Final](../spec_driven_development/final-project-ship-with-sdd.md)、[pstack Ch 8](../pstack/08-bug-fix-playbook.md) | 部分 | [Ch 27](./27-ai-bdd-loop.md) |
| 5-3 | 導入 AI x BDD 的整體流程 | [SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md)、[SDD Final](../spec_driven_development/final-project-ship-with-sdd.md)、[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) | 部分 | [Ch 29](./29-example-mapping.md)、[Final](./final-project-booking-pipeline.md) |
| 5-4 | 從 testplan 反推，既有專案導入 BDD | [pstack Ch 9](../pstack/09-feature-refactoring.md)、[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md)、[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) | 缺 | [Ch 32](./32-brownfield-bdd.md) |
| 5-5 | Gherkin 與 DSL 寫作標準 | [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)、[SDD 練習 B](../spec_driven_development/practice-b-three-notations.md)、[pstack Ch 28](../pstack/28-skill-authoring.md) | 部分 | [Ch 28](./28-gherkin-dsl-style.md) |
| 5-6 | 把整個專案的測試改成 BDD 結構 | [pstack Ch 14](../pstack/14-verification-principles.md)、[cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md)、[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) | 缺 | [Ch 32](./32-brownfield-bdd.md) |
| 5-7 | Journey 型驗收與 Specification by Example | [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)、[SDD Ch 21](../spec_driven_development/21-event-storming.md)、[pstack Ch 21](../pstack/21-verification-skills.md) | 部分 | [Ch 29](./29-example-mapping.md) |
| 5-8 | 一份 Gherkin 落地前後端（DSL refine） | [SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md)、[pstack Ch 21](../pstack/21-verification-skills.md)、[SDD 練習 B](../spec_driven_development/practice-b-three-notations.md) | 缺 | [Ch 30](./30-one-gherkin-two-drivers.md) |
| 5-9 | 把 BDD 放進 SDD | [SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md)、[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) | 部分 | [Ch 31](./31-bdd-in-sdd.md) |
| 5-10 | 以 feature file 為粒度的 tasks | [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)、[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、[pstack Ch 27](../pstack/27-multi-phase-autopilot.md) | 部分 | [Ch 31](./31-bdd-in-sdd.md) |
| 5-11 | Truth 與 Plan 兩種 artifact（上） | [SDD Ch 39](../spec_driven_development/39-spec-drift-rot.md)、[SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md)、[SDD Ch 26](../spec_driven_development/26-skeptics-case.md) | 部分 | [Ch 33](./33-truth-vs-plan.md) |
| 5-12 | Truth 與 Plan 兩種 artifact（下） | [SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md)、[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)、[SDD Ch 24](../spec_driven_development/24-the-new-code.md) | 部分 | [Ch 33](./33-truth-vs-plan.md) |

各章的統計：

| 外部課綱 | 單元數 | 完整 | 部分 | 缺 |
|---|---|---|---|---|
| CH1 Agent Coding | 7 | 1 | 6 | 0 |
| CH2 Skill 模組化 | 11 | 2 | 9 | 0 |
| CH3 SDD | 17 | 6 | 11 | 0 |
| CH4 TDD + E2E | 8 | 0 | 6 | 2 |
| CH5 BDD | 12 | 0 | 9 | 3 |
| 合計 | 55 | 9 | 41 | 5 |

缺口集中在後兩段。CH4 和 CH5 一個「完整」都沒有，五個「缺」全在這裡：TDD skill 裡的 3A、E2E 驅動前端、把既有專案改成 BDD、整個測試套件的 BDD 結構、一份 Gherkin 同時落地前後端。這也是本課 Part 4 和 Part 5 篇幅最大的原因。

## 底層機制：可執行規格這條產物鏈怎麼串起來

「可執行規格」聽起來像一種文件格式，其實它是一條**鏈**：每一份產物都指向上一份，而每一個指向都能用腳本檢查。這門課從頭到尾用同一組 ID 把鏈串起來：

```
example_map.yaml          R1「同一間會議室不能重疊」+ 具體例子 + 待決問題
   │  example_map.py（Ch 8）檢查每條規則都有例子，產生 Gherkin 骨架
   ▼
features/booking.feature  Rule: R1 … / @sc_book_02 Scenario: …
   │  gherkin_lint.py（Ch 28）檢查寫法
   ▼
spec.md                   FR-002 … 驗收條件指向 feature 檔
   │  plan_lint.py（Ch 13）、inventory.py（Ch 15）
   ▼
tasks.md                  - [ ] T004 [US1] [FR-002] [@sc_book_02] …
   │  task_coverage.py（Ch 16）、scenario_task_map.py（Ch 31）
   ▼
testplan                  TP-002  level: unit  trace: R1, FR-002, @sc_book_02
   │  testplan_lint.py（Ch 24）
   ▼
tests/unit/…::test_tp002_…  red_gate.py（Ch 25）：先紅，而且紅的原因對
   │  aaa_lint.py（Ch 21）
   ▼
drivers: api / ui         同一份 feature 用兩種 driver 各跑一次（Ch 30）
   │
   ▼
truth                     features/、contracts、glossary
                          truth_drift.py（Ch 33）：事實和程式還對得上嗎
```

三件事讓這條鏈「可執行」：

1. **ID**：`R1`（業務規則）、`FR-002`（功能需求）、`US1`（使用者故事）、`T004`（任務）、`TP-002`（測試案例）、`@sc_book_02`（情境標籤）、`EP:POST /bookings`（端點）。ID 讓「這個任務在實作哪條規則」從一句話變成一個可以 grep 的事實。
2. **檢查腳本**：每個箭頭都有一支小腳本確認它接上了：每條規則有例子、每個情境有任務、每個任務有來源、每個測試先紅。腳本便宜、穩定，可以放進 hook 或 CI，每次改動都跑。
3. **會跑的測試**：鏈的末端是真的會執行的 feature 和測試，而且同一份 feature 能同時驗後端 API 和前端 UI。

為什麼用腳本，不全交給 LLM 當評審？因為「有沒有接上」是確定性的問題，腳本答得又快又不會累；LLM 評審留給「接得對不對」這種語意問題。這是 [pstack Ch 21](../pstack/21-verification-skills.md) 的驗證技能思路，也是 [pstack Ch 14](../pstack/14-verification-principles.md)「證明它能動」原則的延伸。

反過來說，**產物沒有 ID、沒有檢查，就會漂移**：spec 說一套、程式做一套，沒有人發現。這正是 [SDD Ch 39](../spec_driven_development/39-spec-drift-rot.md) 講的規格腐爛。本課的腳本是對這個問題的工程回答。

## 貫穿全課的範例：會議室預約

所有章節共用同一個領域，讓你不必每章重新理解一個新例子：

| 項目 | 內容 |
|---|---|
| 會議室 | `Taipei`、`Tokyo`、`Seoul` |
| 人 | `Alice`、`Bob`、`Carol` |
| 時間 | 同一天的 `HH:MM`，區間是半開的 `[start, end)`：10:00–11:00 和 11:00–12:00 **不**衝突 |
| R1 | 同一間會議室不能重疊預約 → 409 `Taipei is taken 10:00-11:00 by Bob` |
| R2 | 結束時間必須晚於開始時間 → 422 `end must be after start` |
| R3（選用） | 只能訂辦公時間 08:00–20:00 → 422 |
| R4 | 只有預約人能取消 → 403；預約不存在 → 404；成功 → 204 |
| API | `POST /bookings`、`GET /bookings`、`DELETE /bookings/{id}?by=…`、測試用 `POST /reset` |
| UI | 一頁表單：Room、Start、End、Name 四個欄位，`Book` 按鈕，結果寫在 `role=status` 的段落 |

選它的理由：小到一章寫得完；有真實的邊界案例（相鄰時段到底算不算重疊，PM 和 RD 常常各有答案）；有後端也有前端；有權限規則（R4）；PM 不用懂技術也看得懂。

每一章的實驗室都從空目錄開始，彼此獨立，所以細節會有章與章之間的差異：前幾章用的是只有 `POST /bookings` 的最小版 API，`id`、取消與 `GET` 要到後面才加；同一條規則的 Then 句型也不只一種寫法（例如 R2 至少有四種措辭）。每章都會說明自己用哪一種。這種漂移在真實團隊裡同樣會發生，Ch 28 討論怎麼用詞彙表（glossary）與 lint 把它收斂。

[Ch 0](./00-environment-setup.md) 會帶你跑一個已驗證的雛形：一份 feature 檔、兩個 driver，同一組情境分別打 HTTP API 和瀏覽器。

## 這門課和既有課程怎麼分工

| 既有課程 | 它教什麼 | 本課怎麼用它 |
|---|---|---|
| [SDD](../spec_driven_development/README.md) | 需求工程、DDD、SDD 的概念與歷史、Spec Kit 0.11.10、Kiro | Part 3 從 Spec Kit 1.x 與客製化接著講；Ch 10 的 BDD 概念是 Part 5 的前置 |
| [pstack](../pstack/README.md) | skill 的設計與寫作、驗證、eval、自我改進迴路 | Part 2 的方法來源；本課把它套到 BDD 與 TDD 的 skill 上 |
| [harness 工程](../harness_engineering/README.md) | agent 的底層機制：迴圈、context、工具、hook、skill、subagent | Part 1 的底層；需要機制細節時連回去 |
| [AI 應用](../ai_applications/README.md) | Claude Code 基本操作、MCP、Skills 入門 | 先修 |

三種讀法：

1. **讀過 SDD**：Part 1 從 Ch 3 開始；Part 3 先讀 Ch 12 的版本差異，再挑 Ch 13–18。
2. **讀過 pstack**：Part 2 的 Ch 7 和 Ch 11 可以快讀，重點放在 Ch 9–10 的提取器。
3. **都沒讀過**：先讀 [AI 應用 Ch 5](../ai_applications/05-claude-code-basics.md)、[harness Ch 1](../harness_engineering/01-what-is-agent-harness.md)、[SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md)、[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)，再照順序讀本課。

### 盤點時順手修掉的既有錯誤

對照的過程中，我們發現既有教材有幾處錯，已經直接修正：

| 位置 | 原本的問題 | 修正 |
|---|---|---|
| [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) | pytest-bdd 範例跑不起來：沒有 `scenarios()` 綁定、沒有 `target_fixture`、`pytest --bdd` 這個選項不存在；Then 步驟自己扣款再斷言，等於沒測 | 換成在 pytest-bdd 9.0.1 實測過的版本，附真實輸出和故意弄壞的對照 |
| [SDD Final](../spec_driven_development/final-project-ship-with-sdd.md) | `/speckit.analyze` 排在 `/speckit.tasks` 之前 | 改到 tasks 之後；analyze 要有 tasks.md 才能跑 |
| SDD Ch 30、32、34、44 | Spec Kit 路徑寫成 `.specify/specs/`、`.specify/constitution.md` | 0.11.10 與 1.2.5 的腳本都用 repo 根目錄的 `specs/` 和 `.specify/memory/constitution.md` |
| [SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) | preset 範例建了 `.specify/presets/my-company`，卻 `add ./my-company-preset`，也沒有 `preset.yml` | 改成專案層覆蓋與 `specify preset add --dev` 兩條路 |
| SDD Ch 28、34、44 | 「constitution 在 context 最前面」「clarify 會插入 NEEDS CLARIFICATION 標記」 | 依 0.11.10 指令模板改寫：各指令存在就先讀 constitution；標記是 specify 插的，clarify 一次問一題並寫回 spec |
| [SDD Ch 24](../spec_driven_development/24-the-new-code.md) | 沒講 SDD 產物和上下文工程的關係 | 補一節，連到 harness Ch 3 與 Ch 10 |
| SDD Ch 0、27、28、29、練習 E | 把 0.11.10 裝給 Claude Code 的指令寫成 `.claude/commands/speckit.*.md`；Ch 27 的「實際輸出」是照文件編的 | 依 0.11.10 實跑結果改成 `.claude/skills/speckit-*/SKILL.md`，換上真實輸出，並說明在 Claude Code 裡叫用名稱是 `/speckit-plan` |
| [SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) | 「Spec Kit 不在 PyPI」 | 0.11.10 的 README 只寫了 git 安裝，但 PyPI 從 0.11.5 起就有官方發布 |
| [SDD Ch 31](../spec_driven_development/31-tooling-landscape.md) | Claude Code 會同時讀 AGENTS.md 和 CLAUDE.md | 依官方文件改成：預設只在沒有 CLAUDE.md 時讀 AGENTS.md，要兩份都用就在 CLAUDE.md 裡 `@AGENTS.md` 匯入 |
| [AI 應用 Ch 5](../ai_applications/05-claude-code-basics.md)、[Ch 15](../ai_applications/15-skills.md) | settings.json 用了不存在的頂層 `permissionMode`；skill description 上限只寫 1536 | 改成 `permissions.defaultMode`；說明規格上限 1024、Claude Code 清單截在 1536 |
| [SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md) | TDD 範例貼了「3 failed」，實際跑是 `ModuleNotFoundError`，一個測試都沒跑 | 換上真實輸出，補一個只有介面的骨架，讓三個測試真的因斷言失敗而紅 |
| SDD Ch 28、29 | 說 clarify 會逐一處理 `[NEEDS CLARIFICATION]` 標記；說 `specify self upgrade` 會更新 core 模板 | 標記由 specify 自己問完；clarify 另外按類別清單掃描、最多問 5 題；`self upgrade` 只升級 CLI |
| SDD Ch 27、28、29 | 說 `create-new-feature.sh` 會 `git checkout -b`、沒有 git 會失敗；spec 與 tasks 模板骨架是照文件推想的（Overview、User Journeys…） | 依 0.11.10 實際腳本與模板改寫：預設不建 branch（git extension 從 0.10.0 起改為選用）、沒有 git 也能跑；骨架換成模板原文節錄 |
| [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) | 說 brownfield 時先跑 `/speckit.converge`「讓規格追上現況」 | converge 只在 tasks.md 追加任務、模板禁止它改 spec，所以是讓程式碼追上規格 |

## 對比與取捨

把 AI coding 的紀律導入團隊，常見有六種路線：

| 路線 | 做法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|
| 只靠 prompt | 在聊天裡直接要結果 | 最快 | 無法驗證、無法重現 | 一次性腳本、探索 |
| 第一天裝框架 | 裝一套 Spec Kit、prompt pack 或多角色框架 | 立刻有流程可走 | 流程不貼合團隊，產物多到沒人審 | 想觀摩別人怎麼做 |
| 只做 SDD | spec → plan → tasks → implement | 意圖寫下來了 | 沒有可執行的驗收，測試排最後 | 需求清楚、團隊已有測試文化 |
| 只做 TDD | 紅綠重構 | 單元行為正確 | 測的是開發者理解的需求 | 演算法、函式庫 |
| 只做 BDD | 寫 feature 檔 | 有共同語言 | 沒有 agent 流程，step definitions 容易腐化 | 已有 BDD 文化的團隊 |
| 本課的串法 | 五段疊起來，用 ID 和腳本把產物串成鏈 | 每一環都可檢查、可重跑 | 前期成本高，腳本也要維護 | 要長期維護、多人協作的產品 |

本課不主張每個專案都走完五段。一支跑一次的資料轉換腳本，用第一種路線就好。我們主張的是：**你要知道自己跳過了哪一環、跳過的代價是什麼**。[SDD Ch 42](../spec_driven_development/42-when-not-to-use-sdd.md) 對「什麼時候不該用 SDD」有完整的討論，同樣的判斷也適用於 BDD。

## 踩雷集錦

1. **以為 BDD 是一種測試工具**：很多團隊導入 BDD 的第一步是裝 Cucumber 或 pytest-bdd。但 BDD 先是協作方法，分成發現（discovery）、表述（formulation）、自動化（automation）三段，工具只服務最後一段。只導入工具，得到的是寫得比較囉嗦的測試。Ch 26 從產物拆開講這件事。
2. **以為有了 spec 就不用先寫測試**：spec 說要什麼，測試證明做到了，兩者回答不同的問題。SDD 課程的範例幾乎都把測試排在最後，這正是 Part 4 要補的洞；Ch 25 會把測試計畫和紅燈閘門插進 SDD 流程。
3. **把學習順序當成導入順序**：外部課綱和本課的五段是學習順序，因為後面的工具建立在前面之上。團隊導入通常從最痛的一環開始，例如先把驗收條件寫成 feature 檔。工作流要長出來，第一天全裝上去的流程很少活得久，見 Ch 6。
4. **把對照表的「完整」當成可以跳過**：「完整」只代表那個主題在 repo 裡有可動手的教材，不代表內容正確或版本最新。SDD 的 Spec Kit 章節釘在 0.11.10，而且照舊文件寫 Claude Code 的指令裝在 `.claude/commands/`；實際跑一次才發現 0.11.10 早就把指令裝成 skill（`/speckit-plan`）。所以 3-3、3-4、3-5、3-8 雖然標「完整」，本課仍在 Ch 12 用 1.2.5 重新實跑一遍，順便對照兩個版本的差異。
5. **以為檢查腳本可以取代人審**：腳本只回答「有沒有接上」，不回答「接得對不對」。一份測試計畫可以通過所有結構檢查，裡面卻有兩個案例在測同一件事；一個情境可以對應到任務，任務描述卻寫錯了規則。腳本把人從找漏接的苦工裡解放出來，讓人把注意力放在語意上。

## 進階：這份對照表是怎麼做出來的

方法如下：

1. 把外部課綱的 55 個單元抄成清單，每個單元一個 ID。
2. 開五個 agent 平行工作，每個負責一章的單元，各自搜尋整個 repo（不只 `ai/`，也包含 `web/`、`dev_tools/`、`soft_skills/`），讀命中的段落，替每個單元填一筆紀錄。
3. 紀錄的格式固定，這是讓五個 agent 的結果能合併的關鍵：

```json
{
  "id": "4-4",
  "coverage": "none",
  "matches": [
    {"file": "ai/pstack/08-bug-fix-playbook.md", "section": "tdd 技能的護欄", "depth": "mention"}
  ],
  "covered": "repo 裡已經有的內容",
  "missing": "還缺的內容"
}
```

4. 每章另外產出一段「缺口摘要」，按價值排序。
5. 人工抽查。抽查時發現 SDD Ch 10 的 pytest-bdd 範例跑不起來，因而有了上一節的修正。

這個做法有三個限制，用這張表時要記得：

- **我們只看得到課綱標題**。「完整」的意思是 repo 涵蓋了我們從標題推斷出的主題，不代表涵蓋了那門課實際教的內容。
- **agent 的判斷有變異**。「部分」和「完整」的邊界案例是判斷，不是測量。我們抽查過幾個，沒有逐一重做。
- **這是 2026-11-02 的快照**。repo 會長，表不會自己更新。

如果你要拿自己團隊的培訓課綱做同樣的盤點，照抄上面的紀錄格式就好。重點是讓每個 agent 回傳同一種結構，合併和抽查才做得下去。這是 [harness Ch 32](../harness_engineering/32-structured-output.md) 結構化輸出的典型用途。

## 動手練習

1. **讀三個單元**：從對照表挑三個單元，一個「完整」、一個「部分」、一個「缺」。打開表中列的既有章節各讀 10 分鐘，用三句話寫下「它教了什麼、缺了什麼」。再打開本課對應章節的「目標」那一行，看它補的是不是你寫下的缺口。
2. **先跑一次雙 driver 雛形**：照 [Ch 0](./00-environment-setup.md) 建好環境後，跑一次 spike，再故意拿掉 R1 的重疊檢查，看兩個 driver 是不是在同一個情境變紅。我們在 2026-11-02 實際跑的結果如下（`spike-ref` 是 Ch 0 用的那份雛形）：

```bash
cd /tmp/lab/aibdd/spike-ref && source ../.venv/bin/activate
PYTHONPATH=.:tests pytest -q tests --driver api   # 2 passed in 0.11s
PYTHONPATH=.:tests pytest -q tests --driver ui    # 2 passed in 0.83s
```

把 `app.py` 裡重疊判斷的 `if` 條件換成 `if False:` 之後：

```
--driver api
FAILED tests/test_booking.py::test_booking_an_overlapping_slot - AssertionErr...
1 failed, 1 passed in 0.11s
--driver ui
FAILED tests/test_booking.py::test_booking_an_overlapping_slot - AssertionErr...
1 failed, 1 passed in 0.94s
```

同一個情境、同一個原因，從兩個介面各抓到一次。Ch 30 會拆開這個雛形的每一層。

3. **畫你團隊的產物鏈**：照「底層機制」那張圖，畫出你團隊現在的版本：哪幾環有檔案，哪幾環只在人腦或聊天紀錄裡，哪幾環有腳本檢查。只在人腦裡的那幾環，就是你最該從本課挑來讀的章節。

## 本章重點整理

- 本課照外部課綱的五段順序重排 repo 的既有教材：agent 迴圈 → 模組化 skill → SDD → TDD 與 E2E → BDD。每一段補上前一段留下的洞，後一段也會用到前一段的工具。
- 55 個單元裡 9 個完整、41 個部分、5 個缺；缺口集中在 TDD、E2E 與 BDD 的動手部分。本課的新章節補這些洞，並盡量連回既有章節。
- 「可執行規格」是一條鏈：ID 把產物串起來，腳本檢查每一環有沒有接上，會跑的測試在鏈的末端。沒有 ID 和檢查的產物會漂移。
- 全課共用會議室預約這個領域：R1–R4 四條規則、一組 API、一頁 UI。
- 盤點時順手修正了 SDD 課程的幾處錯誤，最嚴重的是 Ch 10 跑不起來的 pytest-bdd 範例。

## 自我檢核

- [ ] 不看本章，能說出五段的順序，以及每一段留給下一段的那個洞嗎？
- [ ] 如果有人說「我們已經在用 Spec Kit 了，不需要 BDD」，你會用哪兩句話回應？
- [ ] 能不能畫出產物鏈，並說出至少四個 ID 各自串起哪兩份產物？
- [ ] 想一個你現在的專案：哪一環最常漂移？要用哪種檢查才抓得到？
- [ ] 能說出這份對照表的三個限制，以及它們各自會讓你在什麼情況下誤判嗎？

## 延伸閱讀

- **[水球軟體學院 — AI x BDD：規格驅動全自動開發術](https://waterballsa.tw/ai-bdd/)**
  - **讀哪裡**：課程頁的章節列表。
  - **學到什麼**：五章 55 個單元的公開課綱，也就是本課的骨架。
  - **和本章的關聯**：對照表左欄的出處。我們讀不到它的教材，只用了它的順序與單元主題；想知道原課怎麼教，要去讀原課。
- **[Birgitta Böckeler — Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**（martinfowler.com，2025-10-15）
  - **這篇說什麼**：把 SDD 分成 spec-first、spec-anchored、spec-as-source 三個層次，並實際用過 Kiro 和 spec-kit 後提出批評：流程不分問題大小、markdown 多到難審、agent 仍會無視規格。
  - **讀哪裡**：三個層次的定義，以及她用 spec-kit 做中型功能的那段經驗。
  - **為什麼值得讀**：Part 3 的客製化和 Ch 33 的 Truth vs Plan，都是在回應她點出的這幾個問題。讀之前最好已讀過 [SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md)。
- **[Dan North — Introducing BDD](https://dannorth.net/blog/introducing-bdd/)**（原刊於 *Better Software*，2006 年 3 月）
  - **這篇說什麼**：BDD 的起點。把「test」換成「behaviour」、用句子替測試命名、問「系統還缺哪一個最重要的行為」、Given/When/Then 模板，以及它和通用語言（ubiquitous language）的關係。
  - **讀哪裡**：全文，篇幅不長。
  - **和本章的關聯**：Part 5 所有的東西都從這篇長出來；Ch 26 會從它的論點出發比較 TDD 與 BDD 的產物。
- **[Gojko Adzic — Specification by Example](https://gojko.net/books/specification-by-example/)**（Manning，2011）
  - **這本書的定位**：訪談幾十個團隊後歸納出的「用例子寫規格」做法，是 BDD 協作那一半最完整的參考。
  - **讀哪幾章**：官網可下載的範例章「Key process patterns」，先建立整體圖像。
  - **和本章的關聯**：Ch 29 的 Example Mapping 與 Three Amigos、Ch 33 的活文件，都對應到這本書的流程模式。
- **《Growing Object-Oriented Software, Guided by Tests》** — Steve Freeman、Nat Pryce（Addison-Wesley，2010）
  - **這本書的定位**：外圈驗收測試、內圈單元測試的「雙迴圈」與 walking skeleton 的出處。
  - **讀哪幾章**：Part I 講 TDD 的動機與雙迴圈，Part III 是一個從零長出來的完整範例。
  - **和本章的關聯**：Ch 22 的 outside-in 雙迴圈、Ch 30 的 driver 分層都建立在它之上。書雖然以 Java 寫成，方法本身和語言無關。

地圖看完了，接著從最底層開始：先看清楚「vibe coding」和「AI 輔助工程」差在哪裡，再談怎麼控制 agent 的迴圈。

→ [Ch 2 Vibe coding 與 AI 輔助工程：同一個功能看兩次](./02-vibe-coding-vs-engineering.md)
