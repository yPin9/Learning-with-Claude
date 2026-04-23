# Ch10: Stash

把 workdir 的改動**暫時放一邊**，等等再拿回來。適合「做到一半突然要切去救火」。

## 10.1 基本用法

```bash
git stash                        # 存 workdir + index 改動
git stash list                   # 看有哪些 stash
git stash pop                    # 拿回最新的 stash 並從列表刪掉
git stash apply                  # 拿回但不從列表刪
git stash drop                   # 刪掉最新 stash（不套回）
git stash clear                  # 全刪
```

### 帶訊息
```bash
git stash push -m "WIP: trying approach X"
```

之後 `git stash list` 看得到訊息，方便分辨。

## 10.2 典型場景

### 場景 1：切 branch 救火
```bash
# 正在 feature branch 上改一半
git stash
git switch main
# ... 救火、commit、push ...

git switch feature
git stash pop
```

### 場景 2：pull 前暫存
```bash
git stash
git pull
git stash pop
```

或直接設 `rebase.autoStash=true`，`pull --rebase` 自動 stash/pop。

### 場景 3：想看看其他 branch 是什麼樣
```bash
git stash
git switch other-branch
# ... 看完 ...
git switch -
git stash pop
```

## 10.3 Stash 的範圍

預設 `git stash` 只存**已追蹤檔案**的改動。未追蹤檔（新檔）和 ignored 檔不動。

```bash
git stash -u               # 包含 untracked
git stash --include-untracked   # 同上
git stash -a               # 包含 untracked + ignored（更激進）
git stash --all
```

**常用 `-u`**——新檔也該被保護。

## 10.4 部分 stash

### `-p` 互動式
```bash
git stash push -p
# 像 git add -p，逐 hunk 問要不要 stash
```

超好用——「只暫存一部分改動，其他繼續留著改」。

### stash 特定檔
```bash
git stash push file1.txt file2.txt
git stash push -m "frontend stuff" src/ui/
```

## 10.5 保留 staged，只 stash unstaged

```bash
git stash --keep-index
```

用途：「我 add 了一些改動準備 commit，但 workdir 還有實驗中的東西；想跑 test 確認 staged 的改動是對的」：
```bash
git add good-changes.py
git stash --keep-index    # staged 留下，其餘收起來
pytest                     # 測 staged 的改動
git stash pop              # 其他改動回來
```

## 10.6 看 stash 內容

```bash
git stash show                   # 最新 stash 的摘要
git stash show stash@{1}         # 特定編號
git stash show -p                # 看完整 diff
git stash show -p stash@{2}
```

## 10.7 從 stash 套到某 branch

```bash
git stash branch new-branch      # 建 branch + 套 stash
```

等效於：
1. 建 new-branch 從 stash 建立時的 base commit
2. 切到 new-branch
3. pop stash

用途：「本來在 main 改的，改完發現應該是個 feature branch」。

## 10.8 Stash 的儲存

`git stash` 內部是個**特殊 commit**：
- 一個 commit 存 workdir 狀態
- 一個存 index 狀態
- 都指向 `refs/stash`

```bash
git log --oneline refs/stash
```

所以 stash **不會丟**（直到 drop 或 clear）。但它**不是 branch**，不會被 push（push 不推 stash）。

## 10.9 Stash 踩雷

### 雷 1：`pop` 衝突
```bash
git stash pop
# CONFLICT (content): ...
```

這時 stash **還沒被刪**（git 知道發生衝突）。解：
```bash
# 解衝突
git add <resolved>
# 手動從 stash list 刪（因為 pop 沒自動刪）
git stash drop
```

為了避免這個，可以先 `apply` 再手動 drop：
```bash
git stash apply
# 確認 OK
git stash drop
```

### 雷 2：誤 `git stash clear`
**清了就不在 stash list**。但 refs/stash 最近版本還在一陣子（reflog）：
```bash
git fsck --unreachable | grep commit
# 找到像 stash 的 commit，看它的 tree
git show <hash>
# 如果是你要的，apply
git stash apply <hash>
```

這是 nuclear option，平常不會用到。

### 雷 3：stash 很多個，搞不清哪個是啥
```bash
git stash list
# stash@{0}: WIP on feature: abc1234 msg
# stash@{1}: WIP on main: def5678 msg
# stash@{2}: WIP on feature: 789abcd msg
```

預設訊息沒鑑別度。**用 `-m` 寫清楚**：
```bash
git stash push -m "auth refactor wip"
```

### 雷 4：以為 stash 同步到 remote
Stash **只在本地**。換機器就沒了。長期保存不該用 stash——用 branch。

### 雷 5：stash 後 rebase base
```bash
git stash
git rebase main        # 改了 current branch
git stash pop          # 可能衝突
```

rebase 跨越 stash 的 base commit，pop 容易衝突。

## 10.10 比較：stash vs WIP commit

```bash
# Stash
git stash
git switch main; ...
git switch -
git stash pop

# WIP commit
git commit -am "WIP"
git switch main; ...
git switch -
git reset HEAD^       # 退回到 WIP commit 前（改動還在 workdir）
```

### Stash 優勢
- 不污染 log
- 不用 commit 訊息
- 輕量

### WIP commit 優勢
- **會被 push**（如果你記得 push）——換機器 / 給 review
- Reflog 友善
- 可以 rebase 改掉

**我個人建議**：短期（小時）用 stash，跨天用 WIP commit。

## 10.11 `git stash` 的小命令索引

```bash
git stash                            # push 簡寫
git stash push [-m msg] [-u] [-p]    # 完整
git stash list
git stash show [-p] [stash@{n}]
git stash apply [stash@{n}]
git stash pop [stash@{n}]
git stash drop [stash@{n}]
git stash clear
git stash branch <name> [stash@{n}]
```

### 常用 alias
```bash
git config --global alias.stsh "stash push -u -m"
```

用：
```bash
git stsh "auth wip"
```

## 10.12 練習

1. 改個檔、stash、看 `git stash list`、`git stash show -p`。
2. 做一堆改動（含新檔），用 `git stash -u` 存下、切 branch、再回來 pop。
3. 用 `git stash push -p` 只 stash 檔案的一部分改動。
4. 故意製造 stash pop 衝突，練習解。

## 本章重點
- `git stash` 暫存 workdir + index，切場景後 `pop` 回來
- 預設**不含 untracked**，通常要 `-u`
- `git stash push -m "..."` 加訊息，list 看得懂
- `git stash pop` 衝突時 stash **沒自動刪**，要手動 drop
- Stash 只在本地、不 push；長期存用 branch
- `--keep-index` 讓 staged 留著，測 staged 的改動
