# Ch 9 — Feature 與 Refactoring：先命名資料形狀、先釘住行為

> **目標**：讀懂 Feature playbook 的「throughput checkpoint」與委派 brief 該包含什麼，以及 Refactoring playbook 的八步——特別是「先釘住行為再動結構」「結構改變、行為不變」「如果讀者負擔沒降低就 revert」。你會親手做一次重構：用一個枚舉全部 16 種輸入組合的釘子（pin）把舊行為鎖住，看一個「看起來更乾淨」的版本如何悄悄改變了 3 種組合的行為，再做出一個真正等價的版本。最後也會談 Prototype playbook——唯一一份「品質規則反轉」的流程。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26。`orders` repo、pin 腳本與所有輸出都是實際跑出來的。

## 為什麼需要這個？

Ch 8 是修正**錯的**行為。這一章處理另外兩種改動：

- **Feature**：加入**新的**行為
- **Refactoring**：改變**結構**，行為**完全不變**

這兩者最大的風險正好相反。Feature 的風險是「資料結構選錯，之後每一行都在補救」；Refactoring 的風險是「以為沒改行為，其實改了」。pstack 為兩者各寫了一份 playbook，而它們的第一步都是同一個主題：**在寫任何程式碼之前，先把某樣東西固定下來**。Feature 固定的是資料形狀，Refactoring 固定的是行為。

## 先建立直覺：蓋新房 vs 改格局

```
  Feature（蓋新房間）                    Refactoring（改格局，不改用途）
  ──────────────────                    ──────────────────────────────
  先畫結構圖：                            先拍照存證：
    這個房間的承重、管線怎麼接？              每個房間現在怎麼用？
    （資料形狀 + 組織結構）                   （characterization test / pin）
          │                                      │
          ▼                                      ▼
  決定哪些工班可以同時進場                  先拆掉沒用的隔間
  （throughput checkpoint）                 （subtract before you add）
          │                                      │
          ▼                                      ▼
  施工、驗收                               小步移動，每步都對照照片
                                          （每一步都要 pin 仍然綠）
                                                 │
                                                 ▼
                                          改完問：住起來比較好找東西嗎？
                                          不是的話，拆回去（reader load）
```

## 一、Feature playbook：八步裡最重要的兩步

Feature 的完整流程在 Ch 3 已經走過一次。這裡深入兩個 Ch 3 沒展開的部分。

### 1.1 Throughput checkpoint：決定怎麼分工之前，先回答四個問題

第 3 步要求寫出四個 todo 項目，不適用的寫 `n/a: <理由>`，不准刪：

| 項目 | 要回答的問題 | 原則依據 |
|---|---|---|
| **Blocking first steps** | 有哪些事一定要先做完，才能開始平行？ | Foundational Thinking（scaffold first） |
| **Independent workstreams** | 哪些部分不碰同樣的檔案、服務、層？那些可以平行 | — |
| **Shared mutable state** | 有沒有兩個工作者會寫同一個東西？預設是把目標拆開，只有真正的不變量才序列化 | Separate Before Serializing Shared State |
| **Smallest safe decomposition** | 如果一個 worker 最好，說明為什麼 | Laziness Protocol |

為什麼要這麼正式？因為 agent 的預設行為往往是兩個極端之一：**完全不平行**（一個 agent 從頭做到尾，慢），或**盲目平行**（開五個 subagent 改同一個模組，互相覆蓋）。四個問題逼它在中間找到正確的位置。

playbook 還加了一段很重要的補充：

> Code-coupled work (one feature, one migration) goes to a single owner with the checkpoint inline. That owner fans out internally after the blocking phase. Parent-level fan-out is for slices that produce independent artifacts (audits, cross-subsystem investigations, competing experiments).

意思是：**一個功能就給一個 owner**，由它在完成阻塞步驟後自己決定要不要再分工。主 session 層級的平行化，只用在產出彼此獨立的工作（例如同時審計三個子系統）。這避免了「主 session 把一個功能切成五塊、五個 agent 各寫一塊、最後拼不起來」。

### 1.2 委派 brief：要給什麼

第 4 步把 brief 的內容列得很細：

```
  檔案路徑（scope）
  + 已命名的資料形狀，以及它的組織結構（依 Model the Domain：
      狀態機而不是散落的 boolean、查表而不是分支、型別模型而不是重複的形狀假設）
    ── 在 delegate 寫任何邏輯之前就選好
  + 成功條件
```

並且：

> When the implementation admits multiple valid shapes (error handling, abstraction layer, test structure), delegate via the **arena** skill instead ... Mandatory: no skip-with-reason escape, and Laziness Protocol does not override it (the gain is review separation, not lines saved).

這是 pstack 少數「不准 skip」的步驟。理由在括號裡：**價值是審查分離，不是省多少行**。當一個實作有多種合理寫法時，讓同一個 agent 自己寫、自己選，等於讓作者自己判。arena 讓多個 runner 各寫一版、由另一個家族的評審挑（Ch 18）。

還有兩句容易被忽略的：

- 「Port shared-primitive improvements to all consumers and verify each.」——你改進了一個共用的函式，所有用它的地方都要跟著驗證。這和 Ch 8 的「Check for the pattern, not just the instance」是同一個精神。
- 「Surgical edits, re-ground against the source for upstream-derived files.」——如果檔案是從上游衍生的（例如 vendored 的程式碼、產生出來的檔案），每次改之前重新對照來源。

## 二、Refactoring playbook：契約不變，結構改變

開頭的定位：

> **You own the contract. The structure changes. The behavior does not.**
>
> If the cleanup reveals a missing feature or a real bug, split it out and ship the structural change first against the pinned contract. A redesign is allowed, but name it and route to Feature.

這段話定義了 Refactoring 的邊界：**重構過程中發現 bug，不准順手修**。先把結構改變對著釘住的舊行為出貨，bug 另外開一個 Bug fix。為什麼？因為一個「重構 + 修 bug」的 diff，你沒辦法證明它是重構——行為變了，你不知道哪些變化是 bug 修正、哪些是重構弄壞的。

我們用一個例子把八步全部走一遍。

### 範例：一個長滿 boolean 的狀態函式

```js
// status.mjs：訂單狀態標籤，四個 boolean 旗標
export function statusLabel(o) {
  if (o.isCancelled) {
    if (o.isRefunded) return "refunded";
    return "cancelled";
  }
  if (o.isShipped) {
    if (o.isPaid) return "shipped";
    return "shipped (unpaid)";
  }
  if (o.isPaid) return "paid";
  if (o.isRefunded) return "refunded";
  return "pending";
}
```

### Step 1：先釘住行為契約

> Pin the behavior contract first. ... write a characterization test, snapshot, or equivalence harness that captures current behavior before any structure moves. ... Type check and lint are not a pin.

四個 boolean 只有 2⁴ = 16 種組合，所以我們可以**全部枚舉**，不需要挑樣本。寫一支 pin 腳本，第一次跑時把 16 種組合的輸出存起來，之後每次跑都和存下來的比對：

```js
// pin.mjs：枚舉四個旗標的全部組合，記錄或比對 statusLabel 的輸出
import fs from "node:fs";
const flags = ["isPaid", "isShipped", "isCancelled", "isRefunded"];
const { statusLabel } = await import(process.argv[2] ?? "./status.mjs");
const rows = [];
for (let mask = 0; mask < 1 << flags.length; mask++) {
  const o = Object.fromEntries(flags.map((f, i) => [f, Boolean(mask & (1 << i))]));
  rows.push({ mask, label: statusLabel(o) });
}
const out = JSON.stringify(rows, null, 1);
if (process.argv[3] === "--write") { fs.writeFileSync("pin.json", out); console.log(`pinned ${rows.length} combinations`); }
else {
  const want = JSON.parse(fs.readFileSync("pin.json", "utf8"));
  const diffs = rows.filter((r, i) => r.label !== want[i].label);
  for (const d of diffs) console.log(`DIFF mask=${d.mask.toString(2).padStart(4, "0")} want=${want[d.mask].label} got=${d.label}`);
  console.log(diffs.length ? `FAIL ${diffs.length} of ${rows.length} combinations changed` : `PASS ${rows.length} of ${rows.length} combinations unchanged`);
  process.exit(diffs.length ? 1 : 0);
}
```

```bash
node pin.mjs ./status.mjs --write
```

```
pinned 16 combinations
```

把釘住的內容印出來看（mask 的位元由右到左是 isPaid、isShipped、isCancelled、isRefunded）：

```
0000 (none) -> pending
0001 isPaid -> paid
0010 isShipped -> shipped (unpaid)
0011 isPaid+isShipped -> shipped
0100 isCancelled -> cancelled
0101 isPaid+isCancelled -> cancelled
0110 isShipped+isCancelled -> cancelled
0111 isPaid+isShipped+isCancelled -> cancelled
1000 isRefunded -> refunded
1001 isPaid+isRefunded -> paid
1010 isShipped+isRefunded -> shipped (unpaid)
1011 isPaid+isShipped+isRefunded -> shipped
1100 isCancelled+isRefunded -> refunded
1101 isPaid+isCancelled+isRefunded -> refunded
1110 isShipped+isCancelled+isRefunded -> refunded
1111 isPaid+isShipped+isCancelled+isRefunded -> refunded
```

**釘子本身就是一次調查。** 看 `1001`：一張已付款又已退款的訂單，顯示「paid」。這幾乎可以確定是 bug——使用者退款後看到「已付款」。還有 `1010`：已出貨、已退款、未付款？這個組合在真實世界裡大概不該存在。

依 playbook，**我們不修**。我們把它記下來（之後另開 Bug fix 或 Feature），然後對著**包含這些怪行為**的釘子做重構。官方指南對遷移也有同樣的要求：「the result must match the original exactly, bugs included.」

先 commit 釘子：

```
4db14bb test(status): pin all 16 flag combinations
```

### Step 2–3：命名缺少的結構、命名目標形狀

> Name the structure the code is missing per **principle-model-the-domain**. Boring code stays when the shape is already clear and local. The reshape must delete branches or invalid states, not add indirection.

這段程式碼缺的結構是什麼？它其實是一份**有優先順序的規則表**：取消優先、出貨其次、然後付款、然後退款、最後預設。巢狀 if 把這個順序藏在縮排裡。

目標形狀：一個有序的 `[條件, 標籤]` 陣列，第一個符合的規則勝出。

（更根本的結構是「把四個 boolean 換成一個 `state` 欄位」，讓 `isShipped && isCancelled` 這種非法組合根本無法表示。但那會**改變行為**（有些組合不再存在），所以那是 Feature，不是 Refactoring。playbook 說：「A redesign is allowed, but name it and route to Feature.」）

### Step 4：先減法

> Subtract before you add. Delete dead code, collapse one-caller wrappers, drop redundant validators, and remove orphan references before introducing the new shape.

這個例子沒有死碼可刪。在真實的 codebase 裡，這一步常常是最有價值的：先刪掉沒人用的分支和包裝層，剩下的東西往往小到不需要重構。

### Step 5：小步移動，每步都讓 pin 保持綠

先看一個**錯誤**的嘗試。一個看起來更乾淨的版本：用一個優先順序陣列，退款優先：

```js
// status-clean.mjs：看起來很合理的「清理」
const PRIORITY = [
  ["isRefunded", "refunded"],
  ["isCancelled", "cancelled"],
  ["isShipped", null],
  ["isPaid", "paid"],
];
export function statusLabel(o) {
  for (const [flag, label] of PRIORITY) {
    if (!o[flag]) continue;
    if (flag === "isShipped") return o.isPaid ? "shipped" : "shipped (unpaid)";
    return label;
  }
  return "pending";
}
```

這個版本甚至「順便修好了」`1001` 那個 bug（退款訂單不再顯示 paid）。跑 pin：

```bash
node pin.mjs ./status-clean.mjs
```

```
DIFF mask=1001 want=paid got=refunded
DIFF mask=1010 want=shipped (unpaid) got=refunded
DIFF mask=1011 want=shipped got=refunded
FAIL 3 of 16 combinations changed
exit=1
```

三種組合變了。其中 `1001` 是我們**想要**的修正，但 `1010` 和 `1011` 呢？已出貨又已退款的訂單，原本顯示「shipped」，現在顯示「refunded」——這是對是錯？可能取決於產品決定（部分退款？退貨中？）。**一個重構的 diff 裡混了一個 bug 修正和兩個未經決定的行為改變**，而且如果沒有 pin，你完全不會知道。

這就是為什麼 playbook 要先釘住。現在做一個忠實的版本，把巢狀 if 原本的順序攤平成表：

```js
// status-table.mjs：攤平成有序的規則表，順序和原本的巢狀 if 完全一致
const RULES = [
  [(o) => o.isCancelled && o.isRefunded, "refunded"],
  [(o) => o.isCancelled, "cancelled"],
  [(o) => o.isShipped && o.isPaid, "shipped"],
  [(o) => o.isShipped, "shipped (unpaid)"],
  [(o) => o.isPaid, "paid"],
  [(o) => o.isRefunded, "refunded"],
];
export function statusLabel(o) {
  return RULES.find(([when]) => when(o))?.[1] ?? "pending";
}
```

```bash
node pin.mjs ./status-table.mjs
```

```
PASS 16 of 16 combinations unchanged
exit=0
```

### Step 6：在真實產物上證明行為不變

我們的 pin 本身就是 playbook 說的 equivalence check（「a script that diffs old-vs-new outputs, a recorded baseline replayed against the new code」）。因為輸入空間只有 16 種，這個證明是**完整的**，不是抽樣的——這是一個難得的情況。輸入空間大的時候，要用錄製的真實輸入回放，或隨機產生大量輸入。

替換原檔、再跑一次、commit：

```
PASS 16 of 16 combinations unchanged
944ea23 refactor(status): replace nested branches with an ordered rule table
4db14bb test(status): pin all 16 flag combinations
```

### Step 7：值得保留嗎？

> Confirm the change is worth keeping. The success measure is reduced reader load (**principle-minimize-reader-load**). If the diff does not lower reader load somewhere, revert it.

誠實地評估這個重構：

| | 巢狀 if | 規則表 |
|---|---|---|
| 縮排深度 | 2 層 | 0 層（每條規則一行） |
| 「什麼時候是 refunded？」 | 要追兩個分支 | 看兩行（第 1 條和第 6 條） |
| 隱藏狀態 | 順序藏在縮排裡 | 順序就是陣列的順序，**但依然重要** |
| 加一種新狀態 | 找到對的分支插進去 | 在對的位置插一行 |

收益是有的，但**不大**。規則表把順序變得可見了，但順序依然是隱藏的語意（第 1 條必須在第 2 條前面）。Minimize Reader Load 的測試是「一個新讀者能不能在 30 秒內回答『X 從哪來、什麼能改變 X』」——規則表比巢狀 if 好一點，但真正的大幅改善要靠 Step 3 說的那個 Feature：把四個 boolean 換成一個 state。

這種「收益不大」的判斷是 Step 7 的意義：**重構不是免費的**，它會讓 git blame 變難讀、讓正在進行的其他分支產生衝突。收益要真的存在。這個例子我們保留，理由是它讓下一步（改成單一 state）的 diff 會變得很小。

### Step 8：commit 的順序

> A subtraction commit, then the reshape, then any follow-on cleanup.

我們的歷史是「pin → reshape」，沒有減法 commit（沒東西可刪）。如果有，順序是 pin → 刪除 → 重塑 → 收尾。

**回覆**要包含：改變的結構、釘住它的 pin、等價證明、讀者負擔的變化、出貨了什麼、revert 了什麼。以及——**不准有新行為**。`1001` 的 bug 寫進「後續工作」，不寫進這次的 diff。

## 三、Prototype playbook：規則反轉的那一份

Prototype 是唯一明確說「品質規則反轉」的 playbook：

> The one playbook where the Laziness Protocol's "smallest change" and the verification bar invert. Speed over polish, code quality does not matter, no planning. The rigor is in picking the right design cheaply. Propose variations the user didn't ask for, throw an approach away and try another.

| | 一般 playbook | Prototype |
|---|---|---|
| 程式碼品質 | 重要 | 不重要 |
| 最小改動 | 是 | 否，主動提出使用者沒要求的變體 |
| 驗證 | 證明行為正確 | 觀察要決定的那件事（截圖、計時、輸出） |
| 產物 | 可出貨的程式碼 | 一個決定 + 一個可丟棄的工具 |
| 位置 | production source 裡 | 隔離的 scratch 目錄 |

它的第一步是一條守門規則：「Scope the decision the prototype exists to make ... No decision means no prototype. Route to Feature.」**沒有要做的決定，就不要做 prototype**——否則它會變成「先寫個草稿」然後草稿被當成正式程式碼出貨。

第 4 步：比較多個選項時，把它們放在**同一個切換器**後面（按鈕或快捷鍵），每個變體標好名字。這是 Exhaust the Design Space 原則「變便宜」的方式。

回覆最後要「Say plainly that the prototype is throwaway」。

Prototype 也是 Ch 2 那條「事實問題不要問人」的執行者：agent 想問「A 還是 B 比較快」，答案是寫兩個 prototype 量一量。

## 四、README 先行：給共用程式碼的另一種 Feature

官方設計章的一個建議：

> For a package or API that other code will use, start with the doc a user would read. ... Writing the tutorial first forces the caller's view.

```text
/pstack:poteto-mode write a tutorial for how i would use the new config package first. then /teach me why it beats the current one.
```

這和 architect 的「caller's usage is written first」是同一個想法：**先寫使用方式，型別和實作從使用方式推出來**。文件同時變成 agent 檢查自己工作的具體目標。

## 對比與取捨

| | Feature | Refactoring | Prototype |
|---|---|---|---|
| 行為 | 新增或改變 | 不變 | 探索，不出貨 |
| 第一步固定什麼 | 資料形狀 | 行為（pin） | 要做的決定 |
| 驗證 | 新行為在真實介面上成立 | pin 從頭到尾都綠 | 觀察結果 |
| 發現 bug 時 | 視情況 | 拆出去，不准順手修 | 記下來 |
| 結束條件 | 驗證通過 + PR | 等價證明 + 讀者負擔降低 | 一個有證據的推薦 |

## 踩雷集錦

1. **用型別檢查當 pin**：playbook 原文：「Type check and lint are not a pin.」編譯過只代表形狀對，不代表行為一樣。本章的 `status-clean.mjs` 完全能跑、沒有語法錯誤，卻改了 3 種組合的行為。
2. **重構時順手修 bug**：`status-clean.mjs` 修好了 `1001`，同時改了兩個沒人決定過的組合。混在一起的 diff 無法審查。拆開：先等價重構，再另開 Bug fix。
3. **重構沒有降低讀者負擔還保留**：「If the diff does not lower reader load somewhere, revert it.」重構會讓 blame 變難讀、讓其他分支衝突，它要有真實的收益。
4. **一個功能切給多個 subagent**：「Code-coupled work goes to a single owner.」主 session 把一個功能切成五塊，五個 agent 對介面的理解各不相同，最後拼不起來。
5. **prototype 變成正式程式碼**：prototype 的程式碼品質規則是反轉的。如果它的程式碼進了 production source，所有反轉的規則（不測試、不抽象、不在乎品質）都跟著進去了。

## 進階：再往深一層

- **輸入空間大的時候怎麼釘**：本章的 16 種組合可以窮舉。真實的函式通常不行。三個常用的辦法：錄下 production 的真實輸入回放（最真實）、property-based testing 隨機產生大量輸入（覆蓋廣）、在新舊實作並行跑一段時間比對（GitHub 的 [Scientist](https://github.com/github/scientist) 函式庫就是做這件事的）。
- **做那個真正的 Feature**：把四個 boolean 換成 `state: "pending" | "paid" | "shipped" | "cancelled" | "refunded"`。你會發現 16 種組合要對應到 5 種狀態——有些組合要決定怎麼遷移（`1010` 是什麼？）。這些決定是產品決定，正好是 Never Block on the Human 說的「真正該問人」的那一類。
- **Visual parity playbook**：UI 遷移的「pin」是截圖。它要求像素級比對一個凍結的基準，而不是用眼睛看。原理和本章的 pin.json 完全一樣，只是產物從 JSON 換成 PNG。

## 動手練習

1. 照本章建出 `orders` repo，跑一次 pin。然後自己寫一個**不同的**「看起來更乾淨」的版本（例如用 `switch(true)`），確認 pin 會不會抓到你不小心改掉的組合。
2. 把 `1001` 當成 Bug fix 做：先寫一個失敗的測試（「已付款又已退款的訂單應該顯示 refunded」），commit，再修。修完之後 pin 會變紅——你要怎麼處理 pin？（提示：pin 是重構用的釘子；行為**有意地**改變時，更新釘子本身就是那次 Bug fix 的一部分，要在 commit 訊息裡說明。）
3. 挑你專案裡一個有三層以上巢狀 if 的函式，列出它的輸入維度。它的組合能窮舉嗎？不能的話，你會用哪一種方法釘住它？

## 本章重點整理

- Feature 先固定資料形狀，Refactoring 先固定行為，Prototype 先固定要做的決定
- Throughput checkpoint 的四項（阻塞步驟、獨立工作流、共享可變狀態、最小安全分解）不准刪，不適用寫 `n/a: <理由>`
- 一個功能給一個 owner；多種合理實作時強制用 arena，理由是審查分離
- Refactoring 的 pin 要在任何結構改動前存在；型別檢查不是 pin；重構中發現的 bug 拆出去
- 重構要真的降低讀者負擔，否則 revert；Prototype 是唯一品質規則反轉、產物可丟棄的流程

## 自我檢核

- [ ] 不看本章，能說出 throughput checkpoint 的四項，並各舉一個「這項會改變分工決定」的例子嗎？
- [ ] 本章的 `status-clean.mjs` 修好了一個 bug，為什麼它仍然是一個壞的重構？
- [ ] 「把四個 boolean 換成一個 state」為什麼是 Feature 而不是 Refactoring？
- [ ] 想一個你最近做過的重構：你有沒有在動結構之前先釘住行為？如果沒有，你怎麼確定行為沒變？

## 延伸閱讀

### 原始碼

- **[playbooks/feature.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/feature.md)**、**[refactoring.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/refactoring.md)**、**[prototype.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/prototype.md)**
  - **讀哪裡**：三份整份對照讀，注意每一份第一步固定的是什麼
  - **能學到什麼**：本章引文的原文

### 官方指南

- **[docs/guide/04-design.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/04-design.md)**
  - **讀哪裡**：「Prototype instead of debating」「Write the README first for shared code」「How much design work does a task deserve?」
  - **能學到什麼**：一張「多少設計工作才夠」的階梯，幫你判斷什麼時候該跳過 architect

### 書籍

- **《Refactoring: Improving the Design of Existing Code》第二版** — Martin Fowler（Addison-Wesley, 2018）
  - **這本書的定位**：重構的標準參考書；「重構不改變可觀察行為」的定義來自這裡
  - **讀哪幾章**：Chapter 2「Principles in Refactoring」（為什麼、什麼時候重構）；Chapter 10「Simplifying Conditional Logic」裡的「Replace Nested Conditional with Guard Clauses」與「Replace Conditional with Polymorphism」，和本章的規則表是同一類手法
- **《Working Effectively with Legacy Code》** — Michael Feathers（Prentice Hall, 2004）
  - **讀哪幾章**：Chapter 13「I Need to Make a Change, but I Don't Know What Tests to Write」
  - **和本章的關聯**：characterization test（本章的 pin）就是這本書提出的概念

### 文章

- **[GitHub Scientist](https://github.com/github/scientist)** — GitHub
  - **這是什麼**：一個讓新舊實作在 production 並行、比對結果的函式庫
  - **為什麼值得讀**：它的 README 解釋了「輸入空間太大無法窮舉時，怎麼用真實流量當 pin」，是本章「進階」那一節的生產級版本

Part 1 的 playbook 還剩最後一類：效能。效能問題的特殊之處在於，它的「證據」是一個數字，而數字比任何東西都容易騙人。

→ [Ch 10 Perf、Hillclimb 與 forensics](./10-perf-hillclimb-forensics.md)
