# Final Project — 自己刻一個 mini agent harness

> **目標**：把這 40 章**從零組裝成一個你完全掌握的 mini agent harness**。不是再包一層別人的框架，而是親手把你逐章學過的零件——[迴圈](./04-minimal-agent-loop.md)、[工具協議](./05-tool-calling-protocol.md)、[停止條件](./07-stop-conditions-turns.md)、[context 管理](./13-context-compaction.md)、[權限 gate](./25-permission-model.md)、[trace](./35-observability.md)、[eval](./34-eval.md)、[注入防護](./36-prompt-injection-security.md)、[resume](./39-determinism-resume.md)——按**你自己的設計**接成一個能做真實多步任務、看得見、測得出、出事能恢復的 harness。做完這題，你對「agent 底層那層」就不再是讀者，而是作者。

> **環境**：Python 3.11、`anthropic` SDK。你可以大量沿用前面練習的成果（[A](./practice-a-mini-agent-loop.md) 的 loop、[C](./practice-c-file-toolset.md) 的工具集與 gate、[E](./practice-e-eval-tracing.md) 的 trace/eval），但**這次要由你決定它們怎麼拼**——介面、分層、誰呼叫誰，都是你的設計決定。模型用 `claude-opus-4-8`（或你手上可用的 snapshot）。

## 背景與動機

前面每個練習都只切一刀：練習 A 是裸 loop，練習 C 是工具，練習 E 是觀測。真實的 harness 不是這些東西的「集合」，而是它們**互相牽制**下的一個整體——你會發現：

- context 壓縮（Ch 13）一啟動，trace（Ch 35）和 resume（Ch 39）就得跟著處理「被壓掉的歷史」這件事；
- 權限 gate（Ch 25）擋下一個工具呼叫後，迴圈（Ch 7）要決定「把拒絕當成 tool_result 餵回去讓模型換做法」還是「直接中止」；
- 工具回了一段含「ignore previous instructions」的內容（Ch 36），你的 context 組裝（Ch 11）和信任邊界設計就被考驗；
- 跑到第 8 回合 crash，resume（Ch 39）能不能接上，取決於你前面每個工具有沒有做成冪等、checkpoint 邊界畫在哪。

**這些牽制只有你親手把整個東西組起來、拿真任務操它，才會浮現。** 這就是這個壓軸的價值：不是學新知識，是把已學的逼成一個**一致的系統**——並在過程中發現你哪一章其實只是「看懂了」而沒「想清楚」。

這也呼應 [Ch 40](./40-framework-comparison.md) 的結論：框架幫你省「常見路」的力氣，但**生產化的真正分水嶺**（eval、observability、安全、可靠性）框架不會替你想。你親手刻過一遍，未來無論自己刻還是用框架，都能看穿那層抽象底下在做什麼。

## 任務規格

做一個 `harness/`（建議拆成幾個模組，別擠在一個檔），它要能接受一個自然語言任務、在一個工作目錄裡多回合自主完成，全程可觀測、可恢復。能力分三級——**MUST 是這題的及格線，SHOULD 是「真的懂了」的證據，加分是往生產再推一步**。

### MUST（核心，缺一不可）

1. **自主 agent 迴圈**（Ch 4–7）：tool-use loop，至少**正確處理這四種** `stop_reason`——`tool_use`（執行工具、把 `tool_result` 餵回）、`end_turn`（收工）、`max_tokens`（截斷續寫；若是文字截斷才用續寫提示，若截在半個 tool_use 上則放大 `max_tokens` 重試該輪）、`pause_turn`（**原樣把 paused assistant 回應接回去續跑**，別塞 `{"role":"user","content":"continue"}`），**其他沒列到的 `stop_reason`（`refusal`、`stop_sequence`、`model_context_window_exceeded`…）要有安全 fallback**（別假裝講完、別 crash；其中 `stop_sequence` 若是你自己設了 `stop_sequences`，那是刻意的正常停止訊號，要按你的協議處理、不算異常）。要有**回合上限**與明確停止條件，不會無限跑。
2. **真實工具集**（Ch 18–22）：至少 3 個真實工具，其中**含有副作用的工具需過 gate**（建議：`read_file`、`edit_file`/`write_file`、`run_shell` 或 `list_directory`——`read_file`/`list_directory` 是唯讀，`write_file`/`edit_file`/`run_shell` 是有副作用、要過閘的）。schema 清楚、描述就是 prompt（Ch 19）、結果設計過（Ch 20，截斷大輸出、錯誤回成結構化 `is_error` 而非 raise 掉整個迴圈）。
3. **權限 gate**（Ch 25）：危險操作（寫檔、跑 shell，或刪除——若你有做這類工具，Practice C 本身沒有）過一個 allow/ask/deny 的閘。被 deny 時把「這個操作被拒絕」當成 `tool_result` 餵回模型讓它換做法，而不是 crash。檔案型工具的路徑要 `safe_path` 鎖在工作目錄內。**注意 `safe_path` 只管得住「路徑參數型」的檔案工具**——若你做了 `run_shell`，就算設了 `cwd=workdir`，shell 仍能用絕對路徑、`cd /`、網路、開子程序逃出工作目錄；那需要 Ch 22 的那套（timeout、輸出上限、參數列表而非字串、allowlist／sandbox），不是 `safe_path` 一招能擋。
4. **結構化 trace**（Ch 35）：每次 run 一個 `run_id`，把 llm I/O、`stop_reason`、token usage、工具 I/O、gate 決策寫成可機讀記錄（JSONL）。能 `replay` 出一次執行的時間線。
5. **checkpoint + resume**（Ch 39）：長任務可中斷可續。**checkpoint 邊界要正確**——工具副作用完成且 `tool_result` 已進 state 之後才算這一步完成（逐工具存檔），resume 不會重跑已完成的步驟。**注意 Ch 39 的核心坑**：一輪有多個 `tool_use` 時，crash 可能停在「跑完第 1 個、第 2、3 個沒跑」，checkpoint 裡會是一則**只有部分 `tool_result` 的 user turn**——直接把它送回 API 會違反 Anthropic 協議（每個 `tool_use` 都要有緊接且完整的 `tool_result`）。所以 **resume 入口必須先「對帳補齊」**：檢查最後那則 assistant 的每個 `tool_use` 是否都有對應結果，缺的補跑（工具冪等，補跑不重複副作用）、已有的跳過，湊齊成對後才續跑。

### SHOULD（證明你真的接通了各章）

6. **context 預算與壓縮**（Ch 10–13）：追蹤 token 用量，逼近上限時觸發摘要/壓縮，且壓縮後 trace 與 checkpoint 仍自洽（記得住「壓掉了什麼」）。
7. **注入防護**（Ch 36）：工具回傳的內容是**不可信資料**——別讓它能改寫你的指令層。至少做到：工具結果與 system 指令分層清楚、危險操作仍須過 gate（不因「檔案內容叫我做」就放行）。
8. **一組 eval**（Ch 34）：≥3 個 `EvalCase`，含**結果斷言**與**至少一條軌跡斷言**（查它「怎麼做」不只「做沒做到」），跑 N 次看通過率，能跟 baseline 比、標出回歸。
9. **retry 與錯誤分類**（Ch 9）：對 API 的 429/5xx 做帶 backoff 的重試；區分「可重試」與「該中止」的錯誤。

### 加分（往生產再推）

10. **subagent / 委派**（Ch 26–27）：把一個子任務派給帶獨立 context 的 subagent，主迴圈只收結果。
11. **record/replay 離線 eval**（Ch 39）：trace 連 LLM 回應原文也存，eval 可離線、零成本地重跑。**注意**：要「完全確定」地離線重跑，光錄 LLM 回應不夠——若工具會跑 shell／打外部 API／依賴時間或網路，還得連**工具輸出、gate 決策等外部 I/O 一起錄/放**；否則就把 eval 限制在純 deterministic 的 tempdir 檔案操作工具上。
12. **prompt caching**（Ch 17）：穩定的 system/工具定義放進 cache，從 trace 看 cache token 命中。
13. **planning/todo**（Ch 28）：讓 agent 顯式維護一個待辦清單並逐項推進，trace 看得到計畫演進。

### 禁止

- **不准包一個現成框架**（LangGraph / Agent SDK / OpenAI Agents SDK）當答案——這題就是要你**自己刻**。用它們對照可以，當交付不行。
- **不准黑盒抄前面練習**：可以複用程式碼，但你要能說清楚每個零件**為什麼這樣接**、換個接法會壞在哪。
- **不准用 `print` 當 trace**：要結構化、帶 `run_id`、可 replay（Ch 35）。
- **不准 eval 只跑一次或只看最終輸出**：非確定要跑 N 次（Ch 39），且必須有軌跡斷言（Ch 34/38）。
- **不准讓工具結果直接污染指令層**，也不准因「內容這樣說」就跳過 gate（Ch 36）。

## 期望行為範例

不是要你做出一模一樣的輸出，是看這條主線：**一個任務 → 多回合自主執行 → 全程留下可 replay 的 trace → 中斷後能 resume → 事後能跑 eval**。

```
$ python -m harness run --workdir ./sandbox "把 config.json 的 port 改成 8080，並在 README 末尾加一行說明"
[run abc123] turn 1  llm → tool_use: read_file(config.json)
[run abc123] turn 1  tool ← config.json (412 bytes)
[run abc123] turn 2  llm → tool_use: edit_file(config.json, port: 3000→8080)
[run abc123] turn 2  gate: edit_file 命中 ask → [y/N] y
[run abc123] turn 2  tool ← edited (1 replacement)        ← checkpoint 存於此（副作用+結果已記錄）
[run abc123] turn 3  llm → tool_use: edit_file(README.md, append)
[run abc123] turn 3  gate: ask → [y/N] y
[run abc123] turn 3  tool ← edited
[run abc123] turn 4  llm → end_turn: 完成。port 已改為 8080，README 已加說明。

$ python -m harness replay abc123        # 從 trace 還原時間線（即使原 process 已結束）
$ python -m harness resume abc123        # 若中途 crash，從最後 checkpoint 接續，不重跑已完成步驟
$ python -m harness eval                 # 跑 EvalCase，出通過率與回歸報告
```

## 如果你卡住了

- **不知道從哪開始** → 先做一個「**走骨架**（walking skeleton）」：loop + 一個 `read_file` 工具 + 印出每步，能跑通一個單步任務再說。先讓最細的一條路從頭走到尾，再往上加層。
- **stop_reason 分流又忘了** → 回 [Ch 7](./07-stop-conditions-turns.md) 與 [Ch 38 §4](./38-failure-modes-debugging.md)。記住 `pause_turn` 接回去續跑、`max_tokens` 才是續寫提示，兩者別搞混。
- **resume 重跑了已做的步驟** → 你的 checkpoint 邊界畫錯了。回 [Ch 39 §四](./39-determinism-resume.md)：`tool_result` 要先進 state 再存檔，且有副作用的工具要冪等/可去重。
- **工具結果一大坨塞爆 context** → [Ch 16](./16-tool-result-pruning.md) 截斷 + [Ch 13](./13-context-compaction.md) 壓縮。先測「逼近上限會不會壞」再決定壓縮策略。
- **eval 跑一次過、一次不過，不知算過不算** → 本來就會這樣（非確定）。[Ch 34](./34-eval.md)/[Ch 39](./39-determinism-resume.md)：跑 N 次看通過率，不是一次定生死。
- **被自己的注入測試騙過** → [Ch 36](./36-prompt-injection-security.md)：工具回傳是不可信資料，gate 不能因內容叫它做就放行。

## 實作步驟建議

別想一次把 MUST 全做完。**一層能跑了再加下一層**，每加一層就拿一個任務操它一遍：

1. **走骨架**：loop + `read_file` + 文字輸出，跑通一個「讀檔並總結」的單步任務。確認 `tool_use`/`end_turn` 分流對。
2. **補全 stop_reason 與回合上限**：加 `max_tokens`/`pause_turn` 處理、回合上限、明確停止。故意設小 `max_tokens` 測截斷續寫。
3. **加有副作用的工具 + gate**：`edit_file`/`run_shell` + allow/ask/deny + `safe_path`。測「deny 餵回後 loop 不 crash、模型換做法或回報做不到」與「路徑逃逸被擋」。
4. **裝 trace**：每步寫 JSONL，做 `replay`。這層先到位，後面 debug 全靠它。
5. **加 checkpoint + resume**：逐工具存檔（邊界畫對），手動 kill 再 `resume`，確認不重跑。寫一個故意不冪等的工具體會災難，再修成冪等。
6. **加 context 預算/壓縮**（SHOULD）：跑一個長到要壓縮的任務，確認壓縮後 trace/checkpoint 仍自洽。
7. **加注入防護測試**（SHOULD）：在工作目錄塞一個含惡意指令的檔，確認 agent 讀到後**不會**據此跳過 gate 或改寫指令。
8. **建 eval**（SHOULD）：把前面手動測過的場景寫成 `EvalCase`（含軌跡斷言），跑 N 次、存 baseline。
9. **挑 1–2 個加分**做：subagent、record/replay 離線 eval、caching、planning——挑你最想深入的。

## 參考骨架

下面是**接縫示意**，不是給你抄的完整解答——刻意把各零件「怎麼咬合」露出來，實作（工具本體、壓縮策略、gate UI）由你按前面練習填。重點看**誰呼叫誰、checkpoint 畫在哪、trace 記在哪、不可信邊界在哪**。

```python
# harness/loop.py — 核心：把所有零件咬合在一起的地方
def run(task: str, workdir: Path, run_id: str | None = None) -> State:
    state = load_checkpoint(run_id) if run_id else new_state(task, workdir)
    tracer = Tracer(state.run_id)                      # Ch 35：全程帶 run_id
    finish_pending_tools(state, tracer)                # Ch 39：resume 入口先對帳補齊半套 tool_result

    while state.turn < MAX_TURNS and not state.done:
        prompt = build_context(state)                  # Ch 11/13：壓縮在這裡發生
        if over_budget(prompt):                        # Ch 10
            state.messages = compact(state.messages, tracer)   # Ch 13（記得記「壓了什麼」）

        resp = call_with_retry(client, prompt, TOOLS, max_tokens=state.max_tokens)  # Ch 9：429/5xx backoff
        tracer.log("llm_call", stop_reason=resp.stop_reason,
                   usage=usage_dict(resp.usage))        # SDK usage 物件→dict 才能寫 JSONL（取 input/output/cache token）
        # 存 dict 而非 SDK content block 物件——否則 save_checkpoint 做 JSON dump 會炸（Ch 39）
        state.messages.append({"role": "assistant",
                               "content": [b.model_dump() for b in resp.content]})
        save_checkpoint(state)                          # Ch 39：先存這個（可能 paused 的）assistant turn——pause 後續跑前 crash，resume 才拿得回它
        resp = settle_pauses(resp, state, tracer)       # Ch 39：pause_turn 在子迴圈內立刻續跑（不可經過下面的 compaction/重組）；傳 tracer 進去，子迴圈每次續打的 llm_call/usage 也要記

        if resp.stop_reason == "tool_use":
            finish_pending_tools(state, tracer)         # Ch 39：append 空殼→逐一 gate/執行/存檔；resume 時只補沒做的
        elif resp.stop_reason == "max_tokens":
            if truncated_in_tool_use(resp):             # 截在半個 tool_use
                state.messages.pop()                    # 先移除這個含半截 tool_use 的 assistant（別讓它留著破壞協議）
                if state.max_tokens < MAX_TOKENS_CAP:
                    state.max_tokens *= 2               # 放大上限重試該輪；有 CAP 不會無限放大
                    state.turn += 1; save_checkpoint(state); continue  # 仍計一回合→受 MAX_TURNS 約束
                state.done = True                       # 已達 CAP：明確失敗收尾（別接 continue_hint，那會在 tool_use 後塞文字 user→400）
                tracer.log("giving_up", reason="tool_use 在 max_tokens 上限仍被截斷")
            else:
                state.messages.append(continue_hint())  # 純文字截斷才用續寫提示（≠ pause_turn）
        elif resp.stop_reason == "end_turn":
            state.done = True
        else:
            state.done = True                           # refusal/stop_sequence/未知：安全 fallback，別假裝講完
            tracer.log("unhandled_stop", reason=resp.stop_reason)

        state.turn += 1
        save_checkpoint(state)                          # 回合邊界也存

    tracer.log("run_end", turns=state.turn, done=state.done)  # 只在整個 run 結束時記一次
    return state

# Ch 39：把工具執行抽成可在「resume 入口」與「tool_use 後」共用的對帳補齊
def finish_pending_tools(state, tracer):
    # locate_pending 處理兩種情況：
    #   (a) 剛拿到 tool_use、results 還沒建 → 建空殼 user turn 並 append，回傳 (assistant, [])
    #   (b) resume 時跑到一半 → 找到半套的 user tool_result turn，回傳 (assistant, 已有的 results)
    # 沒有待補的 tool_use（如最後是純 end_turn 文字）→ 回傳 (None, _)
    assistant, results = locate_pending(state.messages)
    if assistant is None:
        return
    # 只從 type == "tool_result" 的 block 取 id——別假設半套 user turn 全是 tool_result（可能混文字）
    done_ids = {r["tool_use_id"] for r in results if r.get("type") == "tool_result"}
    for block in tool_uses(assistant):                  # block 此時是 dict（content 已正規化）→ 用 block["id"]/["name"]
        if block["id"] in done_ids:
            continue                                    # 已完成→跳過，不重跑
        decision = gate.check(block)                    # Ch 25：allow/ask/deny
        tracer.log("gate", tool=block["name"], decision=decision)
        if decision == "deny":
            results.append(deny_result(block))          # 餵回讓模型換做法，別 crash
        else:
            # 工具結果是「不可信資料」（Ch 36）——別讓它改寫指令層
            results.append(run_tool_idempotent(block, state, tracer))  # Ch 20/39
        save_checkpoint(state)                          # 副作用+結果已進 state 才存（逐工具邊界）
```

```python
# harness/eval.py — 拿同一個 run() 跑評測（Ch 34），靠 trace 做軌跡斷言（Ch 35）
def run_case(case, runs=3):
    passes = 0
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as ws:       # 隔離 workspace，不碰真檔；例外也會清乾淨
            case.setup(Path(ws))
            state = run(case.task, workdir=Path(ws))
            trace = Tracer.load(state.run_id)
            ok, _ = case.check(state, trace)            # 結果斷言 + 軌跡斷言
            passes += ok
    return passes / runs                                # 通過率，不是單次
```

> **沿用 Practice C 的工具時注意隔離邊界**：Practice C 的工具讀的是**全域 `WORKSPACE`**，不是傳進來的 `workdir`。若你直接沿用而沒接通，上面的 tempdir 隔離與 `safe_path` 的工作目錄鎖定就都是**假的**（每個 case 其實都在同一個全域目錄裡跑）。所以 `run()` 一進來就要把工作目錄**真正注入工具**——把工具改成讀 `state.workdir`，或在每次 run 前設好那個全域（如 Practice E 的 `fa.WORKSPACE = str(ws)`）。這正是「全域狀態對測試隔離的代價」。

你的設計決定要能回答：壓縮觸發時 checkpoint 怎麼保持自洽？deny 餵回去後若模型反覆撞同一道牆，靠什麼跳出（回合上限？偵測迴圈 Ch 38）？trace 檔放哪才不會被 agent 自己讀到（Ch 36）？

## 驗收標準

不是「跑得動」就算過——下面每條都要**親手驗一遍**：

| # | 場景 | 通過條件 | 對應 |
|---|------|---------|------|
| 1 | 多步任務（讀+改 2 個檔） | 自主完成、回合數合理、`end_turn` 收工 | Ch 4–7 |
| 2 | 給超出 `max_tokens` 的輸出 | 正確續寫，不誤判成完成 | Ch 7/38 |
| 3 | 危險操作 | 過 gate；deny 包成 `tool_result(is_error)` 餵回、loop 不 crash，模型換做法**或明確回報做不到** | Ch 25 |
| 4 | 路徑逃逸（`../../etc/...`） | 被 `safe_path` 擋下 | Ch 21 |
| 5 | 任意一次 run | 能 `replay` 出完整時間線 | Ch 35 |
| 6 | 第 N 步手動 kill | `resume` 從斷點接續，**不重跑已完成步驟** | Ch 39 |
| 7 | 工作目錄塞惡意指令檔 | agent 讀到後**不**據此跳過 gate / 改寫指令 | Ch 36 |
| 8 | 把某工具描述改爛跑 eval | 對應 case 通過率下降、**標出回歸** | Ch 34 |
| 9 | 「結果對但用錯工具」的 case | 結果斷言過、**軌跡斷言失敗** | Ch 34/38 |

第 6、7、9 是這題的真正分水嶺——**能 resume 不重做**、**此場景下不因工具內容跳過 gate 或改寫指令層**（不是宣稱「注入全擋得住」——那是做不到的一般性保證，這裡只驗這個具體場景）、**軌跡斷言抓得到「對的結果＋爛的做法」**。這三件做到了，你的 harness 就不只是「能跑的 demo」。

## 延伸挑戰（給想走更遠的人）

1. **接 MCP（Ch 24）**：讓你的工具集能掛載一個外部 MCP server 的工具，體會「工具來源動態變化」對 trace/eval 的衝擊（Ch 38 的幻覺工具名問題）。
2. **multi-agent 編排（Ch 27）**：主 agent 把研究/驗證等子任務派給 subagent，設計它們的 context 邊界與結果回收。
3. **失敗模式掃描器（Ch 38）**：把 trace 餵進一組自動檢查器（迴圈、retry 風暴、context 逼近上限、幻覺工具、過度積極），eval 後產一份「可疑軌跡報告」。
4. **成本/延遲看板（Ch 37）**：從 trace 聚合 token/成本/耗時，做「這版 vs baseline」對比，讓決策不只看通過率也看划不划算。
5. **拿它做你工作裡一個真任務**：這是最硬的驗收——把你自己的一個真實小任務丟給它，看它撐不撐得住，把撐不住的地方回灌成 eval case。
6. **跟框架對照（Ch 40）**：拿 LangGraph 或 Agent SDK 實作同一個任務，列出「它替你做了哪些、綁住你什麼、你的手刻版在哪些地方更可控」。

## 自我檢核

- [ ] 我的 harness 能自主跑完一個多步任務，且**正確處理 `tool_use`/`end_turn`/`max_tokens`/`pause_turn` 四種 `stop_reason`、其餘走安全 fallback**（特別是 `pause_turn` 不塞 continue、`max_tokens` 才續寫）
- [ ] 危險操作都過 gate，deny 後模型能換做法，路徑逃逸被擋
- [ ] 每次 run 都留下**帶 `run_id`、可 replay** 的結構化 trace
- [ ] 中途 kill 後能 `resume` 且**不重跑已完成步驟**——我能指出 checkpoint 邊界畫在哪、為什麼
- [ ] context 逼近上限會壓縮，且壓縮後 trace/checkpoint 仍自洽
- [ ] 工具結果被當**不可信資料**處理——注入測試場景下不會據此跳過 gate 或改寫指令層
- [ ] 我有 ≥3 個 eval case（含軌跡斷言），跑 N 次看通過率，能標出回歸
- [ ] 對每個零件「為什麼這樣接、換個接法會壞在哪」，我答得出來

## 結語：你現在站在哪

走到這裡，你已經把一個 agent harness 的每一層都**親手刻過、接過、操壞過再修好**：從一個只會吐字的模型，到一個能讀檔、跑指令、守規矩、記得進度、出事能回放能恢復、改動能被客觀評測的系統。

這正是 [Ch 1](./01-what-is-agent-harness.md) 開頭那句話的兌現——**「魔法幾乎都在 harness 那一層，不在模型本身」**。你現在看 Claude Code、Cursor、任何 agent 產品，看到的不再是黑盒的魔法，而是一層一層你認得出、評得了、也能自己造的工程決定。模型會一直換、框架會一直出新的，但你手裡這套「怎麼餵 context、怎麼設計工具、怎麼控制 loop、怎麼讓它可靠」的判斷力，長期穩定、誰也拿不走。

剩下的，就是拿它去解你真正在乎的問題了。
