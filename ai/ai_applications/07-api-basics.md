# Ch 7 — Messages API 與 SDK

> 目標:會呼叫 Anthropic Messages API,理解 messages 結構、system prompt、基本參數。能用 SDK 寫出一個最小的對話程式。

## 進 builder 路線的起點

前面幾章你當 Claude Code 的 user。從這章開始,你寫的是**呼叫 Claude 的 backend**——別人進你的產品,後端呼叫 Claude。

## Messages API 的核心

**只有一個 endpoint 要記**:

```
POST https://api.anthropic.com/v1/messages
```

所有的 chat、tool use、thinking、caching 都在這個 endpoint 上。Anthropic 沒有 chat / completions 兩分的歷史負擔。

### 最小請求

```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  }'
```

### 必備欄位

| 欄位 | 意義 |
|---|---|
| `model` | 哪個模型。建議 `claude-sonnet-4-6`,日常用 |
| `max_tokens` | 這次能輸出的最大 token 數 |
| `messages` | 對話歷史,從 user 開始,user / assistant 輪替 |

### 選用欄位

| 欄位 | 意義 |
|---|---|
| `system` | System prompt(一段 string 或 content block 陣列) |
| `temperature` | 0–1(Claude 最大 1,不是 2) |
| `top_p` | nucleus sampling,不建議跟 temperature 同時調 |
| `stop_sequences` | 遇到這些字串就停輸出 |
| `stream` | true/false,要 streaming 回傳的話 |
| `tools` | 工具定義,tool use 時用 |
| `metadata.user_id` | 使用者識別,給 Anthropic 做 abuse detection |

---

## Python SDK

```bash
pip install anthropic
```

```python
from anthropic import Anthropic

client = Anthropic()   # 讀 ANTHROPIC_API_KEY 環境變數

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You are a helpful Python tutor. Answer briefly.",
    messages=[
        {"role": "user", "content": "What's a decorator?"}
    ]
)

print(response.content[0].text)
print(f"Input tokens: {response.usage.input_tokens}")
print(f"Output tokens: {response.usage.output_tokens}")
```

### 注意 `content` 的結構

response 的 `content` 是 **content block 的 list**,不是單一 string。原因:一次 response 可能包含 text、tool_use、thinking 多種 block。

```python
for block in response.content:
    if block.type == "text":
        print(block.text)
    elif block.type == "tool_use":
        print(f"Tool: {block.name}, input: {block.input}")
```

同樣,你送的 `messages[i].content` 可以是:

- 一段 string(最常見,等同單一 text block)
- 一個 list of content blocks(含圖、tool_result 等)

---

## TypeScript SDK

```bash
npm install @anthropic-ai/sdk
```

```typescript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic();

const response = await client.messages.create({
  model: "claude-sonnet-4-6",
  max_tokens: 1024,
  system: "You are a helpful Python tutor. Answer briefly.",
  messages: [{ role: "user", content: "What's a decorator?" }],
});

console.log(response.content[0].type === "text" && response.content[0].text);
```

API shape 跟 Python 幾乎一樣。Node 環境預設讀 `ANTHROPIC_API_KEY`。

---

## 多輪對話

**Messages API 是 stateless**——你每次要把完整歷史送回:

```python
messages = []

# 第一輪
messages.append({"role": "user", "content": "What's FastAPI?"})
resp1 = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=messages,
)
messages.append({"role": "assistant", "content": resp1.content[0].text})

# 第二輪
messages.append({"role": "user", "content": "Show me a minimal example."})
resp2 = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=messages,
)
```

**自己管 history**。沒有 session ID 這回事。好處:你自主控制什麼留什麼丟。

### 長對話的 context 問題

對話越長 → messages 越大 → 每次 call 成本和延遲都升 → 接近 200k 時會報錯。

**應對**:

- 截斷舊訊息(但太粗暴可能丟重要 context)
- Summarize 老訊息(需要另一次 LLM call,下一段)
- **Prompt caching**(Ch 9)

### Summarize 舊對話

```python
def summarize_history(messages):
    if len(messages) < 20: return messages
    old = messages[:-10]    # 保留最新 10 則
    recent = messages[-10:]
    summary = client.messages.create(
        model="claude-haiku-4-5",    # 用便宜 model 做 summary
        max_tokens=500,
        messages=[
            {"role": "user", "content": f"Summarize this conversation concisely:\n\n{old}"}
        ]
    ).content[0].text
    return [{"role": "user", "content": f"Summary of prior conversation: {summary}"}] + recent
```

---

## System Prompt 實務

### 簡單版:string

```python
client.messages.create(
    system="You are a concise coding assistant.",
    ...
)
```

### 結構化版:content block 陣列

**推薦格式**,方便加 prompt caching(下一章):

```python
client.messages.create(
    system=[
        {
            "type": "text",
            "text": "You are a coding assistant."
        },
        {
            "type": "text",
            "text": LARGE_CONTEXT,     # 文件、規範,可以很大
            "cache_control": {"type": "ephemeral"}   # 快取這段
        }
    ],
    ...
)
```

---

## Retry 與 Error Handling

SDK 自帶 retry(429、500、529 等)。預設 2 次,可調:

```python
client = Anthropic(max_retries=5)
```

常見錯誤:

| 錯誤 | 原因 | 處理 |
|---|---|---|
| `401` | API key 壞 | 檢查 env / key |
| `400 invalid_request_error` | request 格式錯 | 讀訊息 |
| `429` | rate limit | SDK 會自動 retry with backoff |
| `500 overloaded_error` | Anthropic 側擠爆 | retry,或 fallback 其他 model |
| `529` | timeout | 重試,或切成 streaming |

### 實務 retry 模板

```python
from anthropic import APIStatusError
import time

def call_with_retry(messages, max_retries=3):
    for i in range(max_retries):
        try:
            return client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=messages,
            )
        except APIStatusError as e:
            if e.status_code == 529 and i < max_retries - 1:
                time.sleep(2 ** i)   # exponential backoff
                continue
            raise
```

SDK 的預設 retry 夠用,自訂通常只為加 log 或 fallback。

### Fallback 到別的 model

```python
try:
    resp = client.messages.create(model="claude-sonnet-4-6", ...)
except APIStatusError as e:
    if e.status_code == 529:    # overloaded
        resp = client.messages.create(model="claude-haiku-4-5", ...)    # 降級
    else:
        raise
```

Production 常用 pattern——大 model 暫時 overload 時自動 fallback 到小 model 維持服務。

---

## 成本計算

Anthropic 按 token 計費。主要三種單價:

| | Input | Output | Cache write | Cache read |
|---|---|---|---|---|
| Haiku 4.5 | 最便宜 | 略貴 | +25% | -90% |
| Sonnet 4.6 | 中 | 中 | +25% | -90% |
| Opus 4.7 | 最貴 | 最貴 | +25% | -90% |

(具體數字會變,查官方 pricing 頁)

**計算範例**:

```python
resp.usage
# MessageUsage(
#     input_tokens=150,
#     output_tokens=80,
#     cache_read_input_tokens=0,
#     cache_creation_input_tokens=0
# )
```

production 要在 log 裡記這些,每天 aggregate 看趨勢。

### 用 extended context(1M)的成本

超過 200k input 的部分**價格 2x**。所以 1M context 不是「同樣價格塞 5 倍」,是「塞到 200k 後每個 token 更貴」。預算時注意。

---

## 實用模板:最小的 chat server

```python
# server.py
from fastapi import FastAPI
from pydantic import BaseModel
from anthropic import Anthropic

app = FastAPI()
client = Anthropic()

class ChatRequest(BaseModel):
    messages: list[dict]    # [{"role": ..., "content": ...}, ...]
    system: str = "You are a helpful assistant."

@app.post("/chat")
async def chat(req: ChatRequest):
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=req.system,
        messages=req.messages,
    )
    return {
        "text": resp.content[0].text,
        "tokens": {
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
        }
    }
```

這就是 LLM chatbot 後端的 minimal 形狀。後面的章節會在這之上疊 tool use、caching、eval。

---

## Streaming 概念預告

若需要 **token-by-token 顯示**(打字機效果):

```python
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

Streaming 的細節 Ch 10 再講。現在知道有這個機制即可。

---

## 自我檢核

- [ ] Messages API 是 stateful 還是 stateless?多輪對話怎麼維持?
- [ ] `response.content` 為什麼是 list 不是 string?
- [ ] System prompt 為什麼建議用結構化(content block array)?
- [ ] 429 和 529 的差別?SDK 預設會重試嗎?
- [ ] 超過 200k context 後 token 價格會怎樣?

→ [Ch 8 Tool Use:真正在做什麼](./08-tool-use.md)
