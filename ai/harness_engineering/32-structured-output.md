# Ch 32 — Structured output 與 schema 強制

> **目標**：到目前為止 agent 的輸出大多是「給人讀的自由文字」。但在 agent 系統內部，常常需要模型回**機器能直接解析的結構化資料**：orchestrator 拆出的子任務清單（Ch 27/28）、router 的分類決定（Ch 27）、抽取 subagent 抽出的欄位（Ch 26）、要塞進下一個工具的參數。讀完你能說出「夾在散文裡的 JSON」為什麼脆弱、取得結構化輸出的三條路（prompt-and-parse / 用工具呼叫強制結構 / 原生 structured outputs）各自的可靠度與取捨、為什麼**「工具呼叫本質上就是結構化輸出」**（你前面每次定 `input_schema` 都在做這件事）、`tool_choice` 怎麼強迫模型「一定要產出符合某 schema 的東西」、以及為什麼即使 schema「保證合法」，你**仍要做語意驗證**（結構對 ≠ 內容對）。

> **環境**：Python + Anthropic SDK。會用到你已經熟的 tool use（Ch 18-20）與 `tool_choice`，並用 `pydantic` 來定義/驗證 schema。會提到 Anthropic 的原生 structured outputs 能力；其確切 API 形態與可用性以官方文件為準，本章聚焦「概念 + 最通用可靠的做法」。

## 為什麼需要這個？「散文裡的 JSON」會在半夜炸掉

agent 系統裡，模型的輸出常常不是給人看的，而是**要餵給程式的下一步**：

- orchestrator 要模型「把大任務拆成子任務」，然後 fan-out（Ch 27）——它需要一個**乾淨的子任務陣列**，好用 for 迴圈派出去。
- router 要模型「把這個請求分類成 退款/技術/帳務」（Ch 27）——它需要一個**確定的列舉值**，好 `if` 分流。
- 抽取 agent 讀完一份文件，要回「公司名、金額、日期」——它需要一個**欄位齊全的物件**，好寫進資料庫。

如果你只是在 prompt 裡寫「請回 JSON」，模型**多半**會給你 JSON，但麻煩在那個「多半」：

```
模型可能回：
   好的，這是拆解結果：              ← 多了前言
   ```json
   { "subtasks": ["查A", "查B",] }   ← 尾逗號、被 markdown 包住
   ```
   希望這對你有幫助！                ← 又多了結尾
```

你的 `json.loads()` 會在這些地方爆炸：多餘的前言/結尾、被 ```` ```json ```` 包住、尾逗號、漏欄位、把數字寫成字串、欄位名拼錯……。而且它**不穩定**——同樣的 prompt 跑 100 次，99 次好好的，第 100 次多一句話就讓你的 pipeline 半夜掛掉。

問題的本質：**自由文字輸出沒有任何「形狀保證」。** 你是在「請求」一個格式，不是在「強制」一個格式。agent 系統要可靠，內部的資料交換就不能建立在「模型這次心情好、格式剛好對」之上。

關鍵心態：**要結構化資料，就用「會強制形狀」的機制，別用「請模型自律產 JSON + 自己硬解析」。** 而你其實早就有這個機制了——它叫 tool use。

## 先建立直覺：結構化輸出是「填表格」，不是「寫作文」

自由文字像請人「寫一段話描述這筆訂單」——你拿到的是散文，得自己從裡面挖資料。結構化輸出像給人「一張欄位固定的表格」去填——公司名一格、金額一格、日期一格，填完你直接讀格子，不必猜。

```
   ❌ 寫作文（自由文字）：           ✅ 填表格（結構化輸出）：
   「這筆訂單是 Acme 公司下的，      ┌─────────────┬──────────┐
     金額大概一萬二，日期我記得      │ company     │ "Acme"   │
     是上週四…」                     │ amount      │ 12000    │
        ↑ 你得自己解析、還可能挖錯   │ date        │ "2026-..." │
                                      └─────────────┴──────────┘
                                         ↑ 直接讀格子，形狀有保證
```

表格的威力在於**形狀是預先約定好的**：有哪些欄、每欄什麼型別、哪些必填——這份約定就是 **schema**。模型的工作從「自由發揮」收斂成「把約定好的格子填對」。這讓下游程式可以**信賴形狀**，只需操心內容對不對，不必再操心「這次格式會不會壞」。

> **你前面早就在用這個了**：Ch 18-20 每次定工具的 `input_schema`，就是在給模型一張「呼叫這個工具該填的表格」。當模型回一個 `tool_use` block，那個 `input` 就是**一份結構化的呼叫資料**（一個物件，不是散文）。換句話說——**tool use 回的就是結構化輸出**，你只是之前把它當「呼叫工具」在用，沒意識到它同時也是「讓模型產出特定形狀資料」的手段。（精確地說：tool use 保證你拿到的是「一個 structured call」；要它**嚴格符合 schema**，還要再開 `strict`，見第二節。）本章就是把這個洞察明講、並用它來拿結構化資料。

## 一、三條路：從最不可靠到最可靠

取得結構化輸出有三種做法，可靠度差很多：

| 做法 | 怎麼運作 | 可靠度 | 何時用 |
|---|---|---|---|
| **1. Prompt-and-parse** | prompt 寫「請回 JSON」，自己 `json.loads` | **低**——沒有形狀保證，要容錯+重試 | 快速原型、或環境不支援前兩者 |
| **2. 工具呼叫強制結構** | 定一個 schema 工具，用 `tool_choice` 強迫呼叫，並開 `strict` 強制形狀 | **高**——配 `strict` 後 `input` 嚴格符合 schema | 最通用、最可攜的可靠做法 |
| **3. 原生 structured outputs** | API 直接保證輸出嚴格符合你給的 JSON schema | **最高**——平台層強制 schema 一致 | 平台支援時，要「保證合法」的首選 |

往下逐一看。

## 二、做法 2：用工具呼叫強制結構（最通用可靠的一招）

這是本章最該掌握的技巧，因為它**到處都能用**（任何支援 tool use 的模型/版本），而且你已經會 90%。核心三步：

1. **把你要的結構定義成一個工具的 `input_schema`**。
2. **用 `tool_choice` 強迫模型「一定要呼叫這個工具」**——它就不能回自由文字、只能產出符合 schema 的 `input`。
3. **從回傳的 `tool_use` block 取 `input`**——那就是你要的結構化資料（再做語意驗證，見第四節）。

用 `pydantic` 定義 schema、再用 `tool_choice` 強制：

```python
from anthropic import Anthropic
from pydantic import BaseModel, Field

client = Anthropic()

# 1. 用 pydantic 定義你要的形狀——它能直接吐 JSON schema，省得手寫
class TaskBreakdown(BaseModel):
    subtasks: list[str] = Field(description="彼此獨立、可並行的子任務，每個是一句話")
    rationale: str = Field(description="為什麼這樣拆")

SCHEMA_TOOL = {
    "name": "submit_breakdown",
    "description": "提交把大任務拆成子任務的結果。",
    "strict": True,                                      # 開 strict：要求輸出嚴格符合 schema
    "input_schema": TaskBreakdown.model_json_schema(),   # pydantic → JSON schema
}

def decompose(big_task: str) -> TaskBreakdown:
    resp = client.messages.create(
        model="claude-opus-4-8", max_tokens=1024,
        tools=[SCHEMA_TOOL],
        # 2. 強迫模型「一定要呼叫 submit_breakdown」，不准回自由文字
        tool_choice={"type": "tool", "name": "submit_breakdown"},
        messages=[{"role": "user", "content": f"把這個任務拆成可並行的子任務：{big_task}"}],
    )
    # 3. 取出那個 tool_use block 的 input（防禦性：可能因截斷/拒答而沒有）
    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise ValueError(f"模型未如預期回傳 tool_use（stop_reason={resp.stop_reason}）")
    # 4. 用 pydantic 再驗一次（型別、必填欄位）並轉成型別物件——最後一道保險
    return TaskBreakdown.model_validate(tool_use.input)
```

幾個要點，注意「強制呼叫」和「強制形狀」是**兩件事**：

- **`tool_choice` 控制「要不要 / 呼叫哪個工具」**（Ch 18 提過，這裡是它最有用的場景）：`{"type":"auto"}`（模型自己決定要不要用工具）、`{"type":"any"}`（一定要用某個工具，但哪個由模型挑）、`{"type":"tool","name":...}`（**強迫用指定的那個**）、`{"type":"none"}`（禁止用工具）。要拿結構化輸出，用 `tool`——它保證模型**會去呼叫**那個工具、不回自由文字。
- **但「呼叫了」不等於「填得嚴格符合 schema」**：光靠 `tool_choice` 強制呼叫，模型仍**可能**回不合型別、漏必填欄位的 `input`。要讓 `input` **嚴格貼合 schema**，得在工具定義裡加 **`"strict": true`**（如上）——這才開啟平台層的 schema 強制。所以可靠的結構化輸出 = **強制呼叫（`tool_choice`）＋ 強制形狀（`strict`）**，兩個都要。
- **pydantic 一魚兩吃**：`model_json_schema()` 產出餵給 API 的 schema（不必手寫易錯的 JSON schema），`model_validate()` 在拿回 `input` 後再做一次型別/必填驗證並轉成好用的型別物件。schema 定義和驗證用**同一份 pydantic 模型**，不會兩邊不一致。即使開了 `strict`，這層本地驗證仍是值得留的最後保險（也順便擋語意問題，見第四節）。
- **這招為什麼可靠**：因為 tool use（尤其配 `strict`）把「回什麼」鎖成「填這張表」——它不會回「散文 + JSON」，只會回一個 structured `input`，不是靠你 prompt 拜託。

這個模式在 agent 系統裡無所不在：orchestrator 用它拿「子任務陣列」（Ch 27）、router 用它拿「分類列舉值」、抽取 agent 用它拿「欄位物件」。**只要你需要模型回機器可讀的東西，就定一個 schema 工具 + 強制 `tool_choice`。**

## 三、做法 3：原生 structured outputs（平台層的保證）

工具呼叫雖可靠，但它本質是「借用工具機制來拿結構」，語意上有點繞（你不是真的要呼叫什麼工具，只是要一份資料）。所以 Anthropic 也提供**原生的 structured outputs**：你直接給 API 一份 JSON schema，要求**輸出嚴格符合**它——平台層保證回來的是合法、符合 schema 的 JSON，不必再借工具、也不必擔心 markdown 包裹或尾逗號那類問題。

概念上它跟做法 2 很像（都是「給 schema、拿形狀有保證的輸出」），差別在：

- **語意更直接**：你就是要「一份符合這個 schema 的輸出」，不是「借用一個工具呼叫來夾帶資料」。
- **適用情境不同**：做法 2 是「我要模型回一份資料給程式吃」；原生 structured outputs 則是把「整個回應就是一份符合 schema 的 JSON」變成 API 的一個輸出格式設定。兩者都由平台層保證形狀，是**互補**的結構化輸出手段（Anthropic 文件就把「strict tool use」和「JSON 輸出」並列為結構化輸出的兩種形態）。

> **可用性與確切 API 形態請以官方文件為準**：structured outputs 是相對較新的能力——「怎麼指定輸出 schema」的參數名稱（例如以某個輸出格式設定指定 `json_schema`）、支援的模型、beta 標記等會隨版本變，而且**工具定義上的 `strict`** 跟**原生 JSON 輸出的格式設定**是兩個不同的開關，別混為一談。本章要你記住的是**概念與取捨**，細節查文件。

實務建議：**做法 2 和做法 3 都把「形狀」交給平台機制保證，差別只是語意與適用情境。** 看你的平台/SDK 版本支援哪個：要「整個回應就是一份 JSON」用原生 structured outputs（更直接）；要「模型在 agent 流程中回一份資料給下一步」用工具呼叫 + `strict`（最通用可攜）。**唯一要避免的是把做法 1（純 prompt-and-parse）當成生產主力**——它沒有任何形狀保證。

## 四、即使「形狀保證」了，你仍要做語意驗證

這是最多人忽略、卻最重要的一點：**schema 保證的是「結構」，不是「內容對不對」。**

schema 能保證：`amount` 是個數字、`subtasks` 是字串陣列、`category` 是三個列舉值之一。schema **保證不了**：

- `amount` 是 `-5000`（型別對，但訂單金額不該是負的）。
- `subtasks` 是空陣列 `[]`（形狀合法，但「拆成 0 個子任務」對你的 orchestrator 是壞輸入）。
- `date` 是 `"2099-13-45"`（是字串，但不是合法日期）。
- 模型把 `category` 填成 `"退款"`，但其實這個請求根本是技術問題（形狀對、語意錯）。

所以**邊界驗證仍然必要**（呼應 Ch 20、Ch 25 的「在系統邊界驗證」原則）：

```python
bd = TaskBreakdown.model_validate(tool_use.input)   # 結構驗證（pydantic）
# 語意驗證——schema 管不到的業務規則
if not bd.subtasks:
    raise ValueError("拆解結果為空，無法 fan-out")
if len(bd.subtasks) > 20:
    raise ValueError("拆太細（>20），請收斂")        # 接 Ch 27：拆太細成本爆炸
```

pydantic 其實能幫你把一部分語意驗證也寫進模型（用 `field_validator`、`conint(gt=0)` 之類的約束），讓「結構 + 基本業務規則」一起驗。但跨欄位、跨系統的規則（「這個 category 跟 description 對得上嗎」）通常還是要你自己檢查。

核心原則：**結構化輸出讓你不用再操心「格式會不會壞」，但「內容對不對」永遠是你的責任。** 別因為 schema 合法就照單全收，尤其當這份資料要去驅動真實動作（寫資料庫、派 worker、做決定）。

## 五、一個取捨：別太早把模型「鎖進表格」

結構化輸出很好，但有個微妙的代價：**強制結構會限制模型的「思考空間」**。如果你要模型一邊做複雜推理、一邊只能填一個沒有「思考欄位」的緊湊表格，它的推理品質可能變差——因為它沒地方「想出聲」。

兩個實務對策：

- **給一個「思考欄位」**：在 schema 裡留一個 `reasoning` / `rationale` 字串欄（上面 `TaskBreakdown` 就有），讓模型先在那裡推理，再填結論欄位。這保留了它「想清楚再答」的空間。
- **分兩步：先推理，再抽取**：第一次呼叫讓模型**自由文字**地分析（充分推理），第二次呼叫（或用一個便宜模型）把那段自由文字**抽取成結構化**。複雜任務這樣往往比「一步逼出結構」品質更好——這也是 Ch 26 抽取型 subagent 的典型用法（讓 Haiku 把長文字抽成欄位）。

一句話：**結構化是給「最終要被程式吃的那一步」用的，不是給「需要深度推理的那一步」用的。** 想清楚你是要模型「思考」還是「交付結構」，必要時把兩者拆開。

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 怎麼拿結構化資料 | prompt 寫「回 JSON」+ 自己解析 | **schema 機制強制（工具呼叫 / 原生 structured outputs）** | 生產一律用機制強制；prompt-and-parse 只配原型 |
| 工具呼叫的 `tool_choice` | `auto`（模型自己決定） | **`tool`（強迫呼叫指定工具）** | 要保證拿到結構 → 強制指定那個工具 |
| schema 定義方式 | 手寫 JSON schema | **pydantic 模型（`model_json_schema`）** | pydantic：定義+驗證同一份，不易出錯 |
| 拿到結構後 | 直接信任使用 | **再做語意/業務規則驗證** | 一定要驗：schema 保證結構不保證內容 |
| 複雜推理 + 要結構 | 一步逼出緊湊結構 | **留 reasoning 欄 / 先推理再抽取** | 別太早鎖表格，保留思考空間 |
| 原生 vs 工具呼叫 | 一律工具呼叫 | **平台支援就用原生 structured outputs** | 原生更直接；不確定支援用工具呼叫保底 |

## 踩雷集錦

1. **生產靠 prompt-and-parse**：「請回 JSON」+ `json.loads`，遲早被多餘前言/markdown 包裹/尾逗號炸掉。用 schema 機制強制。
2. **用了工具但 `tool_choice` 設 auto**：模型可能選擇**不**呼叫工具、改回自由文字，你又拿不到結構。要結構就 `{"type":"tool","name":...}` 強制。
3. **以為 schema 合法 = 內容正確**：金額負數、空陣列、假日期、分類錯——形狀都合法。一定要加語意驗證。
4. **手寫 JSON schema 跟驗證邏輯各寫一份**：兩邊容易不一致。用 pydantic 一份模型同時產 schema + 驗證。
5. **把需要深度推理的任務硬塞進緊湊結構**：模型沒地方思考，品質下降。留 reasoning 欄，或先推理再抽取。
6. **結構化輸出沒設 `max_tokens` 餘裕**：複雜結構（長陣列、巢狀物件）可能被 `max_tokens` 截斷成不完整、無法解析的 JSON。給足空間，並處理截斷情況。
7. **巢狀過深 / schema 過於複雜**：模型較難可靠填好極深的巢狀結構，也較易出錯。schema 盡量扁平、欄位語意清楚（呼應 Ch 18 schema 設計）。

## 進階：再往深一層

- **串流結構化輸出**：結構化輸出也能串流（Ch 8），但你拿到的是**逐步成形的部分 JSON**——中途的字串不是合法 JSON，不能邊收邊 `json.loads`。要嘛等完整、要嘛用支援「部分 JSON」的解析器。SDK 通常提供事件讓你知道某個欄位收完了。
- **結構化輸出 vs 工具呼叫的本質統一**：到這裡你應該看出來——**工具呼叫、結構化輸出，底層是同一件事**：給模型一份 schema、讓它產出符合 schema 的資料。「呼叫工具」只是這份資料剛好被 harness 拿去執行某個函式；「結構化輸出」是這份資料被你的程式直接消費。理解這點，Ch 18-20 和本章就連成一體了。
- **列舉與聯集型別**：router 分類這種「就這幾個值」的場景，用 schema 的 `enum` 把選項鎖死，比讓模型自由產字串再比對可靠得多。pydantic 的 `Literal["退款","技術","帳務"]` 直接對應。
- **schema 演進與相容**：結構化輸出的 schema 跟 API 一樣會演進（加欄位、改型別）。下游程式要對「多了沒見過的欄位」「某選用欄位缺了」有容忍度，別一變動就崩——這跟一般 API 版本相容是同一套思維。
- **結構化輸出在多 agent 裡是「介面契約」**：當 orchestrator 把子任務以結構化形式發給 worker、worker 又以結構化形式回報（Ch 26/27），這些 schema 就成了 agent 之間的**介面契約**。把它們設計好、版控好，多 agent 系統才不會在「彼此格式對不上」這種低級問題上崩。
- **這跟 OpenAI 的「JSON mode / function calling」是同類概念**：各家 API 都有對應機制（function calling、JSON mode、structured outputs）。原理一致：用 schema 把模型輸出收斂成可解析的形狀。學會 Anthropic 這套，換平台也是同樣的思路。

## 動手練習

1. 用第二節的模式（pydantic + `tool_choice` 強制）實作一個 `decompose`：給它一個大任務，拿回一個**保證是字串陣列**的子任務清單。故意把同一個 prompt 跑十次，確認形狀每次都對（對照「prompt 請回 JSON」的不穩定）。
2. 實作一個 **router 分類器**：schema 用 `Literal` 鎖死三個分類，強制模型回其中之一。試著餵模稜兩可的輸入，看它怎麼選——並加一個「信心不足時回 `unknown`」的選項。
3. **語意驗證練習**：在第一題的結果上加驗證——空陣列、>20 個、有重複子任務都要擋下並回可行動的錯誤。體會「結構對 ≠ 內容對」。
4. **先推理再抽取**：對一個需要推理的任務，先讓模型自由文字分析，再用第二次呼叫把分析抽成結構。對照「一步逼出結構」的品質差異。
5. （概念）為一個 orchestrator→worker 的介面設計兩份 schema（派工單、回報單）。想想哪些欄位必填、哪些選用、worker 失敗時怎麼用結構表達「這塊有缺口」（接 Ch 27 容錯）。

## 本章重點整理

- agent 系統內部需要**機器可讀**的輸出（拆解清單、分類值、抽取欄位、工具參數）。「散文裡的 JSON」沒有形狀保證，遲早炸。
- 三條路：**prompt-and-parse（低，只配原型）< 工具呼叫強制（高，最通用）< 原生 structured outputs（最高，平台支援時首選）**。
- **工具呼叫回的就是結構化輸出**：定 `input_schema` + 用 `tool_choice={"type":"tool","name":...}` 強迫模型填那張表，從 `tool_use.input` 拿結構。你前面每次定工具都在做這件事。**強制呼叫（`tool_choice`）和強制形狀（工具定義上的 `strict`）是兩件事，要可靠的結構兩個都開。**
- **用 pydantic 一份模型**同時產 schema（`model_json_schema`）和驗證（`model_validate`），定義與驗證不會脫節。
- **schema 保證結構，不保證內容**：金額負數、空陣列、假日期、分類錯都可能形狀合法。**語意/業務驗證仍是你的責任**（系統邊界驗證，Ch 20/25）。
- **別太早把模型鎖進緊湊表格**：留 `reasoning` 欄，或「先自由推理、再抽取成結構」，保住推理品質。

## 自我檢核

- [ ] 我能說出「prompt 請回 JSON 自己解析」為什麼在生產上不可靠
- [ ] 我能解釋「工具呼叫回的就是結構化輸出」，說出 `tool_choice` 各值的差別，並知道「強制呼叫（`tool_choice`）」和「強制形狀（`strict`）」是兩件事
- [ ] 我能用 pydantic + 強制 tool_choice 寫出一個拿結構化資料的函式
- [ ] 我能舉出至少三個「schema 合法但內容錯」的例子，並說明為何仍要語意驗證
- [ ] 我能說明「強制結構可能傷推理品質」以及兩種對策
- [ ] 我能講清楚結構化輸出在多 agent 系統裡作為「介面契約」的角色

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool use 概覽](https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview)** — Anthropic
  - **讀哪裡**：`tool_choice` 的三種模式（auto / any / tool）、`input_schema` 的定義、回傳的 `tool_use` block 結構。
  - **能學到什麼**：本章做法 2 的權威依據——怎麼用工具呼叫穩穩拿到結構化資料。
  - **前提知識**：Ch 18-20（工具 schema、描述、結果）。

- **[Anthropic — Structured outputs](https://docs.claude.com/en/docs/build-with-claude/structured-outputs)** — Anthropic
  - **讀哪裡**：怎麼指定輸出的 JSON schema、strict 模式的保證、支援的模型與限制。
  - **能學到什麼**：本章做法 3 的確切 API 形態與可用性——平台層保證 schema 一致的最新細節。
  - **前提知識**：先讀過本章第二、三節，理解「為什麼要 schema 強制」。

### 工具 / 函式庫

- **[Pydantic 官方文件](https://docs.pydantic.dev/latest/)** — Pydantic
  - **讀哪裡**：`BaseModel`、`model_json_schema()`、`model_validate()`、`field_validator`、`Field` 約束（`gt`、`Literal` 等）。
  - **能學到什麼**：用一份模型同時定義 schema 與驗證——本章所有程式碼範例的底層工具。
  - **為什麼值得讀**：pydantic 幾乎是 Python 結構化資料的事實標準，學會它在 agent 工程裡到處都用得上（schema、設定、驗證）。

下一章是 Part 4 的最後一塊輸入/輸出能力：**多模態輸入**。到目前為止 agent 看到的都是文字，但很多真實任務要它**看圖**——讀截圖除錯、看設計稿產程式碼、解析 PDF/圖表。下一章談怎麼把圖片等非文字內容餵給模型，以及它對 context、成本、工具設計的影響。

→ [Ch 33 多模態輸入](./33-multimodal-input.md)
