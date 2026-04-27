# Ch4: branch / switch / restore（取代 checkout）

Git 2.23 把 `checkout` 拆成兩個更清楚的命令：
- `git switch`：切 branch
- `git restore`：還原檔案

`checkout` 還在、還能用，但新手學 `switch` + `restore` 比較不會搞混。

## 4.1 為什麼拆開

`git checkout` 歷史包袱，什麼都做：
- 切 branch：`git checkout main`
- 建 + 切 branch：`git checkout -b feature`
- 還原檔案：`git checkout -- file.txt`
- 切到某 commit：`git checkout abc1234`
- 切到某 commit 的某檔：`git checkout abc1234 -- file.txt`

同一命令做完全不同的事，容易誤用。拆完：
- **`switch`**：動 HEAD（branch 層級）
- **`restore`**：動 workdir / index（檔案層級）

## 4.2 branch 管理

```bash
git branch                    # 列本地 branch
git branch -a                 # 列全部（含 remote-tracking）
git branch -v                 # 顯示每個 branch 最新 commit
git branch -vv                # 加上 upstream 資訊

git branch feature            # 建 branch（不切）
git branch feature main       # 從 main 建
git branch feature abc1234    # 從某 commit 建

git branch -d feature         # 刪（若已合併）
git branch -D feature         # 強制刪（未合併也刪）⚠️

git branch -m new-name        # 改當前 branch 名
git branch -m old new         # 改 old → new

git branch --merged main      # 列已合併到 main 的 branch
git branch --no-merged main   # 列未合併的
```

### 列出「過期」的 branch
```bash
git branch --merged main | grep -v "main" | xargs git branch -d
```

刪掉所有已合入 main 的本地 branch。清理神器。

## 4.3 `git switch`

```bash
git switch main                 # 切 main
git switch feature              # 切 feature
git switch -                    # 切回前一個（像 shell cd -）

git switch -c feature           # 建 + 切（= git checkout -b）
git switch -c feature main      # 從 main 建

git switch --detach abc1234     # 切到某 commit（detached HEAD）
git switch -c temp abc1234      # 建 branch 切過去
```

### `switch` 和未提交改動

有未 commit 改動時切 branch：
- 如果改動**不衝突**（新目標 branch 不動到那些檔）：git 讓你切，改動跟著你
- 如果**衝突**：git 拒絕切，要你先 commit / stash / restore

```bash
# 強制丟棄後切
git switch --discard-changes feature    # ⚠️ 丟 workdir 改動
```

## 4.4 `git restore`

```bash
git restore file.txt                 # 用 index 覆蓋 workdir
git restore .                        # 還原所有 workdir 改動

git restore --staged file.txt        # 取消 staging（index → HEAD）
git restore --staged .               # 取消所有 staging

git restore --staged --worktree file.txt     # 兩者都還原
git restore -SW file.txt                     # 縮寫
```

### 從其他來源還原
```bash
git restore --source=HEAD~2 file.txt         # 用 HEAD~2 的版本覆蓋 workdir
git restore --source=main --staged file.txt  # 用 main 的版本覆蓋 index
```

配合 `-p` 互動式挑 hunk：
```bash
git restore -p file.txt        # 選擇性還原
```

## 4.5 對照表：checkout vs 新命令

| 舊 | 新 |
|---|---|
| `git checkout main` | `git switch main` |
| `git checkout -b feature` | `git switch -c feature` |
| `git checkout abc1234` | `git switch --detach abc1234` |
| `git checkout -- file.txt` | `git restore file.txt` |
| `git checkout HEAD~2 -- file.txt` | `git restore --source=HEAD~2 file.txt` |

**建議**：新 code / 新命令用 `switch` / `restore`，看到 `checkout` 也要懂。

## 4.6 Upstream tracking

```bash
git branch -vv
# feature abc1234 [origin/feature: ahead 2, behind 1] latest commit msg
```

Tracking 關係讓：
- `git pull` 知道要從哪拉
- `git push` 知道要推去哪
- `git status` 顯示 ahead/behind

### 設 tracking
```bash
git branch --set-upstream-to=origin/main    # 當前 branch tracking origin/main
git branch -u origin/main                    # 縮寫

# push 時同時設
git push -u origin feature
```

### 開了 push.autoSetupRemote 後更省
`git config --global push.autoSetupRemote true` 開了後：
```bash
git push      # 自動用同名 remote branch 建 tracking
```

## 4.7 Remote-tracking branch（`origin/main`）

```
本地 branch:       main, feature
remote-tracking:   origin/main, origin/feature
遠端實體 branch:   origin 的 main, origin 的 feature
```

`origin/main` 是**本地的一份「上次 fetch 時 origin 的 main 長什麼樣」的快照**。不會自己更新，要 `git fetch` 才會。

```bash
git log main..origin/main     # origin 有但本地沒的 commit（pull 會拉這些）
git log origin/main..main     # 本地有但 origin 沒的（push 會推這些）
```

## 4.8 幾個常見情境

### 場景 1：開始做新 feature
```bash
git switch main
git pull
git switch -c feature/my-stuff
# ... 改 code ...
git add -p
git commit -m "..."
git push -u origin feature/my-stuff
```

### 場景 2：切回 main 修緊急 bug
```bash
git stash         # 先存起來（或 commit）
git switch main
git pull
git switch -c hotfix/xxx
# ... 修 ...
git commit -m "..."
# ... 推 + 開 PR ...

git switch feature/my-stuff
git stash pop
```

### 場景 3：丟棄某檔的改動
```bash
git restore file.txt
```

### 場景 4：取消 add
```bash
git restore --staged file.txt
```

### 場景 5：從 main 拿某檔當下版本過來（不整個 merge）
```bash
git restore --source=main -- path/to/file.txt
```

### 場景 6：看某檔歷史版本、臨時用一下
```bash
git switch --detach abc1234
# 編譯、測試 ...
git switch -      # 回到原 branch
```

## 4.9 `checkout` 仍有用的場合

有些事 `switch` / `restore` 還沒接：

```bash
git checkout --theirs file.txt      # merge 衝突時選對方版本
git checkout --ours file.txt        # 選自己版本
git checkout --orphan new-root      # 建一個沒 parent 的 branch（少用）
```

## 4.10 本地分支命名慣例

沒強制規則，但常見：
- `feature/xxx` / `feat/xxx`
- `fix/xxx` / `bugfix/xxx`
- `hotfix/xxx`（緊急 production 修）
- `refactor/xxx`
- `chore/xxx`（雜事、不改功能）
- `docs/xxx`
- `experiment/xxx`（可能被丟棄）

用 `/` 會在 GUI / 某些工具顯示為「資料夾」，方便整理。

## 4.11 `git branch --track` 的微妙

```bash
git switch -c feature origin/feature
# 自動設 tracking 到 origin/feature
```

```bash
git switch -c feature main
# 不 tracking 任何 remote（因為 main 是本地 branch）
```

這偶爾造成困惑：「我的 feature 怎麼沒 tracking？」——看你從什麼建的。

## 4.12 陷阱集

### 陷阱 1：`git branch feature` 不會切過去
```bash
git branch feature        # 只建，不切
git switch feature        # 要自己切
```

用 `git switch -c feature` 一步完成。

### 陷阱 2：`git branch -D` 不救
```bash
git branch -D feature    # 刪了
# 但 commit 其實還在（reflog 救得回，Ch11）
```

### 陷阱 3：本地 branch 和 remote branch 名不同也能 tracking
```bash
git switch -c my-local origin/someone-else-branch
```

可以但容易搞混。儘量同名。

### 陷阱 4：`git pull` 時 branch 沒 tracking
```bash
git pull
# fatal: There is no tracking information for the current branch.
```

解法：
```bash
git branch -u origin/feature
# 或
git pull origin feature
```

## 4.13 練習

1. 建一個 `feature/test` branch，改個檔，commit，push 上去。
2. 切回 main，用 `restore --source=feature/test` 把 feature 的某檔拉過來（但不 merge）。
3. 刪所有已合併進 main 的本地 branch。
4. 在 detached HEAD 狀態改東西、commit、然後切回 main——學著怎麼把 detached 的改動救回來（用 branch）。

## 本章重點
- 新語法：**`switch` 切 branch、`restore` 還原檔案**
- `checkout` 仍能用但容易誤操作
- `branch -d` 刪（安全），`-D` 強制刪
- `origin/main` 是本地的 remote 快照，`fetch` 後才更新
- Tracking 關係影響 pull/push/status 行為
- `push.autoSetupRemote` 和 `git switch -c` 是現代日常組合
