# Ch5: fetch / pull / push 的真相

90% 的 git 恐懼症源於對遠端操作的誤解。這章把 `fetch`、`pull`、`push` 的每個細節攤開。

## 5.1 Remote 是什麼

Remote 是**別的 repo 的別名**。`origin` 是 `git clone` 時建的預設名。

```bash
git remote -v                    # 看 remote 清單 + URL
git remote add upstream git@github.com:owner/repo.git
git remote remove upstream
git remote rename old new
git remote set-url origin git@github.com:user/repo.git
```

一個 repo 可以有多個 remote（例如 fork 工作流：`origin` 是你的 fork，`upstream` 是原始 repo）。

## 5.2 `git fetch`：只拉不 merge

```bash
git fetch                        # 拉預設 remote (origin)
git fetch origin                 # 明確
git fetch --all                  # 所有 remote
git fetch origin main            # 只拉 main

git fetch --prune                # 刪掉本地已不存在遠端的 remote-tracking branch
```

`fetch` 做什麼：
- 下載 remote 的最新 objects 進 `.git/objects/`
- 更新 `origin/main`、`origin/feature` 等 remote-tracking branch
- **不動本地 branch，不動 workdir**

所以 `fetch` **永遠安全**。隨時可以 fetch 看看遠端有什麼。

看 fetch 後有啥新的：
```bash
git log main..origin/main       # origin/main 比本地 main 多的 commit
git diff main origin/main       # diff
```

### 自動 prune
```bash
git config --global fetch.prune true
```

之後 `fetch` 自動清掉本地殘留的已刪 remote branch。

## 5.3 `git pull`：fetch + merge（或 rebase）

```bash
git pull = git fetch + git merge origin/current_branch
```

或：
```bash
git pull --rebase = git fetch + git rebase origin/current_branch
```

### 預設策略選擇

Git 2.27+ 預設 `pull` 會警告你該選哪種：
```
hint: Pulling without specifying how to reconcile divergent branches is
hint: discouraged. You can squelch this message by running one of the following
hint: commands sometime before your next pull:
```

**選擇**：
```bash
git config --global pull.rebase true       # 預設 rebase（推薦）
git config --global pull.ff only           # 只 fast-forward（最保守）
git config --global pull.rebase false      # 預設 merge
```

### 為什麼推薦 `pull.rebase=true`

```
本地 main:  A - B - C - (my_local)
remote/main: A - B - D

pull (merge):         A - B - C - M(merge)
                           \     /
                            D---

pull --rebase:        A - B - D - C'
```

**merge 版**多一個 merge commit，歷史雜亂。**rebase 版**乾淨線性。

只有你**還沒 push** 的 commit 用 rebase，不會影響別人。

### 例外：已 push 的 branch 別 rebase
如果你的 branch 已經 push 且別人可能基於它開發，**別 rebase**（會改寫歷史，破壞他們的 branch）。

## 5.4 `git push`

```bash
git push                        # push 當前 branch
git push origin main            # 明確
git push -u origin feature      # push + set upstream
```

### 幾個重要選項

```bash
git push --force                    # 強制覆寫 remote（⚠️⚠️⚠️）
git push --force-with-lease         # 強制但安全（推薦）
git push --delete origin feature    # 刪 remote branch
git push origin :feature            # 舊語法，等效
git push --tags                     # 推 tag
git push --dry-run                  # 模擬（不實際推）
```

### `--force` vs `--force-with-lease`

`--force`：**無條件**覆寫 remote。如果別人同時 push 了，你直接覆蓋掉他們的 commit。

`--force-with-lease`：檢查 remote 目前狀態是否和你本地的 remote-tracking 一致。不一致則拒絕——保護別人的 commit。

**永遠用 `--force-with-lease`，不用 `--force`**。

```bash
# Alias
git config --global alias.pushf "push --force-with-lease"
```

### 什麼時候該 force push

**該**：
- Rebase 過自己的 feature branch 後
- Amend 過已 push 的 commit（只能你一人用的 branch）
- 清掉不小心 push 的敏感資訊

**不該**：
- 多人共用的 branch（main / develop）—**絕對不可**
- 不確定遠端狀態時

### 保護主幹
GitHub Settings → Branches → Branch protection rules：
- 禁止 force push 到 main / master
- 禁止直接 push 到 main（必須走 PR）

**開這個**，不然遲早有人炸 repo。

## 5.5 `git remote show origin`

```bash
git remote show origin
```

完整列出：
- Fetch/push URL
- 每個 branch 的 tracking 關係
- 哪些本地 branch 「out of date」
- 哪些遠端 branch 本地沒追蹤

Debug remote 問題用這個。

## 5.6 典型工作流程

### 早上開工
```bash
git switch main
git pull               # 或 git fetch + git rebase
```

### 開新 feature
```bash
git switch -c feature/xxx
# ... 改 code ...
git add -p
git commit -m "..."
git push -u origin feature/xxx
# 在 GitHub 開 PR
```

### 同步 main 的更新到 feature
```bash
git switch feature/xxx
git fetch origin main
git rebase origin/main
# 解衝突（如有）
git push --force-with-lease    # feature 是你一人用的 branch，可 force
```

### PR 合併後清理
```bash
git switch main
git pull
git branch -d feature/xxx      # 本地刪
# remote 通常 GitHub 自動刪（合併 PR 時勾）
```

## 5.7 `git pull` 的陷阱

### 陷阱 1：產生意料之外的 merge commit
沒設 `pull.rebase=true` 時：
```
本地 commit A
remote 也有新 commit B
git pull → 產生 merge commit M
```

`git log` 看到「Merge branch 'main' of ...」——多餘且醜。

### 陷阱 2：在 feature branch 上 `git pull`
```bash
git switch feature
git pull                # 拉 origin/feature
```

如果你本地 rebase 過（commit hash 變了），`pull` 會試著 merge 原本的、現在不同 hash 的版本——一團亂。解法：**rebase 過就 `push --force-with-lease`，不要 pull**。

### 陷阱 3：`pull` 在髒 workdir
未 commit 的改動 + `pull` merge 衝突 = 混亂。設 `rebase.autoStash=true` 讓它自動 stash。

## 5.8 `git fetch` 單獨使用的價值

比 `pull` 安全：
```bash
git fetch                            # 先看有啥
git log main..origin/main --oneline  # 遠端多了哪些
git diff main origin/main            # 看 diff
git rebase origin/main               # 確認後才 rebase
```

「先偵察再攻擊」的工作方式。

## 5.9 Remote-tracking branch 的運作

```
執行 git fetch 後：
  .git/refs/remotes/origin/main  ← 存 remote main 的 commit hash
  .git/refs/remotes/origin/feature ← 存 remote feature 的 commit hash
```

這些 **ref** 和本地 branch 一樣，只是不能 checkout 直接改。把它們當成「**本地快照的 pointer**」。

### 檢查
```bash
cat .git/refs/remotes/origin/main
git log origin/main --oneline -5
```

## 5.10 多 remote 的 fork 工作流

```bash
# 1. Fork 原 repo 到你的 GitHub 帳號
# 2. Clone 你的 fork
git clone git@github.com:yourname/repo.git
cd repo
git remote -v
# origin  git@github.com:yourname/repo.git (fetch)
# origin  git@github.com:yourname/repo.git (push)

# 3. 加 upstream 指向原 repo
git remote add upstream git@github.com:original/repo.git
git remote -v
# origin    git@github.com:yourname/repo.git
# upstream  git@github.com:original/repo.git

# 4. 平時同步 upstream
git fetch upstream
git switch main
git rebase upstream/main
git push      # 更新自己的 fork
```

這是 open source 貢獻的標準流程。Ch17 細講。

## 5.11 `push --force-with-lease --force-if-includes` (Git 2.30+)

比 `--force-with-lease` 更精細的保護：
```bash
git push --force-with-lease --force-if-includes
```

除了檢查 remote ref 相符，還檢查「遠端最新 commit 是否在你本地 reflog」——避免某些 `--force-with-lease` 無法抓的邊緣情況。

## 5.12 其他 remote 命令

```bash
git remote update              # fetch 所有 remote
git remote prune origin        # 清掉 origin 上已刪的 remote-tracking
git ls-remote origin           # 列 origin 所有 ref（不下載）
```

## 5.13 陷阱集

### 陷阱 1：push 到錯 branch
沒 upstream tracking 時：
```bash
git push origin feature        # OK，明確
git push                        # 預設 push 哪裡？
```

開 `push.default=current`（Ch0 設過）讓它總是推當前 branch 到同名。

### 陷阱 2：push 後才發現 commit 訊息爛
你一個人的 feature branch：
```bash
git commit --amend -m "better message"
git push --force-with-lease
```

主幹不行。

### 陷阱 3：`git pull` 拒絕 "refusing to merge unrelated histories"
兩個沒共同祖先的 repo。通常是誤操作。要真合併：
```bash
git pull --allow-unrelated-histories
```

但十有八九是 clone 錯 repo。

## 5.14 練習

1. `git fetch` 後用 `log main..origin/main` 看遠端有什麼你沒有。
2. Clone 一個 repo、加第二個 remote、fetch 它、diff 兩個 remote 的 main。
3. 做一個本地 commit，push 時加 `--dry-run` 看會 push 什麼。
4. 練 `push --force-with-lease`：先做一個 rebase，push，再本地改個 commit amend，再 `--force-with-lease`。

## 本章重點
- `fetch` 永遠安全，習慣先 fetch 再 merge/rebase
- **`pull.rebase=true`** 讓 pull 預設 rebase，歷史乾淨
- **永遠用 `--force-with-lease`**，不用 `--force`
- 只 force push **自己的 feature branch**，不動 shared branch
- 多 remote（fork 流程）：origin 是自己 fork，upstream 是原 repo
- 開 `push.autoSetupRemote` 省掉每次 `-u`
- 保護主幹：GitHub branch protection rule
