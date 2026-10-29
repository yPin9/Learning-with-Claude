# pstack 學習筆記：拆解 poteto-mode，打造你自己的 agent 工作流

> 給天天用 Claude Code、讀過（或至少知道）agent harness 怎麼運作，想搞懂「一套嚴謹的 agent 工作流是怎麼被設計出來的」，最後能替自己的 repo 寫出一套的工程師。

這一系列拆解 Cursor 官方 plugins repo 裡的 **pstack**——React Compiler 維護者 Lauren Tan（網路代號 poteto）把自己的工程習慣寫成的一組 Agent Skills。它的核心是一個叫 `/poteto-mode` 的分派技能：讀你的任務、挑一份流程手冊（playbook）、把步驟抄進待辦清單、需要時呼叫其他技能、把寫程式的工作交給子代理（subagent），最後拿出證據才說「做完了」。

我們全程在 **Claude Code** 裡操作（官方 plugin 本身就支援 Claude Code，不需要第三方移植版），但重點不在「學會用一個 plugin」，而是**把它的每一個設計決策拆開來看**：為什麼是一個路由技能加 23 份 playbook？為什麼 24 條原則要拆成 24 個檔案？為什麼「只能引用這個 session 讀過的原則」？為什麼評審永遠要找不同模型家族？最後你會替這個 repo 自己寫一個 `course-mode`。

## 為什麼學這個？

- **它是目前最完整、最公開的「agent 工作流工程」範本之一**：上游 repo 有 14k 顆星、從 2026-05 到 2026-10 累積了 169 個 commit，每一條規則背後都有一次真實的失敗。讀它等於讀一份「agent 會在哪裡出錯」的田野筆記。
- **它把資深工程師腦中的判斷變成可執行的文字**：「先重現再修」「先命名資料形狀」「別相信一個沒解釋過的數字」——這些你可能早就知道，但 pstack 示範了怎麼把它們寫成 agent 會照做、而且做完會被檢查的形式。
- **你會需要自己的版本**：poteto-mode 是一個人的風格。這門課的終點不是「照抄」，而是讓你能判斷哪些設計該借、哪些該丟，替自己的 repo 寫出一套。

## 先修知識

- **Claude Code 日常使用**（程度：會用 slash command、知道 subagent 與 skill 是什麼）
- **git 基本功**（程度：branch、rebase、worktree 至少聽過）
- **一門能跑測試的語言**（本課範例用 Node.js 內建的 `node:test`，Python 讀者也看得懂）
- 強烈建議但非必要：[ai/harness_engineering](../harness_engineering/README.md) 的 Ch 26（subagent）、Ch 28（planning / todo）、Ch 29（skills）、Ch 30（hooks）——本課大量引用這幾章的概念

## 課程地圖

### Part 0 — 入門：pstack 是什麼、怎麼裝（Ch 0–4）
- [Ch 0 環境搭建：在 Claude Code 裝 pstack](./00-environment-setup.md)
- [Ch 1 pstack 是什麼：go deep first](./01-what-is-pstack.md)
- [Ch 2 解剖 poteto-mode 的 SKILL.md](./02-anatomy-of-poteto-mode.md)
- [Ch 3 一次 poteto-mode 執行的生命週期](./03-run-lifecycle.md)
- [Ch 4 跨 harness 轉譯：官方 adapter vs 社群移植版](./04-cursor-to-claude-code.md)

### Part 1 — 路由與 playbook（Ch 5–10）
- [Ch 5 路由器設計：一個 mode 加 23 份 playbook](./05-router-design.md)
- [Ch 6 怎麼下 prompt：目標、完成條件、證據](./06-prompting-poteto-mode.md)
- [Ch 7 Investigation：/how、/why、/teach、/recall](./07-investigation-how-why.md)
- [Ch 8 Bug fix：先重現、二分搜尋、紅先綠後](./08-bug-fix-playbook.md)
- [Ch 9 Feature 與 Refactoring：先命名資料形狀、先釘住行為](./09-feature-refactoring.md)
- [Ch 10 Perf、Hillclimb 與 forensics](./10-perf-hillclimb-forensics.md)
- [練習 A：照 Bug fix playbook 手動修一個真 bug](./practice-a-bug-fix-by-playbook.md)

### Part 2 — 原則系統（Ch 11–15）
- [Ch 11 原則是可引用的詞彙](./11-principles-as-vocabulary.md)
- [Ch 12 Core 原則十條](./12-core-principles.md)
- [Ch 13 Architecture 原則六條](./13-architecture-principles.md)
- [Ch 14 Verification 原則五條](./14-verification-principles.md)
- [Ch 15 Delegation、Meta 與 Autonomy](./15-delegation-meta-autonomy.md)
- [練習 B：用原則審一個 PR](./practice-b-principle-review.md)

### Part 3 — 委派與多模型（Ch 16–20）
- [Ch 16 Subagent 紀律](./16-subagent-discipline.md)
- [Ch 17 模型角色與 setup-pstack](./17-model-roles-setup.md)
- [Ch 18 architect 與 arena：設計兩次，挑底再嫁接](./18-architect-arena.md)
- [Ch 19 swarm：切片、競速與聚合](./19-swarm.md)
- [Ch 20 interrogate 與 blast-radius：只留跑得出來的發現](./20-interrogate-blast-radius.md)
- [練習 C：在 Claude Code 手刻一場 mini arena](./practice-c-mini-arena.md)

### Part 4 — 驗證、清理與交付（Ch 21–24）
- [Ch 21 驗證 skill：讓 agent 自己檢查自己](./21-verification-skills.md)
- [Ch 22 benchmark-checklist：解釋那個數字](./22-benchmark-checklist.md)
- [Ch 23 清理文字與註解：unslop、no-comments、technical-writing](./23-unslop-no-comments.md)
- [Ch 24 開 PR、Babysit 與 Shipping](./24-pr-babysit-shipping.md)

### Part 5 — 長時間自主執行（Ch 25–27）
- [Ch 25 Autonomous run 與過夜契約](./25-autonomous-run.md)
- [Ch 26 figure-it-out 與決策日誌](./26-figure-it-out-decision-log.md)
- [Ch 27 多階段計畫、Autopilot 與 Orchestrate](./27-multi-phase-autopilot.md)
- [練習 D：寫一份過得了 check-plan 的計畫與決策日誌](./practice-d-plan-and-log.md)

### Part 6 — 變成你自己的（Ch 28–31）
- [Ch 28 skill 寫作學：只留會改變決策的文字](./28-skill-authoring.md)
- [Ch 29 Eval：盲測一個 skill 變更](./29-eval-playbook.md)
- [Ch 30 自我改進迴路：reflect、correct、automate-me](./30-self-improvement-loop.md)
- [Ch 31 批判與取捨：什麼時候不該用 pstack](./31-critique-tradeoffs.md)
- [Final Project：替這個 repo 打造 course-mode](./final-project-course-mode.md)

## 學習方式建議

1. **原始檔案放旁邊讀**：每章都會引用 pstack 的原文。裝好 plugin 後，原文就在 `~/.claude/plugins/` 底下（Ch 0 教你找），讀教材時開著對照，比讀摘要準。
2. **故意違反一條規則**：叫 agent 跳過重現直接修、叫它不給證據就收工，看它怎麼說、看結果差在哪。失敗的對照組比成功的範例更能讓你記住規則為什麼存在。
3. **注意哪些輸出是真的跑出來的**：本課所有程式碼與指令輸出都在本機實際跑過；agent 對話範例因為每次都不同，一律標成「示意」。這個區分本身就是 pstack 的核心紀律之一。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[pstack 原始碼（cursor/plugins/pstack）](https://github.com/cursor/plugins/tree/main/pstack)**
  - 一切以這裡為準。`skills/poteto-mode/SKILL.md` 是全課的主角；`skills/poteto-mode/playbooks/` 是 23 份流程手冊；`skills/principle-*` 是 24 條原則
- **[pstack guide（docs/guide）](https://github.com/cursor/plugins/tree/main/pstack/docs/guide)**
  - 官方的 10 頁使用指南，從安裝一路到過夜執行。本課 Part 1、4、5 大量對應它，但會補上它沒講的「為什麼這樣設計」
- **[Claude Code adapter（docs/harness/claude-code.md）](https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md)**
  - 官方的 Cursor → Claude Code 名稱對照表；Ch 4 整章拆解它

### 推薦文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)**
  - pstack 的 router、orchestrator-workers、evaluator-optimizer 幾種結構，都能在這篇的 workflow 分類裡找到原型
- **[Flavio Copes — What is poteto-mode in pstack?](https://flaviocopes.com/poteto-mode/)** 與 **[A deep dive into pstack](https://flaviocopes.com/pstack/)**
  - 第三方的英文導讀，篇幅短、適合先建立全貌；本課的深度在它之上
- **[Anthropic — Claude Code 官方文件：Skills、Subagents、Hooks、Plugins](https://code.claude.com/docs/en/overview)**
  - pstack 在 Claude Code 上依賴的四個機制；遇到「這個欄位 Claude Code 到底認不認」時以這裡為準

### 讀完本課之後

- **[michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude)**：另一條路線的移植版（建置期改寫 + hooks），讀它的 `CHANGES.md` 能看到一個移植版怎麼長期追上游
- **[ai/spec_driven_development](../spec_driven_development/README.md)**：pstack 的作者說「我不相信規劃，最好的 spec 是程式碼」；這門課剛好站在另一邊，對照著讀會很有收穫

## 給 AI 協作者的備註

本課以「先講直覺 → 讀 pstack 原文 → 拆設計決策 → 可跑範例 → 踩雷 → 延伸閱讀」為固定節奏，繁體中文、口吻直接有觀點。版本釘定：pstack 0.15.36（上游 commit `c742dd6`）、Claude Code 2.1.295、macOS。新增或修訂章節時，請對照根目錄 `.claude/skills/learn/SKILL.md` 的深度 checklist；agent 對話一律標「示意」，不可冒充真實執行紀錄。
