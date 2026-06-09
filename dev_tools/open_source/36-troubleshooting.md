# Ch 36 — 疑難雜症排解

> **目標**：協作中總會遇到「災難現場」——本章是急救手冊：救壞掉的 PR、撤銷錯誤的操作、歷史改寫的代價（filter-repo）、誤推 secret 的正確處理、detached HEAD、把 commit 提交到錯的 branch 等。學完你遇到災難不慌——因為 git 幾乎什麼都救得回（reflog，Ch 9）。

> **環境**：git 2.40+、GitHub。前置：Ch 6（rebase/force）、Ch 9（reflog/救援）、Ch 34（secret 安全）。

## 為什麼要有一章「急救手冊」

協作久了，你一定會遇到：commit 到錯的 branch、PR 不知怎麼搞壞了、誤推了 secret、rebase 災難、誤刪東西。新手遇到這些會慌、會用更危險的操作把事情搞更糟。

但好消息（承 Ch 9）：**git 幾乎什麼都救得回**——commit 過的東西在 reflog 裡（90 天）。這章是常見災難的急救步驟集，讓你遇到時冷靜處理，而不是慌亂中把小問題變大災難。

> 總原則：**遇到災難，先停下來，別慌亂操作。** 第一個動作通常是 `git status` + `git reflog`（看現況、看歷史）。多數「災難」是可逆的——先搞清楚狀況再動，別在慌亂中 `reset --hard` 或 force-push 把事情搞更糟。

## 災難一：commit 到錯的 branch

最常見的低級錯——你以為在 feature branch，其實在 main，commit 了。

```bash
# 在 main 上不小心 commit 了（還沒 push）
# 救法：把那些 commit 搬到正確的 branch

git switch -c feature              # 從現在位置開 feature（commit 還在這）
git switch main
git reset --hard origin/main       # main 退回到遠端狀態（移除誤 commit）
git switch feature                 # commit 都在 feature 了
```

或用 cherry-pick（Ch 9）把特定 commit 挑到對的 branch。核心：commit 沒消失（在 reflog），只是「搬到對的 branch + 把錯的 branch 退回」。

> 如果已經 push 到 main（且 main 受保護擋不住或你有權限）：別 force-push 共享的 main（黃金法則，Ch 6）！用 `git revert` 產生「反向 commit」撤銷（見災難五），而非改寫歷史。

## 災難二：rebase / reset 搞砸了

承 Ch 9——這是 reflog 的主場：

```bash
# rebase 把歷史搞亂了 / reset --hard 退過頭
git reflog                         # 找搞砸「之前」的那個 HEAD@{n}
git reset --hard HEAD@{5}          # 回到那個乾淨狀態
# 或 git reset --hard ORIG_HEAD（git 在危險操作前存的原 HEAD）
```

`git reflog` + `git reset --hard HEAD@{n}` 能救回幾乎任何「commit 過但被弄丟」的狀態。`ORIG_HEAD` 是 rebase/merge/reset 前的捷徑。**只有「從沒 commit 過」的工作目錄改動救不回**（被 reset --hard 覆蓋的未提交改動）——所以重要的先 commit/stash（Ch 9）。

## 災難三：誤推 secret（最嚴重）

承 Ch 34——把 API key / token / 密碼 / 私鑰 commit 並 push 了。**這是最嚴重的，處理順序錯了會出大事。**

```
   正確的處理順序（順序很重要！）：

   1. 立刻撤銷/輪換那個 secret  ← 第一優先！假設它已洩漏
      （改密碼、撤銷 token、重新產生 key）
      理由：一 push 到公開 repo，爬蟲幾秒內就掃到。即使你下一秒刪掉，
            也來不及——它已經被抓走了。所以「讓那個 secret 失效」最優先。

   2. 從 git 歷史清除（次要）
      - 刪 commit 救不了（歷史還在、且可能已被 clone/fork/快取）
      - 要用 git filter-repo 或 BFG 從整個歷史清除
```

```bash
# 撤銷 secret 後，從歷史清除（git-filter-repo，推薦）
pip install git-filter-repo
git filter-repo --path config/secrets.env --invert-paths   # 從所有歷史移除該檔
# 或用 BFG Repo-Cleaner
# 然後 force-push（這會改寫歷史，協調團隊重新 clone）
git push --force --all
```

> 鐵則：**誤推 secret，第一件事永遠是「撤銷那個 secret」，不是「刪 commit」。** 因為 push 出去的瞬間（尤其公開 repo）就該假設它已洩漏——爬蟲、fork、快取、別人的 clone 都可能已經有了。從歷史清除是「亡羊補牢」（防止更多人看到），但救不了「已經洩漏」這件事。GitHub 的 secret scanning 偵測到已知格式的 secret 還會自動通知/協助撤銷。

## 災難四：detached HEAD（HEAD 飄了）

承 Ch 3——你 `git checkout <某 commit>`（不是 branch），進入 detached HEAD（HEAD 直接指 commit，不在任何 branch）。在這狀態 commit，那些 commit 沒有 branch 接住，switch 走就「弄丟」了：

```bash
# 看到 "You are in 'detached HEAD' state"
# 如果你在這狀態做了 commit 想保留：
git switch -c new-branch           # 開一個 branch 接住這些 commit（在 switch 走之前！）

# 如果已經 switch 走了、commit 弄丟了：
git reflog                         # 找那些 detached 的 commit
git branch rescued <那個hash>      # 救回來
```

detached HEAD 本身不危險（看舊版本、實驗用），危險的是「在裡面 commit 後 switch 走沒接住」。記得：要保留就先開 branch。

## 災難五：已 push 的 commit 要撤銷

已經 push 到共享 branch（main）的 commit 發現錯了。**不能 force-push 改寫**（黃金法則，Ch 6——別人可能已基於它）。用 **revert**（產生一個「反向」的新 commit 來抵消）：

```bash
git revert <壞commit>              # 產生一個撤銷它的新 commit（不改寫歷史）
git push                           # 安全（沒改寫，只是加了反向 commit）

# 撤銷一個 merge commit（特殊，要指定保留哪個 parent）
git revert -m 1 <merge-commit>     # -m 1 = 保留第一個 parent（通常是主線）
```

revert vs reset：
- **reset**：改寫歷史（移除 commit）——只能用在**沒 push / 個人 branch**。
- **revert**：加一個反向 commit（不改寫）——用在**已 push / 共享 branch**（安全）。

> revert merge commit 的陷阱：revert 一個 merge 後，那條 branch 的內容「在 git 眼中已經被 revert 過」——之後想重新 merge 它，git 會以為已經合過、不重合。這是個經典坑（要 revert the revert 或重新處理）。merge 後發現要撤銷，謹慎處理（查 git 文件的 "reverting a merge"）。

## 災難六：branch 開錯位置 / 要搬一段 commit

你的 branch 基於錯的 base（如該基於 main 卻基於 develop），或要把一段 commit 搬到別處。用 `git rebase --onto`（Ch 6 進階提過）：

```bash
# feature 基於錯的 base，想改基於 main
git rebase --onto main <錯的base> feature
#   把 feature 上「<錯的base> 之後」的 commit，搬到 main 上

# 只搬一段 commit（從 A 到 B 搬到 newbase）
git rebase --onto newbase A B
```

`rebase --onto` 是「精準搬移一段 commit」的手術刀——branch 開錯位置、要把部分 commit 移植時用。較進階，但遇到「我的 branch 基於錯的東西」時是正解。

## 災難七：PR 不知怎麼搞壞了

PR 的 branch 亂了（誤 merge、誤 rebase、衝突解錯、混入不該有的 commit）：

```bash
# 1. 先看狀況
git log --oneline --graph main..HEAD    # 我這 branch 比 main 多了什麼
git reflog                              # 我做過什麼

# 2. 常見救法：
# - 混入不該有的 commit → git rebase -i 把它 drop（Ch 7）
# - 整個亂了 → reflog 找回乾淨狀態，或基於最新 main 重新 cherry-pick 你要的 commit
# - 想重來 → 從 main 開新 branch，cherry-pick 你真正要的改動過去（Ch 9）

# 3. 你個人的 PR branch 可以 force-push 修正（Ch 6，沒別人基於它）
git push --force-with-lease
```

對個人 PR branch，「重建」常比「修補」乾淨——從最新 main 開新 branch、cherry-pick 你要的改動、force-push。因為是你個人的 branch（沒別人基於它），這安全。

## 災難八：誤刪 branch / 誤刪 commit

承 Ch 9：

```bash
git branch -D feature              # 誤刪（含未合併 commit）
git reflog                         # 找那 branch 最後的 commit hash
git branch feature <那個hash>       # 重建

# 遠端 branch 被誤刪
# 如果本地還有：git push origin feature 重建
# 本地也沒了：reflog（本地的）或請有 clone 的人重推
```

## 通用急救心法

遇到任何 git 災難：

```
   1. 停下來，別慌亂操作（慌亂的 reset --hard / force-push 把小事變大事）
   2. git status —— 現在是什麼狀態？
   3. git reflog —— 我做過什麼？哪裡是乾淨的？
   4. 想清楚再動 —— 多數災難可逆（commit 過的在 reflog）
   5. 不確定就先備份：git branch backup-現狀（開個 branch 標記現況）
   6. 真的不確定就問 / 查 —— 別在慌亂中做不可逆的事
```

最重要的兩個安全網：**reflog**（救回弄丟的 commit）和**先開 backup branch**（動手前標記現況）。有這兩個，你幾乎不可能真的搞丟東西。

## 踩雷集錦

1. **慌亂中操作把事情搞更糟**：第一動作是停下來 + `git status` + `git reflog`，不是慌亂 reset/force-push。
2. **誤推 secret 只刪 commit**：第一件事是**撤銷 secret**（已洩漏）！刪 commit 救不了已洩漏的 key（Ch 34）。
3. **對共享 branch 用 reset/force 撤銷已 push 的 commit**：違反黃金法則（Ch 6）。用 `revert`（加反向 commit，不改寫）。
4. **detached HEAD 裡 commit 後 switch 走沒接住**：要保留先 `git switch -c branch`。弄丟了用 reflog 救。
5. **revert merge 後想重新 merge 卻不合**：revert merge 的經典坑。merge 後要撤銷謹慎處理。
6. **不知道 reflog 能救，rebase 災難就重做整個 branch**：reflog 救絕大多數「弄丟」的情況。先 reflog。
7. **filter-repo 改寫歷史不協調團隊**：清除 secret 用 filter-repo 會改寫整個歷史（所有 hash 變），團隊要重新 clone——要協調公告。

## 進階：再往深一層

- **`git filter-repo`**（取代舊的 `filter-branch`）：改寫整個歷史的工具——清除 secret、移除大檔案、改 email。極強但極危險（改寫所有 commit），用前備份、用後協調團隊重 clone。
- **BFG Repo-Cleaner**：清除歷史中 secret/大檔案的快速工具（比 filter-repo 簡單，針對性強）。
- **`git reflog` 的時限**：reflog 條目 90 天（可達）/30 天（不可達）後過期被 gc。超過就難救（`git fsck --lost-found` 是最後手段，Ch 9）。
- **`ORIG_HEAD` / `MERGE_HEAD`**：git 在危險操作前/中存的參照，快速復原的捷徑。
- **`git rerere`**（Ch 8）：反覆解同樣衝突自動套——也算一種「救援」（救你免於重複勞動）。
- **GitHub 的保護兜底**（Ch 23）：branch protection 的「禁 force-push / 禁刪除」從系統層防止很多災難——這也是為什麼要設它（事前預防 > 事後救援）。
- **`git stash` 救未提交改動**（Ch 9）：要做危險操作但有未 commit 的改動，先 stash 保護它。

## 動手練習

1. 製造「commit 到錯 branch」（在 main commit），練習搬到 feature + reset main——確認 commit 沒丟。
2. 故意 `git reset --hard HEAD~3`，用 reflog + `git reset --hard HEAD@{1}`（或 ORIG_HEAD）救回。
3. 練習 `git revert` 撤銷一個（假裝已 push 的）commit——對比 reset（理解 revert 不改寫歷史）。
4. 進入 detached HEAD（`git switch --detach <某commit>`）、commit、switch 走、用 reflog 救回那個 commit。
5. （安全環境）模擬誤推 secret 的處理：先「撤銷」（假裝輪換 key），再用 `git filter-repo` 從歷史移除那個檔案——體驗正確順序。
6. 用 `git rebase --onto` 把一個 branch 從錯的 base 搬到對的 base。
7. 動手前養成 `git branch backup-現狀` 的習慣——多一道保險。

## 本章重點整理

- 總原則：遇到災難先停下來，`git status` + `git reflog` 看清楚再動；多數災難可逆（commit 過的在 reflog 90 天）。
- commit 到錯 branch：搬到對的 branch + reset 錯的 branch（commit 沒丟）。
- rebase/reset 搞砸：reflog + `reset --hard HEAD@{n}` 或 ORIG_HEAD 救回。
- **誤推 secret：第一件事是撤銷/輪換 secret**（已洩漏），再用 filter-repo/BFG 清歷史（救不了已洩漏，只防更多人看到）。
- 已 push 到共享 branch 的 commit：用 `revert`（加反向 commit，不改寫）不用 reset/force（黃金法則）。
- detached HEAD 要保留 commit 先開 branch；誤刪 branch/commit 用 reflog 救。
- 兩大安全網：reflog（救弄丟的）+ 動手前開 backup branch（標記現況）。

## 自我檢核

- [ ] 遇到 git 災難，第一個動作該是什麼（而不是什麼）？
- [ ] 誤推 secret 的正確處理順序是什麼？為什麼「撤銷 secret」比「刪 commit」優先？
- [ ] 撤銷一個「已 push 到共享 main」的錯 commit，該用 revert 還是 reset？為什麼？
- [ ] reflog 能救什麼、救不了什麼？兩大安全網是什麼？
- [ ] filter-repo 改寫歷史後，為什麼要協調團隊？

## 延伸閱讀

### 工具 / 文件

- **[git-filter-repo](https://github.com/newren/git-filter-repo)** 與 **[BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)**
  - **讀哪裡**:清除歷史中 secret/大檔案的用法。
  - **和本章的關聯**:誤推 secret 後清歷史的工具（記得先撤銷 secret）。

- **[GitHub Docs: Removing sensitive data from a repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)**
  - **讀哪裡**:整篇——正確的處理順序（撤銷 + 清歷史）。
  - **和本章的關聯**:誤推 secret 的官方權威步驟。

### 書籍 / 站點

- **[Pro Git, Ch 7.7 (Reset Demystified)](https://git-scm.com/book/en/v2/Git-Tools-Reset-Demystified)** 與 **[Data Recovery](https://git-scm.com/book/en/v2/Git-Internals-Maintenance-and-Data-Recovery)**
  - **讀哪幾章**:reset 三態的徹底解釋、reflog/fsck 救援。
  - **和本章的關聯**:reset/救援機制的官方完整版。

- **[Oh Shit, Git!?!](https://ohshitgit.com/)** / **[Dangit, Git!?!](https://dangitgit.com/)**
  - **這是什麼**:常見 git 災難的急救速查（白話、實用）。
  - **為什麼值得讀**:本章的「速查卡」版，遇到災難時直接查。

急救手冊備好了，最後一章把視野拉到長期——開源生涯：怎麼持續貢獻、建立聲譽、從貢獻者走到 maintainer，以及開源與職涯。

→ [Ch 37 開源生涯](./37-open-source-career.md)
