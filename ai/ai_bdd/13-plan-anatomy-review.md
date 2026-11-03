# Ch 13 — 解剖 /plan：plan.md、research.md 與怎麼 review

> **目標**：逐段讀懂 Spec Kit 1.2.5 的 `plan-template.md` 與 `speckit-plan` skill：Technical Context 的未知數怎麼變成 research.md 的決策、Constitution Check 為什麼判兩次、Complexity Tracking 何時該填、Phase 1 的 data-model、contracts、quickstart 各從 spec 的哪一段推出來；看懂 `setup-plan.sh` 怎麼找到 feature；最後用兩層 review（人看的清單加上 `plan_lint.py`）審五份計畫，其中一份由 agent 實際產生，並說得出哪些錯只有人抓得到。

> **環境**：Spec Kit 1.2.5（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`）、macOS 內建 GNU bash 3.2.57、Python 3.12.15（`/tmp/lab/aibdd/.venv`，`plan_lint.py` 只用標準函式庫）、Claude Code 2.1.295（一次真實的 `claude -p`，2026-11-02）、macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch13-plan/`，從空目錄開始。

## 為什麼需要這個？

這個 repo 跑過 `/speckit.plan`。[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 的「第四步」把它定位成「給定 WHAT，決定 HOW」；[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) 的參考 reflection 記下兩個典型失誤：constitution 寫了最小外部依賴，research.md 還是列了五家 e-mail 服務；功能沒有對外 API，plan 還是產了一份 `api-spec.json`。[SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) 引 Böckeler 的實測，把這類現象叫範疇膨脹（scope inflation）。缺的有兩件事：沒人逐行讀過釘住版本的 plan 模板與 plan 指令原文（[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) 的 scaffold 解剖只說模板「引導 agent 輸出」哪些東西，SDD Ch 28 第四步的 plan.md 範例用的是 Architecture Overview 這類自訂段落，[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) 照原文節錄的是 spec 與 tasks 模板）；review 也只有「架構合不合理」「data-model 和領域模型一不一致」兩個問題。

在 agent 之前，這份文件叫設計文件，或架構決策紀錄（Architecture Decision Record，ADR）：Nygard 2011 年提的格式寫脈絡、決策、狀態、後果，[SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md) 的「進階延伸」講過為什麼「為何不選 X」最值錢。現在寫它的換成 agent，幾分鐘交出幾百行（本章那次真實的 run 交了 6 個檔、450 行），人讀不完就開始略讀。可是 plan 是攔下 HOW 的錯最便宜的地方：過了這關，`/speckit-tasks` 會把一個錯拆成好幾個任務，`/speckit-implement` 再把任務變成程式碼。本章逐行讀 1.2.5 的模板與 skill，實跑 `setup-plan.sh` 看它做了什麼、沒做什麼，用會議室預約走一遍「spec 的哪一段推出哪一份產物」，再把 review 拆成兩層：機器判得了的交給 `plan_lint.py`，其餘留給人。還沒看過 1.2.5 的整棵目錄樹、指令怎麼變成 skill，先讀 [Ch 12](./12-spec-kit-1x.md)。

## 先建立直覺：送建照前的法規檢討

建築師送建照，圖說裡有一張法規檢討表：建蔽率、容積率、停車位，一條一條寫「符合」或「不符合」，不符合要附理由；圖改完要再檢討一次，因為改圖可能讓原本符合的條目變成不符合。選材另附比較表：選了哪種、為什麼、比過哪些。`/speckit-plan` 是同一套東西：

```
spec.md（業主要什麼） + constitution.md（這塊地的法規）
  ├─ setup-plan.sh：把 plan-template.md 複製成 plan.md     ← 整個流程裡唯一的程式
  ├─ Technical Context：不知道的欄位寫 NEEDS CLARIFICATION
  ├─ GATE 1：Constitution Check（研究之前）
  ├─ Phase 0 ──> research.md     每個未知數 → Decision / Rationale / Alternatives considered
  ├─ Phase 1 ──> data-model.md   spec 的 Key Entities
  │              contracts/      系統對外的介面
  │              quickstart.md   驗收情境 → 可執行的驗證步驟
  ├─ GATE 2：Constitution Check（設計之後再判一次）
  ▼
/speckit-tasks 讀 plan.md 和上面每一份
```

類比有一處不成立：建築法規是法律，有人蓋章；constitution 是團隊自己寫、可以修的，而 plan 上的「符合」**沒有人蓋章**，是 agent 自己填的。底層機制一節會看到它的後果。

## 一、實驗室：一份 spec、一份 constitution

```bash
mkdir -p /tmp/lab/aibdd/ch13-plan/booking && cd /tmp/lab/aibdd/ch13-plan/booking
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify init --here --force --non-interactive --integration claude --script sh
bash .specify/scripts/bash/create-new-feature.sh --json --short-name room-booking "Book a meeting room, reject overlapping bookings, cancel your own booking"
```

（本章用 [Ch 0](./00-environment-setup.md) 的 `specify125` 包裝跑同一行。）`create-new-feature.sh` 建出 `specs/001-room-booking/spec.md`（spec 模板的複本）和 `.specify/feature.json`，1.2.5 的 `speckit-specify` skill 第 3 步做的也是這兩件事。我們不跑 `/speckit-specify`，直接蓋掉 spec 和 init 從模板複製、還沒填的 constitution：本章要審的是 plan，輸入固定，結果才能比。兩個檔的內容（`tail -n +1` 的輸出）：

```markdown
==> specs/001-room-booking/spec.md <==
# Feature Specification: Room Booking
**Feature Branch**: `001-room-booking` | **Created**: 2026-11-02 | **Status**: Draft

## User Scenarios & Testing *(mandatory)*
### User Story 1 - Book a room (Priority: P1)
Alice books a meeting room for a slot today and sees at once whether she got it.
**Acceptance Scenarios**:
1. **Given** room "Taipei" is free all day, **When** Alice books "Taipei" from 10:00 to 11:00, **Then** the booking is confirmed
2. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice books "Taipei" from 10:30 to 11:30, **Then** the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
3. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice books "Taipei" from 11:00 to 12:00, **Then** the booking is confirmed

### User Story 2 - See today's bookings (Priority: P2)
Carol checks who holds which room before she walks over.
**Acceptance Scenarios**:
1. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Carol lists the bookings, **Then** she sees Taipei 10:00-11:00 held by Bob

### Edge Cases
- 10:00-11:00 and 11:00-12:00 do not overlap: intervals are half-open [start, end).
- A slot whose end is not after its start is rejected.

## Requirements *(mandatory)*
### Functional Requirements
- **FR-001**: System MUST let a person book a room (Taipei, Tokyo, Seoul) for a same-day HH:MM slot.
- **FR-002**: System MUST reject an overlapping booking of the same room with "Taipei is taken 10:00-11:00 by Bob" (R1).
- **FR-003**: System MUST reject a slot whose end is not after its start with "end must be after start" (R2).
- **FR-004**: System MUST list today's bookings with room, slot and holder.
- **FR-005**: People MUST be able to book from one web page that shows "Booked Taipei" or "Rejected: <reason>".

### Key Entities
- **Room**: Taipei, Tokyo or Seoul.
- **Booking**: a room held by one person for a half-open interval today; has an id.

## Success Criteria *(mandatory)*
- **SC-001**: Every acceptance scenario above passes against both the HTTP API and the web page.
- **SC-002**: The person learns the outcome in the same response, without reloading the page.

## Assumptions
- One day, one office, no time zones. No login: who you are is the name you type.
- Bookings may be lost when the server restarts (training system).

==> .specify/memory/constitution.md <==
# Booking Constitution
## Core Principles
### I. Test-First
Every functional requirement gets an acceptance check that fails before the code exists.
### II. Minimal Dependencies
Runtime dependencies: FastAPI, uvicorn and the standard library. Anything else needs a Complexity Tracking row.
### III. One Deployable
One Python package, `booking/`, serves both the API and the page. No second service, no frontend build step.
### IV. Rule IDs Everywhere
Business rules are called R1-R4 and are cited by ID in specs, plans and tests.
## Governance
A plan records PASS or VIOLATION for every principle twice: before research and after design.
Every VIOLATION has a Complexity Tracking row. Amending this file needs its own reviewed PR.
**Version**: 1.0.0 | **Ratified**: 2026-11-02 | **Last Amended**: 2026-11-02
```

兩個細節之後會用到。Governance 的「每條原則判兩次」是我們訂的格式：模板只寫 `[Gates determined based on constitution file]`，沒規定怎麼記錄，而 constitution 是 plan skill 一定會讀的輸入，格式約定放這裡 agent 才看得到。另外，constitution 管整個專案、提到 R1–R4，這份 spec 只涵蓋 R1、R2（取消預約的 R4 在[練習 D](./practice-d-cancel-booking.md)）；第七節的 agent 注意到了這個落差。兩個檔寫好、還沒寫 plan 之前，先在 `ch13-plan/` 下 `cp -R booking scratch`，第六節要用這份複本。

## 二、模板與 skill 逐段讀

1.2.5 的 `plan-template.md` 有 113 行、五個區塊，每塊的原文與用意：

| 區塊 | 模板原文 | 防的是什麼 | 典型壞法 |
|---|---|---|---|
| Summary | `[Extract from feature spec: primary requirement + technical approach from research]` | 讀者一眼看出做什麼、怎麼做 | 把 spec 重抄一遍 |
| Technical Context | 9 個欄位，形如 `[e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]`（只有 Storage 是 `or N/A`） | 把「不知道」變成數得出來的研究清單 | 猜一個值填上去，看起來很確定 |
| Constitution Check | `*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*` 加上 `[Gates determined based on constitution file]` | 研究可能把違規帶進來，例如選了一個新資料庫 | 只判一次、全 PASS、沒有證據 |
| Project Structure | 「The delivered plan must not include Option labels.」 | tasks 需要真的檔案路徑 | 留著 `Option 1/2/3` |
| Complexity Tracking | 「Fill ONLY if Constitution Check has violations that must be justified」，三欄 Violation／Why Needed／Simpler Alternative Rejected Because | 例外要有人負責 | 照抄範例列，或有違規卻空白 |

Complexity Tracking 的範例列 `[e.g., 4th project]`、`[why 3 projects insufficient]` 透露了它的來歷。Spec Kit 2025-08-22 的第一版 `plan-template.md` 把 Simplicity、Architecture、Testing 等五組檢查寫死在 Constitution Check 裡，第一條就是 `Projects: [#] (max 3 - e.g., api, cli, tests)`；2025-09-16 的 commit `b18ef208` 才換成 `[Gates determined based on constitution file]`，範例列一路留到 1.2.5（`gh api` 查這個檔的 commit 歷史可確認）。原始碼裡的方法論文件 `spec-driven.md` 描述的仍是寫死的版本：「Phase -1: Pre-Implementation Gates」，Simplicity Gate 問「Using ≤3 projects?」，對應 constitution 的 Article VII「Maximum 3 projects」，文件稱之為「compile-time checks for architectural principles」。現在 gate 從**你的** constitution 推出來，constitution 因此成了參數層（[Ch 17](./17-constitution-and-presets.md) 的主題）。「compile-time」則言過其實，底層機制一節會說明。

`.claude/skills/speckit-plan/SKILL.md`（169 行）的骨幹，原文：

```markdown
2. **Load context**: Read FEATURE_SPEC and `.specify/memory/constitution.md`. Load IMPL_PLAN template (already copied).
3. **Execute plan workflow**: Follow the structure in IMPL_PLAN template to:
   - Fill Technical Context (mark unknowns as "NEEDS CLARIFICATION")
   - Fill Constitution Check section from constitution
   - Evaluate gates (ERROR if violations unjustified)
   - Phase 0: Generate research.md (resolve all NEEDS CLARIFICATION)
   - Phase 1: Generate data-model.md, contracts/, quickstart.md
   - Re-evaluate Constitution Check post-design
…（略）
- ERROR on gate failures or unresolved clarifications
```

- **constitution 一定讀**：第 2 步沒有條件；`speckit-specify`、`clarify`、`checklist`、`tasks`、`implement`、`taskstoissues` 寫的都是「IF EXISTS」（grep 已安裝的 SKILL.md 可確認，[Ch 12](./12-spec-kit-1x.md) 有整張表）。另一個無條件讀的是 `speckit-analyze`，它把 constitution 衝突一律列為 CRITICAL（`converge` 也把它當最高權威，但寫的是 if present）；plan 則是 constitution 第一次直接左右設計的地方。
- **Phase 0 的題目有三個來源**：每個 NEEDS CLARIFICATION、每個依賴（best practices）、每個整合（patterns），輸出格式是 `Decision` / `Rationale` / `Alternatives considered`。skill 說要「dispatch research agents」，在 Claude Code 裡就是 subagent，前提是 agent 拿得到派工的工具。
- **Phase 1 有三條限制**：data-model 寫欄位、關係、來自需求的驗證規則與狀態轉移；contracts 只在有對外介面時寫（「Skip if project is purely internal」，練習 E 那份多餘的 `api-spec.json` 就是沒守這條）；quickstart 是驗證指南（validation guide），「Do not include full implementation code」（CHANGELOG 0.9.3 有一條「clarify quickstart validation guide scope (#2805)」）。
- **到 Phase 1 為止**：「Command ends after Phase 1 design.」`tasks.md` 是 Phase 2，屬於 `/speckit-tasks`。結尾的 Done When 三個勾選框是「流程跑完」「hook 處理了」「回報了路徑」，沒有一條關於內容對不對。

填好的樣子，`specs/001-room-booking/plan.md`。幾個選擇：Performance Goals 寫 `N/A, the spec sets none` 而不是 `NEEDS CLARIFICATION`，因為「確定沒有要求」不是未知數；研究過的值後面掛 `(research D#)`，讀者知道理由在哪；gate 表兩欄分開判、附證據；Complexity Tracking 留標題寫「No violations」，不刪、也不留範例列，reviewer 看得出這一段有人想過。

````markdown
# Implementation Plan: Room Booking
**Branch**: `001-room-booking` | **Date**: 2026-11-02 | **Spec**: [spec.md](./spec.md)

## Summary
People book a room for a same-day slot (FR-001) through an HTTP API and one web page (FR-005). Overlaps (R1, FR-002)
and reversed slots (R2, FR-003) are rejected with the spec's exact messages; today's bookings can be listed (FR-004).
A pure `Schedule` class owns the rules, FastAPI exposes it, and the page calls the API with `fetch` (research D3).

## Technical Context
**Language/Version**: Python 3.12
**Primary Dependencies**: runtime FastAPI 0.162 + uvicorn 0.54; dev only pytest 9.2, pytest-bdd 9.0.1, Playwright 1.64.1
**Storage**: in-memory list inside one `Schedule` (research D2)
**Testing**: the same pytest-bdd scenarios run through the API and through the page (SC-001)
**Target Platform**: local macOS/Linux dev server
**Project Type**: web-service plus one HTML page served by the same app
**Performance Goals**: N/A, the spec sets none
**Constraints**: same-day HH:MM, half-open intervals [start, end) (research D1)
**Scale/Scope**: 3 rooms, a handful of people, one day

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Before research | After design | Evidence |
|---|---|---|---|
| I. Test-First | PASS | PASS | quickstart.md has a check for every acceptance scenario |
| II. Minimal Dependencies | PASS | PASS | runtime = fastapi + uvicorn; everything else is dev-only |
| III. One Deployable | PASS | PASS | `booking/api.py` serves `/` and `/bookings` |
| IV. Rule IDs Everywhere | PASS | PASS | contracts name R1/R2 on every rejection |

## Project Structure
```text
specs/001-room-booking/  plan.md research.md data-model.md quickstart.md contracts/interfaces.md
booking/                 schedule.py (rules, no FastAPI import), api.py (routes), static/index.html
tests/                   features/booking.feature, step_defs/ (api + ui drivers), unit/test_schedule.py
```
**Structure Decision**: one Python project plus `static/`; the page is one file, so no frontend project (Principle III).

## Complexity Tracking
No violations; nothing to justify.
````

## 底層機制：setup-plan.sh 只複製一個檔，閘門沒有人執行

`/speckit-plan` 裡只有第 1 步是程式：跑 `setup-plan.sh --json`，parse 出 `FEATURE_SPEC`、`IMPL_PLAN`、`FEATURE_DIR`、`BRANCH`。在另一個剛 `specify init`、還沒建任何 feature 的專案 `mech/` 依序實跑（2026-11-02）：

```
$ bash .specify/scripts/bash/setup-plan.sh --json; echo exit=$?
ERROR: Feature directory not found. Set SPECIFY_FEATURE_DIRECTORY or run the specify command to create .specify/feature.json.
ERROR: Failed to resolve feature paths
exit=1
$ bash .specify/scripts/bash/create-new-feature.sh --json --short-name room-booking 'Book a room, reject overlaps' 2>/dev/null
{"BRANCH_NAME":"001-room-booking","SPEC_FILE":"/tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking/spec.md","FEATURE_NUM":"001"}
$ bash .specify/scripts/bash/setup-plan.sh --json
Copied plan template to /tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking/plan.md
{"FEATURE_SPEC":"/tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking/spec.md","IMPL_PLAN":"/tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking/plan.md","FEATURE_DIR":"/tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking","BRANCH":"001-room-booking"}
$ bash .specify/scripts/bash/setup-plan.sh --json 2>&1 >/dev/null
Plan already exists at /tmp/lab/aibdd/ch13-plan/mech/specs/001-room-booking/plan.md, skipping template copy
$ SPECIFY_FEATURE_DIRECTORY=specs/002-typo bash .specify/scripts/bash/setup-plan.sh --json >/dev/null; echo exit=$?
Copied plan template to /tmp/lab/aibdd/ch13-plan/mech/specs/002-typo/plan.md
exit=0
$ cat .specify/feature.json; ls specs/002-typo
{"feature_directory":"specs/002-typo"}
plan.md
```

`common.sh` 的 `get_feature_paths` 依序找 feature：有環境變數 `SPECIFY_FEATURE_DIRECTORY` 就用它，並寫回 `.specify/feature.json`；沒有就讀 `.specify/feature.json`；兩者都沒有就報第一個指令那個錯。它**不看 git 分支**（`mech/` 連 `git init` 都沒做）。`--json` 模式把狀態訊息印到 stderr、JSON 印到 stdout（第四個指令丟掉 stdout 後只剩狀態行），agent 才能直接 parse stdout。最後兩個指令是真實的坑：環境變數打錯一個字，`setup-plan.sh` 照樣 exit 0，在**沒有 spec.md** 的資料夾建出 plan.md，還把 `feature.json` 改指過去，之後的 `/speckit-tasks` 跟著走錯；腳本不檢查 spec.md 在不在，要等 agent 讀 FEATURE_SPEC 讀不到才會發現。

剩下每一步，包括填 Technical Context、判 gate、寫 research、寫 Phase 1、再判一次，都是模型讀了 SKILL.md 之後**自己決定照不照做**。沒有程式在「研究之前」停下來檢查 gate，也沒有程式比對兩次判定。第七節那次真實的 run，agent 先寫 research.md 和四份 Phase 1 檔案，**最後**才寫 plan.md：「Before research」那一欄是研究完才填的。模板說它是閘門，流程裡它是一張表單。要真的擋，得換成程式：把本章的 `plan_lint.py` 接進 hook 或 CI（[Ch 3](./03-propose-constrain-feedback.md) 的回饋迴圈、[Ch 25](./25-tdd-in-sdd-pipeline.md) 的 RED 閘門），或把 /plan 拆成有先後的步驟（[Ch 15](./15-research-and-analysis-skills.md)）。

和 SDD 課用的 0.11.10 相比（對照 [Ch 12](./12-spec-kit-1x.md) 實驗室裡兩個版本 `specify init` 的實際 diff）：0.11.10 的 `setup-plan.sh` 默默收下不認得的參數，1.2.5 回 `ERROR: Unknown option '--dry-run'`、exit 1（CHANGELOG 1.0.3）；找不到模板時 0.11.10 印 Warning 再 `touch` 一個空的 plan.md，1.2.5 報錯、不建檔，空模板也拒絕（1.2.1）；JSON 的目錄欄位從 `SPECS_DIR` 改名 `FEATURE_DIR`（1.0.5）；0.11.10 的 skill 還有「更新 CLAUDE.md 的 agent context」一步，1.2.5 拿掉了（0.12.0 改成 opt-in extension）。

## 三、Phase 0 與 Phase 1：從 spec 推出每一份產物

| spec 的哪一段 | 推出的產物 | skill 的推導規則 | 本章的 lint |
|---|---|---|---|
| Technical Context 的未知數、依賴、整合 | research.md | 一題一個 Decision / Rationale / Alternatives considered | PL-03、PL-04、PL-05 |
| Key Entities 與 FR 的驗證規則 | data-model.md | 實體、欄位、關係、驗證規則、狀態轉移 | PL-07 |
| 使用者動作與對外介面（FR） | contracts/ | 系統對使用者或其他系統暴露的每個介面 | PL-01 |
| Acceptance Scenarios 與 SC | quickstart.md | 可執行的驗證情境、指令與預期結果，不放實作 | PL-08 |

四個檔放在 `specs/001-room-booking/` 下（`tail -n +1` 的輸出）。quickstart 的指令要等程式照這份計畫寫出來才跑得動，本章沒有跑；後面章節真正寫出的程式也不全照它走（[Ch 22](./22-backend-e2e-first.md) 用 `create_app` 工廠、SQLite 與 `BOOKING_TEST_MODE`，[Ch 23](./23-frontend-e2e-playwright.md) 的 `/reset` 沒有開關），計畫本來就會和實作分岔。在本章它是被審的文件：

```markdown
==> research.md <==
# Research: Room Booking
## D1 Time and overlap (FR-001, FR-002, FR-003)
- **Decision**: parse "HH:MM" into `datetime.time`; two bookings of one room overlap when `a.start < b.end and b.start < a.end`.
- **Rationale**: half-open intervals keep 10:00-11:00 and 11:00-12:00 legal; `time` objects compare as times.
- **Alternatives considered**: comparing the strings (`"9:00" > "10:00"` is True); minutes since midnight as an int (works, but we lose pydantic's free validation).
## D2 Storage (FR-004)
- **Decision**: keep bookings in a list inside one `Schedule` object per process.
- **Rationale**: the spec's assumptions allow losing bookings on restart; Principle II limits runtime dependencies.
- **Alternatives considered**: SQLite through stdlib `sqlite3` (no new dependency, but schema work nobody asked for yet); PostgreSQL (breaks Principle II).
## D3 How the page talks to the API (FR-005, SC-002)
- **Decision**: `GET /` returns `static/index.html`; its script calls `POST /bookings` with `fetch` and writes the outcome into `<p role=status>`.
- **Rationale**: same origin, no CORS, no build step (Principle III); the API's `detail` text reaches the page unchanged (SC-002).
- **Alternatives considered**: a plain HTML form POST (reloads the page, against SC-002); a React/Vite frontend (a build step for one form).
## D4 Test isolation (SC-001)
- **Decision**: a test-only `POST /reset` clears the `Schedule`; it exists only when `BOOKING_TEST=1`.
- **Rationale**: the UI driver talks to a running server, so it cannot swap in a fresh app object per scenario.
- **Alternatives considered**: a fresh app object per test (works for the API driver only); restarting uvicorn per scenario (a process start for every scenario).

==> data-model.md <==
# Data Model: Room Booking
## Room
A value, not a table: one of `Taipei`, `Tokyo`, `Seoul` (FR-001).
## Booking
| Field | Type | Rule |
|---|---|---|
| id | int | assigned from 1 upward, never reused (FR-004) |
| room | Room | required (FR-001) |
| start, end | `datetime.time` | `end > start`, else R2 "end must be after start" (FR-003) |
| by | str | non-empty name; there is no login (spec Assumptions) |

Invariant (R1, FR-002): no two bookings of one room overlap; intervals are half-open [start, end).

==> contracts/interfaces.md <==
# Contracts: Room Booking (FR-001 to FR-005)
Errors use FastAPI's `{"detail": "<text>"}` with the spec's exact text; the page shows that text unchanged (SC-002).

| Interface | Success | Rejected |
|---|---|---|
| `POST /bookings`: `room` (Taipei/Tokyo/Seoul), `start`, `end` ("HH:MM"), `by` | 201, the booking plus `id` from 1 (FR-001) | 409 R1 `Taipei is taken 10:00-11:00 by Bob` (FR-002); 422 R2 `end must be after start` (FR-003) |
| `GET /bookings` | 200, every booking today (FR-004) | none |
| `POST /reset`, only when `BOOKING_TEST=1` | 204, schedule cleared (SC-001, research D4) | none |
| `GET /` page: textboxes `Room` `Start` `End` `Name`, button `Book` | `<p role=status>` says `Booked Taipei` | `Rejected: <detail>` |

==> quickstart.md <==
# Quickstart: Room Booking
A validation guide, not an implementation. Start the app with `BOOKING_TEST=1 uvicorn booking.api:app --port 0`
(the OS picks a free port; uvicorn prints it). Bodies and errors follow [contracts/interfaces.md](./contracts/interfaces.md).

| Check | Covers | Do | Expect |
|---|---|---|---|
| Q1 | US1 scenario 1 | POST /bookings Taipei 10:00-11:00 by Bob | 201 with `"id": 1` |
| Q2 | US1 scenario 2 | POST Taipei 10:30-11:30 by Alice | 409 `Taipei is taken 10:00-11:00 by Bob` |
| Q3 | US1 scenario 3 | POST Taipei 11:00-12:00 by Alice | 201 |
| Q4 | FR-003 | POST Taipei 11:00-10:00 by Alice | 422 `end must be after start` |
| Q5 | US2 | GET /bookings | Bob's Taipei 10:00-11:00 is listed |
| Q6 | SC-001, SC-002 | `pytest tests --driver ui` | every scenario passes in Chromium, no page reload |
Between runs: POST /reset.
```

- **D1 的備選是真的對手**：字串比較正是 [Ch 2](./02-vibe-coding-vs-engineering.md) 的 B2 bug（`"9:00" > "10:00"` 為 True，本章在 Python 3.12 實測）；分鐘整數行得通，寫的是取捨，不是貶低。
- **Room 是值，Booking 是實體**：Booking 有 `id`，Room 沒有身分。換成 OpenAPI，前者是帶 `required` 的 schema、後者是 inline 的值（一個 enum），正是 [SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md)「把領域模型對應到 OpenAPI」的規則；本章為了篇幅用 markdown 表格寫契約，agent 那次也是。
- **FR-005 推出一份 UI 契約**：可存取名稱（accessible name）`Room`、`Book`、`role=status` 是 [Ch 23](./23-frontend-e2e-playwright.md) 的 Playwright locator 和 [Ch 30](./30-one-gherkin-two-drivers.md) 的 UI driver 要依賴的東西；寫進契約，下游才不會各自發明。這份契約還有兩處沒寫好：網頁那列沒有引用 FR-005（它只出現在標題的範圍裡），錯誤說明那行尾巴掛著只屬於本 feature 的 `(SC-002)`。第五節的 plan_lint 只查 FR 有沒有在資料夾裡出現，放過了它們；[Ch 14](./14-artifact-first.md) 的契約檢查器第一次跑就抓到，在那裡修。
- **D4 沒有對應的 FR**：測試用的基礎設施 spec 不會提，它錨定在 SC-001（兩個 driver 都要跑），不算範疇膨脹。錨不到 spec、也錨不到 constitution 的決策才是。

## 四、review 清單：哪幾條交給腳本

| # | 檢查 | 腳本判（`plan_lint.py`） | 人判 | 本章出錯的例子 |
|---|---|---|---|---|
| 1 | 產物齊全 | PL-01：四個區塊、research、data-model、quickstart、contracts/ 都在 | 該不該有（純內部專案可以沒有 contracts） | 剛複製的模板 |
| 2 | 模板殘留 | PL-02：佔位符、`ACTION REQUIRED`、`Option N:` | 無 | flawed |
| 3 | 未知數 | PL-03：沒有殘留的 NEEDS CLARIFICATION | 決定有沒有根據 | flawed 的 Storage |
| 4 | research 條目 | PL-04：三欄都在、備選不是 None | 備選回答同一個問題、是真的對手 | flawed 的 D5；near-miss 的 D3 |
| 5 | 追溯 | PL-05：每個 FR/SC 有人處理、沒有 spec 外的 ID、每個決策有錨 | 決策真的滿足它錨的那條需求 | flawed 的 FR-007；near-miss 的閉區間 |
| 6 | constitution gate | PL-06：每條原則判兩次、VIOLATION 有 Complexity Tracking | PASS 是真的，對得上 Technical Context | flawed 的 II；near-miss 的 PostgreSQL |
| 7 | data-model、quickstart | PL-07、PL-08：每個實體、每個 story 都在，quickstart 不放實作 | 欄位規則、預期結果和 spec 原句一致 | flawed 少了 Room、US2 |
| 8 | 對下游與環境 | 無 | 欄位名、錯誤格式、port 對得上下游與這台機器 | agent 的 `reason`、port 8000 |

分界線是「判斷需不需要讀懂意思」。腳本那欄是結構與對應，判得又快又穩；人那欄要知道備選方案合不合理、區間該開該閉、PostgreSQL 違不違反 Principle II、這台機器的 port 8000 有沒有人用，只有人判得了（或交給另一個被嚴格約束的 LLM，再由人抽查）。[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的 Plan+Tasks Review PR 該花時間的是人那欄。

## 五、plan_lint.py

`/tmp/lab/aibdd/ch13-plan/plan_lint.py`，125 行，只用標準函式庫：

```python
#!/usr/bin/env python3
"""plan_lint.py: check a Spec Kit plan folder against its spec.md and the constitution.
Only what a script can decide (PL-01..PL-08). Exit 0 = clean, 1 = problems, 2 = usage error."""
import argparse, os, re, sys
from pathlib import Path

SECTIONS = ["Summary", "Technical Context", "Constitution Check", "Project Structure"]
ID = re.compile(r"\b(?:FR|SC)-\d{3}\b")
ANCHOR = re.compile(r"\b(?:FR|SC)-\d{3}\b|\bR\d\b|\bPrinciple [IVX]+\b")
# an unknown is a [marker], a **Field**: value or a table cell, not prose that mentions the phrase
UNKNOWN = re.compile(r"\[NEEDS CLARIFICATION|^\s*(?:[-*]\s*)?\*\*[^*]+\*\*\s*:.*NEEDS CLARIFICATION"
                     r"|^\s*\|.*NEEDS CLARIFICATION")

def section(text, heading):  # body under a heading, up to the next heading of the same level
    lvl = len(heading) - len(heading.lstrip("#"))
    m = re.search(rf"^{re.escape(heading)}\b.*?$(.*?)(?=^#{{1,{lvl}}} |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""

def field(body, name):  # "- **Decision**: text" -> "text"
    m = re.search(rf"^[\s*-]*\**{name}\**\s*:\**\s*(.*)$", body, re.I | re.M)
    return m.group(1).strip() if m else ""

def lineno(text, needle):
    return next((i for i, l in enumerate(text.splitlines(), 1) if needle in l), 0)

def lint(fdir, constitution, template):
    probs = []
    def bad(rule, path, msg, line=0):
        probs.append(f"{os.path.relpath(path)}{f':{line}' if line else ''}: {rule} {msg}")
    spec, const = (fdir / "spec.md").read_text(), constitution.read_text()
    docs = {p.relative_to(fdir).as_posix(): p.read_text() for p in sorted(fdir.rglob("*")) if p.is_file()
            and not p.relative_to(fdir).as_posix().startswith(("spec.md", "tasks.md", "checklists/"))}
    plan, P, research, R = docs.get("plan.md", ""), fdir / "plan.md", docs.get("research.md", ""), fdir / "research.md"
    # PL-01 the parts /speckit-plan must produce (contracts may be skipped only for purely internal projects)
    need = [f"'## {s}' in plan.md" for s in SECTIONS if not re.search(rf"^## {s}\b", plan, re.M)]
    need += [f for f in ("research.md", "data-model.md", "quickstart.md") if f not in docs]
    need += [] if any(n.startswith("contracts/") for n in docs) else ["contracts/"]
    for f in need:
        bad("PL-01", fdir, f"missing {f}: Phase 0/1 of /speckit-plan must produce it")
    # PL-02 template residue: the template's own [placeholders], ACTION REQUIRED, Option labels
    marks = set(re.findall(r"\[[^\]\n]{3,}\](?!\()", template.read_text()))
    for i, line in enumerate(plan.splitlines(), 1):
        hits = [(line.find(m), m) for m in marks if m in line]
        hits += [(x.start(), x.group()) for x in re.finditer(r"ACTION REQUIRED|\bOption \d+:", line)]
        if hits:  # report the leftmost hit, so the output does not depend on set order
            bad("PL-02", P, f"template text left in the plan: {min(hits)[1][:50]}", i)
    # PL-03 unknowns that Phase 0 should have resolved
    for name, text in docs.items():
        for i, line in enumerate(text.splitlines(), 1):
            if UNKNOWN.search(line):
                bad("PL-03", fdir / name, "unresolved NEEDS CLARIFICATION: research it, record the decision", i)
    # PL-04 research entries (## sections that have a Decision) need all three fields
    parts = re.split(r"^## (.+)$", research, flags=re.M)
    entries = [(t, b) for t, b in zip(parts[1::2], parts[2::2]) if field(b, "Decision")]
    for title, body in entries:
        for f in ("Decision", "Rationale", "Alternatives considered"):
            if not field(body, f):
                bad("PL-04", R, f"'{title}' has no '{f}:'", lineno(research, title))
        if re.fullmatch(r"(none|n/?a|-)\.?", field(body, "Alternatives considered"), re.I):
            bad("PL-04", R, f"'{title}': name the options you rejected and why", lineno(research, title))
    # PL-05 traceability: every spec ID addressed, no invented IDs, every decision has an anchor
    principles = re.findall(r"^### ([IVX]+)\. (.+?)\s*$", const, re.M)
    spec_ids, seen = set(ID.findall(spec)), {}
    known = spec_ids | set(re.findall(r"\bR\d\b", spec)) | {f"Principle {n}" for n, _ in principles}
    for name, text in docs.items():
        for i, line in enumerate(text.splitlines(), 1):
            for x in ID.findall(line):
                seen.setdefault(x, (name, i))
    for x in sorted(spec_ids - seen.keys()):
        bad("PL-05", fdir / "spec.md", f"{x} is not addressed anywhere in the plan folder", lineno(spec, x))
    for x in sorted(seen.keys() - spec_ids):
        bad("PL-05", fdir / seen[x][0], f"{x} is not in spec.md (scope creep or a stale ID)", seen[x][1])
    for title, body in entries:
        if not set(ANCHOR.findall(title + body)) & known:
            bad("PL-05", R, f"'{title}' cites no FR/SC, rule or principle: who needs it?", lineno(research, title))
    # PL-06 constitution gate: every principle judged twice, every VIOLATION justified
    if not principles:
        bad("PL-06", constitution, "no '### I. Name' principles: is the constitution still the template?")
    gate, tracking = section(plan, "## Constitution Check"), section(plan, "## Complexity Tracking")
    for num, name in principles:
        row = next((l for l in gate.splitlines() if name.lower() in l.lower()), None)
        verdicts = re.findall(r"\b(PASS|VIOLATION)\b", row or "")
        if row is None:
            bad("PL-06", P, f"principle {num}. {name} is missing from the Constitution Check")
        elif len(verdicts) < 2:
            bad("PL-06", P, f"{num}. {name}: record PASS/VIOLATION before research AND after design", lineno(plan, row))
        if "VIOLATION" in verdicts and name.lower() not in tracking.lower():
            bad("PL-06", P, f"{num}. {name} is a VIOLATION with no Complexity Tracking row", lineno(plan, row))
    # PL-07 every Key Entity of the spec has its own section in data-model.md
    model = docs.get("data-model.md", "")
    for e in re.findall(r"^- \*\*(.+?)\*\*", section(spec, "### Key Entities"), re.M):
        if not re.search(rf"^##+ {re.escape(e)}\b", model, re.M):  # not the '# Data Model: ...' title
            bad("PL-07", fdir / "data-model.md", f"entity '{e}' from spec.md has no section")
    # PL-08 quickstart validates every user story and holds no implementation code
    quick = docs.get("quickstart.md", "")
    for n in re.findall(r"^### User Story (\d+)", spec, re.M):
        if not re.search(rf"\b(?:US|(?:User )?Story ){n}\b", quick):
            bad("PL-08", fdir / "quickstart.md", f"no validation step for User Story {n} (US{n})")
    for m in re.finditer(r"^```.*?$(.*?)^```", quick, re.M | re.S):
        if re.search(r"^\s*(def|class) \w", m.group(1), re.M):
            bad("PL-08", fdir / "quickstart.md", "implementation code: keep commands and expected results",
                quick[:m.start()].count("\n") + 1)
    return probs

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("feature_dirs", nargs="+", type=Path, help="specs/NNN-name folders")
    ap.add_argument("--constitution", type=Path, help="default: <root>/.specify/memory/constitution.md")
    ap.add_argument("--template", type=Path, help="default: <root>/.specify/templates/plan-template.md")
    a, probs = ap.parse_args(), []
    for fdir in a.feature_dirs:  # root = nearest parent with .specify/, as Spec Kit's scripts do
        root = next((d for d in [fdir.resolve(), *fdir.resolve().parents] if (d / ".specify").is_dir()), None)
        const = a.constitution or (root and root / ".specify/memory/constitution.md")
        tmpl = a.template or (root and root / ".specify/templates/plan-template.md")
        if not all(p and p.is_file() for p in (fdir / "spec.md", const, tmpl)):
            print(f"plan_lint: {fdir}: need spec.md, a constitution and plan-template.md "
                  "(run inside a specify project or pass --constitution/--template)", file=sys.stderr)
            return 2
        probs += lint(fdir, const, tmpl)
    n, k = len(a.feature_dirs), len(probs)
    print("\n".join(probs + [f"{n} plan{'s' * (n != 1)}, {k} problem{'s' * (k != 1)}"]))
    return 1 if probs else 0

if __name__ == "__main__":
    sys.exit(main())
```

三個設計選擇。**PL-02 讀模板本身**：殘留佔位符不是寫死的清單，而是從 `plan-template.md` 抽出所有 `[...]`（1.2.5 有 25 個），Spec Kit 改模板，規則自動跟上，這和 [Ch 10](./10-templates-and-scripts.md) 從模板反推檢查同一個思路。**錨點不只 FR/SC**：決策可以錨在 spec 的規則 ID（`R1`）或 constitution 的原則（`Principle II`），因為 Phase 0 的題目本來就有一部分來自依賴與整合。原則只認這個寫法，PL-06 也只從模板那種 `### II. 名稱` 標題找原則，寫成「constitution II」或 `## II.` 都對不上（[Ch 15](./15-research-and-analysis-skills.md) 踩雷 6 的 lab 就是這樣）。**沒有 Decision 的 `##` 段落視為備註**，不檢查：agent 常在 research.md 寫一段「驗證狀態」之類的說明。在專案外跑（沒有 `.specify/`、也沒給 `--constitution`），它印出 `plan_lint: <dir>: need spec.md, a constitution and plan-template.md …` 並 exit 2。

## 六、四份計畫實跑

除了好計畫，再準備三份：`scratch/` 是第一節留下的複本，在它裡面跑一次 `setup-plan.sh`，plan.md 就是原封不動的模板；`-flawed` 與 `-nearmiss` 是好計畫資料夾的複本（`cp -R specs/001-room-booking specs/001-room-booking-flawed`），各自套用下表的改動。字串照抄、位置照表上寫的放，下面的行號才對得上（實驗室裡用一支小腳本 `make_variants.py` 做同樣的字串替換）：

| 變體 | 檔案 | 改動 | 該被哪條抓到 |
|---|---|---|---|
| flawed | plan.md | Storage 的值改成 `NEEDS CLARIFICATION`；Summary 最後一行的行尾（同一行）接上 ` Weekly repeats (FR-007) reuse it.` | PL-03；PL-05 |
| flawed | plan.md | II 那列改成 `PASS`、`VIOLATION`、`Celery + Redis send the confirmation e-mail`；`booking/` 那行前插入兩行 `# [REMOVE IF UNUSED] Option 2: Web application`、`frontend/src/App.tsx` | PL-06；PL-02 |
| flawed | research.md | 結尾空一行，再加四行：`## D5 Notifications`、`- **Decision**: e-mail a confirmation through Celery + Redis.`、`- **Rationale**: people like a receipt.`、`- **Alternatives considered**: None.` | PL-04、PL-05 |
| flawed | data-model.md | 刪掉 `## Room` 與它下面那一行 | PL-07 |
| flawed | quickstart.md | 刪掉 Q5（US2）那列；結尾空一行，再加一個 python 圍欄區塊，內容是 `def test_overlap(client):` 與縮排四格的 `...` | PL-08 ×2 |
| near-miss | data-model.md | Invariant 改成閉區間 `[start, end]`，`a.start <= b.end and b.start <= a.end` 算衝突 | 抓不到 |
| near-miss | plan.md、research.md | Storage 與 D2 的 Decision 改成 PostgreSQL 16 + SQLAlchemy 2.0，gate 照舊全 PASS | 抓不到 |
| near-miss | research.md | D3 的 React/Vite 備選換成 `a native iOS app; an Electron desktop app.` | 抓不到 |

在 `/tmp/lab/aibdd/ch13-plan/`、啟用 course venv 之後：

```
$ python plan_lint.py booking/specs/001-room-booking
1 plan, 0 problems
$ python plan_lint.py booking/specs/001-room-booking-flawed
booking/specs/001-room-booking-flawed/plan.md:33: PL-02 template text left in the plan: [REMOVE IF UNUSED]
booking/specs/001-room-booking-flawed/plan.md:12: PL-03 unresolved NEEDS CLARIFICATION: research it, record the decision
booking/specs/001-room-booking-flawed/research.md:19: PL-04 'D5 Notifications': name the options you rejected and why
booking/specs/001-room-booking-flawed/plan.md:7: PL-05 FR-007 is not in spec.md (scope creep or a stale ID)
booking/specs/001-room-booking-flawed/research.md:19: PL-05 'D5 Notifications' cites no FR/SC, rule or principle: who needs it?
booking/specs/001-room-booking-flawed/plan.md:26: PL-06 II. Minimal Dependencies is a VIOLATION with no Complexity Tracking row
booking/specs/001-room-booking-flawed/data-model.md: PL-07 entity 'Room' from spec.md has no section
booking/specs/001-room-booking-flawed/quickstart.md: PL-08 no validation step for User Story 2 (US2)
booking/specs/001-room-booking-flawed/quickstart.md:14: PL-08 implementation code: keep commands and expected results
1 plan, 9 problems
$ python plan_lint.py booking/specs/001-room-booking-nearmiss
1 plan, 0 problems
```

`python plan_lint.py scratch/specs/001-room-booking` 回 `1 plan, 51 problems`：PL-02 24 個、PL-03 8 個、PL-05 7 個、PL-01 與 PL-06 各 4 個、PL-07 與 PL-08 各 2 個。lint 在這裡兼作待辦清單，告訴你 `setup-plan.sh` 交出來的還差什麼。好計畫 0 個；flawed 的 8 處改動全被抓到，共 9 個問題（exit 1）。near-miss 也是 **0 problems（exit 0）**，可是錯得比 flawed 更嚴重：閉區間讓 spec 的 US1 情境 3（11:00 接 10:00–11:00）被拒，和 D1 自己的半開區間互相矛盾；PostgreSQL 違反 Principle II，gate 卻寫 PASS，D2 的備選欄甚至還寫著「PostgreSQL (breaks Principle II)」；iOS app 和 Electron 回答的不是「網頁怎麼呼叫 API」。三個錯都通過了每一條結構規則，要靠清單人判那欄的第 4、5、6 列。

## 七、讓 agent 真的跑一次 /speckit-plan

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

`agent-run/` 是 booking 在寫 plan 之前的複本（當時 spec.md 與 constitution.md 的內容和上面相同，只多了空行，本章為了篇幅壓掉空行）。預先允許的只有讀寫與搜尋檔案、Skill、`setup-plan.sh`、`mkdir`、`ls`，其他需要詢問的操作一律自動拒絕：

```bash
cd /tmp/lab/aibdd/ch13-plan/agent-run
claude -p "/speckit-plan Python 3.12, FastAPI 0.162 + uvicorn 0.54, pytest 9.2 + pytest-bdd 9.0.1, Playwright 1.64.1 for the page." --max-turns 20 --max-budget-usd 4 --tools "Bash,Read,Write,Edit,Glob,Grep,Skill" --allowedTools "Read Write Edit Glob Grep Skill Bash(bash .specify/scripts/bash/setup-plan.sh:*) Bash(.specify/scripts/bash/setup-plan.sh:*) Bash(mkdir:*) Bash(ls:*)" --permission-prompts none --setting-sources project --strict-mcp-config --no-session-persistence --output-format stream-json --verbose > run1.jsonl
```

14 個 turn、169 秒、`total_cost_usd` 0.7845594，模型 claude-opus-5-5，寫出 6 個檔共 450 行（research.md 131、plan.md 96、quickstart.md 82、data-model.md 58、contracts/ 兩份共 83）。用 [Ch 4](./04-prompt-files-to-skills.md) 的 `timeline.jq` 攤開事件流：第一個 Bash 呼叫裡就跑了 `setup-plan.sh --json`；讀了 spec、constitution、模板，也讀了 `run1.jsonl`（log 寫在專案目錄裡，就成了上下文；log 應該放專案外）；想在 `/tmp` 開一個 `uv sync` 的 spike 驗證版本，被 `--permission-prompts none` 擋下；依序寫 research、data-model、兩份 contracts、quickstart，**最後才寫 plan.md**。`plan_lint.py` 回 `1 plan, 6 problems`：PL-04 四個（R-05、R-10、R-11、R-12 沒有 `Alternatives considered:`），PL-05 兩個（R-09 測試隔離、R-10 API driver 沒有錨點，補上「(SC-001)」即可）。照清單由人審，看到的是另一個層次：

- **它把 spec 的缺口攤開，而不是自己補**：constitution 提 R1–R4、spec 只有 R1、R2，它寫明「I didn't invent the other two」；4 個沒實測的決策標成「(verify)」，quickstart 前兩步就是用來驗它們的。FR-003 沒有驗收情境，它從 Edge Cases 補了一個並在報告裡說明；這其實是改 spec，該退回 spec 那一層定案（[Ch 18](./18-clarify-skill.md) 的 clarify）。
- **契約和本課的下游對不上**：欄位叫 `holder` 不叫 `by`，錯誤體是 `{"reason": ...}` 不是 `detail`，UI 契約用元素 id 而不是可存取名稱。照這份 spec 它沒做錯：spec 沒規定欄位名，它用的正是 spec 自己的字（FR-004 的 holder、FR-005 的 `Rejected: <reason>`）；但 [Ch 30](./30-one-gherkin-two-drivers.md) 的兩個 driver 依賴 `by`、`detail` 與 `Room`、`Book`。該修的是 spec 或 constitution，而不是靠 reviewer 記得。
- **quickstart 和這台機器對不上**：手動驗證那步用 `uvicorn booking.app:app`、打開 `http://127.0.0.1:8000/`；這台機器的 8000 長期被別的專案佔用（[Ch 0](./00-environment-setup.md) 踩雷 4，本章用 `lsof -nP -iTCP:8000 -sTCP:LISTEN` 再確認一次）。gate 則照 Governance 畫了兩欄、全部 PASS，但如前述，兩欄都是最後才填的。

## 對比與取捨

| 方式 | 抓得到 | 抓不到 | 成本 | 適用 |
|---|---|---|---|---|
| 人讀全文 | 語意、取捨、和下游與環境的衝突 | 漏掉的 ID、殘留模板，眼睛會滑過 | 高：這次 agent 交了 450 行 | 每份計畫一次，時間花在清單人判那欄 |
| `plan_lint.py` | 結構、殘留、未決、追溯、gate 的紀錄 | 意思對不對（near-miss 是 0 problems） | 本機實測每次 0.01–0.04 秒（含 Python 啟動），可放進 hook／CI | 每次改 plan |
| `/speckit-analyze` | spec、plan、tasks 之間的不一致；constitution 衝突一律列 CRITICAL | 要等 tasks.md 產生才能跑；本身是 LLM，每次結果不同 | 一次 agent run | tasks 之後、implement 之前 |

## 踩雷集錦

1. **以為 Constitution Check 是閘門** → 它是一張 agent 自己填的表。本章那次 run 先做完研究和設計，最後才填「Before research」那欄。要擋，就把檢查做成程式，放在 hook 或 CI。
2. **以為 lint 0 problems 就是好計畫** → near-miss 帶著三個語意錯誤拿到 0 problems。lint 只證明「有接上」，「接得對」要靠人。
3. **以為 lint 寫完就可信** → 這支 lint 自己出過三個錯：第一版在 agent 的產出上報了 18 個問題，12 個是我們的慣例造成的誤報（把「had no NEEDS CLARIFICATION items」這句敘述當成未決、不認得「Story 1」、把說明段落當成決策、不承認錨在 `R1` 或 `Principle II` 的決策）；種缺陷時發現刪掉 `## Room` 卻沒被抓到，因為標題 `# Data Model: Room Booking` 也含 Room；同一行有多個佔位符時，回報哪一個取決於 set 的順序，輸出跟著 hash 種子變。檢查器要先看它失敗、看它誤報，跟 [pstack Ch 21](../pstack/21-verification-skills.md) 引的「a check you've never seen fail proves nothing」同一個道理；[Ch 32](./32-brownfield-bdd.md) 的 `mutate.py` 把這件事做成工具。
4. **以為 research.md 的備選越多越好** → 練習 E 的五家 e-mail 服務、near-miss 的 iOS app 都「有備選」。備選要回答同一個問題、是真的會被選的對手，並寫出輸在哪裡。
5. **以為 `SPECIFY_FEATURE_DIRECTORY` 只影響這一次** → 它會寫回 `.specify/feature.json`，而 `setup-plan.sh` 不檢查 spec.md 在不在：打錯一個字，就多出一個沒有 spec 的 plan，後面每個指令都跟著走錯。

## 進階：讓閘門真的擋得住

- **hook**：plan skill 會讀 `.specify/extensions.yml` 的 `hooks.before_plan`、`hooks.after_plan`，`optional: false` 的 hook 要求 agent 一定執行。把 `plan_lint.py` 包成 extension 指令掛在 `after_plan`，就有「寫完 plan 一定跑 lint」的形狀，但執行它的仍是模型。不靠模型的版本是 Claude Code 的 PostToolUse hook：`specs/*/plan.md` 一被寫入就跑 lint（[Ch 3](./03-propose-constrain-feedback.md) 的做法），或放進 CI。本段未實測，為理論預期行為；驗證方法是故意留一個 `NEEDS CLARIFICATION`，看 hook 是否回報 PL-03。
- **社群 extension**：1.2.5 原始碼的 `extensions/catalog.community.json` 列了 `plan-review-gate`（「Require spec.md and plan.md to be merged via MR/PR before allowing task generation」）、`critique`（「Dual-lens critical review of spec and plan」）、`adrkit`（用既有 ADR 檢查產出的 plan）等。本章只讀了目錄描述，全部未實測。
- **拆開 /plan、定出黃金產物**：research 與系統分析各自成為單一職責的 skill、產物先各自檢查再合併，是 [Ch 15](./15-research-and-analysis-skills.md)；把好計畫的契約定為黃金產物、反推產生它的 skill，是 [Ch 14](./14-artifact-first.md)。合併後 research.md 裡值得長期保留的決策應另寫成 ADR（只新增、不改寫的 record），plan.md 隨整個 plan 資料夾封存，這是 [Ch 33](./33-truth-vs-plan.md) 的分工。

## 動手練習

1. 把 agent-run 的 6 個 lint 問題修到 0：4 個決策補上真的備選與落選理由，R-09、R-10 補上錨點。再照第七節的人審結果改 spec，把 `by`、`detail` 與可存取名稱寫進 FR，重跑一次 `/speckit-plan`（log 寫到專案外），比較兩次的契約。
2. 為 `plan_lint.py` 加一條 PL-09：`contracts/` 裡的 `.yaml`／`.json` 要能 parse（PyYAML 的 `yaml.safe_load`）。先做一個少了 `}` 的 OpenAPI 檔看它失敗，再修好看它通過。
3. 從清單人判那欄挑一條做成規則，例如「Technical Context 出現 constitution 沒列的 runtime 依賴，而 II 是 PASS」。在 near-miss 上看它抓到 PostgreSQL，在好計畫上確認沒有誤報；做不到的那幾條，寫一句話說明它為什麼需要讀懂意思。

## 本章重點整理

- `/speckit-plan` 只有 `setup-plan.sh` 一步是程式：依 `SPECIFY_FEATURE_DIRECTORY` → `.specify/feature.json` 找 feature、複製模板、回傳路徑；不檢查 spec.md，不判 gate。
- 模板五個區塊各防一種錯；未知數寫 `NEEDS CLARIFICATION`，在 Phase 0 變成 Decision / Rationale / Alternatives；gate 從 constitution 推出、研究前後各判一次，違規進 Complexity Tracking。
- Phase 1 的推導：Key Entities → data-model，對外介面 → contracts，驗收情境 → quickstart（驗證指南，不放實作）。
- review 分兩層：結構與對應交給 `plan_lint.py`（PL-01～PL-08），意思對不對交給人；near-miss 證明 0 problems 不等於沒問題，lint 自己也要靠種缺陷來測。

## 自我檢核

- [ ] 不看本章，說出 plan-template 的五個區塊各自防哪一種錯，以及 Performance Goals 什麼時候該寫 N/A、什麼時候該寫 NEEDS CLARIFICATION。
- [ ] 為什麼 gate 要判兩次？舉一個「研究之後才出現」的違規。為什麼說它在 1.2.5 是表單而不是閘門？
- [ ] `setup-plan.sh` 依什麼順序找 feature？在什麼情況下會在沒有 spec.md 的資料夾建出 plan.md？
- [ ] near-miss 的三個錯，分別要用清單的哪一列抓？為什麼 PL-05 的錨點檢查抓不到 PostgreSQL？
- [ ] 你要怎麼證明一支檢查器真的在檢查？說出本章用過的兩種做法。

## 延伸閱讀

- **[Spec Kit v1.2.5：`templates/commands/plan.md`](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/plan.md) 與 [`templates/plan-template.md`](https://github.com/github/spec-kit/blob/v1.2.5/templates/plan-template.md)**（GitHub，官方原始碼）
  - **讀哪裡**：plan.md 的 Outline、Phases、Key rules，模板的 Constitution Check 與 Complexity Tracking。**學到什麼**：安裝時會改寫 frontmatter、把 `{SCRIPT}` 換成 `.specify/scripts/bash/setup-plan.sh --json`、`/memory/` 補成 `.specify/memory/`、多加兩行 hook 名稱的轉換說明，模板裡的 `__SPECKIT_COMMAND_PLAN__` 則換成 `/speckit-plan`；升級前 diff 這兩個檔，就知道 plan 的行為改了什麼。**和本章的關聯**：本章引用的每一句都出自這兩個檔；先讀過 Ch 12 的目錄樹。
- **[Spec Kit：`spec-driven.md`](https://github.com/github/spec-kit/blob/v1.2.5/spec-driven.md)**（GitHub，Spec Kit 的方法論文件）
  - **讀哪裡**：「Constitutional Compliance Through Gates」與「Articles VII & VIII: Simplicity and Anti-Abstraction」。**學到什麼**：Phase -1 gates 與 ≤3 projects 的原意；Complexity Tracking 的範例列就是那套寫死條文留下的。**和本章的關聯**：對照 1.2.5 的模板，看 gate 怎麼從寫死的條文變成從 constitution 推出，以及「compile-time checks」和實際流程的落差。
- **[Michael Nygard — Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)**（Cognitect blog，2011-11-15）
  - **讀哪裡**：全文，篇幅不長，重點是 Title、Context、Decision、Status、Consequences 五個欄位。**學到什麼**：沒記下理由的決策，後人只能盲目接受或盲目推翻。**和本章的關聯**：research.md 的一個條目就是一份輕量 ADR，少了 Status 與 Consequences，所以值得長期保留的決策要升格成真正的 ADR（Ch 33）。
- **[Birgitta Böckeler — Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**（martinfowler.com，2025-10-15）
  - **讀哪裡**：她用 spec-kit 做功能的那段經驗，特別是 review markdown 的描述。**學到什麼**：spec-kit 產出大量互相重複的 markdown，她寧可 review 程式碼；agent 有時無視指示，有時又過度照做。**和本章的關聯**：兩層 review 就是對「markdown 多到審不完」的回答，機器處理結構，人把注意力留給語意；讀之前先看 [SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) 的整理。

清單和 lint 擋住了壞計畫，可是好計畫長什麼樣，到目前為止只存在我們手寫的那一份裡。下一章把這份好計畫裡的契約當成黃金產物（先修好第三節點出的那兩處），反過來推出能穩定產生它的 skill。

→ [Ch 14 Artifact-first：先定黃金產物，再反推 skill](./14-artifact-first.md)
