# Ch 15 — Skills 的設計與寫作

> 目標:把 Skill 從 Ch 6 的「知道有這東西」提升到「寫得出可用的 skill」。什麼 skill 值得寫、怎麼寫 description、什麼是反例。

## Skills 的本質

**Skill = 結構化的可重用 prompt + 可選附件**,讓 Claude Code / Agent SDK 能在特定任務下自動載入。

技術上就是一個檔案:

```
~/.claude/skills/<name>/SKILL.md
或
.claude/skills/<name>/SKILL.md
```

```markdown
---
name: <name>
description: <何時用這 skill>
---

<skill body:給 Claude 的指令>
```

但「寫 skill」和「寫好 skill」差別巨大。

---

## Skill 的三個角色

Skill 實務上做三件事:

### 1. SOP(標準作業流程)

「我每次要 commit 都想走某流程」→ 寫成 skill。

```markdown
---
name: prepare-commit
description: Prepare a commit message and verify the diff before committing. Use when the user asks to commit or create a commit.
---

Before drafting the commit message:

1. Run `git status` and `git diff --staged` — list what's changing.
2. Run `git log --oneline -5` — see recent commit style.
3. Identify the nature of changes: feat / fix / refactor / docs / test / chore
4. Write message in format:
   - Subject line < 50 chars, imperative mood
   - Body (if needed) explains *why* not *what*, separated by blank line

If there are unstaged changes, ask before staging them.
Never commit `.env` or credentials files — check diff for them.
```

### 2. Domain knowledge

「我這 project 有特殊規範,Claude 每次都該記得」→ 寫成 project skill。

```markdown
---
name: sqlalchemy-conventions
description: This project's SQLAlchemy 2.x conventions. Use when writing or modifying database models, queries, or migrations.
---

This project uses SQLAlchemy 2.x with strict typing.

Models:
- Must use `Mapped[...]` and `mapped_column(...)` (2.x style, not legacy Column)
- Inherit from `Base` defined in `app.models.base`
- Each model has `id: Mapped[int] = mapped_column(primary_key=True)`
- Timestamps via `TimestampMixin` (already exists)

Queries:
- Use `select()` statement style, not legacy `.query(...)`
- `async_session.scalars(stmt).all()` for multiple, `.one()` or `.one_or_none()` for single
- No raw SQL unless unavoidable

Migrations:
- Autogenerate with `alembic revision --autogenerate`
- Review the generated file, rename with descriptive slug
- Don't edit DB schema without migration
```

### 3. 工具呼叫流程

「用 X 工具要注意 Y、Z」→ skill 把工具使用 pattern 固化。

```markdown
---
name: browser-research
description: Conduct web research using the browser MCP server. Use for fact-checking, current events, or pulling structured info from websites.
---

Steps:
1. Start with a search using `mcp__brave-search__web_search`
2. Pick top 3-5 results that look authoritative
3. For each, use `mcp__playwright__browse` to read full content
4. Cite sources in your final summary with URL

Don't just use one source — cross-check at least two.
If information is behind a paywall, note that instead of fabricating content.
```

---

## Description 的藝術

Description 是 Claude 「自動判斷何時該用這 skill」的依據。寫不好的後果:skill 永遠不被自動觸發,或亂觸發。

### 壞 description

```yaml
description: Helper for commits
```

太模糊,Claude 不知道「commit 時」具體是什麼情境。

### 好 description

```yaml
description: Prepare a commit message and verify the diff before committing. Use when the user asks to commit, create a commit message, or the user has finished making changes and wants to record them.
```

**三要素**:

1. **這 skill 做什麼**(「Prepare a commit message ...」)
2. **什麼情境用它**(「Use when the user asks to commit ...」)
3. **具體訊號**(「create a commit message, ... finished making changes」)

Description 的上限是 1536 字元,通常用不到。**兩三句話是甜蜜點**。

### 寫 description 的 checklist

- [ ] 句子明確具體,不是抽象修飾
- [ ] 含「Use when ...」的條件
- [ ] 不跟其他 skill 的 description 互相重疊(避免 Claude 混淆)
- [ ] 涵蓋使用者可能的不同說法(「commit」、「record changes」、「check in」)

---

## Body 的設計

### 用祈使句,不是描述句

```markdown
# BAD
When you need to commit, you should run git status first.

# GOOD
Run `git status` first.
```

Claude 會順 body 的風格輸出。祈使句讓 Claude 自己也用祈使句思考。

### 結構化 > 散文

Claude 讀 list / heading 比讀大段 paragraph 更準:

```markdown
## Before committing
1. Run `git status`
2. Run `git diff --staged`
3. ...

## Message format
- Subject < 50 chars
- Body explains *why*
- ...

## Don't
- Don't commit .env files
- Don't bypass pre-commit hooks
```

### 提供具體範例

Few-shot 在 skill 也有用:

```markdown
## Examples

Good:
> feat(auth): add OAuth login flow
>
> Users requested Google SSO. Adds /auth/google endpoint and
> updates User model with oauth_provider field.

Bad:
> fix bug
> updated things
> asdf
```

Claude 看過例子會更準模仿。

### 不要重複 Claude 本來就會的事

```markdown
# BAD skill body
You are an AI assistant. Be helpful.
Use markdown to format your response.
```

這些 Claude 內建就會,寫了浪費 context。**Skill 只寫「這特定 skill 的特定東西」**。

---

## 何時該寫 skill,何時不該

### ✓ 寫 skill

- 工作流**重複出現**,值得固化
- 有**具體 domain 規則**(命名、format、SOP)
- 跨 session 要保持**一致性**
- 涉及**工具組合**,流程非 trivial

### ✗ 不寫 skill

- 一次性任務(直接在對話講清楚就好)
- 內容只是「Claude 本來就會的」(寫了沒用)
- 內容是「當前狀態」(state 會變,別 bake 進 skill)
- 超過 5 頁(太大的話拆 skill,或改成 subagent)

---

## Skill vs CLAUDE.md vs Memory 如何分工

三個都是「告訴 Claude 背景」的機制,但有分工:

| 機制 | 範圍 | 何時載入 |
|---|---|---|
| `CLAUDE.md` | 整個 repo / 全域 | 每次 session 開始就載入 |
| Memory(auto-memory) | 使用者跨 session | 每次 session 自動載入 |
| Skills | 特定任務 | 任務相關時 on-demand 載入 |

**心法**:

- **永遠相關的** → CLAUDE.md(但控制大小)
- **使用者個人,跨 project** → memory
- **特定任務的 know-how** → skill

把 skill 內容塞 CLAUDE.md 會讓 CLAUDE.md 膨脹,每個 session 都付錢。把 CLAUDE.md 內容做成 skill 會讓 Claude 有時沒讀到。**分清楚界線**。

---

## Supporting Files

Skill 目錄下可以放其他檔案:

```
~/.claude/skills/pr-review/
├── SKILL.md
├── checklist.md
├── template.md
└── examples/
    ├── good-review.md
    └── bad-review.md
```

SKILL.md 引用:

```markdown
Use the checklist in @checklist.md.
Template for the final comment: @template.md
Reference good / bad examples in @examples/
```

Claude 觸發 skill 時會跟著把這些檔讀進來。

**何時用 supporting files**:

- 主 SKILL.md 會太長
- 有可重用的 template / checklist
- 有 few-shot examples

---

## Shell Injection

`` !`command` `` 會被執行,輸出 inline 進 skill:

```markdown
---
name: dev-context
description: Load current dev environment state when debugging.
---

Current branch:
!`git branch --show-current`

Recent commits:
!`git log --oneline -5`

Running processes:
!`ps aux | grep -E "(python|node)" | head -5`
```

每次 skill 被觸發時重算。

**有用場景**:

- Inject 當前狀態(branch、time、env vars)
- Dynamic context 避免 stale

**注意**:
- Shell 會 block,command 慢 → skill 慢
- Command 失敗要考慮 fallback

---

## 測試 Skill

### 1. Manual invoke

強制呼叫測試:

```
/prepare-commit
```

看 Claude 行為是否如預期。

### 2. 情境測試

不直接呼叫,讓 Claude 自己判斷該不該用:

```
> I want to commit my changes
```

看 Claude 是否**自動啟動**這 skill。沒啟動 = description 寫得不夠觸發。

### 3. 反例測試

```
> Read src/foo.py
```

看 Claude 有**沒有誤觸發** commit 相關 skill(該不觸發的情境)。誤觸發 = description 太寬。

---

## 反例:過度設計的 skill

### 反例 1:一個 skill 做十件事

```markdown
---
name: pr-workflow
---

This skill handles:
1. Create a branch
2. Make changes
3. Run tests
4. Format code
5. Commit
6. Push
7. Open PR
8. Add reviewers
9. Poll for approval
10. Merge
```

太大,觸發時 Claude 要全背。**拆成多個小 skill**,或該用 subagent 分層。

### 反例 2:過於 defensive

```markdown
If the user asks about commits, but not really about commits, or is asking about
something else that sounds like commits, don't use this skill. Unless they do mean
commits, in which case use it. But if in doubt, confirm first...
```

Claude 會被繞暈。Description 寫清楚,邊界 case 交給 model 判斷。

### 反例 3:把 prompt engineering 黑魔法全塞進去

```markdown
You are a 10x engineer. You breathe deeply. You think step by step.
Take your time. You will be tipped $500 if correct. Be confident.
```

現代 Claude 不 buy 這些招式。Skill 裡**只寫 task-specific 的東西**。

---

## 社群共享的 skill 模式

Team 想共享 skill 怎麼辦?

### Option 1:放 repo 裡

```
.claude/skills/
├── our-conventions.md
├── pr-review.md
└── ...
```

Commit 進去,同事 clone repo 後自動有。**推薦的 default 模式**。

### Option 2:Plugin

Plugin 是 Claude Code 的 bundle 機制,可以一次 bundle skills + commands + MCP config。

```
my-plugin/
├── plugin.json
├── skills/
├── agents/
└── mcp-servers.json
```

發布 plugin(plugin marketplace 或自己提供 URL),用戶一個指令裝好所有東西。適合:

- 公司內部工具標準化
- 發布給社群

---

## Best Practices 整理

1. **Description 具體 + 有觸發條件**
2. **Body 祈使句 + 結構化 + 有例子**
3. **一個 skill 一件事**,拆得細一點
4. **用 supporting files** 把大 skill 拆
5. **不要寫 Claude 本來就會的**
6. **定期 review**:skill 用了幾次?有沒有誤觸發?
7. **版本控制**:skills 放 repo,跟 code 一起 review

---

## 範例:真實專案的 skill 集

假設一個 FastAPI 後端專案的 `.claude/skills/`:

- `testing.md` — 測試怎麼跑、怎麼寫
- `migrations.md` — alembic 使用規範
- `api-design.md` — endpoint 風格、response schema
- `commit-format.md` — conventional commits
- `pr-review.md` — review 該看什麼
- `security-check.md` — 提交前檢查(secrets、injection)

+ `CLAUDE.md` 放最 top-level 的東西(技術棧、run 指令)
+ Agents 放 subagent(例如 security-reviewer)

這組配置讓新人 clone repo + `claude` 就得到完整 AI 協作環境。

---

## 自我檢核

- [ ] Skill 的 description 一定要有什麼三要素?
- [ ] Skill body 為什麼用祈使句?
- [ ] Skill vs CLAUDE.md vs memory 的分工?
- [ ] Shell injection 的用途和風險?
- [ ] 列三個該拆小 skill 的訊號。

→ [Ch 16 Claude Agent SDK 基礎](./16-agent-sdk.md)
