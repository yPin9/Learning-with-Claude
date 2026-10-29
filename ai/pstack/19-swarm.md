# Ch 19 — swarm：切片、競速與聚合

> **目標**：搞懂 swarm 和 arena 的根本差別——arena 讓多個 runner 做**同一件事**然後挑一個，swarm 讓多個 worker 做**不同的切片**（或在宣告好規則的前提下競速）然後彙總成一份報告。你會讀懂 swarm 的四個階段與它最嚴格的幾條規則（每個 worker 自己的輸出、結果要記錄 SHA 與方法、缺席是 gap 不是 pass），並用 shell 的平行執行實作官方範例「每個 package 一個 worker、一份報告」，包括一次重派、一個 gap、以及一次因為 SHA 過期而全部作廢重跑。

> **環境**：pstack 0.15.36、Node.js 26、POSIX shell（`xargs -P` 做平行）。本章用 shell 行程代替 subagent 示範 swarm 的**資料流與規則**；所有輸出都是實際跑出來的。真正的 swarm 由主 session spawn subagent 執行，行為依 swarm 原文描述，本章沒有實際 spawn。

## 為什麼需要這個？

有一類工作的價值在**覆蓋率**，不在設計：

- 檢查 monorepo 裡每一個 package 的測試
- 在一個 PR 上跑十條不同的驗證路徑（Multi-phase plan 要求每個 PR 十條 live lane）
- 對一個效能改善在足夠大的樣本上確認
- 對 app 做 fuzz，找回歸

這些工作可以完美地平行：每個切片彼此獨立。但平行化有自己的失敗模式——一個 worker 默默掛掉、一個 worker 量了錯的 commit、主 session 把十份原始報告全部貼進 context。swarm 是把「平行覆蓋」做對的那套規則。

官方指南的區分：

> Reach for it when parallelism buys coverage or lets independent checks race. `/arena` gives every worker the same design or code brief, then picks a base and grafts the best parts. `/swarm` covers slices or runs a race with a selection rule declared up front.

## 先建立直覺：arena vs swarm

```
  arena：同一題，挑最好的一份                 swarm：分頭做，彙總成一份
  ─────────────────────────                 ─────────────────────────
         同一份 brief                         切片 1   切片 2   切片 3   切片 4
       ┌────┼────┐                            │       │       │       │
       ▼    ▼    ▼                            ▼       ▼       ▼       ▼
      A    B    C                          worker  worker  worker  worker
       └────┼────┘                            │       │       │       │
            ▼                                 PASS   ISSUES  BLOCKED  （缺席）
      評審 → 挑底 → 嫁接                        └───────┴───┬───┴───────┘
            ▼                                            ▼
      一份合成的成品                          一張結果表 + 問題摘要 + gap 清單
```

swarm 還有第二種形狀：**競速（race）**——N 個 worker 拿同一份 brief，用一條**事先宣告**的規則決定結果（`first pass`、`rank all`、`best-of`）。它和 arena 的差別是：沒有挑底與嫁接的儀式，規則是機械的。

## 一、swarm 的四個階段

swarm 的 SKILL.md 很短（約 680 tokens），四個階段：

### Phase A：Frame

1. **說清楚完成條件，以及 swarm 要交回的成品或報告**
2. **選形狀**：切片、競速、或混合。競速或混合時，**spawn 之前**就宣告規則：`first pass`、`rank all`、或 `best-of`
3. **選 worker 的模型**：`swarm workers` 那一行（Ch 17），競速時每一條 arm 的模型要事先指名
4. **每個會寫東西的 worker 有自己的可寫輸出**。驗證或量測 commit 時，每份 brief 要寫出**確切的 SHA**；量測的 brief 還要寫出方法（樣本數、一個樣本是什麼、順序），worker 在結果裡記錄兩者

第 2 條「事先宣告」是在防一種很隱蔽的偏差：**看到結果之後才決定怎麼挑**。如果你在 race 跑完之後才決定「取最快的」還是「取中位數」，你會不自覺地挑一個讓你想要的結論成立的規則。

### Phase B：Fan out

在一則訊息裡 spawn 全部 N 個 worker（Cursor 版預設在 cloud，Claude Code 對應到 worktree，Ch 16）。

> Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

三種回報狀態的語意：

| 狀態 | 意思 | 例子 |
|---|---|---|
| `PASS` | 檢查跑了，而且通過 | 測試全綠 |
| `ISSUES` | 檢查跑了，找到可以證明的問題（**全部列出，不只第一個**） | 兩個測試失敗，各附訊息 |
| `BLOCKED` | 檢查沒辦法跑 | 缺環境變數、服務起不來 |

`BLOCKED` 和 `ISSUES` 的區分很重要：`ISSUES` 是關於**被檢查的東西**的資訊，`BLOCKED` 是關於**檢查本身**的資訊。把兩者混在一起，你會把「環境壞了」當成「程式碼壞了」。

### Phase C：Aggregate

> Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and respawn that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result.

四條規則：

1. **沒有記錄 SHA 與方法的結果，丟掉**
2. **重派一次**
3. **第二次還是沒有，記成 gap**
4. **gap 不算 pass**；覆蓋型的 swarm，每個必要切片都要有結果

「Do not paste raw worker dumps.」——主 session 不貼原始輸出（Guard the Context Window）。

### Phase D：Report

一份在對話裡的彙總報告：結果表、每個問題一行、gap 或缺席、以及（如果有競速）用了哪條規則。

## 二、實作：每個 package 一個 worker

官方的範例 prompt：

```text
/swarm check every package under packages/ against its check.sh. one worker per package. one report.
```

我們建一個小 monorepo，五個 package，刻意讓它們各自落在不同的狀態：

```
packages/
├── auth/check.sh       → 印 "auth: 12 tests ok"，exit 0
├── billing/check.sh    → 印兩個失敗，exit 1
├── cart/check.sh       → 印 "cart: 7 tests ok"，exit 0
├── profile/            → （worker 會在這裡「掛掉」，不產出任何結果）
└── search/check.sh     → 印「缺 ELASTIC_URL」到 stderr，exit 3
```

### worker

每個 worker 負責**一個切片**、寫**自己的檔案**、結果裡記錄 **SHA 與方法**：

```sh
#!/bin/sh
# worker.sh <package> <out-dir>：跑一個 package 的 check.sh，寫一份 JSON 結果
pkg="$1"; out="$2/$pkg.json"; sha=$(git rev-parse --short HEAD)
if [ "$pkg" = "profile" ]; then exit 0; fi          # 模擬一個中途掛掉、什麼都沒寫的 worker
if [ ! -x "packages/$pkg/check.sh" ]; then
  printf '{"slice":"%s","sha":"%s","method":"check.sh","status":"BLOCKED","evidence":"no check.sh"}\n' "$pkg" "$sha" > "$out"; exit 0
fi
log=$(sh "packages/$pkg/check.sh" 2>&1); code=$?
case $code in
  0) status=PASS ;; 1) status=ISSUES ;; *) status=BLOCKED ;;
esac
ev=$(printf '%s' "$log" | tr '\n' ';' | sed 's/"/\\"/g')
printf '{"slice":"%s","sha":"%s","method":"check.sh","status":"%s","exit":%s,"evidence":"%s"}\n' "$pkg" "$sha" "$status" "$code" "$ev" > "$out"
```

（exit code 的對應——0 是 PASS、1 是 ISSUES、其他是 BLOCKED——是本章為這個 monorepo 訂的約定，不是 pstack 的規定。真實的 swarm 由 worker 自己判斷並附證據。）

平行執行，最多 4 個同時跑：

```bash
ls packages | xargs -P 4 -I{} ../worker.sh {} /tmp/swarm-out
ls /tmp/swarm-out
```

```
auth.json
billing.json
cart.json
search.json
```

五個切片，只有四份結果。`profile` 的 worker 什麼都沒寫。

### aggregator

```js
// aggregate.mjs：讀每個切片的結果；SHA 不對或沒有結果的重派一次；第二次還沒有就記 gap
import fs from "node:fs";
import { execFileSync } from "node:child_process";

const [outDir] = process.argv.slice(2);
const slices = fs.readdirSync("packages");
const head = execFileSync("git", ["rev-parse", "--short", "HEAD"]).toString().trim();
const read = (s) => {
  try {
    const r = JSON.parse(fs.readFileSync(`${outDir}/${s}.json`, "utf8"));
    return r.sha === head && r.method ? r : null;
  } catch { return null; }
};
const rows = [];
for (const s of slices) {
  let r = read(s);
  if (!r) {
    console.error(`respawn ${s}: no valid result at ${head}`);
    execFileSync("../worker.sh", [s, outDir]);
    r = read(s);
  }
  rows.push(r ?? { slice: s, status: "GAP", evidence: "no result after one respawn" });
}
console.log(`swarm report at ${head}: ${slices.length} slices`);
console.log("| slice | result | evidence |\n|---|---|---|");
for (const r of rows) console.log(`| ${r.slice} | ${r.status} | ${r.evidence.slice(0, 70)} |`);
const pass = rows.filter((r) => r.status === "PASS").length;
const gaps = rows.filter((r) => r.status === "GAP").length;
console.log(`\n${pass} PASS, ${rows.length - pass - gaps} not passing, ${gaps} gap. Coverage complete: ${gaps === 0 ? "yes" : "no"}`);
process.exit(pass === rows.length ? 0 : 1);
```

```bash
node ../aggregate.mjs /tmp/swarm-out; echo "exit=$?"
```

```
respawn profile: no valid result at 5cd7b01
swarm report at 5cd7b01: 5 slices
| slice | result | evidence |
|---|---|---|
| auth | PASS | auth: 12 tests ok |
| billing | ISSUES | billing: rounding test failed: 10.005 -> 10.00, want 10.01;billing: ta |
| cart | PASS | cart: 7 tests ok |
| profile | GAP | no result after one respawn |
| search | BLOCKED | search: needs ELASTIC_URL, not set |

2 PASS, 2 not passing, 1 gap. Coverage complete: no
exit=1
```

這份報告示範了 swarm 的每一條規則：

- **billing 列出了全部兩個問題**（rounding 與 tax），不只第一個
- **search 是 BLOCKED，不是 ISSUES**：它缺環境變數，不代表 search 的程式碼有問題
- **profile 被重派一次，還是沒有，記成 GAP**
- **「Coverage complete: no」**：五個切片只有四個有結果，這個 swarm 的結論是「不完整」，不是「4/5 通過」
- **整份報告約 600 位元組**，不是五份原始 log

最危險的錯誤是把 profile 當成「沒有回報 = 沒有問題」。如果 aggregator 只數 ISSUES，它會說「只有 billing 有問題」，然後 profile 裡的任何 bug 都被略過了。

### 結果要綁定 SHA

現在有人修好了 billing，新增一個 commit。用**同一批舊結果**再彙總一次：

```bash
git commit -qam "fix(billing): rounding and EU tax"
node ../aggregate.mjs /tmp/swarm-out
```

```
respawn auth: no valid result at c9b6fad
respawn billing: no valid result at c9b6fad
respawn cart: no valid result at c9b6fad
respawn profile: no valid result at c9b6fad
respawn search: no valid result at c9b6fad
swarm report at c9b6fad: 5 slices
| slice | result | evidence |
|---|---|---|
| auth | PASS | auth: 12 tests ok |
| billing | PASS | billing: 9 tests ok |
| cart | PASS | cart: 7 tests ok |
| profile | GAP | no result after one respawn |
...
```

所有舊結果都記錄著 `5cd7b01`，HEAD 已經是 `c9b6fad`，全部作廢、全部重跑。這就是「each brief names the exact SHAs」的意義：**一個驗證結論只對一個特定的 commit 有效**。auth 和 cart 在新 commit 上的結果碰巧一樣，但那是重跑之後才知道的事，不是可以假設的事。

Shipping playbook 把這個想法推得更細：用 `git patch-id` 判斷一個 rebase 之後，舊的驗證結論是否還能沿用（Ch 24）。

## 三、競速：事先宣告規則

競速的形狀：N 個 worker 拿同一份 brief，用事先宣告的規則決定結果。

| 規則 | 意思 | 適合 |
|---|---|---|
| `first pass` | 第一個 PASS 的 worker 勝出，其他停止 | 找一個可行解（例如「找到一個能重現 bug 的輸入」） |
| `rank all` | 全部跑完，依量測結果排序 | 比較多個選項（例如多個模型在同一任務上的表現） |
| `best-of` | 全部跑完，取最好的一個 | 生成任務，品質差異大 |

「For a model race, name each arm's model up front.」如果你要比較兩個模型，事先指名每一條 arm 用哪個模型，避免事後挑選。

和 arena 對照：arena 也是「同一份 brief、多個 runner」，但它有評審、挑底、嫁接——它假設最好的結果是**合成**出來的。競速假設最好的結果**就是其中一個**，用機械的規則選出來。

## 四、swarm 在 pstack 裡的位置

swarm 很少單獨被叫用，它是其他流程的驗證引擎：

| 使用者 | 怎麼用 swarm |
|---|---|
| Multi-phase plan | 每個 PR 的「Verify, live」是十條 lane，「per the swarm skill, on the `swarm workers` model」 |
| Autopilot-full | 「Fan out independent verifiers at the code-ready SHA per the swarm skill」 |
| Shipping | 每個 PR 一個獨立的驗證者 |
| create-verification-skill 之後 | 「a /swarm can split a full pass by feature-map entry and aggregate the results」 |
| poteto-mode 的 Subagents | 「Verification is the exception and stays on the `swarm workers` model and speed」 |

最後一條值得注意：**驗證固定用 swarm workers 的模型**，不隨工作難度調整（Ch 17）。理由推測是：驗證要可比較，同一類驗證在不同的 PR 上用同一個模型，結果才有意義（這是推論，原文沒有說明理由）。

## 底層機制：為什麼「缺席」必須是一個獨立的狀態

```
  沒有 GAP 狀態的彙總                        有 GAP 狀態的彙總
  ──────────────────                        ─────────────────
  收到的結果：PASS PASS ISSUES BLOCKED        收到的結果：PASS PASS ISSUES BLOCKED
  沒收到的：profile                          沒收到的：profile → 重派 → 還是沒有 → GAP
        │                                          │
        ▼                                          ▼
  「只有 billing 有問題」                     「billing 有問題、search 無法檢查、
                                              profile 沒有被檢查」
  → profile 被當成通過了                      → 覆蓋不完整，結論不能當作全部通過
```

在一個 agent 系統裡，worker 缺席的原因很多：context 用完、工具錯誤、模型拒絕、cloud VM 掛掉。如果彙總只看「收到了什麼」，每一次缺席都會悄悄變成一次通過。

## 對比與取捨

| | arena | swarm（切片） | swarm（競速） |
|---|---|---|---|
| 每個 worker 的任務 | 相同 | 不同的切片 | 相同 |
| 結果怎麼組合 | 評審 + 挑底 + 嫁接 | 彙總成一張表 | 事先宣告的機械規則 |
| 價值來自 | 多樣的設計嘗試 | 覆蓋率 | 機率（某一個會成功）或比較 |
| 缺席怎麼處理 | 用 N-1 繼續，記錄 | 重派一次，再缺就是 gap | 依規則（first pass 可能不受影響） |
| 適合 | 設計、命名、演算法 | 測試矩陣、多路驗證、審計 | 找可行解、比較模型 |

官方 Pitfall：「Using `/arena` for coverage. `/arena` repeats one design or code brief, then picks a base and grafts the best parts. `/swarm` partitions slices or declared race arms and aggregates one report.」

## 踩雷集錦

1. **把缺席當成通過**：本章的 profile 沒有回報任何東西。沒有 GAP 狀態的彙總會說「只有 billing 有問題」。
2. **BLOCKED 和 ISSUES 混用**：search 缺環境變數是 BLOCKED。把它記成 ISSUES，你會派人去修一個沒壞的 package。
3. **只回報第一個問題**：「A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.」只報第一個，修完之後再跑一次 swarm 才發現第二個，浪費一整輪。
4. **沿用舊 commit 的結論**：本章新增一個 commit 之後，所有舊結果都作廢了。結論要綁定 SHA。
5. **跑完才決定競速規則**：看到結果再選「取最快」還是「取中位數」，是在挑一個讓你想要的結論成立的規則。spawn 之前宣告。

## 進階：再往深一層

- **同一台機器上平行量測會互相干擾**：本章的 `xargs -P 4` 平行跑檢查沒問題，但如果 worker 在**量效能**，四個同時跑會搶 CPU，每個量到的數字都不準。這是 swarm Phase A step 4 要求量測的 brief 寫出「method (sample count, what one sample is, order)」的原因之一；Cursor 版預設每個 worker 一台 cloud VM 也是。在 Claude Code 的 worktree 模式下，效能量測型的 swarm 要序列化，或放到不同的機器上。
- **把 aggregate.mjs 的 respawn 做成真的 subagent**：在 Claude Code 裡，主 session 可以讀 worker 的結果檔，對缺席的切片重新 spawn 一個 subagent。結果檔放在 `/tmp/swarm-<id>/`（每個 worker 自己的檔案），主 session 只讀那些 JSON，不讀 worker 的過程。
- **讀 Multi-phase plan 的十條 lane**：它要求其中一條是「Regression lane against trunk」——同一個場景在 trunk 與 PR head 上各跑一次。這讓 swarm 能區分「這個 PR 弄壞的」和「trunk 本來就壞的」（Ch 27）。

## 動手練習

1. 照本章建出 monorepo，跑一次 swarm。然後讓 `cart/check.sh` 隨機失敗（例如 `[ $((RANDOM % 2)) -eq 0 ] || exit 1`），連續跑五次。結果穩定嗎？swarm 的規則能幫你發現這是一個 flaky 的檢查嗎？如果不能，你會加什麼？
2. 修改 worker.sh，讓 BLOCKED 也附上**怎麼解除**的建議（例如「set ELASTIC_URL」）。修改 aggregate.mjs，把 BLOCKED 的建議彙總成一個「需要人處理」的清單。這對應 `/ketchup` 的哪個設計？
3. 為一個競速任務寫 Frame：「找出三個 JSON parser 函式庫中，哪一個在我們的 10MB fixture 上最快」。你選哪條規則？每條 arm 的 brief 要寫出哪些方法細節，結果才可比較？

## 本章重點整理

- arena 是同一題挑最好的並嫁接；swarm 是分頭做切片並彙總，或依事先宣告的規則競速
- 四個階段：Frame（完成條件、形狀、模型、每個 worker 自己的輸出、SHA 與方法）→ Fan out（每份 brief 獨立完整）→ Aggregate（無 SHA / 方法的丟掉、重派一次、再缺就是 gap、gap 不算 pass）→ Report（一張表，不貼原始輸出）
- PASS / ISSUES / BLOCKED：ISSUES 是被檢查的東西有問題，要列出全部；BLOCKED 是檢查本身跑不了
- 本章的實作：5 個切片得到 2 PASS、1 ISSUES、1 BLOCKED、1 GAP，結論是「覆蓋不完整」；新增 commit 後舊結果因 SHA 不符全部作廢

## 自我檢核

- [ ] 不看本章，能說出 swarm 彙總階段的四條規則嗎？
- [ ] 為什麼「gap 不算 pass」在 agent 系統裡特別重要？列出三種 worker 可能缺席的原因
- [ ] 一個 swarm 回報「4/5 PASS，1 BLOCKED」，你能說「這次改動是安全的」嗎？為什麼？
- [ ] 什麼時候用 arena、什麼時候用 swarm 的競速？各舉一個你工作中的例子

## 延伸閱讀

### 原始碼

- **[skills/swarm/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/swarm/SKILL.md)**
  - **讀哪裡**：全文（四個 phase）
- **[playbooks/multi-phase-plan.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/multi-phase-plan.md)**
  - **讀哪裡**：「Verification」段落與「Verify, live」的十條 lane 模板
  - **能學到什麼**：swarm 被用來驗證一個 PR 時，brief 要寫到多細

### 官方指南

- **[docs/guide/04-design.md「Cover slices and races with /swarm」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/04-design.md)**
  - **能學到什麼**：swarm 與 arena 的官方區分

### 文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** 的「Parallelization」一節
  - **這篇說什麼**：sectioning（把任務切成獨立的子任務平行做）與 voting（同一任務多次）
  - **和本章的關聯**：swarm 的切片形狀就是 sectioning，競速形狀是 voting 的一種；這篇也提到 guardrails 與 code review 適合用 sectioning
- **[Google — Testing on the Toilet / Flaky tests](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html)**
  - **這篇說什麼**：Google 內部 flaky test 的規模與處理方式
  - **和本章的關聯**：動手練習 1 的延伸——一個 swarm 的結論只和它的每一個檢查一樣可靠；flaky 的檢查會讓 PASS 與 ISSUES 都不可信

### 對照閱讀

- **[harness_engineering 練習 D — 用 subagent 做 multi-agent 研究工作流](../harness_engineering/practice-d-multi-agent-research.md)**（本 repo）
  - **和本章的關聯**：那個練習從 harness 層面實作平行 subagent 與結果合併；本章是 pstack 對這件事的規則

arena 與 swarm 都是「多個 agent 一起做」。下一章的兩個技能是「多個 agent 一起**拆**」：interrogate 讓對手去弄壞一個改動，blast-radius 找出改動會在哪裡造成破壞。

→ [Ch 20 interrogate 與 blast-radius：只留跑得出來的發現](./20-interrogate-blast-radius.md)
