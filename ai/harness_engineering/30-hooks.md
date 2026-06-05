# Ch 30 — Hooks

> **目標**：Ch 25 的權限閘門、Ch 29 的 skill，都是「harness 內建」或「靠模型判斷」的機制。本章談 **Hook（鉤子）**——讓**你**在 agent 生命週期的特定時點（工具呼叫前/後、收到使用者輸入時、回合結束時、context 壓縮前……）插入**自己的程式碼**，去**攔截、檢查、改寫、阻擋、記錄** agent 的行為。讀完你能說出 hook 跟 agent 迴圈的根本差異——**hook 是 harness 在固定時點「保證會跑」的確定性程式碼，不受模型意志影響**；能列出常見的 hook 事件與各自用途；理解 PreToolUse hook 怎麼「在工具真的執行前擋下或放行」並把回饋餵回模型（接 Ch 20/25）；以及為什麼 hook 是「不分叉 harness 就能客製化它」的關鍵手段，還有它帶來的安全風險（hook 跑的是你權限下的任意程式碼）。

> **環境**：Hook 主要是一種 **harness 提供的擴充點 + 設定約定**，最成熟的實作是 Claude Code 的 hooks（用設定檔註冊、執行 shell 命令、用 exit code 與 JSON 跟 harness 溝通）。本章以這套為主要範例，並標明哪些是 Claude Code 的具體實作、哪些是通用原則。Python 仍是我們示範 hook 腳本內容的語言。

## 為什麼需要這個？有些事不能「拜託模型記得做」

到目前為止，你影響 agent 行為的手段都帶著一個共同弱點：**它們都經過模型這個機率性的腦袋**。

- system prompt 寫「改完檔案要跑 formatter」——模型**大多數時候**會記得，但偶爾忘。
- 工具 description 寫「不要碰 `.env`」（Ch 19）——模型**通常**遵守，但被巧妙的任務一帶可能就破例。
- 權限閘門（Ch 25）是 harness 內建的、確定的——但它的規則是 harness 作者寫死的，**你**想加一條「禁止寫入 `migrations/` 目錄」的自訂規則，改不到。

問題的本質：**有些事你需要「保證發生」，不能是「希望模型記得」。** 「每次編輯後一定要跑 formatter」「絕對不准刪除 `prod` 設定」「每個工具呼叫都要寫進稽核日誌」——這些是**政策**，政策要靠**確定性的機制**執行，不能靠機率性的模型自律。

**Hook 就是這個確定性機制：你註冊一段程式碼，掛在 agent 生命週期的某個時點，harness 保證在那個時點「一定」執行它，跟模型想不想、記不記得無關。** 它跑在迴圈**之外**，是你（harness 使用者）伸進 agent 內部的一隻手。

關鍵心態：**模型負責「決定做什麼」（機率性、靈活）；hook 負責「強制某些事一定發生 / 一定不發生」（確定性、剛性）。** 兩者分工，才能既靈活又可控。

## 先建立直覺：hook 是「生產線上的固定檢查站」

把 agent 迴圈想成一條生產線：模型決定下一個動作 → 工具執行 → 結果回來 → 再決定……。Hook 就是你在這條線的**固定位置**裝的**檢查站**，每個經過的東西都**一定**會被檢查站處理，無論模型有沒有意識到它存在。

```
   使用者輸入 ──[UserPromptSubmit hook]──▶ 模型決定動作
                                              │
                          ┌───[PreToolUse hook]──┐   ← 工具執行【前】：
                          │   檢查 / 改寫 / 擋下   │      可 allow / deny / ask
                          ▼                       │
                      工具執行                     │
                          │                       │
                          └──[PostToolUse hook]───┘   ← 工具執行【後】：
                          │   驗證結果 / 跑 formatter
                          ▼
                      結果回模型 ──▶ …… ──[Stop hook]──▶ 回合結束時
```

每個檢查站（hook 事件）都是 harness 「保證會經過」的點。最關鍵的是 **PreToolUse**——它站在「模型決定要呼叫某工具」和「工具真的執行」之間。在這裡你可以：看一眼模型想呼叫什麼工具、帶什麼參數，然後**放行、改寫、或直接擋下**——而且這個決定是**你的程式碼**做的，不是模型做的。

> **這個 session 也跑著 hook**：系統提示裡提到「使用者可能設定 hooks，把回饋（如 `<user-prompt-submit-hook>`）當成來自使用者」。當你在 Claude Code 裡設了一個 hook，它就在我每次要用工具前/後、或你每次送出訊息時觸發——我（模型）甚至不一定知道它存在，但它一定會跑。本章談的就是這層機制。

## 一、常見的 hook 事件：在生命週期的哪些點掛

不同 harness 提供的事件略有差異，但以 Claude Code 為代表，常見的有這幾類（記住：**事件 = 「保證會跑你的程式碼」的時點**）：

| 事件 | 觸發時機 | 典型用途 |
|---|---|---|
| **PreToolUse** | 模型決定呼叫某工具、**執行前** | 擋下危險動作、改寫參數、加自訂權限規則、記錄「打算做什麼」 |
| **PostToolUse** | 工具**成功執行後**、結果回模型前（失敗另有對應事件） | 改完檔自動跑 formatter/linter、驗證結果、把額外資訊附加給模型 |
| **UserPromptSubmit** | 使用者送出輸入、模型看到前 | 注入額外 context（當前分支、時間）、過濾/擋下不當輸入 |
| **Stop** | 模型認為任務完成、要停下時 | 強制檢查「真的做完了嗎」、不滿足條件就要它繼續 |
| **SubagentStop** | 一個 subagent 結束時 | 對 subagent 的產出做收尾檢查（接 Ch 26） |
| **PreCompact** | context 壓縮**前** | 壓縮前先把重要狀態存檔（接 Ch 13） |
| **SessionStart / SessionEnd** | session 開始 / 結束 | 載入專案狀態、清理、寫總結日誌 |
| **Notification** | harness 發出通知時 | 接到自訂的提醒/告警通道 |

這張表的價值在於：**你不必改 harness 原始碼，就能在這些點插入任意邏輯。** 想在每次編輯後跑 formatter？掛 PostToolUse。想禁止碰某些檔案？掛 PreToolUse。想每回合都把當前 git 分支告訴模型？掛 UserPromptSubmit。

## 二、PreToolUse：在動作發生前攔截（hook 最強的用法）

PreToolUse 是 hook 機制裡最有力的點，因為它能**在副作用發生之前**介入。Claude Code 的做法是：你在設定裡註冊一個命令，配上「對哪些工具觸發」的 matcher；該工具要執行前，harness 把工具名稱與參數（以 JSON 從 stdin）餵給你的命令，**你的命令用 exit code 或 JSON 回應決定接下來怎樣**。

一個「禁止寫入受保護路徑」的 PreToolUse hook（Python 腳本）：

```python
#!/usr/bin/env python3
# 註冊成 Write/Edit 工具的 PreToolUse hook。harness 會把工具呼叫資訊以 JSON 從 stdin 餵進來。
import json, sys
from pathlib import Path

REPO = Path("/path/to/repo").resolve()
# 受保護的「目錄/檔案」——用解析後的絕對路徑比對，而不是脆弱的子字串比對
PROTECTED = [REPO / "migrations", REPO / "prod.config", REPO / ".env"]

data = json.load(sys.stdin)                      # harness 餵入：含 tool_name、tool_input 等
raw = data.get("tool_input", {}).get("file_path", "")

# 關鍵：先 resolve 成絕對路徑（吃掉 ../、symlink），再判斷是否落在受保護路徑下——
# 這正是 Ch 21 safe_path 的教訓：安全比對一定要先正規化，別用子字串比。
target = (REPO / raw).resolve()
blocked = any(target == p or p in target.parents for p in PROTECTED)

if blocked:
    # 用 JSON 明確回「拒絕」，並附理由——deny 的理由會回饋給模型（接 Ch 20：錯誤要可行動）
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"政策禁止寫入受保護路徑：{raw}。請改動別處或請人類處理。",
        }
    }))
    sys.exit(0)

# 沒問題就什麼都不輸出、正常結束 → harness 照常繼續（仍會走原本的權限流程）
sys.exit(0)
```

> 上面的路徑比對刻意用 `Path.resolve()` + `parents` 檢查，而不是 `if "migrations/" in path`——後者會被 `../`、symlink、大小寫、路徑片語邊界（`migrations_backup/` 也含 `migrations`）等繞過。**安全相關的 hook 要像 Ch 21 的 `safe_path` 一樣嚴謹**；這仍是教學用的精簡版，正式環境還要處理大小寫不敏感檔案系統、UNC 路徑等細節。

兩種跟 harness 溝通的方式，要分清楚：

- **Exit code（簡單情況）**：`exit 0` = 成功放行；**`exit 2` = 「阻擋」訊號**，且這個 hook 寫到 **stderr** 的內容會被**回饋給模型**（讓它知道為什麼被擋、該怎麼調整——Ch 20「工具結果/錯誤要可行動」的精神）；其他非零 = 非阻擋性錯誤（記到日誌但不擋）。**注意「exit 2 會不會真的擋下」是看事件**：PreToolUse、UserPromptSubmit、Stop、SubagentStop、PreCompact 這類「動作還沒發生」的事件，exit 2 能擋；**PostToolUse 擋不了**（工具已經跑完了），它的 exit 2 只是把 stderr 餵給模型當回饋；SessionStart/End、Notification 的 stderr 則只給使用者看。
- **JSON（精細控制）**：在 stdout 印出結構化 JSON，可以表達更細的決定——例如 PreToolUse 的 `permissionDecision`（核心三種：`allow` / `deny` / `ask`；較新版本另有 `defer` 等，本章聚焦最常用的三種）與理由。上面的例子就是用這個方式回「deny + 理由」。回理由時注意可見性：`deny` 的理由是**給模型看**（讓它調整），`allow` / `ask` 的理由則是**給使用者看**。

注意這個設計的精妙：**擋下的決定由你的確定性程式碼做，但「為什麼被擋」會回饋給模型**，於是模型能**理解並調整**（換個路徑、改用別的方法、或回報給使用者），而不是莫名其妙地撞牆。這跟 Ch 25 的權限閘門是同一個哲學——**規則由 harness/hook 強制，但要給模型可行動的回饋**——只是 hook 讓「規則」變成**你可以自訂的**。

## 三、PostToolUse：動作發生後收尾（自動化的好朋友）

PostToolUse 跑在工具**執行後、結果回模型前**。它管不了「擋下」（事情已經做了），但非常適合**自動化收尾**與**驗證**：

```python
#!/usr/bin/env python3
# 註冊成 Edit/Write 的 PostToolUse hook：改完 .py 檔就自動跑 formatter。
import json, sys, subprocess

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "")

if path.endswith(".py"):
    try:
        result = subprocess.run(["black", path], capture_output=True, text=True)
    except FileNotFoundError:
        # black 沒裝：別讓 hook 自己 crash（那會以非預期的方式失敗）。
        # 明確用 exit 2 把問題回饋給模型，讓它知道「格式化工具不在、這步沒做成」。
        print("找不到 black，無法格式化；請確認環境或改用其他 formatter。", file=sys.stderr)
        sys.exit(2)
    if result.returncode != 0:
        # 用 exit 2 把 formatter 的錯誤回饋給模型，讓它知道「你剛改的檔沒過格式化」
        print(f"black 格式化失敗：{result.stderr}", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
```

PostToolUse 的經典用途：

- **改完即格式化/lint**：模型每次編輯完，hook 自動跑 formatter——不必拜託模型記得（它一定會忘）。
- **驗證副作用**：寫完檔後檢查它是否符合某些不變條件（語法對嗎？必要欄位都在嗎？），不對就用 exit 2 把問題回饋給模型，逼它修。
- **附加資訊給模型**：把「這個動作影響了哪些下游檔案」這類額外 context 加進去。

核心價值同樣是**確定性**：「每次編輯後跑 formatter」這件事，做成 PostToolUse hook 就是**保證發生**，做成 system prompt 指示就是**機率發生**。生產環境要的是前者。

## 四、Hook vs 權限模型 vs 工具：界線在哪

這三個都跟「控制 agent 動作」有關，容易混淆。釐清分工：

| | 它是什麼 | 誰寫的 | 確定性？ | 類比 |
|---|---|---|---|---|
| **工具的 description / system prompt** | 文字指引 | 你 | **機率性**（模型可能不遵守） | 給員工的工作守則 |
| **權限閘門**（Ch 25） | harness 內建的核可機制 | harness 作者 | 確定性 | 公司內建的門禁系統 |
| **Hook** | 你註冊、harness 在固定時點呼叫的程式碼 | **你** | **確定性** | 你自己加裝的檢查站 |

把關係講白：

- **Hook 是「你能自訂的確定性層」**。權限閘門也是確定性的，但它的規則是 harness 作者定的；hook 讓**你**在不分叉 harness 的前提下，加上**自己的**確定性規則與自動化。可以說 hook 是「使用者可程式化的權限/政策/自動化層」。
- **Hook 跑在迴圈之外，不經過（主）模型**。這是它跟 prompt/description 的根本差異——後者要經過模型那顆機率性的腦袋，hook（執行 shell 命令/HTTP/腳本那種）不用。模型甚至可以完全不知道某個 hook 存在，它照樣執行。（補充：較新的 Claude Code 還支援 prompt-based / agent-based hook，那類會再叫一次 LLM/agent，屬另一種 hook；本章講的是最通用、最「確定性」的命令型 hook。）
- **Hook 和權限閘門怎麼交互**：no-decision 的 hook（什麼都不回）會讓動作繼續走 harness 內建的權限流程，等於兩道關卡疊加。但 PreToolUse hook 回 **`allow`** 時，會**跳過**內建的權限詢問（你已經替它做主了）；回 `deny` 則直接擋下。要小心：回 `allow` 等於你的 hook 變成那個動作的最終放行者，責任在你。

> **一句話記住**：**prompt 是「拜託模型做」，權限閘門是「harness 替你守的門」，hook 是「你自己裝的、保證會跑的檢查站」。** 要「保證發生」的事，用 hook，別用 prompt。

## 五、安全：hook 跑的是你權限下的任意程式碼

Hook 的威力來自「它執行你寫的任意程式碼」，而這正是它的風險：

- **hook 以你的身分、你的權限執行**。一個寫壞的 PostToolUse hook（例如手滑 `rm -rf`）會真的造成破壞，而且它**每次都跑**，放大了影響。
- **別把不明來源的 hook 設定照抄進來**。hook 設定 = 一段會在你機器上自動執行的程式碼。從網路抄一份 hook 設定，跟執行別人的 shell 腳本是同一回事（呼應 Ch 29 skill 的安全討論、Ch 22 sandbox、Ch 36 注入安全）。
- **hook 本身要穩**：hook 會在 agent 的每個相關時點觸發，一個會卡住或常常失敗的 hook 會拖垮整個 agent 體驗。hook 要快、要穩、錯誤要處理好。
- **PreToolUse hook 是安全控制點，但別當成唯一防線**：它能擋危險動作，但如果 hook 邏輯有漏洞（路徑比對沒考慮到 symlink、相對路徑繞過……Ch 21 的 `safe_path` 教訓），照樣被繞過。hook 是縱深防禦的一層，不是萬靈丹。

核心原則：**hook 是把雙面刃——它給你確定性的控制力，也給你確定性地把事情搞砸的能力。** 寫 hook 要像寫生產程式碼一樣謹慎。

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 要「保證發生」的事 | 寫進 system prompt / description | **做成 hook** | 必須保證 → hook；只是偏好/建議 → prompt |
| 自訂政策（禁某些動作） | 等 harness 作者支援 | **PreToolUse hook 自己加** | hook：不分叉 harness 就能加自訂規則 |
| 改完檔跑 formatter | 拜託模型記得跑 | **PostToolUse hook 自動跑** | hook：確定性，模型一定會忘 |
| hook 擋下時 | 默默失敗 | **回饋理由給模型（stderr/JSON）** | 給可行動回饋（Ch 20），讓模型能調整 |
| hook 邏輯複雜度 | 塞一大坨邏輯進 hook | **hook 保持快、穩、單一職責** | hook 每次都跑，慢或脆會拖垮體驗 |
| 採用外部 hook 設定 | 直接照抄 | **先審查（它是會自動跑的程式碼）** | hook = 任意程式碼執行風險，要審 + 沙箱 |

## 踩雷集錦

1. **把「必須保證」的事寫進 prompt**：「記得每次都跑測試」——模型偶爾忘，生產就出事。要保證的事做成 hook。
2. **hook 擋下卻不給理由**：PreToolUse 回 deny 但不寫理由，模型只知道撞牆、不知怎麼調整，可能反覆重試同一個被擋的動作。一定要回可行動的理由（Ch 20）。
3. **PostToolUse 想拿來「擋」動作**：太晚了，副作用已發生。要擋得用 PreToolUse。PostToolUse 只能收尾、驗證、回饋。
4. **hook 很慢或會卡住**：hook 每個相關時點都跑，一個慢 hook（例如同步呼叫遠端 API）會讓 agent 每步都卡。hook 要快，重活丟背景（Ch 31）。
5. **hook 路徑比對有漏洞**：用簡單字串比對擋路徑，被相對路徑、symlink、大小寫繞過（Ch 21 `safe_path` 的教訓）。安全相關的 hook 要嚴謹。
6. **照抄不明來源的 hook 設定**：等於在自己機器上跑別人的程式碼。hook 設定要當成可執行程式碼來審查。
7. **hook 失敗沒處理**：hook 自己拋例外/非預期非零退出，可能擋掉本該放行的動作、或讓 agent 行為變得難以預測。hook 要把自己的錯誤處理乾淨。

## 進階：再往深一層

- **Hook 作為可觀測性的骨幹**：在 PreToolUse / PostToolUse 掛日誌 hook，你就有了「agent 做過的每個動作 + 結果」的完整稽核軌跡——這是 Ch 35 可觀測性的基礎。多 agent 系統尤其需要（Ch 27 提過：worker 過程不進 lead context，靠 hook 記 trace 才看得到）。
- **Hook 注入 context（UserPromptSubmit）**：每次使用者送訊息時，hook 可以自動把「當前分支、未提交變更、目前時間、相關 ticket」塞進去，讓模型不必自己去查。這是「用確定性程式碼餵 context」的好例子。
- **Stop hook 強制完成標準**：模型說「做完了」時，Stop hook 可以檢查「測試真的過了嗎？todo 真的清空了嗎？」，不滿足就擋下、要它繼續——把「完成的定義」變成確定性的把關（接 Ch 7 停止條件、Ch 28 todo）。
- **PreCompact 保存狀態**：context 要被壓縮前（Ch 13），hook 先把關鍵狀態寫到檔案/記憶體，避免重要資訊在壓縮中流失。這跟 Practice B 的記憶體機制可以結合。
- **Hook vs 中介層（middleware）**：概念上 hook 就是 agent 迴圈的 middleware——熟悉 web 框架 middleware 的人會很有親切感。差別是 hook 掛的點是「agent 生命週期事件」，而非「HTTP 請求/回應」。
- **這是 Claude Code 的 hooks 系統**：上述事件名稱、exit code 語義、JSON 輸出格式都是 Claude Code 的具體實作。不同 harness 的 hook 機制細節各異（事件集合、溝通協定），但「在生命週期固定點插入確定性程式碼」的核心概念是通用的。延伸閱讀的官方文件是權威細節來源。

## 動手練習

1. 在 Claude Code 設一個 PostToolUse hook：每次 Edit/Write 一個 `.py` 檔後，自動跑 `black`（或你慣用的 formatter）。觀察它「保證執行」——即使你從沒在 prompt 裡提過格式化。
2. 設一個 PreToolUse hook，禁止對某個路徑（例如 `secrets/`）的寫入，回 `deny` + 理由。故意叫 agent 去寫那裡，看它被擋下後**怎麼根據回饋調整**。
3. **對照實驗**：先只用 system prompt 寫「改完一定要跑 lint」，跑十次任務數數它漏掉幾次；再改成 PostToolUse hook，確認它 100% 執行。親身感受「機率性 vs 確定性」。
4. 設一個 UserPromptSubmit hook，自動把當前 git 分支名注入到模型看到的內容裡。驗證模型在不主動查的情況下就「知道」目前在哪個分支。
5. （概念）設計一個「稽核日誌」hook 方案：在 PreToolUse 記下「打算做什麼」、PostToolUse 記下「結果如何」，寫成一份可重放的 log。想想這對 debug 多步任務（Ch 38）有多大幫助。

## 本章重點整理

- **Hook = 你註冊、harness 在生命週期固定時點「保證執行」的確定性程式碼**，跑在 agent 迴圈之外、不經過模型。要「保證發生」的事用 hook，別靠 prompt。
- 常見事件：**PreToolUse**（執行前可擋/改/放行）、**PostToolUse**（執行後收尾/驗證）、UserPromptSubmit、Stop、SubagentStop、PreCompact、SessionStart/End、Notification。
- **PreToolUse 最有力**：在副作用發生前介入，用 exit code（0 放行 / 2 阻擋且 stderr 回饋模型）或 JSON（`allow`/`deny`/`ask` + 理由）跟 harness 溝通。**擋下要給可行動回饋**（接 Ch 20/25）。
- **PostToolUse 適合自動化**：改完即格式化/lint、驗證副作用——做成 hook 才能「保證每次都跑」。
- **hook 是「你可自訂的確定性層」**：權限閘門的規則是 harness 作者定的，hook 讓你不分叉 harness 就加自訂政策與自動化。
- **安全**：hook 以你的權限跑任意程式碼、每次都跑——別照抄不明來源的 hook，hook 要快/穩/錯誤處理好，安全相關的比對要嚴謹（Ch 21/22/36）。

## 自我檢核

- [ ] 我能解釋 hook 跟 system prompt/description 的根本差異（確定性 vs 機率性、是否經過模型）
- [ ] 我能列出至少四個 hook 事件，並說出各自典型用途
- [ ] 我能說明 PreToolUse 怎麼擋下一個工具呼叫，以及為什麼要把理由回饋給模型
- [ ] 我能分辨 hook、權限閘門、prompt 三者的分工
- [ ] 我能舉一個「該用 hook、不該用 prompt」的例子，並說明依據
- [ ] 我知道 hook 的安全風險（任意程式碼執行、每次都跑），以及該怎麼防範

## 延伸閱讀

### 官方文件

- **[Anthropic — Claude Code Hooks](https://docs.claude.com/en/docs/claude-code/hooks)** — Anthropic
  - **讀哪裡**：hook 事件列表（PreToolUse/PostToolUse/…）、設定格式與 matcher、exit code 語義、JSON 輸出與 `permissionDecision`。
  - **能學到什麼**：本章所有機制的權威細節與最新格式——實際寫 hook 前必讀。
  - **前提知識**：Ch 25（權限模型）會讓你更快理解 PreToolUse 的決定流程。

- **[Anthropic — Claude Code Hooks Guide / 範例](https://docs.claude.com/en/docs/claude-code/hooks-guide)** — Anthropic
  - **讀哪裡**：實際的 hook 範例（自動格式化、擋路徑、注入 context）。
  - **能學到什麼**：把本章的概念對應到可直接抄改的設定範本。
  - **前提知識**：讀過本章第二、三節。

### 部落格 / 技術文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic
  - **這篇說什麼**：強調「給 agent 護欄（guardrails）與人類監督點」的重要性——hook 正是實作這些護欄的工程手段。
  - **讀哪裡**：關於 guardrails 與 human oversight 的段落。
  - **為什麼值得讀**：理解 hook 不只是自動化小工具，而是「讓自主 agent 仍可控」的關鍵基礎設施。

下一章談一個長任務必備的能力：**背景任務**。當 agent 要跑一個耗時的東西（編譯、測試、長時間的 subagent 調查），它不該傻等——下一章談怎麼把工作丟到背景、邊跑邊做別的、完成時再回來收，以及這對 agent 迴圈與 context 的影響。

→ [Ch 31 背景任務](./31-background-tasks.md)
