# Ch 23 — 前端 E2E 先行：用 Playwright 驅動 UI 開發

> **目標**：在畫面還不存在時，先用 Playwright 寫出會失敗的瀏覽器規格，讓紅燈一步步把 UI「拉」出來。讀完你能說出 `get_by_role`、`get_by_label`、`get_by_test_id` 各自找什麼，auto-wait 與 web-first assertion 在 driver 裡怎麼重試，`sleep` 為什麼讓測試時好時壞；會用 trace 與截圖替紅燈附上證據，讓 agent 在「只准改 UI 檔」的約束下把規格做到綠，並分清楚 Playwright MCP、playwright-cli 這類探索工具和提交進 repo 的規格各自負責什麼。

> **環境**：macOS 26.6（arm64）、Python 3.12.15、Playwright for Python 1.64.1（Chromium 156.0.8078.4；無頭模式實際啟動 `chromium_headless_shell-1248`）、pytest 9.2.0、FastAPI 0.162.0、uvicorn 0.54.0、httpx 0.28.1、Claude Code 2.1.295；對照用的 pytest-playwright 0.10.0 裝在本章自己的 venv。除了標明「未實測」的段落，所有指令與輸出都是 2026-11-02 在這台機器上跑的，兩段 agent 紀錄是實際執行的節錄。

## 為什麼需要這個？

這門課之前，整個 repo 沒有一行瀏覽器測試：搜不到 `page.goto`、`get_by_role`、`data-testid`（本課 [Ch 0](./00-environment-setup.md) 的冒煙測試用過 `get_by_role`，只為確認環境能跑）。UI 的驗收靠兩樣東西：[web/firebase 練習 A](../../web/firebase/practice-a-login-page.md) 與 [練習 B](../../web/firebase/practice-b-realtime-board.md) 的「測試用例」表（「登入後按 F5」「開兩個分頁，一邊登出」，由人照著點），以及原則：[pstack Ch 21](../pstack/21-verification-skills.md)「一、create-verification-skill」要求 Drive 用穩定的 handle（ARIA label、data 屬性）、Evidence 截下動作與結果；[pstack Ch 31](../pstack/31-critique-tradeoffs.md)「九、pstack 之外」說已有 Playwright 套件的團隊，驗證技能應該**呼叫**它而不是重寫。原則都對，手上卻沒有能跑的套件。agent 讓這個洞更痛：[Ch 7](./07-messy-baseline.md) 讓 autopilot skill 一口氣做完整個預約 app，它的交接報告在「What wasn't verified」底下寫著（實際執行紀錄，2026-11-02）：「Browser: the page's JavaScript has never been run; the tests only check that the page is served with a form and all three rooms. Try one booking, one conflict and one cancel by hand.」

後端有測試，前端只是「看起來做完了」。瀏覽器自動化不新。Selenium 2004 年在 ThoughtWorks 誕生，後來併入它的 WebDriver 在 2018-06-05 成為 W3C Recommendation；Puppeteer（repo 建於 2017-05，v1.0.0 於 2018-01）透過 Chrome DevTools Protocol 操作瀏覽器；Playwright 的 repo 建於 2019-11，v1.0.0 於 2020-05，Python 版 2020-07 開工（日期取自 GitHub API）。這段歷史裡最難纏的是**時序**：按下按鈕後要等多久才檢查？常見答案是 `sleep` 或手寫等待，結果就是 Martin Fowler 2011 年描述的局面：時好時壞的測試（flaky test）開始被忽略，被忽略的測試等於不存在。Playwright 的回答是兩個設計：動作前先等元素可以操作（自動等待，auto-wait），斷言自己重試到成立（web-first assertion）。本章把 [Ch 22](./22-backend-e2e-first.md) 的 outside-in 外圈搬進瀏覽器：先寫會紅的 UI 規格，把 auto-wait 拆到 driver 原始碼，實測 `sleep` 有多不可靠，讓 agent 在約束下把 UI 做到綠，最後比較「探索」與「規格」。

## 先建立直覺：一位只聽螢幕閱讀器的使用者

想像一位看不到螢幕的使用者，只聽得到螢幕閱讀器念的「文字框，Room」「按鈕，Book」「狀態，Booked Taipei」。她不知道按鈕是不是藍色、在不在右上角，只認得「名字叫 Book 的按鈕」。她能完成預約，頁面的結構就是對的。Playwright 推薦的定位器（locator）用她的方式找元素：`get_by_role("button", name="Book")` 查無障礙樹（accessibility tree）裡角色是 button、可及名稱（accessible name）是 Book 的節點，不看 CSS class，也不看第幾個 `<div>`。改版型她照樣找得到；把按鈕改名，她和測試一起找不到，而這正是你想知道的事。比喻的邊界：她聽得到 `aria-label`，明眼人卻看不到。本章第一版 UI 只用 `aria-label` 標欄位，規格全綠，第七節的探索 agent 卻回報「四個空白輸入框，看不出哪個是哪個」。通過 role 與 label 規格證明的是「結構找得到」，不是「畫面看得懂」。

```
   UI 規格（外圈；先寫、先紅）  book(...)  expect(status).to_have_text("Booked Taipei")
         │ 紅：哪個元素找不到、哪個值不對 ＋ trace.zip、final.png（證據）
         ▼
   做 UI（人或 agent；只准改 booking/static/）
         ▼
        綠 ─────▶ 探索（playwright-cli / MCP / Chrome）
         ▲                    │ 新發現
         └──── 分流：釘成哪一層的規格？先寫紅燈
```

## 一、實驗室：後端已經綠，畫面還不存在

從空目錄 `/tmp/lab/aibdd/ch23-playwright/` 開始。要建的檔案：`booking/__init__.py`（空檔）、`booking/api.py`、`pytest.ini`、`run_ui_tests.sh`、`tests/conftest.py`、`tests/ui/test_book_ui.py`，以及之後才出現的 `booking/static/index.html` 和 `tests/demo/` 底下的對照實驗（Playwright 與 Chromium 的安裝、瀏覽器為什麼不在 venv 裡，見 [Ch 0](./00-environment-setup.md) 的「三、Playwright：瀏覽器不在 venv 裡」）。後端行為和 [Ch 22](./22-backend-e2e-first.md) 相同（R1 → 409、R2 → 422、`POST /reset`），下面給單檔版讓本章能獨立重建；`GET /` 在 UI 檔不存在時回 404：

```python
"""booking/api.py: the booking HTTP API (same behaviour as Ch 22) plus GET / serving the UI file."""
from datetime import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

UI = Path(__file__).parent / "static" / "index.html"
app = FastAPI()
BOOKINGS: list[dict] = []

class BookingIn(BaseModel):
    room: str
    start: time
    end: time
    by: str

@app.post("/bookings", status_code=201)
def book(b: BookingIn):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in BOOKINGS:
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    booking = {"id": len(BOOKINGS) + 1, **b.model_dump()}
    BOOKINGS.append(booking)
    return booking

@app.get("/bookings")
def list_bookings():
    return BOOKINGS

@app.post("/reset", status_code=204)
def reset():
    BOOKINGS.clear()

@app.get("/", response_class=HTMLResponse)
def index():
    if not UI.exists():
        raise HTTPException(404, "UI not built yet")
    return UI.read_text()
```

`pytest.ini` 三行：`[pytest]`、`pythonpath = .`、`testpaths = tests/ui`，前者讓 `from booking.api import app` 不必像 Ch 0 冒煙測試那樣設 `PYTHONPATH`，後者讓預設只跑規格。`run_ui_tests.sh`（記得 `chmod +x`）也是三行：`#!/bin/sh`、一行註解、`cd "$(dirname "$0")" && exec /tmp/lab/aibdd/.venv/bin/python -m pytest -q "$@"`。寫死 venv 的絕對路徑是為了 agent：[Ch 0](./00-environment-setup.md) 發現這台機器的使用者設定會覆寫 Claude Code 的 `PATH`，agent 的 Bash 看到的是系統的 Python 3.9。`tests/conftest.py` 是整章的骨架：

```python
"""tests/conftest.py: a live server, one browser per session, a fresh context (and trace) per test."""
import asyncio, os, random, socket, threading, time
from pathlib import Path
import httpx, pytest, uvicorn
from playwright.sync_api import expect, sync_playwright
from booking.api import app

TIMEOUT_MS = 2000  # the local server answers in milliseconds; 2 s keeps a red run short
expect.set_options(timeout=TIMEOUT_MS)

def with_latency(asgi, max_ms):  # test-only: delay every POST by 0..max_ms, like a slow network
    async def slowed(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            await asyncio.sleep(random.uniform(0, max_ms) / 1000)
        await asgi(scope, receive, send)
    return slowed

@pytest.fixture(scope="session")
def base_url():
    if os.environ.get("BASE_URL"):  # an already running dev server or preview environment
        yield os.environ["BASE_URL"]
        return
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    latency = int(os.environ.get("LATENCY_MS", "0"))
    server = uvicorn.Server(uvicorn.Config(with_latency(app, latency), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()

@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):  # remember whether the test body failed
    report = yield
    if report.when == "call":
        item.body_failed = report.failed
    return report

@pytest.fixture
def page(browser, base_url, request):
    httpx.post(base_url + "/reset")
    context = browser.new_context(base_url=base_url)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(TIMEOUT_MS)
    yield page
    if getattr(request.node, "body_failed", True) or os.environ.get("KEEP_EVIDENCE"):
        out = Path("test-results") / request.node.name
        page.screenshot(path=out / "final.png")
        context.tracing.stop(path=out / "trace.zip")
    else:
        context.tracing.stop()  # passed: discard the trace
    context.close()
```

- **`TIMEOUT_MS = 2000`**：Playwright 預設的動作逾時是 30000 ms、`expect` 是 5000 ms（原始碼裡的 `DEFAULT_PLAYWRIGHT_TIMEOUT_IN_MILLISECONDS = 30000` 與 `self._timeout or 5_000`）。本機 server 幾毫秒就回應、整套綠燈約 1 秒，2 秒讓一次紅燈幾秒內結束；打遠端 preview 或慢的 CI 機器時調高。
- **一個 browser、每個測試一個 context**：context 像全新的無痕視窗，cookie 與 storage 互不相通。熱啟動時我們量到 `launch` 約 60 ms、`new_context` 加 `new_page` 約 30 ms，所以每個測試開新 context 為的是**隔離**而不是速度；伺服器端狀態由 `POST /reset` 清掉。port 0 避開被佔用的 port（Ch 0 踩雷第 4 條），設了 `BASE_URL` 就改打已在跑的 dev server 或 preview 環境。
- **`LATENCY_MS` 與 hook wrapper**：`LATENCY_MS` 是測試專用的 ASGI 包裝，讓每個 POST 隨機延遲 0 到 N 毫秒，產品程式碼不動；第四節用它模擬慢網路。`pytest_runtest_makereport` 的 hook wrapper 記下測試本體有沒有失敗，`page` 收尾時只替失敗的測試留下 `trace.zip` 與 `final.png`；`KEEP_EVIDENCE=1` 則全部保留。

## 二、先寫會失敗的 UI 規格，讓紅燈把畫面拉出來

規格照全課共用的會議室預約情境寫（[Ch 1](./01-course-map.md)「貫穿全課的範例」）：空時段訂得到、重疊時看得到原因、訂好的會出現在清單上。它們寫在 UI 之前，用**使用者看得到的名字**（Room、Book、status），那就是 UI 的契約。下面是 `tests/ui/test_book_ui.py` 的前 24 行，第七節會再補一個：

```python
"""tests/ui/test_book_ui.py: UI specs written before the UI exists."""
import httpx
from playwright.sync_api import Page, expect

def book(page: Page, room, start, end, name):
    page.goto("/")
    page.get_by_label("Room").fill(room)
    page.get_by_label("Start").fill(start)
    page.get_by_label("End").fill(end)
    page.get_by_label("Name").fill(name)
    page.get_by_role("button", name="Book").click()

def test_booking_a_free_slot(page: Page):
    book(page, "Taipei", "10:00", "11:00", "Alice")
    expect(page.get_by_role("status")).to_have_text("Booked Taipei")

def test_overlap_is_rejected_with_the_reason(page: Page, base_url):
    httpx.post(base_url + "/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"})
    book(page, "Taipei", "10:30", "11:30", "Alice")
    expect(page.get_by_role("status")).to_have_text("Rejected: Taipei is taken 10:00-11:00 by Bob")

def test_a_confirmed_booking_is_listed(page: Page):
    book(page, "Taipei", "10:00", "11:00", "Alice")
    expect(page.get_by_test_id("booking-1")).to_have_text("Taipei 10:00-11:00 Alice")
```

重疊那個測試用 httpx 直接把 Bob 的預約放進去：**安排（Arrange）走 API，動作與斷言走 UI**。Bob 怎麼訂進去不是這個測試要驗的，走 UI 只會更慢、還讓「訂房」被測兩次；Alice 那一次一定走畫面，那是 UI 規格存在的理由。接著從沒有 `index.html` 開始，每次只補一小步、每步都重跑 `./run_ui_tests.sh`，看紅燈沿著旅程往下一格移動：

| UI 現在有什麼 | 第一個紅的地方（真實訊息節錄） | 結果 |
|---|---|---|
| 沒有 `index.html` | `Locator.fill: Timeout 2000ms exceeded`／`waiting for get_by_label("Room")` | 3 failed in 6.90s |
| 只有四個欄位 | `Locator.click: Timeout 2000ms exceeded`／`waiting for get_by_role("button", name="Book")` | 3 failed in 6.88s |
| 加上按鈕，沒有 script | `Locator expected to have text 'Booked Taipei'`，實際值是空字串 | 3 failed in 6.83s |
| 加上送出的 script | 清單那個：`Locator expected to have text 'Taipei 10:00-11:00 Alice'` | 1 failed, 2 passed in 3.01s |
| 加上清單 | — | 3 passed in 1.11s |

```
$ ./run_ui_tests.sh -k free_slot --tb=short | grep "^E "
E   AssertionError: Locator expected to have text 'Booked Taipei'
E   Actual value:  
E   Call log:
E     - Expect "to_have_text" get_by_role("status") with timeout 2000ms
E     - waiting for get_by_role("status")
E       21 × locator resolved to <p role="status"></p>
E          - unexpected value ""
…（略：Aria snapshot，四個 textbox、button "Book"、status，文字框裡都沒有值）
```

第三列最值得細看：`expect` 在 2 秒內檢查了 21 次，狀態列一直是空的；附帶的 aria snapshot 顯示四個文字框**是空的**，可是我們填過值。第五節的 trace 會回答：`<form>` 裡的 `<button>` 預設是 submit，沒有 script 攔截時，瀏覽器照 HTML 的老規矩送出 `GET /?room=Taipei&start=10%3A00&…`，整頁重新載入。「按了沒反應」其實是「按了，頁面重來一次」，下面的 `e.preventDefault()` 就是為此。**紅的原因要對。** 第一次紅燈說 `waiting for get_by_label("Room")`，但 server 回 500、路由打錯、頁面空白，都會得到同一句話。錯誤訊息說的是症狀，原因要看證據：第五節的 trace 顯示 `GET / 404`，截圖是 `{"detail":"UI not built yet"}`，這才確定「紅，是因為 UI 還沒做」。[Ch 25](./25-tdd-in-sdd-pipeline.md) 的紅燈閘門用 junit XML 區分對的紅與錯的紅；UI 規格的紅，多半要看 trace 才分得出來。最後讓三個規格全綠的 `booking/static/index.html`（33 行；第七節會在它身上找到 bug；用 `textContent` 而不是 `innerHTML`，名字裡的 HTML 只會被當成文字；API 回的時間是 `10:00:00`，所以清單用 `slice(0, 5)`；Ch 22 的版本回 `10:00`，兩種都適用）：

```html
<!doctype html>
<title>Room booking</title>
<form id=f>
  <input name=room aria-label=Room>
  <input name=start aria-label=Start>
  <input name=end aria-label=End>
  <input name=by aria-label=Name>
  <button>Book</button>
</form>
<p role=status></p>
<ul aria-label=Bookings></ul>
<script>
const status = document.querySelector("[role=status]");
const list = document.querySelector("ul");
async function refresh() {
  const rows = await (await fetch("/bookings")).json();
  list.replaceChildren(...rows.map((b) => {
    const li = document.createElement("li");
    li.dataset.testid = "booking-" + b.id;
    li.textContent = `${b.room} ${b.start.slice(0, 5)}-${b.end.slice(0, 5)} ${b.by}`;
    return li;
  }));
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
</script>
```

## 三、定位器：找使用者看得到的東西

| 定位器 | 找什麼 | 本章用在 | 什麼時候別用 |
|---|---|---|---|
| `get_by_role("button", name="Book")` | 無障礙樹裡的角色＋可及名稱 | 按鈕、狀態列 | 元素沒有語意角色 |
| `get_by_label("Room")` | 關聯到 `<label>`、`aria-labelledby` 或 `aria-label` 的輸入元素 | 四個欄位 | 不是表單欄位 |
| `get_by_test_id("booking-1")` | `data-testid`（可用 `selectors.set_test_id_attribute()` 換名） | 清單裡特定一筆 | 文字或角色本身就是要驗的東西 |
| `get_by_text("Book")` | 含有這段文字的元素 | 不用 | 找互動元素時幾乎都有更好的選擇 |

官方文件建議優先用面向使用者的屬性與明確的契約，例如 `get_by_role()`；test id 最耐改版，但「不是面向使用者的」。清單那一筆用 test id，是因為要指名「第 1 筆預約」而不是任何長得像它的列；[練習 D](./practice-d-cancel-booking.md) 的取消按鈕會在這個 handle 底下找。**文字定位器的陷阱**：`get_by_text` 預設不分大小寫、比對子字串。`tests/demo/test_selectors.py` 在同一頁先訂 Taipei、再訂 Tokyo，按鈕都用 `get_by_text("Book")` 找：

```python
from playwright.sync_api import expect

def test_book_twice_clicking_by_text(page):
    page.goto("/")
    for room in ["Taipei", "Tokyo"]:
        for label, value in [("Room", room), ("Start", "10:00"), ("End", "11:00"), ("Name", "Alice")]:
            page.get_by_label(label).fill(value)
        page.get_by_text("Book").click()
        expect(page.get_by_role("status")).to_have_text("Booked " + room)
```

```
$ ./run_ui_tests.sh tests/demo/test_selectors.py --tb=short | grep -E "^E   (play|    [12])|failed"
E   playwright._impl._errors.Error: Locator.click: Error: strict mode violation: get_by_text("Book") resolved to 2 elements:
E       1) <button>Book</button> aka get_by_role("button", name="Book")
E       2) <p role="status">Booked Taipei</p> aka get_by_text("Booked Taipei")
1 failed in 0.60s
```

Playwright 的定位器是嚴格的（strict mode）：第二次點擊命中兩個元素就報錯，不猜你要哪個，而且 0.6 秒就失敗、不等逾時，還順手建議 `get_by_role("button", name="Book")`。第六節 agent 做的版本多了 `<h1>Room booking</h1>` 和 `<h2>Bookings</h2>`，同一個測試在**第一次**點擊就命中 3 個元素：用文字找按鈕，壞不壞取決於頁面其他地方寫了什麼。`get_by_role` 的 `name` 預設同樣是不分大小寫的子字串比對，頁面上若有「Booking history」按鈕就要加 `exact=True`。**label 沒有關聯**也會被抓到：把 `aria-label=Room` 換成 `<label>Room</label> <input name=room>`（`label` 沒有 `for`、也沒包住 `input`），`free_slot` 得到 `Locator.fill: Timeout 2000ms exceeded`、`waiting for get_by_label("Room")`（`1 failed, 2 deselected in 2.47s`；看完改回 `aria-label`）。測試在替無障礙把關：螢幕閱讀器同樣不知道這個框叫 Room。

## 底層機制：auto-wait 與 web-first assertion 怎麼重試

Python 端只是 client，真正等待與重試的是 wheel 內附的 Node driver（[Ch 0](./00-environment-setup.md)「底層機制」）。直接讀 `playwright/driver/package/lib/coreBundle.js`：

```
 Python 測試                  Node driver（coreBundle.js）                   Chromium
 locator.click() ──pipe──▶  解析 selector；命中 >1 個 → strict mode 錯誤
                            _retryAction，每輪之間等 [0, 20, 100, 100, 500] ms：
                              checkElementStates(visible, enabled, stable) → 捲進可視範圍
                              → hit target：點擊點上是不是它本人？ ──CDP──▶ 滑鼠事件
 expect(loc).to_have_text() ▶ 先檢查一次，再 retryWithProgressAndBackoff：間隔 [20, 50, 100, 100, 500]，
                              大於 timeout/5 的級距拿掉、前面補 0、最後一級一直重複，每輪重新解析 locator；
                              逾時 → 回傳最後的值、call log、aria snapshot
```

`fill` 檢查 visible、enabled、editable；`click` 檢查 visible、enabled、stable 再加 hit target（官方 Auto-waiting 文件對 stable 的定義：連續兩個 animation frame 的 bounding box 不變）。在第二節 `index.html` 的 `</form>` 後面暫時加一行 `<div style="position:fixed;inset:0">Cookie banner</div>`（一條蓋住整頁、沒關掉的橫幅）再跑 `free_slot`：四次 `fill` 都過了，因為 `fill` 不檢查 hit target；紅的是 `Locator.click: Timeout 2000ms exceeded`（`1 failed, 2 deselected in 2.41s`）：

```
$ ./run_ui_tests.sh -k free_slot --tb=short | grep "^E "
…（略：TimeoutError 那行、Call log、waiting for 與 locator resolved to <button>Book</button>）
E     - attempting click action
E       2 × waiting for element to be visible, enabled and stable
E         - element is visible, enabled and stable
E         - scrolling into view if needed
E         - done scrolling
E         - <div>Cookie banner</div> intercepts pointer events
E       - retrying click action
E       - waiting 20ms
…（略：同樣的檢查再兩輪，中間是 waiting 100ms、waiting 500ms）
```

拿掉那行 `<div>`、改把按鈕寫成 `<button disabled>`，每一輪就變成 `element is not enabled`；看完記得改回去。`expect` 的 backoff 規則有個違反直覺的後果：**逾時越長，不代表檢查越密**。`poll_count.py` 對一個永遠不會出現的文字給三種逾時，數 call log 的「N × locator resolved」：1000 ms 檢查 11 次、2000 ms 檢查 21 次、5000 ms 只檢查 14 次。5 秒時 500 沒被拿掉（500 ≤ 5000/5），前 270 ms（0＋20＋50＋100＋100）之後每 500 ms 才看一次，狀態已經對了，它最多晚半秒才發現。web-first assertion 是輪詢（polling）不是事件通知，保證「最終會看到」，不保證「立刻看到」。也因為每輪都重新解析 locator，元素被重新渲染成新節點也沒關係，不會有 Selenium 那種拿著舊參考的 stale element 錯誤。

## 四、sleep 對 auto-wait：同一個測試跑二十次

`tests/demo/test_waiting.py` 用三種寫法等狀態列，各跑 20 次：

```python
import pytest
from playwright.sync_api import expect

def submit(page):  # same journey as book() in tests/ui/test_book_ui.py
    page.goto("/")
    for label, value in [("Room", "Taipei"), ("Start", "10:00"), ("End", "11:00"), ("Name", "Alice")]:
        page.get_by_label(label).fill(value)
    page.get_by_role("button", name="Book").click()

@pytest.mark.parametrize("run", range(20))
def test_read_right_away(page, run):
    submit(page)
    assert page.get_by_role("status").inner_text() == "Booked Taipei"

@pytest.mark.parametrize("run", range(20))
def test_sleep_300ms_then_read(page, run):
    submit(page)
    page.wait_for_timeout(300)
    assert page.get_by_role("status").inner_text() == "Booked Taipei"

@pytest.mark.parametrize("run", range(20))
def test_web_first_assertion(page, run):
    submit(page)
    expect(page.get_by_role("status")).to_have_text("Booked Taipei")
```

`LATENCY_MS=0 ./run_ui_tests.sh tests/demo/test_waiting.py --tb=no` 的最後一行是 `20 failed, 40 passed in 13.88s`，`LATENCY_MS=600` 的一次是 `34 failed, 26 passed in 37.42s`（延遲是亂數，每次不同；驗證時重跑一次是 `30 failed, 30 passed in 39.15s`）。每個失敗的測試在 `test-results/` 留一個目錄；每次跑之前先 `rm -rf test-results`（否則前幾節留下的目錄也會被算進去），跑完用 `ls test-results | sed 's/\[.*//' | sort | uniq -c` 數出各寫法的失敗數：

| 寫法 | `LATENCY_MS=0` | `LATENCY_MS=600` |
|---|---|---|
| 點完立刻讀 `inner_text()` | 20/20 失敗 | 20/20 失敗 |
| `wait_for_timeout(300)` 再讀 | 0/20 失敗 | 10–14/20 失敗（七次） |
| `expect(...).to_have_text(...)` | 0/20 失敗 | 0/20 失敗 |

第一種在零延遲下也全紅：`click()` 回來時送出事件已派發，`fetch` 還沒回應；`inner_text()` 只等元素**存在**，不等文字**變對**。第二種最危險：零延遲時 20 次全綠，所以會被 merge；延遲 0 到 600 ms 時約一半的請求超過 300 ms，我們七次執行分別失敗 11、11、10、11、11、14、10 次。失敗訊息只有 `AssertionError: assert '' == 'Booked Taipei'`，沒有 call log、沒有時間線索，在 CI 上只會讓人想「重跑就好」；成功時它也白等 300 ms。Playwright 在 `wait_for_timeout` 的說明寫：只該用於除錯，用計時器的測試在正式環境「are going to be flaky」；Fowler 寫「Never use bare sleeps to wait for asynchonous responses: use a callback or polling.」web-first assertion 就是內建的輪詢。

## 五、證據：trace 與截圖

回到第一次紅燈。之後每次 `free_slot` 紅都會覆寫同一個目錄，所以先把 `index.html` 暫時移開、`rm -rf test-results`，再跑一次 `./run_ui_tests.sh -k free_slot`。`ls test-results/test_booking_a_free_slot` 列出 `final.png` 與 `trace.zip`；截圖是 1280×720，畫面是 Chromium 的 JSON 檢視：`{"detail":"UI not built yet"}`。`trace.zip` 用 Playwright 1.64 附的文字版 trace 工具讀，不必開 GUI，agent 也讀得懂。在那個目錄先 `python -m playwright trace open trace.zip`（印出摘要，其中一行是 `Playwright: 1.64.0-beta-1791568591000`，內附 driver 的版本字串），再看動作與請求：

```
$ python -m playwright trace actions
     # Time       Action                                                  Duration
  ──── ─────────  ─────────────────────────────────────────────────────── ────────
    1. 0:00.001  Create page                                                 69ms
    2. 0:00.072  Navigate                                                    20ms
                 /
    3. 0:00.094  Fill "Taipei"                                               2.0s  ✗
                 getByLabel('Room')
    4. 0:02.183  Screenshot                                                  11ms
$ python -m playwright trace requests
     # Start     Method   Status   Name                                            Duration     Size Route
  ──── ───────── ──────── ──────── ───────────────────────────────────────────── ────────── ──────── ──────────
    1. 0:00.078  GET      404      127.0.0.1:49617                                      5ms      161
```

`page.goto` 沒有因為 404 失敗，這是設計：它的說明寫明，伺服器回任何有效的 HTTP 狀態碼（包括 404、500）都不丟例外，所以失敗落在下一個動作，trace 把原因補回來。`trace open` 把內容解到目前目錄的 `.playwright-cli/`，`trace close` 清掉；同一組還有 `errors`、`console`、`snapshot`、`screenshot`。第二節「按鈕沒有 script」那一輪，`trace requests` 的第二列是 `GET 200 127.0.0.1:55878?room=Taipei&start=10%3A00&...`，原生表單送出就是這樣抓到的；那份 trace 的動作列表裡還有一列 `Expect "to_have_text"`。圖形介面是 `python -m playwright show-trace test-results/<test>/trace.zip`（時間軸、動作前後的 DOM 快照、network、console、原始碼）。本章在終端機寫作，**GUI 沒有實測**；官方文件說也可以把檔案丟進 trace.playwright.dev，資料只在你的瀏覽器裡載入、不會傳出去。成功的旅程也能留證據：把 `index.html` 放回來（第二節的全綠狀態），跑 `KEEP_EVIDENCE=1 ./run_ui_tests.sh`，得到 `3 passed in 1.03s`，三個測試各留下 `final.png` 與 `trace.zip`。這就是 [pstack Ch 21](../pstack/21-verification-skills.md) 的 Evidence 標準：截下動作與結果，放在 cleanup 不會刪的地方，交接時附上。想讓 agent 自己讀 trace，`python -m playwright trace install-skill` 會把 174 行的 `playwright-trace` skill 複製到**目前目錄**的 `.claude/skills/playwright-trace/`（我們在 lab 暫存目錄試過，`~/.claude` 沒有被動到）。注意它是照 Node 版寫的：內文 37 處都是 `npx playwright trace`，`allowed-tools` 也是 `Bash(npx playwright trace:*)`；只裝了 Python 的專案要把它改成 `python -m playwright trace`。

## 六、讓 agent 把 UI 做到綠

規格會說下一步，這正是 agent 需要的回饋（[Ch 3](./03-propose-constrain-feedback.md) 的提案、約束、回饋）。我們把 lab 複製成 `agent-run/`、拿掉 `index.html`、用 `shasum` 記下另外五個檔案，只給 agent 三種權限：讀檔、改 `booking/static/` 底下的檔案、執行 `./run_ui_tests.sh`。交給它的規格檔有**四個**測試：實際順序是我們先做了第七節的 CLI 探索、補上 `test_a_missing_field_is_named`（它的註解寫著 `[object Object]`，agent 讀得到），才跑這一段。下面是 `run1-agent-ui.sh` 的核心（前面只有 `set -eu`、`LAB=/tmp/lab/aibdd/ch23-playwright` 與 `cd "$LAB/agent-run"`）：

```sh
claude -p "The UI specs in tests/ui/test_book_ui.py fail because booking/static/index.html does not exist yet. Create booking/static/index.html (plain HTML with inline JavaScript, no build step, no external files) so that every spec passes. The HTTP API in booking/api.py already works. Do not change the API, the tests or the test configuration. Run ./run_ui_tests.sh to check your work; you are done when it reports that all tests passed." \
  --setting-sources project \
  --strict-mcp-config \
  --tools "Read,Glob,Grep,Edit,Write,Bash" \
  --allowedTools "Read" "Glob" "Grep" "Edit(/booking/static/**)" "Bash(./run_ui_tests.sh *)" \
  --permission-prompts none \
  --max-turns 20 \
  --max-budget-usd 4 \
  --no-session-persistence \
  --output-format stream-json --verbose \
  > "$LAB/transcripts/run1-agent-ui.jsonl" && rc=0 || rc=$?
```

官方權限文件寫明 `Write(path)` 這類規則**永遠不會比對成功**，限制寫入路徑要用 `Edit(...)`，它涵蓋所有會改檔的內建工具；在 CLI 旗標裡 `/booking/...` 以工作目錄為錨點。`--permission-prompts none` 讓其他需要核准的動作直接被拒，`--setting-sources project` 避開使用者設定裡的 `PATH`。執行時 CLI 先印了 `Warning: no stdin data received in 3s, proceeding without it`，建議加 `< /dev/null`；第七節的腳本加上了。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，帳號預設模型 claude-opus-5-5）。

用 `jq` 從 stream-json 抽出的工具呼叫依序是：`Bash`（`ls`、`cat run_ui_tests.sh`）→ `Read` 四次（規格、`api.py`、`conftest.py`、`pytest.ini`）→ `Write booking/static/index.html` → `Bash`（`./run_ui_tests.sh … ; for i in 1 2 3; do LATENCY_MS=800 ./run_ui_tests.sh …`，被拒）→ `Bash ./run_ui_tests.sh`（`4 passed`）。結果：`success`、9 turns、46.7 秒、`total_cost_usd` 0.191、1 次權限拒絕。agent 讀到 conftest 的 `LATENCY_MS`，想用 800 ms 延遲連跑三次，直覺很好；但那一行把 `tail`、`echo`、`for` 迴圈與 `LATENCY_MS=800` 賦值串在一起，拒絕訊息逐一列出不在 `Bash(./run_ui_tests.sh *)` 範圍內的部分（文件也寫明 allow 規則不會越過任意變數的賦值去比對）。它的交接報告把這件事放在最前面：「**Not tested:** … The specs have therefore only run with no added latency.」我們接手驗收：`shasum -c ../agent-run.before.sha` 五個檔案全是 `OK`；`./run_ui_tests.sh` 得到 `4 passed in 1.36s`；補上被擋的那項，`LATENCY_MS=800` 連跑三次是 `4 passed in 4.67s`、`4.37s`、`4.29s`。三個觀察。**規格約束行為，不約束實作**：agent 的 `index.html` 有 119 行，用了看得見的 `<label for>`、`type="time"` 欄位、送出期間停用按鈕、丟棄過期的清單刷新，四個規格同樣全綠。**規格沒管的事要有人管**：`type="time"` 讓使用者打不出 `9:00`，這該由 PM 決定。**約束要和工具一起設計**：想讓 agent 自己做延遲測試，給 `run_ui_tests.sh` 一個參數，比放寬 Bash 規則安全；更完整的防竄改（hook 擋改測試、拿 red commit 比對每個斷言的原文）見 [Ch 20](./20-tdd-with-agents.md)。

## 七、探索不是規格：playwright-cli、Playwright MCP、Claude in Chrome

規格回答「我們知道的行為還在嗎」，探索式測試（exploratory testing）回答「有沒有我們還不知道的問題」。Playwright 1.64.1 的 Python 套件內附兩個探索工具：`python -m playwright cli`（附一份給 agent 的 SKILL.md）與 `python -m playwright mcp`（MCP，即 Model Context Protocol 的 server）。**先不用 LLM，拿 CLI 探一次。** 對第二節那版 UI，另開一個終端機在 lab 目錄跑 `python -m uvicorn booking.api:app --port 0`（拿到 51654），再 `python -m playwright cli open http://127.0.0.1:51654/ --browser chromium`（為什麼加 `--browser` 見本段最後），頁面快照存成 YAML：

```
$ cat .playwright-cli/page-*.yml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - textbox "Room" [ref=e3]
    - textbox "Start" [ref=e4]
    - textbox "End" [ref=e5]
    - textbox "Name" [ref=e6]
    - button "Book" [ref=e7]
  - status
  - list "Bookings"
```

這就是 `get_by_role` 查的那棵無障礙樹，`e3` 這類 ref 是下一個指令用的 handle。依序（省略前綴 `python -m playwright`）`cli fill e3 Taipei`、`fill e4 10:00`、`fill e6 Alice`（End 留空）、`cli click e7`，每個動作都印出它執行的 Python 程式碼，例如 `page.get_by_role("button", name="Book").click()`；點完後的快照裡是 `status [ref=e8]: "Rejected: [object Object]"`。API 對 R2 回的 `detail` 是字串，FastAPI 自己的欄位驗證失敗時卻是物件陣列（空的 `end` 得到 `time_parsing`、`input is too short`），UI 直接把陣列接到字串後面。`cli generate-locator e8` 回 `getByText('Rejected: [object Object]')`：工具記下的是**現在的文字**，連 bug 一起。最後說 `--browser chromium`：CLI 和 MCP 預設用 `chrome` 通道，這台機器上就是 `/Applications` 裡的 Google Chrome 156.0.8078.76（從程序路徑確認），不是內附的 Chromium；兩者都接受 `--browser chromium`（`--help` 只列出 chrome、firefox、webkit、msedge，原始碼把 `chromium` 對應到 `chrome-for-testing` 通道），加上後啟動的是內附的 `chromium-1248`，和規格同一個 Chromium 版本（規格在無頭模式跑的是同版的 `chromium_headless_shell-1248`）。**再讓 agent 透過 MCP 探索。** 我們用一支小腳本 `mcp_tools.py` 直接走 stdio JSON-RPC（`initialize`、`tools/list`），問出內附的 MCP server 有 25 個工具、schema 合計 21,956 bytes；Claude Code 預設以 tool search 延遲載入 MCP 工具定義（官方 MCP 文件）。`browser_navigate` 只回 345 個字元加上快照檔的路徑，`browser_snapshot` 才把整棵樹放進回應。順帶校正兩處：[AI 應用 Ch 12](../ai_applications/12-using-mcp.md)「常見 MCP server 配置」寫的 `@modelcontextprotocol/server-playwright`，`npm view` 回 404，官方套件是 `@playwright/mcp`（2025-03-13 首次發布），Python wheel 內附的是同一個 server；[AI 應用 Ch 6](../ai_applications/06-claude-code-advanced.md) 把 `mcpServers` 寫在 `.claude/settings.json`，現行文件則是專案層的 `.mcp.json` 或 `~/.claude.json`，下面用 `--mcp-config` 明確指定。`run2-explore-mcp.sh` 和第六節同一套約束，差別是：prompt 要它像細心的使用者一樣試正常預約、重疊預約與至少三種缺漏或錯誤的輸入，不准改檔，逐條回報「做了什麼 → 頁面上的原文 → 為什麼是問題」；旗標換成 `--mcp-config mcp.json --strict-mcp-config --tools "Read" --allowedTools "mcp__playwright__*" --max-budget-usd 3`，前面加 `ENABLE_TOOL_SEARCH=false`（`--tools "Read"` 可能連 tool search 需要的工具一起拿掉，我們乾脆讓 MCP 工具一開始就載入；另一種組合沒有試）、結尾加 `< /dev/null`。`mcp.json` 只有一個 server：`/tmp/lab/aibdd/.venv/bin/python -m playwright mcp --headless --isolated --browser chromium`（`--isolated` 讓 profile 只存在記憶體）。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，帳號預設模型 claude-opus-5-5）。

agent 呼叫了 10 次工具：navigate、snapshot、截圖、console、用 `browser_evaluate` 讀整頁 HTML、network，再用三次 `browser_run_code_unsafe` 批次送出表單（報告說共 38 次），最後再截一次圖。結果：11 turns、84.9 秒、0.397 美元、0 次拒絕。報告列了 10 個問題，節錄五個：

```
3. Booked `a`, `A ` and ` A` at 09:00-10:00 while `A` was already taken at that time -> `Booked a`, `Booked A `, `Booked  A` -> The overlap check treats these as different rooms. …
4. Left Start, End or everything empty -> `Rejected: [object Object]` …
6. Entered `09:00Z` as Start and `10:00` as End … -> no message at all -> The server returns a 500 error and the status line never updates …
7. Double-clicked Book on a free slot in room R -> `Rejected: R is taken 09:00-10:00 by Dana` -> The first click succeeded, but the user is told it failed …
9. Opened the page -> four empty boxes and `Book`, with no visible text -> Labels exist only for screen readers …
```

agent 的報告不是證據，我們重現了兩條。`curl` 送 `"start":"09:00Z"` 得到 `Internal Server Error  <- HTTP 500`，server log 是 `TypeError: can't compare offset-naive and offset-aware times`（pydantic 把 `09:00Z` 解析成帶時區的時間）。Playwright `dblclick()` 之後，狀態列是 `'Rejected: Taipei is taken 10:00-11:00 by Alice'`，清單卻只有一筆 `Taipei 10:00-11:00 Alice`：使用者被告知自己的預約擋住了自己。85 秒、不到半美元，找到四個規格沒碰過的問題，這是探索的價值。它也示範了探索為什麼不能當規格：每次走的路徑不同；它自己報告在沒有 reset 的共用 server 上留下 17 筆測試資料；它用的 `browser_run_code_unsafe` 在工具描述裡自稱「RCE-equivalent」，而 `mcp__playwright__*` 這條萬用規則也放行了它，要收緊就逐一列出要給的工具。

| 發現 | 釘在哪一層 | 誰決定 |
|---|---|---|
| 4 `[object Object]` | UI 規格（下面就做） | RD |
| 6 `09:00Z` → 500 | API 測試（[Ch 22](./22-backend-e2e-first.md) 那一層）＋「伺服器出錯要有訊息」的 UI 規格 | RD |
| 7 連點兩下 | UI 規格，釘在原因上（見「進階」） | RD |
| 3 `a`、`A `、` A` 算不同會議室 | 先問業務規則：會議室是固定清單嗎？（[Ch 29](./29-example-mapping.md) 的問題卡） | PM＋RD |
| 9 看不見的欄位名稱 | 改成看得見的 `<label for>`，`get_by_label` 照樣通過 | 設計＋RD |

**把第 4 條釘成規格。** 先寫紅燈，接在 `test_book_ui.py` 後面：

```python
def test_a_missing_field_is_named(page: Page):  # found by exploration: "Rejected: [object Object]"
    book(page, "Taipei", "10:00", "", "Alice")
    expect(page.get_by_role("status")).to_have_text("Rejected: End is required")
```

對第二節那版 UI 跑，紅的原因正是探索看到的：`Actual value: Rejected: [object Object]`，`1 failed, 3 passed in 3.45s`。修正只動 script：空欄位在送出前就擋下，其他驗證錯誤（像 `9:00`）把 FastAPI 的陣列轉成「欄位: 訊息」。套用下面的 `diff -U0` 之後，`./run_ui_tests.sh` → `4 passed in 0.81s`：

```diff
@@ -14,0 +15,2 @@
+const LABELS = {room: "Room", start: "Start", end: "End", by: "Name"};
+const reason = (d) => typeof d === "string" ? d : d.map((e) => `${LABELS[e.loc.at(-1)]}: ${e.msg}`).join("; ");
@@ -25,0 +28,3 @@
+  const data = Object.fromEntries(new FormData(f));
+  const missing = Object.keys(LABELS).find((k) => !data[k].trim());
+  if (missing) return void (status.textContent = `Rejected: ${LABELS[missing]} is required`);
@@ -27 +32 @@
-                                      body: JSON.stringify(Object.fromEntries(new FormData(f)))});
+                                      body: JSON.stringify(data)});
@@ -29 +34 @@
-  status.textContent = r.ok ? "Booked " + j.room : "Rejected: " + j.detail;
+  status.textContent = r.ok ? "Booked " + j.room : "Rejected: " + reason(j.detail);
```

**Claude in Chrome（本段未實測，為理論預期行為）。** `claude --chrome` 透過瀏覽器擴充功能操作你**自己的** Chrome：官方文件說它在看得見的視窗裡動作、共用你瀏覽器的登入狀態、遇到登入頁或 CAPTCHA 會停下來請你處理。這讓它適合探索需要你 session 的 staging，也正是它不適合當回歸測試的理由：結果綁在你的帳號與 cookie 上。我們沒有在這個 lab 跑它，因為它會操作這台機器上真正在用的瀏覽器；要驗證，照官方 Chrome 文件裝好擴充功能，`/chrome` 確認狀態是 Enabled，再請它打開你本機的 dev server 試一次表單。

## 對比與取捨

| 方式 | 回答的問題 | 每次結果可比較 | 本章實測的成本 | 適合 |
|---|---|---|---|---|
| 手動「測試用例」表 | 有人照表點過一次嗎 | 否 | 人的時間 | 還沒自動化的一次性驗收 |
| 提交的 Playwright 規格（本章 fixture） | 我們知道的行為還在嗎 | 是 | 4 個規格約 1 秒 | 回歸、agent 的回饋迴圈、CI |
| pytest-playwright 0.10.0 | 同上，用旗標取代自寫 fixture | 是 | 同 4 個規格 `4 passed in 2.83s` | 實際專案（video、多瀏覽器、device 模擬） |
| playwright-cli 探索 | 頁面上現在有什麼 | 否 | 每步數秒，不用 LLM | 摸索頁面、產生定位器草稿 |
| Playwright MCP＋agent | 有沒有沒想到的問題 | 否 | 11 turns、85 秒、0.40 美元 | 發現未知問題 |
| Claude in Chrome（未實測） | 登入後的真實環境長怎樣 | 否 | — | 需要你 session 的 staging |

pytest-playwright 那列是在本章的 `.venv-plugin/` 跑的（不動共用 venv）：conftest 只剩 live server 的 `base_url` 與一個 autouse 的 reset，`pytest.ini` 加 `addopts = --tracing retain-on-failure --screenshot only-on-failure`，規格一字不改；把 `"Booked "` 改成 `"Saved "` 得到 `1 failed, 3 passed`，證據在 `test-results/tests-ui-test-book-ui-py-test-booking-a-free-slot-chromium/` 的 `test-failed-1.png` 與 `trace.zip`。它的原始碼用的是同一招：`pytest_runtest_makereport` hook wrapper 記下失敗。本課自己寫 fixture，是因為共用 venv 只裝 Ch 0 那 7 個套件，也因為 20 行 fixture 讓機制看得見；實際專案用 plugin。

## 踩雷集錦

1. **以為 `inner_text()` 會等文字變對**：它只等元素存在，點完立刻讀，零延遲下也 20/20 失敗。正確認識：讀值再 `assert` 是快照；要等的狀態用 `expect(...).to_have_text(...)`。
2. **以為 sleep 夠長就好**：零延遲時 `wait_for_timeout(300)` 20 次全綠所以被 merge，延遲一來 20 次裡失敗 10 到 14 次，訊息只有 `assert '' == 'Booked Taipei'`。正確認識：sleep 是在賭時間，輸了也不留線索；等「狀態」，不等「時間」。
3. **用文字找按鈕**：`get_by_text("Book")` 第二次預約撞上 `Booked Taipei`，在 agent 那版 UI 第一次就撞上兩個標題。正確認識：互動元素用 `get_by_role` 加 name，需要時加 `exact=True`。
4. **以為 `get_by_label` 綠了，標籤就沒問題**：只有 `aria-label` 時規格與螢幕閱讀器都滿意，明眼人卻看不到欄位名稱（探索第 9 條）；沒有 `for` 的 `<label>` 則讓規格紅。正確認識：label 規格驗的是「找得到」；看得見的 `<label for>` 兩邊都顧到。
5. **以為 web-first assertion 驗的是最終狀態**：它在逾時內**任何一刻**成立就通過。「進階」的連點實驗裡它 10 次通過 8 到 9 次，使用者一秒後看到的卻都是 `Rejected`。正確認識：對會被覆寫的狀態，改等一個會留下來的狀態，或直接驗原因。
6. **把探索產生的定位器貼進規格**：`generate-locator` 給的是 `getByText('Rejected: [object Object]')`，把 bug 的文字釘成期望值。正確認識：探索產生草稿，規格裡的定位器與期望值由人決定。

## 進階：釘住競態、放進 CI

**連點兩下。** 直覺寫法是 `dblclick()` 之後 `expect(status).to_have_text("Booked Taipei")`。我們在 `transient-demo/` 用第二節那版 UI、`LATENCY_MS=600` 跑 10 次，每次斷言通過後再等一秒印出狀態列：結果 `1 failed, 9 passed in 18.83s`（延遲是亂數；驗證時另跑兩次是 `2 failed, 8 passed` 與 `1 failed, 9 passed`），而每次通過後印出的全是 `Rejected: Taipei is taken 10:00-11:00 by Alice`。201 先回來時狀態列真的短暫顯示過 `Booked Taipei`，隨後到的 409 才蓋掉它。這種測試在 bug 還在時是綠的。改法是先等一個會留下來的狀態（`expect(page.get_by_test_id("booking-1")).to_have_text(...)`），再驗**原因**：用 `page.on("request", ...)` 收集 POST，斷言只有一個。修正前的 UI 在 `LATENCY_MS=0` 與 `600` 下都得到 `AssertionError: one double-click sent 2 bookings`（`1 failed in 0.86s`、`1.04s`），每次都在同一行、為同一個原因紅；agent 那版送出期間停用按鈕，兩種延遲都 `1 passed`。**放進 CI（本段未實測：沒有在 GitHub Actions 上跑過，為理論預期行為）。** 照 [cicd Ch 6](../../dev_tools/cicd/06-workflow-structure.md) 的結構寫 `.github/workflows/ui-specs.yml`；action 版本是 2026-11-02 用 `gh api` 查到的最新主版號（那一章的 `checkout@v4` 已經落後）。本機驗證過的部分：PyYAML 解析出 6 個 step（它把 `on` 讀成布林 `True`，這是 YAML 1.1 的老問題；GitHub Actions 自己的解析器把它當觸發條件，所有 workflow 都這樣寫）；`install --dry-run --only-shell chromium` 只列出 Chrome Headless Shell 與 FFmpeg，不下載完整 Chromium，因為規格全在無頭模式跑；`python -m pytest -q` 就是 `run_ui_tests.sh` 跑的同一行。失敗時上傳 `test-results/`，PR 上的人和 agent 都能下載 trace，用第五節的指令讀；artifact 的細節見 [cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md)。

```yaml
name: ui-specs
on: pull_request
jobs:
  ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: {python-version: "3.12"}
      - run: pip install "pytest==9.2.0" "fastapi==0.162.0" "uvicorn==0.54.0" "httpx==0.28.1" "playwright==1.64.1"
      - run: python -m playwright install --with-deps --only-shell chromium
      - run: python -m pytest -q
      - if: failure()
        uses: actions/upload-artifact@v7
        with: {name: ui-evidence, path: test-results/}
```

**Firebase 專案（本段未實測：這台機器沒有 firebase CLI）。** 練習 A 的表可以逐列翻成規格：「登入後按 F5」是 `page.reload()` 再 `expect`；「開兩個分頁，一邊登出」是同一個 context 的兩個 `new_page()`（共用 storage）；練習 B 的「兩視窗，A 發言，B 不刷新即看到」是兩個 `browser.new_context()`（兩個使用者），B 那頁的 `expect` 會一直重試到 `onSnapshot` 把留言推過來。整套用 [web/firebase Ch 21](../../web/firebase/21-emulator-suite.md) 的 `firebase emulators:exec` 包起來跑，`BASE_URL` 指向你的本機 dev server。

## 動手練習

1. **定下 `9:00` 的訊息**：修正後的 UI 對 `9:00` 顯示 `Rejected: Start: Input should be in a valid time format, input is too short`（我們用 CLI 探到的）。替使用者寫一句更好的話，先寫紅燈規格，再改 UI。
2. **讓參考 UI 通過連點規格**：照「進階」的描述寫出等 `booking-1`、數 POST 的規格，確認它紅，再改 `index.html` 直到綠，最後用 `LATENCY_MS=800` 連跑三次。
3. **探索第 6 條**：先在 API 層寫一個 `09:00Z` 會失敗的測試，決定該回 422 還是接受時區；再寫「伺服器出錯時狀態列要有訊息」的 UI 規格。
4. **只用文字工具除錯**：把 `"Booked "` 改成 `"Saved "`，跑規格，只靠 `trace actions`、`trace errors` 與 `final.png` 指出哪一步、為什麼紅。

## 本章重點整理

- UI 規格先寫、先紅：用使用者看得到的名字（role、label）當契約，每補一小步就看紅燈往旅程下一格移動；安排走 API，動作與斷言走 UI。
- auto-wait 與 web-first assertion 都是 driver 裡的輪詢：動作每輪檢查 visible、enabled、stable 與 hit target，斷言按 backoff 重新解析 locator；它們保證「逾時內某一刻成立」，不保證「最終狀態」；`sleep` 與「讀值再 assert」則是在賭時間，零延遲下看不出來，延遲一來就時好時壞。
- 紅燈要附證據：失敗才留 `trace.zip` 與截圖，`playwright trace` 讓人和 agent 不開 GUI 也讀得到原因。
- agent 在「只准改 UI 檔、只能跑測試腳本」的約束下能把規格做到綠；收尾要自己驗：檔案沒被改、規格重跑、補上它被擋下的檢查。
- 探索（CLI、MCP、Chrome）找未知問題，規格釘住已知行為；發現要分流到正確的層，有些要先問 PM。

## 自我檢核

- [ ] 不看本章，說出 `click` 與 `fill` 各等哪些條件，哪一個多了 hit target 檢查、它防的是什麼情境。
- [ ] 為什麼 5 秒逾時的 `expect` 只檢查 14 次，2 秒的反而檢查 21 次？這對「斷言多快看到變化」有什麼影響？
- [ ] 一個 `sleep(300)` 的 UI 測試在你筆電上連跑 50 次全綠，你怎麼說服同事它仍有問題？用本章哪個實驗？
- [ ] 第一次紅燈只說 `waiting for get_by_label("Room")`，你怎麼確認它是「對的紅」？需要哪兩份證據？
- [ ] 探索 agent 回報 10 個問題，你怎麼決定哪些變成 UI 規格、哪些往下到 API、哪些先問 PM？各舉一例。

## 延伸閱讀

- **Playwright for Python 官方文件：[Locators](https://playwright.dev/python/docs/locators)、[Auto-waiting](https://playwright.dev/python/docs/actionability)、[Trace viewer](https://playwright.dev/python/docs/trace-viewer)**（Microsoft）
  - **讀哪裡**：Locators 開頭的內建定位器清單與「Locate by test id」「Strictness」；Auto-waiting 的動作與檢查對照表、Stable 與 Receives Events 的定義；Trace viewer 的「Recording a trace」「Using trace.playwright.dev」
  - **學到什麼**：為什麼優先用面向使用者的定位器、test id 為什麼耐改版卻「不是面向使用者的」；每個動作等哪些條件；GUI 版 trace viewer 每個分頁看什麼
  - **和本章的關聯**：第三節與「底層機制」的官方依據；本章只實測了文字版 trace 工具，GUI 留給你自己開
- **[Martin Fowler — Eradicating Non-Determinism in Tests](https://martinfowler.com/articles/nonDeterminism.html)**（martinfowler.com，2011-04-14）
  - **讀哪裡**：「Why non-deterministic tests are a problem」與「Asynchronous Behavior」兩節
  - **學到什麼**：時好時壞的測試為什麼比沒有測試更糟（團隊學會忽略紅燈），以及用 callback 或輪詢取代 sleep
  - **和本章的關聯**：第四節量的就是這件事；文章早於 Playwright，講原理不講工具
- **Testing Library：[Which query should I use?](https://testing-library.com/docs/queries/about/) 與 [Guiding Principles](https://testing-library.com/docs/guiding-principles)**
  - **讀哪裡**：「Priority」排序（`getByRole` 第一、`getByTestId` 最後），以及引自 Kent C. Dodds 的「The more your tests resemble the way your software is used, the more confidence they can give you.」
  - **學到什麼**：「用使用者的方式找元素」的理由，以及 test id 為什麼排最後
  - **和本章的關聯**：「只聽螢幕閱讀器的使用者」這個比喻背後的原則；Playwright 的 `get_by_*` 命名與優先順序幾乎一一對應
- **[microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)**（Microsoft，GitHub README）
  - **讀哪裡**：開頭的定位說明，以及 MCP 與 CLI 的比較段落
  - **學到什麼**：它用結構化的 accessibility snapshot 而不靠截圖；官方認為 CLI 加 skill 比 MCP 省 token，MCP 適合需要持續狀態、反覆檢視頁面的迴圈
  - **和本章的關聯**：第七節實測的兩條路；README 跟著最新版走，旗標以 `python -m playwright mcp --help` 為準

前端的規格寫好了，但「先寫哪些、寫在哪一層、哪些交給探索」目前是憑經驗決定的。下一章把這個決定寫成測試計畫：每個案例有編號、層級與來源，再讓每個案例變成一個紅燈。

→ [Ch 24 testplan skill：測試計畫是最可靠的計畫](./24-testplan-skill.md)
