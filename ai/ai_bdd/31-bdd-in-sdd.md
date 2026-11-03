# Ch 31 — 把 BDD 放進 SDD：以 feature file 為粒度的 tasks

> **目標**：把 Spec Kit spec.md 裡的 Gherkin 驗收情境抽成 `features/*.feature`，讓它成為 SDD 流程裡唯一的可執行合約；把 tasks.md 從「依層切」改成「依情境切」：每個 task 擁有同一個 feature 檔裡的一到兩個情境，下一行是用 tag 選中它們的 Verify 指令，開工時紅、收工時綠。為此寫兩支腳本（`extract_features.py` 讓 spec 與 feature 檔一致，`scenario_task_map.py` 檢查每個情境恰好一個主人、每行 Verify 恰好選中它擁有的情境），把格式放進 preset 0.2.0 的 tasks-template 與 constitution 的一節，包成 `bdd-plan` skill，再用三次 `claude -p` 比較 stock `/speckit-tasks` 與它。

> **環境**：Spec Kit 1.2.5、Claude Code 2.1.295（claude-opus-5-5）、Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（parser 為 gherkin-official 42.0.1）、FastAPI 0.162.0，macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch31-bdd-in-sdd/` 從空目錄開始，複製 Ch 17 的專案與 Ch 0、27、28 的幾個檔案；輸出都是 2026-11-02 實跑。

## 為什麼需要這個？

這個 repo 的 SDD 與 BDD 一直並排，只在概念上相遇。[SDD Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md) 的「兩種意義可以共存嗎？」答可以：先寫 Gherkin 當驗收標準，再讓 agent 生成通過它的實作；但沒說 Gherkin 放在哪個檔、誰能改、tasks 怎麼對到它。[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md) 的「環節三：TASKS 拆分」給每個 task 一個 `acceptance_criterion_ids` 與 `verification_command`，「環節五：VERIFY 驗收」把驗收分成跑測試的機械層與 LLM 判讀的語意層，都只有骨架。[SDD 練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) 是能跑的版本，它的參考解答在檢討裡寫「verify_acceptance 靠命名慣例太脆弱」：agent 沒照規定把測試函式取名 `test_ac1`，驗收條件和測試就對不上了。

tasks 的形狀則往反方向走。[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md)「第六步」的範例依層排成 Database、API Layer、Notification Worker、Integration，最後一個 phase 才是 Tests；[SDD Ch 30](../spec_driven_development/30-kiro.md) 的 Kiro 練習要你確認測試 task 排在實作之後。Spec Kit 自己的模板好一點：phase 依 user story 切，story 裡面卻仍依層排，測試還是選配。`/speckit-tasks` 的原文是「Tests are OPTIONAL: Only generate test tasks if explicitly requested in the feature specification or if user requests TDD approach」與「Within each story: Tests (if requested) → Models → Services → Endpoints → Integration」（0.11.10 與 1.2.5 用 `specify init` 裝出來的 tasks-template 逐位元組相同，sha256 都是 `fc29a233…`，我們比對過）。這樣排出來的 task 做完沒有，只能聽 agent 自己說。Claude Code 的 plan mode（本課 [Ch 3](./03-propose-constrain-feedback.md) 的「二、提案」、[harness Ch 28](../harness_engineering/28-planning-todo.md) 的「三、Plan mode」、[AI 應用 Ch 5](../ai_applications/05-claude-code-basics.md)）讓 agent 先唯讀規劃、人核可後才動手，但核可的那份計畫預設存在 `~/.claude/plans/`（`plansDirectory` 可改），不進 repo，也沒有腳本檢查過它；本章要的「進階版 plan mode」是一份放在 spec 目錄裡、核可前先過腳本的計畫。[Ch 27](./27-ai-bdd-loop.md) 把「做完」交給 `bdd_check.py`，但它一次判斷整個套件、用三個全域階段，並把「以 feature 為單位」留給本章。本章的答案：每個 task 擁有幾個情境，跑那幾個情境就是它的完成條件；「每個情境恰好屬於一個 task、每個 task 的指令恰好跑它的情境」則是腳本檢查得了的事。

## 先建立直覺

Bill Wake 2003 年談 INVEST 的「Valuable」時把一個 story 比成多層蛋糕：網路層、持久層、邏輯層、畫面層；拆 story 時端上桌的只是蛋糕的一部分，而一整層資料層若沒有畫面層，對客人沒什麼價值。依層切的 tasks 是先烤完所有海綿、再抹所有奶油，最後一層蓋上去之前沒有一口能試吃。依情境切是直刀切：每一塊都小，但每一層都有一點，切下來當場試吃（Verify）。每塊盤子貼一個編號（`@sc_book_02`），出菜單上每個編號只歸一個人（一個 task）。比喻在兩處不成立：蛋糕切了就吃掉，情境每次改動都要重跑；有些塊在動刀之前就已經好吃（相鄰時段那個情境，R1 還沒做就會過），它得跟它守護的那一塊一起上桌。Gherkin、tag 與 step 綁定見 [Ch 26](./26-bdd-vs-tdd-artifacts.md)、[Ch 27](./27-ai-bdd-loop.md)；Spec Kit 1.2.5 的目錄、preset 與 constitution 參數層見 [Ch 12](./12-spec-kit-1x.md)、[Ch 17](./17-constitution-and-presets.md)。

```
 spec.md（計畫）User Story 2 ──extract_features.py──▶ features/cancellation.feature（合約；簽核後只有人改）
   **Feature file** 一行 + gherkin 區塊   --check 抓漂移      @sc_cancel_01  @sc_cancel_02   （tag 即 pytest marker）
                                                                    │
 tasks.md：- [ ] T006 [US2] [FR-004] [@sc_cancel_01] …   ◀── scenario_task_map.py：每個情境恰好一個主人，
             - Verify: `… pytest -q -m "sc_cancel_01"`              Verify 選中的 = 它擁有的
                    │ 開工前跑：紅在 Then；收工時跑：綠
                    ▼
 slice_stop.py（進階）：.bdd/task 指向哪個 task，Stop 就跑那一行 Verify
```

## 一、驗收條件變成 feature 檔

起點是 [Ch 17](./17-constitution-and-presets.md) 的終點：constitution 1.1.0、preset `booking-bdd` 0.1.0（spec 的驗收是帶 tag 的 Gherkin 區塊）、`sdd-specify` skill、`tools/constitution_params.py`。Ch 17 只要求 commit constitution，所以第二行先把其餘成果也 commit 進去（`specs/` 除外；已經全部 commit 過的話，git 會說沒有東西可 commit、exit 1，無妨）。漏了這步，clone 帶不走 preset，第四節的 `preset update` 會回 `Error: Preset 'booking-bdd' is not installed`。最後一行寫 `.specify/feature.json`：它在 Spec Kit 的 `.specify/.gitignore` 裡，clone 不到，少了它 `setup-tasks.sh` 只回 `ERROR: Feature directory not found. Set SPECIFY_FEATURE_DIRECTORY or run the specify command to create .specify/feature.json.`。

```bash
mkdir -p /tmp/lab/aibdd/ch31-bdd-in-sdd && cd /tmp/lab/aibdd/ch31-bdd-in-sdd
git -C ../ch17-presets/booking add -A -- . ':!specs' && git -C ../ch17-presets/booking commit -qm "Ch 17 end state"
git clone -q ../ch17-presets/booking booking && cd booking     # 只帶走 Ch 17 已 commit 的狀態
ln -s /tmp/lab/aibdd/.venv .venv && source .venv/bin/activate
mkdir -p specs/001-room-booking .claude/skills/bdd-plan/scripts
echo '{"feature_directory": "specs/001-room-booking"}' > .specify/feature.json
```

`specs/001-room-booking/spec.md` 由我們手寫，形狀照 preset，每個 user story 多一行 `**Feature file**:`（第四節把它加進 spec-template）。開頭是標題、一行 `**Feature Branch**`／`**Created**`／`**Status**`、一行 `**Input**`（一句需求描述），`## User Scenarios & Testing *(mandatory)*` 底下兩個 story。User Story 2 原文：

~~~markdown
### User Story 2 - Cancel a booking (Priority: P2)

The person who booked a room can give it back; nobody else can.
**Independent Test**: book, then cancel as the same person and as someone else.
**Feature file**: `features/cancellation.feature`

```gherkin
Rule: R4 Only the person who booked can cancel

  @sc_cancel_01
  Scenario: The person who booked cancels
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Bob cancels the booking of "Taipei" from 10:00 to 11:00
    Then room "Taipei" is free all day

  @sc_cancel_02
  Scenario: Someone else tries to cancel
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Alice cancels the booking of "Taipei" from 10:00 to 11:00
    Then the cancellation is rejected because only Bob can cancel this booking
```
~~~

`@sc_cancel_01` 的成功結果寫成 `Then room "Taipei" is free all day`，不是 [Ch 29](./29-example-mapping.md) 會議定下的 `Then the booking is cancelled`：第五節三次實跑讀的就是這份 spec（S、A 的 tasks 都在 `tests/test_cancellation.py` 替這句另排一支 step：它在 Ch 28 是 Given，當 Then 用綁不上），所以本章保留，你自己的專案照 Ch 29 寫。User Story 1 是 `### User Story 1 - Book a room (Priority: P1)`、``**Feature file**: `features/booking.feature` ``，gherkin 區塊是 [Ch 27](./27-ai-bdd-loop.md) 的 `features/booking.feature` 去掉前兩行（`Feature:` 與空行）、每行少縮兩格，所以 tag 照 Ch 27 的編號：`@sc_book_02` 是重疊、`@sc_book_03` 是相鄰（Ch 28 與 Final Project 正好相反）；空一行再接 `Rule: R2 The end must be after the start`：一個情境 `@sc_book_04`「Booking a slot that ends before it starts」，三步 `Given room "Taipei" is free all day`、`When Alice books "Taipei" from 11:00 to 10:00`、`Then the booking is rejected because end must be after start`（[Ch 28](./28-gherkin-dsl-style.md) 的 R2 句型，不是練習 A 的 `the end must be after the start`：第二節照搬 Ch 28 的 `rejected` step，because 後面要逐字等於 API 訊息）。`## Requirements` 列 FR-001 到 FR-005，依序對應「能預約」（US1）、R1、R2、「本人能取消」（US2）、R4，每條一句；`## Success Criteria` 一條 SC-001：兩個 feature 檔的每個情境，對執行中的系統（原文 against the running system）都通過。這份 spec 過得了 Ch 17 的閘門（`spec_check.py` 回 `1 file, 0 problems`）。`setup-tasks.sh` 要求同目錄有 plan.md，第五節的 agent 也從它知道檔案放哪。我們的 `plan.md` 刻意精簡（17 行，[Ch 13](./13-plan-anatomy-review.md) 的 plan_lint 會嫌它缺 research.md 等產物）：`## Summary` 一句（FastAPI app 在 `app.py`、資料放記憶體、驗收就是 `features/` 的情境）；`## Technical Context` 兩行（Python 3.12、FastAPI 0.162、pytest 9.2、pytest-bdd 9.0.1、`POST /reset` 清空資料、測試指令 `.venv/bin/python -m pytest -q`）；`## Project Structure` 五條：`app.py` 的 `POST /bookings (201, 409, 422)`、`GET /bookings`、`DELETE /bookings/{id}?by= (204, 403, 404)`、`POST /reset`，兩個 feature 檔「extracted from spec.md; only humans change them」，`tests/dsl.py` 是唯一碰 HTTP 的檔、`tests/conftest.py` 提供 `booking` fixture，一個 feature 檔一個 step 模組，`pytest.ini` 每個 `@sc_` tag 一個 marker。它沒寫 request body 的欄位，第五節會看到這個洞。

區塊由腳本搬成 feature 檔，不讓 LLM 轉寫：「照著重寫一遍」正是 [Ch 8](./08-translation-sandwich.md) 說的翻譯損耗會發生的地方。

```
$ python .claude/skills/bdd-plan/scripts/extract_features.py specs/001-room-booking/spec.md
features/booking.feature: written, 4 scenarios from User Story 1
features/cancellation.feature: written, 2 scenarios from User Story 2
2 files, 0 problems
```

搬完再過 Ch 28 的 lint（`python ../../ch28-gherkin-style/.claude/skills/gherkin-style/scripts/gherkin_lint.py --glossary ../../ch28-gherkin-style/glossary.yaml features/`），回 `2 files, 0 problems`。產生的檔案是 `Feature: <story 標題>`、空一行、區塊整段縮兩格；寫入前先用 gherkin-official 解析，壞掉的區塊（把 `Scenario:` 寫成 `Scenario`）回 `User Story 2: (12:5): expected: #TagLine, #RuleLine, #Comment, #Empty, got 'Scenario Someone else tries to cancel'`、exit 1，那個 story 不寫檔。誰是合約？簽核之前 spec.md 是來源，PM 與 `/speckit-clarify` 都在 spec 裡改；簽核之後 `features/` 是合約，只能經由「改 spec → 重新抽取 → 人審 → 重新簽核」改它（簽核沿用 Ch 27 的 `bdd_check.py approve`，進階一節接上；[Ch 33](./33-truth-vs-plan.md) 把這條線畫成 truth 與 plan）。兩份並存就會漂移：在副本裡（`cp -R . ../drift`）替 US2 加一個 `@sc_cancel_03`，模擬 clarify 寫回一個新例子，`--check` 立刻回 `features/cancellation.feature: differs from User Story 2 in specs/001-room-booking/spec.md; re-extract, review, re-approve` 與 `2 files, 1 problems`，exit 1。抽取不管風格：Ch 17 實測 2 那份 agent 寫的 spec 照原樣抽，三個 story 都因為缺 `**Feature file**:` 那一行被擋下，`0 files, 3 problems`（它是 0.1.0 模板寫的）；補上三行後抽出三個檔、12 個情境，Ch 28 的 lint 卻報 `3 files, 36 problems`，其中 34 則是會議室名稱沒加引號（GH-09）。

<details>
<summary><code>.claude/skills/bdd-plan/scripts/extract_features.py</code> 完整原始碼（49 行）</summary>

```python
#!/usr/bin/env python3
"""extract_features.py: copy each user story's ```gherkin block in spec.md into the feature file named on
the story's `**Feature file**:` line. With --check, compare and write nothing.
Exit 0 clean, 1 problems (story without a file line or block, parse error, drift), 2 bad usage."""
import argparse, re, sys
from pathlib import Path
from gherkin import Parser
from gherkin.errors import CompositeParserException

STORY = re.compile(r"^### User Story (\d+) - (.+?) \(Priority: P\d+\)$", re.M)
FILE, BLOCK = re.compile(r"^\*\*Feature file\*\*: `([\w/.-]+\.feature)`", re.M), re.compile(r"^```gherkin\n(.*?)^```$", re.M | re.S)
count = lambda kids: sum(count(c["rule"]["children"]) if "rule" in c else "scenario" in c for c in kids)

def stories(text):
    heads = list(STORY.finditer(text))
    for h, nxt in zip(heads, heads[1:] + [None]):
        body = text[h.end():nxt.start() if nxt else len(text)].split("\n## ")[0]
        yield f"User Story {h[1]}", h[2], FILE.search(body), BLOCK.findall(body)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--check", action="store_true", help="report drift between spec.md and features/")
    a = ap.parse_args()
    if not a.spec.is_file(): ap.error(f"{a.spec} not found")
    problems, done = [], {}
    for story, title, file, blocks in stories(a.spec.read_text(encoding="utf-8")):
        if not (file and blocks) or file[1] in done:
            problems.append(f"{a.spec}: {story} " + (f"also names {file[1]}, written from {done.get(file[1])}" if file and blocks
                            else "needs a **Feature file**: `features/<name>.feature` line and a ```gherkin block"))
            continue
        body = "\n".join("  " + ln if ln.strip() else "" for b in blocks for ln in b.splitlines())
        text, out = f"Feature: {title}\n\n{body}\n", Path(file[1])
        try: n = count(Parser().parse(text)["feature"]["children"])
        except CompositeParserException as e:
            problems.append(f"{a.spec}: {story}: {e.errors[0]}")
            continue
        done[file[1]] = story
        same = out.is_file() and out.read_text(encoding="utf-8") == text
        if a.check and not same: problems.append(f"{out}: differs from {story} in {a.spec}; re-extract, review, re-approve")
        elif not a.check:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(f"{out}: {'unchanged' if same else 'written'}, {n} scenarios from {story}")
    print("\n".join(problems + [f"{len(done)} files, {len(problems)} problems"]))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

</details>

## 二、tasks 依情境切：一個 slice、一行 Verify

先手寫一份黃金 tasks.md（[Ch 14](./14-artifact-first.md) 的做法：先定產物，再反推模板與 skill），放在 `specs/001-room-booking/tasks.md`：

```markdown
# Tasks: Room Booking
**Input**: specs/001-room-booking/ (spec.md, plan.md) and the approved features/*.feature
**Tests**: the scenarios are the tests. A task that changes behaviour owns scenarios and ends when its Verify passes.

## Phase 1: Setup
- [ ] T001 Register sc_book_01..04 and sc_cancel_01..02 under markers in pytest.ini
- [ ] T002 [P] Bind each feature once with scenarios() in tests/test_booking.py and tests/test_cancellation.py

## Phase 2: User Story 1 - Book a room (Priority: P1) MVP
**Feature file**: `features/booking.feature` | **Story check**: `.venv/bin/python -m pytest -q tests/test_booking.py`
- [ ] T003 [US1] [FR-001] [@sc_book_01] Book a free room: POST/GET /bookings and POST /reset in app.py, Booking DSL in tests/dsl.py, steps in tests/test_booking.py
  - Verify: `.venv/bin/python -m pytest -q -m "sc_book_01"`
- [ ] T004 [US1] [FR-002] [@sc_book_02] [@sc_book_03] Reject an overlapping booking and name the holder in app.py; @sc_book_03 guards the half-open boundary
  - Verify: `.venv/bin/python -m pytest -q -m "sc_book_02 or sc_book_03"`
- [ ] T005 [US1] [FR-003] [@sc_book_04] Reject a booking whose end is not after its start in app.py
  - Verify: `.venv/bin/python -m pytest -q -m "sc_book_04"`

## Phase 3: User Story 2 - Cancel a booking (Priority: P2)
**Feature file**: `features/cancellation.feature` | **Story check**: `.venv/bin/python -m pytest -q tests/test_cancellation.py`
- [ ] T006 [US2] [FR-004] [@sc_cancel_01] Cancel your own booking: DELETE /bookings/{id}?by= in app.py, cancel() in tests/dsl.py, steps in tests/test_cancellation.py
  - Verify: `.venv/bin/python -m pytest -q -m "sc_cancel_01"`
- [ ] T007 [US2] [FR-005] [@sc_cancel_02] Reject a cancellation by anyone else (403, names who can cancel) in app.py
  - Verify: `.venv/bin/python -m pytest -q -m "sc_cancel_02"`

## Phase 4: Polish
- [ ] T008 Run every scenario together: `.venv/bin/python -m pytest -q`
```

五個決定，各有一個沒選的替代方案。**task 行沿用 Spec Kit 格式**（`- [ ] T### [P?] [Story] …`），情境用 `[@sc_x_NN]` 引用，和 [Ch 16](./16-tasks-binder.md) 的 label run（`[FR-003] [EP:POST /bookings] [@sc_book_02]` 這種連續方括號）同一套慣例，`/speckit-implement` 照樣打勾；另開 YAML 檔，stock skill 就讀不到。**Verify 放在下一行的固定標籤**，不塞進描述：腳本找得到，人一眼看得到。**用 tag 選，不用測試名稱**：pytest-bdd 的函式名來自情境名，改名就斷，正是練習 F 的病；`-m` 用的是情境 ID。**上限是一個 feature 檔**：一個 feature 檔就是一個 story，task 跨兩個檔就同時屬於兩個 story，Spec Kit 以 story 為單位的 MVP 與 Checkpoint 就拆不開了。**守門的情境跟著它守的那個走**：`@sc_book_03`（相鄰時段）在 R1 還沒寫時就會過，單獨成 task 永遠紅不起來；和 `@sc_book_02` 同一個 task，開工時才是「一紅一綠」。綁定（T002）放在 Setup：綁定後每個情境都被收集，未實作的會報找不到 step，但 `-m` 把它們排除在別人的 Verify 之外。

T001 到 T003 做完的狀態，用前幾章的檔案拼：Ch 0 的雛形 app 去掉 R1 與 R2，只剩 T003 要的 POST、GET 與 reset（Ch 0 的 HTML 頁也還在）；Ch 28 的 DSL 與 step definitions 正是 T003 到 T005 會寫出的五支 step；`pytest.ini` 照 Ch 28 那份，四行 markers 換成 T001 列的六個 tag（說明文字別照抄：Ch 28 的 `sc_book_02` 是相鄰，這裡是重疊）。

```bash
cp ../../ch00-setup/smoke/app.py . && sed -i '' -e '/if b.end <= b.start:/,/raise HTTPException(422/d' \
   -e '/for x in BOOKINGS:/,/raise HTTPException(409/d' app.py
mkdir -p tests && cp ../../ch28-gherkin-style/tests/{dsl.py,conftest.py,test_booking.py} tests/
printf 'from pytest_bdd import scenarios\n\nscenarios("../features/cancellation.feature")\n' > tests/test_cancellation.py
```

T003 的 Verify 回 `1 passed, 5 deselected in 0.01s`。輪到 T004，改程式之前先跑它的 Verify：

```
$ pytest -q -m "sc_book_02 or sc_book_03" --tb=short
F.                                                                       [100%]
…（略）
tests/test_booking.py:25: in rejected
    assert outcome == Outcome(False, reason.replace('"', ""))
E   AssertionError: assert Outcome(confi..., reason=None) == Outcome(confi...11:00 by Bob')
…（略：confirmed: True != False，reason: None != 'Taipei is taken 10:00-11:00 by Bob'）
1 failed, 1 passed, 4 deselected in 0.03s
```

紅在 Then（`rejected`），訊息說的正是缺的行為，守門的 `@sc_book_03` 是那個點。在 `create()` 開頭補回 Ch 0 的三行：

```python
    for x in BOOKINGS:
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
```

同一行 Verify 變成 `2 passed, 4 deselected in 0.01s`，T004 完成。這時 story check（`pytest -q tests/test_booking.py`）是 `1 failed, 3 passed`，失敗的是 `test_booking_a_slot_that_ends_before_it_starts`：slice 完成看 Verify，story 完成看 Checkpoint，兩件事。兩種常見的 Verify 寫錯，各跑一次。tag 打錯一字，`-m "sc_book_02 or sc_book_3"` 回 `1 passed, 5 deselected`、exit 0：看起來是綠的，`@sc_book_03` 卻根本沒跑。改用測試名稱，`pytest -q tests/test_booking.py::test_booking_an_overlapping_slot` 現在是 `1 passed`；在副本（`cp -R . ../rename`）把 spec 裡的情境改名成 `Booking a slot that overlaps another one`、重新抽取後，它是 exit 4 的 `ERROR: not found: /private/tmp/lab/aibdd/ch31-bdd-in-sdd/rename/tests/test_booking.py::test_booking_an_overlapping_slot`、`(no match in any of [<Module test_booking.py>])`、`no tests ran in 0.00s`，同一份副本裡 `-m "sc_book_02"` 照樣 `1 passed, 5 deselected`。tag 是 ID，名稱是給人讀的標題，兩者分開，改標題才不會牽動計畫。

## 三、scenario_task_map.py：每個情境恰好一個主人

打錯的 tag 會安靜地少跑一個情境，寫 tasks 的又常是 agent，所以「情境與 task 對得上」交給腳本。它讀 features/ 的 AST（和 pytest-bdd、Ch 28 的 lint 同一個 parser）與 tasks.md 的 task 行、Verify 行，不跑任何測試就回答六個問題：沒有主人（SM-1）、兩個主人（SM-2）、引用不存在的 tag（SM-3）、跨兩個 feature 檔或超過 `--max-per-task`（SM-4）、沒有 Verify 或用測試名稱選（SM-5）、Verify 選中的和擁有的不同或寫了不存在的 tag（SM-6）。

<details>
<summary><code>.claude/skills/bdd-plan/scripts/scenario_task_map.py</code> 完整原始碼（104 行）</summary>

```python
#!/usr/bin/env python3
"""scenario_task_map.py: every scenario in features/ has exactly one owning task in tasks.md, and each
owning task's Verify line selects exactly its scenarios, evaluated the way `pytest -m` does.
  SM-1 no task owns a scenario (or it has no @sc_ tag)  SM-2 more than one task owns a scenario
  SM-3 a task cites an unknown @sc_ tag                 SM-4 a task spans two feature files, or owns > --max-per-task
  SM-5 no `  - Verify: `...-m "<tags>"`` line, or it selects by test name (::, -k)
  SM-6 Verify runs other scenarios than the task owns, or names an unknown tag
Path arguments in Verify are not evaluated. Exit 0 clean, 1 problems, 2 bad usage."""
import argparse, ast, re, shlex, sys
from pathlib import Path
from gherkin import Parser

TASK, OWNS = re.compile(r"^- \[[ xX]\] (T\d{3})\b"), re.compile(r"\[@(sc_[a-z0-9]+_\d{2})\]")
VERIFY = re.compile(r"^\s+- Verify: `(.+)`\s*$")
names = lambda tags: {t["name"].lstrip("@") for t in tags}

def scenarios(root):
    """tag -> (file, line, markers), untagged scenarios, all tags; pytest-bdd 9 adds Rule and Feature tags."""
    found, untagged, seen = {}, [], set()
    def walk(f, children, inherited):
        for c in children:
            if "rule" in c:
                walk(f, c["rule"]["children"], inherited | names(c["rule"]["tags"]))
            elif "scenario" in c:
                sc, line = c["scenario"], c["scenario"]["location"]["line"]
                marks, ids = names(sc["tags"]) | inherited, [t for t in names(sc["tags"]) if t.startswith("sc_")]
                found.update({t: (f, line, marks) for t in ids})
                seen.update(marks)
                if not ids: untagged.append((f, line, sc["name"]))
    for f in sorted(root.rglob("*.feature")):
        feature = Parser().parse(f.read_text(encoding="utf-8"))["feature"]
        walk(f, feature["children"], names(feature["tags"]))
    return found, untagged, seen

def read_tasks(path):
    """task id -> (line, owned tags, Verify command or None); Verify is an indented line under the task."""
    plan, current = {}, None
    for i, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if m := TASK.match(text):
            current, plan[m[1]] = m[1], (i, OWNS.findall(text), None)
        elif current and (v := VERIFY.match(text)) and plan[current][2] is None:
            plan[current] = plan[current][:2] + (v[1],)
        elif not text.startswith("  "):
            current = None
    return plan

def marker_expr(cmd):
    """The -m expression in cmd (python's own `-m pytest` skipped); None if absent or cmd selects by test name."""
    words = shlex.split(cmd)
    if "-k" in words or any("::" in w for w in words): return None
    return next((words[i + 1] for i, w in enumerate(words[:-1]) if w == "-m" and words[i + 1] != "pytest"), None)

def selects(expr, markers):
    def ev(n):
        if isinstance(n, ast.BoolOp): return (all if isinstance(n.op, ast.And) else any)(ev(v) for v in n.values)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not): return not ev(n.operand)
        if isinstance(n, ast.Name): return n.id in markers
        raise ValueError(f"unsupported term {ast.unparse(n)!r}; use tags with and/or/not")
    return ev(ast.parse(expr, mode="eval").body)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tasks", type=Path, help="tasks.md")
    ap.add_argument("--features", type=Path, default=Path("features"))
    ap.add_argument("--max-per-task", type=int, default=3, help="bdd-plan's max_scenarios_per_task")
    a = ap.parse_args()
    scen, untagged, known = scenarios(a.features) if a.features.is_dir() else ({}, [], set())
    if not a.tasks.is_file() or not (scen or untagged):
        ap.error(f"need {a.tasks} and at least one scenario under {a.features}/")
    plan = read_tasks(a.tasks)
    owners = {t: [tid for tid, (_, owned, _) in plan.items() if t in owned] for t in scen}
    problems = [(f, n, f"SM-1 scenario {name!r} has no @sc_ ID tag, so no task can own it") for f, n, name in untagged]
    for t, who in owners.items():
        if len(who) != 1:
            msg = f"SM-2 @{t} is owned by {' and '.join(who)}; keep one owner" if who else f"SM-1 @{t} has no owning task"
            problems.append(scen[t][:2] + (msg,))
    for tid, (line, owned, verify) in plan.items():
        if not owned: continue
        def say(rule, msg): problems.append((a.tasks, line, f"{rule} {tid} {msg}"))
        for t in (t for t in owned if t not in scen): say("SM-3", f"cites @{t}, which no scenario under {a.features}/ has")
        if len(files := sorted({scen[t][0].name for t in owned if t in scen})) > 1:
            say("SM-4", f"owns scenarios of {' and '.join(files)}; one feature file per task")
        if len(owned) > a.max_per_task: say("SM-4", f"owns {len(owned)} scenarios; split it (at most {a.max_per_task})")
        sel, expr = set(), marker_expr(verify) if verify else None
        if expr is None:
            say("SM-5", 'has no Verify line that selects its scenarios with -m "<tags>"')
        else:
            try:
                sel = {t for t, (_, _, m) in scen.items() if selects(expr, m)}
                for n in sorted({n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)} - known):
                    say("SM-6", f"Verify names {n}, which is no tag under {a.features}/")
                for t in sorted(set(owned) - sel): say("SM-6", f"Verify never runs @{t}")
                for t in sorted(sel - set(owned)): say("SM-6", f"Verify also runs @{t} (owner: {', '.join(owners[t]) or 'none'})")
            except SyntaxError:
                say("SM-6", f"Verify -m {expr!r} is not tags joined by and/or/not")
            except ValueError as e:
                say("SM-6", f"Verify -m {expr!r}: {e}")
        print(f"{tid} owns {' '.join('@' + t for t in owned)}; Verify runs {len(sel & set(owned))} of {len(owned)}")
    for f, n, msg in sorted(problems, key=lambda p: (str(p[0]), p[1])): print(f"{f}:{n}: {msg}")
    print(f"{len(scen) + len(untagged)} scenarios, {sum(bool(v[1]) for v in plan.values())} owning tasks, {len(problems)} problems")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

</details>

黃金那份回每個 task 一行（`T004 owns @sc_book_02 @sc_book_03; Verify runs 2 of 2` 之類）與 `6 scenarios, 5 owning tasks, 0 problems`。一份差一點的版本，用 `sed` 從黃金那份改三處：T004 的 Verify 把 tag 打錯一字、T005 也認領 `@sc_book_03`、T007 的 Verify 改用測試名稱：

```
$ mkdir -p ../fixtures && sed -e 's/or sc_book_03"/or sc_book_3"/' -e 's/\[@sc_book_04\]/& [@sc_book_03]/' -e 's|-m "sc_cancel_02"|tests/test_cancellation.py::test_someone_else_tries_to_cancel|' specs/001-room-booking/tasks.md > ../fixtures/tasks-nearmiss.md
$ python .claude/skills/bdd-plan/scripts/scenario_task_map.py ../fixtures/tasks-nearmiss.md --max-per-task 2
…（略：五行 owns，T004 與 T005 是 1 of 2，T007 是 0 of 1）
../fixtures/tasks-nearmiss.md:13: SM-6 T004 Verify names sc_book_3, which is no tag under features/
../fixtures/tasks-nearmiss.md:13: SM-6 T004 Verify never runs @sc_book_03
../fixtures/tasks-nearmiss.md:15: SM-6 T005 Verify never runs @sc_book_03
../fixtures/tasks-nearmiss.md:22: SM-5 T007 has no Verify line that selects its scenarios with -m "<tags>"
features/booking.feature:18: SM-2 @sc_book_03 is owned by T004 and T005; keep one owner
6 scenarios, 5 owning tasks, 5 problems
```

第一節那個加了 `@sc_cancel_03` 的副本（`../drift`，先把黃金 tasks.md 與 `scenario_task_map.py` 複製進去），重新抽取後再跑，得到 `features/cancellation.feature:18: SM-1 @sc_cancel_03 has no owning task` 與 `7 scenarios, 5 owning tasks, 1 problems`，exit 1：spec 長了一個例子，計畫就得替它找一個主人（新的 task，或併進既有的 slice，見動手練習 1）。它和 [Ch 16](./16-tasks-binder.md) 的 `task_coverage.py` 分工：那支問「每個 FR、端點、情境至少有一個 task 嗎」，這支問「每個情境恰好一個主人嗎、主人的指令真的跑它嗎」。

## 底層機制：從一個 tag 到被選中的測試

```
 情境的 tag ∪ Rule 的 tag ∪ Feature 的 tag     pytest-bdd 9.0.1 scenario.py 第 323–326 行
   ▼ pytest_bdd_apply_tag（預設 getattr(pytest.mark, tag)）：marker 名 = tag 去掉 @
 pytest -m "sc_book_02 or sc_book_03"         _pytest/mark/expression.py：expr := and_expr ('or' and_expr)*，
   │                                            and_expr := not_expr ('and' not_expr)*，not_expr := 'not' … | '(' expr ')' | ident
   ▼ 對每個測試的 marker 名稱集合求值 ─▶ 選中或 deselected；一個都沒選中 ─▶ exit 5（No tests were collected）
```

`scenario_task_map.py` 不執行 pytest，把同一條路走一遍：從 AST 算出每個情境的 marker 集合（自己、Rule、Feature 三層的 tag，和上面那三行一致；Ch 26 的 `-m agenda` 與 Ch 30 讀的 `plugin.py` 已看過這件事，我們在探針專案再確認一次：Feature 層的 `@booking` 選中全部三個情境，Rule 層的 tag 選中它底下的兩個），再用 Python 的 `ast` 解析 `-m` 字串、在集合上求布林值。黃金計畫的五行 Verify、打錯字的那一行、加上一個 `not … and not …`，共七個運算式，靜態結果和 `pytest --collect-only -q -m …` 選中的測試逐一相同。為什麼靜態算，不直接 collect？collect 要綁定存在、要能 import app 與 DSL；靜態檢查在 T002 綁定之前、app 還不存在時就能跑，計畫階段才用得上。代價寫在 docstring 最後一行（`--help` 的說明段會印出）：Verify 裡的路徑參數不算（`-m sc_cancel_01 tests/test_booking.py` 什麼都不跑，所以模板規定只用 `-m`）；pytest 允許但 Python 名稱不允許的寫法（`sc-book-01`、`marker(arg=1)`）一律報 SM-6，不猜，這也和 Ch 28 的 GH-10（ID tag 只用小寫加底線）一致。exit code 是另一個陷阱：`-m` 一個都沒選中是 exit 5，至少會被看見；`or` 裡有一半打錯是 exit 0，完全安靜（Verify 沒開 `--strict-markers`；開了是 exit 4，見踩雷 3），SM-6 的「names an unknown tag」為後者而設。

## 四、preset 0.2.0 與 constitution 的 Acceptance Contract

格式交給 Spec Kit 的兩個擴充點。preset 放在專案外的 `../presets/booking-bdd/`（由 Ch 17 那份複製），改三處：`preset.yml` 版本改成 `0.2.0`，`provides.templates` 加一項 `name: "tasks-template"`、`file: "templates/tasks-template.md"`、`replaces: "tasks-template"`；spec-template 在三個 `**Acceptance Scenarios**` 上方各加一行 ``**Feature file**: `features/[feature].feature` ``；constitution-params.md（`append`）在 Ch 17 的 `## Skill Parameters` 前加下面那段 `## Acceptance Contract`、最後加 `### bdd-plan`。tasks-template 用 `replace`，因為要換的是 story 內部的排法，只能整份換（[Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `booking-tdd` 也整份換掉它，兩個 preset 不能疊，見 Final Project 的踩雷 1；那裡把兩份合成一個 preset）。它保留核心模板的骨架（Setup、每個 story 一個 phase、Polish、Dependencies），`**Tests**` 改成「Not optional. The scenarios in `features/` are the tests」，`**Organization**` 改成「One phase per user story, which is one feature file. Inside it, one task per scenario slice」，範例 task 是兩個佔位的 slice（`[@sc_[feature]_01]`，以及帶守門註記的 `[@sc_[feature]_02] [@sc_[feature]_03]`），全文 53 行，篇幅所限不全列：照這段描述與下面的 Format 自己寫，下方 `grep` 印出的是我們那份的 `**Tests**` 行（它折成兩行，只印第一行）。agent 照著寫的規則是 Format 這段：

```markdown
- **[Refs]**: `[FR-###]`, then `[@sc_<feature>_<NN>]` for every scenario the task owns
- **Verify**: the next line is `  - Verify: \`<command>\``; the command selects the owned tags with `-m "a or b"`
- Every `@sc_` tag in features/ has exactly one owning task, and a task owns scenarios of one feature file
- Run Verify before the change: an owned scenario fails in a Then step, for the missing behaviour.
  A scenario that already passes, or only guards another's boundary, rides with that other scenario.
```

```
$ specify preset update booking-bdd --dev ../presets/booking-bdd      # 先 export SPECKIT_PYTHON_EXECUTABLE=$PWD/.venv/bin/python
✓ Preset 'booking-bdd' removed successfully
Installing preset from /private/tmp/lab/aibdd/ch31-bdd-in-sdd/presets/booking-bdd...
✓ Preset 'Booking BDD' v0.2.0 installed (priority 10)
$ specify preset resolve tasks-template | tail -1; .specify/scripts/bash/setup-tasks.sh --json | jq -r '.TASKS_TEMPLATE_CONTENT' | grep '^\*\*Tests'
    (top layer from: booking-bdd v0.2.0)
**Tests**: Not optional. The scenarios in `features/` are the tests. A task that changes behaviour owns
```

（`specify` 是 Ch 17 定義的 shell 函式，即 `uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`，另需 `export COLUMNS=160`。）`setup-tasks.sh` 用 `resolve_template_content` 走 Ch 17 那張解析圖，結果放進 `TASKS_TEMPLATE_CONTENT`，`/speckit-tasks` 的 SKILL.md 第 4 步要它「Use TASKS_TEMPLATE_CONTENT … as the structure」。拿掉 `SPECKIT_PYTHON_EXECUTABLE`、PATH 上只剩系統的 Python 3.9.6 時，錯誤訊息會誤導人：第一行是 `Error: PyYAML is required to resolve preset template composition`，後兩行卻說找不到模板：`ERROR: Could not resolve required tasks-template from the template override stack for …` 與 `Template 'tasks-template' was not found in any supported location (overrides, presets, extensions, or shared core)`，exit 1。核心模板明明在，真正的原因只在第一行。

constitution 是另一個擴充點。九個 stock skill 都會讀它（Ch 17），寫在這裡，連沒叫我們 skill 的人也讀得到。專案的 `.specify/memory/constitution.md` 在 `## Skill Parameters` 前加這一節：

```markdown
## Acceptance Contract

Acceptance scenarios live in `features/*.feature`, extracted from the approved spec by
`extract_features.py`; after approval only a human changes them. Tasks are slices of scenarios,
never layers, and there is no test phase after implementation. Every scenario has exactly one
owning task, whose Verify command selects it by its `@sc_` tag, fails before the change and
passes after it. `scenario_task_map.py` checks the ownership.
```

並在 `## Skill Parameters` 最後照 Ch 17 的約定加 `### bdd-plan`，yaml 區塊只有一行 `max_scenarios_per_task: 2`；`.claude/skills/bdd-plan/params.yaml` 宣告預設值 `features_dir: features`、`max_scenarios_per_task: 3`、`verify_command: '.venv/bin/python -m pytest -q -m "{tags}"'`。上限 2 是本專案的選擇：一個主要情境加一個守門情境。加一節、加一個 skill 區段，照 Governance 是 MINOR，Ch 17 的檢查擋住沒升版的修改：

```
$ python3 tools/constitution_params.py check --against HEAD
.specify/memory/constitution.md: changed since HEAD: needs a minor bump, version is 1.1.0
1 file, 1 problems
$ sed -i '' 's/\*\*Version\*\*: 1.1.0/**Version**: 1.2.0/' .specify/memory/constitution.md
$ python3 tools/constitution_params.py check --against HEAD && python3 tools/constitution_params.py get bdd-plan | jq -c .
1 file, 0 problems
{"features_dir":"features","max_scenarios_per_task":2,"verify_command":".venv/bin/python -m pytest -q -m \"{tags}\"","_from_constitution":["max_scenarios_per_task"]}
```

## 五、bdd-plan skill 與三次實跑

模板是形狀，constitution 是規則，兩者都只是給 agent 讀的字；skill 把流程和閘門串起來。`.claude/skills/bdd-plan/SKILL.md` 全文如下（`skill-lint.mjs .claude/skills` 回 `12 skills, 0 problems`）；「每個指令單獨跑」是 Ch 27、Ch 28 實跑學到的：agent 把檢查器和別的指令串成一行，`--allowedTools` 的規則就接不住。第 4 步前兩條重述第四節模板的 `**Organization**`，在本章兩者一致：先紅後綠發生在每個 slice 裡，不是另一個 phase。Final Project 的模板把每個 story 拆成 RED、GREEN 兩個 `##` phase，step definitions 也移到 RED 段；把這個 skill 搬過去，第 4 步要跟著模板改，否則 skill 與模板互相矛盾（Final 的第三次實跑，agent 照了模板、沒照 skill）。

```markdown
---
name: bdd-plan
description: Plan a Spec Kit feature whose spec.md carries Gherkin acceptance scenarios. Writes tasks.md as scenario slices - each task owns one or more @sc_ scenarios of one feature file and carries a Verify command that selects them by tag - and gates it with scenario_task_map.py. Use after /speckit-plan instead of /speckit-tasks when spec.md has gherkin blocks, or when asked to plan work per scenario or per feature file.
argument-hint: "[feature directory; default from .specify/feature.json]"
---

# bdd-plan

Feature directory: $ARGUMENTS (if empty, `feature_directory` in `.specify/feature.json`). Call it FEATURE.
Run each command below as a command of its own: no pipes, no `;`, no `&&`.

1. **Parameters.** `.venv/bin/python tools/constitution_params.py get bdd-plan` prints features_dir,
   max_scenarios_per_task and verify_command. A non-zero exit means the constitution needs fixing: stop.
2. **Contract.** `.venv/bin/python .claude/skills/bdd-plan/scripts/extract_features.py FEATURE/spec.md --check`.
   If it reports a problem, stop and ask the human to extract and approve the features.
   Never write features/ yourself: it is the contract a human signed.
3. **Template.** `.specify/scripts/bash/setup-tasks.sh --json`. TASKS_TEMPLATE_CONTENT is the shape of tasks.md.
4. **Write FEATURE/tasks.md.** Read spec.md, plan.md and every feature file first.
   - One phase per user story, in priority order; the story's feature file is its contract.
   - A task is a vertical slice: the step definitions, then the app change, for the scenarios it owns.
     Never a layer (models, routes, tests), and never a test task after the implementation.
   - Every `@sc_` tag has exactly one owning task, cited as `[@sc_x_NN]`. A task owns at most
     max_scenarios_per_task scenarios, all from one feature file.
   - A scenario that would already pass, or that only guards another scenario's boundary,
     rides with that other scenario's task.
   - The line after an owning task is `  - Verify: \`<verify_command>\``, {tags} = its tags joined by ` or `.
5. **Gate.** `.venv/bin/python .claude/skills/bdd-plan/scripts/scenario_task_map.py FEATURE/tasks.md --features <features_dir> --max-per-task <max_scenarios_per_task>`.
   Fix tasks.md and rerun until the last line ends in `0 problems`. After three failing runs, stop and report what is left.

Done when the gate prints `0 problems`. Report the number of tasks per story and paste the gate's output.
```

> 以下為實際執行紀錄（節錄），每次執行結果不同；2026-11-02，Claude Code 2.1.295，claude-opus-5-5。

三個專案都從同一份 spec、plan、features 出發，各自 `git clone`，旗標相同：`--max-turns 20 --max-budget-usd 4`、`--tools "Read,Write,Edit,Glob,Grep,Bash,Skill"`、`--allowedTools "Read,Write,Edit,Glob,Grep,Skill,Bash(.specify/scripts/bash/setup-tasks.sh *),Bash(.venv/bin/python *)"`、`--permission-mode acceptEdits --permission-prompts none --setting-sources project,local --strict-mcp-config --no-session-persistence`，stdin 接 `/dev/null`（指令稿是實驗目錄的 `runs/run.sh`）。差別只在設定：S 是 Ch 17 的狀態（constitution 1.1.0、核心 tasks-template、沒有 bdd-plan），跑 `/speckit-tasks`；A 有本章的 constitution 1.2.0 與 bdd-plan skill，但移除 preset、用核心模板，也跑 `/speckit-tasks`；B 是完整的本章設定，跑 `/bdd-plan`。實跑時兩支腳本的排版比本章鬆，行為相同：本章的版本對三份產物與所有範例重跑，輸出逐字一致。

| 實測 | tasks.md | Verify 行 | `scenario_task_map.py` | turns／時間／費用 |
|---|---|---|---|---|
| S：stock skill＋Ch 17 設定 | 205 行、17 個 task | 0 | `6 scenarios, 0 owning tasks, 6 problems` | 18／161 s／$0.71 |
| A：stock skill＋本章 constitution 與 skill，核心模板 | 169 行、10 個 task | 5 | `0 problems` | 22／139 s／$0.68 |
| B：`/bdd-plan`＋preset 0.2.0 | 66 行、11 個 task | 5 | `0 problems`（第一次跑就過） | 18／58 s／$0.33 |

**S 不是測試排最後，而是整個 story 一起先紅。** constitution 的 Test-First 讓它寫了「Tests are included: the spec doesn't explicitly request them, but Constitution III requires them.」每個 story 先排 DSL、全部 step、「跑一次確認每個情境都失敗」三個 task，再排依規則切的實作 task。它還附了一張 Scenario Coverage 表，寫出每個情境在哪個 task 先紅、哪個 task 轉綠：資訊都在，只是寫成表格與散文，腳本一個主人也認不出來。它自己決定用固定的 port 8765 起 uvicorn（理由是 SC-001 寫了 running system），而這台機器的 8765 正被另一個專案佔著，[Ch 22](./22-backend-e2e-first.md) 的踩雷第 2 條實測過這會得到假綠燈；我們的 plan.md 沒寫 request body，它便把欄位取名 `person` 而不是課程的 `by`。後兩件是 plan 的洞，不是 tasks 的。

**A 用的是核心模板，排出來卻是 slice。** 它的回報寫著「the spec has Gherkin scenarios, so the constitution's Acceptance Contract applies. Each task is therefore a scenario slice with a Verify command, rather than the template's separate models, services and tests tasks.」constitution 點名了 `scenario_task_map.py`，它便去讀 bdd-plan 的 SKILL.md 與檢查器原始碼，最後自己跑了閘門。stock SKILL.md 明寫測試選配、story 內依層排，這次輸給了 constitution 的一段話；代價是多花的回合，以及它先讀了檢查器才下筆（Ch 17 實測 2 也是如此）。

**B 最短也最便宜**，五步照做，閘門第一次就過。但 T009 的描述多了「and 404 for an unknown id」，T008 多了「including end equal to start」，兩者都沒有情境；B 在回報裡把它們列為「Behaviour with no scenario」，卻仍寫進 task，A 則把 404 放進 Open Questions：「no scenario covers it, so no task implements it」。檢查器只問每個情境有沒有主人，不問每個行為有沒有情境，後者要人審。每組只跑一次，三組的設定也不只差一個變數，表上的差別只能當線索，不能當結論。

## 對比與取捨

| 做法 | task 怎麼切 | 驗收怎麼接到 task | 測試何時寫 | 誰檢查對應 |
|---|---|---|---|---|
| SDD Ch 28「第六步」範例 | 依層：Database、API、Worker、Integration | 沒有 | 最後一個 phase | 人讀 |
| Spec Kit 1.2.5 stock（實測 S） | 依 story；story 內依層 | 描述與覆蓋表寫了情境 ID，task 行沒有 `[@…]` | 有要求才寫；這次整個 story 一起先紅 | `/speckit-analyze`（LLM，唯讀） |
| Kiro（[SDD Ch 30](../spec_driven_development/30-kiro.md)） | 依賴圖分 wave | SDD Ch 30 只列 ID、描述、依賴、狀態 | 練習要求排在實作之後 | 本章未實測 |
| [SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)、練習 F | task DAG | `acceptance_criterion_ids`；練習 F 靠 `test_acN` 命名 | 每個 task | `validate_task_dag` 加 LLM 語意層 |
| [pstack Ch 27](../pstack/27-multi-phase-autopilot.md) | 每個 PR 一段 | 「You see」加 Verify unit／live／perf | 每個 PR | `check-plan.mjs` |
| 本章 | 情境 slice，上限一個 feature 檔 | `[@sc_x_NN]` 加一行 `-m` Verify | 每個 slice 先紅 | `scenario_task_map.py`、`slice_stop.py` |

依情境切不是免費的。它要先有情境：還在探索、沒有例子的工作（技術 spike、效能調校）切不出 slice，[Ch 15](./15-research-and-analysis-skills.md) 的 research 或 pstack 的「Verify, perf」比較合適。共用的基礎建設（綁定、DSL、資料庫 migration）仍是橫的，放在 Setup，不歸任何情境。同一個 story 的 slice 改同一批檔案，幾乎沒有 `[P]`（標記的語意見 SDD [Ch 29](../spec_driven_development/29-spec-kit-internals.md) 的「`tasks-template.md`：`[P]` 標記與並行安全」），並行度比依層切低。換來的是每個 task 都有一個不靠 agent 自述的完成條件，而且整份計畫能被一支 104 行的腳本檢查。

## 踩雷集錦

1. **錯誤直覺：一個情境一個 task 最細、最好追蹤 → 正確認識：** 守門的情境在動手前就是綠的（T004 開工時 `@sc_book_03` 是那個點），單獨成 task 永遠紅不起來，「先紅」的紀律就破功。讓它跟著它守的情境；上限是一個 feature 檔。
2. **錯誤直覺：Verify 寫測試函式名最精確 → 正確認識：** 函式名由情境名產生，改個標題就是 exit 4 的 `ERROR: not found`，和練習 F 的 `test_ac1` 同一種脆弱。用 `-m` 選情境 ID，標題隨便改。
3. **錯誤直覺：Verify 回 exit 0，它擁有的情境就都跑過了 → 正確認識：** `-m "sc_book_02 or sc_book_3"` 回 `1 passed`、exit 0，`@sc_book_03` 根本沒跑。加上 `--strict-markers`，這種打錯會變成 exit 4 的 `ERROR: Unknown marker(s) in '-m' expression: sc_book_3.`（[Ch 26](./26-bdd-vs-tdd-artifacts.md) 踩雷 5）；打成另一個存在的 tag 它就放行：`--strict-markers -m "sc_book_02 or sc_book_01"` 回 `2 passed, 4 deselected`、exit 0。把 T004 的 Verify 改成這樣，SM-6 回 `T004 Verify never runs @sc_book_03` 與 `T004 Verify also runs @sc_book_01 (owner: T003)`：拿選中的和擁有的對帳，兩種錯都抓得到，而且在計畫階段就抓。
4. **錯誤直覺：換掉 tasks-template，`/speckit-tasks` 就會照做；沒換就不會 → 正確認識：** 實測 A 用核心模板，卻因 constitution 的一段話與它點名的檢查器排出了 slice；模板、constitution、skill 都是 agent 讀的字，哪一段贏要看當下。可靠的是閘門：不管誰寫的 tasks.md，`scenario_task_map.py` 的判斷都一樣。
5. **錯誤直覺：閘門 0 problems，計畫就對了 → 正確認識：** 實測 B 把沒有情境的 404 寫進 T009，檢查器照樣放行。它只保證每個情境有主人；每個行為歸誰管，要靠 review：能寫成例子的，開一張問題卡請 owner 補（[Ch 29](./29-example-mapping.md)）；404 這種狀態碼是 API 合約，照 Ch 29 的 EM-06 進 [Ch 24](./24-testplan-skill.md) 的測試計畫，不寫成情境。

## 進階：一次只驗一個 slice 的 Stop hook

Ch 27 的 Stop hook 依全域階段跑整個套件。把 Ch 27 做好的 skill 複製進來（`cp -R ../../ch27-bdd-loop/walk/.claude/skills/bdd .claude/skills/`），在本章的狀態（T004 做完、T005 到 T007 未做）簽核再跑 green：`bdd_check.py approve` 回 `approved 2 feature file(s); phase is now steps`，`bdd_check.py green` 回三行 pass、`@sc_book_04` 一行 RED-right、取消的兩個情境各一行 RED-wrong（`StepDefinitionNotFoundError`），最後是 `6 scenarios: 3 passed, 1 red for the right reason, 2 red for the wrong reason; expect green: NOT met`。整個套件要到最後一個 slice 才會綠，接在 Stop 上會一直擋。粒度要換成 task：人把正在做的 task 寫進 `.bdd/task`（Ch 27 的 `feature_guard.py` 不讓 agent 寫 `.bdd/`），hook 跑那一行 Verify。`.claude/hooks/slice_stop.py`：

```python
"""slice_stop.py: Stop hook. `.bdd/task` holds "<task id> <tasks.md>" for the slice in progress; run that
task's Verify line and block the stop while it fails. No .bdd/task: no opinion."""
import json, os, shlex, subprocess, sys
from pathlib import Path
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
sys.path.insert(0, str(root / ".claude/skills/bdd-plan/scripts"))
from scenario_task_map import read_tasks
event = json.load(sys.stdin)
if not (root / ".bdd/task").exists():
    sys.exit(0)
task, tasks_md = (root / ".bdd/task").read_text().split()
verify = read_tasks(root / tasks_md)[task][2]
run = subprocess.run(shlex.split(verify), cwd=root, capture_output=True, text=True)
if run.returncode != 0:
    why = f"{task} is not done: `{verify}` exited {run.returncode}\n" + run.stdout.strip().splitlines()[-1]
    print(json.dumps({"systemMessage": why} if event.get("stop_hook_active") else {"decision": "block", "reason": why}))
```

```
$ export CLAUDE_PROJECT_DIR=$(pwd -P); echo "T005 specs/001-room-booking/tasks.md" > .bdd/task
$ echo '{"stop_hook_active": false}' | python .claude/hooks/slice_stop.py
{"decision": "block", "reason": "T005 is not done: `.venv/bin/python -m pytest -q -m \"sc_book_04\"` exited 1\n1 failed, 5 deselected in 0.03s"}
$ echo "T004 specs/001-room-booking/tasks.md" > .bdd/task; echo '{"stop_hook_active": false}' | python .claude/hooks/slice_stop.py; echo "exit=$?"
exit=0
```

`stop_hook_active` 為 true 時，同一個 T005 事件改輸出 `systemMessage`、放行，和 Ch 27 的 `bdd_stop.py` 一樣只推一次。接線是把 Ch 27 `settings.json` 裡 Stop 那一項的腳本換成它（exec form、`.venv/bin/python`）。本段只用管線餵 JSON 測過，沒有在 `claude -p` 的真實 session 裡觸發（三次額度用在第五節），session 裡的行為是理論預期；要驗證，接好 Stop、讓 `.bdd/task` 指向 T005、請 agent 收工，看它是否收到那段 reason 並接著做。另外兩條路本章都未實測：Spec Kit 的 extension 能在 `extension.yml` 註冊 `after_tasks` 掛勾，但 1.2.5 的 `speckit-tasks` SKILL.md 是請 agent 自己輸出 `EXECUTE_COMMAND` 再去叫那個指令，屬於提示層，不是 harness 執行；Kiro 的 hooks 見 SDD Ch 30「.kiro/hooks/」一節。

## 動手練習

1. 把 [Ch 29](./29-example-mapping.md) v3 的 `sc_cancel_03`（Bob 取消之後 Alice 訂走同一時段，Bob 又來取消）以 `@sc_cancel_03` 加進 spec 的 US2，依序跑 `extract_features.py --check`、重新抽取、`scenario_task_map.py`，看三個輸出怎麼一路把你推到「替它找一個主人」。它該自成一個 task，還是跟 T006 或 T007 走？用「開工時紅在 Then」與守門情境的規則回答。（「取消一筆不存在的預約」別寫成情境：Ch 29 的 EM-06 把這種 404 交給 API 層的測試計畫。）
2. 複製你在 Ch 17 實測 2 得到的 spec（`/tmp/lab/aibdd/ch17-presets/booking/specs/001-meeting-room-booking/spec.md`），替每個 story 補上 `**Feature file**:` 後抽取、跑 Ch 28 的 lint。agent 每次寫得不同，我們那份是 36 則；你的問題裡哪些該改 spec、哪些該改 glossary？我們那份的 `@sc_booking_07`（兩人同時預約）切得成一個 slice 嗎，它的 Verify 該跑什麼？
3. 照進階一節做 T005：寫 `.bdd/task`、看 `slice_stop.py` 擋下；補上 R2 後再餵一次。再讓 `.bdd/task` 指向不存在的 `T099`，看 hook 怎麼失敗，改成回報一句人看得懂的話。

## 本章重點整理

- spec.md 的 Gherkin 區塊由 `extract_features.py` 機械地搬成 `features/*.feature`，簽核後 features/ 是合約；`--check` 抓 spec 與合約之間的漂移。抽取不管風格，風格交給 Ch 28 的 lint。
- task 依情境切：一個 task 擁有同一個 feature 檔的一到兩個情境，下一行的 Verify 用 `-m` 選它們，開工時紅在 Then、收工時綠。守門情境跟著它守的情境走；選 tag，不選名稱。
- `scenario_task_map.py` 不跑測試就能判斷每個情境恰好一個主人、每行 Verify 恰好選中它擁有的情境；它重現 pytest-bdd「scenario、Rule、Feature 三層 tag 都是 marker」與 pytest 的 `-m` 文法，七個運算式和 `--collect-only` 逐一相同。
- 格式住在 preset 0.2.0 的 tasks-template（`replace`），規則住在 constitution 的 Acceptance Contract，流程與閘門住在 `bdd-plan` skill。三次實跑裡，stock 設定排出 0 個可辨認的主人，加了 constitution 的 stock skill 與 bdd-plan 都過閘；閘門擋不住沒有情境的行為。

## 自我檢核

- [ ] 不看本章，說出 SM-1 到 SM-6 各擋哪一種錯，並各舉一個本章出現過的實例。
- [ ] 為什麼 `@sc_book_03` 不能自成一個 task？如果硬要，它的 Verify 開工前會是什麼結果？
- [ ] `-m "sc_book_02 or sc_book_3"` 與 `-m "sc_book_3"` 的 exit code 各是多少？為什麼前者比後者危險？
- [ ] 靜態判斷 Verify 會選中哪些情境，依賴 pytest-bdd 的哪個行為？如果某天 pytest-bdd 不再把 Feature 的 tag 套到情境上，`scenario_task_map.py` 哪一行要改？
- [ ] 實測 S、A、B 的差別，你能歸到模板、constitution、skill 加閘門的哪一層？要把三者分開，第四次實跑該怎麼設計？
- [ ] spec.md 和 features/ 各在什麼時候是合約？clarify 在簽核之後改了 spec，之後必須依序發生哪三件事？

## 延伸閱讀

- **[Bill Wake — INVEST in Good Stories, and SMART Tasks](https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/)**（xp123，2003-08-17）
  - **讀哪裡**：「V – Valuable」那段的多層蛋糕，以及後半的 SMART tasks。**學到什麼**：垂直切的原始論證（一整層資料層若沒有畫面層，對客人沒什麼價值），以及 task 要 Specific、Measurable（量的是「can we mark it as done?」）。**和本章的關聯**：本章的 Verify 行就是讓 task 可量測的那把尺。**前提**：知道 user story 是什麼。
- **[Gojko Adzic — Splitting user stories: the hamburger method](https://gojko.net/2012/01/23/splitting-user-stories-the-hamburger-method/)**（2012）
  - **讀哪裡**：全文。**學到什麼**：團隊習慣依技術步驟拆工作，作者把那些步驟當成漢堡的層，替每層挑品質等級，再組成一口一口的垂直切片。**和本章的關聯**：本章的 slice 是同一個想法的 BDD 版，每一口用一個情境定義、用一行 Verify 驗收。
- **[pytest — Working with custom markers](https://docs.pytest.org/en/stable/example/markers.html)** 與 **[Exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)**
  - **讀哪裡**：用 `-m` 搭 `and`、`or`、`not` 與括號選測試，註冊 markers 與 `strict_markers`（`--strict-markers` 開的就是它）；exit code 4 與 5 的定義。**學到什麼**：`-m` 精確比對 marker 名、`-k` 比對名稱子字串。**和本章的關聯**：這是 Verify 只准用 `-m`、SM-5 擋下 `-k` 的根據。
- **[pytest-bdd 官方文件](https://pytest-bdd.readthedocs.io/en/stable/)**
  - **讀哪裡**：「Organizing your scenarios」的 tags 一段。**學到什麼**：tag 以 marker 存放、`@` 自動去掉、開 `--strict-markers` 時每個 tag 都要登記在 `pytest.ini`，以及 `pytest_bdd_apply_tag` hook；「Rules」一節另外寫明 Rule 的 tag 會套到底下每個情境。**和本章的關聯**：Feature 層的 tag 也會套，tags 那段的範例用到了卻沒明說；`scenario_task_map.py` 依賴的這一條，[Ch 26](./26-bdd-vs-tdd-artifacts.md) 用 `-m agenda` 實測過、[Ch 30](./30-one-gherkin-two-drivers.md) 從 `plugin.py` 讀過，本章再從 `scenario.py` 看它在哪一行合併。
- **[Spec Kit v1.2.5 — templates/commands/tasks.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/tasks.md)**
  - **讀哪裡**：「Task Generation Rules」的 Checklist Format 與 Phase Structure。**學到什麼**：stock `/speckit-tasks` 的原始規則，安裝後就是 `.claude/skills/speckit-tasks/SKILL.md`。**和本章的關聯**：本章的 tasks-template 與 bdd-plan 改了哪幾條、留下哪幾條，對照著讀最清楚。

計畫現在有了可執行的合約與可檢查的粒度，但前提是專案從 spec 開始；已經有一堆測試、卻沒有任何 feature 檔的專案，得先把既有的測試反推成情境。

→ [Ch 32 既有專案導入 BDD：從測試反推，重構成 Gherkin + StepDef](./32-brownfield-bdd.md)
