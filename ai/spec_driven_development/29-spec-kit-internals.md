# Ch 29 — GitHub Spec Kit（三）：底層怎麼運作

> **目標**：拆開 `/speckit.*` 指令的黑盒子，看清楚一條指令怎麼對應到 per-agent 的 prompt/skill 檔、怎麼呼叫 Bash/PowerShell 腳本完成 git 操作，以及 templates 如何用 `[NEEDS CLARIFICATION]` 和 `[P]` 這兩種標記在語言層面約束 LLM 的輸出。
>
> **環境**：Spec Kit v0.11.10（查證日期 2026-06-30）。指令名稱與腳本介面在此版本前已歷經多次重命名，本章所有細節以當前 README 與 `spec-driven.md` 為準。

---

在前兩章，我們從使用者的角度跑完了安裝與端到端工作流。但每次輸入 `/speckit.specify`，背後到底發生了什麼？prompt 是如何被送進 LLM 的？branch 怎麼自動被創出來？`[NEEDS CLARIFICATION]` 為什麼能讓 LLM 乖乖停在問題上而不是自行填答？這一章我們從外殼一層層剝到核心。

## 心智圖像：三層架構

Spec Kit 的底層由三層組成，我們先把全貌放出來，再逐層深入：

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1 — 指令層  (Command Layer)                                │
│  /speckit.<command>  →  per-agent prompt 檔 / skill 定義         │
│  Agent 讀進來的「說明書」，告訴它這個指令的目標、步驟、限制      │
└────────────────────────────┬─────────────────────────────────────┘
                             │ 指令 prompt 呼叫腳本
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 2 — 腳本層  (Script Layer)                                 │
│  .specify/scripts/bash/*.sh  或  .specify/scripts/ps/*.ps1       │
│  處理 git 操作、檔案系統操作、自動編號、prerequisite 檢查         │
└────────────────────────────┬─────────────────────────────────────┘
                             │ 腳本產出 / 複製 template
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 3 — 模板層  (Template Layer)                               │
│  .specify/templates/spec-template.md / plan-template.md / ...    │
│  作為「主動約束」的骨架，[NEEDS CLARIFICATION] 與 [P] 標記       │
│  引導 LLM 寫什麼、不寫什麼、哪裡留空給人類回答                   │
└──────────────────────────────────────────────────────────────────┘
```

三層分工明確：**指令層**管「要做什麼」，**腳本層**管「怎麼動 git 和檔案系統」，**模板層**管「LLM 的輸出長什麼形狀」。

---

## 在這之前人們怎麼做

在 Spec Kit 出現之前，有兩種主流作法：

**作法一：直接提問**。把需求貼進 GitHub Copilot 或 Claude，等它產出一段程式碼。問題是 LLM 對模糊指令的反應是「填空」——它會用最有可能的細節填滿你沒說的部分，而這些細節未必是你想要的。

**作法二：人工寫規格**。在 Confluence 或 Notion 寫需求文件，再手動把相關段落貼給 LLM。問題是複製貼上很容易把上下文漏掉，而且文件跟程式碼是兩個分離的世界，一邊更新另一邊就落後了。

Den Delimarsky 在 2025-09-02 的 GitHub Blog 公告裡把這個核心問題描述得很清楚：

> We treat coding agents like search engines when we should be treating them more like literal-minded pair programmers.

「字面意義」(literal-minded) 的意思是：你說什麼它就做什麼，沒說的它就自己猜。Spec Kit 的目標是把「沒說清楚的部分」消滅在規格階段，而不是讓 LLM 在實作時亂猜。

---

## Layer 1：指令如何對應到 prompt 檔 / skill 定義

### `specify init` 在做什麼

當你執行 `specify init my-project --integration claude` 時，Python CLI (`specify-cli`) 做的事情是：

1. 在專案根目錄建立 `.specify/` 目錄樹（memory、templates、scripts）
2. **讀取 per-agent 的整合設定**（`_agent_config.py`）——這是 CLI 的核心設定檔，存放所有 agent 對應的安裝目錄、指令格式、是否支援 skills 模式等資訊
3. 把每個 `/speckit.*` 指令對應的 prompt 文字寫進 agent 的指令目錄

對 Claude Code 來說，這些 prompt 檔被寫進 `.claude/commands/` 或 `.claude/skills/`（取決於是否加了 `--integration-options="--skills"`）。對 GitHub Copilot 來說是 `.github/prompts/`。對 Gemini CLI 是 `.gemini/commands/`。

**每個 `/speckit.<command>` 指令，本質上就是一個 Markdown 格式的 prompt 文字檔**，裡面寫著這個「動作」的目標、前置條件、步驟，以及要呼叫哪些腳本。

### 兩種安裝模式：slash-command vs skills

Spec Kit 支援兩種模式，在安裝時決定：

| 模式 | 安裝方式 | Agent 呼叫語法 | 典型 agent |
|---|---|---|---|
| slash-command 模式（預設） | prompt 寫進 agent 指令目錄 | `/speckit.specify` | Copilot、Cursor、Gemini CLI |
| skills 模式 | agent skill 定義（通常是 YAML + prompt） | `$speckit-specify`（Codex）或 skill 選單 | Claude Code、Codex CLI、Zed |

> 如果你對 Claude Code 的 commands vs skills 差異還不熟，先回看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)。

加了 `--integration-options="--skills"` 之後，安裝程式改走 skills 路徑。指令名稱在 Codex CLI 裡從 `/speckit.*` 變成 `$speckit-*`——這不是 Spec Kit 設計的，而是 Codex CLI 自己的 skill 呼叫語法。

### 每個 prompt 檔的結構

以 `/speckit.specify` 對應的 prompt 文字為例，它大致包含：

```
目標（Goal）：
  為使用者描述的功能生成功能規格，只聚焦在 WHAT 和 WHY，不涉及 HOW。

前置條件（Prerequisites）：
  - 執行 create-new-feature.sh，取得下一個可用的 feature 編號與分支名稱。
  - 確認 .specify/memory/constitution.md 存在。

步驟（Steps）：
  1. 呼叫 create-new-feature.sh <feature-name>
  2. 讀取 .specify/templates/spec-template.md
  3. 根據使用者輸入與模板，產出 specs/<branch>/spec.md

限制（Constraints）：
  - 不得提出任何技術方案。
  - 對於不確定的地方，用 [NEEDS CLARIFICATION: <問題>] 標記，不要自行假設。
```

實際 prompt 更長，但骨架就是這樣：**目標** + **前置條件（呼叫哪個腳本）** + **步驟** + **限制**。每一條限制都是在告訴 LLM「不准越界」。

---

## Layer 2：腳本如何處理 git 和檔案系統

`.specify/scripts/bash/` 目錄（Windows 對應 `.specify/scripts/ps/`）裡有五個腳本，各自負責不同的 plumbing（底層操作）：

```
.specify/scripts/bash/
├── check-prerequisites.sh   # 確認所需 artifact 存在
├── common.sh                # 共用函式（顏色輸出、錯誤處理）
├── create-new-feature.sh    # 掃描現有 specs/，取下一個號碼，建分支，複製 template
├── setup-plan.sh            # 確保 plan.md 的相依文件目錄存在
└── setup-tasks.sh           # 確保 tasks.md 的相依文件目錄存在
```

### `create-new-feature.sh` 的邏輯

這是整個腳本層最核心的一支。它做三件事：

**第一：自動找下一個 feature 編號**

```bash
# 偽碼示意（實際腳本為 Bash）
existing=$(ls specs/ 2>/dev/null | grep -E '^[0-9]{3}-' | sed 's/-.*//' | sort -n | tail -1)
next=$(printf "%03d" $((10#${existing:-0} + 1)))
```

它掃描 `specs/` 目錄下所有子目錄，找到形如 `NNN-name` 的資料夾，取最大的編號加一。所以如果你已經有 `specs/001-user-auth/` 和 `specs/002-payment/`，下一個就是 `003-`，不需要人工管理編號。

**第二：建立語意性的 git branch**

腳本把 feature 名稱轉換成 git branch 名稱（空格換底線或連字號、轉小寫），然後執行：

```bash
git checkout -b "${next}-${feature_slug}"
```

branch 名稱與 specs 子目錄名稱一致，讓 `specs/003-payment-refund/` 對應 `git branch 003-payment-refund`。追蹤工作項目時，branch 就是索引。

**第三：把 template 複製進去**

```bash
mkdir -p "specs/${next}-${feature_slug}"
cp .specify/templates/spec-template.md "specs/${next}-${feature_slug}/spec.md"
```

這個 `spec.md` 剛複製時是空白骨架（內有 `[NEEDS CLARIFICATION]` 標記），LLM 讀到 prompt 後會把它填寫完整。

### `check-prerequisites.sh` 的邏輯

這個腳本在幾個指令的 prompt 裡被引用，目的是確認「上一個 artifact 已存在」再繼續。

> **注意（查證 2026-06-30）**：根據 `spec-driven.md`，`check-prerequisites.sh` 是腳手架中真實存在的腳本，但 `spec-driven.md` 並未將其描述為正式的「phase-gate」機制；它的確切行為以具體 release 版本的原始碼為準，此處描述的是其文件化的意圖，而非逐行驗證的行為。

概念上，這個腳本會做類似：

```bash
if [ ! -f "specs/${BRANCH}/spec.md" ]; then
  echo "ERROR: spec.md 不存在，請先執行 /speckit.specify"
  exit 1
fi
```

阻止 LLM 在 spec 還沒寫的情況下就衝去執行 `/speckit.plan`。

### 跨平台設計：sh vs ps

安裝時可以指定 `--script sh`（Bash，預設）或 `--script ps`（PowerShell），對應 Windows 環境。CLI 的 `init.py` 原始碼驗證：

```python
SCRIPT_TYPE_CHOICES = {"sh", "ps"}
```

兩組腳本提供完全相同的語意，只是語法不同。這讓 Spec Kit 可以在 Linux、macOS、Windows 上用一致的工作流。

---

## Layer 3：模板怎麼約束 LLM

這是整個架構中最細膩的部分。模板不只是空白表格，它們是 **主動約束（active constraints）**——告訴 LLM「什麼要寫、什麼不准寫、什麼要留空」。

`spec-driven.md`（Spec Kit 的方法論文件）明確描述了這個設計意圖：

> Templates constrain the LLM's output in productive ways. The spec template enforces 'Focus on WHAT users need and WHY'...

### `spec-template.md`：只許寫 WHAT 和 WHY

spec 模板強制執行一條核心原則：**規格描述需求，不描述解法**。模板的結構大致如下（基於文件描述，非直接引用模板全文）：

```markdown
# Feature: [FEATURE_NAME]

## Overview
[描述這個功能解決什麼問題，以及為什麼重要]

## User Journeys
### Journey 1: [角色] 想要 [目的]
- 前置條件：...
- 步驟：...
- 預期結果：...

## Acceptance Criteria
- [ ] [驗收條件 1]
- [ ] [驗收條件 2]

## Out of Scope
- [明確排除的功能]

## Open Questions
- [NEEDS CLARIFICATION: 這個功能需要支援多語系嗎？]
- [NEEDS CLARIFICATION: 離線模式下的行為是什麼？]
```

關鍵是那個 **`[NEEDS CLARIFICATION: ...]`** 標記。

### `[NEEDS CLARIFICATION]` 的工作原理

這個標記的設計很精妙。它在 prompt 的 Constraints 段落裡被定義：

- 當 LLM 遇到它無法從現有資訊確定的需求時，**不准自行假設，必須用 `[NEEDS CLARIFICATION: <具體問題>]` 標記**
- 指令的 prompt 告訴 LLM：如果產出的 spec.md 裡仍有 `[NEEDS CLARIFICATION]`，表示這份 spec 尚未完成，需要人類回答這些問題再繼續

這是一個精巧的「注意力鉤子（attention hook）」。因為 `[NEEDS CLARIFICATION]` 是特殊格式（方括號 + 全大寫關鍵字），LLM 在生成時更容易把它當成「這裡是待填項目」而非正文，從而避免了「自動填空」的問題。

`/speckit.clarify` 指令存在的目的就是系統性地把這些標記轉換成問答過程——它會逐一問你每個 `[NEEDS CLARIFICATION]` 的問題，把你的答案寫回 spec.md 的 Clarifications 段落。

**實際效果對比：**

假設你說：「我需要一個使用者登入功能」。

不用模板的 LLM 可能直接產出：

```
使用者輸入 email 和密碼，系統以 bcrypt 加密驗證，
失敗三次後鎖定帳號 15 分鐘，支援 Google SSO...
```

（這是 HOW，不是 WHAT/WHY，而且 bcrypt、15 分鐘、Google SSO 都是 LLM 自行填空的）

用 spec-template.md 的 LLM 會產出：

```markdown
## User Journeys
### Journey 1: 已有帳號的使用者想要進入系統

## Open Questions
- [NEEDS CLARIFICATION: 需要支援 Social Login（如 Google、GitHub）嗎？]
- [NEEDS CLARIFICATION: 多次失敗後的鎖定策略由產品決定，還是工程自行決定？]
- [NEEDS CLARIFICATION: 是否需要 MFA？]
```

後者把不確定的地方攤開來，讓人類決定，而不是讓 LLM 猜。

### `tasks-template.md`：`[P]` 標記與並行安全

tasks 模板裡有另一個關鍵標記：**`[P]`**，代表 Parallel（可並行執行）。

tasks.md 的結構大致如下：

```markdown
## Phase 1: Setup
- [P] Task 1.1: 建立資料庫 schema
- [P] Task 1.2: 初始化 API 路由結構
- Task 1.3: 整合測試（依賴 1.1 和 1.2）

## Phase 2: Core Implementation
- Task 2.1: 實作登入邏輯（依賴 Phase 1）
```

`[P]` 標記的語意是：這些 task 沒有互相依賴，可以讓多個 AI agent（或人類）同時處理，不用等前一個完成。沒有 `[P]` 的 task 表示有依賴關係，必須按順序執行。

在生成 tasks.md 時，LLM 被 tasks-template 的結構約束：每個 task 只能由上游 artifact（plan.md、data-model.md、contracts/）推導，不能憑空添加。

### 模板解析的優先順序

Spec Kit 支援 extensions（新增指令）、presets（覆蓋現有模板）、bundles（角色打包）。在 runtime 時，模板解析走這個優先堆疊：

```
project-local overrides
       ↓ （如果沒有）
presets（由 specify preset add 安裝）
       ↓ （如果沒有）
extensions（由 specify extension add 安裝）
       ↓ （如果沒有）
core 預設模板
```

這讓團隊可以在 presets 裡定義自己的規格格式（例如加入公司特有的安全檢核欄位），同時還能 `specify self upgrade` 更新 core 部分而不覆蓋自訂。

---

## 從一個 `/speckit.specify` 指令追蹤完整呼叫鏈

把三層串在一起，追蹤一次完整的呼叫：

```
使用者在 Claude Code 輸入：
/speckit.specify 建立一個任務管理功能，讓使用者可以新增、完成、刪除任務
         │
         ▼
[Layer 1] Claude 讀取 .claude/commands/speckit.specify.md
  → prompt 告訴 Claude：
    1. 先呼叫 create-new-feature.sh "task-management"
    2. 讀取產生的 specs/<branch>/spec.md（已含模板骨架）
    3. 根據使用者輸入填寫 spec，遵守 WHAT/WHY 限制
    4. 不確定的地方用 [NEEDS CLARIFICATION] 標記
         │
         ▼
[Layer 2] Claude 執行 Bash: create-new-feature.sh "task-management"
  → 掃描 specs/，發現已有 001-auth，002-profile
  → next = "003"
  → git checkout -b 003-task-management
  → mkdir specs/003-task-management/
  → cp .specify/templates/spec-template.md specs/003-task-management/spec.md
         │
         ▼
[Layer 3] Claude 讀取 specs/003-task-management/spec.md（模板骨架）
  → 填寫 Overview、User Journeys、Acceptance Criteria
  → 遇到「刪除後是否可以還原？」不確定
  → 寫入 [NEEDS CLARIFICATION: 刪除任務後是否需要支援復原（undo）功能？]
         │
         ▼
最終產出 specs/003-task-management/spec.md，
git 已切到 003-task-management branch，
spec.md 被 git add（或 Claude 提示你 commit）
```

整個過程中，Claude 同時扮演兩個角色：**腳本執行者**（呼叫 bash 做 git 操作）和 **規格撰寫者**（根據模板填寫內容）。Spec Kit 把這兩個角色的邊界切清楚——腳本做機械性操作，LLM 做需求分析。

---

## 對比：Spec Kit 這個設計選擇 vs 替代方案

| 方案 | 優點 | 缺點 |
|---|---|---|
| **Spec Kit：模板 + 腳本 + prompt 分層** | 每層職責清楚；可跨 agent 移植；模板可 override；bash/ps 雙平台 | 多層間接，debug 時需要追蹤三層；腳本語言（bash）對複雜邏輯不夠強 |
| **全部用 LLM prompt 完成（不用腳本）** | 安裝簡單，不依賴 shell | LLM 做 git 操作容易出錯；branch 命名不一致；沒有確定性保證 |
| **全部用 CLI 程式完成（不用 LLM 生成 spec）** | 操作確定性高 | 失去 LLM 的需求分析能力；規格要全部人工撰寫 |
| **其他 agent 框架（如 Kiro 三文件）** | IDE 整合深，UI 體驗好 | 綁定特定 IDE；工作流相對固定，擴充性不同 | 

> 如果你想比較 Spec Kit 與 Kiro 的架構選擇，先往前跳看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)，或往後看 [Ch 32 工具橫向對比：什麼任務選什麼](./32-tool-comparison.md)。

---

## 踩雷集錦

### 錯誤直覺一：「`/speckit.*` 是 agent 內建功能」

**錯誤認識**：以為輸入 `/speckit.specify` 時是 Claude Code 或 Copilot 自帶的功能在運作，跟 Spec Kit 無關。

**正確認識**：`/speckit.*` 是 `specify init` 寫進 `.claude/commands/` 或 `.github/prompts/` 的 prompt 文字檔，agent 讀的是這些檔案。如果你沒有執行過 `specify init`，或者刪掉了 `.claude/` 目錄，這些指令就不存在了。這也是為什麼不同 agent 的指令前置符不同（Codex CLI 用 `$speckit-*`）——各 agent 的指令格式由 `_agent_config.py` 配置，不是 Spec Kit hardcode 進去的。

### 錯誤直覺二：「模板只是格式，LLM 可以不理它」

**錯誤認識**：把 `spec-template.md` 當成可選的參考格式，認為 LLM 填不填標記無所謂。

**正確認識**：模板是 prompt 流程的一部分。指令的 prompt 明確要求 LLM「讀取模板，按格式填寫，遇到不確定的地方用 `[NEEDS CLARIFICATION]` 而非自行假設」。如果你覆蓋（override）了 `spec-template.md` 但移除了這些結構提示，LLM 的輸出質量會下降，因為它失去了邊界約束。

### 錯誤直覺三：「`[P]` 標記是 Spec Kit 的並行執行引擎」

**錯誤認識**：以為加了 `[P]` 的 task，Spec Kit 會自動並行執行它們。

**正確認識**：`[P]` 是一個文字標記，它的作用是**給人類或多 agent 協調者的訊號**，表示這幾個 task 可以安全並行，但 Spec Kit 本身不會自動 fork 多個 agent 去並行執行。實際的並行執行取決於你的工作流——你可以手動開多個 agent session，或自行編寫協調腳本。Spec Kit 管的是「告訴你哪裡可以並行」，不管「實際怎麼並行」。

### 錯誤直覺四：「腳本執行失敗 LLM 會告訴你」

**錯誤認識**：以為 `create-new-feature.sh` 出錯時，agent 會清楚地報告腳本錯誤。

**正確認識**：LLM 是透過呼叫 shell 工具來執行腳本的。如果腳本的 stderr 沒有被妥善傳回 agent 的上下文，agent 有可能看不到錯誤而繼續執行，產出看似正常但實際上 branch 沒建或 template 沒複製的情況。**確認的方式**：用 `/speckit.checklist` 或手動 `git branch --show-current` 確認當前 branch 名稱確實是 `NNN-feature-name` 格式。

### 錯誤直覺五：「bash 版和 ps 版的腳本行為完全一樣」

**錯誤認識**：以為 `--script sh` 和 `--script ps` 只是語法不同，行為等價。

**正確認識**：概念上等價，但細節行為可能有差異，特別是路徑處理（Windows 的 `\` vs `/`）、glob 模式（bash 的 `ls specs/` 和 PowerShell 的 `Get-ChildItem`）在某些邊界情況下可能不同。在 Windows 上遇到奇怪的 branch 命名問題，先確認你用的是 `--script ps`，不是 `--script sh`。

---

## 進階延伸

### 自訂 template：把公司規範寫進規格約束

Spec Kit 的 preset 機制讓你可以覆蓋 `spec-template.md`，加入公司特有的欄位：

```bash
# 建立一個 preset 目錄
mkdir -p .specify/presets/my-company/templates

# 複製並修改 spec 模板
cp .specify/templates/spec-template.md .specify/presets/my-company/templates/spec-template.md
# 加入公司欄位，例如：
# ## Compliance Requirements
# - [NEEDS CLARIFICATION: 這個功能是否涉及個資（GDPR/CCPA）？]
# - [NEEDS CLARIFICATION: 需要 SOC 2 稽核日誌嗎？]

# 啟用 preset
specify preset add ./my-company-preset
```

這樣每次 `/speckit.specify` 產出的 spec.md 都會自動包含公司的合規檢核欄位，不用靠工程師記得填。

### 擴充指令：寫一個 `/speckit.security-review`

Spec Kit 的 extension 機制允許你新增 `/speckit.*` 指令。你需要提供一個 prompt 文字和（可選的）腳本：

> **注意（查證 2026-06-30）**：`/speckit.review` 指令在 2026-06 前只存在於 GitHub issue #1323 作為功能請求，尚未合併進 main。官方 extension 撰寫文件是此功能的正確參考，而非本書的任何描述。

概念上，extension 的結構是在特定目錄放置 prompt 文字，然後執行：

```bash
specify extension add ./my-security-review-extension
```

安裝後，agent 就能使用 `/speckit.security-review` 這個指令。

### 追蹤 Spec Kit 的演進

Spec Kit 從 2025-08-21 創建到 2026-06-29 的 v0.11.10，已發布 175+ 次 release。指令名稱本身也演進過：原始 launch（2025-09-02 blog）用的是未加前置的 `/specify`、`/plan`、`/tasks`；`/speckit.` 前置和 `/constitution`、`/clarify`、`/analyze` 等指令都是後來加入的。

追蹤演進的最可靠方式：

```bash
# 查看 CHANGELOG 或 releases
specify self check  # 確認目前版本
# 到 GitHub releases 頁面看每個版本的說明
```

---

## 動手練習

**練習一：解剖你的 prompt 檔**

如果你已經完成 Ch 27 的安裝，找到你的 agent 對應的指令目錄，用文字編輯器打開其中一個 prompt 檔（例如 `.claude/commands/speckit.specify.md`）。

問自己：
- 這個 prompt 如何告訴 LLM 呼叫哪個腳本？
- Constraints 段落裡有哪些限制條款？
- 如果你把 `[NEEDS CLARIFICATION]` 的說明從這個 prompt 裡移除，LLM 的行為會如何改變？

**練習二：模擬一次 `create-new-feature.sh`**

在你的 spec-kit 專案目錄裡，手動執行：

```bash
ls specs/
# 查看目前有哪些 feature 目錄

# 手動跑腳本
.specify/scripts/bash/create-new-feature.sh "my-test-feature"

# 確認結果
git branch --show-current  # 應該是 XXX-my-test-feature
ls specs/                  # 應該多了一個新目錄
cat specs/XXX-my-test-feature/spec.md  # 應該是 spec-template.md 的內容
```

**練習三：在 spec-template.md 加入一個強制欄位**

找到 `.specify/templates/spec-template.md`，在 `## Open Questions` 區塊前加入：

```markdown
## Definition of Done
- [ ] 所有 Acceptance Criteria 有對應的自動化測試
- [ ] [NEEDS CLARIFICATION: 這個功能的 rollback 計畫是什麼？]
```

然後跑一次 `/speckit.specify`，觀察 LLM 是否在產出的 spec.md 裡包含這個新欄位。

**練習四：驗證 `[P]` 標記的語意**

用 `/speckit.tasks` 為一個你的 feature 產出 tasks.md。找到所有標有 `[P]` 的 task，問自己：

- 這些 task 真的沒有互相依賴嗎？（LLM 可能判斷錯誤）
- 如果你想讓某個沒有 `[P]` 的 task 改成可並行，你需要手動修改 tasks.md 嗎，還是有辦法透過修改 plan.md 讓 LLM 重新推導？

---

## 本章重點整理

- Spec Kit 的底層是三層架構：**指令層**（per-agent prompt 文字檔）→ **腳本層**（bash/ps 處理 git 和檔案系統）→ **模板層**（主動約束 LLM 的輸出形狀）
- `specify init` 把每個 `/speckit.*` 指令的 prompt 寫進 agent 的指令目錄；指令不是 agent 內建的，是 Spec Kit 安裝進去的
- `create-new-feature.sh` 做三件事：自動找下一個 feature 編號、建 git branch、從 template 複製骨架
- `[NEEDS CLARIFICATION: <問題>]` 是一個「注意力鉤子」，強制 LLM 在不確定的地方停下來問人，而不是自行猜測
- `[P]` 標記是給人類或協調者看的「可並行」訊號，Spec Kit 不自動並行執行
- 模板支援 preset override 和 extension，優先順序：project-local > presets > extensions > core
- Spec Kit 迭代極快（v0.11.10，175+ releases），指令名稱本身也演進過；課程內容以查證日期 2026-06-30 為準，使用前確認 `specify self check`

---

## 自我檢核

用自己的話回答，不要翻書：

- [ ] 如果同事問「`/speckit.specify` 是 Claude Code 內建的嗎？」，你怎麼解釋它的來源？
- [ ] 面試時被問「Spec Kit 的 template 是怎麼約束 LLM 的？」，你會舉什麼具體例子？
- [ ] `[NEEDS CLARIFICATION]` 和 `[P]` 這兩個標記，一個是給 LLM 看的信號，一個是給人（或協調者）看的信號——你能分清楚哪個是哪個嗎？
- [ ] 如果你想讓 `/speckit.specify` 產出的 spec.md 必須包含一個「合規需求」欄位，你會修改哪一層？（指令層的 prompt、腳本層、還是模板層？）
- [ ] `create-new-feature.sh` 的三個步驟是什麼？如果 `specs/` 目錄為空，第一個 feature 的編號會是什麼？

---

## 延伸閱讀

- **github/spec-kit — `spec-driven.md`（主要）**：https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
  本章「模板作為主動約束」、`[NEEDS CLARIFICATION]` 的機制說明，直接來自這份文件。建議通讀一遍，它是 README 之外最重要的原始資料，把方法論的細節都講清楚了。與本章的關聯：Chapter 的 Layer 3 幾乎完全來自這裡。

- **github/spec-kit — README（指令表格與 .specify/ 目錄樹）**：https://raw.githubusercontent.com/github/spec-kit/main/README.md
  完整的 Core / Optional 指令表格、`specify init` 的 flag 定義、scaffolded 目錄結構。每次 Spec Kit 發版後，從這裡確認指令名稱有沒有再改。與本章的關聯：Layer 1 的指令清單與 Layer 2 的腳本目錄結構以此為準。

- **Supported Integrations — Spec Kit Docs**：https://github.github.io/spec-kit/reference/integrations.html
  各 agent 的安裝目錄（`.claude/commands/` vs `.github/prompts/` vs `.gemini/commands/`）與 skills 模式說明。你想弄清楚「我用的 agent 的 prompt 檔在哪裡」，這頁是最快的答案。與本章的關聯：Layer 1 的 per-agent 安裝對應。

- **GitHub Blog — Spec-driven development with AI（原始 launch 公告）**：https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/
  Den Delimarsky，2025-09-02。看原始 launch 的設計動機（「literal-minded pair programmers」），以及當時只有四個未加前置的指令（`/specify`、`/plan`、`/tasks` + 手動 implement）。對比現在的 v0.11.10，能感受到整個工具怎麼演進出來的。與本章的關聯：歷史脈絡與「為什麼這樣設計」的第一手資料。

- **github/spec-kit Releases**：https://github.com/github/spec-kit/releases
  175+ 次 release 的 changelog。看命令重命名的歷史（`/quizme` → `/speckit.clarify`、bare `/specify` → namespaced `/speckit.specify`）。如果你的課程材料哪天跟實際行為對不上，先來這裡找是哪個 release 改的。與本章的關聯：理解版本依賴性的最直接工具。

- **Martin Fowler — Exploring Gen AI: SDD Tools**：https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
  Martin Fowler 的站點對 SDD 工具橫向比較的分析，是理解 Spec Kit 在整個 SDD 工具版圖裡的位置的好參考。與本章的關聯：Ch 32 的前置閱讀，也幫助理解本章的架構選擇為什麼做出這樣的取捨。

---

下一章我們把視角轉到 AWS Kiro，看看它用「三檔規格」（requirements.md / design.md / tasks.md）+ EARS 句型 + steering + hooks 這套完全不同的架構，解決同樣的問題。比較兩者，才能真正理解各自的取捨。

→ [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)
