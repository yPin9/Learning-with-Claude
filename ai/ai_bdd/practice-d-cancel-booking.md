# 練習 D — testplan → TDD → E2E 做出「取消預約」

> **目標**：把 Ch 19–25 拼成一次完整的交付。從一份給定的 spec 推出取消預約（R4）的測試計畫，先立介面 stub，讓 unit、API、UI 三層共 11 個測試在 RED 閘門前「紅得對」，交給 implementer 在 hook 的約束下做到綠，最後由你自己跑 VERIFY 與 mutant。完成後你能說出每個案例抓哪個 bug、哪一處行為沒有任何測試釘住，也能說出第二個功能進來時，Ch 24、Ch 25 的工具在哪裡失準。

> **環境**：macOS 26.6（arm64）、Python 3.12.15、pytest 9.2.0、FastAPI 0.162.0（starlette 1.10.0）、Playwright 1.64.1（Chromium）、Spec Kit 1.2.5（只用到 `create-new-feature.sh`）、Claude Code 2.1.295。實驗目錄 `/tmp/lab/aibdd/practice-d-cancel/`，從空目錄開始。參考解答的輸出都是 2026-11-02 到 11-03 凌晨在這台機器上實際跑出來的；GREEN 那一段是一次 `claude -p` 的實際執行紀錄（2026-11-02，模型 claude-opus-5-5）。

## 背景與動機

Ch 25 的流水線只在空 repo 上做過第一個功能。第二個功能多了兩件事：它踩在已經上線的程式上，舊程式裡的決定會被新行為翻出來；它和舊功能共用一套測試，每一支「一份計畫對一組測試」的工具都得回答「哪些測試是我的」。取消預約兩件都碰到。前面幾章替它留了三張紙條：[Ch 22](./22-backend-e2e-first.md) 審 agent 的程式時寫下「一旦能刪除，刪掉最後一筆後 id 會被重用」；[Ch 23](./23-frontend-e2e-playwright.md) 說取消按鈕要掛在 `get_by_test_id("booking-<id>")` 底下；[Ch 25](./25-tdd-in-sdd-pipeline.md) 進階一節說兩份計畫的測試同場跑時，閘門只餵新功能的測試檔，新計畫的編號接著舊的編。照 Ch 25 第三節的單一功能做法跑一次全套，你會親眼看到第三張紙條的前半句為什麼省不了。

```
 spec.md（給定）  R4 · FR-001..006 · @sc_cancel_01..03
    │ testplan_lint --source；review：「這一案抓得到哪個別案抓不到的 bug？」
 testplan.md      TP-011..021：5 unit · 4 api · 2 ui（接著 001 的 TP-010 編）
    │ Phase 2：介面 stub（例外、Schedule.cancel、頁面的清單列與 Cancel 按鈕）
 RED              tests/*/test_cancel*.py，11 個紅燈
    │ red_gate --plan（只餵這個功能的檔案）＋全套（001 仍綠）＋trace＋aaa_lint
    │ commit、tag red-002-cancel-us1、echo GREEN > .tdd/phase
 GREEN            implementer 只改 booking/；tdd_guard 鎖測試、require_green 擋收工
 VERIFY＋review   red_gate --expect green · tamper_check · kill_matrix（14 個 mutant）
```

PM 交來 spec，你寫計畫、立 stub、寫紅燈、過閘門，實作交給 agent，驗收自己做：這是團隊的日常形狀。工具最後都會說 `0 problems`，要練的是它們放行之前那幾輪，以及全部放行之後還剩什麼要靠人看。這和 [pstack 練習 D](../pstack/practice-d-plan-and-log.md) 要你找的「它們都通過了、但計畫或日誌仍然是錯的」是同一個題目，換到測試這一側。

## 任務規格

**起點**是 Ch 25 結束時的 `booking/`（我們的 lab 是 commit `c6a5051`）：

```bash
mkdir -p /tmp/lab/aibdd/practice-d-cancel && cd /tmp/lab/aibdd/practice-d-cancel
git clone -q /tmp/lab/aibdd/ch25-red-gate/booking booking && cd booking
git config user.name kata && git config user.email kata@example.invalid   # clone 不帶 local 設定
source /tmp/lab/aibdd/.venv/bin/activate && python -m pytest -q | tail -1     # 10 passed in 0.85s
bash .specify/scripts/bash/create-new-feature.sh --json --short-name cancel "Cancel a booking"
```

最後一行在 stdout 印出一行 JSON（stderr 另有兩行 `# To persist: export SPECIFY_FEATURE=…` 提示），含 `"BRANCH_NAME":"002-cancel"`、spec 的路徑與 `"FEATURE_NUM":"002"`：Spec Kit 1.2.5 照模板建出 `specs/002-cancel/spec.md`，把目前的功能寫進被 gitignore 的 `.specify/feature.json`，不開 git branch。裝了 preset 的專案要靠 PyYAML 解析模板，所以要在啟用 venv 的 shell 裡跑；用系統的 `python3` 會得到 `Error: PyYAML is required to resolve preset template composition`，exit 2（同一則錯誤 [Ch 17](./17-constitution-and-presets.md) 在 `resolve-template.sh` 上記過）。`.tdd/` 被 gitignore、不會被 clone，hook 在你建立 `.tdd/phase` 之前都不作用。我們 lab 的 `schedule.py`、`api.py` 是 Ch 25 第二次實跑時 agent 寫的（Ch 25 沒有列出）：`Schedule.bookings` 是 list 屬性，id 是 `len(self.bookings) + 1`；lab 裡的 `testplan_lint.py` 是更早的 98 行副本，我們先換成 [Ch 24](./24-testplan-skill.md) 正文那一版（99 行，下面的 diff 以它為準）。照 Ch 25 第五節自己做 GREEN 的話，你的 `Schedule` 是 Ch 19 的 `_last_id` 計數器加 Ch 21 的 `bookings()` 方法：把下面單元測試裡的四個 `schedule.bookings` 寫成 `schedule.bookings()`（不改的話，閘門把 TP-015 判成 RG-2：`TypeError: 'method' object is not iterable`；GREEN 之後 TP-011、012、013、015 也永遠綠不了，實測 `4 failed, 1 passed`）。你的 id 本來就不重用，所以 GREEN 只缺 `cancel`；mutant 的替換文字改成對你的 `_bookings` 與 `_last_id`（例如 `booking_id >= self.next_id` 變成 `booking_id > self._last_id`）。這條路我們從 Ch 25 正文重建的狀態跑過一次：`red-gate.txt`、VERIFY 與 14 個 mutant 的輸出和下文一字不差。其餘依賴：頁面是 `api.py` 裡沒有清單的 `PAGE` 字串，api 測試用 `TestClient(create_app())`，`tests/ui/conftest.py` 在 thread 裡起 uvicorn、只把 `expect` 縮成 2 秒。

| 交付物 | 驗收標準 |
|---|---|
| `specs/002-cancel/spec.md` | 下面這份，覆寫腳本建出的模板 |
| `specs/002-cancel/testplan.md` | 從 TP-011 編起；unit、api、ui 三層都有；`testplan_lint.py --source` 印 `0 problems`；每一案答得出 review 問題 |
| 介面 stub | `NotFound`、`Forbidden`、什麼都不做的 `Schedule.cancel(booking_id, by)`、頁面的清單列 `data-testid="booking-<id>"` 與按了沒反應的 Cancel；寫紅燈之前 commit |
| 紅燈 | 一案一測 `test_tpNNN_*`，在 `tests/<level>/test_cancel*.py`；只餵這些檔案的 `red_gate.py --plan` 印 `0 problems`；全套 `11 failed, 10 passed`；trace 與 `aaa_lint.py`（unit、api）`0 problems`；`red-gate.txt` 跟著 commit，tag `red-002-cancel-us1` |
| 實作 | tag 之後只有 `booking/` 變動；`.tdd/phase` 是 GREEN，你或 agent 做都可以 |
| VERIFY | 你自己跑：`red_gate --expect green` 每個 spec ID 都 met、`tamper_check --base red-002-cancel-us1` `0 problems`、全套 `21 passed`；存成 `verify.txt` |
| review | 用 mutant 說出每案抓哪個 bug；找出至少一處綠燈沒釘住的行為 |

**規則**：001 的 10 個測試一字不改；計畫編號只增不減。這個功能省略 `plan.md` 與 `tasks.md`：沒有新的技術決策（同一個 FastAPI app、同一個記憶體裡的 `Schedule`），plan 要定的只剩介面，而介面直接寫成 stub 並 commit；tasks 的角色由計畫擔任，每個 TP 是一件待辦（Ch 24 第六節）。要走完整的 Spec Kit 順序，見延伸挑戰。`specs/002-cancel/spec.md`：

```markdown
# Feature Specification: Cancel a booking

**Feature Branch**: `002-cancel`
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cancel my own booking (Priority: P1)

Bob no longer needs the room he booked. He cancels the booking and the slot is free again. Nobody else can cancel it.

**Acceptance Scenarios**:

1. `@sc_cancel_01` **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Bob cancels the booking of "Taipei" from 10:00 to 11:00, **Then** the booking is cancelled
2. `@sc_cancel_02` **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice cancels the booking of "Taipei" from 10:00 to 11:00, **Then** the cancellation is rejected because only Bob can cancel this booking
3. `@sc_cancel_03` **Given** Bob has cancelled the booking of "Taipei" from 10:00 to 11:00, **When** Alice books "Taipei" from 10:00 to 11:00, **Then** the booking is confirmed

### Edge Cases

- A cancelled booking is gone: cancelling it again is answered like an id that never existed.
- A cancelled booking no longer blocks its room and time (R1 counts only current bookings).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (R4): System MUST let the person who made a booking cancel it.
- **FR-002** (R4): System MUST reject a cancellation by anyone else, naming the booker: `only Bob can cancel this booking`.
- **FR-003**: System MUST reject a cancellation of an id that has no booking: `booking 7 not found`.
- **FR-004**: Booking ids MUST never be reused, also after a cancellation.
- **FR-005**: `DELETE /bookings/{id}?by=<name>` MUST answer 204 on success, 403 for FR-002 and 404 for FR-003, with the message in `detail`; `GET /bookings` no longer lists a cancelled booking.
- **FR-006**: The booking page MUST list current bookings, one row each (`Taipei 10:00-11:00 Bob`) with a Cancel button. Cancel acts as the person in Name; the status line then shows `Cancelled Taipei 10:00-11:00` or `Rejected: <detail>`.

## Assumptions

- Booking, overlap (R1) and range (R2) rules are those of `specs/001-booking/spec.md`.
- Office hours (R3) are a separate feature.
```

情境句型就是 [Ch 29](./29-example-mapping.md) 之後定為全課標準的 R4 說法；標籤編號則是這份 spec 自己的（Ch 29 的 map 把第 3 條編成 `sc_cancel_04`）。FR 照 Spec Kit 的慣例每份 spec 從 FR-001 編起，TP 卻要接著編：TP 編號會變成測試名稱的一部分，而兩個功能的測試在同一次 pytest 裡跑。**編號要在哪個範圍內唯一，取決於它在哪裡被當成鑰匙。**

## 期望輸出範例

交 RED 之前的兩行，與 GREEN 之後的兩行：

```
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/002-cancel/testplan.md | tail -1
11 tests, 11 red as planned, 0 problems
$ python -m pytest -q --tb=no | tail -1
11 failed, 10 passed in 5.12s
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan specs/002-cancel/testplan.md --expect green | tail -1
11 tests, 11 green as planned, 0 problems
$ python .claude/skills/tdd/scripts/tamper_check.py --base red-002-cancel-us1
10 files, 0 problems
```

邊界：只斷言 `response.status_code == 404` 的 TP-018，程式還不存在就會過，閘門說 `RG-2 TP-018 passes before the code exists, so it cannot show that the code works`。

## 如果你卡住了

1. **閘門點名 001 的 TP-001 到 TP-010，十行 RG-4**：讀 `red_gate.py` 的 `judge()`，有 `--plan` 時，名字帶 TP 編號卻不在這份計畫裡的測試一律是 RG-4。接著編號避開的是撞名，不是這個。這一次 pytest 該跑哪些檔案？001 還綠著又靠哪一次執行證明？
2. **TP 測試「passes before the code exists」**：路由還不存在時，FastAPI 回的也是 404。只看狀態碼分不出「還沒做」與「做好了」。
3. **UI 測試每個卡 30 秒，閘門說 `TimeoutError`**：conftest 只縮短了 `expect`，動作仍是 Playwright 預設的 30 秒。更根本的是頁面上還沒有測試要找的東西：UI 的介面 stub 長什麼樣？
4. **unit 測試在 Act 丟出 `Conflict`**：stub 不做事時，「取消後再訂同一時段」會撞上那筆還在的預約。哪一層會把這個例外變成可以斷言的值？
5. **`KeyError: 'id'`**：斷言式裡的運算在比較之前就炸了。比對整個 response。
6. **`testplan_lint.py` 的 `--tests` 只收一個路徑**：讀 `trace()` 呼叫 pytest 的那一行與 argparse 的定義，要改的不超過四行。

## 實作步驟建議

### Step 1：起點與 spec

clone、確認 `10 passed`、放進 spec。先讀 `booking/schedule.py`：取消一筆之後，下一筆的 id 會是多少？

### Step 2：測試計畫與 review

照 [Ch 24](./24-testplan-skill.md) 的格式推：R4 的兩側、FR-003 的兩種「找不到」（從沒存在、已經取消）、id 規則、三個 API 對應、兩條頁面旅程。lint 到 `0 problems` 後逐案問「哪個 bug 只有它抓得到」，至少拿 agent 寫過、取消後會重用 id 的兩種算法去想：我們 lab 裡 Ch 25 那次 agent 的 `len(self.bookings) + 1`（Ch 22 第三節講過它的毛病），與 Ch 22 那次 agent 的「既有最大 id 加一」。

### Step 3：介面 stub

兩個例外、不做事的 `cancel`、頁面的清單列與沒反應的 Cancel；全套仍 `10 passed` 再 commit。RED 期間發現 stub 不夠，由人補（guard 在 RED 擋原始碼）。

### Step 4：RED 與閘門

`mkdir .tdd && echo RED > .tdd/phase`，一案一測寫進三個 `test_cancel*.py`，照 [Ch 25](./25-tdd-in-sdd-pipeline.md) 第三節的順序過閘門，每個「紅得不對」都先修；都過了才 commit、打 tag、寫 GREEN。

### Step 5：GREEN 與 Step 6：VERIFY、review

自己做就走 [Ch 22](./22-backend-e2e-first.md) 的雙迴圈（api、ui 是外圈，unit 是內圈）；交給 agent 就只給 `booking/` 的編輯權與一條測試指令。之後自己跑 VERIFY，再用 mutant 檢查每一案抓得到什麼，找出至少一處沒有測試釘住的行為。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### Step 2：計畫，v1 與一次 review

v1 的 lint 是 `6 unit, 3 api, 2 ui, 4 out of scope`、`11 cases, 0 problems`（拿掉 OOS-2 那列會多一行 `TPL-05 R2 from specs/002-cancel/spec.md is in no case's trace and not in Out of scope`）。review 只改了 TP-015：v1 讓 Bob 取消**最舊**的 1 號，`len + 1` 會把 Carol 編成重複的 2 號而被抓到，「最大 id 加一」卻照樣給出 3 號。改成取消最新的那一筆，兩種算法都會重用 2 號：

```diff
@@ -32 +32 @@
-### TP-015 Ids are not reused after a cancellation
+### TP-015 Ids are not reused after the newest booking is cancelled
@@ -35 +35 @@
-- arrange: Bob holds Taipei 10:00-11:00 as booking 1; Alice holds Tokyo 10:00-11:00 as booking 2; Bob cancels booking 1
+- arrange: Bob holds Taipei 10:00-11:00 as booking 1; Alice holds Tokyo 10:00-11:00 as booking 2 and cancels it
@@ -37 +37 @@
-- assert: the schedule holds booking 2 by Alice and booking 3 by Carol
+- assert: the schedule holds booking 1 by Bob and booking 3 by Carol
```

定稿的標頭照 Ch 24（`# Test plan: 002-cancel`、`Spec:` 一行、`IDs are never renumbered…` 一行），IDs 那一行末尾多一句 `TP-001..TP-010 belong to 001-booking.`。每一案照 Ch 24 的六行格式（`### TP-NNN <標題>`、`- level: … | priority: … | kind: …`、`- trace:`、`- arrange:`、`- act:`、`- assert:`）展開下表，欄位文字照抄。TP-019 在 v1 是 unit，怎麼搬到 api 見 Step 4：

| 案例 | level · priority · kind | trace | arrange | act | assert |
|---|---|---|---|---|---|
| TP-011 The booker cancels one booking and the other stays | unit · P1 · happy | R4, FR-001, @sc_cancel_01 | Bob holds Taipei 10:00-11:00 as booking 1; Alice holds Tokyo 10:00-11:00 as booking 2 | Bob cancels booking 1 | the schedule holds only booking 2, Tokyo 10:00-11:00 by Alice |
| TP-012 Someone else cannot cancel | unit · P1 · sad | R4, FR-002, @sc_cancel_02 | Bob holds Taipei 10:00-11:00 as booking 1 | Alice cancels booking 1 | rejected with "only Bob can cancel this booking"; the schedule still holds booking 1 |
| TP-013 An unknown id is rejected | unit · P2 · sad | FR-003 | Bob holds Taipei 10:00-11:00 as booking 1 | Bob cancels booking 7 | rejected with "booking 7 not found"; the schedule still holds booking 1 |
| TP-014 A cancelled booking cannot be cancelled again | unit · P2 · boundary | FR-003 | Bob held Taipei 10:00-11:00 as booking 1 and cancelled it | Bob cancels booking 1 again | rejected with "booking 1 not found" |
| TP-015 Ids are not reused after the newest booking is cancelled | unit · P1 · boundary | FR-004 | （見上面的 diff） | Carol books Seoul 10:00-11:00 | （見上面的 diff） |
| TP-016 The API answers 204 and stops listing the booking | api · P2 · happy | FR-005, @sc_cancel_01 | Bob holds Taipei 10:00-11:00 via POST /bookings | DELETE /bookings/1?by=Bob | 204; GET /bookings is [] |
| TP-017 The API answers 403 with the booker's name | api · P2 · sad | R4, FR-005, @sc_cancel_02 | Bob holds Taipei 10:00-11:00 via POST /bookings | DELETE /bookings/1?by=Alice | 403, detail "only Bob can cancel this booking"; GET /bookings has 1 item |
| TP-018 The API answers 404 for an unknown id | api · P2 · sad | FR-003, FR-005 | no bookings | DELETE /bookings/7?by=Bob | 404, detail "booking 7 not found" |
| TP-019 A freed slot is booked again with a new id | api · P1 · boundary | R1, FR-004, @sc_cancel_03 | Bob holds Taipei 10:00-11:00 via POST /bookings and DELETEs it as Bob | Alice POSTs Taipei 10:00-11:00 | 201 with booking 2, Taipei 10:00-11:00 by Alice |
| TP-020 The page cancels a booking from its row | ui · P1 · happy | FR-006, @sc_cancel_01 | Bob holds Taipei 10:00-11:00 via POST /bookings; the page is open; Name is Bob | Bob presses Cancel in the row of booking 1 | the status line reads "Cancelled Taipei 10:00-11:00"; the row is gone |
| TP-021 The page shows why someone else cannot cancel | ui · P2 · sad | R4, FR-006, @sc_cancel_02 | Bob holds Taipei 10:00-11:00 via POST /bookings; the page is open; Name is Alice | Alice presses Cancel in the row of booking 1 | the status line reads "Rejected: only Bob can cancel this booking"; the row is still listed |

```markdown
## Out of scope

| ID | What | Why |
|---|---|---|
| OOS-1 | Office hours (R3) | a separate feature, per the spec's Assumptions |
| OOS-2 | The overlap and range rules (R1, R2) themselves | unchanged; 001-booking's TP-001..TP-010 run in the same suite |
| OOS-3 | Proving who is asking | `by` is whatever the client sends; R4 stops mistakes, not attackers; revisit with accounts |
| OOS-4 | Two cancels of one booking at the same instant | in-memory, single-process store, as in 001-booking's OOS-3 |
```

TP-014 是一個**設計決定**：RFC 9110 說 DELETE 是冪等的，但冪等指對伺服器狀態的效果，重送「will have the same intended effect, even if the original request succeeded, though the response might differ」，第二次取消回 404 不違反它。OOS-3 是最重要的一列：測試全綠，Alice 照樣能送 `?by=Bob`。

### Step 3：介面 stub

domain 的 stub 和計畫一起 commit（`d334fa4`）：`InvalidRange` 下面加 `class NotFound(BookingError): pass` 與 `class Forbidden(BookingError): pass`，`Schedule` 最後加 `def cancel(self, booking_id, by):` 與一行 `return None`，註解 `# 002-cancel interface stub: no behaviour yet`；CLAUDE.md 改一行，說功能文件在 `specs/<feature>/`。頁面的 stub 是第一次過閘門後才補的（理由在 Step 4）。UI 的介面就是規格要找的 handle：清單列的 test id 與叫 Cancel 的按鈕，按下去會怎樣留給 GREEN。Book 的 handler 用 `document.querySelector("button")` 拿第一個按鈕，Cancel 排在它後面，001 的 UI 測試照樣 `1 passed`：

```diff
@@ -11,0 +12 @@ PAGE = """<input aria-label=Room><input aria-label=Start><input aria-label=End><
+<ul aria-label=Bookings></ul>
@@ -13,0 +15,11 @@ const value = label => document.querySelector(`[aria-label=${label}]`).value;
+async function refresh() {  // 002-cancel UI stub: the rows and Cancel buttons the specs locate, no cancel yet
+  const rows = await (await fetch("/bookings")).json();
+  document.querySelector("ul").replaceChildren(...rows.map(b => {
+    const row = document.createElement("li"), cancel = document.createElement("button");
+    row.dataset.testid = `booking-${b.id}`;
+    cancel.textContent = "Cancel";
+    row.append(`${b.room} ${b.start}-${b.end} ${b.by} `, cancel);
+    return row;
+  }));
+}
+refresh();
```

### Step 4：RED

`tests/unit/test_cancel.py`，版面照 [Ch 21](./21-tdd-skill-aaa.md) 的 3A。TP-011 與 TP-015 斷言整份清單：stub 的 `cancel` 不做事時清單多一筆，紅在斷言上：

```python
from datetime import time

import pytest

from booking.schedule import Booking, Forbidden, NotFound, Schedule

TAIPEI_BOB = Booking(1, "Taipei", time(10), time(11), "Bob")

def bob_holds_taipei():
    schedule = Schedule()
    schedule.book("Taipei", time(10), time(11), "Bob")
    return schedule

def test_tp011_booker_cancels_one_booking_and_the_other_stays():
    schedule = bob_holds_taipei()
    schedule.book("Tokyo", time(10), time(11), "Alice")

    schedule.cancel(1, "Bob")

    assert schedule.bookings == [Booking(2, "Tokyo", time(10), time(11), "Alice")]

def test_tp012_someone_else_cannot_cancel():
    schedule = bob_holds_taipei()

    with pytest.raises(Forbidden, match="^only Bob can cancel this booking$"):
        schedule.cancel(1, "Alice")

    assert schedule.bookings == [TAIPEI_BOB]

def test_tp013_unknown_id_is_rejected():
    schedule = bob_holds_taipei()

    with pytest.raises(NotFound, match="^booking 7 not found$"):
        schedule.cancel(7, "Bob")

    assert schedule.bookings == [TAIPEI_BOB]

def test_tp014_cancelled_booking_cannot_be_cancelled_again():
    schedule = bob_holds_taipei()
    schedule.cancel(1, "Bob")

    with pytest.raises(NotFound, match="^booking 1 not found$"):
        schedule.cancel(1, "Bob")

def test_tp015_ids_are_not_reused_after_the_newest_booking_is_cancelled():
    schedule = bob_holds_taipei()
    schedule.book("Tokyo", time(10), time(11), "Alice")
    schedule.cancel(2, "Alice")

    schedule.book("Seoul", time(10), time(11), "Carol")

    assert [(b.id, b.by) for b in schedule.bookings] == [(1, "Bob"), (3, "Carol")]
```

`tests/api/test_cancel_api.py`：

```python
from fastapi.testclient import TestClient

from booking.api import create_app

def bob_holds_taipei():
    client = TestClient(create_app())
    client.post("/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"})
    return client

def test_tp016_delete_answers_204_and_the_list_drops_the_booking():
    client = bob_holds_taipei()

    response = client.delete("/bookings/1", params={"by": "Bob"})

    assert (response.status_code, client.get("/bookings").json()) == (204, [])

def test_tp017_delete_by_someone_else_is_403_naming_the_booker():
    client = bob_holds_taipei()

    response = client.delete("/bookings/1", params={"by": "Alice"})

    assert (response.status_code, response.json()["detail"]) == (403, "only Bob can cancel this booking")
    assert len(client.get("/bookings").json()) == 1

def test_tp018_delete_of_an_unknown_id_is_404_naming_the_id():
    client = TestClient(create_app())

    response = client.delete("/bookings/7", params={"by": "Bob"})

    assert (response.status_code, response.json()["detail"]) == (404, "booking 7 not found")

def test_tp019_freed_slot_is_booked_again_with_a_new_id():
    client = bob_holds_taipei()
    client.delete("/bookings/1", params={"by": "Bob"})

    response = client.post("/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Alice"})

    assert (response.status_code, response.json()) == (201, {"id": 2, "room": "Taipei", "start": "10:00", "end": "11:00", "by": "Alice"})
```

`tests/ui/test_cancel_page.py`，按鈕在那一列底下找（Playwright 文件「Matching inside a locator」的串接寫法）：

```python
import httpx
from playwright.sync_api import expect

def press_cancel_as(name, base_url, page):
    httpx.post(f"{base_url}/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"})
    page.goto(base_url)
    page.get_by_label("Name").fill(name)
    page.get_by_test_id("booking-1").get_by_role("button", name="Cancel").click()

def test_tp020_page_cancels_a_booking_from_its_row(base_url, page):
    press_cancel_as("Bob", base_url, page)

    expect(page.get_by_role("status")).to_have_text("Cancelled Taipei 10:00-11:00")
    expect(page.get_by_test_id("booking-1")).to_have_count(0)

def test_tp021_page_shows_why_someone_else_cannot_cancel(base_url, page):
    press_cancel_as("Alice", base_url, page)

    expect(page.get_by_role("status")).to_have_text("Rejected: only Bob can cancel this booking")
    expect(page.get_by_test_id("booking-1")).to_be_visible()
```

**第一次過閘門**交出的版本有三處不同：TP-018 只斷言 `response.status_code == 404`（名字也還沒有 `_naming_the_id`）；TP-019 照 v1 寫在 unit（Bob 訂了又取消，Alice 訂同一時段，斷言 `booking == Booking(2, ...)`）；頁面還沒有 stub（conftest 也還是 Ch 25 那份）。照 Ch 25 跑全套再判：

```
$ python -m pytest -q --tb=no --junitxml=.tdd/red.xml | tail -1
10 failed, 11 passed in 61.12s (0:01:01)
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/002-cancel/testplan.md
…（略：TP-016、017、011～015 共 7 行證據）
tests.api.test_bookings_api::test_tp006_overlap_is_409_naming_the_holder: RG-4 TP-006 is not in the plan; add the case or rename the test
…（略：TP-007 的 RG-4）
tests.api.test_cancel_api::test_tp018_delete_of_an_unknown_id_is_404: RG-2 TP-018 passes before the code exists, so it cannot show that the code works
…（略：TP-008 的 RG-4）
tests.ui.test_cancel_page::test_tp020_page_cancels_a_booking_from_its_row: RG-2 TP-020 stops before its assertion: playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.; fix the test or the stub until it reaches its assert
…（略：TP-021 同一句）
tests.unit.test_cancel::test_tp019_freed_slot_is_booked_again_with_a_new_id: RG-2 TP-019 stops before its assertion: booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob; fix the test or the stub until it reaches its assert
…（略：TP-005、TP-001～004、009、010 的 RG-4）
21 tests, 7 red as planned, 14 problems
```

- **RG-4 × 10**：接著編號避開了 `test_tp001_` 撞名，擋不住這個；Ch 25 進階一節要閘門只餵新功能的測試檔，為的就是它：只讓 pytest 跑這個功能的檔案 `tests/*/test_cancel*.py`（zsh 與 bash 都會展開），001 還綠著另由全套證明。兩個一起用，編號在人讀的地方也不會撞。
- **TP-018 先綠**：路由不存在時 FastAPI 回 `404 {"detail": "Not Found"}`，和 R4 的 404 只差在 `detail`。連 `detail` 一起斷言。
- **TP-020、021 各等 30 秒**：頁面上沒有那一列，`click` 用預設的 30000 ms 等到逾時，整套跑了 61 秒。由人補上頁面 stub；`tests/ui/conftest.py` 的 `page` 加 `page.set_default_timeout(2_000)`（測試設定，RED 期間 test-writer 可以改），讓動作也只等 2 秒。
- **TP-019 的 `Conflict`**：stub 不做事，Alice 撞上 Bob 那筆，例外從 Act 漏出，閘門把它和 ImportError 歸成同一類。Ch 25 第二節料到過這種紅（「程式存在之後（下一個 story、既有專案）才會」），給的解法是 `--allow booking.schedule.Conflict`：加上它，這份報告裡的 TP-019 就成了 `TP-019 red: booking.schedule.Conflict: Taipei is taken 10:00-11:00 by Bob`。我們沒用，因為這個前綴對報告裡每個測試都生效：一個 Arrange 把同一時段訂兩次的壞測試，加同一旗標也得到 `1 tests, 1 red as planned, 0 problems`。同一個「還沒做」在 api 層是 409，跑得到斷言，所以計畫的 level 改成 api、測試搬到 `tests/api/`。這是 Ch 24「選看得到結果的最低一層」的補充：對「不該丟例外」的行為，看得到的最低一層是例外被翻成值的那一層。

改完，`python -m pytest -q --tb=no tests/*/test_cancel*.py --junitxml=.tdd/red.xml` 是 `11 failed in 4.77s`（第一次是 61 秒），閘門剩一個：`RG-2 TP-019 stops before its assertion: KeyError: 'id'`、`11 tests, 10 red as planned, 1 problems`。搬過去的 TP-019 第一版寫 `response.json()["id"]`，409 的 body 沒有 `id`。改成比對整個 body（上面的定稿），紅燈訊息還多說了原因：

```
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/red.xml --plan specs/002-cancel/testplan.md | tee specs/002-cancel/red-gate.txt
…（略：TP-016、017、018 三行）
TP-019 red: AssertionError: assert (409, {'detai...1:00 by Bob'}) == (201, {'id': 
TP-020 red: AssertionError: Locator expected to have text 'Cancelled Taipei 10:00-
TP-021 red: AssertionError: Locator expected to have text 'Rejected: only Bob can 
…（略：TP-011～015：兩行 assert、三行 DID NOT RAISE）
11 tests, 11 red as planned, 0 problems
```

全套是 `11 failed, 10 passed in 5.12s`。trace 撞上同一個假設：`--tests tests` 印十行 `TPL-10 TP-001 is not in the plan; add the case or rename the test` 這類訊息與 `11 cases, 11 tests, 10 problems`；改傳三個檔案，得到 `testplan_lint.py: error: unrecognized arguments: tests/ui/test_cancel_page.py tests/unit/test_cancel.py`（exit 2）。讓 `--tests` 收多個路徑：

```diff
@@ -67 +67 @@
-    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", tests], capture_output=True, text=True)
+    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *tests], capture_output=True, text=True)
@@ -71 +71 @@
-        return bad(tests, "TPL-09", f"pytest cannot collect {', '.join(files) or tests}: {why[0]}")
+        return bad(" ".join(tests), "TPL-09", f"pytest cannot collect {', '.join(files) or ' '.join(tests)}: {why[0]}")
@@ -81 +81 @@
-                bad(nodeid, "TPL-11", f"{c['id']} has level {level}; move the test under {tests}/{level}/")
+                bad(nodeid, "TPL-11", f"{c['id']} has level {level}; move the test under a {level}/ directory")
@@ -89 +89 @@
-    ap.add_argument("--tests", help="test dir: one test_tpNNN_* per case, under <dir>/<level>/")
+    ap.add_argument("--tests", nargs="+", help="test dirs or files of this feature: one test_tpNNN_* per case, under <level>/")
```

```
$ python .claude/skills/testplan/scripts/testplan_lint.py specs/002-cancel/testplan.md --source specs/002-cancel/spec.md --tests tests/*/test_cancel*.py
specs/002-cancel/testplan.md: 5 unit, 4 api, 2 ui, 4 out of scope
11 cases, 11 tests, 0 problems
$ python .claude/skills/tdd/scripts/aaa_lint.py tests/unit/test_cancel.py tests/api/test_cancel_api.py
2 files, 9 tests, 0 problems
```

反過來也一樣：001 的 trace 照 Ch 24 寫 `--tests tests`，現在是 `10 cases, 10 tests, 11 problems`。共用一套測試之後，每個功能的檢查都要指名自己的檔案。Stop 事件餵給 Ch 25 版的 `require_green.py`，回 `{"systemMessage": "require_green: RED ok (11 failed, 10 passed in 5.24s); review the failures before GREEN"}`；它不帶計畫，拿第一次那份報告問不加 `--plan` 的閘門，只抓到 TP-019、020、021，看不到 TP-018 先綠，也沒有 RG-4。交接（之後把 Edit 測試檔的事件餵給 `tdd_guard.py`，回 `GREEN phase: tests/unit/test_cancel.py is a test file; ...` 的 deny）：

```bash
git add booking/api.py && git commit -qm "stub(002-cancel): page rows and Cancel buttons, no behaviour"   # 頁面 stub 已在 Step 3 commit 過就跳過
git add .claude/skills/testplan/scripts/testplan_lint.py && git commit -qm "testplan_lint: --tests takes several paths (one feature's files)"
git add tests specs/002-cancel && git commit -qm "test(002-cancel): TP-011..021 red for the right reason"
git tag red-002-cancel-us1 && echo GREEN > .tdd/phase
```

### Step 5：GREEN（實際執行紀錄）

prompt 只講角色、範圍與指令，行為在測試與計畫裡：

```text
GREEN phase for specs/002-cancel. You are the implementer.
The failing tests are tests/unit/test_cancel.py, tests/api/test_cancel_api.py and tests/ui/test_cancel_page.py; specs/002-cancel/testplan.md says what each one proves. Make the whole suite pass by changing source files under booking/ only.
Run tests with: /tmp/lab/aibdd/.venv/bin/python -m pytest -q
Reply with the files you changed and why, then the last line of the test output.
```

```sh
LAB=/tmp/lab/aibdd/practice-d-cancel && cd "$LAB/booking"   # 上面的 prompt 存在 $LAB/runs/green-prompt.txt
claude -p "$(cat "$LAB/runs/green-prompt.txt")" \
  --setting-sources project,local --no-session-persistence --strict-mcp-config \
  --tools "Read,Write,Edit,Glob,Grep,Bash" \
  --allowedTools "Read,Write,Edit,Glob,Grep,Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)" \
  --permission-prompts none --max-turns 20 --max-budget-usd 3 \
  --output-format stream-json --verbose --include-hook-events \
  < /dev/null > "$LAB/runs/green.jsonl" 2> "$LAB/runs/green.err"
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，claude-opus-5-5）。

result 是 `success`、49 秒、US$0.287、3 次權限拒絕、`num_turns` 27（26 次工具呼叫加 1：10 Read、9 Edit、7 Bash）。stderr 只有一行 `Ignoring 1 permissions.allow entry from .claude/settings.json: this workspace has not been trusted. …`（後半教你怎麼信任這個資料夾）：專案的 allow 規則沒生效，hook 照跑。用 Ch 25 那支從 stream-json 抽時間線的小腳本整理：

```
…（略：Read SKILL.md 與九個檔案、一個列目錄的 Bash）
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q 2>&1 | tail -25; git tag
  pytest 11 failed, 10 passed in 5.20s
…（略：一個寫 /tmp 暫存檔的 Bash 被拒；Edit schedule.py 三次、api.py 六次，guard 每次都沒有輸出）
tool Bash /tmp/lab/aibdd/.venv/bin/python -m pytest -q --junitxml=/tmp/lab/aibdd/green-002.xml 2>&1 | 
  pytest 21 passed in 1.13s
…（略：red_gate.py 與 tamper_check.py 兩個 Bash 被拒，再一個 git status）
  hook Stop exit=0 {"systemMessage": "require_green: GREEN ok (21 passed in 1.11s)"}
```

它先看紅燈，從沒碰測試；VERIFY 的兩支腳本被權限擋下，因為我們刻意沒放行（Ch 25 第二次實跑放行了，卻因為 agent 改用絕對路徑而沒對上），VERIFY 本來就該由人跑。回覆裡兩句值得記下：「Ids now come from a `next_id` counter instead of `len(bookings) + 1`, which would have reused an id after a cancellation (FR-004, TP-015, TP-019).」與「The page also reloads the list after pressing Book. No test requires this, but FR-006 says the page must list current bookings」。頁面那邊，它新增 `cancelBooking(b)`：送 ``fetch(`/bookings/${b.id}?by=${encodeURIComponent(value("Name"))}`, {method: "DELETE"})``，`response.ok` 時狀態列寫 `` `Cancelled ${b.room} ${b.start}-${b.end}` ``、否則寫 `` `Rejected: ${(await response.json()).detail}` ``，最後 `await refresh()`；每個 Cancel 掛上 `cancel.onclick = () => cancelBooking(b)`，Book 的 handler 結尾也加了 `await refresh()`。其餘改動（`git diff -U0` 節錄，略去頁面 script、兩個 docstring 與 import）：

```diff
--- a/booking/api.py
+++ b/booking/api.py
@@ -67,0 +76,9 @@ def create_app():
+    @app.delete("/bookings/{booking_id}", status_code=204)
+    def cancel_booking(booking_id: int, by: str):
+        try:
+            schedule.cancel(booking_id, by)
+        except Forbidden as e:
+            raise HTTPException(403, str(e))
+        except NotFound as e:
+            raise HTTPException(404, str(e))
+
--- a/booking/schedule.py
+++ b/booking/schedule.py
@@ -19,0 +20 @@ class Schedule:
+        self.next_id = 1  # a counter, not len(bookings): ids are never reused (FR-004)
@@ -26 +27,2 @@ class Schedule:
-        booking = Booking(len(self.bookings) + 1, room, start, end, by)
+        booking = Booking(self.next_id, room, start, end, by)
+        self.next_id += 1
@@ -29,2 +31,8 @@ class Schedule:
-    def cancel(self, booking_id, by):  # 002-cancel interface stub: no behaviour yet
-        return None
+    def cancel(self, booking_id, by):
+        for held in self.bookings:
+            if held.id == booking_id:
+                if held.by != by:
+                    raise Forbidden(f"only {held.by} can cancel this booking")
+                self.bookings.remove(held)
+                return
+        raise NotFound(f"booking {booking_id} not found")
```

### Step 6：VERIFY 與 review

VERIFY 由我們自己跑，另成一個 commit：

```
$ python -m pytest -q | tail -1
21 passed in 1.19s
$ python -m pytest -q tests/*/test_cancel*.py --junitxml=.tdd/green.xml | tail -1
11 passed in 0.75s
$ python .claude/skills/tdd/scripts/red_gate.py .tdd/green.xml --plan specs/002-cancel/testplan.md --expect green | tee specs/002-cancel/verify.txt
@sc_cancel_01 met: TP-011 TP-016 TP-020
…（略：@sc_cancel_02、03 與 FR-001～003 met）
FR-004 met: TP-015 TP-019
…（略：FR-005、FR-006、R1、R4 met）
11 tests, 11 green as planned, 0 problems
$ python .claude/skills/tdd/scripts/tamper_check.py --base red-002-cancel-us1 | tee -a specs/002-cancel/verify.txt
10 files, 0 problems
```

001 用自己的計畫驗：只跑它的四個測試檔，`--expect green` 是 `10 tests, 10 green as planned, 0 problems`；全套報告配 002 的計畫則是 `21 tests, 11 green as planned, 10 problems`，十行 RG-4 又回來了。

**review：每一案抓得到什麼。** 綠燈只證明合約被滿足。我們寫了 `kill_matrix.py`（61 行，放在 lab 根目錄，不進 repo；和 Ch 25 動手練習 3 的 `kill_matrix.sh` 同名不同物，那支換掉整份 `schedule.py`）：把專案複製到暫存目錄（略過 `.git`、`__pycache__`、`.tdd`），一次植入一個 bug，只跑 `tests/*/test_cancel*.py`，從失敗測試的 `test_tpNNN_` 名字回推案例；替換文字找不到或不只一處、或沒變異的副本已經紅，exit 2；有 mutant 活下來，exit 1。原始碼沒有收進來（Ch 32 的 `mutate.py` 才是本課正式的 mutation 工具），14 個 bug 都是對 GREEN 程式的一處文字替換：`schedule.py` 裡把 `self.bookings.remove(held)` 換成 `pass`（removes-nothing）或 `self.bookings.pop()`（removes-newest）；`if held.by != by:` 換成 `if False:`（anyone-can-cancel）；訊息用 `{by}` 取代 `{held.by}`（names-the-caller）；`raise NotFound(...)` 換成 `return`（unknown-is-silent），或只在 `booking_id >= self.next_id` 時才丟（repeat-cancel-is-ok）；`Booking(self.next_id,` 換成 `len(self.bookings) + 1`（ids-from-length）或 `max((b.id for b in self.bookings), default=0) + 1`（ids-from-max）。`api.py` 裡把 `status_code=204` 換成 200、403 與 404 的對應互換、Cancel 的 `response.ok ?` 換成 `true ?`（page-ignores-errors），以及刪掉 Book 或 Cancel 之後的那行 `await refresh();`（book-skips-refresh、page-keeps-the-row）。

```
$ python ../kill_matrix.py .; echo "exit=$?"
…（略：removes-nothing，被六案抓到）
removes-newest       killed by TP-011
…（略：anyone-can-cancel、names-the-caller、unknown-is-silent，各被三案抓到）
repeat-cancel-is-ok  killed by TP-014
ids-from-length      killed by TP-015 TP-019
ids-from-max         killed by TP-015 TP-019
delete-answers-200   killed by TP-016
403-sent-as-404      killed by TP-017
404-sent-as-403      killed by TP-018
page-ignores-errors  killed by TP-021
book-skips-refresh   SURVIVES
page-keeps-the-row   killed by TP-020
14 mutants, 13 killed, 1 survived
exit=1
```

- **七案各有一個只有它抓得到的 bug**（TP-011、014、016、017、018、020、021）。TP-012、013、015、019 沒有，它們抓的 bug 另一層也抓得到。照 Ch 24「沒殺掉列出的 bug，不等於該刪」：前三個是規則在內圈的家，壞掉時 0.0x 秒就指向 `schedule.py`；TP-019 是 spec 自己的情境 `@sc_cancel_03`。
- **review 改 TP-015 值不值得？** 把 v1 寫法放回一份副本跑 `--only ids-from-length --only ids-from-max`，得到 `ids-from-length killed by TP-015 TP-019` 與 `ids-from-max killed by TP-019`。v1 擋不住「最大 id 加一」，這次沒出事只因為 TP-019 取消的剛好是唯一、也就是最新的一筆。那是巧合，不是設計。
- **活下來的 `book-skips-refresh` 是 agent 自己加的那一行**。把它刪掉，全套照樣 `21 passed in 1.32s`：合約以外的行為，綠燈不會替你守。

最後審權限。agent 用那條看似很窄的 `Bash(/tmp/lab/aibdd/.venv/bin/python -m pytest *)`，把 `--junitxml=/tmp/lab/aibdd/green-002.xml` 寫到了 repo 外面。官方權限文件說 `*`「matches any text, including spaces」，它的表格裡 `Bash(git log * main)` 就放行 `git log --output=<file> main`。pytest 的同類旗標更危險，`--help` 寫著 `--basetemp=dir` 會移除已存在的目錄。在 scratch 目錄實測，有測試用到 `tmp_path` 時：

```
$ mkdir -p bt victim && echo "keep me" > victim/notes.md && printf 'def test_uses_tmp(tmp_path):\n    assert tmp_path.exists()\n' > bt/test_tmp.py && python -m pytest -q bt/test_tmp.py --basetemp=victim -p no:cacheprovider 2>&1 | tail -1; find victim | head; cat victim/notes.md
1 passed in 0.00s
…（略：find 只列出 victim 與 pytest 建的兩個子目錄）
cat: victim/notes.md: No such file or directory
```

本練習的測試沒有用 `tmp_path`，同一個旗標什麼都沒刪；Ch 22 的外圈測試有用。要收緊，就放行不帶 `*` 的完整指令、改用一支不轉傳任意旗標的包裝腳本（Ch 23 的 `run_ui_tests.sh` 會轉傳 `"$@"`，一樣要改），或照文件建議用 sandbox 管檔案系統。

**解答說明**：

- **api 層為什麼用 TestClient，不用 Ch 22 的子 process server？** 取消沒有新的啟動設定，也沒有持久化，Ch 22 對比表裡 TestClient 那一列「測不到」的事（啟動指令與它帶的環境變數、真的重啟、process 邊界）都不涉及。UI 層的 thread uvicorn 加真瀏覽器，才是這個功能的 E2E。
- **為什麼一次紅 11 個，不照 Beck 一次一個？** [〈Canon TDD〉](https://newsletter.kentbeck.com/p/canon-tdd)擔心第一個測試變綠時推翻一個決定，預先寫好的測試全要重工。這次的重工（TP-018、TP-019 兩次、頁面 stub）都發生在 RED、起因是閘門，改的都是測試與 stub，沒有一行實作要重寫；介面決定事先寫進 stub，GREEN 之後沒有測試要改。介面沒定時，回到一次一個。
- **第二次取消為什麼是 404 而不是 410？** RFC 9110 說伺服器知道資源是永久消失時，410 優於 404。我們的計數器知道 `id < next_id` 的編號發出過，回 410 做得到；spec 的 Edge Cases 卻寫「answered like an id that never existed」，沒寫理由，[Ch 29](./29-example-mapping.md) 的 Example Mapping 也把「第二次回 404」記成 PM 與 RD 的決定（Q2）。要不要讓 client 分得出「取消過」與「從沒有過」是產品決定，測試只負責釘住它。
- **閘門與 trace 為什麼改成「只看這個功能的檔案」，而不是合併兩份計畫？** 我們把兩份計畫的案例接成一個檔案試過：在 red tag 上是 `21 tests, 11 red as planned, 10 problems`，001 已經綠的 10 個測試全成了 RG-2「passes before the code exists」；GREEN 之後印出 `FR-001 met: TP-001 TP-011`，把兩份 spec 各自的 FR-001（訂房、取消）算成同一條。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| Bob 的 1 號：`DELETE /bookings/1?by=Bob` | `204`，空 body；`GET /bookings` 是 `[]` | TP-016 |
| 同一筆再取消一次 | `404 {"detail":"booking 1 not found"}` | TP-014；冪等看效果，不看回應 |
| `DELETE /bookings/1?by=Alice`、`?by=bob` | 兩個都是 `403 {"detail":"only Bob can cancel this booking"}` | TP-017；名字逐字比對 |
| Alice 的 client 送 `?by=Bob` | `204`，清單變成 `[]` | OOS-3：`by` 不是身分 |
| `DELETE /bookings/7?by=Bob`、`/bookings/0?by=Bob` | `404 booking 7 not found`、`404 booking 0 not found` | TP-018 |
| `DELETE /bookings/1`（沒有 `by`）、`/bookings/abc?by=Bob` | `422`，`"msg":"Field required"`；`422`，`int_parsing` | FastAPI 的參數驗證，不在計畫裡 |
| 取消後 Alice 訂 Taipei 10:00–11:00 | `201`，`"id":2` | TP-019；id 不重用 |
| 頁面 Name 填 Bob，按 `booking-1` 的 Cancel | `Cancelled Taipei 10:00-11:00`，那一列消失 | TP-020 |
| 頁面 Name 填 Alice | `Rejected: only Bob can cancel this booking`，那一列還在 | TP-021 |
| 頁面沒有 stub 時的 UI 紅燈 | `RG-2 ... TimeoutError: Locator.click: Timeout 30000ms exceeded.` | 動作逾時預設 30 秒 |

## 延伸挑戰（加分）

- **釘住活下來的那一行**：寫 TP-022「The page lists a booking made with the form」（ui、P2、trace FR-006），斷言 `get_by_test_id("booking-1")` 含 `Taipei 10:00-11:00 Alice`。我們在一份 clone 裡試過：lint 是 `12 cases, 12 tests, 0 problems`，`kill_matrix.py --only book-skips-refresh` 變成 `killed by TP-022`；但對已經實作好的程式，RED 閘門把**每一個**測試都判成先綠（`12 tests, 0 red as planned, 12 problems`）。替已經存在的行為補測試，紅燈證明不了什麼，mutant 證明得了；想一想這在 [Ch 32](./32-brownfield-bdd.md) 的既有專案裡會變成什麼規模。
- **走完整的 Spec Kit 順序**：用 Ch 25 的 `booking-tdd` preset 跑 `/speckit-plan` 與 `/speckit-tasks`，比較它排出的任務和你這次省略的步驟。
- **讓閘門認得多份計畫**：替 `red_gate.py` 加一個旗標，宣告「這些 TP 屬於別的功能，不算 RG-4，但必須綠」。用第一次那份報告證明十行 RG-4 消失、TP-018 的 RG-2 還在。

## 自我檢核

- [ ] 不看解答，說出第一次閘門的四類問題各自的成因與修法，並指出哪幾類是 Ch 25 沒料到的
- [ ] 為什麼 TP-019 在 unit 層紅得不對、在 api 層紅得對？「看得到結果的最低一層」該怎麼補充？
- [ ] v1 的 TP-015 漏掉哪一種 id 算法？這次為什麼沒出事，你怎麼用 mutant 證明那是巧合？
- [ ] 兩個功能共用一套測試之後，閘門、trace、VERIFY 各要怎麼限定範圍？001 沒退步靠哪一次執行證明？
- [ ] 全部綠燈、`tamper_check` 0 problems 之後，你用什麼方法找到沒有任何測試釘住的行為？`Bash(... -m pytest *)` 又實際放行了什麼？

## 延伸閱讀

- **[RFC 9110 — HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)**（R. Fielding、M. Nottingham、J. Reschke，IETF，2022 年 6 月）
  - **讀哪裡**：9.2.2「Idempotent Methods」、9.3.5「DELETE」（成功時 202、204、200 各在什麼情況）、15.5.4「403 Forbidden」最後一段（想隱藏資源存在時改回 404）與 15.5.5「404 Not Found」（永久消失時 410 優於 404）
  - **學到什麼**：冪等說的是多次相同請求「對伺服器的預期效果」與一次相同，不保證回應相同；DELETE 成功時三種狀態碼的分工；403、404、410 各自回答什麼問題
  - **和本練習的關聯**：FR-005 的 204、TP-014 的「第二次取消回 404」與解答說明的 410，都在這幾節裡有依據；前提是分得清 HTTP 方法與狀態碼的類別
- **[Playwright for Python — Locators](https://playwright.dev/python/docs/locators)**（Microsoft，官方文件）
  - **讀哪裡**：「Matching inside a locator」「Locate by test id」與「Lists」
  - **學到什麼**：在一個 locator 底下串接 `get_by_role` 縮小範圍；test id 的取捨（文件說它「not user facing」，建議優先用 `get_by_role()` 這類面向使用者的屬性）；在清單裡挑出一筆的幾種寫法
  - **和本練習的關聯**：`get_by_test_id("booking-1").get_by_role("button", name="Cancel")` 的寫法出自第一節；清單很長時，「Lists」的 `filter(has_text=...)` 是不靠 test id 的另一條路。先讀過 [Ch 23](./23-frontend-e2e-playwright.md) 的定位器表
- **[Claude Code 文件 — Configure permissions](https://code.claude.com/docs/en/permissions)**
  - **讀哪裡**：「Wildcard patterns」的對照表與「What a Bash rule doesn't match」
  - **學到什麼**：Bash 規則的 `*` 怎麼比對（連空格一起）、規則比對的是指令文字而不是程式本身，以及哪些限制要交給 sandbox
  - **和本練習的關聯**：那條 pytest 規則為什麼放行了 `--junitxml=<任何路徑>`；文件明說 Bash 規則比對的是 Claude 寫出的指令文字，deny 規則因此「isn't a security boundary around the program」，allow 規則的 `*` 也會放行你沒想到的旗標。pytest 對 `--basetemp` 的警告在 [How to use temporary directories and files in tests](https://docs.pytest.org/en/stable/how-to/tmp_path.html) 的「Temporary directory location and retention」：「will be cleared blindly before each test run」；pytest 9.2.0 的 `TempPathFactory.getbasetemp()` 在第一次有測試要暫存目錄時才清，所以沒用 `tmp_path` 的那次什麼都沒刪

到這裡，測試計畫、三層紅燈、閘門、VERIFY 與 mutant 都是 RD 寫給 RD 與 agent 看的。PM 讀得懂 spec 的三個情境，卻沒辦法從 `test_tp015_...` 判斷它們是否忠於那三句話。下一章換一個角度：同一組規則寫成 BDD 的產物，多出來的是什麼。

→ [Ch 26 BDD 到底在做什麼：從產物看 TDD 與 BDD 的差別](./26-bdd-vs-tdd-artifacts.md)
