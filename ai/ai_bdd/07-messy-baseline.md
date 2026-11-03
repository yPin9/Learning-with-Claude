# Ch 7 — 混亂的起點：單檔 BDD skill 與一口氣做完的 autopilot

> **目標**：做出 Part 2 要改造的「混亂基準線」，而且把它量化：用 skill-creator 產生單檔的 `bdd-monolith`，用一份手寫的 `autopilot` skill 一口氣做出會議室預約 app 並保留 transcript；再用自己的驗收探針、突變測試與 `skill_metrics.py` 做事後檢討，把每一個失敗對回 skill 的結構。

> **環境**：Claude Code 2.1.295（`claude -p`，模型 `claude-opus-5-5`）、skill-creator（`claude-plugins-official` 快照 a7ba5b93，用 `--plugin-dir` 臨時載入）、課程 venv（Python 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1、FastAPI 0.162.0）、jq 1.7.1（macOS 內建）、macOS 26.6 arm64。三次 `claude -p` 都是 2026-11-02 的實際執行紀錄（節錄），每次執行結果不同，你重跑會拿到不一樣的檔案與數字。

## 為什麼需要這個？

Part 1 講完了 skill 是什麼、怎麼用 skill-creator 做（[Ch 4](./04-prompt-files-to-skills.md)、[Ch 5](./05-skill-creator.md)）。Part 2 要**改造** skill：拆出規則檔、模板、腳本，最後做出改造 skill 的 skill（[Ch 11](./11-skill-engineering-skill.md)）。改造之前要先有「改造前」，而且要是真的、量過的，不是我們想像出來的爛 skill。現實裡很常見的兩種 skill：一種是跟 skill-creator 說「幫我做一個 BDD skill」，拿到第一版草稿就收工，產物是兩百多行、什麼都寫在一起的 SKILL.md；另一種是 autopilot（自動駕駛式）skill：「你是資深全端工程師，一口氣把整個 app 做完，不要問問題。」

repo 談過這類失敗：pstack [Ch 25](../pstack/25-autonomous-run.md) 的「為什麼需要這個？」列了無人看管時的四種失敗，[Ch 27](../pstack/27-multi-phase-autopilot.md) 描述 Autopilot 的 PR 佇列，harness [Ch 38](../harness_engineering/38-failure-modes-debugging.md) 有「失敗模式圖鑑」。但沒有一章拿一次真實執行來對帳。這一章把兩種 skill 都真的跑一次、留下 transcript（執行紀錄），再做事後檢討（post-mortem）：每一個症狀，對回 skill 結構上的哪一個缺口。

## 先建立直覺：一本手冊，還是一條有檢查站的產線

想像你把新同事丟進沒人的辦公室，桌上放一本兩百頁的手冊，說「明天早上交一個能用的系統」。早上你回來，桌上有一個系統、一疊他自己寫的測試，和一張紙條：「做完了，測試都過。」另一種做法是產線：每一站有明確的輸入、輸出，和一個**不歸工人管**的檢查。工人可以很聰明，但「過站」不是他說了算。

```
  單檔 skill（手冊）                    分階段流程（產線）
  ┌────────────────────────────────┐
  │ 角色、原則、步驟、規則、模板、 │    spec ─▶ [檢查] ─▶ tasks ─▶ [檢查] ─▶ code ─▶ [檢查]
  │ 例子，全在一個檔案             │              │                  │                 │
  └───────────────┬────────────────┘              腳本、hook 或人，不是寫 code 的那個 agent
                  │ 呼叫時整份放進對話
                  ▼
   agent ─▶ 一路做到「我覺得做完了」─▶ result: success
```

比喻的限制：agent 不會「懶得讀手冊」，但它手上的手冊在長對話裡可能被壓縮掉後半（見「底層機制」）。比喻成立的是這句：**手冊裡寫「確認測試都過」，不等於產線上有一個檢查站。**

## 一、用 skill-creator 產生 bdd-monolith

> skill-creator 的完整迴圈（訪談、草稿、eval、改寫、description 最佳化）在 [Ch 5](./05-skill-creator.md)。這一章刻意只用「草稿」這一步，模擬「拿到第一版就收工」的用法。

lab 目錄從空目錄開始：`mkdir -p /tmp/lab/aibdd/ch07-messy/{gen,transcripts,drafts,tools,probes,skills} && cd /tmp/lab/aibdd/ch07-messy`。`gen/` 是 skill-creator 的工作目錄，`autopilot-run/` 在第二節建立；以下指令都在這裡執行，Python 指令先 `source /tmp/lab/aibdd/.venv/bin/activate`。

我們沒有安裝 plugin，而是用 `--plugin-dir` 只在這次 session 載入；`--setting-sources project` 讓我們自己 user 層的 plugin、hook 與同步 skill 不混進實驗：

```sh
#!/bin/sh
# run-skill-creator.sh [TARGET] [NAME]：用 skill-creator 產生 bdd-monolith 草稿（不跑 eval）
set -eu
TARGET="${1:-.claude/skills/bdd-monolith/SKILL.md}"   # Run 1 用預設值（受保護的 .claude/）
NAME="${2:-01-skill-creator}"
LAB=/tmp/lab/aibdd/ch07-messy
SC="$HOME/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator"
cd "$LAB/gen"
claude -p "/skill-creator:skill-creator Create a new skill called bdd-monolith at $TARGET.

What it is for: developing features in a Python project the BDD way. Write the Gherkin .feature file first, bind it with pytest-bdd step definitions, watch the scenarios fail, then implement until they pass. Our team's app is a FastAPI meeting-room booking API (rooms Taipei, Tokyo, Seoul; a room cannot be double-booked; end must be after start).

This is a non-interactive run: nobody can answer questions, so make reasonable assumptions and list them in your final message. Skip test cases and evals: stop after the SKILL.md draft." \
  --plugin-dir "$SC" \
  --setting-sources project \
  --tools "Read,Write,Edit,Glob,Grep,Bash" \
  --allowedTools "Read,Write,Edit,Glob,Grep,Bash(mkdir *),Bash(ls *)" \
  --permission-prompts none \
  --max-turns 20 \
  --no-session-persistence \
  --output-format stream-json --verbose \
  > "$LAB/transcripts/$NAME.jsonl" && rc=0 || rc=$?
echo "claude exit=$rc"
```

旗標的取捨：`--tools` 決定「有哪些工具」，`--allowedTools` 決定「哪些不用問就能跑」，`--permission-prompts none` 讓需要核准的動作直接被拒，因為沒有人能按 Yes。`--max-turns` 在 2.1.295 的 `claude --help` 裡**看不到**（原始碼標了 `hideHelp()`），但[官方 CLI reference](https://code.claude.com/docs/en/cli-reference) 有記載（「Limit the number of agentic turns」），傳了也會被接受；它怎麼計數，見「底層機制」。`stream-json` 把每個事件寫成一行 JSON，這就是要留的 transcript。prompt 開頭的 `/skill-creator:skill-creator` 會在執行前展開成 skill 內容；stream-json 不回放展開後的訊息，所以「它有被載入」是從 `system/init` 事件的 skill 清單、以及草稿照 skill-creator 建議寫出的「pushy」description 推斷的。

### Run 1：result 是 success，檔案卻不存在

執行 `chmod +x run-skill-creator.sh && ./run-skill-creator.sh`，不帶參數，目標就是預設的 `.claude/skills/bdd-monolith/SKILL.md`。這次 5 個 tool call、2 個被拒。transcript 是一行一個 JSON 事件，用 jq 抽出拒絕理由與最終狀態：

```bash
jq -r 'select(.subtype=="permission_denied") | "\(.tool_name): \(.message | capture("What required approval: (?<w>.*)").w)"' transcripts/01-skill-creator.jsonl
jq -r 'select(.type=="result") | "subtype=\(.subtype) is_error=\(.is_error)"' transcripts/01-skill-creator.jsonl; ls gen/.claude
```

```
Bash: Contains brace with quote character (expansion obfuscation)
Write: Claude requested permissions to write to /private/tmp/lab/aibdd/ch07-messy/gen/.claude/skills/bdd-monolith/SKILL.md, but you haven't granted it yet.
subtype=success is_error=false
ls: gen/.claude: No such file or directory
```

被拒的 Bash 是 skill-creator 想在暫存目錄建一個專案、把它寫的範例真的跑一次，好習慣；它不在白名單裡，又踩到 brace 加引號的安全檢查，沒人能核准，就被拒。被拒的 Write 撞上 Claude Code 的**受保護路徑**（[protected paths](https://code.claude.com/docs/en/permission-modes#protected-paths)）：寫入 `.claude/` 在 default 模式一律要人核准，文件寫明 allow 規則不能預先放行（我們的 `--allowedTools` 明明列了 `Write`，照樣被拒）；`-p` 沒有人能核准，所以被拒。兩件事記下來。第一，**`subtype: success` 只代表迴圈正常結束，不代表任務完成**；這次是 agent 在最後訊息自己承認「the skill file wasn't created」，如果它沒說，結果欄位一模一樣。第二，**寫 skill 的 agent 不能直接改 `.claude/`，這是設計**：文件說受保護路徑是為了避免意外改壞 repo 狀態與 Claude 自己的設定；skill 會被之後每個 session 載入，要人看過才放進去，和 [Ch 6](./06-workflows-are-grown.md) 「`.claude/` 的改動要走 review」是同一件事。草稿沒有遺失，被拒的 Write 的完整參數就在 result 事件的 `permission_denials` 裡；用 `jq -j` 原樣輸出（`-r` 會在結尾多補一個換行，檔案就多一行）：

```bash
mkdir -p drafts/run1 && jq -j 'select(.type=="result") | .permission_denials[] | select(.tool_name=="Write") | .tool_input.content' \
  transcripts/01-skill-creator.jsonl > drafts/run1/SKILL.md && wc -l drafts/run1/SKILL.md
```

```
     242 drafts/run1/SKILL.md
```

### Run 3：寫到 drafts/，由人搬進 .claude/

同一份 prompt，只把目標換到非保護路徑（Run 2 是下一節的 autopilot）：`./run-skill-creator.sh drafts/bdd-monolith/SKILL.md 03-skill-creator-drafts`。這次 7 個 tool call，Write 成功（222 行），另外兩個被拒的是「列出上層目錄」與「用 python3 檢查套件、驗 frontmatter」；`num_turns=8`、100.3 秒、估計 $0.54（Claude Code 在客戶端估的金額，不是帳單）。最後一步由人做：讀過之後 `mkdir -p gen/.claude/skills && cp -R gen/drafts/bdd-monolith gen/.claude/skills/`（Run 1 沒建出 `gen/.claude`，少了 `mkdir` 會得到 `cp: gen/.claude/skills: No such file or directory`）。再 `cp -R gen/drafts/bdd-monolith skills/` 留一份在 `skills/bdd-monolith/`，它就是 Part 2 的改造對象。它的段落依序是 Phase 0: Orient、Phase 1: Write the feature file、Phase 2: Bind the steps、Phase 3: Watch it fail、Phase 4: Implement until green、Finish with a report、Team app: meeting-room booking API（內含 Worked example）。

它寫得不差，Phase 4 的「Do not change a `Then` assertion to match what the code happens to do」和 pstack [Ch 8](../pstack/08-bug-fix-playbook.md) 的「`tdd` 技能的護欄」是同一個方向。兩份草稿引用的 `pytest-bdd generate`、`--generate-missing`、`--gherkin-terminal-reporter` 在 pytest-bdd 9.0.1 都存在。它的最後訊息坦白「its example code has never been run」，我們就替它跑：在 `draft-check/` 把 Worked example 的三段程式碼原封不動存成它指定的 `tests/features/booking.feature`、`tests/conftest.py`、`tests/step_defs/test_booking.py`，配一個只有 `def create_app(): return FastAPI()` 的 `app/main.py`。照它 Phase 3 的指令 `pytest tests/step_defs/test_booking.py -v` 跑，第一個結果是 `ModuleNotFoundError: No module named 'app'`：範例沒設 pythonpath，正好是它自己 Phase 3 表格裡「The harness is broken」「this is not a valid red」那一列（Run 1 的草稿倒是寫了 `pythonpath = ["."]`）。改用 `python -m pytest -q -p no:warnings tests`（`python -m` 會把目前目錄放進 `sys.path`）：`7 failed in 0.09s`，七個都是 `assert 404 == 201` 或 `assert 404 == 422` 的「行為不存在」，綁定本身沒有錯。所以內容大致可用，問題在兩個地方：**同一個 prompt 跑兩次，生出兩份不一樣的 skill**（第四節量給你看）；而且**兩份都自己發明了「會議室在不同時區」**（Run 3 原文：「The rooms are in different timezones, so require offsets at the API boundary」），從房間名稱推出 `AwareDatetime` 與 UTC 偏移，還順手建議 PostgreSQL exclusion constraint；這些都沒有人要求過。

## 二、autopilot skill：一句話做完整個 app

這份 skill 是我們手寫的，模仿常見的 autopilot 寫法（角色設定、一串步驟、幾條「要做好」的原則），不是任何特定來源的複本：

```markdown
---
name: autopilot
description: Build a complete, working application end to end from a short request, in one go, without stopping to ask questions. Use when the user says "build me an app", "make the whole thing", "autopilot", or describes an app they want built from scratch.
---

# Autopilot

You are a senior full-stack engineer. The user wants a finished, working app, not a plan. Build the whole thing in one go.

## Workflow

1. Read the request and infer what the user needs. Do not ask questions; make reasonable assumptions.
2. Design the architecture: backend, frontend, data model and API.
3. Implement the backend.
4. Implement the frontend.
5. Write tests that cover the main features.
6. Run the tests and fix anything that fails.
7. Polish: error handling, validation, a clean UI, and a README.
8. Report what you built.

## Rules

- Never stop to ask the user anything. Keep going until the app is complete.
- Follow best practices and write clean, maintainable code.
- Prefer a complete feature set over a minimal one; users expect a real product.
- Make sure all tests pass before you finish.
- End with a short summary of what you built.
```

專案 `autopilot-run/` 一開始只有這份 skill（`.claude/skills/autopilot/SKILL.md`）和一個把課程 venv 放進 PATH 的 `.claude/settings.json`：`{"env": {"PATH": "/tmp/lab/aibdd/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"}}`。`run2-autopilot.sh` 在 `autopilot-run/` 裡執行，旗標和上一節相同，差別是沒有 `--plugin-dir`、`--allowedTools` 多了 `Bash(python *),Bash(pytest *)`、執行前先 `export PATH="/tmp/lab/aibdd/.venv/bin:$PATH"`、輸出寫到 `transcripts/02-autopilot.jsonl`，prompt 是：

```
/autopilot Build a meeting-room booking web app for our office. Rooms: Taipei, Tokyo, Seoul. People book a room for a time range on one day; a room must never be double-booked. Backend: FastAPI with POST /bookings taking JSON {room, start, end, by} (times as "HH:MM") and GET /bookings. Frontend: one HTML page served at / where people can book a room. Tests: pytest-bdd. The Python environment is ready: python and pytest are on PATH with fastapi, pytest-bdd and httpx installed; there is no network, so do not pip install anything.
```

一個 25 次 tool call 的 transcript 要用工具讀。[Ch 4](./04-prompt-files-to-skills.md) 的 `timeline.jq` 是一則訊息一行；這裡要的是一個 tool call 一行（序號、工具、目標、是否被拒），寫成 `tools/tool_calls.jq`：

```
# tool_calls.jq：每個 tool call 一行（序號、工具、目標、是否被拒），用 jq -rs -f 執行
(map(select(.type == "result"))[0].permission_denials | map(.tool_use_id)) as $no
| [.[] | .message? | objects | .content? | arrays | .[] | select(.type? == "tool_use")]
| to_entries[]
| "\(.key + 1)\t\(.value.name)\t\(.value.input | .file_path // .command // .pattern | gsub("\\s+"; " ") | .[0:58])\t\(if (.value.id | IN($no[])) then "DENIED" else "" end)"
```

```bash
jq -rs -f tools/tool_calls.jq transcripts/02-autopilot.jsonl
jq -r 'select(.type=="result") | "result=\(.subtype) num_turns=\(.num_turns) duration_ms=\(.duration_ms) cost_usd=\(.total_cost_usd)"' transcripts/02-autopilot.jsonl
jq -r 'select(.type=="result") | .permission_denials[] | "\(.tool_name) lines=\(.tool_input.command | split("\n") | length) chars=\(.tool_input.command | length)"' transcripts/02-autopilot.jsonl
```

```
1	Bash	ls -la && ls -la .claude .claude/skills .claude/skills/* 2	
2	Bash	cat .claude/settings.json; echo; pip list 2>/dev/null | gr	DENIED
3	Write	/private/tmp/lab/aibdd/ch07-messy/autopilot-run/app/__init	
…（略：4–12 是另外 9 次 Write）
13	Bash	python -m pytest -q 2>&1 | tail -25	
14	Write	/tmp/booking-smoke/smoke.py	
15	Bash	cd /private/tmp/lab/aibdd/ch07-messy/autopilot-run BOOKING	DENIED
…（略：16–24 是修測試、寫 README、再跑 pytest）
25	Bash	python -m pytest -q 2>&1 | tail -4; python -m pytest -q 2>	
result=success num_turns=26 duration_ms=256527 cost_usd=1.1377206
```

第三條指令量被拒的兩條指令有多大，輸出是 `Bash lines=723 chars=25168` 與 `Bash lines=17 chars=1073`。autopilot 的第一個實質動作，是一條 **723 行、25,168 個字元的 shell 指令**：查環境、用 heredoc 寫出全部檔案、跑 pytest，字面上的「一口氣」。被安全檢查擋下（理由 `Contains brace with quote character`）後，它改用 10 次 Write，10 次全出自同一則模型回應。第二條被擋的是它想做的實機驗證：起 4 個 uvicorn worker 打 40 個競爭請求，再把 transaction 拿掉確認測試會失敗（理由 `A variable in this command can't be checked before it runs`）。最後訊息（實際執行紀錄，節錄）：

> The booking app is built and all 32 pytest-bdd tests pass on three runs in a row, but I never ran it on a real server or in a browser. …
> **Cancelling (added):** I added `DELETE /bookings/{id}` and a Cancel button, because otherwise a mistaken booking could never be removed. …
> **No login:** anyone can cancel anyone's booking, and `by` is whatever name people type.

它做出 SQLite 儲存、選填的 `date` 欄位、`DELETE` 與取消按鈕、執行緒與多行程的競態測試、README；`app/`、`tests/` 與 README 合計 759 行，18 個 Scenario（含 Outline）展開成 32 個測試。prompt 只要了兩個 endpoint 和一個頁面。

## 三、驗收：它說「做完了」，我們怎麼知道？

它的報告有兩個可驗的主張：32 個測試全過、雙重預約會被擋。我們分三層驗。**第一層，重跑它自己的測試**：`cd autopilot-run && python -m pytest -q` 得到 `32 passed, 1 warning in 0.54s`，成立。

**第二層，我們自己的驗收探針。** A 組是 prompt 寫了或直接蘊含的行為（狀態碼照 HTTP 慣例：衝突 409、輸入不合法 422）；B 組是本課領域早就定好、prompt 卻沒寫出來的契約：R1 的錯誤訊息 `Taipei is taken 10:00-11:00 by Bob`、R4「只有預約人能取消」：

```python
"""test_probe_api.py：用我們自己的驗收探針檢查 autopilot 做出來的 API（不讀它的測試）"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app  # 這次執行產生的 factory；你重跑時名字可能不同


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(tmp_path / "probe.db"))  # 每個探針一個全新的 SQLite 檔


def book(api, room, start, end, by="Alice"):
    return api.post("/bookings", json={"room": room, "start": start, "end": end, "by": by})


# A 組：prompt 寫了、或直接蘊含的行為
@pytest.mark.parametrize("taken, attempt, status", [
    (None, ("Taipei", "10:00", "11:00"), 201),
    (("Taipei", "10:00", "11:00"), ("Taipei", "10:30", "11:30"), 409),
    (("Taipei", "10:00", "11:00"), ("Taipei", "11:00", "12:00"), 201),
    (None, ("Taipei", "11:00", "10:00"), 422),
    (None, ("Paris", "10:00", "11:00"), 422),
], ids=["A1-free", "A2-overlap", "A3-adjacent", "A4-end-before-start", "A5-unknown-room"])
def test_a_stated(api, taken, attempt, status):
    if taken:
        assert book(api, *taken, by="Bob").status_code == 201
    assert book(api, *attempt).status_code == status


# B 組：團隊早就定好、但 prompt 沒寫出來的契約（R1 的訊息、R4 的取消權限）
def test_b1_r1_detail(api):
    book(api, "Taipei", "10:00", "11:00", by="Bob")
    assert book(api, "Taipei", "10:30", "11:30").json()["detail"] == "Taipei is taken 10:00-11:00 by Bob"


def test_b2_r4_only_booker_cancels(api):
    booking_id = book(api, "Taipei", "10:00", "11:00", by="Bob").json()["id"]
    assert api.delete(f"/bookings/{booking_id}?by=Alice").status_code == 403
```

```bash
PYTHONPATH=autopilot-run python -m pytest -v -p no:warnings --tb=line probes/test_probe_api.py
```

```
probes/test_probe_api.py::test_a_stated[A1-free] PASSED                  [ 14%]
…（略：A2–A5 同樣 PASSED）
probes/test_probe_api.py::test_b1_r1_detail FAILED                       [ 85%]
probes/test_probe_api.py::test_b2_r4_only_booker_cancels FAILED          [100%]
…（略）
E   AssertionError: assert 'Taipei is al...-11-02 by Bob' == 'Taipei is ta...-11:00 by Bob'
…（略）
E   AssertionError: assert 204 == 403
…（略）
========================= 2 failed, 5 passed in 0.13s ==========================
```

A 組全過：相鄰的 10:00–11:00 與 11:00–12:00 不衝突，結束早於開始回 422。B 組全掛，而且掛得理所當然，它從沒看過這兩條規則：衝突訊息是 `Taipei is already booked 10:00-11:00 on 2026-11-02 by Bob`，日期是跑探針那天（它自己加了 `date` 欄位、預設今天），Alice 取消 Bob 的預約則回 204。**第三層，補跑它想跑卻被擋下的突變測試（mutation testing）**：故意把程式改壞，看它的測試抓不抓得到。在複本上改，不動原始碼：

```sh
#!/bin/sh
# mutants.sh：補跑 autopilot 想跑卻被擋下的突變測試（在複本上改，不動原始碼）
set -u
LAB=/tmp/lab/aibdd/ch07-messy
mutant() {  # $1 = 名稱, $2 = sed 表達式, $3 = 跑幾次
  rm -rf "$LAB/mut" && mkdir "$LAB/mut"
  cp -R "$LAB/autopilot-run/app" "$LAB/autopilot-run/tests" "$LAB/autopilot-run/pytest.ini" "$LAB/mut/"
  sed -i '' -e "$2" "$LAB/mut/app/store.py"   # macOS 的 BSD sed；GNU sed 寫 sed -i -e
  echo "--- $1（跑 $3 次）"
  for i in $(seq "$3"); do (cd "$LAB/mut" && python -m pytest -q -p no:warnings | tail -1); done \
    | sed -E 's/ in [0-9.]+s//' | sort | uniq -c
}
mutant "M1 拿掉 transaction" 's/"BEGIN IMMEDIATE"/"SELECT 1"/; s/"COMMIT"/"SELECT 1"/; s/"ROLLBACK"/"SELECT 1"/' 20
mutant "M2 相鄰時段也算重疊" 's/start_time < ? AND end_time > ?/start_time <= ? AND end_time >= ?/' 5
```

```
--- M1 拿掉 transaction（跑 20 次）
  14 1 failed, 31 passed
   1 2 failed, 30 passed
   5 32 passed
--- M2 相鄰時段也算重疊（跑 5 次）
   5 2 failed, 30 passed
```

M2 每次都被抓到。M1 拿掉的是它最得意的設計（「the overlap check and the insert happen in a single write transaction」），保護拿掉之後，它的競態測試 20 次裡有 5 次**全綠**；前一批 20 次是 8 次全綠。它自己在報告裡懷疑過「The race tests may also be weak」，懷疑是對的，只是沒有任何結構要求它在宣告完成前證明。突變測試的工具化在 [Ch 32](./32-brownfield-bdd.md) 的 `mutate.py`。

## 底層機制：skill 是怎麼被「執行」的

```
 /autopilot <prompt>
     │  Claude Code 把渲染後的 SKILL.md 當成「一則訊息」放進對話，之後不再重讀檔案
     ▼
 ┌────────────────────────────────── agent loop ──────────────────────────────────┐
 │  模型回應 ── 含 tool_use？── 是 ─▶ 權限檢查 ─┬─ 允許 ─▶ 執行 ─▶ tool_result ─┐ │
 │     ▲                                        └─ 拒絕 ─▶ 拒絕訊息當結果 ──────┤ │
 │     └────────────────────────────────────────────────────────────────────────┘ │
 │           否 ─▶ 最後一段文字 ─▶ Stop hook（若有；exit 2 = 不准停）─▶ 結束      │
 └────────────────────────────────────────────────────────────────────────────────┘
                                                     result: subtype=success
```

**skill 不是程式，是一則訊息。** 官方文件的 [Skill content lifecycle](https://code.claude.com/docs/en/skills#skill-content-lifecycle)：「the rendered `SKILL.md` content enters the conversation as a single message and stays there across later turns … Claude Code does not re-read the skill file on later turns」。`Make sure all tests pass before you finish` 是這則訊息裡的一句話；模型可以照做，也可以把「我寫的測試通過」當成照做了，迴圈裡沒有東西會去檢查那句話。

**迴圈什麼時候停？** 模型不再呼叫工具就停，`subtype: success` 的意思是迴圈沒出錯。真正有牙齒的檢查點都在 skill 文字之外：權限檢查（本章三次執行共 6 次 DENIED）、hook（`PreToolUse` 能擋工具呼叫；`Stop` hook 以 exit code 2 結束時，文件寫的是「Prevents Claude from stopping, continues the conversation」，見 [harness Ch 30](../harness_engineering/30-hooks.md)），以及一支會 pass/fail 的外部 predicate（pstack [Ch 25](../pstack/25-autonomous-run.md) 的「底層機制：為什麼 predicate 必須是一支腳本」）。文件的疑難排解說得更直接：「Claude skipped a rule that must hold every time: move the rule into a hook.」

**長 skill 的尾巴可能不見。** 同一節文件：自動壓縮後，Claude Code 為每個 skill 重新附上「the first 5,000 tokens」，所有 skill 共用 25,000 tokens。repo 的 learn SKILL.md 有 38,664 bytes，「正確性關卡」從第 597 行、「風格指南」從第 685 行開始，都在全檔七成以後。它有幾個 token，[Ch 4](./04-prompt-files-to-skills.md) 用 Claude Code 的估價器估過，最低估值也超過 5,000，所以這兩節多半落在壓縮後保留的開頭之外（確切切點本章沒有量）。結構上的風險是清楚的：越要緊的規則越該放前面，或者乾脆不靠 skill 文字承載。

**turn 數不是你以為的那個 turn。** 三次執行的（tool call 數，模型回應數，result 裡的 `num_turns`）分別是 Run 1（5, 4, 6）、Run 2（25, 9, 26）、Run 3（7, 4, 8），模型回應數取 transcript 裡不同的 message id 數；三次都是 `num_turns` = tool calls + 1，`--max-turns` 都是 20。Run 2 的 26 超過上限 20，卻沒有被截斷，所以 `--max-turns` 計的單位不是 `num_turns`。本章三次的模型回應數都不到 20；[練習 C](./practice-c-custom-sdd-flow.md) 有一次執行在第 20 個模型回應後以 `error_max_turns` 停下，和「一個模型回應算一次」相符，但本章沒有另外驗證，請以你的版本實測為準。不管怎麼計，它是成本的斷路器，不是完成條件。

## 四、skill_metrics.py：把「混亂」量出來

pstack [Ch 28](../pstack/28-skill-authoring.md) 的 `skill-lint.mjs` 檢查格式（frontmatter、名稱、description、連結），[Ch 2](../pstack/02-anatomy-of-poteto-mode.md) 的 `sections.mjs` 量每段字數。我們要量的是**結構**：拆過沒有、規則有沒有例子、完成條件能不能被機器判定。四個門檻的來源：`lines` > 500 來自 Claude Code 文件與 skill-creator 都寫的「Keep SKILL.md under 500 lines」；`unexampled` > 0，因為沒有例子的規則最容易被各自解讀（[Ch 9](./09-rule-files.md) 的主題）；`checkable_done` = 0，因為完成條件要能被腳本判定；`inline_templates` > 1，因為 skill-creator 自己示範的寫法只內嵌一份輸出格式，更多的模板屬於 templates/（[Ch 10](./10-templates-and-scripts.md)）。另外，一個沒關上的 code fence 也算問題。完整原始碼（Python 3.12，只用標準函式庫）：

```python
#!/usr/bin/env python3
"""skill_metrics.py: measure how a skill is built, not how well it is written.

SKILL = a skill directory (with SKILL.md) or a SKILL.md path. Heuristic metrics:
  lines / sections   SKILL.md lines; ##/### headings outside code fences
  rules              list items outside code fences, in every .md file of the skill
  unexampled         rules with no `inline code`, link, "e.g."/"例如", nor a code block in their section
  checkable_done     SKILL.md lines saying done/finish/完成 AND naming a command in backticks
  bundled            files in the skill directory other than SKILL.md
  inline_templates   markdown/text/untagged code blocks in SKILL.md with <placeholders>
Problems: an unclosed fence, lines > --max-lines, unexampled > --max-unexampled,
checkable_done == 0, inline_templates > --max-inline-templates.
Exit 0 = no problems, 1 = problems, 2 = bad input. With --json the summary goes to stderr.
"""
import argparse, json, re, sys
from pathlib import Path

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")
ANCHOR = re.compile(r"`[^`]+`|\]\([^)]+\)|\be\.g\.|for example|例如|比如", re.I)
DONE = re.compile(r"\b(done|finish(ed)?|complete[ds]?)\b|完成|做完|寫完|才算", re.I)
COMMAND = re.compile(r"`(?:python3?|pytest|node|npm|npx|uv|make|bash|sh|\./|ruff|playwright)\b[^`]*`")
PLACEHOLDER = re.compile(r"<(?![/!])([^<>\n]{1,60})>")
HTML = set("details summary br p a div span code pre img b i em strong ul ol li table tr td th "
           "form input button select option label script style section main h1 h2 h3".split())


def blocks(text):
    """(kind, info, lines, fence_line) runs. CommonMark: a closing fence repeats the opening
    character, is at least as long and has no info string; other fence lines are content."""
    out, cur, kind, info, opener, at = [], [], "prose", "", "", 0
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m and kind == "prose":
            out.append((kind, info, cur, at))
            cur, kind, info, opener, at = [], "code", m.group(2).strip().lower(), m.group(1), n
        elif m and kind == "code" and m.group(1)[0] == opener[0] \
                and len(m.group(1)) >= len(opener) and not m.group(2).strip():
            out.append((kind, info, cur, at))
            cur, kind, info, at = [], "prose", "", 0
        else:
            cur.append(line)
    return out + [(kind, info, cur, at)]


def rules_in(text):
    """(rules, unexampled) for one markdown file, judged section by section."""
    secs = [{"items": [], "code": False}]
    for kind, _, lines, _ in blocks(text):
        secs[-1]["code"] |= kind == "code"
        for line in lines if kind == "prose" else []:
            if re.match(r"^#{1,6}\s", line):
                secs.append({"items": [], "code": False})
            elif ITEM.match(line):
                secs[-1]["items"].append(line)
    bare = sum(1 for s in secs if not s["code"] for i in s["items"] if not ANCHOR.search(i))
    return sum(len(s["items"]) for s in secs), bare


def measure(path):
    skill_md = path / "SKILL.md" if path.is_dir() else path
    root, text = skill_md.parent, skill_md.read_text(encoding="utf-8")
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    prose = [line for k, _, ls, _ in blocks(body) if k == "prose" for line in ls]
    counts = [rules_in(body if md == skill_md else md.read_text(encoding="utf-8"))
              for md in sorted(root.rglob("*.md"))]
    return {
        "skill": root.name, "lines": len(text.splitlines()), "bytes": len(text.encode()),
        "sections": sum(1 for line in prose if re.match(r"^#{2,3}\s", line)),
        "rules": sum(r for r, _ in counts), "unexampled": sum(u for _, u in counts),
        "checkable_done": sum(1 for line in prose if DONE.search(line) and COMMAND.search(line)),
        "bundled": sum(1 for f in root.rglob("*") if f.is_file() and f != skill_md),
        "inline_templates": sum(1 for k, info, ls, _ in blocks(body) if k == "code"
                                and info in ("", "markdown", "md", "text", "txt")
                                and any((p.split() or [""])[0].lower() not in HTML
                                        for p in PLACEHOLDER.findall("\n".join(ls)))),
        "unclosed_fence": next((at for k, _, _, at in blocks(text)[-1:] if k == "code"), None),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="print a JSON list instead of text")
    ap.add_argument("--max-lines", type=int, default=500, help="default 500 (Claude Code skills docs)")
    ap.add_argument("--max-unexampled", type=int, default=0)
    ap.add_argument("--max-inline-templates", type=int, default=1)
    a = ap.parse_args()
    results = []
    for p in a.skills:
        if not (p / "SKILL.md" if p.is_dir() else p).is_file():
            print(f"{p}: no SKILL.md here; pass a skill directory or a SKILL.md path", file=sys.stderr)
            return 2
        m = measure(p)
        m["problems"] = [msg for bad, msg in [
            (m["unclosed_fence"], f"code fence opened at line {m['unclosed_fence']} never closes; "
                                  "counts after it are unreliable"),
            (m["lines"] > a.max_lines, f"SKILL.md has {m['lines']} lines, budget {a.max_lines}"),
            (m["unexampled"] > a.max_unexampled, f"{m['unexampled']} rules have no example or anchor"),
            (m["checkable_done"] == 0, "no done criterion that names a command"),
            (m["inline_templates"] > a.max_inline_templates,
             f"{m['inline_templates']} inline templates, max {a.max_inline_templates}")] if bad]
        results.append(m)
        if not a.json:
            print(f"{m['skill']:<13} " + " ".join(f"{k}={m[k]}" for k in ("lines", "sections", "rules",
                  "unexampled", "checkable_done", "bundled", "inline_templates")))
            print("".join(f"  problem: {msg}\n" for msg in m["problems"]), end="")
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    problems = sum(len(m["problems"]) for m in results)
    print(f"{len(results)} skills, {problems} problems", file=sys.stderr if a.json else sys.stdout)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

一支檢查工具要先看它通過一次。`good-mini` 是我們寫來讓它通過的玩具：

```bash
mkdir -p skills/good-mini/rules
cat > skills/good-mini/SKILL.md <<'EOF'
---
name: good-mini
description: Toy skill for checking skill_metrics.py on a passing case. Use when asked to add one booking rule as a pytest-bdd scenario.
---

# good-mini

1. Write one scenario in `features/booking.feature` that follows [rules/scenarios.md](rules/scenarios.md).
2. Run `python -m pytest -q` and copy the failing line into the report.
3. Implement until `python -m pytest -q` passes.

Done when `python -m pytest -q` exits 0 and the report quotes the red line from step 2.
EOF
cat > skills/good-mini/rules/scenarios.md <<'EOF'
# Scenario rules

## SC-1 One When per scenario

- Rule: a scenario has exactly one `When`.
- Good: `When Alice books "Taipei" from 10:30 to 11:30`, then one `Then`.
- Bad: a second `When`, such as `And Alice books "Tokyo" from 10:30 to 11:30`.
EOF
```

把第 3 步改成「Implement until the same command passes.」，它報 `1 rules have no example or anchor`；改回點名 `python -m pytest -q` 就是 `1 skills, 0 problems`。把 autopilot 也放進 `skills/`（`mkdir -p skills/autopilot && cp autopilot-run/.claude/skills/autopilot/SKILL.md skills/autopilot/`），然後量四份 skill：

```bash
python tools/skill_metrics.py skills/bdd-monolith skills/autopilot skills/good-mini \
  ~/Documents/github/Learning-with-Claude/.claude/skills/learn
```

```
bdd-monolith  lines=222 sections=8 rules=33 unexampled=16 checkable_done=0 bundled=0 inline_templates=0
  problem: 16 rules have no example or anchor
  problem: no done criterion that names a command
autopilot     lines=27 sections=2 rules=13 unexampled=13 checkable_done=0 bundled=0 inline_templates=0
  problem: 13 rules have no example or anchor
  problem: no done criterion that names a command
good-mini     lines=12 sections=0 rules=6 unexampled=0 checkable_done=1 bundled=1 inline_templates=0
learn         lines=821 sections=39 rules=79 unexampled=60 checkable_done=0 bundled=0 inline_templates=12
  problem: code fence opened at line 664 never closes; counts after it are unreliable
  problem: SKILL.md has 821 lines, budget 500
  problem: 60 rules have no example or anchor
  problem: no done criterion that names a command
  problem: 12 inline templates, max 1
4 skills, 9 problems
```

同一批 skill 給 `node /tmp/lab/tools/skill-lint.mjs skills` 看，結果是 `3 skills, 0 problems`：格式完全合法。兩次 skill-creator 的草稿對照（`python tools/skill_metrics.py drafts/run1 skills/bdd-monolith`）：Run 1 是 `lines=242 sections=7 rules=22 unexampled=5 inline_templates=1`，Run 3 是 `lines=222 sections=8 rules=33 unexampled=16 inline_templates=0`，兩份都 `checkable_done=0`。同一個 prompt，規則數一份 22、一份 33。怎麼讀這些數字：

- **autopilot 的 13 條規則沒有一條有錨點**：「Follow best practices」「Make sure all tests pass」的意思全靠模型自己決定。
- **bdd-monolith 的 16 條**集中在沒有 code block 的段落：Phase 4 的「Take one failing scenario at a time…」、Finish with a report 的三條、Team app 的業務規則。其中有誤判：「Describe behavior, not mechanics」其實附了例子，只是用雙引號不是反引號；「The rooms are Taipei, Tokyo, and Seoul.」是事實，不是規則。啟發式的數字是煙霧警報器，不是裁判。
- **learn 的 `line 664 never closes` 是真的結構問題**：模板用 ```` ```markdown ```` 包住，模板裡又有 ```` ``` ```` 開頭的範例。[CommonMark 規格](https://spec.commonmark.org/0.31.2/#fenced-code-blocks)規定「Closing code fences cannot have info strings」，第一個裸的 ```` ``` ```` 就關掉外層，沒關上的 fence 則一路延伸到檔尾。所以任何照規格解析的工具都看不出原本的模板邊界，最後一段吞掉了風格指南。內嵌模板不只佔 context，連「模板在哪裡結束」都表達不清。另外我們不管 fence、逐行掃全檔：21 行提到完成，0 行點名指令。

## 五、事後檢討：症狀 → 結構原因

| # | 症狀 | 證據 | skill 結構上的原因 | 之後誰來修 |
|---|---|---|---|---|
| 1 | 第一個動作是 723 行的單一指令，想一次寫完 | tool_calls 第 2 列 DENIED | 「Build the whole thing in one go」，沒有階段與檢查點 | [Ch 16](./16-tasks-binder.md) 的 tasks、[Ch 31](./31-bdd-in-sdd.md)：一個 task 一個驗證指令 |
| 2 | 加了沒人要的 SQLite、`date`、DELETE、競態測試，759 行 | 最後訊息、`wc -l` | 「Prefer a complete feature set」，沒有 out-of-scope | [Ch 8](./08-translation-sandwich.md) example map、[Ch 18](./18-clarify-skill.md) clarify |
| 3 | 任何人都能取消別人的預約；R1 訊息格式不同 | B 組 2 failed | 團隊契約不在 skill 也不在 prompt；「Never ask」把決策埋進程式碼 | [Ch 8](./08-translation-sandwich.md)、[Ch 9](./09-rule-files.md) |
| 4 | 「32 tests pass」為真，拿掉 transaction 卻有 5/20 全綠 | `mutants.sh` | 完成條件是「自己寫的測試通過」，沒有獨立 predicate | [Ch 20](./20-tdd-with-agents.md) Stop hook、[Ch 25](./25-tdd-in-sdd-pipeline.md) RED 閘門 |
| 5 | 沒在真的 server 與瀏覽器上跑過 | 最後訊息、tool_calls 第 15 列 | skill 沒定義證據；被擋時沒有替代路徑 | [Ch 22](./22-backend-e2e-first.md)、[Ch 23](./23-frontend-e2e-playwright.md) |
| 6 | bdd-monolith 16/33 條規則沒例子，0 條可檢查的完成條件 | `skill_metrics.py` | 單檔、規則和例子分開、done 寫成散文 | [Ch 9](./09-rule-files.md)–[Ch 11](./11-skill-engineering-skill.md) |
| 7 | 同一 prompt 兩份草稿差 20 行、11 條規則，都發明了時區 | 兩份草稿的 metrics | 只取 skill-creator 的第一版，等於取一個隨機樣本 | [Ch 5](./05-skill-creator.md) eval、[Ch 11](./11-skill-engineering-skill.md) |

公平地說，這個 autopilot 的模型表現不差：A 組全過，最後訊息誠實列出沒驗證的項目。但那份誠實來自模型，不來自結構；skill 只要求「a short summary」。換一個模型、或同一個模型的下一次執行，沒有任何東西保證它會說「I never ran it on a real server」。對照 SDD [練習 F](../spec_driven_development/practice-f-mini-sdd-pipeline.md) 的「心智圖像：pipeline 的五個隘口」：spec 解析、每個 task 單獨呼叫 agent、跑測試、逐條驗收、出報告，每個隘口都由程式判斷。pstack [Ch 27](../pstack/27-multi-phase-autopilot.md) 的 Autopilot-full 更要求「nothing merges until your swarm, which never wrote the code, has proven the code that lands」。我們的 autopilot 一個隘口都沒有。工具層級的範圍膨脹，SDD [Ch 40](../spec_driven_development/40-empirical-evidence.md) 的「Böckeler 的 scope inflation」有實測；Kiro Autopilot 在大任務上刪程式碼不還原的案例在 SDD [Ch 32](../spec_driven_development/32-tool-comparison.md) 的錯誤直覺 6；「一個 skill 做十件事」的反例在 AI 應用 [Ch 15](../ai_applications/15-skills.md) 的「反例:過度設計的 skill」。

## 對比與取捨

| 做法 | 誰判定「做完」 | 檢查點 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|---|
| 單檔 skill（bdd-monolith） | agent 讀散文自己判斷 | 無 | 一個檔、好分享，內容可以寫得很好 | 規則與例子分離；done 不可檢查；變長後壓縮會截尾 | 個人、短任務、草稿 |
| autopilot skill | agent | 無 | 快，產物看起來完整 | 範圍膨脹；契約進不來；證據靠自述 | 拋棄式原型 |
| 分階段 pipeline（SDD 練習 F） | 腳本（pytest exit code、逐條驗收） | 每個 task | 可重跑、可比較 | 要自己寫、自己維護 | 功能等級的工作 |
| pstack Autopilot-stack（Ch 27） | 沒寫 code 的 swarm 加人 | 每個 PR | 規模大時仍可控 | 成本高、要基礎設施 | 多 PR 的計畫 |
| 模組化 skill 加 hook（Ch 9–11、20、27） | hook 與 script | 每次工具呼叫或要停時 | 規則可執行，不靠記得 | 檔案多、要維護 | 團隊長期使用 |

## 踩雷集錦

1. **錯誤直覺：`result: success`、exit 0 就是做完了。** 正確認識：Run 1 兩者都成立，SKILL.md 卻根本沒寫出來。`success` 只說迴圈沒出錯；完成與否要看產物在不在、驗收過不過。
2. **錯誤直覺：skill-creator 的草稿就是成品。** 正確認識：它自己的流程是 draft → run test prompts → review → improve → repeat。只取第一版等於取一個隨機樣本：同一 prompt 的兩份草稿，規則數一份 22、一份 33，兩次都把城市名稱腦補成時區。
3. **錯誤直覺：SKILL.md 寫了「Make sure all tests pass」就有檢查站。** 正確認識：那是一則訊息裡的一句話。測試是 agent 自己寫的，拿掉最重要的保護後仍有 5/20 全綠。要擋，用 `Stop` hook 或外部 predicate（[Ch 20](./20-tdd-with-agents.md)）。
4. **錯誤直覺：設了 `--max-turns` 就不會失控。** 正確認識：Run 2 的 `num_turns` 26 超過 20 也沒被截斷，計數單位和你看到的數字不同；而且它只能切斷，不能判斷做完。
5. **錯誤直覺：metrics 數字越低，skill 越好。** 正確認識：`skill_metrics.py` 量結構不量內容，會把事實當規則、把雙引號裡的例子當沒有例子；learn 的 fence 問題更讓後段計數全部打折。先讀定義，再讀數字。

## 進階：transcript 是資料，不是 log

stream-json 的每一行都是結構化事件：`system/init` 記錄模型、工具、載入的 plugin 與 skill；`assistant` 裡的 `tool_use` 是 agent 想做什麼，`user` 裡的 `tool_result` 是實際發生什麼；`system/permission_denied` 帶拒絕理由；最後的 `result` 有 `num_turns`、`total_cost_usd`、`permission_denials`。事後檢討因此可以寫成程式：`tool_calls.jq` 只是第一步，被拒的呼叫、`result` 與最後訊息可以挖成候選事故，由人判斷是不是「agent 出錯、而且有人為它花了時間」，再記進 [Ch 6](./06-workflows-are-grown.md) 的 `ledger.yaml`（`ledger.py` 只讀這份人記的帳本，不讀 transcript）；同樣的抽取方式也能比較兩次執行間的差異。另一個值得想的問題：讓 skill-creator 跑完自己的迴圈，第五節哪些列會改善？eval 加 assertions 能抓到「範例沒跑過」，description 最佳化改善的是觸發率；兩者都不會讓 skill 長出一個可檢查的完成條件，也不會把 R4 這種團隊規則帶進來。那是結構的工作，也就是 Ch 9 到 Ch 11 的內容。

## 動手練習

1. 換一個你自己的小 app 需求，用 `run2-autopilot.sh` 的旗標跑一次 autopilot（`--max-turns` ≤ 20），用 `tool_calls.jq` 讀 transcript，填一張和第五節同欄位的事後檢討表，「證據」欄要是實際輸出。
2. 它說頁面「never ran … in a browser」。用 Playwright 起一個真的 uvicorn、在頁面上預約一次再撞一次（[Ch 23](./23-frontend-e2e-playwright.md) 會正式教 locator 與 `expect`），把結果補進你的事後檢討表。
3. 把 `test_probe_api.py` 的 B 組換成你團隊真的有、但 prompt 沒寫的兩條規則，先預測它們怎麼失敗，再跑一次對答案。然後對你常用的一個 skill 跑 `skill_metrics.py`：替一條 unexampled 規則補例子、把一句「做完」改寫成點名指令的完成條件，再跑一次，看哪個 problem 消失。

## 本章重點整理

- 混亂基準線要真的跑、真的量：`bdd-monolith` 是 skill-creator 實際寫出的草稿（同一 prompt 跑兩次，兩份不同），autopilot 的 app 來自一次 25 個 tool call 的實際執行。
- `.claude/` 是受保護路徑，寫 skill 的 agent 在 `-p` 模式寫不進去；先寫到 `drafts/`，人 review 後再搬。
- skill 是一則訊息，不是程式；`subtype: success` 只代表迴圈沒出錯。有牙齒的檢查點在 skill 文字之外：權限、hook、外部 predicate。
- autopilot 做對了 prompt 說出口的部分（A 組 5/5），做不到沒說出口的部分（B 組 0/2），它最重要的保護只被自己的測試在 15/20 次抓到。
- `skill_metrics.py` 量結構；格式合法（skill-lint 0 problems）不等於結構健康。

## 自我檢核

- [ ] 不看本章，說出 Run 1 為什麼 `subtype` 是 `success`、檔案卻不存在，以及 Claude Code 為什麼這樣設計。
- [ ] 用自己的話解釋：SKILL.md 裡的「Make sure all tests pass」和一個擋住停止的 `Stop` hook，為什麼是兩種強度不同的保證？
- [ ] autopilot 的 B 組探針失敗，是程式寫錯還是別的原因？換一個更強的模型能不能解決？
- [ ] 如果只能在 autopilot skill 裡加一行字、或加一個 hook，你選哪個？加什麼？

## 延伸閱讀

- **[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)**：Erik Schluntz、Barry Zhang（Anthropic，2024-12-19）
  - **讀哪裡**：「Workflow: Prompt chaining」的 gate 一段，與「Agents」一節談 stopping conditions、compounding errors 的段落。
  - **學到什麼／關聯**：workflow（預定路徑，中間可插程式檢查）與 agent（自己決定路徑）的分野；第五節「之後誰來修」那一欄，多半就是把 gate 補回 workflow。
- **[Claude Code 文件：Skills](https://code.claude.com/docs/en/skills)**
  - **讀哪裡**：「Skill content lifecycle」整節，以及 Troubleshooting 的「Claude stops following a skill」。
  - **學到什麼／關聯**：skill 何時進對話、壓縮後保留多少（5,000／25,000 tokens），以及官方建議把每次都必須成立的規則移進 hook。前提是知道 skill 的三層載入（[Ch 4](./04-prompt-files-to-skills.md)）。
- **[skill-creator 的 SKILL.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/skill-creator/skills/skill-creator/SKILL.md)**（`anthropics/claude-plugins-official`）
  - **讀哪裡**：「Write the SKILL.md」的 pushy description 建議、「Progressive Disclosure」的 500 行上限、「Improving the skill」的四條原則。
  - **學到什麼／關聯**：本章只用了它的草稿步驟；讀完你會知道被跳過的 eval 迴圈原本要補什麼、補不了什麼。

我們有了量過的混亂基準線，也看到 autopilot 掛在 B 組：不是程式寫錯，而是團隊的規則從來沒進到 prompt。下一章往上游追，看需求在進到 agent 之前，就在 Sales、PM、RD 之間掉了什麼。

→ [Ch 8 需求翻譯三明治：Sales、PM、RD 之間掉了什麼](./08-translation-sandwich.md)
