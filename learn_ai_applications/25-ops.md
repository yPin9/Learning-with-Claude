# Ch 25 — 成本、降級、失敗處理

> 目標:LLM app 的 ops 心法——成本控管、降級策略、失敗優雅處理、rate limit 應對、deploy pipeline。

## LLM app 的 ops 特殊性

傳統 web app 的 ops 顧慮:CPU、memory、network、DB。

LLM app 多一個:

- **Token cost**(最大頭)
- **API 供應商 uptime**(你依賴的 vendor)
- **Model 版本管理**
- **Rate limit**(比傳統 API 更容易撞)
- **Latency 分布**(p99 很爛)

---

## 成本管理

### 你的 cost 來源(回顧)

- LLM token(input + output + cache)
- Embedding(RAG 有的話)
- Tool 執行成本(外部 API、DB)
- Observability storage
- Compute / hosting

**LLM token 通常是 bulk,先管它**。

### 成本刪減順序

按 impact 排序:

**1. Prompt caching(最高 ROI)**

Ch 9 細講。system prompt 和 tool definitions cache 起來通常省 50%+。

**2. Model tier 分配**

- 分類、格式轉、簡單摘要 → Haiku
- 一般對話、RAG answer → Sonnet
- 複雜推理、agent → Opus

**3. Context 控管**

- Retrieval top 3 vs top 10
- Summarize 舊對話而不是全塞
- 不要把整個 codebase / KB 無腦灌

**4. Output 控制**

- `max_tokens` 設實際需要的上限(預設常太大)
- Prompt 要求簡短

**5. Batch 離線任務**

Ch 10 提過。非 real-time 的任務 batch API 半價。

**6. 快取 end-user 結果**

FAQ 類問題答案穩定 → app 層 cache(不是 prompt caching),key=query hash,skip LLM。

### Cost dashboard

要有:

```
Daily cost by:
  - feature (chat / analyze / summarize)
  - model
  - user tier (free / pro / enterprise)

Alerts:
  - daily total > 1.5x 30-day average
  - any user > $50/day
  - cache hit rate < expected threshold
```

### Budget gating

對 per-user cost 上限硬性檢查:

```python
from collections import defaultdict
from datetime import datetime, timedelta

user_daily_cost = defaultdict(lambda: {"date": None, "cost": 0})

def check_budget(user_id, daily_limit=1.0):
    today = datetime.utcnow().date()
    state = user_daily_cost[user_id]
    if state["date"] != today:
        state["date"] = today
        state["cost"] = 0
    if state["cost"] > daily_limit:
        raise BudgetExceeded(user_id, state["cost"])
    return state

def charge(user_id, cost):
    state = check_budget(user_id)
    state["cost"] += cost
```

每次 LLM call 後 charge。超標 degrade 或拒絕 service。

---

## 降級策略(Degradation)

系統有部分服務不可用時,不要整個掛。**Degrade gracefully**。

### 1. Model fallback

主 model overloaded → 切小 model:

```python
async def robust_call(messages, primary="claude-sonnet-4-6", fallback="claude-haiku-4-5"):
    for model in [primary, fallback]:
        try:
            return await client.messages.create(model=model, messages=messages, ...)
        except APIStatusError as e:
            if e.status_code in (429, 529) and model == primary:
                continue
            raise
    raise ServiceUnavailable()
```

Haiku 品質可能差一點,但有回應 > 整個掛。

### 2. Skip optional features

RAG 的 reranker 掛了?skip reranker 直接用 vector search 結果,品質降一點但能回應。

```python
try:
    ranked = rerank(query, candidates)
except ReRankerError:
    log.warning("Rerank skipped, using vector order")
    ranked = candidates
```

### 3. Cached response

用戶問過類似 query?回 cache(明確標示):

```python
result = cache.get(query_hash)
if result:
    return {**result, "cached": True}
```

### 4. Minimal answer

最後 fallback:回「系統忙碌,請稍後」——比錯答案好。

### 5. Circuit breaker

Vendor API 短時間多次失敗 → 暫停呼叫:

```python
from datetime import datetime, timedelta

class CircuitBreaker:
    def __init__(self, fail_threshold=5, reset_after=timedelta(minutes=1)):
        self.failures = 0
        self.opened_at = None
        self.fail_threshold = fail_threshold
        self.reset_after = reset_after

    def allow(self):
        if self.opened_at is None:
            return True
        if datetime.utcnow() - self.opened_at > self.reset_after:
            self.opened_at = None
            self.failures = 0
            return True
        return False

    def record_success(self):
        self.failures = 0

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.fail_threshold:
            self.opened_at = datetime.utcnow()
```

**原則**:fail fast,不要 5 個 request 都等 30 秒 timeout。

---

## Rate Limit 應對

Anthropic API(和別家)都有 rate limit:

- RPM(requests per minute)
- TPM(tokens per minute)
- 並發 concurrent request

看你的 tier 有不同 limit。

### 應對

**1. SDK 內建 retry**

預設有 exponential backoff。能幫多數 transient limit。

**2. Request queue**

高峰期 queue 起來,別直接 429:

```python
from asyncio import Semaphore

llm_semaphore = Semaphore(10)   # 並發 10

async def guarded_call(**kwargs):
    async with llm_semaphore:
        return await client.messages.create(**kwargs)
```

**3. User-level rate limit**

防止單 user 卡死 global quota(Ch 22 提過)。

**4. Upgrade tier**

真的不夠就提 tier(聯絡 Anthropic)或分散 key。

### 估算

設計時算:

- **Peak QPS × avg tokens per request = peak TPM**
- 對照你 tier 的 TPM upper bound
- 留 2x safety margin(尖峰時 double)

---

## 模型版本管理

### 問題

Anthropic 發新模型 → 你該 migrate 嗎?

### 建議流程

1. **Pin 到具體 version**(不要用 `claude-sonnet-latest`)
2. 新 model 出 → **跑 eval** 比較
3. 明顯變好 → 1% traffic 測試
4. Prod metrics 無 regression → 漸進 rollout
5. 舊 model pin 留用(fallback)直到新 model 穩定 N 週

### Claude 的模型 ID

```python
model = "claude-sonnet-4-6"        # 追隨 minor update
# vs
model = "claude-sonnet-4-6-20250319"   # pin 到具體 snapshot
```

生產建議:**pin 到 snapshot**,自己控更新時機。

### 舊模型 deprecation

Anthropic 會 deprecate 舊 model。要:

- 訂閱 deprecation 通知
- 每個 model 有 migration plan
- 不要讓 critical path 依賴單一 model ID

---

## Deployment pipeline

### 最小 pipeline

1. **Local dev**:改 prompt / code
2. **Unit tests**:LLM-independent 的邏輯
3. **Assertion eval**:small golden set,每 PR 跑
4. **LLM eval**:每 PR 或每 day
5. **Staging deploy**:少量真實流量
6. **Production gradual rollout**:10% → 50% → 100%
7. **Monitor**:dashboards、alerts

### 特殊處:prompt/skill 改動

Prompt 改動是 **code change**,要走 PR + review:

- Commit 到 git
- PR 描述改動意圖
- 自動跑 eval
- Reviewer 看 eval 結果才 approve

**不要口頭改 prompt**。改完沒紀錄、沒 review、沒 eval = 災難。

---

## 失敗模式的 pattern

常見失敗,你要有對應 response:

| 失敗 | 症狀 | 處理 |
|---|---|---|
| API 429 | Rate limit | Backoff + retry |
| API 529 | Overloaded | Fallback model / 延遲 |
| API 500 | Vendor bug | Retry,persistent 則 alert |
| Tool timeout | 外部 API 慢 | 返回 partial result + note |
| JSON parse fail | LLM 輸出格式壞 | Re-prompt / fallback prompt |
| Schema validation fail | LLM 參數錯 | 塞 error + retry |
| Max iterations | Agent 迷路 | Escalate / 回 partial |
| Context overflow | 對話太長 | Summarize / compact |
| Injection attempt | User 可疑輸入 | Log + deny / reduce trust |
| Policy violation (output) | LLM 輸出違規 | Replace + alert |

**每個都要 proactively 處理**,不是等 bug 回報才 fix。

---

## Latency 優化

### 量測

Trace(Ch 21)各 span 時間。常見 bottleneck:

- **Retrieval**:embedding call + vector search
- **LLM TTFT**:model warmup、長 context
- **Tool exec**:外部 API
- **Final LLM gen**:output tokens × per-token time

### 優化

**1. Parallelize**

Retrieval 和 LLM 可同時跑的步驟 → async。

**2. Streaming**

Ch 10 已講。TTFT 感知延遲大降。

**3. Prompt caching**

Ch 9 已講。TTFT 也降。

**4. 小 model for small tasks**

分類用 Haiku,比 Sonnet 快。

**5. Prefetch / speculate**

User 打字時就預跑 retrieval,送出時已有結果。

**6. Reduce output tokens**

`max_tokens` 設合理。讓 prompt 要求簡短。

### SLA

Set 期望:

- **p50 < 2s**:OK 對話
- **p95 < 5s**:OK 大多 LLM app
- **p99 < 15s**:OK 複雜 task

超過這些要改架構,不是小優化。

---

## 災難 playbook

發生時做什麼的 checklist:

### Vendor API 大面積掛

1. 切到 fallback model provider(若有多廠)
2. 啟動 cached responses(降品質但有服務)
3. 通知 user(status page)
4. 等 vendor 恢復
5. Post-mortem:要不要改 multi-provider?

### 成本爆炸

1. 找 top cost contributors(user、feature、prompt)
2. 臨時禁用 abusive user / feature
3. Review 是否 prompt 被 escalate(eg. attacker 塞大 prompt)
4. 確認 caching 有效
5. Per-user budget gate 啟動

### Prod model output 出包

1. Immediate:切到 known-good previous prompt/model
2. Collect broken cases(變 golden)
3. Fix + eval
4. Deploy fix
5. Post-mortem:eval 為何沒抓到?

---

## 自建 vs 買

- **Observability**:PoC 自建,規模後考慮買(Langfuse/Braintrust)
- **Eval**:核心自建(你的 golden 最懂你),工具層可用庫
- **RAG infra**:核心自建(chunking / retrieval policy),vector DB 多半買(Qdrant self-host / Pinecone SaaS)
- **Model serving**:用 API,別自 host 除非合規需要
- **Prompt management**:git 存,不用買 prompt platform
- **Agent framework**:用 Anthropic 的 Agent SDK(就是為 Claude 設計),不要硬套 LangChain 之類

**原則**:LLM app 還在快速演化,不要 over-invest 在特定 framework。**把複雜度留在你自己的 code**,可以 refactor。

---

## Ops 成熟度 checklist

- [ ] Prompt caching 覆蓋 >80% 的重複 prompt
- [ ] Cost dashboard + alert
- [ ] Per-user budget gate
- [ ] Model fallback 機制
- [ ] Circuit breaker
- [ ] Request queue / rate limit per user
- [ ] Eval 自動化跟 deployment pipeline 整合
- [ ] Model version pinned
- [ ] Full observability(Ch 21)
- [ ] Security / guardrails(Ch 22)
- [ ] Disaster runbook
- [ ] Cost per feature / user / request 可觀測

打勾 <8 個你還在玩 PoC。>10 算是 production ready。

---

## 自我檢核

- [ ] 成本刪減的優先順序是什麼?
- [ ] Circuit breaker 解決什麼問題?
- [ ] 為什麼 model ID 要 pin 到 snapshot?
- [ ] Prompt 改動為什麼要走 PR review?
- [ ] 你的 LLM app 災難 playbook 該有哪些 scenarios?

→ [Final Project — 上線一個真實 AI 應用](./final-project-ship-it.md)

前面章節都走完了。接下來是 practice(A–D)和 final project——這是把上面知識變成你的東西的關鍵步驟。

→ [Practice A — Prompting 實戰](./practice-a-prompting.md)
→ [Practice B — 寫一個 MCP server](./practice-b-mcp-server.md)
→ [Practice C — 寫一個 agent](./practice-c-agent.md)
→ [Practice D — RAG + eval pipeline](./practice-d-rag-eval.md)
