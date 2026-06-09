# Ch 5 — merge 的本質

> **目標**：搞懂 merge 到底做了什麼——fast-forward vs 3-way merge、merge commit 的結構、git 怎麼自動合併、什麼時候會衝突。merge 是整合別人改動的最基本方式，理解它你才看得懂協作歷史長的樣子。

> **環境**：git 2.40+。

## 為什麼要理解 merge

協作的本質是「把分開做的工作合在一起」。merge 就是 git 做這件事的核心操作——你 `git pull`（內含 merge）、PR 被 "Merge" 進 main、整合 upstream 更新（Ch 25），全是 merge。

但很多人對 merge 的理解停在「按一下就好」，結果看到 merge commit 一頭霧水、遇到衝突就慌。這章把 merge 的內部機制拆開，讓你看懂歷史圖、知道衝突為什麼發生、為什麼有時 merge 不產生 merge commit（fast-forward）。

## 先建立直覺：把兩條分岔的路接起來

回到 Ch 3 的「branch 是便利貼」。當兩條 branch 從同一點分岔、各自 commit，merge 就是「把這兩條岔路的成果合成一個新狀態」。

```
   分岔後：
              D ◄─── E        [feature]   ← feature 加了 D, E
             /
   A ◄─ B ◄─ C
             \
              F               [main]      ← main 同時加了 F

   merge feature 進 main：
              D ◄─── E
             /        \
   A ◄─ B ◄─ C         M     [main]       ← M 是「merge commit」，有兩個 parent
             \        /
              F ──────
```

`M` 是一個特殊的 commit——**它有兩個 parent**（E 和 F），代表「這裡把兩條歷史合起來了」。普通 commit 只有一個 parent；merge commit 有兩個（或更多）。這是 merge 在歷史圖上的指紋。

## fast-forward：最簡單的 merge（不產生 merge commit）

不是所有 merge 都產生 merge commit。如果**目標 branch 從分岔點以來沒有任何新 commit**，merge 就只是「把便利貼往前移」——叫 **fast-forward**。

```
   merge 前（main 沒動，feature 領先）：
   A ◄─ B ◄─ C ◄─ D ◄─ E
             ▲           ▲
          [main]    [feature]

   git switch main; git merge feature  →  fast-forward：
   A ◄─ B ◄─ C ◄─ D ◄─ E
                         ▲
                    [main][feature]    ← main 直接「快轉」到 E，沒有 merge commit
```

因為 main 沒有自己的新 commit，git 不需要「合併」任何東西——只要把 main 這張便利貼移到 feature 的位置。歷史保持一條直線。

```bash
git switch main
git merge feature                  # 若可 fast-forward，預設就 ff（直線歷史）
git merge --no-ff feature          # 強制產生 merge commit（即使能 ff）
git merge --ff-only feature        # 只允許 ff，不能就報錯（保證直線歷史）
```

`--no-ff` vs 預設的差別在歷史圖：`--no-ff` 永遠造一個 merge commit，留下「這裡合進了一個 feature」的明確記錄（很多團隊偏好，因為一眼看出 feature 邊界）；預設 ff 則讓歷史更扁平。Ch 22 的 branching model 會涉及這個選擇。

## 3-way merge：真正的合併

當兩條 branch 都有新 commit（真的分岔了，像最上面那張圖），fast-forward 不可能——git 要做 **3-way merge**：

```
   git 看三個版本：
   1. base：分岔點（C，兩條的共同祖先）
   2. ours：當前 branch 的最新（F，main）
   3. theirs：要合進來的（E，feature）

   對每個檔案 / 每一塊改動，git 比對：
   - 只有 ours 改了 → 用 ours 的版本
   - 只有 theirs 改了 → 用 theirs 的版本
   - 兩邊都改了「同一塊」→ 衝突！要人來決定（Ch 8）
   - 兩邊都沒改 → 保持原樣
```

「3-way」就是因為它看三個版本（base + 兩邊），靠 base 判斷「誰改了什麼」。這比「直接比兩個檔案」聰明——有了共同祖先當基準，git 能分辨「這行是 A 加的」還是「B 刪的」，自動合併大部分情況。

```bash
git switch main
git merge feature
#   若無衝突：自動產生 merge commit
#   若有衝突：停下來，要你解決（Ch 8）
```

> 為什麼需要 base（共同祖先）：沒有 base，git 只能看到「兩個不同的檔案」，無法判斷差異是「一邊新增」還是「另一邊刪除」。有了 base，它能算出「相對於共同起點，各自做了什麼」，才能合理合併。這就是 3-way 比 2-way 強的關鍵。

## 什麼時候會衝突

衝突**只在一種情況發生：兩條 branch 改了「同一塊區域」（git 無法自動決定要哪個）。**

會衝突：
- 兩邊都改了同一個檔案的同一行（或相鄰行）
- 一邊改了某檔案、另一邊刪了它
- 兩邊都新增同名檔案但內容不同

**不會**衝突（git 自動合併）：
- 兩邊改不同檔案
- 兩邊改同檔案的**不同區域**（離得夠遠）
- 一邊新增檔案、另一邊沒碰

```
   不衝突（改不同區域，git 自動合併）：
   feature 改了第 1 行，main 改了第 50 行 → git 兩個都收，合併成功

   衝突（改同一區域）：
   feature 把第 10 行改成 X，main 把第 10 行改成 Y → git 不知道要 X 還 Y，停下來問你
```

理解這點，你就懂為什麼「常常同步」能減少衝突（Ch 25）——分岔越久，兩邊改的東西越多，撞在一起的機率越高。Ch 8 專講怎麼解衝突。

## merge commit 的真相

merge commit 和普通 commit 一樣是個 commit，只是：
- **有兩個（或更多）parent**（被合併的兩條歷史的頂端）
- 它的「內容」是合併後的結果（3-way merge 的產物）
- 預設 message 是 "Merge branch 'feature' into main"（可改）

```bash
git log --oneline --graph          # 用圖看 merge 結構（協作必用）
git show <merge-commit>            # 看 merge commit
git log --merges                   # 只看 merge commit
git cat-file -p <merge-commit>    # 看它的兩個 parent
```

`git log --graph` 畫出的 ASCII 分岔圖，是你讀懂協作歷史的關鍵工具——一眼看出哪裡分岔、哪裡合併。

## 一個完整的 merge 場景

整合別人的改動（不用 pull 的自動合併，手動掌控，承 Ch 4）：

```bash
git fetch origin                   # 先下載，看清楚
git log --oneline --graph main origin/main   # 看分岔狀況
git switch main
git merge origin/main              # 整合
#   情況一：fast-forward（你的 main 沒新 commit）→ 直線快轉
#   情況二：3-way（都有新 commit）→ 產生 merge commit
#   情況三：衝突 → 停下來解決（Ch 8），解完 git merge --continue
```

如果 merge 到一半想放棄：

```bash
git merge --abort                  # 回到 merge 前的乾淨狀態
```

## 踩雷集錦

1. **以為 merge 一定產生 merge commit**：fast-forward 不會（目標 branch 沒新 commit 時）。想強制留記錄用 `--no-ff`。
2. **看到 merge commit 慌張**：它只是有兩個 parent 的普通 commit，代表這裡合過。`git log --graph` 看結構。
3. **不懂衝突為什麼發生**：只在「兩邊改同一塊」時。改不同區域 git 自動合併。分岔越久衝突越多——勤同步（Ch 25）。
4. **merge 衝突解到一半放棄不知怎麼辦**：`git merge --abort` 回到 merge 前。安全。
5. **`pull` 製造意外 merge commit**：`pull` 內含 merge（Ch 4）。想要直線歷史考慮 `pull --ff-only` 或 rebase（Ch 6）。
6. **merge 後才發現合錯了**：merge 也是 commit，能 revert（`git revert -m 1 <merge>`，`-m 1` 指定保留哪個 parent 的主線）。但 revert merge 有後續陷阱（Ch 36）。
7. **把「自動合併成功」當成「合併正確」**：git 自動合併是「文字層面沒撞」，**不保證邏輯正確**——兩邊各改不同函式可能語意上不相容，merge 成功但程式壞了。merge 後一定要跑測試。

## 進階：再往深一層

- **merge 演算法**：預設用 `ort`（git 2.34+ 取代舊的 `recursive`），處理 rename、criss-cross merge 更好。`-s` 可指定策略（`ours`/`theirs`/`octopus`）。
- **`git merge -s ours`**：產生 merge commit 但**完全忽略**對方的改動（保留 ours）——標記「這條 branch 已處理過、不要它的內容」時用，很特殊。
- **octopus merge**：一次合併多條 branch（多於兩個 parent）。少見，多用於把多個 topic branch 一次整合。
- **merge base**：`git merge-base A B` 找兩條 branch 的共同祖先（3-way 的 base）。理解它能 debug 「為什麼這個 merge 衝突這麼多」（共同祖先太遠）。
- **rerere**（Ch 0 設了 `rerere.enabled`）：記住你解過的衝突，下次同樣衝突自動套用——反覆 merge/rebase 長命 branch 時省大量重複工。Ch 8 細講。
- **semantic conflict**：文字不衝突但邏輯衝突（A 改了函式簽名、B 在別處呼叫舊簽名）——git 抓不到，只有測試/編譯能抓。這是「自動 merge 成功 ≠ 正確」的本質。

## 動手練習

1. 建兩條 branch 從同一點分岔，各改**不同檔案**，merge——觀察 git 自動合併成功、產生 merge commit。
2. 改成各改**同一檔案的不同區域**（一個改開頭、一個改結尾），merge——確認仍自動成功（不衝突）。
3. 改成各改**同一行**，merge——這次衝突了（解法 Ch 8，先 `git merge --abort` 回去）。
4. 製造一個能 fast-forward 的情境（main 沒新 commit），`git merge feature` 看它直線快轉、無 merge commit；再用 `--no-ff` 比較。
5. `git log --oneline --graph --all` 畫出你的分岔/合併歷史，學會讀這張圖。
6. 做一個 merge 後跑測試，故意製造一個「文字不衝突但邏輯壞掉」的 semantic conflict（一邊改函式名、一邊用舊名），體會「merge 成功 ≠ 正確」。

## 本章重點整理

- merge 把兩條分岔的歷史合起來；merge commit 有兩個 parent（歷史圖上的合併點）。
- fast-forward：目標 branch 沒新 commit 時，merge 只是把便利貼快轉，不產生 merge commit（直線歷史）。
- 3-way merge：兩邊都有新 commit 時，git 用共同祖先（base）+ 兩邊（ours/theirs）三方比對，自動合併大部分。
- 衝突只在「兩邊改同一塊」發生；改不同區域 git 自動合併。分岔越久衝突越多。
- 自動合併成功只代表「文字沒撞」，**不保證邏輯正確**——merge 後必跑測試（semantic conflict）。

## 自我檢核

- [ ] merge commit 和普通 commit 的結構差別是什麼？
- [ ] 什麼情況 merge 是 fast-forward（不產生 merge commit）？`--no-ff` 改變什麼？
- [ ] 3-way merge 為什麼需要「共同祖先」？它比直接比兩個檔案強在哪？
- [ ] 衝突在什麼情況發生、什麼情況不會？為什麼勤同步能減少衝突？
- [ ] 為什麼「git 自動合併成功」不代表「合併是正確的」？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 3.2 — Basic Branching and Merging](https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging)**
  - **讀哪幾章**：3.2（fast-forward vs 3-way 的圖解）。
  - **和本章的關聯**：本章機制的官方圖解版。

### 部落格 / 文章

- **[Merge strategies and algorithms](https://git-scm.com/docs/merge-strategies)** — git 官方文件
  - **讀哪裡**：ort、recursive、ours/theirs 策略。
  - **和本章的關聯**：merge 演算法的進階參考。

- **[Git Merge: A Visual Guide](https://www.atlassian.com/git/tutorials/using-branches/git-merge)** — Atlassian
  - **這篇說什麼**：fast-forward vs 3-way 的視覺化教學。
  - **為什麼值得讀**：圖多、清楚，補強直覺。

merge 把歷史合成分岔的樣子。下一章是它最大的對手——rebase，它把歷史改成直線，但代價是改寫歷史（且有一條鐵律）。

→ [Ch 6 rebase 與它的爭議](./06-rebase.md)
