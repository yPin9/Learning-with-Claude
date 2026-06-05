# Ch 4 — 最小可行 agent loop

> **目標**：把 Ch 2 那個你手動 append、手動再呼叫的流程，寫成一個真正會自己轉的 `while` 迴圈。讀完你會有一個**能跑、能用多個工具、能自己跑好幾回合**的最小 agent——大約 60 行 Python——並且看懂它的每一行為什麼存在。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續 Ch 0 的 `client = Anthropic()`。本章的工具用本地 Python 函式，不需要外部服務。

## 為什麼這章是整門課的轉捩點

前面三章你都在「看」與「手動操作」。這章你第一次**把 harness 寫出來**。一旦這個迴圈會自己轉，你就擁有了一個真正的 agent 骨架——之後每一章（context 壓縮、工具設計、permission、subagent……）都是在這個骨架上加東西。所以這 60 行值得你逐行讀懂，而不是複製貼上跑過就算。

我們的策略：先把 Ch 2 的手動步驟「原地」翻成迴圈，看到它動；然後拆解每個設計決策，特別是 Ch 2 留下的兩個坑（多工具、stop_reason 不只兩種）這次要正面處理。

## 先建立直覺：迴圈就是「自動化你在 Ch 2 手做的事」

Ch 2 你做了什麼？送請求 → 看回應 → 如果要工具就執行並 append → 再送一次。你是用人腦在判斷「還要不要再送一次」。迴圈做的事一模一樣，只是把那個判斷交給一個 `while`：

```
   ┌─────────────────────────────────────────────┐
   │  messages（一路累積，就是 agent 的記憶）        │
   └─────────────────────────────────────────────┘
                     │
       ┌─────────────▼──────────────┐
       │  呼叫模型(messages, tools)   │ ◀──────────┐
       └─────────────┬──────────────┘             │
                     ▼                             │
            看 resp.stop_reason                    │
          ┌──────────┴───────────┐                │
          ▼                      ▼                 │
   == "tool_use"            其他（end_turn…）        │
          │                      │                 │
   執行所有 tool_use          回傳最終答案           │
   把結果 append 回 messages    break ───────────▶ 結束
          │                                        │
          └─── append 後回到迴圈頂 ─────────────────┘
```

整個 agent 的「自主」就藏在這張圖的那條回邊（loop back）裡：只要模型還在要工具，迴圈就不停；一旦它說 `end_turn`，迴圈就停。**你不需要事先知道任務要跑幾回合**——模型每一輪自己決定，這正是 Ch 1 說的「控制流程由模型擁有」。

## Step 1：先把工具收進一個「註冊表」

Ch 2 我們偷懶把工具寫死成 `get_weather`。但真正的 harness 會有多個工具，迴圈必須能「根據模型給的 `name`，找到對應的函式去執行」。所以我們需要一份對照表——把「給模型看的 schema」和「實際執行的函式」綁在一起。

```python
from anthropic import Anthropic

client = Anthropic()

# --- 工具的兩面：schema（給模型看）+ 實作（harness 執行）---

def get_weather(city: str) -> str:
    fake_db = {"Taipei": "晴，攝氏 28 度", "Tokyo": "多雲，攝氏 22 度"}
    return fake_db.get(city, f"查無 {city} 的天氣資料")

def add(a: float, b: float) -> str:
    return str(a + b)

# schema 清單：送給模型的「能力說明書」
TOOL_SCHEMAS = [
    {
        "name": "get_weather",
        "description": "查詢指定城市目前的天氣，回傳攝氏溫度與狀況。",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名稱，例如 Taipei"}},
            "required": ["city"],
        },
    },
    {
        "name": "add",
        "description": "把兩個數字相加，回傳總和。需要精確計算時使用，不要自己心算。",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]

# 名字 → 函式 的對照表，迴圈靠它做分派（dispatch）
TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "add": add,
}
```

這個「`TOOL_SCHEMAS`（宣告面）＋ `TOOL_FUNCTIONS`（執行面）」的拆法，就是 Ch 3 講的工具兩面。把名字對應到函式的這個 dict，是迴圈能「自動分派」的關鍵——模型回 `name="add"`，我們就 `TOOL_FUNCTIONS["add"]` 拿到函式。Ch 5 會把這套做得更嚴謹，現在這樣剛好夠。

## Step 2：寫一個「執行一輪所有工具請求」的函式

Ch 2 踩雷第 5 點說過：模型一個回應裡可能有**多個** `tool_use` 區塊，而且正確的回覆是**一則** user 訊息裝**所有** tool_result。我們把這個邏輯獨立成一個函式，迴圈本體才會乾淨：

```python
def run_tool_uses(content_blocks) -> dict:
    """執行這一輪所有的 tool_use 區塊，回傳一則要 append 的 user 訊息。"""
    tool_results = []
    for block in content_blocks:
        if block.type != "tool_use":
            continue  # 跳過 text 等其他區塊
        func = TOOL_FUNCTIONS.get(block.name)
        if func is None:
            # 模型要求了一個不存在的工具——把錯誤回給它，讓它自己修正
            result = f"錯誤：沒有名為 {block.name} 的工具"
            is_error = True
        else:
            try:
                result = func(**block.input)
                is_error = False
            except Exception as e:
                # 工具執行炸了，也把錯誤回給模型，而不是讓整個 agent 崩潰
                result = f"工具 {block.name} 執行失敗：{e}"
                is_error = True
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,      # 對上這次呼叫（Ch 2 強調過）
            "content": str(result),
            "is_error": is_error,
        })
    return {"role": "user", "content": tool_results}
```

兩個設計重點，現在就建立習慣：

1. **一輪可能多個工具，全部跑完、結果裝進同一則 user 訊息**。`tool_results` 是個 list，迴圈跑完才一次 append。這正是 Ch 2 答應補上的正確形狀。
2. **工具失敗不該讓 agent 崩潰**。工具不存在、或執行丟例外，我們都把錯誤**當成工具結果回給模型**（`is_error: True`），讓模型有機會自己換個方式。如果這裡直接讓例外往上炸，整個 agent 就死了——這是初學者最常見的脆弱寫法。Ch 9 會把錯誤處理講透，這裡先種下「錯誤是給模型的資訊，不是終止訊號」的觀念。

> `is_error` 欄位告訴模型「這次工具結果代表失敗」。它是選填的，但加上去能讓模型更聰明地反應（例如換參數重試），是好習慣。

## Step 3：迴圈本體

萬事俱備。把 Ch 2 的手動流程包成 `while`：

```python
def run_agent(user_input: str, max_turns: int = 10) -> str:
    messages = [{"role": "user", "content": user_input}]

    for turn in range(max_turns):
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            tools=TOOL_SCHEMAS,          # 每一輪都要帶（Ch 2 踩雷第 4 點）
            messages=messages,
        )

        # 把模型這一輪的完整回應接回歷史（含 text + 任何 tool_use）
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "tool_use":
            # 模型要工具：執行，把結果 append，回到迴圈頂再問
            tool_result_message = run_tool_uses(resp.content)
            messages.append(tool_result_message)
            continue

        # 其他 stop_reason：最小版本一律當「結束」處理並回傳文字。
        # 注意這是簡化——嚴格說只有 end_turn 是「正常講完」；
        # max_tokens（被長度截斷、其實沒講完）、pause_turn 等需要不同處理，留到 Ch 7。
        final_text = "".join(b.text for b in resp.content if b.type == "text")
        return final_text

    # 跑滿 max_turns 還沒結束——強制停，避免無限迴圈
    return "（達到最大回合數上限，agent 未能在限定回合內完成任務）"
```

跑跑看：

```python
print(run_agent("台北現在幾度？再幫我算 28 加 22 等於多少。"))
```

可能的輸出（模型會先後要 `get_weather` 和 `add` 兩個工具，跑了三回合才結束）：

```
台北現在是攝氏 28 度（晴天）。28 加 22 等於 50。
```

**這就是一個能跑的 agent。** 它自己決定要用哪些工具、用幾次、什麼時候算完成。你沒有寫任何「先查天氣再算加法」的流程——那是模型當場排的。

## 逐行解剖：每個決策為什麼這樣下

這 60 行裡每個看似隨意的選擇，其實都在回答一個真實問題。逐一看：

### 為什麼用 `for turn in range(max_turns)` 而不是 `while True`？

因為 `while True` 配上「模型一直要工具」會變成**無限迴圈**——而且是會燒錢的無限迴圈（每一圈都是一次付費 API 呼叫）。模型有時會卡在「反覆呼叫同一個工具」的迴圈裡（Ch 38 會看到這種失敗模式）。`max_turns` 是一道最基本的保險絲。**這是 policy（Ch 3）在最小 harness 裡的第一次現身**：「最多跑幾輪」就是一條停止策略。Ch 7 會把停止條件做得更細。

### 為什麼每輪都把 `resp.content` 原樣 append？

因為模型無狀態（Ch 1）。它下一輪要看到「自己上一輪要求了什麼工具」，才知道現在拿到的 tool_result 對應什麼。漏掉這步，API 會因為「tool_result 找不到對應的 tool_use」而報錯（Ch 2 踩雷第 2 點）。`messages` 這個一路變長的 list，就是 agent 的全部記憶。

### 為什麼判斷 `stop_reason == "tool_use"`，而不是判斷「content 裡有沒有 tool_use」？

兩者通常一致，但 `stop_reason` 是模型**明確的意圖訊號**，比我們自己翻 content 去猜更可靠。養成「用 stop_reason 當紅綠燈」的習慣，因為之後會有更多種 stop_reason（`max_tokens`、`pause_turn` 等），它們都需要不同處理，而 content 裡有沒有某種 block 沒辦法區分這些。

### 為什麼 final_text 要用 `"".join(...)` 而不是 `resp.content[0].text`？

Ch 0、Ch 2 反覆強調過：`content` 是 block list，第一個不保證是 text。一個 end_turn 的回應**理論上**可以有多個 text 區塊，或前面夾著別的區塊。`"".join(b.text for b in resp.content if b.type == "text")` 是「把所有文字區塊接起來」的穩健寫法，不會因為位置假設而炸。

### 為什麼 `continue` 之後沒有再 append assistant？

注意順序：我們是**先** append assistant 回應（不管哪種 stop_reason 都 append），**再**判斷要不要執行工具。所以 tool_use 分支裡只需要再 append 一則 tool_result，然後 `continue` 回頂端重新呼叫。這個「先存回應、後分流」的順序能避免「忘了存 assistant」的經典 bug。

## 失敗示範：少了 max_turns 會怎樣

教材的慣例——先看壞的。假設你天真地寫成 `while True`，又給了一個模型可能反覆呼叫的工具（例如一個每次都回「還沒好，再查一次」的工具）：

```python
# 反例！不要這樣寫
while True:
    resp = client.messages.create(...)
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason == "tool_use":
        messages.append(run_tool_uses(resp.content))
        continue
    return ...
```

如果模型陷入「我再查一次 → 還沒好 → 我再查一次」，這個迴圈**永遠不會停**，每圈一次 API 呼叫，你的帳單一路往上。等你發現時可能已經燒掉幾百次呼叫。`max_turns` 不是潔癖，是必需品。記住這個畫面：**任何會自己迴圈的東西，都必須有一個你能信任的剎車。**

## 踩雷集錦

1. **`max_turns` 設太小或太大**：太小，複雜任務還沒做完就被截斷（你會看到那句「達到上限」）；太大，失控時燒更多錢。沒有萬用值——簡單對話 5–10 夠，複雜 coding agent 可能要 50+。重點是**一定要有**，數字之後可調。
2. **把工具例外往上拋**：`func(**block.input)` 沒包 try/except，工具一炸整個 `run_agent` 就拋例外結束。模型本來可以換個參數重試的機會就沒了。把錯誤當 tool_result 回給模型，agent 才有韌性。
3. **`run_tool_uses` 漏掉「跳過非 tool_use 區塊」**：模型常常 text 和 tool_use 混在一個回應裡。沒有 `if block.type != "tool_use": continue`，你會試圖對一個 text 區塊呼叫 `.name` / `.input` 而 `AttributeError`。
4. **以為「end_turn 才需要處理」**：除了 tool_use 和 end_turn，還有 `max_tokens`（回應被長度截斷）等。本章的 `else` 把它們全當「結束」處理——對最小版本可以，但要知道這是簡化。例如 `max_tokens` 其實代表「答案沒講完」，正規 harness 會想辦法續寫（Ch 7）。
5. **tool_result 的 `content` 沒轉成字串**：工具回傳可能是 dict、數字、物件。`tool_result` 的 `content` 需要是字串（或符合規格的結構）。我們用 `str(result)` 保底。回傳複雜結構怎麼格式化，是 Ch 20 的主題。

## 進階：再往深一層

- **把迴圈包成一個 class**：現在 `messages`、`TOOL_*` 都是散的。實務上你會把它們收進一個 `Agent` class，`messages` 變成 instance 狀態，`run()` 是方法。這讓「多輪對話」（使用者問完一題再問下一題）變得自然——Ch 6 會做這件事。本章刻意保持函式式，是為了讓你看清資料流而不被 class 結構干擾。
- **一輪內的工具有時可以平行跑**：`run_tool_uses` 我們是 for 迴圈一個一個跑。如果模型一次要了三個**彼此獨立、沒有副作用**的工具（例如查三個城市天氣），它們可以平行執行省時間。但要小心：有副作用、有共享狀態、有先後順序需求、或有 rate limit 的工具，**不能**無腦丟去平行跑——例如「先寫檔再讀同一個檔」平行化就會壞掉。平行化要用到 `asyncio` 或 thread pool，是 Part 4（Ch 31）的內容，那裡會講怎麼判斷哪些能平行。最小版本先序列跑，正確優先。
- **`tool_choice` 可以控制模型用不用工具**：`create()` 有個 `tool_choice` 參數。預設帶 `tools` 時是 `{"type": "auto"}`（模型自己決定用不用）；`{"type": "any"}` 強迫它這一輪一定要用某個工具；`{"type": "tool", "name": "..."}` 指定用哪個；`{"type": "none"}` 則禁止它用工具（這一輪只准講話）。一個限制要記住：強制式的 `any` / `tool` 跟 extended thinking 不相容（要模型先想再被逼著用工具，語意衝突）。最小迴圈我們用預設 `auto`，但知道有這個控制鈕，Ch 5、Ch 28（planning）會用到。

## 動手練習

1. 把 Step 1–3 接起來跑，餵它「台北幾度？順便算 100 加 250」，確認它會連續用兩個工具、跑滿三回合才回答。
2. 加一個會「故意失敗」的工具（例如 `def broken_tool(): raise ValueError("壞了")`），把它放進註冊表，然後叫 agent 用它。觀察 agent 收到 `is_error` 的結果後怎麼反應——它會放棄、道歉、還是換方法？這讓你體會踩雷第 2 點為什麼重要。
3. 把 `max_turns` 改成 `1`，餵一個需要用工具的問題。你會看到 agent 在第一輪要了工具、但因為沒有第二輪可以處理結果，直接撞上限回傳那句話。親眼確認 `max_turns` 是怎麼截斷的。
4. 在迴圈裡每輪印出 `turn`、`resp.stop_reason`、`resp.usage.input_tokens`，跑一個三回合的任務，觀察 input_tokens 怎麼一輪比一輪大——這是 Part 2 的伏筆。

## 本章重點整理

- 一個能跑的最小 agent ≈ 「工具註冊表 + 一個看 `stop_reason` 分流的 `for` 迴圈」，大約 60 行。
- 迴圈的「自主」來自那條回邊：只要 `stop_reason == "tool_use"` 就執行工具並再問一次，直到模型 `end_turn`。
- 一輪可能有多個 tool_use，全部執行、結果裝進**同一則** user 訊息。
- 工具錯誤要當成 tool_result 回給模型（`is_error`），不要讓它炸掉整個 agent。
- `max_turns` 是最小 harness 裡的第一道 policy——任何會自轉的迴圈都必須有剎車。

## 自我檢核

- [ ] 不看程式碼，我能畫出這個迴圈的流程圖，並指出哪條邊造就了「自主」
- [ ] 我能解釋為什麼每輪都要 append `resp.content`，漏掉會怎樣
- [ ] 我能說出為什麼工具例外要包起來當結果回給模型，而不是往上拋
- [ ] 我能說出至少兩種 `max_turns` 以外、本章 `else` 分支其實簡化掉的 stop_reason，以及它們理想上該怎麼處理
- [ ] 我知道「一輪多工具」時 tool_result 的正確組裝方式

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool use（含 multi-turn / 多工具）](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)**
  - **讀哪裡**：「Handling tool use and tool result content blocks」與「Multiple tool use」段落，對照本章 `run_tool_uses` 的多工具處理。
  - **能學到什麼**：官方對「一則 user 訊息裝多個 tool_result」的正式說明，以及 `is_error`、`tool_choice` 的完整語意。
  - **前提知識**：本章看完即可。

- **[Anthropic — Handling stop reasons](https://docs.anthropic.com/en/api/handling-stop-reasons)**
  - **讀哪裡**：各種 `stop_reason` 的列表與建議處理方式。
  - **能學到什麼**：本章 `else` 分支簡化掉的那些 stop_reason 各代表什麼，為 Ch 7 鋪路。
  - **前提知識**：本章看完即可。

### 部落格 / 技術文章

- **[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic（2024）
  - **這篇說什麼**：把 agent 定義為「LLM 在一個迴圈裡使用工具」——你剛寫的這 60 行，就是那句定義的最小實現。
  - **讀哪裡**：「Agents」一節對 autonomous loop 的描述。
  - **為什麼值得讀**：讓你確認自己寫的東西正是業界對 agent 的標準定義，不是玩具。

下一章我們把這章偷懶帶過的「tool calling」這塊正式講透：schema 的每個欄位、`tool_choice`、平行工具、`tool_result` 的完整規格、以及怎麼把 Python 函式自動轉成 schema。

→ [Ch 5 Tool calling 協議](./05-tool-calling-protocol.md)
