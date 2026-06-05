# 練習 D — 用 subagent 做 multi-agent 研究工作流

> **目標**：把 Part 4（Ch 26–33）的進階能力**收斂到一個會動的東西**上——一個「研究 agent」：你丟一個研究問題，它**自己拆題**（Ch 28 planning）、**並行派出多個 subagent**各查一塊（Ch 26 subagent + Ch 27 編排 + Ch 31 並行）、每個 subagent 在**隔離的 context** 裡調查並回**結構化結論**（Ch 26 蒸餾 + Ch 32 schema 強制），最後 orchestrator 把這些結論**彙整成一份報告**（Ch 27 synthesis）。完成後你會有一個能力：拿到「幫我研究 X」這種大而模糊的需求，你知道怎麼把它拆成可並行的小調查、讓每個小調查獨立又乾淨地回報、再把碎片拼成有結論的整體——這正是 orchestrator-worker 模式的精髓。

> **環境**：Python 3.11、`anthropic` SDK、`concurrent.futures`。沿用[練習 A](./practice-a-mini-agent-loop.md) 的 agent loop 觀念與 [Ch 32](./32-structured-output.md) 的 schema 強制輸出。為了**不依賴外部網路**、能離線跑，subagent 的「調查」對象是一個**本地文字語料庫**（一個資料夾的 `.txt`），用一個 `search_docs` 工具去查。把它換成真的 web search 或 RAG 檢索，整個骨架不變。

## 背景與動機

到目前為止你都在做**單一 agent**：一個 loop、一套工具、一條 context。Part 4 把視野拉高到「**多個 agent 協作**」，但它是分章學的：

- **Ch 26**：subagent 是什麼——把一塊工作丟給一個**有獨立 context** 的子 agent 做，子 agent 自己跑完，只把**蒸餾過的結論**交回來（而不是把一堆中間過程塞回主 context）。
- **Ch 27**：multi-agent 編排——orchestrator-worker 模式：主 agent 拆題、把獨立的子任務**並行**派給多個 worker、再**彙整**它們的結果。
- **Ch 28**：planning & todo——動手前先**拆解**，拆解的結果決定了「哪些事可以並行」。
- **Ch 31**：背景與並行——獨立的 I/O-bound 工作（多個 subagent 各自打 API）應該**同時跑**，而不是排隊。
- **Ch 32**：結構化輸出——要讓 orchestrator 能**程式化地**收齊各 subagent 的結果，子 agent 就得回**符合 schema 的結構化結論**，而不是一段讓你 regex 硬切的自由文字。

這些你都「分章學過」。但真實的研究任務不會按章節來——它會是「**幫我研究這個專案的權限模型是怎麼設計的、有哪些已知風險**」，然後上面**每一條原則都要同時用上**：你得拆題（Ch 28）、派並行 subagent（Ch 27/31）、讓每個 subagent 在乾淨 context 裡查並回結構化結論（Ch 26/32）、最後彙整（Ch 27）。這個練習就是逼你把它們**織進同一個工作流**。

這個模式不是玩具——它就是 Anthropic 自己的 [multi-agent research 系統](https://www.anthropic.com/engineering/built-multi-agent-research-system)的精簡同款：一個 lead agent 拆題、多個 subagent 並行調查、最後彙整成報告。做完它，你會從「聽過 orchestrator-worker」變成「知道它每一塊為什麼這樣接」。

## 任務規格

做一個 `mini_research.py`，提供一個 `research(question) -> str`，內部分四段：

**第一段：拆題（Ch 28 planning + Ch 32 schema）**
- `make_plan(question)`：呼叫模型，用 **`tool_choice` 強制呼叫**一個 `submit_plan` 工具，schema **強制回「字串陣列」**（拿到就是乾淨 list、不用 parse）；數量 **3–6 個、彼此獨立可並行**是靠 prompt 引導 + 你在程式裡 runtime 收斂（schema 不保證個數）。
- 子問題必須是「可以各自獨立調查、不互相依賴」的——這是後面能並行的前提（Ch 27/28）。

**第二段：並行派 subagent（Ch 26 隔離 + Ch 27 fan-out + Ch 31 並行）**
- 每個子問題交給一個 `research_subagent(subquestion)`：它有**自己的 messages**（**不含** orchestrator 的歷史——Ch 26 context 隔離）、自己的 loop、自己的回合上限（Ch 7 停止條件）。
- subagent 用 `search_docs` 工具在語料庫裡查證據，查夠了就**呼叫 `submit_finding` 回結構化結論**。
- 多個 subagent 要**真的並行**跑（`ThreadPoolExecutor`——它們是 I/O-bound 的 API 迴圈，正是 Ch 31 該並行的場景），不是 for 迴圈排隊。

**第三段：結構化回報（Ch 26 蒸餾 + Ch 32）**
- 每個 subagent 回的 finding 是固定 schema：`answer`（結論）、`evidence`（證據／`檔名:行號`）、`confidence`（high/medium/low，語料裡找不到就 low）。
- 這是**穿過 context 邊界的唯一東西**——subagent 內部翻了多少檔、查了幾次都留在它自己的 context 裡，orchestrator 只拿到這份乾淨結論（Ch 26 的核心價值）。

**第四段：彙整（Ch 27 synthesis）**
- `synthesize(question, findings)`：把所有 finding 餵回模型，產一份報告——**先給總結論、再分點、並明確標出彼此衝突或 `confidence=low` 的缺口**。
- 彙整是一次**模型呼叫**（讓它真的調和、判斷衝突），**不是**把字串接起來。

**禁止**
- 不准把獨立的 subagent **排隊序跑**（for 迴圈逐一 `research_subagent`）——那等於放棄 multi-agent 的全部意義（Ch 27/31）。
- 不准把 **orchestrator 的完整歷史塞進每個 subagent** 的 messages——那是 context 污染又花錢，違反 Ch 26 隔離。
- 不准讓 subagent 回**自由文字**再讓 orchestrator 用 regex 硬切——必須 `tool_choice` + schema 強制（Ch 32）。
- 不准用**字串拼接**當「彙整」——synthesis 是一次 LLM 呼叫，要它調和衝突、點出缺口。
- 不准在多個 thread 間**共用同一個可變 messages list**——每個 subagent 自己一份（Ch 31 並行安全）。

**可選加分**
- subagent 用**便宜模型**（如 `claude-haiku-4-5-20251001`）、orchestrator 用**強模型**——抽取型工作交便宜模型，是 Ch 26/37 的成本分層。
- 給 orchestrator 加**進度追蹤**（Ch 28 todo 風格）：哪些子問題完成、哪個信心低。
- subagent 失敗或超回合上限時**優雅降級**（回一個 `confidence=low` 的佔位 finding），別讓一個壞掉的 subagent 卡死整個流程。
- 把 `search_docs` 換成**真的 web search** 或對程式碼庫的 `grep`——體會骨架不變、只換工具。

## 期望輸出範例

關鍵是看「**拆題 → 並行調查 → 結構化結論 → 彙整出有缺口標注的報告**」這條主線：

```
$ python mini_research.py "這個專案的權限模型是怎麼設計的？"
[orchestrator] 規劃中…
[orchestrator] 拆成 4 個子問題：
  1. 權限決策是在哪一層強制的（模型 vs harness）？
  2. 有哪些 permission 等級／決策值（allow/ask/deny）？
  3. 危險操作（寫檔、跑命令）怎麼被攔下？
  4. permission 的決策依據是什麼（工具名 vs 後果）？
[orchestrator] 並行派出 4 個 subagent…          ← 4 個同時跑，不是排隊
  ✓ [high]   權限決策是在哪一層強制的（模型 vs harness）？
  ✓ [high]   有哪些 permission 等級／決策值（allow/ask/deny）？
  ✓ [medium] 危險操作（寫檔、跑命令）怎麼被攔下？
  ✓ [low]    permission 的決策依據是什麼（工具名 vs 後果）？   ← 語料證據不足，誠實標 low
[orchestrator] 彙整報告…

# 權限模型設計報告

**總結論**：本專案的權限由 harness 在程式層強制（非靠模型自律），用 allow/ask/deny
三值決策，危險的寫類操作會在工具真正執行前被攔下問人。

1. 決策層級：permission gate 寫在 run_tool_uses 裡、工具函式被呼叫之前…（證據：permission.txt:12）
2. 決策值：allow（放行）/ ask（問人）/ deny（拒絕）…（證據：permission.txt:20）
3. 危險操作攔截：write/edit/run_command 走 ask…（證據：tools.txt:8）

**證據不足／需再查**：
- 「決策依據是工具名還是後果」這點語料裡只有零散線索（confidence=low）——
  程式碼層面似乎按工具名硬編，但設計文件說應按「後果」判斷，兩者可能不一致，建議直接讀 check_permission 原始碼確認。
```

子問題彼此獨立，所以 4 個 subagent 同時開查；其中一個查不到足夠證據就**誠實回 low**，而彙整時這個缺口被**明確點出來**而不是被糊過去——這就是這份練習要你做對的事。

## 如果你卡住了

1. **不知道從哪開始**：先把**單一** `research_subagent` 做通（給它一個寫死的子問題，讓它 `search_docs` + 回 `submit_finding`），確認一個 subagent 能在隔離 context 裡查完並回結構化結論。**再**做拆題、**最後**才加並行與彙整。別想一次把四段都接好。
2. **subagent 不肯交 finding，一直搜尋或乾脆閒聊**：它的 messages 裡要明確說「查夠了就呼叫 `submit_finding`」，並設**回合上限**；到上限還沒交，就回一個 `confidence=low` 佔位 finding（別讓它無限跑）。也可以在最後一回合把 `tool_choice` 設成強制交 finding。
3. **並行沒效果（跑起來像排隊）**：確認你真的用了 `ThreadPoolExecutor` 且**沒有**在 `as_completed` 之前 `.result()` 等每一個。API 呼叫是 I/O-bound，GIL 不擋（Ch 31）——thread 就夠，不用 process。
4. **多個 subagent 結果互相污染／報錯 `messages` 亂掉**：八成是**共用了同一個 messages list**。每個 `research_subagent` 進去第一件事就是建**自己的** `messages = [...]`，絕不碰外面的。
5. **orchestrator 收到的 finding 很難用**：你大概讓 subagent 回了自由文字。回 Ch 32——用 `tool_choice={"type":"tool","name":"submit_finding"}` 強制呼叫、`strict: True` 強制 schema，拿 `tool_use.input` 就是乾淨 dict。
6. **彙整只是把 finding 接起來**：那不是 synthesis。把所有 finding 序列化成 JSON 餵回模型，要它「調和、點出衝突與 low-confidence 缺口」——讓**模型**做整合判斷，這才是 Ch 27 的彙整。
7. **沒有語料可查**：建一個 `corpus/` 資料夾、丟幾個 `.txt`（可以把前面幾章的重點貼進去當測試語料），或把 `search_docs` 指到你專案的程式碼。

## 實作步驟建議

### Step 1：做 `search_docs` 工具 + 一個能跑完的 `research_subagent`
先做 `search_docs`（關鍵字比對，回 `檔名:行號: 內容`）。再寫 `research_subagent(subquestion)`：**自己的** messages、`SEARCH_TOOL` + `FINDING_TOOL` 兩個工具、回合上限的 loop。拿一個寫死的子問題跑通：它會先 `search_docs` 幾次、再 `submit_finding`。確認回來的是結構化 dict（answer/evidence/confidence）。

### Step 2：做拆題 `make_plan`（Ch 28 + Ch 32）
用 `tool_choice` 強制呼叫 `submit_plan`、`strict: True` 強制 schema，回 3–6 個獨立子問題的清單。加上限保護（`subs[:6]`）和空清單的防呆。

### Step 3：並行 fan-out（Ch 27 + Ch 31）
用 `ThreadPoolExecutor` 把每個子問題丟給一個 `research_subagent`，`as_completed` 收結果。**每個 subagent 一份自己的 messages**。包 `try/except`：某個 subagent 爆了就回 `confidence=low` 佔位，不要讓整批掛掉。

### Step 4：彙整 `synthesize`（Ch 27）
把 findings 序列化成 JSON 餵回**強模型**，prompt 要它「先總結論、分點說明、**明確標出衝突與 low-confidence 缺口**」。回純文字報告。

### Step 5：串成 `research()`，跑期望輸出的主線
`research(question)` = 拆題 → 印計畫 → 並行派 subagent → 印各 finding 的 confidence → 彙整 → 回報告。跑出「並行調查 + 誠實標 low + 彙整點出缺口」這三件事，這題就成了。

### Step 6（可選）：成本分層、進度追蹤、優雅降級、換真實檢索
挑一個深入：subagent 換 haiku、orchestrator 用 opus（Ch 26/37）；加 todo 風格進度；強化失敗降級；或把 `search_docs` 換成真的 web search / 程式碼 grep。

## 完整參考解答

**先自己寫完再看！** 這題的價值在「親手把 Part 4 的五條原則織進同一個工作流」——尤其是 **context 隔離**（每個 subagent 自己一份 messages）和**結構化回報**（穿過邊界的只有乾淨 finding）這兩處，最容易寫出「看起來會動、其實 subagent 在共用狀態或回自由文字」的版本。照抄會錯過撞洞、補洞的頓悟。

<details>
<summary>點開參考實作（mini_research.py）</summary>

```python
# mini_research.py — orchestrator-worker 多 agent 研究工作流（Part 4 綜合：Ch 26/27/28/31/32）
import os
import sys
import json
import concurrent.futures
from pathlib import Path
import anthropic

client = anthropic.Anthropic(max_retries=2, timeout=60.0)
ORCH_MODEL = "claude-opus-4-8"               # orchestrator：拆題與彙整，用強模型
WORKER_MODEL = "claude-haiku-4-5-20251001"   # subagent：抽取型調查，用便宜模型（Ch 26/37 成本分層）

CORPUS = Path(os.environ.get("RESEARCH_CORPUS", "./corpus"))   # 研究語料庫（一堆 .txt）

# ---------- 調查工具：在語料庫裡關鍵字搜尋（換成 web search / grep 不影響骨架） ----------

def search_docs(query: str) -> str:
    hits = []
    for f in sorted(CORPUS.glob("*.txt")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if query.lower() in line.lower():
                hits.append(f"{f.name}:{i}: {line.strip()}")
                if len(hits) >= 12:           # 控制回傳體積（Ch 20）
                    break
        if len(hits) >= 12:
            break
    if not hits:
        return f"找不到含『{query}』的內容。換個關鍵字再試，或這塊語料可能沒有。"
    return "\n".join(hits)

SEARCH_TOOL = {
    "name": "search_docs",
    "description": "在研究語料庫裡用關鍵字搜尋，回傳相關片段（含 檔名:行號）。多換幾個關鍵字找。",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜尋關鍵字"}},
        "required": ["query"],
    },
}

# ---------- 結構化回報：subagent 唯一能交回的東西（Ch 26 蒸餾 + Ch 32 schema 強制） ----------

FINDING_TOOL = {
    "name": "submit_finding",
    "description": "提交對這個子問題的調查結論。所有欄位都要根據 search_docs 找到的證據填。",
    "strict": True,                          # 強制輸出嚴格符合 schema（Ch 32）
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "對子問題的結論，一兩句話"},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "支持結論的證據片段或 檔名:行號",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "對結論的信心；語料裡證據不足就填 low，別硬掰",
            },
        },
        "required": ["answer", "evidence", "confidence"],
        "additionalProperties": False,
    },
}

def research_subagent(subquestion: str) -> dict:
    """一個獨立 subagent：自己的 context、自己的 loop，最後回結構化 finding。
    它的 messages 不含 orchestrator 的歷史（Ch 26 context 隔離）。"""
    messages = [{"role": "user", "content":
        f"你要調查這個子問題：{subquestion}\n"
        f"用 search_docs 在語料庫裡找證據（可多查幾次、換關鍵字）。"
        f"查到足夠資訊後，呼叫 submit_finding 回報結論——一定要根據搜尋結果，"
        f"找不到就誠實填 confidence=low，不要編。"}]
    last_turn = 5
    for turn in range(last_turn + 1):
        # 最後一回合強制交 finding，避免無限搜尋（Ch 7 停止條件 + Ch 32 強制呼叫）
        tool_choice = ({"type": "tool", "name": "submit_finding"} if turn == last_turn
                       else {"type": "auto"})
        try:
            resp = client.messages.create(
                model=WORKER_MODEL, max_tokens=1024,
                tools=[SEARCH_TOOL, FINDING_TOOL],
                tool_choice=tool_choice,
                messages=messages,
            )
        except anthropic.APIError as e:
            return {"subquestion": subquestion, "answer": f"（調查時 API 出錯：{e}）",
                    "evidence": [], "confidence": "low"}
        messages.append({"role": "assistant", "content": resp.content})

        # 交了 finding 就收工。注意：模型偶爾會在同一回合同時發 search_docs + submit_finding；
        # 這裡直接採用 finding、忽略同回合的搜尋——因為我們不再續這段對話（subagent 結束），
        # 所以「未回 tool_result」不會造成 API 錯誤（那條規則只在你要『繼續』對話時才適用）。
        finding = next((b for b in resp.content
                        if b.type == "tool_use" and b.name == "submit_finding"), None)
        if finding is not None:
            return {"subquestion": subquestion, **finding.input}

        # 處理 search_docs 呼叫，把結果餵回去
        results = []
        for b in resp.content:
            if b.type == "tool_use" and b.name == "search_docs":
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": search_docs(**b.input)})
        if results:
            messages.append({"role": "user", "content": results})
        else:
            # 既沒搜尋也沒交 finding（純講話）→ 推它一把
            messages.append({"role": "user",
                "content": "請呼叫 search_docs 繼續找，或直接 submit_finding 交結論。"})

    # 理論上 last_turn 已強制交 finding，這裡是最後保險（Ch 27 優雅降級）
    return {"subquestion": subquestion, "answer": "（未在回合上限內收斂出結論）",
            "evidence": [], "confidence": "low"}

# ---------- 拆題：orchestrator 第一步（Ch 28 planning + Ch 32 schema 強制） ----------

PLAN_TOOL = {
    "name": "submit_plan",
    "description": "把研究問題拆成彼此獨立、可並行調查的子問題。",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "subquestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-6 個彼此獨立的子問題，每個可由一個 subagent 單獨調查、不依賴其他子問題的答案",
            },
        },
        "required": ["subquestions"],
        "additionalProperties": False,
    },
}

def make_plan(question: str) -> list[str]:
    resp = client.messages.create(
        model=ORCH_MODEL, max_tokens=1024,
        tools=[PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_plan"},   # 強制呼叫（Ch 32）
        messages=[{"role": "user", "content":
            f"研究問題：{question}\n把它拆成 3-6 個彼此獨立、可並行調查的子問題。"}],
    )
    tu = next((b for b in resp.content
               if b.type == "tool_use" and b.name == "submit_plan"), None)
    if tu is None:
        raise ValueError(f"規劃失敗：模型沒回 submit_plan（stop_reason={resp.stop_reason}）")
    subs = [s.strip() for s in tu.input["subquestions"] if s.strip()]
    if not subs:
        raise ValueError("規劃回了空的子問題清單")
    # 注意：schema 只保證「string 陣列」，數量 3-6 是 prompt 引導 + 這裡 runtime 收斂，
    # 不是 schema 強制的。少於 1 個已在上面擋掉；上限切到 6 防並行數爆掉。
    return subs[:6]

# ---------- 並行派 subagent（Ch 27 fan-out + Ch 31 並行） ----------

def run_subagents(subquestions: list[str]) -> list[dict]:
    findings = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(subquestions)) as pool:
        futures = {pool.submit(research_subagent, q): q for q in subquestions}
        for fut in concurrent.futures.as_completed(futures):   # 誰先完成先收
            q = futures[fut]
            try:
                findings.append(fut.result())
            except Exception as e:                             # 單一 subagent 爆掉不拖垮全批
                findings.append({"subquestion": q, "answer": f"（subagent 失敗：{e}）",
                                 "evidence": [], "confidence": "low"})
    # 依原始順序排回，報告比較好讀
    order = {q: i for i, q in enumerate(subquestions)}
    findings.sort(key=lambda f: order.get(f["subquestion"], 999))
    return findings

# ---------- 彙整：把碎片拼成有結論的整體（Ch 27 synthesis，是一次 LLM 呼叫不是字串拼接） ----------

def synthesize(question: str, findings: list[dict]) -> str:
    bundle = json.dumps(findings, ensure_ascii=False, indent=2)
    resp = client.messages.create(
        model=ORCH_MODEL, max_tokens=2048,
        messages=[{"role": "user", "content":
            f"原始研究問題：{question}\n\n"
            f"以下是各子問題的獨立調查結果（JSON，含 confidence）：\n{bundle}\n\n"
            f"請彙整成一份報告：先給總結論，再分點說明（附證據），"
            f"並用獨立段落【明確指出彼此衝突或 confidence=low 的缺口】，"
            f"不要假裝沒有缺口、也不要把 low-confidence 的內容講得像定論。"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")

# ---------- orchestrator：把四段串起來，附進度（Ch 28） ----------

def research(question: str) -> str:
    print("[orchestrator] 規劃中…")
    subs = make_plan(question)
    print(f"[orchestrator] 拆成 {len(subs)} 個子問題：")
    for i, q in enumerate(subs, 1):
        print(f"  {i}. {q}")

    print(f"[orchestrator] 並行派出 {len(subs)} 個 subagent…")
    findings = run_subagents(subs)
    for f in findings:
        print(f"  ✓ [{f['confidence']:^6}] {f['subquestion']}")

    print("[orchestrator] 彙整報告…\n")
    return synthesize(question, findings)

if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "這個專案的權限模型是怎麼設計的？"
    print(research(q))
```

**解答說明**：

- **拆題 = planning + schema 強制（Ch 28 + Ch 32）**：`make_plan` 用 `tool_choice` **強制呼叫** `submit_plan`、`strict: True` **強制 schema**，所以拿到的 `subquestions` 是乾淨的 list，不用 parse 自由文字。prompt 明說「彼此獨立、可並行、不依賴其他子問題的答案」——**這是後面能並行的前提**。獨立性拆錯（子問題 B 要等 A 的答案），並行就會出錯結果。
- **context 隔離是 subagent 的靈魂（Ch 26）**：`research_subagent` 第一件事是建**自己的** `messages`，裡面**只有那個子問題**，完全不含 orchestrator 的歷史，也不含別的 subagent 在查什麼。它內部 `search_docs` 翻了幾次、看了多少雜訊，全留在它自己的 context 裡——**穿過邊界交回 orchestrator 的，只有一份結構化 finding**。這就是 Ch 26 反覆講的：subagent 幫主 agent「擋掉」一大堆中間 token，主 context 只收結論。
- **結構化回報讓彙整變可能（Ch 32）**：finding 是固定 schema（answer/evidence/confidence）。`strict: True` 保證的是**形狀**——三個欄位一定都在、`confidence` 一定是那三個 enum 值之一——所以 `run_subagents` 收回來的是一排乾淨 dict，`synthesize` 能直接序列化餵回模型。但要分清楚：**strict 只管結構、不管內容**——它**不保證** evidence 真的來自 `search_docs`、不保證引用為真、也不保證 `confidence` 校準得準。它的價值是「逼模型**一定要明確表態**信心等級」這個欄位存在且合法，誠不誠實仍要靠 prompt 引導、靠 evidence 可被 synthesize/人事後檢查。即便如此，有這個強制存在的 enum 欄位，缺口才**有機會**在彙整時被看見（而不是被一段自信的散文糊過去）。
- **真的並行（Ch 27 + Ch 31）**：`ThreadPoolExecutor` 讓多個 subagent **同時**打 API。它們是 I/O-bound（大半時間在等 API 回應），thread 就夠、GIL 不是瓶頸（Ch 31）。`as_completed` 邊完成邊收。**每個 subagent 一份自己的 messages**，所以多 thread 不會互相踩——沒有共用可變狀態，這是並行安全的關鍵。
- **優雅降級（Ch 27）**：`run_subagents` 包 `try/except`、subagent 內部也有 API 錯誤與回合上限的保險——任何一條查爆了，回一個 `confidence=low` 佔位 finding，**整批照樣完成**。一個 subagent 不該拖垮整個研究。
- **彙整是一次 LLM 呼叫，不是字串拼接（Ch 27）**：`synthesize` 把 findings 餵回**強模型**，要它調和、判斷衝突、**用獨立段落點出 low-confidence 缺口**。這一步才是 orchestrator-worker 的「收斂」——把並行查到的碎片，整合成一個有總結論、也誠實標注不確定的整體。
- **成本分層（Ch 26/37）**：subagent 用 `haiku`（抽取型、量大）、orchestrator 用 `opus`（拆題與彙整、要判斷力）。這是 multi-agent 省錢的標準做法——別讓便宜就能做的調查也燒貴模型。
- **這就是 Anthropic research 系統的精簡同款**：lead agent 拆題 → 並行 subagent 各查一塊（隔離 context）→ 回壓縮結論 → lead 彙整。你做的這套，理解了就懂那篇 [engineering blog](https://www.anthropic.com/engineering/built-multi-agent-research-system) 為什麼那樣設計，以及它為什麼說「multi-agent 燒 token 燒得兇、要值得才用」。
- **這版刻意省略的東西（生產要補）**：(1) `search_docs` 是樸素關鍵字比對，真實研究要換成語意檢索（embedding/RAG）或真 web search，並處理「查到太多」的排序與去重。(2) 沒有跨 subagent 的**去重與交叉驗證**——兩個 subagent 可能查到同一份證據或互相矛盾的事實，更完整的版本會在彙整前做一輪事實核對。(3) 沒有**second-pass**：真實系統發現缺口（太多 low）時會**再派一輪** subagent 補查，而不是一次就交。(4) 沒有把中間結果落盤（Ch 39 可重現）——長研究中途崩潰就得從頭來。(5) **subagent loop 沒檢查 `stop_reason`**：最後一回合強制 `submit_finding` 時，若 `max_tokens=1024` 把 tool 的 `input` **截斷**，`stop_reason` 會是 `max_tokens`、`tool_use.input` 可能不完整。生產版應像[練習 A](./practice-a-mini-agent-loop.md) 那樣分流 `stop_reason`，遇 `max_tokens` 就**提高上限重試**該回合，而不是直接當失敗。這裡為聚焦主線而省略。

</details>

## 測試用例

先在 `corpus/` 放幾個 `.txt` 當語料（可把前面幾章重點貼進去，或指到你的程式碼庫）。

| 步驟 | 操作 | 預期行為 | 驗證了什麼 |
|---|---|---|---|
| 1 | 跑單一 `research_subagent("...")` | 它 `search_docs` 幾次後回 `submit_finding`（結構化 dict） | subagent 隔離 loop + 結構化回報（Ch 26/32） |
| 2 | `make_plan("研究 X")` | 回 3–6 個**彼此獨立**的子問題 list | 拆題 + schema 強制（Ch 28/32） |
| 3 | 在 `run_subagents` 計時，對比改成 for 迴圈序跑 | 並行版**明顯較快**（≈ 最慢那個 vs 全部相加） | 真的並行（Ch 27/31） |
| 4 | 給一個**語料裡沒有**的子問題 | 該 subagent 回 `confidence=low`，不硬掰 | 誠實標不確定（Ch 32 enum） |
| 5 | 故意讓一個 subagent 丟例外（如壞掉的 query） | 該條回 low 佔位，**其他照樣完成**、整批不掛 | 優雅降級（Ch 27） |
| 6 | 跑完整 `research()`，看報告 | 報告**明確點出** low-confidence 缺口，不是全當定論 | 彙整調和 + 標缺口（Ch 27） |
| 7 | 檢查每個 subagent 的 messages | 都**不含** orchestrator 歷史、彼此不共用 | context 隔離 + 並行安全（Ch 26/31） |

第 3、6、7 步是這份練習的核心驗收——並行真的有效、彙整真的調和並標缺口、context 真的隔離。

## 延伸挑戰（加分）

1. **second-pass 補查（Anthropic research 系統的關鍵）**：彙整後若 `low` 太多或發現衝突，orchestrator **自動再派一輪** subagent 針對缺口補查，再彙整。體會「研究是迭代的、不是一次就交」。
2. **跨 subagent 事實核對**：彙整前先讓一個「核對 agent」掃所有 finding，標出**互相矛盾**的結論交給人或再查，而不是讓 synthesize 自己硬調和。
3. **動態並行度 + 限流**：子問題很多時別一次開幾十個 thread 打爆 rate limit——用 `ThreadPoolExecutor(max_workers=K)` 限並行度，並對 429 退避重試（Ch 31/37）。
4. **subagent 帶多模態（Ch 33）**：語料含圖表截圖時，給 subagent 一個「讀圖」工具，讓它能 `search_docs` 之外也「看圖」找證據——把 Part 4 最後一塊也接進來。
5. **可重現與中途續跑（接 Ch 39）**：把計畫與各 finding **落盤成 JSON**，崩潰後能從「已完成的 subagent」續跑，不用整個重來。
6. **真 web search**：把 `search_docs` 換成真的網路搜尋工具，加上來源 URL 進 `evidence`，做成一個真能用的研究助手——並親身體會「subagent 抓回的網頁內容會把 context 撐爆，更需要蒸餾」（Ch 26）。

## 自我檢核

- [ ] 我的每個 subagent 都有**自己的** messages，不含 orchestrator 歷史、彼此不共用
- [ ] 我能解釋為什麼 subagent 要回**結構化 finding** 而不是自由文字（orchestrator 怎麼用、Ch 32）
- [ ] 我的 subagent 真的**並行**跑（能在計時上看出來），且知道為什麼用 thread 而非 process（Ch 31）
- [ ] 我的拆題保證子問題**彼此獨立**——能說出若不獨立，並行會出什麼錯
- [ ] 我的彙整是一次 **LLM 呼叫**會調和衝突、標出 low-confidence 缺口，而不是字串拼接
- [ ] 我的流程在單一 subagent 失敗時**優雅降級**，不會整批掛掉
- [ ] 我能說清楚這個工作流分別用上了 Ch 26/27/28/31/32 的哪條原則，以及它跟 Anthropic research 系統的對應

做完這題，你已經能把 Part 4 的「多 agent 協作」**整套**用到一個真實的研究需求上——這正是把「聽過 orchestrator-worker」變成「會搭一個 multi-agent 工作流」的分水嶺。

至此 Part 4（進階能力）全部結束：subagent、多 agent 編排、planning/todo、skills、hooks、背景任務、結構化輸出、多模態——agent「**能做很多事**」的能力面補齊了。但「能做」不等於「做得好、做得穩、做得安全」。Part 5 換一副眼鏡：怎麼**知道** agent 變好還變壞（eval）、怎麼**看見**它在幹嘛（observability）、怎麼防它被**惡意輸入劫持**（prompt injection）、怎麼把它跑得**又快又便宜**、怎麼**debug** 一個會自己亂跑的系統。

→ [Ch 34 Eval：怎麼知道 agent 變好還變壞](./34-eval.md)
