# Practice A: 災難救援情境

七個情境，每個都在 sandbox 實作一遍。完成後你對 reflog 不再恐懼。

**原則**：**全部在 sandbox 練，不要動真 repo**。

## 準備 sandbox

```bash
mkdir /tmp/disaster && cd /tmp/disaster
git init
for i in 1 2 3 4 5; do
    echo "v$i" > file.txt
    git add file.txt
    git commit -m "v$i"
done
git log --oneline
# 你應該看到 5 個 commit
```

## 情境 1：reset --hard 後悔

### 製造災難
```bash
git reset --hard HEAD~3
git log --oneline
# 剩 v1, v2
```

### 任務
救回所有 5 個 commit。

### 驗證
`git log --oneline` 看到 5 個 commit。

<details>
<summary>解答</summary>

```bash
git reflog
# 找到 reset 前的 HEAD（message 有 "commit: v5"）
git reset --hard HEAD@{1}

# 或用 ORIG_HEAD 捷徑
git reset --hard ORIG_HEAD
```
</details>

## 情境 2：誤刪 branch

### 製造災難
```bash
git switch -c important-work
for i in 10 11 12; do
    echo "important v$i" > important.txt
    git add important.txt
    git commit -m "important v$i"
done

git switch main
git branch -D important-work
# 「刪了」
```

### 任務
不能用 `git log`（branch 沒了），只用 reflog 救回 `important-work` branch 和它的 commit。

<details>
<summary>解答</summary>

```bash
git reflog
# 找 "checkout: moving from important-work to main" 那行
# 它的 hash 就是 important-work 被刪時的 tip
git branch important-work <那個 hash>

# 驗證
git log important-work --oneline
```
</details>

## 情境 3：rebase 炸掉

### 製造災難
```bash
git switch -c feature
for i in 100 101 102 103; do
    echo "feature v$i" >> feature.txt
    git add feature.txt
    git commit -m "feature v$i"
done

# 亂來
git rebase -i HEAD~4
# 在編輯器裡把最上面改成 drop，隨便亂，存檔
# 或任意解衝突解錯
# 假設：drop 了 v100 和 v101
```

### 任務
回到 rebase 前的 feature branch 狀態（4 個 commit 都在）。

<details>
<summary>解答</summary>

```bash
git reflog
# 找 "rebase -i (start): checkout HEAD~4" 的前一個 entry
# 或 "rebase (start)"

git reset --hard HEAD@{N}
# N 是 rebase 開始前的那個 entry

# 或直接（如果 ORIG_HEAD 還指著 rebase 前）
git reset --hard ORIG_HEAD
```
</details>

## 情境 4：amend 後後悔

### 製造災難
```bash
git switch main
echo "important message here" > log.txt
git add log.txt
git commit -m "Important feature with full context"

# 後悔
git commit --amend -m "oops"
```

### 任務
救回原本的「Important feature with full context」訊息和該 commit。

<details>
<summary>解答</summary>

```bash
git reflog
# abc1234 HEAD@{0}: commit (amend): oops
# def5678 HEAD@{1}: commit: Important feature with full context  ← 要這個

git reset --hard HEAD@{1}

# 驗證
git log -1
```
</details>

## 情境 5：commit 到錯 branch

### 製造災難
```bash
git switch main

# 本來要在 feature branch，結果在 main
echo "feature code" > feat.txt
git add feat.txt
git commit -m "Add feature"
echo "more feature" >> feat.txt
git commit -am "More feature"
echo "even more" >> feat.txt
git commit -am "Even more"
```

### 任務
- 把那 3 個 commit **搬到** `misplaced` branch
- `main` 回到 3 個 commit 之前的乾淨狀態

### 驗證
- `git log main --oneline` 不含那 3 個 commit
- `git log misplaced --oneline` 有那 3 個

<details>
<summary>解答</summary>

```bash
# 建 branch 指當前（含那 3 個 commit）
git branch misplaced

# main 退回 3 個
git reset --hard HEAD~3

# 驗證
git log main --oneline
git log misplaced --oneline
```
</details>

## 情境 6：誤刪檔後 commit

### 製造災難
```bash
git switch main
echo "crucial data" > crucial.txt
git add crucial.txt
git commit -m "Add crucial"

# 幾個 commit 後
echo "x" > x.txt; git add x.txt; git commit -m "add x"
echo "y" > y.txt; git add y.txt; git commit -m "add y"

# 誤刪 crucial
git rm crucial.txt
git commit -m "cleanup"

# 又 commit 好幾個
echo "z" > z.txt; git add z.txt; git commit -m "add z"
```

### 任務
救回 `crucial.txt`（內容 "crucial data"），不要丟失其他後續 commit。

<details>
<summary>解答</summary>

```bash
# 找出 crucial.txt 最後一次存在的 commit
git log --all --oneline -- crucial.txt
# abc1234 add z
# def5678 cleanup       ← 這個 commit 刪了它
# 789abcd add y         ← 這個之前它還在

# 從 "add y" 的版本拉回
git checkout 789abcd -- crucial.txt
# 或
git restore --source=789abcd -- crucial.txt

git add crucial.txt
git commit -m "Restore crucial.txt"
```
</details>

## 情境 7：stash drop 後悔

### 製造災難
```bash
git switch main
echo "experimental" > exp.txt
git add exp.txt
git stash push -m "my experiment"

echo "wip stuff" >> file.txt
git stash push -m "wip"

git stash list

git stash drop stash@{1}    # 不小心刪了 "my experiment"
```

### 任務
救回 "my experiment" stash 的內容。

<details>
<summary>解答</summary>

```bash
# 找 orphan stash commit
git fsck --unreachable | grep commit | awk '{print $3}' > /tmp/orphans
while read h; do
    if git log -1 --format=%s "$h" | grep -q "my experiment"; then
        echo "$h"
    fi
done < /tmp/orphans
# 輸出一個 hash

git stash apply <hash>
# exp.txt 應該在 workdir
```

**如果找不到**，就表示已經 gc 清了。預防：重要 stash 改成 branch。
</details>

## 情境 8 (Bonus)：force push 被覆蓋

這個需要**模擬遠端**。複雜一點。

### 準備
```bash
# 建 bare repo 當「遠端」
mkdir /tmp/origin.git
git -C /tmp/origin.git init --bare

# 兩個「工作區」(模擬兩台機器)
git clone /tmp/origin.git /tmp/alice
git clone /tmp/origin.git /tmp/bob

cd /tmp/alice
git config user.email alice@test.com
git config user.name alice
echo "base" > file.txt
git add file.txt
git commit -m "base"
git push
```

### 製造災難
```bash
cd /tmp/bob
git pull
echo "bob's important work" >> file.txt
git commit -am "bob's change"
git push

cd /tmp/alice
# Alice 不 pull 就 force push（覆蓋 bob）
echo "alice's thing" >> file.txt
git commit -am "alice's change"
git push --force
```

`/tmp/origin.git` 現在只有 Alice 的 commit，Bob 的被覆蓋。

### 任務
救回 Bob 的 commit。

<details>
<summary>解答</summary>

```bash
# Bob 本地還有 reflog
cd /tmp/bob
git reflog
# 找到 bob's change 的 hash

# Cherry-pick 到當前
git fetch
git reset --hard origin/main
git cherry-pick <bob's-commit-hash>
git push
```

預防：Alice 用 `--force-with-lease` 就會被擋。
</details>

## 完成檢查

每個情境都試過後，你應該：
- [ ] 能直接打出 `git reflog` 看輸出
- [ ] 理解 `HEAD@{N}` 語法
- [ ] 知道 `git fsck --unreachable` 的用途
- [ ] 能區分 `reset --hard` vs `revert` 的場景
- [ ] 救回誤刪 branch 不用翻文件

## 思考題

1. 如果誤刪 repo 整個 `.git/` 資料夾呢？（Answer：沒備份就沒救）
2. `git gc --prune=now` 後 orphan commit 還救得回嗎？（Answer：通常不）
3. Reflog 到期了的 commit 還找得到嗎？（Answer：看 gc 有沒有清，沒清就 `fsck --lost-found` 還找得到）

## 本練習重點
- Reflog 是第一條救命索
- `fsck --lost-found` 是第二條
- Branch 刪了但 commit 沒 gc → 靠 reflog 找 tip
- Workdir 未 commit → 救不回（養成 commit / stash）
- Force push 被覆蓋 → 靠別人的本地 reflog
- 預防最好：tag / branch 當 bookmark、`--force-with-lease`
