# Ch 23 — Agent 架構模式

> 目標:知道幾個被驗證的 agent 架構,以及什麼場景用什麼模式。不是硬背定義,是建立「看問題選架構」的直覺。

## 架構的光譜

把「LLM 系統」放在一條光譜上:

```
純 prompt 呼叫      workflow       agent
(一次 LLM call) → (固定 pipeline) → (動態決策)
         確定性                                不確定性
         便宜、快                              貴、慢
         好控制                                難控制
```

**agent 不是「更好」,是更靈活**。代價是確定性、成本、可觀測性下降。

**Anthropic 的建議**(Building Effective Agents):**能用 workflow 就別用 agent**。90% 場景 workflow 夠。

---

## 先搞懂:Workflow vs Agent

### Workflow = 固定 pipeline

```
Input → Step 1 (LLM) → Step 2 (tool) → Step 3 (LLM) → Output
```

步驟**寫死**。每次走同樣的路,只是資料不同。

### Agent = 動態決策

```
Input → LLM 決定 next action → 執行 → LLM 看結果決定 next → ... → Output
```

每步 LLM 看狀況**自己決定**下一步。路徑每次可能不同。

### 怎麼選

| 問題 | 選 |
|---|---|
| 步驟固定、已知 | Workflow |
| 工具選擇依任務 | Agent |
| 有明確成功標準 | 兩者皆可 |
| 步驟數 < 3 | Workflow |
| 需要探索 | Agent |
| 預算緊 | Workflow |
| 長期 review / debug 重要 | Workflow |

---

## Workflow 的六種模式(Anthropic 整理)

### 1. Prompt Chaining

單純串接,每步一個 LLM call:

```
Query → summarize → translate → format → Output
```

用途:複雜任務拆成 focused sub-tasks,每步品質更好。

```python
def chain(query):
    summary = llm("Summarize: " + retrieve(query))
    translated = llm("Translate to English: " + summary)
    formatted = llm("Format as bullet points: " + translated)
    return formatted
```

### 2. Routing

分類 → 不同下游處理:

```
Query → Classify → {billing path | tech path | general path}
```

```python
def route(query):
    category = classify(query)   # LLM or classifier
    if category == "billing":
        return handle_billing(query)
    elif category == "tech":
        return handle_tech(query)
    else:
        return handle_general(query)
```

**好處**:每條路徑專門化,prompt 更短更準。

### 3. Parallelization

**Sectioning**:把 input 切成獨立部分並行處理。

```python
async def summarize_long_doc(doc):
    sections = split_sections(doc)
    summaries = await asyncio.gather(*[
        asyncio.to_thread(llm, f"Summarize: {s}") for s in sections
    ])
    final = llm("Combine these: " + "\n".join(summaries))
    return final
```

**Voting**:同 task 跑多次,多數決或 consensus。

```python
def answer_with_vote(query):
    answers = [llm(query, temperature=0.7) for _ in range(3)]
    return most_common(answers)
```

用於:需要 robustness 時,或有明確「對錯」的 task(數學、分類)。

### 4. Orchestrator-Workers

一個 orchestrator LLM 分派工作給多個 workers。**跟 Multi-Agent 很像,但 orchestrator 不「自主」**,是 workflow 中的一步。

```python
def ochestrator_workers(task):
    # Orchestrator 分解任務
    subtasks = llm(f"Break down this task: {task}. Output JSON list.")
    # Workers 平行做
    results = parallel([llm_worker(st) for st in subtasks])
    # 組合
    return llm(f"Combine results: {results}")
```

### 5. Evaluator-Optimizer

迭代改進:

```
Generator → Output → Evaluator → if not good: feedback → Generator
                              → if good: done
```

```python
def iterate(task, max_iter=5):
    output = generator(task)
    for _ in range(max_iter):
        verdict, feedback = evaluator(task, output)
        if verdict == "good":
            return output
        output = generator(task, prev=output, feedback=feedback)
    return output
```

適合:寫作、code、設計——**有明確 quality 標準但難一次寫對**。

### 6. Agent(純動態決策)

就是 Ch 8–18 講的 tool-use agent loop:

```python
while not done:
    action = llm.decide(state)
    state = execute(action)
    done = llm.check_done(state)
```

LLM 完全控制流程。靈活但 hardest to control。

---

## 混合:Workflow + Agent

**最常見的真實架構**:workflow 主導 + agent 做特定步驟。

例:

```
Query → Classify (workflow step)
      → If complex: use agent
      → Otherwise: direct LLM call
```

不要以為「用 agent 就全都 agent」。**agent 可以是你 pipeline 的一個 block**。

---

## ReAct Pattern

2022 paper 提的基礎 agent 模式,仍是主流:

```
Thought: I need to find out X.
Action: search("X")
Observation: {result}
Thought: I have X. Next I need Y.
Action: ...
```

**Reasoning + Acting 交替**。現代 LLM tool use 就是 ReAct 的封裝——模型輸出 thought + tool_use,執行後把 result 塞回。

你不用手寫 ReAct,tool use 就內建了。但**理解它的原理**有助於 debug agent 行為:

- Claude 如果沒 think 就 act → prompt 缺少 thinking block
- Claude 重複 action → observation 沒變化或 feedback 不清楚

---

## Plan-Execute Pattern

Agent 先**全盤規劃**,再執行:

```
Step 1: Plan = llm("Plan how to do: {task}. Output list of steps.")
Step 2: for each step in plan:
    execute(step)
```

vs ReAct(邊做邊想):Plan-Execute 先想完再做。

**優缺**:

| | ReAct | Plan-Execute |
|---|---|---|
| 適應變化 | 好 | 差(plan 會過時) |
| 可預測 | 低 | 高 |
| Debug | 難 | 相對易 |
| 長任務 | 容易迷路 | 有大綱不迷路 |

**混合**:先 plan,執行時仍允許調整(每 N 步重新 plan)。

### Plan Mode in Claude Code

Claude Code 的 plan mode 就是強制 Plan-Execute:先給你 plan,你同意才執行。Ch 5 講過。

---

## Multi-Agent 架構

多個 agent 合作:

### Supervisor-Workers

一個 supervisor agent 分配、監督 worker agents:

```
Supervisor agent
    ├── Worker: Researcher
    ├── Worker: Writer
    └── Worker: Fact-checker
```

### Debate

多 agent 辯論同一問題,最後合議:

```
Q → Agent A 提案 → Agent B 駁 → Agent A 反駁 → ... → Consensus
```

用途:複雜判斷、需要多視角的題。**成本高,用在高價值場景**。

### 注意

Multi-agent 的坑(Ch 17 講過):

- 成本倍增
- Context 傳遞複雜
- 失敗模式疊加

**不要為了用 multi-agent 而用**。Claude 自己往往夠強,不需要多個分身。

---

## State Machine

傳統 software pattern,在 agent 也適用:

```python
states = ["init", "gather_info", "propose", "refine", "done"]
current = "init"
state_machine = {
    "init": ("gather_info", gather_initial_info),
    "gather_info": ("propose" if info_complete else "gather_info", ...),
    "propose": ("refine" if needs_review else "done", ...),
    ...
}
```

LLM 在每個 state 做特定任務 + 決定下一個 state。

**優**:可預測、easy debug、可恢復。
**劣**:寫起來 verbose,不夠靈活。

適合:**有明確 phases** 的任務(e.g., 交易流程、診斷流程)。

---

## Memory 架構

Agent 的「記憶」分層:

### Short-term

當前 session 的 context window。自動 garbage collect(壓縮、截斷)。

### Medium-term

一個 session 的 summary,跨 session 傳遞。儲存:markdown、JSON 檔。

### Long-term

大量 knowledge:RAG、graph DB、structured DB。

### Procedural(程序記憶)

「做這類事用這套方法」。用 skills / playbooks 實現。

### 設計原則

- **清楚分層**,不要全塞一個 bucket
- **Retrieve 時按需**,不要全灌 context
- **更新時注意 consistency**(舊記憶過時怎麼辦)

---

## 系統化選擇:一張決策樹

```
你有明確固定流程?
  ├── Yes → Workflow
  │    ├── 步驟獨立 → Parallelization
  │    ├── 要分類 → Routing
  │    ├── 要迭代 → Evaluator-Optimizer
  │    └── 一般 → Chaining
  └── No → 需要動態決策?
       ├── Yes → Agent
       │   ├── 任務長 + 有明確 stages → Plan-Execute
       │   ├── 短 + 探索性 → ReAct (pure tool use)
       │   └── 多領域獨立子任務 → Multi-Agent
       └── No → 單 LLM call 就好(不要 over-engineer)
```

---

## 常見反 pattern

### 反 pattern 1:All-agent

「用 agent 就能解決一切」。過度複雜,debug 地獄,成本爆炸。

### 反 pattern 2:Agent 當 workflow 用

寫了 agent 但每次都走同樣路徑——**這代表該 workflow**,不該 agent。LLM 被浪費在「決定」明顯答案。

### 反 pattern 3:No evaluation

選了架構但沒 eval 驗證比別的好。**架構選擇靠 eval 對比**,不是靠直覺。

### 反 pattern 4:Over-memory

給 agent 「記住一切」的 memory → context 爆炸、retrieval 差、cost 高。**memory 要設計**,不是全存。

### 反 pattern 5:太多 subagent

每個子任務都 spawn subagent → 樹狀爆炸 → 超級貴。Subagent 應該是**大任務**,不是小 function。

---

## Case study:支援 ticket 處理

需求:user 發支援 ticket,自動 triage、回覆或 escalate。

### Architecture 選擇

Version 1:Workflow-first

```
Ticket → Classify category (LLM classifier)
       → If FAQ-able: retrieve FAQ + LLM answer
       → If account issue: fetch data + LLM answer
       → Else: escalate to human
```

**為什麼 workflow**:高頻常見 case,固定路徑夠,cost low, latency low,可 A/B 每個 step。

Version 2:Agent for edge cases

```
Workflow handles 80%
Edge case (unusual, multi-step) → spawn agent
```

Agent 用於低頻複雜場景。成本高但每月 occurrence 少,可接受。

**這是真實 production 常見樣貌**:80% workflow + 20% agent。不是全 agent。

---

## Case study:Claude Code 自己

Claude Code 本身就是一個 agent 產品。架構:

- **主 agent loop**:ReAct-style tool use(Read / Edit / Bash...)
- **Subagents**:用於 plan mode、專門任務
- **Skills**:procedural memory
- **Hooks**:lifecycle events
- **Memory**:CLAUDE.md + auto-memory
- **Plan mode**:一種 Plan-Execute 變體(user-in-the-loop)

**為什麼不是純 agent**:因為「寫 code」的任務極廣,純 agent 迷路率高。Plan mode + skills + hooks 加上「確定性」框架。

把 Claude Code 當 agent 架構的**教科書範例**研究,你能學到很多。

---

## 總結:架構選擇的原則

1. **從小往大長**:先 single LLM call → workflow → agent。一步一步加複雜度。
2. **架構跟 eval 一起選**。選 A 但 eval 差就換 B。
3. **Production ≠ demo**:demo 愛炫 agent,production 愛 workflow。
4. **可觀測 > 聰明**:能 debug 的架構永遠贏。
5. **成本要算**:架構決策直接決定每月帳單。

---

## 自我檢核

- [ ] Workflow 和 agent 的本質區別?
- [ ] Anthropic 的六種 workflow pattern 各自適用?
- [ ] ReAct 和 Plan-Execute 的差別?各自適合?
- [ ] Multi-agent 的三個常見陷阱?
- [ ] 為什麼「全 agent」不是好架構選擇?

→ [Ch 24 真實產品拆解](./24-product-teardowns.md)
