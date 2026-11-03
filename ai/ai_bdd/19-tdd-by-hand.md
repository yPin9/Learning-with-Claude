# Ch 19 — 手打一遍 TDD：紅綠重構的會議室 kata

> **目標**：從空目錄手打一遍測試驅動開發（Test-Driven Development，TDD）：先列測試清單，每次只把一項變成測試，確認它因為**對的原因**變紅，用假實作、三角定位、明顯實作三種方式變綠，再在綠燈上做一次真的重構。7 個循環加一次重構，各一個 commit，這 8 個 commit 每個都是綠的。你也會看到 pytest 怎麼分辨「斷言失敗」和「根本沒跑到」，知道測試層級與測試替身怎麼選，以及 TDD 受過哪些批評、什麼時候不該用它。

> **環境**：Python 3.12.15、pytest 9.2.0（[Ch 0](./00-environment-setup.md) 建好的 `/tmp/lab/aibdd/.venv`）、git 2.54.0、macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch19-tdd-kata/`。本章沒有 agent，所有指令與輸出都是 2026-11-02 實際跑出來的，長輸出只留關鍵行；你的 commit hash 會因作者與時間不同而不一樣。

## 為什麼需要這個？

在 TDD 普及之前，常見的做法是先把程式寫完，再由同一個人或另一個團隊補測試。事後補的測試有一個結構性的毛病：它照著已經存在的程式寫，很容易變成「確認程式做了它做的事」。[Ch 2](./02-vibe-coding-vs-engineering.md) 那個從實作推出來的 smoke test 就是這樣，在錯的版本上全綠。

Gergely Orosz 在 [The Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/how-kent-beck-shapes-the-software)（2026-07）的整理說，TDD 與其說是 Kent Beck 發明的，不如說是他「rediscovered」的：Beck 小時候讀父親的一本程式設計書，書裡建議拿一卷真實的輸入磁帶，先**手打出你期待的輸出磁帶**，再寫程式直到實際輸出和它一致；多年後他寫 SUnit 時想起這招，把它搬進測試框架。TDD 怎麼在 XP 裡成形、和 SDD 差在哪裡，[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md) 的「TDD：顛倒的 SDD？」講過，這裡不重複。

這個 repo 對 TDD 的覆蓋是零碎的。[SDD Ch 2](../spec_driven_development/02-sdd-ddd-bdd-tdd-map.md) 的「TDD：用測試驅動實作」有一段沒跑過的購物車範例，重構那一步是一行註解；[pstack Ch 8](../pstack/08-bug-fix-playbook.md) 手動跑了一次修 bug 的紅到綠，沒有重構；[pstack Ch 9](../pstack/09-feature-refactoring.md) 的「Step 5：小步移動，每步都讓 pin 保持綠」只做重構。**沒有一處在新功能上把整個迴圈從頭跑完**，本章補的就是這一塊。

AI 時代還要手打？要。[Ch 20](./20-tdd-with-agents.md) 會把紅、綠、重構分給 agent，用 hook 擋住它在紅燈時改程式、在綠燈時改測試。閘門設在哪裡、紅的原因對不對、「剛好夠的程式」長什麼樣，你得自己做過一次才審得出來。

## 先建立直覺

**棘輪扳手**。鎖一排螺絲時，棘輪讓你每次只轉一小格，手放開也不會倒退。TDD 的每個綠燈就是一格：測試把已經做到的行為卡住，下一步做壞了只會退回上一格，不會退回原點。螺絲清單就是測試清單（test list）：開工前先寫下要鎖哪些，鎖的過程中發現漏了就加上去。

這個比喻有一處不成立：棘輪不知道你鎖的是不是對的螺絲。TDD 也不知道需求對不對，它只保證程式符合你寫下的例子，SDD Ch 2 的說法是「TDD 會讓你把錯誤的需求實作得非常正確」。例子本身對不對，是 [Ch 8](./08-translation-sandwich.md) 的 Example Mapping 與 Part 5 的 BDD 要處理的事。

```
  TESTLIST.md ──挑一項──▶ 寫一個測試 ──跑──▶ 紅？ ──否──▶ 先懷疑測試（C6）
       ▲                                     │是
       │                                     ▼
       │                              紅的原因對嗎？ ──否──▶ 修環境或測試（踩雷 1）
       │                                     │是
       │                                     ▼
       │                         寫剛好夠的程式 ──跑──▶ 全綠
       │                                                 │
       │   途中發現新情況就加進清單                       ▼
       └──── commit ◀──跑：全綠── 重構（只在綠燈上；可略過）
```

三個閘門各擋一種錯：「紅的原因對嗎」擋住環境問題假裝成紅燈；「剛好夠的程式」擋住沒有測試要求的程式碼；「只在綠燈上重構」讓重構途中出現的紅燈一定是重構造成的。

## 規則：一張清單、一個循環、三條定律

Beck 在〈[Canon TDD](https://newsletter.kentbeck.com/p/canon-tdd)〉（2023-12-11）把流程寫成五步：

1. 「Write a list of the test scenarios you want to cover」
2. 「Turn exactly one item on the list into an actual, concrete, runnable test」
3. 「Change the code to make the test (& all previous tests) pass (adding items to the list as you discover them)」
4. 「Optionally refactor to improve the implementation design」
5. 「Until the list is empty, go back to #2」

Robert C. Martin 在〈[The Cycles of TDD](http://blog.cleancoder.com/uncle-bob/2014/12/17/TheCyclesOfTDD.html)〉（2014-12-17）把順序壓成三條定律：

1. 「You must write a failing test before you write any production code.」
2. 「You must not write more of a test than is sufficient to fail, or fail to compile.」
3. 「You must not write more production code than is sufficient to make the currently failing test pass.」

第二條的「fail to compile」在 Python 裡對應 import 失敗：測試引用一個還不存在的名字就已經算紅，該停筆去補產品程式碼。他估計三條定律的循環以秒計，一輪紅、綠、重構「on the order of a minute or so」。重構（refactoring）採 Martin Fowler 的定義：「a change made to the internal structure of software to make it easier to understand and cheaper to modify without changing its observable behavior」（[DefinitionOfRefactoring](https://martinfowler.com/bliki/DefinitionOfRefactoring.html)）。可觀察的行為不變，所以重構前後同一組測試都要綠。

變綠有三種做法，名字來自 Beck《Test-Driven Development: By Example》第三部的 Green Bar Patterns：

| 做法 | 做什麼 | 什麼時候用 | 本章哪幾輪 |
|---|---|---|---|
| 假實作（Fake It） | 回傳寫死的常數，先求綠 | 還看不出一般解，或想先確認測試本身會綠 | C1、C3 |
| 三角定位（Triangulate） | 加第二個例子，逼出一般解 | 一個例子不足以決定抽象 | C2、C4、C5 |
| 明顯實作（Obvious Implementation） | 直接寫出你有把握的解 | 解法一眼可見；意外變紅就退回假實作 | C7 |

Canon TDD 也列了每一步的常見錯誤，本章會碰到其中三個：一口氣「convert all the items on the Test List into concrete tests」、「mixing refactoring into making the test pass」，以及「copying actual, computed values & pasting them into the expected values of the test」（期望值從程式抄來，測試就只能證明程式和自己一致）。

## Kata 準備：空目錄、git、測試清單

```bash
mkdir -p /tmp/lab/aibdd/ch19-tdd-kata && cd /tmp/lab/aibdd/ch19-tdd-kata
source /tmp/lab/aibdd/.venv/bin/activate
git init -q
git config user.name kata && git config user.email kata@example.invalid   # 這台機器沒有全域 git 身分；你有的話可略過
mkdir booking tests && touch booking/__init__.py
printf '[pytest]\npythonpath = .\ntestpaths = tests\n' > pytest.ini
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
```

`pythonpath = .` 不能省。pytest 預設的 import 模式把測試檔所在的 `tests/` 放進 `sys.path`，不放專案根目錄；少了這行，`import booking` 會失敗，而且失敗的樣子和「程式還沒寫」幾乎一樣（踩雷 1）。

測試清單 `TESTLIST.md` 在開工前寫，寫**行為**，不寫實作；Canon TDD 對這一步的提醒是別「mixing in implementation design decisions」。R1、R2 與半開區間 `[start, end)` 是全課共用的規則（見 [Ch 1](./01-course-map.md)），取消預約（R4）留給[練習 D](./practice-d-cancel-booking.md)：

```markdown
# Test list: Schedule.book

- [ ] 空的會議室可以訂：Bob 訂 Taipei 10:00-11:00，拿到 id 1
- [ ] 每筆預約拿到下一個 id
- [ ] R1 同一間、時段重疊 → Conflict "Taipei is taken 10:00-11:00 by Bob"
- [ ] 相鄰時段可以訂：10:00-11:00 之後的 11:00-12:00
- [ ] 不同會議室同一時段互不影響
- [ ] R2 結束不晚於開始 → InvalidRange "end must be after start"
```

`git add -A && git commit -qm "chore: project skeleton and test list"` 之後，`git log --oneline` 是 `e5aca0a chore: project skeleton and test list`；這時 `pytest -q` 印 `no tests ran in 0.00s`，exit 5，也就是「沒有收集到任何測試」。底層機制一節會把本章遇到的 exit code 整理在一起。

## C1–C2：先假實作，再三角定位

**C1 紅**。`tests/test_schedule.py` 的第一版。介面在這裡第一次被決定，而且是由使用端決定的：`Schedule().book(room, start, end, by)` 回傳一個能整筆比較的 `Booking`，時間用 `datetime.time` 而不是字串（Ch 2 的 B2 就是 `"9:30" <= "11:00"` 這種字串比較）：

```python
from datetime import time

from booking.schedule import Booking, Schedule


def t(hhmm):
    return time.fromisoformat(hhmm)


def test_free_room_is_booked():
    schedule = Schedule()

    booking = schedule.book("Taipei", t("10:00"), t("11:00"), "Bob")

    assert booking == Booking(1, "Taipei", t("10:00"), t("11:00"), "Bob")
```

每個測試分三段、用空行隔開：準備、動作、檢查，[Ch 21](./21-tdd-skill-aaa.md) 會給它名字（Arrange-Act-Assert）並寫成 lint。第一次 `pytest -q` 是收集錯誤：`E   ModuleNotFoundError: No module named 'booking.schedule'`，接著 `Interrupted: 1 error during collection`、`1 error in 0.03s`，exit 2。這是第二條定律的「fail to compile」：紅了，但還沒有任何行為被檢查。停筆，補上剛好讓它 import 得到的骨架 `booking/schedule.py`：

```python
from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Booking:
    id: int
    room: str
    start: time
    end: time
    by: str


class Schedule:
    def book(self, room, start, end, by):
        return None
```

`frozen=True` 讓 `Booking` 不可變；dataclass 自動產生的 `==` 讓測試能整筆比較，不必逐欄斷言。再跑一次，這次是**斷言**紅了：

```
$ pytest -q; echo "exit=$?"
…（略）
E       AssertionError: assert None == Booking(id=1, room='Taipei', start=datetime.time(10, 0), end=datetime.time(11, 0), by='Bob')
…（略）
E        +    where datetime.time(10, 0) = t('10:00')
E        +    and   datetime.time(11, 0) = t('11:00')
…（略）
1 failed in 0.01s
exit=1
```

**C1 綠：假實作**。把 `return None` 換成寫死的答案 `return Booking(1, "Taipei", time(10, 0), time(11, 0), "Bob")`，結果 `1 passed in 0.00s`、exit 0。看起來像作弊，用處在於先確認測試本身會綠、介面拼得起來，再讓下一個測試逼出真正的邏輯。勾掉清單第一項，`git add -A && git commit -qm "C1 free room is booked (fake it)"`；之後每一輪都這樣 commit，只寫訊息。

**C2 紅**。第二個例子：

```python
def test_each_booking_gets_the_next_id():
    schedule = Schedule()
    schedule.book("Taipei", t("10:00"), t("11:00"), "Bob")

    second = schedule.book("Tokyo", t("14:00"), t("15:00"), "Alice")

    assert second == Booking(2, "Tokyo", t("14:00"), t("15:00"), "Alice")
```

紅燈是 `E       AssertionError: assert Booking(id=1,... 0), by='Bob') == Booking(id=2,...), by='Alice')`，下面列出不同的欄位 `['id', 'room', 'start', 'end', 'by']`，摘要 `1 failed, 1 passed in 0.01s`。

**C2 綠：三角定位**。兩個例子並排，寫死的常數撐不住，一般解自己浮現：`Schedule` 加一個 `__init__` 設 `self._last_id = 0`，`book` 先 `self._last_id += 1`，再 `return Booking(self._last_id, room, start, end, by)`。`2 passed in 0.00s`，commit `C2 ids count up (triangulate)`。C2 的第二筆刻意選了**不同會議室、不同時段**，C3 會用到這個選擇。

## C3–C4：R1 衝突，先寫死訊息

**C3 紅**。測試檔開頭加上 `import pytest`，import 那行改成 `from booking.schedule import Booking, Conflict, Schedule`：

```python
def test_overlapping_slot_is_rejected():
    schedule = Schedule()
    schedule.book("Taipei", t("10:00"), t("11:00"), "Bob")

    with pytest.raises(Conflict) as rejected:
        schedule.book("Taipei", t("10:30"), t("11:30"), "Alice")

    assert str(rejected.value) == "Taipei is taken 10:00-11:00 by Bob"
```

第一次跑又是收集錯誤：`ImportError: cannot import name 'Conflict' from 'booking.schedule'`，exit 2。注意這時連前兩個已經綠的測試**都沒有跑**，摘要只有 `1 error in 0.03s`。在 `schedule.py` 補上 `class BookingError(Exception)` 與 `class Conflict(BookingError)`（內容都是 `pass`）之後，紅的原因才變成行為：`E       Failed: DID NOT RAISE Conflict`，`1 failed, 2 passed in 0.01s`。

**C3 綠：能多假就多假，直到舊測試不准**。最假的寫法是「已經有預約就一律拒絕」，在 `book` 開頭加 `if self._last_id: raise Conflict("Taipei is taken 10:00-11:00 by Bob")`（分兩行寫）。`pytest -q --tb=line` 印 `FAILED tests/test_schedule.py::test_each_booking_gets_the_next_id - booking.s...`、`1 failed, 2 passed in 0.00s`，exit 1。新測試綠了，C2 卻紅了：Alice 訂的是 Tokyo，不該被拒。這就是 Canon TDD 第 3 步那個括號「(& all previous tests)」的作用：**舊測試決定你能假到什麼程度**。讓三個測試都綠、又最少的寫法，是補上「同一間會議室」這個條件，訊息照樣寫死；為此 `__init__` 多一個 `self._bookings = []`，`book` 在回傳前把新預約 append 進去：

```python
        if any(b.room == room for b in self._bookings):
            raise Conflict("Taipei is taken 10:00-11:00 by Bob")
```

`3 passed in 0.00s`。寫死的訊息不能出貨，所以在清單上加一項 `- [ ] R1 訊息點名真正佔用的人（C3 的訊息是寫死的）`，這就是「adding items to the list as you discover them」。commit `C3 R1 overlap is rejected (fake message)`。

**C4 紅：換一組人、房間、時間**：

```python
def test_rejection_names_whoever_holds_the_slot():
    schedule = Schedule()
    schedule.book("Tokyo", t("14:00"), t("15:00"), "Carol")

    with pytest.raises(Conflict) as rejected:
        schedule.book("Tokyo", t("14:30"), t("15:30"), "Alice")

    assert str(rejected.value) == "Tokyo is taken 14:00-15:00 by Carol"
```

紅燈是 `E       AssertionError: assert 'Taipei is ta...-11:00 by Bob' == 'Tokyo is tak...5:00 by Carol'`，底下的差異是 `E         - Tokyo is taken 14:00-15:00 by Carol` 與 `E         + Taipei is taken 10:00-11:00 by Bob` 兩行，`1 failed, 3 passed in 0.01s`、exit 1。

**C4 綠**：訊息改從佔用者那筆預約組出來；`{b.start:%H:%M}` 走 `time` 的格式化，印成 `10:00`：

```python
        for b in self._bookings:
            if b.room == room:
                raise Conflict(f"{b.room} is taken {b.start:%H:%M}-{b.end:%H:%M} by {b.by}")
```

`4 passed in 0.00s`，commit `C4 R1 message names the holder (triangulate)`。到這裡「重疊」其實還沒實作：同一間會議室的**任何**第二筆都會被拒。沒有測試要求更多，程式就不多寫。

## C5–C6：邊界，以及第一次就綠的測試

**C5 紅：相鄰時段**，全課最愛的邊界：

```python
def test_adjacent_slot_is_free():
    schedule = Schedule()
    schedule.book("Taipei", t("10:00"), t("11:00"), "Bob")

    booking = schedule.book("Taipei", t("11:00"), t("12:00"), "Alice")

    assert booking == Booking(2, "Taipei", t("11:00"), t("12:00"), "Alice")
```

紅燈是 `E               booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob`，`1 failed, 4 passed in 0.01s`。這次紅的不是 assert，而是程式丟出了不該丟的 `Conflict`。紅的原因仍然對：它就是「相鄰被誤擋」這個行為本身。先寫錯的版本：閉區間的重疊公式最順手，[Ch 2](./02-vibe-coding-vs-engineering.md) 那份沒人審過的 API 用的就是它，`if b.room == room and start <= b.end and b.start <= end:`。結果照樣紅，`pytest -q --tb=line` 印 `E   booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob`、`1 failed, 4 passed in 0.00s`。把兩個 `<=` 換成 `<`，也就是半開區間，才 `5 passed in 0.00s`。commit `C5 adjacent slots are free (half-open)`。

**C6：第一次跑就綠**。不同會議室、同一時段：

```python
def test_other_rooms_are_independent():
    schedule = Schedule()
    schedule.book("Taipei", t("10:00"), t("11:00"), "Bob")

    booking = schedule.book("Tokyo", t("10:00"), t("11:00"), "Alice")

    assert booking == Booking(2, "Tokyo", t("10:00"), t("11:00"), "Alice")
```

結果 `6 passed in 0.01s`。沒看過它紅，就不知道它會不會紅。手動做一次突變（mutation）：刪掉房間條件，看誰發現：

```bash
perl -pi -e 's/b\.room == room and //' booking/schedule.py
pytest -q --tb=line; echo "exit=$?"
pytest -q -k "not other_rooms"; echo "exit=$?"
git checkout -- booking/schedule.py
```

第一個 pytest 印 `FAILED tests/test_schedule.py::test_other_rooms_are_independent - booking.sch...`、`1 failed, 5 passed in 0.00s`、`exit=1`；第二個印 `5 passed, 1 deselected in 0.00s`、`exit=0`。只有新測試抓到；拿掉它，突變體在其餘 5 個測試下全綠。更值得注意的是時間點：在 C3，「沒有房間條件」的寫法會讓 C2 紅（前面跑過）；C5 加上時間比較之後，C2 那兩筆時段不重疊，房間條件就**沒有任何測試在守了**。一個測試守住哪些程式，會隨程式改變而變。C6 留下，commit 訊息寫明 `green at once, mutation-checked`。

## C7 與重構：明顯實作，然後替規則命名

**C7**。R2 的兩個例子用 `parametrize` 一次給，包含「相等」這個邊界。先在 `schedule.py` 補一個 `class InvalidRange(BookingError)`、加進測試的 import：

```python
@pytest.mark.parametrize("start, end", [("11:00", "10:00"), ("10:00", "10:00")])
def test_end_must_be_after_start(start, end):
    schedule = Schedule()

    with pytest.raises(InvalidRange) as rejected:
        schedule.book("Taipei", t(start), t(end), "Bob")

    assert str(rejected.value) == "end must be after start"
```

兩組參數都是 `Failed: DID NOT RAISE InvalidRange`，`2 failed, 6 passed in 0.02s`。這條規則的解一眼可見，用明顯實作：在 `book` 開頭加 `if end <= start: raise InvalidRange("end must be after start")`（分兩行寫），`8 passed in 0.01s`，commit `C7 R2 end must be after start (obvious implementation)`。

**重構**。清單清空了，看一眼程式：R1 的核心規則 `start < b.end and b.start < end` 是一段沒有名字的比較式，和房間條件擠在同一個 `if` 裡，而它正是 C5 寫錯過一次的那行。把它抽成 `Booking.overlaps`（Fowler 重構目錄的 [Extract Function](https://refactoring.com/catalog/extractFunction.html)，舊名 Extract Method），把半開區間的決定寫進 docstring，迴圈變數 `b` 改名 `existing`。改到一半手滑，把 `b.` 換成 `self.` 時換過頭，寫成 `return self.start < self.end and start < end`。跑一次，`FAILED tests/test_schedule.py::test_adjacent_slot_is_free`、`1 failed, 7 passed in 0.01s`。重構前是全綠，所以這個紅燈只可能是重構造成的，不用猜。改回來，`8 passed in 0.00s`。kata 結束時完整的 `booking/schedule.py`：

```python
from dataclasses import dataclass
from datetime import time


class BookingError(Exception):
    pass


class Conflict(BookingError):
    pass


class InvalidRange(BookingError):
    pass


@dataclass(frozen=True)
class Booking:
    id: int
    room: str
    start: time
    end: time
    by: str

    def overlaps(self, start, end):
        """Half-open [start, end): a booking ending at 11:00 leaves 11:00 free."""
        return self.start < end and start < self.end


class Schedule:
    def __init__(self):
        self._last_id = 0
        self._bookings = []

    def book(self, room, start, end, by):
        if end <= start:
            raise InvalidRange("end must be after start")
        for existing in self._bookings:
            if existing.room == room and existing.overlaps(start, end):
                raise Conflict(
                    f"{room} is taken {existing.start:%H:%M}-{existing.end:%H:%M} by {existing.by}"
                )
        self._last_id += 1
        booking = Booking(self._last_id, room, start, end, by)
        self._bookings.append(booking)
        return booking
```

commit `refactor: name the half-open overlap rule (Booking.overlaps)`。`git show --stat HEAD` 的最後一行是 `1 file changed, 9 insertions(+), 3 deletions(-)`，只動了 `booking/schedule.py`：測試一行沒改，這是「行為不變」最直接的證據。整段歷史：

```
$ git log --oneline
f5156f2 refactor: name the half-open overlap rule (Booking.overlaps)
d01fce0 C7 R2 end must be after start (obvious implementation)
2adc795 C6 other rooms are independent (green at once, mutation-checked)
e361ea9 C5 adjacent slots are free (half-open)
51d26a4 C4 R1 message names the holder (triangulate)
c18a909 C3 R1 overlap is rejected (fake message)
8327d9f C2 ids count up (triangulate)
4fa726b C1 free room is booked (fake it)
e5aca0a chore: project skeleton and test list
```

我們再用一個 `for c in $(git rev-list --reverse HEAD)` 迴圈逐一 checkout、各跑一次 `pytest -q`（先 `export PYTHONDONTWRITEBYTECODE=1`，原因見踩雷 4）：8 個 kata commit 依序是 1 到 6 passed、8 passed、8 passed，審查者 checkout 任何一格都看得到能跑的狀態，`git bisect` 也不會停在壞掉的格子上。骨架那格是 exit 5，印什麼要看 `tests/__pycache__/` 在不在：照本章做下來它會在，git 刪掉測試檔後這個被忽略的目錄留著，印 `no tests ran in 0.00s`；先刪掉兩個 `__pycache__` 再跑，git 不追蹤空目錄，那一版就沒有 `tests/`，pytest 發出 `PytestConfigWarning: No files were found in testpaths`，印 `1 warning in 0.00s`。

## 底層機制：pytest 怎麼判定「紅」，原因藏在哪裡

```
pytest -q
 ├─ 1. 設定：找到 rootdir 與 pytest.ini（pythonpath、testpaths）
 ├─ 2. 收集（collection）：import 每個 tests/test_*.py
 │      ├─ AssertionRewritingHook 在 import 當下改寫測試檔裡的 assert
 │      └─ import 失敗 → 整批中斷，別的檔案的測試也不跑 → Interrupted → exit 2
 ├─ 3. 執行：每個測試 setup（fixture）→ call（測試本體）→ teardown
 │      ├─ call 裡 assert 失敗或丟出例外 → FAILED
 │      └─ setup / teardown 丟例外     → ERROR
 └─ 4. 回報：摘要行 + exit code
```

| exit code | `ExitCode` 名稱 | 本章在哪裡出現 | 對 TDD 的意義 |
|---|---|---|---|
| 0 | `OK` | 每一輪的綠 | 綠 |
| 1 | `TESTS_FAILED` | C1 第二次起的紅（C3 的 import 錯誤除外） | 紅，但還要分 FAILED 或 ERROR |
| 2 | `INTERRUPTED` | C1、C3 的 import 失敗、踩雷 1 | 沒有任何行為被檢查 |
| 5 | `NO_TESTS_COLLECTED` | 骨架 commit | 沒有測試 |

列舉定義在 pytest 9.2.0 的 `_pytest/config/__init__.py`，另有 3 `INTERNAL_ERROR`、4 `USAGE_ERROR`、6 `MAX_WARNINGS_ERROR`。**exit code 不足以判定紅的原因**：我們寫了一個 setup 階段就 `raise RuntimeError("database is down")` 的 fixture，pytest 記成 `ERROR test_fixture_error.py::test_uses_broken_fixture - RuntimeError: databas...`、`1 error in 0.03s`，exit code 卻是 1，和斷言失敗一樣。只看 exit code，「fixture 壞了」會被當成「測試紅了」。[Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py` 讀每個測試的報告（junit XML）而不是 exit code，原因就在這裡。

**紅燈裡的資訊從哪來**。C1 的 `+    where datetime.time(10, 0) = t('10:00')` 這種逐層展開，是 pytest 在 import 測試檔時改寫 `assert` 敘述的結果。跑過之後，`tests/__pycache__/` 裡是 `test_schedule.cpython-312-pytest-9.2.0.pyc`，`booking/__pycache__/` 裡是一般的 `schedule.cpython-312.pyc`；檔名裡的 `-pytest-9.2.0` 來自 `_pytest/assertion/rewrite.py` 的 `PYTEST_TAG`。被改寫的只有符合 `python_files` 的測試檔、命令列直接指定的檔案、`conftest.py` 和註冊過要改寫的模組，產品程式碼不受影響。用一個 id 從 2 起算的突變體比較有沒有改寫（`PYTHONDONTWRITEBYTECODE=1` 已 export）：

```bash
perl -pi -e 's/self\._last_id \+= 1/self._last_id += 2/' booking/schedule.py
pytest -q -k free_room --tb=short | grep -E "^E |passed|failed"
pytest -q -k free_room --tb=short --assert=plain | grep -E "^E |passed|failed"
git checkout -- booking/schedule.py
```

兩次都是 `1 failed, 7 deselected in 0.01s`。預設的輸出有 `E     Omitting 4 identical items, use -vv to show`、`E     Differing attributes:`、`E     ['id']`、`E       id: 2 != 1`；加上 `--assert=plain`，`E` 行只剩一行 `E   AssertionError`。有改寫，一眼看得出錯在 `id`、其他 4 個欄位相同。判斷紅的原因對不對，靠的就是這些資訊。

兩種 `.pyc` 的失效規則也不同。我們讀了兩個檔案標頭的旗標欄：`schedule.cpython-312.pyc` 是 0，即 CPython 預設的 timestamp pyc，標頭存來源檔的 mtime 與大小，對得上就沿用舊 bytecode（[PEP 552](https://peps.python.org/pep-0552/)）；pytest 寫的測試檔 pyc 是 3，checked-hash，每次都比對原始碼雜湊；`rewrite.py` 的註解說「Timestamps are unusable for us」，理由是重新 checkout 或還原快取會讓每個檔案的 mtime 都變、整批快取失效。這個差別會在踩雷 4 咬人。

## 測試層級與測試替身

測試金字塔（test pyramid）一般歸功於 Mike Cohn 2009 年的《Succeeding with Agile》。Fowler 的 [TestPyramid](https://martinfowler.com/bliki/TestPyramid.html)（2012-05-01）說穿過 UI 的端到端測試「brittle, expensive to write, and time consuming to run」，所以大量測試放在單元層，中間一層經由服務介面。對照本課的三層，同一台機器上的實測：

| 層級 | 本課的例子 | 本機實測（pytest 報的時間） | 負責的章 |
|---|---|---|---|
| 單元（unit） | `Schedule.book` 的 8 個測試加進階節的 oracle | `9 passed in 0.01s`；含直譯器啟動的 wall time 0.13–0.21 秒 | 本章、Ch 21 |
| API（服務層） | [Ch 0](./00-environment-setup.md) 雛形的 `--driver api` | `2 passed in 0.09s` | [Ch 22](./22-backend-e2e-first.md) |
| UI（端到端） | 同一個雛形的 `--driver ui`（Chromium） | `2 passed in 0.66s` | [Ch 23](./23-frontend-e2e-playwright.md) |

一輪紅綠重構至少跑三次測試（看紅、看綠、重構後再看綠），Martin 說一輪約一分鐘，每次執行就得是秒級，這是單元層的工作；上層測試證明各層接得起來，那是 Ch 22 的外圈。

測試替身（test double）是 Gerard Meszaros 的用語。Fowler 的 [TestDouble](https://martinfowler.com/bliki/TestDouble.html)（2006-01-17）列了五種：Dummy「passed around but never actually used」，Fake 有能用但走捷徑的實作，Stub「provide canned answers to calls made during the test」，Spy 是會記錄呼叫的 stub，Mock「pre-programmed with expectations」。放進預約領域（以下協作者是假設的，R1–R4 沒有）：用 list 存預約的 `Schedule` 對 API 層就是一個 fake 儲存庫，永遠回 `09:00` 的時鐘是 stub，記下通知寄給誰的物件是 spy。本章一個替身都沒用：`Schedule` 沒有 I/O，用真物件最直接，這是 Fowler〈[Mocks Aren't Stubs](https://martinfowler.com/articles/mocksArentStubs.html)〉說的古典派「use real objects if possible and a double if it's awkward to use the real thing」；mockist 則「will always use a mock for any object with interesting behavior」。替身越多，測試越容易釘在「誰呼叫了誰」上，[pstack Ch 14](../pstack/14-verification-principles.md) 的「四、Test Behavior, Not Implementation」實測過這種測試在行為壞掉時照樣綠。

## 批評與界線：什麼時候不用 TDD

2014 年，Rails 的作者 David Heinemeier Hansson（DHH）在 RailsConf 主題演講與〈[TDD is dead. Long live testing.](https://dhh.dk/2014/tdd-is-dead-long-live-testing.html)〉（2014-04-23）公開唱反調：「My name is David, and I do not write software test-first.」他的技術論點是「Test-first units leads to an overly complex web of intermediary objects and indirection in order to avoid doing anything that's "slow"」；六天後的〈[Test-induced design damage](https://dhh.dk/2014/test-induced-design-damage.html)〉把它定義成為了方便先寫測試、讓測試變快或變成單元測試，卻「harming the clarity of the code」的改動。隨後他和 Beck、Fowler 對談五場（2014-05-09 到 06-04，[Is TDD Dead?](https://martinfowler.com/articles/is-tdd-dead/)）。Beck 的立場是「programmers deserve to feel confident that their code works, TDD is one (not the only) way to reach that」，他自己很少用 mock；對 design damage，他認為問題「less about TDD and more about the quality of design decisions」。結論是「We all value self-testing code a lot, we all agree TDD is valuable in some contexts」，分歧在適用範圍。

證據也停在「某些情境」。Nagappan 等人在 Microsoft 三個團隊與 IBM 一個團隊做的個案研究（Empirical Software Engineering 13(3)，2008）摘要寫道：「pre-release defect density of the four products decreased between 40% and 90% relative to similar projects that did not use the TDD practice」，團隊主觀感受初期開發時間多了 15–35%（[摘要引文](https://neverworkintheory.org/2012/01/25/realizing-quality-improvement-through-test-driven-development.html)）。個案研究不是隨機分派，推不出因果。Fucci 等人分析 39 位專業開發者的 82 筆紀錄，發現先寫或後寫測試的順序「had no important influence」，效益可能來自「fine-grained, steady steps」（[arXiv:1611.05994](https://arxiv.org/abs/1611.05994)）。放回本章：比「測試寫在前面」更關鍵的，可能是 7 個小步、每步都綠。

我們的判斷，以下情況不用 TDD：

1. **還不知道要什麼行為**：探索、比較方案，寫可丟棄的程式，決定之後再帶著清單回來。pstack 把這種情況的規則整個反過來（[pstack Ch 9](../pstack/09-feature-refactoring.md) 的「三、Prototype playbook：規則反轉的那一份」）。
2. **事先寫不出期望輸出**：版面好不好看、生成的文字好不好。改用截圖當證據（Ch 23）或評審。
3. **測試比 bug 貴**：pstack 的 bug-fix playbook 叫你用 `tdd` 技能先寫失敗測試，但「Skip it when the test would be expensive, integration-heavy, or unclear」；跳過的只是寫測試，失敗的重現仍要先進 git 歷史（[pstack Ch 8](../pstack/08-bug-fix-playbook.md) 的「六、Step 5：讓失敗的重現先進 git 歷史」）。
4. **沒有測試的舊程式**：先用特徵測試（characterization test）釘住現狀（[reading_code Ch 30](../../soft_skills/reading_code/30-reading-bad-code.md) 的原則三），再對新行為 TDD。
5. **需求本身可疑**：TDD 會把它實作得很正確。先回頭問，[Ch 18](./18-clarify-skill.md) 的 clarify 就是為這件事設計的。

## 對比與取捨

| 方式 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| TDD：一次一個測試，先紅再綠 | 每一步都有證據；介面由使用端決定；歷史每格都綠 | 節奏慢；需要秒級回饋的測試層；拖慢探索 | 規則清楚、能在單元層表達的領域邏輯，例如本章的 `Schedule` |
| 先把清單全寫成測試，再逐一實作 | 需求一次攤開 | 同時好幾個紅，失去「只有一個紅」的訊號；清單在途中長不出來 | 從既有規格轉寫驗收測試（Ch 24 的 test plan → 紅測試） |
| 事後補測試（test-after） | 不打斷寫程式的節奏；Fucci 等人發現順序本身影響不大 | 容易照著實作寫；沒有「看過它紅」的紀錄 | 小步補，每個測試做一次突變檢查 |
| 特徵測試 | 先釘住現狀，敢動舊程式 | 連 bug 一起釘住 | 沒有測試的舊程式 |
| 不寫自動測試 | 最快 | 每次改動都靠人眼 | 拋棄式 spike、一次性腳本 |

## 踩雷集錦

1. **「紅了就是還沒實作」** → 紅有兩種：行為的紅與環境的紅。在 refactor commit 上用 `perl -ni -e 'print unless /^pythonpath/' pytest.ini` 拿掉那一行再跑，exit 2，`E   ModuleNotFoundError: No module named 'booking'`、`Interrupted: 1 error during collection`、`1 error in 0.04s`。`booking/schedule.py` 明明存在。和 C1 的第一個紅對照：那次是 `No module named 'booking.schedule'`，模組確實不存在。兩者都是 exit 2、都在收集階段，只有讀 `E` 那行才分得出來。規則：每次紅都先讀原因；斷言失敗、`DID NOT RAISE`、被測行為自己丟出的領域例外（C5）才是對的紅，import 失敗、fixture 錯誤都不是。
2. **「`pytest.raises(match=...)` 會比對整句訊息」** → 它用 `re.search`（pytest 9.2.0 的 `_pytest/raises.py`），部分符合就算過。在 C3 的綠燈上，對 C4 的情境寫一個寬鬆版 `with pytest.raises(Conflict, match="is taken"):`，它在寫死 `Taipei ... by Bob` 的假實作上直接 `1 passed in 0.00s`，三角定位咬不到。本章用 `str(rejected.value) == ...` 整句比對；要用 `match=` 就給完整訊息（同一情境會失敗並印出 `Regex pattern did not match.`），訊息裡有 `.`、`(` 之類的字元時記得 `re.escape`。
3. **「第一次就綠，代表這個行為已經做好了」** → 也可能代表測試什麼都沒測。C6 用一次手動突變證明它會紅；而且測試的守備範圍會跟著程式變（C2 在 C5 之後就不再守房間條件）。
4. **「突變完 `git checkout` 還原，程式就回來了」** → 不一定。`+= 1` 改成 `+= 2` 長度不變，同一秒內改了又改回，timestamp pyc 的 mtime 與大小都對得上，Python 會沿用舊的 bytecode。在 refactor commit 上把「原版跑一次 → 突變 → 跑 → 還原 → 跑」重複 20 次：

```bash
unset PYTHONDONTWRITEBYTECODE   # 前面 export 過；pyc 組要讓 Python 寫 .pyc
for mode in pyc nopyc; do
  [ "$mode" = nopyc ] && export PYTHONDONTWRITEBYTECODE=1 && rm -rf booking/__pycache__ tests/__pycache__
  missed=0; stale=0
  for i in $(seq 1 20); do
    pytest -q >/dev/null
    perl -pi -e 's/self\._last_id \+= 1/self._last_id += 2/' booking/schedule.py
    case "$(pytest -q | tail -1)" in *failed*) ;; *) missed=$((missed+1)) ;; esac
    git checkout -q -- booking/schedule.py
    case "$(pytest -q | tail -1)" in *failed*) stale=$((stale+1)) ;; esac
  done
  echo "$mode: mutant reported green $missed/20, original reported red $stale/20"
done
```

```
pyc: mutant reported green 4/20, original reported red 14/20
nopyc: mutant reported green 0/20, original reported red 0/20
```

有 `.pyc` 時兩個方向都會錯：突變體被報成綠（你以為測試抓不到它），還原後的正確程式被報成紅。數字取決於兩次寫檔是否落在同一秒，每次跑都不同（同一個迴圈另外兩次是 2/20 與 17/20、5/20 與 12/20），nopyc 那行每次都是 0。少了第一行的 `unset`，前面 export 過的變數讓 pyc 組也寫不出 `.pyc`，兩行都會是 0/20，坑就藏起來了。我們用腳本重放本章時也中過一次：重構的手滑版和改正版長度相同，腳本在同一秒內寫回改正版，pytest 照樣報 `1 failed, 7 passed`，刪掉 `booking/__pycache__` 才是 `8 passed`。測試檔不受影響，因為它們是 checked-hash pyc。腳本化的突變一律先 `export PYTHONDONTWRITEBYTECODE=1` 並刪掉 `__pycache__`，[Ch 32](./32-brownfield-bdd.md) 的 `mutate.py` 用得到這條。

5. **「變綠的時候順手重構」** → Canon TDD 點名的錯誤：「mixing refactoring into making the test pass」。綠燈之前重構，紅了你分不清是功能沒做完還是重構弄壞；本章的手滑能一眼定位，是因為它發生在全綠之後。同理，**重構 commit 不改測試**：refactor commit 只動了 `booking/schedule.py`，這正是行為沒變的證據。

## 進階：讓清單之外的情況也說話

**oracle 測試**。8 個例子只檢查了重疊的幾種相對位置。[Ch 2](./02-vibe-coding-vs-engineering.md) 的「進階：用一個慢但一眼看得懂的 oracle 窮舉」用「兩段時間有沒有共用任何一分鐘」對整個 API 做差分測試，6084 組配對跑 15 到 17 秒。重構把規則變成純函式之後，同一個 oracle 可以直接打在 `Booking.overlaps` 上，放進 suite 每次跑。`tests/test_overlap_oracle.py`：

```python
from datetime import time
from itertools import product

from booking.schedule import Booking

GRID = [time(h, m) for h in range(8, 13) for m in (0, 30)]  # 08:00, 08:30 ... 12:30


def minutes(t):
    return t.hour * 60 + t.minute


def test_overlaps_agrees_with_shared_minutes():
    checked, wrong = 0, []
    for s1, e1, s2, e2 in product(GRID, repeat=4):
        if not (s1 < e1 and s2 < e2):
            continue
        shared = set(range(minutes(s1), minutes(e1))) & set(range(minutes(s2), minutes(e2)))
        if Booking(1, "Taipei", s1, e1, "Bob").overlaps(s2, e2) != bool(shared):
            wrong.append(f"{s1:%H:%M}-{e1:%H:%M} vs {s2:%H:%M}-{e2:%H:%M}")
        checked += 1
    assert not wrong, f"{len(wrong)} of {checked} pairs disagree, e.g. {wrong[0]}"
```

10 個時間點排出 45 段時段、45 × 45 = 2025 組配對。兩段區間最多 4 個不同端點，所以 4 個點就排得出所有相對位置，10 個是寬裕的。真程式上 `1 passed in 0.01s`；換成 C5 的閉區間版本，`E   AssertionError: 240 of 2025 pairs disagree, e.g. 08:00-08:30 vs 08:30-09:00`、`1 failed in 0.01s`。240 剛好是所有首尾相接的配對：在第 k 個點交接的有 k × (9 − k) 種，k 從 0 加到 9 是 120，兩種先後順序再乘 2。這個測試是程式寫完才補的，它不驅動設計，負責例子沒照到的角落；動手練習 1 會看到它抓出 8 個例子全放過的突變體。看完 240 之後改回半開版本，把它 commit 成 `test: overlaps agrees with a shared-minutes oracle`（[Ch 20](./20-tdd-with-agents.md) 的 guard 在綠燈階段看到沒 commit 的測試檔，就不准改程式）。

**Transformation Priority Premise**。Martin 在 [2013 年的文章](http://blog.cleancoder.com/uncle-bob/2013/05/27/TheTransformationPriorityPremise.html)替「從假實作走到一般解」的程式變化排了優先序，主張「When passing a test, prefer higher priority transformations」。對照本章：C1 是 `(nil->constant)`，C2 是 `(constant->scalar)`，C3 是 `(unconditional->if)`，C4 又是一次 `(constant->scalar)`（寫死的訊息換成從資料組出來）。它的實際用處在挑下一個測試：挑一個能用較高優先序的變化通過的（「When posing a test choose one that can be passed with higher priority transformations」）。

**TCR**。「test && commit || revert」：測試過了自動 commit，沒過就把改動丟掉，起源是 Beck 與 Lars Barlindhaug、Oddmund Strømme、Ole Johannessen 在奧斯陸的一次 code camp（[InfoQ 的介紹](https://www.infoq.com/articles/test-commit-revert/)）。我們在 kata 上試過一次：把 R2 的 `<=` 改成 `<` 後執行 `pytest -q --tb=no && git commit -qam "tcr" || git restore booking`，結果 `1 failed, 8 passed in 0.01s`，改動消失、HEAD 沒有新 commit；這條指令只還原 `booking/`，測試檔不受影響。TCR 約束的是步伐：一步跨太大，輸掉的就多。

**兩個 commit 的寫法**。本章一輪一個 commit。pstack Ch 8 修 bug 時先把失敗的測試單獨 commit，修正疊在上面（「六、Step 5：讓失敗的重現先進 git 歷史」），審查者可以 checkout 紅的那一格親眼看它紅。[Ch 20](./20-tdd-with-agents.md) 的 hook 需要這種紅 commit 當閘門：agent 動產品程式碼之前，失敗的測試必須已經存在。

## 動手練習

1. **找出活下來的突變體**。`Booking.overlaps` 有一個只差一個字元的突變體，能讓 8 個例子測試全綠。不看答案先找出來，預測 oracle 會報幾組不一致，再補一個例子測試殺掉它。

<details><summary>答案（實測）</summary>

`return self.start <= end and start < self.end`：新預約的結束時間等於既有預約的開始時間時，被誤判為重疊。只跑例子（`pytest -q tests/test_schedule.py`）是 `8 passed`；oracle `120 of 2025 pairs disagree, e.g. 08:30-09:00 vs 08:00-08:30`，只有一個方向，所以是 240 的一半。補上「Bob 訂 10:00-11:00、Alice 訂 09:00-10:00 應該成功」的例子，同一個指令在突變體上 `1 failed, 8 passed`，在真程式上 `9 passed`（整個 suite 連 oracle 是 `10 passed`）。C5 只守了右邊那條界線。

</details>

2. **換一個清單順序重來**。從空目錄重做 kata，這次先做 R2、最後才做 id。哪些假實作消失了、哪些換了樣子？最終的 `book` 長得一樣嗎？
3. **只動測試的重構**。7 個測試的準備段幾乎相同。把它整理成 fixture 或小工具函式，全程保持綠，commit 的 `--stat` 只能出現 `tests/`。做完帶著它去讀 Ch 21。
4. **在每個 commit 上重跑**。照 C7 之後那段描述寫出逐 commit 重跑的迴圈，確認你的歷史除了骨架之外每格都綠；再故意把某一輪的測試和實作拆成兩個 commit，看迴圈怎麼顯示那一格。

## 本章重點整理

- TDD 的單位是一個循環：從清單挑一項，寫一個測試，看它因為對的原因變紅，用剛好夠的程式變綠，只在綠燈上重構，commit。清單在途中會長大（C3 加了一項）。
- 三種變綠：假實作先確認測試與介面，三角定位用第二個例子逼出一般解，明顯實作在有把握時直接寫；舊測試決定你能假到什麼程度。
- 紅要讀原因：收集錯誤（exit 2）沒有檢查任何行為；斷言失敗與 fixture 錯誤都是 exit 1，要看 FAILED 或 ERROR。pytest 在 import 測試檔時改寫 assert，紅燈才印得出兩邊的值。
- 第一次就綠的測試要用突變驗證；腳本化突變要關掉 `.pyc`，否則同一秒內的同長度修改會跑到舊 bytecode。
- 骨架之後的每個 commit 都綠、重構 commit 不動測試，這段歷史本身就是證據。TDD 證明程式符合例子，不證明例子就是對的需求。

## 自我檢核

- [ ] 不看本章，寫出 Canon TDD 的五步與三條定律，並說出第二條定律的「fail to compile」在 Python 裡對應什麼。
- [ ] C3 為什麼不能用「已經有預約就一律拒絕」當假實作？是哪個測試擋住的？
- [ ] 一個測試第一次跑就綠，你會做哪兩件事再決定留不留它？
- [ ] exit code 1 可能代表哪兩種紅？要看輸出的哪裡分辨？
- [ ] C6 的突變在 C3 時會被哪個測試抓到？為什麼 C5 之後就抓不到了？
- [ ] 你的專案裡哪一塊程式不適合 TDD？它屬於「批評與界線」五種情況的哪一種？

## 延伸閱讀

- **[Kent Beck — Canon TDD](https://newsletter.kentbeck.com/p/canon-tdd)**（2023-12-11）
  - **這篇說什麼**：Beck 本人對「TDD 到底是什麼」最短的定義：五步，加上每一步最常見的錯誤。
  - **讀哪裡**：全文，特別是每一步後面的 mistakes。
  - **和本章的關聯**：kata 照這五步走；清單在 C3 長出一項、踩雷 5，都直接對應原文。
- **[Robert C. Martin — The Cycles of TDD](http://blog.cleancoder.com/uncle-bob/2014/12/17/TheCyclesOfTDD.html)**（2014-12-17）
  - **這篇說什麼**：三條定律，以及秒、分、十分鐘、小時四種尺度的循環。
  - **讀哪裡**：三條定律與 Red-Green-Refactor 兩節。
  - **和本章的關聯**：C1、C3 先讓它 import 得到，就是第二條定律的最小循環；同作者的 Transformation Priority Premise 是進階節的延伸。
- **[Martin Fowler、Kent Beck、David Heinemeier Hansson — Is TDD Dead?](https://martinfowler.com/articles/is-tdd-dead/)**（2014-05-09 到 06-04，五場對談）
  - **這是什麼**：TDD 最有名的一次公開辯論，Fowler 寫的每場摘要加上錄影連結。
  - **讀哪裡**：先讀五場摘要，再看第 2 場「Test-induced design damage」的錄影；搭配 DHH 的原文。
  - **為什麼值得讀**：三個人都是一線實作者，爭的是適用範圍而不是對錯；本章「批評與界線」由此整理。
- **[Fucci et al. — A Dissection of the Test-Driven Development Process: Does It Really Matter to Test-First or to Test-Last?](https://arxiv.org/abs/1611.05994)**（IEEE Transactions on Software Engineering 43(7)，2017；arXiv 預印本 2016-11）
  - **核心貢獻**：把 TDD 拆成順序、粒度、一致性、重構量四個維度，用 39 位專業開發者的 82 筆紀錄檢驗，發現順序影響不大，細小而穩定的步伐才有關係。
  - **讀哪裡**：摘要與討論；統計方法可以先跳過。
  - **和本章的關聯**：說明本章為什麼最在意「每一輪都小、每一輪都綠」，而不只是「測試寫在前面」。
- **《Test-Driven Development: By Example》** — Kent Beck（Addison-Wesley；[出版社頁面](https://www.pearson.com/store/en-ca/p/test-driven-development-by-example/P200000009421/9780321146533)記 2002-11-08 出版、版權年 2003，所以不少書目寫 2003）
  - **這本書的定位**：TDD 的原典，用 Money 範例把每一個小步都寫出來，連走錯的那幾步也寫。
  - **讀哪幾章**：第一部 The Money Example 的前幾章，看假實作與三角定位實際怎麼用；第三部的 Green Bar Patterns。
  - **和本章的關聯**：本章三種變綠方式的名字都出自這本書；kata 每一輪的步幅，大約就是書中 Money 範例的步幅。

手打過一次，你知道每個紅燈該長什麼樣、每個綠燈背後該有多少程式。下一章把同一個循環拆給不同的 agent 角色，再用 hook 擋住紅燈時改程式、綠燈時改測試這兩種越界。

→ [Ch 20 AI 時代的 TDD：把紅、綠、重構分給 agent，用 hook 守門](./20-tdd-with-agents.md)
