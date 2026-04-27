# Practice B — 寫一個 MCP server

> 目標:從零寫出一個能在 Claude Code / Desktop 中實際使用的 MCP server,覆蓋 tools、resources、error handling、security、deploy。

## 題目:Task Tracker MCP Server

你要寫一個 MCP server 管理個人 TODO list。Claude 能:

- 列 TODO
- 加 / 改 / 刪 / 標記完成
- 按標籤、優先度篩選
- 匯出 markdown

**資料儲存**:本地 JSON 檔(簡單,避免 DB 複雜度)。

---

## Spec

### Required Tools

1. **`list_tasks`**
   - Args: `status` (optional: "pending"/"done"/"all", default "pending"), `tag` (optional), `priority` (optional: 1-5)
   - Returns: list of tasks
   - Error:無

2. **`create_task`**
   - Args: `title` (required), `description` (optional), `tags` (optional list), `priority` (optional 1-5, default 3), `due_date` (optional ISO8601)
   - Returns: created task with generated id
   - Error: title 空 / 格式錯

3. **`update_task`**
   - Args: `id` (required), 其他欄位(optional,只改指定的)
   - Returns: updated task
   - Error: id 不存在

4. **`mark_done`**
   - Args: `id` (required)
   - Returns: updated task
   - Error: id 不存在 / 已 done

5. **`delete_task`**
   - Args: `id` (required)
   - Returns: `{"deleted": true}`
   - Error: id 不存在

### Required Resources

- **`tasks://all`** — all tasks as markdown
- **`tasks://pending`** — pending tasks as markdown
- **`tasks://tag/{tag}`** — tasks with specific tag

### Nice to have(選做)

- **`search_tasks`** tool:by keyword
- **`export_markdown`** tool:完整 markdown 匯出
- Task hierarchy(subtask)
- Sync 到 Notion / GitHub issue(進階)

---

## Step by Step

### Step 1:設 project

```bash
mkdir mcp-task-tracker
cd mcp-task-tracker
python -m venv venv
source venv/bin/activate  # 或 Windows 對應
pip install mcp pydantic
```

`pyproject.toml`:

```toml
[project]
name = "mcp-task-tracker"
version = "0.1.0"
dependencies = ["mcp>=1.0", "pydantic>=2"]

[project.scripts]
mcp-task-tracker = "task_tracker:main"
```

### Step 2:寫 data model

```python
# task_tracker/models.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    description: str = ""
    tags: list[str] = []
    priority: int = 3   # 1-5
    status: str = "pending"   # pending | done
    due_date: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
```

### Step 3:寫 storage

```python
# task_tracker/storage.py
from pathlib import Path
import json
from .models import Task

DATA_DIR = Path.home() / ".task-tracker"
DATA_FILE = DATA_DIR / "tasks.json"

def load() -> list[Task]:
    if not DATA_FILE.exists():
        return []
    data = json.loads(DATA_FILE.read_text())
    return [Task(**t) for t in data]

def save(tasks: list[Task]):
    DATA_DIR.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps([t.model_dump() for t in tasks], indent=2))
```

### Step 4:寫 server

```python
# task_tracker/__init__.py
import mcp
from mcp.server.fastmcp import FastMCP
from .models import Task
from . import storage

app = FastMCP("task-tracker")

@app.tool()
def list_tasks(status: str = "pending", tag: str = "", priority: int = 0) -> list[dict]:
    """List tasks with optional filters.

    Args:
        status: "pending", "done", or "all"
        tag: Filter by tag (empty = no filter)
        priority: 1-5 (0 = no filter)
    """
    tasks = storage.load()
    if status != "all":
        tasks = [t for t in tasks if t.status == status]
    if tag:
        tasks = [t for t in tasks if tag in t.tags]
    if priority > 0:
        tasks = [t for t in tasks if t.priority == priority]
    return [t.model_dump() for t in tasks]

@app.tool()
def create_task(title: str, description: str = "", tags: list[str] = None,
                priority: int = 3, due_date: str = "") -> dict:
    """Create a new task.

    Args:
        title: Task title (required, non-empty)
        description: Longer description
        tags: List of tag strings
        priority: 1 (high) to 5 (low)
        due_date: ISO8601 date string (optional)
    """
    if not title or not title.strip():
        raise ValueError("Title must be non-empty")
    if not (1 <= priority <= 5):
        raise ValueError("Priority must be 1-5")
    task = Task(
        title=title.strip(),
        description=description,
        tags=tags or [],
        priority=priority,
        due_date=due_date or None,
    )
    tasks = storage.load()
    tasks.append(task)
    storage.save(tasks)
    return task.model_dump()

@app.tool()
def mark_done(id: str) -> dict:
    """Mark a task as done."""
    tasks = storage.load()
    for t in tasks:
        if t.id == id:
            if t.status == "done":
                raise ValueError(f"Task {id} already done")
            t.status = "done"
            from datetime import datetime
            t.updated_at = datetime.utcnow().isoformat()
            storage.save(tasks)
            return t.model_dump()
    raise ValueError(f"Task {id} not found")

# ... update_task, delete_task 類似

@app.resource("tasks://pending")
def pending_as_markdown() -> str:
    """All pending tasks as markdown."""
    tasks = storage.load()
    pending = [t for t in tasks if t.status == "pending"]
    lines = ["# Pending Tasks\n"]
    for t in sorted(pending, key=lambda x: x.priority):
        lines.append(f"- [{t.id}] [P{t.priority}] {t.title}")
        if t.due_date:
            lines[-1] += f" (due {t.due_date})"
    return "\n".join(lines)

def main():
    app.run()

if __name__ == "__main__":
    main()
```

### Step 5:測試

```bash
pip install -e .
mcp-task-tracker   # 會 stdin/stdout 等 JSON-RPC
```

**最小手測**(另一個 terminal):

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | mcp-task-tracker
```

應該看到 tools JSON。

### Step 6:在 Claude Code 接上

`~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "task-tracker": {
      "command": "mcp-task-tracker"
    }
  }
}
```

重啟 Claude Code:

```
/mcp    # 確認 task-tracker connected

> Add a task: finish MCP practice, priority 1, tag "learning"
> List my pending tasks
> Mark task abc123 as done
```

---

## 驗收 checklist

- [ ] 5 個 required tools 全實作且有 docstring
- [ ] 3 個 resources 可讀
- [ ] Error handling:title 空 / id 不存在 都回合理 error
- [ ] Security:path 沒 injection(這題沒外部輸入當 path 比較安全,但仍可檢查)
- [ ] 在 Claude Code 實測 10 個指令,全部 work
- [ ] README 含安裝 + 配置說明

---

## 加分挑戰

### 1. 加 search_tasks

Full-text 搜 title + description:

```python
@app.tool()
def search_tasks(query: str, status: str = "all") -> list[dict]:
    """Search tasks by keyword in title or description."""
    ...
```

### 2. Remote deployment

改用 Streamable HTTP transport,deploy 到某 server,從另一台電腦用 API 的 MCP Connector 接。

### 3. Sync to GitHub Issues

再開 subcommand,把 task sync 到 GitHub repo 的 issues。

### 4. 寫 test

Pytest + fixture:

```python
def test_create_task():
    task = create_task(title="Test", priority=1)
    assert task["title"] == "Test"
    assert task["priority"] == 1
```

### 5. Publish 到 PyPI

打包成真的 package,社群可以 `pip install mcp-task-tracker` 用。

---

## 反思問題

做完後問自己:

1. 你的 tool description 寫得 Claude 理解正確嗎?有沒有需要改?
2. Error 的訊息 Claude 看完能不能 recover(re-prompt)?
3. 如果改成 multi-user(每 user 自己的 tasks),哪裡要改?
4. 這個 server 跟 `@memory` MCP server 差異在哪?
5. 如果要支援「subtask」(task 底下有 task),data model 怎麼改?

---

## 這 Practice 訓練到的能力

- MCP 協議實作
- 型別驅動的 schema 設計
- Tool + Resource 的分工
- Error 設計
- Deploy 和整合 Claude Code

**把這 repo 留著**,當未來寫 MCP server 的 template。
