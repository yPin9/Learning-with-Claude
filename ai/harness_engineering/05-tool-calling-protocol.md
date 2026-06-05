# Ch 5 — Tool calling 協議

> **目標**：把 Ch 2、Ch 4 用過但沒講透的 tool calling 協議攤開到每個欄位。讀完你能精確說出：一個工具 schema 的每個欄位怎麼影響模型、`tool_choice` 四種值、平行工具的正確收發、`tool_result` 能放什麼（不只字串）、以及怎麼把一個 Python 函式自動轉成 schema，不用手寫 JSON。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版）。本章談的是 client-side tool（由你的 harness 執行的工具，Ch 1 區分過的那種）的協議。

## 為什麼要把協議講到這麼細

Ch 4 那個迴圈能跑，但它對協議的理解停在「能動就好」。真實 agent 的很多疑難雜症，根源都在協議細節沒搞清楚：

- 「模型為什麼老是不用我的工具？」——多半是 schema 的 `description` 或參數說明寫得讓模型看不懂（這章建立基礎，Ch 19 深挖）。
- 「為什麼 API 突然報 400？」——多半是 tool_use / tool_result 沒成對、或 `tool_use_id` 對不上。
- 「我想讓工具回一張圖給模型看，可以嗎？」——可以，但你得知道 `tool_result` 的 content 不只能放字串。

把協議當成一份你必須讀懂的合約。這章就是逐條讀這份合約。

## 先建立直覺：tool schema 是「寫給模型的 API 文件」

你寫過 API 給別人用嗎？你會寫一份文件：這個 endpoint 叫什麼、做什麼、要哪些參數、每個參數什麼型別什麼意思。**tool schema 就是寫給「模型」這個呼叫方看的 API 文件**——差別只在讀者是 LLM，不是人類工程師。

```
   你給人類的 API 文件              你給模型的 tool schema
   ┌────────────────────┐         ┌──────────────────────────┐
   │ POST /weather       │         │ name: "get_weather"       │
   │ 查詢城市天氣          │  ≈      │ description: "查詢城市天氣" │
   │ 參數 city: string    │         │ input_schema: {city: ...} │
   │   （城市名）          │         │   description: "城市名"    │
   └────────────────────┘         └──────────────────────────┘
       人類讀完決定怎麼呼叫            模型讀完決定要不要用、傳什麼
```

這個類比有個關鍵推論：**文件寫得爛，呼叫方就用得爛**。人類工程師看到爛文件會猜、會去翻 source；模型不會，它只能照你給的字面去判斷。所以 schema 的文字品質，直接等於模型用工具的品質。記住這條，Ch 19 整章都在講「怎麼把這份文件寫好」。

## 協議全貌：一次往返交換哪些東西

先把 Ch 2 的流程用「協議」的語言重述，標出每個欄位的歸屬：

```
   ── 請求（你 → API）────────────────────────────
   {
     model, max_tokens,
     system: "...",                    ← system prompt（Ch 11）
     tools: [ {name, description, input_schema}, ... ],   ← 能力說明書
     tool_choice: {type: "auto"},      ← 用不用工具的控制鈕
     messages: [ ... 對話歷史 ... ],
   }

   ── 回應（API → 你）────────────────────────────
   {
     stop_reason: "tool_use",          ← 紅綠燈
     content: [
       {type: "text", text: "我來查一下"},
       {type: "tool_use", id: "toolu_..", name: "get_weather", input: {city:"Taipei"}},
     ],
   }

   ── 你回覆工具結果（你 → API，下一個請求）──────────
   messages += [
     {role: "assistant", content: <上面整個 content>},   ← 原樣接回
     {role: "user", content: [
        {type: "tool_result", tool_use_id: "toolu_..", content: "晴, 28度"},
     ]},
   ]
```

接下來逐塊拆。

## 一、工具 schema 的每個欄位

一個工具就是三個欄位。看似簡單，每個都有講究：

```python
{
    "name": "get_weather",
    "description": "查詢指定城市目前的天氣，回傳攝氏溫度與天氣狀況。",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市的英文名稱，例如 'Taipei'、'Tokyo'。不要傳中文。",
            },
            "units": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "溫度單位，預設攝氏。",
            },
        },
        "required": ["city"],
    },
}
```

- **`name`**：工具的識別碼。規則：用清楚的動詞片語（`get_weather`、`read_file`、`run_query`），不要用 `tool1` 這種無意義名字——**模型會用名字本身推斷用途**。只能是字母、數字、底線、連字號。
- **`description`**：最重要的一個欄位。模型靠它判斷「這個工具能不能解決我現在的問題」。要寫清楚：做什麼、什麼時候該用、什麼時候**不**該用、有什麼副作用。一句話的 description 通常不夠。Ch 19 整章在講這個。
- **`input_schema`**：一份 **JSON Schema**，描述參數。這不是 Anthropic 發明的格式，是業界通用的 [JSON Schema](https://json-schema.org/) 標準。常用功能都能用；但較複雜的 JSON Schema 功能在 tool use（尤其 strict tool use）下有支援限制，用到冷門功能時要查官方文件確認。常用的這些：
  - `type`：`string` / `number` / `integer` / `boolean` / `object` / `array`。
  - `properties`：每個參數一個條目，**每個參數自己也要有 `description`**——這跟工具的 description 一樣重要，模型靠它知道該傳什麼。
  - `required`：哪些參數必填。沒列進去的就是選填。
  - `enum`：限定值只能是清單裡的幾個（上面的 `units`）。**善用 enum**——它把模型的選擇限制在合法範圍，比在 description 裡寫「請傳 celsius 或 fahrenheit」可靠得多。
  - 還能用 `minimum` / `maximum`（數字範圍）、`items`（陣列元素的 schema）、`pattern`（字串正則）等。

> **關鍵心法**：能用 schema 結構表達的限制，就別只寫在 description 裡。`enum`、`required`、`type` 是模型**比較會遵守**的強訊號；description 裡的自然語言更弱，模型更容易忽略。但要誠實說清楚：一般（非 strict）tool use 下，這些**不是硬保證**——模型偶爾還是會輸出不符 schema 的 input。真正保證 input 一定合 schema 要開 **strict tool use**（`strict: true`，本章進階區會提）。所以正確心態是「用 schema 把約束結構化，能大幅提高遵守率」，**但 harness 這層仍要驗證**（本章後面的 Pydantic 段落就是在做這件事）。把約束結構化是第一招，驗證是第二道保險。

## 二、`tool_choice`：用不用工具的控制鈕

預設情況下（帶了 `tools` 但沒設 `tool_choice`），等同 `{"type": "auto"}`：模型自己決定這一輪要不要用工具、用哪個。但你可以接管這個決定：

| `tool_choice` | 意思 | 什麼時候用 |
|---|---|---|
| `{"type": "auto"}` | 模型自由決定用不用、用幾個（預設） | 一般 agent 迴圈，讓模型自主 |
| `{"type": "any"}` | 強制這一輪**至少用一個**工具（模型選哪個、可能用多個） | 你確定這一輪一定要動作，不要它打嘴砲 |
| `{"type": "tool", "name": "X"}` | 強制用指定的工具 X | 你要的就是結構化輸出，例如「一定要呼叫 `extract_data`」（Ch 32 的技巧） |
| `{"type": "none"}` | 禁止用任何工具，只准講話 | 你要模型純文字總結、不要它再動作 |

> 注意 `any` 是「**至少**一個工具」，不保證剛好一個——模型仍可能一次要多個。若你要「強制剛好用一個工具」，得在 `tool_choice` 裡再加 `"disable_parallel_tool_use": true`（本章進階區會提這個旗標）。

```python
# 範例：強制模型一定要用 extract_contact 這個工具（拿來做結構化抽取）
resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=1024,
    tools=[extract_contact_schema],
    tool_choice={"type": "tool", "name": "extract_contact"},
    messages=[{"role": "user", "content": "我叫王小明，電話 0912345678"}],
)
```

一個要記住的限制：**強制式的 `any` / `tool` 跟 extended thinking 不相容**。原因很具體：`any` / `tool` 的作法是在 assistant 訊息開頭「預填」(prefill) 一個 tool use，逼模型直接進入工具呼叫，因此模型**不會在 `tool_use` 之前產生自然語言或思考內容**——這跟 thinking「先產生一段推理再回應」直接衝突。所以 extended thinking 只相容 `auto` 與 `none`。需要 thinking 的場景就用 `auto`。

## 三、平行工具：一輪多個 tool_use

Ch 4 我們已經處理過「一輪多工具」，這裡把協議講清楚。當模型判斷「這幾件事彼此獨立、可以一起做」時，它會在**同一個回應**裡放多個 `tool_use` 區塊：

```
content: [
  {type: "text", text: "我同時查兩個城市"},
  {type: "tool_use", id: "toolu_A", name: "get_weather", input: {city: "Taipei"}},
  {type: "tool_use", id: "toolu_B", name: "get_weather", input: {city: "Tokyo"}},
]
```

**收的規矩**：你執行這兩個工具，把兩個結果裝進**同一則** user 訊息的 content 陣列，每個 tool_result 用自己的 `tool_use_id` 對上號：

```python
{
    "role": "user",
    "content": [
        {"type": "tool_result", "tool_use_id": "toolu_A", "content": "台北 晴 28度"},
        {"type": "tool_result", "tool_use_id": "toolu_B", "content": "東京 多雲 22度"},
    ],
}
```

這正是 Ch 4 `run_tool_uses` 在做的事。協議上有幾條會直接讓 API 回 400 的硬規則，一起記住：

1. **每一個 tool_use 都要有對應的 tool_result**，少一個就報錯。不能「先回一個、下一輪再回另一個」。
2. **裝 tool_result 的那則 user 訊息，必須緊接在含 tool_use 的 assistant 訊息後面**，中間不能插別的訊息。
3. **那則 user 訊息的 content 裡，所有 `tool_result` 區塊要排在最前面**；如果你還想附一段文字給模型，文字要放在所有 tool_result 之後。

> **要不要平行執行？** 協議只要求你「把結果都湊齊、按上面規則送回去」，沒要求你怎麼跑它們。而且要澄清一個常見誤解：**同一個 assistant 回應裡的多個 tool_use 之間沒有語義順序**——你可以任意順序、或平行執行它們。對獨立、無副作用的工具（查兩個城市），平行跑省時間（Ch 31）。那如果模型不小心一次批出了其實互相依賴的工具呼叫（例如「建檔」和「寫入同一個檔」）怎麼辦？正解**不是**讓 harness 去猜先後順序，而是照常執行、把出問題的那個用 `is_error: true` 回報，讓模型下一輪重新發出依賴的呼叫。協議的收發形狀跟你內部怎麼執行是兩回事。

## 四、`tool_result` 能放什麼：不只是字串

Ch 4 我們的 tool_result content 都是字串。但協議允許更多：

```python
# 1. 最常見：純字串
{"type": "tool_result", "tool_use_id": "...", "content": "晴, 28度"}

# 2. 標記為錯誤（Ch 4 用過）
{"type": "tool_result", "tool_use_id": "...", "content": "找不到該城市", "is_error": True}

# 3. content 也可以是 block 陣列——例如回傳一張圖給模型看
{
    "type": "tool_result",
    "tool_use_id": "...",
    "content": [
        {"type": "text", "text": "這是台北的雷達回波圖："},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<base64>"}},
    ],
}
```

重點：

- **字串是 90% 的情況**。但當工具的產出是圖片、或是「文字 + 圖」的混合（例如截圖工具、圖表工具），你可以把 image block 放進 tool_result，模型就看得到圖（前提是用支援視覺的模型）。除了 text / image，官方也支援在 tool_result 裡放 `document` block（例如回一份 PDF 給模型）。多模態輸入是 Ch 33 的主題。
- **`is_error: True`** 告訴模型「這次工具結果代表失敗」。它不是行為保證，而是一個訊號——讓模型知道這是失敗結果，通常它會據此重試、換參數、或回覆使用者說明，而不是把錯誤訊息當成正常資料拿來用。
- **tool_result 的內容要「給模型好讀」**，不是「給機器解析」。回一坨原始 JSON 不如回整理過的摘要——這是 Ch 16、Ch 20 的主題。協議允許你放任何字串，但放什麼是設計問題。

## 五、別再手寫 schema：從 Python 函式自動生成

手寫 JSON schema 又煩又容易跟實際函式簽名不同步（你改了函式參數，忘了改 schema，模型就傳錯參數）。實務上你會**從函式自動生成 schema**。最輕量的做法是用型別提示 + docstring：

```python
import inspect
from typing import get_type_hints

# Python 型別 → JSON Schema 型別 的對照
_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}

def schema_from_function(func) -> dict:
    """從函式的型別提示與 docstring 生出一份 tool schema。"""
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    properties, required = {}, []
    for pname, param in sig.parameters.items():
        py_type = hints.get(pname, str)
        properties[pname] = {"type": _PY_TO_JSON.get(py_type, "string")}
        # 沒有預設值的參數視為必填
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "name": func.__name__,
        "description": (func.__doc__ or "").strip(),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def get_weather(city: str, units: str = "celsius") -> str:
    """查詢指定城市目前的天氣，回傳溫度與狀況。city 用英文名。"""
    ...

print(schema_from_function(get_weather))
```

輸出：

```python
{
    'name': 'get_weather',
    'description': '查詢指定城市目前的天氣，回傳溫度與狀況。city 用英文名。',
    'input_schema': {
        'type': 'object',
        'properties': {'city': {'type': 'string'}, 'units': {'type': 'string'}},
        'required': ['city'],   # units 有預設值，自動變選填
    },
}
```

這個小函式就把「函式即工具」變成現實——參數名稱和必填與否會跟函式簽名同步，因為它**是從簽名生出來的**，你改簽名就自動跟著變。但它還很陽春：沒處理 `enum`、沒抓每個參數的 description、碰到 `Literal` / Enum / list / 巢狀 object 這些複雜型別會失準。所以別過度承諾「永遠同步」——簡單型別同步得很好，複雜的還是得靠更完整的工具。概念到位即可。

**生產等級的人不會自己刻這個**，會用現成的：

- **Pydantic**：把參數定義成 Pydantic model，`model.model_json_schema()` 直接吐 JSON Schema，型別驗證也一併有了。這是目前最主流的做法。
- **框架內建**：Claude Agent SDK、LangChain 的 `@tool` decorator 等都有「函式 → 工具」的自動轉換（Ch 40 對比）。

但你**先懂上面那 20 行在做什麼**，再用框架，你才知道它幫你做了什麼、出錯時去哪找。

## 把分派也做嚴謹：升級 Ch 4 的 run_tool_uses

Ch 4 的 `TOOL_FUNCTIONS` dict 分派堪用，但 input 直接 `**block.input` 灌進函式有風險：模型可能傳了多餘的 key、或漏了必填的、或型別不對。嚴謹一點的版本會在呼叫前驗證——這正是 Pydantic 的價值：

```python
# 概念示意：用 Pydantic 驗證模型傳來的 input
from pydantic import BaseModel, ConfigDict, ValidationError

class GetWeatherArgs(BaseModel):
    # 預設 Pydantic v2 會「忽略」多餘欄位、且會做型別轉換（coercion，例如 "28" → 28）。
    # 要嚴格擋多餘 key，加上 extra="forbid"；要連型別轉換都禁掉，可另設 strict=True。
    model_config = ConfigDict(extra="forbid")

    city: str
    units: str = "celsius"

def dispatch(name, raw_input) -> tuple[str, bool]:
    if name == "get_weather":
        try:
            args = GetWeatherArgs(**raw_input)     # ← 驗證 + 填預設值
        except ValidationError as e:
            return f"參數錯誤：{e}", True            # 把驗證錯誤回給模型
        return get_weather(args.city, args.units), False
    return f"未知工具 {name}", True
```

模型偶爾會傳出不符 schema 的 input（尤其複雜 schema）。在 harness 這層驗證，把錯誤**當 tool_result 回給模型讓它自己修**，比讓函式因為 `TypeError` 炸掉穩健得多。這是 Ch 4「錯誤是給模型的資訊」原則的延伸。

## 踩雷集錦

1. **參數沒寫 `description`**：很多人只給工具本身寫 description，參數光禿禿一個 `{"type": "string"}`。模型不知道該傳什麼格式（`Taipei` 還是 `台北`？日期是 `2024-01-01` 還是 `Jan 1`？），就會亂傳。**每個參數都要有 description**。
2. **能用 enum 卻寫在 description 裡**：「units 請填 celsius 或 fahrenheit」寫在自然語言裡，模型可能傳 `C`、`攝氏`、`Celsius`。用 `enum: ["celsius", "fahrenheit"]` 把它變成硬約束。
3. **平行工具只回一個 tool_result**：模型一次要了三個工具，你只回一個結果，API 報錯「缺少對應的 tool_result」。規則是**每個 tool_use 都要有對應 tool_result，且全部裝在同一則訊息**。
4. **schema 和函式簽名不同步**：手寫 schema 最常見的 bug——函式加了個參數，schema 忘了改，模型永遠不知道有這個參數。自動生成（或至少寫測試檢查兩者一致）能根除這類問題。
5. **把 `tool_choice` 設成 `any`/`tool` 又開 thinking**：兩者不相容，API 會報錯。要 thinking 就用 `auto`。
6. **以為 `tool_result` 只能放字串**：能放 block 陣列（含 image）。需要回圖給模型時別卡在這。

## 進階：再往深一層

- **JSON Schema 的進階用法**：`anyOf`（多選一型別）、`$ref`（重用定義）、巢狀 object/array 都支援。但**越複雜的 schema，模型越容易填錯**——這是真實的取捨。實務經驗：把工具拆小、schema 扁平，往往比一個巨大複雜 schema 的工具更可靠。Ch 18 會談「工具粒度」這個設計問題。
- **server-side tools 的協議不同**：本章全是 client-side tool（你執行）。Anthropic 還有 server-side tools（如 web search），它們的 tool_use 由 Anthropic 那端執行、結果直接出現在回應裡，你不需要也不能自己回 tool_result。用到時看官方文件，別跟 client tool 搞混。
- **`disable_parallel_tool_use`**：`tool_choice` 裡可以加這個旗標（`{"type": "auto", "disable_parallel_tool_use": true}`），強制模型一次最多只要一個工具。當你的工具有順序依賴、不想處理「一輪多工具」的複雜度時有用。代價是慢（不能平行）。
- **strict tool use（`strict: true`）**：前面說一般 tool use 下，模型「偶爾」會輸出不符 schema 的 input。如果你需要**保證** input 一定符合 schema，可以開啟 strict tool use——它會約束模型的輸出嚴格遵守你的 JSON Schema。代價是 strict 模式對 JSON Schema 的支援有額外限制（不是所有功能都能用），且可能影響延遲。需要強保證（例如把工具當結構化輸出用，Ch 32）時再開；一般 agent 工具用預設、靠 harness 驗證即可。用前查官方「strict tool use」文件確認限制。

## 動手練習

1. 用本章的 `schema_from_function` 把 Ch 4 的 `add` 函式轉成 schema，比對它和你 Ch 4 手寫的版本差在哪（提示：自動版少了參數 description——這正是它的限制）。
2. 給 `get_weather` 加一個 `units` 參數並用 `enum` 限定，然後問模型「台北幾度，用華氏」，觀察它有沒有正確在 input 裡帶 `units: "fahrenheit"`。
3. 故意讓模型傳一個不符 schema 的 input（例如把工具 schema 的 `city` 標成 required 卻問一個沒有城市的問題，看模型怎麼處理），體會為什麼 harness 這層要驗證。
4. 設 `tool_choice={"type": "any"}`，問一個根本不需要工具的問題（「你好嗎」），看模型被強迫之下怎麼硬湊一個工具呼叫——體會強制的副作用。

## 本章重點整理

- tool schema 是「寫給模型的 API 文件」：`name`（用途靠它推斷）、`description`（最關鍵）、`input_schema`（JSON Schema，每個參數都要有 description）。
- 能結構化的約束（`enum`/`required`/`type`）比寫在 description 裡的自然語言可靠——優先用硬約束。
- `tool_choice` 四種：`auto`（預設自由）/`any`（強制用某個）/`tool`（強制用指定）/`none`（禁用）；強制式跟 thinking 不相容。
- 平行工具：一個回應可含多個 tool_use，每個都要有對應 tool_result，全部裝在同一則 user 訊息。
- `tool_result` 的 content 可以是字串、可以標 `is_error`、也可以是含 image 的 block 陣列。
- 別手寫 schema：從函式型別提示自動生成（自己刻或用 Pydantic），讓 schema 永遠跟簽名同步。

## 自我檢核

- [ ] 我能說出工具 schema 三個欄位各自如何影響模型行為
- [ ] 我知道為什麼「每個參數都要有 description」「能用 enum 就別寫在 description」
- [ ] 我能正確收發「一輪多工具」：每個 tool_use 對應一個 tool_result，全裝同一則訊息
- [ ] 我能說出 `tool_choice` 四種值各自的用途與那個 thinking 限制
- [ ] 我能解釋「從函式自動生成 schema」解決了什麼問題，以及 Pydantic 在這裡的角色

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool use overview](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)**
  - **讀哪裡**：「Specifying tools」「Tool use examples」「Handling tool results」整段，以及 `tool_choice` 與 `disable_parallel_tool_use` 的說明。
  - **能學到什麼**：本章每個欄位的官方權威定義；以及本章沒展開的 fine grained tool streaming 等進階主題。
  - **前提知識**：本章看完即可——這是本章的「原始合約」，遇到行為不符預期時以它為準。

- **[JSON Schema 官方網站](https://json-schema.org/understanding-json-schema/)**
  - **讀哪裡**：「Understanding JSON Schema」的 type、object、enum、required 幾節。
  - **能學到什麼**：`input_schema` 用的就是 JSON Schema，這份文件讓你知道還有哪些約束可用（`pattern`、`minimum`、`items`…）。
  - **前提知識**：本章對 input_schema 的介紹。

### 部落格 / 技術文章

- **[Writing tools for AI agents（Anthropic Engineering）](https://www.anthropic.com/engineering)** — Anthropic Engineering
  - **這篇說什麼**：在 Engineering 索引頁找「為 agent 寫工具」主題的文章，談工具設計原則——本章建立了協議基礎，這類文章把「怎麼寫得讓模型用得好」推得更深。
  - **讀哪裡**：找標題含 tool / writing tools 的那篇。
  - **為什麼值得讀**：協議是死的，工具設計是活的；Ch 18–20 會深入，這篇是好的前導。

下一章我們把焦點從「單次協議」拉到「多輪」：對話歷史怎麼累積、怎麼用一個 class 把 messages 收成 agent 的狀態、以及多輪對話裡哪些東西該留、哪些該丟——這是通往 Part 2 context 管理的橋。

→ [Ch 6 多輪對話與訊息歷史管理](./06-message-history.md)
