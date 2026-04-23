# Ch18: 衝突解決

Merge / rebase / cherry-pick 都可能遇到。這章講怎麼正確解。

## 18.1 什麼時候發生衝突

Git 能自動合併：
- 不同檔的改動
- 同檔不同行的改動

Git 無法自動合併：
- **同檔同行**被兩邊都改
- 一邊刪檔、另一邊改
- Rename 衝突

## 18.2 衝突的樣子

Merge 或 rebase 時衝突，檔案長這樣：

```python
def greet(name):
<<<<<<< HEAD
    return f"Hello, {name}!"
=======
    return f"Hi {name}"
>>>>>>> feature
```

- `<<<<<<< HEAD`：當前 branch（你的）開始
- `=======`：分界
- `>>>>>>> feature`：要合入的 branch（對方的）結束

### 對 rebase 更直覺的標籤
```python
<<<<<<< HEAD
    return f"Hi {name}"          # 這是「當前目標」（其實是被 replay 上去的那端）
=======
    return f"Hello, {name}!"
>>>>>>> abc1234 (original commit message)
```

Rebase 時 HEAD 是「你 rebase 到的那端」，`>>>>>>>` 是「你正在 replay 的原 commit」。**和 merge 相反**，要留意。

## 18.3 怎麼解

三步：
1. **打開檔案，選出最終版本**（刪掉 `<<<<<<< ======= >>>>>>>` 這些標記）
2. `git add <file>`
3. `git merge --continue` / `git rebase --continue` / `git cherry-pick --continue`

## 18.4 看狀態

衝突中：
```bash
git status
```

輸出：
```
You have unmerged paths.
  (fix conflicts and run "git commit")
  (use "git merge --abort" to abort the merge)

Unmerged paths:
  (use "git add <file>..." to mark resolution)
        both modified:   auth.py
        deleted by us:   old.py
        added by them:   new.py
```

類別：
- **both modified**：兩邊都改
- **deleted by us / them**：一邊刪一邊改
- **added by us / them**：一邊加一邊也加（同檔名）
- **both added**：兩邊都新加（內容不同）

## 18.5 各類衝突的解法

### both modified（最常見）
手動編輯檔，選最終版：
```bash
vim auth.py
git add auth.py
```

### deleted by us（對方刪了、我們改了）
決定要**保留**還是**真的刪**：
```bash
# 保留
git add auth.py

# 真的刪
git rm auth.py
```

### deleted by them（我們刪了、對方改了）
類似：
```bash
# 保留對方的改
git checkout --theirs auth.py
git add auth.py

# 確認刪
git rm auth.py
```

### rename 衝突
一邊 rename、一邊改：
```bash
git status
# renamed: old.py -> new.py
# modified: old.py
```

git 通常自動處理得還好。必要時：
```bash
git rm old.py
mv new.py final.py
git add final.py
```

## 18.6 `git checkout --ours` / `--theirs`

整個檔選一邊：
```bash
git checkout --ours file.txt       # 用「我的」版本（HEAD）
git checkout --theirs file.txt     # 用「對方」版本
git add file.txt
```

**rebase 中 `ours` 和 `theirs` 的含義反過來**——因為 rebase 是「把你的 commit 套到對方上」，所以 HEAD 是對方：

| 情境 | `--ours` | `--theirs` |
|---|---|---|
| merge | 當前 branch | 被 merge 的 branch |
| rebase | base branch（目標） | 你要 replay 的 commit |

困惑就看 `git status` 的描述。

## 18.7 `git mergetool`

叫外部 merge 工具：
```bash
git mergetool
```

設定：
```bash
git config --global merge.tool vimdiff
git config --global merge.tool meld
git config --global merge.tool vscode
```

### VS Code 當 mergetool
```bash
git config --global merge.tool vscode
git config --global mergetool.vscode.cmd 'code --wait $MERGED'
```

VS Code 有 3-way merge UI，可視化解衝突。

### 其他選擇
- **meld**：跨平台 3-way diff
- **kdiff3**：老牌
- **beyond compare**：商業但強
- **nvim-based**：各種 neovim plugin

## 18.8 3-way merge 的理解

你以為 git 做的是「左右兩邊合併」。實際上是**3-way**：考慮**base**（共同祖先）。

```
     A (base)
    / \
   /   \
  B     C
   \   /
    merge
```

Git 看：
- A → B 改了什麼
- A → C 改了什麼

如果 B 和 C 改動不衝突 → 自動合併
如果 B 和 C 改動**同一位置**不同 → 衝突

這就是為什麼「同一個地方被改」會衝突，但「檔案一邊加 header、一邊加 footer」通常不會。

### 顯示 3-way 資訊
```bash
git config --global merge.conflictStyle diff3
```

衝突區塊會多一個「**base**」段：
```
<<<<<<< HEAD
ours
|||||||
base
=======
theirs
>>>>>>> feature
```

看 base 幫助判斷「兩邊各改了什麼」、「要保留哪方或合併」。**強烈建議開**。

### C++23 的 zdiff3
```bash
git config --global merge.conflictStyle zdiff3
```

更精簡，只顯示真正衝突的部分。

## 18.9 `git rerere`：記住衝突解法

```bash
git config --global rerere.enabled true
```

啟用後，git 記錄「這個衝突你怎麼解的」，下次同樣衝突自動套用。

用於反覆 rebase 同一 feature branch 到 main：每次都要解同樣衝突，開 rerere 後第二次起自動。

看記錄：
```bash
ls .git/rr-cache/
git rerere status
git rerere diff
```

忘記某個：
```bash
git rerere forget <path>
```

## 18.10 中途逃生

```bash
git merge --abort           # merge 中途放棄
git rebase --abort          # rebase 中途放棄
git cherry-pick --abort
git revert --abort
```

回到操作前狀態。**最安全的重開**。

## 18.11 繼續

```bash
git merge --continue
git rebase --continue
git cherry-pick --continue
git revert --continue
```

Rebase 特別有 `--skip`（跳過這個 commit）：
```bash
git rebase --skip
```

## 18.12 衝突的幾種預防

### 1. 頻繁 rebase
Branch 活太久 → 衝突多。每天 rebase 一次 main。

### 2. PR 小
改 100 行的 PR 衝突率遠小於 1000 行。

### 3. 先行溝通
大重構前和同事協調。避免兩人重寫同一個檔。

### 4. 尊重格式化
不要把 function 順序重排——無關改動讓衝突像 bomb crater。

### 5. Lockfile / 生成檔常衝突
`package-lock.json`、`Cargo.lock` 這類。策略：
- Merge 策略設 `theirs`：`.gitattributes` 加 `package-lock.json merge=theirs`
- 或每次 merge 後 `npm install` 重建

## 18.13 配合 `diff` 看衝突更清楚

```bash
git diff                    # 當前衝突檔的 diff
git diff --ours file.txt    # 我方改了什麼
git diff --theirs file.txt  # 對方改了什麼
git diff --base file.txt    # base 是什麼
```

## 18.14 解衝突的思考流程

```
看衝突 →
  1. 兩邊**意圖**分別是什麼？（讀 commit message、讀 code）
  2. 兩邊都要？→ 合併兩邊的改動（最常見）
  3. 只要一邊？→ --ours 或 --theirs
  4. 都不要？→ 都刪掉（少見）
  5. 需要第三方案？→ 自己寫
→ 存檔、測試
→ git add
→ continue
```

**解衝突最怕「匆忙亂選」導致功能壞掉**。慢下來，確認兩邊 intent。

## 18.15 複雜衝突：長命的 feature branch

branch 活了三個月，main 大改：

### 策略 1：小步 rebase
不要一次 rebase 3 個月。每天 / 每週 rebase，衝突分散。

### 策略 2：`rerere` + `exec`
```bash
git config --global rerere.enabled true
git rebase -i --exec "make test" origin/main
```

rerere 記住每次解法，exec 每次 commit 後跑 test，確認 rebase 後每個 commit 還有功能。

### 策略 3：棄療、重做
有時候 rebase 成本超過重新 cherry-pick 有用的改動：
```bash
# 從 main 開新 branch
git switch -c new-feature main

# 手動把關鍵改動搬過來（cherry-pick 或 restore）
git restore --source=old-feature path/to/changed/files
```

## 18.16 練習

Sandbox：
```bash
mkdir /tmp/conflict && cd /tmp/conflict
git init
echo "line 1" > a.txt
git add a.txt
git commit -m "init"

# 建 feature branch
git switch -c feature
echo "feature line" > a.txt
git add .
git commit -m "feature"

# 回 main 做衝突改動
git switch main
echo "main line" > a.txt
git add .
git commit -m "main"

# 試 merge
git merge feature
# CONFLICT

# 看衝突
cat a.txt
git status
git diff

# 解（用 diff3 style 開更清楚）
git config merge.conflictStyle diff3
# 重跑 merge 看差別
```

練習：
1. 手動解上面的衝突（合併兩邊）
2. 用 `--ours` 解
3. 用 `--theirs` 解
4. 中途 `git merge --abort` 逃
5. 開 `rerere.enabled`，製造同樣衝突兩次，看第二次自動

## 18.17 本章重點
- 衝突標記：`<<<<<<<` / `=======` / `>>>>>>>`；**開 `diff3` 看 base**
- **Merge 的 ours/theirs 和 rebase 的相反**，困惑時看 `git status`
- `git mergetool` 叫外部工具（VS Code、meld）
- `rerere.enabled=true` 自動記衝突解法
- `--abort` 是任何時候的逃生按鈕
- 預防 > 解決：小 PR、常 rebase、lockfile 衝突靠 `merge=theirs` 策略
- 複雜衝突慢下來、看兩邊 intent 再動手
