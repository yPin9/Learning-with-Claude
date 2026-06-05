# Ch 6 — 多輪對話與訊息歷史管理

> **目標**：把 Ch 4 那個「跑完一個任務就結束」的函式，升級成能撐住**連續對話**的有狀態 agent。讀完你會懂：對話歷史（messages）為什麼是 agent 的全部記憶、它必須遵守哪些結構規則、怎麼用一個 class 把它收好、以及「歷史會無限長大」這個問題為什麼是 Part 2 的入口。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續 Ch 4 的工具註冊表與 `run_tool_uses`。

## 為什麼單一函式撐不起真實對話

Ch 4 的 `run_agent(user_input)` 有個隱藏假設：**一次呼叫只處理一個任務，做完 messages 就丟掉**。但真實的 agent 對話長這樣：

> 使用者：「台北幾度？」 → agent 查完答「28 度」
> 使用者：「那比東京熱嗎？」 ← 這句的「那」指的是台北，agent 必須記得上一輪

第二句的「那」只有在 agent **還記得**第一輪講過台北時才有意義。但 Ch 4 的函式每次都從空的 `messages` 開始，第二次呼叫時，上一輪的台北早就被丟了。於是 agent 變成一個健忘症患者，每句話都當第一句聽。

這章要解決的就是：**讓 messages 跨任務存活下來**。而一旦它存活，它就會一直長大——這又帶出新問題。我們一個一個處理。

## 先建立直覺：messages 是 agent 唯一的記憶體

回到 Ch 1 的鐵律：模型無狀態，每次 inference 都是全新的。所以 agent「記得什麼」完全等於「這次請求的 messages 裡有什麼」。換句話說：

```
   agent 的記憶  ≡  messages 這個 list 的內容
```

沒有別的地方。沒有隱藏的 session、沒有伺服器幫你記。你往 messages 裡放什麼，模型就「記得」什麼；你從 messages 拿掉什麼，模型就「忘記」什麼。**這個等式是整個 context engineering（Part 2）的根基**——因為「管理 agent 的記憶」就字面上等於「管理這個 list」。

多輪對話的本質，就是讓這個 list 跨越多次「使用者輸入」持續累積：

```
   第 1 輪使用者問 ──▶ [user, assistant, (tool_use, tool_result)*, assistant]
   第 2 輪使用者問 ──▶ 上面那串 + [user, assistant, ...]
   第 3 輪使用者問 ──▶ 再接上去 ...
                         └─ 越來越長，但模型靠它記得整段對話
```

## Step 1：把 agent 收成一個有狀態的 class

Ch 4 的 `messages` 是函式裡的區域變數，函式結束就消失。要讓它跨輪存活，最自然的做法是把它變成一個物件的狀態：

```python
from anthropic import Anthropic

class Agent:
    def __init__(self, system_prompt: str = "你是一個有用的助理。"):
        self.client = Anthropic()
        self.system_prompt = system_prompt
        self.messages = []          # ← 對話歷史，跨輪存活的記憶
        self.max_turns = 10

    def chat(self, user_input: str) -> str:
        """處理使用者的一次輸入，內部可能跑多個 tool 回合，回傳最終文字。"""
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            resp = self.client.messages.create(
                model="claude-opus-4-8",
                max_tokens=1024,
                system=self.system_prompt,      # system prompt 放這裡，不在 messages 裡
                tools=TOOL_SCHEMAS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue

            return "".join(b.text for b in resp.content if b.type == "text")

        return "（達到最大回合數上限）"
```

迴圈本體跟 Ch 4 幾乎一樣，關鍵差別是 **`self.messages` 不再每次重置**。現在可以連續對話：

```python
agent = Agent()
print(agent.chat("台北幾度？"))          # → 28 度（查了 get_weather）
print(agent.chat("那比東京熱嗎？"))       # → 模型記得「那=台北」，再查東京來比
print(agent.chat("剛剛我問你哪個城市？"))  # → 模型答得出「台北和東京」，因為都在 messages 裡
```

第二、三句之所以成立，是因為 `self.messages` 累積了前面所有輪的內容。**你沒有寫任何「記憶」邏輯——記憶就是這個沒被清空的 list。**

> 注意 `system_prompt` 是用 `create()` 的 `system=` 參數傳的，**不是**塞進 `messages`。system prompt 是「整段對話的設定」，它不屬於 user 也不屬於 assistant，有獨立的位置。為什麼這樣設計、怎麼寫好它，是 Ch 11 的主題。

## Step 2：messages 的結構規則

既然 messages 是手動累積的，你就得遵守 API 對它形狀的約定。這些規則 Ch 2、Ch 5 零散提過，這裡集中講清楚，因為多輪對話最容易踩。要先分清兩種「規則」：有些是 **API 真正的硬限制**（違反會 400），有些是**你最好維持的不變量**（API 可能容忍，但守住能避免一堆麻煩）。

```
   一個良好維持的 messages 結構
   ┌─────────────────────────────────────────────┐
   │ (system 透過 system= 參數，不在這個 list 裡)    │
   │                                               │
   │ user       ← 對話從 user 開始                  │
   │ assistant                                     │
   │ user       ← 維持 user / assistant 交替        │
   │ assistant  (含 tool_use)                      │
   │ user       (tool_result 排在最前，緊接上一則)    │
   │ assistant                                     │
   │ ...                                           │
   └─────────────────────────────────────────────┘
```

**API 的硬規則（違反會 400）：**

1. **第一則必須是 `user`**（system 走獨立參數，不算在內）。
2. **tool_use 與 tool_result 成對且緊接**：assistant 訊息裡的每個 tool_use，下一則 user 訊息必須有對應的 tool_result，而且這則 user 訊息要**緊接**在那則 assistant 訊息後面。
3. **tool_result 要排在 user content 的最前面**：如果這則 user 訊息除了 tool_result 還想夾一段文字，文字必須放在所有 tool_result **之後**（Ch 5 提過的格式要求）。

**你最好維持的不變量（API 不一定報錯，但守住更安全）：**

4. **角色交替** user / assistant / user…。要誠實說清楚：native 的 Anthropic Messages API 其實**允許**連續同角色的訊息（它會把相鄰同角色的內容合併處理），所以「連續兩個 user」在 native API 不一定直接 400。但在某些環境（Amazon Bedrock、舊版 SDK/adapter、各種 wrapper）會嚴格要求交替並回 `roles must alternate between "user" and "assistant"`。把「嚴格交替」當成你 harness 的不變量去維持，跨平台最不會出事。
5. **呼叫 `create()` 時，messages 結尾通常應是 user**，這樣模型知道「換我講了」。例外：你也可以讓結尾是 assistant 來做 **response prefill / 續寫**（給模型開個頭逼它接下去）——那是進階用法，本章的 chat loop 不用，預設以 user 結尾即可。

我們的 `chat()` 為什麼自動守這些？因為流程固定是「append user → 迴圈裡 append assistant →（要工具就 append user(tool_result)）」，天然交替、天然以 user 收尾。但當你開始**手動修改** messages（壓縮、刪除——Part 2 會做），就很容易破壞交替或 tool 配對，弄出非法結構。**記住上面這幾條，Part 2 改 messages 時才不會把它改壞。**

## Step 3：哪些東西該留在歷史裡？

現在 `self.messages` 會一直長大。一個自然的問題冒出來：**所有東西都該留嗎？**

先看歷史裡有哪些「住戶」，以及它們的去留價值：

| 住戶 | 例子 | 之後還需要嗎？ |
|---|---|---|
| 使用者的問題 | 「台北幾度」 | 通常要留——後續輪可能引用 |
| 模型的回答文字 | 「台北 28 度」 | 通常要留——對話連貫性 |
| 模型的 tool_use 請求 | 要求 `get_weather(Taipei)` | 必須跟它的 result 一起留或一起刪 |
| 工具結果 | 「晴, 28度」 | **常常是最大、最可丟的**——尤其大檔內容、長 JSON |
| 模型的中間思考 | 「我先查台北再比較」 | 任務完成後通常沒用 |

關鍵觀察：**工具結果往往是 messages 裡最肥的部分**。想像一個工具回傳了整個檔案的內容（幾千 token），這段在工具結果被模型用過、任務也推進之後，繼續原樣留在歷史裡，每一輪都要重送、重新計費。這就是 Ch 16（tool 結果裁剪）要處理的。

但**現在還不要急著刪**。這章的重點是讓你**看見**這個問題，建立「歷史不是只能無腦累積、是可以管理的」這個意識。真正的壓縮策略（摘要、丟棄、外移到 memory）是 Part 2 一整個 Part。過早優化、亂刪歷史，反而會破壞 Step 2 的結構規則或刪掉模型還需要的東西。

> **本章的立場**：先讓它正確地長大（守規則、不漏東西），再學怎麼科學地讓它瘦下來。順序不能反。

## Step 4：觀察歷史怎麼膨脹

把問題變得可見。給 `Agent` 加一個方法，印出目前歷史的規模：

```python
    def debug_size(self):
        n_msgs = len(self.messages)
        # 粗估 token：用字元數 / 4 當近似（精確算法 Ch 10 講）
        approx_chars = sum(len(str(m["content"])) for m in self.messages)
        print(f"訊息數: {n_msgs}, 內容約 {approx_chars} 字元 (~{approx_chars // 4} tokens)")
```

連續對話幾輪後呼叫 `agent.debug_size()`，你會看到訊息數和字元數**單調上升，從不下降**。每多問一句，下一次請求就更大、更貴、更慢。這不是 bug，是 messages 設計的必然——也正是為什麼 context 管理是一門獨立學問。

> 這裡的「字元數 / 4」只是**極粗略**的近似，方便你現在有個感覺。真正怎麼數 token、怎麼編列預算，是 Ch 10 的正題。先用這個髒估計建立「每輪都在變大」的體感即可。

## 失敗示範：手殘破壞角色交替

教材慣例，看一個壞的。假設你想「省點空間」，天真地把上一則 assistant 回答刪掉但留著它的 user 問題：

```python
# 反例！直接刪中間某則，破壞了交替
agent.messages.pop(-2)   # 假設刪掉一則 assistant，導致 user 後面直接又是 user
agent.chat("繼續")       # 下次 create() 時送出非法結構
```

後果分兩種。如果只是弄出「連續兩個 user」，在某些環境（Bedrock、wrapper）你會撞到：

```
anthropic.BadRequestError: messages: roles must alternate between "user" and "assistant"
```

而在 native Anthropic API 它可能不報錯、默默把兩則合併——但合併後的語意未必是你要的，一樣是 bug，只是更難發現。更明確、跨平台都會 400 的是另一種：如果你刪掉了一個 assistant 的 tool_use 卻留著對應的 tool_result，就變成「有 tool_result 卻找不到它要回應的 tool_use」，這個一定報錯。**這就是為什麼 Part 2 的壓縮不能亂刪，要嘛整段成對處理、要嘛換成摘要訊息來維持結構合法。** 先吃過這個虧，Part 2 學壓縮時你就知道在小心什麼。

## 踩雷集錦

1. **每次對話都 new 一個 Agent**：那就退回 Ch 4 的健忘症了。要連續對話，就重用同一個 `agent` 物件，讓 `self.messages` 累積。反過來，如果你**故意**要「開新對話」（清空記憶），就 new 一個、或加個 `reset()` 把 `self.messages` 清空。
2. **把 system prompt 塞進 messages**：有人寫 `messages=[{"role":"system",...}]`。native 的 Anthropic Messages API **沒有 system 角色在 messages 裡**，system 是獨立的 `system=` 參數，塞進 messages 會報錯或行為錯亂。（這跟 OpenAI Chat Completions 把 system 放 messages 的習慣不同，從 OpenAI 遷移過來的人最常踩。補充：Anthropic 另有一個 OpenAI 相容端點，那個會接受 OpenAI 風格的 system 訊息並自動搬到 system 參數——但本課用的是 native API，照搬 OpenAI 寫法會出事。）
3. **以為歷史會自動瘦身**：不會。messages 只增不減，除非你主動管理。沒有任何機制會幫你「自動忘掉舊的」——那正是 Part 2 要你親手做的事。
4. **手動刪歷史破壞交替或 tool 配對**：如上面的失敗示範。動 messages 一定要維持「user/assistant 交替」「tool_use/tool_result 成對」兩個不變量。
5. **多個使用者共用一個 Agent 物件**：如果你的服務同時服務多個使用者，每個使用者要有自己的 `Agent`（自己的 messages），不能共用一個——否則甲的對話會混進乙的歷史。會話隔離是基本的多租戶問題。

## 進階：再往深一層

- **messages 該存哪裡？** 現在 `self.messages` 活在記憶體裡，程式一關就沒了。真實產品會把它**持久化**（存資料庫、檔案），這樣使用者下次回來能接續對話。持久化還帶出「怎麼從中斷點恢復」的問題——這跟 Ch 39（resume / journaling）相關。本章先放記憶體，知道它需要被存起來即可。
- **不是所有歷史都要餵給模型**：你存起來的完整歷史（給人看、給稽核）和「這次請求實際送進模型的 messages」可以是**兩份東西**。產品常常存全量、但送給模型的是經過裁剪/摘要的版本。把「儲存」和「餵給模型的 context」分開想，是 Part 2 的重要心法——你會在 Ch 13、Ch 14 反覆用到。
- **system prompt 也算 context 成本**：system prompt 每一輪都會被送、被計費。它通常不變，所以是 prompt caching（Ch 17）的頭號受益者——把固定不變的 system + 工具定義快取起來，能省下大量重複計算。本章先知道「system 每輪都送」這個事實。

## 動手練習

1. 用 `Agent` 跑一段三輪對話，第二、三輪故意用「那個」「剛剛那個」這種需要上下文的指代詞，確認 agent 答得對。然後把第二句改成 `Agent()`（new 一個新的）再問，看它怎麼答不出來——對比體會「記憶 = 沒清空的 list」。
2. 每輪都呼叫 `debug_size()`，畫出（或心算）訊息數與字元數的成長曲線。特別觀察「有用到工具的那一輪」字元數跳多少——這讓你親眼看到工具結果是肥肉。
3. 故意 `agent.messages.pop()` 刪掉中間一則，再 `chat()`，重現那個 `roles must alternate` 的 400 錯誤。記住它，Part 2 改 messages 時你會慶幸先踩過。
4. 給 `Agent` 加一個 `reset()` 方法清空 `self.messages`，模擬「開新對話」按鈕。

## 本章重點整理

- agent 的記憶**就是** messages 這個 list，沒有別的地方；多輪對話＝讓這個 list 跨輪累積。
- 把 agent 收成 class，`self.messages` 變成跨輪存活的狀態，就能撐住連續對話與指代。
- messages 的結構分兩種規則：**API 硬限制**（第一則須 user、tool_use/tool_result 成對緊接、tool_result 排在 user content 最前）違反會 400；**該維持的不變量**（角色交替、結尾以 user 收尾）native API 可能容忍，但守住跨平台最安全。
- system prompt 走獨立的 `system=` 參數，**不**放進 messages。
- 歷史只增不減、工具結果常是最肥的部分——這就是 Part 2 context 管理的入口，但本章只「看見」問題，先別亂刪。

## 自我檢核

- [ ] 我能解釋為什麼「agent 的記憶 = messages 這個 list」，沒有隱藏的 session
- [ ] 不看程式碼，我能分辨 messages 哪些是「違反必 400 的硬規則」、哪些是「該維持但 API 可能容忍的不變量」
- [ ] 我知道 system prompt 為什麼不放在 messages 裡、放哪裡
- [ ] 我能指出歷史裡哪一類住戶通常最肥、最該在 Part 2 被處理
- [ ] 我能說出「儲存的完整歷史」和「送進模型的 context」為什麼可以是兩份東西

## 延伸閱讀

### 官方文件

- **[Anthropic — Messages API（messages 結構與 system 參數）](https://docs.anthropic.com/en/api/messages)**
  - **讀哪裡**：`messages` 陣列的角色規則、`system` 參數的說明。
  - **能學到什麼**：本章四條結構規則的權威來源；以及 system 為何是獨立參數而非 message。
  - **前提知識**：本章看完即可。

- **[Anthropic — Migrating from OpenAI（若你有 OpenAI 背景）](https://docs.anthropic.com/en/api/openai-sdk)**
  - **讀哪裡**：system 訊息處理方式的差異那段。
  - **能學到什麼**：為什麼 OpenAI 的「system 放 messages」習慣搬過來會踩雷（踩雷第 2 點），幫有 OpenAI 經驗的人校正直覺。
  - **前提知識**：用過 OpenAI API 者更有感；沒用過可跳過。

### 部落格 / 技術文章

- **[Effective context engineering for AI agents（Anthropic Engineering）](https://www.anthropic.com/engineering)** — Anthropic Engineering
  - **這篇說什麼**：為什麼「管理一份會無限增長的對話歷史」是 agent 的核心工程挑戰。本章讓你看見問題，這篇給出全局視角。
  - **讀哪裡**：在 Engineering 索引頁找 context engineering 該篇；先讀它論述「歷史增長與取捨」的部分。
  - **為什麼值得讀**：它正是 Part 2 的思想總綱，現在讀一遍，進 Part 2 會很順。

下一章我們處理 loop 的另一個關鍵問題：什麼時候該停？除了 `max_turns`，還有哪些停止條件、各種 `stop_reason` 該怎麼正確處理、以及怎麼避免 agent 陷入「反覆做同一件事」的迴圈。

→ [Ch 7 停止條件與 turn 控制](./07-stop-conditions-turns.md)
