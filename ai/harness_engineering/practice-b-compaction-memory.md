# 練習 B — 給 agent 加上 context 壓縮 + memory

> **目標**：把 Part 2 的兩大支柱——**壓縮（Ch 13）**和 **memory（Ch 14）**——實際裝到練習 A 的 mini-agent 上，讓它能在「長到撐爆 context」的對話裡活下來，而且**該記住的關鍵事實不會在壓縮中被弄丟**。完成後你會親手驗證 Ch 14 那句核心主張：「壓縮前先寫 memory」是黃金搭檔。

## 背景與動機

練習 A 的 mini-agent 有個你還沒撞到的致命缺陷：`self.messages` **只增不減**。聊得夠久、或工具回了幾個大結果，它遲早會逼近 context window，然後請求開始變慢、變貴，最後直接 `model_context_window_exceeded` 撐爆（Ch 7、Ch 10）。

Part 2 教了怎麼治：Ch 13 的壓縮把舊歷史濃縮成摘要、Ch 14 的 memory 把關鍵事實外移到對話之外。但你到目前為止是「分開看這兩塊」。這個練習要你把它們**同時**裝到同一個 agent 上，並親手製造一個情境，逼出「為什麼這兩個非得搭配使用不可」：

- 只有壓縮、沒有 memory → 跑久了，早期的關鍵事實（使用者最初的需求、確認過的結論）會在多層摘要後被稀釋、甚至消失。agent 默默變笨。
- 只有 memory、沒有壓縮 → 對話本身還是會無限長大、撐爆 context。
- **兩個一起，且「壓縮前先把關鍵事實寫進 memory」** → 對話保持輕量、關鍵事實安全外存。這才是 production agent 的長對話生存方案。

這跟你在 Claude Code 跑長任務時看到的「conversation compacted」提示、以及它的自動記憶機制，是**同一類概念**（這裡講的是公開的設計範式，不是宣稱它內部就是這幾行）。做完這題，你對那行提示底下大概在發生什麼，會有第一手的直覺。

## 任務規格

在練習 A 的 `mini_agent.py` 基礎上擴充（沿用它的 `Agent`、`run_tool_uses`、工具註冊表、`stop_reason` 分流、API 錯誤處理），新增以下能力：

**memory（Ch 14）**
- 新增兩個工具 `read_memory()` 與 `write_memory(content: str)`，用一個本地 markdown 檔（如 `agent_memory.md`）做持久化。
- 在 system prompt 給**明確的記憶準則**：只記「相對穩定、未來會用」的（使用者長期偏好、跨任務關鍵事實、已確認的結論），不記一次性的寒暄與閒聊。
- 在每段對話/session **開始時讀一次** memory 進 context（建立背景）。
- 跨 session 驗證：重啟程式後，agent 仍記得上一段對話寫進 memory 的偏好。

**壓縮（Ch 13）**
- 把 `count_tokens`（Ch 10）、`find_safe_split`、`summarize`、`compact`（Ch 13）接進來。
- 在 `chat()` 每輪呼叫模型**之前**，檢查歷史 token 是否超過門檻（例如 context window 的某個百分比，或一個你設的絕對值），超過就先 `compact` 再繼續。
- 摘要器用**便宜模型**（`claude-haiku-4-5-20251001`），跟主 agent 的模型分開。
- 壓縮必須**安全**：不拆散 tool_use/tool_result 配對、重組後維持角色交替（用 Ch 13 的 `find_safe_split` + 橋接邏輯）。

**兩者的協同（本練習的靈魂）**
- 實作「**壓縮前先寫 memory**」：在觸發 `compact` **之前**，讓 agent 有機會把「即將被壓縮掉的那段歷史」裡的關鍵事實先 `write_memory`。這樣壓縮可以放心濃縮對話，因為精華已經安全地存在 memory 外部了。

**禁止**
- 不准用框架代勞壓縮/記憶（LangChain memory、各種 vector store SDK 等）。這兩塊要你自己刻——正是練習重點。
- 壓縮切點不准拆散 tool 配對（會 400）。
- memory 準則不准寫成「把有用的都記下來」（會變垃圾場，Ch 14 失敗示範）。

**可選加分**
- 結構化 memory（分類：偏好/事實/結論），`read_memory` 支援只讀某一類。
- 壓縮前後印 `analyze_history`（Ch 12），把體積變化可視化。
- 接上 prompt caching（Ch 17），並觀察「壓縮發生時 `cache_read_input_tokens` 掉下來」這個 Part 2 反覆強調的張力。

## 期望輸出範例

關鍵是看「長對話 → 觸發壓縮 → 早期事實仍記得」這條主線：

```
$ python mini_agent_b.py
mini-agent (B) 已啟動（壓縮 + memory 已開）

你> 我這個專案資料庫用 PostgreSQL 15，以後給我指令都假設是這個版本
[工具] write_memory(content="專案事實：資料庫 PostgreSQL 15")
agent> 好的，我記下來了：這個專案用 PostgreSQL 15。

你> （...接著聊了 30 輪，工具回了好幾個大結果，歷史逼近門檻...）
[系統] 歷史接近上限，壓縮前先讓 agent 保存關鍵事實...
[工具] write_memory(content="已確認：使用者要的匯出腳本完成於 export.py")
[系統] conversation compacted（4200 → 900 tokens）

你> 我一開始說資料庫是哪個版本來著？
[工具] read_memory()
agent> 你的資料庫是 PostgreSQL 15。   ← 雖然原始對話已被壓縮，事實從 memory 撈回來了
```

跨 session 驗證（重啟程式）：

```
$ python mini_agent_b.py        # 重新啟動
[系統] 已載入長期記憶
你> 給我一個建資料表的範例
agent> （給出 PostgreSQL 15 語法的範例）   ← 記得上一段 session 寫進 memory 的偏好
```

對照失敗情況（只壓縮、不寫 memory）：

```
你> 我一開始說資料庫是哪個版本來著？
agent> 抱歉，我不太確定，能再說一次嗎？   ← 細節在多層摘要中被稀釋掉了
```

## 如果你卡住了

1. **不知道從哪加起**：先加 memory（較獨立），跑通跨 session 記憶，再加壓縮。**別想一次裝好兩個。** 兩個都能單獨動之後，最後再接「壓縮前寫 memory」的協同。
2. **壓縮切點老是 400**：你大概在 tool_use 和 tool_result 中間切了。回去看 Ch 13 的 `find_safe_split`——切點落在「帶 tool_result 的 user 訊息」時要往前挪，把整個 tool 往返留在保留段。
3. **壓縮後第一個請求就 400（但不是 tool 配對問題）**：多半是角色交替壞了——摘要是 user 訊息，後面緊接著又一個 user。用 Ch 13 的橋接邏輯（`recent[0]` 是 user 就插一則 assistant「確認收到摘要」）。
4. **memory 變垃圾場**：你的準則太鬆。回去看 Ch 14 失敗示範——準則要嚴格限定「相對穩定、未來會用」，把判斷交給模型但給清楚的界線。
5. **壓縮後 agent 忘了關鍵事實**：這正是練習要你體會的！如果你**沒**做「壓縮前先寫 memory」，這是預期行為。加上協同後再試一次。
6. **怎麼觸發壓縮來測試**：別真的聊 30 輪。把門檻調很低（例如 1500 token），或灌一兩個大的 `read_text_file` 結果進歷史，幾輪就會觸發。測完再調回正常值。
7. **`count_tokens` 怎麼來**：用 Ch 10 的 `client.messages.count_tokens(...)`，把 `system` + `tools` + `messages` 一起算。別自己用字數估。

## 實作步驟建議

### Step 1：先把 memory 裝上（獨立可測）
加 `read_memory`/`write_memory` 兩個工具進註冊表，在 system prompt 寫好記憶準則，並在 `Agent.__init__` 或第一次 `chat()` 時讀一次 memory 塞進開頭。跑兩段獨立 session 驗證跨對話記憶（Ch 14 練習 1）。

### Step 2：把壓縮裝上（獨立可測）
接進 Ch 13 的 `find_safe_split`/`summarize`/`compact`，摘要器用 Haiku。在 `chat()` 開頭加門檻檢查：`count_tokens` 超過就 `compact`。先用很低的門檻 + 大工具結果逼出壓縮，確認壓縮後不 400、體積真的縮小。

### Step 3：接上「壓縮前先寫 memory」的協同
在 `compact` **觸發前**，插入一步：讓 agent（或一個專門的小提示）把「即將被壓縮的舊段」裡的關鍵事實 `write_memory`。最簡單的做法是壓縮前先送一則內部請求：「以下歷史即將被壓縮，把其中**之後仍可能用到的關鍵事實**寫進 memory（用 write_memory），沒有就不寫」。

### Step 4：製造對比，親眼看到差別
跑同一個長任務兩遍：一遍**關掉** Step 3 的協同、一遍**開啟**。兩遍都在壓縮後問「我最初說的關鍵事實是什麼」。看關掉時 agent 忘了、開啟時從 memory 撈回來了。**這個對比就是這份練習的全部意義。**

### Step 5：收尾與量測
壓縮前後印 `analyze_history`（Ch 12）看體積變化；（可選）接 prompt caching 並觀察壓縮如何讓 `cache_read` 掉下來（Ch 17）。

## 完整參考解答

**先自己寫完再看！** 這題的價值在「親手撞到壓縮丟資訊、再用 memory 救回來」的過程，照抄解答會錯過那個頓悟。

<details>
<summary>點開參考實作（在練習 A 的 mini_agent.py 上擴充）</summary>

```python
# mini_agent_b.py — 練習 A 的 agent + 壓縮(Ch13) + memory(Ch14)
import os
import ast
import operator
import datetime
import anthropic

client = anthropic.Anthropic(max_retries=2, timeout=60.0)
MODEL = "claude-opus-4-8"
SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"   # 摘要用便宜模型（Ch 13）

MEMORY_PATH = "agent_memory.md"
# 故意調很低，方便幾輪就觸發壓縮來觀察。注意這比 max_tokens(2048) 還低，純粹是示範值；
# 真實門檻要設成「window 大小 - output budget(max_tokens) - system/tools tokens」之後還留緩衝。
COMPACT_THRESHOLD = 1500
KEEP_RECENT = 6

# ---------- 工具：計算 / 讀檔（沿用練習 A，省略 _safe_eval 細節，見練習 A） ----------
# ... get_current_time / calculate / read_text_file 同練習 A ...

# ---------- 新增工具：memory（Ch 14） ----------

def read_memory() -> str:
    """讀取 agent 的長期記憶筆記。"""
    if not os.path.exists(MEMORY_PATH):
        return "（目前沒有任何長期記憶）"
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        return f.read()

def write_memory(content: str) -> str:
    """把一條重要、相對穩定、未來會用到的資訊追加進長期記憶。"""
    with open(MEMORY_PATH, "a", encoding="utf-8") as f:
        f.write(content.rstrip() + "\n")
    return "已寫入長期記憶。"

# ---------- 工具兩面（在練習 A 的 TOOL_SCHEMAS/TOOL_FUNCTIONS 上補這兩個） ----------
MEMORY_SCHEMAS = [
    {
        "name": "read_memory",
        "description": "讀取你的長期記憶筆記（跨對話保存的使用者偏好與關鍵事實）。"
                       "對話開始時、或需要回憶先前結論時使用。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_memory",
        "description": "把一條『相對穩定、未來會用到』的資訊寫進長期記憶："
                       "使用者長期偏好、跨任務關鍵事實、已確認的結論。"
                       "不要寫一次性的寒暄、閒聊、或能重新查到的東西。",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "精煉、具體的一條事實或偏好"}},
            "required": ["content"],
        },
    },
]
# TOOL_SCHEMAS = [...練習A的三個...] + MEMORY_SCHEMAS
# TOOL_FUNCTIONS = {...練習A的三個..., "read_memory": read_memory, "write_memory": write_memory}

# ---------- 壓縮（Ch 13，原樣搬過來） ----------

def _role(msg) -> str:
    return msg["role"] if isinstance(msg, dict) else msg.role

def _starts_with_tool_result(msg) -> bool:
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list) and content:
        first = content[0]
        ftype = first["type"] if isinstance(first, dict) else first.type
        return ftype == "tool_result"
    return False

def find_safe_split(messages, keep_recent: int) -> int:
    # 簡化版（同 Ch 13）：假設 tool_result 排在 user content 開頭。它擋住「最常見」的拆配對，
    # 但不做 tool_use id ↔ tool_result id 的完整比對；一輪多工具、混合內容的嚴謹版要再加。
    # 對本練習夠用，但你上 production 前要把它補成「按 id 配對」的版本。
    idx = max(0, len(messages) - keep_recent)
    while idx > 0 and _starts_with_tool_result(messages[idx]):
        idx -= 1
    return idx

def _block_field(block, key):
    return block[key] if isinstance(block, dict) else getattr(block, key)

def _render_transcript(msgs) -> str:
    """把舊歷史攤平成可讀文字餵給摘要/抽取器。
    一定要逐塊處理 text / tool_use / tool_result——直接 str(content) 會把
    SDK 的 block 物件印成醜 repr，甚至漏掉工具結果裡的關鍵事實（這正是摘要漏資訊的元兇）。"""
    lines = []
    for m in msgs:
        role = _role(m)
        content = m["content"] if isinstance(m, dict) else m.content
        if isinstance(content, str):                       # 純文字訊息
            lines.append(f"[{role}] {content}")
            continue
        for block in content:                              # block 陣列
            btype = _block_field(block, "type")
            if btype == "text":
                lines.append(f"[{role}] {_block_field(block, 'text')}")
            elif btype == "tool_use":
                lines.append(f"[{role} 呼叫工具] "
                             f"{_block_field(block, 'name')}({_block_field(block, 'input')})")
            elif btype == "tool_result":
                lines.append(f"[工具結果] {_block_field(block, 'content')}")
    return "\n".join(lines)

def summarize(old_messages) -> str:
    transcript = _render_transcript(old_messages)
    resp = client.messages.create(
        model=SUMMARIZER_MODEL,
        max_tokens=1024,
        system=("你是一個對話壓縮器。把以下 agent 與使用者的對話歷史，濃縮成一段給「未來的自己」"
                "接手用的摘要。必須保留：使用者的原始目標與限制、已完成的步驟與其結果、"
                "已知的重要事實與決定、目前遇到的問題或待辦。可以省略：寒暄、已被推翻的嘗試、"
                "冗長的中間推理。用條列、精簡、具體（含關鍵數值/檔名/ID），不要客套。"),
        messages=[{"role": "user", "content": f"以下是要壓縮的對話歷史：\n\n{transcript}"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")

def compact(messages, keep_recent: int = KEEP_RECENT) -> list:
    split = find_safe_split(messages, keep_recent)
    if split <= 0:
        return messages
    old, recent = messages[:split], messages[split:]
    summary_text = summarize(old)
    summary_message = {
        "role": "user",
        "content": f"[以下是先前對話的壓縮摘要]\n{summary_text}\n[摘要結束，以下是最近的對話]",
    }
    rebuilt = [summary_message]
    if recent and _role(recent[0]) == "user":
        rebuilt.append({"role": "assistant",
                        "content": "了解，我已根據以上摘要掌握目前進度，繼續處理。"})
    return rebuilt + recent

# ---------- token 量測（Ch 10） ----------

def history_tokens(system, messages) -> int:
    resp = client.messages.count_tokens(
        model=MODEL, system=system, tools=TOOL_SCHEMAS, messages=messages,
    )
    return resp.input_tokens

# ---------- 協同：壓縮前先寫 memory（本練習靈魂，Ch 14） ----------

# 這一步只給模型「write_memory」這一個工具——讓它「自己決定」要不要寫、寫什麼，
# 而不是我們抽完文字硬塞。這才是 Ch 14 的 agentic memory（模型判斷記什麼）。
WRITE_MEMORY_ONLY = [s for s in MEMORY_SCHEMAS if s["name"] == "write_memory"]

def save_key_facts_before_compaction(old_messages):
    """壓縮前，讓便宜模型『自己用 write_memory』把舊段的關鍵事實寫進 memory（agentic）。"""
    transcript = _render_transcript(old_messages)
    msgs = [{"role": "user", "content":
             "以下對話歷史即將被壓縮。判斷其中有沒有『之後仍可能用到、且相對穩定』的關鍵事實"
             "（使用者長期偏好、已確認的結論、關鍵數值/檔名/ID）。"
             "有的話逐條呼叫 write_memory 寫入（一條一次、精煉具體、**只寫對話裡明確出現的，別推測**）；"
             f"沒有就直接回覆「無」、不要呼叫工具。\n\n{transcript}"}]
    for _ in range(5):                      # 小剎車，防它無限呼叫
        resp = client.messages.create(
            model=SUMMARIZER_MODEL, max_tokens=512,
            tools=WRITE_MEMORY_ONLY, messages=msgs,
        )
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        msgs.append(run_tool_uses(resp.content))   # 沿用練習 A 的 run_tool_uses 執行 write_memory

# ---------- Agent（在練習 A 的 Agent 上加壓縮 + memory 啟動載入） ----------

SYSTEM_PROMPT = """你是一個簡潔、誠實的命令列助理。需要精確計算或即時資訊時請使用工具。

## 長期記憶
你有一份跨對話保存的長期記憶筆記。
- 對話開始時已為你載入現有記憶（見下方）。
- 當使用者表達長期偏好、或出現之後還會用到的關鍵事實時，用 write_memory 記下來。
- 只記「相對穩定、未來會用」的；不要把一次性的閒聊塞進去。
"""

class Agent:
    def __init__(self):
        self.messages = []
        self.max_turns = 10
        self._reload_memory()              # 啟動時把 memory 載進 system（push-based）
        print("[系統] 已載入長期記憶")

    def _reload_memory(self):
        """把 memory 檔的最新內容重建進 system prompt。
        每段 session 開始、以及壓縮剛寫過 memory 之後都要呼叫——否則 system 裡會是舊快照。"""
        mem = read_memory()
        self.system_prompt = SYSTEM_PROMPT + f"\n## 目前的長期記憶\n{mem}\n"

    def reset(self):
        self.messages = []
        self._reload_memory()              # 新 session 要重讀 memory（不是只清空 messages）

    def _maybe_compact(self):
        toks = history_tokens(self.system_prompt, self.messages)
        if toks <= COMPACT_THRESHOLD:
            return
        split = find_safe_split(self.messages, KEEP_RECENT)
        if split <= 0:
            return
        print(f"[系統] 歷史接近上限({toks} tok)，壓縮前先保存關鍵事實...")
        save_key_facts_before_compaction(self.messages[:split])   # ← 協同：先寫 memory
        before = toks
        self.messages = compact(self.messages, KEEP_RECENT)
        self._reload_memory()              # ← 關鍵：剛寫進 memory 的事實要重新載進 system，
                                           #     否則摘要若漏掉它，當輪模型仍看不到剛保存的事實
        after = history_tokens(self.system_prompt, self.messages)
        print(f"[系統] conversation compacted（{before} → {after} tokens）")

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        for _ in range(self.max_turns):
            self._maybe_compact()    # ← 每輪呼叫模型前先檢查是否要壓縮
            try:
                resp = client.messages.create(
                    model=MODEL, max_tokens=2048,
                    system=self.system_prompt, tools=TOOL_SCHEMAS, messages=self.messages,
                )
            except anthropic.APIStatusError as e:
                if e.status_code == 429 or e.status_code >= 500:
                    return "（伺服器忙碌，已重試多次仍失敗，請稍後再試）"
                return f"（請求錯誤 {e.status_code}，可能是程式設定問題）"
            except anthropic.APIConnectionError:
                return "（無法連線，請檢查網路後再試）"

            self.messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue
            if resp.stop_reason == "end_turn":
                return text
            if resp.stop_reason == "max_tokens":
                return text + "\n（⚠️ 回應因長度上限被截斷）"
            return text + f"\n（收到未預期的 stop_reason: {resp.stop_reason}）"
        return "（達到最大回合數上限，未能完成）"
```

**解答說明**：

- **memory 是兩個檔案工具 + system prompt 準則**（Ch 14）：`write_memory` append、`read_memory` 讀全文。準則寫死在 system，把「記什麼」的判斷交給模型（agentic memory），但用明確界線防垃圾場。**這是最小實作**：沒有去重、更新、大小上限——跑久了 memory 會膨脹、甚至累積矛盾（偏好改了舊的還在）。延伸挑戰 1、2 補這塊。
- **啟動時載入 memory，且壓縮後要重載**：`_reload_memory` 把 memory 檔內容拼進 system prompt。關鍵是它要在三個時機呼叫——`__init__`、`reset`（新 session 重讀）、以及**壓縮剛寫過 memory 之後**。最後這個最容易漏：壓縮前 `save_key_facts` 把事實寫進了檔案，但若不重載，當輪的 system 還是舊快照，萬一摘要又剛好漏掉那條事實，模型當輪就「看不到」剛存的東西。注意 memory 放進 system 會佔 context、也影響快取前綴（Ch 17），別讓它無限長大。
- **壓縮在每輪呼叫模型前檢查**（`_maybe_compact`）：用 `count_tokens` 的 `input_tokens` 量真實 token，超門檻才壓。壓縮用 Ch 13 的安全切分 + 橋接，擋住最常見的拆配對（完整的 id 配對版見 `find_safe_split` 註解）。
- **靈魂在 `save_key_facts_before_compaction`，而且它是真・agentic**：壓縮**之前**，我們只把 `write_memory` 這一個工具給便宜模型，讓它**自己判斷**要不要寫、寫哪幾條（跑一個小 tool-use loop），而不是抽完文字硬塞。這對齊 Ch 14「讓模型判斷記什麼」的精神，prompt 還明確要求「只寫對話裡明確出現的、別推測」以免把幻覺永久化。寫完後事實安全躺在 `agent_memory.md`，即使摘要把對話濃縮模糊了，下次 `read_memory` 仍撈得回來——**這就是 Ch 14「壓縮前先寫 memory」黃金搭檔的具體實作。**
- **摘要器/抽取器用 Haiku、主 agent 用 Opus**：把簡單的摘要/抽取任務分派給便宜模型（Ch 13、Ch 37 前哨）。

</details>

## 測試用例

| 步驟 | 操作 | 預期行為 | 驗證了什麼 |
|---|---|---|---|
| 1 | 告訴它一個關鍵事實（「資料庫是 PG 15」） | 呼叫 `write_memory` 記下 | memory 寫入 + agentic 判斷 |
| 2 | 重啟程式，問需要那事實的問題 | 從 memory 記得（給 PG 15 答案） | 跨 session 記憶 |
| 3 | 把門檻調低，灌幾個大工具結果 | 觸發壓縮，印出「compacted」與體積變化 | 壓縮觸發 + 安全切分 |
| 4 | 壓縮後立刻送一句 | 不 400 | tool 配對 + 角色交替沒被破壞 |
| 5 | **關掉**協同，壓縮後問早期關鍵事實 | agent 忘了/不確定 | 證明「只壓縮會丟資訊」 |
| 6 | **開啟**協同，同樣流程 | agent 從 memory 撈回事實 | 證明「壓縮前寫 memory」救回資訊 |
| 7 | 用鬆準則閒聊一陣 | `agent_memory.md` 變流水帳 | 重現垃圾場失敗（Ch 14） |

第 5、6 步的對比是這份練習的核心驗收——務必親手跑出兩種結果。

## 延伸挑戰（加分）

1. **結構化 memory**：把 `agent_memory.md` 分成「偏好/事實/結論」三段，`read_memory(category)` 只讀其中一類，觀察撈回的 context 精簡多少（Ch 14 進階）。
2. **memory 去重/更新**：使用者改了偏好（「其實我改用 MySQL」），讓 agent 找到舊那條並覆蓋，而不是 append 出矛盾（Ch 14 踩雷 6）。
3. **遞迴壓縮**：把門檻調更低，跑到「摘要本身又超門檻」，實作把舊摘要再壓一層，觀察資訊逐層流失，體會為什麼關鍵事實該靠 memory 而非賭它撐過多層摘要（Ch 13 第四節）。
4. **結構化摘要**：把 `summarize` 改成吐 `{goal, done, facts, todo}` 的 JSON（先看 Ch 32），比較它和自由文字摘要哪個更不容易漏欄位。
5. **接 prompt caching（Ch 17）**：給 system + 工具加 `cache_control`，記錄每輪 `cache_read_input_tokens`，觀察壓縮發生那一輪它如何掉下來——親眼看到「省未來 token vs 保住快取前綴」的張力。

## 自我檢核

- [ ] 我的 agent 能跨 session 記住寫進 memory 的關鍵事實
- [ ] 我的壓縮會在門檻觸發、且壓縮後不會 400（沒拆散 tool 配對、沒破壞角色交替）
- [ ] 我能親手跑出「只壓縮會忘事 vs 壓縮前寫 memory 就記得」的對比
- [ ] 我能解釋為什麼摘要器用便宜模型、主 agent 用貴模型
- [ ] 我的 memory 準則夠嚴，不會把閒聊記成流水帳
- [ ] 我能說清楚壓縮、memory 在我的 agent 裡各自解決什麼、怎麼協同

做完這題，你的 mini-agent 已經能在長對話下既不撐爆、又不遺忘關鍵事實——這是它從「玩具」走向「能跑真實長任務」的關鍵一步。Part 3 我們轉向另一條主線：**工具系統**。前面工具都很陽春，接下來要認真設計「好的工具長什麼樣」——從 schema、描述、結果格式，到檔案/shell 工具、MCP、與 permission。

→ [Ch 18 好的 tool 長什麼樣：schema 設計](./18-tool-schema-design.md)
