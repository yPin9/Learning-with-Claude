# Ch 27 — GitHub Spec Kit（一）：安裝與 bootstrap

> **目標**：把 Spec Kit 的 CLI 裝起來、用 `specify init` 把一個專案 bootstrap 出來、讀懂 `.specify/` scaffold 長什麼樣，並且理解每個產物為什麼存在。
>
> **環境**：uv ≥ 0.4 / Python 3.11+ / Git；Spec Kit v0.11.10（latest，查證日期 2026-06-30）。命令名稱和 flag 在此版本之後仍會異動，建議安裝後先跑 `specify --version` 確認。

---

## 為什麼需要一個 CLI 而不是一個 Markdown 範本？

在 Spec Kit 出現之前，「先寫規格再讓 AI 實作」這件事確實有人在做，但做法分散：有人維護一個 Notion 頁、有人在 README 開個 `## Design` 區塊、有人用 Issues 手動填 acceptance criteria。問題不在格式，而在**整合的缺口**：

- 規格和 AI agent 工作目錄沒有連結，agent 看不到規格。
- 功能編號靠人工，一旦並行開發就衝突。
- 不同 AI agent（Copilot、Claude Code、Cursor）各有各的 slash command 路徑，沒有統一的安裝點。
- 缺少 phase gate（沒計劃不能實作），全靠工程師自律。

GitHub 在 2025-09-02 發布 Spec Kit，由 Principal Product Manager Den Delimarsky 主導，並在 README 說明：「This project is heavily influenced by and based on the work and research of John Lam」（https://github.com/jflam）。核心主張用 Delimarsky 的話說：

> "We treat coding agents like search engines when we should be treating them more like literal-minded pair programmers."

這句話點出了問題所在：搜尋引擎猜意圖，配對編程夥伴要的是無歧義的合約。Spec Kit 就是把這個「合約產線」打包成一個可安裝的 CLI。

---

## 心智圖像：整條 scaffold 從哪裡來

```
磁碟初始狀態（空資料夾）
    │
    ▼  specify init my-app --integration claude
    │
    ├─ .specify/                  ← 規格機器的心臟
    │   ├─ memory/
    │   │   └─ constitution.md   ← 專案憲法（/speckit.constitution 生成）
    │   ├─ templates/
    │   │   ├─ spec-template.md  ← 規格範本（強制 WHAT/WHY，無 HOW）
    │   │   ├─ plan-template.md  ← 計劃範本
    │   │   ├─ tasks-template.md ← 任務範本（含 [P] 標記）
    │   │   └─ CLAUDE-template.md
    │   └─ scripts/
    │       ├─ bash/             ← Linux/macOS 用
    │       │   ├─ check-prerequisites.sh
    │       │   ├─ common.sh
    │       │   ├─ create-new-feature.sh
    │       │   ├─ setup-plan.sh
    │       │   └─ setup-tasks.sh
    │       └─ (PowerShell equivalents for Windows)
    │
    └─ .claude/                   ← Claude Code 的 slash command prompt files
        └─ commands/
            ├─ speckit.constitution.md
            ├─ speckit.specify.md
            ├─ speckit.plan.md
            ├─ speckit.tasks.md
            ├─ speckit.implement.md
            └─ ... (其餘 commands)
```

之後每呼叫一次 `/speckit.specify`，`create-new-feature.sh` 會掃描現有 `specs/` 資料夾自動編號，在 Git 建立語義分支，並從 `spec-template.md` 複製出一個空白規格：

```
specs/
└─ 001-user-login/
    ├─ spec.md          ← /speckit.specify 填寫
    ├─ plan.md          ← /speckit.plan 填寫
    ├─ tasks.md         ← /speckit.tasks 填寫
    ├─ research.md      ← /speckit.plan 選擇性產出
    ├─ data-model.md    ← /speckit.plan 選擇性產出
    └─ contracts/
        └─ api-spec.json
```

---

## 安裝

### 前置需求

| 工具 | 最低版本 | 說明 |
|------|---------|------|
| Python | 3.11+ | uv 會自動解析，不需手動切換 |
| Git | 任何現代版本 | scaffold 腳本需要 git 指令 |
| uv | ≥ 0.4（建議）| 替代方案：pipx |

**uv** 是 Astral 出品的 Python 套件管理工具，文件在 https://docs.astral.sh/uv/。如果你還沒裝：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 安裝 `specify` CLI

Spec Kit 發布在 GitHub，不在 PyPI，所以要從 git tag 安裝。把 `vX.Y.Z` 換成你想釘死的版本（目前最新是 v0.11.10，查證日期 2026-06-30）：

```bash
uv tool install specify-cli \
  --from git+https://github.com/github/spec-kit.git@v0.11.10
```

安裝成功後確認：

```bash
specify --version
# 輸出範例：specify-cli 0.11.10
```

> 注意：版本號與 git tag 對應；不釘死 tag 改用 `@main` 的話，每次 `uv tool install` 都可能拉到不同行為。這個 repo 的 release 節奏非常快（v0.11.10 在 2026-06-29 發布，同年八月該 repo 才創立一週年），建議釘版本並訂閱 release feed。

如果你只想**試用一次**而不永久安裝：

```bash
uvx --from git+https://github.com/github/spec-kit.git@v0.11.10 specify --version
```

`uvx` 會建立一個臨時環境執行後丟棄。Spec Kit 的 self-upgrade 邏輯能偵測到這種 `uvx` 模式。

---

## `specify init`：把專案 bootstrap 起來

### 基本用法

在一個已經有 `git init` 的資料夾內執行：

```bash
mkdir taskify && cd taskify && git init
specify init taskify --integration claude
```

`--integration` 指定要支援的 AI agent。常用值：

| integration key | 對應 agent | command files 寫到哪 |
|----------------|-----------|-------------------|
| `claude` | Claude Code | `.claude/commands/` |
| `copilot` | GitHub Copilot | `.github/prompts/` |
| `gemini` | Gemini CLI | `.gemini/commands/` |
| `cursor-agent` | Cursor | `.cursor/` |
| `codex` | OpenAI Codex CLI | `.codex/` |

完整清單用 `specify integration list` 查，README 說「30+」，文件頁 https://github.github.io/spec-kit/reference/integrations.html 列出了 Claude Code、Gemini CLI、Cursor CLI、Qwen CLI、opencode、Codex CLI、Kiro CLI、Tabnine CLI 等超過 47 個 agent（查證日期 2026-06-30；README 聲稱「30+」但文件頁實際列出更多，數字 version-dependent）。

### 進階旗標

| flag | 說明 |
|------|------|
| `--integration-options="--skills"` | 安裝 agent skills 而非 slash-command prompt files（適合 Claude Code、Codex 的 skills 模式） |
| `--here` / `specify init .` | 在現有目錄原地初始化，不建子目錄 |
| `--force` | 覆寫已有的 `.specify/` |
| `--ignore-agent-tools` | 只建 `.specify/` scaffold，不寫 agent command files |
| `--script sh\|ps` | 選腳本語言：`sh`（預設，bash/Linux/macOS）或 `ps`（PowerShell/Windows）|

Windows 使用者範例：

```powershell
specify init . --integration claude --script ps
```

這會把 `.specify/scripts/` 下的腳本換成 `.ps1` 版本。

> 版本注意：`--integration` 在非互動式環境（CI 等）預設值是 `copilot`（確認自 `src/specify_cli/commands/init.py`，`DEFAULT_INIT_INTEGRATION = "copilot"`，查證日期 2026-06-30）。如果你在 GitHub Actions 裡跑 `specify init`，記得明確傳 `--integration` 或結果可能出乎意料。

### 實際輸出

執行成功後會看到類似：

```
✓ Created .specify/memory/constitution.md
✓ Created .specify/templates/spec-template.md
✓ Created .specify/templates/plan-template.md
✓ Created .specify/templates/tasks-template.md
✓ Created .specify/scripts/bash/create-new-feature.sh
✓ Created .specify/scripts/bash/check-prerequisites.sh
✓ Created .specify/scripts/bash/common.sh
✓ Created .specify/scripts/bash/setup-plan.sh
✓ Created .specify/scripts/bash/setup-tasks.sh
✓ Created .claude/commands/speckit.constitution.md
✓ Created .claude/commands/speckit.specify.md
... (依 integration 不同而異)

Run /speckit.constitution in your AI coding agent to set up your project's
governing principles.
```

---

## `.specify/` scaffold 解剖

### 1. `memory/constitution.md`

這個檔案在你第一次跑 `/speckit.constitution` 之前是空的，或者有 placeholder。它是**整個專案的技術憲法**，存放不會每個 feature 都改變的原則：程式碼品質標準、測試策略、架構邊界、UX 一致性規則。

每次 `/speckit.plan` 執行時，agent 都會讀 constitution.md 並和計劃草稿做一致性檢查。這是讓憲法真的有用的關鍵：它不是給人讀的文件，是給 LLM 讀的約束輸入。

### 2. `templates/spec-template.md`

這是本章最值得細看的檔案。spec-kit 的 `spec-driven.md`（https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md）描述範本的核心功能：

> Templates constrain the LLM's output in productive ways, enforce 'Focus on WHAT users need and WHY' and use '[NEEDS CLARIFICATION: ...]' markers.

換句話說，範本是**主動的 LLM 約束（active LLM constraint）**，不是被動的格式建議。`spec-template.md` 裡的 section heading 和 placeholder 直接出現在 agent 的 context，迫使它填充正確的資訊類型。

`[NEEDS CLARIFICATION: ...]` 是特殊標記：當 agent 無法確定某個需求細節時，它寫這個 token，而不是猜。之後 `/speckit.clarify` 可以掃描這些 token 並做 structured Q&A。

### 3. `templates/plan-template.md` 與 `tasks-template.md`

`plan-template.md` 的結構引導 agent 輸出：技術選型理由、架構決策、`research.md`（外部資料研究）、`data-model.md`（資料模型）、`contracts/`（API 合約，如 `api-spec.json`）。

`tasks-template.md` 強制每個 task 有前置條件宣告。`[P]` 是特殊標記，表示這個 task 和同層其他 `[P]` tasks 可以**並行執行**——不是自動並行，是給人工或 orchestration 工具的提示。

### 4. `scripts/`

這些 shell 腳本是 slash command 的「底層 plumbing（管線）」：

| 腳本 | 作用 |
|------|------|
| `create-new-feature.sh` | 掃描 `specs/` 自動計算下一個功能編號，建立語義化 Git branch，複製 spec-template |
| `check-prerequisites.sh` | 驗證必要的產物存在才允許下一個 phase 執行（phase gate） |
| `setup-plan.sh` | 為 plan phase 建立目錄結構 |
| `setup-tasks.sh` | 為 tasks phase 建立目錄結構 |
| `common.sh` | 共用函式（路徑解析、錯誤處理等） |

> 澄清（來自 corrections.md）：`check-prerequisites.sh` 是 scaffold 裡真實存在的腳本，但 `spec-driven.md` 並未把它描述成一個有文件化的「artifact-existence phase gate 機制」。它的實際行為以實際腳本內容為準，不要過度引申。

### 5. agent command files（以 Claude Code 為例）

`specify init --integration claude` 會把 prompt files 寫到 `.claude/commands/`。每個 `/speckit.*` 命令對應一個 `.md` 文件：

```
.claude/commands/
├─ speckit.constitution.md
├─ speckit.specify.md
├─ speckit.clarify.md
├─ speckit.plan.md
├─ speckit.checklist.md
├─ speckit.tasks.md
├─ speckit.analyze.md
├─ speckit.implement.md
├─ speckit.taskstoissues.md
└─ speckit.converge.md
```

這些 `.md` 文件的內容就是「prompt」—— 當你在 Claude Code 輸入 `/speckit.specify`，Claude Code 把那個 `.md` 的內容連同你的輸入一起送入對話。

**skills 模式**的差異：`--integration-options="--skills"` 時，files 會改寫到 skills 目錄（如 `.claude/skills/`），Codex CLI 則用 `$speckit-<command>` 語法。以實際安裝結果為準，因為 per-agent 路徑 version-dependent。

---

## 版本演進：命令名稱的歷史脈絡

理解這個歷史能幫你看懂舊教學和 Stack Overflow 討論：

| 時期 | 安裝法 | 核心命令集 |
|------|--------|-----------|
| 2025-09-02 發布 | 從 git 安裝（同現在），無版本 tag | `/specify`, `/plan`, `/tasks`, 手動 implement |
| 2025 後期 | 釘 tag 安裝 | 加入 `/constitution`；命令開始加 `/speckit.` prefix |
| v0.11.10（2026-06-29）| `@v0.11.10` tag | `/speckit.constitution`, `/speckit.specify`, `/speckit.clarify`, `/speckit.plan`, `/speckit.checklist`, `/speckit.tasks`, `/speckit.analyze`, `/speckit.implement`, `/speckit.taskstoissues`, `/speckit.converge` |

這個演進有一個具體例子：`/speckit.clarify` 的前身叫 `/quizme`。如果你找到一篇教 `/quizme` 的文章，那是舊版行為。

---

## 升級與自我維護

```bash
# 確認目前版本
specify self check

# 升級到最新（不指定 tag 則抓最新 release）
specify self upgrade

# 先看會改什麼，不真的動
specify self upgrade --dry-run

# 升到指定版本
specify self upgrade --tag v0.11.10
```

Spec Kit 有**extensions / presets / bundles** 擴充機制：
- **extension**：新增新的 commands 和 templates（`specify extension add <source>`）
- **preset**：覆寫既有 templates/commands（`specify preset add <source>`）
- **bundle**：打包成按角色的整套設定（`specify bundle install <source>`）

Template 解析優先順序（高到低）：project-local 覆寫 > presets > extensions > core。

---

## 踩雷集錦

### 1. 不釘 tag，升級後 slash command 消失

**錯誤直覺**：`@main` 永遠是最新最好的。  
**正確認識**：Spec Kit 在不到一年內發布了 175+ releases，命令名稱改過（`/quizme` → `/speckit.clarify`），舊的 agent command files 不會自動更新。釘死 tag，升級前先看 changelog（https://github.com/github/spec-kit/releases），升完重跑 `specify init --force` 更新 command files。

### 2. 在 CI 裡跑 `specify init` 沒傳 `--integration`

**錯誤直覺**：非互動式環境 init 失敗，應該就不執行。  
**正確認識**：非互動式環境下 `specify init` 的 `--integration` 預設值是 `copilot`（來自 `_agent_config.py` 的 `DEFAULT_INIT_INTEGRATION`）。你可能以為沒有設定，但其實裝了一堆 Copilot 的 prompt files。若是要裝 Claude Code 整合，一定要明確傳 `--integration claude`。

### 3. 跳過 `/speckit.constitution` 直接 `/speckit.specify`

**錯誤直覺**：憲法只是一個 template，之後再補也不影響功能。  
**正確認識**：`/speckit.plan` 在產出計劃時會參照 `constitution.md`。如果檔案是空的 placeholder，agent 的計劃就沒有約束；如果根本不存在，可能導致 check-prerequisites 拒絕執行 plan phase。先跑 `/speckit.constitution` 建立至少基本的原則再進行下一步。

### 4. Windows 環境不傳 `--script ps`

**錯誤直覺**：腳本應該都相容，或者 Git Bash 就夠了。  
**正確認識**：預設是 `sh` 腳本。在純 PowerShell 環境下 `.sh` 腳本無法直接執行，會在 `create-new-feature.sh` 等地方失敗。如果你的團隊全用 PowerShell，初始化時傳 `--script ps`；若混用，考量清楚再選。

### 5. 以為 `specify init` 會替你 `git init`

**錯誤直覺**：`specify init` 是「全包」的初始化。  
**正確認識**：`specify init` 假設 Git repository 已存在。在沒有 `.git/` 的資料夾跑 `specify init`，`create-new-feature.sh`（需要 `git checkout -b`）之後會失敗。先 `git init` 再 `specify init`。

---

## 進階延伸

### Skills 模式 vs Slash-Command 模式

這兩者的差異在交付機制：
- **slash-command 模式**（預設）：每個命令是一個 `.md` prompt file，agent 把它的內容注入對話 context。
- **skills 模式**（`--integration-options="--skills"`）：agent 把命令視為可呼叫的「技能」，有些 agent（Claude Code、Codex）在 skills 模式下有更乾淨的命名空間（Codex 用 `$speckit-<command>` 而非 `/speckit.*`）。

哪個好？以 Claude Code 而言，slash-command 模式更普遍且文件更齊，skills 模式適合需要在 agent 內部做 command discovery 的場景。

### 多 agent 環境

一個 repo 可以同時 init 多個 integration：

```bash
specify init . --integration claude
specify init . --integration copilot --ignore-agent-tools  # 只加 copilot prompts，不覆寫 .specify/
```

不過 `--ignore-agent-tools` 的確切行為以當下版本的 `init.py` 為準。

### 訂閱 release 自動通知

因為 Spec Kit 迭代極快，建議在 GitHub 把 spec-kit repo 的 Releases 通知打開（Watch → Custom → Releases），或訂閱 https://github.com/github/spec-kit/releases.atom。

---

## 動手練習

**目標**：完整跑一次 bootstrap，確認 scaffold 產物符合預期。

1. 建立一個新資料夾 `sdd-hello`，在裡面跑 `git init`。
2. 安裝 Spec Kit CLI（釘 v0.11.10 或當下最新 tag）。
3. 跑 `specify init sdd-hello --integration claude`（如果是 Windows 加 `--script ps`）。
4. 列出 `.specify/` 資料夾樹狀結構（`find .specify -type f`，Windows 用 `Get-ChildItem .specify -Recurse`），對照本章的 scaffold 圖，確認每個 section 都在。
5. 打開 `spec-template.md`，找出 `[NEEDS CLARIFICATION: ...]` 的用法和任何 `[P]` 標記，用自己的話解釋這兩個標記要解決什麼問題。
6. 用 `git status` 看 init 寫了哪些檔案，選擇要不要把 `.specify/` 加進版控（提示：版控 template 有意義，版控 constitution.md 更有意義，但 `.claude/commands/` 要不要版控取決於團隊是否共用 agent 設定）。

---

## 本章重點整理

- Spec Kit 是一個 Python CLI（`specify`），透過 `uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@<tag>` 安裝，需要 Python 3.11+、Git。
- `specify init <project> --integration <agent>` 產生兩類產物：`.specify/`（templates + scripts + memory）和 agent-specific command/skill files（如 `.claude/commands/`）。
- `.specify/templates/` 裡的 `.md` 範本是**主動的 LLM 約束**，不只是格式指引；`[NEEDS CLARIFICATION]` 讓 agent 標記不確定點，`[P]` 標記可並行任務。
- 命令集從 2025 啟動時的裸命令（`/specify`, `/plan`）演進成目前的 `/speckit.*` namespace，版本之間有差異，釘 tag 是最安全的做法。
- 升級用 `specify self upgrade [--dry-run] [--tag vX.Y.Z]`，升後要重跑 `specify init --force` 更新 agent command files。
- Windows 要傳 `--script ps`；CI 要明確傳 `--integration`（預設是 `copilot`）。

---

## 自我檢核

- [ ] 不看筆記，說出用 `uv` 安裝 `specify-cli` 的完整命令（含 git tag 參數）。
- [ ] 用自己的話解釋 `spec-template.md` 的 `[NEEDS CLARIFICATION: ...]` 標記和 `[P]` 標記各自解決什麼問題。
- [ ] 如果有人問你「`/speckit.constitution` 和 `constitution.md` 的關係是什麼」，你怎麼回答？
- [ ] 說出 `--integration copilot` 和 `--integration claude` 在磁碟上最明顯的差異（command files 寫到哪）。
- [ ] 說出為什麼在 CI 環境跑 `specify init` 不傳 `--integration` 是個陷阱。
- [ ] 如果你的同事說「我找到一篇教 `/quizme` 的教學」，你怎麼解釋這個命令現在在哪裡？

---

## 延伸閱讀

1. **github/spec-kit 官方 README** — https://github.com/github/spec-kit  
   從「Get Started」、「Available Slash Commands」兩個 section 入手，再看「Detailed Process」裡的 Taskify 範例；那個範例展示了完整的目錄樹和每個 artifact 的實際內容。和本章的關聯：把本章的 scaffold 圖和真實 README 對照，確認理解無誤。

2. **spec-driven.md（in-repo 方法論文件）** — https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md  
   解釋 templates 如何作為 LLM 約束、`create-new-feature.sh` 如何自動編號、constitution 的結構意義。本章談了 scaffold 的「是什麼」，這份文件談「為什麼這樣設計」。

3. **GitHub Blog 發布公告** — https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/  
   Den Delimarsky 寫的原始動機文（2025-09-02）。呈現了 Spec Kit 試圖解決的問題（vibe coding 的失控），以及原始的四階段工作流（現在已演進）。對比舊命令和本章介紹的新命令，能幫助你理解設計的演進邏輯。

4. **specify init source — commands/init.py** — https://raw.githubusercontent.com/github/spec-kit/main/src/specify_cli/commands/init.py  
   直接看 CLI source code 確認 flag 的確切定義和預設值（`DEFAULT_INIT_INTEGRATION`、`SCRIPT_TYPE_CHOICES`、deprecated flags 等）。本章多個「踩雷」都源自於和這個 source 文件仔細核對。

5. **Supported AI Coding Agent Integrations** — https://github.github.io/spec-kit/reference/integrations.html  
   完整的 per-agent table，包括 integration key、command files 安裝路徑、skills 模式支援情況。本章給的 table 是快速參考，這個頁面是完整清單。

6. **Releases feed** — https://github.com/github/spec-kit/releases  
   了解 Spec Kit 迭代速度（v0.11.10 於 2026-06-29 發布，repo 於 2025-08-21 建立，約十個月超過 175 releases）。訂閱這個 feed 是讓本章學到的知識不快速過期的最低成本辦法。

下一章進入實際工作流：從 `/speckit.specify` 到 `/speckit.converge`，跑完一個完整的端到端 feature cycle，看清楚每個命令的輸入輸出和 phase gate 如何連接。

→ [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)
