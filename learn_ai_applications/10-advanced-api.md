# Ch 10 — Extended Thinking / Streaming / Batch / Files

> 目標:把 API 其他實用 feature 一次過:extended thinking、streaming、batch、files、citations。知道哪個功能該在什麼場景用。

這章是 builder 工具箱的補充——不是每個 app 都要用,但遇到對的場景會省很多事。

---

## 1. Extended Thinking

**Claude 在回答前做「內心推理」,這段推理不算在 output token 裡**。

### 為什麼要它

比起舊版 CoT(叫 Claude「step by step 想」然後把推理放 output):

| | 舊 CoT | Extended Thinking |
|---|---|---|
| 推理是否在 output | 是,污染結果 | 否,分開 |
| 用 token | 吃 output 額度 | 獨立額度 |
| 品質 | 跟一般輸出差不多 | 針對推理優化 |
| Cost | 算 output 錢 | 算 thinking 的 token 錢(類 input) |

**適用場景**:

- 難的 debug(需要試多種假設)
- 複雜架構決策
- 多步數學 / 邏輯推理
- 細節多的 code review

**不適用**:
- 一般聊天(沒複雜推理的題沒用)
- 延遲敏感的場景(thinking 會多花 5–30 秒)

### 啟用

```python
resp = client.messages.create(
    model="claude-opus-4-7",        # thinking 目前偏向大模型
    max_tokens=4096,
    thinking={
        "type": "enabled",
        "budget_tokens": 10000      # thinking 最多 10k tokens
    },
    messages=[
        {"role": "user", "content": "Debug this race condition in ..."}
    ]
)

# 取出 thinking + 答案
for block in resp.content:
    if block.type == "thinking":
        print(f"THINKING: {block.thinking[:200]}...")
    elif block.type == "text":
        print(f"ANSWER: {block.text}")
```

### `budget_tokens`

Thinking 的上限。Claude 會在這 budget 內盡可能推理。

- 簡單問題:1k
- 中等:5k
- 複雜:10k–20k
- 極複雜:30k+(上限視模型)

**budget 大 ≠ 效果好**,超過問題本身需求會浪費。

### 在 Claude Code 的 thinking

Claude Code 有 `/think`、`/think hard`、`/ultrathink` 三個 trigger,依序給更多 budget。日常寫 code 偶爾用 `/think hard` 讓 Claude 多想想,對大重構有幫助。

---

## 2. Streaming

要 token-by-token 回應(打字機效果)、或要在 client 側 progress render,用 streaming。

### Python

```python
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Write a short story"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

    # 結束後取完整 response
    final = stream.get_final_message()
    print(f"\nTotal tokens: {final.usage.output_tokens}")
```

### Event types

Stream 會發多種 event:

- `message_start`:整個 response 開始
- `content_block_start`:開始一個 block(text、tool_use、thinking...)
- `content_block_delta`:block 的增量
- `content_block_stop`:block 結束
- `message_delta`:message 層級變化(stop_reason)
- `message_stop`:整個結束

大多 SDK 幫你抽象了這些。要底層控制可以直接拿 events:

```python
with client.messages.stream(...) as stream:
    for event in stream:
        print(event.type, event)
```

### Streaming + Tool Use

Tool use 的 input 也會 streaming——Claude 一個 token 一個 token 組出 JSON。SDK 幫你累積,最後拿到完整 tool_use block。

**注意**:streaming 時 tool input 的 JSON 是 partial 的,你不能 parse 到一半就執行。等 `content_block_stop`。

### 何時用 streaming

- 使用者面對對話介面(需要看到進度)
- 長輸出(> 500 tokens),避免 TTFT 過長
- SSE / WebSocket 推送到前端

**不用**:
- 一次性 job(batch processing)
- 要整塊 validate 再用的輸出
- 不在乎延遲的離線任務

---

## 3. Batch API

**Batch 是離線、便宜、慢版的 API**。送一批 request 給 Anthropic,幾分鐘到 24 小時內處理完。

**價格:5 折**(半價)。

```python
# 建 batch
batch = client.messages.batches.create(
    requests=[
        {
            "custom_id": "request-1",
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "Summarize this: ..."}]
            }
        },
        # ...上千個
    ]
)

# 輪詢或等待
while True:
    b = client.messages.batches.retrieve(batch.id)
    if b.processing_status == "ended":
        break
    time.sleep(10)

# 取結果
for result in client.messages.batches.results(batch.id):
    print(result.custom_id, result.result.message.content)
```

### 何時用 batch

- **資料標記 / 分類**:1 萬筆資料要 Claude 分類,一次 batch 半價。
- **Offline eval**:跑 eval suite 對多組 prompt 配多組 model。
- **大量 summarization / translation**。
- **不急的後台任務**。

### 不用 batch

- 面對用戶的 request(batch 延遲不可控)
- 需要 tool use 的流程(一次性請求簡單,loop 本身難放 batch)
- Real-time 需求

---

## 4. Files

Files API 讓你**上傳檔案後用 file ID 引用**,不用每次請求都塞檔案內容。

```python
# 上傳
file = client.files.create(file=open("large_doc.pdf", "rb"))
file_id = file.id

# 用 file_id 在 message 裡
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {"type": "document", "source": {"type": "file", "file_id": file_id}},
            {"type": "text", "text": "Summarize this PDF"}
        ]
    }]
)
```

### 用途

- **大 PDF / doc 重複使用**:上傳一次,多次 query。
- **多人共用資料**:同組可存取同 file_id。
- **配合 caching**:file content 被當作 input 計 cache。

支援格式:PDF、text、部分 markdown 等。具體查文件。

### 限制

- 單檔大小上限(文件寫,現階段 32MB 左右)
- Files 有 retention(幾天到幾週)
- 敏感資料要考慮 upload 的合規

---

## 5. Citations

Claude 可以在輸出中標註「這段我是根據哪個資料」,以結構化 block 返回。

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {...},
                "citations": {"enabled": True}
            },
            {"type": "text", "text": "What does this doc say about X?"}
        ]
    }]
)

for block in resp.content:
    if block.type == "text":
        for citation in block.citations or []:
            print(citation)
```

### 為什麼重要

RAG 場景必備:讓使用者**看得到引用來源**。這對信任度、產品可信度、合規(例如法律 / 醫療)都重要。

手寫 citation(讓 Claude 自己說「source: p.3」)容易 hallucinate。Citations API 是結構化的,有實際位置 offset,不會編。

---

## 6. Vision(圖片輸入)

Claude 能讀圖(PNG、JPEG、GIF、WebP):

```python
import base64

with open("diagram.png", "rb") as f:
    img_data = base64.b64encode(f.read()).decode()

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}},
            {"type": "text", "text": "What does this diagram show?"}
        ]
    }]
)
```

或用 URL:

```python
{"type": "image", "source": {"type": "url", "url": "https://..."}}
```

### 用途

- UI 截圖 debug
- 圖表 / 流程圖分析
- OCR(英文很準,中文近期也可以)
- 多模態 RAG

### 限制

- 圖太大會被 resize(影響細節讀取)
- 一次 request 限幾張
- Token 計算:一張圖約等於 ~1500 tokens,視大小

---

## 7. 其他幾個小 feature

### `metadata.user_id`

給 request 標一個 end-user 的 anonymized ID,Anthropic 會用來做 abuse detection。**不要放真實 email / username**,hash 後放。

```python
client.messages.create(
    ...,
    metadata={"user_id": hashlib.sha256(user_email.encode()).hexdigest()}
)
```

### `stop_sequences`

指定若干字串,生成遇到就停。用於:

- 多段輸出按 delimiter 切
- Code gen 時避免超過某個 section

```python
stop_sequences=["\n\nHuman:", "```"]
```

### Pydantic / 型別輸出

透過 tool use 的 JSON Schema 達成。Ch 8 已講。不要靠 prompt 求 JSON。

---

## 綜合範例:智能客服的多 feature 組合

```python
# 特徵:
# - 大量靜態 system prompt → caching
# - 用 thinking 處理難問題
# - Streaming 給前端打字機效果
# - Tool use 查訂單
# - Citations 標註資料來源

with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=2048,
    thinking={"type": "enabled", "budget_tokens": 5000},
    system=[
        {"type": "text", "text": "You are a support agent."},
        {"type": "text", "text": KNOWLEDGE_BASE, "cache_control": {"type": "ephemeral"}}
    ],
    tools=[get_order_tool, search_kb_tool],
    messages=[{
        "role": "user",
        "content": [
            {"type": "document", "source": {...}, "citations": {"enabled": True}},
            {"type": "text", "text": user_query}
        ]
    }]
) as stream:
    for text in stream.text_stream:
        yield text    # 傳給前端
```

一個 request 用了 5 個 feature。真實 production LLM app 差不多就這複雜度。

---

## 功能選擇速查

| 需求 | 用什麼 |
|---|---|
| 要 progressive UI | Streaming |
| 大量離線任務、不急 | Batch |
| 要內心推理不污染輸出 | Extended Thinking |
| 大 PDF 多次 query | Files + caching |
| RAG 要標來源 | Citations |
| 要輸出嚴格 JSON | Tool use(Ch 8) |
| 要讀圖 | Vision |
| System prompt 重複 | Prompt Caching(Ch 9) |

---

## 自我檢核

- [ ] Extended Thinking 和舊式 CoT 的差別?
- [ ] Streaming 的 token 到 tool input 時,能中途 parse 嗎?
- [ ] Batch API 的折扣是多少?什麼場景該用?
- [ ] 為什麼 citations API 比讓 Claude 自己說來源可靠?
- [ ] 大量請求同一 PDF,用 files + caching 還是每次塞文字?

→ [Ch 11 MCP 為什麼存在](./11-mcp-why.md)
