# Practice C — 寫一個 agent

> 目標:用 Claude Agent SDK 寫一個能 ship 的 agent。涵蓋 tool use loop、hooks、subagents、error handling、cost control。

## 題目:自動化 Research Agent

做一個 CLI agent:**給一個研究題目 → agent 搜尋網路、閱讀幾個來源、產出帶引用的摘要報告**。

```bash
$ research-agent "How does Model Context Protocol work?"
[Agent researches...]
[Report written to report-20260423.md]
```

---

## Spec

### 輸入

- 一個 research query(自然語言)

### 輸出

- 一份 markdown report:
  - Executive summary(3 句)
  - 主要發現(5 bullet points)
  - 詳細 section(每個發現一段)
  - 引用列表(至少 5 個 source URL)
  - 元資料(query、time、agent steps、cost)

### 行為

- 用 WebSearch + WebFetch 做 research
- 讀至少 5 個不同 source
- Cross-reference(多個 source 印證)
- 失敗某 source 不影響整體
- Budget: $2 max, 50 tool calls max
- 有 progress output(不是 silent)

---

## Step by Step

### Step 0:Setup

```bash
mkdir research-agent
cd research-agent
python -m venv venv
source venv/bin/activate
pip install claude-agent-sdk
```

### Step 1:最小可跑版本

```python
# research_agent.py
import asyncio
import sys
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    topic = " ".join(sys.argv[1:])
    if not topic:
        print("Usage: python research_agent.py <topic>")
        sys.exit(1)

    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        allowed_tools=["WebSearch", "WebFetch", "Write"],
        permission_mode="acceptEdits",
        system_prompt="""You are a research assistant.

Given a topic:
1. Use WebSearch to find 5+ authoritative sources
2. Use WebFetch to read them in detail
3. Write a markdown report to research-report.md with:
   - Executive summary (3 sentences)
   - 5 key findings
   - Detailed discussion
   - Citations (URL list)
4. Tell me you're done.

Be concise. Don't hallucinate — only state claims backed by fetched content.
""",
    )

    async for msg in query(prompt=f"Research this topic: {topic}", options=options):
        # 基本 progress 輸出
        if msg.get("type") == "assistant":
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    print(block["text"][:200])
                elif block.get("type") == "tool_use":
                    print(f"[TOOL] {block['name']}")

if __name__ == "__main__":
    asyncio.run(main())
```

跑跑看:

```bash
python research_agent.py "How does Kubernetes networking work?"
```

會看到 Claude 搜尋、fetch、寫 report。

### Step 2:加 budget control

```python
class BudgetTracker:
    def __init__(self, max_cost=2.0, max_calls=50):
        self.cost = 0
        self.calls = 0
        self.max_cost = max_cost
        self.max_calls = max_calls

    def add(self, in_tokens, out_tokens):
        # 粗算 Sonnet 的 cost
        self.cost += in_tokens * 3 / 1_000_000 + out_tokens * 15 / 1_000_000

    def add_call(self):
        self.calls += 1

    def exceeded(self):
        return self.cost >= self.max_cost or self.calls >= self.max_calls


budget = BudgetTracker()

async for msg in query(prompt=..., options=options):
    if msg.get("type") == "assistant":
        usage = msg.get("usage", {})
        budget.add(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                budget.add_call()
        print(f"[budget] ${budget.cost:.3f}, calls: {budget.calls}")
        if budget.exceeded():
            print("[BUDGET EXCEEDED] stopping")
            break
```

### Step 3:加 hooks 做安全

```python
from claude_agent_sdk import HookMatcher

async def validate_fetch(input_data, tool_id, ctx):
    url = input_data.get("tool_input", {}).get("url", "")
    # 只允許 http/https,block 可疑
    if not url.startswith(("http://", "https://")):
        return {"hookSpecificOutput": {"permissionDecision": "deny",
                "permissionDecisionReason": "Only http(s) URLs allowed"}}
    # Block local / internal
    if "localhost" in url or "127.0.0.1" in url or ".internal" in url:
        return {"hookSpecificOutput": {"permissionDecision": "deny",
                "permissionDecisionReason": "Internal URLs not allowed"}}
    return {}

options = ClaudeAgentOptions(
    ...,
    hooks={
        "PreToolUse": [HookMatcher(matcher="WebFetch", hooks=[validate_fetch])]
    }
)
```

### Step 4:加 iteration limit

SDK 可能有 `max_turns` 或類似 option。手動 guard:

```python
turn = 0
async for msg in query(prompt=..., options=options):
    if msg.get("type") == "assistant":
        turn += 1
        if turn > 30:
            print("[TURN LIMIT] stopping")
            break
```

### Step 5:報告 final stats

```python
print(f"\n=== Research complete ===")
print(f"Total cost: ${budget.cost:.3f}")
print(f"Total tool calls: {budget.calls}")
print(f"Turns: {turn}")
```

Open `research-report.md` 看結果。

---

## 加分挑戰

### 1. 用 subagent 做 fact-check

定義 `.claude/agents/fact-checker.md`:

```markdown
---
name: fact-checker
description: Verify specific claims against sources. Use when main research agent has draft claims that need verification.
tools: WebSearch, WebFetch
---

Given a claim + source URL, verify whether the source supports the claim.
Output: CONFIRMED / DISPUTED / UNCLEAR, with direct quote from source.
```

主 agent 在寫 report 前先 delegate 幾個關鍵 claim 給 fact-checker。

### 2. Cross-source consensus

主 agent 寫 report 時,對每個 finding 標「confidence」(多少 sources 支持)。低 confidence 的用 "preliminary" wording。

### 3. 進度 UI

用 rich / textual 做漂亮 progress bar:

```python
from rich.console import Console
from rich.live import Live

console = Console()
with Live(...) as live:
    async for msg in query(...):
        live.update(...)
```

### 4. 題目分類

Research agent 一開始先讓 Claude 判斷題目類別(technical / historical / biography / news),不同類別用不同 research strategy:

- Technical → 偏向官方文件、技術 blog
- News → 優先近期 sources
- 等等

### 5. 結果快取

同 query 24hr 內 cache 結果,避免重跑。

---

## 驗收 checklist

- [ ] 給一個 topic,能跑出 markdown report
- [ ] Report 含至少 5 個 citations
- [ ] 失敗 URL(dead link)不 crash,agent 繼續
- [ ] Budget 超過會停
- [ ] Progress 輸出可看
- [ ] Hooks 禁止 local / internal URL
- [ ] Report 裡 claim 跟 citation 對得上(抽樣 3 個驗證)

---

## 反思問題

1. Agent 花最多 turn 在哪些 tool?怎麼能減少?
2. WebFetch 失敗率多高?什麼原因(CORS、timeout、404)?
3. Report 質量對 topic type 敏感嗎?哪類 topic 寫得好,哪類差?
4. 如果要做成 web service(user 透過 API 提交 topic),哪些要改?
5. 這 agent 的「非 Claude dependency」是什麼?換 LLM 要改多少?

---

## 這 Practice 訓練到的能力

- Agent SDK 實戰
- Tool allowlist 設計
- Budget control pattern
- Hooks 安全層
- Cost observability
- 長流程 graceful handling

把這份 code 當你未來寫 agent 產品的起手式。
