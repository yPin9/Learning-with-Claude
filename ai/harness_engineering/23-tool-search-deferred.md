# Ch 23 — Tool search 與 deferred tools

> **目標**：你已經會設計單一工具（Ch 18-22）。但當工具從 5 個長到 50 個、200 個，會發生什麼？讀完你能說出「工具太多」會在三個地方付出代價（注意力稀釋、快取前綴膨脹、每次請求的 token/延遲），理解 deferred tools（延遲載入）與 tool search（按需發現）這套機制怎麼把「一大櫃工具」變成「需要時才遞到模型手上」，能說出它本質上就是 Ch 15 的 RAG 套到工具上，也知道什麼時候**不該**用它（工具不多時直接全載最好）。

> **環境**：概念 + Anthropic Messages API。Anthropic 的「Tool Search Tool」已是 server-side GA 功能，但**確切的 type 字串、欄位名稱會隨版本演進**——本章重點是**機制與取捨**，實作前請對照當前官方文件確認識別字串。**有趣的是：你正在用的這個 harness（Claude Code）此刻就在用「延遲載入工具」這個概念**——下面會指出來，但也會分清楚「API 的機制」與「某個 harness 怎麼呈現它」。

## 為什麼需要這個？工具多了會壞掉

前面五章你學會把單一工具做好。但真實的 agent 不是只有一個工具：一個能寫程式的 agent 可能有檔案讀/寫/列/改、shell、git 操作、跑測試、查文件、搜尋網路……再接上幾個 MCP server（Ch 24），工具數量輕鬆破百。

直覺上「工具越多，agent 能力越強」——但這是個陷阱。工具一多，會在**三個地方**同時付出代價：

1. **注意力稀釋**：所有工具的 schema（名稱、描述、參數，Ch 18/19）每次請求都塞進 context。50 個工具的描述可能就是好幾千、上萬 token 的牆。模型要在這面牆裡挑對工具——工具越多、彼此越像，挑錯的機率越高（Ch 19 講的 disambiguation 在 100 個工具裡會崩潰）。研究與實務都觀察到：**工具數量過多會降低工具選用的準確率**。
2. **快取前綴膨脹**：Ch 17 講過，工具定義通常放在 prompt 最前面、是快取前綴的一部分。工具 schema 是固定的、適合快取——但「固定的一大塊」也意味著**每個請求都要載入這一大塊**（命中快取仍有讀取成本，且任何工具定義變動就讓整段快取失效）。
3. **每次請求的 token 與延遲**：就算快取命中，輸入 token 仍照算（快取讀取較便宜但非免費），而且更長的 context 通常對應更高的延遲。你為了「以防萬一」掛上的那 90 個這次用不到的工具，每一次對話輪都在付錢、付延遲。

```
   工具數量 ↑
   ┌─────────────────────────────────────────┐
   │ 5 個工具    schema 牆很薄，全塞進去最省事     │ ← 直接全載
   │ 20 個工具   開始佔可觀 token，但還能接受        │ ← 灰色地帶
   │ 100+ 個工具 牆比很多任務本身還大；模型挑錯、     │ ← 需要 tool search
   │            快取前綴肥、每輪都付這筆稅            │
   └─────────────────────────────────────────┘
```

核心矛盾：**你希望 agent「有能力做很多事」（工具多），又希望每次請求「只看到此刻相關的少數工具」（context 乾淨）。** Deferred tools + tool search 就是同時要這兩者的方法。

## 一、核心直覺：這是「給工具做的 RAG」

回想 Ch 15 的 RAG：知識庫有上萬份文件，但你不會把全部塞進 context——你**按查詢檢索出相關的幾份**再注入。Tool search 是**一模一樣的點子，只是檢索的對象從「文件」換成「工具定義」**：

```
   RAG（Ch 15）                     Tool search（本章）
   ─────────────                    ─────────────────
   上萬份文件                        上百個工具
   不全塞 context                    不全塞 context
   依 query 檢索相關文件               依任務檢索相關工具
   注入檢索到的內容                    注入檢索到工具的完整 schema
```

把這個對應記牢，本章其他東西都是它的細節。差別有兩點要講清楚，免得類比把你帶歪：(1) 文件檢索回來是「給模型讀的內容」，工具檢索回來是「給模型**用**的能力與 schema」——檢索錯的代價更直接（模型會少掉它本來該有的能力）。(2) 這是**架構上的**類比（都在做 just-in-time 檢索），**不代表底層一定用向量 embedding**——下面第三節會講，Anthropic 官方的工具檢索變體是 regex 或 BM25，不是語意向量。所以別把「給工具做的 RAG」讀成「一定是 embedding 語意搜尋」。

## 二、機制：deferred loading + 按需發現

整套機制拆成兩個動作：

**① Deferred loading（延遲載入）**：你仍然定義全部 100 個工具，但其中大部分標記為「延遲」（`defer_loading: True`）——這些工具的**完整定義一開始不進 context**（從 system prompt 的工具區段移除）。模型一開始只看到**沒有延遲的核心工具**（每個任務幾乎都要用的，像 `read_file`）+ 一個「工具搜尋工具」。延遲的工具對模型而言是「一個能查的目錄裡的條目」，要搜出來才會展開。

> **注意「API 機制」與「harness 呈現」的差別**：在 Anthropic API 裡，deferred 工具的完整定義不進初始 context，模型透過搜尋去查一個工具目錄。**有些 harness（例如 Claude Code）會額外把 deferred 工具的「名字清單」提示給模型**——那是 harness 的設計選擇，不是 deferred loading 的必要語義。下面活例子那段就是 Claude Code 這樣做。

**② 按需發現（tool search）**：context 裡放一個特別的「工具搜尋工具」。模型發現「我需要一個能寄 Slack 訊息的工具」時，就**呼叫這個搜尋工具**查詢，API（或你的 harness）找出相符的工具、把**它們的完整定義注入**回對話。此後那幾個工具就「正式上線」，模型能像一般工具一樣呼叫。

```
   一般做法（全載）                      Deferred + search
   ─────────────                       ─────────────────
   system prompt                        system prompt
   ├ tool A 完整 schema                  ├ tool_search（唯一全載的「元工具」）
   ├ tool B 完整 schema                  ├ 核心工具 read_file 完整 schema
   ├ … （98 個）                         └ 其餘 98 個工具：定義不在 context 裡（可被搜）
   └ tool Z 完整 schema
                                        模型：search("send slack message")
   每輪都背著整面 schema 牆               ← 命中的 slack_send 定義被注入回對話
                                        模型：呼叫 slack_send(...)
```

關鍵體會：**deferred 的工具不是「不存在」，是「還沒展開」**。它在一個可被查詢的目錄裡，需要時把它叫出來。這跟人用一個有幾百個指令的 CLI 一樣——你不會背下全部 `--help`，你需要時 `grep` 一下。

這也直接回應第一節的快取問題：deferred 工具的定義**不進 system prompt 的前綴**，所以你新增/修改一個 deferred 工具時，**不會弄髒 Ch 17 那段固定的快取前綴**——這正是延遲載入除了省 token 之外的另一個好處。

### 你正在用的這個 session 就是活例子

打開這次對話的 system 訊息，你會看到類似這樣的東西（這是 Claude Code 餵給我的）：

```
The following deferred tools are now available via ToolSearch. Their schemas
are NOT loaded — calling them directly will fail. Use ToolSearch with query
"select:<name>" to load tool schemas before calling them:
  AskUserQuestion  CronCreate  NotebookEdit  WebFetch  WebSearch
  mcp__notion__notion-search  mcp__plugin_discord_discord__reply  …
```

這是延遲載入概念的一個**具體 harness 樣貌**（Claude Code 的實作，不是 Anthropic API 的規範形狀）：幾十個工具（很多來自 MCP server，注意那些 `mcp__…` 前綴——這是 Ch 24 的主題）被設成 **deferred**，這個 harness 選擇把它們的**名字清單**提示給模型，但**參數 schema 不載入**。當模型真的需要某個工具（例如本章稍早我要用 `TodoWrite`），得先用 `ToolSearch` 把它的 schema 撈出來，才能呼叫。沒撈就直接呼叫，會得到 harness 層的 `InputValidationError`（這是這個 harness 的錯誤名，不是 Messages API 的標準錯誤）——因為那個工具的參數規格還沒載入。

這不是巧合。Claude Code 接了一堆 MCP server 與內建工具，全載會是一面巨大的 schema 牆，所以它把不常用的設成 deferred。**你讀這章的同時，正在第一線體驗它解決的問題**——只是記得：「列出名字」是這個 harness 的呈現方式，API 機制本身不要求這一步。

## 三、Anthropic 的 Tool Search Tool（API 層）

上面的機制，Anthropic 在 Messages API 提供了官方支援，叫 **Tool Search Tool**（server-side，已 GA，不需要 beta header）。用法的形狀大致是：

```python
# 形狀示意——確切的 dated type 字串以當前官方文件為準
response = client.messages.create(
    model="claude-opus-4-8",
    tools=[
        # ① 一個「工具搜尋」server tool，由 Anthropic 提供實作。
        #    目前有兩種變體：regex（用 Python re 的 pattern 比對）與 bm25（自然語言查詢）
        {"type": "tool_search_tool_bm25_20251119", "name": "tool_search"},

        # ② 你自己的工具，想延遲的標記 defer_loading（完整定義平常不進 context）
        {"name": "read_file", "description": "...", "input_schema": {...}},   # 核心工具，照常全載
        {"name": "slack_send", "description": "...", "input_schema": {...},
         "defer_loading": True},          # ← 延遲：定義平常不進 context，等被搜出才展開
        {"name": "jira_create_issue", "...": "...", "defer_loading": True},
        # … 再 98 個 defer 的工具
    ],
    messages=[...],
)
```

運作流程：模型看到一個它能用的「搜尋工具」+ 沒延遲的核心工具。需要某個 deferred 能力時，它對搜尋工具發查詢，API 端做檢索、把命中工具以 `tool_reference` 內聯展開進對話歷史，模型接著就能呼叫那些工具（之後同一對話內可重用，不必重搜）。**省下的是「那 98 個 deferred 工具的完整 schema 不必每輪都佔 context」**——只有被搜出來的才展開。

幾個務實重點：

- **查詢型態不一定是「語意」**：官方目前兩種變體——`regex` 變體查的是 Python `re.search()` 的 pattern（不是自然語言）、`bm25` 變體吃自然語言關鍵字。**兩者都不是向量 embedding 的語意檢索**（這點對下面的 RAG 類比很重要）。
- **核心工具別 defer**：每個任務幾乎都會用到的工具（`read_file`、`run_command`）設成不延遲，省下「每次都要先搜一下」的來回。Defer 留給長尾——那些「偶爾才需要」的工具。
- **這是 server-side 檢索**：用 Anthropic 的 Tool Search Tool 時，檢索由 API 端做，你不用自己寫檢索邏輯。但代價是多一次工具呼叫的來回（見「對比與取捨」）。
- **dated type 字串會變**：`tool_search_tool_bm25_20251119` 這種帶日期的識別字串會隨版本更新（也有不帶日期的別名）。`defer_loading` 是現行的正式欄位。**實作前以你呼叫當下的官方文件為準**——本章重心放在「機制與何時用」而非死記某個 dated 字串。

## 四、自己實作一套（不靠官方功能時）

如果你不用 Anthropic 的 Tool Search Tool（例如你想要可控的檢索邏輯、或在別的模型上跑），這套機制可以自己做。本質上你需要一個**工具登錄表 + 一個搜尋工具 + 注入邏輯**：

```python
# 全部工具登錄在這裡（schema 完整存著，但平常不全餵給模型）
TOOL_REGISTRY = {
    "slack_send": {"description": "傳訊息到 Slack 頻道",
                   "input_schema": {...}, "keywords": ["slack", "message", "notify"]},
    "jira_create_issue": {"description": "建立 Jira issue", "input_schema": {...},
                          "keywords": ["jira", "ticket", "issue", "bug"]},
    # … 上百個
}

# 這個「元工具」是少數一開始就全載給模型的工具
TOOL_SEARCH = {
    "name": "search_tools",
    "description": "依關鍵字或描述搜尋可用工具。當你需要一個目前手上沒有的能力時，"
                   "先用這個找出對應工具，它會把工具的完整用法載入。",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "想做的事，例如「寄 slack 通知」"}},
        "required": ["query"],
    },
}

def search_tools(query: str, k: int = 3) -> list[dict]:
    # 真實系統用 embedding / BM25；這裡用最樸素的關鍵字打分示意
    q = query.lower()
    scored = []
    for name, spec in TOOL_REGISTRY.items():
        score = sum(kw in q for kw in spec["keywords"]) + (1 if name in q else 0)
        if score:
            scored.append((score, name, spec))
    scored.sort(reverse=True, key=lambda x: x[0])
    # 回給模型「可用的工具定義」——這些定義會被 harness 加進下一輪的 tools
    return [{"name": n, "description": s["description"], "input_schema": s["input_schema"]}
            for _, n, s in scored[:k]]
```

harness 的迴圈要做的事：模型呼叫 `search_tools` → 你跑檢索 → 讓命中的工具變成模型接下來能呼叫的工具 → 把「找到這些工具」回給模型。這裡有**兩種路線**，別混在一起：

- **完全自製 harness**（上面的骨架）：你自己管 `tools` 清單，檢索回完整 schema，由你把它們加進這個對話接下來幾輪可用的工具集。
- **Anthropic-compatible 的自訂搜尋**：如果你想用 Anthropic 的機制但自訂檢索邏輯，作法不是回完整 schema，而是讓你的搜尋工具回一個含 `tool_reference` 的標準 `tool_result`；被 reference 的工具仍必須在 top-level `tools` 裡以 `defer_loading: True` 定義好，由 API 負責展開（不要為了 prompt cache 把工具硬塞回 prefix）。

注意兩個設計點：

- **檢索品質決定一切**：樸素關鍵字會漏掉「我想通知團隊」對應到 `slack_send` 的語意關聯。生產系統通常用 embedding 檢索（跟 Ch 15 的 RAG 同一套技術）。檢索召回不好，模型就「找不到它其實有的能力」——這是 tool search 最主要的失敗模式。
- **載入後要留著**：被搜出來的工具，接下來幾輪要保留在 `tools` 裡，別模型剛 search 完、下一輪你又把它抽掉，否則它呼叫時會撲空。何時「卸載」一個用完的工具是進階話題（見下）。

## 五、什麼時候**不要**用

這套機制有真實成本，不是「工具多就無腦開」：

- **工具不多（個位數到十幾個）→ 直接全載**。Tool search 引入一次額外的「搜尋來回」（latency + 一輪 token），如果你的工具牆本來就不大，這個來回比你省下的還貴。**過早優化 tool search 是常見的過度工程。**
- **每個工具幾乎每次都用 → 別 defer**。Defer 的價值來自「長尾工具很少被用到」。如果你 5 個工具每個都高頻使用，defer 只會讓每次任務都多一次搜尋。
- **檢索不可靠的場景要謹慎**：如果你的工具描述寫得爛（Ch 19 沒做好）、或工具彼此語意太接近，檢索會召回錯的或漏掉對的。Tool search 會**放大** Ch 19 的描述品質問題——描述就是被檢索的對象。

一句話判準：**當「全部工具 schema 的 token 成本 × 每輪都付」明顯大於「偶爾多一次搜尋來回的成本」，才值得上 tool search。** 通常那個轉折點在工具數量達到數十個、且多數是長尾的時候（官方的經驗值：10 個以下通常傳統全載就好；工具定義超過約 1 萬 token、或 30-50 個工具之後選用準確率明顯下降，就很值得上）。注意**真正該看的是 token 量，不只是工具「個數」**——10 個 schema 又臭又長的工具，可能比 30 個精簡工具更該延遲。

## 對比與取捨

| 做法 | context 佔用 | 工具選用準確率 | 額外延遲 | 適用 |
|---|---|---|---|---|
| 全載所有工具 | 高（隨工具數線性成長） | 工具少時高、多時下降 | 無 | 工具數少（< ~20） |
| Deferred + tool search | 低（只載核心 + 被搜出的） | 取決於檢索品質 | 多一次搜尋來回 | 工具多（數十～上百），多為長尾 |
| 分組 / 多 agent | 各 agent 只帶自己那組工具 | 高（每組工具少） | 視編排而定 | 能力能清楚分 domain 時（Ch 26-27） |

第三行先記著：**「把工具分給不同 subagent」也是一種「讓每個模型一次只看到少數工具」的手段**，和 tool search 是不同路線、可互補。Ch 26-27 會深入。

## 踩雷集錦

1. **工具還沒載入就直接呼叫**：deferred 工具在 search 之前，模型手上只有名字、沒有 schema，硬呼叫會驗證失敗（你這個 session 的 `InputValidationError` 警語就是在防這個）。harness 要回一個清楚的錯誤：「請先用 search 載入此工具」。
2. **把核心高頻工具也 defer 了**：結果每個任務都先卡一次搜尋，latency 不減反增。核心工具別延遲。
3. **工具描述寫得爛卻指望 search 找得到**：檢索的對象就是描述/關鍵字。Ch 19 的描述品質在這裡被放大成「能不能被發現」。
4. **檢索召回太窄**：只回 top-1、或關鍵字硬比對，導致「明明有這能力卻搜不到」。寧可多回幾個候選讓模型挑，也別漏。
5. **搜出來就立刻卸載**：模型 search 完、下一輪你把工具抽走，它呼叫時撲空。載入的工具要在接下來的輪次保留夠久。
6. **工具沒幾個卻硬上 tool search**：為了「以後可能會多」而提前引入搜尋來回，是過度工程。等真的到數十個再說。
7. **把 dated `type` 字串當定值寫死**：`tool_search_tool_bm25_20251119` 這種帶日期的識別字串會隨版本更新。用前查當前文件，否則某天升級就壞（功能本身已 GA，不需 beta header）。

## 進階：再往深一層

- **何時「卸載」工具**：載入的工具佔著 context，任務換了階段後可以卸掉以騰出空間。但卸載要小心——剛卸掉模型又想用就得重搜。常見策略是「整個 context 壓縮/分段時順手清掉這一階段不再需要的工具」，呼應 Ch 13-14 的 compaction。
- **檢索粒度：工具 vs 工具組**：有時該檢索的不是單一工具，而是「一組相關工具」（例如「Jira 操作」一次帶出 create/update/comment 三個）。設計檢索時想清楚回傳的顆粒度，呼應 Ch 18 的工具切分。
- **與 MCP 的關係（Ch 24 預告）**：tool 暴增最常見的來源就是「接了好幾個 MCP server，每個吐出十幾個工具」。MCP 讓你輕鬆接入大量工具，也因此**讓 tool search 常常變得很值得，在大型 MCP setup（100+、200+ 工具）裡甚至接近必需**。（反過來，若 MCP 工具不多、domain 分得清楚、或有好的 agent routing，就未必需要。）你這個 session 裡那一長串 `mcp__notion__*`、`mcp__plugin_discord_discord__*` 正是這樣來的——它們全被設成 deferred。
- **檢索本身的 prompt injection 面**：如果工具描述來自不可信來源（例如第三方 MCP server 的描述文字），那段描述既被模型讀、又參與檢索——惡意描述可能誘導模型「搜出並使用」某個危險工具。這把 Ch 36 的 prompt injection 與工具發現連起來：**deferred 不等於安全，被搜出來的工具一樣要過權限把關（Ch 25）。**
- **準確率的雙刃**：tool search 在工具極多時提升準確率（牆變薄、模型不被淹沒），但若檢索召回差，反而會降低——因為模型連「它有這能力」都不知道。這是個有最適點的曲線，不是單調變好。

## 動手練習

1. 把你在練習 A/前面章節做的 agent 工具清單列出來，估算「全部 schema 的 token 量」。如果只有 5-6 個工具——恭喜，你現在**不該**上 tool search，寫下為什麼。
2. 人為把工具擴到 30 個（可以是假的 stub），再估一次 token 量與「每輪都付這筆」的成本。感受轉折點在哪。
3. 用第四節的骨架實作 `search_tools`：登錄 10 個工具，用樸素關鍵字檢索。測試查詢「我想通知團隊」能不能搜到 `slack_send`——大概率不能，這就是「該換 embedding 檢索」的訊號。
4. 在你的 harness 迴圈裡，加上「模型呼叫未載入的 deferred 工具時，回一個清楚的『請先 search 載入』錯誤」，而不是讓它崩。
5. 觀察你正在用的這個 Claude Code session：找出 system 訊息裡的 deferred tools 清單，數一數有幾個、其中幾個是 `mcp__` 前綴（MCP 來的）。這就是本章理論的活體標本。

## 本章重點整理

- 工具太多會在三處付代價：**注意力稀釋**（選錯工具）、**快取前綴膨脹**、**每輪的 token/延遲**。「以防萬一全掛上」是要付租金的。
- Tool search 本質是**給工具做的 RAG**（Ch 15）：不全載，依任務檢索出相關工具再注入。
- 機制兩件事：**deferred loading**（多數工具的定義平常不進初始 system prompt、不展開 schema；某些 harness 可能額外提示名字）+ **按需發現**（模型用搜尋工具把需要的工具叫出來）。
- Anthropic 提供官方 **Tool Search Tool**（server-side GA，regex/bm25 兩種變體，搭配 `defer_loading` 與 `tool_reference`）；也能自己用工具登錄表 + 檢索 + 注入實作。**dated type 字串會變，以文件為準；官方檢索不是向量語意搜尋。**
- **不是工具多就無腦開**：工具少、或多數高頻時，多出來的搜尋來回比省下的貴。轉折點通常在數十個長尾工具。
- 檢索品質決定成敗，且會**放大** Ch 19 的描述品質問題；被搜出的工具仍要過權限把關（Ch 25）、仍有 prompt injection 面（Ch 36）。
- 你這個 session 正在用它——MCP 工具暴增是最常見的觸發原因（Ch 24）。

## 自我檢核

- [ ] 我能說出「工具太多」具體在哪三個地方付代價，而不是只說「context 變長」
- [ ] 我能用「給工具做的 RAG」這個類比向別人解釋 tool search，並指出它和文件 RAG 的差異
- [ ] 我能說清楚 deferred loading 與按需發現各自做什麼、為什麼核心工具不該 defer
- [ ] 給定一個 agent 的工具清單，我能判斷它「該不該」上 tool search，並講出判準
- [ ] 我知道 tool search 怎麼放大 Ch 19 的描述品質問題、又和 Ch 25 權限、Ch 36 injection 連在一起
- [ ] 我能在自己用的 Claude Code session 裡指出 deferred tools 清單，並解釋它為什麼存在

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool Search Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)** 與 **[Tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference)**
  - **讀哪裡**：Tool Search Tool 的 `defer_loading`、regex/bm25 兩種變體；Tool reference 怎麼把搜出的工具內聯展開、何時適用。
  - **能學到什麼**：本章機制的官方權威形狀——確切的 dated type 字串、欄位名、good/bad use cases 的官方建議以這裡為準（會隨版本變）。
  - **前提知識**：懂 Ch 18 的工具定義與 tool_use/tool_result 循環。

- **[Model Context Protocol（MCP）](https://modelcontextprotocol.io/)**
  - **讀哪裡**：MCP 怎麼讓 client 接入多個 server、每個 server 暴露多個工具。
  - **能學到什麼**：理解「工具為什麼會暴增到需要 tool search」——MCP 是最常見的源頭，直接銜接 Ch 24。
  - **前提知識**：無，但讀完本章再讀更有感。

### 部落格 / 技術文章

- **[Anthropic — Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)** — Anthropic
  - **這篇說什麼**：怎麼為 agent 設計工具，包含「工具太多會稀釋選用準確率」「描述品質影響可發現性」的論述。
  - **讀哪裡**：談工具數量、命名與描述對選用影響的段落。
  - **為什麼值得讀**：本章第一節「三處代價」與第五節「描述被放大」的權威背景。

下一章把本章一直在點名的東西講透：**MCP**——讓 agent 用一套標準協定接入大量外部工具與資料源（也正是你 session 裡那串 `mcp__…` 工具的來源），以及它帶來的工具暴增為什麼讓本章的 tool search 幾乎變成必需。

→ [Ch 24 MCP 整合](./24-mcp-integration.md)
