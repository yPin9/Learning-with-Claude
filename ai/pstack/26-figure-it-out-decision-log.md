# Ch 26 — figure-it-out 與決策日誌

> **目標**：讀懂兩個讓「你不在場時做的工作」變得可以審查的技能。`figure-it-out` 在沒有現成 playbook 適用時，先**設計一份流程**再開始做：框定（可證偽的完成條件、量化的範圍、嚴謹度）、設計工作流、跑假設迴圈、留下決策軌跡、驗證後交回。`show-me-your-work` 定義那份軌跡：一個 TSV、每個決定一列、證據是指標不是散文、只能追加、交回前要和 transcript 對帳、要由另一個家族的模型審過。你會驗證 `log.sh` 的防禦行為（包括防止試算表公式注入），並寫一支稽核工具在真實的日誌裡抓到一列引用了不存在的 commit 的紀錄，再用「追加一列取代它」而不是「改掉它」修正。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26。`log.sh` 是 pstack 附的原始腳本；稽核工具是本課寫的；所有輸出都是實際跑出來的。

## 為什麼需要這個？

Ch 25 的迴圈跑了一整晚。早上你面對的是：一個 branch、幾十個 commit、一份幾百 MB 的 transcript。你要怎麼判斷這個晚上的工作可不可以信任？

- 重讀 transcript：幾個小時，而且你會在第三十分鐘開始跳著讀
- 只看最後的結果：「predicate 通過了」——但你不知道它中間做了什麼決定、跳過了什麼、有沒有走捷徑
- 讀 agent 的總結：作者寫的總結，是作者想讓你看到的那個故事

pstack 的答案是一份**在過程中寫下的、每一列都附證據指標的決策日誌**，加上一個**交回前的自我對帳**與一個**另一家族模型的審查**。

## 先建立直覺：飛行紀錄器，不是機長的回憶錄

```
   機長的回憶錄（agent 的總結）            飛行紀錄器（決策日誌）
   ──────────────────────────            ──────────────────────
   事後寫                                  過程中寫，每個決定一列
   一個連貫的故事                           離散的紀錄，每列獨立
   「我們順利完成了遷移」                    ts / phase / decision / why / evidence / result
   無法對照                                 evidence 是一個指標：commit、file:line、artifact
   可以修改                                 只能追加：錯的紀錄由新的一列取代，舊的不刪
                                          交回前：和 transcript 對帳；另一家族的模型審一次
```

## 一、figure-it-out：沒有 playbook 時，先設計一份

> When the task matches no playbook, design one. The deliverable before any code is the workflow itself.

poteto-mode 的路由規則（Ch 5）把三種情況導到這裡：沒有 playbook 適合；大型或跨切面的工作（即使 Feature 也適用）；**使用者要離開、之後回來審**的工作。第三種最常見——Ch 25 的過夜契約就會走到這裡。

todo 的第一項是「read the Principles section of the poteto-mode skill」，然後是五個 phase。

### Phase A：Frame——先接地，再承諾

開始之前，必須能說出三件事：

- **完成的定義，寫成一個可證偽的 predicate**（Prove It Works）
- **範圍，量化的**：大概多少單元、多少工作量，以及接地時發現的阻礙
- **嚴謹度，偏高**：「One-way doors and high blast radius get more. Reversible low-stakes steps get less. Rigor is gates and artifacts, not 'try harder'.」

最後一句很重要：**嚴謹度不是態度，是關卡與產物**。「這次要特別小心」沒有意義；「每一個單元都要過 pixel diff、每一個 PR 都要 swarm 驗證」才有意義。

還有一個平衡：「Reversible work proceeds (the **never-block-on-the-human** principle skill), but a multi-hour run earns one checkpoint.」——一個要跑好幾個小時的工作，值得在開始前讓人看一次框定與取捨。這是 pstack 少數主動要求停下來給人看的地方。

### Phase B：設計工作流

- 拆成**原子的、可以獨立落地的單元**；**風險最大的未知先做**；scaffold 與驗證先於功能（Foundational Thinking）
- **驗證 harness 先於工作**，baseline 從改動前的狀態擷取，「so the check reads as 'old value vs new value'」
- 單向門的設計決定跑 architect；機械性的工作不要跑（「A second arena over a settled design is over-engineering」）
- 決定什麼要 fan out：只沿著接縫平行化，每個 worker 自己的 worktree；「Don't over-fan」
- **把設計好的 phase 清單寫下來**：「That list is what the human reviews.」

### Phase C：跑迴圈

> Each unit is an experiment. State the hypothesis, make the smallest change, measure against the predicate on the real artifact, keep it if it advanced, revert it if it didn't.

三條特別的規則：

- **「When something passes too easily, suspect the observation method before the system.」**
- **委派的工作配一個判官**：「If a worker games the gate, reset and harden the contract. If the gate itself is wrong, fix the gate in its own change rather than routing around it.」
- **結論只有三種**：VERIFIED、NOT VERIFIED、INCONCLUSIVE。「Inconclusive is not a pass. Don't hide a negative.」

「worker 鑽閘門的漏洞」是長時間執行裡很真實的失敗：一個被要求「讓測試通過」的 worker，可能會改測試。figure-it-out 的回應是**強化合約**（例如把測試放在 worker 的寫入範圍之外，Ch 12 Build the Lever 的「Keep it outside the delegates' write scope」），而不是事後抓。

### Phase D：決策軌跡

用 show-me-your-work 記錄。「figure-it-out's work is usually ambitious enough to commit the trail so the reviewer can read it in the PR.」

### Phase E：驗證並交回

對**真實的產品**（不只是 harness）檢查 Phase A 的 predicate；把反覆出現的修正編碼成關卡、lint、檢查或腳本。

**Reply**：你設計的 playbook、嚴謹度與理由、決策軌跡的路徑、對 predicate 驗證了什麼、還有什麼沒解決。

## 二、show-me-your-work：一份 TSV

### 格式

```
ts	phase	decision	why	evidence	result
```

| 欄位 | 內容 | 規則 |
|---|---|---|
| `ts` | ISO8601 時間戳 | |
| `phase` | 階段或工作流 | 只有一個保留值：`start`（見下文） |
| `decision` | 選了什麼或做了什麼，一行 | |
| `why` | 用平白的話說的理由 | 「If a principle drove it, say it plainly, not as a jargon tag.」 |
| `evidence` | 證明它的連結或路徑：commit SHA、PR 編號、`file:line`、artifact | **「Never a paragraph.」** |
| `result` | 結果或 predicate 狀態 | `tests green`、`reverted`、`pixel-diff 0`、`INCONCLUSIVE`、`open` |

「Write each entry the way you'd tell a teammate what you did. Plain words, concrete actions, no AI speak or abstract jargon.」原文的範例：

```
2026-05-24T12:30:00Z	widget	threw out a helper's work because its screenshots were blank	checked the real files instead of trusting its summary	worktree reset	reverted, tightened the instructions for next time
```

**記什麼**：決策點與檢查點，不是每一個動作——一個被選擇的岔路、一個完成並附驗證結果的單元、一次 pivot 或 revert 與它的觸發、一個浮出的阻礙、一個被修好的關卡。迴圈執行時，每次迭代一列。

### log.sh：一支會保護你的小腳本

pstack 附了 `scripts/log.sh <logfile> <phase> <decision> <why> <evidence> <result>`。它做四件事：蓋時間戳、第一次使用時寫表頭、去掉 tab 與換行、**把開頭是 `=`、`+`、`-`、`@` 的格子加上一個單引號**。

最後一件需要解釋。腳本裡的註解：

> The skill expects this log to be read in spreadsheets, so attacker-controlled evidence (PR titles, filenames, generated text) must not become formula execution when a reviewer opens the file.

這是 **CSV / TSV 公式注入**：一個以 `=` 開頭的格子，在 Excel 或 Google Sheets 裡會被當成公式執行。決策日誌的 evidence 常常來自外部（一個 PR 標題、一個檔名、一段生成的文字），而 reviewer 會用試算表打開日誌。一個惡意的 PR 標題 `=HYPERLINK("http://evil.example","click")` 就可能變成一個可點擊的連結（或更糟的公式）。

實際跑三次：

```bash
LOGSH=$PSTACK/skills/show-me-your-work/scripts/log.sh
bash $LOGSH d.tsv frame "counted the callers first" "wanted the size before a long run" "grep output, 4 files" "4 callers"
bash $LOGSH d.tsv migrate "moved a.mjs
to parseNew	with a tab" "one caller per iteration" "commit 35071f0" "fixtures pass"
bash $LOGSH d.tsv triage "copied a PR title into the log" "the title came from an untrusted PR" "=HYPERLINK(\"http://evil.example\",\"click\")" "-1 regressions"
cat -et d.tsv
```

（第二次的 decision 參數裡刻意放了一個換行與一個 tab。`cat -et` 把 tab 顯示成 `^I`、行尾顯示成 `$`。）

```
ts^Iphase^Idecision^Iwhy^Ievidence^Iresult$
2026-10-28T13:42:51Z^Iframe^Icounted the callers first^Iwanted the size before a long run^Igrep output, 4 files^I4 callers$
2026-10-28T13:42:51Z^Imigrate^Imoved a.mjs to parseNew with a tab^Ione caller per iteration^Icommit 35071f0^Ifixtures pass$
2026-10-28T13:42:51Z^Itriage^Icopied a PR title into the log^Ithe title came from an untrusted PR^I'=HYPERLINK("http://evil.example","click")^I'-1 regressions$
```

- 表頭在第一次寫入時自動加上
- 換行與 tab 被換成空格，每一列仍然是一行、六個欄位（否則一個換行就會把一列切成兩列，讓整個 TSV 錯位）
- `=HYPERLINK(...)` 與 `-1 regressions` 都被加上了 `'`，在試算表裡會被當成文字

（注意 `-1 regressions` 也被加了引號——它不是惡意的，但以 `-` 開頭的格子在某些試算表裡會被當成公式。這個規則寧可多保護。）

參數數目不對時：

```
usage: log.sh <logfile> <phase> <decision> <why> <evidence> <result>
exit=1
```

### 只能追加

> Append-only. A wrong call gets a new row that supersedes it. Never edit or delete history.

改掉一列錯的紀錄，會讓日誌變成「事後整理過的故事」。追加一列取代它，讓 reviewer 看到**錯誤本身**與**它怎麼被發現、被修正**——這通常是整份日誌裡最有價值的資訊。

### 多個 run 寫同一份日誌：`start` 列

一個 run 是一次 agent 對話（包括它後來的回合與它的摘要）。接手、替換的 agent、新的對話都是新的 run。當一個 run 寫入一份已經有內容的日誌時，它的第一列 phase 是 `start`，並說明這一列之前、不是它寫的那些列的時間範圍；evidence 寫這個 run 的識別（例如 agent id）。

這讓稽核可以知道哪些列是哪一個 run 寫的——下一節的對帳只檢查**這個 run 自己的**那幾段。

### 放在哪裡

預設是工作產物、不 commit：工作目錄的 `decisions.tsv`，或多個工作同時進行時的 `.audit/<task-slug>.tsv`（Separate Before Serializing Shared State），而且不進 git。只有在工作夠大、reviewer 需要軌跡才能信任結果時才 commit。

## 三、交回前：和 transcript 對帳

> At the end of the run, before handing back, check the log told the truth.

對照這個 run 的 transcript，逐列檢查：

- 每一列都對應一個真實的決定或動作
- **每一列的 evidence 都能解析，而且顯示了那一列宣稱的東西**
- 一個塑造了工作、卻沒有被記錄的岔路、pivot 或被放棄的做法，是一個缺口，補上

> Correct the log, not the story. The audit never edits or removes a row, even an invented one.

### 寫一支稽核工具

「evidence 能解析」這一條可以機械地檢查。寫一支工具：commit SHA 要存在、路徑要存在、evidence 不能是散文。

```js
// audit-log.mjs <log.tsv> <repo>：檢查每一列的 evidence 指標是否解析；被後面的列取代的不算
import fs from "node:fs";
import { execFileSync } from "node:child_process";
const [log, repo] = process.argv.slice(2);
const [header, ...rows] = fs.readFileSync(log, "utf8").trim().split("\n").map((l) => l.split("\t"));
const col = Object.fromEntries(header.map((h, i) => [h, i]));
let bad = 0;
rows.forEach((r, i) => {
  const ev = r[col.evidence];
  const problems = [];
  for (const [, sha] of ev.matchAll(/commit ([0-9a-f]{7,40})/g)) {
    try { execFileSync("git", ["-C", repo, "cat-file", "-e", `${sha}^{commit}`], { stdio: "ignore" }); }
    catch { problems.push(`commit ${sha} does not exist`); }
  }
  for (const [, p] of ev.matchAll(/(\S+\.(?:mjs|sh|tsv|md|json|png))/g))
    if (!fs.existsSync(p) && !fs.existsSync(`${repo}/${p}`)) problems.push(`path ${p} does not resolve`);
  if (!/commit |\.\w+/.test(ev)) problems.push("evidence is prose, not a pointer");
  const superseded = rows.slice(i + 1).some((later) => problems.some((p) => later[col.decision].includes(`supersedes the ${p.split(" ")[1]} row`)));
  if (problems.length && superseded) { console.log(`row ${i + 2} [${r[col.phase]}] superseded later: ${problems.join("; ")}`); return; }
  if (problems.length) { bad++; console.log(`row ${i + 2} [${r[col.phase]}] ${r[col.decision]}: ${problems.join("; ")}`); }
});
console.log(`${rows.length} rows, ${bad} with unresolved evidence`);
process.exit(bad ? 1 : 0);
```

拿 Ch 25 迴圈的真實日誌，在最後加一列**捏造的**紀錄（一個 agent 宣稱它在某個 commit 上跑了完整的測試，但那個 commit 不存在）：

```bash
bash $LOGSH run.tsv verify "ran the full fixture suite on main" "final check before hand-back" "commit deadbee" "tests green"
node audit-log.mjs run.tsv /tmp/lab/ch25/app; echo "exit=$?"
```

```
row 7 [verify] ran the full fixture suite on main: commit deadbee does not exist
6 rows, 1 with unresolved evidence
exit=1
```

前五列（Ch 25 的真實紀錄）的 commit 都存在；第六列被抓到了。依「correct the log, not the story」，不刪它，追加一列取代它：

```bash
bash $LOGSH run.tsv verify "supersedes the deadbee row: the fixture suite ran on 8677205, not deadbee" \
  "audit found the earlier row cited a commit that does not exist" "commit 8677205" "tests green"
```

```
verify	ran the full fixture suite on main	commit deadbee
verify	supersedes the deadbee row: the fixture suite ran on 8677205, not deadbee	commit 8677205
```

再跑稽核。第一版的工具在這裡仍然會失敗——因為錯的那一列還在（只能追加）。所以工具本身也要理解「取代」：

```
row 7 [verify] superseded later: commit deadbee does not exist
7 rows, 0 with unresolved evidence
exit=0
```

（這支工具用「`supersedes the <sha> row`」這個字串辨識取代關係，是本課訂的約定；show-me-your-work 沒有規定取代列的格式。用一個固定的格式，讓取代關係可以被機器讀出來，是 Encode Lessons in Structure 用在日誌本身。）

這支工具只檢查了「evidence 能不能解析」。原文的對帳還要求兩件工具做不到的事：**evidence 是否顯示了那一列宣稱的東西**（commit 存在，但它真的是那個改動嗎？），以及**有沒有沒被記錄的決定**（要讀 transcript 才知道）。這兩件事需要判斷。

## 四、另一家族的模型審軌跡

> Before handing back, spawn a subagent on a different model family from the one that did the work. Self-review is not a substitute.

審查者讀軌跡與 transcript，標出使用者該注意的地方——不是重做工作，而是掃描：

- 證據薄弱或缺席的決定
- 跳過的驗證步驟，或 transcript 裡沒有證據的驗證宣稱
- 事後看起來有風險的選擇（過早的、擴大範圍的、掩蓋症狀的）
- 使用者隨便看一眼會漏掉的缺口

每一個產生了軌跡的 run，回覆都要以一個 **Attention** 段落結束：第一行是 `reviewed by <model>`，然後逐條列出每個標記，指向具體的列或時刻。「"No flags" is a valid value. The model name is not.」——「沒有標記」是合法的；但不能省略是誰審的。

在 Claude Code 預設環境下，「另一個家族」不可得（Ch 17）。這時候 Attention 段落至少要誠實地寫出 `reviewed by` 的是哪個模型，讓你知道這份審查是同家族的。補救方法和 Ch 17 一樣：手動交給另一個家族（例如 Codex plugin）。

早上你用 `/pstack:show-me-your-work catch me up on what you did last night` 要求審查形式的報告。官方建議：「Read that section first, then the log rows it points at. You're auditing decisions, not re-reading the whole night.」

只要快速狀態而不是稽核時，用 `/pstack:ketchup`：唯讀、用平白的話說自你上一則訊息以來改變了什麼，然後列出**每一個等你決定的事**，附上它的推薦、證據、風險，「so you can decide from the reply alone」。

## 底層機制：為什麼證據必須是指標，不是散文

```
   evidence 是散文                               evidence 是指標
   ───────────────                              ───────────────
   「我仔細檢查了所有的測試，                       「commit 8677205」
    它們都正常通過，沒有發現任何問題」               「artifacts/verify-notes/list.json」
                                                  「src/parse.mjs:12」
         │                                               │
   只能「相信」或「不相信」                          可以被解析：存在嗎？
   無法稽核                                       可以被檢查：它顯示了宣稱的東西嗎？
   寫的人可以寫得很有說服力                         寫的人無法讓一個不存在的 commit 存在
```

一段散文的說服力取決於寫得好不好；一個指標的說服力取決於它指向的東西存不存在、顯示了什麼。這是本課反覆出現的主題（Ch 14 的證據階梯、Ch 20 的「只收證物」）在日誌上的形式。

## 對比與取捨

| 方式 | 審查成本 | 可信度 | 失敗模式 |
|---|---|---|---|
| 重讀 transcript | 極高 | 高（如果你真的讀完） | 你不會真的讀完 |
| agent 的最終總結 | 低 | 低 | 作者寫的故事 |
| 決策日誌（散文 evidence） | 中 | 中 | 無法稽核 |
| **決策日誌（指標 evidence）+ 對帳 + 異家族審查** | 低到中 | 高 | 審查者與作者同家族時盲點重疊；沒被記錄的決定要靠讀 transcript 才找得到 |

## 踩雷集錦

1. **evidence 寫成一段話**：原文：「Never a paragraph.」寫 commit、路徑、`file:line`、artifact。本章的稽核工具會把散文 evidence 標出來。
2. **改掉錯的那一列**：「Correct the log, not the story.」追加一列取代它。錯誤本身是日誌最有價值的部分。
3. **用 `printf >>` 直接寫日誌，而 evidence 來自外部**：一個以 `=` 開頭的 PR 標題可能在 reviewer 的試算表裡被執行。用 `log.sh`，或自己做同樣的清理。
4. **每個動作都記一列**：日誌會變成另一份 transcript。只記決策點與檢查點。
5. **審查者省略**：「Self-review is not a substitute.」而且 Attention 段要寫出審查者的模型——同家族的審查要被看見是同家族的。

## 進階：再往深一層

- **把稽核工具擴到「evidence 顯示了宣稱的東西」**：對 `commit <sha>` 的列，檢查那個 commit 的 diff 是否碰了 decision 裡提到的檔案（例如 decision 說「moved a.mjs」，那個 commit 的 `git show --stat` 應該包含 `a.mjs`）。這把一部分「需要判斷」的檢查變成機械的。
- **讀 `figure-it-out` 的「Pair delegated work with a judge」**：這句話和 Feature playbook 的「arena 不准 skip，因為價值是審查分離」是同一個想法：長時間執行中，每一個 worker 的產出都要有一個不是它的判官。在 Claude Code 上，最便宜的判官是一支腳本（predicate、稽核工具）；其次是另一個模型。
- **用 `column` 讀日誌**：原文建議 `column -s$'\t' -t decisions.tsv` 在終端機裡渲染成表格；commit 進 GitHub 的 TSV 會被渲染成表格。

## 動手練習

1. 用 `log.sh` 為你下一個真實的任務記五列日誌。然後用本章的 audit-log.mjs 稽核它。有幾列的 evidence 是散文？
2. 故意在日誌裡寫一列引用一個不存在的檔案路徑，跑稽核，然後依原文的規則追加一列取代它。你的取代列寫了什麼 why？
3. 用 figure-it-out 的 Phase A 為一個大型任務框定：寫出可證偽的 predicate、量化的範圍、嚴謹度與它的關卡和產物。給一個同事看——他能只看這份框定就判斷你的計畫合不合理嗎？

## 本章重點整理

- figure-it-out：沒有 playbook 適用、大型工作、使用者要離開時，先設計流程——Frame（可證偽的 predicate、量化的範圍、嚴謹度是關卡與產物）→ 設計（原子單元、風險最大的先做、harness 先於工作、寫下 phase 清單給人審）→ 迴圈（每個單元是實驗；委派配判官；結論只有 VERIFIED / NOT VERIFIED / INCONCLUSIVE）→ 軌跡 → 驗證並交回
- show-me-your-work：一個 TSV、每個決策一列、evidence 是指標不是散文、只能追加、多個 run 用 `start` 列區分
- `log.sh` 會加表頭、壓平換行與 tab、把可能被試算表當成公式的格子加上引號
- 交回前和 transcript 對帳（本章的稽核工具抓到一列引用了不存在的 commit），用追加一列取代、不改舊列；再由另一家族的模型審查，回覆以 Attention 段結束並寫出審查者的模型

## 自我檢核

- [ ] 不看本章，能說出決策日誌的六個欄位，以及 evidence 為什麼不能是散文嗎？
- [ ] `log.sh` 為什麼要處理以 `=` 開頭的格子？如果不處理，什麼情況下會出問題？
- [ ] 「Correct the log, not the story」在實務上是什麼意思？為什麼不直接改掉錯的那一列？
- [ ] figure-it-out 說「Rigor is gates and artifacts, not 'try harder'」。為你目前的一個任務，把「要小心」改寫成具體的關卡與產物

## 延伸閱讀

### 原始碼

- **[skills/figure-it-out/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/figure-it-out/SKILL.md)**
  - **讀哪裡**：全文，五個 phase
- **[skills/show-me-your-work/](https://github.com/cursor/plugins/tree/main/pstack/skills/show-me-your-work)**
  - **讀哪裡**：SKILL.md 的「Audit the log against the transcript」與「Cross-model review of the trail」；`scripts/log.sh` 的註解
- **[skills/ketchup/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/ketchup/SKILL.md)**
  - **讀哪裡**：「Gather what changed」與「Write the reply」
  - **能學到什麼**：一份「讓人能只看回覆就做決定」的狀態報告該怎麼寫

### 安全

- **[OWASP — CSV Injection](https://owasp.org/www-community/attacks/CSV_Injection)**
  - **讀哪裡**：整頁
  - **能學到什麼**：`log.sh` 的單引號前綴所防禦的攻擊；為什麼 `=`、`+`、`-`、`@` 都要處理
  - **和本章的關聯**：決策日誌的 evidence 常常來自不可信的來源（PR 標題、檔名、生成的文字）

### 文章

- **[Google SRE Book — Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)**
  - **這篇說什麼**：blameless postmortem 的寫法：時間線、決策、證據
  - **和本章的關聯**：決策日誌是一份「在過程中寫的 postmortem 素材」；只能追加、錯誤也要記錄的原則和 postmortem 文化一致

### 對照閱讀

- **[harness_engineering Ch 35 — Observability](../harness_engineering/35-observability.md)**（本 repo）
  - **和本章的關聯**：從 harness 層面看「怎麼讓 agent 的執行可以被觀察」；決策日誌是一種人類可讀的、決策層級的 trace

一個任務、一份日誌。下一章把規模放大到一整個計畫、一疊 PR、一群 subagent。

→ [Ch 27 多階段計畫、Autopilot 與 Orchestrate](./27-multi-phase-autopilot.md)
