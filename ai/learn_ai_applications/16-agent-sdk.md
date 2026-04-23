# Ch 16 — Claude Agent SDK 基礎

> 目標:用 Agent SDK 寫一個能真實跑的 agent。理解 `query()` vs `ClaudeSDKClient`、options 的設計、hooks 和 permission 怎麼在 SDK 裡用。

## 為什麼要 Agent SDK 而不是 Anthropic SDK

Anthropic SDK 是「打 messages API」的底層 wrapper。用它寫 agent 你要自己:

- 跑 tool use loop
- 管 context / history
- 寫 hook 機制
- 寫 permission gate
- 連 MCP server
- 做 session / checkpoint

**全部自己來很辛苦**。Claude Agent SDK 把這些打包成一個 library。

Claude Code CLI 本身就用 Agent SDK 寫的——同樣的引擎,只是 CLI 是 terminal 介面,SDK 讓你 embed 到自己的 code。

---

## 安裝

```bash
# Python
pip install claude-agent-sdk

# Node
npm install @anthropic-ai/claude-agent-sdk
```

需要 `ANTHROPIC_API_KEY` 在環境變數(或用 Claude Max 的 session token,SDK 自動偵測)。

---

## 兩個 entry point

### `query()`:one-shot

最簡單:問一個問題,agent 跑完返回。

```python
import asyncio
from claude_agent_sdk import query

async def main():
    async for message in query(prompt="What's in my current directory?"):
        print(message)

asyncio.run(main())
```

Agent 會:

1. 收到你的 prompt
2. 跑 tool use loop(用 Read、Bash、Glob 等內建工具)
3. 逐步 yield messages(assistant 輸出、tool use、tool result)
4. 最後 end

**適合**:腳本、CI 任務、一次性查詢。

### `ClaudeSDKClient`:long-lived session

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async def main():
    options = ClaudeAgentOptions(model="claude-sonnet-4-6")
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Read README.md and summarize")
        async for msg in client.receive_messages():
            print(msg)

        await client.query("Now modify it to add a 'Testing' section")
        async for msg in client.receive_messages():
            print(msg)

asyncio.run(main())
```

**Session 持續**:同一 client 多次 query,context 會累積。

**適合**:chatbot、多輪 agent、需要 session state 的場景。

---

## `ClaudeAgentOptions`:所有配置的集中處

Options 是 agent 的行為控制面板:

```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    # 模型
    model="claude-sonnet-4-6",

    # System prompt(補充,不替代內建)
    system_prompt={"type": "preset", "preset": "claude_code", "append": "Focus on security."},

    # 或完全自訂 system prompt:
    # system_prompt="You are a dev assistant.",

    # 工具白名單
    allowed_tools=["Read", "Glob", "Grep", "Bash"],

    # permission mode
    permission_mode="acceptEdits",    # 或 default / plan / bypassPermissions

    # 連 MCP servers
    mcp_servers={
        "postgres": {"command": "uvx", "args": ["mcp-server-postgres"],
                     "env": {"DATABASE_URL": "..."}},
    },

    # Hooks
    hooks={...},   # 後述

    # Session 持久化
    session_id="my-agent-session-1",
    resume=True,   # 接續上次
)
```

### 常用欄位細節

**`allowed_tools`**:限制 agent 能用的工具集。**少即是多**——給 8 個工具 > 給 30 個。

**`permission_mode`**:

- `default`:每個有副作用的工具 call 都 ask
- `acceptEdits`:自動允許檔案修改,其他 ask
- `bypassPermissions`:全部自動(危險,只在 sandboxed env)
- `plan`:只讀,禁 write

Production agent 通常用 `default` + `allowed_tools` 限制工具,或 `acceptEdits` 在 QA 流程中。

**`system_prompt`**:

`{"type": "preset", "preset": "claude_code"}` 用內建 Claude Code 的 system prompt(推薦),`append` 加自己的補充。

完全自訂用 string:`system_prompt="You are ..."`——但會失去內建的 tool-use convention。

---

## 寫一個實用的 agent:PR reviewer

```python
# pr_reviewer.py
import asyncio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async def review_pr(pr_diff: str) -> str:
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        allowed_tools=["Read", "Glob", "Grep"],   # 只讀
        permission_mode="default",
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": """You are reviewing a pull request.
Output format:
- ISSUES: list of issues (line, severity, description)
- SUGGESTIONS: improvement ideas
- VERDICT: APPROVE / REQUEST_CHANGES
""",
        },
    )

    final_text = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Review this PR diff:\n\n{pr_diff}")
        async for msg in client.receive_messages():
            if msg.type == "assistant" and hasattr(msg, "content"):
                for block in msg.content:
                    if block.type == "text":
                        final_text.append(block.text)

    return "\n".join(final_text)

if __name__ == "__main__":
    import sys
    diff = sys.stdin.read()
    result = asyncio.run(review_pr(diff))
    print(result)
```

用法:

```bash
git diff main | python pr_reviewer.py
```

**這就是一個 production-style agent**。你可以用同樣模式做:

- 自動 triage issue
- 自動生成 release notes
- 自動監控 log 並 alert
- 自動回 Slack 內部問題

---

## Hooks in SDK

SDK 的 hooks 跟 Claude Code 的 hooks 概念一致,但配置方式是 Python 物件:

```python
from claude_agent_sdk import HookMatcher

async def before_bash(input_data, tool_use_id, context):
    """Block dangerous commands."""
    command = input_data.get("tool_input", {}).get("command", "")
    if "rm -rf" in command:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Blocked destructive command"
            }
        }
    return {}

options = ClaudeAgentOptions(
    ...,
    hooks={
        "PreToolUse": [HookMatcher(matcher="Bash", hooks=[before_bash])]
    }
)
```

**用途**:

- Pre-flight validation(上例)
- Post-action logging
- Auto-format after Edit
- Notification on Stop

---

## Custom Tools(In-Process)

除了內建 + MCP,你可以**直接在 SDK code 裡定義 tool**:

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("get_weather", "Get weather for a city", {"city": str})
async def get_weather(args):
    city = args["city"]
    # 呼叫真的 API
    return {"content": [{"type": "text", "text": f"{city}: 22°C sunny"}]}

# 把這些 tool 包成一個 in-process MCP server
weather_server = create_sdk_mcp_server(
    name="weather",
    version="1.0.0",
    tools=[get_weather]
)

options = ClaudeAgentOptions(
    mcp_servers={"weather": weather_server},
    allowed_tools=["mcp__weather__get_weather", "Read"],
)
```

**好處**:不用另開 process,tool 跟主程式共享 state。適合 tool 需要 app 內部狀態的場景。

---

## Streaming vs 一次性 response

SDK 預設是 streaming(yield messages)。想收完整 response 一次:

```python
async def collect_response(client):
    messages = []
    async for msg in client.receive_messages():
        messages.append(msg)
    return messages
```

或 `query()` 用 list comprehension。

**Recommended**:處理 streaming,對大 response 有更好 UX。

---

## Session 與 Resume

```python
options = ClaudeAgentOptions(
    session_id="agent-v1-user-42",
    resume=True,
)
```

`session_id` 讓 SDK 知道這 session 的身份。加 `resume=True` 嘗試接續之前的對話。

SDK 把 session 存在本地(`~/.claude/sessions/...`)。多台機器共享需要自管。

**適用**:

- 定期喚醒的 agent(「每天早上 9 點 check X」)
- 長任務中斷後恢復
- Chatbot 保持 user 的長期 context

---

## Checkpointing(進階)

Agent 跑到一半系統炸,能不能從中間 resume?SDK 提供 checkpoint 機制(細節因版本而異,查文件):

- 每次重要 tool call 前自動 checkpoint
- 失敗時 resume 從上次 checkpoint
- **重要用途**:部分完成的長任務不要從頭跑

這是把 agent 做成「可重入」的關鍵能力。Ch 18 會再細講長任務的設計。

---

## 錯誤處理

### Agent 內部錯誤

```python
from claude_agent_sdk import AnthropicError

try:
    async for msg in query(prompt="..."):
        print(msg)
except AnthropicError as e:
    print(f"Agent failed: {e}")
```

SDK 自動 retry 可重試的錯(429、529)。不可重試(auth、invalid request)就 raise。

### Tool 執行錯誤

Tool 內部 exception 會被抓,變成 tool_result 的 is_error,送回給 Claude——Claude 有機會繞過或 report 給使用者。

### Timeout

SDK options 有 tool-level timeout 設定。長跑工具(browser、大檔 IO)要注意。

---

## 搭配 Claude Code 的 skills / agents / hooks

Agent SDK 自動讀 `.claude/` 目錄的 skills、agents、hooks、settings.json。所以**你在 Claude Code 配的東西,SDK 直接繼承**。

```
your_project/
├── .claude/
│   ├── settings.json      ← SDK 會讀
│   ├── skills/            ← 可用
│   ├── agents/            ← 可用
│   └── CLAUDE.md          ← 會被當 context
└── run_agent.py
```

一致性是設計的重點——**同一組配置**,CLI 和 SDK 共享。

---

## Python vs TypeScript

兩版 API shape 幾乎一樣。差別:

- Python:`async def` + `async for`
- TypeScript:`async function` + `for await`
- Python:`ClaudeAgentOptions` dataclass
- TypeScript:plain objects

Pick whichever matches你現有 stack。後端多 Python,前端整合多 TS。

---

## 什麼時候該用 SDK,什麼時候該用 Anthropic SDK

| 需求 | 建議 |
|---|---|
| 純 API 呼叫,沒 tool | Anthropic SDK |
| 很多 tool,但無 agent loop(簡單分類 / 摘要) | Anthropic SDK + 自己寫簡單 tool loop |
| 真正的 agent(多步、有記憶、工具組合) | Claude Agent SDK |
| 要用 Claude Code 的 skills / MCP / hooks | Claude Agent SDK(必要) |
| 要極致控制每個 byte | Anthropic SDK |
| 要整合 file checkpointing / session | Claude Agent SDK |

**不要重造輪子**。如果你在 Anthropic SDK 之上寫 tool use loop + hooks + permission,就是在重寫 Agent SDK。

---

## 一個小 workflow:daily standup digest

```python
# standup_digest.py
import asyncio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async def standup_digest(user: str):
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        mcp_servers={
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."},
            },
            "slack": {
                "command": "...",
            },
        },
        allowed_tools=[
            "mcp__github__list_user_pull_requests",
            "mcp__github__list_user_commits",
            "mcp__slack__post_message",
        ],
        system_prompt=f"""You are preparing a daily standup digest for user '{user}'.

        1. Fetch their GitHub activity in the last 24 hours.
        2. Summarize into: "Yesterday", "Today (planned, infer from open PRs)", "Blockers (if any)".
        3. Post to Slack channel #standup with the summary.
        """
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Generate and post standup for {user}")
        async for msg in client.receive_messages():
            pass    # 或 log

if __name__ == "__main__":
    asyncio.run(standup_digest("alice"))
```

排程跑這腳本,就是個自動 standup bot。這就是 Agent SDK 的實用形狀。

---

## 自我檢核

- [ ] `query()` 和 `ClaudeSDKClient` 的差別?各自的適用?
- [ ] Agent SDK 和 Anthropic SDK 的關係?
- [ ] `ClaudeAgentOptions` 的 `allowed_tools` 為什麼建議限縮?
- [ ] SDK 怎麼讀 `.claude/` 的 skills / MCP?
- [ ] 什麼時候你**不該**用 Agent SDK?

→ [Ch 17 Subagents 與多 agent 協作](./17-subagents.md)
