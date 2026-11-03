# Ch 26 — BDD 到底在做什麼：從產物看 TDD 與 BDD 的差別

> **目標**：靠產物而不是口號分辨 TDD 與 BDD。同一組規則（R1、R2）先寫成單元測試，再寫成 feature 檔、step definitions 與 driver，最後產出活文件，三疊都在 pytest-bdd 9.0.1 上實際跑過；說得出每份產物誰寫、誰讀、誰能改，為什麼 feature 檔是比單元測試更耐用的 agent 合約、它又在哪裡失守；看懂 pytest-bdd 把一句英文變成一次函式呼叫的機制，以及它的 cucumber JSON 悄悄丟掉的東西。

> **環境**：Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（Gherkin parser 是 gherkin-official 42.0.1），用 [Ch 0](./00-environment-setup.md) 建好的 `/tmp/lab/aibdd/.venv`；git 2.54；macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch26-bdd-vs-tdd/`，輸出都是 2026-11-02 實際跑出來的。本章沒有呼叫 LLM，文中「agent 的捷徑」是我們手動做出來的 diff。

## 為什麼需要這個？

這個 repo 已經從概念上比較過 TDD 和 BDD：[SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md) 的「四個方法的對比表」逐列比較核心問題、受眾、產物和粒度；[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md) 的「一個可以跑的範例：同一個需求，四種記法」把登入鎖定寫成 unittest 和 `.feature` 並排；[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 講了 test 改叫 behaviour 的來龍去脈，也附了一組實測過的 step definitions。還沒做的是：把兩疊產物放進**同一個專案**跑起來，再逐份問誰寫、誰讀、誰能改。agent 讓這三個問題變得要緊。以前寫測試的工程師和審 PR 的工程師至少是兩個腦袋；現在同一個 agent 在同一個迴圈裡寫實作也寫測試，測試不再自動算是獨立的檢查。我們需要一份 agent 不能隨手改、業務方又讀得懂的產物，feature 檔是最常被提名的候選。本章用實際執行檢驗這個說法，連它不成立的地方一起看。

歷史只交代到夠用（細節在 SDD Ch 10）。Dan North（現名 Daniel Terhorst-North）教 TDD 時發現學員卡在同一串問題：「where to start, what to test and what not to test, how much to test in one go, what to call their tests, and how to understand why a test fails」。他改用 behaviour 這個詞，2003 年開始寫取代 JUnit 的 JBehave；2004 年底和業務分析師 Chris Matts 談過後，兩人發現他們在替「分析過程本身」定義通用語言，Given/When/Then 由此而來。〈Introducing BDD〉2006 年 3 月刊在 *Better Software*，其中一節標題就是「Acceptance criteria should be executable」。後來 Aslak Hellesøy 的 Cucumber 讓這種句子以純文字檔執行，那個格式叫 Gherkin。

## 先建立直覺

把它想成一次裝修留下的三份文件：

- **合約條款**：「浴室地板往排水孔傾斜，倒一桶水十秒內流光。」你讀得懂、簽了名，改一個字要你同意。這是 feature 檔。
- **包商的施工記錄**：水泥配比、防水刷幾道。對包商很重要，你不讀也讀不懂。這是單元測試。
- **驗收報告**：每條條款旁打勾或打叉，而且是驗收當天實際倒水量出來的。這是活文件（living documentation）。

把包商換成一個手很快、自己寫施工記錄的 agent。它想讓驗收通過有三條路：把坡度做對；改施工記錄，反正你不讀；改合約，但你會讀，而且可以規定施工期間合約不准動。feature 檔被當成「agent 合約」的理由就在第三條。這個比喻有一處會誤導人，而那一處正是本章最重要的發現：驗收不是你親手倒水，是用**包商寫的量測工具**量的，也就是 step definitions（把 feature 檔的每一句對應到一段程式的函式）。工具做壞了，合約一字未改，報告照樣全部打勾。

```
  [TDD]                            [BDD]
  tests/unit/test_schedule.py      features/booking.feature           ← PM、QA、RD 共寫共審
     │                                │ 每一句交給一個 step definition
     │                             tests/acceptance/test_booking.py   ← RD 或 agent 寫，RD 讀
     │                                │ 經由 driver 操作系統
     │                             tests/acceptance/driver.py         ← RD 或 agent 寫，RD 讀
     └──────► booking/schedule.py ◄───┘
```

TDD 那一疊全是程式碼：寫的是 RD 或 agent，讀的是會讀 Python 的人。BDD 那一疊多了兩種 TDD 沒有的東西：三方讀得懂的 feature 檔，和每次執行都重新產生、任何人都能讀的活文件（`pytest --cucumberjson` 再經 `living_doc.py` 轉成 `reports/living-doc.md`）。夾在中間的 step definitions 和 driver 跟單元測試一樣是程式碼，也一樣多半由 agent 寫；比喻失準的那一處就在這裡。

## BDD 不是一種測試寫法：三個實踐各留下什麼

「BDD 就是用 Given/When/Then 寫測試」只描述了三分之一。Cucumber 官方文件把 BDD 拆成三個實踐，Seb Rose 與 Gáspár Nagy 的《The BDD Books》也照這三段規劃成書（出版過的是《Discovery》2018、《Formulation》2021 兩冊，內容將併入 Manning 還在 MEAP 階段的《Effective Behavior-Driven Development》）：

| 實踐 | 在做什麼（依 Cucumber 的說法） | 留下的產物 | 主要參與者 | 本課 |
|---|---|---|---|---|
| 發現（discovery） | 拿一個小改動談具體例子，探索、發現並同意細節 | example map：規則、例子、問題 | PM、QA、RD（Three Amigos） | [Ch 8](./08-translation-sandwich.md)、[Ch 29](./29-example-mapping.md) |
| 表述（formulation） | 把例子寫成能自動化的形式，再確認大家同意 | `features/*.feature` | QA 或 RD 執筆，PM 審 | [Ch 28](./28-gherkin-dsl-style.md) |
| 自動化（automation） | 從一個自動化測試開始，實作例子描述的行為 | step definitions、driver、活文件 | RD、agent | 本章、[Ch 27](./27-ai-bdd-loop.md)、[Ch 30](./30-one-gherkin-two-drivers.md) |

TDD 整個落在第三列。「從產物看差別」的答案因此很直接：**TDD 只有自動化的產物；BDD 多了前兩列，而前兩列是寫給不寫程式的人讀的**。本章的實驗集中在第二列和第三列的交界，因為 agent 就站在那條線上：第三列歸它寫，第二列它不該碰。

## 實驗：同一組規則，兩疊產物

規則是全課共用的 R1（同一間會議室不能重疊，區間半開）與 R2（結束要晚於開始），見 [Ch 1](./01-course-map.md) 的「貫穿全課的範例：會議室預約」。從空目錄開始；`pytest.ini` 的 `pythonpath = .` 讓測試 import 得到 `booking`，`markers` 登記 feature 檔會用到的 tag（pytest-bdd 會把每個 Gherkin tag 變成 pytest marker，沒登記會被警告）：

```bash
mkdir -p /tmp/lab/aibdd/ch26-bdd-vs-tdd && cd /tmp/lab/aibdd/ch26-bdd-vs-tdd
source /tmp/lab/aibdd/.venv/bin/activate
mkdir -p booking tests/unit tests/acceptance features reports && touch booking/__init__.py
cat > pytest.ini <<'EOF'
[pytest]
pythonpath = .
markers =
    r1: rule tag
    r2: rule tag
    sc_book_01: scenario id
    sc_book_02: scenario id
    sc_book_03: scenario id
EOF
```

被測的是一個純領域層。[Ch 19](./19-tdd-by-hand.md) 用 TDD 一步一步長出過 `Schedule.book`；這裡是一份精簡版，多公開一個 `bookings` 清單給 driver 讀（是屬性，不是 [Ch 21](./21-tdd-skill-aaa.md) 加的 `bookings()` 方法）。注意 `start < b.end and b.start < end` 這行半開區間的重疊判斷：兩段相交，若且唯若各自的開始都早於對方的結束，所以 11:00 開始的預約可以緊接在 10:00-11:00 後面；等一下我們會故意把它弄壞。

```python
# booking/schedule.py
from dataclasses import dataclass
from datetime import time

class BookingError(Exception): ...  # a business rule said no; str(error) is what users see
class Conflict(BookingError): ...  # R1
class InvalidRange(BookingError): ...  # R2

@dataclass(frozen=True)
class Booking:
    id: int
    room: str
    start: time
    end: time
    by: str

class Schedule:
    def __init__(self) -> None:
        self.bookings: list[Booking] = []

    def book(self, room: str, start: time, end: time, by: str) -> Booking:
        if end <= start:
            raise InvalidRange("end must be after start")
        for b in self.bookings:  # half-open [start, end): 11:00 may follow 10:00-11:00
            if b.room == room and start < b.end and b.start < end:
                raise Conflict(f"{room} is taken {b.start:%H:%M}-{b.end:%H:%M} by {b.by}")
        booking = Booking(len(self.bookings) + 1, room, start, end, by)
        self.bookings.append(booking)
        return booking
```

### 產物一：TDD 的單元測試

```python
# tests/unit/test_schedule.py
from datetime import time

import pytest

from booking.schedule import Conflict, InvalidRange, Schedule

def test_overlap_is_rejected_and_names_the_holder():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")
    with pytest.raises(Conflict) as err:
        schedule.book("Taipei", time(10, 30), time(11, 30), "Alice")
    assert str(err.value) == "Taipei is taken 10:00-11:00 by Bob"

def test_back_to_back_slots_do_not_overlap():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")
    assert schedule.book("Taipei", time(11), time(12), "Alice").by == "Alice"

@pytest.mark.parametrize("start, end", [(time(10), time(10)), (time(11), time(10))])
def test_end_must_be_after_start(start, end):
    with pytest.raises(InvalidRange, match="end must be after start"):
        Schedule().book("Tokyo", start, end, "Alice")
```

### 產物二：feature 檔、step definitions、driver

```gherkin
# features/booking.feature
Feature: Room booking
  Rooms are shared. A booking holds a room for the half-open
  interval [start, end) on one day.

  @r1
  Rule: R1 A room cannot be booked twice for overlapping times

    @sc_book_01
    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

    @sc_book_02
    Scenario: Back-to-back slots do not overlap
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed

  @r2
  Rule: R2 End must be after start

    @sc_book_03
    Scenario Outline: A booking must end after it starts
      Given room "Tokyo" is free all day
      When Alice books "Tokyo" from <start> to <end>
      Then the booking is rejected because the end must be after the start

      Examples:
        | start | end   |
        | 10:00 | 10:00 |
        | 11:00 | 10:00 |
```

```python
# tests/acceptance/driver.py
from datetime import time

from booking.schedule import BookingError, Schedule

class DomainDriver:  # the only place that knows how to reach the system; Ch 30 adds API and UI drivers
    def __init__(self): self.schedule = Schedule()

    def book(self, room, start, end, by):
        try:
            self.schedule.book(room, time.fromisoformat(start), time.fromisoformat(end), by)
        except BookingError as err:
            return ("rejected", str(err))
        return ("confirmed", None)

    def agenda(self, room):
        mine = sorted((b for b in self.schedule.bookings if b.room == room), key=lambda b: b.start)
        return [f"{b.start:%H:%M}-{b.end:%H:%M} {b.by}" for b in mine]
```

```python
# tests/acceptance/test_booking.py
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from driver import DomainDriver

scenarios("../../features")

@pytest.fixture
def driver(): return DomainDriver()

@given(parsers.parse('room "{room}" is free all day'))
def free_all_day(driver, room): assert driver.agenda(room) == []

@given(parsers.parse('{who} has booked "{room}" from {start} to {end}'))
def has_booked(driver, who, room, start, end): assert driver.book(room, start, end, who) == ("confirmed", None)

@when(parsers.parse('{who} books "{room}" from {start} to {end}'), target_fixture="result")
def books(driver, who, room, start, end): return driver.book(room, start, end, who)

@then("the booking is confirmed")
def confirmed(result): assert result == ("confirmed", None)

@then(parsers.parse('the booking is rejected because "{room}" is taken {slot} by {who}'))
def rejected_taken(result, room, slot, who): assert result == ("rejected", f"{room} is taken {slot} by {who}")

@then("the booking is rejected because the end must be after the start")
def reversed_range(result): assert result == ("rejected", "end must be after start")
```

`Rule:` 帶著規則 ID，`@sc_book_0N` 給每個例子一個穩定 ID，之後 tasks（[Ch 16](./16-tasks-binder.md)）和測試計畫（[Ch 24](./24-testplan-skill.md)）引用的是 tag，因為標題會被改；R1 的步驟句子和 [Ch 0](./00-environment-setup.md) 雛形的一致，R2 的 Then 用 [練習 A](./practice-a-first-loop.md) 定下的標準句（[Ch 28](./28-gherkin-dsl-style.md) 起的 lab 用一個變體，原因在那一章）。driver 把結果翻成 `("confirmed", None)` 或 `("rejected", 訊息)` 這種和介面無關的形狀；換一個打 HTTP 或開瀏覽器的 driver，只要 `book` 與 `agenda` 的簽名和回傳形狀不變，feature 檔和 step definitions 都不必改，要換的只有 `driver` 這個 fixture。[Ch 30](./30-one-gherkin-two-drivers.md) 照這個原則接上 API 與 UI 兩個 driver，但接在 Ch 28 的 step 與 DSL 後面，不是這裡的：它的 `book(who, room, start, end)` 回傳 `Outcome`，`agenda(room)` 和這裡一樣回傳 `"10:00-11:00 Bob"` 這種字串清單。三條接線在 SDD Ch 10 的「工具鏈：從 .feature 到可執行測試」逐條解釋過：`scenarios("../../features")` 把資料夾裡每個情境綁成一個測試，`target_fixture="result"` 把 When 的回傳值交給 Then，Then 只觀察、不動手。`from driver import ...` 找得到，是因為 `tests/acceptance/` 沒有 `__init__.py`，pytest 預設的 prepend 匯入模式會把它放進 `sys.path`。兩疊一起跑，再用 tag 切片：

```bash
pytest tests -v 2>&1 | grep -E "PASSED|FAILED|passed|failed"
pytest tests -q -m r1 | tail -1
pytest tests -q -m sc_book_02 | tail -1
```

```
tests/acceptance/test_booking.py::test_booking_an_overlapping_slot PASSED [ 12%]
tests/acceptance/test_booking.py::test_backtoback_slots_do_not_overlap PASSED [ 25%]
tests/acceptance/test_booking.py::test_a_booking_must_end_after_it_starts[10:00-10:00] PASSED [ 37%]
tests/acceptance/test_booking.py::test_a_booking_must_end_after_it_starts[11:00-10:00] PASSED [ 50%]
tests/unit/test_schedule.py::test_overlap_is_rejected_and_names_the_holder PASSED [ 62%]
tests/unit/test_schedule.py::test_back_to_back_slots_do_not_overlap PASSED [ 75%]
tests/unit/test_schedule.py::test_end_must_be_after_start[start0-end0] PASSED [ 87%]
tests/unit/test_schedule.py::test_end_must_be_after_start[start1-end1] PASSED [100%]
============================== 8 passed in 0.02s ===============================
2 passed, 6 deselected in 0.01s
1 passed, 7 deselected in 0.01s
```

上半是 BDD，下半是 TDD，都是 4 個。兩邊的名字都是句子：North 當年從 agiledox 學到的那一課，現代 TDD 早就吸收了，所以差別**不在命名**。差別在讀者：`pytest.raises(Conflict)`、`time(10, 30)`、`[start0-end0]`（可以用 `ids=` 改善，改完讀者還是工程師）都假設讀者會讀 Python；feature 的測試名直接從情境標題來（`Back-to-back` 的連字號被丟掉），Outline 的參數 id 是那一列的值。粒度也不同：單元測試驗的是 `Schedule` 這個類別的契約，哪天重疊檢查搬進資料庫，這組測試多半得跟著重寫，需求卻一個字也沒變。最後兩行是 tag：Rule 上的 `@r1` 套到它底下每個情境，「只跑 R1」不需要知道任何函式名稱。

### 產物三：活文件

`--cucumberjson` 寫出的是給工具讀的 JSON（單元測試不會進來，狀態掛在每一步上）；`living_doc.py` 把它轉成給人讀的 Markdown，`--junit` 再拿 pytest 自己寫的 JUnit XML 當狀態的第二個來源。腳本只用標準函式庫，存在實驗目錄的根部：

<details><summary>living_doc.py 完整原始碼（36 行）</summary>

```python
# living_doc.py
"""Turn pytest-bdd's cucumber JSON into Markdown for people who don't read Python.

--junit adds the status pytest itself wrote: the cucumber JSON leaves out a step
that has no step definition, and the whole scenario when that step comes first."""
import argparse
import json
import sys
import xml.etree.ElementTree as ET

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("report", help="file written by pytest --cucumberjson=PATH")
ap.add_argument("--junit", help="file written by pytest --junitxml=PATH")
args = ap.parse_args()
junit_failed = set()
if args.junit:
    for case in ET.parse(args.junit).iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            junit_failed.add(case.get("name"))
total = failed = 0
for feature in json.load(open(args.report, encoding="utf-8")):
    print(f"## {feature['name']} ({feature['uri']})\n")
    for sc in feature["elements"]:
        ok = all(s["result"]["status"] == "passed" for s in sc["steps"]) and sc["id"] not in junit_failed
        junit_failed.discard(sc["id"])
        total, failed = total + 1, failed + (not ok)
        variant = sc["id"][sc["id"].find("["):] if "[" in sc["id"] else ""
        tags = " ".join("@" + t["name"] for t in sc["tags"])
        print(" ".join(p for p in (f"- [{'PASS' if ok else 'FAIL'}]", sc["name"], variant, tags) if p))
        for step in sc["steps"]:
            print(f"  - {step['keyword']} {step['name']}")
    print()
for name in sorted(junit_failed):  # pytest failed it, the JSON has no trace of it: only the test name is left
    print(f"- [FAIL] {name} (only in the JUnit XML)")
print(f"{total + len(junit_failed)} scenarios, {failed + len(junit_failed)} failed", file=sys.stderr)
sys.exit(1 if failed or junit_failed else 0)
```

</details>

```bash
pytest tests -q --cucumberjson=reports/cucumber.json --junitxml=reports/junit.xml | tail -1
python living_doc.py reports/cucumber.json --junit reports/junit.xml > reports/living-doc.md; echo "exit=$?"
head -5 reports/living-doc.md
```

```
8 passed in 0.01s
4 scenarios, 0 failed
exit=0
## Room booking (features/booking.feature)

- [PASS] Booking an overlapping slot @sc_book_01
  - Given Bob has booked "Taipei" from 10:00 to 11:00
  - When Alice books "Taipei" from 10:30 to 11:30
```

輸出裡沒有 `R1`、`R2` 的標題，不是腳本漏寫：pytest-bdd 的 JSON 根本沒有 Rule 這一層。為什麼要第二個來源，先看沒有它會怎樣。後面每個實驗都會弄壞東西，所以先 commit，壞了用 `git checkout` 還原；然後加兩條還沒寫完 step definitions 的情境，第二條連第一步都沒有定義：

```bash
printf '__pycache__/\n.pytest_cache/\nreports/\n' > .gitignore
git init -q && git config user.name lab && git config user.email lab@example.com
git add -A && git commit -qm "booking: R1 and R2 as unit tests and as a feature"
cat >> features/booking.feature <<'EOF'

  Rule: R1 follow-up, not built yet

    Scenario: Bob hears about the attempt
      Given Bob has booked "Seoul" from 10:00 to 11:00
      When Alice books "Seoul" from 10:30 to 11:30
      Then the booking is rejected because "Seoul" is taken 10:00-11:00 by Bob
      And Bob gets a notification about the attempt

    Scenario: Bob can mute the notifications
      Given Bob has muted notifications
EOF
pytest tests/acceptance -q --cucumberjson=reports/c.json --junitxml=reports/j.xml | tail -1
python living_doc.py reports/c.json | grep -A3 "Bob hears"
python living_doc.py reports/c.json --junit reports/j.xml | grep -E "Bob hears|mute"
git checkout -q features/booking.feature
```

```
2 failed, 4 passed in 0.01s
5 scenarios, 0 failed
- [PASS] Bob hears about the attempt
  - Given Bob has booked "Seoul" from 10:00 to 11:00
  - When Alice books "Seoul" from 10:30 to 11:30
  - Then the booking is rejected because "Seoul" is taken 10:00-11:00 by Bob
6 scenarios, 2 failed
- [FAIL] Bob hears about the attempt
- [FAIL] test_bob_can_mute_the_notifications (only in the JUnit XML)
```

pytest 說兩個失敗。第一條在 JSON 裡只列出跑到的三步、全部 passed，第四步根本不在報告裡；第二條整個不在 JSON 裡，只讀 JSON 的活文件連它存在都不知道，還報「5 scenarios, 0 failed」。JUnit XML 由 pytest 自己寫，不經過 pytest-bdd 的報告機制，拿它交叉比對兩條都抓得到；JSON 裡找不到的那條沒有標題可印，只剩測試函式名。原因在「底層機制」第 4 點。

## 誰寫、誰讀、誰能改：把 feature 檔當 agent 的合約

| 產物 | 誰寫 | 誰讀、誰審 | agent 在實作期間 | 被改了誰看得出來 |
|---|---|---|---|---|
| example map（Ch 8、29） | PM、QA、RD 一起 | 三方 | 可以主持討論，不做決定 | 三方 |
| `features/*.feature` | QA 或 RD 執筆，PM 同意 | PM、QA、RD | 可以起草；實作期間不准改 | PM：diff 是他的語言 |
| step definitions、driver | RD 或 agent | RD | 產生、維護 | 只有讀 Python 的人 |
| 單元測試 | RD 或 agent，和實作一起寫 | RD | 產生、維護 | 只有讀 Python 的人 |
| 活文件 | 機器，每次執行重產 | 所有人 | 不該手改 | 下一次執行就覆蓋 |

關鍵在第四欄。TDD 的測試和實作由同一隻手在同一個迴圈裡輪流寫，RED 階段它必須能改測試；要擋它在 GREEN 階段回頭改測試，得像 [Ch 20](./20-tdd-with-agents.md) 那樣用階段檔把每一輪切開，而 RED 階段新寫的測試仍然只有讀得懂 Python 的人能審。feature 檔在自動化開始前就談好了，整段實作期間都可以凍結，審它的是行為的主人。

實驗：agent 改程式時把半開區間寫成閉區間（`<` 變 `<=`），相鄰時段開始被拒絕。接著假設它被要求「讓測試通過」卻沒去修程式。(a)、(b)、(c) 三個改法是我們手動做的，模擬 agent 被要求讓測試通過時會走的捷徑（弱化斷言這一類在 Ch 20 有系統整理），不是 agent 的實際輸出（`perl -pi -e` 在 macOS 和 Linux 上都能原地改檔，兩邊的 `sed -i` 語法不同；貼進互動式 zsh 前先 `setopt interactivecomments`，否則三行 `#` 註解會被當成指令，印出 `zsh: number expected` 這類錯誤，其餘指令照跑）：

```bash
perl -pi -e 's/start < b\.end and b\.start < end/start <= b.end and b.start <= end/' booking/schedule.py
pytest -q tests --tb=line 2>&1 | grep -E "^E |^FAILED|passed|failed"
# (a) 把單元測試的輸入挪離邊界
perl -pi -e 's/time\(11\), time\(12\), "Alice"/time(11, 1), time(12), "Alice"/' tests/unit/test_schedule.py
git diff -U0 tests/unit/test_schedule.py | tail -2; pytest -q tests/unit | tail -1
git checkout -q tests/unit/test_schedule.py
# (b) 把情境的輸入挪離邊界
perl -pi -e 's/from 11:00 to 12:00/from 11:01 to 12:00/' features/booking.feature
git diff -U0 features/booking.feature | tail -2; pytest -q tests/acceptance | tail -1
git checkout -q features/booking.feature
# (c) 把 Then 掏空，再看活文件怎麼說
perl -pi -e 's/assert result == \("confirmed", None\)/assert result/' tests/acceptance/test_booking.py
git diff -U0 tests/acceptance/test_booking.py | tail -2; pytest -q tests/acceptance --cucumberjson=reports/cheat.json | tail -1
python living_doc.py reports/cheat.json 2>/dev/null | grep Back-to-back
git checkout -q .
```

```
E   AssertionError: assert ('rejected', ...11:00 by Bob') == ('confirmed', None)
E   booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob
FAILED tests/acceptance/test_booking.py::test_backtoback_slots_do_not_overlap
FAILED tests/unit/test_schedule.py::test_back_to_back_slots_do_not_overlap - ...
2 failed, 6 passed in 0.01s
-    assert schedule.book("Taipei", time(11), time(12), "Alice").by == "Alice"
+    assert schedule.book("Taipei", time(11, 1), time(12), "Alice").by == "Alice"
4 passed in 0.01s
-      When Alice books "Taipei" from 11:00 to 12:00
+      When Alice books "Taipei" from 11:01 to 12:00
4 passed in 0.01s
-def confirmed(result): assert result == ("confirmed", None)
+def confirmed(result): assert result
4 passed in 0.01s
- [PASS] Back-to-back slots do not overlap @sc_book_02
```

壞版本兩疊都抓到了，紅的原因也都對：一邊是 Then 的斷言，一邊是不該出現的 `Conflict`。接著三個一行的 diff，bug 都還在，全部綠燈。**(a) 和 (b) 是同一種作弊**（把輸入挪離邊界），差別不在語法，在位置與讀者。(a) 落在 agent 本來就要寫的目錄，審的人得看出 `time(11, 1)` 為什麼不對；(b) 落在 PM 的檔案裡，標題寫 Back-to-back、When 卻寫 11:01，讀需求的人一眼看得出矛盾。更要緊的是 (b) 能被機器擋下：[Ch 27](./27-ai-bdd-loop.md) 用 PreToolUse hook 在實作期間拒絕經 Edit、Write 工具對 `features/` 的編輯，繞過工具的改法（例如 shell 的 `sed`）由簽核時記下的 hash 在收工時抓到；也可以用 CODEOWNERS 規定 `features/` 的變更要 PM 核准（[Ch 6](./06-workflows-are-grown.md) 用同一招審 `.claude/` 的變更，也說明了要開「Require review from Code Owners」才算強制；本章沒有在 GitHub 上實測）。

**(c) 是 feature 檔守不住的洞**。合約一字未改，被掏空的是 agent 自己寫的量測工具：`assert result` 對任何非空 tuple 都成立。活文件寫著「Back-to-back slots do not overlap：PASS」，程式實際上拒絕；這次 `--junit` 也救不了，因為 pytest 自己也說 passed。抓得到它的只有兩種東西：把 step definitions 當程式碼審，以及**拿一個已知的壞版本去跑，要求套件變紅**。剛才的 `<=` 就是這樣的壞版本：誠實的 step 讓它紅，掏空的 step 讓它活下來。這是 [pstack Ch 14](../pstack/14-verification-principles.md)「四、Test Behavior, Not Implementation」裡「Break the behavior」那一刀；[Ch 32](./32-brownfield-bdd.md) 會把它做成 `mutate.py`。所以「feature 檔比單元測試更耐用」的精確說法是：它和實作分開、可以凍結、由行為的主人審，讓 (a)、(b) 這類改動從藏在程式碼裡變成攤在需求文件上；但它的可信度上限是 step definitions 的誠實度，而 step definitions 仍是 agent 寫的程式碼。

## 底層機制：pytest-bdd 怎麼把一句英文變成一次函式呼叫

```
.feature ─gherkin-official─► GherkinDocument ─FeatureParser─► Feature.scenarios {標題: 模板}
   ─scenarios()，收集時─► test_<標題>()；Outline → parametrize；tag → pytest mark
   ─執行時逐步─► 在 fixture 裡找 step definition（型別相同、parser 比對整句）→ 呼叫 → target_fixture
   ─reporting.py 的 hooks─► cucumber_json.py ─► reports/*.json
```

四個從 `site-packages/pytest_bdd/` 原始碼讀出、會直接影響寫法的事實：

1. **step definition 是 fixture**。`@given(...)` 在你的模組裡登記一個名為 `pytestbdd_stepdef_given_<文字>` 的 fixture（`steps.py`），可見範圍照 fixture 規則走：寫在某個測試模組的步驟，別的模組看不到（實測：另開一個測試模組綁同一個資料夾，四個情境全部 `StepDefinitionNotFoundError`），要共用就放進 `conftest.py`。
2. **比對看型別**。`find_fixturedefs_for_step` 先濾掉型別不同的定義，再讓 parser 比對文字；`And`、`But`、`*` 沿用前一步的型別（`parser.py` 的 `parse_steps`）。[Cucumber 的 Gherkin Reference](https://cucumber.io/docs/gherkin/reference/) 是相反的規則（它也是 `Rule` 自 Gherkin 6 起成為關鍵字的出處）：「Keywords are not taken into account when looking for a step definition」。同一句話寫在 Given 和 When 底下，在 Cucumber 是同一步，在 pytest-bdd 是兩步；`@step(...)` 不帶型別，可以接任何一種。
3. **情境以標題為 key**。`feature.scenarios[scenario.name] = scenario`：同一個檔案裡兩個情境同名，後一個蓋掉前一個，不報錯。測試函式名也從標題來，改標題等於改測試 ID。
4. **報告只記錄跑到的步驟**。步驟的紀錄在 `before_step` 才建立；找不到定義時觸發的是 `pytest_bdd_step_func_lookup_error`，外掛沒替它接上任何記錄，所以那一步和它後面的步驟都不存在；它若是第一步，情境一筆步驟紀錄都沒有，`cucumber_json.py` 就把整個情境跳過。某一步失敗時，`ScenarioReport.fail()` 把**剩下所有步驟**也標成 failed，只有第一個帶錯誤訊息。對照 [Cucumber 的 API 文件](https://cucumber.io/docs/cucumber/api/)：找不到定義的那一步是 undefined，失敗或未定義之後的步驟是 skipped；在 pytest-bdd 的 JSON 裡，失敗之後的步驟變成 failed，未定義的那一步則連同後面的步驟一起消失（實測：在第一個情境的 Given 後面插一步沒有定義的 `And Carol is watching`，JSON 裡那個情境只剩 Given 一步，而且是 passed）。

對照 `cucumber_json.py` 的型別定義和實際輸出，pytest-bdd 9.0.1 的 cucumber JSON 也**沒有**：Rule 與 Rule 的 tag、Examples 的 tag、步驟底下的 data table 與 doc string、情境層級的狀態；`match.location` 一律是空字串。從這種輸入產生活文件，原則只能是：狀態從執行結果拿，而且要有第二個來源；文字從 `.feature` 拿（動手練習第 3 題）。

## Gherkin 關鍵字巡禮：pytest-bdd 9 的實測支援表

`booking.feature` 用到了 Feature 與描述、Rule、Scenario、Scenario Outline、Examples，以及 Rule 與 Scenario 上的 tag。Background、data table、doc string 和 Feature 層的 tag 放進第二個 feature。R2 寫成 Outline、agenda 那份不分 `Rule:` 又加上 Background，都是為了把語法走一遍，不是寫作範本：[Ch 28](./28-gherkin-dsl-style.md) 的 `gherkin_lint.py`（`--glossary` 指向該章那份）對這兩個檔回 `2 files, 3 problems`（GH-06：agenda 的 Background 只服務一個情境；GH-11：agenda 情境沒掛在 `Rule:` 底下；GH-07：R2 Outline 的 `end` 欄每列都是 10:00）。agenda 的結果到了 [Ch 30](./30-one-gherkin-two-drivers.md) 與 Final Project 寫成 `Then the agenda of "Taipei" is` 加一張資料表，這裡用 doc string 只為了示範它：

```gherkin
# features/agenda.feature
@agenda
Feature: Daily agenda

  Background:
    Given these bookings exist:
      | room   | start | end   | by    |
      | Taipei | 10:00 | 11:00 | Bob   |
      | Taipei | 14:00 | 15:00 | Carol |

  @sc_agenda_01
  Scenario: The agenda lists bookings in time order
    When Alice books "Taipei" from 11:00 to 12:00
    Then the agenda for "Taipei" reads:
      """
      10:00-11:00 Bob
      11:00-12:00 Alice
      14:00-15:00 Carol
      """
```

兩個新步驟接到 step definitions 尾端。`datatable` 和 `docstring` 是保留的參數名，pytest-bdd 直接注入表格（list of lists，第一列就是表頭那一列）和文字區塊（一個字串）。新 tag 先不登記，看看會怎樣：

```bash
cat >> tests/acceptance/test_booking.py <<'EOF'

@given("these bookings exist:")
def these_bookings_exist(driver, datatable):
    header, *rows = datatable
    for row in rows:
        b = dict(zip(header, row))
        assert driver.book(b["room"], b["start"], b["end"], b["by"]) == ("confirmed", None)

@then(parsers.parse('the agenda for "{room}" reads:'))
def agenda_reads(driver, room, docstring): assert driver.agenda(room) == docstring.splitlines()
EOF
pytest tests -q 2>&1 | grep -oE "Unknown pytest.mark.[a-z_0-9]+|[0-9]+ passed.*" | sort
pytest tests -q --strict-markers > reports/strict.txt 2>&1; echo "exit=$?"; grep -m1 "not found" reports/strict.txt
printf '    agenda: feature tag\n    sc_agenda_01: scenario id\n' >> pytest.ini
pytest tests -q --strict-markers -m agenda | tail -1
```

```
9 passed, 2 warnings in 0.02s
Unknown pytest.mark.agenda
Unknown pytest.mark.sc_agenda_01
exit=2
'agenda' not found in `markers` configuration option
1 passed, 8 deselected in 0.01s
```

沒登記的 tag 平常只是警告，開了 `--strict-markers` 就在收集階段中斷（exit 2）。tag 存在 Python 的 set 裡，字串雜湊每個行程重新隨機，所以警告的先後和 `--strict-markers` 點名哪一個，每次執行可能不同（警告因此先 `sort`；固定 `PYTHONHASHSEED` 就固定下來，我們換了幾個值看到兩種結果）。`Example:` 是 `Scenario:` 的同義詞（換掉照樣 `4 passed`）。其餘元素的實測結果，以及對 `booking.feature` 做小改動的邊界探針（每列在一份 `git archive` 匯出的拷貝裡改一處、跑一次；引號內是輸出原文）：

| 元素或改動 | pytest-bdd 9.0.1 的實際反應 |
|---|---|
| `Rule`（Gherkin 6 起）與 `Background` | 支援；Rule 的 tag 套到底下每個情境，JSON 沒有 Rule 這一層；Feature 層與 Rule 層各可有一個 Background（`parser.py` 的 `_extract_rule_background`），依序接在每個情境前 |
| 把 `Back-to-back` 那個情境改成和第一個同名 | `3 passed`，沒有警告；`test_booking_an_overlapping_slot` 跑的是**後一個**情境的步驟 |
| 第一個情境的 `Given` 換成 `*`；另一份拷貝改把 `Then` 換成 `*` | 前者收集時中斷：`StepError: First step in a scenario or background must start with 'Given', 'When' or 'Then', but got *.`；後者 `Step definition is not found: When "the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob"`：`*` 繼承了 When |
| 第一個情境寫成 `Given Bob books ...`（借用 When 的句子） | `Step definition is not found: Given "Bob books "Taipei" from 10:00 to 11:00"` |
| 在第一個情境後面插入 `Background:` | gherkin parser 回報 `(15:5)` 處 `got 'Background:'`；pytest-bdd 翻成 `BackgroundError: Multiple 'Background' sections detected. Only one 'Background' is allowed per feature.`，可是檔案裡只有一個 |
| Outline 寫 `<person>`，Examples 沒有這一欄 | `4 passed`；渲染後是 `When <person> books "Tokyo" from 10:00 to 10:00`，`{who}` 吃下了字面上的 `<person>` |
| tag 寫成 `@sc-book-01` 並照樣登記 | 能用：`--strict-markers -m "sc-book-01 or r2"` 得到 `3 passed, 5 deselected` |

最後一列和常見說法不同：pytest 9.2.0 的 `-m` 文法允許連字號（`_pytest/mark/expression.py` 的 `ident: (\w|:|\+|-|\.|\[|\]|\\|/)+`）。本課仍用底線，理由是 Python 程式裡可以直接寫 `pytest.mark.sc_book_01`，連字號只能靠 `getattr`。

## 對比與取捨

| 做法 | 誰讀得懂 | 主要成本 | 適用情境 |
|---|---|---|---|
| TDD 單元測試 | RD | 低，跟著程式一起寫 | 領域規則的邊界、演算法、重構安全網 |
| 不用 Gherkin 的驗收測試（好名字的 pytest 加 driver） | RD；名字取得好時 QA 勉強讀得懂 | 中，少一層翻譯 | 業務方不讀測試的團隊、純技術 API |
| Gherkin 加 step definitions | PM、QA、RD | 高，多一層要維護、也會被掏空的程式 | PM 真的會讀會審；需要一份可凍結的 agent 合約 |
| 再加上活文件 | 任何人 | 一支腳本加上 CI 每次重產 | 業務方要看「系統此刻做到什麼」 |

為什麼不全部用第二列？好名字的 pytest 驗收測試加 driver，已經拿到 BDD 自動化那一半的大部分好處，還少一層翻譯。Gherkin 值得它的成本只在兩種情況：有不寫程式的人真的會讀、會改、會核准這些句子；或者你需要一份和實作分開、能在實作期間凍結的檔案來約束 agent。兩者都不成立時，它只是比較囉嗦的測試，也就是 SDD Ch 10 踩雷集錦第 4 條說的「得到的是語法，不是 BDD 的實質」。

## 踩雷集錦

1. **錯誤直覺：BDD 就是把測試改寫成 Given/When/Then → 正確認識：** 三個實踐裡只有自動化落在測試上；沒有發現與表述，feature 檔只是一種格式。檢查方法：問「這份 feature 檔上一次是誰審的？」答案只有工程師，它就是第二列的測試穿了 Gherkin 的衣服。
2. **錯誤直覺：feature 檔沒被改過，行為就有保障 → 正確認識：** 實測 (c) 裡 feature 一字未改、活文件寫 PASS、程式拒絕相鄰預約。把 step definitions 當程式碼審，並保留至少一個已知的壞版本，要求套件在它上面變紅。
3. **錯誤直覺：cucumber JSON 全綠就是全綠 → 正確認識：** 有一步沒有 step definition 的情境，在 JSON 裡只剩跑到的步驟、全部 passed，第一步就沒有定義的連情境都不見；失敗之後的步驟則被標成 failed 而不是 skipped。活文件要拿 JUnit XML 或 pytest 的 exit code 交叉比對，也要列出「JUnit 說失敗、JSON 裡卻沒有」的測試。
4. **錯誤直覺：情境標題只是給人看的 → 正確認識：** 在 pytest-bdd 裡標題是 key，也是測試函式名。同名情境無聲覆蓋（實測 4 個變 3 個），改標題就改了測試 ID。引用一律用 `@sc_` tag；CI 加一行 `grep -hE "^\s*(Scenario|Example|Scenario Outline):" features/*.feature | sed 's/^ *//' | sort | uniq -d`，有輸出就是有重複（在同名的那份拷貝上實測印出 `Scenario: Booking an overlapping slot`）。
5. **錯誤直覺：tag 打錯頂多跑少一點 → 正確認識：** 不開 `--strict-markers` 時，`pytest -m "sc_book_1 or r1"`（把 `sc_book_01` 打錯）得到 `2 passed, 7 deselected`、exit 0，打錯的那個情境從頭到尾沒跑，CI 照樣綠。開了之後同一個指令 exit 4：`ERROR: Unknown marker(s) in '-m' expression: sc_book_1.`。在 CI 開 `--strict-markers`，並把 `pytest.ini` 的 markers 清單當成 feature 檔的一部分一起審。

## 進階：活文件的格式，legacy JSON 與 Cucumber Messages

pytest-bdd 9.0.1 只會寫 cucumber JSON：`grep -rlE "ndjson|messages"` 掃過整個 `site-packages/pytest_bdd/` 沒有任何命中（exit 1）。Cucumber 那一邊已經往前走了：把 Cucumber Messages 轉成 JSON 的 [cucumber/json-formatter](https://github.com/cucumber/json-formatter) 在 2025-07-21 封存，README 寫著「If you are the maintainer of a tool that consumes the legacy Cucumber JSON format you should consider updating your tool to consume Cucumber Messages instead.」官方的 [html-formatter](https://github.com/cucumber/html-formatter) 則「works with _any_ Cucumber implementation with a `message` formatter that outputs cucumber messages」。也就是說，Cucumber 生態系的報告工具吃 Messages（NDJSON 事件流），pytest-bdd 給的是被稱作 legacy 的 JSON，兩邊不能直接接上；中間的轉換 pytest-bdd 9.0.1 本身沒有提供，就我們查到的是如此，請以你安裝的版本為準。不論格式怎麼變，本章的原則都成立：狀態從執行結果拿、要有第二個來源，文字從 `.feature` 拿。給 agent 的活文件也不需要 HTML：agent 要的是把 `features/` 當成「現在的事實」來讀（[Ch 33](./33-truth-vs-plan.md) 的 truth），再加上機器判讀得了的狀態，也就是 pytest 的 exit code 與 JUnit XML。Markdown 和 HTML 是給人的。

## 動手練習

1. **R3 進兩疊**：替 R3（辦公時間 08:00–20:00，訊息 `outside office hours 08:00-20:00`）各寫一份產物：`booking.feature` 加一個帶 `@r3` 的 Rule 和一個 Outline，`test_schedule.py` 加對應的單元測試。先不實作，跑一次看兩疊各怎麼紅，哪一邊的訊息 PM 讀得懂？單元測試 import 一個還不存在的例外類別時，它紅在哪個階段（[Ch 25](./25-tdd-in-sdd-pipeline.md) 的紅燈閘門專門處理這件事）？動手前先回答：19:00-20:00 算不算辦公時間內？
2. **找一個會活下來的壞版本**：把 R2 的 Then（`reversed_range`）改成只檢查 `assert result[0] == "rejected"`，再找一個讓它活下來、卻會被原本誠實版本抓到的 `schedule.py` 修改。提示：拒絕了，但理由說錯了。改回誠實版本，確認同一個壞版本讓套件變紅。
3. **把文字從 `.feature` 補回來**：讓 `living_doc.py` 依每個情境和每一步的 `line`，回 feature 檔找上方最近的 `Rule:` 當小標題，並把步驟底下的 data table 或 doc string 縮排印出。完成後 agenda 那一段的 `these bookings exist:` 底下會出現三列表格，`reads:` 底下出現三行時段。

## 本章重點整理

- BDD 有三個實踐：發現、表述、自動化。TDD 的產物全落在自動化；BDD 多出 example map 與 feature 檔，讀者是不寫程式的人。
- 同一組規則寫成兩疊：單元測試驗類別的契約；feature 加 step definitions 加 driver 驗行為，driver 是唯一知道怎麼碰系統的地方。
- feature 檔是比單元測試更耐用的 agent 合約，因為它和實作分開、能凍結、由行為的主人審；它的可信度上限是 step definitions 的誠實度，要靠審查和已知的壞版本守住。
- pytest-bdd 的 step definition 是 fixture，比對看型別，情境以標題為 key，報告只記錄跑到的步驟；它的 cucumber JSON 沒有 Rule、Examples tag、表格與 doc string，也看不到未定義的步驟；第一步就未定義的情境整個不在裡面，只有 JUnit XML 和 exit code 知道它失敗了。

## 自我檢核

- [ ] 不看本章，說出 BDD 三個實踐各自留下的產物，以及 TDD 落在哪一個。
- [ ] 三個「讓測試變綠」的捷徑裡，哪一個能用 hook 擋下、哪一個只有已知的壞版本抓得到？為什麼 (a) 很難用同一招擋？
- [ ] 一個情境裡有一步沒有 step definition，pytest、cucumber JSON、只讀 JSON 的活文件各會說什麼？那一步若是第一步，答案哪裡不同？
- [ ] `*` 在 pytest-bdd 裡為什麼不能當第一步？放在 When 後面又會怎樣？Cucumber 為什麼沒有這個問題？
- [ ] 你的團隊如果沒有人會讀 feature 檔，你會選對比表的哪一列？說出你因此放棄了什麼。

## 延伸閱讀

- **[Dan North — Introducing BDD](https://dannorth.net/blog/introducing-bdd/)**（原刊 *Better Software*，2006 年 3 月）
  - **讀哪裡**：全文不長；重點是「Test method names should be sentences」、「"Behaviour" is a more useful word than "test"」與「Acceptance criteria should be executable」三節。
  - **學到什麼**：BDD 是從教 TDD 的困擾長出來的；Given/When/Then 原本是分析的語言，不是測試框架的語法。
  - **和本章的關聯**：本章「TDD 的產物全是程式碼、BDD 多出給非工程師讀的產物」就是從這篇的出發點推下去的。
- **[Cucumber — Behaviour-Driven Development](https://cucumber.io/docs/bdd/)** 與 **[Seb Rose、Gáspár Nagy — The BDD Books](https://bddbooks.com/)**
  - **讀哪裡**：Cucumber 頁面的 Discovery、Formulation、Automation 三段；BDD Books 網站的系列介紹（舊版 Leanpub 兩冊已停售，內容將併入 Manning 自 2025 年 10 月起以 MEAP 連載、預定 2027 年出版的《Effective Behavior-Driven Development》）。
  - **學到什麼**：三個實踐的官方說法，以及把表述（formulation）當成一門獨立技藝的理由。
  - **和本章的關聯**：「BDD 不是一種測試寫法」那張表的出處；發現與表述在 Ch 29 和 Ch 28 展開。
- **[pytest-bdd 官方文件](https://pytest-bdd.readthedocs.io/en/stable/)**（9.0.1）
  - **讀哪裡**：「Using Asterisks in Place of Keywords」、「Rules」、「Datatables」、「Docstrings」、「Organizing your scenarios」、「Reporting」。
  - **學到什麼**：本章實測過的行為的官方說法；「Test code generation helpers」一節的 `--generate-missing` 是下一章的起點。
  - **和本章的關聯**：支援表的對照基準。文件沒寫的部分（同名情境覆蓋、JSON 遺漏未定義的步驟、`*` 不能當第一步）是本章讀原始碼與實測得到的。

本章的 step definitions 是手寫的，三疊產物也是一次寫好的。下一章把順序交給 agent：先有 feature，跑出未定義的步驟，用 `--generate-missing` 產生骨架，紅燈要紅對原因，再實作到綠，同時用 hook 把 `features/` 鎖在 agent 碰不到的地方。

→ [Ch 27 AI x BDD 迴圈與 bdd skill：先寫可執行規格，再寫 step definitions](./27-ai-bdd-loop.md)
