# Ch 24 — 開 PR、Babysit 與 Shipping

> **目標**：讀懂 pstack 交付流程的三份 playbook。**Opening a PR** 是每一份建造類 playbook 的共用結尾：worktree、小而有序的 commit、Conventional Commits 標題、一份「一分鐘簡報」式的 PR 內文。**Babysit** 把一個 PR 或一疊 PR 推到 merge-ready：先宣告模式、只處理 merge frontier、依「衝突 → review 討論 → CI」的順序。**Shipping** 是 Babysit 之後的那一半：每個 PR 由沒寫過它的 agent 獨立驗證，只合併從底部開始連續通過驗證的那一段。你會用 `git patch-id` 實際看到「rebase 之後舊的驗證結論還能不能用」，並寫一支小工具算出一疊 PR 裡哪些可以合併。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26。patch-id 實驗與可合併段落的計算是實際跑出來的。Babysit 用的 `watch-pr` 腳本需要 Bun，本機沒有安裝，本章不示範它。

## 為什麼需要這個？

agent 寫完程式碼之後，交付還有一大段路：

- PR 要怎麼切？一個大 PR 還是五個小的？
- PR 描述要寫什麼？
- CI 紅了怎麼辦？重試？修？
- 有人留了二十則 review 評論，哪些要修、哪些要反駁？
- 全部綠了，可以合併了嗎？

每一步都有 agent 典型的錯誤：一個 3,000 行的 PR、一篇 lab notebook 式的 PR 描述、對 flaky CI 無限重試、照單全收所有 review 評論、「全綠了就合併」。

## 先建立直覺：交付的三段

```
  建造類 playbook（Bug fix / Feature / Refactoring / Perf / Hillclimb）
         │
         ▼
  ┌─ Opening a PR ───────────────────────────────────────────────┐
  │ worktree → 小而有序的 commit → /deslop、/no-comments          │
  │ → technical-writing + unslop 的標題與內文 → 開 PR（ready，不是 draft） │
  │ → 貼出 URL，繼續做。**不會**自動開始 babysit                      │
  └──────────────────────────────────────────────────────────────┘
         │  （使用者要求時，通常在整疊 PR 都建好之後）
         ▼
  ┌─ Babysit ──────────────────────────────────────────────────────┐
  │ 宣告模式 → 只處理 merge frontier → 衝突 → review 討論 → CI      │
  │ → 停在 merge-ready。**永遠不合併**                                │
  └──────────────────────────────────────────────────────────────┘
         │  （使用者要求「land / ship / merge」時）
         ▼
  ┌─ Shipping ─────────────────────────────────────────────────────┐
  │ 每個 PR 一個獨立的驗證者（不是作者）→ 只合併從底部開始連續通過的段落   │
  │ → 用 patch-id 確認舊的結論還描述現在的程式碼 → 由下而上，一次一個       │
  └──────────────────────────────────────────────────────────────┘
```

三段之間有兩道人工閘門：開 PR 不等於開始盯 PR；merge-ready 不等於合併。

## 一、Opening a PR

### Worktree

> Work from a git worktree off main. Subagents inherit it. Multiple `Task` calls on the same branch each get their own worktree, or `git fetch && git reset --hard origin/<branch>` between them. Dirty branch with unrelated work: patch out, fresh worktree, apply.

和 Ch 16 的隔離是同一件事。「Snarled worktree: reset from main, redo minimally.」——一個搞得一團亂的 worktree，從 main 重來、最小化地重做，比試圖解開它便宜。

### Commit

> Commit liberally. Rebase into small, ordered commits before opening PRs. Each commit is a future PR: landable, ordered to tell the story.

「每一個 commit 都是一個未來的 PR」——這是一個很強的要求：每個 commit 都要能單獨合併、單獨回退。這正是 Sequence Work into Verifiable Units 的交付面（Ch 14）。「Commit bodies don't restate the subject.」

### 標題

Conventional Commits：`type(scope): subject`，沒有結尾句號。type 例如 `feat`、`fix`、`docs`、`refactor`、`test`、`chore`、`perf`；scope 是範圍；subject 短、祈使句，「names a real symbol when one carries the change」。原文範例：`fix(pstack): retarget opening-a-pr babysit trigger`。

### 內文：一分鐘簡報

> The PR body is a one-minute briefing, not a lab notebook, for a reviewer who has the diff. It says why the change exists, what it leaves out, what it could break, and how you proved it works.

固定的 `##` 段落，依序，沒話說的段落就刪掉：

| 段落 | 內容 | 不要放 |
|---|---|---|
| `## Why` | 問題與做法，1–3 句；開頭是使用者的目標（poteto-mode Non-negotiables） | SHA、rebase 歷史、「based on main」 |
| `## What changed` | 1–3 條；只在真實符號承載了改動時才寫它的名字；改名要寫兩邊 | 檔案逐一清單 |
| `## Scope` | **永遠要有**；1–3 項：涵蓋了什麼、刻意不涵蓋什麼 | 符號、路徑 |
| `## Tradeoffs` | 只放 reviewer 會問的被拒絕方案 | 沒有真正選擇時就不要這段 |
| `## Blast Radius` | 1–2 句：碰到誰、為什麼安全或有風險；main 是紅的就說留著它紅的代價 | — |
| `## Verification` | 1–3 條，每條是一個真實的執行路徑與結果；效能只放一個主要數字 `before → after` | 樣本數方法論、swarm 細節、指標表 |

「The body becomes the squash commit body, so keep that commit under about 40 lines.」完整的 SHA、swarm 或 arena 的 lane 細節、「CLEAN」判決，放在連結的附件裡。

Ch 23 的改寫後 PR 描述就是這個格式的最小版本。

### 開 PR 的細節

- **Forge**：預設 `gh`；`origin` CLI 存在且能解析 repo 時優先用它。「Do not require Graphite (`gt`).」
- **Ready，不是 draft**：「Open every PR ready, never as a draft.」
- **大小**：「Prefer five narrow PRs to one large PR.」
- **Stack**：一條 base-branch 鏈。根 PR 指向 trunk，每個子 branch rebase 到父的確切 tip、PR 指向父 branch。
- **不會自動 babysit**：「Opening a PR does not start a babysit. Post the URL and keep building. ... A babysit for each new PR stalls the build and spends checks on commits later waves restart.」

在 Claude Code 上，adapter 規定沒有內建 PR 工具，用 step 給的 forge 指令（例如 `gh pr create`）；這不算 skip，不用寫 skip 那一行。

## 二、Babysit：推到 merge-ready，然後停

> **You own the merge frontier. Declare a mode, clear one PR at a time, stop where the human's call begins.**

### Step 1：先宣告模式

| 模式 | 觸發片語 | 做什麼 |
|---|---|---|
| `drive` | 「babysit this」「get it green」「merge-ready」 | 跑迴圈直到 merge-ready |
| `background` | 計畫還在執行中 | 分類但不阻塞 |
| `threads-only` | 「address the bugbot comments」 | 只回 review 評論，不碰別的 |
| `check` | 「check on X」「is it green」 | 一次狀態檢查與報告 |

沒宣告就預設 `drive`。小的或只改文件的 PR 用 `check`。

### Step 2：只處理 merge frontier

> The lowest unmerged PR is the only one that matters until it merges. Upstack threads get read and batched, never fixed at the cost of restarting the frontier's checks.

一疊 PR 裡，**最底下還沒合併的那一個**是唯一重要的。上面的 PR 的評論先讀、先收集，但不要為了修它們而讓 frontier 的 CI 重跑。「If you catch yourself upstack while the frontier is red, stop and go back down.」

### Step 3–5：一疊一個 babysitter、不改 stack 拓撲、順序是衝突 → 討論 → CI

- **一疊 PR 只有一個 babysitter**（Separate Before Serializing Shared State）
- **不改 stack 拓撲**：「No base retarget, rebase, stack-wide submit, or force-push from inside a babysit.」要 rebase 的事回報給 owner
- **順序是衝突、review 討論、CI**，「Batch every known fix into one push wave」——所有已知的修正一次推，讓 CI 只重跑一次。衝突是唯一「回報而不解決」的阻礙

### Step 7：先分類 CI 再重試

> Flake or infrastructure earns one fresh build, never a job retry. One retry only. An identical second failure means it was never flake ... A failure in code the diff never touches means a stale base, so check with `git merge-base --is-ancestor` before assuming flake.

```
  CI 紅了
     │
     ├─ 失敗在 diff 沒碰到的程式碼？ ── 是 ──▶ 可能是 base 過期：git merge-base --is-ancestor 檢查
     │                                     → 回報需要 rebase，不要重試
     ├─ 看起來像 flake 或基礎設施？ ── 是 ──▶ 一次全新的 build（不是 job retry），只一次
     │                                     → 第二次一模一樣地失敗 → 它從來不是 flake，去讀 log
     └─ 失敗在 diff 自己的程式碼 ──────────▶ 這是唯一值得一個 commit 的情況
```

### Step 8：懷疑地處理 bot 評論

> Bugbot is triaged skeptically, always. Verify each claim against the code per `../references/bugbot-triage.md`.

poteto-mode 的 Non-negotiables 也說：「They catch real bugs and also file non-issues and nitpicks, so assess each on its merits and dismiss noise with a concrete reason instead of churning code.」真的發現要用「先紅後綠」的證明修，而且修在**擁有那段程式碼的最低 PR**；雜訊要附上具體的反證駁回。這和 Ch 20 interrogate 的「只收證物」是同一種態度。

（Bugbot 是 Cursor 的功能。在 Claude Code 上，adapter 說 Bugbot 在 GitHub PR 上留下的評論就是一般的 review thread，照常處理；需要啟動、等待或設定 Bugbot 的步驟，在 PR 沒有 Bugbot 檢查時就跳過。）

### Step 9：停在人的那條線

> Owner approval is a wait, not a blocker to fix. Babysitting never authorizes merging. Only an explicit request to merge, land, ship, or merge when ready does.

即使全部綠了，Babysit 也不合併。官方指南：「Babysit stops at merge-ready. It never merges, even with everything green, because merging is a different decision.」

## 三、Shipping：綠的不等於安全的

> **You own what lands. Verify each PR independently, land only the verified run from the root, then keep your hands off the stack.**

### Step 1：每個 PR 一個獨立驗證者

> One verifier per PR ... runs the change the way a user would with the matching control skill ..., parent against head, and posts `PASS`, `PASS+NOTES`, or `FAIL` on that PR. Only a verdict from an agent that did not write the code counts, because an author checks what it meant to build. Green CI and an approving bot review are not verdicts, because neither runs the change as a user would.

「an author checks what it meant to build」——作者檢查的是他**想要**做出來的東西。Ch 1 信條 3 的原文出處就在這裡。

### Step 2：只合併從底部開始、連續通過驗證的那一段

> Walk up from the lowest unmerged PR and stop at the first one without `PASS` or `PASS+NOTES`. The PRs below it are the run, and the top one is the ceiling. A verified PR above an unverified one waits, because it cannot land without the code below it.

寫成一支小工具：

```js
// landable.mjs：從一疊 PR 的狀態算出 merge frontier、可合併的段落、在缺口上方等待的 PR
const stack = JSON.parse(process.argv[2]);
const run = [];
for (const pr of stack) {
  if (pr.merged) continue;
  if (pr.verdict === "PASS" || pr.verdict === "PASS+NOTES") run.push(pr.id); else { console.log(`ceiling: #${pr.id} has ${pr.verdict ?? "no verdict"}`); break; }
}
const frontier = stack.find((p) => !p.merged);
console.log(`merge frontier (babysit works here): #${frontier?.id ?? "none"}`);
console.log(`land bottom-up, one at a time: ${run.length ? run.map((id) => "#" + id).join(" -> ") : "nothing"}`);
const waiting = stack.filter((p) => !p.merged && !run.includes(p.id) && (p.verdict ?? "").startsWith("PASS"));
if (waiting.length) console.log(`verified but waiting above the gap: ${waiting.map((p) => "#" + p.id).join(", ")}`);
```

一疊五個 PR（由下而上）：#101 已合併、#102 PASS、#103 PASS+NOTES、#104 FAIL、#105 PASS：

```bash
node landable.mjs '[{"id":101,"merged":true},{"id":102,"verdict":"PASS"},{"id":103,"verdict":"PASS+NOTES"},{"id":104,"verdict":"FAIL"},{"id":105,"verdict":"PASS"}]'
```

```
ceiling: #104 has FAIL
merge frontier (babysit works here): #102
land bottom-up, one at a time: #102 -> #103
verified but waiting above the gap: #105
```

#105 自己通過了驗證，但它在 #104 上面；合併它會把 #104 沒通過的程式碼一起拉進 trunk。另一個例子，最底下的 PR 還沒有結論：

```
ceiling: #201 has no verdict
merge frontier (babysit works here): #201
land bottom-up, one at a time: nothing
verified but waiting above the gap: #202
```

什麼都不能合併。「no verdict」和「FAIL」在這條規則裡是一樣的——缺少證據不等於通過（和 Ch 19 swarm 的「gap 不算 pass」同一個原則）。

（原文還有一個例外：一個上方的 PR 如果它的 branch 既不包含、也不需要下方的程式碼，可以移到 trunk 上單獨合併——但「It needs a fresh verdict first, because its old one ran with that code beneath it and trunk does not have it.」本章的工具沒有實作這個例外。）

### Step 3：用 patch-id 確認結論還描述現在的程式碼

> A verdict covers a patch, and a chain rebase moves every PR above it to a new SHA without failing any check. Compare `git patch-id` at the verdict SHA with the current head before trusting an older verdict.

`git patch-id` 對一個 diff 算出一個雜湊：**同樣的改動內容，不管它在哪個 commit 上、SHA 是什麼，patch-id 都一樣**。這讓我們能判斷「rebase 之後，這個 PR 的改動本身變了沒」。

實驗：一個 PR 把 limit 從 3 改成 5，在 `6d7e100` 上拿到 PASS。

```bash
git show HEAD | git patch-id --stable
```

```
verdict PASS recorded at 6d7e100, patch-id b1a2a90b2f4c
```

trunk 上有人改了 README（不相關）。把 PR rebase 到新的 trunk：

```
after rebase onto new trunk: head ce27feb, patch-id b1a2a90b2f4c -> same patch, verdict can stand
```

SHA 變了（`6d7e100` → `ce27feb`），patch-id 沒變。改動的內容完全一樣，舊的結論可以沿用。

trunk 上又有人改了**同一個檔案的同一行**（加了註解與一個新的常數）。再 rebase，這次有衝突，手動解決：

```
Auto-merging config.mjs
CONFLICT (content): Merge conflict in config.mjs
after conflicted rebase: head 2920daf, patch-id 7ab5ec609fc5 -> patch changed, fresh verdict needed
```

解決衝突改變了 patch 的內容，patch-id 不同了。舊的 PASS 驗證的是另一個 patch，**不能沿用**。

原文對這個規則還有一個細緻的例外：如果兩個 patch 只差在測試、文件或 lint 設定，一條 lane 的結果可以保留——前提是它跑的東西在兩個 SHA 上建置出來相同（「apart from noise」）。沒有建置輸出可以比較的 lane（例如 dev server）就要重跑。

### Step 4–5：由下而上、一次一個；合併時不碰 stack

- 永遠合併段落裡**最低**的那一個未合併 PR（`gh pr merge <pr> --squash`）
- **讀回 PR 確認結果**：「a merge command can report success when a merge queue only queued the PR, and a requested auto-merge is not a merge」
- 合併後把上一個 PR 的 base 改到 trunk、把它的 patch 移到合併結果上、`--force-with-lease` 推送；patch-id 規則決定它的結論是否保留
- 「Leave the stack alone while it lands.」

## 底層機制：為什麼「全綠」不是合併的理由

```
   「全綠」實際上證明了什麼                          Shipping 要求的證明
   ─────────────────────                          ─────────────────
   CI：寫好的測試在這個 SHA 上通過                    一個沒寫過這段程式碼的 agent，
   bot review：一個模型讀了 diff 沒找到問題              像使用者一樣跑了這個改動（parent vs head），
   作者：「我測過了」                                   在這個 patch 上，給出 PASS
         │                                                 │
         ▼                                                 ▼
   都沒有「像使用者一樣跑過這個改動」                  而且結論綁定 patch-id，
   作者檢查的是他想做出來的東西                         rebase 後要確認它仍然描述現在的程式碼
```

## 對比與取捨

| | Opening a PR | Babysit | Shipping |
|---|---|---|---|
| 觸發 | 每個建造類 playbook 的結尾 | 使用者要求（通常整疊建好後） | 使用者要求 land / ship / merge |
| 產出 | 一個 ready 的 PR 與 URL | merge-ready 的 frontier | 合併的 PR |
| 會合併嗎 | 否 | **永遠不會** | 只合併連續通過驗證的段落 |
| 誰驗證 | 作者（建造 playbook 裡的驗證） | CI + review + 作者修正 | 獨立的、不是作者的 agent |
| 最常見的錯 | 一個大 PR、lab notebook 式描述 | 處理 frontier 以上的 PR、無限重試 CI | 「全綠就合併」、沿用 rebase 前的結論 |

## 踩雷集錦

1. **開 PR 之後立刻開始 babysit**：原文明確反對：它會卡住建造，而且「spends checks on commits later waves restart」。整疊建好再盯。
2. **flaky 就重試，再重試**：「One retry only. An identical second failure means it was never flake.」而且失敗在 diff 沒碰到的程式碼時，先檢查 base 是否過期。
3. **修 frontier 以上的 PR 評論**：會讓 frontier 的 CI 重跑。先收集，等 frontier 合併後的下一波推送。
4. **rebase 之後沿用舊的驗證結論**：本章的衝突 rebase 讓 patch-id 變了，舊的 PASS 驗證的是另一個 patch。
5. **合併一個在缺口上方的已驗證 PR**：本章的 #105 通過了驗證，但合併它會把 #104 的未驗證程式碼一起帶進來。

## 進階：再往深一層

- **讀 `bugbot-triage.md`**：poteto-mode 的 `references/bugbot-triage.md`（9.6KB）是一份「怎麼判斷一則 bot 評論是真 bug、雜訊還是需要問人」的評分標準，而且 Babysit step 9 要求把有用的駁回模式加回這份標準——一個會自我累積的審查知識庫。
- **讀 `watch-pr`**：`poteto-mode/scripts/watch-pr/` 是一支 TypeScript 寫的 GitHub PR 監控工具（附測試），輸出 `READY`、`WAITING`、`ADVANCE`、`COMPLETE` 等結論。它是 Build the Lever 在「盯 PR」這件事上的完整實作：與其讓 agent 每次自己拼 `gh` 指令，給它一支有明確結論的工具。需要 Bun 才能執行。
- **把 patch-id 檢查寫進 CI**：在合併前檢查「最後一次驗證的 patch-id 是否等於目前 head 的 patch-id」，不等就擋下。這把 Shipping step 3 從 agent 的紀律變成一個結構化的檢查（Encode Lessons in Structure）。

## 動手練習

1. 照本章做 patch-id 實驗，然後試第三種 rebase：trunk 改了**同一個檔案的另一行**（不衝突）。patch-id 會變嗎？為什麼？（提示：patch-id 對 diff 的內容雜湊，但 diff 的 context 行呢？用 `git patch-id --stable` 的文件確認。）
2. 擴充 landable.mjs，實作原文的例外：上方一個不依賴下方程式碼的 PR，可以標成「可單獨移到 trunk，但需要新的驗證」。你要怎麼判斷「不依賴」？
3. 拿你最近開的一個 PR，用本章的段落表重寫它的描述。原本的描述有哪些段落是「lab notebook」？哪一段是缺的（通常是 `## Scope`）？

## 本章重點整理

- Opening a PR：worktree、每個 commit 都是一個未來的 PR、Conventional Commits 標題、Why / What changed / Scope / Tradeoffs / Blast Radius / Verification 的一分鐘簡報、ready 不是 draft、不會自動 babysit
- Babysit：先宣告模式；只處理 merge frontier；衝突 → review 討論 → CI，一次推送所有已知修正；CI 只重試一次、先檢查 base 是否過期；bot 評論懷疑地處理；停在 merge-ready，永遠不合併
- Shipping：每個 PR 一個不是作者的驗證者；只合併從底部開始連續通過的段落（本章：#102、#103 可合併，#105 在缺口上方等待）；用 patch-id 確認 rebase 後結論仍描述現在的程式碼（本章：乾淨 rebase 保留、衝突 rebase 作廢）
- 「全綠」不是合併的理由：CI、bot、作者都沒有像使用者一樣跑過這個改動

## 自我檢核

- [ ] 不看本章，能說出 Babysit 的四種模式與各自的觸發片語嗎？
- [ ] 為什麼 Babysit 只處理 merge frontier？處理 frontier 以上的 PR 會付出什麼代價？
- [ ] `git patch-id` 解決了什麼問題？一個 rebase 之後 SHA 變了、patch-id 沒變，代表什麼？
- [ ] 本章的 #105 自己通過了驗證，為什麼不能合併？在什麼條件下它可以？

## 延伸閱讀

### 原始碼

- **[playbooks/opening-a-pr.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/opening-a-pr.md)**、**[babysit.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/babysit.md)**、**[shipping.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/shipping.md)**
  - **讀哪裡**：三份全文；Opening a PR 的「Descriptions」段，Babysit 的 step 7，Shipping 的 step 2–3
- **[references/bugbot-triage.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/references/bugbot-triage.md)**
  - **能學到什麼**：一份可以借用的 bot 評論分類標準

### 官方文件

- **[git-patch-id 官方文件](https://git-scm.com/docs/git-patch-id)**
  - **讀哪裡**：`--stable` 與 `--unstable` 的差別，以及它忽略了什麼（行號、空白）
  - **能學到什麼**：本章實驗背後的雜湊規則
- **[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)**
  - **讀哪裡**：Specification 一節
  - **能學到什麼**：PR 標題格式的原始規格

### 文章

- **[Google Engineering Practices — Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html)**
  - **這篇說什麼**：為什麼小的變更比較好審、比較安全、比較快合併，以及怎麼切
  - **和本章的關聯**：「Prefer five narrow PRs to one large PR」的完整論證
- **[Graphite — Stacked diffs / stacked PRs](https://graphite.dev/guides/stacked-diffs)**
  - **這是什麼**：stacked PR 工作流的介紹
  - **和本章的關聯**：pstack 的 stack 規則（子 branch rebase 到父的 tip、由下而上合併）就是這種工作流；注意 pstack 明確「Do not require Graphite (`gt`)」，它只用 `gh` 或 `origin` 實作同樣的工作流

Part 4 結束。你已經能讓 agent 在一個任務裡從頭到尾做對並交付。接下來的 Part 5，處理你不在旁邊看的時候。

→ [Ch 25 Autonomous run 與過夜契約](./25-autonomous-run.md)
