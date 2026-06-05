# Ch 26 — Subagent：把任務委派出去

> **目標**：到目前為止我們只有「一個」agent——一個 loop、一份 context、一組工具。本章談 agent 怎麼**生出另一個 agent** 去處理子任務。讀完你能說出 subagent 到底是什麼（一個有自己 context、自己 system prompt、自己工具、自己 loop 的全新 agent 實例）、它最重要的價值為什麼是 **context 隔離**而非「更聰明」、為什麼「從父 agent 的角度看，subagent 就是一個工具」、委派時那份「任務簡報（brief）」為什麼必須**自給自足**、以及什麼時候**不該**用 subagent（它不是免費的——每個 subagent 都是一次完整的 agent 執行）。

> **環境**：Python + Anthropic SDK，延續前面章節的 agent loop。**你正在用的這個 session 本身就有 subagent 能力**——它有一個 `Agent` 工具，可以派出 `Explore`（唯讀搜尋）或 `general-purpose` 子 agent 去跑多步驟任務。本章其實是在拆解你每天看到的「我派一個 agent 去查這個」底下發生了什麼。具體工具名稱（`Agent`/`Task`/`Explore`）是**各 harness 的設計、會演進**，重點在機制與取捨。

## 為什麼需要這個？單一 agent 的兩個天花板

前面 25 章，我們的 agent 一直是「一個 loop 配一份 context」。這個模型很好懂，但它撞到兩面牆：

**牆一：context 會被「過程」弄髒。** 想像你要 agent「在這個 50 萬行的 repo 裡找出所有跟付款相關的程式」。它得 grep、讀十幾個檔、列目錄、再 grep……這些**中間步驟**——每個工具呼叫、每個動輒幾百行的檔案內容——全都堆進它唯一那份 context（Part 2 講了整整一個 Part 它有多稀缺）。等它終於找到答案，context 已經被一堆「過程垃圾」塞滿，剩沒多少空間做真正的工作。問題是：你其實**只想要最後那個答案**（「付款邏輯在 `payments/` 這三個檔」），不想要它翻箱倒櫃的全過程。

**牆二：一個 loop 一次只能做一件事。** 如果任務天然可以拆成「同時查五個獨立子問題」，單一 agent 只能一個一個串行做，慢。

Subagent 同時治這兩面牆：**派一個全新的 agent 去做那個髒活，它在自己獨立的 context 裡翻箱倒櫃，做完只把「乾淨的結論」回報給你。** 過程留在子 agent 那邊、用完即丟；你的主 context 只多了一段精煉的答案。而且既然是獨立的 agent，你可以一次派好幾個**並行**跑。

## 先建立直覺：subagent 是「你雇的外包」，而簡報是你給它的全部資訊

把主 agent 想成一個專案經理（PM）。它接到一個大任務，但不想自己跳進細節泥巴裡。它**外包**：找一個外包工程師，給對方一張**工單**——「去查清楚 X，查完給我一份摘要」。外包工程師關起門來自己幹活（自己 google、自己讀 code、自己試），PM 完全看不到他桌上的草稿；幾小時後外包交回**一頁結論**。PM 的辦公桌（context）始終乾淨，只多了那一頁。

```
   主 agent（PM）的 context                    subagent（外包）的 context
   ┌─────────────────────────┐                ┌─────────────────────────────┐
   │ 使用者任務               │   ──工單──▶    │ 工單（你給的 brief）          │
   │ ...                      │                │ grep 結果（300 行）           │  ← 髒活
   │ [呼叫 dispatch_agent]    │                │ 讀 file A（500 行）           │     全留
   │ [tool_result：一頁摘要] ◀│──一頁結論──    │ 讀 file B（400 行）           │     在這
   │ ...繼續主線              │                │ grep again（200 行）          │
   └─────────────────────────┘                │ → 我的結論：付款在 X/Y/Z      │
        ↑ 乾淨，只多一段                        └─────────────────────────────┘
                                                     ↑ 用完即丟
```

這張圖藏著本章**最重要的一個觀念**：

> **從父 agent 的角度看，subagent 就是「一個工具」。** 父 agent 呼叫 `dispatch_agent(task=...)`，等一個 `tool_result` 回來——它根本不知道（也不需要知道）那個 tool_result 是「跑了一整個 agent loop、燒了 20 次工具呼叫」才生出來的。對父 agent 來說，這跟呼叫 `read_file` 沒有本質區別：丟參數進去、拿結果出來。**subagent 把「一整段 agent 工作」封裝成了「一次工具呼叫」。**

這個封裝就是 context 隔離的來源：子 agent 的 20 次工具往返**完全不會**出現在父 agent 的訊息歷史裡，只有最後那一段 return 值會。

## 一、subagent 的解剖：它跟主 agent 一模一樣，只是「巢狀」

別把 subagent 想成什麼特殊機制。它**就是你前面寫過的那個 agent loop**——只是這次是被另一個 agent 啟動的。一個 subagent 有它自己的：

- **獨立的 context（訊息歷史）**：全新的、空的 `messages`。**它看不到父 agent 的對話**——這點極其重要，下一節整節在講。
- **自己的 system prompt**：可以跟父 agent 完全不同。父是「通用 coding agent」，子可以是「你是一個唯讀的程式碼搜尋專家，只負責找出位置並回報，不修改任何東西」。
- **自己的工具子集**：通常**比父 agent 少**。一個負責「搜尋」的 subagent 只需要 read/grep/glob，不該給它 write/shell（最小權限，呼應 Ch 25）。
- **自己的模型選擇**：簡單的子任務（搜尋、抽取、摘要）可以指派**便宜模型**（Haiku），省錢省延遲（Ch 37 前哨）。
- **自己的 loop 與停止條件**：自己的 `max_turns`、自己的 token 預算。

最小實作就是「把 agent loop 包成一個函式，回傳它的最終文字」：

```python
def run_subagent(task: str, system: str, tools: list, tool_funcs: dict,
                 model: str = "claude-haiku-4-5-20251001", max_turns: int = 15) -> str:
    """跑一個獨立的 agent loop，回傳它的最終文字結論。
    注意：messages 從空開始——subagent 看不到父 agent 的任何歷史。"""
    messages = [{"role": "user", "content": task}]      # ← 全新 context，只有這份 brief
    last_text = ""                                       # 追蹤最近一次 assistant 文字，給 fallback 用
    for _ in range(max_turns):
        resp = client.messages.create(
            model=model, max_tokens=2048,
            system=system, tools=tools, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        last_text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "tool_use":
            # run_tool_uses 回傳的是【一則完整的 user 訊息】：
            #   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., ...}, ...]}
            # 所以直接 append（不是只 append tool_result block list）。
            messages.append(run_tool_uses(resp.content, tool_funcs))   # 子 agent 自己的工具迴圈
            continue
        if resp.stop_reason == "end_turn":
            return last_text
        if resp.stop_reason == "max_tokens":
            return last_text + "\n（⚠️ 子 agent 回應因長度上限被截斷，結論可能不完整）"
        return last_text + f"\n（子 agent 收到未預期的 stop_reason: {resp.stop_reason}）"
    # 跑到回合上限：用追蹤到的 last_text，而不是去翻 messages[-1]
    # （最後一輪很可能是 tool_use，messages[-1] 會是 tool_result 而非 assistant 文字）
    return "（subagent 達到回合上限，未能完成；以下是目前進度）\n" + last_text
```

看出來了嗎？這跟 Ch 4 的主 loop **幾乎一樣**——差別只在它被當成函式呼叫、且回傳「最終文字」而不是進入互動。subagent 不是新魔法，是**遞迴地套用同一個 loop**。

## 二、把 subagent 接成「父 agent 的一個工具」

現在把 `run_subagent` 包成父 agent 可以呼叫的工具。父 agent 看到的，就是一個叫 `dispatch_agent` 的工具：

```python
DISPATCH_SCHEMA = {
    "name": "dispatch_agent",
    "description": (
        "派出一個獨立的子 agent 去完成一個界定清楚、可獨立完成的子任務"
        "（例如：在 repo 裡搜尋某類程式、調查一個問題、彙整某主題的資料）。"
        "子 agent 有自己的 context，【看不到目前的對話】——所以 task 必須把"
        "所有必要背景講清楚、自給自足。它會關起門做完，只回給你一段【摘要結論】。"
        "適合：可平行、需要大量探索但你只要結論的工作。"
        "不適合：瑣碎的單步操作（那直接自己做就好）、需要持續來回對話的任務。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {"type": "string",
                     "description": "給子 agent 的完整工單：要做什麼、需要的背景、希望回傳什麼格式。必須自給自足。"},
        },
        "required": ["task"],
    },
}

def dispatch_agent(task: str) -> str:
    # 子 agent 用「搜尋專家」人設 + 唯讀工具子集 + 便宜模型
    return run_subagent(
        task=task,
        system="你是一個唯讀的程式碼調查專家。用 read_file/list_directory/grep 找答案，"
               "不修改任何東西。完成後回一段【精煉的結論】：直接給答案與關鍵檔案位置，"
               "不要把你讀過的原始內容整段貼回來。",
        tools=READONLY_SCHEMAS, tool_funcs=READONLY_FUNCS,   # 只給唯讀工具（最小權限，Ch 25）
        model="claude-haiku-4-5-20251001",
    )
```

`dispatch_agent` 回傳的那個字串，跟任何工具一樣，會被父 agent 的工具迴圈包成 `{"type": "tool_result", "tool_use_id": <這次呼叫的 id>, "content": <回傳字串>}`，再塞進下一則 user 訊息回給模型——不是函式 `return` 就自動進 API。換句話說，「一整個 subagent run」最後就濃縮成父 context 裡的一個 tool_result block。

於是在父 agent 的工具註冊表裡，`dispatch_agent` 跟 `read_file`、`write_file` 並列。父 agent 某一輪可能這樣用：

```
父 agent> 我需要先搞清楚付款邏輯在哪。我派個子 agent 去查。
[tool_use] dispatch_agent(task="在這個 repo 裡找出所有跟『付款處理』相關的程式碼。
            背景：這是一個 Django 電商專案，付款可能散在 models/views/services。
            回給我：相關檔案的路徑清單 + 每個檔大概負責什麼，一兩句即可。")
   ┌─ 子 agent 在自己的 context 裡跑了 12 次工具呼叫（grep、讀檔、再 grep）… ─┐
   └─ 這 12 次往返【完全沒進】父 agent 的歷史 ─────────────────────────────┘
[tool_result] 付款邏輯在三處：payments/services.py（核心收款流程）、
              payments/models.py（Payment/Refund 資料表）、
              orders/views.py 的 checkout()（呼叫付款的入口）。
父 agent> 好，那我接下來改 payments/services.py…   ← context 只多了上面那段摘要
```

**這就是 Claude Code 的 `Task`/`Agent` 工具在做的事**——也就是你這個 session 裡那個「派 Explore agent 去搜尋」的能力。父 agent（就是現在跟你對話的我）呼叫 Agent 工具、子 agent 在隔離的 context 裡翻 repo、只把濃縮結果回報。你看到的「一段乾淨的搜尋結論」，背後是一整個被封裝起來的 agent run。

## 三、最關鍵的設計：簡報必須「自給自足」

這是 subagent **最容易出錯、也最重要**的一點，值得單獨一節。

子 agent 的 context **從空白開始**。它**看不到**：父 agent 跟使用者的對話、前面已經查到的東西、使用者的原始需求、你們聊到一半建立的共識——**統統看不到**。它唯一知道的，就是你在 `task` 參數裡寫給它的那段文字。

> **精確一點（自建 harness vs Claude Code）**：在我們上面那個 `run_subagent` 裡，子 agent 的 `messages` 真的是空白，只有 `task`。在 Claude Code 這類成熟 harness 裡，子 agent 拿到的是一個**全新、隔離的 context**——同樣**不含父對話、父讀過的檔、父叫過的 skill**——但它仍會載入 harness 定義的「啟動資訊」：子 agent 自己的 system prompt、環境資訊、CLAUDE.md/memory、git 狀態等。也就是說「看不到父對話」永遠成立，但「完全空白」只對我們這個最小實作精確；重點不變：**它不知道你沒寫進 brief 的東西。**（另外有些 harness 提供「fork」型 subagent 會刻意繼承父對話，那是不同模式，預設的 named subagent 不繼承。）

這就像**一個聰明的同事剛走進房間**：他沒聽過你們前面的討論，你不能說「就照剛剛講的那樣去查一下」——他不知道「剛剛」是什麼。你必須把背景、目標、約束、期望的產出格式，**一次講清楚**。

對比兩份簡報：

```
   ❌ 不自給自足（子 agent 會瞎猜或做錯）
   task="幫我查一下那個 bug 在哪"
        ↑ 哪個 bug？哪個 repo？什麼症狀？要回什麼？子 agent 一無所知

   ✅ 自給自足
   task="在 /app 這個 Flask 專案裡，定位一個 bug：使用者回報『結帳時偶爾
        重複扣款兩次』。請查可能造成重複提交/重複呼叫付款 API 的程式碼
        （重試邏輯、缺少冪等鍵、按鈕重複觸發）。回給我：最可疑的 2-3 處
        檔案+行號，以及你判斷的理由，各一兩句。不要動任何檔案。"
```

寫好簡報是一種**稅**——你得花力氣把腦袋裡的 context 顯式地寫出來。這也是「要不要派 subagent」的成本之一：如果寫簡報的力氣比自己做還大，那就別派（見第五節）。

> **這份課程的系統提示其實就在教這件事**：它對「怎麼寫給 subagent 的 prompt」有明確指引——「像對一個剛走進房間的聰明同事下簡報：解釋你想達成什麼、你已經排除了什麼、給足周邊脈絡讓它能自己判斷」，並警告「**絕不要把理解外包出去**」（不要寫「根據你的發現把 bug 修掉」這種把分析推給子 agent 的話，因為那等於你自己沒搞懂）。這正是本節的精神。

## 四、回傳契約：subagent 該回「結論」，不是「過程」

subagent 的回傳值，是它**唯一**會進入父 agent context 的東西。所以它回什麼、怎麼回，直接決定了「context 隔離」這個好處能不能兌現。

如果你的 subagent 把「我讀了 A 檔，內容是……（貼 500 行）、然後讀了 B 檔……（再貼 400 行）」整串回給父 agent——那**隔離就破功了**，那些垃圾還是流回了主 context，你白派了。

所以 subagent 的 system prompt 一定要明確要求**精煉輸出**：

- 「回**結論**，不要回過程」——給答案與關鍵位置，不要把讀過的原始內容整段貼回。
- 「用固定格式回」——例如「檔案清單 + 每項一句說明」，讓父 agent 好解析（這也呼應 Ch 20 tool result 設計、Ch 32 structured output）。
- 「講清楚信心與缺口」——「我沒找到 X，可能在我沒搜到的地方」，讓父 agent 知道這份結論的邊界。

這跟 Ch 16（tool result 裁剪）是同一個精神，只是搬到了 agent 層級：**大量的中間資訊應該在「來源處」就被消化掉，只讓精煉的結果往上流。** subagent 是這個原則最強的體現——它連「消化」這件事本身都是 agentic 的（子 agent 自己讀、自己判斷、自己總結）。

## 五、什麼時候**不該**用 subagent（成本與取捨）

subagent 很迷人，但它**不是免費的**，而且新手最常見的毛病就是濫用它。每派一個 subagent，你付出：

- **完整的 agent 執行成本**：它是一整個 loop，自己燒 token、自己跑好幾輪、有自己的延遲。Anthropic 自己的 multi-agent research 系統就提到：一般 agent 約用掉**單純聊天互動的 4 倍** token，而多 agent 系統約**15 倍**——隔離與並行是用錢換的。
- **寫簡報的稅**：你得把背景顯式寫清楚（第三節），這本身要花力氣與 token。
- **協調與整合成本**：子 agent 回來的結果你還得讀、得整合；派越多，整合負擔越重（這是 Ch 27 multi-agent 編排的主題）。
- **失去共享 context**：子 agent 看不到全局，可能做出「局部對、全局錯」的判斷。

所以判斷準則大致是：

| 適合派 subagent | 不適合（自己做就好） |
|---|---|
| 需要**大量探索**但你只要**結論**（搜尋、調查、彙整） | 瑣碎的單步操作（讀一個你已知路徑的檔） |
| 子任務**界定清楚、可獨立完成** | 需要跟父 agent **持續來回**、緊耦合的工作 |
| 多個子任務**可平行**（同時查五個獨立問題） | 純線性、一步接一步、沒有並行餘地 |
| 過程會**弄髒** context（大量中間產物） | 過程很短、結果很小（封裝沒有意義） |
| 需要**不同人設/工具/模型**（唯讀搜尋專家、便宜模型） | 跟父 agent 用同一套工具和人設就夠 |

一句話：**subagent 的本質是「拿錢和協調成本，換 context 乾淨與並行」。** 當這筆交換划算（探索量大、要結論、可並行）才派；瑣事自己做。

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 子 agent 看不看得到父對話 | 繼承父 context | **全新空白 context** | 全新：隔離才有意義；代價是 brief 要自給自足 |
| 子 agent 的工具 | 跟父一樣全給 | **只給子任務需要的子集** | 子集：最小權限（Ch 25），搜尋型只給唯讀 |
| 子 agent 的模型 | 一律用貴模型 | **依子任務難度選** | 搜尋/抽取用便宜模型（Haiku），省錢省延遲 |
| 子 agent 回傳 | 連過程一起回 | **只回精煉結論** | 只回結論：否則隔離破功（Ch 16 精神） |
| 何時派 | 能拆就拆 | **探索量大且要結論才派** | 後者：subagent 不免費，瑣事自己做 |

## 踩雷集錦

1. **以為 subagent 看得到父對話**：最常見、最致命。子 agent 的 context 是空的，你不寫進 `task` 的東西它就不知道。寫出「照剛剛說的去做」這種簡報，子 agent 只能瞎猜。**簡報必須自給自足。**
2. **把「理解」外包出去**：寫「去研究一下然後把問題修好」——這是把分析責任推給子 agent，通常代表你自己還沒想清楚。好的簡報證明你已經理解：給出具體檔案、具體要查什麼、具體要回什麼。
3. **subagent 把過程整串回吐**：子 agent 不做摘要、把讀過的檔案內容全貼回來，context 隔離直接破功。system prompt 要強制「回結論不回過程」。
4. **濫用 subagent 做瑣事**：讀一個已知路徑的檔還派個 agent 去，純粹是燒錢加延遲。封裝沒有意義的事就自己做。
5. **沒給 subagent 停止條件**：子 agent 沒有 `max_turns`，萬一卡在某個查不到的問題上會無限燒。一定要有回合上限與「找不到就回報找不到」的指示。
6. **給 subagent 過大的工具權限**：搜尋型 subagent 給了 write/shell，等於放大了被誤用/被注入的面（Ch 25/36）。給最小子集。
7. **同步等待一堆 subagent 卻沒並行**：subagent 的並行價值要靠「同時發起、一起等」才兌現（Ch 27、Ch 31）；一個一個 `dispatch` 等回來再發下一個，就失去了並行的好處。

## 進階：再往深一層

- **並行派發**：subagent 真正的速度優勢在「一次發多個、一起等」。在你這個 session 裡，這對應「在單一訊息裡同時呼叫多個 Agent 工具」。實作上要 async/執行緒同時跑多個 `run_subagent`，再收集結果——這是 Ch 27（multi-agent 編排）與 Ch 31（背景任務）的核心。
- **遞迴深度**：在**自建** harness 裡，subagent 自己也可以再有 `dispatch_agent` 工具、再往下派，理論上可無限巢狀——但每層都放大成本與「傳話遊戲」的失真。所以多數系統限制深度（常常只允許一層：主 agent 派 worker，worker 不再往下派）。**Claude Code 就是直接禁止**：named subagent 不能再生出 subagent，要巢狀委派得由主對話串接。設計自己的 harness 時，建議比照——預設只允許一層，避免失控。
- **一次性 vs 可恢復**：第二節的 `run_subagent` 是**一次性**的——跑完就沒了，再呼叫一次是全新的、不記得上次。有些 harness 支援「對同一個 subagent 續傳訊息」（保留它的 context）。**你這個 session 的工具描述就點明了這個區別**：用 `Agent` 開新的是「全新、無記憶」，而對既有 agent 續傳要用另一個機制（傳 id/name）。要不要支援「可恢復 subagent」取決於你的場景——多數委派是一次性的。
- **結果的信任與驗證**：子 agent 可能出錯或被它讀到的內容注入（Ch 36）。父 agent 不該無條件相信子 agent 的回傳；關鍵決策應該驗證（例如子 agent 說「檔案在 X」，父 agent 真的去動 X 之前先確認 X 存在）。
- **子 agent 的可觀測性**：因為子 agent 的過程不進父 context，從父 agent 看它就是個黑盒。debug 時你會想看「子 agent 內部到底跑了什麼」——所以要把子 agent 的完整 trace 記到 log（Ch 35 observability），否則出錯了無從查起。
- **這就是 Anthropic 的 multi-agent research 系統**：一個 orchestrator agent 把研究問題拆成子問題、派多個 subagent 並行去查、再彙整。它的工程筆記（延伸閱讀）直接講了本章每個取捨：context 隔離、簡報品質決定成敗、token 成本是十幾倍、什麼任務適合多 agent。

## 動手練習

1. 把第二節的 `run_subagent` 接到你練習 C 的 agent 上，做一個 `dispatch_agent` 工具（唯讀工具子集 + Haiku）。給主 agent 一個任務「找出 workspace 裡哪個檔定義了某個函式」，觀察子 agent 的工具往返**沒有**出現在主 agent 的 `messages` 裡。
2. **故意破壞隔離**：把 subagent 的 system prompt 改成「把你讀過的每個檔案完整內容都回報」，再跑一次同樣任務，觀察主 agent 的 context 怎麼被過程垃圾塞爆——親眼看到「回結論不回過程」為什麼是隔離的命門。
3. **故意寫爛簡報**：把 `task` 寫成「照剛剛說的去查」，看子 agent（空 context）怎麼瞎猜或問不出東西。再改成自給自足的版本，對比結果。
4. 給 subagent 設一個很小的 `max_turns`（例如 3），派一個它做不完的任務，確認它會回「達到上限+目前進度」而不是無限燒或崩潰。
5. （進階）用 `concurrent.futures` 同時派三個 subagent 查三個獨立問題，比較「並行」與「一個一個串行」的總時間差。

## 本章重點整理

- subagent 就是**被另一個 agent 啟動的、一模一樣的 agent loop**——有自己獨立的 context、system prompt、工具子集、模型、停止條件。不是新魔法，是遞迴套用同一個 loop。
- **從父 agent 的角度，subagent 就是一個工具**：丟 `task` 進去、拿一段結論出來。它把「一整段 agent 工作」封裝成「一次工具呼叫」。
- 最大價值是 **context 隔離**：子 agent 在自己的 context 裡做髒活（大量探索、中間產物），只把精煉結論回報，父 context 保持乾淨。其次是**並行**與**專業化**（不同人設/工具/模型）。
- **簡報必須自給自足**：子 agent 看不到父對話，像剛進門的同事。不寫進 `task` 的它就不知道。別把「理解」外包出去。
- subagent **不免費**：完整 agent 執行成本（token 可達十幾倍）、寫簡報的稅、整合成本、失去共享 context。探索量大且只要結論時才派，瑣事自己做。
- 回傳要**精煉**（回結論不回過程），否則隔離破功；給最小工具權限、給停止條件。

## 自我檢核

- [ ] 我能解釋「從父 agent 角度 subagent 就是一個工具」這句話的意思
- [ ] 我能說出 subagent 最重要的價值是 context 隔離，並解釋它怎麼運作
- [ ] 不看本章，我能說出「簡報必須自給自足」為什麼是 subagent 最容易出錯的點
- [ ] 我能舉出兩個「該派 subagent」和兩個「不該派、自己做就好」的情境
- [ ] 我知道 subagent 的回傳為什麼要精煉，不精煉會破壞什麼
- [ ] 我能說明為什麼搜尋型 subagent 該用唯讀工具子集 + 便宜模型

## 延伸閱讀

### 官方文件

- **[Anthropic — Subagents (Claude Code docs)](https://code.claude.com/docs/en/sub-agents)**
  - **讀哪裡**：subagent 的定義、它有獨立 context 與工具集那幾段、以及怎麼設定一個 subagent 的 system prompt 與工具。
  - **能學到什麼**：一個生產級 harness 怎麼把本章的概念變成可設定的功能——對照你寫的 `run_subagent`，看它多了哪些設定面（描述、工具白名單、模型）。
  - **前提知識**：用過 Claude Code 的 Task/Agent 功能更有感。

### 部落格 / 技術文章

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)** — Anthropic Engineering
  - **這篇說什麼**：一個 orchestrator agent 怎麼派多個 subagent 並行做研究、怎麼寫子 agent 的簡報、為什麼 token 用量是十幾倍、什麼任務適合多 agent。
  - **讀哪裡**：「分工與 prompt 設計」和「成本」兩節——直接對應本章第三、五節。
  - **為什麼值得讀**：這是把本章每個取捨放到真實大型系統裡驗證過的第一手筆記，是 Ch 26→27 之間最好的橋樑。

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic
  - **這篇說什麼**：各種 agentic pattern（orchestrator-workers、routing 等）與「該不該上多 agent」的判斷。
  - **讀哪裡**：orchestrator-workers 那段。
  - **為什麼值得讀**：幫你建立「什麼時候值得引入 subagent/多 agent」的設計品味，呼應本章第五節的取捨。

派出一個 subagent 是「委派一件事」。但真實系統常常要**同時協調好幾個 agent**——怎麼拆活、怎麼並行、結果怎麼彙整、一個出錯了怎麼辦。下一章把視角從「派一個」拉到「編排一群」。

→ [Ch 27 Multi-agent 編排](./27-multi-agent-orchestration.md)
