# Ch 12 — Core 原則十條

> **目標**：逐條讀懂 pstack 的十條 Core 原則——它們決定「要做多少」與「什麼時候該重新想設計」。每一條你都會知道：它的觸發情境、它擋的是哪一種 agent 失敗、它和相近的原則差在哪、以及「你跳過了它」長什麼樣。其中 Build the Lever 會配一個實際跑過的 codemod 實驗，示範「能跑」和「做對」差多遠。

> **環境**：pstack 0.15.36、Node.js 26。codemod 實驗與輸出是實際跑出來的；標「示意」的情境是為了說明原則而設計的敘述，沒有對應的程式執行。

## 為什麼需要這個？

Ch 11 講了原則系統的結構。這一章開始讀內容。十條 Core 原則大致回答兩個問題：

- **要做多少？** Laziness Protocol、Subtract Before You Add、Minimize Reader Load、Build the Lever
- **什麼時候該停下來重新想？** Foundational Thinking、Redesign from First Principles、Attack the Premise、Outcome-Oriented Execution、Experience First、Exhaust the Design Space

agent 在這兩個問題上的預設傾向都是錯的：它傾向**做太多**（加抽象、加防禦、加「之後可能用到」的參數），也傾向**不停下來**（第一個設計就照做、修正失敗就再修一次）。

## 先建立直覺：十條原則在一次任務裡的位置

```
   任務開始
      │
      ├─ 新需求要整合進既有設計？ ──────▶ Redesign from First Principles
      ├─ 沒有先例的新設計？ ──────────▶ Exhaust the Design Space（做 2–3 個 prototype）
      ├─ 要選資料結構、決定先做什麼？ ──▶ Foundational Thinking
      ├─ 產品取捨？ ─────────────────▶ Experience First
      │
   動手
      │
      ├─ 先刪掉沒用的 ───────────────▶ Subtract Before You Add
      ├─ 能寫工具就寫工具 ───────────▶ Build the Lever
      ├─ 改動越小越好 ───────────────▶ Laziness Protocol
      ├─ 讀起來要好追 ───────────────▶ Minimize Reader Load
      ├─ 大型遷移的中間狀態 ──────────▶ Outcome-Oriented Execution
      │
   卡住
      │
      └─ 兩次修正因為同一個前提失敗？ ──▶ Attack the Premise
```

## 一、Laziness Protocol

> **索引**：Refactoring, sizing a diff, or tempted to add abstractions, layers, or signal threading. Bias to deletion and the smallest change that solves the problem.

全文只有兩條：

- **Consolidate decisions.** 同一個選擇不要在好幾個地方重複做。放在一個事實來源後面，把結果當成簡單的旗標傳下去。
- **Minimize the diff.** 解決問題的最小改動。少幾行勝過「優雅的」樣板。

**擋的失敗**：agent 修一個 bug 時順手「改善」周圍的程式碼、加一個「之後可能有用」的參數、把一個 if 抽成一個 strategy pattern。每一個都讓 diff 變大、讓審查變難、讓 bug 有更多地方可以藏。

**第一條常被忽略。** 「Consolidate decisions」的意思是：如果「這個使用者是不是管理員」這個判斷出現在五個地方，就讓它只出現在一個地方、算一次，其他地方拿結果。這和「最小 diff」看起來矛盾（合併五個判斷會讓 diff 變大），其實不矛盾：Laziness 的目標是**整個系統**需要維護的決定變少，不只是這一次的 diff 變小。

**和其他原則的關係**：Prototype playbook 明確反轉了它（「The one playbook where the Laziness Protocol's 'smallest change' ... invert」）；Feature playbook 的 arena 步驟明確說它「does not override」（因為價值是審查分離，不是行數）。知道一條原則**不適用**的地方，和知道它適用的地方一樣重要。

## 二、Foundational Thinking

> **索引**：Before writing logic: core types and data structures, scaffold-vs-feature sequencing, what concurrent actors share.

三個重點：

- **資料結構優先**：「Get the data shape right before writing logic. Define core types early, trace every access pattern, and choose structures that match the dominant paths.」
- **DRY 結構，不是 DRY 每一行**：「Three similar statements still beat a premature abstraction.」
- **Scaffold first**：「If something helps every later phase, do it first.」CI、lint、測試基礎設施、共用型別都是 scaffold。

還有一條**並發推論**：「Before sharing state between actors, ask 'what happens if another actor modifies this concurrently?' If not 'nothing', isolate.」

**擋的失敗**：先寫邏輯，寫到第三個函式才發現資料結構不支援某個存取模式，然後在每個呼叫端補救。Ch 3 的 Feature 執行裡，「先命名資料形狀」（Row + formatters 表）就是這條原則。

**「Three similar statements still beat a premature abstraction」** 是一條很重要的平衡。Foundational Thinking 不是叫你一開始就設計完美的抽象，而是叫你一開始就把**資料**弄對。程式碼層級的重複可以容忍，資料模型的不一致不行。

## 三、Redesign from First Principles

> **索引**：Integrating a new requirement into an existing design. Redesign as if it had been foundational from day one.

> When integrating a change, don't bolt it onto the existing design. Redesign as if the requirement had been a foundational assumption from the start.

四條做法：讀完所有受影響的檔案、問「如果從頭寫、帶著這個新需求，會怎麼做？」、把改變傳到每一個參照（型別、文件、範例）、**整體地想重新設計，增量地交付**。

最後一條是關鍵：重新設計不等於一次大改。你在腦中（或在 architect 的設計稿裡）把整個系統當成「這個需求一開始就存在」重新想一遍，然後分成小的、可驗證的步驟交付。

**擋的失敗**：「加一個參數」「加一個特殊情況的 if」——每次新需求都在舊設計旁邊多掛一個東西，三個需求之後，設計已經看不出原本的樣子。

**和 Attack the Premise 的區別**（後者的原文）：Redesign 是「圍繞一個新需求重建設計」；Attack the Premise 是「質疑目前設計假設的一個事實」。一個是需求變了，一個是你對現況的認識可能錯了。

## 四、Attack the Premise

> **索引**：Two or more fixes that share one premise have failed the same gate. Write the premise down and test it directly before you write another fix that assumes it.

觸發條件非常精確：**兩個以上**的修正、**共用一個前提**、**在同一個關卡**失敗。

一個情境（示意）：

```
  症狀：API 呼叫偶爾 timeout
  修正 1：timeout 從 5 秒調到 10 秒          → 還是 timeout
  修正 2：加 3 次 retry                     → 還是 timeout，而且更慢
  ─────────────────────────────────────────────────────────
  兩個修正共用的前提：「伺服器處理得太慢」
  ─────────────────────────────────────────────────────────
  直接測前提：在伺服器端記錄每個請求的處理時間
  結果：伺服器端所有請求都在 200ms 內處理完
  → 前提是錯的。時間花在別的地方（例如 DNS 解析、連線池耗盡）
```

全文的 Pattern：

- **把前提寫下來**：「The premise is the one sentence that every failed fix assumed.」
- **在下一個修正之前測前提**：找一個「如果前提是錯的，結果會不一樣」的觀察。「Look at how the failures are distributed, not only at how large they are.」（失敗是均勻分佈的，還是集中在某些請求？集中的話，那些請求有什麼共同點？）
- **跟著證據走**：證據推翻前提，就去找失敗的共同點；證據支持前提，就問下一個「為什麼」。
- **移除條件，而不是補償它**：「A mechanism that cleans up after it leaves the cause in place and adds work on every run.」

**Stop**：「Do not start the next fix before the premise is written down and tested.」

這條原則是 2026 年 9 月新加的兩條之一（commit「density and mannered-prose pass across the skills, two new principle leaves」），它補的是 Fix Root Causes 沒涵蓋的情況：Fix Root Causes 告訴你「往回追到第一個做錯的地方」，但如果你對「哪裡做錯」的整個認識都建立在一個錯的前提上，你會一直往錯的方向追。

## 五、Subtract Before You Add

> **索引**：Sequencing an addition, refactor, or rewrite. Remove dead weight first, then build on the simpler base.

> When evolving a system, remove complexity first, then build. Default to subtraction.

具體做法：先刪再建、先砍再打磨、為觀察到的使用設計而不是為想像的邊界情況、「No speculative validators, parsers, or guards beyond what the spec demands」、精簡 prompt、「When a reference has no novel content, delete it rather than leaving a stub」。

**擋的失敗**：在一堆沒人用的程式碼上面蓋新功能。新功能必須和那些死碼相容，死碼因此「活」得更久。

Refactoring playbook 的第 4 步直接引用它，commit 順序也是「a subtraction commit, then the reshape」。減法先 commit，有一個額外的好處：**減法 commit 本身很容易審**（只有刪除），審完之後，重塑的 commit 是在一個更小的基礎上做的。

官方指南的引導範例：「use subtract before you add. delete the obsolete adapters first, then design what's left.」

## 六、Minimize Reader Load

> **索引**：Reviewing or shaping code that's hard to trace. Count layers and hidden state, collapse one-caller wrappers, shrink mutable scope.

這條原則定義了「可維護性」：

> Maintainability is the work a reader must do to understand code. Track two axes: **layers to trace** (indirections between the question and the answer) and **state to hold** (hidden or mutable context the reader must keep in their head). LOC, cyclomatic complexity, and "clean architecture" are proxies.

```
           layers to trace（要追幾層）
                 ▲
     6 層 adapter │  ●  很難讀
     堆疊         │
                 │
                 │              ●  很難讀
                 │                 50 個全域變數的扁平檔案
                 └──────────────────────────────▶ state to hold（要記住多少狀態）
```

兩個軸**互相獨立**：一個 6 層的 adapter 堆疊和一個有 50 個全域變數的扁平檔案一樣難讀。兩個都要守。

做法：

- **收掉成本大於價值的層**：只有一個呼叫者的 wrapper、沒有第二個實作的 adapter、從來不需要的投機性間接層
- **相鄰的層要改變抽象**：一層只是把同樣的方法和參數往下傳，就收掉它
- **要求介面壓縮**：一個很寬、卻沒藏多少複雜度的介面，讓讀者既要學介面又要學實作
- **縮小狀態範圍**：純函式優先（回傳勝過修改）、區域變數勝過欄位、欄位勝過模組狀態、模組狀態勝過全域
- **在邊界上命名不變量**，而不是在每個使用者那裡

**The test**：「can a new reader answer 'where does X come from?' and 'what can change X?' in under 30 seconds?」

原文還點出一個很好的對照：「This is the human analog of [Guard the Context Window]. Working memory is finite for readers too.」——人的工作記憶和 agent 的 context window 是同一種資源限制。

Ch 9 的 Refactoring 用它當成功的判準：重構後讀者負擔沒有降低，就 revert。

## 七、Outcome-Oriented Execution

> **索引**：Planned rewrites and migrations with explicit phase boundaries. Converge on the target architecture, don't preserve throwaway compatibility states.

> **Why:** Keeping every intermediate step fully stable often creates temporary compatibility code that becomes long-lived debt.

核心規則：「Prioritize end-state integrity over transitional stability」與「Intermediate breakage is acceptable when it is planned, scoped, and reversible」。

**護欄**（這條原則有明確的適用範圍）：只用在有明確階段邊界的計畫性重寫與遷移；要宣告哪裡可以暫時壞掉；遷移期間被動到的區域要保留高訊號的檢查；計畫完成時要做完整的靜態與執行期驗證。

**擋的失敗**：一個遷移做了半年，因為每一步都要保持舊系統完全正常，於是寫了一堆「新舊相容層」。遷移結束時，相容層沒人敢刪，變成永久的技術債。

**和 Sequence Work into Verifiable Units 的張力**：後者說每一步都要可驗證、綠了才往下走。Outcome-Oriented 說中間可以壞。兩者怎麼同時成立？答案是**驗證的對象不同**：Sequence 驗證的是「這一步做了它該做的事」，Outcome-Oriented 允許的是「整個系統在遷移中途暫時不能完整運作」。每一步都綠，指的是這一步的檢查綠，不是整個產品都能上線。

## 八、Experience First

> **索引**：Product, UX, or feature-scope tradeoffs. Choose user delight over implementation convenience.

> When implementation convenience conflicts with user delight, choose delight.

做法：每個功能、控制項、選項都要有存在的理由；做少一點、做好一點（「polished experience with three features beats rough one with ten」）；決定之前先 prototype；細節要對（轉場、對齊、間距、回饋、錯誤狀態）；收緊核心迴圈。

最重要的是這一段對「使用者」的定義：

> The user is whoever consumes the work. For a UI that is the end user. For a library or an internal API it is the colleague who imports it. The engineer who maintains the code next is a user too.

這解釋了 poteto-mode「Writing the reply」裡那條規則：「Frame impact for the consumer and the maintainer.」回覆要先講對使用者和維護者有什麼影響，再講實作細節——這就是 Experience First 用在溝通上。

**和 Foundational Thinking 的分工**（原文）：「Foundational thinking governs the *sequence* of work. This principle governs the *target*.」一個決定**做的順序**，一個決定**要做成什麼樣**。

## 九、Exhaust the Design Space

> **索引**：A novel interaction or architectural decision with no precedent. Build 2-3 competing prototypes and compare before committing.

全文非常短（395 位元組），補充一句：「It also applies to product design decisions where user experience depends on feel, not logic.」

**擋的失敗**：「Taking the first design」——官方指南列為兩大設計錯誤之一。模型的第一個設計，是它最先想到的設計，不是最好的設計。

這條原則有三個「便宜的執行方式」：Prototype playbook（多個 prototype 放在同一個切換器後面）、architect 的「Design it twice」（至少兩個結構上不同的候選）、arena（多個 runner 平行做同一個 brief）。Ch 18 會詳談後兩者。

觸發條件是「**沒有先例**」。codebase 裡已經有三個類似的 API，第四個照著做就好，不需要做三個 prototype。

## 十、Build the Lever

> **索引**：Any non-trivial work. Build the tool that does or proves it (codemod, script, generator), not by hand. The tool is the artifact a reviewer reruns.

這條原則在 2026-05-26 加入、2026-05-29 被加強成「defaults to building the tool for any non-trivial work」。它的核心：

> A deterministic script turns "trust me" into "run this".

做法：

- 先手動做第一個單元學會做法，再寫工具；在那個單元上重跑工具，和手動版本 diff
- 讓工具可以安全重跑
- 「A deterministic lever beats fan-out」：工具能一次處理全部，就自己跑，不要開一堆 subagent 手動套用
- 要 fan out 給 subagent 時，把工具寫成一份它們都讀的技能，**放在它們的寫入範圍之外**，讓它們不能偷偷改合約
- **「Applying this principle produces a file. If you cited it and there is no codemod, script, generator, or delegate skill in the diff, you didn't apply it.」**

門檻是「是否瑣碎」，不是「是否重複」：「A one-off still earns a lever when the lever is what makes the work checkable.」

### 實驗：一次改名

把 `fetchUser` 改名為 `loadUser`。專案有四個檔案：

```js
// src/api.mjs
export async function fetchUser(id) { return { id, name: `user${id}` }; }
export async function fetchUsers(ids) { return Promise.all(ids.map(fetchUser)); }
```

```js
// src/profile.mjs
import { fetchUser } from "./api.mjs";
export async function profile(id) {
  const u = await fetchUser(id);
  console.log("fetchUser ok", id);
  return u;
}
```

```js
// src/team.mjs
import { fetchUsers } from "./api.mjs";
export const team = (ids) => fetchUsers(ids);
```

```js
// src/check.mjs：一個煙霧測試
import { profile } from "./profile.mjs";
import { team } from "./team.mjs";
const p = await profile(1); const t = await team([2, 3]);
console.log(JSON.stringify({ p: p.name, t: t.map((u) => u.name) }));
```

**手動快速版**：`sed -i '' 's/fetchUser/loadUser/g' src/*.mjs`

```
 src/api.mjs     | 4 ++--
 src/profile.mjs | 6 +++---
 src/team.mjs    | 4 ++--
 3 files changed, 7 insertions(+), 7 deletions(-)
```

```
src/team.mjs:1:import { loadUsers } from "./api.mjs";
src/team.mjs:2:export const team = (ids) => loadUsers(ids);
src/api.mjs:1:export async function loadUser(id) { return { id, name: `user${id}` }; }
src/api.mjs:2:export async function loadUsers(ids) { return Promise.all(ids.map(loadUser)); }
...
```

煙霧測試：

```
loadUser ok 1
{"p":"user1","t":["user2","user3"]}
```

**程式能跑，輸出正確。但改名做錯了。** `fetchUsers` 被一起改成 `loadUsers`——我們沒有要求改它，`team.mjs` 整個檔案根本不在範圍內。因為改得「一致」，程式照樣能跑，煙霧測試完全看不出來。這是 Prove It Works 說的代理指標：「能跑」不等於「做了你要的事」。Refactoring playbook 也警告過：「Spot-check every rename against the actual files. Renames silently miss usages in strings, prose, and back-references.」

**槓桿版**：寫一支有乾跑模式、會自我檢查的 codemod：

```js
// rename.mjs：用字邊界比對識別字，先報告再寫入，寫入後驗證
import fs from "node:fs";
import path from "node:path";

const [dir, from, to, mode = "--dry-run"] = process.argv.slice(2);
const ident = new RegExp(`\\b${from}\\b`, "g");
const files = fs.readdirSync(dir).filter((f) => f.endsWith(".mjs")).map((f) => path.join(dir, f));
const count = (re, text) => (text.match(re) ?? []).length;
const before = { target: 0, neighbors: 0, strings: [] };
const neighbor = new RegExp(`\\b${from}\\w+\\b`, "g");   // fetchUsers 這類「鄰居」
for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  before.target += count(ident, text);
  before.neighbors += count(neighbor, text);
  text.split("\n").forEach((line, i) => {
    for (const s of line.match(/(["'`])(?:(?!\1).)*\1/g) ?? [])
      if (ident.test(s)) before.strings.push(`${file}:${i + 1} ${s}`);
    ident.lastIndex = 0;
  });
}
console.log(`identifier matches: ${before.target}, neighbors like ${from}*: ${before.neighbors} (left alone)`);
for (const s of before.strings) console.log(`REVIEW string literal mentions ${from}: ${s}`);
if (mode === "--write") {
  for (const file of files) fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace(ident, to));
  let left = 0, neighborsAfter = 0;
  for (const file of files) { const t = fs.readFileSync(file, "utf8"); left += count(ident, t); neighborsAfter += count(neighbor, t); }
  console.log(`after: ${from} left=${left}, neighbors=${neighborsAfter} (was ${before.neighbors})`);
  process.exit(left === 0 && neighborsAfter === before.neighbors ? 0 : 1);
}
```

乾跑：

```bash
node rename.mjs src fetchUser loadUser
```

```
identifier matches: 5, neighbors like fetchUser*: 3 (left alone)
REVIEW string literal mentions fetchUser: src/profile.mjs:4 "fetchUser ok"
```

它先告訴你：有 5 處要改、3 處相似但會被保留（`fetchUsers`）、有 1 處在字串裡，需要人判斷。寫入：

```bash
node rename.mjs src fetchUser loadUser --write; echo "exit=$?"
```

```
identifier matches: 5, neighbors like fetchUser*: 3 (left alone)
REVIEW string literal mentions fetchUser: src/profile.mjs:4 "fetchUser ok"
after: fetchUser left=0, neighbors=3 (was 3)
exit=0
```

```
 src/api.mjs     | 4 ++--
 src/profile.mjs | 6 +++---
 2 files changed, 5 insertions(+), 5 deletions(-)
```

`team.mjs` 沒有被碰到；寫入後的後置條件（目標名稱剩 0 個、鄰居數量不變）由工具自己檢查並用 exit code 回報。字串裡的 `"fetchUser ok"` 也被改了——這是一個需要判斷的地方（log 訊息要不要跟著改名？），工具沒有替你決定，而是用 `REVIEW` 標出來。

這支工具也有限制：它用正規表示式而不是語法樹，所以分不清「註解裡的 fetchUser」和「程式碼裡的 fetchUser」；真實的大型改名應該用語言工具（TypeScript 的 rename refactor、jscodeshift）。但它已經示範了槓桿的三個性質：**審查者可以重跑**、**有乾跑**、**會檢查自己的後置條件**。

## 對比與取捨：十條原則的衝突點

| 原則 A | 原則 B | 衝突在哪 | 怎麼解 |
|---|---|---|---|
| Laziness Protocol | Foundational Thinking | 最小 diff vs 先把資料結構弄對 | 資料結構錯了，最小 diff 只是在錯的基礎上補洞；這時 Foundational 優先 |
| Laziness Protocol | Build the Lever | 最小改動 vs 多寫一支工具 | Build the Lever 原文：「build the smallest script that does or proves the job, never a framework」——工具本身也要遵守 Laziness |
| Outcome-Oriented | Sequence Verifiable Units | 允許中間壞掉 vs 每步都要綠 | 驗證的對象不同：每步的檢查要綠，整個產品可以暫時不完整 |
| Experience First | Laziness Protocol | 細節要對 vs 改動要小 | Experience First 決定目標，Laziness 決定怎麼到達；目標不能為了省事而降低 |
| Exhaust the Design Space | Laziness Protocol | 做 3 個 prototype vs 最小改動 | 只在「沒有先例」時觸發；有先例就照先例 |

## 踩雷集錦

1. **把 Laziness Protocol 讀成「不要寫測試」「不要驗證」**：它管的是**改動的範圍**，不是驗證的嚴謹度。最小的修正，加上完整的證明。
2. **「Three similar statements still beat a premature abstraction」被當成永遠不抽象**：它說的是**過早的**抽象。第四次、第五次出現同樣的形狀，就是 Model the Domain 該出場的時候（Ch 13）。
3. **引用 Build the Lever 卻沒有任何腳本**：原文明說這是沒做。手動改了 20 個檔案然後說「我用了 Build the Lever」是自相矛盾。
4. **在有先例時做三個 prototype**：Exhaust the Design Space 的觸發條件是「no precedent」。照既有模式寫第四個 API，不需要比賽。
5. **修第三次之前沒停下來**：兩次修正失敗後，Attack the Premise 要求先寫下前提、直接測它。第三次修正如果還建立在同一個前提上，你是在證據已經說「不在這裡」的地方繼續花力氣。

## 進階：再往深一層

- **把 rename.mjs 換成語法樹版本**：用 TypeScript compiler API 或 [jscodeshift](https://github.com/facebook/jscodeshift) 改寫，讓它分得清識別字、字串、註解。比較兩個版本在同一個專案上的輸出差異——差異的地方，就是正規表示式版本的盲點。
- **Reader load 能量化嗎？**：Minimize Reader Load 說 LOC 和循環複雜度是代理指標。試著為「layers to trace」設計一個量法：從一個使用者可見的行為出發，數到真正的實作要經過幾個函式呼叫。這比 LOC 更接近原則想量的東西，但你會發現它很難自動化——這是這條原則為什麼仍然需要判斷、寫成文字而不是 lint 的原因。
- **讀上游的 commit 歷史找「新增原則」的時間點**：Build the Lever（2026-05-26）、Sequence Work into Verifiable Units（2026-06-06）、Model the Domain（2026-07-11）、Attack the Premise 與 Test Behavior, Not Implementation（同一個 commit，2026-09-07）、Explain the Number（2026-10-03）。每一條新原則都對應一類作者反覆看到的 agent 失敗；第三方文章說「21 條」「23 條」原則，多半是寫在這些原則加入之前。

## 動手練習

1. 照本章建出四個檔案，跑一次 `sed` 版改名和 rename.mjs 版改名，確認你得到一樣的結果。然後在 `src/` 裡加一個註解 `// fetchUser is deprecated`，再跑 rename.mjs 的乾跑——它會報告這個註解嗎？應該要嗎？
2. 挑一次你最近「修了兩次才修好」的經驗，用 Attack the Premise 回頭檢查：兩次修正共用的前提是什麼？當時有什麼觀察可以直接測那個前提？
3. 拿你專案裡一個你覺得「很難讀」的模組，分別用兩個軸評估：要追幾層？要記住多少狀態？它難讀是因為哪一個軸？

## 本章重點整理

- 「要做多少」：Laziness Protocol（合併決定、最小 diff）、Subtract Before You Add（先刪後建）、Minimize Reader Load（層數與狀態兩個軸）、Build the Lever（寫可重跑的工具，引用它就要有檔案）
- 「什麼時候停下來重想」：Foundational Thinking（資料結構與 scaffold 優先）、Redesign from First Principles（當作需求一開始就存在）、Attack the Premise（兩次失敗共用前提就先測前提）、Outcome-Oriented Execution（遷移收斂到終點、不保留相容層）、Experience First（使用者包括下一個維護者）、Exhaust the Design Space（沒有先例時做 2–3 個 prototype）
- 每條原則都有明確的觸發情境與適用範圍；知道它**不適用**的地方和知道它適用的地方一樣重要
- 「能跑」不等於「做對」：本章的 sed 改名讓程式照樣能跑，卻改了不在範圍內的檔案

## 自我檢核

- [ ] 不看本章，能把十條 Core 原則分成「要做多少」和「什麼時候重想」兩組嗎？
- [ ] Redesign from First Principles 和 Attack the Premise 的差別是什麼？各舉一個觸發它的情境
- [ ] 本章的 sed 改名為什麼能通過煙霧測試？你會加什麼檢查讓它失敗？
- [ ] Outcome-Oriented Execution 和 Sequence Work into Verifiable Units 看起來矛盾，怎麼同時成立？

## 延伸閱讀

### 原始碼

- **24 條原則的 leaf 檔（Core 十條）**：[laziness-protocol](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-laziness-protocol/SKILL.md)、[foundational-thinking](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-foundational-thinking/SKILL.md)、[attack-the-premise](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-attack-the-premise/SKILL.md)、[minimize-reader-load](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-minimize-reader-load/SKILL.md)、[build-the-lever](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md) 等
  - **讀哪裡**：每一條全文，都很短
  - **能學到什麼**：本章摘要沒有涵蓋的細節；特別注意每條的「Distinct from」段落

### 書籍

- **《A Philosophy of Software Design》第二版** — John Ousterhout（Yaknyam Press, 2021）
  - **這本書的定位**：Minimize Reader Load 的「interface compression」「pass-through layers」幾乎直接對應這本書的「deep modules」與「pass-through methods」
  - **讀哪幾章**：Chapter 4「Modules Should Be Deep」、Chapter 7「Different Layer, Different Abstraction」
- **《The Pragmatic Programmer》20 週年版** — David Thomas、Andrew Hunt（Addison-Wesley, 2019）
  - **讀哪幾章**：Topic 9「DRY—The Evils of Duplication」（注意它說的 DRY 是**知識**的重複，不是程式碼的重複——和 Foundational Thinking 的「DRY the structure, not every line」是同一個意思）

### 文章

- **[Sandi Metz — The Wrong Abstraction](https://sandimetz.com/blog/2016/1/20/the-wrong-abstraction)**（2016）
  - **這篇說什麼**：「duplication is far cheaper than the wrong abstraction」
  - **和本章的關聯**：Foundational Thinking 的「Three similar statements still beat a premature abstraction」的完整論證
- **[jscodeshift](https://github.com/facebook/jscodeshift)** — Meta
  - **這是什麼**：用語法樹做 JavaScript codemod 的工具
  - **和本章的關聯**：rename.mjs 的生產級版本；Build the Lever 在大型 codebase 裡的標準實作

十條 Core 原則決定做多少、什麼時候重想。下一章的六條 Architecture 原則，決定狀態、驗證與相容性要放在哪裡。

→ [Ch 13 Architecture 原則六條](./13-architecture-principles.md)
