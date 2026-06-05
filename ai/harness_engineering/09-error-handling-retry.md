# Ch 9 — 錯誤處理與重試

> **目標**：讓你的 loop 在真實世界活下來。API 會限流、會 500、會斷線；工具會丟例外。讀完你能分辨每種錯誤該「重試、放棄、還是回報給模型」，知道 SDK 已經幫你重試了哪些、你還要自己處理哪些，並把這套接進 Ch 7 的 loop，做出一個不會一遇錯就整個崩潰的 harness。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續前面的 `Agent` class。

## 為什麼錯誤處理是 agent「能不能上線」的分水嶺

在你電腦上跑通的 agent，跟能放給真實使用者用的 agent，最大的差距往往不是功能，是**錯誤處理**。原因很簡單：你 demo 時跑三次都成功，但上線後跑三萬次，那些「萬分之一」的網路抖動、限流、伺服器過載，全都會發生，而且會在最尷尬的時候發生。

更關鍵的是，agent 把錯誤的殺傷力**放大**了。一個普通的 API 呼叫失敗，使用者重按一下就好。但 agent 一個任務跑十輪、每輪一次 API 呼叫，只要任何一輪因為一個可重試的 429 就讓整個 `chat()` 拋例外崩潰，使用者前面九輪的工作（還有花掉的錢）全部白費。**會自己跑多輪的東西，對「中途出錯」特別脆弱**——所以錯誤處理對 agent 不是加分項，是及格線。

## 先建立直覺：錯誤分三類，對應三種反應

面對任何一個錯誤，harness 要先問一個問題：**這個錯誤，重試有沒有用？** 答案把錯誤分成三類，各對應一種反應：

```
   錯誤發生
       │
       ▼
   「重試有沒有用？」
   ┌────────────────┬─────────────────────┬──────────────────────┐
   ▼                ▼                     ▼
 暫時性的            永久性的               不是「失敗」，是
 (transient)        (permanent)           模型該知道的「結果」
 重試可能會好         重試一百次也一樣        ─────────────────────
 ─────────────      ─────────────         工具執行失敗
 429 限流            401 金鑰錯            （檔案不存在、參數錯）
 500/529 過載        400 請求格式錯
 連線逾時/斷線        404 模型名打錯
       │                │                       │
       ▼                ▼                       ▼
   等一下再重試        放棄、回報給人          當 tool_result 回給模型
  （指數退避）         （別重試，沒用）         讓模型自己換方法（Ch 4 講過）
```

這張圖是本章的骨架。先把這個分類法刻進腦子：**「重試有沒有用」決定一切**。下面把三類各講清楚，並對應到 SDK 的錯誤類別。

## 一、SDK 的錯誤類別階層

`anthropic` SDK 把錯誤組織成一個繼承樹，所有錯誤都繼承自 `anthropic.APIError`。你靠 catch 不同的子類別來分辨情況：

```
APIError                          ← 所有 API 錯誤的根
├── APIConnectionError            ← 連不上（網路問題、DNS、斷線）
│   └── APITimeoutError           ← 請求逾時
└── APIStatusError                ← 伺服器有回應，但回了一個錯誤狀態碼
    │                               （有 .status_code、.request_id 可讀）
    ├── BadRequestError           (400) ← 你的請求格式/內容不對
    ├── AuthenticationError       (401) ← 金鑰錯/沒帶
    ├── PermissionDeniedError     (403) ← 沒權限
    ├── NotFoundError             (404) ← 找不到（例如模型名打錯）
    ├── ConflictError             (409) ← 資源衝突
    ├── RequestTooLargeError      (413) ← 請求太大
    ├── UnprocessableEntityError  (422) ← 內容無法處理
    ├── RateLimitError            (429) ← 限流，你太快了
    ├── InternalServerError       (500) ← 伺服器自己出錯
    └── OverloadedError           (529) ← 伺服器整體過載（高峰時段常見）
```

（SDK 還有 `ServiceUnavailableError` (503)、`DeadlineExceededError` (504) 等更細的類別；不用全背，記住「怎麼分類」比記住每個名字重要。）

把它對應回三類：

- **暫時性（重試有用）**：`APIConnectionError` / `APITimeoutError`、`ConflictError` (409)、`RateLimitError` (429)、`InternalServerError` (500/5xx)、`OverloadedError` (529)。
- **永久性（重試沒用）**：`BadRequestError` (400)、`AuthenticationError` (401)、`PermissionDeniedError` (403)、`NotFoundError` (404)、`RequestTooLargeError` (413)、`UnprocessableEntityError` (422)。這些是**你的請求本身有問題**，重試只會用一樣錯的請求再撞一次。

> 一個你會遇到的具體值：**429（限流）** 和 **529（overloaded，伺服器整體過載）** 是最常見的「暫時性」錯誤，尤其在高併發或尖峰時段。它們不是你的 bug，是「現在太擠，等一下」。正確反應是退避重試，不是放棄。

## 二、好消息：SDK 已經幫你重試了一部分

很多人不知道：**`anthropic` SDK 預設就會自動重試某些暫時性錯誤**，內建指數退避（exponential backoff）。預設會重試的包含連線錯誤、408、409、429、以及 5xx 這類。也就是說，Ch 7 那個沒有任何 try/except 的 loop，其實已經默默享受了 SDK 的重試保護——只是你不知道而已。

你可以調整重試次數：

```python
from anthropic import Anthropic

# 全域設定：這個 client 的每次請求最多重試 5 次
client = Anthropic(max_retries=5)     # 預設是 2

# 或單次請求覆寫
resp = client.with_options(max_retries=0).messages.create(...)   # 這次不重試
```

**這對你的意義**：暫時性錯誤的「等一下再試」這件事，SDK 在單次請求的層級已經處理掉大半。你不需要自己對 `client.messages.create()` 包一層 retry 迴圈去重試 429——那是重造 SDK 已有的輪子，還可能跟它的退避疊在一起亂掉。

那你還要處理什麼？兩種情況：

1. **重試次數用完還是失敗**：SDK 退避重試了 N 次仍然 429/500，它最後會把錯誤**拋出來**。這時才輪到你的程式碼決定怎麼辦（見第四節）。
2. **永久性錯誤**：SDK **不會**重試 400/401/404 這類（因為重試沒用）。它會直接拋出，你要 catch 並做正確的事（通常是回報、不重試）。

## 三、設好 timeout，別讓 agent 永遠卡著

重試之外，另一個務實設定是 **timeout**。預設 timeout 可能比你的場景需要的長，一個卡住的請求會讓整個 agent 回合凍結。明確設它：

```python
client = Anthropic(timeout=60.0)   # 每次請求最多等 60 秒，超過丟 APITimeoutError

# 也可單次覆寫（例如某個你知道會比較久的請求）
resp = client.with_options(timeout=120.0).messages.create(...)
```

`APITimeoutError` 是 `APIConnectionError` 的子類，屬於暫時性，SDK 預設會重試它。設 timeout 的意義是**界定「等多久算卡住」**——配合 Ch 7 的 wall-clock 剎車，agent 才不會因為一個慢請求無限期凍結。

## 四、把錯誤處理接進 loop

現在把三類錯誤的處理，包進 Ch 7 的 `chat()`。重點是**只 catch 你知道怎麼處理的，並且讓 agent 優雅降級而非崩潰**：

```python
import anthropic

class Agent:
    # ... 同前 ...

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            try:
                resp = self.client.messages.create(
                    model="claude-opus-4-8",
                    max_tokens=2048,
                    system=self.system_prompt,
                    tools=TOOL_SCHEMAS,
                    messages=self.messages,
                )
            except anthropic.APIStatusError as e:
                # 伺服器回了錯誤狀態碼。永久性的（4xx）重試沒用，直接回報。
                # 暫時性的（429/5xx）能到這裡，代表 SDK 已經重試到上限仍失敗。
                if e.status_code == 429 or e.status_code >= 500:
                    return "（伺服器忙碌或暫時不可用，已重試多次仍失敗，請稍後再試）"
                # 4xx：請求本身有問題，這是 harness 的 bug，不該對使用者裝沒事
                return f"（請求發生錯誤 {e.status_code}，這通常是程式設定問題，請聯絡維護者）"
            except anthropic.APIConnectionError:
                # 連線問題，SDK 也重試過了仍失敗
                return "（無法連線到服務，請檢查網路後再試）"

            # ── 成功拿到 resp，後續是 Ch 7 的 stop_reason 處理 ──
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue
            return "".join(b.text for b in resp.content if b.type == "text")

        return "（達到最大回合數上限）"
```

設計原則，每一條都重要：

- **catch 具體類別，不要裸 `except Exception`**。裸 catch 會把「程式真正的 bug」（例如你打錯變數名的 `NameError`）也吞掉，讓 debug 變地獄。只 catch 你真的知道怎麼處理的 API 錯誤類別。
- **永久性錯誤要「誠實地失敗」**：400/401 這類是 harness 自己的問題（金鑰沒設好、請求組壞了），不該對使用者裝沒事繼續，也不該重試。回一個「這是設定問題」的訊息，讓問題浮出來被修，而不是被靜默吞掉。
- **暫時性錯誤到這層代表 SDK 已重試到上限**——你的 catch 是「最後一道防線」，給使用者一個體面的「稍後再試」，而不是丟一個醜陋的 traceback。
- **工具錯誤不在這裡 catch**——它在 `run_tool_uses` 裡被轉成 `is_error` 的 tool_result（Ch 4），屬於第三類「回報給模型」，根本不會走到這個 try/except。

## 五、第三類：工具錯誤回給模型（複習與強調）

Ch 4 已經建立過這個原則，但它太重要，在錯誤處理章一定要再強調並放進這張完整的圖。工具執行失敗（檔案不存在、API 回 404、參數讓函式丟 `TypeError`）**不是 agent 的災難，是模型該知道的一條資訊**：

```python
# run_tool_uses 裡（Ch 4）：工具炸了 → 包成 is_error 的 tool_result
try:
    result = func(**block.input)
    is_error = False
except Exception as e:
    result = f"工具 {block.name} 執行失敗：{e}"
    is_error = True
```

為什麼這類不走「重試或放棄」，而是「回給模型」？因為**模型有能力對工具失敗做出反應**：檔案不存在，它可以改去找對的檔名；參數錯，它可以換參數重試；真的做不到，它可以據實告訴使用者。把工具錯誤當成對話的一部分餵回去，agent 就有了「從失敗中恢復」的韌性。反之，如果工具一炸就讓整個 `chat()` 拋例外，你就剝奪了模型自我修正的機會，agent 變得一碰到小狀況就全盤皆輸。

**這是 agent 錯誤處理跟一般程式最不一樣的地方**：在傳統程式裡，錯誤往上拋給呼叫者；在 agent 裡，工具錯誤往「下」傳給模型，因為模型常常是最有能力處理它的那個角色。

## 失敗示範：裸 except 吞掉一切

看一個害人無數的反例：

```python
# 反例！絕對不要這樣
for _ in range(self.max_turns):
    try:
        resp = self.client.messages.create(...)
        # ... 處理 ...
    except Exception:
        continue   # 「出錯就跳過再試一次」
```

這段為什麼是災難：

1. 它把 `AuthenticationError`（金鑰錯，重試一萬次都沒用）也拿去重試，白白燒迴圈、撞 `max_turns`。
2. 它把你程式裡真正的 bug（`KeyError`、`AttributeError`、打錯的變數名）也吞掉，讓你完全看不到問題在哪——你只會看到 agent「莫名其妙跑滿回合然後說失敗」，卻不知道是哪行 code 爛了。
3. `continue` 沒有退避，會瘋狂連打 API。

**裸 `except Exception` + `continue` 是 agent 錯誤處理的頭號反模式。** 寧可不 catch（讓它大聲崩潰，至少你看得到 traceback），也不要這樣靜默吞掉一切。catch 要精準、要有對應的處理，不是用來「讓錯誤消失」。

## 踩雷集錦

1. **重造 SDK 已有的重試**：自己對 `create()` 包一層 retry 去重試 429，結果跟 SDK 的內建退避疊起來，行為混亂、退避亂掉。先知道 SDK 預設會重試暫時性錯誤，調 `max_retries` 就好。
2. **重試永久性錯誤**：對 400/401/404 重試是純粹浪費——請求本身就錯了，重試一百次還是一樣錯。分清楚「暫時 vs 永久」是錯誤處理的核心判斷。
3. **裸 `except Exception`**：吞掉真正的 bug、把不可重試的當可重試。永遠 catch 具體類別。
4. **把工具錯誤當 API 錯誤處理**：工具丟例外不該讓 loop 崩潰或重試整個請求，應在工具執行層包成 `is_error` tool_result 回給模型。兩者是完全不同的處理路徑。
5. **沒設 timeout**：用預設值，可能讓一個卡住的請求凍結整個 agent 回合。明確設 timeout，配合 wall-clock 剎車。
6. **永久性錯誤靜默吞掉、對使用者裝沒事**：金鑰沒設好卻回「請稍後再試」，使用者再試一萬次也不會好。永久性錯誤要誠實暴露，讓它被修。

## 進階：再往深一層

- **idempotency（冪等）**：重試一個請求，怎麼確保它不會被「做兩次」造成重複副作用？通用的解法是 idempotency key——讓重試帶同一個 key，伺服器就能辨識「這是同一個請求的重試，不是新請求」。`anthropic` SDK 的底層 base client 有這套機制的接線，但要注意：直接呼叫 Anthropic Messages 時，預設並沒有自動替你帶上 idempotency key（這塊預設是關的），所以別假設「SDK 重試一定冪等」。對「呼叫一次模型」這種讀取性操作影響不大；但當你的**工具**會造成外部副作用（送錢、發信），冪等性是你自己的工具層必須處理的關鍵問題——不能指望 SDK 幫你擋。
- **退避策略的細節**：指數退避（每次重試等待時間翻倍）配上 jitter（隨機抖動，避免大量 client 同時重試造成「重試風暴」）是業界標準。SDK 內建的退避會參考伺服器的 `Retry-After` / `retry-after-ms` 標頭（伺服器明確說「等這麼久再來」），不過只在它解析出的延遲是「大於 0 且不超過 60 秒」這個合理範圍內才採用；超出範圍就退回自己的指數退避。自己實作退避時這些都要考慮，但多數時候用 SDK 的就好。
- **區分「對使用者的訊息」和「給 log 的細節」**：上面範例回給使用者的是友善的「稍後再試」，但你應該同時把完整的 exception（status code、request id、錯誤訊息）寫進 log。使用者不需要看 traceback，但你 debug 時非常需要——尤其 **request id** 是你跟 Anthropic 回報問題時的關鍵線索。實務取法：失敗的 `APIStatusError` 上可以讀 `e.request_id`；而成功的回應則是從回傳物件的 `_request_id` 拿。把它一起記進 log，銜接 Ch 35 的 observability。

## 動手練習

1. 把 client 改成 `Anthropic(api_key="sk-ant-錯的", max_retries=2)`，呼叫一次，觀察它**不會**重試（401 是永久性），而是立刻拋 `AuthenticationError`——印證 SDK 只重試暫時性錯誤。
2. 把本章的 try/except 接進你的 `Agent.chat()`，然後故意把模型名打成不存在的，確認你看到的是那句「程式設定問題」而不是裸 traceback。
3. 寫一個會丟例外的工具，確認它的錯誤是透過 `is_error` tool_result 回給模型（agent 繼續跑、模型有反應），**而不是**被第四節的 try/except 接住——體會「工具錯誤」和「API 錯誤」走的是兩條完全不同的路。
4. （思考題）把第四節的 catch 改成裸 `except Exception: continue`，想像你打錯了一個變數名導致 `NameError`，這個 bug 會怎麼被這段 catch 藏起來、你 debug 時會看到什麼假象。

## 本章重點整理

- 面對錯誤先問「重試有沒有用」，分三類：暫時性（重試）、永久性（放棄回報）、工具錯誤（回給模型）。
- SDK 預設就會對暫時性錯誤（連線、429、5xx 等）做指數退避重試；用 `max_retries` 調整，別自己重造。
- 永久性錯誤（4xx，金鑰/格式/找不到）重試沒用，要誠實暴露讓它被修，不要靜默吞掉。
- 工具錯誤走第三條路：包成 `is_error` tool_result 回給模型，讓模型自我修正——這是 agent 跟一般程式最不同之處。
- 永遠 catch 具體錯誤類別，絕不用裸 `except Exception` + `continue`。
- 設 timeout、記 log（含 request id），對使用者友善降級、對自己保留 debug 細節。

## 自我檢核

- [ ] 給我任一個錯誤，我能判斷它屬於三類的哪一類、該怎麼反應
- [ ] 我知道 SDK 預設會重試哪些、不重試哪些，以及為什麼不該自己重造重試
- [ ] 我能解釋為什麼工具錯誤要「往下」回給模型，而不是「往上」拋
- [ ] 我能說出裸 `except Exception` + `continue` 的三個具體危害
- [ ] 我知道永久性錯誤為什麼要誠實暴露而非對使用者裝沒事

## 延伸閱讀

### 官方文件

- **[Anthropic — Errors（HTTP 錯誤碼與類型）](https://docs.anthropic.com/en/api/errors)**
  - **讀哪裡**：各 HTTP 狀態碼（400/401/403/404/429/500/529）的意義，以及 request id 的說明。
  - **能學到什麼**：本章三分類的權威依據；以及 529 overloaded、Retry-After 等細節。
  - **前提知識**：本章看完即可。

- **[Anthropic Python SDK README（Retries、Timeouts、Errors）](https://github.com/anthropics/anthropic-sdk-python)**
  - **讀哪裡**：「Handling errors」「Retries」「Timeouts」三節。
  - **能學到什麼**：`max_retries` 的預設值與會自動重試的錯誤清單、`timeout` 的設法、錯誤類別階層的權威來源、以及 `with_options` 的單次覆寫。
  - **前提知識**：本章看完即可——這是本章程式碼的根據。

### 部落格 / 技術文章

- **[Exponential Backoff And Jitter（AWS Architecture Blog）](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)** — Marc Brooker, AWS
  - **這篇說什麼**：為什麼重試要用指數退避 + jitter，以及不加 jitter 會造成的「重試風暴」（thundering herd）。
  - **讀哪裡**：整篇不長；重點看 jitter 那幾段的圖。
  - **為什麼值得讀**：SDK 幫你做了退避，但你該懂它為什麼這樣做——這篇是這個主題最常被引用的權威來源，作者是 AWS 的 principal engineer。

Part 1 到這裡完成——你已經有一個能跑多輪、會用工具、會正確停止、會串流、不會一遇錯就崩潰的 agent loop。接下來的練習 A，要你把這些拼成一個你自己的、完整可跑的 mini agent loop。

→ [練習 A：寫一個能跑的 mini agent loop](./practice-a-mini-agent-loop.md)
