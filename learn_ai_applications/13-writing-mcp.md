# Ch 13 — 寫自己的 MCP server

> 目標:用 Python SDK 寫一個能跑的 MCP server。從最小範例到含錯誤處理、logging、安全檢查的 production-ready 版本。

## 兩個 Python SDK 選擇

Python 寫 MCP server 有兩條路:

### 1. 官方 `mcp` SDK(低階)

- 全 spec 支援
- Async 明確
- 樣板多,但控制力強

### 2. FastMCP(高階,decorator-based)

- Flask-like 風格,decorator 即 tool
- 少 code,快上手
- 適合多數場景

**建議**:FastMCP 起手,需要深度控制再回官方 SDK。

---

## 最小範例(FastMCP)

```bash
pip install mcp
```

```python
# my_server.py
import mcp
from mcp.server.fastmcp import FastMCP

app = FastMCP("my-server")

@app.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

if __name__ == "__main__":
    app.run()    # 預設 stdio transport
```

### 用它

在 Claude Code `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["/full/path/to/my_server.py"]
    }
  }
}
```

重啟 Claude Code:

```
> Use the add tool to compute 42 + 37
```

Claude 會 call `my-server.add(a=42, b=37)` → 79。

---

## 五個核心概念

### 1. Tool 的 docstring 就是 description

Claude 是根據 docstring 決定**何時用、怎麼用**:

```python
@app.tool()
def search_products(query: str, category: str = "") -> list[dict]:
    """Search the product catalog.

    Use this tool when the user asks about products by name or category.
    Returns up to 20 matching products with {id, name, price, category}.

    Args:
        query: Keyword or product name
        category: Optional category filter (e.g., "electronics", "books")
    """
    ...
```

**好 docstring = 好 description = Claude 用對**。

### 2. 型別自動變 JSON Schema

FastMCP 從 function signature 推 schema:

- `int` → `{"type": "integer"}`
- `str` → `{"type": "string"}`
- `list[str]` → `{"type": "array", "items": {"type": "string"}}`
- `Optional[X]` → 選填
- `Literal["a", "b"]` → enum
- Pydantic model → 深度 schema

```python
from pydantic import BaseModel, Field
from typing import Literal

class SearchOptions(BaseModel):
    query: str = Field(description="Keyword")
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=10, ge=1, le=100)

@app.tool()
def search(options: SearchOptions) -> list[dict]:
    """Search products with options."""
    ...
```

Pydantic 的 `description` 會滲透到 schema 裡,Claude 看得到。

### 3. Return 值會被序列化成 tool_result

- String / int / float / bool:直接變文字
- Dict / list:JSON 序列化
- Pydantic model:自動序列化
- 大型 object:建議 return dict / str

**心法**:回給 LLM 的東西是要 LLM「讀」的,格式要好讀。**不要直接 dump 整個 ORM 物件**,篩關鍵欄位。

### 4. Error 要丟 exception

```python
@app.tool()
def get_order(order_id: str) -> dict:
    """..."""
    order = db.find(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    return {"id": order.id, "status": order.status}
```

Exception message 會被轉成 `is_error: true` 的 tool_result 塞給 Claude,Claude 會嘗試 recover 或轉達給使用者。

### 5. Async 也行

I/O-bound 任務用 async:

```python
import httpx

@app.tool()
async def fetch_url(url: str) -> str:
    """Fetch a URL and return text."""
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        return r.text[:5000]    # 截斷
```

FastMCP 會自動 handle sync / async 的差別。

---

## 完整範例:公司內部文件搜尋

```python
# docs_server.py
import mcp
from mcp.server.fastmcp import FastMCP
from pathlib import Path
import re

app = FastMCP("company-docs")

DOCS_ROOT = Path("/var/docs/company")

@app.tool()
def search_docs(query: str, max_results: int = 10) -> list[dict]:
    """Search company internal documents by keyword.

    Returns matching documents with {path, title, excerpt}. Use this when
    the user asks about internal processes, team info, or company policies.

    Args:
        query: Keyword to search for (case-insensitive)
        max_results: Max results to return (1-50)
    """
    max_results = max(1, min(50, max_results))
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []

    for md_file in DOCS_ROOT.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        m = pattern.search(content)
        if not m:
            continue
        # 取 match 前後 100 字當 excerpt
        start = max(0, m.start() - 100)
        end = min(len(content), m.end() + 100)
        excerpt = content[start:end]
        results.append({
            "path": str(md_file.relative_to(DOCS_ROOT)),
            "title": md_file.stem,
            "excerpt": excerpt,
        })
        if len(results) >= max_results:
            break

    return results

@app.tool()
def read_doc(path: str) -> str:
    """Read a full company document by relative path.

    Args:
        path: Relative path under /var/docs/company (e.g., "hr/onboarding.md")
    """
    # Security: 防 path traversal
    target = (DOCS_ROOT / path).resolve()
    if not str(target).startswith(str(DOCS_ROOT.resolve())):
        raise ValueError(f"Path {path} is outside allowed directory")
    if not target.exists():
        raise FileNotFoundError(f"Doc {path} not found")
    return target.read_text(encoding="utf-8")

if __name__ == "__main__":
    app.run()
```

**四個重點**:

1. Tool 有清楚 docstring + args 說明
2. 驗證 / 限制參數(max_results 夾到 1-50)
3. **Security**:防 path traversal
4. 給 Claude 的 return 是 summary(path + excerpt),不是整份文件(那會爆 context)

---

## Resources

MCP server 可以 expose **可讀取的資源**。跟 tool 的差別:

- **Tool**:需要 arguments,有副作用或計算
- **Resource**:有 URI,GET 方式讀,typically 無副作用

```python
@app.resource("docs://catalog")
def doc_catalog() -> str:
    """List all available documents."""
    files = [str(p.relative_to(DOCS_ROOT)) for p in DOCS_ROOT.rglob("*.md")]
    return "\n".join(files)

@app.resource("docs://{path}")
def doc_content(path: str) -> str:
    """Read a specific doc."""
    # 同 read_doc 的邏輯
    ...
```

**使用端**:Client 列出 resources 給使用者選,選中後內容塞進 context。Claude Desktop 的 `@attach context` 功能用的就是 resource。

**多數 tool 也能當 resource 寫**。一般原則:

- 使用者會想主動「貼」這內容 → resource
- LLM 在 autonomous 流程中呼叫 → tool

---

## Prompts(prompt templates)

MCP server 可以 expose prompt 模板:

```python
@app.prompt()
def review_python(pr_content: str) -> str:
    """Prompt for reviewing a Python PR."""
    return f"""Review the following Python PR for:
- Style issues (PEP8)
- Common bugs (shadowing, mutable defaults)
- Security (injection, auth)

PR content:
{pr_content}

Output:
- Line-by-line comments
- Overall verdict: APPROVE / REQUEST_CHANGES
"""
```

Client 可以讓使用者從 prompt list 選擇,server 回傳完整的 prompt 內容塞進對話。

---

## 從 server 啟動 sampling(反向 call LLM)

Advanced:server 可以 request client 的 LLM 幫它做事。例:

```python
@app.tool()
async def summarize_doc(doc_path: str, ctx: mcp.Context) -> str:
    """Summarize a doc using the client's LLM."""
    content = read_doc(doc_path)
    # 讓 client 的 LLM 做 summarize
    result = await ctx.session.create_message(
        messages=[{"role": "user", "content": [{"type": "text", "text": f"Summarize: {content}"}]}],
        max_tokens=300,
    )
    return result.content[0].text
```

**小心使用**。這叫 sampling,server 主動呼叫 client 側 LLM。多數 client(包括 Claude Code)會要求 user 明確同意。

---

## Security 注意事項

寫 MCP server 給別人用(或自己跨系統用),至少做:

### 1. 驗證所有輸入

Claude 會傳什麼進來你不完全可控。**把 Claude 當未信任使用者對待**:

- Path → 防 traversal
- SQL → 用 parameterized,不要 string concat
- Shell command → **絕不 eval user-provided string**
- URL → validate scheme,限制 allowed domain

### 2. 限制能做什麼

範例 — 只允許讀某類檔:

```python
ALLOWED_EXTS = {".md", ".txt"}
if Path(path).suffix not in ALLOWED_EXTS:
    raise ValueError(f"Only {ALLOWED_EXTS} allowed")
```

### 3. 避免 destructive tool

不要寫 `delete_everything` 這種 tool,即使邏輯上需要。寧可:

- Tool 名叫 `mark_for_deletion`
- 真正刪除由人手動執行

原則:**LLM 有犯錯空間,工具設計要 defense in depth**。

### 4. Auth 和 rate limit

公開給網路的 server(SSE / Streamable HTTP):

```python
app = FastMCP("my-server", ...)
# 設定 auth middleware(FastMCP 新版有支援)
```

Rate limit 建議用 nginx / cloudflare 等 external layer,server 內再加一層。

---

## 測試 MCP server

### 手動 stdio 測試

```bash
python my_server.py
```

它會坐在那等 JSON-RPC on stdin。手動送:

```
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

回應:

```
{"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
```

### MCP Inspector

官方工具:

```bash
npx @modelcontextprotocol/inspector python my_server.py
```

打開瀏覽器,GUI 看 tools、resources、prompts,可以 invoke。

### 整合測試:真接 Claude Code

加進 settings,重啟,對話中試。這是真實環境測試。

---

## Deploying 一個遠端 MCP server

要讓 Anthropic API / 其他 LLM 服務遠端接:

```python
# 改成 HTTP transport
app.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

或 SSE:

```python
app.run(transport="sse", ...)
```

加 reverse proxy(nginx / caddy)、TLS、auth middleware,就是一個 production server。

**Docker-able**:

```dockerfile
FROM python:3.12-slim
COPY my_server.py .
RUN pip install mcp
CMD ["python", "my_server.py"]
```

---

## 打包成可分享的 MCP server

要讓社群使用:

1. Python package 化(`pyproject.toml`)
2. Entry point:`mcp-my-server = my_server:main`
3. 發到 PyPI
4. 寫 README 給配置範例

**Naming convention**:`mcp-server-<name>` 或 `mcp-<name>` 都常見。

---

## 典型的自寫 MCP server 場景

1. **公司內部 API 包裝**:把你家的 REST API 包成 MCP。
2. **資料庫 read-only 查詢**:給內部 LLM 查 BI DB 但禁止寫入。
3. **私有知識庫**:文件、wiki、內部 repo 的搜尋介面。
4. **特殊工具**:像「查 Kubernetes cluster 狀態」這種特定領域工具。

---

## 自我檢核

- [ ] FastMCP 和官方 SDK 的差別?什麼時候選哪個?
- [ ] Tool 的 docstring 為什麼重要?
- [ ] 為什麼要限制 return 大小?
- [ ] Security:三個寫 MCP server 必做的驗證。
- [ ] Resource 和 tool 的區別,各自適用?

→ [Ch 14 MCP 進階:resources / prompts / sampling / transport](./14-mcp-advanced.md)
