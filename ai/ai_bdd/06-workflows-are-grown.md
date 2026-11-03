# Ch 6 — 工作流是長出來的：團隊怎麼養一套 agent 工作流

> **目標**：用一本失敗帳本（failure ledger）讓團隊的 agent 工作流「長」出來：每條規則都來自兩次以上的失敗、放在對的層（CLAUDE.md、skill、check、guard）、改動像程式碼一樣經過 review 與出處檢查、用回放與趨勢判斷有沒有效，並在失去理由時退役。你會跑一支 `ledger.py`，看一個構造的三人團隊在八週、四次 retro 裡做了哪些決定，也會實際量一次「第一天就裝一整套框架」裝進了什麼。

> **環境**：Python 3.12.15 + PyYAML 6.0.3（課程 venv）、git、Claude Code 2.1.295（一次 `claude -p`，2026-11-02）、Spec Kit 1.2.5（`uvx`）、macOS 26.6。實驗目錄 `/tmp/lab/aibdd/ch06-grown/`。**帳本裡的團隊、事故與日期是為教學構造的，不是真實團隊的紀錄。**

## 為什麼需要這個？

團隊開始用 agent，常見的起步有兩種。第一種是**被餵食**：第一天裝一套別人的 prompt pack、規則合集或完整框架，看起來樣樣齊全；三個月後 CLAUDE.md 長到幾百行，沒有人敢刪任何一行，因為沒有人知道哪一行在防什麼。第二種是**放任**：每個人在自己的對話裡糾正 agent，「不要改測試」一週被打二十次，下一個 session 全部重來。兩種起步丟掉的是同一樣東西：**規則和它所防的那次失敗之間的連結**。

這個問題在 agent 之前就有解法。SRE 的事後檢討（postmortem）是「a written record of an incident, its impact, the actions taken to mitigate or resolve it, the root cause(s), and the follow-up actions to prevent the incident from recurring」，而且不是每次出事都寫，只在符合事先訂好的觸發條件時寫（使用者看得到的停機、資料遺失、需要值班工程師介入等）；團隊的 coding style guide 也常是從 code review 裡反覆出現的評論長出來的。John Gall 在《Systemantics》（1977）寫得更直接：「A complex system that works is invariably found to have evolved from a simple system that worked.」Claude Code 官方文件的〈Build your setup over time〉也是同一個立場：「You don't need to configure everything up front. Each feature has a recognizable trigger」。

這個 repo 已經教過**單一對象**的版本：一個人的設定（[pstack Ch 30](../pstack/30-self-improvement-loop.md) 的 `/reflect`、`/correct` 與「修正的層級」）、一份 system prompt（[harness Ch 11](../harness_engineering/11-system-prompt-design.md) 的「五、迭代：system prompt 是調出來的，不是寫一次就完」）、一份 constitution（[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的「Constitution 定期評審」）。這章補的是**整個團隊工作流**的版本：一本共用的帳本、好幾個 repo、`.claude/` 的 PR review、怎麼量有沒有效，以及怎麼把規則拆掉。

## 先建立直覺：號誌是在出過事的路口才裝的

城市不會第一天就在每個路口裝紅綠燈。交通工程師看事故紀錄：同一個路口出過幾次事、是哪種事，再決定裝標誌（提醒，駕駛可以不看）、測速照相（違規事後一定被抓到）、紅綠燈或分隔島（在衝突發生之前就把車流分開），或者乾脆改道，讓這種事故不可能發生。路口改建之後，沒用的號誌會被拆掉，否則駕駛學會無視所有號誌。

```
  交通工程                agent 工作流                                本章的名字
  ───────────            ─────────────────────────────              ─────────────
  事故紀錄                帳本：agent 出錯、而且有人為它花了時間          incident  I-xx
  同一路口出事兩次         同一類失敗發生第二次                           class
  標誌                    CLAUDE.md 一行、一個 skill（模型可以不照做）     claude-md / skill
  測速照相                測試、lint、CI（事後一定抓到）                 check
  紅綠燈、分隔島            hook、deny 規則、sandbox（事前擋住）           guard
  改道                    改設計，讓這類失敗不可能發生                    closed
  拆掉沒用的號誌            退役規則                                     retired
```

這個比喻有兩處不成立。交通工程師沒辦法重播一場車禍，我們可以：把過去那次失敗的 code 拿去跑新的檢查（第五節）。路邊多一個標誌幾乎不花錢，CLAUDE.md 多一行卻每一輪都佔 context（[Ch 4](./04-prompt-files-to-skills.md) 量過）。一條規則的一生：

```
  incident ──▶ class（第 2 次）──▶ rule：放在「夠用的最便宜層」
                                      │ 又發生（REPEAT）
                                      ▼
            往上移：claude-md／skill ──▶ check ──▶ guard（guard 也漏 → 找出繞過它的路）
                                      │ 被更強的規則涵蓋（SUPERSEDED）／guard 長期沒觸發（STALE）／原因被設計移除（closed）
                                      ▼
                                 退役（retired）
```

## 一、帳本：先記錄，不急著立規則

什麼時候記一筆？官方 memory 文件〈When to add to CLAUDE.md〉列的觸發點正好是記帳的時機：「Claude makes the same mistake a second time」、「A code review catches something Claude should have known about this codebase」、「You type the same correction or clarification into chat that you typed last session」、「A new teammate would need the same context to be productive」。差別在於：我們先寫進帳本，**要不要變成規則、放在哪一層，留給 retro 決定**。記錄時沿用 SRE 的不究責（blameless）原則——記 agent 缺了什麼 context、失敗長什麼樣，不記「誰的 prompt 寫得爛」。在空目錄 `/tmp/lab/aibdd/ch06-grown/` 建立 `ledger.yaml`（構造的資料），本章的指令都從這個目錄出發：

```yaml
# ledger.yaml: one team's agent-failure ledger (constructed for this chapter, not a real team's data)
# Log an incident when an agent got something wrong AND a person spent time on it.
classes:
  edits-acceptance-tests:   {checkable: yes, zero_exceptions: yes, every_session: yes}
  edits-generated-client:   {checkable: yes, zero_exceptions: yes, every_session: yes, closed: 2026-09-21}  # codegen moved to the build
  paraphrased-error-detail: {checkable: yes, zero_exceptions: no,  every_session: no}
  string-time-compare:      {checkable: yes, zero_exceptions: no,  every_session: no}
  wrong-python:             {checkable: no,  zero_exceptions: no,  every_session: yes}
  imperative-gherkin-steps: {checkable: no,  zero_exceptions: no,  every_session: no}
  unrequested-abstraction:  {checkable: no,  zero_exceptions: no,  every_session: no}

incidents:
  - {id: I-01, date: 2026-09-01, repo: booking-api, class: edits-acceptance-tests, min: 40, note: "changed the expected 409 to 400 in booking.feature"}
  - {id: I-02, date: 2026-09-02, repo: booking-api, class: paraphrased-error-detail, min: 25, note: "'Room already booked' instead of 'Taipei is taken 10:00-11:00 by Bob'"}
  - {id: I-03, date: 2026-09-03, repo: booking-api, class: wrong-python, min: 20, note: "hook ran /usr/bin/python3 3.9: No module named 'fastapi'"}
  - {id: I-04, date: 2026-09-04, repo: booking-web, class: edits-generated-client, min: 30, note: "hand-edited web/src/gen/client.ts, lost at the next codegen"}
  - {id: I-05, date: 2026-09-07, repo: booking-web, class: edits-acceptance-tests, min: 35, note: "deleted the failing UI scenario"}
  - {id: I-06, date: 2026-09-08, repo: booking-api, class: string-time-compare, min: 50, note: "'9:30' compared as a string let a double booking through"}
  - {id: I-07, date: 2026-09-09, repo: booking-api, class: paraphrased-error-detail, min: 15, note: "R2 detail returned as a pydantic error list"}
  - {id: I-08, date: 2026-09-10, repo: booking-api, class: wrong-python, min: 15, note: "ran pytest with the system python"}
  - {id: I-09, date: 2026-09-11, repo: booking-web, class: edits-generated-client, min: 20, note: "added a field to the generated client by hand"}
  - {id: I-10, date: 2026-09-17, repo: booking-api, class: paraphrased-error-detail, min: 20, note: "paraphrased 'end must be after start' despite the CLAUDE.md line"}
  - {id: I-11, date: 2026-09-22, repo: booking-api, class: string-time-compare, min: 30, note: "sorted bookings by the start string, 9:00 after 10:00"}
  - {id: I-12, date: 2026-09-24, repo: booking-api, class: unrequested-abstraction, min: 30, note: "added a repository layer and an ABC to a 40-line app"}
  - {id: I-13, date: 2026-10-05, repo: booking-web, class: edits-acceptance-tests, min: 45, note: "tools/fix_expected.py rewrote features/*.feature; Edit deny does not cover a Python subprocess"}
  - {id: I-14, date: 2026-10-06, repo: booking-web, class: imperative-gherkin-steps, min: 20, note: "steps say 'click the Book button' instead of the behaviour"}
  - {id: I-15, date: 2026-10-08, repo: booking-api, class: imperative-gherkin-steps, min: 25, note: "step text fills fields by CSS id"}

# layer: claude-md | skill (requests the model may skip) | check (test/lint/CI) | guard (hook/deny/sandbox)
rules:
  - {id: W-01, layer: claude-md, added: 2026-08-31, retired: 2026-10-27, where: "CLAUDE.md: Always add docstrings to every function (from a prompt pack)"}
  - {id: W-02, layer: claude-md, added: 2026-08-31, retired: 2026-10-27, where: "CLAUDE.md: Use TypeScript strict mode (from a prompt pack)"}
  - {id: W-03, class: edits-acceptance-tests, layer: guard, added: 2026-09-15, last_fired: 2026-10-20, where: "settings.json deny Edit(/features/**), Edit(/tests/**)"}
  - {id: W-04, class: paraphrased-error-detail, layer: claude-md, added: 2026-09-15, retired: 2026-10-27, where: "CLAUDE.md: detail strings are API, copy them verbatim"}
  - {id: W-05, class: wrong-python, layer: claude-md, added: 2026-09-15, where: "CLAUDE.md Commands: /tmp/lab/aibdd/.venv/bin/python -m pytest"}
  - {id: W-06, class: edits-generated-client, layer: guard, added: 2026-09-15, last_fired: 2026-09-16, retired: 2026-10-27, where: "deny Edit(/web/src/gen/**); codegen moved to the build on 09-21"}
  - {id: W-07, class: paraphrased-error-detail, layer: check, added: 2026-09-29, where: "every rejection scenario asserts the exact detail string"}
  - {id: W-08, class: string-time-compare, layer: check, added: 2026-09-29, where: "times parsed as datetime.time; probe with 9:30 (Ch 2)"}
  - {id: W-09, class: edits-acceptance-tests, layer: guard, scope: org, added: 2026-10-13, last_fired: 2026-10-22, where: "managed settings: strict sandbox, so W-03's Edit denies also bind subprocesses + CODEOWNERS /features/"}
  - {id: W-10, class: imperative-gherkin-steps, layer: skill, scope: org, added: 2026-10-13, where: "gherkin-style skill (Ch 28), shared via the team plugin"}
```

三塊各管一件事。`classes` 為每一類失敗回答三個分流問題（第三節細講），類別的原因被設計移除時記上 `closed`。`incidents` 一行一次失敗，`min` 是有人為它花掉的分鐘數。`rules` 記每條規則的層、加入與退役日期、guard 最後一次真的擋下東西的日子（`last_fired`，實務上從 hook 的紀錄或 CI 的失敗歷史填入），以及是不是全組織共用（`scope: org`）。注意 W-01、W-02 沒有 `class`：它們是第一天從 prompt pack 抄進來的，背後沒有任何一次失敗。

## 二、ledger.py：讓 retro 只剩下決定

`ledger.py report` 讀帳本，把 retro 該做的決定列成七種發現：

| 發現 | 條件 | 建議 |
|---|---|---|
| NEW | 一類失敗出現第 2 次，還沒有規則 | 依三個分流問題給層 |
| REPEAT | 該類最強的規則加上之後，又發生了 | 文字層就往上移；guard 就找繞過它的路 |
| ORG | 同類出現在 2 個以上 repo，規則仍是單一 repo | 修一次、共用（plugin，或組織派送、個人設定檔蓋不掉的 managed settings） |
| SUPERSEDED | 同類已有 check 或 guard，文字規則還留著 | 刪掉文字 |
| STALE | guard 已連續 `--weeks` 週（含）沒觸發，同類也沒有新事故 | 原因不在了就刪 |
| ORPHAN | 規則背後沒有任何事故 | 問是誰要的，然後刪 |
| REOPENED | 類別關閉之後又發生 | 設計上的修正失效了 |

`ledger.py` 還有一個 `gate` 子命令，第四節再用。完整原始碼（Python 3.12 + PyYAML，111 行，存在同一個目錄）：

<details><summary>點開 ledger.py</summary>

```python
"""ledger.py: turn a team's agent-failure ledger into the decisions a retro has to make.
  report  flag NEW, REPEAT, ORG, SUPERSEDED, STALE, ORPHAN and REOPENED (see the chapter's table)
  gate    fail if a commit touches agent workflow files without citing a ledger id (I-xx / W-xx)
"""
import argparse, re, subprocess, sys  # noqa: E401
from collections import Counter, defaultdict
from datetime import date, timedelta

import yaml

RANK = {"claude-md": 0, "skill": 0, "check": 1, "guard": 2}  # 0 = a request the model may skip
WORKFLOW = ("CLAUDE.md", "AGENTS.md", ".claude/", ".github/CODEOWNERS")

def layer_for(c):  # three triage questions, asked in this order
    if c["checkable"]:
        return "guard (hook / deny / sandbox)" if c["zero_exceptions"] else "check (test / lint / CI)"
    return "claude-md" if c["every_session"] else "skill"

def invalid(data, path):
    return ([f"{path}: incident {i['id']} has class '{i['class']}', which is not under classes: (add it or fix the typo)"
             for i in data["incidents"] if i["class"] not in data["classes"]]
            + [f"{path}: rule {r['id']} has layer '{r['layer']}'; use one of {', '.join(RANK)}"
               for r in data["rules"] if r["layer"] not in RANK])

def report(data, args):
    today, window, found = args.today, timedelta(weeks=args.weeks), []
    incidents = [i for i in data["incidents"] if i["date"] <= today]
    rules = [r for r in data["rules"] if r["added"] <= today and not (r.get("retired") and r["retired"] <= today)]
    by_class, rules_of = defaultdict(list), defaultdict(list)
    for i in incidents:
        by_class[i["class"]].append(i)
    for r in rules:
        rules_of[r.get("class")].append(r)
    print(f"{'class':26} {'n':>2} {'repos':>5} {'min':>4}  {'last':10}  rules")
    for name, items in sorted(by_class.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rs, repos = rules_of.get(name, []), {i["repo"] for i in items}
        closed = data["classes"][name].get("closed")
        closed = closed if closed and closed <= today else None
        print(f"{name:26} {len(items):>2} {len(repos):>5} {sum(i['min'] for i in items):>4}  {max(i['date'] for i in items)}  "
              + (" ".join(r["id"] + ":" + r["layer"] for r in rs) or "-") + (f" (closed {closed})" if closed else ""))
        if closed:  # the cause was removed (e.g. by a design change): only a new incident reopens it
            late = [i["id"] for i in items if i["date"] > closed]
            found += [f"REOPENED   {name}: {' '.join(late)} after it was closed on {closed}"] if late else []
            continue
        if not rs:
            if len(items) >= 2:
                found.append(f"NEW        {name}: {len(items)} incidents in {len(repos)} repo(s), no rule"
                             f" -> {layer_for(data['classes'][name])}")
            continue
        top = max(rs, key=lambda r: (RANK[r["layer"]], r["added"]))
        after = [i["id"] for i in items if i["date"] > top["added"]]
        if after:
            fix = "move it to check or guard" if RANK[top["layer"]] == 0 else "the enforcement leaks; find the path around it"
            found.append(f"REPEAT     {name}: {' '.join(after)} after {top['id']} ({top['layer']}, {top['added']}) -> {fix}")
        if len(repos) >= 2 and not any(r.get("scope") == "org" for r in rs):
            found.append(f"ORG        {name}: seen in {len(repos)} repos -> share one fix (plugin / managed settings)")
        found += [f"SUPERSEDED {r['id']} ({r['layer']}): {top['id']} ({top['layer']}) enforces it -> delete the text"
                  for r in rs if RANK[r["layer"]] == 0 and RANK[top["layer"]] > 0]
    for r in rules:
        if r.get("class") not in by_class:
            found.append(f"ORPHAN     {r['id']} ({r['layer']}): no incident behind it -> who needed it? ask, then delete")
        elif r["layer"] == "guard" and today - r.get("last_fired", r["added"]) >= window \
                and not any(today - i["date"] < window for i in by_class[r["class"]]):
            found.append(f"STALE      {r['id']} (guard): last fired {r.get('last_fired', 'never')} -> if the cause is gone, delete it")
    start, minutes = min((i["date"] for i in incidents), default=today), Counter()
    for i in incidents:
        minutes[(i["date"] - start).days // 14] += i["min"]
    print("minutes lost per 14 days since", start, ":", " | ".join(str(minutes[k]) for k in range((today - start).days // 14 + 1)))
    return found, found, f"{len(incidents)} incidents, {len(by_class)} classes, {len(rules)} rules, "

def gate(data, args):
    ids = {i["id"] for i in data["incidents"]} | {r["id"] for r in data["rules"]}
    git = lambda *a: subprocess.run(["git", "-C", args.repo, *a], stdout=subprocess.PIPE, text=True, check=True).stdout  # noqa: E731
    shas, lines = git("rev-list", "--reverse", args.range).split(), []
    for sha in shas:
        files = [f for f in git("show", "--format=", "--name-only", sha).split() if f.startswith(WORKFLOW)]
        if not files:
            continue
        subject = git("log", "-1", "--format=%s", sha).strip()
        cited = set(re.findall(r"\b[IW]-\d+\b", git("log", "-1", "--format=%B", sha)))
        if not cited:
            lines.append(f"FAIL {sha[:7]} {subject}: touches {' '.join(files)} but cites no ledger id (I-xx / W-xx)")
        elif cited - ids:
            lines.append(f"FAIL {sha[:7]} {subject}: cites {' '.join(sorted(cited - ids))}, not in {args.ledger}")
        else:
            lines.append(f"ok   {sha[:7]} {subject}  [{' '.join(sorted(cited))}]")
    return lines, [x for x in lines if x.startswith("FAIL")], f"{len(shas)} commits, {len(lines)} touch workflow files, "

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default="ledger.yaml", help="path to the ledger (default: ledger.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="what the next retro has to decide")
    rp.add_argument("--today", type=date.fromisoformat, default=date.today(), help="read the ledger as of YYYY-MM-DD")
    rp.add_argument("--weeks", type=int, default=4, help="a guard that has not fired for this many weeks is STALE (default 4)")
    gp = sub.add_parser("gate", help="every commit that touches CLAUDE.md, AGENTS.md, .claude/ or CODEOWNERS cites a ledger id")
    gp.add_argument("range", help="git revision range, e.g. origin/main..HEAD, or HEAD for the whole history")
    gp.add_argument("--repo", default=".", help="git repository to check (default: .)")
    args = ap.parse_args()
    data = yaml.safe_load(open(args.ledger, encoding="utf-8"))
    lines = problems = invalid(data, args.ledger)
    summary = ""
    if not problems:
        lines, problems, summary = (report if args.cmd == "report" else gate)(data, args)
    for line in lines:
        print(line)
    print(f"{summary}{len(problems)} problems")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
```

</details>

`--today` 讓同一本帳本重現任何一次 retro：日期之後的事故、規則、退役與關閉一律看不到（唯一的例外是 `last_fired`，它只記最後一次）。`--weeks` 預設 4，是兩次雙週 retro 的長度；這是本章的選擇，不是什麼標準。

### 第一次 retro（09-14）

```bash
cd /tmp/lab/aibdd/ch06-grown && source ../.venv/bin/activate
python ledger.py report --today 2026-09-14; echo "exit=$?"
```

```
class                       n repos  min  last        rules
…（略）
string-time-compare         1     1   50  2026-09-08  -
minutes lost per 14 days since 2026-09-01 : 250
NEW        edits-acceptance-tests: 2 incidents in 2 repo(s), no rule -> guard (hook / deny / sandbox)
NEW        edits-generated-client: 2 incidents in 1 repo(s), no rule -> guard (hook / deny / sandbox)
NEW        paraphrased-error-detail: 2 incidents in 1 repo(s), no rule -> check (test / lint / CI)
NEW        wrong-python: 2 incidents in 1 repo(s), no rule -> claude-md
ORPHAN     W-01 (claude-md): no incident behind it -> who needed it? ask, then delete
ORPHAN     W-02 (claude-md): no incident behind it -> who needed it? ask, then delete
9 incidents, 5 classes, 2 rules, 6 problems
exit=1
```

`string-time-compare` 只發生一次，沒有被列出：一次是軼事，第二次才是類別（[pstack Ch 30](../pstack/30-self-improvement-loop.md) 的 `/correct`：「A class counts once it has happened twice」）。團隊的決定寫成 W-03 到 W-06：兩個 guard、兩行 CLAUDE.md。注意 `paraphrased-error-detail` 的建議是 check，團隊卻選了比較便宜的 CLAUDE.md 一行（W-04）——先試便宜的也沒關係，帳本會告訴他們這個選擇撐不撐得住。W-01、W-02 被標成 ORPHAN，但沒人想在第一次 retro 為兩行字爭論，於是留著。

### 第二、三次 retro（09-28、10-12）

- **09-28**（完整輸出用 `python ledger.py report --today 2026-09-28` 重跑）的關鍵一行是 `REPEAT     paraphrased-error-detail: I-10 after W-04 (claude-md, 2026-09-15) -> move it to check or guard`：CLAUDE.md 那一行沒擋住第三次改寫錯誤訊息，團隊把它往上移成 check（W-07：每個拒絕情境都斷言完整的 detail 字串）。字串比較時間也出現第二次（NEW），加上 W-08。同一次報出的 ORG（`edits-acceptance-tests` 出現在兩個 repo）先記著，沒有處理。
- **10-12**（`--today 2026-10-12`）的關鍵一行是 `REPEAT     edits-acceptance-tests: I-13 after W-03 (guard, 2026-09-15) -> the enforcement leaks; find the path around it`：連 guard 都漏了（第三節拆解），加上 ORG 又出現一次，W-09 做成全組織共用。同一次還報了 W-04 已被 W-07 涵蓋（SUPERSEDED），以及 gherkin 寫成點擊步驟的問題出現在兩個 repo（NEW → skill），做成共用的 skill（W-10，名字沿用 [Ch 28](./28-gherkin-dsl-style.md) 的 `gherkin-style`）。

### 第四次 retro（10-26）：該拆的時候

```bash
python ledger.py report --today 2026-10-26; echo "exit=$?"
```

```
class                       n repos  min  last        rules
edits-acceptance-tests      3     2  120  2026-10-05  W-03:guard W-09:guard
paraphrased-error-detail    3     1   60  2026-09-17  W-04:claude-md W-07:check
edits-generated-client      2     1   50  2026-09-11  W-06:guard (closed 2026-09-21)
imperative-gherkin-steps    2     2   45  2026-10-08  W-10:skill
string-time-compare         2     1   80  2026-09-22  W-08:check
wrong-python                2     1   35  2026-09-10  W-05:claude-md
unrequested-abstraction     1     1   30  2026-09-24  -
minutes lost per 14 days since 2026-09-01 : 250 | 80 | 90 | 0
SUPERSEDED W-04 (claude-md): W-07 (check) enforces it -> delete the text
ORPHAN     W-01 (claude-md): no incident behind it -> who needed it? ask, then delete
ORPHAN     W-02 (claude-md): no incident behind it -> who needed it? ask, then delete
STALE      W-06 (guard): last fired 2026-09-16 -> if the cause is gone, delete it
15 incidents, 7 classes, 10 rules, 4 problems
exit=1
```

這次沒有任何 NEW 或 REPEAT，四個發現全是「拆」。W-06 擋的是手改產生的 client，而 09-21 團隊把 codegen 搬進 build，那個目錄不再進版控，guard 從此沒再觸發。團隊在 10-27 退役 W-01、W-02、W-04、W-06（帳本裡的 `retired`）。今天（11-02）再跑一次，最後兩行是 `15 incidents, 7 classes, 6 rules, 0 problems` 與 `exit=0`。

### 第一版 ledger.py 漏掉的事

第一版沒有 `closed` 這個欄位。把 `edits-generated-client` 的 `closed: 2026-09-21` 拿掉另存一份，再用 `--ledger` 指給 `ledger.py`（它是主命令的選項，要寫在 `report` 前面；寫在後面會得到 `unrecognized arguments`），11-02 跑出來是：

```
$ sed 's/, closed: 2026-09-21}/}/' ledger.yaml > ledger-no-closed.yaml
$ python ledger.py --ledger ledger-no-closed.yaml report --today 2026-11-02 | tail -2
NEW        edits-generated-client: 2 incidents in 1 repo(s), no rule -> guard (hook / deny / sandbox)
15 incidents, 7 classes, 6 rules, 1 problems
```

規則退役了，類別就被當成全新的問題再報一次——如果團隊照做，等於把剛拆掉的 guard 裝回去。問題出在混淆了兩件事：**規則退役**（這條規則不再需要）和**類別關閉**（這類失敗已經不可能發生）。pstack 的 `/correct` 說「Drop a rule once its mistake can't happen」；「can't happen」本身得是帳本裡的一筆事實，而不是規則消失的副作用。加上 `closed` 之後，該類只在關閉後又發生時才會以 REOPENED 回來。

壞掉的帳本也要說清楚怎麼修。把 I-11 的類別打錯一個字母（`sed '/I-11/s/string-time-compare/string-time-compar/' ledger.yaml > ledger-typo.yaml`），`python ledger.py --ledger ledger-typo.yaml report` 拒絕產生報告，印出 `ledger-typo.yaml: incident I-11 has class 'string-time-compar', which is not under classes: (add it or fix the typo)` 與 `1 problems`，結束碼 1。

## 底層機制：為什麼這個迴圈會收斂，而不是只會變大

`ledger.py` 本身很笨：它只比日期、數次數。讓工作流收斂的，是它在「先建立直覺」那張生命週期圖上強制的四股力量：**入口門檻**（第 2 次才立規則，one-off 進不來）、**往上移**（REPEAT 把「請求」換成「強制」）、**衰減**（SUPERSEDED、STALE、ORPHAN、closed 把規則拿掉）、**出處**（gate 讓每個 `.claude/` 改動都引用帳本，ORPHAN 進不來）。少了任何一股，規則數都只會單調增加。被餵食的工作流最缺的是**衰減**：一條沒有出處的規則，沒有人能證明它不需要，所以永遠不會被拿掉。本章的團隊在五次執行裡的數字（五次都實際跑過；09-28、10-12 兩次沒有全文照貼）：

| 執行日 | 生效規則 | 發現 | 其中「拆」的發現 |
|---|---|---|---|
| 09-14 | 2 | 6 | 2（ORPHAN） |
| 09-28 | 6 | 5 | 2 |
| 10-12 | 8 | 6 | 3 |
| 10-26 | 10 | 4 | 4 |
| 11-02 | 6 | 0 | 0 |

為什麼「請求」層會漏，而需要往上移？官方〈Best practices〉把指令分成兩種：寫在 prompt、CLAUDE.md、skill 裡的是非確定性的，「whether Claude follows that line on a given turn is still Claude's call」；hook、deny 規則、managed settings 是 harness 每次都執行的。所以 REPEAT 發生在文字層時，第一個問題不是「要不要加粗、加 IMPORTANT」，而是「能不能讓腳本判斷」。官方的說法是：「If you keep adding emphasis to a CLAUDE.md line and Claude still misses it, that's a sign the line belongs in a hook.」

## 三、挑層：三個分流問題，以及往上移、往下拆

`layer_for` 依序問三個問題，順序和 [Ch 4](./04-prompt-files-to-skills.md)「對比與取捨」那張表的讀法一致：

1. **能不能讓一支腳本判斷對錯**（`checkable`）？能，再問第 2 題；不能，跳到第 3 題。
2. **是不是一次都不能發生、沒有例外**（`zero_exceptions`）？是 → guard（hook、deny、sandbox，事前擋住）；否 → check（測試、lint、CI，事後一定抓到）。
3. **是不是每個 session 都需要知道**（`every_session`）？是 → CLAUDE.md 一行；否 → skill，用到時才載入。

為什麼不乾脆全放 guard？官方文件說 hook 是給「a rule with *zero* exceptions」的，並提醒「Every hook and deny rule will occasionally fire when it shouldn't, and someone has to maintain each one. If you ever find yourself at forty of them, most are probably preferences that Claude could have weighed on its own.」所以也要**往下拆**：「if you have hooks for things that are only preferences, delete them and let Claude decide.」帳本讓往下拆有根據，SUPERSEDED 與 STALE 就是證據。為什麼不全放 CLAUDE.md？它每個 session 都載入，官方建議每份低於 200 行，而且它只是請求。

**guard 也會漏**。I-13 是本章最值得記住的一筆：W-03 用 deny 擋了 `Edit(/features/**)`，agent 卻寫了一支 Python 腳本去改 feature 檔。官方 permissions 文件〈What `Read` and `Edit` rules cover〉寫明，這類規則涵蓋內建檔案工具、`cat`、`sed`、`tee` 等它認得的 Bash 指令與重導向，但「don't apply to ... arbitrary subprocesses that read or write files indirectly, like a Python or Node script that opens files itself」，要 OS 層級的保證得開 sandbox。所以 W-09 用 managed settings 替全組織開 sandbox：文件說它在 OS 層級生效，指令啟動的子行程也受限（macOS 用 Seatbelt），而 `Edit` 的 deny 規則會併進 sandbox 的寫入黑名單 `sandbox.filesystem.denyWrite`，W-03 因此也管得到那支 Python 腳本。sandbox 預設還留一個逃生口：被擋的指令可以請求在 sandbox 外重試（`bypassPermissions` 模式下連問都不問），所以 W-09 同時設 `allowUnsandboxedCommands: false`，也就是文件說的 strict sandbox mode。最後加上 `/features/` 的 CODEOWNERS，讓改到驗收契約的 PR 一定會送到 QA 手上。**本章沒有實測 sandbox 的攔截**（需要開 sandbox 的 Claude Code session），以上依官方文件；你可以在自己的 session 開 sandbox 後，請 agent 用 `python -c` 寫入 `features/` 來驗證。

## 四、把 `.claude/` 當程式碼審：CODEOWNERS 與出處閘門

為什麼 `.claude/` 要走 review？官方〈Choose who a rule applies to〉：「`.claude/settings.json` and the hooks in it belong to the repo. Once you commit them, they apply to everyone who works in the repo.」改 `.claude/` 就是改每個同事的 agent（[ai_applications Ch 6](../ai_applications/06-claude-code-advanced.md) 的「一個 team 推廣 Claude Code 的設法」正是靠 commit `.claude/` 讓全組拿到同一套環境）；個人偏好有自己的位置（`.claude/settings.local.json`、`CLAUDE.local.md`）。下面用一支腳本建一個歷史和帳本對得上的 `booking-api` repo，其中兩個 commit 是錯的示範。`make_demo_repo.sh`：

```sh
#!/bin/sh
# make_demo_repo.sh: build booking-api/, whose history mirrors ledger.yaml. Some commits cite the ledger, two do not.
set -e
rm -rf booking-api && mkdir booking-api && cd booking-api && git init -q
c() { git add -A && git -c user.name=Alice -c user.email=alice@example.com commit -q -m "$1"; }
mkdir features && echo 'Feature: Room booking' > features/booking.feature && echo 'app = None' > app.py
c "Initial booking API"
printf '# booking-api\n- Always add docstrings to every function\n- Use TypeScript strict mode\n' > CLAUDE.md
c "Add a community best-practices pack to CLAUDE.md"
mkdir .claude && echo '{"permissions": {"deny": ["Edit(/features/**)", "Edit(/tests/**)"]}}' > .claude/settings.json
c "W-03: deny edits to features/ and tests/ (I-01, I-05)"
printf -- '- detail strings are API: copy them verbatim\n- Tests: /tmp/lab/aibdd/.venv/bin/python -m pytest -q\n' >> CLAUDE.md
c "W-04, W-05: detail strings and the venv python (I-02, I-07, I-03, I-08)"
echo '{"permissions": {"deny": ["Edit(/features/**)", "Edit(/tests/**)", "Bash(git push *)"]}}' > .claude/settings.json
c "Tidy settings.json (W-11)"
mkdir .github && printf '/CLAUDE.md @acme/agent-workflow\n/.claude/ @acme/agent-workflow\n/features/ @acme/qa\n' > .github/CODEOWNERS
c "W-09: owners for workflow files and the acceptance contract (I-13)"
cat > CLAUDE.md <<'EOF'
# booking-api

## Commands

<!-- W-05 <- I-03, I-08: hooks and pytest ran /usr/bin/python3 3.9 -->
- Tests: `/tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short`

## Contract

<!-- W-03, W-09 block edits here; W-07 asserts exact detail strings -->
- `features/` is the acceptance contract. Its `detail` strings are part of the API.
EOF
c "Retire W-01, W-02 (ORPHAN) and W-04 (SUPERSEDED by W-07)"
```

**CODEOWNERS** 的語意依 GitHub 文件〈[About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)〉：檔案可以放在 `.github/`、根目錄或 `docs/`，GitHub 依序找、用第一個找到的；「Order is important; the last matching pattern takes the most precedence」；owner 必須對 repo 有 write 權限；要讓 owner 的核准變成**必要**，得在 branch protection 或 ruleset 打開「Require review from Code Owners」。`@acme` 是虛構的組織，**本章沒有在 GitHub 上實測這些設定的強制效果**。推上 GitHub 之後，可以用 `gh api repos/<owner>/<repo>/codeowners/errors` 檢查語法；我們對 `cli/cli` 實際查詢，回傳 `{"errors":[]}`。

CODEOWNERS 管「誰審」（[harness Ch 43](../harness_engineering/43-agents-in-team-workflow.md) 的「問題一：誰為這份輸出負責？」用同一個機制處理 agent 產出的問責），管不到「為什麼改」。出處閘門管後者：`ledger.py gate` 檢查每個碰到 `CLAUDE.md`、`AGENTS.md`、`.claude/`、`.github/CODEOWNERS` 的 commit，訊息裡有沒有引用帳本裡存在的 I-xx 或 W-xx：

```bash
sh make_demo_repo.sh
python ledger.py gate --repo booking-api HEAD; echo "exit=$?"
```

```
FAIL dd08859 Add a community best-practices pack to CLAUDE.md: touches CLAUDE.md but cites no ledger id (I-xx / W-xx)
ok   916aaa5 W-03: deny edits to features/ and tests/ (I-01, I-05)  [I-01 I-05 W-03]
ok   9181337 W-04, W-05: detail strings and the venv python (I-02, I-07, I-03, I-08)  [I-02 I-03 I-07 I-08 W-04 W-05]
FAIL 0f1c105 Tidy settings.json (W-11): cites W-11, not in ledger.yaml
ok   6977f2a W-09: owners for workflow files and the acceptance contract (I-13)  [I-13 W-09]
ok   6e0cc1d Retire W-01, W-02 (ORPHAN) and W-04 (SUPERSEDED by W-07)  [W-01 W-02 W-04 W-07]
7 commits, 6 touch workflow files, 2 problems
exit=1
```

（commit hash 每次重建都不同。）第一個 FAIL 正是 ORPHAN 的源頭：prompt pack 進來的那一刻就沒有出處。第二個 FAIL 引用了不存在的 W-11——引用格式對了，帳本裡卻沒有這條。在 CI 上可以只檢查這個 PR 的範圍，例如 `python ledger.py gate origin/main..HEAD`。

**出處不必花 token**。最後一個 commit 的 CLAUDE.md 把出處寫成獨立一行的 HTML 註解。官方 memory 文件說「Block-level HTML comments ... in CLAUDE.md files are stripped before the content is injected into Claude's context」。我們在 2.1.295 實測一次：

```bash
cd booking-api && mkdir -p ../logs && claude -p "Without using any tools, quote verbatim every line of the project CLAUDE.md content you were given, in order, including any HTML comments. Then answer in one line: did that content include any HTML comment (yes/no)?" \
  --tools "" --max-turns 1 --setting-sources project --no-session-persistence --output-format json \
  < /dev/null > ../logs/strip-test.json 2> ../logs/strip-test.err
```

> 以下為實際執行紀錄，每次執行結果不同。執行日期 2026-11-02，1 turn，`total_cost_usd` 約 0.025（客戶端估算）。內容是 `strip-test.json` 的 `result` 欄位，原樣照貼（模型自己加了一層 markdown fence）。

~~~
```markdown
# booking-api

## Commands

- Tests: `/tmp/lab/aibdd/.venv/bin/python -m pytest -q --tb=short`

## Contract

- `features/` is the acceptance contract. Its `detail` strings are part of the API.
```

No, the content did not include any HTML comment.
~~~

兩行出處註解都沒有進到模型的 context。附帶一提，[Ch 4](./04-prompt-files-to-skills.md) 的 `context_cost.py` 算的是檔案位元組，在 `ch06-grown/` 跑 `python /tmp/lab/aibdd/ch04-prompt-files/context_cost.py booking-api`，對這份 CLAUDE.md 回報 `336 B`；去掉兩行註解剩 193 B，前者高估了真正進入 context 的量。

## 五、改動有沒有用：回放、趨勢、行為 eval

**回放**最便宜，也最該先做：新的檢查要在**過去那次失敗的 code** 上紅，在修好的 code 上綠（pstack Ch 30 的「Prove each new check fails on a real past mistake」）。W-08 是 [Ch 2](./02-vibe-coding-vs-engineering.md) 的 `9:30` 探針，事故當時的 code 就是 Ch 2 的 vibe 版本（沿用 Ch 2 建好的實驗目錄）：

```bash
cd /tmp/lab/aibdd/ch02-vibe && APP=vibe.app python -m pytest -q --tb=line probes -k unpadded
APP=fixed.app python -m pytest -q probes -k unpadded
```

```
F                                                                        [100%]
…（略）
/private/tmp/lab/aibdd/ch02-vibe/probes/test_acceptance.py:36: AssertionError: assert ['Bob', 'Alice'] == ['Bob']
…（略）
1 failed, 4 deselected in 0.13s
.                                                                        [100%]
1 passed, 4 deselected in 0.14s
```

**趨勢**是第二層，而且是弱證據。`minutes lost per 14 days` 從 250 掉到 80、90、0，看起來規則有效，但這組數字是構造的、事故總共 15 筆、團隊越到後面越懶得記帳也會讓曲線往下掉。把它當煙霧偵測器：數字回升時一定要看，數字下降時不要拿去證明什麼。

**行為 eval** 最貴：把事故當時的 prompt 存下來，在有、無這次改動的設定下各重跑 N 次，比較成功率與變異。skill 的觸發率可以用 [Ch 5](./05-skill-creator.md) 的 `run_eval.py` 量，eval 的設計見 [harness Ch 34](../harness_engineering/34-eval.md)。**本章沒有實測行為 eval。** 另外兩個該追的是成本：常駐 context 的位元組（Ch 4 的 `context_cost.py`），以及 guard 的總數（官方那句「forty of them」）。

## 六、第一天就裝一整套：量量看裝進了什麼

我們在空目錄實際跑一次 Spec Kit 1.2.5 的初始化（指令細節見 [Ch 12](./12-spec-kit-1x.md)），再用 Ch 4 的 `context_cost.py` 量：

```bash
cd /tmp/lab/aibdd/ch06-grown && mkdir day-one && cd day-one
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify init --here --force --non-interactive --integration claude --script sh
find .claude .specify -type f | wc -l; find .claude .specify -name '*.md' -print0 | xargs -0 cat | wc -l
python /tmp/lab/aibdd/ch04-prompt-files/context_cost.py . | tail -2
```

```
      30
    3134
  every turn: 1,339 B   only when used: 135,302 B
1 projects, 0 problems
```

30 個檔、3,134 行 markdown（skill 的指令加上範本）、10 個 skill。每輪常駐只有 1,339 B，因為它用 skill 做漸進揭露（Ch 4）；**問題不在 token**。問題在於這 3,134 行裡，沒有一行能對到你帳本裡的任何一次失敗，所以第一次 retro 就會冒出一長串 ORPHAN，而且沒人說得出哪一行能刪。Spec Kit 自己也承認規則得由你來寫：裝好的 `.specify/memory/constitution.md` 是一份範本，第一個原則寫著 `[PRINCIPLE_1_NAME]`。

這不是在說 Spec Kit 不好——本課 Part 3 整段都在用它。差別在順序：Part 3 是因為「agent 做出不是我們要的東西」這一類失敗，才把規格流程拉進來，並且用 preset 與 constitution 改成自己的樣子（Ch 17）。**用拉的，不用推的**；照單全收別人的整套設定，等於 [pstack Ch 31](../pstack/31-critique-tradeoffs.md) 警告的「照抄 poteto 的個人偏好」。被餵食的工作流正在失敗的訊號：

| 訊號 | 帳本或工具上看到什麼 |
|---|---|
| 規則說不出來源 | ORPHAN 一長串；gate 在引入那個 commit 就 FAIL |
| 文字規則擋不住 | 同一類在規則之後還有 REPEAT |
| 規則只增不減 | 生效規則數單調上升，從來沒有 SUPERSEDED 或 STALE 被處理 |
| 大家繞過它 | 有人開 `--safe-mode`、關掉 plugin；官方 plugin 介面的 Installed 分頁有「Not used recently」群組 |
| 升級時改動消失 | 直接改裝進來的 skill，下一次升級被蓋掉（Ch 17 的題目） |

## 對比與取捨

| 做法 | 第一天 | 每條規則有出處嗎 | 會變小嗎 | 多個 repo | 適合 |
|---|---|---|---|---|---|
| 第一天裝框架或 prompt pack | 樣樣齊全 | 沒有 | 幾乎不會 | 每個 repo 複製一份 | 當參考書讀，不當規格裝 |
| 各自在聊天裡糾正 | 什麼都沒有 | 只在對話裡 | 不累積，也談不上變小 | 每人每 repo 重來 | 一個人、一次性的專案 |
| pstack 的 `/reflect`、`/correct` | 很小 | 有（session、git 歷史） | 有（「Drop a rule once...」） | 以單一 repo 為主 | 個人或單一 repo |
| 帳本驅動（本章） | 一本空帳本 | 有（I-xx、W-xx） | 有（SUPERSEDED、STALE、ORPHAN） | ORG 發現，加上 plugin 或 managed settings | 團隊、多個 repo |

多個 repo 時，官方的觸發點是「A second repository needs the same setup → Package it as a plugin」；一定要全組織生效、不能被個人覆寫的規則（例如 `permissions.deny`）放 managed settings。兩條路不能互換：plugin 帶得走 skill、hook、MCP server，帶不走權限與 sandbox 設定（plugin 自己的 `settings.json` 只認 `agent` 與 `subagentStatusLine`，其他鍵一律丟掉），所以 W-10 走 plugin、W-09 走 managed settings。plugin 的代價也要記得：「An enabled plugin is part of every session, not only the sessions where you use it.」

## 踩雷集錦

1. **「先裝一套 best practices，之後再慢慢刪」** → 沒有出處的規則沒有人敢刪。本章的 W-01、W-02 撐過三次 retro，直到第四次「拆」成為主要議題才退役。要裝，就讓每條都在帳本裡有一筆理由。
2. **「失敗一次就立一條規則」** → `unrequested-abstraction` 八週只發生一次。官方文件與 pstack 都以「第二次」為門檻；一次是軼事，先記帳就好。
3. **「deny 擋住了 Edit，就擋住了改檔」** → I-13 用一支 Python 腳本繞過了 W-03。deny 規則不涵蓋自己開檔的子行程；要 OS 層級的保證得開 sandbox，並關掉「到 sandbox 外重試」的逃生口（strict 模式），再用 CODEOWNERS 讓人審契約的改動。
4. **「事故變少，所以規則有效」** → 趨勢是弱證據：樣本小、記帳的人會累。先回放（新檢查在舊失敗上要紅），再談趨勢與行為 eval。
5. **「`.claude/` 是我的個人設定」** → commit 進去的 `.claude/settings.json` 對 repo 裡的每個人都生效。個人偏好放 `.claude/settings.local.json` 或 `CLAUDE.local.md`；共用的改動走 CODEOWNERS 與 gate。

## 進階：再往深一層

- **把 gate 接成必要檢查，並自動填 `last_fired`**：CI 跑 `python ledger.py gate origin/main..HEAD`，在 branch protection 把它和「Require review from Code Owners」都設成必要（本章沒有在 GitHub Actions 上實測）。checkout 時要把 `origin/main` 一起抓下來：範圍解析不到時 git 回 `fatal: ambiguous argument 'origin/main..HEAD': unknown revision ...`，gate 以 1 結束。`last_fired` 則讓 guard 類的 hook 擋下東西時附加一行 JSONL，retro 前用腳本把最後日期寫回帳本。
- **從對話紀錄挖候選事故**：pstack 的 `/reflect` 與 `/automate-me` 讀 `~/.claude/projects/<slug>/*.jsonl`（[pstack Ch 30](../pstack/30-self-improvement-loop.md)）；挖出來的只是候選，進帳本前要有人確認。
- **組織層級**：一個專門的 workflow repo 放帳本與 plugin marketplace；managed settings 放零例外的規則；官方也提供全機器生效的 managed CLAUDE.md（macOS 在 `/Library/Application Support/ClaudeCode/CLAUDE.md`），文件建議它只放指引、強制的部分交給 managed settings。
- **`/doctor prompt-audit`**：官方 memory 文件說它（v2.1.283 起）會找出為舊模型寫的指示、指向不存在檔案的指示、彼此矛盾的檔案。它看不到你的帳本，所以找不到 ORPHAN；兩者互補。本章未實測。

## 動手練習

**兩週 retro**。在你的一個 repo（或本章的 `booking-api`）照下面的節奏跑一次完整週期：

1. 開一本空的 `ledger.yaml`（`classes: {}`、`incidents: []`、`rules: []`；`classes` 是 mapping，不是清單），把現有 CLAUDE.md 的每一行都登記成一條沒有 `class` 的規則。先跑一次 `report`：還沒有任何事故，每一行都是 ORPHAN。
2. 兩週內，每當 agent 出錯且你花了超過 5 分鐘處理，就記一筆 incident（5 分鐘是本練習的門檻，不是標準）。
3. retro 當天跑 `python ledger.py report --today <日期>`，把每個發現填進下面的表，決定才寫規則。
4. 每條新規則開一個 PR，commit 訊息引用 I-xx／W-xx，跑 `python ledger.py gate origin/main..HEAD` 確認通過（`booking-api` 沒有 remote，就在分支上改跑 `python ledger.py gate --repo booking-api main..HEAD`）。
5. 第二次 retro 先看 REPEAT 與 ORPHAN：哪些規則被證明有用、哪些可以拆？

```markdown
# Retro YYYY-MM-DD（ledger.py report：N incidents, M problems；下次 retro：YYYY-MM-DD）
| 發現 | 決定（立規則／往上移／修漏洞／刪除／再觀察） | 規則 | 層 | owner | PR |
|---|---|---|---|---|---|
| REPEAT <class> | 往上移 | W-xx | check | Bob | #12 |
```

預測題：把 `--weeks` 改成 2，分別用 `--today 2026-10-26` 與 `--today 2026-11-09` 跑。哪一次會多出 STALE？如果多出來了，為什麼那幾條不該照建議刪掉？先回答再跑。

## 本章重點整理

- 工作流靠帳本長出來：先記錄失敗，第二次才立規則，每條規則都要能對到帳本裡的 I-xx。
- 三個分流問題決定層：能不能讓腳本判斷、是不是零例外、是不是每個 session 都需要；REPEAT 把文字規則往上移，guard 的 REPEAT 代表有繞路。
- 收斂靠四股力量：入口門檻、往上移、衰減（SUPERSEDED、STALE、ORPHAN、closed）、出處（gate）；被餵食的工作流缺的是衰減。
- `.claude/` 是每個同事的 agent 設定，用 CODEOWNERS 決定誰審、用 gate 確認為什麼改；出處寫成 CLAUDE.md 的獨立註解行不花 token（2.1.295 實測）。
- 判斷改動有沒有用：先回放、再看趨勢、最後才是行為 eval；趨勢是弱證據。

## 自我檢核

- [ ] 不看本章，說出 `ledger.py` 的七種發現各自的觸發條件，以及哪幾種是「拆」。
- [ ] 為什麼「規則退役」和「類別關閉」要分開記？第一版沒分開時出了什麼事？
- [ ] I-13 為什麼能繞過 W-03？W-09 用哪兩個機制補上，各自在什麼時間點生效？
- [ ] 一條規則從 CLAUDE.md 被移到 check 之後，原本那一行該怎麼處理？帳本怎麼提醒你？
- [ ] 為什麼「每輪只有 1,339 B」不足以替第一天裝整套框架辯護？
- [ ] 回放、趨勢、行為 eval 各證明了什麼、沒證明什麼？

## 延伸閱讀

- **[Extend Claude Code](https://code.claude.com/docs/en/features-overview)** — Claude Code 官方文件
  - **讀哪裡**：〈Build your setup over time〉那張「trigger → 加什麼」的表，以及〈Understand context costs〉。
  - **學到什麼**：官方建議的工作流生長順序，從 CLAUDE.md 到 plugin，以及每個機制的常駐成本。
  - **和本章的關聯**：本章的帳本把這張表的「trigger」變成可以計數、可以檢查的紀錄。
- **[Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)** — Claude Code 官方文件
  - **讀哪裡**：〈Deterministic and non-deterministic instructions〉、〈Keep CLAUDE.md short〉、〈Choose who a rule applies to〉。
  - **學到什麼**：請求與強制的分界、什麼值得寫進 CLAUDE.md、規則放在哪個設定檔決定了它對誰生效。
  - **和本章的關聯**：第三節的分流問題與「往上移、往下拆」都以這幾段為依據。
- **[Postmortem Culture: Learning from Failure](https://sre.google/sre-book/postmortem-culture/)** — John Lunney、Sue Lueder，《Site Reliability Engineering》第 15 章（Google）
  - **讀哪裡**：postmortem 的定義、blameless 的做法、事故存放庫與定期回顧。
  - **學到什麼**：一個成熟的「從失敗學習」制度怎麼避免流於形式。
  - **和本章的關聯**：失敗帳本是給 agent 用的輕量 postmortem；書裡「an unreviewed postmortem might as well never have existed」對帳本同樣成立。
- **《Systemantics: How Systems Work and Especially How They Fail》** — John Gall（Quadrangle，1977；1975 年初版名為 General Systemantics，2002 年版改名《The Systems Bible》）
  - **讀哪裡**：Gall's law 所在的段落（原書並沒有用「Gall's law」這個名字稱呼它）。
  - **學到什麼**：為什麼從零設計的複雜系統很少能運作，而能運作的複雜系統幾乎都是從能運作的簡單系統長出來的。
  - **和本章的關聯**：「長出來，而不是裝上去」的最早表述之一。

這一章讓工作流有了生長與修剪的節奏；Part 1 的最後一步，是把提案、約束、回饋這個最小迴圈完整地親手跑一遍，帳本裡的前幾筆事故會從那裡來。

→ [練習 A：跑一次完整的提案、約束、回饋迴圈](./practice-a-first-loop.md)
