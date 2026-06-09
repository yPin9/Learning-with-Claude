# 練習 E — 設定完整團隊協作規則

> **目標**：把 Part 5（branching model、保護分支、CODEOWNERS、同步、PR 拆分、規範自動化）綜合起來，把一個 repo 設定成「準備好讓團隊協作」的狀態——保護分支 + CODEOWNERS + CI + 規範自動化。完成後你會具備「從零建立一個協作 repo 的基礎設施」的能力，這是團隊 lead / 維護者的核心技能（也是 Part 6 的前奏）。

> 前置：Part 5 全部（Ch 22-27）、Ch 14（CI）。

## 背景與動機

前面你都是「在別人設好的 repo 裡協作」。這個練習換你當「設定規則的人」——從零把一個 repo 武裝成能讓團隊安全協作的樣子。這是團隊 lead、開源維護者的核心技能：一個沒有保護規則、沒有 CI、沒有 CODEOWNERS 的 repo，協作起來處處是雷（有人直接推 main、PR 沒人審、格式各寫各的）。做完這個練習，你能把任何 repo 變成「協作友善」的，也為 Part 6（維護者視角）打底。

## 任務規格

### 你要建立的東西

把一個 repo（新建或現有的測試 repo）設定成完整的團隊協作 repo，包含：

1. **選定並文件化 branching model**（Ch 22）
2. **branch protection**（Ch 23）：保護 main
3. **CODEOWNERS**（Ch 24）：責任地圖
4. **CI**（Ch 14）：自動測試 + lint
5. **規範自動化**（Ch 27）：pre-commit hook + commit 格式
6. **協作文件**：CONTRIBUTING + PR/issue 範本

### 驗收標準

- [ ] repo 有 main，且 main 受 branch protection 保護（不能直接 push、要 PR + review + CI）
- [ ] 有 `.github/CODEOWNERS` 定義責任地圖
- [ ] 有 CI（GitHub Actions）跑測試 + lint，且設為 required check
- [ ] 有 `.pre-commit-config.yaml`（格式化 + 基本檢查 + 擋 secret）
- [ ] 有 CONTRIBUTING.md（說明流程、branching model、規範）
- [ ] 有 PR 範本 + issue 範本
- [ ] 用一個測試 PR 驗證：直接 push main 被擋、PR 要 CI 綠 + approve 才能 merge
- [ ] 寫一份「設計說明」：為什麼選這個 branching model、這套規則嚴格度為什麼適合（假想的）團隊規模

## 期望成果

一個任何人 clone 下來、開 PR 就會被引導走正規流程的 repo——直接推 main 被擋、commit 格式自動檢查、CI 自動跑、對的人自動被指派審查。一個「不可能不小心搞壞」的協作 repo。

## 如果你卡住了

1. **不知道選哪個 branching model？** 多數情況選 GitHub Flow（最簡單，Ch 22）。在 CONTRIBUTING 寫清楚即可。
2. **branch protection 設不了？** 需要 repo admin 權限（你自己的 repo 就有）。Settings → Branches / Rules。
3. **CI 怎麼寫？** 最簡單：一個 workflow 跑你的測試 + 一個 linter。本練習重點不是寫複雜 CI（那是 cicd 課），能跑就好——下面解答給範例。
4. **required check 名字對不上？** branch protection 裡選的 check 名要和 CI 的 job/step 名完全一致（Ch 23 踩雷）。
5. **CODEOWNERS 沒生效？** 確認檔案在 `.github/CODEOWNERS`、owner 是對 repo 有權限的 user/team、配了 `Require review from Code Owners`（Ch 24）。
6. **單人怎麼測 review？** 自己不能 approve 自己的 PR。可用分身帳號，或先把 approve 數設 0（只測 CI/protection），體驗其他規則。

## 實作步驟建議

### Step 1：建 repo + 選 branching model

建 repo、寫一個小程式 + 測試，在 CONTRIBUTING 寫明用 GitHub Flow（或你選的）。

### Step 2：CI（Ch 14）

加 GitHub Actions 跑測試 + lint。

### Step 3：branch protection（Ch 23）

保護 main：require PR + approval + CI（required check）+ 禁 force-push + include admins。

### Step 4：CODEOWNERS（Ch 24）

寫責任地圖，配 `Require review from Code Owners`。

### Step 5：規範自動化（Ch 27）

`.pre-commit-config.yaml`（格式化 + 檢查 + 擋 secret）+ （選）Conventional Commits。

### Step 6：協作文件

CONTRIBUTING + PR 範本 + issue 範本。

### Step 7：驗證

開測試 PR，確認規則生效（推 main 被擋、要 CI+approve）。

## 完整參考解答

**自己先設一遍，卡住再看。**

<details>
<summary>點開完整設定（含可用範例檔案）</summary>

### Step 1：repo + 程式 + branching model

```bash
mkdir team-repo && cd team-repo
git init && git switch -c main

mkdir src tests
cat > src/calc.py <<'EOF'
def add(a, b):
    return a + b
EOF
cat > tests/test_calc.py <<'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from calc import add

def test_add():
    assert add(2, 3) == 5
EOF
git add . && git commit -m "Initial commit"
gh repo create team-repo --public --source=. --push
```

### Step 2：CI（`.github/workflows/ci.yml`）

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.x' }
      - run: pip install pytest black flake8
      - run: black --check src tests          # format check
      - run: flake8 src tests --max-line-length=100   # lint
      - run: pytest tests/ -v                  # test
```

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add test, lint, format checks"
git push
# CI 會跑，記下 job 名稱「test」（branch protection 要用）
```

### Step 3：branch protection（Settings → Branches → Add rule，對 main）

或用 `gh api`（進階）。網頁設定勾選：

```
Branch name pattern: main
☑ Require a pull request before merging
    ☑ Require approvals: 1
    ☑ Dismiss stale pull request approvals when new commits are pushed
    ☑ Require review from Code Owners
☑ Require status checks to pass before merging
    ☑ Require branches to be up to date before merging
    選 required checks: test
☑ Require conversation resolution before merging
☑ Do not allow force pushes
☑ Do not allow deletions
☑ Do not allow bypassing the above settings (include administrators)
```

### Step 4：CODEOWNERS（`.github/CODEOWNERS`）

```
# 預設：所有檔案歸你（或團隊）
*               @你的帳號

# 範例：不同部分不同 owner（單人練習可都設自己）
/src/           @你的帳號
/tests/         @你的帳號
/.github/       @你的帳號
*.md            @你的帳號
```

```bash
git add .github/CODEOWNERS
git commit -m "chore: add CODEOWNERS"
git push   # 注意：此時 main 已保護，可能要走 PR（見驗證）
```

> 注意：Step 3 設了保護後，你就不能直接 push main 了！後續的 commit（CODEOWNERS、pre-commit config 等）要走 PR——這正好驗證保護生效。或在設保護「之前」先把這些檔案 push 好。

### Step 5：規範自動化（`.pre-commit-config.yaml`）

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
      - id: detect-private-key        # 擋誤提交私鑰（Ch 0/34）
      - id: check-merge-conflict      # 擋殘留的 conflict marker（Ch 8）
  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/flake8
    rev: 7.0.0
    hooks:
      - id: flake8
        args: ['--max-line-length=100']
```

```bash
pip install pre-commit
pre-commit install              # 裝進本機 git hook
# 之後 commit 自動跑這些；CI（Step 2）兜底
```

（選）Conventional Commits：加 commitlint + commit-msg hook（Ch 27），或在 CONTRIBUTING 要求格式。

### Step 6：協作文件

```markdown
<!-- CONTRIBUTING.md -->
# Contributing

## Branching model
We use **GitHub Flow**: branch off `main`, open a PR, get it reviewed
and CI-green, then squash-merge back to `main`.

## Workflow
1. `git switch -c feat/your-feature` (off latest main)
2. Make changes; `pre-commit install` runs format/lint on commit
3. Run tests: `pytest tests/`
4. Push and open a PR with a clear description, link issues with `Closes #N`
5. Address review, get CI green + 1 approval, squash-merge

## Conventions
- Commit messages: imperative mood; Conventional Commits preferred (feat:/fix:/...)
- Code style: enforced by black + flake8 (pre-commit + CI)
```

```markdown
<!-- .github/PULL_REQUEST_TEMPLATE.md -->
## What

## Why

## Testing

Closes #
```

```markdown
<!-- .github/ISSUE_TEMPLATE/bug.md -->
---
name: Bug Report
about: Report a bug
---
### Environment
### Steps to reproduce
### Expected
### Actual
```

### Step 7：驗證規則生效

```bash
# 1. 試直接 push main → 被擋
echo "x" >> src/calc.py
git commit -am "test direct push"
git push origin main
#   ! [remote rejected] main -> main (protected branch hook declined)  ← 保護生效！
git reset --hard origin/main    # 撤銷

# 2. 走正規流程
git switch -c test/verify-rules
echo "# comment" >> src/calc.py
git commit -am "test: verify protection rules"   # pre-commit 自動跑
git push -u origin test/verify-rules
gh pr create --fill
gh pr checks --watch            # CI 要綠
# 試 merge → 被擋（需要 1 approval + Code Owner review）
gh pr merge --squash            #   會說缺 approval
# （用分身 approve，或調設定後）→ 滿足條件才能 merge
```

**解答說明**：

這個練習建立了一個完整的協作基礎設施：
- **保護分支**（Step 3）：main 不能直接推、要 PR + CI + review——Ch 1 的「main 受保護」變系統強制。
- **CI**（Step 2）：自動 test + lint + format check，設為 required——壞東西進不了 main。
- **CODEOWNERS**（Step 4）：責任地圖 + 強制 owner review。
- **pre-commit + CI 雙防線**（Step 5）：本機即時格式化/擋 secret，CI 兜底。
- **協作文件**（Step 6）：CONTRIBUTING（規矩）+ 範本（引導貢獻者）——降低貢獻門檻、統一流程。

驗證（Step 7）確認規則真的生效——直接推 main 被擋、PR 要滿足條件才能 merge。這就是一個「協作友善、不可能不小心搞壞」的 repo。

注意 Step 3 之後 main 就鎖了，後續檔案要走 PR——這既是「驗證保護生效」，也讓你體會「設了保護後自己也要守規矩」（include administrators 的意義，Ch 23）。

</details>

## 檢查點

| 項目 | 檢查 |
|---|---|
| branching model | CONTRIBUTING 寫明 |
| branch protection | 直接 push main 被擋 |
| CI | test/lint/format 跑、設為 required |
| CODEOWNERS | 改檔案自動指派 owner |
| pre-commit | commit 時自動格式化/檢查 |
| 文件 | CONTRIBUTING + PR/issue 範本齊 |
| 驗證 | PR 要 CI 綠 + approve 才能 merge |

## 延伸挑戰（加分）

1. **Rulesets 版**：用較新的 Rulesets（Ch 23）取代 branch protection，體驗 pattern 套多 branch、org 層級規則。
2. **Conventional Commits + 自動 changelog**：加 commitlint 強制格式，配 release-please / semantic-release 自動生成 changelog 與版本（Ch 27/32 預習）。
3. **PR 標題檢查 action**：加一個 GitHub Action 檢查 PR 標題符合 Conventional Commits（squash merge 時 PR 標題=commit message）。
4. **auto-label / auto-assign**：加 labeler action（依檔案路徑自動 label）、自動指派 reviewer。
5. **多 branch 保護**：除了 main，加保護 `release/*`（模擬 Git Flow 的 release branch，Ch 22）。
6. **完整 maintainer infra**：再加 LICENSE、CoC、SECURITY.md、Dependabot（這些是 Part 6 練習 F 的內容，可先預做）。

## 自我檢核

- [ ] 我能從零把一個 repo 設定成「協作友善、不可能不小心搞壞 main」的狀態
- [ ] 我理解 branch protection + CI（required）+ CODEOWNERS 怎麼配合強制協作流程
- [ ] 我設了 pre-commit + CI 雙防線（本機即時 + 遠端兜底）
- [ ] 我寫了引導貢獻者的協作文件（CONTRIBUTING + 範本）
- [ ] 我能說出為什麼這套規則的嚴格度適合（假想的）團隊規模

Part 5 完成——你不只會在團隊裡協作，還會設定團隊協作的規則。Part 6 正式切換到**維護者視角**：當你是專案的 owner，怎麼審別人的 PR、管理 issue、做 release、經營社群、處理安全問題。

→ [Ch 28 從貢獻者到維護者](./28-becoming-maintainer.md)
