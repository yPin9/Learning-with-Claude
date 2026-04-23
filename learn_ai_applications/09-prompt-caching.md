# Ch 9 — Prompt Caching:省錢也省延遲

> 目標:把 prompt caching 用對。理解 cache breakpoint、TTL、cost 結構,以及什麼情況該用、不該用。

## 為什麼 prompt caching 存在

多數 LLM app 的 request 長這樣:

```
[超長 system prompt: 10k tokens] + [tool definitions: 3k] + [少量使用者輸入: 50 tokens]
```

前面 13k tokens **每次都一樣**,但你每次都付錢 + 每次都重算。這是浪費。

**Prompt caching**:告訴 Anthropic「這段內容之後可能重複,幫我快取」。下次相同開頭的 request 來,快取讀取:

- **Cache read: 原價的 10%**(省 90%)
- **Cache write: 原價的 1.25 倍**(第一次寫貴 25%)
- **Latency**:TTFT 可能降 50–80%

對 system prompt / tool definitions 重的 app,這是**一個旋鈕省 80% 成本**。

---

## 標記 cache

在 `system`、`tools`、`messages` 的 content block 加 `cache_control`:

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are a helpful assistant."   # 小段,不快取
        },
        {
            "type": "text",
            "text": LARGE_KNOWLEDGE_BASE,           # 大段
            "cache_control": {"type": "ephemeral"}  # 到這裡為止 cache
        }
    ],
    messages=[
        {"role": "user", "content": "Q1"}
    ]
)
```

**`cache_control` 標在 cache 範圍的**末尾**那個 block**。從 request 開頭到這個 block(含)之前的所有內容,構成一個 cache entry。

### Cache 結構:從頭 match 到 breakpoint

Cache key = request 前綴,直到你標 cache_control 的位置。

```
system text 1 + system text 2 [cache_control]
↓
cache key = hash(system text 1 + system text 2)
```

下次同 prefix 的 request 進來就 hit cache,讀取只要 10% 價錢。

### 最小 cacheable 長度

**1024 tokens**(Sonnet / Opus)或 **2048 tokens**(Haiku)。太短的片段不會被 cache。

---

## TTL:快取存活多久

預設是 **5 分鐘**(ephemeral)。每次 hit 延長 5 分鐘。5 分鐘沒人用 → 失效。

**1 小時版本**(beta):

```python
"cache_control": {"type": "ephemeral", "ttl": "1h"}
```

1 小時版寫入貴 100%(2 倍),但 hit rate 高的情況省更多。

### 怎麼選

- **短對話**:預設 5 分鐘夠
- **多用戶但活躍度低**:看 qps
- **對話間隔會超過 5 分鐘,且 prefix 很大**:1h
- **一次性 request**:不要 cache(寫入更貴就虧了)

---

## 多個 cache breakpoint

你可以設**最多 4 個 cache_control**,形成「巢狀快取」:

```python
system=[
    {"type": "text", "text": SYSTEM_BASE, "cache_control": {"type": "ephemeral"}},        # 1
],
tools=[
    {...}, {...}, {..., "cache_control": {"type": "ephemeral"}}                           # 2
],
messages=[
    {"role": "user", "content": [
        {"type": "text", "text": HISTORY_BLOB, "cache_control": {"type": "ephemeral"}},   # 3
        {"type": "text", "text": CURRENT_QUERY}
    ]}
]
```

**從 request 開頭計**:

- Cache 1 範圍:system base
- Cache 2 範圍:system base + tools
- Cache 3 範圍:system base + tools + history

不同輪次下:
- 第一次 call:全部寫入
- 第二次 call 只換 query:hit cache 3(含 history),很省
- 第三次 call 換了 history 但 tools 沒變:hit cache 2,寫 cache 3 新版

**設計原則**:變動頻率低的放前面,變動頻率高的放後面。

---

## 驗證 cache 有沒有命中

Response 的 `usage` 有:

```python
resp.usage
# MessageUsage(
#     input_tokens=50,                       # 「新」的 input tokens
#     output_tokens=80,
#     cache_creation_input_tokens=10000,     # 這次寫入快取的 tokens
#     cache_read_input_tokens=0,             # 這次從快取讀的 tokens
# )
```

第二次 call(相同 prefix):

```python
# input_tokens=50,
# cache_creation_input_tokens=0,
# cache_read_input_tokens=10000,     # ← hit 了!
```

**沒 hit 時檢查**:

- Prefix 是不是完全一樣(一個字符不同就 miss)
- 是不是超過 5 分鐘沒人用
- 是不是 token 太少(< 1024 不 cache)

---

## 什麼情況該用 cache

### ✓ 該用

- System prompt 有**大型 knowledge / guideline**(1k tokens+)
- Tool definitions 多且穩定
- RAG 場景:retrieved context 在同 session 內多次使用
- 多輪對話:前面歷史不變,只換最後一個 user turn
- Few-shot examples 固定

### ✗ 不該用

- 內容 < 1024 tokens
- 一次性呼叫(絕對 miss cache)
- 內容每次都變(隨機化 system prompt)
- Hot path 的內容你會頻繁修改 → 每次都 re-write,貴

---

## 什麼狀態「被認為相同」

**嚴格字串比對**。差一個 tab / 空白 / 大小寫 → miss。

常見失誤:
- Prompt 裡塞時間戳(`今天是 2026-04-23`)
- 動態組 prompt 時 join 用不同分隔符
- 加一個 user id 在 prefix 裡

**對策**:把變動內容**放到 cache breakpoint 之後**。

---

## Cache 的成本算式

假設一段 10,000 tokens 的 system prompt,每分鐘有 20 個 request 用它:

**無 cache**:
- 每次 input 10,000 tokens,20 request/min × 60 min = 1200 requests/hour
- 每小時 12,000,000 input tokens
- 以 Sonnet 的 input 價 $3/M 計:每小時 $36

**有 cache(5 分鐘 TTL)**:
- 每 5 分鐘 1 次 cache write:12 次/小時
- 12 次 × 10,000 tokens × 1.25x = 150,000 tokens at write price = $0.45
- 其餘 1188 次 read:1188 × 10,000 × 0.1x = 1,188,000 tokens at read price
- 1,188,000 tokens at $0.3/M:$0.36
- 每小時:**$0.81**

**節省 97.8%**。Numbers approximate,但這個量級是對的。

---

## Cache 不是 LRU

Cache 是**對這個 request 的 prefix hash 存儲**,TTL 到就清。不是「高頻命中就常駐」。同 prefix 沒人用 5 分鐘,清掉。

### Account / Org 層級

Cache 在 organization 級別共享——同 org 多台機器用同 prefix 也共享。不是「每個 API key 獨立」。

---

## 實戰:快取版的 chat 後端

```python
SYSTEM_BLOCKS = [
    {
        "type": "text",
        "text": "You are a support agent for Acme Corp."
    },
    {
        "type": "text",
        "text": COMPANY_KNOWLEDGE,    # 20k tokens 的公司內部知識
        "cache_control": {"type": "ephemeral"}
    }
]

TOOLS_WITH_CACHE = [
    {...},
    {...},
    {..., "cache_control": {"type": "ephemeral"}}    # tools 結尾
]

def chat(messages):
    return client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_BLOCKS,
        tools=TOOLS_WITH_CACHE,
        messages=messages,
    )
```

只要 qps > 1/(5分鐘) = 0.003 qps,cache 就開始回本。大型 app 的 system prompt 幾乎總是該 cache。

---

## Chat conversation 的進階 caching

在多輪對話中,**history 也可以 cache**。每加一個 turn 後標 cache_control:

```python
messages = [
    {"role": "user", "content": "Q1"},
    {"role": "assistant", "content": "A1"},
    {"role": "user", "content": "Q2"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "A2", "cache_control": {"type": "ephemeral"}}
    ]},
    {"role": "user", "content": "Q3"}    # 當前這輪
]
```

第三輪 call 時 cache 了到 A2 為止的全部歷史。下一輪 Q4 時,cache 再延伸到 A3 為止。

**Claude Code / Agent SDK 內建這個模式**,你手寫 agent 要記得加。

---

## 陷阱

### 陷阱 1:內容動態注入

```python
# BAD
system = [
    {"type": "text", "text": f"Today is {datetime.now()}. You are ..."}
]
```

每秒都變,cache miss 率 100%。改成:

```python
# GOOD
system = [
    {"type": "text", "text": "You are ..."},
    {"type": "text", "text": STATIC_KNOWLEDGE, "cache_control": {...}}
]
# 時間戳放到第一個 user message 裡,不 cache
```

### 陷阱 2:cache_control 放錯位置

```python
# BAD - 這只 cache 了 SHORT 這段,沒包含 HUGE
system=[
    {"type": "text", "text": SHORT, "cache_control": {...}},
    {"type": "text", "text": HUGE}
]

# GOOD
system=[
    {"type": "text", "text": SHORT},
    {"type": "text", "text": HUGE, "cache_control": {...}}
]
```

### 陷阱 3:以為「小於 1024」也會 cache

Silent fail——不會報錯,就是沒 cache。監控 `cache_creation_input_tokens` 為 0 時調查。

### 陷阱 4:模型切換

Model 是 cache key 一部分。`sonnet-4-5` 的 cache 和 `sonnet-4-6` 的 cache 完全獨立。migrate 模型時預期要 re-warm。

---

## 什麼時候不該用 caching

- Request 一次性 / 極低頻
- Prompt 每次都大幅變動
- 都是小 request(< 1024 tokens 的 prefix)
- 你在做 eval,想隔離變數(cache 會影響 latency 測量)

---

## 自我檢核

- [ ] Cache write 和 cache read 的價格差多少?
- [ ] 最小 cacheable 長度是多少?短於這數字會怎樣?
- [ ] 兩個 TTL 選項的差別?什麼時候用 1h 值得?
- [ ] 怎麼驗證這次 request 有沒有 hit cache?
- [ ] Cache miss 最常見的三個原因?

→ [Ch 10 Extended Thinking / Streaming / Batch / Files](./10-advanced-api.md)
