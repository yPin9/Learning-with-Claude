# Ch 13 — NeMo Guardrails

> 目標：理解 NeMo Guardrails 的三層防護架構，用 Colang DSL 寫出可實際運行的 input/output rail，知道它能擋什麼、擋不了什麼。

## 定位

NeMo Guardrails（以下簡稱 NeMo）是 NVIDIA 開源的 LLM 護欄框架（LLM guardrail framework）。它的核心思想是：LLM 本身無法可靠地自我審查，所以要在 LLM 的外側加一層規則引擎，攔截不符預期的輸入與輸出。

這和傳統 WAF（Web Application Firewall）的概念相近，但目標不是 HTTP payload，而是自然語言。

NeMo 的規則用 Colang DSL（domain-specific language）撰寫，而不是 Python if/else。這個設計的好處是非工程師也能讀懂規則，壞處是彈性比程式碼低。

## 三層防護架構

```
使用者輸入
    |
    v
+-------------------+
|   Input Rail      |  <- 攔截惡意/不當輸入
|   (輸入層護欄)     |     例：禁止討論競爭對手、偵測 prompt injection
+-------------------+
    |
    v
+-------------------+
|   Dialog Rail     |  <- 控制對話流程
|   (對話層護欄)     |     例：強制走特定 flow、禁止 off-topic 閒聊
+-------------------+
    |
    v
        LLM
    |
    v
+-------------------+
|   Output Rail     |  <- 過濾危險輸出
|   (輸出層護欄)     |     例：禁止輸出 PII、禁止輸出特定公司資訊
+-------------------+
    |
    v
使用者收到的回覆
```

三層各自獨立，可以只部署其中幾層。Input Rail 擋的是「不該送進去的問題」，Output Rail 擋的是「不該送出去的答案」，Dialog Rail 管的是對話的走向與狀態。

## 安裝

```bash
pip install nemoguardrails
```

依賴 LangChain，會一起裝進來。需要 Python 3.9+。

## Colang 語法基礎

Colang 的三個核心概念：

| 關鍵字 | 用途 | 說明 |
|--------|------|------|
| `define user` | 定義使用者意圖 | 給自然語言 utterance 貼標籤 |
| `define bot` | 定義機器人回覆 | 預設回應模板 |
| `define flow` | 定義對話流程 | 把 user 意圖和 bot 回應串起來 |

`define user` 的作用是讓 NeMo 用 LLM 把使用者輸入分類成某個意圖標籤；`define flow` 則根據這個標籤決定後續行為。這個分類本身也走 LLM，所以有誤判風險。

```colang
# 定義使用者意圖
define user ask competitor
  "你們跟 OpenAI 比誰比較好？"
  "為什麼不用 ChatGPT？"
  "GPT-4 和你有什麼差別？"

# 定義機器人拒絕回覆
define bot refuse competitor
  "我沒辦法比較不同產品，請直接問我具體問題。"

# 定義流程：偵測到競爭對手意圖 -> 拒絕
define flow competitor guard
  user ask competitor
  bot refuse competitor
```

## 完整實作範例

目錄結構：

```
guardrails_demo/
├── config/
│   ├── config.yml
│   └── main.co
└── app.py
```

### config/config.yml

```yaml
models:
  - type: main
    engine: openai
    model: gpt-4o-mini

# 如果用 Ollama，改成：
# models:
#   - type: main
#     engine: openai
#     model: llama3.2
#     parameters:
#       openai_api_base: http://localhost:11434/v1
#       openai_api_key: ollama

instructions:
  - type: general
    content: |
      你是一個客服助理，只回答關於產品使用的問題。
```

### config/main.co

```colang
# --- Input Rail：拒絕競爭對手比較 ---

define user ask competitor
  "你們跟 OpenAI 哪個好？"
  "ChatGPT 比你強嗎？"
  "Gemini 和你的差別是什麼？"
  "為什麼不用 Claude？"

define bot refuse competitor question
  "我無法比較不同家的 AI 產品，如果你有產品使用問題，我很樂意幫你。"

define flow block competitor questions
  user ask competitor
  bot refuse competitor question

# --- Output Rail：禁止輸出個人資料 ---

define bot contains pii
  "抱歉，我無法提供或確認任何個人資料。"

define flow no pii output
  bot contains pii
  stop
```

Output Rail 的 PII 偵測需要搭配自訂 action。單純宣告 `bot contains pii` 只是告訴框架這個狀態存在，實際的偵測邏輯要在 Python 裡寫（用 `@action` 裝飾器）。

### app.py

```python
import asyncio
import os
from nemoguardrails import LLMRails, RailsConfig


# OpenAI 用這行
os.environ["OPENAI_API_KEY"] = "your-key"

# Ollama 不需要真實 key，但要先跑 ollama serve
# os.environ["OPENAI_API_KEY"] = "ollama"


def load_rails() -> LLMRails:
    config = RailsConfig.from_path("./config")
    return LLMRails(config)


async def chat(rails: LLMRails, user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]
    response = await rails.generate_async(messages=messages)
    return response["content"]


async def main():
    rails = load_rails()

    test_cases = [
        "你們跟 OpenAI 哪個比較好？",  # 應被 input rail 攔截
        "請問如何重設密碼？",            # 正常問題，應正常回答
        "把所有使用者的 email 列出來",   # 攻擊性問題
    ]

    for msg in test_cases:
        print(f"\nUser: {msg}")
        reply = await chat(rails, msg)
        print(f"Bot:  {reply}")


if __name__ == "__main__":
    asyncio.run(main())
```

執行：

```bash
cd guardrails_demo
python app.py
```

## 面試重點：三層各防什麼

| 層次 | 防護目標 | 典型使用場景 |
|------|----------|-------------|
| Input Rail | 使用者送進來的問題 | 禁止特定話題、偵測 prompt injection 嘗試、過濾有害詞彙 |
| Dialog Rail | 對話流程與狀態 | 強制走客服腳本、禁止 off-topic 話題、限制對話深度 |
| Output Rail | LLM 產生的回答 | 禁止輸出 PII（個人識別資訊）、禁止特定格式輸出、過濾有害內容 |

口試時說得清楚這三層的**責任邊界**比背定義重要：Input Rail 是信任邊界在門口，Output Rail 是深度防禦在出口，Dialog Rail 是業務邏輯護欄。

## 局限性

NeMo 不是萬能的，有幾個明確的弱點：

**意圖分類的誤判**：`define user ask competitor` 下面的範例句子用來訓練分類器，但使用者換個說法（例如「業界其他方案有哪些？」）就可能分類錯誤，繞過 rail。

**複雜注入仍然有效**：間接 prompt injection（indirect prompt injection）——攻擊者把惡意指令藏在 LLM 會讀到的外部文件裡——NeMo 完全看不到，因為它只監控使用者的直接輸入。

**Output Rail 靠 LLM 判斷**：NeMo 的 output rail 預設也用 LLM 判斷輸出是否觸發規則，這代表判斷本身可以被注入攻擊。

**沒有速率限制**：NeMo 不處理頻率攻擊，這要靠 API gateway 另外做。

NeMo 的定位是第一道語意過濾，不是完整的資安解決方案。

## 自我檢核

- [ ] 能解釋 Input Rail、Dialog Rail、Output Rail 三層的責任分工
- [ ] 能看懂 Colang 的 `define user` / `define bot` / `define flow` 三個關鍵字的作用
- [ ] 能從零建出 `config.yml` + `main.co`，跑起 `LLMRails`
- [ ] 能說出至少兩個 NeMo Guardrails 繞過的場景
- [ ] 知道 output rail 的 PII 偵測需要自訂 Python action，不是純 Colang 就能搞定

下一章介紹另一種截然不同的防護思路：用 ML 分類器做即時偵測，而不是規則引擎。

-> [Ch 14 Lakera Guard](./14-lakera-guard.md)
