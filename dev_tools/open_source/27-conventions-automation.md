# Ch 27 — commit/PR 規範自動化

> **目標**：把團隊的協作規範從「靠人記得遵守」變成「自動強制/自動完成」。掌握 Conventional Commits（結構化 commit message）、commitlint（檢查 commit 格式）、pre-commit hooks（commit 前自動跑檢查）、以及這些怎麼串起自動 changelog / 版本號。學完你能讓「規範」自動發生，而不是靠 review 一次次手動抓。

> **環境**：git 2.40+、Node/Python（hook 工具）、GitHub。前置：Ch 2（commit message）、Ch 14（CI）。

## 為什麼規範要自動化

團隊有一堆規範：commit message 格式、code style、push 前要跑測試、PR 標題要符合某格式…… 如果這些**靠人記得遵守 + reviewer 手動抓**，會出現：

- 有人忘了跑 formatter，PR 一堆格式問題，reviewer 浪費時間挑。
- commit message 各寫各的，沒法自動生成 changelog。
- 同樣的低級錯誤（trailing whitespace、console.log 沒刪）一再被 review 抓。

**自動化**把這些變成「機器自動做/自動擋」：commit 前自動格式化、commit message 格式不對自動拒絕、PR 標題不符自動標紅。這讓 reviewer 能專注在真正重要的（設計、邏輯），而不是當人肉 linter。對協作的價值巨大——它把「規範」從社會壓力變成系統保證。

## 先建立直覺：三道自動防線

規範自動化在三個時機介入：

```
   你寫 code
        │
   ① commit 時（本機）：pre-commit hook
        │  自動格式化、跑 linter、擋壞 commit message
        ▼
   你 push
        │
   ② push 時（本機）：pre-push hook（選用）
        │  跑測試
        ▼
   開 PR / CI
        │
   ③ CI（遠端）：lint/test/commit 格式檢查 + PR 標題檢查
        │  最後一道，本機繞過了 CI 還是擋
        ▼
   merge
```

本機 hook（①②）是「即時、快速、繞得過」的第一道；CI（③）是「權威、繞不過」的最後一道。兩者配合：hook 給你即時回饋（commit 當下就修），CI 確保「就算有人沒裝 hook，也擋得住」。

## Conventional Commits：結構化的 commit message

承 Ch 2 的好 commit message，**Conventional Commits** 是一個廣泛採用的**格式規範**——讓 commit message 不只給人讀，還能**被機器解析**（自動生成 changelog、決定版本號）：

```
<type>[optional scope]: <description>

[optional body]

[optional footer]
```

type 是關鍵——它分類這個 commit 是什麼：

```
feat:     新功能（觸發 minor 版本號 +1）
fix:      bug 修復（觸發 patch 版本號 +1）
docs:     文件
style:    格式（不影響邏輯）
refactor: 重構（不改行為）
test:     測試
chore:    雜務（build、依賴）
perf:     效能
ci:       CI 設定

# breaking change（觸發 major 版本號 +1）：
feat!: ...
# 或 footer 加 BREAKING CHANGE: ...
```

實例：

```
feat(auth): add password reset flow

Users can now reset their password via email link.

Closes #123
```

```
fix(api): handle null token in login

BREAKING CHANGE: login() now throws AuthError instead of returning null.
```

為什麼這個格式有價值：

- **自動 changelog**：工具掃 commit 的 type，自動生成「Features / Bug Fixes / ...」分類的 changelog（Ch 32）。
- **自動版本號**：`fix:` → patch、`feat:` → minor、`feat!:`/`BREAKING CHANGE` → major，工具自動算 semver（Ch 32）。
- **一眼分類**：`git log` 看 type 就知道每個 commit 是什麼。

> 認識論誠實：Conventional Commits 不是普世必要——很多優秀專案不用它（Linux kernel 有自己的格式）。它的價值在「自動化 changelog/版本」，所以**用 squash merge + 自動發布**的專案特別愛用（PR 標題就是 squash commit message，符合格式就能自動生成 release）。不是所有專案都需要。看專案 CONTRIBUTING——它要求的話就遵守，沒要求別硬套。

## pre-commit hooks：commit 前自動把關

git 有 **hooks**（在特定 git 操作時自動執行的腳本）。**pre-commit hook** 在你 `git commit` 時自動跑——可以自動格式化、跑 linter、擋壞 commit：

```
   git commit
        │
   pre-commit hook 自動跑：
   ├─ 格式化（prettier / black / gofmt）→ 自動修好格式
   ├─ linter（eslint / flake8）→ 有問題就擋住 commit
   ├─ 檢查（沒留 console.log / debugger / 大檔案 / secret）
   └─ 全過 → commit 成功；任一失敗 → commit 被擋，你先修
```

最流行的工具是 **pre-commit**（框架，Python 寫但語言無關）：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace      # 自動刪行尾空白
      - id: end-of-file-fixer
      - id: check-added-large-files  # 擋太大的檔案
      - id: detect-private-key       # 擋誤提交私鑰（呼應 Ch 0/34）
  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black                    # 自動格式化 Python
```

```bash
pip install pre-commit
pre-commit install                   # 裝進這個 repo 的 git hook
# 之後每次 git commit 自動跑這些檢查
```

裝了之後，commit 時自動格式化、自動擋壞東西——你根本沒機會 commit 不符規範的 code。對團隊：每個人 `pre-commit install` 後，大家的 commit 都自動符合規範，reviewer 不用再挑格式。

> hooks 是本機的、可繞過（`git commit --no-verify` 跳過、或沒裝 hook）。所以 **hook 是「即時方便」的第一道，但不能當唯一防線**——CI 要再檢查一次（繞不過）。hook + CI 雙保險：hook 給即時回饋、CI 兜底。

## commitlint：檢查 commit message 格式

要強制 Conventional Commits 格式，用 **commitlint** + **commit-msg hook**：

```bash
# 裝 commitlint（Node 生態）
npm install --save-dev @commitlint/cli @commitlint/config-conventional
# 配一個 commit-msg hook（用 husky 或 pre-commit framework）
```

```js
// commitlint.config.js
module.exports = { extends: ['@commitlint/config-conventional'] };
```

之後 commit message 不符 Conventional Commits 格式（如忘了 type、type 拼錯）就被擋：

```bash
$ git commit -m "fixed the bug"
⧗   input: fixed the bug
✖   subject may not be empty / type may not be empty
✖   found 2 problems
# commit 被擋，要你改成 "fix: ..."
```

CI 端也可以跑 commitlint（檢查 PR 的所有 commit、或 PR 標題）——本機繞過了 CI 還是擋。

## PR 標題 / 其他自動化

除了 commit，還有一堆能自動化的：

- **PR 標題檢查**：用 GitHub Action 檢查 PR 標題符合 Conventional Commits（squash merge 時 PR 標題=commit message，所以要檢查）。
- **PR 大小標籤**：自動給 PR 加 `size/S`、`size/L` 標籤（提醒大 PR，Ch 26）。
- **自動 label**：依改的檔案路徑自動加 label（`labeler` action）。
- **自動指派 reviewer**：CODEOWNERS（Ch 24）或 round-robin。
- **format check in CI**：CI 跑 `prettier --check` / `black --check`，格式不對標紅。

這些都是 GitHub Actions（[cicd 課](../cicd/README.md) 教寫），把「規範」搬到自動執行。

## 一個完整的規範自動化堆疊

一個正經團隊 repo 的規範自動化（綜合本章）：

```
   本機（即時）：
   .pre-commit-config.yaml:
   - 格式化（black/prettier）
   - linter（flake8/eslint）
   - 檢查（trailing whitespace、large files、private keys）
   - commitlint（Conventional Commits 格式）

   CI（兜底，繞不過）：
   - lint + format check（本機繞過了這裡擋）
   - test
   - commitlint（檢查所有 commit / PR 標題）
   - PR 標題格式檢查

   結果：
   - 進 main 的 code 一定符合格式
   - commit message 一定是 Conventional Commits
   → 可自動生成 changelog + 版本號（Ch 32）
   → reviewer 專注設計，不用當人肉 linter
```

## 踩雷集錦

1. **規範只靠人記得 + review 手動抓**：低效、reviewer 浪費時間挑格式。自動化（hook + CI）。
2. **只有本機 hook 沒有 CI**：hook 可繞過（`--no-verify`、沒裝）。CI 要兜底（繞不過）。
3. **硬套 Conventional Commits 到不需要的專案**：它的價值在自動 changelog/版本。專案不用自動發布就沒必要。看 CONTRIBUTING。
4. **pre-commit hook 太慢**：commit 時跑一堆重檢查（如全測試）讓 commit 卡很久——hook 放快的（格式、lint），重的（測試）放 pre-push 或 CI。
5. **hook 沒提交到 repo**：`.pre-commit-config.yaml` / commitlint config 要進版控，團隊才共享同一套。但 hook 本身要每人 `pre-commit install`（或用會自動裝的機制）。
6. **`--no-verify` 養成習慣**：偶爾 emergency 繞過 hook 可以，但養成習慣繞過就失去意義。CI 兜底就是防這個。
7. **commitlint 擋住卻不知怎麼改**：看它的錯誤訊息（缺 type、格式錯），改成 `type: description` 格式。

## 進階：再往深一層

- **git hooks 的種類**：pre-commit、commit-msg、pre-push、post-merge 等——不同時機觸發。`.git/hooks/` 是本機的，工具（pre-commit/husky）幫你管理與分享。
- **husky**（Node 生態）：管理 git hooks 的流行工具，把 hook 設定進版控、`npm install` 時自動裝。
- **semantic-release / release-please**（Ch 32）：讀 Conventional Commits 自動算版本號、生成 changelog、發 release——規範自動化的終極回報。
- **lint-staged**：只對「這次要 commit 的檔案」跑 linter/formatter（不是全專案），快很多。配 pre-commit。
- **secret 掃描**：`detect-secrets`、gitleaks、GitHub 的 secret scanning——commit/push 前擋住誤提交的 API key/密碼（呼應 Ch 0/34/36）。
- **commitizen**：互動式幫你寫 Conventional Commits（問你 type/scope/description，產生格式正確的 message）——不用記格式。
- **規範的彈性**：自動化是為了省力，不是為了僵化。規則太嚴（連 WIP commit 都擋）會煩——本機開發可寬鬆（squash 時再整理，Ch 7），進 main 才嚴格。

## 動手練習

1. 在一個測試 repo 裝 pre-commit framework，配 `.pre-commit-config.yaml`（trailing-whitespace + 一個 formatter），`pre-commit install`，故意 commit 一個有行尾空白的檔案，看它自動修/擋。
2. 試 `git commit --no-verify` 繞過 hook——理解「hook 可繞過，所以要 CI 兜底」。
3. 寫幾個 Conventional Commits 格式的 message（feat/fix/docs，含一個 breaking change）。
4. 裝 commitlint + commit-msg hook，故意 commit 一個不符格式的 message，看它被擋。
5. 看一個用 Conventional Commits 的開源專案（很多），看它的 commit log 和自動生成的 changelog/release notes，理解格式怎麼變成 changelog（Ch 32 預習）。
6. 為一個（假想）團隊設計規範自動化堆疊：哪些放 pre-commit、哪些放 CI，為什麼。

## 本章重點整理

- 規範自動化把「靠人記得 + review 手動抓」變成「機器自動做/擋」——reviewer 專注設計，不當人肉 linter。
- 三道防線：pre-commit hook（本機即時、可繞過）→ pre-push（選用）→ CI（權威、繞不過）。hook + CI 雙保險。
- Conventional Commits：結構化 commit message（`type: description`），讓機器能解析 → 自動 changelog + 版本號；用 squash merge + 自動發布的專案特別受益（非普世必要）。
- pre-commit hooks（pre-commit framework / husky）：commit 時自動格式化、lint、擋壞東西、擋 secret。
- commitlint + commit-msg hook：強制 commit message 格式；PR 標題檢查、auto-label 等是 CI 端自動化。
- hook 可繞過（`--no-verify`），所以 CI 要兜底；config 進版控讓團隊共享。

## 自我檢核

- [ ] 為什麼規範要自動化，而不是靠 review 手動抓？
- [ ] 本機 hook 和 CI 各是哪道防線？為什麼不能只有 hook？
- [ ] Conventional Commits 的價值是什麼？什麼專案特別需要它、什麼專案不需要？
- [ ] pre-commit hook 該放什麼檢查、不該放什麼（提示：速度）？
- [ ] commit message 格式要強制，靠什麼工具 + 什麼 hook？

## 延伸閱讀

### 規範 / 工具

- **[Conventional Commits](https://www.conventionalcommits.org/)**
  - **讀哪裡**：規範本身（很短）+ FAQ。
  - **和本章的關聯**：格式的權威定義；Ch 32 自動發布的基礎。

- **[pre-commit framework](https://pre-commit.com/)**
  - **讀哪裡**：Quick start + 可用 hooks。
  - **和本章的關聯**：本章 pre-commit hook 的主力工具。

- **[commitlint](https://commitlint.js.org/)**
  - **讀哪裡**：Getting started。
  - **和本章的關聯**：強制 commit message 格式。

### 部落格 / 文章

- **[Automating your workflow with git hooks](https://blog.github.com/)** 類 GitHub/工程部落格
  - **這篇說什麼**：git hooks 自動化團隊規範的實務。
  - **為什麼值得讀**：把 hook + CI 的組合放進真實團隊流程。

Part 5 的團隊協作機制都齊了。用練習 E 把它們綜合起來：把一個 repo 設定成有完整協作規則的團隊專案——保護分支、CODEOWNERS、CI、規範自動化。

→ [練習 E：設定完整團隊協作規則](./practice-e-team-rules.md)
