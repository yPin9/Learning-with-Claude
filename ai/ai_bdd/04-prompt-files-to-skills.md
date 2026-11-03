# Ch 4 — Prompt files 的天花板：為什麼會有 Skill

> **目標**：說得出 prompt file 從 `.cursorrules`、CLAUDE.md、AGENTS.md 一路演化到 Agent Skills 的理由；把同一份知識包成兩種形狀（全塞進 CLAUDE.md vs 短 CLAUDE.md + 3 個 skill），實際量出每一輪常駐的 token，親眼看 Claude Code 怎麼列出、載入一個 skill；知道漸進揭露在哪裡失靈，以及一條規則該放 prompt file、skill、hook 還是 lint。

> **環境**：Claude Code 2.1.295（`claude -p` 預設模型 claude-opus-5-5，context window 1,000,000 tokens）、macOS 26.6 arm64、Python 3.12.15 + PyYAML 6.0.3（`source /tmp/lab/aibdd/.venv/bin/activate`）、jq 1.7.1。實驗目錄 `/tmp/lab/aibdd/ch04-prompt-files/`，三次 `claude -p` 於 2026-11-02 執行。

## 為什麼需要這個？

[Ch 3](./03-propose-constrain-feedback.md) 的 CLAUDE.md 只有幾行約束（CLAUDE.md 的基本寫法見 [ai_applications Ch 5](../ai_applications/05-claude-code-basics.md) 的「CLAUDE.md:每個專案都該有」）。真實團隊的 CLAUDE.md 不會停在幾行：agent 第二次犯同一個錯，就多一條；code review 抓到一個慣例，又多一段；有人把 release 流程貼進去，再多四十行。每一條加的時候都合理，加總起來卻是一份**每個 session、每一次請求都整份送進 context** 的文件。

官方的建議很直接：「target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence.」這句話有兩個成本。一個是錢與空間：常駐的文字每一輪都付。另一個更麻煩：注意力。Liu et al. 的 lost in the middle 與 Chroma 的 context rot 報告都指出，輸入越長、無關內容（干擾項，distractor）越多，模型抓重點的能力越不穩定（機制回看 [harness Ch 10](../harness_engineering/10-context-window-budget.md) 的「理由二：注意力會被稀釋」）。這個 repo 自己就有證據：[pstack Ch 15](../pstack/15-delegation-meta-autonomy.md) 的「三、Encode Lessons in Structure」量到，learn skill 裡那條禁用字規則每次寫課都在 context 裡，全 repo 仍有 510 處違規，其中 4 處是作者在規則就在眼前時寫的。問題於是變成：**怎麼把大量 know-how 交給 agent，又不讓它每一輪都付全部的錢、分全部的心？** 業界花了將近兩年，從「一個常駐檔」走到「一櫃子按需打開的 skill」。本章把這段路走一遍，再用實驗量出它省了什麼、沒省什麼。

## 先建立直覺：公告欄與工具櫃

```
 A：公告欄（一份大 CLAUDE.md）              B：工具櫃（短 CLAUDE.md + skills）
 ┌──────────────────────────────┐          ┌──────────────────────────────┐
 │ 專案事實（5 條）               │          │ 專案事實（5 條）               │ ← 每輪都看
 │ 寫情境的規則（34 行）          │          ├──────────────────────────────┤
 │ 寫測試的規則（20 行）          │          │ 抽屜標籤（name + description） │ ← 每輪都看
 │ 發版流程（9 行）               │          ├──────────────────────────────┤
 └─ 每一輪全部重看一次 ──────────┘          │ 抽屜內容（SKILL.md body）      │ ← 任務需要時才拉開
                                           │ 抽屜裡的附件（scripts/ 等）    │ ← body 叫它讀時才讀
                                           └──────────────────────────────┘
```

公告欄上的每一個字，路過的人都得掃過；工具櫃只讓人看到抽屜標籤，需要時才拉開一格。這就是漸進揭露（progressive disclosure）：metadata 常駐、body 觸發時載入、附檔需要時才讀（三層的定義回看 [harness Ch 29](../harness_engineering/29-skills.md) 的「二、Progressive disclosure」）。這個比喻有三個地方會騙人，後面都會量給你看：抽屜**一旦拉開就不會自己關上**（body 留在對話裡直到 session 結束）；**標籤本身也佔牆面**（skill 一多，清單就變長）；**拉不拉抽屜是 agent 自己決定的**（它可能不拉，也可能拉錯）。

## 一、prompt file 的四代演化

```
 代（1 單一常駐檔／2 分範圍規則／3 叫得出名字的 prompt file／4 Agent Skills）
 1  2024-04  Cursor .cursorrules           0.32（04-12）：repo 根目錄一個常駐檔，整個專案共用
 1  2024-10  Copilot copilot-instructions  VS Code 先支援（10-29），github.com 2025-01 跟進
 1  2025-02  Claude Code CLAUDE.md         上架首日（02-24）的 0.2.9 已會自動載入
 1  2025-08  AGENTS.md                     跨工具開放格式（agentsmd/agents.md 建於 08-19）
 2  2025-01  Cursor 0.45 .cursor/rules     多個規則檔，Agent 自己挑；後來發展成四種套用模式
 2  2025-05  Copilot .instructions.md      VS Code 1.100：frontmatter 的 applyTo 指定適用檔案（05-08）
 2  2025-12  Claude Code .claude/rules     2.0.64：`paths:` 決定何時載入（12-10）
 3  2025-03  Claude Code .claude/commands  0.2.31：你打 /名字，把一段 prompt 塞進對話（03-05）
 3  2025-09  SlashCommand tool             1.0.123：Claude 也能自己叫用 command（09-23）
 4  2025-10  Agent Skills                  Anthropic 發表（10-16），Claude Code 2.0.20 同日支援
 4  2025-12  開放標準                      agentskills.io 規格（12-18）
 4  2026-01  commands 併入 skills           2.1.3（01-09）：.claude/commands/x.md 成為 skill 的舊寫法
```

日期來源：Cursor changelog 0.32.x（2024-04-12，第一次出現 `.cursorrules`）與 0.45.x（2025-01-23）；GitHub changelog 2024-10-29（VS Code 的 Copilot Chat v0.22「newly introduced」`.github/copilot-instructions.md`）與 2025-01-21（github.com 公開預覽）；VS Code 1.100 release notes（2025-05-08；GitHub 的 Copilot coding agent 到 2025-07-23 才支援 `.instructions.md`）；Claude Code 版本號取自 CHANGELOG、日期取自 npm 發佈時間（UTC）；Anthropic 工程部落格（2025-10-16，2025-12-18 更新）。

| 世代 | 解決了什麼 | 留下什麼 |
|---|---|---|
| 單一常駐檔 | 不用每次重講專案慣例 | 什麼都放一起，越長越貴、越不被遵守 |
| 分範圍規則 | 依路徑載入；Cursor 的 description 模式已經是「讓 agent 自己挑」 | 仍然只有文字；範圍只能用路徑表達，不能用「任務」表達 |
| 叫得出名字的 prompt file | 多步驟流程不必常駐，需要時 `/名字` 叫出來 | 一個檔、沒有附件；最初只有人能叫，2.1.295 的 command 檔也不在模型的 skill 清單裡（[Ch 5](./05-skill-creator.md) 實測） |
| Agent Skills | 名字 + description 常駐、body 按需、可附 script 與範本、跨工具標準 | 清單仍常駐、觸發仍是機率、body 載入後不會離開 |

Cursor 的四種模式（Always / Auto Attached / Agent Requested / Manual，現在的文件改叫 Always Apply / Apply to Specific Files / Apply Intelligently / Apply Manually）與 AGENTS.md 生態，SDD 課 [Ch 31](../spec_driven_development/31-tooling-landscape.md) 的「3a. Cursor」與「4. Context 檔」已經整理過。其中「CLAUDE.md 與 AGENTS.md 的關係」記下一個常被誤會的細節（官方 memory 文件，2026-11-02 查證）：Claude Code 2.1.277 起才直接讀 AGENTS.md，而且預設**只有在找不到 CLAUDE.md 時**才讀；兩者並存時，要在 CLAUDE.md 裡 `@AGENTS.md` 匯入，或把 Project instructions 設定改成 `claude-md-and-agents-md`。

## 二、常駐 prompt file 的六種失效

| 失效 | 機制 | 你會在哪裡看到 |
|---|---|---|
| 固定稅 | CLAUDE.md 在每一次請求都整份送出 | 本章實測 1、2 的 usage |
| 干擾項稀釋注意力 | 寫測試時，發版流程和情境規則也在 context 裡 | context rot 報告：干擾項的影響隨長度變大 |
| 任務之間互相衝突 | 「發版時不准改 features/」和「寫情境時更新 features/」同時在場 | 官方：「if two instructions contradict each other, Claude may pick one arbitrarily」 |
| 越長越不被遵守 | 規則被淹沒在中段 | 官方的 200 行建議；pstack Ch 15 的 510 處違規 |
| 只能放文字 | 不能附 script、範本、範例檔 | 每次都讓模型即興重寫同一段程式 |
| 可攜性差 | `.cursorrules`、`copilot-instructions.md`、CLAUDE.md 各寫一份 | AGENTS.md 與 Agent Skills 標準要解的就是這個 |

還有一條常被忘記的事實：官方 memory 文件說，CLAUDE.md 是以一則 user message 接在系統提示**後面**送進去的，Claude 把它當「context, not enforced configuration」。模型讀了、試著照做，但沒有保證。必須每次都成立的規則，從來就不屬於 prompt file。

## 三、動手：同一份知識，兩種包裝

先手寫 B（短 CLAUDE.md + 3 個 skill），再用程式把它「壓平」回沒有 skill 的年代，得到 A。每個 SKILL.md 開頭兩條 `---` 之間是 frontmatter（YAML 設定，`name` 與 `description` 寫在這裡），之後是 body（結構回看 [harness Ch 29](../harness_engineering/29-skills.md) 的「一、SKILL.md 的解剖」）。A 拿掉 frontmatter、標題降一級、每段前加一行來源註解，規則文字逐字相同，差別只在包裝。在空目錄 `/tmp/lab/aibdd/ch04-prompt-files/` 裡建立以下五個檔案，本章的指令都在這個目錄執行。

`b-skills/CLAUDE.md`（只放處處相關的事實）：

```markdown
# booking: meeting-room booking service

- Python 3.12 + FastAPI. Run all tests with `pytest -q` from the repo root.
- Domain logic lives in `booking/schedule.py`; the HTTP layer in `booking/api.py`.
- Times are same-day `HH:MM`. Intervals are half-open `[start, end)`: 10:00-11:00 and 11:00-12:00 do not overlap.
- Business rules have IDs R1-R4. Cite the rule ID in test names, scenario titles and commit messages.
- Do not edit files under `features/` unless the task is to change a scenario.
```

`b-skills/features/booking.feature`：

```gherkin
Feature: Book a meeting room

  @sc_book_01
  Scenario: R1 overlapping booking is rejected
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Alice books "Taipei" from 10:30 to 11:30
    Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
```

`b-skills/.claude/skills/scenario-conventions/SKILL.md`：

```markdown
---
name: scenario-conventions
description: House rules for Gherkin scenarios in this booking project (fixed step wording, @sc_ ID tags, one When, observable Then). Use whenever you write, edit or review a .feature file or a Given/When/Then scenario here, even if the user does not mention conventions.
---

# Scenario conventions

## Tags
- Every Scenario gets exactly one ID tag `@sc_<feature>_<NN>`, e.g. `@sc_book_03`.
- Continue the numbering already in the file. Never reuse a number, even after a deletion.

## Step wording (reuse verbatim; new wording needs a new step definition)
- `Given room "Taipei" is free all day`
- `Given Bob has booked "Taipei" from 10:00 to 11:00`
- `When Alice books "Taipei" from 10:30 to 11:30`
- `Then the booking is confirmed`
- `Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob`

## Shape
- One `When` per scenario. A second action is a second scenario.
- `Then` states what a user can observe (confirmed, or rejected and why), never internal state.
- 3-5 steps. Start the title with the rule ID: `Scenario: R1 overlapping booking is rejected`.

## Example
Good:

    @sc_book_02
    Scenario: R1 adjacent slots do not overlap
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 11:00 to 12:00
      Then the booking is confirmed

Bad (two actions, internal state in Then, no tag, no rule ID):

    Scenario: booking works
      When Alice books "Taipei" from 10:00 to 11:00
      And Bob books "Taipei" from 10:30 to 11:00
      Then the schedule has 1 item
```

`b-skills/.claude/skills/pytest-conventions/SKILL.md`：

```markdown
---
name: pytest-conventions
description: How unit tests are written in this booking project (rule-ID test names, Arrange-Act-Assert layout, Schedule tested directly, exact error messages). Use when adding or changing tests under tests/, or when asked to write a failing test first.
---

# Test conventions

- Name tests `test_<rule>_<behaviour>`, e.g. `test_r1_overlapping_booking_is_rejected`.
- Lay out each test as Arrange, Act, Assert, separated by one blank line. One Act per test.
- Unit tests call `Schedule` directly. Only tests under `tests/api/` go through HTTP.
- For rejections assert the exception type and the exact message:
  `with pytest.raises(Conflict, match="Taipei is taken 10:00-11:00 by Bob"):`
- No `time.sleep` and no real clock: pass times in explicitly.
- A new behaviour starts with a failing test. Run `pytest -q` and show the failure before changing code.

## Example

    def test_r1_adjacent_slots_do_not_overlap():
        schedule = Schedule()
        schedule.book("Taipei", time(10), time(11), by="Bob")

        booking = schedule.book("Taipei", time(11), time(12), by="Alice")

        assert booking.by == "Alice"
```

`b-skills/.claude/skills/release-checklist/SKILL.md`（有副作用的流程，只准人叫用；你不會想讓 Claude 看程式碼「感覺可以發版了」就自己發版）：

```markdown
---
name: release-checklist
description: Cut a release of the booking service (version bump, changelog, tag). Run only when the user types /release-checklist.
disable-model-invocation: true
---

# Release checklist

1. `git status` must be clean. If not, stop and report.
2. Run `pytest -q`. If anything fails, stop.
3. Bump `version` in `pyproject.toml`: patch for fixes, minor for a new rule.
4. Add `## vX.Y.Z - YYYY-MM-DD` to `CHANGELOG.md` with `### Added` / `### Fixed` lists that cite rule IDs.
5. Commit as `release: vX.Y.Z`, then `git tag vX.Y.Z`.
6. Do not push. Print the push commands for a human to run.
```

`flatten_skills.py` 把 B 壓平成 A：

```python
#!/usr/bin/env python3
"""flatten_skills.py SRC DST: rebuild SRC as if skills never existed. DST/CLAUDE.md is
SRC/CLAUDE.md plus every skill body (frontmatter dropped, headings one level down); DST has no skills."""
import argparse, shutil, sys
from pathlib import Path
import yaml


def body_of(skill_md):
    text = skill_md.read_text()
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end == -1:
        raise ValueError("no frontmatter between --- lines")
    meta = yaml.safe_load(text[4:end]) or {}
    lines, fence = [], False
    for line in text[end + 5:].splitlines():
        fence ^= line.lstrip().startswith("```")       # leave code blocks untouched
        lines.append("#" + line if line.startswith("#") and not fence else line)
    return meta.get("name", skill_md.parent.name), "\n".join(lines).strip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--force", action="store_true", help="replace DST if it exists")
    a = ap.parse_args()
    if a.dst.exists() and not a.force:
        sys.exit(f"error: {a.dst} exists; pass --force to replace it")
    sections, problems = [], 0
    for skill_md in sorted((a.src / ".claude/skills").glob("*/SKILL.md")):
        try:
            name, body = body_of(skill_md)
            sections.append(f"<!-- inlined from .claude/skills/{name}/SKILL.md -->\n{body}")
        except (ValueError, yaml.YAMLError) as e:
            problems += 1
            print(f"{skill_md}: {e}")
    if not problems:
        shutil.rmtree(a.dst, ignore_errors=True)
        skip = (a.src / ".claude").resolve()
        shutil.copytree(a.src, a.dst, ignore=lambda d, n: ["skills"] if Path(d).resolve() == skip else [])
        text = (a.src / "CLAUDE.md").read_text().rstrip() + "\n\n" + "\n".join(sections)
        (a.dst / "CLAUDE.md").write_text(text)
        print(f"wrote {a.dst}/CLAUDE.md: {len(text.splitlines())} lines, {len(text.encode()):,} bytes")
    print(f"{len(sections) + problems} skills, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

```
$ source /tmp/lab/aibdd/.venv/bin/activate && python flatten_skills.py b-skills a-bloated
wrote a-bloated/CLAUDE.md: 73 lines, 3,247 bytes
3 skills, 0 problems
```

73 行離 200 行還很遠，這是刻意的：檔案小到你打得完，量出來的差距才看得出是誰造成的。放大到真實尺寸的版本在第五節。

## 四、量：什麼東西每一輪都在付錢

`context_cost.py` 把官方文件寫的載入規則變成程式：每個檔案歸到「每輪都付」（always、listing）或「用到才付」（invoke、demand），順便檢查官方給的幾條上限。

```python
#!/usr/bin/env python3
"""context_cost.py DIR...: sort a project's instruction files by when Claude Code loads them.
  always   root CLAUDE.md, .claude/CLAUDE.md, CLAUDE.local.md (else AGENTS.md); rules without paths:
  listing  "name: description" of each skill Claude may invoke on its own
  invoke   a SKILL.md body, once the skill is used;   demand  skill folder files, rules with paths:
Problems (code.claude.com/docs, 2026-11-02): CLAUDE.md > 200 lines, SKILL.md > 500 lines,
listing text > 1,536 chars, an invocable skill without description."""
import argparse, sys
from pathlib import Path
import yaml


def split(text):
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end == -1:
        return {}, text                       # Claude Code also ignores such frontmatter
    try: meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError: meta = {}          # Claude Code loads it with no fields set
    return (meta if isinstance(meta, dict) else {}), text[end + 5:]


def scan(root):
    items, problems = [], []
    add = lambda when, p, text: items.append((when, len(text.splitlines()), len(text.encode()), p.relative_to(root)))
    claude = [p for p in (root / "CLAUDE.md", root / ".claude/CLAUDE.md", root / "CLAUDE.local.md") if p.is_file()]
    for p in claude or [p for p in [root / "AGENTS.md"] if p.is_file()]:
        add("always", p, text := p.read_text())
        if len(text.splitlines()) > 200:
            problems.append(f"{p.relative_to(root)}: {len(text.splitlines())} lines, over the 200-line guidance")
    for p in sorted((root / ".claude/rules").rglob("*.md")):
        add("demand" if split(p.read_text())[0].get("paths") else "always", p, p.read_text())
    for skill in sorted((root / ".claude/skills").glob("*/SKILL.md")):
        text = skill.read_text()
        meta, body = split(text)
        name = meta.get("name", skill.parent.name)
        desc = " ".join(str(meta.get(k, "")) for k in ("description", "when_to_use")).strip()
        if not meta.get("disable-model-invocation"):
            if not desc:
                problems.append(f"{name}: no description in frontmatter; Claude Code falls back to the first body line")
            elif len(desc) > 1536:
                problems.append(f"{name}: listing text is {len(desc)} chars, cut at 1,536")
            add("listing", skill, f"- {name}: {desc[:1536]}")
        add("invoke", skill, body)
        if len(text.splitlines()) > 500:
            problems.append(f"{name}: SKILL.md is {len(text.splitlines())} lines, over 500")
        for extra in sorted(f for f in skill.parent.rglob("*") if f.is_file() and f != skill):
            add("demand", extra, extra.read_text(errors="replace"))
    return items, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    total = 0
    for root in ap.parse_args().dirs:
        items, problems = scan(root)
        total += len(problems)
        print(f"== {root}")
        for when, lines, size, rel in items:
            print(f"  {when:<8}{lines:>5} lines {size:>7,} B  {rel}")
        every = sum(s for w, _, s, _ in items if w in ("always", "listing"))
        later = sum(s for w, _, s, _ in items if w in ("invoke", "demand"))
        print(f"  every turn: {every:,} B   only when used: {later:,} B")
        for p in problems:
            print(f"  PROBLEM {p}")
    print(f"{len(ap.parse_args().dirs)} projects, {total} problems")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
```

```
$ python context_cost.py a-bloated b-skills
== a-bloated
  always     73 lines   3,247 B  CLAUDE.md
  every turn: 3,247 B   only when used: 0 B
== b-skills
  always      7 lines     495 B  CLAUDE.md
  listing     1 lines     262 B  .claude/skills/pytest-conventions/SKILL.md
  invoke     20 lines     866 B  .claude/skills/pytest-conventions/SKILL.md
  invoke      9 lines     423 B  .claude/skills/release-checklist/SKILL.md
  listing     1 lines     282 B  .claude/skills/scenario-conventions/SKILL.md
  invoke     34 lines   1,259 B  .claude/skills/scenario-conventions/SKILL.md
  every turn: 1,039 B   only when used: 2,548 B
2 projects, 0 problems
```

這是檔案的位元組，和送進 context 的量有兩個落差。第一，官方 memory 文件說 CLAUDE.md 裡的區塊級 HTML 註解送出前會被拿掉，`context_cost.py` 沒扣：A 那三行 `<!-- inlined from … -->` 共 196 B，實際常駐約 3,051 B。第二，模型付的是 token。Claude Code 自帶一個估價器 `claude plugin details`（[pstack Ch 0](../pstack/00-environment-setup.md) 的「二」用它量過 pstack），它只吃 plugin，所以把三個 skill 包成臨時 plugin，用 `--plugin-dir` 只在這次指令載入：

```bash
mkdir -p probe/.claude-plugin && cp -R b-skills/.claude/skills probe/skills
echo '{ "name": "probe", "version": "0.0.1", "description": "the three booking skills, packaged so plugin details can price them" }' > probe/.claude-plugin/plugin.json
claude --plugin-dir ./probe plugin details probe
```

```
…（略）
  Always-on:   ~238 tok   added to every session

Per-component (rounded)
  component             always-on  on-invoke
  pytest-conventions          ~90       ~370
  scenario-conventions       ~100       ~540
  release-checklist           ~50       ~180
…（略）
```

有了估價，可以先**預測**：A 每輪多付三個 body（約 370 + 540 + 180 = 1,090 tok），B 每輪多付兩條可被 Claude 叫用的清單（約 90 + 100 = 190 tok），所以 B 應該每輪少約 900 tok。用真的 `claude -p` 驗證（實測 1 在 A、實測 2 在 B），兩個目錄跑完全相同的指令。`--setting-sources project` 與 `--strict-mcp-config` 排除你個人的 plugin、skill 與 MCP，讓兩邊只差在專案檔案；`--max-turns 1` 加 `--permission-prompts none`，任何需要核准的工具都會被自動拒絕。（`--max-turns` 在 2.1.295 的 `--help` 裡沒有列出，但可用。）

```bash
list_skills() { (cd $1 && claude -p "List the names of the skills in your skill listing, one per line. Do not call any tools." \
  --output-format json --max-turns 1 --allowedTools Read --permission-prompts none \
  --setting-sources project --strict-mcp-config --no-session-persistence > ../runs/$2.json); }
mkdir -p runs && list_skills a-bloated run1-a && list_skills b-skills run2-b     # 實測 1 在 A、實測 2 在 B
for f in runs/run1-a.json runs/run2-b.json; do jq -r --arg f "$f" '"\($f)  input \(.usage | .input_tokens + .cache_creation_input_tokens + .cache_read_input_tokens) tok (read from cache \(.usage.cache_read_input_tokens), written \(.usage.cache_creation_input_tokens))  skills listed: \(.result | split("\n") | length)"' "$f"; done
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

```
runs/run1-a.json  input 15674 tok (read from cache 8504, written 7168)  skills listed: 13
runs/run2-b.json  input 14761 tok (read from cache 8504, written 6255)  skills listed: 15
```

**B 每輪少 913 tok，和預測的約 900 tok 對得上。** input 是三項的和：和先前請求相同的開頭從 prompt cache 讀（read from cache）、這次新寫進快取的部分（written），加上沒進快取的零頭（這兩次都只有 2 tok），讀法見 [harness Ch 17](../harness_engineering/17-prompt-caching.md) 的「三、讀 usage」。兩次讀快取的量相同（8,504），差異全落在新寫入的那一段，也就是 CLAUDE.md 與 skill 清單所在的位置。B 回答的清單比 A 多兩個名字 `pytest-conventions`、`scenario-conventions`，**沒有** `release-checklist`，和官方表格一致：`disable-model-invocation: true` 的 skill「Description not in context」。（JSON 的 `modelUsage` 另有一筆 claude-haiku-5-5 的 1,190 input tokens 小呼叫，不在主對話的 context 裡，不列入比較。）

## 底層機制：Claude Code 怎麼列出、怎麼載入一個 skill

實測 3 在 B 給一個真的任務，用 `stream-json` 錄下每一則訊息。`Skill` 工具在官方工具表裡標為需要權限，所以放進 `--allowedTools`；`Read` 本來就不需要。

```bash
(cd b-skills && claude -p "features/booking.feature is missing a case: Bob has Taipei booked 10:00-11:00 and Alice books Taipei 11:00-12:00, which must be confirmed. Reply with only the new scenario in a gherkin code block. Do not edit any file." \
  --output-format stream-json --verbose --max-turns 4 --allowedTools "Read,Skill" --permission-prompts none \
  --setting-sources project --strict-mcp-config --no-session-persistence > ../runs/run3-b-task.jsonl)
```

`timeline.jq` 把事件流壓成一行一則：

```jq
# timeline.jq: one line per message of a `claude -p --output-format stream-json --verbose` run
if .type == "assistant" then (.message.usage | .input_tokens + .cache_creation_input_tokens + .cache_read_input_tokens) as $in
  | .message.content[] | "assistant [\($in) tok in] \(.name // "text") \(.input // .text | tostring | gsub("\n"; " ") | .[0:58])"
elif .type == "user" then .message.content[] | "user      \(.type) \(.content // .text | tostring | gsub("\n"; " ") | .[0:58])"
else empty end
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。

```
$ jq -r -f timeline.jq runs/run3-b-task.jsonl
assistant [14820 tok in] Skill {"skill":"scenario-conventions"}
user      tool_result Launching skill: scenario-conventions
user      text Base directory for this skill: /private/tmp/lab/aibdd/ch04
assistant [14820 tok in] Read {"file_path":"/private/tmp/lab/aibdd/ch04-prompt-files/b-s
user      tool_result 1	Feature: Book a meeting room 2	 3	  @sc_book_01 4	  Scen
assistant [15759 tok in] text ```gherkin @sc_book_02 Scenario: R1 adjacent slots do not 
```

```
 （還原成流程）
 session 開始 ─► 系統提示 + CLAUDE.md（一則 user message）+ skill 清單（name: description）
                 （init 事件列出 20 個 skill，含已註冊但不在模型清單裡的 release-checklist，見實測 2）
 第 1 次 API 呼叫 ─► 同一則回應發出兩個 tool_use：Skill 與 Read（兩者 usage 相同）
 Skill 執行 ─► tool_result「Launching skill: scenario-conventions」
            ─► 另一則 user message：「Base directory for this skill: <路徑>」+ SKILL.md body
               （frontmatter 已去掉；base directory 讓 body 裡的相對路徑找得到附檔）
 第 2 次 API 呼叫 ─► 輸入 14,820 → 15,759 tok（+939：body、Read 的結果與 tool 區塊）─► 回答
```

三層的實際價格因此是：清單每輪約 100 tok（估價）；body 從 Skill 執行起留在對話裡，這次讓下一次呼叫多了 939 tok（含 Read 的結果）；附檔要等 body 叫它讀才進來，本章的 skill 沒有附檔。body 進來之後就不走了，官方文件的 skill content lifecycle 一節寫：「the rendered SKILL.md content enters the conversation as a single message and stays there across later turns」，Claude Code 之後也不會重讀這個檔；重複叫用、內容沒變，只加一行「已載入」的提示。對話被自動壓縮（compaction）時，每個叫用過的 skill 只保留最近一次的**前 5,000 tokens**，全部 skill 共用 25,000 tokens 的預算，最舊的先被丟掉。

這次的回答和 skill 裡 Good 範例**一字不差**。這證明 skill 被用上了，卻**不能**證明規則能推廣：題目剛好就是範例本身。公平的遵從度測試要用範例沒涵蓋的案例、每組跑很多次；本章的預算只夠三次 `claude -p`，這個實驗留在動手練習。

## 五、漸進揭露在哪裡失靈

**1. 清單本身會長大。** 生出 N 個幾乎一樣的 skill（description 約 180 字元），只量清單這一層：

```
$ for N in 10 100 300; do
  P=synth$N; rm -rf $P; mkdir -p $P/.claude-plugin; echo "{\"name\": \"$P\", \"version\": \"0.0.1\"}" > $P/.claude-plugin/plugin.json
  for i in $(seq -w 1 $N); do mkdir -p $P/skills/area-$i; printf -- '---\nname: area-%s\ndescription: Conventions for area %s of the booking service (naming, error messages, logging, review checklist). Use when you touch files of area %s or review a pull request that changes them.\n---\n\nKeep area %s small.\n' $i $i $i $i > $P/skills/area-$i/SKILL.md; done
  printf "N=%-4s" $N; claude --plugin-dir ./$P plugin details $P | grep Always-on
done
N=10    Always-on:   ~627 tok   added to every session
N=100   Always-on:   ~6,207 tok   added to every session
N=300   Always-on:   ~18,607 tok   added to every session
```

每多一個 skill，每輪多約 62 tok，線性成長。真正的清單還有上限：官方文件說清單預算是 context window 的 1%（`skillListingBudgetFraction`，預設 `0.01`），超過時名字全部保留，但**從最少被叫用的 skill 開始拿掉 description**——沒有 description，Claude 就很難自己想到它。`plugin details` 的數字一路線性，看不出它有套用這個上限。清單實際花多少，看 `/context` 的 Skills 那一列（套用預算後的大小）或 `/doctor`；超過預算時 `--debug` 的 log 會有警告；`/skill-doctor`（v2.1.252 起）列出每個 skill 的成本與叫用次數，幫你挑該關掉的。本章沒有實測到溢出。

**2. 拉開的抽屜不會關。** 一個 session 若把三個 skill 都用了，B 付 1,039 B 常駐加 2,548 B 的 body，共 3,587 B，**比 A 的 3,247 B（扣掉註解約 3,051 B）還多**。漸進揭露省的是「大部分 skill 在大部分 session 都沒被打開」的那部分；每個 session 都會用到的東西，本來就該是 CLAUDE.md 裡的一行事實。

**3. 大 skill 換個地方一樣大。** 把兩個真實的大 skill 放進同一個實驗：這個 repo 的 learn skill，以及 skill-creator（下一章的主角）：

```bash
REPO=/path/to/Learning-with-Claude      # 這個 repo 在你機器上的位置
mkdir -p scale && cp -R b-skills scale/b-real
cp -R "$REPO/.claude/skills/learn" scale/b-real/.claude/skills/
cp -R ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator scale/b-real/.claude/skills/
python flatten_skills.py scale/b-real scale/a-real && python context_cost.py scale/a-real scale/b-real; echo "exit=$?"
```

```
wrote scale/a-real/CLAUDE.md: 1373 lines, 74,357 bytes
5 skills, 0 problems
== scale/a-real
  always   1373 lines  74,357 B  CLAUDE.md
  every turn: 74,357 B   only when used: 0 B
  PROBLEM CLAUDE.md: 1373 lines, over the 200-line guidance
== scale/b-real
  always      7 lines     495 B  CLAUDE.md
  listing     1 lines     534 B  .claude/skills/learn/SKILL.md
  invoke    817 lines  38,105 B  .claude/skills/learn/SKILL.md
…（略）
  every turn: 1,909 B   only when used: 265,296 B
  PROBLEM learn: SKILL.md is 821 lines, over 500
2 projects, 2 problems
exit=1
```

位元組是精確的：learn 的 body 38,105 B、skill-creator 的 body 32,807 B（另有 17 個附檔，只在需要時讀）。換成 token，估價器的數字會隨打包方式漂移（踩雷 4）：learn 觸發後約 9.2k–15.9k tok、skill-creator 約 8.2k–17.3k tok，兩者常駐都只有 90–190 tok。壓平成 CLAUDE.md 等於每輪付上萬 tok；拆成 skill，平時只付幾百。但 learn 一被觸發，就算取最低估值也超過壓縮後保留的 5,000 tokens，它會留到 session 結束、壓縮後被截到只剩開頭——這正是官方要求「把最重要的指示放在 SKILL.md 開頭」的原因。learn 的結構問題，[Ch 7](./07-messy-baseline.md) 會用 `skill_metrics.py` 量，[Ch 11](./11-skill-engineering-skill.md) 會提出拆法。

**4. 觸發是模型的判斷。** 清單只讓 Claude「有機會」想到它。description 太泛會亂觸發、太窄會不觸發、兩個 skill 太像會互搶。skill-creator 的文件直說：Claude 只會在「自己不太好直接處理」的任務去查 skill，簡單的一步任務即使 description 完全對上也可能不觸發。觸發率要量，下一章就量。

**5. 漸進揭露不是強制力。** body 進了 context，也還是一段請求。官方 skill 除錯文件的建議：Claude 跳過「每次都必須成立」的規則時，把規則搬進 hook。

## 對比與取捨

| 放在哪裡 | 什麼時候進 context | 誰決定 | 每輪成本 | 保證程度 | 適合 |
|---|---|---|---|---|---|
| CLAUDE.md / AGENTS.md | session 開始，每一輪 | 永遠 | 全文 | 請求 | 處處相關的短事實：指令、路徑、半開區間 |
| `.claude/rules/`（有 `paths:`） | 讀寫到符合的檔案時 | Claude Code 比對路徑 | 載入後留在對話 | 請求 | 只跟某些目錄有關的慣例 |
| skill（預設） | 清單每輪；body 用到才進 | Claude 比對 description，或你打 `/名字` | 清單約 60–190 tok/個（本章估價） | 請求，且可能不觸發 | 多步驟程序、參考資料、範例、附 script |
| skill + `disable-model-invocation` | 你打 `/名字` 才進 | 只有你 | 0（實測 2 證實不在清單） | 請求 | 有副作用的流程：發版、部署 |
| hook | 事件發生時由 Claude Code 執行 | harness | 0，除非輸出回到 context | 確定性 | 每次都必須發生的檢查與阻擋（[harness Ch 30](../harness_engineering/30-hooks.md)） |
| lint / test / CI | commit、PR、CI | 工具 | 0 | 確定性 | 能被機械判斷的規則（[pstack Ch 30](../pstack/30-self-improvement-loop.md)「修正的層級」） |
| subagent | 被叫用時，在獨立 context | Claude 或你 | description 每輪都在（像 skill 清單）；執行結果只回摘要 | — | 大量閱讀與搜尋（[harness Ch 26](../harness_engineering/26-subagents.md)） |

讀表的順序：先問「能不能被機器判斷」——能，就去 lint、test 或 hook，不佔任何 context；不能，再問「是不是每個 session 都需要」——是，進 CLAUDE.md；否，做成 skill。CLAUDE.md、memory、skill 的分工，[ai_applications Ch 15](../ai_applications/15-skills.md) 的「Skill vs CLAUDE.md vs Memory 如何分工」已有一張表；本章補上的是量測與後三列。一條規則怎麼隨時間在這些層之間移動，是 [Ch 6](./06-workflows-are-grown.md) 的主題。

## 踩雷集錦

1. **錯誤直覺：「把 CLAUDE.md 拆成好幾個 `@import`，context 就變小了。」** 正確認識：官方文件明說 imports「help you organize a long file but don't reduce its context cost」，被匯入的檔案和匯入它的 CLAUDE.md 一起載入。要真的變小，搬去 skill 或有 `paths:` 的規則。
2. **錯誤直覺：「skill 用完就會卸載。」** 正確認識：body 留在對話裡直到 session 結束；壓縮後每個 skill 只保留前 5,000 tokens、全部共用 25,000。要整個 session 都成立的指示，寫成持續有效的說法（「每次修改後都跑測試」而不是「跑測試」），最重要的放最前面。
3. **錯誤直覺：「frontmatter 上面多一行標題沒關係。」** 正確認識：Claude Code 只在第一行是 `---` 時才讀 frontmatter，否則整份當 body，description 退回 body 的第一行。在 `b-skills` 的副本 `b-broken` 把 `# Scenario conventions (v2)` 放到第一行：`context_cost.py b-broken` 回報 `PROBLEM scenario-conventions: no description in frontmatter; Claude Code falls back to the first body line` 並以 1 結束；`claude plugin validate b-broken` 只給 `⚠ ... No frontmatter block found` 警告、結束碼 0，加 `--strict` 才 `✘ Validation failed` 並回 1。
4. **錯誤直覺：「`plugin details` 的 token 數是量出來的，可以跨 plugin 相加。」** 正確認識：它自己寫著「Token counts are estimates」。照第四節 probe 的做法把 `scale/b-real` 的五個 skill（或單一個）包成臨時 plugin 再估：同一個 pytest-conventions，在 3 個 skill 的 plugin 裡估約 370 tok，放進含 learn 的 5 個 skill 的 plugin 變約 460 tok；同一份 skill-creator，單獨包估約 10.9k、和另外四個一起包估約 17.3k，[Ch 0](./00-environment-setup.md) 在隔離設定目錄正式安裝時估約 8.2k。它也把 `release-checklist` 算進常駐約 50 tok，但實測 2 的清單與 913 tok 的差都顯示它不在清單裡。估價用來在同一份輸出裡比大小，絕對值以真實 usage 為準。
5. **錯誤直覺：「重要的規則寫進 skill 就會被遵守。」** 正確認識：skill 可能不被觸發，觸發了也可能在對話後段被忽略。「不准改 `features/`」這種每次都必須成立的規則，該由 PreToolUse hook 擋下，而不是靠 CLAUDE.md 或 skill 的一句話。

## 進階：再往深一層

- **用路徑劃範圍、讓大 body 不進主對話**：skill 也有 `paths:` 欄位，設了之後只在 Claude 碰到符合的檔案時才**允許**自動觸發（`.claude/rules/` 的路徑規則則是碰到檔案就直接載入）。skill 加 `context: fork`（可配 `agent: Explore`）會在子 agent 的獨立 context 執行，主對話只收到結果；反過來，subagent 定義的 `skills:` 欄位會在啟動時把 skill **全文**預載進子 agent。同一個 skill 內部也能再分層：pstack 把 24 條原則各放一個檔、只在觸發時讀（[pstack Ch 11](../pstack/11-principles-as-vocabulary.md) 的「一、為什麼拆成 24 個檔案」）；工具也走同一條路，MCP 工具平時只常駐名字、schema 用到才載入（[harness Ch 23](../harness_engineering/23-tool-search-deferred.md)）。
- **管清單的旋鈕**：`skillOverrides` 可把 skill 設成 `name-only`、`user-invocable-only` 或 `off`；`skillListingBudgetFraction` 調清單預算；`skillListingMaxDescChars` 調每條 description 的上限（預設 1,536）。plugin 的 skill 不吃 `skillOverrides`，要用 `/plugin` 管。**同一份規則給多個工具讀**：Claude Code 有 CLAUDE.md 時預設不讀 AGENTS.md；最穩的做法是 CLAUDE.md 第一行寫 `@AGENTS.md`，其餘放 Claude Code 專屬的內容。`/doctor prompt-audit`（v2.1.283 起）會找出過時、互相矛盾、指向不存在檔案的指示並提出修改，你同意之前不動檔案。

## 動手練習

1. **先預測再量**：在 `b-skills` 加第 4 個 skill `api-errors`（409 與 422 的 `detail` 格式），先用 `plugin details` 預測實測 2 的 input 會多幾 tok，再自己跑一次實測 2 的指令驗證。差多少？差在哪裡？
2. **公平的遵從度測試**（本章未實測，以下是協定）：把 scenario-conventions 的 Good 範例換成業務規則 R2（end 不晚於 start 就拒絕）的案例，題目改用範例沒有的組合（例如 Carol 已預約 Tokyo 14:00–15:00、Alice 訂 14:30–15:30）。在 A、B 各跑至少 10 次，用 [Ch 5](./05-skill-creator.md) 的 `scenario_rules.py` 評分，比較通過率與平均 input tokens。
3. **對你自己的 repo 跑 `context_cost.py`**：挑一段多步驟流程從 CLAUDE.md 搬到 skill（有副作用就加 `disable-model-invocation: true`），搬前搬後各量一次，並寫下一個「搬了反而更貴」的 session 長什麼樣。再重現第 3 個踩雷，想一想為什麼 Claude Code 自己的驗證器只給警告。

## 本章重點整理

- prompt file 走了四代：單一常駐檔 → 分範圍規則 → 叫得出名字的 prompt file → Agent Skills；每一代都把「不必每輪都看的東西」往外推一層。常駐檔的六種失效：固定稅、干擾項、互相衝突、越長越不被遵守、只能放文字、不可攜；而且它只是請求，不是強制
- 實測：同一份知識，B 每輪比 A 少 913 tok，與估價預測的約 900 tok 一致；`disable-model-invocation` 的 skill 不在模型的清單裡。機制：清單常駐 → 模型呼叫 `Skill` → 「Launching skill」+ 一則含 base directory 與 body 的 user message → body 留到 session 結束，壓縮後只剩前 5,000 tokens
- 界線：清單線性成長且有預算、拉開的抽屜不會關、大 skill 觸發後一樣大、觸發靠機率、不是強制力；選層順序是「能機械判斷 → lint/test/hook；每個 session 都要 → CLAUDE.md；其餘 → skill」

## 自我檢核

- [ ] 不看本章，說出 prompt file 的四代，以及每一代各留下什麼問題沒解決
- [ ] A 的 CLAUDE.md 有 3,247 B，為什麼實測 1 與實測 2 的差只有 913 tok，而不是「A 的全部」？兩次相同的 8,504 代表什麼
- [ ] Claude Code 執行 `Skill` 工具時，對話裡多了哪兩樣東西？body 什麼時候離開 context
- [ ] 你怎麼向同事證明 `disable-model-invocation: true` 的 skill 不佔清單？本章用了哪兩個獨立的訊號
- [ ] 「發版前測試必須全綠」這條規則該放在對比表的哪一列？為什麼不是 release-checklist 裡的一行字

## 延伸閱讀

- **[Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172)** — Nelson F. Liu, Kevin Lin, John Hewitt et al.（TACL）
  - **讀哪裡**：第 2 節多文件問答的「Results and Discussion」與位置曲線圖，模型清單已過時，看方法與趨勢；**學到什麼**：把關鍵資訊放在長輸入的不同位置，模型表現呈 U 形，放在中間最容易被漏掉；**和本章的關聯**：一份 1,373 行的 CLAUDE.md 本身就是長輸入，排在中段的規則最可能被漏掉（這是從論文推論，本章沒有量）
- **[Context Rot: How Increasing Input Tokens Impacts LLM Performance](https://www.trychroma.com/research/context-rot)** — Kelly Hong, Anton Troynikov, Jeff Huber（Chroma，2025-07-14）
  - **讀哪裡**：「Impact of Distractors」一節；**學到什麼**：18 個模型在刻意簡化的任務上，表現仍隨輸入變長而退化，干擾項的影響也隨長度放大；**和本章的關聯**：「無關規則也在 context 裡」到底傷不傷，這是公開量測裡最直接的一份
- **[Claude Code 文件：Extend Claude with skills](https://code.claude.com/docs/en/skills)** 與 **[How Claude remembers your project](https://code.claude.com/docs/en/memory)**
  - **讀哪裡**：skills 頁的「Control who invokes a skill」「Skill content lifecycle」「Skill descriptions are cut short」；memory 頁的「Write effective instructions」「Path-specific rules」「AGENTS.md」；**學到什麼**：每種指示檔在什麼時候載入、各有哪些上限；**和本章的關聯**：本章的 200 行建議、1,536 字元、1% 預算、5,000/25,000 tokens 都來自這兩頁（2026-11-02 查證），版本更新後請重查
- **[Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)** — Anthropic Engineering（2025-10-16，2025-12-18 更新為開放標準）
  - **讀哪裡**：「The anatomy of a skill」「Skills and code execution」「Security considerations when using Skills」三段；**學到什麼**：Agent Skills 的設計理由：三層漸進揭露、為什麼能附程式碼、只裝可信來源的 skill；**和本章的關聯**：第四節與底層機制量到的，就是這三層各自的實際價格
- **[Steering Claude Code: when to use CLAUDE.md, skills, hooks, and subagents](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more)** — Michael Segner（Claude blog，2026-06-18）
  - **讀哪裡**：「When to use each method」一節；**學到什麼**：官方視角的分層決策，例如「Procedures belong in skills. CLAUDE.md is for facts Claude should hold all the time: build commands, monorepo layout, team conventions.」；**和本章的關聯**：對比表的官方版本；本章多了實測與 lint 那一列

三個手寫的 skill 已經量出它們在 context 裡的價格，但「寫得好不好、該觸發時會不會觸發」還沒量。下一章用 Anthropic 的 skill-creator，走一次草稿、測試、評分、調 description、打包的完整迴圈。

→ [Ch 5 用 skill-creator 做出第一個 skill](./05-skill-creator.md)
