# Ch 21 — tdd skill 與 3A：把 Arrange、Act、Assert 寫成規則與 lint

> **目標**：說得出 Arrange-Act-Assert（3A）、Given/When/Then、四階段測試是同一個形狀的三種寫法；把一份「一口氣測完」的測試改寫成一個測試一個 Act，並用植入的 bug 證明改寫後抓得到原本漏掉的錯；寫出用 AST 檢查版面的 `aaa_lint.py`（AAA-1 到 AAA-5），和帶範例的 rule file、能跑的模板一起裝進模組化的 `tdd` skill，再接成 PostToolUse hook；最後從三次真實的 `claude -p` 執行判斷，這份 skill 改變了 agent 寫的測試的哪些地方、沒改變哪些。

> **環境**：macOS 26.6（arm64）、Python 3.12.15、pytest 9.2.0、Claude Code 2.1.295（預設模型 claude-opus-5-5）。實驗目錄 `/tmp/lab/aibdd/ch21-aaa/` 從空目錄開始，先 `source /tmp/lab/aibdd/.venv/bin/activate`。所有輸出都是 2026-11-02 實際跑出來的。

## 為什麼需要這個？

[Ch 19](./19-tdd-by-hand.md) 手打紅綠重構，[Ch 20](./20-tdd-with-agents.md) 用 hook 守住「先紅才准改程式」的順序。兩章管的是**順序**，沒有管**測試本身**。一個測試可以先紅後綠、流程完全合規，卻什麼也沒釘住：`if b2: assert b2.by == "Alice"` 這種寫法，在 `book()` 忘了 `return` 的那天，`b2` 是 `None`，斷言被跳過，測試照樣綠。第一節會真的植入這個 bug，看它溜過去。

這種測試在 AI 之前就有。Bill Wake 說有些測試「像不斷句的長句子（run-on sentence）」；Gerard Meszaros 在《xUnit Test Patterns》（2007）把其中一類叫做條件式測試邏輯（Conditional Test Logic）：測試裡有可能執行、也可能不執行的程式碼。agent 讓問題更大：它寫測試的速度遠超過人審測試的速度，而「我加了測試，全部通過」這句話，不管有沒有真的測到，聽起來都一樣。人審不過來的時候，最便宜的保險是**形狀**：一個測試只做一件事，而且一眼看得出是哪件事。這個 repo 到現在沒有一章講它：[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的「BDD 在 AI 開發時代的角色」把 Given/When/Then 對應到 setup、action、assertion，沒有談測試碼的版面；[Ch 4](./04-prompt-files-to-skills.md) 的示範 skill `pytest-conventions` 用一行帶過「三段之間空一行，一個測試一個 Act」。本章把那一行長成一個模組：帶範例的規則、能跑的模板、一支 lint、一個 hook。

## 先建立直覺：一個測試是一次實驗

想像理化課的實驗紀錄：先**準備**器材和樣品，然後**只改一個變因**，最後**量測**並和預期比對。同時加熱又加催化劑，結果變了，你說不出是哪一個造成的。

```
  準備（可以很多步）           只動一個變因               量測、比對
  [空的 Schedule，           [Alice 訂 Taipei       [拿到的是不是 Alice
   Bob 訂 10:00-11:00]  -->   11:00-12:00]     -->    的那一筆預約？]
       Arrange / Given            Act / When             Assert / Then
```

Act 是唯一的變因，Assert 只量測、不再動手，Arrange 是讓這個變因有意義的前提。開頭那個 `if b2:` 的測試，等於一份實驗紀錄寫著「如果有結果的話，結果是對的」。這個比喻有一處不成立：真正的實驗常刻意同時改好幾個因子；單元測試裡對應的做法是 `@pytest.mark.parametrize`，每組參數仍是一個獨立的測試項目，各自一個 Act。同一個形狀，四個出處各取了名字：

| 出處 | 準備 | 動作 | 驗證 | 收拾 |
|---|---|---|---|---|
| Meszaros，Four-Phase Test（書 2007） | fixture setup | exercise SUT | result verification | fixture teardown |
| Wake，3A（2001 命名，本章引的文章 2011） | Arrange | Act | Assert | 刻意不加第四個 A |
| North 與 Matts，BDD；Gherkin | Given | When | Then | 無 |
| pytest 文件〈Anatomy of a test〉 | Arrange | Act | Assert | Cleanup |

Wake 在 xp123.com 的文章（2011-04-26）寫明「I (Bill Wake) observed and named the pattern in 2001」，並指出 Kent Beck 在《Test-Driven Development: By Example》第 97 頁提過它。他不加第四個 A，因為多數單元測試不碰外部系統、用不到 teardown；pytest 把 Cleanup 列為第四步，實務上它住在 fixture 的 `yield` 之後，不在測試本體。Martin Fowler 的〈GivenWhenThen〉（2013-08-21）把三者串起來：Given/When/Then 是四階段測試的另一種寫法，「Bill Wake came up with the formulation as Arrange, Act, Assert」。差別在層級：Given/When/Then 是 PM 讀得懂的業務例子，AAA 是程式碼的版面；SDD Ch 10 踩雷集錦第一條「Given 是狀態、When 是操作」和「一個測試一個 Act」是同一條紀律。落到會議室領域，一個情境和一個測試逐行對應：

| Gherkin（寫法規則歸 [Ch 28](./28-gherkin-dsl-style.md)） | 段落 | 測試裡的那一行 |
|---|---|---|
| `Given Bob has booked "Taipei" from 10:00 to 11:00` | Arrange | `schedule.book("Taipei", time(10), time(11), by="Bob")` |
| `When Alice books "Taipei" from 11:00 to 12:00` | Act | `booking = schedule.book("Taipei", time(11), time(12), by="Alice")` |
| `Then the booking is confirmed` | Assert | `assert booking == Booking(2, "Taipei", time(11), time(12), "Alice")` |

## 一、同一組行為，兩份測試

從 [Ch 19](./19-tdd-by-hand.md) 結尾的 `booking/schedule.py` 開始（R1 與 R2；46 行全文在該章「C7 與重構」一節），在 `Schedule` 類別最後加一個兩行的查詢方法 `def bookings(self): return list(self._bookings)`；`booking/__init__.py` 是空檔，`pytest.ini` 三行：`[pytest]`、`pythonpath = .`、`testpaths = tests`。

**改寫前**：`before/test_book_before.py`。這份是我們照「一口氣測完」的常見寫法手寫的，不是 agent 產出（agent 實際寫出什麼，第六節看）：

```python
from datetime import time

import pytest

from booking.schedule import Conflict, InvalidRange, Schedule


def test_booking_flow():
    s = Schedule()
    s.book("Taipei", time(10), time(11), by="Bob")
    assert len(s.bookings()) == 1
    b2 = s.book("Taipei", time(11), time(12), by="Alice")
    if b2:
        assert b2.by == "Alice"
    try:
        s.book("Taipei", time(10, 30), time(11, 30), by="Carol")
        assert False, "expected a conflict"
    except Conflict as e:
        assert "Taipei is taken" in str(e)
    for b in s.bookings():
        assert b.by != "Carol"


def test_invalid_ranges():
    s = Schedule()
    with pytest.raises(InvalidRange):
        s.book("Taipei", time(11), time(10), by="Bob")
        s.book("Taipei", time(10), time(10), by="Bob")
```

**改寫後**：每個行為一個測試，三段之間空一行。這份檔案同時是 skill 的模板 `.claude/skills/tdd/templates/test_unit.py`，複製一份成 `tests/test_book.py`：

```python
"""Template for tests in this project: copy the shape, not the data.

Shapes: a returned value, an expected error (pytest.raises), a state change.
Each test: Arrange, blank line, one Act, blank line, Assert. Name: test_<rule>_<behaviour>.
"""
from datetime import time

import pytest

from booking.schedule import Booking, Conflict, InvalidRange, Schedule


def test_r1_adjacent_slots_do_not_overlap():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), by="Bob")

    booking = schedule.book("Taipei", time(11), time(12), by="Alice")

    assert booking == Booking(2, "Taipei", time(11), time(12), "Alice")


def test_r1_overlapping_booking_is_rejected():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), by="Bob")

    with pytest.raises(Conflict, match="Taipei is taken 10:00-11:00 by Bob"):
        schedule.book("Taipei", time(10, 30), time(11, 30), by="Carol")


def test_r1_rejected_booking_is_not_stored():
    schedule = Schedule()
    bob = schedule.book("Taipei", time(10), time(11), by="Bob")

    with pytest.raises(Conflict):
        schedule.book("Taipei", time(10, 30), time(11, 30), by="Carol")

    assert schedule.bookings() == [bob]


def test_r2_end_before_start_is_rejected():
    schedule = Schedule()

    with pytest.raises(InvalidRange, match="end must be after start"):
        schedule.book("Taipei", time(11), time(10), by="Bob")


def test_r2_zero_length_booking_is_rejected():
    schedule = Schedule()

    with pytest.raises(InvalidRange, match="end must be after start"):
        schedule.book("Taipei", time(10), time(10), by="Bob")
```

在正確的程式上兩份都綠：`python -m pytest -q before` 是 `2 passed in 0.00s`，`python -m pytest -q tests` 是 `5 passed in 0.00s`。綠燈分不出好壞，植入 bug 才分得出來。`plant_bugs.py PROJECT SUITE...` 是 [Ch 32](./32-brownfield-bdd.md) `mutate.py` 的迷你版；我們的版本 43 行，篇幅所限不列出，請照這段規格自己寫：每個 bug 把專案複製到暫存目錄（略過隱藏檔），對 `booking/schedule.py` 做一次 `str.replace`（找不到要換的字串就報錯停下，否則等於沒植入），在副本裡對每組測試跑 `python -m pytest -q -p no:cacheprovider <suite>`，印出最後一行並去掉耗時；`none` 列是不植入 bug 的對照，欄寬是我們的排版。五個 bug 的替換：forgot-return 刪掉 `return booking`；zero-length-ok 把 `if end <= start:` 換成 `if end < start:`；adjacent-overlap 把 `self.start < end and start < self.end` 的兩個 `<` 換成 `<=`；blames-newcomer 把訊息裡的 `by {existing.by}` 換成 `by {by}`；returns-first 把 `return booking` 換成 `return self._bookings[0]`。

```bash
python plant_bugs.py . before tests
```

```
bug              before                  tests
none             2 passed                5 passed
forgot-return    2 passed                2 failed, 3 passed
zero-length-ok   2 passed                1 failed, 4 passed
adjacent-overlap 1 failed, 1 passed      1 failed, 4 passed
blames-newcomer  2 passed                1 failed, 4 passed
returns-first    1 failed, 1 passed      1 failed, 4 passed
```

五個 bug，改寫前抓到 2 個，改寫後 5 個。三個漏網之魚各對應一種形狀問題：**forgot-return** 是 `if b2:` 在 `b2 is None` 時跳過斷言；**zero-length-ok**（`end < start`，放行長度 0 的預約）是 `pytest.raises` 裡放了兩句，第一句就丟出例外，第二句 10:00 到 10:00 從來沒執行；**blames-newcomer**（訊息寫成新來的人）是 `"Taipei is taken" in str(e)` 只比對片段，這一條歸第二節的 AAA-7。抓到的那兩個也不一樣好用。把 adjacent-overlap 放進一份副本跑 `python -m pytest -rf before tests`，失敗清單是 `FAILED before/test_book_before.py::test_booking_flow` 和 `FAILED tests/test_book.py::test_r1_adjacent_slots_do_not_overlap`，例外訊息都是 `Taipei is taken 10:00-11:00 by Bob`。左邊只說「flow 壞了」，而且第 12 行之後的檢查全沒跑到，其他行為好不好不知道；右邊的名稱就是診斷，另外 4 個綠燈告訴你別的沒壞。Meszaros 把這叫缺陷定位（Defect Localization）。

## 二、規則寫成 rule file：`rules/aaa.md`

欄位取自 [Ch 9](./09-rule-files.md)：編號、觸發時機、一句話規則、反直覺時才給理由、壞例與好例、「你跳過它的徵兆」、由誰執行。這裡壓縮過：觸發時機與由誰執行在檔頭各寫一次，例子與徵兆只放在容易做錯的規則上。給 agent 讀的檔用英文，例子取自上一節：

```markdown
# AAA rules for tests

Trigger: you write or change a test function under tests/.
Layout: Arrange, blank line, Act, blank line, Assert. ../templates/test_unit.py shows three shapes.
AAA-1 to AAA-5 are checked by ../scripts/aaa_lint.py. AAA-6 and AAA-7 need a reviewer.

## AAA-1 One Act per test
The block above the assertions is one statement: the call the test name talks about.
Inside `pytest.raises` put only that call. Once it raises, the lines after it never run.
Bad:
    with pytest.raises(InvalidRange):
        schedule.book("Taipei", time(11), time(10), by="Bob")
        schedule.book("Taipei", time(10), time(10), by="Bob")
Good: two tests, test_r2_end_before_start_is_rejected and test_r2_zero_length_booking_is_rejected.
Sign you skipped it: the test name needs "and", "flow" or "works".

## AAA-2 Only asserts after the first assertion
Bad: book, assert, book again, assert again. Good: a new test where the second Act starts.
Sign you skipped it: a failure names the test but not the behaviour that broke.

## AAA-3 Blank lines mark the sections
One blank line above the Act and one above the first assertion. No section comments needed.

## AAA-4 No branches or loops in a test
`if b2:` skips its assert when b2 is None. `for b in schedule.bookings():` asserts nothing on an
empty list, and so does `all(...)`. Use @pytest.mark.parametrize for cases, pytest.raises for
errors, one test per branch.

## AAA-5 At least one assertion that can fail
No `assert True`, no test that only calls code. An error you expect goes in pytest.raises.

## AAA-6 Arrange with a fixture or builder once it repeats (review)
The same Arrange lines in three tests: extract a fixture named after the state, such as
`bob_has_taipei_10_to_11`. Keep in the test any setup the reader needs to understand the Act.

## AAA-7 Assert the whole outcome (review)
Compare the full value or the exact message:
    assert booking == Booking(2, "Taipei", time(11), time(12), "Alice")
    with pytest.raises(Conflict, match="Taipei is taken 10:00-11:00 by Bob"):
Bad: `assert "Taipei is taken" in str(e)` still passes when the message blames the wrong person.
```

兩個刻意的選擇。**沒有「一個測試一個 assert」**：Wake 自己說「I don't follow that guideline too closely」，同一個結果的不同面向可以一起斷言，模板的 `test_r1_rejected_booking_is_not_stored` 就是 `pytest.raises` 加一行狀態斷言；紀律在 Act，不在 assert 的個數。**AAA-6、AAA-7 標成 review**：「該不該抽 fixture」「斷言夠不夠完整」是判斷題，寫成 lint 只會換來誤報，這是 [pstack Ch 28](../pstack/28-skill-authoring.md) 的「Test cases if structural, skip if subjective」。

## 三、`aaa_lint.py`：用 AST 檢查版面

存成 `.claude/skills/tdd/scripts/aaa_lint.py`。只用標準函式庫，但要 Python 3.11 以上（`ast.TryStar`，見踩雷第四條）：

```python
#!/usr/bin/env python3
"""aaa_lint.py PATH...: check pytest tests for the Arrange-Act-Assert layout (rules: ../rules/aaa.md).
Blank lines split a test body into blocks; the first top-level assert or `with pytest.raises/warns`
starts Assert, and the block above it is the Act. AAA-1 one Act (one statement in that block or
inside pytest.raises). AAA-2 only asserts after the first assertion. AAA-3 a blank line above it.
AAA-4 no if/for/while/try/match, no all(...). AAA-5 an assertion that can fail.
Exit status: 0 clean, 1 problems found, 2 bad arguments.
"""
import argparse
import ast
import sys
from pathlib import Path

LOGIC = {ast.If: ("if", "one test per branch"), ast.For: ("for", "@pytest.mark.parametrize"),
         ast.While: ("while", "@pytest.mark.parametrize"), ast.Try: ("try", "pytest.raises"),
         ast.TryStar: ("try", "pytest.raises"), ast.Match: ("match", "one test per case")}


def is_raises(stmt):  # `with pytest.raises(...)` holds the Act and its Assert
    funcs = [getattr(item.context_expr, "func", None) for item in getattr(stmt, "items", [])]
    return isinstance(stmt, ast.With) and any(
        getattr(f, "attr", getattr(f, "id", None)) in ("raises", "warns") for f in funcs)


def check(fn, lines):
    body = fn.body[1:] if ast.get_docstring(fn, clean=False) is not None else fn.body
    gap = lambda a, b: any(not text.strip() for text in lines[a.end_lineno:b.lineno - 1])
    out = []
    say = lambda node, rule, msg: out.append((node.lineno, rule, f"{fn.name}: {msg}"))
    for node in ast.walk(fn):
        if type(node) in LOGIC:
            say(node, "AAA-4", "`%s` in a test can skip its asserts; use %s" % LOGIC[type(node)])
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "all":
            say(node, "AAA-4", "all(...) is True for an empty collection; compare with the exact list")
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant) and node.test.value:
            say(node, "AAA-5", "assert on a truthy constant cannot fail")
    checks = [i for i, stmt in enumerate(body) if isinstance(stmt, ast.Assert) or is_raises(stmt)]
    if not checks:
        if not any(isinstance(node, ast.Assert) for node in ast.walk(fn)):
            say(fn, "AAA-5", "no assertion; this test can only fail by raising")
        return out
    i, first = checks[0], body[checks[0]]
    if i > 0 and not gap(body[i - 1], first):
        say(first, "AAA-3", f"no blank line above line {first.lineno}, where the Assert section starts")
    k = i - 1  # walk back from the first assertion to the blank line above the Act block
    while k > 0 and not gap(body[k - 1], body[k]):
        k -= 1
    if is_raises(first) and len(first.body) > 1:
        say(first.body[1], "AAA-1", f"pytest.raises wraps {len(first.body)} statements; "
            "after the first one raises, the rest never run")
    elif not is_raises(first) and i == 0:
        say(first, "AAA-1", "no Act above the first assert; assign the result of the call under test first")
    elif not is_raises(first) and i - k > 1:
        say(body[k], "AAA-1", f"{i - k} statements in the Act block (lines {body[k].lineno}-"
            f"{body[i - 1].end_lineno}); keep one Act, move the setup above a blank line")
    later = [stmt for stmt in body[i + 1:] if not isinstance(stmt, ast.Assert)]
    if later:
        say(later[0], "AAA-2", f"acts again after the first assertion (line {first.lineno}); start a new test here")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="test files, or directories to search for test_*.py and *_test.py")
    files, tests, problems = [], 0, 0
    for p in ap.parse_args(argv).paths:
        if not p.exists():
            ap.error(f"no such file or directory: {p}")
        files += sorted({f for pat in ("test_*.py", "*_test.py") for f in p.rglob(pat)}) if p.is_dir() else [p]
    for f in files:
        source = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            fns, found = [], [(e.lineno, "PARSE", f"cannot parse: {e.msg}")]
        else:
            classes = [c for c in tree.body if isinstance(c, ast.ClassDef) and c.name.startswith("Test")]
            nodes = tree.body + [n for c in classes for n in c.body]
            fns = [n for n in nodes if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
            found = sorted(p for fn in fns for p in check(fn, source.splitlines()))
        tests, problems = tests + len(fns), problems + len(found)
        for line, rule, msg in found:
            print(f"{f}:{line}: {rule} {msg}")
    print(f"{len(files)} files, {tests} tests, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

```bash
L=.claude/skills/tdd/scripts/aaa_lint.py
python $L before; echo "exit=$?"
python $L tests .claude/skills/tdd/templates; echo "exit=$?"
```

```
before/test_book_before.py:9: AAA-1 test_booking_flow: 2 statements in the Act block (lines 9-10); keep one Act, move the setup above a blank line
before/test_book_before.py:11: AAA-3 test_booking_flow: no blank line above line 11, where the Assert section starts
before/test_book_before.py:12: AAA-2 test_booking_flow: acts again after the first assertion (line 11); start a new test here
before/test_book_before.py:13: AAA-4 test_booking_flow: `if` in a test can skip its asserts; use one test per branch
before/test_book_before.py:15: AAA-4 test_booking_flow: `try` in a test can skip its asserts; use pytest.raises
before/test_book_before.py:20: AAA-4 test_booking_flow: `for` in a test can skip its asserts; use @pytest.mark.parametrize
before/test_book_before.py:26: AAA-3 test_invalid_ranges: no blank line above line 26, where the Assert section starts
before/test_book_before.py:28: AAA-1 test_invalid_ranges: pytest.raises wraps 2 statements; after the first one raises, the rest never run
1 files, 2 tests, 8 problems
exit=1
2 files, 10 tests, 0 problems
exit=0
```

改寫前的檔 8 個問題，改寫後的測試與模板 0 個：該抓的地方抓，不該抓的地方安靜。每一行說了「哪一行、哪條規則、怎麼修」，這是給 agent 讀的錯誤訊息該有的樣子（[Ch 10](./10-templates-and-scripts.md) 的腳本設計原則）。模板自己也要過 lint：一份連自己的檢查都過不了的模板，會教 agent 錯的形狀。路徑不存在時它印出 `aaa_lint.py: error: no such file or directory: nope` 並以 2 結束；三種結束碼分開，呼叫它的 hook 才分得出「測試有問題」和「我被用錯了」。

## 底層機制：lint 怎麼從 AST 認出三段

`ast.parse` 把原始碼變成語法樹，每個敘述（statement）帶著 `lineno` 與 `end_lineno`，Python 文件的說法是「the first and last line numbers of source text span」。樹裡**沒有空白行，也沒有註解**。看 `test_invalid_ranges` 在樹裡的樣子：

```bash
python -c 'import ast,sys; [print(s.lineno, s.end_lineno, type(s).__name__, len(getattr(s, "body", []))) for s in ast.parse(open(sys.argv[1]).read()).body[-1].body]' before/test_book_before.py
```

```
25 25 Assign 0
26 28 With 2
```

`with` 從第 26 行跨到 28 行，裡面有 2 個敘述。lint 的演算法就建立在這兩件事上：

```
 原始碼 --ast.parse--> 測試函式 body = [敘述 1, 敘述 2, ...]，各帶 lineno..end_lineno
 兩個相鄰敘述之間（前一句 end_lineno 到下一句 lineno）有空白行 --> 切成新區塊
 [Arrange 區塊 ...] [Act 區塊] [第一個 assert 或 with pytest.raises 起算 = Assert]
                       AAA-1 只能 1 句   AAA-2 之後只准 assert   AAA-3 上方要空一行
 整個函式再走一遍（ast.walk）：AAA-4 控制流程與 all(...)，AAA-5 沒有斷言或斷言恆真
```

為什麼不用正規表示式數行？一個敘述可以跨好幾行（上面的 `with` 佔三行），字串裡可以出現 `assert` 這個字，而 `pytest.raises` 裡有幾句，只有語法樹講得準。為什麼不在執行時追蹤呼叫？TDD 的紅燈階段，被測的程式常常還不存在，測試連 import 都會失敗；靜態檢查不用執行，agent 寫完測試的當下就能檢查。在這台機器上，檢查一個檔的牆鐘時間是 0.01 秒（`/usr/bin/time -p` 量三次都是 `real 0.01`），每次編輯都跑也不心疼。

「註解不在樹裡」有一個後果。本章 `edge/` 下的三個實驗檔都只有一個測試（由模板第一個測試改來）和它用到的 import。把這個測試的兩個空白行換成 `# Act`、`# Assert` 註解、開頭再加 `# Arrange`，存成 `edge/test_comment_markers.py`（`def` 在第 6 行）：pytest 照樣通過，lint 卻回報 AAA-1（`3 statements in the Act block (lines 8-11)`）和 AAA-3，`1 files, 1 tests, 2 problems`。註解行不是空白行，三段被當成一段。這是取捨，不是 bug：Meszaros 建議用註解標出各階段，我們選空白行，因為它不增加文字，而 agent 本來就傾向多寫註解（[pstack Ch 23](../pstack/23-unslop-no-comments.md) 專門處理這件事）。要接受註解標記的團隊，見動手練習第 2 題。

## 四、模組化的 `tdd` skill

```
.claude/settings.json + hooks/aaa_hook.py     PostToolUse 的接線與協定轉接頭（第五節）
.claude/skills/tdd/SKILL.md                   脊椎：五步流程與完成條件，21 行
.claude/skills/tdd/rules/aaa.md               AAA-1..AAA-7，容易做錯的幾條帶壞例與好例
.claude/skills/tdd/templates/test_unit.py     三種形狀，能跑、過 lint；scripts/aaa_lint.py 機械檢查 AAA-1..5
```

`SKILL.md`：

```markdown
---
name: tdd
description: Test-first loop for the booking project, with tests in Arrange-Act-Assert layout. Use when adding or changing behaviour in booking/, fixing a bug that has a cheap local repro, or writing or reviewing tests under tests/.
---

# TDD

1. Pick one behaviour. Name the test after its rule: `test_r3_booking_before_8_is_rejected`.
2. Red: read rules/aaa.md, copy a shape from templates/test_unit.py, write one test.
3. Run `python -m pytest -q <file>::<test>`. It must fail with an AssertionError or the expected
   exception from booking/. ImportError, NameError, a fixture error or a SyntaxError is the
   wrong reason: fix the test first.
4. Green: the smallest change in booking/. Leave the tests alone.
5. Refactor on green and rerun the whole suite after each change. Back to 1.

A PostToolUse hook runs scripts/aaa_lint.py on every test file you write or edit. When it
reports problems, fix the test before anything else.

Done when `python -m pytest -q` passes and
`python .claude/skills/tdd/scripts/aaa_lint.py <each test file you wrote or changed>` prints
`0 problems`. Leave old tests you did not touch alone and list their lint problems in your reply.
```

脊椎只寫流程和「何時去讀哪個檔」；第 3 步「紅的原因要對」沿用 pstack `tdd` 技能的護欄（[pstack Ch 8](../pstack/08-bug-fix-playbook.md)）。它把 ImportError 一律算成錯的紅，比 [Ch 19](./19-tdd-by-hand.md) 窄：import 不到的若是這次要新增的名字，C1、C3 的做法是先補一個空的類別或方法，讓測試紅在斷言上（[練習 B](./practice-b-modular-bdd-skill.md) 的 TDD-04 就這樣寫）；第六節的任務要的 `InvalidRange` 本來就在，三臂的紀錄裡都沒有 ImportError。規則細節在 `rules/`，形狀在 `templates/`，能機械判斷的在 `scripts/`。這是 [harness Ch 29](../harness_engineering/29-skills.md) 的漸進式揭露（progressive disclosure），也是 [pstack Ch 28](../pstack/28-skill-authoring.md)「四、什麼時候拆檔」的判準。完成條件只 lint「你寫或改過的檔」是為了既有專案：拿舊測試一起 lint，完成條件永遠是紅的，agent 不是去改一堆範圍外的舊檔，就是學會無視這一條。pstack 的 skill-lint（`node /tmp/lab/tools/skill-lint.mjs .claude/skills`）檢查 frontmatter 與連結，印出 `1 skills, 0 problems`。已經照 [Ch 20](./20-tdd-with-agents.md) 有一份 `tdd` skill 和 `.tdd/phase` 那兩支 hook 的話，不必二選一：把 `rules/`、`templates/`、`scripts/aaa_lint.py` 放進同一個資料夾，在脊椎的紅燈步驟加上「讀 rules/aaa.md」、完成條件加上 lint，再把下一節的 PostToolUse 加進同一份 `settings.json`。Ch 20 用的是 PreToolUse 與 Stop，三個事件互不衝突。

## 五、接成 PostToolUse hook

`.claude/settings.json` 與 `.claude/hooks/aaa_hook.py`：

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{"type": "command", "command": "/tmp/lab/aibdd/.venv/bin/python",
                 "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/aaa_hook.py"], "timeout": 30}]
    }]
  }
}
```

```python
"""aaa_hook.py: PostToolUse hook. After Claude writes or edits test_*.py or *_test.py, run aaa_lint.py on
it. Problems -> one JSON object {"decision": "block", "reason": ...}; otherwise no output.
Exit 0 once the event is read, 1 when stdin is not hook JSON. Usage: aaa_hook.py < event.json
"""
import json
import os
import subprocess
import sys
from pathlib import Path

if "--help" in sys.argv:
    print(__doc__)
    sys.exit(0)
try:
    event = json.load(sys.stdin)
except ValueError as e:
    sys.exit(f"aaa_hook: stdin is not hook JSON ({e})")
path = Path(event.get("tool_input", {}).get("file_path", ""))
if path.suffix != ".py" or not (path.name.startswith("test_") or path.stem.endswith("_test")):
    sys.exit(0)
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
lint = root / ".claude/skills/tdd/scripts/aaa_lint.py"
proc = subprocess.run([sys.executable, str(lint), str(path)], capture_output=True, text=True)
out = proc.stdout.strip().splitlines()
if proc.returncode == 0:
    sys.exit(0)
if proc.returncode == 1 and out and out[-1].endswith(" problems"):
    reason = f"aaa_lint: {out[-1]}. Fix the test before anything else.\n" + "\n".join(out[:-1])
else:  # the lint itself broke: say so instead of passing a traceback off as findings
    reason = f"aaa_hook: aaa_lint.py did not finish under {sys.executable}\n{proc.stderr[-600:]}"
print(json.dumps({"decision": "block", "reason": reason}))
```

設計依據是官方 hooks 文件（對照 2.1.295）。PostToolUse 在工具成功執行**之後**才跑，擋不住寫入，只能回饋；回饋有兩條路：exit 2 讓 Claude 看到 stderr，或 exit 0 印出 `{"decision": "block", "reason": ...}`，文件說 reason 會出現在工具結果旁邊（本章三次執行裡 hook 沒回報過問題，這條路徑在 2.1.295 的真實觸發見 [Ch 3](./03-propose-constrain-feedback.md)「五、跑一次完整的迴圈」）。我們選 JSON，和 [Ch 3](./03-propose-constrain-feedback.md) 的 `run_tests.py` 一致，也把結束碼留給「hook 自己壞了」。`args` 是 exec form（changelog 記在 2.1.139），`${CLAUDE_PROJECT_DIR}` 不用處理引號；直譯器寫絕對路徑的理由見踩雷第四條。30 秒的 `timeout` 是保險：command hook 預設 600 秒，lint 一個檔 0.01 秒。用管線餵假事件測它（文件規定 hook 收到的 `file_path` 一律是絕對路徑）：

```bash
export CLAUDE_PROJECT_DIR=$PWD PY=/tmp/lab/aibdd/.venv/bin/python H=.claude/hooks/aaa_hook.py
ev() { printf '{"hook_event_name":"PostToolUse","tool_name":"%s","tool_input":{"file_path":"%s"}}' "$1" "$PWD/$2"; }
reason() { $PY -c 'import json,sys; print(json.load(sys.stdin)["reason"].strip())'; }
ev Write tests/test_book.py | $PY $H; echo "exit=$?"
ev Edit before/test_book_before.py | $PY $H | reason | head -3 | cut -c1-110
ev Edit booking/schedule.py | $PY $H; echo "exit=$?"
```

```
exit=0
aaa_lint: 1 files, 2 tests, 8 problems. Fix the test before anything else.
/tmp/lab/aibdd/ch21-aaa/before/test_book_before.py:9: AAA-1 test_booking_flow: 2 statements in the Act block (
/tmp/lab/aibdd/ch21-aaa/before/test_book_before.py:11: AAA-3 test_booking_flow: no blank line above line 11, w
exit=0
```

乾淨的測試檔與原始碼檔安靜放行；亂的測試檔得到逐行清單；`echo hello | $PY $H` 這種壞掉的輸入印出 `aaa_hook: stdin is not hook JSON (Expecting value: line 1 column 1 (char 0))` 並以 1 結束，Claude Code 把它當非阻擋錯誤（non-blocking error）。最後 `python -m json.tool .claude/settings.json` 確認它至少是合法的 JSON（欄位寫錯它抓不到）：`claude --help` 對 `-p` 的說明寫著驗證失敗的設定檔在這個模式下會被靜默忽略，這一步不能省。

## 六、改了 skill，agent 的測試變了嗎？

> 以下是實際執行紀錄（節錄），2026-11-02，Claude Code 2.1.295、claude-opus-5-5，每次執行結果不同。

三個專案，同一句 prompt，各跑一次。A：只有 R1、R2 的乾淨專案，`tests/` 是空的，`tdd` skill 是不含 AAA 的四步紅綠單檔版。B：同 A，但 `tests/test_book.py` 是第一節那份亂的舊測試。C：同 B，但換成本章的模組化 `tdd` skill 加上 hook。三個專案的 `schedule.py` 是行為相同的早期寫法（重疊判斷寫在迴圈裡），C 的 lint 與 hook 是本章兩支腳本較長的第一版；我們用 `diff` 確認：lint 兩版對 13 個測試檔、41 個測試的輸出逐字相同，hook 兩版對 7 種輸入的輸出也逐字相同。

```bash
cd eval/$ARM && PATH="/tmp/lab/aibdd/.venv/bin:$PATH" claude -p '/tdd Add business rule R3 to booking/schedule.py: a booking must lie within office hours 08:00-20:00, otherwise Schedule.book raises InvalidRange("outside office hours 08:00-20:00"). A booking may end exactly at 20:00. Put the tests in tests/test_r3_office_hours.py. When you finish, reply with one short paragraph.' \
  --setting-sources project --tools "Read,Write,Edit,Glob,Grep,Bash,Skill" \
  --allowedTools "Read,Write,Edit,Glob,Grep,Skill,Bash(python -m pytest *),Bash(python .claude/skills/tdd/scripts/aaa_lint.py *)" \
  --permission-prompts none --max-turns 20 --max-budget-usd 3 --no-session-persistence \
  --include-hook-events --output-format stream-json --verbose > ../runs/$ARM.jsonl 2> ../runs/$ARM.err
```

（實際執行的是 `eval/run.sh`，參數相同，這裡排成一條指令。）`--setting-sources project` 讓使用者層設定不載入，因為這台機器的使用者設定會改寫 `PATH`（[Ch 0](./00-environment-setup.md)），所以命令前面自己把 venv 的 `bin` 放到 `PATH` 最前面。跑完後我們自己跑 pytest 與 `aaa_lint.py`，再把三份新測試放進同一份 R3 參考實作，用 `plant_bugs.py --bugs r3` 植入五個 R3 bug（08:00 開始被拒、20:00 結束被拒、不檢查開始、不檢查結束、訊息寫成 `8-20`）：

| | A 乾淨＋單檔 skill | B 舊測試很亂＋單檔 skill | C 舊測試很亂＋模組化 skill＋hook |
|---|---|---|---|
| 新測試數／`aaa_lint` 問題數 | 3／0 | 4／0 | 6／0 |
| 名稱帶規則編號（`test_r3_`） | 0/3 | 0/4 | 6/6 |
| 測試資料 | `"Room A"`、`"ann"` | `"Taipei"`、`"Bob"` | `"Taipei"`、`"Bob"` |
| 錯誤訊息怎麼比對 | `str(exc.value) == ...` | `str(excinfo.value) == ...` | `pytest.raises(..., match=...)`；成功時比對整個 `Booking` |
| 植入的 R3 bug 抓到 | 5/5 | 5/5 | 5/5 |
| hook 執行次數（回報問題） | 無 hook | 無 hook | 10（0） |
| 被權限擋下的 shell 寫檔 | 1（`cat >>`） | 1（`cat >>`） | 1（`python - <<'EOF'` 改原始碼） |
| 工具呼叫／成本／耗時 | 22／$0.39／48 秒 | 23／$0.41／51 秒 | 25／$0.47／55 秒 |

1. **形狀沒變**。三次的新測試都已經是 AAA，lint 全是 0；B 旁邊擺著亂的舊測試，agent 也沒抄它的形狀（倒是抄了它的資料 `Taipei`、`Bob`）。三臂都先只寫一個測試，看到 `1 failed` 才改 `booking/`；之後 A 一次加一個，B、C 各有一次補上 2 個與 4 個。對這個模型、這個任務，AAA 不用教。
2. **變的是慣例**。C 在寫第一個測試前依序讀了 `rules/aaa.md`、`templates/test_unit.py`、`scripts/aaa_lint.py`（從 stream-json 的工具呼叫看到）；只有它用規則編號命名、照模板的形狀寫斷言（錯誤用 `match=`，成功時比對整個 `Booking`；A、B 的 `str(exc.value) == ...` 這類寫法一樣是完整比對，只是形狀不同），回覆裡逐行列出舊檔的問題卻沒去動它：「I left the old `tests/test_book.py` alone, and it has 8 lint problems: `test_booking_flow` has AAA-1 (line 9), AAA-3 (line 11), AAA-2 (line 12) and AAA-4 (lines 13, 15 and 20), and `test_invalid_ranges` has AAA-3 (line 26) and AAA-1 (line 28).」三件事分別對應脊椎第 1 步、模板、完成條件，都是專案自己的約定，模型猜不到。
3. **抓 bug 的能力沒變**。五個 R3 bug 三份都抓到。AAA 讓測試好讀、好定位，不保證斷言夠強；那要靠突變測試（mutation testing，[Ch 19](./19-tdd-by-hand.md) 手做過一次）。
4. **hook 跑了 10 次，全部安靜**（4 次是測試檔、lint 0；6 次是 `booking/schedule.py`，直接放行）。從沒擋過東西的閘門在這裡是正常狀態，它的價值是保證。三臂各試過一次用 shell 寫檔，全被權限擋下，見踩雷第五條。

限制要說清楚：每臂只跑一次，差異可能是雜訊；agent 看得見工作目錄名稱（`eval/b-messy` 這種），違反 [pstack Ch 29](../pstack/29-eval-playbook.md)「一、盲測規則」的去識別化要求；`SKILL.md` 第 1 步的命名範例 `test_r3_booking_before_8_is_rejected` 正好落在這次的任務上，C 的第一個測試就叫這個名字，第 2 點的命名差異有一部分是照抄範例；我們量的是腳本量得到的指標，可讀性這種主觀品質要靠盲評審，本章沒做（未實測）。也沒測較小的模型，換成它們，hook 回報問題的次數可能不是 0，請自己跑一次。

## 對比與取捨

| 把 AAA 放在哪 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| CLAUDE.md 一行（Ch 4 的做法） | 零成本，永遠在 context | 沒有例子、沒有檢查，會被別的指示稀釋 | 原型、個人專案 |
| skill 的 rule file＋模板 | 帶好例壞例，只在寫測試時載入 | 靠 agent 讀到並照做 | 有測試慣例的團隊 |
| PostToolUse hook（本章） | 每次編輯立即回饋，agent 當場修 | 擋不住 shell 寫檔；每次編輯付一點時間 | agent 大量寫測試時 |
| Stop hook 或 CI 跑整個 `tests/` | 收工前或合併前一次查全部，不怕繞路 | 回饋晚，改起來貴 | 任何團隊的底線 |
| skill frontmatter 裡的 hook | hook 跟著 skill 資料夾走 | 文件說 skill 被叫用後才註冊、之後整個 session 都在；沒叫 skill 就沒有（未實測，可在 session 裡用 `/hooks` 確認） | 打包成 plugin 發佈的 skill |
| 只靠 code review | 能判斷 AAA-6、AAA-7 這種語意問題 | 慢，量大時審不完 | 永遠需要，但不該是唯一一層 |

## 踩雷集錦

1. **錯誤直覺：AAA 就是「一個測試一個 assert」**。正確認識：紀律在**一個 Act**。Wake 明說他不太遵守單一斷言的建議；同一個結果的幾個面向一起斷言沒問題，該拆的是第二個 Act。
2. **錯誤直覺：測試裡寫迴圈可以「一次測很多情況」**。正確認識：迴圈與 `if` 讓測試可能沒在測。forgot-return 就是 `if b2:` 放走的；空串列讓 `for` 一次都不執行。多組輸入用 `@pytest.mark.parametrize`，每組各自成為一個測試項目、各自報告。
3. **錯誤直覺：lint 0 problems 就是好測試**。正確認識：lint 只看形狀。把模板第一個測試的斷言換成 `assert booking is not None`，存成 `edge/test_weak.py`：lint 是 `1 files, 1 tests, 0 problems`，但 `python plant_bugs.py . edge/test_weak.py tests/test_book.py` 的 returns-first 那一列是 `1 passed` 對 `1 failed, 4 passed`，弱斷言放走了「回傳別人的預約」。斷言強不強是 AAA-7，歸 review 與突變測試；[pstack Ch 14](../pstack/14-verification-principles.md)「四、Test Behavior, Not Implementation」的「Break the behavior」是同一個檢查的手動版。
4. **錯誤直覺：hook 會用我啟動 Claude Code 時那個 Python**。正確認識：hook 繼承 Claude Code 的環境，使用者設定可以改寫 `PATH`。把 hook 交給系統的 `python3`（3.9.6）跑，`ev Write tests/test_book.py | env PATH=/usr/bin:/bin python3 $H | reason | sed -n '1p;$p'` 得到 `aaa_hook: aaa_lint.py did not finish under /Applications/Xcode.app/Contents/Developer/usr/bin/python3` 與 `AttributeError: module 'ast' has no attribute 'TryStar'`（`ast.TryStar` 是 3.11 才有的節點）。修法是設定裡寫絕對路徑；hook 靠 lint 的摘要行分辨「測試有問題」與「lint 壞了」，lint 當掉才不會被當成測試有問題回報給 agent。
5. **錯誤直覺：PostToolUse 接在 `Write|Edit` 上，所有寫入都會被檢查**。正確認識：官方文件寫明「Claude Code doesn't run a `PostToolUse` hook matching `Edit|Write` when a `Bash` command or a process outside Claude Code rewrites the same file」。第六節三次執行都試過一次 shell 寫檔，擋下它們的是權限：指令不在窄的 `--allowedTools` 裡，`--permission-prompts none` 又讓需要核准的動作自動被拒，不是 hook。Bash 只放行特定指令，再讓 Stop hook 或 CI 對整個 `tests/` 跑一次 lint；文件另外指向 `FileChanged` 事件處理「不管誰寫的」情況，但它只能事後通知、不能擋，matcher 要寫字面檔名（本章未實測）。

## 進階：Arrange 交給 fixture，名字對上 Given

AAA-6 說 Arrange 重複了就抽出去，pytest 的 fixture 是最自然的地方。把模板第一個測試 Arrange 的兩行搬進 `@pytest.fixture def bob_has_taipei_10_to_11()`（建 `Schedule`、訂 Bob 的 10:00–11:00、回傳 schedule），測試改成接收這個 fixture、本體只剩 Act 與 Assert；存成 `edge/test_fixture.py` 實測是 `1 passed`，lint `1 files, 1 tests, 0 problems`。代價是 Wake 講過的「you have to look in two places to understand the full Arrange-ment」，所以 fixture 名字要替讀者省掉那一趟：`bob_has_taipei_10_to_11` 幾乎就是 `Given Bob has booked "Taipei" from 10:00 to 11:00`。這不是巧合，pytest-bdd 的步驟可以用 `target_fixture` 把回傳值變成 fixture；[Ch 27](./27-ai-bdd-loop.md) 的 step definitions 正是把三段分給三種步驟函式：Given 準備狀態，When 用 `target_fixture` 交出結果，Then 只觀察。需要收拾（關檔、清資料庫）時寫在 fixture 的 `yield` 之後，那就是 pytest 的 Cleanup，測試本體仍只有三段。另外兩個方向本章都未實測：**在設定層過濾**：文件說 hook handler 的 `if` 欄位接受權限規則語法（例如 `"Edit(*.ts)"`），可以讓非測試檔的編輯連 Python 都不啟動。一個 `if` 只放一條規則，`test_*.py` 與 `*_test.py` 要各寫一個 handler；權限文件說 `Edit(...)` 規則涵蓋所有內建的改檔工具、`Write(...)` 的路徑規則永遠不匹配，所以兩條都寫成 `Edit(...)`，Write 有沒有一起被濾到，用 `--include-hook-events` 看 hook 事件確認。**換語言**：同樣五條規則可以寫成 TypeScript 的 lint，演算法不變，只是換成那個語言的語法樹。

## 動手練習

1. 不看第一節，自己把 `before/test_book_before.py` 改寫一次，目標是 `aaa_lint.py` 為 0、`python plant_bugs.py . before tests` 的 before 欄抓到 5 個。第一次沒抓到的是哪個 bug？它對應哪條規則？
2. 用標準函式庫的 `tokenize` 讀出 `COMMENT` token 的行號，讓 `# Act`、`# Assert` 所在的行也算分段點。先讓 `edge/test_comment_markers.py` 從 2 problems 變 0，再確認 `before/` 仍是 8；只跑一邊等於沒驗。
3. 照 pstack Ch 29 重做第六節：目錄改成像專案的名字、每臂至少 3 次、加一個看不到臂別的評審比可讀性。你的結論和第六節的第 1、2 點一樣嗎？

## 本章重點整理

- AAA、Given/When/Then、四階段測試是同一個形狀：準備、只動一個變因、量測。紀律在一個 Act，不在 assert 的個數。
- 形狀不對的測試會在綠燈下說謊：改寫前在 5 個植入的 bug 裡抓到 2 個，改寫後 5 個；`if`、兩句的 `pytest.raises`、片段比對各放走一個。
- `aaa_lint.py` 用 AST 的 `lineno`／`end_lineno` 加上原始碼裡的空白行切出三段；樹裡沒有空白行與註解，所以註解不算分段。
- 模組化的 `tdd` skill：脊椎寫流程與完成條件，規則帶例子放 `rules/`，能跑的形狀放 `templates/`，機械檢查放 `scripts/`，PostToolUse hook 在每次編輯後回饋。三次真實執行裡，模型不用教就寫出 AAA；skill 改變的是專案慣例，抓 bug 的能力三臂相同。

## 自我檢核

- [ ] 不看本章，說出 AAA-1 到 AAA-5 各擋哪一種「綠燈說謊」，並為其中兩條各舉一個 plant_bugs 表上的例子。
- [ ] `with pytest.raises(...)` 裡放兩句，為什麼第二句永遠不會執行？lint 從語法樹的哪裡看出來？
- [ ] Wake、Meszaros、pytest 文件對「第四步」的態度各是什麼？pytest 把它放在哪，測試本體才維持三段？
- [ ] 為什麼 lint 用空白行而不是註解分段？想接受註解要用哪個模組，為什麼 `ast` 做不到？
- [ ] 第六節的四個結論裡，哪幾個能歸因於 skill、哪幾個不能？每臂一次與看得見的目錄名稱各削弱了哪一個？
- [ ] 一個接在 `Write|Edit` 上的 PostToolUse hook，在什麼情況下完全看不到 agent 寫進測試檔的內容？你用哪兩層補上？

## 延伸閱讀

- **[Bill Wake — 3A – Arrange, Act, Assert](https://xp123.com/articles/3a-arrange-act-assert/)**（xp123.com，2011-04-26）
  - **讀哪裡**：全文不長，重點是 FAQ 五題裡的前三題（一連串的 act 與 assert、驗證 setup 的額外斷言、「one assert per test」）；寫過任一種 xUnit 測試就讀得懂。**學到什麼**：3A 的原始定義、「Assert First」的寫法、作者自己允許的例外。**和本章的關聯**：AAA-2 與踩雷第一條出自這裡，進階一節 fixture 的代價也是他講的。
- **[Gerard Meszaros — Four Phase Test](http://xunitpatterns.com/Four%20Phase%20Test.html)** 與 **[Conditional Test Logic](http://xunitpatterns.com/Conditional%20Test%20Logic.html)**（xunitpatterns.com；書為《xUnit Test Patterns》，Addison-Wesley，2007）
  - **讀哪裡**：兩頁的「Why We Do This」與「Impact」；網站是出版前的草稿，頁首指向書的第 358 頁與第 200 頁。**學到什麼**：缺陷定位與單一條件測試（Single Condition Test）的論證，以及「Any control structures within a Test Method should be viewed with extreme suspicion!」。**和本章的關聯**：AAA-4 的來源；第一節 `pytest -rf` 那段比的就是缺陷定位。
- **[Martin Fowler — GivenWhenThen](https://martinfowler.com/bliki/GivenWhenThen.html)**（bliki，2013-08-21）
  - **讀哪裡**：全文一頁，注意框架把 given 當成「a set of commands」那段。**學到什麼**：Given/When/Then、四階段測試、3A 的關係，以及 then 的查詢應該沒有副作用。**和本章的關聯**：先建立直覺那張對照表的依據；讀完回看 [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)。
- **[pytest — Anatomy of a test](https://docs.pytest.org/en/stable/explanation/anatomy.html)**
  - **讀哪裡**：整頁，特別是 Act 的定義「the singular, state-changing action」。**學到什麼**：pytest 官方的四步與「the test is ultimately the act and assert steps」。**和本章的關聯**：AAA-1 的「一個 Act」；進階一節的 fixture 寫法就是這句話的實作。
- **[Claude Code — Hooks reference](https://code.claude.com/docs/en/hooks)**
  - **讀哪裡**：「Exit code 2 behavior per event」表、「PostToolUse」的 decision control、「Hooks in skills and agents」；先讀過 [harness Ch 30](../harness_engineering/30-hooks.md) 的「三、PostToolUse」。**學到什麼**：PostToolUse 能回饋什麼、擋不住什麼，exit code 與 JSON 輸出各自的效果。**和本章的關聯**：第五節每個接線決定的依據；hook 協定隨版本變，以你安裝的版本為準。

測試的形狀管好了，下一個問題是先寫哪一層的測試：從使用者碰得到的 HTTP 介面開始，讓外圈的驗收測試帶著內圈的 AAA 單元測試走。

→ [Ch 22 後端 E2E 先行：outside-in 雙迴圈](./22-backend-e2e-first.md)
