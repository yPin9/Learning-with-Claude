# Ch 2 — 解剖一次 agent 執行

> **目標**：把 Ch 1 的「塞紙」比喻變成真的。我們**手動**走完一次含工具的完整往返：送請求 → 模型要求用工具 → 我們執行 → 把結果塞回去 → 模型給最終答案。全程把每個 API 欄位攤開看，你會親眼確認「執行是我們做的，不是模型」。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續 Ch 0 的 `client = Anthropic()` 設定。本章範例不需要任何外部套件，工具我們用一個假的本地函式假裝。

## 為什麼要先手動走一遍

Ch 4 我們就會把這整套包成一個自動迴圈。但如果你還沒親手、一步一步、看著每個欄位走完一次，那個迴圈對你就是黑盒。**這章我們刻意「不寫迴圈」**，用最笨的方式一行一行手動推，目的是讓你對「一輪到底交換了什麼」有徹底的體感。等你受不了這種手動的繁瑣時，你就完全理解 Ch 4 的迴圈在自動化什麼了——這正是我們要的。

## 先建立直覺：一次 agent 執行是一場「回合制對話」

把它想成跟模型玩回合制遊戲。每一回合，你遞出**目前為止的完整劇本**（messages 陣列），模型讀完，回你一段新台詞。它的台詞分兩種結局：

```
   你遞出 messages（劇本）＋ tools（你能幫它做的事清單）
                    │
                    ▼
              模型讀完，回應
                    │
        ┌───────────┴───────────┐
        ▼                        ▼
  stop_reason = "end_turn"   stop_reason = "tool_use"
  「我講完了，這是答案」      「我需要你幫我做件事，
        │                     做完告訴我結果」
        ▼                        │
      結束                       ▼
                          你執行工具，把結果接到劇本後面，
                          再遞一次（進入下一回合）
```

整個 agent 執行，就是這個回合制反覆進行，直到模型說 `end_turn`。**`stop_reason` 是這場遊戲的紅綠燈**——它告訴 harness 「該收工」還是「該去幹活再回來」。Ch 0 我們已經見過 `end_turn`，這章要見的是 `tool_use`。

## Step 1：先給模型一個它能用的工具

模型不會憑空知道它能做什麼。你得在請求裡附上一份 **工具清單**（`tools` 參數），每個工具是一段 JSON schema，描述「這工具叫什麼、做什麼、要哪些參數」。先定義一個極簡單的工具：查某城市的天氣。

```python
from anthropic import Anthropic

client = Anthropic()

tools = [
    {
        "name": "get_weather",
        "description": "查詢指定城市目前的天氣。回傳攝氏溫度與天氣狀況。",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名稱，例如 'Taipei'、'Tokyo'",
                }
            },
            "required": ["city"],
        },
    }
]
```

這份 schema 之後 Ch 18、Ch 19 會整整兩章講「怎麼設計才好」。現在你只要知道：**這就是模型看到的「能力說明書」**。模型靠 `description` 和參數說明來判斷「這個工具能不能幫我達成任務、該傳什麼參數」。

注意一個關鍵事實：**到目前為止，`get_weather` 還只是一段文字描述，我們根本還沒寫它的實作**。模型不需要實作就能「要求」用它——因為要求只是寫字。實作是 harness 的事，等模型真的要求時我們才寫（Step 4）。這正是 Ch 1 那條線的具體展現。

## Step 2：送出第一次請求，看模型「要求用工具」

```python
messages = [
    {"role": "user", "content": "台北現在天氣怎麼樣？"}
]

resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=1024,
    tools=tools,            # ← 把能力說明書一起送進去
    messages=messages,
)

print("stop_reason:", resp.stop_reason)
for block in resp.content:
    print("---", block.type, "---")
    print(block)
```

如果模型決定要用工具，你會看到 `stop_reason` 變成 `tool_use`，而 `content` 裡出現一個 `tool_use` 區塊：

```
stop_reason: tool_use
--- text ---
TextBlock(text='我來幫你查台北的天氣。', type='text')
--- tool_use ---
ToolUseBlock(id='toolu_01A2b3...', input={'city': 'Taipei'}, name='get_weather', type='tool_use')
```

停下來仔細看這個回應，它印證了 Ch 1 講的每一件事：

- **`content` 真的是 list，而且這次有兩個 block**：一個 `text`（模型講給人聽的話）、一個 `tool_use`（結構化的工具請求）。這就是 Ch 0 為什麼說「不要假設 `content[0]` 一定是文字」——這裡 `content[0]` 是 text，但 `content[1]` 是 tool_use，硬取 `content[1].text` 會炸。
- **`tool_use` 區塊裡的三個欄位**最關鍵：
  - `name`：模型想用哪個工具（`get_weather`）。
  - `input`：模型決定要傳的參數（`{'city': 'Taipei'}`）。注意這是模型**自己從「台北」推斷出該傳 `Taipei`** 的——這就是模型提供的「智能」。
  - `id`：這次工具呼叫的唯一識別碼（`toolu_...`）。**先記住這個 id，Step 5 把結果送回去時必須用它對上號**，否則模型不知道這個結果對應哪次呼叫。
- **模型沒有執行任何東西**。它沒有去查天氣——它根本不會查。它只是輸出了「請你用 `Taipei` 去呼叫 `get_weather`」這個請求，然後停下來等你（`stop_reason: tool_use` 就是「我停在這，等你回我工具結果」）。

## Step 3：把模型的回應接回劇本

回合制遊戲的規矩：你遞出的劇本必須包含**完整的來龍去脈**。模型無狀態（Ch 1），所以下一回合你得把「模型剛剛說了什麼」也一起遞回去，否則它會忘記自己要求過工具。

做法是把整個 assistant 回應**原封不動**接到 `messages` 後面：

```python
# 把模型這一輪的完整回應（含 text + tool_use）接進歷史
messages.append({"role": "assistant", "content": resp.content})
```

`resp.content` 是那個 block list，直接塞進去即可。SDK 接受這種「把回應物件的 content 放回 messages」的寫法。**這一步是 Ch 6（訊息歷史管理）的雛形**：agent 的「記憶」就是這樣一輪一輪手動累積出來的。

## Step 4：harness 真的去執行工具

現在輪到我們幹活。模型要求用 `Taipei` 呼叫 `get_weather`，所以我們**現在才**寫這個工具的實作（真實世界這裡會去打天氣 API，這裡用假資料示範）：

```python
def get_weather(city: str) -> str:
    # 真實情況這裡會去呼叫某個天氣服務的 API。
    # 為了範例可重現，我們回傳寫死的假資料。
    fake_db = {
        "Taipei": "晴，攝氏 28 度",
        "Tokyo": "多雲，攝氏 22 度",
    }
    return fake_db.get(city, f"查無 {city} 的天氣資料")

# 先確認模型這一輪真的要求了工具——否則 content 裡根本沒有 tool_use 區塊
assert resp.stop_reason == "tool_use", "模型沒有要求用工具，這段不該執行"

# 從模型的請求裡取出它要呼叫的工具與參數
tool_use_block = next(b for b in resp.content if b.type == "tool_use")
result_text = get_weather(**tool_use_block.input)   # 等同 get_weather(city="Taipei")
print("工具執行結果:", result_text)
```

```
工具執行結果: 晴，攝氏 28 度
```

這幾行就是 harness 的心臟：**「看模型要求什麼 → 對照到一個真的函式 → 用模型給的參數呼叫它 → 拿到結果」**。Ch 5 會把「怎麼從 name 對應到正確的函式」做得更嚴謹（現在我們偷懶寫死成 `get_weather`），但機制就是這樣。

那行 `assert` 不是裝飾：如果模型這一輪選擇直接回答而沒要求工具（`content` 裡沒有任何 `tool_use` 區塊），`next(...)` 會丟 `StopIteration`。先擋掉，你才不會在「模型有時用工具、有時不用」的真實情況下被這個 edge case 咬到。Ch 4 的迴圈會用更正規的 `if/else` 取代這個 assert。

## Step 5：把工具結果塞回去，遞出下一回合

模型在等結果。我們要送一則**新的 user 訊息**，內容是一個 `tool_result` 區塊，並用 Step 2 記下的那個 `id` 對上號：

```python
messages.append({
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_block.id,   # ← 必須對上 Step 2 的那個 id
            "content": result_text,
        }
    ],
})

# 遞出下一回合
resp2 = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=1024,
    tools=tools,
    messages=messages,
)

print("stop_reason:", resp2.stop_reason)
print(resp2.content[0].text)
```

這一次模型拿到了天氣資料，通常就不再需要工具，於是給出最終答案：

```
stop_reason: end_turn
台北現在是晴天，氣溫攝氏 28 度，是個舒服的好天氣。
```

順利的話 `stop_reason` 回到 `end_turn`——紅綠燈轉綠，這一輪 agent 執行結束。

要強調「通常」「順利的話」：拿到工具結果後，模型**不保證**一定 `end_turn`。它可能還想再要一個工具（又是 `tool_use`，於是再轉一回合）、可能因為輸出太長被 `max_tokens` 截斷、也可能出現 `refusal` 之類的其他結局。所以正規的 harness 不會假設「執行完工具就結束」，而是**每一回合都重新看 `stop_reason` 來決定下一步**——這正是 Ch 4 迴圈、Ch 7 停止條件要處理的事。本章為了把流程看清楚才走 happy path。

> **為什麼 `tool_result` 是放在 `user` 角色裡？** 直覺上你可能覺得工具結果「不是使用者講的」，怎麼算 user？這是 Messages API 的設計約定：對話只有 user / assistant 兩種角色輪流，而「環境回饋給模型的東西」（包含工具結果）一律歸在 user 這一側。把它想成：assistant 提出請求，user 這一側（你的 harness 代表外部世界）回覆結果。記住這個約定就好，Ch 5 會再強調。

## 把整段串起來：一次執行的全貌

剛剛那五步，攤平了就是一次 agent 執行的最小完整形態。用一張圖收尾：

```
messages = [user: "台北天氣?"]
        │  create(messages, tools)
        ▼
   resp: [text, tool_use(get_weather, Taipei)]   stop_reason=tool_use
        │  ① append assistant 回應到 messages
        │  ② harness 執行 get_weather("Taipei") → "晴, 28度"
        │  ③ append tool_result(對上 id) 到 messages
        ▼
messages = [user, assistant(tool_use), user(tool_result)]
        │  create(messages, tools)  ← 同一份 messages，變長了
        ▼
   resp2: [text "台北晴, 28度..."]               stop_reason=end_turn
        ▼
      結束，把答案給使用者
```

關鍵觀察：**每一回合送出去的 `messages` 都比上一回合長**（多了 assistant 的請求、多了 tool_result）。模型靠這份不斷增長的劇本維持「記憶」。這個「越長越貴、總有一天塞不下」的問題，就是 Part 2 整個 Part 的主題。現在先讓它長著。

## 踩雷集錦

1. **「模型回了 `tool_use` 我就直接讀 `content[0].text`」**：當 `stop_reason` 是 `tool_use` 時，`content` 裡通常**同時有** text 和 tool_use 區塊，順序也不保證。要用 `block.type` 判斷，不能靠索引位置。硬取錯的 block 會 `AttributeError`。
2. **忘了把 assistant 回應接回 messages**：很多人 Step 3 跳過，直接送 tool_result。結果模型的劇本裡只有「使用者問題」和「工具結果」，中間「我要求用工具」那段不見了，API 會報錯（tool_result 找不到對應的 tool_use）或模型行為錯亂。**tool_use 和 tool_result 必須成對出現在歷史裡**。
3. **`tool_use_id` 對錯或漏填**：`tool_result` 的 `tool_use_id` 必須精確等於模型那個 `tool_use` 區塊的 `id`。填錯、填空、或自己亂編一個，API 會拒收。它是「這個結果回應哪次呼叫」的唯一線索。
4. **後續請求忘了再帶 `tools`**：Step 5 的第二次 `create` 仍然要帶 `tools=tools`。工具清單不是「設定一次就記住」，它是每次請求的一部分。漏帶，模型在這一回合就「看不到」那些工具了。
5. **一次回應可能要求「多個」工具**：模型可以在一個回應裡放**好幾個** `tool_use` 區塊（要求平行做多件事）。本章為了簡單只示範一個，但 Step 4 的 `next(...)` 只抓第一個是會漏的。Ch 5 會處理「一輪多工具」的正確做法——現在先知道有這回事。

## 進階：再往深一層

- **把送出去的 payload 印出來看**：在 `create()` 之前，先 `import json; print(json.dumps(messages, default=str, ensure_ascii=False, indent=2))`。你會看到那份「劇本」原原本本的 JSON 結構——user/assistant 角色如何交錯、tool_use 和 tool_result 如何用 id 串起來。這比 Ch 0 提到的 `ANTHROPIC_LOG` 更精準，因為你看的就是你親手組的那份資料。養成「不確定就把 messages 印出來」的習慣，debug agent 時這招能救你無數次。
- **token 在每一回合怎麼長**：在每次 `resp` 後印 `resp.usage`。你會發現第二次請求的 `input_tokens` 明顯比第一次大——因為劇本變長了。手動感受這個增長，Part 2 講 context 預算時你就有了實感而不是空談。
- **模型也可能「不用工具直接答」**：把問題換成「1 加 1 等於多少？」，模型大概率直接 `end_turn` 回答，根本不碰 `get_weather`。「要不要用工具」是模型每一輪自己判斷的——這個判斷品質，深受工具 `description` 寫得好不好影響（Ch 19）。

## 動手練習

1. 把 Step 1–5 的程式碼接起來跑一次，確認你看到 `tool_use` → 執行 → `tool_result` → `end_turn` 的完整流程。
2. 把問題改成「台北和東京哪裡比較熱？」，觀察模型會不會要求**呼叫兩次** `get_weather`（一次台北、一次東京）。如果它在同一個回應裡放了兩個 tool_use 區塊，你 Step 4 的 `next(...)` 只處理第一個——親眼看看會發生什麼，這就是踩雷第 5 點。

   **這裡要特別記住正確的回覆格式**：當一個回應含多個 tool_use 區塊時，正確做法**不是**送多則 user 訊息、一則裝一個結果，而是送**一則** user 訊息、它的 `content` 陣列裡放**所有**的 `tool_result`（每個各自對上自己的 `tool_use_id`）。本章 Step 5 只示範了單一 tool_result 的形狀，那是單工具情況；多工具的正確組裝留到 Ch 5 完整處理。試的時候別把它當成多工具的範本。
3. 故意把 Step 5 的 `tool_use_id` 改成 `"toolu_wrong"`，跑跑看 API 怎麼罵你。記住那個錯誤訊息。

## 本章重點整理

- 一次 agent 執行是回合制：每回合遞出**完整且不斷增長**的 messages，模型用 `stop_reason` 告訴 harness 「結束」(`end_turn`) 還是「要工具」(`tool_use`)。
- 模型只「要求」工具（輸出一個 `tool_use` 區塊，含 name / input / id），執行永遠是 harness 做的。
- 一個完整往返 = 送請求 → 接住 assistant 回應 → 執行工具 → 用對上 id 的 `tool_result` 回覆 → 再送請求。
- tool_use 與 tool_result 必須成對、用 `id` 串連，且工具結果掛在 `user` 角色下。

## 自我檢核

- [ ] 我能說出 `stop_reason` 的 `tool_use` 和 `end_turn` 各自要 harness 做什麼
- [ ] 不看程式碼，我能描述一次含工具的往返有哪幾步、誰做哪步
- [ ] 我知道 `tool_use_id` 是幹嘛的，以及填錯會怎樣
- [ ] 我能解釋為什麼「每一回合 messages 都變長」，以及這預告了 Part 2 的什麼問題

## 延伸閱讀

### 官方文件

- **[Anthropic — Tool use overview](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)**
  - **讀哪裡**：「How tool use works」整段，對照本章 Step 1–5 逐步看；特別看它對 `tool_use` / `tool_result` 區塊的欄位定義。
  - **能學到什麼**：本章手動走的流程，官方版本怎麼描述；以及 `tool_choice`、平行工具呼叫等本章還沒展開的細節。
  - **前提知識**：本章看完即可，這份文件 Ch 5 會整個吃透。

- **[Anthropic — Messages API reference（content blocks）](https://docs.anthropic.com/en/api/messages)**
  - **讀哪裡**：request body 的 `messages` 結構，以及 `content` 可以是字串或 block 陣列這兩種形式。
  - **能學到什麼**：印證本章「assistant 回應的 content 可直接塞回 messages」的寫法為什麼合法，以及 user content 何時要用 block 陣列（放 tool_result 時）。
  - **前提知識**：本章 Step 3、Step 5 看完即可。

下一章我們把前三章的觀察收斂成一個可以反覆套用的心智模型——harness = loop + context + tools + policy——然後 Part 1 就開始把「loop」這塊真的寫成程式。

→ [Ch 3 心智模型：loop + context + tools + policy](./03-mental-model.md)
