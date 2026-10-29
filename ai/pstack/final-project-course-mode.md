# Final Project — 替這個 repo 打造 course-mode

> **目標**：把整門課用一次。你要為這個 repo（Learning-with-Claude）寫一個自己的 mode 技能 `course-mode`：一個路由技能、三份 playbook、兩支把 `learn` 技能的結構規則編碼成檢查的腳本，以及一份盲測的 eval 設計。做完之後，你會有一個真的能用在這個 repo 上的工作流——而且在打造它的過程中，你會用它找到這個 repo 裡已經存在的問題。

> **環境**：Claude Code 2.1.295、Node.js 26、git。參考解答的技能、playbook、腳本與所有輸出都是在這個 repo 上實際跑出來的（2026-10-28）。eval 設計**沒有實際執行**。參考解答放在 `/tmp` 的練習目錄裡，**沒有安裝進這個 repo 的 `.claude/skills/`**——要不要安裝，是你的決定。

## 背景與動機

pstack 的作者說：「`poteto-mode` is my style. you may not want exactly that.」Ch 31 的結論是：pstack 最值得帶走的，是它關於「agent 會怎麼出錯」的模型，而不是它的 58 個技能。

這個 repo 已經有一個技能：`.claude/skills/learn/SKILL.md`。它定義了一套很完整的**教學風格**：直覺先行、多組範例、踩雷、延伸閱讀、「code 要真的跑過」、「嚴禁輕描淡寫詞」……。但它幾乎全部是**文字規則**。Ch 15 已經發現其中一條在全 repo 有 510 次違規。

`course-mode` 要做的，是把 pstack 的幾個核心設計套用到這個 repo：

| pstack 的設計 | 在 course-mode 裡變成 |
|---|---|
| 一個路由技能 + 多份 playbook（Ch 5） | 三份 playbook：開新課、寫一章、登錄一門課 |
| 條件 → 動作的 Non-negotiables（Ch 2） | 「任何 code → 先跑」「任何 agent 對話 → 標示意」…… |
| 只能引用讀過的原則（Ch 11） | 五條借用的 pstack 原則，引用要說出改變了哪個決定 |
| Encode Lessons in Structure（Ch 15） | `check-chapter.mjs` 與 `check-course.mjs` |
| 先看檢查失敗（Ch 3、14、21） | 每一條規則都要先在一個壞的案例上觸發 |
| Eval playbook（Ch 29） | 一份去識別化的 eval 設計 |

它**不取代** `learn` 技能。分工是：`learn` 擁有教學風格，`course-mode` 擁有證據與檢查。

## 任務規格

| 交付物 | 驗收標準 |
|---|---|
| `course-mode/SKILL.md` | 通過 Ch 28 的 `skill-lint.mjs`；有 Non-negotiables、Principles、Playbooks 三段；`disable-model-invocation: true`；description 說出它和 `learn` 的分工 |
| 三份 playbook | 每份都有編號步驟與 **Reply** 段；步驟裡指名要跑的腳本 |
| `scripts/check-chapter.mjs` | 檢查 `learn` 技能的結構規則（至少：標題、目標 blockquote、行數範圍、自我檢核、延伸閱讀、踩雷至少 3 條、輕描淡寫詞、相對連結、結尾的下一章連結）；在一個壞的範例上每一條規則都會觸發 |
| `scripts/check-course.mjs` | 檢查 README 的連結都存在、每個檔案都被 README 連到、每章的「下一章」符合 README 的順序、根目錄 README 有這門課的條目 |
| 在真實課程上的結果 | 對本課（`ai/pstack`）跑到 0 problems；對至少兩門其他課程跑，整理出問題的分類 |
| eval 設計 | 一個去識別化的 prompt（通過 Ch 29 的 `blind-check.mjs`）、一份只給評審的 rubric、兩個變體、從 transcript 判斷的方法 |

**規則**：腳本不准依賴任何 npm 套件（只用 Node 內建模組），讓任何 clone 這個 repo 的人都能直接跑。

## 期望輸出範例

對本課：

```
$ node scripts/check-chapter.mjs ai/pstack/[0-9]*.md ai/pstack/practice-*.md
36 files, 0 problems
$ node scripts/check-course.mjs ai/pstack .
37 files in README order, 0 problems
```

對一個故意寫壞的章節：

```
01-bad.md:1: title is not '# Ch N — ...', '# 練習 X — ...', or '# Final Project — ...'
01-bad.md:1: no '> **目標**：' blockquote near the top
...
1 files, 10 problems
```

## 如果你卡住了

1. **「檢查器什麼都沒輸出、exit 0」**：這是最危險的一種失敗——一個什麼都沒檢查的檢查器，回報「通過」。在 macOS 上 `/tmp` 是 `/private/tmp` 的 symlink，`import.meta.url` 用的是真實路徑；如果你用 `import.meta.url === \`file://${process.argv[1]}\`` 判斷「是不是被直接執行」，從 `/tmp` 執行時永遠不相等，主程式永遠不會跑。用 `pathToFileURL(fs.realpathSync(process.argv[1])).href` 比較。
2. **「其他課程報了幾十個踩雷集錦不足」**：先看那些檔案的踩雷是怎麼寫的。`learn` 技能只說「至少 3 條」，沒說要用編號清單。如果你的規則比 `learn` 技能更嚴，那是你的檢查器錯了，不是那門課錯了。
3. **「連結檢查在一個模板上報了一堆錯」**：範例與模板裡的連結（在 code block 裡、在 inline code 裡）不是真的連結。Ch 28 的 skill-lint 犯過一樣的錯。
4. **「練習檔都報缺延伸閱讀」**：讀 `learn` 技能的**練習模板**——它沒有延伸閱讀段。不同類型的檔案有不同的必要段落。
5. **「check-course 說根目錄 README 沒有這門課」**：那是 `learn` 技能 Step 6a 要你做的事。check-course 把那一步變成了一個會失敗的檢查。

## 實作步驟建議

### Step 1：讀 `learn` 技能，列出能機械檢查的規則

把 `.claude/skills/learn/SKILL.md` 的每一條規則分成三類：能機械檢查的（標題格式、段落存在、行數、禁用字）、需要執行才能檢查的（「code 要真的跑過」）、需要判斷的（「直覺先行」「範例要具體」）。只有第一類進 `check-chapter.mjs`。

### Step 2：寫 check-chapter.mjs，先寫一個壞範例

先寫一個每一條規則都違反的 `01-bad.md`，再寫檢查器，直到每一條規則都在它上面觸發。

### Step 3：在本課上跑，修本課

本課應該是最接近規則的課程（它是在 `learn` 技能目前的版本下寫的）。修到 0。注意哪些「問題」其實是檢查器的錯。

### Step 4：在其他課程上跑，分類結果

挑兩到三門其他課程。不要修它們——它們不在範圍內——但要把問題分類：哪些是真的違規、哪些是你的檢查器太嚴、哪些是 `learn` 技能本身演進造成的。

### Step 5：寫 check-course.mjs

把 `learn` 技能的 Step 6a（根目錄 README）與「README 與檔案要一致」編碼成檢查。

### Step 6：寫 SKILL.md 與三份 playbook

照 Ch 28 的寫作原則：條件 → 動作、只留改變決策的文字、用路徑引用腳本與 `learn` 技能、不重述。

### Step 7：設計 eval

照 Ch 29：一個去識別化的 prompt、兩個變體、一份 rubric、從 transcript 與產物判斷。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### 檔案結構

```
course-mode/
├── SKILL.md
├── playbooks/
│   ├── new-course.md
│   ├── write-chapter.md
│   └── register-course.md
└── scripts/
    ├── check-chapter.mjs
    └── check-course.mjs
```

### SKILL.md

```markdown
---
name: course-mode
description: Route work on this repo's tutorial courses (new course, write or repair a chapter, register a finished course) through playbooks that run every example, label every agent transcript as illustrative, and prove each file with scripts/check-chapter.mjs and scripts/check-course.mjs. Use for /course-mode or when asked to work on a course under <category>/<topic>/ in this style.
disable-model-invocation: true
---

# Course mode

Scripts live in `scripts/` next to this file. Run them from the repo root.

## Non-negotiables

- First message on a task → restate the reader the course is for, what they should be able to do after it, and the smallest change that gets there. Don't wait for confirmation.
- Any code, command, or tool output in a chapter → run it first in the environment the chapter pins, then paste the real output. If it cannot run here, write `本段未實測，為理論預期行為` and say where it would run.
- Any agent conversation in a chapter → label it `（示意）`. Never present it as a recorded run.
- Any version, flag, count, date, or quote → check it against the source before writing it. If you cannot check it, hedge it in the same sentence.
- A check you wrote → show it failing on a case it must catch before you trust its pass.
- After writing or editing a chapter → run `node <skill>/scripts/check-chapter.mjs <file>` and fix every line it prints.
- Before calling a course done → run `node <skill>/scripts/check-course.mjs <course-dir> .` and fix every line it prints.
- The same correction twice → add it to a script, not to this file.

## Principles

Read the pstack leaf for any principle you cite, and name the decision it changed.

- **Prove It Works.** Every pasted output came from a run.
- **Explain the Number.** Every number in a chapter has a source or a condition.
- **Encode Lessons in Structure.** A rule that fails twice becomes a check in `scripts/`.
- **Minimize Reader Load.** Build intuition before code, define terms on first use, link back to earlier chapters.
- **Never Block on the Human.** Ask once at the start of a new course (scope, depth, size). After that, decide and report.

## Playbooks

Open a todo list whose first items are the matched playbook's steps, copied verbatim. A step you skip stays with `skip: <reason>`.

- **New course.** A topic with no folder yet. File: `playbooks/new-course.md`.
- **Write a chapter.** One chapter, practice, or final project in an existing course. File: `playbooks/write-chapter.md`.
- **Register a course.** A course whose files are written. File: `playbooks/register-course.md`.

Distinct from the repo's `learn` skill, which owns the teaching style. This mode owns the evidence and the checks.
```

設計說明：

- **Non-negotiables 的每一條都來自本課的一次真實失敗**：「先跑再貼」來自 `learn` 技能的正確性關卡；「標示意」是本課從頭到尾的紀律；「先看檢查失敗」是本課七支工具都出過錯的教訓（Ch 31）；「同樣的修正兩次 → 寫進腳本」是 Encode Lessons in Structure 本身
- **只借了五條原則**，而且都改寫成這個 repo的情境（「Every pasted output came from a run」）。全部 24 條搬過來，大部分和寫教材無關
- **Never Block on the Human 被限制了範圍**：`learn` 技能的 Step 1 要求開新課前先問清楚。course-mode 尊重這一點（「Ask once at the start of a new course」），之後才不再停下來
- **最後一句劃清和 `learn` 的界線**——Ch 28 的「Distinct from」寫法

### 三份 playbook

```markdown
### New course

**You own the outline. Ask once, then decide.**

1. Ask the four questions the `learn` skill lists (scope, background, depth, hands-on) in one message, unless the user already answered them.
2. Research the topic from primary sources first. Pin versions and record where each pinned fact came from.
3. Draft the course map with parts, one line per chapter, and at least one practice per part. Size it by the `learn` skill's scale table.
4. Create `README.md` from the `learn` template. Leave chapter files to Write a chapter.
5. Write the course-scope memory file the `learn` skill's Step 6b names.

**Reply:** the course map, the pinned versions and their sources, and what you chose not to cover.
```

```markdown
### Write a chapter

**You own every claim in the file.**

1. Build and run the chapter's examples in a scratch directory before writing prose. Keep the scratch path.
2. Run each example's failure case too, and keep the real output.
3. Write the chapter from the `learn` template. Paste only outputs from step 1 and 2. Label agent dialogue `（示意）`.
4. Run `node <skill>/scripts/check-chapter.mjs <file>`. Fix every line.
5. Reread every number, date, and quote against its source. Fix or hedge.

**Reply:** what the chapter teaches, which outputs are real and where they ran, what stayed untested, and the check output.
```

```markdown
### Register a course

**You own the course's place in the repo.**

1. Run `node <skill>/scripts/check-course.mjs <course-dir> .`. Fix every line except the missing root entry.
2. Add the course to the root `README.md` under its category, with a blank line after each heading.
3. Rerun `check-course.mjs`. It must print `0 problems`.
4. Update the course-scope memory with the finish date.

**Reply:** the root README entry, the check output, and the memory line.
```

Write a chapter 的第 1 步把「跑範例」放在「寫文字」**之前**，是 `learn` 技能正確性關卡最後一句的直接編碼：「先跑 / 先查 → 再寫。不要先把整章寫完再回頭補驗證——那時你已經傾向相信自己寫的了。」

Register a course 第 2 步的「with a blank line after each heading」來自這個 repo 的 git 歷史：commit `0a9aafe` 就是在修根目錄 README 的標題後少了空行（MD022/MD032，Ch 30）。依 `/correct`，這一條應該最終變成 markdownlint 的設定；在那之前，它是 playbook 裡的一個具體步驟。

### check-chapter.mjs

```js
#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const BANNED = ["只需要", "很簡單", "顯然", "simply", "trivially", "obviously"];
const RANGE = { chapter: [300, 550], practice: [400, 600], final: [500, 700] };

export function checkChapter(file) {
  const text = fs.readFileSync(file, "utf8");
  const lines = text.split("\n");
  const base = path.basename(file);
  const kind = base.startsWith("practice-") ? "practice" : base.startsWith("final-project-") ? "final" : "chapter";
  const problems = [];
  const fail = (n, msg) => problems.push(`${base}:${n}: ${msg}`);

  if (!/^# (Ch \d+|練習 [A-Z]|Final Project) — /.test(lines[0])) fail(1, "title is not '# Ch N — ...', '# 練習 X — ...', or '# Final Project — ...'");
  if (!lines.slice(0, 6).some((l) => l.startsWith("> **目標**："))) fail(1, "no '> **目標**：' blockquote near the top");

  const [lo, hi] = RANGE[kind];
  if (lines.length < lo) fail(lines.length, `${lines.length} lines, under the ${kind} minimum of ${lo}`);
  if (lines.length > hi) fail(lines.length, `${lines.length} lines, over the ${kind} maximum of ${hi}`);

  const h2 = lines.map((l, i) => [l, i + 1]).filter(([l]) => l.startsWith("## "));
  const has = (re) => h2.find(([l]) => re.test(l));
  if (!has(/^## 自我檢核/)) fail(1, "no '## 自我檢核' section");
  if (kind !== "practice" && !has(/^## 延伸閱讀/)) fail(1, "no '## 延伸閱讀' section");
  if (kind === "chapter") {
    for (const [name, re] of [["踩雷集錦", /^## 踩雷集錦/], ["進階", /^## 進階/]]) if (!has(re)) fail(1, `no '## ${name}' section`);
    const trap = has(/^## 踩雷集錦/);
    if (trap) {
      const end = h2.find(([, n]) => n > trap[1])?.[1] ?? lines.length;
      const items = lines.slice(trap[1], end - 1).filter((l) => /^(\d+\. |- \*\*|\*\*)/.test(l)).length;
      if (items < 3) fail(trap[1], `踩雷集錦 has ${items} numbered items, needs at least 3 (numbered, bold bullet, or bold lead-in)`);
    }
  }
  if (kind === "practice") {
    if (!/<details>/.test(text)) fail(1, "reference solution is not folded in <details>");
    if (!has(/^## (測試用例|如果你卡住了)/)) fail(1, "no 測試用例 or 如果你卡住了 section");
  }
  const check = has(/^## 自我檢核/);
  if (check && !lines.slice(check[1]).some((l) => /^- \[ \] /.test(l))) fail(check[1], "自我檢核 has no '- [ ]' items");

  let fence = false;
  lines.forEach((l, i) => {
    if (l.trimStart().startsWith("```")) { fence = !fence; return; }
    if (fence || l.includes("allow-banned:")) return;
    for (const w of BANNED) if (l.includes(w)) fail(i + 1, `banned word "${w}"`);
    const prose = l.replace(/`[^`]*`/g, "");
    for (const [, href] of prose.matchAll(/\]\((\.{1,2}\/[^)\s#]+)(?:#[^)]*)?\)/g))
      if (!fs.existsSync(path.resolve(path.dirname(file), href))) fail(i + 1, `link ${href} does not resolve`);
  });
  if (fence) fail(lines.length, "unclosed code fence");

  const last = lines.filter((l) => l.trim()).at(-1) ?? "";
  if (kind !== "final" && !/^→ \[.+\]\(\.\/.+\.md\)$/.test(last)) fail(lines.length, "does not end with '→ [next](./file.md)'");
  return problems;
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  const files = process.argv.slice(2);
  let total = 0;
  for (const f of files) { const p = checkChapter(f); total += p.length; for (const x of p) console.log(x); }
  console.log(`${files.length} files, ${total} problems`);
  process.exit(total ? 1 : 0);
}
```

（行數範圍 300–550 / 400–600 / 500–700 直接取自 `learn` 技能的「長度」一節；它把「普通章 300–450」與「原理深挖章 400–550」合併成一個寬範圍，因為檢查器分不出一章是哪一種。`checkChapter` 被 export 出來，讓其他工具或測試可以直接呼叫。）

### check-course.mjs

```js
#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const dir = path.resolve(process.argv[2]);
const repoRoot = path.resolve(process.argv[3] ?? path.join(dir, "../.."));
const problems = [];
const readme = fs.readFileSync(path.join(dir, "README.md"), "utf8");
const order = [...readme.matchAll(/\]\(\.\/([^)#\s]+\.md)\)/g)].map((m) => m[1]).filter((f, i, a) => a.indexOf(f) === i);

for (const f of order) if (!fs.existsSync(path.join(dir, f))) problems.push(`README links ${f}, which does not exist`);
for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".md") && f !== "README.md"))
  if (!order.includes(f)) problems.push(`${f} is not linked from README`);

order.forEach((f, i) => {
  const p = path.join(dir, f);
  if (!fs.existsSync(p) || f.startsWith("final-project-")) return;
  const last = fs.readFileSync(p, "utf8").trim().split("\n").at(-1);
  const next = last.match(/^→ \[.*\]\(\.\/([^)]+)\)$/)?.[1];
  if (next !== order[i + 1]) problems.push(`${f} points next to ${next ?? "nothing"}, README order says ${order[i + 1] ?? "nothing"}`);
});

const rel = path.relative(repoRoot, dir).split(path.sep).join("/");
const root = fs.readFileSync(path.join(repoRoot, "README.md"), "utf8");
if (!root.includes(`](./${rel}/README.md)`)) problems.push(`root README.md has no entry linking ./${rel}/README.md`);

for (const p of problems) console.log(p);
console.log(`${order.length} files in README order, ${problems.length} problems`);
process.exit(problems.length ? 1 : 0);
```

### 先看每條規則觸發：一個故意寫壞的章節

```markdown
# 第一章 概論

這很簡單，看 [下一章](./02-missing.md)。

（一個 code block，裡面有「只需要」——它不該被抓）

## 自我檢核

沒有打勾項目

## 踩雷集錦

1. 只有一條
```

```
01-bad.md:1: title is not '# Ch N — ...', '# 練習 X — ...', or '# Final Project — ...'
01-bad.md:1: no '> **目標**：' blockquote near the top
01-bad.md:16: 16 lines, under the chapter minimum of 300
01-bad.md:1: no '## 延伸閱讀' section
01-bad.md:1: no '## 進階' section
01-bad.md:13: 踩雷集錦 has 1 numbered items, needs at least 3 (numbered, bold bullet, or bold lead-in)
01-bad.md:9: 自我檢核 has no '- [ ]' items
01-bad.md:3: banned word "很簡單"
01-bad.md:3: link ./02-missing.md does not resolve
01-bad.md:16: does not end with '→ [next](./file.md)'
1 files, 10 problems
exit=1
```

十條規則全部觸發；code block 裡的「只需要」沒有被抓——兩個方向都對了。 <!-- allow-banned: 說明那個字在 code block 裡沒被抓 -->

### 打造它的過程中，檢查器自己的四個錯

| 第幾版 | 錯誤 | 怎麼發現的 |
|---|---|---|
| 1 | 什麼都沒輸出、exit 0（`/tmp` 是 symlink，主程式判斷永遠失敗） | 對本課跑，**連最後的統計行都沒有**——一個本該印 `N files` 的工具沉默，就是它根本沒跑 |
| 2 | 要求練習檔有「延伸閱讀」 | 對本課跑，四個練習全部報錯；讀 `learn` 的練習模板，發現它本來就沒有這一段 |
| 2 | 沒有略過 inline code 裡的連結 | Ch 28 裡一個寫在反引號裡的 `[Ch N](./0N-xxx.md)` 被當成壞連結 |
| 3 | 踩雷只認編號清單 | 對 spec_driven_development 跑，44 個檔案報「0 條」；打開一個，它用的是粗體段落 |

第一個是最危險的：**它會讓每一次執行都「通過」**。如果你只看 exit code，你永遠不會發現。這是本課第八次「檢查工具本身有錯」，也是最隱蔽的一次。

### 用它修本課

修好檢查器的前兩個錯之後，本課有 8 個真的問題：

```
01-what-is-pstack.md:288: 288 lines, under the chapter minimum of 300
04-cursor-to-claude-code.md:43: link ../../docs/harness/claude-code.md does not resolve
11-principles-as-vocabulary.md:57: link ../principle-redesign-from-first-principles/SKILL.md does not resolve
27-multi-phase-autopilot.md:258: 258 lines, under the chapter minimum of 300
31-critique-tradeoffs.md:239: 239 lines, under the chapter minimum of 300
31-critique-tradeoffs.md:238: link ./final-project-course-mode.md does not resolve
practice-a-bug-fix-by-playbook.md:371: 371 lines, under the practice minimum of 400
practice-c-mini-arena.md:339: 339 lines, under the practice minimum of 400
36 files, 8 problems
```

兩個連結是**引用 pstack 原文時一起複製過來的相對路徑**——在 pstack 的 repo 裡它們是對的，在這個 repo 裡會 404。改成指向 GitHub 上的原檔。

五個行數不足的檔案，依 `learn` 技能的說法（「如果一章寫完只有 150 行，表示某個維度沒寫到，找出來補上」），每一個都補了一段**真正缺的內容**，而不是灌水：Ch 1 補了「讀這門課的三種方式」、Ch 27 補了「在 Claude Code 上跑這些 playbook」、Ch 31 補了「給不同角色的建議」「如果只能帶走五件事」「其他解法」「本課沒有涵蓋的部分」、練習 A 補了「三個看起來對的錯誤修法」、練習 C 補了「把 arena 寫進決策日誌」「一場 arena 怎麼失敗」「常見問答」。

最後一個連結（指向這份 Final Project）在這份檔案寫好之後自動解決。

### 在其他課程上跑：問題的分類

修正檢查器之後，對這個 repo 的另外三門課跑（不修它們，只分類）：

```
ai/harness_engineering: 50 files, 53 problems
ai/spec_driven_development: 51 files, 18 problems
compilers/compiler_frontend: 18 files, 78 problems
```

依問題類型分組（第 3 版檢查器，踩雷規則修正之前的 spec_driven 數字是 62）：

```
== ai/harness_engineering
  37 under the chapter minimum
  11 banned word
   3 under the practice minimum
   1 link does not resolve
   1 does not end with '→ [next](./file.md)'
== compilers/compiler_frontend
  18 no '> **目標**：' blockquote near the top
  15 no '## 進階' section
  15 no '## 踩雷集錦' section
  15 no '## 延伸閱讀' section
   9 under the chapter minimum
   3 under the practice minimum
```

三種性質不同的結果：

- **harness_engineering**：真的違規（行數不足、禁用字），可以用 `/correct` 的「只擋新增」策略（Ch 30）讓之後的修改不再惡化，舊的分批清
- **spec_driven_development**：62 → 18，差的 44 個是**檢查器的錯**（踩雷格式），不是課程的錯
- **compiler_frontend**：這是最有意思的。`learn` 技能明確說「以 `compilers/compiler_frontend/` 為基準」「當不確定寫法時，直接打開這幾個檔案抄格式」——但這門課的檔案用的是較早的格式（`> 目標：` 而不是 `> **目標**：`，沒有踩雷集錦、進階、延伸閱讀這幾段）。**技能的模板演進了，它指定的範例沒有跟著改。**

最後一點正好是 Ch 18 design red flags 的「Two ways to do one task」：「The design supports more than one way to do the same task. An agent copies whichever way it finds first, so every way keeps gaining callers.」也是 `/correct` 的假設：「an agent that sees only the files it opened, copies the nearest example」。一個照 `learn` 技能指示去「抄 compiler_frontend 的格式」的 agent，會寫出不符合 `learn` 技能自己模板的章節。

修法有兩個方向，選擇是這個 repo 的維護者的決定：把 `learn` 技能的範例指向一門符合目前模板的課；或把 compiler_frontend 補到目前的模板。在那之前，`check-chapter.mjs` 能讓這個不一致**被看見**。

### check-course 的結果

寫好這份檔案、但還沒登錄根目錄 README 之前：

```
README links final-project-course-mode.md, which does not exist
root README.md has no entry linking ./ai/pstack/README.md
37 files in README order, 2 problems
```

第二行就是 `learn` 技能 Step 6a 要你做的事。依 Register a course playbook 修完之後：

```
37 files in README order, 0 problems
```

另外三門課的 check-course 都是 `0 problems`——它們的 README、檔案、下一章連結都一致，也都登錄在根目錄 README 裡。

### eval 設計（沒有執行）

**變體**：A = 只有 `learn` 技能；B = `learn` + `course-mode`。

**候選看得到的東西**（去識別化）：

```
notes-course/          ← 一門假的、已經有三章的小課程，風格照 learn 技能
prompt.md
```

```
我想在 notes-course 裡加一章，講怎麼用 SQLite 的 WAL 模式讓讀寫可以同時進行。照這個 repo 的課程風格寫，範例要能在 macOS 上跑。
```

```
$ node blind-check.mjs eval-view/notes-course eval-view/prompt.md
OK nothing in view names the experiment
```

（這台機器有 `sqlite3` 3.51.0，所以題目的範例在候選的環境裡可以真的跑。）

**rubric**（只給評審）：

| # | 標準 | 怎麼量 |
|---|---|---|
| R1 | 章節裡貼的每一段輸出，重跑時都得到相同的結果 | 評審逐段重跑 code block |
| R2 | agent 對話（如果有）都標了示意 | 搜尋 |
| R3 | `check-chapter.mjs` 的問題數 | 跑腳本 |
| R4 | 可查證的事實（SQLite 版本行為、PRAGMA 名稱、預設值）有錯的數量 | 評審對照 SQLite 官方文件 |
| R5 | 教學品質（直覺先行、範例具體、踩雷有用） | 評審判斷，1–5 分 |

**chain-following 的判斷**（不問候選）：從 transcript 看候選有沒有**在寫文字之前**先跑 `sqlite3`（Write a chapter 的第 1 步）、有沒有跑 `check-chapter.mjs`（第 4 步）。從產物看：章節裡有沒有「本段未實測」的標示出現在它沒跑的地方。

**預測**（猜測，沒有驗證）：B 在 R1、R3 上應該明顯較好（它被要求先跑、要跑檢查）；R5 可能沒有差別（兩個變體都有 `learn` 技能的教學風格）；R4 是最不確定的——course-mode 要求查證，但查證的品質取決於模型。如果 eval 顯示 B 在 R5 上**變差**，代表 course-mode 的檢查讓 agent 把注意力從教學轉到了通過檢查——這正是 Ch 31 說的「痕跡變成表演」的失效模式，那會是不推廣這個變體的理由。

依 Ch 29，跑的時候用 `claude plugin eval` 要加 `--no-publish`；用 Agent 工具手動跑的話，兩個候選各自一個 worktree、各自一個去識別化的目錄名稱。

</details>

## 測試用例

| 檢查 | 預期 |
|---|---|
| `check-chapter.mjs` 對 `01-bad.md` | 10 個問題，涵蓋每一條規則 |
| `check-chapter.mjs` 對一個 code block 裡有禁用字的章節 | 不報那個禁用字 |
| `check-chapter.mjs` 從 `/tmp` 執行 | 印出最後的統計行（不是沉默） |
| `check-chapter.mjs` 對本課 | `36 files, 0 problems` |
| `check-chapter.mjs` 對一個用粗體段落寫踩雷的章節 | 不報踩雷不足 |
| `check-course.mjs` 對本課，登錄根 README 之前 | 報 `root README.md has no entry` |
| `check-course.mjs` 對本課，登錄之後 | `0 problems` |
| `skill-lint.mjs` 對 `course-mode/` | `1 skills, 0 problems` |
| `blind-check.mjs` 對 eval 的候選視角 | `OK nothing in view names the experiment` |

## 延伸挑戰（加分）

- **真的安裝它**：把 `course-mode/` 複製到 `.claude/skills/course-mode/`，用它寫一章新的課（任何主題），看它的 todo、它有沒有先跑範例、它最後的回覆有沒有附上 check-chapter 的輸出。依 Ch 28 的提醒：這是一個會影響這個 repo 之後每一個 session 的改動，先用 eval 證明它有用。
- **把 check-chapter 接成 PostToolUse hook**：每次 `Write` 或 `Edit` 一個 `<category>/<topic>/*.md` 檔，自動跑 check-chapter，把結果加回 context（Ch 30 的進階）。這讓 course-mode 的檢查不再依賴 agent 記得跑。
- **只擋新增**：把 check-chapter 改成只對「這次改動碰到的檔案」報錯，讓它能放進這個 repo 的 CI 而不會被其他課程的舊問題擋住（Ch 30 的 lint-added）。
- **跑那個 eval**：照參考解答的設計，各跑三次。結果和預測一致嗎？
- **解決 compiler_frontend 的不一致**：寫一份給這個 repo 維護者的建議（Ch 24 的 PR 描述格式：Why / What changed / Scope / Tradeoffs），在兩個修法方向之間做出推薦。

## 自我檢核

- [ ] 你的 `course-mode/SKILL.md` 通過 skill-lint，而且每一條 Non-negotiable 你都說得出它擋的是哪一次真實的失敗
- [ ] 你的 check-chapter 在一個壞範例上，每一條規則都觸發過；在一個 code block 的禁用字上不觸發
- [ ] 你知道你的檢查器在第一次寫出來時有什麼盲點，以及你是怎麼發現的
- [ ] 你在其他課程上的結果，能分出「真的違規」「檢查器的錯」「技能本身的演進」三類
- [ ] 你的 eval 設計通過 blind-check，而且你寫下了「什麼結果會讓你不推廣這個變體」
- [ ] 你能說出 course-mode 從 pstack 借了什麼、刻意沒借什麼，以及理由

## 延伸閱讀

### 本課的回顧路線

- **[Ch 2 — 解剖 poteto-mode](./02-anatomy-of-poteto-mode.md)**：你的 SKILL.md 的結構就是從這裡來的；對照讀一次，看你保留了哪幾段
- **[Ch 15 — Delegation、Meta 與 Autonomy](./15-delegation-meta-autonomy.md)** 與 **[Ch 30 — 自我改進迴路](./30-self-improvement-loop.md)**：check-chapter 的禁用字規則從這兩章長出來；「只擋新增」的延伸挑戰在 Ch 30
- **[Ch 28 — skill 寫作學](./28-skill-authoring.md)**：你的 SKILL.md 與 playbook 要符合的寫作原則
- **[Ch 29 — Eval](./29-eval-playbook.md)**：你的 eval 設計的依據

### 原始碼

- **[poteto-mode/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md)**：你的 mode 的原型
- **[skills/automate-me/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/automate-me/SKILL.md)**：pstack 自己產生「你的 mode」的方式——從你的對話紀錄挖出來，而不是像本專案這樣從一個既有的技能與 repo 歷史出發。比較兩種做法：哪一種更適合一個已經有明確風格規範的 repo？
- **[本 repo 的 `.claude/skills/learn/SKILL.md`](../../.claude/skills/learn/SKILL.md)**：course-mode 要和它分工的那個技能

### 文章

- **[Anthropic — Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)**
  - **讀哪裡**：關於技能裡附帶腳本的段落
  - **和本專案的關聯**：course-mode 的兩支腳本，就是「技能附帶的、讓 agent 不必每次重新推理的確定性程式碼」

這門課到這裡結束。你讀了一個人怎麼把自己的工程判斷寫成 agent 會照做、而且做完會被檢查的形式；你也看到那些檢查本身會出錯，以及怎麼抓到它們。最後留下的問題是你自己的：在你的 repo 裡，哪一條你一直在對 agent 重複說的話，應該是下一支腳本？
