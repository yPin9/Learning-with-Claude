# Ch 5 — Claude Code 起手式

> 目標:把 Claude Code 當日常工具。掌握 permission mode、slash command、session、IDE extension、plan mode 這些核心概念。

## 什麼是 Claude Code

**Claude Code 是 Anthropic 出的「跑在 terminal 的 Claude」**。它不是一個 chatbot,是一個 agent——能讀你本地檔案、改 code、跑 bash、裝工具。

跟 Cursor 的差別:

| | Claude Code | Cursor |
|---|---|---|
| 形式 | CLI 為主,IDE 為輔 | IDE 為主 |
| 底層 | 自研 agent loop + 自家模型 | VS Code fork + 多家模型 |
| 側重 | 自動化、agent、腳本 | 互動式寫 code |
| Extensibility | Skills / Hooks / MCP / Subagents | 部分 |

**一句話**:Cursor 像「有 Claude 的 VS Code」,Claude Code 像「有手有腳的 Claude」。

---

## 安裝(速讀)

```bash
# 安裝 CLI
npm install -g @anthropic-ai/claude-code

# 或用 brew(Mac)
brew install claude-code

# 登入
claude login
```

之後在任何 repo 下跑:

```bash
claude
```

就進入 interactive session。

**IDE extension**:VS Code 和 JetBrains 都有,裝了之後 Claude Code 知道你 IDE 打開的檔案、選取的範圍。

---

## 第一次互動:最簡單的事

```
$ cd my-project
$ claude

> Read src/app.py and explain what it does
```

Claude 會用 `Read` tool 開檔,然後回你。

或者:

```
> There's a bug where the API returns 500 for empty input. Find and fix it.
```

Claude 會 `Grep` / `Read` / `Edit`,可能還會跑 tests。

**重點**:它**不是一問一答**,是個能執行多步工具的 agent。

---

## Permission Mode(關鍵概念)

Claude Code 有四種權限模式,控制它能不能擅自執行工具:

| Mode | 行為 |
|---|---|
| **default**(預設) | 每個「有副作用」的動作都問你 yes/no |
| **acceptEdits** | 自動允許檔案修改,其他問 |
| **bypassPermissions** | 幾乎所有工具自動允許(危險) |
| **plan** | **只讀**,禁止任何修改 |

**切換方式**:
- 啟動時 `claude --permission-mode plan`
- 對話中按 `Shift+Tab` 切換
- 配置在 `.claude/settings.json` 固定預設

### Plan Mode:最重要的安全閥

`plan mode` 是 Claude Code 的「先想再做」。模型**能讀檔、分析、規劃,但不能改任何東西**。它會在最後交出一份 plan,你同意後才切到執行模式開始改。

**什麼時候該用**:

- 大範圍重構
- 不熟悉的 codebase(避免 Claude 亂改)
- 高風險變動(DB migration、production config)

**養成習慣**:遇到**沒把握**的任務,先 plan mode,看過 plan 再同意。這一步省下的事故成本,比多花 5 分鐘讀 plan 高 100 倍。

### 設預設 permission mode

`~/.claude/settings.json`:

```json
{
  "permissionMode": "default",
  "permissions": {
    "allow": [
      "Bash(npm run test:*)",
      "Bash(git status)",
      "Read(**)"
    ],
    "deny": [
      "Bash(rm -rf*)"
    ]
  }
}
```

- `allow` 裡的 pattern 會自動放行,不用逐次 prompt
- `deny` 裡的 pattern 會直接 block,連問都不問

這配置非常重要——之後 Ch 6 和 practice 會再講。

---

## 對話中的關鍵操作

### 1. Slash Commands

斜線開頭的指令。內建重要的:

| Command | 作用 |
|---|---|
| `/help` | 幫助 |
| `/clear` | 清當前 conversation context |
| `/compact` | 手動壓縮 context(快滿時) |
| `/resume` | 恢復上次 session |
| `/config` | 開設定 |
| `/permissions` | 看 / 改 permission 規則 |
| `/model` | 切換模型(Sonnet / Opus / Haiku) |
| `/status` | 看當前 session 的 token 用量 |

你可以自訂 slash command(見 Ch 6)。

### 2. `!` 前綴:直接跑 shell

```
> ! git status
```

前面加 `!`,跳過 Claude 直接跑 shell command,結果輸出到對話。

**用途**:讓 Claude 看到某個命令的輸出(例如 error log、ls 結果)。比「請 Claude 跑 X」快。

### 3. `#` 前綴:把當前 prompt 加入 CLAUDE.md

```
> # From now on, always run tests after editing Python files.
```

自動把這句加到 `CLAUDE.md`,變成 persistent memory。

### 4. `@` mention file

```
> Explain @src/auth.py
```

明確指名一個檔案,Claude 會用 `Read` 開它(不需它自己 Grep)。

### 5. Shift+Tab 切 permission mode

如前述。

### 6. Ctrl+R 看完整 tool output

長輸出會截斷。按 `Ctrl+R` 看完整。

---

## CLAUDE.md:每個專案都該有

CLAUDE.md 是 Claude Code 的 **memory file**——自動載入到 session 的起始 context。

`./CLAUDE.md`(專案根)和 `~/.claude/CLAUDE.md`(user 全局)都會被讀。

### 該寫什麼

**✓ 寫**:

- 這專案的技術棧和約定
- 命名規範、檔案結構慣例
- 常用指令(如何 run、test、deploy)
- 不要做的事(如 "don't write tests for private helpers")

**✗ 不要寫**:

- code 本身
- 會變動的具體狀態(PR 編號、當前分支)
- 大量 tutorial 內容(Claude 不需要被教基礎)

### 範例

```markdown
# Project: acme-api

FastAPI + SQLAlchemy 2.0 + PostgreSQL.

## Commands
- Test: `pytest -xvs`
- Run: `uvicorn app.main:app --reload`
- Lint: `ruff check && ruff format`

## Conventions
- DB models in `app/models/`, Pydantic schemas in `app/schemas/`
- All endpoints use async, never sync
- Migrations via alembic, don't edit schema directly

## Don't
- Don't add libraries without asking
- Don't write integration tests for trivial getters
- Don't push to main, always PR
```

Claude Code 會把它當開場 context。寫得好,Claude 行為就對齊專案慣例。

---

## Session 與 Context 管理

Claude Code 的 session 有 context window 上限(200k token)。長時間 session 會慢慢填滿。

### 看目前用量

```
/status
```

或看底下狀態列。

### Context 快滿怎麼辦

**選項 1:`/compact`**

Claude 會 summarize 當前對話,壓縮成摘要。好處:保留重要資訊;缺點:細節會丟。

**選項 2:`/clear`**

從頭開始。如果你已經把結論總結好,這是乾淨做法。

**選項 3:任它自動壓縮**

快到 limit 時 Claude Code 會自動 compact。

### 多 session 並行

同一個 repo 下可以開兩個 terminal 各跑一個 `claude`。Session 互相獨立,但改檔案會互相看到。**適合**:一個做 feature,一個同時看 test / log。

### `/resume`

`Claude Code` 自動 save session。下次在同 repo 跑 `claude --resume` 或 `/resume` 可以接回上次。

---

## IDE Extension

VS Code 和 JetBrains 裝 Claude Code extension 後:

- **Claude 知道你當前打開的檔案**
- **可以把 Claude 的建議直接 apply 到 IDE 的編輯區**
- **使用 IDE 的 diagnostics**(Claude 能看到 linter 報的錯)

設定在 IDE 裡登入同一個 Claude 帳號即可。

**建議**:有 IDE 的人都裝。光是「Claude 知道你在看哪個檔案」就省下大量 `@mention`。

---

## 模型選擇

```
/model
```

切換用哪個模型。建議:

| 模型 | 何時用 |
|---|---|
| **Sonnet 4.6** | 日常寫 code、改 code、debug,性價比最高 |
| **Opus 4.7** | 難的架構設計、需要深度思考的 debug、大規模重構 |
| **Haiku 4.5** | 大量簡單任務、cost 敏感、快速 prototype |

**Fast mode**(Opus 4.6):更快的 Opus,犧牲一點最新度。`/fast` 切換,只在 Opus 4.6 有效。

實務上:多數人 default 用 Sonnet,遇到真難的切 Opus,做 batch 用 Haiku。

---

## Plan Mode 的實戰

舉一個真實場景:你要重構一個 50 個檔案用到的 util 函式。

**錯誤做法**:

```
> Rename foo_helper to format_currency everywhere
```

Claude 會立刻開幹,可能改到 50 個檔案後發現有個測試因為別的理由失敗,但已經 changes committed。

**正確做法**:

```
> [Shift+Tab to plan mode]
> I want to rename foo_helper to format_currency across the codebase. Before changing, list all usages and any risks.
```

Claude 會 grep、read、然後報告:

```
Found in 47 files. Risks:
- Used as a default argument in 3 places — rename may affect defaults
- One usage is in a migration file (historical), changing would break deployment
- Test mocks reference foo_helper by string in 2 tests
```

看完你決定:「忽略 migration,處理其他」,然後 `Shift+Tab` 切到執行,再交出指令。

**這個「先看再做」的節奏,是 Claude Code 用得好的根本**。

---

## 幾個你會常用的快捷技巧

### 1. 一句話「繼續上次的 TODO」

CLAUDE.md 放個 `## TODO` section。下次開 session 直接「繼續 TODO」。

### 2. 對 Claude 下「小而具體」的指令

不要「幫我優化這個 function」。改成「改 foo() 的演算法從 O(n²) 到 O(n log n),用 heap 替代 nested loop」。後者可驗證,前者無法。

### 3. 有 error 時,貼 full trace

```
> Test failed. Trace:
!pytest tests/test_foo.py -xvs 2>&1 | tail -40
```

`!` 跑 pytest,結果灌進 context,Claude 有資訊 debug。

### 4. 不讓 Claude 自作聰明

```
# In CLAUDE.md or session start:
"When you're unsure about intent, ask me before changing code. Don't make up requirements."
```

---

## 自我檢核

- [ ] Plan mode 和 default mode 的差別?什麼時候該用 plan?
- [ ] `CLAUDE.md` 該放什麼、不該放什麼?
- [ ] Shift+Tab 做什麼?`!` 和 `#` 前綴呢?
- [ ] 大重構時的「先看再做」節奏怎麼走?
- [ ] `/compact` 和 `/clear` 的差別?

→ [Ch 6 Claude Code 進階:skills / hooks / subagents / MCP](./06-claude-code-advanced.md)
