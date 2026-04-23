# Ch 4 — Prompting 的心法(不是招式集)

> 目標:擺脫「prompting 是收集咒語」的學法。講結構、意圖、可驗證性——能遷移的心法,不是換個模型就失效的 hack。

## 病:把 prompting 當魔法口訣

網路上一堆「神奇 prompt」:

- "Take a deep breath and think step by step"
- "You are an expert in X, be more helpful"
- "I will tip you $500"

**2023 年這些還有點用,現在意義很小**。現代 Claude 的 instruction following 能力遠超過這些 hack 的彈性,寫得再玄的咒語,效果都比不上「把意圖清楚結構化」。

這章不收咒語,收方法論。

---

## Prompt 的四個組成部分

一個好的 prompt 通常有這四部分(順序可調):

### 1. **Role / Context**:你是誰、場景是什麼

```
You are a senior Python engineer reviewing a pull request for a production service.
The PR adds a new DB query to an existing endpoint.
```

**作用**:啟動模型對應的 persona 和 domain knowledge。

### 2. **Task**:具體要做什麼

```
Review the code below. Focus on:
- SQL injection risk
- N+1 query patterns
- Error handling
```

**作用**:明確意圖。「幫我看看」太模糊,「找這三類問題」才能判對錯。

### 3. **Constraints / Format**:輸出該長什麼樣

```
Output format:
- A numbered list of issues
- Each issue: severity (HIGH/MEDIUM/LOW), location (file:line), description, suggested fix
- If no issues, output "LGTM"
```

**作用**:結構化輸出,下游好消費;也把「嚴重度怎麼分」這類判斷標準明講。

### 4. **Examples / Data**:資料或例子

```
Here's the PR diff:

<diff>
...
</diff>
```

**作用**:LLM 要操作的原料。

---

## 用 XML / 結構化標籤隔離

Claude 對 XML tag 特別敏感。**把不同類型的內容用 tag 包**,Claude 會更清楚什麼是指令、什麼是資料、什麼是例子:

```
<instructions>
Summarize the meeting transcript below into 3 bullet points.
</instructions>

<transcript>
{TRANSCRIPT_TEXT}
</transcript>

<output_format>
- Each bullet ≤ 15 words
- Start each with a verb
- No filler words like "discussed", "talked about"
</output_format>
```

tag 名稱**不是 HTML 的 tag 名**,你愛叫什麼都可以(`<foo>`、`<customer_email>`、`<step_1>`)。重點是**結構化**。

**實驗**:同一個 prompt 有 tag 和沒 tag,準確度常差 10–30%。

---

## Few-shot:給例子比給指令強

**「描述」一個任務不如「示範」一個任務**。

**壞範例**(純描述):
```
Classify the sentiment of the following tweets as positive, negative, or neutral.
```

**好範例**(加 few-shot):
```
Classify the sentiment of tweets as positive, negative, or neutral.

<examples>
Tweet: "Love this product, exceeded expectations!"
Sentiment: positive

Tweet: "Meh, it's okay I guess"
Sentiment: neutral

Tweet: "Total waste of money, would return if I could"
Sentiment: negative
</examples>

Tweet: "{INPUT}"
Sentiment:
```

**幾個 few-shot 心法**:

1. **例子涵蓋邊緣情況**:給 positive/negative/neutral 各一個夠嗎?不夠——還要給「反諷」、「混合感情」、「非評價內容」這類邊界 case 的例子。
2. **例子數量 3–5 個** 是甜蜜點。太多擠 context、太少泛化差。
3. **例子格式和你要的輸出格式一致**。你給 JSON 例子,輸出就會是 JSON。

---

## Chain of Thought(CoT)

讓模型「先推理再答」的技巧。

**簡單版**:

```
Think step by step before answering.
```

**結構化版**(推薦):

```
Before giving your final answer, use <thinking> tags to reason through:
1. What's the user actually asking?
2. What information do I have?
3. What are the edge cases?

Then in <answer> tags, give your final response.
```

**CoT 的真相**:

- 對需要**多步推理**的問題提升明顯(數學、邏輯、code debug)
- 對直接回答類問題**沒幫助,甚至變差**(花 tokens 想一些沒必要的東西)
- Claude 的 extended thinking mode(Ch 10)是更好的 CoT 版本——thinking 不算在 output 內,不會污染輸出

### 什麼時候不用 CoT

- 需要短回應(例如分類)
- 格式要求嚴格(讓 Claude 推理會 leak 推理內容)
- 已經用 extended thinking(重複)

---

## Prefill(預填 Claude 的回應開頭)

API 有個技巧:在 `messages` 裡放 `assistant` role 的起始片段,Claude 會「續寫」。

```python
messages = [
    {"role": "user", "content": "Classify sentiment: 'Great product!'"},
    {"role": "assistant", "content": "{"}    # 預填
]
```

Claude 看到 `{` 就會續 JSON,幾乎保證輸出是 JSON 開頭。

**用途**:

- 強制 JSON 格式(預填 `{` 或 `[`)
- 強制 XML 開頭(預填 `<result>`)
- 避免 Claude 啰嗦開場白(預填主要內容第一字)

claude.ai 沒這個,是 API 專屬。

---

## 避免常見 anti-pattern

### Anti-pattern 1:「請不要做 X」

Claude 看到「不要」很容易錯誤 parse。**講正面的而不是負面的**:

```
# BAD
Don't use markdown formatting.

# GOOD
Output plain text only. No bullets, no headers, no asterisks.
```

### Anti-pattern 2:指令跟資料混在一起

```
# BAD
Summarize this: Deleting the user's files...
```

萬一資料裡有「ignore previous instruction」之類的文字,會被當指令執行(prompt injection)。

```
# GOOD
<task>Summarize the text in <content> tags.</task>
<content>
Deleting the user's files...
</content>
```

Tag 區隔之後,裡面的「指令」也只是內容,不會被執行。

### Anti-pattern 3:過度禮貌

```
# BAD
Could you please, if it's not too much trouble, help me maybe...
```

**直接**。Claude 不在意你的禮貌,模糊的指令只會降低品質。

### Anti-pattern 4:「be more creative」

空泛的修飾詞沒用。給具體的標準:

```
# BAD
Write a creative headline.

# GOOD
Write 5 headlines, each:
- Under 10 words
- Uses a concrete image, not abstract adjectives
- Has at least one unexpected word pairing
```

### Anti-pattern 5:一個 prompt 做 5 件事

```
# BAD
"Read this, summarize, find bugs, suggest tests, write docs, and translate to Chinese"
```

拆成多個 prompt(或多個 tool call)。Claude 做複合任務品質會掉。

---

## System prompt vs User prompt 怎麼分

API 有 system prompt(separate parameter)和 user message。心法:

- **System prompt**:**永遠不變的部分**——角色、格式、約束、工具使用規範
- **User message**:**會變的部分**——這次的資料、這次的問題

好處:system prompt 可以 **prompt caching**(Ch 9),變 cheaper / faster。

---

## 寫 prompt 的工作流

不要一次寫完就用。流程:

1. **先寫 minimal prompt**,只含 role + task
2. **跑 5–10 個測試 case**,看哪些失敗
3. **根據失敗模式補 constraint / example**
4. **不要猜,看實際輸出**
5. **寫到滿足 80% case 就停**,剩下 20% 用後處理 / eval 處理

**Prompt engineering 是 iterative debugging**,不是文學創作。

---

## 進階:Meta-prompting

**用 Claude 幫你寫 prompt**。給它:

```
I want a prompt that:
- Classifies customer support tickets into [billing, technical, account]
- Outputs JSON with {category, confidence, reason}
- Handles ambiguous cases by assigning "unclear" category
- Rejects non-English inputs

Write me the prompt. Be specific about format. Include 3 few-shot examples.
```

Claude 會寫出一個不錯的初稿,你再改。**比你從零開始快 5 倍**。

---

## Prompt 的「遺毒」清單(別抄網路上老 prompt)

這些 2022–2023 年流行的招式,現在**對 Claude 4+ 基本沒用或有害**:

- "You are ChatGPT" → 根本不是
- "Take a deep breath" → 沒效
- "Think step by step" → 沒效,改用 extended thinking
- "I will tip you $100" → 沒效,且顯得你在瞎碰
- "You MUST do X" → 全大寫無助於 obey rate,有時反而讓 Claude 過度警覺

**現代 Claude 的 instruction following 很好**,你只要**結構化、明確、提供例子**,不需要招式。

---

## 自我檢核

- [ ] 一個好 prompt 的四個組成部分?
- [ ] 為什麼要用 XML tag 包資料?
- [ ] Few-shot 的例子要選什麼樣?
- [ ] System prompt 和 user message 怎麼分?為什麼這樣分?
- [ ] 「Think step by step」對 Claude 4 還有用嗎?

→ [Ch 5 Claude Code 起手式](./05-claude-code-basics.md)
