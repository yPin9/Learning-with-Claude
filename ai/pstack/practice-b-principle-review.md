# 練習 B — 用原則審一個 PR

> **目標**：把 Ch 11–15 的 24 條原則當成審查詞彙用一次。你會拿到一個「看起來合理、測試全綠」的 agent PR，任務是寫出一份審查：每個發現都要點名一條原則、指出具體的行、說明它會造成什麼錯，**而且要用一支能跑的腳本或測試證明它**——沒有證明的發現不算數（這是 Ch 20 的 `interrogate` 會用的同一條規則）。最後你要指出這個 PR 沒有修到的病因，並給出方向。

> **環境**：Node.js 20 以上（本練習在 Node.js 26 驗證）、git。所有參考解答的輸出都是實際跑出來的。

## 背景與動機

一個 agent 收到這份 bug 報告：「使用者在推播 job 重試後收到兩次同樣的通知」。它開了一個 PR，PR 說明寫得有條有理，測試全部通過，還附了一句「Benchmarked: 2x faster than before」。

你是 reviewer。這種 PR 在 agent 大量產出的時代非常典型：**每一行單獨看都說得通**，問題藏在行與行之間、藏在它沒有測的情境裡。用「我覺得這裡怪怪的」審它，你會被 agent 一一反駁；用原則名稱加上一個能跑的證明審它，每個發現都站得住。

## 起始程式碼

建一個 repo，`main` 分支上是原始版本：

```js
// notify.mjs（main）
export async function sendAll(users, transport) {
  const sent = [];
  for (const user of users) {
    await transport.send(user.id, `hello ${user.name}`);
    user.status = "sent";
    sent.push(user.id);
  }
  return sent;
}
```

```js
// test/notify.test.mjs（main）
import { test } from "node:test";
import assert from "node:assert/strict";
import { sendAll } from "../notify.mjs";

test("sends one message per user", async () => {
  const calls = [];
  const transport = { send: async (id) => calls.push(id) };
  await sendAll([{ id: 1, name: "a" }, { id: 2, name: "b" }], transport);
  assert.deepEqual(calls, [1, 2]);
});
```

開一個分支 `agent/fix-duplicates`，套上 agent 的 PR：

```diff
--- a/notify.mjs
+++ b/notify.mjs
@@ -1,9 +1,30 @@
+export const helpers = {
+  // Phase 1: dedupe users
+  dedupe(users) {
+    const seen = new Set();
+    return users.filter((u) => (seen.has(u.id) ? false : (seen.add(u.id), true)));
+  },
+};
+
+// Kept for compatibility with existing callers.
 export async function sendAll(users, transport) {
+  return sendNotifications(users, transport);
+}
+
+export async function sendNotifications(users, transport) {
   const sent = [];
-  for (const user of users) {
-    await transport.send(user.id, `hello ${user.name}`);
-    user.status = "sent";
-    sent.push(user.id);
+  // Phase 2: send
+  for (const user of helpers.dedupe(users)) {
+    if (!user || !user.id) continue;
+    if (user.isSent) continue;
+    try {
+      await transport.send(user.id, `hello ${user.name}`);
+      user.status = "sent";
+      user.isSent = true;
+      sent.push(user.id);
+    } catch (err) {
+      // ignore transient errors so the batch keeps going
+    }
   }
   return sent;
 }
--- a/test/notify.test.mjs
+++ b/test/notify.test.mjs
-import { sendAll } from "../notify.mjs";
+import { sendAll, sendNotifications, helpers } from "../notify.mjs";
 ...
+
+test("uses dedupe", async () => {
+  let called = false; const orig = helpers.dedupe;
+  helpers.dedupe = (u) => { called = true; return orig(u); };
+  await sendNotifications([{ id: 1, name: "a" }], { send: async () => {} });
+  helpers.dedupe = orig;
+  assert.ok(called);
+});
```

PR 說明：

```markdown
# fix: prevent duplicate notifications after retry

Users reported getting the same notification twice after the job retried.

## What changed
- Added `sendNotifications` with deduplication and an `isSent` flag.
- Wrapped sends in try/catch so one failure does not stop the batch.
- Kept `sendAll` for compatibility.

## Verification
- All tests pass.
- Benchmarked: 2x faster than before.
```

在 PR 分支上跑測試：

```
ℹ pass 2
ℹ fail 0
```

**背景資訊**（reviewer 知道、agent 的 PR 沒提到的）：這個 job 每次執行（包括重試）都會從資料庫**重新載入**使用者；`status` 欄位目前沒有寫回資料庫；`sendAll` 只有這個 job 一個呼叫者，沒有外部使用者。

## 任務規格

寫一份審查，至少包含：

| 項目 | 要求 |
|---|---|
| 發現 | 至少 6 個；每個有：原則名稱、檔案與行、它會造成什麼錯 |
| 證明 | 每個發現附一支能跑的腳本或測試，以及它實際印出的輸出；不能跑的發現（例如效能宣稱）寫出你要求 PR 作者補上的證據 |
| 病因 | 指出這個 PR 沒修到的真正病因，用 Fix Root Causes 的「合法狀態」判斷說明 |
| 修正方向 | 一個修正方向，附至少一個能區分「PR 版本」與「正確版本」的測試 |
| 分級 | 每個發現標 blocking 或 non-blocking，並說明理由 |

**規則**：「我覺得」「可能會」「看起來」開頭的發現，沒有附證明就不算。每個發現都要能被 PR 作者自己重跑。

## 如果你卡住了

1. **先別看 diff，先模擬真實情境**：寫一支腳本模擬「job 執行、某個使用者的推播失敗一次、job 重試」，而且每次重試都**重新載入**使用者（照背景資訊）。在 `main` 和 PR 分支上各跑一次，數每個使用者收到幾次通知。結果會讓你嚇一跳。
2. **`try/catch` 吞掉錯誤，對「重試」這件事有什麼影響？** job 是靠什麼知道要重試的？
3. **`if (!user || !user.id) continue;`**：哪些 `id` 值會讓 `!user.id` 成立？在 JavaScript 裡，哪個合法的數字是 falsy 的？
4. **`status` 和 `isSent`**：兩個欄位表達同一件事時，誰保證它們同步？試試看一個 `status: "sent"`、沒有 `isSent` 的使用者會怎樣。
5. **「uses dedupe」測試**：用 Ch 14 的兩個假想修改審它——把 `dedupe` 改成什麼都不做，測試會失敗嗎？

## 實作步驟建議

### Step 1：建 repo、重現原始 bug

在 `main` 上寫重試模擬腳本，確認原始 bug（重試後重複）真的存在。

### Step 2：在 PR 分支上跑同一支腳本

比較兩個分支的結果。寫下你看到的現象，以及它和 PR 說明的「fix duplicates」之間的落差。

### Step 3：逐行審 diff，每一個可疑點寫一個證明

不要只寫評論。每個可疑點寫一段能跑的程式（node -e、一個小測試），證明它的後果。

### Step 4：判斷病因

用 Fix Root Causes 問：「觸發症狀的狀態合法嗎？」——重試是合法的狀態嗎？重試時系統知道誰已經收到了嗎？

### Step 5：寫修正方向與區分測試

寫一個在 PR 分支上失敗、在你的修正上通過的測試。

### Step 6：整理成審查

依任務規格的表格整理，標出 blocking / non-blocking。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考審查</summary>

### 證明 0：重試模擬

```js
// retry-sim.mjs：模擬 job 重試；每次重試都從 db 重新載入使用者；使用者 2 的推播第一次會失敗
const { sendAll } = await import(`${process.cwd()}/notify.mjs`);
const db = [{ id: 1, name: "a" }, { id: 2, name: "b" }, { id: 3, name: "c" }];
const delivered = [];
let failures = 1;
const transport = {
  async send(id) {
    if (id === 2 && failures > 0) { failures--; throw new Error("503 from push provider"); }
    delivered.push(id);
  },
};
for (let attempt = 1; attempt <= 2; attempt++) {
  const users = db.map((u) => ({ ...u }));
  try { await sendAll(users, transport); console.log(`attempt ${attempt}: ok`); break; }
  catch (e) { console.log(`attempt ${attempt}: failed (${e.message}), job will retry`); }
}
const counts = Object.fromEntries(db.map((u) => [u.id, delivered.filter((d) => d === u.id).length]));
console.log("deliveries per user:", JSON.stringify(counts));
```

```
=== main
attempt 1: failed (503 from push provider), job will retry
attempt 2: ok
deliveries per user: {"1":2,"2":1,"3":1}
=== PR branch
attempt 1: ok
deliveries per user: {"1":1,"2":0,"3":1}
```

`main` 重現了回報的 bug：使用者 1 收到兩次。PR 分支上重複消失了——**但使用者 2 一次都沒收到**，而且 job 回報成功。

### 發現 1（blocking）：吞掉錯誤讓症狀消失，同時讓 bug 變得更嚴重

- **原則**：Fix Root Causes（「Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide a bug.」）
- **位置**：`notify.mjs` 的 `try { ... } catch (err) { /* ignore */ }`
- **後果**：推播失敗不再拋出錯誤，job 不再失敗，**也就不再重試**。重複消失的原因不是修好了，而是觸發重複的那個重試不見了。代價是失敗的使用者永遠收不到通知，而且沒有任何紀錄。
- **證明**：證明 0 的 PR 分支輸出 `{"1":1,"2":0,"3":1}`。

這是整份 PR 最嚴重的問題：它把一個「多送一次」的 bug，換成一個「靜默地少送」的 bug。後者更難被發現——沒有使用者會回報「我沒收到一個我不知道存在的通知」。

### 發現 2（blocking）：`!user.id` 跳過 id 為 0 的使用者

- **原則**：Boundary Discipline（驗證放在邊界，不在內部到處 guard）；Fix Root Causes（guard 只在處理的狀態合法時才是修正）
- **位置**：`if (!user || !user.id) continue;`
- **後果**：`0` 在 JavaScript 是 falsy。id 為 0 的使用者被靜默跳過。如果 null 使用者或沒有 id 的使用者是**不合法**的狀態，它應該在載入的邊界被拒絕（並報錯），而不是在送通知的迴圈裡被悄悄丟掉。
- **證明**：

```
F3 user with id 0 -> sends: [5]
```

（輸入是 `[{ id: 0 }, { id: 5 }]`，只送了 5。）

### 發現 3（blocking）：`isSent` 與 `status` 是兩個必須同步的欄位

- **原則**：Model the Domain（「The sign that you skipped this is ... a second boolean that must stay in sync with the first.」）
- **位置**：`user.status = "sent"; user.isSent = true;` 與 `if (user.isSent) continue;`
- **後果**：跳過的判斷只看 `isSent`，但其他地方（以及資料庫裡既有的資料）用的是 `status`。一個 `status: "sent"`、沒有 `isSent` 的使用者會被重送。
- **證明**：

```
F2 status=sent but isSent missing -> sends: [1]
```

### 發現 4（non-blocking，但要改）：「uses dedupe」測試釘在實作上

- **原則**：Test Behavior, Not Implementation
- **位置**：`test("uses dedupe", ...)`
- **後果**：它測的是「呼叫了 `helpers.dedupe`」，不是「重複的輸入只送一次」。把 `dedupe` 改成直接回傳輸入（行為壞了），測試照樣通過；把去重內聯進迴圈（內部改了、行為沒變），測試反而會失敗。
- **證明**（把 `dedupe` 的 body 改成 `return users;` 後）：

```
✔ sends one message per user
✔ uses dedupe
duplicate input now sends: [1,1]
```

### 發現 5（non-blocking）：一個呼叫者的相容層

- **原則**：Migrate Callers Then Delete Legacy APIs；Minimize Reader Load（「wrappers with one caller」）
- **位置**：`// Kept for compatibility with existing callers.` 與 `sendAll` → `sendNotifications`
- **後果**：`sendAll` 只有一個呼叫者、沒有外部使用者（背景資訊）。保留它讓讀者多追一層，而且「相容」這個詞暗示有一個不存在的外部契約。
- **證明**：`grep -rn "sendAll\|sendNotifications" --include='*.mjs' .` 列出所有呼叫端；在這個 repo 裡只有測試和 job。要求：要嘛遷移呼叫端並刪除一個名字，要嘛說明誰是那個「existing caller」。

### 發現 6（non-blocking）：敘述性的註解

- **原則**：poteto-mode 的 Comments 規則（「A verify or test script gets no phase-narrating comments such as `// Phase 1: add cards`」）；Ch 23 的 `/no-comments`
- **位置**：`// Phase 1: dedupe users`、`// Phase 2: send`、`// ignore transient errors so the batch keeps going`
- **後果**：前兩個重述程式碼；第三個是一段為 workaround 辯護的註解——Fix Root Causes：「If a workaround needs a paragraph-long comment to justify it, the code is wrong.」這一行註解本身就是發現 1 的線索。
- **證明**：這一類不需要執行；刪掉三行註解，程式碼的意思沒有任何損失。

### 發現 7（blocking，直到補上證據）：沒有解釋的效能數字

- **原則**：Explain the Number；benchmark-checklist
- **位置**：PR 說明的「Benchmarked: 2x faster than before」
- **後果**：沒有跑了幾次、範圍、量的是什麼、限制因素是什麼。而且這個 PR 的目的是修正確性，不是效能——兩倍快是從哪裡來的？最可能的解釋是：吞掉錯誤之後，失敗的推播不再觸發重試，所以 job 「跑得比較快」。如果是這樣，這個數字量到的正是 bug（發現 1）。
- **證明**：不能替作者跑。要求補上：量測腳本、交錯量測的次數與範圍、限制因素；或刪掉這一行。

### 病因

用 Fix Root Causes 的判斷：**觸發症狀的狀態（重試）是合法的**——推播服務回 503 是正常會發生的事，job 重試是正確的反應。所以病因不在「重試」，而在**重試時系統不知道誰已經收到了**：`status = "sent"` 只寫在記憶體裡的物件上，每次重試重新載入使用者時，這個資訊就不見了。

這是 Make Operations Idempotent 的違反：「derive each step from the state that exists, not from what the previous run meant to do.」第二次執行無法從現存的狀態推出「使用者 1 已經送過了」，因為那個狀態從來沒有被寫下來。

PR 的 `isSent` 也只寫在記憶體裡，所以就算拿掉 try/catch，它也修不好跨重試的重複（每次重試都是新的物件）。dedupe 只處理同一次呼叫內的重複輸入，和回報的 bug 無關——它是「也許有幫助」的東西（Bug fix playbook：「Belt-and-suspenders that 'might help' is a hypothesis, not a fix. It does not ship.」）。

### 修正方向

把「已送出」持久化，並且在每次執行時從持久化的狀態推出要跳過誰：

```js
// notify.mjs（修正方向）
export async function sendAll(users, transport, { markSent }) {
  const sent = [];
  for (const user of users) {
    if (user.status === "sent") continue;
    await transport.send(user.id, `hello ${user.name}`);
    await markSent(user.id);
    sent.push(user.id);
  }
  return sent;
}
```

區分測試（用一個 fake store 模擬資料庫）：

```js
// test/notify.test.mjs（修正方向）
import { test } from "node:test";
import assert from "node:assert/strict";
import { sendAll } from "../notify.mjs";

const fakeStore = (users) => {
  const db = new Map(users.map((u) => [u.id, { ...u }]));
  return { load: () => [...db.values()].map((u) => ({ ...u })), markSent: async (id) => { db.get(id).status = "sent"; } };
};

test("sends one message per user", async () => {
  const calls = [];
  const store = fakeStore([{ id: 1, name: "a" }, { id: 2, name: "b" }]);
  await sendAll(store.load(), { send: async (id) => calls.push(id) }, store);
  assert.deepEqual(calls, [1, 2]);
});

test("a retry after a failure delivers every user exactly once", async () => {
  const delivered = [];
  let failures = 1;
  const transport = { send: async (id) => { if (id === 2 && failures-- > 0) throw new Error("503"); delivered.push(id); } };
  const store = fakeStore([{ id: 1, name: "a" }, { id: 2, name: "b" }, { id: 3, name: "c" }]);
  await assert.rejects(sendAll(store.load(), transport, store), /503/);
  await sendAll(store.load(), transport, store);
  assert.deepEqual(delivered, [1, 2, 3]);
});

test("a user with id 0 is still notified", async () => {
  const calls = [];
  const store = fakeStore([{ id: 0, name: "zero" }]);
  await sendAll(store.load(), { send: async (id) => calls.push(id) }, store);
  assert.deepEqual(calls, [0]);
});
```

在修正方向上：

```
✔ sends one message per user
✔ a retry after a failure delivers every user exactly once
✔ a user with id 0 is still notified
ℹ pass 3
ℹ fail 0
```

同一份測試在 agent 的 PR 分支上：

```
✔ sends one message per user
✖ a retry after a failure delivers every user exactly once
✖ a user with id 0 is still notified
ℹ pass 1
ℹ fail 2
```

**這個修正方向還有一個剩下的風險**（推論，沒有測）：如果程序在 `transport.send` 成功之後、`markSent` 之前崩潰，重試時這個使用者會被再送一次。這個方向把語意從「重試必定重複」改進到「只在一個很窄的崩潰視窗內可能重複」（at-least-once）。要做到真正的 exactly-once，需要推播服務支援冪等鍵（idempotency key，例如以 `notification:<userId>:<campaignId>` 當鍵，讓服務端自己去重），或用 outbox pattern。這一點應該寫進審查的「沒涵蓋的風險」，讓作者決定是否在這個 PR 處理。

### 審查摘要（可以直接貼到 PR 上）

> 這個 PR 讓重複消失的方式，是吞掉推播失敗的錯誤，使得 job 不再重試。結果是失敗的使用者永遠收不到通知，而 job 回報成功（重試模擬：PR 分支上使用者 2 收到 0 次，`main` 上使用者 1 收到 2 次，腳本附上）。
>
> 病因是 `status = "sent"` 沒有被持久化，重試時重新載入的使用者不知道誰已經送過。`isSent` 同樣只在記憶體裡，修不到跨重試的重複；`dedupe` 處理的是同一次呼叫內的重複輸入，和回報的 bug 無關。
>
> Blocking：吞掉錯誤（Fix Root Causes）、`!user.id` 跳過 id 0（Boundary Discipline）、`isSent` 與 `status` 不同步（Model the Domain）、「2x faster」沒有量測證據（Explain the Number）。Non-blocking：「uses dedupe」測試釘在實作上（Test Behavior）、單一呼叫者的相容層（Migrate Callers Then Delete）、敘述性註解。
>
> 建議方向：持久化已送出的狀態，每次執行從持久化的狀態推出要跳過誰。附三個測試，PR 分支上兩個失敗、修正方向上全部通過。

</details>

## 測試用例

你的審查至少要能證明以下現象（每一條都應該有一支腳本或測試）：

| 現象 | 在 PR 分支上的實際結果 | 在正確版本上的預期 |
|---|---|---|
| 使用者 2 第一次推播失敗、job 重試 | 使用者 2 收到 0 次，job 回報成功 | 每個使用者剛好 1 次 |
| id 為 0 的使用者 | 被跳過 | 收到通知 |
| `status: "sent"` 但沒有 `isSent` | 被重送 | 被跳過 |
| `dedupe` 被改成不做事 | 「uses dedupe」測試仍通過 | 行為測試應該失敗 |
| 原始的「每人一則」測試 | 通過 | 通過（它從來沒有測到重試的情境） |

## 延伸挑戰（加分）

- **用 cite-audit 的思路審 PR 說明**：PR 說明裡每一個宣稱（「fix duplicates」「one failure does not stop the batch」「2x faster」），各自的證據是什麼？寫一支小工具，從 PR 說明抽出每個宣稱，標出它在 diff 或測試裡有沒有對應的證據。
- **實作冪等鍵**：假設推播服務支援一個 `idempotencyKey` 參數，同一個鍵在 24 小時內只會送一次。改寫修正方向使用它，並寫一個測試證明「`send` 成功後、`markSent` 前崩潰」也不會重複。
- **交給 agent 審，再審它的審查**：在這個 repo 上跑 `/pstack:interrogate the agent/fix-duplicates branch. intent: users must get each notification exactly once, even across retries.`，比較它的發現和你的。它有沒有找到吞錯誤的問題？它有沒有附上能跑的證明？

## 自我檢核

- [ ] 你的每一個發現都附了一個能跑的證明，或明確寫出你要求作者補的證據
- [ ] 你能解釋為什麼吞掉錯誤會讓「重複」消失，以及為什麼這比原始的 bug 更糟
- [ ] 你能用 Fix Root Causes 的「合法狀態」判斷，說明病因是「狀態沒有持久化」而不是「發生了重試」
- [ ] 你的區分測試在 PR 分支上失敗、在你的修正方向上通過
- [ ] 能說出自己的審查和參考解答的差異，並解釋各自取捨

Part 2 結束。你已經能用原則詞彙判斷一份工作好不好；接下來看 pstack 怎麼把工作分給多個 agent、多個模型，並讓它們互相檢查。

→ [Ch 16 Subagent 紀律](./16-subagent-discipline.md)
