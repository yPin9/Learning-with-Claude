# Ch 21 — 驗證 skill：讓 agent 自己檢查自己

> **目標**：理解 pstack 為什麼把「驗證」做成**專案自己的技能**：`/create-verification-skill` 產生一份教 agent 像使用者一樣驅動你的 app 的技能（Launch、Doctor、Drive、Evidence、Cleanup 加上 feature map），`/create-formal-verification-skill` 產生一份檢查「測試碰不到的性質」的技能。你會為一個小型 HTTP 服務手寫一份驗證技能並依規格「端到端證明一次」，看一個把證據放錯地方的 cleanup 如何吃掉證據，再用一支有界的窮舉檢查器在 409 個狀態中找出練習 A 那個 bug 的最短反例。

> **環境**：pstack 0.15.36、Node.js 26、curl。驗證技能與窮舉檢查器是本課作者依 pstack 規格手寫的（不是由 `/create-verification-skill` 產生），所有輸出都是實際跑出來的。本機沒有安裝 TLA+；本章的「形式化驗證」是一個在 JavaScript 裡做的有界窮舉，不是 model checker。

## 為什麼需要這個？

官方指南第 6 頁：

> Verification is the slowest step in most agent work, because it's the step that usually waits on a human. Make the agent able to do it, and you stop being the bottleneck. Skip it, and running more agents only gets you more unchecked work to review.

以及 setup 指南：

> If you're new to pstack, say yes. An agent that can check its own work keeps going until the check passes. An agent that can't hands every result back to you to check by hand. Of everything in this guide, the verification skill pays off the most.

Prove It Works 原則要求「檢查真實的產物」。但「真實的產物」常常是一個要啟動、要登入、要點按鈕的 app。如果每次 agent 都要自己摸索怎麼啟動、在哪個 port、要什麼測試帳號，它會：花大量 token 重新摸索；用不同的方式驅動 app，結果不可比較；摸索失敗時直接把驗證交回給你。

驗證技能把「怎麼驅動這個 app」寫成一份可以重複執行的說明書，**commit 進 repo**，讓每一個人、每一個 agent 都用同一種方式驗證。

## 先建立直覺：一份給新同事的「怎麼測這個 app」手冊

```
  .claude/skills/verify-<app>/
  ├── SKILL.md
  │   ├── Launch    怎麼從空的資料目錄啟動到「可用」（不只是 process 有回應）
  │   ├── Doctor    一個唯讀的檢查：這個 instance 值得驅動嗎？（版本對、port 是我們的）
  │   ├── Drive     用真實的指令 / selector 驅動（不是範例）
  │   ├── Evidence  要截取什麼、放在哪裡；證據的標準
  │   ├── Cleanup   只殺自己啟動的；清掉 instance 與暫存狀態，**留下證據**
  │   └── Helpers   附帶的腳本與怎麼呼叫
  └── features/
      ├── README.md             功能索引
      └── <feature>.md          每個功能一份：子功能、使用者怎麼到達、怎麼驅動、陷阱
```

這份技能是**寫給 agent 讀的**：「You write the generator's output for the next agent, not for a human: it will be read cold, mid-task, by an agent that has never seen the app.」

## 一、create-verification-skill：訪談 repo，不是訪談人

> Answer these from the codebase and only ask the user what you cannot observe.

它從 repo 回答五個問題：

| 問題 | 例子 |
|---|---|
| **Surface**：使用者實際碰到什麼？ | web UI、CLI/TUI、桌面 app、API、mobile、library |
| **Run**：怎麼在本機啟動？ | 優先用 repo 自己的 dev 指令（package scripts、Makefile、README） |
| **Drive**：agent 怎麼用程式驅動它？ | 先找現有的 harness（Playwright、expect、curl 得到的 endpoint）；沒有才用通用做法（瀏覽器 / CDP、tmux / PTY、HTTP） |
| **Observe**：能截取什麼證據？ | 截圖、終端機紀錄、response body、log、exit code、DB 狀態 |
| **Isolate**：能不能兩個 instance 並存？ | 不能的話要在技能裡寫明：「refusing to double-drive a shared instance beats corrupting the user's session」 |

還有一條前提：「If the checkout doesn't build or start as-is, fix that first (or report it precisely) before generating; a skill written against a broken base teaches wrong steps.」

### 各段落的嚴格要求

**Launch**：「Ready means usable, not just that a process answers. A database health check can pass while its init scripts are still running.」

**Doctor**：一個唯讀的檢查——process 在、版本對、port 是我們的、auth 有效。「An agent runs this first whenever anything looks off.」

**Drive**：「the harness recipe with real selectors/commands from this repo, not examples. Prefer stable handles (ARIA labels, data attributes, prompt strings, route paths) over coordinates and tab order.」

**Evidence** 的證據標準：

- 走真實的使用者路徑，不是內部 setter 或測試專用的 endpoint
- 截取**動作**與**結果狀態**，不只最後的畫面
- 驗證副作用（寫入的檔案、插入的列、送出的訊息），不只看得到的東西
- 只有在 production 邊界本來就隔離外部系統的地方才用 mock
- dry-run 要觀察它實際跳過了什麼，不要相信它的名字：「some dry-runs still touch the network or open a browser」

**Cleanup**：「Never kill by process name; kill what you started. Cleanup removes instances and scratch state, never the evidence.」

### 交付前必須證明一次

> Run its own instructions end to end once: launch, doctor, drive ONE mapped feature ..., capture evidence, clean up. After cleanup, confirm the evidence still exists at the named location — a cleanup that eats the proof fails this step. ... A generated skill that was never executed is a draft, not a deliverable.

## 二、實作：為一個 notes API 寫驗證技能

一個小型的 notes 服務：

```js
// server.mjs：GET /health、GET /notes、POST /notes；資料存在 NOTES_DATA 指定的目錄
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
const dataDir = process.env.NOTES_DATA ?? "./data";
const file = path.join(dataDir, "notes.json");
fs.mkdirSync(dataDir, { recursive: true });
if (!fs.existsSync(file)) fs.writeFileSync(file, "[]");
const VERSION = "1.4.0";
const server = http.createServer(async (req, res) => {
  const send = (code, body) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
  if (req.url === "/health") return send(200, { ok: true, version: VERSION, pid: process.pid });
  if (req.url === "/notes" && req.method === "GET") return send(200, JSON.parse(fs.readFileSync(file, "utf8")));
  if (req.url === "/notes" && req.method === "POST") {
    let raw = ""; for await (const c of req) raw += c;
    const { title } = JSON.parse(raw || "{}");
    if (!title?.trim()) return send(400, { error: "title is required" });
    const notes = JSON.parse(fs.readFileSync(file, "utf8"));
    const note = { id: notes.length + 1, title: title.trim() };
    notes.push(note); fs.writeFileSync(file, JSON.stringify(notes));
    return send(201, note);
  }
  send(404, { error: "not found" });
});
server.listen(Number(process.env.PORT ?? 0), "127.0.0.1", () => console.log(`notes ready on ${server.address().port}`));
```

幾個為驗證而做的設計：`PORT=0` 讓作業系統挑一個空的 port（兩個 instance 才能並存）；`NOTES_DATA` 讓每次驗證用一個全新的資料目錄；`/health` 回報版本與 **pid**（Doctor 用 pid 確認「這個 port 是我們啟動的那個 process」）。

### 技能本體（`.claude/skills/verify-notes/SKILL.md`，節錄）

```markdown
---
name: verify-notes
description: Drive the notes HTTP API the way a client does and capture proof. Use when a change touches server.mjs or note storage, or when asked to verify notes behavior.
---

# Verify notes

## Launch
Run `.claude/skills/verify-notes/scripts/verify.sh launch` from the repo root. It starts the server on a free
port with an empty data dir under `/tmp/verify-notes`, waits for the log line `notes ready on <port>`, and writes
the pid and port. Ready means `/health` answers with our pid.

## Doctor
`verify.sh doctor` reads `/health` and checks the pid is the one we started and the version is `1.4.0`.

## Drive
`verify.sh drive` creates a note through `POST /notes`, posts an empty title, lists notes, and copies the stored file.

## Evidence
Artifacts go to `artifacts/verify-notes/` in the repo, outside the run dir. A save response alone is not proof:
compare the list response and the file on disk.

## Cleanup
`verify.sh cleanup` kills the pid we started and removes `/tmp/verify-notes`. It never touches `artifacts/`.

## Helpers
`scripts/verify.sh launch|doctor|drive|cleanup`.
```

### helper 腳本

```sh
#!/bin/sh
# verify.sh launch|doctor|drive|cleanup : drive the notes API the way a client does
set -eu
RUN=${RUN:-/tmp/verify-notes}
EVIDENCE=${EVIDENCE:-$PWD/artifacts/verify-notes}
case "$1" in
  launch)
    rm -rf "$RUN" && mkdir -p "$RUN/data" "$EVIDENCE"
    NOTES_DATA="$RUN/data" node server.mjs > "$RUN/server.log" 2>&1 & echo $! > "$RUN/pid"
    for i in $(seq 1 50); do grep -q "notes ready on" "$RUN/server.log" 2>/dev/null && break; sleep 0.1; done
    sed -n 's/notes ready on //p' "$RUN/server.log" > "$RUN/port"; echo "launched pid=$(cat "$RUN/pid") port=$(cat "$RUN/port")" ;;
  doctor)
    h=$(curl -fsS "http://127.0.0.1:$(cat "$RUN/port")/health")
    echo "$h" | grep -q "\"pid\":$(cat "$RUN/pid")" && echo "$h" | grep -q '"version":"1.4.0"' && echo "doctor ok $h" || { echo "doctor FAIL $h"; exit 1; } ;;
  drive)
    base="http://127.0.0.1:$(cat "$RUN/port")"
    curl -sS -X POST "$base/notes" -d '{"title":"  Release checklist  "}' > "$EVIDENCE/create.json"
    curl -sS -o "$EVIDENCE/empty.json" -w '%{http_code}' -X POST "$base/notes" -d '{"title":"   "}' > "$EVIDENCE/empty.status"
    curl -sS "$base/notes" > "$EVIDENCE/list.json"
    cp "$RUN/data/notes.json" "$EVIDENCE/stored.json"
    echo "create: $(cat "$EVIDENCE/create.json")"; echo "empty title -> $(cat "$EVIDENCE/empty.status") $(cat "$EVIDENCE/empty.json")"
    echo "list: $(cat "$EVIDENCE/list.json")"; echo "on disk: $(cat "$EVIDENCE/stored.json")" ;;
  cleanup)
    kill "$(cat "$RUN/pid")" 2>/dev/null || true; rm -rf "$RUN"; echo "cleaned $RUN" ;;
esac
```

（50 × 0.1 秒是 Launch 最多等 5 秒的上限，這個值是本章訂的；腳本沒有在逾時時報錯，是一個刻意保留給動手練習的缺陷——一個逾時還繼續往下跑的 Launch，會讓 Doctor 在錯誤的時間點失敗。）

### 端到端證明一次

```bash
V=.claude/skills/verify-notes/scripts/verify.sh
$V launch && $V doctor && $V drive && $V cleanup
ls artifacts/verify-notes
```

```
launched pid=96069 port=61630
doctor ok {"ok":true,"version":"1.4.0","pid":96069}
create: {"id":1,"title":"Release checklist"}
empty title -> 400 {"error":"title is required"}
list: [{"id":1,"title":"Release checklist"}]
on disk: [{"id":1,"title":"Release checklist"}]
cleaned /tmp/verify-notes
--- evidence after cleanup:
create.json
empty.json
empty.status
list.json
stored.json
server gone, run dir gone: ls: /tmp/verify-notes: No such file or directory
```

每一個要求都對應到輸出裡的一行：

- **Launch 到「可用」**：等到 `notes ready on` 那行 log，而不是 process 一啟動就算
- **Doctor 確認是我們的 instance**：`/health` 回報的 pid 等於我們啟動的 pid
- **Drive 走真實的使用者路徑**：`POST /notes`，不是直接寫 `notes.json`
- **Evidence 驗證副作用**：不只看 POST 的 response，還比對 list 與**磁碟上的檔案**；「title 被 trim」這個行為在三處都一致
- **Evidence 涵蓋失敗路徑**：空標題回 400
- **Cleanup 只殺自己的 pid、清掉 run 目錄、證據留下**

### 一個吃掉證據的 cleanup

把證據目錄放進 run 目錄裡（一個很自然的錯誤：「所有暫存東西都放在一起」）：

```bash
export EVIDENCE=/tmp/verify-notes/evidence
$V launch >/dev/null && $V drive >/dev/null
ls $EVIDENCE | wc -l
$V cleanup
ls $EVIDENCE
```

```
evidence files before cleanup: 5
cleaned /tmp/verify-notes
ls: /tmp/verify-notes/evidence: No such file or directory
```

cleanup 正確地清掉了 run 目錄——連同證據一起。這正是規格要求「After cleanup, confirm the evidence still exists」的原因。一個沒有經過這一步證明的驗證技能，可能每次都「通過」，卻從來沒有留下任何人能事後檢查的東西。

### feature map

`features/README.md` 是功能索引，每個功能一份檔案，四個固定的 H2：`Sub-features`、`How to get to it (user POV)`、`Driving it with <harness>`、`Gotchas`。pstack 附的範例（`create-note.md`）有一條很好的 Gotcha：

> A save status alone is insufficient proof. Reopen the note from the list.

這和本章 Drive 比對 list 與磁碟檔案是同一個道理。另一條規則：「If the seed data lacks a precondition that a journey needs, the journey reports a failure and the reason goes in that feature's `Gotchas`. Never weaken the journey to make it pass.」

### 把它當成基礎設施

> Treat the verification skill as infrastructure, not a one-off. Commit it, so every person and every agent on the team drives the app the same way.

官方指南還建議在 agent 一直寫一次性腳本去點 app 時，做一個小的 **control CLI** 讓技能呼叫（Build the Lever）。一個 agent 用得好的 CLI 有這些特徵：少數幾個可組合、做實事的指令；破壞性動作有 `--dry-run`；子指令漸進揭露功能；錯誤訊息說出該怎麼做；豐富的 `--help`；機器可讀的輸出（如 JSON）。本章的 `verify.sh` 是一個最小的版本。

## 三、maintain-verification-skill：讓 feature map 保持誠實

app 會變，feature map 會過時。`/maintain-verification-skill` 每天跑一次（官方建議用排程自動化）：每個 feature 一個平行的唯讀 source reader，然後一次 live pass 驅動每個 mapped feature。它只會有三種結果：

| 結果 | 意思 |
|---|---|
| `clean` | 全部涵蓋，不用改 |
| `changed` | 一個 PR，內容是被證明的修正，**只限於驗證技能自己的目錄** |
| `blocked` | 說出擋住的原因 |

「It never edits product code. If the live pass catches a product regression, it reports the regression instead of hiding it with a doc edit.」——驗證技能發現 app 壞了，它回報 app 壞了，而不是把文件改成符合壞掉的行為。

## 四、formal verification：測試碰不到的性質

驗證技能證明的是**你驅動的路徑**。有些 bug 活在沒人驅動的路徑上：兩次重試重疊、lock 在寫入中途過期、狀態變化亂序到達。

`/create-formal-verification-skill` 從程式碼挑一個**目標**：「a promise the code makes, where a break harms users and tests rarely reach the case that breaks it. It picks the strongest target, not the easiest to verify.」然後依問題選工具：

- 「事件會不會以壞的順序發生」→ 對設計的小模型跑 model checker，預設 TLA+
- 「程式碼對每一個輸入都正確嗎」→ 直接在程式碼上跑的 verifier
- 沒有 verifier 支援你的語言 → 「tests the real code with generated inputs, not a hand-written model of the function」
- 一個在任何規模都要成立的性質 → 可能需要證明

它有一條和本課反覆出現的主題完全一致的要求：

> It verifies one property and shows that property failing on a broken variant of the code, because a check you've never seen fail proves nothing.

### 實驗：對練習 A 的 exporter 做有界窮舉

本機沒有 TLA+。依上面第三條（沒有 verifier 時，用產生的輸入測真實的程式碼），我們寫一支**有界窮舉**：在明確的範圍內列舉所有失敗點，檢查性質。

**性質**：每一列剛好出現一次，依序。
**範圍**：列數 0–9、batch 大小 1–4、最多一次失敗、失敗可以發生在任何一批的任何一列之前。

```js
// check.mjs：在範圍內窮舉所有「哪一批、哪一列之前斷線」，檢查每一列剛好一次、依序
const { exportRows } = await import(process.argv[2]);
const MAX_ROWS = 9, MAX_BATCH = 4;
function memSink(failAtBatch, failAfterRows) {
  const lines = []; let batch = 0, failed = false;
  return {
    lines,
    count: () => lines.length,
    writeBatch(rows) {
      batch++;
      rows.forEach((r, i) => {
        if (!failed && batch === failAtBatch && i === failAfterRows) { failed = true; throw new Error("reset"); }
        lines.push(r.id);
      });
    },
  };
}
let states = 0, shortest = null;
for (let n = 0; n <= MAX_ROWS; n++)
  for (let bs = 1; bs <= MAX_BATCH; bs++)
    for (let fb = 0; fb <= Math.ceil(n / bs) + 1; fb++)
      for (let fr = 0; fr < bs; fr++) {
        states++;
        const rows = Array.from({ length: n }, (_, i) => ({ id: i + 1 }));
        const sink = memSink(fb, fr);
        exportRows(rows, { sink, batchSize: bs });
        const ok = sink.lines.length === n && sink.lines.every((id, i) => id === i + 1);
        if (!ok && (!shortest || n < shortest.n)) shortest = { n, batchSize: bs, failAtBatch: fb, failAfterRows: fr, wrote: sink.lines };
      }
console.log(`${process.argv[2]}: ${states} states within rows<=${MAX_ROWS}, batch<=${MAX_BATCH}, one failure`);
console.log(shortest ? `COUNTEREXAMPLE ${JSON.stringify(shortest)}` : "property holds: every row exactly once, in order");
process.exit(shortest ? 1 : 0);
```

（`fb` 從 0 開始——0 代表不失敗——到 `ceil(n / bs) + 1`，多一個是為了涵蓋「失敗設在一個不存在的批次」這種不會觸發的情況。）

**先在壞的版本上看它失敗**（練習 A 的原始程式碼）：

```bash
node check.mjs ./buggy.mjs; echo "exit=$?"
```

```
./buggy.mjs: 409 states within rows<=9, batch<=4, one failure
COUNTEREXAMPLE {"n":2,"batchSize":2,"failAtBatch":1,"failAfterRows":1,"wrote":[1,1,2]}
exit=1
```

**最短反例**：兩列、batch 大小 2、第一批寫完第一列後斷線——寫出了 `[1, 1, 2]`。這比練習 A 的手寫測試（10 列、batch 4）小得多，而且是**最小的**會觸發 bug 的情況。用使用者的話說：「第一批寫到一半斷線，重試時把已經寫過的第一筆又寫了一次。」

**再在修好的版本上跑**：

```bash
node check.mjs ./fixed.mjs; echo "exit=$?"
```

```
./fixed.mjs: 409 states within rows<=9, batch<=4, one failure
property holds: every row exactly once, in order
exit=0
```

**誠實地說明它涵蓋了什麼**——formal verification 技能的交付要求：「The handoff says what was verified, with which tool and bounds, what the check found, and what it doesn't cover. A pass covers the model or the bound, not the whole system.」

- 驗證了：在列數 ≤ 9、batch ≤ 4、**最多一次**失敗的範圍內，每一列剛好一次、依序
- 工具：JavaScript 的有界窮舉，直接執行真實的 `exportRows`
- 沒涵蓋：兩次以上的失敗、`count()` 本身不正確的 sink、真實檔案系統的部分寫入（寫到一列的一半）、並發的多個 exporter

有界窮舉的力量來自「小範圍假說」（small scope hypothesis）：大部分 bug 在很小的輸入上就會出現。本章的反例用 2 列就觸發了，就是一個例子。

### statechart：讓非工程師看得懂

`/statechart` 從一個 TLA+ 模型產生 Mermaid 狀態圖，讓 PM 或設計師看到「使用者會看到什麼」，並把讀者的規則（「離線時你永遠不會看到 Synced」）變成不變量。圖上標出每個違反規則的狀態，報告給出到達它的最短路徑。「Before the hand-off, the agent reproduces each bug path in the real code, so a modeling error doesn't reach the reader as a bug.」本章沒有示範它（需要 TLA+ 工具鏈）。

## 對比與取捨

| 驗證方式 | 證明什麼 | 涵蓋範圍 | 成本 | 適合 |
|---|---|---|---|---|
| 單元測試 | 你寫到的案例 | 你想得到的 | 低 | 每個改動 |
| 驗證技能（driven） | 真實的使用者路徑在真實的 app 上可行 | feature map 列出的路徑 | 建立一次，之後低 | UI、CLI、API 的行為 |
| 有界窮舉 / property test | 一個性質在範圍內的**所有**情況成立 | 明確的範圍 | 中 | 重試、批次、解析器 |
| model checker（TLA+） | 設計層級的性質在所有交錯下成立 | 模型的範圍 | 高 | 並發、分散式、狀態機 |
| 證明 | 在任何規模下成立 | 全部 | 很高 | 必須在任何規模成立的性質 |

## 踩雷集錦

1. **Launch 只等 process 啟動**：process 有回應不代表可用。原文：「A database health check can pass while its init scripts are still running.」等一個代表「可用」的訊號。
2. **用程序名稱 kill**：`pkill node` 會殺掉使用者自己在跑的其他 node 程式。原文：「Never kill by process name; kill what you started.」本章用記錄下來的 pid。
3. **證據放在 cleanup 會刪的地方**：本章示範了 5 個證據檔被 cleanup 一起刪掉。證據要放在技能指定、cleanup 不碰的位置。
4. **把 feature map 改成符合壞掉的行為**：maintain 技能明確禁止：「it reports the regression instead of hiding it with a doc edit.」
5. **沒看過檢查失敗就相信它**：本章的窮舉檢查器先在壞的版本上找到反例，才在好的版本上的「property holds」有意義。一個從來沒失敗過的檢查器，可能根本沒有在檢查。

## 進階：再往深一層

- **用 `/pstack:create-verification-skill` 產生一份，和本章手寫的比較**：它會訪談 repo、寫出技能、證明一次。看它的 Drive 用了什麼 handle、Evidence 標準寫得多嚴、Cleanup 怎麼確保證據留下。在 Claude Code 上，依 adapter 它寫到 `.claude/skills/verify-<app>/`；社群移植版則固定叫 `verify`（Ch 4 的 substitution 規則）。
- **把窮舉檢查器擴到兩次失敗**：加一個迴圈讓第二次失敗發生在任意位置。狀態數會變成多少？修好的版本還成立嗎？（提示：想一想修正依賴的 `count()` 在兩次失敗之間是否仍然正確。）
- **讀 TLA+ 的入門資料**：如果你的系統有並發或分散式的部分，本章的 JavaScript 窮舉很快會不夠用（並發的交錯數會爆炸）。TLA+ 用一個小模型描述設計，TLC 會窮舉所有交錯。

## 動手練習

1. 照本章建出 notes 服務與 verify.sh，跑一次端到端證明。然後故意弄壞 server：讓 POST 回 201 但不寫磁碟。哪一個證據會暴露這個 bug？如果 Evidence 只檢查 POST 的 response，會發生什麼？
2. 為 notes 服務寫一份 `features/create-note.md`，用 pstack 的四個 H2。你的 Gotchas 裡至少要有一條是你在第 1 題才發現的。
3. 對練習 B 的 notify 服務寫一個有界窮舉：使用者數 0–5、失敗發生在任意一個使用者、最多一次重試。在 agent 的 PR 分支和參考修正上各跑一次。最短反例是什麼？

## 本章重點整理

- 驗證技能是專案自己的基礎設施：Launch（到可用）、Doctor（唯讀、確認是我們的 instance）、Drive（真實指令與穩定的 handle）、Evidence（真實路徑、動作與結果、副作用）、Cleanup（只殺自己的、留下證據）、Helpers，加上 feature map
- 交付前必須端到端證明一次，並在 cleanup 後確認證據還在；本章示範了一個吃掉證據的 cleanup
- maintain-verification-skill 只修技能自己、不改產品程式碼，發現回歸就回報
- formal verification 驗證一個性質在範圍內的所有情況，並先在壞的版本上看它失敗；本章的有界窮舉在 409 個狀態裡找到 2 列的最短反例
- 每一次驗證都要說清楚它涵蓋了什麼、沒涵蓋什麼

## 自我檢核

- [ ] 不看本章，能說出驗證技能的六個段落，以及每一段最容易犯的錯嗎？
- [ ] 為什麼規格要求「cleanup 之後確認證據還在」？如果省略這一步，可能發生什麼？
- [ ] 本章的窮舉檢查器「property holds」涵蓋了什麼、沒涵蓋什麼？你會怎麼向 PM 解釋這個結論的範圍？
- [ ] 你的專案裡，有沒有一個「測試很少碰到、壞掉會傷害使用者」的性質？它適合用哪一種驗證方式？

## 延伸閱讀

### 原始碼

- **[skills/create-verification-skill/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md)** 與 **[feature-map-example/](https://github.com/cursor/plugins/tree/main/pstack/skills/create-verification-skill/references/feature-map-example)**
  - **讀哪裡**：Section 2 的六個段落要求；範例的 `create-note.md`
- **[skills/create-formal-verification-skill/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/create-formal-verification-skill/SKILL.md)**
  - **讀哪裡**：怎麼挑目標、怎麼選工具、交付時要說明什麼

### 官方指南

- **[docs/guide/06-verify-and-ship.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md)**
  - **讀哪裡**：「Create a project verification skill」到「Show the behavior as a statechart」
  - **能學到什麼**：control CLI 的六個特徵；formal verification 的使用時機

### 書籍與文章

- **《Software Abstractions》修訂版** — Daniel Jackson（MIT Press, 2012）
  - **這本書的定位**：Alloy 的作者，提出「small scope hypothesis」——大部分 bug 在很小的範圍內就能找到
  - **讀哪幾章**：Chapter 5「Analysis」的 small scope hypothesis 一節
  - **和本章的關聯**：本章的有界窮舉在 2 列就找到反例，正是這個假說的例子
- **[Hillel Wayne — Learn TLA+](https://learntla.com/)**
  - **這是什麼**：目前最友善的 TLA+ 入門教材
  - **讀哪裡**：Core 部分的前幾章
  - **和本章的關聯**：pstack 的 formal verification 預設工具；當你的性質涉及並發交錯時，從這裡開始
- **[Chris Newcombe et al. — How Amazon Web Services Uses Formal Methods](https://cacm.acm.org/research/how-amazon-web-services-uses-formal-methods/)**（Communications of the ACM, 2015）
  - **核心貢獻**：AWS 用 TLA+ 在 S3、DynamoDB 等系統的設計中找到測試沒抓到的深層 bug
  - **讀哪裡**：整篇，特別是他們怎麼說服工程師採用、以及找到的 bug 的例子
  - **和本章的關聯**：formal verification 在生產系統上的成本與報酬的第一手報告

驗證讓 agent 能自己檢查行為。下一章處理最容易騙人的那一種證據：數字。

→ [Ch 22 benchmark-checklist：解釋那個數字](./22-benchmark-checklist.md)
