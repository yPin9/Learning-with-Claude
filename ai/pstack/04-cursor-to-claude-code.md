# Ch 4 — 跨 harness 轉譯：官方 adapter vs 社群移植版

> **目標**：搞懂一份為 Cursor 寫的技能，是靠什麼機制在 Claude Code 裡動起來的。你會讀完官方的 adapter 對照表、跑一次官方的產生器（並故意弄壞它看它怎麼擋）、拆解社群移植版的「建置期改寫 + hook」路線，最後能判斷：如果你要讓自己的技能跨 harness，該選哪一種設計。

> **環境**：pstack 0.15.36（上游 `c742dd6`）、社群移植版 `michael-denyer/pstack-claude` 0.9.107、Node.js 26。產生器與移植版的統計數字都是本機實際跑出來的。

## 為什麼需要這個？

Agent Skills 是一個開放格式：一個資料夾、一份 `SKILL.md`、frontmatter 有 `name` 和 `description`。Cursor、Claude Code、Codex 都讀得懂。**但技能的內文會提到工具名稱**——「spawn a `Task`」「call `AskQuestion`」「read `~/.cursor/rules/`」。這些名字在別的 harness 裡不存在或叫別的名字。

這是所有「跨平台」問題的老問題：同一份內容，不同的執行環境。軟體史上解這個問題有兩條經典路線：

- **執行期轉譯**：內容不變，執行時查一張對照表（像 i18n 的 runtime lookup，或直譯器）
- **建置期轉譯**：事先把內容改寫成目標平台的版本（像編譯器，或為每個平台各出一個 build）

pstack 的官方支援和社群移植版，剛好各自選了一條。對照著讀，你會看到兩種選擇的真實代價。

## 先建立直覺：翻譯員 vs 翻譯本

```
  官方路線：帶一個翻譯員                       社群路線：出一本翻譯本
  ───────────────────────                     ──────────────────────
  原文技能（Cursor 名稱）                       原文技能（Cursor 名稱）
     │                                            │
     │ + 開頭插一行：                              │ 99 條改寫規則（建置期套用）
     │   「先讀 adapter 對照表」                   │ + 50 個檔案的自訂分支
     ▼                                            ▼
  Claude Code 載入                              改寫後的技能（Claude Code 名稱）
     │                                            │
     ▼                                            ▼
  模型讀到 `Task` → 查表 → 用 `Agent`           模型直接讀到 `Agent`
                                                  + SessionStart hook 注入路由提示
```

翻譯員便宜、永遠跟原文同步，但每次都要花時間查表，而且翻譯員（模型）可能查錯。翻譯本讀起來順，但每次原文改版都要重新翻，而且翻譯者會不自覺地加入自己的詮釋。

## 一、官方路線：一行字 + 一張對照表

### 1.1 那一行字

Ch 2 提過，Claude Code 版的 poteto-mode 只比 Cursor 版多一行：

> In Claude Code, read [the Claude Code adapter](https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md) once per session before you follow this skill. It maps the Cursor names in pstack skills to Claude Code.

就這樣。內文的 `Task`、`AskQuestion`、`grok-4.7-xhigh-fast` 全部保留原樣。

### 1.2 那張對照表

`docs/harness/claude-code.md` 有五張表。挑最關鍵的列出：

**工具**

| Cursor | Claude Code |
|---|---|
| `Task` | `Agent` |
| `AskQuestion` | `AskUserQuestion`；`-p` 模式下不存在，改用純文字問 |
| Open a todolist | 有 `TodoWrite` 就用；沒有就把清單寫在回覆裡，每個階段更新 |
| 叫另一個 pstack 技能 | `Read` 那個技能的 `SKILL.md` 然後照做。`Skill` 工具會拒絕大部分 pstack 技能。要使用者自己打時寫 `/pstack:<name>` |

最後一列很有意思：pstack 技能是 `disable-model-invocation: true`，所以模型**不能**用 `Skill` 工具打開它們。那 poteto-mode 要叫 `how` 怎麼辦？答案是「直接用 `Read` 讀那個檔案」。這繞過了 disable-model-invocation 的限制——但這是合理的，因為限制的目的是「別讓模型在使用者沒要求時啟動 pstack」，而這裡使用者已經用 `/pstack:poteto-mode` 啟動了。

**Subagent 欄位**

| Cursor | Claude Code |
|---|---|
| `subagent_type: generalPurpose` | `subagent_type: "general-purpose"` |
| `poteto-agent`、`comment-sicko` | `pstack:poteto-agent`、`pstack:comment-sicko` |
| `readonly: true` | `subagent_type: "Explore"`，它保留 `Bash`，所以 brief 要寫「不要改檔案」，事後檢查 `git status` |
| `environment: "cloud"` | `isolation: "worktree"`，並指名 worker 要 checkout 的 branch 或 commit |
| `run_in_background: true` | 省略（Claude Code 的 subagent 預設在背景跑） |
| `resume` | 開一個新 agent，把先前的結果貼進 brief |

注意 `readonly: true` 那列的誠實：Claude Code 的 `Explore` 型別**不是真的唯讀**（它還有 Bash，可以 `echo > file`），所以對照表要求在 brief 裡明說、事後驗證。這是 Prove It Works 原則用在轉譯本身：不相信「它是唯讀的」這個代理指標，去檢查 `git status` 這個真實產物。

**模型**

| Cursor | Claude Code |
|---|---|
| `claude-opus-5-5-xhigh` | `model: "opus"`、`effort: "xhigh"`；`model` 只接受 `opus`、`sonnet`、`haiku`、`fable`；去掉 `-fast` |
| `grok-*`、`gpt-*` 等非 Claude slug | 省略 `model`，讓 agent 跑在 session 的模型上，並說明 |
| `auto`、`inherit-parent` | 省略 `model` |

> 一個需要你自己核對的地方：adapter 說可以傳 `effort`。本課撰寫時所用的 Claude Code session 裡，`Agent` 工具的 schema 只看得到 `model` 欄位（四選一），沒有看到 `effort`。不同版本或設定可能不同，以你環境中工具 schema 的實際欄位為準；schema 不接受的欄位，adapter 自己也說「Never pass a field or value the tool schema rejects」。

**Cursor 專屬功能**

> Claude Code has no Cursor Automations, Bugbot, Cursor cloud agents, or built-in PR tool. When a step needs one, put one line in your reply, `Skipped in Claude Code: <step>. <reason>.`, then go on to the next step. Do not stop, ask, or retry.

這是很漂亮的降級策略：**跳過要留紀錄、不要停下來、不要問、不要重試**。和 todo 的 `skip: <reason>` 是同一個精神——偏離可以，但要被看見。

### 1.3 產生器：讓對照表不會漏

只靠人記得「新技能用了新的 Cursor 名稱要更新對照表」一定會漏。官方用一支產生器 `scripts/generate-pstack-harness-trees.mjs`（188 行）把 `pstack/` 轉成 `harness/pstack/claude-code/`。它做三件事：

1. 用一組正規表示式（`TERMS`，22 條）掃每個技能，找出它用了哪些 Cursor 名稱
2. 只有**用了 Cursor 名稱**的技能，才在 `# 標題` 後面插入那一行 adapter 提示
3. **任何被用到的名稱如果在 adapter 裡沒有對應的列，產生器直接失敗**

```js
// 節錄自 scripts/generate-pstack-harness-trees.mjs
const TERMS = [
  { key: "`Task`", re: /`Task`|\bTask (tool|subagent|call)/ },
  { key: "`AskQuestion`", re: /AskQuestion/ },
  { key: "todolist", re: /\btodo ?list\b/i },
  // ...共 22 條
];
// ...
for (const [key, using] of users) {
  if (!adapter.includes(key)) problems.push(`Claude Code adapter has no row for ${key}, used by ${using.join(", ")}`);
}
```

跑一次：

```bash
node scripts/generate-pstack-harness-trees.mjs
```

```
58 skills, 23 SKILL.md files with the adapter line
```

58 個技能裡只有 23 個需要插那一行。另外 35 個（全部 24 條原則、`bro`、`sharpen`、`unslop`、`tdd` 等）內文沒有任何 Cursor 名稱，在任何 harness 都能原樣運作。**純文字的工程判斷本來就是可攜的；不可攜的是工具呼叫。**

現在故意弄壞：從 adapter 刪掉 `AskQuestion` 那一列（在 `/tmp` 的複本上操作），再跑：

```bash
sed -i '' '/AskQuestion/d' pstack/docs/harness/claude-code.md
node scripts/generate-pstack-harness-trees.mjs; echo "exit=$?"
```

```
FAIL Claude Code adapter has no row for `AskQuestion`, used by automate-me, poteto-mode, setup-pstack
exit=1
```

它不只說「缺了」，還說**哪三個技能會受影響**。README 說 CI 在產生的副本過期時會失敗，所以這個錯誤擋在合併之前。這就是 Encode Lessons in Structure：「記得更新對照表」不是寫在貢獻指南裡的一句提醒，而是一個會讓 CI 變紅的檢查。

## 二、社群路線：建置期改寫 + hook

### 2.1 改寫規則

移植版把轉譯規則放在 `tools/substitutions.json`，數一下：

```bash
node -e 'const s=require("./tools/substitutions.json"); console.log("substitutions:", s.substitutions.length)'
```

```
substitutions: 99
```

每條規則長這樣：

```json
{
  "pattern": "repro this with /verify-<app>.",
  "files": "^skills/poteto-help/",
  "replacement": "repro this with /verify.",
  "rationale": "the port's create-verification-skill writes a project skill named verify (#71)"
}
```

每條都附 `rationale`，這是好習慣。但注意這條規則的性質：它不只是「把 Cursor 名稱換成 Claude Code 名稱」，而是**改變了行為**（驗證技能的命名方式不同了）。

### 2.2 自訂分支

移植版用 `tools/forks.json` 宣告哪些檔案和上游刻意不同：

```bash
node -e 'const f=require("./tools/forks.json"); for (const p of Object.keys(f)) { const e=f[p]; const kinds={}; for (const v of Object.values(e)) kinds[v.kind]=(kinds[v.kind]||0)+1; console.log(p, Object.keys(e).length, JSON.stringify(kinds)); }'
```

```
pstack 50 {"policy":42,"port-feature":8}
thermos 1 {"policy":1}
```

50 個檔案有自訂分支，其中 42 個是 `policy`（移植者選擇保留的行為改動），只有 8 個是 `port-feature`（上游無法承載的平台機制）。它的 `CONTRIBUTING.md` 把這條界線叫做 **sync boundary**：

> Upstream owns skill content. This port owns the Cursor-to-Claude-Code translation. It also carries named forks, each declared in `tools/forks.json` ...

並且誠實地寫出代價：每次同步上游，這些 policy 分支都要做三方合併，「a wide rewrite conflicts each time upstream touches the same lines, and someone has to settle the change again under time pressure.」

這是建置期轉譯的典型命運：**翻譯本一旦開始加入翻譯者的詮釋，就慢慢變成另一本書**。這不一定是壞事（移植版的很多 policy 改動是合理的改進），但你讀它時讀到的已經不是 poteto 的風格，而是 poteto + 移植者的風格。

### 2.3 SessionStart hook：讓它自己啟動

移植版 `hooks/hooks.json`：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh\" claude" }
        ]
      }
    ]
  }
}
```

hook 在 Claude Code 這條路徑上最後執行 `cat session-start-context.md`，把這段文字印出來、加進 session 的 context：

```
<EXTREMELY_IMPORTANT>
You have pstack.

Invoke the `pstack:poteto-mode` skill and follow its instructions when a task meets any of these:

- it touches more than one file, or changes a signature other files call
- it involves a design or architecture choice
- it is a bug whose cause is not yet known, or a performance issue

It routes to the right pstack skill from there. For smaller tasks, such as a contained change to one file with an obvious test, a question, or a one-line edit, work directly and verify on the real artifact.
...
</EXTREMELY_IMPORTANT>
```

而且移植版的 poteto-mode frontmatter **拿掉了** `disable-model-invocation: true`，於是模型可以自己打開它。

這是和官方完全相反的產品決策：

| | 官方 | 移植版 |
|---|---|---|
| 誰決定什麼時候付嚴謹稅 | 使用者（打 slash command） | 模型（依 hook 給的三個條件判斷） |
| 「改兩個檔案」會觸發嗎 | 不會，除非你叫它 | 會 |
| 風險 | 你忘了叫，該嚴謹的地方沒嚴謹 | 小事也跑整套流程，成本失控 |

兩種都說得通。重點是你要**知道**你裝的是哪一種，才不會在帳單或行為上被嚇到。

## 底層機制：模型怎麼「查表」

官方路線依賴一個前提：模型讀到 `Task` 時，會記得 adapter 說要換成 `Agent`。這靠的是什麼？

```
  session 開始
    │
  /pstack:poteto-mode ...
    │
    ├─ poteto-mode 全文進 context（第一行：先讀 adapter）
    ├─ 模型呼叫 Read(docs/harness/claude-code.md) → 對照表進 context
    │
    │  ...之後每次遇到 Cursor 名稱...
    │
    └─ 模型在 context 裡同時看得到「原文的 Task」和「對照表的 Task → Agent」
       → 注意力機制把兩者連起來 → 呼叫 Agent 工具
```

沒有任何程式在做替換。這是**用 context 做轉譯**。它的可靠度取決於兩件事：對照表有沒有真的被讀進來（Ch 3 說過，transcript 裡查得到）；以及對照表在 context 裡離使用點多遠（長 session 被壓縮後，對照表可能被摘要掉——這時 adapter 說的「once per session」就不夠了，要在壓縮後重讀）。

> 最後這點是推論，不是在 adapter 裡寫明的規則：Claude Code 的 context 壓縮會把早期內容摘要化，一張被摘要成「有一張名稱對照表」的表，對模型就沒有用了。長任務時，如果你看到 agent 又開始用 `Task` 或 `generalPurpose` 這些名字，就是這個徵兆，叫它重讀 adapter 即可。

## 對比與取捨

| 面向 | 執行期轉譯（官方） | 建置期轉譯（社群） |
|---|---|---|
| 與原文同步 | 同一天，同一個 repo | 有延遲，每次同步要合併 |
| 每次執行的成本 | 多讀一份對照表（數千 tokens） | 零（已經改寫好） |
| 正確性風險 | 模型可能查錯、壓縮後忘記 | 改寫規則可能漏、可能改錯語意 |
| 防呆機制 | 產生器：用到的名稱沒有對照列就 CI 失敗 | 改寫規則附 rationale；fork 要宣告 |
| 行為偏離原文 | 幾乎沒有 | 42 個 policy 分支 |
| 適合 | 想要原汁原味、想讀懂原作 | 想要「裝了就順」、接受另一套風格 |

**如果你要讓自己的技能跨 harness**，本課的建議是：先學官方路線。把內文寫成「工具無關」的語言能走多遠就走多遠（pstack 的 35 個純文字技能證明了這可以走很遠），剩下的工具呼叫集中成一張對照表，再用一個像產生器那樣的檢查防止漏列。等你真的遇到「模型查表查錯」的問題，再考慮建置期改寫。

## 踩雷集錦

1. **以為移植版就是原版的「Claude Code 翻譯」**：移植版有 50 個檔案和上游刻意不同（42 個是行為改動）。讀移植版學 pstack，你學到的是移植者修改過的版本。本課引用原文一律以官方版為準。
2. **看到 agent 用了 `Task` 就以為壞了**：官方版的內文本來就寫 `Task`。只要它最後呼叫的是 `Agent` 工具，就沒問題。真正的問題是它**呼叫失敗**或**卡住問你 Task 是什麼**——這通常代表它沒讀 adapter。
3. **以為 `Explore` subagent 是唯讀的**：它有 Bash。adapter 明確要求 brief 寫「不要改檔案」並事後檢查 `git status`。你自己寫技能時也一樣：不要把「型別叫 Explore」當成安全保證。
4. **在 Claude Code 等 Bugbot、cloud agent**：這些不存在。adapter 規定跳過並留一行 `Skipped in Claude Code: ...`。如果 agent 停下來問你 Bugbot 在哪，它沒照 adapter 做。
5. **兩個都裝**：兩份 plugin 都叫 `pstack`，指令都是 `/pstack:*`，hook 只在移植版有。同時啟用時你很難判斷是哪一份在動作。選一個。

## 進階：再往深一層

- **讀產生器的其他檢查**：除了對照表，它還檢查「技能資料夾名稱必須等於 frontmatter 的 `name`」（因為 Cursor 用資料夾名、Claude Code 用 `name` 列出技能），以及名稱必須是小寫連字號、最多 64 字元（Agent Skills 規格的限制）。這些都是「跨 harness 時才會冒出來」的不一致，被編碼成檢查。
- **讀移植版的 `CHANGES.md`**：每個版本的條目都寫了改了什麼、為什麼、怎麼驗證（例如 0.9.106 那條還寫了「這個 run 無法區分兩個 prompt」這種誠實的負面結果）。這是一份很好的「如何維護一個長期分支」範本。
- **思考第三條路**：Claude Code 的 hook 也能做 PreToolUse 檢查（移植版給 Copilot 用的 `pre-tool-use.awk` 會拒絕「用了使用者沒存過的模型」的 subagent 呼叫）。用 hook 在**工具呼叫時**攔截並修正名稱，是介於執行期與建置期之間的設計。參考 [harness_engineering Ch 30 — Hooks](../harness_engineering/30-hooks.md)。

## 動手練習

1. 在 `/tmp` 複製一份上游，跑產生器確認 `58 skills, 23 SKILL.md files with the adapter line`。然後在任一個原則技能（例如 `principle-laziness-protocol`）的內文加一句提到 `AskQuestion` 的話，再跑一次。輸出變成多少？那個原則現在多了什麼？
2. 打開移植版的 `tools/forks.json`，隨機挑三個 `policy` 項目讀它的 `why`。你同意這個改動嗎？如果你是上游作者，你會收這個改動嗎？
3. 寫一段 50 字以內的 SessionStart hook 提示，讓 Claude Code 在「使用者說出 bug」時才建議（不是自動啟動）使用 `/pstack:poteto-mode`。比較它和移植版那段的差異。

## 本章重點整理

- 官方 Claude Code 支援 = 23 個技能開頭多一行「先讀 adapter」+ 一份對照表 + 一支會在漏列時讓 CI 失敗的產生器
- 社群移植版 = 99 條建置期改寫規則 + 50 個自訂分支 + 一個讓模型自動啟動的 SessionStart hook
- 不可攜的是工具呼叫，不是工程判斷：35 個純文字技能不需要任何轉譯
- 執行期轉譯靠 context，長 session 壓縮後可能失效；看到 agent 又用 Cursor 名稱就叫它重讀

## 自我檢核

- [ ] 不看本章，能說出官方產生器的三個動作，以及它在什麼條件下會讓 CI 失敗嗎？
- [ ] 能解釋為什麼 pstack 的 24 條原則都不需要 adapter 那一行嗎？
- [ ] 如果有人說「移植版比較好，因為不用每次查表」，你會補充哪兩個它沒提到的代價？
- [ ] 想一個你自己寫過、只能在某一個工具裡用的 agent 指示。它不可攜的部分是什麼？能集中成一張對照表嗎？

## 延伸閱讀

### 原始碼

- **[docs/harness/claude-code.md](https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md)**
  - **讀哪裡**：整份，五張表
  - **能學到什麼**：本章所有對照的原文；特別注意每列的附註（例如 `readonly` 那列的 git status 要求）
  - **前提**：知道 Claude Code 的 Agent 工具有哪些參數
- **[scripts/generate-pstack-harness-trees.mjs](https://github.com/cursor/plugins/blob/main/scripts/generate-pstack-harness-trees.mjs)**
  - **讀哪裡**：`TERMS` 陣列與 `classify()` 函式（約第 28–105 行）
  - **能學到什麼**：怎麼用不到 200 行把「跨 harness 一致性」變成 CI 檢查
  - **前提**：讀得懂 Node.js
- **[michael-denyer/pstack-claude CONTRIBUTING.md](https://github.com/michael-denyer/pstack-claude/blob/main/CONTRIBUTING.md)**
  - **讀哪裡**：「The sync boundary」一節
  - **能學到什麼**：一個長期移植版怎麼區分「平台轉譯」和「行為改動」，以及後者的維護成本

### 官方文件

- **[Claude Code 官方文件：Hooks](https://code.claude.com/docs/en/hooks)**
  - **讀哪裡**：SessionStart 事件，以及 hook 輸出怎麼被加進 context
  - **能學到什麼**：移植版自動路由機制的底層規格
  - **前提**：[harness_engineering Ch 30](../harness_engineering/30-hooks.md)
- **[Claude Code 官方文件：Subagents](https://code.claude.com/docs/en/sub-agents)**
  - **讀哪裡**：內建 subagent 型別（general-purpose、Explore）與它們的工具
  - **能學到什麼**：驗證 adapter 對 `Explore` 的描述；以你的版本為準

### 對照閱讀

- **[Agent Skills 規格](https://agentskills.io)**（原始碼在 [agentskills/agentskills](https://github.com/agentskills/agentskills)）
  - **讀哪裡**：SKILL.md frontmatter 欄位一節，特別是 `name` 的限制（最多 64 字元、只能小寫字母數字與連字號、不能以連字號開頭或結尾、必須等於所在資料夾名）
  - **和本章的關聯**：產生器的 `SKILL_NAME` 檢查與「資料夾名必須等於 `name`」檢查都來自這份規格；它也是 pstack 能在多個 harness 被讀懂的基礎
  - **前提**：無，規格本身很短

Part 0 結束。你已經知道 pstack 是什麼、怎麼裝、一次執行長什麼樣、怎麼在 Claude Code 裡動起來。接下來進入它最核心的設計：路由。

→ [Ch 5 路由器設計：一個 mode 加 23 份 playbook](./05-router-design.md)
