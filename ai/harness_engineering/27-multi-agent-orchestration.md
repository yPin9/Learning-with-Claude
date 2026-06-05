# Ch 27 — Multi-agent 編排

> **目標**：Ch 26 教你「派一個 subagent」。本章把視角從「委派一件事」拉到「**協調一群 agent**」：怎麼把一個大任務拆成子任務、並行派出去、再把結果彙整回來。讀完你能說出幾種經典的編排型態（orchestrator-workers、prompt chaining、routing、parallelization、evaluator-optimizer）、編排真正的難點不在「派」而在**拆解 / 並行 / 彙整 / 容錯**這四件事、為什麼 worker 之間**通常不直接對話**（星狀拓撲）、以及多 agent 什麼時候**反而比單一 agent 差**（緊耦合、需要共享 context 的任務）。

> **環境**：Python + Anthropic SDK，延續 Ch 26 的 `run_subagent`。並行用標準庫 `concurrent.futures`。**你正在用的這個 session 本身就是個 orchestrator**——當它「在單一訊息裡同時派出多個 Agent 工具」時，做的正是本章的 fan-out。本章在拆解那個動作底下的工程。「workflow vs agent」「orchestrator-workers」等名詞沿用 Anthropic〈Building Effective Agents〉的定義。

## 為什麼需要這個？一個 subagent 解決不了「一群」的問題

Ch 26 的 `dispatch_agent` 讓主 agent 能把**一件**髒活外包出去。但真實任務常常是「**一堆**可以同時做的髒活」：

- 「調查這五個競品的定價策略」→ 五個獨立的調查，天生可平行。
- 「把這份需求拆成研究 → 撰寫 → 審稿三階段」→ 一條流水線，每段需要不同專長。
- 「這個問題我不確定哪種解法對，跑三種各自評估」→ 同一任務多跑幾次再挑最好的。

這些都不是「派一個」能漂亮處理的。你需要一個**協調者（orchestrator / lead agent）**：它負責**拆**（把大任務切成子任務）、**派**（把子任務發給多個 worker，可能並行）、**收**（等 worker 回來）、**併**（把零散結果彙整成一個連貫的答案）。Ch 26 給了你「worker」這塊積木；本章教你怎麼把一堆積木**組成一個系統**。

關鍵心態轉變：**編排的難點幾乎都不在「怎麼派」**（那 Ch 26 講完了），而在**拆得好不好、並行怎麼跑、結果怎麼併、有人掛了怎麼辦**。這四件事才是本章的肉。

## 先建立直覺：orchestrator 是「PM 帶一個團隊」，不是「一群人亂講話」

Ch 26 把主 agent 比作外包給一個工程師的 PM。現在這個 PM 帶的是**一整個團隊**。但這裡有個關鍵的組織設計問題：**團隊成員之間要不要直接溝通？**

```
   ❌ 網狀（mesh）：每個 worker 都跟其他人講話
      W1 ↔ W2 ↔ W3 ↔ W4   →  N 個人就有 ~N² 條溝通線，
       ↖──────┴──────↗         協調爆炸、誰都不知道全局、極難 debug

   ✅ 星狀（star）：所有溝通都經過 orchestrator
            ┌── W1
      Lead ─┼── W2     →  worker 之間【互不知道對方存在】，
            ├── W3        各自拿 brief、各自回報，Lead 統一彙整
            └── W4        簡單、可控、好 debug
```

**預設用星狀（或星狀 + 共享黑板），別用無管控的網狀。** worker 彼此不直接「對話」——它們是 Ch 26 那種「拿一張自給自足的工單、關門做事、回報結論」的外包。需要共享資訊時，不是讓它們你一句我一句地互傳訊息，而是透過 orchestrator、或一個**共享的工作區/產出物**（Ch 21 的檔案系統當 scratchpad，blackboard 模式）——Anthropic 的 research 系統就用外部記憶體/檔案讓部分產出**繞過 lead**以保真與效能，這跟星狀不衝突，重點是「結構化的共享」而非「自由聊天」。為什麼避免讓 agent 互相自由聊天？因為它會引爆溝通複雜度、製造無止境的來回、而且沒有任何一個 agent 掌握全局——debug 時你會瘋掉。

> **這份課程的協作模型就是星狀**：當主 agent 同時派出多個 Explore subagent，那些 subagent **彼此看不到對方**，各自把結果回報給主 agent，由主 agent 整合。沒有「Explore agent A 去問 Explore agent B」這種事。本章的星狀原則，你每天都在用。

## 一、五種經典編排型態（先有地圖）

Anthropic〈Building Effective Agents〉把常見型態整理得很清楚。先建立全景，後面幾節再深入最重要的 orchestrator-workers：

| 型態 | 長相 | 適合 |
|---|---|---|
| **Prompt chaining**（鏈式） | A 的輸出 → B 的輸入 → C…，一條線 | 任務能拆成**固定順序**的步驟（研究→寫稿→潤飾），每步可用不同 prompt/模型 |
| **Routing**（路由） | 一個 router 先分類，再丟給對應的專家 agent | 輸入**種類多**、各需不同處理（客服分流：退款/技術/帳務各一個專家） |
| **Parallelization**（並行） | 同時發多個、再彙整。兩種變體：**sectioning**（拆成獨立子任務）/ **voting**（同任務多跑幾次取共識） | 子任務獨立可平行（sectioning）；或想用多次取樣提高可靠度（voting） |
| **Orchestrator-workers**（協調者-工人） | lead **動態**拆任務、派 worker、彙整。拆幾個、拆什麼由模型當場決定 | 子任務**事先不知道有幾個 / 是什麼**的開放任務（深度研究、跨多檔重構） |
| **Evaluator-optimizer**（生成-評估迴圈） | generator 產出 → evaluator 批評 → generator 改 → …直到夠好 | 有明確品質標準、且「改了會更好」的任務（翻譯、寫作、有測試的程式） |

**一個重要區分（〈Building Effective Agents〉的核心論點）**：要精確的話，上面**五種全都是 Anthropic 所稱的 workflow**——它們都是「LLM 與工具被編排在預先定義好的程式碼路徑上」。Anthropic 把 "agent" 另外定義成更自主的東西：**LLM 自己動態規劃、用工具、看環境回饋多步執行，路徑不由你寫死**。差別在於「**控制流由誰決定**」：chaining/routing/parallelization 的流程完全是你寫死的；orchestrator-workers 雖然「拆幾個子任務」是模型**當場決定**的（所以更靈活），但它仍是一個 workflow pattern，**不等於**那種完全自主的 agent。重點結論不變：**生產系統絕大多數是 workflow**，因為可預測、好測、好除錯。別一上來就追求「一群完全自主的 agent 自由協作」——那通常是過度設計。先問：這任務能不能用一條寫死的 workflow 解決？能就別上更動態的編排。

## 二、orchestrator-workers 的解剖：拆 → 並行派 → 彙整

這是最強大、也最常被當成「multi-agent」代名詞的型態（Anthropic 的 research 系統就是它）。拆成三個動作，每個都有自己的工程難點。

**動作 1：拆解（decomposition）** — lead agent 把大任務切成子任務。這步**品質決定一切**：

- **拆太細** → worker 一大堆，協調成本與 token 成本爆炸（記得 Ch 26：多 agent 約 15 倍 token），而且很多子任務其實彼此重疊。
- **拆太粗** → 每個 worker 還是要做一大坨，失去並行的意義。
- **每個子任務都要寫一份自給自足的 brief**（Ch 26 第三節）——這是 lead 最重要的工作。brief 爛，worker 就瞎做。

**動作 2：並行派發（fan-out）** — 把子任務同時發給多個 worker。重點是**真的並行**，不是一個跑完再跑下一個（否則白白損失 multi-agent 唯一的速度優勢）。

**動作 3：彙整（synthesis）** — 收集所有 worker 的結論，併成一個連貫答案。彙整本身常常**又是一次 agent 呼叫**（給它所有 worker 的結果，請它消化矛盾、補缺口、寫成一份）。

```python
import concurrent.futures

def orchestrate(big_task: str, subtasks: list[str]) -> str:
    """orchestrator-workers：並行派發 + 容錯收集 + 彙整。
    subtasks 通常是 lead agent 自己拆出來的（這裡為了聚焦並行/彙整，先當成已拆好）。"""

    # ---- 動作 2：fan-out，真正並行跑多個 worker（每個都是 Ch 26 的 run_subagent）----
    # 兩層時間控制要分清楚：
    #   (1) 每個 worker【自己】的上限 = run_subagent 的 max_turns（Ch 26）。這才是真正能
    #       「停掉一個 worker」的機制——它跑滿回合就自己回傳。
    #   (2) orchestrator【等待】的上限 = 下面的 wait(timeout=)。它只決定「我最多等多久」，
    #       時限到了就不再等、用已完成的結果繼續。
    DEADLINE = 120
    results: dict[int, str] = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    future_to_idx = {
        pool.submit(run_subagent,
                    task=st,
                    system="你是調查專家，做完回一段精煉結論（Ch 26）。",
                    tools=READONLY_SCHEMAS, tool_funcs=READONLY_FUNCS,
                    model="claude-haiku-4-5-20251001", max_turns=12): i
        for i, st in enumerate(subtasks)
    }
    # wait 會等到「全部完成」或「整體時限到」——時限到時 not_done 是還沒回來的那些
    done, not_done = concurrent.futures.wait(future_to_idx, timeout=DEADLINE)
    for fut in done:
        i = future_to_idx[fut]
        try:
            results[i] = fut.result()
        except Exception as e:
            # ---- 動作 3 的前提：部分失敗要能降級，不是整批崩 ----
            results[i] = f"（這個子任務失敗了：{e}；後續彙整請略過或標註缺口）"
    for fut in not_done:
        i = future_to_idx[fut]
        fut.cancel()        # 只能取消「還沒開始跑」的；已在跑的取消不了（見下方說明）
        results[i] = "（這個子任務超過整體時限、放棄等待；彙整請標註此處有缺口）"
    # 注意：放棄等待的 worker thread 仍在背景繼續跑直到自己結束——Python 無法硬殺執行緒。
    # 真要「硬性中斷」一個失控 worker，得把它跑成可被 kill 的獨立 process（Ch 22 行程控制），
    # 或信賴它自己的 max_turns 上限會讓它收斂。這裡用 shutdown(wait=False) 不阻塞地關閉 pool：
    pool.shutdown(wait=False)
    # 但注意：shutdown(wait=False) 只是「這個呼叫不等」；ThreadPoolExecutor 的 worker 是
    # 非 daemon thread，所以程式【整個結束】時，直譯器仍可能卡著等那些背景 worker 跑完。
    # 這再次說明：要能真正放掉一個失控 worker，得靠獨立 process（Ch 22），不是 thread。

    # 依原順序組裝（done/not_done 是亂序的）
    ordered = [results[i] for i in range(len(subtasks))]

    # ---- 動作 3：彙整，通常再用一次 agent 呼叫消化所有 worker 結果 ----
    synthesis_input = f"原始任務：{big_task}\n\n各子調查結果：\n" + \
        "\n\n".join(f"[子任務 {i+1}] {r}" for i, r in enumerate(ordered))
    resp = client.messages.create(
        model="claude-opus-4-8", max_tokens=2048,
        system="你是彙整者。把以下多份子調查結果整合成一份連貫、不重複、標明缺口與矛盾的總結。",
        messages=[{"role": "user", "content": synthesis_input}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")
```

這段就是 orchestrator-workers 的骨架。注意三個工程要點都在裡面：**並行**（`ThreadPoolExecutor` 同時提交所有 worker）、**容錯**（已完成的包 `try/except`、超過整體時限的歸進 `not_done` 並標註缺口，一個掛掉/拖慢不拖垮其他）、**彙整**（最後用一次 Opus 呼叫消化）。

> **一個你一定要懂的 Python 真相：執行緒殺不掉。** `concurrent.futures` 沒有「停掉一個正在跑的 worker」的方法——`future.cancel()` 只能取消**還沒開始**的、`wait(timeout=)` 只是讓你**不再等**、`result(timeout=)` 也只是讓呼叫端拋 `TimeoutError`，三者**都不會中止那個 thread**。所以「逾時」在這裡的意思是「**orchestrator 放棄等它了**」，不是「那個 worker 被殺了」——它仍在背景跑、繼續燒 token 直到自己結束。這就是為什麼**每個 worker 自己的 `max_turns`（Ch 26）才是真正的上限**：它保證 worker 一定會在有限步內收斂。要能「硬性 kill」一個失控 worker，唯一可靠的做法是把它跑成獨立 process 再 `kill`（Ch 22 的行程控制）。別誤以為 `timeout` 參數會幫你停掉 worker。

> **為什麼 worker 用 thread 而不是 async？** 因為 worker 的工作本質是「等 API 回應」（I/O bound），thread 就夠了、也最好懂。要更高並行度或跟既有 async 程式整合，才換成 `asyncio` + `anthropic.AsyncAnthropic`。標準庫的 `ThreadPoolExecutor` 對「同時跑 5 個 subagent」這種規模綽綽有餘。

## 三、成本與延遲：算清楚再決定拆幾個

multi-agent 的帳跟單 agent 很不一樣，編排前要會算：

- **總成本 ≈ 所有 agent 的 token 總和**（lead 拆解 + N 個 worker + 彙整）。worker 越多越貴，且是**疊加**的。這就是 Ch 26 那個「15 倍」的來源。
- **總延遲 ≈ 並行分支裡最慢的那個 + 彙整時間**（不是全部相加——這正是並行的價值）。但這個公式有**三個前提**：(a) 所有 worker 真的同時開始（沒被 `max_workers` 排隊——若子任務數 > `max_workers`，多出來的會排隊分批跑，延遲就不再只是單一最慢分支）；(b) 沒撞到 API rate limit / 觸發大量重試；(c) 你有設「等待時限」。「最慢的那個」會被掉隊者（straggler）拖累：4 個 worker 3 個 10 秒回來、1 個跑 90 秒，你得等到第 90 秒（或等待時限 `DEADLINE` 到、放棄它）。所以要設**等待時限**（上面的 `wait(timeout=DEADLINE)`）並決定「時限到就放棄等、用其餘結果」——但記得放棄等待 ≠ 殺掉 worker（第二節）。
- **worker 數量要設上限**：別讓 lead 拆出 50 個 worker。實務上設個 `max_workers`（並行度）和「最多拆幾個子任務」的上限，保護成本與延遲。並行度上限也意味著子任務太多時會排隊，回頭影響上面的延遲公式。

一句話：**多 agent 拿「線性增長的成本」換「並行帶來的延遲下降」與「context 隔離」。** 這筆交換只在「子任務夠多、夠獨立、且值得那個成本」時才划算。

## 四、什麼時候**不該**上 multi-agent

這節跟 Ch 26 第五節同樣重要，因為 multi-agent 是最容易被過度使用的設計。Anthropic 自己的結論很直白：**多 agent 適合「可平行、廣度搜尋、且價值足以蓋過數倍成本」的任務；不適合需要緊密共享 context 或子任務互相依賴的任務。**

| 適合 multi-agent | 不適合（單一 agent 更好） |
|---|---|
| 子任務**獨立、可平行**（查多個來源、多個競品） | 子任務**緊耦合**、一步依賴上一步的中間狀態 |
| **廣度優先**的探索（research、蒐集） | 需要**全局共享 context**的連貫推理（一個 agent 看全貌更好） |
| 子任務之間**不太會互相踩到**（唯讀調查） | 會**互相衝突**（多個 agent 同時改同一份程式碼 → 編輯打架，Ch 21 並發） |
| 價值高到**值得 15 倍成本**（深度研究報告） | 簡單任務（殺雞用牛刀，純粹燒錢加延遲） |

特別點名一個常見誤區：**用多 agent 並行【直接寫入】同一份程式碼**。程式碼編輯**高度共享狀態**（改了 A 檔影響 B 檔、import 要一致、測試要一起過），多個 agent 同時往同一個工作區寫很容易產生衝突、彼此假設不一致。要拿捏分寸：**並行的「讀取 / 探索 / review / 各自產出 patch 提案」很適合**（這些是唯讀或互不干擾的）；真正危險的是**並行直接寫入共享工作區**——那需要 ownership 分區（每個 worker 只准碰自己那塊）、鎖、或由一個中央整合者（integrator）把各 worker 的 patch 收回來統一套用。〈Building Effective Agents〉其實也把「跨多檔的複雜修改」列為 orchestrator-workers 的適用例之一——關鍵不是「程式碼不能多 agent」，而是「**寫入的協調要設計好**，別讓多隻手無管控地搶改同一塊」。multi-agent 最無痛的甜蜜區仍是**唯讀的、可切片的廣度任務**。

## 五、編排的容錯：部分失敗是常態，不是例外

派出 5 個 worker，**幾乎一定**會遇到其中一個逾時、報錯、或回了沒用的東西。新手的 orchestrator 常常一個 worker 一炸、整個 `orchestrate` 跟著拋例外——這在生產上不可接受。好的編排要**優雅降級**：

- **單一 worker 隔離**：每個 worker 的呼叫包在自己的 `try/except`（第二節程式碼），一個掛掉只影響它自己，其餘照常。
- **逾時要有對策**：`timeout` 到了，決定是「放棄它、用 N-1 個結果繼續彙整」還是「重試一次」。多數情況前者，並在彙整時**標註這塊有缺口**（讓最終答案誠實）。
- **彙整者要被告知缺口**：把「子任務 3 失敗」這件事**寫進**給彙整 agent 的輸入（上面程式碼就是這樣做），讓它知道別把缺失的部分當成「不存在」，而是「沒查到」。
- **整體要有預算/回合上限**：避免 lead 無止境地拆、派、再拆。設總 worker 數上限、總 token 預算。
- **彙整那一步本身也會失敗**：第二節的最後一次 Opus 呼叫沒包錯誤處理——生產上它跟任何 API 呼叫一樣會 429/逾時/斷線，要套 Ch 9 的重試與錯誤處理。否則你辛苦並行查完，卻倒在最後一哩。

核心原則：**N 個 worker 裡有 k 個失敗，系統該回「基於 N-k 個結果的、誠實標註缺口的答案」，而不是整個崩掉，也不是假裝完整。**

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 拓撲 | 網狀（worker 互相溝通） | **星狀（都經過 lead）** | 星狀：避免 N² 溝通爆炸，好 debug |
| 路徑決定 | 動態（模型當場決定拆幾個） | **能寫死就寫死（workflow）** | 先試 workflow；只有開放任務才上動態 orchestrator |
| 並行 | 串行派發 | **真並行（ThreadPool）** | 並行：否則失去 multi-agent 唯一的速度優勢 |
| worker 失敗 | 整批拋例外 | **隔離 + 降級 + 標註缺口** | 降級：部分失敗是常態 |
| worker 共享資訊 | 互傳訊息 | **共享檔案 / 經由 lead** | 共享工作區：別讓 agent 互相聊天 |
| 該不該上多 agent | 能拆就拆 | **可平行+廣度+值得成本才上** | 緊耦合/共享 context 的任務用單一 agent |

## 踩雷集錦

1. **讓 worker 互相對話（網狀）**：溝通複雜度爆炸、沒人掌握全局、極難 debug。預設星狀，共享資訊走 lead 或共享檔案。
2. **fan-out 其實是串行**：一個 worker 跑完才派下一個，等於沒並行——白白付了多 agent 的成本卻沒拿到速度。要用 `ThreadPoolExecutor`/async 真正同時跑。
3. **一個 worker 掛掉整批崩**：沒有 per-worker 的 try/except 和 timeout。部分失敗是常態，必須隔離 + 降級。
4. **拆太細**：拆出一堆高度重疊的子任務，token 成本翻好幾倍、彙整還得處理一堆重複。拆解要「獨立、不重疊、夠粗到值得一個 worker」。
5. **彙整者不知道有缺口**：worker 失敗了卻沒告訴彙整 agent，它把「沒查到」當成「不存在」，產出看似完整其實有洞的答案。缺口要顯式傳進彙整輸入。
6. **用多 agent 無管控地並行寫同一份程式碼**：編輯打架、假設不一致。並行讀取/探索/review/產 patch 提案沒問題；並行**直接寫入**共享工作區要有 ownership 分區、鎖、或中央整合者。緊耦合、需要共享全局 context 的推理用單一 agent 串行做。
7. **一上來就追求自主 agent 群**：多數任務一條寫死的 workflow（chaining/routing）就解決了。動態編排是最後手段，不是起點。

## 進階：再往深一層

- **背景執行與串流回報**：worker 不必同步等到全部回來。可以邊跑邊把「已完成的子任務結果」串流回報給使用者（Ch 8 串流、Ch 31 背景任務）。長研究任務尤其需要——讓使用者看到進度，而不是盯著轉圈 90 秒。
- **共享工作區（blackboard 模式）**：當 worker 真的需要看到彼此的部分成果，比「互傳訊息」更好的做法是一個**共享檔案/資料結構**：每個 worker 把結果寫進去、需要時讀別人寫的。這把 N² 的溝通降成「都讀寫同一塊黑板」（Ch 21 的檔案系統正好當這塊黑板，但要注意並發寫入，Ch 21 進階）。
- **動態 vs 靜態拆解**：第二節把 `subtasks` 當成已拆好。真正的 orchestrator-workers 是 **lead agent 自己拆**——它先用一次模型呼叫產出子任務清單（可能是 structured output，Ch 32），再 fan-out。要不要讓拆解動態，取決於「你事先知不知道會有哪些子任務」。
- **evaluator-optimizer 迴圈**：generator 產出、evaluator（另一個 agent，可能用不同模型）打分+給修改意見、generator 再改，迴圈到「夠好」或回合上限。關鍵是 evaluator 要有**明確的停止標準**，否則會無限自我批評。適合翻譯、寫作、有測試訊號的程式。
- **可觀測性是 multi-agent 的命門**：worker 過程不進 lead 的 context（Ch 26），出錯時你從 lead 看只是「某個 worker 回了怪東西」。必須把每個 worker 的完整 trace 記到 log、能單獨重放（Ch 35 observability、Ch 39 可重現）。多 agent 系統不做 tracing，等於閉著眼睛開車。
- **這就是 Anthropic 的 multi-agent research 系統**：一個 lead agent 拆研究問題、並行派多個 subagent 查、用共享記憶體與檔案協調、最後彙整成報告。它的工程筆記把本章許多取捨（拆解品質、並行、成本約 15 倍、協調複雜度、什麼任務適合廣度搜尋）用真實經驗講了一遍——是本章最好的延伸。

## 動手練習

1. 用第二節的 `orchestrate` 派 3 個 worker 查 3 個獨立問題（例如三個不同主題），確認它們**真的並行**（總時間接近最慢那個、而非三個相加）。
2. **故意讓一個 worker 失敗**：把其中一個 subtask 寫成會逾時或報錯的，確認 `orchestrate` 仍回傳「基於其餘 2 個 + 標註第 3 個缺口」的結果，而不是整個崩掉。
3. **比較串行 vs 並行**：把 `ThreadPoolExecutor` 換成 for 迴圈串行跑同樣 3 個 worker，量總時間差，親身體會並行的價值。
4. 實作一個 **routing** workflow：先用一次便宜模型呼叫把使用者問題分類成「程式 / 寫作 / 數學」，再 dispatch 給三種不同 system prompt 的 agent。對比它跟「一個通用 agent 全包」的效果。
5. （進階）實作一個 **evaluator-optimizer** 迴圈：generator 寫一段文案、evaluator 打分+建議、generator 改，最多 3 輪或評分達標就停。觀察「停止標準」沒設好時它怎麼無限自我批評。

## 本章重點整理

- 編排是「拆 → 並行派 → 收 → 併」。難點**不在派**（Ch 26 講完了），在**拆得好不好、並行怎麼跑、結果怎麼併、有人掛了怎麼辦**。
- 五種型態：prompt chaining / routing / parallelization / orchestrator-workers / evaluator-optimizer。**五種都是 workflow**（編排在預定路徑上）；orchestrator-workers 的「拆幾個」由模型動態決定，但仍不等於 Anthropic 定義的「完全自主 agent」。**能用寫死的 workflow 就別上更動態的編排。**
- **預設星狀拓撲**：worker 互不對話，都經過 lead 或共享檔案。網狀會引爆溝通複雜度。
- orchestrator-workers 三動作：拆解（品質決定一切、每個子任務要自給自足 brief）、並行 fan-out（真並行、設逾時）、彙整（通常再一次 agent 呼叫，要告知缺口）。
- **成本疊加、延遲取最大值**：多 agent 拿線性成本換並行延遲與 context 隔離。可平行+廣度+值得成本才上。
- **容錯是常態**：部分 worker 失敗很正常，要隔離 + 降級 + 誠實標註缺口，不能整批崩或假裝完整。
- 緊耦合 / 共享 context / 並行寫程式碼的任務，**單一 agent 通常更好**。

## 自我檢核

- [ ] 我能畫出星狀 vs 網狀拓撲，並說明為什麼預設用星狀
- [ ] 我能說出 orchestrator-workers 的三個動作，以及每個的工程難點
- [ ] 我能解釋「總成本疊加、總延遲取最大值」，並用它判斷該拆幾個 worker
- [ ] 不看本章，我能寫出「一個 worker 逾時/失敗時 orchestrator 該怎麼降級」
- [ ] 我能舉出一個「該上 multi-agent」和一個「該用單一 agent」的任務，並說明依據
- [ ] 我能分辨「workflow（寫死路徑）」和「動態 agent 編排」，並知道該先試哪個

## 延伸閱讀

### 官方文件

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic
  - **讀哪裡**：workflow vs agent 的定義，以及五種型態（prompt chaining / routing / parallelization / orchestrator-workers / evaluator-optimizer）各自的圖與適用場景。
  - **能學到什麼**：本章第一節那張地圖的權威來源，以及「能用 workflow 就別上 agent」這個核心判斷。
  - **前提知識**：讀過 Ch 26 更有感。

### 部落格 / 技術文章

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)** — Anthropic Engineering
  - **這篇說什麼**：一個 lead agent 怎麼拆研究問題、並行派 subagent、怎麼協調與彙整、token 成本（約 15 倍）、什麼任務適合/不適合多 agent。
  - **讀哪裡**：「orchestrator-worker 架構」「prompt 工程與評估」「什麼任務適合多 agent」幾節——對應本章第二、三、四節。
  - **為什麼值得讀**：把本章許多取捨放進真實大型系統、用第一手經驗與數據（如約 15 倍 token）佐證的工程筆記。Ch 26→27 的核心參考。

下一章換個維度：不管是單一 agent 還是一群 agent，要做複雜任務都需要**先規劃、再執行、邊做邊追蹤進度**。下一章談 planning 與 todo 管理——agent 怎麼把一個大任務拆成步驟、記住做到哪、不迷路。

→ [Ch 28 Planning 與 todo 管理](./28-planning-todo.md)
