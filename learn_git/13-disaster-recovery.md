# Ch13: 災難情境與救法

七個真實世界會遇到的場景，一個一個拆解。

## 13.1 情境一：`git reset --hard` 後悔

```bash
git reset --hard HEAD~5   # 丟了 5 個 commit
```

**救法**：
```bash
git reflog
# 找到 reset 前的 HEAD
git reset --hard HEAD@{1}
# 或用 ORIG_HEAD 捷徑
git reset --hard ORIG_HEAD
```

## 13.2 情境二：誤刪本地 branch

```bash
git branch -D important-feature    # 刪了
```

**救法**：
```bash
git reflog | grep important-feature
# 找到 "checkout: moving from important-feature to main" 那行
# 其 hash 就是 branch 被刪時的 tip
git branch important-feature <hash>
```

或：
```bash
git fsck --lost-found
git show <orphan-commit>    # 確認是不是要的
git branch important-feature <orphan-commit>
```

## 13.3 情境三：`rebase` 搞砸

```bash
git rebase -i HEAD~10
# 解衝突解錯了、drop 錯 commit、或中途腦袋當機
```

**救法**：
```bash
# 還在 rebase 中
git rebase --abort

# 已經完成 rebase
git reflog
# 找 "rebase -i (start)" 或 "rebase (start)"
# 前一個 entry 是 rebase 開始前的 HEAD
git reset --hard HEAD@{N}
```

## 13.4 情境四：誤 `push --force` 覆蓋 remote

你 force push 了，發現覆蓋掉同事的 commit。

**救法**：
```bash
# 你本地的 reflog 沒有同事的 commit
# 但 GitHub 的 Activity 有 push 記錄（event 裡有 "before" sha）
# 讓同事找：他們本地還有 pre-push 的 reflog / commits
# 或 GitHub Events API 找

# 找到 hash 後
git fetch
git cherry-pick <missing-commits>
git push --force-with-lease
```

**預防**：
- 永遠 `--force-with-lease`（會拒絕這種情況）
- GitHub branch protection → disable force push on main

## 13.5 情境五：`amend` 後發現不對

```bash
git commit --amend -m "changed my mind"
# 想回 amend 前的訊息
```

**救法**：
```bash
git reflog
# abc1234 HEAD@{0}: commit (amend): changed my mind
# def5678 HEAD@{1}: commit: original message  ← 要這個
git reset --soft HEAD@{1}
# 現在 HEAD 退到 amend 前；改動還在 index
# 可以重新 commit
```

## 13.6 情境六：誤 `stash drop` 或 `stash clear`

```bash
git stash drop stash@{0}     # 刪掉了
```

**救法**：
```bash
git fsck --unreachable | grep commit
# 看每個 unreachable commit 的內容
git show <hash>
# 是你要的 stash 的 "workdir commit"
git stash apply <hash>
```

Stash 的 commit 通常有特殊訊息（"WIP on branch: ..."），好認。

## 13.7 情境七：誤 commit 到錯的 branch

「我在 main 上改東西 commit 了，其實該在 feature」。

**救法**：
```bash
# 假設你在 main 上 commit 了 3 個 commit，這些該在 feature
git branch feature                # 建 feature 指當前（含那 3 個 commit）
git reset --hard HEAD~3           # main 退回去 3 個 commit
git switch feature                # 切到 feature（那 3 個 commit 在這了）
```

如果 main 已經 push：
```bash
# main 本地退回後，reflog 有舊狀態
# 但 push 主幹 force 是大忌
# 用 revert 在 main 抵銷那 3 個
git switch main
git revert HEAD~2..HEAD
git push

# feature 照上面做
```

## 13.8 情境八：commit 了 secret（API key、password）

**立即**：
1. **假設 secret 已洩漏**——趕快到該服務 revoke 並換新
2. **清掉 repo 歷史**

### 最新 commit
```bash
git rm --cached secret.txt
echo "secret.txt" >> .gitignore
git add .gitignore
git commit --amend --no-edit
git push --force-with-lease    # 如果已 push
```

### 歷史中（嚴重）
用 `git-filter-repo`（推薦）或 BFG Repo-Cleaner：

```bash
# 裝 git-filter-repo
pacman -S python-pip
pip install git-filter-repo

# 從整個歷史刪除某檔
git filter-repo --path secret.txt --invert-paths

# 或 replace 某字串
git filter-repo --replace-text <(echo "API_KEY_123==>REDACTED")

git push --force-with-lease --all
```

⚠️ 這會**改所有 commit 的 hash**——所有人要重新 clone。

⚠️ GitHub 的 forks / cache 可能還有舊版——聯絡 GitHub support 強制清。

⚠️ **最重要**：secret 已經在 logs、CI、mirror 上，永遠當它已外洩，立刻 revoke。

## 13.9 情境九：搞不清楚狀態，想「reset 到最乾淨」

「我不知道我在做什麼，想完全同步 origin/main、什麼本地改動都不要」。

```bash
git fetch --all --prune
git switch main
git reset --hard origin/main
git clean -fd           # 清 untracked 檔
# ⚠️ 連 .env 之類也會沒
```

保險版：
```bash
git tag emergency-backup    # 先存個標記
# 然後做上面
# 如果要找回：git log emergency-backup
```

## 13.10 情境十：誤 `git rm -rf` 檔

```bash
git rm important_file.txt
git commit -m "clean up"
```

**救法**：
```bash
git log --all -- important_file.txt   # 找 commit 刪它之前
git checkout <commit-before-delete> -- important_file.txt
git add important_file.txt
git commit -m "Restore important file"
```

## 13.11 情境十一：Detached HEAD 上做了一堆 commit

```bash
git switch abc1234      # detached HEAD
# ... commit 很多 ...
git switch main         # ⚠️ detached 的 commit 變 orphan
```

**救法**：
- **切走前**：`git switch -c rescue-branch` 留下來
- **已切走**：
  ```bash
  git reflog
  # 找 detached 上的 commit
  git branch rescue <hash>
  ```

## 13.12 情境十二：merge 到一半很亂

```bash
git merge feature
# 衝突滿天、解一半頭暈
```

**救法**：
```bash
git merge --abort
```

回到 merge 前。完整安全。

Rebase 類似 `git rebase --abort`。

## 13.13 情境十三：錯了 `git clean -fd`

```bash
git clean -fd    # 刪光 untracked
# 我的 .env! 我的 node_modules!
```

**救法**：
- `.env`: 如果有備份（系統 Recycle Bin、IDE workspace）——救。否則重建。
- `node_modules/`: `npm install` / `yarn` 重建。
- 未 add 的新檔：**沒救**（沒進 git 內部）。

**預防**：永遠先 `git clean -nd` 看會刪什麼再 `-fd`。

## 13.14 情境十四：submodule 搞壞

Submodule 各種詭異狀態（Ch19 細講）。多數情況：
```bash
git submodule deinit -f --all
git submodule update --init --recursive
```

拔乾淨重來。

## 13.15 救命工具包總結

| 工具 | 救什麼 |
|---|---|
| `git reflog` | HEAD 和 branch 的歷史移動 |
| `git reset --hard HEAD@{N}` | 回到某個過去狀態 |
| `git reset --hard ORIG_HEAD` | 回到上次 destructive op 前 |
| `git fsck --lost-found` | 找孤兒 commit |
| `git cherry-pick <hash>` | 只把某 commit 拉到當前 branch |
| `git branch <name> <hash>` | 幫孤兒 commit 建 branch |
| `git merge --abort` / `git rebase --abort` | 逃生 |
| `git checkout <commit> -- <file>` | 救某檔的歷史版本 |
| `git stash list` / `git stash show -p` | 看 stash 有啥 |
| `git filter-repo` | 從歷史中刪檔（secret 清洗） |

## 13.16 心理準備

**Git 幾乎不會真的讓你丟東西**，只要：
1. commit 或 stash 過（reflog 記著）
2. 沒 `git gc --prune=now`
3. 不是 `force push` 後沒本地備份的遠端

心態：**先停手，別再操作。打開 reflog 看**。

## 13.17 預防勝於救援

- **常 commit**（包括 WIP commit）
- **重要操作前打 tag**
- **`--force-with-lease`** 不用 `--force`
- **GitHub branch protection** 保護 main
- **`.gitignore`** 避免 commit 不該 commit 的
- **`rerere.enabled=true`** + **`rebase.autoStash=true`**

## 13.18 練習

Sandbox 演練（**絕對用 sandbox，不要在真 repo 練**）：

1. 做 5 個 commit，`git reset --hard HEAD~3`，用 reflog 救回。
2. 建 branch、切過去、刪掉。用 reflog 找回那 branch 的 tip。
3. 做 `rebase -i` 亂搞，`--abort`。再故意把 rebase 做完，用 reflog 回到 rebase 前。
4. 在 detached HEAD commit，切走，用 reflog 找回。
5. 建個「secret」檔 commit，用 `git rm --cached` + amend 清掉（模擬）。

## 本章重點
- **Reflog 是第一條救命索**
- `ORIG_HEAD` 是上次 destructive op 前的 HEAD
- `fsck --lost-found` 找孤兒 commit
- Force push 搶救要協同同事的本地 reflog
- Secret 洩漏：**先 revoke**，再 `git filter-repo` 清歷史
- 碰到災難：**停手，先 reflog 看**，多數情況救得回
