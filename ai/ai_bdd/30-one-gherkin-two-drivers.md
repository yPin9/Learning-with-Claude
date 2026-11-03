# Ch 30 — 一份 Gherkin、兩個 driver：前後端共用驗收測試

> **目標**：讓同一份 feature 檔同時跑在 HTTP API 與瀏覽器上。你會分出 Farley 的四層（test cases、DSL、protocol drivers、受測系統）並說得出每層該知道什麼；用兩個 driver 的紅綠組合判斷 bug 在哪一層；UI 逼你改 DSL 時往對的層改（旁路只給 Given、Then 一定走受測通道、頁面要公布完成事件）；用 `--driver`、環境變數與 tag 選通道；最後讓 agent 在其他三層鎖住時補上 UI driver，再用改壞頁面的方式驗它。

> **環境**：macOS 26.6（arm64）、Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、uvicorn 0.54.0、httpx 0.28.1、Playwright 1.64.1（Chromium 156.0.8078.4）、Claude Code 2.1.295。實驗目錄 `/tmp/lab/aibdd/ch30-two-drivers/`，從空目錄開始；指令都在 `source /tmp/lab/aibdd/.venv/bin/activate` 之後執行，輸出都是 2026-11-02 實跑的（第二節開頭那組在 11-03 照 Ch 28 定稿重跑）。

## 為什麼需要這個？

在本章之前，R1 在這門課至少有三種寫法：[Ch 22](./22-backend-e2e-first.md) 用 httpx 斷言 409，[Ch 23](./23-frontend-e2e-playwright.md) 填表單、斷言狀態列，[Ch 28](./28-gherkin-dsl-style.md) 用業務語言再寫一次、step 後面接 `TestClient`。同一個行為多份描述，PM 改一個字要改好幾處。repo 裡也有拼圖沒拼起來：[SDD 練習 B](../spec_driven_development/practice-b-three-notations.md) 的「完整參考解答」說 Gherkin「不提 API、不提資料庫」，那正是兩個 driver 的前提；[SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md) 的「可驗證性」一節用 schemathesis、pact 驗 OpenAPI 契約，驗的是回應形狀，不是頁面有沒有把它顯示出來；[pstack Ch 21](../pstack/21-verification-skills.md) 替每種介面各寫一份 Drive 說明，是給 agent 讀的散文，不是能跑的一層。

更早的業界做法是兩套測試各寫一次，或整套 Cucumber 走 UI、step 寫成「When I click Book」，甚至錄製回放。Dave Farley 2015 年的投影片診斷得很直接：「Tests break when the SUT changes (Particularly UI)」、「This is a problem of design, the tests are too tightly-coupled to the SUT!」。他的解法是在測試和系統之間放一層領域語言：

> If my system allows me to placeOrders though a graphical user interface and through a REST API, then I can write one test, and it is a viable specification for both channels of communication.（Farley 接受 InfoQ 訪談，2017-04-28；`though` 是原文筆誤）

agent 讓這件事更值得做：前後端常由不同的人或 agent 實作，兩邊對著同一份 feature 跑，誰把需求理解偏了誰就紅。第二個 driver 還兼任 DSL 的設計審查，第四節的 UI driver 一接上，DSL 裡藏著的 API 習慣全部浮出來。

## 先建立直覺

駕照路考的評分表寫「路邊停車」「停止線前停車」，不寫「踩離合器、打一檔」，所以同一張表能考手排也能考自排。英文的 driver 剛好就是駕駛：評分表是 feature 檔，考官的口令是 DSL，手排與自排兩位駕駛是兩個 protocol driver，道路是受測系統（system under test，SUT）。評分表寫了「踩離合器」，自排車就考不了，這就是 Ch 28 的 GH-03 要擋的命令式步驟。比喻有一處不成立：路考的駕駛不會讓評分結果反過來，我們的 driver 會（第三節）。

Farley 把這個結構叫 Four-Layer Approach（他 2022 年 YOW! 工作坊介紹用的詞），他 2017 年的範例 repo 也照四層分：測試、`BookShoppingDsl`、每家書店一個 `BookShopDriver` 實作，最底下是受測的書店（兩個真網站和一個本地類別）。分層本身更早：GOOS（[Ch 22](./22-backend-e2e-first.md) 用過的 Freeman 與 Pryce 那本書）的範例程式（[sf105/goos-code](https://github.com/sf105/goos-code)）裡，驗收測試透過 `ApplicationRunner` 的領域動作（`startBiddingIn`、`hasShownSniperIsBidding`）操作程式，碰 Swing 視窗的是它底下的 `AuctionSniperDriver`。套到會議室預約：

```
 1 test cases   features/booking.feature  When Alice books "Taipei" from 10:30 to 11:30
       │        tests/test_booking.py     拆出參數，呼叫一次 DSL（Ch 28 的 GH-12）
 2 DSL          tests/dsl.py  booking.book("Alice", "Taipei", "10:30", "11:30") → Outcome
       │                      只說領域動詞；決定 Given 走哪條通道
       ├────────────────────────────┐
 3 drivers      drivers/api.py              drivers/ui.py
       │        POST /bookings → 409        選 Room、填欄位、按 Book、讀狀態列
 4 受測系統      同一個 uvicorn 上的 FastAPI ◀──（頁面自己 fetch）
```

每一層只認識下一層：feature 檔不知道有幾個 driver，DSL 不知道 HTTP 與 selector，driver 不知道有哪些情境。API 與瀏覽器這種進入系統的路，Farley 叫 channel，本章叫通道。

> Playwright 的定位器與 auto-wait 還不熟，先讀 [Ch 23](./23-frontend-e2e-playwright.md) 的「三、定位器」與「底層機制」；DSL 的來歷見 [Ch 28](./28-gherkin-dsl-style.md) 的「四、DSL：句子和系統之間的那一層」。

## 一、拆開 Ch 0 的雛形

[Ch 0](./00-environment-setup.md) 的「六、冒煙測試」照抄了一份 spike，說好由本章拆解。先在本章目錄重跑；spike 要自帶一行 `[pytest]` 的設定檔，否則第二節在根目錄放了 `pytest.ini` 之後，pytest 往上層找設定檔會撿到那份，匯入根目錄的 `app.py`（我們實際踩到過：第三節把 Room 換成下拉選單後，spike 的 UI 跑法 `2 failed`）：

```bash
mkdir -p /tmp/lab/aibdd/ch30-two-drivers && cd /tmp/lab/aibdd/ch30-two-drivers
mkdir -p spike && cp -R /tmp/lab/aibdd/ch00-setup/smoke/app.py /tmp/lab/aibdd/ch00-setup/smoke/features /tmp/lab/aibdd/ch00-setup/smoke/tests spike/ && printf '[pytest]\n' > spike/pytest.ini
cd spike && PYTHONPATH=.:tests pytest -q tests --driver api && PYTHONPATH=.:tests pytest -q tests --driver ui
```

兩個都綠：`2 passed in 0.08s`、`2 passed in 1.04s`。按層看下來：

| 位置 | 層 | 做對了 | 缺了 |
|---|---|---|---|
| `booking.feature` | test cases | 只說會議室、人、時段 | 沒有 `@sc_` ID 與 R 編號（Ch 28 補上） |
| step definitions | step | 只呼叫 `driver.book(...)` | 沒有 DSL：step 直接比對 `("confirmed", None)`；`free(room): pass` 什麼都沒查 |
| `drivers.py` 兩個類別 | protocol drivers | 同名方法、同一種回傳形狀；UI 用 role 與 label | 合約只靠「剛好同名」 |
| `UiDriver.book` 的 `p.goto(...)` 與 `status.filter(...).or_(...).wait_for()` | driver | 每次從新頁面開始，等的是「這一次動作的結果」，不是固定秒數 | `goto` 看似浪費時間，拿掉的後果在第三節 |
| `UiDriver.reset` 用 httpx | driver | 頁面沒有 reset，借 API | 這是旁路（back channel），第四節談它的邊界 |
| `app.py` | 受測系統 | 頁面自己 `fetch` 同一個 `POST /bookings`，兩個通道最後跑同一段規則 | 頁面看不到已有的預約（第四節）；`/reset` 一直開著（進階一節） |
| `base_url` 與 `--driver` | 基礎設施 | port 0、兩個 driver 打同一個 server、一個旗標換通道 | 不能從環境變數設、不能一次跑兩個（第五節） |

雛形缺的是第 2 層：「Given 該不該走瀏覽器」「結果用什麼型別」沒地方放，只能散在每個 step。[Ch 1](./01-course-map.md) 的動手練習在這個雛形上拿掉 R1，兩個 driver 紅在同一個情境；本章在四層版本上重做，再加兩種 Ch 1 沒看過的紅。

## 二、四層落地：把 Ch 28 那一行換成 driver

Ch 28 的 `dsl.py` 有一行 `self.http = TestClient(app)  # Ch 30 swaps this line for an api or ui driver`。回到本章根目錄（`cd ..`），feature、step、glossary 與 lint 照搬你在 Ch 28 建好的目錄，內容要和 Ch 28 列出的檔案一致（那章做過的破壞實驗先改回來；實跑時 `C28` 指向照 Ch 28 定稿重建的目錄）。Ch 29 的目錄多了取消與 journey，但它的 app 沒有頁面，所以從 Ch 28 起步。`app.py` 用 Ch 0 冒煙測試那份去掉第一行的檔名註解：

```bash
C28=/tmp/lab/aibdd/ch28-gherkin-style
mkdir -p features tests/drivers .claude/skills && cp $C28/features/booking.feature features/ && cp $C28/tests/test_booking.py tests/ && cp $C28/glossary.yaml . && cp -R $C28/.claude/skills/gherkin-style .claude/skills/ && sed '1d' /tmp/lab/aibdd/ch00-setup/smoke/app.py > app.py && touch tests/drivers/__init__.py
```

`pytest.ini` 是 Ch 28 那份刪掉 `filterwarnings` 兩行（這裡不用 TestClient）。`tests/dsl.py` 保留 Ch 28 的 `Outcome`，拿掉 `TestClient` 與 `app` 的 import；`Booking.__init__(self, driver)` 存下 driver 並呼叫 `driver.reset()`，`book` 與 `bookings_of` 原樣轉給 driver，`has_booked` 照舊斷言 `self.book(...).confirmed`（最終版在第四節全文列出）。兩個 driver 把同一組呼叫翻成各自通道的話，回傳同一個 `Outcome`。`tests/drivers/api.py`：

```python
"""drivers/api.py: the HTTP channel. Translates DSL calls into requests and responses into Outcomes."""
import httpx

from dsl import Outcome

class ApiDriver:
    def __init__(self, base_url):
        self.http = httpx.Client(base_url=base_url)

    def reset(self):
        self.http.post("/reset").raise_for_status()

    def book(self, who, room, start, end) -> Outcome:
        r = self.http.post("/bookings", json={"room": room, "start": start, "end": end, "by": who})
        return Outcome(True) if r.status_code == 201 else Outcome(False, r.json()["detail"])

    def bookings_of(self, room):
        return [b for b in self.http.get("/bookings").json() if b["room"] == room]
```

`tests/drivers/ui.py`：

```python
"""drivers/ui.py: the browser channel. Fills the form like a person and reads what the page shows."""
from playwright.sync_api import Page

from dsl import Outcome
from drivers.api import ApiDriver

class UiDriver:
    def __init__(self, page: Page, base_url):
        self.page = page
        self.back = ApiDriver(base_url)      # back channel: reset, and state the page does not show

    def reset(self):
        self.back.reset()

    def book(self, who, room, start, end) -> Outcome:
        p = self.page
        p.goto("/")                          # a fresh page: the status line starts empty
        for label, value in [("Room", room), ("Start", start), ("End", end), ("Name", who)]:
            p.get_by_label(label).fill(value)
        p.get_by_role("button", name="Book").click()
        status = p.get_by_role("status")
        status.filter(has_text="Booked").or_(status.filter(has_text="Rejected")).wait_for()
        text = status.inner_text()
        return Outcome(True) if text.startswith("Booked") else Outcome(False, text.removeprefix("Rejected: "))

    def bookings_of(self, room):
        return self.back.bookings_of(room)   # the page has no list (yet)
```

`tests/conftest.py` 的 `base_url`、`browser` 兩個 fixture 照抄 Ch 0 冒煙測試（port 0、thread 裡的 uvicorn、一個 session 一個 Chromium），檔頭 import `os`、`socket`、`threading`、`time`、`pytest`、`uvicorn`、`sync_playwright`、`app`、兩個 driver 與 `Booking`。新的是下面三段：`pytest_addoption` 放在 import 之後，`driver` 與 `booking` 放在 `browser` 之後：

```python
def pytest_addoption(parser):
    parser.addoption("--driver", choices=["api", "ui"], default=os.environ.get("BOOKING_DRIVER", "api"),
                     help="channel the scenarios go through (default: $BOOKING_DRIVER or api)")

@pytest.fixture
def driver(request, base_url):
    if request.config.getoption("--driver") == "api":
        yield ApiDriver(base_url)
        return
    context = request.getfixturevalue("browser").new_context(base_url=base_url)
    yield UiDriver(context.new_page(), base_url)
    context.close()

@pytest.fixture
def booking(driver):
    return Booking(driver)
```

```
$ diff $C28/tests/test_booking.py tests/test_booking.py && echo 'steps: same as Ch 28'
steps: same as Ch 28
$ pytest -q | tail -1; pytest -q --driver ui | tail -1; BOOKING_DRIVER=ui pytest -q | tail -1
6 passed in 0.09s
6 passed in 1.31s
6 passed in 1.27s
```

Ch 28 的 step 檔一字未改，Ch 28 說換掉那一行時「DSL 的介面不必跟著變」，在這六個測試上成立（[Ch 26](./26-bdd-vs-tdd-artifacts.md) 那套 step 沒有搬過來：它們照 `driver.book(room, start, end, by)` 呼叫、比對 tuple，要接這兩個 driver 得再包一層轉接）；再跑兩次，API 0.10 秒、UI 1.21–1.25 秒。三個決定：`Outcome` 屬於 DSL、翻譯屬於 driver（讓 API driver 回 response，step 就得懂 409，UI driver 還得假造一個）；兩個 driver 打同一個 live server（API 用 TestClient 較快，但兩個通道就打在兩種部署上，紅綠組合失去比較意義）；Given 暫時也走受測通道，第四節量了成本再改。

## 三、紅燈組合：壞在哪一層

兩個 driver 的價值不在多跑一次，在紅綠的組合指出 bug 在哪一層。**規則壞了：**重疊判斷改成常見的錯法，只看新預約的開始時間有沒有落在舊預約裡：

```
$ sed -i '' 's/b.start < x\["end"\] and x\["start"\] < b.end/x["start"] <= b.start < x["end"]/' app.py
$ for d in api ui; do echo "--- $d"; pytest -q --driver $d --tb=no -rf | tail -2; done
--- api
FAILED tests/test_booking.py::test_any_overlap_is_rejected[09:30-10:30] - Ass...
1 failed, 5 passed in 0.10s
--- ui
FAILED tests/test_booking.py::test_any_overlap_is_rejected[09:30-10:30] - Ass...
1 failed, 5 passed in 1.24s
```

同一列、同一個斷言（`-vv` 下是 `confirmed: True != False`、`reason: None != 'Taipei is taken 10:00-11:00 by Bob'`），Outline 裡「壓住開頭」那一列抓到它：兩條路都經過的規則壞了。改回來。

**頁面改了。** 先用 `shasum -a 256` 記下 feature、step、DSL、API driver 四個檔。會議室只有三間，前端把 Room 文字框換成 `<select name=room aria-label=Room><option>Taipei<option>Tokyo<option>Seoul</select>`。API 照樣 `6 passed in 0.10s`；UI `6 failed in 1.03s`，每個都是 `Locator.fill: Error: Element is not an <input>, <textarea> or [contenteditable] element`。修法只動 UI driver：迴圈拿掉 Room，前面加一行 `p.get_by_label("Room").select_option(room)`，UI 回到 `6 passed in 1.41s`；那四個檔 `shasum -c` 全部 `OK`。這是 Fowler 最早叫 Window Driver、後來叫 page object 的那種回報：UI 改版只改 driver。

**driver 壞了。** `book()` 裡的 `p.goto("/")` 換成 `if p.url == "about:blank": p.goto("/")`（頁面只載入一次，看起來省時間），同時在 app 放一個真 bug：重疊判斷的兩個 `<` 改成 `<=`，相鄰時段被當成重疊。

```
$ for d in api ui; do echo "--- $d"; pytest -q --driver $d --tb=no -rA | grep -E '^(PASSED|FAILED)|passed in|failed in'; done
--- api
…（略：5 行 PASSED）
FAILED tests/test_booking.py::test_adjacent_slots_do_not_overlap - AssertionE...
1 failed, 5 passed in 0.10s
--- ui
…（略：PASSED 的三個裡有 test_adjacent_slots_do_not_overlap；FAILED 的是 overlap 的三列）
3 failed, 3 passed in 1.25s
```

API 抓到相鄰時段的 bug；UI 讓它**通過**（再跑五次都一樣），反而把三列正確的重疊判成紅。Given 讓 Bob 在同一頁訂過，狀態列留著 `Booked Taipei`；Alice 按下 Book 時回應還沒回來，`wait_for` 看到的是 Bob 那一行。雛形那個看似多餘的 `goto`，保證的正是「讀到這一次的結果」。改回來。

| API | UI | 先懷疑 |
|---|---|---|
| 紅 | 紅，同一列、同一個斷言 | 規則或 API：兩條路都經過的地方 |
| 綠 | 紅 | 頁面，或 UI driver 跟不上頁面 |
| 紅 | 綠 | 頁面自己擋掉了（前端驗證），或 driver 讀錯了東西，像上面這次 |
| 綠 | 綠 | driver 看得到的範圍內都對；看不到的不知道（底層機制一節） |

紅綠組合要能信，前提是每個 driver 都被看過為了對的原因失敗；第六節用同一招驗 agent 寫的 driver。

## 四、UI 逼 DSL 長大

PM 的下一個例子：Alice 訂了 Bob 之後的相鄰時段，兩筆都要出現在行程（agenda）上。寫在 R1 底下，`pytest.ini` 的 markers 加一行 `sc_book_05: R1 both adjacent bookings show on the agenda`，`python .claude/skills/gherkin-style/scripts/gherkin_lint.py features/` 回 `1 files, 0 problems`：

```gherkin
    @sc_book_05
    Scenario: Both adjacent bookings show on the agenda
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the agenda of "Taipei" is
        | 10:00-11:00 Bob   |
        | 11:00-12:00 Alice |
```

**第一版 step 沿用 Ch 28 的 `bookings_of`**，也就是 API 的原始資料：

```python
@then(parsers.parse('the agenda of "{room}" is'))
def agenda_is(booking, room, datatable):
    rows = booking.bookings_of(room)
    assert [f'{b["start"]}-{b["end"]} {b["by"]}' for b in rows] == [r[0] for r in datatable]
```

API 跑法 `1 failed, 6 passed in 0.13s`，差在 `At index 0 diff: '10:00:00-11:00:00 Bob' != '10:00-11:00 Bob'`：pydantic 把 `time` 序列化成 `10:00:00`。順手在 step 裡切掉秒數（`b["start"][:5]`、`b["end"][:5]`）：

```
$ pytest -q | tail -1; pytest -q --driver ui | tail -1; grep -c '<ul' app.py
7 passed in 0.11s
7 passed in 1.56s
0
```

兩個通道都綠，頁面上卻一個 `<ul>` 都沒有：UI 跑法的 Then 是 `UiDriver.bookings_of` 經旁路問 API 答的，它從沒看過頁面。兩個錯一起發生：DSL 把 API 的形狀（秒數、欄位名）漏給了 step；旁路把「使用者看得到什麼」交給後端回答。

**改 DSL：分清楚誰走哪條路。** Given 描述狀態，可以走最快的通道建立或確認；When 與 Then 是受測的行為，一定走受測通道。最終的 `tests/dsl.py`：

```python
"""dsl.py: the booking DSL. Steps call these domain verbs; a driver decides how they reach the app."""
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Outcome:
    confirmed: bool
    reason: str | None = None

class Driver(Protocol):                      # what every channel offers, in domain terms
    def reset(self) -> None: ...
    def book(self, who: str, room: str, start: str, end: str) -> Outcome: ...
    def agenda(self, room: str) -> list[str]: ...

class Booking:
    def __init__(self, driver: Driver, setup: Driver | None = None):
        self.driver = driver                 # When and Then: the channel under test
        self.setup = setup or driver         # Given: state may be set up and checked over a faster channel
        self.setup.reset()

    def book(self, who, room, start, end) -> Outcome:
        return self.driver.book(who, room, start, end)

    def has_booked(self, who, room, start, end):
        outcome = self.setup.book(who, room, start, end)
        assert outcome.confirmed, f"setup: {who} could not book {room}: {outcome.reason}"

    def is_free(self, room) -> bool:
        return self.setup.agenda(room) == []

    def agenda(self, room) -> list[str]:
        """What a person sees for the room, in start order: ["10:00-11:00 Bob", ...]."""
        return self.driver.agenda(room)
```

`agenda()` 回傳使用者在頁面上看到的形狀，和 Ch 26 的 `DomainDriver.agenda` 一樣，每個 driver 負責翻成它。API driver 用下面這段取代 `bookings_of`，切秒數收回 driver 身上：

```python
    def agenda(self, room) -> list[str]:
        rows = sorted((b for b in self.http.get("/bookings").json() if b["room"] == room), key=lambda b: b["start"])
        return [f'{b["start"][:5]}-{b["end"][:5]} {b["by"]}' for b in rows]   # the API sends "10:00:00"
```

UI driver 刪掉 `bookings_of`，`self.back` 的註解改成 `# the page has no reset button: borrow the API`。step 檔相對 Ch 28 只有兩處不同：`room_is_free` 的本體改成 `assert booking.is_free(room), f"{room} already has bookings"`，新 Then 的本體是 `assert booking.agenda(room) == [row[0] for row in datatable]`。`conftest.py` 的 `booking` fixture 改成 `booking(driver, base_url)`，回傳 `Booking(driver, setup=ApiDriver(base_url))`。

UI 跑法誠實地紅了：`AttributeError: 'UiDriver' object has no attribute 'agenda'`，`1 failed, 6 passed in 1.24s`；頁面做不到的事就該紅，或明確標成待辦（第五節）。Given 改走 HTTP 的效果，等 UI 能跑之後各量五次：走受測通道 1.58–1.68 秒，走 HTTP 1.15–1.24 秒。代價是 UI 跑法不再證明「Bob 能用頁面訂房」，那交給其他情境的 When。Farley 2015 年投影片的交易系統範例也這樣分：`@Before` 用 `adminAPI`、`registrationAPI` 建商品與使用者（最後一行才用 `tradingUI` 登入），測試本體走 `tradingUI`。

**頁面長出行程，driver 學三件事。** `app.py` 的 API 部分保持 Ch 0 的寫法，`index()` 改成回傳下面的 `PAGE`（Room 是第三節的下拉選單；三處 `aria-busy` 是第三件事才加的）：

```python
PAGE = """<form id=f>
<select name=room aria-label=Room><option>Taipei<option>Tokyo<option>Seoul</select>
<input name=start aria-label=Start><input name=end aria-label=End><input name=by aria-label=Name>
<button>Book</button></form>
<p role=status></p>
<ul aria-label=Agenda aria-busy=true></ul>
<script>
const status = document.querySelector("[role=status]"), agenda = document.querySelector("ul");
async function refresh() {
  agenda.setAttribute("aria-busy", "true");
  const rows = await (await fetch("/bookings")).json();
  rows.sort((a, b) => a.start.localeCompare(b.start));
  agenda.replaceChildren(...rows.map((b) => Object.assign(document.createElement("li"),
    {textContent: `${b.room} ${b.start.slice(0, 5)}-${b.end.slice(0, 5)} ${b.by}`})));
  agenda.setAttribute("aria-busy", "false");  // the concluding event drivers wait for
}
f.onsubmit = async (e) => {
  e.preventDefault();
  const r = await fetch("/bookings", {method: "POST", headers: {"content-type": "application/json"},
                                      body: JSON.stringify(Object.fromEntries(new FormData(f)))});
  const j = await r.json();
  status.textContent = r.ok ? "Booked " + j.room : "Rejected: " + j.detail;
  if (r.ok) refresh();
};
refresh();
</script>"""
```

UI driver 的第一版 `agenda()` 開頁面、立刻讀清單的 `listitem`、留下 `"Taipei "` 開頭的列。第一件事馬上來，紅在 `book()` 填表單那一步：`strict mode violation: get_by_label("End") resolved to 2 elements`，第二個是 `<ul aria-label="Agenda">`。`get_by_label` 預設不分大小寫、比對子字串（Playwright 1.64.1 的說明：`exact` 預設 false），「Agenda」裡有「end」。修法還是只動 driver：`get_by_label(label, exact=True)`，`7 passed in 1.14s`。

第二件事是時間。用測試專用的 ASGI 包裝讓 `GET /bookings` 慢 50 毫秒，產品程式碼不動：放在 `conftest.py` 的 `base_url` 之前，檔頭 import 加 `asyncio`，`base_url` 裡讀 `latency = int(os.environ.get("READ_LATENCY_MS", "0"))`，把 `uvicorn.Config(app, ...)` 換成 `uvicorn.Config(slow_reads(app, latency), ...)`：

```python
def slow_reads(asgi, ms):  # test-only: delay GET /bookings by READ_LATENCY_MS, like a slow network
    async def slowed(scope, receive, send):
        if ms and scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == "/bookings":
            await asyncio.sleep(ms / 1000)
        await asgi(scope, receive, send)
    return slowed
```

`READ_LATENCY_MS=50` 連跑三次，三次都是 `AssertionError: assert [] == ['10:00-11:00...-12:00 Alice']`、`1 failed, 6 passed`：`goto` 只等到 `load` 事件，清單的 `fetch` 在那之前送出、回應卻在之後才到（一次實測：請求 13 ms、`load` 16 ms、回應 66 ms），driver 讀得太早。如果「free all day」也在頁面上確認，這個空清單會讓它永遠成立。

第三件事是完成事件（concluding event）。driver 回傳值、不斷言，就得知道頁面什麼時候「做完了」。Farley 投影片的說法是「Look for a "Concluding Event" listen for that in your DSL to report an async call as complete」，以及「Never, Never, Never, put a "wait(xx)"」。我們的頁面用清單上的 `aria-busy` 公布它。這是 WAI-ARIA 的標準狀態，意思是「這個元素正在更新」，螢幕閱讀器這類輔助技術可以等它變回 false 再把內容交給使用者；driver 等的是同一個訊號：

```python
    def agenda(self, room) -> list[str]:
        p = self.page
        p.goto("/")
        self._settled()
        items = p.get_by_role("list", name="Agenda").get_by_role("listitem").all_inner_texts()
        return [i.removeprefix(room + " ") for i in items if i.startswith(room + " ")]

    def _settled(self):                      # concluding event: the page says the list has finished loading
        self.page.locator("ul[aria-busy=false]").wait_for(state="attached")
```

延遲 0、50、200 毫秒各跑三次全綠（`7 passed`，1.21–1.24、1.42–1.54、1.92–1.93 秒）。`state="attached"` 不是多寫的：預設的 `visible` 要求非空的外框，空的 `<ul>` 永遠不算可見（踩雷第 3 條）。為什麼不用 `wait_for_load_state("networkidle")`？Playwright 的說明把它標成 **DISCOURAGED**，它等「網路安靜」，不等「這張清單畫好了」。

## 底層機制：一個 Then 走到 Chromium 又回來

```
Then the agenda of "Taipei" is | 10:00-11:00 Bob | ...  pytest-bdd 比對句子，表格以 datatable 傳入
 ▼ agenda_is(booking, room, datatable)    booking 由 request.getfixturevalue 取得（Given 那步建的，這裡拿快取）
 ▼ Booking.agenda("Taipei")               DSL：Then 一律走 self.driver
 ▼ UiDriver.agenda → _settled()           Python ─pipe─▶ Node driver（輪詢）─CDP─▶ Chromium
                                          頁面 refresh() ─HTTP─▶ uvicorn thread → FastAPI
                                          ◀── ul[aria-busy=false] 出現，讀 listitem
 ▼ ["10:00-11:00 Bob", "11:00-12:00 Alice"] == [row[0] for row in datatable]   斷言只在 step
```

**斷言放哪一層，決定 driver 要多聰明。** 我們的 driver 回傳值、step 斷言，這是 Fowler〈PageObject〉（2013）的立場：「I favor having no assertions in page objects.」Farley 的範例 repo 相反，`BookShopDriver` 有 `assertListedInShoppingBasket`，Amazon 版在 driver 裡 `assertEquals`。回傳值的 driver 不能靠 web-first assertion（Ch 23 的「底層機制」）重試到對（它不知道什麼叫對），所以頁面得公布完成事件；斷言型的 driver 可以重試，代價是每多一種檢查就多一個 driver 方法。第六節 agent 的 driver 走中間路線：用 `expect(...)` 等完成事件，仍然回傳值。斷言業務結果和檢查協定細節是兩回事：狀態碼、欄位型別這類檢查，[Ch 32](./32-brownfield-bdd.md) 會放進 driver。

**driver 的每個決定，都決定它看得到哪些 bug。** 把頁面的 `if (r.ok) refresh();` 註解掉，使用者訂完房，清單不會更新。上面那版 `agenda()` 每次重開頁面，重抓的清單把 bug 藏起來：UI 跑法 `7 passed in 1.13s`。把 `agenda()` 的 `p.goto("/")` 改成兩行 `if p.url == "about:blank":` / `p.goto("/")`，註解寫 `# nothing on screen yet; otherwise read what the user sees now`，也就是頁面開著就讀當下畫面：

```
$ pytest -q --driver ui --tb=short | grep -E '^E   A|passed|failed'
E   AssertionError: assert ['10:00-11:00 Bob'] == ['10:00-11:00...-12:00 Alice']
1 failed, 6 passed in 1.25s
```

頁面改回來後 `7 passed in 1.17s`，延遲 200 毫秒時 `1.84s`。「每個動作重開頁面」對 `book()` 是對的（狀態列不能殘留），對 `agenda()` 是錯的（使用者不會為了看清單重新整理）。driver 不是中性的轉接頭。

**fixture 是執行到 step 才要的。** pytest-bdd 9.0.1 產生的測試函式簽名只有 `(request, _pytest_bdd_example)`（`scenario.py` 的 `scenario_wrapper`），step 要的 `booking`、`driver` 在執行到那一步時才用 `request.getfixturevalue` 拿（同檔第 238 行）。下一節它會咬人。

## 五、選通道：旗標、環境變數與 tag

**環境變數不會被 `choices` 檢查。** 打錯的旗標會被擋（`pytest: error: argument --driver: invalid choice: 'API' (choose from api, ui)`），但 `BOOKING_DRIVER` 進來的是 option 的預設值，argparse 不拿預設值比對 `choices`。`BOOKING_DRIVER=API pytest -q` 回 `7 passed in 1.17s`（API 是 0.12 秒）：它掉進 `driver` fixture 的 else，悄悄跑了 UI。else 若寫成 API，標著 UI 的 CI job 就會永遠只測 API，又快又綠。`choices` 加上 `"both"`，在 `pytest_addoption` 之後加：

```python
def pytest_configure(config):  # argparse checks choices only for typed flags, not for env defaults
    if config.getoption("--driver") not in ("api", "ui", "both"):
        raise pytest.UsageError(f"--driver / BOOKING_DRIVER must be api, ui or both, got {config.getoption('--driver')!r}")
```

`BOOKING_DRIVER=API pytest -q` 現在印出 `ERROR: --driver / BOOKING_DRIVER must be api, ui or both, got 'API'`，exit code 4。

**一次跑兩個通道。** 直覺寫法是 `@pytest.fixture(params=["api", "ui"])` 的 `channel` fixture，`driver` 改成 `driver(channel, request, base_url)`、第一行改成 `if channel == "api":`。結果是 `7 failed in 0.03s`，七個都是 `The requested fixture has no parameter defined for test:`，接著 `Requested fixture 'channel' defined in:`。收集到的還是 7 個。參數化發生在收集階段，pytest 只看得到測試「宣告」的 fixture；pytest-bdd 的測試函式沒宣告 `channel`，它在執行到 step 時才被要（上一節最後一點）。修法是 autouse：autouse 的 fixture 在收集時就進入每個測試的 fixture 閉包，`pytest_generate_tests` 才看得到它。用這段取代那個 fixture，放在 `driver` 之前：

```python
def pytest_generate_tests(metafunc):  # runs at collection, so "both" can double every scenario
    if "channel" in metafunc.fixturenames:
        chosen = metafunc.config.getoption("--driver")
        metafunc.parametrize("channel", ["api", "ui"] if chosen == "both" else [chosen])

@pytest.fixture(autouse=True)  # autouse puts "channel" into every scenario's fixture closure
def channel(request):
    return request.param
```

單一通道照舊（API `7 passed in 0.12s`、UI `1.16s`），`--driver both` 是 `14 passed in 1.26s`：

```
$ pytest --driver both -v | grep -E 'agenda|overlap_is_rejected\[(api|ui)-09'
tests/test_booking.py::test_any_overlap_is_rejected[api-09:30-10:30] PASSED [ 42%]
tests/test_booking.py::test_any_overlap_is_rejected[ui-09:30-10:30] PASSED [ 64%]
tests/test_booking.py::test_both_adjacent_bookings_show_on_the_agenda[api] PASSED [ 78%]
tests/test_booking.py::test_both_adjacent_bookings_show_on_the_agenda[ui] PASSED [ 85%]
```

測試 ID 帶上通道，第三節那張紅綠表在一份報告裡排在一起。**tag 就是 marker：** pytest-bdd 9.0.1 把 scenario、feature、Rule 上的 tag 交給 `pytest_bdd_apply_tag` hook，預設實作就是 `getattr(pytest.mark, tag)(function)`（`plugin.py` 第 135–138 行），`Examples:` 上的 tag 則變成那幾列參數的 mark。所以 ID tag 能和通道疊著選：`pytest -q --driver both -m sc_book_05` 回 `2 passed, 12 deselected in 0.44s`，`-m 'sc_book_05 or sc_book_04'` 回 `4 passed, 10 deselected in 0.56s`。忘了在 `pytest.ini` 註冊照樣選得到（`-m sc_book_05` 回 `1 passed, 6 deselected, 1 warning`），那個 warning 是 `PytestUnknownMarkWarning: Unknown pytest.mark.sc_book_05`，發出位置正是上面那個 hook 的 `pytest_bdd/plugin.py:136`；加上 `--strict-markers` 就是 [Ch 26](./26-bdd-vs-tdd-artifacts.md) 關鍵字巡禮看過的收集錯誤。

**頁面還做不到的情境：`@ui_pending`。** 回到第四節中途：行程情境已經約定、API 做好了、頁面還沒有清單。三個選擇：讓 UI 紅著（誠實，但 UI 的 job 從此一直紅、失去訊號）、經旁路回答（假綠，第四節看過）、明確標成待辦。第三種在 `channel` 之後加一個 hook，`pytest.ini` 註冊 `ui_pending`：

```python
def pytest_collection_modifyitems(items):  # @ui_pending: agreed and built on the API, not on the page yet
    for item in items:
        if item.get_closest_marker("ui_pending") and item.callspec.params["channel"] == "ui":
            item.add_marker(pytest.mark.skip(reason="@ui_pending: the page does not offer this yet"))
```

把 `UiDriver.agenda` 暫時改名重現那個狀態，`--driver both` 先是 `1 failed, 13 passed in 1.18s`（`AttributeError`）；在 `@sc_book_05` 同一行加上 `@ui_pending` 之後：

```
$ pytest -q --driver both -rs | tail -2
SKIPPED [1] ../.venv/lib/python3.12/site-packages/pytest_bdd/scenario.py:303: @ui_pending: the page does not offer this yet
13 passed, 1 skipped in 1.06s
```

API 那份照跑，UI 那份跳過並寫明原因。skip 的位置指向 pytest-bdd 內部而不是 feature 檔，所以 reason 得自己說清楚；tag 打成 `@ui_pendng`，不加 `--strict-markers` 就是一個沒人認得的 marker，情境照常在 UI 上跑（實測 `1 failed, 13 passed, 1 warning`）；加了則在收集時就報 ``'ui_pendng' not found in `markers` configuration option``。Ch 28 的 lint 對多一個 tag 沒意見（GH-10 只數 `@sc` 開頭的 tag），待辦掛多久要靠 review。

為什麼「預設每個通道都跑、例外才標」，而不是 Farley 範例那樣每個測試列出 `@Channel({BookDepository, Amazon})`？列舉式的預設是「不跑」：新情境忘了標，UI 覆蓋悄悄變少；例外式忘了標，UI 會紅給你看。（他範例裡的組合 driver 只取 `channels.get(0)`，逐一通道執行的部分不在範例裡。）他 2015 年的投影片還有另一種 tag，`@TimeTravel`、`@Destructive`，標的是測試需要什麼環境，同一個 marker 機制也做得到。

## 六、讓 agent 補一個 driver，feature 一行不動

新通道最常見的來法，是有人（或 agent）在既有的四層上補一個 driver，四層該撐住的正是這件事。我們把第五節結束時的整套複製到 `agent-run/`，UI driver 換成三個方法都 `raise NotImplementedError` 的空殼（UI `7 failed`，API 照樣 `7 passed`），加上 `run.sh`，再用 `shasum -a 256` 記下 `ui.py` 以外的八個檔（空的 `__init__.py` 不算；實跑時副本裡的 step 檔搬自 Ch 28 改號前的實驗目錄，第一行 docstring 寫的是 `GH-13`，其餘和照本章做出來的檔案相同）。人和 agent 共用的入口 `run.sh` 除了 shebang 與註解只有三行：`cd "$(dirname "$0")"`；第一個參數是 `--read-latency` 時 `export READ_LATENCY_MS="$2"` 並 `shift 2`；最後 `exec /tmp/lab/aibdd/.venv/bin/python -m pytest -q "$@"`。延遲做成參數，是因為 Ch 23 的 agent 想在一行裡用 `for` 迴圈加 `LATENCY_MS=800` 自己測延遲，被 `Bash(./run_ui_tests.sh *)` 擋下（allow 規則不越過變數賦值去比對）。prompt 只給結構約束與完成條件：

```text
This project runs one Gherkin suite through two channels. features/booking.feature holds the scenarios, tests/test_booking.py the step definitions, tests/dsl.py the DSL and the Driver protocol, tests/drivers/api.py the HTTP driver, and app.py the API and the page. tests/drivers/ui.py is a stub. Implement UiDriver so that the same scenarios pass through the browser.

Rules:
- Edit only tests/drivers/ui.py.
- book() and agenda() must use the page the way a person does, through the Playwright page passed to the constructor. reset() may call the HTTP API, because the page has no reset.
- Run the suite with ./run.sh. It accepts pytest options, and --read-latency MS as its first option slows GET /bookings.

You are done when ./run.sh --driver ui, ./run.sh --read-latency 200 --driver ui and ./run.sh --driver both all report that every test passed. Reply with a short summary of how the driver knows that the page has finished each action.
```

prompt 檔放在 `agent-run/` 外面，在 `agent-run/` 裡用 `claude -p "$(cat ../agent-prompt.txt)"` 執行，旗標是 `--setting-sources project,local --strict-mcp-config --tools "Read,Glob,Grep,Edit,Write,Bash" --allowedTools "Read" "Glob" "Grep" "Edit(/tests/drivers/ui.py)" "Bash(./run.sh *)" --permission-prompts none --max-turns 20 --max-budget-usd 3 --no-session-persistence --output-format stream-json --verbose`，stdin 接 `/dev/null`。只有 `Edit(/tests/drivers/ui.py)` 能改檔，`--permission-prompts none` 讓其他需要核准的動作直接被拒。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，claude-opus-5-5）。

`success`、15 turns、84.3 秒、`total_cost_usd` 0.302、1 次權限拒絕。14 次工具呼叫：8 次 Read 讀遍每個檔、1 次 Write 寫出 45 行的 driver、5 次 Bash。被拒的是一條複合指令（重複跑 200 毫秒延遲、跑 1000 毫秒、在暫存副本裡用 `sed` 拿掉等待看會不會紅），理由是 `Quoted text in this command can't be checked before it runs`；它改成逐條跑三個指定指令，`7 passed in 1.20s`、`7 passed in 4.51s`、`14 passed in 1.20s`。它的 `book()` 開頁面後先等 `aria-busy` 變 false 才碰表單，按下 Book 後用 `expect(...).to_have_text(re.compile("^(Booked|Rejected)"))` 等結果，成功時再等一次清單刷新。回覆裡的理由與自陳：

> **Opening the page:** a new document starts with `aria-busy=true`, so the driver waits for `false` before touching the form. Submitting earlier would let the first load's refresh finish while the post-booking refresh is still running, and `aria-busy` would go `false` too soon.

回覆的 Limitations 一節自陳沒驗到的事：被拒的那條指令裡，有「拿掉 `aria-busy` 的等待，套件會不會紅」這項檢查。

**不信回報，自己驗。** 八個鎖定檔 `shasum -c` 全部 `OK`；三個指令重跑得到 `7 passed in 1.12s`、`7 passed in 4.31s`、`14 passed in 1.20s`；`httpx` 只出現在 `reset()`。再在副本裡改壞頁面，確認它真的在看頁面：成功訊息的 `Booked ` 改成 `Saved `，UI `3 failed, 4 passed in 16.03s`（三個該成功的情境各等滿 `expect` 預設的 5 秒）、API `7 passed in 0.13s`；拿掉訂房後的 `refresh()`，它紅在 `assert ['10:00-11:00 Bob'] == ['10:00-11:00...-12:00 Alice']`。

**它比我們多想了一步。** 它說的「兩次 refresh 賽跑」，我們的 driver 在 1000 毫秒延遲下也沒紅過。原因在延遲模型：`slow_reads` 在 app **處理之前**睡，延遲的 GET 拿到的永遠是最新資料；真實網路的延遲也發生在回程，那時資料已經舊了。把包裝換成「app 立刻回答，回答晚到」：

```python
def slow_reads(asgi, ms):  # test-only: GET /bookings answers now, the answer arrives READ_LATENCY_MS later
    async def slowed(scope, receive, send):
        async def late(message):
            if message["type"] == "http.response.start":
                await asyncio.sleep(ms / 1000)
            await send(message)
        is_read = scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == "/bookings"
        await asgi(scope, receive, late if ms and is_read else send)
    return slowed
```

```
$ for i in $(seq 10); do READ_LATENCY_MS=200 pytest -q --driver ui -p no:cacheprovider | tail -1; done | sed 's/ in .*//' | sort | uniq -c
   7 1 failed, 6 passed
   3 7 passed
```

我們的 driver 十次紅七次（這是競態，比例每輪不同：驗證時再跑三輪，紅 8、4、5 次），紅在 `assert ['10:00-11:00 Bob'] == [...]`；同一個迴圈換上 agent 的 driver 是 `10 7 passed`。我們另用 `page.on("request")`、`page.on("response")` 記時間，其中一次：頁面第一次載入的 GET 在 24 ms 送出、227 ms 才收到，內容是 Alice 訂房前的舊清單；訂房後的 GET 118 ms 送出、322 ms 收到。舊回應先到，把清單畫成舊的、把 `aria-busy` 設回 false，driver 在 238 ms 看到 false 就讀了。完成事件只說「某一次 refresh 做完了」，沒說是哪一次（時間窗只有幾十毫秒：這支獨立的探針 12 次只抓到 2 次，驗證時另跑 52 次一次也沒抓到；同樣的記錄掛進 UI driver、跑整套，第 3 輪就抓到）。修法照抄 agent：`book()` 的 `goto` 之後加一行 `self._settled()`，十次全綠；延遲 0、200、1000 毫秒下是 `1.21s`、`3.89s`、`13.72s`，代價是每次訂房多等一次清單。我們能發現這一點，靠三件事：要它說明「怎麼知道頁面做完了」、自己重跑而不信它的表格、它把沒驗到的事寫了出來。它被擋下的那條複合指令其實正是該做的驗證；要讓 agent 自己做，給 `run.sh` 加參數比放寬 Bash 規則安全，和 Ch 23 的結論一樣。

## 對比與取捨

| 做法 | 一條規則寫幾次 | UI 改版改哪裡 | 抓得到 | 抓不到 | 適合 |
|---|---|---|---|---|---|
| 兩套測試（Ch 22 的 API 測試＋Ch 23 的 UI 規格） | 2 | UI 規格逐一改 | 各通道的細節 | 兩份描述漂移 | 兩個通道行為本來就不同 |
| Gherkin＋命令式 step（`When Alice clicks "Book"`） | 1 | feature 檔 | UI | API 通道；PM 讀不懂按鈕順序 | 不建議 |
| Gherkin＋step 直接打 HTTP（[Ch 27](./27-ai-bdd-loop.md) 的 `book()` helper） | 1 | — | API | 頁面 | 只有 API 的服務 |
| page object（Fowler 的 Window Driver） | UI 一份 | page object | UI | API | UI 測試套件 |
| 四層＋兩個 driver（本章） | 1 | 只有 UI driver | 兩個通道的接線差異 | 部署與真實網路；driver 自己的盲點 | 同一行為有多個入口 |
| 契約測試（SDD Ch 36 的 schemathesis、pact） | 0（從 schema 產生） | — | 回應形狀、500 | 規則對不對、頁面有沒有顯示 | API 邊界 |

兩個通道差一個數量級（7 個情境：API 約 0.12 秒、UI 約 1.1–1.2 秒）。情境多到 UI 拖慢回饋時，可以每個 PR 跑 `--driver api`，UI 改成夜間全跑、PR 上只跑某個 tag（例如 [Ch 29](./29-example-mapping.md) 的 `@journey`，它說 journey 是最自然的 UI 候選）；這種拆法本章沒有量。

## 踩雷集錦

1. **錯誤直覺：旁路只是讓測試快一點。** 正確認識：Given 走旁路沒問題，Then 走旁路就不是在測 UI；第四節的頁面沒有任何清單，UI 跑法照樣 `7 passed in 1.56s`。只有建立或確認前置狀態能走旁路，結果一定在受測通道上觀察。
2. **錯誤直覺：driver 只是轉接，不會錯。** 正確認識：拿掉每次動作前的 `goto`，UI driver 把相鄰時段的真 bug 判成綠（五次都是），把三列正確的重疊判成紅。driver 要和產品程式碼一樣被看著失敗過；改壞頁面（`Booked` 改 `Saved`、拿掉 `refresh()`）是最便宜的檢查。
3. **錯誤直覺：等元素出現就是等完成。** 正確認識：讀太早會讀到空清單（50 毫秒延遲 3/3 紅）；等 `ul[aria-busy=false]` 用預設的 `visible`，空清單永遠等不到。我們用一個探針測試等一張空行程，call log 是 `21 × locator resolved to hidden <ul aria-busy="false" aria-label="Agenda"></ul>`，接著 `Locator.wait_for: Timeout 2000ms exceeded.`（探針把逾時設成 2 秒，預設 30 秒）。等屬性用 `state="attached"`，而且完成事件得分得出是哪一次（第六節）。
4. **錯誤直覺：`get_by_label("End")` 只會找到 End。** 正確認識：預設不分大小寫、比對子字串，頁面多一個 `aria-label=Agenda` 就 strict mode violation。driver 裡一律 `exact=True`。
5. **錯誤直覺：`choices` 會擋住打錯的值。** 正確認識：只擋命令列，不擋環境變數來的預設值，`BOOKING_DRIVER=API` 悄悄跑了 UI。在 `pytest_configure` 自己驗；tag 搭配 `--strict-markers`。

## 進階：別名隔離、共用環境與第三個通道

- **別名（aliasing）**：Farley 主張在已部署的共用環境裡反覆跑同一批測試、不靠 reset：每個測試建自己的帳號與商品（functional isolation），DSL 再把名字換成帶唯一後綴的版本，投影片的例子是 `Continuous Delivery` 變成 `Continuous Delivery1234`（temporal isolation；InfoQ 訪談叫 proxy-naming）。我們的領域做不到：會議室固定三間（還做成了下拉選單），衝突以會議室為單位，換人名隔離不了任何東西。所以本章靠 `/reset`，代價是同一個環境不能並行跑兩個測試；要在共用環境跑，得先在領域裡找出天然的隔離單位（例如租戶），這是需求問題，不是測試技巧。本段未實測。
- **打已部署的環境**：Ch 23 的 conftest 支援 `BASE_URL`，加進本章的 `base_url`，兩個 driver 都能打 staging；前提是那裡有測試模式的 `/reset`，[Ch 22](./22-backend-e2e-first.md) 第五節把它的存在條件寫成了驗收測試。本段未實測。
- **第三個通道**：`Driver` Protocol 只有三個方法。Ch 26 的 `DomainDriver` 直接呼叫 `Schedule`，把 `book` 改成 `(who, room, start, end)` 並回傳 `Outcome`、再補上 `reset`，就能當第三個通道，最快、但測不到 HTTP；CLI、訊息佇列也是同一個形狀。多一個通道是 `conftest.py` 多一個分支、`--driver` 多一個值，feature 一行不動。本段未實測（動手練習第 3 題是它的 TestClient 版）。

## 動手練習

1. **R3 的兩個通道**：照 [Ch 3](./03-propose-constrain-feedback.md) 的措辭在 `Rule: R3` 下加一個情境（`Then the booking is rejected because it is outside office hours 08:00-20:00`），API 回課程固定的 detail `outside office hours 08:00-20:00`。先只改 API：Ch 28 的 `rejected` step 逐字比對，API 通道會紅在 `reason: 'outside office hours 08:00-20:00' != 'it is outside office hours 08:00-20:00'`。該改句子、step 還是 detail？（實測：另加一支更具體的 Then 蓋不過既有的 `rejected`，放前放後都一樣。）選一個讓 API 轉綠，再預測 UI 通道要不要改，用 `--driver both` 驗證並說出理由。
2. **讓 `book()` 不必重開頁面**：替狀態列也加完成事件（送出時清空並設 `aria-busy=true`），刪掉 `book()` 的 `goto`，用第三節「相鄰 bug」的實驗證明它不再把結果判反。
3. **第三個 driver**：用 `fastapi.testclient.TestClient` 寫 `AsgiDriver` 加進 `--driver`（Ch 28 那兩行 `filterwarnings` 要加回 `pytest.ini`），量三個通道的時間，寫下它比 API driver 多抓到、少抓到什麼。
4. **審你自己的 driver**：列出 UI driver 的每個決定（重開頁面、旁路、等什麼），每個決定配一種改壞頁面的方式，實際跑一次，證明它擋得住或擋不住。

## 本章重點整理

- 四層：test cases 只說業務、DSL 只說領域動詞、protocol driver 翻成某一個通道的話、受測系統只有一個。Ch 28 的 step 檔接上兩個 driver 時一字未改。
- 紅綠組合指出 bug 的層：兩邊同列同斷言紅是規則，只有 UI 紅是頁面或 UI driver；driver 自己也會讓結果反過來，所以每個 driver 都要被看過為對的原因失敗。
- UI 逼 DSL 長大：回傳使用者看得到的形狀；Given 可以走旁路，Then 不行；頁面要公布完成事件，而且要分得出是哪一次做完。
- 選通道：`--driver` 與環境變數（要自己驗）、`both` 需要 autouse 的參數化 fixture；tag 就是 marker，「例外才標」的 `@ui_pending` 比列舉通道安全。
- agent 能在鎖住的其他三層上補一個 driver；收下之前：雜湊、重跑、改壞頁面、換一個更兇的延遲模型。

## 自我檢核

- [ ] 不看本章，畫出四層，並說出 `Outcome`、`409`、`aria-busy`、`get_by_label` 各屬於哪一層，以及為什麼不能放在別層。
- [ ] API 綠、UI 紅時，你會先查哪兩件事？什麼情況會 API 紅、UI 綠？各舉本章一個例子。
- [ ] 為什麼 Given 可以走旁路、Then 不行？用「頁面沒有清單卻 `7 passed`」那個實驗說明。
- [ ] 為什麼 `@pytest.fixture(params=["api", "ui"])` 在 pytest-bdd 裡收集不到 14 個測試？autouse 改變了什麼？
- [ ] 最後的 UI driver 等了哪兩種完成事件？只等其中一種，會在哪種延遲模型下出錯？原本的延遲模型為什麼看不出來？

## 延伸閱讀

- **[Dave Farley — How to Write Acceptance Tests](https://www.youtube.com/watch?v=JDD5EEJgpHU)**（YouTube 頻道 Modern Software Engineering，2020-08-19）與範例 **[davef77/acceptance-testing](https://github.com/davef77/acceptance-testing)**（2017）
  - **讀哪裡**：影片說明說它講「the anatomy of an effective test infrastructure」；repo 先讀 `ExampleBookStoreAcceptanceTest.java`（`@Channel`）、`BookShoppingDsl.java`、`drivers/BookShopDriver.java`，再看 `OrderBook.feature` 與 `AddBookToBasketSteps.java` 怎麼把 Cucumber 接到同一個 DSL。
  - **學到什麼**：四層在原作者手上的樣子，以及 JUnit 測試和 Cucumber step 共用同一個 DSL 的接法。**和本章的關聯**：他的 driver 自己斷言、通道用 annotation 列舉，正好和本章相反，對照著讀最有收穫。**前提**：讀得懂 Java 與 JUnit 4；repo 2017 年後沒更新，Selenium 寫法已過時，看結構即可。
- **[Dave Farley — Acceptance Testing for Continuous Delivery（投影片 PDF）](https://edinburgh.bcs.org/events/2015/acceptance_testing_for_continuous_delivery.pdf)**（BCS Edinburgh，2015）
  - **讀哪裡**：「Language of the Problem Domain - DSL」、「Test Isolation」三種隔離、「Make Test Cases Internally Synchronous」、`@Channel(fixApi, dealTicket, publicApi)` 那頁、「Test Environment Types」。
  - **學到什麼**：完成事件、通道 tag、三種隔離與別名在原始投影片裡的說法。**和本章的關聯**：第四節的完成事件與 Given 走旁路、第五節的 tag、進階的別名隔離，原始論述都在這份投影片。
- **[Ben Linders — Automated Acceptance Testing Supports Continuous Delivery](https://www.infoq.com/news/2017/04/acceptance-testing-delivery)**（InfoQ，2017-04-28，Farley 的訪談）
  - **讀哪裡**：「whatever the nature of the interface」那段與三種隔離（含 proxy-naming）。**學到什麼**：投影片沒有旁白，這篇是他用完整句子講同一套想法，篇幅短。**和本章的關聯**：開頭那段「一個測試、兩個通道」的引文出自這裡。
- **[Martin Fowler — PageObject](https://martinfowler.com/bliki/PageObject.html)**（bliki，2013-09-10）
  - **讀哪裡**：全文，重點是「should allow a software client to do anything and see anything that a human can」與斷言該不該放進 page object 那段。**學到什麼**：page object 對外只說人能做、能看的事，不露出 HTML。**和本章的關聯**：UI driver 是 page object 的一般化，文末說他最早把它叫 Window Driver；本章的 driver 不斷言，採的是他的立場。
- **[Chris James — Scaling acceptance tests](https://quii.gitbook.io/learn-go-with-tests/testing-fundamentals/scaling-acceptance-tests)**（Learn Go with Tests）
  - **讀哪裡**：他把 Farley 的「Specification->DSL->Driver->System」落到 Go 的那段，以及同一份 `GreetSpecification` 先接 HTTP driver、再接 gRPC driver 的例子。**學到什麼**：沒有 Gherkin 的四層長什麼樣，證明四層不依賴 BDD 工具。**和本章的關聯**：同一份 specification 換 driver，就是本章 `--driver` 做的事。**前提**：讀得懂 Go 的 interface。

feature 檔現在是前後端共用的合約，而且每一份都說得出自己壞在哪一層。下一章把它放回 SDD 的流程：tasks 不再按層切，而是依情境切，每個 task 擁有同一個 feature 檔裡的一兩個情境，驗證指令就是用 tag 跑它們。

→ [Ch 31 把 BDD 放進 SDD：以 feature file 為粒度的 tasks](./31-bdd-in-sdd.md)
