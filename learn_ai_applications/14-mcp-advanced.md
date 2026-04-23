# Ch 14 — MCP 進階:resources / prompts / sampling / transport / auth

> 目標:超出基礎的 tool-only server,進入完整 MCP 協議。生產級 server 需要哪些 feature、哪些該懂哪些可忽略。

## Resources 深入

Resources 是「可被引用的內容」。tool 是動詞,resource 是名詞。

### URI 設計

Resource 的識別是 URI。典型:

```
docs://<path>           # 檔案路徑
github://<owner>/<repo>/issue/<n>
slack://channel/<id>/message/<ts>
```

URI 的 scheme 可以任取,但在同一 server 內要一致。

### 靜態 vs 動態 resource

```python
# 靜態:URI 編譯時已知
@app.resource("docs://readme")
def get_readme() -> str:
    return Path("README.md").read_text()

# 動態:URI 含參數
@app.resource("docs://{path:path}")
def get_doc(path: str) -> str:
    return Path(path).read_text()
```

### List resources

Client 要能 enumerate:

```python
@app.list_resources()
def list_docs():
    return [
        mcp.Resource(uri=f"docs://{p}", name=p, mimeType="text/markdown")
        for p in get_all_docs()
    ]
```

Client 看到 list 就能在 UI 展示,user 選哪個 resource 塞進對話。

### MIME type

Resource 可標 MIME:`text/plain`、`text/markdown`、`application/json`、`image/png`...

Client 根據 MIME 決定怎麼 render。

---

## Prompts 深入

Prompts = **server 提供的 prompt template**,讓 user 或 agent 可選。

### 基本

```python
@app.prompt()
def code_review(language: str, code: str) -> str:
    """Generate a code review prompt."""
    return f"""You are a senior {language} engineer.
Review the following code:

```{language}
{code}
```

Output:
- Line-by-line issues
- Overall quality: 1-10
- Refactoring suggestions
"""
```

Client 看到 `code_review(language, code)` 這個 prompt。User 在 UI 選它、填 args,server 回一段 string 或 message list,client 把它當 user message 送給 LLM。

### 多 message prompt

Prompt 不是只能回 string,可以回結構化 messages:

```python
from mcp.types import PromptMessage, TextContent

@app.prompt()
def debug_help(error: str) -> list[PromptMessage]:
    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=f"I got this error:\n\n{error}")
        ),
        PromptMessage(
            role="assistant",
            content=TextContent(type="text", text="Let me help you debug. First, can you share the stack trace?")
        ),
    ]
```

適用:few-shot prompt(user-assistant 例子)、已有對話脈絡要繼續。

### 實務上,prompts 用得不如 tools 多

大部分 server 只做 tools。Prompts 是 MCP 的 nice-to-have,沒用也能做出 work 的 server。

---

## Sampling:Server 反向呼叫 Client 的 LLM

**最會被混淆的 MCP feature**。

概念:Server 在處理 request 時,想用 LLM 做個小推理。它可以**請 client 的 LLM 幫忙**,而不是自己接 LLM API。

### 為什麼要這個

- Server 不必有自己的 LLM key / bill
- User 的 LLM preference 被尊重(client 可能用不同 model)
- 統一的 tracing / observability(client 看得到所有 LLM usage)

### 範例

```python
from mcp.server.fastmcp import Context

@app.tool()
async def summarize(text: str, ctx: Context) -> str:
    """Summarize long text using the client's LLM."""
    result = await ctx.session.create_message(
        messages=[{
            "role": "user",
            "content": [{"type": "text", "text": f"Summarize in 3 bullets:\n\n{text}"}]
        }],
        max_tokens=200,
    )
    return result.content[0].text if result.content else ""
```

### Client 側的控制

Client 收到 sampling request 時**通常會顯示同意對話**——因為這會花 user 的 LLM token。Claude Desktop 會彈權限框。

### 安全考量

**Sampling 是潛在攻擊面**。一個惡意 MCP server 可能:

- 一直 sampling,耗盡 user 的 LLM quota
- 在 sampling prompt 裡注入 prompt injection
- 逼 user LLM 洩漏 client 端資料

**Client 的對策**:
- 預設禁止,每次詢問
- Per-server rate limit
- 敏感對話不允許 sampling

---

## Transport 深入

### stdio

**Server 是 client 啟動的 subprocess**。Client 寫 JSON-RPC 到 server stdin,server 寫回 stdout。

優:
- 最簡單
- 本地,無 network 問題
- 自動生命週期(client 關 → server 結束)

缺:
- 只有同機器
- 不能多 client 共用同 server instance

### SSE(Server-Sent Events)

HTTP 長連接,server 送 event 給 client。雙向有限(client → server 要另開 POST)。

優:
- 遠端,可跨網路
- HTTP 基礎設施(nginx、LB、auth)通用
- 多 client 可連同一 server

缺:
- 單向 streaming,bidirectional 要湊
- 連線管理複雜

### Streamable HTTP(新)

更成熟的 HTTP-based transport,雙向 streaming 一個 endpoint。

優:
- 所有 SSE 的優點
- Bidirectional 原生支援
- 協議更乾淨

2025 年底起,建議新 server 用 Streamable HTTP 而不是 SSE。

### 什麼時候用哪個

- 本地、單 client → stdio
- 遠端、多 user → Streamable HTTP
- 舊 client 相容 → SSE

---

## Auth(authentication / authorization)

早期 MCP 對 auth 沒標準化。2025 年底 spec 加了 **OAuth 2.1 flow** 作為正式支援。

### 簡單版:env 變數

最常見:

```python
import os
API_KEY = os.environ["MY_API_KEY"]

@app.tool()
def fetch_data():
    return external_api_call(api_key=API_KEY)
```

Client 配置 env var:

```json
"env": { "MY_API_KEY": "xxx" }
```

**限制**:config 檔裡明文存 key,對多人用 client 的場景不安全。

### OAuth 2.1

MCP spec 支援標準 OAuth dance:

- Client 第一次連 → server 回 `WWW-Authenticate` header 指向 auth server
- Client 走 OAuth flow 拿 token
- 之後 request 帶 `Authorization: Bearer ...`

**採用中**。2026 年的 production server 逐漸全面支援。

### Per-user context

Server 若要知道「誰在呼叫」,可以從 OAuth claim 讀:

```python
@app.tool()
def list_my_tasks(ctx: Context):
    user_id = ctx.auth.user_id
    return tasks_for(user_id)
```

這讓同一 MCP server 能 serve 不同 user,各自只看自己的資料。

---

## Error Handling 標準

MCP 有標準錯誤 code(延伸 JSON-RPC):

- `-32600`:Invalid Request
- `-32601`:Method not found
- `-32602`:Invalid params
- `-32603`:Internal error
- Server-specific:`-32000` 到 `-32099`

Python SDK 通常 translate Python exception 成對應 code。

**規範**:

- 可預期的使用者錯誤 → raise `ValueError` / 自訂 exception
- 程式 bug → 讓它 raise,framework 會轉成 Internal error
- **敏感資訊不要 leak**(traceback 不要直接回 client)

---

## Notifications

Server 可主動送訊息給 client(不是回 request,是主動 push):

```python
@app.tool()
async def start_long_job(ctx: Context):
    ctx.session.send_log_message(level="info", data="Starting job")
    for i in range(10):
        await do_work()
        ctx.session.send_notification(
            method="notifications/progress",
            params={"progress": (i+1) / 10}
        )
    return "Done"
```

**應用**:progress bar、log stream、async 完成通知。

---

## Server state 和 session

MCP server 的 state 管理:

### 完全 stateless

每個 request 獨立,推薦 default。

```python
@app.tool()
def compute(x, y):
    return x + y    # 無 state
```

### 有限 state(per-session)

Session 是 client-server 一個連線。Session 內可有 state:

```python
from collections import defaultdict

SESSION_STATE = defaultdict(dict)

@app.tool()
def set_context(key: str, value: str, ctx: Context):
    SESSION_STATE[ctx.session_id][key] = value
    return "OK"

@app.tool()
def get_context(key: str, ctx: Context):
    return SESSION_STATE[ctx.session_id].get(key, None)
```

**注意**:session 結束要清 state,不然 memory leak。

### 跨 session state(persistent)

用 DB / Redis / file。視為外部系統處理。

---

## 打包成 Docker / production-ready

一個完整 production MCP server:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY . .

# Streamable HTTP
EXPOSE 8080
CMD ["python", "-m", "my_server", "--transport", "streamable-http", "--port", "8080"]
```

```python
# my_server/__main__.py
import argparse
from . import app

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "stdio":
        app.run()
    else:
        app.run(transport=args.transport, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()
```

配 health check、structured logging、metrics,就是 production ready 的 server。

---

## 測試策略

### 1. Unit test tool 本身

MCP 的 tool 就是普通函數:

```python
def test_search_docs():
    result = search_docs("policy", max_results=5)
    assert len(result) <= 5
    assert all("policy" in r["excerpt"].lower() for r in result)
```

### 2. MCP Inspector

互動試探,前述。

### 3. End-to-end with client

跑真的 Claude Code / Desktop 對 server 發指令。

### 4. Contract test

固定一組 test case,跑多個 server 版本看 schema / 回傳 shape 沒變。

---

## 常見設計陷阱

### 陷阱 1:Tool 太大、做太多事

「super tool」做 10 件事,Claude 選哪條路徑會亂。拆成 focused tools。

### 陷阱 2:Return 太大

回 10 MB JSON 給 LLM。context 爆炸、token 爆炸。**做 summary + pagination**。

### 陷阱 3:忽略 Claude 會錯誤使用

Tool 描述清楚仍會誤用。**server 端要有 defensive checks**——arg validation、rate limit、destructive op 要 double check。

### 陷阱 4:State 綁在 server memory

Server 重啟 state 丟。多 instance 時 state 不共享。需要 persistent state 就接外部 store。

### 陷阱 5:只 expose 成 tool,沒考慮 resource

有些東西(文件集、常用清單)當 resource 更對——user 能主動挑,不需要 Claude 決定。

---

## 本章總結

MCP 的完整 surface:

```
Tools      — Claude 能呼叫的動作
Resources  — 可讀取的內容
Prompts    — 可選的 prompt template
Sampling   — server 反向用 client 的 LLM
Transport  — stdio / SSE / Streamable HTTP
Auth       — env var / OAuth 2.1
Notifications — server push
```

不是每個 server 都要全 implement。**80% 用途,只需要 tools + resources**。

---

## 自我檢核

- [ ] Resource 和 tool 的差別?什麼東西適合當 resource?
- [ ] Sampling 讓 server 反向呼叫 client 的 LLM,為什麼危險?
- [ ] Streamable HTTP 取代 SSE 的原因?
- [ ] Per-session state 和 persistent state 的差別?
- [ ] 寫 MCP server 防禦性設計的五個要點?

→ [Practice B — 寫一個 MCP server](./practice-b-mcp-server.md)(先略過,繼續章節)

→ [Ch 15 Skills 的設計與寫作](./15-skills.md)
