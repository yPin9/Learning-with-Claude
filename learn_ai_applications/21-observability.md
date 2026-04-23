# Ch 21 — Observability:traces / cost / latency

> 目標:把 LLM app 做成「看得見內部」。不是只有普通 APM,是 LLM-native observability。

## LLM app 的觀測盲點

傳統 web service 看:

- QPS、error rate、p99 latency
- CPU、memory、disk

這些仍然需要,但 LLM app 還有:

- **Token usage**(per request、per user、per feature)
- **Cost**(也是 per 這些維度)
- **LLM 延遲**(vs tool 延遲 vs 總延遲)
- **Tool call 成功率**
- **Prompt 內容**(要能回看當時送什麼)
- **Agent trajectory**(跑了幾步、走什麼路線)
- **Eval scores**(上一章)

沒這些看不見就是**盲開** production。

---

## Trace 的概念

**Trace** = 一次 user request 的完整記錄,包含所有 sub-operations。

單次 chat message 可能是:

```
Trace: "Handle user message #12345"
├── Span: Query rewrite (LLM call, 200ms)
├── Span: Retrieval (vector search, 80ms)
│   └── Sub-span: Embedding (100ms)
├── Span: Rerank (50ms)
├── Span: LLM generate (1.5s)
│   ├── Tool call: get_order (300ms)
│   └── Tool call: get_product (200ms)
└── Total: 2.3s
```

每個 span 記:

- Name
- Start / end time
- Parent span
- Attributes(model、tokens、cost、error 等)

這是一個 **tree**,反映整個 request 的 flow。

---

## 用什麼工具

三層選擇:

### 1. 自己 log + grep(PoC)

```python
import logging, json, time

def trace_call(name, **attrs):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = fn(*args, **kwargs)
                logging.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "name": name,
                    "duration_ms": (time.time() - start) * 1000,
                    **attrs,
                    "status": "ok",
                }))
                return result
            except Exception as e:
                logging.error(json.dumps({"name": name, "status": "error", "error": str(e)}))
                raise
        return wrapper
    return decorator

@trace_call("llm_call", model="sonnet-4-6")
def call_llm(prompt):
    ...
```

能看基本結構,但沒 tree 關係、沒聚合 UI。

### 2. OpenTelemetry + 任何後端

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("retrieval") as span:
    span.set_attribute("query", user_query)
    span.set_attribute("k", k)
    results = vector_search(user_query, k)
    span.set_attribute("results_count", len(results))
```

後端可接:Jaeger、Honeycomb、Datadog、Grafana Tempo。

**優點**:通用協議,其他 service 已經用的話省事。
**缺點**:沒特化 LLM semantics,欄位設計要自己想。

### 3. LLM-native platform

- **Langfuse**(open source,可自架)
- **Braintrust**
- **Arize Phoenix**
- **Helicone**(HTTP proxy 架構)

**優點**:原生支援 token、cost、model、prompt、tool call 欄位。UI 能看 prompt content、搜尋 trajectory、cost 分析。
**缺點**:vendor lock-in,要花錢(部分)。

**實務建議**:

- 初期 / PoC → 自己 log 或 Langfuse 自架
- 規模後 → 評估 SaaS(Braintrust / Langfuse cloud)

---

## 用 Langfuse 的範例

```python
from langfuse import Langfuse
from langfuse.decorators import observe

langfuse = Langfuse()

@observe()
def answer_query(query):
    rewrote = rewrite_query(query)
    docs = retrieve(rewrote)
    answer = generate(query, docs)
    return answer

@observe(as_type="generation")
def rewrite_query(query):
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": f"Rewrite: {query}"}]
    )
    return resp.content[0].text

@observe()
def retrieve(query):
    return vector_search(query, k=5)

@observe(as_type="generation")
def generate(query, docs):
    resp = client.messages.create(...)
    return resp.content[0].text
```

裝 decorator 就自動 trace。`as_type="generation"` 告訴 Langfuse 這是 LLM call(自動抓 token usage、model)。

UI 能看到:

- 整個 trace tree
- 每個 LLM call 的 prompt / completion
- Token usage、cost
- 時間軸

---

## 關鍵 metrics dashboard

至少要有這幾個 dashboard:

### 1. Cost tracking

```
Total $/day, split by:
- Model(sonnet / opus / haiku)
- Feature(chat / summarize / classify)
- User(top 10 cost contributors)
```

發現:某 feature 吃 70% cost?是它活該還是 bug?

### 2. Latency

```
p50 / p95 / p99 of:
- End-to-end
- LLM call only
- Retrieval
- Each tool
```

發現:p99 飆到 30 秒,LLM call 佔多少?也許是 retrieval 慢。

### 3. Token usage

```
- Avg input tokens per request
- Avg output tokens per request
- Cache hit rate(重要!)
```

發現:你 caching 以為開了,cache hit rate 只有 15% → 有地方 prefix 不穩定。

### 4. Error rate

```
- 4xx / 5xx from Anthropic API
- Tool call failure rate
- JSON parse failure
- Max iterations hit(agent 沒完成)
```

### 5. Quality signals

- 如果前端有 thumbs up/down → 聚合
- 退回 / retry rate
- User 修正 rate

---

## 一個 trace 要記什麼

每個 LLM call span 建議記:

```yaml
span:
  name: "llm.chat"
  attributes:
    # 模型
    llm.model: "claude-sonnet-4-6"
    llm.temperature: 0.3
    # Tokens & cost
    llm.input_tokens: 12543
    llm.output_tokens: 423
    llm.cache_read_tokens: 10000
    llm.cache_write_tokens: 0
    llm.cost_usd: 0.0042
    # Timing
    duration_ms: 1832
    ttft_ms: 650
    # 內容(or hash)
    llm.prompt_hash: "sha256:..."   # 或存 prompt preview
    llm.completion_preview: "Based on the context..."
    # 功能 tag
    feature: "chat.customer_support"
    user_id_hashed: "abc123..."
```

### Prompt 存或不存?

- **存全文**:debug 爽,但敏感資料可能有合規問題(GDPR、個資)
- **存 hash**:無 leak,但 debug 只能看行為不能看內容
- **存 redacted 版本**:去 PII,留結構

**建議**:

- 開發 / staging 存全文
- Production 存 redacted 版 + full prompt 只保留 N 天
- 敏感 use case(醫療、金融) → 全 hash 或不存

---

## Correlate to user

使用者回報「剛才答案錯了」,你要能找到那次 request。

```python
trace.set_attribute("user_id", hash_user(user_email))
trace.set_attribute("session_id", session_id)
trace.set_attribute("request_id", request_id)
```

User 告訴你 request_id(從前端顯示):

```
UI: "Something's wrong? Give us this code: ABC-123-XYZ"
↓
Copy this code when contacting support
```

Support tool 能用這 code 查 trace。大部分問題 debug 靠這。

---

## Alerting

Dashboard 只被動看。要 alert:

### 典型 alert 規則

- **Cost spike**:per hour cost > 2x last week same hour
- **Error rate**:5xx > 1% over 5 min
- **Latency**:p99 > 10s over 5 min
- **Token explosion**:avg input tokens > 50k(某處把整 repo 塞進 context?)
- **Cache miss surge**:cache hit rate < 50%

### 先 alert,再 investigate

工程師用 trace tool dig 到根本原因。

---

## 特殊場景:Agent trace

Agent 跑 50 步 → trace 有 50 個 span。UI 看不完。

### 對策

- **Summarize trace**:把 50 步壓成階段性 milestone
- **標注失敗 span**:UI 紅色標出錯誤 / retry
- **Trajectory timeline**:看 agent 的「path」走向

Langfuse / Braintrust 的 agent view 有這類 UI。自己寫的要花力氣做。

---

## Production debugging workflow

User 抱怨「agent 答案怪」:

1. 從 support ticket 拿 request_id
2. 查 trace → 看整個 flow
3. 展開 retrieval span → 看 retrieved 什麼
4. 展開 LLM call → 看實際 prompt 和 completion
5. 定位問題:retrieval 錯 / prompt 引導錯 / model hallucinate
6. 把 case 加進 eval golden set
7. 修改後再跑 eval 驗證

**完整閉環:prod → trace → eval → fix → prod**。

---

## Privacy & Compliance

Observability 收集 LLM input / output = 可能收集 PII。

- **Redact PII** 在寫 trace 前
- **Retention policy**:不要永遠存
- **Access control**:誰能看 prompt content?
- **Compliance**(GDPR、HIPAA、SOC2):trace 本身要 audit

**不要被 observability 把你告了**。產品設計時就要想這些。

---

## 自製 observability 最小版

如果不想裝平台,最小可行:

```python
# logger.py
import json, time, uuid, logging
from contextvars import ContextVar

trace_id_var = ContextVar("trace_id", default=None)

def new_trace():
    tid = str(uuid.uuid4())
    trace_id_var.set(tid)
    return tid

def log_event(name, **attrs):
    logging.info(json.dumps({
        "trace_id": trace_id_var.get(),
        "ts": time.time(),
        "name": name,
        **attrs,
    }))

# 使用
trace_id = new_trace()
log_event("llm.call", model="sonnet-4-6", tokens_in=100, tokens_out=50, duration_ms=800)
```

輸出 JSON log,BigQuery / ClickHouse 查。

不優雅但有用。比什麼都沒有好 100 倍。

---

## 自我檢核

- [ ] LLM app observability 跟一般 web APM 差在哪?
- [ ] Trace 結構是什麼樣(tree / flat)?
- [ ] 至少五個你該監控的 metric?
- [ ] Prompt 全文存還是 hash?考量什麼?
- [ ] Support 流程:user 回報問題 → 你怎麼找到 trace?

→ [Ch 22 Safety 與 guardrails](./22-safety.md)
