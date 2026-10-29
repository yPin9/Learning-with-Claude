# Ch 2 — 解剖 poteto-mode 的 SKILL.md

> **目標**：逐段讀懂 poteto-mode 這份 152 行、3,343 個英文字的技能檔：每一段在控制 agent 的哪一種行為、為什麼要這樣寫、如果拿掉會發生什麼事。讀完你能不看原文畫出它的八段結構，並說出至少五個「看起來像廢話、其實在擋一種特定失敗」的句子。

> **環境**：pstack 0.15.36。本章引用的是 Cursor 原版 `pstack/skills/poteto-mode/SKILL.md`；Claude Code 裝的副本只在開頭多一行「先讀 adapter 文件」（Ch 4 詳談），其餘逐字相同。

## 為什麼需要這個？

大多數人寫 agent 指示的方式是「想到什麼寫什麼」：一段角色設定、幾條注意事項、一句「請仔細思考」。這種指示的問題是**沒有結構**——你不知道哪一句在管哪一種行為，所以 agent 出錯時你也不知道該改哪一句。

poteto-mode 是一份**被大量使用、被反覆修改過**的指示（Ch 1 看過它半年 169 個 commit）。它的每一段都有清楚的職責。讀懂它的結構，等於拿到一份「agent 指示該分成哪幾塊」的參考答案。

## 先建立直覺：它是一份「條件 → 動作」的規則表，不是一篇散文

把整份檔案攤開，你會發現它幾乎沒有「請你成為一個優秀的工程師」這種句子。它的主要句型是：

```
   <觸發條件>  →  <要做的具體動作或要叫的技能>
```

例如：

```
   Any code                        → name the data shape first
   Code crossing a function boundary → the architect skill
   Before commit                   → the deslop skill
   Broken skill mid-task           → fix it in its own PR. Don't block.
```

這種寫法有一個重要性質：**每一條都可以被檢查**。「它有沒有在寫 code 前先命名資料形狀？」看 transcript 就知道。「請仔細思考」就無法檢查。這是整份檔案最重要的寫作原則，後面每一段都在用。

## 全貌：八段結構與篇幅分配

我們寫一個小腳本量每一段的字數（這本身就是 Build the Lever 原則的小示範——與其目測，不如寫個能重跑的工具）：

```js
// sections.mjs：量一份 SKILL.md 每個 ## 段落的英文字數與條列數
import fs from "node:fs";

const file = process.argv[2];
const text = fs.readFileSync(file, "utf8");
const body = text.replace(/^---\n[\s\S]*?\n---\n/, "");   // 去掉 frontmatter
const sections = [];
let current = { title: "(preamble)", words: 0, bullets: 0 };
for (const line of body.split("\n")) {
  const h = line.match(/^## (.+)/);
  if (h) {
    sections.push(current);
    current = { title: h[1], words: 0, bullets: 0 };
    continue;
  }
  current.words += line.split(/\s+/).filter(Boolean).length;
  if (/^- /.test(line)) current.bullets += 1;
}
sections.push(current);
const total = sections.reduce((n, s) => n + s.words, 0);
for (const s of sections) {
  const pct = ((s.words / total) * 100).toFixed(1).padStart(5);
  console.log(`${s.title.padEnd(20)} ${String(s.words).padStart(5)} words ${pct}%  bullets=${s.bullets}`);
}
console.log(`${"total".padEnd(20)} ${String(total).padStart(5)} words`);
```

```bash
node sections.mjs $PSTACK/skills/poteto-mode/SKILL.md
```

實際輸出（對 Cursor 原版跑）：

```
(preamble)              21 words   0.6%  bullets=0
Non-negotiables        774 words  23.2%  bullets=19
Principles             692 words  20.7%  bullets=24
Autonomy               106 words   3.2%  bullets=0
Subagents              724 words  21.7%  bullets=0
Writing the reply      244 words   7.3%  bullets=7
Comments                67 words   2.0%  bullets=0
Playbooks              715 words  21.4%  bullets=23
total                 3343 words
```

四大塊幾乎平分（各約 21–23%）：**觸發規則、原則索引、委派規則、playbook 索引**。這個分配本身就透露了作者的判斷：「什麼時候做什麼」「用什麼詞彙判斷」「怎麼分工」「走哪套流程」同等重要；而「怎麼寫回覆」「自主權邊界」「註解規則」是比較短的補充約束。

```
   poteto-mode/SKILL.md
   ┌────────────────────────────────────────────┐
   │ frontmatter   name / description /          │  ← 決定「什麼時候被打開」
   │               disable-model-invocation      │
   ├────────────────────────────────────────────┤
   │ preamble      bundled skills 在 ../<name>/  │  ← 讓它找得到其他技能
   ├────────────────────────────────────────────┤
   │ Non-negotiables   觸發 → 動作 （19 條）       │  ← 行為觸發器
   │ Principles        24 條原則的一行索引         │  ← 判斷詞彙
   │ Autonomy          做 / 停 / 覆寫 / 說不        │  ← 權限邊界
   │ Subagents         誰寫、用什麼模型、何時新開   │  ← 分工
   │ Writing the reply 回覆的句型規則               │  ← 輸出格式
   │ Comments          註解只留不明顯的 why         │  ← 程式碼輸出格式
   │ Playbooks         23 份 playbook 的一行索引    │  ← 流程路由
   └────────────────────────────────────────────┘
```

下面逐段拆。

## 一、frontmatter：控制「什麼時候被打開」

```yaml
---
name: poteto-mode
description: poteto's agent style for concise, detailed responses, deliberate subagents, unslopped prose, simple code, and verified work. Use for poteto, /poteto-mode, or requests to work in this style.
disable-model-invocation: true
icon: crown
color: yellow
---
```

- `description` 在一般技能裡是「模型判斷要不要打開我」的依據。但這裡加了 `disable-model-invocation: true`，模型**不會**自己打開它，所以 description 主要是寫給人看的選單說明。
- `icon` / `color` 是 Cursor 的 UI 欄位，Claude Code 不認，會被忽略。

**為什麼不讓模型自動觸發？** 想像你說「幫我把這個變數改名」，模型判斷「這是 refactoring」，自動打開 poteto-mode，抄 8 步 Refactoring playbook 進 todo、開 subagent、寫 characterization test……一個 10 秒的改名變成 10 分鐘。作者的選擇是讓**人**決定什麼時候付嚴謹稅。社群移植版做了相反的選擇（用 hook 讓模型在「改動超過一個檔案」等條件下自動進入），Ch 4 會比較這兩種選擇。

## 二、preamble：一句話解決「技能怎麼找到技能」

> Bundled skills live at `../<name>/SKILL.md` from this file, so put this file's absolute path in every `poteto-agent` prompt.

這句話解決兩個問題：

1. poteto-mode 要叫 `how`、`architect` 等技能。它們在哪？答案是「跟我同一層的兄弟資料夾」。
2. subagent 是全新的 context，它不知道 pstack 裝在哪。所以每次 spawn 都要把**絕對路徑**傳過去，subagent 才能自己去讀 poteto-mode 全文。

這是一個很容易被忽略的實作細節：**技能之間的引用要能在 subagent 裡解析**。很多人寫的技能在主 session 能用，交給 subagent 就壞，原因就是相對路徑或「你知道的那個檔案」在新 context 裡不存在。

## 三、Non-negotiables：19 條行為觸發器

這段開頭先定一條總則：

> The Principles section below grounds every trigger. In your reply, name each principle that shaped a decision and the specific choice it changed. Cite only principles whose leaf SKILL.md you read this session.

三句話，三個機制：

| 句子 | 擋的是什麼失敗 |
|---|---|
| 每個觸發都以原則為依據 | 規則變成無根據的教條 |
| 回覆要點名原則**以及它改變了哪個決定** | 「我應用了 Laziness Protocol」這種空洞的引用 |
| 只能引用本 session **讀過 leaf 檔**的原則 | 模型憑名字猜原則內容、亂引用 |

第三句特別值得記住。原則索引裡每條只有一行；完整規則在 leaf 技能裡。如果允許只看索引就引用，模型會依名字腦補（「Laziness Protocol 大概是叫我偷懶」），引用就變成裝飾。強制「讀過才能引用」，等於強制「引用之前先載入完整規則」。Ch 11 會整章談這個設計。

接著是 19 條觸發器。挑幾條拆：

**「第一則訊息要用自己的話重述目標」**

> Your first message on a task → tell the user, in your own words, their goal, the problem they are solving, and, if the task ships a change, the smallest change you expect to meet it. Don't wait for confirmation. If the evidence rules out the change the user asked for, show it and stop.

這條擋的是「誤解需求」——agent 最常見也最貴的失敗。重述讓誤解在寫任何程式之前就被看見。注意兩個細節：**「Don't wait for confirmation」**（重述不是請示，說完就繼續做，避免卡在人身上）；**「the smallest change you expect」**（預先說出最小改動，等於先承諾一個 diff 大小，之後膨脹了就有對照）。

**「要問人之前先分類」**

> About to `AskQuestion` on a "which approach" ... fork → classify it before you ask. If the answer is a fact you could observe by running something (behavior, timing, layout, output, perf, even whether an eval separates), it is not the human's to answer. Sketch it via the Prototype playbook ...

這條是 2026-06-06 加的（commit「self-unblock empirical forks with a sketch instead of asking」）。它把問題分成兩種：

```
   agent 想問：「要用 A 還是 B？」
                │
     ┌──────────┴──────────┐
     │ 答案能靠跑東西觀察到嗎？│
     └──────────┬──────────┘
        是 ↙          ↘ 否
   做 prototype         這才是人的決定
   讓結果決定            （產品取向、偏好）
   （不問）              → 問
```

「哪個 layout 比較好看」是偏好，問人；「哪個寫法比較快」是事實，自己量。agent 把事實問題丟給人，是在把自己該做的工作轉嫁出去。

**「碰到程式碼先命名資料形狀」**

> Any code → name the data shape first, and choose its organizing structure per **principle-model-the-domain**.

這條在 Feature、Refactoring playbook 裡會反覆出現。它擋的是「先寫邏輯、寫到一半才發現資料結構錯了」。

**「技能壞了就在獨立 PR 修，不要卡住」**

> Broken skill mid-task → fix it in its own PR. Don't block. Don't silently work around it.

三個選項中只允許一個：卡住（不行）、默默繞過（不行，問題會留著）、另開 PR 修（可以，主任務繼續）。這是 pstack「自我修復」設計的一環。

其他觸發器（Bugbot 留言要用懷疑的態度、PR 狀態請求走 Babysit playbook、長時間工作要留決策日誌……）後面的章節會一一遇到。

## 四、Principles：24 條原則的一行索引

格式非常一致：

```markdown
- **Laziness Protocol.** Refactoring, sizing a diff, or tempted to add abstractions,
  layers, or signal threading. Bias to deletion and the smallest change that solves the problem.
```

每一條 = **原則名** + **什麼時候適用** + **一句話結論**。完整規則在 `principle-laziness-protocol/SKILL.md`。

**為什麼索引要放在 poteto-mode 裡，不讓模型自己去找？** 因為 24 個原則技能也都是 `disable-model-invocation: true`，模型不會自己打開它們。索引是唯一的「目錄」：模型讀索引，判斷這次任務觸發了哪幾條，再去讀那幾條的全文。這是 progressive disclosure 的第二層：poteto-mode 打開後，原則只付索引的成本（約 700 字），需要哪條才付哪條的全文。

## 五、Autonomy：四句話畫出權限邊界

```
**Just do it.**      可逆的工作、對外動作（團隊聊天、更新 ticket、啟動 eval）直接做
**Always pause**     不可逆的寫入：force-push 共用分支、部署、刪資料、發訊息給客戶
**Session overrides** 「Don't stop」「going to bed」「run until done」→ 繼續做
**No is an acceptable answer.** 被問要不要做、被邀請擴大範圍時，給真實判斷
```

前三句定義**什麼時候可以不問人**。注意界線畫在「可逆 vs 不可逆」，不是「大 vs 小」。一個改 50 個檔案的重構是可逆的（git revert），直接做；一個 force-push 只動一行，但不可逆，要停。

第四句在擋另一種失敗：**討好（sycophancy）**。原文：「A recommendation is a judgment, not a validation. Agreement is not the default, candor over sycophancy.」模型傾向同意使用者的提議；這句明確授權它說「這不值得做」。

## 六、Subagents：委派的四條規則

這段雖然沒有條列，但其實是四條規則：

1. **用 `poteto-agent` 當 delegate 的型別**。它是一個包裝，開工前會先讀完 poteto-mode 全文——這樣 subagent 也帶著同一套紀律。但**不要**讓 poteto-agent 去跑 playbook 本身；playbook 由收到 `/poteto-mode` 的主 session 跑。
2. **每次 spawn 的預設值**：背景執行、可用 MCP 的模式、**傳檔案路徑而不是把內容貼進去**、明確指定模型。
3. **模型配合工作**：最難的變更（跨切面設計、複雜並發、精細演算法）給最強的判斷模型；機械性的編輯給快的程式模型；驗證固定用 swarm workers 的模型。
4. **預設開新的 subagent**：修正、追加、重試都開新的，把原始 brief、後續指示、前一個 agent 的報告與 branch 整合後交給它。只有在新工作**一定需要**舊 agent 身上的狀態（它的 checkout、未提交的變更、正在跑的 dev server）時才續用。

第四條的理由寫在最後一句：

> Interrupt-chained resumes silently drop directives, so fire a fresh subagent with consolidated scope rather than trusting a "done" summary.

一個被多次打斷、續用的 agent，會悄悄弄丟早期的指示。新開一個、把所有指示整合成一份 brief，比相信一個被打斷過三次的 agent 說「做完了」可靠。

還有一句定義了主 session 的責任：

> You own every subagent's work. Review the diff and write your own summary, don't pass through what it said.

**主 session 不是傳話筒**。Ch 16 會整章談委派紀律。

## 七、Writing the reply：回覆的句型規則

七條規則，摘要：

- 短的直述句，一句一個想法
- 全文不准出現長破折號（em dash）
- 句中不准用冒號當連接詞（條列前可以）
- 精簡不是刪內容的藉口
- **先講對使用者與維護者的影響，再講實作細節**
- **絕不捏造連結、引用、對話紀錄**；只連結本 session 產生或讀過的東西
- **每個斷言在同一句裡附上證據或標籤**：實測（measured）、推論（inferred）、猜測（guess）

最後兩條是整份檔案裡最值得帶走的。「每個斷言帶標籤」逼 agent 區分「我跑過」和「我覺得」。這也是本課（以及這個 repo 的 `learn` skill）的寫作紀律：程式輸出是真跑的，agent 對話是示意的，兩者要標清楚。

禁用破折號和冒號，是因為作者認為這是 AI 文字最明顯的特徵之一（unslop 技能第 13、14 條）。這條規則對英文有意義；本課是中文，沿用這個 repo 的寫作風格，Ch 23 會談中文情境下哪些 unslop 規則適用、哪些不適用。

## 八、Comments：註解只留「程式碼表達不了的 why」

> Keep a comment only for a non-obvious *why* the code can't show. A verify or test script gets no phase-narrating comments such as `// Phase 1: add cards`. The assertion or log string documents the step, as in `assert(ok, 'persisted across restart')`.

具體的例子比抽象規則有用：測試腳本不要寫 `// Phase 1`，讓 assertion 的訊息本身說明這一步在驗什麼。

## 九、Playbooks：路由規則

這段有三部分。

**抄寫規則：**

> Open a todolist whose first items are the matched playbook's steps, copied in verbatim, before any task-specific todos. A step you choose not to do stays in the list with a one-line `skip: <reason>`.

兩個關鍵字：**verbatim**（原封不動，不准改寫成自己的版本）和 **`skip: <reason>`**（跳過要留紀錄）。Ch 5 會詳談為什麼要原封不動抄。

**例外路由：**

- 大型、跨切面、或使用者要離開之後回來審的工作 → `figure-it-out`（即使 Feature 也適用）
- 沒有任何 playbook 適合 → `figure-it-out`
- 持續多天、很多疊起來的 PR、一個協調者帶一群 subagent → `Orchestrate`

**23 份 playbook 的一行索引**，例如：

```markdown
- **Bug fix.** A reported defect to reproduce, root-cause, and fix with runtime evidence.
- **Perf issue.** A measured slowness to trace and improve against a baseline.
- **Hillclimb.** Sustained, scientific improvement of one metric against a target ...
  Distinct from Perf issue, which is a one-off fix.
```

注意很多條會寫「Distinct from X」。路由最難的不是「這是什麼」，而是「這和那個很像的有什麼不同」。明確寫出區別，模型才不會在兩個相近的 playbook 之間亂選。

## 對比與取捨：三種寫 agent 指示的方式

| 方式 | 例子 | 優點 | 缺點 |
|---|---|---|---|
| 角色散文 | 「你是一位資深工程師，請仔細、嚴謹地完成任務」 | 好寫 | 不可檢查，不知道哪句管什麼 |
| 條件 → 動作規則表 | poteto-mode 的 Non-negotiables | 可檢查、可逐條修改 | 規則多了會互相衝突，需要維護 |
| 程式化流程（hook、腳本） | check-plan.mjs、Claude Code hooks | 一定會執行，不靠模型自覺 | 只能管可以被機器判斷的事 |

pstack 三種都用，但比例很清楚：主體是規則表；能變成程式的就變成程式（Encode Lessons in Structure 原則）；散文幾乎沒有。

## 踩雷集錦

1. **「這麼長，模型讀得完嗎？」**：3,343 個英文字約 5.4k tokens（Ch 0 的實測數字），對現代模型不是負擔。真正的問題是**每一句都會被當成指示執行**，所以每一句都要值得存在。pstack 的 commit log 裡有好幾次「concision pass」，就是在刪掉不改變決策的句子。
2. **以為 Principles 段就是原則本身**：它只是索引。完整規則在 leaf 檔，而且 poteto-mode 明確規定沒讀 leaf 不准引用。只讀這份檔案就說「我懂 pstack 的原則了」，是本章最常見的誤解。
3. **把 Autonomy 讀成「agent 什麼都可以做」**：界線是「可逆」。部署、刪資料、force-push 共用分支、對客戶發訊息，一律停。在 Claude Code 裡，這也和你的 permission 設定互相疊加：pstack 說可以做，Claude Code 的權限模式不允許，還是會被擋。
4. **照抄「不准用破折號」到中文**：中文的破折號（——）是正常標點，不是 AI 特徵。照搬規則會讓中文讀起來很怪。規則要理解它擋的是什麼，再決定怎麼翻譯到你的語境。
5. **以為主 session 會自己寫程式**：在大部分 code playbook 裡，主 session 規劃、審查、驗證，**寫程式交給 subagent**（Bug fix：「Delegate investigation and the fix to subagents, stay in the lead.」）。如果你看到主 session 自己大段改程式，它可能偏離了流程。

## 進階：再往深一層

- **比對 Cursor 版與 Claude Code 版**：`diff $上游/pstack/skills/poteto-mode/SKILL.md $上游/harness/pstack/claude-code/skills/poteto-mode/SKILL.md` 只差一行（「In Claude Code, read the Claude Code adapter once per session...」）。整個 Claude Code 支援只靠這一行加一份對照文件，這種「最小轉譯層」的設計 Ch 4 會拆。
- **讀 poteto-agent 的定義**：`$PSTACK/agents/poteto-agent.md` 只有幾行，但它規定「找不到 poteto-mode 檔案就停下來告訴 parent」——這是 subagent 自我檢查前提條件的範例。
- **把 sections.mjs 用在你自己的指示檔上**：對你的 `CLAUDE.md` 跑一次。如果某一段佔了 60% 的篇幅，問自己：這一段真的值得佔那麼多 context 嗎？

## 動手練習

1. 不看本章，在紙上畫出 poteto-mode 的八段結構，每段寫一句「它控制什麼」。再打開原文對照。
2. 從 Non-negotiables 挑三條本章沒拆的觸發器，各寫一句「這條在擋什麼失敗」。如果你寫不出來，那條就是你下一章要特別注意的。
3. 拿你自己的 `CLAUDE.md`（或任何你寫過的 agent 指示），找出三句「不可檢查」的句子，把它們改寫成「條件 → 動作」的形式。

## 本章重點整理

- poteto-mode 是一份「條件 → 動作」規則表，四大塊（觸發、原則索引、委派、playbook 索引）各佔約兩成篇幅
- 引用原則要點名「它改變了哪個決定」，而且只能引用本 session 讀過全文的原則
- 權限邊界畫在「可逆 vs 不可逆」；並且明確授權 agent 說不
- 委派預設開新 subagent、傳路徑不貼內容、主 session 為所有產出負責
- 回覆的每個斷言要帶「實測 / 推論 / 猜測」標籤

## 自我檢核

- [ ] 不看原文，能說出 poteto-mode 的八個區塊，以及哪四塊佔了大部分篇幅嗎？
- [ ] 能解釋「只能引用讀過 leaf 的原則」這條規則在擋什麼，並舉一個違反時的具體例子嗎？
- [ ] 有人提議「讓 poteto-mode 可以自動觸發，比較方便」，你能說出至少兩個反對理由嗎？
- [ ] 「agent 想問人要用 A 還是 B」時，poteto-mode 要它先做什麼判斷？在你最近的工作裡舉一個「其實是事實問題、卻被丟給人」的例子

## 延伸閱讀

### 原始碼

- **[poteto-mode/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md)**
  - **讀哪裡**：整份。建議用本章的 sections.mjs 先看結構，再逐段讀
  - **能學到什麼**：本章引用的每一句原文
  - **前提**：讀完本章
- **[agents/poteto-agent.md](https://github.com/cursor/plugins/blob/main/pstack/agents/poteto-agent.md)**
  - **讀哪裡**：整份（很短）
  - **能學到什麼**：一個 subagent 怎麼在開工前「繼承」主 session 的紀律
  - **前提**：知道 Claude Code subagent 定義檔的格式

### 官方文件

- **[Claude Code 官方文件：Skills](https://code.claude.com/docs/en/skills)**
  - **讀哪裡**：frontmatter 欄位一節，特別是 `disable-model-invocation`
  - **能學到什麼**：這個欄位在 Claude Code 的確切語意；本章對它的描述以官方文件為準
  - **注意**：Cursor 的 `icon`、`color` 不在 Claude Code 的欄位清單裡

### 對照閱讀

- **[harness_engineering Ch 11 — System prompt 設計](../harness_engineering/11-system-prompt-design.md)**（本 repo）
  - **讀哪裡**：「結構化 vs 散文」相關段落
  - **和本章的關聯**：poteto-mode 本質上是一份「按需載入的 system prompt」，那一章的設計原則在這裡全部看得到實例
- **[harness_engineering Ch 19 — Tool 描述就是 prompt](../harness_engineering/19-tool-descriptions-as-prompt.md)**（本 repo）
  - **和本章的關聯**：playbook 索引裡的「Distinct from X」寫法，和工具描述裡「什麼時候不要用我」是同一種技巧

結構看完了，但它在一次真實的執行裡是怎麼「動起來」的？下一章從你打下 prompt 那一刻開始追。

→ [Ch 3 一次 poteto-mode 執行的生命週期](./03-run-lifecycle.md)
