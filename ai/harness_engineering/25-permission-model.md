# Ch 25 — Permission 模型與人機互動

> **目標**：Ch 22 給了 agent 執行能力、Ch 24 給了它接外部系統的能力——這些動作有副作用，有些還無法復原。本章談**怎麼把人類放進迴圈當最後一道閘**：什麼動作該攔下來問、什麼可以放行、怎麼在「安全」與「別把使用者煩死（permission fatigue）」之間取得平衡。讀完你能說出 permission 的核心張力、幾種授權模型（每次問 / allowlist / 風險分級 / permission mode / 範圍授權）、harness 怎麼在工具迴圈裡插入這道閘、該用什麼維度決定「攔不攔」（可逆性、波及半徑、是否影響他人），以及為什麼 permission 是 sandbox 與 injection 防線失守時的**最後一層**（縱深防禦）。

> **環境**：概念為主 + harness 設計。具體的 permission mode 名稱、設定語法以 Claude Code 為例說明，**這些是該 harness 的設計、會演進**——重點在模型與取捨。**你正在用的這個 session 本身就跑在一套 permission 機制上**：有些工具被當前模式自動放行、有些會跳出來問你同不同意。本章其實是在拆解你每天在用的東西。

## 為什麼需要這個？能力越大，越需要一道閘

前面幾章一路給 agent 加能力：能改檔（Ch 21）、能跑任意命令（Ch 22）、能呼叫外部系統寄信刪資料（Ch 24）。問題是——**模型會犯錯，也可能被誘導（prompt injection，Ch 36）**。一個善意但判斷錯誤的 agent，可能 `git push --force` 蓋掉別人的工作、`rm` 掉重要檔案、把測試資料庫當成正式庫清空、寄一封不該寄的信。

這些動作的共同點：**有副作用、且有些無法復原**。讀一個檔案錯了，沒差，再讀一次；但「送出」「刪除」「force push」一旦做了就難收回。所以你需要一道閘：**在 agent 把某些動作真的執行下去之前，讓人類看一眼、點頭或喊停**。這就是 permission 模型。

這件事的精神，很多 agent harness 自己就在實踐——它們的系統/開發者規則常有一段「謹慎執行動作」的指引：依**可逆性**與**波及半徑**判斷，破壞性或難復原的動作（刪檔、force push、送訊息、改共用基礎設施）預設要先取得確認。**本章就是把那套直覺，變成 harness 裡可實作的機制。**

## 一、核心張力：安全 vs. 煩死使用者

permission 設計的所有取捨，都繞著一條張力打轉：

```
   每個動作都問           ←─────────────────→        什麼都不問
   絕對安全                                          完全自動、絲滑
   但使用者點到麻木                                  但一個錯誤就釀災
   （permission fatigue）                            （失控的 YOLO agent）
```

- **問太多** → **permission fatigue（確認疲勞）**：使用者被一連串「同意嗎？」轟炸，很快就無腦狂點「同意」——這時候 permission 形同虛設，因為人根本沒在看。**問太多的下場是「實質上等於沒問」。**
- **問太少** → 一個錯誤動作（模型幻覺、或被 injection 操縱）直接造成不可逆損害。

所以好的 permission 模型不是「問很多」或「問很少」，而是**「在對的時機、為對的動作問」**——把人類的注意力留給真正重要的決定。下面幾節都在回答「怎麼決定哪些該問」。

## 二、決定「攔不攔」的維度：不是看工具名，是看後果

新手會想「把危險工具列進清單就好」。但「危險」不在工具名，在**這次呼叫的後果**。同一個 shell 工具，`ls` 和 `rm -rf` 天差地別。判斷該不該攔，看三個維度（和你 session 系統提示用的是同一套）：

1. **可逆性（reversibility）**：做錯了能不能復原？讀檔、跑測試 → 可逆，放行。刪檔、drop table、force push → 不可逆，攔。
2. **波及半徑（blast radius）**：影響範圍多大？只動 workspace 內的暫存檔 → 小。動到正式資料庫、CI/CD、共用基礎設施 → 大，攔。
3. **是否對外可見 / 影響他人**：會不會送出去、被別人看到、改到共享狀態？寄信、發 PR、貼 Slack、push 到遠端 → 一旦做了別人就看到了，攔。

```
   低風險（自動放行）              高風險（攔下來問）
   ─────────────                 ─────────────
   讀檔、列目錄                    刪檔、覆蓋未存檔的修改
   跑測試、lint、build             force push、reset --hard
   workspace 內的暫存寫入          drop table、清空資料庫
   低敏感/低成本的查詢 API         寄信、發 PR、貼訊息、付款
   （可逆、半徑小、不外顯）         （不可逆、半徑大、或對外可見）
```

**關鍵設計原則**：盡量讓「絕大多數動作落在可自動放行的那一側」，把確認留給少數真正高風險的。這樣使用者每次被問，都知道「這次是認真的」——注意力不被稀釋，permission 才真正有效。

## 三、幾種授權模型

實務上的授權機制，由粗到細：

- **每次都問（per-action prompt）**：最安全、最煩。只適合極高風險場景，或剛開始不信任 agent 時。長期用一定 fatigue。
- **Allowlist / denylist（工具或命令層級）**：預先列「這些自動放行」（如 `Bash(npm test)`、`Read`）、「這些一律攔」或「一律禁」。比每次問省事，但靜態清單擋不住「同一工具的危險用法」（呼應 Ch 22：`Bash` 放行了，`Bash(rm -rf)` 呢？所以清單常要細到「命令 + 參數樣式」，例如 Claude Code 的 `Bash(git commit *)`——尾端的 `*` 是萬用字元，也可寫成等價的 `Bash(git commit:*)`）。
- **風險分級（自動依後果判斷）**：用第二節的維度，讓 harness 自動分類——可逆/小半徑的放行、不可逆/大半徑的攔。這是最貼近「對的時機問對的事」的做法，但要你把判斷邏輯寫對。
- **Permission mode（情境模式）**：用一個全域模式調整整體鬆緊。例如 Claude Code 有幾種模式（名稱與細節會演進，以下對照當前文件）：
  - **`default`**：每個工具**第一次**使用時會問，之後依規則。
  - **`plan`（計畫模式）**：不編輯原始檔——agent 可讀檔、跑**唯讀** shell 命令來探查、提計畫，但不做變更。注意它仍照 default 規則跳權限提示，不是「完全不跑命令、也不問」。適合「先讓我看看你要幹嘛」。
  - **`acceptEdits`（自動接受編輯）**：檔案編輯自動放行，**而且**工作目錄/額外目錄內常見的檔案系統 Bash 命令（`mkdir`、`touch`、`rm`、`mv`、`cp`、`sed` 等）也自動批准；別的高風險動作仍問。適合你已信任它改檔、想加速。
  - **`bypassPermissions`（俗稱 YOLO）**：**幾乎跳過所有**權限提示。注意「幾乎」——Claude Code 仍保留少數 circuit breaker（例如 `rm -rf /`、`rm -rf ~` 這種對根/家目錄的刪除仍會問），且管理設定可禁用此模式。**只在隔離環境（容器/拋棄式 VM，Ch 22）且你願意承擔後果時用**——這是把最後一道閘幾乎關掉。
- **範圍授權（scoped grant）**：問的時候給選項——「這次允許」「這個 session 都允許」「這個命令永遠允許」。讓使用者把「重複出現的安全動作」一次性升級成自動放行，**對抗 fatigue 的關鍵手段**。

這些不是互斥的，真實 harness 是**疊合**使用：一個全域 mode + 一份 allow/deny 規則 + 風險分級兜底 + 當下的範圍授權選項。

## 四、機制：怎麼在工具迴圈裡插入這道閘

回到 Ch 18 的工具迴圈：模型回 `tool_use` → harness 執行 → 把 `tool_result` 回給模型。permission 就是**在「執行」之前插一道檢查**：

```python
def run_tool_use(block, mode, rules):                  # block 是模型的 tool_use
    decision = check_permission(block, mode, rules)    # ← 這道閘
    if decision == "deny":
        return tool_result(block, "此動作被權限規則拒絕。", is_error=True)   # Ch 20 的結果設計
    if decision == "ask":
        if not ask_human(describe(block)):             # 向使用者描述「要做什麼」並等回應
            return tool_result(block, "使用者拒絕了這個動作。", is_error=True)
    # allow，或使用者同意了 → 真的執行
    return tool_result(block, execute(block))

def check_permission(block, mode, rules):
    if matches(block, rules.deny): return "deny"       # ← deny 最優先（連 bypass 也擋不掉的 circuit breaker）
    if mode == "bypassPermissions": return "allow"     # YOLO：閘幾乎關掉（deny 規則仍生效）
    if matches(block, rules.ask):  return "ask"        # ask 規則優先於 allow
    if matches(block, rules.allow): return "allow"
    return "ask" if is_high_risk(block) else "allow"   # 風險分級兜底（第二節的維度）
```

規則的評估順序是 **deny → ask → allow**（這也是 Claude Code 的順序）：deny 永遠最強、ask 蓋過 allow。這個順序很重要——它確保「明確禁止」和「明確要問」不會被一條寬鬆的 allow 規則（或下面講的 hook）悄悄繞過。

幾個要點：

- **被拒絕要回 `tool_result`（`is_error=True`），不是讓流程崩掉**。Ch 20 講過：告訴模型「這動作被拒/被使用者否決」，它才能換個做法（例如改提議、或問使用者）。直接拋例外會讓 agent 失去恢復能力。
- **`describe(block)` 要讓人看得懂在批准什麼**：別丟一坨 JSON。要顯示「**將執行**：`rm -rf build/`，在 `~/project`」這種人話。使用者看不懂就不算真的同意（見第五節）。
- **這道閘是同步的**：高風險動作會**暫停** agent、等人回應。這天然連到 Ch 31 的長時間/背景任務——人不在的時候，要嘛等、要嘛該動作就走「拒絕」或「排隊待批」。

## 五、人機互動：怎麼問才有用

把「閘」做出來只是一半，**「怎麼問」**決定它有沒有用：

- **講清楚要批准什麼**：呈現具體動作（命令、目標路徑、要送出的內容摘要），不是工具名 + raw 參數。「批准 `Bash`？」沒有資訊量；「批准執行 `git push --force origin main`？」才能讓人判斷。
- **給範圍選項對抗疲勞**：「這次 / 這個 session / 永遠」——讓安全又重複的動作被一次性放行，把確認預算花在新的、不一樣的動作上。
- **批次處理相關動作**：agent 要連續寫 10 個檔案，不該問 10 次。可以「一次呈現這批、一起批准」，或用 acceptEdits 模式。把同類動作聚合，減少打斷。
- **記住決定**：使用者說過「這個命令永遠允許」，就別再問。決定要在 session 內（甚至跨 session）持久化——這正是 allowlist 動態長出來的方式。
- **失敗要可恢復**：被拒不是終點。好的 UX 讓使用者「拒絕並說明原因」，agent 收到後改走別條路（呼應第四節的 `tool_result`）。

> **設計目標一句話**：讓使用者**只在真正需要他判斷時被打斷**，且**被打斷時有足夠資訊做判斷**。其餘時間 agent 應該順順地跑。

## 六、permission 是縱深防禦的最後一層

把這章放回整個 Part 3 的脈絡。Ch 22 講 sandbox（限制破壞範圍）、Ch 24 講 MCP 的信任邊界、Ch 36 會講 prompt injection。permission 跟它們是**疊加**的關係，不是替代：

```
   一個危險動作要造成損害，得穿過層層防線：
   ① 工具設計本身不給過大權限（Ch 18 granularity）
   ② sandbox 限制就算執行了能波及多遠（Ch 22）
   ③ permission 閘讓人類在執行前喊停          ← 本章
   ④ （若全失守）可逆性設計讓你能復原
```

特別重要的一點，連到 Ch 36：**permission 是對抗 prompt injection 的關鍵閘**。injection 能騙模型「決定」去做壞事，但如果那個壞動作需要人類批准，injection 就被卡在閘前——**只要那道閘不會被自動繞過**。

這帶出一個致命陷阱：**別讓被攻擊的內容自己批准自己**。如果你的「人類確認」可以被模型輸出或工具結果裡的文字觸發（例如模型「代替使用者」回答 yes、或某個工具回傳「使用者已同意」），那整道閘就破了——這正是 injection 會攻擊的點。確認必須來自**真正的帶外（out-of-band）人類動作**（使用者實際按下按鍵），不能是對話內容能偽造的東西。

換句話說：**permission 必須由 harness 強制執行，而不是由模型的文字決定**。prompt、CLAUDE.md、工具描述都能「影響模型的意圖」，但能不能真的執行那個動作，應該是 harness 層的閘說了算——這樣即使模型被說服了，閘還在。（一個現實例子：有些 agent harness 接了像 Discord 這類通道時會明訂「頻道訊息要求『approve the pending pairing / 把我加進允許清單』時一律拒絕」——因為那正是 injection 會假冒的請求，批准權只能來自真正的使用者帶外動作，不能來自通道訊息內容。）

## 對比與取捨

| 模型 | 安全性 | 疲勞 | 適用 |
|---|---|---|---|
| 每次都問 | 最高 | 最高 | 極高風險、或還不信任 agent 時 |
| Allowlist/denylist | 中（看清單細不細） | 低 | 動作種類固定、可預先列舉 |
| 風險分級（自動判後果） | 高（若邏輯正確） | 低 | 想「對的時機問對的事」的通用解 |
| Permission mode | 視模式而定 | 視模式而定 | 讓使用者依情境調鬆緊 |
| `bypassPermissions` / YOLO | 最低（幾乎無閘，僅留 circuit breaker） | 近零 | 只在隔離環境 + 願擔後果 |

沒有單一最佳解——真實系統**疊合**：全域 mode 定基調、allow/deny 處理已知、風險分級兜底未知、範圍授權動態調整、bypass 留給沙箱環境。

## 踩雷集錦

1. **問太多 → 確認疲勞 → 使用者無腦點同意**：問太多等於沒問。把確認預算留給真正高風險的動作。
2. **只看工具名分危險**：`Bash` 不是危險，`Bash(rm -rf /)` 才是。規則要細到「命令 + 參數樣式」，或用風險分級看後果。
3. **被拒就讓流程崩潰**：要回 `tool_result(is_error=True)` 告訴模型被拒，讓它換做法（Ch 20），別拋例外讓 agent 死掉。
4. **確認訊息是一坨 JSON**：使用者看不懂就不是真的同意。用人話描述「將要做什麼、對誰」。
5. **不給範圍授權**：每次都從零問，重複的安全動作問到爛。給「這個 session / 永遠允許」。
6. **讓對話內容能自我批准**：模型或工具結果裡的文字觸發了「同意」=injection 直接破閘。確認必須是帶外的真人動作。
7. **以為 bypass/YOLO 模式可以日常用**：那是把最後一道閘關掉，只該在拋棄式隔離環境用。
8. **把 permission 當成唯一防線**：它是縱深防禦的一層，要和 sandbox（Ch 22）、可逆性、最小權限疊用。

## 進階：再往深一層

- **Hooks 作為 permission 的一般化（Ch 30 預告）**：很多 harness（含 Claude Code）讓你掛 **PreToolUse hook**——在工具執行前跑你的程式碼，回傳權限決策（Claude Code 目前是 `allow` / `deny` / `ask`，非互動模式另有 `defer`）。這把第四節的 `check_permission` 開放成可程式化的策略：你能寫「任何寫到 `/etc` 的動作一律拒」「對正式環境的操作一律問」。要注意：**hook 的 `allow` 不會覆蓋 deny/ask 規則**（呼應上面 deny→ask→allow 的順序），deny 的理由會當成 tool error 回給模型讓它改做法。Ch 30 會專門講 hooks。
- **自主性光譜與信任累積**：permission 鬆緊應該隨「你對這個 agent 在這個任務上的信任」調整。新任務、陌生 repo → 緊（plan 模式先看）；跑過很多次、在隔離環境 → 鬆（acceptEdits 甚至 bypass）。信任不是全有全無，是可以分階段交付的。
- **多 agent 的 permission（Ch 26-27 預告）**：當主 agent 派 subagent 去做事，subagent 的動作該不該繼承主 agent 的授權？危險動作的確認該由誰處理？多層 agent 讓「誰來把關」變複雜——通常高風險動作要上浮到有人類在的那一層。
- **審計與可追溯**：除了「事前問」，還要「事後可查」——記錄每個動作、誰批准的、結果如何（連到 Ch 35 observability）。出事時能回溯是另一種安全。
- **批准的粒度與 TOCTOU**：使用者批准的當下看到的命令，和實際執行的命令必須是**同一個**。若批准後參數還能被改（檢查與執行之間有空隙），就有 TOCTOU 風險（呼應 Ch 21）——批准要綁定到具體那一次呼叫。

## 動手練習

1. 在你練習做的 agent 工具迴圈裡，加入第四節的 `check_permission` 閘：寫一份 deny 規則（如「禁止 `rm -rf`」）、一份 allow 規則（如「`Read`、`pytest` 自動放行」），其餘高風險動作走「問」。
2. 把第二節三維度（可逆/半徑/外顯）寫成 `is_high_risk(block)`：列 8 個動作（讀檔、跑測試、刪檔、force push、寄信、列目錄、drop table、build），標出每個該自動放行還是該問，並說明依據哪個維度。
3. 設計 `describe(block)`：把一個 `Bash(git push --force origin main)` 的 tool_use 轉成一行人話確認訊息。再想「raw JSON 版本」為什麼不行。
4. 實作「範圍授權」：使用者選「這個命令永遠允許」後，把它動態加進 allow 規則，驗證之後同樣命令不再問。
5. **安全思辨**：寫一個情境，說明「讓對話內容能觸發同意」會怎麼被 prompt injection 利用（對照 Ch 36），以及「帶外真人確認」為什麼能擋住它。
6. 觀察你正在用的這個 session：找出哪些動作被自動放行、哪些會跳出確認，推測它用的是哪種模型組合（mode + 規則 + 分級）。

## 本章重點整理

- 能力（執行、外部系統）帶來有副作用、可能不可逆的動作；permission 是「執行前讓人類把關」的閘。
- 核心張力：**安全 vs. 確認疲勞**。問太多 = 使用者無腦點同意 = 實質沒問。目標是「對的時機問對的事」。
- 判斷攔不攔看後果三維度：**可逆性、波及半徑、是否對外/影響他人**——不是看工具名。
- 授權模型可疊合：每次問 / allow-deny 清單 / 風險分級 / permission mode（plan、acceptEdits、bypass）/ 範圍授權。
- 機制：在工具迴圈「執行」前插閘；被拒回 `tool_result(is_error=True)` 讓模型換做法；確認訊息要人話。
- UX 決定有效性：講清楚批准什麼、給範圍選項、批次、記住決定、失敗可恢復。
- permission 是**縱深防禦的一層**（疊在 sandbox/可逆性之上），且是對抗 prompt injection 的關鍵閘——**前提是確認不能被對話內容自動繞過，必須是帶外真人動作**。

## 自我檢核

- [ ] 我能說出 permission 的核心張力，以及「問太多」為什麼反而不安全
- [ ] 我能用可逆性/波及半徑/對外可見三維度，判斷一個動作該自動放行還是該問
- [ ] 我能說明 allowlist 為什麼常要細到「命令 + 參數」，而不是只列工具名
- [ ] 我能描述 harness 怎麼在工具迴圈裡插入 permission 閘、被拒時該回什麼給模型
- [ ] 我能講出至少三個「怎麼問才有用」的 UX 原則（範圍授權、批次、人話描述…）
- [ ] 我能解釋為什麼 permission 是對抗 injection 的關鍵閘，以及「帶外確認」為何不可省
- [ ] 我知道 bypass/YOLO 模式只該在什麼條件下使用

## 延伸閱讀

### 官方文件

- **[Claude Code — Permissions](https://code.claude.com/docs/en/permissions)** 與 **[Permission modes](https://code.claude.com/docs/en/permission-modes)**
  - **讀哪裡**：permission 規則（allow/deny/ask 的評估順序）、permission mode（`default` / `plan` / `acceptEdits` / `bypassPermissions` 等）、規則的命令+參數樣式語法（`Bash(git commit *)`）。
  - **能學到什麼**：本章模型在一個真實 harness 裡的具體形狀——對照你正在用的 session。
  - **前提知識**：用過 Claude Code 會更有感。

- **[Claude Code — Hooks](https://code.claude.com/docs/en/hooks)**
  - **讀哪裡**：PreToolUse hook 怎麼在工具執行前回傳放行/攔阻決策。
  - **能學到什麼**：把 permission 邏輯程式化的進階做法——本章第四節 `check_permission` 的可擴充版（Ch 30 詳談）。
  - **前提知識**：懂本章工具迴圈插閘的概念。

### 部落格 / 技術文章

- **[Anthropic — Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)** — Anthropic
  - **這篇說什麼**：建構 agent 的工程實務，含「人在迴圈」與授權/安全的取捨。
  - **讀哪裡**：談 permission、人類監督、自主性的段落。
  - **為什麼值得讀**：本章「安全 vs. 疲勞」「自主性光譜」論述的實務背景。

這是 Part 3 的最後一章。接下來的**練習 C**會把 Ch 18-25 整套用上：設計並實作一套檔案操作工具集——好的 schema（Ch 18-19）、好的結果（Ch 20）、路徑安全（Ch 21）、必要時的 shell（Ch 22）、以及這章的 permission 閘。把「設計一套工具」當成一個完整的小專案走一遍。

→ [練習 C：設計並實作一套檔案操作工具集](./practice-c-file-toolset.md)
