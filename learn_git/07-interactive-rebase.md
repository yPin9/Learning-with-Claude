# Ch7: Interactive Rebase

把亂糟糟的 commit 變成**乾淨歷史**的工具。熟了之後你會天天用。

## 7.1 基本語法

```bash
git rebase -i HEAD~5           # 重整最近 5 個 commit
git rebase -i main             # 重整當前 branch 超過 main 的所有 commit
git rebase -i abc1234          # 從某 commit 之後開始重整
```

`-i` = interactive。會打開編輯器，列出要處理的 commit。

## 7.2 編輯器內容

```
pick abc1234 Add login button
pick def5678 WIP
pick 789abcd Fix typo
pick fedc012 Update README

# Rebase 012fedc..fedc012 onto 012fedc (4 commands)
#
# Commands:
# p, pick   = use commit
# r, reword = use commit, but edit the message
# e, edit   = use commit, but stop for amending
# s, squash = use commit, but meld into previous commit
# f, fixup  = like squash, but discard this commit's log message
# d, drop   = remove commit
# x, exec   = run command
# b, break  = stop here
# ...
```

**注意順序是舊→新**（和 `git log` 反著）。

存檔 + 退出編輯器後 git 會逐行執行。

## 7.3 常用操作

### Reword：改 commit 訊息
```
pick abc1234 Add login button
reword def5678 WIP              ← 這裡
pick 789abcd Fix typo
```

存檔後 git 跳出編輯器讓你改 WIP 那 commit 的訊息。

### Squash：合併到前一個
```
pick abc1234 Add login button
squash def5678 WIP
pick 789abcd Fix typo
```

WIP 合進 "Add login button"，訊息兩個會合併（會開編輯器讓你編輯合併訊息）。

### Fixup：合併且丟棄訊息
```
pick abc1234 Add login button
fixup def5678 WIP
pick 789abcd Fix typo
```

和 squash 類似，但 WIP 的訊息直接丟掉，保留 "Add login button" 原訊息。**這通常是你想要的**——把 fix-up commit 併回主 commit，訊息不用管。

### Drop：刪掉整個 commit
```
pick abc1234 Add login button
drop def5678 WIP               ← 或直接刪整行
pick 789abcd Fix typo
```

或直接**把那行整個刪掉**（不留 drop 關鍵字也行）。

### Edit：暫停讓你修改
```
pick abc1234 Add login button
edit def5678 Oops bug
pick 789abcd Fix typo
```

到那個 commit 時 git 會暫停，讓你 workdir 停在那時狀態：
```bash
# 改 code
git add ...
git commit --amend   # 修正那 commit
# 或
git reset HEAD^      # 拆開那 commit，改完再 add + commit
git rebase --continue
```

### Reorder：換順序
直接**改動行順序**。但要注意**衝突可能發生**（後面的 commit 可能依賴前面的）。

```
# 原本
pick A1 First
pick A2 Second
pick A3 Third

# 改順序
pick A3 Third      # 先做
pick A1 First
pick A2 Second
```

### Exec：插入 shell 命令
```
pick abc1234 Add login button
exec cargo test
pick def5678 Add tests
exec cargo test
```

每個 `exec` 後跑命令，fail 就暫停 rebase。超好用的 regression check。

## 7.4 `--autosquash`

一個 feature：邊寫邊 commit，用 `--fixup` 標記修補：

```bash
git commit -m "Add login button"
# 發現 bug
git commit --fixup=HEAD    # 產生一個 "fixup! Add login button" commit
# 又發現別的問題
git commit --fixup=HEAD~2  # fixup! 更早的某個 commit
```

然後：
```bash
git rebase -i --autosquash main
```

自動把 `fixup!` commit 排到對應原 commit 後，並標為 `fixup`：
```
pick abc1234 Add login button
fixup xxxyyy  fixup! Add login button     ← 自動
pick def5678 Add other
fixup zzzzzz  fixup! Add login button     ← 自動
```

你只要存檔退出，自動全部 fixup 進對的地方。

### 永久開
```bash
git config --global rebase.autoSquash true
```

之後 `rebase -i` 自動啟用。

## 7.5 典型 workflow

### 場景 1：PR 送出前清理

```bash
# 工作過程，commit 很散
git log --oneline -10
# abc1234 docs
# def5678 WIP
# 789abcd Fix typo in login
# fedc012 Add login button
# ...

git rebase -i main
```

編輯：
```
pick fedc012 Add login button
fixup 789abcd Fix typo in login
fixup def5678 WIP
pick abc1234 docs
```

結果：兩個乾淨 commit，"Add login button"（含所有修正）和 "docs"。

### 場景 2：分拆大 commit

```bash
git rebase -i HEAD~3
```

把某 commit 標 `edit`：
```
pick A1 small change
edit A2 big messy commit       ← 要拆
pick A3 another change
```

暫停在 A2 時：
```bash
git reset HEAD^        # 把 A2 的改動退回 workdir（unstaged）
git status             # 看改了啥
git add file1.py
git commit -m "Refactor data layer"
git add file2.py
git commit -m "Update tests"
git rebase --continue
```

A2 被拆成兩個 commit。

### 場景 3：改早期 commit 的訊息

```bash
git rebase -i HEAD~5

# 把目標那行改成 reword
reword abc1234 typo fx
```

存檔 → git 開編輯器讓你改訊息 → 改完存檔 → 繼續。

## 7.6 `edit` 暫停的進階用法

暫停中可以**任意**操作：

```bash
# 進到 edit 狀態
git rebase --edit-todo        # 再改 rebase 計畫！
git rebase --abort            # 放棄整個 rebase

git commit --amend            # 改當前 commit（最常見）
git reset HEAD^               # 拆開當前 commit
git cherry-pick abc1234       # 中途插入別的 commit
```

## 7.7 `exec` 的大用處

```bash
git rebase -i --exec "cargo test" main
```

會在**每個 commit 後面自動加**一行 `exec cargo test`。

意思：每個 commit 後跑一次 test，fail 就暫停。可以用來：
- **Regression 檢測**：看哪個 commit 弄壞 test
- **強制每 commit 編譯**：`--exec "cargo build"`
- **檢查 lint**：`--exec "pre-commit run --all-files"`

## 7.8 Rebase 中的衝突

```
... error: could not apply abc1234 WIP ...
```

解法：
```bash
# 看哪個檔衝突
git status

# 編輯解決衝突
vim conflicted_file.cpp

# 標記解決
git add conflicted_file.cpp

# 繼續
git rebase --continue

# 或放棄整個 rebase
git rebase --abort

# 或跳過這個 commit
git rebase --skip
```

## 7.9 `--onto` 進階

把某段 commit 挪到別的 base：
```
before:
... A - B - C - D - E (feature)
             \
              F - G (topic)

想把 F - G 挪到 E 後面：

git rebase --onto E C topic
```

`--onto E`：新 base 是 E
`C`：舊 base（從這裡之後的 commit 要挪）
`topic`：要挪的 branch

結果：
```
... A - B - C - D - E (main)
                    \
                     F' - G' (topic)
```

一般很少用，但 cherry-pick 整段 commit 時是正解。

## 7.10 安全：rebase 不可逆？

**可逆**——有 reflog（Ch11）：
```bash
git reflog
# abc1234 HEAD@{0}: rebase (finish): returning to refs/heads/feature
# def5678 HEAD@{5}: commit: ...
# ...

git reset --hard HEAD@{5}     # 回到 rebase 前
```

所以隨便 rebase 也不會真的掛——reflog 救得回。

**但**：force push 後如果沒 reflog（其他人的機器），那端就沒得救。所以：**不要 rebase public / shared branch**。

## 7.11 實戰工具組

### Alias
```bash
git config --global alias.ri "rebase -i"
git config --global alias.fix "commit --fixup"
git config --global alias.ria "rebase -i --autosquash"
```

用：
```bash
git fix HEAD~2    # fixup 前兩個 commit
git ria main      # interactive rebase with autosquash
```

### 預設 autoSquash + autoStash
```bash
git config --global rebase.autoSquash true
git config --global rebase.autoStash true
```

後者：rebase 時 workdir 髒，自動 stash，結束後 pop。

## 7.12 常見錯誤

### 錯誤 1：rebase public branch
```bash
git switch main
git rebase -i HEAD~5    # ❌ 改 main 的歷史
git push --force        # ❌ 炸所有人的 main
```

不要。

### 錯誤 2：刪掉有人基於它的 commit
你 rebase -i 刪掉一個 commit，別人基於它的 branch 炸。

### 錯誤 3：衝突解錯、繼續 rebase
```bash
# 沒實際解決就 add
git add .
git rebase --continue
```

導致 commit 內容不對。仔細檢查每個衝突解法。

### 錯誤 4：在 rebase 中途做無關改動
```bash
# 正在 rebase 衝突解決
# 腦袋飛走，改了另一個檔
git add .
git rebase --continue
# 那個「無關改動」被混進某個歷史 commit 了！
```

很難發現。**只解衝突，別做別的事**。

## 7.13 練習

Sandbox：
```bash
mkdir /tmp/rebase-test && cd /tmp/rebase-test
git init
for i in 1 2 3 4 5; do
    echo "line $i" >> file.txt
    git add file.txt
    git commit -m "Add line $i"
done

# 練習 1: squash 最後 3 個 commit 成一個
git rebase -i HEAD~3

# 練習 2: reorder commit 順序
git rebase -i HEAD~5

# 練習 3: reword 某個 commit 訊息

# 練習 4: drop 某個 commit

# 練習 5: 用 --fixup + --autosquash
echo "new line" >> file.txt
git commit --fixup=HEAD~2
git rebase -i --autosquash HEAD~4
```

## 本章重點
- `rebase -i` 的 6 個操作：**pick / reword / squash / fixup / drop / edit**
- `--autosquash` + `commit --fixup` = 乾淨整理
- `exec` 在 rebase 中跑 test，抓 regression
- 用 `--abort` 逃生
- **不要 rebase public branch**，reflog 只救自己
- `rerere.enabled=true` + `rebase.autoSquash=true` + `rebase.autoStash=true` 是現代配置
