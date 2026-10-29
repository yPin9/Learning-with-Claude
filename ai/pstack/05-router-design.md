# Ch 5 — 路由器設計：一個 mode 加 23 份 playbook

> **目標**：理解 poteto-mode 為什麼選擇「一個路由技能 + 23 份獨立 playbook 檔」這種結構，而不是一份大 prompt 或 23 個獨立技能；搞懂「原封不動抄進 todo」與「`skip: <reason>`」這兩條規則在擋什麼；並親手寫一支把 playbook 轉成 todo 的小工具，從它的失敗裡看出為什麼必須逐字抄。

> **環境**：pstack 0.15.36、Node.js 26。playbook 的字數與步驟數是對上游檔案實際量出來的。

## 為什麼需要這個？

假設你要讓 agent 處理五種不同的任務：修 bug、加功能、重構、查效能、回答問題。每一種都有自己的最佳流程。你有幾種選擇：

1. **全部寫進一份大 prompt**：每次都載入五種流程。大部分內容和當下任務無關，浪費 context，而且模型容易把 A 流程的步驟混進 B。
2. **每種流程寫成一個獨立技能**，讓模型自己挑：每個技能的描述都常駐 context；模型可能挑錯，或同時打開兩個。
3. **一個路由器 + 多份流程檔**：路由器常駐（或被叫用時載入），裡面只有每份流程的一行索引；路由器判斷任務類型後，才去讀那一份流程的全文。

pstack 選第三種。這一章拆解這個選擇的每一個細節。

## 先建立直覺：一間醫院的掛號櫃台

```
                          病人：「我頭痛三天了」
                                   │
                          ┌────────▼────────┐
                          │    掛號櫃台      │   ← poteto-mode
                          │ 看症狀，不看病    │      只有「每科看什麼」的一行說明
                          └────────┬────────┘
             ┌─────────────┬───────┼───────┬──────────────┐
             ▼             ▼       ▼       ▼              ▼
          神經科         急診     家醫科   ...      「都不像」→ 特別門診
          （有自己       （有自己  （...）            （figure-it-out：
           的診療流程）   的流程）                    現場設計一套流程）
```

櫃台不需要懂神經科怎麼看診，它的工作是**分得出**什麼該去神經科。每一科的診療流程放在各科自己那裡，病人到了才拿出來。而且每一科的流程都會照順序走完，不會因為醫生今天心情好就跳過量血壓——如果真的跳過，病歷上要寫為什麼。

## 一、結構：索引在 SKILL.md，全文在 playbooks/

```
poteto-mode/
├── SKILL.md                  ← 23 份 playbook 的一行索引（約 715 字）
└── playbooks/
    ├── investigation.md      ← 145 字
    ├── bug-fix.md            ← 368 字
    ├── feature.md            ← 399 字
    ├── ...
    └── multi-phase-plan.md   ← 2,242 字
```

量一下 23 份 playbook（字數用 `wc -w`，步驟數是行首 `數字. ` 的數量）：

```
authoring-a-skill           127 words   4 numbered steps
investigation               145 words   4 numbered steps
autopilot-stack             158 words   2 numbered steps
visual-parity               182 words   5 numbered steps
pause-safely                202 words   4 numbered steps
runtime-forensics           215 words   5 numbered steps
perf-issue                  239 words   6 numbered steps
session-pickup              274 words   5 numbered steps
trace-forensics             314 words   6 numbered steps
autonomous-run              318 words   6 numbered steps
prototype                   334 words   6 numbered steps
bug-fix                     368 words   6 numbered steps
feature                     399 words   8 numbered steps
eval                        407 words   7 numbered steps
worktree-cleanup            443 words   6 numbered steps
refactoring                 477 words   8 numbered steps
hillclimb                   711 words   8 numbered steps
shipping                    771 words   6 numbered steps
orchestrate                 797 words   0 numbered steps
opening-a-pr                929 words   0 numbered steps
autopilot-full             1182 words   7 numbered steps
babysit                    1225 words   9 numbered steps
multi-phase-plan           2242 words   7 numbered steps
```

全部加起來約 12,000 字。如果全塞進 poteto-mode 本體，它會從 3,343 字膨脹到 15,000 字以上，而一次 Bug fix 用到的只有其中 368 字。分檔讓每次執行只付「索引 + 一份 playbook（+ 結尾的 Opening a PR）」的成本。

注意分佈：**常用的 playbook 都很短**（Investigation 145 字、Bug fix 368 字），**長的都是少用的大規模流程**（Multi-phase plan、Babysit、Autopilot-full）。這不是巧合。短流程才能被逐字抄進 todo、被逐步執行；長流程本身就是給「整個專案」用的。

另外注意 `orchestrate` 與 `opening-a-pr` 沒有編號步驟——它們是一組規則（Topology、Supervision；Worktree、Commits、PRs……），而不是順序流程。不是每種工作都適合寫成 1、2、3。

## 二、路由規則：不只是「這是什麼」，還有「這和那個有什麼不同」

poteto-mode 的 Playbooks 段落有兩種路由規則：

**優先規則（寫在索引之前）：**

> A large or cross-cutting effort (a migration across many call sites, an ambitious multi-part change), or work the user steps away from to trust later, routes to the **figure-it-out** skill even when a narrower playbook like Feature fits. Use **figure-it-out** whenever no bundled playbook fits.
>
> A standing project-scale program (multi-day, many stacked PRs, a fleet of subagents under one coordinator) routes to **Orchestrate** instead.

```
  任務進來
     │
     ├─ 是常駐的專案級計畫（多天、很多疊起來的 PR）？ ──是──▶ Orchestrate
     │
     ├─ 是大型跨切面工作，或使用者要離開之後再回來審？ ──是──▶ figure-it-out
     │                                              （即使 Feature 也適用）
     ├─ 對得上某一份 playbook？ ──是──▶ 那一份
     │
     └─ 都對不上 ──▶ figure-it-out（現場設計一份）
```

第二條規則很關鍵：**「使用者要離開之後再回來審」會改變路由**。同樣是加一個功能，你坐在旁邊看，走 Feature；你說「我要去睡了，早上回來看」，走 figure-it-out——因為後者會強制留下決策日誌（Ch 26），讓你早上能審。路由不只依任務類型，也依**監督方式**。

**區別條款（寫在索引裡）：**

```markdown
- **Hillclimb.** Sustained, scientific improvement of one metric against a target ...
  Distinct from Perf issue, which is a one-off fix.
- **Runtime forensics.** Diagnose a runtime symptom ... The deliverable is a diagnosis, not a fix.
- **Trace forensics.** Diagnose a captured profiling artifact ... handed to you after the fact.
- **Orchestrate.** ... Distinct from Autonomous run, which drives one task to a predicate.
  Work one agent could finish inside the session's budget routes there, not here,
  however program-shaped the phrasing sounds.
```

最後一句特別有意思：「however program-shaped the phrasing sounds」。使用者說「幫我把這整個遷移專案跑完」，聽起來很像 Orchestrate；但如果一個 agent 在一個 session 裡就做得完，就不是。**路由看的是工作的實際規模，不是使用者的措辭。**

## 三、為什麼要原封不動抄進 todo

規則原文：

> Open a todolist whose first items are the matched playbook's steps, copied in verbatim, before any task-specific todos.

為什麼不讓模型「依照 playbook 的精神，寫一份適合這個任務的 todo」？我們用一個實驗回答。寫一支把 playbook 轉成 todo 的小工具，為了讓 todo 短一點，只取每步的第一句：

```js
// playbook-todo.mjs：把 playbook 的編號步驟轉成 todo；可以標記要跳過的步驟
import fs from "node:fs";

const [file, ...skips] = process.argv.slice(2);
const skipMap = new Map(skips.map((s) => s.split("=")).map(([n, why]) => [Number(n), why]));
const steps = fs
  .readFileSync(file, "utf8")
  .split("\n")
  .map((line) => line.match(/^(\d+)\. (.*)$/))
  .filter(Boolean);
if (steps.length === 0) {
  console.error(`${file}: no numbered steps; copy its sections by hand`);
  process.exit(1);
}
for (const [, n, text] of steps) {
  const first = process.env.FULL ? text : text.split(/(?<=\.)\s/)[0];   // 預設只取第一句
  const why = skipMap.get(Number(n));
  console.log(why ? `- [x] ${n}. ${first} skip: ${why}` : `- [ ] ${n}. ${first}`);
}
```

對 Bug fix playbook 跑，並且假設我們判斷「這個 bug 沒有便宜的本地測試路徑」，所以跳過第 5 步：

```bash
node playbook-todo.mjs $PSTACK/skills/poteto-mode/playbooks/bug-fix.md \
  "5=no cheap local test path, repro script is the check"
```

```
- [ ] 1. Reproduce it yourself on the matching surface via the control skill (Non-negotiables), even when a debug or instrumentation protocol says to ask the user to reproduce.
- [ ] 2. Binary-search the cause.
- [ ] 3. Plan the fix.
- [ ] 4. Verify on the same surface.
- [x] 5. Stage the commits so the failing repro lands before the fix in git history. skip: no cheap local test path, repro script is the check
- [ ] 6. Run **Opening a PR**.
```

看起來很整齊。**但第 5 步的 skip 是錯的。** 看第 5 步的全文：

```
5. Stage the commits so the failing repro lands before the fix in git history. See the **tdd** skill
   for the failing-test-first cadence when the bug has a cheap local test path. Skip it when the test
   would be expensive, integration-heavy, or unclear.
   This is the canonical **sequence-verifiable-units** principle skill, the failing test first and the fix on top.
```

「Skip it」的 **it** 指的是「用 tdd 技能寫失敗測試」，**不是**「讓失敗的重現先於修正進入 git 歷史」。沒有便宜的測試路徑時，你仍然要把**重現腳本**先 commit、修正後 commit。縮寫版的 todo 把條件子句丟掉了，於是一個看似合理的 skip 理由，實際上跳過了不該跳的東西。

這就是「verbatim」的理由：

1. **條件子句是規則的一部分**。摘要會丟掉「when」「unless」「only if」，而這些正是區分「可以跳過」和「不能跳過」的地方。
2. **摘要是模型的詮釋**。讓模型自己改寫 playbook，等於讓它在執行前先把規則改成它比較想遵守的版本。
3. **逐字才能稽核**。審查者拿 todo 和 playbook 原文逐行對照，一眼看出少了什麼。改寫過的 todo 無法對照。

順帶一提，`FULL=1` 模式也不完美：

```bash
FULL=1 node playbook-todo.mjs $PSTACK/skills/poteto-mode/playbooks/bug-fix.md | sed -n 5p
```

```
- [ ] 5. Stage the commits so the failing repro lands before the fix in git history. See the **tdd** skill for the failing-test-first cadence when the bug has a cheap local test path. Skip it when the test would be expensive, integration-heavy, or unclear.
```

它還是漏掉了下一行縮排的「This is the canonical sequence-verifiable-units principle skill...」，因為我們是逐行比對編號。對 `orchestrate.md` 和 `opening-a-pr.md` 它更直接失敗：

```
.../opening-a-pr.md: no numbered steps; copy its sections by hand
exit=1
```

這兩個失敗教了同一件事：**「抄進 todo」這件事本身就很難自動化得正確**。pstack 把它交給模型做、並要求逐字，是因為模型能處理縮排續行和非編號結構；但也正因為是模型在做，才需要「逐字」這個可稽核的約束。

## 四、`skip: <reason>`：允許偏離，但要留紀錄

> A step you choose not to do stays in the list with a one-line `skip: <reason>`.

這條規則解決一個兩難：

- 如果 playbook 每一步都強制執行，小任務會被大流程拖垮
- 如果允許模型自由決定做哪些步驟，它會傾向跳過麻煩的那些（重現、驗證）

`skip: <reason>` 的折衷是：**可以跳，但跳過的步驟留在清單上，理由寫在旁邊**。審查者讀 todo 時，目光會自然停在 skip 上。上面那個錯誤的 skip，正是因為寫出來了才被我們抓到。如果它是直接從清單上消失，就沒有人會注意到第 5 步被跳過了。

有些步驟明確**不准**跳。Feature playbook 第 4 步（多種合理形狀時用 arena 委派）寫著：「Mandatory: no skip-with-reason escape, and Laziness Protocol does not override it (the gain is review separation, not lines saved).」這是作者在說：這一步的價值不在省事，在於讓寫的人和選的人分開，所以「我覺得不需要」不是有效理由。

## 五、組合：Opening a PR 是共用的結尾

> **Opening a PR.** Invoked at the end of every other playbook.

```
  Bug fix ─────┐
  Feature ─────┤
  Refactoring ─┼──▶ Opening a PR ──（使用者要求時）──▶ Babysit ──▶ Shipping
  Perf issue ──┤
  Hillclimb ───┘
```

PR 的規則（worktree、小而有序的 commit、Conventional Commits 標題、內文分段、`/deslop` 與 `/no-comments`）只寫一次，所有建造類 playbook 都在最後一步引用它。這是 Laziness Protocol 的「Consolidate decisions」用在流程設計上：同一個決定只放在一個地方。

也注意 Opening a PR **不會**自動接 Babysit：「Opening a PR does not start a babysit. Post the URL and keep building.」開 PR 和盯 PR 是兩個不同的決定，後者要使用者明確要求。

## 底層機制：路由是一次分類，誰在做？

路由不是程式碼。沒有任何 classifier 在判斷「這是 Bug fix」。是**模型**讀了 poteto-mode 的索引、讀了你的 prompt，在它的推理裡做了一次分類。

這有三個後果：

1. **你的措辭會影響路由**。Ch 6 會講：「repro first」「don't change any code yet」這些片語是強烈的路由訊號。
2. **路由可能錯**，所以第一則訊息要重述目標——重述裡通常會透露它選了哪一份 playbook，你可以在那時糾正。
3. **路由可以被你覆寫**。直接說「use the Refactoring playbook」它就會用。但官方指南警告不要列舉技能（「don't enumerate skills in your prompt」），因為 playbook 已經安排好順序，你手寫的順序通常會漏步驟或搞錯順序。覆寫 playbook 可以，覆寫 playbook 裡的技能順序不要。

## 對比與取捨

| 設計 | context 成本 | 選錯的風險 | 可稽核性 | 維護 |
|---|---|---|---|---|
| 一份大 prompt 含所有流程 | 每次付全部 | 低（都在），但會混 | 低 | 改一處可能影響全部 |
| 每個流程一個獨立技能（模型自選） | 每個描述都常駐 | 中，可能同時開兩個 | 中 | 各自獨立 |
| **路由器 + 流程檔（pstack）** | 索引 + 一份 | 中，靠重述來抓 | 高（逐字 + skip） | 流程各自一檔，索引集中 |
| 程式化路由（hook 或 classifier） | 低 | 取決於分類器 | 高 | 分類規則要另外維護 |

為什麼 pstack 不把 23 份 playbook 做成 23 個技能？三個理由：它們不該被單獨叫用（playbook 依賴 poteto-mode 的 Non-negotiables 與 Subagents 規則才完整）；會多 23 條常駐描述；而且「哪一份 playbook」的判斷需要 poteto-mode 的優先規則（例如「要離開就走 figure-it-out」），這些規則放在路由器裡才說得清楚。

## 踩雷集錦

1. **讓 agent「依精神」寫 todo**：本章實驗示範了摘要怎麼丟掉條件子句、造成錯誤的 skip。要求逐字，就算看起來很囉唆。
2. **todo 上沒有 skip 就安心**：一個 4 行的改動跑完 8 步 Feature playbook 卻沒有任何 skip，更可能是每一步都被形式化地「打勾」了。去看每一步留下了什麼產物。
3. **以為路由是看關鍵字**：模型做的是語意分類。「這個 bug 讓我覺得要重構整個模組」可能被路由到 Bug fix 也可能到 Refactoring。不確定時，在 prompt 裡說清楚你要的是哪一種結果（修好症狀？還是重整結構？）。
4. **在 prompt 裡列技能順序**：「先 /how 再 /architect 再 /arena」會覆蓋 playbook 的順序，而你手寫的版本通常比 playbook 少步驟。只說目標與限制。
5. **把 figure-it-out 當成「找不到就隨便做」**：它是有嚴格流程的（Frame → 設計 workflow → 跑迴圈 → 決策日誌 → 驗證），而且強制留下可稽核的軌跡。它是「沒有現成流程時，先設計流程」，不是「沒有流程」。

## 進階：再往深一層

- **自己的路由器要不要用 hook 輔助？** 社群移植版用 SessionStart hook 注入「什麼時候該進入 poteto-mode」的三個條件（Ch 4）。你可以更進一步：用 UserPromptSubmit hook 偵測「bug」「slow」「refactor」這類字，在 context 裡加一行「考慮使用 X playbook」的**建議**（而不是自動觸發）。這是程式化提示 + 模型決策的混合。
- **路由錯誤的成本不對稱**：把 Feature 路由成 Investigation，最多是它不寫程式、回你一份分析；把 Investigation 路由成 Feature，它會開始改你的程式碼。所以 Investigation 的觸發片語（「don't change any code yet」）要特別明確，這也是 Ch 6 的重點之一。
- **讀 multi-phase-plan.md**：2,242 字，是唯一一份附「計畫骨架模板」和「檢查腳本」的 playbook。它展示了一份 playbook 可以多重：不只是步驟，還有產物格式與自動檢查（Ch 27）。

## 動手練習

1. 照本章寫出 `playbook-todo.mjs`，對 `refactoring.md` 跑一次縮寫版和 `FULL=1` 版。找出縮寫版丟掉的、會影響 skip 判斷的條件子句至少一個。
2. 改進這支工具，讓它能處理縮排的續行（提示：一行不是以 `數字. ` 開頭、但以空白開頭時，接到上一步）。改完再對 bug-fix.md 跑，確認第 5 步完整了。
3. 寫三個 prompt，分別會被路由到 Investigation、Bug fix、figure-it-out，但描述的是**同一個問題**（例如「匯出的 CSV 少了最後一列」）。差別應該只在你要的結果和你的監督方式。

## 本章重點整理

- 一個路由器 + 23 份流程檔：索引常駐於 poteto-mode，全文按需讀取；常用流程短、大規模流程長
- 路由有優先規則（要離開 → figure-it-out；常駐專案 → Orchestrate）和區別條款（Distinct from X），而且看實際規模不看措辭
- playbook 要逐字抄進 todo，因為摘要會丟掉條件子句；跳過的步驟要留 `skip: <reason>`
- Opening a PR 是所有建造類 playbook 的共用結尾，但不會自動接 Babysit

## 自我檢核

- [ ] 不看本章，能說出 poteto-mode 的兩條優先路由規則，以及為什麼「使用者要離開」會改變路由嗎？
- [ ] 能用本章的第 5 步例子，向同事解釋為什麼 todo 必須逐字抄嗎？
- [ ] 為什麼 pstack 不把 23 份 playbook 做成 23 個獨立技能？至少說出兩個理由
- [ ] 想一個你工作中的重複性任務：如果要為它寫一份 playbook，它會有幾步？哪一步你預期 agent 最常跳過？

## 延伸閱讀

### 原始碼

- **[poteto-mode/SKILL.md 的 Playbooks 段](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md)**
  - **讀哪裡**：最後一個 `##` 段，從「Open a todolist」到結尾
  - **能學到什麼**：本章所有路由規則的原文
- **[playbooks/ 目錄](https://github.com/cursor/plugins/tree/main/pstack/skills/poteto-mode/playbooks)**
  - **讀哪裡**：先讀最短的 investigation.md 與 bug-fix.md，再讀最長的 multi-phase-plan.md
  - **能學到什麼**：同一個作者寫「短流程」與「大規模流程」時，結構差在哪裡

### 官方指南

- **[docs/guide/02-poteto-mode.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/02-poteto-mode.md)**
  - **讀哪裡**：「What happens to your prompt」的流程圖，與最後的 Pitfall
  - **能學到什麼**：官方對路由的視覺化，以及「不要列舉技能」的原文

### 文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** 的「Routing」一節
  - **這篇說什麼**：把輸入分類、導到專門的後續流程；「適合有明確類別、且分類可以準確完成的複雜任務」
  - **和本章的關聯**：poteto-mode 是這個 pattern 的教科書級實作；這篇也提醒了路由的前提是「分類可以準確」，這正是 pstack 用「重述目標」補強的地方

### 對照閱讀

- **[harness_engineering Ch 29 — Skills](../harness_engineering/29-skills.md)**（本 repo）
  - **讀哪裡**：Progressive disclosure 的三層
  - **和本章的關聯**：poteto-mode 的「索引 → playbook 全文」是在技能內部再做一層漸進揭露

路由決定了走哪條路，但路由的輸入是你的 prompt。下一章講怎麼寫一個讓路由選對、讓驗證有依據的 prompt。

→ [Ch 6 怎麼下 prompt：目標、完成條件、證據](./06-prompting-poteto-mode.md)
