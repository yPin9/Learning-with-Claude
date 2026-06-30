# Ch 28 — GitHub Spec Kit（二）：/speckit.* 工作流端到端

> **目標**：走完 `/speckit.constitution` → `specify` → `clarify` → `plan` → `tasks` → `analyze` → `implement` 的完整序列，理解每一個指令產生什麼 Markdown 產物、這些產物之間如何傳遞資訊，以及哪些步驟可以省略、哪些省略會埋下技術債。
>
> **環境**：GitHub Spec Kit v0.11.10（查證日期 2026-06-30）。指令集在快速迭代，以 `specify integration list` 和官方 README 的命令表為準。

---

## 在開始之前：一個心智模型

想像你在帶一位新來的工程師。這位工程師技術力極強，但他只按字面意思辦事——你說「幫我加個按鈕」，他真的只加一顆按鈕，沒有連接任何事件、也沒有問你按下去要做什麼。

Den Delimarsky（GitHub Principal Product Manager）在 2025 年 9 月 Spec Kit 發布時說：

> We treat coding agents like search engines when we should be treating them more like literal-minded pair programmers.

Spec Kit 的整個工作流，就是把你對功能的模糊想法逐步轉換成這位「字面意思工程師」能正確執行的精確文字。每一道 `/speckit.*` 指令，都是把不確定性從文字中逼出來，讓它在代碼之前就得到解決。

用一張圖把工作流看清楚：

```
使用者想法（模糊）
        │
        ▼
┌──────────────────────┐
│  /speckit.constitution│  只跑一次，設全專案準則
└─────────┬────────────┘
          │  .specify/memory/constitution.md
          ▼
┌──────────────────────┐
│  /speckit.specify    │  WHAT + WHY，不談 HOW
└─────────┬────────────┘
          │  specs/001-my-feature/spec.md
          ▼
┌──────────────────────┐  (選用)
│  /speckit.clarify    │  結構化 Q&A，消滅歧義
└─────────┬────────────┘
          │  spec.md 新增 Clarifications 節
          ▼
┌──────────────────────┐
│  /speckit.plan       │  HOW：技術架構 + 設計決策
└─────────┬────────────┘
          │  plan.md + research.md + data-model.md
          │  + quickstart.md + contracts/api-spec.json
          ▼
┌──────────────────────┐  (選用)
│  /speckit.checklist  │  英文驗收條件的單元測試
└─────────┬────────────┘
          │  checklist annotations in plan.md
          ▼
┌──────────────────────┐
│  /speckit.tasks      │  可執行任務列表，[P] 標並行
└─────────┬────────────┘
          │  tasks.md
          ▼
┌──────────────────────┐  (選用)
│  /speckit.analyze    │  跨產物一致性稽核
└─────────┬────────────┘
          │  分析報告，回補 tasks.md
          ▼
┌──────────────────────┐
│  /speckit.implement  │  驅動 agent 逐任務寫碼
└─────────┬────────────┘
          │  實際程式碼變更
          ▼
┌──────────────────────┐  (選用)
│  /speckit.converge   │  對比 spec/plan/tasks 與現況，補剩餘工作
└──────────────────────┘
```

注意：README 用 Core/Optional 兩張表格呈現指令，並不是一個嚴格有序的九步序列。以上順序是根據文件中明確陳述的相對位置（clarify 在 plan 之前，analyze 在 tasks 之後、implement 之前）拼出的合理合成，而非 README 逐字引述（查證日期 2026-06-30）。

---

## 第一步：/speckit.constitution——專案的憲法

### 為什麼需要「憲法」？

在 Spec Kit 出現之前，開發者通常用 README、CONTRIBUTING.md、或零散的 `.cursorrules` 把規範傳給 AI agent。這些文件的問題是：它們沒有被 agent 工作流主動讀取，每次對話都要靠 context window 的碰運氣。

constitution.md 存在 `.specify/memory/` 底下，每道 `/speckit.*` 指令被 agent 執行前，對應的 prompt file 會把憲法自動注入。規範從被動文字變成主動約束。

### 實際跑一遍

```
/speckit.constitution Create principles focused on:
- All state changes must be logged with timestamp and actor
- API must follow REST conventions with versioning (/api/v1/...)
- UI components must have accessibility (ARIA) attributes
- No external dependencies without explicit approval in spec
```

產出寫到 `.specify/memory/constitution.md`。constitution 跑一次即可，後續功能開發全部繼承這些準則。修改時重跑 `/speckit.constitution`，它會覆寫或更新這份文件。

---

## 第二步：/speckit.specify——只問 WHAT，不問 HOW

### 模板作為 LLM 的主動約束

`spec-template.md` 刻意不讓你填技術棧。模板要求聚焦在使用者旅程（user journey）和驗收標準，並且在任何不明確的地方插入 `[NEEDS CLARIFICATION: ...]` 標記。

這是一個反直覺的設計：你可能想在 spec 裡說「用 PostgreSQL 實作」，但 Spec Kit 把這個空間留到 `/speckit.plan`。原因是：技術決策混入需求描述，會讓 spec 在技術棧改變時整個作廢，也讓 agent 在理解需求時被實作細節干擾。

> 如果你對「需求與設計分離」的理論基礎還不熟，先回看 [Ch 7 規格 vs 設計 vs 實作](./07-spec-design-implementation.md)。

### 指令與產物

假設你在做一個任務管理 app（以下用 Taskify 為範例，對應 spec-driven.md 官方文件的實例）：

```
/speckit.specify
I want to add a feature that lets users assign tasks to team members
and receive email notifications when tasks are assigned to them.
```

指令在幕後做三件事（查證自 spec-driven.md，查證日期 2026-06-30）：

1. 呼叫 `create-new-feature.sh`，掃描 `specs/` 目錄下所有現存功能，找出最大號碼，產出下一個編號（例：`002`）
2. 建立語意化分支：`git checkout -b 002-task-assignment-notifications`
3. 把 spec-template.md 複製到 `specs/002-task-assignment-notifications/spec.md`，然後呼叫 LLM 填入內容

產出的 `spec.md` 骨架：

```markdown
# Feature: Task Assignment & Email Notifications

## Overview
Allow users to assign tasks to team members and trigger
email notifications upon assignment.

## User Journeys

### Journey 1: Assigning a Task
1. User opens a task
2. User selects "Assign to" and searches by name
3. System saves assignment and triggers notification

### Journey 2: Receiving Notification
1. Assignee receives email within 5 minutes of assignment
2. Email contains task title, description, and a deep link

## Acceptance Criteria
- [ ] A task can be assigned to exactly one team member
- [ ] Assignment triggers an email within 5 minutes [NEEDS CLARIFICATION: SLA under load?]
- [ ] Unassigning also sends a notification [NEEDS CLARIFICATION: confirm scope]

## Out of Scope
- Push notifications (mobile)
- Assignment history / audit log (separate feature)
```

注意兩個 `[NEEDS CLARIFICATION]`。這些標記是 spec-driven.md 明確設計的機制：模板強迫 LLM 把它自己不確定的地方明確標出來，而不是默默猜測。

---

## 第三步：/speckit.clarify——把歧義逼出來

### 為什麼「猜」比「問」貴

在傳統開發裡，需求歧義往往拖到實作階段才被發現，那時候改動成本已經疊加到架構上。

> 如果你對需求模糊度的系統性成因有興趣，先回看 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)。

`/speckit.clarify` 是 `/speckit.plan` 之前的選用步驟（README 明確標注：`recommended before /speckit.plan`）。它以覆蓋率為基礎，依序對 spec.md 裡的每個歧義點提問。

### 實際對話

```
/speckit.clarify
```

Agent 讀取 spec.md，針對 `[NEEDS CLARIFICATION]` 標記產出問題：

```
Q1: The spec says notification within 5 minutes. Is this a hard SLA 
    (we page on-call if breached) or a soft target (best-effort)?

Q2: Should unassigning a task (removing an assignee) also trigger 
    an email to the former assignee?

Q3: If the assignee's email is invalid, should we fail silently, 
    retry, or surface an error to the assigning user?
```

你逐一回答，agent 把答案折回 spec.md 的 **Clarifications** 節：

```markdown
## Clarifications

**Q: 5-minute SLA hard or soft?**
A: Soft target. Log breaches but do not page.

**Q: Unassign notification?**
A: Yes. Notify former assignee with reason "task reassigned".

**Q: Invalid email?**
A: Retry 3 times, then mark delivery failed in DB; no UI error.
```

spec.md 現在已經沒有未解決的歧義。下一步的 plan.md 可以把這些決策當作輸入。

---

## 第四步：/speckit.plan——HOW 的技術實作計畫

### plan.md 不是 spec.md 的複製

這是踩雷最多的地方。plan.md 的職責是：給定 spec.md 描述的 WHAT，決定 HOW。它主動讀取 constitution.md，確保技術決策符合全局規範。

```
/speckit.plan
Use PostgreSQL for persistence, SendGrid for email delivery,
BullMQ for async job queue (Node.js stack).
```

你在指令裡提供技術棧選擇。agent 產出一組文件：

**`specs/002-task-assignment-notifications/plan.md`**——核心技術計畫：

```markdown
# Technical Plan: Task Assignment & Email Notifications

## Architecture Overview
- New `assignments` table (PostgreSQL)
- BullMQ job: `notification:email` — dispatched on assignment CRUD
- SendGrid template: `task-assignment-v1`

## Delivery Failure Handling
Per clarification: retry 3 times (exponential backoff),
then write delivery_failed=true to assignments table.

## Constitution Alignment
- State changes logged with timestamp + actor ✓
- API endpoint: /api/v1/tasks/:id/assignment ✓
- No new dependencies — SendGrid and BullMQ already in package.json ✓
```

同時產出 `data-model.md`（完整 schema migration 草稿）、`contracts/api-spec.json`（OpenAPI 合約）、`research.md`（agent 查找的技術細節）、`quickstart.md`（本機啟動步驟）。這些支援文件不是裝飾品：tasks.md 在下一步會讀取 data-model.md 和 contracts/ 來確保任務描述夠具體。

---

## 第五步（選用）：/speckit.checklist——英文需求的單元測試

### 為什麼驗收條件需要被驗證？

spec.md 裡的驗收條件是自然語言。就像程式碼有單元測試，自然語言的需求也需要一層品質閘。`/speckit.checklist` 生成一組結構化的檢驗問題，確認每條驗收標準是否夠 SMART（具體、可量測、可達成、相關、時限）。

```
/speckit.checklist
```

產出範例：

```
✓ "A task can be assigned to exactly one team member"
  → Testable? YES. Clear boundary condition.

⚠ "Assignment triggers an email within 5 minutes"
  → Per clarification this is soft target. Spec should say
     "target" not "triggers". Recommend wording update.

✓ "Unassigning also sends a notification"
  → Scope confirmed in clarifications. Testable.
```

> 如果你對驗收條件的系統性寫法有興趣，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

---

## 第六步：/speckit.tasks——把計畫分解成可執行的原子任務

### tasks.md 是 agent 的工單

plan.md 是給人類看的設計文件。tasks.md 是給 agent 執行的工單。兩者的粒度不同：plan 描述系統層次的決策，tasks 描述具體的程式碼動作。

```
/speckit.tasks
```

Agent 讀取 plan.md（必要）、data-model.md 和 contracts/（選用）。產出的 tasks.md：

```markdown
# Tasks: Task Assignment & Email Notifications

## Phase 1: Database

- [ ] Create migration: add `assignments` table with columns
      (id, task_id, assignee_id, assigned_by, created_at, delivery_failed)
      See data-model.md for exact types.
      [P]

- [ ] Create migration: add index on assignments(task_id), assignments(assignee_id)
      [P]

## Phase 2: API Layer

- [ ] POST /api/v1/tasks/:id/assignment
      Body: { assignee_id: UUID }
      Response: 201 with assignment object
      See contracts/api-spec.json for full schema.

- [ ] DELETE /api/v1/tasks/:id/assignment
      Response: 204 No Content

## Phase 3: Notification Worker

- [ ] Create BullMQ worker: notification:email
      - On assignment created: send "task-assigned" template via SendGrid
      - On assignment deleted: send "task-unassigned" template
      - Retry 3 times (exponential backoff)
      - On final failure: set delivery_failed=true in assignments table

## Phase 4: Integration

- [ ] Wire POST/DELETE handlers to dispatch BullMQ jobs
- [ ] Add assignment events to audit log (per constitution: timestamp + actor)

## Phase 5: Tests

- [ ] Unit tests: assignment service (mock SendGrid, mock BullMQ)
- [ ] Integration test: POST /api/v1/tasks/:id/assignment → job enqueued
      [P]
- [ ] Integration test: delivery failure sets delivery_failed flag
      [P]
```

`[P]` 標記代表「可並行（Parallel）執行」，同一個 `[P]` 群組的任務之間沒有依存關係，agent 可以同時開多個工作流處理。

---

## 第七步（選用）：/speckit.analyze——跨產物一致性稽核

### 為什麼需要獨立的分析步驟？

spec → plan → tasks 是三個獨立的 LLM 呼叫，每個步驟都在上一步的產物基礎上前進，但沒有任何機制確保「tasks.md 覆蓋了 spec.md 的所有驗收條件」，或是「plan.md 的資料欄位在 tasks.md 裡都有對應的任務」。

`/speckit.analyze` 在 tasks 和 implement 之間執行，專門找這類跨產物的缺口。

```
/speckit.analyze
```

範例輸出（追加到 tasks.md 或獨立報告）：

```
ANALYSIS REPORT

✓ All acceptance criteria in spec.md have corresponding tasks

⚠ COVERAGE GAP: spec.md Acceptance Criteria #3 says
  "Unassign also sends notification". Tasks Phase 3 covers
  assignment created but does NOT explicitly handle assignment
  deleted. Recommend adding explicit task.

⚠ CONSISTENCY: plan.md mentions SendGrid template
  "task-assignment-v1" but tasks.md says "task-assigned".
  Align naming before implement.

✓ data-model.md columns all referenced in migration tasks
```

你根據報告補齊 tasks.md，再進入實作。這一步的代價是一個額外的 LLM 呼叫，但它替代的是人工對著三份文件比對的時間。

---

## 第八步：/speckit.implement——驅動 agent 逐任務建構

### implement 的工作方式

```
/speckit.implement
```

Agent 讀取 tasks.md，逐一執行每個未完成的 `- [ ]` 項目。完成後把 `[ ]` 改成 `[x]`，相當於對著工單打勾。整個過程：

1. Agent 讀 tasks.md，找到第一個 `- [ ]`
2. 根據任務描述寫程式碼、執行命令、修改檔案
3. 完成後更新 tasks.md 把 `[ ]` 改 `[x]`
4. 繼續下一個任務

constitution.md 在這裡再次發揮作用：prompt file 在 implement 階段同樣注入憲法，確保產出的程式碼符合全局規範（例如 API 版本化、無障礙屬性）。

### 邊界案例：任務寫得不夠具體會怎樣？

如果 tasks.md 有一條：

```markdown
- [ ] Make the notification system work correctly
```

agent 會自行詮釋「正確」的定義，結果很可能偏離你的預期。這就是為什麼 tasks.md 的粒度必須細到「一個任務 = 一個檔案的一個改動或一個 API endpoint」。這也是 `/speckit.analyze` 值得跑的原因——它會把這種過於寬泛的任務描述標出來。

---

## 第九步（選用）：/speckit.converge——對齊現況與規格

`/speckit.converge` 是給「功能實作到一半，規格又更新了」的場景設計的。它把 spec/plan/tasks 的文字描述與現有程式碼做對比，把尚未完成的差距追加為新的 tasks.md 條目，讓迭代能繼續而不是從頭重來。

這個指令也適用 brownfield 場景：已有大量既存代碼，想逐步引入規格驅動開發時，先跑 `/speckit.converge` 讓規格追上現況，再用 `/speckit.implement` 向前推進。

---

## 產物全景：.specify/ 目錄樹

跑完完整工作流，磁碟上長這樣：

```
.specify/
├── memory/
│   └── constitution.md          ← 全專案準則
└── scripts/
    └── bash/
        ├── check-prerequisites.sh
        ├── common.sh
        ├── create-new-feature.sh
        ├── setup-plan.sh
        └── setup-tasks.sh

specs/
└── 002-task-assignment-notifications/
    ├── spec.md                  ← WHAT + WHY + 澄清
    ├── plan.md                  ← HOW：架構 + 設計決策
    ├── tasks.md                 ← 可執行工單，[P] 標並行
    ├── research.md              ← agent 查找的技術細節
    ├── data-model.md            ← Schema migration 草稿
    ├── quickstart.md            ← 本機啟動步驟
    └── contracts/
        └── api-spec.json        ← API 合約
```

每份文件的職責明確：spec.md 是需求合約，plan.md 是技術決策記錄，tasks.md 是執行工單，其他是輔助資料。它們之間的閱讀方向是單向的：後面的文件讀前面的，不應該反向影響。

---

## 底層機制：指令怎麼真正被執行

理解三層結構，才能在出問題時知道哪裡壞掉。

```
使用者輸入 /speckit.specify
        │
        ▼
1. Prompt file（放在 .github/prompts/ 或 .claude/commands/ 等 agent 目錄）
        │  內含：注入 constitution.md 的指令 + shell script 呼叫 + 模板引用 + LLM 約束
        ▼
2. create-new-feature.sh
   掃描 specs/ 計算下一個編號 → 建 git 分支 → 複製 spec-template.md
        │
        ▼
3. LLM 填入模板 → specs/<branch>/spec.md
```

Prompt file 是純 Markdown 文字，你可以打開 `.github/prompts/speckit-specify.prompt.md` 直接閱讀或客製化約束。Spec Kit 不是黑盒 SaaS，所有機制都在你的 repo 裡。

`check-prerequisites.sh` 是真實存在的腳本（查證日期 2026-06-30），設計意圖是在執行某個階段前驗證前置產物是否存在，但 live spec-driven.md 並未將其描述為嚴格的相位閘（phase gate）；確切行為依版本而異。

`[NEEDS CLARIFICATION: ...]` 是語義標記：告訴所有後續的 LLM 呼叫「這個問題仍未解決」。`/speckit.clarify` 的作用就是把這些標記一一消滅，答案折回 Clarifications 節。spec-driven.md 明確描述模板作為「active constraints」，強迫 LLM 聚焦在 WHAT/WHY 而非 HOW。

> Ch 29 會更深挖模板的 prompt 設計和 shell script 細節。

---

## 各指令比較表

| 指令 | 類型 | 輸入 | 主要產物 | 可省略？ |
|------|------|------|----------|----------|
| `/speckit.constitution` | Core（每專案一次） | 人工提供準則 | constitution.md | 不建議省略——影響所有後續指令 |
| `/speckit.specify` | Core | 功能需求描述 | spec.md（+ 功能分支） | 否 |
| `/speckit.clarify` | Optional | spec.md 的歧義 | spec.md 更新（Clarifications 節） | 小功能可省，模糊需求必跑 |
| `/speckit.plan` | Core | spec.md + 技術棧選擇 | plan.md + 支援文件 | 否 |
| `/speckit.checklist` | Optional | plan.md | 驗收條件品質報告 | 可省 |
| `/speckit.tasks` | Core | plan.md（+ 支援文件） | tasks.md | 否 |
| `/speckit.analyze` | Optional | spec/plan/tasks | 一致性分析報告 | 快速功能可省；複雜功能強烈建議 |
| `/speckit.implement` | Core | tasks.md | 程式碼變更 | 否 |
| `/speckit.converge` | Core（迭代用） | 現有代碼 + spec/plan/tasks | 補充的 tasks.md | 新功能可省；迭代/brownfield 重要 |

---

## 踩雷集錦

### 錯誤直覺 1：「spec.md 寫越詳細越好，把技術棧都寫進去」

**正確認識**：spec.md 的模板刻意不允許技術棧細節。把 PostgreSQL、BullMQ 寫進 spec，會讓 LLM 在 spec 階段就開始做 plan 的事，並且讓 spec.md 在技術決策改變時必須整個重寫。正確做法是把技術棧資訊留到 `/speckit.plan`，作為你給 plan 指令的補充輸入。

### 錯誤直覺 2：「跑了 /speckit.analyze 就不需要自己讀 tasks.md」

**正確認識**：`/speckit.analyze` 找的是跨產物的結構性缺口，例如「驗收條件沒有對應任務」或「plan 提到的欄位沒有出現在任務裡」。但它不會替你判斷任務描述的粒度是否夠細、用語是否精確。你仍然需要人工過一遍 tasks.md，確保每個任務對 agent 來說都是無歧義的原子動作。

### 錯誤直覺 3：「/speckit.implement 一次性把所有任務跑完」

**正確認識**：implement 確實會逐一處理 tasks.md 裡的項目，但它不是自動化腳本，而是 LLM 驅動的執行。每個任務完成後，agent 把 `[ ]` 改成 `[x]`，但它可能因為 context window 限制、模型的非確定性、或任務描述不清而跳過某些任務或漏掉測試。HN 上有實際使用者（yoaviram）回報過「implement 指令沒有照流程建立或執行測試」的問題（查證自 HN item 45610996）。最安全的做法是在 implement 完成後，手動確認每個 `[x]` 對應的程式碼和測試都確實存在。

### 錯誤直覺 4：「指令集是穩定的，不會改名」

**正確認識**：Spec Kit 在十個月內從零迭代到 v0.11.10，發布了 175 次以上。`/speckit.clarify` 的前身是 `/quizme`，原始的 `/specify`, `/plan`, `/tasks` 在後續版本全改成 `/speckit.*` namespace。課程寫作時使用的是 v0.11.10（查證日期 2026-06-30）。**永遠以你安裝版本的 `specify integration list` 和官方 README 命令表為準**。

### 錯誤直覺 5：「spec.md 一旦寫完就不再修改」

**正確認識**：`/speckit.clarify` 的作用就是回頭修改 spec.md 的 Clarifications 節。`/speckit.converge` 在迭代時也可能要求你更新 spec 以反映需求演化。spec.md 是活文件，它應該和代碼一起受版本控制，並且在每次功能迭代時被審閱。

---

## 進階延伸

### 一個功能多個 spec？

spec 以功能（feature）為單位，不是以 user story 為單位。如果一個大功能包含多個 user story，你可以把它們全部放進同一個 `/speckit.specify` 的提示詞，讓 spec.md 包含多個 User Journey。或者把大功能拆成兩個編號不同的 spec，用 `/speckit.specify` 分別建立。

Spec Kit 沒有強制規定——但後者（分開建立）讓每個 spec 的 tasks.md 更小、implement 的範圍更可控，在複雜功能上通常更好。

### 把 tasks 轉成 GitHub Issues

```
/speckit.taskstoissues
```

這個 Core 指令（2025 年發布時不存在，v0.11.x 加入）把 tasks.md 的每個任務建成一個 GitHub Issue，讓 tasks 可以在 GitHub Projects 追蹤。對需要多人協作的團隊，這把 Spec Kit 的任務分解接進既有的 PM 工具。

### Skills 模式 vs 指令模式

`specify init` 時有兩種安裝方式：

- **指令模式**（預設）：把 `/speckit.*` 安裝成 prompt 檔案，例如 `.github/prompts/speckit-specify.prompt.md`（Copilot）或 `.claude/commands/speckit-specify.md`（Claude Code）。在 agent 對話框輸入 `/speckit.specify` 觸發。

- **Skills 模式**（`--integration-options="--skills"`）：安裝成 agent skill，例如 `speckit-specify`，在支援 skills 的 agent（Claude Code、Codex CLI 等）裡用不同方式觸發。Codex CLI 的 skills 模式下，呼叫語法是 `$speckit-specify` 而非 `/speckit.specify`。

兩種模式產出相同的 Markdown 產物，差別只在觸發語法和 agent 整合方式。

> 如果你對 Spec Kit 安裝細節還不熟，先回看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)。

---

## 動手練習

以下練習按複雜度排列：

**練習 28-A（不需安裝）**：選一個你熟悉的小功能（例如讓使用者更換頭像），手動寫出：
1. 一份 spec.md：至少兩個 User Journey、三條驗收條件、一個 `[NEEDS CLARIFICATION]`
2. 回答自己提的 clarification，寫進 Clarifications 節
3. 一份 tasks.md：至少五個任務，至少一個 `[P]` 群組

**練習 28-B（完整工作流）**：在測試 repo 裡依序跑：

```
/speckit.constitution
Focus on: TypeScript strict mode, all async functions must handle errors,
no console.log in production code, prefer functional patterns

/speckit.specify
Add a feature that lets users export their task list as a CSV file,
including task title, status, due date, and assigned user.

/speckit.clarify

/speckit.plan
Use Node.js, Express, PostgreSQL. No new npm packages without justification.

/speckit.tasks

/speckit.analyze
```

驗收：逐一閱讀產出的四個檔案。spec.md 是否完全沒有技術棧？tasks.md 是否有 `[P]`？analyze 找到了什麼缺口？

**練習 28-C（邊界案例）**：刻意輸入模糊需求：

```
/speckit.specify
Make the app faster and more secure.
```

記錄 spec.md 產生了多少 `[NEEDS CLARIFICATION]`，再跑 `/speckit.clarify` 觀察 agent 的提問策略。

---

## 本章重點整理

- `/speckit.constitution` 設定全專案準則，以 constitution.md 形式被所有後續指令注入。每個專案跑一次。
- `/speckit.specify` 產出 spec.md，強制聚焦 WHAT/WHY，自動完成功能編號和分支建立。模板以 `[NEEDS CLARIFICATION]` 標記歧義。
- `/speckit.clarify` 是選用步驟，以覆蓋率為基礎提問，把答案折回 spec.md 的 Clarifications 節。README 明確建議在 plan 之前跑。
- `/speckit.plan` 接收技術棧選擇，產出 plan.md + data-model.md + research.md + quickstart.md + contracts/，對照 constitution.md 做對齊驗證。
- `/speckit.tasks` 從 plan.md 推導出可執行工單 tasks.md，用 `[P]` 標記可並行的任務群組。
- `/speckit.analyze` 在 tasks 和 implement 之間做跨產物一致性稽核，找出覆蓋缺口和命名不一致。
- `/speckit.implement` 逐一執行 tasks.md 的工單，完成後把 `[ ]` 改成 `[x]`。
- `/speckit.converge` 比對現況代碼與規格，把差距追加為新任務，支援迭代和 brownfield 場景。
- 完整工作流的「全序列」是依 README 相對位置推論的合理合成，非 README 逐字列舉（查證日期 2026-06-30）。

---

## 自我檢核

- [ ] 用自己的話解釋：spec.md 和 plan.md 的職責有何不同？如果面試被問「你們的規格文件是什麼」，你會怎麼區分這兩份文件？
- [ ] `[NEEDS CLARIFICATION]` 標記由誰產生、由哪個指令消滅？說明這個機制的設計意圖。
- [ ] tasks.md 裡的 `[P]` 代表什麼？在你自己的練習 28-A 中，哪些任務可以標 `[P]`？
- [ ] `/speckit.analyze` 和 `/speckit.checklist` 都是選用步驟，但針對的問題不同。你能說出各自解決的是哪一類問題？
- [ ] 如果 `/speckit.implement` 跑完後，你發現有幾個 `[x]` 任務其實沒有對應的測試，下一步你會怎麼做？（不是用 Spec Kit 解答，而是你的判斷）
- [ ] 指令名稱從 `/specify` 改成 `/speckit.specify`，背後的設計原因是什麼？這對你在課程外自行學習 Spec Kit 有什麼含義？

---

## 延伸閱讀

**[github/spec-kit — 官方 Repository](https://github.com/github/spec-kit)**
先看 README 的「Available Slash Commands」表格，再看「Detailed Process」walkthrough（Taskify 範例），最後翻 `spec-driven.md` 看模板約束的細節。這是本章所有事實的第一手來源。

**[spec-driven.md（in-repo 深度文件）](https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md)**
解釋模板如何作為主動約束（active constraints），說明 create-new-feature.sh 的自動編號邏輯。讀完這份文件，你對「指令 → shell script → 模板填充」三層架構的理解會從概念變成具體。

**[Spec-driven development with AI: Get started with a new open source toolkit — GitHub Blog（Den Delimarsky，2025-09-02）](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/)**
官方發布文，包含「literal-minded pair programmers」核心比喻的原始脈絡，以及 2025 年最初的四步工作流（`/specify` → `/plan` → `/tasks` → Implement）。對比現在的 `/speckit.*` 命名空間，可以看到工具在功能和設計上的演化軌跡。

**[github/spec-kit Releases](https://github.com/github/spec-kit/releases)**
175 次以上的發布記錄（截至 2026-06-29）。追蹤 `/quizme` → `/speckit.clarify` 這類改名事件，了解指令集如何演化，是讓你的 Spec Kit 知識保持最新的最有效方法。

**[Waterfall Strikes Back — Marmelab blog（François Zaninotto，2025-11-12）](https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html)**
Marmelab CEO 的批評性實測。他發現在一個「display current date」的小功能上，Spec Kit 產出了 8 個 Markdown 檔案、1300 行規格文字——「Tony Stark building a robot from scratch in a cave when just screw this bolt on would have sufficed」。值得讀，用來校準你對「什麼功能適合跑完整工作流」的判斷。與 [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md) 對照閱讀效果更好。

**[Good Spec — Addy Osmani（Google，2026-01-13）](https://addyosmani.com/blog/good-spec/)**
Google 工程師從另一個角度談規格的品質指標，包括「curse of instructions」（指令越堆越多，模型遵從率越低）和「不要過度規格化瑣碎任務」的建議。這是本章「什麼時候 /speckit.clarify 和 /speckit.analyze 可以省略」判斷的補充理論基礎。

---

走完這條工作流，你對每個指令的輸入/輸出和設計意圖應該已經有具體的心智模型。下一章我們往底下挖一層：這些指令是怎麼真正被執行的？模板的約束機制在程式碼層面長什麼樣？

→ [Ch 29 GitHub Spec Kit（三）：底層怎麼運作](./29-spec-kit-internals.md)
