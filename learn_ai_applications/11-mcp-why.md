# Ch 11 — MCP 為什麼存在

> 目標:搞懂 MCP 解決的問題、它跟 tool use 的關係、為什麼「寫一次給所有 LLM client 用」是 big deal。

## 先看問題

Tool use(Ch 8)讓 Claude 能呼叫你定義的工具。但:

### 問題 1:每個 LLM client 都要接一次

你有個「查公司內部 DB」的工具,想讓:

- Claude Code(工程師用)
- Claude Desktop(PM 用)
- 公司自建 chatbot(客服用)
- 另一家 LLM 的 agent(partner 用)

都能接。怎麼做?

**沒 MCP 前**:每個 client 都要寫一套 integration。改工具邏輯要改 N 個地方。

### 問題 2:工具生態無法共享

有人寫了超好用的「從 Figma 讀設計」的工具。你想用,要 copy-paste 他的 code 改成你的 client 格式。**重用性是 0**。

### 問題 3:工具權限和 sandbox 各家做各家

Claude Code 的 permission 跟 Claude Desktop 的權限格式不同,跟你自家 client 又不同。每個 client 重造輪子。

---

## MCP 的提案

**定義一個通用協議,讓 LLM client 和外部工具可以互通**。

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  LLM Client              │         │  MCP Server              │
│  (Claude Code / Desktop  │ ←───→   │  (filesystem / DB /      │
│   / Cursor / 自家 agent) │  MCP    │   Slack / 公司內部工具)  │
└──────────────────────────┘         └──────────────────────────┘
```

**任何 client 接任何 server**,只要雙方講 MCP。

### 類比

- **USB-C**:不同廠牌的電腦、手機、螢幕,一個 port 通用。MCP 是「LLM 工具接口的 USB-C」。
- **LSP**(Language Server Protocol):VS Code、vim、Emacs 都能用同一個 pyright 寫 Python。MCP 是 LLM 版的 LSP。

LSP 是 Microsoft 2016 推的協議,**解決了 IDE × 語言爆炸成 N×M 問題,降到 N+M**。MCP 目標一樣:**解決 LLM client × 工具的 N×M 爆炸**。

---

## MCP 不只是 tools

**Tool use** 是「函數呼叫」。**MCP 定義了三種「原語」(primitives)**:

### 1. Tools(工具)

可被 LLM 呼叫的操作:

```
mcp://postgres-server/query(sql: "SELECT * FROM users")
```

跟 tool use 的 tools 概念一樣,但透過 MCP 傳輸。

### 2. Resources(資源)

**LLM 可讀的靜態 / 動態資料**:

```
mcp://filesystem-server/file:///path/to/doc.txt
mcp://github-server/issue/123
```

Resource 不是「執行」,是「讀取」。LLM client 可以把它當 context 餵給 LLM。

### 3. Prompts(預定義的 prompt 模板)

伺服器提供**可重用的 prompt**:

```
mcp://code-review-server/review-python-pr(pr_id: 123)
→ 回傳一串預先組裝好的 messages
```

Client 可以 enumerate、讓使用者選,直接用。

**多數開源 MCP server 只 implement tools**,resources 和 prompts 是漸進採用的。

---

## MCP 跟 tool use 的關係

一個常見誤解:「MCP 是 tool use 的替代」。

**正確理解**:

- **Tool use** = Anthropic API 的 feature(API 呼叫 Claude 時帶 tools)
- **MCP** = 協議,讓外部系統能被接成 tool

流程:

```
你寫/裝 MCP server(e.g., postgres)
     ↓
你的 MCP client(e.g., Claude Code)連它
     ↓
Client 把 server 的 tools 轉成 tool definitions,塞進 API request
     ↓
Claude 決定 call 哪個 tool
     ↓
Client 收到 tool_use,轉發給對應的 MCP server 執行
     ↓
Server 回結果,Client 塞回 API 的 tool_result
```

**MCP 是 tool use 的一個 source**,不是取代它。Client 依然把 MCP 工具轉成 API 的 tool 格式。

---

## MCP 的 transport

MCP 支援多種傳輸方式:

| Transport | 場景 |
|---|---|
| **stdio** | 本地 server,client 啟 subprocess |
| **SSE** (Server-Sent Events) | 遠端 HTTP server |
| **Streamable HTTP** | 新一代 HTTP transport(替換 SSE) |

**Claude Code / Desktop / Agent SDK** 主要用 **stdio**(本地 process)。
**Anthropic API** 有 **MCP Connector**(beta),透過 HTTP 接**遠端** MCP server(SSE / Streamable HTTP)。

---

## 為什麼 Anthropic 推 MCP 而不是自己做閉源

幾個戰略理由:

1. **生態大於獨占**:閉源 tool 框架會被競品繞過,開放協議反而能「全盤皆黑」(別家 LLM 也用,但你的工具集能先發制人)。
2. **LSP 的成功案例**:Microsoft 開放 LSP 後,整個 IDE / 工具生態都受益,Microsoft 的 VS Code 反而變得更吃香。
3. **企業採用**:內部 IT 團隊接一個 MCP server,同時給 Claude、內部 agent、未來可能接入的別家 LLM 用——採用阻力低。

**結果**:2025 年底,MCP 已經被其他主要 LLM 廠商採用,成為事實上的業界標準。

---

## 有哪些現成的 MCP server

**官方**(Anthropic / 社群):

- `filesystem` — 檔案系統存取
- `git` — git 操作
- `github` — GitHub API(issue、PR)
- `gitlab` — 同上,GitLab 版
- `memory` — persistent memory across sessions
- `puppeteer` / `playwright` — browser 自動化
- `sqlite` / `postgres` — DB 查詢
- `slack` — Slack 訊息
- `google-drive` — Google Drive 檔案
- `brave-search` / `perplexity` — web 搜尋
- 更多:https://github.com/modelcontextprotocol/servers

**裝的方式**:在 client 設定裡加這樣一段:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["mcp-server-postgres"],
      "env": { "DATABASE_URL": "postgresql://..." }
    }
  }
}
```

下一章細講。

---

## 心智模型:MCP = 工具生態的 package manager

類比:

| | 程式語言 | MCP |
|---|---|---|
| 單位 | package | MCP server |
| 發布 | PyPI / npm | 各 repo,還在發展 registry |
| 安裝 | pip / npm install | `uvx` / `npx` 啟動 |
| 使用 | import | client 配置後自動接 |

**發布 MCP server 的門檻比寫 package 還低**——就是實作協議,不用發行到 registry 也能用。

---

## MCP 的限制

2025–2026 年的 MCP 不是完美的:

### 限制 1:Auth 還不成熟

MCP 協議本身對 auth 不強制。每個 server 各自處理(env 變數、config 檔、OAuth...)。**企業場景接 SaaS 服務會複雜**。2025 年底有 spec 更新引入 OAuth 2.1,但採用率還在上升。

### 限制 2:發現 / registry 生態弱

裝新 MCP server 要手動配置。沒有 `npm search` 等價物——你怎麼知道某功能有 server 可用?目前靠社群 list 和 Anthropic 官方推薦。

### 限制 3:Schema 不夠嚴謹

Tool schema 用 JSON Schema,但 MCP 不強制所有欄位。有些 server 給得馬虎,client 要寫防呆。

### 限制 4:Stateful 的 server 設計難

MCP server 通常該是 stateless 或有明確 session 概念。共享狀態的 server 設計坑多。

### 限制 5:成本模型

Tool definition 佔 context。**接 10 個 MCP server 每個 5 tools,50 個 tool definitions 灌進 context**,成本顯著。

**對策**:只裝你真的要用的 server,或寫 subagent 做 tool routing。

---

## 什麼時候你該用 MCP

### ✓ 適合 MCP

- 工具會被**多個 client** 用到
- 要把**公司內部系統**接給 LLM,且希望重用
- 想**公開發布**給社群用
- 接**現有開源 MCP server**(省自己寫 tool)

### ✗ 不適合 MCP

- 只有一個 client、一個用途(直接 tool use 就好)
- 極低延遲需求(MCP 多一層 IPC 開銷)
- 工具邏輯跟你 app 深度耦合(外化成 server 反而複雜)

**原則**:有 reuse 需求才套 MCP,否則 tool use 裸寫就好。

---

## 自我檢核

- [ ] MCP 解決的 N×M 問題是什麼?類比 LSP 在哪裡?
- [ ] MCP 的三個原語是?哪一個最常被實作?
- [ ] MCP 和 tool use 是取代關係嗎?流程上怎麼配合?
- [ ] stdio / SSE / Streamable HTTP 三種 transport 各自的場景?
- [ ] 什麼時候你應該不用 MCP,直接寫 tool use?

→ [Ch 12 用現有的 MCP server](./12-using-mcp.md)
