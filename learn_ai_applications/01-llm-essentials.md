# Ch 1 — Token / Context / Sampling / Tool Use 的最低必備

> 目標:搞懂 LLM 的四個核心 primitive。不是學理,是做產品前你必須有的最低共識。

你不需要懂 transformer 架構才能做 AI 應用,但這四件事沒搞清楚,之後每章都會卡:

1. Token 是什麼、長什麼樣
2. Context window 的限制與代價
3. Sampling(temperature、top-p、determinism)
4. Tool use 的機制

---

## 1. Token

**Token 是 LLM 的最小處理單位**,不是字元也不是詞。

Claude 用的 tokenizer 是 BPE 變體。粗略感覺:

| 語言 | 1 token 大約是 |
|---|---|
| 英文 | 3–4 個字元,常見詞一個 token |
| 中文 | 1–2 個漢字(常見漢字通常 1 token,罕用的 2–3 token) |
| 程式碼 | 取決於語言。Python 常見關鍵字通常 1 token |

**幾個實用後果**:

- **中文比英文貴**。同一段話中文版的 token 數通常比英文多 30–50%。
- **unicode 奇怪字元爆炸**。emoji、罕用字會拆成多個 token。
- **空格和標點也是 token**,多餘的 whitespace 會浪費。
- **JSON 和 YAML 差**:JSON 的 `{`、`"`、`:` 都算 token,輸出 JSON 比純文字貴。

### 怎麼數 token

**Python SDK**:

```python
from anthropic import Anthropic
client = Anthropic()
resp = client.messages.count_tokens(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.input_tokens)
```

或 online 工具(Anthropic tokenizer 網頁)。

### 為什麼這重要

成本、延遲、context 限制——全部按 token 算。你做成本估算、快取決策、截斷策略時,要能心算「這段大約多少 token」。

**心法**:
- 英文 1 詞 ≈ 1.3 token
- 中文 1 字 ≈ 1.2 token
- 程式碼 100 行 ≈ 500–1000 token

---

## 2. Context Window

**Context window = 一次 API 呼叫能塞的 input + output token 總數**。

Claude 目前主流 context size:

| 模型 | Context window |
|---|---|
| Claude Sonnet 4.6 | 200k(標準)/ 1M(beta flag) |
| Claude Opus 4.7 | 200k / 1M |
| Claude Haiku 4.5 | 200k |

**200k token 大約是**:
- 英文 ~150k 字(500 頁書)
- 中文 ~160k 字(三到四本小說)
- 程式碼 ~30k 行(一個中型 repo)

### Context 的誤會

**誤會**:「Context 能塞 200k,那我就把整個 repo 塞進去」

**真相**:

1. **Needle-in-haystack 不 perfect**:雖然 benchmark 顯示 LLM 能從 200k 中找到資訊,但**相關性推理** 隨 context 增大變差。塞太多等於稀釋重點。
2. **成本跟 token 成正比**:塞 100k 每次 query 都要錢。
3. **延遲變高**:input 越大 TTFT(time to first token)越長。

### 該塞什麼到 context

按優先順序:

1. **System prompt**:指令、角色、約束。10–500 tokens 通常夠。
2. **Tool definitions**:tool use 的 schema。每個工具約 50–300 tokens。
3. **Retrieval**:針對當下問題的相關資料,**不是所有資料**。
4. **Conversation history**:多輪時前面的對話。
5. **當前使用者輸入**。

**超出 context 時怎麼辦**:
- 截斷舊 history
- Summarize 舊 history(需要另一次 LLM call)
- Retrieval 只取 top-k
- **Prompt caching**(Ch 9)把靜態部分快取

### Extended Context 的陷阱

**1M context 的 beta** 是真的,但:

- **成本結構不同**:超過 200k 的部分價格翻倍
- **延遲爆增**:1M input 的 first token 要好幾十秒
- **不是所有場景都合適**:多數時候 RAG 比長 context 好

「能塞進去」不等於「該塞進去」。

---

## 3. Sampling:temperature / top-p / deterministic

**Sampling 是 LLM 決定下一個 token 用哪個字的方式**。

### Temperature

- `temperature = 0`:每次選機率最高的 token(argmax)
- `temperature = 1`:按機率分布 sample
- `temperature > 1`:更平坦的分布,更隨機
- `temperature < 1`:更尖的分布,接近貪婪

**實務推薦值**:

| 任務 | Temperature |
|---|---|
| 事實回答、結構化輸出 | 0 或 0.1 |
| 一般對話、摘要 | 0.7 |
| 創意寫作、brainstorm | 0.9–1.2 |

### Top-p(nucleus sampling)

「只從累積機率 top-p 的 token 集合裡 sample」。

`top_p = 0.9` 表示只考慮「把最可能的幾個 token 加起來佔 90% 機率」的那組。

**Claude 建議調 temperature 或 top_p 其中一個,不要同時調**。

### Temperature = 0 真的 deterministic 嗎

**不完全**。理由:

1. **Floating point non-associativity**:平行 reduction 順序不同,結果可能差 1e-7,偶爾讓 argmax 不同。
2. **Batching**:你的請求被批次和別人一起處理,batch 組成不同結果可能不同。
3. **模型版本變動**:`claude-sonnet-4-6` 和 `claude-sonnet-4-6-20250319` 不同——點版本可能被更新。

**實務上這意味**:

- `temperature=0` 在 **90%+ 的情況** 結果穩定
- 要**完全 reproduce** 請把所有版本 pin 住(model ID 帶日期後綴)
- 寫 eval 不要逐字比對,比對語義或結構

---

## 4. Tool Use(Function Calling)

**LLM 不能執行程式、不能讀 DB、不能打 API——它只會輸出 token**。

「Tool use」的真相:

1. 你給 LLM 一個**工具清單**(JSON schema 描述每個工具)
2. LLM 決定「我想呼叫這個工具,這是參數」(輸出結構化 JSON)
3. **你的 code** 接到這個 JSON,去**你自己執行**工具,拿到結果
4. 把結果塞回 context,再問 LLM「知道結果了,接下來?」
5. 重複直到 LLM 說「OK 我有答案了」

**LLM 不會自己執行工具**。它只會說「我想執行」。執行是你的責任。

### 最小範例

```python
from anthropic import Anthropic
client = Anthropic()

tools = [{
    "name": "get_weather",
    "description": "Get weather for a city",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
    }
}]

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "今天台北天氣?"}]
)

# resp.content 可能包含 tool_use block
for block in resp.content:
    if block.type == "tool_use":
        result = my_get_weather(block.input["city"])   # 你自己執行
        # 然後再發一次 request,把 result 塞回:
        ...
```

### Tool Use 的關鍵設計原則

**1. Tool description 就是 prompt**

Tool 的 description 和 parameter description 是 LLM 決定「何時用、怎麼用」的主要依據。寫得糟 = LLM 用得糟。

```python
# BAD
"description": "Search"

# GOOD
"description": "Search the product catalog by keyword. Returns up to 10 matching products with id, name, price. Use this when the user asks about products by name or category."
```

**2. 工具數量要少**

Anthropic 自己的建議:**超過 10 個工具效果明顯下降**。理由:

- Context 被 tool definitions 佔掉
- LLM 選擇難度增加
- 同名或相似的工具會互相干擾

超過 10 個的話,考慮**routing**:先讓 LLM 選「類別」,再只 expose 該類別的工具。

**3. 錯誤要塞回 context**

工具執行失敗時,把錯誤訊息當 tool_result 塞回去。LLM 會根據錯誤重試或換方法。

**4. Parallel tool calls**

Claude 可以一次輸出**多個 tool_use block**(如果工具間沒依賴)。你該**平行執行**這些工具,不要順序跑。

### Tool Use 的失敗模式

- **Hallucinate 工具**:LLM 發明不存在的工具(降到 claude-4.x 之後罕見,但看過)
- **錯誤參數**:型別錯、必填漏、值域超過
- **無窮 loop**:調用工具 → 結果不滿意 → 再調 → 又不滿意。要設 **max iterations**
- **繞過工具**:該用 tool 結果卻硬編故事(「基於我的資料...」)

這些全部在 Ch 8 會細講。

---

## 四個 primitive 之間的關係

```
使用者輸入
    ↓
Tokenize → context window 佔用
    ↓
Sampling 產生下一個 token
    ↓
若 token 是「tool_use 結構」→ 執行工具 → 結果塞回 context
    ↓
繼續 sampling 直到 stop
```

這四個 primitive 是做 AI 應用的**最小詞彙**。用它們描述系統:

> "我們的 agent 最多用 20k tokens 的 context,其中 5k 是 system prompt,10k 是 retrieval,留 5k 給 conversation。temperature 設 0.3,配了 5 個工具,tool use 的 max iterations 設 8。"

這段話讓其他工程師知道你的系統長什麼樣。**這才是 AI 工程師的通用語言**。

---

## 自我檢核

- [ ] 「Hello, world」在 Claude tokenizer 大約幾個 token?中文的「你好,世界」呢?
- [ ] 你能描述「200k context 不等於能塞 200k 有用資訊」的三個理由嗎?
- [ ] `temperature=0` 的輸出在什麼情況下仍可能變動?
- [ ] 「Tool use」實際執行工具的是誰?
- [ ] 為什麼工具超過 10 個會變差?

→ [Ch 2 Claude 生態地圖](./02-claude-ecosystem-map.md)
