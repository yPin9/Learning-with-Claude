# Ch 33 — Truth 與 Plan：兩種 artifact 的生命週期

> **目標**：分得出描述「系統現在做什麼」的 truth（`features/`、`contracts/`、`glossary.yaml`、constitution）和描述「這次要改什麼」的 plan（每次改動一份的 spec、plan、research、tasks），說得出兩者怎麼出生、怎麼改、怎麼結束；看懂 OpenSpec 1.16.0 怎麼用 `specs/` 和 `changes/` 把兩者分家；寫出 `truth_drift.py`：漂移檢查只對 truth 做，plan 只檢查生命週期，並在一個真的 git 歷史上抓出過期的 plan。

> **環境**：Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0、PyYAML 6.0.3、git 2.54.0、OpenSpec 1.16.0（`npx @fission-ai/openspec@1.16.0`，Node v26.11.0）、Spec Kit 1.2.5、Claude Code 2.1.295（claude-opus-5-5）、macOS 26.6。lab 從空目錄 `/tmp/lab/aibdd/ch33-truth-plan/` 開始，輸出都是 2026-11-02 實跑的結果；`truth_drift.py` 與 `archive_plan.sh` 修過之後，booking repo 的每一段輸出在 2026-11-03 從空目錄重跑過一次。

## 為什麼需要這個？

用 Spec Kit 做了兩個月，`specs/` 底下有 001 到 004。新同事問：Bob 訂了 Taipei 10:00–11:00，Alice 能不能接著訂 11:00–12:00？`001-room-booking/spec.md` 說不行，要留清潔時間；`003-back-to-back/spec.md` 說可以。兩份都沒寫錯，各自描述當時那次改動要做什麼。真正的問題是：repo 裡沒有任何一份檔案的職責是「描述系統現在做什麼」。

Spec Kit 1.2.5 的 [`spec-driven.md`](https://github.com/github/spec-kit/blob/v1.2.5/spec-driven.md) 寫著「Maintaining software means evolving specifications」，整個流程圍繞「specifications as the central source of truth」。它的工具卻是一次改動開一個資料夾，核心的 10 個 skill 沒有一個把這些資料夾合成一份現況：`/speckit-converge` 只在某個 feature 的 `tasks.md` 後面追加任務，`/speckit-analyze` 嚴格唯讀。Birgitta Böckeler 2025 年 10 月試用後指出，spec-kit 每寫一份 spec 就開一個分支，「which seems to indicate that they see a spec as a living artifact for the lifetime of a change request, not the lifetime of a feature」。

repo 既有教材講了一半。[SDD Ch 7](../spec_driven_development/07-spec-design-implementation.md) 的「單一真相來源（Single Source of Truth）與文件腐化」、[SDD Ch 39](../spec_driven_development/39-spec-drift-rot.md) 的「規格漂移（Spec Drift）的機制」講清楚了問題，Ch 39「現有條件下的務實建議」第 4 條還建議把真相來源轉到可讀的 BDD 測試；[SDD Ch 26](../spec_driven_development/26-skeptics-case.md) 引 Adzic：很多 SDD markdown 像是給工具追蹤自己進度用的。缺的是把兩種 artifact 分開、用不同規則對待。[SDD Ch 24](../spec_driven_development/24-the-new-code.md) 與 [SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) 甚至把 `tasks.md` 也算進要版控的 spec，本章最後要和它們對帳。這個問題比 AI 早得多。BDD 傳統的回答是活文件：Adzic 2011 年的《Specification by Example》第 3 章就叫「Living documentation」。架構決策有 Michael Nygard 2011 年的 ADR：編號「will not be reused」，被推翻時「keep the old one around, but mark it as superseded」。活文件是 truth，ADR 是紀錄，各抓到一半；本章把它們接上 agent 工作流。

## 先建立直覺：現行條文與修正草案

想知道今天的法律怎麼規定，你查**現行條文**，不會翻三年前的**修正草案**。草案寫的是「第 5 條從 A 改成 B」，附立法說明；它是一份 diff，對著提案當時那一版條文寫成。公布施行後，現行條文被改寫，草案進入立法紀錄，從此不再修改，只回答「當初為什麼」。兩份草案同時改第 5 條時，先通過的那份改掉了條文，後一份的「現行條文」欄就過期了，得重新對照。

```
  truth：現在式，只有一份                         plan：未來式，一次改動一份
  features/  contracts/  glossary.yaml           specs/004-cancel-booking/
  .specify/memory/constitution.md                  spec.md  plan.md  tasks.md
        ▲                                                 │ 對著「寫下它那天的 truth」寫成
        │ 同一個 PR：程式碼 + truth 的修改，人審過才合併     │
        └─────────────────────────────────────────────────┘
                                │ 合併後，同一個 PR 裡封存
                                ▼
                 specs/archive/2026-10-21-004-cancel-booking/   過去式，凍結，只回答「為什麼」
```

比喻有兩處不成立。法律沒有測試套件，我們的 truth 是可以執行的 feature 檔，所以能自動檢查 truth 和系統是否對得上，這是 `truth_drift.py` 的工作。草案通過的就是條文本身，plan 卻常在實作途中改主意：001 的 plan 寫 `POST /book`，做出來的是 `POST /bookings`。封存的 plan 記錄當時的意圖，不保證等於做出來的東西。

## 一、三個問題，把每個檔案分類

拿到一個檔案，問三件事：它描述現在，還是這次要改的東西？它跟系統一樣長命，還是跟一次改動一樣長？行為被推翻時，它要被**改寫**，還是**原樣保留、由新的一份取代**？前者是 truth，後者是 plan 或紀錄（record）。

| 產物 | 種類 | 描述的時間 | 什麼時候改 | 被推翻時 | 誰檢查 |
|---|---|---|---|---|---|
| `features/*.feature` | truth | 現在 | 和程式碼同一個 PR | 改寫 | `truth_drift.py` TD-1、TD-2（實際執行） |
| `contracts/http.md` | truth | 現在 | 同上 | 改寫 | TD-3、TD-4（比對 `app.routes`） |
| `glossary.yaml` | truth | 現在 | 同上 | 改寫 | TD-5、TD-6；[Ch 28](./28-gherkin-dsl-style.md) 的 GH-08 |
| `.specify/memory/constitution.md` | truth（原則） | 現在 | 修憲 PR，升版號（[Ch 17](./17-constitution-and-presets.md)） | 改寫 | 只能人審 |
| ADR `docs/adr/NNN-*.md` | record | 決定當時 | 只新增 | 舊的留著，標 superseded | 無 |
| `specs/NNN-*/spec.md`、`plan.md`、`research.md` | plan | 這次改動 | 改動進行中 | 留著，由新的 plan 取代 | LC-1 到 LC-4（生命週期） |
| `specs/NNN-*/tasks.md` | plan | 這次改動 | 打勾 | 同上 | LC-1、LC-3 |
| `example_map.yaml`（[Ch 8](./08-translation-sandwich.md)） | plan | 這次 discovery | 工作坊 | 同上 | `example_map.py` |
| 決策日誌 TSV（[pstack Ch 26](../pstack/26-figure-it-out-decision-log.md)） | record | 一次 run | 只能追加 | 不改 | 無 |

為什麼混在一起就**一定**漂移？假設「相鄰時段能不能訂」被改了 k 次。每份 plan 都當 truth，就有 k 份文件要保持一致；truth 只有一份，就只改一份。更糟的是，去「同步」舊 plan 等於改寫歷史：001 為什麼不准相鄰預約？要留清潔時間。這個理由在 003 推翻它之後仍有價值，下次有人想加緩衝時間，它就是前例。所以規則是：**truth 被推翻就改寫，plan 被推翻就保留**。漂移不是誰偷懶，是時間經過、舊 plan 被新改動超越；只要有人把舊 plan 當 truth 讀，就一定讀到過期的內容。

## 二、實驗：兩個月的 Spec Kit 式歷史

需要 [Ch 28](./28-gherkin-dsl-style.md) lab 裡照該章程式碼區塊建的六個檔：`app.py`、`glossary.yaml`、`pytest.ini`、`features/booking.feature`、`tests/dsl.py`、`tests/test_booking.py`。`tests/conftest.py` 在 Ch 28 只有文字描述，各人寫法不同，所以 `make_history.sh` 照那段描述自己寫一份。它用這些檔建一個 git repo，八個 commit 的日期手動設在 2026-09-01 到 10-20。plan 資料夾照 Spec Kit 的 `specs/NNN-name/` 慣例手寫，沒有實際跑 Spec Kit 指令；內容刻意寫短，只留下後面會用到的句子。lab 根目錄放 `make_history.sh`、`naive_check.sh` 和 `tools/`（`truth_drift.py`、`archive_plan.sh`、`pre-commit`）；之後的指令都先 `source /tmp/lab/aibdd/.venv/bin/activate`，在 `booking/` 或它的 clone 裡執行。clone 一律放在 lab 根目錄（例如 `git clone -q booking drift-a`），`../tools/` 才找得到。

<details><summary>make_history.sh（44 行，完整內容）</summary>

```bash
#!/bin/bash
# make_history.sh [CH28_LAB]: build booking/, eight commits dated 2026-09-01..10-20, from Ch 28's lab files
set -euo pipefail
SRC=${1:-/tmp/lab/aibdd/ch28-gherkin-style}; LAB=/tmp/lab/aibdd/ch33-truth-plan
rm -rf $LAB/booking && mkdir -p $LAB/booking && cd $LAB/booking && git init -q -b main
git config user.name lab && git config user.email lab@example.com
commit() { git add -A && GIT_AUTHOR_DATE="$1T10:00:00+08:00" GIT_COMMITTER_DATE="$1T10:00:00+08:00" git commit -qm "$2"; }
put() { mkdir -p "$(dirname "$1")" && printf '%s\n' "${@:2}" > "$1"; }      # put FILE LINE...
finish() { sed -i '' 's/^- \[ \]/- [x]/' "specs/$1/tasks.md"; }
S=specs/001-room-booking; put $S/spec.md '# Feature: Room booking (001)' '- FR-001 A person books a room for part of one day.' \
  '- FR-002 A booking that overlaps or touches another booking of the same room MUST be rejected:' '  rooms need turnover time, so Bob 10:00-11:00 blocks Alice 11:00-12:00.' '- FR-003 The end must be after the start.'
put $S/plan.md '# Plan 001' '- EP:POST /book creates a booking; EP:GET /bookings lists them.' '- In-memory list; overlap test `start <= other.end and other.start <= end`.'
put $S/tasks.md '- [ ] T001 [US1] [FR-001] [EP:POST /book] booking route in app.py' '- [ ] T002 [US1] [FR-002] features/booking.feature scenarios'
commit 2026-09-01 "001 plan: room booking"
cp -R $SRC/app.py $SRC/glossary.yaml $SRC/pytest.ini $SRC/features . && mkdir tests && cp $SRC/tests/{dsl,test_booking}.py tests/ && put tests/conftest.py 'import pytest' 'from dsl import Booking' '' '@pytest.fixture' 'def booking():' '    return Booking()'
put contracts/http.md '# HTTP contract: every endpoint the app serves (truth). truth_drift.py compares the EP: lines with app.routes.' '' \
  '- EP:POST /bookings  body {room, start, end, by} with HH:MM times -> 201 {room, start, end, by};' '  409 (R1) "Taipei is taken 10:00-11:00 by Bob"; 422 (R2) "end must be after start"' \
  '- EP:GET /bookings  -> 200, every booking' '- EP:GET /  -> the booking page: Room, Start, End, Name, Book, and a role=status line' '- EP:POST /reset  test only: forget every booking'
sed -i '' 's/b.start < x\["end"\] and x\["start"\] < b.end/b.start <= x["end"] and x["start"] <= b.end/' app.py   # MVP: closed intervals
sed -i '' -e '14s/Adjacent slots do not overlap/Back-to-back slots are rejected/' \
  -e '17s/the booking is confirmed/the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob/' features/booking.feature
finish 001-room-booking && git add -A && mkdir -p specs/archive && git mv $S specs/archive/2026-09-03-001-room-booking
commit 2026-09-03 "001 room booking: code, truth files, plan archived"
put specs/002-weekly-meetings/spec.md '# Feature: Weekly meetings (002)' "- FR-001 A person books the same room every week for 1-12 weeks (Ch 8's story)."
put specs/002-weekly-meetings/tasks.md '- [ ] T001 [US1] [FR-001] weekly series in app.py' '- [ ] T002 [US1] [FR-001] features/weekly.feature'
commit 2026-09-10 "002 plan: weekly meetings"
put specs/003-back-to-back/spec.md '# Feature: Back-to-back bookings (003)' \
  '- FR-001 A booking that starts exactly when another ends MUST be confirmed (intervals are [start, end)):' '  Bob 10:00-11:00 and Alice 11:00-12:00 both stand.'
put specs/003-back-to-back/tasks.md '- [ ] T001 [US1] [FR-001] half-open overlap test in app.py' '- [ ] T002 [US1] [FR-001] @sc_book_02 expects confirmed'
commit 2026-09-18 "003 plan: back-to-back bookings"
cp $SRC/app.py app.py && cp $SRC/features/booking.feature features/ && finish 003-back-to-back
commit 2026-09-22 "003 back-to-back: [start, end) intervals"
S=specs/004-cancel-booking; put $S/spec.md '# Feature: Cancel a booking (004)' \
  '- FR-001 Only the person who booked can cancel (R4) -> 403 "only Bob can cancel this booking".' '- FR-002 Cancelling a booking that does not exist -> 404.'
put $S/plan.md '# Plan 004' '- Bookings have no id, so cancel by room and start: EP:DELETE /bookings/{room}/{start}?by=<name>'
put $S/tasks.md '- [ ] T001 [US1] [FR-001] [EP:DELETE /bookings/{room}/{start}] route + 403' '- [ ] T002 [US1] [FR-002] 404 for an unknown booking' '- [ ] T003 [US1] features/cancel.feature'
commit 2026-10-09 "004 plan: cancel a booking"
/tmp/lab/aibdd/.venv/bin/python -c 'from pathlib import Path; p = Path("app.py"); p.write_text(p.read_text().replace(
    "    BOOKINGS.append(b.model_dump())\n    return b", "    item = {\"id\": len(BOOKINGS) + 1, **b.model_dump()}\n    BOOKINGS.append(item)\n    return item"))'
sed -i '' 's/-> 201 {room, start, end, by}/-> 201 {id, room, start, end, by}/' contracts/http.md
commit 2026-10-14 "bookings get ids"
sed -i '' 's/MUST be rejected:/MUST be rejected (changed by 003: now confirmed):/' specs/archive/2026-09-03-001-room-booking/spec.md
commit 2026-10-20 "docs: bring the old 001 spec up to date"
git log --format='%cs %h %s'
```

</details>

```
$ bash make_history.sh
2026-10-20 624c818 docs: bring the old 001 spec up to date
2026-10-14 0995aa4 bookings get ids
2026-10-09 62f39bc 004 plan: cancel a booking
2026-09-22 1f1dc74 003 back-to-back: [start, end) intervals
2026-09-18 fbc6172 003 plan: back-to-back bookings
2026-09-10 31a00a6 002 plan: weekly meetings
2026-09-03 3233f4a 001 room booking: code, truth files, plan archived
2026-09-01 5bff44c 001 plan: room booking
```

作者和日期固定，hash 只由檔案內容與權限決定：六個檔和 Ch 28、[Ch 0](./00-environment-setup.md) 的程式碼區塊逐字相同（一般的 644 權限），你建出來的 hash 就和上面一樣（2026-11-03 照兩章當時的文字重建核對過）；差一個位元組（例如那兩章日後改過），後面指令和訊息裡的 hash 請換成你自己的。001 是 MVP，相鄰預約也拒絕，程式用閉區間 `<=`，plan 在同一個 commit 封存。002 寫完就沒人動。003 改成半開區間，程式和 `@sc_book_02` 一起改，plan 卻忘了封存。004 是取消預約的 plan，寫的時候預約還沒有 id。10-14 預約有了 id，小改動沒寫 plan（[SDD Ch 42](../spec_driven_development/42-when-not-to-use-sdd.md) 的「類別三」）。10-20 有人好心把封存的 001 spec 改成「符合現況」。七個帶程式碼的 commit 逐一 checkout 跑 pytest，都是 `6 passed`：**測試全綠，不代表文件沒問題**。

### 錯誤示範：拿每一份 markdown 比對程式

最直覺的漂移檢查：把所有 markdown 提到的端點（本課的 `EP:` 寫法）收集起來，和程式實際的路由比。

```
$ cat ../naive_check.sh
# naive_check.sh: compare every EP: mention in every markdown file (plans included) with the app's routes
grep -rhoE "EP:[A-Z]+ /[^] ?;,)]*" --include='*.md' . | sed 's/^EP://' | sort -u > /tmp/promised.txt
python -c "from app import app; print(*sorted(f'{m} {r.path}' for r in app.routes
           if getattr(r, 'include_in_schema', False) for m in r.methods), sep='\n')" | sort -u > /tmp/served.txt
comm -23 /tmp/promised.txt /tmp/served.txt     # promised somewhere, served nowhere
$ bash ../naive_check.sh
DELETE /bookings/{room}/{start}
POST /book
```

兩筆都不是漂移。`POST /book` 來自封存的 001 plan，是後來改掉的設計；`DELETE /bookings/{room}/{start}` 來自 004，是還沒做的東西。放進 CI，團隊只剩兩條路：刪掉 plan 裡的 `EP:` 行讓它變綠（毀掉紀錄），或學會忽略它（檢查從此失效）。plan 描述的本來就是 HEAD 以外的時間點，和 HEAD 不一致是正常的。

### 讓 agent 回答「現在」的問題

兩份目錄的程式和 truth 相同（取自第五節清理後的 repo，加封存標記之前的版本），差在 plan 的擺法、`CLAUDE.md`，以及 `separated/` 多一支它的 `CLAUDE.md` 提到的 `tools/truth_drift.py`。`mixed/` 照 Spec Kit 預設把 001、003、004 都留在 `specs/`，`CLAUDE.md` 說 feature spec 放在 `specs/NNN-<name>/`，再加我們寫的一句「The spec is the source of truth: read the relevant spec before you answer a question or change code.」`separated/` 把 001、003 放進 `specs/archive/`，`CLAUDE.md` 換成第六節的五條規則。問題：Q1，Bob 占著 Taipei 10:00–11:00，Alice 訂 11:00–12:00 會被接受嗎？Q2，Alice 取消 Bob 的預約會怎樣？提示要求回答「as it is today」，每題一行，附上「決定答案的那一個檔案」。旗標：`--tools` 與 `--allowedTools` 都是 `"Read,Grep,Glob"`、`--max-turns 12`、`--max-budget-usd 1`、`--setting-sources project,local`、`--strict-mcp-config`、`--no-session-persistence`、`--output-format stream-json --verbose`、`< /dev/null`。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同；每種擺法只跑一次。

| | 擺法 | Q1（引用的檔案） | Q2（引用的檔案） | 工具呼叫（Read） | turns | 費用 | 時間 |
|---|---|---|---|---|---|---|---|
| run 1 | `mixed/` | accepted（`specs/003-back-to-back/spec.md`） | 還沒有取消功能（`app.py`） | 17（15） | 18 | $0.1218 | 20.4 s |
| run 2 | `separated/` | accepted（`features/booking.feature:14`） | 還沒有取消功能（`contracts/http.md`） | 8（6） | 9 | $0.0871 | 10.5 s |

兩次都答對，這點要誠實說：只有三份 plan 的小 repo，模型自己把矛盾對完帳了。差別在過程。run 1 讀遍所有 plan、測試和 `pytest.ini`，最後引用一份 **plan** 當依據（「which replaces the rejection rule in 001's FR-002」）；Q2 它看出 004 的 403「is still only planned (all its tasks are unchecked)」，靠的是翻程式碼。run 2 直接讀 truth，引用的兩個檔案都是 truth，工具呼叫不到一半。plan 越多，run 1 那種對帳越貴；這是推論，我們只量了 n=1。

## 三、OpenSpec：真的把兩者分家的工具

OpenSpec（Fission-AI，MIT；1.16.0 的 GitHub release 時間是 2026-10-30 UTC）的文件說得直接：「Specs are the source of truth — they describe how your system currently behaves. Changes are proposed modifications」。我們用隔離的 HOME 跑它（`HOME=<lab>/.home npx --yes @fission-ai/openspec@1.16.0`，npm 快取和全域設定都留在 lab，`OPENSPEC_TELEMETRY=0`），下面的 `openspec` 指的就是這一串。`openspec init --tools claude` 建出 `openspec/specs/`、`openspec/changes/archive/`、`openspec/config.yaml`，外加 `.claude/` 下 6 個 skill 和 6 個 `/opsx:*` 指令。我們手寫一份 truth `openspec/specs/booking/spec.md`（不能重疊、相鄰可以），再用 `openspec new change add-cancel-booking` 開一個 change（它只建 `.openspec.yaml`，其餘我們手寫）：`proposal.md`、`design.md`、`tasks.md`（2 個任務，勾了 1 個），以及 delta `specs/booking/spec.md`（`## ADDED Requirements` 底下一條 `### Requirement: Only the booker can cancel`）。這幾份檔案的全文沒有收進本章，照 OpenSpec `docs/concepts.md`「Spec Format」的範例寫就會得到同樣的輸出：每條需求一句帶 SHALL 的敘述（少了只是 WARNING）和至少一個 `#### Scenario:`（少了是 ERROR），proposal 的 `## Why` 至少 50 個字元（不足會多印一行警告）：

```
$ openspec archive add-cancel-booking < /dev/null; echo "exit=$?"
Task status: 1/2 tasks
Warning: 1 incomplete task(s) found. Continue? (y/N) ✖ Error: 1 incomplete task(s) found for change 'add-cancel-booking', and no answer could be read from stdin.
Fix: Complete the tasks or rerun with openspec archive add-cancel-booking --yes
exit=1
$ openspec archive add-cancel-booking --yes; echo "exit=$?"
Task status: 1/2 tasks
Warning: 1 incomplete task(s) found. Continuing due to --yes flag.
…（略）
  + 1 added
Totals: + 1, ~ 0, - 0, → 0
Specs updated successfully.
Change 'add-cancel-booking' archived as '2026-11-02-add-cancel-booking'.
exit=0
$ openspec validate --archived; echo "exit=$?"
✗ change/2026-11-02-add-cancel-booking
  ✗ 1 incomplete task (1/2 completed)
Totals: 0 passed, 1 failed (1 items)
exit=1
```

封存做兩件事：把 delta 合進 `openspec/specs/booking/spec.md`，再把整個 change 搬到 `changes/archive/2026-11-02-add-cancel-booking/`。注意 `--yes`：沒有終端機（agent、CI）時一定要加，它卻也跳過「任務沒做完」的確認，於是 truth 多了一條**還沒實作**的需求。補救是另一個檢查 `validate --archived`，CLI 文件說它適合放進 pre-commit hook。

delta 的格式是 `## ADDED / MODIFIED / REMOVED / RENAMED Requirements`，以 `### Requirement:` 標題當鍵。這格式也是演化出來的：它自己的封存紀錄裡，2025-08-06 從 `.spec.md.diff` 改成存完整未來狀態，2025-08-19 又改成只存 delta，因為 reviewer 在 GitHub 上看到整份檔案都是綠的，看不出改了什麼。有了鍵，就看得出 plan 過期。兩個 change 同時改 `No double booking`，一個改名（RENAMED）、一個加緩衝時間（MODIFIED，它取代整條需求，現有的兩個情境要一起抄進去，漏了 `validate` 就報 ERROR）；`openspec validate --changes` 時兩個都通過（`Totals: 2 passed, 0 failed (2 items)`），先封存改名的那個之後：

```
$ openspec validate add-buffer; echo "exit=$?"
Change 'add-buffer' is valid
ℹ [INFO] booking/spec.md: Archive would refuse this delta: booking MODIFIED failed for header "### Requirement: No double booking" - not found
exit=0
$ openspec archive add-buffer --yes; echo "exit=$?"
…（略）
booking MODIFIED failed for header "### Requirement: No double booking" - not found
Aborted. No files were changed.
exit=1
```

這就是「後一份草案的現行條文欄過期了」。`validate` 只給 INFO、exit 0，真正擋下來的是套用（archive）那一步。OpenSpec 沒做的事也要看清楚：它的 truth 是散文，archive 是文字合併，沒有任何一步執行程式碼。它自己的 repo 在 2026-09-29 合併了 [PR #1919](https://github.com/Fission-AI/OpenSpec/pull/1919)，說明寫「Three OpenSpec requirements no longer matched current behavior」，其中一條來自 OpenSpec Cloud 的掃描；同一批自動產生的修正還提了一個 exit code，「init source and tests did not support that claim」，合併前被拿掉。分家解決了「去哪裡讀」，沒有解決「讀到的是不是真的」。三者並排（注意同一個字意思相反：OpenSpec 的 `specs/` 是 truth，Spec Kit 的 `specs/` 是 plan）：

| | OpenSpec 1.16.0 | Spec Kit 1.2.5 | 本課 |
|---|---|---|---|
| truth | `openspec/specs/<capability>/spec.md`（散文） | 沒有專屬位置；constitution 是唯一長期檔 | `features/`、`contracts/`、`glossary.yaml`、constitution |
| plan | `openspec/changes/<name>/` | `specs/NNN-name/` | 沿用 `specs/NNN-name/` |
| truth 怎麼更新 | archive 時合併 delta | 核心沒有這一步 | PR 裡直接改，PR 的 diff 就是 delta |
| 封存 | `changes/archive/YYYY-MM-DD-name/` | 核心沒有 | `specs/archive/YYYY-MM-DD-NNN-name/` 加一行標記 |
| 和程式對帳 | 不執行程式；選用的 `/opsx:verify` 讓 agent 拿一個 change 對照程式碼 | converge 拿某一份 plan 比對程式 | `truth_drift.py truth` 跑情境、比路由 |

## 底層機制：plan 是對某一版 truth 寫的 diff

每份 plan 都有一個隱含的**基準版本**：寫下它那天的 truth。程式碼的基準 git 從歷史算得出來（merge base），OpenSpec 靠 `### Requirement:` 標題對得上，Spec Kit 沒有任何記錄。基準之後 truth 一動，plan 就可能過期，和 code review 裡「你的分支落後 main 了」是同一件事。

```
 plan:  起草 ──人審──▶ 核准 ──實作──▶ 合併 ──同一個 PR──▶ 封存（凍結，加標記）
          │            │
          └─ truth 在基準之後動過 ─▶ 過期：對照現在的 truth 重寫，或刪掉（git log 還留著）

 truth: v(n) ──PR：程式碼 + truth 的修改，人審，truth_drift 全綠──▶ v(n+1)
```

`truth_drift.py` 守三條不變式。一，**main 的每個 commit，truth 都描述程式碼**：每個情境都有跑而且通過，契約和路由一致，詞彙表的詞有人用、路由不用它列為 avoid 的詞（TD-1 到 TD-6）。二，**plan 不是進行中就是已封存**：做完的要封存（LC-1），進行中的要對著現在的 truth（LC-2），太久沒動的要處理（LC-3）。三，**封存的 plan 不再改變**（LC-4）。TD 取 truth drift，LC 取 lifecycle；`ST` 這個前綴 Ch 27 的 `rules/steps.md` 已經用了，兩者會在同一個 repo 裡相遇，所以不共用。漂移檢查只跑在 truth 上，理由在第二節：plan 描述的是基準那一刻或尚未到來的未來，和 HEAD 比必然不一致，而那些不一致都不是 bug。反過來，plan 只檢查生命週期，內容對不對交給人審。

## 四、truth_drift.py：只對 truth 做漂移檢查

`contracts/http.md` 是本章新加的 truth 檔：每個端點一行 `EP:` 方法加路徑，後面是給人讀的說明（內容見 `make_history.sh` 的 `put contracts/http.md`）。完整的 `tools/truth_drift.py`（130 行，Python 3.12，stdlib 加 PyYAML）：

```python
"""truth_drift.py truth|plans [--root DIR]: check the truth against the code, and the plans against their lifecycle.
truth: TD-1 a scenario fails  TD-2 a scenario never ran  TD-3 contracts/ promise a route the app lacks
       TD-4 the app serves a route no contract lists  TD-5 a glossary term is in no feature
       TD-6 a route uses a glossary 'avoid' word
plans: LC-1 every task done, plan not archived  LC-2 truth changed after the plan was last touched
       LC-3 open tasks untouched for --idle-days  LC-4 an archived plan differs from its archived version
Truth = features/ glossary.yaml contracts/ .specify/memory/constitution.md. Plans = specs/<id>/,
archive = specs/archive/<date>-<id>/; plans are never compared with the code. Exit 0 clean, 1 problems, 2 cannot run."""
import argparse, datetime as dt, importlib, json, re, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
import yaml

TRUTH = ["features", "glossary.yaml", "contracts", ".specify/memory/constitution.md"]
SCENARIO = re.compile(r"\s*(?:Scenario Outline|Scenario Template|Scenario|Example):\s*(.*)")
ENDPOINT = re.compile(r"EP:(GET|POST|PUT|PATCH|DELETE) (/[^\s?;,)\]]*)")
norm = lambda path: re.sub(r"\{[^}]*\}", "{}", path)        # /bookings/{id} == /bookings/{booking_id}

def run_suite(root):
    """One pytest run: cucumber JSON says which scenarios ran, JUnit XML says which tests failed."""
    with tempfile.TemporaryDirectory() as tmp:
        cj, jx = Path(tmp, "cuke.json"), Path(tmp, "junit.xml")
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              f"--cucumberjson={cj}", f"--junitxml={jx}"], cwd=root, capture_output=True, text=True)
        if run.returncode not in (0, 1, 5) or not cj.exists() or not jx.exists():   # 2-4: pytest itself broke
            raise RuntimeError(f"pytest exit {run.returncode}: {(run.stdout + run.stderr).strip()[-300:]}")
        ran = {}
        for feature in json.loads(cj.read_text()):
            for el in feature["elements"]:
                ran.setdefault((feature["uri"], el["line"]), []).append(el)
        failed = {}
        for case in ET.parse(jx).iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            if bad is not None:                       # pytest puts the most specific line of the message last
                failed[case.get("name")] = [l.strip() for l in (bad.get("message") or "?").splitlines() if l.strip()][-1][:100]
    return ran, failed

def check_truth(root, app_ref, out):
    ran, failed = run_suite(root)
    features, scenarios = sorted((root / "features").rglob("*.feature")), 0
    for f in features:
        rel, uri = f.relative_to(root).as_posix(), f"{f.parent.name}/{f.name}"  # pytest-bdd's uri: own dir/file
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if m := SCENARIO.match(line):
                scenarios += 1
                if (uri, n) not in ran:
                    out.append(f"{rel}:{n}: TD-2 \"{m[1]}\" never ran; bind {rel} with scenarios() in a test module")
                for el in ran.get((uri, n), []):
                    if el["id"] in failed:
                        tags = " ".join("@" + t["name"] for t in el["tags"])
                        out.append(f"{rel}:{n}: TD-1 {tags} {el['id']} fails: {failed[el['id']]}")
    promised = {}
    for f in sorted((root / "contracts").glob("*.md")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for m in ENDPOINT.finditer(line):
                promised.setdefault((m[1], norm(m[2])), (f"{f.relative_to(root)}:{n}", m[2]))
    module, _, attr = app_ref.partition(":")
    sys.path.insert(0, str(root))
    app = getattr(importlib.import_module(module), attr)
    served = {(verb, norm(r.path)): r.path for r in app.routes if getattr(r, "include_in_schema", False) for verb in r.methods}
    for (verb, path), (where, raw) in promised.items():
        if (verb, path) not in served:
            out.append(f"{where}: TD-3 EP:{verb} {raw} is promised, but the app has no such route")
    for (verb, path), raw in served.items():
        if (verb, path) not in promised:
            out.append(f"{app_ref}: TD-4 the app serves {verb} {raw}; add an EP: line to contracts/ or remove the route")
    terms = (yaml.safe_load((root / "glossary.yaml").read_text()) or {}).get("terms") or {}
    text = " ".join(f.read_text() for f in features).lower()
    for term, entry in terms.items():
        if not re.search(rf"\b{re.escape(term.lower())}", text):
            out.append(f"glossary.yaml: TD-5 term \"{term}\" appears in no feature; a word with no behaviour is a plan")
        for word in (entry or {}).get("avoid", []):
            for (verb, _), raw in served.items():
                if re.search(rf"\b{re.escape(word.lower())}\b", raw.lower()):
                    out.append(f"{app_ref}: TD-6 {verb} {raw} says \"{word}\"; the glossary says \"{term}\"")
    return f"{scenarios} scenarios, {len(promised)} endpoints, {len(terms)} terms"

def git(root, *args):
    run = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(run.stderr.strip())
    return run.stdout.strip()

def check_plans(root, today, idle_days, out):
    specs, truth = root / "specs", [t for t in TRUTH if (root / t).exists()]
    folders = sorted(p for p in specs.glob("*") if p.is_dir() and p.name != "archive")
    for plan in folders:
        rel = plan.relative_to(root).as_posix()
        if not (last := git(root, "log", "-1", "--format=%H %cs", "--", rel)):
            continue                                                     # nobody committed this plan yet
        sha, day = last.split()
        tasks = (plan / "tasks.md").read_text() if (plan / "tasks.md").exists() else ""
        done, todo = len(re.findall(r"^- \[[xX]\]", tasks, re.M)), len(re.findall(r"^- \[ \]", tasks, re.M))
        if done and not todo:
            out.append(f"{rel}: LC-1 all {done} tasks are done; git mv it to specs/archive/<date>-{plan.name} in the merging PR")
            continue
        if moved := git(root, "log", "--format=%h %cs %s", f"{sha}..HEAD", "--", *truth).splitlines():
            out.append(f"{rel}: LC-2 truth changed in {len(moved)} commit(s) since {day}, newest {moved[0]}; "
                       "re-read the plan against features/, contracts/, glossary.yaml")
        if todo and (age := (today - dt.date.fromisoformat(day)).days) > idle_days:
            out.append(f"{rel}: LC-3 {todo} open tasks, untouched for {age} days; finish, re-plan or delete it")
    archived = sorted(p for p in (specs / "archive").glob("*") if p.is_dir())
    for plan in archived:
        rel = plan.relative_to(root).as_posix()
        first = git(root, "log", "--reverse", "--format=%h", "--", rel).splitlines()
        if first and git(root, "diff", "--name-only", first[0], "--", rel):          # working tree vs archived version
            out.append(f"{rel}: LC-4 differs from the version archived in {first[0]}; "
                       f"git checkout {first[0]} -- {rel}, and put new facts in truth")
    return f"{len(folders)} active plans, {len(archived)} archived"

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["truth", "plans"])
    ap.add_argument("--root", default=".", help="project root (default: .)")
    ap.add_argument("--app", default="app:app", help="MODULE:ATTR of the FastAPI app (default: app:app)")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(), help="date for LC-3 (default: today)")
    ap.add_argument("--idle-days", type=int, default=14, help="LC-3 threshold (default: 14)")
    args = ap.parse_args()
    root, out = Path(args.root).resolve(), []
    try:
        counts = (check_truth(root, args.app, out) if args.mode == "truth"
                  else check_plans(root, args.today, args.idle_days, out))
    except (OSError, RuntimeError, ImportError, AttributeError) as e:
        print(f"truth_drift: cannot run {args.mode} in {root}: {e}", file=sys.stderr)
        return 2
    print(*out, f"{counts}, {len(out)} problems", sep="\n")
    return 1 if out else 0

if __name__ == "__main__":
    sys.exit(main())
```

- **跑一次 pytest，要兩份報告**。cucumber JSON 列出哪些情境真的執行了，用（檔案, 行號）當鍵。檔案那一半是 pytest-bdd 寫進 JSON 的 `uri`，它不從專案根目錄算，而是 `parser.py` 第 373 行的「檔案所在目錄名／檔名」：`features/journeys/x.feature` 的 uri 是 `journeys/x.feature`，所以鍵取 `f.parent.name`。這是 `scenarios()` 綁出來的格式（本課一律這樣綁，Ch 27 的 ST-1）；改用 `@scenario("../features/booking.feature", ...)` 綁，uri 會變成 `tests/../features/booking.feature`，跑過的情境也報 TD-2，所以訊息要你用 `scenarios()`。[Ch 26](./26-bdd-vs-tdd-artifacts.md) 實測過，步驟沒定義時 JSON 會漏掉那一步和它後面的步驟、前面的照標 passed，所以成敗看 JUnit XML。`--collect-only` 只回答「收得到」，回答不了「跑過而且通過」。TD-2 補的是 pytest 看不到的洞：沒被任何 `scenarios()` 綁定的 feature 檔，pytest-bdd 不會抱怨。
- **路由讀 `app.routes`**，`include_in_schema` 濾掉 FastAPI 自動加的 `/docs`、`/openapi.json`；路徑參數正規化成 `{}`，契約寫 `{id}`、程式寫 `{booking_id}` 算同一個端點。**pytest 自己壞掉（exit 2 到 4）就回 exit 2**，不假裝成一堆 TD-2。實測放一支語法錯誤的測試檔，得到 `cannot run truth … pytest exit 2`；沒有 git 的目錄跑 `plans` 得到 `fatal: not a git repository`，同樣 exit 2。`--idle-days` 預設 14，是兩週一個迭代的節奏，請換成你們團隊的。

在 `booking/`（HEAD 是 10-20 的 commit）實跑一次約 0.46 到 0.62 秒，大部分花在 pytest 啟動；接著在一份 clone 裡把 feature 搬進子目錄、綁定跟著改，確認子目錄裡的情境也對得上：

```
$ python ../tools/truth_drift.py truth
4 scenarios, 4 endpoints, 3 terms, 0 problems
$ cd .. && git clone -q booking nested && cd nested
$ mkdir features/rooms && git mv features/booking.feature features/rooms/
$ sed -i '' 's|features/booking.feature|features/rooms/booking.feature|' tests/test_booking.py
$ python ../tools/truth_drift.py truth
4 scenarios, 4 endpoints, 3 terms, 0 problems
```

HEAD 是乾淨的，naive 版那兩筆確實是假警報。子目錄那份的 pytest 也是 `6 passed`；鍵若寫成根目錄相對路徑 `(rel, n)`（我們最初就是這樣寫），同一份 clone 報出 4 則 `features/rooms/booking.feature:8: TD-2 "A free room can be booked" never ran; ...`、exit 1，[Ch 29](./29-example-mapping.md) 的 `features/journeys/` 也一樣中。現在讓漂移往兩個方向發生，各在一份 `git clone` 裡做。**程式動了、truth 沒動**：有人以為相鄰預約是 bug，hotfix 把判斷改回 `<=`，再多一條路由。

```
$ sed -i '' 's/b.start < x\["end"\] and x\["start"\] < b.end/b.start <= x["end"] and x["start"] <= b.end/' app.py
$ printf '\n@app.get("/reservations")\ndef reservations():\n    return BOOKINGS\n' >> app.py
$ python ../tools/truth_drift.py truth
features/booking.feature:14: TD-1 @sc_book_02 test_adjacent_slots_do_not_overlap fails: reason: 'Taipei is taken 10:00-11:00 by Bob' != None
app:app: TD-4 the app serves GET /reservations; add an EP: line to contracts/ or remove the route
app:app: TD-6 GET /reservations says "reservations"; the glossary says "booking"
4 scenarios, 4 endpoints, 3 terms, 3 problems
```

**truth 跑到程式前面**：把 004 的內容提前搬進 truth，再把 002 的詞 `series` 加進詞彙表。

```
$ printf '%s\n' 'Feature: Cancel a booking' '' '  Rule: R4 Only the person who booked can cancel' '' '    @sc_cancel_02' \
  '    Scenario: Someone else tries to cancel' '      Given Bob has booked "Taipei" from 10:00 to 11:00' \
  '      When Alice cancels the booking of "Taipei" from 10:00 to 11:00' '      Then the cancellation is rejected because only Bob can cancel this booking' > features/cancel.feature
$ echo '- EP:DELETE /bookings/{id}?by=<name>  -> 204; 403 (R4) "only Bob can cancel this booking"; 404' >> contracts/http.md
$ printf '%s\n' '  series:' '    zh: 週期預約' '    definition: The same room at the same time every week for 1-12 weeks.' '    avoid: [recurrence]' >> glossary.yaml
$ python ../tools/truth_drift.py truth
features/cancel.feature:6: TD-2 "Someone else tries to cancel" never ran; bind features/cancel.feature with scenarios() in a test module
contracts/http.md:8: TD-3 EP:DELETE /bookings/{id} is promised, but the app has no such route
glossary.yaml: TD-5 term "series" appears in no feature; a word with no behaviour is a plan
5 scenarios, 5 endpoints, 4 terms, 3 problems
$ python -m pytest -q -p no:cacheprovider | tail -1
6 passed in 0.02s
```

最後一行是本節重點：pytest 全綠，truth 卻在描述不存在的行為。這是「plan 漏進 truth」：truth 只能和讓它成真的程式碼一起改。兩次都 exit 1。

## 五、plans：在真的 git 歷史上找過期的 plan

用 `--today 2026-10-21` 跑，等於在故事最後一個 commit 的隔天、那個 PR 的 CI 裡執行：

```
$ python ../tools/truth_drift.py plans --today 2026-10-21; echo "exit=$?"
specs/002-weekly-meetings: LC-2 truth changed in 2 commit(s) since 2026-09-10, newest 0995aa4 2026-10-14 bookings get ids; re-read the plan against features/, contracts/, glossary.yaml
specs/002-weekly-meetings: LC-3 2 open tasks, untouched for 41 days; finish, re-plan or delete it
specs/003-back-to-back: LC-1 all 2 tasks are done; git mv it to specs/archive/<date>-003-back-to-back in the merging PR
specs/004-cancel-booking: LC-2 truth changed in 1 commit(s) since 2026-10-09, newest 0995aa4 2026-10-14 bookings get ids; re-read the plan against features/, contracts/, glossary.yaml
specs/archive/2026-09-03-001-room-booking: LC-4 differs from the version archived in 3233f4a; git checkout 3233f4a -- specs/archive/2026-09-03-001-room-booking, and put new facts in truth
3 active plans, 1 archived, 5 problems
exit=1
```

每筆都對得上故事。002 寫於相鄰預約還被拒絕的時候，之後 truth 動了兩次，它的例子可能已經錯了。004 打算用 room 加 start 取消，因為當時沒有 id；10-14 之後該改用 id。修之前先寫一支封存腳本 `tools/archive_plan.sh`，順便補上第六節 run 3 裡 agent 指出的缺口：直接打開封存檔的人看不到任何警告。標記和搬移在同一個 commit，LC-4 比對的「封存版本」就已含標記，不會誤報。`mkdir -p` 那行是給第一次封存的：`git mv` 不會替你建上層目錄，少了它，在還沒有 `specs/archive/` 的 repo（例如這段歷史的第一個 commit）封存 001 會得到 `fatal: renaming 'specs/001-room-booking' failed: No such file or directory`、exit 128；`make_history.sh` 先手動 `mkdir -p` 過，所以本章的歷史沒撞到。接著在 `booking/` 的一份 clone 裡照訊息逐條修：還原 001（LC-4）、封存 003（LC-1）、刪掉沒人做的 002（git log 還留著它）、把 004 改成用 id（LC-2）：

```
$ cat ../tools/archive_plan.sh
#!/bin/sh
# archive_plan.sh ID DATE: move specs/ID to specs/archive/DATE-ID and stamp every .md in it, ready to commit with the merge
set -eu
to=specs/archive/$2-$1
mkdir -p specs/archive                # git mv does not create it, and the first archive has no specs/archive/ yet
git mv "specs/$1" "$to"
for f in "$to"/*.md; do
  printf '> ARCHIVED %s: this file describes a change, not what the system does now. Truth: features/, contracts/, glossary.yaml.\n\n' "$2" | cat - "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done
git add "$to" && echo "archived specs/$1 -> $to"
$ git checkout -q 3233f4a -- specs/archive/2026-09-03-001-room-booking
$ sh ../tools/archive_plan.sh 003-back-to-back 2026-09-22
archived specs/003-back-to-back -> specs/archive/2026-09-22-003-back-to-back
$ git rm -rq specs/002-weekly-meetings
$ printf '%s\n' '# Plan 004' '- Bookings have ids since 0995aa4: EP:DELETE /bookings/{id}?by=<name>' > specs/004-cancel-booking/plan.md
$ sed -i '' 's|{room}/{start}|{id}|' specs/004-cancel-booking/tasks.md
$ git add -A && git commit -qm "plans: restore 001, archive 003, drop 002, re-plan 004 on ids"
$ python ../tools/truth_drift.py plans; python ../tools/truth_drift.py truth
1 active plans, 2 archived, 0 problems
4 scenarios, 4 endpoints, 3 terms, 0 problems
```

LC-4 最早的版本是「列出所有改過封存檔的 commit」。在清理後的 repo 跑那個做法的核心 `git log --diff-filter=M -- specs/archive`，列出的是兩筆：624c818 和清理那個 commit 本身，因為還原也是一次修改，所以檢查永遠紅。改成比對**狀態**（現在的檔案和封存當下是否相同）之後，修好就是修好。設計 gate 的通則：檢查不變式此刻成不成立，不檢查歷史上有沒有人犯過錯。

## 六、讓 agent 讀 truth、寫 plan

第一層是指示。`separated/` 的 `CLAUDE.md` 裡「Truth and plans」一節的五行：

```
- What the system does now: features/*.feature, contracts/http.md, glossary.yaml. Read these first.
- specs/<id>/ is one change in flight. Write only in the folder of the change you were given.
- specs/archive/ is history: read it to learn why, never to learn what the system does now. Never edit it.
- A change edits features/, contracts/ or glossary.yaml only in its spec phase, in the same PR as the code.
- Done means `python tools/truth_drift.py truth` and `python tools/truth_drift.py plans` print 0 problems.
```

第二層是權限：`.claude/settings.json` 寫 `"deny": ["Edit(/specs/archive/**)"]`，`/path` 在專案設定裡以 session 的主要工作目錄為基準（在專案根目錄啟動 Claude Code 時就是專案根目錄）。不要寫 `Write(/specs/archive/**)`：[官方權限文件](https://code.claude.com/docs/en/permissions)明講 `Write(path)` 規則「never match」，路徑規則只看 `Edit` 和 `Read`，而 `Edit` 規則涵蓋所有會改檔案的內建工具。deny 的實際效果 [Ch 3](./03-propose-constrain-feedback.md) 測過；實作期間鎖住 `features/` 用 [Ch 27](./27-ai-bdd-loop.md) 的 `feature_guard.py`，不要再寫一支。第三層是 git 本身：在清理後 repo 的一份 clone 裡，把 `truth_drift.py` 放進 `tools/`、下面這支放進 `.git/hooks/pre-commit` 並 `chmod +x`。少了執行權限，git 只印一行 `hint: The '.git/hooks/pre-commit' hook was ignored because it's not set as executable.` 就讓 commit 通過（實測 `exit=0`）。裝好之後它對人和 agent 一視同仁，只擋 LC-4：

```
$ cat .git/hooks/pre-commit
#!/bin/sh
# pre-commit: refuse any commit while an archived plan differs from its archived version (LC-4)
out=$(/tmp/lab/aibdd/.venv/bin/python tools/truth_drift.py plans | grep "LC-4") || exit 0
echo "$out" >&2
exit 1
$ sed -i '' 's/MUST be rejected:/MUST be confirmed now:/' specs/archive/2026-09-03-001-room-booking/spec.md
$ git commit -qam "docs: refresh the old 001 spec"; echo "exit=$?"
specs/archive/2026-09-03-001-room-booking: LC-4 differs from the version archived in 3233f4a; git checkout 3233f4a -- specs/archive/2026-09-03-001-room-booking, and put new facts in truth
exit=1
```

第三次 `claude -p` 測寫入那一側：在 `separated/` 的副本裡開放 Read、Grep、Glob、Edit、Write，提示是一個把人往改歷史推的請求：「specs/archive/2026-09-03-001-room-booking/spec.md still says back-to-back bookings are rejected, and it confused a new teammate. Fix the docs so nobody is misled again.」這次的設定檔除了 `Edit(...)` 還多寫了一條 `Write(/specs/archive/**)`，依上面的文件，那條不會生效。

> 以下為實際執行紀錄（節錄），2026-11-02，每次執行結果不同。

`CLAUDE.md` 由 Claude Code 自動載入；agent 另外讀了設定檔、`tools/truth_drift.py`、兩份封存的 spec、004 的 spec、三個 truth 檔和 `app.py`，**沒有嘗試改封存**（`permission_denials` 是空的，所以 deny 規則這次沒被觸發），而是新增一份 `README.md`，寫明「What it does now」「What is changing」「Why it got this way」各去哪裡讀，拿 001 和 003 當例子。回覆的最後一段：「anyone who opens the archived spec directly still sees the old rule with no warning, and the archive rules mean that can't change.」13 次工具呼叫、14 turns、$0.1921、27.3 秒。第五節封存腳本的那行標記就是從這句話來的。

三種檢查放在三個時間點：LC-4 放 pre-commit，擋 commit；TD-1 到 TD-6 與 LC-1 放合併前的 CI，擋合併；LC-2、LC-3 每週跑一次或留言在 PR，只提醒。後兩者需要判斷，truth 動過不代表 plan 一定錯，久沒動也可能在等別人，拿來擋合併只會讓人學會繞過。CI 寫法沿用 [Ch 22](./22-backend-e2e-first.md) 的 workflow 草稿，多加兩行指令；GitHub Actions 上本章未實測。

## 七、和 SDD Ch 24、Ch 43 對帳

[SDD Ch 43](../spec_driven_development/43-sdd-in-teams.md) 的「三、spec 與 code 一起進版本控管」反駁「spec 只是過渡文件」，列了三個長期價值：考古、CI 偵測漂移、新人 onboarding；雷 2 又說「Spec 凍結等於規格腐化的開始」。拆成兩種 artifact 之後，各有歸屬：**考古**屬於 plan，保留而且凍結，封存資料夾就是考古現場；**CI 偵測漂移**屬於 truth，對 plan 做只會得到第二節那種假警報；**onboarding** 屬於 truth，run 1 就是新人讀一疊 plan 的樣子，答得出來但要自己對帳；**凍結等於腐化**對 truth 成立、對 plan 不成立，plan 本來就該凍結。Ch 43「都進 git、分開 commit」的建議完全保留，要補的是：**進 git 不等於當 truth**。[SDD Ch 24](../spec_driven_development/24-the-new-code.md) 引 Grove 主張 spec 是單一真相來源，「工具落地」一節把 Kiro 的 requirements、design、tasks 合起來當要版控的 spec。版控沒問題，但 [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 自己說 tasks.md 是「執行工單」，工單做完就結案，不會變成產品說明書。Grove 的論點在本課的落地是：單一真相來源是 `features/`、契約與詞彙表這一組**可執行、只有一份**的 truth；每次改動的 spec.md 是修改它的提案，也就是 Spec Kit「evolving specifications」裡被 evolve 那份東西的 diff。這正是 SDD Ch 39 第 4 條建議的完整版。

## 對比與取捨

| 做法 | 例子 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|
| plan 全留在原地，都當 truth | Spec Kit 預設 | 零成本 | 每次改動讓一些舊 plan 變成謊言；讀者自己對帳 | 一次性、不再維護的專案 |
| plan 用完就刪 | 只靠 git log | 目錄乾淨 | 「為什麼」只剩 commit message；搜不到前例 | 小團隊、改動都很小 |
| 分家：散文 truth + delta 封存 | OpenSpec | 去哪裡讀很清楚；過期的 delta 套用時被擋 | truth 是散文，漂移靠掃描與人審 | 非功能需求多、難以執行驗證的系統 |
| 分家：可執行 truth + 封存 | 本課 | 漂移大多由測試抓到；plan 不會觸發漂移警報 | 要維護 step definitions 與契約；封存靠紀律或工具 | 長期維護、多人協作的產品 |
| 只有一份活規格，程式由它生成 | Tessl 的方向（spec-as-source） | 沒有 plan 與 truth 之分 | Böckeler 說 Tessl 還在探索這一層；生成不確定 | 實驗性專案 |

## 踩雷集錦

1. **錯誤直覺：「spec 要保持最新」適用於每一份 spec。** 結果去改舊 plan，像 10-20 那個「bring the old 001 spec up to date」。正確認識：保持最新的對象是 truth；舊 plan 凍結，新的事實寫進 truth。
2. **錯誤直覺：漂移檢查掃得越廣越好。** naive 版連 plan 一起比，HEAD 乾淨也報了 2 筆。正確認識：漂移檢查只跑 truth；會亂叫的檢查最後一定被關掉。
3. **錯誤直覺：pytest 綠了，truth 就被驗證了。** truth 跑到程式前面那次全綠，`cancel.feature` 卻從沒執行。正確認識：另外確認每個情境都**跑過**（TD-2）；空情境也會通過，見 [Ch 8](./08-translation-sandwich.md) 的實測。
4. **錯誤直覺：plan 搬進 `specs/archive/` 之後，Spec Kit 的編號會接著往下排。** 1.2.5 附的 `create-new-feature.sh`（[Ch 17](./17-constitution-and-presets.md) 的 `sdd-specify` 就呼叫它）只掃 `specs/` 最上層的 `NNN-` 資料夾。在一個 `specify init` 出來的乾淨專案（做法見 [Ch 12](./12-spec-kit-1x.md)）實測：建出 001、002，搬進 `specs/archive/` 後，下一個是 `"FEATURE_NUM":"001"`，從此有兩個 001。內建的 `speckit-specify` 不呼叫這支腳本，而是叫 agent「scanning existing directories in specs/」自己算，結果看模型，未實測。正確認識：Nygard 說編號「will not be reused」；改用 `--timestamp`（實測得到 `20261102-220641-office-hours`），或在 `.specify/init-options.json` 設 `"feature_numbering": "timestamp"`（`speckit-specify` 讀這個欄位，`specify init` 重跑時保留它），或每次自己挑一個沒用過的 `--number`（腳本同樣只檢查最上層有沒有撞號）。
5. **錯誤直覺：TD-1 紅了，就改 feature 讓它綠。** 這等於把回歸藏進文件。正確認識：哪一邊錯由人決定，和 [pstack Ch 21](../pstack/21-verification-skills.md) 的 maintain-verification-skill「reports the regression instead of hiding it with a doc edit」同理；只有這次改動本來就要改變行為，才在同一個 PR 裡改 truth。
6. **錯誤直覺：拿 `/speckit-converge` 對舊 feature 跑一次，就能確認沒漏做。** 正確認識：converge 只該對進行中的 feature 用。它把該 feature 的 spec、plan、tasks 當「sole source of intent」，對 001 跑就會把「相鄰要拒絕」當成未完成的工作，追加任務把 003 做回去。這是讀 SKILL.md 得出的推論，本段未實測。

## 進階：再往深一層

- **讓 LC-2 更準**：現在只要 truth 有 commit 就報。改成只報這份 plan 提到的檔案、`EP:` 端點或 `@sc_` 標籤有變動的 commit，誤報會少很多。
- **散文 truth 的漂移**：constitution 和非功能需求無法執行。OpenSpec PR #1919 的修正經過原始碼與測試核對，自動產生的那條 exit code 因此被拿掉；用 LLM 掃散文 truth，產出要當 plan 審，不能直接合進 truth。
- **Spec Kit 生態的補丁**：Spec Kit 1.2.5 原始碼的 `extensions/catalog.community.json` 列有 `archive` extension 1.4.0（「Archive merged features into main project memory」，整合進 `.specify/memory/`）與 `domain-spec`（「Keeps canonical per-domain specs」）。本章未安裝、未實測。

## 動手練習

1. **把 004 做完並封存**：實作 `DELETE /bookings/{id}?by=`（R4：403、404、204），寫 `features/cancel.feature` 並在測試模組綁定，契約加一行 `EP:`，在同一個 commit 用 `archive_plan.sh` 封存 004。完成條件：`truth` 與 `plans` 都印出 `0 problems`。網域部分可對照 [練習 D](./practice-d-cancel-booking.md)。
2. **把 Ch 27 的鎖延伸到所有 truth**：在 `feature_guard.py` 的 `LOCKS` 加上 `contracts` 和 `glossary.yaml`（規則同 `features`），再加一條任何階段都鎖住的 `specs/archive`。照 Ch 27 的方式餵 JSON 事件，確認 deny 的時機都對。
3. **製造一次 LC-2 誤報**：在 004 之後只改 `glossary.yaml` 裡一個和取消無關的 `zh` 欄位，看 LC-2 是否照報；再照「進階」第一點改寫 LC-2，比較前後輸出。

## 本章重點整理

- truth 描述現在、只有一份、被推翻就改寫；plan 描述一次改動、一次一份、被推翻就保留。把 plan 當 truth 讀，漂移是必然的。plan 還有基準版本：truth 在基準之後動過，plan 就可能過期，OpenSpec 用需求標題偵測，本課用 git（LC-2）提醒。
- 漂移檢查只跑在 truth 上（TD-1 到 TD-6），plan 只檢查生命週期（LC-1 到 LC-4）；掃 plan 的漂移檢查只會產生假警報。
- truth 和程式碼在同一個 PR 改；plan 在合併的 PR 裡封存並加標記，之後凍結，由 pre-commit 擋修改。agent 讀 truth、寫 plan：指示寫在 `CLAUDE.md`，權限用 `Edit(...)` deny（`Write(...)` 規則不會生效），實作期間鎖 truth 交給 Ch 27 的 hook。

## 自我檢核

- [ ] 不看本章，用三個問題把 `tasks.md`、`glossary.yaml`、ADR 各自分類，並說出它們被推翻時各該怎麼處理。
- [ ] OpenSpec 的 `validate` 和 `archive` 對過期的 MODIFIED delta 各有什麼反應？哪一個真的擋下來？
- [ ] pytest 全綠、truth 卻在描述不存在的行為，這是怎麼發生的？`truth_drift.py` 用哪兩份報告抓它？
- [ ] LC-4 為什麼比對狀態而不是歷史？在你自己的 gate 裡舉一個同樣的例子。
- [ ] 有人引 SDD Ch 43 說「spec 不是過渡文件，要一直更新」，你怎麼用兩句話同意他，又指出要補的那一點？

## 延伸閱讀

- **[OpenSpec — Concepts](https://github.com/Fission-AI/OpenSpec/blob/main/docs/concepts.md)**（Fission-AI，持續更新；本章對照 1.16.0）
  - **讀哪裡**：「The Big Picture」「Changes」「Delta Specs」「Archive」四節；再看 repo 裡 `openspec/specs/openspec-conventions/spec.md`，它用自己的格式寫自己的規範。
  - **學到什麼**：specs/changes 分家的理由、delta 的四種操作（concepts.md 列 ADDED、MODIFIED、REMOVED，RENAMED 寫在 conventions spec）、archive 的三個步驟。第三節撞到的兩件事（沒有終端機要加 `--yes`、MODIFIED 要帶齊情境）寫在同目錄的 `troubleshooting.md`。
  - **和本章的關聯**：第三節的實跑照這份文件做；它的 truth 是散文，拿來對照本課的可執行 truth。
- **[Birgitta Böckeler — Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**（martinfowler.com，2025-10-15）
  - **讀哪裡**：spec-first、spec-anchored、spec-as-source 的定義，以及她比較 memory bank 與 spec 的段落（前者「relevant across all AI coding sessions」）。
  - **學到什麼**：外部觀察者眼中，三個工具各把 spec 當成哪一種東西。
  - **和本章的關聯**：她說的 memory bank（規則檔、產品與程式碼的高階描述）大致對應本章的 truth，spec 對應 plan。「一份 spec 一個分支」是 2025 年 10 月的版本，Spec Kit 1.2.5 已不預設開分支（見 [Ch 12](./12-spec-kit-1x.md)）。
- **[Michael Nygard — Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)**（Cognitect blog，2011-11-15）
  - **讀哪裡**：全文，很短；重點是編號規則和 Status 欄位。
  - **學到什麼**：只增不改的紀錄怎麼設計：編號不重用，被推翻的決策保留並標 superseded。
  - **和本章的關聯**：封存的 plan 就是這種紀錄；踩雷第 4 條的編號問題，2011 年就有答案。
- **《Specification by Example》** — Gojko Adzic（Manning，2011）
  - **讀哪裡**：第 3 章「Living documentation」（「Why we need authoritative documentation」一節）與第 11 章「Evolving a documentation system」（「Living documentation should be consistent」「…organized for easy access」）。章節標題我們在 Manning livebook 核對過。
  - **學到什麼**：為什麼團隊需要一份權威、跟著系統更新的文件，以及它長大之後怎麼維持一致、好找。就我所知，第 11 章建議活文件依功能而不是依當初的 user story 組織，細節以原書為準。
  - **和本章的關聯**：本課的 `features/` 就是書中說的 authoritative documentation，plan 則是不該被組織進去的東西。書超過十年，工具章節已過時，文件組織的部分仍適用。

五段路線的每一種產物，到這裡都有了位置與生命週期。最後的專案把它們從 example map 串到雙 driver 驗收，收尾時封存 plan、更新 truth。

→ [Final Project：會議室預約：從 example map 到雙 driver 驗收的完整流水線](./final-project-booking-pipeline.md)
