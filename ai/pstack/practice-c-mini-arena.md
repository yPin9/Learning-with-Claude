# 練習 C — 在 Claude Code 手刻一場 mini arena

> **目標**：把 Ch 16–20 拼起來，在你自己的 Claude Code session 裡**真的**跑一場 arena：寫 brief 與 rubric、用 worktree 隔離兩個 runner、用 Agent 工具 spawn 兩個不同模型的 subagent、用一份 runner 看不到的隱藏測試打分、挑底、嫁接、驗證、寫合成說明，最後用一支檢查器確認整場 arena 的產物符合 arena 的規則。做完之後，你會清楚知道 pstack 的 arena 在你的環境裡哪些部分有效、哪些部分（例如跨家族評審）需要你自己補。

> **環境**：Claude Code 2.1.295 以上、git、Node.js 20 以上。可選：Codex plugin（當跨家族評審）。本練習的框架腳本（setup-arena.sh、hidden-tests.mjs、check-arena.mjs）與參考解答的兩個候選、合成版本都在本機實際跑過；**參考候選是本課作者手寫的**，你的 subagent 會產出不同的程式碼，這正是重點。

## 背景與動機

Ch 18 的迷你 arena 是「手寫兩個候選來示範流程」。這個練習要你讓**真的 subagent** 產出候選，因為 arena 的價值有一大半來自你沒料到的設計——你自己寫兩個候選，你只會寫出你想得到的兩種。

這個練習也會讓你親身遇到 Ch 17 說的「多樣性塌縮」：在 Claude Code 預設環境下，你的兩個 runner 都是 Claude，評審會被跳過。你要決定怎麼面對它。

## 題目

一個 CLI 與設定檔都會用到的小函式：

```markdown
# Task

Write `parseDuration(text)` in `duration.mjs`. It turns a human duration such as `1h30m`, `45s`, or `2d 4h`
into milliseconds. Callers are CLI flags and config files. Invalid input must fail loudly with a message that
names what was wrong. Export only what callers need.

Write the code to `duration.mjs` in your working directory, and a short `RATIONALE.md`: the shape you chose,
two alternatives you considered, and why you rejected them.
```

這個題目適合 arena，因為它有多種合理的形狀（一個正規表示式、一個手寫的掃描器、一個 tokenizer + parser、用 `Intl` 之類的內建……），各有不同的取捨；而且它小到一個 subagent 幾分鐘就能做完。

## 任務規格

| 交付物 | 驗收標準 |
|---|---|
| `judge/`（放在 repo **外面**） | 含 `brief.md`、`rubric.md`、`hidden-tests.mjs`；runner 的工作目錄裡看不到後兩者 |
| 兩個候選 | 各自在自己的 worktree（`/tmp/arena-durations/candidate-<n>/`），由兩個 subagent 產出，各有 `duration.mjs` 與 `RATIONALE.md` |
| 打分表 | 每個候選在 R1–R4 上的分數，來自 `hidden-tests.mjs` 的實際輸出，加上你自己讀完程式碼後的判斷 |
| `final/duration.mjs` | 以一個候選為底，從另一個嫁接最多兩個點子；通過隱藏測試，並通過一個證明嫁接有效的額外檢查 |
| `final/SYNTHESIS.md` | 有 `## Base`、`## Judge`、`## Grafts`、`## Rejected`、`## Verification` 五段 |
| 檢查 | `node check-arena.mjs /tmp/arena-durations final/SYNTHESIS.md` 印出 `OK` |

**規則**：

- 兩個 runner 拿到**逐字相同**的 brief（「the prompt is the contract」）
- runner 看不到 rubric、看不到隱藏測試、不知道對方存在
- 兩個 runner 用**不同的模型**（例如 `opus` 與 `sonnet`）
- 嫁接用手寫，不准整段複製另一個候選的檔案

## 框架腳本

### rubric（只給你自己）

```
R1 Correct on the spec table (hidden-tests.mjs, cases tagged R1)
R2 Errors name the bad input and where it went wrong (cases tagged R2)
R3 No pathological slowdown: a 20,000-character input returns or throws in under 50 ms (R3)
R4 Small surface: one exported function, plus at most one exported error type
```

（R3 的 20,000 字元與 50ms 是本練習訂的門檻：長到能讓有災難性回溯的正規表示式明顯變慢，又短到正常實作在毫秒內完成。）

### hidden-tests.mjs（只給你自己）

```js
import { performance } from "node:perf_hooks";
const mod = await import(process.argv[2]);
const { parseDuration } = mod;
const ok = [["1h30m", 5_400_000], ["45s", 45_000], ["2d 4h", 187_200_000], ["250ms", 250], ["1m1s", 61_000], ["0s", 0]];
const bad = ["", "abc", "1x", "1h30", "h", "-5m", "1.5.5h"];
const score = { R1: [0, 0], R2: [0, 0], R3: [0, 1], R4: [0, 1] };
for (const [input, want] of ok) { score.R1[1]++; try { if (parseDuration(input) === want) score.R1[0]++; } catch {} }
for (const input of bad) {
  score.R1[1]++; score.R2[1]++;
  try { parseDuration(input); } catch (e) {
    score.R1[0]++;
    if (input === "" ? /empty/i.test(e.message) : e.message.includes(JSON.stringify(input)) || e.message.includes(input)) score.R2[0]++;
  }
}
const long = "1h".repeat(10_000) + "x";
const t0 = performance.now(); try { parseDuration(long); } catch {} const ms = performance.now() - t0;
if (ms < 50) score.R3[0] = 1;
const exportsCount = Object.keys(mod).length;
if (exportsCount <= 2 && typeof parseDuration === "function") score.R4[0] = 1;
console.log(JSON.stringify({ file: process.argv[2].split("/").slice(-2).join("/"), ...Object.fromEntries(Object.entries(score).map(([k, [a, b]]) => [k, `${a}/${b}`])), long_ms: +ms.toFixed(2), exports: exportsCount }));
```

注意 R2 的檢查很粗糙（只看錯誤訊息有沒有包含輸入字串、空字串時有沒有說 empty）。它量不到「有沒有說出**哪裡**錯」——那一部分要你讀程式碼判斷。這是 arena 要求「Read every candidate end to end」而不只看分數的原因。

### setup-arena.sh

```sh
#!/bin/sh
# setup-arena.sh <repo> <n> <slug>：為 n 個 runner 各建一個 worktree
set -eu
repo="$1"; n="$2"; slug="$3"
for i in $(seq 1 "$n"); do
  git -C "$repo" worktree add -q -b "arena/$slug-$i" "/tmp/arena-$slug/candidate-$i" HEAD
  echo "candidate-$i -> /tmp/arena-$slug/candidate-$i"
done
```

### check-arena.mjs

```js
// check-arena.mjs <arena-dir> <synthesis-note>：檢查 arena 的產物是否符合規則
import fs from "node:fs";
import path from "node:path";
const [arenaDir, notePath] = process.argv.slice(2);
const problems = [];
const candidates = fs.readdirSync(arenaDir).filter((d) => d.startsWith("candidate-"));
if (candidates.length < 2) problems.push(`only ${candidates.length} candidate(s); arena needs at least 2`);
for (const c of candidates) {
  const dir = path.join(arenaDir, c);
  for (const f of ["duration.mjs", "RATIONALE.md"]) if (!fs.existsSync(path.join(dir, f))) problems.push(`${c}: missing ${f}`);
  for (const leak of ["rubric.md", "hidden-tests.mjs"]) if (fs.existsSync(path.join(dir, leak))) problems.push(`${c}: can see ${leak}`);
}
const note = fs.existsSync(notePath) ? fs.readFileSync(notePath, "utf8") : "";
for (const h of ["Base", "Judge", "Grafts", "Rejected", "Verification"])
  if (!new RegExp(`^##\\s+${h}`, "m").test(note)) problems.push(`synthesis note: no "## ${h}" section`);
console.log(problems.length ? problems.map((p) => `FAIL ${p}`).join("\n") : `OK ${candidates.length} candidates, note complete`);
process.exit(problems.length ? 1 : 0);
```

## 期望輸出範例

候選還沒寫理由、合成說明還不存在時：

```
FAIL candidate-1: missing RATIONALE.md
FAIL candidate-2: missing RATIONALE.md
FAIL synthesis note: no "## Base" section
FAIL synthesis note: no "## Judge" section
FAIL synthesis note: no "## Grafts" section
FAIL synthesis note: no "## Rejected" section
FAIL synthesis note: no "## Verification" section
exit=1
```

完成時：

```
OK 2 candidates, note complete
exit=0
```

## 如果你卡住了

1. **「我把 rubric 放進 repo 了，worktree 裡看得到」**：worktree 共享 repo 的內容。任何 commit 進 repo 的檔案，每個 runner 都看得到。本課作者在準備這個練習時就犯了這個錯，後來把 `judge/` 移到 repo 外面。
2. **「怎麼讓 subagent 用不同的模型？」**：在 Claude Code 裡直接說：「spawn two general-purpose subagents in parallel. The first uses model opus, the second uses model sonnet. Give each the brief below verbatim, plus its own working directory. Do not tell either one about the other.」Agent 工具的 `model` 參數接受 `opus`、`sonnet`、`haiku`、`fable`（以你的版本為準）。
3. **「兩個候選幾乎一樣」**：arena 原文：「If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape.」這是合法的結果，在合成說明裡寫出來。但先確認：brief 是不是寫得太具體，把形狀限制死了？
4. **「兩個候選差很多，我想各取一半」**：不要。原文：「If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.」挑一個當底，最多嫁接一兩個點子。
5. **「沒有其他家族的評審」**：Claude Code 預設環境下就是這樣（Ch 17）。合成說明的 `## Judge` 寫「Skipped」與原因。如果你裝了 Codex plugin，可以在挑完之後把兩個候選和 rubric 交給 `/codex:adversarial-review` 或 `codex:codex-rescue` 當第二意見，並記錄它是否同意你的選擇。

## 實作步驟建議

### Step 1：Frame

建一個空的 repo（只有 README），在 repo 外建 `judge/` 放 brief、rubric、隱藏測試。

### Step 2：隔離

`./setup-arena.sh <repo> 2 durations`，確認兩個 worktree 裡都**沒有** rubric 與隱藏測試。

### Step 3：Fan out

在 Claude Code 裡用一則訊息 spawn 兩個 subagent（不同模型、同一份 brief、各自的工作目錄）。等兩個都完成。

### Step 4：打分與閱讀

對每個候選跑 `node judge/hidden-tests.mjs <path>/duration.mjs`。然後**完整讀完**兩份程式碼與 RATIONALE.md，補上隱藏測試量不到的判斷（錯誤訊息有沒有說出位置？空白怎麼處理？介面好不好用？）。

### Step 5：Pick 與 Graft

選一個當底，寫下理由。從另一個找出最多兩個值得嫁接的點子，用底案的寫法手動加進去。每一個嫁接都要有一個**能證明它有效**的檢查（一個在嫁接前失敗、嫁接後通過的輸入）。

### Step 6：Verify 與合成說明

對 `final/duration.mjs` 跑隱藏測試與你的嫁接檢查，寫 `SYNTHESIS.md`，跑 `check-arena.mjs`。最後清掉 worktree：

```bash
git -C <repo> worktree remove --force /tmp/arena-durations/candidate-1
git -C <repo> worktree remove --force /tmp/arena-durations/candidate-2
git -C <repo> branch -D arena/durations-1 arena/durations-2
```

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。你的 subagent 會產出不同的候選，所以下面的參考解答是「一場 arena 跑完之後應該長什麼樣」的示範，不是標準答案。

<details>
<summary>點開參考實作</summary>

### 兩個參考候選（本課作者手寫，模擬兩種常見的形狀）

**candidate-1**：一個正規表示式把關，再用 `matchAll` 加總。

```js
const UNIT = { ms: 1, s: 1000, m: 60_000, h: 3_600_000, d: 86_400_000 };
export function parseDuration(text) {
  const compact = text.replace(/\s+/g, "");
  if (!/^(\d+(ms|s|m|h|d))+$/.test(compact)) throw new Error(`invalid duration: ${JSON.stringify(text)}`);
  let total = 0;
  for (const [, n, u] of compact.matchAll(/(\d+)(ms|s|m|h|d)/g)) total += Number(n) * UNIT[u];
  return total;
}
```

**candidate-2**：手寫的掃描器，一個專屬的錯誤型別，錯誤訊息帶位置。

```js
const UNIT = new Map([["ms", 1], ["s", 1000], ["m", 60_000], ["h", 3_600_000], ["d", 86_400_000]]);
export class DurationError extends Error {}
export function parseDuration(text) {
  if (text.trim() === "") throw new DurationError("empty duration");
  let total = 0, i = 0;
  while (i < text.length) {
    if (text[i] === " ") { i++; continue; }
    const start = i;
    while (i < text.length && text[i] >= "0" && text[i] <= "9") i++;
    if (i === start) throw new DurationError(`expected a number at position ${start} in "${text}"`);
    const n = Number(text.slice(start, i));
    const unit = text.startsWith("ms", i) ? "ms" : text[i];
    if (!UNIT.has(unit)) throw new DurationError(`unknown unit ${JSON.stringify(text.slice(i, i + 1) || "end of input")} at position ${i} in "${text}"`);
    total += n * UNIT.get(unit);
    i += unit.length;
  }
  return total;
}
```

### 打分

```
{"file":"candidate-1/duration.mjs","R1":"13/13","R2":"6/7","R3":"1/1","R4":"1/1","long_ms":0.13,"exports":1}
{"file":"candidate-2/duration.mjs","R1":"13/13","R2":"7/7","R3":"1/1","R4":"1/1","long_ms":2.06,"exports":2}
```

隱藏測試的分數很接近。candidate-1 在 R2 少一分：空字串時它說 `invalid duration: ""`，沒有說 empty。

**讀完程式碼之後的判斷**（隱藏測試量不到的部分）：

| 面向 | candidate-1 | candidate-2 |
|---|---|---|
| 錯誤訊息說出**哪裡**錯 | 否：所有錯誤都是同一句 `invalid duration` | 是：帶位置與那個字元 |
| 空白 | 任何空白（`\s`，含 tab 與換行） | 只有空格 |
| 程式碼長度 | 7 行 | 18 行 |
| 長輸入 | 0.13ms（V8 的正規表示式引擎沒有在這個模式上災難性回溯） | 2.06ms |

空白那一列需要證明。用一個 tab 試：

```
candidate-1 tab: 5400000
candidate-2 tab: throws expected a number at position 2 in "1h	30m"
```

candidate-2 在設定檔裡遇到 tab 就會失敗——這是一個真實的缺陷，而且隱藏測試沒有涵蓋它。

### Pick 與 Graft

**底案：candidate-2**。理由：brief 說「Invalid input must fail loudly with a message that names what was wrong」，candidate-2 的錯誤訊息說出位置與字元，candidate-1 只說「invalid」。CLI 使用者看到 `unknown unit "x" at position 1 in "1x"` 可以自己修；看到 `invalid duration: "1x"` 要自己猜。

**嫁接：從 candidate-1 取「任何空白都是分隔」**，改寫成底案的掃描器寫法（一行）：

```diff
-    if (text[i] === " ") { i++; continue; }
+    if (/\s/.test(text[i])) { i++; continue; }
```

嫁接的驗證（嫁接前失敗、嫁接後通過）：

```
final tab: 5400000
```

隱藏測試仍全部通過：

```
{"file":"final/duration.mjs","R1":"13/13","R2":"7/7","R3":"1/1","R4":"1/1","long_ms":0.78,"exports":2}
```

### 合成說明

```markdown
## Base
candidate-2 (scanner + DurationError). Wins R2 7/7 vs 6/7: names position and input.
## Judge
Skipped: parent and both runners are Claude; no other family in the cross-judge pool.
## Grafts
From candidate-1: treat any whitespace as a separator (candidate-2 threw on a tab).
## Rejected
candidate-1's single regex gate: fine on R3 here, but its one message cannot say where input went wrong.
## Verification
hidden-tests: R1 13/13, R2 7/7, R3 1/1 (0.78 ms on 20k chars), R4 1/1; "1h\t30m" -> 5400000.
```

### 檢查

```
OK 2 candidates, note complete
exit=0
```

**解答說明**：

- 分數接近時，決定性的判斷來自**讀程式碼**（錯誤訊息的品質），而不是分數。隱藏測試是挑選者的工具，不是挑選者本身。
- 嫁接的點子（空白處理）是在讀程式碼時發現的，隱藏測試沒有涵蓋它。這說明了為什麼 arena 要求「Walk each losing candidate once more for what is worth porting」——落選的候選常常在你沒想到的地方是對的。
- 「Judge: Skipped」誠實地記錄了這場 arena 少了什麼。三個月後有人讀這份合成說明，會知道這個選擇只有同家族的判斷。

</details>

## 測試用例

| 檢查 | 預期 | 說明 |
|---|---|---|
| 兩個 worktree 裡找不到 `rubric.md` 與 `hidden-tests.mjs` | 找不到 | runner 看不到評分標準 |
| 兩個 runner 的 brief | 逐字相同 | prompt 是合約 |
| 每個候選有 `RATIONALE.md`，列出兩個被拒絕的替代方案 | 有 | arena Phase B 的要求 |
| `final/duration.mjs` 跑隱藏測試 | 不低於底案的分數 | 嫁接不能讓底案變差 |
| 至少一個「嫁接前失敗、嫁接後通過」的輸入 | 有 | 證明嫁接有效 |
| `check-arena.mjs` | `OK` | 產物完整 |
| 在一個候選的 worktree 裡放一份 `rubric.md` | `check-arena.mjs` 報 `can see rubric.md` | 確認洩題檢查會觸發 |
| 刪掉一個候選的 `RATIONALE.md` | `check-arena.mjs` 報 `missing RATIONALE.md` | 確認候選產物檢查會觸發 |
| 合成說明少了 `## Judge` | `check-arena.mjs` 報 `no "## Judge" section` | 確認合成說明檢查會觸發 |
| `final/duration.mjs` 拿掉嫁接的那一行 | tab 的輸入再次拋出錯誤 | 確認你的嫁接檢查真的在檢查嫁接 |

後四列是本課反覆出現的原則：**每一個檢查都要先看它在該抓的案例上失敗一次**。

## 把這場 arena 寫進決策日誌

一場 arena 的合成說明是它的**最終**產物；但做決定的過程裡有好幾個岔路——要不要重新框定、哪個候選當底、哪個點子值得嫁接——這些應該在發生的當下記下來（Ch 26）。用 `log.sh`：

```bash
LOGSH=$PSTACK/skills/show-me-your-work/scripts/log.sh
bash $LOGSH arena.tsv frame "two runners on opus and sonnet, same brief, rubric R1-R4 kept outside the repo" \
  "candidates must not see the rubric" "/path/to/judge/rubric.md" "worktrees clean of rubric"
bash $LOGSH arena.tsv pick "candidate-2 as base" "R2 7/7 vs 6/7; its errors name the position" \
  "/tmp/arena-durations/candidate-2/duration.mjs" "base chosen"
bash $LOGSH arena.tsv graft "any whitespace is a separator, from candidate-1" \
  "candidate-2 threw on a tab; candidate-1 did not" "final/duration.mjs" "1h\\t30m -> 5400000"
bash $LOGSH arena.tsv judge "skipped the cross-judge" "no family other than Claude in the pool" "final/SYNTHESIS.md" "INCONCLUSIVE on judge agreement"
```

最後一列的 result 是 `INCONCLUSIVE on judge agreement`：這場 arena 沒有辦法回答「另一個家族會不會同意這個選擇」，日誌應該誠實地說出這一點，而不是讓「Judge: Skipped」只存在於合成說明裡。

（log.sh 會把 decision 裡的 tab 換成空格；`\\t` 在這裡是兩個字元的字面文字，表示輸入裡的 tab，不是真的 tab。）

## 一場 arena 怎麼失敗

做完這個練習，回頭檢查你的 arena 有沒有落入這幾種常見的失敗。每一種都會讓 arena 的成本照付、價值消失：

| 失敗 | 徵兆 | 原文的規則 |
|---|---|---|
| **候選看到了 rubric** | 候選的 RATIONALE.md 用的詞和 rubric 一模一樣 | 「Candidates only see the task.」 |
| **brief 把形狀限制死了** | 兩個候選幾乎一樣，而且你在 brief 裡寫了「用正規表示式」之類的做法 | Phase A：prompt 是合約，但合約應該描述**結果**，不是做法 |
| **只看分數就挑** | 你沒有讀完兩份程式碼；你的挑選理由全部來自隱藏測試 | 「Read every candidate end to end before picking.」 |
| **嫁接太多** | final 和底案的 diff 很大，而且混著兩種風格 | 「usually one or two things per candidate, not most of it」 |
| **嫁接是複製貼上** | final 裡有一段和落選候選逐字相同的程式碼 | 「Fold each graft in by hand ... Don't paste mechanically.」 |
| **差異很大卻取中間** | 你的 final 同時有兩個候選各自的核心結構 | 「If they wildly diverge, Phase A was under-specified. Reframe and re-run.」 |
| **評審被跳過卻沒說** | 合成說明沒有 Judge 段，或寫「judge agreed」但根本沒有評審 | 「skip the judge, pick the base alone in Phase D, and say so」 |

最後一種在 Claude Code 上最容易發生：預設環境下評審一定會被跳過（Ch 17），而一個沒有說明這件事的合成說明，會讓讀者以為這個選擇經過了跨家族的檢驗。`check-arena.mjs` 要求 `## Judge` 段存在，但它檢查不了你在那一段寫的是不是實話——那是你的責任。

## 常見問答

**Q：我只有一個模型可以用，這個練習還有意義嗎？**
有。arena 原文允許「Use the same model N times when the work is generation-bound rather than judgment-sensitive」。同一個模型跑兩次，仍然會產出不同的設計（抽樣的隨機性），你仍然能練習挑底與嫁接。你失去的是**判斷層的多樣性**：兩個 runner 的盲點會比較像。在合成說明裡寫清楚這一點。

**Q：subagent 沒有把檔案寫在我指定的 worktree 裡怎麼辦？**
brief 裡給**絕對路徑**，而且在「Done when」寫明要檢查的檔案（例如「`/tmp/arena-durations/candidate-1/duration.mjs` exists and `node -e "import('...')"` succeeds」）。事後用 `ls` 與 `git -C <worktree> status` 檢查，而不是相信它說「寫好了」（Ch 16）。

**Q：這場 arena 大概要花多少？**
本練習沒有量測。成本取決於你選的模型、subagent 讀了多少 context、它們試了幾次。用 Claude Code 的用量資訊在跑之前與之後各看一次；如果你要做延伸挑戰的三個 runner，先跑兩個的版本量一次成本。這是 Ch 22 的 Explain the Number 用在你自己的工作上。

**Q：隱藏測試的 R2 太粗糙，要不要改進它再跑？**
可以，但**在看到候選之前**改。看了候選之後才改 rubric 或測試，你會不自覺地把它改成偏向你喜歡的那一個——這和 swarm 要求「race 的規則要在 spawn 之前宣告」是同一個道理（Ch 19）。

## 延伸挑戰（加分）

- **加一個跨家族的評審**：把兩個候選的路徑與 rubric 交給 Codex（`codex:codex-rescue` subagent 或 `/codex:adversarial-review`），請它逐條打分並推薦底案，**在你自己挑之前**。它和你的選擇一致嗎？不一致時，依 arena Phase D：「Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.」
- **三個 runner**：加一個 `haiku` runner。便宜的模型在這個題目上表現如何？它有沒有提出另外兩個沒想到的形狀？
- **改 brief 看收斂**：把 brief 改得更具體（例如「use a regular expression」），再跑一次。候選是不是收斂了？這示範了 brief 怎麼限制設計空間——arena 的價值取決於 brief 留了多少空間。
- **把隱藏測試變成 property test**：用隨機產生的合法時間字串（隨機的單位組合、隨機的數字、隨機的空白）與隨機的非法字串跑 1,000 次，斷言合法的一定解析成正確的毫秒數、非法的一定拋出錯誤。你的 final 在這個測試下還站得住嗎？兩個候選呢？
- **讓 runner 互相 interrogate**：把 candidate-1 交給 sonnet 去弄壞、candidate-2 交給 opus 去弄壞，用 Ch 20 的規則（只留跑得出來、違反意圖的發現）。被證明的發現，有沒有改變你的挑底決定？
- **重新框定再跑一次**：如果你的兩個候選差異很大，依 arena 的規則，問題可能在 brief。改寫 brief（例如明確說「錯誤訊息要包含位置」），再跑一次。比較兩次的候選：brief 的改動讓設計空間變小了多少？
- **用 `/pstack:arena` 跑同一題**：在新的 repo 裡跑 `/pstack:arena write parseDuration per this brief: <brief>`，比較它的合成說明和你手刻的。它有沒有在 Judge 那一段說明評審被跳過？

## 自我檢核

- [ ] 你的兩個 runner 看不到 rubric，你能用 `ls` 證明
- [ ] 你挑底的理由裡，至少有一條是讀程式碼才得到的、隱藏測試量不到的判斷
- [ ] 你的每一個嫁接都有一個嫁接前失敗、嫁接後通過的輸入
- [ ] 你的合成說明誠實地記錄了評審有沒有跑、為什麼
- [ ] 你的決策日誌記下了 frame、pick、graft、judge 四個決定，每一列的 evidence 都是指標
- [ ] 你能說出這場 arena 落入了「一場 arena 怎麼失敗」表格裡的哪一種（如果有），以及你下次會怎麼避免
- [ ] 你在看到候選之前就定好了 rubric 與隱藏測試，之後沒有改過
- [ ] 能說出自己的 arena 和參考解答的差異，並解釋各自取捨

Part 3 結束。接下來的 Part 4 處理「做完之後」的事：怎麼讓 agent 自己驗證、怎麼清理、怎麼交付。

→ [Ch 21 驗證 skill：讓 agent 自己檢查自己](./21-verification-skills.md)
