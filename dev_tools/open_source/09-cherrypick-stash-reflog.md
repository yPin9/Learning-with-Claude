# Ch 9 — cherry-pick / stash / reflog

> **目標**：補齊協作常用的三個工具——cherry-pick（把單一 commit 挑到別處）、stash（暫存未完成的工作）、reflog（git 的時光機，救回「弄丟」的 commit）。前兩個是日常便利，reflog 是讓你「不怕弄壞 git」的安全網。

> **環境**：git 2.40+。

## 為什麼這三個放一起

它們不是同一類，但都是協作中你會反覆用到的「實用零件」：

- **cherry-pick**：「我只要那一個 commit，不要整條 branch」——hotfix 跨 branch、把某個修復挑到 release 分支。
- **stash**：「我做到一半但要先切去處理別的」——緊急 bug、要切 branch 但不想 commit 半成品。
- **reflog**：「我好像把東西弄丟了」——rebase 出錯、reset 過頭、刪錯 branch 的救命稻草。

學會 reflog 尤其重要——它讓你敢大膽用 rebase/reset（前面幾章的強力操作），因為**幾乎沒有什麼是真的救不回來的**。

## cherry-pick：挑一個 commit 過來

`git cherry-pick <commit>` 把某個 commit 的改動「複製」一份套用到你當前的 branch。

```
   main:     A ◄─ B ◄─ C
   feature:        D ◄─ E ◄─ F      ← 你只想要 E 這個 commit（一個 hotfix）

   git switch main
   git cherry-pick E

   main:     A ◄─ B ◄─ C ◄─ E'      ← E' 是 E 的「複製」（新 hash，內容相同）
```

注意 `E'` 是新 commit（新 hash）——cherry-pick 是「重做這個改動」，不是搬移原 commit（原 E 還在 feature 上）。

```bash
git cherry-pick a1b2c3              # 挑一個
git cherry-pick a1b2c3 d4e5f6       # 挑多個
git cherry-pick a1b2c3..f7g8h9      # 挑一個範圍
git cherry-pick --continue          # 解完衝突後繼續（cherry-pick 也會衝突）
git cherry-pick --abort             # 放棄
```

協作中的典型用途：

- **hotfix 跨 branch**：bug 修復在 main，但要也套用到正在維護的 `release-2.0` 分支——cherry-pick 那個修復 commit 過去。
- **挑出有用的 commit**：別人的 PR 有 5 個 commit，你只想要其中 1 個——cherry-pick 它。
- **救出 commit**：branch 開錯位置，但上面有好 commit——cherry-pick 到對的 branch。

> 陷阱：cherry-pick 同一個改動到多條 branch，會產生**內容相同但 hash 不同**的 commit。之後 merge 這些 branch 時，git 可能不認得它們是「同一個改動」，導致重複或衝突。能用 merge 整合就別過度 cherry-pick——它是「挑單一改動」的工具，不是常規整合手段。

## stash：把工作暫存起來

你正改到一半，突然要切去別的 branch（緊急 bug、要看別人的 PR）。但半成品不想 commit、又不能帶著未提交的改動切 branch（git 可能擋你或帶過去汙染）。`git stash` 把當前未提交的改動「收進抽屜」，工作目錄變乾淨：

```bash
git stash                    # 把未提交的改動收起來，工作目錄回到乾淨的 HEAD
git stash list               # 看抽屜裡有什麼
#   stash@{0}: WIP on feature: a1b2c3 ...
git stash pop                # 取出最近一個 stash 並套回（從抽屜移除）
git stash apply              # 套回但保留在抽屜（可套到多個 branch）
git stash drop               # 丟棄一個 stash
```

```
   工作中（未提交改動）
        │ git stash
        ▼
   工作目錄乾淨 ← 改動收進抽屜 stash@{0}
        │ 切 branch、處理急事、切回來
        ▼
   git stash pop → 改動套回，繼續做
```

進階用法：

```bash
git stash -u                 # 連未追蹤的新檔案也 stash（預設只 stash 已追蹤的）
git stash -m "wip on login"  # 給 stash 命名（好認）
git stash show -p stash@{0}  # 看某個 stash 的內容
git stash branch new-branch  # 從一個 stash 開一條新 branch（適合「這 stash 其實該獨立」）
```

> 認識論誠實：stash 方便，但它是個「容易遺忘的抽屜」——很多人 stash 了一堆東西忘記，後來搞不清哪個是哪個。更乾淨的替代常常是「直接開一條 branch commit 半成品」（branch 便宜，Ch 3）。stash 適合「真的只是切一下馬上回來」的短暫情境；長一點的中斷，開 branch 更好管理。

## reflog：git 的時光機

這是本章最重要的——**reflog 讓你救回幾乎任何「弄丟」的東西**。

關鍵事實：當你 rebase、reset、刪 branch、amend，那些「被拋棄」的 commit **並沒有立刻消失**。git 把「HEAD 去過哪裡」的每一步都記在 **reflog** 裡，被拋棄的 commit 還在物件庫裡（預設保留約 90 天才被 gc 回收）。

```bash
git reflog                   # 看 HEAD 的移動歷史
#   a1b2c3 HEAD@{0}: rebase finished: ...
#   d4e5f6 HEAD@{1}: rebase: ...
#   g7h8i9 HEAD@{2}: commit: the commit I thought I lost   ← 在這！
#   ...
```

reflog 記錄了每次 HEAD 變動（commit、checkout、rebase、reset、merge…），每筆有個 `HEAD@{n}` 參照。要救回任何一個狀態：

```bash
git reset --hard HEAD@{2}    # 把當前 branch 拉回到 reflog 的那個狀態
# 或從那個 commit 開一條 branch 救出來：
git branch recovered HEAD@{2}
git switch recovered
```

### reflog 救援的經典場景

**場景一：rebase 弄壞了**

```bash
git rebase main              # 結果搞砸了，commit 亂了
git reflog                   # 找 "rebase (start)" 之前的那個 HEAD@{n}
git reset --hard HEAD@{5}    # 拉回 rebase 之前的狀態，當作沒發生過
```

**場景二：reset --hard 過頭，commit 不見了**

```bash
git reset --hard HEAD~3      # 糟糕，那三個 commit 是我要的！
git reflog                   # 它們還在 reflog 裡
git reset --hard HEAD@{1}    # 回到 reset 之前
```

**場景三：刪錯 branch（Ch 3 預告過）**

```bash
git branch -D feature        # 刪掉了，但上面有沒合併的 commit！
git reflog                   # 找那個 branch 最後的 commit hash
git branch feature <那個hash>  # 重建 branch
```

**場景四：amend 後想找回原本的 commit**

```bash
git commit --amend           # 改了，但想找回 amend 前的版本
git reflog                   # amend 前的 commit 還在
```

> 心法：**只要你曾經 commit 過（哪怕後來被 rebase/reset/amend 拋棄），它就在 reflog 裡，90 天內救得回。** 真正救不回的只有「從沒 commit 過的工作目錄改動」（被 `git reset --hard`/`git checkout` 覆蓋的未提交改動——這些沒進 git 的記錄，所以 stash/commit 你重要的改動）。記住 reflog，你就敢大膽用前面幾章的強力操作。

## 三者的協作場景串連

```bash
# 你在 feature 改到一半，緊急 hotfix 要處理
git stash                          # 暫存半成品

git switch main
git switch -c hotfix/crash
# ...修 bug, commit...
git switch main && git merge hotfix/crash   # 或發 PR

# hotfix 也要套到 release-1.0 分支
git switch release-1.0
git cherry-pick <hotfix commit>    # 把修復挑過去

# 回去繼續原本的工作
git switch feature
git stash pop                      # 取回半成品

# ...結果 rebase 時手滑搞砸了...
git reflog                         # 找回 rebase 前的狀態
git reset --hard HEAD@{4}          # 救回來
```

stash 暫存、cherry-pick 跨 branch 套用、reflog 兜底救援——協作日常的三件實用工具。

## 踩雷集錦

1. **cherry-pick 當常規整合手段**：產生重複的 commit（同內容不同 hash），日後 merge 易混亂。它是「挑單一改動」工具，整合用 merge。
2. **stash 堆積忘記**：stash 一堆忘了哪個是哪個。短暫中斷才用 stash；長一點開 branch commit。`git stash list` 定期清。
3. **stash 不含未追蹤檔案**：預設 `git stash` 只收已追蹤的改動，新建的檔案（untracked）不收，切 branch 後它們還在汙染。要連新檔用 `git stash -u`。
4. **以為 reset --hard 的東西永遠沒了**：commit 過的在 reflog 救得回（90 天）。但**未提交**的工作目錄改動被 `reset --hard` 覆蓋就真沒了——重要改動先 commit/stash。
5. **不知道 reflog 存在，rebase 搞砸就重寫**：reflog 能救絕大多數「弄丟」的情況。遇到災難先 `git reflog`，別急著重做。
6. **reflog 是本地的**：reflog 只在你的本機，不會 push、clone 不會帶。別人弄丟的東西你的 reflog 救不了（他自己的 reflog 才行）。
7. **`HEAD@{n}` vs `branch@{n}`**：`HEAD@{n}` 是 HEAD 的移動史；`git reflog show <branch>` 看特定 branch 的。救援時注意用對。

## 進階：再往深一層

- **`git reflog expire`**：reflog 條目預設 90 天（可達 commit）/ 30 天（不可達）過期。理解這個時限——超過就可能被 gc 回收，救不回。
- **`git fsck --lost-found`**：reflog 也找不到時（極端情況），fsck 能掃出「dangling」的 commit（無人指向但還沒被回收的）。最後的救援手段。
- **cherry-pick `-x`**：`git cherry-pick -x <commit>` 在 message 加一行 "(cherry picked from commit ...)"，記錄來源——維護 release 分支時追溯有用。
- **`git stash --keep-index`**：stash 但保留已 staged 的部分——「我想先測 staged 的、把 unstaged 的收起來」。
- **rebase 的安全來自 reflog + ORIG_HEAD**：危險操作（rebase/merge/reset）前，git 會把原 HEAD 存進 `ORIG_HEAD`，`git reset --hard ORIG_HEAD` 是快速復原的捷徑。
- **autostash**（Ch 6）：`rebase.autoStash=true` 讓 rebase 自動 stash/pop，省得手動——本質是 stash + reflog 的組合應用。

## 動手練習

1. cherry-pick：在 feature 做三個 commit，切到 main，只 cherry-pick 中間那個過來，確認 main 只多了那一個改動。
2. stash：改到一半 `git stash`，切 branch 處理別的，切回 `git stash pop` 取回。再試 `git stash -u`（含新檔）。
3. **reflog 救 rebase**：故意把一個 branch rebase 搞砸（解衝突亂解），`git reflog` 找 rebase 前狀態，`git reset --hard` 救回。
4. **reflog 救刪除的 branch**：做幾個 commit 在某 branch、`git branch -D` 刪掉、用 reflog 找回 commit hash、重建 branch。
5. **reflog 救 reset**：`git reset --hard HEAD~3`，再用 reflog 救回那三個 commit。
6. 對比：`git reset --hard` 一個有**未提交**改動的工作目錄，確認那些未提交改動真的沒了（reflog 救不回未 commit 的）——體會「重要的先 commit/stash」。

## 本章重點整理

- cherry-pick：把單一 commit 的改動複製到當前 branch（新 hash）；用於 hotfix 跨 branch、挑出有用 commit。別當常規整合手段。
- stash：把未提交改動收進抽屜，工作目錄變乾淨，切 branch 後再 pop 回來；短暫中斷用，長中斷開 branch 更好。`-u` 含未追蹤檔。
- **reflog 是 git 的時光機**：記錄 HEAD 每次移動，被 rebase/reset/amend/刪 branch「弄丟」的 commit 都在裡面（90 天），`git reset --hard HEAD@{n}` 或開 branch 救回。
- 救不回的只有「從沒 commit 過」的工作目錄改動——重要的先 commit/stash。
- reflog 是本地的，救不了別人弄丟的東西。

## 自我檢核

- [ ] cherry-pick 和 rebase（搬 commit）有什麼不同？為什麼不該拿 cherry-pick 當常規整合？
- [ ] stash 預設收不收未追蹤的新檔案？怎麼一起收？什麼情況用 branch 比 stash 好？
- [ ] rebase/reset 搞砸了，你的第一個救援動作是什麼？
- [ ] 什麼東西是 reflog 也救不回的？這給你什麼操作習慣？
- [ ] 為什麼說「學會 reflog 你就敢大膽用 rebase/reset」？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 7.3 (Stashing)](https://git-scm.com/book/en/v2/Git-Tools-Stashing-and-Cleaning)** 與 **[Ch 10.x — reflog/maintenance](https://git-scm.com/book/en/v2/Git-Internals-Maintenance-and-Data-Recovery)**
  - **讀哪幾章**：7.3（stash 完整）；Data Recovery 那節（reflog + fsck 救援的權威）。
  - **和本章的關聯**：本章三工具的官方完整版。

### 部落格 / 文章

- **[Git reflog: the time machine you didn't know you had](https://www.atlassian.com/git/tutorials/rewriting-history/git-reflog)** — Atlassian
  - **這篇說什麼**：reflog 救援各種災難的實例。
  - **為什麼值得讀**：把 reflog 的救命能力講得最實用，建立「不怕弄壞」的信心。

- **[git-cherry-pick documentation](https://git-scm.com/docs/git-cherry-pick)** — git 官方
  - **讀哪裡**：`-x`、範圍語法、衝突處理。
  - **和本章的關聯**：cherry-pick 的完整選項。

Part 2 的中階 git 都齊了。用練習 B 把 rebase、衝突、reflog 綜合起來——解一個複雜的 rebase 衝突，再用 reflog 從「災難」中救回來。

→ [練習 B：複雜 rebase 衝突 + reflog 救援](./practice-b-rebase-reflog.md)
