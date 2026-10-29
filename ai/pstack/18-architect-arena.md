# Ch 18 — architect 與 arena：設計兩次，挑底再嫁接

> **目標**：讀懂 pstack 最核心的兩個多模型技能。`arena` 讓 N 個 runner 平行做同一件事、由另一個家族的評審依 rubric 打分、挑一個當底、把其他候選的好點子手動嫁接進去；`architect` 在寫程式之前先畫型別與模組邊界，而且「設計兩次」——它的 Phase B 就是一場 arena。你會親手跑一次迷你 arena：兩個真的能跑的限流器候選、一份 rubric、一個用執行結果證明的設計紅旗、一次嫁接，以及驗證嫁接結果的確定性測試。

> **環境**：pstack 0.15.36、Node.js 26。兩個候選與最終版本由本課作者手寫（模擬兩個 runner 的產出），所有程式與輸出都是實際跑出來的；agent 層面的 arena 流程依 arena / architect 原文描述，本章沒有實際 spawn runner。

## 為什麼需要這個？

官方設計章開頭的一句話：

> One attempt at a hard design locks in the first shape the model thought of.

模型的第一個設計，是它最先想到的設計。它不一定差，但你沒有任何東西可以比較，所以你不知道它好不好。人類工程師面對重要的設計決定時，會畫兩三個方案比一比；agent 預設只畫一個，然後直接開始寫。

arena 與 architect 把「畫幾個方案比一比」變成一個有固定步驟、能被審查的流程。

## 先建立直覺：建築設計競圖

```
   業主的需求書（同一份 brief，也是合約）
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
  事務所 A  事務所 B  事務所 C      ← runner：各自獨立、互不知道對方的設計
   │        │        │               （各自的 worktree 或目錄）
   └────────┼────────┘
            ▼
     評審團（不是任何一家事務所的人）  ← cross-judge：另一個模型家族、唯讀、看 rubric
            │
            ▼
     選一個當底案                    ← 主 session 自己也讀完每一份，和評審比對
            │
            ▼
     從落選案裡挑一兩個好點子，       ← graft：手動、在同一個心智模型下重新整合
     重新整合進底案                     不是複製貼上
            │
            ▼
     驗收                            ← 和任何產出一樣，用 Prove It Works 驗證
```

## 一、arena：六個階段

arena 的 SKILL.md 只有 48 行，但每一行都有它的位置。開頭就要求 todo 有六個項目：Frame、Fan out、Cross-judge、Pick、Graft、Verify。

### Phase A：Frame——prompt 就是合約

> Every candidate gets the same prompt, so the prompt is the contract.

1. 說清楚每個候選要產出什麼
2. **寫 rubric**：3–6 條針對**這個任務**、可以打分的具體標準。「It is the picker's tool in Phase D. Candidates only see the task.」——候選看不到 rubric
3. 挑 runner（依 `arena runners` 那一行，Ch 17）
4. **每個候選自己的輸出位置**：worktree，或 `/tmp/arena-<slug>/candidate-<n>/`（Separate Before Serializing Shared State）

為什麼候選看不到 rubric？因為看到 rubric 的候選會**針對 rubric 優化**，而不是針對問題本身。這和 Ch 29 eval 的盲測原則是同一個道理。

### Phase B：Fan out

在一則訊息裡同時 spawn 全部 N 個 subagent，每個拿到：任務、共用 grounding 的路徑、自己的輸出路徑、以及「產出成品，加上一份簡短的理由，說明你考慮過哪些替代方案、拒絕了什麼」。

有候選沒產出？「proceed with N-1 and note the dropout.」

### Phase C：Cross-judge——評審永遠不是同一家族

> Spawn one readonly judge subagent. The judge is never from the parent's family.

評審看到 rubric 和以路徑標示的候選，逐條打分，推薦一個底案並說明理由。它和主 session 的閱讀**平行**進行。

Ch 17 已經看過：在 Claude Code 預設設定下，沒有其他家族可用，評審會被跳過，主 session 自己挑，並且必須說明。

### Phase D：Pick a base

> Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge when Phase C ran one. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous.

挑底的準則：「Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API.」

### Phase E：Graft——手動，在一個心智模型下

> Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand ... so the result stays coherent under one mental model. Don't paste mechanically.

兩個特殊情況：

- **候選收斂成同一個形狀**：「that is strong agreement. Note it and ship the consensus shape. No graft is needed.」
- **候選差異巨大**：「Phase A was under-specified. Reframe and re-run rather than averaging the divergence.」——不要把三個完全不同的設計取平均

### Phase F：Verify

和任何產出一樣，用 Prove It Works 驗證合成後的成品。驗證發現問題時，要回頭判斷：是 Phase A 的框架錯了（重新框定），還是有候選抓到了、你漏掉了嫁接（回 Phase E）。

### 輸出

一份合成的成品，附一份簡短的合成說明：選了哪個當底、為什麼；評審的結論（或評審被跳過）；每一個嫁接與它的來源；拒絕了什麼、為什麼；收斂或缺席的情況；驗證結果。

## 二、迷你 arena：一個限流器

我們手動走一次 arena 的六個階段。任務：設計一個 per-key 的限流器（例如每個 IP 每秒最多 N 次請求）。

### Frame

**brief（兩個候選都拿到）**：「為 HTTP handler 設計一個 per-key 限流器。呼叫端每收到一個請求要能用一行判斷是否放行。」

**rubric（只有挑選者看得到）**：

| # | 標準 | 為什麼是這一條 |
|---|---|---|
| R1 | 呼叫端用一個呼叫完成一次判斷 | handler 不該知道限流器的內部步驟 |
| R2 | 並發呼叫下不會超過上限 | Node 的 handler 是非同步的，會交錯 |
| R3 | 時間可以被測試控制 | refill 行為必須能被確定性地測試 |
| R4 | 記憶體不隨請求數成長 | 高流量下不能每個請求存一筆 |

### Fan out：兩個候選

**candidate-1**（token bucket，一個函式回傳一個物件）：

```js
// candidate-1/limiter.mjs
export function createLimiter({ capacity, refillPerSec }) {
  const buckets = new Map();
  return {
    tryTake(key) {
      const now = Date.now();
      const b = buckets.get(key) ?? { tokens: capacity, at: now };
      b.tokens = Math.min(capacity, b.tokens + ((now - b.at) / 1000) * refillPerSec);
      b.at = now;
      const ok = b.tokens >= 1;
      if (ok) b.tokens -= 1;
      buckets.set(key, b);
      return ok;
    },
  };
}
```

**candidate-2**（sliding window，一個 class，時間由呼叫端傳入）：

```js
// candidate-2/limiter.mjs
export class SlidingWindow {
  constructor(windowMs) { this.windowMs = windowMs; this.hits = new Map(); }
  record(key, now) { (this.hits.get(key) ?? this.hits.set(key, []).get(key)).push(now); }
  count(key, now) {
    const list = (this.hits.get(key) ?? []).filter((t) => now - t < this.windowMs);
    this.hits.set(key, list);
    return list.length;
  }
}
```

依 architect 的 runner prompt，每個候選要**先寫呼叫端的用法**。兩者的用法：

```js
// usage-1.mjs
import { createLimiter } from "./candidate-1/limiter.mjs";
const limiter = createLimiter({ capacity: 3, refillPerSec: 1 });
const results = [1, 2, 3, 4].map(() => limiter.tryTake("ip:1"));
console.log("candidate-1 burst of 4:", JSON.stringify(results));
```

```js
// usage-2.mjs
import { SlidingWindow } from "./candidate-2/limiter.mjs";
const w = new SlidingWindow(1000);
const LIMIT = 3;
const results = [1, 2, 3, 4].map(() => {
  const now = Date.now();
  if (w.count("ip:1", now) >= LIMIT) return false;
  w.record("ip:1", now);
  return true;
});
console.log("candidate-2 burst of 4:", JSON.stringify(results));
```

```
candidate-1 burst of 4: [true,true,true,false]
candidate-2 burst of 4: [true,true,true,false]
```

兩個都通過「連續 4 次、上限 3」這個基本測試。**只看這個結果，你分不出哪個好。**

### 用 design red flags 篩選

architect 要求每個候選都要對照 `references/design-red-flags.md` 篩選。candidate-2 的用法立刻觸發其中一條：

> **Shallow module.** A shallow module exposes a large interface while hiding little complexity. ... Look for these signs:
> - Callers coordinate several methods to complete one operation.

呼叫端要自己做「先 count、再判斷、再 record」三步。這不只是寫起來囉唆——在非同步的 handler 裡，「檢查」和「記錄」之間可能有其他請求插進來。紅旗要用執行證明（interrogate 的標準），寫一個並發的呼叫端：

```js
// race-2.mjs：6 個並發的呼叫端，各自在 count 與 record 之間有一次 await
import { SlidingWindow } from "./candidate-2/limiter.mjs";
const w = new SlidingWindow(1000);
const LIMIT = 3;
const checkThenRecord = async (key) => {
  const now = Date.now();
  if (w.count(key, now) >= LIMIT) return false;
  await new Promise((r) => setTimeout(r, 1));   // 例如：handler 在這裡查了一下快取
  w.record(key, now);
  return true;
};
const results = await Promise.all([1, 2, 3, 4, 5, 6].map(() => checkThenRecord("ip:1")));
console.log("candidate-2, 6 concurrent callers, limit 3:", JSON.stringify(results), "allowed =", results.filter(Boolean).length);
```

```
candidate-2, 6 concurrent callers, limit 3: [true,true,true,true,true,true] allowed = 6
```

上限 3，放行了 6 個。這是 check-then-act 競態：六個呼叫端都在任何人 record 之前做完了 count。**限流器的介面把一個原子操作拆成兩個方法，等於把正確性的責任推給了每一個呼叫端。** candidate-1 的 `tryTake` 在一個同步函式裡完成檢查與扣除，不會被插隊。

### Pick：逐條打分

| 標準 | candidate-1 | candidate-2 | 證據 |
|---|---|---|---|
| R1 一個呼叫完成判斷 | 是（`tryTake`） | 否（count + record） | 兩份 usage |
| R2 並發不超限 | 是（同步完成檢查與扣除） | 否（6 個放行 6 個） | race-2.mjs 的輸出 |
| R3 時間可測試 | **否**（內部呼叫 `Date.now()`） | **是**（`now` 由呼叫端傳入） | 原始碼 |
| R4 記憶體不隨請求成長 | 是（每個 key 一個 bucket） | 否（每個請求存一個時間戳，視窗內不清） | 原始碼 |

candidate-1 贏 3 條、candidate-2 贏 1 條。底案選 candidate-1：它的介面更深（Ousterhout 意義上的 deep module：一個小介面藏住了完整的決策）、未來的維護者更難誤用。

但 candidate-2 在 R3 上有一個 candidate-1 沒有的好點子：**時間是一個依賴，不該在內部寫死**。沒有這一點，candidate-1 的 refill 行為只能靠 `sleep` 測試，又慢又不穩定。

### Graft：手動嫁接，不是複製

不是把 `SlidingWindow` 的 `now` 參數原樣搬過來（那會讓每個呼叫端都要傳時間，違反 R1），而是用 candidate-1 自己的形狀重新表達這個點子：**時間來源是一個可注入的依賴，預設是 `Date.now`**。

```js
// final/limiter.mjs
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

呼叫端的用法完全不變（`createLimiter({ capacity, refillPerSec })`），只有測試會傳 `now`。這就是「Fold each graft in by hand ... so the result stays coherent under one mental model」。

### Verify

有了可注入的時間，refill 終於能被確定性地測試：

```js
// final/limiter.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createLimiter } from "./limiter.mjs";

const clock = (start = 0) => { let t = start; return { now: () => t, advance: (ms) => { t += ms; } }; };

test("a burst beyond capacity is refused", () => {
  const c = clock();
  const l = createLimiter({ capacity: 3, refillPerSec: 1, now: c.now });
  assert.deepEqual([1, 2, 3, 4].map(() => l.tryTake("ip:1")), [true, true, true, false]);
});

test("one token comes back after one second", () => {
  const c = clock();
  const l = createLimiter({ capacity: 3, refillPerSec: 1, now: c.now });
  [1, 2, 3].forEach(() => l.tryTake("ip:1"));
  assert.equal(l.tryTake("ip:1"), false);
  c.advance(999);
  assert.equal(l.tryTake("ip:1"), false);
  c.advance(1);
  assert.equal(l.tryTake("ip:1"), true);
});

test("keys do not share a bucket", () => {
  const c = clock();
  const l = createLimiter({ capacity: 1, refillPerSec: 1, now: c.now });
  assert.equal(l.tryTake("a"), true);
  assert.equal(l.tryTake("b"), true);
  assert.equal(l.tryTake("a"), false);
});
```

```
✔ a burst beyond capacity is refused
✔ one token comes back after one second
✔ keys do not share a bucket
ℹ pass 3
ℹ fail 0
```

第二個測試的邊界值（999ms 不給、1000ms 給）在原本的 candidate-1 上**無法寫成測試**，除非真的等一秒。嫁接直接改善了可驗證性。

> 這份 final 還有一個本章沒處理的風險（推論，沒有測）：token 用浮點數累加，在很長的時間跨度與很小的 refill 速率下可能有精度誤差；而 `buckets` 從不清除閒置的 key，在 key 數量無上限時（例如以 IP 為 key 遭遇大量偽造來源），R4 只對「每個 key」成立，不對「所有 key」成立。合成說明的「拒絕了什麼 / 剩下的風險」應該寫出這兩點。

### 合成說明（arena 的輸出格式）

> 底案：candidate-1。理由：`tryTake` 在一個同步呼叫內完成檢查與扣除，呼叫端用一行判斷（R1），並發下不超限（R2，race-2.mjs：candidate-2 在上限 3 時放行 6 個）；每個 key 一個 bucket（R4）。
>
> 評審：跳過。主 session 與 runner 同為 Claude 家族，cross-judge pool 沒有其他家族可用。
>
> 嫁接：從 candidate-2 取「時間是注入的依賴」，改寫成 `createLimiter` 的可選參數 `now`，預設 `Date.now`；呼叫端用法不變。
>
> 拒絕：candidate-2 的 count / record 分離介面（shallow module，造成 check-then-act 競態）；每請求存時間戳的儲存方式（記憶體隨請求成長）。
>
> 驗證：三個確定性測試通過，包括 999ms / 1000ms 的 refill 邊界。
>
> 剩下的風險：浮點累加精度；閒置 key 不清除。

## 三、architect：先畫形狀，再填實作

architect 有五個 phase：Ground、Sketch、Agree、Implement、Scrap。

**Phase A：Ground。** 「Run the **how** skill over the relevant subsystems. Naming a file isn't grounding.」如果設計會改變所有權或分層，也要跑 `why`，「so the rationale becomes a constraint, not a guess」。只有真正的 greenfield 可以跳過。

**Phase B：Sketch。** 跑 arena，任務是設計草圖（型別、簽章、`not implemented` 的函式本體、偽碼），每個 runner 拿到 `references/runner-prompt.md`。三個硬性要求：

- **Design it twice**：「Require at least two structurally distinct candidates before synthesis, even when the first looks sufficient.」而且是「Whole-shape alternatives, not point fixes inside one shape」
- **對照 design red flags 篩選**（本章用過的那份清單）
- **比較介面深度**：「Prefer the design that hides more complexity behind a smaller, simpler public surface.」

runner prompt 最後一段特別值得記住：

> You are one of the parallel runners, each on a different model. Produce the best design your model can make. Don't hedge against the others. Differences between candidates are the signal used to pick a base and graft. Converging on a safe-looking middle defeats the exploration.

**Phase C：Agree（可選）。** 預設**不停**，直接從合成的設計進入實作。只有使用者明確說「with checkpoint」「stop and show me before implementing」才停下來給人看。

**Phase D：Implement。** 「The synthesized sketch is the contract. Deviations from it are signal to surface, not friction to absorb silently.」一個函式需要草圖沒預料到的參數時，要問：是草圖錯了、需求漏了、還是實作越界了？

**Phase E：Scrap。** 這是 architect 最獨特的部分——它明確規定**什麼時候把設計丟掉重來**：

- 同一種形狀的 workaround 在不相關的地方反覆出現
- 多個不相關的邊界情況都需要特殊分支
- 型別需要逃生口才能編譯（`any`、強制轉型、「實際上總是有值」的 optional 欄位）
- 草圖說狀態不共享，實作卻冒出「需要加鎖」的反射
- 呼叫端必須知道抽象的內部規則才能使用它
- 實作中出現兩個以上同一形狀的、獨立的偏離

「The signal is a *pattern*, not single instances.」還有一句很重要的平衡：「Complexity in the data is not complexity in the design.」——資料本身複雜，不代表設計錯了。

丟掉的時候：重跑 how、當成新限制一開始就存在重新設計、**先減法**（「The new sketch should be smaller than the old one before it grows」）、回 Phase B 重跑 arena。

**輸出**：呼叫端的用法**先寫**，型別草圖從用法推出；附一份依 `rationale-template.md` 的理由說明，其中「Alternatives considered」是必填的。

## 底層機制：為什麼「挑底 + 嫁接」比「取平均」好

```
  取平均（把三個設計的優點都塞進去）          挑底 + 嫁接
  ───────────────────────────────          ────────────────────
  A 的介面 + B 的儲存 + C 的錯誤處理          以 A 為底（一個完整的心智模型）
      │                                        │
      ▼                                        ▼
  三套互相不協調的假設                        從 B 拿一個點子，用 A 的語言重寫
  （A 的介面假設同步，B 的儲存假設非同步）       │
      │                                        ▼
      ▼                                    一致的設計，多了一個好點子
  一個每個部分都「合理」、
  整體卻說不通的設計
```

每個好設計都有一個內在的一致性：它的各個部分基於同一組假設。把三個設計的部分拼起來，三組假設會互相打架。這就是為什麼嫁接要「usually one or two things per candidate, not most of it」，而且要「by hand」。

## 對比與取捨

| 方式 | 何時用 | 成本 | 產出 |
|---|---|---|---|
| 直接實作（不設計） | 有先例、單一檔案、形狀明顯 | 最低 | 程式碼 |
| architect（含 arena） | 跨函式邊界、改變所有權 | 高（2+ runner + 可能的評審） | 型別草圖 + 理由 + 實作 |
| arena 直接用 | 獨立的決定（命名、格式、演算法）需要多個嘗試 | 中到高 | 一份合成成品 + 合成說明 |
| prototype | 開放問題能靠跑東西回答 | 低到中 | 一個決定 + 可丟棄的工具 |
| interrogate | 已完成的改動需要被挑戰 | 中 | 只保留有執行證明的發現（Ch 20） |

官方設計章的「多少設計工作才夠」階梯：一個你不確定的、已完成的小改動，只要 interrogate；跨函式邊界或改變所有權，architect（它會帶上 arena）；獨立的決定用 arena；能靠跑東西回答的開放問題用 prototype；有爭議又難以撤回的設計，architect 之後再 interrogate。

## 踩雷集錦

1. **把 rubric 給候選看**：候選會針對 rubric 優化，而不是針對問題。rubric 只給挑選者與評審。
2. **只看基本測試就挑**：本章兩個候選都通過「連續 4 次、上限 3」。差別要在並發、時間、記憶體這些 rubric 維度上才看得出來。
3. **機械式地合併**：把 candidate-2 的 `now` 參數原樣搬進 `tryTake(key, now)`，會讓每個呼叫端都要傳時間，破壞 R1。嫁接要用底案的語言重寫。
4. **候選差異很大時取中間**：原文：「If they wildly diverge, Phase A was under-specified. Reframe and re-run.」差異大代表 brief 沒說清楚，不是代表要折衷。
5. **architect 的草圖變成神聖不可改**：Phase E 明確規定什麼時候丟掉重來。型別需要 `any` 才能編譯、同樣的 workaround 到處出現，是設計錯了的證據，不是要你更努力地修補。

## 進階：再往深一層

- **讀 rationale-template.md**：它要求的「Tradeoffs accepted」格式（「we accept X in exchange for Y」）與「Open questions and risks」（寫成問題而不是斷言，「so the human's answer is the resolution」），是一份很好的設計文件模板，不只適用於 agent。
- **design red flags 的後半段**：「Split ownership」「Two ways to do one task」「Importable internals」「Hand-synced list」這四條是 2026 年下半年加的，它們的共同主題是「假設下一個貢獻者是 agent」：「Assume the next contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles.」這個假設會改變你對「好設計」的判斷——一個人類能靠文件理解的慣例，agent 會在第一次看到另一種寫法時就跟著複製。
- **用 arena 做非程式碼的決定**：命名、錯誤訊息的措辭、CLI 參數的設計都適合 arena。成本比程式碼 arena 低（產出是文字），而多個獨立嘗試的價值一樣高。

## 動手練習

1. 照本章跑一次 race-2.mjs，然後把 candidate-2 改成提供一個 `tryRecord(key, now, limit)` 方法（在一個同步函式裡完成檢查與記錄）。並發測試還會超限嗎？這個改動讓 candidate-2 在 R1、R2 上的分數變成多少？
2. 為一個你目前要做的設計決定寫一份 arena 的 Frame：brief（給候選）、rubric（只給挑選者，3–6 條）、每個候選的輸出位置。你的 rubric 裡有沒有一條是「只看一次執行結果分不出來」的？
3. 讀 `$PSTACK/skills/architect/references/design-red-flags.md`，拿你專案裡一個模組逐條對照。它觸發了幾條？每一條你能用一次執行證明嗎？

## 本章重點整理

- arena 六個階段：Frame（prompt 是合約、rubric 只給挑選者）→ Fan out（各自的輸出位置）→ Cross-judge（永遠不同家族、唯讀）→ Pick（逐條打分）→ Graft（手動、一兩個點子、在一個心智模型下）→ Verify
- 候選差異大代表 brief 沒說清楚，要重新框定而不是取平均；候選收斂代表強烈共識
- architect：Ground（how / why）→ Sketch（arena、設計兩次、紅旗篩選、比介面深度）→ Agree（預設不停）→ Implement（草圖是合約，偏離是訊號）→ Scrap（模式性的摩擦就丟掉重來）
- 本章的迷你 arena：兩個候選都通過基本測試；用並發執行證明了 shallow module 紅旗（上限 3 放行 6）；嫁接「時間是注入的依賴」讓 refill 能被確定性測試

## 自我檢核

- [ ] 不看本章，能說出 arena 的六個階段，以及 rubric 為什麼不給候選看嗎？
- [ ] 本章的 candidate-2 通過了基本測試，你用什麼證明它的介面有問題？那個證明對應 design red flags 的哪一條？
- [ ] 為什麼嫁接時不把 `now` 參數加到 `tryTake(key, now)` 上？
- [ ] architect 的 Phase E 列了六種「該丟掉設計」的徵兆。你最近有沒有遇過其中一種、卻選擇了繼續修補？

## 延伸閱讀

### 原始碼

- **[skills/arena/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/arena/SKILL.md)** 與 **[skills/architect/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/architect/SKILL.md)**
  - **讀哪裡**：兩份全文（48 行與 79 行）
- **[architect/references/](https://github.com/cursor/plugins/tree/main/pstack/skills/architect/references)**
  - **讀哪裡**：`design-red-flags.md`、`rationale-template.md`、`runner-prompt.md`
  - **能學到什麼**：三份可以直接借用的設計工具

### 官方指南

- **[docs/guide/04-design.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/04-design.md)**
  - **讀哪裡**：「Settle the shape with /architect」「Fan out attempts with /arena」「How much design work does a task deserve?」

### 書籍

- **《A Philosophy of Software Design》第二版** — John Ousterhout（2021）
  - **讀哪幾章**：Chapter 4「Modules Should Be Deep」、Chapter 5「Information Hiding (and Leakage)」、Chapter 11「Design it Twice」
  - **和本章的關聯**：design red flags 的「Shallow module」「Information leakage」「Pass-through method」與 architect 的「Design it twice」都直接來自這本書的概念與用語

### 文章

- **[Martin Fowler — Design Stamina Hypothesis](https://martinfowler.com/bliki/DesignStaminaHypothesis.html)**
  - **這篇說什麼**：為什麼前期的設計工作會在之後回本，以及它在什麼時候不值得
  - **和本章的關聯**：architect 的成本只在「跨函式邊界、改變所有權」時才值得付；這篇給了這個取捨的經濟學版本

arena 讓多個 runner 做**同一件事**然後挑一個；下一章的 swarm 讓多個 worker 做**不同的切片**然後彙總。

→ [Ch 19 swarm：切片、競速與聚合](./19-swarm.md)
