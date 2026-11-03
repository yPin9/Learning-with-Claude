# Ch 12 — Spec Kit 1.x 重訪：指令變成 skill 之後

> **目標**：用 Spec Kit 1.2.5 實際 `specify init` 一個會議室預約專案，說得出它裝進 Claude Code 的 10 個 `speckit-*` skill 與 `.specify/` 裡 20 個檔案各管什麼；用 `skill_io_map.py` 從 SKILL.md 抽出「誰跑哪支腳本、誰讀 constitution、誰提到哪份產物」，再拿一次真實的 `/speckit-specify` 對照；分得清哪些關卡是腳本擋下的硬閘門、哪些只是寫給模型看的文字；知道從 SDD 課的 0.11.10 到 1.2.5 變了什麼，以及改了安裝出來的 skill 會被誰抓到。

> **環境**：Spec Kit 1.2.5（`uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify`，對照組 0.11.10）、Claude Code 2.1.295（模型 claude-opus-5-5）、macOS 26.6、Python 3.12.15 + PyYAML 6.0.3。所有指令與輸出都是 2026-11-02 在 `/tmp/lab/aibdd/ch12-speckit/` 實跑；兩段 `claude -p` 是實際執行紀錄，模型每次的輸出不同。

## 為什麼需要這個？

SDD 課用三章加一個練習講 Spec Kit：[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) 安裝與 scaffold、[SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 走完 `/speckit.*` 工作流、[SDD Ch 29](../spec_driven_development/29-spec-kit-internals.md) 拆三層架構、[SDD 練習 E](../spec_driven_development/practice-e-spec-kit-run.md) 讓你自己跑一次。為什麼要先寫規格、constitution、spec、plan 各是什麼，那幾章講過，本章不重講。它們釘在 0.11.10（2026-06-29），工作流那章的 spec 範例明標「示意」，plan 與 tasks 範例也不照模板的段落；原本照文件寫成的幾處錯，是我們實跑之後才改正的（[Ch 1](./01-course-map.md)「盤點時順手修掉的既有錯誤」）。

我們把兩個版本都實際跑了一次，結果和文件描述不同。第一，0.11.10 的 `specify init --integration claude` 裝出來的已經是 `.claude/skills/speckit-*/SKILL.md`，不是 SDD Ch 27 原本照文件畫的 `.claude/commands/speckit.*.md`（那張圖已依實跑重畫）；Spec Kit 的 CHANGELOG 記著 0.4.5（2026-04-02）「Install Claude Code as native skills」，「指令變成 skill」發生在 SDD 課選定的版本之前。第二，0.11.10 到本課的 1.2.5（2026-10-28）之間，CHANGELOG 又記了 62 個 release：constitution 不再改寫模板、agent context 改成選用、多了 `specify artifact` 等等。

Part 3 後面幾章要拆 `/plan`（[Ch 13](./13-plan-anatomy-review.md)、[Ch 15](./15-research-and-analysis-skills.md)）、綁 tasks（[Ch 16](./16-tasks-binder.md)）、用 preset 改 `/specify`（[Ch 17](./17-constitution-and-presets.md)）、自己寫 clarify skill（[Ch 18](./18-clarify-skill.md)）。要改一個系統，得先看清楚它實際裝了什麼、每個零件讀寫什麼、改了以後升級會怎樣。本章給的就是這張現況圖，同時用 1.2.5 的實際行為複習外部課綱的 3-3、3-4、3-5、3-8：4+2 流程、constitution 與 specify、clarify、tasks 與 implement。

## 先建立直覺：連鎖店的作業卡、空白表單和封條

想像一家連鎖餐廳開新分店，總部寄來一箱東西：每個工作站一張**作業卡**（接單先做什麼、用哪張表、做完交給誰），一疊**空白表單**和一台**打卡機**（前一站的單子沒交，打卡機不讓你打卡），一張貼在牆上的**店規**，還有一份**封條清單**記著箱裡每樣東西的序號。下次總部寄新版來，會先對封條；你在作業卡上加過註記，封條就對不上。

Spec Kit 1.2.5 裝進專案的東西可以一一對上：

```
你在 Claude Code 打 /speckit-plan（或模型自己決定叫用）
      │
      ▼
.claude/skills/speckit-plan/SKILL.md      作業卡：10 個 skill，模型讀了照做
      │  "Run .specify/scripts/bash/setup-plan.sh --json"
      ▼
.specify/scripts/bash/*.sh                打卡機：找出目前的 feature、複製模板，
      │                                   缺前置產物就 exit 1
      │  讀 .specify/feature.json → specs/001-meeting-room-booking/
      ▼
.specify/templates/plan-template.md       空白表單（可被 override、preset 蓋過）
      ▼
specs/001-meeting-room-booking/plan.md …  產物

.specify/memory/constitution.md           店規：每個 skill 都提到它
.specify/integrations/*.manifest.json     封條：每個受管檔的 SHA-256
```

這個比喻有一條邊界：作業卡上的「先看店規」「錯了就停」都只是文字，**沒有任何程式強迫模型照做**。真正會擋下來的是打卡機（腳本的 exit code）和封條（升級時比對雜湊），再加上進階一節的 workflow 閘門。分清楚這兩種關卡，是本章最重要的一件事。

> 如果你對 skill 的載入機制（清單、frontmatter、漸進揭露）還不熟，先回看 [Ch 4](./04-prompt-files-to-skills.md) 的「底層機制：Claude Code 怎麼列出、怎麼載入一個 skill」。

## 一、實跑 specify init：30 個檔案

從空目錄開始。`--non-interactive` 讓它不等方向鍵輸入；`--integration claude` 一定要寫，不寫的後果見踩雷第 1 條。最後兩行存下檔案清單（第五節拿它和 0.11.10 比），再把乾淨的 init 狀態 commit，底層機制的 `drift/` 與進階的 `wf/` 都從它 `git clone`：

```bash
mkdir -p /tmp/lab/aibdd/ch12-speckit/booking && cd /tmp/lab/aibdd/ch12-speckit/booking
git init -q && git -c user.name=lab -c user.email=lab@example.com commit -q --allow-empty -m empty
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify init \
  --here --force --non-interactive --integration claude --script sh
find . -path ./.git -prune -o -type f -print | sort > ../tree-1.2.5.txt
git add -A && git -c user.name=lab -c user.email=lab@example.com commit -q -m "specify init 1.2.5"
```

```
…（略：banner 與「目錄不是空的，--force 直接合併」的警告）
Selected coding agent integration: claude
Selected script type: sh
Initialize Specify Project
├── ● Check required tools (ok)
├── ● Select coding agent integration (claude)
├── ● Select script type (sh)
├── ● Install integration (Claude Code)
├── ● Install shared infrastructure (scripts (sh) + templates)
├── ● Ensure scripts executable (6 updated)
├── ● Constitution setup (copied from template)
├── ● Install bundled workflow (speckit installed)
└── ● Finalize (project ready)
Project ready.
…（略：三個框。Next Steps 列出 /speckit-constitution、-specify、-plan、-tasks、-implement、-converge；另一框列出選用的 -clarify、-analyze、-checklist）
```

整個 init 4.4 秒（uvx 快取是熱的），`wc -l < ../tree-1.2.5.txt` 數出 30 個檔案。依 `find` 的實際清單整理成樹，註解是我們加的：

```
.claude/skills/speckit-{constitution,specify,clarify,plan,checklist,
                        tasks,analyze,implement,converge,taskstoissues}/SKILL.md   10 個 skill
.specify/memory/constitution.md               從模板複製，內容還是 [PROJECT_NAME] 這類佔位符
.specify/memory/.constitution-template.json   記下它來自哪份模板（sha256、source: core）
.specify/templates/{spec,plan,tasks,checklist,constitution}-template.md
.specify/scripts/bash/{check-prerequisites,common,create-new-feature,
                       resolve-template,setup-plan,setup-tasks}.sh
.specify/workflows/{speckit/workflow.yml,workflow-registry.json}   內建 workflow「Full SDD Cycle」與登記簿
.specify/integrations/{claude,speckit}.manifest.json   SKILL.md／共用腳本與模板的 SHA-256
.specify/integration.json                     裝了哪些 integration、預設哪個、invoke_separator "-"
.specify/init-options.json                    ai_skills: true、script: sh、feature_numbering: sequential
.specify/.gitignore                           本機狀態不進版控，第一條規則就是 feature.json
```

**沒有**出現的東西同樣重要：根目錄沒有 `CLAUDE.md`，沒有 `.claude/settings.json`，沒有 `specs/`（第一次 `/speckit-specify` 才建），也沒有建任何 git 分支。

## 二、skill 從哪裡來、怎麼被叫起來

打開 `speckit-plan/SKILL.md` 的 frontmatter：

```yaml
---
name: "speckit-plan"
description: "Execute the implementation planning workflow using the plan template to generate design artifacts."
argument-hint: "Optional guidance for the planning phase"
compatibility: "Requires spec-kit project structure with .specify/ directory"
metadata:
  author: "github-spec-kit"
  source: "templates/commands/plan.md"
user-invocable: true
disable-model-invocation: false
---
```

依 Claude Code 官方的 frontmatter 說明：`argument-hint` 是打 `/` 時的自動完成提示；`user-invocable` 預設 true，代表出現在 `/` 選單；`disable-model-invocation: false` 代表**模型可以自己決定載入它**；`compatibility`（環境需求）與 `metadata`（自由的鍵值）來自 Agent Skills 規格。`metadata.source` 透露它的出身：Spec Kit 原始碼只維護一套 `templates/commands/*.md`，`specify integration list` 在 1.2.5 列出 43 個 integration key，除了只裝共用骨架的 `none`，各自把這套模板轉成自己 agent 的格式。Claude 的轉換器在 `src/specify_cli/integrations/claude/__init__.py`。拿 v1.2.5 原始碼（實驗室解在 `/tmp/lab/bdd/speckit-src/`，我們用 curl 抽查過它和 tag 上的檔案相同）的 `templates/commands/plan.md` 和安裝出來的 SKILL.md 做 `diff`，看得到轉換做了什麼：frontmatter 的 `handoffs` 被丟掉，`scripts` 拿來把本文的 `` Run `{SCRIPT}` `` 換成 `` Run `.specify/scripts/bash/setup-plan.sh --json` `` 之後也拿掉，`/memory/constitution.md` 補成 `.specify/memory/constitution.md`，前後兩段 hook 說明各插入一句「dots 換成 hyphens」的叫用規則。

為什麼要轉成 skill，而不是留在 `.claude/commands/`？Claude Code 官方文件寫明「Custom commands have been merged into skills」：`.claude/commands/deploy.md` 與 `.claude/skills/deploy/SKILL.md` 都產生 `/deploy`，舊格式照樣能用，但 skill 多了自己的資料夾（可以放附屬檔案）與完整的 frontmatter，例如讓 skill 在 subagent 裡跑的 `context: fork`。Spec Kit 真的用過這一點，也退過：CHANGELOG 記著 0.11.3「run /analyze in a forked subagent」，0.11.9 又以「prevent long-session freezes」為由拿掉。

同一個步驟因此有好幾種拼法：Claude Code 裡打 `/speckit-plan`（`integration.json` 的 `invoke_separator: "-"`）；點號 `speckit.plan` 是 Spec Kit 文件與 `workflow.yml` 的寫法，也是 Gemini 這類裝指令檔的 agent 實際打的 `/speckit.plan`；Codex 是 `$speckit-plan`，Kimi 是 `/skill:speckit-plan`。Claude Code 的 `/` 選單裡只有 `speckit-plan` 這個名字；SDD 課沿用點號 `/speckit.plan` 指稱指令（[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) 的「名稱注意」提醒在 Claude Code 裡換成連字號），照打會對不上任何一個 skill。

`disable-model-invocation: false` 的代價是每一輪都付清單錢。另建一個 init 時多加 `--integration-options="--no-model-invocation"` 的專案 `nomodel/`，用 [Ch 4](./04-prompt-files-to-skills.md) 的 `context_cost.py` 比較（`python ../ch04-prompt-files/context_cost.py booking nomodel`）：`booking` 回報 `every turn: 1,339 B   only when used: 135,302 B`，`nomodel` 是 `every turn: 0 B`，10 個 skill 的 frontmatter 都變成 `disable-model-invocation: true`。1,339 B 不多；問題不在錢，在**會被叫用**。我們在 `booking` 的一份副本（已含第四節產生的 spec 001）裡，用一句完全沒提 Spec Kit 的請求實測：

> 實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。指令：`claude -p "Write a feature specification for cancelling a booking: only the person who made a booking can cancel it, and cancelling a booking that does not exist is an error." --max-turns 20 --allowedTools "Skill,Read,Write,Edit,Glob,Grep,Bash(mkdir *),Bash(ls *)" --permission-prompts none --setting-sources project --strict-mcp-config --no-session-persistence --output-format stream-json --verbose < /dev/null`，再用 Ch 4 的 `timeline.jq` 排成時間線。

```
assistant [16385 tok in] Skill {"skill":"speckit-specify","args":"Cancelling a booking: o
user      tool_result Launching skill: speckit-specify
user      text Base directory for this skill: /private/tmp/lab/aibdd/ch12
assistant [23200 tok in] Bash {"command":"ls -la && ls -la .specify .specify/templates s
```

模型的第一個動作就是自己叫用 `speckit-specify`。載入前後輸入從 16,385 變成 23,200 tok，差 6,815；把 10 個 skill 照 Ch 4 的做法包成臨時 plugin 跑 `claude --plugin-dir … plugin details`，它對這個 skill 估的是「on-invoke ~6.8k」，兩者相符（這個估價要登入才呼叫 count_tokens API；換一個沒有登入資料的 `CLAUDE_CONFIG_DIR` 重跑，它退回用字元數估，只給 ~4.6k）。這次 11 回合、72 秒，CLI 估的費用 0.43 美元；結束後 `.specify/feature.json` 從 001 改指向 `specs/002-cancel-booking`（踩雷第 2 條會用到）。

## 三、.specify/ 之外：specify 的子指令

1.2.5 的 `specify --help` 列出 12 個子指令，比 0.11.10 的 9 個多了 `artifact`、`event`、`mcp`（兩版的 `--help` 實際比對；`init`、`check`、`version` 不列在下表）。「本章實跑」欄都是在 `booking/` 執行的結果：

| 子指令 | 做什麼 | 本章實跑 | 本課哪裡用到 |
|---|---|---|---|
| `integration` | 安裝、移除、切換、升級 agent integration；`status` 回報封條是否完好 | `status` → `Integration status: OK` | 底層機制 |
| `preset` | 用 preset 覆蓋模板與指令；`resolve` 追解析堆疊 | `preset list` → `No presets installed.` | Ch 17 |
| `extension` | 加新指令與 hook；內附 git、agent-context、assess、bug、github 五個，都要自己 `add` | `extension list` → `No extensions installed.` | 底層機制 |
| `workflow` | 用 YAML 串步驟與閘門，逐步派給 agent CLI | `workflow list` → `Full SDD Cycle (speckit) v1.0.1` | 進階 |
| `bundle` | 把 preset、extension、workflow 打包成一包 | `bundle list` → `No bundles installed.` | — |
| `artifact` | 列出指令、模板、腳本與組成堆疊（1.0.7 起） | 不加 `--json` 回 exit 2；加了是 10 個 command、5 個 template、4 個 script（沒有 `create-new-feature`，原因見下一節） | — |
| `event` | 執行由 agent 原生 hook 觸發的指令腳本；預設沒有 handler，所以沒寫 `.claude/settings.json` | 未執行 | — |
| `self` | 檢查與升級 CLI 本身 | `self check` → `Up to date: 1.2.5` | — |
| `mcp` | 實驗性的 stdio MCP server（1.1.1 起） | 未執行 | — |

## 底層機制：feature.json、兩種關卡與封條

### 誰決定「現在是哪個 feature」

在還沒有任何 spec 的專案裡，跑 implement 開工前的那道檢查：

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks; echo "exit=$?"
```

```
ERROR: Feature directory not found. Set SPECIFY_FEATURE_DIRECTORY or run the specify command to create .specify/feature.json.
ERROR: Failed to resolve feature paths
exit=1
```

錯誤訊息直接給了答案。`common.sh` 的 `get_feature_paths` 依序看環境變數 `SPECIFY_FEATURE_DIRECTORY`、`.specify/feature.json` 的 `feature_directory`，兩者都沒有就報錯。**git 分支不在這條路上**：`get_current_branch` 只讀環境變數 `SPECIFY_FEATURE`，連 `git` 都不叫。第四節的 `/speckit-specify` 寫好 `feature.json` 後再跑同一行，錯誤變成 `ERROR: plan.md not found in …/specs/001-meeting-room-booking` 與 `Run /speckit-plan first to create the implementation plan.`，仍是 exit 1。這時 `check-prerequisites.sh --json --paths-only` 回報 `"BRANCH": "001-meeting-room-booking"`，`git branch --show-current` 卻印 `main`：JSON 裡叫 BRANCH 的欄位，裝的其實是 feature 目錄名。

「branch 就是索引」是更早的設計（SDD Ch 29 的「`create-new-feature.sh` 的邏輯」原本這樣寫，已照 0.11.10 的腳本改正）。CHANGELOG 記著 0.5.1 把 git extension 做成內附、在核心指令掛上 hook，0.10.0 把它改成選用；0.11.10 與 1.2.5 的 specify skill 都寫明「建分支交給 git extension 的 `before_specify` hook」，也都沒有核心 skill 呼叫 `create-new-feature.sh`；它留下來當手動工具，而 `artifact list` 的內建腳本只從指令模板的 `scripts:` 收集，所以清單上沒有它。`create-new-feature.sh --json --dry-run --short-name cancel-booking "Cancel a booking"` 印出 `{"BRANCH_NAME":"002-cancel-booking",…,"FEATURE_NUM":"002","DRY_RUN":true}`，什麼都不建。

### 硬閘門與軟約束

**硬閘門**（hard gate）是程式執行、不滿足就停的檢查；**軟約束**是寫在 SKILL.md 裡、靠模型自律的句子：

| 關卡 | 誰執行 | 不滿足時 | 依據 |
|---|---|---|---|
| implement、analyze 前要有 plan.md 與 tasks.md | `check-prerequisites.sh` | exit 1，訊息指出該先跑哪一步 | 上面實跑 |
| plan 前要有 spec.md | 沒有程式檢查 | 在副本裡跑 `SPECIFY_FEATURE_DIRECTORY=specs/009-ghost .specify/scripts/bash/setup-plan.sh --json`，照樣印 `Copied plan template to …/009-ghost/plan.md`、exit 0，還把 `feature.json` 改指向這個沒有 spec 的目錄 | 實跑 |
| specify「最多 3 個 `[NEEDS CLARIFICATION]`」 | 模型 | 沒有程式數 | `speckit-specify/SKILL.md` 第 130 行 |
| plan「ERROR on gate failures」（Constitution Check） | 模型 | 沒有程式檢查 | Ch 13 拆解 |
| analyze「STRICTLY READ-ONLY」 | 模型 | 沒有程式阻止寫檔 | `speckit-analyze/SKILL.md` 第 64 行 |
| workflow 的 `gate` 步驟 | `specify workflow` 引擎 | 沒有 TTY 就暫停 | 進階實跑 |
| 改過的 skill 不准被升級蓋掉 | `specify integration upgrade` | exit 1 | 下面實跑 |

「誰執行」欄寫著「模型」的那幾列，就是 Part 3 要補的洞：Ch 13 的 `plan_lint.py` 檢查 Constitution Check 與未解的標記，Ch 18 的 `clarify_check.py` 檢查 `[NEEDS CLARIFICATION]` 清了沒，都是把其中一列換成程式。

### 封條：manifest 雜湊

`.specify/integrations/` 底下的兩份 manifest（安裝清單）記的是每個受管檔案內容的 SHA-256：`shasum -a 256 .claude/skills/speckit-specify/SKILL.md` 印出 `d4d93f2444355136e78bb533988aa2dd560dc4b258f4923f67b1a437e7de38f8`，和 manifest 裡那一筆一字不差。開一份副本 `drift/`，替 specify 加一條公司規則，模擬最常見的客製化：

```bash
cd /tmp/lab/aibdd/ch12-speckit && git clone -q booking drift && cd drift
printf '\n## House rule\n\nWrite every acceptance scenario as Gherkin.\n' >> .claude/skills/speckit-specify/SKILL.md
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify integration status
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify integration upgrade claude; echo "exit=$?"
```

```
Integration status: WARNING
…（略）
- warning managed-files-modified (claude): 1 managed file(s) were modified for integration 'claude'.
  Suggestion: Review the changes before running `specify integration upgrade --force`.
⚠  1 file(s) have been modified since installation:
    .claude/skills/speckit-specify/SKILL.md
Use --force to overwrite modified files, or resolve manually.
exit=1
```

`status` 只是 WARNING（exit 0），`upgrade` 才擋下來（exit 1）。加上 `--force` 升級成功，之後 `grep -c "House rule"` 印 `0`：規則消失了。模板的待遇不同：在另一份副本改 `spec-template.md` 再跑不帶 `--force` 的 `upgrade`，它印 `Preserved 1 customized shared infrastructure file(s) (hash differs from previous install)` 後照常升級，改過的模板留下。刪掉受管腳本 `setup-tasks.sh` 則得到 `Integration status: ERROR`、`error managed-files-missing (speckit)`、exit 1，`integration upgrade claude` 會補回來。封條管得到「檔案被動過」，管不到「動過以後還接不接得上」，後者要靠第四節的腳本。

## 四、跑一次 /speckit-specify，再畫產物地圖

第一節已把 init 的狀態 commit，`git status` 因此能告訴我們模型動了哪些檔案。實跑：

> 實際執行紀錄（節錄），每次執行結果不同（2026-11-02）。旗標同第二節，只是 `--allowedTools` 多一個 `Bash(date *)`、沒接 `< /dev/null`；prompt 是 `/speckit-specify` 加一段 84 個英文字的描述，內容是本課的 R1、R2、R4 與半開區間，全文：`/speckit-specify Meeting room booking for one office. People (Alice, Bob, Carol) book the rooms Taipei, Tokyo and Seoul for a time slot on the same day, given as HH:MM start and end. A room cannot be booked twice for overlapping times; slots are half-open, so 10:00-11:00 and 11:00-12:00 do not overlap. The end must be after the start. A rejected booking must say which booking it collides with, e.g. "Taipei is taken 10:00-11:00 by Bob". Only the person who made a booking can cancel it.`

```
assistant [23506 tok in] Bash {"command":"ls -la .specify .specify/templates .specify/me
user      tool_result ls: specs: No such file or directory .specify: total 24 dr
…（略：三個 Write，依序是 spec.md、checklists/requirements.md、.specify/feature.json；一個 Edit 修 spec.md）
assistant [35258 tok in] text The spec is written and ready for `/speckit-plan`. All 16 
```

6 回合、73 秒、CLI 估 0.39 美元，`permission_denials` 是空的。這次第一則訊息就有 23,506 tok：使用者打斜線指令時，skill 本文直接展開進訊息，不經過 Skill 工具。模型照 SKILL.md 先看了 `init-options.json`（決定編號方式）與 `extensions.yml`（有沒有 hook），然後寫檔：

```bash
git status --short --untracked-files=all
```

```
?? specs/001-meeting-room-booking/checklists/requirements.md
?? specs/001-meeting-room-booking/spec.md
```

第三個檔 `.specify/feature.json`（內容是 `"feature_directory": "specs/001-meeting-room-booking"`）沒出現，因為 `.specify/.gitignore` 排除了它。`grep -c` 數出 16 條 FR、0 個 `NEEDS CLARIFICATION`。spec 用的是 1.2.5 真正的模板骨架（SDD Ch 28 原本重建成 Overview／User Journeys，現在的示意已換成這副骨架）：

```
### User Story 1 - Book a free room (Priority: P1)
…（略）
3. **Given** Bob holds Taipei 10:00-11:00, **When** Alice books Taipei 11:00-12:00, **Then** the booking is accepted, because a slot that starts exactly when another ends does not overlap it.
…（略：三個 User Story 共 17 個 Given/When/Then、9 條 Edge Cases）
- **FR-008**: When a request collides with more than one booking, the rejection MUST name the one with the earliest start.
- **FR-011**: A cancellation request MUST state the person, the room, and the booking's exact start and end times.
- **SC-005**: People get the result of a booking or cancellation request in under 1 second.
```

三件事值得記下。第一，84 個字的描述長成 1,773 字（`wc -w`，含開頭引用的原描述）、16 條 FR，其中 FR-008（撞多筆時報最早那筆）和 SC-005（1 秒內回應）是描述裡沒有的；這只是單次觀察，但和 [SDD Ch 40](../spec_driven_development/40-empirical-evidence.md) 的「二、Böckeler 的 scope inflation」描述的現象一致。第二，0 個 `[NEEDS CLARIFICATION]`：1.2.5 的 specify 要模型「Make informed guesses」，只在影響重大時標記、最多 3 個，所以「用房間與起訖時間、而不是 id 指定要取消哪筆」直接寫成了上面的 FR-011，Assumptions 裡一個字也沒提，而本課的 R4 用的是 `DELETE /bookings/{id}`。第三，`checklists/requirements.md` 的每一格都被模型自己打了 `[x]`，結尾寫著「Validated in one pass; all items passed.」：同一個模型寫、同一個模型驗，正是上一節的軟約束。

### skill_io_map.py：從 SKILL.md 抽出地圖

用動詞猜「讀」或「寫」不可靠（踩雷第 5 條有實例），所以這支腳本只抽確定的事實：誰可被模型叫用、怎麼對待 constitution、跑哪些腳本；再附一張「哪個 skill 提到哪份產物」的證據表，讀寫由人看證據判斷：

```python
"""skill_io_map.py: map which skills touch which Spec Kit artifacts, from their SKILL.md text.

Usage: python skill_io_map.py PROJECT [--skills GLOB] [--who ARTIFACT]
Table 1: may the model invoke it (auto), how it treats constitution.md, which .specify/scripts/bash/*.sh
it runs. Table 2: how many lines name each artifact; --who ARTIFACT prints them. A mention is evidence,
not a verdict. Problems (exit 1): missing or invalid frontmatter, a script or <name>-template it names
that does not exist."""
import argparse, re, sys
from pathlib import Path
import yaml

ARTIFACTS = {"spec": r"spec\.md|FEATURE_SPEC", "plan": r"plan\.md|IMPL_PLAN", "research": r"research\.md",
             "data-model": r"data-model\.md", "contracts": r"contracts/", "quickstart": r"quickstart\.md",
             "tasks": r"tasks\.md|\bTASKS\b", "checklists": r"checklists?/", "feature.json": r"feature\.json"}
SCRIPT = re.compile(r"\.specify/scripts/bash/([\w-]+)\.sh([^`\n]*)")
TEMPLATE = re.compile(r"\b([a-z]+-template)\b(?!\.sh)")

def constitution_mode(body):
    lines = [l for l in body.splitlines() if "memory/constitution.md" in l]
    if not lines:
        return "-"
    if any(re.search(r"\b(Write|overwrite)\b", l) for l in lines):
        return "writes"
    if "Constitution Authority" in body:  # analyze and converge: conflicts are CRITICAL
        return "authority"
    return "if-exists" if all("IF EXISTS" in l for l in lines) else "reads"

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--skills", default="speckit-*", help="skill directory glob (default: speckit-*)")
    ap.add_argument("--who", choices=ARTIFACTS, help="print the lines that name this artifact")
    args = ap.parse_args()
    files = sorted(args.project.glob(f".claude/skills/{args.skills}/SKILL.md"))
    problems, rows = [], []
    for f in files:
        name, text = f.parent.name, f.read_text(encoding="utf-8")
        if not text.startswith("---\n") or text.count("---\n") < 2:
            problems.append(f"{name}: no YAML frontmatter between --- lines")
            continue
        _, front, body = text.split("---\n", 2)
        try:
            meta = yaml.safe_load(front) or {}
        except yaml.YAMLError as e:
            problems.append(f"{name}: frontmatter is not valid YAML ({getattr(e, 'problem', e)})")
            continue
        if not isinstance(meta, dict): problems.append(f"{name}: frontmatter is not a YAML mapping"); continue
        offset = front.count("\n") + 2  # body line 1 is file line offset + 1
        found = SCRIPT.findall(body)
        scripts = [" ".join([s] + [x for x in flags.split() if x != "--json"]) for s, flags in found]
        for script in sorted({s for s, _ in found}):
            if not (args.project / f".specify/scripts/bash/{script}.sh").exists():
                problems.append(f"{name}: runs .specify/scripts/bash/{script}.sh, which does not exist")
        for template in sorted(set(TEMPLATE.findall(body))):
            if not (args.project / f".specify/templates/{template}.md").exists():
                problems.append(f"{name}: names {template}; .specify/templates/{template}.md does not exist")
        hits = {a: [(i + offset, l.strip()) for i, l in enumerate(body.splitlines(), 1) if re.search(p, l)]
                for a, p in ARTIFACTS.items()}
        auto = "no" if meta.get("disable-model-invocation") else "yes"
        rows.append((name, auto, constitution_mode(body), "; ".join(dict.fromkeys(scripts)) or "-",
                     [len(hits[a]) or "." for a in ARTIFACTS]))
        for i, line in hits[args.who] if args.who else []:
            print(f"{name}:{i}: {line[:110]}")
    if not args.who and rows:
        print(f"{'skill':22} {'auto':4}  {'constitution':12}  scripts (--json omitted)")
        for name, auto, mode, scripts, _ in rows:
            print(f"{name:22} {auto:4}  {mode:12}  {scripts}")
        print(f"\n{'lines naming':22}" + "".join(f"{a[:5]:>6}" for a in ARTIFACTS))
        for name, *_, counts in rows:
            print(f"{name:22}" + "".join(f"{c:>6}" for c in counts))
    for p in problems:
        print("PROBLEM", p)
    print(f"{len(files)} skills, {len(problems)} problems")
    return 1 if problems or not files else 0

if __name__ == "__main__":
    sys.exit(main())
```

```bash
cd /tmp/lab/aibdd/ch12-speckit && source /tmp/lab/aibdd/.venv/bin/activate
python tools/skill_io_map.py booking; echo "exit=$?"
```

```
skill                  auto  constitution  scripts (--json omitted)
speckit-analyze        yes   authority     check-prerequisites --require-spec --require-tasks --include-tasks
speckit-checklist      yes   if-exists     check-prerequisites --template checklist-template
speckit-clarify        yes   if-exists     check-prerequisites --paths-only
speckit-constitution   yes   writes        resolve-template constitution-template
speckit-converge       yes   authority     check-prerequisites --require-spec --require-tasks --include-tasks
speckit-implement      yes   if-exists     check-prerequisites --require-tasks --include-tasks
speckit-plan           yes   reads         setup-plan
speckit-specify        yes   if-exists     -
speckit-tasks          yes   if-exists     setup-tasks
speckit-taskstoissues  yes   if-exists     check-prerequisites --require-tasks --include-tasks
…（略：第二張表，每個 skill 提到 9 種產物各幾行）
10 skills, 0 problems
exit=0
```

constitution 欄回答了 SDD 課幾章說法不一的問題：1.2.5 裡**每個** skill 都提到它。只有 constitution 自己寫它；plan 無條件讀，拿來做 Constitution Check；analyze 與 converge 把它當最高權威，衝突一律算 CRITICAL；其餘六個是「IF EXISTS」才讀。Ch 17 會把這一欄當成參數層的設計依據。

失敗的情況也跑一次。在 `booking` 的副本 `edge/` 裡放兩個手寫 skill：`bdd-acceptance` 引用了還沒寫的腳本與模板；`house-rules` 在 frontmatter 上面多了一行標題：

```bash
cp -R booking edge && mkdir -p edge/.claude/skills/house-rules edge/.claude/skills/bdd-acceptance
printf '# House rules\n---\nname: house-rules\ndescription: Team conventions for specs.\n---\n\nUse Gherkin for acceptance scenarios.\n' > edge/.claude/skills/house-rules/SKILL.md
printf -- '---\nname: bdd-acceptance\ndescription: Rewrite acceptance scenarios in spec.md as Gherkin and lint them.\n---\n\n1. Copy the acceptance-template into FEATURE_SPEC.\n2. Run `.specify/scripts/bash/gherkin-lint.sh --json` and fix every finding.\n' > edge/.claude/skills/bdd-acceptance/SKILL.md
python tools/skill_io_map.py edge --skills '*' | grep -E "PROBLEM|skills,"; echo "exit=${pipestatus[1]}"
```

```
PROBLEM bdd-acceptance: runs .specify/scripts/bash/gherkin-lint.sh, which does not exist
PROBLEM bdd-acceptance: names acceptance-template; .specify/templates/acceptance-template.md does not exist
PROBLEM house-rules: no YAML frontmatter between --- lines
12 skills, 3 problems
exit=1
```

（`pipestatus` 是 zsh 的寫法，bash 用 `PIPESTATUS[0]`。）路徑打錯、一個 SKILL.md 都沒找到時，它印 `0 skills, 0 problems` 但 exit 1，免得 CI 把「什麼都沒檢查」當成通過。

讀過 `--who` 印出的證據行，人工整理成讀寫表（「IF EXISTS」才讀的 constitution 不再列，見上一張表）。真實的 specify 執行寫的正是表上那三個檔，一個不多：

| skill | 讀 | 寫 | 腳本關卡 |
|---|---|---|---|
| constitution | 現有 constitution、`constitution-template`（經 `resolve-template.sh` 解析） | 只寫 `constitution.md` | 模板解析失敗就停 |
| specify | `spec-template`、`init-options.json`（編號方式） | `spec.md`、`checklists/requirements.md`、`feature.json` | 無 |
| clarify | `spec.md`、`requirements.md` | 寫回 `spec.md` 的 `## Clarifications`，重驗 `requirements.md` | `--paths-only` 不檢查 |
| plan | `spec.md`、constitution | `plan.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md` | 只要求找得到 feature 目錄 |
| checklist | `spec.md`、`plan.md`、`tasks.md`（存在才讀） | `checklists/<主題>.md`，只增不刪 | 需要 `plan.md` |
| tasks | `plan.md`、`spec.md`；選讀 data-model、contracts、research、quickstart | `tasks.md` | `setup-tasks.sh` 要 `plan.md` 與 `spec.md` |
| analyze | spec、plan、tasks、constitution | 不寫檔，只出報告 | 需要 spec、plan、tasks |
| implement | `tasks.md`、`plan.md`、選讀其餘設計文件、`checklists/` | 程式碼；在 `tasks.md` 打 `[X]` | 需要 plan、tasks |
| converge | spec、plan、tasks、constitution、程式碼 | 只在 `tasks.md` 尾端加 `## Phase N: Convergence` | 需要 spec、plan、tasks |
| taskstoissues | `tasks.md` | GitHub issue（1.1.3 起棄用，改用 github extension） | 需要 plan、tasks |

## 五、從 0.11.10 到 1.2.5：哪些變了、哪些沒變

用 0.11.10 在另一個空目錄 init 一次（0.11.10 沒有 `--non-interactive`，所以把 stdin 接到 `/dev/null`），兩邊各存一份檔案清單：

```bash
mkdir -p /tmp/lab/aibdd/ch12-speckit/v0-11-10 && cd /tmp/lab/aibdd/ch12-speckit/v0-11-10 && git init -q
uvx --from git+https://github.com/github/spec-kit.git@v0.11.10 specify init --here --force --integration claude --script sh < /dev/null
find . -path ./.git -prune -o -type f -print | sort > ../tree-0.11.10.txt   # tree-1.2.5.txt 是第一節存的
cd .. && diff tree-0.11.10.txt tree-1.2.5.txt
```

```
1d0
< ./.claude/skills/speckit-agent-context-update/SKILL.md
12,19c11
< ./.specify/extensions.yml
< ./.specify/extensions/.registry
…（略：agent-context extension 的另外 6 個檔）
---
> ./.specify/.gitignore
23a16
> ./.specify/memory/.constitution-template.json
27a21
> ./.specify/scripts/bash/resolve-template.sh
37d30
< ./CLAUDE.md
```

把 `skill_io_map.py` 對兩版各跑一次再 `diff`，腳本關卡的變化也浮出來：analyze 與 converge 多了 `--require-spec`，checklist 改用 `--template checklist-template`，constitution 改走 `resolve-template`。這也提醒了一種漂移（drift）：把 1.2.5 的 `speckit-constitution/SKILL.md` 複製進 0.11.10 專案，模擬「只換了 skill」的半套升級，`skill_io_map.py mixed` 印出 `PROBLEM speckit-constitution: runs .specify/scripts/bash/resolve-template.sh, which does not exist`、exit 1；同一個專案的 `specify integration status` 只回報 `Modified managed files: 1`、exit 0。把變化和 [v1.2.5 的 CHANGELOG](https://raw.githubusercontent.com/github/spec-kit/v1.2.5/CHANGELOG.md) 對起來：

| 變化 | 從哪一版 | 對本課的影響 |
|---|---|---|
| Claude Code 改裝原生 skill | 0.4.5（2026-04-02） | 兩版都是 skill；SDD Ch 27 原本照文件畫的 `.claude/commands/` 已改正 |
| agent-context extension 改成選用 | 0.12.0（2026-06-29） | init 不再產生 `CLAUDE.md`；1.2.5 的 plan skill 也沒有了 0.11.10 那一步「改寫 CLAUDE.md 的 SPECKIT 區塊」 |
| constitution 不再改寫模板與指令檔 | 0.14.4（2026-07-29） | 它只寫 `constitution.md`，其他 skill 執行時去讀，模板只留指標 |
| 管理用 `.specify/.gitignore` | 0.16.1（2026-08-07） | `feature.json` 不進版控 |
| constitution 模板在執行時解析 | 0.16.2（2026-08-10） | 1.2.5 的 constitution skill 經 `resolve-template.sh`（0.11.10 沒有這支）取模板，preset 因此蓋得到它（Ch 17） |
| `specify artifact` | 1.0.7（2026-09-15） | 查得到一份模板最後由哪一層提供 |
| `--no-model-invocation` | 1.2.0（2026-10-13） | 核心 skill 可以只在你打斜線時才跑 |

沒變的反而是產物的形狀：`spec`、`tasks`、`constitution` 三份模板在兩版之間逐位元組相同，`plan-template.md` 只差一行註解，`checklist-template.md` 差 15 行。所以 SDD Ch 28「第六步」那份依技術分層、測試排最後的 tasks.md，和兩版的真實模板都對不上（SDD Ch 29 節錄的則是真實模板）。真實的 spec 是依優先度排的 User Story 加 Given/When/Then、FR-xxx、SC-xxx 與 Assumptions；真實的 tasks 依 User Story 分階段、MVP 先行，測試任務標著「OPTIONAL - only if tests requested」，Ch 16 會處理這一點。

## 六、本課的「4+2」讀法

外部課綱把這一段叫「Spec Kit 4+2 流程」，我們只讀得到標題，讀不到它怎麼定義。下面是**本課的讀法**：

```
(0) constitution    每個專案一次；之後各 skill 執行時去讀它
       │
(1) specify ──▶ [+1 clarify] ──▶ (2) plan ──▶ (3) tasks ──▶ [+2 analyze] ──▶ (4) implement
                 ≤5 題，一次一題                                唯讀報告
其他選用：checklist（plan 之後）、converge（implement 後補任務）、taskstoissues（已棄用）
```

**4** 是 2025-09 發表時的四步：specify、plan、tasks、implement（[SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) 的「版本演進：命令名稱的歷史脈絡」）；1.2.5 內建的 `workflow.yml` 也只串這四個 command，註解稱它們是「core spec-kit commands provided by every integration」。**+2** 是 init 面板放在核心步驟**之間**的兩個選用 skill：clarify（「run before /speckit-plan if used」）與 analyze（「after /speckit-tasks, before /speckit-implement」）。還有一個巧合：`specify workflow info speckit` 印出 `Steps (6)`，四個 command 加兩道 `gate`，也湊得出「4+2」。哪種接近原課，我們無從確認；本課之後的章節都用第一種。

用 1.2.5 的實際行為快速複習四個單元，概念回 [SDD Ch 28](../spec_driven_development/28-spec-kit-workflow.md) 對應的「第 N 步」：

- **constitution 與 specify（3-4）**：constitution 只寫 `.specify/memory/constitution.md`，檔頭留一段給人 review 的 Sync Impact Report 註解（skill 註明 commit 前該刪掉）；specify 不建分支，而是建 `specs/NNN-短名/`、寫 `feature.json`，最多 3 個 `[NEEDS CLARIFICATION]`，其餘的不確定寫進 Assumptions。
- **clarify（3-5）**：整個 session 最多 5 題、一次只問一題、每題附「**Recommended:**」選項，答案寫回 spec 的 `## Clarifications` 底下的 `### Session YYYY-MM-DD`。Ch 18 會做一個自己的版本。
- **tasks 與 implement（3-8）**：tasks 依 User Story 分階段，格式 `- [ ] T001 [P] [US1] …`；implement 開工前要過 `check-prerequisites`，做完一項就在 `tasks.md` 打 `[X]`。
- **流程順序（3-3）**：上圖。analyze 一定在 tasks 之後，它的腳本關卡 `--require-tasks` 會擋。

## 對比與取捨

| 做法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 在 session 裡逐步打 `/speckit-*` | 每一步都能看、能改、能停 | 順序靠人記；skill 也可能被模型自己叫起來 | 學習、單人、探索期 |
| `specify workflow run speckit` | 順序與兩道人工閘門寫死在 YAML，每步一個 `claude -p` | 只看 exit code 不看產物；預設不加回合與工具限制（見進階） | 想要可重播、可暫停的流程 |
| 直接改 `.claude/skills/speckit-*/SKILL.md` | 立刻生效 | 升級被擋，`--force` 後改動消失 | 一次性實驗 |
| 專案 override、preset、自己的 skill（Ch 15–18） | 活得過升級，可以 code review | 多一層要維護 | 團隊長期使用 |

## 踩雷集錦

1. **錯誤直覺：「Spec Kit 在 Claude Code 裡是 `.claude/commands/` 底下的 `/speckit.plan`。」** 正確認識：0.4.5 起就是 `.claude/skills/speckit-plan/SKILL.md`，打 `/speckit-plan`；點號是文件、`workflow.yml` 與 Gemini 這類指令檔 agent 的寫法。相關的坑：在 CI 或 agent harness 裡跑 `specify init --here --force --non-interactive` 卻沒給 `--integration`，實跑印 `Selected coding agent integration: copilot`，10 個 skill 全寫進 `.github/skills/`，Claude Code 一個也看不到。
2. **錯誤直覺：「切到哪個 git 分支，Spec Kit 就在做哪個 feature。」** 正確認識：決定權在 `.specify/feature.json`。第二節那次自動叫用把它改指向 002，而它被 `.gitignore` 排除，`git status` 完全不顯示；接著你打 `/speckit-plan`，規劃的就是 002。換 feature 前先 `cat .specify/feature.json`，或用 `SPECIFY_FEATURE_DIRECTORY` 明確指定。
3. **錯誤直覺：「在安裝好的 SKILL.md 加一段公司規則最快。」** 正確認識：封條會記下這個改動，下次 `integration upgrade` 直接 exit 1；加 `--force` 才過得去，規則也同時被蓋掉。想活過升級，走 Ch 17 的 override 或 preset，或寫自己的 wrapper skill。
4. **錯誤直覺：「skill 和以前的 command 一樣，我不打就不會跑。」** 正確認識：預設 `disable-model-invocation: false`，一句沒提 Spec Kit 的請求就讓模型自己叫了 `speckit-specify`，還建了新的 feature 目錄。不想要就在 init 加 `--integration-options="--no-model-invocation"`，清單從 1,339 B 變 0 B；官方文件提醒，依賴核心指令的 extension 強制 hook 會因此失效。
5. **錯誤直覺：「grep 到檔名，就知道誰讀誰寫。」** 正確認識：我們先寫過一版用動詞判斷讀寫的原型，它把 implement 標成「寫 plan.md」，因為那一行是 `Check if Dockerfile* exists or Docker in plan.md → create/verify .dockerignore`。`skill_io_map.py booking --who plan` 也會印出 `speckit-converge:81: - modify` 開頭、談 `spec.md` 與 `plan.md` 的一行，它其實在「It MUST NOT:」底下。提及只是證據，所以腳本只印證據，讀寫由人判斷。

## 進階：用 workflow 引擎和一個假的 claude 看清楚派工

`workflow.yml` 的每一步寫著 `command: speckit.specify`，但它到底怎麼叫 Claude Code？比讀原始碼更直接的辦法是換一個假的執行檔。官方 Integration CLI 文件說 `SPECKIT_INTEGRATION_<KEY>_EXECUTABLE` 可以換掉 agent CLI，`_EXTRA_ARGS` 可以附加參數。存成 `/tmp/lab/aibdd/ch12-speckit/bin/fake-claude` 並 `chmod +x`：

```sh
#!/bin/sh
# fake-claude: record how Spec Kit calls the agent CLI, without calling a model
log=${FAKE_CLAUDE_LOG:-/tmp/lab/aibdd/ch12-speckit/fake-claude.log}
{ printf 'argv:'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'; } >> "$log"
echo "fake-claude: got $# args, prompt was: $2"
```

```bash
cd /tmp/lab/aibdd/ch12-speckit && git clone -q booking wf && cd wf
export SPECKIT_INTEGRATION_CLAUDE_EXECUTABLE=/tmp/lab/aibdd/ch12-speckit/bin/fake-claude
export SPECKIT_INTEGRATION_CLAUDE_EXTRA_ARGS="--max-turns 20 --allowedTools Read,Write"
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify workflow run speckit \
  -i spec="Room booking: book a meeting room for a time slot" < /dev/null
cat ../fake-claude.log; ls specs .specify/feature.json
```

```
Running workflow: Full SDD Cycle (speckit)
Version: 1.0.1
  ▸ [specify] speckit.specify …
fake-claude: got 6 args, prompt was: /speckit-specify Room booking: book a meeting room for a time slot
  ▸ [review-spec] gate …
Status: paused
Run ID: 6eb600c9
Resume with: specify workflow resume 6eb600c9
argv: [-p] [/speckit-specify Room booking: book a meeting room for a time slot] [--max-turns] [20] [--allowedTools] [Read,Write]
ls: .specify/feature.json: No such file or directory
ls: specs: No such file or directory
```

三件事。第一，派工就是 `claude -p "/speckit-specify <spec>"`，加上你給的額外參數；不設 `_EXTRA_ARGS`，Spec Kit 不替你加回合上限或工具限制（也不加跳過權限的旗標），能做什麼全看 Claude Code 自己的權限設定，交給真的 claude 之前先想清楚。第二，假的 claude 什麼都沒寫，engine 照樣把 specify 記成 completed，`.specify/workflows/runs/6eb600c9/state.json` 裡 exit_code 是 0：command 步驟只看 exit code，不看產物有沒有生出來。第三，`gate` 在沒有 TTY 時回傳 paused，`specify workflow resume 6eb600c9` 在同樣情況下又停在同一道閘門，假 claude 的紀錄仍只有一行。依原始碼，有 TTY 時它會讓你選 approve 或 reject；這一段本章未實測，請在自己的終端機跑一次確認。這個實驗回答了對比表的那一格：workflow 給你「順序」與「人工閘門」兩種硬保證，產物合不合格它不管，還是得靠 Ch 13 起的檢查腳本。

## 動手練習

1. 對 `nomodel/` 跑 `skill_io_map.py`，確認 auto 欄全是 `no`；再用第二節那句不帶斜線的請求跑一次 `claude -p`（帶上 `--max-turns` 與 `--allowedTools`），看時間線裡還有沒有 `Skill` 呼叫。
2. 替 `edge/` 的 `bdd-acceptance` 補一支只會 `exit 0` 的 `gherkin-lint.sh` 與一份 `acceptance-template.md`，讓 `--skills 'bdd-*'` 回到 `0 problems`。再想一想：這支自己加的腳本在 `integration upgrade --force` 之後還在嗎？改過的 `common.sh` 呢？跑一次對答案。
3. 刪掉 `.specify/scripts/bash/setup-tasks.sh`。先寫下你預期 `integration status` 的狀態與 exit code，再跑一次對答案，最後用 `integration upgrade claude` 修回。
4. 用 `create-new-feature.sh --json --dry-run` 預測下一個編號；再讀 `speckit-specify/SKILL.md` 的第 3 步，說出把 `init-options.json` 的 `feature_numbering` 改成 `timestamp` 後，目錄名會長什麼樣。

## 本章重點整理

- Spec Kit 1.2.5 裝進 Claude Code 的是 10 個 `.claude/skills/speckit-*/SKILL.md`，打 `/speckit-plan` 叫用，全由同一套 `templates/commands/*.md` 轉出。轉成 skill 在 0.4.5 就發生了，SDD 課原本照舊文件寫的 `.claude/commands/` 已依實跑改正。
- 「目前的 feature」由 `.specify/feature.json` 決定，不是 git 分支；它被 `.gitignore` 排除，模型自動叫用 specify 時會默默改掉它。
- 關卡分兩種：腳本的 exit code 是硬閘門；SKILL.md 裡的「最多 3 個」「唯讀」「ERROR on gate failures」是寫給模型的文字，spec 品質清單也是模型自己打勾。
- manifest 用 SHA-256 封住每個受管檔（skill、共用腳本與模板）：改過的 skill 擋住升級、`--force` 會蓋掉；改過的模板在一般升級中保留。`skill_io_map.py` 補上封條看不到的懸空引用。
- 0.11.10 到 1.2.5，產物模板幾乎沒變，變的是基礎設施：agent context 選用、constitution 只寫自己、執行時解析模板、`artifact` 與 `--no-model-invocation`。

## 自我檢核

- [ ] 不看本章，說出 `/speckit-tasks` 讀哪兩份必要文件、寫哪一份，以及擋住它的是哪支腳本、哪個條件。
- [ ] 同事說「我切到 `002-cancel` 分支了，可是 `/speckit-plan` 改的是 001」，你要他先看哪個檔案？為什麼 `git status` 幫不上忙？
- [ ] 從本章的關卡表各舉一個硬閘門和一個軟約束，再說出 Part 3 打算用什麼把其中一個軟約束變硬。
- [ ] 改 `speckit-specify/SKILL.md` 與改 `spec-template.md`，在不加 `--force` 的 `integration upgrade` 下結果有什麼不同？各自該怎麼補救？
- [ ] 用一句話說明本課的「4+2」各指哪幾步，以及為什麼我們不能說那就是外部課程的定義。

## 延伸閱讀

- **[Spec Kit — Claude Code integration](https://github.github.io/spec-kit/integrations/claude.html)**（GitHub 官方文件）
  - **讀哪裡**：「Installed Skills」「User-Invocable-Only Skills」「Runtime Events and Workflow Dispatch」
  - **學到什麼**：skill 的安裝位置、`--no-model-invocation` 的完整限制，以及事件 hook 走 `.claude/settings.json`、可用 `--events false` 關掉
  - **和本章的關聯**：第二節與踩雷第 4 條的官方依據；先看過本章第二節的 frontmatter 再讀
- **[Spec Kit — Upgrade Guide](https://github.github.io/spec-kit/upgrade.html)**
  - **讀哪裡**：「Part 2: Updating Project Files」與「Behavior change: "/constitution" no longer propagates into templates」
  - **學到什麼**：manifest 感知的升級流程、哪些檔案升級永遠不碰（`specs/`、constitution），以及 constitution 改成執行時讀取的理由
  - **和本章的關聯**：底層機制的「封條」與第五節變更表的第三列
- **[Spec Kit — Integration CLI Reference](https://github.github.io/spec-kit/integrations/cli.html)**
  - **讀哪裡**：「Command Invocation」「Report Integration Status」「Runtime Events」「Workflow CLI Dispatch」
  - **學到什麼**：`/speckit-specify`、`/speckit.specify`、`$speckit-specify`、`/skill:speckit-specify` 四種拼法各屬哪些 agent；`status` 何時 exit 1；沒設定 handler 就不寫事件 hook；`SPECKIT_INTEGRATION_<KEY>_EXECUTABLE` 與 `_EXTRA_ARGS`
  - **和本章的關聯**：進階那個假 claude 的實驗就是照這一節做的
- **[Claude Code — Extend Claude with skills](https://code.claude.com/docs/en/skills)**（Anthropic 官方文件）
  - **讀哪裡**：「Frontmatter reference」表格，特別是 `disable-model-invocation`、`user-invocable`、`argument-hint`
  - **學到什麼**：Spec Kit 寫進 frontmatter 的每個欄位在 Claude Code 裡的確切語意，以及描述在清單裡被截斷的長度上限
  - **和本章的關聯**：讀懂 Spec Kit 產生的 skill，靠的是 Claude Code 這一側的規則；Ch 4 有實測

現況圖畫好了。下一章把放大鏡對準最重的那一步：`/speckit-plan` 實際讀了什麼、`plan.md` 與 `research.md` 長什麼樣，以及怎麼用一份清單和一支 lint 去 review 它。

→ [Ch 13 解剖 /plan：plan.md、research.md 與怎麼 review](./13-plan-anatomy-review.md)
