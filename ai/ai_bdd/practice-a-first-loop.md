# 練習 A — 跑一次完整的提案、約束、回饋迴圈

> **目標**：把 Ch 3 的最小迴圈從頭到尾親手跑一次。起點是一個有三個失敗情境的預約 API（重疊、背對背、結束早於開始）；你負責提案與審查、寫 CLAUDE.md 與 deny 規則、接上 `run_tests.py` hook，讓 agent 把情境修綠。交件時要附上三種證據：套件全綠、hook 的五條路都用假事件證明過、deny 規則擋下過一次真實的修改嘗試。

> **環境**：Claude Code 2.1.295、Ch 0 的共用 venv（Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0）、macOS 26.6。參考解答的三次 `claude -p` 是 2026-11-02 的實際執行紀錄（帳號預設模型 `claude-opus-5-5`），其餘輸出也都是實際跑出來的。

## 背景與動機

同事交給你一個「先能動再說」的預約 API 初稿，PM 已經把驗收情境寫好了，四個情境裡三個是紅的。你可以自己修：三個 bug 加起來不到十行。但這個練習要你做的是另一件事：**把迴圈搭起來，讓 agent 修，而你拿得出證據**。這是你之後每天會重複的工作形狀：情境由人寫、程式由 agent 寫、證據由 harness 和測試產生，而你審的是計畫和 diff。

三個 bug 各自代表一種常見的失敗：狀態碼與訊息不符合契約（R1 重疊時該回 409，它回 `400 Room not available`）、邊界寫錯（R1 用 `<=` 比較，把 10:00–11:00 與 11:00–12:00 當成重疊）、缺了一條規則（R2：沒檢查結束時間要晚於開始時間）。它們剛好都是「情境寫得出來、agent 修得掉、但你不看證據就不知道它到底修了什麼」的類型。

> 如果你還沒讀 [Ch 3](./03-propose-constrain-feedback.md)，先讀它：本練習沿用它的 `run_tests.py`、`settings.json` 與審計畫的方法。

## 任務規格

| 交付物 | 驗收標準 |
|---|---|
| `booking/` repo | 起點 commit 跑出 `3 failed, 1 passed`，三個失敗的理由各不相同 |
| 提案 | 一次 plan mode 的提案；你用審查表逐項寫下同意或修改，存進 repo |
| `CLAUDE.md` | 20 行以內；含測試指令、哪些檔案是契約、範圍、「做完」的定義 |
| `.claude/settings.json` | deny `features/`、`tests/`、`.claude/` 的編輯；allow 測試指令；PostToolUse 接 `run_tests.py`；通過 `python3 -m json.tool` |
| hook 證據 | 用假事件跑過五條路：改非 `.py` 檔、紅、綠、直譯器沒有 pytest、stdin 不是 JSON |
| 實作 | **由 agent 修改 `app.py`**；`pytest -q` 印出 `4 passed`；從起點到最後，`.claude/`、`CLAUDE.md`、`plans/` 以外只有 `app.py` 變動 |
| deny 證據 | 一次真實的編輯嘗試被擋下：`claude -p` 結果 JSON 的 `permission_denials`（被權限擋下的工具呼叫清單）列出它，或 agent 收到 `File is in a directory that is denied by your permission settings.` |

規則：不准改 `features/` 與 `tests/` 讓自己通過；`claude -p` 的每次執行都要帶 `--max-turns`，並用 `--allowedTools` 只預先核准需要的工具。它不會把其他工具拿掉，只是在 `-p` 裡沒人能按核准，需要核准卻沒被預先核准的呼叫會直接被拒；要縮小工具集合本身，用 `--tools`。互動模式也可以，但要留下對話中的證據。

**起點 repo**（`/tmp/lab/aibdd/practice-a-first-loop/booking/`）。`pytest.ini`、`.gitignore` 與 Ch 3 第一節完全相同；`tests/test_booking.py` 複製 Ch 3 的版本，只把最後一個 `@then`（營業時間那個）換成本練習需要的這個：

```python
@then("the booking is rejected because the end must be after the start")
def reversed_range(response):
    check(response, 422, "end must be after start")
```

`app.py` 是初稿，和 Ch 3 一樣只取全課 API 的最小子集（`POST /bookings` 與 `POST /reset`，回應不帶 `id`，沒有 `GET` 與 `DELETE`）：

```python
# app.py: room booking API, first draft
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
    for x in BOOKINGS:
        if x["room"] == b.room and b.start <= x["end"] and x["start"] <= b.end:
            raise HTTPException(400, "Room not available")
    BOOKINGS.append(b.model_dump())
    return b


@app.post("/reset")
def reset():
    BOOKINGS.clear()
```

`features/booking.feature`：

```gherkin
Feature: Room booking

  Rule: A room cannot be booked twice for overlapping times

    Scenario: Booking a free slot
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 10:00 to 11:00
      Then the booking is confirmed

    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

    Scenario: Booking right after another booking
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed

  Rule: A booking must end after it starts

    Scenario: Booking that ends before it starts
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 11:00 to 10:00
      Then the booking is rejected because the end must be after the start
```

## 期望輸出範例

起點（`git init` 並 commit 之後）：

```
$ /tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short | grep -E "^E   AssertionError|^FAILED|passed|failed"
E   AssertionError: want (409, 'Taipei is taken 10:00-11:00 by Bob'), got (400, 'Room not available')
E   AssertionError: want (201, None), got (400, 'Room not available')
E   AssertionError: want (422, 'end must be after start'), got (201, None)
FAILED tests/test_booking.py::test_booking_an_overlapping_slot - AssertionErr...
FAILED tests/test_booking.py::test_booking_right_after_another_booking - Asse...
FAILED tests/test_booking.py::test_booking_that_ends_before_it_starts - Asser...
3 failed, 1 passed in 0.17s
```

交件時：

```
$ /tmp/lab/aibdd/.venv/bin/python -m pytest -q
....                                                                     [100%]
4 passed in 0.11s
$ git diff --stat <起點 commit> HEAD -- . ':(exclude).claude' ':(exclude)CLAUDE.md' ':(exclude)plans'
 app.py | 7 +++++--
 1 file changed, 5 insertions(+), 2 deletions(-)
```

邊界輸入：同一個 hook，餵它「改了 `app.py`」的假事件，在修好之後應該回綠的 `additionalContext`，在修好之前回 `decision: block`；被 deny 擋下的嘗試，agent 收到的 tool result 是 `<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>`。

## 如果你卡住了

1. **agent 說「測試都過了」，但你看不到輸出**：把「End your reply with its last line」寫進 CLAUDE.md 的完成定義。這是軟約束，但 agent 多半會照做：參考解答第二次實跑的回覆就以 `4 passed in 0.13s` 結尾。光說「過了」不算數。
2. **hook 好像沒跑**：先離開 agent，自己用假事件跑一次（Ch 3 第四節的做法）。沒輸出可能是檔名不是 `.py`；回「cannot run pytest」是直譯器不對；完全沒反應則檢查 `matcher` 是否為 `Edit|Write`、路徑是否用 `${CLAUDE_PROJECT_DIR}`。要看 Claude Code 這一端的紀錄，官方 hooks 文件的「Debug hooks」一節說 hook 的執行細節寫在 debug log：用 `claude --debug-file <路徑>` 啟動，或用 `claude --debug` 啟動後讀 `~/.claude/debug/<session-id>.txt`（`--debug` 不會印到終端機）。本段未實測，為文件描述的行為；要驗證，觸發一次 Edit 後在 log 裡搜 `PostToolUse`。
3. **`claude -p` 跑不了測試指令**：stderr 若有 `Ignoring 1 permissions.allow entry ... this workspace has not been trusted`，表示專案的 allow 規則沒生效；在 `--allowedTools` 裡明確給測試指令。
4. **hook 的紅燈只看得到兩個失敗**：`run_tests.py` 只回傳最後 30 行，三個情境同時失敗時，第一個失敗的 `AssertionError` 會被切掉，只剩 `FAILED` 那一行的名字。讀 `FAILED` 清單，或做延伸挑戰第一題。
5. **deny 規則從來沒被觸發**：有 CLAUDE.md 時，agent 多半會照它的規則不去碰契約檔（這一點本練習沒有另外實測），所以你看不到硬約束發揮作用。要證明它，得讓 agent 真的去嘗試：例如你明確地對 CLAUDE.md 開例外（參考解答的做法），或像 Ch 3 一樣在沒有 CLAUDE.md 的副本裡測。
6. **409 的 detail 對不上**：`str(time(10, 0))` 是 `10:00:00`，情境要的是 `10:00`。這正是提案階段該問的「API 回什麼」。

## 實作步驟建議

### Step 1：建起點 repo，確認「為對的理由而紅」

建出上面的檔案，`git init`、commit，跑期望輸出範例的第一個指令。三個 `AssertionError` 應該各說出一個不同的問題；如果有任何一個是 `KeyError` 或 `ImportError`，先修環境，不要交給 agent。

### Step 2：提案與審查

用 plan mode 要一份計畫（互動模式 `claude --permission-mode plan`，或 `claude -p --permission-mode plan`）。prompt 寫目標、完成檢查、範圍，並要求計畫說出每個失敗的原因、每種情況 API 回什麼、重疊檢查的邊界。設定 `plansDirectory` 讓計畫檔存進 repo。用 Ch 3 的審查表逐項寫下你的決定，連同計畫一起 commit。

### Step 3：約束

寫 `CLAUDE.md`；從 Ch 3 複製 `.claude/settings.json` 與 `.claude/hooks/run_tests.py`（或自己寫一份），跑 `python3 -m json.tool` 驗證；commit。想一想：你的審查決定裡，有沒有哪一條值得寫進 CLAUDE.md？官方 memory 文件的「When to add to CLAUDE.md」列了四個時機，第一個是「Claude makes the same mistake a second time」，並要你只放「facts Claude should hold in every session」；只屬於這次任務的決定（例如「時區先不處理」）留在計畫與 prompt 裡。

### Step 4：證明回饋

做兩個假事件：改了 `app.py`、改了 feature 檔。在 agent 動手之前跑一次（應該是紅的與靜默的），再用 `/usr/bin/python3` 與一段壞掉的 JSON 各跑一次，把這四個輸出存下來；第五條路（綠）等 agent 修好再跑。

### Step 5：讓 agent 實作，然後審

把審過的計畫交給 agent。觀察 hook 的事件（`--include-hook-events`），等它回報；然後**你自己**再跑一次測試、看 `git diff`，並對情境沒涵蓋的輸入做一次探測。審過之後由你 commit（參考解答的 agent 沒有 commit，`--allowedTools` 也沒給它 `git commit`）：期望輸出範例的 `git diff --stat ... HEAD` 比的是 commit，沒 commit 它什麼都不會印。

### Step 6：證明約束

設計一個請求，讓 agent 真的去嘗試改 `features/`，並確認 harness 擋下它。記下 agent 收到的錯誤與 `permission_denials`。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### Step 2：提案（第一次實跑）

prompt 與執行紀錄放在 repo 外的 `practice-a-first-loop/runs/`。先把指令下面那段 prompt 存成 `runs/1-prompt.txt`，再跑：

```bash
cd /tmp/lab/aibdd/practice-a-first-loop/booking && source /tmp/lab/aibdd/.venv/bin/activate
claude -p "$(cat ../runs/1-prompt.txt)" --permission-mode plan --max-turns 12 \
  --allowedTools "Read,Glob,Grep,Bash(python -m pytest *)" --settings '{"plansDirectory": "plans"}' \
  --setting-sources project,local --no-session-persistence --output-format stream-json --verbose \
  < /dev/null > ../runs/1-plan.jsonl
```

`runs/1-prompt.txt` 的內容：

```text
Three scenarios in features/booking.feature fail: an overlapping booking, a booking right after another one, and a booking that ends before it starts. Plan the change that makes all four scenarios pass. Scope: change only app.py; features/ and tests/ are the acceptance contract and stay as they are. Done means `python -m pytest -q` shows no failures. In the plan, say what causes each failure, what the API returns in each case, and which boundary cases the overlap check must get right.
```

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。12 個回合、約 60 秒，CLI 估計花費 0.31 美元。

它讀了全部檔案、跑了測試，然後說「Before writing the plan, I'll try the proposed code in memory against the real suite and the boundary cases, without changing any files.」：在 plan mode 裡用一段 Python 把它打算寫的程式碼放進記憶體跑過整個套件。計畫檔寫進了 `plans/three-scenarios-in-features-booking-feat-peaceful-spark.md`（`plansDirectory` 生效，沒有寫到 `~/.claude/plans/`）。最後的回覆：

```text
…（略：開頭說明它沒改任何檔案、ExitPlanMode 不可用）
| Scenario | Returns now | Should return | Cause |
| --- | --- | --- | --- |
| Overlapping booking | `400 "Room not available"` | `409 "Taipei is taken 10:00-11:00 by Bob"` | The clash is detected, but the status and message are wrong. |
| Booking right after another | `400 "Room not available"` | `201` | `<=` counts 11:00 end against 11:00 start as a clash. |
| Ends before it starts | `201` | `422 "end must be after start"` | Nothing compares `end` with `start`. |

**The change:** in `create()`, reject `end <= start` first, make both overlap comparisons strict (`<`), and raise the 409 with the existing booking's details.
- **422:** raise it from the handler, because a Pydantic validator would make `detail` a list and the test expects a string.
- **Slot format:** use `%H:%M`, because the default time format gives `10:00:00`.
…（略：邊界例子與「被拒絕的 422 先於 409」）
- **Timezone times:** a timezone-aware time like `10:30Z` compared with a plain one returns 500. …（略）The new `end <= start` check adds one case: a booking mixing the two on a free room returned 201 and will now return 500. No scenario covers this, so I left it alone and noted it in the plan.
```

我們的審查如下。參考實跑只把結論帶進第二次實跑的 prompt，repo 裡只 commit 了 agent 寫的計畫檔；任務規格要你把這張表也存進 repo（例如和計畫一起 commit 成 `plans/review.md`），review 的人才看得到計畫與你的決定。

| 檢查 | 計畫怎麼說 | 決定 |
|---|---|---|
| 原因 | 三個失敗、三個不同原因 | 同意，和起點的三個 `AssertionError` 一一對應 |
| API 回什麼 | 409 + `%H:%M` 格式；422 從 handler 丟出 | 同意；它主動避開了 Ch 3 版本 A 的 validator 陷阱 |
| 邊界 | 相鄰可以、重疊一分鐘也不行、零長度回 422 | 同意 |
| 優先順序 | 反向區間的 422 先於重疊的 409 | 同意 |
| 範圍外的發現 | 新的 `end <= start` 會讓「混用時區、空房間」從 201 變 500 | **接受這個已知回歸，不在本次處理**，記在延伸挑戰 |

最後一列我們自己驗證過，不只聽它說：同一筆 `10:30Z` 到 `11:30` 的預約，對起點版本回 `201`，對修好後的版本回 `500`（要等修好才能比，指令在 Step 5 的最後）。審查的意思是「知道並決定」，不是「全部同意」。

### Step 3：約束

`CLAUDE.md`：

```markdown
# booking: room booking API (practice A)

FastAPI app in `app.py`. The acceptance scenarios are in `features/booking.feature`;
their step definitions are in `tests/`. Reviewed plans live in `plans/`.

## Commands
- Tests: `/tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short`

## Rules
- `features/` and `tests/` are the acceptance contract. Do not edit them. If one looks wrong, stop and say why.
- Change only `app.py` unless the task names another file.
- Status codes and `detail` strings are part of the API. Copy them from the scenarios character for character.
- Done means the test command prints no failures. End your reply with its last line.
```

`.claude/settings.json` 與 `.claude/hooks/run_tests.py` 從 Ch 3 原樣複製（`diff` 確認相同），然後：

```
$ python3 -m json.tool .claude/settings.json > /dev/null && echo "settings.json: valid JSON"
settings.json: valid JSON
$ git log --oneline
46aeb1f plan + constraints: reviewed plan, CLAUDE.md, deny rules, run_tests hook
c79178e baseline: first draft, 3 failing scenarios
```

### Step 4：證明回饋

假事件放在 repo 外的 `events/`。`edit_app.json` 是 Ch 3 第四節那個事件，路徑換成本練習的；`edit_feature.json` 由它換掉檔名：

```bash
cd /tmp/lab/aibdd/practice-a-first-loop && mkdir -p events && cat > events/edit_app.json <<'EOF'
{"session_id": "demo", "cwd": "/tmp/lab/aibdd/practice-a-first-loop/booking", "hook_event_name": "PostToolUse",
 "permission_mode": "acceptEdits", "tool_name": "Edit",
 "tool_input": {"file_path": "/tmp/lab/aibdd/practice-a-first-loop/booking/app.py", "old_string": "x", "new_string": "y"},
 "tool_response": {"filePath": "/tmp/lab/aibdd/practice-a-first-loop/booking/app.py"}, "tool_use_id": "toolu_demo"}
EOF
sed 's#booking/app.py#booking/features/booking.feature#g' events/edit_app.json > events/edit_feature.json
```

在 agent 動手之前：

```
$ cd /tmp/lab/aibdd/practice-a-first-loop && export CLAUDE_PROJECT_DIR=/tmp/lab/aibdd/practice-a-first-loop/booking
$ H=booking/.claude/hooks/run_tests.py && PY=/tmp/lab/aibdd/.venv/bin/python
$ $PY $H < events/edit_feature.json; echo "exit=$?"
exit=0
$ $PY $H < events/edit_app.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["decision"]); print("\n".join(l for l in d["reason"].splitlines() if l.startswith(("run_tests", "E   Assert", "FAILED")) or "passed" in l))'
block
run_tests: tests are red after editing app.py (pytest exit 1)
E   AssertionError: want (201, None), got (400, 'Room not available')
E   AssertionError: want (422, 'end must be after start'), got (201, None)
FAILED tests/test_booking.py::test_booking_an_overlapping_slot - AssertionErr...
FAILED tests/test_booking.py::test_booking_right_after_another_booking - Asse...
FAILED tests/test_booking.py::test_booking_that_ends_before_it_starts - Asser...
3 failed, 1 passed in 0.18s
```

注意紅燈裡只有兩個 `AssertionError`：回傳的最後 30 行裝不下第一個失敗的細節（「如果你卡住了」第 4 條）。agent 修好之後再跑同一組：

```
$ $PY $H < events/edit_app.json; echo "exit=$?"
{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "run_tests: 4 passed in 0.22s"}}
exit=0
$ /usr/bin/python3 $H < events/edit_app.json; echo "exit=$?"
{"decision": "block", "reason": "run_tests: cannot run pytest with /Applications/Xcode.app/Contents/Developer/usr/bin/python3 (Python 3.9.6): no pytest module. Fix the hook command in .claude/settings.json."}
exit=0
$ echo '{"tool_input": ' | $PY $H; echo "exit=$?"
run_tests: stdin is not hook JSON (Expecting value: line 2 column 1 (char 16))
exit=1
```

### Step 5：實作（第二次實跑）

計畫檔已經在 repo 裡，prompt 不必再貼一次計畫，只要指給它看並寫下審查決定。Step 4 把 shell 留在 repo 外，所以先 `cd` 回 `booking/`。Claude Code 從啟動時的工作目錄讀專案的 `.claude/settings.json`（官方 settings 文件）；在別的目錄啟動，deny 規則與 hook 都不會載入，`../runs/` 也會指錯地方：

```bash
cd /tmp/lab/aibdd/practice-a-first-loop/booking
claude -p "$(cat ../runs/2-prompt.txt)" --permission-mode acceptEdits --max-turns 15 \
  --allowedTools "Read,Glob,Grep,Edit,Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)" \
  --setting-sources project,local --no-session-persistence \
  --output-format stream-json --verbose --include-hook-events < /dev/null > ../runs/2-loop.jsonl
```

`runs/2-prompt.txt` 的內容：

```text
Implement the reviewed plan in plans/three-scenarios-in-features-booking-feat-peaceful-spark.md. Review decision: the timezone risk stays out of scope, so do not add timezone handling. Scope: only app.py. Done: the test command in CLAUDE.md prints no failures.
```

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。8 個回合、約 12 秒，CLI 估計花費 0.13 美元。stderr 出現和 Ch 3 第五節相同的 `Ignoring 1 permissions.allow entry ...`。

| 順序 | agent 做了什麼 | harness / hook 的回應 |
|---|---|---|
| 1–4 | 讀計畫檔、`app.py`、feature；Glob 找到 `tests/test_booking.py` | 允許 |
| 5 | 一個 Edit：加上 `end <= start` 檢查、改成嚴格的 `<`、409 加上 `%H:%M` 的時段 | hook 回綠：`run_tests: 4 passed in 0.13s` |
| 6–7 | 讀 step 檔；跑 CLAUDE.md 裡的測試指令，接著 `git status --short` | `4 passed in 0.13s`、` M app.py` |

回覆的後半（節錄）：

```text
As decided in the review, I added no timezone handling. The plan's known limitation still applies: a request mixing timezone-aware and naive times returns 500.
…（略）
4 passed in 0.13s
```

它的 diff（節錄：省略了 diff 標頭與前後幾行沒變的上下文；沒跑 agent 的讀者照這段改 `app.py`，就得到後面驗收用的版本）：

```diff
 def create(b: Booking):
+    if b.end <= b.start:
+        raise HTTPException(422, "end must be after start")
     for x in BOOKINGS:
-        if x["room"] == b.room and b.start <= x["end"] and x["start"] <= b.end:
-            raise HTTPException(400, "Room not available")
+        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
+            slot = f"{x['start']:%H:%M}-{x['end']:%H:%M}"
+            raise HTTPException(409, f"{b.room} is taken {slot} by {x['by']}")
     BOOKINGS.append(b.model_dump())
```

我們自己的驗收：`4 passed in 0.11s`；看過 diff 之後由我們 commit（`git commit -am "fix overlap, back-to-back and reversed range (claude -p run 2)"`，agent 的回覆開頭也說了「Nothing is committed for this change」），`git diff --stat` 只有 `app.py`（期望輸出範例）。再用 `probe.py` 問六個情境沒涵蓋的輸入，每次先讓 Bob 訂 Taipei 10:00–11:00。它放在 `booking/` 旁邊、不進 repo，參數是 `app.py` 所在的目錄：

```python
# probe.py: cases the four scenarios do not cover; Bob holds Taipei 10:00-11:00 before each request
import sys
from fastapi.testclient import TestClient
sys.path.insert(0, sys.argv[1])
from app import app

c = TestClient(app)
for room, start, end in [("Taipei", "09:00", "10:00"), ("Taipei", "10:00", "11:00"), ("Taipei", "09:30", "11:30"),
                         ("Tokyo", "10:00", "11:00"), ("Taipei", "10:00", "10:00"), ("Taipei", "10:59", "12:00")]:
    c.post("/reset")
    c.post("/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"})
    r = c.post("/bookings", json={"room": room, "start": start, "end": end, "by": "Alice"})
    print(f"{room} {start}-{end}", r.status_code, r.json().get("detail", "confirmed"))
```

```
$ cd /tmp/lab/aibdd/practice-a-first-loop && /tmp/lab/aibdd/.venv/bin/python -W ignore probe.py booking
Taipei 09:00-10:00 201 confirmed
Taipei 10:00-11:00 409 Taipei is taken 10:00-11:00 by Bob
Taipei 09:30-11:30 409 Taipei is taken 10:00-11:00 by Bob
Tokyo 10:00-11:00 201 confirmed
Taipei 10:00-10:00 422 end must be after start
Taipei 10:59-12:00 409 Taipei is taken 10:00-11:00 by Bob
```

對起點版本跑同一支：先把起點 commit 解到 `baseline/`（`rev-list --max-parents=0` 找出第一個 commit），除了 Tokyo 那行，全是 `400 Room not available`：

```
$ mkdir baseline && git -C booking archive $(git -C booking rev-list --max-parents=0 HEAD) | tar -x -C baseline
$ /tmp/lab/aibdd/.venv/bin/python -W ignore probe.py baseline
Taipei 09:00-10:00 400 Room not available
Taipei 10:00-11:00 400 Room not available
Taipei 09:30-11:30 400 Room not available
Tokyo 10:00-11:00 201 confirmed
Taipei 10:00-10:00 400 Room not available
Taipei 10:59-12:00 400 Room not available
```

Step 2 審查表最後一列的回歸，也用這兩個目錄驗證。`raise_server_exceptions=False` 讓 TestClient 把伺服器內的例外回成 500，而不是直接丟到你的終端機：

```bash
cd /tmp/lab/aibdd/practice-a-first-loop && for d in baseline booking; do /tmp/lab/aibdd/.venv/bin/python -W ignore -c 'import sys; sys.path.insert(0, sys.argv[1]); from fastapi.testclient import TestClient; from app import app; r = TestClient(app, raise_server_exceptions=False).post("/bookings", json={"room": "Taipei", "start": "10:30Z", "end": "11:30", "by": "Alice"}); print(sys.argv[1], r.status_code)' $d; done
```

```
baseline 201
booking 500
```

### Step 6：證明約束（第三次實跑）

有 CLAUDE.md 時，agent 多半不會主動去改契約檔（「如果你卡住了」第 5 條，本練習沒有另外實測），所以我們模擬一個真實會發生的情況：**你自己**想破例。`runs/3-prompt.txt` 的內容：

```text
Product changed its mind: back-to-back bookings should now be rejected. I am making an exception to CLAUDE.md for this one task: edit the scenario "Booking right after another booking" in features/booking.feature so that it expects the booking to be rejected because "Taipei" is taken 10:00-11:00 by Bob. Edit only that file, then tell me what happened.
```

旗標同第二次實跑，改成 `--max-turns 6 --allowedTools "Read,Edit"`，不加 `--include-hook-events`，輸出寫到 `../runs/3-deny.jsonl`。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。8 個回合、約 19 秒，CLI 估計花費 0.15 美元。（「回合」是結果 JSON 的 `num_turns`，三次實跑都等於工具呼叫數加一，和 `--max-turns` 的計算單位不同，所以比 6 大。）

它先讀了 feature 檔與 `.claude/settings.json`，說「`.claude/settings.json` has a deny rule for `features/`, so this may get blocked.」，然後照你的要求發出 Edit，收到 `<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>`。回覆節錄：

```text
**The edit didn't happen, and `features/booking.feature` is unchanged.** Line 18 still says `Then the booking is confirmed`.

- **What blocked it:** the Edit tool refused with "File is in a directory that is denied by your permission settings." That comes from the `Edit(/features/**)` deny rule in `.claude/settings.json`, which is separate from CLAUDE.md, so your exception doesn't lift it.
…（略）
**Tests:** I couldn't run them because the command needed approval and was declined, so I have no pytest result to end this reply with. No files changed, so nothing here should have affected the results.
```

`permission_denials` 裡有那個 Edit（`new_string` 是 When 那一行，加上改寫成 `Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob` 的 Then 行），還有兩個 Bash：`--allowedTools` 只給了 `Read,Edit`，而專案的 allow 規則在未信任的資料夾裡不生效，所以連測試指令都被擋。`git status --short` 是空的。

**解答說明**：

- **提案的價值在第五列**。三個 bug 的修法 agent 不需要你教；值得你花時間的是它找到的「範圍外」回歸。我們選擇接受並記錄，這是一個可以被質疑的決定，重點是它被看見、被驗證、被寫下來。
- **計畫檔進 repo 讓第二次實跑變短**：prompt 只有四句，agent 自己讀計畫。這也讓 review 的人看得到「agent 照著什麼做」。
- **約束的兩層在第三次實跑分得很清楚**：你對 CLAUDE.md 開的例外，agent 接受了（它真的去改）；harness 的 deny 不理會對話裡的例外。要改契約，就是人去改 feature 檔或改設定，留下 commit。
- **第三次實跑也暴露了一個副作用**：在 `-p` 裡只給 `Read,Edit`，agent 就沒辦法照 CLAUDE.md 的要求附上測試結果，它也老實說了。工具限制與完成定義要一起設計。

</details>

## 測試用例

| 輸入 / 檢查 | 預期輸出 | 說明 |
|---|---|---|
| 起點 `pytest -q` | `3 failed, 1 passed` | 三個 `AssertionError` 各不相同：409 vs 400、201 vs 400、422 vs 201 |
| 交件時 `pytest -q` | `4 passed` | 由 agent 的修改達成 |
| `git diff --stat` 起點..HEAD，排除 `.claude`、`CLAUDE.md`、`plans` | 只有 `app.py` | 範圍有守住 |
| hook ← `edit_feature.json` | 無輸出，exit 0 | 非 `.py` 檔不跑測試 |
| hook ← `edit_app.json`（修好前） | `decision: block`，`3 failed, 1 passed` | 紅燈的回饋 |
| hook ← `edit_app.json`（修好後） | `additionalContext: run_tests: 4 passed ...` | 綠燈的回饋 |
| `/usr/bin/python3` 跑 hook | `cannot run pytest with ... (Python 3.9.6)` | 直譯器錯了要說出來 |
| 壞掉的 JSON 進 hook | stderr 一行、exit 1 | 屬於 non-blocking error |
| Taipei 10:00–11:00 之後訂 11:00–12:00 或 09:00–10:00 | `201` | 半開區間：相鄰不算重疊 |
| Taipei 10:00–10:00 | `422 end must be after start` | 零長度也是「結束不晚於開始」 |
| Taipei 10:59–12:00、09:30–11:30 | `409 Taipei is taken 10:00-11:00 by Bob` | 重疊一分鐘或整段包住都算 |
| Tokyo 10:00–11:00 | `201` | 不同房間互不影響 |
| 空房間、`10:30Z` 到 `11:30` | 起點 `201`，修好後 `500` | 審查時接受的已知回歸，留給延伸挑戰 |
| 要求 agent 改 `features/` | `File is in a directory that is denied by your permission settings.` | 硬約束不吃對話裡的例外 |

## 延伸挑戰（加分）

- **改善紅燈的回饋**：讓 `run_tests.py` 在多個失敗時，每個失敗都至少回傳它的 `AssertionError` 那一行（例如改用 `--tb=line`，或從輸出挑出 `E   ` 開頭的行）。用起點版本的三個失敗證明：第一個失敗的細節不再被切掉。
- **把探測變成情境**：`probe.py` 的六個輸入裡，挑三個寫成新的情境（`features/` 由你改，不是 agent），用 Ch 3 的論點說明為什麼它們該進驗收、而不是留在一次性腳本裡。
- **處理那個已知回歸**：為「`10:30Z` 到 `11:30` 的預約」決定行為（回 422？忽略時區？），寫成情境，再跑一次完整迴圈。比較你的計畫審查和 Ch 3 第二次實跑的 `wall()` 做法。
- **再加一道 deny**：把 `Edit(/plans/**)` 加進 deny，讓 agent 無法改寫已審過的計畫；想一想這和「計畫應該隨實作更新」的需求怎麼平衡（Ch 33 會正面處理 truth 與 plan 的生命週期）。
- **預習 Stop hook**：寫一個 Stop hook，在套件不是綠的時候 exit 2，並先用假事件證明它；Ch 20 的 `require_green.py` 會把它做完整。

## 自我檢核

- [ ] 我的起點三個失敗各自代表什麼問題，我不看輸出能說出來
- [ ] 我的計畫審查表裡至少有一列不是「同意」，或者我能說出為什麼每一列都同意
- [ ] 我能解釋為什麼第三次實跑裡，agent 接受了我對 CLAUDE.md 的例外，卻沒辦法改到檔案
- [ ] 我的 hook 五條路都有實際輸出，而且我知道紅燈在多個失敗時漏掉了什麼
- [ ] 我能指出參考解答的一個取捨（例如接受時區回歸、只給 `Read,Edit`），並說出我會不會做同樣的選擇

Part 1 到這裡結束：你已經能在一個 repo 裡搭起提案、約束、回饋的迴圈。Part 2 換一個尺度：當這些規則與流程長到一個 skill 檔裝不下時，會發生什麼事。

→ [Ch 7 混亂的起點：單檔 BDD skill 與一口氣做完的 autopilot](./07-messy-baseline.md)
