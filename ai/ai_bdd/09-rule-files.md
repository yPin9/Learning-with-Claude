# Ch 9 — 帶範例的 Rule File 與規則提取器

> **目標**：寫出一份每條規則都有穩定 ID、觸發條件、好／壞／邊界三個例子與「由誰強制」的 rule file；用 `rule_extract.py` 從團隊**說的**（CLAUDE.md）、**罵的**（PR review 留言）、**做的**（`.feature` 檔）三種證據提取候選規則，每條附出處與例子交給人審；再用三次真實的 `claude -p` 做 A/B，看例子改變了 agent 輸出的哪一部分。

> **環境**：Python 3.12.15、PyYAML 6.0.3、pytest-bdd 9.0.1（`source /tmp/lab/aibdd/.venv/bin/activate`）；Claude Code 2.1.295，A/B 用預設模型 claude-opus-5-5，2026-11-02 執行；macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch09-rules/`，從空目錄開始。CLAUDE.md、`legacy.feature` 與 PR 留言是為示範而構造的樣本。

## 為什麼需要這個？

「Given 要寫狀態，不要寫動作。」團隊每個人都點頭，然後寫出三種 Given。agent 也一樣：它讀懂這句話，沒說到的部分用先驗補上，而先驗是網路上 Gherkin 的平均值，不是你們的寫法。[Ch 4](./04-prompt-files-to-skills.md) 把規則從常駐的 CLAUDE.md 搬進按需載入的 skill，[Ch 7](./07-messy-baseline.md) 量單檔 skill 裡有多少規則沒有例子；搬家和計數都沒改變一件事：規則本身還是一句話。

給人看的規範早就不這樣寫。[PEP 8](https://peps.python.org/pep-0008/) 現在的版本把範例標成 `# Correct:` 或 `# Wrong:`；ESLint 的規則有穩定的名字，[`no-unused-vars` 的文件頁](https://eslint.org/docs/latest/rules/no-unused-vars)有 incorrect 與 correct 兩組例子，還有一節「When Not To Use It」。寫給 agent 的 prompt file 卻退回一行一句的 bullet。這個 repo 已經有零件：[pstack Ch 11](../pstack/11-principles-as-vocabulary.md) 的「三、leaf 檔的結構」、[pstack Ch 23](../pstack/23-unslop-no-comments.md) 的「一、unslop：有編號的規則」、[pstack Ch 15](../pstack/15-delegation-meta-autonomy.md) 的「三、Encode Lessons in Structure」（需要判斷的規則要「add an example of the failure mode」）、[ai_applications Ch 15](../ai_applications/15-skills.md) 的「提供具體範例」。缺的是把它們組成 skill 的一個模組：每條規則帶幾個例子、放在哪裡、怎麼編號讓脊椎（spine，即 SKILL.md 本體）引用。

規則也不是從空白頁寫出來的。它們散在 repo 裡：寫在 CLAUDE.md 的、在 review 裡一再被糾正的、在程式碼裡成為習慣的。Bacchelli 與 Bird 在 Microsoft 的研究發現，code review 比預期的少在抓缺陷、多在傳遞知識（ICSE 2013），團隊沒寫下來的規矩就藏在這些留言裡。[pstack Ch 30](../pstack/30-self-improvement-loop.md) 的「二、/correct」描述了讀這些來源、把錯誤歸類的流程，但那章沒有實際執行，這個 repo 也沒有一支能跑的提取器。

## 先建立直覺：字典裡的一個詞條

給學習者用的英文字典，「affect」那一條不會只有一句釋義，常見的還有兩三個例句、一個常見誤用，和一段分辨 affect 與 effect 的用法說明。讓人學會的是例句，尤其是那組對比。編字典的人也不憑空造句，而是從大量真實用法裡挑。rule file 是詞條，提取器是挑例句的那一步：

```
 說的  CLAUDE.md 的 bullet ───┐  rule_extract.py
 罵的  PR review 留言 ────────┼─▶ 依 signatures.yaml ─▶ 候選＋出處＋例子 ─▶ 人審 ─▶ rules/gherkin.md ─▶ SKILL.md 引用 ID
 做的  features/*.feature ────┘  歸到同一類               （不改任何規則）     收／退／待議
```

比喻有一處不成立：字典描述大家怎麼用，rule file 規定大家該怎麼寫。多數人的寫法不等於規則，所以提取器不做多數決：做到的比例只是一項證據，文件或留言說了反話就標成衝突，都交給人決定。

## 一、Rule file：一條規則、三個例子、一個 ID

下面是本章結束時的 `rules/gherkin.md`。GH-01、GH-02 是從 [SDD Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md)「踩雷集錦」的錯誤直覺 1（Given 裡混入操作）與 3（Then 漏掉可觀察的結果）種下的；GH-03、GH-04 是第三、四節由提取器加人審得來的，先放在一起看格式：

| 欄位 | 寫什麼 | 為什麼要有 |
|---|---|---|
| `## GH-01 標題` | 穩定 ID 與一句標題 | review、commit、回報都用 ID 引用；退休的規則留空號，號碼不重用（同 pstack Ch 23 的 unslop） |
| `Trigger:` | 什麼情境要想起它 | 沒有觸發條件的規則永遠適用，也就永遠不會被特別想起（pstack Ch 11「二、索引的格式」） |
| `Rule:`／`Why:` | 一句做法；理由只在反直覺時寫 | 理所當然的規則加理由，只是多佔 context |
| `Sign you skipped it:` | 從產物看得出的違規徵兆 | 違規看得見，才可能被 grep、被寫成 lint |
| `Enforced by:` | 現在誰在擋 | 就是 /correct 的「規則 → 由什麼強制」表；寫著 review 的，是下一個該變成 lint 的候選 |
| `Good`／`Bad`／`Edge` | 三個例子 | 好例子給形狀與措辭，壞例子從外面畫線，邊界例子標出直覺會出錯的地方 |

````markdown
# Gherkin rules

Read this before you write or review a `.feature` file. Cite rules by ID (`GH-02`) in reviews,
commits and reports. IDs are stable: a retired rule leaves a gap and its number is never reused.

## GH-01 Given describes a state, not an action
Trigger: writing or reviewing a `Given` (or an `And` under it).
Rule: a `Given` states a fact that already holds before the action; the action goes in the one `When`.
Sign you skipped it: a `Given` whose verb is something a user does now: opens, clicks, fills, books.
Enforced by: review (no lint yet)
Good: `Given room "Taipei" is free all day`
Bad (two actions dressed as setup; which one is under test?):
```gherkin
Given Alice opens the booking page
And Alice books "Taipei" from 10:00 to 11:00
```
Edge (a finished action is a state: "has booked" is fine, "books" is not): `Given Bob has booked "Taipei" from 10:00 to 11:00`

## GH-02 Then states every outcome a user can observe
Trigger: writing or reviewing a `Then`.
Rule: a `Then` names what a user sees: confirmed, or rejected together with the reason shown.
Why: a bare "rejected" also passes when the app rejects for the wrong rule, e.g. R2 firing in an R1 scenario.
Sign you skipped it: a rejection without a reason, or a `Then` about internal state (lists, counters, tables).
Enforced by: review (no lint yet)
Good: `Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob`
Bad (passes for any rejection, including the wrong one): `Then the booking is rejected`
Edge (for a success, "confirmed" is the whole outcome; don't pad it with internal state): `Then the booking is confirmed`

## GH-03 Steps use domain words, not HTTP or UI mechanics
Trigger: a step that touches an API, a page, a status code or a selector.
Rule: steps say what the user does and sees; URLs, JSON, status codes, selectors and clicks live in the driver.
Why: a status code looks more precise than words, but the same scenario also runs through the UI driver, which has no 409 to see.
Sign you skipped it: a number like 403, a path like /bookings, braces, or page, button, clicks, fills in a step.
Enforced by: review (no lint yet)
Good: `When Alice books "Taipei" from 10:30 to 11:30`
Bad (only the API driver can run this):
```gherkin
When Alice posts {"room": "Taipei", "start": "10:30", "end": "11:30"} to /bookings
Then the response status is 409
```
Edge (quoting the reason a user reads is behaviour, even though the API returns the same text): `Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob`

## GH-04 One When per scenario
Trigger: a scenario seems to need a second action.
Rule: a scenario has exactly one action; whatever happened before it is a `Given` state.
Sign you skipped it: `When ... And ...` with two actions, or one `When` line that says "and then".
Enforced by: review (no lint yet)
Good:
```gherkin
Given Bob has booked "Taipei" from 10:00 to 11:00
When Alice books "Taipei" from 11:00 to 12:00
Then the booking is confirmed
```
Bad (the first booking is setup written as an action):
```gherkin
When Bob books "Taipei" from 10:00 to 11:00
And Alice books "Taipei" from 10:30 to 11:30
Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
```
Edge (one line can hide two actions; count actions, not lines): `When Alice books "Taipei" from 11:00 to 12:00 and then cancels it`
````

幾個設計決定：

- **每條三個例子，各有分工。** Anthropic 的提示文件建議給 3–5 個例子，並要它們「vary enough that Claude doesn't pick up unintended patterns」；那是針對整個任務。rule file 一次載入很多條，所以每條固定三個，要加第四個，得說得出它在哪個維度上和前三個不同。
- **例子緊貼規則，不放 `examples/`。** 讀一次就拿到規則和例子，改規則的 diff 也一起改到例子。整份檔案大小、或被幾條規則共用的範例才放 `examples/`（[ai_applications Ch 15](../ai_applications/15-skills.md)「Supporting Files」的 good-review.md 就是這種）。單步例子寫在行內、多步才用 code block，因為這是按需載入的 context，載入後整個 session 都在（[Ch 4](./04-prompt-files-to-skills.md)「五、漸進揭露在哪裡失靈」）。規則用英文寫，和 Gherkin 步驟同一種語言。
- **脊椎只放一句指向它的話**，例如 `Read rules/gherkin.md before writing a .feature file; cite the ID of every rule you applied or broke on purpose.`。把它接進 `bdd` skill 是 [Ch 11](./11-skill-engineering-skill.md) 的事；欄位齊不齊可以機械檢查（動手練習 3），少了例子會怎樣，第五節用 A/B 量給你看。

SDD Ch 10 另外三條沒收：錯誤直覺 2 的「3-7 個有意義的 Scenario」沒有出處，先當 review 的警訊；錯誤直覺 4（Three Amigos）講流程不講產物，歸 [Ch 29](./29-example-mapping.md)；錯誤直覺 5 的正確認識建議「每個 Scenario 有獨立清晰的 step」，和本課相反（[Ch 30](./30-one-gherkin-two-drivers.md) 讓兩個 driver 共用同一組步驟），它擔心的隱藏狀態靠 `target_fixture` 與每個情境重建狀態處理。收下的錯誤直覺 3 也只收一半：它把資料庫狀態的變化也列為 Then 常漏的結果，GH-02 只收使用者看得到的，和 Cucumber 的 [Gherkin Reference](https://cucumber.io/docs/gherkin/reference/) 同一邊：結果要落在系統的輸出上，不是「a record in a database」。

## 二、為什麼例子比指令強

一句指令是壓縮過的描述，agent 用先驗把它解壓：句子沒說到的部分（用哪個動詞、拒絕訊息怎麼寫），它就用自己最熟的寫法補上。例子則是直接從你們的分布裡抽出的樣本。

Min et al.（EMNLP 2022）在分類與選擇題任務上把 in-context 範例的標籤隨機換掉，效果幾乎不掉；起作用的是範例展示了輸出的空間、輸入的分布與格式。搬到 rule file：例子最有力的是傳遞形狀與措辭。團隊那些任意的選擇（說 `has booked` 不說 `has a booking for`、拒絕訊息長什麼樣）本來就只能靠例子傳；反過來，模型可能連壞例子的形狀也一起模仿，所以壞例子要緊貼標籤與理由，旁邊有好例子對照。這是我們從研究推出的寫法，研究本身沒有測 rule file。

邊界例子的道理更早。Winston 1970 年在 MIT 的博士論文讓程式從正例與 near miss（只差一個關鍵特徵就會是正例的反例）學出「拱門」：一個差一點的反例，比許多差很遠的反例更能指出哪個特徵是關鍵。GH-01 的 Edge 就是一組 near miss：`has booked` 和 `books` 只差時態，一個是狀態，一個是動作。

## 三、規則提取器：說的、罵的、做的

提取器不發明分類。有哪些類，由人維護的分類表 `signatures.yaml` 決定；腳本做確定性的部分：歸類、計數、定位、附上例子與出處、標出衝突與沒分到類的剩餘。實驗目錄最後會有 `rule_extract.py`、`signatures.yaml`、`rules/gherkin.md` 與 `project/`（`CLAUDE.md`、`reviews.json`、`features/`）。「做的」用 [Ch 0](./00-environment-setup.md)「六、冒煙測試」的 `smoke/features/booking.feature`（複製到 `project/features/`），加上一份照舊習慣寫的 `project/features/legacy.feature`：

```gherkin
Feature: Legacy booking checks

  Scenario: R2 a booking that ends before it starts is rejected
    Given room "Seoul" is free all day
    When Carol books "Seoul" from 11:00 to 10:00
    Then the response status is 422

  Scenario: book a room from the form
    Given Alice opens the booking page
    And Alice fills "Room" with "Tokyo"
    When Alice clicks "Book"
    Then the status line shows "Booked Tokyo"

  Scenario: R4 cancelling twice
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Bob cancels booking 1
    And Bob cancels booking 1
    Then the response status is 404
```

「說的」是 `project/CLAUDE.md`：

```markdown
# booking

- Run all tests with `pytest -q` from the repo root.
- One When per scenario; a second action is a second scenario.
- Then steps check the HTTP status code first.
- Scenario titles start with the rule ID, e.g. `R1 adjacent slots do not overlap`.
```

「罵的」是 `project/reviews.json`，欄位取自 GitHub REST API 的 pull request review comment，只留腳本用到的幾個；真實資料用 `gh api repos/OWNER/REPO/pulls/comments --paginate --slurp` 匯出（`--slurp` 把多頁包成外層陣列，腳本兩種形狀都收）：

```json
[
{"id": 101, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/41", "path": "features/legacy.feature", "line": 9, "diff_hunk": "@@ -7,0 +8,2 @@\n+  Scenario: book a room from the form\n+    Given Alice opens the booking page", "body": "Given should describe a state, not an action. Opening the page is how the UI gets there, not a precondition of the rule.\n```suggestion\n    Given room \"Tokyo\" is free all day\n```"},
{"id": 102, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/44", "path": "features/legacy.feature", "line": 18, "diff_hunk": "@@ -16,0 +17,2 @@\n+    And Bob cancels booking 1\n+    Then the response status is 404", "body": "Please don't put status codes in the feature. The UI driver can't see a 404; say what the user sees.\n```suggestion\n    Then the cancellation is refused because \"booking 1 not found\"\n```"},
{"id": 103, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/44", "path": "features/legacy.feature", "line": 18, "in_reply_to_id": 102, "diff_hunk": "@@ -17,0 +18 @@\n+    Then the response status is 404", "body": "Agreed, will fix in a follow-up."},
{"id": 104, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/47", "path": "features/legacy.feature", "line": 6, "diff_hunk": "@@ -4,0 +5,2 @@\n+    When Carol books \"Seoul\" from 11:00 to 10:00\n+    Then the response status is 422", "body": "Same as in #44: no HTTP details in Gherkin. The status code belongs in the API driver."},
{"id": 105, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/47", "path": "features/booking.feature", "line": 12, "diff_hunk": "@@ -10,0 +11,2 @@\n+    Scenario: Booking an overlapping slot\n+      Given Bob books \"Taipei\" from 10:00 to 11:00", "body": "This Given is an action (\"books\"). Write the state it leaves behind.\n```suggestion\n      Given Bob has booked \"Taipei\" from 10:00 to 11:00\n```"},
{"id": 106, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/52", "path": "features/legacy.feature", "line": 17, "diff_hunk": "@@ -15,0 +16,2 @@\n+    When Bob cancels booking 1\n+    And Bob cancels booking 1", "body": "Two Whens: the first cancel is setup. Make it a state, e.g. `Given Bob has cancelled booking 1`."},
{"id": 107, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/52", "path": "features/booking.feature", "line": 13, "diff_hunk": "@@ -12,0 +13 @@\n+      When Alice books \"Taipie\" from 10:30 to 11:30", "body": "nit: typo, Taipie -> Taipei"},
{"id": 108, "pull_request_url": "https://api.github.com/repos/acme/booking/pulls/55", "path": "features/booking.feature", "line": 2, "diff_hunk": "@@ -2 +2 @@\n-Feature: Booking\n+Feature: Room booking", "body": "Given the state of the release branch, let's hold this until Monday."}
]
```

這幾個欄位先查過。GitHub 的 OpenAPI 描述對 `diff_hunk` 只寫「The diff of the line that the comment refers to」，沒說那一行在哪裡；我們拿 cli/cli 最近 800 則公開留言核對（2026-11-02），`diff_hunk` 不是空的 793 則，最後一行都正是留言當時（`original_line`）指的那一行，腳本把它當壞例子。同一批有 196 則的 `line` 是 null，`original_line` 都在，所以出處的行號缺 `line` 時改用它。審查者在留言裡插入的建議修改（suggested change），是 `body` 裡一個以 `suggestion` 為語言標記的 code block（同一批有 63 則，腳本的 regex 全部抓得到），腳本把它當好例子。

`signatures.yaml` 的每一類有 `for`（支持這條規則的說法）、選填的 `against`（說反話的說法）與 `probe`（在 feature 檔上量的函式）；收進 rule file 之後才有 `id`：

```yaml
given-state:
  id: GH-01
  title: Given describes a state, not an action
  for: 'given\b.{0,40}(state|action|狀態|操作)'
  probe: given_state
then-outcome:
  id: GH-02
  title: Then states every outcome a user can observe
  for: 'then\b.{0,40}(observable|outcome|side effect|可觀察|副作用)'
mechanics:
  title: Steps use domain words, not HTTP or UI mechanics
  for: 'status code|\bhttp\b|\burl\b|endpoint|selector|json'
  against: 'then\b.{0,30}(status code|\bhttp\b)'
  probe: no_mechanics
one-when:
  title: One When per scenario
  for: 'one when|two whens|second when|second action|一個 when'
  probe: one_when
```

第一次跑的時候 rule file 只有 GH-01、GH-02，用前 27 行模擬：

```
$ head -27 rules/gherkin.md > /tmp/gherkin-v1.md
$ python rule_extract.py --signatures signatures.yaml --rules /tmp/gherkin-v1.md \
    --doc project/CLAUDE.md --reviews project/reviews.json --features project/features; echo "exit=$?"
GH-01 given-state: Given describes a state, not an action  [covered]
  enforced  PR #41 features/legacy.feature:9  Given should describe a state, not an action. Opening the page i
  enforced  PR #47 features/booking.feature:12  This Given is an action ("books"). Write the state it leaves beh
  enforced  PR #55 features/booking.feature:2  Given the state of the release branch, let's hold this until Mon
  practiced 4/5 scenarios (80%)
  bad       PR #41 features/legacy.feature:9  Given Alice opens the booking page  (+2 more)
  good      PR #41 suggestion  Given room "Tokyo" is free all day  (+3 more)
  evidence  enforced in 3 PRs, practiced -> medium

new mechanics: Steps use domain words, not HTTP or UI mechanics  [new]
  enforced  PR #44 features/legacy.feature:18  Please don't put status codes in the feature. The UI driver can'
  enforced  PR #47 features/legacy.feature:6  Same as in #44: no HTTP details in Gherkin. The status code belo
  CONFLICT  CLAUDE.md:5  - Then steps check the HTTP status code first.
  practiced 2/5 scenarios (40%), below --min-adherence 0.8
  bad       PR #44 features/legacy.feature:18  Then the response status is 404  (+2 more)
  good      PR #44 suggestion  Then the cancellation is refused because "booking 1 not foun  (+2 more)
  evidence  enforced in 2 PRs -> low

new one-when: One When per scenario  [new]
  stated    CLAUDE.md:4  - One When per scenario; a second action is a second scenario.
  enforced  PR #52 features/legacy.feature:17  Two Whens: the first cancel is setup. Make it a state, e.g. `Giv
  practiced 4/5 scenarios (80%)
  bad       PR #52 features/legacy.feature:17  And Bob cancels booking 1
  good      booking.feature:8  When Alice books "Taipei" from 10:00 to 11:00  (+3 more)
  evidence  stated, practiced -> medium

unclassified (no class matched; a human reads these):
  CLAUDE.md:3  - Run all tests with `pytest -q` from the repo root.
  CLAUDE.md:6  - Scenario titles start with the rule ID, e.g. `R1 adjacent slots do not
  PR #52 features/booking.feature:13  nit: typo, Taipie -> Taipei
3 candidates (1 covered, 2 new), 1 conflict, 3 unclassified
exit=1
```

讀法：

- **GH-01** 已經在 rule file 裡，兩個 PR 真的在擋，5 個情境 4 個做到。第三筆 enforced 是誤判：PR #55 的「Given the state of the release branch」是英文的 given，不是 Gherkin 的 Given。印出原文，人一眼就看得出來；只印數字，它就混進「3 PRs」了。
- **mechanics** 是這次的收穫：沒人寫下來，兩個 PR 都在擋，5 個情境只有 2 個做到。CONFLICT 指出原因：CLAUDE.md 第 5 行要大家「Then steps check the HTTP status code first」，那是只有 API 測試時留下的規定，兩個寫狀態碼的情境正是照它寫的。
- **one-when** 寫在 CLAUDE.md、4/5 做到，review 只提過一次，不到 pstack Ch 30 /correct 的門檻：「A class counts once it has happened twice.」
- **unclassified** 三筆是腳本認不出的，只能人讀。錯的輸入則以 2 結束並說出修法，例如把單一個 PR 物件當留言餵進去，會得到 `expected a JSON list of review comments, got a dict; export with: gh api repos/OWNER/REPO/pulls/comments --paginate --slurp`。

腳本全文（148 行，Python 3.12 標準庫加 PyYAML）：

<details>
<summary>rule_extract.py</summary>

```python
#!/usr/bin/env python3
"""rule_extract.py: turn what a team states, enforces and practices into candidate rules.

Joins evidence on the classes in --signatures and prints a review sheet; it never edits a rule file.
  stated     bullets in --doc markdown files (CLAUDE.md, a style guide)
  enforced   PR review comments (GitHub REST JSON); a class counts from 2 distinct PRs
  practiced  probes over --features: scenarios that follow / scenarios the probe applies to
Exit 1 if a statement contradicts a class (a human must pick a side), 2 on bad input.
"""
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path
import yaml

ACTION = re.compile(r"^\w+\s+(\w+\s+){0,2}(opens?|clicks?|fills?|types?|submits?|books?|cancels?)\b", re.I)
MECHANICS = re.compile(r"\b(status|response|page|button|clicks?|fills?)\b|\b[1-5]\d\d\b|/\w|[{}]", re.I)
EXPORT = "export with: gh api repos/OWNER/REPO/pulls/comments --paginate --slurp"

def die(msg):
    sys.exit(print(f"error: {msg}", file=sys.stderr) or 2)

# probes get one scenario's steps [(kind, line, text)]; None = n/a, else (follows?, step to quote)
def given_state(st):
    g = [s for s in st if s[0] == "Given"]
    return g and (not (bad := [s for s in g if ACTION.match(s[2])]), (bad or g)[0])
def one_when(st):
    w = [s for s in st if s[0] == "When"]
    return len(w) == 1, (w[1:] or w or st)[0]
def no_mechanics(st):
    bad = [s for s in st if MECHANICS.search(s[2].split(None, 1)[1])]
    return not bad, (bad or st[-1:])[0]
PROBES = {f.__name__: f for f in (given_state, one_when, no_mechanics)}

def scenarios(path):  # one step list per Scenario; And/But take the kind of the step above them
    out, kind = [], None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if m := re.match(r"\s*(Scenario|Example|Scenario Outline|Background|Rule|Feature):", line):
            out.append([] if m[1] in ("Scenario", "Example", "Scenario Outline") else None)
        elif out and out[-1] is not None and (m := re.match(r"\s*(Given|When|Then|And|But)\s+\S", line)):
            kind = kind if m[1] in ("And", "But") else m[1]
            out[-1].append((kind, n, line.strip()))
    return [s for s in out if s]

def bullets(path):  # list items outside code blocks (blocks are blanked, so line numbers stay right)
    text = Path(path).read_text(encoding="utf-8")
    text = re.sub(r"(?ms)^\s*```.*?^\s*```", lambda m: "\n" * m[0].count("\n"), text)
    return [(f"{Path(path).name}:{n}", l.strip()) for n, l in enumerate(text.splitlines(), 1)
            if re.match(r"\s*([-*+]|\d+\.)\s", l)]

def reviews(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as e:
        die(f"{path}: {e}; {EXPORT}")
    if not isinstance(data, list):
        die(f"{path}: expected a JSON list of review comments, got a {type(data).__name__}; {EXPORT}")
    return [c for page in data for c in (page if isinstance(page, list) else [page])]

def mine(a):
    try:
        sigs = yaml.safe_load(Path(a.signatures).read_text(encoding="utf-8"))
        pats = {k: (re.compile(s["for"], re.I), re.compile(s.get("against", "(?!)"), re.I))
                for k, s in sigs.items()}
    except (yaml.YAMLError, AttributeError, KeyError, TypeError, re.error) as e:
        die(f"{a.signatures}: {e!r}; each class needs a 'for' regex")
    if bad := [k for k, s in sigs.items() if s.get("probe") not in (None, *PROBES)]:
        die(f"{a.signatures}: unknown probe in {', '.join(bad)}; known: {', '.join(PROBES)}")
    if a.features and not Path(a.features).is_dir():
        die(f"--features {a.features}: not a directory")
    ids = set(re.findall(r"(?m)^## ([A-Z]{2,4}-\d+)\s", Path(a.rules).read_text("utf-8") if a.rules else ""))
    ev, loose = {k: defaultdict(list) for k in sigs}, []

    def classify(where, text, examples=(), pr=None):
        hits = [k for k, (pro, con) in pats.items() if pro.search(text) or con.search(text)]
        for k in hits:
            side = "CONFLICT" if pats[k][1].search(text) else "enforced" if pr else "stated"
            ev[k][side].append((where, text, pr))
            for kind, w, t in examples if side != "CONFLICT" else ():
                ev[k][kind].append((w, t))
        loose.extend([] if hits else [(where, text)])

    for path in a.doc:
        for where, text in bullets(path):
            classify(where, text)
    for c in reviews(a.reviews) if a.reviews else []:
        if c.get("in_reply_to_id"):
            continue  # a reply belongs to its thread; it is not a new complaint
        pr, hunk = c["pull_request_url"].rsplit("/", 1)[1], c.get("diff_hunk", "").splitlines()
        where = f"PR #{pr} {c['path']}:{c.get('line') or c.get('original_line')}"
        exs = [("bad", where, hunk[-1][1:].strip())] if len(hunk) > 1 else []  # the line commented on
        exs += [("good", f"PR #{pr} suggestion", s.strip())
                for s in re.findall(r"```suggestion\n(.*?)\n```", c["body"], re.S)]
        classify(where, c["body"], exs, pr)
    for path in sorted(Path(a.features).rglob("*.feature")) if a.features else []:
        for st in scenarios(path):
            for k, s in sigs.items():
                if s.get("probe") and (r := PROBES[s["probe"]](st)):
                    ev[k]["applies"].append(r[0])
                    ev[k]["good" if r[0] else "bad"].append((f"{path.name}:{r[1][1]}", r[1][2]))

    sheets, covered, conflicts = [], 0, 0
    for k, s in sigs.items():
        e = ev[k]
        if not (e["stated"] or e["enforced"] or e["CONFLICT"] or e["applies"]):
            continue
        prs = sorted({pr for *_, pr in e["enforced"]}, key=int)
        rate = sum(e["applies"]) / len(e["applies"]) if e["applies"] else None
        kinds = [x for x, ok in (("stated", e["stated"]), (f"enforced in {len(prs)} PRs", len(prs) > 1),
                                 ("practiced", (rate or 0) >= a.min_adherence)) if ok]
        covered, conflicts = covered + (s.get("id") in ids), conflicts + bool(e["CONFLICT"])
        rows = [f"{s.get('id', 'new')} {k}: {s['title']}  [{'covered' if s.get('id') in ids else 'new'}]"]
        rows += [f"  {side:<9} {w}  {t[:64]}" for side in ("stated", "enforced", "CONFLICT") for w, t, _ in e[side]]
        if rate is not None:
            rows.append(f"  practiced {sum(e['applies'])}/{len(e['applies'])} scenarios ({rate:.0%})"
                        + ("" if rate >= a.min_adherence else f", below --min-adherence {a.min_adherence}"))
        for kind in ("bad", "good"):
            uniq = {}
            for w, t in e[kind]:
                uniq.setdefault(t, w)  # the same text from two sources is one example
            shown = list(uniq.items())[:a.examples]
            rows += [f"  {kind:<9} {w}  {t[:60]}" for t, w in shown]
            if shown and len(uniq) > len(shown):
                rows[-1] += f"  (+{len(uniq) - len(shown)} more)"
        rows.append(f"  evidence  {', '.join(kinds) or 'none'} -> {('low', 'low', 'medium', 'high')[len(kinds)]}")
        sheets.append("\n".join(rows))
    print("\n\n".join(sheets), "\nunclassified (no class matched; a human reads these):", sep="\n")
    print(*([f"  {w}  {t[:72]}" for w, t in loose] or ["  (none)"]), sep="\n")
    print(f"{len(sheets)} candidate{'s' * (len(sheets) != 1)} ({covered} covered, {len(sheets) - covered} new), "
          f"{conflicts} conflict{'s' * (conflicts != 1)}, {len(loose)} unclassified")
    return 1 if conflicts else 0

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__.split("\n", 2)[2],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signatures", required=True, help="YAML: class -> {id?, title, for, against?, probe?}")
    ap.add_argument("--rules", help="rule file; a class whose id appears in it is 'covered'")
    ap.add_argument("--doc", action="append", default=[], help="markdown whose bullets state rules (repeatable)")
    ap.add_argument("--reviews", help="PR review comments: a JSON list, or the pages of --slurp")
    ap.add_argument("--features", help="directory searched for *.feature")
    ap.add_argument("--min-adherence", type=float, default=0.8, help="default 0.8: four scenarios in five")
    ap.add_argument("--examples", type=int, default=1, help="examples shown per kind (default 1)")
    try:
        return mine(ap.parse_args())
    except OSError as e:
        die(f"{e.filename}: {e.strerror}")

if __name__ == "__main__":
    sys.exit(main())
```

</details>

## 底層機制：一張候選清單是怎麼算出來的

每一筆輸入先被歸類成（類別, 立場, 出處, 原文, 例子）：bullet 命中 `for` 是 stated、命中 `against` 是 CONFLICT；review 留言命中 `for` 是 enforced，並記下 PR 號、把 `diff_hunk` 的末行當 bad、suggestion 當 good；每個 feature 情境交給 probe，回傳有沒有做到與要引用的那一步。都沒命中的進 unclassified。接著每一類彙整：PR 號去重後兩個以上才算 enforced；做到的比例達 `--min-adherence` 才算 practiced；例子依文字去重（同一句從 review 和 feature 來只算一次），只印前 `--examples` 個並註明還有幾個。三個設計選擇：

1. **信心是「幾種獨立證據同意」，不是機率。** 0–1 種 low、2 種 medium、3 種 high。CLAUDE.md 會過時，review 會誤判，程式碼帶著舊習慣，三種都同意才值得信。只有一種時信心低，不代表規則錯：GH-02 這次沒有任何本地證據，仍是好規則，只是要靠人判斷。
2. **0.8 是我們選的門檻，不是標準**，意思是五個情境四個做到。樣本小的時候，一個情境就能讓比例跨線，所以輸出一定附上分子分母。
3. **為什麼不整段交給 LLM？** 出處要精確到檔案與行號，計數要重跑得出同一個數字，「兩次才算一類」也要數得出來，這些交給腳本；LLM 留給語意工作，替 unclassified 那一桶命名新的類（見「進階」）。這和 [Ch 1](./01-course-map.md) 的分工一樣：「有沒有接上」給腳本，「接得對不對」給人或模型。

## 四、人審關卡：收、退、待議

提取器從不改 rule file，理由和 pstack /reflect 的「Do not auto-apply」相同：規則會影響之後的每一個 agent。我們的決定：

| 候選 | 決定 | 理由 |
|---|---|---|
| GH-01 given-state | 留著；證據劃掉 PR #55 | #55 是英文的 given，不是 Gherkin 的 Given |
| mechanics | 收為 GH-03，改寫 CLAUDE.md:5 | 兩個 PR 都在擋；CLAUDE.md 那條正是違規的來源 |
| one-when | 收為 GH-04 | CLAUDE.md 有寫、4/5 做到，缺的是例子；「兩次」門檻管證據，不管決定 |
| CLAUDE.md:3 `pytest -q` 與 PR #52 錯字 | 不收 | 一個是專案事實，留在 CLAUDE.md；一個是單次筆誤 |
| CLAUDE.md:6 標題以規則 ID 開頭 | 待議 | 新的一類，先補 signature 與 probe（動手練習 1） |

GH-03、GH-04 的全文就是第一節那份檔的後半。另外兩處修改：在 `signatures.yaml` 的 `mechanics:` 與 `one-when:` 底下各加一行 `id: GH-03`、`id: GH-04`；把 CLAUDE.md 第 5 行換成 `- Steps say what the user sees; HTTP status codes live in the API driver (GH-03).`。把 `--rules` 換成完整的 `rules/gherkin.md` 重跑，最後一行變成 `3 candidates (3 covered, 0 new), 0 conflicts, 3 unclassified`，exit 0；原本 CONFLICT 的那一行，成了 GH-03 的 `stated` 證據。但 GH-03 的那一行仍是 `practiced 2/5 scenarios (40%), below --min-adherence 0.8`：規則定了，舊情境還沒改。從這一刻起，提取器也是進度表。

## 五、A/B：例子到底改變了什麼

規則文字相同、任務相同，只差有沒有例子。rule file 的例子只示範了部分重疊與「接在後面」，任務改問三種沒示範過的 R1 邊界，免得 agent 照抄；A 組拿掉 `Good`／`Bad`／`Edge` 行與 code block，B 組用完整的檔，兩組都只給一輪、不給工具。`ab/smoke` 是 Ch 0 冒煙測試專案的複本，`scenarios(...)` 改指向整個 `../features`，放進去的 feature 檔都會被綁定：

```bash
cd /tmp/lab/aibdd/ch09-rules && mkdir -p ab && cp -R /tmp/lab/aibdd/ch00-setup/smoke ab/smoke
sed -i '' 's#scenarios("../features/booking.feature")#scenarios("../features")#' ab/smoke/tests/test_booking.py
printf 'You write Gherkin for a meeting-room booking service: rooms Taipei, Tokyo and Seoul; people Alice, Bob and Carol; same-day HH:MM times; a booking holds the half-open interval [start, end).\n\nFollow these rules:\n\n' > ab/intro.txt
cat > ab/task.txt <<'EOF'
Write one Feature with three scenarios for rule R1 (a room cannot be booked twice for overlapping times):
1. a new booking that ends exactly when an existing booking starts
2. a new booking that lies entirely inside an existing booking
3. a new booking that covers an existing booking completely
Reply with the feature file only: no code fences, no commentary.
EOF
no_examples() { grep -vE '^(Good|Bad|Edge)' rules/gherkin.md | awk '/^```/ {f = !f; next} !f'; }
for arm in a b; do { cat ab/intro.txt; echo '<rules>'; if [ $arm = a ]; then no_examples; else cat rules/gherkin.md; fi
  echo '</rules>'; echo; cat ab/task.txt; } > ab/prompt-$arm.txt; done
cd ab && claude -p "$(cat prompt-a.txt)" --max-turns 1 --tools "" --setting-sources project \
  --strict-mcp-config --no-session-persistence --output-format json > run-a.json  # B 同理；run-a2.json 是 A 再跑一次
```

> 以下是實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。A 的第一份與 B 對同一個情境（新預約完整蓋住既有預約）寫出的三個步驟，`#` 開頭的標註是我們加的；A 重跑的那一份又換了說法，例如 `Given Taipei is booked by Alice from 10:00 to 11:00`：

```gherkin
# A（沒有例子），第一次
    Given Alice has a booking for room Taipei from 10:00 to 11:00
    When Bob books room Taipei from 09:00 to 12:00
    Then Bob's booking is rejected with the reason "room already booked for that time"
# B（有例子）
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Alice books "Taipei" from 09:00 to 12:00
    Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
```

`ab/score.sh` 把每份回覆放進 smoke 的複本，用 pytest-bdd 的 `--generate-missing`（[Ch 27](./27-ai-bdd-loop.md) 會正式用它）數找不到 step definition 的步驟寫法，對真的 app 跑，再用本章的 probe 量 GH-01／03／04：

```sh
#!/bin/sh
# score.sh ARM...: undefined step texts, a real run against the app, and the probes, for each reply
. /tmp/lab/aibdd/.venv/bin/activate
for arm in "$@"; do
  rm -rf $arm out-$arm && cp -R smoke $arm && mkdir out-$arm
  python -c "import json; print(json.load(open('run-$arm.json'))['result'])" | tee out-$arm/r1.feature > $arm/features/r1.feature
  missing=$(cd $arm && PYTHONPATH=.:tests pytest --generate-missing --feature features tests | grep -c 'is not defined')
  run=$(cd $arm && PYTHONPATH=.:tests pytest -q tests --driver api | tail -1)
  probes=$(python ../rule_extract.py --signatures ../signatures.yaml --features out-$arm | grep -c 'practiced 3/3')
  echo "$arm: $missing undefined step texts | $run | $probes of 3 probes hold in 3/3 scenarios"
done
```

```
$ sh score.sh a a2 b
a: 7 undefined step texts | 3 failed, 2 passed in 0.08s | 3 of 3 probes hold in 3/3 scenarios
a2: 7 undefined step texts | 3 failed, 2 passed in 0.08s | 3 of 3 probes hold in 3/3 scenarios
b: 0 undefined step texts | 5 passed in 0.09s | 3 of 3 probes hold in 3/3 scenarios
```

（`2 passed` 是 smoke 原有的兩個情境。）**結構規則，句子就夠了**：三份回覆的 probe 全過，Given 都是狀態、只有一個 When、沒有狀態碼。**家規只能靠例子**：A 的兩份回覆各 9 個步驟，沒有一個綁得上既有的 step definition（每份去重後 7 種寫法），兩份之間的說法也不同；B 的 9 個全部綁得上，對真的 app 跑三個全過，另一個情境的拒絕原因 `taken 10:00-12:00 by Bob` 是照格式推出來的，不是照抄。**最危險的是自創的拒絕訊息**：若有人照 A 補上 step definition，等於把 app 從沒說過的話寫成規格，下一步就是去「修」app 配合它。代價是 claude-opus-5-5 的輸入從 3,662 變成 4,307 tokens（含快取），多 645。

限制要說清楚：每組只跑一到兩次、一個模型、一個任務，這是協定的示範，不是 eval。要下結論，得照 [pstack Ch 29](../pstack/29-eval-playbook.md) 的「一、盲測規則」別讓受測的 agent 看出自己在受測，每組也要多跑幾次、看分布（同章第五節的 `claude plugin eval` 預設每個 case 跑 3 次，理由是「一次結果是一個樣本」）；較弱的模型可能連結構規則都需要例子，本章沒有測。

## 對比與取捨

| 規則放哪裡、長什麼樣 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| CLAUDE.md 的一行 bullet | 永遠在 context、寫起來快 | 每輪付費；沒有例子，agent 用先驗補完 | 處處相關的短事實 |
| rule file＋好／壞／邊界例子 | 按需載入、可用 ID 引用；措辭與界線一起傳過去（只有規則句時，家規傳不過去，見第五節 A 組） | 多佔 context（本章多 645 tokens）；例子會被模仿 | 有固定措辭、有家規的領域 |
| `examples/` 目錄 | 放得下整份檔案 | 規則和例子分兩處，容易漂移 | 範例本身是一整個檔案 |
| lint／hook | 確定性、不佔 context | 只擋得住能機械判斷的規則 | `Sign you skipped it` 寫得成 regex 或 AST 檢查時 |
| 讓 LLM 直接從歷史生規則 | 讀得懂語意、能命名新的類 | 出處與計數不可重現，容易把一次的事當規則 | 只用在 unclassified 那一桶 |

## 踩雷集錦

1. **錯誤直覺：「規則句寫清楚就夠，例子是錦上添花。」** 正確認識：第五節 A 組結構全對，步驟卻 0/9 綁得上，拒絕訊息是自創的。句子傳得了原則，傳不了措辭。
2. **錯誤直覺：「多數人怎麼寫，規則就是那樣。」** 正確認識：mechanics 只有 2/5 情境做到，它仍是規則，違規正是過時的 CLAUDE.md 造成的。做的是證據，不是權威；說的和做的打架時交給人。
3. **錯誤直覺：「提取器挑出的 good 例子可以直接貼進 rule file。」** 正確認識：例子只對一條規則是好的。加 `--examples 9` 重跑，one-when 的 good 裡有 `legacy.feature:11  When Alice clicks "Book"`：一個 When 沒錯，但它違反 GH-03。貼進 rule file 前，拿其他規則再看一遍。
4. **錯誤直覺：「regex 看到關鍵字就分對了。」** 正確認識：它讀不懂語意。PR #55 的英文 given 被算成證據；否定句更糟：把一行 `- Then steps never check the HTTP status code.` 當 `--doc` 餵進去，得到 `CONFLICT  neg.md:1  - Then steps never check the HTTP status code.` 與 exit 1。這句其實贊成 GH-03，卻被標成衝突。所以出處一定印原文，exit 1 的意思是「請人看」，不是判決。

## 進階：held-out、交給 LLM 的那一桶、還沒挖的來源

- **用沒看過的資料驗收規則。** 照時間把留言切兩半，前半提取、人審定案，再拿後半加 `--rules rules/gherkin.md` 跑：後半的抱怨落在 covered 的類，代表規則早點強制就擋得住；落在 new 或 unclassified，代表規則集還有洞。本章沒跑，樣本只有 7 則新留言，切出來的比例沒有意義。
- **unclassified 交給 LLM 命名。** 把那一桶和現有的 `signatures.yaml` 一起交給模型，請它提出新的類與 `for` regex，人審後加進分類表再重跑；模型負責命名，腳本負責計數與出處（本章沒有實際跑這一步）。
- **還沒挖的來源。** `git log --grep=revert` 找被退回的改動；`~/.claude/projects/<slug>/*.jsonl` 裡你對 agent 的糾正（pstack Ch 30「四、/automate-me」讀的就是它）；寫成 Correct／Wrong 格式的風格文件，可以直接收割成例子。反過來，rule file 的例子也是測資：[Ch 28](./28-gherkin-dsl-style.md) 的 `gherkin_lint.py` 應該讓每個 Good 通過、每個 Bad 失敗，做到了，`Enforced by` 才能從 review 改成 lint。

## 動手練習

1. 替 CLAUDE.md:6 加一類 `title-rule-id`：一條 signature，加一個 probe（Scenario 標題以 R 加數字開頭）。probe 現在只拿到步驟，得先讓 `scenarios()` 把標題也交出來。五個情境裡哪幾個做到？這個比例夠不夠讓你把它收進 rule file？
2. 對一個你有權限的 repo 匯出真的留言，先用只有一類的分類表跑，大部分會落進 unclassified。手動分成三類、寫 signature、重跑，看哪一類真的出現兩次以上。
3. 寫一支檢查 rule file 形狀的小腳本：每個 `## <ID>` 都要有四個欄位與三種例子，缺了就 exit 1，先拿一行 CLAUDE.md bullet 改成的規則讓它失敗。

## 本章重點整理

- 一條規則＝穩定 ID＋Trigger＋一句 Rule＋（反直覺才寫的）Why＋違規徵兆＋由誰強制＋好／壞／邊界三個例子。
- 例子傳的是形狀與措辭；家規（固定的步驟寫法、使用者看到的訊息）只能靠例子傳。A/B 中結構規則兩組都對，只有帶例子的那組 9/9 綁得上既有步驟。
- 提取器把說的、罵的、做的按分類表歸成同一類，印出處、例子、做到的比例與衝突；信心是幾種獨立證據同意，不是機率。它不改規則，由人收、退、待議；regex 會誤判（英文的 given、否定句），所以出處一定印原文。

## 自我檢核

- [ ] 不看本章，說得出一條規則的七個欄位，以及哪一個是選填、為什麼選填？
- [ ] 本章 A/B 的 A 組結構全對，為什麼仍算失敗？換成較弱的模型，你預期 `score.sh` 輸出的哪一欄先變？
- [ ] mechanics 只有 2/5 情境做到，我們為什麼仍把它收為規則？什麼樣的證據組合會讓你選擇不收？
- [ ] 你的 repo 裡，哪一條常在 review 被提起、卻不在任何文件裡的規矩？它的 Edge 例子會是什麼？

## 延伸閱讀

- **[Anthropic — Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)**：**讀哪裡**：「Use examples effectively」一節（舊的 multishot prompting 網址會轉到這裡）。**學到什麼**：例子要 relevant、diverse、structured，建議 3–5 個，而且要有足夠變化，免得模型學到不想要的模式。**和本章的關聯**：「每條三個例子」的出處；它談單一任務的 prompt，rule file 是多條規則共用一份 context，所以我們改成每條三個。
- **[Sewon Min et al. — Rethinking the Role of Demonstrations: What Makes In-Context Learning Work?](https://arxiv.org/abs/2202.12837)**（EMNLP 2022）：**讀哪裡**：摘要與 random labels 的實驗；模型與任務都比今天的 agent 舊，讀機制，不要搬數字。**學到什麼**：範例的標籤隨機換掉表現幾乎不掉，起作用的是標籤空間、輸入分布與格式。**和本章的關聯**：解釋第五節為什麼例子改變的是措辭與格式。
- **[Patrick Winston — MIT 6.034 Lecture 15: Learning: Near Misses, Felicity Conditions](https://ocw.mit.edu/courses/6-034-artificial-intelligence-fall-2010/resources/lecture-15-learning-near-misses-felicity-conditions/)**（2010 年課堂錄影）：**讀哪裡**：拱門那段示範，頁面附講稿 PDF。**學到什麼**：near miss 學習的提出者本人，講他 1970 年博士論文（MIT AI-TR-231）裡模型怎麼靠例子與 near miss 長出來。**和本章的關聯**：Edge 例子為什麼要「只差一點」。
- **[Alberto Bacchelli, Christian Bird — Expectations, Outcomes, and Challenges of Modern Code Review](https://www.microsoft.com/en-us/research/publication/expectations-outcomes-and-challenges-of-modern-code-review/)**（ICSE 2013）：**讀哪裡**：留言分類的方法與結果。**學到什麼**：觀察、訪談、問卷，加上人工分類數百則 Microsoft 的 review 留言，發現 review 少在抓缺陷、多在傳遞知識。**和本章的關聯**：「罵的」為什麼是規則的來源；他們的人工分類，就是 signatures.yaml 加人審想做快的那件事。
- **[Miltiadis Allamanis et al. — Learning Natural Coding Conventions](https://arxiv.org/abs/1402.4182)**（FSE 2014）：**讀哪裡**：導論與從語料學慣例的部分，統計模型細節可跳過。**學到什麼**：NATURALIZE 從一個 codebase 學出命名與格式慣例並建議修改，識別字建議的前幾名準確率 94%。**和本章的關聯**：本章的「做的」只有三個手寫 probe；這篇示範慣例可以直接從程式碼學出來，是提取器往前走的方向。

規則有了例子，還剩兩種重複：每次都長得一樣的產物形狀，和每次都照同樣順序做的步驟。

→ [Ch 10 Template 與 Script：把重複的形狀和步驟抽出來](./10-templates-and-scripts.md)
