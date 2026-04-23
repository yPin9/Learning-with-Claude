# Ch 18 — 長時間 agent 的工程挑戰

> 目標:超過 10 步 / 幾小時 / 跨 session 的 agent 有特殊問題。Context 管理、failure recovery、cost、debugging——這章講這些。

## 什麼算「長時間」agent

- 超過 **50 個工具步驟**
- 超過 **30 分鐘**
- 跨 **多 session** 持續(stateful)
- 並行處理 **多個獨立 request**(如 service-style agent)

短 agent(< 10 步)的問題少,長 agent 的問題**非線性增加**。

---

## 挑戰 1:Context Overflow

Agent 每步加料到 context:tool result、thinking、assistant 回應。到第 N 步 context 滿了。

### 自然會累積的東西

- Tool output(尤其 Read 大檔、Grep 結果、API response)
- Conversation history
- System prompt(固定開銷)
- Tool definitions(固定開銷)

看起來像:

```
Step 1: system + tools (30k)
Step 5: + some tool results (40k)
Step 20: + accumulated (120k)
Step 50: ... boom (200k hit)
```

### 對策

**對策 1:Tool result 壓縮**

原始 tool output 100k,但對當下只需要關鍵幾行。Agent 的 runtime(Claude Code / SDK)**自動** summarize 舊 tool output。

**對策 2:Compaction**

Claude Code 和 Agent SDK 內建 compaction——context 快滿時,自動 summarize 前半對話,保留最近 N 步詳細。

在 Claude Code 手動觸發:`/compact`。

**對策 3:Subagent 隔離**

重 context 的子任務交給 subagent。主 agent 只看 summary。Ch 17 已講。

**對策 4:寫 file + 讀 file,不塞 context**

```
Agent: "我 search 了,結果 500 行。我寫到 search_results.md,需要時再 grep 它。"
```

用 filesystem 當 scratch space,比塞進 context 省得多。

**對策 5:1M context beta**

Claude Sonnet 4.6 有 1M context beta。給你 5x space,代價是超過 200k 後的 token 價格 2x。**值得用於極長 agent 任務**。

---

## 挑戰 2:錯誤累積

每步 95% 對 → 10 步後 0.95^10 ≈ 60% 對 → 20 步後 36% 對。

### 錯誤類型

- **Tool invocation 錯**:參數錯、tool 不存在、超時
- **Tool 執行失敗**:API 掛、DB lock、net error
- **推理錯**:從 partial 資訊錯誤推論
- **格式錯**:Claude 輸出的 JSON / code 不符合下游需求

### 防禦策略

**策略 1:每步驗證**

Tool 結果不對 → 塞回 `is_error: true` + 告訴 Claude 怎麼回事。Claude 會嘗試 recover。

```python
if not validate_output(result):
    return {
        "is_error": True,
        "content": "Output failed schema validation. Expected format: ... Got: ..."
    }
```

**策略 2:Retry with different approach**

Tool 失敗時,system prompt 教 Claude「若失敗 3 次,換方法」。

**策略 3:Checkpoint + resume**

Agent 跑到一半壞,不要全重跑。Checkpoint 關鍵狀態到 disk,失敗後從那繼續。

Claude Agent SDK 有 checkpoint 機制(細節看版本),或手動:

```python
def save_checkpoint(step, state):
    Path("checkpoints").mkdir(exist_ok=True)
    with open(f"checkpoints/step_{step}.json", "w") as f:
        json.dump(state, f)

def load_latest_checkpoint():
    files = sorted(Path("checkpoints").glob("step_*.json"))
    if not files: return None
    return json.loads(files[-1].read_text())
```

**策略 4:Human-in-the-loop**

重要 / 不可逆的 step 前停下來 ask 使用者:

- "About to delete 1000 records. Confirm? [y/N]"

Claude Code 的 permission system 幫你做這個——把高風險工具設 `require_permission`。

---

## 挑戰 3:Cost 管理

長 agent 每步 API call,cost 堆積。

### Cost 來源

1. **每次 LLM call**(token * price)
2. **Tool invocation** 本身不收錢,但工具可能有 cost(外部 API、cloud service)
3. **重複的 system prompt**:沒 caching 的話每步都全額

### 壓 cost 的優化

**1. Prompt caching(Ch 9 必讀)**

System + tool definitions 是 repeat heavy,絕對要 cache。**這常是 50%+ 成本節省**。

**2. 小模型 fallback**

Claude Agent SDK 可以用 `model` option 選模型:

- Orchestrator 或關鍵推理:sonnet / opus
- 機械執行:haiku

**3. Tool result 小 token**

Tool 回傳別無腦 dump,寫 summary。

**4. 限 max_iterations / max_turns**

超過就停,避免 runaway cost。Agent SDK 有對應參數。

**5. Monitor usage**

每個 agent session log `resp.usage`,累積。每天看 top 10 最貴 session。

```python
session_tokens = 0
session_cost = 0

async for msg in client.receive_messages():
    if msg.type == "assistant" and msg.usage:
        in_t = msg.usage.input_tokens
        out_t = msg.usage.output_tokens
        # 粗算
        cost = in_t * 0.000003 + out_t * 0.000015
        session_tokens += in_t + out_t
        session_cost += cost
```

**6. Batch 離線任務**

非 real-time 的 agent 流程用 batch API 半價(Ch 10)。

---

## 挑戰 4:Debugging 長 agent

一個跑 100 步的 agent,在第 78 步出錯,怎麼找?

### 預防性:結構化 log 每步

```python
import logging
logging.basicConfig(level=logging.INFO)

async def run_agent(...):
    for step in range(max_steps):
        logger.info(f"Step {step}: sending request")
        response = await call_api(...)
        logger.info(f"Step {step}: stop_reason={response.stop_reason}")
        logger.info(f"Step {step}: tokens in={response.usage.input_tokens} out={response.usage.output_tokens}")

        for block in response.content:
            if block.type == "tool_use":
                logger.info(f"Step {step}: tool={block.name} input={block.input}")
            elif block.type == "text":
                logger.info(f"Step {step}: text={block.text[:200]}")
```

Log 每步的:

- Step number
- Stop reason
- Token usage
- Tool calls + inputs
- Text output preview

### 用 Observability 平台

Ch 21 會細講,但 teaser:

- **Langfuse / Braintrust / Arize**:LLM-native 的 tracing
- **OpenTelemetry** 自己串:generic 但靈活

Agent 每步當 span,整個 session 當 trace。UI 看整個流程。

### Replay 機制

紀錄每步的 input,失敗後可以 replay(送同 prompt、看結果)。對 debugging intermittent 問題有用。

---

## 挑戰 5:跨 session 記憶

Agent 遇到的東西跨 session 要記住:

### Session-level memory

Claude Code 的 memory 機制(auto-memory):Claude 自己判斷該記什麼,寫到 markdown file。**適合個人偏好、事實**。

### Persistent knowledge

RAG / DB store。Ch 19 會細講。**適合大量知識、可檢索**。

### Tool history

跑什麼成功、什麼失敗,記在 DB。下次同 situation 參考。**適合 agent 自我 improve**。

---

## 挑戰 6:並行 request 的隔離

Agent 是 service,同時處理 100 個 user 的 request。要隔離:

### User-level session

每個 user 自己的 session_id。Memory / context 不互串。

### Rate limit per user

防止單用戶吃爆 API quota:

```python
from collections import defaultdict
from time import time

user_tokens = defaultdict(list)

def check_rate_limit(user_id, quota=100000, window=3600):
    now = time()
    user_tokens[user_id] = [(t, n) for t, n in user_tokens[user_id] if now - t < window]
    used = sum(n for _, n in user_tokens[user_id])
    if used > quota:
        raise TooManyRequests()
```

### Sandbox

Agent 會寫檔、跑 bash——**一定要 sandbox**。Docker / firecracker / Pod per session。**生產 agent service 沒 sandbox 就是災難**。

---

## 挑戰 7:無限迴圈

Agent 卡住:「我試 X → 失敗 → 我試 X 另一變化 → 失敗 → ...」

### 偵測

- Max iterations 限制(hard stop)
- **Repetition detection**:最近 3 次 tool call 相同 → 強制 break
- **Progress check**:每 N 步問「你真的在進步嗎?」強制 agent self-assess

### Prompt 級對策

在 system prompt 寫:

```
If you attempt something 3 times and it fails, STOP and report the situation.
Don't repeatedly retry with tiny variations.
```

---

## 挑戰 8:HITL(Human-in-the-Loop)的設計

長 agent 該什麼時候 break 問人?

### 原則

**可逆操作** → autonomous。**不可逆、高影響** → 確認。

| 操作 | HITL? |
|---|---|
| 讀檔、grep、search | 不需要 |
| Format / lint | 不需要 |
| 寫 code 到 working tree | 可選 |
| Commit | 可選 |
| Push / merge / deploy | **需要** |
| Delete / drop | **需要** |
| 外部 API(付費、發訊息) | **需要** |

### 實作

Claude Code 的 permission system 幫你 handle。Agent SDK 也有 hook 可以 intercept。

```python
async def before_destructive(input_data, tool_id, ctx):
    tool = input_data["tool_name"]
    if tool in DESTRUCTIVE_TOOLS:
        # 發 notification 等人批准
        approved = await wait_for_approval(...)
        if not approved:
            return {"hookSpecificOutput": {"permissionDecision": "deny"}}
    return {}
```

---

## 一張「長 agent 健康度」checklist

寫長 agent 前 / 跑後 review:

- [ ] Prompt caching 有開(system / tools)
- [ ] Max iterations 有設
- [ ] Tool result 大小有限制(或 summarize)
- [ ] Checkpointing 有實作(至少關鍵步驟)
- [ ] Log 記錄每步(結構化)
- [ ] HITL gate 在高風險處
- [ ] Rate limit per user
- [ ] Repetition detection
- [ ] 跑在 sandbox(若接 user / 寫檔)
- [ ] Cost monitoring(per session、per user)
- [ ] Observability(trace 每個 agent run)

---

## 案例:如何讓 agent 跑一整天

現實有 agent 系統跑 24 小時(例:watchdog agent 監控 system)。關鍵設計:

1. **Sleep cycles**:agent 不是連續跑,是 cron-like(每 10 分鐘 wake up check 一次)
2. **Small context per wake**:只加載當下相關資料
3. **Shared knowledge base**:memory / DB 跨 wake 持續
4. **Circuit breakers**:連續 N 次失敗 → pause、notify human
5. **Cost cap**:每天 budget,超過就停

**不要想「一個 long-running process」**。想「每 N 分鐘短 agent run + shared memory」。

---

## 自我檢核

- [ ] Context overflow 的五種對策?
- [ ] 錯誤累積:agent 走 20 步每步 95% 對,總正確率?
- [ ] Checkpoint 在哪種錯誤時救你?
- [ ] HITL 該在什麼操作前設?
- [ ] 為什麼「長 agent」通常該設計成 cron-like 而不是 daemon?

→ [Practice C — 寫一個 agent](./practice-c-agent.md)(先略過)

→ [Ch 19 RAG:向量 / hybrid / rerank](./19-rag.md)
