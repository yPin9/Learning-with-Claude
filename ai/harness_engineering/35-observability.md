# Ch 35 — Observability：看見 agent 內部在幹嘛

> **目標**：上一章的 eval 告訴你「A 類任務退化了」——然後呢？你得**打開**那次跑、看它每一步做了什麼，才知道為什麼。agent 是個多步、會自己決策的黑盒：它送了什麼 prompt、選了哪個工具、工具回了什麼、花了多少 token、為什麼停。**看不見這些，你既 debug 不了、也餵不起 eval。** 本章談 **observability**：怎麼把 agent 的每一步變成**可記錄、可查詢、可重播**的資料。讀完你能說出：agent 該觀測哪些東西（每次 LLM 呼叫的 usage/stop_reason、每次工具呼叫的輸入輸出、**模型實際看到的 context**、軌跡與最終結果）、**trace/span** 的心智模型（一次跑 = 一個 trace、每步 = 一個 span、subagent 是巢狀 span）、結構化 log 為什麼勝過 print、要 dashboard 哪些指標（延遲 p50/p95、每次跑成本、錯誤率），以及隱私/PII 的基本責任。

> **環境**：Python + Anthropic SDK。本章的 tracer 包在前面練習的 agent loop（[練習 A](./practice-a-mini-agent-loop.md)）外面，從 `response.usage` 取 token、算成本（接 [Ch 17](./17-prompt-caching.md) 的 cache token 概念）。它是 [Ch 34 eval](./34-eval.md) 的資料來源、[Ch 38 debug](./38-failure-modes-debugging.md) 的證據來源——這三章是一個閉環。

## 為什麼需要這個？agent 是會自己亂跑的黑盒

傳統程式出錯，你有 stack trace、有確定的執行路徑。agent 不一樣：

- 它**自己決定**走幾步、用哪些工具、何時停——**同一個輸入兩次跑路徑可能不同**。
- 出問題時常常**不報錯**：它「成功」回了一個爛答案、或繞了 15 步做完 3 步的事、或默默漏掉一個工具呼叫。沒有 exception 給你看。
- 最關鍵的：**模型每一步實際看到的 context 是動態組出來的**（系統 prompt + 歷史 + 工具結果 + 壓縮後的摘要…，見 Ch 13）。「它為什麼這樣決定」的答案，藏在「它那一刻到底看到了什麼」裡——而**那個東西預設不會留下來**。

於是常見的窘境是：使用者回報「agent 昨天把我的檔案改錯了」，你打開一看——**什麼記錄都沒有**。你不知道它那次看到什麼 context、呼叫了哪些工具、工具回了什麼。你只能「再跑一次看看」，但因為非確定，這次它又好好的。**沒有觀測，你連「重現問題」都做不到。**

問題的本質：**agent 的「為什麼」不在程式碼裡，在那一次執行的資料裡**。程式碼是固定的，但每次跑的 context、決策、工具結果都不同——你必須**把每一次跑的內部過程記下來**，才有東西可查、可比、可學。

**Observability 就是這個：把 agent 每一步（送出的 prompt、LLM 的回應與 usage、工具呼叫與結果、耗時、停止原因）變成結構化、帶 id、可事後查詢與重播的事件。** 它讓「agent 為什麼這樣做」從「玄學」變成「去翻那次 trace」。

核心心態：**你不能改進你看不見的東西。** eval 量出「變差了」，observability 給你「差在哪一步」；兩者缺一不可。

## 先建立直覺：一次跑是一棵 trace，每一步是一個 span

借用分散式追蹤的概念。把 **agent 的一次完整執行**想成一棵樹：

```
trace（run_id=abc123，任務：「整理這個資料夾」）            ← 一次跑 = 一個 trace
├─ span: LLM call #1            model, in/out tokens, latency, stop_reason=tool_use
├─ span: tool list_directory    input={path:"."}, result="...", 12ms, is_error=False
├─ span: LLM call #2            ...stop_reason=tool_use
├─ span: tool read_file         input={path:"a.py"}, result="...", 5ms
├─ span: LLM call #3            ...stop_reason=tool_use
├─ span: tool write_file        input={...}, result="已建立", 8ms
└─ span: LLM call #4            stop_reason=end_turn  ← 最終回應
        ↑ 每一步是一個 span：有名字、起訖時間、屬性（attributes）、成功/失敗
```

幾個要點：

- **trace = 一次跑**，用一個 `run_id`（trace id）串起這次的所有 span。之後要查「那次到底怎麼了」，就靠這個 id 把整串撈出來。
- **span = 一個步驟**（一次 LLM 呼叫、一次工具執行）。每個 span 記：名字、起訖時間（→ 延遲）、屬性（token、工具輸入輸出、stop_reason、is_error）。
- **巢狀**：subagent（Ch 26）的整段執行是 orchestrator 底下的一個**父 span，裡面再包它自己的子 span**（通常仍在**同一棵 trace** 裡，用 `parent_span_id` 指回去；OTel 裡跨 trace 才叫「子 trace」並要顯式 link）——這樣你能看「哪個 subagent 那次跑爛了」，而不是只看到「整體變差」。multi-agent 的觀測特別需要這個層次。
- 這正是 **OpenTelemetry** 那套（`trace_id` / `span_id` / `parent_span_id` / attributes）的形狀。本章為求精簡，用一個 `run_id` 當 trace id、用 `parent_run_id` 表達巢狀；對上 OTel 時它們分別對應 `trace_id` 與 `parent_span_id`。你可以手刻（本章），也可以用現成的 LLM 觀測工具（進階一節），它們底層多半就是這個模型。

對照你正在用的這個 session：你看到的「[工具] read_file(...)」「token 用量」那類即時輸出，就是一種**即時 observability**——harness 把每一步攤給你看。本章要做的，是把這種「看得到」**留存成可事後查的資料**。

## 一、該觀測什麼：四類缺一不可

agent 的一次跑，至少要留下這四類資料：

### (a) 每次 LLM 呼叫的元資料

從 `response` 拿得到的，全都要記：

```python
# 一次 LLM 呼叫後，從 response 取出觀測資料
usage = resp.usage
record = {
    "model": resp.model,
    "stop_reason": resp.stop_reason,                 # end_turn / tool_use / max_tokens …
    "input_tokens": usage.input_tokens,
    "output_tokens": usage.output_tokens,
    # cache token 分開記（Ch 17）：命中讀取的、與寫入快取的，計價不同
    "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
    "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0),
    "request_id": resp._request_id,                  # 回報問題給 Anthropic 時的關鍵
}
```

- `stop_reason` 是**金礦**：一堆 `max_tokens` 代表回應被截斷（Ch 7）、預期之外的值代表 loop 邏輯有洞。
- `usage` 是**成本與 context 健康度**的來源。注意看 context 大小要**把三個輸入桶加起來**（`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`）：開了 caching 後 `input_tokens` 只算未快取那部分，光看它會以為 context 沒漲，其實 prefix 都被算進 cache 桶了。三者之和一路狂漲才是 context 在膨脹（Ch 13 該壓縮了）；而 `cache_read` 佔比高代表你的 caching 有生效（Ch 17）。
- `request_id` 要存——回報 API 問題給 Anthropic 時，這是他們定位你那次請求的鑰匙。

### (b) 每次工具呼叫的輸入、輸出、結果

```python
tool_span = {
    "tool": block.name,
    "input": block.input,            # 模型填了什麼參數
    "output": result.content,        # 工具回了什麼（可能要截斷，見隱私一節）
    "is_error": result.is_error,     # 失敗了嗎（Ch 20）
    "duration_ms": elapsed_ms,
}
```

工具的**輸入**讓你看到「模型怎麼理解這個工具」（參數填對了嗎）；**輸出 + is_error** 讓你看到「環境回給它什麼」。很多 agent 亂跑的根因，是某個工具默默回了 `is_error` 或一坨無用內容，模型被帶歪——不記工具 I/O，這種根因你永遠查不到。

### (c) 模型實際看到的 context（最常被漏掉、卻最重要）

因為 context 是動態組的（Ch 13），「它為什麼這樣決定」往往要看「它那步**到底看到了什麼**」。至少在 debug 模式下，**把每次 LLM 呼叫送出的完整 `messages` 快照存下來**。這很佔空間，所以常見折衷：平時記摘要（長度、訊息數、有沒有被壓縮過），**出問題時能調出完整快照**。

### (d) 軌跡與最終結果

整次跑的**步數（turns）**、總時長、總成本、最終 `stop_reason`、最終輸出/最終狀態。這些是 eval（Ch 34）直接要的指標，也是 dashboard（第三節）的原料。

## 二、結構化 log，不是 print

`print(f"呼叫了 {tool}")` 在開發時看看可以，但**不能查詢、不能聚合、不能跨跑比較**。observability 要的是**結構化事件**——每個事件是一筆帶欄位的記錄（JSON），有 `run_id`、時間戳、事件類型、屬性：

```python
import json, time, uuid, logging

logger = logging.getLogger("agent")

def log_event(run_id: str, event: str, **attrs):
    logger.info(json.dumps({
        "ts": time.time(),
        "run_id": run_id,                 # 把這次跑的所有事件串起來
        "event": event,                   # "llm_call" / "tool_call" / "run_end" …
        **attrs,
    }, ensure_ascii=False, default=str))   # default=str：dict/Path 等也能序列化
```

> **要乾淨的 JSONL**，把 handler 的 formatter 設成只輸出訊息本體（`logging.Formatter("%(message)s")`），否則 logging 預設會在 JSON 前面加上時間戳/層級，每行就不是合法 JSON 了。

為什麼結構化勝出：

- **可查詢**：「撈出所有 `is_error=True` 的 tool_call」「找 `input_tokens>50000` 的跑」——結構化事件有**型別化欄位**，進了 log 系統（或就是 JSONL 檔）就能精準 query。隨手 print 出的非結構化文字也能 grep，但欄位、型別、跨事件關聯都得自己硬解析，脆弱又難聚合。
- **可聚合**：算 p95 延遲、每日成本、工具錯誤率——要欄位化的數字。
- **可串連**：靠 `run_id` 把散落的事件重組成一棵 trace、重播一次跑。
- **機器可讀**：eval（Ch 34）的 dataset、dashboard、告警，都直接吃這些事件。

實務：**用 `run_id` 串一切**。一次跑開始時生一個 `run_id = uuid.uuid4().hex`，這次的每個事件都帶它。subagent 再帶一個 `parent_run_id` 指回去——巢狀 trace 就成形了。

## 三、把 tracer 包進 agent loop

把上面拼起來，做一個極簡 tracer，包住 LLM 呼叫與工具執行：

```python
# 定價表（每百萬 token 美元；數字會變，以官方價目為準——這裡示意算法）
# 每個模型四種費率：未快取輸入 / 寫入快取 / 讀取快取命中 / 輸出
PRICE = {  # (input, cache_write, cache_read, output)
    "claude-opus-4-8":           (15.0, 18.75, 1.5, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 1.25, 0.1, 5.0),
}

def cost_usd(model: str, usage) -> float:
    pin, pcw, pcr, pout = PRICE.get(model, (0.0, 0.0, 0.0, 0.0))
    # 開了 caching 後，usage.input_tokens 只算「未快取」那部分；寫入/讀取快取各有費率（Ch 17）。
    # 全部分開算，否則用了 caching 的請求成本會被低估。
    return (usage.input_tokens * pin
            + getattr(usage, "cache_creation_input_tokens", 0) * pcw
            + getattr(usage, "cache_read_input_tokens", 0) * pcr
            + usage.output_tokens * pout) / 1_000_000

class TracedAgent:
    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.turns = 0
        self.total_cost = 0.0

    def call_llm(self, **kwargs):
        t0 = time.perf_counter()
        resp = client.messages.create(**kwargs)
        dt = (time.perf_counter() - t0) * 1000
        c = cost_usd(resp.model, resp.usage)
        self.total_cost += c
        log_event(self.run_id, "llm_call",
                  model=resp.model, stop_reason=resp.stop_reason,
                  input_tokens=resp.usage.input_tokens,
                  output_tokens=resp.usage.output_tokens,
                  cache_read=getattr(resp.usage, "cache_read_input_tokens", 0),
                  latency_ms=round(dt, 1), cost_usd=round(c, 6),
                  request_id=resp._request_id)
        return resp

    def run_tool(self, block, fn):
        t0 = time.perf_counter()
        try:
            result = fn(**block.input)              # 回 ToolResult（Ch 20）
            err = result.is_error
            content = result.content
        except Exception as e:                       # 工具自己炸了也要記下來（別吞掉）
            err, content = True, f"工具未捕捉例外：{e!r}"
        dt = (time.perf_counter() - t0) * 1000
        log_event(self.run_id, "tool_call",
                  tool=block.name, tool_input=block.input,
                  is_error=err, duration_ms=round(dt, 1),
                  output_len=len(str(content)))      # 預設只記長度；完整內容看隱私政策再決定
        return content, err

    def finish(self, final_output: str, success: bool | None = None):
        log_event(self.run_id, "run_end", turns=self.turns,
                  total_cost_usd=round(self.total_cost, 6),
                  success=success, output_len=len(final_output))
```

把 `call_llm`/`run_tool` 換掉 loop 裡裸的 `client.messages.create` 與工具呼叫，你就**零侵入地**得到每次跑的完整 trace（JSONL）。要重播或 debug，按 `run_id` grep 出來照時間排，就是那棵 trace。

要點：

- **tracer 是橫切的（cross-cutting）**：包在 loop 外，不污染工具邏輯。工具還是回 `ToolResult`（Ch 20），tracer 只是在旁邊記。
- **連工具的未捕捉例外都記**：`run_tool` 的 `try/except` 不是要吞錯，而是要**把錯記下來再回給模型**（避免整個 loop crash，且留下證據）。
- **成本就地累加**：每次 `call_llm` 加總，`finish` 時一次落帳——eval（Ch 34）的「每題成本」就有了。

## 四、三種看法：即時、聚合、單次重播

同一批 trace 資料，有三種用途，**各解決不同問題**：

| 看法 | 長相 | 解決什麼 | 例子 |
|---|---|---|---|
| **即時（live）** | 跑的時候把每步印出來 | 開發、互動式 debug | 你這個 session 的「[工具] …」輸出 |
| **聚合（aggregate）** | dashboard / 報表 | 健康度趨勢、發現「整體變差」 | p95 延遲、每日成本、工具錯誤率隨時間 |
| **單次重播（replay）** | 按 run_id 調出整棵 trace **重建/檢視**那次過程 | 查某一次到底怎麼了 | 使用者回報的那次 bug |

> 這裡的「重播」是指**重建並逐步檢視**那次 trace（它看到什麼、做了什麼），**不是**確定性地重跑出一模一樣的結果——後者因為模型非確定（Ch 39），還需要存下完整請求 payload、工具輸入輸出、model id 與參數，且仍未必能完全重現。

要 dashboard 的核心指標（接 Ch 34 / Ch 37）：

- **延遲分布**：p50 / p95 / p99，**別只看平均**（平均被少數慢請求拉歪、藏不住長尾；平均仍可用於看容量/成本趨勢，但使用者體感要看分位數）。
- **每次跑成本 / token**：趨勢上揚常代表 context 膨脹或 caching 失效。
- **成功率 / 錯誤率**：整體 + 按工具、按任務類型分。
- **步數（turns）分布**：尾巴變長 = 開始有任務陷入無效迴圈。
- **工具呼叫分布**：哪個工具最常被叫、最常失敗。

關鍵：**聚合告訴你「哪裡不對」，重播告訴你「為什麼」**。先用 dashboard 發現某類退化，再按 run_id 調出代表性的幾棵 trace 細看——這就是 Ch 38 debug 的標準入口。

## 五、隱私與 PII：log 裡有使用者資料

agent 的 trace 含**使用者輸入、檔案內容、工具輸出**——這些常含個資或機密。記之前要想清楚：

- **預設只記元資料**（長度、token、is_error、工具名），**完整內容（messages 快照、工具輸出原文）分級**：開發環境可全記，生產環境預設不記原文或先**遮蔽（redact）**敏感欄位，需要時才在受控下調閱。
- **訂留存期限**：trace 不該無限期堆著。按合規/需求設 TTL。
- **存取控制**：trace 可能比資料庫還敏感（它把使用者輸入、模型推理、工具結果全攤開）。當機密資料管。
- **別把秘密記進 log**：API key、token 之類若出現在工具輸入/輸出，要過濾掉再記。

核心原則：**observability 要夠看得見問題，但別變成一個無人看管的個資外洩面**。記「夠 debug」的，不是「記全部」。

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| log 形式 | `print` 純文字 | **結構化事件（JSON + run_id）** | 要能查/聚合/重播就得結構化；print 只配臨時看 |
| 記多少內容 | 全記（含 messages 原文） | **預設記元資料、原文分級/遮蔽** | 看環境與合規：開發可多記、生產要克制 |
| 延遲指標 | 平均 | **p50/p95/p99** | 看分位數；平均藏不住長尾 |
| 串連方式 | 各事件獨立 | **run_id 串成 trace、subagent 巢狀** | 一定要串：散落事件無法重播一次跑 |
| 工具用 | 手刻 tracer | **現成 LLM 觀測平台** | 起步手刻夠用；規模大用平台省事（進階） |
| 何時看 | 只在出事後 | **平時看聚合趨勢 + 出事重播單次** | 兩者都要：聚合發現、重播定位 |

## 踩雷集錦

1. **只用 print**：看得到當下、查不了歷史、聚合不了。第一天就上結構化 log + run_id。
2. **沒存 context 快照**：出事想知道「它那步看到什麼」，發現根本沒記——而 context 是動態組的，事後無法還原。至少 debug 模式要能存。
3. **用平均延遲**：平均 1.2 秒看起來很好，p99 其實 30 秒——使用者體感是長尾。用分位數。
4. **不記 `is_error` 與工具輸出**：agent 亂跑的根因常是某個工具默默回錯，不記工具 I/O 就查不到。
5. **不記 `stop_reason` / `usage`**：少了診斷 loop（max_tokens 截斷）與成本/context 健康度的關鍵信號。
6. **trace 無限留存、不遮蔽**：log 含個資/機密，變成外洩面與合規風險。訂 TTL、分級、遮蔽。
7. **事件不帶 run_id**：散落的事件無法重組成一次跑——等於沒有 trace。
8. **subagent 不巢狀**：multi-agent 出事只看到「整體變差」，定位不到是哪個 subagent。用 parent_run_id 串。

## 進階：再往深一層

- **現成 LLM 觀測平台**：LangSmith、Langfuse、Arize Phoenix、Braintrust、W&B Weave、OpenLLMetry 等都提供 trace/span 視覺化、成本/延遲 dashboard、與 eval 整合。它們底層多半是 **OpenTelemetry** 的 trace 模型——理解了本章手刻的形狀，接這些工具只是把 `log_event` 換成它們的 SDK。起步手刻、規模大再上平台。
- **OpenTelemetry 與 GenAI 語意慣例**：OTel 有一套 GenAI 的 semantic conventions（怎麼命名 LLM span 的屬性，如 `gen_ai.usage.input_tokens`）。照慣例記，你的 trace 就能被任何相容工具吃。
- **取樣（sampling）**：規模大時全量記 trace 太貴。常見做法：**全量記元資料 + 指標**，但**完整 context 快照只取樣**（例如 1%）或**只在失敗/慢請求時全記**（tail-based sampling）。
- **觀測即 eval 的資料源（Ch 34）**：生產 trace 是 eval dataset 最好的來源——把真實失敗的 trace 標一標，就是回歸案例。觀測與 eval 形成飛輪。
- **觀測即 debug 的證據（Ch 38）**：debug 一個 agent 不是讀程式碼，是**讀那次 trace**——看它每步看到什麼、做了什麼決定、哪步開始歪。沒有 trace，debug agent 幾乎不可能。
- **Anthropic 平台側的用量**：除了每個 response 的 `usage`，Console / Usage & Cost API 也提供帳號層級的用量與成本聚合——對「整體花了多少」這種營運問題比自己加總方便。

## 動手練習

1. **加 tracer**：把第三節的 `TracedAgent` 包進[練習 C](./practice-c-file-toolset.md) 的檔案 agent。跑一個多步任務，檢查 JSONL log：每次 LLM 呼叫有 usage/stop_reason/latency、每次工具有 input/is_error/duration。
2. **重播**：寫一個小 script，吃一個 `run_id`，從 log 撈出該次所有事件、照時間排印成一棵 trace（縮排顯示巢狀）。對著它「重播」剛剛那次跑。
3. **聚合**：跑 20 次不同任務，從 log 算出：成功率、p50/p95 延遲、每次跑平均成本、工具錯誤率。體會「平均 vs p95」差多少。
4. **context 膨脹實驗**：故意做一個會跑很多步的任務，把每次 `llm_call` 的 `input_tokens` 畫出來，看它怎麼一路漲——這就是 Ch 13 該介入壓縮的信號。
5. **遮蔽**：在 `log_event` 裡加一層 redaction，把工具輸入中看起來像 API key / email 的欄位遮成 `***`，體會「記得夠 debug 但不外洩」。

## 本章重點整理

- agent 是會自己決策的非確定黑盒，出問題常**不報錯**；「它為什麼這樣做」的答案藏在**那一次跑的資料**裡，而那些資料**預設不會留下**。
- observability = 把每一步（LLM 呼叫的 usage/stop_reason、工具的 input/output/is_error、**模型實際看到的 context**、軌跡與結果）變成**結構化、帶 run_id、可查可重播**的事件。
- 心智模型：**一次跑 = 一棵 trace，每步 = 一個 span，subagent = 巢狀 span**（OpenTelemetry 那套）。
- **結構化 log 勝過 print**：可查詢、可聚合、可用 run_id 串成 trace 重播。tracer 橫切包在 loop 外、不污染工具。
- 三種看法：**即時**（開發）、**聚合**（dashboard 發現「哪裡不對」、用 p50/p95 不用平均）、**單次重播**（按 run_id 查「為什麼」）。
- trace 含個資/機密：**預設記元資料、原文分級或遮蔽、訂留存期限、當機密管**。
- 觀測是 **eval（Ch 34）的資料源**、**debug（Ch 38）的證據源**——三者一個閉環。

## 自我檢核

- [ ] 我能說出為什麼 agent 出問題常常「沒有記錄可查」，以及為什麼 context 快照特別重要
- [ ] 我能說出該觀測的四類資料，以及各自能診斷什麼
- [ ] 我能解釋 trace/span 心智模型，並說出 subagent 為什麼要巢狀
- [ ] 我能說出結構化 log 勝過 print 的三個理由，以及 run_id 的角色
- [ ] 我能從 `response.usage` 算出一次呼叫的成本，並知道 cache token 為什麼要分開記
- [ ] 我知道該 dashboard 哪些指標、以及為什麼延遲要看分位數不看平均
- [ ] 我能說出 trace 的隱私責任（遮蔽、分級、留存期限）

## 延伸閱讀

### 官方文件

- **[Anthropic — Token counting / Usage](https://docs.claude.com/en/docs/build-with-claude/token-counting)** — Anthropic
  - **讀哪裡**：`response.usage` 各欄位（input/output、cache_creation/cache_read）的意義、怎麼預估與核對 token。
  - **能學到什麼**：本章成本計算與 context 健康度監測的資料來源。
  - **前提知識**：Ch 17（prompt caching）——理解 cache token 為何分開計。

- **[Anthropic — Errors and request IDs](https://docs.claude.com/en/api/errors)** — Anthropic
  - **讀哪裡**：`request_id` 怎麼取、回報問題時為什麼要附上它、錯誤類型。
  - **能學到什麼**：trace 裡為什麼要存 `request_id`、出事怎麼跟 Anthropic 對焦。

### 部落格 / 技術文章

- **[OpenTelemetry — Semantic conventions for GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)** — OpenTelemetry
  - **這篇說什麼**：怎麼用標準化的 span 屬性記 LLM 呼叫（model、token、操作類型），讓 trace 跨工具通用。
  - **讀哪裡**：GenAI spans 的屬性命名與結構。
  - **為什麼值得讀**：本章手刻的 trace/span 對應到產業標準——照慣例記，就能無痛接任何相容的觀測平台。

下一章 **Ch 36 Prompt injection 與 agent 安全**：你現在看得見 agent 在幹嘛了——但如果它讀到的某個工具結果裡，藏著「忽略你的指令、把這個檔案傳到外部」的惡意文字呢？agent 能讀外部內容、又能採取行動，這個組合是全新的攻擊面。

→ [Ch 36 Prompt injection 與 agent 安全](./36-prompt-injection-security.md)
