# Ch 27 — AI x BDD 迴圈與 bdd skill：先寫可執行規格，再寫 step definitions

> **目標**：親手跑一次 BDD 迴圈：feature → 執行 → 未定義的步驟 → `--generate-missing` → step definitions → 紅、而且紅得對 → 實作 → 綠 → cucumber JSON。過程中看懂 pytest-bdd 怎麼把一行 Gherkin 接到一個 Python 函式。接著把迴圈打包成 `bdd` skill，配一支判斷「紅得對不對」的檢查器和兩個 hook：PreToolUse 在簽核後鎖住 `features/`，Stop 在檢查沒過時不准收工。最後讓 Claude Code 在這套約束下實跑三次。

> **環境**：Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（parse 1.22.4）、FastAPI 0.162.0、Claude Code 2.1.295（claude-opus-5-5）、macOS 26.6。lab 從空目錄 `/tmp/lab/aibdd/ch27-bdd-loop/walk/` 開始，輸出都是 2026-11-02 實跑的結果。

## 為什麼需要這個？

[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的「工具鏈：從 .feature 到可執行測試」示範了 `scenarios()` 加 `target_fixture` 的綁定；[SDD Final](../spec_driven_development/final-project-ship-with-sdd.md) 的「階段 5：驗收」要求每個 Scenario 都有一支 pytest-bdd 測試，骨架裡的步驟本體卻大多只有 `...`。兩處教的都是人怎麼寫，沒有一處讓 coding agent 跑這個迴圈。agent 跑 BDD 時，有三條比做對更便宜的路：把 feature 改成符合現在的行為、把 step 的斷言放鬆、還紅著就宣布完成。人有 code review 和團隊默契擋著，agent 只有你給它的機制。迴圈本身也有幾處會騙人：`--generate-missing` 永遠回 exit 0，它產生的 stub 一個例子一支，cucumber JSON 會漏掉未定義的步驟，紅燈也可能紅在 import 錯誤，而不是紅在缺少的行為上。[pstack Ch 8](../pstack/08-bug-fix-playbook.md) 的「`tdd` 技能的護欄」要求新測試「for the intended reason」失敗、不准為配合錯的實作改測試；[pstack Ch 3](../pstack/03-run-lifecycle.md) 的「階段 5：先建驗證，再委派」要求先讓檢查存在、看它失敗，再交出實作。本章把這兩條做成 BDD 版，交給 hook 執行，不靠 agent 記得；BDD 怎麼從 TDD 長出來，見 [SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md)。

## 先建立直覺

想像一份裝潢合約。業主逐條簽名的規格書（feature 檔）鎖進抽屜；工班拿到驗收程序（step definitions），寫著每一條怎麼量、怎麼試。開工後工班只能改施工，不能改合約；收工前驗收員照程序試，一條不過就不准下班。

```
 phase spec              phase steps                       phase implement
 寫 .feature ──人 approve──▶ 綁定、列出缺的 step、寫 step defs ──人切換──▶ 改 app、變綠、留 cucumber JSON
 可寫 features/            可寫 tests/                        可寫 app 程式
                          features/ 要等於簽核時的 hash        features/、tests/ 都鎖住
                          收工條件：bdd_check.py red           收工條件：bdd_check.py green
```

每個階段只該寫一個地方，hook 實際上鎖三處：spec 以外的 `features/`、implement 的 `tests/`、永遠的 `.bdd/`；app 程式不鎖，靠 skill 的指引與檢查器。PreToolUse hook 是抽屜的鎖，Stop hook 是驗收員，`.bdd/approved.json` 裡的 hash 是業主的簽名。比喻有一處不成立：裝潢只驗收一次，feature 檔每次改動都要整份重跑，正因為一直重跑，它才值得寫成可執行的規格。Gherkin 關鍵字還不熟就先讀 SDD Ch 10；BDD 與 TDD 在產物上的差別見 [Ch 26](./26-bdd-vs-tdd-artifacts.md)。

## 一、不用 agent，手動跑一次迴圈

被測系統取自 [Ch 0](./00-environment-setup.md) 冒煙測試的 `app.py`，刪掉 R1 那三行重疊檢查，假裝 R1 還沒做：

```bash
mkdir -p /tmp/lab/aibdd/ch27-bdd-loop/walk/{features,tests} && cd /tmp/lab/aibdd/ch27-bdd-loop/walk
cp /tmp/lab/aibdd/ch00-setup/smoke/app.py . && sed -i '' '/for x in BOOKINGS:/,/raise HTTPException(409/d' app.py
ln -s /tmp/lab/aibdd/.venv .venv && source /tmp/lab/aibdd/.venv/bin/activate
```

`.venv` 這個連結讓第四節的 hook 用專案相對路徑找直譯器。再建兩個檔：`tests/conftest.py` 給每個情境一個清空過的 client；`pytest.ini` 註冊情境標籤（不註冊會冒 `PytestUnknownMarkWarning`，見 Ch 26），並濾掉 Starlette 1.10.0 在匯入 TestClient 時發的 `StarletteDeprecationWarning`（它建議改裝 httpx2，共用 venv 不加套件）：

```
==> tests/conftest.py <==
import pytest
from fastapi.testclient import TestClient

from app import app

@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/reset")
    return c
==> pytest.ini <==
[pytest]
testpaths = tests
pythonpath = .
markers =
    sc_book_01
    sc_book_02
    sc_book_03
filterwarnings =
    ignore:Using .httpx. with .starlette.testclient. is deprecated
```

spec 階段先寫 `features/booking.feature`。第三個情境是半開區間的邊界：10:00–11:00 和 11:00–12:00 不衝突。

```gherkin
Feature: Room booking

  Rule: R1 A room cannot be booked twice for overlapping times

    @sc_book_01
    Scenario: Booking a free slot
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 10:00 to 11:00
      Then the booking is confirmed

    @sc_book_02
    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

    @sc_book_03
    Scenario: Booking the slot right after another one
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed
```

### 1. 綁定，然後問它缺什麼

網路上常見的兩種跑法都不會動：`pytest --bdd features/booking.feature` 印出 `pytest: error: unrecognized arguments: --bdd`、exit 4；直接跑 `pytest` 則是 `collected 0 items`、`no tests ran in 0.00s`、exit 5。pytest-bdd 沒有 `--bdd`，也不會自己讀 `features/`：feature 要在某個 Python 測試模組裡被 `scenarios()` 或 `@scenario` 綁定，才會變成測試。第二個結果比較危險，它不是失敗，是「沒有測試」，agent 很容易讀成「沒有東西壞掉」。在 `tests/test_booking.py` 先只寫兩行：`from pytest_bdd import scenarios` 與 `scenarios("../features/booking.feature")`，路徑相對於測試檔所在的目錄。`pytest -q` 得到 `3 failed`、exit 1，每個情境都是 `StepDefinitionNotFoundError: Step definition is not found: Given "room "Taipei" is free all day"` 這一類訊息。exit code 和斷言失敗一模一樣，只看它分不出紅在缺的行為，還是紅在還沒寫的步驟。請 pytest-bdd 列出缺什麼：

```
$ pytest --generate-missing --feature features tests
…（略）
Step When "Alice books "Taipei" from 10:30 to 11:30" is not defined in the scenario "Booking an overlapping slot" in the feature "Room booking" in the file /private/tmp/lab/aibdd/ch27-bdd-loop/walk/features/booking.feature:14
…（略）
@when('Alice books "Taipei" from 10:00 to 11:00')
def _():
    """Alice books "Taipei" from 10:00 to 11:00."""
    raise NotImplementedError
…（略）
============================ no tests ran in 0.01s =============================
exit=0
```

三件事。第一，它不寫檔，只印到終端機。第二，它印的是文字，不是設計：七個未定義步驟印成七支 stub，其中三支 `@when` 只差時間，原樣貼上就是三支各認一個例子的 step。第三，缺東西照樣回 exit 0，不能當閘門。若一個情境都還沒綁，它會改印 `@scenario('features/booking.feature', ...)`，這個路徑從 feature 目錄名算起，貼進 `tests/` 會去找 `tests/features/booking.feature`，收集階段就炸掉（「三、把迴圈打包成 bdd skill」的檢查器輸出裡看得到這個錯）。

### 2. 寫 step definitions：紅，而且紅得對

把清單收斂成一種句型一支、帶參數的 step。完整的 `tests/test_booking.py`：

```python
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/booking.feature")

def book(client, who, room, start, end):
    r = client.post("/bookings", json={"room": room, "start": start, "end": end, "by": who})
    return r.status_code, r.json().get("detail")

@given(parsers.parse('room "{room}" is free all day'))
def room_is_free(client, room):
    assert [b for b in client.get("/bookings").json() if b["room"] == room] == []

@given(parsers.parse('{who} has booked "{room}" from {start} to {end}'))
def existing_booking(client, who, room, start, end):
    assert book(client, who, room, start, end)[0] == 201

@when(parsers.parse('{who} books "{room}" from {start} to {end}'), target_fixture="result")
def books(client, who, room, start, end):
    return book(client, who, room, start, end)

@then("the booking is confirmed")
def confirmed(result):
    assert result == (201, None), f"expected the booking to be confirmed, got {result}"

@then(parsers.parse('the booking is rejected because "{room}" is taken {slot} by {who}'))
def rejected(result, room, slot, who):
    want = (409, f"{room} is taken {slot} by {who}")
    assert result == want, f"expected {want}, got {result}"
```

Given 和 When 共用的是 `book()` 這個 helper，不是同一句話（同樣的翻譯到 [Ch 28](./28-gherkin-dsl-style.md) 收進 DSL，[Ch 30](./30-one-gherkin-two-drivers.md) 再拆成 API 與 UI 兩個 driver）。HTTP 狀態碼只出現在 step 裡，feature 維持業務語言。Given 斷言自己的佈置成功，否則佈置失敗會在 Then 變成看不懂的訊息。Then 只讀 `result`，不動系統。最常漏的是 `target_fixture="result"`：拿掉它，三個 Then 都報 `E       fixture 'result' not found`，因為 When 的回傳值沒有登記成 fixture。加回去再跑 `pytest -q --tb=short`，結果是 `1 failed, 2 passed in 0.04s`，唯一的失敗出在 `tests/test_booking.py:28: in rejected`：`E   AssertionError: expected (409, 'Taipei is taken 10:00-11:00 by Bob'), got (201, None)`。這才是紅得對：失敗在 Then，訊息說的正是缺的行為，要 409 卻拿到 201。另外兩個現在就綠，沒有問題，`@sc_book_03` 是邊界的守門員；如果實作前三個全綠，就代表 step 根本沒測到新行為。

### 3. 實作、變綠、留下活文件

先寫一個常見的錯版本，在 422 檢查之後用閉區間比較：

```python
    for x in BOOKINGS:
        if x["room"] == b.room and b.start <= x["end"] and x["start"] <= b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
```

`pytest -q --tb=line` 回 `1 failed, 2 passed`，失敗的是 `test_booking_the_slot_right_after_another_one`：`expected the booking to be confirmed, got (409, 'Taipei is taken 10:00-11:00 by Bob')`。邊界情境抓到了。兩個 `<=` 改成 `<`，`3 passed in 0.01s`。再用 `pytest -q --cucumberjson=reports/cucumber.json` 輸出 cucumber JSON（[Ch 26](./26-bdd-vs-tdd-artifacts.md) 用它做活文件）。第一次 `reports/` 還不存在：三個點都印出來了，最後卻是報表外掛的 `FileNotFoundError: [Errno 2] No such file or directory: '/private/tmp/lab/aibdd/ch27-bdd-loop/walk/reports/cucumber.json'`，exit 1。`mkdir reports` 後再跑，用這支 `cuke_summary.py`（放在 `walk/`）印出每一步的狀態：

```python
import json, sys
for feature in json.load(open(sys.argv[1])):
    for sc in feature["elements"]:
        tags = " ".join("@" + t["name"] for t in sc["tags"])
        print(f'{tags} {sc["name"]}: ' + ", ".join(f'{s["keyword"]} {s["result"]["status"]}' for s in sc["steps"]))
```

三個情境的 Given、When、Then 都是 passed。這份 JSON 不能當紅綠燈：[Ch 26](./26-bdd-vs-tdd-artifacts.md) 的「產物三：活文件」實測過，沒有定義的那一步和它後面的步驟不會出現在 JSON 裡，第一步就沒定義的情境整個消失，pytest 說紅、JSON 卻沒有任何一步 failed，拿它做的儀表板會顯示全綠（pytest-bdd 9.0.1 只寫 passed、failed、skipped，沒有 Cucumber 的 Undefined 與 Ambiguous）。所以第三節的檢查器以 pytest 的 junit XML 判斷紅綠，JSON 只拿來找紅在哪一步；原因在底層機制一節。

## 二、step 重用與參數型別

`{start}` 什麼字串都吃。假設新情境 `@sc_book_04`（`Given room "Taipei" is free all day`、`When Alice books "Taipei" from 9:00 to 10:00`、`Then the booking is confirmed`）把 09:00 寫成 `9:00`，錯字一路送到 API，變成 Then 裡的 422；第三節的檢查器（pytest.ini 加上 `sc_book_04`、重新 approve 後跑 `green`）把它標成 `RED-right @sc_book_04  Then the booking is confirmed: AssertionError: expected the booking to be confirmed, got (422, [{'type': 'time_parsing', 'loc': ['body', 'start'], 'msg': 'Input should be`（檢查器只截前 140 字），也被騙了。改用有型別的參數，錯字會在比對步驟時就停下來。改動如下：`hhmm` 和 `step` 要放在 import 之後、所有 step 之前，檔頭另加 `from datetime import time`（`parse` 是 pytest-bdd 的依賴，不用另裝）：

```python
from parse import with_pattern

@with_pattern(r"\d\d:\d\d")
def hhmm(text):
    return time.fromisoformat(text)

def step(sentence):
    return parsers.parse(sentence, extra_types={"HHMM": hhmm})

@when(step('{who} books "{room}" from {start:HHMM} to {end:HHMM}'), target_fixture="result")
def books(client, who, room, start, end):
    return book(client, who, room, f"{start:%H:%M}", f"{end:%H:%M}")
```

（Given 那一步同樣改用 `step(...)` 和 `{start:HHMM}`，本體也用 `f"{start:%H:%M}"` 轉回字串；只改句型的話，`time` 物件會原樣送進 JSON，用到它的兩個情境都變成 `TypeError: Object of type time is not JSON serializable` 的 RED-wrong。）同一個錯字現在是 `RED-wrong @sc_book_04  StepDefinitionNotFoundError: Step definition is not found: When "Alice books "Taipei" from 9:00 to 10:00"`，直接指到規格的那一行。陷阱在下一步：`--generate-missing` 會為錯字提議一支新 stub `@when('Alice books "Taipei" from 9:00 to 10:00')`，agent 照貼，就多一支只為錯字存在的 step。錯的是規格，要回頭問人。`converters={"start": time.fromisoformat, ...}` 也能轉型，但它在比對成功之後才轉：錯字先被這支 step 認領，執行時才丟 `ValueError: Invalid isoformat string: '9:00'`（標成 RED-wrong），擋不擋得住全看轉換函式寫得多嚴。重用還有一個更隱蔽的坑：團隊常把共用 step 放在 `conftest.py`。若有人把產生的字面 stub 貼進測試模組，兩支定義都比對得上 `Alice books "Taipei" from 10:30 to 11:30`，模組那支離測試近，默默蓋掉共用的那支。檢查器只看到 `RED-wrong @sc_book_02  When Alice books "Taipei" from 10:30 to 11:30: NotImplementedError`，沒有任何歧義警告（重現時要把 `book()` 和 When 搬進 `conftest.py`；兩支放在同一個模組，實測是參數化的那支贏）。

## 底層機制：一行 Given 怎麼找到它的函式

```
 收集期   scenarios(path)  解析 feature，每個 Scenario 生成 test_<情境名>，tag 轉成 marker
          @given/@when/@then(parser)  在模組登記 fixture：pytestbdd_stepdef_<type>_<pattern>
 執行期   每一步的文字 ─▶ 找出型別相符、parser 比對成功、這個測試看得到的 stepdef fixture
                     ─▶ 依定義位置排序，登記在同一個合成名字下，pytest 取最近的一支
                     ─▶ 解析參數 → converters → 其餘參數向 fixture 要 → 呼叫
                     ─▶ 有 target_fixture 就把回傳值注入成 fixture，後面的步驟拿得到
          一支都找不到 ─▶ StepDefinitionNotFoundError，在 before_step 之前丟出
```

「step definition 就是 fixture」可以直接看：`pytest --fixtures tests/test_booking.py | grep pytestbdd_stepdef` 列出 `pytestbdd_stepdef_when_{who} books "{room}" from {start} to {end}` 等五支，外加 pytest-bdd 內建的三支 `pytestbdd_stepdef_{given,when,then}_trace`（例如寫 `Given trace` 會進 pdb）。這張圖解釋了前面三個現象，也說明為什麼判斷紅得對不對要兩份資料拼起來：pass/fail 以 pytest 的 junit XML 為準，紅在哪一步從 cucumber JSON 取，取不到就當成紅錯原因。

- **共用 step 被蓋掉**：所有比對成功的定義照 pytest 的 fixture 覆蓋規則排序，最近的贏。pytest-bdd 9.0.1 不檢查歧義；Cucumber 遇到兩支都比對成功會報 Ambiguous（Java 版丟 `AmbiguousStepDefinitionsException`）。
- **cucumber JSON 漏步驟**：每一步的報告由 `before_step`、`after_step`、`step_error` 三個 hook 建立。找不到定義時，例外在 `before_step` 之前就丟出，那一步沒有報告；第一步就找不到，整個情境沒有任何一步，報表外掛直接跳過它。
- **`--generate-missing` 回 0**：原始碼在缺東西時設了 `session.exitstatus = 100`，但 pytest 9.2.0 的 `wrap_session` 用 `doit(config, session) or 0` 覆寫，產生器沒有回傳值，結果是 0。

## 三、把迴圈打包成 bdd skill

[Ch 7](./07-messy-baseline.md) 的 `bdd-monolith` 把整套做法塞在一個檔裡，[Ch 11](./11-skill-engineering-skill.md) 第一次把它拆成脊椎加模組（skill 格式本身見 [harness Ch 29](../harness_engineering/29-skills.md) 與 [AI 應用 Ch 15](../ai_applications/15-skills.md)）。最終版多兩樣：階段存在 `.bdd/phase`，hook 讀得到；「做完沒」由 Stop hook 跑檢查器判斷，不等 agent 自己去跑（Ch 11 那版的 done 行也是指令，跑不跑在 agent）。`.claude/skills/bdd/SKILL.md`：

```markdown
---
name: bdd
description: Run a behaviour change through the BDD loop in a pytest-bdd project - feature file first, human approval, step definitions, red for the right reason, implementation, green. Use when asked to add or change behaviour described in features/*.feature, to write step definitions, or to make a failing scenario pass; also when the user mentions Gherkin, Given/When/Then or pytest-bdd.
---

# BDD loop

The feature file is a contract a human approved. Everything after approval proves the code meets
it; nothing after approval changes it. Read the phase first: `cat .bdd/phase` (no file means spec).
Hooks enforce the phase: edits to locked paths are denied, and a turn cannot end while the phase's
check fails. When a hook blocks you, read its reason and act on it; never work around it.

| phase | you may edit | done when | then |
|---|---|---|---|
| spec | features/ | a human has read every scenario | stop; the human runs `approve` |
| steps | tests/ | `bdd_check.py red` exits 0 | stop; the human sets phase implement |
| implement | app code | `bdd_check.py green` exits 0 | report with the checker output |

Checker: `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/bdd_check.py red|green` from the project root,
as a command of its own (no pipes, no `;`): only then does the allow rule match.
Never run `approve` and never write `.bdd/`; those are the human's signature.

## spec
- One behaviour per scenario, one When, a Then that names an observable outcome.
- Tag each scenario `@sc_<feature>_<NN>` and add the tag to `markers` in pytest.ini.

## steps
1. Bind once per feature: `scenarios("../features/<name>.feature")`; start from templates/test_feature.py.
2. List what is missing: `.venv/bin/python -m pytest --generate-missing --feature features tests`.
   It exits 0 even when steps are missing; read the list.
3. The printed stubs are a checklist, not code: one stub per literal step text. Collapse them into
   parsed steps and reuse existing ones first; see rules/steps.md.
4. Run the checker in red mode. Fix every RED-wrong line. For RED-right lines, read the message:
   it must describe the missing behaviour (rules/red-reasons.md).

## implement
- Change application code only. If a scenario or a step looks wrong, stop and say so.
- Run the checker in green mode until it exits 0; paste its last line verbatim.

## Files
- rules/steps.md: binding, target_fixture, parsers, reuse, Then only observes; with examples.
- rules/red-reasons.md: the checker's labels and what each means; when a RED-right is not right.
- templates/test_feature.py: the shape of a new step module.
- scripts/bdd_check.py: `approve` (human only), `red`, `green`.
```

三個附屬檔的內容就是本章前兩節的結論：`rules/steps.md` 借用 [Ch 9](./09-rule-files.md) rule file 的寫法（穩定 ID 加好／壞例子）寫四條，ST-1 用 `scenarios()` 綁一次、ST-2 When 經 `target_fixture` 交出結果、ST-3 一種句型一支參數化 step（壞例子正是上面那三支字面 stub）、ST-4 Then 只觀察（Ch 11 改造出的第一版 `bdd` 也有 `rules/steps.md`，編號是 ST-01..ST-07；最終版是另寫的一份、重新編號，兩組編號不能互換，例如那邊的 ST-03 是「共用 step 放 conftest.py」、ST-04 是「Given 經公開介面佈置並斷言」）；`rules/red-reasons.md` 說明檢查器三種標籤怎麼讀，並點名 405、404 這種「標成 RED-right 卻不算數」的訊息；`templates/test_feature.py` 是一行 docstring、import、`scenarios()`，加三行註解（Given 佈置並斷言、When 只做一件事並回傳 `result`、Then 不改任何東西）。專案的 `CLAUDE.md` 只有兩句：這是 `app.py` 的預約 API，行為用 `bdd` skill 開發、階段在 `.bdd/phase`，Python 是 `.venv/bin/python`。本章篇幅只容得下脊椎與腳本，這四個檔請照上面的描述自己寫：它們只給 agent 讀，本章的指令都不讀它們（skill-lint 只查 frontmatter 與 markdown 連結）。為什麼階段放在檔案裡，不放在對話裡？對話會被壓縮、會換 session，hook 也讀不到對話。為什麼簽核存 hash，不存一個「已簽核」旗標？旗標只記得有人簽過，hash 能發現簽過之後的任何改動，不管是誰、從哪條路改的。檢查器 `.claude/skills/bdd/scripts/bdd_check.py`，和 [Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py` 同一個想法，多看一層「紅在哪一步」：

```python
"""bdd_check.py approve|red|green, run from the project root. approve (a human): hash features/**/*.feature
into .bdd/approved.json, set .bdd/phase to steps. red: >=1 scenario fails in a Then step on an assertion,
none fails another way. green: all pass. red/green check the approval first; exit 0 = expectation met.
"""
import argparse, hashlib, json, re, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

def digests():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path("features").rglob("*.feature"))}

def drift():
    if not Path(".bdd/approved.json").exists():
        return ["no approval yet: a human reviews features/ and runs `bdd_check.py approve`"]
    old, new = json.loads(Path(".bdd/approved.json").read_text()), digests()
    return [f"{f}: changed since approval; restore it or ask a human to re-approve"
            for f in sorted(old.keys() | new.keys()) if old.get(f) != new.get(f)]

def failed_step(steps):  # cucumber JSON keeps And/But, so carry the last Given/When/Then forward
    kind = "given"
    for s in steps:
        kind = kind if s["keyword"] in ("And", "But", "*") else s["keyword"].lower()
        if s["result"]["status"] == "failed":
            return kind, f'{s["keyword"]} {s["name"]}: '
    return None, ""

def reason(bad):
    msg = bad.get("message") or bad.tag
    if m := re.search(r"fixture '\w+' not found", msg):
        return m.group(0) + " (does the step that makes it set target_fixture?)"
    if msg == "collection failure":  # the real error is the traceback's last "E " line
        return next((ln.strip() for ln in reversed((bad.text or "").splitlines()) if ln.startswith("E ")), msg)
    return msg.splitlines()[0].replace("pytest_bdd.exceptions.", "").split(". Line ")[0][:140]

def run_suite():
    with tempfile.TemporaryDirectory() as tmp:
        junit, cuke = Path(tmp, "junit.xml"), Path(tmp, "cucumber.json")
        out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              f"--junitxml={junit}", f"--cucumberjson={cuke}"],
                             capture_output=True, text=True).stdout.strip().splitlines()
        cases = list(ET.parse(junit).iter("testcase")) if junit.exists() else []
        docs = json.loads(cuke.read_text()) if cuke.exists() else []
    seen = {el["id"]: el for feature in docs for el in feature["elements"]}
    rows = []
    for case in cases:
        sc = seen.get(case.get("name"), {})
        label = " ".join("@" + t["name"] for t in sc.get("tags", [])) or case.get("name")
        bad = next((k for k in case if k.tag in ("failure", "error", "skipped")), None)
        if bad is None:
            rows.append(("pass", label, ""))
            continue
        (kind, where), why = failed_step(sc.get("steps", [])), reason(bad)
        right = bad.tag == "failure" and kind == "then" and why.startswith(("AssertionError", "assert "))
        rows.append(("RED-right" if right else "RED-wrong", label, where + why))
    return rows, (out or ["pytest printed nothing"])[-1]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["approve", "red", "green"])
    mode = ap.parse_args().mode
    if mode == "approve":
        Path(".bdd").mkdir(exist_ok=True)
        Path(".bdd/approved.json").write_text(json.dumps(digests(), indent=2) + "\n")
        Path(".bdd/phase").write_text("steps\n")
        print(f"approved {len(digests())} feature file(s); phase is now steps")
        return 0
    if problems := drift():
        print("\n".join(problems) + f"\n0 scenarios run, {len(problems)} problems")
        return 1
    rows, last = run_suite()
    for status, label, why in rows:
        print(f"{status:<10}{label}  {why}".rstrip())
    n = {k: sum(r[0] == k for r in rows) for k in ("pass", "RED-right", "RED-wrong")}
    red_ok = n["RED-wrong"] == 0 and n["RED-right"] > 0
    met = bool(rows) and (red_ok if mode == "red" else n["pass"] == len(rows))
    print(f"{len(rows)} scenarios: {n['pass']} passed, {n['RED-right']} red for the right reason, "
          f"{n['RED-wrong']} red for the wrong reason; expect {mode}: {'met' if met else 'NOT met'}"
          + ("" if rows else f" (pytest: {last})"))
    return 0 if met else 1

if __name__ == "__main__":
    sys.exit(main())
```

判斷刻意保守：失敗在 Then、而且是斷言，才算紅得對，其他一律算錯。junit 對有自訂訊息的斷言記 `AssertionError: ...`，對裸 `assert` 記 `assert 405 == 201`，兩種都要認。把 `app.py` 換回沒有 R1 的版本，先由人簽核，再跑紅燈檢查；之後用 `cp -R` 做四份副本，各弄壞一處（未定義步驟：在 `@sc_book_01` 的 Given 後面插一行 `And the office is open`、在 `@sc_book_03` 開頭插一行 `Given today is a working day`；少 `target_fixture`；`scenarios()` 路徑錯；API 路由打成 `/booking`），各跑一次 `red`；未定義步驟那份改了 feature，要先重新 approve，否則只會看到 `changed since approval`。下面的輸出前五行是簽核加紅燈檢查，後四行依序摘自四份壞副本各自輸出的第一行：

```
$ python .claude/skills/bdd/scripts/bdd_check.py approve && python .claude/skills/bdd/scripts/bdd_check.py red
approved 1 feature file(s); phase is now steps
pass      @sc_book_01
RED-right @sc_book_02  Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob: AssertionError: expected (409, 'Taipei is taken 10:00-11:00 by Bob'), got (201, None)
pass      @sc_book_03
3 scenarios: 2 passed, 1 red for the right reason, 0 red for the wrong reason; expect red: met
RED-wrong @sc_book_01  StepDefinitionNotFoundError: Step definition is not found: Given "the office is open"
RED-wrong @sc_book_01  Then the booking is confirmed: fixture 'result' not found (does the step that makes it set target_fixture?)
RED-wrong tests.test_booking  E   FileNotFoundError: [Errno 2] No such file or directory: '/private/tmp/lab/aibdd/ch27-bdd-loop/walk-path/tests/features/booking.feature'
RED-right @sc_book_01  Then the booking is confirmed: AssertionError: expected the booking to be confirmed, got (405, 'Method Not Allowed')
```

四份壞副本都是 `expect red: NOT met`、exit 1。注意最後一行：它被標成 RED-right，因為失敗確實在 Then、確實是斷言，但 405 是管線壞了，不是行為缺了；整體沒過，是因為同一次執行的另外兩個情境在 Given 就斷言失敗（`assert 405 == 201`），被標成 RED-wrong。腳本只判斷形狀，不判斷語意，訊息要有人讀。

## 四、兩個 hook：鎖住合約，不准紅著收工

`.claude/settings.json` 裡，有 `args` 就是 exec form，不經 shell、路徑不用引號；直譯器寫專案裡的 `.venv/bin/python`，因為 Ch 0 發現這台機器的 `~/.claude/settings.json` 會覆寫 `PATH`，hook 裡寫 `python3` 會跑到系統的 3.9.6，寫 `python` 則根本找不到。`onFailure: "block"` 讓 guard 自己壞掉（找不到檔、exit 1、逾時）時照樣擋下；官方文件註明它要 2.1.295 以上，對 Stop hook 無效。協定的概念見 [harness Ch 30](../harness_engineering/30-hooks.md)。下面依序是 `.claude/settings.json` 與 `.claude/hooks/feature_guard.py`：

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command",
      "command": "${CLAUDE_PROJECT_DIR}/.venv/bin/python",
      "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/feature_guard.py"], "onFailure": "block"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "${CLAUDE_PROJECT_DIR}/.venv/bin/python",
      "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/bdd_stop.py"], "timeout": 120}]}]
  }
}
```

```python
"""feature_guard.py: PreToolUse hook (Edit|Write). By .bdd/phase (missing = spec): deny .bdd/ always,
features/ outside spec, tests/ in implement. Exit 1 if stdin is not JSON."""
import json, os, sys
from pathlib import Path

LOCKS = [(".bdd", lambda phase: False, "ask the human to run bdd_check.py"),
         ("features", lambda phase: phase == "spec",
          "the scenarios are approved; if one is wrong, stop and ask the human to reopen phase spec"),
         ("tests", lambda phase: phase != "implement",
          "step definitions are frozen while implementing; change app code, or ask the human for phase steps")]

def main():
    if "--help" in sys.argv:
        print(__doc__)
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError as e:
        print(f"feature_guard: stdin is not hook JSON ({e})", file=sys.stderr)
        return 1
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd", ".")).resolve()
    target = Path(event.get("tool_input", {}).get("file_path", "")).resolve()  # follows symlinks
    phase = (root / ".bdd/phase").read_text().strip() if (root / ".bdd/phase").exists() else "spec"
    for top, editable, hint in LOCKS:
        if root / top in (target, *target.parents) and not editable(phase):
            reason = f"bdd phase is {phase}: {target.relative_to(root)} is locked; {hint}."
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                  "permissionDecision": "deny", "permissionDecisionReason": reason}}))
            break
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

比對前先 `resolve()`，再用 `parents` 判斷在不在目錄底下；字串前綴比對會被 symlink 或 `features_old/` 這種名字騙過。用管線餵事件測它，先 `export CLAUDE_PROJECT_DIR=$(pwd -P)`、`echo implement > .bdd/phase`。事件只留 hook 會讀的欄位，Claude Code 實際送來的還有 `session_id`、`cwd`、`tool_name` 等（第五節的記錄 hook 看過）：

```
$ printf '{"tool_input":{"file_path":"%s/features/booking.feature"}}' "$CLAUDE_PROJECT_DIR" | python .claude/hooks/feature_guard.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "bdd phase is implement: features/booking.feature is locked; the scenarios are approved; if one is wrong, stop and ask the human to reopen phase spec."}}
exit=0
$ printf '{"tool_input":{"file_path":"%s/app.py"}}' "$CLAUDE_PROJECT_DIR" | python .claude/hooks/feature_guard.py
exit=0
```

改 `app.py` 沒有輸出，表示不表態、交回一般權限流程。改 `tests/test_booking.py` 與寫 `.bdd/phase` 也被拒，理由分別以 `step definitions are frozen while implementing` 與 `ask the human to run bdd_check.py` 結尾；階段改回 spec 後，同一個 `features/` 事件沒有輸出、exit 0。餵它非 JSON，stderr 是 `feature_guard: stdin is not hook JSON (Expecting value: line 1 column 1 (char 0))`、exit 1；依官方文件，真實 session 裡 `onFailure: "block"` 會因此擋下那次呼叫（本章沒有在 session 裡觸發這條路）。`.claude/hooks/bdd_stop.py`：

```python
"""bdd_stop.py: Stop hook. Runs bdd_check.py red (phase steps) or green (implement) and blocks the stop
on failure; with stop_hook_active it lets Claude stop and shows the user the output instead."""
import json, os, subprocess, sys
from pathlib import Path

def main():
    if "--help" in sys.argv:
        print(__doc__)
        return 0
    event = json.load(sys.stdin)
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd", ".")).resolve()
    phase = (root / ".bdd/phase").read_text().strip() if (root / ".bdd/phase").exists() else "spec"
    if phase not in ("steps", "implement"):
        return 0
    mode = "red" if phase == "steps" else "green"
    check = Path(__file__).resolve().parents[1] / "skills/bdd/scripts/bdd_check.py"
    run = subprocess.run([sys.executable, str(check), mode], cwd=root, capture_output=True, text=True)
    if run.returncode == 0:
        return 0
    if event.get("stop_hook_active"):
        print(json.dumps({"systemMessage": f"bdd: {mode} check still fails:\n{run.stdout.strip()}"}))
    else:
        print(json.dumps({"decision": "block", "reason": f"phase {phase} is not done:\n{run.stdout.strip()}"}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

`stop_hook_active` 為 true，表示這次收工本身就是被 Stop hook 擋回去之後的繼續。文件要求檢查它，免得卡在永遠不會成立的條件；另有保險：連擋 8 次、中間沒有任何工具呼叫，Claude Code 會強制結束這一輪（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` 可調）。本章採最保守的讀法，推一次，第二次放行並把結果交給人。`app.py` 換回沒有 R1 的版本：階段為 steps 時，餵 `{"stop_hook_active": false}` 得到 exit 0、沒有輸出（紅得對，放行）；改成 implement 再餵兩次，第二次換成 `true`：

```
$ echo '{"stop_hook_active": false}' | python .claude/hooks/bdd_stop.py
{"decision": "block", "reason": "phase implement is not done:\npass      @sc_book_01\nRED-right @sc_book_02  Then the booking is rejected because \"Taipei\" is taken 10:00-11:00 by Bob: AssertionError: expected (409, 'Taipei is taken 10:00-11:00 by Bob'), got (201, None)\npass      @sc_book_03\n3 scenarios: 2 passed, 1 red for the right reason, 0 red for the wrong reason; expect green: NOT met"}
exit=0
$ echo '{"stop_hook_active": true}' | python .claude/hooks/bdd_stop.py
{"systemMessage": "bdd: green check still fails:\npass      @sc_book_01\n…（略）
exit=0
```

R1 補回去後，同一個事件 exit 0、沒有輸出。最後是 guard 管不到的路：不經 Edit 工具，用 `sed` 把 Then 改成 `is already booked`，再餵一次 `false` 的事件，得到 `{"decision": "block", "reason": "phase implement is not done:\nfeatures/booking.feature: changed since approval; restore it or ask a human to re-approve\n0 scenarios run, 1 problems"}`。matcher 只接 `Edit|Write`，Bash 的 `sed` 根本不會送到 guard；hash 比對不在乎檔案怎麼被改。兩層各管一件事：guard 在動作前擋常見路徑，並把理由回給 Claude；hash 在收工時抓任何路徑的漏網之魚，這是 [pstack Ch 14](../pstack/14-verification-principles.md) 的 Prove It Works：檢查真實的產物，不信任過程的自述。整個 skill 過 [pstack Ch 28](../pstack/28-skill-authoring.md) 的 skill-lint：`1 skills, 0 problems`。

## 五、讓 Claude Code 實跑三次

> 實際執行紀錄（節錄），每次執行結果不同。2026-11-02，Claude Code 2.1.295，claude-opus-5-5。

`agent/` 是 lab 裡另一份專案：feature、pytest.ini、conftest、CLAUDE.md 的內容與本章相同，`app.py` 是行為相同的自寫版（201 時多回傳 `id`），停在「人已 approve、沒有 R1、`tests/` 只有 conftest.py」。每次都用 `claude -p`，加上 `--max-turns`（16、14、8）、`--tools "Read,Edit,Write,Glob,Grep,Bash,Skill"`、`--allowedTools "Read,Edit,Write,Glob,Grep,Skill,Bash(.venv/bin/python *)"`、`--permission-mode acceptEdits --permission-prompts none`、`--setting-sources project,local`、`--max-budget-usd 4`，以 stream-json 加 `--include-hook-events` 記錄；完整命令在 lab 的 `run-agent.sh`，另外掛了一個只記錄 hook 輸入的 hook。表中的 turns 是結果事件的 `num_turns`，約為工具呼叫數加 1，和 `--max-turns` 不是同一個計數單位（[Ch 7](./07-messy-baseline.md) 的底層機制），所以第三次的 10 大於 8 也沒被截斷。

| 次 | 階段 | 提示（節錄） | 發生了什麼 | turns、費用、時間 |
|---|---|---|---|---|
| 1 | steps | write the step definitions… stop when the steps phase is done | 寫出 `tests/test_booking.py`；Stop hook 跑 `red` 後放行 | 16、$0.38、41 s |
| 2 | implement | make the approved scenarios pass | 只改 `app.py`，第一次就寫對半開區間，`green` met | 12、$0.23、14 s |
| 3 | implement | update the feature file to read "… is already booked" | Edit 被 guard 拒絕，沒有繞路，回報怎麼重開 spec | 10、$0.27、22 s |

第一次暴露了 skill 的一個洞。agent 把檢查器和 `tail`、`echo "exit=$?"`、`git status` 串成一行。Claude Code 會把複合命令拆開逐段檢查，每段都要被允許（符合 allow 規則，或是內建的唯讀命令），這一行有一段沒過，整行被拒：`This Bash command contains multiple operations. The following part requires approval: tail -n 8; .venv/bin/python .claude/skills/bdd/scripts/bdd_check.py red; echo "exit=$?"; git status --short`。同一次執行裡較早的 `.venv/bin/python -m pytest …; ls …; cat …` 反而通過了，難處在於猜不準哪一段會卡住。它如實回報「I couldn't run `bdd_check.py red` myself」，我們事後重跑確認 `expect red: met`，並在 SKILL.md 加上「as a command of its own」那一行（其中的「only then」是給 agent 的簡化：單獨一行時，判斷只剩那條 allow 規則）；後兩次它都單獨執行檢查器。第二次它在 `create()` 加了 `b.start < other["end"] and other["start"] < b.end` 的檢查，還主動指出「The 409 message names the first conflicting booking found, so if a request overlaps several, only that one is reported. No scenario covers that case.」，這正是 [Ch 29](./29-example-mapping.md) Example Mapping 的一張問題卡。第三次，Claude 收到的工具結果是 `PreToolUse:Edit hook error: bdd phase is implement: features/booking.feature is locked; the scenarios are approved; if one is wrong, stop and ask the human to reopen phase spec.`。它的回覆有一句值得記：「I could get around it with a shell edit, but that would bypass your sign-off」。它知道 shell 這條路存在，選擇不走；第四節的 hash 檢查就是為它哪天選擇走而準備。記錄 hook 顯示，Claude Code 傳進來的 `CLAUDE_PROJECT_DIR` 與 `file_path` 都是解析過的 `/private/tmp/...`，三次的 `stop_hook_active` 都是 false。三次收工時檢查都已通過，Stop hook 都直接放行，「擋下」只在第四節用管線測過。實跑時三支腳本的註解與寫法和本章不同（檢查器與 guard 的判斷邏輯相同，檢查器當時還多一個沒用到的 `--root` 選項），Stop hook 也是較早的版本，多一段「檔案沒變才放行」的判斷（存於 lab 的 `runs/bdd_stop.at-run-time.py`），這段差異在三次實跑裡都沒有被觸發。

## 對比與取捨

| 做法 | 擋得住 | 擋不住 | 適用 |
|---|---|---|---|
| CLAUDE.md 或 skill 寫「不准改 feature」 | 大多數時候的模型 | 模型判斷「這次例外」 | 偏好與指引 |
| permission deny `Edit(/features/**)`（[Ch 3](./03-propose-constrain-feedback.md)） | Edit、Write，以及 Claude Code 認得的 `sed`、`tee`、重新導向 | `python -c` 這類自己開檔的子程序；spec 階段 agent 也寫不了 | feature 一律由人寫 |
| 本章的 PreToolUse guard | 依階段開關，理由回給 Claude | 不走 Edit、Write 的路徑 | agent 參與起草 feature |
| 簽核 hash（Stop hook、CI 重算） | 任何寫入路徑 | 只能事後發現 | 一定要有的最後一道 |
| CODEOWNERS 加分支保護 | 未經審核的合併 | 本機 session 裡的改動 | 團隊正式流程 |

完成的定義也是一道光譜：agent 自稱完成 → [Ch 3](./03-propose-constrain-feedback.md) 每次編輯後回報的 `run_tests.py` → 本章依階段判斷的 Stop hook → 合併前的 CI。越往右越確定、回饋也越慢，所以四層會並存。[Ch 20](./20-tdd-with-agents.md) 的 `tdd_guard.py` 與 `require_green.py` 是同一組模式用在 TDD 的紅綠階段；本章換成 BDD 的三個階段，多了「合約」這一層。

## 踩雷集錦

1. **錯誤直覺：`--generate-missing` 缺東西會回非零 → 正確認識：** pytest-bdd 9.0.1 配 pytest 9.2.0 永遠回 exit 0。把它當清單讀，閘門交給 `bdd_check.py red`。
2. **錯誤直覺：產生的 stub 貼上就能用 → 正確認識：** 它一個例子一支，參數化是你的工作；貼進模組的 stub 還會默默蓋掉 conftest 的共用 step，pytest-bdd 不報歧義。先 grep 既有 step，再決定要不要新增。
3. **錯誤直覺：紅了就能開始實作 → 正確認識：** 未定義步驟、stub、少 `target_fixture` 全都是 exit 1，和斷言失敗分不出來；`scenarios()` 路徑錯則是收集錯誤，exit 2。就算檢查器標了 RED-right，405 這種管線錯誤一樣過得了形狀檢查，訊息要有人讀。
4. **錯誤直覺：cucumber JSON 是紅綠燈的真相 → 正確認識：** 它漏掉未定義的步驟，第一步就未定義的情境整個消失；`reports/` 不存在時測試全過也回 exit 1。紅綠以 pytest 自己的結果為準，JSON 只拿來定位與做文件。
5. **錯誤直覺：PreToolUse hook 鎖住了 feature → 正確認識：** 它只管 Edit 和 Write，`sed` 照樣改得動，第三次實跑時 agent 自己點出這條路。hash 比對才不管路徑；`.bdd/approved.json` 要 commit，讓簽核留在 review 裡。

## 進階：讓這套約束撐過團隊與 CI

- **Stop hook 的放行策略**：本章推一次；實跑用過的版本是「檔案還在變就繼續擋，停止進展才放行」，比較嚴，代價是多一份狀態檔。官方最佳實務另列 `/goal`，由另一個模型判斷條件，不是確定性的，不能取代腳本。
- **把檢查搬進 CI、不讓 agent 自己簽核**：CI 跑 `bdd_check.py green`（它會先比對 hash），CODEOWNERS 要求 `features/` 與 `.bdd/approved.json` 的改動經 PM 或 QA 核准；`permissions.deny` 加 `Bash(*bdd_check.py approve*)`。本段未實測，為理論預期行為；可以開一個只改 feature 的 PR、或在互動 session 請 Claude 執行 approve 來驗證。
- **為什麼 hook 不寫在 skill 的 frontmatter，以及多個 feature 並行**：frontmatter 也能宣告 hook，但要等 skill 被叫用才註冊；agent 沒載入 `bdd` skill，抽屜就沒鎖。合約保護要一直在，所以放在 `settings.json`。多個 feature 並行時，可讓階段與簽核以 feature 為單位（例如 `.bdd/booking.phase`）；[Ch 31](./31-bdd-in-sdd.md) 接手這件事，切得更細：每個 task 擁有同一個 feature 檔裡的一到兩個情境，Stop hook 只跑那個 task 的 Verify。

## 動手練習

1. 為 R2 加一個情境「end must be after start」，從 spec 走到 implement。`bdd_check.py red` 會回 `expect red: NOT met`，因為 R2 早就實作了、沒有任何 RED-right。這代表什麼？這個情境還要不要寫？你會怎麼跟人說？
2. 重現第二節的 `9:00` 錯字，先不帶型別、再帶型別，各跑一次 `bdd_check.py green` 和 `--generate-missing`；然後照 SKILL.md 走一次正確的修法：重開 spec、改 feature、重新 approve。
3. 把 `bdd_stop.py` 改成用 exit 2 加 stderr 擋下，用第四節那兩個 Stop 事件測，對照官方文件「Exit code 2 behavior per event」說明 Claude 讀到的理由從哪裡來。

## 本章重點整理

- BDD 迴圈是 feature → 簽核 → step definitions → 紅且紅得對 → 實作 → 綠 → 活文件。pytest-bdd 9.0.1 有幾處會騙人：沒有 `--bdd`、未綁定是 exit 5、`--generate-missing` 永遠 exit 0、stub 一例一支、cucumber JSON 漏掉未定義步驟。
- step definition 就是 fixture：比對成功的定義照 fixture 規則取最近的一支，沒有歧義檢查；`target_fixture` 把 When 的結果交給 Then。判斷紅得對要兩份資料：pass/fail 取自 junit XML，失敗的那一步取自 cucumber JSON；只有 Then 的斷言失敗才算對，而且訊息仍要有人讀。
- `bdd` skill 把迴圈分成三個階段、一張完成條件表；guard 依階段鎖目錄並把理由回給 Claude，Stop hook 依階段跑檢查器，簽核 hash 抓任何路徑的竄改。三次實跑裡 agent 寫出 step、一次改對實作、拒絕繞過 guard；真正暴露的問題是 skill 裡的指令寫法，修的是 skill。

## 自我檢核

- [ ] 不看本章，說出 `pytest --bdd`、沒綁定、未定義步驟、少 `target_fixture` 各自的 exit code 與訊息特徵。
- [ ] 為什麼檢查器不能只讀 cucumber JSON？說明 pytest-bdd 何時建立一步的報告，以及未定義步驟為什麼不在裡面。
- [ ] 一個情境在 Then 斷言失敗，訊息是 `got (405, 'Method Not Allowed')`。它紅得對嗎？檢查器怎麼標？你怎麼處理？
- [ ] guard 和簽核 hash 各擋哪一種改動？只能留一個時你留哪個，為什麼？
- [ ] `stop_hook_active` 是什麼？本章的 Stop hook 看到它為 true 時做什麼？換成「還紅就一直擋」會發生什麼？

## 延伸閱讀

- **[pytest-bdd 官方文件](https://pytest-bdd.readthedocs.io/en/stable/)**（9.0.1）
  - **讀哪裡**：「Step arguments」（parse、cfparse、re、converters、extra_types）、「Override fixtures via given steps」、「Scenarios shortcut」、「Reusing steps」、「Test code generation helpers」。
  - **學到什麼、和本章的關聯**：本章每種綁定寫法的出處；`pytest-bdd generate` 和 `--generate-missing` 都只印到 stdout。前提是會寫 pytest fixture；底層機制一節是這份文件沒寫出來的那一半。
- **[Claude Code — Hooks reference](https://code.claude.com/docs/en/hooks)**
  - **讀哪裡**：「PreToolUse decision control」、「Stop」（`stop_hook_active` 與 8 次上限）、「Exit code 2 behavior per event」、「Block the action when a hook fails」、「Workspace trust」。
  - **學到什麼、和本章的關聯**：deny 的理由給誰看、`onFailure` 的版本需求、`-p` 會把資料夾當成已信任所以專案 hook 照跑。兩個 hook 的協定全出自這裡，概念先讀 harness Ch 30。
- **[Claude Code — Best practices](https://code.claude.com/docs/en/best-practices)**
  - **讀哪裡**：「Give Claude a check to run」、「Enforce the check with a Stop hook」、「Enforce the rules that can't have exceptions」。
  - **學到什麼、和本章的關聯**：確定性與非確定性指令的分界（「If something has to happen every time, make it a hook, a deny rule, or a managed setting」）。本章把收工前的檢查從提示詞搬進 Stop hook，就是這一頁的主張。
- **[Cucumber — API reference](https://cucumber.io/docs/cucumber/api/)**
  - **讀哪裡**：「Step results」整節（Undefined、Pending、Failed、Skipped、Ambiguous）。
  - **學到什麼、和本章的關聯**：原版 Cucumber 怎麼處理未定義與歧義的步驟，拿來對照 pytest-bdd 9.0.1 的落差；讀完本章第一、二節再看，踩雷第 2、4 條會更清楚。
- **《Growing Object-Oriented Software, Guided by Tests》** — Steve Freeman、Nat Pryce（Addison-Wesley，2010）
  - **讀哪幾章**：第 5 章「Maintaining the Test-Driven Cycle」的「Start Each Feature with an Acceptance Test」與「Watch the Test Fail」。
  - **學到什麼、和本章的關聯**：「先寫驗收測試、看它因為對的原因失敗」在 AI 之前的原始論證，方法和書中的 Java 無關。`bdd_check.py red` 就是把「Watch the Test Fail」寫成腳本；[Ch 22](./22-backend-e2e-first.md) 已經展開過它的外圈與內圈。

迴圈跑得動了，但 feature 寫得好不好，檢查器一個字也沒管；下一章把 Gherkin 的寫法標準做成 rule file 與 lint。

→ [Ch 28 Gherkin 與 DSL 寫作標準：做成 rule file 與 lint](./28-gherkin-dsl-style.md)
