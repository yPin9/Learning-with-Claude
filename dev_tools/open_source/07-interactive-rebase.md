# Ch 7 — interactive rebase

> **目標**：把練習 A 用過的 `git rebase -i` 學齊整套——pick/reword/edit/squash/fixup/drop/reorder、`--autosquash` 工作流、拆分一個 commit。這是「送出前整理歷史」的主力工具，也是讓你的 PR 乾淨到 reviewer 想按讚的關鍵技能。

> **環境**：git 2.40+。前置：[Ch 6 rebase](./06-rebase.md)（黃金法則同樣適用）。

## 為什麼 interactive rebase 是協作的日常工具

Ch 6 的普通 rebase 是「把 commit 搬到別處」。**interactive rebase（`rebase -i`）是「在搬的過程中，逐一決定每個 commit 怎麼處理」**——合併、改訊息、刪除、重排、拆分。

協作中你天天需要它：
- 發 PR 前把雜亂的 "wip"/"fix" 整理成 atomic commit（練習 A）
- reviewer 說「這三個 commit 應該合成一個」，你 squash
- 發現某個舊 commit 的訊息打錯，你 reword
- 把不小心混進去的 debug commit drop 掉

練習 A 讓你照著做過一次，這章把每個動作的用途、時機、陷阱講齊。

## 先建立直覺：一份可編輯的「待辦清單」

`git rebase -i <base>` 打開一個編輯器，列出 base 之後的所有 commit，每行一個動作。**你編輯這份清單，git 就照你的清單重新「播放」這些 commit。**

```
   git rebase -i HEAD~4   打開：

   pick a1b2c3 Add login form
   pick d4e5f6 wip
   pick g7h8i9 fix login validation
   pick j0k1l2 Update docs

   ← 你把每行的動作（pick）改成你要的，存檔，git 就照辦
```

關鍵心智模型：**這是一份「重播計畫」**。commit 由上到下 = 由舊到新（和 `git log` 相反！）。你改計畫，git 從 base 開始，按計畫一條條重做。

> 承 Ch 6：interactive rebase **也是改寫歷史**（產生新 hash）。黃金法則照樣適用——只整理還沒分享、或你個人的 PR branch，別動共享歷史。

## 七個動作

每行開頭的關鍵字（可用全名或縮寫）：

| 動作 | 縮寫 | 作用 |
|---|---|---|
| `pick` | `p` | 照原樣保留這個 commit |
| `reword` | `r` | 保留 commit，但讓你改 message |
| `edit` | `e` | 停在這個 commit，讓你修改它的內容（拆分/修改） |
| `squash` | `s` | 併進**上一個** commit，**合併兩者的 message** |
| `fixup` | `f` | 併進**上一個** commit，**丟棄**這個的 message |
| `drop` | `d` | 刪除這個 commit（或直接刪掉那一行） |
| `reorder` | — | 沒有關鍵字——直接調整行的順序就是重排 |

### squash vs fixup：最常用、最該分清

兩者都「把這個 commit 併進上一個」，差別只在 message：

```
   原本：
   pick   aaa Add feature
   squash bbb more work on feature      ← squash：合併後讓你編輯兩個 message
   fixup  ccc fix typo in feature       ← fixup：直接丟棄 ccc 的 message

   squash → 結果 commit 的 message 是「Add feature」+「more work...」(你編輯)
   fixup  → 結果 commit 的 message 只有「Add feature」(ccc 的訊息消失)
```

實務：整理一連串開發 commit 時，第一個用 `pick`/`reword`（定調 message），後面的修正用 `fixup`（丟棄那些 "wip"/"fix" 垃圾訊息）。練習 A 就是這個模式。

## reword：改 commit message

最簡單實用的。發現某個 commit 訊息寫爛了：

```bash
git rebase -i HEAD~3
# 把目標那行的 pick 改成 reword，存檔
# git 會在處理到它時打開編輯器讓你重寫 message
```

> 只想改**最後一個** commit 的 message？不用 rebase，直接 `git commit --amend`。`--amend` 是「修改最近一個 commit」（message 或內容），是最輕量的歷史修改。注意它也改 hash（改寫歷史），已 push 的最後一個 commit amend 後也要 force-with-lease。

## edit：拆分或修改一個舊 commit

`edit` 讓 rebase **停在**那個 commit，你可以修改它的內容，甚至把它拆成多個。這是練習 A 延伸挑戰的「拆 commit」。

拆分一個 commit 的流程：

```bash
git rebase -i HEAD~3
# 把要拆的那行改成 edit，存檔
# git 停在那個 commit（已套用）

git reset HEAD^          # 把這個 commit「拆開」——撤銷 commit 但保留改動在工作區
# 現在改動都是 unstaged，你重新分批 commit：
git add file1.py
git commit -m "First atomic part"
git add file2.py
git commit -m "Second atomic part"

git rebase --continue    # 拆完，繼續 rebase
```

`git reset HEAD^`（軟撤銷上一個 commit，保留改動）是拆 commit 的關鍵——它把一坨改動退回工作區，讓你重新分組。

## reorder：重排 commit

直接調整行的順序就重排了。用途：把邏輯相關的 commit 排在一起、或調整成更合理的閱讀順序。

```
   原本：              重排後：
   pick aaa A          pick aaa A
   pick bbb B          pick ccc C    ← 把 C 移到 B 前面
   pick ccc C          pick bbb B
```

> 重排陷阱：如果兩個 commit 改了**同一塊**程式碼，調換順序可能產生衝突（git 重放時撞上）。重排獨立的 commit 安全，重排相依的要準備解衝突（Ch 8）。

## drop：刪除 commit

把該行刪掉、或改成 `drop`。用途：移除誤入的 debug commit、實驗性的 commit。

```
   pick aaa Add feature
   drop bbb DEBUG: print everything    ← 這個 commit 整個消失
   pick ccc Add tests
```

> drop 同樣有相依陷阱：刪掉一個被後面 commit 依賴的 commit（後面的改動建立在它上面），後面會衝突。

## --autosquash：流暢的整理工作流

最優雅的 interactive rebase 用法。開發時，當你想「這個改動是要補回某個舊 commit 的」，用 `--fixup`/`--squash` 標記：

```bash
# 開發中，發現 commit aaa 漏了東西
git add fix-for-aaa.py
git commit --fixup=aaa        # 做一個特殊的 "fixup! Add feature" commit

# 最後整理時：
git rebase -i --autosquash HEAD~5
#   git 自動把 fixup! commit 排到 aaa 後面、並標成 fixup
#   你只要存檔確認即可，不用手動排
```

`--autosquash` 把「標記要併哪 + 自動排好」自動化，比手動找位置改 squash 順手太多。設 `git config rebase.autosquash true` 讓 `-i` 預設啟用。這是進階協作者整理 PR 的標準流程。

## 一個完整的 PR 整理流程

發 PR 前的典型整理（綜合本章）：

```bash
git log --oneline main..HEAD     # 看我這條 branch 的 commit
git rebase -i main               # 整理 main 之後的所有 commit
#   - 把 "wip"/"fix" 用 fixup 併進主 commit
#   - 用 reword 把保留的 commit 訊息寫清楚（Ch 2）
#   - drop 掉誤入的 debug commit
#   - 必要時 reorder 成邏輯順序
git log --oneline main..HEAD     # 確認乾淨了
git push --force-with-lease      # 已 push 過的 PR branch（Ch 6）
```

reviewer 打開你的 PR，看到的是幾個聚焦、訊息清楚的 commit，而不是 20 個 "wip"——這在 review 第一印象上差很多。

## 踩雷集錦

1. **rebase -i 共享歷史**：黃金法則（Ch 6）對 interactive rebase 同樣適用。只整理個人/未分享的 commit。
2. **squash/fixup 搞混**：squash 保留並合併 message，fixup 丟棄 message。整理垃圾訊息用 fixup。
3. **commit 順序看反**：rebase -i 清單裡，**最舊在最上**（和 `git log` 相反）。改錯行就整理錯。
4. **edit 後用 `git commit` 而非流程**：拆 commit 要 `git reset HEAD^` 退回改動再重新 commit，最後 `git rebase --continue`。不是直接 commit。
5. **重排/drop 相依的 commit 產生衝突**：改了同一塊的 commit 換序或刪除會撞。解衝突（Ch 8）或調整計畫。
6. **編輯器存錯/清單全清空**：rebase -i 的清單若存成空的，rebase 會中止（什麼都不做）。不是刪 commit 的方法（刪要用 drop）。
7. **rebase 到一半迷路**：`git status` 會告訴你 rebase 進行到哪、下一步該做什麼。卡住就 `git rebase --abort` 回原點重來。

## 進階：再往深一層

- **`git commit --amend` 是最小的 rebase**：改最後一個 commit。`--amend --no-edit` 只改內容不改 message（把忘了加的檔案補進上一個 commit）。
- **`exec`**：rebase -i 清單裡可加 `exec <command>` 行，在每個 commit 後跑一個指令（如 `exec make test`）——驗證「每個 commit 都能通過測試」（為 bisect 鋪路，Ch 35）。
- **`break`**：`break` 行讓 rebase 在該處暫停，讓你檢查/操作，再 `--continue`。
- **`rebase --update-refs`**（git 2.38+）：rebase 時自動更新中間的其他 branch 指標——stacked PR（Ch 26）的好幫手。
- **rebase root**：`git rebase -i --root` 連最初的 commit 都能整理（沒有 base 的情況）。
- **與 rerere 配合**：reorder/drop 常引發重複衝突，rerere（Ch 0/8）記住解法自動套，省大量重工。

## 動手練習

1. 重做練習 A 的整理，但這次刻意用 `reword` + `fixup` + `drop` + `reorder` 四種動作各至少一次。
2. 練 `edit` 拆 commit：做一個「同時改兩個檔案」的 commit，用 `edit` + `git reset HEAD^` 把它拆成兩個 atomic commit。
3. 練 `--autosquash`：做幾個 commit、用 `git commit --fixup=<sha>` 標記修正、`git rebase -i --autosquash` 看它自動排好。
4. 用 `git commit --amend` 補一個忘了加的檔案到最後一個 commit。
5. 故意 reorder 兩個改同一行的 commit，遇到衝突——體會「重排相依 commit 會撞」（解法 Ch 8，先 abort）。
6. 在 rebase -i 清單加一行 `exec git log --oneline -1`，看它在每個 commit 後執行。

## 本章重點整理

- interactive rebase 是「可編輯的重播計畫」；清單由上到下=由舊到新（和 log 相反）。
- 七個動作：pick（保留）/reword（改訊息）/edit（改內容/拆分）/squash（併+合訊息）/fixup（併+丟訊息）/drop（刪）/reorder（調順序）。
- squash vs fixup：都併進上一個，squash 保留兩者訊息、fixup 丟棄——整理垃圾訊息用 fixup。
- 拆 commit：`edit` + `git reset HEAD^` 退回改動 + 重新分批 commit。
- `--autosquash` + `git commit --fixup` 是最流暢的整理工作流；`--amend` 是最小的歷史修改。
- 黃金法則（Ch 6）同樣適用——只整理未分享/個人 PR branch。

## 自我檢核

- [ ] rebase -i 清單裡 commit 的順序（最舊在哪）？和 `git log` 一樣嗎？
- [ ] squash 和 fixup 差在哪？整理一串含 "wip" 的開發 commit 該用哪個？
- [ ] 怎麼把一個 commit 拆成兩個 atomic commit？
- [ ] `--autosquash` + `git commit --fixup` 解決了什麼麻煩？
- [ ] 只想改最後一個 commit 的訊息/內容，最輕量的方法是什麼？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 7.6 — Rewriting History](https://git-scm.com/book/en/v2/Git-Tools-Rewriting-History)**
  - **讀哪幾章**：7.6 全部——`--amend`、interactive rebase 的每個動作、拆 commit、`filter-branch`（Ch 36 用）。
  - **和本章的關聯**：本章每個操作的官方完整版。

### 部落格 / 文章

- **[Auto-squashing Git Commits](https://thoughtbot.com/blog/autosquashing-git-commits)** — thoughtbot
  - **這篇說什麼**：`--fixup` + `--autosquash` 工作流的實戰教學。
  - **為什麼值得讀**：把本章最優雅的工作流講得清楚實用。

- **[A Branch in Time (a story about revision histories)](https://tekin.co.uk/2019/02/a-talk-about-revision-histories)** — Tekin Suleyman
  - **這篇說什麼**：為什麼乾淨的歷史對團隊長期有價值（含 reword/squash 的論證）。
  - **為什麼值得讀**：給你「為什麼花時間整理歷史」的說服力。

整理歷史的工具齊了，但 rebase/merge 都會遇到衝突。下一章專攻協作中最讓人緊張的時刻：解衝突。

→ [Ch 8 衝突解決](./08-conflict-resolution.md)
