# Ch 5 — 用 skill-creator 做出第一個 skill

> **目標**：會載入與叫用 Anthropic 的 skill-creator，看懂它的工作迴圈（訪談 → 草稿 → 測試 prompt → 帶 skill／不帶 skill 對照 → 評分 → benchmark → 調 description → 打包）與它附帶的 scripts、agents；用它做出本課的 `gherkin-scenario-writer`，親手跑一次真的對照評測與一次觸發率評測，並在過程中抓到這套工具自己的兩個坑（聚合 script 讀不懂 SKILL.md 教的版型；觸發 eval 在 Claude Code 2.1.295 上量不到觸發）；最後用三種 validator 檢查、打包，並和手寫 skill、pstack 的 skill-lint 比較。

> **環境**：Claude Code 2.1.295（預設模型 claude-opus-5-5）、skill-creator plugin（`claude-plugins-official` marketplace，commit a7ba5b93）、macOS 26.6 arm64、Python 3.12.15 + PyYAML 6.0.3（`source /tmp/lab/aibdd/.venv/bin/activate`）、Node v26.11.0、jq 1.7.1。實驗目錄 `/tmp/lab/aibdd/ch05-skill-creator/`，所有實測於 2026-11-02 執行。

## 為什麼需要這個？

[Ch 4](./04-prompt-files-to-skills.md) 手寫了三個 skill，量出它們在 context 裡的價格，卻沒回答兩個更重要的問題：**用了它，產出有沒有比較好？該觸發的時候它會不會被叫起來？** 這個 repo 至今對 skill 的檢查都停在靜態層：[pstack Ch 28](../pstack/28-skill-authoring.md) 的 skill-lint 查 frontmatter 與連結；[ai_applications Ch 15](../ai_applications/15-skills.md) 的「測試 Skill」是手動叫一次，再各問一句該觸發與不該觸發的話，看它的反應；[pstack Ch 29](../pstack/29-eval-playbook.md) 介紹了 `claude plugin eval`，但沒有實跑。在 skill-creator 之前，多數人寫完 skill 就在對話裡試一次「看起來可以」。一次是一個樣本，而且是作者自己、帶著滿腦子 context 試的。skill-creator 把「寫 skill」變成有對照組（baseline）的實驗：同一批 prompt，帶 skill 與不帶 skill 各跑一次、獨立評分、聚合成數字；觸發與否另外用一組「該觸發／不該觸發」的 query 量。本章把這個迴圈從草稿到打包實際跑一遍（只有 description 的自動最佳化沒跑），數字全部是真的，連工具自己的坑也是。

## 先建立直覺：試菜廚房

```
  寫食譜（SKILL.md 草稿）
      │
      ├─► 試菜：兩位不認識彼此的廚師各做一次，A 拿到食譜（with_skill），B 沒拿到（without_skill）
      │        評審照清單打分（grading.json）→ 成績單（benchmark.md）：好多少？多花多少時間與材料？
      │
      ├─► 點菜測試：客人用各種說法點菜，服務生會不會推薦這道（觸發 eval）
      │
      └─► 改食譜，換一批客人再試（iteration-2/ …）；滿意了，裝盒出貨（.skill）
```

兩個測試量的是**不同的東西**：試菜量 body 好不好（食譜內容），點菜測試量 description 會不會把菜推出去（菜單上那一行字）。比喻在一點上會騙人：真的廚房試菜，評審是人；skill-creator 的評審可以是 grader agent、你寫的 script 或你本人，而分數取決於評審手上那張清單。第四節實測：同一批產出，換一張清單，帶 skill 的領先幅度就從 +0.07 變成 +0.20。

## 一、載入與叫用

官方文件給的安裝方式是在 Claude Code 裡打 `/plugin install skill-creator@claude-plugins-official`，或在 shell 用 `claude plugin install`；[Ch 0](./00-environment-setup.md) 已經在隔離的設定目錄實際裝過一次。本章不動你的 `~/.claude`，改用 `--plugin-dir` 只在單次 session 載入，並先把 plugin 複製進實驗目錄，讓檔案版本固定。第一行順便建好後面會寫入的目錄，第二行複製 Ch 4 那份只有 `@sc_book_01` 的 feature file：

```bash
mkdir -p /tmp/lab/aibdd/ch05-skill-creator/{runs,samples,shim,work/features,evalproj/.claude} && cd /tmp/lab/aibdd/ch05-skill-creator
cp /tmp/lab/aibdd/ch04-prompt-files/b-skills/features/booking.feature work/features/
cp -R ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator vendor-skill-creator
ls -m vendor-skill-creator/skills/skill-creator
```

```
LICENSE.txt, SKILL.md, agents, assets, eval-viewer, references, scripts
```

（marketplace 目錄在你第一次開互動式 session 時由 Claude Code 自動下載；沒有的話，從 GitHub 的 `anthropics/claude-plugins-official` 取 `plugins/skill-creator`。）叫用時明寫 plugin 前綴 `/skill-creator:skill-creator`：這台機器的 claude.ai 帳號另外同步了一份**較新的** `anthropic-skills:skill-creator`（`~/.claude/skills/synced/` 下，它把測試案例改存成 `claude plugin eval` 讀得懂的資料夾格式），兩份 description 一模一樣，不寫前綴就不知道會叫到哪一份。

## 二、拆開 skill-creator：一份 SKILL.md、一組 script、三個 agent

SKILL.md 本身 485 行，剛好在它自己建議的「500 行以內」。它描述的迴圈是：抓意圖（4 個問題：要做什麼、何時觸發、輸出格式、要不要測試）→ 訪談 → 寫 SKILL.md（description 要寫得「pushy」一點，因為 Claude 傾向少觸發）→ 2–3 個真實的測試 prompt 存進 `evals/evals.json` → **同一個回合**同時派出帶 skill 與不帶 skill 的 subagent（在獨立 context 裡跑的子任務，見 [harness Ch 26](../harness_engineering/26-subagents.md)）→ 起草斷言（assertion：對產出的一句可檢查陳述，例如「每個情境只有一個 When」）→ 評分、聚合成 benchmark（兩組的通過率、時間、token 對照表）、分析、開 viewer 給人看 → 依回饋改寫 → 下一輪。scripts 是這個迴圈裡確定性的部分，主要的七支如下（另有 `utils.py` 共用模組）：

| script | 做什麼 | 會呼叫 `claude -p` 嗎 |
|---|---|---|
| `quick_validate.py` | 檢查 frontmatter：必填 name/description、允許的欄位、kebab-case、長度、角括號 | 否 |
| `package_skill.py` | 先跑 quick_validate，再把資料夾打包成 `.skill`（zip），略過根目錄的 `evals/` | 否 |
| `aggregate_benchmark.py` | 讀每個 run 的 `grading.json` 與 `timing.json`，算 mean ± stddev 與差值 | 否 |
| `eval-viewer/generate_review.py` | 產生審閱頁面（Outputs、Benchmark 兩個分頁），可 `--static` 輸出 HTML | 否 |
| `run_eval.py` | 觸發 eval：每個 query 開一個 `claude -p`，看第一個工具呼叫是不是這個 skill | 是，每 query 每次一個 |
| `improve_description.py` | 把失敗的 query 交給模型，請它重寫 description（上限 1,024 字元） | 是 |
| `run_loop.py` | 60/40 分 train/test、每 query 跑 3 次、最多 5 輪改寫，用 test 分數挑最好的 description；過程交給第八支 `generate_report.py` 畫成 HTML 報告 | 是，大量 |

三個 agent 是放在 skill 資料夾 `agents/` 裡、給 subagent 讀的角色說明，不是 plugin 註冊的 subagent，所以 [Ch 0](./00-environment-setup.md) 的 `plugin details` 列出 0 個 Agents：`grader.md`（逐條判定 assertion，並**批評 assertion 本身**，太容易過的要指出來）、`comparator.md`（盲比兩份產出，不知道誰帶了 skill）、`analyzer.md`（揭盲後分析贏的原因）。盲測的理由與規則，[pstack Ch 29](../pstack/29-eval-playbook.md) 已經講過。

## 三、實測 1：讓 skill-creator 寫出 gherkin-scenario-writer

非互動模式沒有人回答訪談，所以把訪談的答案一次寫進 prompt。`prompt-run1.md`：

```markdown
/skill-creator:skill-creator Create a new skill named gherkin-scenario-writer at skills/gherkin-scenario-writer/SKILL.md (not under .claude/: a human reviews it and installs it later).

Treat this as the finished interview:
- What it does: turns a booking rule or example (a PM note, a ticket, a chat message) into Gherkin scenarios for our FastAPI meeting-room booking service, written into or appended to a .feature file.
- Domain: rooms Taipei, Tokyo, Seoul; people Alice, Bob, Carol; same-day HH:MM times; intervals are half-open [start, end), so 10:00-11:00 and 11:00-12:00 do not overlap. R1: a room cannot be booked twice for overlapping times (409 "Taipei is taken 10:00-11:00 by Bob"). R2: end must be after start (422 "end must be after start"). R4: only the person who booked can cancel (403 "only Bob can cancel this booking"); an unknown id gives 404 ("booking 7 not found").
- House conventions: English keywords and step text; exactly one ID tag per scenario, @sc_<feature>_<NN> such as @sc_book_03, continuing the numbering already in the file; the title starts with the rule ID ("Scenario: R1 overlapping booking is rejected"); one When per scenario; Then states what a user can observe; 3-5 steps; reuse these steps verbatim and invent a new step only when none fits:
    Given room "Taipei" is free all day
    Given Bob has booked "Taipei" from 10:00 to 11:00
    When Alice books "Taipei" from 10:30 to 11:30
    Then the booking is confirmed
    Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
- Trigger: requests for Gherkin, BDD or acceptance scenarios, or a .feature file, for the booking service. Not for step definitions, pytest unit tests or the implementation.
- Output: the scenarios (a gherkin code block or the edited .feature file), then one line per open question when the rule is ambiguous.
- features/booking.feature in this directory is the existing feature file.

This is a non-interactive run: nobody can answer questions, so do not ask; make reasonable assumptions and list them in your final message. You have no Bash.
Do: draft SKILL.md; write 3 realistic test prompts with expected_output and assertions to the skill's evals/evals.json; run them the way your instructions describe (with-skill and without-skill subagents launched in the same turn, outputs saved in the workspace next to the skill; the output to save is the .feature file); write timing.json from each task notification.
Skip: grading, the review viewer, benchmark aggregation, description optimization and packaging. We run those scripts ourselves afterwards.
```

skill 寫到 `skills/` 而不是 `.claude/skills/`，是因為 `.claude` 是受保護路徑（protected path）：官方權限文件寫明 allow 規則不會預先核准對它的寫入，非互動模式又沒有人能按核准，寫進去會被拒絕。讓人審過再複製進去，本來也就是對的流程。`work/features/booking.feature` 是第一節從 Ch 4 複製來的那份。執行腳本 `run1-skill-creator.sh`（本章的 `.sh` 與後面的 `shim/claude` 建好後都要先 `chmod +x`）：

```sh
#!/bin/sh
# run1-skill-creator.sh: skill-creator drafts gherkin-scenario-writer and runs its first with/without-skill comparison
set -u
LAB=/tmp/lab/aibdd/ch05-skill-creator
cd "$LAB/work"
claude -p "$(cat "$LAB/prompt-run1.md")" \
  --plugin-dir "$LAB/vendor-skill-creator" \
  --setting-sources project --strict-mcp-config --no-session-persistence \
  --permission-mode acceptEdits --allowedTools "Read,Write,Edit,Glob,Grep" --permission-prompts none \
  --max-turns 20 --output-format stream-json --verbose > "$LAB/runs/run1-skill-creator.jsonl"
echo "claude exit=$?"
```

`acceptEdits` 讓工作目錄內的檔案編輯自動核准（subagent 才寫得了產出），protected path 照樣不准；`Bash` 不在允許清單，一律被拒。用 `main_tools.jq` 只看主對話的工具呼叫：

```jq
# main_tools.jq: the main conversation's tool calls in a stream-json log (subagents left out)
select(.type == "assistant" and .parent_tool_use_id == null) | .message.content[] | select(.type == "tool_use")
| "\(.name) \(.input.description // .input.file_path // .input.pattern
    | sub("^.*/ch05-skill-creator/(vendor-skill-creator/skills/)?"; "") | .[0:58])"
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02，約 5.5 分鐘，CLI 估計 US$2.24）。

```
$ jq -r -f main_tools.jq runs/run1-skill-creator.jsonl | sed -E 's#(workspace/iteration-1)/.*#\1/…#' | uniq -c
   1 Glob **/*
   1 Read work/features/booking.feature
…（略：讀 references/schemas.md、3 次 Grep、1 次 Glob）
   1 Read skill-creator/scripts/aggregate_benchmark.py
   1 Read skill-creator/eval-viewer/generate_review.py
…（略）
   1 Write work/skills/gherkin-scenario-writer/SKILL.md
   1 Write work/skills/gherkin-scenario-writer/evals/evals.json
   1 Write work/skills/gherkin-scenario-writer/evals/files/booking.fe
  15 Write work/skills/gherkin-scenario-writer-workspace/iteration-1/…
   1 Agent Eval 1 with skill
   1 Agent Eval 1 without skill
…（略：Eval 2、Eval 3 各兩個；之後 6 次寫 timing.json，最後再 Glob、Read、Grep 各一次）
```

- **它先讀自己的 scripts，才動筆。** 用 Grep 掃過 scripts 之後它說：「The aggregator uses a `run-*` layer that the skill instructions don't mention.」接著細讀 `aggregate_benchmark.py`，於是把每個 run 放在 `eval-1-pm-note-back-to-back/with_skill/run-1/`，同時滿足 SKILL.md 要的描述性名稱與聚合 script 要的 `eval-*`、`run-*`。踩雷 2 會看到不照這樣放的下場。
- **`evals.json` 裡同一份清單寫了兩次**，一次叫 `assertions`、一次叫 `expectations`：SKILL.md 用前者，`references/schemas.md` 用後者，它兩邊都滿足。工具自己的文件互相矛盾，是它在最後的報告裡主動列出的。
- **六個 subagent 同一回合派出**，每個都只拿到「skill 路徑（或沒有）＋任務＋輸入檔的副本＋輸出位置」。帶 skill 的那一組是**直接讀 SKILL.md 照做**，不經過 Claude Code 的觸發機制；所以這裡量的是 body，觸發要另外量（第五節）。skill-creator 在報告裡自己提醒：baseline 其實讀得到 SKILL.md 與 `evals.json`，它只叫它們別讀、沒有檢查。我們查了 stream-json 裡 subagent 的工具呼叫：不帶 skill 的三個只讀、改了自己那份輸入副本，再寫一份 transcript，沒碰這兩個檔案。
- 主對話共有 15 則 assistant 訊息、45 次工具呼叫。subagent 在背景跑完時對話會接著走，所以這次有 4 個 result 事件，`num_turns` 依序是 40、2、2、5，剛好各是那一段主對話的工具呼叫數加 1（39、1、1、4），subagent 的工具呼叫沒算進去。40 大於 `--max-turns 20` 卻沒被截斷，可見兩者數的不是同一種東西；這是觀察，不是文件說的。產出的 SKILL.md 有 93 行，frontmatter 如下：

```
$ sed -n 2,4p work/skills/gherkin-scenario-writer/SKILL.md
name: gherkin-scenario-writer
description: Turns a booking rule or example (a PM note, ticket or chat message) into Gherkin scenarios for the FastAPI meeting-room booking service, written into or appended to a .feature file. Use this whenever someone asks for Gherkin, BDD or acceptance scenarios, or a .feature file, for the booking service, or wants a rule about room bookings, overlaps, times or cancellations captured as scenarios, even if they never say "Gherkin". Not for step definitions, pytest unit tests or the implementation.
---
```

body 依序是 Workflow、Domain、Conventions、Steps、When the rule is unclear、Output 六節。description 照它自己的建議寫得「pushy」（「even if they never say "Gherkin"」），結尾劃了界線。body 比 Ch 4 手寫的 scenario-conventions 多了三樣東西：一張 R1/R2/R4 對照表、**它自己提議的 R2/R4 step 寫法**（「so that separate requests converge on the same steps」）、規則不明時「寫能寫的、其餘列成 open question」的處理方式。它的三個測試 prompt 分別是 PM 的「背靠背預約」便條、一張取消權限的 ticket、一段夾帶未定規則（「不准佔用超過 2 小時」）的 Slack 訊息。

## 四、評分與 benchmark：「比較好」要變成數字

我們跳過 grader agent，用 script 評分。SKILL.md 自己就說：能用程式檢查的 assertion，寫 script 比用眼睛看可靠、也能跨輪重用。`scenario_rules.py` 把 Ch 4 的房規變成五條 assertion，並輸出 skill-creator 要的 `grading.json`：

```python
#!/usr/bin/env python3
"""scenario_rules.py FILE...: check Gherkin scenarios against the booking house rules.

Per scenario: one @sc_<feature>_<NN> tag, a title starting with R1-R4, one When (And/But after a When
count as another), 3-5 steps, house step wording. '-' reads stdin; code fences are skipped. --steps FILE
adds accepted step patterns (a regex per line); --grading OUT writes skill-creator's grading.json.
"""
import argparse, json, re, sys

HOUSE_STEPS = [re.compile(p + "$") for p in (
    r'Given room "\w+" is free all day',
    r'Given \w+ has booked "\w+" from \d\d:\d\d to \d\d:\d\d',
    r'When \w+ books "\w+" from \d\d:\d\d to \d\d:\d\d',
    r'Then the booking is confirmed',
    r'Then the booking is rejected because "\w+" is taken \d\d:\d\d-\d\d:\d\d by \w+')]
CHECKS = {"tag": "Every scenario has exactly one @sc_<feature>_<NN> tag",
          "title": "Every scenario title starts with a rule ID (R1-R4)",
          "one-when": "Every scenario has exactly one When",
          "step-count": "Every scenario has 3-5 steps",
          "wording": "Every step uses the house step wording"}


def scenarios(text):
    """Yield (tags, title, steps) per Scenario; steps are (keyword, rest) pairs."""
    tags, current = [], None
    for line in (raw.strip() for raw in text.splitlines()):
        if not line or line.startswith(("```", "#")):
            continue
        if line.startswith(("@", "Scenario:", "Scenario Outline:", "Example:")) and current:
            yield current
            current = None
        if line.startswith("@"):
            tags += line.split()
        elif line.startswith(("Scenario:", "Scenario Outline:", "Example:")):
            current, tags = (tags, line.split(":", 1)[1].strip(), []), []
        elif current and line.split(" ", 1)[0] in ("Given", "When", "Then", "And", "But"):
            kw, _, rest = line.partition(" ")
            current[2].append((kw, rest))
    if current:
        yield current


def check(tags, title, steps):
    effective, last = [], None
    for kw, rest in steps:
        last = last if kw in ("And", "But") and last else kw
        effective.append(f"{last} {rest}")
    ids = [t for t in tags if re.fullmatch(r"@sc_[a-z]+_\d{2}", t)]
    problems = [] if len(ids) == 1 else [("tag", f"expected one @sc_<feature>_<NN> tag, found {ids or 'none'}")]
    if not re.match(r"R[1-4]\b", title):
        problems.append(("title", "does not start with a rule ID (R1-R4)"))
    if (n := sum(s.startswith("When ") for s in effective)) != 1:
        problems.append(("one-when", f"{n} When steps"))
    if not 3 <= len(steps) <= 5:
        problems.append(("step-count", f"{len(steps)} steps"))
    return problems + [("wording", f'"{s}" is not a house step') for s in effective
                       if not any(r.match(s) for r in HOUSE_STEPS)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--steps", metavar="FILE", help="extra accepted step patterns, one regex per line")
    ap.add_argument("--grading", metavar="OUT", help="also write a skill-creator grading.json here")
    args = ap.parse_args()
    if args.steps:
        lines = open(args.steps, encoding="utf-8").read().splitlines()
        HOUSE_STEPS.extend(re.compile(p.strip() + "$") for p in lines if p.strip() and not p.startswith("#"))
    found, count = [], 0
    for name in args.files:
        text = sys.stdin.read() if name == "-" else open(name, encoding="utf-8").read()
        for tags, title, steps in scenarios(text):
            count += 1
            for key, msg in check(tags, title, steps):
                found.append((key, f"{name}: {title!r}: {key}: {msg}"))
                print(found[-1][1])
    if count == 0:
        found = [(key, "no scenarios found") for key in CHECKS]
        print("no scenarios found (is this Gherkin?)")
    if args.grading:
        exps = [{"text": text, "passed": all(k != key for k, _ in found),
                 "evidence": "; ".join(m for k, m in found if k == key) or f"{count} scenarios checked"}
                for key, text in CHECKS.items()]
        passed = sum(e["passed"] for e in exps)
        with open(args.grading, "w") as f:
            json.dump({"expectations": exps, "summary": {"passed": passed, "failed": len(exps) - passed,
                       "total": len(exps), "pass_rate": round(passed / len(exps), 2)}}, f, indent=2)
    print(f"{count} scenarios, {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
```

先看它在壞例子上會失敗（`samples/bad.feature` 就是 Ch 4 scenario-conventions 裡的 Bad 範例，前面加一行 `Feature:`）：

```
$ printf 'Feature: Book a meeting room\n\n  Scenario: booking works\n    When Alice books "Taipei" from 10:00 to 11:00\n    And Bob books "Taipei" from 10:30 to 11:00\n    Then the schedule has 1 item\n' > samples/bad.feature
$ python scenario_rules.py samples/bad.feature; echo "exit=$?"
samples/bad.feature: 'booking works': tag: expected one @sc_<feature>_<NN> tag, found none
samples/bad.feature: 'booking works': title: does not start with a rule ID (R1-R4)
samples/bad.feature: 'booking works': one-when: 2 When steps
samples/bad.feature: 'booking works': wording: "Then the schedule has 1 item" is not a house step
1 scenarios, 4 problems
exit=1
```

`grade_iteration.sh` 對每個 run 評分，再交給 skill-creator 的聚合 script。我們照 SKILL.md 的寫法，在 skill-creator 目錄下用 `python -m` 執行它；聚合 script 只用標準函式庫，直接 `python 路徑/aggregate_benchmark.py` 也行，但 `package_skill`、`run_eval`、`run_loop` 會 import `scripts.*`，非這樣跑不可：

```sh
#!/bin/sh
# grade_iteration.sh ITERATION_DIR [STEPS_FILE]: grade each run's .feature with scenario_rules.py into run-*/grading.json
set -u
for run in "$1"/eval-*/*/run-*; do
  python scenario_rules.py "$run"/outputs/*.feature ${2:+--steps "$2"} --grading "$run/grading.json" > "$run/rules.txt"
  printf '%-58s %s\n' "${run#"$1"/}" "$(tail -n 1 "$run/rules.txt")"
done
```

```
$ IT=$PWD/work/skills/gherkin-scenario-writer-workspace/iteration-1 && ./grade_iteration.sh $IT
eval-1-pm-note-back-to-back/with_skill/run-1               4 scenarios, 0 problems
eval-1-pm-note-back-to-back/without_skill/run-1            4 scenarios, 0 problems
eval-2-ticket-cancel-permissions/with_skill/run-1          4 scenarios, 6 problems
eval-2-ticket-cancel-permissions/without_skill/run-1       4 scenarios, 9 problems
eval-3-slack-ambiguous-and-unknown-rule/with_skill/run-1   3 scenarios, 2 problems
eval-3-slack-ambiguous-and-unknown-rule/without_skill/run-1 4 scenarios, 6 problems
$ (cd vendor-skill-creator/skills/skill-creator && python -m scripts.aggregate_benchmark $IT --skill-name gherkin-scenario-writer --output $IT/benchmark-strict.json)
…（略：Generated 兩行與 Summary）
$ sed -n '/| Metric/,$p' $IT/benchmark-strict.md
| Metric | With Skill | Without Skill | Delta |
|--------|------------|---------------|-------|
| Pass Rate | 87% ± 12% | 80% ± 20% | +0.07 |
| Time | 17.3s ± 1.0s | 29.3s ± 9.0s | -12.0s |
| Tokens | 17225 ± 148 | 16043 ± 1018 | +1182 |
```

+0.07？打開 `rules.txt` 看扣分的地方，這個數字有兩個問題。第一，**評分器罰錯人**：帶 skill 那組在 eval-2、eval-3 的「wording」失分，全是 skill 自己提議的 R2/R4 step（例如 `Then the cancellation is rejected because booking 7 not found`），而我們的需求本來就允許「沒有合用的才新增」。第二，**評分器沒看到真錯**：不帶 skill 那組在 eval-2 把取消權限標成 `R2`、`R3`，在 eval-3 替還沒定案的 2 小時上限發明了 `R3` 並寫出只有兩步的情境；「title 以 R1–R4 開頭」這條 assertion 對這種錯完全放行。skill-creator 起草的 assertion（「Every new scenario title starts with 'R4 '」）抓得到，代價是每個 eval 要有自己的判斷。

團隊審過 skill 提議的六個 step 後決定接受，寫進 `steps-accepted.txt`。R2 那句在本課不只一種寫法：[練習 A](./practice-a-first-loop.md) 與 [Ch 26](./26-bdd-vs-tdd-artifacts.md) 寫 `Then the booking is rejected because the end must be after the start`；[Ch 28](./28-gherkin-dsl-style.md) 要 because 後面逐字等於 API 的 detail，用的正是這裡提議的 `because end must be after start`。下面保留 skill 當時的提議：

```
# R2/R4 steps proposed by gherkin-scenario-writer (SKILL.md "Steps"), accepted by the team on review
When \w+ cancels the booking of "\w+" from \d\d:\d\d to \d\d:\d\d
When \w+ cancels booking \d+
Then the booking is cancelled
Then the booking is rejected because end must be after start
Then the cancellation is rejected because only \w+ can cancel this booking
Then the cancellation is rejected because booking \d+ not found
```

用同一份產出重新評分（兩組套用同一份清單）：

```
$ ./grade_iteration.sh $IT steps-accepted.txt
…（略：with_skill 三個 run 都是 0 problems；without_skill 依序 0、8、6）
$ (cd vendor-skill-creator/skills/skill-creator && python -m scripts.aggregate_benchmark $IT --skill-name gherkin-scenario-writer) > /dev/null
$ sed -n '/| Metric/,$p' $IT/benchmark.md
| Metric | With Skill | Without Skill | Delta |
|--------|------------|---------------|-------|
| Pass Rate | 100% ± 0% | 80% ± 20% | +0.20 |
| Time | 17.3s ± 1.0s | 29.3s ± 9.0s | -12.0s |
| Tokens | 17225 ± 148 | 16043 ± 1018 | +1182 |
```

同一批產出，delta 從 +0.07 變 +0.20，差別只在 assertion。讀這張表還要知道三件事（前兩件來自讀 `aggregate_benchmark.py`）：`±` 是**三個 eval 之間**的標準差，每個 eval 只跑一次，不是重跑的變異；`benchmark.md` 的 Evals 那一行寫的「3 runs each per configuration」是這個版本寫死的字串（Model 那行也只是佔位的 `<model-name>`），我們實際每組只跑 1 次；eval-1 兩組都 5/5，是不會區分好壞的題目（現有檔案已經示範了慣例，baseline 照抄），skill-creator 的報告在跑完時就預測了這點。帶 skill 那組平均多花 1,182 tokens（多半是讀進了 SKILL.md），卻少花 12 秒；這只是三個樣本，別外推。

## 底層機制：觸發 eval 怎麼判定「有觸發」

```
 run_eval.py 不載入你的 skill，只測 description。每個 query、每一次：
   1. 在 project root（往上找第一個有 .claude/ 的目錄）寫一個暫時檔
        .claude/commands/<name>-skill-<8 位 uuid>.md   內容＝你的 description
   2. 開一個 claude -p "<query>" --output-format stream-json --verbose --include-partial-messages
   3. 盯著串流：第一個 tool_use 是 Skill 或 Read 且參數含暫時名字 → 觸發；其他工具或沒呼叫工具 → 沒觸發
   4. 立刻砍掉 process、刪掉暫時檔；觸發率 ≥ --trigger-threshold（預設 0.5）才算觸發
```

所以：它只量 description，body 從頭到尾沒被讀；eval 專案裡**不能**裝著同名的真 skill，否則模型叫了真的那個，會被算成沒觸發；它看的是「第一個」工具，模型若先 Read 別的檔案再叫 skill，也算沒觸發。`run_eval.py` 呼叫的 `claude -p` 沒有回合上限、也沒限制工具，所以我們在 PATH 前面放一個同名 shim，替每次呼叫加上安全帶：

```sh
#!/bin/sh
# shim/claude: run_eval.py calls `claude -p ...`; add a turn cap, no approvals and a clean config
exec "$HOME/.local/bin/claude" "$@" --max-turns 2 --allowedTools Read --permission-prompts none \
  --setting-sources project --strict-mcp-config --no-session-persistence
```

建好後一定要 `chmod +x shim/claude`：沒有執行權限時，Python 的 subprocess 會安靜地略過它，改跑 PATH 後面那個真的 `claude`，安全帶就沒了（我們用兩個同名的假指令實測過）。`--setting-sources project` 讓 eval 看不到你個人的 skill 與 plugin，結果比較可重現；代價是它和你平常的環境不同，你裝了很多 skill 時，競爭會更激烈。

## 五、實測 2、3：量 description 的觸發率

八個 query，四個該觸發、四個是「關鍵字很像但不該觸發」的近似題（near-miss），其中第四個是**陽性對照**：直接點名要用這個 skill，量測機制正常的話幾乎一定會觸發。`trigger-evals.json`：

```json
[
  {"query": "can you turn this into gherkin for our booking feature file? bob has taipei 10-11 and alice tries 10:30-11:30, she should be rejected", "should_trigger": true},
  {"query": "PM just added a rule: a booking whose end time isn't after its start must be refused with 422 'end must be after start'. Write the acceptance scenarios for features/booking.feature, we're at @sc_book_07", "should_trigger": true},
  {"query": "We need BDD scenarios for cancelling: only the person who booked can cancel (403 otherwise) and an unknown booking id is a 404. Put them in features/cancel.feature", "should_trigger": true},
  {"query": "use the gherkin scenario writer skill to add the adjacent-slots case (Alice 11:00-12:00 right after Bob's 10:00-11:00 in Taipei) to features/booking.feature", "should_trigger": true},
  {"query": "pytest-bdd says StepDefinitionNotFoundError for 'Given room \"Taipei\" is free all day' even though tests/steps/test_booking.py has a @given for it. what's wrong with my step definition?", "should_trigger": false},
  {"query": "write a pytest unit test for Schedule.book that checks two overlapping Taipei bookings raise Conflict with the message 'Taipei is taken 10:00-11:00 by Bob'", "should_trigger": false},
  {"query": "Summarise features/booking.feature for our PM in two plain sentences of Traditional Chinese, no Gherkin please", "should_trigger": false},
  {"query": "POST /bookings returns 500 instead of 422 when end is before start. fix the validation in booking/api.py", "should_trigger": false}
]
```

`trigger_eval.sh` 從第一節建好的空 `evalproj/`（只有一個空的 `.claude/`）執行；`PYTHONPATH` 讓 `python -m scripts.run_eval` 找得到 skill-creator 的 scripts：

```sh
#!/bin/sh
# trigger_eval.sh OUT [DESCRIPTION]: run_eval.py on trigger-evals.json, 1 run per query, through shim/claude
set -u
LAB=/tmp/lab/aibdd/ch05-skill-creator
cd "$LAB/evalproj"
PATH="$LAB/shim:$PATH" PYTHONPATH="$LAB/vendor-skill-creator/skills/skill-creator" \
  python -m scripts.run_eval --eval-set ../trigger-evals.json --skill-path ../work/skills/gherkin-scenario-writer \
  --runs-per-query 1 --num-workers "${WORKERS:-1}" --timeout 60 --verbose ${2:+--description "$2"} > "$LAB/runs/$1.json"
```

> 以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。實測 2 跑的是這支腳本改版前、寫死 4 個 worker 的版本，等同下面的 `WORKERS=4`。

```
$ WORKERS=4 ./trigger_eval.sh run2-trigger 2> runs/run2-trigger.log; tail -9 runs/run2-trigger.log
Results: 4/8 passed
  [FAIL] rate=0/1 expected=True: use the gherkin scenario writer skill to add the adjacent-slots case (
…（略：另外三個該觸發的也是 FAIL，rate=0/1）
  [PASS] rate=0/1 expected=False: pytest-bdd says StepDefinitionNotFoundError for 'Given room "Taipei" i
…（略：另外三個不該觸發的也是 PASS）
```

八題 5 秒跑完，**連陽性對照都沒觸發**。這不是 description 寫壞了，是量測壞了。要診斷又不想再花一次模型呼叫，有個幾乎不花錢的辦法：照 run_eval 的方式放一個暫時 command，用一個不存在的模型名稱啟動——Claude Code 會先印出 init 事件（列出它註冊了哪些 skill），再在 API 那一步失敗：

```
$ cd evalproj && mkdir -p .claude/commands && printf -- '---\ndescription: |\n  Turns a booking rule into Gherkin scenarios.\n---\n\n# gherkin-scenario-writer\n\nThis skill handles: Turns a booking rule into Gherkin scenarios.\n' > .claude/commands/gherkin-scenario-writer-skill-probe0001.md
$ PATH=../shim:$PATH claude -p "use the gherkin scenario writer skill to add the adjacent-slots case" --output-format stream-json --verbose --include-partial-messages --model claude-does-not-exist-0 > ../runs/diag-invalid-model.jsonl 2> ../runs/diag-invalid-model.stderr
$ jq -c 'select(.type=="system" and .subtype=="init") | {skills: (.skills|map(select(test("gherkin")))), slash_commands: (.slash_commands|map(select(test("gherkin"))))}' ../runs/diag-invalid-model.jsonl
{"skills":[],"slash_commands":["gherkin-scenario-writer-skill-probe0001"]}
```

（為了版面，上面只篩出名字含 gherkin 的項目。`claude` 以結束碼 1 收場，stderr 只有一行 `[claude-code:unrecognized_model] {"model":"claude-does-not-exist-0",…}`。主模型沒有被呼叫，但 Claude Code 有時會另跑一次約 1.2k input tokens 的 Haiku 小呼叫，我們幾次實測的 `total_cost_usd` 是 0 或約 US$0.0001。用完記得刪掉暫時檔，再 `cd ..` 回到實驗目錄。）暫時的 command 只出現在 `slash_commands`，**不在模型可以自己叫用的 `skills` 清單裡**；換成 `.claude/skills/<名字>/SKILL.md` 再試一次，兩邊都出現。Claude Code 的 skills 文件也寫了：skill 比 command 多出的能力之一，就是「the ability for Claude to load them automatically when relevant」。所以在 2.1.295 上，這版 `run_eval.py` 幾乎量不到觸發（除非模型剛好自己去 Read 那個暫時檔）。修法是讓暫時檔變成 skill 資料夾，改完後 `diff run_eval.py.orig run_eval.py` 的輸出如下：

```
53,54c53,56
<     project_commands_dir = Path(project_root) / ".claude" / "commands"
<     command_file = project_commands_dir / f"{clean_name}.md"
---
>     # Claude Code 2.1.295 lists .claude/commands/*.md as slash commands only, not as skills
>     # Claude can invoke, so the temporary copy has to be a skill folder.
>     project_commands_dir = Path(project_root) / ".claude" / "skills" / clean_name
>     command_file = project_commands_dir / "SKILL.md"
61a64
>             f"name: {clean_name}\n"
181a185
>             project_commands_dir.rmdir()
```

要照做，把上面這段存成實驗目錄裡的 `run_eval.diff`，再執行 `S=vendor-skill-creator/skills/skill-creator/scripts; cp $S/run_eval.py $S/run_eval.py.orig && patch $S/run_eval.py < run_eval.diff`。改成 skill 之後還有一個新問題：平行的 worker 會**看到彼此的暫時 skill**，模型可能叫了別人的那份而被判成沒觸發（這是讀程式推出來的，沒有單獨實測），所以實測 3 改成一次一個（`WORKERS` 預設 1），以下為實際執行紀錄（節錄），每次執行結果不同（2026-11-02）：

```
$ ./trigger_eval.sh run3-trigger-patched 2> runs/run3-trigger-patched.log; tail -9 runs/run3-trigger-patched.log
Results: 8/8 passed
  [PASS] rate=1/1 expected=True: can you turn this into gherkin for our booking feature file? bob has t
…（略：另外三個該觸發的也是 rate=1/1）
  [PASS] rate=0/1 expected=False: pytest-bdd says StepDefinitionNotFoundError for 'Given room "Taipei" i
…（略：另外三個不該觸發的也是 rate=0/1）
```

8/8 不等於 100%。每題只跑一次，「1/1」是一個樣本：就算每個該觸發的 query 真實觸發率只有 70%，四題各一次全部觸發的機率仍有 0.7⁴ ≈ 24%（假設各次獨立）。skill-creator 本來的設計是 20 題、每題 3 次、再留 40% 當 test set；我們只跑 8 題 1 次，是為了控制模型呼叫次數，結論只能說「沒看到明顯的漏觸發或誤觸發」。

## 六、三種 validator 與打包

`make_variants.sh` 做出四個小 skill，專門挑三種 validator 意見不同的地方（執行 `./make_variants.sh` 一次，產物在 `variants/skills/`）：

```sh
#!/bin/sh
# make_variants.sh: four one-file skills that the three validators disagree about
set -eu
D="Write Gherkin scenarios for the booking service. Use when asked for BDD or acceptance scenarios."
mk() { mkdir -p "variants/skills/$1" && printf '%s\n' "$2" > "variants/skills/$1/SKILL.md"; }
mk angle-brackets "---
name: angle-brackets
description: Write scenarios tagged @sc_<feature>_<NN>. Use when asked for BDD scenarios.
---
Write one scenario per rule."
mk manual-only "---
name: manual-only
description: Regenerate every scenario from docs/rules.md. Run only when the user types /manual-only.
disable-model-invocation: true
---
Rewrite features/*.feature from docs/rules.md."
mk dead-link "---
name: dead-link
description: $D
---
Follow the step glossary in [references/steps.md](references/steps.md)."
mk long-desc "---
name: long-desc
description: $D$(printf ' Also use it for Given/When/Then, feature files, rule examples and acceptance criteria for rooms Taipei, Tokyo and Seoul.%.0s' 1 2 3 4 5 6 7 8)
---
Write one scenario per rule."
```

```
$ for d in variants/skills/*/; do printf '%-15s ' "$(basename $d)"; python vendor-skill-creator/skills/skill-creator/scripts/quick_validate.py $d; done
angle-brackets  Description cannot contain angle brackets (< or >)
dead-link       Skill is valid!
long-desc       Description is too long (1064 characters). Maximum is 1024 characters.
manual-only     Unexpected key(s) in SKILL.md frontmatter: disable-model-invocation. Allowed properties are: allowed-tools, compatibility, description, license, metadata, name
$ node /tmp/lab/tools/skill-lint.mjs variants/skills; echo "exit=$?"
dead-link: link references/steps.md does not resolve
long-desc: description is 1064 chars, over 1024
4 skills, 2 problems
exit=1
$ claude plugin validate variants/skills; echo "exit=$?"
…（略：Validating components in: …）
✔ Validation passed
exit=0
```

| 變體 | quick_validate（Agent Skills 規格與上傳格式） | skill-lint（pstack Ch 28 的寫作慣例） | `claude plugin validate`（Claude Code 能不能載入） |
|---|---|---|---|
| `angle-brackets` | ✗ 角括號 | 通過 | 通過（官方文件：角括號會顯示成 `&lt;` `&gt;`） |
| `manual-only` | ✗ 不認得 `disable-model-invocation` | 通過 | 通過（這是 Claude Code 的擴充欄位） |
| `dead-link` | 通過（不查連結） | ✗ 連結指向不存在的檔案 | 通過 |
| `long-desc`（1,064 字元） | ✗ 超過 1,024 | ✗ 超過 1,024 | 通過（Claude Code 在清單裡截到 1,536） |

三種 validator 守的是三種不同的目標。一個 Claude Code 專用、有副作用的 skill，**正確地**加上 `disable-model-invocation: true` 之後，反而過不了 quick_validate，`package_skill.py` 也拒絕打包它（實測印出同一句 `Unexpected key(s) …` 並回 1）。結論不是哪個 validator 對，而是先決定你的 skill 要去哪裡（Claude Code 專案、plugin、claude.ai 上傳），再挑對應的檢查。frontmatter 不在第一行的情況，Ch 4 的踩雷 3 已經示範過。

`gherkin-scenario-writer` 三關都過（quick_validate `Skill is valid!`、skill-lint `1 skills, 0 problems`、plugin validate `✔ Validation passed`），可以打包：

```
$ (cd vendor-skill-creator/skills/skill-creator && python -m scripts.package_skill /tmp/lab/aibdd/ch05-skill-creator/work/skills/gherkin-scenario-writer /tmp/lab/aibdd/ch05-skill-creator/dist)
…（略：Packaging 與 Validating 的提示）
✅ Skill is valid!

  Added: gherkin-scenario-writer/SKILL.md
  Skipped: gherkin-scenario-writer/evals/evals.json
  Skipped: gherkin-scenario-writer/evals/files/booking.feature

✅ Successfully packaged skill to: /private/tmp/lab/aibdd/ch05-skill-creator/dist/gherkin-scenario-writer.skill
```

`.skill` 就是 zip，根目錄的 `evals/` 不會跟著出貨。在 Claude Code 專案裡用，審過之後由人複製到 `.claude/skills/gherkin-scenario-writer/`；要跨 repo 分享，包成 plugin。照 Ch 4 的作法，把它和 Ch 4 手寫的 scenario-conventions 放進同一個臨時 plugin（兩個 SKILL.md 放進 `probe/skills/`，`claude --plugin-dir ./probe plugin details probe`）估價：skill-creator 的版本常駐約 190 tok、觸發約 2.1k tok，手寫版約 100 tok、470 tok。這是估價器的估計值，只適合比同一次輸出裡的相對大小：同一份 scenario-conventions 單獨估是約 540 tok，Ch 4 三個 skill 一起估也是 540。多出來的是規則表、R2/R4 的 step 與不明規則的處理方式；這些內容有沒有讓手寫版在 eval-2、eval-3 輸給它，本章沒有實測。

## 對比與取捨

| 做法 | 量到什麼 | 成本 | 盲點 |
|---|---|---|---|
| 手寫 + 試一次（Ch 4） | 一個樣本 | 最低 | 作者帶著 context 試，看不出 body 漏了什麼 |
| skill-lint／quick_validate／plugin validate | 格式與連結 | 幾乎為零 | 完全不知道產出好不好 |
| skill-creator 對照評測（本章實測 1） | 帶與不帶 skill 的產出差 | 實測 1 含主對話共約 US$2.24 | 分數取決於 assertion；每組 1 次時 ± 沒有意義 |
| `run_eval.py` 觸發 eval（實測 2、3） | description 會不會被挑中 | 每 query 每次一個 `claude -p` | 只量第一個工具；本版在 2.1.295 要修補才量得到 |
| `run_loop.py` 自動改寫 description | train/test 上的觸發率 | 20 題 × 3 次 × 最多 5 輪 | 本章未實測；量測壞掉時，它會對著雜訊最佳化 |
| `claude plugin eval` | 帶與不帶 plugin、可放進 CI | 每 case 帶、不帶 plugin 各 3 次（預設），llm 類 grader 另有 judge 呼叫 | 本章未實測；格式和 skill-creator 的 `evals.json` 不互通 |

## 踩雷集錦

1. **錯誤直覺：「工具量到 0，就是 description 太弱。」** 正確認識：先檢查量測本身。實測 2 連「點名要用這個 skill」的陽性對照都是 0/1，原因是暫時 command 不在 `skills` 清單裡。每份觸發 eval 都放一題陽性對照，它不過，其他數字都不必讀。
2. **錯誤直覺：「`aggregate_benchmark.py` 沒報錯，結果就可信。」** 正確認識：把實測 1 的產出照 SKILL.md 字面的版型（`pm-note-back-to-back/with_skill/` 底下直接放檔案）複製一份再聚合，它印出 `No eval directories found in …`，結束碼卻是 0，還寫出一份 `Config A`、`Config B` 全為 0% 的 benchmark.md。資料夾有 `eval-` 前綴、只少了 `run-*` 那一層時更糟：連這句都不印，一樣回 0、一樣全為 0%。要先確認它真的讀到了 `eval-*/…/run-*`。
3. **錯誤直覺：「delta 是 skill 的固有屬性。」** 正確認識：同一批產出，assertion 一改，delta 從 +0.07 變 +0.20；太嚴的 assertion 罰了 skill 該做的事，太鬆的 assertion 放過了 baseline 的錯。grader.md 要評分者同時批評 assertion，原因就在這裡。
4. **錯誤直覺：「`benchmark.md` 寫 3 runs、標了 ±，就是統計過的。」** 正確認識：這個版本的「3 runs」是寫死的字串，`±` 是不同 eval 之間的差異。每個 eval 只跑一次時，先看原始的通過數與 delta，agentskills.io 的評測指南也這樣建議。
5. **錯誤直覺：「讓 skill-creator 直接寫進 `.claude/skills/`。」** 正確認識：`.claude` 是 protected path，allow 規則不會預先核准它，非互動模式下會被拒絕。這是好事：skill 會被每個 session 載入，先寫在別處、審過再複製，等於替它加了一道 code review。

## 進階：再往深一層

- **跑完整的 description 最佳化、給人看的審閱頁**：修補後的 `run_eval.py` 也會被 `run_loop.py` 使用。指令是 `python -m scripts.run_loop --eval-set <20 題> --skill-path <skill> --model claude-opus-5-5 --max-iterations 5 --verbose`（`--model` 必填）。它會把 40% 的題目留作 test、用 test 分數挑 `best_description`，避免對訓練題過擬合。呼叫次數是「題數 × 3 × 輪數」等級，本章未實測。給人看的審閱頁則是 `python eval-viewer/generate_review.py <iteration> --benchmark <benchmark.json> --static review.html`，本章的環境產出一個約 74 KB 的單檔 HTML，按「Submit All Reviews」會下載 `feedback.json` 給下一輪讀。
- **盲比兩個版本**：改了 SKILL.md 之後，用 `agents/comparator.md` 讓不知情的 subagent 比較新舊產出，再用 `analyzer.md` 解釋贏在哪裡。**換成 `claude plugin eval`**：把 skill 放進 plugin，寫 `evals/<case>/prompt.md` 與 `graders/*.md`（例如 `tool_used: Skill` 檢查有沒有觸發），它會自動加一組不載入 plugin 的 baseline，並能用 `--threshold` 在 CI 擋下退步；它的 HTML 報告預設會發佈到 claude.ai（`--help` 原文：「already the default when your account supports it」），內容不能外流時加 `--no-publish`。本機同步的新版 skill-creator 已經改用這個資料夾格式。

## 動手練習

1. **補上語意 assertion**：替 `scenario_rules.py` 加一個 `--rules R1,R4` 選項，要求標題的規則 ID 落在清單裡，重新評分 eval-2，確認 baseline 的 `R2`、`R3` 會被抓到、skill 那組不受影響。
2. **量變異**：把 `--runs-per-query` 改成 3 重跑實測 3（會多 16 次 `claude -p`，計入你自己的預算），看哪一題的觸發率不是 0 或 1，再加兩題最難分的近似題。**和手寫版正面比較**：把 Ch 4 的 scenario-conventions 當成 `old_skill`，用同一批 3 個 prompt 跑對照評測（SKILL.md「Step 1: Spawn all runs」裡 baseline 的「Improving an existing skill」那一條），看 eval-2、eval-3 的差距。

## 本章重點整理

- skill-creator 把寫 skill 變成實驗：測試 prompt、帶與不帶 skill 的 subagent、評分、聚合、人工審閱、改寫；description 的觸發另外用 `run_eval.py` 量
- 實測 1：skill-creator 寫出 93 行的 `gherkin-scenario-writer`，6 個 subagent 跑完對照；用 script 評分後，delta 依 assertion 不同是 +0.07 或 +0.20
- 工具自己會壞：SKILL.md 與聚合 script 的版型不一致，聚合失敗時仍回 0；這版 `run_eval.py` 在 2.1.295 量不到觸發，修補後 8/8，但每題只跑一次
- 三種 validator 守三種目標；有 `disable-model-invocation` 的 Claude Code skill 過不了 quick_validate。任何數字都先問：陽性對照過了沒、樣本數多少、`±` 算的是什麼

## 自我檢核

- [ ] 不看本章，畫出 skill-creator 的迴圈，並指出哪幾步是 script、哪幾步要模型
- [ ] 對照評測量的是 body 還是 description？觸發 eval 呢？一個要上傳 claude.ai、又要在 Claude Code 手動叫用的 skill，該跑哪幾個 validator
- [ ] 實測 2 的 8 題沒有一題觸發，你怎麼確定問題在量測而不在 description？幾乎不花錢的那個診斷是怎麼做的
- [ ] 同一批產出，delta 為什麼會從 +0.07 變成 +0.20？這個改動在什麼情況下是作弊

## 延伸閱讀

- **[skill-creator 原始碼](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/skill-creator)** — Anthropic（plugin 版；`anthropics/skills` repo 裡另有一份。2026-11-02 查證時，兩邊 main 上的 `run_eval.py` 都和本章用的 a7ba5b93 相同）
  - **讀哪裡**：SKILL.md 的「Running and evaluating test cases」與「Description Optimization」，以及 `scripts/run_eval.py`、`scripts/aggregate_benchmark.py`；**學到什麼**：迴圈每一步實際讀寫哪些檔案、哪幾步會呼叫 `claude -p`；**和本章的關聯**：本章兩個坑最後都是讀這兩支 script 才看懂的，換版本時先 diff 它們
- **[Improving skill-creator: Test, measure, and refine Agent Skills](https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills)** — Claude blog（2026-03-03）
  - **讀哪裡**：「Using evals to test and improve skills」「Faster, more consistent evaluation with multi-agent support」「Getting skills to trigger at the right time」三節；**學到什麼**：evals、benchmark、平行 agent、盲比與 description 最佳化的設計動機，以及官方在自家 6 個文件類 skill 上用 description 最佳化改善了其中 5 個的觸發；**和本章的關聯**：知道設計者想量什麼，才知道本章哪些數字不能拿來比
- **[Evaluating skill output quality](https://agentskills.io/skill-creation/evaluating-skills)** — Agent Skills 文件
  - **讀哪裡**：「Writing assertions」「Grading principles」「Analyzing patterns」；**學到什麼**：怎麼寫可檢查的 assertion、評分時順便審 assertion 本身，以及 stddev「is only meaningful with multiple runs per eval」；**和本章的關聯**：本章第四節的判讀方式。注意它畫的 workspace 版型（`eval-<名字>/with_skill/`）和 skill-creator 的 SKILL.md 一樣沒有 `run-*` 那一層，這版聚合 script 讀不到，而且一聲不吭（踩雷 2）
- **[Claude Code 文件：Evaluate and iterate on a skill](https://code.claude.com/docs/en/skills)** 與 **[Test plugins with evals](https://code.claude.com/docs/en/plugin-evals)**
  - **讀哪裡**：skills 頁的「Evaluate and iterate on a skill」「Run evals with skill-creator」；plugin-evals 頁的「How an eval run works」「The no-plugin baseline」；**學到什麼**：官方建議怎麼做帶與不帶的比較、`claude plugin eval` 怎麼計分與設門檻；**和本章的關聯**：進階一節的換軌路線。兩頁都寫 skill-creator 與 `claude plugin eval` 的格式不互通（2026-11-02 查證），本機同步的新版 skill-creator 已經往後者靠攏

我們有了一個量過的 skill，也看到量測工具本身會壞。但 skill 只是工作流的一層：哪些規則該進 CLAUDE.md、哪些該是 hook、誰來審 `.claude/` 的變更、什麼時候該把一條規則退休，是下一章的主題。

→ [Ch 6 工作流是長出來的：團隊怎麼養一套 agent 工作流](./06-workflows-are-grown.md)
