# Ch 24 — testplan skill：測試計畫是最可靠的計畫

> **目標**：寫任何程式之前，從 spec 的驗收條件推出一份測試計畫（test plan）：每個案例有穩定編號 `TP-xxx`、層級、優先度、Arrange/Act/Assert 三行與追溯（R、FR、情境），另附不測清單（out of scope）。用 `testplan_lint.py` 檢查結構與追溯，用 reviewer 的一個問題抓出 lint 看不到的重複與缺口，把做法包成 `testplan` skill 交給 agent，最後讓每個 P1 案例變成一個 `test_tpNNN_*` 紅燈測試，由同一支 lint 確認計畫與測試一一對應。

> **環境**：Python 3.12.15、pytest 9.2.0（[Ch 0](./00-environment-setup.md) 的共用 venv）、Claude Code 2.1.295，macOS arm64。實驗目錄 `/tmp/lab/aibdd/ch24-testplan/`，從空目錄開始。所有輸出都在 2026-11-02 實際跑過，三次 `claude -p` 是實際執行紀錄。

## 為什麼需要這個？

[Ch 19](./19-tdd-by-hand.md) 的 kata 從開發者自己列的測試清單開始，[Ch 22](./22-backend-e2e-first.md)、[Ch 23](./23-frontend-e2e-playwright.md) 從某人挑好的驗收測試開始。清單是誰挑的？寫程式前有人審過嗎？換成 agent，這張清單常常根本不存在：它讀完 spec 就寫程式，最後補幾個會過的測試。[SDD Ch 44](../spec_driven_development/44-trust-ladder.md) 階段 3 引用的回報正是「it would forget to create or run tests」，那一階的護欄是驗收清單要在任務核准時就寫定。

測試計畫原本是很重的文件：IEEE 829 出過 1983、1998、2008 三版，2008 版定義了 Master Test Plan、Level Test Plan、Level Test Case 等十種文件，後來被 ISO/IEC/IEEE 29119 系列（2013、2015）取代。敏捷團隊丟掉它，改用 Kent Beck 的測試清單（test list）與 BDD 情境，清單又回到開發者腦中或 feature 檔裡。repo 已有幾塊拼圖：[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)「驗證規則：測試不夠」把 Verify 區塊放進每個 PR 的計畫並由 check-plan 檢查，[pstack 練習 D](../pstack/practice-d-plan-and-log.md) 示範 lint 全過、lane 卻是複製來的，[SDD Ch 11](../spec_driven_development/11-ears-notation.md)「EARS + 測試矩陣：系統化覆蓋邊界」列出每種句型的測試軸，[SDD Final](../spec_driven_development/final-project-ship-with-sdd.md) 的 5b 驗收矩陣則在實作後才填。缺的是寫程式前的獨立產物：一個檔案、每案有編號、一支 lint，而且接得上測試。pstack 的作者說「i don't believe in planning. the best spec is code.」（[pstack Ch 1](../pstack/01-what-is-pstack.md)「四、它不做什麼」），他反對的是「polishing a plan that no code has tested」。測試計畫正好相反：每一行幾分鐘內就變成一個會跑、會紅的測試，計畫立刻被程式碼檢驗。它是最接近程式碼、也最容易被證偽的計畫，所以最可靠。

## 先建立直覺：先出考卷，再上課

想像老師開學前就把期末考卷出好：每題有題號（`TP-001`），註明考哪一課（trace），題型分選擇、計算、申論（unit、api、ui），有配分（priority）和標準答案（assert），最後印一行「不考範圍」（out of scope）。考卷先給另一位老師看：有沒有兩題考同一件事？有沒有整課沒考到？之後的教學（實作）照著考卷走，閱卷是機械的。這個類比有兩處不成立：考卷要保密，測試計畫要攤開給實作者看；考卷出完就定了，計畫會長大，review 後補的案例接在最後，編號永不重排。

```
spec.md        R1 R2 · FR-001..005 · @sc_book_01..04
   │  推導：每條規則找出翻轉點，兩側各一案
   ▼
testplan.md    TP-001..TP-010（level · priority · kind · trace · arrange · act · assert）
   │  testplan_lint.py --source spec.md        結構、追溯、覆蓋（TPL-01..08）
   │  reviewer：這案抓得到哪個別案抓不到的 bug？  lint 看不到
   ▼
tests/unit/    test_tp001_… 一案一測，全部紅燈；testplan_lint.py --tests 對帳（TPL-09..11）
   ▼
實作           Ch 25：過了 RED 閘門才准寫
```

## 一、從驗收條件推出案例

建好 `/tmp/lab/aibdd/ch24-testplan/specs/001-booking/`，`source /tmp/lab/aibdd/.venv/bin/activate`，寫一份精簡過的 Spec Kit 1.2.5 形狀 spec（省略 Success Criteria 等段落；完整模板是 `specify init` 建出的 `.specify/templates/spec-template.md`，[Ch 12](./12-spec-kit-1x.md) 第四節有一份照它長出來的 spec）。情境以 `@sc_book_NN` 編號，R1、R2 標在 FR 後面；`@sc_book_04` 的 Then 把訊息原文加了引號，和全課綁 step 用的 R2 句子（[練習 A](./practice-a-first-loop.md) 的 `the booking is rejected because the end must be after the start`）措辭不同，這份 spec 只被讀 ID、不綁 step。`specs/001-booking/spec.md`：

```markdown
# Feature Specification: Book a meeting room

**Feature Branch**: `001-booking`
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Book a free room (Priority: P1)

Alice picks a room and a same-day time range. She gets it unless someone already holds an overlapping slot.

**Acceptance Scenarios**:

1. `@sc_book_01` **Given** room "Taipei" is free all day, **When** Alice books "Taipei" from 10:00 to 11:00, **Then** the booking is confirmed
2. `@sc_book_02` **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice books "Taipei" from 11:00 to 12:00, **Then** the booking is confirmed
3. `@sc_book_03` **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice books "Taipei" from 10:30 to 11:30, **Then** the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
4. `@sc_book_04` **Given** room "Taipei" is free all day, **When** Alice books "Taipei" from 11:00 to 10:00, **Then** the booking is rejected because "end must be after start"

### Edge Cases

- Ranges are half-open `[start, end)`: 10:00-11:00 and 11:00-12:00 do not overlap.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST let a person book a room for a same-day range and return the booking with an id; ids start at 1.
- **FR-002** (R1): System MUST reject a booking that overlaps a booking of the same room, naming the holder: `Taipei is taken 10:00-11:00 by Bob`.
- **FR-003** (R2): System MUST reject a booking whose end is not after its start: `end must be after start`.
- **FR-004**: `POST /bookings` MUST answer 409 for R1 and 422 for R2, with the message in `detail`.
- **FR-005**: When a booking is rejected, the booking page MUST show `Rejected: <detail>` in its status line.

## Assumptions

- Office hours (R3) and cancelling (R4) are separate features.
```

推案例的方法：每條規則找出判定翻轉的地方，兩側各寫一案；規則每一種被打破的方式寫一案；沒有任何合理 bug 分得出來的兩案算一案。R1 以 Bob 的 `[10:00, 11:00)` 為基準。兩個區間的基本關係有 13 種（James Allen 1983 年的區間代數），我們挑會讓判定翻轉或常被寫錯的幾種，再加上房間這一維：接在後面的 11:00-12:00（接受，TP-002）、跨過結束的 10:30-11:30（即 `@sc_book_03`，TP-003）、整個包住的 09:00-12:00（TP-004）、被包在裡面的 10:15-10:45（TP-010）、不同房間的 Tokyo 10:00-11:00（接受，TP-009）。接在前面、跨過開始、完全相同三種沒收：拿第四節的五個 bug 檢查，前兩種抓到的 bug 已分別由 TP-002、TP-004 抓到，完全相同一個也沒抓到（我們把這三案加進計畫跑過 `paper_mutants.py`）。TP-009、TP-010 是 review 後補的，經過也在第四節。R2 在 end 等於 start 時翻轉：`10:00-10:00` 要拒絕（TP-005），spec 自己的例子 `11:00-10:00` 放到 api 層驗 422（TP-007）。兩案都屬於 SDD Ch 11 測試矩陣 Unwanted-behaviour 那一列的「觸發不想要條件」；同一列的另一軸「剛好未達觸發閾值」是 `10:00-10:01` 這種剛好被接受的值，我們沒有單獨列案，接受的一側只靠 TP-001，第五節三次實跑的 agent 都補了這一案。

層級選看得到結果的最低一層：規則判定放 unit，狀態碼與 `detail` 的對應放 api（409、422 各一案），人看到的放 ui（一條旅程一案），也就是 Ham Vocke 說的「Push your tests as far down the test pyramid as you can」。優先度看錯了的後果：P1 是資料被弄壞或主要路徑不通（重複預約、正常時段訂不到），P2 是狀態碼或訊息錯、或較少見的形狀，P3 是外觀。不測清單有兩個來源：spec 的 Assumptions（R3、R4 是別的功能），以及看得見但這次不處理的風險（兩個請求同時搶一個時段）。最後一條紀律來自 Beck 的〈Canon TDD〉：測試清單是行為分析，「Mistake: mixing in implementation design decisions.」計畫寫「Alice books Taipei 10:30-11:30」，不寫 `Schedule.book(...)`，類別改名時計畫不必動。

## 二、testplan.md：一個案例六行

```markdown
# Test plan: 001-booking

Spec: `specs/001-booking/spec.md`. Turn P1 cases into tests first, then P2.
IDs are never renumbered or reused; a new case takes the next number.

## Cases

### TP-001 A free room is booked
- level: unit | priority: P1 | kind: happy
- trace: FR-001, @sc_book_01
- arrange: no bookings
- act: Alice books Taipei 10:00-11:00
- assert: confirmed as booking 1, Taipei 10:00-11:00 by Alice
### TP-002 A booking that starts when another ends is confirmed
- level: unit | priority: P1 | kind: boundary
- trace: R1, FR-002, @sc_book_02
- arrange: Bob holds Taipei 10:00-11:00
- act: Alice books Taipei 11:00-12:00
- assert: confirmed as booking 2
### TP-003 A booking over the end of another is rejected
- level: unit | priority: P1 | kind: sad
- trace: R1, FR-002, @sc_book_03
- arrange: Bob holds Taipei 10:00-11:00
- act: Alice books Taipei 10:30-11:30
- assert: rejected with "Taipei is taken 10:00-11:00 by Bob"; only Bob's booking is stored
### TP-004 A booking that contains another is rejected
- level: unit | priority: P1 | kind: sad
- trace: R1, FR-002
- arrange: Bob holds Taipei 10:00-11:00
- act: Alice books Taipei 09:00-12:00
- assert: rejected with "Taipei is taken 10:00-11:00 by Bob"
### TP-005 A booking that ends when it starts is rejected
- level: unit | priority: P2 | kind: boundary
- trace: R2, FR-003
- arrange: no bookings
- act: Alice books Taipei 10:00-10:00
- assert: rejected with "end must be after start"
### TP-006 The API reports an overlap as 409
- level: api | priority: P2 | kind: sad
- trace: R1, FR-004, @sc_book_03
- arrange: Bob holds Taipei 10:00-11:00 via POST /bookings
- act: Alice POSTs Taipei 10:30-11:30
- assert: 409, detail "Taipei is taken 10:00-11:00 by Bob"; GET /bookings has 1 item
### TP-007 The API reports end-before-start as 422
- level: api | priority: P2 | kind: sad
- trace: R2, FR-004, @sc_book_04
- arrange: no bookings
- act: Alice POSTs Taipei 11:00-10:00
- assert: 422, detail "end must be after start"
### TP-008 The page shows why a booking is rejected
- level: ui | priority: P2 | kind: sad
- trace: R1, FR-005, @sc_book_03
- arrange: Bob holds Taipei 10:00-11:00 via POST /bookings; the page is open
- act: Alice fills Taipei 10:30-11:30 and presses Book
- assert: the status line reads "Rejected: Taipei is taken 10:00-11:00 by Bob"
### TP-009 The same slot in another room is confirmed
- level: unit | priority: P1 | kind: happy
- trace: R1, FR-002
- arrange: Bob holds Taipei 10:00-11:00
- act: Alice books Tokyo 10:00-11:00
- assert: confirmed as booking 2, room Tokyo
### TP-010 A booking inside another is rejected
- level: unit | priority: P1 | kind: sad
- trace: R1, FR-002
- arrange: Bob holds Taipei 10:00-11:00
- act: Alice books Taipei 10:15-10:45
- assert: rejected with "Taipei is taken 10:00-11:00 by Bob"

## Out of scope

| ID | What | Why |
|---|---|---|
| OOS-1 | Office hours (R3) | a separate feature, per the spec's Assumptions |
| OOS-2 | Cancelling (R4) | a separate feature, planned in practice D |
| OOS-3 | Two requests for one slot at the same instant | in-memory, single-process store; revisit when storage changes |
| OOS-4 | Page layout and the success message | Ch 23's UI specs own the page journey |
```

上面是 review 後的定稿，存成 `specs/001-booking/testplan.md`。每案以 `### TP-NNN <一句話>` 開頭，三位數編號不重排、不重用：TP-009、TP-010 是 review 後補的，所以接在最後，`test_tp009_*` 這類測試名才不必跟著改。kind 分 happy（接受）、sad（拒絕）、boundary（翻轉點上的值，結果可能是任一種）；`trace` 寫這一案證明哪些 R、FR 與情境；`arrange / act / assert` 對應測試的三段，act 只能一個，assert 寫成可逐字比對的值；不測清單每列都要有 Why。為什麼不用 YAML？PyYAML 6.0.3 照 YAML 1.1 把 `10:00` 讀成六十進位整數，`python -c 'import yaml; print(yaml.safe_load("start: 10:00"))'` 印出 `{'start': 600}`，而會議室領域到處都是 `HH:MM`；PM 也能在 PR 裡直接讀 markdown，Spec Kit 的其他產物同樣是 markdown。表格也不合適：3A 三行塞進欄位長到沒法讀，改一個字整列都進 diff。

## 三、testplan_lint.py：把格式與追溯變成檢查

lint 放在 skill 裡（`.claude/skills/testplan/scripts/testplan_lint.py`），只用標準函式庫，系統的 Python 3.9 也能跑：Claude Code 的 Bash 繼承 Claude Code 的環境，使用者設定可以改寫 `PATH`（[Ch 21](./21-tdd-skill-aaa.md) 踩雷 4），跑到的不一定是你 activate 過的 venv。前八條規則只看計畫與 spec，後三條要加 `--tests` 才跑，用同一個直譯器呼叫 pytest，那個直譯器沒裝 pytest 就回報 `No module named pytest`：

```python
"""Lint a test plan (TPL-01..08); --tests traces each case to one collected test (TPL-09..11). Exit 1 on problems."""
import argparse, re, subprocess, sys
from collections import Counter
ALLOWED = {"level": ["unit", "api", "ui"], "priority": ["P1", "P2", "P3"], "kind": ["happy", "sad", "boundary"]}
FIELDS = [*ALLOWED, "trace", "arrange", "act", "assert"]
ID = re.compile(r"\bR\d+\b|\bFR-\d{3}\b|@sc_[a-z0-9_]+")
VAGUE = re.compile(r"\b(works?|correctly|properly|as expected|successfully|fine|handled)\b", re.I)

def parse(path):
    cases, oos, case, section = [], [], None, ""
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.rstrip()
        if line.startswith("## "):
            section, case = line[3:].lower(), None
        elif line.startswith("### "):
            m = re.fullmatch(r"### (TP-\d{3}) \S.*", line)
            case = {"id": m[1] if m else line.split()[1], "ok": bool(m), "at": f"{path}:{n}", "f": {}}
            cases.append(case)
        elif case and line.startswith("- "):
            for part in line[2:].split(" | "):  # "level: unit | priority: P1 | kind: sad"
                key, _, value = part.partition(":")
                case["f"].setdefault(key.strip(), []).append(value.strip())
        elif section == "out of scope" and re.match(r"\| *OOS-\d+", line):
            oos.append((f"{path}:{n}", [c.strip() for c in line.strip("|").split("|")]))
    return cases, oos

def lint(plan, sources, tests, want):
    cases, oos = parse(plan)
    known = {i: s for s in sources for i in ID.findall(open(s, encoding="utf-8").read())}
    problems, seen = [], Counter(c["id"] for c in cases)
    bad = lambda at, rule, msg: problems.append(f"{at}: {rule} {msg}")
    for c in cases:
        cid, f, at = c["id"], c["f"], c["at"]
        if not c["ok"] or seen[cid] > 1:
            bad(at, "TPL-01", f"'{cid}' must be a unique TP-NNN followed by a title; never reuse a number")
        for key in FIELDS:
            if len(f.get(key, [])) != 1:
                hint = "; one action per case, split it" if key == "act" and f.get(key) else ""
                bad(at, "TPL-02", f"{cid} has {len(f.get(key, []))} '{key}' entries, needs 1{hint}")
        for key, allowed in ALLOWED.items():
            if f.get(key, allowed)[0] not in allowed:
                bad(at, "TPL-03", f"{cid} {key} '{f[key][0]}' is not one of {'/'.join(allowed)}")
        c["trace"] = [t for v in f.get("trace", []) for t in re.split(r",\s*", v) if t]
        for t in c["trace"]:
            if t not in known:
                bad(at, "TPL-04", f"{cid} traces {t}, which no --source file mentions")
        if m := VAGUE.search(" ".join(f.get("assert", []))):
            bad(at, "TPL-07", f"{cid} assert is vague ('{m[0]}'); name the value to check")
    traced = {t for c in cases for t in c["trace"]}
    parked = {i for _, cols in oos for i in ID.findall(" ".join(cols))}
    for i, src in known.items():
        if i not in traced | parked:
            bad(f"{plan}:1", "TPL-05", f"{i} from {src} is in no case's trace and not in Out of scope")
    for r in sorted(i for i in traced if i.startswith("R")):
        if not any(r in c["trace"] and c["f"].get("kind") in (["sad"], ["boundary"]) for c in cases):
            bad(f"{plan}:1", "TPL-06", f"{r} has only happy cases; add the case that breaks the rule")
    if not oos:
        bad(f"{plan}:1", "TPL-08", "no rows under '## Out of scope'; say what you will not test and why")
    for at, cols in oos:
        if len(cols) < 3 or not cols[2]:
            bad(at, "TPL-08", f"{cols[0]} has an empty Why")
    if tests:
        trace(cases, tests, want, bad)
    return cases, oos, problems

def trace(cases, tests, want, bad):
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", tests], capture_output=True, text=True)
    if run.returncode not in (0, 5):  # 5 = no tests collected; else a broken test file, a bad path or no pytest
        files, why = re.findall(r"^ERROR (\S+)", run.stdout, re.M), re.findall(r"^E +(.+)", run.stdout, re.M)
        why = why or (run.stderr + run.stdout).strip().splitlines() or [f"exit {run.returncode}"]
        return bad(tests, "TPL-09", f"pytest cannot collect {', '.join(files) or tests}: {why[0]}")
    found = {}
    for nodeid, num in re.findall(r"^(\S+?::test_tp(\d{3})_\w+)", run.stdout, re.M):
        found.setdefault(f"TP-{num}", []).append(nodeid)
    for c in cases:
        c["tests"], level = found.get(c["id"], []), c["f"].get("level", ["?"])[0]
        if len(c["tests"]) > 1 or not c["tests"] and c["f"].get("priority", ["?"])[0] in want:
            bad(c["at"], "TPL-09", f"{c['id']} has {len(c['tests'])} tests named test_tp{c['id'][3:]}_*, needs 1")
        for nodeid in c["tests"]:
            if f"/{level}/" not in f"/{nodeid}":
                bad(nodeid, "TPL-11", f"{c['id']} has level {level}; move the test under {tests}/{level}/")
    for tp in sorted(set(found) - {c["id"] for c in cases}):
        bad(found[tp][0], "TPL-10", f"{tp} is not in the plan; add the case or rename the test")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan")
    ap.add_argument("--source", action="append", required=True, help="file that defines R*, FR-*, @sc_* (repeatable)")
    ap.add_argument("--tests", help="test dir: one test_tpNNN_* per case, under <dir>/<level>/")
    ap.add_argument("--priority", action="append", help="with --tests, require tests only for these priorities")
    a = ap.parse_args()
    cases, oos, problems = lint(a.plan, a.source, a.tests, a.priority or ALLOWED["priority"])
    levels = Counter(c["f"].get("level", ["?"])[0] for c in cases)
    print(f"{a.plan}: {', '.join(f'{v} {k}' for k, v in levels.items())}, {len(oos)} out of scope", *problems, sep="\n")
    tested = f", {sum(len(c.get('tests', [])) for c in cases)} tests" if a.tests else ""
    print(f"{len(cases)} cases{tested}, {len(problems)} problems")
    sys.exit(1 if problems else 0)
if __name__ == "__main__":
    main()
```

八條結構規則：TPL-01 編號唯一；TPL-02 七個欄位各一個（act 多於一個時提示拆案）；TPL-03 欄位值合法；TPL-04 trace 的 ID 要出現在 spec；TPL-05 spec 的每個 ID 要被追溯或列進不測清單；TPL-06 每條被追溯的規則至少有一個 sad 或 boundary 案；TPL-07 assert 不准出現 works、correctly 這類空話；TPL-08 不測清單存在且每列有 Why。先跑定稿，再跑改壞六處的版本：TP-003 塞進第二個 act、TP-004 的 assert 改成「the overlap is handled correctly」、TP-008 的 level 寫成 `e2e`、TP-007 的 trace 把 `@sc_book_04` 換成不存在的 `FR-006`、OOS-3 的 Why 清空、TP-009 的標題改成 TP-004（像有人刪案後重新編號）：

```
$ LINT=.claude/skills/testplan/scripts/testplan_lint.py
$ python $LINT specs/001-booking/testplan.md --source specs/001-booking/spec.md
specs/001-booking/testplan.md: 7 unit, 2 api, 1 ui, 4 out of scope
10 cases, 0 problems
$ python $LINT specs/001-booking/testplan-broken.md --source specs/001-booking/spec.md
specs/001-booking/testplan-broken.md: 7 unit, 2 api, 1 e2e, 4 out of scope
specs/001-booking/testplan-broken.md:20: TPL-02 TP-003 has 2 'act' entries, needs 1; one action per case, split it
specs/001-booking/testplan-broken.md:27: TPL-01 'TP-004' must be a unique TP-NNN followed by a title; never reuse a number
specs/001-booking/testplan-broken.md:27: TPL-07 TP-004 assert is vague ('handled'); name the value to check
specs/001-booking/testplan-broken.md:45: TPL-04 TP-007 traces FR-006, which no --source file mentions
specs/001-booking/testplan-broken.md:51: TPL-03 TP-008 level 'e2e' is not one of unit/api/ui
specs/001-booking/testplan-broken.md:57: TPL-01 'TP-004' must be a unique TP-NNN followed by a title; never reuse a number
specs/001-booking/testplan-broken.md:1: TPL-05 @sc_book_04 from specs/001-booking/spec.md is in no case's trace and not in Out of scope
specs/001-booking/testplan-broken.md:76: TPL-08 OOS-3 has an empty Why
10 cases, 8 problems
```

六處改動全被抓到，共八行：重複的編號在兩個標題上各報一次；TPL-04 與 TPL-05 是同一個改動的兩面，多了一個 spec 沒有的 ID，也少了一個 spec 有的情境。

## 四、lint 過了，計畫還可能是錯的

lint 檢查形狀，看不到內容。我們手做一份典型的「多多益善」計畫 `testplan-dup.md`：在定稿後面加兩案，TP-011 在 api 層再驗一次「被包在裡面」（10:15-10:45 回 409），TP-012 在 ui 層再驗一次「完全相同」（10:00-11:00 顯示 Rejected）。lint 的結論是 `12 cases, 0 problems`，12 案看起來比 10 案周全。reviewer 只問一個問題：**這一案抓得到哪個合理的 bug，而且別案都抓不到？** 答不出來就合併或刪掉。這個問題可以部分機械化：變異測試（mutation testing）在程式裡植入一個合理的小錯，得到一個 mutant，有測試因此失敗就叫殺掉它。寫程式前沒有程式可改，但可以拿規則本身來做：把 R1、R2 寫成函式，再寫幾個合理的寫錯版本，看哪一案的預期會和錯的版本不一致。我們寫了一支 41 行的 `paper_mutants.py`（只認得本章計畫的措辭；原始碼沒有印在本章，寫它是動手練習 2），放進五個 bug：只檢查新預約的開始是否落在舊預約裡（new-start-in-held）、只檢查舊預約的端點是否落在新預約裡（held-ends-in-new）、把區間當閉區間（closed-intervals）、忽略房間（room-ignored）、end 等於 start 也接受（end-equal-start-ok）。對 review 前的 v1（TP-001 到 TP-008）與 dup 各跑一次：

```
$ python paper_mutants.py specs/001-booking/testplan-v1.md specs/001-booking/testplan-dup.md
specs/001-booking/testplan-v1.md: 8 cases with a slot; disagreeing with the rule: none
  new-start-in-held    killed by TP-004
  held-ends-in-new     SURVIVES
  closed-intervals     killed by TP-002
  room-ignored         SURVIVES
  end-equal-start-ok   killed by TP-005
3 of 5 bugs killed; cases that kill no listed bug: TP-001, TP-003, TP-006, TP-007, TP-008
specs/001-booking/testplan-dup.md: 12 cases with a slot; disagreeing with the rule: none
  new-start-in-held    killed by TP-004
  held-ends-in-new     killed by TP-010, TP-011
  closed-intervals     killed by TP-002
  room-ignored         killed by TP-009
  end-equal-start-ok   killed by TP-005
5 of 5 bugs killed; cases that kill no listed bug: TP-001, TP-003, TP-006, TP-007, TP-008, TP-012
```

- **v1 讓兩個 bug 活下來。** 忽略房間是我們自己問出來的，補了 TP-009。held-ends-in-new 不在我們原本的清單上，是第五節第一次實跑時 agent 寫在它的 review 表裡，我們據此補了 TP-010。
- **dup 的 12 案與定稿的 10 案殺掉同樣五個 bug**（定稿的結論行是 `5 of 5 bugs killed; cases that kill no listed bug: TP-001, TP-003, TP-006, TP-007, TP-008`）。TP-011 和 TP-010 抓同一個 bug、只差層級，照「推到最低層」留 unit 那案；TP-012 沒殺掉任何列出的 bug。兩案都刪，回到定稿。
- **沒殺掉列出的 bug，不等於該刪。** 定稿的 TP-001、003、006、007、008 也在那份名單上：TP-001 是第一個紅燈，TP-003 是 spec 自己的例子並鎖住訊息格式，TP-006、007 驗狀態碼對應，TP-008 驗頁面。紙上 mutant 只模擬接受或拒絕，訊息、對應與頁面不在模型裡，要由 reviewer 說出理由。紙上預測準不準？我們另用一份參考實作（不在本章，Ch 25 會從紅燈做起）把五個 bug 寫成程式碼 mutant 跑單元測試，每個都只被紙上預測的那一個測試殺掉。

## 五、testplan skill：讓 agent 寫計畫

skill 放在 `.claude/skills/testplan/`，裡面有 `SKILL.md`、第三節的 `scripts/testplan_lint.py`，以及 `templates/testplan.md`：定稿的骨架，標頭兩行同上，一個 TP-001 案例的六行換成 `<the behaviour, as a sentence>`、`<the exact observable: status, message, stored state>` 這類尖括號說明，不測清單只留表頭與一列 `| OOS-1 | <a behaviour we will not test> | <the reason, and when to revisit> |`。`.claude/skills/testplan/SKILL.md`：

```markdown
---
name: testplan
description: Derive a test plan (specs/<feature>/testplan.md) from a spec's acceptance criteria before any code exists, lint it, and later turn each case into one red test. Use when a spec is ready and its tests are not, or when asked for a test plan, test cases, or what to test.
allowed-tools: Read Write Edit Bash(python .claude/skills/testplan/scripts/testplan_lint.py *)
---

# testplan

A test plan lists the behaviours we will prove, written before any code exists. Do not write production code.

## Write the plan

1. Read the spec. List every rule (`R*`), requirement (`FR-*`) and scenario tag (`@sc_*`). Each one must end up in some case's `trace` or in `## Out of scope`.
2. For each rule, find where it flips. Write the boundary on both sides, one case per distinct way to break the rule, and one accepted case per distinct shape. Two cases that no plausible bug can tell apart are one case.
3. Give each case the lowest level that can observe it: `unit` for domain decisions, `api` for status codes and error mapping, `ui` for what a person sees. One api case per error mapping, one ui case per journey.
4. Copy the shape of `templates/testplan.md`. One `act` per case. The `assert` names the exact value: status, message, stored state. Use only the names, rooms and messages the spec uses. Describe behaviour; do not name classes or functions.
5. Priority: P1 if a wrong answer corrupts data or blocks the main path, P2 for a wrong status or message or a rarer shape, P3 for cosmetics.
6. When a case needs a decision the spec does not make, write `(assumption: ...)` in its assert and list it in your report.
7. From the project root run `python .claude/skills/testplan/scripts/testplan_lint.py <plan> --source <spec>` exactly as written, as a command of its own: no `||`, no pipes, no `python3`. Fix every line it prints until it ends with `0 problems`. If the command is refused or cannot start, stop and report that; do not imitate the lint with other tools.
8. Stop. Report the plan path, the lint summary line, the assumptions, and your answers to the review questions. A reviewer reads the plan before any test is written.

## Review questions (the lint cannot answer these)

- For each case: which plausible bug fails this case and no other? If none, merge or drop it.
- For each rule: is there a case on each side of the boundary?
- Would a broken system still pass any assert? Make it exact.
- Is anything under Out of scope a risk the spec cares about?

## Turn cases into red tests (only when asked)

- One test per case, named `test_tpNNN_<slug>`, under `tests/<level>/`. Its arrange, act and assert blocks follow the plan's lines.
- Add only the names and signatures the tests import, so every test runs to its assert.
- Done when `pytest -q tests` fails every TP test on an assertion and `python .claude/skills/testplan/scripts/testplan_lint.py <plan> --source <spec> --tests tests` prints `0 problems`.
```

第 2、3 步把第一節的推導寫成指令；第 6 步要 agent 把 spec 沒決定的事標成假設，不准自己拍板；第 8 步要它停下，因為計畫是給人審的閘門，寫完順手寫測試就等於沒有閘門。`allowed-tools` 只放行這支 lint 的這一種寫法，不放行整個 `Bash(python *)`：官方文件提醒，repo 裡的 skill 能替自己授權，在 `-p` 模式、沒信任過的資料夾也生效。第 7 步寫的是 `python`，Claude Code 的 Bash 得找得到課程 venv 的 `python`：照這台機器使用者設定裡的 `PATH`，找不到任何 `python`，這時第 7 步會讓 agent 停下回報；下面三次實跑都用 `--setting-sources project`，沒有載入使用者設定。最後一節的「Add only the names and signatures the tests import」假設寫測試的人也能動原始碼；接上 [Ch 20](./20-tdd-with-agents.md) 的階段 guard 之後，RED 擋所有原始碼，stub 要在 RED 之前由人放好，[Ch 25](./25-tdd-in-sdd-pipeline.md) 的第一次實跑就指出了這個矛盾。

我們用 `claude -p` 實跑三次，每次在一個只有 skill、spec 與 `pytest.ini` 的新目錄裡（各自 `git init`，免得往上層找到本章目錄的同名 skill）。第一次用草稿版（還沒有第 4 步「只用 spec 的名字」那句與第 6 步），第二次的第 7 步只寫 `python ${CLAUDE_SKILL_DIR}/scripts/...`，第三次是本章定稿；前兩次 CLI 放行 `Bash(python *)`，第三次只放行 `ls`、`cat` 等唯讀指令。三次分別產出 19、24、24 案，用了 10、15、7 turns，費用 US$0.67、0.62、0.51，耗時 3 分 19 秒、2 分 50 秒、2 分 15 秒。第三次在 `agent-run3/` 裡執行 `claude -p "/testplan specs/001-booking/spec.md" --max-turns 15 --tools "Read,Write,Edit,Glob,Grep,Bash,Skill" --allowedTools "Read,Write,Edit,Glob,Grep,Bash(ls *),Bash(cat *),Bash(find *),Bash(head *),Bash(wc *)" --permission-prompts none --setting-sources project --strict-mcp-config --no-session-persistence --output-format stream-json --verbose < /dev/null`，agent 的 lint 呼叫與最後回報的假設表如下。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，模型 claude-opus-5-5）。

```
$ python .claude/skills/testplan/scripts/testplan_lint.py specs/001-booking/testplan.md --source specs/001-booking/spec.md
specs/001-booking/testplan.md: 18 unit, 3 api, 3 ui, 8 out of scope
24 cases, 0 problems
…（略）
| TP-013 | A room other than "Taipei" exists. The spec names no other room, so the test has to pick one. |
| TP-014 | Times are always HH:MM, so 09:00 keeps its leading zero. |
| TP-018 | R2 is checked before R1; the spec doesn't order them. |
| TP-019 | Success returns status 201. |
| TP-019 | The fields other than `id` are named `room`, `person`, `start` and `end`. |
| TP-022 | The status line reads exactly `Confirmed` on success. The spec gives no success text, so this is invented from its word "confirmed". |
```

- **agent 的計畫比我們的 10 案大得多**（19、24、24 案）。除了第一次的 TP-001（agent 和我們一樣，因為它是第一個紅燈而保留），每一案都說得出抓哪個 bug：時間被截到整點、只比對第一筆預約、自己的預約被豁免，都合理，held-ends-in-new 正是我們漏掉的那個。**假設清單就是 spec 的待決問題**：成功回不回 201、欄位叫什麼（它猜 `person`，本課的 API 用 `by`）、跨日怎麼算、重疊多筆時點名誰。這些該送回 spec（[Ch 18](./18-clarify-skill.md) 的 clarify），不該在測試裡定案。
- **第一次跑，agent 發明了 spec 沒有的會議室「Tainan」與人名「Dan」**，還在 assert 裡自行決定 R2 先於 R1。補上第 4 步那句與第 6 步後，第二、三次只用「Taipei」，同類決定都列成假設。
- **第二次 lint 沒跑成。** agent 下的是 `python … || python3 …`。Claude Code 對組合指令逐段比對權限，`python3` 那段不在 `Bash(python *)` 裡，整條被拒，拒絕訊息的最後一句是「This Bash command contains multiple operations. The following part requires approval: python3 .claude/skills/testplan/scripts/testplan_lint.py specs/001-booking/testplan.md --source specs/001-booking/spec.md」。之後 agent 改用 Grep 模仿 lint 的規則，最後回報「the lint has not run, so I can't report `0 problems`」。我們事後補跑，那份計畫確實是 `24 cases, 0 problems`，但這是運氣，不是證據。修法就是定稿的第 7 步：給一條照抄的指令、禁止 fallback、跑不了就停下回報，`allowed-tools` 放行的正好是那條指令。第三次的 CLI 沒放行任何 python，lint 照樣跑了，可見放行來自 skill 本身。

## 六、每個案例變成一個紅燈測試

計畫標頭寫著「Turn P1 cases into tests first, then P2」。這一節把 6 個 P1 案例（TP-001 到 004、009、010，全在 unit 層）變成測試，4 個 P2 案例留給動手練習；分批的理由在進階一節。紅燈要有意義，測試得跑到 assert 才失敗，不能在 import 就炸，所以先放一個只有名字與簽章的介面 stub，名字沿用 [Ch 19](./19-tdd-by-hand.md) 到 Ch 21 的 `Schedule`（讀回已存預約的 `bookings()` 是 Ch 21 加的查詢方法）。`pytest.ini` 三行：`[pytest]`、`pythonpath = .`、`testpaths = tests`；另建空的 `booking/__init__.py`。`booking/schedule.py`：

```python
"""Interface stub for 001-booking: the names the tests import, no behaviour yet."""
from dataclasses import dataclass
from datetime import time

class BookingError(Exception): pass
class Conflict(BookingError): pass
class InvalidRange(BookingError): pass
@dataclass(frozen=True)
class Booking:
    id: int
    room: str
    start: time
    end: time
    by: str
class Schedule:
    def book(self, room, start, end, by): return None
    def bookings(self): return []
```

`tests/unit/test_schedule.py`，名稱前綴 `test_tpNNN_` 是對應的鑰匙（有計畫時它取代 Ch 21 模板的 `test_<rule>_` 命名，規則 ID 已經寫在計畫的 trace 裡），每個測試的三段對應計畫的 arrange、act、assert。版面照 [Ch 21](./21-tdd-skill-aaa.md) 的 3A 模板：三段之間空一行，重複的 Arrange 抽成 `bob_holds_taipei()`；Ch 21 的 `aaa_lint.py` 對這個檔案印 `1 files, 6 tests, 0 problems`：

```python
from datetime import time

import pytest

from booking.schedule import Booking, Conflict, Schedule

TAKEN = "^Taipei is taken 10:00-11:00 by Bob$"

def bob_holds_taipei():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")
    return schedule

def test_tp001_free_room_is_booked():
    schedule = Schedule()

    booking = schedule.book("Taipei", time(10), time(11), "Alice")

    assert booking == Booking(1, "Taipei", time(10), time(11), "Alice")

def test_tp002_booking_that_starts_when_another_ends_is_confirmed():
    schedule = bob_holds_taipei()

    booking = schedule.book("Taipei", time(11), time(12), "Alice")

    assert booking == Booking(2, "Taipei", time(11), time(12), "Alice")

def test_tp003_booking_over_the_end_of_another_is_rejected():
    schedule = bob_holds_taipei()

    with pytest.raises(Conflict, match=TAKEN):
        schedule.book("Taipei", time(10, 30), time(11, 30), "Alice")

    assert [b.by for b in schedule.bookings()] == ["Bob"]

def test_tp004_booking_that_contains_another_is_rejected():
    schedule = bob_holds_taipei()

    with pytest.raises(Conflict, match=TAKEN):
        schedule.book("Taipei", time(9), time(12), "Alice")

def test_tp009_same_slot_in_another_room_is_confirmed():
    schedule = bob_holds_taipei()

    booking = schedule.book("Tokyo", time(10), time(11), "Alice")

    assert booking == Booking(2, "Tokyo", time(10), time(11), "Alice")

def test_tp010_booking_inside_another_is_rejected():
    schedule = bob_holds_taipei()

    with pytest.raises(Conflict, match=TAKEN):
        schedule.book("Taipei", time(10, 15), time(10, 45), "Alice")
```

跑測試，再讓 lint 對帳，先只要求 P1，再要求全部：

```
$ COLUMNS=120 pytest -q --tb=no -rf
FFFFFF                                                                                                           [100%]
=============================================== short test summary info ================================================
FAILED tests/unit/test_schedule.py::test_tp001_free_room_is_booked - AssertionError: assert None == Booking(id=1, roo...
FAILED tests/unit/test_schedule.py::test_tp002_booking_that_starts_when_another_ends_is_confirmed - AssertionError: a...
FAILED tests/unit/test_schedule.py::test_tp003_booking_over_the_end_of_another_is_rejected - Failed: DID NOT RAISE Co...
FAILED tests/unit/test_schedule.py::test_tp004_booking_that_contains_another_is_rejected - Failed: DID NOT RAISE Conf...
FAILED tests/unit/test_schedule.py::test_tp009_same_slot_in_another_room_is_confirmed - AssertionError: assert None =...
FAILED tests/unit/test_schedule.py::test_tp010_booking_inside_another_is_rejected - Failed: DID NOT RAISE Conflict
6 failed in 0.01s
$ python $LINT specs/001-booking/testplan.md --source specs/001-booking/spec.md --tests tests --priority P1
specs/001-booking/testplan.md: 7 unit, 2 api, 1 ui, 4 out of scope
10 cases, 6 tests, 0 problems
$ python $LINT specs/001-booking/testplan.md --source specs/001-booking/spec.md --tests tests
specs/001-booking/testplan.md: 7 unit, 2 api, 1 ui, 4 out of scope
specs/001-booking/testplan.md:32: TPL-09 TP-005 has 0 tests named test_tp005_*, needs 1
specs/001-booking/testplan.md:38: TPL-09 TP-006 has 0 tests named test_tp006_*, needs 1
specs/001-booking/testplan.md:44: TPL-09 TP-007 has 0 tests named test_tp007_*, needs 1
specs/001-booking/testplan.md:50: TPL-09 TP-008 has 0 tests named test_tp008_*, needs 1
10 cases, 6 tests, 4 problems
```

六個都紅，而且紅在 assert：`assert None == Booking(...)` 與 `DID NOT RAISE Conflict`；紅得對不對的正式判定是 [Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py`。拿掉 `--priority P1`，輸出就是剩下的待辦：計畫成了 agent 的 to-do list，而且這張清單會自己對帳。再故意弄壞兩處：把 `test_tp009_` 改名成 `test_tp011_`，並在 `tests/api/test_inside.py` 多放一個 `test_tp010_`。帶 `--priority P1` 重跑，四行問題裡有 `TPL-09 TP-009 has 0 tests named test_tp009_*, needs 1`、`TPL-09 TP-010 has 2 tests named test_tp010_*, needs 1`、`TPL-11 TP-010 has level unit; move the test under tests/unit/` 與 `TPL-10 TP-011 is not in the plan; add the case or rename the test`，三條 trace 規則都出聲了。

## 底層機制：三條綁定把計畫釘住

1. **逐行解析。** `parse()` 遇到 `### ` 開新案例，`- ` 開頭的行用 ` | ` 與第一個冒號切成欄位，`## Out of scope` 底下的表格列收成不測清單。它不用 markdown 函式庫，所以格式要嚴：欄位名拼錯，就會變成「0 個 assert」。
2. **追溯是集合運算。** 用正規表示式從 spec 抓出 ID 集合，減掉被 trace 的，再減掉不測清單提到的，剩下的就是 TPL-05。
3. **測試由 pytest 說了算。** `--tests` 不自己讀測試檔，而是問 `pytest --collect-only -q`，它列出的 node id 才是真的會跑的測試。名稱拼錯（`tset_tp001`）或 import 失敗都會在這裡現形，收集失敗時直接回報錯誤行，不給假的對應；`--tests` 路徑打錯或直譯器沒裝 pytest 時，回報的是 stderr 的第一行，例如 `ERROR: file or directory not found: nonexist`。
4. **三條綁定，各管一段。** lint 管計畫對 spec，trace 管計畫對測試，pytest 管測試對程式，哪一段斷了都看得見。但三條都只回答「有沒有接上」；「接得對不對」（這案真的抓得到 bug 嗎？assert 夠嚴嗎？）只有 reviewer 與真的執行能回答，第四節就是為此存在。

## 對比與取捨

| 做法 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| 不寫計畫，agent 直接寫測試 | 最快 | 寫程式前沒有審查點；測試是程式碼，PM 讀不懂 | 一次性腳本 |
| Spec Kit 的 `/speckit-checklist` | 檢查需求寫得好不好 | 1.2.5 的模板明文禁止產出「Test cases, test plans, QA procedures」 | spec 定稿前 |
| Spec Kit tasks 裡的測試任務 | 和工作排在一起 | 1.2.5 的 tasks 模板寫「Tests are OPTIONAL」，粒度是檔案，不列邊界 | 已有測試文化 |
| Gherkin feature 檔（Part 5） | PM 讀得懂、可執行 | unit 層的邊界值不該寫成 feature，一案一情境太重 | 驗收層 |
| 本章的 testplan.md | 每案可追溯、可 lint、一案一測 | 多一份產物要維護，重複與缺口仍要人審 | agent 參與的功能開發 |

## 踩雷集錦

1. **錯誤直覺：lint 過了就是好計畫。** 正確認識：lint 只保證形狀。第四節的 v1 零問題卻放過兩個 bug，dup 零問題卻多了兩個沒用的案例。要問「這案抓得到哪個別案抓不到的 bug」。
2. **錯誤直覺：案例越多越安全。** 正確認識：案例數不是覆蓋率。重複的案例拖慢測試、增加維護，還讓人以為缺口補上了。同一個分割留在最低層，上面的層級只驗對應與旅程。
3. **錯誤直覺：刪掉一案後重新編號比較整齊。** 正確認識：測試名、任務與 PR 討論都引用 `TP-009`，重排會讓追溯整片錯位。編號只增不減，刪掉的號碼空著；第一次實跑的 agent 就刪了 TP-012 並留下空號。
4. **錯誤直覺：紅燈就好，紅在哪不重要。** 正確認識：`ImportError` 也是紅，但它說的是「測試還不能跑」，不是「行為還沒做」。先寫只有簽章的 stub，讓測試跑到 assert 才失敗；trace 遇到收集失敗會直接回報，例如一個 import 還不存在的 `NotFound` 的測試，輸出是 `tests: TPL-09 pytest cannot collect tests/unit/test_cancel.py: ImportError: cannot import name 'NotFound' from 'booking.schedule' (/private/tmp/lab/aibdd/ch24-testplan/booking/schedule.py)`。
5. **錯誤直覺：agent 說 lint 過了，就是過了。** 正確認識：第二次實跑的 lint 被權限擋下，agent 改用 Grep 模仿檢查。這次它誠實說沒跑，下一個 agent 不一定。閘門的結果要來自工具輸出，不是 agent 的轉述。

## 進階：批次紅燈、hook 與另一位 reviewer

- **Beck 的警告與批次紅燈。** 〈Canon TDD〉把這列為錯誤：「Mistake: convert all the items on the Test List into concrete tests, then make them pass one at a time.」理由是第一個測試變綠時，可能推翻一個所有測試都依賴的決定而造成重工。agent 流水線（Ch 25）卻需要一個「測試都在、都紅」的審查點。折衷：共用的介面先在 plan 階段（[Ch 13](./13-plan-anatomy-review.md) 的 data-model 與 contracts）定下來，stub 只放這些決定，再按優先度分批轉換，每批都過 trace。人和 agent 結對、介面還沒定的時候，回到 Beck 的一次一案。
- **把 lint 搬進 hook，並換一位 reviewer。** 第二次實跑證明「請 agent 跑 lint」會失靈。更穩的做法是 PostToolUse hook：Write 或 Edit 了 `testplan.md` 就跑 lint，有問題就把輸出寫到 stderr 並以 exit 2 結束；官方文件說明，PostToolUse 的 exit 2 會讓 Claude 看到那段 stderr，工具已經執行過，所以這是回饋而不是阻擋（協定見 [harness Ch 30](../harness_engineering/30-hooks.md)）。本段未實測，為理論預期行為；驗證方式是照 Ch 3 把 JSON 餵給 hook 腳本，看 stdout、stderr 與 exit code。第四節的問題也可以交給沒寫過這份計畫的模型，呼應 pstack Ch 1 的信條「寫的人不能自己判」，本章同樣沒有實測這個設定。

## 動手練習

1. **補完 P2。** 為 TP-005 到 TP-008 各寫一個紅燈測試，api 層用 `TestClient` 與 app factory（Ch 22），ui 層用 Playwright（Ch 23），直到 `--tests tests` 印出 `10 cases, 10 tests, 0 problems`；再把一個 api 測試放進 `tests/unit/`，看 TPL-11 怎麼說。我們的版本跑到了這一步：10 個紅、0 problems，另外 starlette 1.10.0 的 `TestClient` 會多一個 `StarletteDeprecationWarning`。
2. **寫 `paper_mutants.py`。** 用正規表示式從每案的 arrange 與 act 抓出 `Room HH:MM-HH:MM`，assert 含 reject、409 或 422 就算預期拒絕；把正確規則與五個 bug 各寫成一個判定函式，列出每個 bug 被哪些案例殺掉。對 v1 與 dup 跑出和第四節一樣的結論。
3. **找第六個 bug。** 加一個「只比對房間裡第一筆預約」的 mutant。定稿的 10 案殺不掉它，因為每案都只有一筆舊預約。補一個 TP-011：arrange 放兩筆，讓新預約撞上第二筆。我們試過，這樣六個 bug 全死，lint 仍是 0 problems。

## 本章重點整理

- 測試計畫是寫程式前的考卷：每案有編號、層級、優先度、3A 三行與追溯，另附不測清單。它是最接近程式碼的計畫，每一行幾分鐘內就變成會紅的測試。
- 推案例靠找規則的翻轉點，兩側各一案；層級選看得到結果的最低一層；計畫寫行為，不寫類別名。`testplan_lint.py` 檢查形狀與追溯（TPL-01..08），加上 `--tests` 再透過 pytest 收集檢查一案一測與層級（TPL-09..11）。
- lint 看不到重複與缺口，reviewer 要問「這案抓得到哪個別案抓不到的 bug」，紙上 mutant 能把這個問題部分機械化；skill 則要確保 agent 跑得動它的閘門：一條照抄的指令、對應的 `allowed-tools`、跑不了就停。

## 自我檢核

- [ ] 不看本章，能從 R1 的半開區間推出至少四個案例，並說出每一案殺掉哪個 bug 嗎？
- [ ] v1 與 dup 都是 0 problems，為什麼一份放過兩個 bug、另一份多了兩個沒用的案例？有人說「計畫通過 lint 了」，你會追問什麼？
- [ ] Beck 為什麼反對一次把清單全變成測試？什麼條件下你會照做，什麼條件下改成分批？
- [ ] 第二次實跑的 lint 為什麼被拒？如果你的 skill 要 agent 跑 `npm test`，指令與 `allowed-tools` 會怎麼寫？

## 延伸閱讀

- **[Kent Beck — Canon TDD](https://newsletter.kentbeck.com/p/canon-tdd)**（2023-12-11）
  - **讀哪裡**：五個步驟，以及 Test List、Write a Test 兩段裡的「Mistake」。**學到什麼**：測試清單是行為分析而不是設計，與一次一案的理由。**和本章的關聯**：testplan.md 把這張清單寫成可審查的檔案，第六節的分批回應他的警告。
- **[Ham Vocke — The Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)**（martinfowler.com，2018-02-26）
  - **讀哪裡**：「The Test Pyramid」與「Avoid Test Duplication」。**學到什麼**：不同粒度各寫多少、何時把測試往下推、跨層重複怎麼判斷。**和本章的關聯**：level 欄位的選法，以及 dup 計畫裡 TP-011 的處置。
- **[GitHub Spec Kit v1.2.5 — templates/commands/checklist.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/checklist.md)**
  - **讀哪裡**：「Checklist Purpose: "Unit Tests for English"」與 ABSOLUTELY PROHIBITED 清單，再對照同版 [tasks-template.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/tasks-template.md) 開頭的「Tests are OPTIONAL」。**學到什麼**：Spec Kit 怎麼劃分需求品質檢查、工作項目與測試。**和本章的關聯**：說明 testplan 為什麼是 checklist 與 tasks 之外的第三種產物；先讀過 [Ch 12](./12-spec-kit-1x.md)。
- **[Claude Code 文件 — Extend Claude with skills](https://code.claude.com/docs/en/skills)**
  - **讀哪裡**：Frontmatter reference 的 `allowed-tools` 與「Pre-approve tools for a skill」。**學到什麼**：skill 自帶授權的範圍、生效時機與風險。**和本章的關聯**：第五節三次實跑的權限問題都出在這裡。
- **[Goran Petrović、Marko Ivanković — State of Mutation Testing at Google](https://research.google/pubs/state-of-mutation-testing-at-google/)**（ICSE SEIP 2018）
  - **讀哪裡**：摘要，以及把 mutant 結果放進 code review 的段落。**學到什麼**：mutation testing 在大規模下怎麼變成 review 的一部分。**和本章的關聯**：第四節的紙上 mutant 是同一個想法，只是提前到寫程式之前。

計畫與紅燈都有了，下一步把它們接進 SDD 流水線：tasks 之後加一個 TESTPLAN 階段，實作之前加一道確認「紅得對」的閘門。

→ [Ch 25 把 testplan 與 TDD 接進 SDD：TESTPLAN 階段與 RED 閘門](./25-tdd-in-sdd-pipeline.md)
