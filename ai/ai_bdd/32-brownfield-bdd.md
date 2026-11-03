# Ch 32 — 既有專案導入 BDD：從測試反推，重構成 Gherkin + StepDef

> **目標**：把一個沒有 BDD 的既有專案（cicd 課的 `tasktrack`：FastAPI + SQLAlchemy，4 個 pytest 測試）遷成 `features/`、`steps/`、`support/`、`drivers/` 四層。做法是用 AST 從舊測試反推每一個 assert 的去處，寫成 Gherkin 與 step definitions，讓新舊兩套並行，用 `mutate.py` 的手寫突變體證明新套件抓得到舊套件抓得到的每一個 bug，再一個 wave 一個 wave 刪掉舊測試。最後把流程包成 `bdd-migrate` skill 加一個 Stop hook，讓 Claude Code 實跑三次批量遷移。

> **環境**：macOS 26.6 arm64、Python 3.12.15、uv 0.13.1。章節自己的 venv 照 tasktrack 的 requirements 解析出 FastAPI 0.162.0、SQLAlchemy 2.1.6、pytest 9.2.0、httpx 0.28.1，另加 pytest-bdd 9.0.1（gherkin-official 42.0.1）；Claude Code 2.1.295（claude-opus-5-5）。實驗目錄 `/tmp/lab/aibdd/ch32-brownfield/`，輸出皆為 2026-11-02 實跑；GitHub Actions 那段未實測。

## 為什麼需要這個？

Ch 27 到 Ch 31 都是先有情境、再有程式。真實團隊導入 BDD 時，手上通常已經有一套測試，寫的人可能早就調走了，測的是「當年有人覺得重要」的事。整套改寫成 Gherkin 有兩個風險。第一，新套件悄悄測得比較少：兩套都綠，沒有任何訊號告訴你哪個斷言在翻譯途中掉了，本章的第一版翻譯就掉了兩種斷言；[SDD Ch 25](../spec_driven_development/25-tdd-bdd-mda-lineage.md) 的練習 25-B 把一個測試改寫成 spec，預期踩的也是同一個雷：邊界條件在翻譯途中遺漏。第二，翻出來的只有語法：[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的錯誤直覺 4 說得直白，把現有的單元測試翻成 Gherkin，得到的是語法，不是 BDD 的實質。所以本章每翻一步都要回答「這個斷言現在歸誰管」，而且要有一個會失敗的檢查來證明。

前人的做法有兩條線。一條是特徵測試（characterization test）：Michael Feathers 在 2004 年的《Working Effectively with Legacy Code》主張動手前先釘住「現在的行為」，bug 也一起釘；[讀 code 課 Ch 30](../../soft_skills/reading_code/30-reading-bad-code.md) 的原則三、[pstack Ch 9](../pstack/09-feature-refactoring.md) 的「Step 1：先釘住行為契約」都屬於這條線。這次的麻煩是被重構的正是釘子本身：舊測試就是那根 pin，誰來證明新的 pin 跟舊的一樣牢？答案來自 [pstack Ch 14](../pstack/14-verification-principles.md)「四、Test Behavior, Not Implementation」的「Break the behavior」：故意把程式改壞，看兩套測試是不是都喊痛，也就是突變測試（mutation testing）。Ch 7、19、21、26 都把它的工具化留到這一章。另一條線是工具。Spec Kit 1.2.5 的〈Adopting Spec Kit in an Existing Project〉寫明 `specify init`「does not rewrite your application or infer specifications for existing behavior」，也建議別把「document the entire existing system」當第一個 feature。[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 的第九步把 `/speckit.converge` 的方向講清楚了：0.11.10 與 1.2.5 的 converge 模板都把 spec、plan、tasks 當成「sole source of intent」，明文規定不准改 `spec.md`，只往 `tasks.md` 追加程式還沒做到的工作，所以在 brownfield 裡它讓程式碼追上規格，不讓規格追上現況，既有行為得先有人寫進規格。從既有程式與測試反推規格，Spec Kit 本體不做（指南把 brownfield bootstrapping 指向社群 extension 目錄，本章沒有試用），這一章拿舊測試來做。

## 先建立直覺

想像一棟大樓要把舊火警系統換成新的。新系統的規格書寫得再漂亮，也證明不了它跟舊的一樣靈敏，能證明的只有演習：在每個房間點一罐煙霧，看兩套警報是不是都響。舊的響、新的沒響，舊系統就不能拆；兩套都沒響，是共同的盲點；新的響、舊的沒響，算是賺到。更換一層一層來，這一層演習過了才拆這一層的舊探測器。沒點煙的時候兩套都必須安靜，否則演習沒有意義。煙霧罐就是突變體（mutant），一個刻意植入的小 bug；警報響了叫殺死（killed），沒響叫存活（survived）。類比有一處不成立：煙霧罐只測得到你想得到的火源，真正的火可能從沒人點過煙的地方燒起來。突變體是人挑的樣本，不是證明。

```
 tests/（舊套件：4 個測試、10 個 assert）
    │ ① suite_inventory.py：每個 assert 編號 <test>:<n>
    ▼
 migration.yaml：每個 assert 的去處（@sc_ 情境／driver／drop）＋ 分 wave
    │ ② 照 Ch 28 的規則寫 features/，再寫 steps/ → support/ → drivers/
    ▼
 新舊並行：pytest 同時跑 tests/ 與 steps/，兩套都要綠
    │ ③ mutate.py：同一批手寫突變體，兩套各跑一次
    ▼
 lost（舊的殺死、新的沒殺死）是空的？ ──否──▶ 補 step 或 driver，回到 ③
    │是 ④ 刪掉這個 wave 的舊測試、標成 migrated，換下一個 wave
```

## 一、起點：一個沒寫過 BDD 的專案

被測專案用 [cicd 課](../../dev_tools/cicd/README.md) 的 `tasktrack`，不用全課共用的會議室預約：brownfield 的重點是程式與測試都不是照 BDD 寫的，而這個專案我們沒有為本章改過一個字。它有 3 個端點（`POST /tasks`、`GET /tasks`、`PATCH /tasks/{id}/complete`）和 4 個測試，[cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md) 還把測試拆成 unit 與 integration 兩組在 CI 跑。它需要 SQLAlchemy，共用 venv 沒有，所以照 [Ch 0](./00-environment-setup.md) 的規則建章節自己的 venv（`REPO` 是你 clone 這個 repo 的位置）：

```bash
mkdir -p /tmp/lab/aibdd/ch32-brownfield && cd /tmp/lab/aibdd/ch32-brownfield
cp -R "$REPO/dev_tools/cicd/tasktrack" . && cd tasktrack
git init -q && git add -A && git commit -qm "tasktrack as copied from dev_tools/cicd"
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt && .venv/bin/python -m pytest -q
```

uv 裝了 35 個套件，其中 `fastapi==0.162.0`、`pytest==9.2.0`、`sqlalchemy==2.1.6`；pytest 回 `4 passed, 1 warning in 0.03s`。requirements 只寫下限（`fastapi>=0.115`），今天解析出來剛好和課程釘的版本一致，上游一出新版就不一定，要重現本章就在指令後面加 `"fastapi==0.162.0"` 這類釘版。那個 warning 是 Starlette 1.10.0 對 TestClient 的棄用提醒，本章的 `pytest.ini` 照 [Ch 27](./27-ai-bdd-loop.md) 濾掉。接著 `printf 'pytest-bdd==9.0.1\n' >> requirements-dev.txt` 再裝一次，多出 gherkin-official 42.0.1、parse 1.22.4 等 7 個套件。讀舊測試時記下三件事，但一件都不改：`POST /tasks` 回 200 而不是 REST 慣例的 201；`tests/conftest.py` 用 docstring 自己寫著「THIS FUNCTION IS UNSAFE AND SHOULD NOT BE USED」的 `tempfile.mktemp` 決定資料庫檔名，並趕在 import app 之前設好 `DATABASE_URL`；`test_complete_missing_task` 拿 9999 當不存在的 id。遷移期間舊套件就是那根 pin，pin 不能動，pstack Ch 9 引用的原話是「the result must match the original exactly, bugs included」。

## 二、從舊測試反推：行為清單與遷移帳本

第一個問題是「舊套件到底檢查了什麼」。這一步不交給 LLM，因為它是確定性的問題：有幾個 assert、各在哪個測試，漏一個就是漏一個，而 `ast` 一個都不會漏，前提是程式認得測試的寫法。LLM 的工作在下一步，替每個 assert 決定去處。`suite_inventory.py` 放在第六節那個 skill 的 `scripts/` 底下，只用標準函式庫和 PyYAML（由 `uvicorn[standard]` 的 `standard` extra 帶進來）。它只認模組層級的 `def test_*` 與 `assert` 敘述，tasktrack 正好全是這種；測試寫在 `class TestTasks` 裡時它回 `1 files, 0 tests, 0 asserts, 0 problems`，`self.assertEqual` 與藏在 helper 函式裡的 assert 也不算，舊套件有這些寫法就先擴充它：

```python
#!/usr/bin/env python3
"""suite_inventory.py [--map LEDGER] PATH...: list each old test with its HTTP calls and asserts, numbered
<test>:<n>. --map checks a migration ledger (YAML): each assert of a test still in the suite has a destination
(an @sc_ tag in features/, "driver", or "drop: <why>"), and the tests of a migrated wave are gone."""
import argparse, ast, re, sys
from pathlib import Path
import yaml

def url(node):
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in node.values)
    return node.value if isinstance(node, ast.Constant) else "?"

def tests(files):
    for f in files:
        for fn in ast.parse(f.read_text(), filename=str(f)).body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test"):
                calls = sorted((c.lineno, f"{c.func.attr.upper()} {url(c.args[0])}") for c in ast.walk(fn)
                               if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                               and c.func.attr in {"get", "post", "put", "patch", "delete"} and c.args)
                asserts = sorted((a.lineno, ast.unparse(a.test)) for a in ast.walk(fn) if isinstance(a, ast.Assert))
                yield f, fn, [c for _, c in calls], asserts

def check(ledger, found):
    tags = set(re.findall(r"@sc_[a-z]+_\d{2}", " ".join(p.read_text() for p in Path("features").rglob("*.feature"))))
    waves, where, problems = ledger.get("waves", {}), ledger.get("asserts", {}), []
    for name, wave in waves.items():
        for t in wave["tests"]:
            gone = not any(k.startswith(t + ":") for k in found)
            if (wave["state"] == "migrated") != gone:
                problems.append(f"wave {name}: {t} is {'gone' if gone else 'still in the suite'} but state is {wave['state']}")
    for key in found:
        dest = str(where.get(key, ""))
        if not (dest in tags or dest == "driver" or re.fullmatch(r"drop: \S.*", dest)):
            problems.append(f"{key}: destination {dest or 'missing'!r}; use an @sc_ tag from features/, driver, or 'drop: <why>'")
    return problems

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    ap.add_argument("--map", type=Path, metavar="LEDGER", help="migration ledger to check against the suite")
    a = ap.parse_args()
    if a.map and not a.map.is_file():
        ap.error(f"ledger {a.map} not found; start from the skill's templates/migration.yaml")
    found, names, problems = {}, [], []
    files = sorted({f for p in a.paths for f in ([p] if p.is_file() else p.rglob("test_*.py"))})
    for f, fn, calls, asserts in tests(files):
        if fn.name in names:
            problems.append(f"{f}:{fn.lineno}: test name {fn.name} is used twice; <test>:<n> keys need unique names")
        names.append(fn.name)
        print(f"{f}::{fn.name}  {', '.join(calls) or 'no HTTP call'}")
        if not asserts:
            problems.append(f"{f}:{fn.lineno}: {fn.name} has no assert; it can only fail by crashing")
        for n, (line, text) in enumerate(asserts, 1):
            found[f"{fn.name}:{n}"] = text
            print(f"  {fn.name}:{n:<3} L{line:<4} {text}")
    problems += check(yaml.safe_load(a.map.read_text()), found) if a.map else []
    print("\n".join(problems + [f"{len(files)} files, {len(names)} tests, {len(found)} asserts, {len(problems)} problems"]))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

```
$ .venv/bin/python .claude/skills/bdd-migrate/scripts/suite_inventory.py tests
tests/test_tasks.py::test_create_task  POST /tasks
  test_create_task:1   L6    resp.status_code == 200
  test_create_task:2   L8    data['title'] == 'learn docker'
  test_create_task:3   L9    data['milestone'] == 'week-1'
  test_create_task:4   L10   data['done'] is False
  test_create_task:5   L11   isinstance(data['id'], int)
…（略：其餘三個測試的 5 個 assert，去處見下面的帳本）
1 files, 4 tests, 10 asserts, 0 problems
```

key 用「測試名:第幾個」，不用行號：遷移時舊檔會被一段一段刪掉，行號跟著變（第五節刪掉前兩個測試後，`test_complete_task:1` 從 L26 變成 L4），名字不會。代價是測試名必須唯一：cicd Ch 11 那種 unit、integration 各放一份同名測試時，兩個 assert 會搶同一個 key，所以腳本把重複的名字當成問題，回 `test name test_create_task is used twice; <test>:<n> keys need unique names`，exit 1。沒有 assert 的測試也算一個問題：對一個只呼叫 `client.get("/tasks")` 的 `test_tasks_page_loads`，它回報 `test_tasks_page_loads has no assert; it can only fail by crashing`、`1 files, 1 tests, 0 asserts, 1 problems`，exit 1。這種測試沒東西可遷，得先回頭問它想測什麼。去處只有三種：某個情境的 `@sc_` tag（由那個情境負責檢查）、`driver`（協定細節：狀態碼、欄位、型別），或 `drop: <理由>`。把十個決定寫成遷移帳本（ledger）`migration.yaml`，同時把舊測試切成 wave，一個 wave 是一個功能區塊，大小以一個 PR 吞得下為準：

```yaml
waves:
  adding: {state: both, tests: [test_create_task, test_list_tasks]}
  completing: {state: both, tests: [test_complete_task, test_complete_missing_task]}
asserts:
  test_create_task:1: driver               # status 200
  test_create_task:2: "@sc_task_01"        # title
  test_create_task:3: "@sc_task_01"        # milestone
  test_create_task:4: "@sc_task_01"        # done is False
  test_create_task:5: driver               # id is an int
  test_list_tasks:1: driver                # status 200
  test_list_tasks:2: "@sc_task_02"         # order
  test_complete_task:1: driver             # status 200
  test_complete_task:2: "@sc_task_03"      # done is True (the scenario reads it back from the list)
  test_complete_missing_task:1: "@sc_task_04"
```

三個 `status_code == 200` 和 `isinstance(data['id'], int)` 交給 driver：Gherkin 不寫狀態碼（Ch 28 的 GH-03），但它們是 API 對呼叫端的承諾，不能因為 feature 檔不寫就消失。404 那一個交給 `@sc_task_04`，理由是「不存在的任務不能完成」是業務規則，404 只是它在 HTTP 上的寫法；第四節會看到這個判斷的代價。第三節寫好 features 後，`suite_inventory.py tests --map migration.yaml` 回 `1 files, 4 tests, 10 asserts, 0 problems`。故意弄壞三處（把 `adding` 標成 `migrated`、刪掉 `test_create_task:5`、把 `@sc_task_02` 打成 `@sc_task_09`），得到四則問題：兩則 `wave adding: test_create_task is still in the suite but state is migrated`（另一則是 `test_list_tasks`）、`test_create_task:5: destination 'missing'; ...`、`test_list_tasks:2: destination '@sc_task_09'; ...`，最後一行 `1 files, 4 tests, 10 asserts, 4 problems`，exit 1。帳本只是一份宣告：它保證每個 assert 都有人認領，不保證認領的人做到了。

## 三、目標佈局：features、steps、support、drivers

四個目錄各管一層（step definition、`target_fixture` 不熟的話先讀 [Ch 27](./27-ai-bdd-loop.md) 第一節）：`features/` 寫給人讀；`steps/` 把句子拆成參數；`support/` 放情境的世界（資料庫、fixture）與領域動詞；`drivers/` 是唯一知道 HTTP 的地方。分層的來源與「一份 feature、兩個 driver」見 [Ch 30](./30-one-gherkin-two-drivers.md)，這一章只要每個舊 assert 在新結構裡有一個明確的家。詞彙照 [Ch 28](./28-gherkin-dsl-style.md) 的格式寫進 `glossary.yaml`，名詞取自程式本身：`context: tasktrack`，`actions: {adds: has added, completes: has completed}`，`terms` 底下三個詞 `task`、`milestone`、`complete`，各有 `zh`、`definition` 與 `avoid`（依序是 `[todo, ticket, item]`、`[sprint, deadline]`、`[finish, finishes, close, closes]`）。lint 整個從 Ch 28 搬過來：`cp -R /tmp/lab/aibdd/ch28-gherkin-style/.claude/skills/gherkin-style .claude/skills/`。一個 wave 一份 feature 檔，R1 到 R4 是從帳本長出來的業務規則：

```
==> features/adding_tasks.feature <==
Feature: Adding tasks

  Rule: R1 A new task is open and keeps its title and milestone

    @sc_task_01 @smoke
    Scenario: A new task starts open
      Given the task list is empty
      When someone adds the task "learn docker" to milestone "week-1"
      Then the task "learn docker" is open in milestone "week-1"

  Rule: R2 Tasks are listed in the order they were added

    @sc_task_02
    Scenario: The list keeps the order of adding
      Given someone has added the task "a"
      And someone has added the task "b"
      When someone views the task list
      Then the tasks are listed as "a", "b"
==> features/completing_tasks.feature <==
Feature: Completing tasks

  Rule: R3 Completing a task marks it done

    @sc_task_03 @smoke
    Scenario: A completed task is done
      Given someone has added the task "done me"
      When someone completes the task "done me"
      Then the task "done me" is done

  Rule: R4 A task that does not exist cannot be completed

    @sc_task_04
    Scenario: Completing a missing task is refused
      Given the task list is empty
      When someone completes a task that does not exist
      Then completing is refused because the task is not found
```

`gherkin_lint.py --glossary glossary.yaml features` 回 `2 files, 0 problems`。會看狀態的 Then 一律從清單讀回來，不讀建立或完成時 API 的回應，因為使用者事後看得到的是清單；這個翻譯決定下一節會被記上一筆。driver 的第一版長這樣（下一節會改）：

```python
"""Protocol driver for the tasktrack HTTP API: paths, status codes and the shape of a task live here
and nowhere else. Scenarios and TaskBoard never see HTTP."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Task:
    id: int
    title: str
    milestone: str | None
    done: bool

class NotFound(Exception):
    pass

class ApiDriver:
    def __init__(self, http):
        self.http = http  # anything with .request(): TestClient here, an httpx.Client for a live server
    def create(self, title, milestone=None) -> Task:
        return self._task(self._call("POST", "/tasks", json={"title": title, "milestone": milestone}))
    def list(self) -> list[Task]:
        return [self._task(t) for t in self._call("GET", "/tasks")]
    def complete(self, task_id) -> Task:
        return self._task(self._call("PATCH", f"/tasks/{task_id}/complete"))
    def _call(self, method, path, **kw):
        r = self.http.request(method, path, **kw)
        if r.is_error:
            raise NotFound(r.text)
        return r.json()
    @staticmethod
    def _task(body) -> Task:
        return Task(**body)
```

`support/board.py` 是情境的世界：自己的資料庫、driver，以及 step 唯一能叫的領域動詞 `TaskBoard`。它絕不讀 shell 的 `DATABASE_URL`，因為 fixture 會 `drop_all`；要在 CI 打 Postgres，就明確設 `TEST_DATABASE_URL`（本章未實測；cicd Ch 11 的 URL 是 `postgresql+psycopg://...`，還得另裝 psycopg）：

```python
import os, tempfile
# Never the shell's DATABASE_URL: the fixture below drops every table.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/bdd.db")
import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app
from drivers.api import ApiDriver, NotFound

class TaskBoard:
    def __init__(self, driver):
        self.driver = driver
    def add(self, title, milestone=None):
        self.driver.create(title, milestone)
    def tasks(self):
        return self.driver.list()
    def find(self, title):
        found = [t for t in self.tasks() if t.title == title]
        assert len(found) == 1, f"expected one task called {title!r}, found {found}"
        return found[0]
    def complete(self, title):
        self.driver.complete(self.find(title).id)
    def complete_missing(self):
        unused = max((t.id for t in self.tasks()), default=0) + 1  # an id no task has
        try:
            self.driver.complete(unused)
        except NotFound:
            return "the task is not found"
        return "completed"

@pytest.fixture
def board():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as http:
        yield TaskBoard(ApiDriver(http))
```

`complete_missing()` 不沿用 9999，而是取「比現有最大 id 多 1」：情境說的是「一個不存在的任務」，怎麼生出這樣的 id 是 DSL 的事，不該是一個沒人解釋得了的魔術數字。step definitions 依領域概念組織，不依 feature 檔：兩份 feature 都在講任務，所以只有一個 `steps/task_steps.py`。Cucumber 官方 anti-patterns 頁把反過來的做法叫 feature-coupled step definitions，建議「Organise your steps by domain concept」：

```python
from pytest_bdd import given, parsers, then, when

@given("the task list is empty")
def empty(board):
    assert board.tasks() == []
@given(parsers.parse('someone has added the task "{title}"'))
def has_added(board, title):
    board.add(title)
@when(parsers.parse('someone adds the task "{title}" to milestone "{milestone}"'))
def adds(board, title, milestone):
    board.add(title, milestone)
@when("someone views the task list", target_fixture="listed")
def views(board):
    return [t.title for t in board.tasks()]
@when(parsers.parse('someone completes the task "{title}"'))
def completes(board, title):
    board.complete(title)
@when("someone completes a task that does not exist", target_fixture="outcome")
def completes_missing(board):
    return board.complete_missing()
@then(parsers.parse('the task "{title}" is open in milestone "{milestone}"'))
def is_open(board, title, milestone):
    task = board.find(title)
    assert (task.done, task.milestone) == (False, milestone)
@then(parsers.parse("the tasks are listed as {titles}"))
def listed_as(listed, titles):
    assert listed == [t.strip('"') for t in titles.split(", ")]
@then(parsers.parse('the task "{title}" is done'))
def is_done(board, title):
    assert board.find(title).done is True
@then(parsers.parse("completing is refused because {reason}"))
def refused(outcome, reason):
    assert outcome == reason
```

剩下三個小檔。`steps/test_features.py` 是新套件唯一的測試模組，只有 `from pytest_bdd import scenarios` 與 `scenarios("../features")` 兩行：綁整個目錄，新 feature 不會因為忘了綁而默默不跑（Ch 27 看過沒綁定是 exit 5）。根目錄的 `conftest.py` 一行註解加一行 `pytest_plugins = ["support.board", "steps.task_steps"]`，為什麼一定要放根目錄，見底層機制。`pytest.ini` 寫 `pythonpath = .`、`testpaths = tests steps`，`markers` 註冊 `smoke` 與 `sc_task_01` 到 `sc_task_04`（每行一個，冒號後附一句說明），`filterwarnings` 照抄 Ch 27 那一行。結果：`pytest -q steps` 是 `4 passed`；`pytest -q` 新舊一起跑，`8 passed`；`pytest -q -m smoke` 是 `2 passed, 6 deselected`，tag 讓 CI 能先挑最要緊的兩個情境跑。要拿它們打部署後的伺服器還差一步：`the task list is empty` 與 fixture 的 `drop_all` 都假設資料庫歸測試獨用，本章沒有做這一步。

## 四、mutate.py：用突變體證明新套件不比舊的弱

兩套都綠、帳本 0 problems，看起來完成了。換突變體上場。`mutants.yaml` 是人寫的 bug 清單，每一條把 `find` 換成 `replace`，檔案歸人管，agent 不准碰（第六節）：

```yaml
- {id: M1, file: app/main.py, what: create drops the milestone, find: "milestone=payload.milestone", replace: "milestone=None"}
- {id: M2, file: app/main.py, what: a new task starts done, find: "done=False)", replace: "done=True)"}
- {id: M3, file: app/main.py, what: list shows newest first, find: "order_by(Task.id)", replace: "order_by(Task.id.desc())"}
- {id: M4, file: app/main.py, what: complete hits the first task, find: "task = db.get(Task, task_id)", replace: "task = db.query(Task).first()"}
- {id: M5, file: app/main.py, what: unknown task answers 400, find: "status_code=404", replace: "status_code=400"}
- {id: M6, file: app/main.py, what: create answers 201, find: "@app.post(\"/tasks\", response_model=TaskOut)", replace: "@app.post(\"/tasks\", response_model=TaskOut, status_code=201)"}
- {id: M7, file: app/main.py, what: complete is not saved, find: "task.done = True\n        db.commit()\n        db.refresh(task)\n", replace: "db.commit()\n        db.refresh(task)\n        task.done = True\n"}
```

`mutate.py` 是 [Ch 21](./21-tdd-skill-aaa.md) `plant_bugs.py` 的一般化：突變體從資料檔讀，兩組 pytest 選擇由命令列給，除了表格還有一個能當閘門的 exit code：

```python
#!/usr/bin/env python3
"""mutate.py [--only M1,M2] MUTANTS --before ARGS --after ARGS: plant each hand-written mutant (YAML: id, what,
file, find, replace; `find` must occur exactly once) in a scratch copy of the project, run two pytest selections
there, and compare which mutants each kills (pytest exit 1 = killed). Exit 0 when AFTER kills every mutant BEFORE
kills, 1 when it loses one, 2 when the run cannot be trusted (pattern does not apply, red control, pytest error)."""
import argparse, os, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path
import yaml

def pytest_rc(project, args):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # Ch 19: never trust a .pyc next to a mutated file
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args],
                          cwd=project, env=env, capture_output=True).returncode

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mutants", type=Path)
    ap.add_argument("--before", required=True, help='pytest arguments, e.g. "tests"')
    ap.add_argument("--after", required=True, help='pytest arguments, e.g. "steps"')
    ap.add_argument("--only", help="comma-separated mutant ids")
    a = ap.parse_args()
    mutants = [m for m in yaml.safe_load(a.mutants.read_text()) if not a.only or m["id"] in a.only.split(",")]
    if a.only and len(mutants) != len(set(a.only.split(","))):
        ap.error(f"--only {a.only}: not every id is in {a.mutants}")
    bad = [f"{m['id']}: `find` occurs {n} times in {m['file']}; make it match exactly once"
           for m in mutants if (n := Path(m["file"]).read_text().count(m["find"])) != 1]
    if bad:
        print("\n".join(bad + [f"{len(mutants)} mutants, {len(bad)} problems"]))
        return 2
    print(f"{'mutant':8}{'before':10}{'after':10}what it breaks")
    rows = []
    for m in [None] + mutants:
        tmp = tempfile.mkdtemp(prefix="mutant-")
        shutil.copytree(".", tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".*", "__pycache__", "reports"))
        if m:
            f = Path(tmp, m["file"])
            f.write_text(f.read_text().replace(m["find"], m["replace"]))
        rc = [pytest_rc(tmp, shlex.split(s)) for s in (a.before, a.after)]
        shutil.rmtree(tmp)
        word = {0: "survived", 1: "killed"} if m else {0: "green", 1: "RED"}
        cells = [word.get(r, f"error {r}") for r in rc]
        print(f"{m['id'] if m else '-':8}{cells[0]:10}{cells[1]:10}{m['what'] if m else 'no mutant (control)'}")
        rows.append((m, rc))
    (_, control), done = rows[0], rows[1:]
    if control != [0, 0] or any(r not in (0, 1) for _, rc in done for r in rc):
        print("cannot compare: a selection is not green without mutants, or pytest errored; fix that first")
        return 2
    ids = lambda pick: " ".join(m["id"] for m, rc in done if pick(*rc)) or "-"
    print(f"{len(done)} mutants: before kills {sum(rc[0] for _, rc in done)}, after kills {sum(rc[1] for _, rc in done)}; "
          f"lost {ids(lambda b, f: b > f)}; gained {ids(lambda b, f: f > b)}; both missed {ids(lambda b, f: b == f == 0)}")
    return 1 if any(b > f for _, (b, f) in done) else 0

if __name__ == "__main__":
    sys.exit(main())
```

```
$ .venv/bin/python .claude/skills/bdd-migrate/scripts/mutate.py mutants.yaml --before tests --after steps
mutant  before    after     what it breaks
-       green     green     no mutant (control)
M1      killed    killed    create drops the milestone
M2      killed    killed    a new task starts done
M3      killed    killed    list shows newest first
M4      survived  survived  complete hits the first task
M5      killed    survived  unknown task answers 400
M6      killed    survived  create answers 201
M7      survived  killed    complete is not saved
7 mutants: before kills 5, after kills 4; lost M5 M6; gained M7; both missed M4
```

exit 1。舊套件抓得到、新套件抓不到的兩個 bug，正是帳本上寫著 `driver` 與 `@sc_task_04` 的那兩種斷言：`r.is_error` 把 400、404、500 一律當成「找不到」，也從不檢查成功時是不是 200。帳本認領了，程式碼沒兌現。另外兩列也值得看。M7（完成了卻沒存檔）被新套件抓到，因為 Then 從清單讀回狀態，舊測試只看 PATCH 的回應，這是第三節那個決定賺到的。M4（完成時改到第一筆任務）兩套都沒抓到，因為舊測試完成任務時資料庫裡只有一筆。修法是把協定的承諾搬進 driver：`_call` 的兩行換成下面三行，`_task` 在 `return` 前加最後那一行（diff 節錄）：

```
-        if r.is_error:
-            raise NotFound(r.text)
+        if r.status_code == 404:
+            raise NotFound(r.json()["detail"])
+        assert r.status_code == 200, f"{method} {path}: expected 200, got {r.status_code} {r.text}"
+        assert set(body) == {"id", "title", "milestone", "done"} and isinstance(body["id"], int), f"not a task: {body}"
```

再跑一次是 `7 mutants: before kills 5, after kills 6; lost -; gained M7; both missed M4`，exit 0，16 次 pytest 約 6 秒（Apple M5 上重跑多次在 5.6 到 6.3 秒之間）。在暫存副本裡植入 M6 再跑 `pytest steps`，三個會建立任務的情境一起失敗，訊息像 `AssertionError: POST /tasks: expected 200, got 201 {"id":1,"title":"learn docker","milestone":"week-1","done":false}`，結果 `3 failed, 1 passed`：一個協定改動讓每個碰到它的情境同時變紅，訊息指名是哪個呼叫，不是哪個情境，讀的人一眼知道去哪裡找。檢查器自己也要看過它失敗：一個 `find: "db.commit()"` 的突變體得到 ``M8: `find` occurs 2 times in app/main.py; make it match exactly once``，exit 2；`--after` 想寫 `"steps -m sc_task_04"` 卻打成 `"steps -m sc_task_4"`，對照列的 after 欄變成 `error 5`（一個測試都沒選到），接著印出 `cannot compare: ...; fix that first`，exit 2。少了對照列，這個筆誤會讓每個突變體看起來都「沒被殺」，你會以為新套件很弱，其實它一個測試都沒跑。`--only` 的筆誤同理：`--only M1,M9` 回 `mutate.py: error: --only M1,M9: not every id is in mutants.yaml`、exit 2，不會悄悄只跑一個突變體還回 0。

## 底層機制：mutate.py 判什麼，pytest 怎麼找到 steps/ 裡的 step

`mutate.py` 對「無突變、M1 到 M7」逐一做同一件事：把專案複製到暫存目錄（略過 `.*`、`__pycache__`、`reports`），套上突變，再分別以 `-x` 跑 BEFORE 與 AFTER 兩組選擇。判準是 AFTER 殺死的集合包含 BEFORE 殺死的集合，不要求相等：新套件多殺的（M7）是改進，要求相等等於禁止遷移順手變好。「殺死」只認 exit 1。pytest 9.2.0 的 `ExitCode` 是 0 全過、1 有測試失敗、2 中斷（收集錯誤屬於這種）、3 內部錯誤、4 用法錯誤、5 沒收集到測試，另有 6（`MAX_WARNINGS_ERROR`）。如果把非 0 一律當成殺死，打錯字的選擇會讓每個突變體都被「殺死」，假的安心比假的警報更危險。每個突變體各複製一份專案，理由有三：[Ch 19](./19-tdd-by-hand.md) 的踩雷 4 量過，同一秒內改回同長度的檔案時，Python 會沿用舊的 timestamp `.pyc`，所以複製時略過 `__pycache__`，子程序也設 `PYTHONDONTWRITEBYTECODE=1`；跑到一半當掉，工作目錄不會留下被改壞的 `app/main.py`；之後要平行化也不必上鎖。`find` 必須剛好出現一次，因為有人重排程式碼後，一個沒套上的突變體在表上和「存活」長得一模一樣。pytest 這一側，[Ch 27](./27-ai-bdd-loop.md) 看過 step definition 就是名為 `pytestbdd_stepdef_<type>_<pattern>` 的 fixture，測試只看得到自己的模組、上層 `conftest.py` 與 plugin 裡的 fixture。`steps/task_steps.py` 不是 conftest，所以要用 `pytest_plugins` 把它註冊成 plugin。pytest 只在啟動時從命令列給的路徑（沒給就用 `testpaths`）以及它們底下名稱符合 `test*` 的子目錄載入「初始 conftest」（`_pytest/config/__init__.py` 的 `_set_initial_conftests`），而 `pytest_plugins` 只准寫在這些 conftest 裡，寫在別處自 pytest 4.0 起就是錯誤。把那行搬到 `steps/conftest.py` 試試：`pytest steps` 照樣 `4 passed`，因為 `steps/` 是命令列給的路徑；`pytest .` 就在收集階段失敗，`ERROR collecting steps`，訊息開頭是 `Defining 'pytest_plugins' in a non-top-level conftest is no longer supported`，接著要你搬到 rootdir 的 conftest，因為 `steps` 不符合 `test*`，要等到收集時才被載入。能不能過取決於怎麼呼叫 pytest，正是「在我機器上會過」的典型；根目錄的 `conftest.py` 一定是初始 conftest，所以那行放在那裡。

## 五、一個 wave 一個 wave 刪，CI 怎麼接

刪舊測試前要問的不是「新套件夠不夠」，而是「刪掉以後 CI 跑的東西，是不是殺得到刪掉以前殺得到的每一個突變體」。`--before` 填現在 CI 跑的東西（新舊兩套），`--after` 填刪掉之後剩下的。刪 `adding`：`--before "tests steps" --after "steps tests/test_tasks.py::test_complete_task tests/test_tasks.py::test_complete_missing_task"`，結果 `before kills 6, after kills 6; lost -; gained -; both missed M4`。刪掉兩個測試函式、帳本改成 `migrated`，盤點回 `1 files, 2 tests, 3 asserts, 0 problems`，`pytest -q` 是 `6 passed`。`completing` 照做，`--after steps` 一樣 `lost -`，於是 `git rm -r tests`、`testpaths` 只留 `steps`，盤點回 `0 files, 0 tests, 0 asserts, 0 problems`，`4 passed`。七個 commit 從下往上讀就是整段論證：複製、加工具（pytest-bdd、`pytest.ini`、glossary、`mutants.yaml` 與 `.claude/`；這個 commit 打上 `git tag migration-base`，第六節的 Stop hook 拿它當基準）、第一版翻譯、driver 修協定、帳本、刪 adding、刪 completing。每一步都能 checkout 回去重跑，和 [pstack Ch 14](../pstack/14-verification-principles.md) 說的「commit 順序本身是論證」同一個道理。這是 Martin Fowler 說的 [strangler fig](https://martinfowler.com/bliki/StranglerFigApplication.html)（絞殺榕）用在測試上，他在 2024-08-22 版的頁面裡把讓新舊並存、完成後就會刪掉的程式碼叫 transitional architecture，並說它值得那份成本；這裡的過渡架構是帳本、雙套件和下面的 migration gate。CI 延續 [cicd Ch 11](../../dev_tools/cicd/11-services-artifacts-concurrency.md) 的 CI v3，`.github/workflows/acceptance.yml` 多兩個 job。本段未實測，為理論預期行為：我們只用 PyYAML 確認它解析得出 `acceptance` 與 `migration-gate` 兩個 job（頂層的 `on` 照 YAML 1.1 讀成 `True`，Ch 22 提過），要驗證就推到 GitHub 看 Actions 頁面。`acceptance` 用 `actions/checkout@v7`、`actions/setup-python@v7` 裝好環境，依序執行 `mkdir -p reports`（Ch 27 量過目錄不存在時 cucumber JSON 會讓整次執行 exit 1）、`pytest -q --junitxml=reports/junit.xml --cucumberjson=reports/cucumber.json`、用 [Ch 26](./26-bdd-vs-tdd-artifacts.md) 的 `living_doc.py` 產生 `reports/living-doc.md`，最後以 `actions/upload-artifact@v7` 上傳整個 `reports/`；後兩步加 `if: always()`，紅燈時報表最有用。`migration-gate` 跑 `suite_inventory.py tests --map migration.yaml` 與 `mutate.py mutants.yaml --before tests --after steps`，最後一個 wave 遷完就刪掉這個 job。本機跑同樣的報表步驟，`living_doc.py` 印出 `4 scenarios, 0 failed`，四個情境各帶 `[PASS]` 與 tag（例如 `- [PASS] A new task starts open @sc_task_01 @smoke`），底下列出每一步。

## 六、讓 agent 做批量遷移：bdd-migrate skill 與 Stop hook

四個測試手工遷很快，四百個就是 agent 的工作了。agent 遷移有三條比做對更便宜的路：改產品程式碼讓兩套都綠、改 `mutants.yaml` 拿掉難殺的突變體、還有 lost 就宣布完成。skill 寫流程，`settings.json` 擋路，Stop hook 驗收。`.claude/skills/bdd-migrate/SKILL.md`：

```markdown
---
name: bdd-migrate
description: Move an existing pytest suite to pytest-bdd (features/, steps/, support/, drivers/) one module at a time, and prove with hand-written mutants that the new suite catches every bug the old one catches before anything is deleted. Use when asked to migrate tests to BDD or Gherkin, to turn existing tests into feature files and step definitions, or to replace an old test suite with scenarios.
---

# Migrate a suite to BDD

The old suite pins today's behaviour, bugs included. You translate it; you do not improve the app.
Never change app/, tests/, mutants.yaml or glossary.yaml: deny rules block the usual routes and the
Stop hook diffs them against the tag `migration-base`. Run each command on its own (no pipes, `;`, `&&`).

1. Inventory: `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/suite_inventory.py tests` numbers every assert `<test>:<n>`.
2. Ledger: copy templates/migration.yaml to `migration.yaml`. A wave is the old tests of one feature
   area, small enough for one pull request (e.g. adding tasks; completing tasks), state `both`.
   Every assert gets a destination: the `@sc_<area>_<NN>` tag of the scenario that checks it,
   `driver` for wire details (status codes, field names and types), or `drop: <why>`.
3. Features: one file per wave in `features/`, following the gherkin-style rules (GH-01..GH-12) and
   glossary.yaml. `.venv/bin/python .claude/skills/gherkin-style/scripts/gherkin_lint.py --glossary glossary.yaml features`
   must end in `0 problems`. Register every new tag under `markers` in pytest.ini.
4. Layers: `steps/test_features.py` only calls `scenarios("../features")`. Step definitions live in
   `steps/<noun>_steps.py`, grouped by domain noun, one DSL call each. The DSL lives in `support/`,
   all HTTP in `drivers/`. The driver rejects every answer an old assert would have rejected.
   The root `conftest.py` lists the step and fixture modules in `pytest_plugins`.
5. Checks, in order, until all three pass:
   - `.venv/bin/python -m pytest -q` (old and new suites together)
   - `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/suite_inventory.py tests --map migration.yaml`
   - `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/mutate.py mutants.yaml --before tests --after steps`
   A mutant listed under `lost` is a bug the old suite catches and yours misses: find the old assert
   that catches it and move that check into a step or the driver.

Done when the three checks pass. Do not delete old tests; report the mutate table and which waves
are ready. A human deletes a wave's tests and marks it `migrated`.
```

`templates/migration.yaml` 是帳本的空殼：兩行註解、一個 `<module>: {state: both, tests: [...]}` 的 wave、一個 `<test name>:<n>: "@sc_<area>_<NN>"` 的範例。`.claude/settings.json` 的 deny 有 `Edit(/app/**)`、`Edit(/tests/**)`、`Edit(/mutants.yaml)`、`Edit(/glossary.yaml)`、`Bash(rm *)`、`Bash(git rm *)`、`Bash(git tag *)`、`Bash(git commit *)`。沒有寫 `Write(...)`，因為官方 permissions 文件說檔案權限只比對 `Edit(path)` 與 `Read(path)`，`Write(path)` 規則永遠不會命中，而 `Edit` 規則涵蓋所有內建的編輯工具。`Bash(rm *)` 也不是多餘的：`acceptEdits` 模式會在專案目錄內自動核准 `rm`、`mv`、`sed`，deny 規則則在每一種模式下都擋。Stop hook 照 Ch 27 用 exec form 掛上（`command` 是 `${CLAUDE_PROJECT_DIR}/.venv/bin/python`，`args` 是 `["${CLAUDE_PROJECT_DIR}/.claude/hooks/migration_stop.py"]`，`timeout` 180 秒），`.claude/hooks/migration_stop.py`：

```python
"""migration_stop.py: Stop hook for bdd-migrate. Blocks the stop while a locked path differs from the tag
migration-base or a check fails; with stop_hook_active set it lets Claude stop and shows what still fails."""
import json, os, subprocess, sys

S, PY = ".claude/skills/bdd-migrate/scripts", sys.executable
CHECKS = {"locked files differ from migration-base; restore them":
              ["git", "diff", "--exit-code", "--name-only", "migration-base", "--", "app", "tests", "mutants.yaml", "glossary.yaml"],
          "pytest failed": [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
          "suite_inventory.py failed": [PY, f"{S}/suite_inventory.py", "tests", "--map", "migration.yaml"],
          "mutate.py failed": [PY, f"{S}/mutate.py", "mutants.yaml", "--before", "tests", "--after", "steps"]}
if "--help" in sys.argv:
    sys.exit(print(__doc__))
event = json.load(sys.stdin)
for label, cmd in CHECKS.items():
    run = subprocess.run(cmd, cwd=os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd", "."), capture_output=True, text=True)
    if run.returncode:
        why = label + ":\n" + "\n".join((run.stdout.strip() or run.stderr.strip()).splitlines()[-12:])
        out = {"systemMessage": f"bdd-migrate is not done: {why}"} if event.get("stop_hook_active") else {"decision": "block", "reason": why}
        print(json.dumps(out))
        break
```

比對的對象是人打的 tag，不是工作目錄：agent 若把改動 commit 掉，`git status` 一片乾淨，`git diff migration-base` 照樣看得到。用 `git worktree` 把帳本那個 commit 拉出來當測試台（`.venv` 用 symlink 連回本章的 venv），餵 `{"hook_event_name":"Stop","stop_hook_active":false}`：driver 是修好的版本時沒有輸出、exit 0；換回第一版 driver，輸出 `{"decision": "block", "reason": "mutate.py failed:\nmutant  before ...`，整張表都在 reason 裡；同一個事件把 `stop_hook_active` 改成 true，得到以 `bdd-migrate is not done: mutate.py failed:` 開頭的 `systemMessage`，放 Claude 收工；在 `app/main.py` 改一個錯誤訊息，得到 `{"decision": "block", "reason": "locked files differ from migration-base; restore them:\napp/main.py"}`。同一道檢查也會擋到人：刪掉一個 wave 的舊測試並 commit 之後，下一次收工得到 `{"decision": "block", "reason": "locked files differ from migration-base; restore them:\ntests/test_tasks.py"}`，等於叫 agent 把剛刪的測試加回來，所以每刪一個 wave 就由人把 tag 移過去（`git tag -f migration-base`），移完 hook 就沒有輸出了；最後一個 wave 刪完，`--before tests` 沒有測試可跑，`mutate.py` 只會回 `cannot compare`，hook 要和 CI 的 `migration-gate` 一起拿掉。餵它非 JSON，印出 `JSONDecodeError` 的 traceback、exit 1。對 Stop hook 來說這是不擋路的錯誤：官方 hooks 文件寫明 `onFailure` 對 Stop hook 沒有作用，逾時也算失敗，依文件推論，hook 當掉或跑超過 180 秒，Claude 照樣收工；這條路本章沒有在 session 裡觸發過。

> 以下是實際執行紀錄（節錄），每次執行結果不同；2026-11-02，Claude Code 2.1.295，claude-opus-5-5。

| 次 | 起點與提示 | 結果 | 工具呼叫／num_turns／費用／時間 |
|---|---|---|---|
| 1 | 遷移前的 tasktrack；「Migrate the whole old suite in tests/ (both modules)」 | 一份 `tasks.feature`、一個 wave；三項檢查第一次就過；7 個突變體全殺（多殺 M4、M7） | 46／48／$0.85／149 s |
| 2 | 本章第一版翻譯（lost M5 M6）；「Someone already started … Finish the migration.」 | 只改 `drivers/api.py` 與帳本註解；`lost -`，殺 6 個，M4 仍是共同盲點 | 41／42／$0.57／128 s |
| 3 | 同第 1 次，但 SKILL.md 第 2 步改成現在的 wave 定義 | 兩份 feature、兩個 wave；7 個全殺 | 43／45／$0.80／158 s |

三次都用 lab 裡的 `run-agent.sh`：`claude -p` 加 `--max-turns 20`、`--tools "Read,Edit,Write,Glob,Grep,Bash,Skill"`、`--allowedTools "Read,Edit,Write,Glob,Grep,Skill,Bash(.venv/bin/python *)"`、`--permission-mode acceptEdits --permission-prompts none`、`--setting-sources project,local`、`--no-session-persistence --max-budget-usd 5`，stdin 接 `/dev/null`，以 stream-json 加 `--include-hook-events` 記錄。每個專案各自 `git init`，`.venv` 連到本章的 venv，另有一份三句話的 CLAUDE.md（專案是什麼、Python 用 `.venv/bin/python`、`tests/` 正用 bdd-migrate skill 遷移）。實跑時的 hook 是同一套檢查的較長寫法，`suite_inventory.py` 多一個沒用到的 `--features` 選項與一條「每個測試恰屬一個 wave」的檢查、還沒有同名檢查，`mutate.py` 多一個 `--dry-run`、還不檢查 `--only` 的 id，三份存於 lab 的 `runs/at-run-time/`。result 裡的 num_turns 約等於工具呼叫數加一或加二，看來和 `--max-turns` 數的不是同一種單位（推論，沒有拆開驗證），20 沒擋住 42 到 48（Ch 22 也是 20 對 25）。三次的 Stop hook 各跑一次，都是 exit 0、沒有輸出，「擋下」只在前面用管線測過。值得記的有四件事。第一，第 1 次把「both modules」讀成兩個 Python 模組，找不到第二個，就把四個測試塞進同一個 wave，還在報告裡指出「You said two modules, but there is only one」；我們把第 2 步改成現在的「A wave is the old tests of one feature area」，第 3 次就切成兩個 wave（description 裡的「one module at a time」沒改，三次都是它）。第二，三次都在第一次寫檔之前讀了 `mutants.yaml`（第 6、16、6 次工具呼叫，第一次寫檔在第 28、33、25 次），第 1、3 次寫出「完成一筆、另一筆仍 open」的情境，正好殺死 M4。突變體被寫測試的一方看過，就不再是盲測的樣本，所以我們另外留了三個 agent 沒看過的 holdout 突變體（清單藏起已完成的任務、完成兩次會把狀態切回去、404 的訊息改字）：舊套件殺 0 個，本章的參考版與三次 agent 的版本都只殺 H1，看不出誰比較強。第三，第 2 次修對了層：只有 404 是 `NotFound`，其餘非 200 都丟它新增的 `BadAnswer`，還加上「建立與完成的回應要和請求一致」的檢查，並自己寫了 9 個額外突變體放在 `/tmp/bdd-scratch/` 驗證。專案外的目錄寫得進去：依官方 permissions 文件，`acceptEdits` 只放行專案目錄內的寫入，而 `--allowedTools` 裡不帶路徑的 `Write` 核准每一次 Write 呼叫，要限制範圍得改用帶路徑的 `Edit(...)` 規則（本章沒有實測這個寫法）。它也明說 M4 不歸它補：「the skill says to translate the old suite, and M4 isn't a loss from it」。第四，`Bash(git tag *)` 連唯讀的 `git tag -l` 都擋，三次都撞到；第 1 次改用 `git log --decorate` 確認 tag，第 2 次直接回報確認不了。第 1 次還指出 Ch 28 的 gherkin-style 第 5 步寫死了 `tests/dsl.py`，這是把一個專案的 skill 搬到另一個專案時夾帶過去的名詞；它也照抄了舊 conftest 的 `tempfile.mktemp` 和舊測試的 9999，這種事 lint、帳本、突變體都看不到，只能靠 review。

## 對比與取捨

| 做法 | 怎麼說明「沒變弱」 | 代價或盲點 | 適用 |
|---|---|---|---|
| 一次重寫，兩套都綠就刪 | 兩套都綠 | 第四節的第一版也是綠的 | 可以丟的原型 |
| 只有新功能寫 BDD | 不需要說明 | 兩種風格永遠並存，舊的慢慢沒人維護 | 快退役的模組 |
| 比 coverage | 執行到同樣的程式行 | 執行到不等於檢查到，沒有 assert 的測試 coverage 一樣高 | 只當下限 |
| 手寫突變體（本章） | AFTER 殺死的 ⊇ BEFORE 殺死的 | 要人寫、程式改了要跟著改；只是樣本 | 逐 wave 遷移 |
| 突變工具（例如 mutmut） | 自動產生大量突變體 | 慢，還要人工分辨等價突變體 | 大套件、夜間 CI |

我們選手寫突變體，因為遷移要回答的問題很窄：舊套件抓得到的，新套件也要抓得到。突變體從舊 assert 反推最直接，七個就夠用，一輪 6 秒，放得進 Stop hook。突變工具在 [pstack Ch 14](../pstack/14-verification-principles.md) 的進階提過，拿來找「兩套都漏」的盲點比較合適，那是遷移之後的事。

## 踩雷集錦

1. **錯誤直覺：兩套都綠，新套件就等價** → 正確認識：第一版翻譯 `8 passed`、帳本 `0 problems`，`mutate.py` 照樣報 `lost M5 M6`。綠燈只說明兩套都沒在「現在的程式」裡找到問題，證明不了程式出錯時兩套反應一樣。
2. **錯誤直覺：Gherkin 不寫狀態碼，狀態碼就不用測** → 正確認識：feature 不寫，driver 要寫。`status_code == 200` 與 id 的型別放進 driver 之後，一個 201 讓三個情境一起紅，訊息直接指名 `POST /tasks`。
3. **錯誤直覺：`pytest_plugins` 放在 `steps/conftest.py` 也一樣** → 正確認識：`pytest steps` 會過，`pytest .` 會在收集階段報錯。能不能過取決於呼叫方式，所以一律放根目錄的 `conftest.py`。
4. **錯誤直覺：突變體存活就是測試太弱** → 正確認識：先確認突變體真的套上了、選擇真的跑到了測試。`find` 出現兩次是 exit 2；`-m` 打錯字讓對照列變成 `error 5`。少了這兩道檢查，表上的「存活」可能只是「根本沒測」。
5. **錯誤直覺：有 Stop hook，agent 就不可能紅著收工** → 正確認識：Stop hook 當掉或逾時都不會擋，`onFailure` 對它沒有作用。突變體多到 180 秒跑不完時，把 `mutate.py` 移到 CI 的 `migration-gate`，hook 只留便宜的檢查。

## 進階：突變體的來源、範圍與成本

- **holdout 突變體**：`mutants.yaml` 給 agent 看，另一份放在專案外、只有人跑。看過的那份拿來引導，沒看過的那份拿來評估，道理和機器學習把測試集藏起來一樣。本章的 H1 到 H3 只有三個，結論只能說「沒看出差別」。
- **只突變改到的程式**：Petrović 與 Ivanković 描述 Google 以 diff 為範圍，只突變改到、有 statement coverage、又不是 arid（他們判定不值得突變）的行，每行至多一個突變體，再把結果放進強制的 code review。套到遷移上，就是每個 wave 的 PR 只對那個功能區塊的程式產生突變體。

## 動手練習

1. 寫一個殺死 M4 的情境（兩筆任務，完成其中一筆，另一筆仍是 open），重跑 `mutate.py` 確認 `both missed` 變成 `-`。它該放進哪個 wave？帳本要不要多一列？
2. H2（完成兩次會把狀態切回去）兩套都沒抓到。先回答產品問題：完成一個已經完成的任務，應該發生什麼事？答案寫成情境，這張問題卡正是 [Ch 29](./29-example-mapping.md) Example Mapping 的材料。
3. 第 1 次實跑寫出的 `support/fixtures.py` 有一行 `_db_path = tempfile.mktemp(suffix=".db")`，`support/dsl.py` 有一行 `UNKNOWN_TASK_ID = 9999`，分別抄自舊 conftest 與舊測試。為什麼三道檢查都沒抓到？你會寫成 Ch 9 的 rule file、lint，還是 review checklist？

## 本章重點整理

- brownfield 導入 BDD 的核心問題是「新的 pin 是否和舊的一樣牢」：用 AST 盤點每個 assert、寫進遷移帳本、交代去處（情境、driver 或刪除），再分 wave 推進。分層讓每個舊 assert 有家：業務結果進 Then，協定細節（狀態碼、欄位、型別）進 driver，step 依領域概念組織，`pytest_plugins` 只寫在根目錄的 conftest。
- `mutate.py` 的判準是 AFTER 殺死的 ⊇ BEFORE 殺死的；要有對照列、突變體要剛好套上一次、只認 exit 1。刪一個 wave 前，拿「刪掉之後剩下的」去比「現在跑的」。
- agent 做得了批量遷移：skill 給流程，deny 規則擋常見的路，Stop hook 對著 tag 驗收。三次實跑都通過，真正的發現在 skill 的措辭、權限規則的副作用，以及「看過突變體」這件事。

## 自我檢核

- [ ] 不看本章，說出 `mutate.py` 回 exit 0、1、2 各代表什麼，以及判準為什麼是包含而不是相等。
- [ ] 第一版翻譯兩套都綠，卻弄丟了 M5 和 M6。各是哪一個舊 assert 在保護它？修好之後由哪一層負責？
- [ ] 帳本的 key 為什麼用 `<test>:<n>` 不用行號？帳本 `0 problems` 證明了什麼、沒證明什麼？
- [ ] `pytest_plugins` 放在 `steps/conftest.py` 時，哪種呼叫會過、哪種會壞？用「初始 conftest」解釋。
- [ ] 讓 agent 讀得到 `mutants.yaml` 有什麼代價？本章怎麼估計這個代價，這個估計有什麼限制？

## 延伸閱讀

- **《Working Effectively with Legacy Code》** — Michael Feathers（Prentice Hall／Pearson，2004）
  - **讀哪裡**：第 13 章「I Need to Make a Change, but I Don't Know What Tests to Write」（特徵測試怎麼寫）與第 23 章「How Do I Know That I'm Not Breaking Anything?」。**學到什麼**：特徵測試記下的是程式現在實際的行為，不是它該有的行為。**和本章的關聯**：舊套件就是一組特徵測試，本章處理書裡沒談的下一步：換掉這組特徵測試。書超過 20 年、例子是 Java 與 C++，seam 與特徵測試的論證至今適用。
- **[Cucumber — Anti-patterns](https://cucumber.io/docs/guides/anti-patterns/)**（官方文件）
  - **讀哪裡**：「Feature-coupled step definitions」與「How to decouple steps & step definitions」。**學到什麼**：「Organise your steps by domain concept」，step 檔用領域名稱命名，不用 feature 或情境的名稱。**和本章的關聯**：`steps/task_steps.py` 只有一個檔的依據；同頁的「Conjunction steps」可以對照 Ch 28 的 GH-04。
- **[Goran Petrović、Marko Ivanković — State of Mutation Testing at Google](https://research.google/pubs/state-of-mutation-testing-at-google/)**（ICSE-SEIP，2018）
  - **讀哪裡**：摘要、第 3 節（probabilistic diff-based 分析）與第 4 節（arid node）。**學到什麼**：以 diff 為範圍、排除沒有 coverage 與 arid 的行，讓突變測試在大型程式庫跑得動，並把結果放進強制的 code review。**和本章的關聯**：本章的突變體是手寫、整個專案跑；規模變大時，它說明怎麼縮小範圍、怎麼讓人真的去看存活的突變體。讀之前先弄懂本章第四節。
- **[Spec Kit — Adopting Spec Kit in an Existing Project](https://github.com/github/spec-kit/blob/main/docs/guides/existing-projects.md)**（官方文件，1.2.5 的原始碼包裡有同一份）
  - **讀哪裡**：第 3 節「Choose a Bounded First Change」、第 4 節「Plan Against the Repository」與第 5 節「Decide How Specs Will Age」。**學到什麼**：init 不替既有行為推導規格、第一個 feature 要有邊界、converge 排在 implement 之後。**和本章的關聯**：有邊界的第一步和本章的 wave 是同一個判斷；搭配 SDD Ch 28 讀，可以看清 converge 的方向是讓程式追上規格。

遷移做完，`features/` 成了這個專案「現在的行為」唯一的說法；它和每次改動留下的 plan 要怎麼分開保存、誰能改哪一份，是下一章的主題。

→ [Ch 33 Truth 與 Plan：兩種 artifact 的生命週期](./33-truth-vs-plan.md)
