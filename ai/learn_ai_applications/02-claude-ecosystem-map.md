# Ch 2 — Claude 生態地圖

> 目標:把 Anthropic 的七八個產品 / SDK / 協議**放在對的位置**。生態地圖清楚之後,你才知道每個問題該拿哪張牌。

## 問題:太多名字

聽過但可能搞混的東西:

- claude.ai、Claude Pro、Claude Max、Claude Team、Claude Enterprise
- Claude Desktop、Claude Code
- Anthropic API、Anthropic Console、Anthropic Workbench
- Claude Agent SDK、Anthropic SDK
- MCP、MCP Connector
- Skills、Commands、Subagents、Hooks
- Claude App for iOS / Android

**一次釐清**,用「你想解決什麼問題」來分類。

---

## 按「你要幹嘛」分類

### A. 你只想跟 Claude 聊天 / 做事,不寫 code

- **claude.ai**(web 介面)
- **Claude Desktop**(Mac / Windows app)
- **Claude for iOS / Android**
- 付費方案:Pro / Max / Team / Enterprise

**適用**:寫文件、分析資料(用 Artifacts)、有限的 project 分享。
**限制**:沒 tool extensibility(除了 Desktop 能配 MCP),沒 hooks,不能 script。

### B. 你要把 Claude 用到工程場景(terminal、IDE、agent 腳本)

- **Claude Code CLI**:指令列的 Claude,可以讀你本地檔案、改 code、執行 bash
- **Claude Code IDE extensions**(VS Code / JetBrains)
- 裡面有:slash commands、skills、hooks、subagents、permission modes

**適用**:寫 code、debug、refactor、做 repo 級的操作、自動化工作流。
**限制**:要在本機裝 CLI,需要 Anthropic 帳號 + billing。

### C. 你要**寫產品**,API 對 API 串接

- **Anthropic API**(HTTP REST,也叫「Claude API」或「Messages API」)
- **Anthropic SDK**(Python / TypeScript 的 wrapper)
- **Anthropic Console**(Web GUI 管 API key、看用量、測試 prompt)
- **Anthropic Workbench**(Console 裡的 prompt playground)

**適用**:寫 LLM 驅動的 backend、chatbot、pipeline、工具。
**你要寫的**:tool execution loop、prompt management、retry、eval、observability。

### D. 你要**寫 agent**(有狀態、多工具、長時間運行)

- **Claude Agent SDK**(Python / TypeScript)
- 幫你處理:tool loop、hooks、subagent、MCP connection、session、permission
- **是 Claude Code 的底層 library**——Claude Code 本身用 Agent SDK 寫的

**適用**:做產品級 agent(自動化 QA、DevOps bot、個人助理)。
**跟 Anthropic SDK 差別**:Anthropic SDK 是最底層「打 API」的,Agent SDK 多了 agent loop 的基礎設施。

### E. 你要**擴展 Claude 的能力**(接工具、接資料)

- **MCP(Model Context Protocol)**:開放協議,讓 LLM 接外部工具 / 資料 / prompts
- **MCP server**:實作了 MCP 協議的程式(可以是 DB、API、檔案系統、瀏覽器...)
- **MCP client**:Claude Code / Claude Desktop / Claude API 都可以當 client
- **MCP Connector**:Anthropic API 的 beta feature,讓 API 能接遠端 MCP server

**適用**:讓 Claude 能讀你的 DB、操你的內部系統、存取你公司文件。
**好處**:寫一次 MCP server,N 個 LLM client 都能用。

### F. 你要給 Claude「技能」或「命令」

- **Skills**:markdown 檔 + 可選附件,告訴 Claude「某種任務該怎麼做」
- **Commands**(舊機制,merge 到 Skills 中):自訂 slash command
- **Subagents**:有特定任務、獨立 context 的 agent
- **Hooks**:在生命週期事件(tool use 前/後、session 開始/結束)插入自訂邏輯

這些是 Claude Code 和 Agent SDK 特有,不是 API 層級的東西。

---

## 用一張圖理解

```
┌──────────────────────────────────────────────────────────────┐
│                     使用者 / 開發者                           │
└──────────────────────────────────────────────────────────────┘
          │                    │                       │
    非寫 code             power user              builder
          │                    │                       │
    ┌─────▼────┐         ┌─────▼──────┐         ┌──────▼──────┐
    │ claude.ai │         │ Claude Code │         │  寫產品     │
    │ Desktop   │         │    CLI      │         │             │
    └─────┬────┘         └─────┬──────┘         └──────┬──────┘
          │                    │                       │
          │          ┌─────────▼────────┐        ┌─────▼──────────┐
          │          │ Skills / Hooks / │        │ Anthropic SDK  │
          │          │ Subagents /      │        │ (Python/TS)    │
          │          │ MCP clients      │        │ 或 Agent SDK   │
          │          └─────────┬────────┘        └─────┬──────────┘
          │                    │                       │
          ▼                    ▼                       ▼
    ┌─────────────────────────────────────────────────────────┐
    │              Anthropic API (Messages endpoint)          │
    │   tool_use · prompt caching · extended thinking · ...   │
    └─────────────────────────────────────────────────────────┘
                              │
                              ▼
                     ┌────────────────┐
                     │ Claude 模型    │
                     │ (Sonnet / Opus │
                     │  / Haiku)      │
                     └────────────────┘

      ┌──────────────────────────────────────────┐
      │           MCP Servers(工具/資料)         │
      │  filesystem · DB · browser · Slack · ... │
      └──────────────────────────────────────────┘
                   (任何 MCP client 都能連)
```

---

## 誰用誰的對應表

| 我是 | 我該學 | 不用學 |
|---|---|---|
| 想用 Claude 做日常工作 | claude.ai / Desktop | API、SDK |
| 工程師想 boost 工作流 | Claude Code CLI + Skills + Hooks | API(先) |
| 要寫 chatbot 或 API feature | Anthropic API + SDK + prompt caching | Agent SDK(先) |
| 要寫真正的 agent | Agent SDK + MCP | Claude Code 內部 |
| 要擴展 Claude 能力給別人用 | 寫 MCP server | 其他 |
| 要做 Claude Code 自訂 | Skills + Subagents + Hooks | API(這層不夠) |

---

## 常見搞混

### 搞混 1:「Claude Code 和 Agent SDK 是同一個東西嗎?」

**接近但不同**:

- Claude Code = CLI 應用,給你一個 interactive 的開發環境
- Agent SDK = library,你 import 進自己的 code 來建 agent

**Claude Code 的底層就是 Agent SDK**。兩者共享模型調用、tool loop、hooks、skills 載入邏輯。

**使用時機**:
- 互動式開發 / 探索 / debug → Claude Code CLI
- 自動化腳本 / 排程任務 / production service → Agent SDK

### 搞混 2:「MCP 和 tool use 是不是一樣?」

**不一樣**。

- **Tool use** = Anthropic API 的一個 feature,讓 Claude 呼叫你定義的工具
- **MCP** = 開放協議,定義「工具、資料、prompts 怎麼被 LLM 呼叫」的標準

你可以不用 MCP,直接在 API 的 `tools` 參數塞 tool definition。MCP 的價值是**重用**:寫一次 MCP server,能被 Claude Code、Claude Desktop、Anthropic API(透過 connector)、其他 LLM 客戶端都用。

### 搞混 3:「我用 claude.ai 可不可以裝 MCP?」

**不能直接**。claude.ai(web)沒 MCP 支援。MCP 客戶端目前是:

- Claude Desktop(可裝 MCP server)
- Claude Code CLI(可裝 MCP server)
- 你自己寫的 Agent SDK 程式(可接 MCP server)
- Anthropic API + MCP Connector(beta)

未來可能變,但目前是這樣。

### 搞混 4:「Skills 是 Claude Code 才有的嗎?」

**目前對**。Skills 是 Claude Code / Agent SDK 的機制,不是 API 層。

**但概念在 API 層可以模擬**:把 skill 內容變成 system prompt 的一部分。這也是 Skills 本質——**結構化的 prompt reuse 機制**。

### 搞混 5:「Plugin 是什麼?」

Plugin 是 Claude Code 的機制,可以 bundle 一組 skills + commands + MCP config,發布給別人用。類似 VS Code extension。

---

## 這門課接下來的路線

按照這張地圖,後續章節的安排:

- **Part 1**(Ch 3–6):你當 power user 的路線。從 claude.ai 一路升到 Claude Code + MCP。
- **Part 2**(Ch 7–10):API / SDK builder 路線的起點。
- **Part 3**(Ch 11–14):MCP 獨立一 part,因為它是生態的中心。
- **Part 4**(Ch 15–18):Skills 和 Agent SDK。
- **Part 5**(Ch 19–22):跳開 Claude,談通用 AI 工程。
- **Part 6**(Ch 23–25):系統設計與真實案例。

---

## 自我檢核

- [ ] Anthropic SDK 和 Claude Agent SDK 的差別是什麼?各用於什麼場景?
- [ ] MCP 和 API 的 tool use,同一件事嗎?各自的重用性?
- [ ] 「寫一個 DevOps 自動化 bot」該用什麼工具組合?
- [ ] Claude Code 的 hooks 是 API 層 feature 嗎?能在 claude.ai 用嗎?
- [ ] 你的下一個 AI side project,會用哪條路線?為什麼?

→ [Ch 3 claude.ai / Projects / Artifacts](./03-claude-app.md)
