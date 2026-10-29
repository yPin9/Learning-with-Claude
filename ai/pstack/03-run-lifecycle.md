# Ch 3 — 一次 poteto-mode 執行的生命週期

> **目標**：從你按下 Enter 到 agent 回報完成，逐步追一次完整的 poteto-mode 執行（以 Feature playbook 為例），知道每一步發生在主 session 還是 subagent、產生什麼可檢查的產物、哪一步最容易被跳過。最後你會親手做一遍這次執行要求的驗證產物——一份 golden file 和一支會先紅後綠的驗證腳本。

> **環境**：pstack 0.15.36、Claude Code 2.1.295、Node.js 26。本章的 agent 對話與 todo 清單是**示意**（依 playbook 原文推演，不是錄下來的真實執行紀錄）；`wc-cli` 專案、驗證腳本與它們的輸出是**實際跑出來的**。

## 為什麼需要這個？

讀完 Ch 2 你知道 poteto-mode 有哪些規則，但規則是靜態的。真正用的時候你看到的是：一串 todo、幾個背景 subagent、一份很長的回覆。如果你不知道「正常的一次執行長什麼樣」，你就無法判斷它**哪裡偏離了**——而 pstack 的整套設計，就是要讓偏離能被看見。

## 先建立直覺：主 session 是工頭，subagent 是工班

```
  你 ──prompt──▶ ┌──────────────── 主 session（工頭）────────────────┐
                 │  讀 poteto-mode、原則索引、playbook                  │
                 │  重述目標 → 開 todo → 決定分工 → 審 diff → 驗證 → 回報 │
                 └───┬──────────────┬──────────────┬───────────────────┘
                     │ 讀程式        │ 設計          │ 寫程式
                     ▼              ▼              ▼
              ┌──────────┐   ┌──────────────┐  ┌─────────────┐
              │ how 的    │   │ architect →   │  │ poteto-agent │
              │ explorer  │   │ arena 的       │  │ （delegate） │
              │ （唯讀）  │   │ 2+ 個 runner   │  │  在 worktree │
              └──────────┘   └──────────────┘  └─────────────┘
                 回傳摘要          回傳設計稿         回傳 diff + 報告
```

工頭不搬磚，但工頭要**檢查每一塊磚**。主 session 的 context 裡留的是規劃、摘要、diff 與證據；大量讀檔、寫程式的工作在 subagent 的 context 裡發生，結束後只回傳精簡的結果（這是 Guard the Context Window 原則）。

## 任務設定

我們用官方 setup 指南的第一個範例當任務：

```text
/pstack:poteto-mode add a --json flag to this command. text output stays byte-identical. verify both.
```

被改的專案是一個小小的 `wc`（計算行數、字數、位元組數）CLI：

```js
// wc.mjs（改動前）
#!/usr/bin/env node
import fs from "node:fs";

function count(text) {
  const lines = text === "" ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
  const words = text.split(/\s+/).filter(Boolean).length;
  return { lines, words, bytes: Buffer.byteLength(text) };
}

const files = process.argv.slice(2);
for (const file of files) {
  const c = count(fs.readFileSync(file, "utf8"));
  console.log(`${String(c.lines).padStart(7)} ${String(c.words).padStart(7)} ${String(c.bytes).padStart(7)} ${file}`);
}
```

```bash
printf 'hello world\nsecond line here\n' > a.txt; printf 'one\n' > b.txt; printf '' > empty.txt
node wc.mjs a.txt b.txt empty.txt
```

```
      2       5      29 a.txt
      1       1       4 b.txt
      0       0       0 empty.txt
```

`padStart(7)` 是為了模仿 Unix `wc` 的右對齊欄寬；這個「7」正是「byte-identical」這個要求會保護的東西。

## 階段 0：載入（你看不到，但很重要）

```
  /pstack:poteto-mode ...
     │
     ├─ Claude Code 載入 poteto-mode/SKILL.md（約 5.4k tokens）
     ├─ Claude Code 版第一行要求：先讀 docs/harness/claude-code.md（對照表）
     └─ 讀 Principles 索引（已經在 SKILL.md 裡）
```

這一步沒有產物，但你可以從 transcript 看到 agent 有沒有去讀 adapter 文件。**如果它沒讀**，之後它看到 `Task` 這個 Cursor 名稱時可能會困惑或亂猜——這是 Claude Code 上最常見的第一個偏離。

## 階段 1：重述目標（第一則訊息）

Non-negotiables 的第一條：用自己的話說出目標、問題，以及預期的最小改動。不等確認。

（示意）

> 目標：讓 `wc` 能輸出機器可讀的 JSON，給要把結果接到其他程式的使用者用。限制是現有的文字輸出一個位元組都不能變，因為可能有腳本在解析它。預期的最小改動：在 `wc.mjs` 加一個 `--json` 旗標，把「計算」和「格式化」分開，加一支驗證兩種輸出的腳本。

這段話讓你在**任何程式碼產生之前**就能抓到誤解。例如如果你其實想要的是「JSON Lines，一行一個檔案」，這時候說一句就好；等它寫完再說，就是一輪重做。

## 階段 2：開 todo，原封不動抄 playbook

Feature playbook 有 8 步。poteto-mode 要求把它們**逐字**抄成 todo 的前幾項，再加任務特有的項目。

（示意，步驟文字縮寫）

```
[ ] 1. how over the affected subsystem
[ ] 2. architect for parallel design exploration
[ ] 3. throughput checkpoint（四項）
      [ ] Blocking first steps
      [ ] Independent workstreams
      [ ] Shared mutable state
      [ ] Smallest safe decomposition
[ ] 4. Delegate code-writing to a subagent ...（arena 若有多種合理形狀）
[ ] 5. Verify on the matching surface
[ ] 6. Rebase into small, ordered commits
[ ] 7. If the design is contested, interrogate before shipping
[ ] 8. Run Opening a PR
[ ] (task) 釘住現有文字輸出為 golden file
[ ] (task) 驗證 --json 可被解析且數字與文字輸出一致
```

> 在 Claude Code 裡，adapter 規定：session 有 `TodoWrite` 工具就用它；沒有就把清單寫在回覆裡、每個階段更新一次。你的環境有沒有這個工具，取決於 Claude Code 版本與設定，以實際看到的工具清單為準。

一個只改一個檔案的小功能，跑 8 步是不是太重了？這正是 `skip: <reason>` 的用途：

```
[x] 2. architect ... skip: 單一檔案、無跨函式邊界的新介面
[x] 3. throughput checkpoint
      [x] Blocking first steps: 先建 golden file
      [x] Independent workstreams: n/a: 單一檔案
      [x] Shared mutable state: n/a: 無並行
      [x] Smallest safe decomposition: 一個 worker 足夠，改動集中在 wc.mjs
[x] 7. interrogate ... skip: 設計無爭議
```

注意 throughput checkpoint 的四項**不能刪**，不適用就寫 `n/a: <理由>`（playbook 原文：「A dimension that genuinely does not apply ... keeps its item with `n/a: <reason>` rather than being dropped」）。你審查時一眼就能看出它想過什麼、跳過什麼。一份 todo 裡沒有任何 skip，對小任務來說反而可疑——它可能真的做了 8 步，也可能根本沒抄。

## 階段 3：理解（how）

對一個 20 行的檔案，`how` 技能的判斷是「Simple」：不開 explorer，直接一個 explainer 讀完解釋（how 的原文：「When in doubt, take the simple path.」）。產出是 Overview / Key Concepts / How It Works / Where Things Live / Gotchas 這幾段。

對這個任務，真正有價值的是 Gotchas：

（示意）

> Gotchas：輸出用 `console.log` 逐行印，每行後面自帶換行；空檔案的 `lines` 是 0 而不是 1；欄寬寫死成 7。任何把「計算」和「印出」分開的重構，都要確保最後一行仍然以換行結尾。

這條 gotcha 在階段 6 會變成真的 bug。

## 階段 4：命名資料形狀

「Any code → name the data shape first」。這個任務的資料形狀是：

```
  Row    = { file: string, lines: number, words: number, bytes: number }
  Format = "text" | "json"
  formatters: Record<Format, (rows: Row[]) => string>   ← 查表，不用 if/else
```

為什麼用查表（registry）而不是 `if (json) ... else ...`？Model the Domain 原則：「A map, registry, lookup table, or discriminated union instead of branching spread across files.」下一個人加 `--csv` 時，改的是一個表，不是在分支裡再塞一個 `else if`。對只有兩種格式的情況，這個選擇的收益不大——這正是 Model the Domain 也說的「Prefer boring code if the current shape is already clear, local, and unlikely to grow」。在這裡我們選查表，理由是「輸出格式」是典型會長大的維度。

## 階段 5：先建驗證，再委派

驗證的**手段**要在實作之前存在。這是 Foundational Thinking 的「scaffold first」，也是 Build the Lever 的「建一個能重跑的檢查，而不是做完再目測」。

先把現有的文字輸出存成 golden file：

```bash
mkdir -p golden && node wc.mjs a.txt b.txt empty.txt > golden/text.out
```

驗證腳本（兩項檢查，每項印出 PASS 或 FAIL，assertion 訊息本身說明在驗什麼，不寫「Phase 1」這種註解——呼應 poteto-mode 的 Comments 規則）：

```sh
#!/bin/sh
# verify.sh
set -eu
node wc.mjs a.txt b.txt empty.txt > /tmp/wc-text.out
if cmp -s golden/text.out /tmp/wc-text.out; then echo "PASS text output byte-identical to golden"; else echo "FAIL text output changed"; diff golden/text.out /tmp/wc-text.out; exit 1; fi
node wc.mjs --json a.txt b.txt empty.txt > /tmp/wc-json.out
node -e '
const rows = JSON.parse(require("fs").readFileSync("/tmp/wc-json.out", "utf8"));
const want = [["a.txt",2,5,29],["b.txt",1,1,4],["empty.txt",0,0,0]];
const got = rows.map(r => [r.file, r.lines, r.words, r.bytes]);
if (JSON.stringify(got) !== JSON.stringify(want)) { console.log("FAIL json rows", JSON.stringify(got)); process.exit(1); }
console.log("PASS json parses and matches text counts for", rows.length, "files");
'
```

**在功能還不存在時先跑一次**，確認它會失敗（失敗的檢查才證明它真的在檢查東西）：

```
PASS text output byte-identical to golden
node:fs:624
    return binding.readFileUtf8(path, stringToFlags(options.flag));
                   ^

Error: ENOENT: no such file or directory, open '--json'
    ...
exit=1
```

它把 `--json` 當成檔名去讀，失敗了。很好：文字檢查綠、JSON 檢查紅，符合「功能還沒做」的預期。這份驗證腳本先 commit，成為一個獨立的 commit（Sequence Work into Verifiable Units：「the failing test first, then the fix on top」）。

## 階段 6：委派實作，然後審 diff

主 session spawn 一個 `poteto-agent`，brief 大致包含（示意）：

```
目標：給 wc.mjs 加 --json，文字輸出位元組不變。（重述使用者目標）
範圍：只改 /path/to/wc-cli/wc.mjs
資料形狀：Row = {file, lines, words, bytes}；formatters 查表，key 為 "text" | "json"
成功條件：./verify.sh 兩項皆 PASS
已知 gotcha：每行必須以換行結尾，包括最後一行
poteto-mode 路徑：<絕對路徑>/skills/poteto-mode/SKILL.md（開工前讀完）
```

注意 brief 裡**傳的是路徑**，不是把 poteto-mode 全文貼進去。

假設 delegate 交回這個版本（這是我們**故意**做的錯誤版本，用來展示審查與驗證的價值）：

```js
const formatters = {
  text: (rows) =>
    rows.map((r) => `${String(r.lines).padStart(7)} ...${r.file}`).join("\n"),   // ← 少了結尾換行
  json: (rows) => JSON.stringify(rows, null, 2) + "\n",
};
```

它看起來完全合理。跑驗證：

```
FAIL text output changed
3c3
<       0       0       0 empty.txt
---
>       0       0       0 empty.txt
\ No newline at end of file
exit=1
```

就是階段 3 的那條 gotcha。**這個 bug 肉眼審 diff 幾乎看不出來**，終端機上的輸出也看起來一樣（最後少的是一個看不見的換行）。只有位元組層級的比對抓得到。這就是「text output stays byte-identical. verify both.」這句 prompt 的價值：它給了一個可以 pass/fail 的條件，而不是「應該沒問題」。

修正後的版本：

```js
const formatters = {
  text: (rows) =>
    rows.map((r) => `${String(r.lines).padStart(7)} ${String(r.words).padStart(7)} ${String(r.bytes).padStart(7)} ${r.file}`).join("\n") + "\n",
  json: (rows) => JSON.stringify(rows, null, 2) + "\n",
};

const args = process.argv.slice(2);
const format = args.includes("--json") ? "json" : "text";
const files = args.filter((a) => a !== "--json");
const rows = files.map((file) => ({ file, ...count(fs.readFileSync(file, "utf8")) }));
process.stdout.write(formatters[format](rows));
```

```
PASS text output byte-identical to golden
PASS json parses and matches text counts for 3 files
exit=0
```

## 階段 7：整理 commit

```bash
git log --oneline
```

```
8fb58e6 feat(wc): add --json output
09dc1b0 test: pin text output and add json check
2abf7ba wc: initial text output
```

順序本身是一個論證：先有一個釘住舊行為、而且對新功能會失敗的檢查，再有讓它通過的實作。審查者可以 checkout `09dc1b0` 看它紅，再 checkout `8fb58e6` 看它綠——「watch it go red, then green」（Sequence Work into Verifiable Units 原文）。

之後進入 Opening a PR playbook：從 worktree 開 PR、Conventional Commits 標題、PR 內文分 Why / What changed / Scope / Verification 幾段（Ch 24 詳談）。

## 階段 8：回報

最後的回覆要開頭重述目標、點名原則與它改變的決定、每個斷言帶證據（示意）：

> 目標是讓 `wc` 能輸出 JSON，同時不動現有的文字輸出，因為可能有腳本在解析它。
>
> 我加了 `--json`。現在計算結果先收成一組 row，再交給格式化表，`text` 與 `json` 各一個格式化函式。
>
> **Model the Domain** 讓我用查表而不是 if/else，下一個格式只要加一個 key。**Prove It Works** 讓我先存 golden file：第一版實作漏了結尾換行，`cmp` 抓到了（實測，verify.sh 輸出見下）。
>
> 驗證：`./verify.sh` 兩項 PASS（實測）。commit 順序是先檢查後實作，`09dc1b0` 在功能前會失敗（實測）。
>
> 沒做的事：沒有處理 `--json` 和檔名衝突的情況（例如真的有一個叫 `--json` 的檔案）。這是推論出的邊界，沒有測。

看幾個細節：原則點名後面接的是**具體改變了什麼**（查表而不是 if/else；先存 golden）；「實測」與「推論」標在同一句裡；主動列出沒做的事。

## 底層機制：這次執行裡，誰的 context 裝了什麼

| 位置 | 裝了什麼 | 大約多少 |
|---|---|---|
| 主 session | poteto-mode、adapter 對照表、Feature playbook、讀過的原則 leaf、how 的摘要、delegate 的 diff 與報告、驗證輸出 | 指引約 10k tokens（依檔案大小推算）+ 任務內容 |
| how explainer（subagent） | wc.mjs 全文、它自己的推理 | 結束後只回傳摘要 |
| poteto-agent（subagent） | poteto-mode 全文（它自己讀）、brief、wc.mjs、verify.sh、它的嘗試過程 | 結束後只回傳 diff 與報告 |

對 20 行的檔案，這個分工是過度的。對 2,000 行的子系統，差別就很大：探索與實作的大量 token 留在 subagent 裡，主 session 保持乾淨，才能在任務後段仍然清楚記得使用者的原始目標。

## 對比與取捨：同一個任務，三種做法

| 做法 | 產物 | 會抓到換行 bug 嗎？ | 代價 |
|---|---|---|---|
| 直接叫 Claude Code「加個 --json」 | 改好的 wc.mjs | 看運氣；多半它會說「我測過了，輸出一樣」 | 最低 |
| prompt 寫清楚完成條件（不用 pstack） | wc.mjs + 也許一個比對 | 如果它真的做了位元組比對就會 | 低 |
| `/pstack:poteto-mode` | golden file、verify.sh、兩個有順序的 commit、附證據的回覆 | 會，因為 Prove It Works 要求檢查真實產物，且驗證先於實作 | 高（tokens、subagent、時間） |

第二種做法其實已經拿到 pstack 大部分的好處——官方指南自己也這樣說：「Give the agent a goal and a way to check it, in your own words.」pstack 的額外價值是**不用你每次都記得寫**，而且把驗證變成流程的固定一步，而不是看 agent 當下心情。

## 踩雷集錦

1. **todo 裡沒有任何 `skip`**：對小任務，這通常表示它沒有真的照 playbook 抄，或者照抄了但每一步都形式化地「做了」。看 skip 理由是審查一次執行最快的方法。
2. **驗證腳本沒先跑紅**：一個從來沒失敗過的檢查，你不知道它到底有沒有在檢查東西。本章的 verify.sh 在功能前跑出 ENOENT，那一次失敗是證明它有效的證據。
3. **相信「我測過了」**：delegate 的報告說「text output unchanged」，主 session 照抄進回覆。poteto-mode 明確禁止：「Review the diff and write your own summary, don't pass through what it said.」主 session 要自己跑驗證。
4. **把 golden file 當成實作細節而不 commit**：它是審查者能重跑的證據。Prove It Works 原文：「keep its output as an artifact a reviewer can re-run instead of trusting your word.」
5. **小任務也硬跑 architect / arena**：一個單檔、無新介面的改動跑多模型設計比賽，是嚴謹稅付錯地方。Laziness Protocol 和 `skip: <reason>` 就是為了讓它可以合理地跳過。

## 進階：再往深一層

- **讀 transcript 檢查偏離**：Claude Code 的 session 紀錄在 `~/.claude/projects/<slug>/<session>.jsonl`。跑完一次 poteto-mode 後，用 `grep` 找 `docs/harness/claude-code.md`、`playbooks/feature.md`、`principle-` 有沒有被讀過。回覆裡點名了某條原則，transcript 裡卻沒有讀那個 leaf 檔——這就是 Ch 2 那條「只能引用讀過的原則」被違反的證據。
- **把 verify.sh 擴成 property test**：三個固定檔案只覆蓋三種情況。可以隨機產生內容，斷言 `--json` 的數字永遠等於文字輸出的數字。這是 Test Behavior, Not Implementation 的延伸（Ch 14）。

## 動手練習

1. 照本章重建 `wc-cli`，自己寫一個**不同的**錯誤版本（例如把欄寬改成 8、或把 `empty.txt` 的 lines 算成 1），確認 verify.sh 會抓到，並記下它印出什麼。
2. 對同一個任務，在 Claude Code 裡分別試「直接叫它加 --json」和「`/pstack:poteto-mode` + 本章的 prompt」，比較兩次的 todo、commit、回覆。記下哪一次出現了 golden file。
3. 寫下你預期一次 Bug fix playbook 執行的 todo 長什麼樣（Bug fix 有 6 步，原文在 `$PSTACK/skills/poteto-mode/playbooks/bug-fix.md`），哪幾步你預期會被 skip？

## 本章重點整理

- 一次執行的骨架：載入 → 重述目標 → 原封不動抄 playbook 進 todo → 理解 → 命名資料形狀 → 先建驗證 → 委派實作 → 審 diff 並親自驗證 → 有順序的 commit → 附證據的回覆
- 主 session 規劃、審查、驗證；subagent 探索、實作；大量 token 留在 subagent
- 驗證手段要在實作前存在，而且要先看它失敗一次
- `skip: <reason>` 與 `n/a: <reason>` 是你審查一次執行最快的入口

## 自我檢核

- [ ] 不看本章，能依序說出一次 Feature 執行的主要階段，並指出哪些在 subagent 裡發生嗎？
- [ ] 為什麼驗證腳本要在功能存在之前先跑一次？如果它一開始就是綠的，代表什麼？
- [ ] 本章的換行 bug，為什麼肉眼審 diff 很難發現？你最近有沒有遇過類似「看起來一樣、位元組不一樣」的問題？
- [ ] 一份最後回覆裡，你會先檢查哪三個地方來判斷這次執行有沒有照規矩來？

## 延伸閱讀

### 原始碼

- **[playbooks/feature.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/feature.md)**
  - **讀哪裡**：整份，特別是 step 3（throughput checkpoint）與 step 4（委派的 brief 要包含什麼）
  - **能學到什麼**：本章示意的 todo 的原文版本
  - **前提**：讀完本章
- **[skills/how/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/how/SKILL.md)**
  - **讀哪裡**：「1. Assess complexity」
  - **能學到什麼**：Simple 與 Complex 兩條路徑怎麼分；為什麼「When in doubt, take the simple path」

### 官方指南

- **[docs/guide/01-setup.md「Run your first task」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/01-setup.md)**
  - **讀哪裡**：最後一節
  - **能學到什麼**：本章任務的出處，以及官方對「watch the todo list」的說明
- **[docs/guide/06-verify-and-ship.md「State the finish condition up front」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md)**
  - **讀哪裡**：第一節的「Match the check to the change」清單
  - **能學到什麼**：CLI、UI、parser、perf、storage 各自該用什麼驗證；本章的 byte-identical 比對屬於「A CLI change runs the real command」

### 對照閱讀

- **[harness_engineering Ch 28 — Planning 與 todo 管理](../harness_engineering/28-planning-todo.md)**（本 repo）
  - **和本章的關聯**：todo 為什麼能讓長任務不迷路；poteto-mode 的「原封不動抄 playbook」是 todo 設計的一種強約束版本

這次執行假設一切都在 Claude Code 裡順利運作。但這些技能是為 Cursor 寫的——它們到底怎麼在 Claude Code 裡動起來？

→ [Ch 4 跨 harness 轉譯：官方 adapter vs 社群移植版](./04-cursor-to-claude-code.md)
