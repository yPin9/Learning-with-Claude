# Ch 13 — Context 壓縮與摘要

> **目標**：把 Ch 12 選單裡的「策略二：摘要」實作出來。讀完你能寫一個 compaction 機制：在歷史逼近門檻時，把舊段落安全地濃縮成摘要、用摘要取代原文，且**不破壞 tool 配對與角色交替、不丟關鍵資訊**。你會理解摘要 prompt 怎麼寫、哪些該逐字保留、以及遞迴壓縮怎麼處理超長任務。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版），延續前面的 `Agent`、`count_tokens`（Ch 10）與 `analyze_history`（Ch 12）。

## 為什麼 compaction 是 agent 的主流做法

Ch 12 列了四種策略。為什麼摘要（compaction）是長對話、長任務最常用的那個？因為它在「保留資訊」和「縮小體積」之間取得最好的平衡：

- **截斷**太粗暴——直接丟掉舊訊息，可能丟掉之後還需要的東西。
- **外移 memory**（Ch 14）很強，但要設計「何時寫、何時撈」，較重。
- **摘要**剛好：它不是硬丟，而是**把一大段歷史濃縮成「夠用的精華」**——你失去了逐字細節，但保留了語意要點。對「對話越來越長、但早期細節大多用過了」這個最常見的情況，摘要的性價比最高。

這也是為什麼你在 Claude Code、各種 coding agent 裡會看到「conversation compacted」這種提示——它們跑長任務時，背後就是在做這章要教的事。

## 先建立直覺：compaction 是「把舊筆記謄寫成摘要」

想像你在做一個長專案，筆記本快寫滿了。你不會把舊筆記撕掉（資訊會丟），也不會一直換新本子（桌上攤不下）。你會做的是：**把前面幾十頁的筆記，謄寫成一頁精華摘要**，然後把那幾十頁收起來，桌上只留這頁摘要 + 最近還在用的幾頁。

```
   壓縮前（歷史很長）：
   [系統設定][使用者最初需求][回合1][大工具結果][回合2][大工具結果]...[回合8][最近的回合]
    └──────────────── 全部攤在桌上，撐爆 ────────────────┘

   壓縮後：
   [系統設定][📝 前 8 回合的摘要：使用者要做X，已完成A、B，發現C，目前卡在D][最近的回合]
    └─ 舊的濃縮成一段 ─┘                                      └─ 近的保留逐字 ─┘
```

關鍵設計：**近的保留、遠的摘要**。最近幾輪是模型當下推理的依據，要逐字保留；久遠的歷史用過了，濃縮成摘要即可。這就是 Ch 12 講的「半衰期」——近的半衰期還沒到、遠的早就過了。

## 一、compaction 的骨架

整個流程分四步，每一步都有要小心的地方：

```
   ① 觸發判斷：歷史 token 是否逼近門檻？（Ch 12 的觸發策略）
        │ 是
        ▼
   ② 切分：哪些要摘要（舊的）、哪些逐字保留（近的）
        │  ← 切點必須維持 tool_use/tool_result 配對！
        ▼
   ③ 摘要：把「要摘要的那段」交給模型濃縮成一則文字
        │
        ▼
   ④ 重組：用 [摘要訊息] + [保留的近段] 取代原本的長歷史
           ← 重組後必須仍是合法的 messages 結構
```

② 和 ④ 是最容易出錯的地方（Ch 12 的失敗示範就是死在這），所以下面實作時會特別處理。

## 二、實作：一個安全的 compaction 函式

先給完整實作，再逐塊解釋：

```python
from anthropic import Anthropic

client = Anthropic()
# 摘要是相對單純的任務，用更便宜更快的模型就夠（見本章進階）；
# 跟主 agent 用的模型（claude-opus-4-8）分開。
SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"

def _role(msg) -> str:
    return msg["role"] if isinstance(msg, dict) else msg.role

def find_safe_split(messages, keep_recent: int) -> int:
    """從「保留最近 keep_recent 則」往回找一個安全切點，
    確保切點不會把 tool_use 和它的 tool_result 拆散。
    回傳 index：messages[:idx] 要摘要，messages[idx:] 逐字保留。"""
    idx = max(0, len(messages) - keep_recent)
    # 若切點落在「assistant(含 tool_use)」與其 tool_result 之間，往前挪到 assistant 之前
    # 簡化判斷：若 messages[idx] 是帶 tool_result 的 user 訊息，代表它的 tool_use 在 idx-1，
    # 切點要包含那個 tool_use，所以往前移
    while idx > 0 and _starts_with_tool_result(messages[idx]):
        idx -= 1
    return idx

def _starts_with_tool_result(msg) -> bool:
    content = msg.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        ftype = first["type"] if isinstance(first, dict) else first.type
        return ftype == "tool_result"
    return False

def summarize(old_messages) -> str:
    """把一段舊歷史交給模型濃縮成摘要文字。"""
    # 把舊歷史攤平成可讀文字餵給摘要器（這裡簡化處理）
    transcript = _render_transcript(old_messages)
    resp = client.messages.create(
        model=SUMMARIZER_MODEL,           # 用便宜模型做摘要
        max_tokens=1024,
        system=(
            "你是一個對話壓縮器。把以下 agent 與使用者的對話歷史，濃縮成一段給「未來的自己」"
            "接手用的摘要。必須保留：使用者的原始目標與限制、已完成的步驟與其結果、"
            "已知的重要事實與決定、目前遇到的問題或待辦。可以省略：寒暄、已被推翻的嘗試、"
            "冗長的中間推理。用條列、精簡、具體（含關鍵數值/檔名/ID），不要客套。"
        ),
        messages=[{"role": "user", "content": f"以下是要壓縮的對話歷史：\n\n{transcript}"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")

def compact(messages, keep_recent: int = 6) -> list:
    """把舊歷史壓成摘要，回傳重組後的新 messages。"""
    split = find_safe_split(messages, keep_recent)
    if split <= 0:
        return messages   # 沒有足夠的舊歷史可壓，原樣返回

    old, recent = messages[:split], messages[split:]
    summary_text = summarize(old)

    # 用一則 user 訊息承載摘要，明確標示這是壓縮過的歷史
    summary_message = {
        "role": "user",
        "content": f"[以下是先前對話的壓縮摘要]\n{summary_text}\n[摘要結束，以下是最近的對話]",
    }

    # 重組：必須維持「從 user 開始、角色交替」。
    # summary_message 是 user，所以它後面那則「應該」是 assistant 才合法。
    rebuilt = [summary_message]
    if recent and _role(recent[0]) == "user":
        # recent 也從 user 開始 → 會變成連續兩個 user。
        # 插入一則 assistant「確認收到摘要」當作橋，維持交替。
        rebuilt.append({"role": "assistant", "content": "了解，我已根據以上摘要掌握目前進度，繼續處理。"})
    # 若 recent[0] 是 assistant，則 summary(user) + assistant 本來就交替，不必插橋。
    return rebuilt + recent
```

跑起來的效果：

```python
print("壓縮前:", analyze_history(agent.messages))
# {'user_text': 180, 'assistant_text': 920, 'tool_use': 240, 'tool_result': 13500}

agent.messages = compact(agent.messages, keep_recent=6)

print("壓縮後:", analyze_history(agent.messages))
# {'user_text': 600, 'assistant_text': 400, 'tool_use': 80, 'tool_result': 2200}
#   ↑ 摘要算在 user_text；那 13500 的 tool_result 大半被濃縮掉了
```

## 三、逐塊解釋：每個細節為什麼這樣處理

### `find_safe_split`：切點不能拆散 tool 配對

這是整個 compaction 最容易出錯、Ch 12 失敗示範死掉的地方。如果你天真地「保留最後 6 則、其餘摘要」，切點正好落在「assistant 發了 tool_use」和「user 回 tool_result」**之間**，那摘要段就含了一個沒有 result 的 tool_use、保留段含了一個沒有 tool_use 的孤兒 tool_result——下次請求直接 400（Ch 5）。

`find_safe_split` 的做法：若切點處的第一則是「帶 tool_result 的 user 訊息」，代表它配對的 tool_use 在前一則 assistant 裡。這時把切點 `idx` **往前挪**——注意往前挪會讓 `messages[:idx]`（摘要段）變短、`messages[idx:]`（保留的近段）變長，也就是**把配對的 assistant(tool_use) + user(tool_result) 兩則一起留在近段**，不讓它們被拆到兩邊。**永遠不要在 tool_use 和它的 tool_result 中間切。**

> 這裡的判斷是簡化版，且**有一個前提**：它假設 `tool_result` 一定排在 user 訊息 content 的開頭（這是 Ch 5 的格式規則）。如果你的歷史可能含不符這個規則的訊息，嚴謹的實作應該先驗證、不合規就明確報錯，而不是讓這個安全檢查被靜默繞過。真實實作還可能要處理更複雜的情況（一輪多工具、混合內容），但原則不變：**切點必須落在「完整回合的邊界」上**，不能切進一個 tool 往返的中間。

### 摘要 prompt：明確「保留什麼、省略什麼」

`summarize` 的 system prompt 是這章的靈魂。一個爛的摘要 prompt（「請摘要以下對話」）會得到一段漂亮但沒用的概述——它可能省掉了關鍵的檔名、數值、待辦，保留了一堆寒暄。好的摘要 prompt 要**明確指定保留清單**：

- **必須保留**：使用者的原始目標與限制、已完成步驟及其結果、重要事實與決定、當前的問題/待辦。
- **可以省略**：寒暄、已被推翻的嘗試、冗長的中間推理。
- **格式要求**：條列、具體（含關鍵數值/檔名/ID）。

為什麼這樣設計？因為摘要的目的是讓「未來的模型」能無縫接手——它需要的是**任務狀態**（我在做什麼、做到哪、卡在哪），不是對話的文學概述。把摘要 prompt 寫成「給接手者的交接筆記」，比寫成「請摘要」好太多。

### 摘要訊息的包裝：明確標示邊界

重組時，我們把摘要包成一則 user 訊息，前後加上 `[以下是先前對話的壓縮摘要]` / `[摘要結束]` 標記。要釐清：**這些標記不是 API 結構上的要求**（沒有它請求照樣合法），它是**語意與安全層面**的措施——讓模型清楚「這段是 harness 塞進來的、濃縮過的歷史，不是使用者剛剛下的新指令」。少了標記，模型可能把摘要誤當成使用者的最新命令，甚至若摘要裡夾帶了來自工具結果的可疑內容，更可能被當指令執行（這關聯到 Ch 36 的 prompt injection）。所以邊界標記是「便宜但值得」的防呆。

### 維持合法結構（這就是上面那段重組程式碼在做的事）

摘要訊息是 user 角色，所以它後面那則必須是 assistant 才能維持交替（Ch 6）。問題是：`recent[0]` 不一定是 assistant——它也可能是一個普通的 user 訊息（某輪使用者的提問）。如果直接 `[摘要(user)] + [recent(user 開頭)]`，就會出現連續兩個 user。

上面 `compact` 的重組邏輯就是在處理這件事：**若 `recent[0]` 是 user，就在摘要和 recent 之間插一則 assistant「確認收到摘要」當橋**，讓序列變成 user(摘要) → assistant(確認) → user(recent…)，交替合法；若 `recent[0]` 本來就是 assistant，則 user(摘要) → assistant(recent…) 本來就合法，不插橋。這樣兩種情況都安全。**這正是 Ch 12 失敗示範要你避免的坑——重組後一定要回頭用 Ch 6 的規則驗證，別假設「接上去就好」。**

## 四、遞迴壓縮：當摘要本身也變長

長到極致的任務（跑幾百輪）會遇到：壓縮過幾次後，連「摘要 + 近段」都又逼近門檻了。怎麼辦？**遞迴壓縮**——把「舊摘要 + 之後新累積的歷史」再壓一次，產生一個更高層的摘要：

```
   第一次壓縮：[摘要1] + [近段]
   ...又跑很多輪...
   第二次壓縮：把 [摘要1 + 中間累積的歷史] 再壓 → [摘要2] + [新近段]
```

要小心**資訊的逐層流失**：每壓一次，早期的細節就更模糊一層。所以遞迴壓縮的摘要 prompt 要特別強調「保留已確立的關鍵事實與目標」，避免「目標」這種最該記住的東西在多層摘要後被稀釋掉。這也是為什麼**真正重要、不能丟的東西，更適合用 memory 外移（Ch 14）**而不是反覆塞進會被一再壓縮的對話歷史——這是 Ch 13 和 Ch 14 的分工。

## 五、量測：壓縮到底有沒有用、有沒有壞

壓縮是有風險的操作（可能丟資訊），所以**一定要量測**：

1. **體積**：壓縮前後用 `analyze_history` / `count_tokens` 比較 token 數，確認真的縮小了（且縮在對的地方——tool_result 那塊）。
2. **正確性**：壓縮後丟一個「需要早期資訊才能答對」的問題給 agent，看它還答不答得出來。例如「我最開始的需求是什麼？」「你剛剛已經完成了哪幾步？」——如果摘要做得好，這些它都該答得出。
3. **結構合法性**：壓縮後立刻送一次請求，確認沒有 400（沒拆散 tool 配對、沒破壞交替）。

> 這三項量測，本質上就是 Ch 34（eval）的雛形——你在用測試案例驗證一個 harness 行為的正確性。compaction 是少數「做錯了會靜默損害品質」的功能（agent 不會報錯，只是默默變笨），所以它特別需要被測。

## 失敗示範：摘要 prompt 太籠統，丟了關鍵資訊

看一個「壓縮成功縮小了體積、卻悄悄害慘 agent」的例子。摘要 prompt 寫得很隨便：

```python
# 反例！太籠統的摘要 prompt
system="請簡短摘要以下對話。"
```

模型很聽話地產出一段流暢的概述：

```
使用者請助理協助分析一個系統問題，助理進行了若干調查並提供了建議。
```

讀起來沒問題，但**致命**：它把「使用者要分析的是哪個檔」「已經發現的 3 個錯誤具體是什麼」「目前卡在第 2 個的什麼地方」全都抽象掉了。壓縮後 agent 接著跑，發現自己「忘了」關鍵細節，開始重複問已經查過的東西、或基於模糊記憶亂答。

**體積是縮小了，但 agent 變笨了，而且不會報錯。** 這就是為什麼摘要 prompt 要明確列出「必須保留具體的數值/檔名/ID/待辦」，以及為什麼壓縮一定要做正確性量測。籠統的摘要是 compaction 最隱蔽的陷阱。

## 踩雷集錦

1. **切點拆散 tool 配對**：在 tool_use 和 tool_result 之間切，產生孤兒 tool_result → 400。一定要用 `find_safe_split` 把切點挪到回合邊界。
2. **摘要 prompt 太籠統**：「請摘要」會得到漂亮但抽象的概述，丟掉關鍵的數值/檔名/待辦。要明確列保留清單，把摘要當「交接筆記」寫。
3. **不標示摘要邊界**：摘要訊息沒有 `[摘要開始/結束]` 標記，模型可能把它誤當成使用者最新指令。
4. **壓縮後不量測**：compaction 做錯不會報錯，只會讓 agent 默默變笨。一定要測體積、正確性、結構合法性三項。
5. **重組後破壞角色交替**：摘要 user 訊息後緊接著又是 user，連成兩個 user。重組後要用 Ch 6 規則驗證。
6. **把絕對不能丟的東西只交給會被反覆壓縮的歷史**：多層遞迴壓縮會逐漸稀釋細節。真正關鍵、跨整個任務的東西該外移到 memory（Ch 14），別賭它在第五次摘要後還活著。
7. **壓得太頻繁**：每次壓縮要花一次 API 呼叫、又破壞快取（Ch 12）。用門檻觸發、留緩衝，別一有點長就壓。

## 進階：再往深一層

- **誰來做摘要？用便宜的模型**：摘要是個相對單純的任務，常常用一個**更便宜、更快的模型**（例如 Haiku 級）來做就夠，不必動用跑主任務的 Opus。這能顯著降低 compaction 的成本。這呼應 Ch 37——把不同難度的子任務分派給不同價位的模型。
- **結構化摘要 vs 自由文字摘要**：上面是自由文字摘要。更進階的做法是讓摘要器**輸出結構化的狀態**（例如一個 JSON：`{goal, completed_steps, known_facts, open_questions}`），用 Ch 32 的 structured output 強制格式。結構化摘要更穩定、更不容易漏掉某個欄位，也更好讓後續邏輯使用。代價是彈性低一點。
- **保留「錨點」逐字**：有些東西即使在摘要段，也值得逐字保留而非濃縮——例如使用者最初那句精確的需求、或一個關鍵的錯誤訊息原文。可以設計成「摘要中間過程，但把幾個錨點原文附在摘要後」。這是 compaction 的精細調校，視任務而定。
- **Anthropic 也有 API 層的 context 管理機制（但範圍不同）**：除了自己實作摘要式壓縮，Anthropic 提供了 beta 的 **context editing** 能力——但要分清楚它做的是**清除**（clearing）而非**摘要替換**：它能自動清掉舊的 server-side tool result / tool use、或舊的 thinking block，把空間騰出來。這跟本章「呼叫模型把舊歷史濃縮成一段摘要」是**不同的手段**——一個是直接清除、一個是語意濃縮。兩者可以互補。自己刻過摘要式壓縮（像本章）之後，你會更懂內建的 clearing 在幫你做什麼、何時夠用、何時還是得自己做摘要。Ch 40 對比時會回到這點。

## 動手練習

1. 把 `compact` 接進你的 mini-agent：在 `chat()` 開頭檢查歷史 token 是否超過門檻（例如 window 的 70%），超過就先 `compact` 再繼續。跑一個長任務，觀察「conversation compacted」發生時體積的變化。
2. 故意用籠統的摘要 prompt（「請摘要」），跑一個有具體數值/檔名的任務，壓縮後問 agent「我最初要你處理的檔名是什麼」，看它答不答得出來。再換成明確列保留清單的 prompt，對比差異——親身體會失敗示範。
3. 寫一段會「在 tool_use 和 tool_result 中間切」的錯誤切分，重現那個 400，再換成 `find_safe_split`，確認問題消失。
4. （進階）把摘要器改用結構化輸出（先看 Ch 32），讓它吐 `{goal, done, facts, todo}` 的 JSON，比較它和自由文字摘要哪個更不容易漏資訊。
5. （進階）把摘要器的 model 換成更便宜的 Haiku 級模型，比較摘要品質與成本——體會「用對價位的模型做對的事」。

## 本章重點整理

- compaction（摘要壓縮）是長對話/長任務最主流的 context 管理策略：在「保留語意」和「縮小體積」間取得最佳平衡。
- 核心設計是「近的逐字保留、遠的濃縮成摘要」，對應 Ch 12 的半衰期概念。
- 四步驟：觸發 → 安全切分（**切點不能拆散 tool 配對**）→ 摘要 → 重組（**維持合法結構**）。
- 摘要 prompt 是靈魂：明確列「必保留（目標/已完成/事實/待辦/具體數值）vs 可省略」，把它當「交接筆記」寫，別只說「請摘要」。
- 一定要量測：體積、正確性（早期資訊還答得出嗎）、結構合法性——compaction 做錯會靜默變笨、不報錯。
- 進階：用便宜模型做摘要、結構化摘要、保留錨點逐字、真正關鍵的東西外移 memory 而非賭它在多層壓縮後還在。

## 自我檢核

- [ ] 我能說出 compaction 的四步驟，以及哪兩步最容易出錯、為什麼
- [ ] 我能解釋 `find_safe_split` 在防什麼，以及切點切錯會怎樣
- [ ] 我能寫出一個「交接筆記」式的摘要 prompt，並說出它和「請摘要」的差別
- [ ] 我知道 compaction 一定要量測哪三件事，以及為什麼（會靜默變笨）
- [ ] 我能說出為什麼真正關鍵的資訊該外移 memory，而不是賭它在多層摘要後還活著

## 延伸閱讀

### 部落格 / 技術文章

- **[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)** — Anthropic Engineering
  - **這篇說什麼**：把 compaction（壓縮）列為長時程 agent 的核心技術之一，並討論「保留什麼、丟什麼」的原則——本章摘要 prompt 的設計思路與此一致。
  - **讀哪裡**：談 compaction / summarization 與長任務 context 管理的段落。
  - **為什麼值得讀**：本章是「怎麼做」，這篇給「為什麼這樣做、邊界在哪」的權威論述。

- **[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)** — Anthropic Engineering
  - **這篇說什麼**：真實長時程系統如何在跑很久的任務中壓縮 context、並把關鍵成果寫到外部，避免資訊在壓縮中流失。
  - **讀哪裡**：談 context 壓縮與「把成果寫出去」的段落。
  - **為什麼值得讀**：印證本章「關鍵資訊外移而非反覆壓縮」的主張，且來自上線系統的實戰。

### 官方文件

- **[Anthropic — Context editing（beta）](https://docs.anthropic.com/en/docs/build-with-claude/context-editing)**
  - **讀哪裡**：它能清除哪些東西（舊的 tool result / tool use、thinking block）、以及觸發條件。
  - **能學到什麼**：API 層內建的 context 管理是「**清除**」而非「摘要替換」——對照你本章手刻的摘要式壓縮，理解兩者的分工：何時用內建 clearing 就夠、何時還得自己做語意摘要。
  - **前提知識**：本章看完即可；細節 Ch 17、Ch 40 會再碰。

下一章我們做 Ch 12 選單裡的「策略三：外移到 memory」——讓 agent 把真正該長期記住的東西寫到 context 之外，需要時再撈回來，從而既保持 context 輕、又不真的遺忘關鍵資訊。

→ [Ch 14 Memory：短期 vs 長期](./14-memory.md)
