# Ch 6 — rebase 與它的爭議

> **目標**：搞懂 rebase 到底做什麼（搬移 commit、改寫歷史）、它和 merge 的根本差異、最重要的「黃金法則」（別 rebase 公開歷史，否則害死隊友），以及 merge vs rebase 之爭的兩派理由。這是協作中威力最大、也最容易闖禍的操作。

> **環境**：git 2.40+。

## 為什麼 rebase 值得謹慎對待

rebase 是 git 最強大的工具之一，但也是新手最容易闖禍的地方——用對了讓歷史乾淨漂亮，用錯了會覆蓋隊友的 commit、製造混亂。

很多團隊對「該用 merge 還是 rebase」吵翻天。這章不只教 rebase 怎麼用，更要讓你懂**它在改寫什麼、為什麼有條鐵律不能違反、什麼時候該用什麼不該用**。懂了這些，你才不會在團隊裡因為一個 rebase 闖大禍。

## 先建立直覺：merge 是「接起來」，rebase 是「搬過去」

同樣是整合，merge 和 rebase 的哲學完全不同。

```
   分岔的起點：
              D ◄─── E        [feature]
             /
   A ◄─ B ◄─ C ◄─── F        [main]


   merge（Ch 5）：把兩條接起來，留下分岔記錄
              D ◄─── E
             /        \
   A ◄─ B ◄─ C         M      [main]   ← 歷史有分岔，有 merge commit
             \        /
              F ──────


   rebase：把 feature 的 commit「搬」到 main 後面，假裝從沒分岔過
   A ◄─ B ◄─ C ◄─ F ◄─ D' ◄─ E'        [feature]
                              
   ← D', E' 是「重做」的 D, E（內容一樣但是新 commit！），歷史變直線
```

merge 的態度：「我們確實分頭做了，把成果接起來，誠實記錄這個分岔。」
rebase 的態度：「假裝我是從 main 的最新狀態開始做的——把我的 commit 一個個重新套用到 main 後面。」

關鍵：rebase 後的 `D'`、`E'` **不是原本的 D、E**——它們是內容相同但**全新的 commit**（不同的 hash）。原本的 D、E 被「拋棄」（reflog 還找得到，Ch 9）。**rebase 改寫了歷史。** 這是它和 merge 最本質的差別，也是黃金法則的根源。

## rebase 怎麼運作

```bash
git switch feature
git rebase main
#   1. 找 feature 和 main 的共同祖先（C）
#   2. 把 feature 從 C 以來的 commit（D, E）暫存起來
#   3. 把 feature 移到 main 的頂端（F）
#   4. 把 D, E 一個個「重新套用」（cherry-pick）到 F 後面 → D', E'
#   5. 過程中每個 commit 都可能衝突（要逐個解決，Ch 8）
```

注意第 5 點：rebase 是**一個 commit 一個 commit 重放**，所以衝突可能發生很多次（每個被搬的 commit 各一次），不像 merge 一次解決。這是 rebase 衝突比較煩的原因（練習 B 會體驗）。

rebase 中途的控制：

```bash
git rebase --continue        # 解完一個衝突，繼續下一個
git rebase --skip            # 跳過當前 commit（少用）
git rebase --abort           # 放棄整個 rebase，回到 rebase 前（安全網）
```

## 黃金法則：別 rebase 已經公開的歷史

這是整章最重要的一句話，**違反它會害死隊友**：

> **不要 rebase（改寫）已經 push 出去、別人可能基於它工作的 commit。**

為什麼？因為 rebase 改寫歷史（產生新 hash）。如果你 rebase 了已 push 的 branch，再 push 就需要 `--force`（因為遠端的舊歷史和你的新歷史對不上）。而如果隊友已經基於舊的歷史工作了：

```
   你 push 了 feature（含 commit E，hash abc123）
   隊友 pull 了，基於 E 開始工作
        │
   你 rebase feature，E 變成 E'（hash xyz789），force-push
        │
   隊友再 pull → 災難：
   - 隊友本地還有舊的 E（abc123）
   - 遠端現在是 E'（xyz789）
   - git 以為這是兩條不同歷史，隊友會看到重複的 commit、詭異的衝突
   - 隊友要花時間清理你製造的混亂
```

所以鐵律是：**rebase 只用在「還沒分享給別人」的本地 commit。** 一旦 push 了、別人可能用了，就別再 rebase（除非整個團隊講好、且只有你一人在那 branch 上——例如你自己的 PR branch，下面說）。

> 例外（重要）：**你自己的 PR feature branch**，即使 push 了，rebase + force-push 通常是 OK 的——因為那是「你個人的」branch，按慣例沒有別人會基於它工作（大家基於 main）。很多專案甚至**要求**你 rebase PR branch 來保持乾淨歷史。判準：「**有沒有別人會基於這條 branch 工作？**」——你的個人 PR branch：沒有，可 rebase；共享的 main/develop：有，絕不 rebase。

## force-push：rebase 的必然後果（與安全版）

rebase 已 push 的 branch 後，再 push 會被拒（歷史對不上），需要強制：

```bash
git push --force                      # 危險：無條件覆蓋遠端
git push --force-with-lease           # 安全版：只在「遠端是我預期的樣子」時才覆蓋
```

**永遠用 `--force-with-lease` 而非 `--force`**：`--force-with-lease` 會檢查「遠端的 branch 還是我上次看到的樣子嗎」——如果別人在你 rebase 期間 push 了新東西，它會**拒絕**覆蓋（保護你不會蓋掉別人剛 push 的）。`--force` 則不管三七二十一直接蓋，可能無聲覆蓋別人的工作。

```ini
# 可以設 alias 強迫自己用安全版
git config --global alias.pushf "push --force-with-lease"
```

## merge vs rebase：兩派之爭

這是 git 世界的聖戰。沒有絕對答案，理解兩派理由你才能在不同團隊適應。

| | merge | rebase |
|---|---|---|
| 歷史 | 保留真實分岔（有 merge commit）| 改成直線（假裝沒分岔）|
| 誠實性 | 誠實記錄「何時分頭、何時合併」 | 「美化」成線性故事 |
| 可讀性 | 複雜專案的圖可能很亂（一堆分岔線）| 直線好讀、好 bisect |
| 安全性 | 不改寫歷史，安全 | 改寫歷史，有黃金法則風險 |
| 衝突 | 一次解決 | 可能逐 commit 多次解決 |

**merge 派**：「歷史應該誠實反映真實發生的事。分頭做了就該留分岔記錄。rebase 是篡改歷史，而且危險。」

**rebase 派**：「沒人想看一堆無意義的 merge commit 和交錯的線。直線歷史好讀、好 bisect、好 revert。分岔的細節沒有保留價值。」

**實務上的常見折衷**：
- **本地 / 個人 PR branch**：用 rebase 保持乾淨（整理自己的 commit、跟上 main）。
- **整合進共享主線（main）**：用 merge（或 squash merge，Ch 10），保留「這個 PR 在這裡進來」的記錄。
- **絕不 rebase 共享 branch**（main/develop）——黃金法則。

很多專案的具體選擇寫在 CONTRIBUTING（Ch 16）。進新團隊先問/讀：「我們的 PR 要 rebase 還是 merge？」

## 常見用途：用 rebase 跟上 main

協作中 rebase 最常見的正當用途——你的 PR branch 落後了，把它「重新基於」最新的 main：

```bash
git switch feature              # 你的 PR branch
git fetch origin
git rebase origin/main          # 把你的 commit 搬到最新 main 後面
#   解決可能的衝突...
git push --force-with-lease     # 因為改寫了歷史，需要 force（安全版）
```

這讓你的 PR 看起來像「剛從最新 main 開始做的」，reviewer 看到的是乾淨的、基於最新狀態的改動。這是「你自己的 PR branch 可以 rebase」的典型場景。

> rebase vs merge 跟上 main：你也可以 `git merge origin/main` 進你的 feature（不改寫歷史、不用 force）。差別：merge 會在你的 branch 留下 merge commit（歷史有分岔），rebase 保持直線但要 force。兩者都讓你跟上 main，選哪個看專案偏好（Ch 25 深入長命 branch 的同步策略）。

## 踩雷集錦

1. **rebase 共享 branch（main/develop）**：違反黃金法則，害死所有基於它工作的人。共享 branch 永遠用 merge。
2. **用 `--force` 而非 `--force-with-lease`**：`--force` 可能無聲蓋掉別人剛 push 的。永遠用 `--force-with-lease`。
3. **以為 rebase 後的 commit 是原本的**：不是，是新 hash 的「重做版」。原 commit 被拋棄（reflog 可救，Ch 9）。
4. **rebase 衝突解到崩潰**：rebase 逐 commit 重放，衝突可能很多次。每次 `git rebase --continue`；受不了就 `--abort` 回去改用 merge。
5. **rebase 到一半 `git commit` 而非 `--continue`**：解完衝突要 `git add` + `git rebase --continue`，不是 `git commit`（會多造一個 commit 打亂流程）。
6. **pull 預設 rebase 沒搞清楚**：`pull.rebase=true` 會讓 pull 用 rebase 整合——如果你不懂可能困惑。Ch 0 設成 false（用 merge）較直觀。
7. **不知道 rebase 能 abort**：任何時候 `git rebase --abort` 回到 rebase 前的乾淨狀態。這是安全網，放心試。

## 進階：再往深一層

- **interactive rebase**（Ch 7）：`rebase -i` 是 rebase 的強化版，能 squash/reword/reorder/drop——練習 A 用過，Ch 7 深入。它也是改寫歷史，黃金法則同樣適用。
- **`git rebase --onto`**：把一段 commit 從一個 base 搬到另一個 base（不只跟上 main）——救「branch 開錯位置」的利器（Ch 36）。
- **`rebase.autoStash`**：rebase 前自動 stash 未提交的改動、完後還原，省得手動 stash（Ch 9）。
- **`rerere`**（Ch 0/8）：對 rebase 尤其有用——逐 commit 重放時常遇到「同一個衝突」，rerere 記住解法自動套。
- **pull --rebase 的場景**：你的本地 main 有未 push 的 commit、遠端也有新 commit，`pull --rebase` 把你的 commit 搬到遠端後面，避免無意義的 merge commit。對「本機 main 偶爾 commit」的人很順。
- **rebase merge commit**：`rebase --rebase-merges` 能在 rebase 時保留 merge 結構（預設 rebase 會把 merge 攤平）。複雜 branch 結構才需要。

## 動手練習

1. 建分岔（feature 和 main 各有新 commit），先 `git merge` 看歷史圖（有 merge commit、分岔線）；reset 回去再 `git rebase main` 看歷史圖（直線）——對比兩種結果。
2. rebase 後 `git log` 看 commit 的 hash 變了（D→D'），確認「rebase 產生新 commit」。
3. 製造一個 rebase 衝突（feature 和 main 改同一行），逐 commit 解（`git add` + `git rebase --continue`），中途試一次 `git rebase --abort` 看它回到原點。
4. push 一個 branch、rebase 它、用 `git push --force-with-lease`——體驗 rebase 後的 force-push 流程。
5. 模擬黃金法則的災難：兩個 clone（你+「隊友」），你 push branch、隊友 pull、你 rebase + force-push、隊友再 pull——觀察隊友端的混亂（重複 commit/詭異衝突）。深刻體會為什麼別 rebase 共享歷史。
6. 用 `--force` 和 `--force-with-lease` 各試一次（在隊友剛 push 新東西的情境），看 `--force-with-lease` 怎麼擋住你、`--force` 怎麼蓋掉。

## 本章重點整理

- merge 是「接起來」（保留分岔、有 merge commit）；rebase 是「搬過去」（改寫成直線、產生新 hash 的 commit）。
- **黃金法則**：絕不 rebase 已 push、別人可能基於它工作的歷史（共享 branch）。判準：「有沒有別人會基於這條 branch 工作？」
- 例外：你自己的 PR feature branch（沒人基於它）通常可以 rebase + force-push，很多專案還要求這樣。
- rebase 後 force-push **永遠用 `--force-with-lease`**（檢查遠端、防無聲覆蓋），不用 `--force`。
- merge vs rebase 是文化之爭；常見折衷：個人 branch 用 rebase 保持乾淨、共享主線用 merge、絕不 rebase 共享 branch。

## 自我檢核

- [ ] rebase 後的 commit 和原本的是同一個嗎？這對「黃金法則」意味著什麼？
- [ ] 黃金法則是什麼？判斷「能不能 rebase 這條 branch」的關鍵問題是？
- [ ] 為什麼你自己的 PR branch 通常可以 rebase，但 main 絕對不行？
- [ ] `--force` 和 `--force-with-lease` 差在哪？為什麼永遠用後者？
- [ ] merge 派和 rebase 派各自的核心理由是什麼？實務常見的折衷是？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 3.6 — Rebasing](https://git-scm.com/book/en/v2/Git-Branching-Rebasing)**
  - **讀哪幾章**：3.6 全部，尤其 "The Perils of Rebasing"（黃金法則的權威解釋與災難圖解）。
  - **和本章的關聯**：本章黃金法則的官方完整版。

### 部落格 / 文章

- **[Merging vs. Rebasing](https://www.atlassian.com/git/tutorials/merging-vs-rebasing)** — Atlassian
  - **這篇說什麼**：merge vs rebase 兩派的完整對照、golden rule、各種工作流。
  - **讀哪裡**：整篇；本章「兩派之爭」的延伸，圖解豐富。
  - **為什麼值得讀**：這個主題寫得最全面的一篇，含 interactive rebase 與 `--onto`。

- **[--force considered harmful; understanding git's --force-with-lease](https://blog.developer.atlassian.com/force-with-lease/)** — Atlassian Developer
  - **這篇說什麼**：為什麼 `--force` 危險、`--force-with-lease` 怎麼保護你。
  - **為什麼值得讀**：把 force-push 的安全問題講透。

rebase 的基本搬移懂了，下一章看它最實用的形態：interactive rebase——練習 A 用過的，現在把整套機制學齊（squash/reword/edit/reorder/drop）。

→ [Ch 7 interactive rebase](./07-interactive-rebase.md)
