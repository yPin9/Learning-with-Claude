# Ch 7 — 停止條件與 turn 控制

> **目標**：把 Ch 4 那個「`tool_use` 就繼續、其他就結束」的粗糙分流，升級成能正確處理**每一種** `stop_reason` 的 loop。讀完你能回答：模型有哪些停下來的理由、各該怎麼處理、`max_tokens` 被截斷時怎麼續寫、怎麼偵測並打斷「鬼打牆」迴圈、以及一個負責任的 agent 該有哪幾道剎車。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續 Ch 6 的 `Agent` class。

## 為什麼「什麼時候停」比「怎麼跑」更難

Ch 4、Ch 6 的 loop 都用同一個粗暴規則：`stop_reason == "tool_use"` 就繼續，否則回傳。這在 happy path 能動，但它把「不是 tool_use 的情況」全當成「正常講完了」——這是個會出事的簡化。

舉個例子：模型答到一半，因為 `max_tokens` 上限被切斷，`stop_reason` 是 `max_tokens`。Ch 4 的 loop 會把這個**半截的答案**當成最終結果回傳給使用者。使用者看到一句話講到一半就沒了，還以為是 bug。真正的問題是 harness 沒分辨「講完了」和「被切斷了」。

agent 的 loop 控制，難點從來不在「怎麼往前跑」（那就是個 while），而在「怎麼正確地停、以及停在對的地方」。這章把停止這件事做對。

## 先建立直覺：每一輪結束，模型都遞給你一張「為什麼停」的紙條

把 `stop_reason` 想成模型每輪結束時夾帶的一張紙條，上面寫著它**為什麼**停下來。harness 的工作是讀這張紙條，做對應的事：

```
   模型回應 → 讀 stop_reason 這張紙條
   ┌──────────────────┬─────────────────────────────────┐
   │ 紙條寫著           │ harness 該做什麼                  │
   ├──────────────────┼─────────────────────────────────┤
   │ "end_turn"        │ 講完了 → 把答案給使用者，停          │
   │ "tool_use"        │ 要工具 → 執行、把結果塞回、再問       │
   │ "max_tokens"      │ 撞到「本次輸出上限」被切斷 → 提示/續寫 │
   │ "model_context_   │ 撞到「整個 context window」上限       │
   │  window_exceeded" │   → 也是被切斷，但要靠縮 context 解決  │
   │ "stop_sequence"   │ 撞到你設的停止詞 → 通常當結束        │
   │ "pause_turn"      │ server-side 工具迴圈達上限暫停        │
   │                   │   → 原樣回送讓它續                   │
   │ "refusal"         │ 模型拒絕回答 → 當結束、別硬逼         │
   └──────────────────┴─────────────────────────────────┘
```

Ch 4 只認得前兩張紙條，其他全當「end_turn」處理。這章要讓 harness 認得每一張。**注意：實際會出現哪些 stop_reason、叫什麼名字，取決於模型與 API 版本——所以正確的工程姿態不是死背清單，而是「明確處理你認得的，並且對不認得的有安全的預設行為」。** 下面的處理邏輯就按這個原則設計。

## 一、各種 stop_reason 逐一處理

逐張紙條講它的意義與正確反應：

### `end_turn`——正常講完

模型認為任務告一段落、不需要再做事了。這是最常見的「結束」。harness 把累積的文字回給使用者即可。**只有這個才是真正的「正常完成」。**

### `tool_use`——要工具

模型要求用工具（Ch 2–5 講透了）。執行、把 tool_result 塞回、再問。這是 loop 唯一會「繼續轉」的情況。

### `max_tokens`——被長度截斷，沒講完

這是 Ch 4 處理錯的那個。模型還想繼續輸出，但撞到你給的 `max_tokens` 上限被硬切。此時 `content` 裡是**半截**的內容。怎麼辦？兩條路：

1. **簡單版**：告訴使用者「回應被長度限制截斷了」，並把半截內容給他，讓他決定要不要追問。
2. **續寫版**：把這半截的 assistant 回應接回 messages，再呼叫一次——模型會從斷掉的地方接著寫。這就是「continuation」。

```python
# 偵測並提示截斷（簡單版）
if resp.stop_reason == "max_tokens":
    partial = "".join(b.text for b in resp.content if b.type == "text")
    return partial + "\n\n（⚠️ 回應因長度上限被截斷，可輸入「繼續」讓我接著說）"
```

**為什麼不要無腦自動續寫？** 因為自動續寫會偷偷放大成本與延遲，而且若模型陷入冗長輸出，可能一直撞 max_tokens 一直續，停不下來。把「要不要續」交給上層決定（或設一個續寫次數上限）比較安全。根本的解法通常是**把 `max_tokens` 設大一點**，或在 system prompt 要求模型精簡。

還有一個陰險的特例要小心：**`max_tokens` 可能切在一個還沒寫完的 `tool_use` block 中間**。也就是模型正在輸出工具呼叫的參數，結果被長度上限截斷，你拿到一個**不完整、無法執行**的 tool_use。這時不該把它當「半截文字」回傳，也不該硬執行那個殘缺的工具——正確反應是**用更大的 `max_tokens` 重試這一輪**，讓模型有空間把 tool_use 寫完。所以嚴謹的 handler 在 `max_tokens` 時會先看「被截斷的是文字還是 tool_use」，再決定是提示使用者、還是放大上限重試。

### `model_context_window_exceeded`——撞到整個 context window 上限

這跟 `max_tokens` 很像（都是「被截斷、沒講完」），但**原因不同**，所以解法也不同：

- `max_tokens` 是撞到**你這次請求設的輸出上限**——解法是把 `max_tokens` 設大。
- `model_context_window_exceeded` 是撞到**模型整個 context window 的上限**（input + output 加起來塞不下了）——這時把 `max_tokens` 設大沒用，因為問題是 context 本身太肥。解法是**縮小 context**：壓縮歷史、裁剪工具結果、把東西外移到 memory——正是 Part 2 在教的。

這個 reason 在較新的模型（如 Sonnet 4.5 及之後）預設可用，較早的模型可能要透過 beta header 才會回這個值、否則行為不同。處理上可以跟 `max_tokens` 歸在一起「都是截斷」，但要在訊息裡區分原因，否則使用者（和你自己 debug 時）會被誤導成「調大 max_tokens 就好」。

### `stop_sequence`——撞到你設的停止詞

如果你在 `create()` 傳了 `stop_sequences=["END"]` 之類的自訂停止詞，模型輸出碰到它就會停，`stop_reason` 是 `stop_sequence`，並在 `resp.stop_sequence` 告訴你**撞到的是哪一個**停止詞（這個被撞到的序列本身不算正常生成內容，不會出現在輸出文字裡）。這通常是你**刻意設計**的結束信號（例如某種結構化輸出的結尾標記）。沒設 `stop_sequences` 就不會出現，多數 agent 用不到，知道有這回事即可。

### `pause_turn`——server-side 工具迴圈暫停

`pause_turn` 出現在**使用 server-side 工具**的情境：當 Anthropic 那端的取樣迴圈跑到它的迭代上限時，會回一個 `pause_turn`，表示「我還沒做完，但先把目前狀態回給你」。正確處理是**把這個回應原樣接回 messages、再呼叫一次**，讓它從暫停處接續。如果你的 agent 只用 client-side 工具，基本不會遇到它——但別把它誤判成結束。

### `refusal`——模型拒絕

模型基於安全等理由拒絕回應時，可能回 `refusal`。harness 該**當成結束**，把拒絕訊息呈現給使用者，**不要**自動重試或想辦法繞過——那違反安全設計，也通常沒用。

## 二、把分流寫成一個明確的 handler

把上面的邏輯收進 Ch 6 的 `chat()`，用明確的分支取代 Ch 4 的「else 全當結束」：

```python
class Agent:
    # ... __init__、system_prompt、messages 同 Ch 6 ...

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            resp = self.client.messages.create(
                model="claude-opus-4-8",
                max_tokens=2048,
                system=self.system_prompt,
                tools=TOOL_SCHEMAS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})
            reason = resp.stop_reason

            if reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue

            text = "".join(b.text for b in resp.content if b.type == "text")

            if reason == "end_turn":
                return text
            if reason == "max_tokens":
                # 注意：也可能截在半截 tool_use 上，嚴謹版會偵測並放大 max_tokens 重試
                return text + "\n\n（⚠️ 回應因「本次輸出長度上限」被截斷）"
            if reason == "model_context_window_exceeded":
                # 跟 max_tokens 不同：要靠縮 context 解決（Part 2），調大 max_tokens 沒用
                return text + "\n\n（⚠️ 已超出模型 context window 上限，需要縮減對話內容）"
            if reason == "refusal":
                return text or "（模型拒絕回應此請求）"
            if reason in ("pause_turn",):
                continue  # 原樣接回（已 append），再轉一圈讓它續
            # 不認得的 stop_reason：安全預設——把目前文字回傳並標注
            return text + f"\n\n（agent 收到未預期的 stop_reason: {reason}）"

        return "（達到最大回合數上限，未能在限定回合內完成）"
```

設計重點：

- **每種已知 reason 明確處理**，不再有「其他全當結束」的含糊。
- **最後的 fallback 是安全的**：碰到沒見過的 stop_reason（未來 API 可能新增），不會崩潰、不會假裝講完，而是把現有文字回傳並標注，讓你從 log 發現「有個新 reason 我沒處理」。這就是前面說的「對不認得的有安全預設」。
- **`max_tokens` 提高到 2048**：減少被截斷的機率。這是最務實的第一道防線。

## 三、turn 控制：不只是 `max_turns`

`max_turns` 防的是「跑太多輪」，但它太鈍——10 輪小工具呼叫跟 10 輪各讀一個大檔，成本天差地遠。負責任的 agent 通常疊好幾道剎車：

| 剎車 | 防什麼 | 怎麼做 |
|---|---|---|
| `max_turns` | 無限迴圈、鬼打牆 | `for _ in range(max_turns)`（已有） |
| token 預算 | 燒錢 | 累加每輪 `usage`，超過上限就停（Ch 37 深談） |
| wall-clock 逾時 | 卡太久 | 記開始時間，每輪檢查是否超時 |
| 重複偵測 | 模型反覆做同一件事 | 偵測連續相同的 tool 呼叫（下一節） |

token 預算的雛形：

```python
    def chat(self, user_input: str, token_budget: int = 50_000) -> str:
        self.messages.append({"role": "user", "content": user_input})
        spent = 0
        for _ in range(self.max_turns):
            resp = self.client.messages.create(...)
            spent += resp.usage.input_tokens + resp.usage.output_tokens
            # ... 正常處理 ...
            if spent > token_budget:
                return "（已達 token 預算上限，提前停止以控制成本）"
```

**為什麼要疊這麼多？** 因為 agent 的失控有很多種樣貌：有的是回合多但每回合小（`max_turns` 接得住）、有的是回合少但每回合啃巨大檔案（要靠 token 預算）、有的是卡在一個慢工具上（要靠逾時）。單一剎車擋不住所有失控模式。這幾道剎車全都是 Ch 3 講的 **policy** 在 loop 裡的具體實現。

## 四、偵測「鬼打牆」：重複的工具呼叫

一種典型失敗：模型陷入「呼叫 `read_file('x')` → 看結果 → 又呼叫 `read_file('x')` → ……」的迴圈，每圈都一樣，但永遠不 `end_turn`。`max_turns` 最終會停它，但在停之前已經燒了好幾輪。更聰明的做法是**偵測到重複就提早打斷**：

```python
import json

def _tool_signature(content_blocks) -> str:
    """把這一輪所有 tool 呼叫壓成一個可比較的指紋。"""
    calls = [
        (b.name, json.dumps(b.input, sort_keys=True))
        for b in content_blocks if b.type == "tool_use"
    ]
    return json.dumps(sorted(calls))

# 在 loop 裡：
recent_signatures = []
# ... 拿到 resp 後 ...
if reason == "tool_use":
    sig = _tool_signature(resp.content)
    recent_signatures.append(sig)
    # 連續 3 次完全相同的工具呼叫 → 大概卡住了
    if recent_signatures[-3:].count(sig) == 3 and len(recent_signatures) >= 3:
        # 注入一則提示，打斷迴圈，逼模型換策略或收手
        self.messages.append(run_tool_uses(resp.content))  # 仍要回 tool_result（協議要求）
        self.messages.append({
            "role": "user",
            "content": "你已經重複呼叫了相同的工具多次但沒有進展。請換個方法，或如果無法完成，直接告訴我目前的狀況。",
        })
        continue
    self.messages.append(run_tool_uses(resp.content))
    continue
```

注意一個協議細節：即使你判定它在鬼打牆，**那一輪的 tool_use 仍然要回對應的 tool_result**（Ch 5 的硬規則），否則下一個請求會 400。所以是「先正常回 tool_result，再額外注入一則提示訊息」，而不是「跳過 tool_result 直接罵它」。這是初學者寫迴圈打斷時最容易弄錯的地方。

這裡還有個細節值得注意：上面是把提示放在**另一則獨立的 user 訊息**，而不是塞在 tool_result 那則訊息裡、緊接在 tool_result 後面。這是刻意的——官方提醒，在同一則訊息裡把文字直接接在 tool_result 之後，有時會誘導模型直接回一個空的 `end_turn`。分成兩則（先 tool_result、再提示）比較穩。

> 重複偵測沒有完美演算法——「連續 3 次相同」只是一個堪用的啟發式。有時模型重複呼叫是合理的（例如輪詢一個狀態）。所以這是「提示換策略」而非「直接終止」，給模型一個自我修正的機會比硬殺更好。失敗模式的系統性處理是 Ch 38 的主題。

## 失敗示範：把 max_tokens 當成 end_turn

回到開頭那個例子，把它跑出來看。設一個很小的 `max_tokens`，問一個需要長答案的問題：

```python
agent = Agent()
agent.max_turns = 3
resp = agent.client.messages.create(
    model="claude-opus-4-8",
    max_tokens=20,                       # 故意設超小
    system="你是助理",
    messages=[{"role": "user", "content": "詳細解釋什麼是 TCP 三次握手"}],
)
print(resp.stop_reason)                  # → max_tokens
print("".join(b.text for b in resp.content if b.type == "text"))
# → 「TCP 三次握手是建立連線的過程，首先客戶端」  ← 講到一半就斷了
```

如果你的 loop 像 Ch 4 那樣把這個當 `end_turn` 回傳，使用者就收到一句沒講完的話，而且**完全沒有任何提示說它被截斷了**。這就是為什麼要分辨 stop_reason——同樣是「停下來」，原因不同，對使用者該說的話完全不同。

## 踩雷集錦

1. **把所有非 tool_use 都當「正常結束」**：Ch 4 的簡化。`max_tokens`（沒講完）、`refusal`（拒絕）、`pause_turn`（暫停）語意完全不同，混為一談會讓使用者收到誤導的結果。
2. **自動無限續寫 max_tokens**：看到截斷就自動再呼叫續寫，聽起來貼心，但會偷偷加倍成本，且模型若一直冗長就一直續、停不下來。要嘛設續寫次數上限、要嘛把決定權交給上層。
3. **打斷鬼打牆時漏回 tool_result**：判定模型卡住後，直接 append 一則提示卻沒先回那一輪的 tool_result，下個請求就因「tool_use 沒有對應 tool_result」而 400。先回 tool_result，再注入提示。
4. **只靠 `max_turns` 一道剎車**：擋得住「輪數爆炸」，擋不住「單輪啃大檔燒 token」或「卡在慢工具」。不同失控模式要不同剎車。
5. **重複偵測太敏感**：把「合理的重複」（輪詢狀態、分批處理）誤判成鬼打牆而打斷，反而破壞正常任務。用「連續多次完全相同」當門檻，並用「提示換策略」而非「直接殺」，留容錯空間。
6. **硬背 stop_reason 清單當聖經**：API 會演進、新增 reason。寫「明確處理已知、安全 fallback 未知」的程式，比假設「就這幾種」更耐久。

## 進階：再往深一層

- **stop_reason 與 `usage` 一起看**：`max_tokens` 配合 `usage.output_tokens` 剛好等於你的上限，是「被截斷」的鐵證。把這兩者一起記進 log，debug 時一眼看出是不是長度問題。
- **「完成」其實是個語意判斷**：`end_turn` 是模型**自認為**講完了，不代表任務**真的**完成。模型可能過早收手（「我已經盡力了」其實沒做完）。要驗證「任務真的完成沒」，需要的是 eval（Ch 34），不是看 stop_reason。別把「模型停了」等同於「做對了」。
- **stop_sequences 的妙用**：雖然多數 agent 用不到，但在「讓模型產生結構化片段、碰到某標記就停」的場景（例如逐段生成、或防止模型越界續寫）很有用。它是一個便宜的輸出控制手段，值得記在工具箱裡。

## 動手練習

1. 把本章的 `chat()` handler 接進你的 `Agent`，用 `max_tokens=20` 問一個長問題，確認你看到的是「截斷提示」而不是一句沒頭沒尾的話。
2. 寫一個「永遠回相同結果」的假工具（例如 `def poll(): return "尚未完成"`），叫 agent 一直用它，觀察重複偵測在第幾輪打斷、以及被注入提示後模型怎麼反應。
3. 給 `chat()` 加上 token 預算剎車，設一個很小的預算（例如 500），跑一個會用工具的任務，確認它在超預算時提前停並回傳那句話。
4. 把 `max_turns` 與 token 預算同時設小，觀察是哪一道剎車先觸發——體會「不同剎車防不同失控」。

## 本章重點整理

- `stop_reason` 是模型每輪遞來的「為什麼停」紙條；只有 `end_turn` 是真正的正常完成。
- 各 reason 要分別處理：`tool_use` 繼續、`max_tokens` 提示截斷（或謹慎續寫）、`refusal` 當結束別硬逼、`pause_turn` 原樣回送、未知的走安全 fallback。
- turn 控制要疊多道剎車：`max_turns`、token 預算、wall-clock 逾時、重複偵測——單一剎車擋不住所有失控模式。
- 打斷鬼打牆時，協議要求你仍得先回那一輪的 tool_result，再注入提示。
- 「模型停了」不等於「任務做對了」——後者要靠 eval（Ch 34）。

## 自我檢核

- [ ] 我能說出至少四種 stop_reason 及各自的正確處理方式
- [ ] 我能解釋為什麼把 `max_tokens` 當 `end_turn` 會害到使用者
- [ ] 我能說出為什麼單靠 `max_turns` 不夠，還需要哪些剎車
- [ ] 我知道打斷重複工具呼叫時，為什麼還是得先回 tool_result
- [ ] 我能說明為什麼不該硬背 stop_reason 清單，而要寫「已知明確處理、未知安全 fallback」

## 延伸閱讀

### 官方文件

- **[Anthropic — Handling stop reasons](https://docs.anthropic.com/en/api/handling-stop-reasons)**
  - **讀哪裡**：各 `stop_reason` 值的列表與官方建議處理方式。
  - **能學到什麼**：本章每種 reason 的權威定義與當前有效值——遇到本章沒列到的新 reason 時以它為準。
  - **前提知識**：本章看完即可。

- **[Anthropic — Messages API（max_tokens、stop_sequences、usage）](https://docs.anthropic.com/en/api/messages)**
  - **讀哪裡**：`max_tokens`、`stop_sequences` 參數與 `usage` 物件的說明。
  - **能學到什麼**：續寫與截斷判斷需要的精確欄位語意。
  - **前提知識**：本章看完即可。

### 部落格 / 技術文章

- **[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic（2024）
  - **這篇說什麼**：在 agent 的 autonomous loop 段落，強調「停止條件與護欄」是讓 agent 可用於生產的前提。
  - **讀哪裡**：agents 一節關於 stopping conditions / guardrails 的論述。
  - **為什麼值得讀**：把本章的工程細節放回「為什麼生產級 agent 一定要有剎車」的大圖裡。

下一章我們處理使用者體驗的關鍵一環：串流（streaming）。讓 agent 邊想邊把文字吐給使用者看，而不是讓人對著空白畫面等十秒——以及串流如何與本章的 stop_reason、工具呼叫協調。

→ [Ch 8 串流與即時輸出](./08-streaming.md)
