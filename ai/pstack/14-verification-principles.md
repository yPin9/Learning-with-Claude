# Ch 14 — Verification 原則五條

> **目標**：讀懂 pstack 怎麼定義「證據」。五條 Verification 原則——Prove It Works、Fix Root Causes、Sequence Work into Verifiable Units、Test Behavior Not Implementation、Explain the Number——分別回答：什麼算驗證過、修在哪裡算修好、工作要怎麼排順序才能自我證明、什麼樣的測試才有用、一個數字什麼時候可以信。你會跑一個「代理指標說通過、真實產物說失敗」的實驗，以及一個用兩種假想修改來審判三種測試的實驗。

> **環境**：pstack 0.15.36、Node.js 26（`node:test`）。所有程式與輸出都是實際跑出來的。

## 為什麼需要這個？

agent 最常說的一句謊話（通常不是故意的）是「已完成並驗證」。它說這句話時，背後可能是：

- 「程式編譯過了」
- 「測試是綠的」（但測試沒有涵蓋這個改動）
- 「我讀了程式碼，邏輯是對的」
- 「subagent 回報說做完了」
- 「輸出檔案的時間戳記是新的」

這些全都是**代理指標**——和「真的做對了」相關，但不等於。五條 Verification 原則把「證據」的標準拉到真實的產物上。

Part 1 的各個 playbook 已經用過這五條原則很多次（Ch 3 的 golden file、Ch 8 的紅先綠後、Ch 10 的交錯量測）。這一章把它們當成原則本身來讀，補上 playbook 沒有展開的部分。

## 先建立直覺：證據的階梯

```
  弱 ──────────────────────────────────────────────────────────▶ 強

  「我覺得」   「編譯過」   「測試綠」    「我跑了指令    「審查者可以
   讀程式碼     型別檢查    （測試有測到   看到真實輸出」   重跑的腳本，
                          這個行為嗎？）                 先紅後綠」
     │            │            │              │               │
     └────────────┴────────────┘              │               │
          代理指標（proxy）                 Prove It Works   + Build the Lever
                                                           + Sequence Verifiable Units
```

`blast-radius` 技能（Ch 20）把這個階梯寫得更細：「You said so. Worthless on its own」→「You pointed at the line」→「You showed the bad case can't happen」→「You ran it」→「You reproduced it in the running app」。

## 一、Prove It Works

> **索引**：After a task, before declaring done. Verify against the real artifact, not a proxy or "it compiles".

> **Why:** Unverified work has unknown correctness. Indirect verification (file mtimes, output freshness, agent self-reports, cached screenshots) feels cheaper than direct observation. Acting on a wrong inference costs far more than checking the source.

原文點名了四種代理指標：**檔案修改時間、輸出的新鮮度、agent 的自我回報、快取的截圖**。我們用第一種做實驗。

### 實驗：「建置輸出是新的」

一個建置流程從 `src.mjs` 產生 `dist.txt`。驗證方式一：檢查 `dist.txt` 比 `src.mjs` 新（很多建置工具就是這樣判斷要不要重建的）。

```sh
# verify-proxy.sh
if [ dist.txt -nt src.mjs ]; then echo "PASS dist.txt is newer than src.mjs"; else echo "FAIL dist.txt is stale"; exit 1; fi
```

驗證方式二：讀 `dist.txt` 的實際內容，和來源比對。

```sh
# verify-real.sh
want="version=$(node -e 'import("./src.mjs").then(m=>console.log(m.VERSION))')"
got=$(cat dist.txt)
if [ "$got" = "$want" ]; then echo "PASS dist.txt says $got"; else echo "FAIL dist.txt says $got, source says $want"; exit 1; fi
```

情境：從 1.0 建置出 `dist.txt`，然後把來源改成 1.1，**但沒有重新建置**——只是某個流程（例如一次 `git checkout`、一個複製動作、或 agent 自己的 `touch`）更新了 `dist.txt` 的修改時間。

```
PASS dist.txt is newer than src.mjs
FAIL dist.txt says version=1.0, source says version=1.1
```

代理指標說通過，真實產物說失敗。原文的三條做法：

- 「Check process liveness directly, not indirectly through derived state」
- 「Read the actual value, not a cached or derived representation」
- **「When verification fails, suspect the observation method before suspecting the system」**

第三條反方向也成立：**驗證通過得太輕鬆時，也要懷疑觀察方法**（figure-it-out 的原文：「When something passes too easily, suspect the observation method before the system.」）。Ch 10 那份把時間算在錯的函式上的 CPU profile，就是觀察方法本身出錯的例子。

### 「Script the check when you can」

> The strongest proof is a deterministic script that re-runs the same comparison, not a one-time eyeball. Write the script, run it, and keep its output as an artifact a reviewer can re-run instead of trusting your word.

`verify-real.sh` 就是這樣的腳本。它比「我打開 dist.txt 看了一下，是對的」強，因為審查者不需要相信你。

## 二、Fix Root Causes

> **索引**：Debugging. Trace each symptom back to the first place the system went wrong and fix it there. A guard is a fix only when the state it handles is legitimate.

Ch 8 用過這條原則（`parseAmount` 的病因在解析，不在 CLI）。這裡補上它最精細的一條判斷：

> Ask whether the state that triggered the symptom is legitimate. If the system may be in that state, the code that broke on it is the root cause, and handling the state there is the fix. If the state should never exist, a check where it broke hides the bug. Find what produced the state.

```
  某個函式收到 null，崩潰了
            │
   ┌────────┴────────┐
   │ null 是合法的狀態嗎？│
   └────────┬────────┘
     是 ↙         ↘ 否
  （例如：使用者    （例如：一個應該永遠有值的
   可能沒有頭像）    訂單 id）
      │                 │
  崩潰的那個函式       在崩潰處加 if (x == null) return
  就是病因：            是在掩蓋 bug
  在那裡處理 null       → 去找是誰產生了 null
  就是修正
```

這條判斷決定了「加一個 guard」是修正還是掩蓋。原文列出常見的掩蓋手法：「Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide a bug.」練習 A 的「匯出後去重」就是 discarded state 的一種。

其他幾條值得記住的：

- 「When something fails only after a restart, suspect persisted state first: config files, caches, lock files, serialized state.」
- 「Check for the pattern, not just the instance. Find every place with the same fault and fix them all.」
- 「If a workaround needs a paragraph-long comment to justify it, the code is wrong. Fix the code, not the comment.」（這條和 Ch 23 的 Comment Sicko 直接相關）
- 「If the deciding fact took a deep dig to find, propose the cheap, always-on signal that would have shown it at first look.」

## 三、Sequence Work into Verifiable Units

> **索引**：Multi-step work (sweeps, migrations, runs of similar edits) and how you stack commits and PRs. Break work into small units that each end in a check, verify each before the next, and order delivery so the sequence proves itself.

> **Why:** A break caught at the unit that caused it is cheap to localize. A break caught after a batch is buried, and you have already built further on a broken base.

這條原則有兩面：

**執行面**：每個單元是一個「前後括號」——已知良好的狀態、一個改變、跑檢查、再往下。「Rebase onto clean trunk first so every check measures against the real baseline. When a lever does the edits, the per-unit check is nearly free. Run it anyway.」

**交付面**：commit 和 PR 的順序本身是一個論證。「The canonical shape is the failing test first, then the fix on top. Other story orders are a subtraction before the reshape, a baseline capture before the treatment, the scaffold before the feature.」

| 工作類型 | 證明自己的 commit 順序 | 本課的例子 |
|---|---|---|
| Bug fix | 失敗的測試 → 修正 | Ch 8：`c130eeb` → `9991777` |
| Feature | 驗證腳本（對新功能紅）→ 實作 | Ch 3：`09dc1b0` → `8fb58e6` |
| Refactoring | pin → 減法 → 重塑 | Ch 9：`4db14bb` → `944ea23` |
| Perf | baseline 與 harness → 每個被接受的改善 | Ch 10：`9fcbde1` → `9f07468` → `cdacf7f` |

這條原則在 poteto-mode 裡被引用的次數非常多——幾乎每一份建造類的 playbook 都點名它。它的 leaf 名稱也是 24 條裡唯一的例外（`principle-sequence-verifiable-units`，少了 work 和 into），Ch 11 的 cite-audit 要特別處理。

## 四、Test Behavior, Not Implementation

> **索引**：Writing, changing, or keeping a test. Assert what the code's users can observe, so the test fails when that behavior breaks and keeps passing when only the internals change.

這條原則給了一個非常實用的判準：**用兩個假想的修改審判每一個測試**。

> - **Break the behavior.** Make the code return a wrong value, skip an effect, or do the effect twice. If the test still passes, it proves nothing.
> - **Change the internals.** Keep every observable result the same and restructure the code underneath. If the test now fails, it is pinned to the implementation.
>
> Keep a test that catches the first edit and survives the second. Rewrite or delete the rest.

### 實驗：三種測試、兩種修改

一個結帳函式：

```js
// cart.mjs
export const helpers = {
  subtotal(items) { return items.reduce((s, i) => s + i.price * i.qty, 0); },
};
export function checkout(items, payments) {
  const total = helpers.subtotal(items);
  if (total === 0) return { charged: 0 };
  payments.charge(total);
  return { charged: total };
}
```

契約：**空購物車永遠不扣款**；有東西時扣總額。

三種測試：

```js
// tests.mjs（用 IMPL 環境變數切換要測哪一個實作）
import { test } from "node:test";
import assert from "node:assert/strict";
const { checkout, helpers } = await import(process.env.IMPL ?? "./cart.mjs");
const fakePayments = () => { const calls = []; return { calls, charge: (n) => calls.push(n) }; };

test("A pinned: checkout calls helpers.subtotal once", () => {
  let n = 0; const orig = helpers.subtotal;
  helpers.subtotal = (...a) => { n++; return orig(...a); };
  try { checkout([{ price: 5, qty: 2 }], fakePayments()); } finally { helpers.subtotal = orig; }
  assert.equal(n, 1);
});

test("B hollow: checkout returns something", () => {
  assert.ok(checkout([], fakePayments()));
});

test("C behavior: an empty cart never charges, a full cart charges the total", () => {
  const empty = fakePayments();
  assert.deepEqual(checkout([], empty), { charged: 0 });
  assert.deepEqual(empty.calls, []);
  const full = fakePayments();
  assert.deepEqual(checkout([{ price: 5, qty: 2 }, { price: 3, qty: 1 }], full), { charged: 13 });
  assert.deepEqual(full.calls, [13]);
});
```

兩種假想修改：

```js
// break-behavior.mjs：拿掉「空購物車不扣款」的判斷（行為壞了）
export function checkout(items, payments) {
  const total = helpers.subtotal(items);
  payments.charge(total);
  return { charged: total };
}
```

```js
// change-internals.mjs：把 helpers.subtotal 內聯成一個迴圈（行為不變）
export const helpers = {};
export function checkout(items, payments) {
  let total = 0;
  for (const { price, qty } of items) total += price * qty;
  if (total === 0) return { charged: 0 };
  payments.charge(total);
  return { charged: total };
}
```

對三個實作各跑一次：

```bash
for impl in cart break-behavior change-internals; do echo "=== $impl"; IMPL=./$impl.mjs node --test tests.mjs; done
```

```
=== cart
✔ A pinned: checkout calls helpers.subtotal once
✔ B hollow: checkout returns something
✔ C behavior: an empty cart never charges, a full cart charges the total
=== break-behavior
✔ A pinned: checkout calls helpers.subtotal once
✔ B hollow: checkout returns something
✖ C behavior: an empty cart never charges, a full cart charges the total
=== change-internals
✖ A pinned: checkout calls helpers.subtotal once
✔ B hollow: checkout returns something
✔ C behavior: an empty cart never charges, a full cart charges the total
```

（節錄了每個測試的結果行，去掉了執行時間與失敗摘要的重複行。）

| 測試 | 行為壞了時 | 內部改了時 | 判決 |
|---|---|---|---|
| A（釘在實作上） | ✔ 沒抓到 | ✖ 誤報 | **最糟**：該抓的抓不到，不該抓的亂抓 |
| B（空洞的） | ✔ 沒抓到 | ✔ | 刪掉：什麼都證明不了 |
| C（行為） | ✖ 抓到了 | ✔ | **保留** |

測試 A 最值得注意：它看起來很嚴謹（「確認 subtotal 只被呼叫一次」），但它測的是**誰做了工作**，不是**工作的結果**。原文：「A call to an internal helper is implementation, because no user can see it.」

測試 C 的寫法也呼應了原文的一段細節：

> When "an empty cart never charges" is the promise, an assertion that the payment client got no call is behavior. Pair it with a case where the charge happens, so that the test shows it can see a call.

C 同時測了「空的不扣」和「滿的扣 13」。如果只測前一半，一個**永遠不扣款**的實作也會通過——測試要證明它**看得見**扣款，它說「沒有扣款」才有意義。這和 Ch 3「驗證腳本要先看它紅一次」、Ch 6「規則要先看它觸發一次」是同一個道理的第三種形式。

原文列出讓測試「什麼都證明不了」的常見原因，值得逐條對照你的測試：

- assertion 比契約寬鬆
- 期望值是用被測的程式碼算出來的
- 重述一個常數或 prompt 字串，卻沒有執行讀取它的程式碼
- 斷言的資料是測試自己建出來的

## 五、Explain the Number

> **索引**：Apply before you measure, and before you trust, report, or act on a number you measured. Find what limits it, and rule out that it measured something other than the work you think.

這條原則是 2026-10-03 才加入的，Ch 22 會配合 `benchmark-checklist` 整章展開。這裡只看它的骨架：

- **跑之前先寫下決定**：「Name the result that would change a decision, and the noise floor. Stop when the run-to-run spread is larger than the effect.」
- **問「為什麼不是兩倍？」**：「Name the resource or code path that bounds the result ... A guess from reading the code is not a limiter. If you cannot say why the number is not twice as good, you do not know what you measured.」
- **列出這個數字還可能在量什麼，逐一排除**：錯誤、被跳過或被快取的工作、從沒執行的程式碼、沒調校的一方、雜訊、小到端到端無關緊要的部分
- **證據和數字放在一起**：跑了幾次、範圍多大、限制因素是什麼

和 Prove It Works 的界線（原文）：「Distinct from Prove It Works, which checks that an output is real.」Prove It Works 問「輸出是真的嗎」，Explain the Number 問「這個數字代表你以為它代表的東西嗎」。Ch 10 的 H3（memo 讓中位數變成 0）是後者的例子：輸出是真的（`work: 4794, errors: 0`），但數字量到的是快取，不是工作。

## 底層機制：五條原則怎麼互相支撐

```
                     Sequence Work into Verifiable Units
                     （每個單元都要有一個檢查）
                                 │
                       檢查本身要可靠
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
          Prove It Works   Test Behavior     Explain the Number
          檢查真實產物       測試要測行為        數字要解釋得出來
                │                │                │
                └────────────────┼────────────────┘
                                 ▼
                         Fix Root Causes
                  （檢查失敗時，修在第一個做錯的地方）
```

Sequence 要求「每一步都有檢查」；另外三條確保「那個檢查是可信的」；Fix Root Causes 決定「檢查失敗時要修哪裡」。少了任何一條，整個鏈條就有一個弱點：每一步都有檢查但檢查是空洞的（缺 Test Behavior）、檢查看的是代理指標（缺 Prove It Works）、數字很漂亮但量錯了（缺 Explain the Number）。

## 對比與取捨

| 原則 | 回答的問題 | 典型的違反 | 你看得到的證據 |
|---|---|---|---|
| Prove It Works | 輸出是真的嗎？ | 「編譯過了」「檔案是新的」 | 真實指令的輸出、讀回來的值 |
| Fix Root Causes | 修在對的地方嗎？ | 在崩潰處加 guard | 觸發狀態是否合法的判斷 |
| Sequence Verifiable Units | 每一步都驗證了嗎？ | 改完十個檔案才跑一次測試 | 紅先綠後的 commit 順序 |
| Test Behavior | 測試有用嗎？ | 測內部呼叫、斷言 truthy | 兩個假想修改的結果 |
| Explain the Number | 數字可信嗎？ | 單次量測、非交錯、量到快取 | 次數、範圍、限制因素 |

## 踩雷集錦

1. **用修改時間或「檔案存在」當驗證**：本章的實驗裡，`dist.txt` 比來源新，內容卻是舊版本。讀內容，不要讀 metadata。
2. **在崩潰處加 null check 就收工**：先問 null 是不是合法的狀態。不合法的話，你的 guard 讓下一個人更難找到真正的病因。
3. **把「呼叫了某個 helper」當成行為**：本章的測試 A 在行為壞掉時仍然通過，在重構時反而失敗。使用者看不到你內部呼叫了誰。
4. **只測負面案例**：「空購物車不扣款」的測試，如果沒有配一個「滿的購物車會扣款」，一個永遠不扣款的實作也會通過。測試要證明它看得見它聲稱沒看到的東西。
5. **一個 commit 裡同時有測試和修正**：審查者無法確認測試在修正前是紅的。拆成兩個 commit，讓順序自己證明。

## 進階：再往深一層

- **mutation testing**：本章的「兩種假想修改」手動做了兩次。mutation testing 工具（JavaScript 的 [Stryker](https://stryker-mutator.io/)、Python 的 mutmut）會自動對你的程式碼做幾百種小修改（把 `===` 換成 `!==`、刪掉一行……），看你的測試能抓到幾種。抓不到的修改，就是 Test Behavior 說的「proves nothing」的測試覆蓋不到的地方。
- **為「觀察方法」本身寫測試**：Prove It Works 說失敗時先懷疑觀察方法。一個實用的做法是讓驗證腳本在開頭自我檢查：例如 verify-real.sh 可以先確認 `node` 讀得到 `src.mjs`、`dist.txt` 存在且非空，再做比對。這樣一個 FAIL 就不會是「腳本自己壞了」。
- **讀 `blast-radius` 的證據階梯**：它把「你有多確定」分成五級，並要求「For each fact the change's safety depends on, get it as far down this list as is cheap, and say where it stopped」。這是把 Prove It Works 變成可以分級回報的形式（Ch 20）。

## 動手練習

1. 照本章跑 dist.txt 實驗。然後想一個你的專案裡「用 metadata 代替內容」的驗證（例如 CI 檢查 lockfile 有沒有改、檢查某個檔案存不存在），把它改成檢查內容。
2. 拿你專案裡的三個測試，對每一個做本章的兩種假想修改（可以真的改程式碼跑一次，再 revert）。哪一個是 A 類（釘在實作上）？哪一個是 B 類（空洞）？
3. 找一個你最近加的 `if (x == null) return`。回答：x 為 null 是合法的狀態嗎？如果不是，是誰產生了 null？

## 本章重點整理

- Prove It Works：檢查真實產物，不是修改時間、自我回報、快取截圖；驗證失敗或通過得太輕鬆時，先懷疑觀察方法
- Fix Root Causes：觸發症狀的狀態合法時，處理它就是修正；不合法時，guard 是在掩蓋，要去找誰產生了它
- Sequence Work into Verifiable Units：每個單元結束在一個檢查；commit 順序本身是論證
- Test Behavior：用「行為壞了」「內部改了」兩個假想修改審判每個測試；保留抓得到前者、撐得過後者的
- Explain the Number：跑之前先寫下會改變決定的結果；說得出為什麼不是兩倍；排除其他可能在量的東西

## 自我檢核

- [ ] 不看本章，能說出 Prove It Works 原文點名的四種代理指標嗎？
- [ ] 用 Fix Root Causes 的「合法狀態」判斷，分析練習 A 的「匯出後去重」為什麼是掩蓋
- [ ] 本章的測試 A 為什麼比測試 B 更糟？（提示：B 只是沒用，A 會主動誤導）
- [ ] 你團隊的 CI 裡，有沒有一個檢查是「通過得太輕鬆」的？你會怎麼驗證它真的會失敗？

## 延伸閱讀

### 原始碼

- **五條 leaf 檔**：[prove-it-works](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-prove-it-works/SKILL.md)、[fix-root-causes](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-fix-root-causes/SKILL.md)、[sequence-verifiable-units](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-sequence-verifiable-units/SKILL.md)、[test-behavior-not-implementation](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-test-behavior-not-implementation/SKILL.md)、[explain-the-number](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-explain-the-number/SKILL.md)
  - **讀哪裡**：test-behavior-not-implementation 全文（它是 24 條裡最長的之一，2.6KB，每一句都值得讀）

### 文章

- **[Kent C. Dodds — Testing Implementation Details](https://kentcdodds.com/blog/testing-implementation-details)**
  - **這篇說什麼**：用 React 元件的例子說明「測實作細節」的兩種代價：false negative（重構時誤報）與 false positive（壞了卻沒抓到）
  - **和本章的關聯**：本章測試 A 的兩種失敗，在這篇有完整的前端版本
- **[Martin Fowler — Mocks Aren't Stubs](https://martinfowler.com/articles/mocksArentStubs.html)**
  - **這篇說什麼**：classical 與 mockist 兩種測試風格的差別
  - **和本章的關聯**：Test Behavior 原文說「The payload that a mocked external service receives is behavior, because that service observes it」——這正是這篇討論的界線：mock 外部協作者是合理的，mock 內部 helper 是釘在實作上

### 書籍

- **《Unit Testing: Principles, Practices, and Patterns》** — Vladimir Khorikov（Manning, 2020）
  - **這本書的定位**：把「測行為不測實作」系統化的一本書
  - **讀哪幾章**：Chapter 4「The four pillars of a good unit test」（protection against regressions、resistance to refactoring——正好是本章兩個假想修改所測的兩個性質）

### 工具

- **[Stryker Mutator](https://stryker-mutator.io/)**
  - **這是什麼**：JavaScript / TypeScript 的 mutation testing 工具
  - **和本章的關聯**：把「兩種假想修改」自動化成幾百種

Verification 原則定義了什麼是證據。最後一組原則管的是「誰來做」與「什麼時候不用問人」。

→ [Ch 15 Delegation、Meta 與 Autonomy](./15-delegation-meta-autonomy.md)
