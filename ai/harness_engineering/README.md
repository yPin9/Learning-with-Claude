# Harness Engineering 學習筆記：打造你自己的 AI Agent 執行框架

> 給用過 Claude Code / Cursor / LangChain，但想真正搞懂「agent 底層那層框架怎麼運作、怎麼設計才好」的工程師。

這一系列把「agent harness（執行框架／鷹架）」這件事拆開來教：一個 LLM 只會吐文字，是 harness 把它變成一個能讀檔、跑指令、委派子任務、記住進度的 agent。我們全程用 **Python + Anthropic SDK**，從自己手寫一個最小 loop 開始，逐步加上 context 管理、工具系統、subagent、permission、eval，最後整合成一個 production-ready 的 mini harness。

這門課**偏應用與最佳實踐**：每章都會講底層機制幫你建立直覺，但落點在「怎麼設計才好、生產上要注意什麼」，而不是逐行啃某個開源框架的原始碼。

## 為什麼學這個？

- **你天天在用、卻是黑盒**：Claude Code、Cursor、Devin 這些工具的「魔法」幾乎都在 harness 那一層，不在模型本身。看懂它，你對 agent 的能力邊界會有完全不同的判斷力。
- **這是 AI 應用工程的核心技能**：模型會一直換，但「怎麼餵 context、怎麼設計工具、怎麼控制 loop」這些工程問題長期穩定。投資報酬率很高。
- **職涯角度**：「會調 API」現在不稀奇；「能設計並維護一個可靠的 agent 系統」才是公司真正缺的人。

## 先修知識

- **Python**（程度：能讀寫函式、class、async 大致看得懂）
- **用過至少一個 agent 工具**（Claude Code / Cursor / ChatGPT 的工具呼叫都行）——你需要有「agent 在幹嘛」的體感
- **基本的 HTTP / JSON 概念**（知道 API 請求大概長什麼樣）
- 沒有也沒關係的：自己實作過 agent loop、寫過 MCP server、碰過 LangGraph——這些正是本課要教你的

## 課程地圖

### Part 0 — 入門與心智模型（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 什麼是 agent harness？](./01-what-is-agent-harness.md)
- [Ch 2 解剖一次 agent 執行](./02-anatomy-of-agent-run.md)
- [Ch 3 心智模型：loop + context + tools + policy](./03-mental-model.md)

### Part 1 — Agent Loop 核心（Ch 4–9）
- [Ch 4 最小可行 agent loop](./04-minimal-agent-loop.md)
- [Ch 5 Tool calling 協議](./05-tool-calling-protocol.md)
- [Ch 6 多輪對話與訊息歷史管理](./06-message-history.md)
- [Ch 7 停止條件與 turn 控制](./07-stop-conditions-turns.md)
- [Ch 8 串流與即時輸出](./08-streaming.md)
- [Ch 9 錯誤處理與重試](./09-error-handling-retry.md)
- [練習 A：寫一個能跑的 mini agent loop](./practice-a-mini-agent-loop.md)

### Part 2 — Context Engineering（Ch 10–17）
- [Ch 10 Context window 是稀缺資源](./10-context-window-budget.md)
- [Ch 11 System prompt 設計](./11-system-prompt-design.md)
- [Ch 12 訊息歷史的成長與成本](./12-message-history-growth.md)
- [Ch 13 Context 壓縮與摘要](./13-context-compaction.md)
- [Ch 14 Memory：短期 vs 長期](./14-memory.md)
- [Ch 15 RAG 與 context 注入](./15-rag-context-injection.md)
- [Ch 16 Tool 結果的裁剪與格式化](./16-tool-result-pruning.md)
- [Ch 17 Prompt caching](./17-prompt-caching.md)
- [練習 B：給 agent 加上 context 壓縮 + memory](./practice-b-compaction-memory.md)

### Part 3 — Tool 系統設計（Ch 18–25）
- [Ch 18 好的 tool 長什麼樣：schema 設計](./18-tool-schema-design.md)
- [Ch 19 Tool 描述就是 prompt](./19-tool-descriptions-as-prompt.md)
- [Ch 20 Tool 結果設計](./20-tool-result-design.md)
- [Ch 21 檔案系統工具](./21-filesystem-tools.md)
- [Ch 22 執行 shell 與沙箱](./22-shell-and-sandbox.md)
- [Ch 23 Tool search / deferred tools](./23-tool-search-deferred.md)
- [Ch 24 MCP 整合](./24-mcp-integration.md)
- [Ch 25 Permission 模型與人機互動](./25-permission-model.md)
- [練習 C：設計並實作一套檔案操作工具集](./practice-c-file-toolset.md)

### Part 4 — 進階能力（Ch 26–33）
- [Ch 26 Subagent：把任務委派出去](./26-subagents.md)
- [Ch 27 Multi-agent 編排](./27-multi-agent-orchestration.md)
- [Ch 28 Planning 與 todo 管理](./28-planning-todo.md)
- [Ch 29 Skills / 可重用能力包](./29-skills.md)
- [Ch 30 Hooks](./30-hooks.md)
- [Ch 31 背景任務與長時間執行](./31-background-tasks.md)
- [Ch 32 Structured output 與 schema 強制](./32-structured-output.md)
- [Ch 33 多模態輸入](./33-multimodal-input.md)
- [練習 D：用 subagent 做 multi-agent 研究工作流](./practice-d-multi-agent-research.md)

### Part 5 — 品質、可靠性與安全（Ch 34–40）
- [Ch 34 Eval：怎麼知道 agent 變好還變壞](./34-eval.md)
- [Ch 35 Observability](./35-observability.md)
- [Ch 36 Prompt injection 與 agent 安全](./36-prompt-injection-security.md)
- [Ch 37 成本與延遲優化](./37-cost-latency-optimization.md)
- [Ch 38 失敗模式與 debug agent](./38-failure-modes-debugging.md)
- [Ch 39 確定性與可重現](./39-determinism-resume.md)
- [Ch 40 框架對比：Claude Agent SDK / LangGraph / OpenAI Agents SDK / 自己寫](./40-framework-comparison.md)
- [練習 E：給 harness 加上 eval + tracing](./practice-e-eval-tracing.md)

### Part 6 — 整合專案
- [Final Project：自己刻一個 mini agent harness](./final-project-mini-harness.md)

### Part 7 — 導入與落地（Adoption）
> 前六個 Part 教你「怎麼把一個可靠的 agent harness 做出來」。這個 Part 換軸：當你已經會刻了，怎麼判斷該不該上、怎麼漸進放權、怎麼把它織進團隊既有流程而不出事。技術之外的工程決策。
- [Ch 41 該不該上 agent：任務選型與導入決策](./41-when-to-agentify.md)
- [Ch 42 漸進式落地：從輔助到自主的信任階梯](./42-gradual-rollout-trust.md)
- [Ch 43 把 agent 織進團隊工作流](./43-agents-in-team-workflow.md)
- [練習 F：為一個真實任務寫導入評估 + 落地計畫](./practice-f-adoption-assessment.md)

## 學習方式建議

1. **每章親手跑過**：所有範例打開編輯器跑一次，不要只讀。agent 的行為常常違反直覺，看輸出比看說明有用。
2. **故意把它弄壞**：把 tool schema 的某個欄位刪掉、把 system prompt 改爛、把 context 塞爆，看 agent 怎麼當掉或亂跑。失敗的輸出是最好的老師。
3. **拿真實任務餵它**：每學一個能力，就拿你工作裡一個小任務丟給你寫的 agent，看它能不能做。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)**
  - 整門課的觀念主軸；定義了 workflow vs agent、各種 agentic pattern。遇到「該不該上 agent」的設計問題時回來看這篇。
- **[Anthropic API 官方文件](https://docs.anthropic.com/en/api/messages)**
  - 權威來源；Messages API、tool use、prompt caching、streaming 的行為都以這裡為準。
- **[Model Context Protocol 規格](https://modelcontextprotocol.io/)**
  - MCP 的官方 spec；Ch 24 整章以它為基礎。

### 推薦部落格 / 文章

- **[Anthropic Engineering Blog](https://www.anthropic.com/engineering)** — Anthropic 工程團隊
  - Claude Code、context engineering、multi-agent research system 的第一手設計筆記，本課多章直接對應這裡的文章。
- **[Chip Huyen — AI Engineering 系列](https://huyenchip.com/blog/)** — Chip Huyen
  - 從系統工程角度談 LLM 應用、eval、observability，補本課 Part 5 的視角。

### 讀完本課之後

- **《AI Engineering》— Chip Huyen（O'Reilly, 2025）**：把 LLM 應用的工程面（eval、部署、成本）推得更系統化。
- **[LangGraph 官方文件](https://langchain-ai.github.io/langgraph/)**：本課教你「自己刻」之後，這是業界最常見的「不自己刻」的選擇，值得對照（Ch 40 會談）。

## 給 AI 協作者的備註

本課以「先講直覺 → 給可跑範例 → 挖底層機制 → 踩雷 → 延伸閱讀」為固定節奏，繁體中文、口吻直接有觀點。新增或修訂章節時，請對照根目錄 `.claude/skills/learn/SKILL.md` 的深度 checklist。
