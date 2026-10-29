# Ch 30 — 自我改進迴路：reflect、correct、automate-me

> **目標**：搞懂 pstack 怎麼讓「下一次」比「這一次」好。三個技能各管一種學習：`/reflect` 從**一次 session** 裡挖出教訓、提議改技能；`/correct` 從**整個 repo 的歷史**裡找出 agent 反覆犯的錯誤類別，用架構、型別、lint、測試把它們變成不可能再犯；`/automate-me` 從**你自己的對話紀錄**裡讀出你的工作風格，產生一份 `<你的名字>-mode`。你會在這個 repo 的真實 git 歷史裡找到一個 `/correct` 會處理的錯誤類別，並實作 `/correct` 對「已經很普遍的模式」的策略——**只擋新增的違規**——然後在本課自己的檔案上抓到一個本章撰寫時新增的違規。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26。lint 與 git 歷史的輸出是在這個 repo 上實際跑出來的（2026-10-28）。三個技能本身依原文描述，本章沒有實際執行它們（它們會 spawn 多個 subagent 讀你的對話紀錄）。

## 為什麼需要這個？

你在對話裡糾正 agent：「不要直接呼叫資料庫，走 repository 層」。它道歉、改好。下一個 session，另一個 agent 又直接呼叫資料庫。你再糾正一次。

官方指南第 9 頁：

> When you correct agents for the same mistake again and again, the fix belongs in the repo, not in your next prompt.

這一章的三個技能，是 Encode Lessons in Structure（Ch 15）放大到三個層次：一次 session、一個 repo、一個人。

## 先建立直覺：三個時間尺度的學習

```
  時間尺度        技能              輸入                          輸出                        誰核准
  ────────       ──────           ──────                       ──────                     ──────
  一次 session    /reflect          這次對話的 transcript           技能的修改提議               你（逐條）
                                    （三個平行的審查者）             Accepted / Rejected / Backlog
  一個 repo       /correct          commit、revert、review 評論、    架構 / 型別 / lint / 測試    PR 審查
                                    解釋 workaround 的註解           的修正，一類一個 commit
  一個人          /automate-me      你最近幾週的對話紀錄             <你的名字>-mode 技能          你（逐條確認）
                                    （平行的挖掘者）
```

官方對前兩者的分工：「`/reflect` improves skills from one session. `/correct` changes the repo so a mistake class can't come back.」

## 一、/reflect：一次 session 的教訓

觸發：「that took way too long. capture what we learned so the next run doesn't repeat it.」不觸發的情況：「Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.」

### 流程

1. **找到這次對話的 transcript**：只在這個 workspace 的目錄裡找；用第一行的開場 prompt 確認是哪一份
2. **三個平行的審查者**，各一個視角：

| 視角 | 預設模型 | 看什麼 |
|---|---|---|
| Judgment | Opus | 判斷上的錯誤與好的決定 |
| Tooling | Grok（Claude Code 上是 inherit-parent） | 工具使用的問題 |
| Divergent | Opus | 和主流解讀不同的觀點 |

3. **一個 synthesizer** 抽查引用（透過 MCP 查 ticket、聊天、監控），產出 **Accepted / Rejected / Backlog** 三個清單
4. **結構化檢查**：「Move any Accepted item that a lint rule, script, metadata flag, or runtime check would enforce more reliably to Backlog.」——能用結構擋的，不寫進技能，放進 backlog 讓人去做成檢查
5. **套用**：呈現完整的三個清單，**等明確核准**才改任何技能。「Skill changes affect every future agent in the org. Do not auto-apply.」

第 4 步很值得注意：reflect 的產出是「技能的修改」（文字），但它主動把「其實該做成結構的」那些移走。這是 Encode Lessons in Structure 的 Corollary（「If the fix is structural, only use the structural fix. The instruction is the symptom.」）被寫進了流程本身。

第 5 步有幾條保護：「Never edit a skill under `plugins/cache/`」——不改 plugin 的快取（下次更新就被蓋掉），要改就先複製到專案或使用者的技能目錄；「Never open a PR or push」；實質性的修改（超過約 10 行、新的段落）交給技能寫作流程跑「draft / test / iterate」。

官方的提醒：「Approve a proposal only if it would change a future decision. One weird session is an anecdote, not a rule.」

## 二、/correct：讓錯誤類別不可能再發生

> The operator keeps correcting agents in this repo for the same mistakes. Change the repo so the next agent can't make them.

它的核心假設（和 Ch 18 architect 的 design red flags 一樣）：

> Assume every contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Design the repo so a change that looks right from one file is right for the whole repo.

### 找出錯誤類別

讀最近的 commit、revert、review 評論、agent 的指示檔、解釋 workaround 的註解，把錯誤分組成類別。「A class counts once it has happened twice.」

### 修正的層級：由高到低

```
  1. 架構：讓它不可能發生
     每塊狀態一個 owner、每個任務一種做法、藏起內部讓錯的 import 失敗、
     用單一事實來源取代手動同步的清單、刪掉 agent 會複製的舊做法與死碼
          │ 做不到
          ▼
  2. 型別：讓錯的狀態寫不出來
     還是能編譯過 → 加一個 lint 或 CI 檢查，**錯誤訊息要說出該用哪個檔案、型別或函式**
     模式已經很普遍 → **只在改動新增更多時才失敗**
          │ 做不到
          ▼
  3. 測試：測行為
     修掉或刪掉「行為壞了還會通過」或「只改內部就失敗」的測試
          │ 做不到
          ▼
  4. 文件或 agent 規則：最後才用，只用於需要判斷的事
     「Nothing fails when an agent skips them.」
```

官方指南特別說：「Human review isn't on the list. A reviewer who must catch the same mistake on every PR is the problem this fixes.」

### 修正與證明

> Fix the most frequent classes now, one commit each. Prove each new check fails on a real past mistake. Run the same command locally and in CI. Exceptions go on the offending line with a reason, an expiry date, and a human's approval.

「Prove each new check fails on a real past mistake」——本課第 N 次出現的「先看檢查失敗」原則，這次的材料是**真實的歷史錯誤**。

### 規則表

> Keep a table in the agent instruction file that pairs each rule with what enforces it. When the operator corrects you, fix the mistake and add the rule. If the rule was already there and nothing enforces it, that's a repeat, so fix it at the highest level in the same change. Drop a rule once its mistake can't happen.

| 規則 | 由什麼強制 |
|---|---|
| 不直接呼叫資料庫 | `eslint no-restricted-imports`（錯誤訊息指向 `src/repo/`） |
| 新的設定旗標要註冊在 schema | CI 檢查：`config/flags.ts` 與 `schema.json` 的鍵一致 |
| 測試不准 mock 內部 helper | 文件（需要判斷，無法機械檢查） |

「Drop a rule once its mistake can't happen」——當一個錯誤被架構消滅了，規則就從表裡刪掉，因為它不再需要被記住。

## 三、在這個 repo 上找一個錯誤類別

這個 repo 的 git 歷史裡有一個 commit：

```bash
git log --format=%s | grep -iE "lint|修正"
git show --stat 0a9aafe | head -12
```

```
docs: 修正 README 排版以通過 markdown lint
...
    docs: 修正 README 排版以通過 markdown lint

    標題後補空行 (MD022/MD032)、移除標題後多餘空行 (MD012)，純排版無內容變動。

 README.md | 1 file changed, 18 insertions(+), 1 deletion(-)
```

一個專門修「標題後少了空行」的 commit。而這個 repo 根目錄**沒有** markdownlint 的設定檔，也沒有 CI：

```bash
ls .github/workflows .markdownlint* CLAUDE.md AGENTS.md
```

（全部不存在。）

依 `/correct` 的標準，這還不算一個「類別」（只發生過一次被記錄下來的修正）。但它有一個典型的特徵：**修正是手動的，而防止它再發生的結構不存在**。下一個寫 README 的 agent（包括本課在 Ch 31 之後要更新根目錄 README 的時候）可能會再犯一次。依修正層級，這一類的最高可行層級是第 2 層：一個 markdownlint 設定加上 CI 或 pre-commit 檢查，錯誤訊息直接說出 MD022。

### 另一個類別：輕描淡寫詞

Ch 15 發現 `learn` 技能的「嚴禁輕描淡寫詞」規則在全 repo 有 510 次違規——一條每次都會被載入的文字規則（第 4 層）擋不住它。依 `/correct`，它應該往上移到第 2 層（lint）。但現在已經有 505 處違規了，立刻讓 lint 擋所有違規，會讓每一個 PR 都失敗。

`/correct` 的策略：「If the pattern is already common, fail only when a change adds more.」

### 實作「只擋新增的違規」

```js
// lint-added.mjs：只檢查相對於 HEAD 新增的行（以及未追蹤的新檔案），略過 code block
import { execSync } from "node:child_process";
import fs from "node:fs";
const BANNED = ["只需要", "很簡單", "顯然"];
const added = [];
let file = null, line = 0;
for (const l of execSync("git diff -U0 HEAD -- '*.md'").toString().split("\n")) {
  if (l.startsWith("+++ b/")) file = l.slice(6);
  else if (l.startsWith("@@")) line = Number(l.match(/\+(\d+)/)[1]);
  else if (l.startsWith("+") && !l.startsWith("+++")) { added.push([file, line, l.slice(1)]); line++; }
}
for (const f of execSync("git ls-files --others --exclude-standard -- '*.md'").toString().split("\n").filter(Boolean))
  { let fence = false; fs.readFileSync(f, "utf8").split("\n").forEach((t, i) => { if (t.trimStart().startsWith("```")) fence = !fence; else if (!fence) added.push([f, i + 1, t]); }); }
const hits = added.filter(([, , t]) => !t.includes("allow-banned:") && BANNED.some((w) => t.includes(w)));
for (const [f, n, t] of hits) console.log(`${f}:${n} adds a banned word: ${t.trim()}`);
console.log(`${added.length} added lines checked, ${hits.length} new violations`);
process.exit(hits.length ? 1 : 0);
```

（`git diff -U0` 不輸出 context 行，每個 `@@ -a,b +c,d @@` 標頭的 `+c` 是新檔案裡的起始行號。對 diff 片段做 code block 判斷很難——一個片段可能從 code block 的中間開始——所以這個版本只對完整的新檔案略過 code block，這是一個已知的限制。）

**先在一個小 repo 上證明它的行為**：baseline 有 4 處違規，新的改動在既有檔案加了 1 處、新增了一個乾淨的檔案：

```
--- whole repo:
4 hits in 2 files
--- only what this change adds:
old.md:7 adds a banned word: 新的一段：只需要改設定。
6 added lines checked, 1 new violations
exit=1
```

全 repo 有 4 處，但這個改動只**新增**了 1 處——只有它會讓這個 PR 失敗。舊的 3 處留給一次專門的清理。

**再在這個 repo 上跑**（本課的所有檔案都是新的、未追蹤的）。第一版的 lint-added 沒有略過 code block，報了 13 處——其中 11 處是 Ch 15 引用的 lint 輸出與程式碼（code block 裡的內容），但有兩處是真的：

```
ai/pstack/02-anatomy-of-poteto-mode.md:97 adds a banned word: │ Comments          註解只留非顯然的 why         │  ← 程式碼輸出格式
...
ai/pstack/28-skill-authoring.md:148 adds a banned word: | 會被原樣傳給 subagent 的 prompt | ... | 主 session 不需要讀它，只需要把它傳下去 |
12497 added lines checked, 13 new violations
```

第一處在一個 ASCII 圖裡（Ch 15 的 banned-words 略過了 code block，所以沒抓到——但「非顯然」確實用了被禁的字）。第二處是**撰寫 Ch 28 時新增的**——Ch 15 之後，那條規則已經被編碼成 lint 了，但 lint 只在本課作者記得跑的時候才會跑。 <!-- allow-banned: 討論那個被禁的字本身 -->

兩個都修掉，lint-added 也改成略過 code block 之後：

```
7901 added lines checked, 0 new violations
exit=0
505 hits in 394 files
```

這個實驗示範了 `/correct` 的三件事：

1. **「只擋新增」讓 lint 今天就能用**，不必先清完 505 處
2. **兩支實作同一條規則的工具會不一致**（一支略過 code block、一支不略過）——規則要有**一個**實作，CI 和本機跑同一支（「Run the same command locally and in CI」）
3. **一個只有在人記得時才跑的 lint，和文字規則差不多**。真正讓它變成結構的是：hook、CI、pre-commit——讓它**每次都自動跑**

## 四、/automate-me：你自己的 mode

> poteto-mode is one person's style. The playbooks, routing, and model roles underneath it work just as well with yours.

`/automate-me` 不問你的風格是什麼，它**從你的對話紀錄裡讀出來**：

1. **檢查是否已經有你的 mode 技能**；有就進入更新模式（只挖上次修改之後的紀錄、保留沒被推翻的規則、只為新的模式加段落）
2. **挖你的歷史**：只在這個 workspace 的紀錄裡；平行的 subagent 各負責一段時間（例如最近 2–4 週分成 3 段）；找這些訊號：回覆偏好（長度、語氣、「講白話一點」的糾正）、委派習慣、驗證態度（「做完」的定義）、程式碼與文字紀律、流程慣例（worktree、commit、PR）、後設偏好
3. **跨段交叉比對**，然後**問你哪些模式真的是你**
4. 透過技能寫作流程起草 `<你的名字>-mode/SKILL.md`，跑 unslop，從 worktree 開 PR

官方的建議很務實：

> Start smaller than you think. You don't need many skills on day one, or even this whole plugin. Prompt plainly, watch where agents fail, and add a skill or a check when the same failure shows up twice.

在 Claude Code 上，`/automate-me` 會讀 `~/.claude/projects/<slug>/*.jsonl`（adapter 的 Paths 表）。這些紀錄包含你和 agent 的所有對話——讓它讀之前，確認你接受這件事；它預設只讀當前 workspace。

## 底層機制：三個迴路怎麼組合成一個系統

```
                        你在 session 裡糾正 agent
                                  │
               ┌──────────────────┼──────────────────┐
               ▼                  ▼                  ▼
          這次 session 的     這個錯誤在 repo      這是你一貫的
          教訓（/reflect）    裡發生過兩次以上？    工作偏好？
               │              （/correct）        （/automate-me）
               │                  │                  │
        能用結構擋嗎？        架構 > 型別 >        寫進 <你>-mode
        是 → backlog          lint > 測試 >             │
        否 → 改技能（核准後）  文件                       │
               │                  │                  │
               └──────────────────┼──────────────────┘
                                  ▼
                       每一個改動都用 eval 驗證（Ch 29）
                       「Asking for the eval up front keeps the edit honest.」
```

三個迴路的共同紀律：**一次的異常不是規則**（reflect：「One-offs are not learnings」；correct：「A class counts once it has happened twice」；automate-me：問你哪些模式真的是你）；**能用結構就不用文字**；**每個改動都要能被驗證**。

## 對比與取捨

| 技能 | 輸入的範圍 | 產出 | 層級 | 最大的風險 |
|---|---|---|---|---|
| /reflect | 一次 session | 技能修改（文字）+ backlog | 文字（結構化的移到 backlog） | 從一次奇怪的 session 過度擬合 |
| /correct | 整個 repo 的歷史 | 架構 / 型別 / lint / 測試 + 規則表 | 盡量高 | 一個 lint 太嚴，擋住合理的寫法（用例外機制） |
| /automate-me | 你幾週的對話 | 一份 mode 技能 | 文字 | 把一時的習慣當成風格 |
| 手動在 CLAUDE.md 加一條 | 你的記憶 | 一行文字 | 最低 | 沒有東西會在違反時失敗 |

## 踩雷集錦

1. **在 CLAUDE.md 再加一條粗體規則**：`/correct` 的層級裡，文件是最後一層。本章的實驗示範了一條每次都被載入的文字規則，在全 repo 有 505 處違規。
2. **從一次 session 改技能**：「One weird session is an anecdote, not a rule.」reflect 要你核准每一條，而且只核准「會改變未來決定」的。
3. **讓 lint 立刻擋所有舊的違規**：它會讓每個 PR 都失敗，然後被關掉。「只擋新增」讓它今天就能上線。
4. **兩支工具實作同一條規則**：本章的兩支 lint 對 code block 的處理不同，結果不一致。一條規則一個實作，本機與 CI 跑同一支。
5. **改 plugin 快取裡的技能**：reflect 原文：「Never edit a skill under `plugins/cache/`」。下次 plugin 更新就被蓋掉。複製到專案或使用者的技能目錄再改。

## 進階：再往深一層

- **把 lint-added 接成 Claude Code 的 PostToolUse hook**：每次 agent `Write` 或 `Edit` 一個 `.md` 檔，就跑 lint-added，把結果加回 context。這讓規則在**寫的當下**生效，而不是在本課作者記得跑的時候。對這個 repo 來說，這是把 `learn` 技能那條文字規則推到第 2 層最便宜的方式。
- **為這個 repo 寫規則表**：這個 repo 沒有 CLAUDE.md。`/correct` 會在 agent 指示檔裡維護一張「規則 → 由什麼強制」的表。試著為這個 repo 寫一份：`learn` 技能的哪些規則可以被強制？（輕描淡寫詞 → lint；章節要有「自我檢核」與「延伸閱讀」→ 結構檢查；程式碼要真的跑過 → ？）最後一條是 Final Project 的題目之一。
- **讀 reflect 的三個 reviewer prompt**：`reflect/references/` 下的 judgment、tooling、divergent 三份模板。divergent 那份特別有意思：它專門找「和主流解讀不同的觀點」，是對「三個審查者得出一樣的結論」這種假一致的防禦。

## 動手練習

1. 在你的一個 repo 裡，找出你在 code review 裡寫過兩次以上的同一則評論。依 `/correct` 的四層，它最高可以在哪一層被修正？寫出那個修正（哪怕只是一個 lint 規則的設定），並證明它在一個真實的歷史 commit 上會失敗。
2. 照本章寫出 lint-added，在一個有舊違規的 repo 上跑，確認它只報新增的。然後故意在一個既有檔案的**中間**插入一行違規，確認行號正確。
3. 讀你自己最近一週和 Claude Code 的三段對話（`~/.claude/projects/<slug>/`），手動做一次 `/automate-me` 的第 2 步：列出你反覆說過的偏好（「講白話」「先跑測試」「不要加註解」……）。哪些是你真正的風格，哪些只是那一天的心情？

## 本章重點整理

- 三個時間尺度的學習：一次 session（/reflect）、一個 repo（/correct）、一個人（/automate-me）
- /reflect：三個平行審查者 + synthesizer → Accepted / Rejected / Backlog；能用結構擋的移到 backlog；每一條都要核准；不改 plugin 快取
- /correct：錯誤發生兩次才算類別；修正層級是架構 > 型別 / lint > 測試 > 文件；新的檢查要證明它在真實的歷史錯誤上會失敗；模式已經很普遍時只擋新增；維護一張「規則 → 強制者」的表
- 本章在這個 repo 找到一個典型的類別（README 排版只能靠手動修，沒有 lint 設定），並用「只擋新增」的 lint 抓到本章撰寫期間新增的違規
- /automate-me：從你的對話紀錄讀出風格，問你哪些是真的你，產生 `<你的名字>-mode`；從小開始，同一個失敗出現兩次才加技能或檢查

## 自我檢核

- [ ] 不看本章，能說出 `/correct` 的四個修正層級，以及為什麼人工 review 不在清單上嗎？
- [ ] reflect 為什麼要把「能用結構擋的」從 Accepted 移到 Backlog？這反映了哪一條原則？
- [ ] 「只擋新增的違規」解決了什麼問題？它有什麼代價？
- [ ] 本章的兩支 lint 為什麼結果不一致？你的專案裡有沒有「同一條規則有兩種實作」的情況？

## 延伸閱讀

### 原始碼

- **[skills/reflect/](https://github.com/cursor/plugins/tree/main/pstack/skills/reflect)**、**[skills/correct/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/correct/SKILL.md)**、**[skills/automate-me/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/automate-me/SKILL.md)**
  - **讀哪裡**：correct 全文（很短）；reflect 的 step 4–5；automate-me 的 step 1 的訊號清單

### 官方指南

- **[docs/guide/09-make-it-yours.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/09-make-it-yours.md)**
  - **讀哪裡**：「Generate your own mode」「Capture a session's lessons」「Fix the environment with /correct」
  - **能學到什麼**：三個技能的官方 prompt 範例

### 工具

- **[markdownlint](https://github.com/DavidAnson/markdownlint)**
  - **讀哪裡**：規則清單裡的 MD022（標題前後要有空行）、MD032（清單前後要有空行）、MD012（多個連續空行）
  - **和本章的關聯**：這個 repo 的 `0a9aafe` 修的正是這三條；把它們放進設定檔與 CI，就是 `/correct` 第 2 層的修正
- **[pre-commit](https://pre-commit.com/)**
  - **這是什麼**：在 commit 之前自動跑檢查的框架
  - **和本章的關聯**：讓 lint 「每次都自動跑」最常見的方法之一

### 文章

- **[Google SRE Book — Eliminating Toil](https://sre.google/sre-book/eliminating-toil/)**
  - **這篇說什麼**：重複的、手動的、可以自動化的工作是「toil」，以及怎麼系統性地消除它
  - **和本章的關聯**：「每個 PR 都要 reviewer 抓同樣的錯」就是 toil；`/correct` 是對 agent 時代的 toil 的回應

### 對照閱讀

- **[harness_engineering Ch 38 — 失敗模式與 debug agent](../harness_engineering/38-failure-modes-debugging.md)**（本 repo）
  - **和本章的關聯**：怎麼分類 agent 的失敗；`/correct` 的「錯誤類別」是這種分類在 repo 層級的應用

三個自我改進的迴路讓 pstack 越用越貼近你。最後一章退一步問：它在什麼時候不值得用？

→ [Ch 31 批判與取捨：什麼時候不該用 pstack](./31-critique-tradeoffs.md)
