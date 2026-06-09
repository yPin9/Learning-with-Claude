# Ch 8 — 衝突解決

> **目標**：把「解衝突」從讓人冒冷汗的時刻，變成你能冷靜處理的例行公事。理解衝突為什麼發生、conflict marker 怎麼讀、merge 衝突 vs rebase 衝突的差異、解衝突的標準流程、工具，以及 rerere 怎麼讓你不用解第二次同樣的衝突。

> **環境**：git 2.40+。前置：[Ch 5 merge](./05-merge.md)、[Ch 6 rebase](./06-rebase.md)。

## 為什麼解衝突值得專章

衝突是協作的必然——只要多人改同一份程式碼，遲早撞在一起。新手一遇衝突就慌：滿屏的 `<<<<<<<`、不知道刪哪個、怕弄壞。結果要嘛亂刪一通弄壞 code，要嘛 `--abort` 逃避（但問題還在）。

其實衝突解決有一套清楚的流程和心智模型。學會它，衝突就從「災難」變成「git 把決定權交還給我，因為它自己決定不了」——一個再正常不過的協作環節。

## 先建立直覺：git 把它無法決定的交給你

回到 Ch 5：衝突**只在兩條 branch 改了「同一塊」時發生**。git 能自動合併大部分改動（兩邊改不同地方），但當兩邊改了同一行，git **不猜**——它不知道你要哪個，所以停下來，在檔案裡標出衝突的地方，把決定權交給你。

```
   git 的態度：
   「第 10 行，你的 branch 改成 A，要合進來的改成 B。
     我不知道你想要 A、B、還是兩者結合。
     我把兩個版本都標出來，你告訴我最終要什麼。」
```

衝突不是 git 壞了，是 git 誠實地說「這個我決定不了，你來」。理解這點，心態就穩了。

## conflict marker：讀懂衝突標記

當衝突發生，git 在檔案裡插入標記：

```python
def greet(name):
<<<<<<< HEAD
    return f"Hello, {name}!"          # ← 你當前 branch（HEAD / ours）的版本
=======
    return f"Hi there, {name}~"       # ← 要合進來（theirs）的版本
>>>>>>> feature-greeting
```

三個標記：

- `<<<<<<< HEAD`：衝突開始，下面是**你當前 branch（ours）**的版本。
- `=======`：分隔線，上面 ours、下面 theirs。
- `>>>>>>> feature-greeting`：衝突結束，上面是**要合進來（theirs）**的版本。

你要做的：**刪掉標記，留下最終想要的內容**（可能是 ours、theirs、或兩者結合、或全新的寫法）。

```python
# 解決後（假設你決定結合兩者風格）：
def greet(name):
    return f"Hello there, {name}!"
```

> ours/theirs 的方向會反！在 **merge** 時，ours（HEAD）是你當前 branch；但在 **rebase** 時，因為 git 是「把你的 commit 重放到對方上面」，ours 和 theirs 的意義會對調——ours 變成「你正在 rebase 到的目標」，theirs 變成「你正在重放的 commit」。這常讓人困惑，下面詳述。

## 解衝突的標準流程

不管 merge 還是 rebase，流程一致：

```bash
# 1. 衝突發生，git 停下來
git status                    # 看哪些檔案衝突（"Unmerged paths"）

# 2. 打開每個衝突檔案，找 <<<<<<< 標記，編輯成最終想要的內容
#    刪掉所有 marker（<<<<<<<, =======, >>>>>>>）

# 3. 標記為已解決
git add <解決好的檔案>

# 4. 繼續
git merge --continue          # 若是 merge
git rebase --continue         # 若是 rebase（會繼續處理下一個 commit）

# 隨時想放棄：
git merge --abort   /   git rebase --abort
```

核心三步：**找衝突 → 編輯解決 → `git add` → continue**。`git status` 全程是你的導航——它告訴你哪些檔案還沒解、下一步該做什麼。

> 解完衝突一定要 `git add`：`git add` 是告訴 git「這個檔案我解決好了」。沒 add 就 continue，git 會說還有未解決的。`git status` 會把已解決（staged）和未解決（unmerged）分開列。

## merge 衝突 vs rebase 衝突

兩者解法一樣（找標記、編輯、add、continue），但有兩個重要差異：

### 差異一：衝突次數

- **merge**：一次性。所有衝突在**一個** merge commit 裡解決，解完一次就結束。
- **rebase**：逐 commit。rebase 把你的 commit 一個個重放（Ch 6），**每個 commit 都可能各自衝突**，可能要解很多次（每次 `git add` + `git rebase --continue` 處理下一個）。

```
   merge：解 1 次衝突 → 完成
   rebase（重放 5 個 commit）：可能解 5 次衝突（每個 commit 一次）
```

這是 rebase 衝突比較煩的原因。如果 rebase 衝突解到崩潰，可以 `--abort` 改用 merge（一次解決）。

### 差異二：ours/theirs 方向相反

- **merge**：`HEAD`（ours）= 你當前所在的 branch；theirs = 你要 merge 進來的。直覺。
- **rebase**：方向**對調**。因為 rebase 是「把你的 commit 重放到目標 base 上」，git 視角是「base 是 ours、你的 commit 是 theirs」。所以 `<<<<<<< HEAD` 下面是 **base（你 rebase 到的那個，如 main）** 的版本，theirs 是**你自己的 commit**。

這個對調是 rebase 衝突最大的混淆點。**判準**：別硬記 ours/theirs，看標記的 branch 名（`>>>>>>>` 後面）和你正在做的操作，搞清楚每塊是「誰的版本」再決定。`git status` 也會提示當前在 rebase 哪個 commit。

## 工具：讓解衝突不那麼痛

純手動編輯標記可行，但有工具更舒服：

```bash
# 用設定好的 merge tool（如 vimdiff, meld, vscode）
git mergetool

# VS Code / 多數 IDE 內建衝突解決 UI（顯示 ours/theirs/結果三欄，按鈕選擇）
git config --global merge.tool vscode
git config --global mergetool.vscode.cmd 'code --wait $MERGED'

# 看衝突時，啟用 diff3 風格（多顯示「共同祖先 base」，超有用）
git config --global merge.conflictStyle zdiff3
```

`zdiff3`（或舊的 `diff3`）強烈建議開——它在衝突標記裡**多顯示共同祖先（base）的原始版本**：

```python
<<<<<<< HEAD
    return f"Hello, {name}!"
||||||| base                      # ← base：兩邊改之前的原始樣子（zdiff3 才有）
    return f"Hi {name}"
=======
    return f"Hi there, {name}~"
>>>>>>> feature
```

有了 base，你能看出「ours 把 Hi 改成 Hello、theirs 把 Hi 改成 Hi there」——理解兩邊各做了什麼改動，比只看兩個結果好判斷太多。這是解衝突的一大利器，多數人不知道。

## rerere：不解第二次同樣的衝突

`rerere`（reuse recorded resolution）是 git 的隱藏神器（Ch 0 設了 `rerere.enabled`）。它**記住你解過的衝突**，下次遇到**一模一樣**的衝突時自動套用你上次的解法。

什麼時候省事：
- rebase 一條長命 branch，反覆遇到同樣的衝突（每次重放都撞）
- 解了衝突、`--abort`、又重來（rerere 記得你上次怎麼解）
- 維護長期 fork，反覆 merge upstream 遇到同樣衝突（Ch 25）

```bash
git config --global rerere.enabled true   # Ch 0 已設
# 之後解衝突，git 自動記錄；下次同衝突自動套用，git 會說：
#   "Resolved 'file.py' using previous resolution."
git rerere status                          # 看 rerere 記了什麼
```

開了 rerere，反覆性的衝突只解一次，之後 git 自動處理——對長命 branch 的協作幫助巨大。

## 一個完整的衝突解決實戰

```bash
git switch feature
git rebase main                  # 衝突！
# Auto-merging app.py
# CONFLICT (content): Merge conflict in app.py
# error: could not apply a1b2c3...

git status                       # both modified: app.py
# 打開 app.py，看到 <<<<<<< 標記（zdiff3 還會有 ||||||| base）
# 編輯成最終想要的，刪掉所有 marker

git add app.py                   # 標記已解決
git rebase --continue            # 繼續下一個 commit
# 可能又一個衝突... 重複 status → 編輯 → add → continue
# 全部解完 → rebase 完成

git push --force-with-lease      # rebase 改寫了歷史（Ch 6）
```

解完後**一定跑測試**——衝突解決是手動編輯，很容易留下語法錯誤或邏輯錯誤（你可能挑錯版本、或結合得不對）。`git status` 確認沒殘留 marker、跑測試確認 code 正確。

## 踩雷集錦

1. **沒刪乾淨 conflict marker 就 commit**：`<<<<<<<` 留在 code 裡會語法錯誤。解完搜尋一下 `<<<<<<<`/`=======`/`>>>>>>>` 確認都刪了（很多 linter/CI 會擋這個）。
2. **rebase 時 ours/theirs 搞反，挑錯版本**：rebase 的方向和 merge 相反。看 branch 名、用 zdiff3 看 base，別硬背。
3. **解完忘了 `git add`**：git 不知道你解完了，continue 會擋。`git add` 是「我解好了」的信號。
4. **rebase 逐 commit 衝突解到放棄**：受不了就 `git rebase --abort`，改用 merge（一次解決）。沒有非 rebase 不可。
5. **盲目選 ours 或 theirs**：`git checkout --ours/--theirs <file>` 整個檔案取一邊——但這常常錯（你可能需要兩邊的部分）。多數情況要手動結合，不是二選一。
6. **解衝突後不跑測試**：手動編輯極易出錯（挑錯版本、結合錯誤）。解完必跑測試/編譯。
7. **不知道 zdiff3 的存在**：只看兩個結果很難判斷，開 `merge.conflictStyle=zdiff3` 看 base，解衝突難度減半。

## 進階：再往深一層

- **`git checkout --ours/--theirs <file>`**：整個檔案直接取一邊（適合「這個檔案我確定要某一邊的完整版」，如 lock 檔、generated 檔）。但內容衝突多半要手動結合，別濫用。
- **`git diff`（衝突中）**：衝突進行中 `git diff` 顯示「相對於兩邊的差異」，幫你理解衝突範圍。
- **`git log --merge`**：衝突中列出「兩邊各自對衝突檔案做的 commit」——理解衝突的來龍去脈。
- **減少衝突的根本之道**：勤同步（Ch 25，分岔越短衝突越少）、小而頻繁的 PR（Ch 26）、團隊約定模組邊界（少改同一檔）。衝突多常是流程問題，不只是解衝突技巧問題。
- **binary 檔衝突**：binary（圖片、編譯產物）無法文字合併，只能整個取一邊（`--ours`/`--theirs`）。所以 binary 不該進版控（用 LFS 或 build 產生）。
- **語意衝突（semantic conflict）**：文字不衝突但邏輯壞（Ch 5）——git 抓不到，只有測試能抓。解完文字衝突不等於合併正確。

## 動手練習

1. 製造一個 merge 衝突（兩 branch 改同一行），開 zdiff3，看 base + ours + theirs 三個版本，手動結合解決，`git add` + `--continue`。
2. 製造一個 rebase 衝突，注意觀察 ours/theirs 的方向和 merge 相反（看 `>>>>>>>` 後的 branch 名）。
3. 設定一個 mergetool（VS Code 或 meld），用 `git mergetool` 解一次衝突，比較和手動編輯的體驗。
4. 開啟 rerere，解一個衝突、`git rebase --abort`、再 rebase 一次——看 git 自動套用你上次的解法（"using previous resolution"）。
5. 解一個衝突但故意留一個 `=======` marker 沒刪，commit，看後果（語法錯/CI 擋）——體會「marker 要刪乾淨」。
6. 解完一個「文字不衝突但邏輯衝突」的 merge（一邊改函式名、一邊呼叫舊名），跑測試看它失敗——體會 semantic conflict。

## 本章重點整理

- 衝突只在「兩邊改同一塊」發生；git 不猜，把決定權交給你——這是正常協作環節，不是故障。
- conflict marker：`<<<<<<< HEAD`（ours）`=======`（分隔）`>>>>>>> branch`（theirs）；解法=編輯成最終內容、刪光 marker。
- 標準流程：`git status` 導航 → 編輯解決 → `git add`（標記已解）→ `--continue`；隨時 `--abort`。
- merge 衝突一次解決；rebase 衝突逐 commit 可能多次，且 ours/theirs 方向相反。
- `merge.conflictStyle=zdiff3` 顯示 base（解衝突利器）；`rerere` 記住解法、同衝突自動套用。
- 解完必跑測試（手動編輯易錯、semantic conflict git 抓不到）。

## 自我檢核

- [ ] 衝突為什麼發生？為什麼說「衝突是 git 把決定權交還給你」？
- [ ] conflict marker 的三段各是什麼？解衝突後要確認什麼（marker、測試）？
- [ ] merge 和 rebase 的衝突在「次數」和「ours/theirs 方向」上各有什麼差異？
- [ ] zdiff3 多顯示了什麼？為什麼它讓解衝突變簡單？
- [ ] rerere 在什麼場景幫你省最多事？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 3.2 (Basic Merge Conflicts)](https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging)** 與 **[Ch 7.8 — Advanced Merging](https://git-scm.com/book/en/v2/Git-Tools-Advanced-Merging)**
  - **讀哪幾章**：3.2 的衝突基礎；7.8 的進階（`checkout --ours/theirs`、`merge -Xours`、diff3、rerere）。
  - **和本章的關聯**：本章基礎與進階的官方完整版。

### 部落格 / 文章

- **[Better Git conflict resolution with zdiff3](https://blog.gitbutler.com/improving-merge-conflicts/)** 類介紹 zdiff3 的文章 / git 2.35 release notes
  - **這篇說什麼**：zdiff3 比預設的衝突顯示好在哪。
  - **為什麼值得讀**：本章「開 zdiff3」建議的論證。

- **[Git rerere documentation](https://git-scm.com/docs/git-rerere)** — git 官方
  - **讀哪裡**：開頭的概念與適用場景。
  - **和本章的關聯**：rerere 的權威，理解它記什麼、何時自動套。

衝突會解了，下一章補上協作常用的三個救援/輔助工具：cherry-pick（挑 commit）、stash（暫存）、reflog（時光機，救回弄丟的東西）。

→ [Ch 9 cherry-pick / stash / reflog](./09-cherrypick-stash-reflog.md)
