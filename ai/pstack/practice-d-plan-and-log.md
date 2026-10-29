# 練習 D — 寫一份過得了 check-plan 的計畫與決策日誌

> **目標**：把 Ch 25–27 拼起來。你要為一個跨兩個 PR 的修正，依 Multi-phase plan playbook 寫一份計畫，讓 pstack 自己的 `check-plan.mjs` 印出 `0 problems`；同時用 `log.sh` 留下這次規劃的決策日誌，讓 Ch 26 的稽核工具確認每一列的證據都能解析。最難的部分不是讓兩支工具通過，而是找出**它們都通過了、但計畫或日誌仍然是錯的**那些地方。

> **環境**：Node.js 20 以上、git、`log.sh`（pstack 附）、`check-plan.mjs`（pstack 附）、Ch 26 的 `audit-log.mjs`。參考解答的計畫、產生器、日誌與所有工具輸出都是實際跑出來的。

## 背景與動機

練習 B 審了一個錯誤的 PR，並在參考解答裡給出一個修正方向：把「已送出」持久化。那個方向還留下一個風險——推送成功之後、`markSent` 之前崩潰，重試時會再送一次。完整的修正需要兩步：

- **N1**：把已送出的狀態持久化到 store，重試時跳過已送出的使用者
- **N2**：每次推送帶一個冪等鍵，讓推送服務自己去重，關掉 N1 留下的崩潰視窗

這是一個典型的「多階段計畫」：兩個 PR、有依賴關係、有一個要 operator 看過才能合併的使用者可見改動。你要寫的不是程式碼，是計畫本身——一份 owner 能逐格打勾、operator 能從證據稽核的清單。

## 任務規格

| 交付物 | 驗收標準 |
|---|---|
| `plan.md` | `node check-plan.mjs plan.md` 印出 `2 PR sections, 0 problems` 並 exit 0 |
| N1 的 review gate | 有三個方框（截圖、影片、在 chat 等 operator），因為它改變了使用者收到的東西 |
| N2 的 review gate | `None. N2 is not review-gated.`，下面沒有方框 |
| 每個 PR 的十條 lane | Lane 1 是「Regression lane against trunk」；**至少兩條是這個 PR 特有的場景**（不是從另一個 PR 複製過來的） |
| Appendix A | 引用一個真實存在的 prototype commit，而且描述和那個 commit 實際做的事一致 |
| `planning.tsv` | 至少 4 列（frame、prototype、write、check）；`node audit-log.mjs planning.tsv <repo>` 印出 `0 with unresolved evidence` |
| 對帳 | 你自己讀過每一列，確認 evidence 顯示了那一列宣稱的東西；有錯就追加一列取代，不改舊列 |

**規則**：計畫裡的散文不准有長破折號、彎引號、句中冒號（check-plan 會擋）；日誌只能用 `log.sh` 寫入。

## 期望輸出範例

```
Persist sent status in the store (N1)  boxes=27  files=3 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=3 merge=4
Send an idempotency key with each push (N2)  boxes=23  files=2 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=0 merge=4
2 PR sections, 0 problems
```

```
7 rows, 0 with unresolved evidence
```

## 如果你卡住了

1. **「mid-sentence colon」一直出現**：check-plan 在 code 之外，對任何「冒號、空白、非空白字元」報錯。「Pass when: X」「Goal: Y」都會被抓。把它改成「Pass when X」「The goal is Y」。冒號只能出現在 inline code 裡（例如 `` `notification:<userId>` `` 沒問題，因為反引號裡的內容會被略過）。
2. **「does not open with the rule」**：三個驗證區塊的粗體標題後面，第一句必須**逐字**是 `Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.` 一個字都不能改。
3. **「lacks "Ten lanes on `<swarm workers model>` at the PR head"」**：模型名稱要填進反引號裡，例如 ``Ten lanes on `sonnet` at the PR head``。
4. **十條 lane 寫到第五條就想不出來了**：從失敗與邊界想——空輸入、id 為 0、重跑兩次、兩個 job 同時跑、store 寫入失敗、大量資料、在某個步驟中間被殺掉。練習 A 的測試用例表是一個好起點。
5. **兩個 PR 的 lane 很像，要不要用程式產生？**：可以（參考解答就是用模板產生的，Build the Lever）。但產生器會讓你很容易把 N1 的場景整批複製給 N2——然後 N2 沒有任何一條 lane 在測它自己要解決的問題。check-plan 看不出這件事。

## 實作步驟建議

### Step 1：Frame 並寫第一列日誌

用一句話寫出兩個 PR 的分工與理由，`log.sh planning.tsv frame ...`，evidence 指向一個真實的檔案（例如練習 B 的 `retry-sim.mjs`）。

### Step 2：用 prototype 回答開放問題

開放問題是「把已送出的狀態持久化，夠不夠擋住重試造成的重複？」練習 B 的參考修正（`review/root-cause` branch）就是一個 prototype。找出它的 commit SHA，寫一列 prototype 日誌。

### Step 3：寫計畫

從 Multi-phase plan playbook 的骨架開始（或用參考解答的模板），填滿每一個佔位符。

### Step 4：跑 check-plan，修到 0

一次修一類錯誤，每修完跑一次。

### Step 5：自己讀一次，找工具看不到的錯

逐條讀 N2 的十條 lane：哪幾條在測 N2 特有的行為？讀 Appendix A：它描述的 prototype，和那個 commit 實際做的事一致嗎？讀每一列日誌：evidence 真的顯示了那一列說的東西嗎？

### Step 6：修正並記錄

計畫有錯就改計畫、重跑 check-plan、記一列；日誌有錯就**追加一列取代**，不改舊列。最後跑稽核工具。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

### 用模板產生計畫

兩個 PR 的結構完全相同（九個子區塊、十條 lane、四項 perf），手寫兩份很容易漂移。照 Build the Lever，用一個模板加一支小產生器：

```markdown
<!-- pr-template.md：一個 PR 的區段，{} 是佔位符 -->
## {title} ({pid})

**Depends on.** {depends}

**Files.**

{files}

**Build.**

- [ ] {build}

**You see.**

- [ ] {see}

**Verify, unit.** {RULE}

- [ ] {unit} Run `node --test test/`.

**Verify, live.** {RULE} Ten lanes on `sonnet` at the PR head, per the boot recipe.

{lanes}

**Verify, perf.** {RULE}

- [ ] Metric. Wall time of one job run over 500 users, at trunk and at head.
- [ ] Probe. Run `node bench/job.mjs 500` at trunk and head, interleaved, five runs each.
- [ ] Baseline. Record the trunk median first.
- [ ] Rule. Head median more than 20 percent above the trunk median fails.

{gate}

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The operator lands the stack bottom-up.
```

```python
# gen.py：用 pr-template.md 與 plan-skeleton.md 產生 plan.md（Python 3.9 的 f-string 不能含反斜線，所以用 format）
RULE = "Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked."
def lanes(pr, scen):
    out = []
    for i, (s, p) in enumerate(scen, 1):
        prefix = "Regression lane against trunk. " if i == 1 else ""
        out.append("- [ ] Lane %d. %s%s Save `%s-lane%d.png`. Pass when %s." % (i, prefix, s, pr, i, p))
    return "\n".join(out)
pr1 = [
 ("Run the retry scenario at trunk and head with the push stub failing user 2 once.", "head delivers users 1, 2, and 3 once each and trunk shows user 1 twice"),
 ("Run a clean job with no failures.", "every user gets one message"),
 ("Run the job twice in a row with no failures.", "the second run sends nothing"),
 ("Fail the push for user 3 twice and let the job retry twice.", "user 3 gets one message after the second retry"),
 ("Run a job with a user whose id is 0.", "that user gets one message"),
 ("Run a job against an empty user table.", "the job exits 0 and sends nothing"),
 ("Kill the job after user 1 is marked sent, then rerun it.", "user 1 is not sent again"),
 ("Run two jobs at once against the same table.", "the second job waits on the table lock and sends nothing twice"),
 ("Make the store reject markSent once.", "the job exits non-zero and the operator log names the user"),
 ("Run a job with 500 users.", "the job finishes and every user is marked sent"),
]
pr2 = [(s.replace("retry scenario", "retry scenario with idempotency keys"), p) for s, p in pr1]
pr2[6] = ("Kill the job after the push for user 1 returns and before markSent runs, then rerun it.", "the push stub records one delivery for the key of user 1")
pr2[7] = ("Send the same key twice from two jobs started one second apart.", "the push stub records one delivery and the second job logs a duplicate-key response")
GATE_FULL = ("**Review gate.** The operator reviews before merge.\n\n"
  "- [ ] Copy lane 1 screenshots into `docs/media/{pid}-review-retry.png`.\n"
  "- [ ] Record a 30 to 60 second video of the retry run on a lane VM. Save it as `docs/media/{pid}-review.mp4`.\n"
  "- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.")
TEMPLATE = open("pr-template.md").read()
def section(title, pid, depends, files, build, see, unit, scen, gate_none):
    gate = ("**Review gate.** None. %s is not review-gated." % pid) if gate_none else GATE_FULL.format(pid=pid)
    return TEMPLATE.format(title=title, pid=pid, depends=depends, files=files, build=build, see=see, unit=unit,
                           RULE=RULE, lanes=lanes(pid, scen), gate=gate)
s1 = section("Persist sent status in the store", "N1", "None.",
 "- [ ] Edit `src/notify.mjs`.\n- [ ] Edit `src/store.mjs`.\n- [ ] Create `test/retry.test.mjs`.",
 "Make `sendAll` call `store.markSent` after each send and skip users whose stored status is `sent`.",
 "After a retry, `node scripts/dump.mjs` prints every user with status `sent` and the push stub log shows one message per user.",
 "`test/retry.test.mjs` gains the case where user 2 fails once and the job retries.", pr1, False)
s2 = section("Send an idempotency key with each push", "N2", "N1.",
 "- [ ] Edit `src/notify.mjs`.\n- [ ] Edit `test/retry.test.mjs`.",
 "Pass `notification:<userId>:<campaignId>` as the idempotency key on every `transport.send` call.",
 "The push stub log shows the same key on a retried send and records one delivery for it.",
 "`test/retry.test.mjs` gains the case where the job dies between send and `markSent`.", pr2, True)
open("plan.md", "w").write(open("plan-skeleton.md").read().replace("{RULE}", RULE).replace("{S1}", s1).replace("{S2}", s2))
```

`plan-skeleton.md` 是計畫中 PR 區段以外的部分（標題、摘要、How to read this、Program checklist、Close the program、附錄），在 `{S1}{S2}` 的位置插入兩個 PR 區段。它的內容就是下面完整計畫裡除了兩個 PR 區段以外的文字。

### 完整計畫（產生的 plan.md，180 行）

```markdown
# Notify exactly-once plan

Users get each notification once, even across retries.
The rule this program enforces is that delivery state lives in the store, not in memory.
PR ids in order are N1 then N2.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator merges. N1 and N2 stop at merge-ready.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported. Log this tick's row in your decision trail."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per PR with the full lifecycle the execution playbook names.
- [ ] Follow this dependency graph.
  - [ ] N1 is first and branches from `main`.
  - [ ] N2 after N1, based on the N1 branch.
- [ ] Hold the file boundaries. N1 touches only `src/notify.mjs`, `src/store.mjs`, and `test/`. N2 touches only `src/notify.mjs` and `test/`.
- [ ] Hold the review gate. N1 changes what users receive. It waits for the operator's review in chat with screenshots and a video before merge.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`. Never require `gt`.
- [ ] Open the PR ready, never draft, per **Opening a PR**. A stack child targets its parent branch.
- [ ] Run `/deslop` before each commit and `/no-comments` before review.

### Verdict and merge, for every PR

- [ ] A full swarm runs once, at the code-ready head, per `pstack/skills/swarm/SKILL.md`. One gates lane, the ten live lanes, the perf lane, and two audit lanes that distrust the PR body.
- [ ] Judge every finding by the execution playbook's blocking rule. A failure on trunk behavior in a lane marked `Regression lane against trunk.` does not block the PR.
- [ ] The operator lands the stack bottom-up. A new head gets a fresh verdict unless the patch-id rule in `playbooks/shipping.md` carries it.

### Boot recipe, for every live lane

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Run `npm ci`, seed the table with `node scripts/seed.mjs`, and wait for `seeded 3 users` in the log.
- [ ] Drive the job only through `control-cli`. Read the store with `node scripts/dump.mjs` and nothing else.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Persist sent status in the store (N1)

**Depends on.** None.

**Files.**

- [ ] Edit `src/notify.mjs`.
- [ ] Edit `src/store.mjs`.
- [ ] Create `test/retry.test.mjs`.

**Build.**

- [ ] Make `sendAll` call `store.markSent` after each send and skip users whose stored status is `sent`.

**You see.**

- [ ] After a retry, `node scripts/dump.mjs` prints every user with status `sent` and the push stub log shows one message per user.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `test/retry.test.mjs` gains the case where user 2 fails once and the job retries. Run `node --test test/`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `sonnet` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the retry scenario at trunk and head with the push stub failing user 2 once. Save `N1-lane1.png`. Pass when head delivers users 1, 2, and 3 once each and trunk shows user 1 twice.
- [ ] Lane 2. Run a clean job with no failures. Save `N1-lane2.png`. Pass when every user gets one message.
- [ ] Lane 3. Run the job twice in a row with no failures. Save `N1-lane3.png`. Pass when the second run sends nothing.
- [ ] Lane 4. Fail the push for user 3 twice and let the job retry twice. Save `N1-lane4.png`. Pass when user 3 gets one message after the second retry.
- [ ] Lane 5. Run a job with a user whose id is 0. Save `N1-lane5.png`. Pass when that user gets one message.
- [ ] Lane 6. Run a job against an empty user table. Save `N1-lane6.png`. Pass when the job exits 0 and sends nothing.
- [ ] Lane 7. Kill the job after user 1 is marked sent, then rerun it. Save `N1-lane7.png`. Pass when user 1 is not sent again.
- [ ] Lane 8. Run two jobs at once against the same table. Save `N1-lane8.png`. Pass when the second job waits on the table lock and sends nothing twice.
- [ ] Lane 9. Make the store reject markSent once. Save `N1-lane9.png`. Pass when the job exits non-zero and the operator log names the user.
- [ ] Lane 10. Run a job with 500 users. Save `N1-lane10.png`. Pass when the job finishes and every user is marked sent.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Wall time of one job run over 500 users, at trunk and at head.
- [ ] Probe. Run `node bench/job.mjs 500` at trunk and head, interleaved, five runs each.
- [ ] Baseline. Record the trunk median first.
- [ ] Rule. Head median more than 20 percent above the trunk median fails.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 screenshots into `docs/media/N1-review-retry.png`.
- [ ] Record a 30 to 60 second video of the retry run on a lane VM. Save it as `docs/media/N1-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The operator lands the stack bottom-up.

## Send an idempotency key with each push (N2)

**Depends on.** N1.

**Files.**

- [ ] Edit `src/notify.mjs`.
- [ ] Edit `test/retry.test.mjs`.

**Build.**

- [ ] Pass `notification:<userId>:<campaignId>` as the idempotency key on every `transport.send` call.

**You see.**

- [ ] The push stub log shows the same key on a retried send and records one delivery for it.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `test/retry.test.mjs` gains the case where the job dies between send and `markSent`. Run `node --test test/`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `sonnet` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the retry scenario with idempotency keys at trunk and head with the push stub failing user 2 once. Save `N2-lane1.png`. Pass when head delivers users 1, 2, and 3 once each and trunk shows user 1 twice.
- [ ] Lane 2. Run a clean job with no failures. Save `N2-lane2.png`. Pass when every user gets one message.
- [ ] Lane 3. Run the job twice in a row with no failures. Save `N2-lane3.png`. Pass when the second run sends nothing.
- [ ] Lane 4. Fail the push for user 3 twice and let the job retry twice. Save `N2-lane4.png`. Pass when user 3 gets one message after the second retry.
- [ ] Lane 5. Run a job with a user whose id is 0. Save `N2-lane5.png`. Pass when that user gets one message.
- [ ] Lane 6. Run a job against an empty user table. Save `N2-lane6.png`. Pass when the job exits 0 and sends nothing.
- [ ] Lane 7. Kill the job after the push for user 1 returns and before markSent runs, then rerun it. Save `N2-lane7.png`. Pass when the push stub records one delivery for the key of user 1.
- [ ] Lane 8. Send the same key twice from two jobs started one second apart. Save `N2-lane8.png`. Pass when the push stub records one delivery and the second job logs a duplicate-key response.
- [ ] Lane 9. Make the store reject markSent once. Save `N2-lane9.png`. Pass when the job exits non-zero and the operator log names the user.
- [ ] Lane 10. Run a job with 500 users. Save `N2-lane10.png`. Pass when the job finishes and every user is marked sent.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Wall time of one job run over 500 users, at trunk and at head.
- [ ] Probe. Run `node bench/job.mjs 500` at trunk and head, interleaved, five runs each.
- [ ] Baseline. Record the trunk median first.
- [ ] Rule. Head median more than 20 percent above the trunk median fails.

**Review gate.** None. N2 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The operator lands the stack bottom-up.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the report the execution playbook names.

## Appendix A. Prototype evidence

The open question was whether persisting a per-user sent status is enough to stop duplicates across retries. A prototype on branch `review/root-cause` at `1c9bc9e` passed `markSent` into `sendAll` and ran the retry test. It passed. The crash window between send and `markSent` stays open, so N2 exists.

## Appendix B. Alternatives rejected

Dedupe at the end of the job. It drops legitimate duplicate rows and leaves the retry cause in place.

## Appendix C. Risks

A crash between send and `markSent` can resend one user in N1. N2 closes that window with the idempotency key.

## Appendix D. Links and reading list

Read `pstack/skills/how/SKILL.md` before N1. Run `pstack/skills/interrogate/SKILL.md` on N1 before merge.
```

（注意這份計畫的 Program checklist 比 playbook 的骨架**短**：骨架的 tick prompt、PR mechanics、Verdict and merge 都有更多條目。check-plan 只檢查五個 H3 依序存在與三個關鍵字（`git show origin/main:`、`/loop 1h`、`status message`），不檢查每一條是否都在。真實的計畫應該保留骨架的所有條目；這裡為了篇幅刪減，這本身就是一個 check-plan 看不到的缺口。）

### check-plan 的結果

```
Persist sent status in the store (N1)  boxes=27  files=3 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=3 merge=4
Send an idempotency key with each push (N2)  boxes=23  files=2 build=1 you-see=1 verify-unit=1 verify-live=10 verify-perf=4 review-gate=0 merge=4
2 PR sections, 0 problems
```

### 兩個工具看不到、讀了才發現的錯

**錯 1：N2 的 lane 是 N1 的複製品。** 第一版的產生器用 `pr2 = [... for s, p in pr1]` 把 N1 的十條 lane 整批搬給 N2，只改了第一條的措辭。check-plan 印出 0 problems。但 N2 要解決的是「推送成功後、`markSent` 前崩潰」——**十條 lane 裡沒有任何一條在測這件事**。修正：把 Lane 7、8 換成 N2 特有的場景（在 send 與 markSent 之間殺掉 job；兩個 job 送出同一個鍵）。重跑 check-plan，仍然 0 problems——它在修正前後都通過，因為它根本看不到這個問題。

**錯 2：日誌與 Appendix A 描述了一個不存在的 prototype。** 第一版寫的是「A prototype on branch `proto/status-column` at `1c9bc9e` added the column」，日誌那一列也說「tried a status column」。稽核工具確認 `1c9bc9e` 存在——但讀了那個 commit：

```
1c9bc9e fix(notify): persist sent status so a retry skips delivered users
  review/root-cause
```

它在 `review/root-cause` branch 上，做的是「把 `markSent` 傳進 `sendAll`」，沒有任何「column」。commit 存在，但它**沒有顯示那一列宣稱的東西**（Ch 26 對帳的第二條）。修正：改寫 Appendix A；日誌**追加一列取代**，不改舊列。

### 決策日誌

```
phase      decision                                                                                                               evidence                   result
frame      two PRs, N1 persists sent status, N2 adds an idempotency key                                                           /tmp/lab/pb/retry-sim.mjs  scope set
prototype  tried a status column on the store before writing the plan                                                             commit 1c9bc9e             VERIFIED, retry test green
write      generated plan.md from pr-template.md and plan-skeleton.md                                                             /tmp/lab/pd/gen.py         plan.md written
check      ran check-plan on plan.md                                                                                              /tmp/lab/pd/plan.md        0 problems
prototype  supersedes the 1c9bc9e row: the prototype persisted status through markSent on review/root-cause, no column was added  commit 1c9bc9e             VERIFIED, retry test green
write      replaced N2 lanes 7 and 8 with the crash window and a duplicate key                                                    /tmp/lab/pd/gen.py         plan.md regenerated
check      ran check-plan on the regenerated plan.md                                                                              /tmp/lab/pd/plan.md        0 problems
```

（省略了 `ts` 與 `why` 欄位。）

```bash
node audit-log.mjs planning.tsv /tmp/lab/pb/notify
```

```
7 rows, 0 with unresolved evidence
```

**解答說明**：

- 日誌保留了兩個錯誤**和它們的修正**。三個月後有人讀這份日誌，會知道 Appendix A 曾經寫錯過、為什麼改、N2 的 lane 為什麼和 N1 不同。如果直接改掉舊的列，這些資訊就消失了。
- 修改計畫之後，追加了一列「重跑 check-plan」。計畫改了，上一次的 0 problems 就不再描述現在的計畫（和 Ch 24 的 patch-id 是同一個道理：一個結論只對它檢查的那個版本有效）。
- 兩支工具都通過、但兩個錯都存在——這是這個練習最想讓你看到的事。工具把可以機械檢查的錯擋在外面，讓你的注意力能集中在它們看不到的地方；它們不能替你讀。

</details>

## 測試用例

| 檢查 | 預期 |
|---|---|
| `node check-plan.mjs plan.md` | `2 PR sections, 0 problems`，exit 0 |
| 在計畫開頭加「Goal: x」 | check-plan 報 `mid-sentence colon` |
| 刪掉 N2 的 Lane 10 | check-plan 報 `lanes are [1,...,9], expected 1 to 10` |
| 刪掉 N1 perf 的 Baseline | check-plan 報 `perf boxes are [Metric., Probe., Rule.]` |
| 把 N2 的 review gate 改成有方框但仍寫 None. | check-plan 報 `Review gate says None but has boxes` |
| `node audit-log.mjs planning.tsv <repo>` | `0 with unresolved evidence` |
| 在日誌加一列引用不存在的 commit | 稽核工具報 `commit ... does not exist` |
| N2 的十條 lane 裡，有幾條測的是 N2 特有的行為 | 至少 2 條（check-plan 不會檢查這一項，要你自己數） |

## 延伸挑戰（加分）

- **擴充 check-plan**：加一條規則，讓兩個 PR 的 lane 文字重複超過 80% 時報錯。為它寫一個測試案例，用參考解答的第一版產生器（lane 全部複製）證明它會失敗、用修正後的版本證明它會通過。
- **擴充 audit-log**：對 `commit <sha>` 的列，檢查 decision 裡提到的檔名是否出現在那個 commit 的 `git show --stat` 裡。它能自動抓到「錯 2」嗎？（提示：「status column」不是檔名。想一想哪些宣稱可以機械檢查、哪些不行。）
- **讓 agent 寫一份，你來審**：在 Claude Code 跑 `/pstack:poteto-mode turn this into a plan. small verifiable PRs, each with its own proof.`，附上練習 B 的參考修正與這個練習的背景。比較它的計畫和你的：它的 lane 有沒有測到崩潰視窗？它有沒有用 prototype 回答開放問題？它有沒有跑 check-plan？

## 自我檢核

- [ ] 你的計畫通過 check-plan，而且你能說出至少一個它通過了、但仍然有問題的地方
- [ ] 你的每個 PR 至少有兩條 lane 是那個 PR 特有的
- [ ] 你的 Appendix A 引用的 commit 真的存在，而且你讀過它、確認描述一致
- [ ] 你的日誌裡，錯誤是用追加一列取代的方式修正，而不是改掉舊列
- [ ] 能說出自己的計畫與日誌和參考解答的差異，並解釋各自取捨

Part 5 結束。最後一個 Part 回到起點：pstack 是一個人的風格，怎麼讓它變成你自己的？

→ [Ch 28 skill 寫作學：只留會改變決策的文字](./28-skill-authoring.md)
