# Ch 39 — 確定性與可重現

> **目標**：上一章一直在跟「非確定」纏鬥——同一個輸入跑兩次，路徑不一樣、bug 抓不到、eval 分數飄。這一章正面拆解它：agent 的非確定**從哪來**（模型取樣、硬體浮點、工具順序、外部環境、context 組裝）、哪些你**固定得了**、哪些**固定不了**（重要：Anthropic API 沒有 `seed`，連 `temperature=0` 都不保證逐位元相同），以及最實用的兩個目標——**可重現**（reproducibility：debug/eval 時讓「該一樣的地方一樣」）和**可恢復**（resume：長任務被中斷後，從斷點接續而不是從頭重跑）。核心心態：**你追求的不是「宇宙級的完全確定」，而是「在你關心的層次上，把該固定的固定住」。**

> **環境**：Python + Anthropic SDK。本章把前面的工具收尾成「可靠執行」：trace（[Ch 35](./35-observability.md)）是 record/replay 的基礎、eval（[Ch 34](./34-eval.md)）需要重現性才穩、context 管理（[Ch 13](./13-context-compaction.md)）影響 checkpoint 內容、停止條件與 `stop_reason`（[Ch 7](./07-stop-conditions-turns.md)）裡的 `pause_turn` 是 resume 的鉤子、工具設計（[Ch 20](./20-tool-result-design.md)）的冪等性決定 resume 安不安全。

## 為什麼需要這個？「跑兩次不一樣」會毀掉三件事

非確定不是學術潔癖，它實際上會弄壞你的工作流：

- **debug 抓不到 bug（Ch 38）**：「我這邊不重現」——你要修的那次壞掉的執行，重跑就消失了。沒有重現性，你連「有沒有修好」都驗不了。
- **eval 分數會飄（Ch 34）**：同一個 case 跑三次，兩次過一次掛。你以為改 prompt 讓分數從 80 升到 85，其實是雜訊。沒有重現性，你分不清「真的變好」和「這次運氣好」。
- **沒法稽核/重放**：出事後要回答「當時 agent 到底看到什麼、為什麼那樣決定」，如果執行不可重放，你只有一堆對不上的片段。

還有一個獨立但相關的痛點：**長任務跑到一半掛了**（網路斷、process 被殺、撞到 rate limit）。如果不能 resume，你只能從第一回合重跑——前面燒的錢和時間全部重來，而且因為非確定，**重跑的路徑還跟原本不一樣**。

所以這一章有兩個**不同**的目標，別混為一談：

1. **可重現（reproducibility）**：再跑一次能得到（夠接近的）同樣結果——服務 debug 和 eval。
2. **可恢復（resume / checkpoint）**：執行中斷後能從斷點接續——服務長任務的可靠性。

兩者都跟「非確定」對抗，但手段不同。先看非確定到底從哪來。

## 先建立直覺：非確定的五個來源，分兩層

把雜訊來源攤開，你會發現它們分成「模型層」和「你的程式層」——**你能控制的主要在程式層**：

```
   一次 agent 執行的非確定來源
   ├─ 模型層（你只能「降低」，固定不了到逐位元）
   │   ├─ ① 取樣隨機：temperature / top_p / top_k 控制「挑下一個 token 的隨機性」
   │   └─ ② 系統層抖動：浮點累加順序、GPU 平行、MoE 路由、批次組合
   │        → 即使 temperature=0，也不保證每次逐字相同
   └─ 你的程式層（這些你「真的能固定」）
       ├─ ③ 工具執行順序 / 平行：哪個 tool 先回、平行結果的合併順序
       ├─ ④ 外部環境：網路回應、現在幾點、檔案/DB 當下狀態、亂數
       └─ ⑤ context 組裝：你怎麼拼 messages（取最近 N 筆？字典序？時間戳？）
```

關鍵認知：**很多人以為「設 temperature=0 就確定了」，這只對了一半**。temperature=0 把①壓到最低（幾乎總是挑機率最高的 token），但②（硬體/批次層的抖動）依然存在，所以**連 temperature=0 都可能兩次回得不完全一樣**——這個結論 Anthropic 官方文件明講；底層常見原因包括浮點不可結合、GPU 平行歸約順序、批次組成不同等工程因素。而③④⑤其實常常是你 agent 行為飄的**主因**，而且**這些你 100% 控制得了**。

## 一、模型層：你能固定什麼、不能固定什麼

先講最常被誤解的一層。

### 取樣參數：能調，不能「鎖死」

Anthropic Messages API 的取樣旋鈕（**注意：可不可調、值域，依模型而異，下面是傳統行為**）：

- **`temperature`**（傳統 0~1）：越低越「挑機率最高的」，越高越發散。debug/eval 時設**接近 0**（或 0）縮小行為空間。
- **`top_p`**（nucleus sampling）：只從累積機率前 p 的 token 裡挑。
- **`top_k`**：只從機率前 k 個 token 裡挑。

> **⚠️ 較新的 Claude 模型可能不讓你調這些旋鈕。** 截至撰寫，Claude Opus 4.7 及更新的模型已**棄用 `temperature` / `top_p` / `top_k`**——傳非預設值可能直接回 400。所以「設 temperature 接近 0」這招**只對仍開放這些參數的模型成立**，用之前務必查當下模型的 API 文件。這反而強化本章的論點：**連「降溫縮小發散」都不是每個模型給得了的，重現性更要靠下面的 record/replay，而不是取樣參數。**

> **重點：Anthropic 的 Messages API 沒有 `seed` 參數。** 這跟 OpenAI 不一樣（OpenAI 的**部分 API／Chat Completions** 提供 `seed` + `system_fingerprint` 來盡力重現取樣——而且仍是 best-effort、依端點/模型而定，不是所有現代 API 都有通用 seed；OpenAI 官方 API reference 已把回應裡的 `system_fingerprint` 標為 **deprecated**，`seed` 也屬部分／舊式 Chat Completions 能力，別當成現代 API 的通用功能）。所以你**不能**靠「固定 seed」在 Anthropic 上重放模型輸出。能做的（在仍支援取樣參數的模型上）只有「把 temperature 降到接近 0 縮小發散」，加上下面講的**外部 record/replay**。別寫出「設個 seed 就重現」這種程式——那個參數不存在。

### 為什麼 temperature=0 還是不保證逐位元相同

就算 temperature=0、top_k=1，模型也可能兩次回不同，因為②那層：

- **浮點不結合律**：`(a+b)+c ≠ a+(b+c)` 在浮點下，GPU 上累加順序會變。
- **平行/批次**：你的請求跟誰一起進同一批、在哪張卡上算，會微妙改變數值，偶爾翻轉「機率最接近的兩個 token」誰勝出。
- **MoE 路由（若架構用到）**：若模型/serving 架構是 mixture-of-experts，專家路由也可能引入批次相依的差異——這是一般 LLM serving 的可能來源，不代表是某家模型已公開確認的架構細節。

所以模型層的現實是：**你能讓它「大機率一樣」，但拿不到「保證一樣」。** 真要保證一樣，得靠下一節的 record/replay——不重放模型，重放**它上次的輸出**。

### 想真正重現？把模型「snapshot」釘死

模型會**靜默升級**：`claude-opus-4` 這種別名背後的權重會換。今天的行為，下個月可能因為模型更新就變了。要重現/稽核，**用帶日期的明確 snapshot ID**（如 `claude-haiku-4-5-20251001`），別用滾動別名——這樣至少「哪個模型版本產生的這條 trace」是釘死的。

## 二、可重現的真正解法：record / replay

既然模型層固定不了，**重現性的務實做法不是「重新跑模型期望它一樣」，而是「把上次跑的東西錄下來，下次重放錄好的」**——就像測試裡的 VCR / cassette、或前端的 HTTP mock。

概念：

```
   錄製模式（第一次真跑）            重放模式（debug/CI 重跑）
   ┌───────────────────┐          ┌───────────────────┐
   │ 真打 LLM API       │          │ 不打 API           │
   │ 真執行工具         │   ───►   │ 照 key 查出上次錄的 │
   │ 把每個請求的輸入→  │  錄成    │ 回應，原樣回放      │
   │ 輸出 都存成 fixture│  檔案    │ → 完全確定、可離線   │
   └───────────────────┘          └───────────────────┘
```

```python
import json, hashlib
from pathlib import Path

class Recorder:
    """錄/放 LLM 與工具呼叫。key 用「輸入內容的 hash」，重放時照 key 查。"""
    def __init__(self, path: Path, mode: str):  # mode: "record" | "replay"
        self.path = path
        self.mode = mode
        self.store = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def _key(self, kind: str, payload: dict) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return f"{kind}:{hashlib.sha256(blob.encode()).hexdigest()}"

    def call(self, kind: str, payload: dict, real_fn):
        k = self._key(kind, payload)
        if self.mode == "replay":
            if k not in self.store:
                raise KeyError(f"replay 缺這筆錄製：{kind}（輸入變了？需要重錄）")
            return self.store[k]
        # record：真跑一次，存起來
        result = real_fn()
        self.store[k] = result
        self.path.write_text(json.dumps(self.store, ensure_ascii=False, indent=2), "utf-8")
        return result
```

用它包住 LLM 呼叫和工具呼叫：

```python
# LLM 呼叫：key 要含「所有會影響輸出的參數」，不只 messages
llm_payload = {
    "model": model, "system": system, "messages": messages, "tools": tools,
    "max_tokens": 1024, "tool_choice": tool_choice,
    # 還有任何你有傳的：temperature/top_p/top_k（若該模型支援）、
    # thinking 設定、response/output schema、相關 beta header 等
}
resp = rec.call("llm", llm_payload,
                real_fn=lambda: client.messages.create(**llm_payload).model_dump())
# 工具呼叫
out = rec.call("tool", {"name": tool_name, "input": tool_input},
               real_fn=lambda: run_tool(tool_name, tool_input))
```

這樣 **debug 時能 100% 重放那次壞掉的執行**（Ch 38 的「抓壞 trace」），**CI 裡 eval 能離線跑、不花錢、不飄**（Ch 34）。注意 key 的設計：用「輸入 hash」當 key，意味著**只要輸入完全一樣就回放同樣輸出**；輸入一變（你改了 prompt），replay 會 miss → 提示你「這筆要重錄」，這正是你要的訊號。**所以 key 一定要涵蓋所有影響輸出的欄位**（`system`、`max_tokens`、`tool_choice`、取樣參數、thinking/schema、beta header…）——漏掉哪個，那個欄位變了卻 replay 命中舊 fixture，你會拿到對不上的回放、debug 到懷疑人生。

> **record/replay 的邊界**：它讓「重放」確定，但**不讓「真跑」確定**——真跑時模型還是非確定的。它服務的是「重現一次特定執行」（debug/回歸測試），不是「讓生產環境每次都一樣」。也要小心 fixture 會過期：模型升級、工具行為改了，錄製就跟現實脫節，要定期重錄。

## 三、固定你程式層的非確定（③④⑤）

模型層固定不了，但程式層的非確定常常才是行為飄的主因，而且**全在你手上**：

- **③ 工具執行順序 / 平行**：平行跑工具（Ch 31）時，結果回來的順序不定。**合併進 context 前先排序**（按工具呼叫的原始順序，而非完成順序），否則同一批平行呼叫會因為「誰先回」而組出不同的 context，下一回合就分岔。
- **④ 外部環境**：這是最隱蔽的。
  - **時間**：別在 prompt/工具裡直接用 `datetime.now()`——注入一個「邏輯時鐘」，測試時可固定。
  - **亂數**：工具裡若有隨機（抽樣、洗牌），用可注入的 RNG。
  - **網路/DB 狀態**：搜尋結果、API 回應、DB 內容會變——這正是 record/replay 要錄下來的東西。
- **⑤ context 組裝**：你拼 `messages` 的邏輯要**確定**。用了 `set`／`dict` 迭代順序、或「取最近 N 筆但 N 依賴某個浮動值」這類，會讓同樣的歷史組出不同的輸入。組裝邏輯要可重跑出同一結果。

> 一句話：**模型層你只能「降低」非確定，程式層你該「消除」可消除的非確定。** 別把「反正模型本來就非確定」當藉口，放任程式層的雜訊——那些是你能修、也該修的。

## 四、可恢復：checkpoint 與 resume

這是**另一個**目標（不是重現，是中斷後接續）。長任務（跑幾十回合、幾分鐘到幾小時）一定會遇到中斷：process 被殺、機器重啟、rate limit、`pause_turn`。要能 resume，核心是**把「執行到哪了」變成可持久化的狀態，並在恢復時從那個狀態接續**。

### 什麼是「執行狀態」

agent 的可恢復狀態最小集合：

- **完整的 `messages` 歷史**（到目前為止的所有 user/assistant/tool 訊息）——這是 agent 的「記憶」。
- **回合計數 / 停止條件的進度**（Ch 7）。
- **任務層的狀態**：待辦清單（Ch 28 的 todo）、已完成步驟、外部副作用的記錄。

把這些在**每個回合結束時存檔**（checkpoint），中斷後讀回來、從下一回合繼續：

```python
def run_agent(task, checkpoint_path):
    state = load_checkpoint(checkpoint_path)          # 沒有就初始化
    finish_pending_tools(state, checkpoint_path)      # resume 關鍵：先補齊上次中斷時缺的 tool_result
    while not should_stop(state):
        resp = client.messages.create(model=MODEL, messages=state["messages"],
                                       tools=TOOLS, max_tokens=2048)
        # 存「dict」而非 SDK content block 物件——否則 save_checkpoint 做 JSON dump 會炸（SDK 物件不可序列化）。
        # 正規化成 dict 後，checkpoint 寫得出去、讀回來形態一致（下面 finish_pending_tools 也照 dict 取值）。
        state["messages"].append({"role": "assistant",
                                  "content": [b.model_dump() for b in resp.content]})
        save_checkpoint(checkpoint_path, state)   # 先存：萬一 pause 後 crash，resume 才拿得回 paused response
        resp = settle_pauses(resp, state, checkpoint_path)  # 連續 pause_turn：原樣續跑、用「替換」維持角色交替（見下）

        if resp.stop_reason == "tool_use":
            finish_pending_tools(state, checkpoint_path)   # 跑這批工具、逐一存檔（見下）
        elif resp.stop_reason == "end_turn":
            save_checkpoint(checkpoint_path, state)        # 收工前也存，別讓 final turn 在 return 前遺失
            break
        # max_tokens / 未知 stop_reason 的處理略（見 Ch 7）；重點是 pause 已在 settle_pauses 內消化掉

        state["turn"] += 1
        save_checkpoint(checkpoint_path, state)   # 回合邊界也存一次
    return state


def settle_pauses(resp, state, checkpoint_path):
    """消化 pause_turn：把被暫停的 assistant 回應原樣帶著續打，直到 stop_reason 不再是 pause_turn。
    關鍵不變量：一輪 pause 不論續幾次，都要維持成「同一個 assistant turn」，不要 append 成多個
    assistant turn（否則 messages 疊成 user, assistant, assistant, … 角色結構亂掉）。
    千萬別塞 {"role":"user","content":"continue"}（那是處理空 end_turn／max_tokens 截斷的手法，
    套在 pause_turn 上會改寫 server-side 工具迴圈狀態）。"""
    while resp.stop_reason == "pause_turn":
        # 用「跟主迴圈同一套」create 參數續打——system / tool_choice / beta header / thinking 設定
        # 一個都別漏，否則 pause 續跑時行為跟一般回合不一致（呼應前面 record/replay：所有影響輸出的欄位都要一致）。
        resp = client.messages.create(model=MODEL, messages=state["messages"],
                                       tools=TOOLS, max_tokens=2048)
        # ⚠️ 官方沒明確保證 continuation 回應「一定含前一次 paused 的全量內容」。
        #    若你的模型/工具回的是「完整 turn」→ 像這樣整段替換上一個 paused turn即可；
        #    若回的是「續寫片段」→ 要改成累加（extend 同一個 assistant turn 的 content），別整段覆蓋，
        #    否則會丟掉先前的 server_tool_use／部分內容。實作前用 trace 確認你那邊回的是哪一種。
        state["messages"][-1] = {"role": "assistant",       # 替換／累加進「同一個」assistant turn，不是新 append
                                 "content": [b.model_dump() for b in resp.content]}
        save_checkpoint(checkpoint_path, state)             # 每續一次就存：crash 後 resume 接得回最新的 paused 狀態
    return resp


def finish_pending_tools(state, checkpoint_path):
    """確保「最後一個 assistant turn 的每個 tool_use，都有對應的 tool_result」。
    - 正常流程：剛拿到 tool_use、全部還沒跑 → 整批跑完。
    - resume 流程：上次跑到一半中斷 → 只補沒完成的（工具冪等，補跑不會重複副作用）。
    為什麼非有這步不可：Anthropic 工具協定要求每個 tool_use 都要有緊接的 tool_result，
    缺一個就格式錯誤。checkpoint 可能正好停在「跑完工具 1、還沒跑工具 2」的半套狀態，
    這個 user turn 不齊，直接送回 API 會炸——所以 resume 一進來、和每次 tool_use 後，
    都先把它補滿，才保證送出去的 messages 永遠是 tool_use/tool_result 成對的。"""
    msgs = state["messages"]
    if not msgs:
        return
    if msgs[-1]["role"] == "assistant":                    # 還沒建結果 turn（或剛拿到 tool_use）
        assistant = msgs[-1]
        if not any(block_type(b) == "tool_use" for b in assistant["content"]):
            return                                         # 這個 assistant turn 沒要求工具，無事可做
        results = []
        msgs.append({"role": "user", "content": results})  # 與 results 同物件，append 即改 state
    elif msgs[-1]["role"] == "user" and len(msgs) >= 2:    # 可能是半套 tool_result turn——先做基本守門，別把一般 user 訊息誤當成它
        assistant = msgs[-2]
        content = msgs[-1]["content"]
        # 守門：上一個 assistant 必須真的有 tool_use，且這個 user content 必須是 block list。
        # 否則這只是普通使用者訊息（可能是純文字或 image/document block），沒有待補的工具。
        if assistant.get("role") != "assistant" \
           or not any(block_type(b) == "tool_use" for b in assistant["content"]) \
           or not isinstance(content, list):
            return
        results = content
    else:
        return

    # 本範例假設這個 user turn「只含 tool_result blocks」（這也是這個迴圈自己產生的形態）。
    # 重要協定限制：Anthropic 要求 user 訊息裡 tool_result blocks 必須排在 content array 最前面，
    # 文字只能接在所有 tool_result 之後。所以若你的 state 可能混入文字，別直接 append 到尾端——
    # 要把缺的 tool_result 補在文字「之前」，否則仍會 400。
    # 只從 type == "tool_result" 的 block 取 id，別假設 user content 全是 tool_result。
    # 更嚴謹的版本還會驗：這些 id 必須是上一個 assistant 的 tool_use id 子集、不重複、順序正確。
    # 這裡保持精簡只示意「補缺」，這幾條再加固才是生產級對帳。
    done_ids = {result_ref(r) for r in results if block_type(r) == "tool_result"}
    for b in (b for b in assistant["content"] if block_type(b) == "tool_use"):
        if block_id(b) in done_ids:
            continue                                       # 上次已完成、結果已在 state，跳過（冪等也不必重跑）
        results.append(run_tool_idempotent(b, state))      # 副作用＋記錄結果
        save_checkpoint(checkpoint_path, state)            # 結果已進 state 才存 → checkpoint 邊界正確


# 關鍵細節：content block 有兩種形態——剛從 create() 回來是 SDK 物件（用 b.type / b.id），
# 正規化／從 checkpoint JSON 讀回來是 dict（用 b["type"] / b["id"]）。上面 run_agent 已在 append 時
# 用 model_dump() 把它轉成 dict（這樣才能存進 checkpoint），所以這裡主要走 dict 路徑；
# accessor 兩種都接，確保不論誰呼叫、content 是哪種形態都不會把 dict 誤判成「沒有 tool_use」而漏補。
def block_type(b):
    return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)

def block_id(b):
    return b.get("id") if isinstance(b, dict) else getattr(b, "id", None)

def result_ref(b):   # tool_result block 用 tool_use_id 指回它對應的 tool_use（不是 id）
    return b.get("tool_use_id") if isinstance(b, dict) else getattr(b, "tool_use_id", None)
```

### resume 最大的坑：工具不冪等

resume 不是「把 messages 讀回來繼續打 API」就好。真正的難點是**副作用**：如果 checkpoint 存在「呼叫了寄信工具之後、但結果還沒寫進 state」的瞬間掛了，resume 重跑那一步 → **信寄兩次**。

解法是讓工具**冪等或可去重**：

- **冪等設計**：同樣的輸入跑兩次效果等於跑一次（如「設定 X=5」冪等；「X 加 1」不冪等）。
- **去重鍵**：給每個有副作用的操作一個 idempotency key，工具端記錄「這個 key 做過了」就跳過。
- **checkpoint 時機**：在「副作用完成且結果已記錄」之後才算這一步完成——把「執行工具」和「記錄結果」放進同一個可恢復的邊界。

> resume 的安全性**取決於工具的副作用語意**，不是取決於你存檔多勤。一個不冪等的寄信工具，存檔再頻繁也可能重寄。**先讓有副作用的工具可去重，resume 才安全。**

還有一個容易漏的協定坑：checkpoint 可能正好停在「assistant 一次要 3 個工具、只跑完 1 個」的**半套狀態**。這時那個承載 `tool_result` 的 user turn 不齊——**直接讀回來送 API 會格式錯誤**，因為 Anthropic 要求每個 `tool_use` 都有緊接的 `tool_result`。所以 resume 一進來，要先「對帳」：檢查最後一個 assistant turn 的每個 `tool_use` 是否都有結果，缺的先補跑（冪等，所以已做過的不會重複副作用）湊滿，**才**呼叫模型。上面 `finish_pending_tools` 就是做這件事——它在 resume 入口和每次 `tool_use` 後都跑，把這個迴圈自己產生的半套狀態補成對。要對付任意來源的 checkpoint，還得加驗 id 子集、不重複、順序與 block 位置（範例註解有點到）才算生產級。

### `pause_turn`：模型自己要求的暫停

當模型執行長時間的（如某些 server-side 工具）操作時，回應的 `stop_reason` 可能是 **`pause_turn`**。這不是結束、也不是錯誤——它表示「這一輪先暫停」。正確處理是**把那個被暫停的 assistant 回應原樣放回 messages、直接再打一次請求讓它續跑**（這正是上面 `settle_pauses` 做的事）。**別塞一個 `{"role": "user", "content": "continue"}` 之類的續寫提示**——那是處理空 `end_turn`／`max_tokens` 截斷的手法，套在 `pause_turn` 上會改寫 server-side 工具迴圈的狀態。也別把它當成 `end_turn` 收工（那就是 Ch 38 §4 的「過早放棄」bug）。

> **連續多次 `pause_turn` 別堆疊 assistant turn。** 一輪 `pause_turn` 可能要續好幾次才完成。若你每續一次就 `append` 一個 assistant 回應，messages 會變成 `user, assistant, assistant, …` 一路堆下去，角色結構亂掉。所以上面 `settle_pauses` 用一個 continuation 子迴圈：每次拿到新的 paused response 時，**替換**上一個 paused assistant turn（而非再 append 一個），直到 `stop_reason` 不再是 `pause_turn`。參考 Anthropic 的 [Handling stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)。

## 對比與取捨

| 你想要的 | 用什麼 | 固定得了嗎 | 注意 |
|---|---|---|---|
| 重放一次特定執行（debug） | **record/replay**（Ch 35 trace） | ✅ 完全確定 | fixture 會過期，要重錄 |
| eval 分數別飄（CI） | record/replay + 低 temperature | ✅（重放）/ ⚠️（真跑） | 真跑仍需多跑取統計（Ch 34） |
| 模型輸出每次一樣 | 降 temperature + 釘 snapshot | ❌ 只能「大機率一樣」 | Anthropic 無 seed，temp=0 也非逐位元 |
| 程式層別亂飄 | 固定工具順序/時鐘/RNG/組裝 | ✅ 該消除就消除 | 這常是行為飄的真主因 |
| 長任務中斷可接續 | checkpoint + 冪等工具 | ✅ 結構問題 | 不冪等工具會重複副作用 |

兩個目標再強調一次的差別：**可重現**是「同樣輸入→同樣輸出」——但要講精確，這個「同樣輸出」是**在 replay 模式下**靠 record/replay 拿到的；**真跑**時就算同輸入，也只能靠低溫＋釘 snapshot 降低變異，拿不到逐位元相同。**可恢復**則是「中斷→從斷點繼續」（靠 checkpoint + 冪等）。一個服務 debug/eval，一個服務生產可靠性。

## 踩雷集錦

1. **以為 Anthropic 有 `seed`**：沒有。OpenAI 的**部分 API／Chat Completions** 才有（且 best-effort、依端點/模型而定），Anthropic 沒有。注意 OpenAI 官方 API reference 已把回應裡的 `system_fingerprint` 標為 **deprecated**，`seed` 也只是部分／舊式 Chat Completions 能力——它在文件裡仍見得到，但別把它當成現代通用的確定性能力。別寫依賴 seed 重現的程式——要重現用 record/replay。
2. **以為 `temperature=0` 就完全確定**：它只壓低取樣隨機，硬體/批次層的抖動還在，仍可能兩次不同。官方文件講明。
3. **用滾動別名跑稽核/eval**：模型靜默升級會讓你「同樣 prompt 突然行為變了」卻查不出原因。釘帶日期的 snapshot。
4. **只盯模型層、放任程式層**：工具回的順序、`datetime.now()`、`set` 迭代序——這些才常是 eval 飄的主因，而且本來就該固定。
5. **checkpoint 了卻沒做冪等**：resume 重跑那一步 → 重寄信/重扣款。存檔頻率救不了不冪等的副作用。
6. **把 `pause_turn` 當結束**：當成 `end_turn` 收工就是「假裝完成」（Ch 38）。要接回去續跑。
7. **fixture 永不更新**：record/replay 的錄製跟模型/工具現實脫節後，replay 綠燈但生產早就變了——定期重錄。
8. **replay 當成「驗證模型品質」**：replay 只證明「邏輯對著上次的回應沒壞」，不證明「現在的模型還這樣回」。模型品質要靠真跑 eval。

## 進階：再往深一層

- **durable execution / workflow engine**：把 agent 跑在 Temporal、restate 這類「持久化執行」框架上，它們提供 durable 的 workflow 狀態、自動 replay、retry、resume。**但注意**：它們保證的是「workflow 邏輯可被可靠重放」，**不等於替你保證任意外部副作用冪等或 exactly-once**——對外的副作用（寄信、扣款、寫第三方）你**仍要**自己做 idempotency key、去重、交易式 outbox 或補償邏輯。代價是把你的 agent 迴圈套進它的程式模型。值不值得，看你的任務有多長、中斷成本多高。
- **event sourcing 式的狀態**：把狀態存成「事件序列」（append-only）而非「當前快照」，resume 時重放事件重建狀態。好處是可稽核、可回到任一時點；代價是複雜度。
- **「夠用的重現」而非「完美重現」**：很多時候你不需要逐位元一樣，只需要「語意上等價」——用 LLM-as-judge（Ch 34）比對兩次輸出是否「實質相同」，比逐字 diff 更貼近你真正在意的東西。
- **平行/multi-agent 的確定性更難（Ch 27/31）**：多個 subagent 平行跑，完成順序、彙整順序都引入非確定。要確定，得把「合併」這一步做成順序無關（如按固定 key 排序）或顯式定序。
- **rate limit 與重試的非確定**：retry（Ch 9）的 backoff、jitter 本身是隨機的，且「第幾次成功」會變。debug 時把這些也納入 record/replay，否則「偶發」可能根本來自重試時機而非模型。

## 動手練習

1. **證明非確定**：同一個 prompt（temperature=0）連打 5 次，逐字 diff 輸出。觀察是否真的每次完全一樣（多半「幾乎」一樣，偶有差異）——體會「temp=0 ≠ 逐位元確定」。
2. **做一個 mini Recorder**：實作上面的 `Recorder`，用它包住你 Practice 的 agent 的 LLM 呼叫。先 record 跑一次，再 replay 跑一次，確認 replay 不打 API、結果完全一致。
3. **抓程式層非確定**：在你的 agent 裡找出一處用了 `datetime.now()` 或無序集合的地方，把它改成可注入/已排序，讓同樣輸入組出同樣 context。
4. **checkpoint + resume**：給你的 agent 迴圈加每回合存檔。跑到一半手動 kill，再啟動，確認它從斷點接續而非從頭跑。
5. **製造不冪等的災難再修好**：寫一個「append 一行到檔案」的工具（不冪等），在它之後故意中斷、resume，觀察那行被寫兩次；然後加 idempotency key 修好。

## 本章重點整理

- 兩個**不同**目標：**可重現**（同輸入→同輸出，服務 debug/eval）與**可恢復**（中斷→從斷點續，服務可靠性）。手段不同，別混。
- 非確定五來源分兩層：**模型層**（①取樣隨機、②硬體/批次抖動，只能降低）與**程式層**（③工具順序、④外部環境、⑤context 組裝，該消除）。
- **Anthropic API 沒有 `seed`**；**`temperature=0` 也不保證逐位元相同**（硬體/批次抖動）。要重現靠 **record/replay**，不是靠 seed。
- 要稽核/重現，**釘帶日期的模型 snapshot**，別用會靜默升級的滾動別名。
- 程式層的非確定（時鐘、RNG、工具順序、組裝邏輯）常是 eval 飄的**真主因**，而且**你該消除**——別拿「模型本來就非確定」當藉口。
- **resume = checkpoint（存執行狀態）+ 冪等工具**。存檔再勤，不冪等的副作用照樣重複。先讓有副作用的工具可去重。
- `pause_turn` 要接回去續跑，當成 `end_turn` 就是「假裝完成」（Ch 38）。

## 自我檢核

- [ ] 我能分清「可重現」與「可恢復」是兩個不同目標，各自用什麼手段
- [ ] 我能說出非確定的五個來源，並指出哪些在模型層（只能降低）、哪些在程式層（該消除）
- [ ] 我知道 Anthropic API 沒有 `seed`，且能解釋為什麼 `temperature=0` 仍非逐位元確定
- [ ] 我能說明 record/replay 為什麼是重現性的務實解法，以及它的邊界（不驗模型品質、fixture 會過期）
- [ ] 我能說出 resume 需要存哪些執行狀態，以及「冪等工具」為什麼是 resume 安全的前提
- [ ] 我知道 `pause_turn` 該怎麼處理，以及處理錯了會變成哪個失敗模式
- [ ] 我能指出自己 agent 裡至少一處程式層的非確定並說出怎麼固定它

## 延伸閱讀

### 官方文件

- **[Anthropic — Handling stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)** — Anthropic
  - **讀哪裡**：`pause_turn` 的意義與「把回應接回去續跑」的處理方式；其他 `stop_reason` 的對照。
  - **能學到什麼**：resume / 長任務暫停的官方鉤子怎麼用——這是 §四 checkpoint 範例裡 `pause_turn` 分支的依據。
  - **前提知識**：Ch 7（停止條件）、Ch 5（工具迴圈）。

- **[Anthropic — Messages API reference](https://platform.claude.com/docs/en/api/messages)** — Anthropic
  - **讀哪裡**：`temperature` / `top_p` / `top_k` 參數（確認**沒有** `seed`）、`model` 參數可填的 snapshot ID。
  - **能學到什麼**：模型層你實際能調的旋鈕，以及怎麼釘死 snapshot 做重現/稽核。
  - **前提知識**：Ch 4（最小 agent 迴圈）。

### 部落格 / 技術文章

- **[OpenAI — Advanced usage: Reproducible outputs](https://developers.openai.com/api/docs/guides/advanced-usage)** — OpenAI
  - **這篇說什麼**：OpenAI 用 `seed` + `system_fingerprint` 盡力重現取樣，且明說「即使如此也只是 best-effort、不保證完全確定」。
  - **讀哪裡**：seed 的用法與「為什麼仍非完全確定」的說明。
  - **為什麼值得讀**：用「有 seed 的那一家」反過來理解——連有 seed 都只能 best-effort，更印證「重現性的可靠解法是 record/replay 而非取樣參數」。Anthropic 沒這參數，更要靠 replay。

- **[Temporal — What is durable execution](https://temporal.io/blog/what-is-durable-execution)** — Temporal
  - **這篇說什麼**：durable execution 的概念——把長流程的狀態持久化，讓它可中斷、可 resume、可重試。
  - **讀哪裡**：crash-proof / 自動 replay / resume 的核心論述。（**本章補充**：這類框架保證的是「workflow 邏輯可被可靠重放」，**不等於**替你保證對外副作用的「恰好一次」——寄信、扣款、寫第三方仍要你自己做 idempotency key／去重，這點該文不是重點，是本章要你額外注意的。）
  - **為什麼值得讀**：§四的 checkpoint+resume 是這套思想的手刻簡化版；想把 agent 做得真正可靠（生產級長任務）時，這是「不自己刻」的成熟選擇。

下一章 **Ch 40 框架對比**：到這裡你已經把 agent harness 的每個零件都手刻過一遍——迴圈、工具、context、eval、observability、安全、可靠性。最後一章退一步問：這些東西，**業界的框架**（Claude Agent SDK / LangGraph / OpenAI Agents SDK）幫你做了哪些、又綁住了你什麼？什麼時候該用框架、什麼時候該自己刻？帶著你親手刻過的理解去看框架，你會看得比「只會用框架的人」深得多。

→ [Ch 40 框架對比](./40-framework-comparison.md)
