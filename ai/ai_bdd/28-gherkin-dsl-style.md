# Ch 28 — Gherkin 與 DSL 寫作標準：做成 rule file 與 lint

> **目標**：把散在 SDD 課與本課前幾章的 Gherkin 寫作規則收成一份有穩定編號的 rule file（GH-01..GH-12，前四條接續 Ch 9 的編號），其中 11 條寫成建在 gherkin-official parser 上的 `gherkin_lint.py`；在 step 與系統之間放一層 DSL，讓 feature 檔只說領域的話；驗證 `# language: zh-TW` 的寫法與它的陷阱；打包成 `gherkin-style` skill，再用三次真的 `claude -p` 看 agent 照不照做，以及證據在哪裡斷掉。

> **環境**：macOS 26.6 arm64、Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（parser 為 gherkin-official 42.0.1）、PyYAML 6.0.3、FastAPI 0.162.0、Claude Code 2.1.295（預設模型 claude-opus-5-5）；對照組 gherkin-lint 4.2.4（Node v26.11.0，裝在章節目錄）。實驗目錄 `/tmp/lab/aibdd/ch28-gherkin-style/`，輸出皆為 2026-11-02 實跑。

## 為什麼需要這個？

這個 repo 已經有四份 Gherkin 寫作規則：[SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的「踩雷集錦」（Given 寫狀態、Then 列出所有可觀察結果、一個功能 3–7 個 scenario、step definition 別過度共用）、[SDD 練習 B](../spec_driven_development/practice-b-three-notations.md) 參考解答的說明（Then 可斷言、寫 WHAT 不寫 HOW、Background 只放共用前提、Outline 給資料變化），[Ch 4](./04-prompt-files-to-skills.md) 為了量載入成本寫的 `scenario-conventions`，以及 [Ch 9](./09-rule-files.md) 的 `rules/gherkin.md`（GH-01..GH-04，四條都寫著 `Enforced by: review (no lint yet)`）。四份都對，沒有一份會被執行。SDD 練習 B 的解答一邊說 Gherkin「不提 API、不提資料庫」，一邊寫 `When 使用者點擊商品 "SKU-001" 的「加入購物車」按鈕`：When 描述的是按哪顆按鈕。這不是作者粗心，是散文規則的常態：規則活在文件裡，靠寫的人每次記得。人一天寫幾份 feature 檔，review 追得上；agent 一份不到一分半（本章 eval 的三次都在 86 秒內寫完），風格就用 agent 的速度漂移。過去的對策是 wiki 風格指南加 code review：Dan North 2011 年的〈Whose domain is it anyway?〉主張一個情境只該牽涉兩個領域（標題的問題領域、步驟的解法領域），多出來的領域往往只帶來雜訊、讓測試變脆，Cucumber 官方 "Writing better Gherkin" 把宣告式（declarative，描述行為）與命令式（imperative，描述操作）寫成指南，Seb Rose 2019 年用 BRIEF 總結好情境（五個字母各是一個原則，Brief 本身是第六個）。這些都是寫給人讀的。

還缺一樣東西：**step 後面的 DSL**（domain-specific language，領域特定語言；這裡指一組只說領域動詞的函式）。SDD Ch 10 的 step definitions 直接改物件，Ch 0 的雛形讓 step 直接呼叫 driver；都還沒有一層只說領域動詞、和「怎麼送進系統」分開的 DSL。少了這層，句子寫得再宣告式，step 裡還是得有人知道 409 的意思。本章把規則編號、寫成 lint、加上 DSL、包成 skill，然後量 agent 照不照做。

## 先建立直覺

想像一家麵店。菜單寫「牛肉麵（小辣）」，點菜單翻成廚房代號，廚房照標準食譜做，爐子是瓦斯還是電磁爐是設備的事。菜單若寫「瓦斯開到 7、煮 3 分鐘」，換爐子就得重印菜單：這是命令式步驟。外場各自發明菜名（「紅燒牛肉麵」「牛肉湯麵」），廚房就得為同一碗麵備兩份食譜：這是詞彙漂移，菜名表就是 glossary。出餐前有人逐條核對菜單寫法，那是 lint；他嚐不出湯頭，那是 review。類比有兩處不成立：菜單不會被逐條「執行」，feature 檔每次改動都會跑一次；顧客不寫菜單，PM 卻要一起寫 feature 檔（[Ch 29](./29-example-mapping.md)）。Gherkin 關鍵字若還不熟，先讀 [Ch 26 的「Gherkin 關鍵字巡禮」](./26-bdd-vs-tdd-artifacts.md)；rule file 的欄位格式與前四條規則是 [Ch 9](./09-rule-files.md) 定的。

```
 寫給人讀         features/booking.feature：When Alice books "Taipei" from 10:30 to 11:30
     │  ← GH-01..GH-11：句子怎麼寫（gherkin_lint.py 檢查）
 翻譯層           tests/test_booking.py：拆出 who、room、start、end，呼叫一次 DSL
     │  ← GH-12：一個動作一種句型（review）
 領域動詞（DSL）  tests/dsl.py：booking.book("Alice", "Taipei", "10:30", "11:30")
     │            → Outcome(confirmed=False, reason="Taipei is taken 10:00-11:00 by Bob")
     │  ← 怎麼送進系統是 driver 的事（Ch 30）
 系統             POST /bookings → 409
```

## 一、十二條規則，穩定編號

| ID | 規則 | 來源 | 怎麼檢查 |
|---|---|---|---|
| GH-01 | Given 陳述已成立的事實，不是動作 | Ch 9（源自 SDD Ch 10 踩雷 1） | lint（glossary 的 `actions`） |
| GH-02 | 以使用者看得到的結果收尾 | Ch 9（源自 SDD Ch 10 踩雷 3） | lint（缺 Then、Then 是動作）＋ review |
| GH-03 | 只說領域的話：說使用者達成什麼，不說操作哪個控制項（宣告式）；沒有 HTTP 動詞、狀態碼、URL、JSON、資料庫 | Ch 9、SDD 練習 B、Cucumber "Writing better Gherkin" | lint（UI 字詞、技術字詞） |
| GH-04 | 一個 scenario 一個動作：剛好一個 When，順序 Given → When → Then | Ch 9、BRIEF 的 Focused、gherkin-lint 的 `only-one-when` | lint ＋ review（一行藏兩個動作） |
| GH-05 | 3–5 步，Background 算在內 | Cucumber Gherkin Reference、BRIEF 的 Brief | lint ＋ review（湊數） |
| GH-06 | Background 只放共用 Given，至多 4 步，至少服務 2 個 scenario | SDD 練習 B、Cucumber Reference | lint |
| GH-07 | Scenario Outline 只給有意義的變化 | SDD 練習 B、SDD Ch 10 踩雷 2 | lint（列數、不變的欄）＋ review |
| GH-08 | 只用 glossary 的詞 | [SDD Ch 34](../spec_driven_development/34-ubiquitous-language-as-glossary.md) | lint（`avoid` 清單） |
| GH-09 | 參數慣例：會議室加引號、人名不加、時間 24 小時 `HH:MM` | 本課從 Ch 0 起的固定句型 | lint |
| GH-10 | 每個 scenario 剛好一個 `@sc_<area>_<NN>`，全套唯一 | 本課的 ID 慣例（[Ch 1](./01-course-map.md)） | lint |
| GH-11 | 檔名 lower_snake；scenario 掛在 `Rule: R<n>` 下或名稱以 `R<n>` 開頭；名稱檔內唯一 | Ch 1 產物鏈、pytest-bdd 的行為 | lint |
| GH-12 | 一個領域動作一種句型；step definition 一行呼叫 DSL | Ch 4 的 `scenario-conventions`（句型逐字重用） | review（pytest-bdd 找不到 step 會失敗） |

GH-01..GH-04 是 Ch 9 發出的編號，意思照舊（Ch 9 的 GH-03 同時管 UI 操作與技術字詞，這裡仍是一條），本章從 GH-05 接下去。編號發出後不重用，刪掉的規則留空號，和 [pstack Ch 23](../pstack/23-unslop-no-comments.md) 的 unslop 同一個理由：tasks、review 留言、lint 輸出都會引用它，重新編號會讓舊引用悄悄指向另一條規則。Ch 9 把四條規則的 Good／Bad／Edge 例子交給本章當 lint 的測資；各包成一個 scenario 跑過，Good 與 GH-01..GH-03 的 Edge 全是 0 problems，四個 Bad 抓到三個，放行的是 GH-02 的 `Then the booking is rejected`（沒說為什麼被拒）與 GH-04 的 Edge `When Alice books "Taipei" from 11:00 to 12:00 and then cancels it`（一行藏兩個動作），所以這兩條的「怎麼檢查」都有 review。

數字都有出處：Cucumber 的 Gherkin Reference 寫 "We recommend 3-5 steps per example"，同一份文件說 Background 超過 4 行就該把細節收進更高階的步驟；BRIEF 的 Brief 是「大多數情境五行以內」。BRIEF 對到這張表：Business language → GH-03、08；Real data → GH-09 與 glossary 裡的真名；Intention revealing → scenario 名稱（review）；Essential → GH-05 下限加 review；Focused → GH-04；Brief → GH-05 上限。每條規則照 Ch 9 的格式寫進 `rules/gherkin.md`：一行一欄、不加條列符號（[Ch 11](./11-skill-engineering-skill.md) 的 `module_check.py` 就是照行首的 `Trigger:`、`Rule:` 這些欄位名逐條檢查）。檔案給 agent 讀，所以和 step 句子一樣用英文；GH-01..GH-04 的 Trigger、Rule 與 Sign you skipped it 照抄 Ch 9，`Enforced by` 改成現在真正在擋的東西；下面只列一條，其餘照同一格式，拿上表與 `bad/` 的句子當例子自己寫。「Why」只在規則反直覺時寫（[pstack Ch 28](../pstack/28-skill-authoring.md) 的寫作原則），「Enforced by」誠實寫出誰在擋。一條長這樣：

```markdown
## GH-01 Given describes a state, not an action
Trigger: writing or reviewing a `Given` (or an `And` under it).
Rule: a `Given` states a fact that already holds before the action; the action goes in the one `When`.
Sign you skipped it: a `Given` whose verb is something a user does now: opens, clicks, fills, books.
Enforced by: gherkin_lint.py (the verbs under `actions` in glossary.yaml); review (any other verb)
Good: `Given room "Taipei" is free all day`
Bad: `Given Bob books "Taipei" from 10:00 to 11:00`
Edge (a finished action is a state: "has booked" is fine, "books" is not): `Given Bob has booked "Taipei" from 10:00 to 11:00`
```

## 二、glossary.yaml：詞彙表是 lint 的輸入

GH-01、08、09 要知道這個領域的詞。格式沿用 SDD Ch 34 的 `terms:`，加三個給機器用的欄位。`avoid` 是最有用的一欄：與其列「該用的詞」，不如列「大家會順手用的同義詞」，lint 才有東西比；`actions` 把動作的兩種形態綁在一起，`books` 出現在 Given 裡，lint 就能建議 `has booked`。

```yaml
# glossary.yaml: the room-booking vocabulary. gherkin_lint.py reads rooms, people, actions, terms[*].avoid
context: room-booking
rooms: [Taipei, Tokyo, Seoul]
people: [Alice, Bob, Carol]
actions:              # When form -> Given form (GH-01: a Given states a fact, not an action)
  books: has booked
  cancels: has cancelled
terms:
  booking:
    zh: 預約
    definition: One person holds one room for the half-open interval [start, end) on one day.
    avoid: [reservation, reservations, appointment]
  book:
    zh: 預約（動作）
    definition: Ask for a room for an interval. The answer is confirmed or rejected with a reason.
    avoid: [reserve, reserves, reserved, schedule, schedules]
  room:
    zh: 會議室
    definition: A bookable room named after a city.
    avoid: [meeting room, conference room]
```

## 三、gherkin_lint.py：和 pytest-bdd 用同一個 parser

從空目錄 `/tmp/lab/aibdd/ch28-gherkin-style/` 開始：建 `features/`、`bad/`、`tests/` 與 `.claude/skills/gherkin-style/` 底下的 `rules/`、`scripts/`，`cp /tmp/lab/aibdd/ch00-setup/smoke/app.py .` 取得 [Ch 0 第六節](./00-environment-setup.md)的雛形 app（有 R1、R2，沒有 R3），再 `source /tmp/lab/aibdd/.venv/bin/activate`。lint 放在 skill 的 `scripts/` 裡，122 行，只用標準函式庫、PyYAML，以及 pytest-bdd 本來就帶進來的 gherkin-official。它不用正規表示式逐行掃 feature 檔，而是讀 parser 產生的 AST：lint 和 runner 對「這個檔案說了什麼」不會有分歧（同樣的方言、同樣的 `Rule` 巢狀、同樣的描述文字判定）；逐行掃的話，`And` 是 Given 還是 Then、哪幾行屬於 Background 都得自己猜。每則訊息說出修法，因為讀它的常常是 agent；路徑打錯、一個檔都沒找到時回 exit 2，不讓「0 files, 0 problems」被當成通過。完整原始碼在本節最後；先看它在好、壞兩份檔案上的表現。好的範例 `features/booking.feature`：

```gherkin
Feature: Room booking
  People book a room for part of one day. A room is never double-booked,
  so whoever sees "confirmed" can rely on having the room.

  Rule: R1 A room cannot be booked twice for overlapping times

    @sc_book_01
    Scenario: A free room can be booked
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 10:00 to 11:00
      Then the booking is confirmed

    @sc_book_02
    Scenario: Adjacent slots do not overlap
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed

    @sc_book_03
    Scenario Outline: Any overlap is rejected
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from <start> to <end>
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob

      Examples:
        | start | end   |
        | 10:30 | 11:30 |
        | 09:30 | 10:30 |
        | 10:15 | 10:45 |

  Rule: R2 The end must be after the start

    @sc_book_04
    Scenario: A booking that ends before it starts is rejected
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 11:00 to 10:00
      Then the booking is rejected because end must be after start
```

Outline 的三列是三種不同的重疊形狀（壓住結尾、壓住開頭、整段落在裡面），這是 GH-07 說的有意義的變化。R2 的 `Given room "Taipei" is free all day` 不是湊步數：它排除了「其實是被別人訂走才被拒」這個解讀。壞的範例 `bad/Booking-UI.feature` 是照著畫面寫的典型：情境 ID 和 [Ch 27](./27-ai-bdd-loop.md) 不同：這裡 `@sc_book_02` 是相鄰、`@sc_book_03` 是三列的重疊 Outline（Ch 27 相反，[Ch 31](./31-bdd-in-sdd.md) 沿用 Ch 27 的編號）。

```gherkin
Feature: Reservations

  Background:
    Given the system is running
    When Alice opens the booking page

  Scenario: Booking a free slot
    Given Bob books "Taipei" from 10:00 to 11:00
    When Alice fills in the Room field with Taipei
    And Alice presses Book
    Then the API returns 201

  @SC_Book_02
  Scenario: Booking a free slot
    When "Alice" reserves "Taipei" from 9:00 to 10:00

  @sc_book_01
  Scenario Outline: R1 adjacent slots
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Alice books "Taipei" from <start> to 12:00
    Then the booking is confirmed
    Examples:
      | start |
      | 11:00 |
```

```
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py features/
1 files, 0 problems
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py bad/
bad/Booking-UI.feature:1: GH-11 file name 'Booking-UI.feature': use lower_snake_case.feature
bad/Booking-UI.feature:5: GH-03 UI operation "page"; say what the user achieves
bad/Booking-UI.feature:5: GH-06 Background holds shared Given state only
bad/Booking-UI.feature:7: GH-04 2 When steps; keep one action, earlier ones become Given facts
bad/Booking-UI.feature:7: GH-05 6 steps (Background included); keep 3-5
bad/Booking-UI.feature:7: GH-10 0 ID tags; give exactly one @sc_<area>_<NN>
bad/Booking-UI.feature:7: GH-11 no business rule; nest it under "Rule: R<n> ..." or start its name with R<n>
bad/Booking-UI.feature:8: GH-01 Given describes an action ("books"); state the fact: "has booked"
…（略：第 9–15 行的 GH-03、GH-09、GH-02、GH-10、GH-11、GH-08 共 11 則）
bad/Booking-UI.feature:18: GH-07 Scenario Outline with 1 example row; write a plain Scenario
1 files, 20 problems
```

24 行、20 個發現，11 條可檢查的規則全被觸發過；省略的部分包括 `14: GH-11 scenario name repeats line 7; pytest-bdd keeps only one`（底層機制一節看它為什麼要擋）與 `15: GH-09 time "9:00" is not 24-hour HH:MM`。第 7 行的「6 steps」值得多看一眼：scenario 本身只有 4 步，加上 Background 的 2 步才超標，而讀者確實得同時記住 6 步。lint **沒有**抓的是 `Given the system is running`：一句湊數的廢話。路徑打錯（`gherkin_lint.py feature/`）時回 `gherkin_lint.py: error: no .feature files in feature`，exit 2。

<details>
<summary><code>.claude/skills/gherkin-style/scripts/gherkin_lint.py</code> 完整原始碼</summary>

```python
#!/usr/bin/env python3
"""gherkin_lint.py: check .feature files (or directories of them) against the GH rules in
rules/gherkin.md. Each message names the rule and the fix. Exit 0 clean, 1 problems, 2 bad usage."""
import argparse, re, sys
from pathlib import Path
import yaml
from gherkin import Parser
from gherkin.errors import CompositeParserException

UI = re.compile(r"\b(click(s|ed)?|tap(s|ped)?|press(es|ed)?|type[sd]? in(to)?|fill(s|ed)? in|navigates? to"
                r"|visits?|scroll(s|ed)?|button|text ?box|dropdown|checkbox|field|page|screen|form)\b", re.I)
TECH = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b|(?i:\b(HTTP|JSON|API|endpoint|database|SQL|response"
                  r"|status code)\b)|\b[1-5]\d\d\b|(?<![\w\"])/[a-z][\w/-]*")
BAD_TIME = re.compile(r"\b\d:\d\d\b|\b\d{1,2}\.\d\d\b|\b\d{1,2}(:\d\d)? ?[ap]m\b", re.I)
ID_TAG, RULE_ID = re.compile(r"^@sc_[a-z]+_\d{2}$"), re.compile(r"^R\d+\b")
GIVEN, WHEN, THEN = "Context", "Action", "Outcome"

def typed(steps):
    """And/But/* take the type of the step before them, as pytest-bdd does (None if nothing precedes)."""
    prev, out = None, []
    for s in steps:
        prev = s["keywordType"] if s["keywordType"] in (GIVEN, WHEN, THEN) else prev
        out.append((prev, s))
    return out

def count_scenarios(children):
    return sum(1 if "scenario" in c else count_scenarios(c["rule"]["children"]) if "rule" in c else 0
               for c in children)

def lint(path, g, ids):
    found, names = [], {}
    def bad(line, rule, msg): found.append((line, rule, msg))
    def words(text, line, step=True):
        for term, spec in g["terms"].items():
            for w in spec.get("avoid", []):
                if re.search(rf"\b{re.escape(w)}\b", text, re.I):
                    bad(line, "GH-08", f'"{w}" is not glossary language; use the term "{term}"')
        if not step:
            return
        if m := UI.search(text): bad(line, "GH-03", f'UI operation "{m[0]}"; say what the user achieves')
        if m := TECH.search(text): bad(line, "GH-03", f'implementation detail "{m[0]}"; say what the user observes')
        if m := BAD_TIME.search(text): bad(line, "GH-09", f'time "{m[0]}" is not 24-hour HH:MM')
        for room in g["rooms"]:
            if re.search(rf'(?<!"){room}\b(?!")', text): bad(line, "GH-09", f'room {room} needs quotes: "{room}"')
        for who in g["people"]:
            if f'"{who}"' in text: bad(line, "GH-09", f'person "{who}" takes no quotes: {who}')
    def background(bg, served):
        for kind, s in typed(bg["steps"]):
            words(s["text"], s["location"]["line"])
            if kind != GIVEN: bad(s["location"]["line"], "GH-06", "Background holds shared Given state only")
        if len(bg["steps"]) > 4: bad(bg["location"]["line"], "GH-06", f"Background has {len(bg['steps'])} steps; keep 4 or fewer")
        if served < 2: bad(bg["location"]["line"], "GH-06", f"Background serves {served} scenario; inline it")
    def scenario(sc, bg_steps, rule):
        line, steps = sc["location"]["line"], typed(sc["steps"])
        words(sc["name"], line, step=False)
        for kind, s in steps:
            words(s["text"], s["location"]["line"])
            verb = next((v for v in g["actions"] if re.search(rf"\b{v}\b", s["text"])), None)
            if verb and kind == GIVEN:
                bad(s["location"]["line"], "GH-01", f'Given describes an action ("{verb}"); state the fact: "{g["actions"][verb]}"')
            if verb and kind == THEN:
                bad(s["location"]["line"], "GH-02", f'Then describes an action ("{verb}"); state the outcome')
        kinds = [k for k, _ in steps if k]
        if len(kinds) < len(steps): bad(line, "GH-04", "first step is And/But/*; start with Given, When or Then")
        if kinds.count(WHEN) != 1: bad(line, "GH-04", f"{kinds.count(WHEN)} When steps; keep one action, earlier ones become Given facts")
        if kinds != sorted(kinds, key=[GIVEN, WHEN, THEN].index): bad(line, "GH-04", "steps out of order; use Given -> When -> Then")
        if THEN not in kinds: bad(line, "GH-02", "no Then step; end with the outcome the user observes")
        if not 3 <= len(bg_steps) + len(steps) <= 5: bad(line, "GH-05", f"{len(bg_steps) + len(steps)} steps (Background included); keep 3-5")
        rows = [r for ex in sc["examples"] for r in ex["tableBody"]]
        if sc["examples"] and len(rows) < 2: bad(line, "GH-07", f"Scenario Outline with {len(rows)} example row; write a plain Scenario")
        for ex in (ex for ex in sc["examples"] if len(ex["tableBody"]) > 1):
            for i, head in enumerate(ex["tableHeader"]["cells"]):
                if len({r["cells"][i]["value"] for r in ex["tableBody"]}) == 1:
                    bad(ex["location"]["line"], "GH-07", f'column "{head["value"]}" never varies; inline the value')
        tags = [t["name"] for t in sc["tags"] if t["name"].lower().startswith("@sc")]
        if len(tags) != 1: bad(line, "GH-10", f"{len(tags)} ID tags; give exactly one @sc_<area>_<NN>")
        elif not ID_TAG.match(tags[0]): bad(line, "GH-10", f"ID tag {tags[0]} is not @sc_<area>_<NN> (lowercase, 2 digits)")
        elif tags[0] in ids: bad(line, "GH-10", f"ID tag {tags[0]} already used at {ids[tags[0]]}")
        else: ids[tags[0]] = f"{path}:{line}"
        if sc["name"] in names: bad(line, "GH-11", f"scenario name repeats line {names[sc['name']]}; pytest-bdd keeps only one")
        names.setdefault(sc["name"], line)
        if not (rule and RULE_ID.match(rule["name"]) or RULE_ID.match(sc["name"])):
            bad(line, "GH-11", 'no business rule; nest it under "Rule: R<n> ..." or start its name with R<n>')
    def walk(children, bg_steps, rule):
        for c in (c for c in children if "background" in c):
            background(c["background"], count_scenarios(children))
            bg_steps = bg_steps + c["background"]["steps"]
        for c in children:
            if "scenario" in c: scenario(c["scenario"], bg_steps, rule)
            if "rule" in c: walk(c["rule"]["children"], bg_steps, c["rule"])
    if not re.fullmatch(r"[a-z0-9_]+\.feature", path.name):
        bad(1, "GH-11", f"file name {path.name!r}: use lower_snake_case.feature")
    try:
        feature = Parser().parse(path.read_text(encoding="utf-8")).get("feature")
    except CompositeParserException as e:
        return found + [(err.location["line"], "GH-00", f"parse error {err}") for err in e.errors]
    if not feature:
        return found + [(1, "GH-00", "no Feature in this file")]
    walk(feature["children"], [], None)
    return sorted(found, key=lambda p: p[0])

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    ap.add_argument("--glossary", type=Path, default=Path("glossary.yaml"), help="vocabulary (default ./glossary.yaml)")
    a = ap.parse_args()
    if not a.glossary.is_file():
        ap.error(f"glossary {a.glossary} not found; pass --glossary FILE")
    g = {"rooms": [], "people": [], "actions": {}, "terms": {}, **yaml.safe_load(a.glossary.read_text(encoding="utf-8"))}
    files = sorted({f for p in a.paths for f in ([p] if p.is_file() else p.rglob("*.feature"))})
    if not files:
        ap.error("no .feature files in " + " ".join(map(str, a.paths)))
    ids, total = {}, 0
    for f in files:
        for line, rule, msg in lint(f, g, ids):
            print(f"{f}:{line}: {rule} {msg}")
            total += 1
    print(f"{len(files)} files, {total} problems")
    return 1 if total else 0

if __name__ == "__main__":
    sys.exit(main())
```

</details>

## 四、DSL：句子和系統之間的那一層

step definition 只做一件事：把句子拆成參數，呼叫一次 DSL。DSL 的方法用 glossary 的動詞命名，收領域的值、回傳領域的結果，HTTP 只出現在 `tests/dsl.py`：

```python
"""dsl.py: the booking DSL. Steps call these domain verbs; only this file knows how to reach the app."""
from dataclasses import dataclass
from fastapi.testclient import TestClient
from app import app

@dataclass
class Outcome:
    confirmed: bool
    reason: str | None = None

class Booking:
    def __init__(self):
        self.http = TestClient(app)          # Ch 30 swaps this line for an api or ui driver
        self.http.post("/reset")

    def book(self, who, room, start, end) -> Outcome:
        r = self.http.post("/bookings", json={"room": room, "start": start, "end": end, "by": who})
        return Outcome(True) if r.status_code == 201 else Outcome(False, r.json()["detail"])

    def has_booked(self, who, room, start, end):
        assert self.book(who, room, start, end).confirmed, f"setup: {who} could not book {room}"

    def bookings_of(self, room):
        return [b for b in self.http.get("/bookings").json() if b["room"] == room]
```

`tests/conftest.py` 只有一個 fixture，給每個測試一個新的 `Booking`（`import pytest`、`from dsl import Booking`，然後 `@pytest.fixture def booking(): return Booking()`）。`tests/test_booking.py` 是全部的 step definitions：

```python
"""Step definitions: parse the sentence, make one DSL call. No HTTP, no selectors here (GH-12)."""
from pytest_bdd import given, parsers, scenarios, then, when
from dsl import Outcome

scenarios("../features/booking.feature")

@given(parsers.parse('room "{room}" is free all day'))
def room_is_free(booking, room):
    assert booking.bookings_of(room) == []

@given(parsers.parse('{who} has booked "{room}" from {start} to {end}'))
def has_booked(booking, who, room, start, end):
    booking.has_booked(who, room, start, end)

@when(parsers.parse('{who} books "{room}" from {start} to {end}'), target_fixture="outcome")
def books(booking, who, room, start, end):
    return booking.book(who, room, start, end)

@then("the booking is confirmed")
def confirmed(outcome):
    assert outcome == Outcome(True)

@then(parsers.parse("the booking is rejected because {reason}"))
def rejected(outcome, reason):
    assert outcome == Outcome(False, reason.replace('"', ""))
```

`pytest.ini` 註冊 ID tag（沒註冊的 tag 每個都會發一次 `PytestUnknownMarkWarning`），並濾掉 Starlette 1.10.0 對 TestClient 的棄用提醒（原因見 [Ch 2](./02-vibe-coding-vs-engineering.md)）：

```ini
[pytest]
pythonpath = . tests
testpaths = tests
filterwarnings =
    ignore:Using `httpx` with `starlette.testclient` is deprecated
markers =
    sc_book_01: R1 a free room can be booked
    sc_book_02: R1 adjacent slots do not overlap
    sc_book_03: R1 any overlap is rejected
    sc_book_04: R2 end must be after start
```

`pytest -q` 回 `6 passed in 0.03s`，`pytest -q -m sc_book_02` 回 `1 passed, 5 deselected in 0.01s`。四個設計決定：SDD Ch 10 踩雷 5 擔心 step definition 共用後帶著隱藏狀態，這裡的 step 只拆參數，狀態都在明確的 fixture（`booking`、`outcome`）裡，共用句型不等於共用狀態；Given 分兩種，`has_booked` **建立**狀態，`room_is_free` **驗證**狀態，「free all day」才不是一句空話；`rejected` 把句子裡的引號拿掉再比對，GH-09 要會議室加引號、API 訊息沒有，這個落差在翻譯層吸收，不往上推給 feature 檔、也不往下推給 app，代價是 because 後面得逐字寫出使用者讀到的訊息；DSL 暫時直接用 `TestClient`，這一行就是 [Ch 30](./30-one-gherkin-two-drivers.md) 要拆成 api、ui 兩個 driver 的地方，DSL 的介面不必跟著變。第三個決定的代價落在句子上：本章 R2 的 Then 是 `because end must be after start`，不是[練習 A](./practice-a-first-loop.md) 定的標準句 `because the end must be after the start`；Ch 29–31、33 與 Final Project 沿用本章的檔案，也沿用這個變體（換回標準句，這個 step 照樣對得上，斷言卻失敗：`reason: 'end must be after start' != 'the end must be after the start'`，`1 failed, 5 passed`）。拿這個 step 去接 R3 也一樣：app 加上辦公時間檢查（422，`outside office hours 08:00-20:00`），情境用 [Ch 3](./03-propose-constrain-feedback.md) 的 `Then the booking is rejected because it is outside office hours 08:00-20:00`，得到 `reason: 'outside office hours 08:00-20:00' != 'it is outside office hours 08:00-20:00'`、`1 failed, 6 passed`；在同一個模組另加一支只認這句 R3 的 step 也沒用，放在 `rejected` 前面或後面，實測都是參數化的 `rejected` 被選中，所以要嘛 because 後面照抄 API 的原文，要嘛改寫 `rejected` 的比對方式。

**詞彙漂移被誰抓到？** 把 `@sc_book_01` 的 When 改成 `When Alice reserves "Taipei" from 10:00 to 11:00`：

```
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py features/
features/booking.feature:10: GH-08 "reserves" is not glossary language; use the term "book"
1 files, 1 problems
$ pytest -q
…（略）
E   pytest_bdd.exceptions.StepDefinitionNotFoundError: Step definition is not found: When "Alice reserves "Taipei" from 10:00 to 11:00". Line 10 in scenario "A free room can be booked" in the feature "/private/tmp/lab/aibdd/ch28-gherkin-style/features/booking.feature"
…（略）
1 failed, 5 passed in 0.02s
```

兩層都擋。再做 agent 最常做的「修法」：在 `test_booking.py` 尾端補一個內容和 `books` 一樣的 `@when(parsers.parse('{who} reserves "{room}" from {start} to {end}'), target_fixture="outcome")`。`pytest -q` 變成 `6 passed in 0.06s`，lint 仍是同一則 GH-08、`1 files, 1 problems`：runner 滿意了，詞彙多了一個往後要有人維護的同義詞。GH-12 要的修法是把句子改回 `books`；runner 綠了以後，只剩 lint 在擋。實驗做完記得還原（句子改回 `books`、刪掉 `reserves` 那個 step），Ch 29–33 會從這個目錄複製 feature 與 step。

## 五、`# language: zh-TW`

第一行寫 `# language: zh-TW` 就能用中文關鍵字（gherkin-official 42.0.1 的 `gherkin-languages.json` 收了 80 種方言）。Cucumber 的 Reference 同時提醒：Gherkin 該用使用者與領域專家討論時用的語言，並避免在兩種語言之間翻譯。所以中文版不是英文版的翻譯副本，而是給「PM 用中文寫規格」的團隊的寫法，存成 `zh/features/booking.feature`：

```gherkin
# language: zh-TW
功能: 會議室預約

  Rule: R1 同一間會議室的時段不能重疊

    @sc_book_02
    場景: 相鄰的時段不算重疊
      假如 Bob 已預約 "Taipei" 10:00 到 11:00
      當 Alice 預約 "Taipei" 11:00 到 12:00
      那麼 預約成功
```

lint 對它回 `1 files, 0 problems`。`zh/` 是一個小專案：`zh/pytest.ini` 寫 `pythonpath = .. ../tests`、`testpaths = tests`，註冊 `sc_book_02`、濾掉同一條 Starlette 警告，`zh/tests/conftest.py` 照抄第四節那支，`zh/tests/test_booking_zh.py` 用 `scenarios("../features/booking.feature")` 綁中文 feature，三個 step definitions 的句型是 `{who} 已預約 "{room}" {start} 到 {end}`（呼叫 `booking.has_booked`）、`{who} 預約 "{room}" {start} 到 {end}`（呼叫 `booking.book`，`target_fixture="outcome"`）與 `預約成功`（斷言 `outcome == Outcome(True)`），全部呼叫同一個 `Booking` DSL。`cd zh && pytest -v` 跑出 `test_相鄰的時段不算重疊 PASSED`。換一種語言，只換翻譯層。注意 `Rule` 沒有翻譯：zh-TW 方言的 `rule` 只有英文 `Rule`（zh-CN 有 `规则`，zh-TW 沒有 `規則`；cucumber/gherkin 上游 main 分支今天也一樣）。照直覺寫成 `規則:` 呢？

```
$ sed 's/^  Rule: R1/  規則: R1/' zh/features/booking.feature > zh/features/booking_rule_zh.feature
$ python -c 'from gherkin import Parser; f = Parser().parse(open("zh/features/booking_rule_zh.feature").read())["feature"]; print(repr(f["description"]), [next(iter(c)) for c in f["children"]])'
'  規則: R1 同一間會議室的時段不能重疊' ['scenario']
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py zh/features/booking_rule_zh.feature
zh/features/booking_rule_zh.feature:7: GH-11 no business rule; nest it under "Rule: R<n> ..." or start its name with R<n>
1 files, 1 problems
```

沒有任何錯誤：`規則:` 那行被吞成 Feature 的描述文字，scenario 直接掛在 Feature 底下，把 `scenarios()` 指向這個檔，pytest-bdd 照樣 `1 passed`。分組靜靜消失，只有 GH-11 的「每個 scenario 要追溯到一條業務規則」把它抓出來。語言代碼也分大小寫：`# language: zh-tw` 的第一則錯誤是 `GH-00 parse error (1:1): Language not supported: zh-tw`。另外，lint 的 UI 與技術字詞清單是英文的，看不懂中文的「點擊」「按鈕」（動手練習第 3 題）。

## 六、gherkin-style skill

規則、詞彙、lint 都有了，skill 只負責流程：先讀什麼、寫完跑什麼、什麼時候算完成。`SKILL.md` 全文：

```markdown
---
name: gherkin-style
description: House standard for Gherkin feature files and their step DSL in this booking project (declarative steps, one When, glossary words only, @sc_ ID tags, thin pytest-bdd step definitions), plus the lint that checks it. Use when writing, editing or reviewing a .feature file, a Given/When/Then scenario or a step definition, even if the user does not mention style.
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/gherkin_lint.py *)
---

# Gherkin style

Rules live in `${CLAUDE_SKILL_DIR}/rules/gherkin.md` (GH-01..GH-12). Words live in `glossary.yaml` at the project root.

1. Read both files before writing. A word in an `avoid` list is a synonym we do not use.
2. Before writing a step, search `features/` and `tests/` for a phrase that already says it and reuse it word for word.
3. One scenario per example. Put it under `Rule: R<n> <rule in one sentence>` and give it the next unused `@sc_<area>_<NN>` tag.
4. Run `python ${CLAUDE_SKILL_DIR}/scripts/gherkin_lint.py --glossary glossary.yaml features/`. Fix each finding by changing the feature text. Never edit the glossary, the rules or the lint to make a finding go away. Repeat until the last line ends in `0 problems`.
5. If pytest-bdd reports a missing step whose meaning matches an existing step, rewrite the sentence (GH-12). Add a step definition only for a new domain action, and make it one call into `tests/dsl.py`.
6. If a rule looks wrong for the case at hand, keep the rule and say which ID and why in your report.

Done when the lint prints `0 problems`. Report the files you changed, the lint's last line, and any rule you would change.
```

`${CLAUDE_SKILL_DIR}` 在內文與 `allowed-tools` 裡都會被換成 skill 的絕對路徑（官方 skills 文件的 "Available string substitutions"），內文叫 agent 跑的指令剛好被 `allowed-tools` 放行。第 4 步「不准改 glossary、規則或 lint」是反直覺的那種規則：不寫，最省事的修法就是改檢查。[pstack Ch 28](../pstack/28-skill-authoring.md) 的 `skill-lint.mjs` 對它回 `1 skills, 0 problems`。

## 七、eval：agent 照做了嗎

> 以下是實際執行紀錄（節錄），每次執行結果不同；2026-11-02，三次 `claude -p`。

三個專案 `eval/A`、`B`、`C` 各自 `git init`（官方 skills 文件說 Claude Code 會往上層目錄找 `.claude/skills/` 直到 repo 根；不設邊界，可能撿到本章目錄的 skill，A 的 init 訊息確認清單裡沒有 `gherkin-style`），共用同一份 CLAUDE.md（領域、R1/R2/R4 與各自的 HTTP 狀態碼）、`app.py`、上面那份 feature 與 step definitions。A 沒有 skill 也沒有 glossary；B、C 加上兩者。prompt 相同：`Write the acceptance scenarios for business rule R4 (cancelling a booking) in features/cancellation.feature. Write only that feature file: no step definitions and no app code.` 旗標相同：`--setting-sources project --permission-prompts none --max-turns 12 --max-budget-usd 3 --no-session-persistence --output-format stream-json --verbose`；A、B 的工具是 `Read,Write,Edit,Glob,Grep,Skill`，C 多給 `Bash` 與允許規則 `Bash(python *)`（A 的 init 清單另外多出 claude.ai 連接器的 MCP 工具，B、C 沒有；整次沒被呼叫）。下表的 turns 是結果事件的 `num_turns`，約為工具呼叫數加 1，和 `--max-turns` 不是同一個計數單位（[Ch 7](./07-messy-baseline.md) 的底層機制），所以 14、15 超過 12 也沒被截斷。

| 變體 | 叫用 skill | agent 跑 lint | 事後跑 lint | 需新增的 step 句型 | turns／花費／時間 |
|---|---|---|---|---|---|
| A 沒有 skill | — | — | `2 files, 0 problems` | 7（取消有兩種說法） | 10／$0.33／34 s |
| B skill，沒有 shell | 是 | 不能 | `2 files, 0 problems` | 4（重用 `the booking is rejected because`） | 14／$0.51／77 s |
| C skill＋`Bash(python *)` | 是 | 被權限擋下 | `2 files, 0 problems` | 6 | 15／$0.50／86 s |

1. **lint 分不出這三份**。repo 裡已有一份寫得好的 `booking.feature`，`Rule: R4`、`@sc_cancel_01`、會議室加引號、`has booked` 這些 lint 查得到的慣例，三次都照著範例學會了。我們推測 lint 的價值會出現在沒有好範例、或範例本身就亂的專案；這次的設定量不到這一點。
2. **差別在 lint 看不到的地方**。「需新增的 step 句型」：把輸出的 feature 綁進 step 模組、跑 `pytest --generate-missing --feature features tests` 列出未定義步驟，再由我們把引號內容、人名、時間、編號當參數歸併。A 的清單裡同時有 `When "Bob cancels the "Taipei" booking from 10:00 to 11:00"` 與 `When "Alice cancels booking 7"`：同一個動作兩種句型，正是 GH-12 要擋的。B 重用了既有的拒絕句型，C 拿著同一份 skill 卻新造 `the cancellation is rejected because`：差別來自取樣，每組 n = 1，分不出 skill 的效果與雜訊。B、C 都用 `booking 1` 這種流水號，讓情境依賴「id 從 1 開始」這個 API 細節，也是 review 該問、lint 問不到的。要真的量，照 [pstack Ch 29](../pstack/29-eval-playbook.md) 的盲測規則，或用 [Ch 5](./05-skill-creator.md) 的 skill-creator 工具每組跑多次。
3. **「跑 lint 直到 0 problems」的迴圈一次都沒閉合**。B 沒有 shell。C 的 agent 把 skill 給的絕對路徑改成相對路徑，又把 lint、pytest、`git status` 串成一行，其中一段是 `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider`；回來的是 `Permission for this tool use was denied. It requires approval, and this session has no approval surface ...`。拒絕訊息只指出管線 `|` 前面那一整段（lint、`echo` 與這條 pytest）需要核准，沒有再細分。官方 permissions 文件說複合指令的每個子指令都要被允許，allow 規則也不會越過非白名單的環境變數賦值，我們推斷卡在帶環境變數的那條 pytest。B 的最終回覆第一句就寫 `the lint has not been run`，C 寫 `the lint never ran`，兩者都引用 skill 的 Done when 條件、沒有謊報完成；但檢查終究沒跑。（eval 之後我們整理過 lint 的錯誤處理，也把規則改成接續 Ch 9 的編號與格式：agent 當時讀到的是舊編號 GH-01..GH-13、條列寫法、觸發條件全檔共用一行的版本，檢查內容相同。三份輸出用最終版重跑，結果如上表。）

## 底層機制：parser、型別推導與兩個安靜的洞

```
 .feature 文字 ─▶ TokenScanner（一行一個 token）
   ─▶ TokenMatcher：第一行 # language: 決定方言；關鍵字由長到短比對
        "* " 同時在 given/when/then/and/but 清單 → "Unknown"；And/But → "Conjunction"；認不出的行 → #Other
   ─▶ Parser（文法狀態機）：Feature/Rule 標題之後的 #Other 一律是 Description
   ─▶ GherkinDocument（AST）：feature → children → background / scenario / rule
        ├─ pytest-bdd：Conjunction 沿用前一步的型別；feature.scenarios[name] = scenario
        └─ gherkin_lint.py：同一棵 AST、同一套型別推導，再套 GH 規則
```

第一，型別。gherkin-official 的 `token_matcher.py` 第 88 行：一個關鍵字出現在不只一張清單裡，型別就是 `Unknown`。pytest-bdd 的 `parser.py` 第 401 行用 `STEP_TYPE_BY_PARSER_KEYWORD.get(step.keyword_type, current_type)` 讓這種步驟沿用前一步，第一步就是 And 時直接報 `StepError`。lint 的 `typed()` 照抄這個規則，否則 GH-04 數出的 When 個數會和實際執行不同。第二，描述文字。文法是 `FeatureHeader := #Language? Tags? #FeatureLine DescriptionHelper`：標題之後、下一個關鍵字之前的任何行都是描述。方言裡沒有的詞（`規則:`）不是錯誤，是描述，所以 parser 不抱怨。第三，scenario 的儲存。pytest-bdd 9.0.1 的 `parser.py` 第 517、539 行是 `feature.scenarios[scenario.name] = scenario`，一個以名稱為 key 的 dict。把 `@sc_book_02` 改成和 `@sc_book_01` 同名，`pytest --collect-only -q` 回 `5 tests collected in 0.01s`（原本 6 個），`pytest -q -m sc_book_01` 回 `5 deselected in 0.00s`：前一個情境連同它的 ID tag 一起消失，沒有任何警告；lint 回 `features/booking.feature:14: GH-11 scenario name repeats line 8; pytest-bdd keeps only one`（實驗完把名稱改回原文，後面幾章會複製這個 feature）。這兩個洞（被吞掉的 `規則:`、被蓋掉的同名 scenario）有同一個特徵：runner 照樣綠，只有知道慣例的檢查會叫。順帶一提，ID tag 的底線不是 pytest 逼的：pytest 9.2.0 的 `-m` 接受連字號，`@sc-book-01` 用 `-m sc-book-01` 選得到（實測 `1 passed, 1 deselected`）。我們仍規定小寫加底線，因為同一個 ID 會出現在 Python 程式裡（`@pytest.mark.sc_book_01` 必須是合法識別字），全課 grep 也一致。

## 對比與取捨

先試現成工具。gherkin-lint 4.2.4（npm，最新 release 2023-12-20，依賴 gherkin 9.0.0 parser）有 `only-one-when`、`keywords-in-logical-order`、`scenario-size`、`required-tags`、`no-restricted-patterns` 等規則。我們在章節目錄的 `node-cmp/` 用 `npm install gherkin-lint@4.2.4` 裝好，`.gherkin-lintrc` 開這幾條：`only-one-when`、`keywords-in-logical-order`、`no-dupe-scenario-names`、`no-background-only-scenario`、`scenario-size`（Background 4、Scenario 5）、`file-name`（snake_case）、`required-tags`（`^@sc_[a-z]+_\d{2}$`）、`no-restricted-patterns`（Global：click、press、fills in、page、API、reserve），跑同兩份檔案。有 `Rule:` 的好檔案讓它直接崩潰，`TypeError: Cannot read properties of null (reading 'keyword')`，關掉 `no-restricted-patterns` 就跑得完；上游 issue #347（有 `Rule:` 就崩潰）與兩個處理 `Rule:` 的 PR #286、#350 都還開著。壞檔案它找到 9 則，漏掉 Given 寫成動作、沒有 Then、Background 裡的 When、含 Background 的步數、參數慣例、單列 Outline，以及完全沒 tag 的第 7 行：`required-tags` 的 `ignoreUntagged` 預設是 `true`，原始碼也只走 `feature.children`，掛在 `Rule:` 底下的 scenario 根本不檢查。

| 做法 | 抓得到 | 抓不到 | 成本 | 適合 |
|---|---|---|---|---|
| gherkin-lint 4.2.4 | 結構：一個 When、順序、步數、檔名、禁用字串 | 詞彙、Given 寫成動作、Outline 欄位；有 `Rule:` 會崩潰或漏查 | Node 依賴＋設定檔 | 不用 `Rule:`、只要結構檢查 |
| 自寫 lint（本章） | 11 條，含 glossary 與 ID 慣例；和 runner 同一個 parser | 語意、句型重複（GH-12）、中文字詞 | 122 行自己維護 | 有自己詞彙與 ID 慣例的團隊 |
| LLM reviewer | 湊數步驟、流水號依賴、Then 測錯東西 | 不保證每次看到，有變異 | 每次花 token | lint 之後的第二道 |
| skill（規則＋跑 lint 的指令） | 讓 agent 寫之前讀到規則 | agent 不一定跑檢查（第七節） | 觸發時載入 | 寫作階段 |
| PostToolUse hook（進階） | 每次寫 `.feature` 都跑 lint | 同自寫 lint | 設定一次 | 要把檢查變成必經之路 |

## 踩雷集錦

1. **錯誤直覺：lint 0 problems 就是好情境**。正確認識：0 problems 只代表 11 條機械規則沒被違反。eval 三份全是 0，差別在取消動作的兩種說法和流水號依賴；GH-05 的下限還會誘使 agent 塞 `Given the system is running` 湊數，lint 放行，review 要刪。
2. **錯誤直覺：zh-TW 的規則分組寫 `規則:`**。正確認識：gherkin-official 42.0.1 的 zh-TW 方言只有 `Rule`；`規則:` 被吞成描述，分組消失，測試照綠。語言代碼也要照大小寫寫 `zh-TW`。
3. **錯誤直覺：同名 scenario 只是報告不好看**。正確認識：pytest-bdd 9.0.1 用名稱當 dict key，前一個 scenario 整個被蓋掉，它的 ID tag 也選不到。
4. **錯誤直覺：pytest-bdd 說找不到 step，就補一個 step definition**。正確認識：測試會綠，詞彙多一個同義詞。先找既有句型是否說同一件事，有就改句子（GH-12）；runner 綠了之後只剩 GH-08 在擋。
5. **錯誤直覺：skill 裡寫了「跑 lint 直到 0 problems」，檢查就會發生**。正確認識：C 組的 agent 把指令改寫成權限規則接不住的複合指令，被自動拒絕。指令是建議；要必經，交給 harness。

## 進階：把 lint 接成 PostToolUse hook

第七節斷掉的那一環，答案是不靠 agent 記得：[Ch 3](./03-propose-constrain-feedback.md) 的 `run_tests.py` 已經定好課程 hook 的介面（stdin 讀事件 JSON；紅用 `decision: block` 加 `reason`，綠用 `additionalContext`，一律 exit 0；settings 用 exec 形式指定 venv 的 Python），套到 lint 上就是一支 `.claude/hooks/gherkin_lint_hook.py`：`tool_input.file_path` 結尾是 `.feature` 才動作，對那一個檔案跑 lint，把輸出原文交回 Claude。hook 由 harness 直接執行，不是 agent 的 Bash 呼叫，不經過 C 組撞上的 allow 規則比對；Ch 3 實跑時也看到 PostToolUse hook 在 `claude -p`、未信任的資料夾裡照樣觸發。寫這支 hook 有一個值得先踩一次的坑。第一版只看 lint 的 exit code，非 0 就回「找到問題，去改 feature」；用這台機器的系統 Python 3.9 跑它（[Ch 0](./00-environment-setup.md) 提過 user 設定會讓 hook 看到 `/usr/bin/python3`），一份完全正確的 feature 得到 `gherkin_lint found problems ...; fix the feature text:` 加上 `ModuleNotFoundError: No module named 'yaml'`，agent 會開始「修」一份沒壞的檔案。lint 掛掉與找到問題都是 exit 1，所以 hook 要看最後一行是不是 `N files, M problems`，不是就說「lint 沒跑成，檔案沒被檢查」。我們的參考實作（24 行，不附在本章）用手寫的事件 JSON 測過四條路，沒有在 `claude -p` 裡實跑（本章的三次額度用在 eval），規格寫在動手練習第 1 題。

## 動手練習

1. **寫 `gherkin_lint_hook.py`**：照上一節的介面寫，四個事件要得到這四種結果：改好的 `features/booking.feature` → `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "gherkin_lint: 1 files, 0 problems"}}`；改 `tests/dsl.py` → 沒有輸出、exit 0；寫 `bad/Booking-UI.feature` → `decision: block`，`reason` 以 `gherkin_lint found problems in` 開頭並附上 20 則發現；用 `/usr/bin/python3` 跑第一個事件 → `decision: block`，`reason` 說出 `could not run` 與 `ModuleNotFoundError`。再照第七節 C 組的設定建一個專案，裝上它重跑一次 `claude -p`，看 transcript 裡有沒有 `gherkin_lint:` 的回饋。
2. **把 GH-12 變成數字**：寫一支小腳本，把 `pytest --generate-missing` 列出的未定義步驟正規化（引號內容、人名、時間、數字換成佔位符）再去重。eval A 列出的 9 句是 Given `Bob has cancelled the "Taipei" booking from 10:00 to 11:00`、Given `there is no booking 7`、Then `Bob still has "Taipei" from 10:00 to 11:00`、Then `the booking is cancelled`、Then `the cancellation is rejected because booking 7 not found`、Then `the cancellation is rejected because only Bob can cancel this booking`、When `Alice cancels booking 7`、When `Alice cancels the "Taipei" booking from 10:00 to 11:00`、When `Bob cancels the "Taipei" booking from 10:00 to 11:00`；對它算句型數，和本章手算的 7 比對。你的正規化在哪些情況會算錯？
3. **中文專案的 lint**：替 `zh/` 補一份中文 glossary（`actions: {預約: 已預約}`，`avoid` 放「訂位」「保留」）與一組中文 UI 字詞（點擊、按鈕、頁面、欄位）。證明 `zh/features/booking.feature` 沒有誤報，再寫一個 `當 Alice 點擊「預約」按鈕` 的壞例子證明抓得到。

## 本章重點整理

- 散在四處的 Gherkin 規則收成 GH-01..GH-12（前四條沿用 Ch 9 的編號），編號穩定、每條有好壞例子和「誰在擋」；11 條寫成 lint，GH-12（一個動作一種句型）留給 review 與 pytest-bdd 的找不到 step。
- lint 建在 pytest-bdd 用的同一個 gherkin-official parser 上，型別推導照抄 runner；兩個 runner 照綠的洞（zh-TW 的 `規則:` 被吞成描述、同名 scenario 被 dict 蓋掉）只有 lint 叫。
- DSL 是句子與系統之間的領域動詞層：step definition 拆參數、呼叫一次 DSL；runner 會接受同義詞的 step definition，詞彙表不會。
- 三次 `claude -p` 的輸出都通過 lint，差別在 lint 看不到的詞彙決定；n = 1 只是煙霧測試。「叫 agent 跑 lint」的迴圈沒有閉合，要必經就交給 PostToolUse hook。

## 自我檢核

- [ ] 不看表，說出 GH-01..GH-12 裡至少八條，以及哪一條沒有機器擋、為什麼擋不了。
- [ ] 為什麼 lint 要讀 gherkin-official 的 AST、還要照抄 pytest-bdd 的 And/But 型別推導？舉一個逐行正規表示式會誤判的例子。
- [ ] 一個 agent 補上 `reserves` 的 step definition、測試轉綠，你怎麼說明為什麼還不能合併？
- [ ] 在 zh-TW feature 裡寫 `規則:` 會發生什麼？pytest-bdd 會不會報錯？本章哪條規則間接抓到它？
- [ ] 本章的 eval 能不能推論「skill 讓 agent 寫出比較好的 Gherkin」？說出兩個不能的理由，以及你會怎麼重新設計實驗。

## 延伸閱讀

- **[Cucumber — Gherkin Reference](https://cucumber.io/docs/gherkin/reference/)**（官方文件）
  - **讀哪裡**：Steps、Background 的 "Tips for using Background"、Rule（Gherkin v6 起）、Spoken Languages。**學到什麼**：「每個例子 3–5 步」與「Background 超過 4 行就收進更高階步驟」的原文，以及「用領域專家的語言、避免兩種語言互譯」。**和本章的關聯**：GH-05、GH-06 兩個數字的出處，也是第五節中文 feature 的立場。**前提**：Ch 26 的關鍵字巡禮。
- **[Cucumber — Writing better Gherkin](https://cucumber.io/docs/bdd/better-gherkin/)**（官方文件）
  - **讀哪裡**：宣告式與命令式兩組範例，以及檢查問句「實作改了，這句話要不要跟著改？」。**學到什麼**：宣告式描述行為、命令式描述操作，以及怎麼把一段命令式情境改寫成宣告式。**和本章的關聯**：GH-03 的論證；它的「實作細節藏進 step definitions」是本章 DSL 層的起點。
- **[Seb Rose — Keep your scenarios BRIEF](https://cucumber.io/blog/bdd/keep-your-scenarios-brief)**（Cucumber 部落格，2019-09-05）
  - **讀哪裡**：全文，五個字母加上 Brief 本身，六個原則各一段。**學到什麼**：Essential（刪掉不服務規則的部分）與 Brief（大多數情境五行以內）的原始說法；作者是 BDD Books 系列作者之一。**和本章的關聯**：第一節 BRIEF 對照的出處，GH-05 的上下限由此而來。
- **[Dan North — Whose domain is it anyway?](https://dannorth.net/blog/whose-domain-is-it-anyway/)**（2011-01-31）
  - **讀哪裡**：全文，重點是「情境只該涉及問題領域與解法領域」與把登入步驟從欄位操作改寫成「提交有效憑證」的例子。**學到什麼**：多出來的領域為什麼只帶來雜訊、讓測試變脆。**和本章的關聯**：BDD 提出者對宣告式步驟的論證；glossary 與 DSL 層是把「只用利害關係人的語言」變成可檢查的東西。
- **[gherkin-lint（GitHub）](https://github.com/gherkin-lint/gherkin-lint)**
  - **讀哪裡**：README 的 Available rules 與 `required-tags`、`no-restricted-patterns` 的設定，再看 issue #347 與 PR #286。**學到什麼**：現成 linter 涵蓋哪些結構規則（值得抄的清單），以及它為什麼不適合用 `Rule:` 分組的專案。**和本章的關聯**：「對比與取捨」那段實測的對象，GH-04 的 `only-one-when` 也出自它。**前提**：會用 npm；本章的對照裝在章節目錄，沒有裝到全域。

寫作標準管的是「一個例子怎麼寫」；例子從哪裡來、誰在場決定哪條規則需要例子，是下一章的事。

→ [Ch 29 Example Mapping 與 Three Amigos：Journey 型與 Rule 型情境](./29-example-mapping.md)
