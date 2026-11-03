# Ch 2 — Vibe coding 與 AI 輔助工程：同一個功能看兩次

> **目標**：分清楚 AI coding 的五種模式各自自動化了什麼、人還握著什麼；把 Karpathy 2025 年 2 月造的「vibe coding」原義和今天的寬鬆用法分開；親手用驗收探針審一份「沒人審過」的預約 API，看 4 個 bug 怎麼現形、為什麼 vibe 的迴圈永遠看不到它們，再看一次真實的 agent 生成留下了什麼給人做。同一個功能，看兩次。

> **環境**：Python 3.12.15、pytest 9.2.0、FastAPI 0.162.0（starlette 1.10.0、pydantic 2.14.2）、httpx 0.28.1，用 [Ch 0](./00-environment-setup.md) 建好的 `/tmp/lab/aibdd/.venv`；Claude Code 2.1.295（一次真實的 `claude -p`，2026-11-02）；macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch02-vibe/`。

## 為什麼需要這個？

在 AI 之前，寫 code 的人就是第一個讀 code 的人：手指打出 `<=` 的那一刻，腦子至少經過它一次。這個「順便的審查」便宜到沒人注意它存在。agent 把它拿掉了：一個功能可以從一句 prompt 直接變成 diff，中間不經過任何人的腦子。寫的成本掉到接近零，**判斷它對不對**的成本一點都沒掉——瓶頸搬到了意圖與驗證上（[SDD Ch 1](../spec_driven_development/01-why-specs-matter-now.md) 從規格的角度講過同一件事）。

偏偏描述這件事的詞彙在同一年被用亂了。「vibe coding」在 2025 年 2 月出現時有很具體的意思；到了年底 Collins 詞典把它選為年度詞彙時，定義已經寬到涵蓋所有「用 AI 幫忙寫 code」的行為。於是一個人說「我們不 vibe coding」，另一個人聽成「我們不用 AI」，兩邊各自有理地吵起來。

所以我們先校準詞彙（模式的光譜、原義與寬義、人還擁有什麼），再拿同一個預約功能做一次對照：第一次用 vibe 的方式看它，第二次用工程的方式看它。整門課後面的迴圈、skill、SDD、TDD、BDD，都建立在第二種看法上。

> 如果你對 agent 的基本運作（模型只產生文字，工具由 harness 執行）還不熟，先回看 [harness Ch 1](../harness_engineering/01-what-is-agent-harness.md)；Claude Code 的架構拆解在 [ai_applications Ch 24](../ai_applications/24-product-teardowns.md) 的 Case 1。

## 先建立直覺

**驗屋**。兩個人買了同一棟預售屋裡格局一樣的兩戶。第一個人交屋那天走一圈：燈會亮、水龍頭有水、門關得上，簽收。第二個人帶一張驗屋清單：插座逐一插測試器、浴室放水看排水方向、水平儀量地板、對著圖面量尺寸。同一個建商、同一批工班；第二個人簽收前就知道哪兩個插座的線接反了，第一個人要等到電器燒掉才知道。

AI 就是那個手很快、手藝通常不差的建商。**驗屋清單是買方的**，建商不會替你帶。vibe coding 是第一種交屋法，AI 輔助工程是第二種。這個比喻有一處不成立：驗屋時房子已經蓋完；寫軟體時，清單可以在動工**之前**就交給 agent，讓它邊蓋邊自己驗——那是 [Ch 3](./03-propose-constrain-feedback.md) 的主題。第二個直覺：這裡有兩條軸，不是一條。

```
              驗證強度：誰、用什麼確認它是對的
                            ▲
     AI 輔助工程            │             AI 輔助工程
     （補全，每行都讀過）   │     （agent 改多個檔，跑探針、審 diff）
  ──────────────────────────┼──────────────────────────▶ 自主程度：AI 一步做多少
     vibe coding            │             vibe coding
     （聊天貼上，能跑就好） │     （agent，Accept All，不讀 diff）
                            │
```

一個常見的誤解是把 vibe coding 放在自主程度的最右端，好像「用 agent = vibe coding、用補全 = 正經寫 code」。不是。橫軸問 AI 一步做多少事（[SDD Ch 23](../spec_driven_development/23-software-2-to-3.md) 的「Karpathy 的「自主性滑桿」」畫的就是這條軸）；vibe coding 是縱軸的下半部，問的是**有沒有人確認過結果**。你可以用最陽春的聊天介面 vibe coding，也可以讓 agent 一口氣改十個檔案而仍然是嚴謹的工程。

## AI coding 的五種模式：各自交出什麼、人還握著什麼

| 模式 | 例子 | 一次交出什麼 | 回饋從哪來 | 人仍然擁有 |
|---|---|---|---|---|
| 行內補全（tab completion） | GitHub Copilot 補全、Cursor Tab | 游標後的幾行 | 你當下讀它、按不按 Tab | 幾乎全部；它出現在眼前，很難不讀 |
| 對話（chat） | ChatGPT、claude.ai | 一段 code 或解釋 | 你貼回編輯器、自己跑 | 整合：貼到哪、接什麼、怎麼測 |
| IDE 內 agent | Cursor、VS Code Copilot 的 agent 模式 | 跨檔修改，加上它自己跑的指令 | 它讀到的檔案、它跑的測試 | 逐一批准或退回 diff、決定何時停 |
| 終端機 agent | Claude Code、Codex CLI、aider | 一個多步任務：讀、改、跑、再改 | 指令輸出、測試、hook | 權限邊界、驗收標準、合併前審查 |
| 雲端／背景 agent | Claude Code 雲端 session、GitHub Copilot coding agent | 一個分支或一個 PR | 雲端 VM 裡的測試、CI | PR review、CI 閘門、要不要合併 |

往下走，AI 每一步交出來的東西越大，你第一次看到它的時間點越晚：補全是逐行看；Claude Code 的雲端 session 在你闔上筆電後繼續跑；Copilot coding agent 被指派 issue 後自己開 draft PR，做完請你 review。最右欄有一件事從頭到尾沒變：**判斷「這是對的」的那個人**。模式改變的是你在哪個時間點、用什麼粒度行使這個判斷，不是要不要行使。讀碼也有一條類似的演化，見 [reading_code Ch 20](../../soft_skills/reading_code/20-ai-assisted-reading.md) 的「工具生態：從聊天到 agentic 讀碼」，結論相同：驗證的角色沒有消失，只是上移了。

## 「vibe coding」這個詞：原義、界線、寬義

**Karpathy 的原義**。Andrej Karpathy 在 2025 年 2 月 2 日的推文裡造了這個詞（X 的推文 ID 編碼了發文時間，換算是 23:17 UTC；下面引的 Willison 文章寫成 2 月 6 日，以 ID 為準）：「fully give in to the vibes, embrace exponentials, and forget that the code even exists」。他描述的行為很具體：「I "Accept All" always, I don't read the diffs anymore」、碰到錯誤訊息原封不動貼回去（通常就修好了）、code 長到超出自己的理解、修不好的 bug 就繞過去或叫它亂改到 bug 消失。**範圍也很具體**：「It's not too bad for throwaway weekend projects, but still quite amusing.」這是一個人對自己某種玩法的描述，不是方法論。

**Willison 的界線**。六週後 Simon Willison 寫了〈Not all AI-assisted programming is vibe coding (but vibe coding rocks)〉（2025-03-19），把 vibe coding 定義成「building software with an LLM without reviewing the code it writes」，並劃出界線：

> If an LLM wrote the code for you, and you then reviewed it, tested it thoroughly and made sure you could explain how it works to someone else that's not vibe coding, it's software development.

這條線**不看 code 是誰寫的**，看三件事：審過、測過、能向別人解釋。同年 10 月他又提出「vibe engineering」指光譜另一端：資深工程師用 LLM 加速，同時「staying proudly and confidently accountable for the software they produce」。

**字典的寬義**。Collins 詞典把「vibe coding」選為 2025 年度詞彙（2025 年 11 月公布），定義是「the use of artificial intelligence prompted by natural language to assist with the writing of computer code」（引自 CNN 的報導）。「不審查」這個核心條件不見了：照這個定義，Willison 那種審過、測過、能解釋的流程也算。

| 用法 | 判準 | 本章第一次看 | 本章第二次看 |
|---|---|---|---|
| Karpathy 原義 | Accept All、不讀 diff、錯誤貼回去 | 算 | 不算 |
| Willison 的界線 | 沒審查就算；審過、測過、能解釋就不算 | 算 | 不算 |
| Collins 寬義 | 用自然語言叫 AI 幫忙寫 code | 算 | 也算 |

本課採 Willison 的界線：**分界不在 AI 寫了多少，而在人有沒有確認、能不能解釋**。討論前先問一句「你說的 vibe coding 是哪一種？」能省掉很多雞同鴨講。不管 AI 做到多少，下面五件事在本課一直歸人：

1. **意圖與契約**：R1 被拒時訊息要點名誰佔用，讓使用者知道該去找誰商量。沒寫下來，AI 只能猜。
2. **判準**：什麼叫「對」、由誰判斷、用什麼判斷。測試領域把這個判斷依據叫測試神諭（test oracle）。
3. **理解**：能向別人解釋這次改動，Willison 的第三個條件。
4. **決策的最後一票**：agent 替你做的選擇要逐一接受或推翻（稍後的真實生成替我們做了 5 個）。
5. **責任與邊界**：合併、上線、權限、秘密金鑰。Willison 認可 vibe coding 的第一個條件就是低風險（low stakes）；[harness Ch 41](../harness_engineering/41-when-to-agentify.md) 用「可逆性與 blast radius（出錯時波及的範圍）」談的是同一件事。

## 第一次看：用 vibe 的方式交屋

規則只有兩條（全課共用的會議室預約領域）：**R1** 同一間會議室的時段不能重疊，重疊回 409，訊息點名誰佔用，例如 `Taipei is taken 10:00-11:00 by Bob`；**R2** 結束必須晚於開始，否則 422 `end must be after start`。時段是半開區間 `[start, end)`：10:00–11:00 和 11:00–12:00 不重疊。

`pytest.ini` 濾掉一條警告：starlette 1.10.0 的 TestClient 偏好新的 `httpx2` 套件，找不到就退回 `httpx` 並發出 `StarletteDeprecationWarning`，而課程 venv 依 Ch 0 的釘定不另裝套件。

```bash
mkdir -p /tmp/lab/aibdd/ch02-vibe/{vibe,fixed,probes,oracle,agent}
cd /tmp/lab/aibdd/ch02-vibe && source ../.venv/bin/activate
cat > pytest.ini <<'EOF'
[pytest]
filterwarnings =
    ignore:Using `httpx` with `starlette.testclient` is deprecated
EOF
```

`vibe/app.py` **是我們手寫來模仿「沒人審過的 AI 產出」的典型樣子，不是某次真實生成**（真實生成在後面一節）。它乾淨、有 docstring、看起來很專業：

```python
"""Meeting room booking API."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Room Booking API")
bookings = []  # in-memory storage

class BookingRequest(BaseModel):
    room: str
    start: str = Field(..., description="Start time in HH:MM format")
    end: str = Field(..., description="End time in HH:MM format")
    by: str

def is_overlapping(start1, end1, start2, end2):
    """Check if two time ranges overlap."""
    return start1 <= end2 and start2 <= end1

@app.post("/bookings", status_code=201)
def create_booking(req: BookingRequest):
    for b in bookings:
        if b["room"] == req.room and is_overlapping(req.start, req.end, b["start"], b["end"]):
            raise HTTPException(status_code=400, detail="Room already booked for this time")
    booking = {"id": len(bookings) + 1, **req.model_dump()}
    bookings.append(booking)
    return booking

@app.get("/bookings")
def list_bookings():
    return bookings

@app.post("/reset")
def reset():
    bookings.clear()
    return {"status": "ok"}
```

AI 通常還會順手附上測試。`vibe/test_smoke.py`（同樣是手寫模仿）：

```python
from fastapi.testclient import TestClient
from vibe.app import app

client = TestClient(app)

def test_create_booking():
    r = client.post("/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Alice"})
    assert r.status_code == 201

def test_double_booking_rejected():  # 依賴上一個測試留下的狀態
    r = client.post("/bookings", json={"room": "Taipei", "start": "10:00", "end": "11:00", "by": "Bob"})
    assert r.status_code == 400
```

跑 `python -m pytest -q vibe/test_smoke.py`，輸出 `2 passed in 0.13s`：建立成功、重複的被擋，測試全綠。照 Karpathy 的玩法，這裡就收工了。

## 第二次看：把規則寫成驗收探針

第二次看，我們先不讀實作，把 R1、R2 寫成**驗收探針**（acceptance probe，本課用語）：從規則推出來、只透過公開介面（這裡是 HTTP）觀察行為的一小段可執行驗收測試。`probes/test_acceptance.py`：

```python
"""驗收探針：從規則 R1、R2 推出來，不看實作。用 APP=<module> 指定要審的版本。"""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

MODULE = os.environ.get("APP", "fixed.app")

@pytest.fixture
def client():
    module = importlib.reload(importlib.import_module(MODULE))  # 每個探針一份全新的記憶體狀態
    return TestClient(module.app)

def book(client, room, start, end, by):
    return client.post("/bookings", json={"room": room, "start": start, "end": end, "by": by})

def test_free_slot_is_confirmed(client):
    r = book(client, "Taipei", "10:00", "11:00", "Alice")
    assert r.status_code == 201 and r.json()["id"] == 1, r.json()

def test_overlap_is_rejected_naming_the_holder(client):  # R1
    book(client, "Taipei", "10:00", "11:00", "Bob")
    r = book(client, "Taipei", "10:30", "11:30", "Alice")
    assert r.status_code == 409, r.json()
    assert r.json()["detail"] == "Taipei is taken 10:00-11:00 by Bob"

def test_adjacent_slot_is_free(client):  # R1 的邊界：[start, end) 半開區間
    book(client, "Taipei", "10:00", "11:00", "Bob")
    r = book(client, "Taipei", "11:00", "12:00", "Alice")
    assert r.status_code == 201, r.json()

def test_unpadded_time_cannot_double_book(client):  # R1：使用者打 9:30 而不是 09:30
    book(client, "Taipei", "10:00", "11:00", "Bob")
    book(client, "Taipei", "9:30", "10:30", "Alice")
    assert [b["by"] for b in client.get("/bookings").json()] == ["Bob"]  # 看狀態，不只看回應

def test_end_must_be_after_start(client):  # R2
    r = book(client, "Taipei", "11:00", "10:00", "Alice")
    assert r.status_code == 422, r.json()
    assert r.json()["detail"] == "end must be after start"
```

每個設計決定都有「為什麼不用別的寫法」：

- **只碰 HTTP，不 import 內部函式**，所以同一份探針能審任何實作，稍後審真實 agent 的版本也不用改一行。
- **每個探針 `importlib.reload` 一次**，不呼叫被審者的 `/reset`：我們在審一份不信任的 code，隔離機制不能依賴它寫對。
- **`9:30` 那條看系統狀態**（`GET /bookings`）：雙重預約的傷害是「Alice 也被記進去了」，探針就去看這件事。
- **先斷言狀態碼、把 body 當失敗訊息**。第一版的 R2 探針把兩者綁成一個 tuple：`assert (r.status_code, r.json()["detail"]) == (422, ...)`，對 vibe 版本跑出來是 `KeyError: 'detail'`。它紅了，但紅的理由錯了：`KeyError` 只說「回應裡沒有 detail」，沒說「一筆結束早於開始的預約被收下了」。為錯的理由失敗的探針會讓人去修錯的地方（[Ch 25](./25-tdd-in-sdd-pipeline.md) 會把「紅得對不對」做成閘門）。五個探針都照這個順序寫；第一個探針用 `and` 寫在同一行，狀態碼不對就不會去讀 `id`。

```bash
APP=vibe.app python -m pytest -q --tb=short probes
```

```
.FFFF                                                                    [100%]
…（略）
E   AssertionError: assert ['Bob', 'Alice'] == ['Bob']
…（略）
E   AssertionError: {'id': 1, 'room': 'Taipei', 'start': '11:00', 'end': '10:00', ...}
E   assert 201 == 422
…（略）
4 failed, 1 passed in 0.12s
```

五個探針紅了四個：重疊與相鄰兩條分別是 `assert 400 == 409`、`assert 400 == 201`（上面略去），`9:30` 那條看到 Alice 也被記進去，R2 那條看到一筆 11:00–10:00 的預約被收下。四個紅燈各對應一個 bug：

| Bug | 成因（在 `vibe/app.py` 哪裡） | 探針看到的症狀 |
|---|---|---|
| B1 相鄰時段被擋 | `start1 <= end2`：閉區間的重疊公式，規格要半開 | 11:00–12:00 回 400 |
| B2 字串比較時間 | `start: str`，`"9:30" <= "11:00"` 比的是字元 `"9"` 和 `"1"` | 9:30–10:30 和 Bob 的 10:00–11:00 重疊，Alice 卻也被記進去 |
| B3 沒有 R2 檢查 | handler 裡找不到比較 end 與 start 的那一行 | 11:00–10:00 回 201 |
| B4 錯誤契約 | 400 加一句籠統訊息，是實作自己挑的 | 409 不見了，也不知道誰佔用 |

B2 值得多看一眼：如果輸入是補零的 `09:30`，字串的字典序剛好等於時間順序，bug 不會出現——只用 `09:30`、`10:00` 這種「乾淨」資料的測試永遠抓不到它。**這個 bug 住在輸入格式裡，不在邏輯裡**。

## 修好它：審 diff 的五個問題與工程版

探針告訴我們「哪裡錯」；讀 diff 告訴我們「還有沒有別的」。拿 vibe 版本回答這五個問題：

1. **邊界上的型別**：時間、金額、ID 進門時就轉成正確的型別了嗎？（`start: str`，沒有，B2）
2. **區間慣例**：開區間、閉區間還是半開？和規格一致嗎？（閉區間，B1）
3. **每條規則的守門 code 在哪一行**？找不到那一行，就是沒有。（R2 找不到，B3）
4. **錯誤契約**：狀態碼和訊息是規格定的，還是實作自己挑的？（自己挑的，B4）
5. **狀態**：誰能改、什麼時候清空、id 從幾開始、刪除後會不會重用？（`len + 1` 一旦有刪除就會重用 id；目前沒有刪除，先記下）

`fixed/app.py`：

```python
"""會議室預約 API。R1：同一間不可重疊，區間是半開的 [start, end)。R2：end 必須晚於 start。"""
from datetime import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
BOOKINGS: list[dict] = []

class BookingIn(BaseModel):
    room: str
    start: time  # 在邊界就轉成 time；"9:30" 這種不是 HH:MM 的輸入由 pydantic 擋成 422
    end: time
    by: str

def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end  # 半開區間：10:00-11:00 和 11:00-12:00 不重疊

@app.post("/bookings", status_code=201)
def create(b: BookingIn):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in BOOKINGS:
        if x["room"] == b.room and overlaps(b.start, b.end, x["start"], x["end"]):
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    booking = {"id": len(BOOKINGS) + 1, **b.model_dump()}  # 還沒有刪除功能；加上取消時要換掉
    BOOKINGS.append(booking)
    return booking

@app.get("/bookings")
def list_bookings():
    return BOOKINGS

@app.post("/reset")
def reset():
    BOOKINGS.clear()
```

```bash
APP=fixed.app python -m pytest -q probes
python -W ignore -c 'from fastapi.testclient import TestClient; from fixed.app import app; r = TestClient(app).post("/bookings", json={"room": "Taipei", "start": "9:30", "end": "10:30", "by": "Alice"}); print(r.status_code, r.json()["detail"][0]["msg"])'
```

```
.....                                                                    [100%]
5 passed in 0.11s
422 Input should be in a valid time format, input is too short
```

B2 有兩種合理修法，選哪個是**契約決定**，不是技術決定：在邊界嚴格要求 `HH:MM`（我們選這個；pydantic 2.14 的 `time` 對 `9:30` 回 `time_parsing` 錯誤），或把 `9:30` 正規化成 `09:30` 再收下（稍後的真實 agent 選了這個）。兩者都不會雙重預約，差在使用者打 `9:30` 時看到什麼。

## 底層機制：為什麼「能跑」不會告訴你「對不對」

```
 vibe 迴圈：只有「會丟錯的 bug」進得了回饋
   prompt ─▶ code ─▶ 跑跑看 ─▶ 有錯誤訊息？ ─ 有 ─▶ 貼回去，再生成一次（回到 code）
                                  └─ 沒有 ─▶「做完了」

 工程迴圈：任何「違反規則的行為」都進得了回饋
   規則 R1、R2 ─▶ 探針（可執行的判準）─┐
   prompt ─▶ code ─────────────────────┴─▶ 跑探針 + 讀 diff ─ 紅 ─▶ 回饋給 agent 或自己修（回到 code）
                                                          └─ 綠且審過 ─▶ 合併
```

**機制一：回饋只收得到會丟錯的東西**。把 vibe 迴圈看成一個最佳化過程，它的目標函數是「沒有錯誤訊息」。四個 bug 裡，B1 回 400、B2 和 B3 回 201、B4 回 400：沒有一個讓程式崩潰或印出 traceback，兩個 400 帶著 `Room already booked for this time` 這種看起來完全合理的訊息，沒有一個像是「出錯了、可以貼回去」的錯誤訊息。它們**全都滿足 vibe 迴圈的目標函數**，迴圈跑得越久，它們活得越穩。「把錯誤貼回去」修得到會說話的 bug，修不到安靜的那種。

**機制二：prompt 沒寫的慣例，由先驗補上**。「不能重複預約」沒說區間是閉的還是半開的，沒說時間用什麼型別比較。模型（或趕時間的人）只能用自己熟悉的某種寫法補：`start1 <= end2 and start2 <= end1` 是閉區間重疊的標準公式，在它自己的前提下完全正確，只是和我們的規格不同；稍後的真實 agent 則猜了半開。猜對猜錯，都不是你決定的。探針的作用是把這些慣例**釘死**，讓任何一份實作都得面對同一組判準。

**機制三：同源的測試只證明自己和自己一致**。`test_smoke.py` 斷言 `400`，因為實作回 `400`；它的判準就是實作本身。把同一組 smoke test 換到正確的版本上：

```bash
sed 's/from vibe.app/from fixed.app/' vibe/test_smoke.py > fixed/test_smoke_copy.py
python -m pytest -q --tb=line fixed/test_smoke_copy.py
```

```
.F                                                                       [100%]
…（略）
/private/tmp/lab/aibdd/ch02-vibe/fixed/test_smoke_copy.py:12: assert 409 == 400
…（略）
1 failed, 1 passed in 0.09s
```

它在錯的實作上綠、在對的實作上紅；第二個測試還依賴第一個留下的狀態，用 `-k double` 單獨跑會得到 `assert 201 == 400`。判準從哪裡來，比有沒有測試重要得多。每個沒被驗證的假設都是一個可能出錯的步驟，步驟越多錯越會疊加，[ai_applications Ch 0](../ai_applications/00-mental-models.md) 的「誤會 4」用 agent 的步數算過同一筆帳。

## 一次真實的生成：現代 agent 會犯這些錯嗎？

前面的 vibe 版本是手寫的模仿，有人會說那是稻草人。所以我們用真的 agent 跑一次：給它一段典型的 vibe prompt，**不讀它的 code**，直接拿探針審。`agent/prompt.txt`：

```
Build a small FastAPI app in app.py for booking meeting rooms (Taipei, Tokyo, Seoul).
POST /bookings takes JSON {room, start, end, by} with times like 10:00 and must not allow double bookings.
GET /bookings lists bookings, POST /reset clears them for tests.
Keep everything in memory and return the created booking with an id. Do not ask questions; write the file.
```

```bash
cd /tmp/lab/aibdd/ch02-vibe/agent
claude -p "$(cat prompt.txt)" --safe-mode --tools "Read,Write" --allowedTools "Read,Write" \
  --max-turns 6 --max-budget-usd 2 --no-session-persistence --output-format json > run.json 2> run.err
```

`--safe-mode` 關掉 CLAUDE.md、skills、plugins、hooks 等所有客製化；`--tools "Read,Write"` 只給讀寫檔、不給 Bash，它跑不了任何東西，處境和「不驗證」的 vibe coder 一樣；`--allowedTools` 讓這兩個工具不必逐次詢問；`--max-budget-usd 2` 是花費上限（美元，依 Claude Code 的估算）；`--no-session-persistence` 不把這次 session 存檔；`--output-format json` 把結果寫成一份可解析的 JSON。`--max-turns` 沒有列在 2.1.295 的 `claude --help` 裡，但 [CLI reference](https://code.claude.com/docs/en/cli-reference) 有它（限 print mode，到上限就以錯誤結束）。`run.err` 只有一行：`Warning: no stdin data received in 3s, proceeding without it. If piping from a slow command, redirect stdin explicitly: < /dev/null to skip, or wait longer.` 我們沒有重導 stdin，它等了 3 秒，加上 `< /dev/null` 可以省掉。

> 以下為實際執行紀錄（節錄），每次執行結果不同。執行日期 2026-11-02。你自己跑出來的 `agent/app.py` 會不一樣，本節和「進階」裡對 `agent.app` 的結果也會跟著不同。

`run.json` 摘要：`subtype` 為 `success`，`num_turns` 3，`duration_ms` 24633；主模型 `claude-opus-5-5` 輸出 2,729 tokens，另有少量 `claude-haiku-5-5` 用量（輸入 1,299、輸出 17 tokens；JSON 只按模型加總，沒寫用途）；`total_cost_usd` 約 0.10，這是 Claude Code 依牌價的客戶端估算，不是帳單。agent 的收尾訊息列出了它替我們做的決定：

```
The spec left these open, so I decided:
- **Back-to-back bookings:** these are allowed, so 10:00–11:00 and 11:00–12:00 don't conflict.
- **Time format:** times use the 24-hour clock, and `9:00` is accepted and stored as `09:00`. Bookings can't cross midnight, and `24:00` is rejected, so the latest possible end time is `23:59`.
- **Single day:** bookings have no date, so each room has one shared day's schedule.
- **Room names:** these are case-sensitive, so `taipei` is rejected.
- **IDs:** these are integers starting at 1, and `/reset` restarts them at 1 so tests get predictable IDs.
```

它寫的 `agent/app.py` 把時間在邊界補零正規化（註解寫著 `Zero-padding makes the strings safe to compare`），重疊用半開區間，R2 用 pydantic 的 `model_validator`，會議室用 `Literal["Taipei", "Tokyo", "Seoul"]` 限定。同一份探針，不改一行：

```bash
cd /tmp/lab/aibdd/ch02-vibe && APP=agent.app python -m pytest -q --tb=short probes
```

```
.F..F                                                                    [100%]
…（略）
E     - Taipei is taken 10:00-11:00 by Bob
E     + Taipei is already booked from 10:00 to 11:00
…（略）
E   AssertionError: assert [{'type': 'value_error', 'loc': ['body'], 'msg': 'Value error, end must be after start', 'input': {'room': 'Taipei', 'start': '11:00', 'end': '10:00', 'by': 'Alice'}, ...}] == 'end must be after start'
…（略）
2 failed, 3 passed in 0.12s
```

這次生成比我們的模仿版好得多：B1 到 B3 一個都沒有，B4 只剩一半（409 對了，錯誤訊息的內容與格式是它自己挑的），還主動列出它的決定。剩下的紅燈與決定**全落在人該擁有的東西上**：

| 紅燈或決定 | 規格怎麼說 | 誰的責任 |
|---|---|---|
| 衝突訊息沒點名 Bob | R1 要點名誰佔用 | 人：規格寫了，但 prompt 沒帶給它 |
| R2 的 `detail` 是 pydantic 錯誤清單 | R2 要字串 `end must be after start` | 同上 |
| `9:00` 收下並存成 `09:00` | 工程版選擇擋成 422 | 產品：使用者打 9:30 時該看到什麼 |
| 未知會議室、小寫 `taipei` 回 422 | 規格沒說 | 產品（[Ch 8](./08-translation-sandwich.md)、[Ch 18](./18-clarify-skill.md) 的題目） |

這是一次執行（n=1），換一次、換個模型，結果都可能不同。所以結論不是「agent 會犯這些錯」或「不會犯」，而是：**不跑探針，你不知道這次是哪一種**。如果照 Karpathy 的方式 Accept All，那五條決定就靜靜地成了規格。

## 證據：AI 到底讓人快了沒有

「AI 讓我變快了」是另一個靠感覺的判斷。兩份隨機對照試驗（randomized controlled trial, RCT）常被拿來對打，[SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) 的「三、METR 的 19% 變慢」與「四、Peng et al. 的 55.8% 加速」有完整拆解。這裡列出和本章有關的部分，並補上一份 SDD Ch 40 沒提到的 METR 更新。表裡的 CI 是 95% 信賴區間（confidence interval），可以粗略讀成「真實效果大致落在這個範圍」；區間跨過 0，就代表連方向都還不確定：

| | Peng et al.（2023） | METR（2025） | METR 更新（2026-02-24） |
|---|---|---|---|
| 受試者 | 在 Upwork 招募，95 人接受（45 實驗組、50 對照組） | 16 位資深 OSS 開發者，平均約 5 年 repo 經驗 | 57 位開發者、143 個 repo、800 多個任務 |
| 任務與工具 | 從零用 JavaScript 寫 HTTP server；GitHub Copilot | 自己維護的成熟專案裡 246 個任務；主要是 Cursor Pro + Claude 3.5/3.7 Sonnet | 同樣的設計：自己的任務逐一隨機分組；2025 年 8 月起，用當時最新的工具 |
| 結果 | 完成任務者平均 71.17 對 160.89 分鐘，完成時間少 55.8%（95% CI 21%–89%） | 完成時間多 19%（CI +2% 到 +39%） | 完成時間：回鍋者 −18%（CI −38% 到 +9%）、新招募者 −4%（CI −15% 到 +9%）；負值代表變快 |
| 但書 | 成功率高 7 個百分點，不顯著 | 開發者事前預期快 24%，事後仍覺得快了 20% | METR 自己說訊號不可靠：不想放棄 AI 的開發者拒絕參加，加速的估計可能偏低 |

三個能帶走的結論，都不超過論文本身支持的範圍：

1. **情境決定效果**：從零寫一個定義清楚的小東西，和在自己維護多年的大專案裡改東西，是兩種工作。兩份 RCT 方向相反，不代表其中一份錯了。
2. **感覺和測量可以完全相反**：METR 2025 的受試者慢了 19%，卻覺得快了 20%。對「我寫對了」的感覺，沒有理由比對「我變快了」的感覺更可靠——這是我們要探針的原因。
3. **測量本身很難**：METR 2026 的方向變成「可能變快」，但作者自己的圖標題就寫著「selection effects obscure the true speedup」，並且正在改研究設計；只引 −18% 而不引但書，就是誤用。這幾份研究都沒有測 SDD 或 BDD，也不能拿來論證它們。

## 對比與取捨

| 做法 | 寫得快嗎 | 你知道它對不對嗎 | 適合 | 不適合 |
|---|---|---|---|---|
| 手寫 | 慢 | 知道你測過的部分 | 核心邏輯、正在學的領域 | 大量樣板、一次性腳本 |
| vibe coding（不讀、不驗） | 最快 | 只知道「沒丟錯」 | 可丟棄的原型、低風險的個人工具 | 會被別人依賴、碰到錢或個資、要維護超過一個月 |
| AI 同時寫 code 與測試，人不審 | 快 | 只知道 code 和它的測試彼此一致 | 幾乎沒有 | 任何把綠燈當成驗收的場合 |
| AI 輔助工程（探針 + 審 diff） | 快，多了寫探針與讀 diff 的時間 | 知道探針和審查涵蓋的部分 | 要上線、要維護、多人協作 | 還在探索、連規則都寫不出來的時候 |

最後一欄的「還在探索」很重要：規則寫不出來時，先 vibe 幾輪是合理的，[SDD Ch 42](../spec_driven_development/42-when-not-to-use-sdd.md) 的「vibe coding 不是壞習慣」與「決策流程圖」給了四個判斷問題。錯的是把探索出來的東西不經審查就升格成產品。

## 踩雷集錦

1. **「agent 讀得到整個 repo，所以它知道我們的慣例」** → 它知道這次 session 讀到的東西。半開區間、409 要點名誰佔用，如果沒寫在任何它會讀到的地方，它只能猜；本章的真實 agent 就猜了一個不點名的訊息。把慣例寫進它會讀的檔案（Ch 3 的 CLAUDE.md、[Ch 9](./09-rule-files.md) 的 rule file），或直接寫成探針。
2. **「測試都綠了，就是做完了」** → 本章的 smoke test 在錯的版本上全綠，在對的版本上反而紅（`assert 409 == 400`）。從實作推出來的測試只證明實作和自己一致。看到綠燈先問：這個測試的判準是誰定的？
3. **「context 給越多越好」** → Anthropic 的〈[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)〉（2025-09-29）寫道：「as the number of tokens in the context window increases, the model's ability to accurately recall information from that context decreases」。本章最有效的 context 是 5 個探針、41 行；[Ch 4](./04-prompt-files-to-skills.md) 會實測常駐 context 的成本。
4. **「把錯誤訊息貼回去，AI 就會修好」** → 只對會丟錯的 bug 有效。本章四個 bug 沒有一個讓程式崩潰，它們安靜地回 201 或 400。沒有錯誤訊息，就沒有東西可以貼。
5. **「vibe coding 就是不專業」** → Karpathy 自己把它限定在可丟棄的週末專案，Willison 列出它可以用的條件。問題不在 vibe，在於把 vibe 出來的東西當成審過的東西。

## 進階：用一個慢但一眼看得懂的 oracle 窮舉

五個探針是我們**想得到**的五個例子。想不到的呢？差分測試（differential testing）的做法是：找一個慢、但正確性一眼看得出來的參考判準，對大量輸入逐一比對。這裡的參考判準是重疊的定義本身——「兩段時間有沒有共用任何一分鐘」，用分鐘集合的交集算，不需要任何邊界推理。`oracle/exhaustive.py`：

```python
"""exhaustive.py：把預約 API 當黑盒，窮舉「Bob 先訂 A、Alice 再訂 B」的所有組合，
和「兩段時間有沒有共用任何一分鐘」這個慢但一眼看得懂的 oracle 比對。"""
import argparse, importlib, sys, warnings  # noqa: E401
from collections import Counter

warnings.filterwarnings("ignore", message="Using `httpx`")  # starlette 1.10 的棄用提醒，與本章無關
from fastapi.testclient import TestClient  # noqa: E402

ap = argparse.ArgumentParser(description="Differential check of a booking API against a minute-set oracle.")
ap.add_argument("--app", required=True, help="module that exposes a FastAPI `app`, e.g. vibe.app")
ap.add_argument("--step", type=int, default=60, help="grid step in minutes between 08:00 and 20:00")
ap.add_argument("--format", choices=["padded", "unpadded"], default="padded", help="send 09:00 or 9:00")
args = ap.parse_args()
client = TestClient(importlib.import_module(args.app).app)
pad = "02d" if args.format == "padded" else "d"
fmt = lambda m: f"{m // 60:{pad}}:{m % 60:02d}"  # noqa: E731
points = range(8 * 60, 20 * 60 + 1, args.step)
slots = [(s, e) for s in points for e in points if s < e]
tally, example = Counter(), {}
for a in slots:
    for b in slots:
        client.post("/reset")
        bob, alice = [client.post("/bookings", json={"room": "Taipei", "by": who, "start": fmt(s), "end": fmt(e)})
                      .status_code for who, (s, e) in (("Bob", a), ("Alice", b))]
        overlap = bool(set(range(*a)) & set(range(*b)))  # oracle：有沒有哪一分鐘同時被兩人佔用
        if bob != 201:
            verdict = ("first booking rejected", bob)
        elif (alice == 201) == overlap:  # 收下了重疊的，或擋下了不重疊的
            verdict = ("double booking" if overlap else "false conflict", alice)
        else:
            continue
        tally[verdict] += 1
        example.setdefault(verdict[0], f"Bob {fmt(a[0])}-{fmt(a[1])}, Alice {fmt(b[0])}-{fmt(b[1])} -> {bob}/{alice}")
for (kind, code), n in sorted(tally.items()):
    print(f"{kind} (HTTP {code}): {n}   e.g. {example[kind]}")
print(f"{args.app} [{args.format}, step {args.step}]: {len(slots) ** 2} pairs, {tally.total()} disagreements")
sys.exit(1 if tally else 0)
```

範圍是 08:00 到 20:00、每 60 分鐘一格：13 個時間點、78 段時段、78 × 78 = 6084 組有序配對，每組 3 個請求，在這台機器上一次跑 15 到 17 秒；有不一致時 exit 1。要用 `python -m oracle.exhaustive` 執行，實驗目錄才會在 `sys.path` 上（`python oracle/exhaustive.py` 會報 `ModuleNotFoundError: No module named 'vibe'`）。`agent.app` 那兩行來自我們那次生成的檔案；沒跑 `claude -p` 的話把它從迴圈拿掉，否則會得到 `ModuleNotFoundError: No module named 'agent.app'`：

```bash
for app in vibe.app fixed.app agent.app; do for f in padded unpadded; do
  python -m oracle.exhaustive --app $app --format $f; done; done
```

```
false conflict (HTTP 400): 572   e.g. Bob 08:00-09:00, Alice 09:00-10:00 -> 201/400
vibe.app [padded, step 60]: 6084 pairs, 572 disagreements
double booking (HTTP 201): 2046   e.g. Bob 8:00-9:00, Alice 8:00-10:00 -> 201/201
false conflict (HTTP 400): 330   e.g. Bob 10:00-11:00, Alice 11:00-12:00 -> 201/400
vibe.app [unpadded, step 60]: 6084 pairs, 2376 disagreements
fixed.app [padded, step 60]: 6084 pairs, 0 disagreements
false conflict (HTTP 422): 495   e.g. Bob 10:00-11:00, Alice 8:00-9:00 -> 201/422
first booking rejected (HTTP 422): 1794   e.g. Bob 8:00-9:00, Alice 8:00-9:00 -> 422/422
fixed.app [unpadded, step 60]: 6084 pairs, 2289 disagreements
agent.app [padded, step 60]: 6084 pairs, 0 disagreements
agent.app [unpadded, step 60]: 6084 pairs, 0 disagreements
```

- **vibe／補零：572 筆誤擋**，剛好是所有「相鄰」配對。把 13 個時間點編號為 0（08:00）到 12（20:00）：在第 k 點交接的兩段，前段的起點有 k 種選法、後段的終點有 12 − k 種，共 k × (12 − k) 種；k 從 0 加到 12 是 286，Bob 那段在前或在後兩種順序，乘 2 得 572。補零時字串順序等於時間順序，只剩 B1。
- **vibe／不補零：2046 筆雙重預約**，全都牽涉 8 點或 9 點（例子裡兩段都從 8:00 開始，照樣雙重預約）。誤擋反而降到 330：只剩兩段都在 10:00 以後的相鄰配對，同樣的算法換成 10:00 到 20:00 的 11 個點是 2 × 165 = 330。另外 242 筆相鄰配對被 B2「意外地」放行了——**兩個 bug 互相遮蔽**；補零那次的 572 就是 B2 不起作用時 B1 的真實規模。
- **fixed／不補零：2289 筆「不一致」全是 422**，雙重預約是 0。這是我們選的嚴格 `HH:MM` 契約，不是 bug；工具不知道我們的契約，所以照樣報出來——差分測試也需要人提供契約。**agent 兩種格式都是 0**，和它「把 9:00 正規化」的決定一致。

為什麼不用 property-based testing（隨機產生大量輸入、檢查某個性質永遠成立，Python 的代表是 Hypothesis）？在 13 個時間點這麼小的空間裡，窮舉比取樣更強，也不用另外裝套件。格子切細，數量長得很快：`--step 30` 有 25 個點、300 段時段、90,000 組配對，約是現在的 15 倍。「大部分 bug 在很小的範圍內就會出現」這個小範圍假說，在 [pstack Ch 21](../pstack/21-verification-skills.md) 的有界窮舉裡有另一個例子。

## 動手練習

1. **先寫探針再寫 code**：加入 R3「辦公時間 08:00–20:00，超出回 422 `outside office hours 08:00-20:00`」。先在 `probes/` 加一條探針，對 `fixed.app` 跑出紅燈，再改實作到綠。動手前先預測：`exhaustive.py` 的結果會變嗎？（提示：格子範圍是 08:00 到 20:00，而區間是半開的。）
2. **審你自己的 agent**：把本章的 `prompt.txt` 交給你常用的 agent，產出放進新目錄後跑 `APP=<你的模組>.app python -m pytest -q probes`。把每個紅燈分成三類：bug、它不可能知道的契約、它替你做的決定。
3. **一個規格沒回答的問題**：替 `Taipie`（打錯字的會議室）寫一條探針。你會發現自己不知道該斷言什麼——工程版收下它，agent 版回 422。把問題寫下來交給 PM，而不是挑一個順手的答案（Ch 8 與 Ch 18 會把這件事做成流程）。
4. **先預測再執行**：`python -m oracle.exhaustive --app vibe.app --step 30`。執行前寫下你預期的配對數與誤擋數，跑完對答案。

## 本章重點整理

- AI coding 有兩條軸：自主程度（AI 一步做多少）與驗證強度（誰、用什麼確認）。vibe coding 是驗證軸的下半部，不是自主程度的一端。
- Karpathy 的原義是 Accept All、不讀 diff、限可丟棄的專案；Willison 的界線是審過、測過、能解釋；Collins 的寬義把「不審查」拿掉了。討論前先對齊是哪一種。
- 五件事一直歸人：契約、判準、理解、決策的最後一票、責任與邊界。vibe 迴圈的回饋只收得到會丟錯的 bug；本章四個 bug 都安靜地回 201 或 400。驗收探針把規則變成可執行的判準，同源的測試則只證明自己和自己一致。
- 一次真實生成避開了 B1 到 B3，剩下的紅燈都是錯誤訊息的內容與格式，落在契約與決策上。n=1，結論是「不跑探針就不知道」。
- METR 與 Peng 的 RCT 方向相反：情境決定效果，感覺不等於測量；兩者都沒測 SDD 或 BDD。

## 自我檢核

- [ ] 不看本章，說出 Karpathy 原文描述的三個具體行為與他給的適用範圍，以及 Willison 的界線看哪三件事；Collins 的定義為什麼讓這條線消失？
- [ ] vibe 版本的四個 bug 為什麼在「把錯誤貼回去」的迴圈裡永遠不會出現？用「目標函數」解釋。
- [ ] `9:30` 的 bug 為什麼在 `09:30` 的輸入下不會出現？這對你挑測試資料有什麼影響？
- [ ] AI 順手寫的 smoke test 為什麼在正確的實作上反而紅？如果是你寫測試，判準要從哪裡來？
- [ ] 真實生成的兩個紅燈屬於哪一類問題？為什麼「換更強的模型」解決不了它們？
- [ ] 不補零時誤擋從 572 降到 330，少掉的 242 筆去了哪裡？這對「修一個 bug 後紅燈反而變多」有什麼啟示？

## 延伸閱讀

- **[Not all AI-assisted programming is vibe coding (but vibe coding rocks)](https://simonwillison.net/2025/Mar/19/vibe-coding/)** — Simon Willison（2025-03-19）
  - **讀哪裡**：開頭引用的 Karpathy 推文全文（推文本身在 X 上，這篇是最方便的全文轉引），以及「When is it OK to vibe code?」一節的四個條件：low stakes、考慮安全性、當個好的網路公民、別把自己的錢押上去。
  - **學到什麼**：本章採用的那條界線的原始出處，以及「vibe coding 很好玩，但要知道它的邊界」的完整論證。
  - **和本章的關聯**：「人還擁有什麼」第 3、5 點的來源。
- **[Vibe engineering](https://simonwillison.net/2025/Oct/7/vibe-engineering/)** — Simon Willison（2025-10-07）
  - **讀哪裡**：整篇，特別是讓 coding agent 好用的那 12 項工程習慣，例如自動化測試、事先規劃、完整的文件、版本控制、code review 文化、認真的手動 QA。
  - **學到什麼**：光譜另一端的工作方式長什麼樣子，以及為什麼資深工程師反而從 agent 得到更多。
  - **和本章的關聯**：本章「第二次看」是清單裡「自動化測試」與「code review」的最小版本；Ch 3 起再把事先規劃、寫成文件的慣例、自動化的回饋這幾項做成可執行的形式。
- **[Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://arxiv.org/abs/2507.09089)** — Becker、Rush、Barnes、Rein（METR，2025），以及後續的 **[We are Changing our Developer Productivity Experiment Design](https://metr.org/blog/2026-02-24-uplift-update/)**（2026-02-24）
  - **讀哪裡**：論文摘要與「預測對照實際」的結果；更新文解釋新資料為什麼不可靠的那一段。
  - **學到什麼**：一個嚴謹的 RCT 長什麼樣，以及研究者自己怎麼承認測量出了問題。
  - **和本章的關聯**：「感覺和測量可以相反」的證據；也示範了引用數字時連同但書一起引用。
- **[The Impact of AI on Developer Productivity: Evidence from GitHub Copilot](https://arxiv.org/abs/2302.06590)** — Peng、Kalliamvakou、Cihon、Demirer（2023）
  - **讀哪裡**：實驗設計與結果段落（71.17 對 160.89 分鐘、信賴區間、成功率不顯著）。
  - **學到什麼**：55.8% 這個常被引用的數字是在什麼條件下量到的。
  - **和本章的關聯**：和 METR 對照，說明情境決定效果；也提醒你實驗做在 2022 年，用的是當時的補全工具，不是今天的 agent。

第二次看的時候，我們做的其實是一個迴圈的雛形：先把規則變成可執行的判準，再讓 code（不管誰寫的）去面對它。下一章把這個迴圈正式化：在動工之前就把判準交給 agent，用約束框住它能碰的範圍，再讓測試結果自動回到它眼前。

→ [Ch 3 最小的 AI coding 迴圈：提案、約束、回饋](./03-propose-constrain-feedback.md)
