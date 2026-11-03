# Final Project — 會議室預約：從 example map 到雙 driver 驗收的完整流水線

> **目標**：從空目錄做出會議室預約的兩個 user story（預約、取消；FastAPI 後端、三個 API、一頁 UI），讓每一份產物先過前面各章的檢查器才往下傳：example map → feature 檔 → 自己合併的 Spec Kit preset 與 constitution → plan、research、端點盤點 → 依情境切的 tasks → 測試計畫 → 每個 story 一道紅燈閘門 → 一次一個 slice、兩個 driver 一起驗收 → 更新 truth、封存 plan。再把這些檢查器寫成一支 `pipeline.py`，讓人、hook 與 agent 問同一個問題：現在在哪一站，下一站缺什麼。各章的工具各自都對；這個專案要你親眼看到它們接在一起時從哪裡裂開。

> **環境**：Spec Kit 1.2.5（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`）、Claude Code 2.1.295（三次 `claude -p`，claude-opus-5-5）、Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、Playwright 1.64.1（Chromium），macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/final/` 從空目錄開始；參考解答的檔案、22 個 commit 與所有輸出都是 2026-11-02～03 實跑。

## 背景與動機

Part 5 的每一章只做一環，而且各在自己的實驗室裡：[Ch 29](./29-example-mapping.md) 從 map 產生 feature，[Ch 31](./31-bdd-in-sdd.md) 把 spec 裡的 Gherkin 抽成 feature 檔、把 tasks 依情境切，[Ch 25](./25-tdd-in-sdd-pipeline.md) 在實作前插一道紅燈閘門，[Ch 30](./30-one-gherkin-two-drivers.md) 讓一份 feature 走兩個 driver，[Ch 33](./33-truth-vs-plan.md) 分開 truth 與 plan。每個工具都帶著一個沒寫出來的假設：`bdd_check.py` 以為整次 pytest 都是情境，`red_gate.py --plan` 以為計畫以外的測試都該是綠的，每個 preset 都以為 tasks-template 歸它管，每支 Stop hook 都以為「做完」就是它檢查的那件事。分開跑，假設都成立；放進同一個 repo，它們互相踩到。

repo 裡最接近的整合是 [SDD 的 final project](../spec_driven_development/final-project-ship-with-sdd.md)：「3. 分階段任務」帶一個產品走過 Spec Kit、Kiro 或自建 pipeline，驗收卻排在「階段 5：驗收」，在實作 pipeline 之後。[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的「二、spec review 作為新的 code review」審的是 EARS，不是會執行的 Gherkin；[SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md) 的「意義一＋意義二的結合點：ATDD-SDD 混合」是一張圖。[Ch 29](./29-example-mapping.md) 的「七、導入流程：誰起草、誰審、誰簽」在一張 story 上走過一次流程，但只有後端，沒有 Spec Kit，也沒有測試計畫。這個專案補上剩下的：前後端、用自己的 skill 取代 stock 指令、實作期間的跨層檢查、每個 story 一個 checkpoint，而且每個箭頭都有一行指令把關。它對應外部課綱 3-16、4-8、5-3、5-9 四個單元「接起來」的那一半：[Ch 1](./01-course-map.md) 的對照表把 5-3 指到 Ch 29 與本專案，另外三個單元的主章是練習 C、Ch 25 與練習 D、Ch 31，這裡把它們串成一條線。Ch 2–33 的 32 章裡，Ch 3、8、12–17、19–33 這 23 章的腳本、skill 或做法在這裡直接用上（Step 0 那張表是其中 17 章的工具）。

## 先建立直覺

想像一條接力賽跑道。每一棒都練過，單獨計時都很快；比賽輸在交棒區：前一棒以為後一棒會在線外接，後一棒以為前一棒會跑進區內。各章的檢查器是各棒，這個專案的功課是交棒區。每一站把一份產物交給下一站，交接的憑證是 ID（`R1`、`FR-002`、`@sc_cancel_03`、`TP-010`、`T014`），每個交棒區由一支腳本確認交接成立。比喻有一處不成立：接力只跑一次，這條線每次改動都要整條重跑，所以交接要靠腳本，不能靠默契。

```
 站       產物（誰寫）                                 交棒檢查                                    放行條件
 ① 發現   maps/*.yaml（三方；AI 不作答）               example_map.py check、map_trace             READY；map 上每個例子都成了情境
 ② 表述   spec.md 的 gherkin → features/（QA 潤飾）    spec_check、extract --check、gherkin_lint    PM＋QA 執行 approve，hash 進 commit
 ③ 計畫   plan、research、data-model、contracts、       plan_lint、research_check、inventory check
          inventory（agent 起草，RD 審）
 ④ 任務   tasks.md：每個 story 一段 RED、一段 GREEN     scenario_task_map、task_coverage
 ⑤ 紅燈   testplan.md、TP 測試、step definitions        red_gate（只看這個 story 的案例）、          人讀證據、commit、tag red-001-usN
          （test-writer）                              aaa_lint、step 全部綁上
 ⑥ slice  app 程式，一次一個（implementer）            start：開工前紅在 Then；收工：Verify --driver both
 ⑦ story  checkpoint                                   兩個 driver、red_gate --expect green、
                                                       tamper_check、inventory scan（頁面 ↔ 路由）
 ⑧ truth  contracts/、features/、glossary 留下；        truth_drift truth／plans、archive_plan.sh、活文件
          specs/001 封存
```

> 每一站的工具出自哪一章：map 與 tester 的四個方向見 [Ch 29](./29-example-mapping.md)，抽取與 slice 見 [Ch 31](./31-bdd-in-sdd.md)，TP 案例見 [Ch 24](./24-testplan-skill.md)，紅燈閘門見 [Ch 25](./25-tdd-in-sdd-pipeline.md)，driver 見 [Ch 30](./30-one-gherkin-two-drivers.md)，truth 與 plan 見 [Ch 33](./33-truth-vs-plan.md)。

## 任務規格

| 交付物 | 驗收標準（每一條都是一行指令的輸出） |
|---|---|
| `maps/book.yaml`、`maps/cancel.yaml` | `example_map.py check` 回 `2 maps, 0 problems`；map 上每個例子 id 都是某個 feature 檔的 tag |
| `specs/001-room-booking/spec.md` 與 `features/` | `spec_check.py`、`extract_features.py --check`、`gherkin_lint.py` 皆 0；`.bdd/approved.json` 已 commit |
| plan、research、data-model、quickstart、contracts/、inventory | `plan_lint.py`、`research_check.py --plan`、`inventory.py check` 皆 0；contracts/ 同時寫 HTTP 與頁面 |
| preset `booking-pipeline` | 一個 preset 提供 spec-template 與 tasks-template；`specify preset resolve tasks-template` 指向它 |
| `tasks.md` | `scenario_task_map.py` 與 `task_coverage.py` 同時 0 |
| `testplan.md` | `testplan_lint.py` 0；每個案例至少追溯一個 `@sc_` tag |
| 每個 story 的紅燈 | `pipeline.py gate red specs/001-room-booking --story USn` PASS，證據檔隨測試 commit，tag `red-001-usN` |
| 每個 slice | `pipeline.py start` 放行才動手；收工時它的 Verify（`--driver both`）綠 |
| 每個 story 的 checkpoint | `pipeline.py gate story specs/001-room-booking --story USn` PASS |
| 收尾 | `truth_drift.py truth` 與 `plans` 都 0；plan 封存；`living_doc.py` 回 `N scenarios, 0 failed` |
| hooks | PreToolUse 兩支 guard、PostToolUse `aaa_hook.py`、Stop 兩支（RED 一支、GREEN 一支，互斥）；每支用管線餵 JSON 證明會擋 |
| 實跑 | 至少一次 `claude -p`：一個角色在 hook 底下完成一個階段；附上它的回報與你的審查 |

規則：各章腳本的判準不准改，只准加參數或修誤報，每一處改動寫一行理由；腳本只用標準函式庫、PyYAML 與 pytest-bdd 帶進來的 gherkin-official；`claude -p` 只在 `/tmp/lab/aibdd/final/runs/` 的 clone 裡跑，帶 `--max-turns`，用 `--tools` 縮小工具集。

## 期望輸出範例

最後一個 slice 做完、還沒更新 truth 時：

```
$ .venv/bin/python tools/pipeline.py status specs/001-room-booking
PASS discover   2 checks
PASS formulate  4 checks
PASS plan       3 checks
PASS tasks      2 checks
PASS testplan   1 check
PASS red US1    tag red-001-us1: 7 tests, 7 red as planned, 0 problems
PASS story US1  5 checks
PASS red US2    tag red-001-us2: 6 tests, 6 red as planned, 0 problems
PASS story US2  5 checks
FAIL truth      10 scenarios, 0 endpoints, 4 terms, 3 problems
10 gates, 1 failed; next: truth
```

把 contracts/ 複製到根目錄、封存 plan 之後是 `PASS truth      2 checks`。一個跨層的錯：頁面把取消送到 `DELETE /bookings/{id}/cancel`，後端沒有這條路由：

```
$ .venv/bin/python tools/pipeline.py gate story specs/001-room-booking --story US2
…（略：UI 通道的斷言失敗）
FAILED tests/acceptance/test_features.py::test_the_one_where_bob_cancels_his_own_booking[ui]
…（略：另外三個 [ui] 情境）
4 failed, 6 passed, 14 deselected in 1.04s
…（略：TP 測試、VERIFY 與 tamper_check 全過，以及 scan 印出的路由表）
ERROR index.html:22 calls DELETE /bookings/{}/cancel: no backend route
5 routes, 3 calls, 1 problem, 0 warnings
FAIL story US2  4 failed, 6 passed, 14 deselected in 1.04s
```

## 如果你卡住了

1. **`bdd_check.py red` 說 NOT met，`red_gate.py` 說另一回事**：它們判的是不是同一次 pytest 的同一層？每支閘門都以為整次執行歸它管。
2. **裝了 Ch 25 和 Ch 31 的 preset，tasks.md 裡的 RED 段落不見了**：跑 `specify preset resolve tasks-template`，看最上層是誰。
3. **slice 明明做完，Stop hook 還在擋**：讀它跑的指令。整個套件要到最後一個 slice 才會綠。
4. **`task_coverage.py` 報 TC-07**：讀它的規則：同一個 `##` story 段裡，綁情境的 task 要排最前面。你的 RED 任務排在哪一段？
5. **一個 TP 案例怎麼寫都「stops before its assertion」**：看它的 Arrange 用到什麼。同一批還沒實作的行為，Arrange 也用不了。
6. **journey 明明跑過，`truth_drift.py` 說 never ran**：它是用 `scenarios()` 綁進來的嗎？把 `--cucumberjson` 的 `uri` 印出來，和 [Ch 33](./33-truth-vs-plan.md) 第四節說的比對鍵比。

## 實作步驟建議

### Step 0：從空目錄起專案，搬進各章的工具

`git init`、`specify init --here --force --non-interactive --integration claude --script sh`，再 `ln -s /tmp/lab/aibdd/.venv .venv`。從你在各章建好的目錄複製下表的 skill 與腳本，保留各章的資料夾名稱，skill 之間的路徑才不用改。跑 `node /tmp/lab/tools/skill-lint.mjs .claude/skills`，參考解答是 `20 skills, 0 problems`（含 Spec Kit 的 10 個）。

| 出自 | 搬什麼 |
|---|---|
| Ch 8、28、29 | `example-mapping`（`example_map.py`）、`gherkin-style`（GH-01..GH-13）、`glossary.yaml` |
| Ch 27、31 | `bdd`（`bdd_check.py`）、`feature_guard.py`、`bdd-plan`（`extract_features.py`、`scenario_task_map.py`）、`slice_stop.py` |
| Ch 13、15、16、17 | `plan_lint.py`、`technical-research`、`system-analysis`、`tasks-binder`、`sdd-specify`、`constitution_params.py` |
| Ch 20、21、24、25 | `tdd`（`red_gate.py`、`aaa_lint.py`、`tamper_check.py`）、`tdd_guard.py`、`aaa_hook.py`、`testplan` |
| Ch 26、30、32、33 | `living_doc.py`、driver 分層、`mutate.py`、`truth_drift.py`、`archive_plan.sh` |

之後每個 shell 先 `source /tmp/lab/aibdd/.venv/bin/activate`。裝了 preset 之後，Spec Kit 的 bash 腳本要用有 PyYAML 的 Python 解析模板；用系統的 `python3` 跑 Step 2 的 `create-new-feature.sh`，得到 `Error: PyYAML is required to resolve preset template composition`、exit 2（[Ch 17](./17-constitution-and-presets.md) 踩雷 4；也可以設 `SPECKIT_PYTHON_EXECUTABLE`）。Step 4 的 preset 與 constitution 要在 Step 2 之前就位，因為 `create-new-feature.sh` 要用 preset 的 spec-template：參考解答在第一個 commit 就把 preset 放在專案外的 `../presets/booking-pipeline/`，`specify preset add --dev ../presets/booking-pipeline` 回 `✓ Preset 'Booking pipeline' v1.0.0 installed (priority 10)`。

### Step 1：發現

預約與取消各排一場 Example Mapping。取消那張可以直接用 Ch 29 的 v3；預約那張自己排，紅卡由 owner 答（參考解答的 Q2 把營業時間推給下一個改動 R3）。

### Step 2：表述與簽核

`create-new-feature.sh --json --short-name "room-booking" "..."` 用 preset 的 spec-template 建 `specs/001-room-booking/`（preset 在 Step 0 就裝好了）。把 `example_map.py gherkin` 的骨架去掉 `Feature:` 與 `Source:` 兩行、整段少縮兩格，貼進每個 user story 的 gherkin 區塊。QA 在這裡潤飾：重疊那張綠卡改成三列的 Scenario Outline（GH-07），agenda 那張的 Then 改成資料表。journey 不是綠卡（Ch 29 的 EM-05），放在 `features/journeys/`，由 US2 段落點名。抽取、lint、`spec_check`，三方讀過後由人跑 `bdd_check.py approve`，commit `.bdd/approved.json`。

### Step 3：計畫

Technical Context 用 `[DEC-01]`..`[DEC-03]` 指向 research.md 的三個決定：資料放記憶體、頁面用 booking id 取消、一套情境走兩個 driver。contracts/ 寫兩份：`http.md`（Ch 33 的 `EP:` 格式）與 `page.md`（UI driver 依賴的無障礙名稱）。參考解答一開始只寫了前者，到 US2 的 RED 前才發現：沒有 `page.md`，test-writer 寫不出 UI driver 的 `cancel`。

### Step 4：合併 preset，寫 tasks.md

Ch 25 與 Ch 31 的 preset 都 `replaces: "tasks-template"`，不能疊（踩雷 1）。合成一個 `booking-pipeline`：spec-template 用 Ch 31 的；tasks-template 給每個 story 兩段 `##`，RED 段的任務引用 spec 段落、FR 或 constitution 第 II 條，**不引用情境**；GREEN 段的 slice 擁有情境，下一行是 `--driver both` 的 Verify。constitution 1.0.0 寫五條原則（Executable Acceptance、Test-First、Scenario Slices、Rule IDs Everywhere、Truth and Plan），`### bdd-plan` 把 slice 上限訂為 2、Verify 加上 `--driver both`。先手寫一份黃金 tasks.md 過兩支檢查（[Ch 14](./14-artifact-first.md) 的做法），再拿模板對它。從 Ch 31 搬來的 `bdd-plan` 也要對齊：第 4 步的分段照這份模板寫，第 5 步的閘門換成兩支檢查都跑的 `pipeline.py gate tasks`；參考解答漏了這一步，第三次實跑才撞出來。

### Step 5：把檢查器寫成 pipeline.py，接上 hook

一站一個 list，每項是一個指令或一個小函式；`status` 一行一站，`gate` 跑一站，`start` 發一個 slice。phase 由人用一支腳本同時寫兩個 phase 檔；兩支 Stop hook 一支管 RED、一支管 GREEN，任何時刻至多一支表態。

### Step 6：每個 story：RED、閘門、slice、checkpoint

RED：test-writer 把這個 story 的案例加進 testplan.md，寫 TP 測試、step definitions、DSL 與 driver。人跑 `gate red --story USn`，逐行讀證據，commit、tag、切 GREEN。每個 slice 由人跑 `start` 放行，implementer 收工時由 Stop hook 跑它的 Verify；最後一個 slice 之後跑 `gate story`。

### Step 7：truth、封存、活文件

把 plan 的 contracts/ 複製到根目錄成為 truth，`truth_drift.py truth` 到 0。用複製不用搬：plan 要整份封存，搬走的話 `gate plan` 回 `PL-01 missing contracts/: Phase 0/1 of /speckit-plan must produce it`。接著用 `archive_plan.sh` 封存，`plans` 到 0；`--driver both` 跑全套，產生活文件。

### Step 8：誠實復盤

寫下每一站擋過什麼（附輸出）、你改了哪支課程腳本與理由、agent 在哪一站幫了忙或繞了路，以及和 stock Spec Kit 比多了什麼、少了什麼（「對比與取捨」那張表是起點）。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

> **先說清楚這份解答能重現到哪裡**：全文只印了 `pipeline.py`、`phase.sh`、`red_stop.py`，加上 `bdd_check.py` 的 diff、tasks-template 的節錄與 `mutants.yaml`；其餘產物（maps、constitution 與 preset、spec 與 plan 系列文件、tasks.md、testplan.md、feature 檔、測試、step definitions、DSL、driver、app 程式）都只描述形狀，沒有印出來。所以這一頁的輸出（連同前面的期望輸出範例）只在我們的實驗目錄 `/tmp/lab/aibdd/final/booking/` 換上本頁那三支腳本時逐字重現。照同樣步驟做出來的專案，會不一樣的是數字與編號：commit 數與 hash、TP 與 T 的編號（TP-001..013、T003..T016）、測試與情境數（`7 tests`、`10 scenarios`、`24 scenarios`、`37 passed`）、tasks 與引用數（`16 tasks, 25 sources`）、路由與呼叫數、`index.html:22` 這類行號、秒數、突變表，以及 `claude -p` 的回合、費用與回報。該一樣的是每一行輸出的格式、規則 ID（RG-2、TC-07、TD-4……），以及「測試用例」那張表：哪一種改壞在哪一站、被哪一句訊息擋下。

### 檔案結構

```
/tmp/lab/aibdd/final/
├── presets/booking-pipeline/    preset.yml 1.0.1、templates/{spec-template,tasks-template}.md
├── runs/                        三次 claude -p：run.sh、prompt-*.txt、*/run.jsonl、timeline.py
└── booking/                     產品 repo（22 個 commit、tag red-001-us1、red-001-us2）
    ├── .specify/memory/constitution.md    1.0.0：I–V 與 Skill Parameters（sdd-specify、bdd-plan）
    ├── .claude/settings.json              deny 四條、hooks 五支
    ├── .claude/hooks/                     feature_guard、tdd_guard、aaa_hook、slice_stop、red_stop（新）；
    │                                      bdd_stop、require_green 留著做踩雷 3 的對照，沒有接線
    ├── .claude/skills/                    10 個 speckit-* 加 10 個課程 skill
    ├── CLAUDE.md                          10 行：truth 在哪、phase 在哪、每個指令單獨跑
    ├── maps/、sources/、glossary.yaml      maps 照 Ch 33 算 plan，但 map_trace 要讀，所以留在根目錄
    ├── features/booking.feature、cancellation.feature、journeys/free_slot_journey.feature
    ├── contracts/http.md、page.md          收尾時從 plan 複製出來的 truth
    ├── specs/archive/2026-11-03-001-room-booking/   spec、plan、research、data-model、quickstart、
    │                                                inventory、contracts/、tasks、testplan、red-gate-us{1,2}.txt
    ├── booking/schedule.py、api.py、static/index.html
    ├── conftest.py                        --driver 選項：要放根目錄，理由見 Ch 32 的初始 conftest
    ├── tests/unit/、tests/api/             TP-001..013
    ├── tests/acceptance/                  conftest、dsl、test_features（全部 step）、drivers/{api,ui}.py
    └── tools/                             pipeline.py、phase.sh（新）；constitution_params、plan_lint、
                                           living_doc、truth_drift、archive_plan.sh、pre-commit、mutate.py
```

`--driver` 的 `pytest_addoption` 放在根目錄的 `conftest.py`。放進 `tests/acceptance/conftest.py` 試過：`pytest -q tests/acceptance --driver api` 照樣 `12 passed`，`pytest -q --driver api` 卻是 `error: unrecognized arguments: --driver`、exit 4，和 [Ch 32](./32-brownfield-bdd.md) 的 `pytest_plugins` 同一個原因：只有初始 conftest 能加選項。

### tools/pipeline.py

```python
#!/usr/bin/env python3
"""pipeline.py: the booking pipeline as data; every stage runs the course's own checkers, in order. Run it from the
project root; FEATURE_DIR is a Spec Kit feature directory with a spec.md, such as specs/001-room-booking.
  status FEATURE_DIR                    every stage of a plan in flight, one line each; the first FAIL is next
  gate STAGE FEATURE_DIR [--story USn]  discover formulate plan tasks testplan red story truth
  start TASK FEATURE_DIR                hand out a GREEN slice: its scenarios must fail in a Then step first
Exit 0 when every check passes, 1 when one fails, 2 on bad input: arguments, stage, FEATURE_DIR, a missing file."""
import argparse, json, re, subprocess, sys
from pathlib import Path

PY, SK, TAG = sys.executable, ".claude/skills", re.compile(r"@(sc_[a-z0-9]+_\d{2})\b")
STAGES = ("discover", "formulate", "plan", "tasks", "testplan", "red", "story", "truth")
sys.path[:0] = [f"{SK}/bdd/scripts", f"{SK}/bdd-plan/scripts"]   # imported where used, after main's checks

def run(cmd, stdin=None):
    out = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    return out.returncode == 0, (out.stdout + out.stderr).strip().splitlines() or ["(no output)"]

def story_files(f, story):
    """The feature files that spec.md names inside one user story, and their @sc_ tags."""
    spec = Path(f, "spec.md").read_text()
    part = re.search(rf"^### User Story {story[2:]} - .*?(?=^### |^## |\Z)", spec, re.M | re.S)
    files = re.findall(r"`(features/[\w/.-]+\.feature)`", part[0]) if part else []
    return files, sorted({t for x in files for t in TAG.findall(Path(x).read_text())})

def story_cases(f, story):
    """testplan.md cut down to the cases that trace one of the story's tags; red_gate gets this file."""
    files, tags = story_files(f, story)
    plan = Path(f, "testplan.md").read_text() if Path(f, "testplan.md").exists() else ""   # status runs before it exists
    head, *cases = re.split(r"(?m)^(?=### TP-)", plan.split("\n## Out of scope")[0])
    mine = [c for c in cases if set(TAG.findall(c)) & set(tags)]
    Path(".tdd").mkdir(exist_ok=True)
    Path(f".tdd/plan-{story.lower()}.md").write_text(head + "".join(mine))
    return files, tags, [f"tp{c[7:10]}" for c in mine]

def map_trace(_):
    """Every example on a map is a scenario, and every scenario but a journey is on a map."""
    mapped = {i: m for m in sorted(Path("maps").glob("*.yaml")) for i in re.findall(r"- id: (sc_\w+)", m.read_text())}
    tagged = {t: x for x in sorted(Path("features").rglob("*.feature")) for t in TAG.findall(x.read_text())}
    bad = [f"{m}: {i} is on the map but in no feature file" for i, m in mapped.items() if i not in tagged]
    bad += [f"{x}: @{t} is on no map; only journeys may skip the map" for t, x in tagged.items()
            if t not in mapped and "journeys" not in x.parts]
    return not bad, bad + [f"{len(mapped)} map examples, {len(tagged)} scenario tags, {len(bad)} problems"]

def approved(_):
    from bdd_check import drift                             # Ch 27: features/ vs .bdd/approved.json
    bad = drift()
    return not bad, bad + [f"{len(bad)} approval problems"]

def spec_check(f):
    params = run([PY, "tools/constitution_params.py", "get", "sdd-specify"])[1]
    return run([PY, f"{SK}/sdd-specify/scripts/spec_check.py", f"{f}/spec.md", "--params", "-"],
               stdin="\n".join(l for l in params if not l.startswith("note:")))

def steps_bound(files):
    features = [a for x in files for a in ("--feature", x)]
    printed = run([PY, "-m", "pytest", "--generate-missing", *features, "tests/acceptance"])[1]
    missing = [l for l in printed if " is not defined in the scenario " in l]
    return not missing, missing + [f"{len(files)} feature file{'s' * (len(files) != 1)}, {len(missing)} undefined steps"]

def stage(name, f, story=None):
    script = lambda skill, name: f"{SK}/{skill}/scripts/{name}"
    if name == "discover":
        maps = map(str, sorted(Path("maps").glob("*.yaml")))
        return [[PY, script("example-mapping", "example_map.py"), "check", *maps], map_trace]
    if name == "formulate":
        return [spec_check, [PY, script("bdd-plan", "extract_features.py"), f"{f}/spec.md", "--check"],
                [PY, script("gherkin-style", "gherkin_lint.py"), "--glossary", "glossary.yaml", "features"], approved]
    if name == "plan":
        return [[PY, "tools/plan_lint.py", f],
                [PY, script("technical-research", "research_check.py"), f"{f}/research.md", "--plan", f"{f}/plan.md"],
                [PY, script("system-analysis", "inventory.py"), "check", f"{f}/inventory.md", "--spec", f"{f}/spec.md"]]
    if name == "tasks":                                     # the cap lives in the constitution (Ch 17's parameter layer)
        cap = subprocess.run([PY, "tools/constitution_params.py", "get", "bdd-plan"], capture_output=True, text=True)
        return [[PY, script("bdd-plan", "scenario_task_map.py"), f"{f}/tasks.md", "--features", "features",
                 "--max-per-task", str(json.loads(cap.stdout)["max_scenarios_per_task"])],
                [PY, script("tasks-binder", "task_coverage.py"), "check", f, "--features",
                 *map(str, sorted(Path("features").rglob("*.feature")))]]
    if name == "testplan":
        return [[PY, script("testplan", "testplan_lint.py"), f"{f}/testplan.md", "--source", f"{f}/spec.md"]]
    if name == "truth":
        return [[PY, "tools/truth_drift.py", "truth", "--app", "booking.api:app"], [PY, "tools/truth_drift.py", "plans"]]
    files, tags, tps = story_cases(f, story)
    if not tps:
        return [lambda _: (False, [f"no case in {f}/testplan.md traces a tag of {story}; add the story's cases first"])]
    xml, plan = f".tdd/{name}-{story.lower()}.xml", f".tdd/plan-{story.lower()}.md"
    tp_tests = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/unit", "tests/api", "-k", " or ".join(tps),
                f"--junitxml={xml}"]
    if name == "red":                                       # inner loop: the story's cases, red for the right reason
        return [lambda _: (True, run(tp_tests)[1][-1:]), [PY, script("tdd", "red_gate.py"), xml, "--plan", plan],
                [PY, script("tdd", "aaa_lint.py"), "tests/unit", "tests/api"], lambda _: steps_bound(files)]
    return [[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/acceptance", "-m", " or ".join(tags),
             "--driver", "both"],
            tp_tests, [PY, script("tdd", "red_gate.py"), xml, "--plan", plan, "--expect", "green"],
            [PY, script("tdd", "tamper_check.py"), "--base", f"red-{Path(f).name[:3]}-{story.lower()}"],
            [PY, script("system-analysis", "inventory.py"), "scan", "--app", "booking.api:app",
             "--html", "booking/static"]]

def gate(name, f, story=None):
    steps = stage(name, f, story)
    results = [step(f) if callable(step) else run(step) for step in steps]
    shown = [l for step, (ok, lines) in zip(steps, results)    # a failure's tail; the red gate's evidence in full
             for l in (lines[-12:] if not ok else lines if "red_gate.py" in str(step) else lines[-1:])]
    failed = [lines[-1] for ok, lines in results if not ok]
    label = f"{name} {story}" if story else name
    summary = failed[0] if failed else f"{len(steps)} check{'s' * (len(steps) != 1)}"
    return not failed, shown, f"{'FAIL' if failed else 'PASS'} {label:<10} {summary}"

def red_evidence(f, story):
    tag = f"red-{Path(f).name[:3]}-{story.lower()}"
    ok, lines = run(["git", "show", f"{tag}:{f}/red-gate-{story.lower()}.txt"])
    ok = ok and lines[-1].startswith("PASS red")
    gist = next((l for l in lines if "red as planned" in l), "") if ok else "no committed red-gate file that ends in PASS"
    return ok, f"{'PASS' if ok else 'FAIL'} red {story:<6} tag {tag}: {gist}"

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "gate", "start"])
    ap.add_argument("args", nargs="+", help="status FEATURE_DIR | gate STAGE FEATURE_DIR | start TASK FEATURE_DIR")
    ap.add_argument("--story", help="USn, for the red and story gates")
    a = ap.parse_args()
    shape = {"status": "FEATURE_DIR", "gate": "STAGE FEATURE_DIR", "start": "TASK FEATURE_DIR"}[a.cmd]
    if len(a.args) != len(shape.split()):                  # bad input is exit 2, never a FAIL
        ap.error(f"{a.cmd} takes {shape}; got: {' '.join(a.args)}")
    if a.cmd == "gate" and a.args[0] not in STAGES:
        ap.error(f"no stage {a.args[0]!r}; the stages are {' '.join(STAGES)}")
    if not Path(SK).is_dir():
        ap.error(f"no {SK} in {Path.cwd()}: run it from the project root")
    if (a.cmd, a.args[0]) != ("gate", "truth") and not Path(a.args[-1], "spec.md").is_file():
        ap.error(f"no {a.args[-1]}/spec.md: FEATURE_DIR is a feature directory such as specs/001-room-booking")
    if a.cmd == "status":
        f = a.args[0]
        rows = [gate(s, f)[::2] for s in STAGES[:5]]
        for s in (f"US{n}" for n in re.findall(r"^### User Story (\d+) ", Path(f, "spec.md").read_text(), re.M)):
            rows += [red_evidence(f, s), gate("story", f, s)[::2]]
        rows.append(gate("truth", f)[::2])
        failed = [line.split()[1] for ok, line in rows if not ok]
        print("\n".join(line for _, line in rows) + f"\n{len(rows)} gates, {len(failed)} failed"
              + (f"; next: {failed[0]}" if failed else ""))
        return 1 if failed else 0
    if a.cmd == "gate":
        name, f = a.args
        if name in ("red", "story") and not a.story:
            ap.error(f"the {name} gate needs --story USn")
        ok, shown, line = gate(name, f, a.story)
        print("\n".join(shown + [line]))
        if ok and name == "red":                           # the evidence a human reads, then commits with the tests
            Path(f, f"red-gate-{a.story.lower()}.txt").write_text("\n".join(shown + [line]) + "\n")
        return 0 if ok else 1
    task, f = a.args
    from scenario_task_map import marker_expr, read_tasks   # Ch 31: tasks.md and its Verify lines
    verify = read_tasks(Path(f, "tasks.md")).get(task, (0, 0, None))[2]
    if not verify:
        ap.error(f"{task} has no Verify line in {f}/tasks.md")
    ok, lines = run([PY, f"{SK}/bdd/scripts/bdd_check.py", "red", "-m", marker_expr(verify)])   # API channel
    print("\n".join(lines))
    if not ok:
        print(f"{task} not handed out: its scenarios must fail in a Then step before the change")
        return 1
    Path(".bdd/task").write_text(f"{task} {f}/tasks.md\n")
    print(f"{task} handed out: .bdd/task = {task} {f}/tasks.md")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as e:                                    # a file the command reads is missing: bad input, not a FAIL
        print(f"pipeline.py: cannot run {' '.join(sys.argv[1:])}: {e}", file=sys.stderr)
        sys.exit(2)
```

五個決定。**管線是資料**：一站一個 list，加一個檢查是加一行，`status` 和 hook 讀的是同一份。**每支閘門只判自己那一層**：red 站用 `-k` 只跑這個 story 的 TP 測試，交給 `red_gate.py` 一份裁過的計畫（`story_cases`）；情境那一層只確認 step 全部綁上（`--generate-missing` 沒印出未定義），「紅在 Then」留給每個 slice 開工時的 `start`。**story 站把跨層檢查放在一起**：同一組情境走兩個 driver、TP 測試對計畫逐條 met、測試和紅燈 tag 時一樣、頁面裡每個 `fetch()` 都有路由。**`start` 讓 Ch 31 模板那句「Run Verify before the change」第一次被程式檢查**；它用 API 通道判，因為 UI 通道等一個還不存在的按鈕要耗掉 Playwright 預設 30 秒（`30000` ms）的逾時。**輸入錯了是 exit 2，不是 FAIL**：參數個數、站名、不在專案根目錄、FEATURE_DIR 裡沒有 spec.md，都在跑任何檢查之前印一行原因，hook 與 CI 才分得出「檢查沒過」和「檢查沒跑」。少了 FEATURE_DIR 的 `pipeline.py gate red` 回 `pipeline.py: error: gate takes STAGE FEATURE_DIR; got: red`，資料夾打錯的 `status specs/001-room-bookin` 回 `pipeline.py: error: no specs/001-room-bookin/spec.md: FEATURE_DIR is a feature directory such as specs/001-room-booking`，兩者都是 exit 2；truth 站不讀 FEATURE_DIR，plan 封存之後照樣能跑。

課程腳本只動了一支：`bdd_check.py` 把 mode 之後的參數交給 pytest（`nargs=argparse.REMAINDER`，判準不變），節錄自 `diff -u`。參考解答收尾時另外修過 Ch 33 的 `truth_drift.py` 與 `archive_plan.sh` 各一處，那兩處已經併回 Ch 33（踩雷 6），照 Ch 33 現在的版本搬就好。

```diff
--- bdd_check.py（Ch 27）：mode 之後的參數交給 pytest
-def run_suite():
+def run_suite(extra=()):
-                              f"--junitxml={junit}", f"--cucumberjson={cuke}"],
+                              f"--junitxml={junit}", f"--cucumberjson={cuke}", *extra],
…（略：argparse 多一個 nargs=argparse.REMAINDER 的 pytest_args，main 把它交給 run_suite；docstring 改寫）
```

### phase、hook 與權限

```sh
#!/bin/sh
# phase.sh red|green FEATURE_DIR USn: a human switches both phase files at once (Ch 20 .tdd/phase, Ch 27 .bdd/phase)
set -eu
case "$#:${1:-}" in
  3:red)   word=RED;   bdd=steps ;;
  3:green) word=GREEN; bdd=implement ;;
  *) echo "usage: tools/phase.sh red|green FEATURE_DIR USn" >&2; exit 2 ;;
esac
[ -f "$2/spec.md" ] || { echo "tools/phase.sh: no $2/spec.md; FEATURE_DIR is a feature directory" >&2; exit 2; }
mkdir -p .tdd .bdd
printf '%s %s %s\n' "$word" "$2" "$3" > .tdd/phase
echo "$bdd" > .bdd/phase
rm -f .bdd/task                      # a slice is handed out again with tools/pipeline.py start
echo "phase: $(cat .tdd/phase); bdd: $bdd"
```

```python
"""red_stop.py: Stop hook for the RED phase. `.tdd/phase` = "RED <feature dir> <USn>": run that story's red gate and
block the stop while it fails (once; with stop_hook_active, report and let Claude stop). Any other phase: silent."""
import json, os, subprocess, sys
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
event = json.load(sys.stdin)
words = (root / ".tdd/phase").read_text().split() if (root / ".tdd/phase").exists() else []
if words[:1] != ["RED"] or len(words) != 3:
    sys.exit(0)
cmd = [sys.executable, "tools/pipeline.py", "gate", "red", words[1], "--story", words[2]]
run = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
if run.returncode:
    tail = (run.stdout + run.stderr).strip().splitlines()[-8:]          # stderr: pipeline.py's exit-2 message
    why = f"RED {words[2]} is not done: the red gate failed\n" + "\n".join(tail)
    print(json.dumps({"systemMessage": why} if event.get("stop_hook_active") else {"decision": "block", "reason": why}))
```

`.claude/settings.json` 的 hook 段落：PreToolUse（matcher `Edit|MultiEdit|Write|NotebookEdit`）接 `feature_guard.py` 與 `tdd_guard.py`，都是 exec form、`"onFailure": "block"`；PostToolUse（`Write|Edit`）接 `aaa_hook.py`；Stop 接 `red_stop.py` 與 `slice_stop.py`，`timeout` 120。直譯器一律寫 `${CLAUDE_PROJECT_DIR}/.venv/bin/python`（理由見 [Ch 27](./27-ai-bdd-loop.md) 第四節）。`permissions.deny` 四條：`Edit(/specs/archive/**)`、`Bash(git tag *)`、`Bash(*bdd_check.py approve*)`、`Bash(*phase.sh*)`，簽核、打 tag、換階段只歸人。四條裡只有 `Bash(git tag *)` 在實跑中真的擋過一次（第二次實跑，見下）；其餘三條從沒被觸發，本段未實測，驗證方式是在 `runs/` 的 clone 裡要 agent 跑 `tools/phase.sh green specs/001-room-booking US1`，看結果裡的 `permission_denials`。在 GREEN（`.tdd/phase` 為 `GREEN specs/001-room-booking US2`）用管線餵三個 Edit 事件，兩支 guard 的 `permissionDecisionReason`（空白 = 沒有輸出、不表態）：

| 目標 | feature_guard.py | tdd_guard.py |
|---|---|---|
| `features/booking.feature` | `bdd phase is implement: features/booking.feature is locked; the scenarios are approved; …` | （空白） |
| `tests/unit/test_booking_rules.py` | `… step definitions are frozen while implementing; …` | `tdd_guard: GREEN phase: … is a test file; tests are read-only after the red commit; …` |
| `booking/schedule.py` | （空白） | （空白） |

兩支缺一不可：`tdd_guard` 把 `.feature` 當原始碼，GREEN 時放行；`feature_guard` 不管 RED 時改 `booking/`。官方文件說多支 PreToolUse 的決定依 `deny > defer > ask > allow` 合併，任一支拒絕就拒絕。兩支 Stop hook 用管線測擋下的路：

```
$ echo '{"stop_hook_active": false}' | .venv/bin/python .claude/hooks/red_stop.py | jq -r .reason
RED US2 is not done: the red gate failed
…（略：TP-011..013 的三行證據）
tests.unit.test_booking_rules::test_tp008_the_person_who_booked_cancels_and_only_that_booking_goes: RG-2 TP-008 stops before its assertion: ImportError: cannot import name 'Cancellation' from 'booking.schedule' (/private; fix the test or the stub until it reaches its assert
6 tests, 5 red as planned, 1 problems
…（略：aaa_lint 與 step 綁定兩行）
FAIL red US2    6 tests, 5 red as planned, 1 problems
$ echo '{"stop_hook_active": false}' | .venv/bin/python .claude/hooks/slice_stop.py | jq -r .reason
T013 is not done: `.venv/bin/python -m pytest -q -m "sc_cancel_04 or sc_cancel_01" --driver both` exited 1
4 failed, 33 deselected in 31.15s
```

前一段在一份副本的 TP-008 函式裡加了 `from booking.schedule import Cancellation`；後一段是 T013 剛發出、還沒實作。PostToolUse 的 `aaa_hook.py` 也這樣測過：拿掉 TP-001 的兩個空白行再餵一個 Edit 事件，回 `block: aaa_lint: 1 files, 8 tests, 2 problems. Fix the test before anything else.`，兩則是 AAA-1 與 AAA-3。

### preset 與 tasks.md

`presets/booking-pipeline/templates/tasks-template.md`（61 行）的 Format 段與一個 story 的兩段：

```markdown
- RED task: `[ID] [USn] [spec.md#User Story N - Title (Priority: Pn)]`, its FRs, or the constitution's
  `[.specify/memory/constitution.md#II. Test-First (NON-NEGOTIABLE)]`; never a scenario, and never testplan.md,
  which the first RED task creates.
- Slice task: `[ID] [USn] [FR-###] [EP:<METHOD> <path>] [@sc_<feature>_<NN>] Description`, and the next line is
  `  - Verify: \`.venv/bin/python -m pytest -q -m "<tags joined by or>" --driver both\``
- Every `@sc_` tag in features/ has exactly one owner, and a slice owns scenarios of one feature file.
  A scenario that already passes, or only guards another's boundary, rides with that other scenario.
- A journey in features/journeys/ is owned by its story's checkpoint task: it proves the slices compose.
…（略：Setup、Foundational）
## Phase 3: User Story 1 - [Title] (Priority: P1) RED
- [ ] T003 [US1] [spec.md#User Story 1 - [Title] (Priority: P1)] Add the US1 cases to specs/[###-feature-name]/testplan.md with the testplan skill
- [ ] T004 [US1] [FR-001] One red test per US1 case in tests/unit/ or tests/api/; step definitions for features/[feature].feature in tests/acceptance/
- [ ] T005 [US1] [.specify/memory/constitution.md#II. Test-First (NON-NEGOTIABLE)] RED gate: `tools/pipeline.py gate red specs/[###-feature-name] --story US1` prints 0 problems; a human commits, tags red-[###]-us1 and runs `tools/phase.sh green`
## Phase 4: User Story 1 - [Title] (Priority: P1) GREEN
**Feature file**: `features/[feature].feature` | **Story check**: `tools/pipeline.py gate story specs/[###-feature-name] --story US1`
- [ ] T006 [US1] [FR-001] [EP:POST /bookings] [@sc_[feature]_01] [Behaviour the scenario adds] in [file]
  - Verify: `.venv/bin/python -m pytest -q -m "sc_[feature]_01" --driver both`
…（略：T007 checkpoint、Polish 與 Dependencies）
```

（節錄時拿掉了空白行。）RED gate 任務裡的 `tools/phase.sh green`（模板的 T005，黃金 tasks.md 的 T005 與 T012）是簡寫，照字面跑，phase.sh 回 `usage: tools/phase.sh red|green FEATURE_DIR USn`、exit 2，兩個 phase 檔不會被動到（資料夾打錯也一樣，回 `tools/phase.sh: no specs/001-room-bookin/spec.md; FEATURE_DIR is a feature directory`）；你的模板請寫完整的 `tools/phase.sh green specs/[###-feature-name] US1`。黃金 tasks.md 有 16 個 task：US1 的 slice 是 T006（`@sc_book_01`、`@sc_book_05`）、T007（`@sc_book_03` 帶著守門的 `@sc_book_02`）、T008（`@sc_book_04`）；US2 是 T013（`@sc_cancel_04`、`@sc_cancel_01`）、T014（`@sc_cancel_02`、`@sc_cancel_03`），journey 由 checkpoint T015 擁有。`pipeline.py gate tasks` 的兩行是 `10 scenarios, 6 owning tasks, 0 problems` 與 `16 tasks, 25 sources, 0 problems`。

### 實跑：一個 story 從紅到 checkpoint

US1 的紅燈閘門跑在 Foundational 的樁上：`Schedule.book` 回傳 id 為 0 的 `Booking`、`bookings()` 回空串列，路由照合約回應但不擋任何規則。

```
$ .venv/bin/python tools/pipeline.py gate red specs/001-room-booking --story US1
7 failed in 0.12s
TP-001 red: AssertionError: assert Booking(id=0,...), by='Alice') == Booking(id=1,
…（略：TP-002..006）
TP-007 red: assert (201, '{"id":...by":"Alice"}') == (409, '{"deta...:00 by Bob"}'
7 tests, 7 red as planned, 0 problems
2 files, 7 tests, 0 problems
1 feature file, 0 undefined steps
PASS red US1    4 checks
$ .venv/bin/python tools/pipeline.py start T006 specs/001-room-booking
pass      @sc_book_01
RED-right @sc_book_05  Then the agenda of "Taipei" is: AssertionError: assert [] == ['10:00-11:00...-12:00 Alice']
2 scenarios: 1 passed, 1 red for the right reason, 0 red for the wrong reason; expect red: met
T006 handed out: .bdd/task = T006 specs/001-room-booking/tasks.md
```

`@sc_book_01` 在樁上就綠，所以它跟著 `@sc_book_05` 走（Ch 31 的守門規則）。US2 的 checkpoint：

```
$ .venv/bin/python tools/pipeline.py gate story specs/001-room-booking --story US2
10 passed, 14 deselected in 1.12s
6 passed, 7 deselected in 0.12s
@sc_cancel_01 met: TP-008 TP-011
…（略：@sc_cancel_02..04 與 @sc_journey_01）
FR-005 met: TP-008 TP-011
FR-006 met: TP-009 TP-010
FR-007 met: TP-012 TP-013
R1 met: TP-012 TP-013
R4 met: TP-008 TP-009 TP-010 TP-011 TP-013
6 tests, 6 green as planned, 0 problems
12 files, 0 problems
5 routes, 3 calls, 0 problems, 0 warnings
PASS story US2  5 checks
```

journey 的 checkpoint T015 不能用 `start` 發：`1 scenarios: 1 passed, 0 red for the right reason, 0 red for the wrong reason; expect red: NOT met`，接著 `T015 not handed out: its scenarios must fail in a Then step before the change`。它本來就該在所有 slice 之後直接變綠，直接跑它的 Verify（`2 passed, 35 deselected`）。收尾：`contracts/` 複製到根目錄後 `truth_drift.py truth` 回 `10 scenarios, 3 endpoints, 4 terms, 0 problems`；`archive_plan.sh 001-room-booking 2026-11-03` 回 `archived specs/001-room-booking -> specs/archive/2026-11-03-001-room-booking`，`plans` 回 `0 active plans, 1 archived, 0 problems`；`--driver both` 全套 `37 passed in 2.37s`（24 個情境測試加 13 個 TP 測試），`living_doc.py` 回 `24 scenarios, 0 failed`，每個情境以 `[api]`、`[ui]` 各列一次。`status` 整條跑一次 4.95 秒。

### 三次 `claude -p`

> 以下為實際執行紀錄（節錄），每次執行結果不同；2026-11-02（UTC），Claude Code 2.1.295，claude-opus-5-5。

三次都在 `runs/` 的 clone 裡，`run.sh` 先 activate 課程 venv、設 `SPECKIT_PYTHON_EXECUTABLE`，旗標相同：`--max-turns 20 --max-budget-usd 3 --tools "Read,Write,Edit,Glob,Grep,Bash,Skill"`、`--allowedTools` 只放行檔案工具、Skill 與 `Bash(.venv/bin/python *)`（第二次加 `check-prerequisites.sh`，第三次加 `setup-tasks.sh`）、`--permission-mode acceptEdits --permission-prompts none --setting-sources project,local --strict-mcp-config --no-session-persistence`，stdin 接 `/dev/null`。

| 次 | 角色與提示 | 結果 | 工具呼叫／費用／時間 |
|---|---|---|---|
| 1 | test-writer：做 US2 的 T010、T011，直到 `gate red --story US2` 印 PASS | PASS；6 個 TP 案例、cancel 的 step 與兩個 driver | 59／US$1.20／248 s |
| 2 | `/speckit-implement T013 only (GREEN phase: …)` | T013 的 Verify `4 passed, 33 deselected`；3 次被拒 | 41／US$0.55／76 s |
| 3 | `/bdd-plan specs/001-room-booking`，preset 1.0.0 | scenario_task_map 0；task_coverage 8 個 TC-05 | 35／US$0.99／193 s |

**第一次**自己撞上踩雷 5，並換了一條路：「At unit level the arrange or act raises `Conflict`, which the gate counts as stopping before the assertion.」它把三個需要「取消之後」狀態的案例改成 API 層，讓缺的行為以狀態碼出現在斷言裡（`assert (204, '') == (403, …)`），也老實寫下 TP-010 是「weaker red」。它讓 `the booking is cancelled` 同時檢查 agenda：「Checking the response alone would have passed against the stub.」代價是 DSL 多了一個 `last_cancel` 狀態，審查時要問值不值得。它還用 `.venv/bin/python - <<'EOF'` 在 repo 外的暫存目錄寫了一份 cancel 實作，證明測試能變綠後刪掉：guard 只看得到專案裡的 Edit 與 Write，被放行的直譯器什麼都寫得了。**第二次**照 slice 只改 `booking/schedule.py` 與頁面，另外在 tasks.md 打勾，並自己點出這和 CLAUDE.md 的「GREEN 只改 `booking/`」不一致；它還找到我們 `pipeline.py` 的 bug：「`tools/pipeline.py:108` needs the last line of the red-gate file to end in ` 0 problems`, but both committed files end in `0 undefined steps`.」那時的 `red_evidence` 確實寫錯，上面是修正後的版本。`git tag --list` 被 `Bash(git tag *)` 擋下，`git rev-parse` 因為沒人能核准也被拒。**第三次**跟著模板排出兩段式 tasks，沒照 bdd-plan SKILL.md 當時寫的「One phase per user story」；task_coverage 的 8 個問題全是 `TC-05 … cites testplan.md#Cases, but testplan.md does not exist`。錯在我們的模板：黃金 tasks.md 早就改用 spec、FR 與 constitution 當引用，模板沒跟上（Ch 14 進階一節說過，慣例改了要先改黃金產物，模板與檢查器一起跟著改）。它的回報是「I kept the template's citation and did not create `testplan.md` or swap in a different one.」把那 8 行照 1.0.1 的模板改寫（我們的修改，不是 agent 的），兩支檢查是 `17 tasks, 25 sources, 0 problems` 與 `10 scenarios, 7 owning tasks, 0 problems`。之後一個 commit 把 bdd-plan 的 SKILL.md 第 4 步改成兩段式（「Two phases per user story ... as the template shows」）、第 5 步的閘門改成 `pipeline.py gate tasks`，並用 `specify preset update booking-pipeline --dev ../presets/booking-pipeline` 換上 1.0.1（`✓ Preset 'Booking pipeline' v1.0.1 installed (priority 10)`），也就是 Step 4 最後那句要你先做的事。三次的兩支 Stop hook 都跑了、都沒表態：前兩次 agent 收工前自己把檢查跑綠了（RED 閘門 PASS、T013 的 Verify 綠），第三次的 clone 沒有 phase 檔，兩支本來就不表態。擋下的那條路只用管線測過。

**解答說明**：每一個 PASS 背後都有一個人看過的地方：map 的答案、approve、紅燈證據、tag、`start`。腳本負責「有沒有接上」，人負責「接得對不對」；agent 三次都在腳本與人之間的縫裡找到東西，其中兩個是我們自己的 bug。

</details>

## 底層機制：三種「做完」、兩個 phase 檔、一條 ID

「做完」有三種，各有自己的指令、擁有者與頻率。**slice 做完**是它那一行 Verify 綠，agent 每次收工都由 `slice_stop.py` 檢查；**story 做完**是 `gate story` PASS，由人或 CI 在最後一個 slice 之後跑；**plan 做完**是 truth 對上程式、plan 進了 archive，發生在合併的那個 PR。混用就出事：整個套件要到最後一個 slice 才綠，拿它當 agent 的收工條件，每個中途的 slice 都過不了（踩雷 3）。

phase 有兩個檔，因為 guard 有兩支：`tdd_guard.py` 讀 `.tdd/phase` 的第一個字（RED、GREEN），`feature_guard.py` 讀 `.bdd/phase`（steps、implement）。`phase.sh` 同時寫兩個，並把 `.tdd/phase` 後兩個字留給 `red_stop.py`（哪個 feature、哪個 story），我們沒有改兩支 guard 的任何一行。官方文件寫明「When an event fires, its matching hooks run in parallel」，也寫了 PreToolUse 的合併順序，卻沒寫多支 Stop 的決定怎麼合併；所以兩支 Stop hook 設計成互斥：`red_stop.py` 只在 RED 表態，`slice_stop.py` 只在 `.bdd/task` 存在時表態，而 `phase.sh` 每次換階段都刪掉 `.bdd/task`。一條 ID 走過整條線，每個箭頭都有一支腳本：

```
 maps/cancel.yaml          - id: sc_cancel_03                     map_trace：map 上的 id 都成了 tag
 spec.md 的 US2 區塊        @sc_cancel_03  Example: …              extract_features --check：spec 與 feature 一字不差
 features/cancellation     @sc_cancel_03 → pytest marker           bdd_check approve：hash 進 .bdd/approved.json
 tasks.md                  T014 … [@sc_cancel_03] -m "…"           scenario_task_map：恰好一個主人，Verify 恰好選中它
 testplan.md               TP-010 trace: R4, FR-006, @sc_cancel_03 testplan_lint：每個 @sc_ 都有案例或列在範圍外
 red-gate-us2.txt          TP-010 red: assert (204, '') == (403,…  red_gate --plan（裁過的 US2 計畫）
 start T014                RED-right @sc_cancel_03 …               bdd_check red -m：開工前紅在 Then
 gate story                @sc_cancel_03 met: TP-010               red_gate --expect green、--driver both
 living-doc.md             [PASS] … [api] @sc_cancel_03            living_doc：給 PM 讀的那一頁
```

## 測試用例

每一列都在 `demo/` 的副本裡實際改壞、實際跑過：

| 改壞的地方 | 誰抓到 | 真實輸出（節錄） |
|---|---|---|
| spec 與 feature 一起刪掉 `sc_cancel_03`，重新簽核 | discover | `maps/cancel.yaml: sc_cancel_03 is on the map but in no feature file`（formulate 是 `PASS formulate  4 checks`） |
| 簽核後只刪 feature 裡的情境 | formulate（discover 的 map_trace 也報） | `features/cancellation.feature: changed since approval; restore it or ask a human to re-approve` |
| RED 時測試函式裡 import 不存在的名字 | red（`red_stop.py` 擋收工） | `RG-2 TP-008 stops before its assertion: ImportError: cannot import name 'Cancellation' …` |
| 想用 `start` 發一個已經綠的情境 | start | `T015 not handed out: its scenarios must fail in a Then step before the change` |
| GREEN 時把 TP-009 的訊息斷言拿掉 | story | `TAMPER-2 tests/unit/test_booking_rules.py::test_tp009_…: check gone or changed: …`，`red_gate` 仍是 `6 green as planned` |
| 頁面打錯取消的路由 | story | `ERROR index.html:22 calls DELETE /bookings/{}/cancel: no backend route`，`[ui]` 4 failed、API 全過 |
| 新增 `GET /rooms` 卻沒寫進 contracts | truth | `booking.api:app: TD-4 the app serves GET /rooms; add an EP: line to contracts/ or remove the route` |
| GREEN 時改 `tests/` 或 `features/` | guard | 上面那張表的拒絕理由 |

## 對比與取捨

| 做法 | 誰判「做完」 | 測試何時寫 | 跨層檢查 | 代價 |
|---|---|---|---|---|
| [SDD final project](../spec_driven_development/final-project-ship-with-sdd.md) | 驗收矩陣與復盤 | 階段 5，在實作 pipeline 之後 | 由你設計 | 約兩週（階段 0–6 排到第 14 天） |
| stock Spec Kit 1.2.5（`/speckit-tasks`、`/speckit-implement`） | agent 自評；Ch 31 實測 S 的 tasks：`6 scenarios, 0 owning tasks, 6 problems` | 選配；Ch 25 的基準線 agent 自己先寫 27 個測試，事後判定 `27 tests, 23 red as planned, 4 problems` | 無 | 兩個指令 |
| [Ch 29](./29-example-mapping.md) 的試點 | `bdd_check.py` 的 red、green 各判一次全套 | 情境先寫 | 無（只有後端） | 一張 story |
| 本專案 | 三層：slice 的 Verify、story gate、truth | 每個 story 先紅；每個 slice 開工前再確認一次 | 兩個 driver、`inventory.py scan`、`truth_drift.py` | 22 個 commit、五支 hook、169 行的 `pipeline.py`、HTTP 與頁面兩份合約加上 feature 檔要維護 |

stock 那一列的數字出自前面章節的實跑，本專案沒有再跑一次 stock 流程。這套做法換來的不是「agent 寫得更好」：agent 在 stock 設定下也會先寫測試（Ch 25 的基準線就是這樣）。換來的是每一站都留下事後查得到的證據，而且錯會在交棒的那一站被擋下，不會流到下一站才被發現。不適合的情況：一次性的腳本、還在探索的 spike，以及沒有 PM 或 QA 會讀 feature 檔的團隊（那時 Ch 25 的 TDD 流水線就夠了）。

## 踩雷集錦

1. **錯誤直覺：兩個 preset 都裝上，Spec Kit 會合併它們的 tasks-template → 正確認識：** `replaces` 只留最上層。實測兩個都是 `priority 10`，`specify preset list` 說 `Ties are broken by preset id (alphabetical)`，`resolve` 回 `(top layer from: booking-bdd v0.2.0)`，booking-tdd 模板裡寫著 `RED phase` 的 4 行（三個 story 的 RED 段標題，加一行並行範例的註解）從 `/speckit-tasks` 看得到的模板裡消失，沒有任何警告。要合成一份。
2. **錯誤直覺：同一次 pytest 給兩支閘門判，各取所需 → 正確認識：** US1 紅燈時，`bdd_check.py red` 自己跑一次全套，把 7 個紅得對的 TP 測試全標 RED-wrong（它們沒有 Then），回 `19 scenarios: 2 passed, 6 red for the right reason, 11 red for the wrong reason; expect red: NOT met`；同一套測試用 `pytest -q --junitxml=.tdd/whole.xml` 再跑一次、把報告交給 `red_gate.py --plan`，情境變成 10 則 RG-3，回 `19 tests, 7 red as planned, 10 problems`。每支只判自己那一層：`bdd_check.py` 加了參數，`pipeline.py` 分開跑。
3. **錯誤直覺：把各章的 Stop hook 都接上，收工檢查越多越安全 → 正確認識：** T013 做完、T014 還沒開始時餵同一個 Stop 事件，`slice_stop.py` 放行，`bdd_stop.py` 回 `block: 25 scenarios: 21 passed, 2 red for the right reason, 2 red for the wrong reason; expect green: NOT met`，`require_green.py` 回 `block: 4 failed, 21 passed in 0.33s`。後者刻意不看 `stop_hook_active`，每次收工都擋；agent 若不呼叫工具就連續收工，要到官方文件說的 8 次連續續跑上限（8-consecutive-continuation cap）才會被強制結束。收工條件要跟工作單位一樣大。
4. **錯誤直覺：RED 任務和 slice 放同一個 story 段就好 → 正確認識：** Ch 16 的 TC-07 規定綁情境的 task 排在段首，放在一起就是 `16 tasks, 25 sources, 6 problems`。拆成兩個 `##` 段。模板也要跟著黃金產物走：第三次實跑照 1.0.0 模板引用 `testplan.md#Cases`，得到 8 個 TC-05，因為 testplan.md 是第一個 RED 任務才建的。
5. **錯誤直覺：批次紅燈對每個案例都成立 → 正確認識：** Arrange 需要同一批還沒實作的行為時（先取消再訂），樁上的 `cancel` 什麼都沒做，下一個 `book` 在走到斷言之前就丟 `Conflict`（落在 Arrange 或 Act，看案例怎麼寫），閘門判 RG-2；`@sc_cancel_03` 的 Given 也站不起來。我們把 TP-012 改寫在 unit 層重跑一次，得到 `RG-2 TP-012 stops before its assertion: booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob`。內圈的案例要寫成獨立的（第一次實跑改到 API 層，缺的行為變成斷言裡的狀態碼），外圈靠 slice 的順序：T013 做完之後，`start T014` 才回 `RED-right @sc_cancel_03`。
6. **錯誤直覺：各章的腳本在自己的實驗室跑過，搬過來就對 → 正確認識：** 它們沒見過這裡的輸入。參考解答收尾時，當時版本的 Ch 33 腳本撞了兩次。用 `scenarios()` 綁整個目錄時，pytest-bdd 9.0.1 的 cucumber `uri` 是「檔案所在目錄名／檔名」（`parser.py` 第 373 行），`features/journeys/` 的 journey 變成 `journeys/free_slot_journey.feature`，`truth_drift.py` 卻用根目錄相對路徑比對，報出 `TD-2 "A cancelled slot goes to the next person" never ran`；第一次封存時還沒有 `specs/archive/`，`archive_plan.sh` 的 `git mv` 回 `fatal: renaming 'specs/001-room-booking' failed: No such file or directory`。Ch 33 當時的實驗只有一層 `features/`，歷史也先手動建好了 `specs/archive/`，兩個洞在那裡碰不到。兩處各改一兩行、判準不變，修正已併回 [Ch 33](./33-truth-vs-plan.md) 第四、五節；教訓留著：搬進來的每支腳本，先用這個專案的真實輸入各跑一次，再接上 hook 或 CI。

## 進階：兩個 driver 抓得到同一批 bug 嗎

兩個 driver 都綠，只證明它們對正確的程式意見一致。要知道它們對錯的程式是否也一致，用 [Ch 32](./32-brownfield-bdd.md) 的 `mutate.py`：五個手寫突變體，`M4` 改頁面，其餘四個改 `booking/schedule.py`。

```yaml
# mutants.yaml: hand-written bugs for tools/mutate.py (Ch 32). Each `find` occurs exactly once.
- {id: M1, file: booking/schedule.py, what: closed intervals (R1), find: "start < held.end and held.start < end", replace: "start <= held.end and held.start <= end"}
- {id: M2, file: booking/schedule.py, what: anyone can cancel (R4), find: "if self._active[booking_id].by != by:", replace: "if False:"}
- {id: M3, file: booking/schedule.py, what: a cancel keeps the booking, find: "del self._active[booking_id]", replace: "pass"}
- {id: M4, file: booking/static/index.html, what: the agenda shows one booking, find: "rows.map(item)", replace: "rows.slice(0, 1).map(item)"}
- {id: M5, file: booking/schedule.py, what: a booking may end when it starts (R2), find: "if end <= start:", replace: "if end < start:"}
```

```
$ .venv/bin/python tools/mutate.py mutants.yaml --before "tests/acceptance --driver api" --after "tests/acceptance --driver ui"
mutant  before    after     what it breaks
-       green     green     no mutant (control)
M1      killed    killed    closed intervals (R1)
M2      killed    killed    anyone can cancel (R4)
M3      killed    killed    a cancel keeps the booking
M4      survived  killed    the agenda shows one booking
M5      survived  survived  a booking may end when it starts (R2)
5 mutants: before kills 3, after kills 4; lost -; gained M4; both missed M5
$ .venv/bin/python tools/mutate.py mutants.yaml --before "tests/unit tests/api" --after "tests/acceptance --driver both"
…（略：表頭與 M1..M3，兩邊都殺死）
M4      survived  killed    the agenda shows one booking
M5      killed    survived  a booking may end when it starts (R2)
5 mutants: before kills 4, after kills 4; lost M5; gained M4; both missed -
```

UI 通道多抓到頁面的 bug（M4），API 通道看不到它；兩個通道都漏掉「結束等於開始」（M5），因為 `@sc_book_04` 用的是 11:00 到 10:00，只有 TP-005 測了 10:00 到 10:00。內圈與外圈互補，兩層都不能刪。Ham Vocke 的說法是「If a higher-level test spots an error and there's no lower-level test failing, you need to write a lower-level test」：M4 正是這種情況，照理該補一個低層測試，但頁面的 JavaScript 在這個專案沒有低層測試，UI 通道是它唯一的網；M5 反過來，低層抓到、高層沒抓到，照他的「Avoid Test Duplication」不必在高層補情境。另外兩個方向：把每一站寫成 [Ch 25](./25-tdd-in-sdd-pipeline.md) 第六節那種 Spec Kit workflow 的 shell 步驟（`run: … tools/pipeline.py gate tasks specs/001-room-booking`），順序就由 Spec Kit 的引擎保證（本段未實測，驗證方式是照 Ch 25 用假 claude 跑一次 `specify workflow run`）；或者在 CI 上 checkout red tag 重跑 `gate red`，讓紅燈證據不只是一份 commit 進去的文字檔（本段未實測）。

## 延伸挑戰（加分）

- **第二圈：R3 營業時間**。book map 的 Q2 把它推給下一個改動。開 `002-office-hours`（001 封存後，`create-new-feature.sh` 只看 `specs/` 第一層，不加 `--number 2` 會再給一次 `"FEATURE_NUM":"001"`，連 `red-001-us1` 這個 tag 也會撞；見 Ch 33 踩雷 4）：map、spec、tasks、testplan（從 TP-014 接著編號，Ch 25 說的撞號問題）、紅燈、slice、checkpoint、封存。第二圈哪幾站變便宜了？`truth_drift.py plans` 在 001 已封存時說什麼？
- **讓 `start` 認得 checkpoint**：journey 的 T015 被拒是預期的。改成讀 tasks.md 的任務描述，對 checkpoint 直接跑 Verify，並用第三次實跑那份 tasks.md 的 T016 驗證。
- **拿掉 DSL 的 `last_cancel`**：把 `Then the booking is cancelled` 改成帶時段的句型、重新簽核，比較 lint、step 與第一次實跑那一版的取捨。

## 自我檢核

- [ ] 不看本章，說出八站各由哪支腳本把關、哪幾站一定要有人，以及「三種做完」各由誰在什麼時候判。
- [ ] 為什麼 `bdd_check.py` 與 `red_gate.py` 不能判同一次 pytest？各舉一則它們互相誤判的真實輸出。
- [ ] 兩支 Stop hook 為什麼要設計成互斥？如果改成同時接 `require_green.py`，T013 做完的那一刻會發生什麼？
- [ ] 一個 TP 案例還沒走到斷言就丟 `Conflict`：它紅得對嗎？第一次實跑怎麼處理？外圈的同一個問題又靠什麼解決？
- [ ] 第三次實跑的 TC-05 是誰的錯？從 artifact-first 的角度，哪一步應該先抓到它？
- [ ] 突變體 M4 與 M5 各說明了哪一層測試的哪個盲點？

## 延伸閱讀

- **[Claude Code — Hooks reference](https://code.claude.com/docs/en/hooks)**（官方文件，2026-11-03 讀取）
  - **讀哪裡**：「Hook handler fields」（matching hooks run in parallel）、「PreToolUse decision control」（`deny > defer > ask > allow`）、「Stop input」（`stop_hook_active` 與 8 次連續續跑的上限）。
  - **學到什麼、和本章的關聯**：多支 hook 同時接上時誰說了算。PreToolUse 有寫，Stop 沒寫，所以本專案讓兩支 Stop hook 互斥。前提：讀過 [harness Ch 30](../harness_engineering/30-hooks.md) 與 Ch 27 第四節。
- **[Spec Kit — Presets reference](https://github.github.io/spec-kit/reference/presets.html)**（官方文件）
  - **讀哪裡**：「File Resolution」、「Set Preset Priority」、「Resolve a File」，以及「List Installed Presets」裡同分怎麼排的那一句。
  - **學到什麼、和本章的關聯**：解析順序是 overrides、presets、extensions、core；數字小的優先，同分依 preset id 字母序；`replace` 是預設策略，第一個符合的整份勝出。踩雷 1 的三行輸出全由這幾段解釋。前提：[Ch 17](./17-constitution-and-presets.md)。
- **[Ham Vocke — The Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)**（martinfowler.com，2018-02-26）
  - **讀哪裡**：「Acceptance Tests — Do Your Features Work Correctly?」與「Avoid Test Duplication」。
  - **學到什麼、和本章的關聯**：每一層測試抓不同的錯，能往下推就往下推（「Push your tests as far down the test pyramid as you can」）。進階一節的 M4 與 M5 是這個主張的一個小實驗：外圈與內圈各抓一個對方抓不到的突變體。
- **[pytest-bdd 9.0.1 — src/pytest_bdd/parser.py](https://github.com/pytest-dev/pytest-bdd/blob/9.0.1/src/pytest_bdd/parser.py)**（原始碼）
  - **讀哪裡**：`FeatureParser.__init__` 的 `self.rel_filename = os.path.join(os.path.basename(basedir), filename)`，再到 `cucumber_json.py` 看它怎麼變成 `uri`。
  - **學到什麼、和本章的關聯**：文件沒寫的 `uri` 規則，正是踩雷 6 的第一個誤報。工具接工具時，讀被依賴那一方的原始碼比讀文件可靠。
- **[Goran Petrović、Marko Ivanković — State of Mutation Testing at Google](https://research.google/pubs/state-of-mutation-testing-at-google/)**（ICSE-SEIP 2018）
  - **讀哪裡**：摘要，以及他們為什麼把突變結果放進 code review、怎麼考量它佔用開發者的注意力。
  - **學到什麼、和本章的關聯**：本專案的 `mutants.yaml` 只有五個手寫突變體，是人挑的樣本；這篇說明在大型 codebase 裡怎麼以 diff 為單位、機率式地產生並呈現突變體。把突變結果放進 PR 審查，是進階一節最自然的下一步。

這條線從一張 map 走到一份活文件，中間每一次交棒都有一行指令可以重跑。它不保證你做對了：M5 兩個 driver 都沒抓到，`assert False` 也騙得過紅燈閘門（Ch 25）。它保證的是錯多半在交棒的那一站就被看見，看不見的那些，你知道該去哪一層補。下一個改動進來時，從 truth 讀起，再走一次。
