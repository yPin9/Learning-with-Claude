# Ch 1 — pstack 是什麼：go deep first

> **目標**：說得出 pstack 是誰、為了解決什麼問題寫的；能畫出它 58 個技能的分類地圖；理解它最核心的一句主張「if you want to go fast, go deep first」在工程上具體指什麼；並能把它和「直接用 Claude Code」「規格驅動開發」這些替代方案放在同一張圖上比較。

> **環境**：純概念章，不需要動手。引用的原文以 pstack 0.15.36（上游 commit `c742dd6`）為準。

## 為什麼需要這個？

2025 到 2026 年，寫程式的 agent 從「幫你補一段」變成「幫你開 PR」。產量上來了，一個抱怨也跟著出現：**AI 寫了太多 slop**——能編譯、測試會過、看起來合理，但多了不必要的抽象、加了沒人要的防禦性檢查、修了症狀沒修病因、宣稱做完卻沒驗證過。

面對這個問題，業界大致有三種回應：

1. **多加人工 review**：agent 寫，人審。問題是 agent 產量上去後，審查變成瓶頸；而且審查者會疲勞，slop 照樣溜進去。
2. **先寫規格再讓 agent 實作**（spec-driven development）：把需求寫清楚，agent 照規格做。問題是規格本身也可能錯，而且很多決策要寫了程式才知道。
3. **把資深工程師的判斷寫成 agent 會照做的流程**：讓 agent 自己就先重現 bug、自己就先命名資料結構、自己就去驗證真實產物——在產出到達人類之前就把 slop 擋掉。

pstack 是第三種路線目前最完整的公開範例之一。

## 先建立直覺：一個「把資深工程師裝進 agent」的嘗試

想像你帶一個很聰明、很快、但沒有經驗的新人。他的典型問題不是「不會寫」，而是：

- 你說「這裡有 bug」，他**沒重現就開始改**，改在第一個看起來可疑的地方
- 你說「加個功能」，他**直接開寫**，寫到一半才發現資料結構選錯
- 他說「做完了」，你問「你怎麼知道？」，他說「**編譯過了**」

你會怎麼帶他？你不會每次都重講一遍，你會給他一套**檢查表**：修 bug 先重現、加功能先畫資料形狀、說做完之前要拿出證據。你也會給他一套**共同詞彙**：「這叫 root cause，不是 symptom」「這叫 premature abstraction」——之後你只要說一個詞，他就知道你在講什麼。

pstack 就是這兩樣東西：**23 份流程手冊（playbook）是檢查表，24 條原則是共同詞彙**。外加一堆專門的工具技能（多模型評審、平行驗證、決策日誌……），讓檢查表裡的步驟真的能被執行。

```
            你：/pstack:poteto-mode <目標 + 怎麼算完成>
                              │
                              ▼
   ┌────────────────── poteto-mode（分派器）──────────────────┐
   │  1. 讀原則索引（24 條，各自是一個 leaf 技能）               │
   │  2. 比對任務 → 挑 playbook（23 份）                        │
   │  3. 把 playbook 步驟原封不動抄進 todo                       │
   │  4. 逐步執行；步驟需要什麼就叫什麼技能                      │
   │       how / why / architect / arena / swarm / interrogate │
   │       tdd / benchmark-checklist / unslop / no-comments ... │
   │  5. 寫程式交給 subagent（poteto-agent），自己審 diff         │
   │  6. 拿證據驗證真實產物，才回報                              │
   └────────────────────────────────────────────────────────────┘
```

## 一、誰寫的、為什麼寫

pstack 的 README 開頭是作者自己的話（全小寫是原文風格）：

> i'm poteto. i'm not a president or ceo, but i've worked with millions of lines of code at Meta, Netflix, and Cursor. i'm also on the react core team where i help build and maintain react compiler.
>
> there's a growing sense that ai writes too much slop code. i agree. i don't want to ship like a team of twenty slop artists. throughput without quality is not a goal i aspire to. if you want to go fast, go deep first.

poteto 是 Lauren Tan 的網路代號。幾個值得注意的點：

- **定位是個人工作風格，不是框架**。`poteto-mode` 的 description 寫的是「poteto's agent style」，README 最後一節叫「make it yours」，明說「`poteto-mode` is my style. you may not want exactly that.」這決定了整套東西的讀法：它是一份**示範**，示範一個人怎麼把自己的工程判斷編碼進 agent，而不是一個要你照抄的標準。
- **它來自真實的大量使用**。Cursor 官方有一個以「pstack: the skills behind 10,000 agent runs」為題的頁面；第三方報導指出這是作者在 2026 年 5 月開源時，Cursor 內部一週內使用這套技能的次數，而它的前身是作者內部的 `/lauren-mode`。這兩點本課**沒有找到作者第一手的原文可以核對**，請當成二手資料看待。
- **它的目標不是寫更多，是寫更少**。README 的原話：「the goal is not to maximize loc, in fact it's the opposite. pstack helps you write less, but higher quality code.」

## 二、「go deep first」到底是什麼意思

這句話很容易被讀成雞湯。拆開來看，它其實是一個很具體的工程主張：

> **pstack gives you fearless parallelism.** when you can go deep on one agent and trust it to write good, verifiable code, you can truly parallelize with confidence.

邏輯是這樣的：

```
   錯誤的順序（先 broad）                    pstack 的順序（先 deep）
   ─────────────────────                    ─────────────────────────
   開 10 個 agent 平行做                     先讓 1 個 agent 能可靠地
          │                                 把一件事做對、而且自己證明做對
          ▼                                           │
   10 份沒驗證過的產出                                 ▼
          │                                 再開 10 個，每個都帶著同一套
          ▼                                 檢查表與驗證紀律
   人類變成 10 倍的審查瓶頸                             │
          │                                           ▼
          ▼                                 10 份「附證據」的產出
   slop 溜進去                               人類審的是證據，不是重做一遍
```

這就是為什麼 pstack 花大量篇幅在**驗證**：Prove It Works、benchmark-checklist、verification skill、swarm 驗證、interrogate 只保留跑得出來的發現。平行化的前提是你能信任單一 agent 的產出；而信任的前提是產出附帶可以重跑的證據。官方指南第 7 頁把這個順序講得更白：「A loop you don't trust just produces unchecked work faster.」

## 三、組成地圖：58 個技能分成五類

```
pstack/
├── 分派器（1）
│   └── poteto-mode ............ 總入口，讀任務、挑 playbook、叫其他技能
│       ├── playbooks/ ......... 23 份流程手冊（bug-fix.md、feature.md…）
│       ├── references/ ........ bugbot-triage.md
│       └── scripts/ ........... check-plan.mjs、watch-pr/、worktree-audit.sh
│
├── 原則（24）principle-*
│   ├── Core（10）......... laziness-protocol、foundational-thinking、build-the-lever…
│   ├── Architecture（6）.. model-the-domain、boundary-discipline、type-system-discipline…
│   ├── Verification（5）.. prove-it-works、fix-root-causes、explain-the-number…
│   ├── Delegation（2）.... guard-the-context-window、never-block-on-the-human
│   └── Meta（1）.......... encode-lessons-in-structure
│
├── 工作流技能（約 20）
│   ├── 理解：how、why、teach、recall、blast-radius
│   ├── 設計：architect、arena、swarm、interrogate、figure-it-out
│   ├── 建造與清理：tdd、unslop、no-comments、technical-writing、typescript-best-practices
│   ├── 驗證：benchmark-checklist、create/maintain-verification-skill、
│   │        create/maintain-formal-verification-skill、statechart
│   └── 長時間執行：show-me-your-work、ketchup、ketchup-video
│
├── 元技能（約 8）
│   ├── 對齊意圖：in-your-own-words、sharpen、bro
│   ├── 自我改進：reflect、correct、automate-me
│   └── 設定與說明：setup-pstack、poteto-help
│
├── 情境技能：make-bot-ui（Grok Bot 用）
│
└── agents/（2）
    ├── poteto-agent ........... 所有 delegate 的包裝，開工前先讀完 poteto-mode
    └── comment-sicko .......... 專門刪註解的唯讀審查者
```

Ch 0 用 `claude plugin details` 拿到的就是這份清單（58 = 1 + 24 + 33 個其他技能）。分類是本課為了教學整理的，pstack 原文沒有這樣分；原文只把原則分成 Core / Architecture / Verification / Delegation / Meta 五組。

## 四、它不做什麼

讀一套系統，知道它刻意**不做**什麼和知道它做什麼一樣重要。

**沒有規劃技能。** README 有一節直接叫「why are there no planning skills?」：

> cursor already has a great plan mode which works great with pstack. but personally, i don't believe in planning. the best spec is code.

這不是說 pstack 完全不做計畫——Multi-phase plan playbook 會產出很詳細的計畫文件。重點是**計畫不是預設步驟**，而且就算要計畫，也是「先用 prototype 把開放問題跑出答案，再寫計畫」。設計章的原話：「The two most common design mistakes are taking the agent's first design and polishing a plan that no code has tested.」

**不靠人類 review 擋 slop。** `/correct` 技能列出修正錯誤的層級：架構 → 型別 → lint → 測試 → 文件，最後一句是「Human review isn't on the list. A reviewer who must catch the same mistake on every PR is the problem this fixes.」

**不自動觸發。** 57 個技能是 `disable-model-invocation: true`（唯一例外是 `setup-pstack`），要你明確叫用。作者的判斷是：嚴謹是有成本的，應該由人決定什麼時候付。

## 五、貫穿全課的五個設計信條

讀 pstack 的 58 個技能時，你會一直看到同幾個想法用不同形式出現。先把它們列出來，之後每一章都可以回來對照：

### 信條 1：可檢查勝過可信賴

pstack 幾乎不要求 agent「更小心」，而是要求它**留下可檢查的痕跡**：

- todo 裡跳過的步驟要寫 `skip: <reason>`（Ch 3、Ch 5）
- 回覆要點名原則**以及它改變了哪個決定**（Ch 11）
- 每個斷言要標「實測 / 推論 / 猜測」（Ch 2）
- 長時間工作要寫 TSV 決策日誌，每列附證據指標（Ch 26）

這些痕跡本身不保證正確，但讓偏離**被看見**。審查一個 agent 的時間，從「重做一遍它的工作」降到「讀它的痕跡、抽查證據」。

### 信條 2：真實產物勝過代理指標

「編譯過了」「測試綠了」「agent 說做完了」都是代理指標。pstack 反覆要求檢查真實的東西：跑真實的指令、讀寫進去的值、在真實的 UI 走一遍流程。Prove It Works 原則的原文：「Verify against the real artifact, not a proxy or 'it compiles'.」Ch 3 那個漏掉結尾換行的 bug，就是只有真實位元組比對才抓得到的例子。

### 信條 3：寫的人不能自己判

- 註解由沒寫過那些註解的 Comment Sicko 刪（Ch 23）
- arena 的評審要來自不同模型家族（Ch 18）
- Shipping 只接受「沒寫過這段程式碼的 agent」給的驗證結論（Ch 24）
- 決策日誌要由不同家族的模型審一遍（Ch 26）

理由寫在 Shipping playbook 裡：「an author checks what it meant to build」——作者檢查的是他**想要**做出來的東西，不是他**實際**做出來的東西。

### 信條 4：能編碼成結構的，就不要寫成文字

Encode Lessons in Structure 原則：同一個指示寫第二次時，就把它變成 lint、檢查、腳本。pstack 自己就這樣做：計畫格式有 `check-plan.mjs` 檢查（Ch 27），跨 harness 對照表有產生器檢查（Ch 4），決策日誌有 `log.sh` 幫你清理格式（Ch 26）。`/correct` 技能把這個信條推到整個 repo：修正一個錯誤的優先順序是架構 → 型別 → lint → 測試，文件最後（Ch 30）。

### 信條 5：問人是最後手段

- 能靠跑東西回答的問題，自己做 prototype 回答（Ch 2、Ch 9）
- 可逆的工作直接做，做完再呈現（Never Block on the Human，Ch 15）
- 只有不可逆的動作和真正的產品偏好才停下來問

這個信條和信條 3 有一個看似矛盾的地方：既要「寫的人不能自己判」，又要「不要問人」。解法是**用另一個 agent 判，而不是用人判**。人的時間留給只有人能做的決定。

```
                 需要判斷的時候
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  能靠跑東西回答？   需要獨立觀點？    是產品偏好或不可逆？
  → prototype       → 不同家族的       → 問人
    自己觀察          agent 審
```

這五條會在之後的每一章出現。Ch 31 會回頭檢討：它們在什麼情況下會失效。

## 六、演進：一個快速移動的目標

用 GitHub API 數上游 `pstack/` 目錄的 commit（2026-10-28 取得）：

| 月份 | commit 數 | 這段時間的代表性變化（取自 commit 標題） |
|---|---|---|
| 2026-05 | 26 | 05-23 首次加入；figure-it-out、show-me-your-work、refactoring、build-the-lever 陸續加入 |
| 2026-06 | 14 | Hillclimb playbook、per-role 模型設定（setup-pstack）、pause-safely、「用 sketch 代替提問」 |
| 2026-07 | 27 | model-the-domain 原則、teach、create/maintain-verification-skill、swarm；07-14「parity sweep with the private skill tree」（公開版和作者私有版本對齊） |
| 2026-08 | 8 | autopilot playbooks、/no-comments 與 Comment Sicko、technical-writing、babysit / shipping / orchestrate |
| 2026-09 | 15 | 09-01 再把五個技能改成不讓模型自動觸發；09-08 把技能文字裡的分號、破折號、連接用冒號全部換掉；09-23 預設模型改成 Opus 5.5 與 Grok 4.7 |
| 2026-10 | 79 | **10-16 官方 Claude Code 支援上線**（PR #637「install pstack in Claude Code from a generated tree」），10-17 補三個 Claude Code 缺口 |

兩個觀察：

1. **官方 Claude Code 支援是最近才有的**（2026-10-16）。這解釋了為什麼社群裡已經有一堆移植版——它們在官方支援之前就存在了。這也意味著：你在網路上找到的「pstack 在 Claude Code 怎麼用」文章，很多寫於官方支援之前，描述的是移植版。
2. **規則一直在變**。每一次修改幾乎都對應一次 agent 做錯的經驗（例如 06-06 的「self-unblock empirical forks with a sketch instead of asking」，就是把「agent 遇到選擇就問人」這個壞習慣改掉）。讀 pstack 的最好方式之一，就是讀它的 git log：每一個 commit 都是一條「agent 會在這裡出錯」的紀錄。

## 七、讀這門課的三種方式

這門課有 32 章、4 個練習、1 個 final project。依你想從 pstack 拿到什麼，有三條不同的路線：

| 你是誰 | 你想要什麼 | 建議路線 |
|---|---|---|
| **使用者**：想在 Claude Code 裡用 pstack 做事 | 知道什麼時候叫哪個技能、怎麼寫 prompt、怎麼看懂它的輸出 | Ch 0 → Ch 3 → Ch 6 → Ch 8 → Ch 24 → Ch 25 → Ch 31 的決策表 |
| **設計者**：想借鑑它的設計，寫自己的 agent 工作流 | 每個設計決策背後的理由與替代方案 | Ch 2 → Ch 4 → Ch 5 → Ch 11 → Ch 16 → Ch 18 → Ch 28 → Final Project |
| **懷疑者**：想知道它到底值不值得 | 它的成本、缺口、失效條件，以及實驗證據 | Ch 0 的成本 → Ch 17 的多樣性塌縮 → Ch 20 → Ch 22 → Ch 31 |

不管走哪條路線，每章的實驗都建議實際跑一次。本課的一個核心主張是「看到它失敗才算看懂它」，而大部分章節的關鍵發現（Ch 3 的換行 bug、Ch 10 被算錯的 profile、Ch 13 三次三種結果的 race）只有在你自己跑出來的時候才會留下印象。

## 對比與取捨

| 方案 | 核心假設 | 擋 slop 的位置 | 代價 | 適合 |
|---|---|---|---|---|
| 直接用 Claude Code | 模型夠強，給好 prompt 就行 | 你的 prompt 與你的 review | 低 | 小改動、你很熟的程式碼 |
| 規格驅動開發（Spec Kit、Kiro 等） | 需求寫清楚，實作就會對 | 規格與驗收條件 | 寫規格的時間 | 需求明確、跨團隊協作 |
| pstack / poteto-mode | agent 會犯資深工程師一眼看得出的錯，要用流程擋 | 每個 playbook 步驟與驗證證據 | 大量 tokens、subagent、多模型評審 | 有風險的改動、你不想親自審每一行 |
| 社群技能庫（如 superpowers 類） | 類似 pstack，各有側重 | 依各自設計 | 依各自設計 | 想要不同風格的流程 |

這張表不是要你選一個。pstack 自己就說簡單的事不要用它：「Save `/poteto-mode` for work that needs rigor. A small, obvious edit doesn't.」

## 踩雷集錦

1. **「pstack 是一個框架，要照它的方式組織專案」**：不是。它是一組 Markdown 技能，不改你的專案結構，不需要任何 runtime。拿掉它，你的 repo 一個位元都不會變。
2. **「裝了 pstack，agent 就會變嚴謹」**：只有你叫它的時候才會。而且就算叫了，它也只是在 context 裡多了一套指示，模型仍然可能偏離。pstack 處理偏離的方法是把關鍵步驟變成可檢查的產物（todo 裡的 `skip: <reason>`、reply 裡必須點名的原則、決策日誌），讓偏離**被看見**，而不是保證不偏離。
3. **「go deep first = 做得慢」**：作者的主張正好相反——深度是為了之後能平行化。一個 agent 花 30 分鐘做對一件事，比十個 agent 各花 3 分鐘做錯十件事快。前提是你的任務真的需要這種深度。
4. **「沒有規劃技能 = 不做規劃」**：有 Multi-phase plan playbook，有 figure-it-out 會先設計流程。差別是規劃不是預設，而且要先拿 prototype 回答開放問題。
5. **把 poteto 的風格當成唯一正解**：作者自己準備了 `/automate-me`，從你的對話紀錄裡挖出你的習慣，產生 `<你的名字>-mode`。照抄別人的風格，等於把別人的判斷套在你的問題上。

## 進階：再往深一層

- **讀 git log 當教材**：`gh api "repos/cursor/plugins/commits?path=pstack&per_page=100" --paginate --jq '.[] | "\(.commit.author.date[:10]) \(.commit.message | split("\n")[0])"'` 會列出每一個 commit 標題。挑幾個看起來像「修正 agent 行為」的，點進 PR 看描述，你會看到每條規則背後的真實事故。
- **把它放進 Anthropic 的分類**：[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) 把 agent 系統分成 prompt chaining、routing、parallelization、orchestrator-workers、evaluator-optimizer 幾種。poteto-mode 本身是 routing；arena 是 parallelization + evaluator；Orchestrate playbook 是 orchestrator-workers。用這個分類讀 pstack，你會發現它不是發明新結構，而是把已知結構組合成一套可用的工作流。

## 動手練習

1. 不看本章，從 `$PSTACK/skills/` 的目錄名自己分類一次，再和第三節的地圖比對。哪些技能你分不出來？讀它的 description 再分一次。
2. 挑一個你最近讓 Claude Code 做、結果不滿意的任務。用第一節「新人的三個典型問題」對照：它犯的是哪一種？pstack 裡哪一份 playbook 或哪一條原則是在擋這種錯？

## 本章重點整理

- pstack 是 Lauren Tan（poteto）把自己的工程習慣寫成的 Agent Skills：23 份 playbook 是檢查表，24 條原則是共同詞彙
- 核心主張是「先深後廣」：先讓一個 agent 可靠且能自證，平行化才不會放大 slop
- 它刻意不做預設規劃、不靠人類 review、不自動觸發
- 上游變化很快，官方 Claude Code 支援 2026-10-16 才上線

## 自我檢核

- [ ] 能用自己的話向同事解釋「go deep first」為什麼是平行化的前提，而不是平行化的反面嗎？
- [ ] 不看地圖，能說出 pstack 原則的五個分組，並各舉一條嗎？
- [ ] 如果有人問「pstack 跟 spec-driven development 哪個好」，你會怎麼回答？
- [ ] 想一個你目前工作中的任務：它值得付 pstack 的「嚴謹稅」嗎？為什麼？

## 延伸閱讀

### 原始碼與官方說明

- **[pstack README](https://github.com/cursor/plugins/blob/main/pstack/README.md)**
  - **讀哪裡**：開頭作者自述、「why are there no planning skills?」、「make it yours」三段
  - **能學到什麼**：作者自己對這套東西的定位，本章的引文都出自這裡
  - **前提**：無
- **[pstack guide README](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/README.md)**
  - **讀哪裡**：「If you only remember one thing」
  - **能學到什麼**：一句話版本的使用方式——給 agent 一個目標和一個檢查方法
  - **前提**：無

### 文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)**（2024）
  - **這篇說什麼**：把 agent 系統分成五種 workflow 與 autonomous agent，並主張「先用最簡單的結構」
  - **讀哪裡**：Routing、Parallelization、Orchestrator-workers、Evaluator-optimizer 四節
  - **和本章的關聯**：pstack 的每個主要技能都能對到其中一種結構；讀完這篇你會更清楚 pstack 是「組合已知結構」而不是發明新東西
- **[Flavio Copes — A deep dive into pstack](https://flaviocopes.com/pstack/)**
  - **這篇說什麼**：第三方的英文全貌導讀
  - **讀哪裡**：整篇，十分鐘可讀完
  - **為什麼值得讀**：用另一個人的眼睛看同一套東西，可以檢查你的理解；注意它寫作的時間點可能早於官方 Claude Code 支援
- **[The Neuron — pstack explained](https://www.theneuron.ai/explainer-articles/pstack-explained-lauren-tans-system-for-trustworthy-ai-agents/)**
  - **這篇說什麼**：偏新聞性的介紹，包含 pstack 的來歷（lauren-mode、10,000 次執行）
  - **注意**：二手資料，本章引用的來歷細節都出自這類報導，未經作者原文證實

### 對照閱讀

- **[ai/spec_driven_development](../spec_driven_development/README.md)**（本 repo）
  - **讀哪裡**：README 的課程定位
  - **和本章的關聯**：站在「先寫規格」那一邊的完整論述；和 pstack 的「最好的 spec 是程式碼」對照著讀

知道了它是什麼，下一步直接打開主角：poteto-mode 那份 152 行的 SKILL.md。

→ [Ch 2 解剖 poteto-mode 的 SKILL.md](./02-anatomy-of-poteto-mode.md)
