# Ch 16 — Subagent 紀律

> **目標**：讀懂 poteto-mode 的 Subagents 段落背後的四條紀律——用 `poteto-agent` 包裝 delegate、預設開新的 subagent、傳路徑不貼內容、主 session 為每一份 subagent 的產出負責——以及它們在 Claude Code 上怎麼對應。你會寫一支 brief 檢查器把「好的 brief 長什麼樣」編碼成檢查，用 `git worktree` 做出兩個互不干擾的 delegate 工作區，並看到官方 Claude Code 版本裡一個真實存在的小缺口。

> **環境**：pstack 0.15.36、Claude Code 2.1.295、git 2.54、Node.js 26。brief 檢查器與 worktree 指令的輸出是實際跑出來的；subagent 的行為描述以 pstack 原文與 adapter 文件為依據，本章沒有實際 spawn subagent 執行。

## 為什麼需要這個？

> 如果你對 subagent 的基本機制還不熟，先回看 [harness_engineering Ch 26 — Subagent](../harness_engineering/26-subagents.md)。

subagent 的價值是**隔離的 context**：它可以讀大量檔案、嘗試很多次，結束後只回傳結果。但這個隔離是雙向的：

- subagent 看不到主 session 的對話。你在主 session 裡和使用者討論了半小時的需求細節，subagent 一個字都不知道。
- 主 session 看不到 subagent 的過程。它只看到 subagent **說**它做了什麼。

第一點讓「brief 寫不好」變成最常見的委派失敗；第二點讓「相信 subagent 的報告」變成最危險的委派失敗。poteto-mode 的 Subagents 段落針對這兩點設計。

## 先建立直覺：外包的四條規矩

```
  1. 外包商要讀過你的施工規範                    → poteto-agent 開工前先讀完 poteto-mode
     （否則他用他自己的習慣做）

  2. 每一張新工單給新的外包商，                   → Fresh subagents by default
     附上完整的工單歷史                           （consolidated scope）
     （而不是打電話給上一個外包商說「再改一下」）

  3. 給他圖紙的位置，不是把圖紙抄在電話裡          → file pointers, not inlined context

  4. 驗收是你的責任，不是外包商的                  → You own every subagent's work
     （他說「做完了」不算數）
```

## 一、poteto-agent：讓 delegate 繼承同一套紀律

> **Use `subagent_type: "poteto-agent"` for any subagent you spawn inside a playbook step** (code-writing delegates, ad-hoc helpers). The chat that receives `/poteto-mode` runs the playbook. Never spawn a `poteto-agent` to run the playbook itself.

`poteto-agent` 的定義（Claude Code 版，`agents/poteto-agent.md`）：

```markdown
---
name: poteto-agent
description: Delegate subagent for code-writing and helper tasks spawned during a `/poteto-mode` run.
  Use for delegates, not as the runner for `/poteto-mode` itself. ... Reads the `poteto-mode` skill's
  `SKILL.md` in full before any work, including its inline Principles index.
  Substituting `generalPurpose` skips that read and drifts.
is_background: true
---

# Poteto subagent

You are operating as poteto-mode's full agent style. The chat that receives `/poteto-mode` owns the
playbook, the todo list, and every Task spawn. You are one of its delegates. Read the `poteto-mode`
skill's `SKILL.md` in full before doing any work ... Use the absolute path your parent gave you.
With no path, before any other work, search `~/.cursor/plugins/` for a file whose path ends in
`skills/poteto-mode/SKILL.md`. ... If no such file exists, stop and tell your parent.
```

三個設計：

1. **角色分明**：主 session 擁有 playbook、todo、每一次 spawn；delegate 只做被分派的那一塊。**不要讓 poteto-agent 去跑 playbook**——否則就變成「一個 subagent 在跑整套流程，主 session 只是傳話」，失去了「主 session 審查 subagent」的分離。
2. **繼承紀律**：delegate 開工前讀完 poteto-mode 全文。它因此也會遵守 Comments 規則、Writing the reply 規則、只能引用讀過的原則。「Substituting `generalPurpose` skips that read and drifts」——用一般的 subagent，它不知道這些規則。
3. **前提檢查**：找不到 poteto-mode 就**停下來告訴 parent**，不要在沒有規則的情況下繼續做。

在 Claude Code 裡，plugin 提供的 agent 名稱有前綴，所以是 `subagent_type: "pstack:poteto-agent"`（adapter 的 Subagent fields 表）。

### 一個真實的小缺口

注意上面那段的 fallback：「With no path ... search `~/.cursor/plugins/`」。這是 Claude Code 版的檔案，但路徑仍然是 Cursor 的。原因是 Ch 4 的產生器只在**技能**的 `# 標題` 後插入「先讀 adapter」那一行，**agent 定義檔沒有被插入**；而 adapter 的 Paths 表雖然有「`~/.cursor/plugins/` → `~/.claude/plugins/`」這一列，delegate 並沒有被告知要去讀 adapter。

> 以下是推論，本章沒有實際執行驗證：在 Claude Code 裡，如果主 session 忘了在 brief 裡傳 poteto-mode 的絕對路徑，delegate 會去 `~/.cursor/plugins/` 找、找不到、依規定停下來告訴 parent。這是**安全的失敗**（它停下來，而不是沒有規則地繼續），但會浪費一輪。對策很直接：每一份 brief 都傳絕對路徑——這本來就是 poteto-mode preamble 的要求（「put this file's absolute path in every `poteto-agent` prompt」）。

這個例子示範了兩件事：跨 harness 轉譯很難做到百分之百（Ch 4 的產生器檢查的是技能，沒有檢查 agent 檔）；以及 pstack 的「前提不成立就停」設計，讓這類缺口的後果停留在「慢」而不是「錯」。

另外，frontmatter 的 `is_background: true` 是 Cursor 的欄位。Claude Code 的 subagent 預設就在背景執行（adapter：「Omit. Subagents run in the background by default」），所以這個欄位在 Claude Code 上應該只是被忽略（推論）。

## 二、預設開新的 subagent

> **Fresh subagents by default.** Give new work to a fresh subagent with consolidated scope, meaning the original brief, every later directive, and the prior agent's report and branch. This holds for a fix round, a follow-up, a retry, and the next queue item.

什麼時候可以續用舊的 subagent？只有新工作**嚴格需要**活在那個 agent 身上、而且搬移成本很高的狀態：

- 它本地的 checkout
- 它未提交的變更
- 它還在跑的程序（dev server、模擬器、babysit 的 watcher）

理由在最後一句：

> Interrupt-chained resumes silently drop directives, so fire a fresh subagent with consolidated scope rather than trusting a "done" summary.

```
  續用（interrupt-chained）                 新開（consolidated scope）
  ─────────────────────────                ─────────────────────────────
  agent A：brief v1                         agent A：brief v1 → 報告 + branch
    ↓ 打斷：「也處理 X」                          │
  agent A（context 裡有 v1 + X）                  ▼
    ↓ 打斷：「Y 不要動」                   agent B：brief v1 + X + 「Y 不要動」
  agent A（context 被壓縮，                      + A 的報告 + A 的 branch
    v1 的細節被摘要掉）                          （一份完整的、合併過的 brief）
    ↓
  「做完了」← 但「Y 不要動」可能已經被忘了
```

有一個例外要分清楚：「A stop or hold order to a running agent is not reuse.」叫一個正在跑的 agent 停下來，不算續用。

在 Claude Code 裡，adapter 對 `resume` 的對應是：「Spawn a new agent and paste the earlier result into its brief.」——Claude Code 這邊本來就傾向這種做法。

## 三、傳路徑，不貼內容

poteto-mode 對每一次 spawn 的預設值之一：「file pointers not inlined context」。

把一個 500 行的檔案貼進 brief，有三個問題：主 session 的 context 先付了一次成本（Guard the Context Window）；貼上去的內容是**某個時間點的快照**，檔案之後被改過，subagent 讀到的是舊的；subagent 本來就能自己讀檔，貼進去只是重複。

例外是**主 session 獨有、subagent 讀不到的東西**：使用者在對話裡說的需求細節、上一個 agent 的報告、你做的決定。這些要寫進 brief，而且要寫完整——因為 subagent 沒有別的地方可以得到它們。

## 四、你為每一份 subagent 的產出負責

> You own every subagent's work. Review the diff and write your own summary, don't pass through what it said. A second opinion is the same prompt against a different model. Agreement is high-signal.

三句話：

- **審 diff，自己寫摘要**：subagent 說「我修好了，測試都過」。主 session 要做的是：看 diff、自己跑測試、自己寫給使用者的摘要。原樣轉貼 subagent 的報告，等於讓作者自己驗收自己。
- **第二意見 = 同一個 prompt、不同的模型**：不是「請另一個 agent 檢查第一個的結果」（那個 agent 會被第一份結果錨定），而是讓兩個模型**獨立**做同一件事。
- **一致是高訊號**：兩個不同家族的模型獨立得出同一個結論，這個結論比單一模型可信得多。Ch 18 的 arena 就建立在這個想法上。

這條原則在 Orchestrate playbook 裡被寫得更極端：「Read the diff or the artifact, not only the worker's summary. Green CI is an input to a verdict, not a verdict.」

## 五、把「好的 brief」編碼成檢查

綜合 poteto-mode 的 Non-negotiables（「Restate the user's goal in each subagent brief」）、Subagents 段落、Feature playbook 第 4 步，一份好的 brief 至少要有：

| 要素 | 來源 | 為什麼 |
|---|---|---|
| 使用者的目標（重述） | Non-negotiables | subagent 看不到對話 |
| 範圍：可以動哪些檔案（路徑） | Feature 第 4 步 | 防止它改到範圍外 |
| 資料形狀與組織結構 | Feature 第 4 步、Model the Domain | 在它寫邏輯之前就定下 |
| 成功條件：可以 pass/fail | Feature 第 4 步 | 它才知道什麼時候停 |
| poteto-mode 的絕對路徑 | preamble | 讓它能繼承紀律 |
| 不要貼大段內容 | Subagents 段落 | 傳路徑 |
| 不要引用它沒有的 context | Fresh subagents | 「像上次那樣」對新 agent 沒有意義 |

照 Encode Lessons in Structure，把它寫成一支檢查器：

```js
// brief-lint.mjs：檢查一份 subagent brief 有沒有 poteto-mode 要求的要素
import fs from "node:fs";

const text = fs.readFileSync(process.argv[2], "utf8");
const lines = text.split("\n");
const findings = [];
const section = (name) => new RegExp(`^#+\\s*${name}\\b`, "im").test(text);

if (!section("Goal")) findings.push("no Goal section: restate the user's goal in the brief");
if (!section("Scope") || !/`[^`]+\/[^`]+`/.test(text)) findings.push("no Scope with file paths: name the files the delegate may touch");
if (!section("Done when")) findings.push("no Done when section: give a check that can pass or fail");
if (!/poteto-mode\/SKILL\.md/.test(text)) findings.push("no poteto-mode path: the delegate cannot read the rules it inherits");
let fence = false, run = 0, longest = 0;
for (const l of lines) {
  if (l.startsWith("```")) { fence = !fence; run = 0; continue; }
  if (fence) { run++; longest = Math.max(longest, run); }
}
if (longest > 15) findings.push(`inlined block of ${longest} lines: pass a file pointer instead`);
if (/\b(as discussed|you know|the usual|same as before)\b/i.test(text)) findings.push("refers to context the fresh delegate does not have");

console.log(findings.length ? findings.map((f) => `WARN ${f}`).join("\n") : "OK");
process.exit(findings.length ? 1 : 0);
```

（15 行的門檻是本章選的：短的錯誤訊息或指令可以貼，再長就該傳路徑。它不是 pstack 的規定。）

一份壞的 brief：

```markdown
Fix the duplicate bug like we discussed. Same as before, keep it small.

Here is the file:
（後面貼了 30 行程式碼）
```

```
WARN no Goal section: restate the user's goal in the brief
WARN no Scope with file paths: name the files the delegate may touch
WARN no Done when section: give a check that can pass or fail
WARN no poteto-mode path: the delegate cannot read the rules it inherits
WARN inlined block of 30 lines: pass a file pointer instead
WARN refers to context the fresh delegate does not have
exit=1
```

一份好的 brief（用練習 B 的修正當例子）：

```markdown
# Goal
Users must receive each notification exactly once, even when the job retries after a push failure.

# Scope
Edit only `src/notify.mjs` and `test/notify.test.mjs` in the worktree at `/tmp/wt/notify-fix`.

# Data shape
Delivery state lives in the store. `markSent(id)` persists it; `status === "sent"` on a loaded user means skip.

# Done when
`node --test test/` passes, and `node ../retry-sim.mjs` prints `deliveries per user: {"1":1,"2":1,"3":1}`.

# Rules
Read `/Users/you/.claude/plugins/cache/cursor-plugins/pstack/0.15.36/skills/poteto-mode/SKILL.md` in full before any work.
Report the diff and the exact output of both commands. Do not open a PR.
```

```
OK
exit=0
```

注意好的 brief 最後一句：「Report the diff and the exact output of both commands.」——它要求 delegate 交回**主 session 可以自己驗證**的東西，而不是一句「完成」。

## 六、隔離：每個 delegate 自己的工作區

Separate Before Serializing Shared State（Ch 13）用在 delegate 上，就是每個會寫檔的 delegate 有自己的工作目錄。adapter 把 Cursor 的 `environment: "cloud"` 對應到 Claude Code 的 `isolation: "worktree"`。worktree 是 git 內建的功能：同一個 repo、多個工作目錄、各自一個 branch。

```bash
git worktree add -q -b delegate/notify-fix /tmp/wt/notify-fix main
git worktree add -q -b delegate/notify-alt /tmp/wt/notify-alt main
git worktree list
```

```
/private/tmp/lab/pb/notify dce4ef7 [main]
/private/tmp/wt/notify-alt dce4ef7 [delegate/notify-alt]
/private/tmp/wt/notify-fix dce4ef7 [delegate/notify-fix]
```

delegate A 在自己的工作區改了檔案：

```bash
echo "// delegate A edit" >> /tmp/wt/notify-fix/notify.mjs
git status --short                              # 主工作區
git -C /tmp/wt/notify-fix status --short        # A 的工作區
git -C /tmp/wt/notify-alt status --short        # B 的工作區
```

```
--- main tree:
--- A tree:
 M notify.mjs
--- B tree:
```

只有 A 的工作區有變更，主工作區和 B 都乾淨。清理：

```bash
git worktree remove --force /tmp/wt/notify-fix
git worktree remove /tmp/wt/notify-alt
git branch -D delegate/notify-fix delegate/notify-alt
```

worktree 的限制：它隔離了**檔案**，但沒有隔離 port、資料庫、build cache 這些共享資源。兩個 delegate 都要跑 dev server 在 3000 port，worktree 救不了你。這是官方指南推薦 cloud subagent 的原因（「Each one gets its own VM and branch」），也是 Opening a PR playbook 說「Multiple `Task` calls on the same branch each get their own worktree」的原因。

## 底層機制：在 Claude Code 裡一次 spawn 的欄位對應

把 poteto-mode 對每次 spawn 的預設值，經過 adapter 對應到 Claude Code：

| poteto-mode 的預設（Cursor 寫法） | Claude Code 的對應 | 備註 |
|---|---|---|
| `subagent_type: "poteto-agent"` | `subagent_type: "pstack:poteto-agent"` | plugin 前綴 |
| `run_in_background: true` | 省略 | Claude Code 預設背景執行 |
| agent mode（不是 readonly，readonly 會拿掉 MCP） | `general-purpose` 或 `pstack:poteto-agent` | 唯讀需求用 `Explore`，但它保留 Bash，要在 brief 裡寫「不要改檔案」並事後檢查 `git status` |
| file pointers | brief 裡寫路徑 | |
| 每個角色明確指定模型 | `model: "opus" \| "sonnet" \| "haiku" \| "fable"`；非 Claude 的 slug 省略 | Ch 17 |
| `environment: "cloud"` | `isolation: "worktree"` | 只隔離檔案 |
| `resume` | 開新 agent，把舊結果貼進 brief | 本來就是 fresh-by-default 的做法 |

## 對比與取捨

| 做法 | 優點 | 缺點 | 什麼時候用 |
|---|---|---|---|
| 主 session 自己做 | 零委派成本、context 完整 | 主 session 的 context 被實作細節塞滿；作者自己驗收 | 很小的改動 |
| 續用同一個 subagent | 保留它的狀態 | 指示可能在打斷鏈中遺失 | 只在需要它的 checkout、未提交變更或執行中的程序時 |
| **每次開新的 poteto-agent** | brief 完整、紀律一致、審查分離 | 每次都要寫完整的 brief | 預設 |
| 同一個 prompt 交給兩個不同模型 | 獨立的第二意見，一致是高訊號 | 成本加倍 | 決定重要、難以撤回時（Ch 18） |

## 踩雷集錦

1. **讓 poteto-agent 跑 playbook**：原文「Never spawn a `poteto-agent` to run the playbook itself」。playbook 由收到 `/poteto-mode` 的主 session 跑，否則審查分離就不存在了。
2. **brief 裡寫「像我們剛才討論的那樣」**：subagent 沒有參與剛才的討論。把討論的結論寫成完整的句子放進 brief。
3. **轉貼 subagent 的報告當最終回覆**：「don't pass through what it said」。主 session 要自己看 diff、自己跑驗證、自己寫摘要。
4. **用 `Explore` 當成唯讀保證**：它有 Bash。brief 裡要明說不准改檔，事後跑 `git status` 檢查（adapter 的要求）。
5. **忘了傳 poteto-mode 的絕對路徑**：在 Claude Code 上，delegate 的 fallback 會去 `~/.cursor/plugins/` 找（本章第一節的缺口）。每一份 brief 都傳絕對路徑。

## 進階：再往深一層

- **把 brief-lint 接成 PreToolUse hook**：Claude Code 的 PreToolUse hook 可以在 `Agent` 工具被呼叫之前檢查它的參數。用它對 `prompt` 欄位跑 brief-lint，缺要素就拒絕這次 spawn 並說明原因——這是把「好 brief 的規則」從文字變成強制執行。社群移植版的 `pre-tool-use.awk`（給 Copilot 用）做的是類似的事：拒絕「用了使用者沒存過的模型」的 subagent 呼叫。
- **讀 Orchestrate 的 Topology 規則**：「One owner per PR」「A fresh worker for each independent task」「A sub-coordinator only when one area has many parallel workers ... Stop at three levels」「Keep about ten workers in flight per coordinator」。這是把本章的紀律放大到幾十、幾百個 subagent 時的版本（Ch 27）。
- **向上游回報那個缺口**：Ch 4 的產生器檢查技能裡的 Cursor 名稱，但不檢查 `agents/` 裡的。一個最小的修正是讓產生器也掃 `agents/*.md`，或在 poteto-agent 的 fallback 裡同時列出兩個路徑。這是 poteto-mode 的「Broken skill mid-task → fix it in its own PR」用在 pstack 本身。

## 動手練習

1. 照本章寫出 brief-lint。拿你最近一次在 Claude Code 裡交給 subagent 的任務，把你寫的 prompt 存成檔案跑一次。缺了哪些要素？
2. 用 `git worktree` 在你的一個 repo 裡建兩個工作區，各自做一個不同的小改動，確認它們互不干擾；然後各自 commit，用 `git log --all --oneline --graph` 看兩個 branch 的樣子。
3. 讀 `$PSTACK/agents/poteto-agent.md` 與 `$PSTACK/agents/comment-sicko.md`，比較兩個 agent 定義的寫法：一個是「繼承紀律」的通用 delegate，一個是單一用途的審查者。各自的 description 怎麼寫才能讓主 session 選對？

## 本章重點整理

- 用 `pstack:poteto-agent` 當 delegate，讓它開工前讀完 poteto-mode；主 session 擁有 playbook，不交給 delegate
- 預設開新的 subagent，帶著合併過的完整 brief；只在需要它的 checkout、未提交變更或執行中程序時續用
- 傳路徑不貼內容；但主 session 獨有的資訊（使用者的需求、決定、上一個 agent 的報告）要完整寫進 brief
- 主 session 為每份產出負責：自己看 diff、自己驗證、自己寫摘要；第二意見是同一個 prompt 給不同模型
- 會寫檔的 delegate 各自一個 worktree；worktree 只隔離檔案，不隔離 port 與資料庫

## 自我檢核

- [ ] 不看本章，能說出 poteto-mode Subagents 段落的四條紀律嗎？
- [ ] 為什麼「打斷後續用同一個 agent」會遺失指示？什麼情況下續用仍然是對的？
- [ ] 本章發現的 `~/.cursor/plugins/` 缺口，為什麼它的後果是「慢」而不是「錯」？
- [ ] 「請另一個 agent 檢查第一個的結果」和「同一個 prompt 交給另一個模型」差在哪？為什麼 pstack 選後者？

## 延伸閱讀

### 原始碼

- **[poteto-mode/SKILL.md 的 Subagents 段](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md)**
  - **讀哪裡**：從「## Subagents」到「## Writing the reply」之前，特別是「Fresh subagents by default」一段
- **[agents/poteto-agent.md](https://github.com/cursor/plugins/blob/main/pstack/agents/poteto-agent.md)** 與 **[harness 版](https://github.com/cursor/plugins/blob/main/harness/pstack/claude-code/agents/poteto-agent.md)**
  - **讀哪裡**：比對兩份，確認本章說的缺口在你讀的版本是否還存在（上游更新很快）

### 官方文件

- **[Claude Code 官方文件：Subagents](https://code.claude.com/docs/en/sub-agents)**
  - **讀哪裡**：agent 定義檔的 frontmatter 欄位、內建的 subagent 型別
  - **能學到什麼**：驗證 `is_background` 之類的 Cursor 欄位在 Claude Code 是否被忽略
- **[git-worktree 官方文件](https://git-scm.com/docs/git-worktree)**
  - **讀哪裡**：`add`、`list`、`remove`、`prune`
  - **能學到什麼**：本章 worktree 指令的完整選項；`prune` 在 worktree 目錄被手動刪除時很有用

### 文章

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)**
  - **這篇說什麼**：Anthropic 自己的多 agent 研究系統，包括 lead agent 怎麼寫 subagent 的任務描述、為什麼描述不清楚會導致重複工作
  - **和本章的關聯**：本章 brief 要素的獨立佐證；那篇文章說「each subagent needs an objective, an output format, guidance on the tools and sources to use, and clear task boundaries」

### 對照閱讀

- **[harness_engineering Ch 27 — Multi-agent 編排](../harness_engineering/27-multi-agent-orchestration.md)**（本 repo）
  - **和本章的關聯**：從 harness 的角度看同一件事；那一章是「怎麼實作」，這一章是「pstack 怎麼規定」

委派決定了誰做；下一章決定用哪個模型做。

→ [Ch 17 模型角色與 setup-pstack](./17-model-roles-setup.md)
