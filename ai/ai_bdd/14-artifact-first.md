# Ch 14 — Artifact-first：先定黃金產物，再反推 skill

> **目標**：能照 artifact-first 的順序做出一個 skill：先挑一份審過的黃金產物（golden artifact），逐行問「這一行為什麼對」，把答案拆成模板（形狀）與不變量（invariant，它和 spec、下游的關係），寫成一支會失敗的檢查器，再讓 Claude 起草一份「完成條件就是檢查器 exit 0」的 SKILL.md；最後換一個 feature 重新產生，用 `skeleton.py` 比對形狀，並和先寫 prompt 的版本並排比較，說得出這個做法保證了什麼、沒保證什麼。

> **環境**：Python 3.12.15 與課程 venv（兩支新腳本只用標準函式庫，也在 macOS 內建的 Python 3.9.6 跑過）、Claude Code 2.1.295（`claude -p` 三次，2026-11-02，主模型 claude-opus-5-5）、Node v26.11.0（只跑 pstack 的 skill-lint）、skill-creator 快照 a7ba5b93 的 `quick_validate.py`。實驗目錄 `/tmp/lab/aibdd/ch14-artifact-first/`，從空目錄開始；`template_extract.py`、`template_check.py` 原樣取自 [Ch 10](./10-templates-and-scripts.md)，spec 與黃金產物取自 [Ch 13](./13-plan-anatomy-review.md)。

## 為什麼需要這個？

大部分 skill 是先寫 prompt 的（prompt-first）：在 SKILL.md 寫步驟，跑一次，看產物，哪裡不對就補一句。「好」只存在作者腦中，每一句補丁回答的是上一次的失敗，完成的定義是「看起來對」。[Ch 7](./07-messy-baseline.md) 那份 222 行的 `bdd-monolith` 是這條路的起點：從一段描述寫出第一版就收工，沒有任何一份好產物可以對照。放到 SDD 產物上，結果在 [Ch 13](./13-plan-anatomy-review.md)「七、讓 agent 真的跑一次 /speckit-plan」：原版 `/speckit-plan` 把契約寫成兩個檔、83 行，欄位叫 `holder`、錯誤體是 `{"reason": ...}`、UI 用元素 id，模板沒有一處禁止這些寫法。是人審出來的，而那次 review 無法重複，因為「好的契約長什麼樣」沒有寫在任何程式讀得到的地方。

從成品倒推回去的做法並不新。Werner Vogels 2006 年描述 Amazon 的 [Working Backwards](https://www.allthingsdistributed.com/2006/11/working_backwards.html)：動工之前先寫新聞稿、FAQ、畫面草圖與使用手冊（「Start by writing the Press Release. Nail it.」）；Tom Preston-Werner 2010 年提倡 Readme Driven Development：「Write your Readme first」；測試界的 golden master 與 approval testing（ApprovalTests 這類工具）把輸出和一份人核准過的快照比對；TDD 則是在程式碼上做同一件事。Anthropic 的 skill 寫作指南也要你「Create evaluations BEFORE writing extensive documentation」。artifact-first 把這些用在 skill 上：黃金產物是先寫的 README，檢查器是測試，SKILL.md 是要讓測試通過的實作。

repo 裡已有幾塊拼圖：[pstack Ch 27](../pstack/27-multi-phase-autopilot.md)「二、check-plan.mjs：把計畫格式編碼成檢查」把計畫格式寫成檢查，並提醒格式檢查不等於品質檢查；[pstack Ch 28](../pstack/28-skill-authoring.md)「四、什麼時候拆檔：references 與 scripts」說模板放 references、機器判得了的規則放 scripts；[pstack Ch 30](../pstack/30-self-improvement-loop.md)「四、/automate-me：你自己的 mode」從對話紀錄反推 skill（行為 → skill）；[pstack 期末專案](../pstack/final-project-course-mode.md)的順序也是先寫檢查（「Step 2：寫 check-chapter.mjs，先寫一個壞範例」）、最後才寫 SKILL.md（Step 6）；[Ch 10](./10-templates-and-scripts.md) 從三份以上的範例抽模板；[harness Ch 29](../harness_engineering/29-skills.md)「四、寫一個好 skill：description 是命脈，本文要像給人的 SOP」要本文講清楚「做完應該長什麼樣」，讓模型自我驗收，本章把自我驗收換成一支檢查器。還沒有人做的是：從**一份**黃金產物出發，分出形狀與不變量，讓完成條件等於檢查器，再換一個 feature 重新產生、比對，而且用在 SDD 產物上。本章拿 Ch 13 留下的那份契約做一遍。

## 先建立直覺：先封樣，再寫作業標準書

工廠量產之前會先「封樣」：做一個樣品給客戶簽認、封存，叫標準樣品（golden sample）。品管拿它量出檢驗規格、做出檢具，再寫作業標準書，標準書的驗收就是「過得了檢具」；最後拿另一個型號試產，和樣品並排比。先寫 prompt，等於先寫作業標準書，第一批貨出來再憑感覺判斷好壞。

```
prompt-first：   SKILL.md ──► 產物 ──► 人看一看 ──► 補一句 SKILL.md ──► …（「好」只在作者腦中）
artifact-first：
  黃金產物（一份，審過）
    ├─ 逐行問「這一行為什麼對？」
    │    ├─ 答案是「每個 feature 都長這樣」  ──► 模板 templates/interfaces.md（形狀）
    │    └─ 答案引用了 spec、下游或舊的失敗 ──► 不變量 rules/contract.md（CT-01..CT-06）
    ├─ 檢查器 contract_check.py：在黃金產物上 0 problems，在每個突變上各自失敗
    ├─ Claude 起草 SKILL.md：完成條件 = 檢查器 exit 0
    └─ 換一個 feature 重新產生 ──► 檢查器 + skeleton.py 比形狀 ──► 差異回頭改 skill、模板、規則或黃金產物
```

類比有兩處不成立。工廠的檢具要每一件都和樣品一樣；我們要的是形狀相同、內容不同，檢查器量的是形狀與「和 spec 的關係」，不是相等。另外，樣品裡沒有的情況，它教不了：本章的黃金產物沒有 `DELETE`，第六節會看到後果。

## 一、黃金產物：選一份，先審它

選 Ch 13 的 `contracts/interfaces.md` 當黃金產物，三個理由：它短到能逐行讀；同一份 spec 有一個真實的壞版本（Ch 13 的原版 `/speckit-plan` 實跑產物）可以對照；下游真的依賴它，[Ch 23](./23-frontend-e2e-playwright.md) 的 Playwright locator 和 [Ch 30](./30-one-gherkin-two-drivers.md) 的兩個 driver 讀的就是 `by`、`detail` 與可存取名稱（accessible name）。挑黃金產物的標準：給真實輸入寫的、被下游的人審過、短、典型而非特例。建 lab（沒有 Ch 13 的 lab 時，spec 照 Ch 13 第一節重打、契約照下面的內容建，略過 `samples/` 那兩行；`samples/` 是 Ch 13 那一次 agent 實跑的產物，你自己重跑 Ch 13 得到的契約不同，第四節對它的計數也會不同。沒有 Ch 10 的 lab 時，兩支腳本照 Ch 10 第一、二節的程式碼建）：

```bash
mkdir -p /tmp/lab/aibdd/ch14-artifact-first && cd /tmp/lab/aibdd/ch14-artifact-first && mkdir -p golden/001-room-booking/contracts samples/stock-001/contracts scripts templates rules
cp ../ch13-plan/booking/specs/001-room-booking/spec.md golden/001-room-booking/
cp ../ch13-plan/booking/specs/001-room-booking/contracts/interfaces.md golden/001-room-booking/contracts/
cp ../ch13-plan/agent-run/specs/001-room-booking/spec.md samples/stock-001/
cp ../ch13-plan/agent-run/specs/001-room-booking/contracts/*.md samples/stock-001/contracts/
cp ../ch10-templates-scripts/scripts/template_extract.py ../ch10-templates-scripts/scripts/template_check.py scripts/
source ../.venv/bin/activate && export PYTHONDONTWRITEBYTECODE=1   # 不留 __pycache__，之後整個目錄要複製進 skill
```

黃金產物原文（Ch 13 寫的版本，第四節會改它兩處）：

```markdown
# Contracts: Room Booking (FR-001 to FR-005)
Errors use FastAPI's `{"detail": "<text>"}` with the spec's exact text; the page shows that text unchanged (SC-002).

| Interface | Success | Rejected |
|---|---|---|
| `POST /bookings`: `room` (Taipei/Tokyo/Seoul), `start`, `end` ("HH:MM"), `by` | 201, the booking plus `id` from 1 (FR-001) | 409 R1 `Taipei is taken 10:00-11:00 by Bob` (FR-002); 422 R2 `end must be after start` (FR-003) |
| `GET /bookings` | 200, every booking today (FR-004) | none |
| `POST /reset`, only when `BOOKING_TEST=1` | 204, schedule cleared (SC-001, research D4) | none |
| `GET /` page: textboxes `Room` `Start` `End` `Name`, button `Book` | `<p role=status>` says `Booked Taipei` | `Rejected: <detail>` |
```

## 二、一份範例抽不出模板

先試 Ch 10 的提取器：

```
$ python scripts/template_extract.py golden/001-room-booking/contracts/interfaces.md -o templates/interfaces.md; echo "exit=$?"
usage: template_extract.py [-h] -o OUT [-n] [--force] files [files ...]
template_extract.py: error: need 2+ examples to tell boilerplate from content; add another good example
exit=2
```

拒絕是對的。提取器用集合交集認固定行，一份範例沒有交集可算。兩種騙法我們都試了：同一個路徑給兩次，Ch 10 的守門照樣擋（它數的是不同的檔案，同一句 `need 2+ examples`，exit 2）；改給一份內容相同、檔名不同的複本就騙得過：`1 sections, 0 shape rules`，交集是全部，模板逐字等於黃金產物，拿去檢查第六節的 002 契約，在標題就失敗：`no heading matches "# Contracts: Room Booking (FR-001 to FR-005)"`。全部當固定行是錯的，因為「哪一行是每個 feature 都有的」只有作者知道。Ch 10 的進階一節預告過：只有一份黃金產物時，要「只留標題與形狀」，而且接受學不到固定行。所以分兩步：形狀交給一支不看內容的指紋工具，固定行由人決定、寫進手寫的模板。

`scripts/skeleton.py` 把文件化成骨架：標題留層級與第一個詞，表頭留原文，其他行換成 Ch 10 的 word shape 加上格數，連續同形的行併成一行。它 import Ch 10 的 `head`、`shape`、`FENCE`，所以要和 `template_extract.py` 放在同一個目錄：

```python
#!/usr/bin/env python3
"""skeleton.py: reduce markdown to its shape; with --against GOLDEN, diff each file's shape against GOLDEN's.
A heading keeps its level and first word, a table header row stays verbatim, any other line becomes its Ch 10
word shape plus its cell count, a fenced block becomes one line, repeats collapse. Content is dropped.
Exit: 0 same shape, 1 a shape differs, 2 error."""
import argparse, difflib, re, sys
from template_extract import FENCE, head, shape

SEP = re.compile(r"\|[\s:|-]+\|")

def skeleton(path):
    lines, out, i = open(path, encoding="utf-8").read().splitlines(), [], 0
    while i < len(lines):
        s, nxt = lines[i].strip(), lines[i + 1].strip() if i + 1 < len(lines) else ""
        if FENCE.match(s):                                   # a fenced block -> one line; its lines are content
            item, i = s + " …", i + 1
            while i < len(lines) and not FENCE.match(lines[i]): i += 1
        elif re.match(r"#{1,6} ", s): item = s.split(" ", 1)[0] + " " + head(s.split(" ", 1)[1])
        elif SEP.fullmatch(s): item = "|" + "---|" * (s.strip("|").count("|") + 1)
        elif s.startswith("|") and SEP.fullmatch(nxt): item = s          # a header row: column names are structure
        elif s.startswith("|"): item = shape(s) + " |" + " · |" * s.strip("|").count("|")
        else: item = shape(s) if s else None
        if item and (not out or out[-1] != item): out.append(item)
        i += 1
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="markdown documents")
    ap.add_argument("--against", metavar="GOLDEN", help="diff each file's skeleton against GOLDEN's")
    a = ap.parse_args()
    if not a.against:
        for f in a.files: print(*skeleton(f), sep="\n")
        return 0
    differ = 0
    for f in a.files:
        diff = list(difflib.unified_diff(skeleton(a.against), skeleton(f), a.against, f, n=1, lineterm=""))
        differ += bool(diff)
        if diff: print(*diff, sep="\n")
    print(f"{len(a.files)} files, {differ} differ from {a.against}")
    return 1 if differ else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "shapes differ"
    except OSError as e: print(f"error: {e.filename}: {e.strerror}", file=sys.stderr); sys.exit(2)
```

在黃金產物上跑 `python scripts/skeleton.py golden/001-room-booking/contracts/interfaces.md`，九行的契約剩五行：`# Contracts:`、`a`（錯誤那一句）、原樣的表頭、分隔線，以及一行 ``| `a | · | · |``：四列介面都是「以反引號開頭、三格」，併成一行。內容全部消失，剩下的就是第六節要比的東西。模板則由人照黃金產物手寫，存成 `templates/interfaces.md`（`{{…}}` 與 `<!-- shape: … -->` 的格式還不熟，先回看 [Ch 10](./10-templates-and-scripts.md)「二、template_check.py：讓模板變成會失敗的檢查」）：

```markdown
<!-- contract-writer template, written by hand from one golden copy: examples/001-room-booking/contracts/interfaces.md -->
# Contracts: {{功能名稱}} ({{FR 範圍}})
Errors use FastAPI's `{"detail": "<text>"}` with the spec's exact text; the page shows that text unchanged.

| Interface | Success | Rejected |
|---|---|---|
<!-- shape: "| `a" e.g. "| `POST" -->
<!-- one row per interface: `METHOD /path` and its inputs, or `GET /` page and its controls (CT-05); Success = status, what comes back, (FR-…); Rejected = `none`, or "<4xx> <R#> `<exact text>` (FR-…)" parts joined by "; " (CT-03) -->
{{介面列：例「| `POST /bookings`: `room` (Taipei/…」}}
```

三個決定：錯誤那一行是 house 慣例，每個 feature 都一樣，設成固定行；標題保留「Contracts:」與 FR 範圍，這是 Ch 10 踩雷 2 的教訓（沒寫進模板的慣例會掉）；每一列都要以反引號包住的介面開頭。注意固定行**沒有**黃金產物句尾的 `(SC-002)`：那是這個 feature 的 ID，下一個 feature 不會有。來回測試（round trip）因此會失敗，第四節一起看。

## 三、不變量：逐行問「為什麼對」

模板只管形狀。黃金產物真正的價值在於每一行**為什麼**是對的，答案常常引用了另一份文件：

| 黃金產物的這一行 | 為什麼對 | 變成 |
|---|---|---|
| 標題 `# Contracts: Room Booking (FR-001 to FR-005)` | 一眼看出範圍；每個 feature 都該有 | 模板 |
| ``Errors use FastAPI's `{"detail": "<text>"}` …`` | 兩個 driver 讀 `detail`；Ch 13 的 agent 寫成 `reason` | 模板固定行＋CT-04 |
| ``… `end` ("HH:MM"), `by` `` | spec 用 holder 這個字，API 與 driver 用 `by` | CT-04 |
| ``409 R1 `Taipei is taken 10:00-11:00 by Bob` (FR-002)`` | 測試逐字比對訊息；規則 ID 讓 R1 可以 grep | CT-03 |
| 每一列的 `(FR-00N)` | 看得出每條 FR 由哪個介面負責、漏了誰 | CT-01、CT-06 |
| `` `POST /reset`, only when `BOOKING_TEST=1` … (SC-001, research D4) `` | 測試專用介面要寫出條件，錨在需要它的 SC | CT-06、CT-02 |
| ``textboxes `Room` … button `Book` `` | Playwright 的 `get_by_role` 認角色與可存取名稱，不認 id | CT-05 |

判斷規則只有一條：答案是「每個 feature 都長這樣」，進模板；答案引用了 spec、下游或一次舊的失敗，變成不變量；答案是「這樣比較好看」，兩邊都不收，沒有理由的偏好寫成規則只會擋住好的產物（Ch 10 踩雷 5 的 `a：`）。六條不變量照 [Ch 9](./09-rule-files.md)「一、Rule file：一條規則、三個例子、一個 ID」的格式寫成 `rules/contract.md`，每條都有 Trigger、Rule、Why、Sign you skipped it、Enforced by 與 Good／Bad／Edge 三個例子；Good 一律是黃金產物的原文，Bad 取自 Ch 13 那次實跑犯過的錯，沒有實例的才用第四節的單行突變。以 CT-03 為例：

```markdown
## CT-03 A rule's rejection carries status, rule ID and the spec's exact text
Trigger: an FR in spec.md that cites a rule (R1-R4) and quotes a message.
Rule: one `;`-separated part of one cell holds a 4xx status, that rule ID and the quoted text in backticks.
Why: tests assert the text byte for byte (Ch 30's two drivers read the same `detail`); a paraphrase fails them.
Sign you skipped it: the status sits in one column, the text in another and the rule in a third.
Enforced by: contract_check.py CT-03
Good: ``409 R1 `Taipei is taken 10:00-11:00 by Bob` (FR-002)``
Bad (reworded): ``409 R1 `Taipei is already booked` (FR-002)``
Edge (several rejections share a cell, one part each): ``409 R1 ... (FR-002); 422 R2 ... (FR-003)``
```

其餘五條的 Rule 一行：CT-01 spec 定義的每個 `FR-NNN` 都出現在某一列；CT-02 契約引用的 FR、SC、R 編號都在這份 spec 裡定義過；CT-04 人是 `by`、錯誤文字是 `detail`，`holder`、`user`、`owner`、`reason`、`message`、`error` 不准出現在 code span 或 code block（spec 引用的原文除外，見第五節）；CT-05 寫到網頁的列用角色加反引號裡的可存取名稱，不用元素 id 或 CSS selector；CT-06 每一列至少引用一個本 feature 的 FR 或 SC。

## 四、contract_check.py：把完成條件寫成程式

`scripts/contract_check.py` 吃 feature 目錄，讀 `spec.md` 與 `contracts/interfaces.md`；形狀交給 Ch 10 的 `template_check.py`，問題前面標 `SHAPE`，讓 agent 知道該回頭讀模板還是規則：

```python
#!/usr/bin/env python3
"""contract_check.py: done check for contract-writer. FEATURE_DIR/contracts/interfaces.md vs FEATURE_DIR/spec.md
(rules CT-01..CT-06 in rules/contract.md) and vs templates/interfaces.md (shape, via Ch 10's template_check.py).
Exit: 0 no problems, 1 problems, 2 cannot run."""
import argparse, collections, os, re, sys
from template_check import check as shape_check, load

HERE = os.path.dirname(os.path.abspath(__file__))
IDS = re.compile(r"\b(?:FR|SC)-\d{3}\b|\bR\d\b")
AVOID = {"holder": "by", "user": "by", "owner": "by", "reason": "detail", "message": "detail", "error": "detail"}
WORD = re.compile(r"\b(" + "|".join(AVOID) + r")\b")
ROLE = re.compile(r"\b(textbox|textboxes|button|buttons|status|list|link)\b")
SEP = re.compile(r"\|[\s:|-]+\|")
PART = re.compile(r";(?=(?:[^`]*`[^`]*`)*[^`]*$)")   # a ";" outside code spans ends a part

def table_rows(lines):  # (lineno, cells) of every data row: not a header (a row above a separator), not a separator
    out = []
    for i, l in enumerate(lines):
        s, nxt = l.strip(), lines[i + 1].strip() if i + 1 < len(lines) else ""
        if s.startswith("|") and not SEP.fullmatch(s) and not SEP.fullmatch(nxt):
            out.append((i + 1, [c.strip() for c in s.strip("|").split("|")]))
    return out

def code_spans(lines):  # (lineno, code): each code span, or a whole line inside a fence; field names live here
    fence = False
    for n, l in enumerate(lines, 1):
        if l.strip().startswith(("```", "~~~")): fence = not fence; continue
        for c in [l] if fence else re.findall(r"`([^`]+)`", l):
            yield n, c

def check(spec_path, path):
    spec, lines = open(spec_path, encoding="utf-8").read(), open(path, encoding="utf-8").read().splitlines()
    probs, rows = [], table_rows(lines)
    bad = lambda n, rule, msg: probs.append((n, f"{path}:{n}: {rule} {msg}"))
    fr_lines = re.findall(r"^.*\*\*FR-\d{3}\*\*.*$", spec, re.M)               # '- **FR-002**: ...' lines
    defined, row_text = set(IDS.findall(spec)), " ".join(" ".join(c) for _, c in rows)
    quoted = {t for fl in fr_lines if re.search(r"\bR\d\b", fl) for t in re.findall(r'"([^"]+)"', fl)}
    parts = [(n, p) for n, cells in rows for c in cells for p in PART.split(c)]
    for fl in fr_lines:
        fr, rule = re.search(r"FR-\d{3}", fl)[0], re.search(r"\bR\d\b", fl)
        if not re.search(rf"\b{fr}\b", row_text):                             # CT-01
            bad(1, "CT-01", f"{fr} is not answered by any row; cite it in the row of the interface that serves it")
        for t in re.findall(r'"([^"]+)"', fl) if rule else []:                # CT-03
            if not any(re.search(r"\b4\d\d\b", p) and re.search(rf"\b{rule[0]}\b", p) and f"`{t}`" in p
                       for _, p in parts):
                n = next((n for n, p in parts if t in p or fr in p), 1)
                bad(n, "CT-03", f"{fr} ({rule[0]}): write '<4xx> {rule[0]} `{t}` ({fr})' as one part of a cell, "
                                "with the spec's text exactly")
    for n, l in enumerate(lines, 1):                                           # CT-02
        for x in sorted(set(IDS.findall(l)) - defined):
            bad(n, "CT-02", f"{x} is not defined in {spec_path}; drop it, or add it to the spec first")
    for n, w in sorted({(n, w) for n, c in code_spans(lines) if c not in quoted for w in WORD.findall(c)}):  # CT-04
        bad(n, "CT-04", f"`{w}` is not house vocabulary; use `{AVOID[w]}`")
    for n, l in enumerate(lines, 1):                                           # CT-05: no ids, no selectors
        if re.match(r"\|\s*(Id|ID|id)\s*\|", l.strip()) or re.search(r"`#[\w-]+`|\bid=", l):
            bad(n, "CT-05", "a UI control is named by element id; name it by role and accessible name")
    if any(re.search(r"\bpage\b", l) for l in fr_lines) and not any(
            "page" in c[0] and ROLE.search(c[0]) and "`" in c[0] for _, c in rows):
        bad(1, "CT-05", "spec.md asks for a web page, but no row names its controls by role and accessible name")
    for n, cells in rows:                                                      # CT-06
        if not re.search(r"\b(?:FR|SC)-\d{3}\b", " ".join(cells)):
            bad(n, "CT-06", f'row "{cells[0][:30]}" cites no FR or SC; name the requirement that needs it')
    return [msg for _, msg in sorted(probs, key=lambda p: p[0])]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("features", nargs="+", help="feature dirs holding spec.md and contracts/interfaces.md")
    ap.add_argument("--contract", help="check this file instead of FEATURE/contracts/interfaces.md (one feature only)")
    ap.add_argument("--template", default=os.path.join(HERE, "..", "templates", "interfaces.md"),
                    help="shape template (default: ../templates/interfaces.md next to this script)")
    ap.add_argument("--no-shape", action="store_true", help="skip the template; check CT rules only")
    a = ap.parse_args()
    if a.contract and len(a.features) > 1: ap.error("--contract works with one feature dir")
    spec_t = None
    if not a.no_shape:
        spec_t, asked = load(a.template)
        if asked: ap.error(f"{a.template} has open [NEEDS CLARIFICATION] on line(s) {asked}; decide them first")
    probs = []
    for d in a.features:
        path = a.contract or os.path.join(d, "contracts", "interfaces.md")
        if spec_t is not None:   # shape problems from Ch 10's checker, tagged so the agent knows which file to read
            probs += [re.sub(r"^(.*?:\d+: )", r"\1SHAPE ", m, count=1) for m in shape_check(spec_t, path, False)]
        probs += check(os.path.join(d, "spec.md"), path)
    tally = collections.Counter(re.search(r":\d+: (\S+)", p)[1] for p in probs).most_common()
    print(*probs, f"{len(a.features)} files, {len(probs)} problems" +
          (f" ({', '.join(f'{k} {v}' for k, v in tally)})" if tally else ""), sep="\n")
    return 1 if probs else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "problems found"
    except OSError as e: print(f"error: {e.filename}: {e.strerror}", file=sys.stderr); sys.exit(2)
```

這是最終版。第 14 行的 `PART` 與第 37 行的 `quoted`，是第五節起草 SKILL.md 時 Claude 指出衝突之後才加的；第一版直接 `c.split(";")`，CT-04 逐行而不是逐個 code span 比對，也不放過 spec 原文裡的字，另外還有一個沒用到、後來拿掉的 `--json`。摘要行附上按規則的計數，因為下面會看到，問題的**數量**不等於嚴重度。

**第一次跑，抓到的是黃金產物自己：**

```
$ python scripts/contract_check.py golden/001-room-booking; echo "exit=$?"
golden/001-room-booking/contracts/interfaces.md:1: SHAPE "Contracts: Room Booking (FR-001 to FR-005)" lacks the fixed line "Errors use FastAPI's `{"detail": "<text>"}` with the spec's exact text; the page shows that text unchanged."
golden/001-room-booking/contracts/interfaces.md:2: SHAPE 1 line(s) in "Contracts: Room Booking (FR-001 to FR-005)" should start like "| `POST" (shape "| `a"), e.g. "Errors use FastAPI's"
golden/001-room-booking/contracts/interfaces.md:1: CT-01 FR-005 is not answered by any row; cite it in the row of the interface that serves it
golden/001-room-booking/contracts/interfaces.md:9: CT-06 row "`GET /` page: textboxes `Room`" cites no FR or SC; name the requirement that needs it
1 files, 4 problems (SHAPE 2, CT-01 1, CT-06 1)
exit=1
```

兩個 SHAPE 是同一件事：固定行尾巴掛著 `(SC-002)`，就不再是固定行，又被當成內容去比形狀。CT-01 與 CT-06 也是同一件事：FR-005（網頁）只出現在標題的範圍裡，網頁那一列沒有引用任何 ID。規則是對的，錯的是黃金產物，所以改黃金產物，不放寬規則（改之前先 `mkdir -p history`，把原檔複製成 `history/interfaces.v1.md` 留底）：刪掉第 2 行句尾的 ` (SC-002)`，把網頁那列的 ``says `Booked Taipei` `` 改成 ``says `Booked Taipei` (FR-005, SC-002)``。再跑一次是 `1 files, 0 problems`，exit 0。

**再拿 Ch 13 那次真實的 `/speckit-plan` 產物來試。**它把契約拆成 `http-api.md` 與 `web-page.md` 兩檔，預設路徑先失敗，回 2（跑不起來），不是 1：

```
$ python scripts/contract_check.py samples/stock-001; echo "exit=$?"
error: samples/stock-001/contracts/interfaces.md: No such file or directory
exit=2
$ cat samples/stock-001/contracts/{http-api,web-page}.md > samples/stock-001/contracts/both.md && python scripts/contract_check.py samples/stock-001 --contract samples/stock-001/contracts/both.md
…（略）
samples/stock-001/contracts/both.md:1: CT-01 FR-002 is not answered by any row; cite it in the row of the interface that serves it
…（略）
samples/stock-001/contracts/both.md:21: CT-04 `reason` is not house vocabulary; use `detail`
…（略）
1 files, 32 problems (CT-06 13, CT-04 10, CT-01 4, CT-05 2, CT-03 2, SHAPE 1)
```

32 個問題要分開讀。CT-04 的 10 個（`holder` 6 個、`reason` 3 個，另一個是 `{"reason": "<message>"}` 裡的 `message`）與 CT-05 的 2 個，正是 Ch 13 人審找到的 `holder`、`reason` 與元素 id，這次由程式找到。CT-01 的 4 個是真的漏：FR-002 到 FR-005 沒有一列負責（FR-004 只寫在段落文字裡）。CT-03 的 2 個是狀態碼、訊息、規則 ID 分在三欄。CT-06 的 13 個多半是格式差異：它另外畫了元素表、行為表，每一列都沒有 FR。數量最多的一類，不是最嚴重的一類。

**最後，每條規則都要先看它失敗。**從黃金產物各做一份只改一行的突變（lab 裡由 `make_mutants.sh` 用 `sed` 做，一份一個目錄，再用迴圈對每個目錄跑 `contract_check.py` 取最後一行）：

| 突變 | 對黃金產物的單行改動 | 結果 |
|---|---|---|
| `ct01-fr-dropped` | `GET /bookings` 那列的 `(FR-004)` 改成 `(SC-001)` | `1 files, 1 problems (CT-01 1)` |
| `ct02-invented-id` | 同一處改成 `(FR-004, FR-006)` | `1 files, 1 problems (CT-02 1)` |
| `ct03-paraphrase` | R1 的訊息改成 `` `Taipei is already booked` `` | `1 files, 1 problems (CT-03 1)` |
| `ct04-holder` | POST 那列的 `` `by` `` 改成 `` `holder` `` | `1 files, 1 problems (CT-04 1)` |
| `ct05-element-ids` | ``textboxes `Room` `Start` `End` `Name`, button `Book` `` 改成 ``inputs `#room` `#start` `#end` `#name` `#book` `` | `1 files, 2 problems (CT-05 2)` |
| `ct06-unanchored` | 結尾多一列，介面格是連反引號在內的 `` `GET /health` ``（少了反引號會多一個 SHAPE），成功格 `200, the server is up`，拒絕格 `none`，沒有任何 ID | `1 files, 1 problems (CT-06 1)` |
| `shape-column` | 表頭的 `Rejected` 改成 `Errors` | `1 files, 2 problems (SHAPE 2)` |

每個突變只打中它要打的規則（CT-05 兩則：一則抓 `#name` 這類 selector，一則抓「網頁那列不再用角色命名」；SHAPE 兩則：少了固定的表頭，改過的表頭又不符合列的形狀）。突變的範圍要窄：一次改兩件事，就分不出是哪條規則在工作。

## 底層機制：檢查器才是 skill 真正的規格

```
/contract-writer specs/002-…      斜線指令在執行前展開，SKILL.md 本文進 context（Ch 7、Ch 4）
  │ 照 SKILL.md 的「Bundled files」表讀：rules → template → example（用到才載入）
  ▼
寫 contracts/interfaces.md ──► contract_check.py ──► "1 files, N problems (CT-0x …)"
  ▲                               │ 只有輸出進 context，原始碼不必讀
  └──── 照訊息裡的改法修 ◄─────────┘ exit 0 才是完成；exit 2 是跑不起來，不算完成
```

完成條件把「好不好」從 agent 的自我感覺搬到一支程式裡，代價是那支程式成了事實上的規格。[Ch 10](./10-templates-and-scripts.md) 的執行 3 裡，agent 讀了檢查器原始碼、照它寫；本章第五節起草 SKILL.md 的 Claude 也讀了，還找出規則之間的矛盾。所以檢查器裡沒寫的，不保證；寫錯的，agent 會照錯的做。

兩類知識各住一個地方。**形狀**是文件內部的事：標題、固定行、每列怎麼開頭，`template_check.py` 只看這一份文件，看不到 spec。**不變量**是文件之間的事：FR 有沒有被接住、訊息是不是 spec 的原文、欄位是不是 house 用語，CT 規則讀得到 spec，卻不管版面。拆成兩支，任何一邊改了都不必動另一邊。

`skeleton.py` 的角色接近 approval testing 的 scrubber。ApprovalTests 的文件說，輸出有不穩定的部分時，用 scrubber「convert the non-deterministic text to something stable」再和核准過的版本比對。我們的「不穩定部分」是整份內容，因為每個 feature 本來就該不同；`skeleton.py` 是一支把內容全部刷掉的 scrubber，讓不同 feature 的契約都能和同一份核准過的骨架比。

## 五、讓 Claude 起草 SKILL.md

起草的輸入不是一段描述，是整套產物。`draft-run/` 裡放 `contract-writer/`（`examples/001-room-booking/` 的 spec 與改好的黃金產物、`templates/`、`rules/`、`scripts/` 的三支腳本，沒有 SKILL.md）和一個空的 `drafts/`；`.claude/` 是受保護路徑，`-p` 模式寫不進去，所以讓它寫到 `drafts/`（[Ch 7](./07-messy-baseline.md) 的作法）。提示詞 `runs/run1-prompt.txt`：

```text
contract-writer/ holds everything for a Claude Code skill except its SKILL.md:
- examples/001-room-booking/: a spec.md and the contract we want for it, contracts/interfaces.md (the golden copy)
- templates/interfaces.md: the shape cut from that golden copy
- rules/contract.md: rules CT-01..CT-06, one for each thing the golden copy gets right
- scripts/contract_check.py: the done check (it imports the other two scripts)

Write drafts/SKILL.md for this skill. Name: contract-writer. Given a feature dir such as specs/002-cancel-booking, it writes <feature dir>/contracts/interfaces.md from <feature dir>/spec.md. Its done criterion is that `python3 <skill dir>/scripts/contract_check.py <feature dir>` prints `0 problems`; nothing else counts as done. Keep the body under 60 lines, point to the bundled files instead of copying them, and say when to read each. Run the check on the example first to see what passing looks like. Write only drafts/SKILL.md.
```

```bash
cd draft-run && claude -p "$(cat ../runs/run1-prompt.txt)" --tools "Read,Write,Edit,Glob,Grep,Bash" --allowedTools "Read Glob Grep Edit(drafts/**) Bash(python3 contract-writer/scripts/contract_check.py *)" \
  --permission-prompts none --setting-sources project --strict-mcp-config --no-session-persistence --max-turns 12 --max-budget-usd 1.5 --output-format stream-json --verbose < /dev/null > ../runs/run1-draft.jsonl
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

90 秒（取結果事件的 `duration_ms`，下同）、13 turns、0.381 美元，12 次工具呼叫：讀了規則、模板、範例與三支腳本的原始碼，兩次想跑檢查都被拒。第一次是 `python3 … ; echo "exit=$?"`，拒絕訊息寫「The following part requires approval: echo "exit=$?"」，[權限文件](https://code.claude.com/docs/en/permissions)寫明複合指令的每一段都要被允許（同一份文件把 `echo` 列在不必詢問的唯讀指令裡，這段帶 `$?` 的 echo 仍被要求核准，原因我們沒有查明）；第二次改用絕對路徑，對不上相對路徑的 allow 規則。它照實回報「I couldn't run the check on the example, so I never saw it pass」，改用讀程式手算，預測 `1 files, 0 problems`（我們跑出來就是這個）。它寫出 59 行的 SKILL.md，並在最後列出套件裡三個真的問題：

（1）**CT-03 與 CT-04 互相矛盾**：spec 原文若含 `user`、`error` 這類字，CT-03 要它放進反引號，CT-04 又禁止，永遠到不了 0；原文含 `;` 也會被切斷。我們做了一個 fixture `edge/user-in-message/`：spec 只有一行 FR-001，原文是 `"only the user who booked can cancel; ask Bob"`（R4）；契約照模板寫，唯一一列是 `` | `DELETE /bookings/{id}?by=<name>` | 204, the booking is gone (FR-001) | 403 R4 `only the user who booked can cancel; ask Bob` (FR-001) | ``。第一版檢查器報 `1 files, 2 problems (CT-03 1, CT-04 1)`，改成上面的 `PART` 與 `quoted` 之後是 `1 files, 0 problems`。（2）**CT-02 的 Edge 例子用不了**：它說段落文字可以提別的 feature，但形狀規則不准表格外有其他文字，改成寫在格子裡的例子。（3）**模板第一行註解的路徑過時**：少了 `contracts/`（第二節列的是修正後的模板）。

審過的草稿節錄（第六節實際使用的版本）：

```markdown
## Done

Done when `python3 ${CLAUDE_SKILL_DIR}/scripts/contract_check.py <feature dir>` exits 0 and its last line reads
`1 files, 0 problems`. Nothing else counts: not a contract
that looks right, not `10 problems` (which also contains `0 problems`), not exit 2 (the check could not run).
Run it with no flags. `--no-shape`, `--contract` and `--template` check something other than the done criterion.
…（略：Bundled files 表、Steps 四步、What the check will not forgive 三條、When to stop）
```

「Bundled files」是一張四列的表，每個檔各寫一句何時讀：規則「In full before writing」、模板當骨架、範例「side by side, to see how FRs become rows」、檢查器原始碼「Only when a problem's message does not tell you what to change」。幾處是它自己想到的：「`10 problems` 也含有 `0 problems`」這個子字串陷阱，所以完成條件加上 exit code；禁止 `--no-shape` 這類旗標，免得 agent 換一支比較鬆的檢查；明講範例只示範形式，不准照抄 `POST /reset`。我們審過之後改了四處，每一處都有出處：

| 草稿原文 | 改成 | 為什麼 |
|---|---|---|
| description 寫 `<feature dir>/contracts/interfaces.md` | `contracts/interfaces.md in the feature dir` | skill-creator 的 `quick_validate.py` 報 `Description cannot contain angle brackets (< or >)`（Anthropic 的規格寫 description「Cannot contain XML tags」）；pstack 的 skill-lint 沒擋 |
| ``python3 <skill dir>/scripts/…`` | `${CLAUDE_SKILL_DIR}` | [官方文件](https://code.claude.com/docs/en/skills)：它是 SKILL.md 所在的目錄，用來指到 skill 附帶的腳本，「regardless of the current working directory」；Ch 10 看過它在本文裡展開成絕對路徑 |
| 完成條件的指令放在 code block | 寫成一行 ``Done when `…` exits 0`` | [Ch 7](./07-messy-baseline.md) 的 `skill_metrics.py` 報「no done criterion that names a command」，它只認同一行裡的反引號指令 |
| spec 沒給狀態碼、欄位名就停下來回報 | 狀態碼與欄位名由 skill 決定，列在最後的訊息裡 | 和黃金產物矛盾：Ch 13 的 spec 也沒給 409、422，是寫契約的人選的；照草稿做，每個 feature 都會停 |

改完之後 skill-lint 是 `1 skills, 0 problems`，`quick_validate.py` 是 `Skill is valid!`。`skill_metrics.py` 仍報 19 條規則沒有例子，但 27 條「規則」裡有 17 條是 `examples/` 那份 spec 的條列，拿掉範例目錄再量一次，是 10 條、2 條沒例子：量測工具分不出範例是資料。我們還漏審一處：草稿最後一條「原文含 `holder` 等字或 `;` 就停」描述的是第一版檢查器的矛盾，修好檢查器卻沒回頭刪，帶著它跑了第六節，之後才改成只剩 `|` 那一句。

## 六、換一個 feature 重新產生，再比形狀

第二個 feature 是取消預約（R4）。spec 是本章為實驗寫的，沿用 Ch 13 的格式與 [Ch 5](./05-skill-creator.md) 提議的 Then 用語；FR 刻意用業務的話（「the person who made a booking」「Rejected: <reason>」），不寫 `by`、`detail`，也不寫狀態碼。`project/specs/002-cancel-booking/spec.md`（顯示時省略空行）：

```markdown
# Feature Specification: Cancel a Booking
**Feature Branch**: `002-cancel-booking` | **Created**: 2026-11-02 | **Status**: Draft
## User Scenarios & Testing *(mandatory)*
### User Story 1 - Cancel my own booking (Priority: P1)
Bob's meeting moved, so he gives the room back for others to book.
**Acceptance Scenarios**:
1. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Bob cancels that booking, **Then** the booking is cancelled
2. **Given** Bob has booked "Taipei" from 10:00 to 11:00, **When** Alice cancels that booking, **Then** the cancellation is rejected because only Bob can cancel this booking
3. **Given** there is no booking 7, **When** Alice cancels booking 7, **Then** the cancellation is rejected because booking 7 not found
### Edge Cases
- A cancelled slot is free at once: booking "Taipei" from 10:00 to 11:00 again is confirmed.
- A booking keeps its id for life; the id of a cancelled booking is never handed out again.
## Requirements *(mandatory)*
### Functional Requirements
- **FR-001**: The person who made a booking MUST be able to cancel it by its id (R4).
- **FR-002**: System MUST refuse a cancel by anyone else with "only Bob can cancel this booking" (R4).
- **FR-003**: System MUST refuse a cancel of an unknown booking with "booking 7 not found" (R4).
- **FR-004**: Each booking listed on the web page MUST have a Cancel button; the page shows "Cancelled Taipei" or "Rejected: <reason>".
### Key Entities
- **Booking**: a room held by one person for a half-open interval today; has an id. Cancelling removes it.
## Success Criteria *(mandatory)*
- **SC-001**: Every acceptance scenario above passes against both the HTTP API and the web page.
## Assumptions
- No login: who you are is the name you type, so a cancel carries the canceller's name.
```

實驗設計：`project/` 只有這份 spec 和 `.claude/skills/contract-writer/`（審過的套件），沒有 001 的契約，黃金產物只活在 skill 的 `examples/` 裡；第七節的對照專案是同一份 spec，skill 目錄裡卻只有一份手寫的 SKILL.md（沒有規則、模板、腳本與範例），兩次執行的差別只來自 skill。指令的其餘旗標和第五節相同，差別是：在 `project/` 裡跑，提示詞是 `"/contract-writer specs/002-cancel-booking"`，`--tools` 加上 `Skill`，`--allowedTools` 是 `Read Glob Grep Skill Edit(specs/002-cancel-booking/**) Bash(mkdir *) Bash(echo *)` 再加上檢查器的三種寫法（相對路徑，以及 `/tmp/…`、`/private/tmp/…` 兩種絕對路徑；`${CLAUDE_SKILL_DIR}` 展開後是絕對路徑，第五節就卡在這裡），`--max-turns 15 --max-budget-usd 2`。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

34 秒、8 turns、0.199 美元，7 次工具呼叫：列目錄與讀 spec，照「Bundled files」表的順序讀規則、模板、範例 spec、範例契約，沒有讀檢查器原始碼，寫檔，再跑 `python3 .claude/skills/contract-writer/scripts/contract_check.py specs/002-cancel-booking; echo "exit=$?"; grep -c 'NEEDS CLARIFICATION' specs/002-cancel-booking/spec.md`（這次 `Bash(echo *)` 在 allow 清單裡，同樣帶 `echo "exit=$?"` 的複合指令就過了），第一次就 `1 files, 0 problems`。產物：

```markdown
# Contracts: Cancel a Booking (FR-001 to FR-004)
Errors use FastAPI's `{"detail": "<text>"}` with the spec's exact text; the page shows that text unchanged.

| Interface | Success | Rejected |
|---|---|---|
| `DELETE /bookings/{id}`: `by` | 200, the cancelled booking (FR-001) | 403 R4 `only Bob can cancel this booking` (FR-002); 404 R4 `booking 7 not found` (FR-003) |
| `GET /` page: textbox `Name`, list `Bookings` with a button `Cancel` on each booking | `<p role=status>` says `Cancelled Taipei` (FR-004, SC-001) | `Rejected: <detail>` |
```

```
$ python scripts/contract_check.py project/specs/002-cancel-booking && python scripts/skeleton.py --against golden/001-room-booking/contracts/interfaces.md project/specs/002-cancel-booking/contracts/interfaces.md
1 files, 0 problems
1 files, 0 differ from golden/001-room-booking/contracts/interfaces.md
```

形狀和黃金產物完全相同；spec 的「the person who made a booking」與 `<reason>` 被翻成 `by` 與 `<detail>`；沒有照抄 `POST /reset`。它最後的訊息照 SKILL.md 的要求列出自己的選擇與 spec 的缺口（節錄）：

```text
- **Cancel route:** `DELETE /bookings/{id}`, with the canceller's name in a `by` field. The contract doesn't say whether `by` goes in the query string or the body, so settle that in the plan, because some HTTP clients drop `DELETE` bodies.
- **Success:** 200 returning the cancelled booking rather than 204, so the page has the room name for `Cancelled Taipei`.
…（略）
- **R4 undefined:** the spec cites R4 on three FRs but never defines it. The check passes on those citations alone.
```

前兩個選擇，正好落在黃金產物教不到的地方：它沒有 `DELETE`，學不到本課 R4 約定的 204；它的欄位都在 body，沒有示範 query 參數。檢查器全放行，因為「204 還是 200」「query 還是 body」是語意，不是形狀也不是和 spec 的關係。最後一條則打中 CT-02 的弱點：R4 只要在 spec 裡出現過就算「有定義」。審查時我們只改了第一列，把介面寫成 `` `DELETE /bookings/{id}?by=<name>`: `id` from the list, `by` in the query ``、成功格寫成 `204, the booking is gone (FR-001)`，存成第二份黃金產物 `golden/002-cancel-booking/`，兩份一起跑檢查是 `2 files, 0 problems`。

## 七、對照：先寫 prompt 的版本

對照組的 SKILL.md 是我們模仿 prompt-first 手寫的，只用寫黃金產物**之前**就知道的事，該有的常識都有。frontmatter 與第五節的版本相同，只是 description 少了「and iterates until contract_check.py passes」；本文：

```markdown
# contract-writer

Write `<feature dir>/contracts/interfaces.md` for the feature described in `<feature dir>/spec.md`.
Write no other file.

1. Read spec.md in full: user stories, acceptance scenarios, functional requirements (FR-xxx), success criteria.
2. List every interface the feature exposes: HTTP endpoints and the web page.
3. For each endpoint give the method and path, the request fields with their types, the success status and body,
   and every error: status code, message, and when it happens.
4. For the web page, list the controls a user works with and what the page shows after each action.
5. Use the exact messages the spec quotes. Cite the FR IDs each interface serves.
6. Keep it short and precise. Do not add interfaces the spec does not need.
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

同樣的指令在 `project-pf/` 跑（allow 清單少了檢查器，因為沒有）：29 秒、5 turns、0.142 美元，4 次工具呼叫，寫出 59 行。它本身是一份用心的契約：訊息照抄、引用了 FR、寫出 404 先於 403 的檢查順序與空白姓名回 403，還提醒「The existing booking list must give the page each booking's id」。用本課的下游來量：

```
$ python scripts/contract_check.py project-pf/specs/002-cancel-booking
…（略）
project-pf/specs/002-cancel-booking/contracts/interfaces.md:35: CT-04 `error` is not house vocabulary; use `detail`
…（略）
project-pf/specs/002-cancel-booking/contracts/interfaces.md:39: CT-03 FR-003 (R4): write '<4xx> R4 `booking 7 not found` (FR-003)' as one part of a cell, with the spec's text exactly
…（略）
1 files, 16 problems (CT-06 7, CT-04 3, CT-01 2, CT-03 2, SHAPE 1, CT-05 1)
```

| | artifact-first（第六節） | prompt-first（本節） |
|---|---|---|
| 長度與骨架 | 7 行；`0 differ` | 59 行；骨架從 5 行變 32 行，`1 differ` |
| `contract_check.py` | 第一次就 0 problems | 16 problems |
| 介面 | `DELETE /bookings/{id}` | `POST /bookings/{id}/cancel`（理由：DELETE 帶 body 的支援差） |
| 人與錯誤欄位 | `by`、`detail` | `name`、`{ "error": "<message>" }` |
| 規則 ID | 兩個拒絕都標 R4 | 全文沒有 R4 |

各一次只能當個案，但差異的方向很清楚：prompt-first 的 agent 每一個選擇都有道理，只是每一個都是它自己的道理；artifact-first 把團隊的選擇放進黃金產物，agent 要做的是對齊。另一面也要看見：prompt-first 那份寫出的語意（檢查順序、空白姓名）比我們的三欄表格多，黃金產物的格式同時也是天花板。CT-04 也有盲點：它抓到 `error`、`message`，沒抓到 `name`，黑名單只認得看過的同義字。我們試過把 `name` 加進 `AVOID`：本節從 16 個問題變 20 個，但第五節那個 fixture 與第二份黃金產物裡的 `?by=<name>`（值的佔位符），以及 CT-05 突變的 `#name`，立刻各多一個誤報，最後沒有加，留給人審與動手練習 2。

## 對比與取捨

| 做法 | 「好」寫在哪 | 完成條件 | 優點 | 缺點 | 適用 |
|---|---|---|---|---|---|
| prompt-first | SKILL.md 的文字 | agent 自評 | 起步最快 | 目標看不見，每次的道理不同 | 一次性工作、探索 |
| 模板先行（Spec Kit 的模板） | 模板 | 佔位符填完 | 段落一致 | 不管內容與跨文件關係；表單不是閘門（Ch 13） | 文件骨架 |
| 只給範例（Anthropic 的 Examples pattern） | SKILL.md 裡的範例 | 無 | 風格傳達得比描述清楚 | 可能照抄內容；無法判定完成 | 文風、格式示意 |
| artifact-first（本章） | 黃金產物 → 模板＋不變量＋檢查器 | 檢查器 exit 0 | 可重跑、可審、能做回歸 | 前期成本；黃金產物也是天花板；一份範例教不了沒出現的情況 | 一再產生的產物：契約、報告、計畫 |
| 先寫 eval（[Ch 5](./05-skill-creator.md) 的 skill-creator） | 測試提示與斷言 | benchmark 分數 | 量得到觸發與行為 | 要 grader、要跑很多次 | 輸出模糊、要比較版本 |

## 踩雷集錦

1. **錯誤直覺：黃金產物就是目前最好的那份，不必再審。** 正確認識：第一次跑檢查器，黃金產物自己就有 4 個問題：固定行夾著只屬於這個 feature 的 `SC-002`，FR-005 只出現在標題。寫檢查器的過程就是在審黃金產物；改的是黃金產物，不是放寬規則。
2. **錯誤直覺：一份範例也能抽模板，騙一下提取器就好。** 正確認識：同一路徑給兩次，Ch 10 的守門會擋；換成一份檔名不同的相同複本就騙得過，得到逐字等於黃金產物的模板，在下一個 feature 的標題就失敗。哪一行是固定的，只有作者知道。
3. **錯誤直覺：檢查器 0 problems，就是對的契約。** 正確認識：第六節的 200 與沒講清楚的 `by`、第七節的 `name`，檢查器都放行。它保證形狀與對應，語意留給 review（[Ch 13](./13-plan-anatomy-review.md) 清單「人判」那欄）；想多擋一個字，記得每次改檢查器都把黃金產物、突變、fixture 全部重跑，加 `name` 就多出三個誤報。
4. **錯誤直覺：Claude 起草的 SKILL.md 讀起來合理就能用。** 正確認識：草稿要求「spec 沒給狀態碼就停」，和黃金產物互相矛盾；description 裡的 `<feature dir>` 過不了 `quick_validate.py`。拿草稿對黃金產物與驗證器各審一次；改了檢查器也要回頭 grep SKILL.md，我們就帶著一條過時的規則跑了第六節。

## 進階：第二份黃金產物、穩定性與生命週期

- **讓 Ch 10 的提取器接手**：有了兩份審過的契約，`template_extract.py golden/001-room-booking/contracts/interfaces.md golden/002-cancel-booking/contracts/interfaces.md -o templates/interfaces.md --dry-run` 不再拒絕：`1 sections, 1 shape rules`，固定行與形狀規則和手寫模板相同，拿它檢查兩份黃金產物是 `2 files, 0 problems`。差在標題變成 `# {{標題}}`，「Contracts:」又掉了（Ch 10 踩雷 2），表格的四列併成一個以「標題」命名的 placeholder。Ch 10 建議至少三份；在那之前，手寫模板加來回測試比較可靠。
- **讓檢查器變成 hook**：照 [Ch 3](./03-propose-constrain-feedback.md) 的 `run_tests.py`，PostToolUse 比對 `Edit|Write`，寫入 `specs/*/contracts/interfaces.md` 時跑 `contract_check.py`，有問題就回 `{"decision": "block", "reason": …}`，不靠 agent 記得跑。本段未實測；驗證方法是像 Ch 3 一樣把一筆 hook JSON 用管線餵給腳本，看 stdout 與 exit code。
- **黃金產物的生命週期**：house 慣例改了，先改黃金產物，再改模板、規則與檢查器，最後改 SKILL.md，四者一起進版控、一起 review（[pstack Ch 29](../pstack/29-eval-playbook.md) 的盲測可以比較改動前後的產物）。放進 skill `examples/` 的黃金產物描述的是團隊「現在的慣例」，生命週期像 [Ch 33](./33-truth-vs-plan.md) 說的 truth，只經由審過的改動更新；它原本所在的 `specs/001-room-booking/` 則是某次改動的 plan，合併後封存。

## 動手練習

1. **對 data-model 做一次 artifact-first**：[Ch 15](./15-research-and-analysis-skills.md) 沒拆 data-model，理由是還說不出它的檢查。拿 Ch 13 的黃金 `data-model.md`，逐行列出「為什麼對」，至少寫三條不變量（例如每個 Key Entity 有自己一節、每條 Rule 引用 FR 或 R、spec 裡每個 R 都出現），寫成檢查器，並用單行突變證明每條都會失敗。
2. **把 CT-04 從黑名單換成白名單**：介面格裡反引號包住的欄位、`{…}` 路徑參數、`?key=` 查詢參數，都必須屬於 `room`、`start`、`end`、`by`、`id`。目標：第七節的產物多出 `name` 的問題，兩份黃金產物與 `?by=<name>` 那個 fixture 仍是 0。
3. **量穩定性**：本章兩種 skill 各只跑一次，證明不了「穩定」。在三個乾淨的 `project/` 副本上各跑一次 `/contract-writer specs/002-cancel-booking`，記下第一次檢查的結果、骨架差異與成功狀態碼。三次都一樣代表什麼、不代表什麼？（我們沒有跑這組，結果請以你的實測為準。）

## 本章重點整理

- artifact-first 的順序：黃金產物 → 逐行問為什麼對 → 模板（形狀）與不變量（和 spec、下游的關係）→ 會失敗的檢查器 → 完成條件等於檢查器的 SKILL.md → 換 feature 重新產生並比形狀。
- 一份範例分不出固定行；`skeleton.py` 只比形狀，固定行由人寫進模板，再用來回測試確認模板接受黃金產物。
- 寫檢查器會反過來審黃金產物（4 個問題），讓 Claude 起草 SKILL.md 會反過來審檢查器、規則與模板（3 個問題）；每一層都要先看它失敗。
- 各實測一次：artifact-first 版第一次檢查就 0 problems、骨架 0 差異；prompt-first 版 16 problems。檢查器管不到語意：200 還是 204、query 還是 body 仍要人審，黃金產物沒示範過的情況它教不了。

## 自我檢核

- [ ] 不看本章，說出 artifact-first 的步驟，以及每一步的產物和證明它可信的檢查。
- [ ] 黃金產物的一行，怎麼判斷該進模板、變成不變量，還是兩邊都不收？各舉本章一例。
- [ ] 第六節的契約 0 problems、骨架與黃金產物相同，還是被人改了一行。改了什麼？為什麼檢查器抓不到？要讓它抓到，該改 spec、規則還是黃金產物，各有什麼代價？
- [ ] 想一份你團隊一再產出的文件（PR 描述、事故報告、API 文件）：它的黃金產物是哪一份？說出兩條會變成不變量的「為什麼對」。

## 延伸閱讀

- **[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)** — Anthropic（Claude Developer Platform 文件）
  - **讀哪裡**：「Build evaluations first」「Develop Skills iteratively with Claude」「Examples pattern」「Template pattern」，以及 Skill structure 裡 description 的限制。
  - **學到什麼**：「Create evaluations BEFORE writing extensive documentation」；一個 Claude（Claude A）寫 skill、另一個（Claude B）拿它做真實任務，觀察後再回頭改；description「Cannot contain XML tags」；`quick_validate.py` 做得更嚴，任何 `<`、`>` 都擋。
  - **和本章的關聯**：第五節是 Claude A，第六節是 Claude B；本章把「eval」具體化成一份黃金產物和一支檢查器。前提是讀過 [Ch 4](./04-prompt-files-to-skills.md) 的漸進揭露。
- **[ApprovalTests.Python](https://github.com/approvals/ApprovalTests.Python)** 的 README「Overview」，搭配 **[Scrubbers](https://github.com/approvals/ApprovalTests.cpp/blob/master/doc/explanations/Scrubbers.md)**（approvals 組織維護的開源專案）
  - **讀哪裡**：README 的 Overview 一段，與 Scrubbers 的 Introduction。
  - **學到什麼**：「Approvals work by comparing the test results to a golden master」；還沒有 golden master 時，把當下的結果存成快照當 golden master，之後結果一不同就交給人決定更新 master 還是改程式；不穩定的輸出先用 scrubber 轉成穩定的文字再比。
  - **和本章的關聯**：黃金產物就是 golden master，第六節把 run 2 審過再存成第二份，就是「核准」；`skeleton.py` 是一支極端的 scrubber。
- **[Readme Driven Development](https://tom.preston-werner.com/2010/08/23/readme-driven-development.html)** — Tom Preston-Werner（2010-08-23）
  - **讀哪裡**：全文，篇幅短。
  - **學到什麼**：「Write your Readme first」，在任何程式、測試之前；以及「A perfect implementation of the wrong specification is worthless」。
  - **和本章的關聯**：GitHub 共同創辦人用一頁說清楚「先寫給使用者看的產物」為什麼能讓討論落地。把 README 換成黃金產物，就是本章的起點。

`contract-writer` 一次只寫一份產物，有自己的完成檢查。`/speckit-plan` 的研究與系統分析也該這樣：下一章照同一個順序，把它們拆成兩個單一職責的 planner。

→ [Ch 15 把 /plan 拆開：technical-research 與 system-analysis skill](./15-research-and-analysis-skills.md)
