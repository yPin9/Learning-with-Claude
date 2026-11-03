# Ch 25 — 把 testplan 與 TDD 接進 SDD：TESTPLAN 階段與 RED 閘門

> **目標**：在 SDD 流水線的 tasks 與 implement 之間插入兩道由程式判斷的關卡：TESTPLAN（[Ch 24](./24-testplan-skill.md) 的測試計畫變成一案一測的紅燈）與 RED 閘門（`red_gate.py` 讀 pytest 的 JUnit XML 報告，逐個判斷紅的原因，只有斷言失敗與 `DID NOT RAISE` 算數），實作之後用同一支腳本對著計畫做 VERIFY；再用 constitution、tasks-template preset 與 Spec Kit workflow 的 shell 步驟把這個順序釘進 Spec Kit 1.2.5，最後和 test-last 的基準線各實跑一次，說得出這套流程多買到什麼、沒買到什麼。

> **環境**：Spec Kit 1.2.5（`uvx` 執行）、Claude Code 2.1.295（三次 `claude -p`，2026-11-02，模型 claude-opus-5-5）、Python 3.12.15、pytest 9.2.0、Playwright 1.64.1，macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch25-red-gate/`，從空目錄開始；所有輸出都是實際跑出來的。

## 為什麼需要這個？

翻一遍 SDD 課裡的流程，測試都排在後面（本課 [Ch 16](./16-tasks-binder.md) 的 TC-07 與[練習 C](./practice-c-custom-sdd-flow.md) 已經把測試排到實作前，但沒有程式確認它紅過、紅得對）。[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) 的 T3「寫 `test_wordcount.py`」依賴 T1、T2，由同一個 agent 寫，驗收時用正規表示式找 `test_ac1 PASSED`；[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 的 tasks.md 範例把測試放在「Phase 5: Tests」；[SDD Ch 30](../spec_driven_development/30-kiro.md) 的 Kiro 範例把整合測試排成 task 5；[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md) 的 IMPLEMENT prompt 叫 agent 實作完再跑 `verification_command`，VERIFY 再請另一個模型判斷每條 AC 有沒有被測到（它的雷 3：exit code 0 不等於 AC 被滿足）。[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md) 的雷 3 主張用紅綠節律檢查 AI 的實作，[SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md) 的「ATDD-SDD 混合」把人審過的 Gherkin 排在實作之前，SDD 課裡卻沒有一條能跑的流水線真的這樣接。

Spec Kit 1.2.5 本身倒不是 test-last。它的 [tasks-template](https://github.com/github/spec-kit/blob/v1.2.5/templates/tasks-template.md) 寫「Tests are OPTIONAL - only include them if explicitly requested in the feature specification」，要了測試就加一句「Write these tests FIRST, ensure they FAIL before implementation」；`speckit-implement` 的 SKILL.md（源自 [templates/commands/implement.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/implement.md) 的第 6 步）寫「Follow TDD approach: Execute test tasks before their corresponding implementation tasks」。這些都是 [Ch 12](./12-spec-kit-1x.md) 說的軟約束：寫給模型讀的句子。沒有一行程式確認測試真的紅過、紅的原因對、實作的人沒動過測試。我們自己的工具也有洞：[Ch 20](./20-tdd-with-agents.md) 的 `require_green.py` 在 RED 只看 pytest 的 exit code 是不是 1，寫在測試函式裡的 ImportError 也是 1；[Ch 24](./24-testplan-skill.md) 留下一份計畫、6 個 P1 紅燈（動手練習 1 補到 10 個）和一句「紅得對不對的正式判定是 Ch 25 的 `red_gate.py`」。

本章把這幾塊接起來：tasks 之後是 TESTPLAN 階段，implement 之前是 RED 閘門，implement 之後是對著計畫的 VERIFY，三者都由腳本判斷，再接進 Spec Kit。最後和 test-last 的基準線比一次，結果和我們預期的不一樣。

## 先建立直覺：警報器在響，是因為有煙還是沒電？

煙霧警報器會在兩種情況下出聲：偵測到煙，或電池快沒電。裝好之後按測試鈕，要確認的不只是「它會響」，還有「它為了對的原因響」。紅燈也一樣：pytest 回報失敗，可能代表「這個行為還沒做」（有煙），也可能代表「這個測試根本跑不起來」：import 失敗、fixture 壞了、語法錯誤，或者它被 skip、根本沒跑（沒電）。只有前一種紅燈，會在實作完成時為了對的理由變綠。

這個類比有一處不成立：警報器的兩種聲音聽得出來，pytest 的兩種紅燈卻常常長得一樣：寫在測試函式裡的 ImportError 印的也是 `FAILED`，fixture 壞掉的 `ERROR` 和斷言失敗同樣是 exit 1，差別藏在每個測試的失敗類型裡。所以閘門要讀報告，不能只看 exit code。流水線長這樣：

```
 specify → plan → tasks        tasks.md 替每個角色排好工作（第四節的 preset）
   Phase 2   testplan.md、介面 stub；人審計畫                         .tdd/ 尚未建立
   RED       test-writer：一案一測 test_tpNNN_*，只改 tests/           .tdd/phase = RED
   RED 閘門  pytest --junitxml=.tdd/red.xml → red_gate.py --plan …
             0 problems → 人讀證據行、commit、tag red-001-us1、寫 GREEN
   GREEN     implementer：只改原始碼；tdd_guard 擋測試，require_green 擋收工 .tdd/phase = GREEN
   VERIFY    red_gate.py --expect green（逐條對 spec ID）＋ tamper_check --base red-001-us1
```

三道關卡各回答一個問題。TESTPLAN：要證明哪些行為？（`testplan_lint.py` 管形狀，人管內容。）RED 閘門：證明的工具備妥了嗎？每案都有測試、都紅、紅在斷言上。VERIFY：每個證明都通過了，而且和紅燈時是同一批、沒被改過。

> 如果你對 `.tdd/phase` 與兩支 hook 還不熟，先回看 [Ch 20](./20-tdd-with-agents.md) 的「三、tdd_guard.py：PreToolUse 依階段擋檔案」與「四、require_green.py：Stop hook 不收「做完了」」；計畫的格式見 [Ch 24](./24-testplan-skill.md) 的「二、testplan.md：一個案例六行」。

## 一、實驗室：Ch 24 的計畫、Ch 20 的 hook、一個 Spec Kit 專案

在 `/tmp/lab/aibdd/ch25-red-gate/booking/` 裡 `git init`，用 [Ch 17](./17-constitution-and-presets.md) 的 `specify` 函式（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify "$@"`）跑 `specify init --here --force --non-interactive --integration claude --script sh`，再搬進三章的成果：Ch 24 的 `specs/001-booking/spec.md`、`testplan.md`、`booking/` 的介面 stub、`.claude/skills/testplan/` 與 10 個測試（正文的 6 個 P1，加上它動手練習 1 的 4 個 P2。Ch 24 沒有列出 P2 的答案，下面的輸出依賴我們那份的幾個形狀：`tests/unit/test_range.py` 的 `test_tp005_booking_that_ends_when_it_starts_is_rejected`；`tests/api/test_bookings_api.py` 的 `test_tp006_overlap_is_409_naming_the_holder` 與 `test_tp007_end_before_start_is_422_with_the_reason`，用 `TestClient(create_app())`，把 `(status_code, detail)` 當一個 tuple 比對；`tests/ui/test_booking_page.py` 的 `test_tp008_page_shows_why_an_overlapping_booking_is_rejected`，瀏覽器在 `tests/ui/conftest.py` 的 `page` fixture 裡啟動；`booking/api.py` 的 stub 只送出頁面，沒有預約路由）；Ch 20 的 `tdd_guard.py`、`require_green.py`、`settings.json`、`tdd/SKILL.md` 與 `tamper_check.py`；Ch 21 的 `rules/aaa.md`、`templates/test_unit.py` 與 `aaa_lint.py`。`pytest.ini` 照 Ch 24 的三行，再加一行 ``filterwarnings = ignore:Using `httpx` with `starlette.testclient`:UserWarning``（starlette 1.10.0 每次用 TestClient 都會警告）；`.gitignore` 與 CLAUDE.md 照 Ch 20 的寫法。`/speckit-tasks` 需要 `plan.md`，我們照 [Ch 13](./13-plan-anatomy-review.md) 的形狀手寫了最小的一份（Python 3.12 + FastAPI、`booking/` 與 `tests/{unit,api,ui}` 的結構、四條原則的 Constitution Check 表），外加 `data-model.md` 與 `contracts/api.md`，內容就是課程共用領域的 `Booking`、`Schedule` 與三個端點。

先用 Ch 21 的 lint 量一次這 10 個測試：`aaa_lint.py tests` 回 `4 files, 10 tests, 6 problems`。Ch 24 正文的 6 個 P1 已經照 3A 模板空行，問題全在我們的 P2：TP-005、006、007 的三段之間沒有空白行（AAA-1、AAA-3）。我們在這 3 個 unit 與 api 測試的 Arrange、Act、Assert 之間各插一個空白行，內容一字不改，`aaa_lint.py tests/unit tests/api` 變成 `3 files, 9 tests, 0 problems`。UI 測試仍有 2 個問題：填表的 `for` 迴圈（AAA-4），以及 AAA-5「no assertion」，後者是誤報：lint 只認 `assert` 敘述，不認 Playwright 的 `expect(...)`。UI 測試保持原樣，閘門的 lint 只跑 unit 與 api。最後把測試以外的檔案 commit 成一個 commit，測試留給第三節的紅燈 commit：`git add -A . ':!tests' && git commit -qm "T002-T005: test plan TP-001..010 and interface stubs (Ch 24)"`，再 `mkdir -p .tdd && echo RED > .tdd/phase`。

## 二、RED 閘門：讀 JUnit XML 的 red_gate.py

`pytest --junitxml=PATH` 會把每個測試的結果寫成 XML（JUnit XML，CI 工具共用的測試報告格式）。每個 `<testcase>` 沒有 `<failure>`、`<error>`、`<skipped>` 子元素代表通過，否則帶著其中之一與它的 `message` 屬性。閘門的判準全建立在這個屬性上：

| 報告裡看到的 | 例子 | 判定 |
|---|---|---|
| `failure`，message 以 `assert ` 或 `AssertionError` 開頭 | `assert None == Booking(...)`、Playwright 的 `Locator expected to have text` | 紅得對 |
| `failure`，message 以 `Failed: DID NOT RAISE` 開頭 | `pytest.raises(Conflict)` 沒等到例外 | 紅得對 |
| `failure`，其他例外 | ImportError、AttributeError、NotImplementedError、`pytest.fail(...)`、領域例外（不論從 Arrange 漏出或由 Act 丟出） | 停在斷言之前 |
| `error`，`failed on setup with "…"` | fixture 壞了、瀏覽器沒裝 | 不算 |
| `error`，`collection failure` | SyntaxError、模組層的 import 失敗 | 整場沒跑 |
| `skipped` | `@pytest.mark.skip`、xfail | 不算 |
| 三者皆無 | 在程式存在之前就通過 | 測不到新行為 |

存成 `.claude/skills/tdd/scripts/red_gate.py`，只用標準函式庫：

```python
"""red_gate.py JUNIT [--plan PLAN] [--priority P1 ...] [--expect red|green] [--allow PREFIX ...]
Judge a `pytest --junitxml` report. --expect red (default) is the gate before implementation: every
required plan case has a test, every plan test fails on an assertion or DID NOT RAISE, other tests pass.
--expect green is VERIFY: every plan test passes; prints which spec IDs (R1, FR-002, @sc_...) are met.
test_tp001_* belongs to TP-001. Without --plan every failing test is judged. Exit 0 ok, 1 problems, 2 bad input.
"""
import argparse, re, sys
import xml.etree.ElementTree as ET

RIGHT = ("assert ", "AssertionError", "Failed: DID NOT RAISE")  # pytest keeps "AssertionError: " only sometimes

def read_junit(path):
    tests = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        kid = next((k for k in tc if k.tag in ("failure", "error", "skipped")), None)
        msg = (kid.get("message") or "").strip() if kid is not None else ""
        if msg == "collection failure":  # the real error is the last "E   " line of the traceback
            msg += ": " + (re.findall(r"^E +(.+)$", kid.text or "", re.M) or ["?"])[-1]
        m = re.match(r"test_tp(\d{3})_", tc.get("name"))
        tests.append({"id": "::".join(filter(None, [tc.get("classname"), tc.get("name")])),
                      "tp": m and f"TP-{m[1]}", "msg": msg.splitlines()[0] if msg else "",
                      "state": {"failure": "failed"}.get(kid.tag, kid.tag) if kid is not None else "passed"})
    return tests

def read_plan(path):  # {"TP-001": {"priority": "P1", "trace": ["FR-001", "@sc_book_01"]}, ...}
    cases, case = {}, None
    for line in open(path, encoding="utf-8"):
        if m := re.match(r"### (TP-\d{3}) ", line):
            case = cases.setdefault(m[1], {"priority": "?", "trace": []})
        elif case and (m := re.search(r"priority: (P\d)", line)):
            case["priority"] = m[1]
        elif case and line.startswith("- trace:"):
            case["trace"] = re.split(r",\s*", line.split(":", 1)[1].strip())
    return cases

def wrong_reason(t, allow):
    if t["state"] == "failed" and t["msg"].startswith(RIGHT + tuple(allow)):
        return None
    return {"passed": "passes before the code exists, so it cannot show that the code works",
            "skipped": f"is skipped ({t['msg']}); a skipped test proves nothing",
            "error": f"errors outside the test body ({t['msg'][:80]}); fix the fixture"}.get(
        t["state"], f"stops before its assertion: {t['msg'][:80]}; fix the test or the stub until it reaches its assert")

def judge(tests, cases, required, expect, allow):
    problems, evidence = [], []
    for t in tests:
        if t["msg"].startswith("collection failure"):
            return [f"{t['id']}: RG-0 pytest stopped while collecting ({t['msg'][20:]}); no test in this report ran"], []
        planned = t["tp"] in cases if cases else (expect == "green" or t["state"] != "passed")
        label = t["tp"] or "test"
        if cases and t["tp"] and not planned:
            problems.append(f"{t['id']}: RG-4 {t['tp']} is not in the plan; add the case or rename the test")
        elif not planned and t["state"] != "passed":
            problems.append(f"{t['id']}: RG-3 a test outside the plan is {t['state']}: {t['msg'][:80]}")
        elif planned and expect == "red" and (why := wrong_reason(t, allow)):
            problems.append(f"{t['id']}: RG-2 {label} {why}")
        elif planned and expect == "green" and t["state"] != "passed":
            problems.append(f"{t['id']}: RG-5 {label} is {t['state']} after implementation: {t['msg'][:80]}")
        elif planned and expect == "red":
            evidence.append(f"{label} red: {t['msg'][:70]}")
        elif planned:
            evidence.append(t["id"])
    problems += [f"{c}: RG-1 no test named test_tp{c[3:]}_* in the report" for c in sorted(required - {t['tp'] for t in tests})]
    return problems, evidence

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("junit")
    ap.add_argument("--plan", help="the feature's testplan.md")
    ap.add_argument("--priority", action="append", default=[], help="require tests only for these priorities")
    ap.add_argument("--expect", choices=["red", "green"], default="red")
    ap.add_argument("--allow", action="append", default=[], help="another failure-message prefix that counts as red")
    a = ap.parse_args()
    try:
        tests, cases = read_junit(a.junit), read_plan(a.plan) if a.plan else {}
    except (OSError, ET.ParseError) as e:
        print(f"red_gate: {e}", file=sys.stderr)
        return 2
    required = {c for c, v in cases.items() if not a.priority or v["priority"] in a.priority}
    problems, evidence = judge(tests, cases, required, a.expect, a.allow)
    if a.expect == "red":
        lines = evidence
        if not evidence and not problems:
            problems.append("RG-1 nothing fails yet; write the failing test first")
    else:  # VERIFY speaks the spec's language: an ID is met when every case tracing it passed
        passed = {t["tp"] for t in tests if t["state"] == "passed"} - {t["tp"] for t in tests if t["state"] != "passed"}
        lines = []
        for sid in sorted({s for v in cases.values() for s in v["trace"]}):
            tps = [c for c, v in cases.items() if sid in v["trace"]]
            lines.append(f"{sid} {'met' if set(tps) <= passed else 'NOT met'}: {' '.join(tps)}")
    print("\n".join(lines + problems + [f"{len(tests)} tests, {len(evidence)} {a.expect} as planned, {len(problems)} problems"]))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

幾個設計決定（問題代碼 `RG-0` 到 `RG-5` 是閘門自己的編號，和 [Ch 11](./11-skill-engineering-skill.md) bdd skill 規則檔的 `RG-01..07` 同字首、彼此無關）：

- **測試與計畫靠名字對上**：`test_tp001_*` 屬於 TP-001，和 Ch 24 的 `testplan_lint.py --tests` 用同一條規則，參數化的多個項目都要紅。有計畫時，名字不帶 TP 編號的測試算「計畫外」，RED 期間它們必須仍是綠的（RG-3），test-writer 弄壞 `conftest.py` 這類事就在這裡現形。
- **每個紅燈印一行證據**：`TP-003 red: Failed: DID NOT RAISE Conflict` 是給人審的。Ch 20 的 review 一站要人「逐條看失敗原因」，現在那張清單由腳本產生。
- **三種結束碼分開**：0 放行、1 有問題、2 報告讀不到或不是 XML。讀不到報告時是 2 而不是 0，閘門才會「失敗時關上」（fail closed），踩雷集錦第 4 條會看到這救了我們一次。
- **`--allow`** 留給用 `raise NotImplementedError` 當 stub 的團隊。我們的預設不收：stub 若丟例外，每個呼叫它的測試都會為同一個原因紅，斷言寫得對不對完全沒被試過。實測把 stub 的 `return None` 換成 `raise NotImplementedError`，預設是 `10 tests, 3 red as planned, 7 problems`，加 `--allow NotImplementedError` 變成 `0 problems`。另一種要用到它的是 [Ch 19](./19-tdd-by-hand.md) C5 那種紅：已經存在的程式丟出不該丟的 `Conflict`，Ch 19 算它紅得對，閘門預設卻判 RG-2（`stops before its assertion: booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob`），加 `--allow booking.schedule.Conflict` 才收。第一批紅燈面對的是 stub，不會遇到；程式存在之後（下一個 story、既有專案）才會。程式存在之後還有反方向的一種：[Ch 20](./20-tdd-with-agents.md) 有意收下的邊界接受測試（R3 寫之前 08:00–09:00 就訂得到）帶 TP 編號時是 RG-2「passes before the code exists」，它防「擋太多」，證明不了新程式；這種釘子不編 TP 號，當計畫外的測試，閘門只要它綠（RG-3），抓不抓得到東西交給 review 與 mutant。

**紅得對**：在 Ch 24 的 10 個測試上跑 `python -m pytest -q --junitxml=.tdd/red.xml`（`10 failed in 2.51s`；`--tb` 不影響判定，原因見底層機制），再判一次：

```
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/001-booking/testplan.md
TP-006 red: AssertionError: assert (404, 'Not Found') == (409, 'Taipei...11:00 by 
TP-007 red: AssertionError: assert (404, 'Not Found') == (422, 'end mu... after st
TP-008 red: AssertionError: Locator expected to have text 'Rejected: Taipei is tak
TP-005 red: Failed: DID NOT RAISE InvalidRange
TP-001 red: AssertionError: assert None == Booking(id=1, room='Taipei', start=date
…（略：TP-002、003、004、009、010 同為 assert None == Booking(...) 或 DID NOT RAISE Conflict）
10 tests, 10 red as planned, 0 problems
```

**紅得不對**：在一份副本裡做五個常見的錯。TP-002 只呼叫不斷言（`# no Conflict means confirmed`）；TP-005 把 import 搬進函式、猜錯名字（`from booking.schedule import RangeError`）；TP-006 加 `@pytest.mark.skip(reason="API not wired yet")`（檔頭補 `import pytest`）；TP-007 的本體換成 `pytest.fail("TODO: map InvalidRange to 422")`；TP-008 不動，但用 `PLAYWRIGHT_BROWSERS_PATH=/nonexistent` 模擬一台沒裝瀏覽器的 CI：

```
$ PLAYWRIGHT_BROWSERS_PATH=/nonexistent python -m pytest -q --tb=no --junitxml=.tdd/red.xml | tail -1
7 failed, 1 passed, 1 skipped, 1 error in 0.27s
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/001-booking/testplan.md
…（略：TP-001、003、004、009、010 的五行證據）
tests.api.test_bookings_api::test_tp006_overlap_is_409_naming_the_holder: RG-2 TP-006 is skipped (API not wired yet); a skipped test proves nothing
tests.api.test_bookings_api::test_tp007_end_before_start_is_422_with_the_reason: RG-2 TP-007 stops before its assertion: Failed: TODO: map InvalidRange to 422; fix the test or the stub until it reaches its assert
tests.ui.test_booking_page::test_tp008_page_shows_why_an_overlapping_booking_is_rejected: RG-2 TP-008 errors outside the test body (failed on setup with "playwright._impl._errors.Error: BrowserType.launch: Execut); fix the fixture
tests.unit.test_range::test_tp005_booking_that_ends_when_it_starts_is_rejected: RG-2 TP-005 stops before its assertion: ImportError: cannot import name 'RangeError' from 'booking.schedule' (/private/t; fix the test or the stub until it reaches its assert
tests.unit.test_schedule::test_tp002_booking_that_starts_when_another_ends_is_confirmed: RG-2 TP-002 passes before the code exists, so it cannot show that the code works
10 tests, 5 red as planned, 5 problems
```

五個錯全被點名，每行都說了怎麼修。同一個目錄（`.tdd/phase` 寫 RED）用 Ch 20 的 `stop` 函式餵給原版的 Stop hook；hook 自己跑 pytest，所以 `PLAYWRIGHT_BROWSERS_PATH=/nonexistent` 要 export 給它（不給的話 TP-008 紅得對，摘要是 `8 failed, 1 passed, 1 skipped`）。它只看 exit code：`allow | require_green: RED ok (7 failed, 1 passed, 1 skipped, 1 error in 0.34s); review the failures before GREEN`。最後一種錯讓整場收集中斷：把 `test_range.py` 模組層的 import 改成 `RangeError`，pytest 印 `Interrupted: 1 error during collection`，閘門回一行以 `tests.unit.test_range: RG-0 pytest stopped while collecting (ImportError: cannot import name 'RangeError' from 'booking.schedule'` 開頭、以 `no test in this report ran` 結尾的問題（中間是檔案的完整路徑），與 `1 tests, 0 red as planned, 1 problems`。這一種 Ch 20 的 hook 也會擋（exit 2），但只有閘門說得出是哪一行。

## 底層機制：一個例外怎麼變成報告裡的一行

```
 測試丟出例外 ─▶ TestReport.longrepr.reprcrash.message = ExceptionInfo.exconly(tryshort=True)
                  └─ AssertionError 且 saferepr 以 AssertionError('assert 開頭 → 去掉 "AssertionError: "
 _pytest/junitxml.py：call 失敗 → <failure message=…>    setup 失敗 → <error message='failed on setup with "…"'>
                     收集失敗 → <error message="collection failure">，整場中斷（exit 2），報告只剩這一筆
                     skip / xfail → <skipped type="pytest.skip" | "pytest.xfail">
 red_gate.py 只讀 message 屬性的第一行（收集失敗時另取內文最後一個 E 行）
```

三個細節都在已安裝的 pytest 9.2.0 原始碼裡驗證過，也各自決定了一行程式。

**前綴時有時無**。`_pytest/_code/code.py` 的 `_assert_start_repr = "AssertionError('assert "`：只有例外的 repr 以這串開頭，前綴才會被去掉。Python 的 repr 在字串含單引號時改用雙引號，於是同一種失敗有兩種寫法：一個只有 `assert 1 == 2` 與 `assert "Bob" == "Alice"` 兩個測試的檔案，報告裡的 message 分別是 `assert 1 == 2` 與 `AssertionError: assert 'Bob' == 'Alice'`。所以 `RIGHT` 兩種開頭都收；只收一種的閘門，會把一部分正確的紅燈判成錯。

**為什麼讀 message 而不是 traceback 最後一行**。預設的 `--tb=auto` 下，`<failure>` 內文最後一行是 `tests/unit/test_schedule.py:31: Failed`（TP-003）這種「檔案:行號: 例外名」，看起來更好解析；改用 `--tb=short`、`line`、`no` 跑同一組測試，那一行就不見了，`message` 屬性四種寫法下都一樣。閘門不該依賴呼叫者選了哪種 traceback 格式。

**exit code 為什麼不夠**。pytest 的 exit 1 同時涵蓋測試失敗與 fixture 的 setup 錯誤（[Ch 19](./19-tdd-by-hand.md) 實測過），測試函式裡的 ImportError 也只是一個失敗的測試；exit 2 則代表收集中斷，連健康的檔案都沒跑。一個整數裝不下「哪個測試、為什麼」，所以 Ch 20 的 hook 只能分辨「有沒有紅」，分不出「紅得對不對」。

## 三、接上 Stop hook，交接給 implementer

閘門有兩個用處。第一個在 test-writer 的 session 裡：Ch 20 的 `require_green.py` 在 RED 階段改成也問一次閘門。改動只有 RED 分支與 pytest 指令：

```diff
-        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"],
+        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
+                              "--junitxml=.tdd/stop.xml"],
@@
     if phase == "RED":
-        if run.returncode == 1:
+        gate = subprocess.run([sys.executable, str(root / ".claude/skills/tdd/scripts/red_gate.py"), str(root / ".tdd/stop.xml")],
+                              capture_output=True, text=True)
+        if run.returncode == 1 and gate.returncode == 0:
             return emit(systemMessage=f"require_green: RED ok ({lines[-1]}); review the failures before GREEN")
+        if run.returncode == 1:
+            return emit(decision="block", reason=f"require_green: RED phase, but not every failure is red for the right reason.\n{gate.stdout}")
```

（另把 docstring 的「RED needs a failing test」改成「RED needs failing tests that red_gate.py accepts」。）同一份「五個錯」餵給新版：`block | require_green: RED phase, but not every failure is red for the right reason.`，reason 的最後一行是 `10 tests, 5 red as planned, 4 problems`。少的那一個是 TP-002：hook 不知道這次的計畫是哪一份，沒有 `--plan` 就分不出「新寫、卻已經綠的測試」和「本來就綠的舊測試」。所以第二個用處，也就是正式的閘門，由人帶著計畫跑，對應 tasks.md 的 T010（第四節）：

```
$ python -m pytest -q --junitxml=.tdd/red.xml | tail -1
10 failed in 2.50s
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/001-booking/testplan.md | tee specs/001-booking/red-gate.txt | tail -1
10 tests, 10 red as planned, 0 problems
$ python .claude/skills/testplan/scripts/testplan_lint.py specs/001-booking/testplan.md --source specs/001-booking/spec.md --tests tests | tail -1
10 cases, 10 tests, 0 problems
$ python .claude/skills/tdd/scripts/aaa_lint.py tests/unit tests/api
3 files, 9 tests, 0 problems
$ git add tests specs/001-booking && git commit -qm "test(001-booking): TP-001..010 red for the right reason (T007-T010)"
$ git tag red-001-us1 && echo GREEN > .tdd/phase && git log --oneline --decorate | head -2
6938a31 (HEAD -> main, tag: red-001-us1) test(001-booking): TP-001..010 red for the right reason (T007-T010)
a7d9bcb T002-T005: test plan TP-001..010 and interface stubs (Ch 24)
```

證據檔 `red-gate.txt` 跟著測試一起 commit，PR 的審查者讀得到每個紅燈的原因。tdd 的 SKILL.md 也跟著改：RED 那列的完成條件換成「`scripts/red_gate.py` on a `--junitxml` report prints `0 problems`」，GREEN 之後的檢查換成 VERIFY 的兩個指令（`red_gate.py --expect green` 與 `tamper_check.py --base <red tag>`）。實驗室那份另在 description 加了「and a red gate」，結尾多一行 `Test layout rules: rules/aaa.md, checked by scripts/aaa_lint.py.`（併入 Ch 21 模組時加的），第四節的 agent 讀到的就是這一版。`red_gate.py` 的報告放在 `.tdd/`，這也有個順帶的好處：Ch 20 的 guard 把 `.tdd/` 底下除了申訴檔以外的檔案都當成階段檔，agent 用 Edit 改 `.tdd/red.xml` 會收到 `tdd_guard: only a human changes .tdd/phase`。訊息講的是 phase，效果正是我們要的；但 Bash 寫檔不經過它，所以權限裡的 Bash 仍要窄。

## 四、接進 Spec Kit：constitution 第 III 條與 tasks-template preset

順序要出現在每個人都會讀的地方。以 Ch 17 的 constitution 為底（拿掉本章用不到的詞彙表與參數節），重寫第 III 條。依它的 Governance，重新定義原則是 MAJOR，版本升到 2.0.0：

```markdown
### III. Test-First (NON-NEGOTIABLE)
After /speckit-tasks and before any source change, the testplan skill writes
`specs/<feature>/testplan.md`; each case becomes one `test_tpNNN_*` test. Implementation starts
only when `.claude/skills/tdd/scripts/red_gate.py` reports 0 problems for those tests and a human
has tagged the red commit. Implementers never edit tests. VERIFY is `red_gate.py --expect green`
plus `tamper_check.py --base <red tag>`, not a model's opinion.
```

原則說「要什麼」，模板決定「長什麼樣」。preset `booking-tdd`（和[練習 B](./practice-b-modular-bdd-skill.md) 那個同名的 skill 是兩樣東西；第六節的 workflow 也用這個 id）的 `preset.yml` 和 Ch 17 的 `booking-bdd` 同形（`name: "Booking TDD"`、`version: "0.1.0"`），`provides.templates` 只有一項：`name: "tasks-template"`、`replaces: "tasks-template"`。模板是核心 `tasks-template.md` 的複本，`diff` 起來刪 27 行、加 29 行，重點是每個 story 的測試段落：

```markdown
### RED phase for User Story 1 (test-writer; `.tdd/phase` = RED) ⚠️
> **NOTE: not run by /speckit-implement. A human checks these boxes after T012 passes.**
- [ ] T010 [US1] Add the US1 cases to specs/[###-feature-name]/testplan.md with the testplan skill; …（略：testplan_lint 指令）
- [ ] T011 [P] [US1] One red test per US1 case, named test_tp[NNN]_[slug], under tests/[level]/; tests import only names from the T007 stubs
- [ ] T012 [US1] RED gate: `python -m pytest -q --junitxml=.tdd/red.xml`, then `python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/[###-feature-name]/testplan.md` prints 0 problems; a human commits the tests, tags red-[###]-us1 and writes GREEN to .tdd/phase
### Implementation for User Story 1 (implementer; `.tdd/phase` = GREEN; tests/ is read-only)
```

（節錄時拿掉了原檔的空白行。）其餘改動：檔頭的「Tests are OPTIONAL」換成「MANDATORY and test-first (constitution III)」；Phase 2 的「Create base models/entities」換成「Interface stubs ... names and signatures only」；US1 的最後一個任務是 VERIFY（US2、US3 的範例只把測試段落換成一行 RED 任務）；刪掉 Polish 裡的「Additional unit tests (if requested)」。安裝與解析：

```
$ specify preset add --dev ../presets/booking-tdd
Installing preset from /private/tmp/lab/aibdd/ch25-red-gate/presets/booking-tdd...
✓ Preset 'Booking TDD' v0.1.0 installed (priority 10)
$ specify preset resolve tasks-template
  tasks-template: /private/tmp/lab/aibdd/ch25-red-gate/booking/.specify/presets/booking-tdd/templates/tasks-template.md
    (top layer from: booking-tdd v0.1.0)
```

> 以下為實際執行紀錄（節錄），每次執行結果不同。2026-11-02，Claude Code 2.1.295，claude-opus-5-5。

模板不會自己變成任務，要模型照著寫，這是第一次實跑。在只有 spec、plan、constitution、preset 與 `.claude/`（skill、hook、settings）的 clone 裡（沒有計畫、測試與 stub，模擬「tasks 在 TESTPLAN 之前」）跑 `claude -p "/speckit-tasks"`，旗標同第五節的命令，但 `--allowedTools` 的 Bash 只放行 `setup-tasks.sh` 與 `check-prerequisites.sh`、`--max-turns 15`、沒有 `--include-hook-events`。結果：32 次工具呼叫（20 次 Read，包括兩支 hook、`red_gate.py`、`testplan_lint.py` 與 `tamper_check.py`）、2 次 Bash 被擋、一次 Write、US$0.93、176 秒，寫出 164 行、15 個任務的 tasks.md。RED 段落變成四個任務：T007–T009 按層級各寫一份紅燈測試，T010 是閘門；寫計畫的任務被它移到 Phase 2（T002）。它還自己加了一張角色表：

```
…（略：表頭與 T001–T006，那幾列的 `.tdd/phase` 是 missing，T006 由人建立 RED）
| T007 to T010 | test-writer, then human | RED |
| T011 to T014 | /speckit-implement | GREEN |
| T015 | refactorer | REFACTOR |
```

值得記下的四個決定。**它抓到一個跨章的矛盾**：Ch 24 的 testplan skill 叫 test-writer「Add only the names and signatures the tests import」，但 Ch 20 的 guard 在 RED 擋所有原始碼，所以它把 stub（T004、T005）排在 `.tdd/phase` 建立之前，計畫（T002）更早。**它讀過閘門才設計 stub**：「red_gate.py rejects any other exception (RG-2), so do not raise `NotImplementedError` and do not return `None`」，要 `book` 回傳一個 id 為 0 的 `Booking`；第七節會看到它防的正是什麼。**它替 spec 沒講清楚的地方做了決定**：`Schedule.bookings` 到底是屬性還是方法，data-model.md 沒說，它選了方法（照 Ch 21 的模板）。Ch 24 的定稿也是方法 `bookings()`，但本章三次實跑用的是 Ch 24 的前一版：stub 是 `self.bookings = []` 這個屬性，TP-003 寫 `schedule.bookings`，程式與測試和定稿只差這兩處（`testplan_lint.py` 收集失敗時的訊息也是舊寫法，三次實跑都沒執行 lint）；第一到第六節不屬於實跑紀錄的輸出，都在定稿的版本上重跑過。**它把 `aaa_lint.py tests` 加進閘門**（tdd 的 SKILL.md 有一行指向 rules/aaa.md 與 aaa_lint.py）。人審 tasks.md 時我們改了四處：T002、T004、T005 改寫成實跑當時 Ch 24 的樣子（屬性、回傳 None、沒有路由），T010 的 lint 範圍改成 `tests/unit tests/api`。tasks.md 是計畫，commit 過的 stub 與測試才是合約，兩者不一致時改計畫。

為什麼 TESTPLAN 排在 tasks 之後，而不是之前？tasks.md 是團隊追蹤的那份工作表，把計畫、stub、紅燈、閘門都排成任務，誰在哪個階段做什麼就寫在同一份檔案裡，第一次實跑的角色表就是證據。代價是生成 tasks 時還沒有 TP 編號，實作任務只能對到 story 與 FR，要到 VERIFY 才逐條對上 spec ID。反過來先寫計畫再生成 tasks，任務可以直接引用 TP 編號，但計畫就排不進 tasks.md 的順序裡了；兩種都行，我們選讓順序只寫在一個地方。

## 五、IMPLEMENT 與 VERIFY

T001–T010 打勾、紅燈 commit 並 tag 之後，第二次實跑讓 Spec Kit 自己的 implement skill 在 GREEN 階段工作：

```bash
cd /tmp/lab/aibdd/ch25-red-gate/booking && source /tmp/lab/aibdd/.venv/bin/activate
export SPECKIT_PYTHON_EXECUTABLE=/tmp/lab/aibdd/.venv/bin/python   # Ch 17：裝了 preset，bash 解析器要 PyYAML
claude -p "/speckit-implement T011-T014 (GREEN phase)" --setting-sources project,local --no-session-persistence \
  --strict-mcp-config --tools "Read,Write,Edit,Glob,Grep,Bash,Skill" \
  --allowedTools "Read,Write,Edit,Glob,Grep,Skill,Bash(.specify/scripts/bash/check-prerequisites.sh *),Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *),Bash(python -m pytest *),Bash(python .claude/skills/tdd/scripts/red_gate.py *),Bash(python .claude/skills/tdd/scripts/tamper_check.py *)" \
  --permission-prompts none --max-turns 20 --max-budget-usd 3 \
  --output-format stream-json --verbose --include-hook-events < /dev/null > ../runs/2-implement.jsonl 2> ../runs/2-implement.err
```

> 以下為實際執行紀錄（節錄），每次執行結果不同。2026-11-02，claude-opus-5-5，36 次工具呼叫，US$0.64，92 秒。用一支小腳本從 stream-json 抽出工具呼叫、pytest 摘要行、hook 回應與權限拒絕：

```
…（略：讀 SKILL.md、tasks、plan、spec、testplan、兩支 hook、兩支閘門腳本與全部測試，共 19 次 Read；另有三個 Bash，含 for 迴圈的那個被擋）
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=line -p no:cacheprovider
  pytest 10 failed in 2.85s
tool Edit booking/schedule.py
  hook PreToolUse:Edit exit=0 
…（略：再 Edit schedule.py、tests/unit 跑出 7 passed、Write booking/api.py，guard 都沒有輸出）
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short --junitxml=.tdd/green.xml
  pytest 10 passed in 0.80s
tool Bash /tmp/lab/aibdd/.venv/bin/python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan
  denied Bash: no approval surface in this session; permission request denied automatically
…（略：tamper_check 與一個 sed 也被拒；改用 Edit 在 tasks.md 打勾，再跑 git status 與 git diff --stat）
  hook Stop exit=0 {"systemMessage": "require_green: GREEN ok (10 passed in 0.81s)"}
```

它先跑一次全套看紅燈，再改原始碼，從沒試著碰測試，guard 六次都沒出聲；Stop hook 說 `GREEN ok`。VERIFY 沒跑成：tasks.md 開頭有一句「Bare `python` below means the same interpreter」，它呼叫兩支閘門時都用了 venv 的絕對路徑，不符合我們放行的 `Bash(python .claude/...)`。它的回報很誠實：「T014 is still open because neither VERIFY gate could run ... I left T014 unchecked and didn't try running the gates another way.」，另外用 `git diff --stat red-001-us1 -- tests pytest.ini`（空的）當參考，並說明那不能取代 `tamper_check.py`。這正是 VERIFY 該由人或 CI 跑的理由，所以我們自己跑。agent 寫的 `booking/schedule.py` 與 `booking/api.py` 沒有列出，而且用的是屬性 `schedule.bookings`，放到定稿的測試上，TP-003 會以 `TypeError: 'list' object is not callable` 失敗（`1 failed, 9 passed`）。自己重現時 GREEN 由你來做：`schedule.py` 用 Ch 19 結尾的版本加上 Ch 21 的 `bookings()`，`api.py` 以 Ch 22 的 app factory 為底，補上 422、`GET /bookings` 與 Ch 23 那種頁面 script，直到 10 個測試全綠。下面的輸出只取決於哪些測試通過：

```
$ python -m pytest -q --junitxml=.tdd/green.xml | tail -1
10 passed in 0.49s
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan specs/001-booking/testplan.md --expect green
…（略：@sc_book_01..04 與 FR-001，全部 met）
FR-002 met: TP-002 TP-003 TP-004 TP-009 TP-010
…（略：FR-003..005，全部 met）
R1 met: TP-002 TP-003 TP-004 TP-006 TP-008 TP-009 TP-010
R2 met: TP-005 TP-007
10 tests, 10 green as planned, 0 problems
$ python .claude/skills/tdd/scripts/tamper_check.py --base red-001-us1
7 files, 0 problems
```

這就是 SDD Ch 38 交給 LLM 評審的那份對照表，現在是集合運算：每個 spec ID 被哪些案例追溯（Ch 24 的 trace 行），那些案例的測試都過了，ID 才算 met。LLM 不再負責「找對應」，因為對應在寫程式之前就由人審過的計畫定好了；它的剩餘角色是 Ch 24 第四節那種語意問題：計畫本身漏了什麼。

**閘門看不到的一種紅**。在紅燈 commit 上把 TP-009 的斷言換成佔位用的 `assert False, "TODO: compare with Booking(2, 'Tokyo', ...)"`，跑 pytest 寫出 `red.xml`；再換上那份讓 10 個測試全綠的實作，重跑寫出 `green.xml`：

```
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/001-booking/testplan.md | grep -E "TP-009|problems"
TP-009 red: AssertionError: TODO: compare with Booking(2, 'Tokyo', ...)
10 tests, 10 red as planned, 0 problems
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan specs/001-booking/testplan.md --expect green | grep -E "^FR-002|^R1 |RG-|problems"
FR-002 NOT met: TP-002 TP-003 TP-004 TP-009 TP-010
R1 NOT met: TP-002 TP-003 TP-004 TP-006 TP-008 TP-009 TP-010
tests.unit.test_schedule::test_tp009_same_slot_in_another_room_is_confirmed: RG-5 TP-009 is failed after implementation: AssertionError: TODO: compare with Booking(2, 'Tokyo', ...)
10 tests, 9 green as planned, 1 problems
```

閘門判斷的是「紅的種類」，不是「這個紅以後能不能變綠」。在流水線裡，implementer 會被 Stop hook 擋住、只能寫申訴點名 TP-009；更早的一道防線是人讀證據行時看到那個 `TODO`。

## 六、用 Spec Kit workflow 把順序釘死

constitution 與模板都還是文字。[Ch 12](./12-spec-kit-1x.md) 的進階一節發現 `specify workflow run` 的 command 步驟只看 exit code、不看產物；workflow 另有 `shell` 步驟，非零結束就讓整個 run 停下，`gate` 步驟可以用 `verdict_input` 從輸入取得人的決定。把閘門寫成 shell 步驟，順序就由 Spec Kit 的引擎而不是模型保證。`workflows/booking-tdd.yml`：

```yaml
schema_version: "1.0"
workflow:
  id: "booking-tdd"
  name: "SDD with a test plan and a red gate"
  version: "0.1.0"
  author: "ai_bdd course"
  description: "tasks -> TESTPLAN -> RED gate -> implement -> VERIFY"
  integration: "claude"                 # default for the command and prompt steps
requires:
  speckit_version: ">=1.2.0"
inputs:
  feature: {type: string, required: true, enum: ["001-booking"]}   # it lands in shell lines: allowlist only
  plan_verdict: {type: string, default: "", enum: ["", approve, reject]}
  red_verdict: {type: string, default: "", enum: ["", approve, reject]}
steps:
  - {id: tasks, command: speckit.tasks}
  - {id: testplan, type: prompt, prompt: "/testplan specs/{{ inputs.feature }}/spec.md"}
  - id: plan-lint
    type: shell
    run: "/tmp/lab/aibdd/.venv/bin/python .claude/skills/testplan/scripts/testplan_lint.py specs/{{ inputs.feature }}/testplan.md --source specs/{{ inputs.feature }}/spec.md"
  - {id: review-plan, type: gate, message: "Review the test plan", options: [approve, reject], on_reject: abort, verdict_input: plan_verdict}
  - {id: red-tests, type: prompt, prompt: "RED phase, test-writer: one red test per case in specs/{{ inputs.feature }}/testplan.md. Edit tests/ only."}
  - id: red-gate
    type: shell
    run: "/tmp/lab/aibdd/.venv/bin/python -m pytest -q --junitxml=.tdd/red.xml > /dev/null; /tmp/lab/aibdd/.venv/bin/python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/{{ inputs.feature }}/testplan.md"
  - {id: review-red, type: gate, message: "Read every failure reason", options: [approve, reject], on_reject: abort, verdict_input: red_verdict}
  - id: handoff
    type: shell
    run: "git add tests && git commit -qm 'test({{ inputs.feature }}): red' && git tag red-{{ inputs.feature }} && echo GREEN > .tdd/phase"
  - {id: implement, command: speckit.implement}
  - id: verify
    type: shell
    run: "/tmp/lab/aibdd/.venv/bin/python -m pytest -q --junitxml=.tdd/green.xml > /dev/null; /tmp/lab/aibdd/.venv/bin/python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan specs/{{ inputs.feature }}/testplan.md --expect green && /tmp/lab/aibdd/.venv/bin/python .claude/skills/tdd/scripts/tamper_check.py --base red-{{ inputs.feature }}"
```

`workflow:` 底下的 `integration` 是 command 與 prompt 步驟的預設（沒寫時實跑得到 `Cannot dispatch command 'speckit.tasks': integration None CLI not found`）；prompt 步驟送出任意字串，我們自己的 `/testplan` 不叫 `speckit-*`，只能走它。`feature` 會被原樣插進 shell 指令，官方文件說唯一可靠的防護是在來源用 `enum` 限定，所以只收已知的值。

用 Ch 12 的假 claude（`SPECKIT_INTEGRATION_CLAUDE_EXECUTABLE` 指向一支只記錄參數的 shell 腳本）跑，模型一個字都不寫，shell 步驟是真的。在一份 clone 裡從紅燈 commit 的前一個 commit 開始（`git clone -q booking wf && cd wf && git checkout -q -b demo red-001-us1~1`；在原目錄切換的話，第五節沒 commit 的實作會留在工作目錄），放進 workflow 檔、建 `.tdd/phase` 為 RED，`tests/` 用第二節「五個錯」那份（瀏覽器照常可用）：

```
$ specify workflow run workflows/booking-tdd.yml -i feature=001-booking -i plan_verdict=approve < /dev/null
…（略：tasks、testplan、plan-lint、review-plan、red-tests 五步，三個派工步驟各由假 claude 印一行）
  ▸ [red-gate] shell …

Status: failed
Run ID: 41962b21
Error: Shell command exited with code 1.
```

`.specify/workflows/runs/41962b21/state.json` 記著閘門的完整輸出，最後一行是 `10 tests, 6 red as planned, 4 problems`（TP-008 在有瀏覽器時紅得對）。把測試換成正確的那份，從失敗的步驟續跑，並給出第二道閘門的決定：

```
$ specify workflow resume 41962b21 -i red_verdict=approve < /dev/null
…（略：red-gate、review-red、handoff 三步）
  ▸ [implement] speckit.implement …
fake-claude: got 2 args, prompt was: /speckit-implement
  ▸ [verify] shell …

Status: failed
Error: Shell command exited with code 1.
```

這次 red-gate 回 `10 tests, 10 red as planned, 0 problems`，閘門放行。假的 implementer 什麼都沒寫，引擎照樣把 implement 記成 completed（exit 0）；verify 的最後一行是 `10 tests, 0 green as planned, 10 problems`。handoff 已經 commit 並打上 `red-001-booking`，`.tdd/phase` 是 GREEN。把第五節那份全綠的實作放進來，再 `resume` 一次：`Status: completed`，verify 最後兩行是 `10 tests, 10 green as planned, 0 problems` 與 `7 files, 0 problems`。Ch 12 說 workflow 只保證順序與人工閘門，產物合不合格要靠檢查腳本；shell 步驟就是把那些腳本插進順序裡的位置。

## 七、和 test-last 比一次

第三次實跑是基準線。同一份 spec、plan、stub，但用 stock Spec Kit（不裝 preset、不放 hook 與 tdd、testplan 兩個 skill，CLAUDE.md 也拿掉 TDD 那段），constitution 拿掉第 III 條，tasks.md 照 SDD Ch 28 的樣子手寫：Phase 2 是 T002–T004 三個實作任務，Phase 3「Tests」才是 T005–T007 三個測試任務。指令是 `claude -p "/speckit-implement"`，其餘旗標同第五節（Bash 只放行 `check-prerequisites.sh` 與 pytest）。

> 以下為實際執行紀錄（節錄），每次執行結果不同。2026-11-02，claude-opus-5-5，33 次工具呼叫，US$0.55，136 秒。

```
…（略：讀 tasks、plan、spec、data-model、contract、stub 與 constitution，改 .gitignore；兩個 Bash 被擋）
tool Write tests/unit/test_schedule.py
…（略：再 Write tests/api/test_bookings.py、tests/ui/conftest.py、tests/ui/test_booking_page.py）
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=line 2>&1 | tail -50
  pytest 27 failed in 21.89s
tool Edit booking/schedule.py
…（略：再 Edit 一次 schedule.py、Write booking/api.py、跑全套）
  pytest 3 failed, 24 passed in 16.55s
tool Edit booking/api.py
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short 2>&1 | tail -15
  pytest 27 passed in 1.73s
```

它沒照 tasks.md 的順序做：「`tasks.md` lists the tests last, but the skill requires tests first, so I ran T005–T007 before T002–T004. All 27 tests failed against the stubs before I wrote any code.」它說的 skill 就是 implement 的 SKILL.md，裡面要求先測試的是「Follow TDD approach」與「Tests before code」兩句。所以這條基準線其實是「同一個 session 先寫測試、沒有閘門」。它也說了：「That first run stopped at the short failure summary, so I only inferred that the UI tests failed by timing out on the status line.」紅燈有了，原因沒人看。我們事後補看：把它的 27 個測試放回 stub 上，用沒有計畫的閘門判一次：

```
27 failed in 21.57s
…（略：23 行 test red: 證據，例如 assert 404 == 201、Failed: DID NOT RAISE Conflict）
tests.unit.test_schedule::test_fr_001_ids_follow_creation_order: RG-2 test stops before its assertion: AttributeError: 'NoneType' object has no attribute 'id'; fix the test or the stub until it reaches its assert
…（略：test_sc_book_02_r1_range_starting_at_the_end_is_free、test_r1_range_ending_at_the_start_is_free、test_r1_only_applies_within_one_room 同為 AttributeError）
27 tests, 23 red as planned, 4 problems
```

四個測試寫 `booking.id == 2`、`booking.room == "Tokyo"`，在回傳 None 的 stub 上連斷言都沒碰到。第四節那個 tasks agent 要 stub「do not return `None`」，防的正是這種測試。抓 bug 的能力用 Ch 24 第四節的五個 bug 量：把每一組測試連同它那次實跑的 `api.py` 放到我們的參考 `schedule.py` 上，用環境變數 `BUG` 依序植入。這份參考實作就是 Ch 24 說「不在本章」的那份，和只是迴圈的 `kill_matrix.sh` 一樣沒有列出（寫法見動手練習 3）。兩組都是 5/5：

| | 基準線（tasks 寫測試在後） | 本章的流水線 |
|---|---|---|
| 測試從哪來 | 同一個 session 自己想，27 個 | 人審過的計畫，10 個，一案一測 |
| 實際順序 | agent 依 skill 改成先寫測試 | 計畫與紅燈在另一個 session、實作之前 |
| 紅燈的原因 | 沒人看；事後判定 23 對、4 錯 | 10 個都由閘門記錄，證據檔進 commit |
| 實作期間能不能改測試 | 能，沒有任何東西擋 | guard 擋檔案工具，tamper_check 對 red tag：`7 files, 0 problems` |
| VERIFY | agent 說 `27 passed`；名字像 `test_sc_book_01_…` 才對得上 spec | 11 個 spec ID 逐條 met |
| 五個 mutant | 5/5（有的被 4 個測試同時抓到） | 5/5（每個剛好被 1 個測試抓到） |
| 成本 | 一次 run，US$0.55 | tasks 一次 US$0.93、implement 一次 US$0.64，外加人審計畫與紅燈 |

結論要說小一點。這個模型、這個小功能，test-last 的流程在 Spec Kit 1.2.5 裡自己變成了 test-first，寫出的測試和我們的一樣抓得到 bug。流水線多買到的不是「更強的測試」，而是三件可以事後查核的事：紅燈的原因被機器讀過、測試在實作期間沒被動過、驗收用的是 spec 的語言。這和 [Ch 21](./21-tdd-skill-aaa.md) 的發現同一個方向：skill 改變的是慣例與證據，不是抓 bug 的能力。限制：每臂一次；基準線的 agent 看得到目錄名稱與 CLAUDE.md 標題裡的 `baseline`；兩臂的提示詞不同（我們的 implement 有 `T011-T014 (GREEN phase)`）；換成較小的模型，「依 skill 改成先寫測試」不一定還會發生，請自己跑一次。

## 對比與取捨

| 做法 | 誰判斷紅得對不對 | 實作者能不能改測試 | 驗收怎麼對上需求 | 適用 |
|---|---|---|---|---|
| test-last（SDD 練習 F、SDD Ch 28 的範例） | 沒人，測試從沒紅過 | 能 | 命名慣例＋LLM 評審 | 一次性原型 |
| Spec Kit stock（要了測試） | 模型自己讀輸出 | 能 | 模型自評「coverage meets requirements」 | 小團隊、信任模型 |
| [社群 `tdd` extension](https://github.com/d0whc3r/spec-kit-tdd) | 模型照指令讀，證據寫進 `cycle-log.md`，事後由 verify 指令稽核 | 由指令約束 | test list ＋ mutation 稽核 | 想留在 Spec Kit 生態、一次一個行為 |
| 本章：閘門腳本＋hook＋workflow | `red_gate.py` 讀 JUnit XML | guard 擋檔案工具，tamper_check 事後對帳 | `--expect green` 逐條對 spec ID | agent 實作、需要可稽核的交接 |
| SDD Ch 38 的語意 VERIFY | 不處理 | 能 | LLM 判斷 NOT_COVERED | 沒有計畫、只有 AC 時 |

社群 extension 在 1.2.5 的 `catalog.community.json` 裡（id `tdd`、版本 1.1.2、`verified: false`），repo 在 2026-10-24 發了 v2.0.1。它的 `speckit.tdd.run` 指令寫「an assertion failure with expected and actual values is a valid red; a broken test file, a wrong import, or an unrelated failure is not」，判準和我們一樣，差別在執行者：它是 Spec Kit 的 extension hook（`before_implement` 設成 `optional: false`），由模型照 SKILL.md 去叫、去讀輸出；我們的閘門是腳本，由 hook 或 workflow 的 shell 步驟強制。它也一次只做一個行為、測試與程式同一個 session 寫，正是 Beck 的做法；本章是批次紅燈加角色分離（取捨見 Ch 24 的進階一節）。我們只讀了它的 manifest 與指令檔，沒有安裝。

## 踩雷集錦

1. **錯誤直覺：「pytest exit 1 就是紅了，可以開始實作」→ 正確認識**：exit 1 也包含 fixture 的 setup 錯誤，測試函式裡的 ImportError 也只是一個失敗的測試。第二節的「五個錯」在 Ch 20 的 hook 眼中是 `RED ok`，在閘門眼中是 5 個問題。要逐個測試讀失敗類型。
2. **錯誤直覺：「紅的原因對，測試就沒問題」→ 正確認識**：`assert False, "TODO …"` 是貨真價實的 AssertionError，閘門放行，要到 VERIFY 才發現它永遠不會綠。閘門管的是種類；能不能變綠、斷言夠不夠強，靠人讀證據行與 mutant（Ch 24 第四節）。
3. **錯誤直覺：「閘門指令寫在 tasks.md，agent 就會跑」→ 正確認識**：第二次實跑的 agent 把閘門指令寫成 venv 的絕對路徑（tasks.md 說 bare `python` 就是它），VERIFY 的三個指令有兩個被權限擋下。權限規則逐字比對指令開頭（Ch 24 也撞過 `||`）。閘門指令與 allow 規則要出自同一個來源；更根本的是，閘門的結果要來自人或 CI 跑的那一次，不是 agent 的轉述。
4. **錯誤直覺：「workflow 的 shell 步驟用的是我啟用的 venv」→ 正確認識**：`uvx` 執行 `specify` 時把自己的環境放在 `PATH` 最前面。把第六節 YAML 裡的絕對路徑換回 `python`，`plan-lint` 照樣過（純標準函式庫），`red-gate` 卻失敗：stderr 是 `/Users/robertliao/.cache/uv/archive-v0/W_7h3ayzsHhmnYcm/bin/python: No module named pytest` 與 `red_gate: [Errno 2] No such file or directory: '.tdd/red.xml'`，`Shell command exited with code 2`。幸好閘門讀不到報告時回 2 而不是 0，run 停了下來。shell 步驟與 hook 一樣，寫直譯器的絕對路徑。
5. **錯誤直覺：「tasks.md 把測試排在最後，agent 就會最後寫」→ 正確認識**：第七節基準線的 agent 說 implement skill 要求先寫測試，就自己改成先寫測試；反過來，tasks.md 寫「先寫測試」也不保證測試紅在對的地方。文字排的順序是建議，能擋住流程的只有 exit code。

## 進階：一次一個行為、Kiro 與多份計畫

- **批次閘門與一次一個行為**。本章一次讓 10 個測試紅，再交給 implementer；社群 extension 與 Beck 的 Canon TDD 是一次一個。若介面在 plan 階段還沒定案，改成每個 P1 案例各過一次閘門：`red_gate.py --plan … --priority P1` 只要求 P1 有測試，P2 的測試若已存在也照樣判斷；tag 依批次命名（`red-001-us1-p1`）。
- **Kiro 的 steering**。Kiro 沒有 constitution，對應的是 `.kiro/steering/*.md`，[官方文件](https://kiro.dev/docs/steering/)說 front matter 寫成 `inclusion: always` 就每次都載入。在 `.kiro/steering/testing.md` 寫「每個需求的任務先列測試案例、每案一個失敗的測試、確認每個失敗都是斷言，實作任務不得修改測試」，再搭配 Kiro 的 hook 去跑 `red_gate.py`。本段未實測（這台機器沒有 Kiro），Kiro 是否照 steering 重排 tasks.md，請在你的 Kiro 專案裡生成一次 tasks 驗證。
- **同一次 pytest 裡的多份計畫**。閘門靠 `test_tp001_` 對上 TP-001，兩份計畫的測試在同一次執行裡出現，編號就會撞：練習 D 的「取消預約」若也從 TP-001 編起，001-booking 已經綠的 `test_tp001_*` 會被當成「在程式存在前就通過」。只讓新計畫接著編號還不夠：有 `--plan` 時，名字帶 TP 編號卻不在這份計畫裡的測試一律是 RG-4，舊功能的每個測試各一行。所以閘門只餵新功能的測試檔，另跑一次全套確認舊測試仍綠；編號照樣接著編，測試名在全套與人讀的地方才不撞。練習 D 實跑了這兩件事。
- **CI 上的 VERIFY**。`.tdd/` 被 gitignore，CI 拿不到本機的報告：在 CI 裡 checkout red tag 跑 `--expect red`，再 checkout PR 跑 `--expect green` 與 `tamper_check --base <red tag>`，三者都要 0 problems（本段未實測）。

## 動手練習

1. **讓 hook 知道計畫**。Ch 20 的 guard 與 Stop hook 只讀 `.tdd/phase` 的第一個字。約定第二個字是計畫路徑（`RED specs/001-booking/testplan.md`），改 `require_green.py` 把它傳給 `--plan`，用第二節的「五個錯」確認 hook 也抓到 TP-002。
2. **補上第五節的盲點**。替 Ch 21 的 `aaa_lint.py` 加一條規則，抓 `assert False` 與 `assert <falsy 常數>`，接進 T010；用 TP-009 的佔位寫法確認它在 RED 就被擋。
3. **重做第七節的量測**。先照 Ch 24 第四節五個 bug 的定義，寫一份用環境變數 `BUG` 切換的參考 `schedule.py`（沒設 `BUG` 時是正確版，保留 `bookings()` 方法），再寫 `kill_matrix.sh`：把一組測試與全綠的 `api.py` 複製到暫存目錄、換上這份 `schedule.py`、對 `none` 與五個 `BUG` 各跑一次、印出摘要行。本章這組應該是 5/5，每個 bug 剛好一個測試抓到（我們在定稿的測試上也跑過）；你若也跑了基準線，拿它的測試再跑一次，再加 Ch 24 動手練習 3 的「只比對第一筆預約」，看哪一組先失手。

## 本章重點整理

- TESTPLAN 放在 tasks 之後：tasks.md 替 test-writer、人與 implementer 排好任務，計畫、stub 與紅燈在實作之前完成，由人審。
- RED 閘門讀 JUnit XML 的 `message`：斷言失敗與 `DID NOT RAISE` 才算紅得對；ImportError、fixture error、收集錯誤、skip、先綠都不算。pytest 只在 repr 以 `AssertionError('assert` 開頭時去掉前綴，所以兩種寫法都要收。
- 同一支腳本做 VERIFY：每個 spec ID 由追溯到它的案例決定 met 與否，LLM 不再負責找對應；`tamper_check` 確認測試和 red tag 時一樣。
- 接進 Spec Kit 有三層：constitution 第 III 條說要什麼，tasks-template preset 決定 tasks.md 的形狀，workflow 的 shell 步驟用 exit code 擋住順序。前兩層是給模型讀的文字，第三層才是硬閘門。
- 和 test-last 比：這次兩組測試抓 bug 的能力一樣，差在紅燈的原因有沒有被讀過、測試有沒有被凍結、驗收是不是用 spec 的語言。

## 自我檢核

- [ ] 不看本章，說出 JUnit XML 的三種子元素，以及哪三種 message 開頭閘門才算「紅得對」；再舉三個會議室領域裡「紅得不對」的例子，說出閘門各回哪一條 RG 規則。
- [ ] 同一個斷言失敗，為什麼 message 有時帶 `AssertionError:`、有時不帶？只收一種會發生什麼事？
- [ ] Stop hook 裡的閘門為什麼抓不到 TP-002，而人跑的閘門抓得到？要讓 hook 也抓到，需要多知道什麼？
- [ ] 第二次實跑的 VERIFY 為什麼沒跑成？這件事支持「VERIFY 由誰來跑」的哪個主張？
- [ ] Spec Kit 的 constitution、tasks-template preset、workflow shell 步驟，哪一層是硬閘門？各舉一個本章的實測證據。
- [ ] 第七節的基準線為什麼其實不是 test-last？這次比較能支持、不能支持哪些結論？

## 延伸閱讀

- **[pytest — Creating JUnitXML format files](https://docs.pytest.org/en/stable/how-to/output.html)**（官方文件，2026-11-02 讀取）
  - **讀哪裡**：「Creating JUnitXML format files」一節，以及 `record_property` 與 `junit_suite_name`。
  - **學到什麼**：報告怎麼產生、能附加什麼。文件沒寫 `message` 屬性怎麼來；那部分本章是讀已安裝的 `_pytest/junitxml.py` 與 `_pytest/_code/code.py` 才確定的，升級 pytest 時要重看。
  - **和本章的關聯**：`red_gate.py` 的全部輸入；`--junitxml` 與 `--junit-xml` 兩種拼法 9.2.0 都收。
- **[Spec Kit — Workflows reference](https://github.github.io/spec-kit/reference/workflows.html)**
  - **讀哪裡**：「Shell step」、「Gate Verdict Inputs」、「Interpolation and shell safety」、「Resume a Workflow」。
  - **學到什麼**：shell 步驟非零即失敗並保留輸出、`verdict_input` 的值怎麼決定閘門、`{{ }}` 是純字串代換，以及失敗的 run 從失敗的步驟續跑。
  - **和本章的關聯**：第六節的 YAML 每一個欄位都照這頁寫；先讀過 [Ch 12](./12-spec-kit-1x.md) 的進階一節。
- **[d0whc3r/spec-kit-tdd](https://github.com/d0whc3r/spec-kit-tdd)**（MIT，社群 extension）
  - **讀哪裡**：`extension.yml` 的 hooks 段（每個 hook 為什麼設成 optional 或不是，註解寫得很清楚），與 `commands/speckit.tdd.run.md` 的「Phase 3: Prove red, for the right reason」。
  - **學到什麼**：同一個問題在 Spec Kit 生態裡的另一種解法：一次一個行為、證據寫進檔案、事後用 git 歷史與 mutation 稽核。
  - **和本章的關聯**：對比表的第三列；它的 `before_implement` 不設 optional 的理由，正好說明 Spec Kit 的 extension hook 是寫給模型的指示。
- **《Growing Object-Oriented Software, Guided by Tests》** — Steve Freeman、Nat Pryce（Addison-Wesley，2010）；[目錄](https://growing-object-oriented-software.com/toc.html)
  - **讀哪幾章**：第 5 章〈Maintaining the Test-Driven Cycle〉的「Watch the Test Fail」一節，與第 23 章〈Test Diagnostics〉。
  - **學到什麼**：從目錄頁的節名看，前者要你寫程式前先看新測試失敗、讀它的失敗訊息，後者（Design to Fail、Explanatory Assertion Messages 等節）講怎麼讓失敗訊息自己說出哪裡錯；本章沒有引用書中原文。
  - **和本章的關聯**：「先看它失敗、讀失敗訊息」是這本書的紀律；本章把讀訊息這一步交給腳本，並把訊息當成交接的證據。書以 Java 寫成，方法和語言無關。

流水線接好了：計畫、紅燈、閘門、實作、驗收，每一步都有腳本把關。練習 D 用它從零做出 R4「取消預約」，從測試計畫一路到 unit、API 與 UI 三層的紅綠。

→ [練習 D：testplan → TDD → E2E 做出「取消預約」](./practice-d-cancel-booking.md)
