# Ch 12 — 用現有的 MCP server

> 目標:在 Claude Code / Desktop / Agent SDK / Claude API 裝上 MCP server 並實際呼叫。這章是 hands-on,沒理論。

## 裝好第一個 MCP server:filesystem

`filesystem` 是官方 server,給 Claude 精細檔案存取。

### 在 Claude Code

編輯 `~/.claude/settings.json`(全域)或 `./.claude/settings.json`(本專案):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/me/projects"
      ]
    }
  }
}
```

重啟 Claude Code。檢查:

```
/mcp
```

應該看到 `filesystem` 狀態 connected。

**試試用**:

```
> Using the filesystem MCP server, read my ~/projects/foo/README.md
```

Claude 會 call MCP 提供的 `read_file` tool,而不是內建的 `Read`。兩者功能重疊但 MCP 版更靈活(比如可以限制存取範圍)。

### 常見 MCP server 配置

**GitHub**:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"
      }
    }
  }
}
```

**Postgres**:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["mcp-server-postgres"],
      "env": {
        "DATABASE_URL": "postgresql://user:pass@localhost/db"
      }
    }
  }
}
```

**Playwright**(browser 自動化):

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-playwright"]
    }
  }
}
```

**模式**:`command` + `args` 啟 subprocess,`env` 傳敏感 config。

---

## 驗證 MCP server 運作

### 1. 看 tool 列表

Claude Code 載入後,MCP server 的 tools 會被暴露。對話中:

```
> /mcp

Connected servers:
  filesystem (7 tools available)
    - read_file
    - write_file
    - list_directory
    - ...
```

### 2. 直接用 Claude 叫它

```
> What tools does the filesystem server expose?

Claude: The filesystem server exposes these tools:
1. read_file — Read a file
2. write_file — Write/overwrite a file
3. ...
```

### 3. Debug:Server 連不上

常見原因:

- `command` 執行路徑不對(`npx` / `uvx` 不在 PATH)
- `env` 少了必要 var
- Server 啟動後立刻 crash(手動跑 subprocess 看 error)

手動跑:

```bash
npx -y @modelcontextprotocol/server-filesystem /tmp
# 應該看到 MCP protocol 的 JSON-RPC 訊息交流
```

---

## 在 Claude Desktop 裝 MCP

編輯 `~/Library/Application Support/Claude/claude_desktop_config.json`(macOS)或同名檔(Windows 對應路徑)。格式跟 Claude Code 一樣:

```json
{
  "mcpServers": {
    "filesystem": {...},
    "github": {...}
  }
}
```

重啟 Claude Desktop。開一個新對話看設定列有沒有 MCP 的齒輪圖示。

---

## 在 Claude Agent SDK 用 MCP

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.mcp import StdioServerParameters

options = ClaudeAgentOptions(
    model="claude-sonnet-4-6",
    mcp_servers={
        "filesystem": StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
    }
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("Read /tmp/notes.txt using filesystem server")
    async for msg in client.receive_messages():
        print(msg)
```

SDK 幫你 spawn subprocess、管 lifecycle。用法跟 Claude Code 內部一致。

---

## 在 Claude API 用 MCP(Connector,beta)

**API 不能用 stdio**(server-side 沒辦法 spawn process)。要用 **遠端 MCP server**,透過 SSE 或 Streamable HTTP。

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "List my GitHub issues"}],
    mcp_servers=[
        {
            "type": "url",
            "url": "https://your-mcp-server.example.com/sse",
            "name": "github-remote",
            "authorization_token": "bearer xxx"   # optional
        }
    ],
    extra_headers={
        "anthropic-beta": "mcp-client-2025-11-20"   # 或當前版本
    }
)
```

Anthropic 會去 poll 那個 URL,取 tool list,和你 API request 的 tools 合併。

**這 enables**:把公司內部系統包成 MCP HTTP server,只要 API 能觸及的網路,chatbot backend 就能用。

---

## 實用 pattern:只載需要的 MCP

**全部 MCP server 都載太吃 context**。一個實用模式:

- `~/.claude/settings.json`:只放日常常用的 MCP(filesystem、git)
- `./.claude/settings.json`:這個專案專用(例:後端專案 → postgres;前端 → playwright)

團隊共享 `./.claude/settings.json`(commit),個人偏好用 `./.claude/settings.local.json`(gitignore)。

---

## 實用 pattern:用 MCP server 做 company dashboard

假設公司內部有:

- JIRA(任務管理)
- Grafana(metrics)
- 內部 DB(Postgres)

寫 / 裝:

- `mcp-jira`(社群有,或自寫)
- `mcp-grafana`(同上)
- `mcp-postgres`(官方)

設定後,Claude Code 可以處理:

```
> 查 PROJ-123 issue,看看它 related 的 service 最近 24 小時錯誤率,寫個 summary
```

Claude 會依序 call:
1. `mcp-jira.get_issue(PROJ-123)`
2. `mcp-grafana.query(error_rate, service, 24h)`
3. 把結果合成摘要

**這才是 MCP 真正的威力**——工具組合讓 Claude 變公司內部助手。

---

## MCP 和 permission

MCP server 的工具也會經過 Claude Code 的 permission 系統:

```json
{
  "permissions": {
    "allow": [
      "mcp__filesystem__read_file(**)",
      "mcp__postgres__query(SELECT*)"
    ],
    "deny": [
      "mcp__postgres__query(DROP*)"
    ]
  }
}
```

**Pattern syntax**:`mcp__<server_name>__<tool_name>(<arg_pattern>)`。可以 limit 到具體參數形狀。

**強烈建議**:接有寫入 / 執行能力的 MCP server 時,deny 高風險操作。例如:

- `mcp__postgres__query(DROP*)`
- `mcp__postgres__query(DELETE*)`
- `mcp__github__delete_repository(**)`

---

## 調試 MCP:看 server 發什麼

啟 Claude Code 加 verbose flag(確切 flag 查文件):

```
claude --log-level debug
```

或手動跑 server 看 JSON-RPC:

```bash
# Server 側的 stdin / stdout 是 MCP 協議訊息
npx -y @modelcontextprotocol/server-filesystem /tmp < /dev/null
```

訊息長這樣:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
{"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
```

看得到協議的細節,debug 靠它。

---

## 幾個社群熱門 MCP server 介紹

### `mcp-server-memory`

Persistent memory across sessions。Claude 可以「記住」你之前對話 mention 過的 entity、facts、關係。適合個人助理場景。

### `mcp-server-sequential-thinking`

幫 Claude 做結構化 step-by-step 推理。比內建 thinking 更顯式。

### `mcp-server-fetch`

HTTP fetch,但比內建 WebFetch 靈活——可以自訂 headers、method、body。

### `mcp-server-time`

告訴 LLM 現在時間(內建 knowledge 有 cutoff,沒這個 Claude 可能說「我不知道今天幾號」)。

### `mcp-server-browser-use`

啟動 headless browser,agent 能自動瀏覽網頁、填表、點按鈕。**適合自動化 web 任務**。

---

## 何時要自己寫 MCP server

要用現成的沒有、或現有的不符合你需求,就自寫。下一章。

---

## 自我檢核

- [ ] MCP server 在 Claude Code 的配置檔路徑?
- [ ] `/mcp` 指令做什麼?
- [ ] Claude API 用 MCP 的 transport 是哪種?為什麼不能用 stdio?
- [ ] Permission pattern `mcp__postgres__query(DROP*)` 的含義?
- [ ] 你想把公司內部三個系統接給 Claude,該怎麼組合?

→ [Ch 13 寫自己的 MCP server](./13-writing-mcp.md)
