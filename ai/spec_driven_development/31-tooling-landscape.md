# Ch 31 — 其餘版圖：Tessl / BMAD / Cursor / Claude Code / Codex / Devin

> **目標**：理解「規格驅動開發（Spec-Driven Development）」工具版圖的五個家族——spec-as-source 平台（Tessl）、方法論框架（BMAD-METHOD）、plan-first IDE/CLI（Cursor、Claude Code、Cline、Roo Code）、跨工具 context 檔標準（AGENTS.md / CLAUDE.md）、以及自主 agent（Codex、Devin）——並且知道它們各自在規格生命週期的哪一段發力。
>
> **環境**：以下工具資訊標注「查證日期 2026-06-30」，版本、功能、定價隨時可能更動，使用前請查閱官方文件。

## 版圖長什麼樣

在 Ch 27–30 我們深入看了 GitHub Spec Kit 與 AWS Kiro。但整個市場比這兩個工具大得多。一個粗略的地圖：

```
意圖 (Intent / Spec)
──────────────────────────────────────────────────────────────
         [1] spec-as-source 平台
            Tessl (Spec Registry + Framework)
                ↓ 元件規格 / 使用規格 / 測試
         [2] 方法論框架
            BMAD-METHOD (PRD + 架構文件 + 故事)
                ↓ 多 persona 協作，文件先於代碼
         [3] Plan-first IDE/CLI
            Cursor / Claude Code / Cline / Roo Code
                ↓ 規劃模式生成可審查 markdown，再切換到執行
         [4] Context 檔 (規格的「帶入方式」)
            CLAUDE.md / AGENTS.md / CONVENTIONS.md
                ↓ 載入 agent session 的持久指令
         [5] 自主 agent
            OpenAI Codex / Devin
                ↓ 讀 AGENTS.md 後自主規劃、實作、驗證
──────────────────────────────────────────────────────────────
代碼 (Code / Implementation)
```

越往上，越強調「寫規格 → AI 生成實作」；越往下，越強調「給 agent 任務 → agent 自主閉環」。現實中多數團隊同時用到多個層次。

> 如果你對「為什麼要把規格和實作分開」還不清楚，先回看 [Ch 7 規格 vs 設計 vs 實作](./07-spec-design-implementation.md) 與 [Ch 24 Sean Grove《The New Code》](./24-the-new-code.md)。

---

## 1. Tessl：spec-as-source 最激進的賭注

### 歷史脈絡

在 Tessl 出現之前，主流的 AI 輔助開發仍是「給 copilot 一段上下文，它補完下一行」。問題是：agent 對函式庫版本的記憶是訓練時凍結的，在真實專案裡它會混用不同版本的 API、甚至捏造不存在的方法。Spec Kit 和 Kiro 解決了「如何產生和追蹤功能規格」，但沒有解決「如何確保 agent 知道 library 的正確用法」。

Tessl 的切入點是：把函式庫的「正確用法」也變成一份版本化的規格。

### Tessl 是什麼

Guy Podjarny（Snyk 創辦人、前 Akamai CTO）於 2024 年創立 Tessl，願景是把軟體開發從「以代碼為中心（code-centric）」轉移到「以規格為中心（spec-centric）」。公司已完成兩輪融資（種子輪 $25M 由 boldstart 和 GV 領投，Series A $100M 由 Index 和 Accel 領投，共 $125M，2024 年宣布）。（查證日期 2026-06-30）

2025-09-23，Tessl 發布兩個產品（來源：tessl.io 官方部落格，查證日期 2026-06-30）：

**Spec Registry（開放 beta）**

類似 npm 但存的不是代碼，而是規格。概念：

```
npm install lodash      → 你的 node_modules 裡有 lodash 代碼
tessl add lodash@4.17   → 你的 Spec Registry 裡有 lodash 4.17 的「Usage Spec」
```

Usage Spec 描述：這個版本的 API 怎麼用、不能用什麼、有什麼已知 gotcha。Agent 在生成代碼前先查 Spec Registry，不再憑記憶猜測。截至發布時，Registry 收錄 10,000+ 個 OSS 函式庫的 Usage Spec。

**Tessl Framework（封閉 beta）**（版本相關，查證日期 2026-06-30）

框架讓你在動手寫代碼前，先寫兩種規格：

- **Component Spec**：這個元件有哪些能力（capabilities）、對應哪些測試、有哪些 API。
- **Usage Spec**：如何使用這個元件（版本相關指引 + 組織內部規則）。

Framework 的核心主張：測試和防護（guardrail）從規格推導而來，而非事後補寫。

> 注意：Framework 在本文查證時（2026-06-30）仍在封閉 beta，確切的規格語法、GA 時程、定價均未在官方文件中公開。以 tessl.io 官方公告為準。

### Tessl 的取捨

| 優點 | 限制 |
|---|---|
| 解決 agent 的版本幻覺問題 | Framework 仍封閉 beta，難以評估實際工作流 |
| Usage Spec 可版本控制，符合 spec-as-source 理念 | 需要整個生態接受「先寫 spec 再寫代碼」 |
| 10,000+ OSS specs 降低入門障礙 | 組織內部 spec 由誰維護、如何保持更新是難題 |

---

## 2. BMAD-METHOD：方法論框架把文件變成真相來源

### 歷史脈絡

「用 AI 寫代碼」最早的形式是：對著 ChatGPT 貼代碼、說需求、要求補全。問題是每次對話都是孤立的——架構決策、產品需求、設計討論分散在各地，agent 每次都是從零開始。BMAD-METHOD 的答案是：把那些決策變成結構化文件，讓文件成為真相來源，代碼是文件的下游產物。

### BMAD-METHOD 是什麼

「Breakthrough Method for Agile AI-Driven Development」，開源框架，由 bmad-code-org 維護。版本 V6.9.0（2026-06-22 發布，來源：GitHub repo README，查證日期 2026-06-30）。

核心設計：**12+ 個 persona agent**（PM、架構師、Developer、UX 等），每個 persona 有對應的角色定義和技能。**34+ 個工作流**，從腦力激盪到部署。

產出物（文件即真相）：
- PRD（Product Requirements Document，產品需求文件）
- PRFAQ（亞馬遜 Working Backwards 格式）
- 架構規格（architecture spec）
- UX 規格
- 用戶故事（User Story）

**Web Bundle** 設計值得一提：你可以在純網頁 chat（如 Claude.ai 或 ChatGPT）裡跑規劃階段，生成上述文件；再把文件帶進 IDE（Cursor、Claude Code 等）跑實作。規劃和實作不需要在同一個工具裡。

**Party Mode** 是多 persona 同時上線、互相質詢、收斂決策的功能。（查證日期 2026-06-30）

安裝方式：

```bash
# 最新版請以官方 README 為準（查證日期 2026-06-30）
npx bmad-method@latest
```

> 注意：agent 數量（12+）、workflow 數量（34+）來自 README 在查證日期的快照，BMAD 迭代快，數字會隨版本變動。

### BMAD 的取捨

| 優點 | 限制 |
|---|---|
| 把跨對話的決策結構化為文件 | 34+ workflow 學習曲線陡 |
| Web Bundle 讓規劃不綁特定 IDE | 文件品質取決於使用者輸入品質 |
| 開源，可自訂 persona 和 workflow | 沒有 Spec Registry 那種版本化的函式庫知識 |

---

## 3. Plan-first IDE/CLI：四個工具的共同模式

Cursor、Claude Code、Cline、Roo Code 來自不同公司，但有一個共同的架構決策：**把規劃和執行分開**。規劃時 agent 是唯讀的，不能動代碼；你審查計劃後，再讓它切換到寫代碼模式。

這個設計的直覺是對的：改代碼比改計劃貴得多。讓 agent 在你看過計劃之前就開始改文件，就像讓承包商在你看過估價單之前就開始拆牆。

> 如果你對「何時規格、何時 vibe coding」還不熟，先看 [Ch 42 什麼時候不要用 SDD](./42-when-not-to-use-sdd.md)。

### 3a. Cursor：.cursor/rules 與 Plan Mode

Cursor 是 AI-first IDE（基於 VS Code fork，由 Anysphere 開發）。（查證日期 2026-06-30）

**持久規格：.cursor/rules**

`.cursor/rules/` 目錄下放 `.mdc` 檔，每個檔有 YAML frontmatter 控制啟動時機：

```yaml
---
alwaysApply: true          # 每次 session 都載入
---
# 這個 rule 的內容
```

四種啟動模式：

| 模式 | frontmatter 設定 | 觸發條件 |
|---|---|---|
| Always Apply | `alwaysApply: true` | 每次 session |
| Auto Attached | `globs: ["src/**/*.ts"]` | 符合 glob 的檔案開啟時 |
| Agent Requested | `description: "..."` | agent 自行判斷是否需要 |
| Manual | 無特殊設定 | 使用者明確 `@rule-name` 引用 |

`.cursor/rules` 是版本控制的，可以 commit 到 repo——這讓它扮演了「持久規格」的角色。Cursor 也讀 AGENTS.md 和 CLAUDE.md。

**Plan Mode**

Shift+Tab 切換。Plan Mode 裡 agent 會：
1. 問釐清問題
2. 搜尋 codebase
3. 產生一份可編輯的 markdown 計劃

計劃預設存在 home 目錄，「Save to workspace」選項可儲存到專案內（確切路徑依版本而定，查證日期 2026-06-30）。你確認計劃後再切換到執行模式。

### 3b. Claude Code：CLAUDE.md + 唯讀規劃模式

Claude Code 是 Anthropic 的終端/IDE coding agent。（查證日期 2026-06-30，來源：Anthropic 官方部落格，2026-06-18）

**CLAUDE.md**

Session 啟動時自動載入。作用：持久的專案慣例、build 指令、不能改的架構決策。官方建議保持 200 行以內——超過這個長度 agent 遵從度下降。

範例 CLAUDE.md 片段：

```markdown
# Project Conventions

## Build
- `npm run build` → TypeScript compile
- `npm test` → Jest (需要 Docker Compose up)

## Architecture decisions
- 所有 external API calls 走 src/clients/，不允許在 handler 裡直接 fetch
- Error handling 統一用 Result<T, E>，不 throw

## Domain language
- "Order" 指已付款訂單；"Cart" 指未付款。勿混用。
```

**唯讀規劃模式（Plan Mode）**

Shift+Tab 切換。在此模式 agent 只能執行：`Read`、`Glob`、`Grep`、`WebSearch`、`Task`，不能寫入任何檔案。通常會自動派生一個「Explore subagent」去瀏覽 codebase，再回傳分析結果讓你確認。

**其他機制**

| 機制 | 路徑 | 作用 |
|---|---|---|
| Skills | `.claude/skills/SKILL.md` | 可重用的 mini-prompt；名稱 + 描述先載入，invoke 時才讀全文 |
| Subagents | `.claude/agents/` | 獨立 context 的子 agent，用於隔離執行 |
| Slash commands | `.claude/commands/` | 自訂指令（類似 Spec Kit 的 /speckit.*）|
| Rules | `.claude/rules/` | 可路徑範圍限定的規則 |
| Hooks | `settings.json` | 在 agent 動作前後插入 shell 指令 |

### 3c. Cline：Plan 模式與 Act 模式明確分離

Cline 是開源的 VS Code coding agent。核心設計在文件裡說得很直白：**「planning first is highly recommended」**（查證日期 2026-06-30）。

| | Plan Mode | Act Mode |
|---|---|---|
| 讀代碼 | ✓ | ✓ |
| 執行搜尋 | ✓ | ✓ |
| 修改檔案 | ✗ | ✓ |
| 執行命令 | ✗ | ✓ |
| Token 消耗 | 少 | 多 |

切換到 Act Mode 時，Plan Mode 的對話歷史和分析結果都帶過去。這意味著 agent 不需要重新理解 codebase，只是從「思考」切到「行動」。

### 3d. Roo Code：Architect 模式與 Boomerang 任務分解

Roo Code 是另一個開源 VS Code agent，有兩個 spec-first 相關的特殊模式（查證日期 2026-06-30）：

**Architect 模式（🏗️）**

Architect 模式自 v3.3 起可以讀所有檔案，但只能編輯 `.md` 檔。這個限制是刻意設計的：強迫 agent 在這個模式裡只能輸出計劃和設計文件，不能動代碼。規則放在 `.roo/rules-architect/`。

**Orchestrator/Boomerang 模式（🪃）**

複雜任務進來後，Orchestrator 把它分解成多個子任務，分派給 Code / Debug / Architect 等不同模式的 agent 執行。每個子任務在獨立 context 裡跑，完成後只把摘要回傳給 Orchestrator。好處是：複雜任務不會因為一個 agent 的 context window 撐不住而失敗；不同類型的工作用對應的 persona。

---

## 4. Context 檔：讓規格活進 agent session

「規格」要發揮作用，得讓 agent 在正確的時間讀到它。這個問題催生了一個新的檔案格式生態。

### AGENTS.md：跨工具開放標準

（查證日期 2026-06-30，來源：agents.md 官方網站）

AGENTS.md 是「引導 coding agent 的開放格式（a simple, open format for guiding coding agents）」。最初由 OpenAI Codex 帶起，現由 Linux Foundation 下的 Agentic AI Foundation 管理。

截至查證日期，已被 60,000+ 個開源專案採用，支援工具 20+ 個，包括：OpenAI Codex、Google Jules、Factory、Aider、goose、opencode、Zed、Warp、VS Code、Devin、JetBrains Junie、Amp、Cursor、Roo Code、Gemini CLI、GitHub Copilot coding agent、Windsurf、Augment Code 等。

AGENTS.md 的優勢是**可攜性**：同一份 spec 檔，換工具不需要重寫。

### CLAUDE.md 與 AGENTS.md 的關係

Claude Code 讀 CLAUDE.md；它也讀 AGENTS.md（透過 Cursor 等 IDE 的互通規範）。兩者格式相近，都是 Markdown，差別在 agent 的載入優先順序。如果你只想維護一份，AGENTS.md 的跨工具覆蓋面更廣；如果你需要 Claude Code 特定的 hooks 或 skills 設定，CLAUDE.md 是主場。

### Aider 的 CONVENTIONS.md

Aider（終端 AI pair programmer，Paul Gauthier 開發）用 CONVENTIONS.md 做類似的事。載入方式：

```bash
# CLI 啟動時帶入
aider --read CONVENTIONS.md

# 或在 .aider.conf.yml
read: CONVENTIONS.md
```

標記為唯讀的好處：啟用 prompt caching，CONVENTIONS.md 的 token 不重複計費。（查證日期 2026-06-30）

---

## 5. 自主 Agent：Codex 與 Devin

Plan-first 工具讓人類審查計劃後才執行。自主 agent 則是「給一個任務，agent 自己規劃、自己執行、自己驗證」。規格這時候的作用不是「人審查 agent 的計劃」，而是「用規格約束 agent 的行為空間」。

### OpenAI Codex：「one agent for everywhere you code」

（查證日期 2026-06-30，來源：OpenAI 開發者文件）

Codex 定位是「到處都能跑的 coding agent」——App、IDE extension、CLI、Web cloud 四種入口。核心 spec 機制是 **AGENTS.md 的指令鏈（instruction chain）**：

啟動時的讀取順序：

```
~/.codex/AGENTS.override.md      ← 最高優先（全域覆寫）
~/.codex/AGENTS.md               ← 全域設定
repo root/AGENTS.md              ← 專案根目錄
  ↓ （往 cwd 走，每個目錄最多一個 AGENTS.md）
cwd/AGENTS.md                    ← 離目前工作目錄最近，最後覆蓋
```

越接近 cwd 的檔案優先級越高。合計有 32 KiB（`project_doc_max_bytes`）的上限。

Cloud 模式下，Codex 在自己的隔離環境裡平行跑多個任務，回傳 diff 讓你審查。ChatGPT Plus / Pro / Business / Edu / Enterprise 用戶均包含（版本相關，查證日期 2026-06-30）。

在 Spec Kit 的 skills 模式（見 Ch 29），Spec Kit 指令可以用 `$speckit-<command>` 語法讓 Codex 呼叫，這是少數 Spec Kit 與 Codex 明確整合的地方。

> 回顧 [Ch 29 GitHub Spec Kit（三）：底層怎麼運作](./29-spec-kit-internals.md) 的 skills mode 章節。

### Devin：自主 AI 軟體工程師

（查證日期 2026-06-30，來源：Cognition 官方文件）

Devin 是 Cognition 的「autonomous AI software engineer」。官方一句話定位：「Devin is an autonomous AI software engineer that can write, run and test code.」

執行環境整合了 Shell、IDE、Browser，使用者可以在 embedded IDE 裡跟隨或接管。Devin 沒有公開的正式 spec 檔格式——Cognition 的文件建議的是寫有明確完成條件的 prompt（例如「CI 通過即完成」），而非讓你寫一個 spec 檔案給它。Devin 支援 AGENTS.md 標準。

> 注意：Devin 的 SWE-bench 分數、內部規劃機制，Cognition 官方文件均未詳細揭露。任何引用第三方評測數字的說法，請以 Cognition 官方 benchmark 頁為準。

---

## 對比取捨

| 工具/框架 | 規格形式 | 人工審查點 | 自主程度 | 適合規模 |
|---|---|---|---|---|
| Tessl | Component Spec / Usage Spec | 寫規格時 | 低（還需 IDE agent 執行） | 大型產品 + 嚴格 API 版本管控 |
| BMAD-METHOD | PRD / 架構文件 / 故事 | 每個文件產出後 | 低（框架協調，人批准） | 複雜產品規劃 |
| Cursor Plan Mode | 可編輯 markdown 計劃 | 計劃生成後 | 中 | 功能開發 |
| Claude Code Plan Mode | 唯讀規劃 + CLAUDE.md | 計劃生成後 | 中 | 功能開發 + 有客製規格 |
| Cline Plan/Act | 明確模式切換 | 切換 Act 前 | 中 | 功能開發 |
| Roo Code Boomerang | .md 計劃 + 子任務分解 | Orchestrator 分發前 | 中高 | 複雜多步驟任務 |
| OpenAI Codex | AGENTS.md 指令鏈 | Diff 審查 | 高 | 後台平行任務 |
| Devin | 清晰 prompt + 完成條件 | 任務結束後 | 最高 | 獨立、定義清晰的任務 |

---

## 踩雷集錦

**錯誤直覺 1：CLAUDE.md 越長越好，把所有規範都塞進去**

正確認識：超過 200 行後 agent 對規範的遵從率下降（Addy Osmani 的術語「curse of instructions」：「As you pile on more instructions... the model's performance in adhering to each one drops significantly」，2026-01-13）。CLAUDE.md 應該放「如果 agent 忽視會造成明顯錯誤的」規範，細節留給 `.claude/rules/` 的路徑範圍規則。

**錯誤直覺 2：AGENTS.md 和 CLAUDE.md 只能選一個**

正確認識：Claude Code 同時讀兩者。AGENTS.md 負責跨工具的基礎指引；CLAUDE.md 負責 Claude Code 特定的擴充（skills、subagent、hooks）。兩者互補，不衝突。

**錯誤直覺 3：自主 agent（Devin/Codex）不需要寫規格，直接下任務就好**

正確認識：任務描述含糊時，agent 會自行填補，填補的方向不一定對。Cognition 自己的文件說要寫「explicit completion criteria（明確的完成條件）」和「easy-to-verify tasks（容易驗證的任務）」——這本質上就是規格的核心要求。自主程度高的工具更需要把意圖說清楚，不是更少。

**錯誤直覺 4：Roo Code 的 Boomerang 模式可以把複雜任務「徹底分包」**

正確認識：Boomerang 把大任務分解給子 agent，每個子任務跑完只回傳摘要給 Orchestrator。問題是：如果 Orchestrator 對子任務的上下文要求描述不夠精確，子任務可能合格但整體拼在一起不一致。任務分解本身也是一份需要人審查的「計劃」。

**錯誤直覺 5：「spec-driven development」是某個人發明的概念**

正確認識：這個術語在 2025 年由 GitHub Spec Kit、AWS Kiro、Tessl 等工具同時採用，是一個從社群自然結晶的雨傘術語。Sean Grove 的 《The New Code》演講（AI Engineer World's Fair，2025 年 6 月）讓它廣為人知，但沒有單一創始人。（來源：corrections.md 查證，2026-06-30）

---

## 進階延伸

**跨工具 context 管理**：當你同時用 Cursor + Claude Code + Codex 時，一份 AGENTS.md 可以作為共同基線，工具特定的 context（.cursor/rules、CLAUDE.md）做差異化覆蓋。這和 DDD 的 [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md) 有結構上的相似：每個工具是一個 Bounded Context，AGENTS.md 是 Shared Kernel。

**BMAD + Spec Kit 組合**：BMAD 擅長前期規劃（PRD → 架構 → 故事），Spec Kit 擅長把單一功能的規格轉換成可執行的任務流。可以讓 BMAD 產出「功能需求 + 架構決策」，再把每個 Story 餵進 Spec Kit 的 `/speckit.specify` → `/speckit.plan` → `/speckit.tasks` 管線。

**Tessl Spec Registry 的長遠意義**：如果「用法規格（Usage Spec）」像 npm 包一樣可以被社群維護、版本化、發布，那麼 agent 的幻覺問題（API 混版）有機會在語料層面被根治，而非靠 RAG 即時修補。這是一個架構上更乾淨的方向，值得追蹤。

---

## 動手練習

1. 在一個有 5–10 個 JS/TS 檔的小專案裡，寫一份 AGENTS.md（放 repo root）和一份 CLAUDE.md（放 project root）。AGENTS.md 寫跨工具的基本規範（語言、格式、commit 慣例）；CLAUDE.md 寫 Claude Code 特定的 build 指令和架構決策。分別用 Cursor 和 Claude Code 開同一個任務，觀察它們如何讀取這兩個檔案。

2. 用 Claude Code 的 Plan Mode（Shift+Tab）對一個中等複雜度的功能需求進行規劃，不要讓它動任何代碼。把輸出的 markdown 計劃另存，標注你覺得「agent 猜錯了」的地方。切換到執行模式之前，先修正那些地方。記錄：你修正了幾個地方？最後實作是否符合預期？

3. 閱讀 BMAD-METHOD README，選一個現有的小專案，用 web bundle 的 PM persona 跑一次「寫 PRD」流程。把產出的 PRD 和你原本的需求文件（如果有的話）對比：BMAD 的結構幫你想到了哪些你沒提的問題？

---

## 本章重點整理

- 工具版圖分五族：spec-as-source（Tessl）、方法論框架（BMAD）、plan-first IDE/CLI（Cursor/Claude Code/Cline/Roo Code）、context 檔標準（AGENTS.md/CLAUDE.md）、自主 agent（Codex/Devin）。
- Tessl 的 Spec Registry 把「函式庫用法」也版本化，解決 agent 的版本幻覺問題；Framework 目前封閉 beta。
- BMAD-METHOD 讓 PRD / 架構文件 / 用戶故事成為代碼的上游真相；Web Bundle 讓規劃不綁特定 IDE。
- Cursor、Claude Code、Cline、Roo Code 都有「唯讀規劃模式」——設計意圖一致：讓人在 agent 動代碼前審查計劃。
- AGENTS.md 是跨工具的開放標準，由 Linux Foundation 下的 Agentic AI Foundation 管理，20+ 工具支援；CLAUDE.md 是 Claude Code 的專屬 context 檔，兩者互補。
- 自主程度越高的 agent（Codex/Devin）並不意味著規格越不重要——恰恰相反：任務越自主，完成條件和意圖越需要說清楚。
- 「spec-driven development」是 2025 年自然結晶的術語，沒有單一創始人。

---

## 自我檢核

- [ ] 用自己的話解釋 Tessl Spec Registry 和 npm 的類比，以及它解決的是哪一類 agent 問題。
- [ ] 面試被問到「你怎麼讓 AI coding agent 遵守專案慣例」，你會提哪幾個機制？（至少說出三個來自不同工具的方案）
- [ ] Roo Code Architect 模式的「只能編輯 .md 檔」這個限制，是 bug 還是 feature？為什麼？
- [ ] AGENTS.md 和 CLAUDE.md 在一個同時用 Cursor 和 Claude Code 的專案裡，你會怎麼分工？
- [ ] Devin 沒有「正式規格檔格式」，但 Cognition 要你寫明確的完成條件——這和 Ch 10 的 Given-When-Then 在本質上有什麼共同點？（回看 [Ch 10 從驗收條件到 BDD](./10-acceptance-criteria-bdd.md) 後再回答）

---

## 延伸閱讀

1. **Tessl — spec-driven development 工具發布公告**（Simon Maple，Tessl，2025-09-23）
   https://tessl.io/blog/tessl-launches-spec-driven-framework-and-registry/
   閱讀重點：Component Spec 與 Usage Spec 的確切定義，Spec Registry 的設計理念，以及 spec-centric 的論述。本章最核心的第一手資料。

2. **Tessl Series A 公告：AI Native Software Development 的願景**（Guy Podjarny，Tessl，2024-11-14）
   https://tessl.io/blog/announcing-our-series-a-for-ai-native-software-development/
   閱讀重點：code-centric → spec-centric 的論述框架，Podjarny 的背景，以及 $125M 背後的市場假設。和本章 Tessl 節對照閱讀效果最好。

3. **BMAD-METHOD GitHub repository**（bmad-code-org）
   https://github.com/bmad-code-org/bmad-method
   閱讀重點：README 的 persona agent 清單和 workflow 清單，以及 docs.bmad-method.org 的使用指南。Version-dependent，以最新 README 為準。

4. **Cursor Docs — Rules**（Cursor，Anysphere）
   https://cursor.com/docs/rules
   閱讀重點：.mdc 檔格式、四種啟動模式、AGENTS.md 支援。搭配 cursor.com/docs/agent/planning 看 Plan Mode 細節。

5. **Steering Claude Code：skills, hooks, rules, subagents and more**（Anthropic，2026-06-18）
   https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more
   閱讀重點：CLAUDE.md、.claude/skills、.claude/agents、hooks 的確切路徑與行為。本章 Claude Code 節的第一手來源。

6. **Cline — Plan & Act Mode**（Cline Bot Inc.）
   https://docs.cline.bot/core-workflows/plan-and-act
   閱讀重點：Plan Mode 不能做什麼（不能修檔、不能執行命令），以及切換時 context 如何保留。

7. **Roo Code — Boomerang Tasks / Orchestrator**（Roo Code Inc.）
   https://roocodeinc.github.io/Roo-Code/features/boomerang-tasks
   閱讀重點：Orchestrator 分解子任務的機制，Architect 模式的 .md 限制。搭配 docs.roocode.com/basic-usage/using-modes。

8. **OpenAI Codex — Custom instructions with AGENTS.md**（OpenAI）
   https://developers.openai.com/codex/guides/agents-md
   閱讀重點：指令鏈（~/.codex 覆寫 → repo root → cwd）與 32 KiB 上限的精確規則。本章 Codex 節的第一手來源。

9. **AGENTS.md — open standard**（Agentic AI Foundation，Linux Foundation）
   https://agents.md/
   閱讀重點：格式規範、governance，以及 20+ 支援工具清單——這是理解「為何寫一份 spec 可以跨工具複用」的基礎。

10. **Devin — Introducing Devin**（Cognition）
    https://docs.devin.ai/get-started/devin-intro
    閱讀重點：Devin 的 Shell/IDE/Browser 整合環境，以及 Cognition 對「如何給 Devin 好任務」的指引（等價於輕量規格）。內部規劃機制未公開。

下一章我們拿這些工具橫向對比：針對不同類型的任務，哪個工具或組合最對位？

→ [Ch 32 工具橫向對比：什麼任務選什麼](./32-tool-comparison.md)
