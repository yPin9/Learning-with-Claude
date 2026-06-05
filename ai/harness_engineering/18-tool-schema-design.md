# Ch 18 — 好的 tool 長什麼樣：schema 設計

> **目標**：學會把一個工具的 **input schema** 設計好。讀完你能說出 schema 的每個部位（`name` / `description` / `input_schema`）各自在對模型說什麼、為什麼「型別、enum、required、巢狀深度、工具粒度」這些選擇會直接決定模型用得對不對，並能把一個爛 schema 改成讓模型一次就用對的好 schema。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版）。本章延續 Ch 5 的 tool calling 協議——如果你還不熟 `tool_use` / `tool_result` 怎麼一來一回，先回看 [Ch 5 — Tool calling 協議](./05-tool-calling-protocol.md)。

## 為什麼 schema 設計是 agent 成敗的關鍵

Part 1 你已經讓 agent 會用工具了。但那時的工具都很陽春——`get_current_time`、`calculate`，schema 隨便寫都能動。一旦工具變多、變複雜，你會撞到一個殘酷的事實：**模型用不用得對工具，幾乎完全取決於 schema 寫得好不好。**

回想 Ch 5：模型決定要呼叫哪個工具、填什麼參數，靠的**只有你給的 schema**。它看不到你的工具實作、看不到你的程式註解、不知道你心裡想的那些「不言而喻」的規則。schema 就是模型對這個工具的**全部認知**。schema 沒寫清楚的，模型就只能猜——而猜錯的代價是：呼叫了不該呼叫的工具、填了非法的參數、漏填了必填項、或乾脆不敢用這個工具。

所以 tool schema 設計不是「填個 JSON 格式」的雜事，它是 **prompt engineering 的一種**——你是在用 schema 這個語言，跟模型溝通「這個工具是幹嘛的、怎麼正確使用它」。這章談 schema 的**結構**（型別、參數、必填、粒度）；下一章 [Ch 19](./19-tool-descriptions-as-prompt.md) 專門談 schema 裡的**文字描述**怎麼寫（它本身就是 prompt）。兩章合起來，才是完整的「好工具」。

## 先建立直覺：schema 是「給模型填的表單」

想像你要請一個能幹但**完全照字面辦事**的新同事幫你做事。你不能口頭含糊交代，你得給他一張**表單**：表單上有欄位名、每欄要填什麼型別（數字？日期？單選？）、哪些必填、哪些可空、單選欄有哪些選項。表單設計得好，他一次就填對；表單設計得爛（欄位叫「資料」、型別不明、選項不列），他要嘛填錯、要嘛回來問你一堆。

tool schema 就是這張表單，而模型就是那個「能幹但照字面辦事」的同事：

```
   一個 tool schema = 一張表單
   ┌────────────────────────────────────────────┐
   │ 工具名稱: send_email          ← 表單標題      │
   │ 用途說明: 寄一封 email 給指定收件人  ← 抬頭說明 │
   ├────────────────────────────────────────────┤
   │ 欄位:                                        │
   │  • to       [字串, 必填]   收件人 email 地址   │
   │  • subject  [字串, 必填]   主旨               │
   │  • body     [字串, 必填]   內文               │
   │  • priority [單選, 選填]   normal / high      │
   └────────────────────────────────────────────┘
```

模型「填表單」的依據，就是這張表單自己寫了什麼。**表單沒說的，模型不知道。** 這就是整章的核心心法——設計 schema 時不斷問自己：「一個只看得到這張表單、看不到我腦袋的人，會不會填錯？」

## 一、schema 的三個部位

一個 Anthropic 工具定義（Ch 5 看過）由三部分組成，每一部分都在對模型說不同的話：

```python
weather_tool = {
    "name": "get_weather",                        # ① 工具叫什麼
    "description": "查詢某個城市目前的天氣狀況。",    # ② 這工具是幹嘛的、何時用
    "input_schema": {                             # ③ 參數長什麼樣（JSON Schema）
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名稱，例如 'Taipei'、'Tokyo'",
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],   # 單選：只能是這兩個之一
                "description": "溫度單位，預設 celsius",
            },
        },
        "required": ["city"],                     # city 必填，unit 選填
    },
}
```

- **`name`**：模型在 reasoning 時會用名字判斷「這是不是我現在需要的工具」。名字要動詞開頭、語意明確（`get_weather` 好過 `weather`，更好過 `tool1`）。
- **`description`**：說明工具的用途與**使用時機**。這是模型決定「要不要呼叫這個工具」的主要依據，份量極重——所以 Ch 19 整章在談它。本章先放著。
- **`input_schema`**：這就是「表單欄位」，用 **JSON Schema** 描述。它是本章的主角。

`input_schema` 永遠是一個 `type: "object"`，底下 `properties` 列出每個參數，`required` 列出哪些必填。沒列進 `required` 的就是選填。

這三個是**核心**欄位；工具定義另外還有 `strict`、`input_examples`、`cache_control` 等選填欄位（本章進階與後續章節會碰到），入門先掌握這三個即可。

## 二、JSON Schema：你能用的積木

`input_schema` 用的是 JSON Schema 的子集。實務上最常用的積木就這幾種：

```python
"input_schema": {
    "type": "object",
    "properties": {
        # 字串
        "query": {"type": "string", "description": "搜尋關鍵字"},

        # 數字（integer 整數 / number 含小數）
        "limit": {"type": "integer", "description": "回傳幾筆，1-100"},
        "threshold": {"type": "number", "description": "相似度門檻 0.0-1.0"},

        # 布林
        "include_archived": {"type": "boolean", "description": "是否含已封存項目"},

        # 單選（enum）：值只能是清單裡的一個——這是約束模型的利器
        "sort": {
            "type": "string",
            "enum": ["relevance", "date", "popularity"],
            "description": "排序方式",
        },

        # 陣列
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要篩選的標籤清單",
        },

        # 巢狀物件（謹慎使用，見第四節）
        "date_range": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "起始日 YYYY-MM-DD"},
                "end": {"type": "string", "description": "結束日 YYYY-MM-DD"},
            },
            "required": ["start", "end"],
            "description": "日期區間篩選",
        },
    },
    "required": ["query"],
}
```

要記住：**模型會盡量遵守 schema，但 schema 不是鐵牢**。API 只驗證你的「工具定義」本身是否合法；它**不保證**模型生成的 `input` 一定符合你的 schema——一般（非 strict）tool use 下，模型仍可能填出不相容型別、漏掉 `required` 欄位。要在生成階段就保證 `input` 結構符合 schema，得開 strict mode（見本章進階、Ch 32），而且 strict 也只保證**結構**、不保證「`limit` 要在 1-100」這種**語意約束**。所以無論開不開 strict，呼叫工具前都應由**你的程式驗證**參數（Ch 5 的 `is_error`，Ch 20 會再談）。schema 負責「讓模型容易填對」，工具實作的驗證負責「填錯了也不會出事」。兩層都要有。

## 三、設計原則：讓模型一次填對

### 原則 1：能用 enum 就別用自由字串

這是投資報酬率最高的一條。如果一個參數的合法值是有限集合，**用 `enum` 列出來**，別讓模型自由填字串。

```python
# ❌ 自由字串：模型可能填 "high"、"High"、"urgent"、"重要"、"P0"...
"priority": {"type": "string", "description": "優先級"}

# ✅ enum：模型只能從這三個選一個，你的工具實作也不必處理一堆變體
"priority": {"type": "string", "enum": ["low", "normal", "high"]}
```

enum 同時幫了兩邊：模型不用猜你接受什麼格式、你的實作不用寫一堆 `if value in (...)` 的正規化。**只要值域有限，enum 幾乎永遠是對的選擇。**

### 原則 2：required 要誠實，並給選填項合理預設

把「沒有就無法執行」的參數放 `required`，其餘放選填。但別把所有東西都設成必填（模型會被迫硬填、可能瞎編），也別把該必填的設成選填（模型漏填，工具拿到 `None` 崩潰）。

選填參數要在 `description` 講清楚「不填的話預設行為是什麼」，否則模型不知道省略它會發生什麼：

```python
"unit": {
    "type": "string",
    "enum": ["celsius", "fahrenheit"],
    "description": "溫度單位，省略則用 celsius",   # ← 講明預設，模型才敢省略
}
```

### 原則 3：粒度要對——但「合併」和「拆分」都有對的時機

先講清楚：**「帶 `action` 參數的工具」本身不是錯**。Anthropic 官方甚至建議，把心智模型相近、參數高度重疊的相關操作**合併**成一個帶 `action` 的工具，反而能降低工具數量與模型的選擇模糊度（例如 `create_pr` / `review_pr` / `merge_pr` 合成一個 `pr` 工具）。

會出問題的是另一種——當你硬把**參數契約差很多、副作用/權限天差地別**的操作塞進同一個 `action`，就變成「上帝工具」：

```python
# ❌ 上帝工具：模型要先搞懂 action 跟其他參數的對應關係，很容易配錯
{
    "name": "file_op",
    "input_schema": {
        "properties": {
            "action": {"enum": ["read", "write", "delete", "list", "move"]},
            "path": {"type": "string"},
            "content": {"type": "string"},      # 只有 write 要
            "dest": {"type": "string"},         # 只有 move 要
        },
    },
}
```

問題：`content` 只有 `write` 時要、`dest` 只有 `move` 時要。JSON Schema 雖然能用 `oneOf` / 條件 schema 勉強表達「必填與否取決於 action」，但寫起來笨重、模型未必可靠遵守、strict 模式對這類複雜 schema 也有限制。結果就是模型得自己推論這層隱藏規則，很容易 `write` 卻漏填 `content`、或 `read` 卻多填了東西。更糟的是 `read` 跟 `delete` 副作用天差地別，卻共用同一個入口，模型一旦選錯 `action` 就直接刪檔。

```python
# ✅ 拆成數個單一職責的工具，每個的 required 都明確
{"name": "read_file",  "input_schema": {"properties": {"path": {...}}, "required": ["path"]}}
{"name": "write_file", "input_schema": {"properties": {"path": {...}, "content": {...}}, "required": ["path", "content"]}}
{"name": "move_file",  "input_schema": {"properties": {"src": {...}, "dest": {...}}, "required": ["src", "dest"]}}
```

拆開後每個工具的 schema 都乾淨、`required` 都誠實、模型不必推論隱藏對應，更不會「選錯 action 就誤刪」。**但別矯枉過正**：粒度太細（把 `read_file` 拆成 `read_file_line` / `read_file_range` / `read_file_all`）會讓工具數量爆炸，反而稀釋模型的注意力、撐大快取前綴（Ch 17）。

把合併與拆分的判準收成一條規則：

- **可以合併成帶 `action` 的工具**：同一個資源、心智模型相近、參數高度重疊、副作用與權限相當（例如對同一個 PR 的 `open`/`comment`/`close`）。
- **該拆成多個工具**：參數契約差很多、`required` 條件隨 action 而變、副作用/權限差異大（讀 vs 刪），或實測中模型常配錯參數。

`file_op` 屬於後者（`read` 和 `delete` 危險程度天差地別、參數也不重疊），所以拆。拿不準時，問自己：「模型選錯這個 `action` 的代價有多大？」代價越大，越該拆成獨立工具，讓危險操作有自己明確的入口。

### 原則 4：扁平優於深度巢狀

模型填扁平的參數比填深層巢狀的可靠。能攤平就攤平：

```python
# ❌ 沒必要的巢狀
"properties": {"config": {"type": "object", "properties": {
    "timeout": {...}, "retries": {...}}}}

# ✅ 攤平
"properties": {"timeout": {...}, "retries": {...}}
```

巢狀不是不能用——當參數**天生有結構**（像第二節的 `date_range` 有 start/end 一組）時，巢狀反而清楚。判準是：**這組欄位是不是一個有意義的整體？** 是就巢狀，只是為了分類而硬套一層 object 就攤平。

## 四、失敗示範：一個會被模型用錯的 schema

看一個真實常見的爛 schema，逐條診斷它為什麼會害模型出錯：

```python
# 反例！這個 schema 模型幾乎一定會用錯
bad_tool = {
    "name": "search",                          # 💥 名字太泛，搜什麼？檔案？網路？資料庫？
    "description": "搜尋",                       # 💥 等於沒說
    "input_schema": {
        "type": "object",
        "properties": {
            "q": {"type": "string"},            # 💥 縮寫、沒 description
            "type": {"type": "string"},         # 💥 該 enum 卻自由字串，模型會亂填
            "opts": {"type": "object"},         # 💥 黑洞物件，模型不知道裡面要填什麼
        },
        # 💥 沒有 required：模型不知道哪些非填不可
    },
}
```

把它交給模型，你會看到各種翻車：模型把 `type` 填成 `"檔案"`、`"file"`、`"FILE"` 各種變體；`opts` 要嘛空著、要嘛瞎編一堆鍵；因為沒有 `required`，模型有時連 `q` 都不填就呼叫。**這個工具不是壞在實作，是壞在 schema 沒把話講清楚。**

修好它：

```python
good_tool = {
    "name": "search_documents",                # ✅ 明確：搜尋文件庫
    "description": "在公司內部文件庫中以關鍵字搜尋文件，回傳最相關的幾筆標題與摘要。"
                   "當使用者問到公司文件、規章、過往報告時使用。",   # ✅ 用途 + 時機
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {                          # ✅ 完整命名
                "type": "string",
                "description": "搜尋關鍵字，用自然語言或關鍵詞皆可",
            },
            "doc_type": {                       # ✅ enum 取代自由字串
                "type": "string",
                "enum": ["policy", "report", "spec", "all"],
                "description": "限定文件類型，省略則搜全部（等同 all）",
            },
            "limit": {                          # ✅ 攤平、講明範圍與預設
                "type": "integer",
                "description": "回傳筆數，1-20，省略則 5",
            },
        },
        "required": ["query"],                  # ✅ 只有 query 非填不可
    },
}
```

改完之後，模型填這張表單的正確率會有肉眼可見的提升——因為每個欄位都「自我解釋」了。

## 對比與取捨

| 設計選擇 | 偏向 A | 偏向 B | 怎麼選 |
|---|---|---|---|
| 值域有限的參數 | 自由字串（彈性） | **enum（可靠）** | 幾乎永遠選 enum |
| 一個複雜工具 vs 多個簡單工具 | 帶 `action` 的合併工具（工具少） | 多個單一職責（清楚） | 參數重疊、副作用相當→可合併；契約差很多或危險度不一→拆 |
| 參數結構 | 深度巢狀（分類整齊） | **扁平（好填）** | 天生成組才巢狀，否則攤平 |
| required 範圍 | 全設必填（怕漏） | 誠實標必填 | 只標「沒有就無法跑」的 |
| 約束放哪 | 只寫 description | schema(enum/型別) + 實作驗證 | 兩層都要：schema 引導 + 實作兜底 |

## 踩雷集錦

1. **以為 schema 是「驗證格式」用的**：它更重要的角色是**對模型解釋這個工具**。模型靠它決定要不要用、怎麼填。把它當 prompt 寫，不是當資料驗證寫。
2. **該 enum 卻用自由字串**：值域有限就一定 enum。自由字串會收到大小寫/語言/同義詞各種變體，模型也得猜你接受什麼。
3. **黑洞參數 `{"type": "object"}` 不給 properties**：模型完全不知道裡面要填什麼，只能瞎編。要嘛把欄位列出來，要嘛拆成具名參數。
4. **全部設成必填，或全部選填**：全必填→模型被迫瞎編漏不掉的值；全選填→模型連關鍵參數都敢省略。`required` 要誠實反映「沒有就跑不了」。
5. **把危險度/契約差很多的操作塞進一個 `action`**：`action` 工具本身沒問題（相近操作合併反而好），但別把 `read` 跟 `delete`、必填條件隨 action 而變的操作硬塞同一個入口——模型得推論隱藏規則、選錯 `action` 還可能誤刪。這種要拆成單一職責工具。
6. **選填參數不講預設**：模型不知道省略它會發生什麼，要嘛不敢省、要嘛瞎填。`description` 要寫「省略則…」。
7. **參數名用縮寫/代號**：`q`、`opts`、`val` 這種名字逼模型猜語意。名字本身就是給模型的提示，要完整、要語意化。

## 進階：再往深一層

- **strict tool use（嚴格模式）**：在工具定義頂層設 `strict: true`，讓 API 在生成階段就保證 tool `input`**結構上**符合你的 schema（型別、required、enum 都不會違反）。這把「結構正確」從「模型盡量遵守」升級成「保證」。但有三個邊界要記住：(1) 它保證的是**結構**、不保證**語意**（`limit` 不會超出型別，但「1-20」這種範圍仍要你驗）；(2) 它只支援 JSON Schema 的一個**子集**，太複雜的 schema 不能用，且對 strict 工具的數量/複雜度有上限；(3) 即使開了 strict，遇到模型拒答、`max_tokens` 截斷、API 錯誤等情況輸出仍可能不完整——所以下游驗證不能省。用到時查當前模型對 strict 的支援與限制。Ch 32（structured output）會把這個機制講透。
- **schema 也算進 token 成本與快取前綴**：工具定義排在快取前綴的**最前面**（Ch 17 的 tools→system→messages）。所以 (1) schema 越肥，**首次寫入/未命中時**的固定成本越高（命中時是較便宜的 cache-read 成本，但仍要計 token）；(2) 一旦你改任何一個工具的 schema，整個快取前綴（tools/system/messages）全部失效、要重新寫入。這給「別把 schema 寫得又臭又長」「別頻繁改工具」一個實打實的成本理由。
- **JSON Schema 的進階關鍵字**：`minimum`/`maximum`、`minItems`/`maxItems`、`pattern`（正則）、`format`（如 `date`、`email`）這些在**一般 tool schema** 裡可以放，當作給模型的提示與你下游驗證的依據。但要注意：**strict / structured outputs 只支援子集**，部分 SDK 遇到不支援的限制（如 `minimum`/`maxLength`）會把它從 schema 搬到 `description`、再於客戶端驗證。所以別把這些關鍵字當成 API 會嚴格強制的硬約束。先用 enum + 型別 + 清楚的 description 解決 80%，剩下的再考慮這些。
- **工具一多就要管理**：當工具數量上到幾十個，全部塞進每次請求會稀釋注意力又撐大前綴。這時要用「動態揭露/檢索工具」的策略——[Ch 23 — Tool search / deferred tools](./23-tool-search-deferred.md) 專門處理這個問題。本章先把「單一工具的 schema」設計好；工具集合層級的管理是後話。

## 動手練習

1. 拿你練習 A 的 `read_text_file` 工具，故意把它的 schema 改爛（名字改 `f`、`path` 的 description 刪掉、加一個沒 properties 的 `options` object），跑幾個任務，觀察模型怎麼用錯。再改回來，體會差異。
2. 設計一個 `create_calendar_event` 工具的 schema：要有標題、開始時間、結束時間、與會者清單、重複規則（每日/每週/每月/不重複）。練習決定：哪些 required？哪些該 enum？與會者清單用什麼型別？重複規則該不該巢狀？
3. 找一個你寫過的「上帝工具」（或上面的 `file_op`），把它拆成數個單一職責工具，比較拆前拆後模型用錯的機率。
4. 把第二題的 schema 餵給 `count_tokens`（Ch 10），看一個工具定義佔多少 token——體會工具一多，前綴成本會怎麼長。

## 本章重點整理

- tool schema 是模型對工具的**全部認知**——它看不到你的實作，schema 沒寫的它只能猜。所以 schema 設計是 prompt engineering，不是資料驗證。
- 三個部位：`name`（語意化、動詞開頭）、`description`（用途+時機，Ch 19 詳談）、`input_schema`（JSON Schema 描述參數）。
- 投報率最高的原則：**值域有限就用 enum**；其次是 required 誠實、選填講預設、一工具一動作、扁平優於巢狀。
- schema 負責「讓模型容易填對」，工具實作的驗證負責「填錯了也不出事」——兩層防線都要。
- 進階：strict mode 保證結構（不保證語意）、schema 算進快取前綴成本、工具太多要靠 Ch 23 的動態揭露。

## 自我檢核

- [ ] 不看本章，我能解釋「為什麼 schema 寫不好模型就用不好工具」——關鍵是模型只看得到 schema
- [ ] 面試時被問「什麼參數該用 enum」，我能立刻答出判準並舉例
- [ ] 我能指出一個「上帝工具」的具體問題，並說明怎麼拆、以及為什麼別拆過頭
- [ ] 我能說出 schema 約束和工具實作驗證各自負責什麼，為什麼兩層都要
- [ ] 拿我工作中用過的一個工具，我能評估它的 schema 哪裡會害模型填錯、怎麼改

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool use overview](https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview)**
  - **讀哪裡**：`input_schema` 的結構、`name`/`description` 的角色、以及「specifying tools」那節的範例。
  - **能學到什麼**：本章每個欄位的權威定義與官方建議的寫法，尤其是 description 與 schema 怎麼影響模型的工具選擇。
  - **前提知識**：Ch 5（tool calling 協議）看完即可。

- **[JSON Schema — Understanding JSON Schema](https://json-schema.org/understanding-json-schema/)**
  - **讀哪裡**：`type`、`properties`、`required`、`enum`、`array`/`object` 那幾節；`minimum`/`pattern`/`format` 可選讀。
  - **能學到什麼**：`input_schema` 用的就是 JSON Schema 的子集——這份是它的權威教學，幫你正確使用本章用到的所有積木。
  - **前提知識**：懂 JSON 即可。

### 部落格 / 技術文章

- **[Writing tools for agents（Anthropic）](https://www.anthropic.com/engineering/writing-tools-for-agents)** — Anthropic Engineering
  - **這篇說什麼**：從「站在 agent 角度設計工具」出發，談工具命名、粒度、回傳格式、token 效率——和本章「schema 是給模型的表單」完全同調，且推得更深。
  - **讀哪裡**：談 tool 命名與粒度、以及「為 agent 而非為人類設計 API」的段落。
  - **為什麼值得讀**：這是目前對「agent 友善的工具設計」寫得最系統的一篇，本章原則的權威背書與延伸。

下一章我們把放到一邊的 `description` 拿回來認真寫——你會發現工具的描述文字本身就是一段 prompt，寫得好不好，決定模型在「該用工具的時候用、不該用的時候不亂用」。

→ [Ch 19 Tool 描述就是 prompt](./19-tool-descriptions-as-prompt.md)
