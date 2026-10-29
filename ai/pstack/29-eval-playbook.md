# Ch 29 — Eval：盲測一個 skill 變更

> **目標**：搞懂 pstack 的 Eval playbook——它整份是圍繞一個失敗模式設計的：**觀察者效應**，一個知道自己正在被評估的 agent，行為會不一樣。你會讀懂它的七條盲測規則與七個步驟，特別是「從 transcript 判斷 agent 有沒有照著技能做，而不是從它的自我回報」；把盲測規則寫成一支檢查器，在一個洩題的設定上抓到 10 處洩漏（還有一處它第一版沒抓到）；最後看 Claude Code 2.1.295 內建的 `claude plugin eval` 能怎麼補上 pstack eval 的手動部分。

> **環境**：pstack 0.15.36、Claude Code 2.1.295、Node.js 26。blind-check 與它的輸出是實際跑出來的；`claude plugin eval` 的描述來自本機 `--help` 的實際輸出，本章**沒有實際執行**它（每個 case 會跑完整的 Claude 子程序，會產生費用）。

## 為什麼需要這個？

你改了一個技能。你怎麼知道它讓 agent 變好了？

最直覺的做法是：跑一次，看結果，覺得不錯。這有三個問題：

1. **一次結果是一個樣本**。agent 的輸出是隨機的，一次好可能是運氣
2. **你知道你改了什麼**，所以你會看到你想看的（確認偏誤）
3. **agent 知道它在被測試**，如果你的 prompt 寫「這是一個 eval」「列出你用了哪些技能」，它會表現出「被測試時的行為」，不是平常的行為

官方指南：

> A skill edit affects every future session, so test it like the experiment it is. The same goes for adopting someone else's skill. Check that it makes the agent better on your work before you keep it.

以及一個很實際的警告：

> A fix written from one bad session tends to overfit that session, and over many edits the skill drifts. The eval catches the drift before it ships.

## 先建立直覺：雙盲臨床試驗

```
  藥物試驗                                    技能 eval
  ────────                                   ─────────
  受試者不知道自己吃的是藥還是安慰劑             候選 agent 不知道自己在被評估
                                             （prompt 看起來像一般的使用者請求）
  受試者不知道還有其他受試者                     候選不知道其他候選存在
  評估者不知道誰吃了什麼                         評審只看到去識別化的標籤，不知道模型名稱
  用預先定好的指標評估                           評審用 rubric 評分；rubric 不給候選看
  看實際的生理數據，不只問受試者「感覺如何」       從 transcript 看候選實際讀了哪些檔案，
                                             不信候選說「我用了某某技能」
  研究者最後自己讀所有資料                       主 session 自己讀完每一份產出，和評審對照
```

## 一、盲測規則

Eval playbook 開頭的 Non-negotiables：

| 規則 | 擋的是什麼 |
|---|---|
| 候選看到的目錄、檔案、prompt 裡，不准出現 `eval`、`test`、`judge`、`experiment`、`rubric`、`score`、`compare`、`benchmark`、`candidate`、`arena` | 觀察者效應：一個看到「eval」的 agent 會更謹慎、更囉唆、更照本宣科 |
| 候選的 prompt 看起來像一個自然的使用者請求；說目標，不說後設 | 同上 |
| 不准有誘導 chain 的提示：不准問候選「你用了哪些技能、原則、檔案」；要求一般的設計說明，**從程式碼的樣子判斷有沒有照著做，不從自我回報** | 自我回報不可信：被問「你用了 X 嗎」，agent 會說用了 |
| 目錄與 slug 名稱要去識別化，用使用者會取的、像專案的名字 | 洩題 |
| 不准告訴候選其他候選存在 | 競爭心態改變行為 |
| 評審可以知道自己在評審，但只看到去識別化的標籤，**永遠看不到模型名稱** | 評審對特定模型的偏見 |
| 比較兩個變體時，一個評審在一次評分裡用同一個尺度評兩組，不知道哪一組是哪個變體 | 分開評分時尺度會漂移 |

第三條最微妙。你想知道「新版技能有沒有讓 agent 讀 principle 檔」，最直接的方法是問它。但這個問題本身就會改變它的行為（它會去讀，因為你問了），而且它的回答不可信（它可能會說讀了）。pstack 的解法是：不問，**去看它實際做了什麼**。

## 二、七個步驟

1. **Frame**：說清楚測試的變體是什麼、什麼行為算成功；寫 rubric（3–6 條具體標準）**只給評審**
2. **建立去識別化的環境**：每個候選一個工作目錄，變體已經放好；放進一個自然的任務會有的 context（專案骨架、候選自然會讀的技能）
3. **寫一個自然的 prompt**：使用者會打的那句話，不洩漏量什麼
4. **平行 spawn N 個候選**，用不同的模型（照 arena 的 Phase B），各自在自己的去識別化目錄裡，拿同一個 prompt
5. **spawn 一個盲評審**，不同的模型家族（照 arena 的 Phase C），看去識別化的標籤與 rubric，從不看模型名稱
6. **從 transcript 驗證 chain，不從自我回報**：讀每個候選在這個 workspace 的 transcript，看它**實際打開了哪些檔案**。「Grade chain-following from the files it really read plus the shape of the code, never from the candidate's own claims.」
7. **自己讀完每一份產出**，和評審的結論對照。「Disagreement means a model is biased or the rubric is ambiguous.」

**Reply**：測試的變體、rubric、每個候選的筆記、評審的結論、你的綜合判斷、要不要推廣這個變體的建議。

官方指南最後一句：「Read every output yourself before accepting the verdict. If you disagree with the judge, suspect the rubric before you suspect your judgment.」

## 三、把盲測規則寫成檢查器

規則一、四、五、以及第三條的一部分，可以機械地檢查：

```js
// blind-check.mjs <candidate-dir> <prompt-file>：檢查候選看得到的東西有沒有洩漏實驗的存在
import fs from "node:fs";
import path from "node:path";
const BANNED = ["eval", "test", "judge", "experiment", "rubric", "score", "compare", "benchmark", "candidate", "arena"];
const [dir, promptFile] = process.argv.slice(2);
const hits = [];
const word = (s) => BANNED.filter((w) => new RegExp(`(^|[^a-z])${w}`, "i").test(s));
const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); for (const w of word(path.relative(dir, p))) hits.push(`path ${path.relative(dir, p)} contains "${w}"`); if (e.isDirectory()) walk(p); } };
for (const w of word(path.basename(path.resolve(dir)))) hits.push(`working dir ${path.basename(path.resolve(dir))} contains "${w}"`);
walk(dir);
const prompt = fs.readFileSync(promptFile, "utf8");
for (const w of word(prompt)) hits.push(`prompt contains "${w}"`);
if (/which (skills|principles|files) (you|did you) (use|appl|read)/i.test(prompt)) hits.push("prompt asks the candidate to self-report its chain");
if (/other (agents|candidates|models)/i.test(prompt)) hits.push("prompt reveals other candidates exist");
console.log(hits.length ? hits.map((h) => `LEAK ${h}`).join("\n") : "OK nothing in view names the experiment");
process.exit(hits.length ? 1 : 0);
```

（`(^|[^a-z])` 讓 `test` 不會匹配到 `latest` 之類的字中間；它仍然會匹配 `testing`、`tests`，這是刻意的。）

### 一個洩題的設定

```
leaky/
├── prompt.md
└── eval-run-1/
    └── candidate-a/
        ├── invoice.mjs
        └── test-fixtures/
```

```
This is an eval of our new review skill. Fix the bug in invoice.mjs, then list which skills you used.
Other candidates are doing the same task, so do your best; the judge will score you.
```

```bash
node blind-check.mjs leaky/eval-run-1 leaky/prompt.md
```

```
LEAK working dir eval-run-1 contains "eval"
LEAK path candidate-a contains "candidate"
LEAK path candidate-a/invoice.mjs contains "candidate"
LEAK path candidate-a/test-fixtures contains "test"
LEAK path candidate-a/test-fixtures contains "candidate"
LEAK prompt contains "eval"
LEAK prompt contains "judge"
LEAK prompt contains "score"
LEAK prompt contains "candidate"
LEAK prompt asks the candidate to self-report its chain
LEAK prompt reveals other candidates exist
exit=1
```

**第一行是第一版沒抓到的。** 第一版只掃描目錄**裡面**的路徑，沒有檢查候選所在的工作目錄本身叫什麼。但 agent 一定看得到自己的工作目錄（它會出現在 Claude Code 的環境資訊裡、出現在每一個 `pwd`），`eval-run-1` 這個名字本身就洩題了。這是本課第七次「檢查工具本身漏了東西」——而且這次漏的正好是最明顯的那一個。

### 一個去識別化的設定

```
sanitized/
├── prompt.md
└── invoice-tool/
    └── invoice.mjs
```

```
Customers say the invoice total is off by a cent when a line has three items at 0.10. Can you fix it?
Leave a short note on what you changed and why.
```

```
OK nothing in view names the experiment
exit=0
```

注意 prompt 的最後一句：「Leave a short note on what you changed and why.」——這是規則三允許的「要求一般的設計說明」。它不問「你用了哪些技能」，但它讓評審有東西可以讀。

（「three items at 0.10」是一個典型的浮點數誤差情境：`0.1 + 0.1 + 0.1` 在 JavaScript 裡是 `0.30000000000000004`。選這種有真實背景的 bug，prompt 才會像一個真的使用者請求。）

## 四、從 transcript 判斷 chain-following

規則三說不准問候選「你用了什麼」，那要怎麼知道它有沒有照著技能做？Step 6：讀 transcript。

在 Claude Code 上，transcript 在 `~/.claude/projects/<slug>/<session>.jsonl`，subagent 的在 `<session>/subagents/` 底下（Ch 7、Ch 11）。Ch 11 的 cite-audit 已經示範了怎麼從 JSONL 裡找出所有 `Read` 與 `Bash` 碰過的檔案路徑。同樣的工具改一下，就能回答「這個候選有沒有讀 `principle-model-the-domain/SKILL.md`」「它有沒有讀 `playbooks/bug-fix.md`」。

加上**程式碼的樣子**：一個說自己用了 Model the Domain 的候選，它的 diff 裡應該有一個查表或狀態機，而不是又多一個 if/else 分支（Ch 13 的「你跳過了這條的徵兆」）。

兩個證據來源都不依賴候選的自我陳述。

pstack 對讀 transcript 有一條反覆出現的限制：「Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.」——只讀這個 workspace 的。

## 五、Claude Code 的原生工具：`claude plugin eval`

Claude Code 2.1.295 內建了一個 plugin eval 指令。依本機 `claude plugin eval --help` 的輸出：

> Run eval cases (`<eval dir>/**/case.yaml` or `prompt.md` + `graders/*.md`; the eval dir is `evals/` unless `--eval-dir` or the manifest says otherwise) against a plugin and report scored results. Target is a path, a plugin name, or a `plugin@marketplace` id — installed and skills-dir plugins both resolve (and add a no-plugin baseline arm).

幾個和 pstack eval 對應的選項：

| 選項 | 作用 | 對應 pstack eval 的哪一步 |
|---|---|---|
| `--ablation <mode>` | 跑一個**沒有 plugin 的 baseline 組**並報告分數差（預設在解析得到 plugin 時開啟） | Step 1 的「variant under test」：有 vs 沒有 |
| `--runs <n>` | 每個 case 跑幾次（預設 3） | 一次結果是一個樣本 |
| `--judge-model <model>` | LLM grader 的模型（預設 `haiku`） | Step 5 的評審 |
| `--concurrency <n>` | 同時跑幾個（1–8） | Step 4 的平行候選 |
| `--max-cost-usd <usd>` | 硬性的成本上限 | — |
| `--threshold <0..1>` | 任何 case 低於門檻就 exit 1 | 讓 eval 能放進 CI |
| `--json [path]` | 完整結果（prompt、grader、每次的分數） | Step 7 自己讀每一份產出 |
| `--no-publish` | **報告只留在本機** | — |

最後一個要特別注意：help 文字說 HTML 報告「publishing it to claude.ai」是**預設行為**（「already the default when your account supports it」）。如果你的 eval case 裡有公司的程式碼或資料，**一定要加 `--no-publish`**。這是一個對外的動作，pstack 的 Autonomy 會把「發送到外部」列為要停下來的事（Ch 15），你自己用的時候也一樣。

help 文字還有一段安全警告值得照抄：「It loads the plugin and runs its eval suite ... on your machine, as you: only evaluate plugins you trust — the run's sandboxing limits what a malicious plugin can reach but is not a guarantee, and a bundled suite passing is not a security vetting.」

### 它和 pstack eval 的差別

| | pstack Eval playbook | `claude plugin eval` |
|---|---|---|
| 盲測 | 嚴格的七條規則，由主 session 執行 | 取決於你怎麼寫 case；工具本身不檢查洩題 |
| 候選 | 不同模型，平行 | 同一個模型（可用 `--model` 覆寫），有 / 沒有 plugin 兩組 |
| 評審 | 不同家族、看去識別化標籤 | grader（LLM 或其他），預設 haiku |
| chain-following | 從 transcript 判斷 | grader 可以檢查 `tool_used: Skill`（help 提到這是「plugin-fired indicator」） |
| 誰讀產出 | 主 session 一定要自己讀 | `--json` 或 HTML 報告，看你自己 |
| 可重複性 | 每次手動設定 | case 檔進 repo，可以重跑、放進 CI |

兩者互補：`claude plugin eval` 給你**可重複、可放進 CI 的** baseline 比較；pstack 的規則告訴你**怎麼寫 case 才不會洩題**、以及**讀結果時要注意什麼**。把 blind-check 跑在你的 `evals/` 目錄與每個 `prompt.md` 上，就是把兩者接起來。

> 本章沒有實際跑 `claude plugin eval`。case.yaml 的完整格式、grader 的寫法、`with-only` grader 的語意，以你的版本的文件與 `--help` 為準。

## 底層機制：為什麼自我回報不可信

```
   問候選「你用了哪些技能？」                   看 transcript 與程式碼
   ─────────────────────────                 ────────────────────────
   候選在回答前：可能沒讀 principle 檔           Read(.../principle-model-the-domain/SKILL.md)
        │                                    在 transcript 裡有或沒有——不會因為你問而改變
        ▼
   被問了 → 去讀（觀察者效應改變了行為）         diff 裡有查表 / 狀態機，或又多一個 if 分支
        │                                    ——程式碼的樣子不會說謊
        ▼
   回答「我用了 Model the Domain」
   （可能是真的，可能是因為它知道你想聽）
```

這和 Ch 11 的引用規則（「只能引用讀過全文的原則」）、Ch 26 的「證據是指標不是散文」是同一個原則：**能從產物驗證的，就不要從陳述驗證**。

## 對比與取捨

| 方法 | 成本 | 觀察者效應 | 樣本數 | 適合 |
|---|---|---|---|---|
| 改完跑一次看看 | 最低 | 取決於 prompt | 1 | 確認沒有完全壞掉 |
| A/B 各跑一次自己比 | 低 | 你知道哪個是哪個 | 2 | 粗略的方向 |
| **pstack Eval（盲測、多模型、異家族評審、讀 transcript）** | 高 | 最低 | N × 模型數 | 一個會影響很多 session 的技能改動 |
| `claude plugin eval`（ablation、多次執行、grader） | 中到高 | 取決於 case 寫法 | runs × cases | 可重複的回歸檢查、CI |

## 踩雷集錦

1. **prompt 裡寫「這是一個 eval」**：候選會進入「被測試模式」。寫一個使用者真的會打的請求。
2. **問候選「你用了哪些技能」**：被問了它就會去讀，而且它的回答不可信。從 transcript 與程式碼判斷。
3. **工作目錄叫 `eval-run-1`**：本章的檢查器第一版就漏了這一個。候選看得到自己的工作目錄名稱。
4. **評審知道哪個候選是哪個模型**：評審對模型的既有印象會影響評分。只給去識別化的標籤。
5. **用 `claude plugin eval` 時忘了 `--no-publish`**：預設會把報告發佈到 claude.ai。有任何不能外流的內容時，這是一個不可逆的對外動作。

## 進階：再往深一層

- **把 blind-check 接進 `claude plugin eval` 的流程**：在跑 eval 之前，對 `evals/` 底下每一個 case 的 `prompt.md` 與 scaffold 產生的目錄跑 blind-check，有洩漏就不跑。這把 pstack 的盲測規則變成 eval pipeline 的一個關卡（Encode Lessons in Structure）。
- **讀 `reflect` 技能**：Ch 30 會談的 `/reflect` 會根據一次 session 提出技能修改。官方指南建議修改技能時同時要求 eval：「update the review skill so it flags missing migrations, and eval the change」——「Asking for the eval up front keeps the edit honest.」
- **統計意義**：3 次執行 × 2 組，差距要多大才算真的差異？這是 Ch 22 Q5 的問題用在 eval 上。原文 Explain the Number 的最後一段：「For an eval result, ask the same of the trials: did every run do the task, does the gap hold across trials and models, and does the scenario matter.」

## 動手練習

1. 照本章寫出 blind-check，然後為你最近改過的一個技能（或 CLAUDE.md 的一段）設計一個 eval：寫一個去識別化的 prompt、一份只給評審的 rubric（3–6 條）、兩個工作目錄（舊版與新版）。用 blind-check 確認沒有洩題。
2. 擴充 Ch 11 的 cite-audit，讓它回答「這個 transcript 讀過哪些 playbook 與 principle 檔」，輸出成一張表。對兩個候選的 transcript 各跑一次，比較它們的 chain。
3. 讀 `claude plugin eval --help` 的完整輸出，為 pstack 的一個技能（例如 unslop）寫一個最小的 eval case：一個 prompt.md（請它改寫一段有 AI 腔的文字）、一個 grader（檢查輸出裡沒有 em dash 與 rubric 7 的詞彙）。**不要**真的跑它，除非你確認了成本與 `--no-publish`。

## 本章重點整理

- Eval playbook 圍繞觀察者效應設計：知道自己被評估的 agent 行為不同
- 七條盲測規則：候選看得到的東西裡沒有 eval / test / judge 等字；prompt 像自然請求；不問候選它用了什麼；目錄去識別化；不說有其他候選；評審看不到模型名稱；兩組用同一個評審同一個尺度
- chain-following 從 transcript（實際讀了哪些檔案）與程式碼的樣子判斷，不從自我回報
- 本章的 blind-check 在洩題設定上抓到 11 處（第一版漏了工作目錄名稱本身）；去識別化設定通過
- `claude plugin eval` 提供可重複的 with / without baseline、多次執行、grader、成本上限、門檻；它預設會把報告發佈到 claude.ai，有敏感內容時要加 `--no-publish`

## 自我檢核

- [ ] 不看本章，能說出至少五條盲測規則，以及每條在擋什麼嗎？
- [ ] 為什麼「問候選它用了哪些技能」會同時讓結果失真、而且答案不可信？
- [ ] pstack 的 Eval playbook 和 `claude plugin eval` 各自擅長什麼？你會怎麼組合它們？
- [ ] 你上一次改技能或 CLAUDE.md 之後，用什麼判斷它變好了？依本章的標準，那個判斷可信嗎？

## 延伸閱讀

### 原始碼

- **[playbooks/eval.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/eval.md)**
  - **讀哪裡**：全文（407 字），特別是 Non-negotiables 與 Step 6

### 官方指南

- **[docs/guide/09-make-it-yours.md「Test a skill change blind」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/09-make-it-yours.md)**
  - **能學到什麼**：官方的 eval prompt 範例與觀察者效應的說明

### 官方文件

- **Claude Code：`claude plugin eval --help`**（本機）
  - **讀哪裡**：完整輸出，特別是 `--ablation`、`--no-publish`、`--scaffold` 的說明
  - **能學到什麼**：原生 eval 工具的實際選項；本章的表格以此為依據
- **[Anthropic — Define your success criteria / Create strong empirical evaluations](https://docs.claude.com/en/docs/test-and-evaluate/develop-tests)**
  - **讀哪裡**：怎麼設計 eval case、怎麼選 grader
  - **和本章的關聯**：通用的 eval 設計方法；pstack 的盲測規則是在這之上加的一層

### 文章

- **[Hamel Husain — Your AI Product Needs Evals](https://hamel.dev/blog/posts/evals/)**
  - **這篇說什麼**：為什麼 LLM 產品需要系統化的 eval、怎麼從讀真實的 trace 開始
  - **和本章的關聯**：「Read every output yourself」的業界版本；作者特別強調先讀資料再寫 grader
- **[Wikipedia — Hawthorne effect](https://en.wikipedia.org/wiki/Hawthorne_effect)**
  - **和本章的關聯**：「被觀察會改變行為」這個現象的名稱與爭議史；pstack 的盲測規則就是在對 agent 處理這個效應

### 對照閱讀

- **[harness_engineering Ch 34 — Eval](../harness_engineering/34-eval.md)**（本 repo）
  - **和本章的關聯**：從 harness 層面設計 eval；本章專注在「評估一個技能變更」這個特定問題與它的盲測要求

eval 告訴你一個改動好不好。下一章是改動從哪裡來：從 session 裡學到的教訓、從 repo 歷史裡挖出的重複錯誤、從你自己的對話紀錄裡讀出的工作風格。

→ [Ch 30 自我改進迴路：reflect、correct、automate-me](./30-self-improvement-loop.md)
