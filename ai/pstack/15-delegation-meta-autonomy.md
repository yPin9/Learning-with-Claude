# Ch 15 — Delegation、Meta 與 Autonomy

> **目標**：讀完 24 條原則的最後三條——兩條 Delegation（Guard the Context Window、Never Block on the Human）與一條 Meta（Encode Lessons in Structure）——以及 poteto-mode 的 Autonomy 段落。這幾段合起來回答：**哪些工作留在主 session、哪些交出去；什麼時候可以不問人；同一個錯誤犯第二次時該怎麼辦。** 你會在這個 repo 上量出一次搜尋的原始輸出和它的摘要差了 733 倍，並把這個 repo 的 `learn` 技能裡一條「寫在文字裡」的規則變成 lint——然後發現它在全 repo 有 510 次違規，包括本課作者剛寫好的章節。

> **環境**：pstack 0.15.36、Node.js 26（`Object.groupBy` 需要 Node 21 以上）。所有量測與 lint 結果都是在這個 repo 上實際跑出來的（2026-10-28）。

## 為什麼需要這個？

前面 21 條原則談的是「怎麼把工作做對」。這三條談的是**工作流本身**：

- agent 的 context window 是有限的。把它塞滿大量原始資料，它對任務的判斷會變差。
- agent 會傾向停下來問人。每問一次，人就變成瓶頸，「讓 agent 長時間自主工作」就不可能。
- agent 會重複犯同一個錯。你每次都在 prompt 裡提醒，提醒會被忘記、被壓縮掉、被新的 session 遺失。

## 先建立直覺：一個好的主管怎麼帶一個團隊

```
  Guard the Context Window          Never Block on the Human        Encode Lessons in Structure
  ────────────────────────          ────────────────────────        ───────────────────────────
  主管不親自讀每一份 log。            可以撤回的決定，團隊自己決定，     同一個錯第二次發生時，
  他叫人讀，聽摘要。                  事後報告；                       不是再開一次會提醒大家，
  他的注意力留給判斷。                不可撤回的（上線、刪資料、         而是改流程、加檢查，
                                    對客戶發信）才來問。              讓這個錯做不出來。
```

## 一、Guard the Context Window

> **索引**：Context fills up: large outputs, long files, repeated reads, fan-out planning. Route bulk to subagents, keep summaries in the main thread.

> **Why:** Context overflow degrades reasoning quality, creates compression artifacts, and halts progress.

leaf 全文很短，核心做法一條：「Size phases and cap scope. Limit files per phase, set turn budgets, account for mechanism costs.」但這條原則在 pstack 裡**被引用的方式**比全文更能說明它：

| 位置 | 怎麼用這條原則 |
|---|---|
| `how` | 複雜問題開 2–4 個 explorer，主 session 只拿回報 |
| `recall` | 「The raw transcripts stay in the subagents. The main thread gets only their findings.」 |
| Runtime / Trace forensics | 「Parse large artifacts in a subagent ... keep the reduced finding in the main thread.」 |
| Session pickup | 長 transcript 在 subagent 裡解析，主 session 只留縮減後的時間線 |
| Hillclimb | 「Supervise and review the diff rather than typing it」 |
| Multi-phase plan | 探索交給 subagent，「Each returns file pointers ... No inlined dumps.」 |
| poteto-mode Subagents | 「file pointers not inlined context」 |

### 量一下：原始輸出 vs 摘要

在這個 repo 搜尋所有提到 subagent 的地方：

```bash
raw=$(grep -rn --include='*.md' -i "subagent" .)
printf "raw: %s lines, %s bytes\n" "$(printf '%s\n' "$raw" | wc -l)" "$(printf '%s' "$raw" | wc -c)"
```

```
raw: 387 lines, 90890 bytes
```

同一份結果，摘要成「每個資料夾有幾處」：

```bash
printf '%s\n' "$raw" | cut -d: -f1 | xargs -n1 dirname | sort | uniq -c | sort -rn | head -5
```

```
 204 ai/harness_engineering
  95 ai/ai_applications
  67 ai/pstack
  19 ai/spec_driven_development
   1 .claude/skills/learn
summary: 124 bytes (top 5 of 6 dirs)
```

90,890 位元組 vs 124 位元組，差 733 倍。如果主 session 要的答案是「哪門課最常提到 subagent」，摘要就夠了；原始輸出的 90KB 全部是雜訊。在 context 裡，這 90KB 大約是兩萬多個 token（以英文約 4 位元組一個 token 粗估；中文字元比例高時 token 數會更多），它會一直留在對話裡，直到被壓縮。

**誰來做摘要？** 有兩種做法：

- **主 session 自己用工具摘要**（像上面用 `uniq -c`）：最便宜，前提是你事先知道要什麼形狀的摘要
- **交給 subagent 讀原始資料、回報結論**：subagent 的 context 吞下那 90KB，主 session 只拿到回報。適合「要讀懂才能摘要」的情況

Ch 3 的 context 分配表就是這條原則的整體圖像。

**和 Minimize Reader Load 的對應**（Ch 12）：Minimize Reader Load 的原文說「This is the human analog of Guard the Context Window. Working memory is finite for readers too.」兩條原則講的是同一個限制，一條對人、一條對 agent。

## 二、Never Block on the Human

> **索引**：Tempted to ask "should I do X?" on reversible work. Proceed, present the result, let the human course-correct.

> The human supervises asynchronously. Agents must stay unblocked. Make reasonable decisions, proceed, and let the human course-correct after the fact. Since code changes are reversible and reviewable, a wrong decision usually costs less than blocking.

做法：

- **Proceed, then present.** 不要問「要不要做 X？」，做 X，說明為什麼。
- **讓系統自我修復**：發現問題，記下來，下一輪修。
- **不可逆的動作仍然需要確認**：force-push、刪 production 資料、對外發訊息。
- **可逆的動作直接做**：寫程式、改筆記、拆任務。
- **產品方向來自人，執行不該卡住。**

最後一條是這條原則的界線：它不是「agent 什麼都自己決定」，而是「**執行層面**的決定自己做，**方向層面**的決定問人」。Ch 2 講過的那條 Non-negotiable 是它的執行細節：要問人之前，先判斷這是不是一個「跑一下就知道答案」的事實問題；是的話就 prototype，不問。

```
  agent 想問人一個問題
          │
   ┌──────┴──────┐
   │ 可以撤回嗎？   │
   └──────┬──────┘
   否 ↙        ↘ 是
  問人          ┌────────────────┐
               │ 是事實還是偏好？   │
               └────────┬───────┘
            事實 ↙            ↘ 偏好（產品方向）
         跑一下、prototype      用合理預設繼續，
         讓結果決定             在回覆裡說明用了什麼預設、
                              使用者可以怎麼改
```

右下角那條路來自 poteto-mode 的原文：「Under the grant, for a call only the operator can make, apply a default, report it with a full explanation, and say in plain words what the operator could tell you to do instead.」就算是偏好問題，在使用者授權完全自主的時候，也是**先用預設值做、再報告**，而不是停下來等。

還有一個很細的規定：「The operator answers in their own words. Never give a shorthand token to type back.」——不要給使用者一個「回覆 A 或 B」的選單，讓他用自己的話回答。

## 三、Encode Lessons in Structure

> **索引**：You catch yourself writing the same instruction a second time. Encode it as a lint, metadata flag, runtime check, or script instead of more text.

leaf 全文只有兩句（558 位元組）：

> If the lesson needs judgment and cannot be encoded, make the instruction more prominent and add an example of the failure mode.
>
> **Corollary:** If the fix is structural, only use the structural fix. The instruction is the symptom.

第二句是整條原則的核心：**「指示是症狀」**。你需要在 prompt 裡寫「記得 X」，代表系統沒有讓 X 自動發生。

### 實驗：把這個 repo 的一條文字規則變成 lint

這個 repo 的 `.claude/skills/learn/SKILL.md` 裡有一條規則：

> **嚴禁輕描淡寫詞**：`just`、`simply`、`trivially`、`obviously`、`easily`、`只需要`、`很簡單`、`顯然`——這些詞是在告訴讀者「你應該覺得這很簡單」，讓卡住的讀者覺得是自己的問題。刪掉。 <!-- allow-banned: 引用 learn 技能的禁用字規則原文 -->

這條規則一直以文字的形式存在於技能裡，每次寫課都會被載入。它有用嗎？寫一支 lint 檢查：

```js
// banned-words.mjs：檢查 markdown 裡的輕描淡寫詞，略過 code block 與標記了 allow-banned 的行
import fs from "node:fs";
import path from "node:path";

const BANNED = ["只需要", "很簡單", "顯然", "simply", "trivially", "obviously"];
const root = process.argv[2];
const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) =>
  e.name.startsWith(".") ? [] : e.isDirectory() ? walk(path.join(d, e.name)) : e.name.endsWith(".md") ? [path.join(d, e.name)] : []);
const hits = [];
for (const file of walk(root)) {
  let fence = false;
  fs.readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (line.trimStart().startsWith("```")) fence = !fence;
    if (fence || line.includes("allow-banned:")) return;
    for (const w of BANNED) if (line.includes(w)) hits.push({ file: path.relative(root, file), line: i + 1, w });
  });
}
const byWord = Object.groupBy(hits, (h) => h.w);
for (const [w, list] of Object.entries(byWord)) console.log(`${String(list.length).padStart(5)}  ${w}`);
console.log(`${hits.length} hits in ${new Set(hits.map((h) => h.file)).size} files`);
for (const h of hits.slice(0, Number(process.env.SHOW ?? 0))) console.log(`${h.file}:${h.line} ${h.w}`);
process.exit(hits.length ? 1 : 0);
```

（`just` 和 `easily` 沒有放進清單：英文的 `just` 太常以「剛剛」的意思合法出現在引文與指令裡，會產生大量誤報。這是一個刻意的取捨——編碼成結構時，有些規則只能部分編碼。）

對整個 repo 跑（第一版，還沒有 `allow-banned` 例外機制）：

```
  415  只需要
   71  很簡單
   19  顯然
    1  obviously
    4  trivially
510 hits in 398 files
exit=1
```

**510 次違規，分佈在 398 個檔案裡。** 一條每次都被載入的文字規則，擋不住這件事。

更有說服力的是對這門課本身跑：

```
    1  顯然
    2  只需要
    1  obviously
    1  很簡單
5 hits in 4 files
03-run-lifecycle.md:300 顯然
05-router-design.md:33 只需要
05-router-design.md:76 只需要
07-investigation-how-why.md:224 obviously
11-principles-as-vocabulary.md:9 很簡單
```

**本課作者在寫這幾章時，那條規則就在 context 裡，仍然違反了 4 次。** 第 5 次（Ch 7 第 224 行）是誤報：那一行在引用 `why` 技能的禁用字清單，引文本身就含有 `obviously`。 <!-- allow-banned: 說明誤報時需要提到這個字 -->

修正方式依 `/correct` 技能的規則：「Exceptions go on the offending line with a reason」。真的違規的 4 處改寫；誤報的那一行加上一個 HTML 註解（markdown 渲染時看不見）：

```markdown
- **不准用的字**：obviously、clearly、... <!-- allow-banned: 引用 epistemics.md 的禁用字清單 -->
```

lint 加上一行規則：含有 `allow-banned:` 的行跳過。再跑：

```
0 hits in 0 files
exit=0
```

而 repo 的其他課程（修掉本課的 5 處之後）：

```
505 hits in 394 files
```

這 505 處本章不修——它們不在這門課的範圍內，而且應該由一次專門的清理處理（這正是 Ch 30 的 `/correct` 會做的事：「If the pattern is already common, fail only when a change adds more.」也就是先讓 lint 只擋**新增**的違規，舊的分批清）。

這個實驗示範了原則的每一個部分：

| 原則的說法 | 實驗裡對應的事 |
|---|---|
| 「同一個指示寫第二次」 | learn 技能每次寫課都載入這條規則 |
| 「The instruction is the symptom」 | 510 次違規證明文字規則沒有生效 |
| 「Encode it as a lint」 | banned-words.mjs |
| 「If the lesson needs judgment and cannot be encoded, make the instruction more prominent」 | `just` 無法可靠編碼，留在文字規則裡 |
| 例外要寫在那一行並附理由 | `allow-banned:` 註解 |

## 四、Autonomy：四句話的權限邊界

poteto-mode 的 Autonomy 段落（Ch 2 讀過結構）把 Never Block on the Human 落實成具體的權限：

```
**Just do it.**      Use any MCP tool. Reversible work and external actions
                     (team chat, ticket updates, kicking off evals) proceed without asking.
**Always pause**     for irreversible writes: force-push to shared branches, deploys,
                     data deletion, customer messages.
**Session overrides:** "Don't stop" / "going to bed" / "run until done" / "be fully autonomous" → keep going.
**No is an acceptable answer.**
```

注意第一句的一個細節：**「external actions (team chat, ticket updates, kicking off evals)」被歸在「直接做」**。在團隊聊天室發訊息、更新 ticket，在很多人的直覺裡是「對外」的動作、應該問。pstack 的判斷是：這些是可以撤回或修正的（訊息可以補充、ticket 可以改回來）。真正要停的是**客戶訊息**和**不可逆的寫入**。

這個界線你不一定要同意。在 Claude Code 裡，pstack 的 Autonomy 和 Claude Code 自己的權限模式是**疊加**的：

```
  agent 想做某個動作
          │
          ├─ pstack Autonomy：這是 Always pause 的嗎？ ── 是 ──▶ 停下來問
          │                                   否
          ▼
          ├─ Claude Code 權限模式：允許這個工具/指令嗎？ ── 否 ──▶ 被擋（或跳出確認）
          │                                       是
          ▼
        執行
```

兩層都放行才會執行。如果你的團隊規定「在 Slack 發訊息要先確認」，用 Claude Code 的權限設定擋住那個 MCP 工具，比修改 pstack 的文字更可靠（Encode Lessons in Structure）。

**「No is an acceptable answer」** 擋的是另一種失敗：討好。「A recommendation is a judgment, not a validation. Agreement is not the default, candor over sycophancy.」當你問 agent「要不要也順便做 Y？」，它應該可以回答「這不值得做」。

## 底層機制：三條原則怎麼讓長時間自主執行成為可能

```
  沒有這三條                                有這三條
  ──────────                               ──────────
  context 被原始資料塞滿                     主 session 只留摘要與證據
  → 30 分鐘後忘了原始目標                    → 數小時後仍記得目標（Guard）

  每個小決定都停下來問                       可逆的自己決定，事後報告
  → 人離開 10 分鐘就卡住                     → 人可以去睡覺（Never Block）

  同樣的錯每次都要人提醒                     錯誤變成 lint / 檢查 / 腳本
  → 長時間執行累積更多錯                     → 越跑越少錯（Encode Lessons）
```

Part 5 的 Autonomous run、figure-it-out、Autopilot 都建立在這三條原則上。官方指南第 7 頁的「Earn the trust before the loop」清單裡，最後一條就是：「You've read a few transcripts and turned the repeated failures into tools, skills, or checks.」——這是 Encode Lessons in Structure 被列為**讓 agent 自主執行之前的前提條件**。

## 對比與取捨

| 原則 | 它省下的資源 | 代價 | 失效的情況 |
|---|---|---|---|
| Guard the Context Window | 主 session 的 context | 多一層 subagent、摘要可能遺漏細節 | 主 session 真的需要原始細節來做判斷時 |
| Never Block on the Human | 人的時間、等待時間 | 偶爾做錯要撤回 | 動作不可逆、或撤回成本很高時 |
| Encode Lessons in Structure | 每次提醒的成本、遺忘 | 寫與維護 lint 的成本、誤報 | 規則需要判斷、無法機械檢查時 |

## 踩雷集錦

1. **主 session 自己讀完整個大檔案**：讀一個 5,000 行的 log「看看有什麼」，比叫 subagent 讀完回報「有哪幾種錯誤、各幾次」多花幾十倍的 context。先想清楚你要的答案是什麼形狀。
2. **摘要掉了關鍵細節**：subagent 回報「沒有錯誤」，但它把 warning 當成不重要的過濾掉了。brief 裡要明確寫出要回報什麼（how 的 explorer 回報格式包含「Non-Obvious Things」與「Open Questions」，就是為了防這個）。
3. **把「直接做」延伸到不可逆的動作**：Never Block 明確排除 force-push、刪資料、對外發訊息。agent 在「going to bed」模式下 force-push 到共用分支，是違反 Always pause，不是 session override 的合理延伸。
4. **用更多文字對付重複的錯**：在 CLAUDE.md 再加一條粗體、加三個驚嘆號。原文：「If the fix is structural, only use the structural fix. The instruction is the symptom.」本章的 510 次違規就是文字規則的上限。
5. **lint 沒有例外機制**：本章第一版的 lint 把引文也算成違規。沒有例外機制的 lint 會被人繞過或關掉；有例外但要附理由，例外本身就變成可以審查的東西。

## 進階：再往深一層

- **把 banned-words.mjs 只擋新增的違規**：用 `git diff --unified=0 main -- '*.md'` 只取這次改動新增的行，只檢查那些行。這是 `/correct` 說的「If the pattern is already common, fail only when a change adds more」——讓 lint 今天就能上線，而不用先清完 505 處。
- **在 Claude Code 裡用 PostToolUse hook 跑 lint**：每次 `Write` 或 `Edit` 一個 `.md` 檔之後自動跑 banned-words.mjs，把結果加回 context。agent 會在寫完的當下看到違規並修正，而不是等到最後。這比本章事後跑一次更接近「讓錯誤做不出來」。
- **Guard the Context Window 的量化**：Claude Code 的 `/context` 指令（或 status line）可以看到目前 context 的用量。在一次長任務裡定期看它，你會看到哪一類工具呼叫吃掉最多 context——通常是大檔案的 Read 與大量的搜尋輸出。

## 動手練習

1. 在你自己的一個 repo 裡，找一條你在 PR review 裡寫過三次以上的評論。把它寫成一支 lint（哪怕只有 20 行），跑一次看有幾處違規。
2. 下一次你想在 prompt 裡寫「記得……」時，停下來問：這能不能變成一個 hook、一個 lint、一個測試？
3. 列出你的 agent 最近停下來問你的五個問題。用本章的決策圖分類：哪些是不可逆的（該問）、哪些是事實（該 prototype）、哪些是偏好（該用預設值繼續並說明）？

## 本章重點整理

- Guard the Context Window：大量讀取交給 subagent 或工具摘要，主 session 只留結論；本章的搜尋原始輸出與摘要差 733 倍
- Never Block on the Human：可逆的做了再報告；不可逆的才問；事實問題用 prototype 回答；偏好問題在完全授權下用預設值繼續並說明
- Encode Lessons in Structure：「指示是症狀」；能編碼的就編碼成 lint / 檢查 / 腳本，需要判斷的才留在文字裡，並加上失敗的例子
- Autonomy 的界線是可逆性；在 Claude Code 裡它和權限模式疊加，兩層都放行才會執行
- 本章的 lint 在整個 repo 找到 510 次違規，包括本課作者寫的 4 次——一條每次都被載入的文字規則，擋不住它想擋的事

## 自我檢核

- [ ] 不看本章，能說出 Encode Lessons in Structure 的 Corollary，並用本章的實驗解釋它嗎？
- [ ] 「在團隊聊天室發訊息」在 pstack 的 Autonomy 裡屬於哪一類？你同意嗎？在 Claude Code 裡你會怎麼改變這個行為？
- [ ] 什麼時候主 session 應該自己讀原始資料，而不是交給 subagent？
- [ ] 本章的 lint 為什麼不檢查 `just`？這個決定反映了 Encode Lessons in Structure 的哪一句話？

## 延伸閱讀

### 原始碼

- **三條 leaf 檔**：[guard-the-context-window](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-guard-the-context-window/SKILL.md)、[never-block-on-the-human](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-never-block-on-the-human/SKILL.md)、[encode-lessons-in-structure](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-encode-lessons-in-structure/SKILL.md)
  - **讀哪裡**：全部，三份加起來不到 2KB
- **[skills/correct/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/correct/SKILL.md)**
  - **讀哪裡**：整份
  - **能學到什麼**：Encode Lessons in Structure 被推到整個 repo 的版本；本章的例外機制與「只擋新增」都來自這裡（Ch 30 詳談）

### 官方指南

- **[docs/guide/07-overnight.md「Earn the trust before the loop」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/07-overnight.md)**
  - **讀哪裡**：第一節的四項清單
  - **能學到什麼**：三條原則為什麼是自主執行的前提

### 官方文件

- **[Claude Code 官方文件：Permissions / settings](https://code.claude.com/docs/en/settings)**
  - **讀哪裡**：permission modes 與 allow / deny 規則
  - **能學到什麼**：怎麼用結構（而不是文字）限制 agent 的動作，和 pstack 的 Autonomy 疊加

### 文章

- **[Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)**
  - **這篇說什麼**：context 是有限資源、「context rot」、用 subagent 隔離大量探索
  - **和本章的關聯**：Guard the Context Window 的完整論證；也解釋了為什麼塞滿的 context 會讓推理變差

24 條原則讀完了。下一個練習：拿這套詞彙去審一個真實的 PR。

→ [練習 B：用原則審一個 PR](./practice-b-principle-review.md)
