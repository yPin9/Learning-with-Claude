# Ch 29 — Instruction Tuning：資料格式 Alpaca / ChatML

> 目標：理解讓模型「聽話」的訓練資料是什麼格式，以及如何準備高品質的 instruction tuning 資料。

## Instruction Tuning 的本質

Base model 學的是「下一個 token 是什麼」。Instruction tuning 讓模型學習的是「當我被這樣問，我應該這樣回答」。

關鍵區別：**訓練時只計算 response 部分的 loss，instruction 部分只作為 context（不反向傳播）**。

```
輸入格式：[INST] instruction [/INST] response [/INST]
Loss mask：[  0  ]    0     [  0  ]     1      [  0  ]

模型學會的：看到 [INST]....[/INST] 後，生成後面的 response
```

## Alpaca 格式

Stanford Alpaca 提出的格式，至今仍被廣泛使用：

```json
[
    {
        "instruction": "解釋什麼是梯度下降",
        "input": "",
        "output": "梯度下降是一種最佳化演算法..."
    },
    {
        "instruction": "翻譯以下段落成英文",
        "input": "今天天氣很好",
        "output": "The weather is very nice today."
    }
]
```

`input` 是可選的附加上下文（空字串表示沒有）。

**格式化成模型輸入**：

```python
ALPACA_TEMPLATE = """Below is an instruction that describes a task{input_part}. Write a response that appropriately completes the request.

### Instruction:
{instruction}
{input_section}
### Response:
{output}"""

def format_alpaca(sample):
    input_part = ", paired with an input" if sample["input"] else ""
    input_section = f"\n### Input:\n{sample['input']}\n" if sample["input"] else "\n"
    return ALPACA_TEMPLATE.format(
        input_part=input_part,
        instruction=sample["instruction"],
        input_section=input_section,
        output=sample["output"],
    )
```

## ChatML 格式

現代 LLM（Qwen、OpenChat 等）普遍用 ChatML，支援多輪對話：

```
<|im_start|>system
你是一個有用的台灣工程師助理。<|im_end|>
<|im_start|>user
什麼是 Transformer？<|im_end|>
<|im_start|>assistant
Transformer 是...<|im_end|>
<|im_start|>user
它有什麼優點？<|im_end|>
<|im_start|>assistant
主要優點有三個...<|im_end|>
```

```python
def format_chatml(messages):
    result = ""
    for msg in messages:
        result += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    result += "<|im_start|>assistant\n"  # 讓模型從這裡開始生成
    return result

# 訓練資料
conversations = [
    [
        {"role": "system",    "content": "你是台灣資深工程師，用繁體中文回答。"},
        {"role": "user",      "content": "什麼是 attention？"},
        {"role": "assistant", "content": "Attention 是 Transformer 的核心機制..."},
    ]
]
```

## Llama 3 / Llama 3.2 格式

```
<|begin_of_text|><|start_header_id|>system<|end_header_id|>

你是一個有用的助理。<|eot_id|><|start_header_id|>user<|end_header_id|>

你好<|eot_id|><|start_header_id|>assistant<|end_header_id|>

你好！有什麼我可以幫助你的？<|eot_id|>
```

```python
def format_llama3(messages):
    result = "<|begin_of_text|>"
    for msg in messages:
        result += f"<|start_header_id|>{msg['role']}<|end_header_id|>\n\n"
        result += msg["content"]
        result += "<|eot_id|>"
    result += "<|start_header_id|>assistant<|end_header_id|>\n\n"
    return result
```

## 高品質訓練資料的原則

### 1. 多樣性

訓練資料要覆蓋模型的預期使用場景：

```python
categories = {
    "問答": 30,      # 30% 的資料是問答
    "程式": 25,      # 25% 是寫程式
    "分析": 20,      # 20% 是分析推理
    "創作": 15,      # 15% 是創意寫作
    "翻譯": 10,      # 10% 是翻譯
}
```

### 2. 品質勝於數量

100 筆高品質資料通常優於 10000 筆低品質資料。

評估標準：
- Response 直接回應 instruction（不廢話）
- 事實正確
- 格式符合預期
- 繁體中文用詞自然（不是機翻腔）

### 3. 用 LLM 生成資料（謹慎使用）

可以用 GPT-4 或 Claude 生成大量初始資料，再人工審查：

```python
import anthropic

client = anthropic.Anthropic()

def generate_training_sample(topic):
    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"""請生成一個關於「{topic}」的 instruction tuning 訓練資料，格式如下：
{{
  "instruction": "...",
  "input": "",
  "output": "..."
}}
用繁體中文，output 要詳細且準確。"""
        }]
    )
    return message.content[0].text

# 批次生成
topics = ["梯度下降", "Transformer", "LoRA", "量化"]
samples = [generate_training_sample(t) for t in topics]
```

### 4. Loss masking

```python
def tokenize_with_mask(sample, tokenizer, max_length=512):
    # 格式化完整對話
    prompt = format_alpaca(sample)
    response_start = prompt.index("### Response:\n") + len("### Response:\n")

    full_ids = tokenizer.encode(prompt)

    # 計算 instruction 部分的長度（這部分 loss = 0）
    instruction_text = prompt[:response_start]
    instruction_len  = len(tokenizer.encode(instruction_text))

    # labels：instruction 部分是 -100（ignore），response 部分正常計算 loss
    labels = [-100] * instruction_len + full_ids[instruction_len:]
    labels = labels[:max_length]
    full_ids = full_ids[:max_length]

    return {"input_ids": full_ids, "labels": labels}
```

## 準備你自己的繁體中文 instruction 資料

```python
# 基本結構
tw_samples = [
    {
        "instruction": "解釋 Python 的 list comprehension",
        "input": "",
        "output": "List comprehension 是 Python 的語法糖，讓你用一行建立 list...\n\n```python\n# 傳統寫法\nresult = []\nfor i in range(10):\n    result.append(i**2)\n\n# List comprehension\nresult = [i**2 for i in range(10)]\n```"
    },
    # ... 更多樣本
]

# 存成 JSONL 格式
import json
with open("train.jsonl", "w", encoding="utf-8") as f:
    for sample in tw_samples:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
```

## 動手練習

準備一個 50 筆的繁體中文 instruction 資料集：

1. 20 筆：Python / 程式相關
2. 15 筆：ML / AI 概念解釋
3. 10 筆：台灣文化 / 時事
4. 5 筆：創意寫作（詩詞）

每筆都要能通過以下審查：
- instruction 清楚
- output 用繁體中文，不含簡體字
- output 長度適中（50–300 字）

## 自我檢核

- [ ] 理解 instruction tuning 的 loss masking 為什麼只算 response 部分
- [ ] 能把 Alpaca / ChatML / Llama3 格式互相轉換
- [ ] 知道高品質訓練資料的三個標準
- [ ] 準備了至少 20 筆繁體中文 instruction 資料

→ [Ch 30 RLHF / DPO 入門：偏好對齊概念](./30-rlhf-dpo.md)
