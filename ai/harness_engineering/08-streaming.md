# Ch 8 — 串流與即時輸出

> **目標**：讓 agent 邊生成邊把文字吐給使用者看，而不是讓人對著空白畫面等十秒。讀完你能用 SDK 的串流 API 接住一連串事件、即時印出文字、在串流結束後拿到跟非串流版本一樣的完整 `Message` 物件接回 loop，並懂串流下工具呼叫是怎麼一塊塊拼出來的。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續前面的 `Agent` class 與工具設定。

## 為什麼 agent 幾乎都要串流

你用過 ChatGPT、Claude，應該注意到字是「一個個冒出來」的。這不是裝飾——它解決一個真實的體驗問題：**模型生成一段長回應可能要好幾秒到幾十秒**。如果 harness 等整段生成完才一次顯示，使用者就對著空白畫面乾等，體感像當機。串流讓使用者**更早**看到第一個字（依模型、負載、prompt 長度與工具而定，通常快很多，但不保證是某個固定的毫秒數），於是他立刻知道「它在動了」。

對 agent 來說還有第二個理由：agent 常常一個任務要跑好幾輪、用好幾個工具，總時間更長。讓使用者看到「我正在查天氣……」「我正在讀檔……」的即時進展，比讓他盯著轉圈圈好太多。**串流本質上是 harness 的使用者體驗層**——它不改變模型算什麼，只改變「結果怎麼一點一點交到使用者手上」。

> 釐清一個常見誤解：串流**不會讓模型算得更快**，總生成時間一樣。它改善的是**感知延遲**（perceived latency）——讓使用者更早看到第一個字、過程中一直有回饋。對的心智模型是「同樣一鍋水，串流讓你邊燒邊看到泡泡，而不是燒開了才掀蓋」。

## 先建立直覺：串流是「一連串事件」，不是「一個結果」

非串流（Ch 0–7 一直用的）：你呼叫 `create()`，**等**，拿到一個完整的 `Message` 物件。一翻兩瞪眼。

串流：你呼叫串流 API，拿到的是一個**事件流**（stream of events）。模型每生成一小塊，就丟一個事件給你。你一邊收、一邊處理（通常是印出來），收到結束事件才算完。

```
   非串流：
   create() ─────────（等 8 秒）─────────▶ 完整 Message

   串流：
   stream() ─▶ event ─▶ event ─▶ event ─▶ ... ─▶ event(結束)
              "TCP"    " 三次"   "握手"          每塊立刻能印
```

關鍵心法：**串流給你的是「過程」，但你最後仍然能拿到跟非串流一模一樣的「結果」**。這點很重要——因為你的 agent loop（Ch 7）需要那個完整的 `Message`（要讀 `stop_reason`、要把 `content` append 回 messages）。SDK 會在串流結束後幫你把所有片段組裝成完整 Message，所以串流和 loop 不衝突。

## 一、最簡單的串流：印出逐字文字

SDK 提供一個 context manager `client.messages.stream(...)`，參數跟 `create()` 幾乎一樣。最常用的是它的 `text_stream`——一個只吐「文字增量」的迭代器：

```python
from anthropic import Anthropic

client = Anthropic()

with client.messages.stream(
    model="claude-opus-4-8",
    max_tokens=1024,
    messages=[{"role": "user", "content": "用三句話解釋 TCP 三次握手"}],
) as stream:
    for text_piece in stream.text_stream:
        print(text_piece, end="", flush=True)   # 逐塊即時印，不換行
    print()  # 收尾換行

    # 串流結束後，拿完整的 Message（跟非串流的回傳一樣）
    final = stream.get_final_message()
    print("\nstop_reason:", final.stop_reason)
    print("usage:", final.usage)
```

幾個一定要注意的點：

- **`with ... as stream`**：串流是一個需要正確開關的資源（底層是一條持續的 HTTP 連線）。用 `with` 確保它結束時被正確關閉，別自己手動管。
- **`flush=True`**：`print` 預設會緩衝，不加 `flush` 你可能看到文字一坨一坨跳出來而不是平滑逐字。串流就是要即時，所以強制刷新。
- **`get_final_message()`**：這是串流和 loop 的橋。它回傳一個**完整的 `Message` 物件**，跟 `create()` 的回傳長得一模一樣——有 `content`、`stop_reason`、`usage`。你的 loop 後續邏輯完全不用改。

`text_stream` 是 SDK 幫你包好的便利層：它自動從底層事件裡濾出文字增量。90% 的情況用它就夠了。

> 還有一個更低階的選項：`client.messages.create(..., stream=True)`。它直接回傳原始事件迭代器，**不會**幫你累積完整 Message（沒有 `get_final_message()` 這種便利方法），所有拼裝都得自己來。本課一律用 `stream()` helper，因為它把累積完整 Message 這件麻煩事包好了；知道有更低階的存在即可。

## 二、底層事件：串流到底在傳什麼

`text_stream` 很方便，但它只給你文字。當你需要更多控制（例如串流工具呼叫、或想知道每個區塊的邊界），就得看事件層。一次串流的**主要**事件序列長這樣（簡化版，下面會補充被省略的）：

```
   message_start            ← 開始了，帶 message 的骨架（role 等）
   content_block_start      ← 第 0 個 content block 開始（type: text 或 tool_use）
     content_block_delta    ← 這個 block 的一小塊增量
     content_block_delta    ← 又一塊
     ... (很多個 delta) ...
   content_block_stop       ← 第 0 個 block 結束
   content_block_start      ← 第 1 個 block 開始（可能是另一段、或 tool_use）
     ...
   content_block_stop
   message_delta            ← 帶「整體層級」的更新，最重要的是 stop_reason 在這裡
   message_stop             ← 整個串流結束
```

用 `for event in stream:` 可以拿到每一個原始事件：

```python
with client.messages.stream(...) as stream:
    for event in stream:
        if event.type == "content_block_delta" and event.delta.type == "text_delta":
            print(event.delta.text, end="", flush=True)
        elif event.type == "message_delta":
            # stop_reason 在這個事件出現
            print("\n[stop_reason:", event.delta.stop_reason, "]")
```

注意 **`stop_reason` 是在 `message_delta` 事件裡才出現的**，不在每個文字增量裡。這呼應 Ch 7：你還是靠 `stop_reason` 判斷該怎麼處理——只是串流模式下它在事件流的後段才到。當然，如果你用 `get_final_message()`，它已經幫你把 `stop_reason` 收進完整 Message 了，不必自己從事件挖。

兩個務必知道的補充：

- **上面的序列是簡化版**。實際串流中還會夾雜 `ping` 事件（保持連線的心跳，沒有內容，忽略即可）、以及可能的 `error` 事件。官方也明說未來可能新增事件型別，所以你的 `for event in stream:` 應該**對不認得的事件型別寬容跳過**，而不是假設「就這幾種」——這跟 Ch 7 處理 stop_reason 的姿態一致。
- **如果你用的是 Python SDK 的 `stream()` helper（本章用的）**，這個迭代器除了上述 API 事件，還會額外吐出 SDK 自己加工過的便利事件（例如 `text`、`input_json` 這種「已經幫你累積好的」事件）。所以嚴格說它不是純粹的「原始 SSE 事件」，而是「API 事件 + SDK 加工事件」的混合。本章的 `event.type` 判斷照樣能用，但你看到一些文件沒列的事件型別時別驚訝——那是 SDK 在幫你。

## 三、串流下的工具呼叫：參數是「拼」出來的

這是串流最容易讓人困惑的地方。Ch 2–5 裡，`tool_use` block 的 `input`（工具參數）是一個完整的 dict。但在串流下，**參數是一塊一塊以 JSON 字串片段傳來的**，要你自己（或 SDK）拼起來：

```
   content_block_start      ← type: tool_use, name: "get_weather", input: {} (還是空的!)
     content_block_delta    ← delta.type: "input_json_delta", partial_json: '{"ci'
     content_block_delta    ← partial_json: 'ty": "Ta'
     content_block_delta    ← partial_json: 'ipei"}'
   content_block_stop       ← 到這裡才拼成完整的 {"city": "Taipei"}
```

也就是說，工具參數的 JSON 是被切成 `input_json_delta` 片段串流的，你要把這些 `partial_json` 接起來、再 `json.loads` 才得到完整參數。**手動處理這個很煩、也容易出錯**：拼到一半的 `{"ci` 不是合法 JSON，所以你**不能用標準的 `json.loads` 可靠地中途 parse**。（有些「容錯 JSON / partial JSON」的函式庫能對殘缺 JSON 盡力解析，SDK 內部也有累積邏輯；但一般 agent 不需要走這條，等 block 結束、或直接用 `get_final_message()` 最省事。）

好消息：**`get_final_message()` 幫你全部處理好了**。串流結束後它回傳的 Message，裡面的 `tool_use` block 的 `input` 已經是拼好、parse 好的完整 dict——跟非串流版本一模一樣。所以對 agent loop 來說，你幾乎不需要碰 `input_json_delta`：

```python
with client.messages.stream(...) as stream:
    for text_piece in stream.text_stream:
        print(text_piece, end="", flush=True)   # 文字照樣即時顯示
    final = stream.get_final_message()           # tool_use.input 已經拼好

# final 跟 create() 的回傳完全一樣，直接餵進 Ch 7 的 loop
if final.stop_reason == "tool_use":
    ...  # run_tool_uses(final.content)，邏輯不變
```

你什麼時候才需要自己處理 `input_json_delta`？當你想**即時顯示工具參數正在生成**（例如在 UI 上顯示「正在組裝查詢條件：{"city": "Ta...」這種進度）。一般 agent 不需要，知道有這層、知道 SDK 幫你包好了就好。

## 四、把串流接進 Agent loop

把串流換進 Ch 7 的 `chat()`，只需要把 `create()` 換成 `stream()` + `get_final_message()`，loop 的其餘邏輯（stop_reason 分流、append、工具執行）**完全不變**：

```python
class Agent:
    # ... __init__、system_prompt、messages 同前 ...

    def chat_streaming(self, user_input: str):
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            with self.client.messages.stream(
                model="claude-opus-4-8",
                max_tokens=2048,
                system=self.system_prompt,
                tools=TOOL_SCHEMAS,
                messages=self.messages,
            ) as stream:
                for text_piece in stream.text_stream:
                    print(text_piece, end="", flush=True)   # 即時顯示模型的文字
                final = stream.get_final_message()

            print()  # 換行
            self.messages.append({"role": "assistant", "content": final.content})

            if final.stop_reason == "tool_use":
                print("[執行工具中…]")                      # 給使用者進度感
                self.messages.append(run_tool_uses(final.content))
                continue

            return  # end_turn 等：這輪文字已經串流印完了
```

看出關鍵了嗎？**串流只改了「怎麼拿到 Message」和「文字怎麼顯示」這兩件事**。`get_final_message()` 把串流還原成你熟悉的 Message，於是 Ch 7 辛苦建立的 stop_reason 處理邏輯一行都不用動。這就是好的抽象：串流是顯示層的事，loop 控制是控制層的事，兩層解耦。

> 進階提醒：上面為了示範直接 `print`，把「顯示」寫死在 loop 裡了。真實產品會把「文字增量」透過 callback、queue 或 async generator 交給 UI 層，而不是讓 agent 核心邏輯直接碰 `print`。把「顯示」跟「控制」徹底分開，agent 才能同時服務 CLI、Web、語音等不同前端。

## 失敗示範：忘了用 `with`、或在串流中途 parse 工具 JSON

兩個常見錯誤，看一下症狀：

**錯誤一：不用 `with`，串流沒被正確關閉。** 你可能用某種手動迭代的寫法卻沒關掉串流，連線洩漏，量大時耗盡連線資源。SDK 設計成 context manager 就是要你用 `with`——別跟它對抗。

**錯誤二：想在 `input_json_delta` 中途自己 parse JSON。**

```python
# 反例！partial_json 拼到一半不是合法 JSON
buffer = ""
for event in stream:
    if event.type == "content_block_delta" and event.delta.type == "input_json_delta":
        buffer += event.delta.partial_json
        args = json.loads(buffer)   # 💥 拼到一半時 json.loads 會丟 JSONDecodeError
```

`{"ci` 不是合法 JSON，你只能等 `content_block_stop` 後再 parse 完整的 buffer——或者，更簡單，**根本別自己拼，用 `get_final_message()`**。這個錯誤示範的意義是：讓你理解 SDK 的 `get_final_message()` 幫你省掉了什麼麻煩，從而不會手癢去重造它。

## 踩雷集錦

1. **以為串流讓模型變快**：不會。總生成時間一樣，串流改善的是**感知延遲**和過程回饋。別拿串流當效能優化的解（那是 Ch 37 的事）。
2. **不用 `with` 管理串流**：串流是需要開關的連線資源，用 `with` 才能保證關閉。手動管理容易洩漏連線。
3. **在串流中途 parse 工具參數 JSON**：`partial_json` 片段拼到一半不是合法 JSON，中途 `json.loads` 會炸。要等 block 結束，或直接用 `get_final_message()`。
4. **把顯示邏輯（`print`）寫死進 agent 核心**：示範可以，產品不行。要把「文字增量怎麼顯示」抽出去（callback / queue），核心 loop 只管控制流程，才能換不同前端。
5. **忘了 `flush=True`**：不刷新緩衝，逐字串流看起來會一坨一坨跳，失去串流的意義。
6. **以為串流就不用處理 stop_reason 了**：照樣要。`stop_reason` 在 `message_delta` 事件出現，或直接從 `get_final_message()` 拿。串流改的是顯示，不是控制邏輯。

## 進階：再往深一層

- **async 串流**：`AsyncAnthropic().messages.stream(...)` 配 `async for` 是 Web 後端（FastAPI 之類）的標配——你會把文字增量 `yield` 成一個 SSE（Server-Sent Events）或 WebSocket 流推給瀏覽器。同步版本適合 CLI 與學習，async 版本適合服務多個並發使用者。概念一樣，只是 `async`/`await` 包裝。
- **串流事件可以做「即時工具進度」**：進階 UI 會利用 `content_block_start`（知道模型開始要某個工具了）即時顯示「正在準備呼叫 get_weather…」，甚至用 `input_json_delta` 顯示參數逐步成形。這是把串流的「過程」價值用到極致。代價是程式碼複雜度，多數 agent 不需要。
- **串流與 thinking**：當模型開啟 extended thinking，思考內容會以專屬的串流事件（`thinking_delta`、以及帶簽章的 `signature_delta`）傳來。要注意這不是「未經修飾的完整內心獨白」：依設定（例如 summarized 顯示模式）串流到的是**經過濃縮的推理摘要**；若關閉顯示，則根本不會送 thinking 增量。所以你能不能、以及看到多少思考過程，取決於設定，別假設一定看得到完整 chain-of-thought。怎麼處理 thinking 的串流，看官方 streaming 與 extended thinking 文件。

## 動手練習

1. 把第一段的逐字串流範例跑起來，故意拿掉 `flush=True`，對比有無刷新的顯示差異——感受 `flush` 的作用。
2. 用 `for event in stream:` 把所有原始事件的 `event.type` 印出來（不印內容），跑一個會用工具的問題，親眼看到 `message_start` → `content_block_start/delta/stop` → `message_delta` → `message_stop` 的完整序列，並找出 `tool_use` 的 `input_json_delta` 片段。
3. 把 `chat_streaming` 接進你的 `Agent`，跑一個需要兩個工具的任務，觀察「文字即時顯示 →〔執行工具中〕→ 再串流下一段」的節奏，對比 Ch 7 非串流版的「沉默幾秒才一次吐出」。
4. 故意在 `input_json_delta` 中途 `json.loads(buffer)`，重現 `JSONDecodeError`，體會為什麼要等 block 結束。

## 本章重點整理

- 串流改善的是**感知延遲**與過程回饋，不是真實生成速度。
- 用 `with client.messages.stream(...) as stream`，`stream.text_stream` 逐塊吐文字、`get_final_message()` 拿回完整 Message。
- 串流下工具參數以 `input_json_delta` 片段傳來、拼到一半不是合法 JSON；`get_final_message()` 會幫你拼好 parse 好。
- 接進 loop 時，串流只改「怎麼拿 Message」和「文字怎麼顯示」，stop_reason 分流等控制邏輯完全不變。
- 顯示層要跟控制層解耦：核心 loop 別寫死 `print`，把增量交給 UI 層。

## 自我檢核

- [ ] 我能解釋串流改善的是感知延遲而非真實速度，並用一個比喻說清楚
- [ ] 我能寫出用 `with ... stream(...)` + `text_stream` + `get_final_message()` 的最小串流
- [ ] 我知道串流下工具參數是 `input_json_delta` 拼出來的，以及為什麼不能中途 parse
- [ ] 我能說明為什麼串流接進 loop 後，Ch 7 的 stop_reason 邏輯一行都不用改
- [ ] 我知道為什麼產品不該把 `print` 寫死進 agent 核心

## 延伸閱讀

### 官方文件

- **[Anthropic — Streaming Messages](https://docs.anthropic.com/en/api/messages-streaming)**
  - **讀哪裡**：事件類型總覽（`message_start` / `content_block_*` / `message_delta` / `message_stop`），以及 `input_json_delta` 的說明。
  - **能學到什麼**：本章「底層事件」的權威定義；以及 thinking、citations 等進階 block 在串流下的事件型態。
  - **前提知識**：本章看完即可。

- **[Anthropic Python SDK README（Streaming 段落）](https://github.com/anthropics/anthropic-sdk-python)**
  - **讀哪裡**：「Streaming responses」與 `stream()` helper、`text_stream`、`get_final_message()` 的用法；以及 `AsyncAnthropic` 的串流。
  - **能學到什麼**：本章用的便利層在 SDK 裡的完整 API，與同步/非同步的差異。
  - **前提知識**：本章看完即可。

### 部落格 / 技術文章

- **[Server-Sent Events（MDN）](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)** — MDN Web Docs
  - **這篇說什麼**：SSE 是把串流文字推給瀏覽器最常用的傳輸機制；進階區提到的 Web 串流就是建在它之上。
  - **讀哪裡**：「Using server-sent events」概念與 `EventSource` 範例。
  - **為什麼值得讀**：當你要把 agent 串流接到 Web 前端，這是你需要的另一半知識；MDN 是最可靠的 Web 標準來源。

下一章我們補上 loop 的最後一塊韌性：錯誤處理與重試。API 會 429、會 500、會斷線，工具會炸——一個生產級 harness 必須能分辨「該重試」「該放棄」「該回報給模型」，而不是一遇錯就整個崩潰。

→ [Ch 9 錯誤處理與重試](./09-error-handling-retry.md)
