# 練習 A — 寫一個能跑的 mini agent loop

> **目標**：把 Part 1（Ch 4–9）學到的東西全部拼起來，**從零**寫出一個你自己的、能跑的命令列 agent：它能多輪對話、會用工具、正確處理各種 `stop_reason`、工具出錯不崩潰、API 出錯能優雅降級、並有 `max_turns` 剎車。完成後你就擁有一個能往後面所有章節上加東西的骨架。

## 背景與動機

到目前為止，每一章都給你一塊拼圖：Ch 4 的迴圈與工具註冊表、Ch 5 的協議、Ch 6 的有狀態 class、Ch 7 的 stop_reason 處理、Ch 8 的串流、Ch 9 的錯誤處理。但你一直是「看著教材的片段」在學。這個練習要你**闔上書、自己把它們組起來**——這是檢驗你是否真的內化的唯一方法。能獨立寫出來，Part 1 才算真的學會；只能對著解答抄，代表還沒。

我們要做的東西很具體：一個叫 `mini-agent` 的 CLI 程式。你在終端機跟它對話，它是一個帶幾個實用工具的小助理。這不是玩具——它的架構跟真實的 coding agent、客服 agent 是同一套，只是工具少一點。

## 任務規格

寫一個 Python 程式 `mini_agent.py`，滿足以下規格：

**核心行為**
- 啟動後進入一個 REPL（讀取-執行-印出迴圈）：使用者打字 → agent 回應 → 等下一句，直到使用者輸入 `exit` 或 `quit` 離開。
- **跨輪記憶**：第二句以後可以引用前面講過的內容（用 Ch 6 的有狀態 class）。
- 輸入 `reset` 可清空對話記憶、重新開始一段新對話。

**工具（至少實作這三個 client-side 工具）**
1. `get_current_time()`：回傳現在的日期時間（用 Python 的 `datetime`）。無參數。
2. `calculate(expression: str)`：計算一個數學算式字串，回傳結果。**必須安全地求值**（不准用裸 `eval`，見「如果你卡住了」）。
3. `read_text_file(path: str)`：讀取一個文字檔的內容回傳。檔案不存在要回有意義的錯誤（而不是讓程式崩潰）。

**loop 控制（Ch 7）**
- 正確處理 `stop_reason`：`tool_use` 繼續、`end_turn` 回傳、`max_tokens` 提示截斷、未知的走安全 fallback。
- 有 `max_turns` 剎車（建議 10），達上限要回有意義的訊息。

**韌性（Ch 9）**
- **工具錯誤**（檔案不存在、算式非法、函式丟例外）一律包成 `is_error` 的 tool_result 回給模型，**絕不**讓單一工具錯誤導致整個程式崩潰。
- **API 錯誤**：catch 具體類別（不要裸 `except Exception`）。永久性錯誤（4xx）誠實回報、暫時性錯誤（429/5xx）給友善的「稍後再試」。

**禁止**
- 不准用任何 agent 框架（LangChain、Claude Agent SDK 等）。整個 loop 要你自己寫——這正是練習的重點。
- 不准用裸 `eval()` 實作 `calculate`（安全問題，見提示）。
- 不准用裸 `except Exception: continue`（Ch 9 的頭號反模式）。

**可選加分**
- 用 Ch 8 的串流，讓模型的文字逐字顯示。
- 用 Ch 7 的重複偵測，打斷鬼打牆。

## 期望輸出範例

```
$ python mini_agent.py
mini-agent 已啟動（輸入 exit 離開，reset 重來）

你> 現在幾點？
[工具] get_current_time()
agent> 現在是 2026-06-05 14:32:10。

你> 幫我算 (128 * 4) + 17
[工具] calculate(expression="(128 * 4) + 17")
agent> (128 * 4) + 17 = 529。

你> 剛剛那個結果再乘以 2
[工具] calculate(expression="529 * 2")
agent> 529 乘以 2 等於 1058。   ← 注意它記得「剛剛那個結果」是 529

你> 讀一下 notes.txt
[工具] read_text_file(path="notes.txt")
agent> 檔案 notes.txt 不存在，請確認路徑是否正確。   ← 工具錯誤被優雅處理，沒崩潰

你> exit
掰掰。
```

邊界情況該有的行為：

```
你> 算 1 / 0
[工具] calculate(expression="1 / 0")
agent> 這個算式無法計算（除以零），要不要換一個？   ← 工具丟例外 → is_error → 模型優雅回應
```

```
你> reset
（已清空對話記憶）
你> 我剛剛問你什麼？
agent> 抱歉，我沒有先前的對話記錄。   ← reset 後記憶確實清空
```

## 如果你卡住了

1. **不知道從哪開始**：先不要管工具和錯誤處理。先寫一個「只會聊天、沒有工具」的 REPL——`while True: 讀輸入 → 呼叫模型 → 印回應`。跑通了再一塊一塊加工具、加 stop_reason 處理、加錯誤處理。**別想一次寫對所有東西。**
2. **`calculate` 怎麼安全求值**：裸 `eval("1+1")` 能算，但 `eval("__import__('os').system('rm -rf /')")` 也能跑——這是嚴重漏洞。安全做法是用 Python 的 `ast` 模組：`ast.parse(expr, mode="eval")` 解析成語法樹，然後只允許數字與 `+ - * / ** % ()` 這幾種節點，遇到其他節點（函式呼叫、屬性存取、名稱）就拒絕。這裡的「安全」精確的意思是**杜絕任意程式碼執行（RCE）**——它擋的是 `__import__` 那類攻擊。它**不**處理資源耗盡（例如 `9**9**9` 這種超大次方會吃光 CPU/記憶體）；生產級的計算器還要再加算式長度、數字大小、次方上限等限制。對本練習，擋住 RCE 就達標，但你要知道「安全」有這兩個層次。這逼你思考「工具的安全邊界」——正是 Ch 22、Ch 25 的前哨。
3. **多輪記憶做不出來**：回去看 Ch 6。關鍵是 `self.messages` 是 instance 屬性、`chat()` 之間不重置。如果你每次都 `messages = []`，就是 Ch 6 講的健忘症。
4. **工具錯誤還是會讓程式崩潰**：檢查你的工具執行有沒有包 try/except，並把例外轉成 `is_error` tool_result（Ch 4、Ch 9）。例外不該往上拋到 loop 外。
5. **API 錯誤不知道 catch 哪些**：回去看 Ch 9 的三分類。最少要 catch `anthropic.APIStatusError`（看 `status_code` 分永久/暫時）和 `anthropic.APIConnectionError`。
6. **`reset` 怎麼做**：清空 `self.messages` 即可（Ch 6 的 `reset()`）。

## 實作步驟建議

### Step 1：能聊天的空殼 REPL
先寫一個 `Agent` class，只有 `chat(user_input)`：append user → 呼叫模型 → append assistant → 回傳文字。外面包一個 `while` 讀輸入。**先確認多輪記憶會動**（問它「我上一句說什麼」）。這步沒有工具、沒有錯誤處理。

### Step 2：加入工具註冊表與單一工具
照 Ch 4，建 `TOOL_SCHEMAS` + `TOOL_FUNCTIONS`，先只放 `get_current_time`。把 `chat()` 改成會看 `stop_reason == "tool_use"` 並執行工具的迴圈。確認 agent 會在被問時間時呼叫工具。

### Step 3：補齊三個工具，處理多工具與工具錯誤
加上 `calculate`（用 `ast` 安全求值）和 `read_text_file`。寫 `run_tool_uses`（Ch 4）處理「一輪多工具」並把例外包成 `is_error`。測試「讀不存在的檔」「除以零」不會讓程式崩潰。

### Step 4：完整的 stop_reason 處理與 max_turns
把 Ch 7 的 handler 補進去：`max_tokens` 提示截斷、未知 reason 安全 fallback、`max_turns` 剎車。

### Step 5：API 錯誤處理與收尾
照 Ch 9 包上 try/except（具體類別），加 `reset` 指令、`exit`/`quit` 離開、友善的工具呼叫提示輸出。（可選）接上 Ch 8 串流。

## 完整參考解答

**先自己寫完再看！** 對著解答抄，你會以為自己懂了，但闔上書還是寫不出來。

<details>
<summary>點開參考實作</summary>

```python
# mini_agent.py
import ast
import operator
import datetime
import anthropic

client = anthropic.Anthropic(max_retries=2, timeout=60.0)
MODEL = "claude-opus-4-8"

# ---------- 工具實作 ----------

def get_current_time() -> str:
    """回傳現在的日期與時間。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 安全求值：只允許數字與基本算術，杜絕 eval 的任意程式碼執行風險
_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    # 注意 bool 是 int 的子類，要明確排除，否則 True/False 會被當數字
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval(node.operand))
    raise ValueError("不允許的運算式")

def calculate(expression: str) -> str:
    """計算一個數學算式（只支援 + - * / ** % 與括號）。"""
    tree = ast.parse(expression, mode="eval")     # 非法語法會丟 SyntaxError
    return str(_safe_eval(tree))                   # 除以零會丟 ZeroDivisionError


def read_text_file(path: str) -> str:
    """讀取一個文字檔的內容。"""
    with open(path, "r", encoding="utf-8") as f:   # 不存在會丟 FileNotFoundError
        return f.read()


# ---------- 工具兩面：schema + 分派表 ----------

TOOL_SCHEMAS = [
    {
        "name": "get_current_time",
        "description": "取得現在的日期與時間。當使用者問現在幾點、今天日期時使用。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calculate",
        "description": "計算數學算式並回傳精確結果。需要算數時用此工具，不要自己心算。"
                       "只支援數字與 + - * / ** % 和括號。",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "要計算的算式，例如 (3+4)*2"}},
            "required": ["expression"],
        },
    },
    {
        "name": "read_text_file",
        "description": "讀取指定路徑的文字檔內容。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "檔案路徑"}},
            "required": ["path"],
        },
    },
]
TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
    "calculate": calculate,
    "read_text_file": read_text_file,
}


def run_tool_uses(content_blocks):
    """執行這一輪所有 tool_use，回傳一則裝了所有 tool_result 的 user 訊息。"""
    results = []
    for block in content_blocks:
        if block.type != "tool_use":
            continue
        print(f"[工具] {block.name}({', '.join(f'{k}={v!r}' for k, v in block.input.items())})")
        func = TOOL_FUNCTIONS.get(block.name)
        if func is None:
            text, is_error = f"沒有名為 {block.name} 的工具", True
        else:
            try:
                text, is_error = str(func(**block.input)), False
            except Exception as e:
                text, is_error = f"工具執行失敗：{type(e).__name__}: {e}", True
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": text,
            "is_error": is_error,
        })
    return {"role": "user", "content": results}


# ---------- Agent ----------

class Agent:
    def __init__(self, system_prompt="你是一個簡潔、誠實的命令列助理。需要精確計算或即時資訊時請使用工具。"):
        self.system_prompt = system_prompt
        self.messages = []
        self.max_turns = 10

    def reset(self):
        self.messages = []

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            try:
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=2048,
                    system=self.system_prompt,
                    tools=TOOL_SCHEMAS,
                    messages=self.messages,
                )
            except anthropic.APIStatusError as e:
                if e.status_code == 429 or e.status_code >= 500:
                    return "（伺服器忙碌，已重試多次仍失敗，請稍後再試）"
                return f"（請求錯誤 {e.status_code}，可能是程式設定問題）"
            except anthropic.APIConnectionError:
                return "（無法連線，請檢查網路後再試）"

            self.messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text")

            if resp.stop_reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue
            if resp.stop_reason == "end_turn":
                return text
            if resp.stop_reason == "max_tokens":
                return text + "\n（⚠️ 回應因長度上限被截斷）"
            return text + f"\n（收到未預期的 stop_reason: {resp.stop_reason}）"

        return "（達到最大回合數上限，未能完成）"


# ---------- CLI ----------

def main():
    agent = Agent()
    print("mini-agent 已啟動（輸入 exit 離開，reset 重來）\n")
    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n掰掰。")
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("掰掰。")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("（已清空對話記憶）")
            continue
        print("agent>", agent.chat(user_input))


if __name__ == "__main__":
    main()
```

**解答說明**：

- **`calculate` 用 `ast` 而非 `eval`**：`ast.parse(..., mode="eval")` 把算式變成語法樹，`_safe_eval` 只放行數字、二元/一元算術節點，碰到函式呼叫、名稱、屬性存取一律 `ValueError`。這擋掉了 `eval` 的任意程式碼執行。非法語法（`SyntaxError`）與除以零（`ZeroDivisionError`）都會自然往上丟，然後在 `run_tool_uses` 被包成 `is_error`。
- **工具錯誤集中在 `run_tool_uses` 處理**：所有工具例外（`FileNotFoundError`、`ValueError`、`SyntaxError`、`ZeroDivisionError`…）都被 `except Exception` 接住——**注意這個 except 是在「工具執行」這層、且把錯誤轉成資料回給模型，這是正當用法**，跟 Ch 9 罵的「在 loop 層裸 except 吞掉一切」完全不同。差別在於：這裡 catch 完有明確處理（轉成 tool_result），而且不影響 loop 控制流程。
- **API 錯誤在 `chat` 層 catch 具體類別**：`APIStatusError` 看 `status_code` 分永久/暫時，`APIConnectionError` 單獨處理。沒有裸 except。
- **多輪記憶 = `self.messages` 不重置**；`reset()` 清空它。
- **`stop_reason` 完整分流**：tool_use / end_turn / max_tokens / 未知 fallback，對齊 Ch 7。

</details>

## 測試用例

| 輸入序列 | 預期行為 | 驗證了什麼 |
|---|---|---|
| 「現在幾點」 | 呼叫 `get_current_time`，回現在時間 | 無參數工具、stop_reason=tool_use 流程 |
| 「算 (10+5)*3」 | 呼叫 `calculate`，回 45 | 帶參數工具、安全求值 |
| 「剛剛的結果加 100」 | 回 145（記得上一輪是 45） | 多輪記憶 |
| 「算 1/0」 | 工具回除零錯誤，模型優雅說明，程式不崩潰 | 工具錯誤 → is_error |
| 「算 `__import__('os')`」 | 工具拒絕（不允許的運算式），不執行任何系統呼叫 | 安全邊界 |
| 「讀 不存在的檔.txt」 | 工具回檔案不存在，模型轉述，不崩潰 | 工具錯誤處理 |
| `reset` 後問「我剛剛問什麼」 | 答不出來（記憶已清空） | reset 正確性 |
| `exit` | 程式結束 | REPL 收尾 |

逐一跑過這些，全部符合，你的 mini-agent 就合格了。

## 延伸挑戰（加分）

1. **接上串流（Ch 8）**：把 `create()` 換成 `stream()` + `get_final_message()`，讓 agent 回應逐字顯示。注意 loop 控制邏輯應該一行都不用改。
2. **加重複偵測（Ch 7）**：寫一個會反覆回「尚未完成」的假工具，讓 agent 用它，並實作「連續 3 次相同呼叫就注入提示打斷」。記得仍要先回 tool_result 再注入。
3. **token 預算剎車（Ch 7、Ch 37 前哨）**：累加每輪 `usage`，超過預算就停。觀察它和 `max_turns` 哪個先觸發。
4. **持久化對話（Ch 6、Ch 39 前哨）**：把 `self.messages` 存成 JSON 檔，程式重啟後能載回來接續對話。會踩到「tool_use/tool_result 成對」「角色交替」的序列化坑——正好複習 Ch 6。
5. **加一個有副作用的工具**（例如 `write_text_file`），然後思考：它該不該在執行前問使用者確認？這直接帶你進 Ch 25 的 permission 主題。

## 自我檢核

- [ ] 我能不看教材、從零寫出這個 loop 的骨架（工具註冊表 + stop_reason 分流 + 多輪記憶）
- [ ] 我的 `calculate` 用 `ast` 安全求值，擋得住 `__import__` 這類攻擊
- [ ] 工具丟例外時，我的 agent 不崩潰，而是把錯誤回給模型讓它反應
- [ ] 我的 API 錯誤處理 catch 的是具體類別，沒有裸 `except Exception`
- [ ] 我能說出我的 `run_tool_uses` 裡那個 `except Exception` 為什麼是正當的、跟 Ch 9 罵的反模式差在哪
- [ ] 我能解釋我的程式哪幾行對應到 Part 1 的哪一章

寫完並跑過所有測試用例後，你就有了一個真正的 agent 骨架。Part 2 開始，我們要面對它最大的隱憂：那個只增不減、遲早撐爆 context window 的 `messages`——是時候學怎麼科學地管理它了。

→ [Ch 10 Context window 是稀缺資源](./10-context-window-budget.md)
