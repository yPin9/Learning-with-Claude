# Ch 22 — 後端 E2E 先行：outside-in 雙迴圈

> **目標**：在一行後端程式都還沒有的時候，先寫打真 uvicorn server 的端到端（end-to-end，E2E）驗收測試（acceptance test），看它依序紅在「server 起不來」「路由不存在」「規則沒實作」，分辨紅的原因對不對；再下到內圈用單元測試長出 `Schedule`，讓外圈轉綠。你會做出一個管理 server 生命週期的 pytest fixture（port 0、從自己的 log 判斷就緒、確認 pid、失敗時附上 server log），量過三種隔離策略的成本，最後把剩下的後端（驗證、清單、SQLite 儲存、reset 護欄）交給 agent，在測試被鎖住的條件下跑到綠，再審它交回的東西。

> **環境**：macOS 26.6（arm64）、Python 3.12.15、pytest 9.2.0、FastAPI 0.162.0（starlette 1.10.0）、uvicorn 0.54.0、httpx 0.28.1、Claude Code 2.1.295（claude-opus-5-5）。儲存用 Python 內建的 `sqlite3`（SQLite 3.53.1），不另裝套件。輸出都是 2026-11-02 在 `/tmp/lab/aibdd/ch22-backend-e2e/`（從空目錄開始）實跑的，指令都在 `source /tmp/lab/aibdd/.venv/bin/activate` 之後執行。

## 為什麼需要這個？

這個 repo 已經有後端驗證的拼圖，只是沒人把它們拼起來。[pstack Ch 21 的「二、實作：為一個 notes API 寫驗證技能」](../pstack/21-verification-skills.md)在空 port 啟動 server、給它全新的資料目錄、用 curl 打 API，再比對 response、清單與磁碟上的檔案，但那是功能做完**之後**的驗收。[pstack Ch 3 的「階段 5：先建驗證，再委派」](../pstack/03-run-lifecycle.md)先寫驗證、先看它紅，對象卻是一支 CLI。[cicd Ch 11 的「真的讓 `tasktrack` 在 CI 跑 integration test」](../../dev_tools/cicd/11-services-artifacts-concurrency.md)在 CI 接了 Postgres，跑的是程式寫完才補的 TestClient 測試。[SDD final project 的「5a. 自動化驗收測試」](../spec_driven_development/final-project-ship-with-sdd.md)寫了打 `/api/transactions` 的 step definitions，內容是 `...`。

缺的是把順序倒過來：先從驗收條件寫出 HTTP 層的測試（路由、狀態碼、資料有沒有存下來），確認它**因為對的原因**失敗，再讓它驅動開發。本課 [Ch 3](./03-propose-constrain-feedback.md) 與[練習 A](./practice-a-first-loop.md) 交給 agent 的失敗情境已經是這個順序，但它們用 TestClient 在同一個 process 裡打，也沒有往下長出內圈的單元測試。常見的另外兩種做法各有洞：後端做完再補整合測試，測試只會描述程式已經做了什麼；從領域物件往外做單元層級的 TDD，路由、序列化、設定這些接線問題要到最後接上 HTTP 才浮現。

agent 讓這件事更要緊。agent 寫的單元測試，測的是 agent 自己對需求的理解；它可以讓十幾個單元測試全綠，而 server 起不來、路由拼錯、JSON 形狀和前端要的不一樣。一份在動工前由人寫好、穿過真 server 的外圈測試，是 agent 最難繞過的「完成」定義。代價是慢、失敗時難定位、要管 server 的生命週期與資料狀態，本章把它們一個個量出來、處理掉。

## 先建立直覺：先打通導坑，再擴挖

挖隧道常見的做法是先打一條小導坑，從入口通到出口，確認方向、地質、排水都對，再沿著它擴挖成全斷面。**walking skeleton**（行走的骨架）就是軟體的導坑：功能幾乎為零、但每一層都真的接上的版本；在本章，它是「測試發出 HTTP 請求，穿過 uvicorn、FastAPI、我們的程式，再回到測試」。這個詞就我所知出自 Alistair Cockburn；Steve Freeman 與 Nat Pryce 的《Growing Object-Oriented Software, Guided by Tests》（以下稱 GOOS）把它放在 TDD 的起點，第 4 章有一節就叫「First, Test a Walking Skeleton」。導坑打通後，每個功能都是一次擴挖，外圈測試是擴挖時的測量儀：

```
外圈：E2E 驗收測試（真 server、真 HTTP；本章跑一次 0.35–1.5 秒）
  1 從驗收條件寫一個 E2E 測試
  2 跑它，確認紅的原因對
  ├──▶ 內圈：單元測試（本章跑一次 0.02 秒以內）
  │      a 寫一個單元測試，看它紅
  │      b 寫剛好夠的程式讓它綠
  │      c 在綠燈下重構；重複 a–c
  3 再跑外圈：還紅就回內圈
  4 外圈綠了：功能完成，這個測試從「量進度」改當「抓回歸」
```

兩個迴圈的「紅」意思不同：外圈紅代表「從使用者的位置看，這個行為還不存在」，內圈紅代表「下一小步還沒做」。外圈決定**做什麼**與**何時停**，內圈決定**怎麼做**，快到一分鐘能跑好幾次（紅綠重構本身還不熟的話，先讀 [Ch 19](./19-tdd-by-hand.md)，本章的內圈只走到夠用為止）。從最外層的請求往裡長出程式，一般稱為 outside-in。第 4 步出自 GOOS 第 5 章的一節「Separate Tests That Measure Progress from Those That Catch Regressions」。類比的邊界：隧道打通就不再量，外圈測試卻每次改動都要重跑，所以它得夠快、夠穩。

## 一、外圈的地基：在測試裡管一個真的 server

外圈要打的是「會被部署的那個東西」：一個用 `python -m uvicorn` 啟動、讀環境變數、在某個 port 聽 HTTP 的 process。所以第一件事是寫一個能在測試裡把它啟動、確認、收掉的 fixture（pytest 用來準備與清理測試環境的函式）。五個設計決定，各自對應一種本章稍後會親眼看到的失敗：

- **子 process，不用 thread 或 TestClient**：測到真的啟動指令、環境變數與 process 邊界（底層機制一節會看到差別）。
- **`--port 0`，從自己的 log 讀出 port**：作業系統挑空 port，號碼來自我們啟動的 process；固定 port 的下場見踩雷 2。
- **就緒 = log 出現 `Uvicorn running on`，且 `/health` 回報的 pid 是我們的**：uvicorn 0.54.0 的 `server.py` 先跑完 app 的啟動事件（ASGI lifespan）、bind 好 socket，才印這行；pid 檢查就是 pstack Ch 21 的 Doctor。
- **起不來就收掉 process，把整份 log 放進錯誤訊息**：原因在 server 的 stderr，不在測試這一側。
- **用 `sys.executable` 啟動**：server 與 pytest 同一個直譯器，避開 [Ch 0](./00-environment-setup.md) 踩雷 2 的系統 Python。

`pytest.ini` 只有三行（`[pytest]`、`pythonpath = .`、`testpaths = tests`，讓測試能 `import booking`）。`tests/e2e/conftest.py`：

```python
import itertools, os, re, subprocess, sys, time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
READY = re.compile(r"Uvicorn running on (http://127\.0\.0\.1:\d+)")
_serial = itertools.count(1)


class LiveServer:
    """Launch a real uvicorn process on a free port; stop only what we started."""

    def __init__(self, workdir, db, test_mode=True):
        self.log_path = workdir / f"server-{next(_serial)}.log"
        env = {**os.environ, "BOOKING_DB": str(db), "BOOKING_TEST_MODE": "1" if test_mode else ""}
        cmd = [sys.executable, "-m", "uvicorn", "booking.api:create_app", "--factory", "--port", "0"]
        with self.log_path.open("w") as log:
            self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.url = self._wait_until_ready(timeout=10)

    def _wait_until_ready(self, timeout):
        deadline = time.monotonic() + timeout
        while not (m := READY.search(self.log())):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError(f"server did not start (exit {self.proc.returncode})\n{self.log()}")
            time.sleep(0.05)
        health = httpx.get(m[1] + "/health")
        if health.status_code != 200 or health.json().get("pid") != self.proc.pid:
            self.stop()
            raise RuntimeError(f"{m[1]} is not our server: /health -> {health.status_code} {health.text}")
        return m[1]

    def log(self, start=0):
        return self.log_path.read_text()[start:]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("server")
    live = LiveServer(workdir, workdir / "bookings.db")
    yield live
    live.stop()


@pytest.fixture
def api(server, request):
    start = len(server.log())
    with httpx.Client(base_url=server.url) as client:
        yield client
    request.node.add_report_section("teardown", "server log", server.log(start))


@pytest.fixture
def book(api):
    def book(room, start, end, by):
        return api.post("/bookings", json={"room": room, "start": start, "end": end, "by": by})
    return book
```

幾個不明顯的地方。`timeout=10` 是本章訂的上限：這台機器上 server 約 0.2 秒就緒，留 50 倍餘裕給偶爾很慢的 CI 機器，每 0.05 秒看一次 log。`api` 記下測試開始時 log 的長度，結束時只把這段期間新增的 log 交給 `add_report_section`；pytest 印失敗報告時，會一併印出同一測試 teardown 報告裡名稱含 `teardown` 的區段（`_pytest/terminal.py` 的 `_handle_teardown_sections`），所以失敗的測試下面會多一段 `Captured server log teardown`，通過的不印。`book` 是一層薄 helper，讓測試讀起來像規格，[Ch 30](./30-one-gherkin-two-drivers.md) 會把它長成 driver。`BOOKING_DB`、`BOOKING_TEST_MODE` 現在 app 還不讀，先寫進來，因為「怎麼設定這個 server」本身就是合約的一部分（第五節）。整體是 pstack Ch 21 `verify.sh` 的 Launch、Doctor、Cleanup，只殺自己啟動的 process；差別是它用在功能**之前**，生命週期交給 pytest。

## 二、外圈的第一圈：從 ERROR 到「對的紅」

第一個外圈測試就是 walking skeleton：server 起得來、答得出 `/health`。`tests/e2e/test_booking_api.py`：

```python
def test_walking_skeleton_reports_health(api):
    response = api.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

還沒有 `booking/` 套件就跑 `python -m pytest -q; echo "exit=$?"`：

```
____________ ERROR at setup of test_walking_skeleton_reports_health ____________
…（略）
E               RuntimeError: server did not start (exit 1)
…（略）
E               ModuleNotFoundError: No module named 'booking'
…（略）
1 error in 0.09s
exit=1
```

注意是 **`ERROR at setup`**、`1 error`，不是 `FAILED`。pytest 把 fixture 丟出的例外算成 error、把測試本體裡的失敗（斷言或其他例外）算成 failure，exit code 都是 1。這個紅是**錯的原因**：測試還沒碰到任何行為，只證明了「沒有 app」。內圈裡「還 import 不到」可以當最便宜的第一個紅，外圈裡它不算進度；[Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py` 會讀 junit XML 把兩種紅自動分開。訊息裡的 `ModuleNotFoundError` 是 server 自己印的，fixture 把 log 整份帶了回來。

walking skeleton：`booking/__init__.py` 是空檔，`booking/api.py` 只有 13 行，就是第三節完整版只留 `import os`、`from fastapi import FastAPI`、`create_app()` 裡的 `app = FastAPI()`、`/health` 路由與 `return app`。結果 `1 passed in 0.63s`、`exit=0`，時間大多花在啟動與關閉 server。這次的 server log（pytest 暫存目錄裡的 `server0/server-1.log`，這台 macOS 上是 `$TMPDIR/pytest-of-$USER/pytest-current/server0/server-1.log`）裡依序有 `Started server process [22115]`、`Application startup complete.`、`Uvicorn running on http://127.0.0.1:50508`、兩次 `GET /health`（fixture 的 Doctor 一次、測試一次），最後是收到 SIGTERM 後的 `Shutting down` 到 `Finished server process [22115]`：port 0 換成了 50508，整條導坑通了。

R1 的外圈測試接在同一檔案後面。Arrange 段的 `assert` 是前置條件檢查：Bob 的預約要先成立，後面的斷言才有意義，紅燈因此停在真正壞掉的那一步。這刻意違反 [Ch 21](./21-tdd-skill-aaa.md) 的 AAA-1 與 AAA-2：拿 Ch 21 的 `aaa_lint.py` 檢查這個檔，實測 `1 files, 2 tests, 2 problems`。外圈的前置狀態要經 API 建立，若搬進 fixture，它一失敗就成了上面那種 `ERROR at setup`，和「server 沒起來」混在一起；所以那支 lint 只套內圈（本章的單元測試實測 0 problems）：

```python
def test_r1_overlapping_booking_is_rejected(book):
    assert book("Taipei", "10:00", "11:00", "Bob").status_code == 201

    response = book("Taipei", "10:30", "11:30", "Alice")

    assert response.status_code == 409
    assert response.json()["detail"] == "Taipei is taken 10:00-11:00 by Bob"
```

```
E       AssertionError: assert 404 == 201
…（略）
------------------------- Captured server log teardown -------------------------
INFO:     127.0.0.1:50626 - "POST /bookings HTTP/1.1" 404 Not Found
…（略）
1 failed, 1 passed in 0.43s
```

這次是 `FAILED`，下面附著 server 的 log：請求到了，回 404。紅得對，系統活著、行為不存在。再往前一步：在 `api.py` 加上第三節完整版裡的 `BookingIn` 與 `POST /bookings`，但 handler 只寫 `return b`、一律假裝成功，紅燈就往後移到 `E       assert 201 == 409`（`1 failed, 1 passed in 0.38s`）。三個紅依序指出下一步：沒有 app、沒有路由、沒有規則。外圈說不出更多了，「重疊怎麼算」要下到內圈。

## 三、內圈：讓 Schedule 長出 R1

內圈只處理 R1 的重疊判斷，它是純邏輯，不需要 server，所以只跑 `python -m pytest -q tests/unit`。先寫第一個單元測試（下面檔案的第一個函式），`booking/schedule.py` 寫成下面完整版**拿掉 `book` 裡 `for` 迴圈那三行**（只收不拒）：紅在 `E       Failed: DID NOT RAISE Conflict`，`1 failed in 0.01s`。補回迴圈就綠。再加相鄰時段的測試，它第一次跑就過（`2 passed in 0.00s`）；沒紅過的測試不知道抓不抓得到東西，所以我們把兩個 `<` 暫時改成 `<=`，它以 `1 failed, 1 passed in 0.01s` 變紅，改回來才算數。`tests/unit/test_schedule.py`：

```python
from datetime import time

import pytest

from booking.schedule import Conflict, Schedule


def test_overlapping_booking_raises_conflict():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")

    with pytest.raises(Conflict) as error:
        schedule.book("Taipei", time(10, 30), time(11, 30), "Alice")

    assert str(error.value) == "Taipei is taken 10:00-11:00 by Bob"


def test_adjacent_booking_is_allowed():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")

    booking = schedule.book("Taipei", time(11), time(12), "Alice")

    assert booking.start == time(11)
```

`booking/schedule.py`（名稱照本課的領域約定）：

```python
from dataclasses import dataclass
from datetime import time
from itertools import count


class BookingError(Exception):
    pass


class Conflict(BookingError):
    pass


@dataclass(frozen=True)
class Booking:
    id: int
    room: str
    start: time
    end: time
    by: str


class Schedule:
    def __init__(self) -> None:
        self._bookings: list[Booking] = []
        self._ids = count(1)

    def book(self, room: str, start: time, end: time, by: str) -> Booking:
        for b in self._bookings:
            if b.room == room and start < b.end and b.start < end:
                raise Conflict(f"{room} is taken {b.start:%H:%M}-{b.end:%H:%M} by {b.by}")
        booking = Booking(next(self._ids), room, start, end, by)
        self._bookings.append(booking)
        return booking
```

`Conflict` 自己帶著完整訊息，因為「Taipei is taken 10:00-11:00 by Bob」是 R1 定義的一部分：放在純邏輯裡，內圈就測得到，外圈只要確認 API 原樣傳出去。id 用 `count(1)` 而不用 `len(...) + 1`，後者一旦能刪除就會重用 id。接回外圈，`booking/api.py` 完整內容如下，最後那段 `/reset` 先別寫，第四節才加：

```python
import os
from datetime import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from booking.schedule import Conflict, Schedule


class BookingIn(BaseModel):
    room: str
    start: time
    end: time
    by: str


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.schedule = Schedule()

    @app.get("/health")
    def health():
        return {"status": "ok", "pid": os.getpid()}

    @app.post("/bookings", status_code=201)
    def create(b: BookingIn):
        try:
            return app.state.schedule.book(b.room, b.start, b.end, b.by)
        except Conflict as error:
            raise HTTPException(409, str(error))

    @app.post("/reset", status_code=204)
    def reset():
        app.state.schedule = Schedule()

    return app
```

不含 `/reset` 時全套 `4 passed in 0.35s`，外圈綠了。`api.py` 裡沒有任何業務規則：它把 HTTP 翻成 `Schedule.book` 的呼叫，再把 `Conflict` 翻成 409。

## 四、隔離：第二個測試為什麼單獨跑會過

半開區間 `[start, end)` 下相鄰時段要成立，這是 R1 值得在外圈確認的邊界。在 `test_booking_api.py` 加 `test_r1_adjacent_booking_is_confirmed(book)`：前置條件和上一個測試一樣（Bob 訂 `10:00`–`11:00`，斷言 201），Act 改成 `response = book("Taipei", "11:00", "12:00", "Alice")`，Assert 是 `response.status_code == 201`。跑外圈 `python -m pytest -q tests/e2e`：

```
E       AssertionError: assert 409 == 201
…（略）
INFO:     127.0.0.1:50977 - "POST /bookings HTTP/1.1" 409 Conflict
…（略）
1 failed, 2 passed in 0.35s
```

單獨跑 `python -m pytest -q tests/e2e -k adjacent` 卻是 `1 passed, 2 deselected in 0.38s`。session 範圍的 server 還記得上一個測試裡 Bob 訂的 10:00–11:00，這次的前置條件被 409 擋下：測試之間的狀態外洩，結果取決於執行順序。修法有三種，測試本體一行不動，只換 fixture，各跑三次 `python -m pytest -q tests/e2e`：

| 策略 | 做法 | 3 個 E2E 測試 |
|---|---|---|
| 共用 server，每個測試前 `POST /reset` | 本章採用 | 0.35s、0.36s、0.35s |
| 每個測試一個 server | `server` fixture 改 `scope="function"` | 1.16s、1.15s、1.12s |
| TestClient，每個測試一個新 app | `api` 改成 `with TestClient(create_app()) as client: yield client`，兩個 import 寫在 fixture 裡 | 0.10s、0.10s、0.10s（各 1 warning） |

TestClient 不開 port、不起 process，在同一個 process 裡直接呼叫 ASGI app，所以最快；`create_app()` 這種 app factory（每次呼叫回傳一個新 app 的函式）讓每個測試拿到全新狀態。那個 warning 是 ``StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.``：starlette 1.10.0 的 `testclient.py` 先找 `httpx2`，找不到才退回 `httpx` 並警告，本課 venv 只有 httpx 0.28.1，功能不受影響。這 0.10 秒大半是載入 FastAPI：import 寫在 fixture 裡就算進計時；改寫在 conftest 開頭、又像這裡用 `tests/e2e` 指定目錄時，pytest 在開始計時前就載入了這份 conftest，同樣三個測試量到 0.02s。對比表 thread 那一列的 import 也寫在 fixture 裡。每測試一個 server 最乾淨，多出的約 0.8 秒是多啟動兩次 server 的成本；`pytest --durations` 顯示共用 server 的啟動與關閉各約 0.2 秒（`0.20s setup`、`0.23s teardown`），只付一次。我們選第一種：在 `api` fixture 的 `start = ...` 下面加一行 `httpx.post(server.url + "/reset").raise_for_status()`，再寫上 `api.py` 那段 `/reset`，全套 `5 passed in 0.37s`。`raise_for_status()` 不是裝飾：reset 失敗卻默默繼續，等於隔離失效而沒人知道。`/reset` 也帶來新風險，一個能清空資料的端點可能跟著上線，第五節把「正式環境沒有 `/reset`」寫成驗收測試。

## 底層機制：一個請求穿過兩個 process

```
pytest process                               uvicorn process（我們啟動、我們收）
server fixture：Popen ─────────────────────▶ create_app()：app 與 Schedule 都住在這邊
  讀自己的 log ◀─ "Uvicorn running on http://127.0.0.1:50508"
  GET /health ──── TCP ────────────────────▶ {"status": "ok", "pid": ...}
api fixture：記下 log 長度、POST /reset
測試本體：POST /bookings ── TCP ──────────▶ 路由 → pydantic 驗證 → create() → Schedule.book
                 ◀── 201 / 404 / 409 / 500 ── 例外 → HTTPException，或 500 + traceback 進 log
teardown：log 新增的那段 → 報告區段；terminate() ── SIGTERM ──▶ 正常關機
```

**第一，紅燈的形狀告訴你壞在哪一層。** 本章看過的每一種失敗：

| 你看到 | 壞在 | 紅得對嗎 |
|---|---|---|
| `ERROR at setup` + server 的 traceback | server 沒起來（import、設定、port） | 不對：修好它，不算進度 |
| `FAILED assert 404 == ...` | 路由不存在 | 對：行為還不存在 |
| `FAILED assert 201 == 409` | 規則還沒做 | 對 |
| `FAILED assert 500 == ...` | 處理請求時程式炸了 | 不對：這是 bug，不是「還沒做」 |
| 單獨跑會過、一起跑會紅 | 測試之間的狀態 | 不對：隔離問題 |

500 最考驗失敗定位（從失敗反推原因在哪）。把 `create()` 的回傳故意改成 `book(...).as_dict()`（看完改回），加上 `--show-capture=no`（不印擷取區段，等於不帶 log 的 fixture），測試這一側看得到的只有 `E       AssertionError: assert 500 == 201` 與 `<Response [500 Internal Server Error]>`，看不到原因。拿掉那個旗標，同一個失敗下面多了 server 那段 log：

```
------------------------- Captured server log teardown -------------------------
INFO:     127.0.0.1:51318 - "POST /reset HTTP/1.1" 204 No Content
INFO:     127.0.0.1:51319 - "POST /bookings HTTP/1.1" 500 Internal Server Error
…（略）
  File "/private/tmp/lab/aibdd/ch22-backend-e2e/booking/api.py", line 28, in create
…（略）
AttributeError: 'Booking' object has no attribute 'as_dict'
```

E2E 測試的診斷資訊在另一個 process，你得自己把它搬回來；GOOS 第 23 章「Test Diagnostics」把診斷當一等功能，就是這個意思。

**第二，測試碰不到 server 的記憶體。** monkeypatch 是 pytest 內建的 fixture，在測試期間替換屬性、測試結束自動還原。寫一個測試 `test_patch_in_test_process_reaches_the_app(api, monkeypatch)`，暫放在 `tests/e2e/test_boundary.py`、只跑這個檔（做完刪掉，別讓它被鎖進第五節的合約）：先 `monkeypatch.setattr("os.getpid", lambda: 42)`，再斷言 `api.get("/health").json()["pid"] == 42`。打真 server 得到 `E       assert 25024 == 42`、`1 failed in 0.36s`；換成第四節的 TestClient 版 `api` 則是 `1 passed, 1 warning in 0.10s`。同一個測試，一邊 patch 穿得過去，一邊穿不過去。這是 E2E 測試比較接近正式環境的原因，也是它比較難控制的原因：要控制 server，得開一個跨得過 process 的接縫（環境變數、設定檔、測試專用端點），而接縫本身要像 `/reset` 一樣被管好。Ch 0 的冒煙測試把 uvicorn 放在測試 process 的 thread 裡跑，屬於穿得過去的那一邊：把 `server` fixture 換成那種 thread 版，同一個測試是 `1 passed in 0.20s`。

## 五、把剩下的後端交給 agent：測試鎖住，跑到綠

R1 是我們用雙迴圈手做的。剩下的照同樣節奏也做得完，但這正是 agent 適合接手的形狀：合約寫好、會跑、紅得對。**先寫合約**，四條還沒有的行為：R2；清單（時間回成 `HH:MM`、reset 後 id 從 1 起算）；重啟後資料還在；正式模式沒有 `/reset`。`tests/e2e/test_validation_and_storage.py`：

```python
import httpx

TAIPEI_BOB = {"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"}
TOKYO_CAROL = {"room": "Tokyo", "start": "09:00", "end": "09:30", "by": "Carol"}


def test_r2_end_before_start_is_rejected(book):
    response = book("Taipei", "11:00", "10:00", "Alice")

    assert response.status_code == 422
    assert response.json()["detail"] == "end must be after start"


def test_malformed_time_is_rejected(book):
    response = book("Taipei", "25:00", "26:00", "Alice")

    assert response.status_code == 422


def test_bookings_are_listed_as_hh_mm_with_ids_from_1(api):
    api.post("/bookings", json=TAIPEI_BOB)
    api.post("/bookings", json=TOKYO_CAROL)

    response = api.get("/bookings")

    assert response.json() == [{"id": 1, **TAIPEI_BOB}, {"id": 2, **TOKYO_CAROL}]


def test_bookings_survive_a_restart(start_server, tmp_path):
    first = start_server(tmp_path / "bookings.db")
    httpx.post(first.url + "/bookings", json=TAIPEI_BOB)
    first.stop()
    second = start_server(tmp_path / "bookings.db")

    httpx.post(second.url + "/bookings", json=TOKYO_CAROL)

    assert httpx.get(second.url + "/bookings").json() == [{"id": 1, **TAIPEI_BOB}, {"id": 2, **TOKYO_CAROL}]


def test_reset_is_not_exposed_outside_test_mode(start_server, tmp_path):
    production_like = start_server(tmp_path / "bookings.db", test_mode=False)

    response = httpx.post(production_like.url + "/reset")

    assert response.status_code == 404
```

重啟與正式模式要自己開 server，在 `conftest.py` 最後加：

```python
@pytest.fixture
def start_server(tmp_path):
    started = []

    def start(db, test_mode=True):
        started.append(LiveServer(tmp_path, db, test_mode))
        return started[-1]

    yield start
    for live in started:
        live.stop()
```

全套 `4 failed, 6 passed in 1.63s`，四個紅都是斷言失敗、各自紅得對：R2 沒檢查（`assert 201 == 422`）；清單與重啟兩個測試拿到 `{'detail': 'M... Not Allowed'}`，因為 `/bookings` 只有 POST，GET 回 405 `Method Not Allowed`；`/reset` 沒有護欄（`assert 204 == 404`）。`test_malformed_time_is_rejected` 開工時就綠，因為 pydantic 已經擋掉 `25:00`；一開始就綠的驗收測試要說得出理由，這一個釘住的是框架給的行為，防止有人把 `time` 欄位改成 `str`。我們刻意**沒有**在外圈寫 `end == start`，那是 `Schedule` 的邊界，留給內圈。

**再鎖住它。** `.claude/settings.json` 的 `permissions.deny` 是 `["Edit(/tests/e2e/**)", "Edit(/pytest.ini)"]`（寫法在 [Ch 3](./03-propose-constrain-feedback.md) 講過：`/` 開頭相對於專案，用 `Edit` 而不是 `Write`）。它管得住 Claude Code 的檔案工具，管不住 agent 用別的程式間接寫檔，所以再加兩道事後檢查：`git init`（`.gitignore` 排除 `__pycache__/` 與 `.pytest_cache/`）後 commit（訊息 `Outer tests for R2, listing, storage, reset guard (red)`），再用 `shasum -a 256 tests/e2e/*.py pytest.ini > /tmp/lab/aibdd/ch22-locked.sha256` 記下雜湊。

**交出去。** prompt 存在 `/tmp/lab/aibdd/ch22-prompt.txt`，只講結構約束，不重述行為，行為在測試裡：

```text
The tests under tests/ are the specification for this booking backend. Four end-to-end tests in tests/e2e/ fail. Make the whole suite pass.

Rules:
- tests/e2e/ and pytest.ini are locked. Do not try to change them.
- Business rules live in booking/schedule.py: pure Python, no I/O. Before you add a rule there, add a failing unit test for it in tests/unit/ and run it.
- Storage lives in a new module booking/store.py, using the standard-library sqlite3 module.
- booking/api.py wires HTTP to the schedule and the store.
- Run tests with: /tmp/lab/aibdd/.venv/bin/python -m pytest -q

Stop when the full suite is green. Reply with a short list of the files you changed and why.
```

```bash
cd /tmp/lab/aibdd/ch22-backend-e2e && claude -p "$(cat /tmp/lab/aibdd/ch22-prompt.txt)" \
  --max-turns 20 --permission-mode dontAsk \
  --allowedTools "Read,Edit,Write,Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)" \
  --setting-sources project --strict-mcp-config --no-session-persistence \
  --output-format stream-json --verbose > /tmp/lab/aibdd/ch22-agent-run1.jsonl 2> /tmp/lab/aibdd/ch22-agent-run1.err
```

`dontAsk` 讓需要核准的動作直接被拒；`--allowedTools` 只放行檔案工具與那一條 pytest 指令；`--setting-sources project` 不載入使用者自己的設定（Ch 0 發現這台機器的使用者設定會覆寫 `PATH`）；prompt 與 allow 規則都寫 venv 的絕對路徑，不管 `PATH` 長怎樣都跑同一個直譯器，而 allow 規則只放行這個寫法。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，claude-opus-5-5）。

result 訊息用 `jq` 取欄位是 `{"subtype":"success","num_turns":25,"duration_ms":79768,"total_cost_usd":0.3849068,"denied":1}`：80 秒、0.38 美元、一次被拒。agent 的回覆開頭是「The full suite is green at 14 passed, including the four e2e tests that were failing.」，最後還多報了一件事：「A timezone-aware time such as `10:00Z` compared against a stored time without a timezone still causes a 500.」。從 transcript 撈出它四次跑 pytest 的摘要行，依序是：第一個回應就和列目錄的指令一起跑全套，先確認合約的紅 `4 failed, 6 passed in 1.65s`，之後才讀檔；加了 R2 的參數化單元測試（`end` 等於、早於 `start`）與一個空的例外類別後 `2 failed, 2 passed in 0.02s`；實作 R2、再加「從既有預約建立 `Schedule`」的兩個單元測試後 `2 failed, 4 passed in 0.02s`；改 `Schedule` 建構子、新增 `store.py`、改寫 `api.py` 後 `14 passed in 1.47s`。它照我們要的內圈節奏走：每條新規則先有一個紅的單元測試。被拒的那一次是一個複合 Bash 指令（用 `for` 迴圈 `cat` 八個檔案，再接 `ls` 與 `git show`），沒被當成可自動放行的唯讀指令，它改用 Read 工具逐一讀檔。兩個值得記下的細節：result 寫 `num_turns` 25（正好是 24 次工具呼叫加 1），transcript 裡卻只有 7 個不同的 assistant 訊息 id，`--max-turns 20` 也沒讓它中途停下，可見兩者計的不是同一種東西，別拿 `num_turns` 推算離上限多遠；stderr 只有一行警告，開頭是 `Warning: no stdin data received in 3s, proceeding without it.`，後半句建議用 `< /dev/null` 跳過，腳本裡加上它可省這 3 秒。

**不信回報，自己驗。** `shasum -a 256 -c /tmp/lab/aibdd/ch22-locked.sha256` 對四個鎖定檔各印一行 `OK`；`git status --short` 列出改了 `booking/api.py`、`booking/schedule.py`、`tests/unit/test_schedule.py`，新增 `booking/store.py`；我們自己跑 `python -m pytest -q` 得 `14 passed in 1.49s`。這次 agent 沒有嘗試改 `tests/e2e/`，deny 規則沒被觸發，它擋下改檔的樣子在 Ch 3 的實跑看過。

**最後是審。** 綠燈只證明合約被滿足，合約沒寫到的要靠人看。agent 的設計是：`Store.append(decide)` 在一個 `BEGIN IMMEDIATE` 交易裡讀出現有預約，交給 `api.py` 傳進來的 `lambda existing: Schedule(existing).book(...)` 決定，再寫入；`/reset` 只在 `if os.environ.get("BOOKING_TEST_MODE"):` 時才註冊。審查結論：

1. **詞彙漂移**：它新增的例外叫 `InvalidBooking`，本課的領域詞彙是 `InvalidRange`。外圈只看得到 HTTP，管不到內部命名，agent 自己寫的內圈測試也跟著用了它的名字。我們改名、重跑得 `14 passed in 1.47s`，另成一個 commit；詞彙表為什麼要綁進 agent 的 context，見 [SDD Ch 34](../spec_driven_development/34-ubiquitous-language-as-glossary.md)。
2. **id 由既有預約的最大 id 加一算出**：今天正確（reset 清空後從 1 開始、重啟後接續），但一旦能刪除，刪掉最後一筆後 id 會被重用。[練習 D](./practice-d-cancel-booking.md) 做 R4 取消時，先用單元測試與 API 測試釘住「id 不重用」（它的 TP-015、TP-019）。
3. **交易**：讀、判斷、寫入包在同一個寫入交易裡，兩個同時進來的重疊請求不會都通過檢查。這是讀 code 看出來的，我們沒測並發，別把它當成被證明過的性質。
4. **`BOOKING_DB` 變成必填**：不設就啟動失敗，實測 `exit=1`、`KeyError: 'BOOKING_DB'`。agent 主動說明了這個取捨，我們接受，但它沒被任何測試釘住。
5. **它回報、沒修的 500**：用真 server 重現，Bob 訂 `10:00`–`11:00` 回 201，Alice 送 `10:30Z`–`11:30Z` 回 `500 Internal Server Error`，server log 是 `TypeError: can't compare offset-naive and offset-aware times`。我們手寫的版本就有這個 bug；`25:00` 那個測試抓不到它，因為 pydantic 認得 `10:30Z` 是合法時間。留給動手練習。agent 停在合約邊界，把邊界外的問題回報出來而不是順手改掉，正是用測試畫出範圍的效果。

## 六、在 CI 跑外圈（未實測）

> 本段未實測，為理論預期行為：workflow 用 PyYAML 解析過，沒有在 GitHub Actions 上跑過；Postgres 版的 store 不在本章範圍。

CI 裡的外圈要顧三件事：單元測試先跑，紅了就不等外圈（e2e job 設 `needs: unit`）；外圈失敗時留下 server log；正式環境用 Postgres，外圈就對 Postgres 跑。`.github/workflows/booking.yml` 的 e2e job（節錄）：

```yaml
    services:
      postgres:
        image: postgres:16
        env: {POSTGRES_PASSWORD: postgres}
        options: --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
        ports: ["5432:5432"]
    env:
      BOOKING_DB_URL: postgresql://postgres:postgres@localhost:5432/postgres
```

步驟是 checkout、setup-python 3.12、裝釘死版本的套件並另加 `psycopg[binary]`、`python -m pytest -q tests/e2e --basetemp=e2e-tmp`，最後一步 `if: failure()` 用 `actions/upload-artifact@v7`（2026-11 的最新主版本，和 [Ch 32](./32-brownfield-bdd.md) 的 workflow 一致）上傳 `e2e-tmp/**/server-*.log`。`--basetemp` 把 pytest 暫存目錄固定下來；本機實測 log 落在 `e2e-tmp/server0/server-1.log`、`e2e-tmp/test_bookings_survive_a_restar0/server-2.log` 這類路徑，上傳才撈得到。job 直接跑在 runner 上，所以連 `localhost`；store 換成 Postgres 後，reset 要改成 `TRUNCATE bookings RESTART IDENTITY` 這種連序號一起重設的寫法。本章的 SQLite 版拿掉 `services` 與 `BOOKING_DB_URL` 就是它的 CI；health check 怎麼等，[cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md) 有用 `tasktrack` 寫的完整版本。另外，PyYAML 讀這份檔案得到的頂層鍵是 `['name', True, 'jobs']`：YAML 1.1 把 `on` 讀成布林值，自己寫 workflow 檢查腳本時要記得。

## 對比與取捨

| 做法 | 本章實測（3 個測試） | 測得到 | 測不到 | 適合 |
|---|---|---|---|---|
| TestClient，每測試新 app | 0.10s | 路由、驗證、序列化 | 啟動指令與它帶的環境變數、真的重啟、process 邊界 | 大量 API 情境 |
| thread 裡的 uvicorn（Ch 0 冒煙測試） | 0.17–0.24s | 真 HTTP | process 邊界（patch 穿得過去）、重啟 | 快速雛形 |
| 子 process + `/reset`（本章） | 0.35s | 真 HTTP、啟動、環境變數、log | 瀏覽器、正式環境的網路與部署 | 驗收測試主力 |
| 每測試一個子 process | 1.12–1.16s | 同上，完全隔離 | 同上 | 重啟、正式模式這類少數測試 |
| docker compose 全套 + 真資料庫 | 未實測 | 資料庫方言、migration | 回饋最慢 | CI、發版前 |
| schemathesis（[SDD Ch 36](../spec_driven_development/36-domain-model-as-spec-backbone.md)） | 未實測 | 從 OpenAPI 生成的輸入、500 | 業務規則對不對 | 補例子式測試的盲點 |

為什麼不只用 TestClient？它最快，本章兩個最有價值的外圈測試（重啟後資料還在、正式模式沒有 `/reset`）它也能在同一個 process 裡模擬：兩次 `create_app()` 共用一個資料庫檔、先用 monkeypatch 改環境變數再建 app，我們對 agent 的版本實測 `2 passed`。但那只證明「app 會讀設定」，證明不了部署用的那條啟動指令真的把設定帶進去、process 真的重啟過，這兩件事要真的啟動 process。為什麼不全寫成 E2E？內圈 6 個單元測試跑 `0.00s`，外圈 8 個跑 `1.45s`，光重啟那一個就佔 `0.54s`；把 `end == start` 這種邊界放到外圈，每次多付一趟 HTTP，只多證明了一件內圈已經證明的事。

## 踩雷集錦

1. **把 ERROR 當成紅燈**：錯誤直覺是「測試紅了，可以開始寫功能」。正確認識：第二節第一個紅是 `ERROR at setup`，原因是 `ModuleNotFoundError`，和需求無關。外圈的紅要是 `FAILED`，而且失敗的斷言對應到還沒做的行為；setup error 先修，不算進度。
2. **用 sleep 等 server、用固定 port**：錯誤直覺是「睡一秒應該夠了」。我們跑了一支常見寫法（`Popen` 啟動 `uvicorn ... --port 8765`、`time.sleep(wait)`、再用 `httpx.get` 打 `/health`）四次，四次都拿到 200：`sleep 0.1s -> 200`、`sleep 0.1s -> 200`、`sleep 0.3s -> 200`、`sleep 1.0s -> 200`。後兩次之前 uvicorn 印了 `[Errno 48] error while attempting to bind on address ('127.0.0.1', 8765): [errno 48] address already in use`，前兩次連這行都沒有，表示我們的 server 在 0.1 秒內還沒走到 bind 就被收掉了。`lsof` 一查，8765 早被這台機器上另一個專案的 uvicorn 佔了四天，四個 200 都是它回的：這是假綠燈，比紅燈更糟。正確認識：port 用 0，就緒只看自己 process 的 log。把 `LiveServer` 硬改成 8765，得到的是明確的 `RuntimeError: server did not start (exit 3)` 加上那行 Errno 48，而且它一個請求都沒送出去。
3. **測試依賴絕對 id，reset 卻沒重設序號**：錯誤直覺是「reset 就是把資料刪光」。正確認識：清單測試要求 reset 後 id 從 1 開始，這是測試模式合約的一部分。用 Python 內建的 sqlite3（SQLite 3.53.1）實測，插入兩筆、刪光、再插入一筆：`INTEGER PRIMARY KEY AUTOINCREMENT` 的表得到 id 3，一般的 `INTEGER PRIMARY KEY` 得到 1。要嘛讓 reset 連序號一起重設，要嘛讓其他測試只用 response 回傳的 id。
4. **測試專用端點跟著上線**：錯誤直覺是「`/reset` 只有測試會呼叫」。正確認識：端點存在就有人能呼叫。把它的存在條件寫成驗收測試（`test_reset_is_not_exposed_outside_test_mode` 從 `assert 204 == 404` 紅到綠），而不是在 README 裡提醒。
5. **只寫外圈、不下到內圈**：錯誤直覺是「E2E 最接近使用者，全用 E2E 最保險」。正確認識：外圈慢（本章 1.45 秒對 0.00 秒），失敗時只說得出「500」，原因在另一個 process。Ham Vocke 的說法是「Push your tests as far down the test pyramid as you can」：邊界與組合放內圈，外圈只確認「接上了」與「使用者看得到的行為」。

## 進階：控制時間、並行與真的資料庫

- **控制時間要開跨 process 的接縫**：我們的領域只用 `HH:MM`，沒有「現在幾點」。一旦規則依賴時鐘（例如不能訂過去的時段），monkeypatch 實驗已經說明 patch `datetime.now` 沒用；要讓 `create_app` 從環境變數讀一個測試時鐘，和 `BOOKING_TEST_MODE` 一樣只在測試模式生效，再寫一個「正式模式不吃這個變數」的外圈測試。本段未實測。
- **並行跑外圈**：本課 venv 沒有 pytest-xdist。原理上 session fixture 會在每個 worker 各跑一次，port 0 與 `tmp_path_factory` 讓每個 worker 有自己的 server 與資料庫檔，要小心的是寫死的共用資源。本段未實測。
- **交易回滾式隔離跨不過 process**：in-process 測試常讓每個測試開一個交易、結束時 rollback。server 在另一個 process、用自己的連線，測試的交易包不住它，外圈只能用 reset 端點、`TRUNCATE ... RESTART IDENTITY` 或每個 session 一個新資料庫。GOOS 第 25 章「Testing Persistence」從另一個角度談同一個問題。本段未實測。
- **用 schemathesis 補盲點**：`10:30Z` 是例子式測試的典型盲點，我們想到 `25:00`，沒想到「格式合法、語意不相容」的輸入。從 FastAPI 的 `/openapi.json` 生成大量輸入、檢查「不准 500」，正是 SDD Ch 36 介紹的 schemathesis 的強項。本章沒裝它（共用 venv 不加套件），它能否抓到這一個 500，請以實測為準。

## 動手練習

1. **外圈先行，修掉 `10:30Z` 的 500**：在 `test_validation_and_storage.py` 加一個測試，Bob 先訂 `10:00`–`11:00`，Alice 送 `10:30Z`–`11:30Z`，期望 422。我們實測它先紅在 `assert 500 == 422`，server log 區段裡是那個 `TypeError`。先決定拒絕該放在哪一層（輸入格式的 `BookingIn`，還是 `Schedule`），寫下理由再動手。參考：我們在 `BookingIn` 加一個 `field_validator`，對帶 `tzinfo` 的時間丟 `ValueError`，全套 `15 passed in 2.11s`，回應的 detail 是 FastAPI 的錯誤清單。
2. **R3 辦公時間**：外圈先寫 `07:30`–`08:30` 期望 422、detail 為 `outside office hours 08:00-20:00`，我們實測它紅在 `assert 201 == 422`；內圈再用單元測試釘住 `08:00` 與 `20:00` 兩個端點（`19:00`–`20:00` 要成立）。外圈需要測端點嗎？說出理由。
3. **親眼看假綠燈**：先用 `lsof -nP -iTCP:8799 -sTCP:LISTEN` 確認沒人在用 8799，再到一個只放了 `health` 檔的目錄裡跑 `python -m http.server 8799 --bind 127.0.0.1` 自己佔住它（`--bind` 不能省：它預設綁所有介面，我們在這台 macOS 上實測，uvicorn 綁 `127.0.0.1` 照樣成功，兩個版本都綠、佔位的 server 一個請求都沒收到）；把 `LiveServer` 的 `--port 0` 改成 8799 跑一次，再寫一個用 `time.sleep` 等待的版本跑一次，比較兩者的輸出。不要拿別人正在跑的服務做實驗。
4. **換一種交辦方式**：從交辦前那個 commit 開始，拿掉 prompt 裡關於分層的三條規則，再交給 agent 一次。規則被放到哪裡？內圈測試還在嗎？用第五節的五點審它。

## 本章重點整理

- 雙迴圈：外圈是穿過真 server 的驗收測試，決定做什麼、何時停；內圈是單元測試，決定怎麼做，快兩個數量級（本章 0.00 秒對 1.45 秒）。功能完成後，外圈測試從量進度轉成抓回歸。
- walking skeleton 是第一個外圈測試。本章的紅依序是 setup error、`404 == 201`、`201 == 409`，只有後兩個是「對的紅」。
- 管 server 的 fixture：子 process、port 0、從自己的 log 判斷就緒、pid 確認、失敗附 server log、只殺自己啟動的；固定 port 加 sleep 在這台機器上產生過假綠燈。
- 隔離是成本取捨：共用 server 加 `/reset`（0.35 秒）、每測試一個 server（約 1.15 秒）、TestClient（0.10 秒，但碰不到真的啟動指令與 process 重啟）；測試專用端點的存在條件要寫成驗收測試。
- 交給 agent：合約先紅、鎖住（deny 規則、commit、雜湊）、只交辦結構約束、自己重驗，再審合約沒寫到的（詞彙、id、交易、預設值、回報的缺陷）。

## 自我檢核

- [ ] 不看本章，畫出雙迴圈，說出外圈的紅和內圈的紅各代表什麼
- [ ] 本章出現過哪幾種「紅」？哪幾種算進度、哪幾種要先修？各舉一個本章的輸出
- [ ] monkeypatch 為什麼碰不到子 process 的 server？要讓 server 用假時鐘，接縫開在哪裡，又怎麼證明它不會在正式環境生效？
- [ ] 你的 E2E 套件從 2 秒變成 2 分鐘，你會先檢查哪三件事？本章哪一個量測支持你的判斷？
- [ ] agent 交回全綠之後，你會跑哪三個檢查才收下？哪一類問題這三個檢查都抓不到、只能靠人審？

## 延伸閱讀

- **[Growing Object-Oriented Software, Guided by Tests（目錄頁）](https://growing-object-oriented-software.com/toc.html)** — Steve Freeman、Nat Pryce（Addison-Wesley，2009 年 10 月出版，版權頁 2010）
  - **讀哪裡**：第 1 章「The Bigger Picture」到「Levels of Testing」；第 4 章「First, Test a Walking Skeleton」；第 5 章「Start Each Feature with an Acceptance Test」「Watch the Test Fail」；選讀第 23 章「Test Diagnostics」、第 25 章「Testing Persistence」
  - **學到什麼、和本章的關聯**：雙迴圈與 walking skeleton 的原始論述；本章的 server log 區段對應第 23 章，隔離的難處對應第 25 章
  - **前提**：書用 Java、JUnit 4 與 jMock2，第 3 章的工具介紹可跳過；方法與語言無關，出版超過十年仍然適用
- **[Ham Vocke — The Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)**（martinfowler.com，2018-02-26）
  - **讀哪裡**：「Database Integration」「REST API End-to-End Test」「Acceptance Tests — Do Your Features Work Correctly?」「Avoid Test Duplication」
  - **學到什麼、和本章的關聯**：各層測試怎麼分工、為什麼把測試往下推；本章把 `end == start` 留在內圈就是這個原則
  - **前提**：範例是 Spring Boot，讀觀念即可
- **[FastAPI — Testing](https://fastapi.tiangolo.com/tutorial/testing/)** 與 **[Testing Dependencies with Overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/)**（官方文件）
  - **讀哪裡**：「Using TestClient」「Separating tests」；第二頁的 `app.dependency_overrides`
  - **學到什麼、和本章的關聯**：對比表 TestClient 那一列的正式用法；dependency overrides 是 in-process 測試替換依賴的正規做法，它和 monkeypatch 一樣碰不到另一個 process 裡的 server。注意文件（2026-11-02 讀取）寫的是安裝 `httpx`，本課 venv 的 starlette 1.10.0 卻會發出建議改裝 `httpx2` 的警告，以你的版本為準
- **[GitHub Docs — Creating PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)**
  - **讀哪裡**：「Running jobs directly on the runner machine」與「Testing the PostgreSQL service container」
  - **學到什麼、和本章的關聯**：health check 選項、job 在 runner 上為什麼連 `localhost`；第六節的 YAML 照這個形狀寫
  - **前提**：會讀 GitHub Actions 的 job 與 step；[cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md) 有 `tasktrack` 的完整版本

後端的外圈打的是 HTTP，下一章把同一套「先紅、再長」搬到瀏覽器：用 Playwright 寫 UI 的驗收測試，讓它驅動前端開發。

→ [Ch 23 前端 E2E 先行：用 Playwright 驅動 UI 開發](./23-frontend-e2e-playwright.md)
