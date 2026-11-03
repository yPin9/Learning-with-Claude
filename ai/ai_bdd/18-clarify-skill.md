# Ch 18 — clarify 訪談 skill：一次一題、有上限、寫回 spec

> **目標**：讀懂 Spec Kit 1.2.5 的 `speckit-clarify` 怎麼挑題、怎麼問、怎麼寫回；做出可重用的 `clarify-interview` skill：先查 repo 再問人，用 AskUserQuestion 一次問一題並附推薦選項，每個答案都寫回 spec 並標出落點；再用 `clarify_check.py` 把「標記清了沒、紀錄合不合格、答案有沒有落地」變成擋在 plan 前面的檢查。

> **環境**：Spec Kit 1.2.5（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`）、Claude Code 2.1.295（三次 `claude -p`，2026-11-02，預設模型 claude-opus-5-5）、Python 3.12.15（`source /tmp/lab/aibdd/.venv/bin/activate`，檢查器只用標準庫）、macOS 26.6 arm64。lab：`/tmp/lab/aibdd/ch18-clarify/`。

## 為什麼需要這個？

agent 碰到 spec 的缺口有兩個壞預設。一是默默猜：1.2.5 的 `speckit-specify` 明文要它「Make informed guesses based on context and industry standards」，猜的內容進 Assumptions，很少有人細看。二是一次丟出十幾題，人只能隨便答。猜錯的代價要等 plan、tasks、測試都蓋在猜測上才付；問太多的代價是人的注意力。

[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)「第三步」示範了 clarify 的產物，[SDD Ch 08](../spec_driven_development/08-why-requirements-hard.md) 的八種病可以當題庫，[SDD 練習 A](../spec_driven_development/practice-a-requirements-archaeology.md) 讓你手動從模糊需求挖出故事與矛盾。這個 repo 還沒講的是：Spec Kit 內建的 clarify（下稱 stock clarify）**怎麼挑題**、上限多少、答案寫成什麼格式，以及怎麼把訪談（interview）做成別的 skill 也能呼叫、腳本也檢查得了的步驟。Spec Kit 從 0.4.5 起就把 Claude Code 的指令裝成 skill（`/speckit-clarify`），不是 1.x 才改的，見 [Ch 12](./12-spec-kit-1x.md)；SKILL.md 的基本結構見 [harness Ch 29](../harness_engineering/29-skills.md)。

stock clarify 自己也是一路修出來的。0.1.13（2026-03-03）修掉一個自相矛盾的上限：模板第 92 行寫「Maximum of 10 total questions」，其他六處都寫 5（[PR #1557](https://github.com/github/spec-kit/pull/1557)）。0.14.3（2026-07-28）禁止拿 `Acceptance device/runtime matrix (FR-023)` 這種標籤當問題，因為人沒辦法回答一個標籤（[PR #3745](https://github.com/github/spec-kit/pull/3745)）。這兩課本章都會用上：數字交給腳本守，問題要是真的問句。

## 先建立直覺：門診，不是問卷

想像家醫科門診：護理師量過的血壓，醫師不會再問；醫師一次問一題，因為下一題看你上一題怎麼答；門診時間有限，要緊的先問；醫師會說「我建議先做 X」，你點頭就好；每個回答寫進病歷，沒問完的開回診單。這個類比在一處不成立：醫師替你下診斷，clarify 裡做決定的是人，agent 只能推薦。

```
 spec 草稿 ─► 掃 10 類 ─► 候選題 ─► 先查 repo ─┬─ 答得出：寫回，記 (repo: 路徑)，不佔題數
                                             └─ 答不出：impact × uncertainty 排序，取前 N
 一次一題（推薦選項放第一個）─► 人回答 ─► 改對應段落 + 記 `- Q: … → A: … → 落點` + 存檔 ─► 重排
 停：問完 / 人喊停 / 到上限 ─► 沒問到的高影響題留成 [NEEDS CLARIFICATION] 標記
 clarify_check.py：標記、格式、題數、落點 ─► 0 problems 才進 plan
```

## 拆開 speckit-clarify：怎麼挑題、怎麼問、怎麼寫回

建一個 1.2.5 專案，開一個「延長預約」功能，放進一份 feature 檔與一份 spec 草稿。最後一行 `check-prerequisites.sh` 就是 clarify 的第 1 步，它印出的 `FEATURE_SPEC` 是要訪談的檔案。這段在全新目錄重跑過，產出的兩個檔和本章 lab 的逐字相同：

```bash
mkdir -p /tmp/lab/aibdd/ch18-clarify/proj && cd /tmp/lab/aibdd/ch18-clarify/proj && git init -q -b main
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify init --here --force --non-interactive --integration claude --script sh
.specify/scripts/bash/create-new-feature.sh --json --short-name 'extend-booking' 'Meetings often run over. Let the person who booked a room extend the booking.'
mkdir -p features && cat > features/booking.feature <<'EOF'
Feature: Booking a meeting room

  Rule: R1 a room cannot be booked twice for overlapping times

    @sc_book_01
    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

    @sc_book_02
    Scenario: Booking the slot right after another
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed
EOF
cat > specs/001-extend-booking/spec.md <<'EOF'
# Feature Specification: Extend a booking
**Feature Branch**: `001-extend-booking`
**Created**: 2026-11-02
**Status**: Draft
**Input**: User description: "Meetings often run over. Let the person who booked a room extend the booking."

## User Scenarios & Testing *(mandatory)*
### User Story 1 - Extend my own booking (Priority: P1)
Bob booked "Taipei" from 10:00 to 11:00. The meeting runs over, so Bob moves the end of his booking to 11:30 instead of cancelling and booking again.
**Why this priority**: Without it, people cancel and re-book, and can lose the room to someone else in between.
**Independent Test**: Book a slot, extend it, list the bookings, and see the new end time.
**Acceptance Scenarios**:
1. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Bob extends the booking to 11:30, **Then** the booking ends at 11:30
2. **Given** Bob has booked "Taipei" from 10:00 to 11:00 and Carol has booked "Taipei" from 11:15 to 12:00, **When** Bob extends the booking to 11:30, **Then** [NEEDS CLARIFICATION: reject the whole extension, or extend only up to 11:15?]
### Edge Cases
- What happens when the new end time is not later than the current end time?
- What happens when the extension ends exactly when the next booking starts?

## Requirements *(mandatory)*
### Functional Requirements
- **FR-001**: The person who made a booking MUST be able to move its end time later.
- **FR-002**: An extended booking MUST NOT overlap another booking of the same room (R1).
- **FR-003**: Extensions MUST use [NEEDS CLARIFICATION: any HH:MM end time, or fixed 15-minute steps?]
- **FR-004**: [NEEDS CLARIFICATION: may anyone other than the booker extend a booking?]
- **FR-005**: System MUST apply an extension quickly.
### Key Entities
- **Booking**: room, start, end, booked by. An extension changes only the end.

## Success Criteria *(mandatory)*
### Measurable Outcomes
- **SC-001**: A user can extend a booking in under 10 seconds.

## Assumptions
- Times are same-day `HH:MM`, as for booking.
EOF
.specify/scripts/bash/check-prerequisites.sh --json --paths-only
```

`create-new-feature.sh` 先複製 131 行的模板，`cat` 再把它換成本章手寫的草稿：模仿 `/speckit-specify` 的產物形狀，3 個標記剛好是 specify 的上限，FR-005 的「quickly」是一個沒被標記的模糊詞。為了篇幅，草稿只在 `##` 前留空行；三次 agent 實測用的是保留模板空行的 53 行版本，文字相同。feature 檔放 R1 的兩個標準情境，讓訪談有東西可查。agent 實測時 repo 裡還有一個 `booking/schedule.py`（`book()` 守 R1 的半開區間與 R2，`cancel()` 守 R4，lab 的 `runs/` 留有原檔）；它只影響 agent 找得到多少類比，檢查器的示範用不到它，[Ch 19](./19-tdd-by-hand.md) 會用 TDD 從零寫出 `Schedule`。

接著讀安裝好的 skill（294 行），不靠記憶：

```bash
S=.claude/skills/speckit-clarify/SKILL.md
grep -nE "Maximum of 5|EXACTLY ONE|Impact \* Uncertainty|→ A:" $S
grep -c "NEEDS CLARIFICATION" $S .claude/skills/speckit-specify/SKILL.md
```

```
132:    - Maximum of 5 total questions across the whole session.
140:    - If more than 5 categories remain unresolved, select the top 5 by (Impact * Uncertainty) heuristic.
143:    - Present EXACTLY ONE question at a time.
189:    - Append a bullet line immediately after acceptance: `- Q: <question> → A: <final answer>`.
.claude/skills/speckit-clarify/SKILL.md:0
.claude/skills/speckit-specify/SKILL.md:9
```

| 五個階段（引號內是原文） | 它做什麼 |
|---|---|
| 掃描 | 10 類（Functional Scope & Behavior、Domain & Data Model、Interaction & UX Flow、Non-Functional Quality Attributes、Integration & External Dependencies、Edge Cases & Failure Handling、Constraints & Tradeoffs、Terminology & Consistency、Completion Signals、Misc / Placeholders）逐一標 Clear / Partial / Missing，只在內部用；「implementation method, tech-stack comparison, or task breakdown」與不會改變實作或驗證的題目剔除 |
| 排隊 | 全程最多 5 題；每題要能用 2–5 個互斥選項，或「Answer in <=5 words」回答；未解的類別超過 5 個，照 Impact × Uncertainty 取前 5 |
| 問 | 「EXACTLY ONE question at a time」；`**Question:**` 開頭、以 `?` 結尾，一句 Why it matters，再給 `**Recommended:** Option X - 理由` 與選項表；人回 yes 就採用推薦；「Never reveal future queued questions」 |
| 寫回 | 每接受一個答案：`## Clarifications` → `### Session YYYY-MM-DD` → `- Q: … → A: …`，同時改到對應段落（FR、User Story、Edge Cases、Success Criteria），刪掉被推翻的舊句，**每答一題存檔一次** |
| 停 | 問題解完、人說 done、或問滿 5 題；額度用完還有高影響類別，就在完成報告列為 Deferred；報告用 Resolved / Deferred / Clear / Outstanding 四種狀態 |

兩個容易誤會的地方。第一，clarify 全文**沒有**出現 `[NEEDS CLARIFICATION]`（計數 0）：標記是 specify 插的，最多 3 個，specify 自己會「Present all questions together before waiting for responses」，用答案取代標記；clarify 做的是覆蓋度掃描（coverage scan），標記只會在 Misc / Placeholders 那一類被看到。[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) 的「`[NEEDS CLARIFICATION]` 的工作原理」照 0.11.10 也是這樣分工，[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)「第三步」的對話示範原本讓 clarify 針對標記一次列出三題，已改成照模板一次一題。第二，紀錄格式是一行一條 `- Q: <question> → A: <final answer>`（SDD Ch 28 原本寫成粗體 Q 加一行 A 的兩行格式，也已改正），本章的檢查器以此為準。

## 底層機制：為什麼一次一題、有上限、每答必存

**一次一題**，因為題目互相依賴。延長撞到下一筆時，如果答案是「縮短到下一筆開始」，就冒出新題「縮短後怎麼告訴使用者」；答案是「整筆拒絕」，那題就不必問。一次全丟，人得替還不存在的情境作答。**上限**是注意力預算：佇列已照預期價值排序，第 6 題的價值低於前 5 題；10 和 5 曾並存在同一份模板，說明寫在散文裡的數字會漂移。**推薦選項**把 agent 原本會默默做的猜測攤在桌上，人同意時回一個字就好。**紀錄與段落都要寫、每答必存**：紀錄回答「何時決定了什麼」，段落才是 plan 據以設計的需求（`speckit-plan` 讀整份 spec，只改紀錄不改段落，它會讀到前後矛盾的兩處）；每答一題就存檔，對話被壓縮或中斷，已決定的事也不會丟。

AskUserQuestion 是 Claude Code 內建的選擇題工具，規則寫在工具描述裡，從安裝的 binary 就讀得到（原生安裝版的 `~/.local/bin/claude` 是指向版本檔的連結）：

```bash
strings -n 20 "$(readlink ~/.local/bin/claude)" | grep -oE "Use this tool only when you are blocked[^:]*: one you cannot resolve[^.]*\.|If you recommend a specific option[^\"]*\"\(Recommended\)\" at the end of the label|Questions to ask the user \(1-4 questions\)|Must have 2-4 options\. Each option[^.(]*" | sort -u
```

```
If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label
Must have 2-4 options. Each option should be a distinct, mutually exclusive choice 
Questions to ask the user (1-4 questions)
Use this tool only when you are blocked on a decision that is genuinely the user's to make: one you cannot resolve from the request, the code, or sensible defaults.
```

第一行與最後一行正是本章要的兩條紀律：推薦放第一個，repo 答得出就別問。和 Spec Kit 的文字問法對照（它的 skill 全文沒有用到 AskUserQuestion）：

| | speckit-clarify（文字） | AskUserQuestion |
|---|---|---|
| 選項 | 2–5 個，另可「Short」自由作答 | 2–4 個，UI 自動加 Other 讓人打字 |
| 推薦 | 選項表上方 `**Recommended:** Option X - 理由` | 第一個選項，label 結尾加 `(Recommended)` |
| 人怎麼答 | 回字母、回 yes、或 5 字內短答 | 點選；回傳的是**選項的 label 文字**，或人打的字 |
| 一次幾題 | 1 | 1–4（我們固定 1）；header 最多 12 字元 |

官方文件說回傳值是選項的 label；2.1.295 的 binary 把答案原樣組成 `"<題目>"="<答案>"` 交回 agent，除了工具描述，沒有別處處理 `(Recommended)`。所以人點推薦選項時，agent 拿到的答案會帶著這個尾巴（由文件與 binary 推得，互動流程本章未實測）；它不能進紀錄，檢查器的 CL-4 會抓。AskUserQuestion 在 Agent 工具開的 subagent 裡沒有（官方 SDK 文件），在沒有 host（替人接住提問的 SDK 程式或 `--permission-prompt-tool`）的 `claude -p` 裡也沒有（實測 B）。

## clarify-interview：我們自己的訪談 skill

把上面的機制加上兩件 stock 版沒有的事（先查 repo、寫回時標出落點），照 [Ch 11](./11-skill-engineering-skill.md) 的「脊椎 + rules + templates + scripts」拆成 `.claude/skills/clarify-interview/` 底下四個檔：`SKILL.md`（7 步流程）、`rules/question-bank.md`（題庫與排序）、`templates/question.md`（問法與紀錄格式）、`scripts/clarify_check.py`（done 的判準）。

```markdown
---
name: clarify-interview
description: Interview the human about a drafted feature spec before planning - one question at a time, at most 5, each with a recommended option - and write every answer back into the spec. Use after a spec is drafted, when a spec has [NEEDS CLARIFICATION] markers, or when another skill reaches a product decision it cannot answer from the repo.
argument-hint: "[queue] [answers=<file>] [max=<N>] [spec path]"
---

# Clarify interview

Spec: the path in the arguments, else `FEATURE_SPEC` from
`.specify/scripts/bash/check-prerequisites.sh --json --paths-only`.
Modes: default = interview; `queue` = print the queue and stop, write nothing;
`answers=<file>` = take the human's answers from that file instead of asking.
Cap: `max=<N>`, default 5. Done when `python .claude/skills/clarify-interview/scripts/clarify_check.py <spec>`
reports nothing but CL-1 lines for questions you deferred on purpose.

## 1. Scan
Mark each category in `rules/question-bank.md` Clear / Partial / Missing for this spec.
Every `[NEEDS CLARIFICATION: ...]` marker is a candidate; so is every Partial or Missing category.
Drop candidates about implementation method, tech stack or task breakdown (they belong to the plan),
and candidates whose answer would not change a requirement, a test or the UI.

## 2. Answer from the repo before asking
For each candidate, search `.specify/memory/constitution.md`, `features/`, the code, and the
`## Clarifications` of other `specs/*/spec.md`. If the repo decides the question, apply the answer
and log it with `(repo: <path>:<line>)`; it does not count toward the cap. If the repo only suggests
an answer (an analogous rule), keep the question and make that answer the recommended option.

## 3. Queue
Rank what is left by impact x uncertainty (`rules/question-bank.md`, Ranking). Keep the top N.
If nothing is left, say "No critical ambiguities" and stop. In `queue` mode print the coverage table,
the repo answers and the queue, then stop.

## 4. Ask exactly one question
Call AskUserQuestion with one question in the shape of `templates/question.md`: header of 12 characters
or fewer, 2-4 options, the recommended option first with " (Recommended)" at the end of its label and
its reason in the description. Do not reveal later questions.
If AskUserQuestion is unavailable or denied, print the question in the template's text form and stop.
Never answer it yourself.
In `answers=<file>` mode, take the answer from the file; if the file does not answer it, defer it (step 6).

## 5. Write back after every answer
1. Apply the answer where it belongs: replace the marker, or edit the FR, scenario, edge case or SC
   line it changes. Remove any statement the answer contradicts.
2. Under `## Clarifications` (add it at the end of the spec if missing), under `### Session <today>`,
   append `- Q: <question> → A: <answer in words> → <target>`, where target is the FR/SC id, `US<n>`,
   or the heading you edited. Log the answer, never the option letter and never "(Recommended)".
3. Save the file before asking the next question. An answer may make queued questions moot; drop them.

## 6. Stop
Stop when the queue is empty, the human says stop or done, or N questions were asked.
For each high-impact candidate still open, put `[NEEDS CLARIFICATION: <question>]` where its answer
belongs. A deferred question stays in the spec, not in the chat.

## 7. Check and report
Run clarify_check.py and fix every CL-2..CL-7 problem. Report: questions asked, repo answers,
sections touched, deferred markers, and a coverage table (Resolved / Deferred / Clear / Outstanding).
```

- **第 2 步先查 repo**，出自 [pstack Ch 21](../pstack/21-verification-skills.md) 的「Answer these from the codebase and only ask the user what you cannot observe」，要和 [pstack Ch 15](../pstack/15-delegation-meta-autonomy.md) 的 Never Block on the Human 一起讀：repo 答得出的直接寫回；跑一下就知道的技術事實交給 [Ch 15](./15-research-and-analysis-skills.md) 的 `technical-research`；影響低又可逆的照 Never Block 用預設、寫進 Assumptions；只有**產品方向、答錯就要重寫測試**的題目才問人。
- **第 5 步多了 `→ <target>`**。Spec Kit 的格式記得「問了什麼、答了什麼」，看不出「改到哪裡」；多這一段，腳本就能檢查落點。pstack Ch 15 說「Never give a shorthand token to type back」：我們允許人在對話裡回 recommended，但進紀錄的一定是答案本身。
- **第 4 步的退路是停下來印出問題**，不是自己作答；**第 6 步把沒問到的題寫回成標記**，不只寫在報告裡。人不在時猜，就回到本章要解決的問題；報告留在對話，對話結束就沒了，標記留在 spec，plan 前的檢查會擋下它。以下是題庫原文：

```markdown
# Question bank

## Categories (scan every one; from speckit-clarify 1.2.5, step 3)

| Category | Look for | Example question (other booking features) |
|---|---|---|
| Functional scope | who may do it, what is out of scope | May someone other than the booker cancel a booking? |
| Domain & data | fields, identity, state changes | Is a cancelled booking's id ever reused? |
| Interaction & UX | journeys, error and empty states | What does the page show when a booking is rejected? |
| Quality attributes | numbers behind "fast", "reliable" | What does "fast" mean in seconds, or can the word go? |
| Integration | external systems, formats | Is the booking list exported anywhere? |
| Edge cases | negative paths, conflicts | What happens when two people book the last free slot at once? |
| Constraints | fixed limits, rejected options | Is there a longest allowed booking? |
| Terminology | one word per concept | Is it "booking" or "reservation", and which one stays? |
| Completion signals | can a test decide it? | How would an acceptance test see that a booking was cancelled? |
| Placeholders | markers, TODO, vague adjectives | every [NEEDS CLARIFICATION] marker |

The examples show the shape of a question. They are not facts about this project.

## Not every requirement defect is a question

Of the eight requirement defects in SDD Ch 08, four become questions: ambiguity, vagueness,
omission, untestability. Fix complexity, duplication and wordiness by editing the spec yourself.
Inappropriate implementation ("store it in Redis") is a plan topic: move it out, do not ask.

## Ranking

Score impact 1-3: 3 = changes an API status, a stored field or an acceptance test; 2 = changes
what a user sees; 1 = wording only. Score uncertainty 1-3: 3 = nothing in spec or repo points
to an answer; 2 = an analogous rule exists; 1 = a safe default exists. Ask highest product first;
on a tie, ask the one whose answer could make another question moot.

## Good, bad, edge

- Good: "If a new booking overlaps an existing one, should we reject it or offer the nearest
  free slot?" (one decision, options are exclusive)
- Bad: "Cancellation rules (FR-004)" (a label, not a question)
- Bad: "Should cancel be DELETE or POST?" (implementation; the plan decides)
- Bad: "Do back-to-back bookings conflict?" (the repo answers it: half-open intervals)
- Edge: the human answers "it depends on the room". That is a new rule, not an option;
  log it in words and add an FR for it.
```

1–3 分是我們自訂的序數刻度，不是量測值，用處是讓排序理由說得出口、能被 review。「八種病只有四種變成問題」是本章對 SDD Ch 08 的讀法：複雜、重複、冗長是編輯問題，不當實作要移到 plan，拿去問人只是浪費題數。範例刻意取自別的功能，原因見踩雷 2。`node /tmp/lab/tools/skill-lint.mjs .claude/skills`（[pstack Ch 28](../pstack/28-skill-authoring.md) 的 lint）印出 `11 skills, 0 problems`：10 個 speckit skill 加上我們的。最後是 `templates/question.md`：

````markdown
# Question template

## AskUserQuestion input (one question per call)

```json
{"questions": [{
  "question": "<full question ending with ?>",
  "header": "<12 chars max>",
  "multiSelect": false,
  "options": [
    {"label": "<recommended answer> (Recommended)", "description": "<why, one sentence>"},
    {"label": "<other answer>", "description": "<what it changes>"}
  ]
}]}
```

## Text form (when AskUserQuestion is unavailable)

**Question:** <full question ending with ?>
Why it matters: <one sentence>
**Recommended:** <answer> - <reason>

| Option | Answer |
|---|---|
| A | <recommended answer> |
| B | <other answer> |

Reply with your answer in words, or say "recommended".

## Log entry

- Q: <question> → A: <answer in words> → <FR-xxx | SC-xxx | US<n> | heading>
- Q: <question> → A: <answer> → <target> (repo: <path>:<line>)
````

## clarify_check.py：把訪談的契約變成擋 plan 的檢查

```python
#!/usr/bin/env python3
"""clarify_check.py: check a feature spec against the clarify-interview contract.

  CL-1  open [NEEDS CLARIFICATION: ...] marker (outside code fences)
  CL-2  malformed log: "### Session YYYY-MM-DD", then "- Q: <q> → A: <a> → <target>"
  CL-3  a session asked the human more than --max-questions questions; "(repo: ...)" entries don't count
  CL-4  answer is an option token ("B", "yes", "recommended") or keeps "(Recommended)"
  CL-5  answer not applied: no "→ <target>", or the target (FR-003, SC-001, US1, a heading) is missing
  CL-6  the same question is logged twice
  CL-7  "(repo: <path>)" names a file that does not exist
Exit status: 0 clean, 1 problems found, 2 usage error.
"""
import argparse, datetime, json, re, sys
from pathlib import Path

MARKER = re.compile(r"\[NEEDS CLARIFICATION:?\s*([^\]]*)\]")
SESSION = re.compile(r"^### Session (\d{4}-\d{2}-\d{2})\s*$")
ENTRY = re.compile(r"^- Q: (?P<q>.+?) → A: (?P<a>.+?)(?: → (?P<t>[^→]+?))?(?: \(repo: (?P<src>[^)]+)\))?$")
TOKEN = re.compile(r"^(?:option\s+)?[a-e]\.?$|^(?:yes|y|ok|recommended|suggested|default)$", re.I)
ID = re.compile(r"^(?:FR|SC)-\d{3}$|^US\d+$")

def repo_root(spec):
    return next((d for d in spec.resolve().parents
                 if (d / ".specify").is_dir() or (d / ".git").exists()), spec.resolve().parent)

def session_date(line):
    try:
        return datetime.date.fromisoformat(SESSION.match(line).group(1))
    except (AttributeError, ValueError):
        return None

def check(path, max_q):
    probs, body, headings, entries, asked, seen = [], [], set(), [], {}, {}
    session, in_fence, in_clar = None, False, False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fence = line.lstrip().startswith("```")
        in_fence ^= fence
        if in_fence or fence:
            continue
        if line.startswith("## "):
            in_clar = line.strip() == "## Clarifications"
        for m in MARKER.finditer(line):
            probs.append((n, "CL-1", f"open marker: {m.group(1).strip() or '(empty)'}"))
        if not in_clar:
            body.append(line)
            if line.startswith("#"):
                headings.add(re.sub(r"\s*\*\(.*\)\*\s*$", "", line.lstrip("#").strip()))
        elif line.startswith("### "):
            session = session_date(line)
            if session is None:
                probs.append((n, "CL-2", f"bad session heading {line.strip()!r}; want '### Session YYYY-MM-DD'"))
        elif line.startswith("- "):
            m = ENTRY.match(line.strip())
            if m is None or session is None:
                why = "entry before any '### Session' heading" if m else "want '- Q: <question> → A: <answer> → <target>'"
                probs.append((n, "CL-2", f"bad log entry: {why}"))
                continue
            entries.append((n, m))
            if not m.group("src"):
                asked[session] = asked.get(session, 0) + 1
                if asked[session] == max_q + 1:
                    probs.append((n, "CL-3", f"session {session} asked more than {max_q} questions"))
            key = " ".join(m.group("q").lower().split())
            if key in seen:
                probs.append((n, "CL-6", f"question already logged on line {seen[key]}"))
            seen.setdefault(key, n)
    text, root = "\n".join(body), repo_root(path)
    for n, m in entries:
        answer = m.group("a").strip()
        if TOKEN.match(answer) or "(Recommended)" in answer:
            probs.append((n, "CL-4", f"answer {answer!r} is an option token or UI label; log the answer itself"))
        targets = [t.strip() for t in (m.group("t") or "").split(",") if t.strip()]
        if not targets:
            probs.append((n, "CL-5", "answer not applied: add '→ <FR-xxx | heading>' after the answer"))
        for t in targets:
            pat = re.sub(r"^US(\d+)$", r"(?:US|User Story )\1", re.escape(t))
            if not (re.search(rf"\b{pat}\b", text) if ID.match(t) else t in headings):
                probs.append((n, "CL-5", f"target {t!r} not found outside ## Clarifications"))
        src = m.group("src")
        if src and not (root / src.split(":")[0]).exists():
            probs.append((n, "CL-7", f"repo source {src!r} does not exist under {root}"))
    return sorted(probs)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="+", type=Path, help="spec.md files to check")
    ap.add_argument("--max-questions", type=int, default=5, metavar="N",
                    help="questions one session may ask the human (default 5, as speckit-clarify)")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    args = ap.parse_args()
    report = []
    for spec in args.specs:
        if not spec.is_file():
            ap.error(f"{spec}: no such file")
        report += [{"file": str(spec), "line": n, "rule": r, "message": msg}
                   for n, r, msg in check(spec, args.max_questions)]
    if args.json:
        print(json.dumps({"files": len(args.specs), "problems": report}, ensure_ascii=False, indent=1))
    else:
        for p in report:
            print(f"{p['file']}:{p['line']}: {p['rule']} {p['message']}")
        print(f"{len(args.specs)} files, {len(report)} problems")
    return 1 if report else 0

if __name__ == "__main__":
    sys.exit(main())
```

能用腳本判斷的就不交給 agent 自評：標記、紀錄格式、題數、落點、引用的 repo 檔案。在草稿上跑，三個標記各一行（exit 1）；FR-005 的「quickly」沒被抓到，腳本只看結構，模糊詞要靠第 1 步的掃描（或動手練習 1 的 CL-8）：

```
$ python .claude/skills/clarify-interview/scripts/clarify_check.py specs/001-extend-booking/spec.md
specs/001-extend-booking/spec.md:14: CL-1 open marker: reject the whole extension, or extend only up to 11:15?
specs/001-extend-booking/spec.md:23: CL-1 open marker: any HH:MM end time, or fixed 15-minute steps?
specs/001-extend-booking/spec.md:24: CL-1 open marker: may anyone other than the booker extend a booking?
1 files, 3 problems
```

## 實測：三次 claude -p

三次都在 `proj/` 裡跑，共用 `--setting-sources project --strict-mcp-config --no-session-persistence --output-format stream-json --verbose`；`--tools` 只開 Read、Glob、Grep、Bash（B、C 加 Edit），`--allowedTools` 裡的 Bash 只放行 `check-prerequisites.sh` 與 `clarify_check.py`。每次約 1–1.5 分鐘，`total_cost_usd` 約 0.35–0.38（client 端估計）。**A** 是 queue 模式（`/clarify-interview queue specs/001-extend-booking/spec.md`，加 `--permission-prompts none`）：印出 10 類覆蓋表、1 個 repo 答案（相鄰時段不衝突，引 `booking/schedule.py` 與 `features/booking.feature:12`）和 5 題佇列，每題附分數與推薦；第一名是一題沒人預期的「延長要不要限在辦公時間」，原因見踩雷 2。

> **B：訪談模式，但沒有人在**（`--tools` 多列了 `AskUserQuestion`，也不加 `--permission-prompts none`）。以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。init 事件的工具清單是 `{"tools":["Bash","Edit","Glob","Grep","Read"],"permissionMode":"default"}`；最後回報的第一句是「The interview is paused on question 1, because `AskUserQuestion` isn't available in this session and I can't answer it myself.」，後半是文字版問題「**Question:** When Bob extends to 11:30 but Carol's booking starts at 11:15, should the system reject the whole extension, or extend only up to 11:15?」。

清單裡沒有 AskUserQuestion：在 2.1.295 的 `claude -p`（沒有 SDK host，也就是沒有接住提問的程式）裡，這個工具不存在。官方的 [headless 文件](https://code.claude.com/docs/en/headless)只寫到 `--permission-prompts none` 會拿掉它、`dontAsk` 會拒絕它；沒加旗標時也不見，文件沒寫，binary 裡這個工具的 `isEnabled()` 倒是寫得很明白：非互動模式、又沒有 permission prompt tool（`--help` 說的 host：SDK host 或 `--permission-prompt-tool`）就回 false。skill 走了第 4 步的退路，印出文字版問題（後面還有 Why it matters、Recommended 與選項表）就停，沒有替人作答；停之前它已把 repo 答得出的那題寫回 Edge Cases，記了一行 `(repo: features/booking.feature:12)`。也因此，真的跳出 AskUserQuestion 的互動流程**本章未實測**：要驗證，在互動式 `claude` 裡輸入 `/clarify-interview specs/001-extend-booking/spec.md`，看第一次是不是只問一題、推薦選項排第一且帶 `(Recommended)`，答完之後 spec 是否立刻多一行紀錄。互動 session 的 PATH 若被 `~/.claude/settings.json` 的 `env.PATH` 蓋掉而不含 venv（本課的實驗機就是），done 指令的 `python` 會找不到；檢查器只用標準庫，改用 `python3`（系統的 3.9.6）也能跑。

**C：重播 PM 的書面答案**。PM 事先寫好 `answers.md`，標題一行 `# PM answers for 001-extend-booking (2026-11-02)`，底下只有三條（原文）：「If an extension would overlap the next booking, reject the whole extension with the usual conflict message ("Taipei is taken 11:15-12:00 by Carol"). Never shorten it.」「Only the person who booked can extend, same as cancelling.」「Any HH:MM end time is fine, like booking. No 15-minute grid.」指令在 `proj/` 下、啟用 venv 後執行；輸出寫到 `../runs/`，要先 `mkdir -p ../runs`，否則重導向失敗、claude 根本不會啟動（實測時外面另包一層 `perl -e 'alarm 560; exec @ARGV'` 當逾時保護）：

```bash
claude -p "/clarify-interview answers=answers.md specs/001-extend-booking/spec.md" \
  --setting-sources project --strict-mcp-config --tools "Read,Glob,Grep,Edit,Bash" \
  --allowedTools "Read,Glob,Grep,Edit,Bash(.specify/scripts/bash/check-prerequisites.sh *),Bash(python .claude/skills/clarify-interview/scripts/clarify_check.py *),Bash(python3 .claude/skills/clarify-interview/scripts/clarify_check.py *)" \
  --permission-prompts none --max-turns 20 --no-session-persistence --output-format stream-json --verbose < /dev/null > ../runs/c-replay.jsonl
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。agent 寫進 spec 的四行紀錄（全文照錄）：

```
- Q: Does an extension that ends exactly when the next booking starts conflict with it? → A: No, intervals are half-open, so back-to-back bookings do not overlap → Edge Cases (repo: features/booking.feature:12)
- Q: If an extension would overlap the next booking, should the whole extension be rejected, or should it extend only up to that booking? → A: Reject the whole extension with the usual conflict message ("Taipei is taken 11:15-12:00 by Carol"); never shorten it → US1, FR-002
- Q: May anyone other than the booker extend a booking? → A: No, only the person who booked can extend, same as cancelling → FR-004
- Q: Must extensions use any HH:MM end time, or fixed 15-minute steps? → A: Any HH:MM end time, like booking, with no 15-minute grid → FR-003
```

三個答案寫進了情境 2 與 FR-002、FR-003、FR-004；答案檔沒回答的兩題（新結束時間不晚於原本、「quickly」）照第 6 步留成標記。它想跑檢查器，卻把指令寫成 `(python … || python3 …); echo …`，被擋下：「This command uses shell operators that require approval for safety」。擋它的是括號：用 `&&`、`;` 串起來的指令會拆開逐段檢查，每段都符合 allow 規則或屬於唯讀指令就放行（它開場的 `git ls-files && ls -la && ls -R …` 就照常跑了），但 2.1.295 碰到子殼層 `( … )` 或 `{ …; }` 指令群組一律要人批准（binary 裡的判斷是 `hasSubshell||hasCommandGroup`），這次沒人能批，就直接拒絕。它改用 Grep 自己核對，並在報告裡說 done 檢查沒跑成。我們替它跑，只剩兩個延後的標記（行號以本章的緊湊排版計；agent 寫出的原檔保留模板空行，同兩個標記在第 28、39 行）：

```
$ python .claude/skills/clarify-interview/scripts/clarify_check.py specs/001-extend-booking/spec.md
specs/001-extend-booking/spec.md:16: CL-1 open marker: what happens when the new end time is not later than the current end time?
specs/001-extend-booking/spec.md:25: CL-1 open marker: what does "quickly" mean in seconds, or can this requirement go?
1 files, 2 problems
```

人回來答完那兩題（我們手動寫回：新增 FR-006；刪掉 FR-005，ID 不重用；紀錄多兩行，落點是 `FR-006, Edge Cases` 與 `SC-001`），檢查器變乾淨；上限調成 4，第 5 個人工答案就被標出來。這兩段和後面的 fixtures 都跑在 lab 的這份 spec 上，裡面有 agent 的編輯；你自己跑出來的檔案，行號可能不同：

```
$ python .claude/skills/clarify-interview/scripts/clarify_check.py specs/001-extend-booking/spec.md
1 files, 0 problems
$ python .claude/skills/clarify-interview/scripts/clarify_check.py --max-questions 4 specs/001-extend-booking/spec.md
specs/001-extend-booking/spec.md:43: CL-3 session 2026-11-02 asked more than 4 questions
1 files, 1 problems
```

最後把乾淨版複製成 `fixtures/broken.md`，模仿 agent 常犯的錯手改六處：FR-004 那條答案改成 `A`、FR-003 那條改成 `Any HH:MM end time (Recommended)`、刪掉最後一條的 `→ SC-001`、把紀錄裡的落點 `FR-006` 改成 `FR-007`、repo 來源改成不存在的 `docs/glossary.md:3`、結尾再記一次同一題 `- Q: May anyone other than the booker extend a booking? → A: Only the booker → FR-004`。另外 `stock-format.md` 拿掉 FR-004、FR-003 兩條的落點（Spec Kit 原生格式，在我們的契約下刻意擋掉），`ascii-arrow.md` 把 FR-003 那條 `A:` 前的 `→` 打成 `->`（打錯的若是落點前那個，答案會把 `-> FR-003` 吞進去，報的是 CL-5 而不是 CL-2）：

```
$ python .claude/skills/clarify-interview/scripts/clarify_check.py fixtures/broken.md fixtures/stock-format.md fixtures/ascii-arrow.md
fixtures/broken.md:38: CL-7 repo source 'docs/glossary.md:3' does not exist under /private/tmp/lab/aibdd/ch18-clarify/proj
fixtures/broken.md:40: CL-4 answer 'A' is an option token or UI label; log the answer itself
fixtures/broken.md:41: CL-4 answer 'Any HH:MM end time (Recommended)' is an option token or UI label; log the answer itself
fixtures/broken.md:42: CL-5 target 'FR-007' not found outside ## Clarifications
fixtures/broken.md:43: CL-5 answer not applied: add '→ <FR-xxx | heading>' after the answer
fixtures/broken.md:44: CL-3 session 2026-11-02 asked more than 5 questions
fixtures/broken.md:44: CL-6 question already logged on line 40
fixtures/stock-format.md:40: CL-5 answer not applied: add '→ <FR-xxx | heading>' after the answer
fixtures/stock-format.md:41: CL-5 answer not applied: add '→ <FR-xxx | heading>' after the answer
fixtures/ascii-arrow.md:41: CL-2 bad log entry: want '- Q: <question> → A: <answer> → <target>'
3 files, 10 problems
```

## learn skill 的 Step 1：一個已經在用的 clarify 步驟

這個 repo 的 [learn skill](../../.claude/skills/learn/SKILL.md) Step 1「釐清學習目標（務必先問，不要猜）」就是一個 clarify 步驟：「先和使用者對齊，最多一次問完」，五項（主題與範圍、既有背景、深度目標、實作要求、預計規模），規模附「預設 **25–40 章**」，最後一句「如果使用者已經給了這些資訊，就別再問。」

| learn Step 1 | clarify-interview | 為什麼不同 |
|---|---|---|
| 最多一次問完 | 一次一題 | 五項彼此獨立、使用者剛開口時人在場，一輪問完最省；spec 的題目互相依賴 |
| 規模預設 25–40 章 | 推薦選項 | 只有一項有預設 |
| 給過就別再問 | 先查 repo | 它查的是對話；「使用者想繼續寫既有的課程資料夾」那節的「先看 README 是否存在、章節檔是否存在」才是查 repo |
| Step 2 課綱草稿確認後才建檔 | 寫回 spec ＋ 紀錄 | 答案落在課綱；Step 6b 的 course-scope memory 要在 `**Why:**`「把 Step 1 對齊到的東西寫進來」，但那是一段摘要，不是逐題、帶日期的紀錄 |

## 對比與取捨

| 做法 | 一次幾題 | 上限 | 答案去哪 | 適合 |
|---|---|---|---|---|
| 不問，照慣例猜（specify 的預設） | 0 | — | Assumptions | 低影響、可逆的細節 |
| speckit-clarify | 1 | 5 | Clarifications ＋ 對應段落 | Spec Kit 專案，plan 之前 |
| clarify-interview | 1 | 5，可調 | 紀錄＋落點，檢查器把關 | 要被別的 skill 呼叫、要能擋 plan |
| Never Block（pstack Ch 15） | 0，先做再報 | — | 回報裡寫明用了什麼預設 | 可逆的執行層決定 |

## 踩雷集錦

1. **錯誤直覺：clarify 是在回答 `[NEEDS CLARIFICATION]` 標記。** 正確認識：1.2.5 的 clarify 全文沒有這個字串，它掃的是 10 類覆蓋度。沒標記的缺口一樣會進候選（「quickly」在實測 A、C 都被挑出來），跑完也可能還有標記（stock 版不專門處理 specify 的標記；我們的 skill 還會把延後的題寫回成標記，實測 C 留了兩個），所以 plan 前要用 CL-1 擋，不能假設跑過 clarify 就乾淨。
2. **錯誤直覺：題庫範例只是說明。** 正確認識：實測 A 時題庫範例還寫著「Must an extension stay inside office hours (R3)?」。agent 在 repo 找不到 R3，回報裡寫（實測 A 的實際執行紀錄節錄，2026-11-02，每次執行結果不同）「R3 is undefined: it appears only in the question bank's example, as office hours. … The numbering suggests a rule that was never recorded, which is why Q1 ranks highest.」範例改取自別的功能、加上「They are not facts about this project.」之後，實測 C 沒再問它。rule file 的範例會被當成事實，這是 [Ch 9](./09-rule-files.md)「為什麼例子比指令強」的另一面。
3. **錯誤直覺：紀錄寫「A」、寫了就算寫回。** 正確認識：AskUserQuestion 回傳的是 label，推薦選項的 label 又帶著 `(Recommended)`，Spec Kit 也允許回字母；三個月後讀紀錄的人看不到當時的選項表，紀錄要寫答案本身（CL-4）。plan 讀整份 spec，答案只留在紀錄、FR 與情境沒改，它會讀到互相矛盾的兩處，所以每條紀錄要指向存在的落點（CL-5）；腳本確認不了落點寫的就是這個答案，這一步留給 review。
4. **錯誤直覺：訪談在哪都能跑。** 正確認識：AskUserQuestion 在 Agent 工具開的 subagent 與沒有 host 的 `claude -p` 裡都沒有（官方文件、實測 B）。skill 要有退路：印出問題、停下、不自答；done 指令寫成一行不加括號的單一指令，否則像實測 C 一樣被權限擋下。

## 進階：讓別的 skill 呼叫它

- **契約與呼叫方**：輸入是 spec 路徑（可加 `max=`、`answers=`、`queue`），輸出是改過的 spec，完成條件是 `clarify_check.py` 只剩刻意延後的 CL-1。呼叫方照 pstack Ch 28 的「Delegate to other skills by path. Don't restate.」寫一句「run the clarify-interview skill on <spec>」。Ch 15 與 Ch 17 的 skill 都還沒這樣接，以下是建議的改法，本章沒有動它們：[Ch 17](./17-constitution-and-presets.md) 的 `sdd-specify` 只寫「open questions go to the clarify step」，沒點名哪個 skill，可以把那一步寫成這句；Ch 15 的 `technical-research` 第 1 步把它認為漏掉的題目留在最後的訊息裡，其中屬於產品決定的，也可以改成交給它。跑在 subagent 裡的 planner 問不了人，就把題目寫成標記交回主對話。上限也能放進 Ch 17 的 constitution 參數層。`answers=` 等於重播一場會議的結論，[Ch 29](./29-example-mapping.md) 的 Example Mapping 紅卡（問題卡）在會上或會後補上答案，就是現成的答案來源。
- **評估挑題品質**（本章未實測，以下是協定）：在草稿裡埋 N 個已知缺口，queue 模式跑 k 次，量前 5 題命中幾個、排序穩不穩。實測 A 與 C 的佇列就不同（中間改過題庫），所以檢查器只評結果，不評挑題。

## 動手練習

1. 替 `clarify_check.py` 加一條 CL-8：FR 與 SC 行出現 `quickly|fast|robust|intuitive`（後兩個是 speckit-clarify Misc 類的原文舉例）就報錯。先讓它在草稿上紅（FR-005），再在乾淨版上綠。
2. 把 [Ch 8](./08-translation-sandwich.md) 的需求「客戶要能預約每週固定例會」寫成草稿，用 queue 模式跑，或自己照七步走一遍。第一名應該是哪一題？提示：全課的領域只有同一天的 `HH:MM`（[Ch 1](./01-course-map.md) 的領域表）。

## 本章重點整理

- stock clarify：10 類掃描、剔除實作題、impact × uncertainty 取前 5、一次一題附推薦、每答寫回並存檔；`[NEEDS CLARIFICATION]` 是 specify 的，clarify 的文字沒提到它。
- clarify-interview 多做兩件事：先查 repo（答得出就不問、不佔題數），以及寫回時標出落點；沒問到的高影響題留成標記，不留在對話。
- AskUserQuestion：2–4 個選項、推薦放第一並加 `(Recommended)`、回傳 label 文字；subagent 與沒有 host 的 `claude -p` 裡沒有，skill 要有退路。
- `clarify_check.py` 檢查得了結構（標記、格式、題數、落點、來源），檢查不了答案對不對、題目挑得好不好。

## 自我檢核

- [ ] 不看本章，說出 speckit-clarify 的五個階段，以及每個階段一條原文規則。
- [ ] 為什麼 spec 要一次一題，learn Step 1 卻一次問完？各舉一個題目互相依賴或互相獨立的例子。
- [ ] 一個候選題出現時，你怎麼決定它該直接寫回、交給 technical-research、照 Never Block 用預設，還是問人？
- [ ] 實測 B 為什麼一題都沒問出去？要在 CI 裡跑這個 skill，你會怎麼改呼叫方式？CL-5 通過，又代表答案一定落地了嗎？

## 延伸閱讀

- **[Spec Kit `templates/commands/clarify.md`（v1.2.5）](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/clarify.md)**，搭配 PR [#1557](https://github.com/github/spec-kit/pull/1557) 與 [#3745](https://github.com/github/spec-kit/pull/3745)
  - **讀哪裡**：Execution steps 的 3–7 步；兩個 PR 的描述。
  - **學到什麼**：五階段表的原文；上限從 10 與 5 並存修成 5、問題必須是真問句的來由。
  - **和本章的關聯**：clarify-interview 的第 1、3、4、6 步直接繼承它；先讀 Ch 12 比較容易對上安裝後的 skill。
- **[Claude Code — Handle approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input)**
  - **讀哪裡**：「Handle clarifying questions」、「Question format」與「Limitations」。
  - **學到什麼**：AskUserQuestion 的輸入輸出（header 12 字元、2–4 個選項、答案以題目文字為鍵、值是 label），以及 subagent 不能用。
  - **和本章的關聯**：`templates/question.md` 的形狀與 CL-4 抓 `(Recommended)` 的理由都來自這裡；需要先懂 SDK 的 `canUseTool`。
- **《Exploring Requirements: Quality Before Design》**，Donald C. Gause、Gerald M. Weinberg（Dorset House，1989；上半部以 [Exploring Requirements One](https://www.leanpub.com/exploringrequirementsone) 重出於 Leanpub）
  - **讀哪裡**：Exploring Requirements One 第 4 章「The Tried but Untrue Use of Direct Questions」與第 6 章「Context-Free Questions」（含 6.4 Metaquestions）。
  - **學到什麼**：直接問「你要什麼」為什麼常失敗，以及不依賴特定答案、先釐清問題本身的那類問題。
  - **和本章的關聯**：題庫的 10 類就是「先問哪一類」的現代版本。書寫於 1989，談人與人的訪談；原則仍適用，工具部分可略讀。

訪談、研究、分析、tasks 綁定都備齊了，練習 C 把它們串成一條自訂流程，做一個前後端都有的小功能。

→ [練習 C：用自訂 SDD 流程做一個全端小功能](./practice-c-custom-sdd-flow.md)
