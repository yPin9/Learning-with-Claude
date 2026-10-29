# Ch 10 — Perf、Hillclimb 與 forensics

> **目標**：搞懂 pstack 處理效能問題的四份 playbook：一次性的 **Perf issue**、持續爬坡的 **Hillclimb**、對活著的程序做診斷的 **Runtime forensics**、對已經抓下來的 profile 做診斷的 **Trace forensics**。你會實際跑一次迷你 hillclimb——建一個凍結的量測 harness、證明它分得出差別、三次迭代（兩次保留、一次因為「量到的是快取不是工作」而 revert）、每次都寫進決策日誌；再抓一份真實的 CPU profile，親眼看到 profiler 把時間算錯了地方。

> **環境**：pstack 0.15.36、Node.js 26、macOS（Apple Silicon）。所有量測數字都是本機實際跑出來的；**你的數字會不同**，因為機器、負載、Node 版本都會影響。本章關心的是數字之間的**關係**與量測方法，不是絕對值。

## 為什麼需要這個？

效能工作的證據是一個數字，而數字是最容易騙人的證據：

- 快取暖了，數字就好看
- 只跑一次，雜訊就被當成訊號
- 程式碼根本沒執行到被計時的區段，數字是 0
- profiler 把時間記在錯的函式上，你優化了錯的地方

pstack 用四份 playbook 加一個專門的 `benchmark-checklist` 技能（Ch 22 整章）來處理這件事。這一章講 playbook 的流程，下面的實驗會**順便**踩到幾個 benchmark-checklist 要擋的坑。

## 先建立直覺：四份 playbook 的分工

```
                        效能相關的任務
                              │
          ┌───────────────────┼────────────────────┬─────────────────┐
          ▼                   ▼                    ▼                 ▼
     Perf issue           Hillclimb         Runtime forensics   Trace forensics
     一次性修一個          持續改善一個指標      活的程序有症狀       已經有一份 profile
     量得到的慢            直到達標            （leak、空轉、閃爍）  （cpuprofile、trace…）
          │                   │                    │                 │
     baseline → 修 →      凍結 harness →       抓訊號 → 縮減 →      讀檔 → 轉成可查詢 →
     比對 → PR            迴圈（一次一個假設）    證明機制 → 對到原始碼  縮小 → 對到原始碼
          │                   │                    │                 │
     交付：修正            交付：一串被接受的      交付：診斷          交付：診斷
                          commit + 決策日誌       （不修）            （不修）
```

forensics 兩份都**只交付診斷，不修**。知道原因之後，交回 Bug fix 或 Perf issue。

## 一、Perf issue：七條效能口訣

Perf issue playbook 的核心是第 2 步的口訣，**由便宜到貴，依序嘗試**：

```
  1. Don't do it.                    根本不做（沒人用結果的工作，直接停掉）
  2. Do it, but don't do it again.   做一次就好（快取、去重）
  3. Do it less.                     少做一點
  4. Do it later.                    晚點做（延遲、懶載入）
  5. Do it when they're not looking. 在使用者沒在等的時候做（背景、閒置時）
  6. Do it concurrently.             平行做
  7. Do it cheaper.                  做得更便宜（換演算法、換資料結構）

  When an earlier mantra meets the target, stop.
```

這個順序很反直覺：工程師的本能是從第 7 條開始（「換一個更快的演算法」），但第 1 條常常是最大的勝利——那個慢的工作，結果根本沒人用。口訣最後一句「前面的口訣達標就停」是 Laziness Protocol 在效能上的應用。

另外兩條規則：

- **「Tie every fix to a measurement, don't read source instead of measuring.」**讀程式碼推測熱點不算數。
- **「Don't claim a perf ceiling without running it first.」**「這已經是極限了」這種話要有實測支撐。

## 二、Hillclimb：科學地爬坡

Hillclimb 是給「持續改善一個指標」用的。它和 Perf issue 的差別寫在 poteto-mode 的索引裡：「Distinct from Perf issue, which is a one-off fix.」

核心紀律一句話：

> one change, one measurement, keep or revert. Never stack untested changes, and never claim a win from code inspection.

我們跑一次完整的迷你 hillclimb。題目：一個計算不重複使用者的函式，使用者抱怨在使用者很多時很慢。

```js
// unique.mjs（baseline）
export function uniqueUsers(events) {
  const seen = [];
  for (const e of events) {
    if (!seen.includes(e.user)) seen.push(e.user);
  }
  return seen;
}
```

### Step 1：先搞懂工作負載，再選指標

> Ground the workload and architecture before choosing the metric. ... name the realistic workload dimensions that can move the result (data size, history, state, concurrency), and select a case that reproduces the user's complaint.

這個函式有兩個會影響結果的維度：事件數 `n`、不重複使用者數 `users`。使用者抱怨的是「使用者很多時」。

停止條件要**同時有目標和嘗試次數的下限**：「a checkable stop predicate that pairs a target with a floor on attempts so a lucky early win can't end the run」。原文範例是「at least 50% better than baseline and at least 10 iterations」。本章為了篇幅只跑 3 次迭代，這在真實的 hillclimb 裡是不夠的。

### Step 2：建 harness、證明它敏感、然後凍結

```js
// harness.mjs：固定種子產生事件，跑 N 次取中位數，並印出工作量與錯誤數
import { performance } from "node:perf_hooks";
const [modPath, nArg, usersArg, runsArg] = process.argv.slice(2);
const n = Number(nArg), users = Number(usersArg), runs = Number(runsArg ?? 7);
const { uniqueUsers } = await import(modPath);
let seed = 42;
const rand = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
const events = Array.from({ length: n }, () => ({ user: `u${Math.floor(rand() * users)}` }));
const times = [];
let out;
for (let i = 0; i < runs; i++) {
  const t0 = performance.now();
  out = uniqueUsers(events);
  times.push(performance.now() - t0);
}
times.sort((a, b) => a - b);
const expected = new Set(events.map((e) => e.user)).size;
const errors = out.length === expected ? 0 : 1;
const median = times[Math.floor(runs / 2)];
console.log(JSON.stringify({ n, users, runs, median_ms: +median.toFixed(3), min_ms: +times[0].toFixed(3), max_ms: +times[runs - 1].toFixed(3), work: out.length, errors }));
```

幾個設計決定：

- **固定種子**（`seed = 42`，線性同餘產生器的常數 1103515245 與 12345 是 C 標準函式庫範例實作常見的那組）：每次跑都是同一份資料，比較才公平
- **跑 7 次取中位數**：playbook 說「sampled enough to clear the noise (median of N, not a single run)」；7 是本章為了速度選的，不是神奇數字
- **印出 `work` 和 `errors`**：playbook 要求 harness「print its error count and a count of the work done」——這兩個數字之後會救我們一次

**證明 harness 敏感**：「Run contrasting realistic workloads and confirm the target case reproduces the symptom while easier cases separate as expected.」

```bash
node harness.mjs ./unique.mjs 20000 50
node harness.mjs ./unique.mjs 20000 5000
node harness.mjs ./unique.mjs 20000 5000
```

```
{"n":20000,"users":50,"runs":7,"median_ms":2.039,"min_ms":1.161,"max_ms":2.336,"work":50,"errors":0}
{"n":20000,"users":5000,"runs":7,"median_ms":63.772,"min_ms":60.101,"max_ms":65.607,"work":4794,"errors":0}
{"n":20000,"users":5000,"runs":7,"median_ms":65.856,"min_ms":63.447,"max_ms":74.405,"work":4794,"errors":0}
```

50 個使用者 2ms、5,000 個使用者 64ms：目標案例重現了抱怨，簡單案例如預期地快。同一個案例跑兩次，中位數差約 2ms——這就是**雜訊底線**，任何小於這個的「改善」都不能信。（5,000 是 `users` 參數；實際出現的不重複使用者是 4,794，因為隨機抽樣不會剛好抽滿。）

Harness 凍結，commit。之後的每一次迭代都用這同一支 harness，不准改。

### Step 3：開決策日誌

用 `show-me-your-work` 技能附的 `log.sh`（Ch 26 詳談）：

```bash
LOGSH=$PSTACK/skills/show-me-your-work/scripts/log.sh
bash $LOGSH decision.tsv frame \
  "metric: median ms of uniqueUsers on 20k events / 5k users, lower is better" \
  "this case reproduces the complaint; 50-user case stays fast" \
  "harness.mjs runs=7" \
  "baseline 63.8 ms, 65.9 ms on rerun"
```

### Step 4–5：迴圈，一次一個假設

**H1**：假設要點名一個**具體機制**，不是「試試看快取」。「`seen.includes` 是線性掃描，20k 事件 × 約 5k 個已見使用者，這是限制因素」。改成 `Set`：

```js
export function uniqueUsers(events) {
  const seen = new Set();
  for (const e of events) seen.add(e.user);
  return [...seen];
}
```

```
{"n":20000,"users":5000,"runs":7,"median_ms":0.689,"min_ms":0.496,"max_ms":2.249,"work":4794,"errors":0}
{"n":20000,"users":5000,"runs":7,"median_ms":0.684,"min_ms":0.504,"max_ms":1.87,"work":4794,"errors":0}
```

64ms → 0.69ms。`work` 仍是 4794、`errors` 仍是 0。再跑**回歸閘門**（必須持續通過的測試）——這裡我們只有一個「保持第一次出現的順序」的測試，`Set` 依插入順序迭代，所以通過：

```js
// gate.test.mjs
test("keeps first-seen order", () => {
  const events = ["b", "a", "b", "c", "a"].map((user) => ({ user }));
  assert.deepEqual(uniqueUsers(events), ["b", "a", "c"]);
});
```

```
ℹ pass 1
ℹ fail 0
```

保留，commit，記一列。

**H2**：「for-of 與展開運算子有 iterator 開銷」。改成索引迴圈與 `Array.from`。量三次：

```
{"n":20000,"users":5000,"runs":7,"median_ms":0.355,...}
{"n":20000,"users":5000,"runs":7,"median_ms":0.406,...}
{"n":20000,"users":5000,"runs":7,"median_ms":0.362,...}
```

0.69 → 0.36，看起來又快了一倍。**這太好了，好到可疑。** 微小的語法改變不太可能讓速度翻倍。benchmark-checklist 的第 5 問：「Run each side at least 5 times, alternating (A, B, A, B, and so on) so warmup, lazy initialization, caches, and drift favor neither.」我們剛才是先量完 H1、過一陣子再量 H2，不是交錯的。交錯重量 5 輪：

```
h1 0.463
h2 0.337
h1 0.503
h2 0.355
h1 0.465
h2 0.373
h1 0.485
h2 0.361
h1 0.475
h2 0.333
```

兩件事：

1. **H1 剛才的 0.69ms 是高估的**。交錯量時 H1 是 0.46–0.50ms。早先那兩次量測在不同的時間點、不同的機器狀態下跑，被雜訊推高了。如果我們拿 0.69 當基準，H2 的改善會被誇大成約 2 倍。
2. **H2 仍然確實比較快**：0.33–0.37 vs 0.46–0.50，兩個範圍不重疊。依 playbook 的規則（「Accept only when the metric moves past noise and the gate stays green」），保留。

坦白說，寫這一章之前我預期 H2 會被 revert——預期它的差別會淹沒在雜訊裡。我錯了。**這正是 Hillclimb 不准「從讀程式碼宣稱勝利」的原因，反過來也一樣：不准從讀程式碼宣稱失敗。**量測說了算。

（另一個必須提的問題：0.13ms 的差別**重要嗎**？benchmark-checklist 第 6 問「Does it matter?」——如果這個函式在一個 200ms 的請求裡只佔 0.5ms，這個勝利對使用者來說不存在。Hillclimb 的規則也說「Correctness and simplicity outrank the number」。H2 沒有變得更難讀，所以保留；如果它需要一段複雜的手動優化，就算量得到也應該放棄。）

**H3**：「同一份事件陣列算過就快取結果」。用 `WeakMap` 做 memo：

```js
const memo = new WeakMap();
export function uniqueUsers(events) {
  if (memo.has(events)) return memo.get(events);
  const seen = new Set();
  for (let i = 0; i < events.length; i++) seen.add(events[i].user);
  const out = Array.from(seen);
  memo.set(events, out);
  return out;
}
```

```
{"n":20000,"users":5000,"runs":7,"median_ms":0,"min_ms":0,"max_ms":1.495,"work":4794,"errors":0}
```

中位數 0ms。**無限快。**

當然不是。harness 用**同一個陣列**呼叫 7 次，第一次算、後面 6 次都是快取命中。我們量到的是 `WeakMap.get` 的速度，不是工作的速度。benchmark-checklist 第 7 問：「Did it even happen? Confirm the work ran inside the timed region.」

注意 `work: 4794, errors: 0` 在這裡**沒有**抓到問題——結果是對的，只是不是在計時區段內算出來的。這說明了 harness 的輸出要配合人的判斷：`max_ms: 1.495`（唯一一次真的算）和 `median_ms: 0` 之間的巨大落差，就是訊號。

而且這個快取在 production 有沒有用，取決於真實的呼叫模式：如果每次請求都是新的陣列，它永遠不會命中，只會多佔記憶體。Revert，記一列。

### 決策日誌

```bash
cut -f2,5,6 decision.tsv | column -s$'\t' -t
```

```
phase  evidence                                              result
frame  harness.mjs runs=7                                    baseline 63.8 ms, 65.9 ms on rerun
H1     commit 9f07468, harness median 0.689/0.684 ms         kept, 63.8 -> 0.69 ms, gate green
H2     commit cdacf7f; h1 0.463-0.503 ms, h2 0.333-0.373 ms  kept, ranges do not overlap, gate green
H3     harness median 0 ms, min 0, max 1.495                 reverted, measured the cache not the work
```

```bash
git log --oneline
```

```
cdacf7f perf(unique): index loop and Array.from
9f07468 perf(unique): track seen users in a Set
9fcbde1 baseline: uniqueUsers and frozen harness
```

一個被接受的改善一個 commit；被 revert 的 H3 不在歷史裡，但在日誌裡。日誌的價值在這裡：三個月後有人提議「加個快取吧」，日誌告訴他上次試過、為什麼不行。

### Step 6–7：衝過第一個平台、什麼時候停

> Push past the first plateau. On a stall, several rejects in a row, pivot category, combine near-misses, re-read the source, or try something more radical before concluding the hill is climbed.
>
> Stop when the predicate is met ... Don't relax the predicate to meet it, and don't quit while cheap untried hypotheses remain.

兩個方向的紀律：不要在第一次停滯就放棄；也不要為了達標而放寬標準。

## 三、Runtime 與 Trace forensics：先懷疑觀察方法

兩份 forensics playbook 的共同主題：**診斷要來自真實的產物，不是從原始碼推理**。

Trace forensics 的步驟裡有兩句特別重要：

- 「Transform the raw artifact into a form you can query. ... Reach the queryable shape before you read.」——先把 profile 轉成可查詢的形式（原文建議 sqlite），再開始讀
- 「A frame with no source mapping is not yet a diagnosis.」——沒有對應到原始碼的 frame 不是診斷

我們對 baseline 版本（`includes` 那版）抓一份 CPU profile：

```js
// drive.mjs：用 4 萬筆事件驅動慢的版本
import { uniqueUsers } from "./slow.mjs";
const events = Array.from({ length: 40000 }, (_, i) => ({ user: `u${(i * 7919) % 8000}` }));
console.log(uniqueUsers(events).length);
```

（7919 是一個質數，`i * 7919 % 8000` 讓使用者 id 均勻散開而不是連續出現。）

```bash
node --cpu-prof --cpu-prof-dir=prof drive.mjs
```

`.cpuprofile` 是一個 JSON：`nodes`（呼叫樹的節點，每個有 `callFrame`）、`samples`（每次取樣落在哪個節點）、`timeDeltas`（樣本之間的時間）。寫一支小工具把它轉成「每個函式的 self time」排行：

```js
// top-frames.mjs：把 .cpuprofile 轉成依 self time 排序的函式表
import fs from "node:fs";
const prof = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const byId = new Map(prof.nodes.map((n) => [n.id, n]));
const self = new Map();
const dt = prof.timeDeltas;
prof.samples.forEach((id, i) => self.set(id, (self.get(id) ?? 0) + (dt[i] ?? 0)));
const rows = new Map();
for (const [id, us] of self) {
  const { functionName, url, lineNumber } = byId.get(id).callFrame;
  const key = `${functionName || "(anonymous)"} ${url.split("/").pop() || "(native)"}:${lineNumber + 1}`;
  rows.set(key, (rows.get(key) ?? 0) + us);
}
const total = [...rows.values()].reduce((a, b) => a + b, 0);
[...rows].sort((a, b) => b[1] - a[1]).slice(0, 5).forEach(([k, us]) =>
  console.log(`${(us / 1000).toFixed(1).padStart(8)} ms ${((us / total) * 100).toFixed(1).padStart(5)}%  ${k}`));
```

```
   232.4 ms  89.5%  (anonymous) drive.mjs:1
    12.6 ms   4.9%  (idle) (native):0
     5.8 ms   2.2%  uniqueUsers slow.mjs:1
     2.5 ms   1.0%  consoleCall (native):0
     2.5 ms   1.0%  (garbage collector) (native):0
```

**這個結果是錯的。** 它說 89.5% 的時間在 `drive.mjs` 的模組頂層——那裡只有一行建陣列和一行呼叫。我們**知道**（從 hillclimb 的量測）時間在 `uniqueUsers` 的 `includes` 迴圈裡，但 profiler 只給它 2.2%。

Prove It Works 原則說：「When verification fails, suspect the observation method before suspecting the system.」懷疑觀察方法。第一個假設：V8 的 TurboFan 把 `uniqueUsers` inline 進了呼叫者，所以樣本被記在呼叫者身上。測它：

```bash
node --no-turbo-inlining --cpu-prof --cpu-prof-dir=prof2 drive.mjs
```

```
   196.2 ms  91.5%  (anonymous) drive.mjs:1
     6.3 ms   2.9%  (idle) (native):0
     3.8 ms   1.8%  (garbage collector) (native):0
     3.1 ms   1.4%  uniqueUsers slow.mjs:1
     ...
```

**假設被推翻了**：關掉 inlining，歸屬還是錯的。換一個實驗，關掉所有最佳化層級：

```bash
node --max-opt=0 --cpu-prof --cpu-prof-dir=prof3 drive.mjs
```

```
   205.4 ms  93.5%  uniqueUsers slow.mjs:1
     6.6 ms   3.0%  (idle) (native):0
     2.5 ms   1.1%  (anonymous) drive.mjs:1
     ...
```

這次歸屬正確了：93.5% 在 `uniqueUsers`。所以錯誤歸屬和最佳化編譯有關，但不是 TurboFan 的 inlining。

> **精確的 V8 機制本章沒有確認**（推論）：可能和最佳化後的程式碼在取樣時的 frame 歸屬有關，但我們只證明了「關掉最佳化層級就正確、只關 inlining 不夠」。如果你需要確切的原因，要去讀 V8 的 CPU profiler 實作或問 V8 團隊。這裡的教訓不依賴那個機制：**profiler 的輸出也是一個需要驗證的量測**。

這正是 Trace forensics 的「If you have a paired capture, diff the before and after artifacts to confirm. Otherwise, mark the finding as the strongest hypothesis the artifact supports, not a confirmed cause.」——我們用三份對照的 capture 才得到一個可信的歸屬；如果只有第一份，我們會去優化 `drive.mjs` 第 1 行。

## 對比與取捨

| playbook | 輸入 | 交付 | 迴圈？ | 最重要的紀律 |
|---|---|---|---|---|
| Perf issue | 一個量得到的慢 | 修正 + 前後數字 | 否 | 口訣由便宜到貴；每個修正綁一個量測 |
| Hillclimb | 一個指標 + 目標 | 一串被接受的 commit + 決策日誌 | 是 | 一次一個改變、凍結 harness、keep or revert |
| Runtime forensics | 一個活的症狀 | 診斷 | 否 | 在活的程序上證明機制 |
| Trace forensics | 一份已抓的 profile | 診斷 | 否 | 先轉成可查詢、沒對到原始碼不算診斷 |

## 踩雷集錦

1. **非交錯量測**：本章的 H1 先量了 0.69ms，交錯量時是 0.47ms。先後量兩個版本，機器狀態的變化會被算成版本的差別。永遠 A、B、A、B。
2. **量到快取**：H3 的中位數是 0。harness 重複呼叫同一個輸入，memo 讓計時區段裡什麼都沒做。中位數和最大值差幾個數量級時，懷疑量到的不是工作。
3. **相信 profiler 的第一份輸出**：本章的第一份 profile 把 89.5% 的時間算在錯的地方。profiler 是量測工具，量測工具也會錯；用不同設定交叉比對。
4. **從讀程式碼宣稱勝利或失敗**：我預期 H2 會失敗，它成功了。兩個方向的「我覺得」都不算數。
5. **為了達標放寬標準**：「目標是 50%，我們做到 45%，差不多了」。Hillclimb：「Don't relax the predicate to meet it.」做不到就說做不到，附上下一個你會試的想法。

## 進階：再往深一層

- **把 profile 轉成 sqlite**：Trace forensics 建議「Dump the trace or heap snapshot into sqlite, one row per sample, frame, or node」。本章的 top-frames.mjs 是最簡化的版本；轉成 sqlite 後，你可以用 SQL 做「某個函式的所有呼叫者」「某段時間內的熱點」這類查詢，而且查詢可以存成腳本重跑（Build the Lever）。
- **效能口訣的更多細節**：上游 commit「give the perf playbook its eight strategy families」（2026-07-13）顯示這個列表曾經演變過；目前版本是七條。讀那個 PR 可以看到作者怎麼思考效能策略的分類。
- **多個獨立假設時平行跑**：Hillclimb 允許「When several independent hypotheses are live, fan them to parallel subagents, each in its own worktree」。注意在同一台機器上平行跑 benchmark 會互相干擾——量測本身要序列化，或在不同的機器上跑（Ch 19 swarm）。

## 動手練習

1. 照本章跑一次 hillclimb 的 H1 與 H3。然後**修改 harness**，讓它每次都用新的陣列（例如每輪 `events.slice()`），再量一次 H3。memo 版本還快嗎？這個修改應該在凍結 harness 之前就做——你會怎麼在 Step 2 的「證明敏感」階段發現這個需求？
2. 對你專案裡一個慢的指令跑 `node --cpu-prof`（或你的語言的 profiler），用 top-frames.mjs 排行。然後用 `--max-opt=0` 再跑一次，比較兩份排行是否一致。
3. 拿七條口訣，對你最近做過的一次效能優化回頭檢查：你是從第幾條開始的？前面的口訣有沒有可能更便宜地達標？

## 本章重點整理

- Perf issue 的口訣由便宜到貴：不做 → 不重做 → 少做 → 晚做 → 趁沒人看時做 → 平行做 → 做得更便宜；前面的達標就停
- Hillclimb：先證明 harness 敏感再凍結；一次一個有具體機制的假設；交錯量測；keep or revert；每次都記日誌
- 太好的數字要懷疑：本章的 H2 交錯後仍成立，H3 是量到快取
- forensics 只交付診斷；profiler 也會錯，用對照的 capture 交叉驗證

## 自我檢核

- [ ] 不看本章，能依序說出七條效能口訣，並解釋為什麼順序是這樣嗎？
- [ ] 本章的 H1 為什麼一開始量到 0.69ms、交錯後是 0.47ms？如果沒有交錯重量，H2 的改善會被誇大多少？
- [ ] H3 的 `work` 和 `errors` 都正常，為什麼它仍然是一個假的勝利？你是從哪個數字看出來的？
- [ ] 你的團隊報告過的效能數字裡，有沒有一個你現在會想追問「這是交錯量的嗎？計時區段裡真的有工作嗎？」？

## 延伸閱讀

### 原始碼

- **[playbooks/hillclimb.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/hillclimb.md)** 與 **[perf-issue.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/perf-issue.md)**
  - **讀哪裡**：hillclimb 的 Step 1–2（工作負載與 harness）；perf-issue 的口訣
  - **能學到什麼**：本章實驗的原文規則
- **[playbooks/trace-forensics.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/trace-forensics.md)**
  - **讀哪裡**：Step 2–5
  - **能學到什麼**：「先轉成可查詢」與「配對的 capture 才能確認」的原文

### 官方文件

- **[Node.js — `--cpu-prof` 與 V8 profiler](https://nodejs.org/api/cli.html#--cpu-prof)**
  - **讀哪裡**：`--cpu-prof`、`--cpu-prof-dir`、`--cpu-prof-interval`
  - **能學到什麼**：取樣間隔怎麼設；`.cpuprofile` 檔的產生方式
  - **前提**：Node.js 基礎
- **[Chrome DevTools Protocol — Profiler domain](https://chromedevtools.github.io/devtools-protocol/tot/Profiler/)**
  - **讀哪裡**：`Profile`、`ProfileNode`、`CallFrame` 型別
  - **能學到什麼**：本章 top-frames.mjs 解析的 JSON 格式的正式定義

### 書籍與文章

- **《Systems Performance》第二版** — Brendan Gregg（Addison-Wesley, 2020）
  - **這本書的定位**：效能分析方法論的標準參考書
  - **讀哪幾章**：Chapter 2「Methodologies」，特別是 USE method 與「Anti-Methods」（隨機改參數、街燈效應）——後者正是 Hillclimb 要擋的行為
- **[Brendan Gregg — Flame Graphs](https://www.brendangregg.com/flamegraphs.html)**
  - **為什麼值得讀**：火焰圖的發明者解釋怎麼讀 CPU profile；搭配本章的「profiler 也會錯」一起讀，你會更清楚 profile 的每一層代表什麼
- **[Aleksey Shipilёv — JMH samples 與 benchmarking 文章](https://shipilev.net/)**
  - **這是什麼**：JVM 世界最權威的微基準測試資料；死碼消除、常數折疊、量到快取這些陷阱在任何有 JIT 的語言（包括 V8）都一樣
  - **讀哪裡**：「Nanotrusting the Nanotime」一文
  - **前提**：不需要會 Java，概念通用

Part 1 的 playbook 到此全部看完。下一步是練習：照著 Bug fix playbook，一步不漏地修一個真 bug。

→ [練習 A：照 Bug fix playbook 手動修一個真 bug](./practice-a-bug-fix-by-playbook.md)
