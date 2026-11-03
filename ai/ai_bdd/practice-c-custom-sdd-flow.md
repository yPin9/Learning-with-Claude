# 練習 C — 用自訂 SDD 流程做一個全端小功能

> **目標**：把 Ch 12–18 做出的 skill 與檢查串成一條自己的 SDD 流程，替會議室預約加上「查詢某會議室某天的空檔」，後端計算、HTTP API、網頁三層都要改。規劃階段的每份產物都要過它那一章的檢查（共七道）；實作階段讓 agent 一次只做一個使用者故事，每個故事結束時用 `checkpoint.py` 跨層驗收：同一份情境走 API 與瀏覽器兩個 driver、頁面的 `fetch` 對得上後端路由、截圖留證。最後把同一份 tasks.md 交給原版 `/speckit-implement`，對照兩條路差在哪裡。

> **環境**：Spec Kit 1.2.5、Claude Code 2.1.295（三次 `claude -p`，2026-11-02，模型 claude-opus-5-5）、[Ch 0](./00-environment-setup.md) 的課程 venv（Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、Playwright 1.64.1 與 Chromium）、macOS 26.6 arm64。lab 在 `/tmp/lab/aibdd/practice-c-sdd-flow/`，從空目錄開始。參考解答的規劃產物是我們照各 skill 的模板與完成條件手寫的（那些 skill 的 agent 行為已在各章實測過）；實作與對照是三次真實的 `claude -p`；所有檢查輸出都是實際跑出來的。

## 背景與動機

會議室預約已經上線一段時間，它寫在團隊採用 SDD 之前：一個 FastAPI app、一頁表單、兩個情境，沒有 spec 資料夾。PM 帶來下一個需求：「讓大家看得到某間會議室某天還有哪些空檔，不必一個時段一個時段去試。」這是典型的全端小功能：一個算空檔的函式、一個新端點、頁面上的按鈕與清單，三層要對得上。

repo 已經用三種方式走過「從 spec 到實作」：[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) 的「Step 5：implement + 人工檢查」跑完 `/speckit.implement` 再逐條手驗 Given-When-Then；[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) 的「Step 3：接 coding agent」從 Python 流水線一個 task 呼叫一次 claude CLI；[SDD Final](../spec_driven_development/final-project-ship-with-sdd.md) 的「階段 4：實作 pipeline」讓你在 Spec Kit、Kiro、自建流水線三選一。三者有同一個空白：實作階段沒有跨層的關卡。後端測試綠了，頁面可能讀錯欄位；頁面呼叫的網址，後端可能根本沒有；一口氣做完所有故事，人只能在最後面對一大包 diff。Part 3 已經把規劃換成自己的 skill（Ch 14–18），這個練習把它們接起來，再補上實作那一半：一次一個故事，每個故事用一支腳本跨三層驗收，驗完停下來讓人審。

```
規劃：每份產物一道閘門
  sdd-specify ─▶ clarify-interview ─▶ technical-research（research_check）─┐  plan-feature 合併      tasks-binder
  spec_check     clarify_check         system-analysis（inventory check）  ├▶ plan、data-model、 ─▶ tasks.md
                                       contract-writer（contract_check）  ─┘  quickstart：plan_lint  task_coverage
                                       checkpoint.py --entry：上面七道閘門一起重跑
實作：每個故事一輪，輪與輪之間人審
  sdd-implement US1 ─▶ 測試先紅 ─▶ 實作 ─▶ checkpoint.py US1 ─▶ 停 ─▶ 人審、commit ─▶ sdd-implement US2 …
                                           api │ ui＋截圖 │ suite │ routes │ tasks
```

這和工地的分段查驗一樣：每層樓灌漿前，監造要先看過鋼筋，查驗沒過，下一層不准開工。類比有一處不成立：工地的查驗員和施工的是不同的人，我們的 checkpoint 雖然是腳本，按下它的卻是正在施工的 agent，所以參考解答會看到「誰跑了檢查」本身也要被檢查。

## 任務規格

功能是 `specs/001-free-slots`。這個領域只有「今天」（constitution IV：「Same-day bookings only」），「某天」要由 clarify 步驟從 repo 找到答案，而不是去問人。

| 交付物 | 產生它的 skill | 驗收：指令印出的最後一行 |
|---|---|---|
| `spec.md`，Gherkin 情境帶 `@sc_free_NN` | `/sdd-specify`（[Ch 17](./17-constitution-and-presets.md)） | `spec_check.py`：`1 file, 0 problems` |
| spec 的 `## Clarifications`，至少一條 repo 答案 | `/clarify-interview answers=…`（[Ch 18](./18-clarify-skill.md)） | `clarify_check.py`：`1 files, 0 problems` |
| `research.md` 與 spike、`inventory.md`（brownfield） | `/technical-research`、`/system-analysis`（[Ch 15](./15-research-and-analysis-skills.md)） | `research_check.py --plan` 與 `inventory.py check --app … --html …` 都 `0 problems` |
| `contracts/interfaces.md` | `/contract-writer`（[Ch 14](./14-artifact-first.md)） | `contract_check.py`：`1 files, 0 problems` |
| `plan.md`、`data-model.md`、`quickstart.md` | `/plan-feature`（Ch 15）與你 | `plan_lint.py`（[Ch 13](./13-plan-anatomy-review.md)）：`1 plan, 0 problems` |
| `features/free_slots.feature`、`tasks.md` | 從 spec 抽出；`/tasks-binder`（[Ch 16](./16-tasks-binder.md)） | `task_coverage.py check`：`… 0 problems`；每個故事 phase 有可執行的 `**Checkpoint**` |
| `sdd-implement`、`verify-booking-ui` 與 `checkpoint.py` | 你寫 | skill-lint `0 problems`；`checkpoint.py --entry`：`entry: 7 gates, 0 failed` |
| 實作 | agent，一次一個故事 | 每個故事 `US<n>: 5 checks, 0 failed`，`evidence/US<n>/` 有截圖 |
| 對照 | 原版 `/speckit-implement`，同一個 commit、同一組權限 | 用同一支 checkpoint 驗它的結果，寫下差異 |

規則：不准為了過檢查改 `features/`、spec 的情境或預期值；agent 的權限只放行讀檔、寫 `app/`、`tests/`、`pytest.ini` 與 tasks.md、跑 pytest、checkpoint 與原版第一步要用的 `check-prerequisites.sh`；每次 `claude -p` 都帶 `--max-turns`、`--tools`、`--setting-sources project`，stdin 接 `/dev/null`。

**起點。** 在 `/tmp/lab/aibdd/practice-c-sdd-flow/` 放 `setup.sh` 與 `conftest.py`，再執行 `sh setup.sh`。它 clone Ch 17 的專案（Spec Kit 1.2.5、preset、constitution 1.1.0、`sdd-specify`），補上 Ch 13–16 與 Ch 18 的 skill 和腳本，再把 Ch 0 冒煙測試的 app 拆成 `app/main.py` 與 `app/static/index.html`，因為 Ch 15 的 `inventory.py` 只掃 `.html` 和 `.js`。`docs/rules.md` 是一份 truth 檔，替 constitution II 點名卻沒定義的 R1–R4 下定義（Ch 17 踩雷 2）。來源路徑就是各章的 lab；clone 只帶得走已 commit 的檔，Ch 17 的 `sdd-specify` 要先 commit。我們的實驗機上，Ch 15、Ch 17 的 lab 留著兩章修正前的 `fresh.sh`、`inventory.py`、`constitution_params.py`；本練習每個檢查與測試的輸出都用新舊兩版各跑過一次，結果相同（三次 `claude -p` 跑在正文版上）。

```sh
#!/bin/sh
# setup.sh: practice C's starting project in ./proj, assembled from the labs of Ch 0 and Ch 13-18
set -e
L=/tmp/lab/aibdd; S=.claude/skills
git clone -q $L/ch17-presets/booking proj && cd proj   # Ch 17: Spec Kit 1.2.5, preset booking-bdd, constitution 1.1.0, sdd-specify
cp $L/ch13-plan/plan_lint.py tools/
cp -R $L/ch15-planners/project/$S/technical-research $L/ch15-planners/project/$S/system-analysis $L/ch15-planners/project/$S/plan-feature \
  $L/ch16-tasks-binder/project/$S/tasks-binder $L/ch18-clarify/proj/$S/clarify-interview $L/ch14-artifact-first/contract-writer $S/
ln -s $L/.venv .venv                                    # commands say .venv/bin/python: no PATH surprises
mkdir -p app/static tests features docs                 # Ch 0's smoke app, split so inventory.py can scan the page
C=$L/ch00-setup/smoke
python3 - "$C/app.py" <<'PY'
import sys
src = open(sys.argv[1]).read().split("\n", 1)[1]           # drop the '# smoke/app.py' line
head, page = src.split('@app.get("/", response_class=HTMLResponse)\ndef index():\n    return """')
open("app/static/index.html", "w").write(page.rsplit('"""', 1)[0] + "\n")
open("app/__init__.py", "w").write("")
open("app/main.py", "w").write("# app/main.py: room booking API (Ch 0 smoke) + the page in app/static/\nfrom pathlib import Path\n" + head +
    '@app.get("/", response_class=HTMLResponse)\ndef index():\n    return (Path(__file__).parent / "static" / "index.html").read_text()\n')
PY
for f in tests/drivers.py tests/test_booking.py; do tail -n +2 $C/$f > $f; done   # drop the "# smoke/..." line
tail -n +2 $C/features/booking.feature | awk '/Scenario:/ { printf "    @sc_book_%02d\n", ++n } { print }' > features/booking.feature
cp ../conftest.py tests/conftest.py                     # Ch 0's conftest + EVIDENCE_DIR screenshots (below)
printf '[pytest]\npythonpath = . tests\ntestpaths = tests\nmarkers =\n    sc_book_01\n    sc_book_02\n' > pytest.ini
printf '# Business rules (truth file; constitution II names them)\n- R1 A room cannot be booked twice for overlapping times; ranges are half-open [start, end).\n- R2 End must be after start.\n- R3 Office hours are 08:00-20:00.\n- R4 Only the person who booked can cancel.\n' > docs/rules.md
printf '.venv\n__pycache__/\n.pytest_cache/\nevidence/\n' > .gitignore
git add -A && git commit -qm "practice C start: custom SDD skills + booking app as built" && git log --oneline | head -1
```

`conftest.py` 是 Ch 0 那份加兩處：從 `app.main` 匯入；設了 `EVIDENCE_DIR` 時，每個 UI 情境結束前截一張整頁圖。這是後面驗證 skill 的證據來源：

```python
# tests/conftest.py: Ch 0's harness. One uvicorn thread on a free port, --driver api|ui,
# and with EVIDENCE_DIR set, a screenshot of the page at the end of every UI scenario.
import os, pathlib, socket, threading, time
import pytest, uvicorn
from playwright.sync_api import sync_playwright
from app.main import app
from drivers import ApiDriver, UiDriver

@pytest.fixture(scope="session")
def base_url():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started: time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def pytest_addoption(parser):
    parser.addoption("--driver", choices=["api", "ui"], default="api")

@pytest.fixture
def driver(request, base_url):
    if request.config.getoption("--driver") == "api":
        d = ApiDriver(base_url); d.reset(); yield d; return
    page = request.getfixturevalue("browser").new_page()
    d = UiDriver(base_url, page); d.reset(); yield d
    if os.environ.get("EVIDENCE_DIR"):
        out = pathlib.Path(os.environ["EVIDENCE_DIR"]); out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=out / f"{request.node.name}.png", full_page=True)
    page.close()
```

```
$ cd proj && for d in api ui; do .venv/bin/python -m pytest -q --driver $d | tail -1; done
2 passed in 0.08s
2 passed in 0.90s
$ node /tmp/lab/tools/skill-lint.mjs .claude/skills | tail -1
17 skills, 0 problems
```

## 期望輸出範例

規劃全部完成、還沒寫任何程式時：

```
$ .venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py specs/001-free-slots --entry
ok    spec      1 file, 0 problems
ok    clarify   1 files, 0 problems
ok    research  2 decisions, 2 questions, 0 problems
ok    inventory 4 FRs, 4 rows, 0 problems
ok    contract  1 files, 0 problems
ok    plan      1 plan, 0 problems
ok    tasks     10 tasks, 13 sources, 0 problems
entry: 7 gates, 0 failed
```

第一個故事做完、審過時：

```
$ .venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py specs/001-free-slots US1
ok    api       4 passed, 10 deselected in 0.10s
ok    ui        4 passed, 10 deselected in 1.05s; evidence/US1: 4 screenshots
ok    suite     13 passed, 1 deselected in 0.11s | 13 passed, 1 deselected in 1.25s
ok    routes    5 routes, 2 calls, 0 problems, 0 warnings
ok    tasks     T003..T007 [X]; 10 tasks, 13 sources, 0 problems
US1: 5 checks, 0 failed
```

邊界：還沒實作就跑 `checkpoint.py … US1`（前六行），以及 `checkpoint.py … US3`，問一個 tasks.md 裡沒有的故事（最後兩行，exit 2）：

```
FAIL  api       no test carries sc_free_01 or sc_free_02 or sc_free_03 or sc_free_05: 2 deselected in 0.00s
FAIL  ui        no test carries sc_free_01 or sc_free_02 or sc_free_03 or sc_free_05: 2 deselected in 0.00s
ok    suite     2 passed in 0.08s | 2 passed in 0.60s
ok    routes    4 routes, 1 call, 0 problems, 0 warnings
FAIL  tasks     not [X]: T003 T004 T005 T006 T007; 10 tasks, 13 sources, 0 problems
US1: 5 checks, 3 failed
usage: checkpoint.py [-h] [--entry] feature_dir [story]
checkpoint.py: error: no '## Phase N: User Story 3' with @sc_ citations in specs/001-free-slots/tasks.md
```

## 如果你卡住了

1. **`setup-plan.sh` 印出 `Error: PyYAML is required to resolve preset template composition`**：裝了 preset 之後，bash 的模板解析要 PyYAML，系統的 `python3`（3.9.6）沒有。先 `source /tmp/lab/aibdd/.venv/bin/activate`（Ch 17 踩雷 4）。
2. **`StepDefinitionNotFoundError: Step definition is not found: Given "…"`**：pytest-bdd 9.0.1 的 step 只在定義它的測試模組與 `conftest.py` 可見（它把每個 step 註冊成定義處那個模組裡的 pytest fixture，`steps.py` 的 `caller_locals`，可見範圍照 fixture 的規則走）。free_slots.feature 要用的預約 step 若還留在 `test_booking.py` 就找不到，搬進 `tests/conftest.py`。我們用兩個測試檔的小例子實測：搬之前 `1 failed, 1 passed`，搬之後 `2 passed`。
3. **agent 回報「checkpoint 被權限擋下」**：先看被擋的是不是它自己在指令上加的東西（`; echo "exit=$?"`、以 `EVIDENCE_DIR=…` 開頭），或規則外的指令（`sed -i`）；它們都對不上 `Bash(.venv/bin/python …)` 這種規則（Ch 14、Ch 18 也遇過；[權限文件](https://code.claude.com/docs/en/permissions)的 Wrappers 一節寫明，allow 規則不會越過一般環境變數的賦值）。在 SKILL.md 寫明「照原樣、一次一個指令」，比放寬權限好。
4. **US1 的 checkpoint 永遠有一項紅**：看紅的是不是後面故事的情境。US2 的情境在 API 層做完 US1 就綠、在 UI 層要等 US2；suite 若跑全部，US1 永遠過不了。參考解答的第一版 checkpoint 就錯在這裡，是 agent 找到的。
5. **原版 `/speckit-implement` 第一步就 `ERROR: Feature directory not found`**：`.specify/feature.json` 被 `.specify/.gitignore` 排除（Ch 12），新 clone 裡沒有它。重建 `feature.json`（`{"feature_directory":"specs/001-free-slots"}`），或設 `SPECIFY_FEATURE_DIRECTORY` 指給它，兩種我們在新 clone 實測都 exit 0；只是 agent 自己在指令前加這個變數時，會撞上第 3 條的權限規則。
6. **UI 測試一紅就等 30 秒**：`wait_for` 與 `click` 預設逾時 30000 ms。紅燈時先看 `routes` 那一行：頁面打錯網址時，靜態掃描 0.12 秒內就指出是哪一行，UI 層要等滿逾時（參考解答第五節實測 91 秒）。

## 實作步驟建議

### Step 1：建起點，記下現況

跑 `setup.sh`，確認兩個 driver 都 `2 passed`，再跑 `inventory.py scan --app app.main:app --html app/static`，記下現況（`4 routes, 1 call, 0 problems, 0 warnings`）。brownfield 的盤點從這裡開始。

### Step 2：specify 與 clarify，把標記清空

`/sdd-specify` 寫 spec，constitution 1.1.0 讓它最多留 2 個標記。PM 的答案寫成 `answers.md`，用 `/clarify-interview answers=answers.md specs/001-free-slots/spec.md` 重播（`claude -p` 裡沒有 AskUserQuestion，Ch 18 實測 B）。「哪一天」應該由 constitution 回答，不佔題數；新增的 FR 與情境拿下一個沒用過的號碼，不重編。

### Step 3：三個 planner，合併成過得了 plan_lint 的 plan

`setup-plan.sh` 複製模板後，在 Technical Context 寫下真正的技術未知數（`NEEDS CLARIFICATION [DEC-01] …`），先跑一次 `plan_lint.py` 當待辦清單。`/technical-research` 用 spike 回答，`/system-analysis … brownfield` 先掃再盤點，`/contract-writer` 寫契約；三份產物 commit 之後，`fresh.sh` 才判斷得出它們比 spec 新。`/plan-feature` 合併，data-model 與 quickstart 由你寫（Ch 15 沒拆它們），跑到 `1 plan, 0 problems`。

### Step 4：feature 檔、tasks 與入口閘門

把 spec 的 gherkin 區塊放進一個 `Feature:`，`/tasks-binder specs/001-free-slots features/free_slots.feature`，每個故事 phase 結尾的 `**Checkpoint**` 寫成可執行的指令。寫 `checkpoint.py` 與兩個 skill，`--entry` 印出 `entry: 7 gates, 0 failed` 之後 commit、打 tag。

### Step 5：一次一個故事，再對照與破壞

`claude -p "/sdd-implement specs/001-free-slots US1"`；跑完你自己再跑一次 checkpoint、看截圖、讀 diff，報告裡「來源沒決定的事」逐條決定，然後 commit，US2 照做。最後從 tag clone 一份（刪掉 remote），用同一組權限跑原版 `/speckit-implement`，再用你的 checkpoint 驗它；在三個拋棄式副本各弄壞一層，看哪一行先紅、花多久。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### 一、規劃：每份產物與它的檢查輸出

spec 草稿過得了形狀檢查，過不了 clarify 的閘門：兩個標記要在 plan 之前處理（以下在草稿的 commit 上重跑）。

```
$ .venv/bin/python tools/constitution_params.py get sdd-specify | .venv/bin/python .claude/skills/sdd-specify/scripts/spec_check.py specs/001-free-slots/spec.md --params -
1 file, 0 problems
$ .venv/bin/python .claude/skills/clarify-interview/scripts/clarify_check.py specs/001-free-slots/spec.md
specs/001-free-slots/spec.md:70: CL-1 open marker: what does a person see when they ask for a room the system does not know, such as "Osaka"?
specs/001-free-slots/spec.md:76: CL-1 open marker: is a gap too short for a meeting, such as 11:55-12:00, still a free slot?
1 files, 2 problems
```

`answers.md` 是 PM 的三句話：未知會議室要拒絕並說出有哪些房間；再短的空檔都算；預約成功後顯示剛訂的那間的空檔。照 clarify-interview 的第 5 步寫回之後，`## Clarifications` 有四條，第一條由 repo 回答、不佔題數；FR 也跟著改：

```markdown
- Q: Which day do free slots cover? → A: Today only, because bookings are same-day → Assumptions (repo: .specify/memory/constitution.md:16)
- Q: What does a person see when they ask for a room the system does not know, such as "Osaka"? → A: A rejection that names the rooms: "room Osaka not found; choose Taipei, Tokyo or Seoul" → FR-004, US1
- Q: Is a gap too short for a meeting, such as 11:55-12:00, still a free slot? → A: Yes, every gap is a free slot, however short; people decide what is long enough → FR-001
- Q: After a booking is confirmed, which room's free slots does the page show? → A: The free slots of the room that was just booked → FR-003

- **FR-001**: System MUST list a room's free slots for today inside office hours 08:00-20:00 (R3), earliest first, as half-open ranges that no booking of the room overlaps (R1). Every gap counts, however short.
- **FR-002**: Bookings of other rooms MUST NOT change a room's free slots.
- **FR-003**: The booking page MUST show the free slots of the room typed in Room when the person presses "Free slots", and after a booking is confirmed show the free slots of the booked room.
- **FR-004**: System MUST reject a request for the free slots of a room it does not know with "room Osaka not found; choose Taipei, Tokyo or Seoul".
```

FR-004 與 `@sc_free_05` 是 clarify 加的。US2 的情境在草稿裡已經拿走 04，ID 不重編，所以 US1 的情境編號跳號。spec 的兩個 gherkin 區塊用一段 Python 接成 `features/free_slots.feature`（以正規式抓出每個 gherkin 圍欄區塊的內容，每行縮兩格，前面加 `Feature: Free slots of a room`），gherkin-official 解析出 4 個 Rule、5 個情境。節錄三個，省略空行：

```gherkin
  Rule: R1 bookings are half-open, so back-to-back bookings leave no gap
    @sc_free_02
    Scenario: Back-to-back bookings leave no gap between them
      Given Bob has booked "Taipei" from 10:00 to 11:00
      And Alice has booked "Taipei" from 11:00 to 12:00
      When Carol asks for the free slots of "Taipei"
      Then the free slots of "Taipei" are 08:00-10:00, 12:00-20:00
  Rule: Only rooms the system knows have free slots
    @sc_free_05
    Scenario: Asking for a room that does not exist
      When Carol asks for the free slots of "Osaka"
      Then the request is rejected because room Osaka not found; choose Taipei, Tokyo or Seoul
  Rule: R1 a confirmed booking removes its range from the free slots
    @sc_free_04
    Scenario: A new booking splits a free slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 13:00 to 14:00
      Then the free slots of "Taipei" are 08:00-10:00, 11:00-13:00, 14:00-20:00
```

另外兩個是 `@sc_free_01`（空房間整天 `08:00-20:00`，放在 `Rule: R3 free slots lie inside office hours 08:00-20:00` 底下）與 `@sc_free_03`（Tokyo 的預約不影響 Taipei）。plan 一路被 `plan_lint.py` 追著跑：`setup-plan.sh` 剛複製的模板是 `1 plan, 49 problems`；填好 Technical Context、寫下兩題 `NEEDS CLARIFICATION [DEC-NN]` 的草稿是 `1 plan, 15 problems`（PL-01 四個：research、data-model、quickstart、contracts 都還沒有；PL-03 兩個；PL-05 一個：FR-002 沒人處理；PL-06 四個：After design 那欄還是 pending；PL-07、PL-08 各兩個）。DEC-01 問「API 回空檔時送什麼，頁面拿到的是不是 `08:00`」，答案來自一支 spike（`spikes/time_format_spike.py`：同一個 FastAPI app 各回一次 `datetime.time` 與 `%H:%M` 字串，用 TestClient 讀回原文）。下面是它的 stdout，存成 `spikes/time_format_spike.out`；stderr 另有一則 Starlette 對 TestClient 的 httpx 棄用警告（[Ch 2](./02-vibe-coding-vs-engineering.md) 說過它的來由）：

```
fastapi 0.162.0
/as-time [{"start":"08:00:00","end":"10:00:00"}]
/as-text [{"start":"08:00","end":"10:00"}]
```

```markdown
## DEC-01 Time format of a free slot

- **Question**: what does the API send for a free slot's start and end, and does the page get "08:00"?
- **Decision**: send each free slot as `{"start": "HH:MM", "end": "HH:MM"}`, formatted with `%H:%M` on the server.
- **Rationale**: FastAPI sends a `datetime.time` as ISO text with seconds, "08:00:00" (spike); the scenarios (FR-001) and the page show "08:00-10:00", and Principle IV fixes HH:MM.
- **Alternatives considered**:
  - return `datetime.time` and cut the seconds in the page — every client must know to cut them; the API and the page would disagree on the format
  - minutes since midnight (480) — exact, but neither the page nor a person reads it without converting
- **Risks**: POST /bookings still answers "10:00:00" (it returns the model); a client that mixes both endpoints sees two formats. Noticed by any test that compares a booking with a free slot.
- **Evidence**: spikes/time_format_spike.py ; spikes/time_format_spike.out ; https://fastapi.tiangolo.com/tutorial/encoder/
- **Verdict**: feasible
```

DEC-02 問頁面怎麼知道預約之後空檔變了，選「201 之後再 GET 一次」；落選的是在頁面裡自己減掉新預約（R1、R3 會在 Python 與 JavaScript 各寫一份）、讓 POST /bookings 順便回空檔（改動一個 `existing` 端點）、輪詢。兩筆過 `research_check.py`：`2 decisions, 2 questions, 0 problems`。盤點（`inventory.py check … --app app.main:app --html app/static` 得 `4 FRs, 4 rows, 0 problems`）：

```markdown
| ID | Status | Caller | Auth | Request | Responses | Owner | Source |
|---|---|---|---|---|---|---|---|
| EP:GET /rooms/{room}/free-slots | new | page: Free slots button, and the page after a confirmed booking | none | path `room` | 200 list of {start, end} as HH:MM, earliest first; 404 unknown room | backend | FR-001, FR-002, FR-004 |
| EP:GET / | change | browser | none | - | 200 the page, now with a Free slots button and list | web | FR-003 |
| EP:POST /bookings | existing | page: Book form | none; the name is typed | {room, start, end, by} | 201 booking; 409 R1; 422 R2 | backend | FR-003 (the page refreshes after its 201) |
| EP:POST /reset | existing | test suite | test-only | - | 200 | backend | internal: isolates scenarios |
```

契約照 Ch 14 的形狀寫兩列：`GET /rooms/{room}/free-slots` 的 200 與 404（原文 `room Osaka not found; choose Taipei, Tokyo or Seoul`），以及頁面那列的可存取名稱：textbox `Room`、button `Free slots`、list `Free slots of <room>`，`contract_check.py` 得 `1 files, 0 problems`。合併後兩題變成 `… formatted on the server [DEC-01]` 與 `after a 201 the page asks GET /rooms/{room}/free-slots again for the booked room [DEC-02]`；`## Conflicts` 只有一條：DEC-01 說 HH:MM，盤點裡 `existing` 的 POST /bookings 卻回 `"10:00:00"`，決議這次不改、記在 DEC-01 的 Risks。補上 data-model（Booking、Free slot 兩節）與 quickstart（Q1–Q4 四條驗證指令）後，plan_lint 是 `1 plan, 0 problems`。`task_coverage.py sources` 列出 `13 sources to cite (4 FRs, 2 DECs, 2 EPs, 5 scenarios), 2 optional, 2 user stories`；tasks.md 的兩個故事 phase（節錄，省略 Goal、Independent Test 與空行）：

```markdown
## Phase 3: User Story 1 - See when a room is free (Priority: P1) 🎯 MVP
- [ ] T003 [US1] [@sc_free_01] [@sc_free_02] [@sc_free_03] [@sc_free_05] Write the steps "<who> asks for the free slots of", "the free slots of ... are" and "the request is rejected because" in tests/test_free_slots.py, with the free-slot methods of ApiDriver and UiDriver in tests/drivers.py; run them and see them fail
- [ ] T004 [US1] [FR-001] [FR-002] Write unit tests for free_slots() in tests/test_slots.py: empty day, back-to-back, another room, a booking from 07:00 to 09:00, one from 19:30 to 21:00, a 5-minute gap
- [ ] T005 [US1] [FR-001] [FR-002] Implement free_slots(bookings, room) in app/slots.py: gaps inside 08:00-20:00 (R3), half-open (R1), earliest first, every gap however short; no FastAPI import
- [ ] T006 [US1] [FR-004] [DEC-01] [EP:GET /rooms/{room}/free-slots] Serve GET /rooms/{room}/free-slots in app/main.py: 200 `[{"start": "08:00", "end": "10:00"}]` with HH:MM text; an unknown room answers 404 "room Osaka not found; choose Taipei, Tokyo or Seoul"
- [ ] T007 [US1] [FR-003] [EP:GET /] Add a button `Free slots` and a list named "Free slots of <room>" to app/static/index.html; a rejected request shows "Rejected: <detail>" in role=status
**Checkpoint**: `.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py specs/001-free-slots US1` ends with `US1: 5 checks, 0 failed`.
## Phase 4: User Story 2 - Free slots follow a new booking (Priority: P2)
- [ ] T008 [US2] [@sc_free_04] Run @sc_free_04 under both drivers before changing anything; it uses steps from T003, so expect --driver api to pass and --driver ui to fail because the list does not change
- [ ] T009 [US2] [FR-003] [DEC-02] After a 201 from POST /bookings, fetch GET /rooms/{room}/free-slots for the booked room and redraw the list in app/static/index.html
**Checkpoint**: `.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py specs/001-free-slots US2` ends with `US2: 5 checks, 0 failed`.
```

其餘三個 task 是 T001 註冊 marker、T002 綁定 feature 檔、T010 跑 quickstart；`check` 得 `10 tasks, 13 sources, 0 problems`。T008 是「先看它紅」的測試 task：US2 的情境沿用 T003 的 step，API 層在 US1 之後就會綠，只有 UI 層是紅的。

### 二、checkpoint.py 與兩個新 skill

`.claude/skills/sdd-implement/scripts/checkpoint.py`（stdlib only，89 行）：

```python
#!/usr/bin/env python3
"""checkpoint.py: the gates of the custom SDD flow, run from the project root with .venv/bin/python.
  FEATURE_DIR --entry  the 7 planning gates must pass before any story is implemented
  FEATURE_DIR US<n>    after story n: its scenarios under both drivers (UI screenshots in evidence/US<n>/), the suite
                       under both drivers minus later stories' scenarios, page calls vs backend routes, the story's tasks [X]
Story scenarios = the @sc_ tags cited by the tasks of '## Phase N: User Story <n>'. Exit 0 pass, 1 fail, 2 usage."""
import argparse, os, re, shutil, subprocess, sys
from pathlib import Path

PY, SK = sys.executable, ".claude/skills"
INV, APP = [PY, f"{SK}/system-analysis/scripts/inventory.py"], ["--app", "app.main:app", "--html", "app/static"]

def run(cmd, stdin=None, env=None):
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env={**os.environ, **(env or {})})
    out = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode, out[-1] if out else "(no output)", out

def story(tasks, n):
    """Tags and [(task id, done)] of story n's phase, plus the tags cited by later stories."""
    k, tags, ids, later = None, [], [], []
    for line in tasks.read_text().splitlines():
        if line.startswith("## "):
            k = int(m[1]) if (m := re.match(r"## Phase \d+: User Story (\d+)\b", line)) else None
        elif k and (m := re.match(r"- \[([ xX])\] (T\d{3})((?:\s*\[[^\]]+\])*)", line)):
            cited = re.findall(r"\[@(sc_\w+)\]", m[3])
            if k == n: ids.append((m[2], m[1] != " ")); tags += cited
            elif k > n: later += cited
    return tags, ids, later

def gates(d, features):
    spec = f"{d}/spec.md"
    params = subprocess.run([PY, "tools/constitution_params.py", "get", "sdd-specify"], capture_output=True, text=True).stdout
    yield "spec", run([PY, f"{SK}/sdd-specify/scripts/spec_check.py", spec, "--params", "-"], stdin=params)
    yield "clarify", run([PY, f"{SK}/clarify-interview/scripts/clarify_check.py", spec])
    yield "research", run([PY, f"{SK}/technical-research/scripts/research_check.py", f"{d}/research.md", "--plan", f"{d}/plan.md"])
    yield "inventory", run(INV + ["check", f"{d}/inventory.md", "--spec", spec] + APP)
    yield "contract", run([PY, f"{SK}/contract-writer/scripts/contract_check.py", d])
    yield "plan", run([PY, "tools/plan_lint.py", d])
    yield "tasks", run([PY, f"{SK}/tasks-binder/scripts/task_coverage.py", "check", d, "--features", *features])

def checks(d, n, features, tags, ids, later):
    sel, ev = " or ".join(tags), Path(f"evidence/US{n}")
    shutil.rmtree(ev, ignore_errors=True)
    for drv in ("api", "ui"):
        code, last, out = run([PY, "-m", "pytest", "-q", "-m", sel, "--driver", drv], env={"EVIDENCE_DIR": str(ev)})
        passed = int(m[1]) if (m := re.search(r"(\d+) passed", last)) else 0
        if code == 5:                                     # pytest: no test selected
            code, last = 1, f"no test carries {sel}: {last}"
        elif code == 0 and passed < len(tags):            # '-m "a or typo"' runs a alone and exits 0
            code, last = 1, f"{last}: {passed} passed for {len(tags)} scenarios"
        if drv == "ui" and code == 0:
            last += f"; {ev}: {len(list(ev.glob('*.png')))} screenshots"
        yield drv, (code, last, out)
    rest = ["-m", f"not ({' or '.join(later)})"] if later else []   # later stories may still be red
    r = [run([PY, "-m", "pytest", "-q", "--driver", drv, *rest]) for drv in ("api", "ui")]
    yield "suite", (max(r[0][0], r[1][0]), f"{r[0][1]} | {r[1][1]}", r[0][2] + r[1][2])
    yield "routes", run(INV + ["scan"] + APP)
    open_ = [t for t, done in ids if not done]
    code, last, out = run([PY, f"{SK}/tasks-binder/scripts/task_coverage.py", "check", d, "--features", *features])
    done = f"not [X]: {' '.join(open_)}" if open_ else f"{ids[0][0]}..{ids[-1][0]} [X]"
    yield "tasks", (code or len(open_), f"{done}; {last}", out)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("feature_dir")
    ap.add_argument("story", nargs="?", help="US1, US2, ...")
    ap.add_argument("--entry", action="store_true", help="run the planning gates instead of a story checkpoint")
    a = ap.parse_args()
    tasks = Path(a.feature_dir) / "tasks.md"
    if not tasks.is_file() or a.entry == bool(a.story) or (a.story and not re.fullmatch(r"US\d+", a.story)):
        ap.error("give FEATURE_DIR (with tasks.md) and either --entry or US<n>")
    features = re.findall(r"features/[\w./-]+\.feature", tasks.read_text())
    if a.entry:
        steps = gates(a.feature_dir, features)
    elif (found := story(tasks, int(a.story[2:])))[0]:
        steps = checks(a.feature_dir, a.story[2:], features, *found)
    else:
        ap.error(f"no '## Phase N: User Story {a.story[2:]}' with @sc_ citations in {tasks}")
    failed = 0
    for name, (code, last, out) in steps:
        failed += code != 0
        print(f"{'ok  ' if code == 0 else 'FAIL'}  {name:<9} {last}")
        if code and out[-12:-1]:
            print("\n".join("      " + l for l in out[-12:-1]))
    print(f"{a.story or 'entry'}: {'5 checks' if a.story else '7 gates'}, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
```

第 1 次 agent 執行跑的是修正 suite 之前的版本（第三節），第 2、3 次跑的是修正後、排版較長的版本；上面這份是修正版只為篇幅重排的結果，我們在最終狀態上比對過兩者，輸出除了秒數逐行相同。三個設計：**故事的範圍從 tasks.md 讀**，就是 Ch 16 label run 裡的 `@sc_` 引用，不必另一份設定；**每個檢查只回一行**，失敗才附上最後幾行輸出，agent 讀得完、人也讀得完；**`passed < len(tags)` 另外擋**，因為 [Ch 31](./31-bdd-in-sdd.md) 實測過 `-m "a or typo"` 只跑 a 卻 exit 0。`sdd-implement/SKILL.md` 設 `disable-model-invocation: true`，它會改程式，只讓人用 `/sdd-implement` 叫起來：

```markdown
---
name: sdd-implement
description: Implement one user story of a feature under specs/ from its tasks.md, tests first, and stop at that story's checkpoint for review. Use after tasks-binder, when asked to implement or continue a story such as US1.
argument-hint: "<feature dir> <US1|US2|...>"
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash(.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py *) Bash(.venv/bin/python -m pytest *)
---

# sdd-implement

One job: make story `$1` of `$0` true, then stop. The next story starts only after a person has reviewed this one.

## Contract

- Reads: `$0/tasks.md` first, then what its tasks cite (`$0/spec.md`, `research.md`, `inventory.md`, `contracts/interfaces.md`, `data-model.md`), and `features/`, `tests/`, `app/`
- Writes: the files that the unchecked tasks up to `$1`'s phase name, and `[X]` marks in `$0/tasks.md`
- Never writes: `features/`, any other file under `specs/`, `.claude/`
- Done when: `.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py $0 $1` exits 0 and its last line ends with `0 failed`

## Steps

Run every command exactly as written, one per Bash call: no `; echo`, no `&&`, no `VAR=value` prefix. A call denied because of such an addition says nothing about the command itself; run it again as written.

1. Run `.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py $0 --entry`. If a gate fails, stop and report it: a planning artifact is wrong, and code would implement the mistake.
2. Do the unchecked Setup and Foundational tasks, then the tasks of `$1`'s phase, in file order. For anything the page shows, follow `.claude/skills/verify-booking-ui/SKILL.md`.
3. A task that cites a scenario or names `tests/` is a test task: write it, run it with `.venv/bin/python -m pytest -q ...`, and see it fail for the reason the task gives before you write the code that makes it pass. An ImportError or a missing step is not that reason; fix the test first.
4. Copy status codes, messages and formats from the cited source word for word. If no source decides something you need, choose the smallest option and list it in your report.
5. Mark a task `[X]` only when its own check passes.
6. Run the checkpoint for `$1`. Fix what it reports and run it again, at most 3 rounds. Never change a scenario, a step's wording or an expected value to get a pass.
7. Stop. Report the checkpoint output, the files you changed, what the screenshots under `evidence/$1/` show, and every decision the sources left open. Do not start the next story.
```

「Run every command exactly as written」那段是審過第一次執行才加的。驗證 skill `verify-booking-ui/SKILL.md`（21 行）照 [pstack Ch 21](../pstack/21-verification-skills.md) 的五段寫，每段都落在這個專案的真實指令上：

| 段落 | 寫了什麼 |
|---|---|
| Launch | 不准自己啟動 uvicorn、不准挑 port（這台機器的 8000、8765 有別的專案）；`tests/conftest.py` 的 `base_url` 在執行緒裡用空的 port 跑 app，pytest 結束就停 |
| Doctor | `.venv/bin/python -m pytest -q -m sc_book_01 --driver ui` 必須過；不過就是 Chromium 或 harness 壞了，回報並停下 |
| Drive | `.venv/bin/python -m pytest -q -m "<tags>" --driver ui`；只有 `UiDriver` 碰頁面，而且只用可存取名稱（`get_by_role("list", name="Free slots of Taipei")` 等），頁面少了控制項就補頁面，不在 driver 開後門 |
| Evidence | checkpoint 設好 `EVIDENCE_DIR`，每個 UI 情境留一張 `<test name>.png`；至少打開一張說出內容，「測試綠、截圖裡的清單卻不對」算失敗 |
| Cleanup | 沒有要殺的程序；`evidence/` 被 gitignore，由 checkpoint 清空重填，不手動刪 |

`node /tmp/lab/tools/skill-lint.mjs .claude/skills` 得 `19 skills, 0 problems`。skill-creator 的 `quick_validate.py` 對 verify-booking-ui 回 `Skill is valid!`，對 sdd-implement 回 `Unexpected key(s) in SKILL.md frontmatter: argument-hint, disable-model-invocation. Allowed properties are: allowed-tools, compatibility, description, license, metadata, name`。它檢查的是 Agent Skills 規格的欄位，這兩個是 Claude Code 的擴充欄位（[skills 文件](https://code.claude.com/docs/en/skills)「Using skill frontmatter outside Claude Code」：在 Claude Code 之外只能用規格裡的欄位）。Ch 15 的 skill 用了它們；Ch 14 的 contract-writer 只有 `name` 與 `description`，quick_validate 回 `Skill is valid!`。要給別家 agent 用的 skill，就得拿掉。

### 三、實作：sdd-implement 跑兩次

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。

規劃 commit 後打上 tag `ready`，在 `proj/` 跑（lab 的 `runs/run.sh` 包著同一行，三次執行只換 prompt 與目錄）：

```bash
cd /tmp/lab/aibdd/practice-c-sdd-flow/proj && source /tmp/lab/aibdd/.venv/bin/activate
claude -p "/sdd-implement specs/001-free-slots US1" --setting-sources project --strict-mcp-config --no-session-persistence \
  --tools "Read,Glob,Grep,Edit,Write,Bash" --permission-prompts none --max-turns 20 --max-budget-usd 4 \
  --allowedTools "Read" "Glob" "Grep" "Edit(/app/**)" "Edit(/tests/**)" "Edit(/pytest.ini)" "Edit(/specs/001-free-slots/tasks.md)" \
    "Bash(.venv/bin/python -m pytest *)" "Bash(.venv/bin/python .claude/skills/sdd-implement/scripts/checkpoint.py *)" \
    "Bash(.specify/scripts/bash/check-prerequisites.sh *)" --output-format stream-json --verbose < /dev/null > ../runs/r1-us1.jsonl
```

| | 第 1 次：`/sdd-implement … US1` | 第 2 次：`/sdd-implement … US2` | 第 3 次：原版 `/speckit-implement` |
|---|---|---|---|
| 結果 | success | success | `error_max_turns` |
| assistant 回應／工具呼叫 | 12／62 | 8／29 | 20／71 |
| 秒、美元（`total_cost_usd`） | 326、0.87 | 106、0.41 | 318、1.05 |
| 權限拒絕 | 3 | 2 | 1 |

第 3 次在第 20 個回應之後被 `--max-turns 20` 停下；一個回應可以同時發好幾個工具呼叫，第 1 次只用 12 個回應就發了 62 個。結果 JSON 的 `num_turns` 在前兩次是 63、30（呼叫數加一），第 3 次是 21。

**第 1 次（US1）。** 第一個呼叫就是入口閘門（被拒，見下）；接著讀 tasks.md、verify skill、checkpoint 原始碼與六份規劃產物，再讀 app、tests、兩個 feature 檔與 constitution；照 verify skill 的 Doctor 跑 `-m sc_book_01 --driver ui`；讀 `inventory.py` 的 `norm()`，確認新的 `fetch` 會被正規化成 `/rooms/{}/free-slots`。測試先紅，紅的理由各不相同：API 層 `assert [] == ['08:00-20:00']` 與 `('rejected', 'Not Found')`（端點還不存在）；UI 層 `Locator.click: Timeout 30000ms exceeded`，等的是 `get_by_role("button", name="Free slots")`，四個情境等了 120.86 秒；單元測試先對一個回 `[]` 的 stub 紅，再換成真的實作。它把三個預約 step 從 `test_booking.py` 搬進 `conftest.py`。三次拒絕：兩次是它在允許的指令上自己加了東西（`checkpoint.py … --entry; echo "exit=$?"; git ls-files | head -100; ls -a` 與 `EVIDENCE_DIR=evidence/US1 .venv/bin/python -m pytest …`），一次是規則外的 `sed -i` 打勾（改用七次 Edit）。它的報告：

```text
**US1 is implemented, but it doesn't meet the done condition yet.** The checkpoint never ran, and it would fail on its suite check if it did. All four US1 scenarios pass under both drivers, and T001–T007 are marked `[X]`. Nothing is committed, and I didn't start US2.
…（略）
2. **US1 checkpoint not run:** I didn't try it, because the same script had already been denied, so there is no checkpoint output to report. I ran its pytest commands directly (results below). The route and task-coverage checks did not run. By reading the code, the new `fetch` URL should normalise to the `/rooms/{}/free-slots` route.
3. **Suite check will fail until US2:** `@sc_free_04` fails under `--driver ui` because the list doesn't appear after a booking. T008 expects that failure, but the US1 checkpoint runs the whole suite under both drivers, so `US1: 5 checks, 0 failed` can't happen before T009. This is a contradiction in `tasks.md`. I didn't hide it with an `xfail` or have the driver press the button, and I didn't do T009 early. The planners need to narrow the suite check or accept this failure at US1.
```

第 2 條是誤判：被拒的是那串複合指令，不是腳本。第 3 條是真的，只是錯不在 tasks.md，在我們的 checkpoint：當時的 suite 檢查跑全部情境。我們用當時的版本自己跑一次：

```
…（略）
FAIL  suite     14 passed in 0.11s | 1 failed, 13 passed in 31.63s
…（略）
      E             - waiting for get_by_role("list", name="Free slots of Taipei")
…（略）
US1: 5 checks, 1 failed
```

審查的決定：改 checkpoint（`story()` 多回傳後面故事的標籤、suite 加上 `-m "not (…)"`，就是上面原始碼裡帶 `later` 的那幾行），改 SKILL.md（加「照原樣」那段），tasks.md 不動。報告列出的三個開放決定記成下一輪 clarify 的問題：Room 空白時頁面顯示 `Rejected: Not Found`、會議室名稱分大小寫、整天訂滿時只有空清單。重跑得到和期望輸出範例相同的五行與 `US1: 5 checks, 0 failed`（秒數略有不同）。四張截圖裡，back-to-back 那張是 Room 填著 Taipei、清單兩項 `08:00-10:00`、`12:00-20:00`；Osaka 那張是 `Rejected: room Osaka not found; choose Taipei, Tokyo or Seoul`。

**第 2 次（US2）。** 這次 `--entry` 照原樣跑成。T008 如預期：`-m sc_free_04 --driver api` 是 `1 passed, 13 deselected in 0.08s`，`--driver ui` 是 `Locator.wait_for: Timeout 30000ms exceeded`。它改寫一次 `index.html`（201 之後先 GET 剛訂那間的空檔、重畫清單，再寫 `Booked Taipei`），checkpoint 第一次就 `US2: 5 checks, 0 failed`。它想在 `/tmp` 寫一支腳本驗「清單已經顯示時再預約」，被拒兩次；然後打開截圖、描述內容，交回：

```text
- **Booking on a page that already shows a list:** this is the spec's Independent Test for US2. `UiDriver.book` reloads the page first, so the scenario only ever draws the list from empty.
…（略）
3. **Order of updates:** the list is redrawn before the status is written, so "Booked" never appears beside a stale list. As a result, if the free-slots fetch fails at network level after the 201, the status isn't written and the person sees no confirmation. A retry would then get a 409 naming them.
```

我們另寫一支審查用的 Playwright 腳本補驗（`review_same_page.py`，26 行，正文不列出：像 conftest 一樣在執行緒裡起 app，同一頁不重新載入，先按 Free slots、再填表預約，最後訂一間 Osaka；要自己驗，走延伸挑戰第 2 條），輸出和它的推論一致；Osaka 那行也證實了 tasks.md 最後一行回報的缺口：POST /bookings 收下了不存在的會議室。

```
before: ['08:00-10:00', '11:00-20:00']
after:  ['08:00-10:00', '11:00-13:00', '14:00-20:00']
Osaka:  Booked Osaka | list hidden: True
```

T010 由我們跑 quickstart：Q1、Q2 各 `4 passed, 10 deselected`，Q3 `1 passed, 13 deselected`，Q4 是兩張 checkpoint；全套件在兩個 driver 下都 `14 passed`。

### 四、對照：原版 /speckit-implement

以下同樣是實際執行紀錄（節錄），2026-11-02，每次執行結果不同。從 `ready` clone 一份、刪掉 remote，套上同一版 checkpoint，用同一行指令跑 `/speckit-implement`。第一個指令就失敗：`ERROR: Feature directory not found. Set SPECIFY_FEATURE_DIRECTORY or run the specify command to create .specify/feature.json.`。它改用 `SPECIFY_FEATURE_DIRECTORY=$PWD/specs/001-free-slots .specify/scripts/bash/check-prerequisites.sh …`，被拒（規則對不上環境變數開頭），於是直接讀檔。它在 `ls .claude/skills` 看到 `sdd-implement`，**讀 tasks.md 之前就先讀了我們的 SKILL.md、`checkpoint.py` 與 verify skill**；tasks.md 的 Checkpoint 行也點名了那支腳本。它跑了 `--entry` 與 Doctor，測試一樣先紅；US1 的 checkpoint 綠了就繼續做 US2，最後一則訊息是「T009 is green. For T010 I'm running quickstart Q1–Q4, …」，接著撞上 `--max-turns`，沒有報告，T010 沒打勾。用我們的 checkpoint 驗它的結果，US1、US2 都 `5 checks, 0 failed`（它的套件 13 個測試，少寫一個單元測試）。整條流程的對照，前四列的數字來自各章的實測：

| 階段 | 原版 1.2.5 靠什麼收尾 | 自訂流程的閘門 | 實測 |
|---|---|---|---|
| specify | 模型自評的 checklist | `spec_check.py` | Ch 17：原版的 spec 3 problems，`sdd-specify` 0 |
| clarify | 問完 5 題或人喊停；紀錄沒有落點 | `clarify_check.py` | Ch 18：`claude -p` 裡沒有 AskUserQuestion，改用 `answers=` 重播 |
| plan | Done When 只問流程跑完 | research、inventory、contract、plan_lint | Ch 13：原版 plan 6 problems；Ch 14：原版契約 32 problems |
| tasks | Done When 只問有 phase、ID、路徑 | `task_coverage.py` | Ch 16：原版 40 problems，binder 0（現行版 4） |
| implement | 「Validation checkpoints」靠模型判斷，所有 phase 一路做完 | `checkpoint.py`；一次一個故事、做完停下交報告 | 本練習：兩邊的程式都 `0 failed`；原版停在 T010、沒有報告 |

### 五、故意弄壞三處

從最終狀態 clone 三份，各改一行，跑 US1 的 checkpoint：

| 弄壞的地方 | 改動 | api | ui | suite | routes | 整次耗時 |
|---|---|---|---|---|---|---|
| 頁面打錯路徑 | `` fetch(`…/free`) `` | ok | FAIL，91.47 秒 | FAIL（ui 那半） | FAIL | 184.71 秒 |
| 後端回 `time` 物件，違反 DEC-01 | `{"start": s, "end": e}` | FAIL | FAIL | FAIL（兩半） | ok | 4.04 秒 |
| 頁面讀錯欄位 | `x.from+'-'+x.to` | ok | FAIL | FAIL（ui 那半） | ok | 3.91 秒 |

```
route  ERROR index.html:7 calls GET /rooms/{}/free: no backend route
route  E       AssertionError: assert ('rejected', 'Not Found') == ('rejected', ...kyo or Seoul')
shape  E       AssertionError: assert ['08:00:00-20:00:00'] == ['08:00-20:00']
page   E       AssertionError: assert ['undefined-undefined'] == ['08:00-20:00']
```

（每行去掉 checkpoint 的六格縮排、在前面加上副本名，其餘是原文。）路徑錯時，靜態掃描在 0.12 秒內指出第 7 行（單獨跑三次，0.11–0.12 秒）；UI 層等滿三個 30 秒逾時，最後印出的卻是 Osaka 那條的斷言，看起來像 FR-004 的訊息寫錯。回應形狀錯時，API 與 UI 兩層一起紅。頁面讀錯欄位時只有 UI 層紅，API 的情境與路由對照全綠。三層各抓一種錯，少跑任何一層都會放過其中一種。

**解答說明**：

- 同一支 checkpoint 有兩種模式：`--entry` 擋「錯的計畫」，故事模式擋「錯的實作」。前者讓來自六章的七道檢查第一次在同一個專案一起跑，慣例要對齊：Ch 13 的 plan_lint 只找 `### I. 名稱` 的原則、只認 `Principle IV` 這種錨點；Ch 15 lab 的 constitution 用 `## I.`、黃金樣本寫 `constitution III`，Ch 16 的專案原樣跑 plan_lint 因此多出一則 PL-06 與一則 PL-05（Ch 15 踩雷 6）。參考解答的 constitution 來自 Ch 17（`### I.`），research.md 一律寫 `Principle`。
- checkpoint 自己也有 bug：第一版在「做了一半」的狀態下永遠過不了，是 agent 拒絕用 `xfail` 掩蓋才找到。檢查器除了先看它失敗，也要看它在部分完成時怎麼表現。
- 原版 implement 也照跑了 checkpoint，但這次分不出它照的是 tasks.md 的 Checkpoint 行，還是它自己讀到的 `sdd-implement/SKILL.md`：兩處都點名同一支腳本。能確定的是，skill 只在被載入時生效，tasks.md 則是 `/speckit-implement` 第 3 步規定必讀的檔，閘門寫在產物裡，換一套 implement 也帶得走。skill 剩下的價值是「在哪裡停、交什麼給人」。兩條路各只跑一次，這是個案，不是基準。
- 規劃產物是手寫的。你用 agent 跑各個 skill 時，閘門一樣應該是 0，措辭與決策會不同；審的重點放在盤點、契約與 tasks 的語意（Ch 13 清單「人判」那欄）。

</details>

## 測試用例

| 輸入／檢查 | 預期輸出 | 說明 |
|---|---|---|
| spec 草稿跑 `clarify_check.py` | 兩行 `CL-1`，`1 files, 2 problems` | spec_check 0 不代表能進 plan |
| 沒 activate venv 就跑 `setup-plan.sh` | `Error: PyYAML is required to resolve preset template composition` | preset 讓 bash 解析器需要 PyYAML |
| 規劃完成跑 `checkpoint.py --entry` | `entry: 7 gates, 0 failed` | 六章的七道檢查在同一個專案相容 |
| 實作前跑 `checkpoint.py … US1` | `US1: 5 checks, 3 failed`，`no test carries …` | pytest 的 `-m` 選不到測試時 exit 5 |
| `checkpoint.py … US3` | `error: no '## Phase N: User Story 3' …`，exit 2 | 用法錯誤與「檢查沒過」分開 |
| 頁面 `fetch` 打錯路徑 | `routes` 報 `ERROR index.html:7 …`；ui 91 秒後才紅 | 先看便宜又精確的那一層 |
| 後端回 `time` 物件 | api、ui 都是 `08:00:00-20:00:00` | 回應形狀錯，兩層一起紅 |
| 頁面讀 `x.from` | ui 與 suite 的 ui 那半紅：`undefined-undefined`；api、routes 綠 | 只有瀏覽器那層看得到 |
| 原版 implement 在新 clone | `ERROR: Feature directory not found …` | `feature.json` 不進版控 |

## 延伸挑戰（加分）

- **先便宜後昂貴**：讓 checkpoint 先跑 `routes`，它失敗就跳過 `ui`，再量一次「打錯路徑」那個副本（184.71 秒）；或在 conftest 把 Playwright 的預設逾時調短（[Ch 23](./23-frontend-e2e-playwright.md) 用 2000 ms），比較第 1 次執行紅燈那 120.86 秒。
- **補上 Independent Test**：替 `UiDriver` 加一條不重新載入頁面的路徑，寫「清單已顯示、再預約」的情境；先拿第 2 次執行之前的 `index.html` 看它紅，再看現在的。
- **關掉審查留下的三個問題**：Room 空白、大小寫、整天訂滿。走一輪 clarify、改 FR 與情境、重綁 tasks，觀察 `--entry` 裡哪道閘門先紅。
- **把 checkpoint 接成 Stop hook**：當前故事的 checkpoint 沒過就不讓 agent 結束（[Ch 20](./20-tdd-with-agents.md) 的 `require_green.py`、[Ch 31](./31-bdd-in-sdd.md) 的 `slice_stop.py` 是同一個形狀）。本段未實測；驗證方法是用管線把一筆 Stop 事件的 JSON 餵給 hook，看 exit code 與 stdout。換一個技術棧也行：拿 [web/firebase 的 Final Project](../../web/firebase/final-project-realtime-notes.md) 的一個功能走同一條流程，想想 `inventory.py` 的前後端對照要改成什麼。

## 自我檢核

- [ ] 不看本練習，說出 `--entry` 的七道閘門各擋哪一種錯、各來自哪一章，以及哪兩章的慣例會互相衝突。
- [ ] 第一版 checkpoint 為什麼在 US1 做完時永遠過不了？錯在 agent、tasks.md 還是 checkpoint？你用什麼證據判斷？
- [ ] 三種斷裂（打錯路徑、回應形狀、頁面讀錯欄位）各由哪一層抓到？為什麼路徑錯時 UI 層要 91 秒才失敗，訊息還指向別處？
- [ ] 原版 `/speckit-implement` 為什麼在新 clone 的第一步就失敗？`sdd-implement` 為什麼不會？
- [ ] 第 3 次執行也跑了 checkpoint，它的「流程」可能從哪兩處來？為什麼這次分不出是哪一處？閘門放在 skill 裡和放在產物裡，差在哪？
- [ ] 說出你的實作和參考解答的一個差異，以及各自的取捨。

## 延伸閱讀

- **[Spec Kit v1.2.5 — templates/commands/implement.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/implement.md)**
  - **讀哪裡**：Outline 第 5–9 步：「Task phases: Setup, Tests, Core, Integration, Polish」、「Phase-by-phase execution」、「Validation checkpoints: Verify each phase completion before proceeding」，以及 Done When。
  - **學到什麼**：原版的 checkpoint 是一句給模型的指示，沒有指令也沒有停點；phase 名稱照技術層命名，和 `/speckit-tasks` 產的故事 phase 對不上（Ch 16 也指出過）。
  - **和本練習的關聯**：第四節對照表 implement 那列，就是拿這幾句和 `checkpoint.py` 比；先讀 [Ch 12](./12-spec-kit-1x.md) 的讀寫對照表比較好對上。
- **[Ian Robinson — Consumer-Driven Contracts: A Service Evolution Pattern](https://martinfowler.com/articles/consumerDrivenContracts.html)**（martinfowler.com，2006-06-12）
  - **讀哪裡**：「Consumer-Driven Contracts」一節的 Provider Contracts、Consumer Contracts 與 Summary of Contract Characteristics。
  - **學到什麼**：consumer contract「express a subset of the system's business function capabilities in terms of the consumer's expectations」，提供者的契約該從消費者的期待長出來。文章的例子是服務之間的 XML 訊息、schema 版本化與 Schematron 斷言；前提是懂 schema 版本化的痛點，工具細節可以略讀。
  - **和本練習的關聯**：頁面就是 API 的消費者，路由對照與 UI driver 是兩個輕量的消費者端檢查。
- **[Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)**（2024-12-19）
  - **讀哪裡**：「Prompt chaining」一段與它的圖，特別是「You can add programmatic checks (see "gate” in the diagram below) on any intermediate steps」。
  - **學到什麼**：作者建議在固定步驟的串接裡，任何中間步驟都可以放程式化的檢查；這種流程適合能乾淨拆成固定子任務的工作。
  - **和本練習的關聯**：整條自訂流程就是加了閘門的 prompt chaining；每個故事之後停下來等人，是在鏈上多放一道人審的閘。

這個練習的 checkpoint 只問「測試有沒有綠」。測試是不是真的先寫、紅的理由對不對、agent 有沒有偷改測試，Part 4 從親手打一遍 TDD 開始處理。

→ [Ch 19 手打一遍 TDD：紅綠重構的會議室 kata](./19-tdd-by-hand.md)
