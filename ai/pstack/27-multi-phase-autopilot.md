# Ch 27 — 多階段計畫、Autopilot 與 Orchestrate

> **目標**：搞懂 pstack 怎麼把「一個 agent 一個任務」放大到「一個計畫、一疊 PR、一群 subagent」。你會讀懂 Multi-phase plan playbook 的計畫格式——每個 PR 一節、九個固定的子區塊、十條 live lane、雙邊的效能關卡——以及它附帶的檢查腳本 `check-plan.mjs`；理解三份放大規模的 playbook（Autopilot-full、Autopilot-stack、Orchestrate）各自在什麼規模、誰合併、協調者做什麼不做什麼。你會讓一份兩個 PR 的計畫通過 check-plan，再看一份「差一點」的計畫被抓出四個問題——以及一個它抓不到的。

> **環境**：pstack 0.15.36、Node.js 26、Python 3.9（只用來從模板產生計畫）。check-plan.mjs 是 pstack 附的原始腳本；兩份計畫是本課寫的；輸出都是實際跑出來的。三份 autopilot / orchestrate playbook 依原文描述，本章沒有實際執行一個多 PR 的計畫。

## 為什麼需要這個？

有些工作一個 PR 裝不下：一個跨 50 個 package 的遷移、一個要分五步上線的功能、一個月的技術債清理。這種工作的失敗方式和單一任務不同：

- **計畫寫得很漂亮，但沒有人用程式碼測試過它的假設**。官方設計章：「polishing a plan that no code has tested」是兩大設計錯誤之一
- **計畫裡的「驗證」只有「跑測試」**。測試綠了，PR 合了，使用者看到的行為壞了
- **一群 agent 同時寫，互相覆蓋**；或一個協調者自己下去寫程式，失去了「協調者審查 worker」的分離
- **一個 PR 合併之後，上面那些 PR 的驗證結論悄悄失效**

## 先建立直覺：四種規模

```
  規模                    playbook              誰寫程式          誰合併              協調者做什麼
  ────────────────────   ──────────────────    ──────────       ──────────────      ──────────────────
  一個任務                Feature / Bug fix…     delegate          （開 PR，人合併）      主 session 規劃、審查
  一個任務，跑一整晚        Autonomous run         delegate          （開 PR，人合併）      驅動到 predicate
  一個計畫（多個 PR）       Multi-phase plan       （不執行）          ─                    只寫計畫，不實作
  一個 PR 佇列，完全自主     Autopilot-full         每個 PR 一個 owner  owner（root 驗證後）   swarm 驗證每個 PR
  一個 PR 佇列，人來合併     Autopilot-stack        每個 PR 一個 owner  operator             同上，停在 merge-ready
  一個長期專案（多天）       Orchestrate            worker            owner                **從不寫程式**，決定誰做什麼、檢查產出
```

## 一、Multi-phase plan：計畫是一份可以打勾的清單

> **You own the plan, not the code. The plan is a checklist an owner runs box by box and the operator audits from the evidence.** The plan is the deliverable. Do not implement.

七步：

1. **一兩個檔案、做法明顯的改動，不寫計畫**。說一聲，停。
2. **寫之前用 prototype 解決開放問題**。保留 branch、SHA、截圖給 Appendix A。只有沒有任何執行能解決的產品或偏好決定才問 operator。
3. **探索交給 subagent**（`poteto-agent`），每個回傳檔案指標、慣例、測試指令、進入點，「No inlined dumps」
4. **照骨架填計畫**：每個 PR 一節；在「How to read this」裡指名執行用的 playbook（會合併就 autopilot-full；operator 合併就 autopilot-stack；長期專案就 orchestrate）
5. **用 technical-writing 寫、再 unslop**；本體是 how-to 模式，附錄放 explanation 與 reference；不准長破折號、不准句中冒號
6. **跑 `node pstack/skills/poteto-mode/scripts/check-plan.mjs <plan.md>`，修掉它印出的每一行**（Encode Lessons in Structure）
7. **交回**：貼計畫路徑與腳本的輸出，然後停。執行要等 operator 明確說開始

第 2 步呼應 pstack 的「沒有規劃技能」立場（Ch 1）：計畫可以寫，但要**在用程式碼回答了開放問題之後**才寫。

### 驗證規則：測試不夠

> **Verification.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked (the **prove-it-works** principle skill). That sentence is the verification rule. Every verification block opens with it.

這句話被要求**逐字出現**在每一個驗證區塊的開頭。check-plan 會檢查。

三種驗證：

| 區塊 | 內容 |
|---|---|
| **Verify, unit.** | 測試檔與它新增的案例；執行指令 |
| **Verify, live.** | **十條 lane**，在 PR head 上用 swarm workers 的模型、透過 control skill 驅動真實介面；每條一個具體場景、一張截圖、一個通過條件。其中一條是「Regression lane against trunk」：同一個關鍵場景在 trunk 與 head 上各跑一次 |
| **Verify, perf.** | 四項：Metric（在 trunk 與 head 量什麼）、Probe（指令，交錯執行）、Baseline（先量 trunk）、Rule（head 對 trunk、會失敗的數字） |

**為什麼是十條 lane？** 原文沒有解釋這個數字。合理的推論是：它逼計畫作者為每個 PR 想出十個不同的使用者場景，而不是只寫一條 happy path——十條裡至少會有幾條是邊界與失敗情況。這是推論，不是原文的說法。

**Regression lane against trunk** 的設計很細：如果 trunk 還沒有這個功能，這條 lane 記錄這個事實，並改成檢查 diff 新增的行為與使用者等待的最終狀態，「instead of inventing a trunk result」。效能關卡也一樣：「Do not claim a ratio between unlike scenarios.」——兩邊跑的不是同一件事，就不能說 head 比 trunk 快多少。

**Review gate**：會改變互動的 PR 要給 operator 看截圖與影片才能合併；不改變互動的 PR 寫 `**Review gate.** None. <PR id> is not review-gated.`，下面不准有任何方框。

### 每個 PR 的九個子區塊

```
## <任務，動詞片語> (<PR id>)

**Depends on.**       另一個 PR id，或 None.
**Files.**            - [ ] Edit / Create / Delete `<path>`
**Build.**            - [ ] 一個改動，指名符號與檔案
**You see.**          - [ ] 一個可觀察的結果，附確切的 log 行或畫面狀態
**Verify, unit.**     <驗證規則原句>  - [ ] 測試檔與新案例。Run `<command>`.
**Verify, live.**     <驗證規則原句> Ten lanes on `<model>` at the PR head ...
                      - [ ] Lane 1. Regression lane against trunk. ... Save `<x>.png`. Pass when ...
                      ...（到 Lane 10）
**Verify, perf.**     <驗證規則原句>  - [ ] Metric. / Probe. / Baseline. / Rule.
**Review gate.**      None. ... 或者 三個方框（截圖、影片、在 chat 等 operator）
**Merge.**            - [ ] clean verdict at the exact head SHA / triage / rebase 且 patch-id 不變 / 誰合併
```

計畫的其他部分：開頭不到十行的摘要、「How to read this」、「Program checklist」（五個 H3：Arm the program、Spawn owners、PR mechanics、Verdict and merge、Boot recipe）、「Close the program」、四個附錄（Prototype evidence、Alternatives rejected、Risks、Links and reading list）。

「Arm the program」裡有一個值得注意的設計：一個 `/loop 1h` 的審計 tick，附一段要**逐字使用**的 tick prompt。它要求每小時重讀 trunk 上的執行 playbook、對照它審計目前的操作、修正偏離、**只在有新的變化時**才發訊息給 operator：「Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text.」——一個不會每小時刷一張重複狀態表的協調者。

## 二、check-plan.mjs：把計畫格式編碼成檢查

這支 205 行的腳本檢查的東西，可以分成三類：

| 類別 | 例子 |
|---|---|
| **文字規則**（code block 與 inline code 之外） | 長破折號、彎引號、句中冒號（`: ` 後面接非空白） |
| **結構** | 有 H1；開頭摘要少於十行；有「How to read this」「Program checklist」「Close the program」；Program checklist 的五個 H3 依序出現；PR 區段在 Program checklist 與 Close the program 之間；Close 之後只能是附錄，而且要有 Prototype evidence |
| **每個 PR 的內容** | 九個子區塊依序；Depends on 不能是空的；Files / Build / You see / Verify unit / Merge 都要有方框；三個驗證區塊都以驗證規則開頭；live 要有「Ten lanes on \`<model>\` at the PR head」、lane 1 到 10、每條有截圖與通過條件；perf 四項依序；review gate 的兩種形式 |

### 一份通過的計畫

用練習 B 的通知修正當題材：N1 把已送出的狀態持久化，N2 為每次推送加上冪等鍵。完整的計畫在練習 D 的參考解答裡（180 行）；這裡看 check-plan 的輸出：

```bash
node check-plan.mjs plan.md; echo "exit=$?"
```

```
Persist sent status in the store (N1)  boxes=27  files=3 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=3 merge=4
Send an idempotency key with each push (N2)  boxes=23  files=2 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=0 merge=4
2 PR sections, 0 problems
exit=0
```

每個 PR 一行摘要，列出每個子區塊有幾個方框。N1 改變了使用者收到的東西，所以 review gate 有 3 個方框；N2 不改變互動，review gate 是 0。

### 一份差一點的計畫

在通過的計畫上做四個常見的改動：開頭用了「Goal: ... — even across retries」（冒號加長破折號）、N2 少寫一條 lane、N1 的 perf 少了 Baseline、review gate 把「video」改成「clip」：

```
Persist sent status in the store (N1)  boxes=26  ... verify-perf=3 ...
Send an idempotency key with each push (N2)  boxes=22  ... verify-live=9 ...
2 PR sections, 4 problems
plan-nearmiss.md:3: long dash
plan-nearmiss.md:3: mid-sentence colon
plan-nearmiss.md:91: Persist sent status in the store (N1): perf boxes are [Metric., Probe., Rule.], expected [Metric., Probe., Baseline., Rule.]
plan-nearmiss.md:131: Send an idempotency key with each push (N2): lanes are [1,2,3,4,5,6,7,8,9], expected 1 to 10
exit=1
```

四個改動，抓到三個（四行問題，第 3 行有兩個）。**「video」改成「clip」沒被抓到**：review gate 的檢查是「區塊裡有沒有出現 `screenshot`、`video`、`operator` 這三個字」，而同一個區塊的另一行（「Post the screenshots and the video in chat」）仍然有 video。

這是一個很好的提醒：**check-plan 檢查的是格式的必要條件，不是計畫的品質**。它保證每個 PR 都有十條 lane、每條都有截圖與通過條件——但它無法判斷那十條 lane 是不是十個真正不同的場景、通過條件是不是真的能區分對錯。Ch 15 的 banned-words lint、Ch 23 的 unslop-lint 都是同樣的定位：擋住機械可檢查的錯，把判斷留給人與審查者。

（Baseline 被抓到的那一行，錯誤訊息的行號是 91——perf 區塊的開頭，而不是「應該有 Baseline 的那一行」。一個好的檢查工具會盡量把錯誤指向使用者要改的地方；這裡它指向的是區塊。）

## 三、Autopilot-full：一個 PR 佇列，跑到合併

> **You own the verdicts, never the PRs. One owner carries each PR from build to merge, and nothing merges until your swarm, which never wrote the code, has proven the code that lands.**

關鍵規則：

1. **operator 的關卡優先**。授權只涵蓋佇列裡的項目；operator 指名為自己的項目、佇列外的 PR 都停在 merge-ready。「A request to state the plan is not a go.」
2. **每個 PR 一個 owner**（一個 cloud agent），負責建造、開 PR、在真實產物上證明、babysit 到綠、在 step 4 與 5 放行後合併。「Assume the VM dies」——一開始就推送並開始決策軌跡，每完成一個單元就推送
3. **owner 平行地從 trunk 開，不疊**，讓每個 PR 能單獨合併
4. **swarm 驗證一次**：在 code-ready head 跑一次完整的 swarm；之後只有新行為、重新設計、或很大的新 diff 才再跑完整 swarm；rebase、解衝突、小修正只要一個驗證者；純文字修改不用。「Block a PR only for a problem you can show, with a failing run at the head or a doc line the PR made wrong.」

「the root swarm-verifies each PR before its owner merges」——owner 自己的結論不算數，必須是一個沒寫過這段程式碼的 swarm（Ch 24 Shipping 的同一個原則）。

**Autopilot-stack** 是 Autopilot-full 去掉合併：「You wake up to verified PRs, each with its verdict, and you review and land them yourself.」而且「A PR stacks only on a PR it builds on」——只有真的依賴時才疊。

## 四、Orchestrate：協調者從不寫程式

> **You run the program, never the code. Decide which agent does each piece of work, then check what each one produces.**

它的適用範圍有一條明確的界線：「Work that one agent could finish inside the session's budget is not a program.」——如果一個 agent 在一個 session 裡做得完，就走 Autonomous run，不是 Orchestrate。

### Topology

- **每個 PR 一個 owner**：唯一能寫那個 branch 的人；只有 owner 能 push、rebase、retarget。協調者從不寫或改程式碼
- **每個獨立任務一個新的 worker**：kickoff 帶著它需要的一切，包括它依賴的每一份上游報告，**全文貼上**（「Workers can't see their siblings」）
- **只在一個區域有很多平行 worker 時才用子協調者**：它擁有那個區域的 worker 與 kickoff，送回**一份彙總**，從不轉送原始報告。「Stop at three levels」；「Keep about ten workers in flight per coordinator」
- **需要 worker 的建置狀態時才 fork**；只在同一份工作的後續時才傳訊息給 worker；「Never message one to check on it, because a message restarts an idle agent」
- **每個 PR 一個不是作者的驗證者，不同家族**
- **先做一個單元**：「Take it through kickoff, owner, verifier, and merge before you fan out, so a flaw in the kickoff costs one agent instead of fifty.」

最後一條是 Orchestrate 裡最實用的一句：**在放大之前，先用一個單元測試整條流程**。一份有缺陷的 kickoff，在一個 agent 上失敗只浪費一個 agent；在五十個 agent 上失敗就浪費五十個。

### Supervision

- **每份 kickoff 指名證明工作完成的證據**（一個指令與它的預期輸出、一個 control-skill 檢查、一個產物），以及目標、可以與不可以寫的路徑、不可以做的動作。「If you can't name the evidence, the unit isn't scoped yet.」
- **讀 diff 或產物，不只讀 worker 的摘要**；「Green CI is an input to a verdict, not a verdict」；「A verdict covers one head SHA, so a new push needs a new verdict」
- **把偏離範圍或長得超出要求的工作退回**；只保留阻擋整個計畫的修正，其他的當後續

## 五、在 Claude Code 上跑這些 playbook

這三份 playbook 是在 Cursor 的環境裡設計的：cloud agent 各有自己的 VM、`control-ui` / `control-cli` 來自 `cursor-team-kit`、Bugbot 會在 PR 上留言、Cursor Projects 給協調者一個持久的 thread。在 Claude Code 上，依 adapter 的規則，每一項都有對應或降級：

| 計畫裡的元素 | Cursor | Claude Code 上的對應 | 影響 |
|---|---|---|---|
| 每條 live lane 一台 cloud VM | `environment: "cloud"` | `isolation: "worktree"` | 只隔離檔案；十條 lane 同時跑會搶 port、資料庫、CPU（Ch 19） |
| 驅動 UI / CLI | `control-ui`、`control-cli`（cursor-team-kit） | 沒有內建；用你自己的驗證技能（Ch 21）或 Claude in Chrome 之類的瀏覽器工具 | 計畫的 Boot recipe 要改寫成你的驅動方式 |
| 每小時的審計 tick | Cursor 的 `/loop 1h` | Claude Code 的 `/loop` 技能 | 語意相近，行為以你的版本為準（Ch 25） |
| Bugbot triage | Bugbot 的評論 | GitHub 上的一般 review thread；沒有 Bugbot 檢查時 skip 並留一行 | 那一個 Merge 方框變成 skip |
| 協調者的持久 thread | Cursor Projects | 一個長的 Claude Code session；被壓縮時靠決策日誌與 Session pickup | 協調者的記憶比較脆弱，決策日誌更重要 |
| 合併 | `gh` 或 `origin` | `gh` | 相同 |

一個實際的建議：在 Claude Code 上，**先用 Autopilot-stack，不要用 Autopilot-full**。stack 版停在 merge-ready，讓你早上自己審每一個 PR 的結論；在評審多樣性塌縮（Ch 17）、live lane 只能在本機的 worktree 跑的環境裡，保留人工合併這道閘門，比讓 agent 自己合併安全得多。

另一個建議：**計畫的 live lane 數量照寫，執行時誠實記錄**。如果你的環境一次只能跑三條 lane，就三條三條跑，在決策日誌裡記下每一批的時間與結果。不要因為環境限制就把計畫改成三條 lane——check-plan 要求十條是有理由的，而「我們這次只跑了七條」應該是一個被看見的缺口，不是一個被藏起來的捷徑。

### 一個計畫的生命週期

```
   寫計畫（Multi-phase plan playbook）
     │ prototype 回答開放問題 → 填骨架 → technical-writing + unslop → check-plan → 交回
     ▼
   operator 審計畫，說「開始」          ← 唯一的人工啟動點
     │
     ▼
   Arm the program：讀 trunk 上的 playbook、設定 /loop 的審計 tick
     │
     ▼
   Spawn owners：依依賴圖，每個 PR 一個 owner
     │
     ├─ owner：建造 → 開 PR → 自證 → babysit 到綠 → 回報 code-ready head
     ├─ root：swarm 驗證（gates + 十條 live + perf + 兩條 audit）→ verdict
     ├─ review-gated 的 PR：截圖 + 影片給 operator → 等 operator 點頭
     └─ 合併（Autopilot-full：owner；Autopilot-stack：operator）
     │
     ▼
   Close the program：每個方框都有證據 → 回報
```

## 底層機制：規模放大時，什麼東西必須變成結構

```
  一個 agent                         五十個 agent
  ──────────                        ──────────────
  紀律可以靠指示                       指示會被五十種方式誤讀
  └─ 「記得驗證」                      └─ check-plan：每個 PR 的驗證區塊格式是強制的
  └─ 「不要改別人的檔案」               └─ 每個 PR 一個 owner，只有 owner 能寫那個 branch
  └─ 「做完跟我說」                     └─ kickoff 指名證據；協調者讀 diff 不讀摘要
  └─ 「有問題就停」                     └─ 先用一個單元測試整條流程
  主 session 看得到所有東西              協調者只看彙總：子協調者送回一份，不轉送原始報告
```

Encode Lessons in Structure 在規模放大時從「好的實踐」變成「必要條件」：一個指示在一個 agent 上有 90% 被遵守的機率還可以接受，在五十個 agent 上就意味著五個不遵守。

## 對比與取捨

| playbook | 規模 | 誰合併 | 驗證 | 人的參與 |
|---|---|---|---|---|
| Multi-phase plan | 計畫 | — | 計畫裡規定的 unit / live / perf | 審計畫、說「開始」 |
| Autopilot-stack | PR 佇列 | operator | 每個 PR 一次完整 swarm | 早上審並合併 |
| Autopilot-full | PR 佇列 | owner（root 驗證後） | 同上，加上 patch-id 規則 | 設定關卡；可以不在 |
| Orchestrate | 多天、多 PR、幾十到幾百個 subagent | owner | 每個 PR 一個不同家族的驗證者 | 一天看幾次 |

## 踩雷集錦

1. **在開放問題還沒解決時就寫計畫**：Multi-phase plan 第 2 步要先用 prototype 解決。一份建立在未經驗證假設上的計畫，越詳細越危險。
2. **只用「測試通過」驗證一個 PR**：「Tests alone are not sufficient verification.」這句話要逐字出現在每個驗證區塊。
3. **相信 check-plan 0 個問題就代表計畫好**：本章的「clip」沒被抓到。check-plan 保證格式，不保證十條 lane 是十個真正不同的場景。
4. **協調者自己下去寫程式**：Orchestrate 原文：「You never author or edit code.」協調者寫程式，就沒有人在審查協調者了。
5. **一開始就放大到五十個 agent**：「Start with one unit.」一份有缺陷的 kickoff 會被複製五十次。
6. **因為環境限制就悄悄改計畫**：在 Claude Code 上一次只能跑三條 lane，就把計畫改成三條。計畫應該照寫，執行的缺口記在決策日誌裡，讓它被看見。

## 進階：再往深一層

- **讀完整的 Multi-phase plan 骨架**（playbook 裡那段用四個反引號包起來的 markdown）。它的每一個方框都是一次真實執行裡被跳過的步驟。特別是「PR mechanics」裡的 rebase 規則：「Rebase onto current trunk before the code-ready report and babysit. Keep that merge base in fix rounds. Rebase again only at merge prep, on a `git merge-tree` conflict with trunk, on a CI failure that comes from a change on trunk, or when a known concurrent change to the same files lands.」——每一次 rebase 都會讓 CI 重跑、讓驗證結論需要重新檢查，所以 rebase 的時機被精確地限制。
- **check-plan 的測試**：`scripts/check-plan.test.ts` 用 Bun 測試這支檢查器本身。一支檢查工具有自己的測試，是「檢查器也要被檢查」的實踐（Ch 6 的 prompt-lint、Ch 20 的驗證器都曾經在本課出過錯）。移植版 0.9.107 的 changelog 修了一個 check-plan 的 bug：code block 裡的範例文字曾經被算進必要的計畫內容。
- **在 Claude Code 上跑 Autopilot**：Autopilot 的 owner 原本是 Cursor cloud agent。adapter 對應到 `isolation: "worktree"`，但 cloud VM 提供的隔離（各自的 port、資料庫、可以錄影的 UI）worktree 給不了。在本機跑十條 live lane 的 swarm，要特別注意 Ch 19 說的資源衝突。

## 動手練習

1. 拿練習 D 的參考計畫（或你自己的），故意做三種本章沒試過的破壞：刪掉「Program checklist」裡的一個 H3、在一個 lane 裡拿掉 `Pass when`、在 Close the program 之後加一個不是附錄的 `##` 段落。跑 check-plan，它的訊息指向你要改的地方嗎？
2. 找出 check-plan 另一個「格式對、意圖錯」的漏洞（提示：十條 lane 的場景如果全部一樣呢？perf 的 Rule 如果寫「head is fine」呢？），寫一個最小的修改讓它抓得到，並寫一個測試案例證明你的修改有效。
3. 為你手上一個跨多個 PR 的工作，先寫出 Multi-phase plan 第 2 步要的東西：列出所有開放問題，標出哪些能用 prototype 回答、哪些是真正要問 operator 的產品決定。

## 本章重點整理

- Multi-phase plan 只交付計畫、不實作；先用 prototype 解決開放問題；每個 PR 一節、九個固定子區塊；驗證規則逐字出現在三個驗證區塊；live 是十條 lane（含一條對 trunk 的回歸）；perf 是 Metric / Probe / Baseline / Rule
- check-plan.mjs 把計畫格式編碼成檢查；本章的通過計畫 0 個問題、差一點的計畫抓到 4 個——但「video」改成「clip」沒被抓到，因為它檢查的是關鍵字而不是意圖
- Autopilot-full：每個 PR 一個 owner、平行不疊、swarm 驗證一次、只有沒寫過程式碼的 swarm 能放行合併；Autopilot-stack 是它去掉合併
- 在 Claude Code 上：cloud VM 變成 worktree、control skill 要換成你自己的驗證技能、先用 Autopilot-stack 保留人工合併；計畫照寫，執行的缺口記進日誌
- Orchestrate：協調者從不寫程式；每個 PR 一個 owner；每個任務一個新 worker、kickoff 帶齊上游報告；最多三層、每個協調者約十個 worker；先用一個單元跑完整條流程再放大

## 自我檢核

- [ ] 不看本章，能說出 Multi-phase plan 每個 PR 的九個子區塊嗎？
- [ ] 為什麼「Tests alone are not sufficient verification」要逐字出現、而且由腳本檢查？
- [ ] check-plan 通過，代表這份計畫是好的嗎？用本章的「clip」例子說明它保證了什麼、沒保證什麼
- [ ] Orchestrate 的「Start with one unit」在你的工作裡對應什麼？你上一次「一開始就放大」造成了什麼後果？

## 延伸閱讀

### 原始碼

- **[playbooks/multi-phase-plan.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/multi-phase-plan.md)**
  - **讀哪裡**：七個步驟、Verification 段、完整的骨架模板
- **[scripts/check-plan.mjs](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/scripts/check-plan.mjs)** 與 **[check-plan.test.ts](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/scripts/check-plan.test.ts)**
  - **讀哪裡**：開頭的常數（RULE、SUB_BLOCKS、PROGRAM_H3）就是計畫格式的規格
- **[playbooks/autopilot-full.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/autopilot-full.md)**、**[autopilot-stack.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/autopilot-stack.md)**、**[orchestrate.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/orchestrate.md)**
  - **讀哪裡**：orchestrate 的 Topology 與 Supervision 兩節

### 官方指南

- **[docs/guide/07-overnight.md「When the night holds a queue, not a task」與「Run many projects in parallel」](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/07-overnight.md)**
  - **能學到什麼**：三份放大規模的 playbook 的官方 prompt 範例，以及 Cursor Projects 的用法

### 文章

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)**
  - **這篇說什麼**：一個 lead agent 協調多個 subagent 的真實系統；包括「scale effort to query complexity」與 subagent 之間重複工作的問題
  - **和本章的關聯**：Orchestrate 的「一個協調者、明確的 kickoff、不轉送原始報告」與這篇的經驗高度一致；這篇也提到多 agent 系統的 token 成本可能是單 agent 的數倍
- **[Martin Fowler — Strangler Fig Application](https://martinfowler.com/bliki/StranglerFigApplication.html)**
  - **和本章的關聯**：大型遷移的經典分階段策略；對照 Outcome-Oriented Execution（Ch 12）與 Multi-phase plan 的「每個 PR 都要能單獨驗證、單獨合併」，你會看到兩種處理「遷移中間狀態」的哲學

Part 5 的內容到此結束。下一個練習：寫一份過得了 check-plan 的計畫，配一份對得上帳的決策日誌。

→ [練習 D：寫一份過得了 check-plan 的計畫與決策日誌](./practice-d-plan-and-log.md)
