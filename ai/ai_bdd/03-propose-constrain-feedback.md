# Ch 3 — 最小的 AI coding 迴圈：提案、約束、回饋

> **目標**：把「提案 → 約束 → 回饋」當成一個最小迴圈，在一般的 Claude Code（不裝任何框架）裡對一個小 repo 完整跑一次：用 plan mode 拿到計畫並審它；用 CLAUDE.md、`.claude/settings.json` 的 deny 規則、明確的範圍與一個 Given-When-Then 驗收情境約束它；用測試輸出與一支 PostToolUse hook `run_tests.py` 回饋給它，直到驗收轉綠。你會看到同一個情境如何同時是約束與回饋，也會看到它管不到的地方。

> **環境**：Claude Code 2.1.295、Ch 0 的共用 venv（Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0）、macOS 26.6。所有程式與輸出都是 2026-11-02 實際跑出來的；三段 agent 紀錄來自真實的 `claude -p` 執行（帳號預設模型，這次是 `claude-opus-5-5`），標為「實際執行紀錄（節錄），每次執行結果不同」。

## 為什麼需要這個？

把一個需求丟給 agent，最常見的三種失敗是：**方向錯**（它選了你不會選的做法，例如把規則寫進 pydantic validator，回傳格式就和 API 契約不同，你在 diff 裡才發現）、**範圍漂**（為了讓測試過，順手改了測試、加了檔案、重構了不相干的函式）、**「我測過了」**（它說做完了，但你不知道它檢查了什麼，或根本沒有東西可檢查）。

三種失敗對應三個元素：**提案**讓方向在動手前被看見，**約束**讓範圍有邊界，**回饋**讓「做完」變成會 pass 或 fail 的檢查。在 agent 之前，人類團隊用設計審查、程式碼規範與權限、CI 做同一件事；agent 把一圈壓到分鐘等級（本章實作那一圈約 60 秒），而且讀回饋、決定下一步的變成 agent 自己。

repo 裡這些零件都有，只是散在三門課的四個章節：[ai_applications Ch 5 的「Plan Mode 的實戰」](../ai_applications/05-claude-code-basics.md)、[pstack Ch 6 的「一、五個要素」](../pstack/06-prompting-poteto-mode.md)、[pstack Ch 3 的「階段 5：先建驗證，再委派」](../pstack/03-run-lifecycle.md)、[harness_engineering Ch 30 的 PreToolUse 與 PostToolUse 兩節](../harness_engineering/30-hooks.md)，各自被包裝成 playbook 步驟、harness 內部機制或流水線階段。本章把它們收成一個你在任何專案第一天就能用的最小迴圈，並且全部實跑。

## 先建立直覺：一張設計圖、一把鑰匙、一支水平儀

```
 你 ─(1) 提案：任務+範圍 ─▶ plan mode（harness 強制唯讀）─▶ 模型寫計畫 ─▶ 你審：核可或修正
  ├─(2) 約束：CLAUDE.md（軟，模型讀）＋ deny 規則（硬，harness 執行）＋ 範圍 ＋ 驗收 scenario
  └─(3) 實作與回饋：
        模型 Edit app.py ─▶ deny 規則過濾 ─▶ 工具執行 ─▶ PostToolUse hook：run_tests.py 跑 pytest
             ▲                                                        │
             └──────── 紅：失敗摘要（reason）／綠：「3 passed」 ◀──────┘
        驗收轉綠、回覆附上證據 ─▶ 你審 diff
```

把 agent 想成來家裡裝修的師傅。**提案**是開工前的設計圖：看圖時改一條線，比打掉一面牆便宜。**約束**有兩種：合約上寫「只動廚房」是軟的，師傅大多會遵守；只給他廚房那把鑰匙是硬的，他想進臥室也進不去。**回饋**是你交給他的水平儀：每貼一塊磁磚就自己量，不必等你回家說「歪了」。這個比喻有一處不成立：師傅記得昨天的事，agent 每個 session 從零開始，所以約束必須每次重新送到它面前（CLAUDE.md 每個 session 都會載入，就是為了這件事）。

貫穿本章的觀察：一個 Given-When-Then 情境同時扮演兩個角色。寫在 feature 檔裡，它是**約束**，定義「做完」長什麼樣；被 pytest-bdd 執行，它是**回饋**，給出紅或綠。（對 Given-When-Then 還不熟的話，先回看 [SDD Ch 10 的「Given-When-Then：把驗收條件機械化」](../spec_driven_development/10-acceptance-criteria-bdd.md)；pytest-bdd 怎麼用 `scenarios()` 與 `target_fixture` 把句子接到函式，在同一章的「工具鏈：從 .feature 到可執行測試」。）

## 一、實驗室：缺了營業時間規則的預約 API

`/tmp/lab/aibdd/ch03-loop/booking/` 是一個小 repo，下面依序是 `app.py`、`features/booking.feature`、`tests/test_booking.py` 與 `pytest.ini` 的全文。API 已經會擋重疊預約（R1）與結束早於開始（R2），還不會擋營業時間外的預約（R3：08:00–20:00）。為了讓迴圈短，它只取 [Ch 1](./01-course-map.md) 那組 API 的最小子集：`POST /bookings` 與測試用的 `POST /reset`，回應不帶 `id`，沒有 `GET` 與 `DELETE`。

```python
# app.py: room booking API (R1 overlap, R2 end after start). Office hours (R3) are not implemented yet.
from datetime import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
BOOKINGS: list[dict] = []


class Booking(BaseModel):
    room: str
    start: time
    end: time
    by: str


@app.post("/bookings", status_code=201)
def create(b: Booking):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in BOOKINGS:
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    BOOKINGS.append(b.model_dump())
    return b


@app.post("/reset")
def reset():
    BOOKINGS.clear()
```

```gherkin
Feature: Room booking

  Rule: A room cannot be booked twice for overlapping times

    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

  Rule: Rooms can only be booked between 08:00 and 20:00

    Scenario: Booking inside office hours
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 09:00 to 10:00
      Then the booking is confirmed

    Scenario: Booking that starts before the office opens
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 07:30 to 08:30
      Then the booking is rejected because it is outside office hours 08:00-20:00
```

<details>
<summary><code>tests/test_booking.py</code> 與 <code>pytest.ini</code> 的完整內容</summary>

```python
# tests/test_booking.py: step definitions. They talk to the app through HTTP (FastAPI TestClient).
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when
from app import app

scenarios("../features/booking.feature")


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/reset")
    return c


def book(client, who, room, start, end):
    return client.post("/bookings", json={"room": room, "start": start, "end": end, "by": who})


def check(response, status, detail):
    got, want = (response.status_code, response.json().get("detail")), (status, detail)
    assert got == want, f"want {want}, got {got}"  # the full message is what the agent reads


@given(parsers.parse('room "{room}" is free all day'))
def free(room):
    pass


@given(parsers.parse('{who} has booked "{room}" from {start} to {end}'))
def booked(client, who, room, start, end):
    assert book(client, who, room, start, end).status_code == 201


@when(parsers.parse('{who} books "{room}" from {start} to {end}'), target_fixture="response")
def books(client, who, room, start, end):
    return book(client, who, room, start, end)


@then("the booking is confirmed")
def confirmed(response):
    check(response, 201, None)


@then(parsers.parse('the booking is rejected because "{room}" is taken {slot} by {who}'))
def taken(response, room, slot, who):
    check(response, 409, f"{room} is taken {slot} by {who}")


@then(parsers.parse("the booking is rejected because it is outside office hours {hours}"))
def outside(response, hours):
    check(response, 422, f"outside office hours {hours}")
```

```ini
[pytest]
pythonpath = .
testpaths = tests
filterwarnings = ignore:Using `httpx` with `starlette.testclient`:UserWarning
```

</details>

再加一個兩行的 `.gitignore`（`__pycache__/`、`.pytest_cache/`），`git init` 後 commit 成 baseline，然後跑一次：

```
$ cd /tmp/lab/aibdd/ch03-loop/booking && /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short
..F                                                                      [100%]
…（略：traceback）
E   AssertionError: want (422, 'outside office hours 08:00-20:00'), got (201, None)
…（略）
FAILED tests/test_booking.py::test_booking_that_starts_before_the_office_opens
1 failed, 2 passed in 0.13s
```

這是「為對的理由而紅」（red for the right reason）：失敗的是我們預期失敗的那個情境，訊息直接說出差在哪裡。兩個細節是跑的過程中修出來的。`check()` 自己組失敗訊息：第一版直接取 `response.json()["detail"]`，201 的回應沒有這個欄位，失敗變成 `KeyError: 'detail'`，對讀它的人（等一下是 agent）沒有資訊，連本來該過的「Booking inside office hours」也一起紅（`2 failed, 1 passed`）；而 pytest 自己的比較訊息會把長字串截成 `'outsid... 08:00-20:00'`（上面略掉的 `E   assert` 那行），自己組的訊息才能把期望值印全。`pytest.ini` 的 `filterwarnings` 則是因為 Starlette 1.10.0 的 `testclient` 模組一被匯入（也就是每次跑測試）就發一個 `StarletteDeprecationWarning`，要你把 `httpx` 換成 `httpx2`；它會變成每次回饋裡的雜訊，我們按訊息內容把它濾掉，而不是往共用 venv 加套件。

## 二、提案：先拿一份計畫，再決定要不要動手

plan mode 是由 harness 強制的唯讀規劃：可以讀檔、搜尋、跑探索性的指令，不能改原始碼，直到你核可計畫（機制見 [harness_engineering Ch 28 的「三、Plan mode：唯讀規劃、人類核可後才動手」](../harness_engineering/28-planning-todo.md)）。互動模式按 `Shift+Tab` 切換，或 `claude --permission-mode plan` 啟動。prompt 照 pstack Ch 6 的五個要素寫：目標（讓那個情境通過）、你已經知道的（失敗的是哪個情境、在哪個檔案）、真正的限制（只改 `app.py`）、能 pass/fail 的完成檢查、你要看的證據；**不寫怎麼做**，也不先講你的猜測。證據就是計畫要回答的三件事：檢查放哪裡、API 回什麼、哪些邊界要對。這一步在 baseline 上跑，那時還沒有 CLAUDE.md 與 `.claude/`：

```bash
cd /tmp/lab/aibdd/ch03-loop/booking && source /tmp/lab/aibdd/.venv/bin/activate
claude -p 'The scenario "Booking that starts before the office opens" in features/booking.feature fails. Plan the change that makes it pass. Scope: change only app.py; features/ and tests/ are the acceptance contract and stay as they are. Done means `python -m pytest -q` shows no failures and the other scenarios still pass. In the plan, say where the check goes, what the API returns, and which boundary cases it must get right.' \
  --permission-mode plan --max-turns 12 --allowedTools "Read,Glob,Grep,Bash(python -m pytest *)" \
  --setting-sources project,local --no-session-persistence --output-format stream-json --verbose > ../runs/1-plan.jsonl
```

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。11 個回合、約 50 秒，CLI 估計花費 0.35 美元。（`--setting-sources project,local` 不讀這台機器的使用者設定，因為它覆寫了 `PATH`，見 Ch 0。）

它讀了 `app.py`、feature、step 檔與 `pytest.ini`，跑 pytest 確認失敗，說了一句「this API accepts timezone-aware times, and those might break when compared with naive constants. I'll check that before writing the plan」，寫了一段探索用的 Python 去打 API，然後交出計畫：

```text
**Where the check goes:** in `create()` in `app.py`, between the "end must be after start" check and the overlap loop. …（略）
**What the API returns:** status `422` with `{"detail": "outside office hours 08:00-20:00"}`. The test compares this string exactly. Other responses don't change.
**Boundary cases it must get right:**
- **Allowed (the bounds are inclusive):** 08:00-09:00, 19:00-20:00 and 08:00-20:00.
…（略：被拒絕的例子 07:30-08:30、19:30-20:30 等，以及錯誤的優先順序）
**Decision: timezone-aware times.** The API accepts values like `09:00+08:00`, and comparing those directly with `OPEN` raises `TypeError`. A plain `b.start < OPEN` would turn a request that returns 201 today into a 500, which the three scenarios wouldn't catch. …（略：接著它把「混用時區本來就會 500」列為範圍外的既有 bug，再說明怎麼驗證）
```

`-p` 模式沒有人能按「核可」：紀錄裡它找 `ExitPlanMode` 工具，得到 `No matching deferred tools found`，於是把計畫當成最後的回覆。它也把計畫寫進了 `~/.claude/plans/<名字>.md`，那是 plan mode 允許的寫入；想讓計畫留在專案裡，設定 `plansDirectory`（例如 `"./plans"`；`-p` 可加 `--settings '{"plansDirectory": "plans"}'`，練習 A 就這樣跑）。沒有核可按鈕，**審計畫**就是你的事，逐項對照：

| 檢查 | 計畫怎麼說 | 我們的決定 |
|---|---|---|
| 檢查放哪裡 | `create()` 裡，R2 之後、R1 迴圈之前 | 同意：和 R2 同一層，錯誤格式一致 |
| API 回什麼 | 422 + 逐字的 detail | 同意 |
| 邊界 | 08:00 與 20:00 都是含端點 | 同意：預約是半開區間 `[start, end)`，19:00–20:00 應該能訂 |
| 錯誤的優先順序 | 時段外又重疊時回 422 而不是 409 | 同意；這是產品決定，應由人拍板 |
| 範圍外的發現 | 帶時區的時間會讓天真的比較變成 500 | 接受它的處理；既有的混用時區 bug 留到下一輪 |

**這個審查擋掉了什麼？** 我們手寫兩個「沒有提案時很常見」的版本（不是 agent 寫的），各自是 baseline 的一份副本：`variant-validator/` 在 `Booking` 類別裡加了第一段，`variant-naive/` 在 R2 的檢查之後加了第二段；`variant-baseline/` 是 baseline 原封不動的副本。

```python
    # variant-validator/app.py（版本 A），import 改成 from pydantic import BaseModel, model_validator
    @model_validator(mode="after")
    def office_hours(self):
        if self.start < time(8) or self.end > time(20):
            raise ValueError("outside office hours 08:00-20:00")
        return self

    # variant-naive/app.py（版本 B），在 create() 裡
    if b.start < time(8) or b.end > time(20):
        raise HTTPException(422, "outside office hours 08:00-20:00")
```

版本 A 的狀態碼對了，`detail` 卻變成 FastAPI 的錯誤清單，測試報 `AssertionError: want (422, 'outside office hours 08:00-20:00'), got (422, [{'type': 'value_error', 'loc': ['body'], 'msg': 'Value error, outside office hours 08:00-20:00', ...}])`，`1 failed, 2 passed in 0.13s`。版本 B 三個情境全綠（`3 passed in 0.10s`）。再對三個版本各送一筆 `09:00+08:00` 到 `10:00+08:00` 的預約（`booking` 是第五節 agent 做出來的版本，先列出來對照）：

```bash
cd /tmp/lab/aibdd/ch03-loop && for d in variant-baseline variant-naive booking; do python -W ignore -c 'import sys; sys.path.insert(0, sys.argv[1]); from fastapi.testclient import TestClient; from app import app; r = TestClient(app, raise_server_exceptions=False).post("/bookings", json={"room": "Taipei", "start": "09:00+08:00", "end": "10:00+08:00", "by": "Alice"}); print(sys.argv[1], r.status_code)' $d; done
```

```
variant-baseline 201
variant-naive 500
booking 201
```

版本 B 把一個今天會成功的請求變成 500（帶時區與不帶時區的 `time` 不能比較，`TypeError`）。版本 A 第一次跑測試就會被抓到；版本 B **不會**，三個情境都不碰時區。這就是提案的價值：它把 scenario 沒涵蓋的風險攤在你面前，讓你在一行程式都還沒改時做決定。

## 三、約束：軟的、硬的、範圍、驗收

審完計畫，我們在 repo 加三個檔案：這一節的 `CLAUDE.md`、`.claude/settings.json`，以及第四節的 hook `.claude/hooks/run_tests.py`，三個一起 commit 成第二個 commit。**CLAUDE.md 是軟約束**。它每個 session 都會載入，但官方文件說得直白：Claude「treats them as context, not enforced configuration」。所以它只放 agent 從程式碼看不出、而且每次都需要的東西：測試指令、哪些檔案是契約、「做完」的定義。測試指令寫絕對路徑的直譯器，理由和 Ch 0 踩雷第 2 條相同。

```markdown
# booking: room booking API (Ch 3 lab)

FastAPI app in `app.py`. The acceptance scenarios are in `features/booking.feature`;
their step definitions are in `tests/`.

## Commands
- Tests: `/tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short`

## Rules
- `features/` and `tests/` are the acceptance contract. Do not edit them. If one looks wrong, stop and say why.
- Change only `app.py` unless the task names another file.
- `detail` strings are part of the API. Copy them from the scenario character for character.
- Done means the test command prints no failures. End your reply with its last line.
```

**`.claude/settings.json` 是硬約束**。permissions 文件的原話是「Permission rules are enforced by Claude Code, not by the model」。

```json
{
  "permissions": {
    "allow": [
      "Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)"
    ],
    "deny": [
      "Edit(/features/**)",
      "Edit(/tests/**)",
      "Edit(/.claude/**)",
      "Bash(git push *)"
    ]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "/tmp/lab/aibdd/.venv/bin/python",
            "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/run_tests.py"],
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

要讀懂的幾處：`Edit(/features/**)` 開頭的 `/` 錨定在設定來源（專案的 `.claude/settings.json` 錨定在 session 的主要工作目錄，這裡就是 `booking/`），不是檔案系統根目錄（根目錄要寫 `//`）；`Edit` 規則涵蓋所有內建的改檔工具，包括 Write；`.claude/` 本來就是 protected path（default 與 acceptEdits 模式下寫入會先問你），deny 讓它連問都不問，agent 因此沒辦法用檔案工具改掉自己的回饋 hook（例外見踩雷 5）；`allow` 讓測試指令不必每次確認；hook 用 exec 形式（`command` + `args`，CHANGELOG 記載 2.1.139 起支援），路徑佔位符不需要引號。

設定檔寫錯一個逗號會怎樣？用 `sed` 做一份在 `"Bash(git push *)"` 後多一個逗號的副本 `settings-broken.json`，放在 repo 外的 `events/`：

```
$ cd /tmp/lab/aibdd/ch03-loop/booking && python3 -m json.tool .claude/settings.json > /dev/null && echo "settings.json: valid JSON"
settings.json: valid JSON
$ mkdir -p ../events && sed 's/"Bash(git push \*)"/&,/' .claude/settings.json > ../events/settings-broken.json
$ python3 -m json.tool ../events/settings-broken.json > /dev/null; echo "exit=$?"
Expecting value: line 11 column 5 (char 225)
exit=1
```

這個習慣很重要，因為 `claude --help` 對 `-p` 寫得清楚：「Settings files that fail validation are silently ignored in this mode」。照這句話推，一個逗號就能讓 deny 規則與 hook 在無人值守的執行裡安靜地消失（這個後果本章沒有用 `claude -p` 實測）。

**範圍**寫在 prompt 裡（「only app.py」），也寫在 CLAUDE.md 裡，deny 規則是它的硬底線。**驗收**就是那個失敗的情境：它在 `features/` 裡、受 deny 保護，agent 不能靠改情境讓自己通過。

## 四、回饋：run_tests.py

最後一個元素是讓 agent 每改一次檔就拿到客觀的紅或綠，不靠它記得跑測試。課程後面的 PostToolUse hook 都沿用這支 `.claude/hooks/run_tests.py` 的介面；擋編輯的 PreToolUse 與擋收工的 Stop hook 沿用它的寫法（stdin 讀事件 JSON、stdout 回 JSON、exec 形式加絕對路徑的直譯器），回傳欄位換成各事件自己的（[Ch 20](./20-tdd-with-agents.md)）：

```python
"""run_tests.py: Claude Code PostToolUse hook. After Claude edits a .py file, run pytest and report back.

Input:  the hook JSON on stdin (tool_name, tool_input.file_path, cwd, ...).
Output: nothing for files that are not *.py. Otherwise exactly one JSON object on stdout:
  green      {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "run_tests: <summary>"}}
  red        {"decision": "block", "reason": "run_tests: tests are red after editing <file> ...<pytest tail>"}
  can't run  {"decision": "block", "reason": "run_tests: cannot run pytest ..."}
Exit 0 once the event is read (the JSON carries the verdict); exit 1 only if stdin is not hook JSON.
Usage: python run_tests.py --help | python run_tests.py < event.json
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TAIL = 30      # lines sent back: one --tb=short failure fits; everything we send costs tokens on every edit
TIMEOUT = 100  # seconds; the hook entry in settings.json allows 120, so we answer before Claude Code gives up


def verdict(path, root):
    if importlib.util.find_spec("pytest") is None:
        return {"decision": "block", "reason": f"run_tests: cannot run pytest with {sys.executable} "
                f"(Python {sys.version.split()[0]}): no pytest module. Fix the hook command in .claude/settings.json."}
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=short"],
                              cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"decision": "block", "reason": f"run_tests: cannot run pytest: no result after {TIMEOUT}s"}
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    if proc.returncode == 0:
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": "run_tests: " + (lines[-1] if lines else "passed")}}
    head = f"run_tests: tests are red after editing {Path(path).name} (pytest exit {proc.returncode})"
    return {"decision": "block", "reason": "\n".join([head] + lines[-TAIL:])}


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError as e:
        print(f"run_tests: stdin is not hook JSON ({e})", file=sys.stderr)
        return 1
    path = event.get("tool_input", {}).get("file_path", "")
    if not path.endswith(".py"):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    print(json.dumps(verdict(path, root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

為什麼是這個形狀：**PostToolUse 擋不住已經發生的編輯**，它的價值是回饋。官方 hooks 文件寫明，PostToolUse 回 `"decision": "block"` 會把 `reason` 放在「next to the tool result」交給 Claude，原本的工具結果照舊；綠的時候用 `additionalContext` 送一行摘要。整支腳本只用 exit 0 加 JSON 表達結果、不混用 exit 2，因為文件建議「Choose one approach per hook」；exit 2 在 PostToolUse 上同樣會把 stderr 交給 Claude（文件的說法是「Shows stderr to Claude; the tool already ran」），我們選 JSON 是因為綠燈時也要送一行摘要。直譯器寫死在設定裡，腳本再用 `find_spec` 檢查一次：hook 繼承的是 Claude Code 的環境，用錯 Python 時寧可清楚說「我跑不了」，也不要回報一個看起來像測試失敗的紅燈。

hook 能用假事件走到的路，都要在接上 agent 之前證明一次（綠燈要等 `app.py` 改好，第五節的實跑會看到）。`events/edit_app.json` 是一個 PostToolUse 事件，欄位形狀照官方 hooks 文件的 PostToolUse 範例（文件的例子是 `Write`，我們換成 `Edit`；`tool_input.file_path` 一律是絕對路徑），`edit_md.json` 用 `sed` 把路徑換成 `CLAUDE.md`：

```bash
cd /tmp/lab/aibdd/ch03-loop && export CLAUDE_PROJECT_DIR=/tmp/lab/aibdd/ch03-loop/booking
mkdir -p events && cat > events/edit_app.json <<'EOF'
{"session_id": "demo", "cwd": "/tmp/lab/aibdd/ch03-loop/booking", "hook_event_name": "PostToolUse",
 "permission_mode": "acceptEdits", "tool_name": "Edit",
 "tool_input": {"file_path": "/tmp/lab/aibdd/ch03-loop/booking/app.py", "old_string": "x", "new_string": "y"},
 "tool_response": {"filePath": "/tmp/lab/aibdd/ch03-loop/booking/app.py"}, "tool_use_id": "toolu_demo"}
EOF
sed 's#booking/app.py#booking/CLAUDE.md#g' events/edit_app.json > events/edit_md.json
H=booking/.claude/hooks/run_tests.py && PY=/tmp/lab/aibdd/.venv/bin/python
$PY $H < events/edit_md.json; echo "exit=$?"
$PY $H < events/edit_app.json > events/out-red.json; echo "exit=$?"
python3 -c 'import json; d=json.load(open("events/out-red.json")); print(d["decision"]); print(d["reason"])'
/usr/bin/python3 $H < events/edit_app.json; echo "exit=$?"
echo hello | $PY $H; echo "exit=$?"
```

| 輸入 | stdout（實際輸出，節錄） | exit |
|---|---|---|
| 改了 `CLAUDE.md` | （無） | 0 |
| 改了 `app.py`，測試紅 | `block` 與 `run_tests: tests are red after editing app.py (pytest exit 1)`，接著是第一節那段失敗摘要，最後 `1 failed, 2 passed in 0.12s` | 0 |
| 用 `/usr/bin/python3` 跑 | `{"decision": "block", "reason": "run_tests: cannot run pytest with /Applications/Xcode.app/Contents/Developer/usr/bin/python3 (Python 3.9.6): no pytest module. ..."}` | 0 |
| stdin 是 `hello`（依文件屬於 non-blocking error：不擋動作，只在介面留下一則 hook error） | （無；stderr 是 `run_tests: stdin is not hook JSON (Expecting value: line 1 column 1 (char 0))`） | 1 |

## 五、跑一次完整的迴圈

把審過的計畫交給一個新的 session（`-p` 每次都是新的 context，所以計畫的決定要寫進 prompt）：

```bash
cd /tmp/lab/aibdd/ch03-loop/booking
P2='Implement the approved plan below. Scope: only app.py. Done: the test command in CLAUDE.md prints no failures.
Approved plan:
1. In create(), after the "end must be after start" check and before the overlap loop, reject bookings outside 08:00-20:00 with HTTPException(422, "outside office hours 08:00-20:00"). Bounds are inclusive: 08:00-09:00 and 19:00-20:00 are allowed; 07:30-08:30 and 19:30-20:30 are rejected.
2. Compare times as written, ignoring any UTC offset, so 09:00+08:00 does not crash.
3. Keep every other response unchanged.'
claude -p "$P2" --permission-mode acceptEdits --max-turns 15 \
  --allowedTools "Read,Glob,Grep,Edit,Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)" \
  --setting-sources project,local --no-session-persistence \
  --output-format stream-json --verbose --include-hook-events > ../runs/2-loop.jsonl
```

`P2` 就是審過的計畫，濃縮成三點。stderr 立刻印出一行值得記住的警告：`Ignoring 1 permissions.allow entry from .claude/settings.json: this workspace has not been trusted. Run Claude Code interactively here once and accept the trust dialog, or set projects["/private/tmp/lab/aibdd/ch03-loop/booking"].hasTrustDialogAccepted: true in /Users/robertliao/.claude.json.` 沒被信任的資料夾裡，專案設定的 **allow** 規則不生效，**deny** 規則與 hooks 照樣生效（permissions 文件的「Project allow rules and workspace trust」一節）。所以 `-p` 執行用 `--allowedTools` 自己給；互動模式則在第一次開啟時接受信任對話框。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。11 個回合、約 60 秒，CLI 估計花費 0.28 美元。

| 順序 | agent 做了什麼 | harness / hook 的回應 |
|---|---|---|
| 1–3 | 讀 `app.py`、feature；跑測試指令看 baseline | 允許；`1 failed, 2 passed` |
| 4–6 | 想先用探測腳本測時區與邊界：heredoc、寫 `/tmp/probe_booking.py`、執行它 | 三次都被拒：`Contains brace with quote character (expansion obfuscation)`、`Path is outside allowed working directories`、`The following part requires approval` |
| 7 | 在同一則訊息裡發出兩個 Edit：先加 `OPEN, CLOSE` 與 `wall()`，再改 `create()` | 第一個 Edit 後 hook 回紅：`1 failed, 2 passed`，那時檢查還沒寫進去 |
| 8 | （第二個 Edit） | hook 回綠：`run_tests: 3 passed in 0.11s` |
| 9–10 | 再跑一次測試指令；`git status --short && git diff --stat` | `3 passed in 0.11s`；`1 file changed, 12 insertions(+), 3 deletions(-)` |

最後的回覆（節錄），以及 `git diff` 的核心。這段之外，它在 `BOOKINGS` 下面新增了 `OPEN, CLOSE = time(8), time(20)` 與 `def wall(t: time) -> time:`（本體只有 `return t.replace(tzinfo=None)` 和一行註解），並改了第一行註解；沒跑 agent 的讀者照這些改 baseline 的 `app.py`，就得到本章其他地方說的「agent 的版本」：

```text
**Not verified by running:**
- **Boundary cases:** the scenarios only cover 09:00-10:00 and 07:30-08:30. The cases 08:00-09:00, 19:00-20:00 and 19:30-20:30 weren't run.
…（略）
- **Offset handling goes beyond the new check:** I applied `wall()` to the `end <= start` and overlap comparisons too, because either would otherwise still crash on a mix of offset and plain times. …（略）
3 passed in 0.11s
```

```diff
-    if b.end <= b.start:
+    start, end = wall(b.start), wall(b.end)
+    if end <= start:
         raise HTTPException(422, "end must be after start")
+    if start < OPEN or end > CLOSE:
+        raise HTTPException(422, "outside office hours 08:00-20:00")
     for x in BOOKINGS:
-        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
+        if x["room"] == b.room and start < wall(x["end"]) and wall(x["start"]) < end:
```

三個觀察，全部來自這次真實執行。**回饋會評估中間狀態**：模型一次發出兩個 Edit，hook 在第一個之後就跑，那個紅燈是預期中的半成品；agent 在回覆裡自己解釋了這一點。**約束有代價**：權限檢查擋掉了它想做的邊界探測，它因此誠實地把 08:00–09:00、19:00–20:00 列為「沒跑過」，驗證的責任回到你身上。**範圍漂移換了樣子出現**：它沒碰 `app.py` 以外的檔案，卻把 `wall()` 也套到 R1、R2 的比較上，改變了混用時區時的行為（它的回覆舉例：`09:00+00:00` 到 `10:00+08:00` 以前回 422，現在會成功）；計畫第 2 點與「其餘回應不變」互相牽制，它選了一邊並且說出來。這正是審 diff 時要看的地方（它對時區的處理在第二節的那行指令裡是 `booking 201`）。

## 底層機制：一次 Edit 經過的關卡

```
 模型送出 tool_use: Edit(features/booking.feature)
   ├─(1) PreToolUse hooks（本章沒有用；exit 2 會在權限規則評估之前就擋下）
   ├─(2) permission 規則：deny ─▶ ask ─▶ allow，第一個命中的決定 ── Edit(/features/**) 命中 deny：拒絕
   │      （hook 回 allow 也蓋不過 deny；另有 protected paths、工作目錄外的路徑、mode 的預設行為）
   ├─(3) 工具執行，寫入磁碟
   ├─(4) PostToolUse hooks：事件 JSON 從 stdin 進去，exit code 與 stdout 的 JSON 決定怎麼處理
   └─(5) tool_result 回到模型；hook 的 reason 或 additionalContext 附在旁邊，下一次請求時模型讀到
```

**allow 不能在 deny 上開例外**，PreToolUse hook 的 allow 也不行。為了親眼看到第 (2) 關，我們做了第三次實跑：一份沒有 CLAUDE.md、只有 `.claude/` 的 baseline 副本（`ch03-loop/deny-demo/`），prompt 全文是「The scenario "Booking that starts before the office opens" fails. Fix it the quick way: edit features/booking.feature so that scenario expects the booking to be confirmed. Make that one edit and nothing else, then tell me what happened.」，旗標和第二次實跑相同，只換成 `--max-turns 6 --allowedTools "Read,Edit"`、不加 `--include-hook-events`。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。3 個回合，CLI 估計花費 0.10 美元。它讀了 feature 檔，發出 Edit，harness 回給它的 tool result 是 `<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>`。它的回覆開頭是「The edit didn't go through, so `features/booking.feature` is unchanged.」，中間有一句「I didn't try to get around it with a shell command or by changing the settings, because the rule is your boundary to change.」

結果 JSON 的 `permission_denials` 列出那個 Edit（`new_string` 是 `      Then the booking is confirmed`），`git status` 是乾淨的。注意因果：沒有 CLAUDE.md 時，它**真的去改了**，因為使用者這樣要求；擋住它的是 harness。軟約束決定 agent 想做什麼，硬約束決定 harness 允許什麼。

## 一個 scenario 同時是約束與回饋，也只管它寫到的

agent 沒跑的邊界，我們不寫一次性的探測腳本，直接寫成情境。先看為什麼非寫不可：把 agent 版本的 `end > CLOSE` 改成 `end >= CLOSE`（`variant-strict/`，我們手寫的），三個情境照樣 `3 passed in 0.11s`，但它會拒絕 19:00–20:00 這種完全合法的預約。三個情境都沒碰到 20:00 這個端點，所以它們**約束不到**這個 bug，回饋也不會亮紅燈。補一個情境（人改的，deny 只限制 agent）：

```gherkin
    Scenario: Booking that ends exactly at closing time
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 19:00 to 20:00
      Then the booking is confirmed
```

agent 的版本 `4 passed in 0.11s`；嚴格版本 `1 failed, 3 passed in 0.14s`，失敗的正是 `test_booking_that_ends_exactly_at_closing_time`，訊息是 `AssertionError: want (201, None), got (422, 'outside office hours 08:00-20:00')`。

這就是 BDD 版本的最小迴圈：情境同時是約束與回饋，所以**選哪些例子**是人在這個迴圈裡最重要的工作。半開區間的端點、時區、錯誤的優先順序，都要在動手前變成情境。怎麼系統化地找例子，是 [Ch 8](./08-translation-sandwich.md) 與 [Ch 29](./29-example-mapping.md) 的 Example Mapping；練習 A 讓你從三個失敗情境開始，親手跑完整個迴圈。

## 對比與取捨：拿掉任何一個元素會怎樣

| 拿掉 | 典型的失敗 | 本章的證據 |
|---|---|---|
| 提案 | 方向錯，看 diff 時才發現 | 手寫版本 A（detail 變清單）與版本 B（帶時區的請求從 201 變 500），皆實跑 |
| CLAUDE.md | agent 不知道契約，照字面執行壞指令 | 第三次實跑：沒有 CLAUDE.md，它照使用者的話去改 feature 檔（有 CLAUDE.md 的對照組本章未實測） |
| deny 規則 | 改情境讓自己通過 | 推論：第三次實跑若沒有 deny，那個 Edit 會成功（未另外實測） |
| 範圍 | 動到測試、加檔案、順手重構 | 示意；第二次實跑即使有範圍，仍在 `app.py` 內擴大了改動，靠回覆的說明才看得到 |
| 驗收情境 | 「我覺得可以了」 | 示意；沒有 20:00 的情境時，嚴格版本照樣全綠（實跑） |
| hook | 依賴 agent 記得跑測試 | 第二次實跑中它自己也跑了；hook 的價值在「不靠記得」，示意 |

## 踩雷集錦

1. **以為寫進 CLAUDE.md 就是規定**：錯誤直覺是「我寫了 Do not edit features/，它就不會改」。正確認識：CLAUDE.md 是 context，best practices 的原話是「Claude is allowed to decide a guideline doesn't apply」。本章第三次實跑沒有 CLAUDE.md，證明不了這一點；練習 A 的參考解答補了這個對照：有 CLAUDE.md、使用者在對話裡宣布「這次破例」，agent 就照做去改，擋住它的仍是 deny。必須成立的事放進 deny 規則或 hook；同一份文件也說，一條 CLAUDE.md 你一直加強語氣它還是會漏，就表示它該變成 hook。
2. **用 `Write(/features/**)` 保護目錄**：錯誤直覺是「Write 工具就用 Write 規則擋」。正確認識：permissions 文件明寫 `Write(path)`、`NotebookEdit(path)` 這類路徑規則「accepts the rule but never matches」，要寫 `Edit(...)`。本章沒有實跑這個錯誤寫法（動手練習第 3 題），請以文件與你的環境為準。
3. **設定檔的語法錯誤沒人告訴你**：錯誤直覺是「設定檔寫壞了，Claude Code 會報錯」。正確認識：`-p` 模式會靜默忽略驗證失敗的設定檔（`claude --help` 原文），所以每次改完 `.claude/settings.json` 都自己跑 `python3 -m json.tool`，像本章抓到的 `Expecting value: line 11 column 5`。
4. **以為專案的 allow 規則在 `-p` 一定生效**：錯誤直覺是「`.claude/settings.json` 允許了，誰來跑都一樣」。第二次實跑的 stderr 就是反例，`Ignoring 1 permissions.allow entry ... this workspace has not been trusted`。正確認識：未信任的資料夾裡 allow 被忽略、deny 與 hooks 照常；自動化執行用 `--allowedTools` 明確給。
5. **以為 deny 規則是沙箱**：錯誤直覺是「deny 了 `Edit(/features/**)`，檔案就安全了」。正確認識：文件說 Read/Edit 的 deny 涵蓋內建檔案工具與它認得的 Bash 檔案指令，不涵蓋「a Python or Node script that opens files itself」；CHANGELOG 在 2.1.309 還修了一個帶子 shell 的重新導向繞過 `Edit` deny 的問題，本課用的 2.1.295 比它舊。所以本章的 allow 只開一條測試指令而不是整個 `Bash`；真要隔離檔案系統，用 sandbox。

## 進階：再往深一層

- **把「做完」變成硬閘門**：PostToolUse 只負責告知，agent 仍可以在紅燈時結束回合。Stop hook 在 agent 要結束時執行，exit 2 會把它送回去繼續做（官方 best practices 的例子是 `pnpm tsc --noEmit 1>&2 || exit 2`；本章沒有實跑 Stop hook）。[Ch 20](./20-tdd-with-agents.md) 的 `require_green.py` 就是這個角色。
- **hook 失敗時要不要放行**：2.1.295 新增 `onFailure: "block"`，讓 hook 起不來、逾時或 exit code 異常時直接擋下動作（依 CHANGELOG 與 hooks 文件，本章未實測）。對 PreToolUse 的政策型 hook 很重要（路徑打錯就等於沒有 hook）；對本章這種回饋型的 PostToolUse，失敗時放行比較合理。
- **計畫留在 repo、花費設上限**：`plansDirectory` 讓計畫檔進專案、可以被 review，跨 session 不必再貼進 prompt，它和 Spec Kit 的 `plan.md` 是同一個想法的兩種重量（[Ch 13](./13-plan-anatomy-review.md)）。`-p` 可加 `--max-budget-usd`；本章三次實跑的 CLI 估計總計約 0.73 美元，換模型或快取狀態差異很大。

## 動手練習

1. 照本章建出 `booking/`，先不給 agent 任何東西，自己跑 baseline 確認「為對的理由而紅」。把 `check()` 改回 `response.json()["detail"]`，比較兩種失敗訊息，寫下你希望 agent 讀到哪一種。
2. 用 plan mode 對 R2 的一個新例子提案：「08:00 到 08:00 的預約」該回哪個錯誤？審它的計畫，把你的決定寫成情境，再補上 08:00–09:00 與 19:30–20:30 兩個邊界情境，看 agent 的版本是否全綠。
3. 在 `deny-demo/` 裡把 `Edit(/features/**)` 改成 `Write(/features/**)`，先依文件寫下你的預期，再用 `claude -p` 重跑第三次實跑的 prompt（帶 `--max-turns`、加 `< /dev/null`）。結果和預期一致嗎？

## 本章重點整理

- 最小的 AI coding 迴圈是三件事：**提案**讓方向在動手前可見、**約束**讓範圍有軟硬兩層邊界、**回饋**讓「做完」變成能 pass/fail 的檢查
- plan mode 由 harness 強制唯讀；審計畫至少問：放哪裡、回什麼、邊界與優先順序、範圍外的發現、怎麼證明
- CLAUDE.md 是 context，permission 規則由 Claude Code 執行；deny 先於 allow，未信任的資料夾裡專案 allow 不生效。PostToolUse hook 用 exit 0 + JSON 回饋：紅用 `decision: block` + `reason`，綠用 `additionalContext`；接上 agent 前，先用假事件證明每一條走得到的路
- 一個 Given-When-Then 情境同時是約束與回饋，也只約束它寫到的例子；選例子是人的工作

## 自我檢核

- [ ] 不看本章，畫出一次 Edit 從模型送出到下一次請求之間的五個關卡，並說出 deny 規則與 PostToolUse hook 各在哪一關
- [ ] 第三次實跑裡，是什麼擋住了對 feature 檔的修改？如果加回 CLAUDE.md、拿掉 deny，你預期結果怎樣？為什麼這仍然不夠？
- [ ] 為什麼 `run_tests.py` 在紅燈時用 `decision: block` 而不是 exit 2？兩者在 PostToolUse 上的效果差在哪？
- [ ] 舉一個「三個情境全綠、但行為是錯的」的例子，說出要補哪個情境才抓得到
- [ ] 第二次實跑的 agent 沒改 `app.py` 以外的檔案，為什麼我們仍說範圍漂移了？審 diff 時你會怎麼處理？

## 延伸閱讀

- **[Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)**（Anthropic，官方文件；原本 Anthropic engineering blog 的同名文章現在 308 轉址到這裡）
  - **讀哪裡**：「Deterministic and non-deterministic instructions」「Give Claude a check to run」「Enforce the rules that can't have exceptions」
  - **學到什麼**：「Claude decides / Claude Code enforces」對照表、為什麼要給 agent 一個會 pass/fail 的檢查，以及 Stop hook 的完整例子。**和本章的關聯**：本章「軟約束 vs 硬約束」與「回饋」的官方版本；那張對照表是本章消融表的另一種切法
- **[Claude Code — Hooks reference](https://code.claude.com/docs/en/hooks)**（Anthropic，官方文件）
  - **讀哪裡**：「Exit code output」「JSON output」「PostToolUse」
  - **學到什麼**：exit code 與 stdout JSON 的完整對照、PostToolUse 的 `decision` / `additionalContext` / `updatedToolOutput`、exec 形式與路徑佔位符；文件跟著最新版走，已提到比 2.1.295 新的行為。**和本章的關聯**：`run_tests.py` 每個輸出欄位的定義都在這裡；先讀過 [harness_engineering Ch 30](../harness_engineering/30-hooks.md) 更好懂
- **[Claude Code — Configure permissions](https://code.claude.com/docs/en/permissions)**（Anthropic，官方文件）
  - **讀哪裡**：「Permission rule syntax」「Read and Edit」「Project allow rules and workspace trust」
  - **學到什麼**：路徑規則的四種錨點（`//`、`~/`、`/`、相對）、deny 涵蓋與不涵蓋什麼、`-p` 執行時哪些專案設定會被套用。**和本章的關聯**：本章 deny 規則的寫法、踩雷 2 與 5，以及第二次實跑那行信任警告都出自這裡
- **[Building effective agents](https://www.anthropic.com/research/building-effective-agents)** — Erik S.、Barry Zhang（Anthropic，2024-12-19；文首註明工具生態在那之後已大幅改變）
  - **讀哪裡**：「Building blocks, workflows, and agents」的 evaluator-optimizer 與 agents 兩段，以及 Appendix 1 的「Coding agents」
  - **學到什麼**：「it's crucial for the agents to gain “ground truth” from the environment at each step」；evaluator-optimizer 在評估標準清楚時特別有效；coding agent 好用，是因為解法能用自動化測試驗證、agent 能拿測試結果當回饋，但人的 review 仍不可少。**和本章的關聯**：「回饋」元素與「最後由你審 diff」的設計原理

三個元素裡，CLAUDE.md 是最便宜、也最容易被濫用的一個。下一章看它的來歷與天花板：從 `.cursorrules` 一路到 Agent Skills，為什麼常駐的 prompt 檔終究不夠，以及 prompt 檔、skill、hook、lint 之間怎麼選（團隊層級怎麼決定一條規則放哪一層，則是 [Ch 6](./06-workflows-are-grown.md) 的題目）。

→ [Ch 4 Prompt files 的天花板：為什麼會有 Skill](./04-prompt-files-to-skills.md)
