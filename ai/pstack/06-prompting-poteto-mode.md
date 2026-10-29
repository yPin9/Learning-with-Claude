# Ch 6 — 怎麼下 prompt：目標、完成條件、證據

> **目標**：學會寫一個讓 poteto-mode 選對 playbook、而且能自己判斷「做完了沒」的 prompt。你會掌握官方指南的「五個要素、兩個不要」，知道哪些短片語是強烈的路由訊號，學會用 `/pstack:sharpen` 與 `/pstack:in-your-own-words` 在送出前校正方向，並寫一支小工具把常見的 prompt 錯誤變成自動檢查。

> **環境**：pstack 0.15.36、Node.js 26。`prompt-lint.mjs` 與它的輸出是實際跑出來的；agent 的回覆是示意。

## 為什麼需要這個？

pstack 的 playbook 再嚴謹，輸入都是你的一句話。這句話決定三件事：

1. **路由**：走 Investigation、Bug fix 還是 Feature
2. **完成條件**：什麼時候可以停
3. **證據形式**：最後拿什麼給你看

如果你的 prompt 是「幫我看一下匯出的問題」，路由要猜（看一下是要查原因還是要修？），完成條件不存在（看到什麼程度算看完？），證據沒有要求（它會給你一段文字解釋）。playbook 只能在你給的框架裡嚴謹。

## 先建立直覺：你在寫一張工單，不是在下指令

```
  壞的工單                               好的工單
  ─────────                             ─────────
  「匯出有問題，處理一下」                 問題：CSV 匯出從昨天部署後少了最後一列
                                        已知：失敗的 job id 是 4812
                                        限制：先重現再修
                                        完成：6 萬列的測試資料全部匯出
                                        證據：給我看修前修後的列數
```

好的工單**不寫怎麼做**。它寫的是：現在怎樣、要變成怎樣、怎麼知道變成了、你已經知道什麼、有什麼不能碰。「怎麼做」是 playbook 和 agent 的事。

## 一、五個要素

官方指南（02-poteto-mode.md）說一個有用的 prompt 最多帶五樣東西，每樣一句話就夠：

| 要素 | 意思 | 例子 |
|---|---|---|
| **目標** | 什麼壞了，或你要什麼 | the csv export drops its last row since yesterday's deploy |
| **完成檢查** | 必須能 pass 或 fail | done means the 60k-row fixture exports every row |
| **你要看的證據** | 真實的指令輸出、流程影片、寫入的值、前後數字 | show me the row counts before and after |
| **你已經知道的** | 症狀、重現步驟、log、連結，幫它省一次搜尋 | failing job id is 4812 |
| **真正的限制** | 會改變 agent 行為的條件 | repro first; zero behavior change; don't change any code yet |

五樣全有的官方範例：

```text
/pstack:poteto-mode the csv export drops its last row since yesterday's deploy. failing job id is 4812. repro first, then fix. done means the 60k-row fixture exports every row. show me the row counts before and after.
```

「完成檢查」是最常被省略、也最重要的一項。官方原文：「It must be able to pass or fail. 'Make it better' and 'work on it for an hour' aren't checks.」沒有完成檢查，Prove It Works 原則就沒有東西可以 prove。

## 二、兩個不要

**不要寫「怎麼做」。**

> Say what to achieve, and leave the agent room to find a better path than the one you'd pick.

「用 Redis 快取查詢結果讓它變快」把解法寫死了。「這個查詢 p50 是 800ms，目標 200ms 以下」留下空間——也許真正的問題是少了索引，根本不需要快取。Perf issue playbook 的效能口訣第一條就是「Don't do it」：先問這個工作能不能整個不做。你寫死「加快取」，就跳過了這個問題。

列舉技能也算是「寫怎麼做」：

> **Pitfall:** don't enumerate skills in your prompt ("use /how, then /architect, then /arena..."). The playbook already sequences them, and a hand-written sequence usually reorders or drops steps the playbook would have kept.

**一開始不要講你對原因的猜測。**

> A stated guess narrows the search to wherever you pointed. Let the agent restate the problem before you share your hunch.

你說「我覺得是 batch size 的問題」，agent 會優先去看 batch size，找到一個看起來相關的地方就改。這是錨定效應。如果你真的有線索，等它先重述完問題、提出它自己的假設，再把你的猜測當成「另一個假設」丟進去。

## 三、路由訊號：短片語，大影響

有些片語幾乎決定了路由與行為：

| 片語 | 效果 |
|---|---|
| `repro first` | Bug fix 第 1 步不能被省略；修之前一定要先重現 |
| `don't change any code yet` | 釘住 Investigation：只讀、只回答 |
| `zero behavior change` | 釘住 Refactoring：先建立行為釘（characterization test） |
| `new task` | 重新路由，不要接著上一個 playbook 做 |
| `let me review before proceeding` | 在設計後加一個人工檢查點（architect 的「with checkpoint」） |
| `i'm stepping away` / `going to bed` | session 覆寫：不再停下來問；並路由到 figure-it-out（留決策日誌） |
| `in a fresh worktree off <base>` | 隔離工作目錄，避免和其他 agent 互踩 |

`new task` 值得多說一句。官方指南：

> "new task" tells `/poteto-mode` to re-match rather than continue the prior playbook. ... Without those two phrases, a mode mid-Feature tends to treat your question as the next feature step.

一個做到一半的 Feature 執行，你問「順便看一下為什麼登出後快取還在」，它很可能把這當成 Feature 的下一步，開始改快取的程式碼。加上「new task ... don't change any code yet」，它才會切換成 Investigation。

> **關於中文 prompt**：官方的片語都是英文。路由是模型的語意判斷（Ch 5），所以「先重現再修」「先不要改任何程式碼」應該也能起作用——但這是**推論**，本課沒有做過中英文 prompt 的對照實驗。如果你發現中文 prompt 的路由不穩，最便宜的修法是在中文句子裡保留那幾個英文片語。

## 四、送出前校正：`/sharpen` 與 `/in-your-own-words`

**`/pstack:in-your-own-words`**：整個技能只有一句話——「Restate in your own words what you think the user's goals are and what problem they're trying to solve.」它不跑任何 playbook，只重述。適合在一個雜亂的報告（一長串討論、一個模糊的 bug 回報）丟進來之後，先確認 agent 理解對了。

官方的做法是把重述變成任務的第一步：

```text
/pstack:poteto-mode read this thread. restate the underlying issue in your own words, in plain english. don't change any code yet.
```

> A misreading shows up in the restatement, before any code exists. Correct it there, and it costs you one message instead of one wrong fix.

**`/pstack:sharpen`**：讓 prompt「更深而不是更大」。它要 agent 先做四件事再改寫你的 prompt：

1. 這個領域頂尖的專業人士會做什麼你沒要求的事
2. 他們會從什麼最原始的材料開始（而不是別人消化過的）
3. 他們會用什麼具體方法和工具
4. 除了你和 agent 之外，**什麼東西**能證明結果是對的

然後改寫你的 prompt 去要求這些，並且「Leave choices that are mine to make open instead of deciding them.」它**不做任務**，只還你一個更好的 prompt。

（示意）

```text
/pstack:sharpen write a migration guide for the v2 config format.
```

> 頂尖的做法：從真實使用者的 v1 設定檔開始，而不是從 schema 文件開始；把每一個破壞性變更配上一個「改前 / 改後」的實際範例；附一支能自動轉換的腳本。
> 最原始的材料：repo 裡所有 v1 設定檔的實際樣本、v1→v2 的 diff、issue tracker 裡跟設定有關的抱怨。
> 外部驗證：拿三個真實的 v1 設定檔，照指南操作，轉出來的 v2 能被新版本載入且行為一致。
>
> 改寫後的 prompt：write a migration guide for the v2 config format. start from the real v1 config files in this repo... done means three real v1 configs, migrated by following the guide, load in v2 and behave the same. leave the decision on whether to ship an auto-migration script to me.

第 4 點最有價值：它逼你想出一個**不是 agent 自己說了算**的完成條件。

## 五、接續的 prompt：越短越好

當對話裡已經有足夠的 context，接續的 prompt 可以很短：

```text
/pstack:poteto-mode do it
```

```text
continue
```

```text
keep going until done
```

> Short works because the playbook holds the structure ... Your words carry the intent, and the skill carries the rigor.

在 Cursor 裡，作者建議用 Custom Mode 讓 poteto-mode 每一輪都留在 context 裡。Claude Code 沒有一模一樣的機制；一個 skill 被載入後會留在對話裡，但長對話被壓縮時可能被摘要掉。所以在 Claude Code 裡，長任務的接續 prompt 帶上 `/pstack:poteto-mode` 前綴比較保險（這是依 Claude Code 壓縮行為的推論）。

## 六、把常見錯誤變成檢查：prompt-lint

官方指南的 Pitfalls 列了幾種 prompt 錯誤。照 Encode Lessons in Structure 原則，與其每次靠記憶，不如寫一支檢查：

```js
// prompt-lint.mjs：用關鍵字粗略檢查一個 prompt 有沒有踩官方指南列的坑
const prompt = process.argv.slice(2).join(" ");
const p = prompt.toLowerCase();
const findings = [];

const has = (...words) => words.some((w) => p.includes(w));

if (!has("done means", "done when", "until", "stays", "must", "verify", "show me", "完成", "直到", "驗證", "給我看"))
  findings.push("no done check: say what result passes or fails");
if (/\b(for|work on (it|this) for) \d+ ?(min|minute|hour|h)\b/.test(p) || /(\d+ ?(分鐘|小時))/.test(prompt))
  findings.push("a duration is not a finish condition: replace it with a predicate");
if (has("make it better", "improve it", "clean it up", "弄好一點", "優化一下"))
  findings.push("vague goal: name the number, output, or behavior that should change");
if (/use \/\w+.*then \/\w+/.test(p) || /先 ?\/\w+.*再 ?\/\w+/.test(prompt))
  findings.push("enumerated skills: state the goal and let the playbook sequence them");
if (has("i think the cause", "probably because", "the bug is in", "我覺得是", "應該是因為"))
  findings.push("leading theory: ask for a restatement first, share the hunch after");
if (!has("show me", "evidence", "output", "before and after", "video", "screenshot", "給我看", "證據", "前後"))
  findings.push("no proof requested: ask for the command output, artifact, or number");

console.log(findings.length ? findings.map((f) => `WARN ${f}`).join("\n") : "OK");
process.exit(findings.length ? 1 : 0);
```

先對四個 prompt 跑：

```bash
node prompt-lint.mjs "fix the export bug, work on it for 2 hours"
```

```
WARN no done check: say what result passes or fails
WARN no proof requested: ask for the command output, artifact, or number
```

```bash
node prompt-lint.mjs "use /how then /architect then /arena to make it better. i think the cause is the batch size"
```

```
WARN no done check: say what result passes or fails
WARN vague goal: name the number, output, or behavior that should change
WARN enumerated skills: state the goal and let the playbook sequence them
WARN leading theory: ask for a restatement first, share the hunch after
WARN no proof requested: ask for the command output, artifact, or number
```

```bash
node prompt-lint.mjs "the csv export drops its last row since yesterday. failing job id is 4812. repro first, then fix. done means the 60k-row fixture exports every row. show me the row counts before and after."
node prompt-lint.mjs "匯出的 CSV 少了最後一列。先重現再修。完成的定義是 6 萬列的測試資料全部匯出，給我看修前修後的列數。"
```

```
OK
OK
```

**注意第一個 prompt 的結果。** 「work on it for 2 hours」明明是一個「時間長度不是完成條件」的典型例子，但 duration 這條規則**沒有觸發**。原因是正規表示式寫的是 `hour\b`，而「hours」的 `r` 後面還有 `s`，`\b` 不成立。

這是一個很好的反面教材：**一條你從沒看過它抓到目標的檢查，你不知道它到底有沒有在檢查。** 這和 Ch 3「驗證腳本要先看它紅一次」是同一個道理，只是方向相反——這裡是「規則要先看它對該抓的案例真的觸發一次」。

修正（允許複數）：

```js
if (/\b(for|work on (it|this) for) \d+ ?(mins?|minutes?|hours?|h)\b/.test(p) || /(\d+ ?(分鐘|小時))/.test(prompt))
```

```bash
node prompt-lint.mjs "fix the export bug, work on it for 2 hours"
node prompt-lint.mjs "修好匯出的 bug，做 2 小時就好"
```

```
WARN no done check: say what result passes or fails
WARN a duration is not a finish condition: replace it with a predicate
WARN no proof requested: ask for the command output, artifact, or number
WARN no done check: say what result passes or fails
WARN a duration is not a finish condition: replace it with a predicate
WARN no proof requested: ask for the command output, artifact, or number
```

這支工具是關鍵字比對，很粗糙：它會把「until I'm bored」當成有完成條件，也看不出「done means it looks good」其實不可檢查。它的價值不在準確，而在**把你容易忘的坑擺在眼前**。真正的判斷還是要你自己做——或交給 `/pstack:sharpen`。

## 底層機制：prompt 怎麼流進 playbook

```
  你的 prompt
     │
     ├─ 目標 ──────────────▶ 第一則訊息的「重述」
     │                       子代理 brief 的開頭
     │                       PR 的 ## Why
     │                       最後回覆的開頭
     │
     ├─ 限制（repro first 等）─▶ 路由判斷 + 釘住特定步驟
     │
     ├─ 完成檢查 ─────────────▶ Verify 步驟的判準
     │                        Autonomous run 的 predicate
     │
     ├─ 證據要求 ─────────────▶ 最後回覆必須附上的產物
     │
     └─ 已知資訊 ─────────────▶ how / why 的種子，省一次搜尋
```

poteto-mode 的 Non-negotiables 要求把使用者的目標**一路傳下去**：「Restate the user's goal in each subagent brief, and open each PR's `## Why` with it. Open each final report with it too.」你的第一句話會出現在四個地方。寫好它的報酬是四倍。

## 對比與取捨

| prompt 風格 | 例子 | 結果 |
|---|---|---|
| 指令式 | 「在 export.js 第 42 行把 `<` 改成 `<=`」 | 精確，但如果你判斷錯，它照錯的做。這種一行改動也不需要 poteto-mode |
| 願望式 | 「讓匯出更穩定」 | 路由要猜、沒有完成條件、證據隨便給 |
| 工單式（五要素） | 本章範例 | 路由清楚、能自我驗證、證據可審 |
| 工單式 + 預先猜測 | 「……我覺得是 batch size」 | 錨定效應，可能修在你猜的地方而不是病因 |

## 踩雷集錦

1. **用時間當完成條件**：「做兩個小時」「跑到早上」。官方原文：「a duration is not a finish condition ... you'll wake up to four hours of motion instead of a result.」換成「直到 X 通過」。
2. **完成條件不可檢查**：「done means it looks good」「done means it's clean」。問自己：一個不認識你的人，能不能只看結果就判斷 pass 或 fail？
3. **在 prompt 裡排技能順序**：你的順序幾乎一定比 playbook 少步驟。真的想要某個技能，只在你要**覆寫**某個預設時才點名（例如「這次 arena 開 5 個候選」）。
4. **任務切換不說 new task**：長對話裡直接問新問題，它會當成上一個任務的延續。
5. **相信一支沒看過它觸發的檢查**：本章的 prompt-lint 示範了一條規則因為正規表示式的一個 `\b` 而永遠不會觸發。任何 lint、測試、驗證腳本，都要先確認它對該抓的案例真的會抓。

## 進階：再往深一層

- **把 prompt-lint 做成 hook**：Claude Code 的 UserPromptSubmit hook 可以在 prompt 送出前執行腳本。用它在偵測到 `/pstack:poteto-mode` 開頭時跑 prompt-lint，把 WARN 加進 context，讓 agent 在第一則訊息裡主動問你缺的那一項。注意這和 Never Block on the Human 有張力：比較好的做法是讓 agent 「用合理預設繼續，並在重述裡說明它補了什麼假設」，而不是停下來問。
- **讀 `sharpen` 的原文**：它只有幾行，但它的第 2 點（「rawest material」）是一個很深的想法：agent 傾向從別人整理過的二手資料開始（README、摘要），而專家從原始資料開始（log、原始碼、真實樣本）。這個差別在 `why` 技能的設計裡會再出現（Ch 7）。

## 動手練習

1. 拿你最近給 Claude Code 的三個 prompt，用五要素表逐一檢查。每個缺了什麼？改寫成工單式。
2. 照本章寫出 prompt-lint，然後**為每一條規則寫一個它應該抓到的 prompt 和一個它不應該抓到的 prompt**，確認六條規則都至少觸發過一次。你會不會再找到一個像 `hours` 那樣的漏洞？
3. 對同一個任務，分別用「工單式」和「工單式 + 你的猜測」各下一次 `/pstack:poteto-mode`（建議在一個不重要的練習專案），比較兩次的重述與第一個假設有什麼不同。

## 本章重點整理

- 一個好 prompt 有五要素：目標、可 pass/fail 的完成檢查、要看的證據、已知資訊、真正的限制
- 兩個不要：不寫怎麼做（包括不列技能順序）、一開始不講你對原因的猜測
- 短片語是強路由訊號：`repro first`、`don't change any code yet`、`zero behavior change`、`new task`
- 送出前可以用 `/pstack:in-your-own-words` 確認理解、用 `/pstack:sharpen` 讓 prompt 更深
- 任何把錯誤變成檢查的工具，都要先看它對該抓的案例真的觸發

## 自我檢核

- [ ] 不看本章，能說出五要素和兩個不要嗎？
- [ ] 「work on it for an hour」和「keep going until every fixture passes」差在哪？為什麼後者可以交給 /loop，前者不行？
- [ ] 你的同事說「我都直接告訴 agent 我覺得問題在哪，這樣比較快」，你會怎麼回應？
- [ ] `/sharpen` 的第 4 點「除了你和我之外，什麼能證明結果是對的」——為你目前手上的一個任務回答這個問題

## 延伸閱讀

### 官方指南

- **[docs/guide/02-poteto-mode.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/02-poteto-mode.md)**
  - **讀哪裡**：「What goes in a prompt」「Follow up short」「Switch tasks with "new task"」三節
  - **能學到什麼**：本章五要素與路由片語的原文
- **[docs/guide/10-recipes-and-pitfalls.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/10-recipes-and-pitfalls.md)**
  - **讀哪裡**：「The pitfalls」清單
  - **能學到什麼**：官方整理的 14 個常見錯誤；本章的 prompt-lint 只編碼了其中幾個，你可以挑更多來編碼

### 原始碼

- **[skills/sharpen/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/sharpen/SKILL.md)** 與 **[skills/in-your-own-words/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/in-your-own-words/SKILL.md)**
  - **讀哪裡**：整份（都只有幾行）
  - **能學到什麼**：一個只有一句話的技能也能有用；技能的價值在於「在對的時間被叫用」，不在於長度

### 文章

- **[Anthropic — Prompt engineering overview](https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/overview)**
  - **讀哪裡**：「Be clear and direct」與「Let Claude think」相關章節
  - **和本章的關聯**：通用的 prompt 原則；本章的五要素是把這些原則具體化成「工單」格式

### 對照閱讀

- **[spec_driven_development 的需求工程章節](../spec_driven_development/README.md)**（本 repo）
  - **和本章的關聯**：EARS 格式的需求句和本章的「完成檢查」是同一個追求：讓一個需求變成可以 pass/fail 的陳述

Prompt 寫好了，最常見的第一種任務是「我還不懂這段程式碼」。下一章從唯讀的 Investigation 開始。

→ [Ch 7 Investigation：/how、/why、/teach、/recall](./07-investigation-how-why.md)
