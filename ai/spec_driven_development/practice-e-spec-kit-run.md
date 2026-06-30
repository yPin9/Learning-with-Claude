# 練習 E — 用 Spec Kit 把練習 D 的 spec 跑成可動小功能

> **目標**：安裝 Spec Kit、把練習 D 的 spec 餵進去跑完整流程，親手記錄哪裡好用、哪裡 over-engineer、哪裡一定要人工修。
>
> **環境**：GitHub Spec Kit v0.11.x、Python 3.11+、uv（或 pipx）、Git、任一支援 `/speckit.*` 的 AI coding agent（Claude Code、GitHub Copilot、Gemini CLI 等）。指令集以 2026-06-30 確認的 README 為準；版本迭代快，執行前先跑 `specify self check`。（查證日期 2026-06-30）

## 背景與動機

「寫好 spec 有什麼用？」這是練習 D 結束後最正常的問題。光把規格寫進 Markdown 文件，然後自己複製貼上給 AI，和「直接把需求貼給 AI」沒有本質差別——少了 pipeline 的紀律，spec 只是更正式的提示詞而已。

Spec Kit 要解決的正是這個問題：把「spec 是第一公民」這件事從個人紀律轉成工具強制。它不讓你直接跑 `/speckit.implement`；在 spec 還沒確認、plan 還沒產出之前，腳本會拒絕往下走。這是「前置條件門禁（phase gate）」的具體落地。

> 如果你還沒寫完練習 D 的 spec，先回看 [練習 D — 把需求＋領域模型寫成一份完整的 spec](./practice-d-write-a-spec.md)。沒有原料就沒有這道菜。

本練習要你把那份 spec 實際跑過 `/speckit.specify → /speckit.clarify → /speckit.plan → /speckit.tasks → /speckit.analyze → /speckit.implement` 全流程，然後回答三件事：

1. 工具在哪些地方幫你消了認知負擔？
2. 工具在哪些地方 over-engineer，讓你花的時間比直接寫還多？
3. 哪些地方需要人工修，自動化擋不住？

這三個答案，比「能跑起來」更重要。

---

## 心智圖像：五層漏斗

```
   需求意圖（你的文字）
         ↓
   [/speckit.specify]   → spec.md（WHAT / WHY，不含 HOW）
         ↓
   [/speckit.clarify]   → 釐清模糊點，補進 spec.md
         ↓
   [/speckit.plan]      → plan.md + research.md + data-model.md + contracts/
         ↓
   [/speckit.tasks]     → tasks.md（[P] 標記可平行任務）
         ↓
   [/speckit.implement] → 實際程式碼
```

每層只有在上層的產物存在時才能執行。`check-prerequisites.sh` 是腳本層的守門員；它不是 README 正式記錄的「相位門」，而是被 scaffolded 進去的防呆邏輯，可能隨版本調整——這是你需要親眼看 `.specify/scripts/bash/` 目錄的原因。

`/speckit.*` 指令不是「真的指令」，而是 agent 的 prompt 檔案（slash command prompt files）被注入進去的。`specify init` 做的事，是把 `.specify/templates/` 裡的 spec-template.md、plan-template.md、tasks-template.md 複製進去，再把對應的 prompt 檔案寫進 agent 目錄（例如 Claude Code 的 `.claude/commands/`）。當你在 agent 對話框輸入 `/speckit.specify`，agent 讀的其實是那個 prompt 檔案，然後以 template 作為輸出格式的約束。

這個架構解釋了一個重要的限制：**工具能約束輸出格式，但不能約束輸出品質**。如果你給 `/speckit.specify` 的輸入本來就模糊，它會產出格式正確但內容模糊的 spec.md。垃圾進，格式正確的垃圾出。

> 如果你想深入這個機制，看 [Ch 29 — GitHub Spec Kit（三）：底層怎麼運作](./29-spec-kit-internals.md)。

---

## 任務規格

### 精確輸入

你需要：

1. **練習 D 產出的 spec**（Markdown 或純文字均可）。規格至少要包含：
   - 功能名稱與一句話定位
   - User Story（至少兩條，Given-When-Then 格式）
   - 非功能需求（至少一條，如效能或安全）
   - 邊界條件（至少兩條）
2. **一個乾淨的 Git repo**（可以是空的，也可以是現有小專案）。

### 精確輸出（你需要繳交的產物）

| 產物 | 路徑 | 說明 |
|---|---|---|
| `.specify/memory/constitution.md` | 專案根目錄下 | 用 `/speckit.constitution` 產出的專案原則 |
| `specs/001-<feature>/spec.md` | 由工具自動命名 | `/speckit.specify` 產出的功能規格 |
| `specs/001-<feature>/plan.md` | 同上 | `/speckit.plan` 產出的技術計畫 |
| `specs/001-<feature>/tasks.md` | 同上 | `/speckit.tasks` 產出的任務清單 |
| 可執行的小功能 | 你的 repo | `/speckit.implement` 跑完後，至少有一個能執行的測試或主程式 |
| `reflection.md`（你手寫） | 專案根目錄 | 三欄筆記：「哪裡省力 / 哪裡 over-engineer / 哪裡要人修」 |

### 什麼是「好的」中間產物

你不需要完美的 spec.md，但下面這些是最低標準：

**spec.md 的最低標準：**
- 至少兩條 Given-When-Then 格式的 acceptance criteria
- 至少一個 `[NEEDS CLARIFICATION]` 被 `/speckit.clarify` 回答後填入（如果完全沒有，表示你的輸入太模糊或太完整，要反思哪個）
- Out of Scope 區塊至少一條（明確說「不做什麼」和說「做什麼」同樣重要）

**tasks.md 的最低標準：**
- 任務粒度：每個任務不超過「一個函式或一個測試檔」的工作量
- 至少一個 `[P]` 標記（如果真的全部線性，回去重新分析 plan.md）
- 每個任務有明確的完成條件（「實作 X 函式」而不是「完成 X 功能」）

**不是好的 tasks.md 的例子：**
```markdown
- [ ] 實作整個訂閱到期提醒系統
```
這個任務沒有可驗證的完成條件，agent 可以把任何東西標記為「完成」。

### 限制

- **不能**手動複製貼上自己的 spec 文字去假裝是工具產出。必須透過 `/speckit.specify` 流程走完。
- **不能**跳過 `/speckit.plan` 直接跑 `/speckit.implement`（工具本身會擋，但如果你繞過去，練習就沒有意義）。
- **可以**在任何一步插入人工編輯——這正是我們想觀察的。每次編輯請記下「我改了什麼、為什麼工具沒有自動修好」。

### 驗收條件

- `git log` 能看到至少三個 commit，對應 spec / plan / implement 三個階段。
- `specs/001-<feature>/tasks.md` 裡有至少一個標記 `[P]` 的平行任務（如果沒有，說明你的任務設計太線性，回去改）。
- `reflection.md` 每欄至少兩條具體觀察，不接受「很好用」或「有點慢」這種沒有根據的評語。

---

## 期望輸出範例

假設練習 D 的 spec 是一個「訂閱到期提醒」功能（每天掃使用者訂閱，對即將到期的發 email）。

### `/speckit.specify` 跑完後 `spec.md` 應該長什麼樣

```markdown
# Feature: Subscription Expiry Reminder

## Overview
Send automated email reminders to users whose subscriptions expire within
configurable lead times (e.g. 7 days, 1 day).

## User Stories

### Story 1: Receive advance notice
GIVEN a user whose subscription expires in 7 days
WHEN the daily reminder job runs
THEN the system sends one email to the user's registered address
  AND the email includes the exact expiry date and a renewal link

### Story 2: Receive final warning
GIVEN a user whose subscription expires in 1 day
WHEN the daily reminder job runs
THEN the system sends one final-notice email distinct from the 7-day notice

## Non-Functional Requirements
- The reminder job MUST complete within 5 minutes for up to 10,000 users.

## Out of Scope
- SMS / push notifications
- Real-time subscription changes (covered in a separate spec)

## [NEEDS CLARIFICATION: ...]
- What happens if a user has multiple overlapping subscriptions?
- Is there an opt-out mechanism required?
```

注意 `[NEEDS CLARIFICATION: ...]` 標記——這是 Spec Kit 的 spec-template.md 強制插入的佔位符，用來逼你正視還沒答的問題。`/speckit.clarify` 的工作就是把這些問題一個一個問完，再把答案填進去。

### `/speckit.tasks` 跑完後 tasks.md 片段

```markdown
## Tasks

- [ ] [P] Set up database query for expiring subscriptions (T1)
- [ ] [P] Implement email template for 7-day notice (T2)
- [ ] [P] Implement email template for 1-day notice (T3)
- [ ] Send 7-day reminder (depends on T1, T2)
- [ ] Send 1-day reminder (depends on T1, T3)
- [ ] Write integration test: daily job processes 100 users in < 30s
- [ ] Write unit test: user with multiple subscriptions receives one email per subscription
```

`[P]` 表示 T1、T2、T3 可以平行跑；後面的任務因為有依賴關係，必須等前置完成。

---

## 如果你卡住了

1. **`specify init` 跑完什麼都沒出現**：確認你在 Git repo 根目錄，且指定了 `--integration`。跑 `specify integration list` 確認你用的 agent 名稱是對的（例如 `claude` 不是 `claude-code`）。

2. **`/speckit.specify` 叫我輸入 user journey 但我已經有 spec 了**：把練習 D 的 spec 內容貼進 agent 的對話框，作為 user journey 的輸入。工具不是從檔案讀；它從你在對話框告訴 agent 的東西出發，然後把結果寫進 `specs/` 目錄。

3. **`/speckit.plan` 一直在問我選哪個技術棧**：這是正常行為，plan 階段需要你做技術選擇。不想選就回答「用最簡單的 Python + stdlib，不依賴任何框架」，然後看它怎麼設計。

4. **tasks.md 沒有任何 `[P]` 標記**：你的任務可能全部線性相依，或者工具漏掉了。回去看 plan.md，找出哪些子模組之間真的沒有依賴，手動在 tasks.md 裡加 `[P]`，然後再跑 `/speckit.implement`。

5. **`/speckit.implement` 把整個 repo 大翻新**：你遇到了 HN 用戶 hatmanstack 在 Kiro 上遇到的問題的 Spec Kit 版本——agent 過度解讀任務範圍。建議用 Supervised 模式（如果你的 agent 支援）或把 tasks.md 拆得更細，每次只跑一到兩個任務。

> 如果你對 Autopilot vs Supervised 的概念還不熟，可以看 [Ch 30 — AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md) 裡的執行模式說明——雖然是 Kiro 的名詞，概念是通的。

---

## 實作步驟建議

### Step 1：安裝與初始化（約 10 分鐘）

確認環境：

```bash
python --version   # 需要 3.11+
uv --version       # 或 pipx --version
git --version
```

安裝（把 `vX.Y.Z` 換成 [最新 tag](https://github.com/github/spec-kit/releases)）：

```bash
uv tool install specify-cli \
  --from git+https://github.com/github/spec-kit.git@vX.Y.Z
specify --version
```

初始化專案（以 Claude Code 為例）：

```bash
cd your-project
specify init . --integration claude
```

Windows 用戶加 `--script ps` 讓腳本產出 PowerShell 版本。`specify integration list` 會印出你安裝的這個版本支援哪些 agent；如果你用的 agent 不在清單裡，先確認 integration key 的拼法（例如是 `copilot` 不是 `github-copilot`）。

安裝後的完整 `.specify/` 目錄結構：

```
.specify/
  memory/
    constitution.md       ← /speckit.constitution 寫到這裡
  scripts/
    bash/
      check-prerequisites.sh
      common.sh
      create-new-feature.sh
      setup-plan.sh
      setup-tasks.sh
    ps/                   ← --script ps 時產出 PowerShell 版本
  templates/
    spec-template.md
    plan-template.md
    tasks-template.md
    CLAUDE-template.md    ← Claude Code 專用
specs/                    ← 每個 feature 一個子目錄，由工具建立
  001-your-feature/
    spec.md
    plan.md
    tasks.md
    research.md
    data-model.md
    contracts/
      api-spec.json       ← 如果 plan 判斷需要的話
```

`create-new-feature.sh` 是自動掃現有 `specs/` 目錄、選下一個未用的三位數編號、然後建立語義化 branch 的腳本。這個自動編號機制讓你同時開多個 feature 也不會衝突。

### Step 2：寫好 constitution，跑 specify（約 20 分鐘）

在 agent 對話框裡跑：

```
/speckit.constitution 這個專案優先程式碼可讀性、完整測試覆蓋、
最小外部依賴。新功能一律有 unit test，不允許硬寫 magic constant。
```

然後把練習 D 的 spec 整理成「user journey + 邊界條件」的格式，在 agent 裡觸發：

```
/speckit.specify
```

看它問什麼，把你的 spec 內容作為輸入回答。

### Step 3：clarify + plan（約 30 分鐘）

跑 `/speckit.clarify`，針對 `[NEEDS CLARIFICATION]` 標記的問題一一回答。這步是工具真正幫你挖漏洞的地方，認真對待。

確認 spec.md 更新後，跑：

```
/speckit.plan
```

檢查產出的 `plan.md`：架構選擇合不合理？`data-model.md` 裡的欄位和你在練習 D 裡定義的領域模型一致嗎？不一致就手動修，並在 `reflection.md` 記一筆。

### Step 4：tasks + analyze（約 15 分鐘）

```
/speckit.tasks
```

檢查 `tasks.md`，確認有 `[P]` 標記。然後跑可選的分析步驟：

```
/speckit.analyze
```

它會比對 spec / plan / tasks 之間有沒有覆蓋缺口。如果報告說某個 acceptance criteria 沒有對應任務，就補上。

### Step 5：implement + 人工檢查（約 45 分鐘）

```
/speckit.implement
```

跑完後，做三件事：

1. 跑測試（如果工具沒有自動產生測試，這是第一個要記進 `reflection.md` 的觀察）。
2. 對照 `spec.md` 裡的每一條 Given-When-Then，手動驗一遍。
3. 寫 `reflection.md`。

---

## 完整參考解答

**寫完再看。** 你的 `reflection.md` 是唯一真正的「解答」，因為它記錄的是你的觀察，別人替代不了。但如果你想對照一個範本：

<details>
<summary>點開參考 reflection.md</summary>

```markdown
# Spec Kit 跑程紀錄 — 訂閱到期提醒功能
日期：2026-xx-xx  
Spec Kit 版本：v0.11.10  
Agent：Claude Code  

## 哪裡省力

1. `/speckit.clarify` 問出了「多訂閱使用者收幾封 email」這個我在練習 D 裡沒有想清楚的問題。
   如果直接實作，大概在 code review 才會被發現。
2. `tasks.md` 自動標出 T1/T2/T3 可以平行，我自己規劃的時候習慣寫成線性清單，
   沒想到資料庫查詢和 email template 根本不需要互相等待。
3. `plan.md` 裡的 `data-model.md` 把我在練習 D 裡用文字描述的 `Subscription` entity
   轉成了欄位表，省了我 15 分鐘的 schema draft。

## 哪裡 over-engineer

1. `/speckit.plan` 幫我產了 `api-spec.json`，但這個功能根本沒有對外 API，全是 internal job。
   多了一份空的 OpenAPI 文件讓人困惑，最後刪掉了。
2. `research.md` 裡洋洋灑灑列了五個 email 傳送服務（SendGrid、SES、Mailgun、Postmark、SMTP），
   但 constitution 裡我已經說了「最小外部依賴」。工具沒有讀到這個限制，最後人工刪成只留 SMTP。
3. tasks.md 有一個任務是「設計 retry exponential backoff 機制」，這是日後的需求，
   spec.md 裡根本沒提到。over-engineering 的典型案例。

## 哪裡要人工修

1. `/speckit.implement` 跑完後的測試用 mock SMTP server，但 mock 的 port 號是 magic constant 2525，
   沒有放進設定檔。需要人工把它提出來。
2. 實作的 SQL 查詢用了 DATEDIFF，但 spec.md 裡說的是「即將到期」而不是精確的天數計算，
   DATEDIFF 在跨時區場景會差一天。需要人工加 timezone normalization。
3. 工具沒有產生任何 integration test，只有 unit test。
   spec.md 的非功能需求說「5 分鐘內完成 10,000 名使用者」，沒有自動驗證這個條件。
```

這份 reflection 展示的模式是：工具擅長「把模糊說清楚」和「把平行關係可視化」，但它不讀 constitution 的每一條原則，也不懂「什麼時候不要產東西」。

</details>

<details>
<summary>點開範例 constitution.md（供對照）</summary>

```markdown
# Project Constitution

## Core Principles

1. **Code clarity over cleverness**: prefer readable loops over one-liners;
   avoid nested ternaries.
2. **Test everything**: every new function must have at least one unit test
   in the same PR.
3. **Minimal external dependencies**: use stdlib first; add a dependency only
   if the implementation would take more than 2 hours to write correctly.
4. **No magic constants**: all configuration values in a config file or env var.
5. **Timezone awareness**: all date comparisons normalize to UTC before comparing.

## Scope Boundaries

This project is a backend job runner. It does not expose a public API.
Do not generate API specs or HTTP handler scaffolding.
```

注意最後兩行。就算寫了「不要生成 API spec」，`/speckit.plan` 還是可能生成——所以 constitution 不是萬能的，你仍然要在 plan 階段人工審查。

</details>

---

## 觀察記錄框架

跑完每個步驟後，立刻在 `reflection.md` 填一行。不要等到全部跑完才回想，因為你會忘掉細節：

| 步驟 | 花了多少時間 | 產出的文件行數 | 我改了什麼 | 為什麼工具沒修好 |
|---|---|---|---|---|
| `/speckit.constitution` | | | | |
| `/speckit.specify` | | | | |
| `/speckit.clarify` | | | | |
| `/speckit.plan` | | | | |
| `/speckit.tasks` | | | | |
| `/speckit.analyze` | | | | |
| `/speckit.implement` | | | | |
| 人工驗收 | | | | |

「花了多少時間」包含你等 agent 跑完的時間加上你自己審查的時間。Colin Eberhardt 在 Scott Logic 的測試裡把兩者分開計時（agent time vs review time），你也可以這樣記，對比會更清楚。

---

## 測試用例表

用下面這些場景驗證你的實作是否正確，也驗證 spec.md 的覆蓋是否完整：

| 情境 | 輸入狀態 | 期望行為 | 你的實作有沒有處理 |
|---|---|---|---|
| 正常 7 天提醒 | 使用者 A 訂閱到期日 = 今日 + 7 天 | 收到一封 7-day notice email | |
| 正常 1 天提醒 | 使用者 B 訂閱到期日 = 今日 + 1 天 | 收到一封 final notice email | |
| 同天到期 | 使用者 C 訂閱到期日 = 今日 | 不在提醒範圍，不收信 | |
| 已過期 | 使用者 D 訂閱到期日 = 昨天 | 不在提醒範圍，不收信 | |
| 多訂閱使用者 | 使用者 E 有 2 份訂閱，分別 7 天和 30 天後到期 | 只收 7-day 那份的提醒，30 天那份不在範圍 | |
| 重複執行 | 同一天跑兩次 job | 使用者不收到兩封相同的提醒（冪等性）| |
| 空使用者集 | 當天無人到期 | job 正常結束，不報錯 | |
| 10,000 名使用者 | 效能邊界測試 | 完成時間 < 5 分鐘 | |

最後一欄留白讓你自己填。如果有任何一格是「沒有處理」，你有兩個選擇：補進 spec.md 然後跑 `/speckit.converge`，或者手動寫那個案例的實作和測試。

> 如果你對「冪等性」在訂閱通知場景的意涵還不熟，可以回看 [Ch 19 — 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md)，`Aggregate` 的設計正是防止重複副作用的第一道防線。

跑完測試用例表後，回頭看你的 spec.md：「重複執行冪等性」這個場景有沒有在某個 Given-When-Then 裡出現？如果沒有，說明你在練習 D 的 spec 裡遺漏了一條非功能需求。這是 Spec Kit 能幫你發現的問題，也是它做不到的問題——工具能保證格式，但不能替你想清楚業務邊界。

---

## 踩雷集錦

**錯誤直覺 1：constitution 寫了就不用審 plan**

正確認識：constitution 是對 agent 的「原則性提示」，不是編譯期約束。`/speckit.plan` 可能生出 constitution 明確禁止的東西（例如你說「不要用外部套件」，它還是可能推薦 Celery）。plan 階段一定要人工審查，把不符合的部分刪掉或改掉，才跑 tasks。

---

**錯誤直覺 2：`[NEEDS CLARIFICATION]` 標記被 `/speckit.clarify` 問完就消失了**

正確認識：clarify 的工作是在 spec.md 的對應區塊填入答案，但標記本身是由 spec-template.md 定義的佔位符——某些情況下它不會被自動刪除，只是下面多了你的答案。確認 spec.md 裡的每個 `[NEEDS CLARIFICATION]` 都已被回答，然後手動清掉殘留的標記，否則 plan 階段 agent 可能把它當成未解決的問題繞回來問你。

---

**錯誤直覺 3：tasks.md 裡的任務就是要全部平行跑**

正確認識：`[P]` 只是標記「這個任務和其他 `[P]` 任務之間沒有依賴，可以安全平行執行」，不代表你的 CI 或開發環境會自動平行跑。`/speckit.implement` 的執行順序仍然由 agent 決定；`[P]` 是給人看的文件，也是讓 agent 知道「不用等前面那個跑完」的提示。

---

**錯誤直覺 4：spec 越詳細，agent 實作越準確**

正確認識：Addy Osmani（Google，2026-01-13）記錄了「指令詛咒（curse of instructions）」：當 spec 堆越來越多規則，agent 對每一條的遵守率反而下降。Colin Eberhardt（Scott Logic，2025-11-26）的測試也顯示，2,000+ 行的規劃文件沒有帶來更好的程式碼品質，bug 照樣出現。spec 的目標是「消除模糊性」，不是「列出所有你想得到的規則」。

---

**錯誤直覺 5：跑完 `/speckit.implement` 等於功能完成**

正確認識：實作指令驅動的是任務清單的完成，但 spec.md 裡的驗收條件未必被自動驗證。HN 用戶 yoaviram 報告（item 45610996）implement 指令「跑完了但沒有寫或執行測試」。Zaninotto（Marmelab，2025-11-12）也觀察到 agent 把「verify implementation」任務標記為完成，卻沒有實際寫任何測試。每次 implement 結束後，你要手動對照 acceptance criteria 驗收，而不是看任務清單全打勾就當結束。

---

## 延伸挑戰

完成基本流程後，可以挑戰：

1. **跑 `/speckit.converge`**：在 implement 之後故意加一個新的邊界條件到 spec.md，然後跑 `/speckit.converge`，看它如何把差距分析成新的任務追加進 tasks.md。

2. **換一個 agent 跑同一份 spec**：如果你用 Claude Code 跑了第一遍，改用 GitHub Copilot 或 Gemini CLI 跑同一份 `specs/001-<feature>/`（從 plan 步驟開始），比較兩個 agent 產出的程式碼在測試覆蓋和架構選擇上有什麼差異。非確定性（non-determinism）是 Böckeler 觀察到的 Tessl 問題，在 Spec Kit 上你會觀察到多少？

3. **試試看 `/speckit.taskstoissues`**：如果你有 GitHub repo，把 tasks.md 轉成 GitHub Issues。記錄這個指令的實際行為和你預期的差異。

4. **故意給一份爛的 spec**：把練習 D 的 spec 把驗收條件全刪掉，只留功能名稱和一句話描述，跑 `/speckit.clarify`，看工具挖出多少你沒想到的問題。這是測試工具「發現模糊」能力的壓力測試。

---

## 自我檢核

完成本練習後，你應該能回答：

- [ ] `[NEEDS CLARIFICATION]` 標記從哪裡來？它在 spec 流程裡扮演什麼角色？用自己的話解釋給沒用過 Spec Kit 的人聽。
- [ ] `/speckit.constitution` 和 `/speckit.specify` 的順序為什麼重要？如果先跑 specify 再跑 constitution，哪裡會出問題？
- [ ] 你的 `reflection.md` 裡有沒有「哪裡要人工修」的案例是 spec 設計問題造成的（而不是工具能力問題）？如果有，下次你會怎麼在 spec 撰寫階段就預防它？
- [ ] `[P]` 標記告訴 agent 什麼事？它和實際的並行執行有什麼關係？
- [ ] Colin Eberhardt 的測試發現 Spec Kit 比直接迭代提示慢約 10 倍（單一功能場景，2025 年 11 月數據）。你自己的體驗是什麼？如果你也更慢，它帶來的規格產物有沒有讓你覺得值得？
- [ ] `specify init` 實際上在你的 repo 裡新增了哪些檔案？各有什麼用途？如果你刪掉 `.specify/templates/spec-template.md`，下一次跑 `/speckit.specify` 會發生什麼事？
- [ ] 「spec-first」和「spec-as-source」有什麼不同？根據你這次跑的流程，Spec Kit 屬於哪一類？（提示：跑完 implement 後，如果你直接改了程式碼但沒改 spec.md，Spec Kit 會怎麼做？）

---

## 延伸閱讀

- **[github/spec-kit 官方 README](https://github.com/github/spec-kit)**：指令表（Core + Optional）、安裝指令、scaffolded 產物目錄樹。版本迭代快，課程裡寫的指令名稱要和這份 README 的當前版本核對。從「Get Started」和「Available Slash Commands」區塊開始。

- **[spec-driven.md（repo 內文件）](https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md)**：解釋 templates 如何作為 LLM 約束、`/speckit.specify` 怎麼自動編號並建立 branch。這是「底層機制」的一手資料，和 [Ch 29 — Spec Kit 底層怎麼運作](./29-spec-kit-internals.md) 配合讀。

- **[Spec Kit Releases](https://github.com/github/spec-kit/releases)**：查最新 tag（指令名稱歷史上改過，例如 `/quizme` 改成 `/speckit.clarify`）。在跑 install 前先確認你要 pin 的版本號。

- **[Putting Spec Kit Through Its Paces（Scott Logic，Colin Eberhardt，2025-11-26）](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)**：迄今最詳細的 Spec Kit 端到端複現，有每個階段的逐行計時和行數。閱讀「Plan」和「Implementation」段落，看他找到的 bug 在 spec 的哪個位置被漏掉了。和你自己的 reflection.md 對照，找異同。

- **[Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl（Birgitta Böckeler, martinfowler.com，2025-10-15）](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**：三大 SDD 工具的中立評測，定義了 spec-first / spec-anchored / spec-as-source 三層分類。閱讀「false sense of control」段落，它解釋了為什麼你在本練習裡看到的某些問題不是 bug，而是工具定位本身的取捨。

- **[How to write a good spec for AI agents（Addy Osmani，2026-01-13）](https://addyosmani.com/blog/good-spec/)**：解釋「指令詛咒」——為什麼堆更多規則反而讓 agent 的遵守率下降。讀「curse of instructions」和「adjust spec detail to task complexity」兩節，幫你校準 spec 的粒度。

→ [Ch 33 一個問題，兩個時代：DDD 與 SDD 是同一場仗](./33-ddd-sdd-same-fight.md)
