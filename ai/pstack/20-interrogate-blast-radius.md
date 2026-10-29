# Ch 20 — interrogate 與 blast-radius：只留跑得出來的發現

> **目標**：讀懂 pstack 的兩個「拆」的技能。`interrogate` 讓不同模型的對手去弄壞一個改動，**只有重跑指令能證明失敗、而且那個失敗違反了意圖的發現才留下**；`blast-radius` 找出一個小改動會在 diff 以外的哪裡造成破壞，並且**用執行程式碼證明它之所以安全的那一個事實**。你會對 Ch 18 合成的限流器跑一次手動 interrogate（五個發現留下一個），看到「意圖怎麼寫」如何決定一個真實的問題算不算發現，再對修正它的改動做一次 blast-radius。

> **環境**：pstack 0.15.36、Node.js 26。對手的發現清單由本課作者撰寫（模擬兩個不同模型的對手），每個發現的指令與輸出都是實際跑出來的；記憶體數字每次執行會略有不同（本章兩次分別是 23 MB 與 29 MB）。

## 為什麼需要這個？

code review 有兩個老問題，agent 時代都被放大了：

1. **雜訊**：review 評論裡混著真正的 bug、風格偏好、「也許會有問題」的猜測。作者要逐條判斷、逐條回覆，真正的 bug 被淹沒在裡面。agent reviewer 能在一分鐘內產出五十條評論，雜訊問題變得更嚴重。
2. **說服力**：「這裡可能有 race condition」這種評論，作者可以回「不會，因為……」，然後雙方各說各話。沒有人跑過任何東西。

pstack 的解法很激進：**沒有執行證明的發現不算發現**。官方指南：「Adversarial review of a plan with no code behind it invents risks that will never happen.」

## 先建立直覺：法庭只收證物

```
   對手（不同模型）提出的「指控」                 法庭（主 session）
   ──────────────────────────                 ─────────────────
   F1：時鐘倒退會清空 bucket  ＋ 指令            重跑指令 → 失敗 → 違反意圖？ → 成立
   F2：burst 會超過上限       ＋ 指令            重跑指令 → 沒有失敗 → 駁回
   F3：閒置的 key 不會被清掉   ＋ 指令            重跑指令 → 失敗 → 違反意圖？ → 意圖沒提到 → 駁回（但記錄）
   F4：b 應該改名為 bucket     （沒有指令）       沒有證物 → 駁回
   F5：高負載下可能會慢        （沒有指令）       沒有證物 → 駁回
                                                   │
                                                   ▼
                                     判決書：成立的指控 + 每個對手被駁回幾條、為什麼
                                     （判決只列出，不執行任何修正）
```

## 一、interrogate：四段

interrogate 的 SKILL.md 很短，但每一句都是規則。

### Frame：先說清楚意圖

> The scope is what the user names. Otherwise it is the branch diff from its merge base with the base branch, through the working tree. State the intent with the **in-your-own-words** skill, from the user's message, the commits, and the PR description. Ask the user when the intent is unclear.

**意圖是審判的法律**。一個發現要成立，必須違反意圖。沒有意圖，就沒有判準——任何「這和我預期的不一樣」都會變成發現。下面的實驗會看到這一點有多關鍵。

### Fan out：對手的 brief

借用 arena 的 Phase A step 3–4 與 Phase B（各自的 worktree、模型取自 `interrogate reviewers` 那一行），每個對手拿到範圍、意圖、和這段 brief：

> Break this change. Prove each finding with a run against the real code, such as a failing test, a repro script, or command output that shows the wrong behavior. For a design or plan, run a prototype or probe the current tree. Keep repros in your own worktree. Report each finding with a title, the exact command, the output it printed, and the output the intent requires. Leave out anything you did not run. That covers style, naming, structure, and risks that need a future change to matter. No findings is a valid result.

注意被**明確排除**的東西：風格、命名、結構、「要等未來某個改動才會有影響」的風險。以及最後一句——「沒有發現」是一個合法的結果。這句話在對抗模型的一個傾向：被叫去找問題的模型，會覺得「什麼都沒找到」等於沒完成任務，於是製造問題。

### Verify：重跑每一個指令

> Verify each finding per arena Phase F by rerunning its command. Keep a finding when the output shows the failure and the failure breaks the intent. Drop the rest. Merge findings that share a root cause.

**兩個條件**：輸出顯示了失敗，**而且**這個失敗違反意圖。

> Skip arena Phases C, D, and E. Adversaries find different failures, so the verdict keeps every verified finding, not one adversary's list.

這是 interrogate 和 arena 最大的不同：arena 要挑**一個**最好的；interrogate 要保留**所有**被證明的。不同的對手找到不同的失敗，挑一個對手的清單就會漏掉其他對手的。

### Output

- **Intent**：重述的意圖
- **Findings**：每個被驗證的發現，附指令、輸出、它怎麼違反意圖、哪些對手找到它。沒有就寫「no verified findings」
- **Dropped**：每個對手被駁回幾條、為什麼

而且 interrogate **只報告，不修**（「Report the findings. Do not apply fixes.」）。官方指南：「Read the Dropped list too. You can override the verdict.」

## 二、實驗：審判 Ch 18 的限流器

目標程式碼是 Ch 18 嫁接出來的 `final/limiter.mjs`：

```js
export function createLimiter({ capacity, refillPerSec, now = Date.now }) {
  const buckets = new Map();
  return {
    tryTake(key) {
      const t = now();
      const b = buckets.get(key) ?? { tokens: capacity, at: t };
      b.tokens = Math.min(capacity, b.tokens + ((t - b.at) / 1000) * refillPerSec);
      b.at = t;
      const ok = b.tokens >= 1;
      if (ok) b.tokens -= 1;
      buckets.set(key, b);
      return ok;
    },
  };
}
```

**意圖**：「never more than capacity in a burst, and refills one token per 1/refillPerSec seconds」

### 對手的發現（模擬兩個對手：opus 與 sonnet）

```json
[
  {"id":"F1","by":["opus"],"title":"clock stepping backwards drains a bucket","cmd":"node f-clock-back.mjs","want":"[true,true,false]","breaks":"refills one token per 1/refillPerSec seconds"},
  {"id":"F2","by":["opus","sonnet"],"title":"a burst can exceed capacity","cmd":"node f-burst.mjs","want":"[true,true,true,false,false]","breaks":"never more than capacity in a burst"},
  {"id":"F3","by":["sonnet"],"title":"idle keys are never evicted","cmd":"node f-idle-keys.mjs","want":"heap stays flat","breaks":null},
  {"id":"F4","by":["sonnet"],"title":"rename b to bucket for clarity","cmd":null,"want":null,"breaks":null},
  {"id":"F5","by":["opus"],"title":"might be slow under heavy load","cmd":null,"want":null,"breaks":null}
]
```

三個有指令的發現，各自的重現腳本：

```js
// f-clock-back.mjs：取一個 token 後，系統時鐘被 NTP 往回調 10 秒，再過 5 秒
import { createLimiter } from "./limiter.mjs";
let t = 100_000;
const l = createLimiter({ capacity: 3, refillPerSec: 1, now: () => t });
l.tryTake("k");
t -= 10_000;
t += 5_000;
const after = [1, 2, 3].map(() => l.tryTake("k"));
console.log(JSON.stringify(after));
```

```js
// f-burst.mjs：時間不動，連續 5 次
import { createLimiter } from "./limiter.mjs";
const l = createLimiter({ capacity: 3, refillPerSec: 1, now: () => 0 });
console.log(JSON.stringify([1, 2, 3, 4, 5].map(() => l.tryTake("k"))));
```

```js
// f-idle-keys.mjs：20 萬個只出現一次的 key
import { createLimiter } from "./limiter.mjs";
let t = 0;
const l = createLimiter({ capacity: 1, refillPerSec: 1, now: () => t });
const before = process.memoryUsage().heapUsed;
for (let i = 0; i < 200_000; i++) { l.tryTake(`ip:${i}`); t += 1000; }
console.log(`heap grew ${Math.round((process.memoryUsage().heapUsed - before) / 1e6)} MB for 200000 one-off keys`);
```

### 驗證器

把 interrogate 的 Verify 規則寫成程式：

```js
// verify-findings.mjs：重跑每個發現的指令；只保留「失敗，而且違反意圖的某一條」的發現
import fs from "node:fs";
import { execSync } from "node:child_process";
const intent = process.argv[2];
const findings = JSON.parse(fs.readFileSync("findings.json", "utf8"));
console.log(`intent: ${intent}\n`);
const kept = [], dropped = [];
for (const f of findings) {
  if (!f.cmd) { dropped.push(`${f.id} ${f.title}: no command, nothing ran`); continue; }
  const got = execSync(f.cmd).toString().trim();
  if (got === f.want) { dropped.push(`${f.id} ${f.title}: rerun shows the intended output (${got})`); continue; }
  if (!f.breaks || !intent.includes(f.breaks)) { dropped.push(`${f.id} ${f.title}: real (${got}), but breaks no clause of the intent`); continue; }
  kept.push(`${f.id} ${f.title} [${f.by.join(", ")}] breaks "${f.breaks}"\n    $ ${f.cmd}\n    got:  ${got}\n    want: ${f.want}`);
}
console.log("VERIFIED FINDINGS"); kept.forEach((k) => console.log("  " + k));
console.log("\nDROPPED"); dropped.forEach((d) => console.log("  " + d));
```

> 本課第一版的驗證器**只檢查了第一個條件**（輸出不等於期望值就保留），結果 F3 被當成成立的發現。重讀 interrogate 原文才發現漏了「and the failure breaks the intent」。現在的版本要求每個發現指出它違反意圖的哪一條；用字串包含判斷是很粗糙的做法，真實的 interrogate 由主 session 用判斷完成這一步。

```bash
node verify-findings.mjs "never more than capacity in a burst, and refills one token per 1/refillPerSec seconds"
```

```
intent: never more than capacity in a burst, and refills one token per 1/refillPerSec seconds

VERIFIED FINDINGS
  F1 clock stepping backwards drains a bucket [opus] breaks "refills one token per 1/refillPerSec seconds"
    $ node f-clock-back.mjs
    got:  [false,false,false]
    want: [true,true,false]

DROPPED
  F2 a burst can exceed capacity: rerun shows the intended output ([true,true,true,false,false])
  F3 idle keys are never evicted: real (heap grew 29 MB for 200000 one-off keys), but breaks no clause of the intent
  F4 rename b to bucket for clarity: no command, nothing ran
  F5 might be slow under heavy load: no command, nothing ran
```

五個發現，一個成立。逐一看駁回的理由：

- **F2**：兩個對手都提出了，而且聽起來很合理（「burst 會超過上限」）。但重跑指令，輸出正是意圖要求的。**兩個對手一致，不代表它是對的**——一致是「高訊號」，但仍然要重跑。
- **F3**：是一個**真的**問題（20 萬個一次性的 key 讓 heap 長了 29 MB），但意圖沒有提到記憶體。
- **F4、F5**：沒有指令。F4 是風格，F5 是一個沒有量測的猜測——brief 明確排除這兩類。

F1 是真的 bug：時鐘往回調 10 秒再前進 5 秒，`t - b.at` 是 -5000，token 被扣成負的。一個還有 2 個 token 的 key，接下來三次全部被拒絕。這種事在真實世界會發生：NTP 校時、虛擬機暫停後恢復、使用者手動改系統時間，都可能讓 `Date.now()` 倒退。

### 意圖決定了什麼算發現

F3 被駁回，不是因為它不重要，而是因為**意圖沒有說記憶體要有界**。把那一條加進意圖：

```bash
node verify-mem.mjs "never more than capacity in a burst, refills one token per 1/refillPerSec seconds, and memory stays bounded by active keys"
```

```
VERIFIED FINDINGS
  F1 clock stepping backwards drains a bucket [opus] breaks "refills one token per 1/refillPerSec seconds"
    ...
  F3 idle keys are never evicted [sonnet] breaks "memory stays bounded by active keys"
    $ node f-idle-keys.mjs
    got:  heap grew 29 MB for 200000 one-off keys
    want: heap stays flat
```

F3 變成了成立的發現。這是 interrogate 設計裡最重要的一課：**意圖寫得不完整，真實的問題會被合法地駁回**。這就是為什麼 Frame 要求用 `in-your-own-words` 重述意圖、意圖不清楚時要問使用者——意圖是唯一需要人參與的地方。

而 Dropped 清單把 F3 列了出來（「real ... but breaks no clause of the intent」），讓人有機會說「其實記憶體我也在乎」。這是官方指南說「Read the Dropped list too. You can override the verdict.」的原因。

## 三、blast-radius：證明那一個安全的事實

F1 的修正：把預設時鐘從 `Date.now`（牆上時鐘，會倒退）換成 `performance.now()`（單調時鐘，依規格不會倒退）。

```diff
-export function createLimiter({ capacity, refillPerSec, now = Date.now }) {
+import { performance } from "node:perf_hooks";
+
+export function createLimiter({ capacity, refillPerSec, now = () => performance.now() }) {
```

一行的改動。它會在別處弄壞什麼？這是 blast-radius 的問題。

### blast-radius 的核心主張

> Listing the callers is not the job. The agent can grep those in a second. The job is the breakage grep won't show you.
>
> A blast-radius writeup that sounds right is worthless. It reads as convincing whether or not it's true. So don't hand back the writeup. Find the one or two facts the whole thing depends on and prove them by running code.

### 證據階梯

| 級 | 說明 |
|---|---|
| 1 | 你說的。單獨存在時毫無價值 |
| 2 | 你指出了那一行（真實的 `file:line`，或函式庫自己的原始碼） |
| 3 | 你證明壞的情況不會發生（一步一步走過失敗路徑，它到不了） |
| 4 | **你跑了它**：一支呼叫真實程式碼、錯了就大聲失敗的腳本或測試 |
| 5 | 你在執行中的 app 裡重現了它 |

「For each fact the change's safety depends on, get it as far down this list as is cheap, and say where it stopped.」

### 找出那一個事實

`performance.now()` 和 `Date.now()` 有一個重要的差別：**原點不同**。`Date.now()` 是從 1970 年起的毫秒數；`performance.now()` 是從程序啟動起的毫秒數。如果任何地方把這兩種時間混在一起比較（例如把 `b.at` 寫進資料庫、下次啟動再讀回來；或另一段程式碼用 `Date.now()` 去和 `b.at` 比），這個改動就會造成大災難。

所以這個改動之所以安全，依賴**一個事實**：

> **bucket 的時間戳只在限流器內部、和同一個 `now()` 來源的值比較；它從不離開這個物件。**

把它推到第 4 級：

```bash
node --input-type=module -e '
import { createLimiter } from "./limiter.mjs";
const l = createLimiter({ capacity: 2, refillPerSec: 1 });
console.log("public keys:", JSON.stringify(Object.keys(l)));
console.log("origins differ: Date.now()=", Date.now(), " performance.now()=", Math.round(performance.now()));
const burst = [1,2,3].map(() => l.tryTake("k"));
const t0 = performance.now(); while (performance.now() - t0 < 1005) {}
console.log("default clock, burst then 1.005s later:", JSON.stringify(burst), l.tryTake("k"));
'
```

```
public keys: ["tryTake"]
origins differ: Date.now()= 1793194064684  performance.now()= 11
default clock, burst then 1.005s later: [true,true,false] true
```

三件事被證明了：

1. **對外只有 `tryTake`**：`buckets` 與裡面的 `at` 無法從外部取得（第 4 級，執行 `Object.keys`）
2. **兩個時鐘的原點差了 1.79 兆毫秒**：如果真的有混用，數字會差到不可能不出事——這確認了風險是真的，所以那個事實很重要
3. **預設時鐘下行為正確**：真實地等 1.005 秒，refill 了一個 token（第 4 級）

（1.005 秒而不是剛好 1 秒，是為了留 5ms 的餘量避開計時邊界；`while` 忙等是為了在 `-e` 腳本裡不用 async。）

Ch 18 的三個確定性測試也照樣通過（它們注入自己的時鐘，不受預設值影響）：

```
ℹ pass 3
ℹ fail 0
```

### 不能用 grep 證明的部分

blast-radius 的 Step 3：「Look where grep stops.」這個例子裡，grep 可以告訴你 `createLimiter` 被誰呼叫，但它**不能**告訴你：有沒有呼叫端傳入 `now: Date.now`、然後把結果和另一個用預設時鐘的限流器的狀態混在一起？在這個小 repo 裡沒有其他呼叫端，所以風險被清除；在真實的 codebase 裡，這要逐一確認每個傳了 `now` 的呼叫端。

### blast-radius 的報告格式

> - **What it does.** 把限流器的預設時鐘從牆上時鐘換成單調時鐘；不明顯的部分是兩者的原點不同。
> - **The one fact it's safe because of.** bucket 的時間戳只在限流器內部、和同一個來源比較，從不離開物件。到第 4 級：`Object.keys` 只有 `tryTake`；預設時鐘下 burst 與 refill 行為正確（指令與輸出如上）。
> - **Risks.** 有呼叫端傳入自訂的 `now`，同時又依賴預設時鐘的語意（可能性低：本 repo 沒有其他呼叫端；代價高：時間差 1.79 兆毫秒，所有 key 立刻滿桶或永遠空桶）。檢查方法：`grep -rn "createLimiter(" --include='*.mjs'` 後逐一看是否傳了 `now`。
> - **Cleared.** 確定性測試：注入自己的時鐘，不受影響（3 pass）。
> - **Before you merge.** 加一個「時鐘倒退不清空 bucket」的回歸測試（就是 F1 的腳本改成 assertion）。

## 四、什麼時候用哪一個

| 技能 | 輸入 | 問的問題 | 證據要求 | 會修嗎 |
|---|---|---|---|---|
| interrogate | 一個改動 + 意圖 | 這個改動能被弄壞嗎？ | 每個發現要有重跑會失敗、而且違反意圖的指令 | 不會，只報告 |
| blast-radius | 一個小改動 | 它會在 diff 以外弄壞什麼？ | 那一個安全事實要推到盡可能高的證據等級 | 不會，交回一份報告與一支測試 |
| no-comments | 一個 diff | 哪些註解該刪？ | 由沒寫過那些註解的 reviewer 判斷（Ch 23） | 會（修被接受的） |
| 一般 code review | 一個 PR | 有什麼問題？ | 不一定 | 視情況 |

官方的建議：「A small, finished change you're unsure about needs `/interrogate` alone.」「For a small diff you don't fully trust, `/blast-radius` finds what it could break elsewhere.」以及「Don't spend adversaries on an abstract plan. `/interrogate` drops every finding a run does not prove, so a plan with no code behind it gives them little to break.」

## 底層機制：為什麼「跑得出來」這個門檻這麼有效

```
   沒有執行門檻                                  有執行門檻
   ────────────                                 ──────────
   對手的成本：寫一句話                            對手的成本：寫一支能失敗的腳本
   → 產出大量便宜的猜測                            → 只產出它真的相信、而且證明得了的
   作者的成本：逐條反駁                            作者的成本：重跑指令
   → 被說服的是「說得好的」那方                     → 被說服的是「跑得出來的」那方
   審查結果：取決於辯論能力                         審查結果：取決於程式碼的實際行為
```

這個門檻讓**發現的成本**和**驗證的成本**對調了：提出一個發現變貴（要寫腳本），驗證一個發現變便宜（重跑指令）。在 agent 能大量產出評論的環境裡，這個對調是讓審查維持可用的關鍵。

代價也很清楚：很多真實但難以在短時間內重現的問題（例如只在特定的生產流量下出現的競態）會被駁回。interrogate 不是唯一的審查方式，它是**最不容易被雜訊淹沒**的那一種。對於「測試很難碰到」的性質，pstack 有另一個工具：formal verification（Ch 21）。

## 踩雷集錦

1. **兩個對手都同意就直接接受**：本章的 F2 被兩個對手提出，重跑之後不成立。一致是高訊號，不是證明。
2. **意圖寫得太窄**：F3 是真實的記憶體問題，因為意圖沒提到記憶體而被駁回。interrogate 只能保護你寫下的意圖。
3. **驗證器只檢查「輸出不同」**：本課第一版就犯了這個錯，把不違反意圖的 F3 當成成立。兩個條件都要。
4. **讓 interrogate 去修**：它只報告。修是另一個步驟，通常是新的 Bug fix 或 Feature。混在一起，你就失去了「審查者不是作者」的分離。
5. **blast-radius 交回一份很有說服力的分析**：原文：「A blast-radius writeup that sounds right is worthless.」要找到那一個事實，跑程式碼證明它，並說出證據停在第幾級。

## 進階：再往深一層

- **把驗證器接上 CI**：interrogate 的發現格式（標題、指令、實際輸出、意圖要求的輸出）本身就是一個回歸測試的雛形。把被驗證的發現轉成測試 commit 進 repo，下一次同樣的問題會被 CI 擋下來——這是 Encode Lessons in Structure 用在審查結果上。
- **對抗計畫而不是程式碼**：原文允許「For a design or plan, run a prototype or probe the current tree」。對一份設計，對手要寫 prototype 證明設計的某個假設不成立。這比對著文字辯論有用得多，但成本更高。
- **跨家族的對手**：在 Claude Code 預設設定下，`interrogate reviewers` 的兩個席位都是 Claude（Ch 17）。用 Codex plugin 的 `/codex:adversarial-review` 加一個 GPT 家族的對手，是本課目前找到最直接的補法（本章沒有實際執行）。

## 動手練習

1. 照本章重建限流器與三個發現腳本，跑一次驗證器。然後自己扮演第三個對手，找一個新的發現（提示：`capacity: 0`？`refillPerSec` 為負數？非常大的 `capacity`？），寫成指令加入 `findings.json`。它被保留還是駁回？
2. 把 F1 的腳本改寫成一個 `node:test` 測試，在換成 `performance.now()` 之前與之後各跑一次（注意：F1 注入了自己的時鐘，所以預設值的改動**不會**影響它——你要怎麼寫一個能證明預設時鐘不會倒退的測試？這個問題本身就是 blast-radius 的練習）。
3. 拿你最近收到的一則 code review 評論，判斷它在 interrogate 的規則下會被保留還是駁回。如果會被駁回，要怎麼把它變成一個可以重跑的指令？

## 本章重點整理

- interrogate：Frame（用 in-your-own-words 重述意圖）→ Fan out（對手必須用執行證明每個發現；風格、命名、未來的風險一律排除；沒有發現是合法結果）→ Verify（重跑；輸出失敗**而且**違反意圖才保留；同病因合併）→ Output（成立的發現 + 每個對手的駁回清單），只報告不修
- 意圖決定什麼算發現：本章的記憶體問題在意圖加上一條之後才成立；Dropped 清單讓人能推翻判決
- blast-radius：不是列出呼叫者，而是找到改動之所以安全的那一個事實，用執行程式碼把它推到證據階梯的第 4 級以上，並說出停在哪一級
- 執行門檻讓「提出發現」變貴、「驗證發現」變便宜，這是審查在大量 agent 評論下仍然可用的關鍵

## 自我檢核

- [ ] 不看本章，能說出 interrogate 保留一個發現的兩個條件嗎？
- [ ] 本章的 F3 是真實的問題，為什麼它第一次被駁回？這說明了 interrogate 的哪個限制？
- [ ] blast-radius 的證據階梯有五級，本章的那個安全事實推到了第幾級？要推到第 5 級需要什麼？
- [ ] interrogate 和 arena 都會開多個不同模型的 subagent，為什麼 interrogate 跳過 arena 的挑底與嫁接？

## 延伸閱讀

### 原始碼

- **[skills/interrogate/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/interrogate/SKILL.md)** 與 **[skills/blast-radius/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/blast-radius/SKILL.md)**
  - **讀哪裡**：兩份全文；blast-radius 的「How sure are you」證據階梯
  - **能學到什麼**：本章所有規則的原文

### 官方指南

- **[docs/guide/04-design.md「Break it with /interrogate」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/04-design.md)** 與 **[10-recipes-and-pitfalls.md「Review a branch skeptically」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/10-recipes-and-pitfalls.md)**
  - **能學到什麼**：官方的 prompt 範例，例如「no nitpicks unless it's an actual bug or regression」這種預先過濾雜訊的寫法

### 文章與規格

- **[W3C High Resolution Time](https://www.w3.org/TR/hr-time-3/)**
  - **讀哪裡**：monotonic clock 與 time origin 的定義
  - **能學到什麼**：`performance.now()` 為什麼不會倒退、它的原點在哪裡——本章那個安全事實背後的規格
  - **注意**：Node.js 的 `perf_hooks.performance` 依循這份規格的語意；Node 的實作細節以 Node 文件為準
- **[Google Engineering Practices — How to do a code review](https://google.github.io/eng-practices/review/reviewer/)**
  - **這篇說什麼**：Google 的 code review 指南，包括如何標示 nit、什麼是 blocking
  - **和本章的關聯**：一般 code review 的成熟做法；對照 interrogate 的「只收證物」，你會看到兩種審查文化各自在擋什麼

Part 3 結束。下一個練習：在 Claude Code 裡，真的用 subagent 跑一場 arena。

→ [練習 C：在 Claude Code 手刻一場 mini arena](./practice-c-mini-arena.md)
