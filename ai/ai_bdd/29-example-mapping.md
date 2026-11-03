# Ch 29 — Example Mapping 與 Three Amigos：Journey 型與 Rule 型情境

> **目標**：用 Three Amigos 的三種視角排一場「取消預約」的 Example Mapping，拿 [Ch 8](./08-translation-sandwich.md) 的 `example_map.py` 從 `NOT READY` 排到 `READY`、產生 rule 型情境；分清 rule 型（一個例子說明一條規則）與 journey 型（一個目標走過幾條規則）各證明什麼、什麼時候一個情境可以串好幾組 When/Then，並把 journey 寫成新規則 GH-13 讓 lint 管得到；看一條 journey 抓到 rule 型情境全綠時漏掉的 bug，再請 AI 當 tester amigo 實跑一次，看它能不能在寫程式之前找到同一個洞；最後把 AI x BDD 的導入流程（誰起草、誰審、誰簽、哪一行指令把關）在這張 story 上從頭跑到尾，並打包成 `example-mapping` skill。

> **環境**：macOS 26.6 arm64、Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（gherkin-official 42.0.1）、FastAPI 0.162.0、PyYAML 6.0.3、Claude Code 2.1.295（claude-opus-5-5，兩次 `claude -p`）。lab 是 `/tmp/lab/aibdd/ch29-example-mapping/`，從空目錄開始；輸出皆為 2026-11-02 實跑，`./rerun_chapter.sh` 依本章順序重跑全部輸出（兩次 agent 執行不重跑，只重驗它們留下的檔案）。

## 為什麼需要這個？

[Ch 8](./08-translation-sandwich.md) 用一張例會 story 證明需求會在 Sales、PM、RD 之間掉東西，給了 Example Mapping 的桌面與 `example_map.py`；[Ch 28](./28-gherkin-dsl-style.md) 把「一個例子怎麼寫」收成 GH-01..GH-12 與 lint。還有兩件事沒人回答。第一，桌子旁邊該坐誰、各自該問什麼。第二，有些情境天生不只一個動作：取消預約的目的是「好讓別人能用這間會議室」，要證明它，得先有人被拒、再有人取消、最後被拒的人訂到，三個 When；GH-04 說一個情境只能有一個 When。repo 裡已經有這種情境，只是沒人點破。SDD [Ch 21](../spec_driven_development/21-event-storming.md) 的「從工作坊產物到規格素材」把 Event Storming 的時間軸翻成「成功下訂並付款」與「付款失敗後庫存恢復」，各有兩組 When/Then；把那段 Gherkin 存成檔案丟給 Ch 28 的 lint，兩個情境都得到 `GH-04 2 When steps; keep one action, earlier ones become Given facts`。SDD [Ch 10](../spec_driven_development/10-acceptance-criteria-bdd.md) 的「Specification by Example（實例化需求）」只有一段概念；[pstack Ch 21](../pstack/21-verification-skills.md) 的 feature map 要求「Never weaken the journey to make it pass」，沒說什麼時候該用 journey。導入流程也一樣：SDD [Ch 22](../spec_driven_development/22-two-meanings-of-spec-driven.md) 的「ATDD-SDD 混合」只是一張四個節點的流程圖；SDD [Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的 Spec Review 審的是 spec.md 裡的 EARS 驗收條件，不是 Gherkin。agent 一分半鐘就能起草一份 feature 檔（Ch 28 的三次 eval 都在 86 秒內），誰起草、誰簽核，沒有一處寫清楚。

這些做法有它的歷史。Ron Jeffries 的 3C（SDD [Ch 9](../spec_driven_development/09-user-stories-invest.md)）說需求住在對話裡，沒說對話裡要有誰；George Dinwiddie 2009 年在部落格文章〈If you don't automate acceptance tests?〉把業務、開發、測試三方的對話叫做 three amigos，2011 年在 *Better Software* 寫成專文；Gojko Adzic 同年出版《Specification by Example》，從約五十個專案歸納出七個流程模式；Matt Wynne 2015 年給了 Example Mapping；Gherkin 6（2018 年底）加上 `Rule` 關鍵字，「這個例子屬於哪條規則」從此寫得進 feature 檔。Journey 型更早就有，Cockburn 的 use case 主成功情境（SDD [Ch 12](../spec_driven_development/12-use-cases-nfr.md)）就是一條路徑。本章要做的，是把「rule 型還是 journey 型」變成一個有理由、有 lint 擋的選擇。

## 先建立直覺

想像驗屋。驗屋師拿清單逐項驗：每個插座有沒有電、每個排水孔會不會積水，哪一項不過，報告寫得清清楚楚。這是 rule 型：一條規則、一個例子、一個結果。屋主搬進去住一天，早上洗澡、煮早餐、出門鎖門，驗的是這些東西接在一起能不能過日子：熱水器單獨沒問題、廚房單獨沒問題，兩邊同時用熱水卻不夠。這是 journey 型。驗屋之前，買方、建商、驗屋師坐下來把「怎樣算合格」列成清單，答不出來的寫在紅卡上，那是 Three Amigos 加 Example Mapping。類比有兩處不成立：住一天驗不到每個插座，所以 journey 取代不了清單；驗屋只做一次，feature 檔每次改動都要重跑。

```
 story 卡（黃）：身為預約人，我可以取消自己的預約，好讓別人能用這間會議室
      │ 規則（藍）＋例子（綠）＋問題（紅）          │ "so that"：使用者要達成的目標
      ▼                                           ▼
 maps/cancel.v3.yaml ─ example_map.py gherkin ─▶ features/cancellation.feature   rule 型：一例一規則、一個 When
                                               features/journeys/*.feature      journey 型：一個目標、跨規則、When/Then 交替
      └────────────── 兩種都走同一組 step definitions 與 DSL（Ch 28）──────────────▶ app.py
```

> 四色卡片、25 分鐘 timebox 與 `example_map.py` 的 YAML 格式在 [Ch 8](./08-translation-sandwich.md) 的第三、四節；GH-01..GH-12 與 DSL 在 [Ch 28](./28-gherkin-dsl-style.md)。

## 一、Three Amigos：三種視角，不是三個人

Agile Alliance 的詞彙表把 three amigos 定義成檢視一個工作增量的三種主要視角，各問一個問題：業務問「What problem are we trying to solve?」，開發問「How might we build a solution to solve that problem?」，測試問「What about this, what could possibly happen?」。重點是視角，不是人數。Wynne 的原話是「The bare minimum is your three amigos: a developer, a tester and a product person」：最少三種，多了無妨，Ch 8 就多請了手上有客戶原信的 Sales。詞彙表說這三種視角在開發前、中、後都要檢視；Example Mapping 這一場排在 story 進開發之前，時間用 Ch 8 的 25 分鐘。

| 視角 | 問什麼 | 最常產出的卡 | 本章的例子 |
|---|---|---|---|
| 業務（PM） | 這張 story 要解決誰的什麼問題？ | 黃卡的 "so that"、藍卡、紅卡的答案 | 「好讓別人能用這間會議室」、R4 |
| 開發（RD） | 怎麼做？會碰到哪條既有規則？ | 藍卡、技術面的紅卡 | Q2：同一筆按兩次取消要回什麼 |
| 測試（QA） | 那如果……呢？ | 綠卡（邊界、反例）、紅卡 | 取消之後，別人訂得到嗎？ |

本課把 AI 當第四個參與者，問「文件之間有什麼矛盾、哪個例子沒人寫」，只起草卡片、只提問，紅卡的答案由 owner 填（Ch 8 踩雷 3）。三種視角裡最常被省掉的是測試：PM 和 RD 都在想「做什麼」，只有 tester 的工作是想「哪裡會壞」。本課把 tester 找漏網例子的方法寫成四個方向：**邊界**（11:00 接在 10:00–11:00 後面）、**換一個人**（Alice 而不是 Bob）、**同一件事做兩次**、**然後呢**（這件事發生之後，下一個人遇到什麼）。這份清單是我們從 Ch 8、Ch 28 的例子歸納的，不是哪本書的原文；第六節把它寫進 skill。

## 二、一場取消預約的 Example Mapping

從空目錄開始，沿用 Ch 28 的專案與 Ch 8 的腳本（`C08`、`C28` 指向你在 Ch 8、Ch 28 建好的目錄；實跑時兩個變數指向依兩章定稿重建的目錄）：

```bash
C08=/tmp/lab/aibdd/ch08-sandwich; C28=/tmp/lab/aibdd/ch28-gherkin-style
mkdir -p /tmp/lab/aibdd/ch29-example-mapping && cd /tmp/lab/aibdd/ch29-example-mapping
ln -s /tmp/lab/aibdd/.venv .venv && source .venv/bin/activate
mkdir -p .claude/skills/example-mapping/scripts maps sources features/journeys bad/journeys reports
cp -R $C28/.claude/skills/gherkin-style .claude/skills/ && cp -R $C28/tests $C28/glossary.yaml $C28/pytest.ini .
cp $C28/features/booking.feature features/ && cp $C08/tools/example_map.py .claude/skills/example-mapping/scripts/
```

`example_map.py` 放進 skill 的 `scripts/`，和 Ch 28 把 lint 放進 `gherkin-style` 同一個理由：腳本跟著用它的 skill 走。PM 帶來的 story 卡 `sources/cancel-story.md`：

```markdown
# Story card: cancel a booking (PM, before the session)

As someone who booked a room, I can cancel my booking,
so that someone else can use the room.

Notes from the PM:
- Today people who no longer need a room just don't show up; the room sits empty while others were turned away.
- Only the person who booked should be able to cancel it.
- Bookings are for one day, HH:MM, half-open [start, end), as in the rest of the product.
```

第一場會只有 PM 和 RD，tester 請假。25 分鐘後的桌面 `maps/cancel.v1.yaml`：

```yaml
# maps/cancel.v1.yaml: the first session, PM and RD only (no tester), 25 minutes
story: 取消預約（身為預約人，我可以取消自己的預約，好讓別人能用這間會議室）
source: sources/cancel-story.md（PM 的 story 卡與筆記）
rules:
  - id: R4
    text: 只有預約的人能取消這筆預約
    examples:
      - id: sc_cancel_01
        title: The one where Bob cancels his own booking
        given: Bob has booked "Taipei" from 10:00 to 11:00
        when: Bob cancels the booking of "Taipei" from 10:00 to 11:00
        then: the booking is cancelled
      - id: sc_cancel_02
        title: The one where Alice tries to cancel Bob's booking
        given: Bob has booked "Taipei" from 10:00 to 11:00
        when: Alice cancels the booking of "Taipei" from 10:00 to 11:00
        then: the cancellation is rejected because only Bob can cancel this booking
questions:
  - id: Q1
    text: 秘書能不能替主管取消？
    owner: PM
    blocks: [R4]
    answer:
  - id: Q2
    text: 同一筆預約按了兩次取消（開了兩個分頁），第二次要看到什麼？
    owner: PM
    blocks: [R4]
    answer:
  - id: Q3
    text: 已經開始的預約還能取消嗎？
    owner: PM
    blocks: [R4]
    answer:
```

```
$ python .claude/skills/example-mapping/scripts/example_map.py check maps/cancel.v1.yaml
maps/cancel.v1.yaml: 1 rules, 2 examples, 3 questions (3 open) -> NOT READY
  problem: question Q1 is open, owner PM (blocks R4): 秘書能不能替主管取消？
…（略：Q2、Q3 兩行，同一個格式）
1 maps, 3 problems
```

這場會順手定了 Ch 28 留下的 R4 句型（Ch 28 的 eval 裡，取消出現過兩種說法）：`{who} cancels the booking of "{room}" from {start} to {end}`，用會議室加時段指出是哪一筆，不寫流水號；成功是 `the booking is cancelled`，被拒是 `the cancellation is rejected because {reason}`。詞彙也是會議的產物，`glossary.yaml` 的 `terms` 加一個詞：

```yaml
  cancel:
    zh: 取消（動作）
    definition: Give a booking back. Only the person who booked can do it; the room is free again.
    avoid: [delete, deletes, deleted, remove, removes, removed, withdraw, withdraws]
```

會後 owner 把答案寫回，存成 `maps/cancel.v2.yaml`（v1 只改了三個 `answer`）：Q1 `不在這張 story；代理取消另開一張（PM，2026-11-02）`；Q2 `第二次回 404「booking N not found」；這是 API 合約，寫進 API 層的測試計畫，不寫成情境（PM、RD）`；Q3 `現在的預約沒有日期，也沒有「現在幾點」；等加上日期的 story 再談（PM）`。Q2 值得多看一眼：不是每個答案都要變成情境。狀態碼與 id 是 API 合約，該進 [Ch 24](./24-testplan-skill.md) 的測試計畫；硬寫成 Gherkin，step 裡就會出現 `booking 7` 這種流水號與 404 這種狀態碼；後者 GH-03 會叫，前者只有 review 擋得住（Ch 28 的 eval 也看過 `booking 1`）。`example_map.py check maps/cancel.v2.yaml` 回 `1 rules, 2 examples, 3 questions (0 open) -> READY`；`example_map.py gherkin maps/cancel.v2.yaml > features/cancellation.feature` 結束碼 0，產生的檔案和 Ch 8 同一個格式：`Feature:` 是 story、`Source:` 是原話的出處，`Rule: R4 只有預約的人能取消這筆預約` 底下兩個 `Example:`，帶著 `@sc_cancel_01`、`@sc_cancel_02`，steps 照抄 map。

被測系統 `app.py` 是 Ch 0 雛形加上 id 與 R4（介面照全課約定：`DELETE /bookings/{id}?by=...` 回 204、403、404）。RD 決定保留取消紀錄：取消只把 `cancelled` 設成 true，`active()` 篩掉已取消的，列表、取消、重疊檢查都看 `active()`。這個設計決定第五節會用到。

```python
"""app.py: the booking API of Ch 0 plus R4 (cancel). Cancelled bookings stay in BOOKINGS for the history."""
from datetime import time
from itertools import count
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
BOOKINGS: list[dict] = []
ids = count(1)

class Booking(BaseModel):
    room: str
    start: time
    end: time
    by: str

def active():
    return [x for x in BOOKINGS if not x["cancelled"]]

@app.post("/bookings", status_code=201)
def create(b: Booking):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in active():
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    BOOKINGS.append(booking := {"id": next(ids), **b.model_dump(), "cancelled": False})
    return booking

@app.get("/bookings")
def list_bookings():
    return active()

@app.delete("/bookings/{booking_id}", status_code=204)
def cancel(booking_id: int, by: str):
    x = next((x for x in active() if x["id"] == booking_id), None)
    if x is None:
        raise HTTPException(404, f"booking {booking_id} not found")
    if x["by"] != by:
        raise HTTPException(403, f"only {x['by']} can cancel this booking")
    x["cancelled"] = True

@app.post("/reset")
def reset():
    global ids
    BOOKINGS.clear()
    ids = count(1)
```

DSL（`tests/dsl.py`）在 `bookings_of` 之後加兩個方法；step definitions（`tests/test_booking.py`）把綁定改成 `scenarios("../features")`，檔尾加四支（下面同一個區塊，前半是 DSL）。pytest-bdd 9.0.1 的 `feature.py` 第 74 行用 `glob.iglob(os.path.join(path, "**", "*.feature"), recursive=True)` 找檔，所以子目錄 `features/journeys/` 也會被綁進來。

```python
    # tests/dsl.py，class Booking 裡、bookings_of 之後
    def cancel(self, who, room, start, end) -> Outcome:
        # R1: active bookings of a room never overlap, so room + exact interval names at most one
        ids = [b["id"] for b in self.bookings_of(room) if (b["start"][:5], b["end"][:5]) == (start, end)]
        assert ids, f"no booking of {room} from {start} to {end} to cancel"
        r = self.http.delete(f"/bookings/{ids[0]}", params={"by": who})
        return Outcome(True) if r.status_code == 204 else Outcome(False, r.json()["detail"])

    def has_cancelled(self, who, room, start, end):  # the fact includes the booking it cancelled
        self.has_booked(who, room, start, end)
        assert self.cancel(who, room, start, end).confirmed, f"setup: {who} could not cancel {room}"

# tests/test_booking.py，接在檔尾
@given(parsers.parse('{who} has cancelled the booking of "{room}" from {start} to {end}'))
def has_cancelled(booking, who, room, start, end):
    booking.has_cancelled(who, room, start, end)

@when(parsers.parse('{who} cancels the booking of "{room}" from {start} to {end}'), target_fixture="outcome")
def cancels(booking, who, room, start, end):
    return booking.cancel(who, room, start, end)

@then("the booking is cancelled")
def cancelled(outcome):
    assert outcome == Outcome(True)

@then(parsers.parse("the cancellation is rejected because {reason}"))
def cancellation_rejected(outcome, reason):
    assert outcome == Outcome(False, reason)
```

`has_cancelled` 先訂再取消，因為 `Bob has cancelled the booking of ...` 這個事實本身就包含「Bob 訂過」（GH-01：Given 陳述事實）；另一種寫法是先寫一行 `Bob has booked ...` 再寫取消，但第一行只是在重述第二行已經包含的前提。`pytest.ini` 的 `markers` 加上 `sc_cancel_01`..`sc_cancel_04`、`sc_journey_01` 與 `journey` 六行（每行一個名字加說明，和 Ch 28 一樣）。

## 三、Specification by Example 的七個流程模式

Adzic 書中第 2 章「Key process patterns」的七個模式，可以當成檢查整條流程有沒有缺環的清單。左欄是書中的章節名；右兩欄是本課的對應，是我們的讀法：

| 流程模式 | 在做什麼 | 本課的落點 |
|---|---|---|
| Deriving scope from goals | 從業務目標推出範圍，不從使用者指定的解法出發 | 黃卡的 "so that"；journey 從這裡來（第四節） |
| Specifying collaboratively | 規格由不同視角一起寫 | Three Amigos 與 Example Mapping（本章、Ch 8） |
| Illustrating using examples | 用具體例子說明規則 | 綠卡；`example_map.py check` 要求每條規則至少一個例子 |
| Refining the specification | 把例子精煉成關鍵例子，去掉雜訊 | GH-01..GH-13 與 `gherkin_lint.py`（Ch 28、本章） |
| Automating validation without changing specifications | 自動化時不改寫規格的文字 | step definitions 加 DSL（Ch 28）；簽核 hash 鎖住 feature（Ch 27） |
| Validating frequently | 經常對系統驗證 | CI 跑 lint 與 `bdd_check.py green`（第七節） |
| Evolving a documentation system | 讓規格長成活文件 | `living_doc.py`（[Ch 26](./26-bdd-vs-tdd-artifacts.md)） |

書中第 7 章「Illustrating using examples」要求例子 precise、complete、realistic、easy to understand。對到本課：精確靠 GH-09 的參數慣例，真實靠 glossary 裡的真名，完整靠 tester 的四個方向，好懂靠 GH-05 的 3–5 步。表格也是一種說明方式（本課的補充，不是書中的小節）：一張綠卡可以是一張小表，表述時只有在每一列都是同一條規則的不同情況時，才寫成 Scenario Outline（GH-07），R1 的三種重疊形狀（Ch 28）就是一例。

## 四、Rule 型與 Journey 型

Seb Rose 在〈Gherkin Rules〉裡說：「Each example in an example map illustrates one (and only one) rule.」所以 Example Mapping 的產出天生是 **rule 型**：一個例子說明一條規則，最少的 Given、一個 When、使用者看得到的 Then，`example_map.py gherkin` 把它們放在 `Rule:` 底下。**journey 型**說明的不是規則，是 story 的目標：一條路徑走過幾條規則，後面的步驟吃前面步驟留下的狀態，When 與 Then 交替。John Ferguson Smart 與 Jan Molak 在《BDD in Action》第二版第 9.3 節把驗收測試分層，前兩個小節的標題是「The Business Rules layer describes the expected outcomes」與「The Business Flow layer describes the user's journey」，說的是同一組分工。什麼時候值得串好幾組 When/Then？三個條件同時成立：一條規則的輸出是另一條規則的輸入（取消之後，R1 要放行）；風險在交接（狀態由系統自己產生，不是 Given 佈置的）；PM 想讀一段完整的故事（活文件裡最先被讀的就是它）。不成立的情況：前面的 When 只是在佈置狀態，那就是 GH-04 說的「earlier ones become Given facts」；或者想測畫面操作，那是 [Ch 23](./23-frontend-e2e-playwright.md) 的 UI 測試。數量上，一張 story 一到兩條（本課的取捨，不是業界標準：journey 慢又跨規則，多了就成了一套沒人讀得完的回歸測試），放在 `features/journeys/`，用 `@journey` 標起來：目錄讓數量一眼可見，tag 讓 `pytest -m journey` 選得到。宣告式的要求不變：管句子怎麼寫的那幾條（GH-01..GH-03、GH-08、GH-09）一條都不放寬。`features/journeys/free_slot_journey.feature`：

```gherkin
Feature: Giving a room back
  Bob was turned away from a room that Alice no longer needs. Once she cancels,
  he can take it: the "so that" on the story card.

  @journey @sc_journey_01
  Scenario: A cancelled slot goes to the next person
    Crosses R1 and R4.
    Given Alice has booked "Taipei" from 10:00 to 11:00
    When Bob books "Taipei" from 10:30 to 11:30
    Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Alice
    When Alice cancels the booking of "Taipei" from 10:00 to 11:00
    Then the booking is cancelled
    When Bob books "Taipei" from 10:30 to 11:30
    Then the booking is confirmed
```

`Crosses R1 and R4.` 是 scenario 的描述文字（Cucumber Reference：描述可以放在 Scenario 底下，只要沒有一行以關鍵字開頭）。這時 skill 裡的 lint 還是 Ch 28 的原版，它對這條 journey 說：

```
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py features/
features/journeys/free_slot_journey.feature:6: GH-04 3 When steps; keep one action, earlier ones become Given facts
features/journeys/free_slot_journey.feature:6: GH-04 steps out of order; use Given -> When -> Then
features/journeys/free_slot_journey.feature:6: GH-05 7 steps (Background included); keep 3-5
features/journeys/free_slot_journey.feature:6: GH-11 no business rule; nest it under "Rule: R<n> ..." or start its name with R<n>
3 files, 4 problems
```

三條路。拆成三個 rule 型情境：每段單獨成立，交接沒人驗。壓成一個 When（`When Alice gives the slot to Bob`）：一行藏好幾個動作，GH-04 的 Edge 正是這種寫法，紅了也看不出是哪一段。第三條是新增一條規則，給 journey 自己的形狀。這不算「改 lint 讓發現消失」（Ch 28 的 skill 第 4 步禁止的事）：新規則有新編號、經 review 加進來；它只放寬帶 `@journey` 而且放在 `features/journeys/` 的情境，其他規則照樣適用；被濫用時它會反過來叫。`rules/gherkin.md` 加上：

```markdown
## GH-13 Journeys are tagged, placed, and still declarative
Trigger: a scenario seems to need a second `When` to show a story's goal (its "so that").
Rule: a scenario that walks one goal of a story (its "so that") through several rules is a journey. Tag it `@journey` plus an ID `@sc_journey_<NN>`, keep it in `features/journeys/`, name the rules it crosses in its description (`Crosses R1 and R4.`), and after the Givens alternate When and Then 2-4 times. GH-04, GH-05 and the `Rule:` part of GH-11 give way to this rule; every other GH rule still applies to each step.
Why: a rule example pins one rule with the least setup; a journey shows that the rules compose along the path a user takes. With a Then after every When, the first red step names the broken hand-off.
Sign you skipped it: more journeys than rules in a feature, or a red journey nobody can explain from its first failing step.
Enforced by: gherkin_lint.py (tag, place, description, shape); review (one or two journeys per story)
Good: `When Alice cancels the booking of "Taipei" from 10:00 to 11:00` / `Then the booking is cancelled` / `When Bob books "Taipei" from 10:30 to 11:30` / `Then the booking is confirmed`
Bad: a rule example tagged `@journey` to get around GH-04; a journey written as clicks; two Whens in a row.
Edge: 4 is our cap, not a law: one more than the longest journey we have (3 actions). A fifth action usually starts a second goal; split there, or raise the cap in review. Journeys are not on the map, so their IDs use their own area and never collide with `example_map.py` numbers.
```

lint 的改動存成 `gh13.diff`，`patch .claude/skills/gherkin-style/scripts/gherkin_lint.py < gh13.diff` 套上去（122 行變 135 行），`SKILL.md` 把 `GH-01..GH-12` 改成 `GH-01..GH-13`、第 3 步補一句 journey 放哪裡，並跟 [Ch 27](./27-ai-bdd-loop.md) 的 `bdd` skill 一樣把 `python` 改成 `.venv/bin/python`（[Ch 0](./00-environment-setup.md) 踩雷 2 與 Ch 27：沒 activate 的環境裡，`python3` 跑到系統的 3.9.6，`python` 在這台機器上根本找不到；agent 的 shell 不保證 activate 過）。形狀用一個正規表示式表達：把每一步換成 G、W、T，整串要符合 `G*(WT+){2,4}`。

```diff
--- gherkin_lint.py
+++ gherkin_lint.py
@@ -15,0 +16 @@
+JOURNEY = re.compile(r"G*(WT+){2,4}")  # GH-13: Givens, then 2-4 actions, each followed by what the user sees
@@ -52,0 +54,6 @@
+    def journey(sc, kinds, rule, line):
+        shape = "".join("GWT"[[GIVEN, WHEN, THEN].index(k)] for k in kinds)
+        if not JOURNEY.fullmatch(shape): bad(line, "GH-13", f"journey shape {shape}: Givens, then 2-4 When steps, each followed by a Then")
+        if rule: bad(line, "GH-13", "a journey crosses rules; move it out of Rule:")
+        if len(set(re.findall(r"\bR\d+\b", sc["description"]))) < 2:
+            bad(line, "GH-13", 'name the rules it crosses in its description, e.g. "Crosses R1 and R4."')
@@ -65,2 +72,9 @@
-        if kinds.count(WHEN) != 1: bad(line, "GH-04", f"{kinds.count(WHEN)} When steps; keep one action, earlier ones become Given facts")
-        if kinds != sorted(kinds, key=[GIVEN, WHEN, THEN].index): bad(line, "GH-04", "steps out of order; use Given -> When -> Then")
+        is_journey = "@journey" in [t["name"] for t in sc["tags"]]
+        if is_journey != ("journeys" in path.parts):
+            bad(line, "GH-13", "@journey scenarios, and only they, live in features/journeys/")
+        if is_journey:
+            journey(sc, kinds, rule, line)
+        else:
+            if kinds.count(WHEN) != 1: bad(line, "GH-04", f"{kinds.count(WHEN)} When steps; keep one action, earlier ones become Given facts")
+            if kinds != sorted(kinds, key=[GIVEN, WHEN, THEN].index): bad(line, "GH-04", "steps out of order; use Given -> When -> Then")
+            if not 3 <= len(bg_steps) + len(steps) <= 5: bad(line, "GH-05", f"{len(bg_steps) + len(steps)} steps (Background included); keep 3-5")
@@ -68 +81,0 @@
-        if not 3 <= len(bg_steps) + len(steps) <= 5: bad(line, "GH-05", f"{len(bg_steps) + len(steps)} steps (Background included); keep 3-5")
@@ -82 +95 @@
-        if not (rule and RULE_ID.match(rule["name"]) or RULE_ID.match(sc["name"])):
+        if not is_journey and not (rule and RULE_ID.match(rule["name"]) or RULE_ID.match(sc["name"])):
```

套上之後 `gherkin_lint.py features/` 回 `3 files, 0 problems`。再寫兩份壞例子。`bad/cancel_and_rebook.feature` 是 rule 型情境硬標 `@journey`：`Feature: Cancel and rebook` / `Rule: R4 Only the person who booked can cancel the booking` / `@journey @sc_cancel_09` / `Scenario: R4 Bob cancels and books again` / `Given Bob has booked "Taipei" from 10:00 to 11:00` / `When Bob cancels the booking of "Taipei" from 10:00 to 11:00` / `Then the booking is cancelled`。`bad/journeys/screen_script_journey.feature` 照螢幕錄影寫：`Feature: Giving a room back, written from a screen recording` / `@journey @sc_journey_02` / `Scenario: Alice frees a room for Bob` / `Given Alice is on the booking page` / `When Alice fills in the Room field with "Taipei"` / `And Alice presses Book` / `Then Alice sees "Booked Taipei"` / `When Bob books "Taipei" from 10:30 to 11:30` / `Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Alice` / `When Alice cancels the booking of "Taipei" from 10:00 to 11:00` / `When Bob books "Taipei" from 10:30 to 11:30` / `Then the booking is confirmed`（每個 ` / ` 是一行，縮排照一般 feature 檔；`Feature:` 與 `Rule:` 那兩行之後各空一行，行號才會和下面的輸出一樣）：

```
$ python .claude/skills/gherkin-style/scripts/gherkin_lint.py bad/
bad/cancel_and_rebook.feature:6: GH-13 @journey scenarios, and only they, live in features/journeys/
bad/cancel_and_rebook.feature:6: GH-13 journey shape GWT: Givens, then 2-4 When steps, each followed by a Then
bad/cancel_and_rebook.feature:6: GH-13 a journey crosses rules; move it out of Rule:
bad/cancel_and_rebook.feature:6: GH-13 name the rules it crosses in its description, e.g. "Crosses R1 and R4."
bad/journeys/screen_script_journey.feature:4: GH-13 journey shape GWWTWTWWT: Givens, then 2-4 When steps, each followed by a Then
…（略：第 4 行的 GH-13 描述；第 5、6、7 行的 GH-03：page、fills in、presses）
2 files, 9 problems
```

標 `@journey` 想繞路的那份四則全中：位置不對、形狀只有一個 When、掛在 `Rule:` 底下、沒寫跨了哪些規則。螢幕腳本那份被 GH-13 與 GH-03 一起抓，證明 journey 沒有因此免於宣告式的要求；lint **沒有**抓到的是 `Then Alice sees "Booked Taipei"`，那是畫面文字，要靠 review。回歸檢查：新版 lint 對 Ch 28 的好檔、壞檔、zh 目錄與 Ch 9 的兩組例子，輸出和原版逐字相同。`pytest -q` 回 `9 passed`，`-m journey` 回 `1 passed, 8 deselected`。

## 底層機制：一條 journey 怎麼把結果交給下一個 Then

```
 _execute_scenario：for step in scenario.steps（scenario.py 第 270 行）
   When Bob books ...      → 回傳 Outcome(False, "Taipei is taken ...") → target_fixture="outcome"
                           → inject_fixture（第 253–254 行）把 outcome 的 cached_result 改成這個值（compat.py 第 98–121 行）
   Then ... rejected ...   → request.getfixturevalue("outcome") 拿到「目前」的值
   When Alice cancels ...  → 回傳 Outcome(True)，再覆寫一次；下一個 Then 讀到的是取消的結果
 狀態本身（誰訂了什麼）在 app 的 BOOKINGS 裡，由前面的 When 產生；Given 經 DSL 走同一個 API 佈置
```

pytest-bdd 9.0.1 照順序執行每一步；When 帶 `target_fixture` 時，回傳值登記成 fixture，已經有值就強制覆寫 `cached_result`。後果：兩個 When 連著出現，第一個的結果還沒人看就被蓋掉。在探針目錄 `probe-ww/` 試：複製 `tests/`、`pytest.ini` 與 `app.py`，把 `app.py` 第 39 行 R4 的權限判斷寫反（`!=` 改成 `==`，預約人反而被拒）；`features/journeys/` 只放這條 journey 與副本 `no_then_journey.feature`，副本刪掉取消後面那個 Then、情境改名 `The same journey without a Then after the cancel`、ID 改成 `@sc_journey_02`（沒註冊，所以濾掉 marker 警告）。`grep` 只留每段 traceback 的位置與 `reason:` 那行：

```
$ pytest -q -m journey --tb=short -W ignore::pytest.PytestUnknownMarkWarning | grep -E '^tests/.*: in |^E +reason:|^FAILED|failed in'
tests/test_booking.py:21: in confirmed
E       reason: 'Taipei is taken 10:00-11:00 by Alice' != None
tests/test_booking.py:37: in cancelled
E       reason: 'only Alice can cancel this booking' != None
FAILED tests/test_booking.py::test_the_same_journey_without_a_then_after_the_cancel
FAILED tests/test_booking.py::test_a_cancelled_slot_goes_to_the_next_person
2 failed in 0.05s
```

有中間 Then 的版本紅在 `cancelled`：取消被拒，訊息直指 R4。少了它的版本紅在最後的 `confirmed`：「Taipei 被 Alice 佔著」，看起來像 R1 壞了，讀報告的人會去錯地方修。這就是 GH-13 要求 `(WT+)` 的理由，lint 對第二版回 `GH-13 journey shape GWTWWT`。順帶一提，`scenarios("../features")` 綁的檔案依 Feature 名稱排序（`feature.py` 第 80 行），所以「Giving a room back」排在「Room booking」前面。

## 五、journey 抓到的漏洞

RD 要保留取消紀錄，所以加了 `cancelled` 欄位與 `active()`，列表與取消都改看 `active()`；`create` 的重疊迴圈卻還留著 Ch 0 原本那行 `for x in BOOKINGS:`（把 `app.py` 第 24 行的 `active()` 改回 `BOOKINGS`）。這是雛形演進時很容易漏掉的一行。用 v2 的 feature 跑整套：

```
$ pytest -q --tb=line
F........                                                                [100%]
…（略：FAILURES 段的斷言比對）
/private/tmp/lab/aibdd/ch29-example-mapping/tests/test_booking.py:21: AssertionError: assert Outcome(confi...:00 by Alice') == Outcome(confi..., reason=None)
…（略）
FAILED tests/test_booking.py::test_a_cancelled_slot_goes_to_the_next_person
1 failed, 8 passed in 0.04s
```

八個 rule 型情境全綠：取消回 204、別人取消回 403、R1 與 R2 照舊。只有 journey 紅。它紅在第幾步？`pytest -q -m journey --cucumberjson=reports/journey.json` 之後，用 `python -c 'import json; [print(i, s["keyword"], s["name"], "->", s["result"]["status"]) for f in json.load(open("reports/journey.json")) for sc in f["elements"] for i, s in enumerate(sc["steps"], 1)]'` 印出每一步的狀態：

```
1 Given Alice has booked "Taipei" from 10:00 to 11:00 -> passed
2 When Bob books "Taipei" from 10:30 to 11:30 -> passed
3 Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Alice -> passed
4 When Alice cancels the booking of "Taipei" from 10:00 to 11:00 -> passed
5 Then the booking is cancelled -> passed
6 When Bob books "Taipei" from 10:30 to 11:30 -> passed
7 Then the booking is confirmed -> failed
```

第一個紅的 Then 說完了整件事：取消之前一切正常，取消本身也成功，取消之後房間還被佔著。（換檔時有個坑：`active()` 和 `BOOKINGS` 一樣長，檔案大小不變；同一秒內換回正確版本，Python 會沿用舊的 `.pyc`，我們實際撞到一次「正確版本也紅」。`rerun_chapter.sh` 因此設了 `PYTHONDONTWRITEBYTECODE=1`。）journey 抓到它，但 journey 不該是唯一抓到它的東西：它慢、跨兩條規則，紅了要人讀步驟清單才知道原因。正確的下一步是回到 map：這是一張少掉的綠卡，「取消掉的預約不再佔住會議室」。照 Rose 的「一個例子一條規則」，它屬於 R1，不屬於 R4。問題是：如果第一場會有 tester 在，這張卡會不會早就在桌上？

## 六、AI 當 tester amigo

先把方法打包成 `example-mapping` skill。`SKILL.md` 全文：

```markdown
---
name: example-mapping
description: Prepare, run or review an Example Mapping session (story, rules, examples, questions) for this booking project and turn a ready map into rule-style Gherkin. Use when a new story or change request arrives, when asked for examples, edge cases or acceptance criteria, when reviewing maps/*.yaml, or before writing a .feature file for a story that has no map yet.
allowed-tools: Bash(.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/example_map.py *)
---

# Example Mapping

You help three amigos (business, development, testing) map one story. You draft cards and ask; people decide.
Map format: `${CLAUDE_SKILL_DIR}/templates/map.yaml`. Card rules: `${CLAUDE_SKILL_DIR}/rules/mapping.md` (EM-01..EM-06).

1. Read the story, the files its `source:` names, `glossary.yaml` and `features/`. Start from the template, or open the existing map.
2. For every rule, hunt for the example nobody wrote: the boundary, another person, the same action twice, and what happens next (EM-03). Add it as a green card (a title is enough) or, if nobody can say the outcome, as a red card with an owner.
3. Never write an `answer` (EM-01). If the source files already answer a question, quote the file in your report instead of asking.
4. Run `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/example_map.py check <map>` as a command of its own (no pipes, no `;`). Fix every problem except open questions; those wait for their owner.
5. Once `check` says READY, print the skeleton with `... example_map.py gherkin <map>` and hand it to the gherkin-style skill.
6. If the story's goal ("so that ...") is a path through several rules, propose one journey for `features/journeys/` (GH-13) in your report; it is not a green card (EM-05).

Done when the map is READY, or when every remaining problem is an open question with an owner. Report the cards you added and the checker's last line.
```

`rules/mapping.md` 照 Ch 9 的格式（和 Ch 28 的規則檔一樣，一行一欄）寫六條規則，例子刻意取自 Ch 8 的例會與 R1、R2，不取自這張 story，免得把答案塞給要測的 agent。EM-01 紅卡只由 owner 回答，主持人（人或 AI）一律留空（review；`example_map.py` 把空答案算成未決）；EM-02 一張綠卡只說明一張藍卡（review）；EM-03 每條規則往四個方向找漏網例子（review，tester 的責任）；EM-04 例子用 glossary 的詞與既有句型，標題寫 "The one where ..."（GH-08 的 lint、GH-12 的 review）；EM-05 journey 來自 story 的目標，不是綠卡，一張 story 一到兩條（review、GH-13）；EM-06 狀態碼、id、payload 進 API 測試計畫，不進 feature（review、GH-03）。`templates/map.yaml` 是 v1 的形狀，值換成佔位符。佔位符要加引號：`story: <yellow card: As a ..., I can ...>` 這種沒引號的寫法，`check` 回 `cannot read: mapping values are not allowed here`、exit 2；加上引號之後回 `NOT READY`、2 problems（不合格的例子 id、未答的問題），沒填的模板不會被當成 READY。

> 以下是實際執行紀錄（節錄），每次執行結果不同；2026-11-02，Claude Code 2.1.295。

`ai-tester/` 是一個 `git init` 過的小專案（Ch 28 說明過為什麼要劃出 repo 邊界），只有 skill、`CLAUDE.md` 兩行、v1 map、story 卡、glossary 與 `booking.feature`。prompt：`You are the tester amigo joining an Example Mapping session for the story in maps/cancel.v1.yaml (cancelling a booking). Use the example-mapping skill. Leave maps/cancel.v1.yaml as it is and write your version to drafts/cancel.tester.yaml: the same map plus the examples and questions you think are missing. Do not answer any question.` 旗標：`--tools "Read,Write,Glob,Grep,Skill,Bash"`、`--allowedTools "Read,Write,Glob,Grep,Skill,Bash(.venv/bin/python *)"`、`--permission-mode acceptEdits --permission-prompts none --setting-sources project,local --strict-mcp-config --no-session-persistence --max-turns 12 --max-budget-usd 2`，輸入接 `< /dev/null`，完整指令在 lab 的 `run-tester.sh`。結果：15 turns、104 秒、$0.46、0 次拒絕（turns 是結果事件的 `num_turns`，約為工具呼叫數加 1，和 `--max-turns` 不是同一個計數單位，見 [Ch 7](./07-messy-baseline.md) 的底層機制）。它先叫用 skill，讀了七個檔（含 skill 的規則、模板與檢查器原始碼），寫出草稿，再**單獨**執行檢查器（skill 第 4 步那句「as a command of its own」來自 Ch 27 的教訓），得到 `1 maps, 9 problems`，九則全是未答的紅卡。我們重驗：8 個例子、9 個問題、填了答案的 0 個。它的報告裡有一段正是我們要的：

> **Missing rule:** `sc_cancel_06` and `sc_cancel_07` depend on a cancelled booking no longer holding the room, which R4 doesn't say. I kept them under R4 because you asked for the same map plus examples and questions, so the amigos should decide whether this needs its own blue card (EM-02).

它在寫程式之前就找到了第五節的洞（`sc_cancel_06`：The one where Alice books the slot Bob gave up），知道它不屬於 R4，也照 EM-01 把決定留給人；報告最後還照 EM-05 提了一條 journey，和第四節那條同一個形狀。它沒有加藍卡，是因為我們的 prompt 寫了「the same map plus the examples and questions」，它照字面讀。6 張綠卡、6 張紅卡交給三方分流：

| AI 的卡 | 處理 | 理由 |
|---|---|---|
| 06 Alice 訂到 Bob 放掉的時段 | 收下，改編 `sc_cancel_04`，移到 R1 | 第五節的洞；EM-02 |
| 08 Alice 接手後 Bob 再取消一次 | 收下，改編 `sc_cancel_03`，留在 R4 | 「這一筆」指目前持有人的那筆，回 `only Alice can cancel this booking` |
| 03、04、05、07 | 刪；07 併入 06，04 的情節交給 journey | 03 與 02 同結果；04 跨兩條規則；05 是 API 層的保證（取消只動那一個 id），進測試計畫；07 同規則同結果 |
| 紅卡 Q9 通知、Q6 只取消一部分 | 收下，PM 當場回答：另開 story；不行，取消整筆再重訂 | Q9 是好問題，但不在這張的範圍 |
| 紅卡 Q4、Q5、Q7、Q8 | 不收 | Q4、Q5 併入 Q3（沒有時鐘）；Q7、Q8 是 API 合約（EM-06） |

`maps/cancel.v3.yaml` 是 v2 加上：R4 底下的 `sc_cancel_03`（title `The one where Bob cancels again after Alice has taken the slot`；given 兩行 `Bob has cancelled the booking of "Taipei" from 10:00 to 11:00`、`Alice has booked "Taipei" from 10:00 to 11:00`；when `Bob cancels the booking of "Taipei" from 10:00 to 11:00`；then `the cancellation is rejected because only Alice can cancel this booking`）；新藍卡 `R1`（text `重疊只算有效的預約；取消掉的預約不再佔住會議室`）與它的 `sc_cancel_04`（title `The one where Alice books the slot Bob gave up`；given `Bob has cancelled the booking of "Taipei" from 10:00 to 11:00`；when `Alice books "Taipei" from 10:00 to 11:00`；then `the booking is confirmed`）；兩張紅卡 Q4（`被拒絕過、想要這個時段的人，會在取消後收到通知嗎？（AI tester 的 Q9）`，blocks R1，answer `這張不做；通知另開一張 story（PM）`）與 Q5（`能不能只取消一部分時段？（AI tester 的 Q6）`，blocks R4，answer `不行；取消整筆再重訂（PM）`），owner 都是 PM。`check` 回 `2 rules, 4 examples, 5 questions (0 open) -> READY`；重新產生 `cancellation.feature`，lint 回 `3 files, 0 problems`，正確的 app 是 `11 passed`。再換上第五節的漏洞版：

```
…（略：`pytest -q --tb=line` 的進度列與斷言比對；三行 --tb=line 的第二行是 tests/dsl.py:21: AssertionError: setup: Alice could not book Taipei）
FAILED tests/test_booking.py::test_a_cancelled_slot_goes_to_the_next_person
FAILED tests/test_booking.py::test_the_one_where_bob_cancels_again_after_alice_has_taken_the_slot
FAILED tests/test_booking.py::test_the_one_where_alice_books_the_slot_bob_gave_up
3 failed, 8 passed in 0.05s
```

現在有一個 rule 型情境（`sc_cancel_04`）用最少的步驟、在 Then 指出同一個漏洞，`sc_cancel_03` 則在 Given 就站不起來。journey 留著，當沒人想到時的那張網。

第二次實跑（同樣是實際執行紀錄）讓 AI 起草 journey：另一個 `git init` 過的 `ai-journey/`，有 v3 的 feature、step definitions、DSL 與帶 GH-13 的 `gherkin-style` skill，沒有 `features/journeys/`；prompt 要它依 v3 的 "so that" 寫一條 journey、只寫那一個檔；旗標同上，多給 `Edit`。結果：18 turns、42 秒、$0.31。它寫出的 journey 只有 7 步，用 `Given Bob has booked ...` 起頭，三組 When/Then 全用既有句型；我們事後跑 lint 是 `3 files, 0 problems`，`pytest -m journey` 是 `1 passed, 10 deselected, 2 warnings`（兩個 warning 是沒註冊的 marker：它照 prompt 只寫那一個檔，沒碰 `pytest.ini`）。本章第一版 journey 有 9 步，把第一筆預約也寫成 When 加 Then；它的寫法更省（BRIEF 的 Essential），第四節那條就是照它改的。兩點沒那麼好。第一，它自己的檢查一次都沒跑成：它把 lint、pytest、`git status` 串成一行，被 `--permission-prompts none` 自動拒絕，報告第一句就是 `neither the lint nor pytest has been run`。`gherkin-style` 的第 4 步沒有「as a command of its own」那句，`example-mapping` 有；兩次各 n = 1，只能說和 Ch 27 的經驗一致，要必經還是交給 Ch 28 的 PostToolUse hook。第二，它用 `@sc_cancel_05` 當 ID，合 GH-10 的格式，卻會和 map 下一張綠卡的編號撞在一起，所以 GH-13 規定 journey 用自己的 `@sc_journey_<NN>`。它在報告裡提了一條規則修改：「GH-13 says a journey follows "one person" ... I'd reword it to "one goal"」。它說得對，取消的旅程本來就有兩個人；我們照改，上面那份 GH-13 已是改過的版本。這就是 Ch 28 skill 第 6 步「規則看起來不對，照做並在報告裡說」該有的樣子。第一次實跑時，`example-mapping` 裡引用的 GH 編號是 Ch 28 改號前的舊號（當時的 GH-14 即今天的 GH-13），內容相同；第二次實跑讀到的 GH-13 是改字之前的版本（寫的是 one person）。兩次讀到的規則檔也都還是條列寫法、觸發條件全檔共用一行，後來才照 Ch 9 的格式改成一行一欄、每條各寫觸發條件；這次改格式沒有動到其他欄位。

## 七、導入流程：誰起草、誰審、誰簽

```
 ① 發現    PM、RD、QA（AI 起草、提問）       maps/<story>.yaml                example_map.py check → READY
 ② 表述    example_map.py gherkin（rule 型）  features/<story>.feature         gherkin_lint.py → 0 problems
           PM 或 QA 寫 journey（AI 可起草）   features/journeys/*.feature
 ③ 簽核    PM＋QA                            .bdd/approved.json               bdd_check.py approve（只由人執行）
 ④ 自動化  agent，bdd skill 的 steps 階段     tests/：step definitions、DSL     bdd_check.py red
           agent，implement 階段              app 程式                         bdd_check.py green
 ⑤ 發布    CI                                reports/living-doc.md            living_doc.py → N scenarios, 0 failed
     └──── journey 變紅、使用者回報的新情況，回到 ① 變成綠卡或紅卡
```

①的紅卡由 owner 回答，答案若是 API 合約就進測試計畫（Ch 24）；②之後三方在同一個 PR 上審 map 與 feature 的 diff。SDD Ch 22 的混合流程是這張圖的骨架；差別在每個箭頭都有一行指令把關，而且起點是 map，不是 Markdown spec。拿這張 story 當試點，從空目錄的 `pilot/` 走一遍（lab 裡是 `build_pilot.sh`）：放進 v3 的 `features/`（含 journey）、`tests/`、`glossary.yaml`、`pytest.ini`、`.venv` 連結、空的 `reports/`、本章兩個 skill、Ch 27 的 `bdd` skill 與 Ch 26 的 `living_doc.py`；第二節的 `app.py` 另存成 `variants/app_with_cancel.py`，`pilot/app.py` 用它刪掉 `@app.delete` 那一段（取消的路由）的版本。lint 照樣是 `3 files, 0 problems`。steps 與 implement 兩個階段的檔案用第二節那幾份代替 agent 的產出（agent 在這兩個階段的實跑見 Ch 27 第五節）：

```
$ python .claude/skills/bdd/scripts/bdd_check.py approve
approved 3 feature file(s); phase is now steps
$ python .claude/skills/bdd/scripts/bdd_check.py red
RED-right @journey @sc_journey_01  Then the booking is cancelled: AssertionError: assert Outcome(confi...n='Not Found') == Outcome(confi..., reason=None)
…（略：@sc_book_01..04 共 6 行 pass）
RED-right @sc_cancel_01  Then the booking is cancelled: AssertionError: assert Outcome(confi...n='Not Found') == Outcome(confi..., reason=None)
RED-right @sc_cancel_02  Then the cancellation is rejected because only Bob can cancel this booking: AssertionError: assert Outcome(confi...n='Not Found') == Outcome(confi...this booking')
RED-wrong @sc_cancel_03  Given Bob has cancelled the booking of "Taipei" from 10:00 to 11:00: AssertionError: setup: Bob could not cancel Taipei
RED-wrong @sc_cancel_04  Given Bob has cancelled the booking of "Taipei" from 10:00 to 11:00: AssertionError: setup: Bob could not cancel Taipei
11 scenarios: 6 passed, 3 red for the right reason, 2 red for the wrong reason; expect red: NOT met
$ cp variants/app_with_cancel.py app.py; echo implement > .bdd/phase
$ python .claude/skills/bdd/scripts/bdd_check.py green
…（略：11 行 pass）
11 scenarios: 11 passed, 0 red for the right reason, 0 red for the wrong reason; expect green: met
$ python living_doc.py reports/cucumber.json --junit reports/junit.xml > reports/living-doc.md
11 scenarios, 0 failed
```

（`living_doc.py` 讀的兩份報告來自另跑一次的 `pytest --cucumberjson=reports/cucumber.json --junitxml=reports/junit.xml`，`11 passed`。）活文件的第一段就是那條 journey，七步照原文列出，這是 PM 最先會讀的一頁。紅燈閘門回 `NOT met`，這是試點該抓到的那種事。Ch 27 的檢查器長在「替既有端點加一條規則」的 story 上；這張是「整個能力都還不存在」的第一張 story。三則 RED-right 其實是 `Not Found`（路由不存在），Ch 27 的 `red-reasons.md` 早就點名 404、405 不算數；兩則 RED-wrong 是 Given 用到了還不存在的取消。五行指向同一個原因。我們的處理：人讀過這五行，手動切到 implement，把這段輸出貼進 PR；同時在 [Ch 6](./06-workflows-are-grown.md) 的 ledger 記一筆「新能力的第一張 story，red 閘門會誤報」，等它出現第二次再決定要不要改檢查器。不放寬檢查器，是因為放寬之後，真正紅錯原因的情況也會一起放過。誰寫什麼，把 [Ch 26](./26-bdd-vs-tdd-artifacts.md) 的表加上簽核與把關：

| 產物 | 起草 | 審 | 簽核／把關 | agent 不可以 |
|---|---|---|---|---|
| `maps/*.yaml` | 三方當場寫；AI 可起草卡片 | 三方 | owner 答完紅卡；`check` → READY | 填任何 `answer` |
| rule 型 feature | `example_map.py gherkin`；QA 潤飾或審 AI 的草稿 | PM、RD | PM＋QA 執行 `approve`；lint 0 | 簽核之後改它（Ch 27 的 guard 與 hash） |
| journey | PM 或 QA；AI 可起草 | 三方 | 同上；GH-13 | 為了變綠刪掉中間的 Then |
| step definitions、DSL | agent | RD | `bdd_check.py red` | 改 feature 迎合 step |
| app 程式 | agent | RD（code review） | `bdd_check.py green`、CI | 改 tests/ 迎合程式 |
| 活文件 | CI | PM、所有人 | `living_doc.py` exit 0 | 手改 |

feature 檔由 PM 核准，是因為它記的是業務決定；QA 執筆或審稿，是因為邊界與反例是他的工作；RD 不單獨改，因為他改的會是程式的語言。和 SDD Ch 43 的 Spec Review 比，差在審什麼：Ch 43 審 spec.md 的 EARS，這裡審的是會執行的 feature 與它的 map；Ch 43 的 Definition of Ready（spec 裡沒有 `[NEEDS CLARIFICATION]`），在這裡變成 map READY、lint 0、已簽核三件可以用指令檢查的事。導入分三段，每段只加一樣東西（[Ch 6](./06-workflows-are-grown.md) 的「工作流是長出來的」）：

| 階段 | 範圍 | 加上什麼 | 什麼時候往下走 |
|---|---|---|---|
| 試點 | 一張 story（本章） | map、兩支 lint、Ch 27 的三個階段，全部手動跑 | 從 ① 走到 ⑤ 一次；PM 讀得懂活文件；閘門的誤報記進 ledger |
| CI | 同一個 repo | PR 上跑 `gherkin_lint.py` 與 `bdd_check.py green`；CODEOWNERS 把 `features/`、`maps/`、`.bdd/approved.json` 指給 PM、QA；`-m journey` 在合併後跑 | 沒有人需要繞過閘門 |
| 團隊 | 每張新 story | DoR＝map READY＋lint 0＋已簽核；照 Ch 6 的兩週 retro 回顧 ledger | — |

CI 與 CODEOWNERS 這一段本章未實測，為理論預期行為；驗證方式是開一個只改 feature 的 PR，確認 CODEOWNERS 要求 PM 核准、CI 的 `bdd_check.py green` 先回報 hash 不符。

## 對比與取捨

| 寫法 | 證明什麼 | 紅了告訴你什麼 | 成本 | 適用 |
|---|---|---|---|---|
| rule 型（一例一規則） | 一條規則在最小狀態下成立 | 哪條規則壞了：scenario 就掛在那條 `Rule:` 底下 | 便宜、快，可以大量寫 | 每條規則的邊界與反例 |
| journey 型（When/Then 交替） | 規則沿著使用者路徑接得起來 | 第一個紅的 Then 指出哪一段交接斷了 | 慢、較脆；一張 story 一到兩條 | story 的 "so that" |
| 把 journey 拆成幾個 rule 型 | 每段單獨成立 | 不知道接在一起會不會壞 | 低 | 規則之間沒有狀態交接時 |
| 螢幕腳本（點擊、填欄位） | 畫面操作可行 | 常是 selector 或時序 | 最脆，換畫面就要改 | 留給 [Ch 23](./23-frontend-e2e-playwright.md) 的 UI 測試 |

## 踩雷集錦

1. **錯誤直覺：journey 比較接近真實，多寫 journey 比較安全。** 正確認識：journey 慢、跨規則、紅了要讀步驟清單才知道原因。第五節的漏洞被 journey 抓到之後，我們把它寫回 map，變成 rule 型的 `sc_cancel_04`；journey 留著當網子，一張 story 一到兩條。
2. **錯誤直覺：rule 型情境加個 `@journey` 就能多寫幾個 When。** 正確認識：GH-13 反過來檢查形狀、位置、`Rule:` 與描述，`bad/cancel_and_rebook.feature` 四則全中。新規則要經 review 加進 rules file，不是在情境上貼標籤。
3. **錯誤直覺：journey 裡連續兩個 When 沒關係，最後的 Then 會抓到。** 正確認識：`target_fixture` 只留最後一個結果。權限判斷寫反時，少了中間 Then 的版本報「Taipei is taken 10:00-11:00 by Alice」，像是 R1 壞了；有中間 Then 的版本直接報 `only Alice can cancel this booking`。
4. **錯誤直覺：AI tester 提的卡越多越好，全部收下。** 正確認識：run 1 的 6 張綠卡收了 2 張，其餘是同規則同結果的重複、跨規則的情節，或由設計保證的事；6 張紅卡收 2 張。卡片的價值在會改變結果的那一個（key example）。它把關鍵那張放在 R4 下，是 EM-02 的錯，它自己在報告裡點了出來，搬卡的是人。
5. **錯誤直覺：每張紅卡的答案都要變成一個情境；閘門在 Ch 27 跑得通，試點照搬就好。** 正確認識：Q2 的答案是 API 合約，進測試計畫（EM-06），寫成情境會把 404 與 id 帶進 step。閘門也一樣要試：`bdd_check.py red` 在新能力的第一張 story 回 `NOT met`，五行紅燈同一個原因，該由人判讀並記進 ledger，不是放寬檢查器。

## 進階：journey 的去處、走後門的 Given、map 的健康度

- **journey 交給哪個 driver**：`pytest -m journey` 選得出 journey（本章實測 `1 passed, 8 deselected`），它們是最自然的 UI 測試候選；rule 型便宜，適合走 API。要不要這樣分，[Ch 30](./30-one-gherkin-two-drivers.md) 的「五、選通道」給了另一個預設：每個情境兩個通道都跑，頁面還做不到的才用 `@ui_pending` 標成例外，理由是忘了標時，列舉式會悄悄少跑，例外式會紅給你看。
- **Given 走後門**：試點的 RED-wrong 來自 Given 經由 API 佈置「已取消」。另一種做法是 DSL 直接寫入測試資料，讓 Given 不依賴還沒做好的功能，紅燈就會落在 Then；代價是佈置的狀態可能和真實路徑產生的不一樣，而 journey 存在的理由正是檢查真實路徑。本段未實測，為理論預期行為；要驗證，可以給 `has_cancelled` 一個寫入 `BOOKINGS` 的版本，重跑試點的 `red`。
- **map 的健康度**：YAML 是資料，可以算數。每條規則的例子數（太多代表規則裡藏著好幾條，Ch 8 引 Wynne）、紅卡 owner 的分布（Ch 8 的「量三明治」）、每張 story 的 journey 數。這幾個數字適合在第三階段的回顧裡看。

## 動手練習

1. **把 Q9 排成一張 story**：「被拒的人在時段空出來時收到通知」。寫 story 卡（含 "so that"）、v1 map，用 tester 的四個方向至少找出兩張綠卡、一張紅卡，`check` 跑到 READY，再寫它的 journey，讓它通過 GH-13。
2. **找出 GH-13 的界線**：把本章的 journey 改成 5 個 When（例如 Bob 再取消一次、Carol 接手），看 lint 怎麼說；再刪掉 `Crosses R1 and R4.`，再把它搬到 `features/` 底下。每一步預測輸出再執行。
3. **map 健康度腳本**：寫一支 20 行左右的腳本讀 `maps/*.yaml`，印出每條規則的例子數與每個 owner 的紅卡數，最後一行 `N maps, M rules`。拿 v1、v2、v3 與 `ai-tester/drafts/cancel.tester.yaml` 跑，看 AI 的草稿在哪個數字上最不一樣。

## 本章重點整理

- Three Amigos 是三種視角：業務問要解決什麼、開發問怎麼做、測試問那如果呢。tester 找漏網例子的四個方向是邊界、換一個人、同一件事做兩次、然後呢。
- Example Mapping 的產出天生是 rule 型：一個例子說明一條規則。journey 型說明 story 的目標，跨規則、When/Then 交替；GH-13 給它自己的形狀、位置與 ID，其他寫作規則照樣適用。
- pytest-bdd 用 `target_fixture` 把每個 When 的結果覆寫進同一個 fixture；每個 When 後面接 Then，第一個紅的 Then 才指得出哪一段交接斷了。
- 本章的 journey 抓到 rule 型情境全綠時漏掉的軟刪除漏洞；AI tester 在寫程式之前就提了同一張卡。漏洞的歸宿是 map 上的一張 rule 型綠卡，journey 留著當網子。
- 導入流程的每個箭頭都有指令把關：map READY、lint 0、人簽核、red、green、活文件。試點會抓到閘門的誤報，交給人判讀與 ledger，不是放寬檢查器。

## 自我檢核

- [ ] 不看本章，說出 Three Amigos 三種視角各問的問題，以及 tester 找漏網例子的四個方向，各舉一個取消預約的例子。
- [ ] rule 型與 journey 型各證明什麼？為什麼 Example Mapping 的產出天生是 rule 型？
- [ ] 用 `target_fixture` 的運作方式解釋：為什麼 journey 裡兩個 When 連在一起，會讓報錯指向錯的規則？
- [ ] 第五節的漏洞為什麼讓八個 rule 型情境全綠？最後是哪一張卡、掛在哪條規則下修好這個缺口？
- [ ] 在導入流程裡，feature 檔由誰起草、誰簽核？agent 在哪些產物上可以起草、在哪些上永遠不能動？試點時 `bdd_check.py red` 回 `NOT met`，你怎麼判斷、怎麼處理？

## 延伸閱讀

- **[Gojko Adzic — Specification by Example](https://livebook.manning.com/book/specification-by-example/chapter-2/)**（Manning，2011；連結是第 2 章）
  - **讀哪裡**：第 2 章「Key process patterns」的七個模式；第 7 章「Illustrating using examples」與第 8 章「Refining the specification」。**學到什麼**：約五十個專案歸納出的流程，以及好例子的四個性質。**和本章的關聯**：第三節那張表的左欄，也是判斷整條流程缺哪一環的清單。**前提**：讀過 Ch 8 與本章第二節，知道一張 map 長什麼樣。
- **[Seb Rose — Gherkin Rules](https://cucumber.io/blog/bdd/gherkin-rules/)**（Cucumber 部落格，2021-03-15）
  - **讀哪裡**：全文，重點是 `Rule` 關鍵字的由來與「Each example in an example map illustrates one (and only one) rule」。**學到什麼**：Example Mapping 四種卡片裡，只有規則與例子該進 feature 檔。**和本章的關聯**：rule 型情境的定義，以及 journey 為什麼不是一張綠卡。**前提**：Ch 26 的 `Rule` 關鍵字。
- **[Agile Alliance — Three Amigos](https://agilealliance.org/glossary/three-amigos)**（詞彙表）
  - **讀哪裡**：三種視角各自的問題與 Origins 一段。**學到什麼**：three amigos 是在開發前、中、後檢視工作的三種視角；Origins 指向 Dinwiddie 2009 年的部落格文章，頁面另附他在 *Better Software* 的專文〈The Three Amigos: All for One and One for All〉（2011 年 11/12 月號、第 13 卷第 6 期，刊期見他的[著作清單](https://idiacomputing.com/publications/)）。**和本章的關聯**：第一節那張表的來源；想讀原文，從這裡找到 Dinwiddie 的文章。**前提**：SDD Ch 9 的 3C。
- **[John Ferguson Smart、Jan Molak — BDD in Action, Second Edition](https://livebook.manning.com/book/bdd-in-action-second-edition/chapter-9/)**（Manning，2023；連結是第 9 章）
  - **讀哪裡**：9.3「Layers of abstraction」的四小節：Business Rules、Business Flow、Business tasks、Technical。**學到什麼**：規則層描述期望的結果，流程層描述使用者的旅程，往下才是任務與技術細節。**和本章的關聯**：rule 型與 journey 型的分工，以及 Ch 28 的 DSL、Ch 30 的 driver 各在哪一層。**前提**：會讀 Java 或 JavaScript／TypeScript 的步驟定義，Manning 的書頁說範例用 Java、JavaScript 與 TypeScript。
- **[Cucumber — Example Mapping](https://cucumber.io/docs/bdd/example-mapping/)**（官方文件）
  - **讀哪裡**：四色卡片的說明與「rules that summarise a set of examples」那句。**學到什麼**：用具體例子探索問題領域，藍卡是歸納一組例子的規則；所以一張新綠卡可能需要一張新藍卡來歸納它。**和本章的關聯**：第六節 v3 多出的 R1 藍卡，就是為了歸納 AI 那張綠卡才加上的。**前提**：Ch 8 的四色卡片。

規則與旅程都只透過 DSL 碰系統。下一章把 DSL 裡那一行 `TestClient` 拆成 API 與 UI 兩個 driver，讓同一份 feature 各跑一次。

→ [Ch 30 一份 Gherkin、兩個 driver：前後端共用驗收測試](./30-one-gherkin-two-drivers.md)
