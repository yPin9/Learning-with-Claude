# AI 應用實戰:Claude 生態與通用 LLM 工程

> 給已經會寫 code、用過 Claude 但停在「會問問題」階段的工程師。目標是一手把 Claude 用到極致(power user),一手能用 Claude 生態做產品(builder)。兼收通用 AI 工程(RAG、eval、observability)。

---

## 這門課不是什麼

- **不是 prompting 教學手冊**。「三句話讓 Claude 幫你寫履歷」這種不收。
- **不是 Anthropic 文件翻譯**。文件照抄沒意義,這裡只講你讀完文件會遇到的問題。
- **不是純 agent 狂熱**。Agent 被講得太神,但 80% 的 AI 產品不需要 agent,要會分辨。

## 這門課講什麼

- **Claude 生態怎麼拼起來**:claude.ai / Claude Code / API / MCP / Skills / Agent SDK 各自的位置、適用場景、不要誤用。
- **Power user 路線**:Claude Code 的 skills / hooks / subagents / MCP config,把 Claude 變成你的第二雙手。
- **Builder 路線**:API + tool use + prompt caching + Agent SDK 做出能 ship 的產品。
- **通用 AI 工程**:RAG、eval、observability、safety——LLM 產品不只是「呼叫 API」。
- **系統設計心法**:Agent 架構模式、真實產品拆解、成本與失敗處理。

## 為什麼是 Claude 生態

三個理由:

1. **生態最完整且一致**:從 consumer app 到 Code CLI 到 API 到 Agent SDK,設計哲學統一。MCP 是 Anthropic 主推的開放協議,已被其他廠商採用。
2. **Tool use / caching / thinking 這些 primitive 的實作最成熟**。
3. **Claude Code 是「LLM agent 怎麼做」的活教材**。讀懂它等於讀懂 agent 設計。

本門課用 Claude 當主軸,但 Part 5 通用 AI 工程章節儘量不綁死一家。

## 章節地圖

### Part 0 — 心法
- [Ch 0 LLM 應用的錯誤心智模型](./00-mental-models.md)
- [Ch 1 Token / Context / Sampling / Tool Use 的最低必備](./01-llm-essentials.md)
- [Ch 2 Claude 生態地圖](./02-claude-ecosystem-map.md)

### Part 1 — 用好 Claude(power user 路線)
- [Ch 3 claude.ai / Projects / Artifacts](./03-claude-app.md)
- [Ch 4 Prompting 的心法(不是招式集)](./04-prompting.md)
- [Ch 5 Claude Code 起手式](./05-claude-code-basics.md)
- [Ch 6 Claude Code 進階:skills / hooks / subagents / MCP](./06-claude-code-advanced.md)
- [Practice A — Prompting 實戰](./practice-a-prompting.md)

### Part 2 — Claude API(builder 路線起點)
- [Ch 7 Messages API 與 SDK](./07-api-basics.md)
- [Ch 8 Tool Use:真正在做什麼](./08-tool-use.md)
- [Ch 9 Prompt Caching:省錢也省延遲](./09-prompt-caching.md)
- [Ch 10 Extended Thinking / Streaming / Batch / Files](./10-advanced-api.md)

### Part 3 — MCP(Model Context Protocol)
- [Ch 11 MCP 為什麼存在](./11-mcp-why.md)
- [Ch 12 用現有的 MCP server](./12-using-mcp.md)
- [Ch 13 寫自己的 MCP server](./13-writing-mcp.md)
- [Ch 14 MCP 進階:resources / prompts / sampling / transport](./14-mcp-advanced.md)
- [Practice B — 寫一個 MCP server](./practice-b-mcp-server.md)

### Part 4 — Agent SDK + Skills
- [Ch 15 Skills 的設計與寫作](./15-skills.md)
- [Ch 16 Claude Agent SDK 基礎](./16-agent-sdk.md)
- [Ch 17 Subagents 與多 agent 協作](./17-subagents.md)
- [Ch 18 長時間 agent 的工程挑戰](./18-long-running-agents.md)
- [Practice C — 寫一個 agent](./practice-c-agent.md)

### Part 5 — 通用 AI 應用工程
- [Ch 19 RAG:向量 / hybrid / rerank](./19-rag.md)
- [Ch 20 Evaluation:沒 eval 的 LLM app 是玩具](./20-eval.md)
- [Ch 21 Observability:traces / cost / latency](./21-observability.md)
- [Ch 22 Safety 與 guardrails](./22-safety.md)
- [Practice D — RAG + eval pipeline](./practice-d-rag-eval.md)

### Part 6 — 系統設計與案例
- [Ch 23 Agent 架構模式](./23-agent-patterns.md)
- [Ch 24 真實產品拆解](./24-product-teardowns.md)
- [Ch 25 成本、降級、失敗處理](./25-ops.md)

### Final Project
- [上線一個真實 AI 應用](./final-project-ship-it.md)

---

## 學習建議

1. **Part 0 一定先讀**。不讀的話後面每章你都在錯頻道接收。
2. **選你的主線**。power user 偏前端?先 Part 1,跳 Part 2,做 Practice A;builder 偏後端?Part 1 快速掃,重點在 Part 2–4。通用 AI 工程(Part 5)所有人都要讀。
3. **Practice 不是讀的,是寫的**。沒自己寫過 MCP server / agent,不算學會。
4. **Final project 是上線一個東西**,不是寫 demo。這門課最後一步,也是最重要的一步——把前面所有知識黏起來。

## 建議環境

- Python 3.11+(Agent SDK 最低要求),TypeScript(選修,MCP / Skills 會碰)
- Claude Code CLI(Part 1 必要)
- Anthropic API key(Part 2 起必要,沒 key 這門課大半沒得做)
- IDE:VS Code 或 Cursor 加 Claude Code extension

## 預估時數

對已經會寫 code 的工程師:

- Part 0:3 小時,純讀。
- Part 1–4:每 part 約 10–20 小時(讀 + 動手),**共 50–80 小時**。
- Part 5:20–30 小時。
- Part 6:讀 5 小時,思考與討論另計。
- Practices 每個 5–15 小時。
- Final project **10–30 小時**,視野心。

**總計 120–200 小時**。走完你會是能 ship AI 產品的工程師,不是只會寫 prompt 的玩家。

## 參考

- **Anthropic 官方文件**:API 文件、Claude Code 文件、Agent SDK 文件——這門課的第一手資料。章節會指到特定頁。
- **Model Context Protocol 規格** — `modelcontextprotocol.io`
- **The Prompt Report** (Schulhoff et al., 2024) — prompting 技巧的學術綜述,當字典用。
- **Building Effective Agents** (Anthropic 2024 blog) — agent 設計原則,濃縮。
- **LLM Powered Autonomous Agents** (Lilian Weng 2023) — 架構模式,仍然適用。

## 一段話總結這門課的態度

**別迷信 agent,也別迷信 prompt。AI 應用是工程問題,不是魔法。**

你最後會需要:寫清楚的 prompt、組合對的工具、建立 eval、監控成本、處理失敗。這些都是傳統軟體工程的延伸,只是多了一個不保證確定性的 component 叫 LLM。

這門課把那個 component 的「可預測性」教給你。
