# Ch 23 — 清理文字與註解：unslop、no-comments、technical-writing

> **目標**：搞懂 pstack 怎麼處理 agent 產出的「文字」與「註解」。`unslop` 是一份有編號的 AI 文字特徵清單；`no-comments` 把註解交給一個**沒寫過那些註解**的審查者 Comment Sicko；`technical-writing` 是一套四層的文件標準（Diátaxis、Google 開發者文件風格、STE、Global English）。你會把 unslop 裡能機械檢查的規則寫成一支 lint，在一段典型的 agent PR 描述上抓到 11 處、在改寫後抓到 0 處；從練習 B 的 diff 抽出新增的註解，用 Comment Sicko 的保留清單逐條判決；並討論這些英文規則搬到中文時，哪些適用、哪些會出錯。

> **環境**：pstack 0.15.36、Node.js 26、git。lint 與 diff 的輸出是實際跑出來的；Comment Sicko 的判決由本課作者依它的規則手動做出，不是實際 spawn 的結果。

## 為什麼需要這個？

agent 寫的文字有一種很容易辨認的味道：「This PR serves as a robust fix for the crucial issue」「It's not just a bug fix, but a significant step toward...」「I hope this helps!」。讀者看到這些句子，會本能地降低對整份內容的信任——即使內容本身是對的。

agent 寫的註解有另一種味道：`// Phase 1: dedupe users`、`// ignore transient errors so the batch keeps going`。前者重述程式碼，後者為一個 workaround 辯護。

官方指南的說法：

> Cleanup is not optional polish. A diff with narrating comments and defensive dead weight reads as unfinished to reviewers, and the extra code is where the next bug hides.

而 pstack 把這件事分成三個技能，各管一件：

> `/deslop` cleans slop out of the code, `/unslop` cleans it out of prose, and `/no-comments` hands the comments to a reviewer who didn't write them.

（`deslop` 屬於 `cursor-team-kit` plugin，不在 pstack 裡；社群移植版另外附了一份。）

## 先建立直覺：三種「多餘」

```
   prose（PR 描述、回覆、文件）       code（diff）                 comments（diff 裡的註解）
   ──────────────────────────       ──────────                   ─────────────────────────
   「serves as a robust fix」        沒有理由的 guard              // Phase 1: dedupe users
   「not just X, but Y」             死掉的相容路徑                 // ignore errors so it keeps going
   「I hope this helps!」            和任務無關的修改               // IMPORTANT: do not remove
          │                              │                              │
       /unslop                        /deslop                      /no-comments
     （規則清單，改寫）           （cursor-team-kit）         （另一個 agent 判決，然後重構）
```

## 一、unslop：有編號的規則

unslop 的 SKILL.md 是一份規則清單，每條有一個**穩定的編號**：

> Rule numbers are stable ids that other skills cite. A removed rule leaves a gap.

這就是為什麼 poteto-mode 的 Writing the reply 可以說「A colon as a mid-sentence connector is also out (unslop rule 14)」——用編號引用一條規則，和 Ch 11 用名字引用一條原則是同一個設計。規則被刪掉時留下空號，而不是重新編號，所以舊的引用不會悄悄指向另一條規則。

規則分成幾類（目前編號到 33，中間有空號）：

| 類別 | 規則（節錄） |
|---|---|
| Content | 3 膚淺的 -ing 片語（highlighting...、ensuring...）；5 模糊的歸屬（「Experts believe」） |
| Language | 7 AI 詞彙（delve、crucial、pivotal、tapestry…）；8 花俏的「是」（serves as、boasts）；9 「not just X, but Y」；10 硬湊三點；11 同義詞輪換；12 假範圍（from X to Y） |
| Style | 13 em dash；14 句中冒號；15 粗體過多；16 行內標題式條列；17 標題用 Title Case；18 裝飾性 emoji；19 彎引號 |
| Communication | 20 聊天機器人用語（I hope this helps!）；22 討好 |
| Filler | 23 填充詞（in order to → to）；24 過度避險；25 空泛的結論 |
| Jargon | 26 抽象隱喻名詞（leverage、north star、flywheel、surface as in "API surface"…） |
| Plain speech | 27 說它做什麼，不說它感覺如何；28 拆長句；29 主動語態；30 刪副詞或換更強的動詞；31 用平白的詞；32 矯飾的文字；33 過度壓縮 |

### 最有價值的三條

**27：說它做什麼，不說它感覺如何。**

> "the database stays close at hand", "SQL you can read", "types that follow your schema" name a feeling. The fix names the mechanism or a number: "`.toSQL()` returns the exact string sent to the database", "a column rename fails the build". ... One more check: if the sentence could appear unchanged in another project's docs, it says nothing about this one. Cut it.

「如果這句話可以原封不動出現在另一個專案的文件裡，它對這個專案什麼都沒說」——這是一個非常實用的測試。

**26：抽象隱喻名詞。** 它特別點名「harness as a metaphor」「surface as in "API surface"」「scaffolding as a metaphor」——這些詞在本課與這個 repo 的其他課程裡很常見。它們不是錯，但 unslop 的立場是：通常有一個更具體的詞可以用。

**33：過度壓縮。** 這條和前面幾條方向相反，很重要：

> Dropped articles, verbless fragments, symbol-speak, and abbreviations that make the reader decode instead of read. "Parser rejects bad date → exit 2, no write" becomes "The parser rejects a bad date, exits with code 2, and writes nothing."

精簡不是把句子壓成電報。poteto-mode 的 Writing the reply 也說：「Terse is not an excuse to drop content.」

## 二、把能機械檢查的規則寫成 lint

照 Encode Lessons in Structure：能機械檢查的規則，就不要靠 agent 記得。

```js
// unslop-lint.mjs：unslop 裡能用正規表示式粗略檢查的幾條規則
import fs from "node:fs";
const RULES = [
  [7, "AI vocabulary", /\b(additionally|crucial|delve|enhance|fostering|garner|interplay|intricate|pivotal|showcase|tapestry|testament|underscore|vibrant|seamless(ly)?|robust)\b/i],
  [8, "fancy 'is'", /\b(serves as|stands as|boasts)\b/i],
  [9, "not just X, but Y", /\bnot (just|only)\b[^.]*\bbut\b/i],
  [13, "em dash", /—|–| -- /],
  [14, "mid-sentence colon", /[a-z]: [a-z]/],
  [20, "chatbot phrase", /\b(I hope this helps|Let me know if|Certainly!|Great question)\b/i],
  [23, "filler", /\b(in order to|due to the fact that|it is important to note)\b/i],
  [24, "hedging", /\b(could potentially|might possibly)\b/i],
  [30, "adverb propping a verb", /\b(significantly|greatly|drastically|seamlessly) (improve|enhance|reduce|speed)/i],
];
const text = fs.readFileSync(process.argv[2], "utf8");
let n = 0;
text.split("\n").forEach((line, i) => {
  if (/^\s*(```|- \[|#)/.test(line)) return;
  for (const [id, name, re] of RULES) if (re.test(line)) { n++; console.log(`${i + 1}: rule ${id} ${name}: ${line.trim().slice(0, 80)}`); }
});
console.log(`${n} findings`);
process.exit(n ? 1 : 0);
```

（`robust` 與 `seamless` 不在 unslop 第 7 條的原始清單裡，是本章加的兩個常見例子。第 14 條的正規表示式只抓「小寫字母、冒號、空白、小寫字母」，會漏掉很多、也會誤抓一些，它只是一個起點。）

一段典型的 agent PR 描述：

```markdown
# fix: prevent duplicate notifications after retry

This PR serves as a robust fix for the crucial duplicate-notification issue — users were getting the same message twice.

In order to address this, we delve into the retry path: the job now deduplicates users and seamlessly improves reliability.

It is not just a bug fix, but a significant step toward a more resilient pipeline. Additionally, it could potentially reduce load.

I hope this helps! Let me know if you have questions.
```

```
3: rule 7 AI vocabulary: This PR serves as a robust fix for the crucial duplicate-notification issue — us
3: rule 8 fancy 'is': This PR serves as a robust fix for the crucial duplicate-notification issue — us
3: rule 13 em dash: This PR serves as a robust fix for the crucial duplicate-notification issue — us
5: rule 7 AI vocabulary: In order to address this, we delve into the retry path: the job now deduplicates
5: rule 14 mid-sentence colon: In order to address this, we delve into the retry path: the job now deduplicates
5: rule 23 filler: In order to address this, we delve into the retry path: the job now deduplicates
5: rule 30 adverb propping a verb: In order to address this, we delve into the retry path: the job now deduplicates
7: rule 7 AI vocabulary: It is not just a bug fix, but a significant step toward a more resilient pipelin
7: rule 9 not just X, but Y: It is not just a bug fix, but a significant step toward a more resilient pipelin
7: rule 24 hedging: It is not just a bug fix, but a significant step toward a more resilient pipelin
9: rule 20 chatbot phrase: I hope this helps! Let me know if you have questions.
11 findings
exit=1
```

四個段落，11 個發現。但 lint 抓不到**最大的問題**：這段描述幾乎沒有內容。它沒說病因是什麼、改了哪裡、怎麼驗證的。unslop 第 27 條（「if the sentence could appear unchanged in another project's docs, it says nothing」）會判每一句都不合格，但這條規則無法用正規表示式檢查。

改寫成 Opening a PR playbook 要求的格式（Why / What changed / Verification，Ch 24）：

```markdown
# fix(notify): persist sent status so a retry skips delivered users

## Why
Users got the same notification twice after the job retried. The job reloads users on every attempt, and the sent status lived only in memory.

## What changed
- `sendAll` takes a `markSent` callback and skips users whose stored status is `sent`.

## Verification
- `node --test test/` passes. The retry test delivers users 1, 2, 3 once each.
```

```
0 findings
exit=0
```

**0 個發現，但這不是改寫成功的證據**——改寫成功的證據是它說出了病因、改動、驗證方式。lint 只能保證你沒犯那幾種可以被字串比對抓到的錯。

## 三、這些規則搬到中文

unslop 是為英文寫的。本課（以及這個 repo）是中文。直接套用會出問題：

| unslop 規則 | 在中文的對應 | 適用嗎 |
|---|---|---|
| 13 em dash | 中文的破折號「——」是正常標點 | **不適用**。這個 repo 的寫作風格大量使用「——」，照搬規則會讓中文讀起來很怪 |
| 14 句中冒號 | 中文的全形冒號常用於引出說明 | 部分適用：「原因很簡單：……」這種為了製造懸念的冒號可以刪 | <!-- allow-banned: 引用一個要刪掉的句型 -->
| 7 AI 詞彙 | 中文有自己的一套：「賦能」「深入探討」「至關重要」「無縫」「打造」「助力」 | **適用**，但要換成中文的清單 |
| 9 not just X, but Y | 「不僅……更是……」「不只是……而是……」 | **適用** |
| 20 聊天機器人用語 | 「希望這對你有幫助！」「有任何問題歡迎詢問」 | **適用** |
| 22 討好 | 「好問題！」「你說得完全正確！」 | **適用** |
| 23 填充詞 | 「值得注意的是」「需要指出的是」「總的來說」 | **適用** |
| 27 說它做什麼 | 同樣的測試：這句話能不能原封不動出現在別的專案裡？ | **完全適用** |

這個 repo 的 `learn` 技能已經有一份中文版的類似規則（「嚴禁輕描淡寫詞」「不要寫純 AI 腔 meta 話」「不要無意義的過場句」），Ch 15 把其中一條編碼成了 lint。兩份規則的精神一致：**刪掉不承載資訊的字**；差別只在語言的表面特徵。

教訓是 Ch 2 說過的：**規則要理解它在擋什麼，再決定怎麼翻譯到你的語境**。em dash 在英文是 AI 特徵，是因為人類英文作者很少那麼頻繁地用它；在中文，破折號沒有這個統計特徵。

## 四、no-comments 與 Comment Sicko

註解需要另一種處理方式，理由在官方指南裡：

> Comments need their own pass, and not from the agent that wrote them. An author defends its comments the way you'd defend yours.

`/no-comments` 的流程：

1. spawn `comment-sicko`（Claude Code 是 `pstack:comment-sicko`），傳入範圍（預設是對 base branch 的 diff，含工作目錄）
2. 審查它的報告與 diff：拒絕改到應用程式碼的、超出範圍的、刪掉受保護註解的……
3. 直接修掉瑣碎的被接受旗標
4. 需要重塑形狀的，跑一次 `/architect`（只到草圖）
5. 對「do not remove」「talk to X before changing」這類**宣稱限制**的註解，提議把限制編碼成型別、執行期檢查、測試或 lint，等核准
6. 報告刪除數、還原的註解、重跑次數、草圖、修正、編碼提議等

### Comment Sicko 的保留清單

Comment Sicko 的定義寫得很有個性（「A deranged comment-hater that savors deletion」），但它的規則非常精確。只有這幾種註解可以留下：

- 法律或授權標頭
- **由外部依賴、平台、廠商或協定強加、我們無法改變的**不明顯的行為
- `// prettier-ignore`；lint 抑制只在那條規則本身有問題、太囉唆或只是風格時留下
- 定義**公開 API 契約**的 doc comment
- 解釋一個程式碼無法表達的限制的 issue 或 RFC 連結

> That list is my only leash. When I am not sure a keep clause applies, the comment dies.

兩個關鍵設計：

**我們自己程式碼的意外，不准用註解解釋。** 「Surprises in our own code are meat. Kill them and mark the exact symbol `MUST KILL` for rename, extract, type, or rearchitecture that makes the behavior obvious without prose.」——如果你的程式碼需要一段註解才看得懂，該改的是程式碼（改名、抽出、加型別、重新設計），不是加註解。

**長篇辯解是自白。** 「`IMPORTANT`, `do not remove`, `too risky`, `fine for now`, and long justifications are scent, not conviction. ... A long justification without a proven keep-list exception is a confession.」

### 實際判決一次：練習 B 的 diff

從練習 B 的 agent PR 抽出新增的註解：

```bash
git diff main agent/fix-duplicates -- '*.mjs' | grep -nE '^\+.*(//|/\*)'
```

```
7:+  // Phase 1: dedupe users
14:+// Kept for compatibility with existing callers.
25:+  // Phase 2: send
35:+      // ignore transient errors so the batch keeps going
```

依 Comment Sicko 的規則判決（本課作者手動做的）：

| 註解 | 判決 | 理由 |
|---|---|---|
| `// Phase 1: dedupe users` | 刪 | 重述下一行的 `helpers.dedupe`；不在保留清單裡。poteto-mode 的 Comments 規則也點名「no phase-narrating comments such as `// Phase 1`」 |
| `// Phase 2: send` | 刪 | 同上 |
| `// Kept for compatibility with existing callers.` | 刪，並標 `sendAll` 為 **MUST KILL** | 宣稱了一個外部限制（「existing callers」），但 `sendAll` 只有一個內部呼叫者，限制不成立（練習 B 的發現 5）。該做的是遷移呼叫端並刪掉其中一個名字 |
| `// ignore transient errors so the batch keeps going` | 刪，並標那個 `catch` 為 **MUST KILL** | 這是一段為 workaround 辯護的註解，正是「a long justification ... is a confession」。它指向的程式碼就是練習 B 的發現 1（吞錯誤讓使用者靜默地收不到通知）。Fix Root Causes：「If a workaround needs a paragraph-long comment to justify it, the code is wrong.」 |

四條全刪，兩個 `MUST KILL` 旗標指向的正好是練習 B 裡最嚴重的兩個問題。這不是巧合：**為程式碼辯護的註解，常常就是那段程式碼有問題的證據**。Comment Sicko 透過刪註解，讓那些問題失去掩護。

## 五、technical-writing：四層標準

`/technical-writing` 用於文件、RFC、README、PR 描述、commit 訊息。開頭三條在所有層之上：

- **刪掉每一個不做事的字**
- **用短的、日常的字**（use 不用 utilize）
- **當一條規則讓句子變差時，用別的方式修句子，或不要動它**：「A sentence that follows every rule and sounds like a machine wrote it has failed.」

還有：「**The codebase is the word list.** Write the real symbol, file, flag, or command name, not a synonym or a description of it.」

### 第一層：Diátaxis，先選模式

> One document, one mode.

| | 服務學習 | 服務工作 |
|---|---|---|
| **引導行動** | **Tutorial**：說出學習者會做出什麼；每一步都有看得到的結果；用「我們」與命令句 | **How-to**：解決一個人有的問題；假設讀者有能力；只有動作，不離題 |
| **引導理解** | **Explanation**：一個有邊界的主題；給背景、設計決策、歷史、限制；**只有這裡允許表達意見** | **Reference**：只描述；不指導、不說服、沒有意見；乾、完整、確定 |

「Don't mix modes: no reference tables inside a tutorial, no tutorial hand-holding inside reference, no arguing inside a how-to. Split and link instead.」

回頭看這門課本身：每一章是 **explanation**（為什麼這樣設計）混著 **tutorial**（跟著做實驗）。依 Diátaxis，這是混合模式。這個 repo 的 `learn` 技能刻意選擇了這種「一章包辦概念、機制、範例、踩雷」的教材格式。兩種標準服務不同的目標：Diátaxis 服務**查閱與工作**中的文件，這個 repo 服務**從頭到尾讀完的教材**。知道你為什麼偏離一個標準，比盲目遵守它更重要。

### 其他三層

- **Google 開發者文件風格**：句子寫給讀者（second person、present tense、active voice）
- **STE（Simplified Technical English）**：一個動作一個字、保留冠詞、能用平白動詞就不用 -ing
- **Global English**：給非母語讀者的句法

Opening a PR playbook 要求 PR 標題、描述、commit 內文都用 technical-writing（除了 Diátaxis 那層），再套 unslop。

## 底層機制：為什麼註解要交給「沒寫過的人」

```
   作者審自己的註解                            另一個 agent 審
   ──────────────────                        ─────────────
   「這個註解解釋了一個重要的細節」              「這個細節是我們自己的程式碼造成的嗎？」
   （作者知道那個細節，所以覺得註解有用）         → 是：改程式碼讓它不需要註解
                                              → 否（外部限制）：證明它今天在一條活的路徑上成立
                                              → 不確定：刪
   作者的盲點：他看不出程式碼本身不清楚，          審查者的優勢：他沒有作者的 context，
   因為他知道它在做什麼                         所以看得出哪裡需要 context 才看得懂
```

這和 Ch 1 的信條 3「寫的人不能自己判」完全一致。Comment Sicko 的「When I am not sure a keep clause applies, the comment dies」是一個刻意偏向刪除的預設：保留一個註解的舉證責任在註解那一方。

（社群移植版在 0.9.106 對這個預設做了一個 policy 修改：一個陳述外部依賴規則的註解，Comment Sicko 要去查那個依賴的文件或原始碼；查不到或無法確定時標為 `HOLD` 而不是刪除。這是 Ch 4 說的「翻譯本加入了翻譯者的詮釋」的一個例子——而且是一個合理的改進。）

## 對比與取捨

| 工具 | 處理什麼 | 誰判斷 | 會改程式碼嗎 | 限制 |
|---|---|---|---|---|
| unslop | 文字裡的 AI 特徵 | 規則清單 + 判斷 | 否（只改文字） | 很多規則需要判斷；英文中心 |
| unslop-lint（本章） | 能字串比對的那幾條 | 正規表示式 | 否 | 會漏、會誤抓；抓不到「沒有內容」 |
| no-comments / Comment Sicko | 註解 | 另一個 agent | 會（修被接受的旗標、可能跑 architect） | 刪除偏向；外部依賴規則的判斷要查證 |
| technical-writing | 文件的結構與句子 | 四層標準 | 否 | Diátaxis 不一定適合教材 |
| deslop（cursor-team-kit） | 程式碼裡的多餘 | 另一個技能 | 會 | 不在 pstack 內 |

## 踩雷集錦

1. **把 lint 0 個發現當成寫得好**：本章改寫前的描述有 11 個發現，但真正的問題是「沒有內容」，lint 抓不到。0 個發現只代表沒犯那幾種可以被比對的錯。
2. **把英文規則直接套到中文**：中文的破折號是正常標點。規則要理解它在擋什麼，再翻譯。
3. **讓作者自己審自己的註解**：作者知道每個細節，所以覺得每個註解都有用。交給沒寫過的人。
4. **用註解解釋自己程式碼的怪行為**：Comment Sicko 會刪掉它並標 `MUST KILL`。該改的是程式碼。
5. **過度壓縮**：unslop 第 33 條。「Parser rejects bad date → exit 2, no write」讓讀者解碼；寫完整的句子。

## 進階：再往深一層

- **寫一份中文版的 unslop-lint**：從這個 repo 的 `learn` 技能與本章的對照表收集中文的 AI 特徵詞（「賦能」「深入探討」「至關重要」「不僅……更……」「希望這對你有幫助」），寫成 lint，對這個 repo 跑一次。你會像 Ch 15 那樣發現很多違規嗎？
- **把 Comment Sicko 的保留清單變成 lint 的一部分**：「`// Phase N:`」「`// TODO`」「`// HACK`」「`// IMPORTANT`」這些模式可以機械地抓出來，作為 Comment Sicko 的預篩。剩下的才需要 agent 判斷。
- **讀 technical-writing 的完整四層**：特別是「Vary the rhythm」一節——它在警告另一種失敗：一份遵守所有規則、每句都很短、沒有任何觀點的文件，讀起來像機器寫的。「Have a view where the mode allows it.」

## 動手練習

1. 照本章跑 unslop-lint，然後拿你最近讓 agent 寫的一份 PR 描述或文件跑一次。有幾個發現？再用第 27 條手動檢查：哪幾句「可以原封不動出現在別的專案裡」？
2. 在你的一個 repo 跑 `git diff main -- '*.ts' '*.js' | grep -E '^\+.*//'`，對每一條新增的註解用 Comment Sicko 的保留清單判決。有幾條能留下？刪掉的那些，有沒有哪一條指向一段需要重構的程式碼？
3. 拿這門課的任一章，用 Diátaxis 標出每一節是哪一種模式。你同意本章說「教材刻意混合模式」的取捨嗎？如果要把它拆成符合 Diátaxis 的文件，會拆成幾份？

## 本章重點整理

- unslop 是一份有穩定編號的 AI 文字特徵清單；編號讓其他技能能引用、刪除時留空號
- 能字串比對的規則可以編碼成 lint（本章：改寫前 11 個、改寫後 0 個），但「沒有內容」這個最大的問題只能靠判斷與第 27 條
- 英文規則搬到中文要先理解它在擋什麼：破折號不適用，AI 詞彙、「不僅……更……」、聊天機器人用語都適用
- no-comments 把註解交給沒寫過它們的 Comment Sicko；保留清單很短，不確定就刪；自己程式碼的意外要改程式碼、不准用註解解釋
- 本章的判決：練習 B 的四條新增註解全刪，兩個 MUST KILL 正好指向那個 PR 最嚴重的兩個問題
- technical-writing 先用 Diátaxis 選模式（tutorial、how-to、reference、explanation），一份文件一種模式

## 自我檢核

- [ ] 不看本章，能說出 unslop 為什麼用穩定編號、而且刪除規則時要留空號嗎？
- [ ] 本章改寫後的 PR 描述 lint 是 0，為什麼這不足以證明它寫得好？你會用什麼來判斷？
- [ ] Comment Sicko 對「我們自己程式碼的意外」和「外部依賴強加的行為」處理方式不同，為什麼？
- [ ] 找一個你寫過、你覺得「很重要所以加了註解」的地方。用 Comment Sicko 的規則判決它，結果是什麼？

## 延伸閱讀

### 原始碼

- **[skills/unslop/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md)**
  - **讀哪裡**：全文，特別是 26、27、32、33 四條
- **[agents/comment-sicko.md](https://github.com/cursor/plugins/blob/main/pstack/agents/comment-sicko.md)** 與 **[skills/no-comments/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/no-comments/SKILL.md)**
  - **讀哪裡**：Comment Sicko 的保留清單；no-comments 的 step 2（怎麼審 Comment Sicko 的報告）
- **[skills/technical-writing/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/technical-writing/SKILL.md)**
  - **讀哪裡**：開頭三條、「Vary the rhythm」、「Pick the mode first」

### 官方文件與標準

- **[Diátaxis](https://diataxis.fr/)** — Daniele Procida
  - **讀哪裡**：首頁的四象限圖，與「The compass」一節
  - **能學到什麼**：technical-writing 第一層的原始框架
- **[Google developer documentation style guide](https://developers.google.com/style)**
  - **讀哪裡**：「Voice and tone」「Write for a global audience」
  - **能學到什麼**：第二層與第四層的原始標準

### 文章

- **[Wikipedia — Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)**
  - **這是什麼**：Wikipedia 編輯整理的 AI 生成文字特徵清單
  - **和本章的關聯**：unslop 的很多規則（AI 詞彙、「not just X, but Y」、硬湊三點、膚淺的 -ing 片語）都能在這裡找到對應；這份清單有大量真實的例子
  - **注意**：這是社群維護的頁面，內容會持續變動
- **[Jeff Atwood — Code Tells You How, Comments Tell You Why](https://blog.codinghorror.com/code-tells-you-how-comments-tell-you-why/)**
  - **和本章的關聯**：poteto-mode 的「Keep a comment only for a non-obvious *why* the code can't show」的經典論述；Comment Sicko 比它更激進——連「why」都要先試著用程式碼表達

清理完了，接下來是交付：開 PR、盯 PR、合併 PR。

→ [Ch 24 開 PR、Babysit 與 Shipping](./24-pr-babysit-shipping.md)
