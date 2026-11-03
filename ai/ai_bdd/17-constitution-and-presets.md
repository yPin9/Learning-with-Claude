# Ch 17 — Constitution 參數層與改寫 /specify

> **目標**：把 constitution 從「每個 skill 都整份讀一遍的散文」變成**參數層**：每個自訂 SDD skill 在裡面有一節自己的 yaml，缺節就用 skill 的預設值，改值要照規則升版，全部交給 `constitution_params.py` 檢查。再用同一套參數改寫 /specify：用 preset 把驗收條件改成 Gherkin，用自己的 `sdd-specify` skill 加上詞彙表與完成閘門，並實測「直接改安裝好的 skill」在升級時怎麼消失。

> **環境**：Spec Kit 1.2.5、Claude Code 2.1.295（三次 `claude -p`，2026-11-02，帳號預設模型 claude-opus-5-5）、Python 3.12.15 + PyYAML 6.0.3（`source /tmp/lab/aibdd/.venv/bin/activate`）、macOS 26.6 arm64。實驗目錄 `/tmp/lab/aibdd/ch17-presets/`，從空目錄開始。

## 為什麼需要這個？

Spec Kit 的 skill 是通用流程。`/speckit-specify` 不知道你的團隊要 Gherkin 還是 EARS、詞彙表放在哪、一份 spec 最多能留幾個待釐清標記。過去的做法是**改原檔**：SDD [Ch 29](../spec_driven_development/29-spec-kit-internals.md) 的練習三原本請你直接在 `.specify/templates/spec-template.md` 加欄位（現在已改成專案層覆蓋）；0.11.10 的 `/speckit.constitution` 修憲時還會順手改 plan、spec、tasks 三份模板（我們用 `gh api` 讀了 v0.11.10 的指令檔，第 4 步叫「Consistency propagation checklist」）。代價在升級：CHANGELOG 從 0.0.1（2025-08-22）到 1.2.5（2026-10-28）列了 239 個版本，每一版都可能蓋掉你的修改。

1.2.5 的做法分兩條路。模板交給 preset（Spec Kit 的模板覆蓋包，0.3.0 起就有）與專案層覆蓋，執行時才解析，你的版本疊在核心模板上面；constitution 只剩一個寫入者，0.14.4 起 `/speckit-constitution` 不再改模板（CHANGELOG：「stop propagating guidance into templates」），其他 skill 執行時自己去讀。但「自己去讀」讀的是整份散文，沒有一個 stock skill 知道哪一段是給它的設定。本章補兩件事：

1. **參數層**：我們自己寫的 SDD skill（本章示範 `sdd-specify`；Ch 18 的 `clarify-interview`、Ch 24 的 `testplan`、Ch 31 的 `bdd-plan` 都能照同一個約定接上）寫成通用流程，專案特有的值從 constitution 裡以 skill 為名的那一節讀；缺節用預設；改值要升版，腳本把關。
2. **改寫 /specify**：preset、直接改安裝好的 skill、自己的 wrapper skill，三條路各跑一次，看誰活得過升級、誰管得到流程。constitution 的基本用法見 SDD [Ch 28 第一步](../spec_driven_development/28-spec-kit-workflow.md) 與 [Ch 43「一、共同規範」](../spec_driven_development/43-sdd-in-teams.md)；1.2.5 的目錄與 skill 清單見 [Ch 12](./12-spec-kit-1x.md)。

## 先建立直覺：總部食譜、分店設定卡、菜單版型

想像一家連鎖餐廳。總部的**食譜**（stock skill）寫通用流程：「依分店設定的辣度調味」。分店櫃台有一張**設定卡**（constitution 的參數層），卡上沒寫的項目食譜自有預設，改卡要店長簽名、標日期（修憲升版）。**菜單版型**（模板）由區域經理發（preset），分店也能在牆上貼手寫公告（專案層覆蓋），公告最優先。總部改版會**換掉整本食譜**，你寫在食譜上的眉批跟著被收走，版型、公告、設定卡都還在。分店也可以寫一張自己的**流程卡**（`sdd-specify`）：照設定卡調味，出餐前用量杯量一次（腳本閘門）。

比喻在一處不成立：店員會照卡做，agent 不一定。設定卡對 stock skill 只是「被要求去讀的一段文字」，所以本章每一層都配一支腳本。

```
參數  constitution.md ── 散文（原則、Governance）：9 個 stock skill 整份讀，只是提示
                     └─ ## Skill Parameters / ### sdd-specify ← constitution_params.py get（缺節 → params.yaml）
形狀  resolve-template.sh：overrides > presets（priority 小者優先）> extensions > core
流程  /sdd-specify：參數 → 詞彙表 → create-new-feature.sh → 寫 spec → spec_check.py 閘門
```

## 一、1.2.5 裡誰讀 constitution

repo 裡的說法不一致：SDD [Ch 27](../spec_driven_development/27-spec-kit-install.md) 說 plan 會讀，[Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 原本說每道指令執行前都會注入，[Ch 34](../spec_driven_development/34-ubiquitous-language-as-glossary.md) 的「機制一」說 specify 和 plan，都是 0.11.10 時代的閱讀。1.2.5 不用猜：[Ch 12](./12-spec-kit-1x.md) 的 `skill_io_map.py` 已把十個 skill 的用法分成四類，這裡 grep 原文看措辭：

```bash
specify() { uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify "$@"; }   # 本章的 specify
export COLUMNS=160      # rich 在非 TTY 預設 80 欄，長路徑會折行
mkdir -p /tmp/lab/aibdd/ch17-presets/booking && cd /tmp/lab/aibdd/ch17-presets/booking
git init -q && specify init --here --force --non-interactive --integration claude --script sh
grep -c "memory/constitution.md" .claude/skills/speckit-*/SKILL.md | sed 's|.claude/skills/speckit-||; s|/SKILL.md||' | paste -sd ' ' -
# analyze:2 checklist:1 clarify:1 constitution:4 converge:2 implement:1 plan:1 specify:1 tasks:1 taskstoissues:1
```

十個全部提到它，用法分四種（引號內是 SKILL.md 原文）：specify、clarify、checklist、tasks、taskstoissues 五個寫「IF EXISTS: Load `.specify/memory/constitution.md` for project principles and governance constraints」，implement 是「IF EXISTS: Read … for governance constraints」；plan 寫「Read FEATURE_SPEC and `.specify/memory/constitution.md`」（沒有 IF EXISTS），拿它填 Constitution Check，「ERROR if violations unjustified」；analyze 與 converge 有一段「Constitution Authority」，違反 MUST 一律 CRITICAL，converge 遇到沒填的範本就跳過；constitution 讀現值、寫回，且「Write only `.specify/memory/constitution.md`」。

答案是「九個讀、一個寫」，路徑 `.specify/memory/constitution.md`。「注入」要修正：沒有程式把檔案塞進 context，是 SKILL.md 裡的一句指示，agent 照做才發出一次 Read（第五節的實跑看得到）。更要緊的是**每個 stock skill 都讀整份**。SDD [Ch 38](../spec_driven_development/38-build-your-own-pipeline.md) 主張 constitution 在 PLAN 注入、不該干擾 SPEC；stock skill 做不到這種切分，specify 也會讀到「不准用 Redis」。參數層讓我們自己的 skill 只拿到屬於自己的那一節。

## 二、參數層：每個 skill 在 constitution 裡有一節

四條約定：(1) `## Skill Parameters` 底下每個 `### <skill 名>` 一節，名字和 `.claude/skills/<skill 名>/` 一字不差；(2) 每節只放一個 yaml 區塊，散文給人讀、yaml 給腳本讀（說明可以寫在標題和 yaml 之間）；(3) skill 自帶 `params.yaml`，列出它認得的 key 與預設值，constitution 只能覆寫其中的 key；(4) 缺節就全用預設，並在 stderr 說一聲。

為什麼不放別處？獨立設定檔對機器友善，但不在團隊審查、升版的那份文件裡，而「驗收用什麼格式」本來就是治理決定；CLAUDE.md 每個 session 都載入、沒有版本號；把值寫進 skill 資料夾，skill 就不再通用，複製到下一個專案會連值一起帶走。constitution 已經被九個 skill 讀、被團隊審（[Ch 43](../spec_driven_development/43-sdd-in-teams.md)），參數放這裡，改參數和修憲走同一個流程。下面是 `.specify/memory/constitution.md`（1.0.0）：

~~~markdown
# Room Booking Constitution

## Core Principles

### I. Executable Acceptance First
Every user-visible behaviour is specified as at least one concrete scenario before code is
written. A requirement without a scenario is not ready for planning.

### II. Stable Rule IDs
Business rules carry stable IDs (R1..R4). Specs, scenarios, tasks and tests cite them.

### III. Test-First (NON-NEGOTIABLE)
A test is written and seen failing for the right reason before the code that makes it pass.

### IV. Small Surface
Same-day bookings only; times are HH:MM; ranges are half-open [start, end).

## Domain Vocabulary

`docs/glossary.yaml` is the vocabulary: specs use its terms and never the words it lists
under `avoid`. Glossary changes are reviewed like code.

## Governance

Amendments are pull requests that change this file and bump the version.
MAJOR: a principle removed or redefined, or a Skill Parameters value changed or removed.
MINOR: a principle, section, skill section or parameter added. PATCH: wording only.
`tools/constitution_params.py check --against <base>` enforces the parameter rules.

**Version**: 1.0.0 | **Ratified**: 2026-11-02 | **Last Amended**: 2026-11-02

## Skill Parameters

<!-- One ### section per custom skill, named exactly like the skill folder.
     Each section holds one yaml block. A missing section means "use that
     skill's defaults" (its params.yaml). Changing a value is a MAJOR amendment. -->

### sdd-specify

```yaml
acceptance_format: gherkin
glossary: docs/glossary.yaml
```
~~~

`docs/glossary.yaml` 與 `.claude/skills/sdd-specify/params.yaml`：

```yaml
# Domain vocabulary for room booking. Truth artifact: reviewed like code.
terms:
  booking:
    means: "One person's hold on one room for a half-open range [start, end) on one day"
    avoid: [reservation, appointment]
  room:
    means: "One of Taipei, Tokyo, Seoul"
    avoid: [meeting space, venue]
  conflict:
    means: "Two bookings of the same room whose ranges overlap; touching ends do not overlap"
    avoid: [clash, double booking, double-booking]
  cancel:
    means: "Remove a booking; only the person who booked it may do so (R4)"
    avoid: [delete, revoke]
```

```yaml
# Defaults for sdd-specify. The constitution's "### sdd-specify" section may
# override any key listed here; a key not listed here is an error.
acceptance_format: given-when-then   # given-when-then | gherkin | ears
glossary: null                       # path to a glossary yaml, or null
max_clarifications: 3                # the cap /speckit-specify itself uses
scenario_tag: "@sc_[a-z0-9]+_[0-9]{2}"
```

預設 3 來自 `speckit-specify` 自己的「LIMIT: Maximum 3 [NEEDS CLARIFICATION] markers total」；`scenario_tag` 是本課的情境 ID 慣例（`@sc_book_01`）。SDD [Ch 34](../spec_driven_development/34-ubiquitous-language-as-glossary.md) 把術語寫進 constitution，我們反過來只讓 constitution 指向一份 truth 檔（[Ch 33](./33-truth-vs-plan.md)），constitution 保持短，詞彙表照自己的節奏審。這份詞彙表用 `means`，[Ch 28](./28-gherkin-dsl-style.md) 的 `glossary.yaml` 則沿用 SDD Ch 34 的 `definition`（加 `zh`），另有 `rooms`、`people`、`actions` 三欄給它的 `gherkin_lint.py` 用；本章的 `spec_check.py` 與那支 lint 在 `terms` 底下都只讀 `avoid`，拿本章這份餵 lint（2026-11-03 實測），靠那三欄的檢查（GH-01 的動作形態、GH-09 的會議室與人名引號）就不會觸發，而 Ch 28 列為該避開的 `meeting room` 正是第五節需求原文的用詞，兩章要共用一份詞彙表，得先照 Ch 28 的格式對齊。

### `tools/constitution_params.py`

```python
"""Per-skill parameters kept in the constitution's "## Skill Parameters".

  get SKILL               print SKILL's effective parameters as JSON
                          (its params.yaml defaults overlaid by "### SKILL")
  check [--against REF]   validate the constitution; with a git REF, also demand
                          the version bump that the parameter changes require
"""
import argparse, json, re, subprocess, sys
from pathlib import Path
import yaml

CONST, SKILLS = Path(".specify/memory/constitution.md"), Path(".claude/skills")
VERSION = re.compile(r"\*\*Version\*\*: (\d+)\.(\d+)\.(\d+)")
SECTION = re.compile(r"^### (\S+)\n(?:(?!###|```)[^\n]*\n)*```yaml\n(.*?)```", re.M | re.S)

def sections(text):
    tail = text.partition("\n## Skill Parameters")[2].split("\n## ")[0]
    return {m[1]: yaml.safe_load(m[2]) or {} for m in SECTION.finditer(tail)}

def defaults(skill):
    p = SKILLS / skill / "params.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None

def key_problem(skill, given):
    if (base := defaults(skill)) is None:
        return f"### {skill}: no {SKILLS}/{skill}/params.yaml (typo, or a skill without parameters?)"
    if extra := sorted(set(given) - set(base)):
        return f"### {skill}: unknown key(s) {extra}; known: {sorted(base)}"

def get(skill):
    given = sections(CONST.read_text(encoding="utf-8")).get(skill)
    if given is None:
        print(f"note: no '### {skill}' in {CONST}; using defaults", file=sys.stderr)
    if err := key_problem(skill, given or {}):
        sys.exit(f"{CONST}: {err}")
    print(json.dumps({**defaults(skill), **(given or {}), "_from_constitution": sorted(given or {})}, indent=2))

def needed_bump(old, new):
    so, sn = sections(old), sections(new)
    if any(sn.get(s, {}).get(k, object()) != v for s, p in so.items() for k, v in p.items()):
        return 2, "major"                     # a parameter changed or disappeared
    if any(k not in so.get(s, {}) for s, p in sn.items() for k in p):
        return 1, "minor"                     # a parameter or skill section was added
    return 0, "patch"                         # prose only: the level is a reviewer's call

def check(against):
    text = CONST.read_text(encoding="utf-8")
    problems = [f"{len(ph)} unfilled placeholder(s), e.g. {ph[0]}"
                for ph in [re.findall(r"\[[A-Z][A-Z0-9_]+\]", re.sub(r"<!--.*?-->", "", text, flags=re.S))] if ph]
    if "Sync Impact Report" in text:
        problems.append("Sync Impact Report comment still present; remove it before committing")
    if not (m := VERSION.search(text)):
        problems.append("no '**Version**: X.Y.Z' line")
    problems += [e for s, p in sections(text).items() if (e := key_problem(s, p))]
    if against and m:
        old = subprocess.run(["git", "show", f"{against}:{CONST}"], capture_output=True, text=True, check=True).stdout
        ov, nv = tuple(map(int, om.groups())) if (om := VERSION.search(old)) else (0, 0, 0), tuple(map(int, m.groups()))
        got = next((2 - i for i in range(3) if nv[i] > ov[i] and nv[:i] == ov[:i]), -1)
        need, name = needed_bump(old, text)
        if old != text and got < need:
            problems.append(f"changed since {against}: needs a {name} bump, version is {'.'.join(m.groups())}")
    for p in problems:
        print(f"{CONST}: {p}")
    print(f"1 file, {len(problems)} problems")
    return 1 if problems else 0

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = ap.add_subparsers(dest="cmd", required=True)
sub.add_parser("get").add_argument("skill")
sub.add_parser("check").add_argument("--against", metavar="REF")
args = ap.parse_args()
sys.exit(get(args.skill) if args.cmd == "get" else check(args.against))
```

`get` 輸出 JSON（縮排兩格；下面用 `jq -c` 壓成一行），skill 不必懂 Markdown；`_from_constitution` 列出哪些值來自 constitution。`check` 只看得懂參數：散文改了它只要求至少 PATCH，該升哪一級留給審查的人。

```text
$ python3 tools/constitution_params.py get sdd-specify | jq -c .
{"acceptance_format":"gherkin","glossary":"docs/glossary.yaml","max_clarifications":3,"scenario_tag":"@sc_[a-z0-9]+_[0-9]{2}","_from_constitution":["acceptance_format","glossary"]}
```

再做三個破壞。刪掉整個 `### sdd-specify` 節：stderr 印 `note: no '### sdd-specify' in .specify/memory/constitution.md; using defaults`，JSON 變成全預設（`"acceptance_format": "given-when-then"`），exit 0。在 yaml 多打一行 `max_clarification: 2`（少一個 s）：`.specify/memory/constitution.md: ### sdd-specify: unknown key(s) ['max_clarification']; known: ['acceptance_format', 'glossary', 'max_clarifications', 'scenario_tag']`，exit 1。換回 `specify init` 種下的範本再跑 `check`：`19 unfilled placeholder(s), e.g. [PROJECT_NAME]` 與 `no '**Version**: X.Y.Z' line`，`1 file, 2 problems`。缺節是「這個專案接受預設」，所以放行但要說出來；打錯字必須擋，因為悄悄忽略 `max_clarification` 等於悄悄用預設值 3。

### 修憲要升版

Governance 那段規則給人讀，`check --against` 把它變成能擋 commit 的檢查（基準 commit 裡若還是 init 種下、沒有版本號的範本，舊版本當成 0.0.0）。commit 1.0.0 之後，在 `### sdd-specify` 加一行 `max_clarifications: 2`（團隊決定：待釐清的問題要在 Example Mapping 時處理掉，見 [Ch 8](./08-translation-sandwich.md)），版本先不動，`python3 tools/constitution_params.py check --against HEAD` 回 `.specify/memory/constitution.md: changed since HEAD: needs a minor bump, version is 1.0.0` 與 `1 file, 1 problems`，exit 1。版本改成 1.1.0 後是 `1 file, 0 problems`。commit 之後把 `acceptance_format` 改成 `ears`（EARS 需求句型，見 SDD [Ch 11](../spec_driven_development/11-ears-notation.md)）、版本只升到 1.2.0，得到 `needs a major bump, version is 1.2.0`。為什麼改值算 MAJOR？因為已經寫好的產物會一夕之間不合規，第六節有實例。這對應 [semver](https://semver.org/) 對 MAJOR 的定義：不相容的變更（把參數看成 skill 讀的 API）。這套規則有個漏洞：腳本比的是 constitution 的文字，不是疊上預設之後的有效值。`max_clarifications: 2` 算「新增參數」，只要求 MINOR，實際上卻把預設的 3 收緊成 2，一份有 3 個待釐清標記、原本合規的 spec 會因此被第四節的閘門以 SPEC-3 擋下（實測）。要堵住，就讓 `needed_bump` 先把兩邊都疊上 `params.yaml` 的預設再比。最後一次修改我們退回去，本章後面都停在 1.1.0（`max_clarifications: 2`）。

## 三、用 preset 改 spec 的形狀

參數管得了「要什麼格式」，管不了模板長什麼樣。核心 spec-template 在每個 user story 底下放的是 `1. **Given** [initial state], **When** [action], **Then** [expected outcome]`，我們要換成帶標籤的 Gherkin。preset 放在專案外（`/tmp/lab/aibdd/ch17-presets/presets/booking-bdd/`），因為它要給很多 repo 用。`preset.yml`：

```yaml
schema_version: "1.0"
preset:
  id: "booking-bdd"
  name: "Booking BDD"
  version: "0.1.0"
  description: "Gherkin-shaped acceptance scenarios and a Skill Parameters section"
  author: "ai_bdd course"
  repository: "https://example.invalid/booking-bdd"
  license: "MIT"
requires:
  speckit_version: ">=1.2.0"
provides:
  templates:
    - type: "template"
      name: "spec-template"
      file: "templates/spec-template.md"
      description: "Acceptance scenarios as tagged Gherkin"
      replaces: "spec-template"
    - type: "template"
      name: "constitution-template"
      file: "templates/constitution-params.md"
      description: "Append a Skill Parameters section to the core constitution"
      strategy: "append"
tags:
  - "bdd"
  - "gherkin"
```

兩項各用一種組合策略（composition strategy）。constitution-template 用 `append`：`templates/constitution-params.md` 的內容就是上面 constitution 的 `## Skill Parameters` 那節，只留 `acceptance_format: gherkin` 一行，接在核心範本後面。這樣 `/speckit-constitution` 拿到的「必須遵守的結構」裡就有參數節（第六節會看到它被保留）。spec-template 用預設的 `replace`，因為要改的是每個 story 中間那段，只能整份換。代價是你擁有一份核心模板的分叉：Spec Kit 改了核心模板，你得自己 `diff -u` 新舊核心、把差異帶進來。`templates/spec-template.md` 是核心模板的複本，三處 `**Acceptance Scenarios**:` 連同底下的編號清單換成這段：

~~~markdown
**Acceptance Scenarios** *(Gherkin; tag each scenario `@sc_<feature>_<NN>`)*:

```gherkin
Rule: [R# the business rule this story protects]

  @sc_[feature]_01
  Scenario: [one concrete example]
    Given [a state, not an action]
    When [the single action]
    Then [an observable outcome]
```
~~~

用 `` grep -c '^```gherkin' `` 數這份模板得 3。在 `booking/` 安裝並確認解析結果（`specify preset resolve spec-template` 則指向 preset 裡那份，`(top layer from: booking-bdd v0.1.0)`）：

```text
$ specify preset add --dev ../presets/booking-bdd
Installing preset from /private/tmp/lab/aibdd/ch17-presets/presets/booking-bdd...
✓ Preset 'Booking BDD' v0.1.0 installed (priority 10)
$ specify preset resolve constitution-template
…（略）
  Composition chain:
    1. [base] core → /private/tmp/lab/aibdd/ch17-presets/booking/.specify/templates/constitution-template.md
    2. [append] booking-bdd v0.1.0 → /private/tmp/lab/aibdd/ch17-presets/booking/.specify/presets/booking-bdd/templates/constitution-params.md
```

專案層覆蓋（project-local override）蓋在 preset 上面：放一份 `.specify/templates/overrides/spec-template.md`，`resolve` 立刻改指 `(top layer from: project override)`；刪掉就回到 `booking-bdd v0.1.0`。SDD Ch 29 修正前的範例建了 `.specify/presets/my-company` 卻 `add ./my-company-preset`，也沒有清單檔（manifest，即 `preset.yml`）。照那樣做、或少了清單、或把 `append` 寫成 `merge`，三種真實錯誤分別是 `Error: Directory not found: ./my-company-preset`、`Validation Error: Manifest not found: …/no-manifest/preset.yml`、`Validation Error: Invalid strategy 'merge': must be one of ['append', 'prepend', 'replace', 'wrap']`，都在安裝時就以 exit 1 擋下。

## 底層機制：一份模板怎麼疊出來

解析寫在 `.specify/scripts/bash/common.sh` 的 `resolve_template_content`；官方 ARCHITECTURE.md 說同一套規則實作了三次（Python 的 `PresetResolver`，也就是 `specify preset resolve`；bash；PowerShell）。

```
resolve_template_content("spec-template")
 1. .specify/templates/overrides/spec-template.md     存在 → 直接輸出（永遠是 replace）
 2. .specify/presets/<id>/   依 .specify/presets/.registry 的 priority 由小到大（預設 10，同分比 id）
      讀 preset.yml 的 strategy（要 Python 3 + PyYAML）
      replace → 這層就是底，停止往下找；prepend / append / wrap → 記下來，繼續找底
 3. .specify/extensions/<id>/templates/               （永遠是 replace）
 4. .specify/templates/spec-template.md               核心
 → 從底往上套用記下的層：prepend 放前、append 放後、wrap 把底塞進 {CORE_TEMPLATE}
```

由這張圖推出、也都實測過的性質：

- **模板執行時解析，安裝只是複製。** `preset add --dev` 把目錄複製進 `.specify/presets/booking-bdd/`。在來源目錄加一行註解後，解析結果裡 `grep -c` 得 0；跑 `specify preset update booking-bdd --dev ../presets/booking-bdd` 之後才是 1。
- **command override 在安裝時寫死。** preset 的 `type: "command"` 會被渲染進 `.claude/skills/speckit-*/SKILL.md`（README：「command overrides are applied at install time」）。移除 preset 時內容會還原，但我們實測時 frontmatter 的引號格式不同、還多了一行 `# Speckit Specify Skill`，`integration status` 仍報一個被改過的檔，再 `upgrade --force` 一次才逐位元組回到原樣。
- **升級靠雜湊辨識你的修改。** `.specify/integrations/claude.manifest.json` 與 `speckit.manifest.json` 記著每個受管檔案的 sha256。`constitution.md` 不在任何一份清單裡，它屬於專案（0.0.91 起 CHANGELOG：「preserve constitution.md during reinitialization」）。
- **有了 preset，bash 解析器就需要 PyYAML。** 這台機器 PATH 上的 `python3` 是系統的 3.9.6，沒有 yaml：

```text
$ env PATH=/usr/bin:/bin:/usr/sbin:/sbin bash -c 'python3 --version; .specify/scripts/bash/resolve-template.sh spec-template > /dev/null; echo "exit=$?"'
Python 3.9.6
Error: PyYAML is required to resolve preset template composition
ERROR: Could not resolve required spec-template from the template override stack for /tmp/lab/aibdd/ch17-presets/booking
exit=1
```

裝 preset 之前同一個指令會成功，所以這個錯在「裝了 preset 之後」才冒出來。前面加上 `SPECKIT_PYTHON_EXECUTABLE=/tmp/lab/aibdd/.venv/bin/python`（`common.sh` 認的正式覆寫變數；`SPECKIT_PYTHON` 是舊名）就印出 3 個 gherkin 區塊、exit 0。我們自己的腳本也一樣：用 `/usr/bin/python3` 跑 `constitution_params.py` 得到 `ModuleNotFoundError: No module named 'yaml'`。[Ch 0](./00-environment-setup.md) 發現這台機器的使用者設定會覆寫 Claude Code 的 `PATH`，所以本章的 `claude -p` 都先 activate venv、加 `--setting-sources project`。

## 四、改 /specify 的流程：三條路實測

**路一：直接改安裝好的 skill。** preset 只改形狀，要改「怎麼寫 spec」得動到 skill。在 `booking/` 的副本裡往 SKILL.md 尾巴加一行規則，`specify integration status` 立刻變成 `Integration status: WARNING`、`Modified managed files: 1`，接著升級：

```text
$ printf '\nHouse rule: write every acceptance scenario as a tagged Gherkin block.\n' >> .claude/skills/speckit-specify/SKILL.md
$ specify integration upgrade; echo "exit=$?"
⚠  1 file(s) have been modified since installation:
    .claude/skills/speckit-specify/SKILL.md

Use --force to overwrite modified files, or resolve manually.
exit=1
$ specify integration upgrade --force | tail -1; grep -c "House rule" .claude/skills/speckit-specify/SKILL.md
✓ Integration 'Claude Code' upgraded successfully
0
```

一般的 upgrade 會擋，`--force` 之後那行規則消失。再加一次、改跑 `specify init --here --force --non-interactive --integration claude --script sh`，結果一樣是 0，只有一句通用的「may overwrite existing files」。（我們沒有換版本，是用同一個 1.2.5 重裝，測的是檔案保護機制；跨版本升級請以你的環境實測為準。）

**路二：preset 的 command override。** 同一條規則改包成 preset `specify-house-rules`：`preset.yml` 宣告一項 `type: "command"`、`name: "speckit.specify"`、`strategy: "wrap"`，`commands/speckit.specify.md` 第一行是 `{CORE_TEMPLATE}`，後面接一節 `## House Rules`。在另一份副本實測：安裝後 SKILL.md 尾巴出現 House Rules；`integration status` 報 `Modified managed files: 1`，一般 upgrade 因此 exit 1；`upgrade --force` 之後規則還在、status 歸零；再 `init --here --force`，規則仍在，但 status 又回到 `Modified managed files: 1`，一般 upgrade 再度 exit 1。它活得過升級，代價是每次安裝、每次 `init --here --force` 之後都要再 `upgrade --force` 一次讓雜湊同步（1.0.12 的 CHANGELOG 修過「sync integration manifest hashes after preset re-registration on upgrade」）。用 `wrap` 只能**加**規則：stock 流程裡的當場提問、由 agent 自行判斷怎麼解析模板，這些都改不掉。把 `strategy` 換成 `replace` 可以整份換掉（實測裝完 SKILL.md 只剩 frontmatter、一行標題和我們寫的內文），但那等於分叉一份 stock 指令，和 spec-template 的 `replace` 一樣得自己追核心改版；既然要自己寫流程，路三更乾淨。

**路三：自己的 `sdd-specify`。** 不碰 Spec Kit 的檔案，寫一個新 skill，重用 Spec Kit 的管線：`create-new-feature.sh` 會用 `resolve_template_content` 解析 spec-template、建目錄、寫 `.specify/feature.json` 給下游的 `/speckit-plan`。流程自己定，完成條件可以檢查（[pstack Ch 28](../pstack/28-skill-authoring.md) 的寫法）：

```markdown
---
name: sdd-specify
description: Write a feature spec (specs/NNN-name/spec.md) in this repo's house format. Reads the sdd-specify parameters from the constitution, uses the glossary, and is done only when spec_check passes. Use it instead of /speckit-specify in this repo.
argument-hint: "<feature description>"
---

# sdd-specify

Feature description: $ARGUMENTS
If it is empty, stop and ask for one.

## 1. Parameters
Run `python3 tools/constitution_params.py get sdd-specify`. It prints JSON with
acceptance_format, glossary, max_clarifications and scenario_tag. If it exits non-zero,
stop and show its message: the constitution has to be fixed first.

## 2. Vocabulary
If `glossary` is not null, read that file. Use its terms; never write a word it lists under `avoid`.

## 3. Scaffold
Run `.specify/scripts/bash/create-new-feature.sh --json --short-name "<2-4 words>" "<description>"`.
It creates the feature directory, copies the resolved spec-template (presets included) to
SPEC_FILE and records `.specify/feature.json`. Work on the SPEC_FILE it prints.

## 4. Write
Fill SPEC_FILE and keep the template's headings in their order.
- WHAT and WHY only: no stack, endpoints or code structure.
- Acceptance scenarios in `acceptance_format`. For gherkin: one gherkin block per user story;
  `Rule:` names the business rule ID; the line above each Scenario holds a tag matching
  `scenario_tag`; Given is a state, When is one action, Then is an observable outcome.
- At most `max_clarifications` `[NEEDS CLARIFICATION: ...]` markers. Do not ask the user now;
  open questions go to the clarify step.

## 5. Gate
Run `python3 tools/constitution_params.py get sdd-specify | python3 .claude/skills/sdd-specify/scripts/spec_check.py SPEC_FILE --params -`.
Fix every line it reports and run it again. After 3 failing runs, stop and report what is left.

## Done when
- spec_check prints `1 file, 0 problems`.
- Your reply names SPEC_FILE, the parameters that came from the constitution, and any open markers.
```

閘門 `.claude/skills/sdd-specify/scripts/spec_check.py` 從 stdin 吃 `get` 的 JSON，所以換一個專案、換一組參數照樣能用：

```python
"""Check a spec.md against sdd-specify's effective parameters.

  SPEC-1  every user story has an acceptance scenario in acceptance_format
  SPEC-2  every Gherkin scenario has a tag matching scenario_tag on the line above
  SPEC-3  at most max_clarifications [NEEDS CLARIFICATION] markers
  SPEC-4  no word the glossary lists under 'avoid' (the quoted **Input** line is exempt)

usage: constitution_params.py get sdd-specify | spec_check.py SPEC --params -
"""
import argparse, json, re, sys
from pathlib import Path
import yaml

SCENARIO = r"^\s*(?:Scenario|Scenario Outline|Example):"
FORMATS = {
    "gherkin": r"```gherkin\n(?:.*\n)*?" + SCENARIO[1:] + r"(?:.*\n)*?\s*Given (?:.*\n)*?\s*When (?:.*\n)*?\s*Then ",
    "given-when-then": r"\*\*Given\*\*.*\*\*When\*\*.*\*\*Then\*\*",
    "ears": r"\b(?:When|While|If|Where)\b[^.\n]*\bshall\b",
}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("spec", type=Path)
ap.add_argument("--params", required=True, help="JSON from constitution_params.py get; '-' = stdin")
args = ap.parse_args()
prm = json.load(sys.stdin if args.params == "-" else open(args.params))
lines = args.spec.read_text(encoding="utf-8").splitlines()
problems = []

starts = [i for i, l in enumerate(lines) if l.startswith("### User Story")]
for a, b in zip(starts, starts[1:] + [len(lines)]):
    body = "\n".join(lines[a:b]).split("\n## ")[0] + "\n"
    if not re.search(FORMATS[prm["acceptance_format"]], body, re.M):
        story = re.match(r"### (User Story \S+)", lines[a])[1]
        problems.append((a + 1, f"SPEC-1 {story} has no {prm['acceptance_format']} acceptance scenario"))
for i, l in enumerate(lines):
    if prm["acceptance_format"] == "gherkin" and re.match(SCENARIO, l):
        if not any(re.fullmatch(prm["scenario_tag"], t) for t in lines[i - 1].split()):
            problems.append((i + 1, f"SPEC-2 scenario has no tag matching {prm['scenario_tag']}"))
marks = [i for i, l in enumerate(lines) if "[NEEDS CLARIFICATION" in l]
if len(marks) > prm["max_clarifications"]:
    problems.append((marks[-1] + 1, f"SPEC-3 {len(marks)} clarification markers > {prm['max_clarifications']}"))
if prm["glossary"]:
    terms = yaml.safe_load(Path(prm["glossary"]).read_text(encoding="utf-8"))["terms"]
    for term, t in terms.items():
        for word in t.get("avoid", []):
            for i, l in enumerate(lines):
                if not l.startswith("**Input**") and re.search(rf"\b{re.escape(word)}\b", l, re.I):
                    problems.append((i + 1, f"SPEC-4 '{word}' -> use '{term}'"))
for n, msg in sorted(problems):
    print(f"{args.spec}:{n}: {msg}")
print(f"1 file, {len(problems)} problems")
sys.exit(1 if problems else 0)
```

SPEC-4 豁免 `**Input**` 那行，因為那是使用者原話的引用。`node /tmp/lab/tools/skill-lint.mjs .claude/skills` 印出 `11 skills, 0 problems`（十個 speckit 加上 `sdd-specify`）。

## 五、同一句需求，各跑一次

對照組 `booking-stock/` 是另一個全新的 `specify init`：沒有 preset，constitution 是上面那份 1.1.0 去掉 `## Skill Parameters`（Governance 的 MAJOR、MINOR 兩行刪掉參數的部分，提到 `tools/constitution_params.py` 那行也刪掉），`docs/glossary.yaml` 一樣在。這是一個認真寫了 constitution、但只靠散文的團隊。實驗組是 `booking/`（1.1.0、preset、`sdd-specify`）。兩邊用同一段需求（`../description.txt`）：「Let employees book a meeting room (Taipei, Tokyo or Seoul) for a time range on one day. Two bookings of the same room must not overlap; back-to-back bookings such as 10:00-11:00 and 11:00-12:00 are fine. Reject a booking whose end is not after its start. When a booking is rejected because of an overlap, tell the person who holds the room and when.」

```bash
cd /tmp/lab/aibdd/ch17-presets/booking-stock && source /tmp/lab/aibdd/.venv/bin/activate
claude -p "/speckit-specify $(cat ../description.txt)" --max-turns 20 --max-budget-usd 5 \
  --allowedTools "Read,Write,Edit,Glob,Grep,Bash(mkdir *),Bash(ls *),Bash(date *),Bash(.specify/scripts/bash/*)" \
  --permission-prompts none --setting-sources project --strict-mcp-config \
  --no-session-persistence --output-format stream-json --verbose > ../run1-stock.jsonl
```

實測 2 在 `booking/` 跑同一條指令，只換兩處：`/sdd-specify`，以及 `--allowedTools "Read,Write,Edit,Glob,Grep,Bash(ls *),Bash(python3 tools/constitution_params.py *),Bash(python3 .claude/skills/sdd-specify/scripts/spec_check.py *),Bash(.specify/scripts/bash/create-new-feature.sh *)"`。`claude -p` 把開頭的 `/skill 名` 當成載入那個 skill（官方文件：「loads only the first skill, with the rest of the line as its arguments」）；`--permission-prompts none` 讓清單外、需要核准的動作一律被拒（`git status` 這類唯讀指令本來就不必核准）；管線指令能跑，是因為 `|` 兩邊各自符合一條 allow 規則。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。實測 1：9 個回合、約 96 秒，CLI 估計 0.46 美元。實測 2：12 個回合、約 107 秒，0.44 美元。

實測 1 先讀 constitution，接著說「The constitution makes `docs/glossary.yaml` binding, so I'll read that before writing anything.」，寫完還自己 grep 一遍 `avoid` 清單。它找模板的方式是 `ls .specify/presets .specify/overrides 2>&1; which specify`：PATH 上沒有 `specify`，真正的覆蓋目錄則是 `.specify/templates/overrides/`。這個專案剛好沒有 preset 和覆蓋，它讀核心模板，結果是對的。實測 2 的工具呼叫照 SKILL.md 的五步走，中間多讀了 `spec_check.py` 的原始碼，也自己讀了整份 constitution 去找規則編號（參數層限制的是腳本交給它什麼，不是它能讀什麼），第一次跑閘門就是 `1 file, 0 problems`。同一個行為（重疊時說出誰訂了），實測 1 寫成一條「1. **Given** Ada holds Tokyo 10:00-11:00, **When** Ben books Tokyo 10:30-11:30, **Then** the booking is rejected and Ben is told that Ada holds Tokyo 10:00-11:00. (R2, R3)」，實測 2 寫成：

```gherkin
Rule: R3 A rejection for a conflict names who holds the room and when

  @sc_booking_08
  Scenario: The rejection names the holder and the held range
    Given Ana holds Taipei from 10:00 to 11:00 on 2026-11-10
    When Bo books Taipei from 10:30 to 11:30 on 2026-11-10
    Then the booking is rejected as a conflict
    And Bo is told that Ana holds Taipei from 10:00 to 11:00 on 2026-11-10
```

用實驗組的參數檢查對照組（在 `booking/` 跑 `get sdd-specify | spec_check.py ../booking-stock/specs/001-room-booking/spec.md --params -`）得到三行 `SPEC-1 User Story N has no gherkin acceptance scenario`（第 15、33、54 行）與 `1 file, 3 problems`：

| | 實測 1：stock + 散文 constitution | 實測 2：preset + 參數 + `sdd-specify` |
|---|---|---|
| 行數、user story | 131 行、3 個 | 212 行、3 個 |
| 驗收 | 19 條編號 Given/When/Then | 12 個帶 `@sc_booking_NN` 的 Gherkin 情境（4 個 Outline） |
| 詞彙表違規、待釐清標記 | 0、0 | 0、2（上限 2） |
| spec_check | 3 problems（全是形狀） | 0 problems |
| 規則編號 | R1 = 輸入合法、R2 = 不衝突 | R1 = 不衝突、R2 = 輸入合法 |

對照組比預期好：散文 constitution 讓它讀了詞彙表、引用了規則編號。差在形狀與確定性：沒有人要求它寫 Gherkin；詞彙表有遵守是「這一次」，實驗組則由閘門判定。最後一列是兩邊都踩到的雷：constitution 寫了「Stable Rule IDs (R1..R4)」，卻沒有任何檔案定義 R1 到 R3，兩次執行各自發明了一套相反的編號，實測 2 還把它列成待釐清標記。實測 2 的三段 Gherkin 前面加一行 `Feature:` 之後都能被 gherkin-official 42.0.1 解析（3 個 Rule、12 個情境），[Ch 31](./31-bdd-in-sdd.md) 會把它們變成 `.feature` 檔。

## 六、讓 stock skill 修憲，讓腳本收尾

參數層最怕 stock 的 `/speckit-constitution`：它照「解析出來的 constitution-template」重寫整份檔案。preset 用 `append` 把參數節放進了那份範本，理論上會被保留。實測 3 在 `booking-preset/`（實測 2 之前用 `cp -R booking booking-preset` 留下的副本）執行，旗標同上，prompt 是 `/speckit-constitution Add principle V. Rejections Name Their Rule: every rejection message cites the business rule ID it enforces. Also lower sdd-specify's max_clarifications from 2 to 1.`，`--allowedTools "Read,Write,Edit,Glob,Grep,Bash(ls *),Bash(.specify/scripts/bash/resolve-template.sh *)"`。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。11 個回合、約 35 秒，CLI 估計 0.26 美元。

它第一個指令就跑了 `resolve-template.sh constitution-template --json`，拿到的範本結尾正是 preset 接上的 `## Skill Parameters`。它讀了 `tools/constitution_params.py`，想用一段 Python 一次改完被權限擋下，改用四次 Edit。結果：新增原則 V、`max_clarifications: 1`、版本 1.1.0 → 2.0.0，參數節原樣保留。它的解釋是「A new principle on its own would be a minor bump. Governance makes any Skill Parameters value change major, and `constitution_params.py` requires it, so 1.2.0 would fail `check --against`.」它也照 stock skill 的規定，在檔案最上面留了一段 Sync Impact Report（同步影響報告，給審查者看的 HTML 註解）。收尾交給腳本：`check --against HEAD` 回 `Sync Impact Report comment still present; remove it before committing`、exit 1；刪掉那段註解之後是 `1 file, 0 problems`。最後驗證「改值為什麼是 MAJOR」：用 2.0.0 的參數檢查實測 2 那份在 1.1.0 下通過的 spec，得到 `spec.md:212: SPEC-3 2 clarification markers > 1`。

## 對比與取捨

| 做法 | 能改什麼 | `integration upgrade` | `upgrade --force`、`init --here --force` | 適用情境 |
|---|---|---|---|---|
| 手改 `.claude/skills/speckit-*/SKILL.md` | 流程 | 擋下，exit 1 | 改動消失 | 短暫實驗 |
| 手改核心 `.specify/templates/*.md` | 形狀 | 保留，但共用檔一個都不更新 | 改動消失 | 不建議 |
| 專案層覆蓋 `templates/overrides/` | 形狀（整份取代） | 保留 | 保留 | 單一 repo 的小改 |
| preset 模板 | 形狀（四種策略、可疊） | 保留 | 保留 | 多個 repo 共用格式 |
| preset command override | 在 stock 流程外加規則（`replace` 則整份換） | 擋下，直到 `--force` 同步 | 重新套用；init 之後要再 `--force` | 對 stock skill 補幾條規則 |
| 自己的 skill（`sdd-specify`） | 流程、參數、閘門 | 不受管 | 不受管 | 要讀參數、要有完成條件 |

升級兩欄是用同一個 1.2.5 重裝的實測結果。自己的 skill 也有成本：stock specify 會產生 `checklists/requirements.md`、會跑 extension hook，我們的版本都沒有；Spec Kit 改了 specify 的流程，你得自己決定要不要跟。本章的組合是 preset 管形狀、constitution 管參數、自己的 skill 管流程與閘門，各管一件事。

## 踩雷集錦

1. **錯誤直覺：直接改 `.claude/skills/speckit-specify/SKILL.md` 最快。** 正確認識：一般的 `integration upgrade` 會擋（exit 1），`--force` 與 `init --here --force` 把改動整個蓋掉（實測 `grep -c` 得 0）。要加規則用 preset 的 command override，要換流程寫自己的 skill。
2. **錯誤直覺：constitution 寫了「Stable Rule IDs」，編號就會穩定。** 正確認識：原則提到 ID 卻沒有定義，兩次實跑給了相反的 R1、R2。ID 必須住在一份 truth 檔（[Ch 33](./33-truth-vs-plan.md) 的 truth，例如 feature 檔裡的 `Rule: R1 …`；[Ch 8](./08-translation-sandwich.md) 的 example map 是 ID 第一次被寫下的地方，但 Ch 33 把它算作那次 discovery 的 plan），constitution 只指過去；更好的是讓它成為參數（例如 `rules: docs/rules.yaml`），由閘門檢查 spec 引用的 ID 都存在。
3. **錯誤直覺：preset 裝好以後，改來源目錄就會生效。** 正確認識：`preset add --dev` 是複製。改完來源要 `specify preset update <id> --dev <dir>`，實測解析結果從 0 變 1。
4. **錯誤直覺：裝了 preset，其他照舊。** 正確認識：bash 解析器開始需要 Python 3 + PyYAML，這台機器的系統 `python3`（3.9.6）沒有 yaml，`resolve-template.sh` 與我們的腳本都會失敗；agent 的 Bash 只要吃到使用者設定的 `PATH` 就會踩到。用 venv、`SPECKIT_PYTHON_EXECUTABLE`，或在 SKILL.md 寫絕對路徑的直譯器。
5. **錯誤直覺：閘門過了，spec 就是對的。** 正確認識：`spec_check.py` 只檢查形狀、數量和用詞。實測 2 的 agent 動筆前先讀了閘門的原始碼，它寫的是「會過這支檢查」的 spec。規則編號對不對、情境有沒有涵蓋規則，仍要靠 [Ch 18](./18-clarify-skill.md) 的訪談與人審；[Ch 1](./01-course-map.md) 踩雷第 5 條是同一件事。

## 進階：疊兩個 preset

preset 可以疊，`priority` 小的優先。另做一個只 `append` 一節「Personal Data」的 `compliance-footer`，以 `--priority 5` 裝在 `booking-bdd`（10）上面：`preset list` 依序列出 priority 5、10，`preset resolve spec-template` 印出 `1. [base] booking-bdd v0.1.0` 與 `2. [append] compliance-footer v0.1.0` 的組合鏈，解析結果裡 `## Assumptions` 在第 143 行、`## Personal Data` 在第 157 行。再跑 `specify preset set-priority compliance-footer 20`，footer 掉到 `booking-bdd` 下面，`grep -c "Personal Data"` 變成 0：上面那層是 `replace`，它就是底，壓在底下的 `append` 不參與組合。疊 preset 時，先想清楚誰是底。另外兩個延伸方向：官方有一個選用的 `constitution-sync` preset（0.15.1 起）：只有 `.specify/memory/.constitution-template.json` 的來源雜湊證明 constitution 還是生成的原樣時，才隨 preset 變動重新產生它，手寫過的不覆蓋（ARCHITECTURE.md 的 Constitution lifecycle；本章未實測）。另外，SPEC-1 用 regex 判斷「像不像 Gherkin」；嚴格的做法是像 [Ch 28](./28-gherkin-dsl-style.md) 的 `gherkin_lint.py` 一樣交給 gherkin-official 的 parser，第五節那三段就是這樣驗的。

## 動手練習

1. 把 `### sdd-specify` 的 `acceptance_format` 改成 `ears`。不看本章，先猜 `check --against HEAD` 要求哪一級再跑；接著用新參數檢查實測 2 的 spec，解釋為什麼三個 user story 都報 SPEC-1。
2. 為 [Ch 18](./18-clarify-skill.md) 的 `clarify-interview` 先寫 `params.yaml`：`max_questions` 預設 5（`speckit-clarify` 1.2.5 寫著「Maximum of 5 total questions across the whole session」）。在 constitution 加一節，讓 `check` 告訴你升哪一級；再把節名故意打成 `clarify-interveiw`，看 `check` 怎麼說。
3. 把 SDD Ch 29 練習三那節 Definition of Done 直接加進核心 `.specify/templates/spec-template.md`（那個練習現在改走專案層覆蓋，這裡故意走它原本的路），跑 `specify integration upgrade` 與 `--force`，對照上面那張表的第二列；再用一個 `append` 的 preset 做同一件事，重跑一次。

## 本章重點整理

- 1.2.5 的十個 speckit skill 都提到 `.specify/memory/constitution.md`：九個讀、一個寫，而且都整份讀；「注入」其實是 SKILL.md 裡請 agent 去 Read 的一句話。
- 參數層：`## Skill Parameters` 底下每個 skill 一節 yaml，skill 自帶 `params.yaml` 預設值；缺節用預設、打錯字擋下；改值 MAJOR、加值 MINOR，`constitution_params.py check --against` 把關。
- 形狀交給 preset：模板執行時解析（overrides > presets > extensions > core），`replace` 是底，`prepend`、`append`、`wrap` 往上疊；有了 preset，bash 解析器需要 PyYAML。
- 流程交給自己的 skill：`sdd-specify` 讀參數、查詞彙表、用 `create-new-feature.sh` 建檔、以 `spec_check.py` 為完成條件。直接改安裝好的 skill 撐不過 `--force`。
- 閘門只證明形狀；規則編號這類語意問題，要靠 truth 檔與人審。

## 自我檢核

- [ ] 不看本章，說出 1.2.5 的 stock skill 讀 constitution 的三種方式（例如當 gate、當 CRITICAL 依據），各舉一個 skill；唯一寫它的是哪一個？
- [ ] 為什麼「缺節」回 exit 0、「多一個不認得的 key」回 exit 1？反過來設計會出什麼事？
- [ ] 解釋為什麼 `append` 的 preset 排在 `replace` 的 preset 下面時完全不起作用。
- [ ] 你的團隊想讓 `/speckit-specify` 多遵守一條規則，你選路一、路二還是路三？放棄的那兩條各輸在哪個升級情境？
- [ ] 實測 1 沒有 Gherkin、實測 2 有。哪些差異來自模板、哪些來自閘門？要分開兩者，你會怎麼設計第三次實跑？

## 延伸閱讀

- **[Spec Kit presets README](https://github.com/github/spec-kit/blob/v1.2.5/presets/README.md)** 與 **[ARCHITECTURE.md](https://github.com/github/spec-kit/blob/v1.2.5/presets/ARCHITECTURE.md)**（v1.2.5）
  - **讀哪裡、學到什麼**：README 的「How It Works」「Composition Strategies」講解析堆疊與四種策略，「Command Overrides」講 command 為什麼是安裝時寫入；ARCHITECTURE 的「Template Resolution」「Constitution lifecycle」講三份實作與 constitution 只在 init 時種一次。
  - **和本章的關聯**：「底層機制」那張圖是這兩份文件加上 `common.sh` 原始碼的濃縮；preset 的欄位以 `presets/scaffold/preset.yml` 為準。
- **[spec-driven.md](https://github.com/github/spec-kit/blob/v1.2.5/spec-driven.md)**（Spec Kit 的設計理念文件）
  - **讀哪裡、學到什麼**：「The Constitutional Foundation」到「Constitutional Evolution」，九條 article 的原意、IV 到 VI 留給專案自訂的設計，以及修憲流程的官方說法。
  - **和本章的關聯**：它把 plan-template 的關卡稱為「compile-time checks for architectural principles」，並說「The LLM cannot proceed without either passing the gates…」。本章的立場是關卡要有腳本才算數；讀原文，判斷你同不同意。
- **[Claude Code — Skills](https://code.claude.com/docs/en/skills)**（官方文件）
  - **讀哪裡、學到什麼**：frontmatter 參考表、`$ARGUMENTS` 的替換規則、skill 放置位置與同名時誰優先（個人 > 專案），以及 `claude -p` 只載入第一個 skill。
  - **和本章的關聯**：`sdd-specify` 與 `speckit-specify` 都能被模型自動叫用，description 決定它挑哪一個；想只留你的版本，看 `disable-model-invocation` 與 `user-invocable` 的差別。

參數層給每個自訂 skill 一個讀設定的地方，但 spec 裡最後那兩個待釐清標記還沒有人問。下一章把「問」做成 skill：一次一題、有上限、答案寫回 spec；那個上限也能照本章的約定放進 constitution 的一節（動手練習 2）。

→ [Ch 18 clarify 訪談 skill：一次一題、有上限、寫回 spec](./18-clarify-skill.md)
