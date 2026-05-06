# Ch 4 — Agent 與 Tool Calling

> 目標：搞懂 Agent 跟 Chain 的本質差異，能寫出帶 tool 的 ReAct agent，識別 agent 的執行邊界就是攻擊面的邊界。

---

## Chain vs Agent：固定流程 vs 動態決策

```
Chain（固定流程）：
  input → step1 → step2 → step3 → output
  開發者預先定義每一步，LLM 只負責各步驟的生成

Agent（動態決策）：
  input → LLM 決定 → 用 tool A？用 tool B？不用 tool？
              ↓
          執行 tool → 拿到結果 → LLM 再決定 → ... → 回答
  LLM 自己決定要不要用 tool、用哪個、用幾次
```

這個差異在資安上意義重大：**Chain 的攻擊面是可枚舉的**（你知道它每次會做什麼），**Agent 的攻擊面不是**（LLM 可能在你沒預期的情況下呼叫某個 tool）。

---

## ReAct Loop

Agent 最常用的推理框架是 ReAct（Reason + Act）：

```
使用者問題: "台北現在幾度？如果我想換算成華氏是多少？"

Thought: 我需要知道台北的氣溫，應該用天氣工具查詢。
Action: get_weather(city="台北")
Observation: 台北目前 28°C，晴天

Thought: 拿到攝氏溫度了，現在需要換算成華氏，用計算工具。
Action: calculate(expression="28 * 9/5 + 32")
Observation: 82.4

Thought: 我有所有需要的資訊了，可以回答了。
Final Answer: 台北目前 28°C（82.4°F），晴天。
```

每一輪 Thought → Action → Observation 就是一個 iteration，agent 會一直循環直到 LLM 輸出 "Final Answer"。

---

## Tool 定義：@tool decorator

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查詢指定城市的目前天氣。城市名稱用中文或英文皆可。"""
    # 真實情境會呼叫 Weather API，這裡用假資料
    weather_data = {
        "台北": "28°C，晴天，濕度 75%",
        "高雄": "31°C，多雲，濕度 80%",
        "台中": "27°C，陰天，濕度 70%",
    }
    return weather_data.get(city, f"找不到 {city} 的天氣資料")

@tool
def calculate(expression: str) -> str:
    """計算數學算式，支援加減乘除和括號。例如：2 * (3 + 4)"""
    allowed_chars = set("0123456789 +-*/().")
    if not all(c in allowed_chars for c in expression):
        return "錯誤：expression 包含不允許的字元"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"計算失敗: {e}"

# 查看 tool 的 schema（LLM 就是用這個決定怎麼呼叫）
print(get_weather.name)         # get_weather
print(get_weather.description)  # 查詢指定城市的目前天氣...
print(get_weather.args)         # {'city': {'title': 'City', 'type': 'string'}}
```

LLM 拿到所有 tool 的 name、description、args schema，再根據使用者問題決定呼叫哪個。**description 寫得好不好直接影響 agent 能不能正確選 tool**——這也是攻擊者可以利用的：如果攻擊者能影響 tool description（例如透過 RAG 撈到惡意內容），就可能誘導 agent 呼叫預期外的 tool。

---

## 完整 Agent 範例

```python
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_react_agent, AgentExecutor
from langchain import hub

@tool
def get_weather(city: str) -> str:
    """查詢指定城市的目前天氣。城市名稱用繁體中文。"""
    weather_data = {
        "台北": "28°C，晴天，濕度 75%",
        "高雄": "31°C，多雲，濕度 80%",
        "台中": "27°C，陰天，濕度 70%",
    }
    return weather_data.get(city, f"找不到 {city} 的天氣資料")

@tool
def calculate(expression: str) -> str:
    """計算數學表達式。只支援數字和 +, -, *, /, (, ) 符號。"""
    allowed_chars = set("0123456789 +-*/().")
    if not all(c in allowed_chars for c in expression):
        return "錯誤：expression 包含不允許的字元"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"計算失敗: {e}"

tools = [get_weather, calculate]
llm = ChatOllama(model="llama3.2", temperature=0)

# 從 LangChain hub 拉 ReAct prompt template（需網路）
react_prompt = hub.pull("hwchase17/react")

agent = create_react_agent(llm, tools, react_prompt)
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,      # 印出每一步 Thought/Action/Observation
    max_iterations=5,  # 最多跑 5 輪，防止無限 loop
    handle_parsing_errors=True,
)

result = agent_executor.invoke({
    "input": "台北現在幾度？換算成華氏是多少？"
})
print(result["output"])
```

執行輸出（verbose=True 的樣子）：

```
> Entering new AgentExecutor chain...
Thought: 我需要查詢台北的天氣
Action: get_weather
Action Input: 台北
Observation: 28°C，晴天，濕度 75%
Thought: 現在需要把 28°C 換算成華氏
Action: calculate
Action Input: 28 * 9/5 + 32
Observation: 28 * 9/5 + 32 = 82.4
Thought: 我有了所有資訊
Final Answer: 台北目前 28°C（82.4°F），晴天，濕度 75%。
> Finished chain.
```

---

## 執行邊界 = 攻擊面

Agent 能做什麼，取決於你給了它哪些 tool。這個邊界就是攻擊者的目標：

```
低風險 tool（唯讀、無副作用）：
  - get_weather(city)      → 只讀外部 API
  - search_docs(query)     → 只讀內部文件

高風險 tool（有副作用、能執行任意動作）：
  - execute_shell(cmd)     → 直接 RCE
  - send_email(to, body)   → 外部通訊
  - write_file(path, data) → 寫入檔案系統
  - http_request(url, ...)  → 任意 HTTP 請求（SSRF）
```

攻擊者若能透過 prompt injection 控制 agent 的 Thought，就能讓它呼叫高風險 tool 執行惡意動作。這在 Ch 11 會完整展開。

---

## 最小權限原則在 Agent 設計的意義

傳統系統的最小權限原則（Principle of Least Privilege）在 agent 設計完全適用：

| 原則 | Agent 的對應做法 |
|---|---|
| 只給必要權限 | 只定義業務邏輯需要的 tool，絕不加「方便」但高風險的 tool |
| 限制操作範圍 | tool 內部加白名單，例如 `write_file` 只能寫特定目錄 |
| 操作可稽核 | 所有 tool 呼叫要 log，包含參數和回傳值 |
| 危險操作需確認 | 不可逆操作（刪除、傳送）在 tool 內加 confirmation 機制 |
| 不信任 LLM 輸出 | tool 的參數不管 LLM 怎麼填，都要在 tool 內驗證 |

**最重要的一條**：LLM 輸出不可信。Tool 要自己驗證輸入，不能假設 LLM 只會傳合理的參數。攻擊者控制的惡意 prompt 可能讓 LLM 傳 `{"path": "../../../etc/passwd", "data": "..."}` 給你的 `write_file` tool。

---

## 自我檢核

- [ ] 說得清楚 Chain 和 Agent 的決策模式差異
- [ ] 能畫出 ReAct 的 Thought → Action → Observation 循環
- [ ] 能用 `@tool` decorator 定義一個帶 type hint 和 docstring 的 tool
- [ ] 知道 `max_iterations` 為什麼不能省
- [ ] 說得出「最小權限原則」在 agent tool 設計上的三個具體做法

有了 Agent 的概念，補上最後一塊：AI 服務怎麼用 FastAPI 暴露出去，以及 Pydantic 在哪裡擋住亂七八糟的輸入。

→ [Ch 5 — Pydantic + FastAPI](./05-pydantic-fastapi.md)
