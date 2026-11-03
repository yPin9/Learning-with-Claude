# Ch 8 — 需求翻譯三明治：Sales、PM、RD 之間掉了什麼

> **目標**：跟著一個客戶請求（「客戶要能預約每週固定例會」）走過 Sales、PM、RD 三手，標出意思在哪裡掉的；用真的程式證明「規格錯了，測試照樣全綠」，以及為什麼任何寫程式的 skill 都修不到這一層；學會 Example Mapping（Matt Wynne，2015）的四色卡片、25 分鐘 timebox 與準備度訊號，寫出 `example_map.py`：檢查每條規則都有例子、未決問題擋住準備度、產生 `Rule:` 加 `Example:` 的 Gherkin 骨架。

> **環境**：課程 venv（Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、gherkin-official 42.0.1、PyYAML 6.0.3）、Claude Code 2.1.295（AI 主持人實驗用一次 `claude -p`，2026-11-02 的實際執行紀錄，節錄，每次執行結果不同）、macOS 26.6 arm64。lab 目錄 `/tmp/lab/aibdd/ch08-sandwich/` 從空目錄開始，Python 指令先 `source /tmp/lab/aibdd/.venv/bin/activate`。

## 為什麼需要這個？

[Ch 7](./07-messy-baseline.md) 的 autopilot 掛在 B 組探針，原因不是程式寫錯，而是團隊的規則從來沒進到 prompt。直覺的反應是換一個更好的 skill、更強的模型。這一章要說服你：很多錯在第一行程式碼之前就已經犯下，而且犯在人和人之間。

repo 談過交接的損耗。SDD [Ch 15](../spec_driven_development/15-ubiquitous-language.md) 的「在這之前，人們怎麼做」寫 BA → PM → Dev → Test 一路交接，一個「理賠申請」最後有五個名字；SDD [Ch 21](../spec_driven_development/21-event-storming.md) 說 BA → UML → 程式是兩次翻譯、兩次語意損耗；SDD [Ch 33](../spec_driven_development/33-ddd-sdd-same-fight.md) 的雷 2、雷 5 說規格工具只會放大業務理解，不能取代它。缺的是三樣東西：**Sales 這個角色**與夾在中間的 PM（[Ch 1](./01-course-map.md) 對照的外部課綱 2-3 單元稱為 Sales/PM/RD 的「三明治問題」，本課叫它「需求翻譯三明治」）、**一個看得見的證明**（為什麼寫程式的 agent 修不到這一層），以及**一個能讓三方一起產出可執行例子的做法**。repo 裡還沒有 Example Mapping，這一章補上。

## 先建立直覺：三明治與傳話遊戲

```
  客戶 Carol ──原信──▶ Sales ──CRM 一行──▶ PM ──AC1–AC3──▶ RD ──程式＋測試──▶ 全綠
                         │                    │                  │
                  掉：撞期怎麼辦、假日、      夾在中間：           照規格做，
                      「整年」              上面是 Sales 的承諾      驗的是規格，
                  多：「已答應下個月上線」    「下個月上線」          不是 Carol
                                          下面是 RD 的工時
                                          → 自己決定 12 週、整批拒絕
```

像傳話遊戲：每一手都只聽得到上一手。但這個比喻有兩處不成立。第一，傳話遊戲的失真是隨機的；三明治的失真是**系統性**的，每個角色都朝自己的壓力修剪：Sales 要成交、PM 要守時程、RD 要符合規格。第二，軟體的每一手都有書面產物，**只要原話跟著需求走，損耗就追得回來**。這一章的工具都在做這件事：讓原話、規則、例子、問題一起走到最後。

## 一、一個請求的旅程

三份上游產物放在 `sandwich/` 底下，內容是我們為本章構造的情境，請照抄成三個檔案：

```
$ head -n 20 sandwich/*.md
==> sandwich/01-customer.md <==
寄件者：Carol（宏遠科技 行政）
主旨：會議室預約

我們每週一早上 10 點都在 Taipei 開週會，每次都要跟別的部門搶。
能不能一次把整年的週一都訂起來？如果哪一週已經被別人訂走，
那週我們可以改去 Tokyo，但至少要讓我們知道是哪幾週。
國定假日那週不用訂。

==> sandwich/02-sales-crm.md <==
客戶：宏遠科技（窗口 Carol）
需求：要能預約每週固定例會（每週一 10:00，Taipei）。
備註：客戶很急，已答應下個月上線。

==> sandwich/03-pm-spec.md <==
使用者故事：身為使用者，我可以建立每週重複的預約，不用每週手動預約。
AC1 可指定會議室、開始日期、時段與重複週數（1–12 週）。
AC2 系統一次建立所有週的預約。
AC3 任何一週與既有預約衝突時，整批拒絕，不建立任何預約。
```

| 交接 | 原話 | 寫下來的 | 掉了或變了 | 誰原本抓得到 |
|---|---|---|---|---|
| Carol → Sales | 整年；被訂走的週可改 Tokyo，至少要知道哪幾週；假日不用訂 | 每週固定例會（週一 10:00，Taipei） | 期間、撞期處理、假日都掉了；多了「已答應下個月上線」 | Sales 把原信附給 PM |
| Sales → PM | （PM 沒看過原信） | AC1 1–12 週、AC3 整批拒絕 | 「整年」縮成 12 週，為了趕上 Sales 的承諾；「讓我們知道」變成「整批拒絕」 | 問 Carol 一句「撞期時你希望怎樣」 |
| PM → RD | AC1–AC3 | `weekly.py` 與兩個 scenario | 什麼都沒掉，RD 忠實照做 | 沒有人：這一層已經看不到原信 |

PM 是三明治的餡：上面壓著 Sales 的承諾，下面頂著 RD 的工時，於是自己做了兩個業務決定（12 週、整批拒絕），而且沒有回頭問。注意最後一列：RD 沒有犯錯。

## 二、實驗：規格錯了，測試照樣全綠

RD 的實作只照 AC1–AC3 寫，存成 lab 根目錄的 `weekly.py`（例會需要日期，本課共用的會議室領域原本只處理同一天的 `HH:MM`；衝突訊息也從 R1 的 `Taipei is taken 10:00-11:00 by Bob` 改成帶日期的 `Taipei is taken on 2026-11-16 by Bob`）：

```python
"""weekly.py：RD 照 PM 規格 AC1–AC3 實作的每週例會（只有記憶體，沒有 API）。"""
from dataclasses import dataclass
from datetime import date, time, timedelta


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Booking:
    room: str
    day: date
    start: time
    end: time
    by: str


class Calendar:
    def __init__(self):
        self.bookings: list[Booking] = []

    def taken(self, room, day, start, end):  # 半開區間 [start, end)：10:00–11:00 與 11:00–12:00 不重疊
        return next((b for b in self.bookings
                     if (b.room, b.day) == (room, day) and b.start < end and start < b.end), None)

    def book(self, room, day, start, end, by):
        if hit := self.taken(room, day, start, end):
            raise Conflict(f"{room} is taken on {day} by {hit.by}")
        self.bookings.append(booking := Booking(room, day, start, end, by))
        return booking

    def book_weekly(self, room, first, start, end, by, weeks):
        if not 1 <= weeks <= 12:  # AC1：1–12 週
            raise ValueError("weeks must be between 1 and 12")
        days = [first + timedelta(weeks=i) for i in range(weeks)]
        for day in days:  # AC3：任何一週衝突就整批拒絕
            if hit := self.taken(room, day, start, end):
                raise Conflict(f"{room} is taken on {day} by {hit.by}")
        return [self.book(room, day, start, end, by) for day in days]  # AC2：一次建立
```

RD 從 AC 寫的 `features/pm_spec.feature`：

```gherkin
Feature: Weekly meeting (RD's reading of the PM spec)

  Scenario: A weekly series books every week
    Given room "Taipei" is free all month
    When Carol books "Taipei" every week from 2026-11-09 10:00 to 11:00 for 4 weeks
    Then 4 weekly bookings are created

  Scenario: A taken week rejects the whole series
    Given Bob has booked "Taipei" on 2026-11-16 from 10:00 to 11:00
    When Carol books "Taipei" every week from 2026-11-09 10:00 to 11:00 for 4 weeks
    Then the series is rejected because "Taipei is taken on 2026-11-16 by Bob"
    And 0 weekly bookings are created
```

`tests/test_weekly.py` 是一份 step definitions，綁 `features/` 底下所有檔案，等一下的客戶版例子也用它（`scenarios()` 接受目錄，pytest-bdd 的綁定細節在 [Ch 27](./27-ai-bdd-loop.md)）：

```python
"""一份 step definitions，同時綁 features/ 底下的每一個 feature 檔。"""
from datetime import date, time

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from weekly import Calendar, Conflict

scenarios("../features")


@pytest.fixture
def calendar():
    return Calendar()


@given(parsers.parse('room "{room}" is free all month'))
def free(calendar, room):
    assert not [b for b in calendar.bookings if b.room == room]


@given(parsers.parse('{who} has booked "{room}" on {day} from {start} to {end}'))
def booked(calendar, who, room, day, start, end):
    calendar.book(room, date.fromisoformat(day), time.fromisoformat(start), time.fromisoformat(end), who)


@when(parsers.parse('Carol books "{room}" every week from {first} {start} to {end} for {weeks:d} weeks'),
      target_fixture="outcome")
def book_weekly(calendar, room, first, start, end, weeks):
    try:
        args = (room, date.fromisoformat(first), time.fromisoformat(start), time.fromisoformat(end))
        return {"result": calendar.book_weekly(*args, "Carol", weeks), "error": None}
    except Conflict as e:
        return {"result": None, "error": str(e)}


@then(parsers.parse("{n:d} weekly bookings are created"))
def created(calendar, n):
    assert len([b for b in calendar.bookings if b.by == "Carol"]) == n


@then(parsers.parse('the series is rejected because "{reason}"'))
def rejected(outcome, reason):
    assert outcome["error"] == reason


@then(parsers.parse("{day} is reported as taken by {who}"))
def reported(outcome, day, who):
    assert outcome["error"] is None, f"the whole series was rejected: {outcome['error']}"
    assert (date.fromisoformat(day), who) in outcome["result"].skipped
```

`pytest.ini` 是四行：`[pytest]`、`pythonpath = .`、`testpaths = tests`、`addopts = -p no:cacheprovider`。執行 `python -m pytest -v`，兩個 scenario 都 `PASSED`，`2 passed in 0.01s`。全綠。現在用 Carol 真正會遇到的情況跑一次：Bob 早就訂了 11/16（週一）那一場。

```bash
python -c "
from datetime import date, time; from weekly import Calendar, Conflict
cal = Calendar(); cal.book('Taipei', date(2026, 11, 16), time(10), time(11), 'Bob')
try: cal.book_weekly('Taipei', date(2026, 11, 9), time(10), time(11), 'Carol', 4)
except Conflict as e: print('Conflict:', e)
print('Carol bookings:', [b.day.isoformat() for b in cal.bookings if b.by == 'Carol'])"
```

```
Conflict: Taipei is taken on 2026-11-16 by Bob
Carol bookings: []
```

Carol 要的是「訂到三週，告訴我 11/16 不行」。她拿到的是一筆都沒有，而 RD 的測試全綠。

## 底層機制：為什麼寫程式的 skill 修不到這一層

軟體工程常引用 Boehm 1984 年的論文來區分兩件事：**驗證**（verification）問「產品有沒有照規格做對」，**確認**（validation）問「做的是不是使用者要的產品」。寫程式這一側的每一道檢查都是驗證：

```
  Carol 的意思 ──[確認：要靠人＋原話]──▶ 規格 AC1–AC3 ──[驗證：測試、lint、review、coding skill]──▶ 程式
         ▲                                    │
         └────── 只有這條路能發現 AC3 錯了 ──────┘   測試的「正確答案」（oracle）就是規格本身
```

測試的斷言來自規格，所以規格錯的時候，測試、lint、code review、Ch 7 的 `bdd-monolith` 會**一致地**同意那個錯。`bdd-monolith` 的 Phase 1 寫「If the request leaves a rule open, pick the most reasonable reading」，可是 AC3 沒有留白，它寫得很清楚，只是清楚地錯了；skill 看不到 Carol 的原信，就沒有東西可以比。SDD [Ch 8](../spec_driven_development/08-why-requirements-hard.md) 的「為什麼 AI 代理程式讓這些病變得更危險」說的是同一件事的另一面：agent 不會停下來問，只會選一種解讀繼續寫。

更直接的證明：我們在複本 `variant-customer/` 裡照 Carol 的讀法改寫 `book_weekly`（被訂走的週跳過並回報、其他週照訂，回傳一個帶 `booked` 與 `skipped` 的 `Series`；約十行，就是動手練習第 2 題，這裡先不給答案）。等第四節產生出客戶版的 `features/weekly.feature` 之後，兩種實作各跑同樣五個 scenario：

| 實作 | PM 的 AC3 scenario | Carol 的 W2 例子 | 結果 |
|---|---|---|---|
| RD（照 AC3） | PASSED | FAILED：`assert 0 == 3` | `1 failed, 4 passed` |
| 客戶版（照 W2） | FAILED：`assert None == 'Taipei is taken on 2026-11-16 by Bob'` | PASSED | `1 failed, 4 passed` |

**沒有任何一種實作能讓兩份規格同時變綠**，因為衝突在需求裡，不在程式裡。選哪一邊是業務決定，要由聽過 Carol 原話、有權決定的人來做。agent 能做的是把衝突攤開，不能替人選。SDD [Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的「BMAD-METHOD 的 spec-first persona 分工」也是同一條鏈：PM persona → Architect → Developer，每個轉換點都能 review，但如果客戶原話沒有一起進鏈，review 比的只是譯本對譯本。

## 三、Example Mapping：把對話變成四色卡片

Example Mapping 是 Matt Wynne 在 2015 年 12 月 8 日的 Cucumber 部落格文章〈Introducing Example Mapping〉提出的。在它之前，很多團隊的做法是傳文件，或開一場 Wynne 形容為沒有結構、拖太久又無聊的需求會：SDD [Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的錯誤直覺 4 說 BDD 真正的價值是 Three Amigos（業務、開發、測試坐下來對齊），但沒說這場對話怎麼開才不會變成閒聊。Example Mapping 給它一個桌面和一個時限。桌上四種卡片：**黃卡**是要討論的那張 story，**藍卡**是已知的規則或驗收條件，**綠卡**是說明某條規則的具體例子，**紅卡**是在場沒有人答得出來的問題。

規矩只有幾條。在場的人至少是「three amigos: a developer, a tester and a product person」；我們的情境多請一位 Sales，因為原信在他手上。一張大小適中的 story 大約 25 分鐘排完，時間到就用大拇指投票（thumb-vote）決定能不能開工；還剩幾個小問題時，Wynne 讓大家投票決定能不能邊做邊解。例子取名用《六人行》的集名格式：「The one where Bob already has the second Monday」，先講清楚情節，Given／When／Then 之後再寫。Wynne 也給了讀桌面的訊號：滿桌紅卡表示「we still have a lot to learn about this story」；滿桌藍卡表示 story 又大又複雜，也許該切；一條規則底下例子特別多，那條規則可能太複雜，裡面藏著好幾條規則。會後的形式化，Steve Tooke（2018）的建議是：「Have the person who came to the meeting with the least understanding of the story write up the “minutes” of the meeting as Gherkin scenarios and share back to check understanding.」

情境裡那場 25 分鐘的會結束時，桌面長這樣（和第一節一樣是構造的）：

```
 [黃] 預約每週固定例會
 [藍] W1 連訂 N 週 ─ [綠] Carol books four Mondays     [藍] W2 撞期的週回報、其他照訂 ─ [綠] Bob already has the second Monday
 [藍] R1 半開區間  ─ [綠] Bob's meeting ends as Carol's starts   [藍] W3 跳過國定假日 ─ （還沒有例子）
 [紅] Q1 最多幾週？「整年」vs 12 週（Sales）   Q2 撞期要不要自動改訂 Tokyo？（PM）   Q3 假日表從哪來？（PM）
```

W 開頭的是這張 story 的新規則，R1 是全課沿用的「同一間不能重疊」。注意 W2 和 PM 的 AC3 正面衝突：會議桌上，Sales 唸出 Carol 的原信，衝突在寫程式前就浮上來了。

## 四、example_map.yaml 與 example_map.py

把桌面存成 YAML，就能用腳本檢查準備度、產生 Gherkin。這是 `maps/weekly.v1.yaml`，也就是會議結束時的樣子：

```yaml
# maps/weekly.v1.yaml：25 分鐘 Example Mapping 結束時的桌面
story: 預約每週固定例會
source: sandwich/01-customer.md（Carol 的原信，Sales 帶進會議室）
rules:
  - id: W1
    text: 每週同一天、同一時段、同一間會議室，連續訂 N 週
    examples:
      - id: sc_weekly_01
        title: The one where Carol books four Mondays
        given: room "Taipei" is free all month
        when: Carol books "Taipei" every week from 2026-11-09 10:00 to 11:00 for 4 weeks
        then: 4 weekly bookings are created
  - id: W2
    text: 某一週已被別人訂走時，其他週照訂，被訂走的那週要回報
    examples:
      - id: sc_weekly_02
        title: The one where Bob already has the second Monday
        given: Bob has booked "Taipei" on 2026-11-16 from 10:00 to 11:00
        when: Carol books "Taipei" every week from 2026-11-09 10:00 to 11:00 for 4 weeks
        then:
          - 3 weekly bookings are created
          - 2026-11-16 is reported as taken by Bob
  - id: R1
    text: 每一週各自套用「同一間不能重疊」，區間是半開的 [start, end)
    examples:
      - id: sc_weekly_03
        title: The one where Bob's meeting ends as Carol's starts
  - id: W3
    text: 國定假日那週跳過
    examples: []
questions:
  - id: Q1
    text: 最多訂幾週？Carol 說「整年」，PM 規格寫 1–12 週
    owner: Sales
    blocks: [W1]
    answer:
  - id: Q2
    text: 被訂走的那週要不要自動改訂 Tokyo？
    owner: PM
    blocks: [W2]
    answer:
  - id: Q3
    text: 國定假日表從哪裡來？誰維護？
    owner: PM
    blocks: [W3]
    answer:
```

格式的取捨：`source` 讓原話跟著走；例子的 `id` 會變成 Gherkin tag 與 pytest marker（pytest-bdd 9 把 `@sc_weekly_02` 轉成 marker，`-m sc_weekly_02` 就能單獨跑它）。限定小寫字母、數字與底線是全課慣例，不是 pytest 的限制（`-m` 也選得到 `@sc-weekly-02`）：同一個 ID 也可能寫成 Python 的 `@pytest.mark.sc_weekly_02`，那裡必須是合法識別字（[Ch 26](./26-bdd-vs-tdd-artifacts.md) 的關鍵字巡禮實測了連字號 tag）。`given`／`when`／`then` 可以先空著，會議裡只寫標題是正常的。為什麼用 YAML 不直接寫 Gherkin？因為紅卡在 Gherkin 裡沒有位置，而「還有問題沒答」正是我們最想讓工具擋住的狀態。這比 Wynne 嚴格：他讓投票決定剩下的小問題能不能邊做邊解；在這個格式裡，「邊做邊解」也要寫進那張紅卡的 `answer`，決定才留得下紀錄。

`tools/example_map.py`（Python 3.12，標準函式庫加 PyYAML）：

```python
#!/usr/bin/env python3
"""example_map.py: check an Example Mapping session (YAML) and turn it into a Gherkin skeleton.

usage: example_map.py check MAP [MAP ...]  |  example_map.py gherkin MAP [--draft]
check    problems: a rule with no example, a duplicate id, an example id that breaks the
         marker convention (^[a-z][a-z0-9_]*$), a When without a Then (or the reverse), and every
         open question (empty answer), because open questions block readiness.
gherkin  prints Feature / Rule / Example; refuses while problems remain unless --draft, which
         prints open questions as "# OPEN" comments (exit stays 1). An example without steps
         becomes  Given TODO formulate "<title>"  so it stays red until someone formulates it.
Exit 0 = ready, 1 = problems, 2 = unreadable map. Last line: N maps, M problems (stderr for gherkin).
"""
import argparse, re, sys
import yaml

SCENARIO_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def as_list(value):
    return value if isinstance(value, list) else [value] if value else []


def open_questions(m):
    return [q for q in m.get("questions") or [] if not str(q.get("answer") or "").strip()]


def problems_of(m):
    out, seen = [], set()
    for rule in m["rules"]:
        rid = str(rule.get("id", "?"))
        out += [f"duplicate id {rid}"] if rid in seen else []
        seen.add(rid)
        if not rule.get("examples"):
            out.append(f"rule {rid} has no example: add a green card, or turn it into a question")
        for ex in rule.get("examples") or []:
            eid = str(ex.get("id", ""))
            if not SCENARIO_ID.match(eid):
                out.append(f"example '{eid}' in {rid}: id must match ^[a-z][a-z0-9_]*$ (it becomes a pytest marker)")
            out += [f"duplicate id {eid}"] if eid in seen else []
            seen.add(eid)
            if bool(ex.get("when")) != bool(ex.get("then")):
                out.append(f"example {eid}: write both When and Then, or neither (title only)")
    for q in open_questions(m):
        blocks = f" (blocks {', '.join(map(str, as_list(q.get('blocks'))))})" if q.get("blocks") else ""
        out.append(f"question {q.get('id')} is open, owner {q.get('owner', 'nobody')}{blocks}: {q.get('text')}")
    return out


def gherkin(m):
    opened = open_questions(m)
    note = lambda q, pad: f"{pad}# OPEN {q.get('id')} ({q.get('owner')}): {q.get('text')}"
    lines = [f"Feature: {m['story']}"] + ([f"  Source: {m['source']}"] if m.get("source") else [])
    lines += [note(q, "  ") for q in opened if not q.get("blocks")]
    for rule in m["rules"]:
        lines += ["", f"  Rule: {rule['id']} {rule['text']}"]
        lines += [note(q, "    ") for q in opened if str(rule["id"]) in map(str, as_list(q.get("blocks")))]
        for ex in rule.get("examples") or []:
            lines += ["", f"    @{ex['id']}", f"    Example: {ex['title']}"]
            if not (ex.get("when") and ex.get("then")):
                lines.append(f'      Given TODO formulate "{ex["title"]}"')
                continue
            steps = [("Given", s) for s in as_list(ex.get("given"))] + [("When", ex["when"])]
            steps += [("Then", s) for s in as_list(ex["then"])]
            lines += [f"      {'And' if i and kw == steps[i - 1][0] else kw} {s}" for i, (kw, s) in enumerate(steps)]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="\n".join(
        __doc__.splitlines()[2:]), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate maps and report readiness").add_argument("maps", nargs="+")
    g = sub.add_parser("gherkin", help="print a Gherkin skeleton for one map")
    g.add_argument("map")
    g.add_argument("--draft", action="store_true", help="print even if the map is not ready")
    a = ap.parse_args()
    paths, total = (a.maps if a.cmd == "check" else [a.map]), 0
    for path in paths:
        try:
            m = yaml.safe_load(open(path, encoding="utf-8"))
            if not (isinstance(m, dict) and m.get("story") and isinstance(m.get("rules"), list)):
                raise ValueError("needs a 'story' text and a 'rules' list")
        except (OSError, yaml.YAMLError, ValueError) as e:
            print(f"{path}: cannot read: {e}", file=sys.stderr)
            return 2
        found = problems_of(m)
        total += len(found)
        if a.cmd == "check":
            examples = sum(len(r.get("examples") or []) for r in m["rules"])
            print(f"{path}: {len(m['rules'])} rules, {examples} examples, {len(m.get('questions') or [])} "
                  f"questions ({len(open_questions(m))} open) -> {'NOT READY' if found else 'READY'}")
            print("".join(f"  problem: {p}\n" for p in found), end="")
        elif found and not a.draft:
            print(f"{path}: not ready ({len(found)} problems); run `example_map.py check {path}` "
                  "or pass --draft", file=sys.stderr)
        else:
            print(gherkin(m), end="")
    print(f"{len(paths)} maps, {total} problems", file=sys.stdout if a.cmd == "check" else sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
```

先檢查會議結束時的桌面：

```bash
python tools/example_map.py check maps/weekly.v1.yaml; echo "exit=$?"
```

```
maps/weekly.v1.yaml: 4 rules, 3 examples, 3 questions (3 open) -> NOT READY
  problem: rule W3 has no example: add a green card, or turn it into a question
  problem: question Q1 is open, owner Sales (blocks W1): 最多訂幾週？Carol 說「整年」，PM 規格寫 1–12 週
  problem: question Q2 is open, owner PM (blocks W2): 被訂走的那週要不要自動改訂 Tokyo？
  problem: question Q3 is open, owner PM (blocks W3): 國定假日表從哪裡來？誰維護？
1 maps, 4 problems
exit=1
```

這時直接 `gherkin` 會被拒絕（stderr 印出 ``maps/weekly.v1.yaml: not ready (4 problems); run `example_map.py check maps/weekly.v1.yaml` or pass --draft``，exit 1）。加上 `--draft` 會照樣產生，紅卡變成 `# OPEN Q2 (PM): ...` 註解放在它卡住的 `Rule:` 底下，沒寫 steps 的 `sc_weekly_03` 變成 `Given TODO formulate "The one where Bob's meeting ends as Carol's starts"`。

會後，Sales 帶著 Q1 回去問 Carol，PM 回答 Q2、Q3，並照 Wynne 的建議把 W3 切成另一張 story。`maps/weekly.v2.yaml` 就是 v1 加上這些決定：

| 改了什麼 | v2 的內容 |
|---|---|
| Q1 的 `answer` | `Carol 同意先上 12 週，整年留給下一張 story（Sales 已回信確認）` |
| Q2 的 `answer` | `不自動改訂；回報被訂走的週，由使用者自己處理（PM）` |
| Q3 的 `answer` | `W3 拆成另一張 story「例會跳過國定假日」，這張不做（PM）` |
| 規則 W3 | 整條刪掉（切成新 story） |
| `sc_weekly_03` | 補上 `given: Bob has booked "Taipei" on 2026-11-16 from 09:00 to 10:00`，`when` 與 W1 相同，`then: 4 weekly bookings are created` |

然後 `python tools/example_map.py check maps/weekly.v2.yaml` 回報 `3 rules, 3 examples, 3 questions (0 open) -> READY` 與 `1 maps, 0 problems`，`python tools/example_map.py gherkin maps/weekly.v2.yaml > features/weekly.feature` 結束碼是 0。產生的 `features/weekly.feature`（節錄 W2）：

```gherkin
  Rule: W2 某一週已被別人訂走時，其他週照訂，被訂走的那週要回報

    @sc_weekly_02
    Example: The one where Bob already has the second Monday
      Given Bob has booked "Taipei" on 2026-11-16 from 10:00 to 11:00
      When Carol books "Taipei" every week from 2026-11-09 10:00 to 11:00 for 4 weeks
      Then 3 weekly bookings are created
      And 2026-11-16 is reported as taken by Bob
```

三個 tag 不先登記，pytest 會對每一個發出 `PytestUnknownMarkWarning`；在 `pytest.ini` 加上 `markers =` 與 `sc_weekly_01`、`sc_weekly_02`、`sc_weekly_03` 三行就安靜了（tag 怎麼拿來選測試，[Ch 30](./30-one-gherkin-two-drivers.md) 會用到）。對 RD 原本的 `weekly.py` 跑全部（`python -m pytest -v`）：

```
tests/test_weekly.py::test_a_weekly_series_books_every_week PASSED       [ 20%]
…（略：第 2、3 個也是 PASSED）
tests/test_weekly.py::test_the_one_where_bob_already_has_the_second_monday FAILED [ 80%]
tests/test_weekly.py::test_the_one_where_bobs_meeting_ends_as_carols_starts PASSED [100%]
…（略：FAILURES 段的 traceback）
E       assert 0 == 3
E        +  where 0 = len([])
…（略）
FAILED tests/test_weekly.py::test_the_one_where_bob_already_has_the_second_monday
========================= 1 failed, 4 passed in 0.03s ==========================
```

這個紅燈「紅得對」：不是 import 錯、不是 step 沒綁，是行為和 Carol 的例子不符。三明治掉的意思，第一次在上線前變成一個會失敗的測試。下一步不是讓 RD 偷偷改程式，而是刪掉 `pm_spec.feature` 的 AC3 scenario、照 W2 改 `book_weekly`，而且 commit 訊息要指得出是 Q2 的答案授權了這個改變（動手練習第 2 題）。

## 五、AI 當主持人：起草卡片，不准替人回答

AI 適合做的是苦工：把三份文件讀完、列出彼此的矛盾、起草卡片。我們讓 Claude 讀 `customer.md`、`sales-note.md`、`pm-spec.md`（就是 `sandwich/` 的三份，複製到 `facilitator/` 並改名，指令在那個目錄裡跑），寫出 `ai-draft.yaml`。指令重點：每條規則至少一個例子；三份文件之間的每個缺口或矛盾都變成一張紅卡，標明 owner；**不准回答任何問題、不准發明沒人說過的規則**。prompt 的頭尾兩句原文：

> You are facilitating an Example Mapping session (Matt Wynne, 2015) for Sales, PM, RD and QA. Read customer.md (the customer's original email), sales-note.md and pm-spec.md, then write ai-draft.yaml: …
> Do not answer any question yourself and do not invent rules nobody stated: leave every answer empty. Finish with a five-line summary.

執行用 `claude -p`，`--tools "Read,Write"`、`--allowedTools "Read,Write"`、`--max-turns 6`、`--permission-prompts none`、`--setting-sources project`、`--no-session-persistence`（旗標的意思見 [Ch 7](./07-messy-baseline.md) 第一節）；prompt 中間附上第四節的 YAML 格式（多數值換成 `<...>` 佔位）與 v1 用到的五種 step 句型。兩個細節：`--tools` 只管內建工具，這次的 init 紀錄裡還列著帳號連上的 claude.ai MCP 工具（沒被呼叫），要一起排除得加 [Ch 4](./04-prompt-files-to-skills.md) 用過的 `--strict-mcp-config`；stdin 是沒有資料的管線時，Claude Code 會等 3 秒，印出開頭為 `Warning: no stdin data received in 3s, proceeding without it.` 的警告，指令尾端加 `< /dev/null` 可以略過。

實際執行紀錄（節錄，2026-11-02，每次執行結果不同）：5 turns、90.1 秒、估計 $0.41、0 次拒絕。它的總結第 2 行：

> 2. **Examples and questions:** there are 5 green cards and 14 red cards, and every `answer` is empty; I had no YAML parser here, so the file hasn't been parsed.

它沒有 YAML parser，就由我們的工具來判（`python tools/example_map.py check facilitator/ai-draft.yaml`）：

```
facilitator/ai-draft.yaml: 4 rules, 5 examples, 14 questions (14 open) -> NOT READY
  problem: question Q1 is open, owner PM (blocks W1): The customer wants a whole year but AC1 caps repetition at 1 to 12 weeks. Does the cap stay, change, or go?
…（略：Q2–Q14）
1 maps, 14 problems
```

14 張紅卡裡，有幾張是我們人工排的 v1 沒想到的（原文是英文，以下是中譯）：Q10「客戶和 Sales 只給了 10:00 開始，例會幾點結束？」（三份文件確實都沒寫，我們的 v1 直接假設了 11:00）、Q7「客戶只說要知道哪幾週被訂走，回報可以寫出是誰訂的嗎？」（`taken by Bob` 可能洩漏其他部門的行程）、Q12「Sales 承諾的『下個月上線』到底包含什麼」。它在總結裡標出自己加了兩個 step（`Carol can book "Tokyo"...`、`no booking is created on...`），但我們的 step definitions 對不上的句子其實有四句。把草稿用 `--draft` 轉成 Gherkin，配同一份 step definitions 對 RD 的 `weekly.py` 跑，結果是 `4 failed, 1 passed`：兩個是 AC3 整批拒絕造成的斷言失敗，兩個是 `StepDefinitionNotFoundError`，正好是它沒算進去的那兩句（`Given "room "Taipei" is free all year"`、`Given "2026-11-16 is a national holiday"`）；它標出的兩句排在已經失敗的步驟後面，根本沒執行到。agent 的自我回報會漏，跑一次才算數。用語要綁一份詞彙表，那是 [Ch 28](./28-gherkin-dsl-style.md) 的 rule file 與 lint 的工作。

分工因此很清楚：

| 誰 | 做什麼 | 不做什麼 |
|---|---|---|
| AI 主持人 | 讀完所有文件、列矛盾、起草藍綠紅卡、用固定詞彙寫例子 | 回答紅卡、決定範圍、替客戶解讀「整年」 |
| `example_map.py` | 判定結構與準備度、產生 Gherkin、讓沒寫完的例子保持紅 | 判斷規則對不對 |
| 人（Sales、PM、RD、QA，必要時客戶） | 回答紅卡、切 story、投票決定能不能開工 | 把紅卡「先假設一下」就開工 |

## 對比與取捨

| 做法 | 產物 | 誰在場 | 抓得到 | 抓不到 | 成本 |
|---|---|---|---|---|---|
| 傳文件（原信 → CRM → 規格） | 一路縮短的文字 | 各自 | 格式完整 | 每一手修剪掉的意思 | 最低，但事後最貴 |
| Example Mapping | 四色卡片、YAML、Gherkin 骨架 | three amigos（加 Sales） | 規則、邊界例子、未決問題 | 跨 story 的流程與領域事件 | 一張 story 約 25 分鐘 |
| Event Storming（SDD [Ch 21](../spec_driven_development/21-event-storming.md)、[練習 C](../spec_driven_development/practice-c-event-storming.md)） | 便利貼時間軸 | 業務與技術全體 | 領域事件、流程、熱點 | 單一 story 的細節規則 | 數小時 |
| 通用語言與詞彙表（SDD [Ch 15](../spec_driven_development/15-ubiquitous-language.md)） | glossary | 業務與開發 | 命名漂移 | 規則本身錯了 | 持續維護 |
| AI 單獨寫規格 | 完整漂亮的 spec | AI | 文件之間的矛盾（如果給了原文） | 原文之外的意思、業務決定 | 低 |

## 踩雷集錦

1. **錯誤直覺：換一個更好的 coding skill 或更強的模型，就會做對。** 正確認識：寫程式這一側的檢查都是驗證，oracle 是規格。底層機制一節的實驗裡，兩種實作各自讓一份規格變紅，沒有一種能同時通過；選哪一邊要由人決定。
2. **錯誤直覺：Example 先寫個標題，之後再補 steps，反正不會出事。** 正確認識：在 pytest-bdd 9.0.1，一個沒有 step 的 `Example:` 會**通過**。我們用三個例子實測（`probe-empty/`）：有 step 的 `PASSED`、沒有 step 的 `PASSED`、只有 TODO step 的 `FAILED`（`StepDefinitionNotFoundError`）。所以 `example_map.py` 一律產生 TODO step，讓沒形式化的例子保持紅。
3. **錯誤直覺：AI 主持人可以順便把問題答掉，省一輪來回。** 正確認識：它一回答，三明治就搬進了模型裡，它變成那個沒看過原信、自己做決定的 PM。紅卡的 `answer` 只能由 owner 填。
4. **錯誤直覺：紅卡太多代表這場會開失敗了。** 正確認識：Wynne 的讀法是「還有很多要學」，該做的是把問題帶走、或把 story 切小（我們把 W3 切出去），不是硬著頭皮開工。
5. **錯誤直覺：把 PM 的驗收條件翻成 Gherkin 就是 BDD。** 正確認識：`pm_spec.feature` 就是 Gherkin，而且全綠，它忠實地編碼了錯誤的決定。SDD Ch 10 的錯誤直覺 4 早就說過：只有語法、沒有三方對話，就不是 BDD 的實質。

## 進階：讓原話一路追得到

- **追溯鏈**：`source:` 欄位進到 feature 的 `Source:` 描述行，例子 ID 變成 `@sc_weekly_02`，之後 [Ch 16](./16-tasks-binder.md) 的 task 會寫成 `- [ ] T005 [US1] [@sc_weekly_02] ...`，[Ch 31](./31-bdd-in-sdd.md) 再檢查每個情境都有 task。任何一行程式都追得回 Carol 的那封信。**量三明治**：統計每張 map 的紅卡 owner。如果 Sales 擁有的紅卡長期偏多，損耗多半發生在第一手；這比事後檢討「誰寫錯了」有用。
- **和 Spec Kit 的關係**：Spec Kit 模板裡的 `[NEEDS CLARIFICATION: ...]` 標記就是寫在規格裡的紅卡；[Ch 18](./18-clarify-skill.md) 的 clarify skill 一次問一題、問到上限就停，答案寫回 spec，沒問到的留著標記。Example Mapping 的深入版，包含 journey 型與 rule 型情境、Specification by Example 的流程模式，在 [Ch 29](./29-example-mapping.md)。

## 動手練習

1. 新需求：「秘書要能幫主管訂會議室，主管也要能取消。」它和 R4（只有預約人能取消）直接衝突。自己寫出客戶原信、Sales 一行紀錄、PM 規格三份產物，標出每一手掉了什麼；再排一張 map（可以一個人扮四個角色，或用第五節的方式請 AI 起草），用 `example_map.py check` 跑到 `READY`，產生 Gherkin。
2. 照 Q2 的答案實作 W2：刪掉 `pm_spec.feature` 的 AC3 scenario，改 `book_weekly` 讓 `sc_weekly_02` 變綠，commit 訊息寫出是哪一張紅卡的答案授權了這個改變。
3. 照第五節的指令重點寫一份自己的 prompt，跑兩次 AI 主持人（限定工具、加 `--max-turns` 與 `< /dev/null`），比較兩次的紅卡清單：哪些問題兩次都有？哪些只出現一次？只出現一次的那些，你會怎麼確保下次不漏？

## 本章重點整理

- 需求翻譯三明治：Sales 修剪掉客戶的細節並加上承諾，PM 夾在承諾與工時之間自己做了業務決定，RD 忠實地實作；三手都「沒做錯」，結果卻錯了。
- 寫程式這一側的每一道檢查都是驗證，oracle 是規格；規格錯時，測試、review 與 coding skill 會一致地同意那個錯。本章的兩種實作各讓一份規格變紅。
- Example Mapping（Wynne 2015）：黃卡 story、藍卡規則、綠卡例子、紅卡問題，three amigos，約 25 分鐘，大拇指投票；滿桌紅卡表示還要再學，滿桌藍卡表示 story 可能該切。
- `example_map.py`：每條規則至少一個例子、未決問題擋住準備度、例子 ID 可當 pytest marker；產生的 Gherkin 讓沒寫完的例子保持紅，因為空的 `Example:` 在 pytest-bdd 9.0.1 會通過。
- AI 適合當主持人：讀完文件、列出矛盾、起草卡片；回答紅卡、做業務決定的永遠是人。

## 自我檢核

- [ ] 不看本章，用三明治圖說出 Carol 的需求在每一手掉了什麼，以及為什麼 RD 那一手「什麼都沒掉」反而最危險。
- [ ] 解釋驗證與確認的差別，並用底層機制一節的兩種實作說明：為什麼更強的 coding agent 也無法讓兩份規格同時變綠？
- [ ] 為什麼 `example_map.py` 要把沒寫 steps 的例子變成 `Given TODO formulate ...`，而不是乾脆不產生 step？
- [ ] 如果你的 AI 主持人在草稿裡把 Q2 答成「自動改訂 Tokyo」，你的流程會在哪一步攔下來？

## 延伸閱讀

- **[Introducing Example Mapping](https://cucumber.io/blog/bdd/example-mapping-introduction/)**：Matt Wynne（Cucumber 部落格，2015-12-08）
  - **讀哪裡**：整篇，特別是卡片顏色、25 分鐘與大拇指投票、怎麼讀桌面上紅藍卡數量的段落。
  - **學到什麼／關聯**：四種卡片在本章 `example_map.yaml` 各有對應（story、rules、examples、questions）。準備度規則比原文嚴格：Wynne 讓大拇指投票決定剩下的小問題能不能邊做邊解，`example_map.py` 則要求把這個決定也寫進 `answer`。
- **[Your first Example Mapping session](https://cucumber.io/blog/bdd/your-first-example-mapping-session/)**：Steve Tooke（Cucumber 部落格，2018-05-23；原刊於作者的 Medium，2018-05-16）
  - **讀哪裡**：主持人怎麼記紅卡、範圍跑掉時怎麼處理，以及會後由誰把紀錄寫成 Gherkin。
  - **學到什麼／關聯**：第一次排 map 的實務細節；它的「least understanding 的人寫 Gherkin 紀錄」正是 `example_map.py gherkin` 之後該做的形式化步驟。
- **Verifying and Validating Software Requirements and Design Specifications**：Barry W. Boehm（IEEE Software 1(1)，1984 年 1 月，頁 75–88，[DOI](https://doi.org/10.1109/MS.1984.233702)）
  - **讀哪裡**：定義 verification 與 validation 的段落（我們只核對了書目資料，沒有取得全文，細節以原文為準）。
  - **學到什麼／關聯**：「驗證不等於確認」最常被引用的出處之一；本章底層機制一節，就是把 coding agent 放進這個框架裡看。

Example Mapping 把「規則加例子」從會議裡拿出來，交給工具檢查。下一章把同樣的形狀用在 agent 自己身上：每一條寫給 agent 的規則，都帶著好例子、壞例子與邊界例子，還要能從既有的 feature、review 留言與 CLAUDE.md 裡提取出來。

→ [Ch 9 帶範例的 Rule File 與規則提取器](./09-rule-files.md)
