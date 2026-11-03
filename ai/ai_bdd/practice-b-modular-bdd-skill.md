# 練習 B — 把單檔 skill 拆成模組化 skill

> **目標**：把 Part 2 的方法親手走一遍。起點是一份 97 行的單檔 TDD skill `tdd-monolith`（全文在任務規格），`module_check.py` 量出 4 個問題；終點是模組化的 `booking-tdd`：29 行的脊椎、每條規則都帶 Good／Bad／Edge 例子、一份報告模板、兩支附測試的腳本，`module_check.py`、80 行門檻的 `skill_metrics.py` 與 skill-lint 都印 0 problems；再交一份改造報告，去處表涵蓋草稿的每一行，待決由你回答。

> **環境**：Python 3.12.15 與課程 venv（pytest 9.2.0、PyYAML 6.0.3）、Node v26.11.0（只跑 skill-lint）、macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/practice-b-modular/` 從空目錄開始，工具從你 Ch 11 的 skill 複製。草稿是本課手寫、模仿單檔草稿典型形狀的，不是工具產生的；參考解答的輸出都是 2026-11-02 實際跑出來的，最後的對照用了一次 `claude -p`（Claude Code 2.1.295）。

## 背景與動機

[Ch 11](./11-skill-engineering-skill.md) 讓 agent 用 skill-engineering 拆 `bdd-monolith`，人負責審。審得好的前提是自己拆過：去處表的每一列都是一個判斷，這句話是步驟、規則、事實、模板，還是該刪。腳本量得出「16 條規則沒有例子」，量不出它們該併成幾條、例子從哪裡來。這個練習換一份草稿，由你做 Ch 11 交給 agent 的第 3、4 步，腳本只負責量和驗。判斷時的總原則是 [pstack Ch 28](../pstack/28-skill-authoring.md)「四、什麼時候拆檔：references 與 scripts」那句：不是每次觸發都要讀的拆出去，程式檢查得了的寫成腳本。

草稿是會議室預約領域層的 TDD skill，教 agent 用紅、綠、重構改 `booking/schedule.py`。TDD 本身在 [Ch 19](./19-tdd-by-hand.md) 才細講，這裡只用到三個詞：紅（先寫一個會失敗的測試）、綠（最小的改動讓它通過）、重構（綠燈時整理）。產物不叫 `tdd`：[Ch 20](./20-tdd-with-agents.md) 寫明課程的 `tdd` skill 由它和 [Ch 21](./21-tdd-skill-aaa.md) 分兩章寫，同一個專案裝兩個搶同一句觸發語的 skill，只會讓 agent 挑錯。這裡叫 `booking-tdd`，和 [Ch 10](./10-templates-and-scripts.md) 的 `booking-bdd` 一樣只活在練習目錄裡。

```
 drafts/tdd-monolith/SKILL.md（97 行）          out/booking-tdd/
   frontmatter、動機段 ───────── 改寫／刪 ──▶ SKILL.md：6 個步驟、1 行 done、Files 清單
   Phase 0–4 與 Common mistakes ── 拆、併 ──▶ 步驟 ＋ rules/cycle.md（每條 Good／Bad／Edge）
                   程式判斷得了的句子 ──▶ scripts/testlist_check.py、name_check.py（＋ tests/）
   Domain notes ─── 有出處的事實 ──▶ references/schedule-api.md；沒有出處的 ──▶ 待決
   內嵌的報告模板 ────────────── 搬 ──▶ templates/tdd-report.md ＋ template_check.py（Ch 10 原樣）
```

## 任務規格

| 交付物 | 驗收標準 |
|---|---|
| `out/booking-tdd/SKILL.md` | 不超過 80 行：編號步驟、一行點名指令與輸出的 done、每個附屬檔一行的 Files 清單 |
| 結構 | `module_check.py out/booking-tdd`、`skill_metrics.py out/booking-tdd --max-lines 80 --max-inline-templates 0`、`node /tmp/lab/tools/skill-lint.mjs out` 都印 `1 skills, 0 problems` |
| `rules/*.md` | [Ch 9](./09-rule-files.md) 的格式：`## <ID> <標題>`、Trigger、Rule、只在反直覺時寫的 Why、Sign you skipped it、Enforced by，Good、Bad、Edge 各一；ID 前綴不和別章重複 |
| `templates/*.md` | 一份報告模板，`template_check.py` 讀得懂；一份好報告 0 problems，一份壞報告被擋 |
| `scripts/*.py`、`tests/` | 兩支新腳本：只用標準庫、`--help`、結束碼 0 沒問題／1 有問題／2 跑不了、最後一行摘要、每則訊息說出修法；每支都有好輸入、壞輸入的測試，故意改壞腳本時測試要紅 |
| `out/booking-tdd.report.md` | Ch 11 的 `refactor-report.md`；去處表涵蓋第 1–97 行且不重疊；不加 `--allow-open` 的 `template_check.py` 印 `1 files, 0 problems` |

```bash
mkdir -p /tmp/lab/aibdd/practice-b-modular && cd /tmp/lab/aibdd/practice-b-modular
mkdir -p drafts/tdd-monolith out tests fixtures tools
cp -R /tmp/lab/aibdd/ch11-skill-eng/skills/skill-engineering tools/
source /tmp/lab/aibdd/.venv/bin/activate && export PYTHONDONTWRITEBYTECODE=1
S=tools/skill-engineering/scripts
```

規矩照 Ch 11：不改 `drafts/`、不寫 `.claude/`（拆完由人搬），重用的模組原樣複製、ID 照舊（SK-06）。`tools/skill-engineering/` 是 Ch 11 的 skill：六支腳本（和 Ch 7、9、10、11 印出的 code block 逐字相同）、SK-01..SK-08、`refactor-report.md`。把下面全文存成 `drafts/tdd-monolith/SKILL.md`：

````markdown
---
name: tdd-monolith
description: Test-driven development for the meeting-room booking domain in booking/schedule.py. Use this skill whenever the user asks to add or change booking behaviour, fix a booking bug, or write unit tests for the Schedule class, or says TDD, test first, red-green-refactor or kata, even if they only name a rule such as R1 or R2.
---

# Test-driven development for the booking domain

This skill walks you through test-driven development (TDD) on the booking domain layer. TDD matters
here because the booking rules are full of boundaries (adjacent slots, zero-length bookings) that are
easy to get almost right, and an agent that writes the code first tends to write tests that agree with
whatever it wrote. Working test-first means every rule is pinned down by a test that failed before the
code existed, which is the only evidence that the test can catch a regression later.

## Phase 0: Orient

Before anything else, read booking/schedule.py and the tests under tests/ so you know what already
exists. Run the suite once with `python -m pytest -q` and make sure it is green before you start. If it
is red, stop and tell the user, because a red baseline hides whether your new test fails for its own reason.

## Phase 1: Write the test list

Write the behaviours you plan to cover into `TESTLIST.md` before you write any test. Each line is one
behaviour, written as a checkbox (`- [ ]`) that starts with the rule it belongs to (R1, R2, ...).

- Describe what a user of the schedule observes, not how the code does it.
- Never mention class names, method calls or data structures in the list.
- Include the boundaries: adjacent slots, equal start and end, the first and last minute of a range.
- Add an item whenever you think of a new case instead of keeping it in your head.
- Every item in `TESTLIST.md` must be ticked before you finish.

## Phase 2: Red

Pick one unticked item and write exactly one test for it.

- Name every test `test_r<N>_<behaviour>`, for example `test_r1_adjacent_slot_is_free`.
- Never reuse a test name in a file. Python keeps only the last function with that name, so the earlier test silently stops running.
- Put the test in tests/test_schedule.py unless the user names another file.
- Use the rooms, people and times of the domain (Taipei, Bob, 10:00), not placeholders.
- Run only the new test first: `python -m pytest -q tests/test_schedule.py::<name>`.
- Make sure it fails for the right reason: an `AssertionError`, or `Failed: DID NOT RAISE` when you expect an exception.
- An ImportError, NameError, AttributeError, a fixture error or a SyntaxError means the test is broken, not that the behaviour is missing. Fix the test and run it again.
- If the new test passes at once, it is not red. Either the behaviour already exists or the test is wrong.

## Phase 3: Green

Write the smallest change in booking/ that makes the new test pass.

- Do not edit the tests while you make them pass.
- Faking it with a constant is fine; the next test will force the real code.
- Run the whole suite, not only the new test, with `python -m pytest -q`.
- Tick the item in TESTLIST.md only after the whole suite is green.

## Phase 4: Refactor

With everything green, remove duplication in booking/ and in the tests. Run the whole suite after
every change and never refactor on red. Then go back to Phase 2 for the next item.

## Common mistakes

- Writing several tests before running any of them.
- Asserting only the exception type without its message.
- Changing a test so that it matches what the code does.

## Domain notes

Schedule lives in booking/schedule.py:

- `Schedule().book(room, start, end, by)` returns `Booking(id, room, start, end, by)`; ids start at 1.
- `Conflict` is raised for R1 with a message like "Taipei is taken 10:00-11:00 by Bob".
- `InvalidRange` is raised for R2 with the message "end must be after start".
- Times are `datetime.time` values and intervals are half-open: 10:00-11:00 and 11:00-12:00 do not overlap.
- R5: a booking may not be longer than two hours. Raise InvalidRange("bookings are limited to 2 hours").

## Finish with a report

When every item is ticked and the suite is green, write a short report for the user in this shape:

```markdown
# TDD report: <feature>

## Rules
- <rule id>: <one line about the rule>

## Cycles
| Item | Test | Red | Green |
|---|---|---|---|
| <item> | <test name> | <first failure line> | <passed count> |

## Refactoring
- <what changed, or none>

## Open questions
- <question, or none>
```

Make sure the report lists every cycle, that the Red column quotes the real failure line, and that the
Open questions section is never left out.
````

動手之前先量起點。同一份草稿，skill-lint（`node /tmp/lab/tools/skill-lint.mjs drafts`）印的是 `1 skills, 0 problems`：它查 frontmatter、名稱、description 與相對連結，回答「註冊得起來嗎」；結構問題只有 `module_check.py` 看得到：

```
$ python3 $S/module_check.py drafts/tdd-monolith
tdd-monolith: SKILL.md has 97 lines, budget 80; move what one step reads into a module
tdd-monolith: 16 list-item rules have no example; add one, or move the rule to rules/ with Good and Bad
tdd-monolith: no done line that names a command; write 'Done when `python3 ...` prints ...'
tdd-monolith: 1 inline templates; move each to templates/
1 skills, 4 problems
```

## 期望輸出範例

交件時的檢查（skill-lint 與報告的嚴格檢查只印 `1 skills, 0 problems`、`1 files, 0 problems`，這裡不重複）。行數和測試數可以和我們不同，最後一行要一樣：

```
$ python3 $S/module_check.py out/booking-tdd
1 skills, 0 problems
$ python3 $S/skill_metrics.py out/booking-tdd --max-lines 80 --max-inline-templates 0
booking-tdd   lines=29 sections=1 rules=18 unexampled=0 checkable_done=1 bundled=7 inline_templates=0
1 skills, 0 problems
$ python -m pytest -q -p no:cacheprovider tests
..........                                                               [100%]
10 passed in 0.18s
```

邊界情況：草稿第 36 行說「Python keeps only the last function with that name」，把它做成 `fixtures/shadow/test_shadow.py`：

```
$ cat fixtures/shadow/test_shadow.py
def test_r1_adjacent_slot_is_free():
    assert False, "this test never runs"


def test_r1_adjacent_slot_is_free():
    assert True
$ python -m pytest -q -p no:cacheprovider fixtures/shadow
.                                                                        [100%]
1 passed in 0.00s
$ python3 out/booking-tdd/scripts/name_check.py fixtures/shadow
fixtures/shadow/test_shadow.py:5: TDD-03 test_r1_adjacent_slot_is_free is defined again (first at line 1); Python keeps only this one, so pytest never runs the first; rename one
1 files, 2 tests, 1 problems
```

會失敗的那個測試從來沒跑過。Python 語言參考說函式定義是「an executable statement」，執行時把名字綁到新的函式物件，第二個 `def` 蓋掉第一個，pytest 只收得到一個。綠燈、零警告，只有讀原始碼的程式看得到。

## 如果你卡住了

1. **去處表不知道怎麼切**：先按 `##` 段落切，再把段落裡目的不同的條列分開，空行跟著前一段。寫完用這段程式查漏行與重疊（Ch 11 核對 agent 的報告用的是同一招）：

```bash
$ python3 - out/booking-tdd.report.md drafts/tdd-monolith/SKILL.md <<'PY'
import re, sys
rows = re.findall(r"(?m)^\| (\d+)-(\d+) \|", open(sys.argv[1], encoding="utf-8").read())
hits, total = [n for a, b in rows for n in range(int(a), int(b) + 1)], len(open(sys.argv[2]).read().splitlines())
print(f"{len(rows)} rows; draft has {total} lines; gaps: {sorted(set(range(1, total + 1)) - set(hits))}; "
      f"overlaps: {sorted({n for n in hits if hits.count(n) > 1})}")
PY
22 rows; draft has 97 lines; gaps: []; overlaps: []
```

2. **想不出 Edge，或 `module_check.py` 說還有條列沒例子**：Edge 是「看起來違反、其實沒有」或反過來的那一個，不准重用測試名稱的 Edge 是 `@pytest.mark.parametrize`（一個名字跑好幾個案例，不算重名）。`skill_metrics.py` 數的是 skill 裡每一份 `.md` 的條列，references 與模板也算：條列裡放一段 inline code，或改寫成 `Key: value` 的行。
3. **`script_candidates.py` 的清單看起來不完整**：它找「檢查用語加反引號」。第 26 行（清單不寫程式碼）和第 36 行（不准重用名稱）都沒被列出，卻是最值得寫成腳本的兩句；第 70 行被列出，只因為錯誤訊息裡有 must。清單是起點，逐行讀草稿才是工作。
4. **`template_check.py` 一直報 unfilled placeholder**：報告裡只要出現兩層大括號包起來的字，就算你在描述模板，它也當成沒填的佔位；這條不受 `--allow-open` 影響，兩種模式都報。換個說法寫。

## 實作步驟建議

1. **先預測，再量**：在草稿上標出步驟、規則、事實、模板與「程式判斷得了」的句子，再跑 `skill_metrics.py` 和 `script_candidates.py`，輸出貼進報告的「改造前」。對答案：掃描器多抓了哪句、漏了哪句？
2. **去處表**：每段原文一列、行號寫成範圍、依據寫 SK 編號。沒人給過的事實（SK-07）寫成 `- 待決：[NEEDS CLARIFICATION: …]`；需求的出處是 [Ch 1](./01-course-map.md)「貫穿全課的範例：會議室預約」那張表。
3. **規則檔**：一個步驟一條規則，常見錯誤併進去。Good 和 Bad 盡量用真的東西：pytest 的 E 行、真的測試名稱。挑 ID 前綴前，在 `ai/ai_bdd/` 底下 grep 一次。
4. **兩支腳本，先寫會失敗的測試**：從第 1 步的清單和逐行閱讀挑兩句。測試放在 skill 外的 `tests/`，用 subprocess 跑 CLI，因為 agent 碰到的介面就是結束碼、摘要行和訊息裡的修法。先看測試紅，再寫腳本。
5. **模板、脊椎、報告**：內嵌模板搬成 `templates/tdd-report.md`，用一份好報告、一份壞報告驗它；寫脊椎，跑 `module_check.py` 到 0 problems；新輸出貼進「改造後」，逐條回答待決，讓嚴格模式通過。最後刪掉一支腳本的核心判斷，確認測試會紅，再改回來。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### SKILL.md 與 rules/cycle.md

```markdown
---
name: booking-tdd
description: Test-first loop for the booking domain layer in booking/schedule.py - a test list, one red test at a time, the smallest change to green, refactoring on green - checked by scripts. Use when adding or changing booking behaviour, fixing a booking bug that has a cheap unit-level repro, or when asked for TDD, test first or red-green-refactor. Not for feature files or step definitions (the bdd skill), and not for API or UI tests.
---

# Booking TDD

You work in booking/ and tests/ and keep a test list in TESTLIST.md. Each step cites its rule in rules/cycle.md;
read that rule before the step. Run every script from the project root as a command of its own.

1. Orient: read booking/schedule.py and tests/, then run `python -m pytest -q` and `python3 ${CLAUDE_SKILL_DIR}/scripts/name_check.py tests`. If either reports a problem, stop and report it (TDD-01).
2. Test list: write TESTLIST.md (TDD-02), then run `python3 ${CLAUDE_SKILL_DIR}/scripts/testlist_check.py TESTLIST.md` and fix every line it prints.
3. Red: pick one open item. Read `references/schedule-api.md` and write one test in `tests/test_schedule.py`, unless the user names another file (TDD-03).
   Run `python3 ${CLAUDE_SKILL_DIR}/scripts/name_check.py tests` until it prints `0 problems`, then run the new test alone; it must fail as TDD-04 says.
4. Green: make the smallest change in booking/ (TDD-05). When `python -m pytest -q` passes, tick the item.
5. Refactor while green, rerunning `python -m pytest -q` after each change (TDD-06). Go back to step 3 while an item is open.
6. Report: copy `templates/tdd-report.md` to `reports/<feature>.md` and replace every `{{…}}`. Run
   `python3 -B ${CLAUDE_SKILL_DIR}/scripts/template_check.py ${CLAUDE_SKILL_DIR}/templates/tdd-report.md reports/<feature>.md` and fix what it prints.

Done when `python -m pytest -q` passes, `python3 ${CLAUDE_SKILL_DIR}/scripts/testlist_check.py TESTLIST.md --tests tests --done` prints `0 problems`, and template_check.py prints `0 problems` for the report.

## Files
- `rules/cycle.md`: TDD-01..TDD-06, one rule per step, each with Good, Bad and Edge examples.
- `references/schedule-api.md`: the Schedule facts a test needs, each with its source (step 3).
- `templates/tdd-report.md`: the report (step 6).
- `scripts/testlist_check.py`: the test list's items and open count; with --tests, ticked rules without a test (steps 2 and done).
- `scripts/name_check.py`: test names test_r<N>_<behaviour> and names defined twice (steps 1 and 3).
- `scripts/template_check.py`: checks the report against the template (step 6); Ch 10's script, copied unchanged. `-B` keeps its import from writing `__pycache__` into the skill.
- `scripts/template_extract.py`: imported by template_check.py; not run on its own.
```

```markdown
# TDD cycle rules

Read the rule a step of SKILL.md cites before you do that step. Cite rules by ID (`TDD-04`) in the report.
IDs are stable: a retired rule leaves a gap and its number is never reused.

## TDD-01 Start from a green suite and clean names
Trigger: before the first new test of a session.
Rule: run the whole suite and name_check.py first; if either reports a problem, stop and report it instead of adding a test.
Why: on a red baseline you cannot tell whether a new test fails for its own reason, and renaming old tests is a refactoring someone asks for.
Sign you skipped it: the first pytest run of the session already contains your new test.
Enforced by: review (the report's first cycle)
Good: `python -m pytest -q` prints `4 passed` and name_check.py prints `0 problems`; then the first new test.
Bad: writing test_r2_zero_length_booking_is_rejected while test_r1_overlapping_slot_is_rejected fails.
Edge (a collection error is red although no test failed): `Interrupted: 1 error during collection`

## TDD-02 The test list names behaviour, one rule per item
Trigger: writing or changing TESTLIST.md.
Rule: one `- [ ] R<N> <behaviour>` item per behaviour, in the words a user of the schedule would use; no code.
Sign you skipped it: a backtick, a call such as book(...), or an item without R<N>.
Enforced by: testlist_check.py
Good: `- [ ] R1 adjacent slot is free: Bob 10:00-11:00, then Alice 11:00-12:00`
Bad: `- [ ] R1 Schedule.book() checks self._bookings for overlaps`
Edge (a boundary is an item of its own, not a note on another): `- [ ] R2 zero-length booking 10:00-10:00 is rejected`

## TDD-03 One test at a time, named for its rule
Trigger: writing a test function.
Rule: write one test for one open item and run it before the next; name it test_r<N>_<behaviour>; never reuse a name.
Why: Python keeps only the last def of a name, so a reused name deletes the earlier test without an error.
Sign you skipped it: several new tests in one red run, or pytest counting fewer tests than the file has defs.
Enforced by: name_check.py (names); review (one at a time)
Good: `def test_r1_adjacent_slot_is_free():`
Bad (two defs, `1 passed`, the first never ran): `def test_booking():` twice in tests/test_schedule.py
Edge (parametrize gives one name several cases; that is not a reuse): `@pytest.mark.parametrize("end", [time(10), time(9)])`

## TDD-04 Red means the behaviour is missing, not that the test is broken
Trigger: the first run of a new test.
Rule: the new test must fail on its assertion; an error means it cannot run yet: fix the test, or add the empty class or method it imports or calls, and run it again.
Sign you skipped it: a green run right after a red run whose last E line is an ImportError, NameError or SyntaxError.
Enforced by: review (the report's Red column quotes the E line)
Good: `E       Failed: DID NOT RAISE Conflict`
Bad: `E   ModuleNotFoundError: No module named 'booking'`
Edge (red for the right reason today, green for a wrong message tomorrow): `pytest.raises(Conflict, match="Taipei is taken 10:00-11:00 by Bob")`, not `pytest.raises(Conflict)`

## TDD-05 Green is the smallest change, and the tests stay as they are
Trigger: making a red test pass.
Rule: change only booking/; a constant is fine; tick the item after the whole suite passes.
Sign you skipped it: a diff under tests/ in a green step, or a ticked item with no test named for its rule.
Enforced by: testlist_check.py --tests (ticked rules); review (the diff)
Good: `return Booking(1, room, start, end, by)` to pass test_r1_free_room_is_booked
Bad: editing the expected message in the test until it matches what the code prints.
Edge (a new test that passes at once is not red; tick it only after you confirm the behaviour exists): `1 passed` on its first run

## TDD-06 Refactor only on green
Trigger: removing duplication in booking/ or tests/.
Rule: refactor only while the whole suite is green, and rerun it after every change.
Sign you skipped it: a refactoring with no pytest run after it, or one started while a test was red.
Enforced by: review
Good: `python -m pytest -q` prints `5 passed` after the overlap check moves into Booking.overlaps.
Bad: renaming a method while test_r2_zero_length_booking_is_rejected still fails.
Edge (a test that pins down existing behaviour is not a refactoring): give it an item and a Red step of its own.
```

脊椎每一步點名一條規則，判斷得了的地方點名一支腳本，done 行是三個指令和它們要印的字（SK-05）；第 1 步的 `name_check.py` 是人審時加的，理由在解答說明。規則的前綴用 `TDD`，因為 `TC` 是 [Ch 16](./16-tasks-binder.md) 的、`TD` 是 [Ch 33](./33-truth-vs-plan.md) 的；`module_check.py` 只查一個 skill 裡的重號，跨 skill 撞號靠 review（SK-06）。只有 TDD-01、TDD-03 寫 Why：少了它，agent 會覺得「先跑舊測試」「名字別重複」是多餘的囉嗦。TDD-04 改了草稿：草稿把 AttributeError 一律算成測試壞了，但呼叫還沒寫的方法也會得到它（我們跑出來的是 `E       AttributeError: 'Schedule' object has no attribute 'cancel'`），這時該補的是空方法，不是改測試；匯入還沒寫的例外類別得到的 ImportError 同理，Ch 19 的 C3 就是先補空的 `Conflict` 才紅對原因。

### references、模板、兩支腳本與測試

```markdown
# Schedule: the facts a test needs

Read this before you write a test (step 3). booking/schedule.py outranks this page: check it first and report any difference.

- `Schedule().book(room, start, end, by)` returns `Booking(id, room, start, end, by)`; ids start at 1. (draft; Ch 19's code)
- R1: `Conflict("Taipei is taken 10:00-11:00 by Bob")` names the room, the taken slot and who holds it. (Ch 1; Ch 19's code)
- R2: `InvalidRange("end must be after start")`, also when end equals start. (Ch 1; Ch 19's code)
- Times are `datetime.time`; intervals are half-open, so `10:00-11:00` and `11:00-12:00` do not overlap. (Ch 1; Ch 19's code)
- Test data: rooms `Taipei`, `Tokyo`, `Seoul`; people `Alice`, `Bob`, `Carol`. (Ch 1)
```

```markdown
<!-- tdd-report.md: written by hand in template_extract.py's format, so template_check.py can read it -->
# TDD report: {{feature}}
## Rules
<!-- shape: "- a9:" e.g. "- R1:" -->
{{Rules: one line per rule this session touched, e.g. "- R1: a room cannot be booked twice for overlapping times"}}
## Cycles
| Item | Test | Red | Green |
|---|---|---|---|
<!-- shape: "| a9" e.g. "| R1" -->
{{Cycles: one row per ticked item; Red quotes the E line (TDD-04), e.g. "| R2 zero-length ... | test_r2_... | E ... | 5 passed |"}}
## Refactoring
{{Refactoring: what changed while green, e.g. "- moved the overlap test into Booking.overlaps", or "- none"}}
## Open questions
<!-- write "- none", or one "- [NEEDS CLARIFICATION: <question>]" per question; a person answers each before the report is final -->
{{Open questions: e.g. "- none"}}
```

模板留著草稿的四段和英文標題，重構不改變交出去的東西；形狀規則照 Ch 10 的格式手寫，因為草稿裡唯一的「範例」本身就是模板，`template_extract.py` 要兩份以上真的報告才歸納得出來。驗它之前，把兩支 Ch 10 腳本原樣複製進 skill（`cp $S/template_check.py $S/template_extract.py out/booking-tdd/scripts/`）。`fixtures/report-r1.md` 記我們實際跑過的一個 R1 循環，共 11 行、標題與內容之間不留空行：標題 `# TDD report: R1 overlapping bookings`，Rules 一行 `- R1: a room cannot be booked twice for overlapping times`，Cycles 照抄表頭再加一列 `| R1 overlapping slot is rejected | test_r1_overlapping_slot_is_rejected | E       Failed: DID NOT RAISE Conflict | 1 passed |`，Refactoring 與 Open questions 各一行 `- none`；`report-broken.md` 是它的前 9 行，情境列刪掉開頭的 `R1 `（每個標題後多一個空行，前 9 行就切在別處，結果變成 `2 files, 3 problems`）。`python3 -B out/booking-tdd/scripts/template_check.py out/booking-tdd/templates/tdd-report.md fixtures/report-r1.md fixtures/report-broken.md` 印出 `fixtures/report-broken.md:1: no heading matches "## Open questions"; copy it in`、`fixtures/report-broken.md:7: 1 line(s) in "Cycles" should start like "| R1" (shape "| a9"), e.g. "| overlapping slot i"` 與 `2 files, 2 problems`。

```python
#!/usr/bin/env python3
"""testlist_check.py: check a TDD test list (TDD-02, TDD-05) and count the items still open.
Under the title, a line is blank, a heading, a <!-- comment -->, or "- [ ] R<N> <behaviour>" ("- [x]" once green).
Problems: any other line; an item without its rule ID; code in an item (backticks, a call like book(...), a dotted
name like Schedule.book); with --tests DIR, a ticked R<N> that no test_r<N>_* function under DIR covers; with
--done, every open item. Exit: 0 ok, 1 problems, 2 cannot run."""
import argparse, ast, re, sys
from pathlib import Path

ITEM, RULE = re.compile(r"- \[( |x)\] (.*)"), re.compile(r"R(\d+)\s+\S")
CODE = re.compile(r"`|\b[A-Za-z_]\w*\(|\b[A-Za-z_]\w+\.[A-Za-z_]\w*")

def tested_rules(root):
    """Rule numbers N that some test_r<N>_* function under root covers."""
    files = [f for f in root.rglob("*.py") if f.name.startswith("test_") or f.name.endswith("_test.py")]
    defs = [n.name for f in files for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"), str(f)))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return {m[1] for name in defs if (m := re.match(r"test_r(\d+)_", name))}

def check(path, tests, done):
    probs, items, open_ = [], 0, 0
    say = lambda n, msg, rule="TDD-02": probs.append(f"{path}:{n}: {rule} {msg}")
    covered = tested_rules(tests) if tests else None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not (s := line.strip()) or s.startswith(("#", "<!--")):
            continue
        if not (m := ITEM.fullmatch(s)):
            say(n, f'"{s[:30]}" is not an item; write "- [ ] R<N> <behaviour>"')
            continue
        items, ticked, text = items + 1, m[1] == "x", m[2]
        open_ += not ticked
        if not (r := RULE.match(text)):
            say(n, f'"{text[:30]}" names no rule; start it with R<N>, e.g. "R1 adjacent slot is free"')
        if c := CODE.search(text):
            say(n, f'"{c[0]}" is code; write what a user of the schedule observes instead')
        if ticked and r and covered is not None and r[1] not in covered:
            say(n, f"R{r[1]} is ticked but no test is named test_r{r[1]}_*; write the test or untick it", "TDD-05")
        if done and not ticked:
            say(n, f'"{text[:30]}" is still open; finish it, or move it to the report\'s Open questions', "DONE")
    return probs, items, open_

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("testlist", type=Path, help="the test list, e.g. TESTLIST.md")
    ap.add_argument("--tests", type=Path, help="test directory: every ticked rule needs a test_r<N>_* there")
    ap.add_argument("--done", action="store_true", help="finishing: an open item is a problem")
    a = ap.parse_args()
    if a.tests and not a.tests.is_dir(): ap.error(f"--tests {a.tests} is not a directory")
    probs, items, open_ = check(a.testlist, a.tests, a.done)
    print(*probs, f"{items} items, {open_} open, {len(probs)} problems", sep="\n")
    return 1 if probs else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "problems found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

```python
#!/usr/bin/env python3
"""name_check.py: check test names against TDD-03. PATH = test files or directories (test_*.py, *_test.py).
A test is a def test* at module level or in a class Test*. Problems: a name that is not test_r<N>_<behaviour> in
lower snake case; a name defined twice in one module or class (Python keeps the last def, so pytest never runs
the first); a file that does not parse. Exit: 0 ok, 1 problems, 2 cannot run."""
import argparse, ast, re, sys
from pathlib import Path

NAME = re.compile(r"test_r\d+(_[a-z0-9]+)+")

def check(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except SyntaxError as e:
        return [f"{path}:{e.lineno}: PARSE cannot parse: {e.msg}; fix the file first"], 0
    probs, count = [], 0
    for body in [tree.body] + [c.body for c in tree.body if isinstance(c, ast.ClassDef) and c.name.startswith("Test")]:
        seen = {}
        for f in body:
            if not (isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test")):
                continue
            count += 1
            if f.name in seen:
                probs.append(f"{path}:{f.lineno}: TDD-03 {f.name} is defined again (first at line {seen[f.name]}); "
                             "Python keeps only this one, so pytest never runs the first; rename one")
            elif not NAME.fullmatch(f.name):
                probs.append(f"{path}:{f.lineno}: TDD-03 {f.name} does not name its rule; "
                             "rename it test_r<N>_<behaviour>, e.g. test_r1_adjacent_slot_is_free")
            seen.setdefault(f.name, f.lineno)
    return probs, count

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="test files or directories")
    a = ap.parse_args()
    files = sorted({f for p in a.paths for f in ([p] if p.is_file() else p.rglob("*.py"))
                    if f.name.startswith("test_") or f.name.endswith("_test.py")})
    if not files: ap.error(f"no test_*.py or *_test.py under {', '.join(map(str, a.paths))}")
    results = [check(f) for f in files]
    probs = [p for r, _ in results for p in r]
    print(*probs, f"{len(files)} files, {sum(c for _, c in results)} tests, {len(probs)} problems", sep="\n")
    return 1 if probs else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "problems found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

```python
"""Black-box tests for the booking-tdd skill's two scripts: each case writes files, runs the CLI, checks exit and text."""
import subprocess, sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "out/booking-tdd/scripts"
TWICE = "def test_r1_free():\n    assert False\n\n\ndef test_r1_free():\n    assert True\n"
CASES = [  # id, script, files to write, arguments, exit code, text the output must contain
    ("open-is-not-a-problem", "testlist_check.py", {"T.md": "- [x] R1 overlap is rejected\n- [ ] R1 adjacent slot is free\n"},
     ["T.md"], 0, "2 items, 1 open, 0 problems"),
    ("done-needs-all-ticked", "testlist_check.py", {"T.md": "- [ ] R1 adjacent slot is free\n"},
     ["T.md", "--done"], 1, 'T.md:1: DONE "R1 adjacent slot is free" is still open'),
    ("code-in-an-item", "testlist_check.py", {"T.md": "- [ ] R1 Schedule.book() rejects overlaps\n"},
     ["T.md"], 1, 'TDD-02 "Schedule.book" is code'),
    ("item-without-rule", "testlist_check.py", {"T.md": "- [ ] adjacent slot is free\n"}, ["T.md"], 1, "names no rule"),
    ("ticked-without-test", "testlist_check.py", {"T.md": "- [x] R2 zero-length is rejected\n", "t/test_a.py": "def test_r1_x():\n    pass\n"},
     ["T.md", "--tests", "t"], 1, "TDD-05 R2 is ticked but no test is named test_r2_*"),
    ("missing-list", "testlist_check.py", {}, ["nope.md"], 2, "error: FileNotFoundError"),
    ("names-pass", "name_check.py", {"test_a.py": "def test_r1_free():\n    pass\n"}, ["."], 0, "1 files, 1 tests, 0 problems"),
    ("name-defined-twice", "name_check.py", {"test_a.py": TWICE}, ["test_a.py"], 1,
     "test_a.py:5: TDD-03 test_r1_free is defined again (first at line 1)"),
    ("name-without-rule", "name_check.py", {"test_a.py": "def test_booking():\n    pass\n"}, ["."], 1,
     "TDD-03 test_booking does not name its rule"),
    ("no-test-files", "name_check.py", {}, ["."], 2, "no test_*.py or *_test.py under ."),
]


@pytest.mark.parametrize("script,files,args,code,text", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_script(tmp_path, script, files, args, code, text):
    for name, body in files.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(body)

    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=tmp_path, capture_output=True, text=True)

    assert (r.returncode, text in r.stdout + r.stderr) == (code, True), r.stdout + r.stderr
```

這份 `tests/test_scripts.py` 一張表十個案例，每支腳本都有好輸入、壞輸入、跑不了（結束碼 2）三種。弄壞一次：刪掉 `name_check.py` 第 23–25 行的重名判斷、把第 26 行的 `elif` 改成 `if`，再跑 `python -m pytest -q -p no:cacheprovider tests`，得到 `1 failed, 9 passed in 0.19s`：紅的是 `FAILED tests/test_scripts.py::test_script[name-defined-twice] - AssertionErro...`，`E` 行是壞掉的腳本在重名的檔上印出的 `1 files, 2 tests, 0 problems`（摘要依終端機寬度截斷，輸出導向檔案或管線時退回 80 欄，Ch 11 第四節看過；寬的終端機會印出整句）。改回來是 `10 passed`。

### 改造報告的去處表

````markdown
## 去處
| 原文行 | 去處 | 依據 | 說明 |
|---|---|---|---|
| 1-4 | SKILL.md frontmatter | SK-01 | name 改成 booking-tdd（tdd 是 Ch 20–21 的課程 skill）；description 補上 Not for 的邊界 |
| 5-7 | SKILL.md 標題 | SK-01 | 照搬 |
| 8-13 | 刪 | SK-08 | 動機段；規則本身不需要它 |
| 14-19 | SKILL.md 步驟 1、rules/cycle.md TDD-01 | SK-02 | because 那句留成 TDD-01 的 Why；步驟 1 加跑 name_check.py |
| 20-24 | SKILL.md 步驟 2、TDD-02 的 Rule | SK-02 | 項目格式 `- [ ] R<N> <behaviour>` |
| 25-28 | TDD-02 的 Good、Bad、Edge；不寫程式碼由 testlist_check.py 擋 | SK-03 | 第 26 行掃描器沒抓到，人補的 |
| 29-30 | testlist_check.py --done、done 行 | SK-05 | 「全部打勾才算完」變成指令 |
| 31-35 | SKILL.md 步驟 3、TDD-03、name_check.py | SK-03 | 命名規則由腳本檢查 |
| 36-36 | name_check.py 的重複名稱檢查、TDD-03 的 Why | SK-03 | 掃描器沒抓到；同名會讓前一個測試消失 |
| 37-38 | SKILL.md 步驟 3、references/schedule-api.md 的測試資料 | SK-01 | 測試資料補上出處（Ch 1） |
| 39-39 | SKILL.md 步驟 3 | SK-01 | 先單獨跑新測試 |
| 40-41 | TDD-04 | SK-02 | 紅燈原因留給 review，見待決 |
| 42-43 | TDD-05 的 Edge | SK-02 | 一跑就綠的新測試不算紅 |
| 44-47 | SKILL.md 步驟 4 | SK-01 | 照搬 |
| 48-51 | TDD-05；打勾前要有測試由 testlist_check.py --tests 擋 | SK-03 | 第 51 行變成檢查 |
| 52-57 | SKILL.md 步驟 5、TDD-06 | SK-02 | 照搬，加例子 |
| 58-63 | TDD-03 的 Sign、TDD-04 的 Edge、TDD-05 的 Bad | SK-06 | 三條常見錯誤併進已有的規則，不另開 ID |
| 64-71 | references/schedule-api.md | SK-01 | 只有寫測試那一步讀；每行標出處，程式碼優先 |
| 72-73 | 刪，進待決 | SK-07 | R5 沒有出處 |
| 74-77 | SKILL.md 步驟 6、done 行 | SK-05 | 交報告的時機變成指令 |
| 78-94 | templates/tdd-report.md | SK-04 | 內嵌模板搬出去，改成 template_check.py 讀得懂的格式 |
| 95-97 | template_check.py（段落、未填的佔位）、TDD-04 的 Enforced by | SK-04 | 紅燈欄要引用真的 E 行，留給 review |
````

「改造前」「改造後」兩段是第 1 步與第 5 步的輸出原樣，不重印。「如果你卡住了」第 1 點的程式核對過這張表，沒有漏行也沒有重疊。待決共 7 條，交件時有 4 條是 `[NEEDS CLARIFICATION]`（R5、AttributeError、紅燈原因要不要寫成腳本、`python` 指向哪個直譯器），那時 `--allow-open` 是 `1 files, 0 problems`、嚴格模式報 4 個 `open question`；人答完改成 `- 已決：…` 才是 0。

### 對照：讓 skill-engineering 拆同一份

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，模型 claude-opus-5-5）。

我們用 Ch 11 的 `run-agent.sh`（只換 `LAB`）在 `agent/` 跑了一次：那裡有 `.claude/skills/skill-engineering/`、這份草稿、`docs/rules.md`（Ch 1 那張表的原文）和空的 `out/`；prompt 要它拆成名叫 `tdd` 的 skill（這次執行早於我們改名），開放問題寫進報告。7 分 48 秒、99 個 tool call、US$2.09，`--max-turns 20` 一樣沒擋住它，19 次被拒裡 14 次是 `cd /tmp/tdd-scratch && …` 的組合指令。它的產物 `module_check.py` 與 80 行門檻的 `skill_metrics.py` 都是 0 problems（26 行脊椎、三個規則檔 14 條），嚴格模式的報告是 `1 files, 19 problems`，19 條待決等人答。

判斷有三處和我們相同：R5 刪掉進待決；也寫了清單與名字檢查，它的 `testname_check.py` 在 `fixtures/shadow` 抓到同一個重名；也看出 AttributeError 的矛盾，但保留草稿的規則、寫成待決。不同處更有意思：它多寫一支 `red_check.py`，自己用 `--junitxml` 跑一個測試再貼標籤（和 Ch 25 的 `red_gate.py` 同一個思路，在我們的 R1 紅燈上印 `red: Failed: DID NOT RAISE Conflict`）；它刪光 `Schedule` 的介面事實，因為 `agent/` 裡沒有 `booking/` 程式可以對照（待決裡它自己這麼說），SK-07 照字面執行，而我們以 Ch 19 的程式為出處留下；它的 description 原封不動，腳本用 `python` 呼叫，也沒有能重跑的腳本測試。隔離有個破口：它的第 14–16 次呼叫用 Glob、Grep 搜了整個 `/tmp/lab/aibdd`，參考解答就在隔壁的 `out/`。Grep 結果約 35KB，超過輸出上限，它只看到前 2KB 的檔名預覽，沒讀到答案。我們推論原因是 allow 清單寫了不帶路徑的 `Read`、`Glob`、`Grep`（permissions 文件：不帶路徑的 `Read`「Matches all file reads」），工作目錄外的讀取因此被預先核准，沒有另做實驗驗證。重做盲測前，把允許的讀取限縮在專案裡；這個修法本段未實測，為理論預期行為。它也在專案外的 `/tmp/tdd-scratch/` 留了暫存檔，我們刪了。只跑一次，是樣本，不是結論。

**解答說明**：

- **為什麼是這兩支腳本**：清單打勾卻沒有測試、名字重複讓測試消失，判斷單純、錯了又沒人發現。第 40 行（紅燈要因為對的原因）`script_candidates.py` 也列了，我們留給 review：讀終端機輸出會撞上截斷；讀 junit XML 的版本是 [Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py`，不在 Part 2 長一支平行的。
- **測試放在 skill 外面**：skill 裡的檔是給 agent 讀的，`module_check.py` 要求每個都被 SKILL.md 點名；腳本測試是給改腳本的人跑的，`evals/` 放的又是 skill 行為的評估素材。代價是只複製 skill 目錄的人會漏掉測試，所以兩者放同一個 repo 的相鄰目錄。黑箱測 CLI 是因為 agent 用的是指令，import 函式來測，測不到 argparse 與結束碼；十個案例約 0.2 秒。例子一行一個、緊跟著規則，整個檔才看得懂的例子才另外放（Ch 21 的 `templates/test_unit.py` 那種）。
- **第 1 步為什麼加了 `name_check.py`**：人審時我們拿兩支腳本量了 Part 4 已建好的兩個專案（做完 Ch 19、21 可以重跑）。Ch 21 照 `test_r<N>_` 命名，`name_check.py tests` 是 `1 files, 5 tests, 0 problems`；Ch 19 手打的 kata 沒有這個慣例，`2 files, 8 tests, 8 problems`（含它進階一節的 `test_overlap_oracle.py`），清單照 done 行加 `--tests tests --done` 是 `7 items, 0 open, 7 problems`。原本只有第 3 步要求 `0 problems`，等於叫 agent 在紅燈步驟裡順手改八個舊名字；現在基準不乾淨就停下來回報，改名是有人要求才做的重構。
- **踩過的雷**：`template_check.py` 匯入 `template_extract.py` 時，把 `__pycache__/template_extract.cpython-312.pyc` 寫進 `out/booking-tdd/scripts/`，`bundled` 從 7 變 8；`module_check.py` 略過 `__pycache__` 所以不報，它卻會跟著 skill 搬進 `.claude/`，所以實驗目錄 export `PYTHONDONTWRITEBYTECODE=1`、第 6 步寫 `python3 -B`。另外，Claude Code 裡的 `python3` 可能是系統的 3.9.6（Ch 7 的 transcript 看過，Ch 11 第一節有引用），兩支新腳本只用標準庫，四支腳本的 `--help` 我們都用 `/usr/bin/python3` 跑過。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| 清單一項沒打勾，不加 `--done` | `2 items, 1 open, 0 problems`，exit 0 | 寫清單的階段，開著的項目是正常的 |
| 同一份清單加 `--done` | `DONE ... is still open`，exit 1 | 完成條件 |
| 清單項目寫了 `Schedule.book()` | `TDD-02 "Schedule.book" is code` | 清單寫行為，不寫設計 |
| R2 打勾，測試只有 `test_r1_x` | `TDD-05 R2 is ticked but no test is named test_r2_*` | 只到規則層級，看不出「這一項」有沒有測試 |
| 同名的兩個 `def` | `TDD-03 ... is defined again (first at line 1)`；pytest 是 `1 passed` | pytest 自己看不到的錯 |
| 清單檔不存在；目錄裡沒有測試檔 | 都是 exit 2：前者一行 `error: FileNotFoundError: ...`，後者是 argparse 的 usage 加 `name_check.py: error: no test_*.py ...` | 跑不了不能看起來像「有問題」 |
| `report-r1.md` 與 `report-broken.md` 一起檢查 | `2 files, 2 problems`，都出自 `report-broken.md` | 模板擋得住少一段與形狀不對 |

## 延伸挑戰（加分）

- **把覆蓋檢查做進工具**：照 Ch 11 動手練習 1，把「如果你卡住了」那段程式做成 `module_check.py --map REPORT --source DRAFT`；刪掉報告的一列，確認它說出缺了哪幾行。
- **讓清單和測試一對一**：`--tests` 只到規則層級，R1 有任何一個測試，所有 R1 項目都算有。設計一個標記讓每一項點名它的測試，想想代價；[Ch 24](./24-testplan-skill.md) 的 TP 編號做的就是這件事。
- **盲測一次**：照 Ch 11 第五節的協定，讓 agent 在同一個專案用 `tdd-monolith` 與 `booking-tdd` 各做一次「加上 R3 辦公時間」；除了驗收探針，再訂一筆 10:00–13:00，看哪一組實作了草稿虛構的 R5。先補好對照裡那個讀取破口。

## 自我檢核

- [ ] 不看參考解答，說出草稿的動機段、Common mistakes、Domain notes 與內嵌報告模板在改造後各去了哪裡、依據哪一條 SK 規則。
- [ ] skill-lint 讓草稿全過，`module_check.py` 卻報 4 個問題。兩支工具各回答哪一個問題？
- [ ] 重用測試名稱為什麼 pytest 看不到、`name_check.py` 看得到？用 Python 執行 `def` 的語意解釋。
- [ ] `script_candidates.py` 漏了哪兩句、誤報了哪一句？「紅燈要因為對的原因」它抓到了，為什麼我們沒寫成腳本？
- [ ] 第 1 步為什麼最後多了 `name_check.py`？沒改的話，agent 在一個舊專案上會做出什麼事？
- [ ] 能說出自己的拆法和參考解答的差異，並解釋各自取捨。

## 延伸閱讀

- **[Canon TDD](https://newsletter.kentbeck.com/p/canon-tdd)** — Kent Beck（2023-12-11）
  - **讀哪裡**：五個步驟，以及 Test List、Write a Test 兩段以「Mistake:」開頭的句子（「mixing in implementation design decisions」「convert all the items on the Test List into concrete tests」）。
  - **學到什麼／關聯**：TDD-02「清單寫行為、不寫程式碼」與 TDD-03「一次一個測試」在 Beck 自己文字裡的出處。知道紅、綠、重構三個詞就讀得懂。
- **[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)** — Anthropic（Claude Developer Platform 文件）
  - **讀哪裡**：「Advanced: Skills with executable code」底下的「Solve, don't defer」「Provide utility scripts」「Create verifiable intermediate outputs」，與「Common patterns」的「Examples pattern」。
  - **學到什麼／關聯**：腳本要自己處理錯誤、訊息要具體到照著就能修（「Make validation scripts verbose with specific error messages」），是兩支腳本每則訊息都帶修法的依據；例子比描述更能傳達形狀，是規則檔三個例子的依據。
- **[Extend Claude with skills](https://code.claude.com/docs/en/skills)** — Claude Code 文件；配 **[Python 語言參考 §8.7](https://docs.python.org/3/reference/compound_stmts.html#function-definitions)**
  - **讀哪裡**：前者的「Add supporting files」「Available string substitutions」（`${CLAUDE_SKILL_DIR}`）「Skill content lifecycle」；後者文法區塊之後的頭兩句。
  - **學到什麼／關聯**：「Keep `SKILL.md` under 500 lines」是官方的建議（一則 tip，不是硬限制），80 行是本課為了逼出拆分的預算；附屬檔要從 SKILL.md 點名並說明何時讀，這是 Files 清單一行一檔的理由。§8.7 說明函式定義是會執行的敘述，所以同名的 `def` 會讓前一個測試消失。

`booking-tdd` 把「怎麼做」拆乾淨了，報告的待決卻又一次指向沒人寫下來的「要做什麼」：R5 該不該存在，skill 說了不算，需求文件說了才算。Part 3 從那份文件開始，先用 Spec Kit 1.2.5 實際建一個專案，看它裝進 Claude Code 的每個 skill 讀寫哪些檔。

→ [Ch 12 Spec Kit 1.x 重訪：指令變成 skill 之後](./12-spec-kit-1x.md)
