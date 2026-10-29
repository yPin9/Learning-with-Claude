# Ch 22 — benchmark-checklist：解釋那個數字

> **目標**：把 Explain the Number 原則的完整版本——`benchmark-checklist` 技能的七個問題——逐一用一個會騙人的實驗走過：一個看起來是 CPU 瓶頸其實是在等待的程式、一個「新函式庫快 400 倍」其實是沒調校的比較、一個量到錯誤路徑的「優化」、一個從來沒被消費的 generator、一個只佔端到端 2.5% 的微優化。讀完你能在任何人（包括 agent 與你自己）報告一個效能數字時，問出那七個問題，並知道每個問題的證據該長什麼樣。

> **環境**：pstack 0.15.36、Node.js 26、macOS（Apple Silicon）。所有數字都是本機實際跑出來的，**你的數字會不同**；本章關心數字之間的關係與它們代表什麼。

## 為什麼需要這個？

> 如果你還沒讀過 Ch 10 的 hillclimb 實驗，先回看 [Ch 10 — Perf、Hillclimb 與 forensics](./10-perf-hillclimb-forensics.md) 的 H2 與 H3：非交錯量測高估了 50%，memo 讓中位數變成 0。

官方指南：

> A before-and-after number is the easiest evidence to get wrong by accident. A warm cache, a debug build on one side, or work that never ran inside the timed region can each produce a convincing speedup.

一個錯誤的效能數字比一個錯誤的功能更危險：功能錯了，使用者會回報；數字錯了，它會進入 PR 描述、進入週報、成為下一個決定的依據，而且沒有人會「回報」一個數字是錯的。

`benchmark-checklist` 是 pstack 對這件事的回答。Perf issue 與 Hillclimb playbook 會自動跑它；你在 playbook 之外量了東西、或看到別人的數字好得可疑時，自己打 `/pstack:benchmark-checklist`。

## 先建立直覺：七個問題

```
  一個數字：「export 快了 30%」
       │
       ├─ 1. 為什麼不是兩倍？            → 說出限制因素（limiter），從 profile 或計數器來
       ├─ 2. 每一邊都調校過嗎？           → 兩邊都用 production 的設定
       ├─ 3. 有沒有違反物理極限？         → 做算術：頻寬、核心數、被移除部分的佔比
       ├─ 4. 有錯誤嗎？                  → 數錯誤與非成功回應，檢查輸出正確
       ├─ 5. 能重現嗎？                  → 每邊至少 5 次、交錯、中位數與範圍
       ├─ 6. 重要嗎？                    → 量使用者真正在等的端到端路徑
       └─ 7. 工作真的發生了嗎？           → 確認工作在計時區段內執行
       │
       ▼
  結論：faster / slower / no measurable difference / inconclusive
       ＋ 數字、單位、次數、範圍、限制因素
```

## 一、跑之前：先寫下你要宣稱的句子

技能的第一段不是問題，是準備：

- **寫下你預期要做出的宣稱**，用你會出貨的句子（「export is 30% faster at p50 on the 60k-row dataset」）。七個問題就是在測這個句子。
- **讀量測腳本**：它計時什麼、計數什麼、忽略什麼。
- **檢查機器負載**：`uptime` 看 load average、`nproc` 看核心數。機器忙的話找出是什麼在跑；停不掉就交錯兩邊讓它們看到同樣的雜訊，並在報告裡說明。

（macOS 沒有 `nproc`；用 `sysctl -n hw.ncpu`。）

「先寫下宣稱」對應 Explain the Number 的第一條：「Write the decision before you run. Name the result that would change a decision, and the noise floor.」先決定什麼結果會改變你的決定，再跑，你就不會在看到數字之後才編一個故事去解釋它。

## 二、七個問題，七個實驗

### Q1：為什麼不是兩倍？

> Name the limiter. Profile in a run you do not report, because profilers and tracers slow the work down. ... If a change did not move the number, the limiter explains why, so find it before you call the change useless.

一個處理 40 個工作項目的程式，每個項目做一點 CPU 運算，加上一次 20ms 的等待（模擬一次網路呼叫）。調整並行度：

```js
// q1-limiter.mjs：同時量 wall time 與 CPU time
import { performance } from "node:perf_hooks";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function fetchAll(n, concurrency) {
  const work = Array.from({ length: n }, (_, i) => i);
  const worker = async () => { while (work.length) { work.pop(); await sleep(20); let x = 0; for (let i = 0; i < 2e5; i++) x += i; } };
  await Promise.all(Array.from({ length: concurrency }, worker));
}
for (const c of [1, 2, 4, 8]) {
  const cpu0 = process.cpuUsage(), t0 = performance.now();
  await fetchAll(40, c);
  const wall = performance.now() - t0, cpu = process.cpuUsage(cpu0);
  console.log(`concurrency ${c}: wall ${wall.toFixed(0)} ms, cpu ${((cpu.user + cpu.system) / 1000).toFixed(0)} ms, cpu/wall ${((cpu.user + cpu.system) / 1000 / wall).toFixed(2)}`);
}
```

```
concurrency 1: wall 884 ms, cpu 50 ms, cpu/wall 0.06
concurrency 2: wall 431 ms, cpu 20 ms, cpu/wall 0.05
concurrency 4: wall 215 ms, cpu 5 ms, cpu/wall 0.02
concurrency 8: wall 106 ms, cpu 5 ms, cpu/wall 0.04
```

**限制因素是等待，不是 CPU**：cpu/wall 只有 0.02–0.06，代表 94% 以上的時間程序在等。而 wall time 幾乎完全符合 40 × 20ms ÷ 並行度（800 / 400 / 200 / 100 ms，加上一點開銷）。這個模型讓你能預測：並行度到 40 時，wall time 會接近 20ms；超過 40 就沒有意義了，因為只有 40 個工作。

如果有人說「我把這個迴圈優化了一倍」（指那個 `for (i < 2e5)`），Q1 會告訴你：那個迴圈只佔總時間的幾個百分點，優化它對 wall time 幾乎沒有影響。

（第一輪的 CPU time 是 50ms、之後是 5–20ms，是因為 JIT：第一輪的迴圈大多在直譯模式下跑，之後被最佳化了。這也是一個 Q2 的線索——暖機與否會改變量到的東西。）

### Q2：每一邊都調校過嗎？

> Run every side the way production runs it: release builds, production flags and env, batching and transaction settings, connection pools, caches as warm or cold as production sees them ... If one side runs on defaults, you compared configurations, not implementations.

情境：「我們的 exporter 很慢，換成新的 CSV 函式庫快了很多。」

```js
// q2-untuned.mjs：「我們的 exporter」每列 append 一次；「新函式庫」組成一個字串寫一次
import fs from "node:fs";
import { performance } from "node:perf_hooks";
const rows = Array.from({ length: 5000 }, (_, i) => `${i},${i * 3}\n`);
const perRow = () => { fs.writeFileSync("/tmp/q2-a.csv", ""); for (const r of rows) fs.appendFileSync("/tmp/q2-a.csv", r); };
const csvLib = () => { let s = ""; for (const r of rows) s += r; fs.writeFileSync("/tmp/q2-b.csv", s); };
const time = (fn) => { const t0 = performance.now(); fn(); return performance.now() - t0; };
const a = [], b = [];
for (let i = 0; i < 5; i++) { a.push(time(perRow)); b.push(time(csvLib)); }
const med = (x) => x.sort((p, q) => p - q)[2].toFixed(1);
console.log(`"our exporter" (append per row): median ${med(a)} ms`);
console.log(`"new library"  (one write)     : median ${med(b)} ms`);
```

```
"our exporter" (append per row): median 78.8 ms
"new library"  (one write)     : median 0.2 ms
```

約 400 倍。結論：「換函式庫」？

Q2 的原文：「A limiter that is a setting (a commit per row, a debug build, a missing index) means that side is untuned. Tune it and measure again before you pick a winner.」我們的 exporter 慢的原因是**每列開關一次檔案**——這是一個設定（寫入的批次大小），不是實作的本質。把我們的 exporter 也改成一次寫入，差距就消失了。這次比較比的是「每列寫一次」vs「一次寫完」，不是「我們的程式碼」vs「新函式庫」。

而且原文特別強調：「When the user is choosing what to adopt, narrowing the claim to the code as it ships today does not fix this, because they adopt the option, not today's settings.」——就算你在報告裡寫「在目前的設定下，新函式庫比較快」，讀報告的人做的決定是「要不要換函式庫」，而這個決定不該建立在一個沒調校的比較上。

### Q3：有沒有違反物理極限？

> Do the arithmetic. Compare bytes per second with disk and network bandwidth, operations per second times cost per operation with your cores, and the time saved with the time the changed piece took. Removing a piece that takes 10% of the run can make the run at most about 11% faster.

最後一句是 Amdahl 定律的特例：一個佔總時間比例 p 的部分被**完全移除**，最多加速 1 / (1 − p) 倍。p = 10% 時是 1 / 0.9 ≈ 1.11，也就是約 11%。

用 Ch 10 的數字做一次算術：hillclimb 把 `uniqueUsers` 從 64ms 降到 0.36ms。如果有人說「整個請求快了 50%」，問：那個請求原本多長？如果請求是 200ms，`uniqueUsers` 佔 64ms（32%），完全移除最多快 1 / 0.68 ≈ 1.47 倍——接近 47%，所以「快 50%」**已經超過了理論上限**，代表它量到了別的東西（快取、另一個改動、雜訊）。

### Q4：有錯誤嗎？

> Count failures and non-success responses, and check that outputs are correct, not just present. Rejections are often fast, and timeouts and retries are slow. If the script does not count errors, add the count.

一個「新版本」的 JSON 解析：

```js
// q4-errors.mjs：新版本加了一個長度檢查
import { performance } from "node:perf_hooks";
const inputs = Array.from({ length: 50_000 }, (_, i) => JSON.stringify({ id: i, tags: Array(20).fill("x") }));
const oldParse = (s) => JSON.parse(s);
const newParse = (s) => { if (s.length > 64) throw new Error("too long"); return JSON.parse(s); };
for (const [name, fn] of [["old", oldParse], ["new", newParse]]) {
  let errors = 0; const t0 = performance.now();
  for (const s of inputs) { try { fn(s); } catch { errors++; } }
  console.log(`${name}: ${(performance.now() - t0).toFixed(1)} ms, errors ${errors}/${inputs.length}`);
}
```

```
old: 14.0 ms, errors 0/50000
new: 47.4 ms, errors 50000/50000
```

**這個結果和本章作者的預期相反。** 預期是「新版本拒絕所有輸入，所以看起來很快」（原文說「Rejections are often fast」）。實際上新版本**慢了三倍多**——在 V8 裡，`throw` 一個 `Error` 要建立 stack trace，比 `JSON.parse` 一個短字串還貴。

但不管方向是快還是慢，結論一樣：**新版本 100% 的輸入都失敗了，這個數字量的是錯誤路徑，不是解析**。如果腳本沒有數 `errors`，你會看到「新版本慢了 3 倍」然後去優化它，或者（在另一個錯誤處理比較便宜的語言或情境裡）看到「快了 3 倍」然後出貨一個什麼都不解析的版本。

這也是 Ch 10 harness 要求「print its error count and a count of the work done」的原因。

### Q5：能重現嗎？

> Run each side at least 5 times, alternating (A, B, A, B, and so on) so warmup, lazy initialization, caches, and drift favor neither. Report the median and range. A gap smaller than the run-to-run variation is no measurable difference.

Ch 10 的 H2 是這一題的完整實驗：先後量時，H1 是 0.69ms；交錯量 5 輪時，H1 是 0.46–0.50ms。非交錯的量測把 H1 高估了約 40%，如果拿它當基準，H2 的改善會被誇大。

### Q6：重要嗎？

> Next to any micro result, measure the end-to-end path a user waits on, with realistic data sizes and concurrency, and report the micro result as a share of the whole.

Ch 10 優化過的 `uniqueUsers`，放進一個完整的 CLI 執行：

```js
// q6-cli.mjs
import { uniqueUsers } from "/tmp/lab/ch10/h2.mjs";
const events = Array.from({ length: 20000 }, (_, i) => ({ user: `u${i % 5000}` }));
const t0 = performance.now(); uniqueUsers(events); const t = performance.now() - t0;
console.error(`in-process uniqueUsers: ${t.toFixed(2)} ms`);
```

```bash
for i in 1 2 3; do /usr/bin/time -p node q6-cli.mjs; done
```

```
in-process uniqueUsers: 1.03 ms
real 0.04
in-process uniqueUsers: 1.00 ms
real 0.04
in-process uniqueUsers: 1.01 ms
real 0.04
```

使用者執行這個 CLI 要等約 40ms（`/usr/bin/time` 的精度到 10ms），其中 `uniqueUsers` 佔約 1ms——**約 2.5%**。就算把它優化到 0，使用者等待的時間最多少 2.5%。Ch 10 的 H2 把它再快了 25%，對端到端的影響是 2.5% × 25% ≈ 0.6%。

（這裡的 1ms 比 Ch 10 harness 的 0.36ms 高，是因為這裡只跑一次、包含了第一次執行的 JIT 暖機；harness 跑 7 次取中位數，量的是暖機後的狀態。兩個數字量的是不同的東西——這本身就是 Q2「caches as warm or cold as production sees them」的例子：一個只跑一次的 CLI，production 看到的就是冷的那個。）

### Q7：工作真的發生了嗎？

> Confirm the work ran inside the timed region ... Lazy code (generators nobody iterates, promises nobody awaits, results the JIT can discard) and timeouts produce numbers for work that never happened.

把一個 `map` 改寫成 generator：

```js
// q7-lazy.mjs
import { performance } from "node:perf_hooks";
const data = Array.from({ length: 200_000 }, (_, i) => ({ id: i, price: i % 97 }));
const before = (rows) => rows.map((r) => ({ ...r, tax: r.price * 0.05 }));
function* after(rows) { for (const r of rows) yield { ...r, tax: r.price * 0.05 }; }
for (const [name, fn, consume] of [["before (array)", before, false], ["after (generator), not consumed", after, false], ["after (generator), consumed", after, true]]) {
  const t0 = performance.now(); const out = fn(data); let n = 0; if (consume) for (const _ of out) n++;
  console.log(`${name.padEnd(34)} ${(performance.now() - t0).toFixed(2)} ms  work done: ${consume ? n : Array.isArray(out) ? out.length : 0}`);
}
```

```
before (array)                     7.64 ms  work done: 200000
after (generator), not consumed    0.01 ms  work done: 0
after (generator), consumed        7.76 ms  work done: 200000
```

「改成 generator 快了 700 倍」——因為計時區段裡**什麼都沒做**。generator 被建立了，但沒有人迭代它。消費之後，兩者幾乎一樣（7.64 vs 7.76ms，差距在雜訊範圍內）。`work done` 那一欄是這個實驗的關鍵：它讓「工作量為 0」變得可見。

Ch 10 的 H3（memo）是同一題的另一種形式：工作發生在計時區段**之前**（第一次呼叫），之後的六次都只是查快取。

## 三、結論的格式

> - Lead with the verdict: faster, slower, no measurable difference, or inconclusive.
> - Give the number with its unit, the run count, the range, and the limiter. For example, "p50 41 ms → 33 ms, median of 7 runs per side, range 32 to 35 ms after, bound by JSON parsing on one core."
> - Call the verdict inconclusive when you claim a difference but cannot name the limiter, when a side ran untuned, or when you could not check questions 4 and 7. Name the gap.

**inconclusive 是一個合法、而且常常是正確的結論**。本章的五個實驗，如果直接報告，結論都是錯的：

| 實驗 | 天真的結論 | 依七個問題的結論 |
|---|---|---|
| Q1 優化 CPU 迴圈 | 「迴圈快了一倍，程式會快很多」 | 限制因素是等待；優化迴圈對 wall time 幾乎無影響 |
| Q2 換函式庫 | 「新函式庫快 400 倍」 | inconclusive：我們這一邊沒調校（每列寫一次） |
| Q4 新解析器 | 「新版本慢 3 倍」 | inconclusive：100% 錯誤，量到的是錯誤路徑 |
| Q6 微優化 | 「uniqueUsers 快 25%」 | faster（micro），但只佔端到端 2.5%，使用者感受不到 |
| Q7 generator | 「快 700 倍」 | no measurable difference：消費後 7.64 vs 7.76ms |

PR 描述裡只放**一個**主要數字（Opening a PR playbook）；次數、範圍、限制因素的證據放在連結的附件或筆記裡。

## 四、ballpark 的例外

> For a quick ballpark the user asked for, one run is enough. Still check questions 4 and 7, and say that it is one run. Skip the rest unless that run looks wrong. A choice between options is never a ballpark.

最後一句很重要：使用者說「大概量一下就好」，如果他是要**在兩個選項之間做選擇**，那就不是 ballpark，七個問題都要問。

## 底層機制：為什麼「說出限制因素」是最重要的一題

```
   你知道限制因素                              你不知道限制因素
   ──────────────                             ──────────────
   「被 JSON 解析綁在一個核心上」                「快了 30%」
        │                                           │
        ├─ 能預測：多一個核心會怎樣                     ├─ 不能預測任何事
        ├─ 能解釋：為什麼改動 X 沒效果                   ├─ 改動 X 沒效果 → 「X 沒用」（可能是錯的）
        ├─ 能檢查：數字有沒有超過那個極限（Q3）            ├─ 不能檢查
        └─ 能發現：量到的是不是別的東西（Q7）              └─ 量到快取、量到錯誤，都看不出來
```

原文：「If you cannot say why the number is not twice as good, you do not know what you measured.」限制因素是讓一個數字從「一個觀察」變成「一個模型」的那一步。有了模型，其他六個問題都有了檢查的依據。

## 對比與取捨

| 做法 | 成本 | 會被騙的情況 |
|---|---|---|
| 跑一次、報數字 | 最低 | 全部七種 |
| 跑多次取平均 | 低 | Q1、Q2、Q3、Q4、Q6、Q7（雜訊減少了，系統性的錯誤沒有） |
| 交錯跑、中位數與範圍 | 中 | Q1、Q2、Q3、Q4、Q6、Q7 |
| **七個問題全部問** | 高 | 很少；剩下的通常是「production 和量測環境不一樣」 |
| 在 production 上量（A/B、canary） | 很高 | 最少，但需要基礎設施 |

## 踩雷集錦

1. **比較沒調校的一邊**：本章的 Q2 「快 400 倍」來自「每列寫一次」這個設定。先把兩邊都調成 production 的樣子再比。
2. **不數錯誤**：Q4 的新版本 100% 失敗。不數錯誤的 benchmark，量到的可能是錯誤路徑——而錯誤路徑可能比正常路徑快，也可能慢。
3. **計時區段裡沒有工作**：Q7 的 generator、Ch 10 的 memo。在報告裡附上「完成的工作量」（列數、bytes、請求數），讓「0」變得可見。
4. **報告一個超過理論上限的改善**：Q3 的算術。被改動的部分佔 32%，端到端就不可能快超過 47%。超過了，就是量到了別的東西。
5. **把 ballpark 用在選擇上**：「大概比一下哪個快」如果是要選一個，就不是 ballpark。

## 進階：再往深一層

- **統計檢定**：原文說「When the call is close, use a rank-sum test or the harness's own statistics.」Mann-Whitney U 檢定（rank-sum test）不假設常態分佈，適合效能量測這種常常有長尾的資料。[hyperfine](https://github.com/sharkdp/hyperfine) 這類命令列 benchmark 工具會自動做暖機、多次執行、報告平均、標準差與範圍。
- **Q1 的工具**：原文列了 `top` / `pidstat` 看每個程序的 CPU、runtime profiler（`node --cpu-prof`、`py-spy`、`perf`）、I/O wait、`strace -c`（Linux）。本章用 `process.cpuUsage()` 比 wall time，是一個最簡單的「CPU-bound 還是在等」判斷。記得 Ch 10 的教訓：profiler 本身也會錯，而且原文說「Profile in a run you do not report, because profilers and tracers slow the work down.」
- **讓 harness 自動回答 Q4 與 Q7**：Hillclimb 要求凍結的 harness 印出錯誤數與工作量，「so each keep-or-revert checks questions 4 and 7 for free」。你自己的 benchmark 腳本也該這樣做。

## 動手練習

1. 修正 Q2 的「我們的 exporter」，讓它也一次寫入（或每 1,000 列寫一次），再跑一次比較。差距剩多少？寫一句符合原文格式的結論。
2. 把 Q4 的長度門檻從 64 改成 1,000（讓所有輸入都通過），再跑一次。兩個版本的差距在雜訊範圍內嗎？依 Q5 的規則交錯跑 5 輪再判斷。
3. 拿你專案最近一次的效能改善（或一個 PR 裡的效能宣稱），逐一回答七個問題。哪幾題你答不出來？那個數字的結論應該是什麼？

## 本章重點整理

- 跑之前先寫下你要宣稱的句子；七個問題在測這個句子
- Q1 限制因素（本章：cpu/wall 0.05，瓶頸是等待）、Q2 調校（本章：「快 400 倍」來自每列寫一次）、Q3 物理極限與 Amdahl（移除 10% 最多快約 11%）、Q4 錯誤（本章：100% 失敗而且反而更慢）、Q5 交錯重現、Q6 端到端佔比（本章：2.5%）、Q7 工作有沒有發生（本章：未消費的 generator 快 700 倍）
- 結論只有四種：faster、slower、no measurable difference、inconclusive；說不出限制因素、有一邊沒調校、沒檢查 Q4 與 Q7，就是 inconclusive
- 選擇之間的比較永遠不是 ballpark

## 自我檢核

- [ ] 不看本章，能依序說出七個問題嗎？
- [ ] 本章 Q4 的結果和預期相反，為什麼結論仍然是「這個數字不能用」？
- [ ] 一個改動讓某個函式快了 10 倍，那個函式佔請求時間的 5%。端到端最多快多少？
- [ ] 你最近看過的效能數字裡，有哪一個你現在會判成 inconclusive？缺的是哪一題的證據？

## 延伸閱讀

### 原始碼

- **[skills/benchmark-checklist/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/benchmark-checklist/SKILL.md)**
  - **讀哪裡**：全文，特別是「Before you run anything」與 Report 段
- **[principle-explain-the-number](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-explain-the-number/SKILL.md)**
  - **讀哪裡**：最後一段「You skipped this when」

### 書籍

- **《Systems Performance》第二版** — Brendan Gregg（Addison-Wesley, 2020）
  - **讀哪幾章**：Chapter 2「Methodologies」的 Anti-Methods 與 Benchmarking；Chapter 12「Benchmarking」整章（「benchmarking sins」清單和本章的七個問題高度重疊）
- **[Gernot Heiser — Systems Benchmarking Crimes](https://gernot-heiser.org/benchmarking-crimes.html)**
  - **這是什麼**：一位作業系統研究者整理的「學術論文裡常見的 benchmark 錯誤」清單
  - **和本章的關聯**：「only relative numbers」「no indication of variance」「improper baseline」這些條目和 Q2、Q5 直接對應；它證明這些錯誤在專業研究者之間也很常見

### 文章與工具

- **[Andrey Akinshin — Pro .NET Benchmarking](https://aakinshin.net/prodotnetbenchmarking/)** 與他的部落格
  - **為什麼值得讀**：BenchmarkDotNet 的作者，對「為什麼 benchmark 會說謊」寫得最系統化的人之一；語言是 .NET，但統計與方法論通用
- **[hyperfine](https://github.com/sharkdp/hyperfine)**
  - **這是什麼**：命令列的 benchmark 工具，自動暖機、多次執行、報告範圍、比較多個指令
  - **和本章的關聯**：Q5 的自動化；用它取代本章 Q6 的 `for i in 1 2 3; do /usr/bin/time ...`

驗證行為、驗證數字之後，交付之前還有一步：清理。下一章是 pstack 對「AI 文字」與「AI 註解」的處理。

→ [Ch 23 清理文字與註解：unslop、no-comments、technical-writing](./23-unslop-no-comments.md)
