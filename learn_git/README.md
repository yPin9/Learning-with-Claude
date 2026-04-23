# learn_git

給「已經會 `git add/commit/push`、但想**真正熟練**」的人。不教「什麼是版本控制」，直接進真實世界會踩的坑。

## 前提
- 會命令列、裝過 git
- 用過 GitHub（至少發過一兩個 PR）
- 有被 merge conflict 嚇過、被「不小心 push 錯 branch」燒過

## 目標
讀完你會：
- **不怕 rebase、不怕丟 commit**（reflog 是你的朋友）
- 用 CLI 流暢做 interactive rebase、cherry-pick、bisect
- 寫 pre-commit / pre-push hook 不用外掛 framework
- 熟 worktree 一人多分支並行開發
- 看得懂 `.git/` 目錄、會解剖 object
- 在 GitHub PR 流程裡**寫出乾淨歷史**

**不深入**：packfile 格式細節、refspec 魔術、自訂 transport、server-side 架設。

## 環境
- **Windows + MSYS2 UCRT64 bash**（課程以此為準）
- WSL 也 OK
- Git 2.30+（`git config init.defaultBranch`、`switch`/`restore`、`maintenance` 這些都要）

## 章節地圖

### Part 1 — 心智模型
- [Ch0: 環境設定](00-environment-setup.md)
- [Ch1: Git 不是 SVN——snapshot 與 object graph](01-git-is-not-svn.md)
- [Ch2: 三大區——workdir / index / HEAD](02-three-areas.md)

### Part 2 — 日常命令的 power 用法
- [Ch3: log / diff 的真實用法](03-log-and-diff.md)
- [Ch4: branch / switch / restore（取代 checkout）](04-branch-switch-restore.md)
- [Ch5: fetch / pull / push 的真相](05-remote-fetch-pull-push.md)

### Part 3 — 寫歷史
- [Ch6: merge vs rebase](06-merge-vs-rebase.md)
- [Ch7: interactive rebase](07-interactive-rebase.md)
- [Ch8: amend / cherry-pick / revert](08-amend-cherrypick-revert.md)
- [Ch9: commit message 與 atomic commits](09-commit-messages-atomic.md)
- [Ch10: stash](10-stash.md)

### Part 4 — 災難救援
- [Ch11: reflog——人生救星](11-reflog.md)
- [Ch12: reset vs revert vs restore 全解](12-reset-revert-restore.md)
- [Ch13: 災難情境與救法](13-disaster-recovery.md)

### Part 5 — 底層（輕量）
- [Ch14: 四種 object](14-git-objects.md)
- [Ch15: .git/ 目錄探險](15-dot-git-directory.md)
- [Ch16: gc、fsck、maintenance](16-gc-and-maintenance.md)

### Part 6 — GitHub PR 協作
- [Ch17: GitHub PR workflow](17-github-pr-workflow.md)
- [Ch18: 衝突解決](18-conflict-resolution.md)
- [Ch19: submodule 的坑 vs subtree](19-submodule-vs-subtree.md)
- [Ch20: hooks](20-hooks.md)
- [Ch21: bisect](21-bisect.md)

### Part 7 — 進階
- [Ch22: worktree](22-worktree.md)
- [Ch23: 大檔案策略](23-large-files.md)
- [Ch24: signed commits](24-signed-commits.md)

### 練習
- [Practice A: 災難救援情境](practice-a-disaster-recovery.md)
- [Practice B: 把爛 commit 整理成乾淨 PR](practice-b-clean-commits.md)
- [Practice C: 手寫 pre-commit hook](practice-c-pre-commit-hook.md)
- [Final Project: bisect 自動化找 regression](final-project-bisect-regression.md)

## 學習順序建議

**快速路徑**（只想補強日常）：Ch0→Ch3→Ch4→Ch6→Ch7→Ch11→Ch17→Ch18→Ch20

**完整路徑**：按順序。Part 4（reflog）是心理安全感來源，早讀早放心。

## 風格

- 每章都有「**壞的做法 vs 好的做法**」對照
- 說清楚為什麼，不只列命令
- **誠實指出坑**——什麼時候該 force push、什麼時候不該
- 範例都用 bash，Windows 路徑會標註
