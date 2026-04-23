# Ch17: GitHub PR Workflow

整合前面所學的完整流程。以 GitHub PR 為中心。

## 17.1 兩種模式

### 模式 A：Branch-based（同 repo）
你有 push 權限：
```
origin (真 repo)
  └─ main
  └─ feature/xxx   ← 你推的
```

### 模式 B：Fork-based（常見於 open source）
你沒 push 權限：
```
upstream (原 repo)      origin (你的 fork)
  └─ main         ←     └─ main
                        └─ feature/xxx   ← 你推這
```

GitHub PR 可以跨 repo（從 fork 開 PR 到 upstream）。

## 17.2 Branch-based 完整流程

### 起始
```bash
# 同步 main
git switch main
git pull
```

### 開 branch
```bash
git switch -c feature/add-auth
```

### 開發 + commit
```bash
# ... 改 code ...
git add -p
git commit -m "..."
git commit -m "..."
```

### Push + 開 PR
```bash
git push -u origin feature/add-auth     # 第一次 push，設 tracking
# 或開了 push.autoSetupRemote
git push

# 用 gh CLI 開 PR
gh pr create
# 或用 web UI
```

### 期間 main 有更新
```bash
git fetch origin
git rebase origin/main
# 解衝突（如果有）
git push --force-with-lease
```

### Review 回饋
```bash
# 改 code
git add -p
git commit --fixup=<commit-hash>      # 或一般 commit
git push
```

### 合併前清理
```bash
git rebase -i --autosquash origin/main
# 整理成 atomic commits
git push --force-with-lease
```

### Merge
- GitHub UI 按 Merge
- 或 `gh pr merge --squash` / `--rebase` / `--merge`

### Cleanup
```bash
git switch main
git pull
git branch -d feature/add-auth
# remote branch 通常 GitHub 自動刪（合併時勾 "Automatically delete head branches"）
```

## 17.3 Fork-based 流程

### 一次性設定
```bash
# 1. Fork 在 GitHub
# 2. Clone 你的 fork
git clone git@github.com:yourname/repo.git
cd repo

# 3. 加 upstream
git remote add upstream git@github.com:original-owner/repo.git
git remote -v
```

### 日常同步 upstream
```bash
git fetch upstream
git switch main
git rebase upstream/main
git push          # 更新自己的 fork main
```

### 開發
```bash
git switch -c feature/xxx
# ... 改 ...
git commit -m "..."
git push -u origin feature/xxx
```

### 開 PR（從 fork → upstream）
```bash
gh pr create --base main --head yourname:feature/xxx
# 或 web UI
```

### Upstream main 更新怎麼辦
```bash
git fetch upstream
git switch feature/xxx
git rebase upstream/main
git push --force-with-lease
```

## 17.4 `gh` CLI 常用

```bash
gh pr create                          # 開 PR（互動）
gh pr create --title "..." --body "..."
gh pr create --draft                  # Draft PR
gh pr create --fill                   # 用 commit 訊息填

gh pr list                            # 看 PR
gh pr list --author @me
gh pr list --state all

gh pr view 123                        # 看 PR #123
gh pr view 123 --web                  # 瀏覽器開

gh pr checkout 123                    # checkout 別人的 PR 到本地試
gh pr diff 123
gh pr comment 123 --body "LGTM"
gh pr review 123 --approve
gh pr review 123 --request-changes --body "..."

gh pr merge 123 --squash
gh pr merge 123 --rebase
gh pr merge 123 --merge

gh pr close 123
gh pr reopen 123
```

### Review 別人 PR
```bash
gh pr checkout 123         # 本地 checkout
# 跑 test、看 code
gh pr review 123 --approve
```

## 17.5 PR 的 Merge 策略

GitHub 設定裡可以選允許哪些：

### Create a merge commit
保留 PR 所有 commit + 加 merge commit：
```
main: --- A - B -------- M
               \       /
                C - D (feature)
```

訊息：`Merge pull request #123 from ...`

### Squash and merge
PR 所有 commit 壓成一個進 main：
```
main: --- A - B - S
                  ↑
              S = C+D 合成
```

訊息：PR 標題 + 你寫的說明。

### Rebase and merge
PR commit 逐個接到 main（無 merge commit）：
```
main: --- A - B - C' - D'
```

### 什麼時候用哪個

| 情境 | 選 |
|---|---|
| PR 內部已是 atomic 的幾個 commit | Rebase and merge |
| PR 有一堆 WIP commit 懶得整理 | Squash and merge |
| 要保留「這是一個 PR」的資訊 | Create merge commit |
| 團隊有統一規範 | 跟規範 |

## 17.6 常見團隊規範

### 規範 A：PR 合併前 squash
所有 PR merge 時 squash，main 歷史每個 commit 都是一個 PR。
- 優：簡單、乾淨
- 缺：細節丟了

### 規範 B：強制 atomic commits + Rebase
PR 裡每個 commit 都要 atomic，merge 用 rebase。
- 優：細節保留、blame 精準
- 缺：reviewer 要懂拆分 commit

### 規範 C：小 PR（Trunk-based）
限制 PR 大小（例如 < 400 行）、快速 merge。
- 優：review 快、衝突少
- 缺：拆 feature 要功夫

## 17.7 Draft PR

「還沒 ready 但想要 CI 跑 / 要早期回饋」：
```bash
gh pr create --draft
```

Draft PR 上面掛 "Draft" 標誌，**通常不能 merge**（除非明確改成 ready）。

Ready：
```bash
gh pr ready
```

## 17.8 PR 訊息 template

repo 裡放 `.github/pull_request_template.md`：
```markdown
## Summary
<!-- 一兩行說這 PR 做什麼、為什麼 -->

## Test plan
- [ ] 跑過 unit test
- [ ] 本地起服務確認
- [ ] ...

## Related issues
Closes #
```

`gh pr create` 會帶進去。好 template 省掉反覆溝通。

## 17.9 CODEOWNERS

`.github/CODEOWNERS`：
```
# Format: <pattern> <owner>
*.py           @alice @bob
/src/auth/     @security-team
```

有改動路徑的 owner 自動被 request review。防止「誰應該 review」的困惑。

## 17.10 Branch protection rules

**一定要開**（至少 main branch）：

GitHub Repo Settings → Branches → Branch protection rules：
- ✅ Require pull request before merging
- ✅ Require approvals（至少 1）
- ✅ Dismiss stale approvals when new commits pushed
- ✅ Require status checks（CI 必須 pass）
- ✅ Require branches to be up to date（PR 要同步 main）
- ✅ Require conversation resolution before merging
- ✅ Require linear history（強制 rebase/squash）— 可選
- ❌ Allow force pushes（禁用，main 絕不可 force）
- ❌ Allow deletions（禁）

## 17.11 Keep PRs up-to-date with main

### 情境：PR 開了三天，main 動了
**選項 1：Rebase（推薦）**
```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```
歷史乾淨、衝突一次解。

**選項 2：Merge main into feature**
```bash
git fetch origin
git merge origin/main
git push
```
PR 會出現一個「Merge branch 'main' into feature」的醜 commit。**不推薦**。

**選項 3：GitHub 的 "Update branch" 按鈕**
就是選項 2 的 UI 版。會產生 merge commit。有些 repo 禁。

## 17.12 解決衝突（PR 層面）

GitHub 說 "This branch has conflicts"：
```bash
git fetch origin
git rebase origin/main
# 解衝突（Ch18）
git add ...
git rebase --continue
git push --force-with-lease
```

## 17.13 Issue 關聯

Commit message 或 PR 訊息寫：
```
Fixes #123
Closes #456
Resolves #789
```

Merge 後 issue 自動關。

## 17.14 `gh` 隱藏絕活

```bash
# 看 CI 狀態
gh pr checks

# 看所有變更檔
gh pr diff

# 直接開瀏覽器
gh pr view --web

# 當前 branch 對應的 PR
gh pr view

# 列出所有需要你 review 的 PR
gh pr list --search "review-requested:@me"

# 把 PR checkout 到本地試 code
gh pr checkout 456
# 跑 test、給 review、切走
git switch -
```

## 17.15 PR 大小控制

研究顯示：**PR 超過 400 行，review 品質開始下滑**。

拆 PR 的技巧：
1. **基礎設施先行**：先一個 PR 加新的依賴/util/config
2. **改動後加功能**：後續 PR 加功能
3. **一個概念一個 PR**：不要混功能和格式化
4. **提取重構為獨立 PR**：重構和功能改動分開

## 17.16 PR stack（進階）

一個大 feature 拆成多個 PR，彼此 base 在前一個上：
```
main ← PR1 ← PR2 ← PR3
```

工具：
- `gh` 原生不支援太好
- **Graphite** / **ghstack** / **spr** 等第三方工具
- 手動做：PR2 的 base branch 設成 PR1 的 branch

Google / Meta 重度使用，GitHub 原生還在演進中。

## 17.17 典型問題

### Q: 我的 PR 要不要每個 commit 都 green CI？
看專案。嚴格的用 `rebase -i --exec "make test"` 保證每個 commit 能過。大多數 squash-merge 的專案只看最終狀態。

### Q: Force push 會不會破壞 review？
現代 GitHub review **不會**失效（評論仍掛在檔案行上）。但 reviewer 可能要重新看新 diff。**給 reviewer 留 comment 說你 force push 了什麼**。

### Q: Rebase vs merge main into feature？
如上，**rebase 乾淨**、merge 產生噪音 commit。只有不能 force push 的情況用 merge。

### Q: 一個 PR 多久 merge 合理？
理想 < 1 天。> 3 天就該切小。超過 1 週通常是 PR 太大。

## 17.18 練習

Sandbox（fork 一個自己的測試 repo 在 GitHub）：

1. 走一次完整 branch PR 流程：建 branch → commit → push → `gh pr create` → merge → 清理。
2. Fork 一個 open source 專案（小的），走 upstream 流程。
3. 練 `gh pr checkout` + review 自己的舊 PR。
4. 建一個 draft PR，修改後 `gh pr ready`。
5. 故意在 PR 期間讓 main 前進，練 rebase 同步。

## 本章重點
- Branch-based（同 repo）vs Fork-based（upstream + origin）兩種 workflow
- `gh` CLI 讓 PR 整合進 terminal 流程
- Merge 策略：Merge commit / Squash / Rebase
- **Branch protection rules 是必備**，保護 main
- PR 同步 main **用 rebase + force-with-lease**，不用 merge
- PR 大小控制 < 400 行，大的拆成 stack
- `.github/pull_request_template.md` 和 `CODEOWNERS` 建立團隊規範
