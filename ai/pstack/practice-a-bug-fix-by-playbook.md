# 練習 A — 照 Bug fix playbook 手動修一個真 bug

> **目標**：把 Ch 5–8 學到的東西拼起來：拿一份真實的 bug 報告，**不靠 agent、自己一個人**，一步不漏地照 Bug fix playbook 的六步做完——重現、二分搜尋病因、規劃修正、同介面驗證、紅先綠後的 commit、寫出符合 poteto-mode 規範的回覆。做完之後你會知道每一步「卡在哪裡」，之後審查 agent 跑同一份 playbook 時，你就知道該看哪裡。

> **環境**：Node.js 20 以上（本練習在 Node.js 26 驗證）、git。不需要安裝任何套件。所有參考解答的輸出都是實際跑出來的。

## 背景與動機

這個 bug 直接取自 pstack 官方指南的範例 prompt：

```text
/poteto-mode the export writes duplicate rows when a retry lands mid-run. repro first, then fix and verify.
```

這是一類很常見、也很容易修錯的 bug：**一個不冪等（non-idempotent）的操作碰上重試**。它常見的錯誤修法有好幾種，每一種都能讓「症狀」消失。這個練習的真正考題，是你能不能證明你修的是病因。

為什麼要手動做？因為你之後要**審** agent 做的版本。你自己走過一次「先寫重現腳本」「列假設」「挑能淘汰最多假設的實驗」，你才會在 agent 的 todo 上看到 `skip: <reason>` 時，判斷那個理由合不合理。

## 起始程式碼

建一個資料夾 `ledger-export`，`git init`，放入以下三個檔案，commit 一次當作起點。

```js
// src/file-sink.mjs
// 一個寫 CSV 的 sink。failAtBatch / failAfterRows 用來模擬「寫到一半連線斷掉」。
import fs from "node:fs";

export function fileSink(path, { failAtBatch = 0, failAfterRows = 0 } = {}) {
  let batch = 0;
  let failed = false;
  fs.writeFileSync(path, "");
  return {
    writeBatch(rows) {
      batch += 1;
      rows.forEach((row, i) => {
        if (!failed && batch === failAtBatch && i === failAfterRows) {
          failed = true;
          throw new Error(`sink: connection reset in batch ${batch}`);
        }
        fs.appendFileSync(path, `${row.id},${row.amount}\n`);
      });
    },
    path,
    count() {
      const text = fs.readFileSync(path, "utf8");
      return text === "" ? 0 : text.trimEnd().split("\n").length;
    },
  };
}
```

```js
// src/export.mjs
export function exportRows(rows, { sink, batchSize = 500, retries = 3, log = () => {} }) {
  for (let start = 0; start < rows.length; start += batchSize) {
    const batch = rows.slice(start, start + batchSize);
    for (let attempt = 1; ; attempt++) {
      try {
        sink.writeBatch(batch);
        break;
      } catch (err) {
        log(`batch at ${start} failed (attempt ${attempt}): ${err.message}`);
        if (attempt === retries) throw err;
      }
    }
  }
}
```

```js
// bin/export.mjs
import { exportRows } from "../src/export.mjs";
import { fileSink } from "../src/file-sink.mjs";

const [out = "out.csv", n = "10"] = process.argv.slice(2);
const rows = Array.from({ length: Number(n) }, (_, i) => ({ id: i + 1, amount: (i + 1) * 100 }));
const sink = fileSink(out, {
  failAtBatch: Number(process.env.FAIL_AT_BATCH ?? 0),
  failAfterRows: Number(process.env.FAIL_AFTER_ROWS ?? 0),
});
exportRows(rows, { sink, batchSize: Number(process.env.BATCH ?? 4), log: (m) => console.error(m) });
console.log(`exported ${rows.length} rows, file has ${sink.count()} lines`);
```

**bug 報告**（使用者原文）：「昨天匯出 job 中途遇到一次連線重置，job 有自動重試、最後顯示成功，但匯出的檔案裡有幾筆重複的資料，對帳對不起來。」

## 任務規格

照 Bug fix playbook 的六步完成以下交付物：

| 交付物 | 驗收標準 |
|---|---|
| `repro.sh`（放在 repo **外**） | 在 bug 存在時印 `FAIL ...` 並 exit 1；修好後印 `PASS ...` 並 exit 0；在 CLI 這個「使用者看到 bug 的介面」上跑 |
| 假設清單 | 至少 3 個候選病因，每個寫出「什麼觀察能淘汰它」，並標出你實際做了哪個實驗、結果是什麼 |
| `test/export.test.mjs` | 用 `node:test`；涵蓋下方「測試用例」表的所有情況；在修正前**因為正確的理由**失敗 |
| 兩個有順序的 commit | 第一個只加測試（紅），第二個只改 `src/`（綠）；`git log --oneline` 顯示這個順序 |
| 修正 | 修在病因所在的層；不准在匯出結束後去重；不准把 `retries` 改成 1 |
| 最後的回覆 | 開頭重述目標；寫出壞掉的、病因、修正、驗證；點名至少一條原則以及它改變了哪個決定；每個斷言標「實測 / 推論」；貼原樣的紅與綠輸出 |

**禁止**：修改 `src/file-sink.mjs` 的故障注入邏輯（它代表真實世界的不可靠網路，你不能讓網路變可靠）。

## 期望輸出範例

修正前：

```
$ FAIL_AT_BATCH=2 FAIL_AFTER_ROWS=2 node bin/export.mjs /tmp/out.csv 10
batch at 4 failed (attempt 1): sink: connection reset in batch 2
exported 10 rows, file has 12 lines
$ cut -d, -f1 /tmp/out.csv | tr '\n' ' '
1 2 3 4 5 6 5 6 7 8 9 10
```

修正後：

```
$ FAIL_AT_BATCH=2 FAIL_AFTER_ROWS=2 node bin/export.mjs /tmp/out.csv 10
batch at 4 failed (attempt 1): sink: connection reset in batch 2
exported 10 rows, file has 10 lines
$ cut -d, -f1 /tmp/out.csv | tr '\n' ' '
1 2 3 4 5 6 7 8 9 10
```

注意修正後**仍然會印出失敗訊息**——連線還是會斷，重試還是會發生，只是重試不再產生重複。

## 測試用例

批次大小一律用 4，資料是 `id` 1 到 n。

| 情況 | 輸入 | 預期 | 為什麼要測這個 |
|---|---|---|---|
| 沒有失敗 | n=10 | id 1..10 各一次，依序 | 基準線 |
| 批次中間失敗 | n=10，第 2 批第 2 列前失敗 | 1..10 各一次 | 回報的 bug 本身 |
| 批次第一列前失敗 | n=10，第 2 批第 0 列前失敗 | 1..10 各一次 | 邊界：失敗前什麼都沒寫 |
| 批次最後一列前失敗 | n=10，第 1 批第 3 列前失敗 | 1..10 各一次 | 邊界：失敗前寫了 batchSize−1 列 |
| 最後一個短批次裡失敗 | n=10，第 3 批（只有 2 列）第 1 列前失敗 | 1..10 各一次 | 邊界：最後一批不滿 |
| 空輸入 | n=0 | 檔案為空 | 邊界 |
| sink 一直失敗 | 每次 writeBatch 都 throw | 錯誤在重試預算用完後拋出 | 重試不能吞掉真正的失敗 |
| 重跑一次已完成的匯出 | 同一個 sink 再呼叫一次 `exportRows`（不清空檔案） | 依你的設計而定：寫下你期望的行為並測它 | 冪等性的另一面：「跑兩次」會怎樣？本練習的參考解答用 `base` 記住起始行數，所以會再寫一份——這是不是你要的？ |
| 兩筆合法的相同資料 | `[1,100]、[2,50]、[2,50]、[3,70]、[4,10]`，批次 2，第 2 批第 0 列前失敗 | id 為 1,2,2,3,4 | 擋住「去重」這種症狀修正 |

## 如果你卡住了

1. **重現腳本要檢查什麼？** 不只是「行數等於 10」。想一想：如果某個錯誤的修正剛好讓行數是 10、但順序亂了或少了一筆又多了一筆呢？比對完整的 id 序列。
2. **假設怎麼列？** 從「重複的資料是從哪裡來的」往回想：是 sink 寫了兩次？是 exporter 送了兩次？是 CLI 呼叫了兩次？哪個觀察能一次淘汰最多？（提示：在 exporter 的 catch 裡印出「這次重試要送哪些 id」。）
3. **「批次第一列前失敗」那個測試在修正前通過了，正常嗎？** 正常，而且很重要：它告訴你 bug 只在「失敗前已經寫了一部分」時出現。這本身就是一個淘汰假設的證據。
4. **修在哪一層？** 問 Make Operations Idempotent 原則的三個問題：這個操作跑兩次會怎樣？上一次在任意一點崩潰會怎樣？重跑會收斂到同一個結果嗎？然後問：「重試的時候，exporter 知道 sink 已經寫了多少嗎？它能問嗎？」
5. **去重為什麼不行？** 看最後一個測試用例。帳本裡可以有兩筆金額、id 都相同的合法資料（例如同一張單被拆成兩筆相同的付款）。去重會把合法資料刪掉。這就是 Fix Root Causes 說的：「Ask whether the state that triggered the symptom is legitimate.」

## 實作步驟建議

### Step 1：重現（playbook 第 1 步）

在使用者用的介面（CLI）上重現，做成一支 pass/fail 的腳本，放在 repo 外面。先跑一次確認它是紅的。

### Step 2：二分搜尋病因（playbook 第 2 步）

寫下至少 3 個假設。挑一個能淘汰最多假設的實驗，加 instrumentation 跑，記下結果。確認存活的**機制**（不只是「哪個檔案」，而是「哪一行在什麼條件下做了什麼」）。

### Step 3：寫失敗的測試並 commit（playbook 第 5 步的前半）

依「測試用例」表寫 `test/export.test.mjs`。跑一次，確認失敗的那幾個**是因為重複的 id 而失敗**（讀 assertion 的 actual / expected），不是因為 import 錯誤。單獨 commit。

### Step 4：規劃並實作修正（playbook 第 3 步）

判斷修正是否跨函式邊界（是的話要先 architect；這個練習的修正可以只在 `exportRows` 內）。實作。

### Step 5：同介面驗證並 commit（playbook 第 4、5 步）

先跑測試，再回到 CLI 跑 `repro.sh`。兩個都綠才算。單獨 commit。

### Step 6：寫回覆（playbook 的 Reply）

照任務規格的要求寫。貼**原樣**的紅與綠輸出。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### repro.sh

```sh
#!/bin/sh
# 放在 ledger-export 的上一層：../repro.sh
FAIL_AT_BATCH=2 FAIL_AFTER_ROWS=2 node bin/export.mjs /tmp/out.csv 10 2>/dev/null >/dev/null
got=$(cut -d, -f1 /tmp/out.csv | tr '\n' ' ')
want="1 2 3 4 5 6 7 8 9 10 "
if [ "$got" = "$want" ]; then echo "PASS ids=$got"; exit 0; else echo "FAIL ids=$got want=$want"; exit 1; fi
```

修正前：

```
FAIL ids=1 2 3 4 5 6 5 6 7 8 9 10  want=1 2 3 4 5 6 7 8 9 10
```

### 假設清單與實驗

| 假設 | 什麼觀察能淘汰它 | 結果 |
|---|---|---|
| H1：CLI 或呼叫端把 `exportRows` 呼叫了兩次 | 在 `exportRows` 開頭印一行；只印一次就淘汰 | 只印一次，淘汰 |
| H2：sink 的 `writeBatch` 內部重複寫了某一列 | 無故障注入時檔案正確（10 行）就淘汰 | 無故障時 10 行，淘汰 |
| H3：重試時 exporter 把**整個批次**重送，但失敗前已經有部分列寫進去了 | 在 catch 裡印出重送的 id，並對照失敗當下檔案已有的行數 | 重送 `[5,6,7,8]`，失敗時檔案已有 6 行（1–6），**存活** |
| H4：`count()` 算錯行數 | 手動 `wc -l` 對照 | 一致，淘汰 |

最能切的實驗是 H3 那個：包一層 sink，在每次 `writeBatch` 與 catch 時印出「要送的 id」和「sink 目前的 count」（不改原始碼，用一支臨時腳本包裝）：

```js
const wrapped = { ...sink, writeBatch(b) {
  calls++; console.log(`writeBatch call ${calls}: ids=[${b.map(r=>r.id)}] sink.count()=${sink.count()}`);
  return sink.writeBatch(b);
} };
exportRows(rows, { sink: wrapped, batchSize: 4, log: (m) => console.log("catch:", m, "sink.count()=" + sink.count()) });
```

```
writeBatch call 1: ids=[1,2,3,4] sink.count()=0
writeBatch call 2: ids=[5,6,7,8] sink.count()=4
catch: batch at 4 failed (attempt 1): sink: connection reset in batch 2 sink.count()=6
writeBatch call 3: ids=[5,6,7,8] sink.count()=6
writeBatch call 4: ids=[9,10] sink.count()=10
```

它一次就淘汰了 H1（`exportRows` 只跑了一輪，四次 `writeBatch` 都來自同一個迴圈）、確認了 H3 的機制：**第 2 批寫了 id 5、6 之後斷線（count 從 4 變成 6），重試從 5 開始重送整批，5、6 被寫了兩次。**

「批次第一列前失敗」的測試在修正前通過，正好支持 H3：失敗前沒有任何部分寫入時，重送整批不會產生重複。

### 機制

`writeBatch` 不是原子的：它一列一列 append，中途可能斷。`exportRows` 的重試假設「失敗 = 這批什麼都沒寫」，所以重送整批。這個假設是錯的。病因在 **exporter 的重試邏輯**：它沒有問 sink「你實際上已經寫到哪裡了」。

### 修正

```js
// src/export.mjs
export function exportRows(rows, { sink, batchSize = 500, retries = 3, log = () => {} }) {
  const base = sink.count();
  for (let start = 0; start < rows.length; start += batchSize) {
    const end = Math.min(start + batchSize, rows.length);
    for (let attempt = 1; ; attempt++) {
      const durable = sink.count() - base;
      const pending = rows.slice(Math.max(start, durable), end);
      try {
        if (pending.length > 0) sink.writeBatch(pending);
        break;
      } catch (err) {
        log(`batch at ${start} failed (attempt ${attempt}): ${err.message}`);
        if (attempt === retries) throw err;
      }
    }
  }
}
```

**解答說明**：每次嘗試之前，從 sink 的**實際狀態**（`count()`）推出已經寫了多少，只送還沒寫的部分。這是 Make Operations Idempotent 的核心：「derive each step from the state that exists, not from what the previous run meant to do. Read the current state, compare it to the goal, and apply only the difference.」

- `base` 記錄開始前 sink 裡已有的行數，讓這個函式在 sink 不是空的時候也正確
- `Math.max(start, durable)`：一般情況 `durable` 等於 `start`（上一批全部寫完）；斷在批次中間時 `durable` 大於 `start`，跳過已寫的部分
- `pending.length > 0` 的判斷：如果剛好在最後一列寫完後才斷（`durable === end`），不需要再送

這個修正**依賴 `count()` 反映真實的持久化狀態**。在真實的系統裡，這相當於「寫入目標能回報自己的進度」（例如資料庫的最後一個 id、object storage 的 multipart upload 已完成的 part）。如果目標做不到，另一個正確的方向是讓每一批的寫入變成原子的（先寫暫存檔再 rename，或用交易），那就是改 sink，而本練習禁止改 sink 的故障注入——但你可以在回覆的「沒做的事」裡提出這個方向。

### 測試

```js
// test/export.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { exportRows } from "../src/export.mjs";
import { fileSink } from "../src/file-sink.mjs";

const rows = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1, amount: (i + 1) * 100 }));
const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), "export-")), "out.csv");
const ids = (file) => fs.readFileSync(file, "utf8").trimEnd().split("\n").filter(Boolean).map((l) => Number(l.split(",")[0]));

const cases = [
  { name: "no failure", n: 10, failAtBatch: 0, failAfterRows: 0 },
  { name: "fails mid-batch", n: 10, failAtBatch: 2, failAfterRows: 2 },
  { name: "fails before the first row of a batch", n: 10, failAtBatch: 2, failAfterRows: 0 },
  { name: "fails before the last row of a batch", n: 10, failAtBatch: 1, failAfterRows: 3 },
  { name: "fails inside the final short batch", n: 10, failAtBatch: 3, failAfterRows: 1 },
];

for (const c of cases) {
  test(`every row exactly once, in order: ${c.name}`, () => {
    const file = tmp();
    const sink = fileSink(file, c);
    exportRows(rows(c.n), { sink, batchSize: 4 });
    assert.deepEqual(ids(file), rows(c.n).map((r) => r.id));
  });
}

test("empty input writes nothing", () => {
  const file = tmp();
  exportRows([], { sink: fileSink(file), batchSize: 4 });
  assert.deepEqual(ids(file), []);
});

test("a sink that keeps failing still surfaces the error after the retry budget", () => {
  const sink = { count: () => 0, writeBatch: () => { throw new Error("down"); } };
  assert.throws(() => exportRows(rows(3), { sink, batchSize: 4, retries: 3 }), /down/);
});

test("two identical ledger lines both survive a retry", () => {
  const file = tmp();
  const input = [{ id: 1, amount: 100 }, { id: 2, amount: 50 }, { id: 2, amount: 50 }, { id: 3, amount: 70 }, { id: 4, amount: 10 }];
  exportRows(input, { sink: fileSink(file, { failAtBatch: 2, failAfterRows: 0 }), batchSize: 2 });
  assert.deepEqual(ids(file), [1, 2, 2, 3, 4]);
});
```

三個版本對這份測試的實際結果：

| 版本 | pass | fail | 說明 |
|---|---|---|---|
| 原始（有 bug） | 5 | 3 | 「批次中間」「最後一列前」「最後短批次」失敗 |
| 症狀修正：匯出結束後去重 | 7 | 1 | 只有「兩筆合法的相同資料」失敗——它把合法的重複刪掉了 |
| 參考修正：從 sink 狀態推出待送部分 | 8 | 0 | |

（「去重」版本的實作：照常匯出，最後讀整個檔案、用 `new Set(lines)` 去掉重複的行再寫回。在加入最後一個測試之前，它會通過全部 7 個測試——這就是為什麼那個測試存在。）

### commit 順序

```
<sha2> fix(export): resume a retried batch from the rows the sink already holds
<sha1> test(export): failing repro for duplicate rows after a mid-batch retry
<sha0> feat: batch exporter with retries
```

在 `<sha1>` 上跑測試：3 個失敗（修正前的紅）。在 `<sha2>` 上：8 個全過、`repro.sh` 印 `PASS`。

### 回覆範例

> 目標：讓匯出在中途遇到連線重置並重試時，每一筆資料剛好出現一次，對帳才對得起來。
>
> 壞掉的：第 2 批在寫完 id 5、6 之後斷線，重試後檔案有 12 行，id 5、6 各出現兩次（實測，`repro.sh` 輸出 `FAIL ids=1 2 3 4 5 6 5 6 7 8 9 10`）。
>
> 病因：`exportRows` 的重試假設失敗的批次什麼都沒寫，於是重送整批；但 `writeBatch` 是逐列 append、中途可能斷，失敗時前兩列已經持久化了（實測，在 catch 裡印出重送 `[5,6,7,8]` 時 sink 已有 6 行）。
>
> 修正：每次嘗試前用 `sink.count()` 推出已寫入的列數，只送剩下的部分。**Make Operations Idempotent** 讓我從 sink 的實際狀態推出下一步，而不是從「上一次打算做什麼」推。**Fix Root Causes** 讓我拒絕在匯出結束後去重：去重會刪掉合法的相同資料（實測，去重版本在「兩筆合法的相同資料」測試上失敗）。
>
> 驗證：測試 commit 上 8 個測試有 3 個失敗，修正 commit 上 8 個全過；CLI 的 `repro.sh` 印 `PASS ids=1 2 3 4 5 6 7 8 9 10`（皆實測，輸出如下）。
>
> 沒做的事：這個修正依賴 `count()` 反映真實的持久化狀態。如果真實的寫入目標無法回報進度，應該改成讓每一批的寫入是原子的（推論，沒有實作）。

</details>

## 三個看起來對、其實錯的修法（看完解答再讀）

這三種修法都會讓 `repro.sh` 變綠，也都是 agent 在這類 bug 上常見的第一反應。每一種都對應一條本課讀過的原則。

**1. 把 `retries` 改成 1。** 沒有重試就沒有重複。但連線一斷，整個匯出就失敗——你把一個「多寫兩筆」的 bug，換成一個「什麼都匯不出來」的 bug。這正是 Bug fix playbook 開頭說的「Belt-and-suspenders that 'might help' is a hypothesis」的反面：它不是多加保護，而是拿掉了一個正確的機制來掩蓋另一個錯誤。任務規格明確禁止這個做法。

**2. 在 catch 裡吞掉錯誤、跳過這一批。** 匯出「成功」了、沒有重複——但斷線那一批剩下的列（id 7、8）永遠不見了。練習 B 的 PR 犯的就是這個錯。Fix Root Causes：「Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide a bug.」

**3. 匯出結束後去重。** 參考解答的測試表已經示範：它會刪掉兩筆合法的相同資料。而且它讓 exporter 在中途的任何時間點，檔案內容都是錯的（有重複），只有在最後一步之後才「對」——如果程序在去重之前崩潰，留下的是一個有重複的檔案。Make Operations Idempotent 要求的是「Converge to the same end state regardless of partial prior runs」，去重只在「跑到最後」時才收斂。

一個辨識錯誤修法的通用問題：**這個修法有沒有讓某個本來正確的行為變得不正確？** 三種錯誤修法都有（重試、資料完整性、合法的重複資料），參考解答沒有。

## 審查自己的解答

寫完之後，用這張清單對自己的提交做一次審查——這也是 Ch 14 的五條 Verification 原則的實際用法：

| 問題 | 對應原則 | 你的答案 |
|---|---|---|
| 重現腳本在修正前紅、修正後綠，而且比對的是完整的輸出？ | Prove It Works | |
| 失敗的測試是因為**正確的理由**失敗（重複的 id），不是 import 錯誤？ | Test Behavior, Not Implementation | |
| 修正在第一個做錯的地方（重試的邏輯），不是在症狀出現的地方（輸出檔案）？ | Fix Root Causes | |
| commit 歷史是紅先綠後，每個 commit 只做一件事？ | Sequence Work into Verifiable Units | |
| 修正從現存的狀態（sink 的 count）推出下一步？ | Make Operations Idempotent | |

## 延伸挑戰（加分）

- **讓 sink 的寫入變成原子的**：另寫一個 `atomicFileSink`，每一批先寫到暫存檔、成功後再一次 append（或 rename）。用同一份測試驗證 exporter 的**原始版本**搭配這個 sink 是否也不會重複。比較兩種修法：一個修在 exporter、一個修在 sink，各自的前提是什麼？
- **用 `git bisect` 練習**：在起始程式碼之前加幾個「早期版本是對的」的 commit（例如早期版本一列一列寫、每列各自重試），再用你的 `repro.sh` 跑 `git bisect run` 找出引入 bug 的 commit。
- **property-based test**：隨機產生 n（0–50）、batchSize（1–8）、failAtBatch、failAfterRows，跑 500 次，斷言「每一列剛好一次、依序」。你的修正在所有組合下都成立嗎？
- **把 repro.sh 變成 git bisect 的輸入，再把 bisect 本身寫進回覆**：如果你做了上一個延伸挑戰（在起始程式碼之前加幾個「早期版本是對的」的 commit），把 `git bisect run ../repro.sh` 的完整輸出貼進你的回覆的「病因」段，作為「這個 bug 是在哪個 commit 引入的」的執行期證據。比較它和「我讀程式碼覺得是這裡」哪一個更有說服力。
- **交給 agent 跑一次，然後審它**：在一個全新的 `ledger-export` 複本裡跑 `/pstack:poteto-mode the export writes duplicate rows when a retry lands mid-run. repro first, then fix and verify.`，拿你的參考解答當對照，逐步審它的 todo、skip 理由、commit 順序、回覆。它有沒有提出去重？

## 自我檢核

- [ ] 你的重現腳本在修正前紅、修正後綠，而且比對的是完整的 id 序列而不只是行數
- [ ] 你能說出「批次第一列前失敗」這個測試在修正前通過的原因，以及它支持了哪個假設
- [ ] 你能向同事解釋為什麼「匯出結束後去重」是症狀修正，並用一個具體的測試證明
- [ ] 你的 commit 歷史是「紅的測試 → 綠的修正」，而且每個 commit 只做一件事
- [ ] 你能說出「三個看起來對的錯誤修法」各自讓哪一個本來正確的行為變得不正確
- [ ] 你的假設清單裡，至少有一個假設是被**執行期證據**淘汰的，而不是被「讀程式碼覺得不像」淘汰的
- [ ] 你的回覆裡每一個斷言都標了「實測」或「推論」，而「推論」的那幾個你說得出要怎麼驗證
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨

Part 1 結束。你已經會用 playbook 了；接下來要看 playbook 背後那套「判斷詞彙」——24 條原則。

→ [Ch 11 原則是可引用的詞彙](./11-principles-as-vocabulary.md)
