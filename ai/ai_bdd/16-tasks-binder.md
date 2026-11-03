# Ch 16 — tasks 綁定器：把多份分析文件綁進 tasks.md

> **目標**：說得出 Spec Kit 1.2.5 的 tasks 格式能追溯什麼、不能追溯什麼：`[US1]` 只指回使用者故事，決策、端點、情境都沒有位置。為 tasks.md 定一套引用慣例，寫出 `task_coverage.py` 雙向檢查「每個來源都有 task、每個 task 都有來源、每個引用都指得到東西」，再加上「依使用者故事切 phase」與「測試寫在實作之前」兩條結構規則；把它包成 `tasks-binder` skill，和原版 `/speckit-tasks` 在同一組輸入上各跑一次，看兩者差在哪裡，以及腳本漏了什麼。

> **環境**：Python 3.12.15 與 [Ch 0](./00-environment-setup.md) 的課程 venv、Spec Kit 1.2.5（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`；本章只用 init 裝好的 skill、模板與 `setup-tasks.sh`）、Claude Code 2.1.295（claude-opus-5-5）、macOS 26.6 arm64。lab 在 `/tmp/lab/aibdd/ch16-tasks-binder/`，指令都在其中的 `project/` 執行、venv 已 activate，改壞的變體放在與 `project/` 同層的 `bad/`。腳本輸出是 2026-11-02 實際跑出來的；三段 agent 紀錄來自真實的 `claude -p`，每次結果不同。

## 為什麼需要這個？

[Ch 15](./15-research-and-analysis-skills.md) 交出兩份帶 ID 的分析文件：research.md 的 `DEC-01`–`DEC-03`，inventory.md 的五個 `EP:` 端點。加上 spec.md 的 `FR-001`–`FR-006` 與 feature 檔的四個情境標籤，001-booking 一共有 18 件必須有人去做的事。agent 實作時照著走的是 tasks.md，沒變成 task 的事就不會被做。

原版 `/speckit-tasks` 對這 18 件事的照顧，寫在 1.2.5 的檔案裡（下一節逐行看）：`setup-tasks.sh` 告訴 agent 的設計文件只有 research.md、data-model.md、contracts/、quickstart.md 四種，inventory.md 不在其中；生成流程對 research.md 只有一句指示：「Extract decisions for setup tasks」；task 格式裡能追溯的只有 `[Story]`；`## Done When` 問的是產出了沒、hook 處理了沒、回報了沒，沒有一條問「每個需求都有 task 嗎」。覆蓋（coverage）在 Spec Kit 裡歸 `/speckit-analyze` 管。它是唯讀的 LLM 稽核，用「inference by keyword / explicit reference patterns like IDs or key phrases」把 task 對到需求，讀 spec.md、plan.md、tasks.md 與 constitution，不讀 research.md 和 inventory.md。關鍵字比對分不出「做了」和「提到了」；模型也許讀得出差別，但那要靠它每次都讀對，沒有一支會失敗的檢查。

repo 的既有教材各做了一半。[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)「第六步」的範例用一句話指回來源（「See data-model.md for exact types」），但 phase 照技術層切（Database → API Layer → Notification Worker → Integration → Tests）、測試排最後、沒有 task ID，「第七步」再把覆蓋交給 analyze。[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)「環節三：TASKS 拆分」的 `validate_task_dag` 拒絕沒有 `acceptance_criterion_ids` 的 task，保證每個 task 有出處，卻不保證每個驗收條件都有 task：它只查一個方向。[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)「每個 PR 的九個子區塊」用 `check-plan.mjs` 檢查計畫的形狀，不檢查計畫和需求對不對得上。

這個問題比 agent 老得多。需求追溯（requirements traceability）在 1990 年代已是需求工程的研究題目：Gotel 與 Finkelstein 1994 年研究了一百多位從業者，把追溯分成 pre-RS（需求寫進規格之前從哪來）與 post-RS（寫進規格之後往設計、程式、測試去）兩段。本章做的是 post-RS 裡的一小截，從規格與分析文件到 tasks。工具是一套引用慣例加一支腳本：腳本回答「接上了沒」，人回答「接得對不對」。

## 先建立直覺：工單上的圖號

工地主任把施工圖拆成工單，每張工單寫明依據：「依 S-201 詳圖 3 施作」。監造驗收從兩頭查：拿著圖逐一找工單，沒有工單的詳圖是漏做；拿著工單逐張找圖號，沒寫圖號的工單是沒人要求的工作；寫了 S-210、圖冊裡卻沒有這張，是抄錯。

```
 來源：有 ID 的東西                     tasks.md：描述之前先寫依據
 spec.md          FR-001 … FR-006  ─┐
 research.md      DEC-01 … DEC-03   │   - [ ] T008 [US1] [FR-001] [DEC-03] [EP:POST /bookings] Add POST …
 inventory.md     EP:POST /bookings ├─▶ - [ ] T012 [US2] [@sc_book_02] [@sc_book_03] Write the steps …
 booking.feature  @sc_book_01 … 04  │   - [ ] T013 [US2] [FR-002] [DEC-02] Check overlap and insert …
 data-model.md    沒有 ID：#標題   ─┘
 三種檢查：來源沒有 task＝漏做（TC-06）；task 沒有來源＝孤兒（TC-04）；引用指不到東西＝斷線（TC-05）
```

這個類比有兩處不成立。施工圖天生有圖號，我們的文件得先發 ID：[Ch 15](./15-research-and-analysis-skills.md) 讓問題帶 `[DEC-NN]`（「一、planner 的合約」）、端點寫成 `EP:<METHOD> <path>`（「三、system-analysis」），合併進 plan.md 時 ID 也留著，本章才有東西可引用。一張工單通常只依據一張圖，一個 task 卻常同時實作三、四個來源。類比在另一處完全成立：監造查得出沒有工單的詳圖，查不出照著錯的詳圖施工的工單，腳本也一樣。Spec Kit 1.2.5 把指令裝成 `.claude/skills/speckit-*/SKILL.md`、用 `/speckit-tasks` 呼叫，見 [Ch 12](./12-spec-kit-1x.md)。

## 一、起點，以及原版 tasks 看得到什麼

lab 是 Ch 12 那份乾淨的 1.2.5 init，加上 Ch 15 lab（`ch15-planners/`）的三個 skill、constitution、spikes 與 001-booking 的產物，plan.md 取 Ch 15「四、」手動合併的那份（存在 lab 的 `runs/r3-merged/`）；feature 檔取[練習 A](./practice-a-first-loop.md) 的四個情境，每個 Scenario 前補一行標籤，Rule 名稱加上 R1、R2：

```bash
mkdir -p /tmp/lab/aibdd/ch16-tasks-binder && cd /tmp/lab/aibdd/ch16-tasks-binder && git clone -q /tmp/lab/aibdd/ch12-speckit/booking project && cd project
C=/tmp/lab/aibdd/ch15-planners/project; cp -R $C/.claude/skills/{technical-research,system-analysis,plan-feature} .claude/skills/
cp $C/.specify/memory/constitution.md .specify/memory/ && cp -R $C/specs $C/spikes . && cp $C/../runs/r3-merged/specs/001-booking/plan.md specs/001-booking/
mkdir features && sed -e 's/Rule: A room cannot/Rule: R1 A room cannot/' -e 's/Rule: A booking must end after it starts/Rule: R2 End must be after start/' \
  /tmp/lab/aibdd/practice-a-first-loop/booking/features/booking.feature | awk '/Scenario:/ { printf "    @sc_book_%02d\n", ++n } { print }' > features/booking.feature
printf '{\n  "feature_directory": "specs/001-booking"\n}\n' > .specify/feature.json
```

Ch 15 的正文沒有全文列出 001 的 spec.md 與 research.md，只照正文重建 Ch 15 的人，這兩個檔會和 lab 不同。本章的腳本只讀幾種行，這幾行對上，後面的計數、行號與每一行問題就相同（`sources` 印出的 FR 摘要文字除外）：spec.md 第 7、10 行是 `### User Story 1 - Book a free room (Priority: P1)` 與 `### User Story 2 - See who holds a room (Priority: P2)`，第 17–22 行是 `- **FR-001**:` 到 `- **FR-006**:`；research.md 第 5、18、31 行是 `## DEC-01 Storage that survives a restart`、`## DEC-02 R1 under concurrent requests`、`## DEC-03 Time format at the API`，三個 Verdict 都不是 `infeasible`；inventory.md 就是 Ch 15「三、」列出的那份。

Ch 15 手動合併的 plan.md 帶著 parent 列出的四條 `## Conflicts`，我們照它的建議處理（人審過）：research.md 的 DEC-01 刪掉「(the code today)」；DEC-03 的 Decision 把句尾的 `"9:00" is rejected with 422.` 換成 `a value that does not match ^\d{2}:\d{2}$ (such as "9:00") is rejected with 422 and the detail "use HH:MM, e.g. 09:00"`，Risks 改寫成「略過格式檢查時，pydantic 的原文訊息會到使用者手上」；DEC-02 的 Risks 補上 `the API then answers 503 with a detail that says to retry`；inventory.md 的 `EP:POST /bookings` 一列，Responses 改成 `201 booking with id; 409 R1; 422 R2 or a time not in HH:MM; 503 store busy, retry`。plan.md 的四條 conflict 各標上 `Resolved:`，Constitution Check 的兩個 `pending` 改成 PASS，再補一節 `## Project Structure`，列出 app/main.py、app/store.py、app/static/index.html、tests/test_booking.py、features/booking.feature：tasks 要寫檔案路徑，Ch 15 的合併沒有這一節。最後新增 data-model.md，兩個標題 `## Room`（三間會議室是常數，不存）與 `## Booking`（id、room、start、end、by 五欄的表，id 由 store 從 1 開始、永不重用，以及半開區間的重疊定義），這是原版 `/speckit-plan` Phase 1 的產物，Ch 15 沒有拆它。research.md 與 inventory.md 的修改都在原行內、不增刪行，plan.md 與 data-model.md 只有標題會被檢查讀到，所以用自己的措辭改也不影響後面的輸出（我們照這段描述另建一份重跑，每段輸出都相同）。改完之後，Ch 15 的兩支完成檢查（`research_check.py specs/001-booking/research.md --plan specs/001-booking/plan.md` 與 `inventory.py check specs/001-booking/inventory.md --spec specs/001-booking/spec.md`）照樣印出 `3 decisions, 3 questions, 0 problems` 與 `6 FRs, 5 rows, 0 problems`（換 [Ch 13](./13-plan-anatomy-review.md) 的 `plan_lint.py` 來跑是 `1 plan, 7 problems`：兩則出自 constitution 與 research.md 的寫法，五則是沒有 Summary、contracts、quickstart，見 Ch 15 踩雷 6），feature 檔過了 [Ch 28](./28-gherkin-dsl-style.md) 的 `gherkin_lint.py`（本專案沒有 glossary.yaml，`--glossary` 指向 Ch 28 lab 那份；`1 files, 0 problems`）。原版 `/speckit-tasks` 的第一步 `.specify/scripts/bash/setup-tasks.sh` 在這個起點印出的 `AVAILABLE_DOCS` 是 `✓ research.md`、`✓ data-model.md`、`✗ contracts/`、`✗ quickstart.md`：inventory.md 和 features/ 都在，清單裡沒有它們，因為清單寫死在腳本第 45–50 行。格式本身，看模板與 skill 原文：

```bash
grep -n 'Tests\*\*: The examples\|^## Format\|🎯 MVP\|for traceability' .specify/templates/tasks-template.md
grep -n 'Extract decisions for setup\|\[TaskID\] \[P?\]\|CORRECT: `- \[ \] T001 Create\|missing file path\|^- \[ \] tasks.md generated' .claude/skills/speckit-tasks/SKILL.md
```

```
12:**Tests**: The examples below include test tasks. Tests are OPTIONAL - only include them if explicitly requested in the feature specification.
16:## Format: `[ID] [P?] [Story] Description`
77:## Phase 3: User Story 1 - [Title] (Priority: P1) 🎯 MVP
247:- [Story] label maps task to specific user story for traceability
73:   - If research.md exists: Extract decisions for setup tasks
152:- [ ] [TaskID] [P?] [Story?] Description with file path
170:- ✅ CORRECT: `- [ ] T001 Create project structure per implementation plan`
177:- ❌ WRONG: `- [ ] T001 [US1] Create model` (missing file path)
216:- [ ] tasks.md generated with all phases, task IDs, and file paths
```

四件事值得記下。測試是選配，spec 沒要求就不產；我們的 spec 沒寫，這個要求來自 constitution II（每條規則實作前先有可執行的情境），所以下一節的 tasks.md 在表頭明寫覆寫。一個使用者故事一個 phase、P1 標成 MVP，這是 MVP 優先（MVP-first）：先交出一個能獨立驗收的故事，而不是先蓋完一整層；SDD Ch 28 的 Database → API → Tests 是照層切（layer-sliced），要等最後一層才有第一個能驗收的東西。第 170 行與第 177 行互相打架：沒有路徑的 `T001` 算對，`T001 [US1] Create model` 卻因為「missing file path」算錯，範例也是規格，這種矛盾模型會照單全收。第 216 行是 Done When 的第一條，只問有沒有 phase、ID、路徑。同一個 1.2.5 的另外兩個 skill 也值得一看：`speckit-converge` 的 SKILL.md 第 220 行讓它補上的 task 寫成 `- [ ] T042 <imperative description> per <source-ref> (<gap-type>)`，第 230 行規定「Never reuse or renumber existing IDs」：官方已經在寫來源，只是寫在句尾。`speckit-implement` 第 150 行讀 tasks.md 時列的 phase 卻是「Setup, Tests, Core, Integration, Polish」，一組照層命名的名稱，和 `/speckit-tasks` 產的故事 phase 對不上。

## 二、引用慣例：每個 task 說出它讓哪件事成立

慣例只有一句：**在 Spec Kit 的 labels（`[P]`、`[USn]`）之後、描述之前，用連續的方括號寫出這個 task 讓哪些 ID 成立。** 這串方括號我們叫它 label run。能引用的東西：

| 引用 | 例子 | 定義在 | 必須有 task？ |
|---|---|---|---|
| 功能需求 | `[FR-002]` | spec.md 的 `- **FR-002**:` 行 | 是 |
| 技術決策 | `[DEC-02]` | research.md 的 `## DEC-02` | 是；Verdict 為 `infeasible` 的不必 |
| 端點 | `[EP:POST /bookings]` | inventory.md 表格第一欄 | Status 是 `new`、`change` 的必須；`existing` 選配 |
| 情境 | `[@sc_book_02]` | feature 檔的標籤行 | 是 |
| 沒有 ID 的文件 | `[plan.md#Project Structure]` | 該檔的某個標題 | 否，只檢查指得到 |

1. **只認 label run**。寫在描述中間或句尾的 ID 不算，理由在「底層機制」。
2. **引用讓它成立的 ID，不是碰到的每個 ID**。T003 建 SQLite store，引用 DEC-01 與 FR-006；FR-001 到 FR-004 也用得到 store，但讓它們成立的是別的 task。
3. **情境由寫 step 的 task 引用，FR、決策、端點由實作它的 task 引用**。分開引用，「測試在前」才查得出來。
4. **沒有 ID 的文件用 `檔名#標題`**，只檢查標題存在、不要求覆蓋：一個標題不是一個需求。要覆蓋 data-model 的每個實體，先替它們發 ID，道理同 [Ch 15](./15-research-and-analysis-skills.md)「一、planner 的合約」的「問題帶 ID」。
5. **ID 永不重編**。新 task 拿下一個沒用過的號碼，即使它排在前面的 phase。Spec Kit 要求 ID 是「Sequential number … in execution order」，但 task 一旦被 issue、commit、PR 引用，重編就斷掉所有外部連結，converge 也選了不重編。兩者衝突時我們選穩定，所以檢查只要求 ID 不重複。

照這套慣例手寫的 001-booking tasks.md，當作黃金樣本（`specs/001-booking/tasks.md`）：

```markdown
# Tasks: Room booking

**Input**: specs/001-booking/ (spec.md, plan.md, research.md, inventory.md, data-model.md) and features/booking.feature
**Tests**: required. Constitution II: every rule has an executable scenario before it is implemented, so each story phase binds its scenarios first.
**Citations**: each task cites what it makes true: `[FR-NNN]` spec.md, `[DEC-NN]` research.md, `[EP:<METHOD> <path>]` inventory.md, `[@sc_<name>_NN]` features/, `[<file>.md#<heading>]` for documents without IDs.

## Format: `[ID] [P?] [Story?] [Citation...] Description with file path`

## Phase 1: Setup (Shared Infrastructure)
- [ ] T001 [plan.md#Project Structure] Create app/, app/static/ and tests/ as plan.md lists them
- [ ] T002 [P] [plan.md#Technical Context] Add pytest.ini: register markers sc_book_01..sc_book_04, filter the starlette TestClient warning

## Phase 2: Foundational (Blocking Prerequisites)
- [ ] T003 [DEC-01] [FR-006] [data-model.md#Booking] Create the SQLite store in app/store.py: one file through sqlite3, table bookings(id, room, start, end, by), ids never reused
- [ ] T004 [EP:POST /reset] Add test-only POST /reset, hidden from OpenAPI, in app/main.py
- [ ] T005 [P] [plan.md#Project Structure] Bind features/booking.feature with scenarios() in tests/test_booking.py; steps reach the app through TestClient after POST /reset

**Checkpoint**: `pytest` collects the four scenarios; all fail.

## Phase 3: User Story 1 - Book a free room (Priority: P1) 🎯 MVP
**Goal**: Alice books Taipei from 10:00 to 11:00 and sees "Booked Taipei".
**Independent Test**: `pytest -m "sc_book_01 or sc_book_04"` passes.
- [ ] T006 [US1] [@sc_book_01] Write the steps for booking a free slot in tests/test_booking.py
- [ ] T007 [US1] [@sc_book_04] Write the steps for the end-before-start rejection in tests/test_booking.py
- [ ] T008 [US1] [FR-001] [DEC-03] [EP:POST /bookings] Add POST /bookings in app/main.py: body {room, start, end, by}; a time not matching ^\d{2}:\d{2}$ answers 422 "use HH:MM, e.g. 09:00"; success answers 201 with the booking and its id
- [ ] T009 [US1] [FR-003] Reject an end that is not after the start with 422 in app/main.py
- [ ] T010 [US1] [FR-005] [EP:GET /rooms] Serve GET /rooms with ["Taipei", "Tokyo", "Seoul"] in app/main.py
- [ ] T011 [P] [US1] [FR-005] [EP:GET /] Serve the booking page from app/static/index.html: Room select filled from GET /rooms, Start, End, Name, Book, role=status

**Checkpoint**: @sc_book_01 and @sc_book_04 pass. spec.md puts R1 in User Story 2, so this MVP still accepts overlapping bookings: demo it, do not release it.

## Phase 4: User Story 2 - See who holds a room (Priority: P2)
**Goal**: Bob sees every booking; a rejected booking names who holds the slot.
**Independent Test**: `pytest -m "sc_book_02 or sc_book_03"` passes and GET /bookings lists both bookings.
- [ ] T012 [US2] [@sc_book_02] [@sc_book_03] Write the steps for the overlap rejection and the slot right after a booking in tests/test_booking.py
- [ ] T013 [US2] [FR-002] [DEC-02] Check overlap and insert inside one BEGIN IMMEDIATE transaction in app/store.py; 409 "Taipei is taken 10:00-11:00 by Bob"; a lock timeout answers 503
- [ ] T014 [US2] [FR-004] [EP:GET /bookings] List every booking with id, room, start, end, by in app/main.py
- [ ] T015 [US2] [FR-004] Show the day's bookings under the form in app/static/index.html

## Phase 5: Polish & Cross-Cutting Concerns
- [ ] T016 [FR-006] Book, restart uvicorn on a free port, and confirm GET /bookings still lists the booking; record the commands in specs/001-booking/quickstart.md

## Dependencies & Execution Order
- Setup → Foundational → User Story 1 → User Story 2 → Polish. US2 extends POST /bookings from US1.
- In each story phase the scenario tasks come first (constitution II).
- Not bound, reported to the planners: no source pins the R2 detail text or the answer to an unknown room.
```

表頭的 `**Tests**: required` 直接覆寫模板的「Tests are OPTIONAL」，理由寫在同一行。描述裡引號內的字串都抄自來源：`"use HH:MM, e.g. 09:00"` 來自 DEC-03，`"Taipei is taken 10:00-11:00 by Bob"` 來自 constitution III 的例句。T009 刻意不寫 R2 的錯誤訊息，因為沒有任何來源定義它，最後一行把這個缺口交回給 planner。US1 的 checkpoint 說「不要發布」，原因在「四、」的執行紀錄裡：spec 把 R1 放進了 US2。

## 三、task_coverage.py：把綁定變成檢查

腳本有兩個子命令。`sources` 列出這個功能必須綁定的東西，也就是 binder 的工作清單：

```bash
TC=.claude/skills/tasks-binder/scripts/task_coverage.py
python $TC sources specs/001-booking --features features/booking.feature
```

```
FR-001                 spec.md:17           Users MUST be able to book a room (Taipei, Tokyo or Seoul) for a
…（略）
DEC-03                 research.md:31       Time format at the API
EP:POST /bookings      inventory.md:7       new; FR-001, FR-002, FR-003
…（略）
@sc_book_04            booking.feature:25   Scenario: Booking that ends before it starts
US1                    spec.md:7            Book a free room (Priority: P1)
US2                    spec.md:10           See who holds a room (Priority: P2)
18 sources to cite (6 FRs, 3 DECs, 5 EPs, 4 scenarios), 0 optional, 2 user stories
```

`check` 檢查 tasks.md：`python $TC check specs/001-booking --features features/booking.feature` 在黃金樣本上印出 `16 tasks, 18 sources, 0 problems`。加上 `--matrix` 會先印一張 `| Source | Tasks |` 表（`| FR-002 | T013 |`、`| @sc_book_03 | T012 |`……），review 時從這張表讀起，逐列問「這個 task 真的會讓這個 ID 成立嗎」。完整原始碼（stdlib only，`.claude/skills/tasks-binder/scripts/task_coverage.py`）：

```python
#!/usr/bin/env python3
"""Bind tasks.md to its sources. `sources DIR`: what the tasks must cite (FR-NNN in spec.md;
DEC-NN in research.md unless its Verdict is infeasible; EP:<METHOD> <path> rows with Status new or
change in inventory.md; @sc_<name>_NN tags of --features). `check DIR [--tasks FILE] [--matrix]`: a
task is a Spec Kit 1.2.5 line with citations right after its labels, or [<file>.md#<heading>]:
- [ ] T005 [P] [US1] [FR-002] [DEC-02] [EP:POST /bookings] Description with file path
TC-01 unique IDs; TC-02 phases are Setup, Foundational, User Story N, Polish or Convergence;
TC-03 [USn] matches the story phase; TC-04 every task cites; TC-05 citations and story phases
resolve; TC-06 every source and user story has a task; TC-07 tests first: scenario tasks lead a
story phase, and a test task (cites a scenario or names tests/) precedes tasks citing its IDs."""
import argparse, difflib, re, sys
from pathlib import Path

ID = {"FR": r"FR-\d{3}", "DEC": r"DEC-\d\d", "EP": r"EP:(?:GET|POST|PUT|PATCH|DELETE) /\S*",
      "scenario": r"@sc_[a-z0-9]+_\d{2}"}
DEFINED_IN = {"FR": "spec.md", "DEC": "research.md", "EP": "inventory.md", "scenario": "the feature files"}
SHARED = ("Setup", "Foundational", "Polish", "Convergence")

def n(count, word): return f"{count} {word}{'' if count == 1 else 's'}"
def kind_of(cite): return next((k for k, rx in ID.items() if re.fullmatch(rx, cite)), None)
def line_no(text, pos): return text.count("\n", 0, pos) + 1

def collect(feature, features):
    """Every citable ID -> (kind, where, text, required); user stories -> (where, title)."""
    src, spec = {}, (feature / "spec.md").read_text()
    for m in re.finditer(r"^- \*\*(FR-\d{3})\*\*:\s*(.*)$", spec, re.M):
        src[m[1]] = ("FR", f"spec.md:{line_no(spec, m.start())}", m[2], True)
    stories = {m[1]: (f"spec.md:{line_no(spec, m.start())}", m[2])
               for m in re.finditer(r"^### User Story (\d+)\s*-?\s*(.*)$", spec, re.M)}
    if (feature / "research.md").exists():
        text = (feature / "research.md").read_text()
        heads = list(re.finditer(r"^## (DEC-\d\d)\b *(.*)$", text, re.M))
        for h, nxt in zip(heads, heads[1:] + [None]):
            verdict = re.search(r"\*\*Verdict\*\*:\s*(\S+)", text[h.end():nxt.start() if nxt else None])
            src[h[1]] = ("DEC", f"research.md:{line_no(text, h.start())}", h[2],
                         not (verdict and verdict[1] == "infeasible"))
    if (feature / "inventory.md").exists():
        for i, line in enumerate((feature / "inventory.md").read_text().splitlines(), 1):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("| EP:") and len(cells) > 1:
                src[cells[0]] = ("EP", f"inventory.md:{i}", f"{cells[1]}; {cells[-1]}", cells[1] in ("new", "change"))
    for f in features:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("@"):
                name = next((x.strip() for x in lines[i + 1:] if not x.strip().startswith("@")), "")
                for tag in re.findall(ID["scenario"] + r"\b", line):
                    src[tag] = ("scenario", f"{f.name}:{i + 1}", name, True)
    return src, stories

def why_dangling(cite, feature, src):
    """None when the citation resolves, else what is wrong with it."""
    if kind := kind_of(cite):
        close = difflib.get_close_matches(cite, [s for s in src if kind_of(s) == kind], 1)
        return f"which {DEFINED_IN[kind]} does not define" + (f"; did you mean {close[0]}?" if close else "")
    if not re.fullmatch(r"[\w./-]+\.md#.+", cite):
        return "which is not a citation; use FR-NNN, DEC-NN, EP:<METHOD> <path>, @sc_<name>_NN or <file>.md#<heading>"
    file, _, heading = cite.partition("#")
    path = next((b / file for b in (feature, Path(".")) if (b / file).is_file()), None)
    if not path:
        return f"but {file} does not exist"
    heads = {re.sub(r"^#+\s*", "", x).strip() for x in path.read_text().splitlines() if x.startswith("#")}
    close = difflib.get_close_matches(heading, heads, 1)
    return None if heading in heads else f"but {file} has no heading '{heading}'" + (f"; did you mean '{close[0]}'?" if close else "")

def check(args):
    src, stories = collect(args.feature, args.features)
    path, problems, tasks = args.tasks or args.feature / "tasks.md", [], []
    phase, phase_at, bad_phases, first_other = None, None, set(), {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("## "):
            phase, phase_at = line[3:].strip(), f"{path.name}:{i}"
        if not re.match(r"- \[[ xX]\] ", line):
            continue
        at, m = f"{path.name}:{i}", re.match(r"- \[[ xX]\] (T\d{3})\b(.*)", line)
        if not m:
            problems.append(f"{at}: TC-01 task has no ID; write '- [ ] T001 ...'")
            continue
        tid, rest, labels = m[1], m[2], []
        while lm := re.match(r"\s*\[([^\]]+)\]", rest):
            labels.append(lm[1])
            rest = rest[lm.end():]
        if tid in [t[0] for t in tasks]:
            problems.append(f"{at}: TC-01 {tid} is used twice; a new task takes the next free number")
        name = (re.match(r"Phase \d+: (.*)", phase or "") or [None, None])[1]
        story = re.match(r"User Story (\d+)\b", name or "")
        us = [x for x in labels if re.fullmatch(r"US\d+", x)]
        cites = [x for x in labels if x != "P" and x not in us]
        scenario_task = any(kind_of(c) == "scenario" for c in cites)
        if not name or not (story or name.startswith(SHARED)):
            if phase not in bad_phases:
                bad_phases.add(phase)
                problems.append(f"{phase_at or at}: TC-02 '{phase or '(no heading)'}' is not a Setup, Foundational, "
                                "User Story N or Polish phase; slice tasks by user story, not by layer")
        elif story and story[1] not in stories:
            if phase not in bad_phases:
                bad_phases.add(phase)
                problems.append(f"{phase_at}: TC-05 '{phase}' names User Story {story[1]}, which spec.md does not define")
        elif story and us != [f"US{story[1]}"]:
            problems.append(f"{at}: TC-03 {tid} sits in User Story {story[1]}; label it [US{story[1]}] only")
        elif not story and us:
            problems.append(f"{at}: TC-03 {tid} sits in {name.split()[0]}, which takes no story label")
        if not cites:
            late = [x for x in re.findall(r"\[([^\]]+)\]", rest) if kind_of(x)]
            hint = f" (found [{late[0]}] later in the line; move it before the description)" if late else ""
            problems.append(f"{at}: TC-04 {tid} cites no source{hint}; add [FR-NNN], [DEC-NN], "
                            "[EP:<METHOD> <path>], [@sc_<name>_NN] or [<file>.md#<heading>]")
        problems += [f"{at}: TC-05 {tid} cites {c}, {reason}" for c in cites
                     if c not in src and (reason := why_dangling(c, args.feature, src))]
        if story and scenario_task and phase in first_other:
            problems.append(f"{at}: TC-07 {tid} binds a scenario after {first_other[phase]}; "
                            "in a story phase, scenario tasks come first (constitution II)")
        elif story and not scenario_task:
            first_other.setdefault(phase, tid)
        is_test = scenario_task or bool(re.search(r"(?<![\w/])tests/", rest))
        tasks.append((tid, name or "", cites, is_test, at))
    cited, done_by = {}, {}
    for tid, _, cites, is_test, at in tasks:
        for c in filter(kind_of, cites):
            cited.setdefault(c, []).append(tid)
            if not is_test:
                done_by.setdefault(c, tid)
            elif c in done_by:
                problems.append(f"{at}: TC-07 {tid} tests {c} after {done_by[c]} implements it; write the test first")
    problems += [f"{where}: TC-06 {sid} is cited by no task; bind it to the task that makes it true"
                 for sid, (_, where, _, req) in src.items() if req and sid not in cited]
    problems += [f"{where}: TC-06 User Story {k} has no phase; add '## Phase N: User Story {k} - {title}'"
                 for k, (where, title) in stories.items()
                 if not any(re.match(rf"User Story {k}\b", t[1]) for t in tasks)]
    if args.matrix:
        print("| Source | Tasks |\n|---|---|")
        for sid, (_, _, _, req) in src.items():
            print(f"| {sid}{'' if req else ' (optional)'} | {', '.join(cited.get(sid, [])) or '-'} |")
    required = sum(req for *_, req in src.values())
    print("\n".join(problems + [f"{n(len(tasks), 'task')}, {n(required, 'source')}, {n(len(problems), 'problem')}"]))
    return 1 if problems else 0

def sources(args):
    src, stories = collect(args.feature, args.features)
    for sid, (kind, where, text, req) in src.items():
        print(f"{sid:<22} {where:<20} {'' if req else '(optional) '}{text[:64]}")
    for k, (where, title) in stories.items():
        print(f"{'US' + k:<22} {where:<20} {title}")
    kinds = [kind for kind, *_, req in src.values() if req]
    counts = ", ".join(n(kinds.count(k), k) for k in ID if kinds.count(k))
    print(f"{n(len(kinds), 'source')} to cite ({counts}), {len(src) - len(kinds)} optional, "
          f"{len(stories)} user stor{'y' if len(stories) == 1 else 'ies'}")
    return 0

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sources", "check"):
        p = sub.add_parser(name)
        p.add_argument("feature", type=Path, help="feature dir, e.g. specs/001-booking")
        p.add_argument("--features", type=Path, nargs="*", default=[], help=".feature files whose @sc_ tags this feature owns")
        if name == "check":
            p.add_argument("--tasks", type=Path, help="tasks file to check (default FEATURE_DIR/tasks.md)")
            p.add_argument("--matrix", action="store_true", help="print a source -> tasks table first")
    args = ap.parse_args()
    tasks = [args.tasks or args.feature / "tasks.md"] if args.cmd == "check" else []
    missing = [p for p in [args.feature / "spec.md", *args.features, *tasks] if not p.is_file()]
    if missing:
        print(f"task_coverage: {missing[0]} not found", file=sys.stderr)
        return 2
    return check(args) if args.cmd == "check" else sources(args)

if __name__ == "__main__":
    sys.exit(main())
```

結束碼：0 乾淨、1 有問題、2 檔案找不到（`task_coverage: nope.md not found`）。**先看它失敗。** 在黃金樣本上做五個常見的錯，存成 `../bad/tasks-mistakes.md`：T003 的 `[data-model.md#Booking]` 拼成 `#Bookings`；T013 的 `[FR-002] [DEC-02]` 搬到句尾（agent 最常見的寫法）；T007 整行搬到 T009 之後；T010 的 `[EP:GET /rooms]` 拼成 `[EP:GET /room]`；T015 的 `[US2]` 改成 `[US1]`。

```bash
python $TC check specs/001-booking --features features/booking.feature --tasks ../bad/tasks-mistakes.md; echo "exit=$?"
```

```
tasks-mistakes.md:14: TC-05 T003 cites data-model.md#Bookings, but data-model.md has no heading 'Bookings'; did you mean 'Booking'?
tasks-mistakes.md:26: TC-07 T007 binds a scenario after T008; in a story phase, scenario tasks come first (constitution II)
tasks-mistakes.md:27: TC-05 T010 cites EP:GET /room, which inventory.md does not define; did you mean EP:GET /rooms?
tasks-mistakes.md:36: TC-04 T013 cites no source (found [FR-002] later in the line; move it before the description); add [FR-NNN], [DEC-NN], [EP:<METHOD> <path>], [@sc_<name>_NN] or [<file>.md#<heading>]
tasks-mistakes.md:38: TC-03 T015 sits in User Story 2; label it [US2] only
spec.md:18: TC-06 FR-002 is cited by no task; bind it to the task that makes it true
research.md:18: TC-06 DEC-02 is cited by no task; bind it to the task that makes it true
inventory.md:9: TC-06 EP:GET /rooms is cited by no task; bind it to the task that makes it true
16 tasks, 18 sources, 8 problems
exit=1
```

五個錯、八行問題，後三行是連鎖反應：句尾的引用不算數，FR-002 與 DEC-02 就沒人綁；拼錯的端點 ID 不存在，真的那個也就沒人綁。每行都指向要改的檔案與行號並說出改法，`did you mean` 用 `difflib` 找最接近的已知 ID。**照層切的版本**引用一個不少，phase 照 SDD Ch 28 的方式切（`../bad/tasks-layered.md`）：

```markdown
# Tasks: Room booking (sliced by layer)
## Phase 1: Database
- [ ] T001 [DEC-01] [FR-006] [DEC-02] [FR-002] Create the store and the overlap check in app/store.py
## Phase 2: API Layer
- [ ] T002 [FR-001] [FR-003] [DEC-03] [EP:POST /bookings] Add POST /bookings in app/main.py
- [ ] T003 [FR-004] [FR-005] [EP:GET /bookings] [EP:GET /rooms] [EP:POST /reset] Add the other routes in app/main.py
## Phase 3: Page
- [ ] T004 [FR-005] [EP:GET /] Build the booking page in app/static/index.html
## Phase 4: Tests
- [ ] T005 [@sc_book_01] [@sc_book_02] [@sc_book_03] [@sc_book_04] Write the steps in tests/test_booking.py
```

```
tasks-layered.md:2: TC-02 'Phase 1: Database' is not a Setup, Foundational, User Story N or Polish phase; slice tasks by user story, not by layer
…（略）
spec.md:7: TC-06 User Story 1 has no phase; add '## Phase N: User Story 1 - Book a free room (Priority: P1)'
spec.md:10: TC-06 User Story 2 has no phase; add '## Phase N: User Story 2 - See who holds a room (Priority: P2)'
5 tasks, 18 sources, 6 problems
```

18 個來源全都有 task，覆蓋率 100%，問題全在結構：沒有任何一個 phase 做完時能交出可驗收的東西，測試在最後一個 phase。只看覆蓋率不夠，原因在此。

## 底層機制：label run、二分圖與兩種「測試在前」

```
 spec.md       ^- **FR-NNN**:                 ─┐
 research.md   ^## DEC-NN 與該節的 Verdict     ├─ collect()：ID → (檔名:行, 文字, 必須?)
 inventory.md  | EP:… | Status | … | Source |   │           使用者故事 → (spec.md:行, 標題)
 *.feature     標籤行裡的 @sc_<name>_NN       ─┘
 tasks.md      - [ ] Tnnn 之後連續的 [..] ──▶ P、USn、引用；剩下的描述只用來找 tests/ 與錯放的引用
     ├─ 逐行：TC-01 ID、TC-02 phase 名、TC-03 故事標籤、TC-04 有引用、TC-05 指得到、TC-07 情境在前
     └─ 全檔：TC-06 每個必須的來源與故事都有 task；TC-07 同一個 ID 的測試在實作之前
```

把來源和 task 想成二分圖（bipartite graph）的兩排點，每個引用是一條邊：TC-06 要求左排每個必須的點至少有一條邊，TC-04 要求右排每個點至少有一條邊，TC-05 要求每條邊的左端真的存在。三條都是圖上的計數，結果可重現，可以放進 hook 或 CI。

**為什麼只認 label run？** 從黃金樣本刪掉 T009（唯一實作 R2 的 task），再在 T013 句尾加上 `; leave the FR-003 range check to a later task`，存成 `../bad/tasks-prose.md`。用 grep 找 ID，和用腳本查：

```bash
grep -oE 'FR-[0-9]{3}' ../bad/tasks-prose.md | sort -u | tr '\n' ' '; echo
python $TC check specs/001-booking --features features/booking.feature --tasks ../bad/tasks-prose.md
```

```
FR-001 FR-002 FR-003 FR-004 FR-005 FR-006
spec.md:19: TC-06 FR-003 is cited by no task; bind it to the task that makes it true
15 tasks, 18 sources, 1 problem
```

grep 說六條 FR 都在，因為「先別做 FR-003」也含有 FR-003。這是所有關鍵字比對的毛病：提到不等於綁定。label run 是位置約定，寫在對的位置才算數，「提到」和「綁定」因此分得開。

**兩種「測試在前」。** 第一種在故事 phase 內：引用情境的 task 必須排在第一個不引用情境的 task 之前。第二種跨 phase、以 ID 為單位：一個測試 task（引用情境，或描述裡有 `tests/` 底下的檔案）若和某個實作 task 引用同一個 ID，必須排在它前面。第二種是看了真實執行的結果才加的，見下一節。認「測試 task」靠路徑，是啟發式：描述裡沒寫 `tests/` 的測試，它認不出來。

**腳本看不到的。** 從黃金樣本刪掉 T009 與 T014（GET /bookings 的後端），把它們的 ID 掛到隔壁：T013 多引用 `[FR-003]`、T015 多引用 `[EP:GET /bookings]`（`../bad/tasks-overcite.md`）。結果是 `14 tasks, 18 sources, 0 problems`，可是 R2 與 GET /bookings 都沒有人實作了。過度引用（over-citation）讓覆蓋檢查失去意義，只有讀描述的人看得出「T013 做的是重疊檢查，它不會讓 R2 成立」。

## 四、tasks-binder skill 與三次真實執行

skill 本體 `.claude/skills/tasks-binder/SKILL.md`：

```markdown
---
name: tasks-binder
description: Write a feature's tasks.md from its planning documents (spec.md FRs and user stories, research.md decisions, inventory.md endpoints, feature-file scenarios) so that every task cites its sources and every source has a task. Use after planning, when asked to generate or update the tasks of a feature under specs/.
argument-hint: "<feature dir> <feature file>"
allowed-tools: Read Grep Glob Bash(python .claude/skills/tasks-binder/scripts/task_coverage.py *)
---

# tasks-binder

One job: bind what the planners decided into `$0/tasks.md`. Do not change their decisions; do not implement.

## Contract

- Reads: `$0/spec.md`, `$0/plan.md`, `$0/research.md`, `$0/inventory.md`, `$0/data-model.md` if present, `.specify/memory/constitution.md`, `$1`
- Writes: `$0/tasks.md` only
- Done when: `python .claude/skills/tasks-binder/scripts/task_coverage.py check $0 --features $1` prints `0 problems`

## Steps

1. If plan.md has a `## Conflicts` entry not marked `Resolved`, stop and list it: a task bound to two contradicting sources implements the contradiction.
2. Run `python .claude/skills/tasks-binder/scripts/task_coverage.py sources $0 --features $1`. Its list is the work: every line needs at least one task; an `(optional)` line needs one only if this feature changes it.
3. Fill `templates/tasks.md`: one phase per user story in priority order, the P1 story marked MVP. Setup and Foundational hold only what every story needs. Never slice phases by layer (Database, API, Tests).
4. Cite in the label run, after `[USn]` and before the description: the IDs this task makes true, not every ID it touches. A scenario is cited by the task that writes its steps; an FR, decision or endpoint by the task that implements it. Documents without IDs: `[<file>.md#<heading>]`.
5. In each story phase, scenario tasks come first. Copy constraints from the source into the description word for word: status codes, error details, formats.
6. If tasks.md exists, keep every task ID; a new task takes the next free number, even when it goes in an earlier phase. Never renumber.
7. Run the done check until it prints `0 problems`. Never edit spec.md, research.md, inventory.md or the feature file to silence it: a source that needs no task is a planning problem; say so in your final message.
```

它和 [Ch 15](./15-research-and-analysis-skills.md) 的 planner 同構：一個產物、一支完成檢查、不准改別人的產物。第 1 步接在 Ch 15 parent 的 `## Conflicts` 之後：兩份來源互相矛盾時，綁定只會把矛盾變成工作。第 2 步把「找出所有要綁的 ID」交給腳本，模型負責判斷每個 ID 由哪個 task 實作。`$1` 是第二個參數（`$ARGUMENTS[1]` 的簡寫）。`templates/tasks.md`（35 行）是從黃金樣本抽出的骨架，做法同 [Ch 10](./10-templates-and-scripts.md)：標題、三行表頭（`**Input**` 換成佔位，`**Tests**`、`**Citations**` 照抄黃金樣本）、Format 行，Setup、Foundational、User Story 1（標 MVP）、Polish 各留一行 `<…>` 佔位的 task（例如 `- [ ] T00N [US1] [@sc_<name>_NN] Write the steps for <scenario> in <test file>`），最後是 Dependencies 一節。本章沒有列出它的全文，後面的輸出也都不讀它，自己重做時照這段描述寫一份。`node /tmp/lab/tools/skill-lint.mjs .claude/skills` 得到 `14 skills, 0 problems`；用 [Ch 4](./04-prompt-files-to-skills.md) 的 `context_cost.py` 量，它的 listing 每 turn 佔 328 B。

**實際執行紀錄（節錄），2026-11-02，每次執行結果不同。** 同一組輸入（拿掉黃金樣本）各跑一次。共同旗標是 `--setting-sources project --strict-mcp-config --tools "Read,Write,Edit,Glob,Grep,Bash" --permission-prompts none --max-turns 20 --max-budget-usd 3 --no-session-persistence`，stdin 接 `/dev/null`；`--allowedTools` 都是 `Read`、`Glob`、`Grep`、`Edit(specs/001-booking/tasks.md)`，原版另加 `Bash(.specify/scripts/bash/setup-tasks.sh *)`，binder 另加 `Bash(python .claude/skills/tasks-binder/scripts/task_coverage.py *)`。prompt 分別是 `/speckit-tasks` 與 `/tasks-binder specs/001-booking features/booking.feature`。表中的 turns 是結果 JSON 的 `num_turns`，三次都等於工具呼叫數加一，單位和 `--max-turns` 不同（見 [Ch 15](./15-research-and-analysis-skills.md)「四、」的成本表），所以會超過 20；三次都正常結束（`subtype` 是 `success`）。

| 執行 | turns | 秒 | 美元 | tasks | 行數 | `task_coverage.py check` |
|---|---|---|---|---|---|---|
| 1. 原版（作廢，見下） | 26 | 166 | 0.71 | 19 | 182 | 不列 |
| 2. 原版，乾淨的副本 | 22 | 212 | 0.90 | 22 | 203 | `40 problems`（22 行 TC-04、18 行 TC-06） |
| 3. `tasks-binder` | 23 | 130 | 0.57 | 20 | 61 | 執行當時 `0 problems`，現行版本 `4 problems` |

**第 1 次作廢。** 那份副本是從已經 commit 了黃金樣本的 repo clone 出來再刪檔的；agent 跑了 `git show --stat --format='%h %s' HEAD`，看到 commit 訊息「tasks-binder skill, task_coverage.py, golden tasks.md」。它沒有讀那些檔，但已經知道有一份標準答案和一支覆蓋檢查。第 2、3 次改用只有一個 commit、訊息中性的新 repo。**第 2 次（原版）做得不差。** 它讀了 inventory.md 和 feature 檔（`git ls-files` 列得到，清單沒列也擋不住），因為 constitution II 而產了測試 task，每個故事 phase 都把情境排在前面，還把七件文件沒決定的事列成一張表。它的引用有三種寫法：句中的方括號（T003 的 Create `app/store.py` `[DEC-01] [DEC-02]`）、句尾的括號（`(EP:GET /rooms, FR-005)`）、寫在 phase 的 Goal 行。它寫的 tasks.md 存成 `../bad/tasks-stock.md`（你自己跑出來的會不同），在 task 行裡找 ID：

```bash
grep -E '^- \[ \] T[0-9]{3}' ../bad/tasks-stock.md | grep -oE 'FR-[0-9]{3}|DEC-[0-9]{2}|EP:(GET|POST) /[a-z]*|@sc_[a-z]+_[0-9]{2}' | sort -u | tr '\n' ' '; echo
```

```
@sc_book_01 @sc_book_02 @sc_book_03 @sc_book_04 DEC-01 DEC-02 DEC-03 EP:GET / EP:GET /bookings EP:GET /rooms EP:POST /bookings FR-002 FR-003 FR-004 FR-005 FR-006
```

18 個少了 FR-001 與 `EP:POST /reset`，而這兩件事其實都有 task 在做，只是沒寫出 ID。內容大致齊全，卻沒有方法能機械地確認；嚴格檢查的 40 行裡，有三行是 `found [DEC-01] later in the line` 這種寫了、但寫錯位置的引用。

**第 3 次（binder）** 第一個工具呼叫就跑了 `sources`，接著讀了模板和 `task_coverage.py` 的原始碼，寫一次、檢查一次就得到 `20 tasks, 18 sources, 0 problems`：腳本的 docstring 本身就是寫給它讀的規格。它先確認四條 conflict 都已 `Resolved`，最後把缺口原樣交回：沒有任何來源定義 R2 的錯誤訊息、未知會議室的回應、`/reset` 怎麼擋在正式環境之外，`uvicorn` 與 `httpx` 也不在 plan 的依賴裡。同一個 R2 缺口，第 2 次自己決定成 `the end must be after the start`（拿情境句當 API 的 detail）；我們手寫的第一版黃金樣本也犯了同一種錯，寫上課程慣例的 `end must be after start`，而 lab 的來源裡最接近的只有本章 sed 加上的 Rule 標題 `R2 End must be after start` 與情境句，沒有一處規定 API 的 detail。`task_coverage.py` 看不到這種沒有來源的限制。**兩次乾淨執行都把 R1 放進 US2。** spec 的 US2 寫的是「Bob sees every booking; a rejected booking names who holds the slot」，兩次都照字面切，也都警告 US1 的 MVP 會接受重疊預約：原版寫「do not release this increment」，binder 寫「If US1 should ship with R1, that's a change to the spec's stories」。作廢的第 1 次自作主張把 R1 搬進 US1，我們的第一版黃金樣本也是。照 skill 的規則，故事怎麼切是 spec 的決定，binder 該做的是回報，所以現在的黃金樣本跟著 spec 切，並在 checkpoint 寫明不要發布。

**腳本漏掉的，和怎麼補。** binder 的輸出在當時的腳本下是 0 problems，讀它的人卻看得出四個測試寫在實作之後：US2 的 `test_list_bookings` 排在 GET /bookings 之後，Polish 裡三個測試對應的 FR-006、DEC-02、DEC-03 早在前面的 phase 實作了。當時的 TC-07 只認情境 task，這四個測試都沒引用情境。我們補上「同一個 ID 的測試在實作之前」，再加一條執行後才想到的「故事 phase 必須對應 spec 裡真的有的故事」，在保存下來的同一份輸出（`../bad/tasks-binder-run.md`）上重跑 `python $TC check specs/001-booking --features features/booking.feature --tasks ../bad/tasks-binder-run.md`：

```
tasks-binder-run.md:46: TC-07 T017 tests FR-004 after T015 implements it; write the test first
tasks-binder-run.md:52: TC-07 T018 tests FR-006 after T002 implements it; write the test first
tasks-binder-run.md:53: TC-07 T019 tests DEC-02 after T013 implements it; write the test first
tasks-binder-run.md:54: TC-07 T020 tests DEC-03 after T008 implements it; write the test first
20 tasks, 18 sources, 4 problems
```

修正版**沒有**再用 agent 跑一次，三次 `claude -p` 已用完。這張表也**不能**回答「binder 比較便宜」：每種只跑了一次，第 2 次多花的 82 秒與 0.33 美元，可能來自它寫了 203 行（binder 只寫 61 行），也可能是單次的變異。能說的只有：同樣的輸入、同一個模型，拿到引用慣例與一支會失敗的完成檢查的那一次，引用全寫在 label run；沒拿到的那一次，寫在三種地方。

## 對比與取捨

| 做法 | 引用寫在哪 | 誰查覆蓋 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|---|
| 原版 `/speckit-tasks` + `/speckit-analyze` | `[USn]`，其餘隨模型 | LLM 關鍵字推論 | 零設定，跟著官方升級 | 靠模型自律，不保證可重現；analyze 不讀 research.md、inventory.md | 小功能、探索 |
| `tasks-binder` + `task_coverage.py`（本章） | label run | 腳本，雙向 | 可重現；能進 hook、CI；錯誤訊息說怎麼修 | 慣例與腳本要維護；只查接上了沒 | 有多份分析文件、要長期維護 |
| 程式流水線（SDD Ch 38） | `acceptance_criterion_ids` 欄位 | 程式，單向（task → AC） | 結構化、好並行 | 要寫程式；沒查反方向 | CI 驅動的大量規劃 |
| `check-plan.mjs`（pstack Ch 27） | 每個 PR 的九個子區塊 | 腳本，只查格式 | 計畫的形狀很嚴格 | 不查和需求對不對得上 | 多 PR 的大改動 |
| issue tracker 連結 | issue key | tracker 的報表 | 和日常工作流整合 | 規格與 tracker 各自漂移 | 已重度使用 tracker 的團隊 |

## 踩雷集錦

1. **錯誤直覺：描述裡提到 FR-003，就算綁上了。** 正確認識：`tasks-prose.md` 裡「先別做 FR-003」也讓 grep 找到 FR-003。只認固定位置的引用，「提到」與「綁定」才分得開。
2. **錯誤直覺：引用越多越保險。** 正確認識：`tasks-overcite.md` 刪掉兩個 task、把 ID 掛到隔壁，檢查照樣 0 problems。只引用讓它成立的 ID；review 時用 `--matrix` 逐列問「這個 task 真的會讓這個 ID 成立嗎」。
3. **錯誤直覺：照使用者故事切 phase，MVP 自然就能交付。** 正確認識：MVP 能不能交付取決於 spec 怎麼切故事。001 的 spec 把 R1 放在 US2，兩次乾淨執行都照做，也都警告 US1 會接受重疊預約；該修的是 spec（[Ch 18](./18-clarify-skill.md) 的 clarify），不是讓 binder 偷偷搬。
4. **錯誤直覺：檢查通過就代表測試在前。** 正確認識：第一版腳本放過了四個寫在實作之後的測試；現行版本以 ID 為單位查，但認「測試 task」靠 `tests/` 路徑，描述裡沒寫路徑的測試照樣認不出來。
5. **錯誤直覺：比較兩個 skill 時，把答案檔刪掉就乾淨了。** 正確認識：第 1 次執行用一行 `git show --stat --format='%h %s' HEAD` 就看到了「golden tasks.md」。A/B 的副本要用不含答案的歷史，最穩的是只有一個 commit 的新 repo。

## 進階：接回 Spec Kit 的流程

- **掛成 `after_tasks` hook。** speckit-tasks 的 SKILL.md 在回報前會讀 `.specify/extensions.yml` 的 `hooks.after_tasks`，`optional: false` 的 hook 要求 agent 實際執行。把 `task_coverage.py check` 包成 extension command 掛上去，原版流程也有了覆蓋檢查。本段未實測，為依 1.2.5 SKILL.md 原文的預期行為；驗證方法是寫一份 extensions.yml、跑 `/speckit-tasks`，看 stream-json 裡有沒有那次執行。
- **和 converge 並存。** 照 converge SKILL.md 第 220 行的格式，它補的 task 會長這樣：`- [ ] T004 Return 201 from POST /new per FR-001 (partial)`（這行是我們照格式手寫的，沒有實跑 converge）。把它放進一個小 fixture 檢查，得到 `TC-04 T004 cites no source`。兩條路：converge 之後把 `per FR-001` 改寫進 label run；或讓 Convergence phase 也接受 `per <ID>`，代價是在一個角落放回「提到就算」。
- **對照表是下游的介面。** `--matrix` 的「來源 → task」表，[Ch 24](./24-testplan-skill.md) 的測試計畫可以拿來對 `trace`，[Ch 31](./31-bdd-in-sdd.md) 的 `scenario_task_map.py` 要求每個情境恰好一個主人 task，並附上驗證指令；tasks.md 本身是計畫類產物，合併後該歸檔，見 [Ch 33](./33-truth-vs-plan.md)。哪些來源必須覆蓋，也可以變成 [Ch 17](./17-constitution-and-presets.md) 參數層裡 `tasks-binder` 的參數。

## 動手練習

1. **修 spec，再重綁。** 把「a rejected booking names who holds the slot」從 US2 搬到 US1，依規則 5 把 T012、T013 搬進 Phase 3、標成 `[US1]`、不改編號，T012 放在 T008 之前。我們實測的結果是 `16 tasks, 18 sources, 0 problems`。說明為什麼改 spec 之前，同一份黃金樣本也是 0 problems。
2. **加上 R3。** spec.md 加一條 `- **FR-007**:`（辦公時間 08:00–20:00），feature 檔加一個 `@sc_book_05` 情境（句子抄 [Ch 3](./03-propose-constrain-feedback.md) 的「Booking that starts before the office opens」）。先預測檢查會報什麼，我們得到兩行 TC-06 與 `16 tasks, 20 sources, 2 problems`；再用 T017、T018 補上，回到 `18 tasks, 20 sources, 0 problems`。
3. **brownfield 的 002。** 把 Ch 15 第 2 次執行寫的 inventory.md（`/tmp/lab/aibdd/ch15-planners/runs/r2/project/specs/002-cancel/inventory.md`）複製進 `specs/002-cancel/`，`python $TC sources specs/002-cancel` 列出 `6 sources to cite (4 FRs, 2 EPs), 3 optional, 0 user stories`：三個 `existing` 端點是選配，而 spec 沒有任何 `### User Story`，現行腳本會用 TC-05 擋下所有故事 phase。替 spec 補上故事，寫 `features/cancel.feature`（`@sc_cancel_01`–`03`）與 tasks.md，把檢查跑到 0 problems。
4. **延伸**：加一條 TC-08，同一個 phase 裡兩個 `[P]` task 的描述指向同一個檔案時報錯（`[P]` 的語意見 [SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md)「`tasks-template.md`：`[P]` 標記與並行安全」）。先準備一份會失敗的輸入。

## 本章重點整理

- Spec Kit 1.2.5 的 tasks 格式只用 `[Story]` 追溯；決策、端點、情境沒有位置，`setup-tasks.sh` 不知道 inventory.md，覆蓋交給用關鍵字推論的 `/speckit-analyze`。
- 引用慣例：在 `[USn]` 之後、描述之前寫出 task 讓哪些 ID 成立；有 ID 的來源要求覆蓋，`檔名#標題` 只要求指得到；ID 永不重編。
- `task_coverage.py` 把綁定當成二分圖：漏做（TC-06）、孤兒（TC-04）、斷線（TC-05），再加上故事 phase（TC-02、TC-03）與測試在前（TC-07）。它回答「接上了沒」，過度引用、沒有來源的限制、切錯的故事都看不到。
- 三次真實執行裡，原版的內容不差但引用無法機械確認；binder 把腳本當完成條件、把缺口交回 planner；它的輸出又促成了一條新的 TC-07。

## 自我檢核

- [ ] 不看本章，說出 1.2.5 的 tasks 格式有哪幾個欄位，以及原版 `/speckit-tasks` 的生成流程對 research.md 下了什麼指示。
- [ ] 為什麼一份覆蓋率 100% 的 tasks.md 仍可能是錯的？舉出本章的兩個例子，並說出各由哪條規則或哪一種人審抓到。
- [ ] label run、grep 式的關鍵字比對、`/speckit-analyze` 這種 LLM 推論，各會怎麼判讀「T013 句尾寫著『先別做 FR-003』」？哪一種的答案可以重現？
- [ ] 「ID 永不重編」和 Spec Kit 的「Sequential number … in execution order」衝突時，你會怎麼選？代價是什麼？
- [ ] binder 遇到「spec 的故事切法讓 MVP 不能發布」時，該做什麼、不該做什麼？

## 延伸閱讀

- **[Spec Kit v1.2.5 — templates/tasks-template.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/tasks-template.md)** 與 **[templates/commands/tasks.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/tasks.md)**
  - **讀哪裡**：模板的 Phase 3 範例與「Implementation Strategy」；command 的「Task Generation Rules」與「Done When」。
  - **學到什麼**：本章引用的 tasks 模板與指令原文：故事 phase、MVP、測試選配、checklist 格式與互相矛盾的兩個範例。
  - **和本章的關聯**：我們的慣例是在這個格式上加一段 label run，不是另起格式；先讀 [Ch 12](./12-spec-kit-1x.md) 更好。
- **[O. Gotel, A. Finkelstein — An Analysis of the Requirements Traceability Problem](https://doi.org/10.1109/ICRE.1994.292398)**（IEEE International Conference on Requirements Engineering，1994，pp. 94–101）
  - **學到什麼**：以一百多位從業者的實證研究，把需求追溯分成 pre-RS 與 post-RS，並指出多數追溯問題出在 pre-RS 那一段。
  - **讀哪裡**：摘要與 pre-RS、post-RS 的定義；不熟需求工程的話，先讀 [SDD Ch 8](../spec_driven_development/08-why-requirements-hard.md)。
  - **和本章的關聯**：`task_coverage.py` 只處理 post-RS 的一小截；「R2 的錯誤訊息沒有任何來源」落在 pre-RS 那一段：課程慣例裡有這句，寫 spec 時沒帶進來，腳本看不到。
- **[Bill Wake — INVEST in Good Stories, and SMART Tasks](https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/)**（xp123.com，2003-08-17）
  - **讀哪裡**：整篇，很短；重點是 Valuable 一段與後半的 SMART tasks。
  - **學到什麼**：Valuable 用多層蛋糕比喻「slice vertically through the layers」；task 要 SMART，Measurable 的判準是「can we mark it as done?」。
  - **和本章的關聯**：照故事、不照層切的經典說法；「能不能打勾」要有判準，本章的 label run 讓每個 task 說出它讓哪個 ID 成立，[Ch 31](./31-bdd-in-sdd.md) 再替每個擁有情境的 task 附上一行用 tag 選情境的 Verify 指令。
- **[Jeff Patton — The New User Story Backlog is a Map](https://jpattonassociates.com/the-new-backlog/)**（2008-10-08，2023 年更新）
  - **讀哪裡**：backbone、walking skeleton 與用膠帶畫發布線那幾段。
  - **學到什麼**：先橫跨 backbone 做出能走的骨架，再逐次加厚：「building up all major features a little at a time. That way we never release a car without brakes.」
  - **和本章的關聯**：001 的 US1 正是一台沒有煞車的車（沒有 R1）；這篇教你怎麼切故事，binder 才綁得出能發布的 MVP。

tasks 綁好了，constitution II 在本章被當成「要求測試」的那一行讀了好幾次：哪個 skill 讀 constitution 的哪一段、讀到什麼參數，下一章把它整理成所有 SDD skill 共用的參數層，再改寫 /specify。

→ [Ch 17 Constitution 參數層與改寫 /specify](./17-constitution-and-presets.md)
