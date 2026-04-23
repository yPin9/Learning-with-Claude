# Ch11: Reflog——人生救星

最重要的一章。**理解 reflog 等於在 git 世界多一條命**。

## 11.1 What

Reflog = **reference log**。記錄**每個 ref**（HEAD、branch、stash）的**歷史移動**。

```bash
git reflog
```

典型輸出：
```
abc1234 HEAD@{0}: commit: Add login
def5678 HEAD@{1}: rebase (finish): returning to refs/heads/feature
789abcd HEAD@{2}: rebase (pick): Add login
fedc012 HEAD@{3}: checkout: moving from main to feature
...
```

每行：
- **commit hash**
- **ref 名**`@{序號}`：0 是最新，1 是上一步，依此類推
- **操作描述**：commit、rebase、checkout、merge、reset...
- **訊息**（如果有）

## 11.2 為什麼它救命

Git 的操作（commit、rebase、reset、amend...）**改的都是 ref**。Reflog 記錄 **ref 每次被改前的位置**。

所以：
- `reset --hard` 弄丟 commit？→ reflog 找得到舊位置
- Rebase 搞砸？→ reflog 有 rebase 前的 HEAD
- 誤刪 branch？→ reflog 有那 branch 最後的 commit
- Amend 後悔？→ reflog 有舊 commit

**只要那個 ref 指過的 commit，reflog 都記著**。

### 限制
- **Reflog 有過期**：預設 90 天（可配）
- **只在本地**：別的機器的 reflog 你看不到
- **只包含 reachable 的 ref**：未 commit 的 workdir 改動救不回

## 11.3 核心操作：回到 N 步前

```bash
git reset --hard HEAD@{3}    # 回到 HEAD 3 步前的狀態
```

這個 `HEAD@{N}` 語法可以當 commit 引用——放哪裡都行。

## 11.4 典型情境：reset --hard 後悔

```bash
# 災難
git reset --hard HEAD~5    # 丟掉 5 個 commit

# 救援
git reflog                 # 看 reset 前的 HEAD
# abc1234 HEAD@{1}: reset: moving to HEAD~5
# def5678 HEAD@{2}: commit: ...   ← 這是 reset 前

git reset --hard HEAD@{2}
# 或直接用 hash
git reset --hard def5678
```

## 11.5 典型情境：rebase 搞砸

```bash
git rebase -i HEAD~10
# 做了一堆事，某個衝突解錯，或 drop 錯 commit，整個 branch 爛了

git reflog
# ... 找 rebase 開始前的 HEAD ...
# abc1234 HEAD@{15}: rebase -i (start): checkout HEAD~10

git reset --hard HEAD@{15}   # 回到 rebase 前
# 或找更早一點的（rebase (start) 的前一個）
```

**簡化版**：
```bash
git reflog | grep "rebase (start)"
# 找到後 reset
```

## 11.6 典型情境：誤刪 branch

```bash
git branch -D feature       # 刪掉了

# 那 branch 指的 commit 是什麼？
git reflog --all            # 看所有 ref 的 reflog
# 或
git reflog feature          # ❌ branch 沒了，reflog 也沒了？

# 實際上 branch 的 reflog 也刪了，但 commit 本身還在 reflog 的 HEAD
# 找最後一次切到 feature 的時候
git reflog | grep feature
# abc1234 HEAD@{10}: checkout: moving from feature to main

# 救回
git branch feature abc1234
```

或直接 `git fsck --lost-found` 找孤兒 commit。

## 11.7 典型情境：amend 後悔

```bash
git commit -m "good message"
# 後悔了
git commit --amend -m "changed my mind"

# 想回原本那個
git reflog
# abc1234 HEAD@{0}: commit (amend): changed my mind
# def5678 HEAD@{1}: commit: good message         ← 原來的

git reset --soft HEAD@{1}    # 回到 amend 前（改動保留在 index）
```

## 11.8 典型情境：checkout 切 branch 前沒 commit

```bash
# workdir 有改動
git switch main        # ⚠️ 通常 git 會警告「會 overwrite」
# 如果沒警告就切了，某些情況可能丟東西
```

Reflog 只記 ref 移動，**workdir 沒 commit 的東西救不回**。所以 git 才會警告。

預防：養成切 branch 前 `git status` + `stash` / `commit`。

## 11.9 `git fsck --lost-found`

找**沒被任何 ref 可達**的 commit（孤兒）：

```bash
git fsck --lost-found
# dangling commit abc1234
# dangling commit def5678
```

看內容：
```bash
git show abc1234
```

救回：
```bash
git branch rescue abc1234    # 建 branch 指向它
```

這是**終極救命工具**，當 reflog 也找不到時用。

## 11.10 `HEAD@{time}` 語法

可以用時間：

```bash
git reflog
git show HEAD@{yesterday}
git show HEAD@{"1 hour ago"}
git show HEAD@{"2024-04-15 10:30"}

git reset --hard HEAD@{2.days.ago}
```

罕用但有時方便。

## 11.11 `ORIG_HEAD`

某些 destructive 操作前會自動存一個 `ORIG_HEAD`：
- `git merge`
- `git rebase`
- `git reset`
- `git pull`

```bash
git reset --hard ORIG_HEAD
```

等同「回到上次那些操作之前」的捷徑，不用 grep reflog。

## 11.12 Reflog 不是無限

預設過期規則：
- Reachable commits 的 reflog：**90 天**
- Unreachable（orphan）：**30 天**

可配：
```bash
git config gc.reflogExpire "1 year"
git config gc.reflogExpireUnreachable "90 days"
```

手動清：
```bash
git reflog expire --expire=30.days ALL
```

日常不用管，**知道有期限**即可。

## 11.13 如何預防需要 reflog

反過來說：寫 code 時讓 reflog 不必要：
1. **切 branch 前 commit 或 stash**
2. **Force push 前 `--force-with-lease`**（保護別人）
3. **重要操作前 tag / branch 當 bookmark**：
   ```bash
   git tag backup-before-big-rebase
   git rebase -i ...
   # 如果想回去
   git reset --hard backup-before-big-rebase
   ```
4. **多 commit 少 stash**：commit 比 stash 安全

## 11.14 Reflog 的 remote tracking 版

```bash
git reflog origin/main
# 看 origin/main 每次 fetch 時的 hash
```

有時有用：「昨天的 origin/main 是哪個 commit」。

## 11.15 配合 `cherry-pick` 救命

找到孤兒 commit 後，如果不想整個 reset（會丟現在的進度）：
```bash
git cherry-pick <orphan-hash>
```

把那個 commit 的改動**複製**到當前 branch 不影響其他進度。

## 11.16 實際演練

```bash
# 建 sandbox
mkdir /tmp/reflog-test && cd /tmp/reflog-test
git init
for i in 1 2 3 4 5; do
    echo "v$i" > a.txt
    git add a.txt
    git commit -m "v$i"
done

git log --oneline
# e5... v5
# d4... v4
# c3... v3
# b2... v2
# a1... v1

# 災難：reset 掉 3 個
git reset --hard HEAD~3
git log --oneline
# b2... v2
# a1... v1

# 救援：reflog
git reflog
# b2... HEAD@{1}: reset: moving to HEAD~3
# e5... HEAD@{2}: commit: v5
# d4... HEAD@{3}: commit: v4
# ...

git reset --hard HEAD@{2}
git log --oneline
# 全部回來了
```

## 11.17 當 reflog 也救不回

**極少發生**，但：
1. 機器壞、`.git/` 丟失 → 本地 reflog 沒了
2. `git gc --prune=now --aggressive` 強制清過期 → orphan 真的走了
3. Force push 後，遠端的 reflog（如果開了）可能有舊版——server 才看得到

最後的救命：
- GitHub 有 **Activity** 頁看 push 歷史（包含 orphaned 的 branch tip）
- 同事的 local clone 還有你 force push 前的 commit

## 11.18 常用 reflog 查詢

```bash
git reflog                   # HEAD 的 reflog
git reflog show branch-name  # 某 branch 的
git reflog --all             # 所有 ref
git reflog --date=iso        # 時間戳
git reflog | grep rebase     # 找 rebase 記錄
git reflog | grep "reset:"
```

## 11.19 練習

1. 做 5 個 commit，`git reset --hard HEAD~3`，用 reflog 救回。
2. 做 `git rebase -i` 亂搞，用 reflog 找 "rebase -i (start)" 並 reset 回去。
3. 建 branch、切過去、切回、刪掉。只用 `git reflog` 救回那 branch。
4. 試 `git fsck --lost-found`（應該會看到剛才的孤兒）。

## 本章重點
- **Reflog 記錄每個 ref 的每次移動**
- `git reflog` + `git reset --hard HEAD@{N}` 救回一切（reset/rebase/amend 後悔）
- `git fsck --lost-found` 找孤兒 commit
- `ORIG_HEAD` 是上次 destructive 操作前的 HEAD
- **本地才有 reflog**，換機器看不到
- 預防：重要操作前加 tag，force push 用 `--force-with-lease`
- **沒 commit 的 workdir 改動救不回**——養成 commit/stash 習慣
