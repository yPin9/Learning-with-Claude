# Ch 15 — 把 /plan 拆開：technical-research 與 system-analysis skill

> **目標**：說得出為什麼要把 Spec Kit 的 `/speckit-plan` 拆成單一職責的 planner，以及一份 planner 合約要寫清楚的三件事：讀什麼、只寫哪一個檔、哪支檢查算完成。寫出兩個 planner skill 與它們的完成檢查：`technical-research` 把 plan 裡帶 ID 的開放問題變成有證據的 `research.md`；`system-analysis` 用 `inventory.py` 盤點端點，greenfield（還沒有程式）從 spec 的 FR 推，brownfield（已經有程式）從 FastAPI 路由表與頁面裡的 `fetch(` 掃，抓出前端呼叫了、後端卻沒有的路由。最後用一個 parent skill 依檢查結果決定重跑誰、合併回 `plan.md`，並從三次真實執行看清楚這樣拆的成本與失敗方式。

> **環境**：Python 3.12.15 與 [Ch 0](./00-environment-setup.md) 的課程 venv（FastAPI 0.162.0、pydantic 2.14.2、SQLite 3.53.1）、Spec Kit 1.2.5（只讀它裝好的 skill 與模板）、Claude Code 2.1.295，macOS 26.6 arm64，shell 是 zsh。lab 在 `/tmp/lab/aibdd/ch15-planners/`，指令都在其中的 `project/` 執行、venv 已 activate，`bad/` 與 `project/` 同層。自己重做時從空目錄開始，照各區塊前寫的路徑建檔；001 的 `plan.md` 至少要有第二節那三行，`spec.md` 要有第三節那六條 FR（每條一行 `- **FR-00N**: …`），兩支檢查只讀這些行，輸出會和本章相同。腳本輸出是 2026-11-02 實際跑出來的；三段 agent 紀錄來自真實的 `claude -p`，每次結果不同。

## 為什麼需要這個？

[Ch 13](./13-plan-anatomy-review.md) 拆解過 `/speckit-plan` 的產物，這一章看它的**結構**。Spec Kit 1.2.5 裝進 `.claude/skills/speckit-plan/SKILL.md` 的第 3 步，要 agent 在一次呼叫裡做完六件事：填 Technical Context（未知數標成 `NEEDS CLARIFICATION`）、填 Constitution Check、評估 gate、Phase 0 產 `research.md`、Phase 1 產 `data-model.md`／`contracts/`／`quickstart.md`、設計完再檢一次 constitution。五種產物共用一份 `Done When`，第一條是「Plan workflow executed and design artifacts generated」，沒有一條是腳本能判斷內容的。這種結構的後果，[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) 的參考 reflection.md（一份範本，不是實測紀錄）在「哪裡 over-engineer」一節寫得很具體：`research.md` 列了五個 email 服務，而 constitution 早就要求最小外部依賴；plan 還替一個沒有對外 API 的功能產了 `api-spec.json`。兩個錯是同一種病：research 回答了沒人問的問題，contracts 描述了不存在的介面，流程裡沒有一步會因此失敗。

這些工作在 agent 出現前就是分開做的：極限程式設計（Extreme Programming，XP）用 spike，一段丟棄式程式回答一個技術問題；Michael Nygard 2011 年提倡架構決策紀錄（Architecture Decision Record，ADR），一個決策一份短文件；整合幾個系統之前先列介面清單。Spec Kit 把它們壓進一個指令，好處是好裝、好示範；[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md) 自建流水線時把 SPEC、PLAN、TASKS 分成獨立環節，PLAN 本身仍是一個。本章把 [AI 應用 Ch 15](../ai_applications/15-skills.md)「反例 1:一個 skill 做十件事」的教訓用到規劃上。

## 先建立直覺：分圖、簽證、套圖

蓋一棟樓，建築師畫平面、結構技師算梁柱、水電技師排管線，三套圖各自送審、各自簽證；開工前再「套圖」，把三套疊在一起找風管穿過大梁的地方。沒有人請一位技師一次畫完三套，因為那樣沒有人能單獨審任何一套，衝突也全在同一個人腦子裡被悄悄「處理掉」。

```
  spec.md · constitution · plan.md 的 NEEDS CLARIFICATION [DEC-NN]
         │                              │
         ▼                              ▼
  technical-research             system-analysis             data-model、quickstart：本章不拆，
  spike／文件 → research.md       盤點 → inventory.md          留在 /speckit-plan；contracts 已由
  檢查：research_check.py        檢查：inventory.py check     Ch 14 的 contract-writer 負責
         └──────────────┬───────────────┘
                        ▼
         plan-feature（parent）：閘門決定重跑誰 → 合併進 plan.md → ## Conflicts（套圖）
```

這個類比有一處不成立：建築各科有不同的專業執照，我們的 planner 是同一個模型拿到不同指示。拆開換到的不是專業知識，是三樣東西：每個 planner 的 context 只裝它要的；每份產物能單獨審、單獨重跑；每份產物有一支會失敗的檢查。如果你對 skill 的載入方式（description 常駐、本文用到才載入）還不熟，先回看 [Ch 4](./04-prompt-files-to-skills.md)；「先定產物與檢查，再寫 skill」的做法在 [Ch 14](./14-artifact-first.md)。

## 一、planner 的合約

| planner | 讀 | 只寫 | 完成檢查（印出 `0 problems`） |
|---|---|---|---|
| `technical-research` | spec.md、plan.md 的 `[DEC-NN]`、constitution | research.md（與 `spikes/`） | `research_check.py research.md --plan plan.md` |
| `system-analysis` | spec.md；brownfield 再加 `app/` 與頁面 | inventory.md | `inventory.py check inventory.md --spec spec.md`，brownfield 加 `--app`、`--html` |
| `plan-feature`（parent） | 前兩份產物與檢查結果 | plan.md | 不剩 `NEEDS CLARIFICATION`，兩支檢查都 `0 problems` |

合約三欄，每一欄都要寫到能被檢查。**只寫一個檔**是為了能單獨重跑：同時寫 research.md 和 data-model.md 的 planner，重跑時會蓋掉有人手改過的另一份。**問題帶 ID** 讓「回答了哪些問題」變成可比對的事實：plan.md 寫 `NEEDS CLARIFICATION [DEC-02] …`，research.md 用 `## DEC-02` 回答，兩邊集合必須相等，沒回答的和沒人問的（練習 E 那五個 email 服務）都抓得到。要不要拆也從這裡判斷：**說不出一支能單獨失敗的檢查，就先不要拆**，否則只是多一次交接。data-model 與 quickstart 還說不出這種檢查，contracts 在 Ch 14 已有 `contract_check.py` 與只寫契約的 `contract-writer`，本章因此只再拆兩個。

## 二、technical-research：開放問題 → 證據 → research.md

起點是 001-booking 的 `plan.md` 與專案的 constitution。constitution 只有三條：I 小技術棧，不加資料庫伺服器、queue、cache 這類 runtime service；II 每條規則先有可執行的例子；III 錯誤訊息要說怎麼修。plan 的 Technical Context 裡三個未知數各帶一個 ID（節錄；`Input format` 是我們加的欄位，1.2.5 的 plan-template 註明那段結構是「presented in advisory capacity」）：

```markdown
**Storage**: NEEDS CLARIFICATION [DEC-01] where do bookings live so that they survive a restart (FR-006)?
**Constraints**: NEEDS CLARIFICATION [DEC-02] how does R1 hold when two requests book the same slot at the same moment?
**Input format**: NEEDS CLARIFICATION [DEC-03] which time format does the API accept, and does "9:00" pass?
```

skill 本體 `.claude/skills/technical-research/SKILL.md`：

```markdown
---
name: technical-research
description: Answer a feature plan's open technical questions with evidence and write the decisions to research.md. Use when plan.md has NEEDS CLARIFICATION [DEC-NN] items, or when asked to research options for a feature under specs/.
argument-hint: "<feature dir, e.g. specs/001-booking>"
allowed-tools: Read Grep Glob Bash(python spikes/*) Bash(python .claude/skills/technical-research/scripts/research_check.py *)
---

# technical-research

One job: turn the open technical questions in `$0/plan.md` into decisions backed by evidence. Do not design endpoints, data models or tasks; other planners own those.

## Contract

- Reads: `$0/spec.md`, `$0/plan.md`, `.specify/memory/constitution.md`
- Writes: `$0/research.md`; spikes as `spikes/<name>.py` with their output in `spikes/<name>.out`
- Done when: `python .claude/skills/technical-research/scripts/research_check.py $0/research.md --plan $0/plan.md` prints `0 problems`

## Steps

1. Collect the questions: every `NEEDS CLARIFICATION [DEC-NN]` in plan.md. Answer exactly these. A question you think is missing goes in your final message, not in research.md.
2. For each question, list options that change the shape of the solution, not two spellings of one option. Drop an option the constitution forbids and name the article.
3. Get evidence for the option you pick and for the strongest loser. Prefer a spike when the question is about behaviour you can run in minutes: write it, run it with `python spikes/<name>.py > spikes/<name>.out`, quote the numbers. Otherwise cite official documentation you have read.
4. Write one entry per question from `templates/entry.md`, in DEC order.
5. Run the done check; fix and re-run until it prints `0 problems`. Do not edit plan.md: the parent planner merges decisions.
```

`allowed-tools` 只預先核准唯讀工具、跑 spike、跑檢查；Claude Code 文件明講這個欄位「does not restrict which tools are available」，它是免問清單，不是圍欄，寫檔沒有預先核准，在預設權限模式下每次寫入都會問你。`$0` 是第一個參數（`$ARGUMENTS[0]` 的簡寫），同一個 skill 能用在任何 feature 目錄。第 1 步禁止自己加題目；第 2 步要求改變解法形狀的選項，即 [pstack Ch 18](../pstack/18-architect-arena.md)「三、architect」的 design it twice；第 5 步不准碰 plan.md，合併是 parent 的事。這些欄位是照 [Ch 14](./14-artifact-first.md) 的做法反推出來的：我們先手寫一份三筆的黃金樣本 research.md（lab 的 `specs/001-booking/research.md`），再抽出模板與檢查。`templates/entry.md` 是每筆紀錄的骨架：

```markdown
## DEC-NN <the question in a few words>

- **Question**: <the NEEDS CLARIFICATION text from plan.md, unchanged>
- **Decision**: <what we will do, one sentence>
- **Rationale**: <why, citing FR-xxx, R1-R4 and constitution articles>
- **Alternatives considered**:
  - <option that changes the shape of the solution> — <why it lost>
- **Risks**: <what would make this decision wrong later, and how we would notice>
- **Evidence**: <URL> ; <spikes/<name>.py> ; <spikes/<name>.out>
- **Verdict**: feasible | feasible-with-risk | infeasible
```

其中 Decision、Rationale、Alternatives considered 是 Spec Kit Phase 0 原本就要的三欄，Question 把題目原文抄回來，多加的三欄各有用途：Evidence 和 Rationale 分開，review 的人才分得出觀察和推論；Risks 寫什麼會讓決定變錯、怎麼發現，就是 [SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md)「進階延伸」講的 ADR Consequences；Verdict 讓下游不讀全文也知道路走不走得通。**完成檢查**先拿一份一次生成式的 research.md 來試（`../bad/research-oneshot.md`）：

```markdown
## DEC-04 Calendar sync

- **Question**: should bookings sync to Google Calendar?
- **Decision**: yes, through the Google Calendar API.
- **Rationale**: most teams use it.
- **Alternatives considered**:
  - Outlook
- **Risks**: none.
- **Evidence**: well known
- **Verdict**: probably fine
```

跑 `python .claude/skills/technical-research/scripts/research_check.py ../bad/research-oneshot.md --plan specs/001-booking/plan.md; echo "exit=$?"`：

```
DEC-04: alternative 'Outlook' does not say why it lost (use ' — ')
DEC-04: risks say 'none'; name what would make this decision wrong
DEC-04: evidence 'well known' is neither a URL nor an existing file
DEC-04: verdict 'probably fine' is not one of feasible, feasible-with-risk, infeasible
DEC-01: asked in specs/001-booking/plan.md but has no entry
DEC-02: asked in specs/001-booking/plan.md but has no entry
DEC-03: asked in specs/001-booking/plan.md but has no entry
DEC-04: not asked in specs/001-booking/plan.md; drop it or add the question
1 decision, 3 questions, 8 problems
exit=1
```

八個問題對應幾種典型的偷懶：沒說輸在哪裡的替代方案、`Risks: none`、拿「well known」當證據、拿「probably fine」當結論、三題沒回答、一題沒人問。這支檢查**不判斷**決定對不對，只判斷文件能不能被審：每筆的證據都是一個網址或一個存在的檔案。網址只看開頭是不是 `http(s)://`，不會真的去連，證據寫 `https://no-such-host.invalid/sqlite` 的一筆照樣 `1 decision, 0 problems`；連不連得上、撐不撐得住結論，要人審。檢查本身也要先看它失敗：第一版在同一份檔上只報 6 個問題，`Risks: none` 是規則沒寫，`Outlook` 那條則是 bug，`field()` 先 `strip()` 掉了第一個子項目的縮排，第一版的正規式卻要求子項目前面有空白；我們會發現，是因為事先知道這份壞檔有幾個錯，回頭數了輸出。完整原始碼（stdlib only）：

```python
#!/usr/bin/env python3
"""Done check for technical-research. Each '## DEC-NN' entry in research.md needs Question,
Decision, Rationale, Alternatives considered ('  - <option> — <why it lost>'), Risks (not
'none'), Evidence (';'-separated URLs or existing files) and a Verdict. With --plan, the
entries must answer exactly the [DEC-NN] questions that plan.md asks."""
import argparse, re, sys
from pathlib import Path

FIELDS = ["Question", "Decision", "Rationale", "Alternatives considered", "Risks", "Evidence", "Verdict"]
VERDICTS = ("feasible", "feasible-with-risk", "infeasible")

def field(body, name):
    m = re.search(rf"^- \*\*{name}\*\*:(.*?)(?=^- \*\*|\Z)", body, re.M | re.S)
    return m.group(1).strip() if m else ""

def check_entry(dec, body):
    out = [f"{dec}: missing **{f}**" for f in FIELDS if not field(body, f)]
    alts = re.findall(r"^\s*- (.+)$", field(body, "Alternatives considered"), re.M)
    if field(body, "Alternatives considered") and not alts:
        out.append(f"{dec}: no alternatives listed; add '  - <option> — <why it lost>'")
    out += [f"{dec}: alternative '{a}' does not say why it lost (use ' — ')" for a in alts if " — " not in a]
    if field(body, "Risks").lower().rstrip(".") in ("none", "n/a", "-"):
        out.append(f"{dec}: risks say 'none'; name what would make this decision wrong")
    for item in filter(None, (s.strip() for s in field(body, "Evidence").split(";"))):
        if not item.startswith(("http://", "https://")) and not Path(item).exists():
            out.append(f"{dec}: evidence '{item}' is neither a URL nor an existing file")
    if field(body, "Verdict") not in VERDICTS + ("",):
        out.append(f"{dec}: verdict '{field(body, 'Verdict')}' is not one of {', '.join(VERDICTS)}")
    return out

def n(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("research", type=Path)
    ap.add_argument("--plan", type=Path, help="plan.md whose [DEC-NN] questions research.md must answer")
    args = ap.parse_args()
    parts = re.split(r"^## (DEC-\d\d)\b.*$", args.research.read_text(), flags=re.M)
    ids, entries = parts[1::2], dict(zip(parts[1::2], parts[2::2]))
    problems = [] if entries else [f"{args.research}: no '## DEC-NN' entries"]
    problems += [f"{d}: appears {ids.count(d)} times" for d in entries if ids.count(d) > 1]
    problems += [p for dec, body in entries.items() for p in check_entry(dec, body)]
    summary = n(len(entries), "decision")
    if args.plan:
        asked = set(re.findall(r"\[(DEC-\d\d)\]", args.plan.read_text()))
        problems += [f"{d}: asked in {args.plan} but has no entry" for d in sorted(asked - entries.keys())]
        problems += [f"{d}: not asked in {args.plan}; drop it or add the question" for d in sorted(entries.keys() - asked)]
        summary += ", " + n(len(asked), "question")
    print("\n".join(problems + [f"{summary}, {n(len(problems), 'problem')}"]))
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

**實際執行：讓 agent 從零做一次。** 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。我們刪掉 lab 副本裡 001 的 research.md、inventory.md 與 `spikes/`，在副本根目錄跑 `claude -p "/technical-research specs/001-booking"`，加上 `--setting-sources project --tools "Read,Write,Edit,Glob,Grep,Bash,WebFetch" --permission-prompts none --max-turns 20 --max-budget-usd 3 --no-session-persistence`，`--allowedTools` 只開 `Read`、`Glob`、`Grep`、`Edit(specs/**)`、`Edit(spikes/**)`、`Bash(mkdir spikes)`、`Bash(python spikes/*)`、這支檢查，以及四個文件網站的五條 `WebFetch(domain:…)` 規則。32 turns、179 秒、0.82 美元：它寫了三支 spike（共 338 行）、讀了五份官方文件，最後自己跑完成檢查得到 `3 decisions, 3 questions, 0 problems`。它找到的比我們手寫的黃金樣本多：DEC-02 的 spike 讓 8 個請求搶同一個時段、各跑 30 次，並在檢查與寫入之間刻意停 1 ms 放大競態（它自己註明這是最壞情況）：不加鎖時 30 次全部超賣、最多 8 筆成交；`threading.Lock` 在多執行緒下 30 次全對，換成多個 process（`uvicorn --workers 2` 就是這種情況）又是 30 次全部超賣；`BEGIN IMMEDIATE` 兩種都 30 次只成交 1 筆。我們的黃金樣本只用一句「holds only inside one process」帶過，它用數字證明了。DEC-03 也多查出 pydantic 2.14.2 的 `time` 接受 `"10:00:30"`、整數 `36000` 與帶時區的 `"10:00Z"`；我們拿現有的 app 重驗最後一項，送 `start` 為 `"10:00Z"`、`end` 為 `"11:00"`，得到 `500 Internal Server Error`：帶時區與不帶時區的 `time` 比大小會丟出 `TypeError: can't compare offset-naive and offset-aware times`，回的不是任何一條規則的 4xx。

兩個觀察。合約的「讀」欄沒被遵守：它讀了 `app/`、002 的 spec、甚至 parent 的 SKILL.md；這次讀程式碼讓研究更準（它從 `app/main.py` 知道資料現在存在記憶體），但要記得「讀」只是說明，不是圍欄。3 次權限拒絕（兩次 `python -c`、一次 `ls -la && ls -laR …` 複合指令），每次它都改用允許的方式繼續：`--allowedTools` 收得窄，代價是幾個 turn。

## 三、system-analysis：端點盤點的兩種模式

**greenfield：從 FR 推。** 還沒有程式時，盤點從 spec 的 FR 推。001-booking 的 spec 有六條，每條一行 `- **FR-00N**: …`（檢查只讀這種行）：FR-001 可預約 Taipei、Tokyo、Seoul，同一天、時間寫 HH:MM；FR-002 與同一間的既有預約重疊要拒絕並說出誰占用（R1），相鄰不算重疊；FR-003 結束必須晚於開始（R2）；FR-004 看得到所有預約；FR-005 預約頁面要列出系統知道的會議室；FR-006 伺服器重啟後預約還在。它的 `inventory.md`：

```markdown
# Endpoint inventory: 001-booking

Mode: greenfield (from spec.md; no code yet)

| ID | Status | Caller | Auth | Request | Responses | Owner | Source |
|---|---|---|---|---|---|---|---|
| EP:POST /bookings | new | page: Book form | none; the name is typed | {room, start, end, by}; times HH:MM | 201 booking with id; 409 R1; 422 R2 | backend | FR-001, FR-002, FR-003 |
| EP:GET /bookings | new | page: day list | none | - | 200 list of bookings | backend | FR-004 |
| EP:GET /rooms | new | page: Room select | none | - | 200 ["Taipei", "Tokyo", "Seoul"] | backend | FR-005 |
| EP:GET / | new | browser | none | - | 200 the booking page | web | FR-001, FR-005 |
| EP:POST /reset | new | test suite | test-only, hidden from OpenAPI | - | 200 | backend | internal: isolates tests |

## FRs without an endpoint

- FR-006 (survive a restart): storage, decided in research.md DEC-01

## Mismatches

None: greenfield, there is no code to compare yet.
```

八個欄位回答 planning 時一定會被問的問題：誰呼叫（Caller）、怎麼知道呼叫者是誰（Auth）、送什麼收什麼、誰負責、為了哪條 FR。ID 是全課慣例的 `EP:<METHOD> <path>`，[Ch 16](./16-tasks-binder.md) 的 tasks 直接引用。`templates/inventory.md` 是這個檔的骨架：同一行表頭、同樣兩個 section，格子與條目換成 `<…>` 佔位。檢查好的一份、改壞的一份：

```bash
INV=.claude/skills/system-analysis/scripts/inventory.py
python $INV check specs/001-booking/inventory.md --spec specs/001-booking/spec.md
sed '/^- FR-006/d' specs/001-booking/inventory.md > /tmp/no-fr006.md
python $INV check /tmp/no-fr006.md --spec specs/001-booking/spec.md
```

```
6 FRs, 5 rows, 0 problems
FR-006: not in any row; add one or list it under '## FRs without an endpoint'
6 FRs, 5 rows, 1 problem
```

用 `sed` 把那一列的 ID 改成 `GET /rooms`、Status 改成 `planned`、Source 改成 FR-009，三條規則各報一行（`'GET /rooms': ID must look like 'EP:POST /bookings'` 等，`6 FRs, 5 rows, 3 problems`）。再看一個**該失敗卻通過**的：`sed '/EP:GET \/rooms /d'` 整列刪掉 `EP:GET /rooms` 再檢查，結果是 `6 FRs, 4 rows, 0 problems`。FR-005（頁面要列出系統知道的會議室）仍出現在 `EP:GET /` 那列的 Source，覆蓋規則只要求每條 FR 被提到，所以過了。頁面的資料從哪個端點來是語意問題，腳本答不了；greenfield 只能靠人審，brownfield 才有程式可以對帳。

**brownfield：從程式碼掃。** 時間快轉：001 做出來了，後端和頁面是兩個人寫的。現在要規劃 002（取消預約，R4），先盤點現況。lab 的 app 兩個檔，`app/main.py`：

```python
# app/main.py — feature 001 as built (the backend by one person, the page by another)
from datetime import time
from pathlib import Path
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app, rooms = FastAPI(), APIRouter(prefix="/rooms")
BOOKINGS: list[dict] = []

class Booking(BaseModel):
    room: str
    start: time
    end: time
    by: str

@app.post("/bookings", status_code=201)
def create(b: Booking):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in BOOKINGS:
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    BOOKINGS.append(b.model_dump() | {"id": len(BOOKINGS) + 1})
    return BOOKINGS[-1]

@app.get("/bookings")
def list_bookings(): return BOOKINGS

@rooms.get("/{room}/bookings")
def room_bookings(room: str): return [x for x in BOOKINGS if x["room"] == room]

app.include_router(rooms)

@app.post("/reset", include_in_schema=False)
def reset(): BOOKINGS.clear()

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(): return (Path(__file__).parent / "static" / "index.html").read_text()
```

`app/static/index.html`：

```html
<form id=f><select name=room aria-label=Room></select><input name=start aria-label=Start>
<input name=end aria-label=End><input name=by aria-label=Name><button>Book</button></form>
<p role=status></p><ul id=day></ul>
<script>
const api = '/bookings';
async function loadRooms() {
  const r = await fetch('/rooms');
  const names = r.ok ? await r.json() : ['Taipei', 'Tokyo', 'Seoul'];
  f.room.innerHTML = names.map(n => `<option>${n}</option>`).join('');
}
async function showDay(room) {
  const r = await fetch(`/rooms/${room}/bookings`);
  day.innerHTML = (await r.json()).map(b => `<li>${b.start}-${b.end} ${b.by}</li>`).join('');
}
f.onsubmit = async e => {
  e.preventDefault();
  const r = await fetch(api, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(Object.fromEntries(new FormData(f))),
  });
  const j = await r.json();
  document.querySelector('[role=status]').textContent = r.ok ? 'Booked ' + j.room : 'Rejected: ' + j.detail;
  showDay(f.room.value);
};
loadRooms();
</script>
```

掃描 `python $INV scan --app app.main:app --html app/static; echo "exit=$?"`：

```
| ID | Caller |
|---|---|
| EP:POST /bookings | - |
| EP:GET /bookings | - |
| EP:GET /rooms/{room}/bookings | index.html:12 |
| EP:POST /reset | - |
| EP:GET / | - |
ERROR index.html:7 calls GET /rooms: no backend route
WARN  index.html:17 POST fetch(api): URL is not a string literal; trace it by hand
5 routes, 3 calls, 1 problem, 1 warning
exit=1
```

三種結果三種處置。`ERROR` 是頁面第 7 行呼叫 `GET /rooms`、後端沒有這條路由，這是我們埋的錯：001 的盤點有這一列，後端沒做，頁面在 404 時退回寫死的三間會議室，看起來能用，所以沒人發現。`WARN` 是 `fetch(api, …)` 的網址是變數，靜態掃描解不出來，要人去追；`POST /bookings` 的 Caller 因此是 `-`，這是**假陰性**，不是沒人呼叫。Caller 是 `-` 的其他路由（`GET /bookings`、`POST /reset`、`GET /`）不一定有問題，測試、瀏覽器、其他服務都可能是呼叫者，由 skill 寫進 Caller 欄。brownfield 的檢查多兩條規則：Status 是 `existing` 或 `change` 的列必須真的在 app 裡；scan 的每個 ERROR 必須出現在 `## Mismatches`。把 001 的 `GET /rooms` 照抄成 `existing` 再加上 `--app app.main:app --html app/static` 檢查，兩條規則各報一行：`EP:GET /rooms: Status 'existing', but the app serves no such route`、`scan found 'GET /rooms' with no backend route; list it under '## Mismatches'`。

`system-analysis/SKILL.md` 和上一個 skill 同構：description 說它盤點功能碰到的端點與頁面、回報前後端不一致，用在會新增或改動 HTTP 端點、頁面的功能；`allowed-tools` 只預先核准 `Read Grep Glob` 與 `Bash(python .claude/skills/system-analysis/scripts/inventory.py *)`，第二個參數 `$1` 是模式。六個步驟：brownfield 先跑 `scan` 並留著輸出；照模板一個端點一列，`existing` 只能給 scan 列出的路由；Caller 用使用者的話寫（頁面元件、測試、其他服務），沒有 FR 要求的端點寫 `internal: <why>`；每條 FR 都要出現，或列在 `## FRs without an endpoint` 並說明在哪處理；brownfield 把每個 ERROR 抄進 `## Mismatches` 並註明「這次修」或「另開 bug」，每個 WARN 手動追到底；最後跑完成檢查到 `0 problems`。`inventory.py` 完整原始碼（stdlib；`scan` 與 `check --app` 會匯入被掃的 app，所以要在裝了 FastAPI 的 venv 裡跑）：

```python
#!/usr/bin/env python3
"""Endpoint inventory for the system-analysis skill. scan: the FastAPI app's routes vs the
fetch() calls in HTML/JS (ERROR: no route serves a call; WARN: URL is not a string literal).
check: inventory.md vs spec.md (header, 'EP:<METHOD> <path>' IDs, Status, cited FRs, every FR
mentioned); with --app, 'existing'/'change' rows must be served and scan ERRORs listed in '## Mismatches'."""
import argparse, importlib, re, sys
from pathlib import Path

COLUMNS = ["ID", "Status", "Caller", "Auth", "Request", "Responses", "Owner", "Source"]

def n(count, word):  # "1 problem", "2 problems"
    return f"{count} {word}{'' if count == 1 else 's'}"

def norm(path):  # '/rooms/${r}/bookings?x=1' and '/rooms/{room}/bookings' -> '/rooms/{}/bookings'
    path = re.sub(r"^https?://[^/]+", "", path).split("?")[0].split("#")[0]
    return re.sub(r"\$?\{[^}]*\}", "{}", path).rstrip("/") or "/"

def backend_routes(target):
    from fastapi.routing import APIRoute, iter_route_contexts  # FastAPI >= 0.137.2
    sys.path.insert(0, ".")
    module, _, attr = target.partition(":")
    app = getattr(importlib.import_module(module), attr or "app")
    return [(m, rc.path) for rc in iter_route_contexts(app.routes)
            if isinstance(rc.original_route, APIRoute) for m in sorted(rc.methods)]

def fetch_calls(paths):
    for f in paths:
        text = f.read_text()
        for m in re.finditer(r"\bfetch\(", text):
            depth, i = 1, m.end()
            while depth and i < len(text):  # walk to the matching ')'
                depth, i = depth + {"(": 1, ")": -1}.get(text[i], 0), i + 1
            args = text[m.end():i - 1]
            meth = re.search(r"method:\s*['\"](\w+)", args)
            lit = re.match(r"\s*(['\"`])(.*?)\1(\s*\+)?", args)
            url = lit.group(2) + ("{}" if lit.group(3) else "") if lit else None
            where = f"{f.name}:{text.count(chr(10), 0, m.start()) + 1}"
            yield (meth.group(1).upper() if meth else "GET"), url, where, args.split(",")[0].strip()

def analyze(args):
    routes = backend_routes(args.app)
    files = [p for h in args.html or [] for p in (sorted(Path(h).rglob("*")) if Path(h).is_dir() else [Path(h)])
             if p.suffix in (".html", ".js")]
    served, callers, errors, warns = {(m, norm(p)) for m, p in routes}, {}, [], []
    calls = list(fetch_calls(files))
    for method, url, where, raw in calls:
        if url is None:
            warns.append(f"WARN  {where} {method} fetch({raw}): URL is not a string literal; trace it by hand")
        elif (method, norm(url)) in served:
            callers.setdefault((method, norm(url)), []).append(where)
        else:
            errors.append((f"{method} {norm(url)}", f"ERROR {where} calls {method} {norm(url)}: no backend route"))
    return routes, calls, served, callers, errors, warns

def scan(args):
    routes, calls, _, callers, errors, warns = analyze(args)
    print("| ID | Caller |\n|---|---|")
    for m, p in routes:
        print(f"| EP:{m} {p} | {', '.join(callers.get((m, norm(p)), [])) or '-'} |")
    print("\n".join([e[1] for e in errors] + warns + [
        f"{n(len(routes), 'route')}, {n(len(calls), 'call')}, {n(len(errors), 'problem')}, {n(len(warns), 'warning')}"]))
    return len(errors)

def check(args):
    text, frs = args.inventory.read_text(), re.findall(r"\*\*(FR-\d{3})\*\*", args.spec.read_text())
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines() if line.startswith("|")]
    problems = [] if rows and rows[0] == COLUMNS else [f"table header must be | {' | '.join(COLUMNS)} |"]
    _, _, served, _, errors, _ = analyze(args) if args.app else (None, None, set(), None, [], None)
    for r in rows[2:]:
        rid = r[0]
        if not re.fullmatch(r"EP:(GET|POST|PUT|PATCH|DELETE) /\S*", rid):
            problems.append(f"'{rid}': ID must look like 'EP:POST /bookings'")
        if len(r) != len(COLUMNS) or not all(r):
            problems.append(f"{rid}: every column needs a value (write '-' for none)")
        elif r[1] not in ("new", "existing", "change"):
            problems.append(f"{rid}: Status '{r[1]}' is not new, existing or change")
        elif args.app and r[1] != "new" and (rid[3:].split(" ")[0], norm(rid.split(" ", 1)[-1])) not in served:
            problems.append(f"{rid}: Status '{r[1]}', but the app serves no such route")
        problems += [f"{rid}: cites {fr}, which {args.spec} does not define" for fr in re.findall(r"FR-\d{3}", r[-1]) if fr not in frs]
    ids = [r[0] for r in rows[2:]]
    problems += [f"{i}: listed {ids.count(i)} times" for i in sorted(set(ids)) if ids.count(i) > 1]
    problems += [f"{fr}: not in any row; add one or list it under '## FRs without an endpoint'" for fr in frs if fr not in text]
    mismatches = text.split("## Mismatches", 1)[1] if "## Mismatches" in text else ""
    problems += [f"scan found '{key}' with no backend route; list it under '## Mismatches'" for key, _ in errors if key not in mismatches]
    print("\n".join(problems + [f"{n(len(frs), 'FR')}, {n(len(rows) - 2, 'row')}, {n(len(problems), 'problem')}"]))
    return len(problems)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="brownfield: routes + fetch() calls -> rows and mismatches")
    c = sub.add_parser("check", help="inventory.md against spec.md (and the app, with --app)")
    c.add_argument("inventory", type=Path)
    c.add_argument("--spec", type=Path, required=True)
    for p in (s, c):
        p.add_argument("--app", required=p is s, help="module:attribute of the FastAPI app, run from the project root")
        p.add_argument("--html", nargs="*", help="HTML/JS files or directories to search for fetch(")
    args = ap.parse_args()
    return 1 if (scan if args.cmd == "scan" else check)(args) else 0

if __name__ == "__main__":
    sys.exit(main())
```

**實際執行：用 brownfield 模式規劃 002。** 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。`claude -p "/system-analysis specs/002-cancel brownfield"`，旗標同上（`--tools` 少了 WebFetch），寫入只允許 `Edit(specs/002-cancel/**)`、Bash 只允許 `inventory.py`。19 turns、85 秒、0.48 美元，最後一次檢查（含 `--app --html`）是 `4 FRs, 5 rows, 0 problems`。埋的錯抓到了，`## Mismatches` 第一條寫「001-booking FR-005 was planned in specs/001-booking/inventory.md and never built」；WARN 也照步驟手動追完（「`api` is '/bookings' (index.html:5)」）。它還多找到一個 bug：`POST /bookings` 被標成 `change`，理由是「today id is len(BOOKINGS) + 1, so once a cancel shrinks the list two bookings can share an id and a cancel can hit the wrong one」，這個 bug 在 001 不會發作，加了取消才會；以及 research.md 的 DEC-01 決定用 SQLite、程式卻還存在記憶體，計畫和事實已經分岔，那是 [Ch 33](./33-truth-vs-plan.md) 的主題。它也有檢查抓不到的地方：`DELETE /bookings/{id}` 成功時寫的是 200 加上被取消的那筆，本課 R4 的約定是 204；002 的 spec 沒寫狀態碼，它就照自己的判斷填了，該修的是 spec（[Ch 18](./18-clarify-skill.md) 的 clarify），不是 `inventory.py`。

## 底層機制：inventory.py 怎麼把前後端對上

```
app.main:app ─ import ─▶ iter_route_contexts(app.routes) ─▶ (GET, /rooms/{room}/bookings) …  ─┐
app/static/*.html ─ 找 fetch( 數括號到配對的 ) ─▶ (GET, `/rooms/${room}/bookings`) (POST, api) ─┤
                        兩邊都過 norm()：{room}、${room} → {}，去掉 ?query 與 #fragment    ◀──┘
        用 (method, 正規化 path) 對帳 ─▶ 對得上：寫進 Caller ｜ 對不上：ERROR ｜ 網址不是字面值：WARN
```

**後端那半邊為什麼不直接讀 `app.routes`？** FastAPI 0.137.0（2026-06-14）的 breaking change：`include_router` 不再把子 router 的路由複製進來，[release note](https://github.com/fastapi/fastapi/releases/tag/0.137.0) 原文是「Now `router.routes` is no longer a plain list of `APIRoute` objects, it can contain these intermediate objects that can contain additional routers, forming a tree」，並說 `router.routes` 應視為內部實作細節；[0.137.2](https://github.com/fastapi/fastapi/releases/tag/0.137.2) 才補上 `iter_route_contexts()`，給「以前用 `router.routes`」的進階用途。在 lab 的 app 上，`app.routes` 的元素型別是 `APIRoute`、`Route`、`_IncludedRouter` 三種，只挑 `APIRoute` 會漏掉帶 prefix 的 `GET /rooms/{room}/bookings`；在 0.137 之前，`include_router` 會把路由複製進來，`app.routes` 是攤平的清單，這樣寫是對的。把 `backend_routes()` 的 return 換回舊寫法 `return [(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in sorted(r.methods)]` 再掃一次，多出 `ERROR index.html:12 calls GET /rooms/{}/bookings: no backend route`，摘要變成 `4 routes, 3 calls, 2 problems, 1 warning`：一個假 ERROR，而且很難懷疑，因為它和真的那個長得一模一樣。另一條路 `app.openapi()` 有 prefix，卻少了 `include_in_schema=False` 的兩條：`python -W ignore -c "from app.main import app; print(list(app.openapi()['paths']))"` 印出 `['/bookings', '/rooms/{room}/bookings']`，`POST /reset` 與 `GET /` 都能被呼叫，卻不在 schema 裡。用 grep 找 `@app.get(` 也一樣看不到 prefix。

**前端那半邊**是正規式加括號配對：找到 `fetch(` 之後往後數括號到配對的 `)`；第一個參數若是字串字面值（單引號、雙引號、反引號）就當網址，字面值後面緊接 `+` 就補一個 `{}`（`'/bookings/' + id`），否則記成 WARN。method 在同一段參數裡找 `method: '…'`，找不到就是 GET；數括號讓跨行的 `fetch(api, {` 也抓得到下一行的 `method: 'POST'`。它**不**認得 axios、`XMLHttpRequest`、`<form action>` 與在別處組好的網址，這些只會變成「沒有 Caller」或 WARN，不會變成錯的 ERROR：寧可少說，不要說錯。有一處沒做到：`norm()` 會去掉 `https://主機`，頁面裡第三方的 `fetch("https://calendar.example.com/bookings")` 會被記成 `EP:GET /bookings` 的 Caller，摘要照樣 `0 problems`；頁面會呼叫外部 API 時，先把絕對網址挑出來另列。

## 四、parent：重跑誰、怎麼合併

`plan-feature` 是 parent，不是 router。[pstack Ch 5](../pstack/05-router-design.md) 的 router 讓模型讀一份索引、做一次分類、挑**一個**流程；parent 不分類，把每個 planner 過一遍，用**腳本結果**決定要不要重跑，再合併。它設 `disable-model-invocation: true`，不佔 skill listing，只會被人用 `/plan-feature` 叫起來。它的步驟（修正後的版本，修了什麼見下文）：一、對每個 planner 先跑完成檢查，只有產物不存在、檢查失敗、或閘門判定過期時才重跑，否則重用並說明；二、合併進 plan.md，把每個 `NEEDS CLARIFICATION [DEC-NN] <question>` 換成一行決定並**保留** `[DEC-NN]`，加一節 `## Planner outputs` 記每份產物的檢查與結果，再把 research.md 與 inventory.md 並排讀，矛盾全列進 `## Conflicts`，附兩邊出處與建議解法，**不准**靠改 planner 的產物來消除矛盾；三、planner 寫不進產物時，照樣合併已通過檢查的部分，並記下被擋住的 planner；四、不剩 `NEEDS CLARIFICATION`、兩支檢查都 `0 problems` 才算完成。

**實際執行：一次沒有完成的合併。** 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。我們在 greenfield 狀態（沒有 `app/`）跑 `/plan-feature specs/001-booking`，當時第 1 步的過期判斷還是 `test $0/spec.md -nt <artifact>`；research.md 與 inventory.md 都在、檢查都通過，寫入只允許 `Edit(specs/001-booking/plan.md)`。預期它兩個都重用、直接合併。實際上：兩支檢查都 `0 problems`，但 `test specs/001-booking/spec.md -nt specs/001-booking/research.md` **成立**，因為 lab 副本是 `cp -R` 出來的，三個檔的 mtime（修改時間）落在同一秒、spec.md 最後寫入，只晚了不到 1 ms，而它的 Bash 跑在 zsh，`-nt` 比得到秒以下（同一個 test 在原始目錄回 exit 1）。於是它用 Skill 工具叫起兩個 planner；它們沒有 `context: fork`，planner 的指示只是被加進**同一段對話**，執行者還是 parent 自己，權限是 parent 那一份再加上 planner 的 `allowed-tools`：`python spikes/race_spike.py` 因此不必核准就跑了，加上 `> spikes/race_spike.out` 卻被擋，因為導向的目標照 `Edit` 規則審。重跑 spike 存檔、重寫 research.md 與 inventory.md 的四次寫入，沒有一條規則核准，全被拒絕。被拒絕之後，它連允許的 plan.md 也沒改，原話是「I didn't attempt this edit, since it needs the same approval」。39 turns、127 秒、0.72 美元，plan.md 一個字沒動。它附上的合併文字裡有四條 `## Conflicts`，第一條：

```markdown
1. **Does code exist?** research.md DEC-01 calls the in-memory list "the code today", and DEC-03 Risks describes the page's free-text inputs. inventory.md says "greenfield ... no code yet". No `app/` exists. Proposed: inventory.md is right; drop both phrases on the next technical-research run. The decisions do not change.
```

這條抓到的是我們自己：手寫的黃金樣本 research.md 是在 lab 已經有 app 時寫的，和「greenfield」的 inventory 互相矛盾。兩支檢查都看不到這種矛盾，只有把兩份產物並排讀才看得到，這就是套圖。第二條指出 DEC-03 讓格式錯誤的時間回 422，inventory 卻只把 422 記給 R2，而 constitution III 要求 `detail` 說出怎麼修。我們把它給的 Technical Context 三行與 `## Planner outputs` 手動貼進 plan.md 的副本（`## Conflicts` 只貼摘要），`NEEDS CLARIFICATION [DEC-01] …` 變成 `One SQLite file through the standard-library sqlite3 module [DEC-01]`，再跑 parent 的完成條件：`grep -c "NEEDS CLARIFICATION"` 印出 `0`，兩支檢查印出 `3 decisions, 3 questions, 0 problems` 與 `6 FRs, 5 rows, 0 problems`。ID 留著，所以 `--plan` 的比對在合併後仍成立，[Ch 16](./16-tasks-binder.md) 的 tasks 也引用得到。注意 `grep -c` 在零筆時印 `0` 卻 exit 1，完成條件要看印出的數字，不是 exit code。

**修正閘門：比 commit，不比 mtime。** `cp -R` 和 `git clone` 都會重設 mtime（兩種我們都試過，clone 下來的副本在 zsh 裡 `test -nt` 一樣誤判；macOS 的 `/bin/sh` 是 bash 3.2，只比到秒，同一個 test 回 exit 1，對錯看檔案剛好落在哪一秒），但不會改 commit 的先後。修正版第 1 步改呼叫 `scripts/fresh.sh`，它像 `test` 一樣只用 exit code 回答，所以沒有 `--help` 與摘要行：

```sh
#!/bin/sh
# fresh.sh FEATURE_DIR ARTIFACT: exit 0 when ARTIFACT was committed after the last commit that touched FEATURE_DIR/spec.md and spec.md has no uncommitted edits
s=$(git log -1 --format=%H -- "$1/spec.md"); a=$(git log -1 --format=%H -- "$2")
[ -n "$s" ] && [ -n "$a" ] && git diff --quiet HEAD -- "$1/spec.md" && git merge-base --is-ancestor "$s" "$a"
```

在一個 `git init` 過的小 repo 裡先 commit spec、再 commit research，`cp -R` 一份，在副本裡（zsh）實測：`test spec.md -nt research.md` 回 exit 0，誤判「spec 比較新」；`fresh.sh` 回 exit 0，判為未過期；spec 改了還沒 commit，回 exit 1；改完 commit 之後也是 exit 1；產物還沒 commit 也回 exit 1，寧可重跑。只比 commit 的第一版少了 `git diff --quiet` 那個條件，spec 改了沒 commit 時照樣回 0，等於把過期的產物當新的用。上面步驟三也是這次執行之後才加的。修正版**沒有**再用 agent 跑一次（本章三次 `claude -p` 已用完），`fresh.sh` 的行為以上面的實測為準。

**成本。**

| 執行 | turns | 秒 | 美元 | 權限拒絕 | 產物與結果 |
|---|---|---|---|---|---|
| 1. `technical-research`（001） | 32 | 179 | 0.82 | 3 | research.md 6,709 B、三支 spike；`0 problems` |
| 2. `system-analysis`（002，brownfield） | 19 | 85 | 0.48 | 1 | inventory.md 5 列；`0 problems` |
| 3. `plan-feature`（001） | 39 | 127 | 0.72 | 5 | 沒有寫入；合併文字與 4 條 conflicts |

turns 是結果 JSON 的 `num_turns`，大約是工具呼叫數加一（第 1 次 31 次呼叫、32 turns），和 `--max-turns 20` 的單位不同，所以會超過 20。三次的主模型都是 claude-opus-5-5；第 1 次另有 0.011 美元記在 claude-haiku-5-5，三次裡只有它用了 WebFetch，應是摘要網頁的小模型。常駐成本用 [Ch 4](./04-prompt-files-to-skills.md) 的 `context_cost.py` 量：兩個 planner 的 listing 每 turn 共 516 B（technical-research 243 B、system-analysis 273 B），parent 因 `disable-model-invocation` 不佔。審的成本也要算：agent 的 research.md 是我們手寫版（2,985 B）的 2.2 倍長。這張表**不能**回答「拆開比較貴還是便宜」，我們沒有在同一個 feature 上跑原版 `/speckit-plan` 對照，它還會產 data-model、contracts、quickstart，工作量不同；它能回答的是，拆開後成本可以按 planner 歸因，而第 3 次多花的 turn 來自一個錯的閘門。兩個 planner 互不依賴，可以並行，延遲取較慢的那個、成本相加，算法見 [harness Ch 27](../harness_engineering/27-multi-agent-orchestration.md)「三、成本與延遲」。

## 對比與取捨

| 做法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 原版 `/speckit-plan` 一次做完 | 一個指令、上手快 | 沒有分段的完成檢查；一個輸入變了全部重跑；review 一大包 | 小功能、探索 |
| planner skill + parent（本章） | 產物可單獨審、重跑、失敗；成本可歸因 | skill 變多要維護；交接與合併本身會出錯（第 3 次執行） | 要長期維護、多人審的功能 |
| router 只挑一個 planner | 只付需要的那份成本 | 分類靠模型，可能挑錯 | 使用者明說「只研究 X」 |
| Spec Kit extension 的 `before_plan` hook | 留在官方流程，升級較穩 | 仍在同一段 plan 對話；Phase 0 照樣產 research.md | 全面採用 Spec Kit 的團隊 |
| 程式流水線（[SDD Ch 38](../spec_driven_development/38-build-your-own-pipeline.md)） | 每步是程式、結構化輸出、好並行 | 要寫並維護程式 | CI、大量重複的規劃 |

## 踩雷集錦

1. **錯誤直覺：拆越細越好。** 正確認識：拆的單位是「自己的輸入、單一產物、能單獨失敗的檢查」。contracts 先有了檢查（[Ch 14](./14-artifact-first.md) 的 `contract_check.py`）才拆成 `contract-writer`；data-model 與 quickstart 還說不出檢查，所以留在 /speckit-plan。要拆，先寫檢查（例如每個 Key Entity 有欄位表、每條驗證規則指回一條 FR），順序和 Ch 14 的 artifact-first 一樣。
2. **錯誤直覺：檢查通過就代表內容對。** 正確認識：刪掉 `EP:GET /rooms` 整列仍是 `0 problems`，DELETE 回 200 也通過。腳本回答「接上了沒」，人回答「接得對不對」。
3. **錯誤直覺：用 mtime 判斷產物過期。** 正確認識：`cp -R` 和 `git clone` 都會重設 mtime，第 3 次執行因此把兩個 planner 重跑了一遍（寫入被權限擋下）。比 commit 的先後，或比內容 hash。
4. **錯誤直覺：合約寫了「讀這些」、`allowed-tools` 列了工具、parent「呼叫」了 planner，就有邊界。** 正確認識：第 1 次執行讀了 `app/`、別的 spec、parent 的 SKILL.md；`allowed-tools` 只是免問清單；沒有 `context: fork` 的 skill 只是把指示加進同一段對話，第 3 次執行的 parent 就自己當起了 planner，權限是自己那份加上 planner 的 `allowed-tools`。要強制，用權限的 deny 規則，或讓 planner 在有自己工具清單的 subagent 裡跑。
5. **錯誤直覺：照網路範例用 `app.routes` 列路由。** 正確認識：FastAPI 0.137 起它是一棵樹，會漏掉帶 prefix 的路由、產生假 ERROR。用 `iter_route_contexts()`，並在註解寫明版本下限。
6. **錯誤直覺：每支檢查各自 0 problems，換一支別章的檢查也會過。** 正確認識：本章 lab 的 constitution 精簡成三個 `## I.` 標題，黃金樣本的 research.md 寫「constitution III」；[Ch 13](./13-plan-anatomy-review.md) 的 `plan_lint.py` 照 Spec Kit 的 constitution 模板只找 `### I. 名稱`，錨點只認 `Principle III`。把合併後的 001 放進一個完整的 1.2.5 init（plan_lint 要讀它的 plan-template.md）再跑，得到 `1 plan, 9 problems`：DEC-03 的 PL-05 與「找不到原則」的 PL-06 來自寫法，其餘七則是本章不產的段落與檔案（PL-01 五則、PL-08 兩則）。要接 plan_lint，就照模板寫 `### I.` 與 `Principle III`，Constitution Check 也照 Ch 13 每條判兩次（三處都改了再跑，只剩那七則），[練習 C](./practice-c-custom-sdd-flow.md) 就是這樣過的。

## 進階：隔離 planner、掛進 Spec Kit

- **`context: fork`**：planner 的 frontmatter 加 `context: fork`（可配 `agent:`），它就在獨立的 subagent 裡以 SKILL.md 為任務執行，看不到對話歷史。官方文件說 forked skill 預設在背景執行、`-p` 模式下會等結果，背景執行時工具集較窄，要完整工具就設 `background: false`。這能讓第 1 次執行那段研究（最後一個 turn 的輸入已達 51,978 tokens）留在 subagent 裡，只把結論帶回 parent，也讓 planner 和 parent 拿不同的權限。本段未實測，為依官方文件的預期行為；驗證方法是給 planner 加上 `context: fork` 後重跑第 3 次執行，看 stream-json 裡是否出現獨立的 subagent。subagent 的紀律見 [pstack Ch 16](../pstack/16-subagent-discipline.md)。
- **Spec Kit 1.2.5 的 extension hook**：`docs/extensions/developing.md` 列出 `before_plan`、`after_plan` 等 hook point，每個 hook 有 `command`、`priority`（數字小的先跑，預設 10）、`optional`；`speckit-plan` 的 SKILL.md 開頭會讀 `.specify/extensions.yml`，必要的 hook 直接執行、可選的提示你。把兩個 planner 包成 extension command、註冊成 `before_plan`，就能留在官方流程裡。本段未實測；Phase 0 仍會 Generate research.md。要拿掉這一步，得用 preset 的 command override 以預設的 `replace` 整份換掉 `speckit.plan`（1.2.5 的 `docs/reference/presets.md`）；[Ch 17](./17-constitution-and-presets.md) 示範的 `wrap` 只能加規則。

## 動手練習

1. 在 `index.html` 加一個取消按鈕，呼叫 `` fetch(`/bookings/${b.id}?by=${name}`, {method: 'DELETE'}) ``，先預測 `inventory.py scan` 會多哪一行 ERROR 再驗證；替後端加上 `@app.delete("/bookings/{booking_id}")` 後再掃，確認 `norm()` 讓兩邊對上。
2. 讓 `fetch_calls` 認得同一個檔裡的 `const NAME = '…'`，使 `fetch(api, …)` 的 WARN 消失；先準備一個會失敗的輸入，再改程式。
3. 替 002 寫一題 `NEEDS CLARIFICATION [DEC-01]`：取消時怎麼確認呼叫者就是預約人？至少兩個改變形狀的選項（自報姓名、session cookie……），用一支 spike 或一份官方文件當證據，跑 `research_check.py` 到 `0 problems`；再用 `cp -R` 複製一份 `git init` 過的 lab，確認 `fresh.sh` 不會把它誤判為過期。

## 本章重點整理

- `/speckit-plan` 一次做六件事、沒有能檢查內容的完成條件；拆成 planner 的判準是「單一產物 + 能單獨失敗的檢查」。
- planner 合約三欄：讀什麼、只寫哪個檔、哪支檢查算完成；問題帶 `[DEC-NN]`，「答了沒、多答了沒」就能比對。
- `technical-research` 用 spike 與官方文件當證據，`research_check.py` 只檢查每筆證據是網址或存在的檔案；`system-analysis` 的 greenfield 從 FR 推，brownfield 用 `iter_route_contexts()` 加 `fetch(` 掃描對帳，ERROR、WARN、沒有 Caller 是三種不同的處置。
- parent 用腳本與閘門決定重跑誰、合併時保留 ID、把兩份產物的矛盾列進 `## Conflicts`；閘門錯了，成本就花在重做上。

## 自我檢核

- [ ] 不看本章，說出 planner 合約的三欄，並舉一個「說不出檢查所以先不拆」的例子。
- [ ] `research_check.py` 為什麼要同時抓「沒回答的問題」和「沒人問的問題」？各自對應一次生成式規劃的哪種毛病？
- [ ] 在 FastAPI 0.162 上，`app.routes`、`app.openapi()`、`iter_route_contexts()` 各會漏掉什麼？怎麼讓一支掃描腳本在 FastAPI 改版時大聲失敗，而不是默默少列？
- [ ] 第 3 次執行為什麼一個字都沒寫進 plan.md？說出至少兩個原因，以及各該改 skill、權限還是閘門。

## 延伸閱讀

- **[Spec Kit v1.2.5 — templates/commands/plan.md](https://github.com/github/spec-kit/blob/v1.2.5/templates/commands/plan.md)**
  - **讀哪裡**：Outline 第 3 步，以及 Phases 下的 Phase 0、Phase 1。
  - **學到什麼**：原版 /plan 一次包辦的六件事；research.md 只要求 Decision、Rationale、Alternatives considered。
  - **和本章的關聯**：本章的兩個 planner 各對應這份檔的一段；先讀 [Ch 13](./13-plan-anatomy-review.md) 更好。
- **[Michael Nygard — Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)**（2011-11-15）
  - **讀哪裡**：全文很短；重點是 Title、Context、Decision、Status、Consequences 五段，與「Small, modular documents have at least a chance at being updated」。
  - **學到什麼**：一個決策一份短紀錄，負面後果也要寫。
  - **和本章的關聯**：research.md 的每個 `DEC-NN` 是一份迷你 ADR，Risks 是 Consequences 的負面那一半。
- **[Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)**（2024-12-19）
  - **讀哪裡**：Routing、Parallelization、Orchestrator-workers 三段，與「we recommend finding the simplest solution possible」那句。
  - **學到什麼**：router、並行、orchestrator 各適合什麼情況，什麼時候不該再加複雜度。
  - **和本章的關聯**：`plan-feature` 是固定順序的 parent，不是動態拆題的 orchestrator；讀完會知道本章為什麼先選最簡單的那種。前提是讀過 [harness Ch 27](../harness_engineering/27-multi-agent-orchestration.md)。
- **[Claude Code docs — Extend Claude with skills](https://code.claude.com/docs/en/skills)**
  - **讀哪裡**：Frontmatter reference 的 `allowed-tools`、`disable-model-invocation`、`context`；「Run skills in a subagent」；「Available string substitutions」。
  - **學到什麼**：本章三個 SKILL.md 每個欄位的確切語意，特別是 `allowed-tools` 只免問、不限制。
  - **和本章的關聯**：踩雷 4 與進階第一點的出處。

兩份分析文件都有了能被引用的 ID：`DEC-02`、`EP:GET /rooms`、`EP:DELETE /bookings/{id}`。下一章把它們和 FR、情境標籤一起綁進 tasks.md，再用腳本確認沒有一條掉在地上。

→ [Ch 16 tasks 綁定器：把多份分析文件綁進 tasks.md](./16-tasks-binder.md)
