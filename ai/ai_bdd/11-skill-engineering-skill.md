# Ch 11 — skill-engineering：用 skill 改造 skill

> **目標**：把 Ch 7、9、10 的量測與提取工具組成一個會改造 skill 的 skill：29 行的脊椎、SK-01..SK-08 的規則檔、一份報告模板，加上一支當完成條件的 `module_check.py`。用它讓 agent 把 Ch 7 的 222 行 `bdd-monolith` 拆成模組化的 `bdd`、由人審過，再用一次小型盲測比較改造前後 agent 怎麼做事；最後讓它檢查它自己，並量這個 repo 的 learn skill、提出拆法（不改檔）。

> **環境**：Python 3.12.15 與課程 venv（PyYAML 6.0.3、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、Pydantic 2.14.2）、Claude Code 2.1.295（`claude -p` 預設模型 claude-opus-5-5，2026-11-02 共跑 3 次）、Node v26.11.0（只跑 pstack 的 skill-lint 與 blind-check）、jq 1.7.1、macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch11-skill-eng/` 從空目錄開始；五支腳本從前三章的實驗目錄原樣複製，只有 `module_check.py` 是新寫的。

## 為什麼需要這個？

[Ch 7](./07-messy-baseline.md) 用 `skill_metrics.py` 量出 `bdd-monolith` 的毛病，[Ch 9](./09-rule-files.md) 用 `rule_extract.py` 把規則挖成帶例子的 rule file，[Ch 10](./10-templates-and-scripts.md) 用 `template_extract.py`、`template_check.py`、`script_candidates.py` 把形狀和步驟抽成模板與腳本。三章的每一步都是人拿工具、對著一份 skill 手做；改下一個 skill，又要照同樣的順序再做一遍。Ch 10 說「每次都一樣的步驟」是腳本的候選；這一串步驟每次都一樣，中間卻夾著要判斷的部分（哪段原文該去哪裡），所以該寫成 skill：交給 agent 做，由腳本驗收。

外部課綱把這個單元叫「實作 /skill-engineering skill 來取代 /skill-creator」。[Ch 5](./05-skill-creator.md) 的 skill-creator 強在行為：訪談、跑有 skill 與沒 skill 的測試、看結果、調 description 的觸發率。它不管結構：Ch 5 經過一輪 eval 的 `gherkin-scenario-writer` 仍是 93 行、10 條沒例子的規則、0 個能用指令判斷的完成條件；它的改寫原則「Explain the why」也和 [pstack Ch 28](../pstack/28-skill-authoring.md)「一、Authoring playbook」的「Tell it to do the thing and skip the reason」相反。Anthropic 的 skill 寫作指南甚至說，要 Claude 幫你寫 skill，不需要「a "writing skills" skill」。就格式而言這句話對；模型不知道的是團隊自己的東西：規則用什麼 ID、例子長什麼樣、報告照哪份模板、已經有哪些模組、怎樣才算做完。本章的 skill 裝的就是這些。

零件在 pstack 都出現過：Ch 28「五、驗證：把 playbook 的 step 2 寫成 lint」的 skill-lint、[Ch 29](../pstack/29-eval-playbook.md)「三、把盲測規則寫成檢查器」、[Ch 30](../pstack/30-self-improvement-loop.md)「一、/reflect：一次 session 的教訓」（提議改 skill，等人核准）。缺的是把它們串成一個會動手的 skill，再拿它改它自己。

## 先建立直覺：重構，不是重寫

Martin Fowler 對重構（refactoring）的定義是「a change made to the internal structure of software to make it easier to understand and cheaper to modify without changing its observable behavior」。改造 skill 是同一件事，只是對象換成給 agent 讀的文字，程式重構的每樣配備都有對應：code smell 是太長、規則沒例子、完成條件不是指令（`skill_metrics.py`、`script_candidates.py` 量得出來）；extract method 是抽出規則、模板、腳本（Ch 9、Ch 10 的提取器）；「編譯通過」是脊椎、接線、規則欄位都對（本章的 `module_check.py`）；「測試證明行為沒變」是盲測（pstack Ch 29）；code review 是人讀報告、回答待決。

```
 草稿 SKILL.md（skill-creator 的產物，或任何單檔 skill）
   │ 1 量     skill_metrics.py、script_candidates.py               腳本
   │ 2 收集   rule_extract.py：團隊的規則檔已經涵蓋哪些             腳本
   │ 3 對應   每段原文 → 去處（脊椎／規則 ID／模板／腳本／刪）        agent，依 SK-01..SK-08
   │ 4 組裝   脊椎 + rules/ + templates/ + scripts/                  agent
   │ 5 檢查   module_check.py 印出 0 problems 為止                   腳本
   │ 6 報告   去處表 + 待決；template_check.py 驗形狀                agent 寫、腳本驗
   ▼
 out/<name>/ ── 人：回答待決、決定收哪些、搬進 .claude/ ──▶ 盲測：行為有沒有變
```

這個比喻有一處不成立：程式的「可觀察行為」由測試定義，跑一次就有答案；skill 的可觀察行為是 agent 的行為，每次不同，一次執行只是一個樣本。所以結構交給腳本判，行為只能抽樣看，第五節會碰到這件事的代價。

## 一、skill-engineering 長什麼樣

```bash
mkdir -p /tmp/lab/aibdd/ch11-skill-eng && cd /tmp/lab/aibdd/ch11-skill-eng
mkdir -p skills/skill-engineering/{rules,templates,scripts} inputs runs
cp /tmp/lab/aibdd/ch07-messy/tools/skill_metrics.py /tmp/lab/aibdd/ch10-templates-scripts/scripts/{script_candidates,template_extract,template_check}.py /tmp/lab/aibdd/ch09-rules/rule_extract.py skills/skill-engineering/scripts/
cp -R /tmp/lab/aibdd/ch07-messy/skills/bdd-monolith inputs/
```

複製來的五支應和 Ch 7、9、10 印出的程式碼逐字相同（我們用那三章的 code block 重建這五支，重跑本章每一個不靠 agent 的輸出，結果不變）。`inputs/bdd-monolith` 則是你在 Ch 7 自己跑出的草稿：本章的行號與數字來自 Ch 7 記錄的那一份（Ch 5 的 `gherkin-scenario-writer` 也一樣），你的會不同。skill 由四部分組成：29 行的 `SKILL.md`、`rules/skill-writing.md`（第二節）、`templates/refactor-report.md`（第二節）、`scripts/` 裡的六支腳本（第三節新寫 `module_check.py`）。`SKILL.md` 全文，最後的 Files 清單就是整個目錄：

```markdown
---
name: skill-engineering
description: Refactor an existing agent skill - a skill-creator draft or any long single-file SKILL.md - into a short spine plus rules/, templates/ and scripts/, using measuring and extracting scripts, and prove the split with a check. Use when asked to split, modularize, slim down, restructure or audit a skill, or to turn a skill's prose into rule files, templates or scripts. Not for interviewing someone about a brand-new skill or for tuning how often a description triggers; skill-creator does those.
---

# Skill engineering

You turn one skill directory `<in>` into `<out>/<name>/` plus the report `<out>/<name>.report.md`.
Never edit `<in>` and never write under `.claude/`; a person reviews `<out>` and moves it.
Run every script from the project root as a command of its own: `python3 ${CLAUDE_SKILL_DIR}/scripts/<script> ...`.

1. Measure: run `python3 ${CLAUDE_SKILL_DIR}/scripts/skill_metrics.py <in>` and `python3 ${CLAUDE_SKILL_DIR}/scripts/script_candidates.py <in>/SKILL.md`. Copy `templates/refactor-report.md` to the report path and paste both outputs under 改造前.
2. Collect what exists: list the project's rule files, templates and scripts. If the project has a `signatures.yaml`, run `python3 ${CLAUDE_SKILL_DIR}/scripts/rule_extract.py --signatures <it> --rules <rule file> --doc <in>/SKILL.md` to see which draft rules a rule file already covers.
3. Read `rules/skill-writing.md`, then map every line range of `<in>/SKILL.md` to one 去處 row: the spine, a rule ID, a template, a script, `references/`, or dropped with the reason. Cite an SK ID in each row.
4. Build `<out>/<name>/`. The spine keeps the numbered steps, one done line that names a command, and a Files list with one line per bundled file: what it is and the step that reads it. Rules go to `rules/<topic>.md` in the format of `rules/skill-writing.md`. Copy reused files unchanged and keep their IDs.
5. Check: run `python3 ${CLAUDE_SKILL_DIR}/scripts/module_check.py <out>/<name>` and fix every line it prints. Then run `script_candidates.py` on the new SKILL.md; for each candidate you leave as prose, add a 待決 line that says why.
6. Report: paste the outputs of step 5 under 改造後. Every fact the draft states that nobody gave you, every conflict with an existing module and every rule you dropped goes under 待決 as `- 待決：[NEEDS CLARIFICATION: <question>]`.

Done when `python3 ${CLAUDE_SKILL_DIR}/scripts/module_check.py <out>/<name>` prints `0 problems` and `python3 ${CLAUDE_SKILL_DIR}/scripts/template_check.py ${CLAUDE_SKILL_DIR}/templates/refactor-report.md <out>/<name>.report.md --allow-open` prints `0 problems`. Then stop: a person answers 待決 and decides whether the change needs a blind comparison.

## Files
- `rules/skill-writing.md`: SK-01..SK-08, where each kind of sentence goes. Read at step 3.
- `templates/refactor-report.md`: the report. Copied at step 1, checked at done.
- `scripts/skill_metrics.py`: structure counts of a skill (step 1).
- `scripts/script_candidates.py`: deterministic steps still written as prose (steps 1 and 5).
- `scripts/rule_extract.py`: which draft rules an existing rule file covers (step 2; needs PyYAML).
- `scripts/module_check.py`: the done check (step 5); imports skill_metrics.py and template_check.py.
- `scripts/template_check.py`: checks a document against a template (done); imports template_extract.py.
- `scripts/template_extract.py`: derives a template from 2+ examples of one output (step 4, when the draft has them).
```

四個寫法決定：

- **不改輸入、不寫 `.claude/`**：Ch 7 已經看到 `claude -p` 寫不進受保護路徑；更根本的理由是 skill 會被之後每個 session 載入，要人看過才搬（[Ch 6](./06-workflows-are-grown.md) 的 `.claude/` review 閘門）。
- **`python3` 要指向課程 venv**：`rule_extract.py` 與 `module_check.py` 需要 PyYAML。Ch 7 留下的 transcript 是證據：啟動前沒把 venv 放進 `PATH` 的那次，agent 的 `python3 --version` 印出 3.9.6；放了的那次，`python --version` 是 3.12.15。第四節的啟動腳本因此先 `export PATH`，本章三次執行的 `python3` 也都跑得動需要 PyYAML 的腳本。
- **一次一個指令**：權限檢查會把 `&&`、`;`、`|` 拆開逐段比對，每一段都要被允許（Claude Code permissions 文件「Compound commands」）。第四節的四次拒絕裡，兩次是組合指令中有一段過不了。
- **done 行點名指令與它印的字**：這是 SK-05，也是 Ch 7 的 `checkable_done` 判得出來的寫法；它同時寫給 agent 和檢查器看。

## 二、規則檔與報告模板

規則檔沿用 Ch 9 的欄位（ID、Trigger、Rule、只在反直覺時寫的 Why、Sign you skipped it、Enforced by、Good／Bad／Edge），前綴 `SK` 是本課留給 skill 寫作的。八條各有出處：

| ID | 規則 | Enforced by | 出處 |
|---|---|---|---|
| SK-01 | 脊椎只放步驟、done 行、Files 清單；只有一步會讀的內容，搬去那一步的模組 | module_check.py | Claude Code 文件「Skill content lifecycle」、[Ch 4](./04-prompt-files-to-skills.md) |
| SK-02 | 規則搬進 `rules/`，有 ID 與 Good、Bad、Edge 例子 | module_check.py | Ch 9 |
| SK-03 | 程式判斷得了的步驟，換成一行腳本呼叫 | script_candidates.py（人逐條收） | Ch 10 |
| SK-04 | 每次都要交的產物做成模板，在寫它的那一步點名檢查指令 | module_check.py、template_check.py | Ch 10 |
| SK-05 | done 是一個指令與它印出的字 | module_check.py | Ch 7 |
| SK-06 | 重用專案已有的模組；ID 照舊，永不重編 | module_check.py（同一 skill 內的重複 ID）、review | Ch 9 |
| SK-07 | 沒人給過的事實是問題，不是規則 | review（報告的待決） | Ch 7 的時區發明 |
| SK-08 | 理由只留在少了它規則看起來像錯的地方 | review | pstack Ch 28 |

每條都是同一個格式。這是最需要判斷的一條，Edge 是第五節之後改寫的版本：

```text
## SK-07 A fact nobody gave you is a question, not a rule
Trigger: the draft states a domain fact, number or constraint that the request, the code and the project files do not.
Rule: leave it out of the skill and ask about it under 待決.
Why: a drafting model fills gaps with plausible facts, and once a fact is a rule every later run builds on it.
Sign you skipped it: a constraint in the skill that no file, ticket or message mentions.
Enforced by: review (the report's 待決 section)
Good: `- 待決：[NEEDS CLARIFICATION: 會議室真的在不同時區嗎？草稿要求 AwareDatetime，需求沒提。]`
Bad: `The rooms are in different timezones, so require offsets at the API boundary.`
Edge (a claim about the code is tested before it is dropped with its premise): "mixed datetimes crash" is true for app.py (`10:30+08:00` after a naive booking gives a 500); only "the rooms are in different timezones" was invented.
```

其餘七條照上表的內容與同一格式自己寫：檔頭兩行（在第 3 步讀、每個去處列引用一個 SK 編號、ID 不重用），每條至少有 Trigger、Rule、Sign you skipped it、Enforced by、Good、Bad 六個欄位，因為 `module_check.py` 會逐條查。SK-08 在 skill-creator 的「Explain the why」與 pstack 的「skip the reason」之間選了 pstack，但保留 pstack Ch 28 自己的例外：少了理由、agent 會把規則「修正」掉的地方，理由留著。我們的 Good 是 `Treat the feature file as fixed while you implement; change it only on purpose, and say so.`，Bad 是草稿第 8 行那段「because each one protects the next」的動機段落，Edge 是 `A scenario that passes before you implement is a failure: it can never catch a regression.`。

報告模板用 Ch 10 的格式手寫（只有一份範例時，`template_extract.py` 會拒絕歸納，那是 Ch 10 的規矩），所以 `template_check.py` 讀得懂它：

````markdown
<!-- refactor-report.md: written by hand in template_extract.py's format, so template_check.py can read it -->
# 改造報告：{{技能名稱}}
## 改造前
```text
{{改造前：skill_metrics.py 與 script_candidates.py 的完整輸出}}
```
## 去處
<!-- 每段原文一列，行號一律寫成範圍（第 7 行寫 7-7）；依據寫 SK 編號 -->
<!-- shape: "| 9-9" e.g. "| 12-19" -->
| 原文行 | 去處 | 依據 | 說明 |
|---|---|---|---|
{{去處：例「| 12-19 | rules/steps.md ST-2 | SK-02 | 原本沒有例子 |」}}
## 改造後
```text
{{改造後：module_check.py 與 script_candidates.py 的完整輸出}}
```
## 待決
<!-- 沒有就寫「- 無：沒有待決事項。」；有就寫「- 待決：[NEEDS CLARIFICATION: 問題]」，人回答並刪掉標記之前不算定稿 -->
<!-- shape: "- a：" e.g. "- 待決：" -->
{{待決：例「- 待決：會議室真的在不同時區嗎？」}}
````

H1 寫成 `改造報告：{{技能名稱}}`，是 Ch 10 踩雷 2 的教訓：要守的前綴要編進模板。我們先確認它擋得住三種情況：一份照格式填好的兩列小報告加 `--allow-open` 是 `1 files, 0 problems`；同一份不加 `--allow-open`，待決那行被報成 `open question [NEEDS CLARIFICATION: 會議室真的在不同…]`；把一列去處的 `7-7` 改成 `7`，得到 `1 line(s) in "去處" should start like "| 12-19" (shape "| 9-9")`。第二種就是人的閘門：agent 交件時可以帶著問題，人答完、刪掉標記，報告才過得了嚴格檢查。

## 三、module_check.py：完成條件是一支指令

`module_check.py` 匯入 Ch 7 的 `measure()` 與 Ch 10 的 `load()`，不重寫它們；自己只加脊椎預算、接線、規則欄位、腳本介面四類檢查：

```python
#!/usr/bin/env python3
"""module_check.py: check that a skill is a spine plus modules, the shape skill-engineering builds.
SKILL = a skill directory. Problems:
  frontmatter  not YAML; name is not the folder name; description empty or over 1024 chars
  spine        skill_metrics.py findings: over --max-spine lines, unexampled rules, no done line that names
               a command, an inline template, an unclosed fence
  wiring       a bundled file SKILL.md never names (evals/ is exempt: it is test data for the skill, not for
               the agent); a rules/ templates/ scripts/ references/ path SKILL.md names that does not exist
  rules        a "## <ID> title" in rules/*.md missing Trigger:, Rule:, Sign you skipped it:, Enforced by:,
               Good or Bad; an ID used twice
  templates    a templates/*.md with an open [NEEDS CLARIFICATION] (template_check.py would refuse it)
  scripts      a scripts/*.py whose --help does not exit 0
Exit: 0 ok, 1 problems, 2 cannot run."""
import argparse, re, subprocess, sys
from pathlib import Path
try: import yaml
except ImportError: print("error: module_check.py needs PyYAML; run it with a Python that has it", file=sys.stderr); sys.exit(2)
sys.dont_write_bytecode = True                    # importing the helpers must not write __pycache__ into the skill
from skill_metrics import measure
from template_check import load

FIELDS = ("Trigger:", "Rule:", "Sign you skipped it:", "Enforced by:", "Good", "Bad")
NAMED = re.compile(r"\b((?:rules|templates|scripts|references)/[\w.-]+(?:/[\w.-]+)*\w)")

def check(skill, max_spine):
    out, text = [], (skill / "SKILL.md").read_text(encoding="utf-8")
    say = lambda msg: out.append(f"{skill.name}: {msg}")
    head, fm = re.match(r"---\n(.*?)\n---\n", text, re.S), None
    try:
        fm = yaml.safe_load(head[1]) if head else None
    except yaml.YAMLError as e:
        say(f"frontmatter is not YAML ({getattr(e, 'problem', e)}); quote a value that contains ': '")
    if not isinstance(fm, dict):
        if not out: say("no frontmatter mapping between --- lines; the skill never registers")
    else:
        if fm.get("name") != skill.name: say(f'name "{fm.get("name")}" is not the folder name "{skill.name}"')
        if not 0 < len(d := str(fm.get("description") or "")) <= 1024:
            say(f"description is {len(d)} chars; write 1-1024 chars on when to use the skill")
    m = measure(skill)
    for bad, msg in [
        (m["unclosed_fence"], f"code fence opened at line {m['unclosed_fence']} never closes"),
        (m["lines"] > max_spine, f"SKILL.md has {m['lines']} lines, budget {max_spine}; move what one step reads into a module"),
        (m["unexampled"], f"{m['unexampled']} list-item rules have no example; add one, or move the rule to rules/ with Good and Bad"),
        (not m["checkable_done"], "no done line that names a command; write 'Done when `python3 ...` prints ...'"),
        (m["inline_templates"], f"{m['inline_templates']} inline templates; move each to templates/")]:
        if bad: say(msg)
    named, have = set(NAMED.findall(text)), {p.relative_to(skill) for p in skill.rglob("*")}
    for rel in sorted(p.as_posix() for p in have if (skill / p).is_file() and not {"__pycache__", "evals"} & set(p.parts)):
        if rel != "SKILL.md" and rel not in named:
            say(f"{rel} is never named in SKILL.md; list it under Files with the step that reads it, or delete it")
    for rel in sorted(named - {p.as_posix() for p in have}):
        say(f"SKILL.md names {rel}, which does not exist")
    seen = {}
    for rf in sorted((skill / "rules").glob("*.md")):
        for block in re.split(r"(?m)^(?=## )", rf.read_text(encoding="utf-8"))[1:]:
            rid = (block.split() + ["?", "?"])[1]
            if not re.fullmatch(r"[A-Z]{2,4}-\d+", rid):
                continue  # a heading without an ID is a section, not a rule
            if rid in seen: say(f"rules/{rf.name}: {rid} is also used in rules/{seen[rid]}; an ID is never reused")
            seen[rid] = rf.name
            if miss := [f for f in FIELDS if not re.search(rf"(?m)^{re.escape(f)}", block)]:
                say(f"rules/{rf.name}: {rid} lacks {', '.join(miss)}")
    for t in sorted((skill / "templates").glob("*.md")):
        if load(t)[1]: say(f"templates/{t.name} still has [NEEDS CLARIFICATION]; a person answers it first")
    for s in sorted((skill / "scripts").glob("*.py")):
        r = subprocess.run([sys.executable, "-B", str(s), "--help"], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        if r.returncode: say(f"scripts/{s.name} --help exits {r.returncode}; give it argparse and a docstring")
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="+", type=Path, help="skill directories")
    ap.add_argument("--max-spine", type=int, default=80, help="SKILL.md line budget (default 80)")
    a = ap.parse_args()
    if bad := [str(p) for p in a.skills if not (p / "SKILL.md").is_file()]:
        ap.error(f"no SKILL.md in {', '.join(bad)}; pass skill directories")
    probs = [p for s in a.skills for p in check(s.resolve(), a.max_spine)]
    print(*probs, f"{len(a.skills)} skills, {len(probs)} problems", sep="\n")
    return 1 if probs else 0

if __name__ == "__main__":
    try: sys.exit(main())                         # a crash exits 2: it must not look like "problems found"
    except Exception as e: print(f"error: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(2)
```

80 行是本課的預算，不是標準：Claude Code 文件的建議是「Keep `SKILL.md` under 500 lines」，pstack 的 poteto-mode 脊椎 152 行（[pstack Ch 2](../pstack/02-anatomy-of-poteto-mode.md)）。我們選 80，是為了逼出拆分；你的團隊可以用 `--max-spine` 改。frontmatter 用 PyYAML 解析，不用正規表示式，pstack Ch 28 的 skill-lint 第二個 bug（把 `>-` 當成整個 description）就是正規表示式造成的。

一支檢查器要先看它失敗。`fixtures/broken/` 是一個每種錯各放一個的小 skill，五個檔照這段描述自己建：`SKILL.md` 的 frontmatter 是 `name: broken-skill`（不等於資料夾名）與 `description: Toy skill with one defect per module_check rule: use: never`（`rule: use:` 沒加引號），正文只有兩步：``1. Read `rules/style.md` and `references/domain.md` first.`` 與 ``2. Fill `templates/report.md`; done when `python3 scripts/check.py` prints `ok`.``，而 `references/domain.md` 不存在；`scripts/old.py` 只有一行 `print("left over")`，SKILL.md 沒提到它；`rules/style.md` 有兩條 `## ST-1`，第一條只有 Trigger、Rule、Good，第二條六個欄位齊全；`templates/report.md` 是 `# 報告`、`## 結果`、一行 `[NEEDS CLARIFICATION: 要不要列紅燈？]`、一行 `{{結果}}`；`scripts/check.py` 只有 `import sys` 與 `sys.exit(3)`。三支驗證器的結果：

```
$ python3 skills/skill-engineering/scripts/module_check.py fixtures/broken
broken: frontmatter is not YAML (mapping values are not allowed here); quote a value that contains ': '
broken: scripts/old.py is never named in SKILL.md; list it under Files with the step that reads it, or delete it
broken: SKILL.md names references/domain.md, which does not exist
broken: rules/style.md: ST-1 lacks Sign you skipped it:, Enforced by:, Bad
broken: rules/style.md: ST-1 is also used in rules/style.md; an ID is never reused
broken: templates/report.md still has [NEEDS CLARIFICATION]; a person answers it first
broken: scripts/check.py --help exits 3; give it argparse and a docstring
1 skills, 7 problems
```

同一個目錄交給另外兩支驗證器：`node /tmp/lab/tools/skill-lint.mjs fixtures` 印出 `broken: name "broken-skill" is not the folder name` 與 `1 skills, 1 problems`；skill-creator 的 `scripts/quick_validate.py fixtures/broken` 第一行是 `Invalid YAML in frontmatter: mapping values are not allowed here`。三支管三層：`quick_validate.py` 照規格查 frontmatter，遇到第一個錯就停；skill-lint 用正規表示式讀 frontmatter，沒發現 YAML 根本壞了，倒是讀出了 name 不等於資料夾名；`module_check.py` 看不到 name（YAML 解析失敗就無從比對），但看得到模組之間的接線。拿它量其他章的 skill：Ch 10 的 `skills-after/booking-bdd` 有 2 個問題，其中一個是真的漏網之魚，`scripts/template_extract.py is never named in SKILL.md`（沒做 Ch 10 動手練習 1 的 `sync_markers.py`，會多一行 `SKILL.md names scripts/sync_markers.py, which does not exist`）；Ch 5 經過 eval 的 `gherkin-scenario-writer` 有 3 個（93 行、10 條沒例子、沒有指令式完成條件）；skill-creator 本身有 25 個。這 25 個要拆開讀：4 個是脊椎問題（485 行、244 個沒例子的條列、沒有 done 行、1 份內嵌模板）；13 個是慣例不同，它寫 `agents/grader.md`、`assets/…`、`python -m scripts.run_loop` 或只寫檔名 `run_eval.py`，都不在我們的前綴表裡，四支要用 `-m` 跑的腳本以路徑執行 `--help` 會 `ModuleNotFoundError`；7 個檔 SKILL.md 用任何寫法都沒提到；剩下 1 個是真的介面缺口，`quick_validate.py` 不認 `--help`，把它當成 skill 路徑，回 `SKILL.md not found`。`module_check.py` 檢查的是本課的慣例，不是通用標準。

## 底層機制：誰讀了什麼，誰判斷什麼

拆檔有沒有用，取決於 skill 在 Claude Code 裡怎麼被讀。Skill 工具一呼叫，SKILL.md 的內容以一則訊息進入對話並一直留著；自動壓縮後，每個 skill 只保留最近一次呼叫的前 5,000 tokens，所有 skill 共用 25,000（Claude Code 文件「Skill content lifecycle」）。附屬檔只在 agent 讀它時進來，腳本照理只有輸出進 context。本章三次執行的 transcript 記下了實際發生的事：

```
 呼叫 Skill ──▶ 脊椎進對話（SKILL.md 檔案大小，Run 3：3,635 B；單檔版：11,406 B）
     │
     ├─ 每一步點名的模組 ──▶ Read ──▶ 進對話（Run 3 讀了 7 份 markdown + 1 支腳本原始碼，22,883 B）
     ├─ 腳本 ──▶ Bash 執行 ──▶ 只有輸出進對話 ……但 Run 1 動手前先 Read 了六支腳本的原始碼
     └─ done 行的指令 ──▶ 印出 0 problems ──▶ 停；待決留給人
```

三個推論。第一，**漸進揭露（progressive disclosure）只在任務用不到某些步驟時省錢**：從頭走到尾的任務，每一步的模組都會被讀，Run 3 讀進的 skill 文字（26,518 B）是單檔版的 2.3 倍（[Ch 4](./04-prompt-files-to-skills.md)「五、漸進揭露在哪裡失靈」）。拆檔換到的是可檢查、可分開維護，不是必然省 token。第二，**agent 會讀你的檢查器**：Run 1 在第 7 次呼叫讀了 `module_check.py`，第 38 次第一次跑它就是 0 problems；Ch 10 也看過同一件事，所以檢查器要編進你真正要的東西。第三，**接線比篇幅重要**：Anthropic 的寫作指南要求「Keep references one level deep from SKILL.md」，並提醒「If Claude never accesses a bundled file, it might be unnecessary or poorly signaled in the main instructions」。`module_check.py` 的接線檢查，就是讓每個附屬檔都由 SKILL.md 直接點名、寫出哪一步讀。

## 四、拿 bdd-monolith 開刀

工作目錄 `refactor/` 模擬團隊的 repo：Ch 0 冒煙專案的 `app.py`、`features/`、`tests/`，加上 `drafts/bdd-monolith/`（Ch 7 的草稿原樣）、`shared/`（Ch 9 的 `rules/gherkin.md` 與 `signatures.yaml`、Ch 10 的 `templates/bdd-report.md`、`template_check.py`、`template_extract.py`）、空的 `out/`，以及 `.claude/skills/skill-engineering/`。三次執行共用同一支啟動腳本：

```sh
#!/bin/sh
# run-agent.sh DIR NAME PROMPT_FILE: one claude -p run inside DIR; transcript to runs/NAME.jsonl
set -eu
LAB=/tmp/lab/aibdd/ch11-skill-eng
export PATH="/tmp/lab/aibdd/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1   # python3 = course venv (3.12 + PyYAML)
cd "$LAB/$1"
claude -p "$(cat "$LAB/$3")" \
  --setting-sources project,local --strict-mcp-config \
  --tools "Skill,Read,Write,Edit,Glob,Grep,Bash" \
  --allowedTools "Skill,Read,Write,Edit,Glob,Grep,Bash(python3 *),Bash(pytest *),Bash(mkdir *),Bash(cp *),Bash(ls *)${EXTRA_ALLOW:+,$EXTRA_ALLOW}" \
  --permission-prompts none --max-turns 20 --max-budget-usd 3 \
  --no-session-persistence --output-format stream-json --verbose \
  < /dev/null > "$LAB/runs/$2.jsonl" && rc=0 || rc=$?
echo "claude exit=$rc"
```

`EXTRA_ALLOW` 是第五節才加的，這次沒設，展開成空字串。prompt 是使用者會打的話：

```text
drafts/bdd-monolith is the BDD skill that skill-creator drafted for this repo. Refactor it with the skill-engineering skill into a modular skill named bdd: write the skill to out/bdd/ and the report to out/bdd.report.md.

The team's shared rule file, report template and scripts are in shared/ (shared/signatures.yaml is the taxonomy for rule_extract.py); reuse them where they fit. Nobody can answer questions during this run, so put open questions in the report.
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

`./run-agent.sh refactor 01-refactor prompts/01-refactor.txt` 跑了 6 分 3 秒，result 是 `success`，`num_turns=57`、10 次模型回應、55 個 tool call、4 次被拒，Claude Code 估計 US$1.56。`--max-turns 20` 又一次沒有截斷它，和 Ch 5、Ch 7 看到的一樣。讀 transcript 用 Ch 7 的 `tool_calls.jq`，它遇到 Skill 呼叫會當掉（`null (null) cannot be matched, as it is not a string`，jq 結束碼 5），因為這次 Skill 呼叫的參數只有 `skill`（另一個選填欄位是 `args`），前三個欄位都是 null；把目標欄位改成 `.file_path // .command // .pattern // .skill // ""` 就好。時間線：第 1–3 次是 `Skill skill-engineering`、列檔、一個指令連跑三支量測腳本（`rule_extract.py` 認出 GH-03 已涵蓋、32 條無法歸類）；第 4–22 次讀了 17 個檔，包括四支腳本的原始碼、`shared/` 的模組、`app.py` 與測試；第 26–36 次寫 8 個模組檔，另在 `/tmp/bdd-scratch/` 寫 3 份測試用的 log 與 feature 檔；第 37–55 次複製重用的檔、跑檢查（第 38 次第一次跑 `module_check.py` 就是 0 problems）、用好壞兩種輸入測兩支新腳本、寫報告。被拒的是第 13、24、25、39 次：`for` 迴圈裡的變數、`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python3 -m pytest … | tail -15`、一個從 `.claude/` 複製模板的 `cp`、含 `echo "exit=$?"` 的組合指令。

第 24 次被拒最值得記：Claude Code 比對 allow 規則前，只剝掉「certain known-safe environment variables」，「An allow rule won't match past an assignment of any other variable」（permissions 文件）。文件沒有列出那份清單；Ch 10 的兩次 agent 執行裡，`PYTHONDONTWRITEBYTECODE=1 python3 …` 配得上 `Bash(python3 *)`（transcript 裡沒有拒絕），這次多了 `PYTHONPATH=.:tests` 就配不上，所以我們推斷 `PYTHONPATH` 不在清單裡。拒絕訊息又叫 agent 別重試，所以這次執行從頭到尾沒跑過 pytest。改造前後，在 `refactor/` 用 `S=.claude/skills/skill-engineering/scripts` 量：

```
$ python3 $S/skill_metrics.py drafts/bdd-monolith out/bdd
bdd-monolith  lines=222 sections=8 rules=33 unexampled=16 checkable_done=0 bundled=0 inline_templates=0
  problem: 16 rules have no example or anchor
  problem: no done criterion that names a command
bdd           lines=30 sections=1 rules=17 unexampled=0 checkable_done=1 bundled=11 inline_templates=0
2 skills, 2 problems
$ python3 $S/module_check.py out/bdd; node /tmp/lab/tools/skill-lint.mjs out
1 skills, 0 problems
1 skills, 0 problems
```

`rules=17` 不能和 33 直接比：`skill_metrics.py` 數的是條列，改造後的 17 個是 6 個步驟加 11 行 Files 清單；規則搬進了四個規則檔，成為 23 條有 ID、有 Good／Bad／Edge 的規則：照抄的 `gherkin.md`（GH-01..04），新寫的 `scenarios.md`（SC-01..05）、`steps.md`（ST-01..07）、`red-green.md`（RG-01..07）。`steps.md` 的例子取自專案真正的 `tests/test_booking.py`，不是草稿的範例。脊椎從 11,415 B 變 3,452 B，整個 skill 卻長到 38,871 B。另外它多了兩支沒人要求的腳本：`feature_lint.py`（49 行，用 Ch 9 的 probe 查 GH-01..04）和 `red_check.py`（43 行，替紅燈 log 的每個失敗貼標籤），依據是 SK-03。

報告 111 行。去處表 58 列，我們用一段小程式核對過（第六節），涵蓋草稿的全部 222 行，沒有缺口也沒有重疊。待決 17 條，節錄三條：

```text
- 待決：[NEEDS CLARIFICATION: 會議室真的在不同時區嗎？草稿要求 AwareDatetime、步驟寫死 2026-03-02 與 +08:00，但 app.py 的 start、end 是沒有日期的 time。已移除這條陷阱、時間戳與 Mixed datetimes 情境。]
- 待決：[NEEDS CLARIFICATION: 會議室清單是什麼？草稿說 Taipei、Tokyo、Seoul，專案只出現 Taipei。已從描述和規則移除，範例用 Annex 代表第二間，要換成真實名稱嗎？]
- 待決：[NEEDS CLARIFICATION: 這次環境擋掉 pytest，整套測試一次都沒跑。references/booking-example.md 的情境與新 Then 沒有執行過；red_check.py 只用手寫的 log 測過，真實 pytest 輸出的格式未驗證。請先跑一次再定稿，可以嗎？]
```

**人審。** 第一件事是把「沒跑過」的東西跑一次。在冒煙專案的複本加一條 `Rule: Bookings stay inside office hours, 08:00 to 20:00`，底下一個情境 `Scenario: Booking that ends after office hours`（`Given room "Tokyo" is free all day`、`When Carol books "Tokyo" from 19:30 to 20:30`、`Then the booking is rejected because it is outside office hours 08:00-20:00`），在 `tests/test_booking.py` 用 `@then(parsers.parse("the booking is rejected because it is outside office hours {hours}"))` 綁這個 Then，內容是 `assert result == ("rejected", f"outside office hours {hours}")`。還不實作，照新 skill 第 4 步把 log 寫進檔案（情境名稱決定摘要行的長度；換一個名字，下面截斷的位置就不同）：

```
$ mkdir -p reports; PYTHONPATH=.:tests pytest -q tests --driver api > reports/r3.red.log 2>&1
$ grep ^FAILED reports/r3.red.log
FAILED tests/test_booking.py::test_booking_that_ends_after_office_hours - Ass...
$ python3 $B/scripts/red_check.py reports/r3.red.log; echo "exit=$?"
other   tests/test_booking.py::test_booking_that_ends_after_office_hours: read it; not a valid red
passed  2: check none is a new scenario (RG-01)
no valid red: nothing failed on an assertion
1 failures, 2 problems
exit=1
```

（`B` 是 `refactor/out/bdd` 的絕對路徑。）這是一個紅得完全正確的情境，卻被判成「不算紅」。原因在 pytest 9.2.0 的 `_pytest/terminal.py`：verbose 小於 2、又不在 CI（環境變數 `CI` 或 `BUILD_NUMBER`）時，摘要行的訊息會截到終端機寬度；輸出導向檔案時取不到寬度，`_pytest/_io/terminalwriter.py` 退回 `shutil.get_terminal_size(fallback=(80, 24))` 的 80 欄，那行摘要剛好 80 個字元，`AssertionError` 只剩 `Ass...`。`-q -vv` 也不行，兩者相抵後 verbose 是 1。加上 `CI=1` 再跑一次，同一支腳本印出 `valid … read it: is the behavior what is missing? (RG-02)`、`1 failures, 0 problems`，exit 0。照原本的第 4 步「Follow what it prints until it exits 0」，agent 永遠走不出這一步。我們做兩處修改：第 4 步的指令加上 `CI=1`；`red_check.py` 看到以 `...` 結尾的摘要時改以 2 結束，並說出修法（`error: reports/r3.cut.log: pytest cut the summary to the terminal width; rerun it with CI=1`）。

其他決定：Taipei、Tokyo、Seoul 出現在產生草稿的需求裡（Ch 7 的 prompt），只是沒留在 repo，所以恢復，範例的 `Annex` 換回 `Tokyo`。這一條說明 SK-07 的邊界：agent 分不出「草稿模型編的」和「需求給了、但沒記錄下來的」，只有人知道。報告模板的情境表要標籤，專案的 feature 檔卻沒有，於是第 2 步加一句：替新情境加 `@sc_<feature>_<NN>` 並登記到 `pytest.ini` 的 `markers =`。範例 feature 檔與它新加的 Then 真的跑過，`6 passed in 0.10s`。17 條待決的回答寫回報告（`- 已決：…`），嚴格模式的 `template_check.py` 變成 `1 files, 0 problems`。人審過的版本放在 `skills/bdd/`，`module_check.py` 與 skill-lint 都是 0 problems。

## 五、盲測：改造前後，agent 做事有沒有不同

結構指標從 3 個問題降到 0（`module_check.py` 在草稿上報 222 行超過 80 行預算、16 條沒例子、沒有 done 行），只說明它好檢查了，沒說明 agent 做得比較好。照 [pstack Ch 29](../pstack/29-eval-playbook.md)「一、盲測規則」做一次小型盲測：同一個任務、同一個專案，只差 `.claude/skills/bdd/` 的內容。改造前那組放 `bdd-monolith` 的全文，只把 `name:` 改成 `bdd`，免得名字洩題。兩個專案名用亂數分派（`acme-rooms` 是改造前、`meeting-rooms` 是改造後，對照表 `KEY.txt` 放在兩個專案外面；兩份 transcript 都沒碰到它，但專案外的讀取並沒有被擋，[練習 B](./practice-b-modular-bdd-skill.md) 用同一支啟動腳本的對照執行就搜過整個 `/tmp/lab/aibdd`），`.claude/` 以外的檔案逐一比過 `shasum`，完全相同。pstack Ch 29 的 `blind-check.mjs` 在兩組都報 4 行 `LEAK path tests… contains "test"`：那是 pytest 專案本來就有的 `tests/`，兩組一樣，不是實驗的痕跡；prompt 裡沒有任何禁用字。prompt 是一個自然的請求，不問 agent 用了哪些 skill：

```text
Bookings have to stay inside office hours, 08:00 to 20:00. A booking that starts before 08:00 or ends after 20:00 should be rejected with 422 and the detail "outside office hours 08:00-20:00". Please add this rule to the booking API the same way the existing rules were added, and leave me a short note on what you changed and why.
```

評審不是模型，是兩支對兩組一視同仁的腳本，所以看不到模型名稱，也沒有偏好。`grading/probe_r3.py` 是 agent 看不到的驗收探針；`grading/grade.py` 再看 feature 檔涵蓋了哪些邊界，並從 transcript 讀出 agent 打開了哪些 skill 檔、是不是先紅再改 `app.py`：

```python
# probe_r3.py: hidden acceptance probe for R3 (office hours 08:00-20:00), run against a project's app.py
import pytest
from fastapi.testclient import TestClient
from app import app

OUT = "outside office hours 08:00-20:00"
CASES = [("07:30", "09:00", 422, OUT), ("08:00", "09:00", 201, None), ("19:00", "20:00", 201, None),
         ("19:30", "20:30", 422, OUT), ("21:00", "22:00", 422, OUT), ("11:00", "10:00", 422, "end must be after start")]

@pytest.mark.parametrize("start,end,status,detail", CASES)
def test_r3(start, end, status, detail):
    c = TestClient(app); c.post("/reset")
    r = c.post("/bookings", json={"room": "Tokyo", "start": start, "end": end, "by": "Carol"})
    assert (r.status_code, r.json().get("detail") if status != 201 else None) == (status, detail)
```

```python
#!/usr/bin/env python3
"""grade.py PROJECT TRANSCRIPT: the same scripted checks for every project, whichever skill it had.
Exit 0 always: it reports, and a person compares the blocks."""
import json, re, subprocess, sys
from pathlib import Path

LAB, proj, log = Path(__file__).resolve().parents[1], Path(sys.argv[1]).resolve(), Path(sys.argv[2])
def last(*cmd, pythonpath="."):
    env = {"PATH": "/tmp/lab/aibdd/.venv/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": pythonpath}
    return subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True).stdout.strip().splitlines()[-1]
print(f"== {proj.name}\nsuite     {last('pytest', '-q', '-p', 'no:cacheprovider', 'tests', pythonpath='.:tests')}")
print(f"R3 probe  {last('pytest', '-q', '-p', 'no:cacheprovider', '-W', 'ignore::UserWarning', str(LAB / 'grading/probe_r3.py'))}")
text = "".join(p.read_text() for p in proj.glob("features/*.feature"))   # literal steps and Examples rows
spans = re.findall(r"from (\d\d:\d\d) to (\d\d:\d\d)", text) + re.findall(r"\|\s*(\d\d:\d\d)\s*\|\s*(\d\d:\d\d)\s*\|", text)
edges = {k for s, e in spans for k, hit in (("starts<08:00", s < "08:00"), ("starts=08:00", s == "08:00"),
                                             ("ends=20:00", e == "20:00"), ("ends>20:00", e > "20:00")) if hit}
print(f"R3 edges  {len(edges)}/4 {sorted(edges)}")
events = [json.loads(l) for l in log.read_text().splitlines()]
uses = [c for e in events if e["type"] == "assistant" for c in e["message"]["content"] if c["type"] == "tool_use"]
out = {c["tool_use_id"]: json.dumps(c.get("content")) for e in events if e["type"] == "user"
       for c in e["message"]["content"] if isinstance(c, dict) and c.get("type") == "tool_result"}
print(f"skill     {[u['input'].get('skill') for u in uses if u['name'] == 'Skill'] or 'never invoked'}")
print(f"read      {sorted({re.sub(r'.*/[.]claude/skills/', '', u['input']['file_path']) for u in uses
                           if u['name'] == 'Read' and '/.claude/skills/' in u['input']['file_path']}) or 'nothing more'}")
red = next((i for i, u in enumerate(uses, 1) if u["name"] == "Bash" and "pytest" in u["input"]["command"]
            and re.search(r"[1-9]\d* failed", out.get(u["id"], ""))), None)
edit = next((i for i, u in enumerate(uses, 1) if u["name"] in ("Edit", "Write") and u["input"]["file_path"].endswith("/app.py")), None)
print(f"red first first red run: call {red}; first app.py edit: call {edit}")
res = next(e for e in events if e["type"] == "result")
print(f"cost      {len(uses)} tool calls, {res['duration_ms'] / 1000:.0f} s, US${res['total_cost_usd']:.2f}, {len(res['permission_denials'])} denied")
for rep in sorted(proj.glob("reports/*.md")):
    print(f"report    {rep.name}: {last(sys.executable, '-B', str(LAB / 'skills/skill-engineering/scripts/template_check.py'), str(LAB / 'skills/bdd/templates/bdd-report.md'), str(rep))}")
```

兩次執行同時啟動，`EXTRA_ALLOW` 預先核准專案文件寫明的測試指令（`Bash(PYTHONPATH=.:tests pytest *)`、加 `CI=1` 的同一句、`Bash(PYTHONPATH=.:tests python3 *)`），兩組相同。

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

```
$ python3 grading/grade.py acme-rooms runs/02-acme-rooms.jsonl; python3 grading/grade.py meeting-rooms runs/03-meeting-rooms.jsonl
== acme-rooms
suite     13 passed in 0.14s
R3 probe  6 passed in 0.12s
R3 edges  4/4 ['ends=20:00', 'ends>20:00', 'starts<08:00', 'starts=08:00']
skill     ['bdd']
read      nothing more
red first first red run: call 11; first app.py edit: call 12
cost      20 tool calls, 141 s, US$0.37, 1 denied
== meeting-rooms
suite     11 passed in 0.11s
R3 probe  6 passed in 0.11s
R3 edges  4/4 ['ends=20:00', 'ends>20:00', 'starts<08:00', 'starts=08:00']
skill     ['bdd']
read      ['bdd/references/booking-api.md', 'bdd/references/booking-example.md', 'bdd/rules/gherkin.md', 'bdd/rules/red-green.md', 'bdd/rules/scenarios.md', 'bdd/rules/steps.md', 'bdd/scripts/red_check.py', 'bdd/templates/bdd-report.md']
red first first red run: call 23; first app.py edit: call 27
cost      35 tool calls, 105 s, US$0.61, 4 denied
report    office-hours.md: 1 files, 0 problems
```

任務本身兩組都做對：探針 6/6，四個邊界（07:59、08:00、20:00、20:01）都寫進了 Scenario Outline，都是先紅再改 `app.py`，Then 也都寫成 `Then the booking is rejected because it is outside office hours 08:00-20:00`。差別都落在 skill 明文要求的地方。改造後那組照第 2 步替三個情境加上 `@sc_hours_01..03` 並登記 markers，交出一份通過模板的報告；改造前那組在最後訊息裡交代，沒有報告檔。代價：改造後那組讀進的 skill 文字是 2.3 倍、多花 65%，4 次被拒裡有一次是 skill 自己第 4 步的 `> reports/office-hours.red.log`。permissions 文件說輸出重導向的目標要另外過一次寫入檢查，那條指令在這裡要核准；agent 改成不重導向地跑一次，再用 Write 把看到的 log 寫成檔。

真正的行為差異藏在兩份最後訊息裡。兩組都想到帶時區的時間，也都想用一支一次性的腳本試，都被拒（改造前那組第 14 次、改造後那組第 31 次呼叫），之後才分岔。改造前那組的 skill 裡有那句「The rooms are in different timezones ... write a scenario for it」；它改用情境來試，寫了兩個帶時區的情境（`When Alice books "Taipei" from 10:00+08:00 to 11:00+08:00`），在自己的第一版撞到 500，於是讓 R3 比較時鐘時間，並在訊息裡註明「(my assumption)」與「Mixed times still crash」。改造後那組的 skill 裡沒有那句話（改造時刪掉、人審時我們照准），它只憑推理寫下「times with an offset ... should now return a 500」，沒有情境涵蓋。我們事後補一個探針，各訂一筆 `10:00+08:00`–`11:00+08:00` 與 `07:00+08:00`–`09:00+08:00`：

```
$ for p in blind-base acme-rooms meeting-rooms; do (cd $p && PYTHONPATH=. python3 -W ignore -c 'from fastapi.testclient import TestClient; from app import app; c = TestClient(app, raise_server_exceptions=False); c.post("/reset"); print([c.post("/bookings", json={"room": "Tokyo", "start": s, "end": e, "by": "Carol"}).status_code for s, e in [("10:00+08:00", "11:00+08:00"), ("07:00+08:00", "09:00+08:00")]])'); done
[201, 201]
[201, 422]
[500, 500]
```

原始專案收下兩筆，改造前那組收一筆、擋一筆，改造後那組兩筆都 500：Pydantic 2.14.2 接受 `10:00+08:00` 當 `time`，`b.start < OPENS` 拿它和不帶時區的 `time(8)` 比，丟出 `TypeError: can't compare offset-naive and offset-aware times`。這一次，重構前後的 agent 交出了行為不同的 app；最可能的起因是那句被刪掉的話，但每組只跑一次，這是假說，不是結論。那句話一半是編的（會議室在不同時區），一半對這個 app 為真（混用 aware 與 naive 時間會 500；連原始專案都會，先訂 `10:00`、再訂重疊的 `10:30+08:00` 就是 500）。SK-07 的 Edge 因此改寫成第二節看到的版本；要不要拒收帶時區的時間，是交給 PM 的新待決。

限制要說清楚：每組只跑一次、一個模型、一個任務；時區探針是讀完 transcript 才補的，事後補的探針容易偏向我們已經注意到的差異；grader 自己也會看錯：把讀 Examples 表的那個正規表示式拿掉、只讀字面的 `from HH:MM to HH:MM`，改造前那組只剩 `R3 edges 2/4`、改造後那組 `1/4`，因為兩組的邊界都寫在 Scenario Outline 的 Examples 表裡。這是協定的示範，不是 eval。要下結論，照 pstack Ch 29「五、Claude Code 的原生工具：claude plugin eval」用 `--runs` 多跑幾次看分布，並加 `--no-publish`。

## 六、拿它改它自己

「用 skill 優化 skill」最便宜的一步，是讓 skill-engineering 的腳本檢查 skill-engineering 自己。第一次跑，`skill_metrics.py` 說 `bundled=11`，可是這個 skill 只有 8 個附屬檔。`find` 找到 `scripts/__pycache__/` 裡三個 `.pyc`：`module_check.py` 匯入 `skill_metrics`、`template_check`（再匯入 `template_extract`），Python 就把位元組碼寫進了被檢查的 skill。加 `sys.dont_write_bytecode = True` 之後變 `bundled=9`，剩下的一個來自 `--help` 探測：子程序裡的 `template_check.py` 又匯入了一次。子程序改用 `python -B` 才是 `bundled=8`。一支檢查器在它檢查的東西上留下痕跡，下次搬進 `.claude/` 就會一起被搬走；直接跑 `template_check.py` 也會留下 `template_extract` 的 `.pyc`，所以第四節的啟動腳本設了 `PYTHONDONTWRITEBYTECODE=1`。另一個自檢發現：用系統的 Python 3.9.6 跑，原本會丟 `ModuleNotFoundError` 並以 1 結束，在我們的約定裡 1 代表「有問題」；現在印 `error: module_check.py needs PyYAML; run it with a Python that has it`，以 2 結束。同一條約定，最後一行接住所有例外：讀到非 UTF-8 的檔，印一行 `error: UnicodeDecodeError: …` 並以 2 結束，不是 traceback 加 1（Run 1 用的是只接 `OSError` 的前一版，正常輸入下兩版輸出相同）。修完的自檢：

```
$ python3 $S/module_check.py skills/skill-engineering; python3 $S/script_candidates.py skills/skill-engineering/SKILL.md
1 skills, 0 problems
skills/skill-engineering/SKILL.md:10: run      [every] Run every script from the project root as a comman
skills/skill-engineering/SKILL.md:14: lint     [every] 3. Read `rules/skill-writing.md`, then map every l
skills/skill-engineering/SKILL.md:15: lint     [per] 4. Build `<out>/<name>/`. The spine keeps the numb
skills/skill-engineering/SKILL.md:17: lint     [Every] 6. Report: paste the outputs of step 5 under 改造後. 
skills/skill-engineering/SKILL.md:22: lint     [each] - `rules/skill-writing.md`: SK-01..SK-08, where ea
1 files, 5 candidates (lint 4, run 1), 0 judgment lines, 10 already scripted
```

（這裡的 `S` 是 `skills/skill-engineering/scripts`。）五個候選逐條決定：第 10 行是「怎麼執行」的說明，不是檢查；第 17、22 行的 every、each 落在需要判斷的句子裡，誤報；第 15 行「每個附屬檔一行」早就由 `module_check.py` 的接線檢查擋著，掃描器只看得到文字、看不到別處的強制；只有第 14 行「每一段原文都對到一列去處」是真的候選。第四節核對 Run 1 的報告時，我們用一段 7 行的 Python 把去處表的範圍攤開、逐行計數，印出 `58 rows; draft has 222 lines; gaps: []; overlaps: []`；把它寫成 `module_check.py` 的 `--map` 選項，是動手練習 1。第三個自我修正來自第五節：盲測找到的 500，讓 SK-07 的 Edge 從「程式碼裡寫的事實不算捏造」改成「刪掉前提之前，先在程式上試它說的事」。這三次修改都經過人；照 pstack Ch 30 的 `/reflect`，skill 的修改不自動套用。

## 七、量 learn，提出拆法（不改檔）

這個 repo 的 `.claude/skills/learn/SKILL.md` 是最真實的靶子：821 行、38,664 B，觸發一次全部載入。`python3 $S/module_check.py <repo>/.claude/skills/learn` 的結果：

```
learn: code fence opened at line 664 never closes
learn: SKILL.md has 821 lines, budget 80; move what one step reads into a module
learn: 60 list-item rules have no example; add one, or move the rule to rules/ with Good and Bad
learn: no done line that names a command; write 'Done when `python3 ...` prints ...'
learn: 12 inline templates; move each to templates/
1 skills, 5 problems
```

依頂層段落量出大小（第 274–567 行的模板區裡有它自己的 `##` 標題，視為一段），提出去處。ID 前綴是提案：

| 段落（行） | 大小 | 去處 | 依據 |
|---|---|---|---|
| frontmatter、定位、啟動條件、工作流程 Step 1–6（1–177）、互動節奏（812–821） | 11,910 B | 脊椎：description、一句定位、何時啟動、六個步驟、互動節奏；6a、6b 的兩段 markdown 搬到 `templates/` | SK-01、SK-04 |
| 教學設計原則（178–249）、命名（250–273） | 4,427 B | `rules/teaching.md`（TD-01..） | SK-02 |
| 檔案模板（274–567） | 8,337 B | `templates/readme.md`、`chapter.md`、`practice.md`；巢狀 fence 的問題一起消失 | SK-04 |
| 資源品質標準（568–596） | 1,801 B | `rules/reading.md`（RD-01..） | SK-02 |
| 正確性關卡（597–628） | 2,785 B | 四條進脊椎當不可違反條款，其餘 `rules/correctness.md` | SK-01 |
| 深度標準（629–669）、風格指南（685–766） | 7,116 B | 能機械判斷的交給 pstack 期末專案的 `check-chapter.mjs`；其餘 `rules/style.md`（WR-01..） | SK-03、SK-02 |
| 規模指引（670–684）、常見狀況（777–811） | 1,998 B | `references/` | SK-01 |
| 範例參考（767–776） | 290 B | 刪，進待決 | SK-06 |

留在脊椎的段落加起來 11,910 B，扣掉 Step 6 兩段內嵌模板（第 133–137、147–170 行）的 1,059 B，上限是 10,851 B，約現在的 28%；真正的脊椎會比這更短。最該先處理的是正確性關卡：它從第 597 行開始，Ch 7 已經說明它多半落在壓縮後保留的前 5,000 tokens 之外，拆完之後最要緊的四條就在脊椎開頭。「範例參考」那一段要進待決：learn 的 description 與風格指南都說以 `compilers/compiler_frontend/` 為基準，但拿 pstack 期末專案的 `check-chapter.mjs` 檢查它點名的兩個範本，得到 `2 files, 7 problems`：`01-frontend-overview.md` 只有 192 行，沒有 `> **目標**：`、延伸閱讀、踩雷集錦、進階；練習範本 331 行，低於 400，也沒有 `> **目標**：`。範例和模板已經分家，該跟誰是人的決定。完成條件可以寫成 `Done when node scripts/check-chapter.mjs <file> prints 0 problems`，那支檢查器就是 [pstack 期末專案](../pstack/final-project-course-mode.md) 從 learn 抽出來的；[Ch 10](./10-templates-and-scripts.md) 的 `script_candidates.py` 在 learn 上找到的 24 個候選，是 `scripts/` 的工作清單。

## 對比與取捨

| 怎麼改一個 skill | 優點 | 缺點 | 適用 |
|---|---|---|---|
| 手改，照 pstack Ch 28 的寫作原則 | 最靈活 | 因人而異、沒有檢查、改完不知道漏了什麼 | 小 skill、一次性 |
| skill-creator 的 improve 迴圈 | 有 with／without 的 baseline，能調觸發率 | 不管結構；Ch 5 經過一輪 eval 仍沒有指令式完成條件 | 要量輸出品質、調 description |
| skill-engineering（本章） | 結構可檢查、重用團隊模組、每段原文有去處、問題交給人 | 不量行為；agent 會照檢查器寫；17 條待決要人答；檢查器帶著本課的慣例 | 已有草稿、要長期維護 |
| pstack `/reflect`（Ch 30） | 從真實的一次 session 學 | 一次是軼事 | 事後修補 |
| 盲測（pstack Ch 29） | 看行為，而且評審不知道哪組是哪組 | 貴；n 小時分不出運氣與差異 | 改動會影響很多 session 時 |

## 踩雷集錦

1. **錯誤直覺：「結構指標從 3 個問題降到 0，skill 就變好了。」** 正確認識：盲測裡兩組都通過驗收探針；改造後那組多交了報告與標籤，交出的 app 卻讓一筆 `10:00+08:00` 的預約從 201 變成 500，讀進的 skill 文字還是 2.3 倍、多花 65%。結構分數量的是「好不好檢查」，Fowler 那句「without changing its observable behavior」要另外驗。
2. **錯誤直覺：「agent 說它測過，就是測過。」** 正確認識：`red_check.py` 只在手寫的 log 上測過（agent 自己在待決裡寫了）；真實的 pytest 輸出被截成 `- Ass...`，有效的紅被判成無效。人審的第一步，是把待決裡「沒跑過」的項目真的跑一次。
3. **錯誤直覺：「虛構的前提刪掉就好。」** 正確認識：時區前提是編的，「混用 aware 與 naive 時間會 500」對這個 app 卻是真的。兩組 agent 都想試帶時區的時間、都被拒；skill 裡還留著「write a scenario for it」的那組改寫成情境、抓到 500，那句話已被 agent 與人一起刪掉的那組沒有，交出遇到時區就 500 的 R3（n=1，不能當定論）。刪之前先在程式上試它說的事。
4. **錯誤直覺：「skill 裡寫的指令，agent 都跑得動。」** 正確認識：`PYTHONPATH=…` 開頭的指令配不上 `Bash(python3 *)`，輸出重導向在 `-p` 下要核准，兩次都被拒；第一次讓 Run 1 整場沒跑過測試。skill 的指令要在同樣的權限設定下跑過一次。[Ch 27](./27-ai-bdd-loop.md) 的最終版乾脆讓檢查器自己跑 pytest。
5. **錯誤直覺：「檢查工具沒報錯，就沒有副作用，也沒有偏見。」** 正確認識：`module_check.py` 第一次自檢就把 `__pycache__` 寫進了被檢查的 skill；拿它量 skill-creator，25 個問題裡 13 個只是慣例不同。評分腳本也會錯：grader 少讀 Examples 表，邊界分數就從兩組的 4/4 掉到 2/4 與 1/4。

## 進階：再往深一層

- **讓 skill-engineering 改它自己的文字**：本章只讓腳本檢查它自己。下一步是開一個 `claude -p`，對 `skills/skill-engineering` 執行它自己的六個步驟，輸出到 `out/`，看它對 SK 規則提出什麼修改，再照 pstack Ch 30 的 `/reflect` 只收人核准的部分。
- **把盲測擴成 eval，並限制每一步讀的量**：用 `claude plugin eval` 的 `--runs` 對同一組 case 各跑數次、`--ablation` 加無 plugin 的對照組，保留本章的探針當 grader，先用 blind-check 擋掉洩題的 case。Run 3 把四個規則檔全讀了；替每個規則檔宣告「哪一步讀」，讓 `module_check.py` 比對脊椎點名的步驟，是控制 context 的下一步。

## 動手練習

1. 把第六節的覆蓋檢查做成 `module_check.py --map REPORT --source DRAFT`：有缺口或重疊就各報一行並以 1 結束。先拿 Run 1 的報告驗證它通過，再刪掉一列，確認它指出缺了哪幾行。
2. 對 Ch 9 的 `signatures.yaml` 修一個盲點：`rule_extract.py` 沒把草稿第 37 行的「one `When` per scenario」歸到 GH-04，因為反引號把關鍵字隔開了。改 `one-when` 的 `for` 正規表示式，重跑第四節的量測，確認它變成 covered，且其他類別的結果沒變。
3. 挑你自己的一個單檔 skill，照本章的工作目錄擺好，用 skill-engineering 跑一次。先預測 `module_check.py` 會報什麼、待決會有幾條，再對答案；把你回答待決時改掉的決定，列成一張「agent 不可能知道」的清單。

## 本章重點整理

- skill-engineering 是 Part 2 的方法套在自己身上：腳本量與檢查（`skill_metrics.py`、`script_candidates.py`、`rule_extract.py`、`template_check.py`、`module_check.py`），agent 做需要判斷的對應與改寫，人回答待決、決定收什麼、要不要盲測。
- 完成條件是一支指令；報告的待決用 `[NEEDS CLARIFICATION]` 標記，agent 可以帶著問題交件，人答完之前過不了嚴格檢查。
- 在 `bdd-monolith` 上：222 行 → 30 行的脊椎加 23 條帶例子的規則，`module_check.py` 從 3 個問題到 0；人審找到一個真的缺陷（截斷的摘要讓 `red_check.py` 誤判），也證明 agent 分不出「編的」與「沒記錄的」事實。
- 結構好檢查不等於行為不變：n=1 的盲測裡兩組都通過驗收探針，改造後的版本多交了報告與標籤、多花 65%；它的 R3 遇到帶時區的時間就 500，最可能的起因是被一起刪掉的那句時區警告，但這只是假說。

## 自我檢核

- [ ] 不看本章，說出 skill-engineering 六個步驟各由誰負責（腳本、agent、人），以及哪一步的輸出是給人的閘門。
- [ ] 為什麼 Run 3 讀進的 skill 文字比單檔版多？在什麼樣的任務上，拆檔才會真的省 context？
- [ ] `red_check.py` 在真實 log 上誤判的原因是什麼？如果 agent 照原本的第 4 步做，會發生什麼事？
- [ ] 盲測中哪一個差異最可能來自 skill 的結構，哪一個來自 skill 的內容？各舉一個；再說明時區探針為什麼是「事後補的」，以及 n=1 讓你不能下什麼結論。
- [ ] 你會怎麼回答 Anthropic 指南那句「不需要 a writing skills skill」？哪些東西是模型無論多強都不知道、必須寫進 skill 的？

## 延伸閱讀

- **[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)** — Anthropic（Claude Developer Platform 文件）
  - **讀哪裡**：「Develop Skills iteratively with Claude」（Claude A 改 skill、Claude B 用 skill 做真實任務）、「Observe how Claude navigates Skills」、「Build evaluations first」。
  - **學到什麼／關聯**：本章的分工就是 Claude A／Claude B 模式加上腳本與人審；「Ignored content」那一段解釋為什麼要從 transcript 看 agent 讀了哪些檔。前提是讀過 [Ch 4](./04-prompt-files-to-skills.md) 的三層載入。
- **[Extend Claude with skills](https://code.claude.com/docs/en/skills)** — Claude Code 文件
  - **讀哪裡**：「Add supporting files」與「Skill content lifecycle」兩節，以及 `${CLAUDE_SKILL_DIR}` 的說明。
  - **學到什麼／關聯**：SKILL.md 進對話後一直留著、壓縮後每個 skill 保留前 5,000 tokens；附屬檔要由 SKILL.md 點名並說明何時讀。這是 SK-01 與 `module_check.py` 接線檢查的依據。權限細節在同站的 [Configure permissions](https://code.claude.com/docs/en/permissions)：「Compound commands」、「Wrappers」（最後一段講環境變數剝除）與「Redirections」三段，解釋了本章幾次被拒的原因。
- **[skill-creator 的 SKILL.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/skill-creator/skills/skill-creator/SKILL.md)** — Anthropic（`claude-plugins-official`）
  - **讀哪裡**：「How to think about improvements」的四條原則與「Advanced: Blind comparison」。
  - **學到什麼／關聯**：它的改進迴圈以 eval 為中心，「Explain the why」與本章 SK-08 的取捨正好相反；它的盲測比較交給 `agents/comparator.md`，評的是輸出，不是 skill 的結構。和本章對照，看得出兩者在補彼此的洞。
- **[playbooks/authoring-a-skill.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/authoring-a-skill.md)** — pstack
  - **讀哪裡**：全文，重點是「When in doubt, delete. Keep only prose that changes a decision.」那一段。
  - **學到什麼／關聯**：SK-08 與 SK-01 的原文出處；它的四步（寫、驗證、有結構才寫測試、開 PR）可以看成 skill-engineering 的人工版本。這個 repo 的解讀在 [pstack Ch 28](../pstack/28-skill-authoring.md)。
- **[Martin Fowler — DefinitionOfRefactoring](https://martinfowler.com/bliki/DefinitionOfRefactoring.html)**（bliki，2004-09-01）
  - **讀哪裡**：全文很短，名詞與動詞兩個定義。
  - **學到什麼／關聯**：「without changing its observable behavior」是本章第五節存在的理由。程式有測試定義可觀察行為；skill 沒有，只能靠盲測抽樣，這是 skill 重構比程式重構難的地方。

模組化的 `bdd` skill 管住了「怎麼做」，報告裡的待決卻一再指向同一件事：要做什麼，沒有任何檔案寫下來。Part 2 的最後一步，是你自己把另一個單檔 skill 從頭拆一次。

→ [練習 B：把單檔 skill 拆成模組化 skill](./practice-b-modular-bdd-skill.md)
