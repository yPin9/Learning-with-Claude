# Ch 0 — 環境搭建：在 Claude Code 裝 pstack

> **目標**：在 Claude Code 裡裝好 pstack，知道它的檔案實際落在哪裡、每個 session 會多吃多少 context、兩條安裝路線（官方 vs 社群移植版）差在哪，並且學會用「隔離的設定目錄」先試裝、不弄髒自己的環境。

> **環境**：Claude Code 2.1.295、macOS（Apple Silicon）、git 2.54、Node.js 26。pstack 上游版本 0.15.36（`cursor/plugins` commit `c742dd6`，2026-10-28 取得）。社群移植版 `michael-denyer/pstack-claude` 0.9.107。本章所有指令輸出都是在這個環境實際跑出來的。

## 為什麼需要這一章？

裝一個 plugin 聽起來不需要一整章。但 pstack 有三件事會讓「照著 README 打兩行指令」的人踩雷：

1. **它是為 Cursor 寫的**。技能裡寫的是 `Task`、`AskQuestion`、`~/.cursor/rules/`、`grok-4.7-xhigh-fast` 這些 Cursor 世界的名字。在 Claude Code 裡它能不能動、靠什麼機制轉譯，你得先知道，否則它叫你「spawn a Task」時你會以為它壞了。
2. **網路上有兩種以上的安裝方式**。搜尋「pstack Claude Code」會先看到一堆社群移植版，其中一個有 3.5k 顆星。很多人（包括本課作者一開始）會以為「官方只支援 Cursor，要用就得裝移植版」——事實上**官方 marketplace 本身就有 Claude Code 版**。
3. **它不是免費的**。58 個技能的描述每個 session 都會常駐在 context 裡。你應該在裝之前就知道這筆帳是多少。

## 先建立直覺：plugin = 一個「會被複製進你設定目錄的資料夾」

```
  GitHub 上的 marketplace repo                你的 Claude Code 設定目錄
  ┌──────────────────────────┐               ┌──────────────────────────────────┐
  │ cursor/plugins           │   marketplace │ ~/.claude/plugins/                │
  │ ├── .claude-plugin/      │   add         │ ├── marketplaces/cursor-plugins/  │ ← 整個 repo 的 clone
  │ │   └── marketplace.json │ ────────────▶ │ │                                 │
  │ ├── pstack/      (Cursor)│               │ └── cache/cursor-plugins/pstack/  │
  │ └── harness/pstack/      │   install     │     └── 0.15.36/                  │ ← 真正被載入的那份
  │     └── claude-code/ ────┼─────────────▶ │         ├── skills/  (58 個)      │
  │         (產生出來的副本)  │               │         ├── agents/  (2 個)       │
  └──────────────────────────┘               │         └── docs/                 │
                                             └──────────────────────────────────┘
```

兩個動作分開：`marketplace add` 是「把一個 plugin 目錄（marketplace）登記並 clone 下來」；`install` 是「從那個目錄挑一個 plugin，複製到 cache 並啟用」。pstack 的 Cursor 原版在 `pstack/`，**Claude Code 裝的是 `harness/pstack/claude-code/` 這份自動產生的副本**——這個細節 Ch 4 會整章拆。

> 如果你對 Claude Code 的 skill 與 plugin 機制還不熟，先回看 [harness_engineering Ch 29 — Skills](../harness_engineering/29-skills.md) 的「Progressive disclosure」一節。

## 一、先在隔離目錄試裝（推薦做法）

Claude Code 讀 `CLAUDE_CONFIG_DIR` 環境變數來決定設定目錄。把它指到 `/tmp` 底下的一個新資料夾，你就能完整試裝一個 plugin，而**完全不碰你真正的 `~/.claude`**。試完刪掉資料夾就沒了。

```bash
export CLAUDE_CONFIG_DIR=/tmp/cc-pstack-test
mkdir -p "$CLAUDE_CONFIG_DIR"
claude plugin marketplace add cursor/plugins
claude plugin install pstack@cursor-plugins
```

實際輸出：

```
Adding marketplace…SSH not configured, cloning via HTTPS: https://github.com/cursor/plugins.git
Refreshing marketplace cache (timeout: 120s)…
Cloning repository (timeout: 120s): https://github.com/cursor/plugins.git
Clone complete, validating marketplace…
✔ Successfully added marketplace: cursor-plugins (declared in user settings)
Installing plugin "pstack@cursor-plugins"...✔ Successfully installed plugin: pstack@cursor-plugins (scope: user)
```

確認狀態：

```bash
claude plugin list
```

```
Installed plugins:

  ❯ pstack@cursor-plugins
    Version: 0.15.36
    Scope: user
    Status: ✔ enabled
```

`installed_plugins.json` 記下了它到底裝了哪一個 commit——這是你之後回報問題、或比對「我讀的教材跟我裝的版本一不一致」的依據：

```json
{
  "version": 2,
  "plugins": {
    "pstack@cursor-plugins": [
      {
        "scope": "user",
        "installPath": "/tmp/cc-pstack-test/plugins/cache/cursor-plugins/pstack/0.15.36",
        "version": "0.15.36",
        "gitCommitSha": "c742dd61706fbacc1b95f1a20b7b988cf096ce6c"
      }
    ]
  }
}
```

（為了篇幅刪掉了 `installedAt` / `lastUpdated` 兩個時間欄位。）

**為什麼要先試裝？** 不是因為 pstack 危險，而是因為**你還不知道它會改變你的 agent 什麼行為**。裝進真正的設定目錄後，它的 58 個技能描述會出現在你**每一個專案、每一個 session** 裡。先在隔離目錄看清楚它長什麼樣，再決定要不要裝進日常環境，這本身就是 pstack 自己會要求的紀律——在動手之前先觀察真實的產物，而不是相信描述。

## 二、它到底裝了什麼：`claude plugin details`

```bash
claude plugin details pstack@cursor-plugins
```

實際輸出（節錄）：

```
pstack 0.15.36
  Description: if you want to go fast, go deep first. pstack helps you write less, but higher quality code. rigorous agent workflows you can parallelize with confidence.
  Source: pstack@cursor-plugins

Component inventory
  Skills (58)  architect, arena, automate-me, benchmark-checklist, blast-radius, bro, correct, ...
  Agents (2)  comment-sicko, poteto-agent
  Hooks (0)
  MCP servers (0)
  LSP servers (0)

Projected token cost
  Always-on:   ~3,577 tok   added to every session

Per-component (rounded)
  component                                           always-on  on-invoke
  poteto-mode                                               ~50      ~5.4k
  poteto-help                                               ~40      ~3.9k
  why                                                      ~110      ~2.2k
  technical-writing                                         ~70      ~2.2k
  bro                                                      < 20        ~30
  ...
```

這組數字值得停下來讀：

| 欄位 | 意義 | pstack 的值 |
|---|---|---|
| Always-on | 每個 session 開始就常駐 context 的成本（技能的 name + description） | 約 3,577 tokens |
| on-invoke | 某個技能被打開時，它的 SKILL.md 全文會再加多少 | poteto-mode 約 5.4k、bro 約 30 |
| Hooks | 會在特定事件自動執行的 shell 指令 | 0（官方版不裝任何 hook） |

**3,577 tokens 是多還是少？** 以 200k 的 context window 來說不到 2%。但它是「每個 session 都付」的固定成本，而且它會讓模型在每次決定要不要用 skill 時多看 58 行描述。這是 progressive disclosure 的第一層代價——Ch 5 會再回來談 pstack 怎麼用 `disable-model-invocation` 控制這件事。

**poteto-mode 打開時多 5.4k**，再加上它要求讀的 playbook（每份 1–13KB）與原則檔，一次完整的 Feature 流程在主 session 裡讀進的指引文字，粗估在 10k tokens 上下（這是依檔案大小推算的量級，不是實測值）。這就是 pstack 的「嚴謹稅」，官方指南自己也承認：「pstack spends extra tokens on subagents and review panels. That's the price of the rigor.」

## 三、找到原文：之後每一章都要對照的檔案

```bash
cd /tmp/cc-pstack-test/plugins/cache/cursor-plugins/pstack/0.15.36
ls
wc -l skills/poteto-mode/SKILL.md
ls skills/poteto-mode/playbooks | wc -l
ls -d skills/principle-* | wc -l
```

```
LICENSE
README.md
agents
docs
plugin.json
skills
     152 skills/poteto-mode/SKILL.md
      23
      24
```

三個數字記住：**poteto-mode 本體 152 行、23 份 playbook、24 條原則**。之後各章的「讀原文」都指向這個資料夾。正式安裝到 `~/.claude` 之後，路徑會變成 `~/.claude/plugins/cache/cursor-plugins/pstack/<版本>/`。

一個實用的別名：

```bash
# 加進 ~/.zshrc；版本號換掉時只要改這一行
export PSTACK=~/.claude/plugins/cache/cursor-plugins/pstack/0.15.36
# 之後：less $PSTACK/skills/poteto-mode/SKILL.md
```

## 四、在 Claude Code 裡叫用它

Claude Code 會替 plugin 的指令加上前綴，所以 Cursor 裡的 `/poteto-mode` 在這裡是：

```text
/pstack:poteto-mode add a --json flag to this command. text output stays byte-identical. verify both.
```

其他技能同理：`/pstack:how`、`/pstack:arena`、`/pstack:setup-pstack`。

注意一件事：pstack 幾乎所有技能的 frontmatter 都寫了 `disable-model-invocation: true`。意思是**模型不會自己決定打開它們，只有你打 slash command 才會觸發**。這是刻意的設計（Ch 5 詳談）：poteto-mode 一次要花幾千 tokens 外加一堆 subagent，作者不希望模型因為你說了一句「幫我改個 bug」就自作主張啟動整套流程。

## 五、設定模型角色：`/pstack:setup-pstack`

pstack 把工作分成「角色」（寫程式的 delegate、做判斷的、評審小組……），每個角色預設一個模型。在 Cursor 裡預設是 `grok-4.7-xhigh-fast` 寫程式、`claude-opus-5-5-xhigh` 做判斷與寫文字。

在 Claude Code 裡跑：

```text
/pstack:setup-pstack
```

依官方 adapter 文件的規定，它在 Claude Code 會：

- 從 `Agent` 工具的 schema 偵測你能用的模型（`opus`、`sonnet`、`haiku`、`fable`）
- 把預設值換算過來：`claude-opus-5-5-xhigh` → `opus xhigh`；**`grok-4.7-xhigh-fast` → `inherit-parent`**（Claude Code 沒有 Grok，只能讓它跑在你主 session 的模型上）
- 把結果寫到 `~/.claude/rules/pstack-models.md`

> **本段未實測**：本章沒有在真實 session 裡跑完 `/pstack:setup-pstack`（它會互動式詢問預算），以上流程依據 adapter 文件 `docs/harness/claude-code.md` 的「setup-pstack」一節。adapter 也說 Claude Code 會把 `~/.claude/rules/` 底下每個 `.md` 載入每個新 session；如果你跑完後發現新 session 沒讀到，以你環境的實際行為為準，並在第一次用 pstack 前請 agent 自己去讀那個檔案。

這裡埋了一個重要的伏筆：**pstack 的評審設計大量依賴「不同模型家族」**（例如 arena 的評審「永遠不能和主 session 同一家族」）。在 Claude Code 裡你只有 Claude 一個家族，所以這些多樣性設計會退化。Ch 17、Ch 18 會講怎麼補（提示：這台機器上已經裝了 Codex plugin，它就是另一個家族）。

## 六、另一條路：社群移植版

為了對照，我們把社群裡星數最多的移植版也裝進**另一個**隔離目錄：

```bash
export CLAUDE_CONFIG_DIR=/tmp/cc-port-test
mkdir -p "$CLAUDE_CONFIG_DIR"
claude plugin marketplace add michael-denyer/pstack-claude
claude plugin install pstack@pstack-claude
claude plugin details pstack@pstack-claude
```

實際輸出（節錄）：

```
pstack (Claude Code port) (pstack) 0.9.107
Component inventory
  Skills (67)  architect, arena, automate-me, babysit, benchmark-checklist, ..., deslop, fix-ci, ...
  Agents (4)  thermo-nuclear-review-subagent, comment-sicko, thermo-nuclear-code-quality-review-subagent, poteto-agent
  Hooks (1)  SessionStart  (harness-only — no model context cost)
  Always-on:   ~4,191 tok   added to every session
  poteto-mode                                               ~50      ~6.9k
```

| | 官方 `pstack@cursor-plugins` | 社群 `pstack@pstack-claude` |
|---|---|---|
| 版本 | 0.15.36 | 0.9.107 |
| 技能數 | 58 | 67（多了 `deslop`、`babysit`、`fix-ci` 等） |
| Agents | 2 | 4 |
| Hooks | 0 | 1（SessionStart） |
| 常駐成本 | ~3,577 tok | ~4,191 tok |
| poteto-mode 打開成本 | ~5.4k | ~6.9k |
| 轉譯方式 | 執行期：讀 adapter 文件自己對照 | 建置期：改寫文字 + hook 注入路由提示 |
| 自動觸發 | 否（`disable-model-invocation: true`） | 是（hook 叫模型在符合條件時自動進入） |

注意 `details` 說 hook 是「no model context cost」——那是指 hook 的**定義**不佔 context；但這個 hook 執行時會把一段路由提示印到 stdout，而 Claude Code 會把 SessionStart hook 的輸出加進 context。這兩件事要分清楚（Ch 4 會讀那段提示的原文）。

**本課選官方版**，理由三個：它是原文本身（教材引用的每一行都對得上）；它沒有 hook，不會在你沒叫它的時候改變 agent 行為；它和 Cursor 版同一天更新。移植版的價值在 Ch 4 當作「另一種轉譯設計」來讀。

## 七、正式安裝與移除

試完、決定要用，就在真正的設定目錄裡裝：

```bash
unset CLAUDE_CONFIG_DIR          # 回到預設的 ~/.claude
claude plugin marketplace add cursor/plugins
claude plugin install pstack@cursor-plugins
```

或在 Claude Code session 裡打 `/plugin marketplace add cursor/plugins`、`/plugin install pstack@cursor-plugins`，效果相同。

第三方 marketplace 預設**不自動更新**。想讓它跟上游走：`/plugin` → Marketplaces 分頁 → 選 `cursor-plugins` → Enable auto-update；或手動 `claude plugin update pstack@cursor-plugins`。

清掉試裝目錄：

```bash
rm -rf /tmp/cc-pstack-test /tmp/cc-port-test
```

要從正式環境移除，`claude plugin uninstall pstack@cursor-plugins`（或在 `/plugin` 介面裡停用）。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 官方 `pstack@cursor-plugins` | 原文、同步更新、無 hook、行為可預期 | 需要 agent 在執行期查對照表；評審多樣性在 Claude Code 退化 | 想讀懂 pstack 本身、想自己控制何時啟動 |
| 社群移植版 | 文字已改寫成 Claude Code 名稱；有自動路由 hook；多了幾個技能 | 跟上游有一層延遲與自訂改動；hook 會在你沒叫它時改變行為 | 想「裝了就自動用」、不在意與原文的差異 |
| 不裝，只讀原文 | 零 context 成本 | 沒有實際體驗 | 只想借鑑設計、寫自己的 mode（本課 Final Project 的路線） |

## 踩雷集錦

1. **「官方只支援 Cursor」**：錯。上游 repo 根目錄的 `.claude-plugin/marketplace.json` 直接列出 `pstack`，`source` 指向 `./harness/pstack/claude-code`。社群移植版存在，是因為它們比官方支援更早出現、或想做不同的設計取捨，不是因為官方沒有。
2. **在 Claude Code 打 `/poteto-mode` 沒反應**：Claude Code 給 plugin 指令加前綴，要打 `/pstack:poteto-mode`。官方 README 的範例全是 Cursor 寫法，照抄會失敗。
3. **以為裝好就會自動生效**：官方版的技能是 `disable-model-invocation: true`，模型不會自己用。你說「幫我修這個 bug」，它照常用 Claude Code 自己的方式修，不會跑 pstack 流程。要用就要明確打 slash command。
4. **兩個移植版同時裝**：官方版和社群版的 plugin 名字都叫 `pstack`，指令都是 `/pstack:*`。同時啟用會讓你搞不清楚到底哪一份在回應。本章用兩個隔離目錄分開試，就是為了避免這件事。
5. **忘了版本**：pstack 更新很快（2026-05 到 2026-10 有 169 個 commit），教材引用的某條規則可能在你的版本已經改了。對照 `installed_plugins.json` 的 `gitCommitSha`，有出入時以你安裝的那份原文為準。

## 進階：再往深一層

- **看 marketplace 怎麼宣告 plugin 來源**：`cat ~/.claude/plugins/marketplaces/cursor-plugins/.claude-plugin/marketplace.json`。你會看到同一個 repo 裡還有 `origin-apps` 等其他 plugin，pstack 只是其中一個。自己做 plugin 時，這就是最小可用的 marketplace 寫法。
- **project scope 安裝**：`claude plugin install` 預設 `scope: user`。如果只想在某個 repo 用，可以研究 project scope，把啟用狀態寫進該 repo 的 `.claude/settings.json`，讓團隊成員 clone 下來就有同一套設定。
- **把 token 成本當成選型依據**：`claude plugin details` 對任何 plugin 都能用。裝新 plugin 前先看 Always-on，是控制 context 預算的好習慣（呼應 [harness_engineering Ch 10 — Context window 是稀缺資源](../harness_engineering/10-context-window-budget.md)）。

## 動手練習

1. 用 `CLAUDE_CONFIG_DIR` 在 `/tmp` 試裝官方版，跑 `claude plugin details`，記下你的 Always-on 數字。和本章的 3,577 比對；如果差很多，去 `installed_plugins.json` 看你裝的是哪個 commit。
2. 打開 `$PSTACK/skills/poteto-mode/SKILL.md`，數一數它有幾個 `##` 段落、每段大概在講什麼。下一章會逐段拆，先自己猜一遍。
3. 用 `grep -l "disable-model-invocation: true" $PSTACK/skills/*/SKILL.md | wc -l` 算出有幾個技能不讓模型自己觸發。哪幾個是例外？猜猜看為什麼。

## 本章重點整理

- pstack 官方 marketplace 直接支援 Claude Code：`/plugin marketplace add cursor/plugins` + `/plugin install pstack@cursor-plugins`，指令加 `pstack:` 前綴
- 裝的是 `harness/pstack/claude-code/` 這份自動產生的副本：58 技能、2 agents、0 hooks，常駐約 3.6k tokens
- 用 `CLAUDE_CONFIG_DIR` 隔離試裝，裝之前先看 `claude plugin details` 的成本
- 大部分技能不會自動觸發，要你明確叫用；評審的模型多樣性在 Claude Code 會退化，之後要補

## 自我檢核

- [ ] 不看筆記，能畫出 `marketplace add` 與 `install` 各自把什麼東西放到哪裡嗎？
- [ ] 如果同事說「pstack 只能在 Cursor 用」，你能用哪個檔案當證據反駁？
- [ ] 能解釋 Always-on 和 on-invoke 兩種成本的差別，以及 `disable-model-invocation` 影響的是哪一種嗎？
- [ ] 說得出官方版與社群移植版至少三個具體差異，以及你為什麼選其中一個嗎？

## 延伸閱讀

### 官方文件 / 原始碼

- **[pstack docs/guide/01-setup.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/01-setup.md)**
  - **讀哪裡**：「Install the plugin」與「Keep the cost in check」兩節
  - **能學到什麼**：官方對 Claude Code 安裝的一段話（本章的依據之一），以及作者自己給的省錢建議（主 session 用強模型、程式角色用便宜模型）
  - **前提**：無
- **[pstack docs/harness/claude-code.md](https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md)**
  - **讀哪裡**：「setup-pstack」與「Models」兩張表
  - **能學到什麼**：Cursor 模型 slug 怎麼換算成 Claude Code 的 `model` 欄位；為什麼 Grok 會變成 `inherit-parent`
  - **前提**：知道 Claude Code subagent 的 `model` 參數是什麼
- **[Claude Code 官方文件：Plugins](https://code.claude.com/docs/en/plugins)**
  - **讀哪裡**：marketplace 的結構、安裝 scope、更新策略
  - **能學到什麼**：本章用到的 `marketplace add` / `install` / `details` 背後的規格
  - **注意**：Claude Code 更新頻繁，欄位名稱以你版本的 `claude plugin --help` 為準

### 對照用的移植版

- **[michael-denyer/pstack-claude README](https://github.com/michael-denyer/pstack-claude)**
  - **讀哪裡**：Install 段與 `CONTEXT.md`（它的設計詞彙表）
  - **能學到什麼**：一個移植版要同時支援 Claude Code、Codex、Pi、Copilot 時，怎麼組織「一份技能樹、多種執行環境」
  - **前提**：先讀完本章第六節的對照表

裝好了，接下來要先搞懂：這東西到底是誰寫的、為了解決什麼問題。

→ [Ch 1 pstack 是什麼：go deep first](./01-what-is-pstack.md)
