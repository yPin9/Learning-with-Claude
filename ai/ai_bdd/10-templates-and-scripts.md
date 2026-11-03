# Ch 10 — Template 與 Script：把重複的形狀和步驟抽出來

> **目標**：能從三份好產物抽出一份模板（固定行、`{{placeholder}}`、形狀規則，拿不準的地方留 `[NEEDS CLARIFICATION: …]`），用 `template_check.py` 讓它變成會失敗的檢查；能用 `script_candidates.py` 在 skill 的文字裡找出機器判斷得了的步驟，把它換成一行腳本呼叫；知道一支給 agent 用的腳本要有哪些介面：`--help`、`--dry-run`、`--json`、0/1/2 結束碼（exit code）、會指出改法的錯誤訊息。

> **環境**：Python 3.12.15 與課程 venv（`source /tmp/lab/aibdd/.venv/bin/activate`）、Claude Code 2.1.295（`claude -p` 預設模型 claude-opus-5-5，2026-11-02 跑了 3 次）、jq 1.7.1、Node v26.11.0（只跑 pstack 的 skill-lint）。實驗目錄 `/tmp/lab/aibdd/ch10-templates-scripts/`，三支腳本只用 stdlib，放在 `scripts/`。

## 為什麼需要這個？

skill 變胖，多半來自兩種重複。一種是**形狀**的重複：每次 BDD 改動都要交一份長得一樣的報告，於是 SKILL.md 寫一句「報告要有規則、情境、紅燈、綠燈」，或把整份範本貼進去（這個 repo 的 learn skill 就把 README、章節、練習三份範本 inline 在 821 行裡）。另一種是**步驟**的重複：「加了 tag 要登記到 `pytest.ini`」「確認紅燈是 AssertionError 而不是 ImportError」，寫成句子，交給 agent 每次自己做、自己用眼睛核對。前者讓每次交出的形狀都差一點，後者讓每次都可能漏一步。

在這之前，大家一次手工抽一條。Spec Kit 把模板當成主動約束（[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) 的「Layer 3：模板怎麼約束 LLM」）；[pstack 練習 D](../pstack/practice-d-plan-and-log.md) 的「用模板產生計畫」用 `pr-template.md` 加 `gen.py` 產出 180 行計畫；[pstack Ch 15](../pstack/15-delegation-meta-autonomy.md) 的「實驗：把這個 repo 的一條文字規則變成 lint」把禁用字規則寫成 `banned-words.mjs`；[pstack 期末專案](../pstack/final-project-course-mode.md)的「Step 1：讀 `learn` 技能，列出能機械檢查的規則」再把 learn 的結構規則寫成 `check-chapter.mjs`。每一條都有效，但每一條都要有人先讀完、先想到。Spec Kit 1.2.5 的 `speckit-specify` 更直接：它讓模型產生一份 16 項的 checklist，再自己 `Review the spec against each checklist item`；其中「All mandatory sections completed」與「No [NEEDS CLARIFICATION] markers remain」兩項，一支程式就判斷得了。本章做兩個提取器（extractor）：`template_extract.py` 從幾份好產物歸納出模板，`script_candidates.py` 從 skill 的文字找出該變成腳本的步驟。它們只**提出候選**，決定仍然在人身上。需要判斷的規則交給 [Ch 9](./09-rule-files.md) 的 rule file；[Ch 11](./11-skill-engineering-skill.md) 再把三種提取器組成 skill-engineering。

## 先建立直覺：模具與治具

工廠重複做同一個零件時，師傅會做兩樣東西：**模具**（mold）決定形狀，**治具**（jig）把每次都一樣的定位和鑽孔固定下來，不靠手感。template 是模具，script 是治具。開模的做法是把三個好零件疊起來：三個都一樣的地方是模具的固定部分，各自不同的地方是模穴（placeholder），疊不起來又看不出誰對的地方，不要自己修，標出來問人。做治具的做法是把作業指導書從頭讀一遍，用螢光筆標出「機器判斷得了對錯」的句子。這個比喻有一處會騙人：模具是硬的，模板卻可以逐段決定嚴格或彈性（Anthropic 的 skill 寫作指南把 Template pattern 分成「ALWAYS use this exact template structure」與「use your best judgment」兩種），第三節會看到一段資訊更多的紅燈說明，被嚴格的形狀規則判成問題。

```
 reports/*.md ─ template_extract.py ─► 模板草稿 ─ 人回答每個 [NEEDS CLARIFICATION] ─► templates/bdd-report.md
 新報告 ─ template_check.py（對照模板）─► 0 沒問題 / 1 有問題 / 2 跑不起來
 SKILL.md 的句子 ─ script_candidates.py ─► lint / run / edit / classify / template 候選
 人挑一條做成腳本 ─► SKILL.md 那句話換成一行呼叫 ─► 再掃一次，候選消失
```

## 一、模板提取器：三份好報告疊在一起

素材是三份「過去寫得好的」BDD 變更報告，放在 `reports/`：R1 重疊、R2 結束晚於開始、R4 只有預約人能取消。報告是我們為本章手寫的，裡面的 pytest 輸出是在 [Ch 0](./00-environment-setup.md) 冒煙專案（`smoke/`）的擴充版（加上 R2、R3、R4 的情境與實作）上真的跑出來的；重現本章不需要那個擴充版。三份的段落順序、表頭 `| 標籤 | 情境 | 狀態 |`、兩個 ` ```text ` 區塊和裡面的測試指令都逐字相同，也沒有空行，就是下面抽出來的模板裡那些不帶 `{{…}}`、也不是註解或問題的行。不同的只有下表這些；情境列寫成 `| @sc_book_01 | Booking a free slot | 新增 |`，狀態一律是「新增」，紅燈、綠燈區塊裡都只有測試指令和一行摘要。照表寫出三份，本節的輸出就能逐字重現：

| 位置 | `book-overlap.md` | `end-after-start.md` | `cancel-own-booking.md` |
|---|---|---|---|
| H1 | `# 變更報告：R1 同一間會議室不能重疊預約` | `# 變更報告：R2 結束時間必須晚於開始時間` | `# 變更報告：R4 只有預約人能取消` |
| 規則 | ``- **R1** 同一間會議室不能重疊預約；區間是半開的 `[start, end)`，10:00–11:00 和 11:00–12:00 不衝突。`` | ``- **R2** 結束時間必須晚於開始時間，相等也算錯，回 422 `end must be after start`。`` | `- **R4** 只有預約人能取消：別人取消回 403，id 不存在回 404，成功回 204。` |
| 情境列 | `@sc_book_01` Booking a free slot；`@sc_book_02` Back-to-back slots do not overlap；`@sc_book_03` Booking an overlapping slot | `@sc_book_04` End before start；`@sc_book_05` Zero-length booking | `@sc_cancel_01` The booker cancels their own booking；`@sc_cancel_02` Someone else cannot cancel it；`@sc_cancel_03` Cancelling an unknown booking |
| 紅燈區塊第二行 | `1 failed, 2 passed, 3 warnings in 0.12s` | `2 failed, 3 passed, 5 warnings in 0.13s` | `3 failed, 5 passed, 8 warnings in 0.15s` |
| 紅燈說明 | `紅的原因正確：規則還沒實作，API 照單全收。` | 與左欄逐字相同 | ``紅的原因正確：`DELETE /bookings/{id}` 還不存在，三個情境都拿到 404 `Not Found`。`` |
| 綠燈區塊第二行 | `3 passed, 3 warnings in 0.09s` | `5 passed, 5 warnings in 0.11s` | `8 passed, 8 warnings in 0.11s` |
| `## 假設與待決` | `- 假設：所有時間都在同一天，不處理跨日。` | **沒有這一段** | ``- 待決：取消後 id 會被重用（`len(BOOKINGS) + 1`），等 PM 決定要不要改。`` |

```python
#!/usr/bin/env python3
"""template_extract.py: derive a markdown template from 2+ good examples of one kind of document.
Lines all examples repeat stay; the rest become {{placeholders}}; varying lines that start alike give a shape
rule; a section some examples lack becomes a [NEEDS CLARIFICATION] question. Exit: 0 ok, 1 open questions, 2 error.
"""
import argparse, os, re, sys

FENCE, OPEN = re.compile(r"\s*(```|~~~)"), re.compile(r"\[NEEDS CLARIFICATION:[^\]]*\]")

def sections(text):  # [(level, title, lineno, [(lineno, line)])]; a '#' inside a code fence is content
    out, fence = [], False
    for n, line in enumerate(text.splitlines(), 1):
        fence ^= bool(FENCE.match(line))
        m = None if fence else re.match(r"(#{1,6}) (.+?)\s*$", line)
        if m: out.append((len(m[1]), m[2], n, []))
        elif out: out[-1][3].append((n, line.rstrip()))
    return out

def head(line):      # list/table markup + first word, cut after a colon: '- **R1** ...' -> '- **R1**'
    m = re.match(r"\s*((?:[-*+]|\d+\.|\|)?\s*)(\S+?[：:]|\S+)", line)
    return m[1] + m[2] if m else ""

def shape(line):     # letters (CJK too) -> a, digits -> 9: '| @sc_book_01' -> '| @a_a_9'
    return re.sub(r"\d+", "9", re.sub(r"[^\W\d_]+", "a", head(line)))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="good examples of one kind of document")
    ap.add_argument("-o", "--out", required=True, help="where to write the template")
    ap.add_argument("-n", "--dry-run", action="store_true", help="print the template; write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing template")
    a = ap.parse_args()
    docs = {os.path.realpath(f): sections(open(f, encoding="utf-8").read()) for f in a.files}  # one key per file
    if len(docs) < 2: ap.error("need 2+ examples to tell boilerplate from content; add another good example")
    if os.path.exists(a.out) and not (a.force or a.dry_run):
        ap.error(f"{a.out} exists and may hold a person's answers; compare with --dry-run, or overwrite with --force")
    key = lambda s: (s[0], "" if s[0] == 1 else s[1])    # every H1 is "the title"
    order = []                                            # merge the heading orders of all examples
    for secs in docs.values():
        at = -1
        for s in secs:
            if key(s) not in order: order.insert(at + 1, key(s))
            at = order.index(key(s))
    out = [f"<!-- template_extract.py: {len(docs)} examples: {', '.join(map(os.path.basename, docs))} -->"]
    asked, rules = [], 0
    for k in order:
        have = {f: s for f, secs in docs.items() for s in secs if key(s) == k}
        titles = {s[1] for s in have.values()}
        out.append("#" * k[0] + " " + ((title := titles.pop()) if len(titles) == 1 else "{{" + (title := "標題") + "}}"))
        if len(have) < len(docs):
            gone = "、".join(os.path.basename(f) for f in docs if f not in have)
            out += ["<!-- optional -->", f"[NEEDS CLARIFICATION: 「{title}」只出現在 {len(have)}/{len(docs)} "
                                         f"份範例（缺：{gone}）；必填還是選填？]"]
            asked.append(len(out))
        bodies = [[l for _, l in s[3]] for s in have.values()]
        sets = [{l.strip() for l in b if l.strip()} for b in bodies]
        fixed = set.intersection(*sets) if len(sets) >= 2 else set()   # one example proves nothing fixed
        varying, fence = [], False                        # (example index, line) outside code fences
        for i, l in ((i, l) for i, b in enumerate(bodies) for l in b):
            fence ^= bool(FENCE.match(l))
            if not fence and l.strip() and l.strip() not in fixed and not FENCE.match(l): varying.append((i, l))
        if len({shape(l) for _, l in varying}) == 1 and len({i for i, _ in varying}) >= 2:  # 2+ examples agree
            out.append(f'<!-- shape: "{shape(varying[0][1])}" e.g. "{head(varying[0][1])}" -->')
            rules += 1
        run = False                                       # consecutive varying lines share one placeholder
        for raw in bodies[0]:
            if (l := raw.strip()) and l not in fixed:
                if not run: out.append("{{" + f"{title}：例「{l if len(l) <= 32 else l[:32] + '…'}」" + "}}")
                run = True
            elif l or out[-1].strip(): out.append(raw); run = False   # keep fixed lines, no double blanks
    if a.dry_run: print("\n".join(out).rstrip())
    else:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write("\n".join(out).rstrip() + "\n")
    print(f"{'would write' if a.dry_run else 'wrote'} {a.out}: {len(order)} sections, {rules} shape rules",
          *[f"{a.out}:{n}: {out[n - 1]}" for n in asked], f"{len(docs)} files, {len(asked)} problems", sep="\n",
          file=sys.stderr if a.dry_run else sys.stdout)       # a dry run's stdout is the template itself
    return 1 if asked else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "problems found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

跑在三份報告上，產生 `templates/bdd-report.md`：

````
$ python scripts/template_extract.py reports/*.md -o templates/bdd-report.md; echo "exit=$?"
wrote templates/bdd-report.md: 6 sections, 4 shape rules
templates/bdd-report.md:25: [NEEDS CLARIFICATION: 「假設與待決」只出現在 2/3 份範例（缺：end-after-start.md）；必填還是選填？]
3 files, 1 problems
exit=1
$ cat templates/bdd-report.md
<!-- template_extract.py: 3 examples: book-overlap.md, cancel-own-booking.md, end-after-start.md -->
# {{標題}}
## 規則
<!-- shape: "- **a9**" e.g. "- **R1**" -->
{{規則：例「- **R1** 同一間會議室不能重疊預約；區間是半開的 `[s…」}}
## 情境
<!-- shape: "| @a_a_9" e.g. "| @sc_book_01" -->
| 標籤 | 情境 | 狀態 |
|---|---|---|
{{情境：例「| @sc_book_01 | Booking a free s…」}}
## 紅燈
<!-- shape: "a：" e.g. "紅的原因正確：" -->
```text
$ PYTHONPATH=.:tests pytest -q tests --driver api
{{紅燈：例「1 failed, 2 passed, 3 warnings i…」}}
```
{{紅燈：例「紅的原因正確：規則還沒實作，API 照單全收。」}}
## 綠燈
```text
$ PYTHONPATH=.:tests pytest -q tests --driver api
{{綠燈：例「3 passed, 3 warnings in 0.09s」}}
```
## 假設與待決
<!-- optional -->
[NEEDS CLARIFICATION: 「假設與待決」只出現在 2/3 份範例（缺：end-after-start.md）；必填還是選填？]
<!-- shape: "- a：" e.g. "- 假設：" -->
{{假設與待決：例「- 假設：所有時間都在同一天，不處理跨日。」}}
````

逐項讀。**固定行**是三份都逐字出現的行：表頭、分隔線、` ```text `，還有那行測試指令。三份報告跑同一個指令，本身就是一個訊號：每次都一樣的步驟，正是第四節要找的腳本候選（[Ch 3](./03-propose-constrain-feedback.md) 的 `run_tests.py` hook 就是它的治具）。**placeholder** `{{段落：例「…」}}` 標出各份不同的內容，連續的變動行合併成一個，附上第一份的開頭當提示。我們不用 Spec Kit 的 `[FEATURE NAME]` 寫法，因為方括號在 markdown 裡已經被連結、勾選框 `- [ ]`、tasks 的 `[P]` 與 `[NEEDS CLARIFICATION]` 本身用掉，檢查器分不出來；`{{` 沒有這個問題。**形狀規則** `<!-- shape: … -->` 是從變動行歸納出的「每一行該怎麼開頭」，機制見「底層機制」一節。最後，三份裡只有兩份有「假設與待決」：提取器分不出這是慣例還是疏漏，所以不猜，留一個問題，並以結束碼 1 表示「寫出來了，但還不能用」。

## 二、template_check.py：讓模板變成會失敗的檢查

模板只是一份文件，能擋住錯誤的是檢查器。`scripts/template_check.py` 讀同一種格式，和提取器共用 `sections` 與 `shape`：

```python
#!/usr/bin/env python3
"""template_check.py: check documents against a template written by template_extract.py.
Problems: a missing or misplaced heading, a missing fixed line, lines breaking a shape rule, an empty section,
an unfilled {{placeholder}}, an open [NEEDS CLARIFICATION] (unless --allow-open). Exit: 0 ok, 1 problems, 2 error.
"""
import argparse, json, re, sys
from template_extract import FENCE, OPEN, sections, shape

PH = re.compile(r"\{\{[^}]*\}\}")

def load(path):
    spec, asked = [], []
    for level, title, n, body in sections(open(path, encoding="utf-8").read()):
        s = {"level": level, "title": title, "optional": False, "shape": None, "fixed": [], "content": False,
             "rx": re.compile(".+".join(map(re.escape, PH.split(title))) + "$")}
        for i, l in ((i, l.strip()) for i, l in body):
            if m := re.fullmatch(r'<!-- shape: "(.*)" e\.g\. "(.*)" -->', l): s["shape"], s["example"] = m[1], m[2]
            elif l == "<!-- optional -->": s["optional"] = True
            elif l.startswith("<!--") or not l: continue   # other comments are notes for the writer
            elif OPEN.search(l): asked.append(i)
            elif PH.search(l): s["content"] = True
            else: s["fixed"].append(l)
        spec.append(s)
    return spec, asked

def check(spec, path, allow_open):
    lines, probs, last = open(path, encoding="utf-8").read().splitlines(), [], -1
    report = lambda n, msg: probs.append((n, f"{path}:{n}: {msg}"))
    secs = sections("\n".join(lines))
    for s in spec:
        hit = next((j for j, d in enumerate(secs) if d[0] == s["level"] and s["rx"].match(d[1])), None)
        if hit is None:
            if not s["optional"]: report(1, f'no heading matches "{"#" * s["level"]} {s["title"]}"; copy it in')
            continue
        _, title, n, body = secs[hit]
        if hit < last: report(n, f'section "{title}" is out of order; move it to where the template has it')
        last, present = max(last, hit), {l.strip() for _, l in body}
        for f in [f for f in s["fixed"] if f not in present]: report(n, f'"{title}" lacks the fixed line "{f}"')
        fence, content, off = False, 0, []
        for i, l in ((i, l.strip()) for i, l in body):
            fence ^= bool(FENCE.match(l))
            if l and l not in s["fixed"] and not l.startswith("<!--") and not FENCE.match(l):
                content += 1
                if s["shape"] and not fence and not (PH.search(l) or OPEN.search(l)) and shape(l) != s["shape"]:
                    off.append((i, l))
        if off: report(off[0][0], f'{len(off)} line(s) in "{title}" should start like "{s["example"]}" '
                                  f'(shape "{s["shape"]}"), e.g. "{off[0][1][:20]}"')   # one per section
        if s["content"] and not content: report(n, f'section "{title}" has no content; fill it')
    for i, l in enumerate(lines, 1):
        if l.strip().startswith("<!--"): continue
        for m in PH.findall(l): report(i, f"unfilled placeholder {m[:24]}…; replace it with real content")
        for m in [] if allow_open else OPEN.findall(l):
            report(i, f"open question {m[:30]}…]; answer it, or pass --allow-open while drafting")
    return [msg for _, msg in sorted(probs, key=lambda p: p[0])]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("template")
    ap.add_argument("files", nargs="+", help="documents that should follow the template")
    ap.add_argument("--allow-open", action="store_true", help="drafting: [NEEDS CLARIFICATION] is not a problem")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    a = ap.parse_args()
    spec, asked = load(a.template)
    if asked: ap.error(f"{a.template} still has [NEEDS CLARIFICATION] on line(s) {asked}; a person decides each "
                       "one, deletes the marker, and keeps or drops the <!-- optional --> above it")
    probs = [p for f in a.files for p in check(spec, f, a.allow_open)]
    if a.json: print(json.dumps({"files": len(a.files), "problems": probs}, ensure_ascii=False))
    else: print(*probs, f"{len(a.files)} files, {len(probs)} problems", sep="\n")
    return 1 if probs else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "problems found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

先拿還沒定稿的模板去檢查，它拒絕工作，回 2：`templates/bdd-report.md still has [NEEDS CLARIFICATION] on line(s) [25]; a person decides each one, deletes the marker, and keeps or drops the <!-- optional --> above it`。人做決定：我們選**必填**（沒寫假設的報告，看不出 agent 有沒有想過假設），把第 24–25 行換成一行 HTML 註解 `<!-- 必填。沒有就寫「- 無：本次沒有新的假設。」；沒有答案的問題寫成 [NEEDS CLARIFICATION: 問題]，答覆前報告不算完成 -->`。把給填寫者的說明放進註解是 Spec Kit 模板的做法（1.2.5 的 `spec-template.md` 寫的是 `ACTION REQUIRED: The content in this section represents placeholders.`）。接著跑**來回測試**（round trip）：模板必須接受它自己的來源。

```
$ python scripts/template_check.py templates/bdd-report.md reports/*.md; echo "exit=$?"
reports/end-after-start.md:1: no heading matches "## 假設與待決"; copy it in
3 files, 1 problems
exit=1
$ printf '## 假設與待決\n- 無：本次沒有新的假設。\n' >> reports/end-after-start.md
$ python scripts/template_check.py templates/bdd-report.md reports/*.md; echo "exit=$?"
3 files, 0 problems
exit=0
```

失敗的那一份不是壞報告，是「必填」這個決定的後果；來回測試讓代價立刻浮現：要嘛補範例，要嘛改回選填。範例之間的段落順序若不一致，也會在這一步以「out of order」現形，所以提取器不必另外偵測順序衝突。再拿一份壞草稿試。`drafts/office-hours.md` 模擬 agent 填到一半：規則沒用 `- **R3**` 開頭、留了一個 placeholder、漏掉綠燈、還有一個沒回答的問題：

````markdown
# 變更報告：R3 只能訂辦公時間
## 規則
- R3：只能訂 08:00–20:00 之間的時段。
## 情境
| 標籤 | 情境 | 狀態 |
|---|---|---|
| @sc_book_06 | Starting before office hours | 新增 |
{{情境：例「| @sc_book_01 | Booking a free s…」}}
## 紅燈
```text
$ PYTHONPATH=.:tests pytest -q tests --driver api
1 failed, 9 passed, 10 warnings in 0.14s
```
紅的原因正確：辦公時間的檢查還不存在。
## 假設與待決
- 待決：[NEEDS CLARIFICATION: 20:00 整點結束算在辦公時間內嗎？]
````

```
$ python scripts/template_check.py templates/bdd-report.md drafts/office-hours.md; echo "exit=$?"
drafts/office-hours.md:1: no heading matches "## 綠燈"; copy it in
drafts/office-hours.md:3: 1 line(s) in "規則" should start like "- **R1**" (shape "- **a9**"), e.g. "- R3：只能訂 08:00–20:00"
drafts/office-hours.md:8: unfilled placeholder {{情境：例「| @sc_book_01 | B…; replace it with real content
drafts/office-hours.md:16: open question [NEEDS CLARIFICATION: 20:00 整點…]; answer it, or pass --allow-open while drafting
1 files, 4 problems
exit=1
```

四種錯各抓到一次，每一則都說出位置和改法。加 `--allow-open`（草稿模式）時最後一則不算，剩 `1 files, 3 problems`；加 `--json` 時四則問題變成一行 `{"files": 1, "problems": [...]}`，給下一支程式接。模板定稿之後還有一個陷阱：再跑一次提取器，就會把人的決定蓋掉。所以提取器遇到既有的輸出檔會回 2，並給兩條路：`templates/bdd-report.md exists and may hold a person's answers; compare with --dry-run, or overwrite with --force`。照第一條路走：

```
$ python scripts/template_extract.py reports/*.md -o templates/bdd-report.md --dry-run 2>/dev/null | diff - templates/bdd-report.md
23a24
> <!-- 必填。沒有就寫「- 無：本次沒有新的假設。」；沒有答案的問題寫成 [NEEDS CLARIFICATION: 問題]，答覆前報告不算完成 -->
```

報告 2 補了段落之後，重新抽取不再發問，但會丟掉人寫的那一行。`--dry-run` 把模板印到 stdout、摘要印到 stderr，才能直接接 `diff`；這是 clig.dev 的兩條慣例「Send output to `stdout`」「Send messaging to `stderr`」。

## 底層機制：提取器怎麼分出「形狀」與「內容」

固定行是集合交集：某一段在每份範例裡都出現的整行，原樣保留；只有一份範例的段落沒有交集可算，全部當內容。聰明的部分在形狀。`head()` 取一行的列表或表格標記加第一個詞，遇到冒號就截斷：`- **R1** 同一間…` 取成 `- **R1**`，`- 假設：所有時間…` 取成 `- 假設：`。`shape()` 再把連續的字母（含中文）換成 `a`、連續的數字換成 `9`，於是 `@sc_book_01` 和 `@sc_cancel_03` 都變成 `@a_a_9`。這個手法借自序列標註的 word shape 特徵：Jurafsky 與 Martin 的 [Speech and Language Processing](https://web.stanford.edu/~jurafsky/slp3/) 第 18 章（2026-08-19 版草稿）在 18.5.1 節（詞性標註的特徵）把 `DC10-30` 映成 `XXdd-dd`，較短的 short word shape 再把連續的同類字元合併成 `Xd-d`，18.5.2 節再把兩者列為命名實體辨識（NER）的特徵。我們用短版而且不分大小寫，因為報告要守的是「先寫規則編號」，不是大小寫。一段裡所有變動行都同形，而且這些行來自至少兩份範例，才立一條規則。

兩個設計決定。第一，**不確定就問，問了就擋**。Spec Kit 的 `spec-driven.md` 要求模板「Don't guess: If the prompt doesn't specify something, mark it」，提取器面對互相矛盾的範例時遵守同一條；檢查器再拒絕使用還有問題的模板，把「人要先決定」做成結構。改成一句「記得先處理模板裡的問題」，就是 [pstack Ch 15](../pstack/15-delegation-meta-autonomy.md) 說的「The instruction is the symptom」。第二，**結束碼分三種**：0 沒事、1 有發現、2 跑不起來，和 macOS 的 `diff(1)` 一致（0 沒有差異、1 有差異、大於 1 是出錯）。agent 只看結束碼就能決定下一步：1 去修文件，2 去修指令或環境。

## 三、模板放在 skill 的哪裡，主幹怎麼叫它

模板放在 skill 目錄的 `templates/`，檢查器放 `scripts/`，主幹（spine，也就是 SKILL.md 本體）只在需要的那一步提到它們（`skills-after/booking-bdd/` 的第 8 步，前後對照見第四節末）：

```
8. Finish with a report: copy `${CLAUDE_SKILL_DIR}/templates/bdd-report.md` to `reports/<change>.md`, replace every `{{…}}`, and run `python3 ${CLAUDE_SKILL_DIR}/scripts/template_check.py ${CLAUDE_SKILL_DIR}/templates/bdd-report.md reports/<change>.md` until it prints `0 problems`. Read the template only at this step.
```

三個寫法各有來由。Claude Code 的 skills 文件要你「Reference supporting files from `SKILL.md` so Claude knows what each file contains and when to load it」，所以這一步同時說了檔案是什麼、何時讀；`${CLAUDE_SKILL_DIR}` 會展開成 skill 所在目錄，不受 agent 當下 `cd` 到哪裡影響（下面執行 3 收到的 body 裡，它已是 `/private/tmp/lab/…/.claude/skills/booking-bdd` 這樣的絕對路徑）；「until it prints `0 problems`」把完成條件交給檢查器，而不是 agent 的自我感覺（[Ch 14](./14-artifact-first.md) 會把這個做法推到底）。模板放在第三層，是 [Ch 4](./04-prompt-files-to-skills.md) 量過的漸進揭露：沒觸發這個 skill 的 session 一個字都不付，觸發了也要等 agent 去讀才付。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。專案是報告用的冒煙專案擴充版（R3 已實作），加上這份 skill 與兩份真實輸出 `runs/r3-red.txt`、`runs/r3-green.txt`。agent 當時讀到的兩支腳本是定稿前一版：檢查邏輯相同，差在訊息措辭、`--json`、`--dry-run`、`--force` 三個選項，以及當時只把 `OSError` 轉成結束碼 2。

```
$ cd agent-run-3 && claude -p "R3（只能訂 08:00–20:00）已經做完了：…照 booking-bdd skill 寫這次變更的報告 reports/office-hours.md。只寫這個檔案；不要改其他檔案，也不要重跑測試。" --output-format stream-json --verbose --max-turns 12 --allowedTools "Skill,Read,Write,Edit,Bash(python3 *)" --permission-prompts none --setting-sources project --strict-mcp-config --no-session-persistence > ../runs/agent-run-3.jsonl
```

用 jq 列出 15 個工具呼叫，順序是：`Skill booking-bdd` → `Read .claude/skills/booking-bdd/templates/bdd-report.md` → 讀 feature 檔與兩份輸出 → `cat` 檢查器原始碼 → 讀 `app.py`、測試與 `pytest.ini` → `grep` 列出情境標籤 → `grep -n -A14 "def shape"` → `Write reports/office-hours.md` → `python3 .claude/skills/booking-bdd/scripts/template_check.py …`，印出 `1 files, 0 problems`，第一次檢查就通過。同一個提示詞跑了三種變體，各一次，只能當個案：

| 執行 | skill | 檢查結果 | turns | 費用 | 時間 |
|---|---|---|---|---|---|
| 1 | 模板 + 檢查器（前一版草稿：段落、固定行與形狀規則相同；H1、空行與兩處提示文字不同） | 第一次就 0 problems | 18 | $0.317 | 56.0 s |
| 2 | 只有文字的 `skills-before`（第四節） | 沒有檢查器；事後用定稿模板檢查：6 problems | 14 | $0.239 | 45.7 s |
| 3 | 本章定稿 | 第一次就 0 problems | 17 | $0.268 | 42.6 s |

- **模板在 Skill 啟動後立刻被讀**，早於任何專案檔案。這次的任務就是第 8 步，所以這不算違反「Read the template only at this step」；agent 在前幾步會不會提早讀，三次執行都測不出來。沒觸發 skill 的 session 不載入模板，靠的是檔案分開，不是這句話。
- **agent 會讀你的檢查器**。它 `cat` 了 `template_check.py`、`grep` 了 `shape()`，交出的紅燈段多了一行「未依流程變紅：@sc_book_07 …」，開頭的「詞＋全形冒號」剛好滿足 `a：`。檢查器因此成了規格的一部分：沒要求的不保證，要求錯了照錯的做。（`--allowedTools` 只預先核准、不限制；`--permission-prompts none` 只拒絕會跳出詢問的呼叫，而 `ls`、`cat`、`grep` 在 Claude Code 內建的唯讀指令清單裡，本來就不詢問，所以照跑。）
- **模板沒寫的慣例會掉**。三份範例的標題都以「變更報告：」開頭，模板的 H1 卻是 `{{標題}}`，執行 3 交出的是 `# 只能在辦公時間內預約`。
- **內容上的洞，檢查器看不到**。三次執行都指出同一個缺口：沒有情境測「結束晚於 20:00 要拒絕」。我們把 `or b.end > time(20)` 從 `app.py` 刪掉再跑，結果 `10 passed in 0.12s`，測試確實擋不住。模板保證形狀，意思要靠讀的人或 [Ch 32](./32-brownfield-bdd.md) 的突變測試。執行 2 的報告反而在紅燈段多了一張逐情境的分析表，資訊更多，卻被只從三行歸納出來的 `a：` 規則判成問題之一。

## 四、腳本提取器：在 skill 的文字裡找機器判斷得了的步驟

靶子是 `skills-before/booking-bdd/SKILL.md`，我們照 [Ch 7](./07-messy-baseline.md) `bdd-monolith` 的形狀手寫的濃縮版：

```markdown
---
name: booking-bdd
description: Add or change room-booking behaviour test-first with pytest-bdd. Use when a change touches rules R1-R4, features/*.feature or tests/test_booking.py.
---

# Booking BDD

1. Read `features/` first and reuse existing step wording; add a new step only when no existing step fits.
2. Write scenarios declaratively: describe what the user sees, not which endpoint is called.
3. Every scenario has exactly one `When` and one tag like `@sc_book_03`.
4. After adding a tag, register it under `markers =` in `pytest.ini`, one line per tag, so pytest prints no PytestUnknownMarkWarning.
5. Run `PYTHONPATH=.:tests pytest -q tests --driver api` and check that each new scenario fails with an AssertionError, not an ImportError or a fixture error.
6. Implement the smallest change, then run the same command again until it reports no failures.
7. Keep step definitions thin; business rules belong in `app.py`.
8. Finish with `reports/<change>.md` containing `## 規則`, `## 情境`, `## 紅燈`, `## 綠燈` and `## 假設與待決`, with the real pytest output pasted into 紅燈 and 綠燈.

| You see | It means | Do |
|---|---|---|
| `StepDefinitionNotFoundError` | a step has no binding | write the step definition |
| `ImportError` or a fixture error | the harness is broken | fix it; this is not a valid red |
| `AssertionError` | the behaviour is missing | valid red, implement |
```

`scripts/script_candidates.py` 的核心是一張可以直接改的表 `KINDS`：每一類是「全部要命中的幾個正規表示式」，由上往下比，第一個命中的勝出。判斷詞（declaratively、thin、合理、清楚…）排在前面，因為「寫得清楚」這種句子就算帶著數字，也不該交給程式。

```python
#!/usr/bin/env python3
"""script_candidates.py: list the deterministic steps a skill still leaves to the agent's eyes.
lint: a literal format or number to check. run: a command whose output must be read. edit: a mechanical change
to a named config file. classify: a decision table keyed on literal output. template: an output shape.
Exit: 0 none, 1 candidates found, 2 error."""
import argparse, collections, re, sys

CHECK = (r"\b(check|verify|ensure|confirm|make sure|until|must|exactly|every|each|per|at least|at most)\b"
         r"|檢查|確認|核對|一律|必須|務必|一定|至少|最多|不超過|嚴禁|每[一個條章份行]")
QUANT = r"\d+\s*([–~-]\s*\d+\s*)?(行|字|條|lines?\b|chars?\b|characters)"
KINDS = [  # (kind, patterns that must all match); the first hit wins, so judgment is tested early
    ("scripted", [r"scripts/\S+\.(py|mjs|sh)"]),
    ("template", [r"(`#{1,6} [^`]+`.*){3}"]),
    ("judgment", [r"\b(declarative\w*|readable|reasonable|appropriate|meaningful|thin|clear|concise|prefer|"
                  r"consider)\b|合理|適當|清楚|易讀|有意義|具體|直覺|自然|視情況|判斷"]),
    ("run", [CHECK, r"`(\w+=\S+ )*(python3?|pytest|uvx?|pip|git|npm|node|make|curl|specify|claude)[\s`]"]),
    ("edit", [r"\b(register|add|append|update)\b|登記|加到|寫進", r"`[\w./-]+\.(ini|toml|cfg|json|ya?ml)`"]),
    ("lint", [CHECK, r"`[^`]+`|" + QUANT]), ("lint", [QUANT]),
]

def scan(path, rows, stats):
    fence, table = None, None
    add = lambda n, k, why, text: rows.append(f"{path}:{n}: {k:<8} [{why}] {text}")
    for n, line in enumerate(open(path, encoding="utf-8").read().splitlines() + [""], 1):
        s = line.strip()
        if s.startswith("|"):                             # a table: [first line, rows, rows keyed on a literal]
            table = table or [n, -1, 0]
            table[1] += not re.fullmatch(r"\|[\s:|-]+\|", s)
            if re.match(r"\|\s*`", s): table[2] += 1; continue
        elif table:
            if table[2]: add(table[0], "classify", "table", f"{table[1]} rows, {table[2]} keyed on literal output")
            table = None
        if m := re.match(r"(```|~~~)\s*(\w*)", s):
            if fence and fence[1] in ("markdown", "md", "gherkin") and n - fence[0] > 5:
                add(fence[0], "template", "fenced", f"{fence[1]} block, {n - fence[0] - 1} lines")
            fence = None if fence else (n, m[2])
            continue
        for k, pats in KINDS:
            if all(hits := [re.search(p, line, re.I) for p in pats]):
                stats[k] += 1
                if k not in ("scripted", "judgment"):
                    add(n, k, "3+ headings" if k == "template" else hits[0][0], s[:50])
                break

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="+", help="SKILL.md (or any markdown) files to scan")
    a = ap.parse_args()
    rows, stats = [], collections.Counter()
    for path in a.skills: scan(path, rows, stats)
    kinds = ", ".join(f"{k} {v}" for k, v in collections.Counter(r.split()[1] for r in rows).most_common())
    print(*rows, f"{len(a.skills)} files, {len(rows)} candidates ({kinds}), {stats['judgment']} judgment lines, "
          f"{stats['scripted']} already scripted", sep="\n")
    return 1 if rows else 0

if __name__ == "__main__":
    try: sys.exit(main())                             # a crash exits 2: it must not look like "candidates found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

```
$ python scripts/script_candidates.py skills-before/booking-bdd/SKILL.md; echo "exit=$?"
skills-before/booking-bdd/SKILL.md:10: lint     [Every] 3. Every scenario has exactly one `When` and one t
skills-before/booking-bdd/SKILL.md:11: edit     [register] 4. After adding a tag, register it under `markers 
skills-before/booking-bdd/SKILL.md:12: run      [check] 5. Run `PYTHONPATH=.:tests pytest -q tests --drive
skills-before/booking-bdd/SKILL.md:15: template [3+ headings] 8. Finish with `reports/<change>.md` containing `#
skills-before/booking-bdd/SKILL.md:17: classify [table] 3 rows, 3 keyed on literal output
1 files, 5 candidates (lint 1, edit 1, run 1, template 1, classify 1), 2 judgment lines, 0 already scripted
exit=1
```

五類各一個，每一個都有下家：第 8 步的 template 交給前兩節的工具；第 4 步的 edit 是本章動手練習 1；第 3 步的 lint 是 [Ch 28](./28-gherkin-dsl-style.md) `gherkin_lint.py` 的工作；第 5 步的 run 加上第 17 行的紅燈判讀表，在 [Ch 11](./11-skill-engineering-skill.md) 變成 `red_check.py`，完整版是 [Ch 27](./27-ai-bdd-loop.md) 的 `bdd_check.py red`（TDD 那一側的同類工具是 [Ch 25](./25-tdd-in-sdd-pipeline.md) 的 `red_gate.py`）。第 2、7 步被判成需要判斷，留給 rule file。第 1、6 步沒被標：它們確實是固定步驟，但句子裡沒有字面量可抓，是啟發式的盲點。

**真實世界的考驗：這個 repo 的 learn skill。**pstack 期末專案已經用人工從它抽出 `check-chapter.mjs` 與 `check-course.mjs`，正好當標準答案。在 repo 根目錄跑：

```
$ python /tmp/lab/aibdd/ch10-templates-scripts/scripts/script_candidates.py .claude/skills/learn/SKILL.md
…（略）
.claude/skills/learn/SKILL.md:65: lint     [300–450 行] - **普通章** 300–450 行
…（略）
.claude/skills/learn/SKILL.md:355: template [fenced] markdown block, 14 lines
…（略）
1 files, 24 candidates (lint 20, template 4), 43 judgment lines, 0 already scripted
```

| 兩支人工檢查器裡的規則 | learn 的出處 | 掃描器 |
|---|---|---|
| 標題格式 | 第 355 行起的章節範本 | 標成 template |
| `> **目標**：`；行數範圍；`<details>`；禁用字 | 第 704、720–723、759、735 行 | lint |
| 自我檢核、其中的 `- [ ]`、延伸閱讀、結尾 `→ [下一章]` | 第 708 行（一行寫了四條） | lint |
| 練習要有測試用例或卡關提示 | 第 482 行起的練習範本 | **半截**：標成 template，但巢狀 fence 讓它以為範本在第 500 行結束，這兩段都在那之後 |
| 踩雷至少 3 條；要有進階段落 | 第 659、660 行 | **漏**：「錯誤直覺」的「直覺」命中判斷詞；另一句沒有字面量或數字 |
| 根目錄 README 要登錄 | 第 126–140 行（Step 6a） | **漏**：沒有檢查詞 |

依我們逐條對照，兩支檢查器裡有 13 條規則在 learn 找得到出處（check-course 另外三條 README 與下一章順序的檢查，learn 只在範本與「改章節結構」的提醒裡提到，沒算進來）：掃描器標到 9 條，1 條半截，3 條漏掉。反方向，24 個候選我們逐條讀過，23 個是真的規則（第 720–723 行與第 65–68 行重複，見下），只有第 726 行（「如果一章寫完只有 150 行」）是把行數規則換句話說，沒有新內容。兩支檢查器裡的「連結要能解析」「fence 要閉合」在 learn 沒有任何一句話，是 pstack 自己加的：提取器找不到沒寫下來的規則。順帶兩個觀察：同一條行數規則在第 65–68 行與第 720–723 行各寫一次，正是 pstack Ch 15 說的「同一個指示寫第二次」；章節範本只解析到 14 行，因為它用 ` ```markdown ` 包住，裡面又有一行裸的 ` ``` `，而 CommonMark 規定不帶 info string、長度「at least as long as the opening fence」的 fence 就會把它關上（[Ch 7](./07-messy-baseline.md) 的 `skill_metrics.py` 也因此報出 fence 沒閉合）。範本 inline 在 SKILL.md 裡連解析都會壞，又多一個搬去 `templates/` 的理由（本章顯示範本時改用四個反引號的外層 fence，就是這個原因）。

**把文字換成一行呼叫。**候選變成腳本之後，SKILL.md 裡那句話就換成呼叫，再掃一次確認：

```
$ diff skills-before/booking-bdd/SKILL.md skills-after/booking-bdd/SKILL.md
11c11
< 4. After adding a tag, register it under `markers =` in `pytest.ini`, one line per tag, so pytest prints no PytestUnknownMarkWarning.
---
> 4. After adding a tag, run `python3 ${CLAUDE_SKILL_DIR}/scripts/sync_markers.py --write` from the project root.
…（略：第 8 步換成第三節那一行）
$ python scripts/script_candidates.py skills-after/booking-bdd/SKILL.md | tail -1
1 files, 3 candidates (lint 1, run 1, classify 1), 2 judgment lines, 2 already scripted
$ node /tmp/lab/tools/skill-lint.mjs skills-before skills-after
2 skills, 0 problems
```

`sync_markers.py` 是動手練習 1 要你寫的那支（我們的參考實作只留在作者實驗目錄的 `extras/`，本章不收錄；第三節的 agent 執行時它已經在 skill 裡）。五個候選剩三個，兩個變成「already scripted」，pstack Ch 28 的 skill-lint 兩版都過。剩下的三個留給 Ch 25 與 Ch 28：提取器把工作排好了，不是把工作做完了。

[pstack Ch 21](../pstack/21-verification-skills.md) 在「二、實作：為一個 notes API 寫驗證技能」列過 agent 用得好的 control CLI 有哪些特徵；本章的三支各示範了幾項：

| 介面 | 本章哪裡做了 | 為什麼 agent 需要 |
|---|---|---|
| `--help` 說清楚用途與結束碼 | 三支都把 docstring 直接當說明 | 不讀原始碼也知道怎麼呼叫、每個結束碼代表什麼 |
| 預設不破壞；`-n, --dry-run` 預覽；`--force` 才覆寫 | `template_extract.py` | clig.dev 的旗標表：「Dry run. Do not run the command, but describe the changes」 |
| `--json` | `template_check.py` | 下一步用程式接，不必解析文字 |
| 0／1／2，crash 也歸 2 | 三支都有 | 靠結束碼分辨「去修文件」與「去修指令」 |
| 錯誤說出位置與改法 | `drafts/office-hours.md:3: … should start like "- **R1**"` | Anthropic 建議錯誤回應給「specific and actionable improvements」，不給 traceback |

## 對比與取捨

| 重複的東西放在哪裡 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| SKILL.md 裡的一句話 | 零成本 | 靠 agent 記得、用眼睛核對；執行 2 交出的形狀就和範例不同 | 需要判斷的事（Ch 9 的 rule file；[pstack Ch 30](../pstack/30-self-improvement-loop.md)「修正的層級：由高到低」把文字規則排在最後一層） |
| 範本 inline 在 SKILL.md | 一個檔案看完 | 每次觸發都載入；巢狀 fence 讓範本解析錯 | 很短、每次都用的格式 |
| `templates/` 加檢查器 | 按需載入，結果可檢查 | 要維護；嚴格度要逐段決定；agent 會照檢查器寫 | 一再交付的產物：報告、計畫、PR 描述 |
| `scripts/` 腳本，或由 hook 呼叫 | 確定、可重跑，只有輸出進 context；hook 版不靠 agent 記得 | 要測、要維護；只做得了機器判斷得了的部分 | 每次都一樣的動作與核對（[pstack Ch 28](../pstack/28-skill-authoring.md)「四、什麼時候拆檔：references 與 scripts」的標準；hook 見 [Ch 3](./03-propose-constrain-feedback.md)） |

## 踩雷集錦

1. **錯誤直覺：「兩份範例就夠歸納模板。」** 正確認識：兩份範例裡的巧合會變成規則。只拿 R1、R2 兩份去抽，再檢查第三份，得到 `reports/cancel-own-booking.md:10: "紅燈" lacks the fixed line "紅的原因正確：規則還沒實作，API 照單全收。"`；只給一份（或同一個檔給兩次）時提取器直接拒絕（`need 2+ examples …`，結束碼 2）。至少三份，每次改模板都跑來回測試。
2. **錯誤直覺：「範例都有的慣例，agent 自然會守。」** 正確認識：模板沒編進去的，agent 不保證。我們第一版用逐字元的共同前綴保留「變更報告：」，結果連 R1、R2、R4 共有的「R」也留下，產生 `# 變更報告：R{{標題}}`；定稿版把整個標題當內容，執行 3 交出的標題就沒有「變更報告：」。要守的慣例，明確寫進模板。
3. **錯誤直覺：「腳本壞了會印 traceback，夠清楚了。」** 正確認識：Python 未處理的例外以結束碼 1 收場，而 1 在我們的約定是「有發現」。第一版 `template_extract.py` 遇到輸出目錄不存在，印的就是 `FileNotFoundError` 加結束碼 1，只看結束碼的 agent 會以為模板有問題。能修的自己修（建目錄），修不了的轉成 2 和一行說明。只接 `OSError` 還不夠：`iconv -f UTF-8 -t BIG5 drafts/office-hours.md > drafts/big5.md` 做一份 Big5 草稿交給 `template_check.py`，只接 `OSError` 的前一版印出 `UnicodeDecodeError` 的 traceback、結束碼 1；定稿版接住任何例外，印 `error: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc5 in position 2: invalid continuation byte`、結束碼 2。
4. **錯誤直覺：「掃描器說沒有，就是沒有。」** 正確認識：啟發式有盲點，也有 bug。第一版的指令偵測把 `` `pytest.ini` `` 當成 `pytest` 指令，把第 4 步錯分成 run；「錯誤直覺」裡的「直覺」讓 learn 的「踩雷至少 3 條」被當成判斷題略過。提取器自己也要用「該抓到」與「不該抓到」的例子各跑一次，這和 [pstack Ch 28](../pstack/28-skill-authoring.md) skill-lint 的教訓相同。
5. **錯誤直覺：「模板越嚴越好。」** 正確認識：執行 2 那張資訊更多的分析表，被 `a：` 這條只從三行歸納的形狀規則擋下。每條形狀規則都要有人審：真正的慣例（規則編號、情境標籤）留下，碰巧一致的拿掉，或把那一段改成彈性。

## 進階：再往深一層

- **從 transcript 找步驟**：文字只寫了 agent「該做」的事，transcript 記下它「每次都在做」的事。作者實驗目錄裡的 `extras/script_candidates_transcripts.py` 多一個 `--transcripts` 模式（本章不收錄它的程式碼，你的實驗目錄裡沒有它），拿本課 Ch 3 與 Ch 7 在 2026-11-02 跑的 5 份 `claude -p` 紀錄去數：`pytest -q` 在 3 個 session 共跑 9 次，輸出被 `tail -30`、`tail -25`、`tail -6`、`tail -4`、`tail -1` 或 `grep … | head -20` 裁成不同長度，只有一次沒裁；`python --version` 出現在 3 個 session 的前三個指令裡，是在摸索環境（[Ch 0](./00-environment-setup.md) 的 `check_env.py` 把同一件事做成一個指令）。Claude Code 存在 `~/.claude/projects/<目錄>/` 的 session 檔，assistant 訊息的結構與 stream-json 相同，我們用本機一份確認過能解析。
- **模板的維護與它看不到的東西**：把範例放進 skill 的 `examples/`，CI 每次跑來回測試；範例更新時用 `--dry-run | diff` 看模板會怎麼變，再決定接不接受（[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) 的「自訂 template：把公司規範寫進規格約束」是手工改模板的版本）。`template_check.py` 只看一份文件，報告的情境表漏了 `@sc_book_07` 它看不出來；這類對帳交給 [Ch 16](./16-tasks-binder.md) 的 `task_coverage.py` 與 [Ch 31](./31-bdd-in-sdd.md) 的 `scenario_task_map.py`。只有一份黃金產物的情況（[Ch 14](./14-artifact-first.md)）需要「只留標題與形狀」的模式，而且要接受學不到固定行。
- **Spec Kit 的 16 項 checklist**：掃描器把 `speckit-specify` 整份 checklist 標成一個 template 候選（第 150 行），沒有逐條拆。把其中兩項交給 `template_check.py`、其餘留給模型自評，是 [Ch 17](./17-constitution-and-presets.md) 改寫 `/speckit-specify` 時的好起點。

## 動手練習

1. **把第 4 步做成 `sync_markers.py`**：讀 `features/**/*.feature` 的 tag，對照 `pytest.ini` 的 `markers =`。預設只檢查（1 = 有沒登記的 tag）；`--write` 補上、`-n, --dry-run` 只印要補的行、`--json` 印一行 JSON；沒有 `pytest.ini` 時回 2。動手前先重現我們的實測：在 [Ch 0](./00-environment-setup.md) 冒煙專案的副本加兩個 tag，`PYTHONPATH=.:tests pytest -q tests --driver api` 是 `2 passed, 2 warnings in 0.08s`，加 `--strict-markers` 變成收集錯誤，而且只報第一個：``ERROR tests/test_booking.py - Failed: 'sc_book_01' not found in `markers` con...``。兩個邊界也要處理：`@_wip` 會讓 pytest-bdd 9.0.1 在收集時丟出 `AttributeError: Marker name must NOT start with underscore`，整個測試模組都不跑，你的腳本要指出行號與改名建議；專案用 `pyproject.toml` 設定時不可自建 `pytest.ini`，因為 pytest 文件寫明 `pytest.ini` 「take precedence over other files … even when empty」。
2. **先預測再跑**：把 `drafts/office-hours.md` 照模板補成第四份報告（規則改成 `- **R3** …`、placeholder 換成 `@sc_book_07` 那一列、回答那個問題、補上綠燈），但在紅燈段放一張執行 2 那樣的逐情境表，和三份報告一起放進 `scratch/four/` 再抽一次。先寫下你預期哪條形狀規則會消失，再跑 `--dry-run | diff` 對答案。再想一想：換成執行 3 那份讀過檢查器才寫的報告，為什麼一條都不會少？
3. **從 learn 挑一個候選做成檢查，再修掃描器一個盲點**：例如第 115 行「常見卡點提示 3–5 條」，做出 `--help`、`--json` 與 0/1/2，用一份只有 2 條提示的練習檔證明它會失敗。接著讓 `script_candidates.py` 標到第 659 行「踩雷至少 3 條」，又不讓 `declaratively` 那一步被標；改完把本章的掃描全部重跑，確認其他結果沒變。

## 本章重點整理

- 重複有兩種：形狀交給模板，步驟交給腳本；兩者都可以從既有的產物與文字提取，再由人決定。
- `template_extract.py` 用集合交集找固定行、用 word shape 找形狀規則，範例矛盾時留 `[NEEDS CLARIFICATION]`；`template_check.py` 拒絕還有問題的模板；每次改模板都用來回測試確認它接受自己的來源。
- 模板放在 skill 的第三層，主幹寫清楚是什麼、何時讀、完成條件是哪支檢查器；實測中 agent 會讀檢查器的原始碼，交出的報告剛好通過它，所以檢查器要編進你真正要的東西。
- `script_candidates.py` 用一張可改的表把句子分成 lint、run、edit、classify、template 與判斷題；在 learn 上，人工檢查器 13 條有出處的規則它標到 9 條，還順帶標出寫了兩次的行數規則和被巢狀 fence 切斷的範本。給 agent 的腳本要有 `--help`、預設不破壞、`--dry-run`、`--json`、0/1/2 與指出改法的錯誤訊息，讓它不必猜。

## 自我檢核

- [ ] 不看程式，說出 `template_extract.py` 怎麼決定一行是固定行、形狀規則還是 placeholder，以及為什麼只給一份範例時它拒絕工作。
- [ ] `template_check.py` 遇到還有 `[NEEDS CLARIFICATION]` 的模板，為什麼回 2，而不是照樣檢查、印一個警告？
- [ ] 執行 3 的 agent 讀了檢查器的原始碼。這對你寫檢查器的方式有什麼影響？舉一條你會因此加進模板的規則。
- [ ] 在你自己的一份 skill 裡找出一句 edit 類、一句 run 類的步驟，說出它們失敗時各有多安靜。
- [ ] 提取器為什麼拒絕覆寫既有的模板？`--dry-run` 為什麼把摘要印到 stderr？

## 延伸閱讀

- **[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)** — Anthropic（Claude Developer Platform 文件）
  - **讀哪裡**：「Set appropriate degrees of freedom」「Template pattern」「Provide utility scripts」「Create verifiable intermediate outputs」。
  - **學到什麼**：低自由度的步驟交給腳本（「Prefer scripts for deterministic operations」）、模板分嚴格與彈性、驗證腳本的錯誤訊息要具體到能直接拿來修；也提醒你寫清楚腳本是拿來「跑」還是拿來「讀」。
  - **和本章的關聯**：第三節的主幹寫法與第四節末的介面表都以它為據。同一主題在 Claude Code 這邊的細節（supporting files、`${CLAUDE_SKILL_DIR}`）見 [Extend Claude with skills](https://code.claude.com/docs/en/skills) 的「Add supporting files」與「Available string substitutions」。
- **[Spec-Driven Development（spec-driven.md，v1.2.5）](https://github.com/github/spec-kit/blob/v1.2.5/spec-driven.md)** — GitHub Spec Kit
  - **讀哪裡**：「Template-Driven Quality: How Structure Constrains LLMs for Better Outcomes」的第 2、3 點。
  - **學到什麼**：`[NEEDS CLARIFICATION]` 的原始動機（「Don't guess」），以及把 checklist 當成 spec 的「unit tests」、由模型自評的設計。
  - **和本章的關聯**：本章把它的標記用在提取器上，並把 checklist 裡機器判斷得了的部分改成真的程式。
- **[Command Line Interface Guidelines](https://clig.dev/)** — Aanand Prasad、Ben Firshman、Carl Tashian、Eva Parish
  - **讀哪裡**：「The Basics」「Output」「Errors」「Arguments and flags」（標準旗標表裡的 `-n, --dry-run`）。
  - **學到什麼**：結束碼、`--json`、stdout 與 stderr 的分工、把錯誤改寫成建議；寫給人的好 CLI，大多也是寫給 agent 的好 CLI。
  - **和本章的關聯**：第四節末介面表的出處；搭配 Anthropic 工程部落格的 [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)（2025-09-11）讀，後者從 agent 的角度談錯誤回應與輸出的 token 成本。

模板管住了產物的形狀，腳本接手了機器判斷得了的步驟；下一章把它們和 Ch 9 的 rule file 組成一個會改造 skill 的 skill，拿 Ch 7 的單檔 BDD skill 開刀。

→ [Ch 11 skill-engineering：用 skill 改造 skill](./11-skill-engineering-skill.md)
