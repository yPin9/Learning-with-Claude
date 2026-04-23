# Ch 8 — Tool Use:真正在做什麼

> 目標:徹底搞清楚 tool use 的機制、如何寫好 tool definition、parallel tool call、錯誤處理、和常見 failure mode。

Ch 1 講過 tool use 的基本形狀。這章進實作。

## Tool use 的完整 loop

再強調一次流程:

```
1. 你定義 tools(JSON schema)
2. 送 messages + tools 給 API
3. Claude 回:"我想 call tool X,參數是 Y"
4. 你的 code 執行 tool X(Y),拿到 result
5. 把 result 作為 tool_result 塞回 messages
6. 再送 messages(已含 tool call + result)給 API
7. Claude 可能再要 call 別的 tool(回 3),或給出最終答案
```

**4、5、6 是你要寫的 loop**。這個 loop 你不能外包給 Anthropic(SDK 也沒幫你跑,除非用 Agent SDK——那是下 part)。

---

## Tool definition 格式

```python
tools = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city. Returns temp, conditions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'Taipei', 'Tokyo'"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit",
                    "default": "celsius"
                }
            },
            "required": ["city"]
        }
    }
]
```

**四個欄位**:

- `name`:工具名,snake_case 最穩
- `description`:**最重要**,Claude 憑這個決定何時用
- `input_schema`:JSON Schema
- `required`:必填參數

### Description 是 prompt

Tool description 是 Claude 決定「用不用、怎麼用」的依據。寫得糟就用得糟。

**壞範例**:

```python
"description": "Search"
```

**好範例**:

```python
"description": "Search the product catalog by keyword or category. Returns up to 20 matching items with fields: id, name, price, category, in_stock. Use this when the user asks about products by name, category, or feature. Do NOT use this for customer support or order history—use get_order for that."
```

**三個重點**:
1. **做什麼** + 回傳什麼
2. **什麼時候用**(positive 範例)
3. **什麼時候不用**(負向,避免誤用)

---

## 完整的 tool use loop 實作

```python
from anthropic import Anthropic
client = Anthropic()

def run_tool(name, inputs):
    if name == "get_weather":
        # 真的呼叫 API
        return f"{inputs['city']}: 22°C, sunny"
    elif name == "get_order":
        return f"Order {inputs['order_id']}: shipped"
    raise ValueError(f"Unknown tool: {name}")

def agent_loop(user_message, tools, max_iterations=10):
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_iterations):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=tools,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "end_turn":
            # Claude 給出最終答案
            return resp

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    try:
                        result = run_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {e}",
                            "is_error": True,
                        })

            messages.append({"role": "user", "content": tool_results})

    raise RuntimeError("Hit max iterations")
```

**關鍵細節**:

1. **一輪可能含多個 tool_use block**。全都要執行、全都要對應一個 tool_result。
2. **tool_result 是 user message 的 content block**,不是 assistant 的。
3. **`tool_use_id` 要對上**,Claude 才知道哪個 result 回應哪個 call。
4. **Error 也要塞回**,加 `is_error: true`,Claude 會嘗試 recover。
5. **`stop_reason`** 告訴你是結束(`end_turn`)還是要 call tool(`tool_use`)。
6. **max_iterations** 必須有——避免無窮迴圈。

---

## Parallel Tool Calls

Claude 一次可能輸出**多個 tool_use block**(如果判斷可平行)。

```
user: "查詢台北和東京天氣"
↓
Claude 輸出 2 個 tool_use:
  - get_weather(city="Taipei")
  - get_weather(city="Tokyo")
```

**你該平行執行**,不要一個個跑:

```python
import asyncio

async def run_tools_parallel(blocks):
    tasks = []
    for block in blocks:
        if block.type == "tool_use":
            tasks.append(asyncio.to_thread(run_tool, block.name, block.input))
    return await asyncio.gather(*tasks)
```

或 `concurrent.futures.ThreadPoolExecutor`。平行化能大幅降低 latency。

### 強制 Claude 平行

如果 Claude 傾向 sequential(同時想要兩個資訊但分兩次要),試著在 system prompt 或 tool description 提:

```
When you need data from multiple sources, call tools in parallel whenever possible.
```

通常 Claude 4+ 自己就會平行,除非有依賴。

---

## `tool_choice` 參數:強制行為

預設 Claude 自己決定要不要 call tool。想強制:

```python
# 強制 call 某個特定 tool
tool_choice={"type": "tool", "name": "get_weather"}

# 強制至少 call 一個 tool(any)
tool_choice={"type": "any"}

# 禁止 call tool(只能回文字)
tool_choice={"type": "none"}

# 預設:auto
tool_choice={"type": "auto"}
```

### 用途

- **強制輸出結構化資料**:定義一個 `record_answer` 工具,強制 call 它 → Claude 的答案會是工具參數 JSON,而不是自由文字。
- **分階段**:第一次強制 `search`,取得結果後再 auto。

---

## 結構化輸出技巧

想讓 Claude 輸出嚴格 JSON,**不要靠 prompt 求**,用 **tool call 來拿**:

```python
tools = [{
    "name": "record_sentiment",
    "description": "Record the sentiment analysis result.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"}
        },
        "required": ["sentiment", "confidence", "reason"]
    }
}]

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=tools,
    tool_choice={"type": "tool", "name": "record_sentiment"},
    messages=[{"role": "user", "content": "Tweet: 'Meh, not bad'"}]
)

# 取結果
for block in resp.content:
    if block.type == "tool_use":
        print(block.input)   # 保證符合 schema 的 dict
```

**這比「請回 JSON」的 prompt 可靠 10 倍**。型別有保證、enum 有保證、required 有保證。

---

## 常見 failure mode

### Failure 1:Claude 發明不存在的 tool

**徵兆**:`tool_use` 的 `name` 不在你的 tools list。

**現代模型罕見**,但仍可能在:
- Tool name 太像 Claude 認為「應該存在」的工具
- 描述誤導 Claude 以為有類似的 tool

**處理**:`is_error: true` + message「Tool X doesn't exist. Available: [list]」。

### Failure 2:參數型別 / schema 違反

**徵兆**:`input` JSON 不符合 `input_schema`。

你寫 loop 時應該 **validate 參數,失敗就當 error 塞回**:

```python
import jsonschema

try:
    jsonschema.validate(block.input, tool["input_schema"])
except jsonschema.ValidationError as e:
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": f"Invalid input: {e.message}. Schema: {tool['input_schema']}",
        "is_error": True,
    })
```

### Failure 3:無限迴圈

Claude call tool → 結果不滿意 → 再 call → 又不滿意 → ...

**防禦**:`max_iterations`,以及在 prompt 裡明講「if you can't find what you need in 3 tries, admit it」。

### Failure 4:繞過工具

該用 tool 卻硬編故事:

> user: "我訂單 #123 的狀態?"
> Claude: "基於我的資料,訂單 #123 應該已經出貨..."(沒 call `get_order`!)

**防禦**:system prompt 明說「對於訂單狀態、庫存、帳戶資訊,**必須** call 對應工具,不能自行推測」。

### Failure 5:太積極用 tool

有時候 Claude 會對 trivial 問題都 call tool(「1+1」也去 call calculator)。

**防禦**:tool description 加「Use this only for calculations beyond trivial arithmetic (>= 3-digit numbers or floats)」。

---

## 工具數量的原則

再強調 Ch 1 提的:**超過 10 個工具效果下降**。

### 解法 1:routing

建一個 `determine_intent` 工具先 classify,然後根據 intent 只 expose 相關工具集:

```python
intents = {
    "billing": [get_invoice, get_payment_method, ...],
    "technical": [get_logs, check_status, ...],
    "account": [get_profile, update_profile, ...],
}
```

### 解法 2:subagent delegation

把「專屬工具集的任務」交給 subagent——主 agent 看不到這些工具,就不會誤 call。

### 解法 3:壓縮相似工具

原本 10 個 `get_user_by_email`、`get_user_by_id`、`get_user_by_phone`...
壓成 1 個 `find_user(lookup_type, value)`。工具少了,表達力不變。

---

## Tool use 的 prompt caching

Tool definitions 不變時可以 cache:

```python
tools=[
    {...first_tool...},
    {...second_tool...},
    {..., "cache_control": {"type": "ephemeral"}}   # 快取到這裡
]
```

Ch 9 細講。

---

## 一個小型 agent 的完整範例

```python
SYSTEM = """You are a customer support agent. Use tools to look up order and product info.
Never guess about order status or prices—always use tools.
When you have the info, respond concisely to the user."""

TOOLS = [
    {
        "name": "get_order",
        "description": "Look up an order by ID. Returns status, items, tracking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_product",
        "description": "Look up a product by name or SKU.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    }
]

def run_agent(user_msg):
    messages = [{"role": "user", "content": user_msg}]

    for i in range(10):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "end_turn":
            return resp.content[-1].text

        # 處理 tool calls
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                result = run_tool(b.name, b.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": results})

    return "I couldn't complete your request."


print(run_agent("Where's my order #A12345?"))
```

**這是所有 LLM agent 的底層樣子**,不管框架多漂亮,底下就是這個 loop。

---

## 自我檢核

- [ ] Tool use loop 的 7 步流程,你能閉眼寫嗎?
- [ ] `tool_result` 是哪個 role 的 content block?
- [ ] Tool description 該寫什麼?為什麼重要?
- [ ] Parallel tool call 怎麼實作?什麼情況 Claude 會平行?
- [ ] `tool_choice={"type": "tool", "name": X}` 的用途?
- [ ] Claude 繞過工具硬編答案時怎麼防?

→ [Ch 9 Prompt Caching:省錢也省延遲](./09-prompt-caching.md)
