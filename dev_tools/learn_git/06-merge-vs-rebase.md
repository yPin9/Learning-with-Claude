# Ch6: merge vs rebase

**政治話題**。團隊內吵起來能吵三小時。本章給你真實世界的取捨。

## 6.1 兩者做什麼

### Merge
把兩個 branch 的歷史**合在一起**，產生一個新的 merge commit（有兩個 parent）。

```
Before:
... A - B - C (main)
         \
          D - E (feature)

git switch main
git merge feature

After:
... A - B - C - - - M (main)
         \        /
          D - E (feature)
```

`M` 的 parent 是 C 和 E。原 commit 保留。

### Rebase
把 feature 上的 commit **複製到 main 後面**，hash 改變：

```
Before:
... A - B - C (main)
         \
          D - E (feature)

git switch feature
git rebase main

After:
... A - B - C (main)
             \
              D' - E' (feature)
```

D'、E' 是新 commit（內容可能同 D/E 但 hash 不同）。feature 看起來「像是從 C 直接開始」。

## 6.2 快速對照

| | Merge | Rebase |
|---|---|---|
| 歷史 | 保留所有 branch 結構 | 線性化 |
| 產生新 commit | merge commit | 複製原 commit 成新 commit |
| Commit hash | 不變 | 變 |
| 衝突解決 | 一次解決（在 merge commit） | 每個 commit 可能要解 |
| 可逆性 | 可 `git revert` merge | 可 reflog 救，但複雜 |
| 適合 | public branch | 個人 feature branch |

## 6.3 Fast-forward merge

當 main 沒新 commit、feature 領先：
```
... A - B - C (main)
             \
              D - E (feature)
```

```bash
git switch main
git merge feature
```

git 發現可以**直接把 main 移到 E**，不需要 merge commit：
```
... A - B - C - D - E (main, feature)
```

這叫 **fast-forward**。

### 強制產生 merge commit
即使可以 FF，有時想保留 branch 軌跡：
```bash
git merge --no-ff feature
```

產生：
```
... A - B - C - - - - M (main)
             \      /
              D - E
```

### 強制只 FF
```bash
git merge --ff-only feature
```

不能 FF 就失敗（不產生 merge commit）。

## 6.4 何時 merge，何時 rebase

### 用 **merge**
- **要保留 feature branch 的歷史軌跡**（「這幾個 commit 是一起做的」）
- **多人共用的 branch**（別人基於某個 commit 開發中）
- **main → feature 同步**（很少這樣，但需要時）
- **long-lived branch 之間**（release、develop）

### 用 **rebase**
- **把你個人的 feature branch 更新到最新 main**
- **整理 feature 上凌亂的 commit**（interactive rebase, Ch7）
- **讓歷史保持線性**

### 黃金法則
> **Rebase 只你自己的 branch，Merge public branch**

已 push 給別人用的 branch 不要 rebase。

## 6.5 實務工作流 1：rebase-then-merge

最常見的 GitHub flow：

```bash
# 開發 feature
git switch -c feature
# ... 做事、commit ...

# main 有新更新，同步
git fetch origin
git rebase origin/main

# push（rebase 後要 force）
git push --force-with-lease

# 開 PR，merge 時 GitHub 用 "Merge" 或 "Squash"
```

PR merge 可以選：
- **Create a merge commit**：留 feature 歷史 + merge commit
- **Squash and merge**：所有 commit 合成一個進 main（歷史乾淨但丟細節）
- **Rebase and merge**：feature commit 直接接到 main（線性歷史 + 保留細節）

**推薦**：重要 feature 用 rebase and merge，小修用 squash。

## 6.6 實務工作流 2：merge-only

不爽改歷史的：
```bash
git switch -c feature
# ... 改 ...

git switch main
git merge --no-ff feature
```

歷史會有 merge commit 鼓包但每個 commit hash 永不變。Linux kernel 和一些保守專案走這條。

## 6.7 rebase 的危險

### 危險 1：force push 時 rebase 錯 branch
```bash
git switch main
git rebase origin/feature   # 把 main rebase 到 feature！
git push --force            # 炸 main
```

rebase 前**看清當前 branch** (`git status`)。

### 危險 2：rebase shared branch
```bash
# 隊友正在基於 feature/xxx 做事
git rebase -i feature/xxx
git push --force
# 隊友 pull → 一團亂
```

**溝通**。或直接不 rebase shared branch。

### 危險 3：解衝突解錯
rebase 每個 commit 都要套一次，衝突可能出現多次。要謹慎。

用 `rerere`（記住衝突解法）可以減輕：
```bash
git config --global rerere.enabled true
```

之後 git 記住你怎麼解的，下次類似衝突自動套。

## 6.8 Merge 策略

```bash
git merge feature                          # 預設
git merge --strategy=ours feature          # 保留 main、忽略 feature 的改動
git merge -X theirs feature                # 衝突時自動選 feature 的
git merge -X ours feature                  # 衝突時自動選 main 的
git merge -X ignore-all-space feature      # 忽略空白差異
```

### `ours` strategy vs `-X ours`
- `--strategy=ours`：**完全不合併 feature 的改動**，只是紀錄「我已合併」
- `-X ours`：正常合併，**衝突時**用 main 的

名字相近行為差很遠，注意。

## 6.9 中斷 merge / rebase

### Merge 中衝突 → 放棄
```bash
git merge --abort
```

### Rebase 中衝突 → 放棄
```bash
git rebase --abort
```

回到開始前狀態。

### 繼續
解完衝突後：
```bash
git add <resolved-files>
git merge --continue     # 或 git rebase --continue
```

### 跳過這個 commit（rebase only）
```bash
git rebase --skip
```

## 6.10 `git merge --squash`

```bash
git merge --squash feature
```

把 feature 的所有改動**塞進 index**，但**不產生 merge commit**。你自己 `git commit` 產生一個新 commit 涵蓋所有 feature 的改動。

用途：feature 有 50 個 WIP commit，不想帶進 main——用 squash 合成一個乾淨 commit。

```bash
git switch main
git merge --squash feature
git commit -m "Add feature X"
# feature branch 還在，commit 沒動，但 main 上看到一個整合的 commit
```

## 6.11 圖解常見情境

### 情境 A：feature 開發期間 main 動了
```
... A - B (main)
     \
      C - D (feature)

main 有新 commit:

... A - B - E - F (main)
     \
      C - D (feature)
```

**選項 1: merge main into feature**
```bash
git switch feature
git merge main

... A - B - E - F (main)
     \        \
      C - D - M (feature)
```

**選項 2: rebase feature onto main（推薦）**
```bash
git switch feature
git rebase main

... A - B - E - F (main)
                 \
                  C' - D' (feature)
```

Option 2 歷史更乾淨。前提：feature 是你一人的。

### 情境 B：PR 就緒，要合回 main

**路徑 1：rebase then fast-forward**
```bash
git switch feature
git rebase main       # 確保 feature 在 main 最新之上
git switch main
git merge feature     # fast-forward（因為 feature 領先 main）
```

結果是線性歷史：
```
... main ... C' - D' (HEAD, main, feature)
```

**路徑 2：--no-ff merge**
```bash
git switch main
git merge --no-ff feature
```

結果：
```
... main ... - M
         \   /
          C - D
```

看得出來「這幾個 commit 是一起的」。

## 6.12 實用建議

1. **設 `pull.rebase=true`**：日常同步不產生 merge commit。
2. **Feature branch 個人用時 rebase**：歷史乾淨。
3. **開 PR 前 rebase 到 main 最新**：避免衝突要 reviewer 處理。
4. **PR merge 策略看專案慣例**：有些強制 squash，有些強制 rebase。
5. **看到 merge commit 訊息「Merge branch 'main' of ...」**：基本是 `git pull` 產生的無意義 merge，該設 `pull.rebase=true`。

## 6.13 練習

1. 建 sandbox，做兩個 branch 有分歧的 commit。試 merge、rebase、rebase --interactive，看歷史差異。
2. 故意製造一個衝突，分別練 merge 時解衝突、rebase 時解衝突。
3. 試 `--no-ff` 和沒 `--no-ff` 的差別。

## 本章重點
- **Merge**：保留歷史，產 merge commit。**Rebase**：線性化，改寫 hash。
- 黃金法則：**rebase 私人 branch，merge public branch**
- 預設 `pull.rebase=true`
- Feature branch 同步 main 用 rebase
- PR 合併策略看團隊慣例，GitHub 有三種
- `rerere.enabled=true` 幫你省解衝突的時間
- 遇到陌生狀態 `git merge --abort` / `git rebase --abort` 逃生
