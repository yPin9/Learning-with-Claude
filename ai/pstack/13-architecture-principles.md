# Ch 13 — Architecture 原則六條

> **目標**：讀懂 pstack 的六條 Architecture 原則——它們決定**狀態、驗證與相容性要放在哪裡**。你會在 TypeScript 7 的型別檢查器上看到「讓非法狀態無法表示」的實際錯誤訊息，在 Node.js 上親眼看到兩個 worker 共寫一個 state.json 時，三次執行出現三種不同的結果（崩潰、遺失更新、剛好沒事），再看「把共享拆掉」怎麼讓問題整個消失。

> **環境**：pstack 0.15.36、Node.js 26、TypeScript 7.0.2（只裝在 `/tmp` 的練習資料夾）。所有程式與輸出都是實際跑出來的；並發實驗的結果每次都可能不同，這正是重點。

## 為什麼需要這個？

Core 原則決定「做多少」。Architecture 原則決定「**放在哪裡**」：

- 狀態放在一個結構裡，還是散在一堆 boolean 裡？
- 驗證放在系統的邊界，還是每一層都檢查一次？
- 型別能不能替你擋掉錯的參數？
- 一個操作被中斷、重跑時，結果還對不對？
- 新 API 上線時，舊 API 留多久？
- 兩個並行的工作者，要不要寫同一個東西？

這六個問題的錯誤答案有一個共同點：**它們不會讓程式立刻壞掉**。散落的 boolean 能跑、重複的驗證能跑、共寫的 state.json 大部分時間也能跑。它們讓程式在某個特定的條件下壞掉，而那個條件往往很難重現。agent 特別容易犯這類錯，因為它寫完之後跑一次測試，測試是綠的。

## 先建立直覺：六條原則各自管哪一種「位置」

```
                ┌─────────────────────── 系統邊界 ───────────────────────┐
  外部輸入 ───▶ │ Boundary Discipline：在這裡驗證、解析成型別               │
  (CLI/env/    │         │                                               │
   網路/檔案)   │         ▼                                               │
               │ Type System Discipline：解析後的資料有自己的型別，內部信任型別│
               │         │                                               │
               │         ▼                                               │
               │ Model the Domain：狀態放進一個結構（狀態機、查表、型別模型）   │
               │         │                                               │
               │         ▼                                               │
               │ Make Operations Idempotent：每一步從現存狀態推出，重跑會收斂  │
               │                                                         │
               │ Separate Before Serializing：並行的工作者各寫各的           │
               │                                                         │
               │ Migrate Callers Then Delete：新舊 API 不並存               │
               └─────────────────────────────────────────────────────────┘
```

## 一、Model the Domain

> **索引**：Writing stateful logic, or code that branches a lot or repeats a shape assumption across files. Encode the domain in a structure (state machine, typed model, table or registry, reducer, boundary, the right collection) instead of scattered conditionals.

這條原則在 poteto-mode 裡有特殊地位：Non-negotiables 的「Any code → name the data shape first, and choose its organizing structure per **principle-model-the-domain**」直接點名它，Feature 與 Refactoring playbook 也都引用它。

它給了一份「該伸手去拿什麼」的清單：

| 你看到的 | 該換成的 |
|---|---|
| 散落的 boolean、phase、生命週期檢查 | 狀態機 |
| 鬆散的參數、重複的形狀假設 | 型別模型 |
| 散在多個檔案裡的分支 | map、registry、查表、discriminated union |
| 臨時的狀態修改 | reducer 或 command/event 模型 |
| 依執行順序組織的模組（load、validate、transform、save） | 依一塊領域知識組織的模組（「Execution order is not ownership.」） |
| 重複的行為、所有權或不變量 | 一個小的模組邊界 |
| 存取模式需要的結構 | queue、cache、index、graph、tree、正規化的集合 |

最後一條「依執行順序組織」很容易被忽略。很多 codebase 有 `load.js`、`validate.js`、`transform.js`、`save.js`，每個檔案都要知道「訂單」「使用者」「發票」的規則。原則說：「Temporal decomposition is another sign. Phase-named modules repeat the same domain rules across steps.」

**它也明確告訴你什麼時候不要用**：「Do not force an abstraction. Prefer boring code if the current shape is already clear, local, and unlikely to grow.」

**你跳過它的徵兆**：「a new feature that grows an existing if/else chain by one more branch, or a second boolean that must stay in sync with the first.」

Ch 9 的 `statusLabel` 就是這條原則的完整案例：四個 boolean 產生 16 種組合，其中好幾種在真實世界不該存在。下一節的 Type System Discipline 示範怎麼讓它們**無法被寫出來**。

## 二、Type System Discipline

> **索引**：Designing types or a signature in any typed language. Make illegal states unrepresentable, brand primitives, parse external data at boundaries.

> Make the compiler reject impossible states, mismatched primitives, and unhandled variants. Prefer defining errors and special cases out of existence over proliferating handlers.

三個做法，我們用 TypeScript 7.0.2 實際驗證。

**1. 給語意不同的原始型別加上品牌（brand）。** `UserId` 和 `OrderId` 底層都是 string，但不該互換：

```ts
// ids.ts
type Brand<T, B extends string> = T & { readonly __brand: B };
export type UserId = Brand<string, "UserId">;
export type OrderId = Brand<string, "OrderId">;

export function parseUserId(raw: string): UserId {
  if (!/^u_[a-z0-9]+$/.test(raw)) throw new Error(`not a user id: ${raw}`);
  return raw as UserId;
}
export function parseOrderId(raw: string): OrderId {
  if (!/^o_[a-z0-9]+$/.test(raw)) throw new Error(`not an order id: ${raw}`);
  return raw as OrderId;
}

function refund(order: OrderId, by: UserId): string {
  return `${by} refunded ${order}`;
}

const user = parseUserId("u_42");
const order = parseOrderId("o_7");
console.log(refund(order, user));
console.log(refund(user, order));   // 參數順序寫反了
```

`as UserId` 只出現在 parse 函式裡：「Validate once at creation, trust the type downstream.」

**2. 用 discriminated union 讓非法狀態無法表示，並要求窮舉所有變體。** 把 Ch 9 的四個 boolean 換成一個 `state`：

```ts
// status.ts
type Order =
  | { state: "pending" }
  | { state: "paid"; paidAt: string }
  | { state: "shipped"; paidAt: string; trackingId: string }
  | { state: "refunded"; refundedAt: string };

export function label(o: Order): string {
  switch (o.state) {
    case "pending": return "pending";
    case "paid": return `paid ${o.paidAt}`;
    case "shipped": return `shipped ${o.trackingId}`;
    // 忘了處理 "refunded"
  }
}

const bad: Order = { state: "shipped", paidAt: "2026-10-01" };   // 出貨了卻沒有 trackingId
```

```bash
npx tsc --noEmit --strict ids.ts status.ts; echo "exit=$?"
```

```
ids.ts(21,20): error TS2345: Argument of type 'UserId' is not assignable to parameter of type 'OrderId'.
  Type 'UserId' is not assignable to type '{ readonly __brand: "OrderId"; }'.
    Types of property '__brand' are incompatible.
      Type '"UserId"' is not assignable to type '"OrderId"'.
status.ts(7,34): error TS2366: Function lacks ending return statement and return type does not include 'undefined'.
status.ts(15,7): error TS2322: Type '{ state: "shipped"; paidAt: string; }' is not assignable to type 'Order'.
  Property 'trackingId' is missing in type '{ state: "shipped"; paidAt: string; }' but required in type '{ state: "shipped"; paidAt: string; trackingId: string; }'.
exit=1
```

三個錯誤，三種擋法：

| 錯誤 | 擋掉了什麼 | 對應的原則做法 |
|---|---|---|
| TS2345 | 參數順序寫反 | Brand semantic primitives |
| TS2366 | 新增一個狀態後，忘了在某個地方處理它 | Exhaust variants（窮舉） |
| TS2322 | 「已出貨但沒有追蹤碼」這種不可能的狀態 | Make illegal states unrepresentable |

和 Ch 9 的對照：四個 boolean 版本有 16 種可表示的組合，其中包括「已取消又已出貨又已退款」；discriminated union 版本只有 4 種，每一種都是真實的狀態，而且每種狀態**一定**帶著它需要的欄位。

**3. 從權威 schema 推出型別**：「When a protocol buffer, OpenAPI spec, GraphQL schema, database migration ... owns a shape, derive from it instead of hand-rolling a parallel type.」手寫一份和 schema 平行的型別，兩邊遲早會不同步。

TypeScript 的具體寫法 pstack 另外放在 `typescript-best-practices` 技能裡，它不會自動載入，原文建議碰到 `.ts` 檔時明確打 `/typescript-best-practices`（在 Claude Code 是 `/pstack:typescript-best-practices`）。

## 三、Boundary Discipline

> **索引**：Wiring validation, error handling, or framework adapters. Guards at system boundaries, trust internal types, keep business logic pure.

```
  邊界（CLI 參數、設定檔、外部 API、網路協定）    內部
  ─────────────────────────────────          ──────────────────
  驗證、回傳錯誤、防禦性處理                     型別化的資料、錯誤傳遞
                                              不重複驗證，信任型別
                                              商業邏輯是純函式
```

一個例子：從環境變數讀設定。

```js
// config.mjs
export function parseConfig(env) {
  const errors = [];
  const port = Number(env.PORT ?? "8080");
  if (!Number.isInteger(port) || port < 1 || port > 65535) errors.push(`PORT must be 1-65535, got ${JSON.stringify(env.PORT)}`);
  const retries = Number(env.RETRIES ?? "3");
  if (!Number.isInteger(retries) || retries < 0) errors.push(`RETRIES must be a non-negative integer, got ${JSON.stringify(env.RETRIES)}`);
  if (errors.length) return { ok: false, errors };
  return { ok: true, config: Object.freeze({ port, retries }) };
}

// 內部：純函式，不再檢查 retries 是不是數字
export function backoffSchedule({ retries }) {
  return Array.from({ length: retries }, (_, i) => 100 * 2 ** i);
}
```

```js
// main.mjs：唯一碰 process.env 的地方
import { parseConfig, backoffSchedule } from "./config.mjs";
const parsed = parseConfig(process.env);
if (!parsed.ok) { for (const e of parsed.errors) console.error(`config: ${e}`); process.exit(2); }
console.log(`listening on ${parsed.config.port}, backoff ${backoffSchedule(parsed.config).join(",")} ms`);
```

```bash
node main.mjs; echo "exit=$?"
PORT=99999 RETRIES=two node main.mjs; echo "exit=$?"
```

```
listening on 8080, backoff 100,200,400 ms
exit=0
config: PORT must be 1-65535, got "99999"
config: RETRIES must be a non-negative integer, got "two"
exit=2
```

三個值得注意的設計：

- **一次報告所有錯誤**，而不是遇到第一個就停。使用者修一次就能把設定弄對。
- **錯誤訊息說出期望值和實際值**（`got "two"`）。
- **`backoffSchedule` 不檢查 `retries`**。原則的測試：「"Is this data crossing a system boundary right now?" If not, validation is redundant.」

（65535 是 TCP 埠號的上限，因為埠號是 16 位元無號整數；100 × 2ⁱ 是常見的指數退避，100ms 起跳是本例的選擇，不是標準。）

**擋的失敗**：agent 很喜歡「防禦性程式設計」——在每個函式開頭加一句 `if (typeof x !== "number") throw`。原文：「Scattered validation is noisy, redundant, and gives a false sense of safety.」內部到處都在檢查，代表沒有人確定邊界有沒有檢查過。這和 Bug fix playbook 說的「Belt-and-suspenders that 'might help' is a hypothesis, not a fix」是同一件事。

還有一條常被忽略的：「Do not re-export transport, storage, framework, or wire types through the public surface.」你的模組對外暴露的是領域概念（`Order`），不是 HTTP response 物件或資料庫 row。

## 四、Make Operations Idempotent

> **索引**：Designing commands, lifecycle steps, or loops that run amid crashes and retries. Converge to the same end state.

練習 A 整個就是這條原則。三個測試問題：

1. What happens if this runs twice in a row?
2. What happens if the previous run crashed at every possible point?
3. Does re-execution converge to the same end state?

核心做法：

> derive each step from the state that exists, not from what the previous run meant to do. Read the current state, compare it to the goal, and apply only the difference.

練習 A 的修正就是這句話的程式碼版本：不是「重送這一批」（上一次**打算**做的事），而是「問 sink 已經有幾列，只送剩下的」（**現存的**狀態）。

原則還有一段關於「殘留物」的規則，很值得記住：

> Anything a run leaves behind, such as a lock, a temporary file, a half-written record, or a claimed job, must be recognizable as stale by the next run. ... For example, write to a temporary path and rename it into place, or compare artifacts by content instead of by creation order.

一個沒辦法判斷是否過期的 lock 檔，會讓每一次崩潰都需要人手動清理。

## 五、Migrate Callers Then Delete Legacy APIs

> **索引**：Introducing a new internal API while old callers exist. Migrate and delete in one wave.

全文很短，重點是適用條件：

- 沒有外部使用者依賴向後相容
- 專案能吸收協調好的破壞性變更
- 新 API 是某個簡化或重構計畫的一部分

在這些條件下，**不要**留相容層、不要讓新舊 API 並存。原因和 Outcome-Oriented Execution 一樣：「暫時的」相容層會變成永久的技術債。

Refactoring playbook 第 5 步：「For API reshapes, migrate every caller and delete the old API in the same wave. No compatibility shims, no parallel old-and-new paths.」

實務上，這條原則和 Build the Lever 一起用：寫一支 codemod 把所有呼叫端遷移過去（Ch 12 的 rename.mjs 是最小的例子），用「舊 API 的呼叫次數 = 0」當作可以刪除的檢查條件，然後在同一個 PR 裡刪掉舊 API。

**它不適用的地方**同樣重要：公開的函式庫、有外部客戶的 API、跨團隊的服務介面——這些有外部使用者，需要棄用期。原則自己就把適用範圍寫在「When this applies」裡。

## 六、Separate Before Serializing Shared State

> **索引**：Concurrent actors might write the same file, branch, key, or object. Eliminate the sharing first.

> When concurrent actors might share mutable state, first ask whether they need the same mutable object. If not, eliminate the sharing. When sharing is real, enforce serialization structurally ... Instructions and conventions are not concurrency control.

最後一句「Instructions and conventions are not concurrency control」對 agent 特別重要：在 prompt 裡寫「請不要同時寫同一個檔案」不是並發控制。

### 實驗：兩個 worker、一個 state.json

這是原則全文裡的例子：「Two workers writing their own `lastX` field into one `state.json` is still shared mutation.」

```js
// shared.mjs：兩個 worker 各自更新同一個檔案裡自己的欄位（讀 → 改 → 寫）
import fs from "node:fs/promises";
const file = "state.json";
await fs.writeFile(file, JSON.stringify({}));
const tick = () => new Promise((r) => setTimeout(r, Math.random() * 5));

async function worker(field, n) {
  for (let i = 1; i <= n; i++) {
    const state = JSON.parse(await fs.readFile(file, "utf8"));
    await tick();                       // 模擬讀與寫之間的其他工作
    state[field] = i;
    await fs.writeFile(file, JSON.stringify(state));
  }
}
await Promise.all([worker("indexer", 50), worker("metrics", 50)]);
console.log("shared state.json:", await fs.readFile(file, "utf8"));
```

每個 worker 只寫**自己的**欄位，看起來互不干擾。跑三次：

```
<anonymous_script>:1


SyntaxError: Unexpected end of JSON input
    at JSON.parse (<anonymous>)
    at worker (file:///private/tmp/lab/ch13/race/shared.mjs:8:24)
    at async Promise.all (index 0)
    at async file:///private/tmp/lab/ch13/race/shared.mjs:14:1

Node.js v26.11.0
```

```
shared state.json: {"metrics":48,"indexer":50}
```

```
shared state.json: {"indexer":50,"metrics":50}
```

**三次執行，三種結果：**

1. **崩潰**：一個 worker 讀檔時，另一個 worker 正在寫，讀到了一個寫到一半的（空的）檔案。
2. **遺失更新**：`metrics` 停在 48，不是 50。indexer 讀了舊的 state（metrics 還是 47 或 48）、改了自己的欄位、寫回去，把 metrics 最新的值蓋掉了。
3. **剛好沒事**。

第三次的結果是最危險的：如果你只跑一次、剛好遇到第三種，你會以為程式是對的。原文的 Why：「Concurrent writes to shared state create race conditions that are intermittent, hard to reproduce, and expensive to debug.」

**直覺的修法是加鎖。** 原則說：「Treat 'we need a lock' as a design smell to check, not as the default answer.」先問：這兩個 worker 需要**同一個**物件嗎？它們寫的是兩個獨立的事實（indexer 的進度、metrics 的進度），只是剛好放在同一個檔案裡。拆開：

```js
// separate.mjs：每個 worker 擁有自己的檔案
import fs from "node:fs/promises";
const tick = () => new Promise((r) => setTimeout(r, Math.random() * 5));

async function worker(field, n) {
  const file = `${field}-state.json`;
  for (let i = 1; i <= n; i++) {
    await tick();
    await fs.writeFile(file, JSON.stringify({ last: i }));
  }
}
await Promise.all([worker("indexer", 50), worker("metrics", 50)]);
const read = async (f) => JSON.parse(await fs.readFile(`${f}-state.json`, "utf8")).last;
console.log("separate files:", JSON.stringify({ indexer: await read("indexer"), metrics: await read("metrics") }));
```

```
separate files: {"indexer":50,"metrics":50}
```

沒有鎖、沒有協調，問題整個消失。合併只在**讀取 / 報告的邊界**發生（最後那個 `read`）。原文：「`indexer-state.json` + `metrics-state.json` is not [shared mutation].」

（這個版本每個 worker 只是覆寫自己的檔案，單一寫入者覆寫自己的檔案仍然可能在崩潰時留下寫到一半的檔案——那是 Make Operations Idempotent 的問題，解法是寫暫存檔再 rename。兩條原則各管一件事。）

### 這條原則在 pstack 裡到處都是

| 場景 | 共享的東西 | pstack 的拆法 |
|---|---|---|
| arena 的多個候選 | 工作目錄 | 每個候選自己的 worktree 或 `/tmp/arena-<slug>/candidate-<n>/` |
| hillclimb 平行試假設 | 工作目錄 | 「fan them to parallel subagents, each in its own worktree」 |
| 多個 agent 在同一個 repo | working tree、port、build output | cloud subagent 或 worktree |
| 多個 run 共用決策日誌 | `decisions.tsv` | `.audit/<task-slug>.tsv`，每個任務一個檔 |
| Autopilot 的多個 PR | branch | 「One owner per PR. The owner is the only writer of its branch」 |

官方指南的 Pitfall：「Parallel agents in one worktree. They overwrite each other, and nobody can tell which agent changed what.」

## 對比與取捨

| 原則 | 它把東西放在哪 | 不放在哪 | 什麼時候不適用 |
|---|---|---|---|
| Model the Domain | 一個結構裡 | 散落的條件式 | 形狀已經清楚、局部、不太會長大 |
| Type System Discipline | 型別裡（編譯器檢查） | 執行期的檢查 | 無型別語言（但「在邊界解析」仍適用） |
| Boundary Discipline | 系統邊界 | 每一層 | — |
| Make Operations Idempotent | 從現存狀態推導 | 從上次的意圖推導 | 真正只會執行一次、不會重試的操作（罕見） |
| Migrate Callers Then Delete | 同一個 wave 裡 | 長期並存的相容層 | 有外部使用者需要棄用期 |
| Separate Before Serializing | 各自擁有的目標 | 共享的可變物件 | 單一寫入目標是真正的不變量時，結構化地序列化 |

## 踩雷集錦

1. **跑一次並發測試是綠的就以為沒問題**：本章的 shared.mjs 第三次執行完全正確。race condition 的特性就是「大部分時候沒事」。用壓力測試、重複執行、或像本章一樣插入隨機延遲放大它。
2. **用加鎖當預設答案**：鎖解決了 race，但帶來了死鎖、效能、鎖過期的問題。先問能不能拆開。
3. **在 prompt 裡寫「不要同時改同一個檔案」**：原文：「Instructions and conventions are not concurrency control.」要用結構（不同的 worktree、不同的檔案）擋。
4. **到處加型別檢查**：在內部函式開頭加 `if (typeof x !== "number")`，是 Boundary Discipline 說的「false sense of safety」。驗證放在邊界，內部信任型別。
5. **在有外部使用者時套用 Migrate Then Delete**：這條原則的適用條件明確寫了「No external users depend on backward compatibility」。公開 API 刪了就是破壞性變更。

## 進階：再往深一層

- **讓 shared.mjs 的 bug 穩定重現**：把隨機延遲改成固定的交錯順序（例如用兩個 Promise 手動控制哪個 worker 先讀、哪個先寫），讓「遺失更新」每次都發生。這是 Bug fix playbook 說的「synthesize the trigger, tighten conditions」。能穩定重現的 race，才能寫成回歸測試。
- **formal verification 的位置**：「兩個 retry 重疊」「lock 在寫入中途過期」這類 bug，測試很少碰得到。pstack 的 `/create-formal-verification-skill` 會用 TLA+ 之類的 model checker 檢查「一個 job 永遠不會同時被執行兩次」這種性質（Ch 21）。本章的 race 是最小的例子；真實系統的 race 狀態空間大到只能靠 model checker 窮舉。
- **`typescript-best-practices` 技能**：pstack 把型別原則的 TypeScript 語法細節（discriminated union、邊界用 `unknown`、窮舉、從 schema 推出型別）獨立成一個技能。讀它可以看到一條抽象原則怎麼落地成一門語言的具體規則。

## 動手練習

1. 照本章跑 shared.mjs 十次，記錄每一種結果出現幾次。然後把 `tick()` 拿掉再跑十次——結果的分佈變了嗎？為什麼拿掉延遲會讓 bug 更難出現（或更容易）？
2. 把 Ch 9 的 `statusLabel` 改寫成本章的 discriminated union 版本，對 16 種舊組合決定每一種要對應到哪一個新狀態。哪幾種組合你決定不了？那些就是 Never Block on the Human 說的「真正該問人的產品決定」。
3. 在你的專案裡找一個「內部函式在檢查參數型別」的地方，追到資料的來源：它是在哪個邊界進入系統的？那個邊界有驗證嗎？

## 本章重點整理

- Model the Domain：狀態放進結構（狀態機、查表、型別模型），依領域知識而不是執行順序組織模組
- Type System Discipline：品牌化原始型別、讓非法狀態無法表示、窮舉變體、從權威 schema 推型別——本章 TS 7 實際抓到三種錯
- Boundary Discipline：驗證只在邊界，內部信任型別、商業邏輯是純函式
- Make Operations Idempotent：從現存狀態推導下一步；殘留物要能被下一次執行判斷為過期
- Migrate Callers Then Delete：沒有外部使用者時，遷移與刪除在同一個 wave 完成
- Separate Before Serializing：先拆掉共享，「需要鎖」是一個要檢查的設計異味；本章的共寫實驗三次三種結果

## 自我檢核

- [ ] 不看本章，能說出六條 Architecture 原則各自管的是「什麼東西放在哪裡」嗎？
- [ ] 本章的 shared.mjs 每個 worker 只寫自己的欄位，為什麼還是會遺失更新？
- [ ] TS2366 那個錯誤擋掉的是哪一種未來的 bug？如果沒有它，那個 bug 會在什麼時候被發現？
- [ ] 你的團隊有沒有一個「暫時的」相容層已經存在超過半年？用 Migrate Callers Then Delete 的適用條件檢查：它真的需要存在嗎？

## 延伸閱讀

### 原始碼

- **六條 leaf 檔**：[model-the-domain](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-model-the-domain/SKILL.md)、[type-system-discipline](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-type-system-discipline/SKILL.md)、[boundary-discipline](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-boundary-discipline/SKILL.md)、[make-operations-idempotent](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-make-operations-idempotent/SKILL.md)、[migrate-callers-then-delete-legacy-apis](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md)、[separate-before-serializing-shared-state](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-separate-before-serializing-shared-state/SKILL.md)
- **[skills/typescript-best-practices](https://github.com/cursor/plugins/blob/main/pstack/skills/typescript-best-practices/SKILL.md)**
  - **讀哪裡**：整份
  - **能學到什麼**：Type System Discipline 在 TypeScript 的具體語法

### 文章

- **[Yaron Minsky — Effective ML（"Make illegal states unrepresentable"）](https://blog.janestreet.com/effective-ml-revisited/)** — Jane Street Tech Blog
  - **這篇說什麼**：「讓非法狀態無法表示」這句話的出處之一，用 OCaml 的型別示範
  - **和本章的關聯**：本章 discriminated union 例子的理論根源；不需要會 OCaml，概念直接對應到 TypeScript
- **[Alexis King — Parse, don't validate](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/)**（2019）
  - **這篇說什麼**：驗證只回答「對不對」，解析則把資料轉成一個「對」的型別，讓之後的程式碼不需要再檢查
  - **和本章的關聯**：Boundary Discipline 與 Type System Discipline「parse external data at boundaries」的完整論證；本章的 `parseConfig` 與 `parseUserId` 就是這篇的做法
- **[Martin Kleppmann — How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)**（2016）
  - **這篇說什麼**：分散式鎖為什麼比看起來難（lease 過期、時鐘飄移、fencing token）
  - **和本章的關聯**：說明為什麼 Separate Before Serializing 把「需要鎖」當成設計異味——鎖本身就是一個很難做對的系統

### 書籍

- **《Designing Data-Intensive Applications》** — Martin Kleppmann（O'Reilly, 2017）
  - **讀哪幾章**：Chapter 7「Transactions」的 lost update 一節（本章 shared.mjs 的正式名稱與各種解法）、Chapter 11「Stream Processing」的 idempotence 討論
  - **注意**：第二版正在撰寫中；第一版的這兩章內容仍然完全適用

下一章的五條 Verification 原則，定義了「什麼才算證據」。

→ [Ch 14 Verification 原則五條](./14-verification-principles.md)
