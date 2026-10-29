# Ch 8 — Bug fix：先重現、二分搜尋、紅先綠後

> **目標**：逐步讀懂 Bug fix playbook 的六步與它開頭那段「科學方法」宣言，理解為什麼「先重現」「二分搜尋病因」「失敗的重現先進 git 歷史」這三件事是不能省的。你會在一個真實的 9-commit repo 上用 `git bisect run` 找到引入 bug 的 commit、親眼看一個只修症狀的改動如何通過重現腳本卻在另一個呼叫端繼續壞掉，最後做出一組紅先綠後的 commit。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26（`node:test`）。`ledger` repo、`repro.sh`、`git bisect` 與測試的輸出都是實際跑出來的。

## 為什麼需要這個？

給 agent 一個 bug 報告，最常見的失敗長這樣：

1. 它讀報告，在程式碼裡找到**第一個看起來相關的地方**
2. 改了它
3. 跑測試，綠了（或者根本沒有相關測試）
4. 回報「已修復」

這四步裡沒有一步證明了 bug 真的存在、病因真的在那裡、修改真的修好了它。Bug fix playbook 的每一步都在打斷這個流程。

## 先建立直覺：醫生不會沒量體溫就開藥

```
  agent 的直覺流程                         Bug fix playbook
  ─────────────────                       ─────────────────
  讀症狀描述                               1. 自己重現（在同一個介面上）
     │                                         │ 重現不了 → 合成觸發條件、加 instrumentation
     ▼                                         ▼
  猜一個病因                               2. 列出假設 → 二分搜尋 → 用執行期證據淘汰
     │                                         │ 確認存活的「機制」
     ▼                                         ▼
  改                                       3. 規劃修正（跨函式邊界就先 architect）→ 委派
     │                                         ▼
     ▼                                     4. 在同一個介面上驗證：原本的重現現在通過
  「測試過了」                                  ▼
                                           5. commit 順序：失敗的重現在前、修正在後
                                               ▼
                                           6. Opening a PR
```

## 一、開頭那段宣言

playbook 的開頭不是步驟，是一段態度：

> Be scientific. Every shipped line traces to runtime evidence. Belt-and-suspenders that "might help" is a hypothesis, not a fix. It does not ship. When evidence refutes a hypothesis, revert what it motivated. The smallest change the evidence justifies ships, nothing more.

拆成四條規則：

| 規則 | 擋的是什麼 |
|---|---|
| 每一行出貨的程式碼都能追到執行期證據 | 「我覺得這樣比較安全」的程式碼 |
| 「也許有幫助」的雙重保險是假設，不是修正，不出貨 | 順手加的 null check、try/catch、retry |
| 證據推翻假設時，revert 那個假設所引起的改動 | 試了三種修法，三種都留在 diff 裡 |
| 只出貨證據支持的最小改動 | 修 bug 順便重構 |

第二條特別重要。agent（和人）在不確定的時候傾向「多加一層保護」。這些保護會**藏住**真正的錯誤，讓下一次出問題時更難追。Fix Root Causes 原則講得更直接：「Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide a bug.」

## 二、Step 1：自己重現，在同一個介面上

> Reproduce it yourself on the matching surface via the control skill (Non-negotiables), even when a debug or instrumentation protocol says to ask the user to reproduce. Ask the user only with a stated, specific reason the control surface cannot reach the target, and only after driving it as far as it goes. If it won't reproduce directly, synthesize the trigger, tighten conditions, or instrument until it fires.

三個要點：

- **「yourself」**：不要叫使用者重現。只有在你的工具真的碰不到目標（而且你說得出具體原因、已經盡力推到最遠）時才問。
- **「matching surface」**：使用者在 CLI 看到的 bug，就在 CLI 重現；在瀏覽器看到的，就在瀏覽器重現。在單元測試裡重現一個 UI bug，可能只是重現了你**以為**的病因。
- **重現不了就製造條件**：縮小條件、合成觸發、加 instrumentation 直到它出現。「我這邊重現不了」不是終點。

我們的示範專案：一個計算 CSV 金額總和的小工具。

```js
// total.mjs（目前 HEAD 的樣子）
export function parseAmount(s) {
  return parseFloat(s.trim());
}
export function total(rows) {
  return rows.reduce((sum, r) => sum + parseAmount(r.amount), 0);
}
```

```js
// cli.mjs
import fs from "node:fs";
import { total } from "./total.mjs";
const [, ...lines] = fs.readFileSync(process.argv[2], "utf8").trim().split("\n");
console.log(total(lines.map((amount) => ({ amount }))));
```

```
// fixture.csv
amount
1,200
300
45
```

使用者回報：「總和不對，應該是 1545」。在使用者用的同一個介面（CLI）上重現：

```bash
node cli.mjs fixture.csv
```

```
346
```

重現了。接著做一個**可以 pass/fail 的重現腳本**，放在 repo 外面（下一步 bisect 會切換 commit，放在 repo 裡會被切掉）：

```sh
#!/bin/sh
# ../repro.sh
got=$(node cli.mjs fixture.csv)
if [ "$got" = "1545" ]; then echo "PASS total=$got"; exit 0; else echo "FAIL total=$got want=1545"; exit 1; fi
```

```
FAIL total=346 want=1545
exit=1
```

這支腳本是整個修 bug 過程的「北極星」：它在修之前是紅的，在修之後必須是綠的。

## 三、Step 2：二分搜尋病因

> Binary-search the cause. Form the candidate hypotheses, then rule them out until one survives. Seed them with `how` over the affected subsystem and the **why** skill for regression history. Each pass, take the split that cuts the most remaining problem space, get runtime evidence, eliminate. When program state is unclear, add instrumentation or logging and read it as the code runs. Don't guess.

「二分搜尋」在這裡有兩種意思，兩種都要會：

**(a) 在假設空間裡二分。** 列出所有可能的病因，每次挑一個能**淘汰最多假設**的實驗。例如 346 這個數字：

```
  假設 A：CSV 讀取漏了某一列        → 346 = 300 + 45 + 1？ 還是少了一列？
  假設 B：某個金額被解析錯         → 1,200 → 1？
  假設 C：加總邏輯錯（例如覆蓋）    → 不太像，346 不是任何單一值
```

一個實驗切最多：印出每一列解析後的值。如果是 `[1, 300, 45]`，A 和 C 同時被淘汰，只剩 B。這就是「take the split that cuts the most remaining problem space」。

**(b) 在 git 歷史裡二分。** 如果這是**回歸**（以前是好的），`git bisect` 能在 log₂(N) 次測試內找到引入 bug 的 commit。我們的 repo 有 8 個 commit：

```
d96bbee c8: tweak cli
93b2f6b c7: tweak cli
dae8e9a c6: tweak cli
804a853 c5: trim whitespace in amounts
f6d25e3 c4: tweak cli
47a480b c3: tweak cli
bd7c537 c2: tweak cli
58449cf c1: total amounts from csv
```

已知 HEAD 是壞的、c1 是好的。把重現腳本交給 `git bisect run`，它會自動二分：

```bash
git bisect start HEAD 58449cf
git bisect run ../repro.sh
git bisect reset
```

```
running '../repro.sh'
PASS total=1545
Bisecting: 1 revision left to test after this (roughly 1 step)
[dae8e9a70bb5bf5f733a0898aba9e2697ec76ec0] c6: tweak cli
running '../repro.sh'
FAIL total=346 want=1545
Bisecting: 0 revisions left to test after this (roughly 0 steps)
[804a85301bb0bb0c71139b7c0a6a01feba453cb0] c5: trim whitespace in amounts
running '../repro.sh'
FAIL total=346 want=1545
804a85301bb0bb0c71139b7c0a6a01feba453cb0 is the first bad commit
commit 804a85301bb0bb0c71139b7c0a6a01feba453cb0
Author: dev <dev@example.com>
Date:   Wed Oct 28 18:12:04 2026 +0800

    c5: trim whitespace in amounts

 total.mjs | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
bisect found first bad commit
```

三次測試，找到 c5。c5 的改動是：

```diff
 export function parseAmount(s) {
-  return Number(s.replace(/,/g, ""));
+  return parseFloat(s.trim());
 }
```

作者想處理前後空白，把 `Number(s.replace(/,/g, ""))` 換成 `parseFloat(s.trim())`，卻丟掉了「去掉千分位逗號」。`parseFloat("1,200")` 讀到逗號就停，回傳 `1`。

注意 commit 訊息「trim whitespace in amounts」完全沒提到逗號。如果你只用 `why` 讀 commit 訊息，你不會發現這個 commit 刪掉了什麼。**`git bisect` 給的是執行期證據**，commit 訊息給的是作者的意圖，兩者不同。

「Confirm the surviving *mechanism* with runtime evidence」：找到 commit 還不夠，要確認**機制**。在 REPL 裡跑一次 `parseFloat("1,200")` 看到 `1`，機制就確認了。

## 四、Step 3：規劃修正——修病因，不是修症狀

最省事的修法是在 CLI 裡先把逗號去掉：

```js
// cli.mjs 的「症狀修正」
console.log(total(lines.map((amount) => ({ amount: amount.replace(/,/g, "") }))));
```

重現腳本馬上變綠：

```
PASS total=1545
```

看起來修好了。但是 `parseAmount` 還有別的呼叫端：

```js
// report.mjs
import { parseAmount } from "./total.mjs";
export const largest = (amounts) => Math.max(...amounts.map(parseAmount));
```

寫一個從**使用者角度**描述行為的測試（不是測內部實作，Ch 14 會談 Test Behavior, Not Implementation）：

```js
// test/total.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { total } from "../total.mjs";
import { largest } from "../report.mjs";

test("total keeps thousands separators", () => {
  assert.equal(total([{ amount: "1,200" }, { amount: "300" }, { amount: " 45 " }]), 1545);
});

test("largest compares full amounts, not the digits before a comma", () => {
  assert.equal(largest(["1,200", "300"]), 1200);
});
```

在「症狀修正」的版本上跑測試，兩個都失敗（節錄 assertion 的值）：

```
    actual: 346,
    expected: 1545,
    actual: 300,
    expected: 1200,
```

CLI 好了，但 `total()` 直接被呼叫時還是錯，`largest()` 也錯——它認為 300 比 1,200 大。這就是 Fix Root Causes 的「Check for the pattern, not just the instance」：症狀出現在 CLI，病因在 `parseAmount`，所有呼叫 `parseAmount` 的地方都受影響。

我們撤掉症狀修正，**先把這個失敗的測試單獨 commit**：

```bash
git add test && git commit -m "test(total): failing repro for comma amounts"
```

此時跑測試（這就是「紅」）：

```
✖ total keeps thousands separators (0.534959ms)
✖ largest compares full amounts, not the digits before a comma (0.079708ms)
ℹ tests 2
ℹ suites 0
ℹ pass 0
ℹ fail 2
...
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:

  346 !== 1545
```

然後修病因：在 `parseAmount` 裡同時 trim 與去逗號。

```diff
 export function parseAmount(s) {
-  return parseFloat(s.trim());
+  return Number(s.trim().replace(/,/g, ""));
 }
```

playbook 的第 3 步還有兩個規定：**跨函式邊界的修正先跑 `architect`**（這個修正只改一個函式的內部，不需要）；**實作委派給 subagent**，用你設定的 bug-fix 模型，給明確的範圍。這個例子小到主 session 自己改也合理，但流程上的位置是「主 session 規劃、subagent 寫、主 session 審」。

## 五、Step 4：在同一個介面上驗證

> Verify on the same surface. The original repro now passes. "Inconclusive" or wrong-surface is not a pass. Flag it. Unit tests show branch behavior, not bug absence.

最後一句值得記住：**單元測試顯示的是分支行為，不是 bug 不存在**。測試綠了不夠，要回到使用者看到 bug 的那個介面（CLI）跑原本的重現：

```
✔ total keeps thousands separators (0.276583ms)
✔ largest compares full amounts, not the digits before a comma (0.053459ms)
ℹ tests 2
ℹ pass 2
ℹ fail 0
```

```
PASS total=1545
```

兩者都通過才算。如果重現腳本因為環境問題跑不了，答案是「inconclusive」，要明確標出來，不能當成 pass。

## 六、Step 5：讓失敗的重現先進 git 歷史

> Stage the commits so the failing repro lands before the fix in git history. See the **tdd** skill for the failing-test-first cadence when the bug has a cheap local test path. Skip it when the test would be expensive, integration-heavy, or unclear.
> This is the canonical **sequence-verifiable-units** principle skill, the failing test first and the fix on top.

```bash
git log --oneline -3
```

```
9991777 fix(total): keep thousands separators when trimming amounts
c130eeb test(total): failing repro for comma amounts
f4a2105 c9: add largest() report
```

審查者可以 checkout `c130eeb` 看兩個測試紅，再 checkout `9991777` 看它們綠。這一對 commit 本身就是「這個修正有效」的論證。

回顧 Ch 5 的陷阱：「Skip it」指的是**用 `tdd` 技能寫測試**這件事。如果寫測試很貴（要架整套整合環境），可以不寫新測試，但仍然要讓**某種**失敗的重現（例如 repro.sh）先進歷史。

### `tdd` 技能的護欄

`tdd` 本身有幾條值得單獨記住：

- 「Run the new test before fixing. Confirm it fails **for the intended reason**.」——我們的測試失敗訊息是 `346 !== 1545`，正是 bug 的症狀。如果它因為 import 路徑錯而失敗，那不算。
- 「Prefer no new test over a bad test.」壞測試的定義：主要在測 mock、寫死了目前的實作細節、依賴時序或全域狀態、為了小修正要架昂貴的基礎設施、證明完修正就會被刪掉。
- 「Do not change tests merely to match a wrong implementation.」

## 七、Step 6 與回覆

最後跑 Opening a PR（Ch 24）。回覆的格式：

> **Reply:** what was broken, root cause, fix, how you verified. Paste failing-then-passing repro output verbatim.

「verbatim」又出現了：貼**原樣**的失敗輸出和通過輸出，不是「測試從紅變綠了」這種摘要。

（示意）

> 目標：讓 CSV 總和在金額有千分位逗號時正確。
>
> 壞掉的：`fixture.csv` 的總和是 346，應該是 1545（實測，repro.sh 輸出 `FAIL total=346 want=1545`）。
>
> 病因：c5（804a853）為了處理空白，把 `parseAmount` 從 `Number(s.replace(/,/g, ""))` 換成 `parseFloat(s.trim())`，丟掉了去逗號的步驟；`parseFloat("1,200")` 回傳 1（`git bisect run` 三次測試定位，實測）。
>
> 修正：在 `parseAmount` 同時 trim 與去逗號。**Fix Root Causes** 讓我修在 `parseAmount` 而不是 CLI：CLI 層的修正會讓重現通過，但 `largest()` 仍然把 300 判成比 1,200 大（實測，測試輸出 `actual: 300, expected: 1200`）。
>
> 驗證：c130eeb 上兩個測試失敗、9991777 上兩個通過；CLI 重現 `PASS total=1545`（皆實測，輸出如下）。

## 底層機制：為什麼「先重現」這麼重要

```
              沒有重現                              有重現
  ┌─────────────────────────────┐   ┌──────────────────────────────┐
  │ 修改 ──▶ 「應該修好了」        │   │ 重現（紅）──▶ 修改 ──▶ 重現（綠）│
  │                              │   │                              │
  │ 你無法區分：                  │   │ 你可以區分：                  │
  │  · 修好了                     │   │  · 修好了（紅→綠）             │
  │  · 沒修好但測試沒涵蓋          │   │  · 沒修好（紅→紅）             │
  │  · 修了另一個問題              │   │  · 重現本身有問題（綠→綠）      │
  │  · 根本沒有 bug（誤報）        │   │                              │
  └─────────────────────────────┘   └──────────────────────────────┘
```

重現把一個模糊的「它壞了」變成一個**二元的、可以重跑的判準**。沒有它，後面每一步的「驗證」都是在驗證你的信念，不是在驗證系統。

## 對比與取捨

| 做法 | 找病因的方法 | 修在哪 | 證據 | 風險 |
|---|---|---|---|---|
| 直接改 | 讀程式碼猜 | 第一個可疑的地方 | 「測試過了」 | 修症狀、修錯地方 |
| 加防禦 | 不找 | 出錯的地方加 guard | 錯誤不再出現 | 把 bug 藏起來 |
| **Bug fix playbook** | 重現 + 二分 + 執行期證據 | 第一個做錯的地方 | 紅→綠的 commit 對 + 同介面重現 | 慢、需要能重現 |
| `git bisect` | 歷史二分 | （只找到 commit，不修） | 第一個壞的 commit | 只適用於回歸；需要可自動化的重現 |

## 踩雷集錦

1. **在錯的介面上重現**：使用者在 CLI 看到的 bug，你在單元測試裡「重現」了，但你重現的是你猜的病因。原文：「'Inconclusive' or wrong-surface is not a pass.」
2. **重現腳本放在 repo 裡再跑 bisect**：bisect 會 checkout 舊 commit，腳本可能還不存在。本章把 repro.sh 放在 `../`。
3. **把症狀修正當修好**：本章的 CLI 修正讓 repro.sh 通過了。只有寫出從呼叫端角度描述行為的測試，才看出 `largest()` 還是錯的。重現通過是必要條件，不是充分條件。
4. **留著被推翻的嘗試**：試了 A 沒用、試了 B 有用，最後 diff 裡 A 和 B 都在。playbook 要求「When evidence refutes a hypothesis, revert what it motivated.」
5. **測試因為錯的理由失敗**：新測試紅了，但紅的原因是 import 錯或 fixture 路徑錯。`tdd` 要求確認「fails for the intended reason」——看失敗訊息是不是 bug 的症狀。

## 進階：再往深一層

- **instrumentation 優先於猜測**：當狀態不清楚時，playbook 說「add instrumentation or logging and read it as the code runs. Don't guess.」在 Node 裡，`node --inspect` 加 Chrome DevTools、或在關鍵點加一行 `console.error(JSON.stringify(state))`，都比讀程式碼推理快。加的 log 記得在修完後移除（它也是「也許有幫助」的東西）。
- **用 `/loop` 驅動長時間的追查**：playbook 提到「Drive a long or stubborn hunt with Cursor's `/loop` command」。Claude Code 也有 `/loop`（本機的 skill 清單裡就有）；搭配一個 pass/fail 的重現腳本，它可以一直嘗試假設直到重現變綠（Ch 25）。
- **Attack the Premise**：如果你試了兩個修正、都因為同一個前提失敗（例如都假設「問題在解析」），停下來把前提寫下、直接測它，而不是寫第三個修正（Ch 12）。
- **加一個永遠開著的訊號**：Fix Root Causes 最後一條：「If the deciding fact took a deep dig to find, propose the cheap, always-on signal that would have shown it at first look.」這個例子裡，一個在 `parseAmount` 遇到非數字字元時記 warning 的 log，就能讓下次的 bug 一眼被看見。

## 動手練習

1. 照本章建出 `ledger` repo（c1 到 c9），自己跑一次 `git bisect run`。然後**故意**把 repro.sh 放進 repo 裡、在 c1 之後才 commit 它，再跑一次 bisect，看會發生什麼。
2. 寫一個**只測內部實作**的壞測試（例如斷言 `parseAmount` 內部呼叫了 `Number`），確認它在修正前後都通過或都失敗——它無法區分 bug 存不存在。
3. 對你自己專案裡一個最近修過的 bug，回頭檢查：失敗的重現有沒有先進歷史？修的是病因還是症狀？如果重來，你會怎麼排 commit？

## 本章重點整理

- Bug fix playbook 的態度：每一行出貨的程式碼都追得到執行期證據；「也許有幫助」的保護不出貨；被推翻的假設要 revert
- 先在使用者看到 bug 的同一個介面上自己重現，做成一支 pass/fail 的重現腳本
- 二分搜尋有兩種：在假設空間裡挑最能淘汰的實驗；在 git 歷史裡用 `git bisect run`
- 修病因不修症狀：找出所有受同一個錯誤影響的地方
- 失敗的重現先進 git 歷史、修正在後；回覆貼原樣的紅與綠

## 自我檢核

- [ ] 不看本章，能說出 Bug fix playbook 的六步，以及開頭宣言的四條規則嗎？
- [ ] 本章的 CLI 症狀修正讓重現通過了，為什麼它仍然是錯的？你是靠什麼發現的？
- [ ] 「Unit tests show branch behavior, not bug absence」這句話，用你自己的例子解釋
- [ ] `git bisect run` 需要什麼前提才能用？如果 bug 不是回歸（一直都有），你要用哪一種二分？

## 延伸閱讀

### 原始碼

- **[playbooks/bug-fix.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/bug-fix.md)**
  - **讀哪裡**：整份（368 字）
  - **能學到什麼**：本章所有引文的原文
- **[skills/tdd/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/tdd/SKILL.md)** 與 **[principle-fix-root-causes](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-fix-root-causes/SKILL.md)**
  - **讀哪裡**：tdd 的「If a Failing Test Is Impractical」；fix-root-causes 的 Pattern 清單
  - **能學到什麼**：什麼時候不該寫測試；怎麼判斷一個 guard 是修正還是掩蓋

### 官方文件

- **[git-bisect 官方文件](https://git-scm.com/docs/git-bisect)**
  - **讀哪裡**：「Bisect run」一節，特別是 exit code 125 的語意（「這個 commit 無法測試，跳過」）
  - **能學到什麼**：本章的 `git bisect run` 背後的完整規則；當某些舊 commit 根本編不過時，重現腳本要回傳 125
  - **前提**：基本 git 操作

### 書籍

- **《Why Programs Fail: A Guide to Systematic Debugging》** — Andreas Zeller（Morgan Kaufmann, 第二版 2009）
  - **這本書的定位**：把除錯當成科學方法的教科書；playbook 的「列假設、做實驗、淘汰」就是這本書的核心
  - **讀哪幾章**：Chapter 6「Scientific Debugging」與 Chapter 13「Isolating Failure Causes」（delta debugging，git bisect 的理論背景）
  - **注意**：出版超過 15 年，工具章節過時，方法論章節完全適用
- **《Debugging: The 9 Indispensable Rules》** — David J. Agans（AMACOM, 2002）
  - **這本書的定位**：比 Zeller 輕量的實務版本；「Make It Fail」「Quit Thinking and Look」兩條規則和 playbook 的 Step 1、Step 2 幾乎一字不差
  - **讀哪幾章**：Rule 2 與 Rule 3，各一章，很短

### 對照閱讀

- **[harness_engineering Ch 38 — 失敗模式與 debug agent](../harness_engineering/38-failure-modes-debugging.md)**（本 repo）
  - **和本章的關聯**：那一章是 debug agent 本身；這一章是讓 agent 用正確的方法 debug 你的程式

修 bug 是改正錯誤的行為；下一章是加入新的行為和改變結構。

→ [Ch 9 Feature 與 Refactoring：先命名資料形狀、先釘住行為](./09-feature-refactoring.md)
