# Ch 6 — Claude Code 進階:skills / hooks / subagents / MCP

> 目標:把 Claude Code 從「會用」升到「會改」。Skills 給它能力、Hooks 給它規範、Subagents 給它分身、MCP 給它工具。

這四個機制是 Claude Code extensibility 的四個支柱。先看全景,再逐個展開。

## 四個支柱的定位

| 機制 | 解決什麼 | 類比 |
|---|---|---|
| **Skills** | 「某類任務該怎麼做」的知識 | 一本 SOP 手冊 |
| **Hooks** | 生命週期事件的介入 | trigger / middleware |
| **Subagents** | 把特定任務 delegate 給專門 agent | 外包專家 |
| **MCP** | 接外部工具 / 資料源 | plugin |

**何時用哪個**:

- 要 Claude 「學會新任務」 → Skill
- 要 Claude **每次做 X 前 / 後** 執行某動作 → Hook
- 要 Claude 把某子任務交給 **context 獨立** 的 agent → Subagent
- 要 Claude 存取 **外部系統**(DB、API) → MCP

---

## 1. Skills

Skill = **告訴 Claude 一類任務該怎麼做**。

### 位置

```
~/.claude/skills/<skill-name>/SKILL.md       # 全局(所有 project 可用)
.claude/skills/<skill-name>/SKILL.md         # 專案(只在這 repo)
```

**Priority**:enterprise > personal > project。name 衝突時以高優先權為準。

### 最小 SKILL.md

```markdown
---
name: commit-msg
description: Write a commit message following this project's conventions. Use when the user asks to commit or create a commit message.
---

Follow Conventional Commits format: `<type>(<scope>): <summary>`

Types: feat, fix, refactor, docs, test, chore
Scope: the module/area affected (e.g., auth, api, db)
Summary: imperative, present tense, no period, < 50 chars

If the change is significant, add a body after a blank line explaining why.
```

就這樣。Claude Code 會自動讀 `name` + `description` 載入 skill 的存在感;真正的 body 只在 skill 被觸發時才注入 context(節省 token)。

### Skill 何時會被載入

兩種方式:

1. **Claude 自動判斷**:根據 `description` 判斷當前任務是否適用
2. **手動呼叫**:`/commit-msg` 或在對話中顯式提及 skill 名

想**禁止自動載入**:frontmatter 加 `disable-model-invocation: true`,只能手動呼叫。

### 進階:supporting files

Skill 目錄下可以放其他檔案:

```
~/.claude/skills/commit-msg/
├── SKILL.md
├── examples.md
└── template.txt
```

SKILL.md 裡 reference:

```markdown
See examples in @examples.md.
Use template @template.txt when drafting.
```

Claude 被觸發 skill 後會跟著載入。

### Shell injection

SKILL.md 裡 `` `!command` `` 會被執行,輸出 inline 進 skill:

```markdown
Current git branch:
!`git branch --show-current`
```

Skill 每次被觸發時重算。有時用於「動態 context」。

### Skill 寫作原則

1. **Description 要足夠具體**,Claude 才能正確判斷觸發時機。
2. **Body 用指令句**,不要說「you should ...」,直接「Do this」、「Follow X」。
3. **Skills 不是 prompt 教材**,不是用來教 Claude 基礎。Skills 是**任務流程的標準化**。
4. **一個 skill 一件事**。不要「super skill」做 10 件。

---

## 2. Hooks

Hooks = **在 Claude Code 的生命週期事件插入 shell 命令或 HTTP call**。

### 可用的 hook

| Hook | 觸發時機 |
|---|---|
| `SessionStart` | Session 開始 / resume / compact |
| `SessionEnd` | Session 結束 |
| `UserPromptSubmit` | User 送出一則訊息後 |
| `PreToolUse` | 任何 tool 執行前 |
| `PostToolUse` | Tool 執行成功後 |
| `PostToolUseFailure` | Tool 執行失敗後 |
| `PermissionRequest` | 權限對話要出現前 |
| `SubagentStart` / `SubagentStop` | 子 agent 生命週期 |
| `Stop` | Claude 結束一個 turn |

### 配置範例

`.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "./scripts/validate-bash-command.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "npm run lint:fix"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "say 'Claude stopped'"
          }
        ]
      }
    ]
  }
}
```

### Hook 能做什麼

**實用例子**:

1. **自動 format**:`PostToolUse: Edit|Write` → 跑 prettier / ruff
2. **自動 test**:`PostToolUse: Edit` matching python 檔 → 跑相關 test
3. **Bash 安全檢查**:`PreToolUse: Bash` → 檢查有沒有 `rm -rf`、`curl | sh` 之類,有就退出 1(block)
4. **Notification**:`Stop` → 通知系統 Claude 結束了(長任務)
5. **Log 所有操作**:`PostToolUse` → append 到 audit log

### Hook 退出值的語義

- `exit 0`:放行
- `exit 2`:block,**stderr 會送回 Claude** 當 feedback
- 其他 non-zero:block,silent

**`exit 2` 是最有用的**:你可以寫「這 bash 有問題,改用 XX」,Claude 收到後會自動修正。

### Hook 的限制

- **每個 hook 預設 60 秒 timeout**(可配置)
- **Hook 不能修改 Claude 的 input/output**,只能 block / 放行 / 給 feedback
- **Hook 以你當前 user 權限執行**,寫壞會搞爛系統

### 注意:memory ≠ hook

使用者經常搞混。

- 「以後 commit 前都先跑 test」→ **hook**(自動執行)
- 「以後幫我 review PR 要看 security」→ **memory / CLAUDE.md**(Claude 每次提醒自己)

Memory 是 Claude 看文字自我約束;hook 是 harness 實際攔截。要確定「必然執行」用 hook。

---

## 3. Subagents

Subagent = **獨立 context 的專門 agent,由主 agent delegate 給他**。

### 為什麼要 subagent

- **Context isolation**:子任務的大量 tool output 不污染主對話
- **專門化**:給它 focused system prompt + 限制 tool set
- **Scope control**:某任務要用 admin 權限,你只給 subagent,主 agent 不接觸

### 定義 Subagent

`.claude/agents/<name>.md` 或 `~/.claude/agents/<name>.md`:

```markdown
---
name: security-reviewer
description: Expert security reviewer. Use for reviewing code changes for security vulnerabilities, auth issues, and input validation.
tools: Read, Glob, Grep
model: opus
---

You are a security-focused code reviewer.

When given code to review:
1. Check for SQL injection, XSS, CSRF patterns
2. Check authentication and authorization logic
3. Check input validation on all user-facing entry points
4. Flag use of unsafe functions (eval, exec, shell=True)

Output format:
- [CRITICAL] for vulns that must be fixed
- [WARNING] for suspicious patterns
- [OK] when clean

Be thorough but terse. No general code quality comments.
```

**Frontmatter 欄位**:

- `name`:subagent 名
- `description`:Claude 決定何時 delegate 的依據
- `tools`:限制可用工具,留白表示所有
- `model`:用哪個模型,留白用 session 預設

### 使用

主 agent 會自動判斷何時 delegate(根據 description)。你也可以顯式:

```
Please use the security-reviewer subagent to audit auth.py
```

或者看 Claude 是否 spawn subagent(`/status` 或介面上會顯示)。

### 何時該寫 subagent

- 某類任務有**穩定的 scope**(reviewer、test writer、migrator)
- 主任務**不需要看到 subagent 的中間過程**(節省 context)
- 需要**限制權限**(這個 agent 只能讀不能寫)

### 跟 skill 的差別

| | Skill | Subagent |
|---|---|---|
| 本質 | 指令 + 知識 | 獨立 agent 實例 |
| Context | 注入主對話 | 獨立 context |
| 回傳 | 主 agent 直接看到 | 回傳 summary 到主 agent |
| 適用 | 短、頻繁的任務 | 長、大量 tool use 的任務 |

通常:**輕量用 skill,重量用 subagent**。

---

## 4. MCP(Claude Code 側)

MCP 在 Claude Code 是什麼:**讓 Claude 連到外部 MCP server,拿到新的工具 / 資料 / prompts**。

(MCP 本身的深入見 Part 3)

### 配置

`.claude/settings.json` 或 `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
    },
    "postgres": {
      "command": "uvx",
      "args": ["mcp-server-postgres"],
      "env": {
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```

**運作**:Claude Code 啟動時 spawn 這些 MCP server(本地 stdio),把 server 提供的 tools 加入可用工具集。

### 常用的社群 MCP server

- `filesystem`:更精細的檔案存取(官方)
- `postgres` / `sqlite`:DB 操作
- `github`:GitHub API(issue、PR)
- `slack`:Slack 訊息
- `playwright` / `puppeteer`:browser 自動化
- `memory`:persistent memory across sessions

裝好一個新 MCP server 通常是「**把它加進 settings.json 的 `mcpServers`**」。

### 看當前連了哪些 MCP

```
/mcp
```

### 何時該用 MCP 而不是寫 subagent / skill

- 需要**外部系統**(DB、API、Slack...)
- 工具可能被**其他 client**(Claude Desktop、別人的 agent)共用
- 你想把工具**版本管理**在獨立的 package

---

## 四者組合的威力

真實例子:我要建一個「自動 review PR」的 workflow。

- **Subagent:`pr-reviewer`** - 獨立 context、focused prompt
- **Skill:`pr-review-checklist`** - 列出 review 該檢查的項目
- **Hook:`SessionStart`** - session 一開始檢查是否在 PR branch
- **MCP:`github` server** - 讓 agent 能 read PR diff、post comment

一個指令「review PR #123」就能跑完整流程。**這才是 Claude Code 真正的能力**。

---

## 設定檔的層級

```
Enterprise managed   ~/Library/Application Support/ClaudeCode/managed-settings.json (macOS)
User                ~/.claude/settings.json
Project shared      ./.claude/settings.json       (commit 進 git)
Project local       ./.claude/settings.local.json (不 commit,個人覆寫)
```

優先順序從上到下遞減。

**實務建議**:

- Team 共享的 skill / hook / MCP → `./.claude/settings.json`(commit)
- 你個人的偏好 → `~/.claude/settings.json`
- 不想讓 team 看到的本地設定 → `./.claude/settings.local.json`

### 一個 team 推廣 Claude Code 的設法

專案根放:

```
.claude/
├── settings.json          # team 預設
├── skills/                # team 共享 skills
│   └── ...
├── agents/                # team 共享 subagents
│   └── ...
└── CLAUDE.md              # team 共享專案說明
```

每個工程師只要:

1. 裝 Claude Code
2. `cd` 進這 repo
3. `claude`

**自動得到整組環境**。這是 Claude Code 最強的一點——**團隊級 AI 協作的最小單位**。

---

## 自我檢核

- [ ] Skills / Hooks / Subagents / MCP 各自解決什麼問題?
- [ ] 為什麼「以後提交前自動跑 test」要用 hook 不是 memory?
- [ ] Subagent 和 skill 的差別?
- [ ] Skill 的 `disable-model-invocation: true` 用來幹嘛?
- [ ] Hook 的 `exit 2` 和 `exit 1` 行為差別?

→ [Practice A — Prompting 實戰](./practice-a-prompting.md)(先略過,繼續章節)

→ [Ch 7 Messages API 與 SDK](./07-api-basics.md)
