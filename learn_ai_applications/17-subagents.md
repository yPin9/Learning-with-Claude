# Ch 17 — Subagents 與多 agent 協作

> 目標:知道什麼情境該拆成多 agent,怎麼設計 subagent 的 system prompt 和工具範圍,避開「agent 爆炸」反 pattern。

## Subagent 的定位

Subagent = **由主 agent 啟動、獨立 context 的專門 agent**。

跟 tool 比:

- Tool 是一次函數呼叫,input → output
- Subagent 是 spawn 另一個有自己 LLM call loop 的 agent

跟主 agent 並列跑的 peer:

- 概念上可以,但 Claude Agent SDK / Claude Code 預設是樹狀(主 → 子),不是平行 peer

---

## 什麼時候該寫 subagent

### ✓ 該用 subagent

**1. 子任務 context 大、主 agent 不需要看細節**

例如:「幫我 refactor 整個 auth 模組」。這任務中的 read file、grep、分析——主 agent 只需要最終 summary,中間過程吃 context 浪費。

**2. 需要獨立的 tool / permission set**

例如:migrator 只需要 write 權限到 migration 資料夾,不該有其他寫入權限。

**3. 專門化能改善品質**

例如:security-reviewer 有專注的 prompt「只看安全」,比「通用 code reviewer」更到位。

**4. 需要並行**

開 3 個 subagent 各審不同模組,比一個 agent 線性做快 3 倍。

### ✗ 不該用 subagent

**1. 小任務**

「讀這個檔」不值得 spawn subagent——context 開銷、延遲、API call 成本都多。

**2. 需要主 agent 看中間過程**

Debugging 類任務——主 agent 要判斷下一步,需要看每個 tool output。

**3. 狀態高度耦合**

主 agent 和子任務共享很多 state,拆 subagent 要同步,反而複雜。

---

## 定義 Subagent(Claude Code / SDK 通用)

`.claude/agents/<name>.md`:

```markdown
---
name: security-reviewer
description: Specialized security reviewer. Use for reviewing code changes for security vulnerabilities, auth issues, and input validation.
tools: Read, Glob, Grep
model: opus
---

You are a security-focused code reviewer.

Scope:
- SQL injection, XSS, CSRF
- Auth / authorization logic
- Input validation (especially user-facing)
- Unsafe function use (eval, exec, shell=True, pickle.loads)
- Secret leakage (hardcoded keys, tokens in logs)

Do NOT comment on:
- Style / formatting
- Performance
- General code quality

Output format:
- [CRITICAL] for vulnerabilities
- [HIGH] for suspicious patterns
- [MEDIUM] for concerns worth checking
- [OK] if clean

Be precise. Quote exact lines. Suggest exact fixes.
```

**四個 frontmatter 欄位**:

- `name`:唯一
- `description`:主 agent 用它判斷何時 delegate(重要!)
- `tools`:限制工具集(預設繼承父,寫了就是限制)
- `model`:用哪個 model。重要推理用 `opus`,便宜 / 快用 `haiku`

---

## Subagent 的 description 寫法

跟 skill 一樣,description 決定「何時被 delegate」。

### 壞

```yaml
description: Code reviewer
```

### 好

```yaml
description: Specialized security reviewer. Use this agent when reviewing code for security vulnerabilities, when the user mentions "security review" / "audit", or when changes touch authentication, crypto, or user input handling.
```

**觸發訊號要具體**。主 agent 會自動決定 delegate,但判斷力取決於描述。

---

## 主 agent 如何 delegate

**Claude Code 自動**:主 agent 讀使用者 prompt,判斷「這個 subagent 的 description 符合此任務 → delegate」。

**顯式呼叫**:

```
> Use the security-reviewer agent to audit src/auth/
```

Claude 會 spawn 對應 subagent。

**SDK 中顯式呼叫**:

```python
from claude_agent_sdk import Agent  # (API shape 視版本)

# 在主 agent 流程中顯式 call
result = await Agent.invoke(
    subagent_type="security-reviewer",
    prompt="Review src/auth/"
)
```

---

## Subagent 的 context 隔離

關鍵理解:**subagent 有自己的 context window**。主 agent 塞了什麼進去,subagent 不自動知道。

### 傳遞 context 的方式

**1. Prompt 帶入**

```
Main agent → spawn security-reviewer with prompt:
  "Review the changes in src/auth/login.py. Focus on OAuth flow.
   Context: this is part of PR #123 which adds Google SSO."
```

把重要 context 寫進 prompt。

**2. Shared filesystem**

Subagent 能讀同 repo 的檔案,就能存取狀態。

**3. 回傳結構化結果**

Subagent 結束時回 summary,主 agent 看到這個。不要期待主 agent 看到 subagent 內部 tool calls。

---

## 並行 subagent

Claude(主 agent)可以一次 spawn 多個 subagent,它們平行跑。

**訊號**:主 agent 的 `assistant` message 含多個 `tool_use` block,每個對應一個 subagent 呼叫。

### 使用場景

**例子 1:多模組並行 review**

```
> Review all three modules in parallel: src/auth/, src/billing/, src/api/
```

Main agent 開 3 個 reviewer subagent 平行跑。**3x 速度**。

**例子 2:Research + Implementation 分頭進行**

```
> Research how to implement OAuth, and simultaneously sketch the DB schema.
```

一個 subagent 做 research(web search + reading),另一個 schema(read code + draft)。最後主 agent 合。

### 並行的限制

- API rate limit 會撞到
- 資源爭用(同時編輯同檔會衝突)
- 結果合併需要主 agent 額外邏輯

建議:**真正獨立的任務才並行**,有依賴的還是 sequential。

---

## Context 壓力的解法

主 agent 跟多個 subagent 互動,context 會快速擠爆。對策:

### 策略 1:Subagent 回傳精簡 summary

Subagent 內部跑 50 次 tool call,但只回 main agent 一段 200 字結論。

Subagent system prompt:

```
When finished, respond with ONLY:
- A 2-3 sentence summary
- A structured list of findings

Don't include intermediate reasoning or tool outputs in your response.
```

### 策略 2:結果寫 file,main agent 讀

Subagent 把詳細 output 寫進 `./tmp/reports/security.md`,只告訴 main agent「report saved」。需要時 main agent 再讀。

### 策略 3:多 agent 協作用專門格式

例如 subagent 強制輸出 JSON:

```
Output format: JSON object with {severity, issues, verdict}.
No prose.
```

Main agent 直接 parse JSON。

---

## Agent 爆炸反 pattern

常見誤區:**把每件事都做成 agent**。

### 反例

```
main-agent → spawn planner-agent
                → spawn researcher-agent
                      → spawn web-searcher-agent
                            → spawn url-validator-agent
                                  → spawn DNS-checker-agent (!!)
```

每層 agent 增加:
- Context 開銷
- API call 成本(每層都要 model call)
- Latency(串聯更慢)
- 錯誤傳播路徑(任一 agent 失敗整個鏈掛)

### 更好的設計

- **3 層以內**:main + subagent + (optional) sub-sub
- **Subagent 做真正獨立的任務**,不是「另一層 Claude 呼叫」
- **單純工具用 tool**,不要包成 agent

---

## 多 agent 協調模式(Anthropic 的 Building Effective Agents)

幾種常見模式:

### 1. Orchestrator-Workers

Main agent 當 orchestrator,分派任務給 worker subagents。每個 worker 回 result,orchestrator 整合。

```
Orchestrator (決策 + 分派)
    ├── Worker 1 (讀 DB)
    ├── Worker 2 (call API)
    └── Worker 3 (生成 report)
```

### 2. Evaluator-Optimizer

一個 agent 生成,另一個 evaluate,結果 feedback 給第一個改。Loop 到滿意。

```
Generator  ←──┐
    ↓         │
  output      │
    ↓         │
Evaluator ────┘ (approved? yes/no+feedback)
```

面試題自動生成、code 優化、文案改寫都是這模式。

### 3. Routing

First agent 分類 / 選路線,不同路線交給不同 subagent。

```
Router (classify input)
  ├── billing? → Billing agent
  ├── technical? → Technical agent
  └── general? → General agent
```

客服系統常用。

### 4. Chain

線性 pipeline,每步驟是一個 agent / LLM call。**不是真的 agent**,但概念類似。

```
Input → Agent A → 結果 → Agent B → 結果 → Agent C → Output
```

### 哪個模式合適

| 場景 | 模式 |
|---|---|
| 任務有獨立子任務 | Orchestrator-Workers |
| 生成質量需要迭代 | Evaluator-Optimizer |
| 不同輸入要不同處理 | Routing |
| 線性流程 | Chain(其實用 workflow 就好) |

**心法**:**多 agent 前先問「能不能用 workflow?」**。能就不要多 agent。

---

## Subagent vs Tool 的判斷矩陣

| | Tool | Subagent |
|---|---|---|
| 邏輯複雜度 | 簡單函數 | 需要 LLM 推理 |
| Context 需求 | 無(或少) | 可能大 |
| 輸出格式 | 結構化 | 自由 / 半結構 |
| Cost | 一次工具執行 | 至少一次 LLM call |
| 重用性 | 高 | 中 |

**原則**:**能 tool 就 tool**,不能(需要判斷、推理、多步工具)才 subagent。

---

## 組合 subagent + skills + MCP

一個真實 setup 可能是:

```
Main agent
  │
  ├── [skill] pr-review-checklist    (告訴主 agent 怎麼 review)
  ├── [mcp] github-server             (抓 PR diff)
  ├── [mcp] jira-server               (查關聯 issue)
  ├── [subagent] security-reviewer    (專業安全審查)
  ├── [subagent] test-writer          (自動補 test case)
  └── [subagent] docs-updater         (同步更新 docs)
```

這樣一個指令「review PR #456」會自動:

1. 用 github MCP 抓 diff
2. 讀 skill 的 checklist
3. 開 3 個 subagent 並行(security / tests / docs)
4. 主 agent 整合結果,post 回 GitHub

這就是「真 agent 產品」的複雜度。

---

## 自我檢核

- [ ] Subagent 和 tool 怎麼選?
- [ ] Subagent 的 `description` 決定什麼?
- [ ] 主 agent 怎麼把 context 傳給 subagent?
- [ ] Agent 爆炸反 pattern 的訊號?
- [ ] Orchestrator-Workers 和 Routing 模式的差別?

→ [Ch 18 長時間 agent 的工程挑戰](./18-long-running-agents.md)
