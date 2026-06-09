# 練習 A — 把雜亂開發整理成乾淨歷史

> **目標**：把 Part 1（commit 是溝通、atomic commit、branch 是協作單位）綜合起來。你會拿到一段「真實開發過程」的雜亂 commit 歷史，把它整理成乾淨、atomic、訊息清楚、可以拿去發 PR 的樣子。完成後你會具備「送出前整理歷史」這個專業協作者的基本功。

> 本練習會用到 `git rebase -i` 做整理——這裡給你**手把手的步驟**，完整的 interactive rebase 機制在 [Ch 7](./07-interactive-rebase.md) 深入。先照做、建立手感，Ch 7 再補原理。

## 背景與動機

開發過程是混亂的：試錯、改了又改、commit 一堆 "wip"、"oops"、"fix typo"。這完全正常。但**把這團混亂直接發成 PR**，是新手最明顯的破綻——reviewer 看到一堆 "fix"、"wip"，第一印象就扣分，而且很難審。專業協作者會在送出前花幾分鐘，把歷史 rebase 成乾淨的樣子。這個練習就是練這個轉換：從「給自己看的草稿」到「給別人看的成品」。

## 任務規格

### 先製造一段「雜亂的開發歷史」

照著跑這個腳本，產生一個有問題的 branch（模擬你真實的混亂開發）：

```bash
mkdir clean-history-practice && cd clean-history-practice
git init && git switch -c main

# 初始狀態
cat > calc.py <<'EOF'
def add(a, b):
    return a + b
EOF
cat > README.md <<'EOF'
# Calculator
A simple calculator.
EOF
git add . && git commit -m "Initial commit"

# 開 feature branch，開始「混亂開發」
git switch -c feature/multiply

# 一堆雜亂的 commit（模擬真實開發）
cat >> calc.py <<'EOF'

def multiply(a, b):
    return a * b
EOF
git commit -am "wip"

# 改錯了又改
sed -i 's/return a \* b/return a*b  # fixme/' calc.py
git commit -am "fix"

sed -i 's/return a\*b  # fixme/return a * b/' calc.py
git commit -am "actually fix the multiply"

# 加文件
sed -i 's/A simple calculator./A simple calculator with add and multiply./' README.md
git commit -am "update readme"

# typo
echo "" >> README.md
echo "## Usage" >> README.md
git commit -am "add usage section"

sed -i 's/Usage/Usage Examples/' README.md
git commit -am "typo"

# 不相關的東西混進來
cat >> calc.py <<'EOF'

def subtract(a, b):
    return a - b
EOF
git commit -am "oh also add subtract"
```

現在 `git log --oneline` 看你的傑作：

```
xxxxxxx oh also add subtract
xxxxxxx typo
xxxxxxx add usage section
xxxxxxx update readme
xxxxxxx actually fix the multiply
xxxxxxx fix
xxxxxxx wip
xxxxxxx Initial commit
```

七個 commit，訊息一團糟，還混了不相關的東西。**這就是你要整理的對象。**

### 你要把它整理成

一段乾淨的歷史，理想長這樣（atomic + 清楚 message）：

```
xxxxxxx Add subtract function
xxxxxxx Document add and multiply in README
xxxxxxx Add multiply function
xxxxxxx Initial commit
```

每個 commit：
- **atomic**：一個 commit 一件事（multiply 的開發過程三個 commit 合成一個、README 的三個 commit 合成一個、subtract 獨立）。
- **訊息清楚**：祈使句、說清楚做了什麼（Ch 2）。
- **沒有 "wip"/"fix"/"typo"** 這種垃圾訊息。
- **subtract 獨立**：它和 multiply 無關，該是自己的 commit（atomic 原則）。

### 驗收標準

- [ ] 整理後的歷史沒有任何 "wip"/"fix"/"typo"/"oh also" 類訊息
- [ ] multiply 的開發過程（wip + fix + actually fix）合成一個 atomic commit
- [ ] README 的三次改動合成一個 commit
- [ ] subtract 是獨立的 commit（不和 multiply 混在一起）
- [ ] 每個 commit message 是祈使句、清楚說明做了什麼
- [ ] 最終 `calc.py` 和 `README.md` 的內容和整理前完全一樣（整理歷史不改變最終結果！）

## 期望輸出範例

```
$ git log --oneline
a1b2c3d Add subtract function
e4f5g6h Document add and multiply in README
i7j8k9l Add multiply function
m0n1o2p Initial commit

$ cat calc.py        # 內容和整理前一模一樣
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

def subtract(a, b):
    return a - b
```

## 如果你卡住了

1. **rebase -i 打開後我看不懂？** 它列出你的 commit（從舊到新），每行前面是一個動作（預設 `pick`）。你改這些動作來整理：`squash`/`fixup` 合併、`reword` 改訊息、`drop` 刪除、調整行順序來重排。
2. **怎麼把三個 commit 合成一個？** 把後兩個的 `pick` 改成 `squash`（或 `fixup`，差別是 fixup 丟棄那個 commit 的訊息）。它們會被併進前一個。
3. **subtract 混在最後一個 commit，怎麼讓它獨立？** 它本來就是獨立的 commit（"oh also add subtract"），你只要 `reword` 它的訊息即可——不用拆。難的是如果它和別的混在一個 commit，那要用 `edit` 拆開（進階，Ch 7）。本練習它已經獨立，只需改訊息。
4. **rebase 中途卡住/想放棄？** `git rebase --abort` 回到整理前的狀態，重來。這是安全網——rebase 隨時能 abort。
5. **改壞了？** 別怕。`git reflog`（Ch 9）記錄了所有狀態，能找回 rebase 前的 commit。

## 實作步驟建議

### Step 1：先看清楚現況

```bash
git log --oneline
git log -p          # 看每個 commit 改了什麼，搞清楚哪些該合併
```

子目標：在腦中規劃——哪些 commit 屬於 multiply、哪些屬於 README、哪個是 subtract。

### Step 2：規劃整理後的樣子

在紙上/腦中寫下目標歷史（4 個 commit）。整理 = 把 7 個 commit 重組成這 4 個。

### Step 3：啟動 interactive rebase

```bash
git rebase -i main      # 整理 main 之後（feature branch 上）的所有 commit
```

編輯器會打開，列出 7 個 commit（從舊到新）。

### Step 4：編輯 rebase 計畫

把動作改成：

```
pick   <wip>              -> reword（改成 "Add multiply function"）
squash <fix>             （併進上面）
squash <actually fix>    （併進上面）
pick   <update readme>    -> reword（改成 "Document add and multiply in README"）
squash <add usage>       （併進上面）
squash <typo>            （併進上面）
pick   <oh also subtract> -> reword（改成 "Add subtract function"）
```

存檔離開，git 會依序處理（reword 時會讓你改訊息、squash 時讓你編合併後的訊息）。

### Step 5：驗證

```bash
git log --oneline        # 應該剩 4 個乾淨 commit
cat calc.py README.md     # 內容應和整理前完全一樣
```

## 完整參考解答

**自己動手卡關後再看。**

<details>
<summary>點開完整整理流程</summary>

### 啟動 rebase

```bash
git rebase -i main
```

打開的編輯器內容（commit 由舊到新，最舊在最上）：

```
pick 1111111 wip
pick 2222222 fix
pick 3333333 actually fix the multiply
pick 4444444 update readme
pick 5555555 add usage section
pick 6666666 typo
pick 7777777 oh also add subtract
```

### 改成這樣

```
reword 1111111 wip
fixup  2222222 fix
fixup  3333333 actually fix the multiply
reword 4444444 update readme
fixup  5555555 add usage section
fixup  6666666 typo
reword 7777777 oh also add subtract
```

說明：
- `reword`：保留這個 commit，但讓我改它的 message。
- `fixup`：把這個 commit 併進**上一個**（pick/reword），並**丟棄**它的 message（用 `squash` 的話會讓你合併兩個 message，這裡我們不要那些垃圾訊息，所以用 fixup）。

存檔離開。git 開始處理：

1. **第一個 reword**（multiply）：跳出編輯器，把 `wip` 改成：
   ```
   Add multiply function
   ```
2. fixup 2222222、3333333 自動併入（無互動）。
3. **第二個 reword**（readme）：改成：
   ```
   Document add and multiply in README

   Update the description and add a Usage Examples section.
   ```
4. fixup 5555555、6666666 自動併入。
5. **第三個 reword**（subtract）：改成：
   ```
   Add subtract function
   ```

### 結果

```bash
$ git log --oneline
a1b2c3d Add subtract function
e4f5g6h Document add and multiply in README
i7j8k9l Add multiply function
m0n1o2p Initial commit

$ cat calc.py
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

def subtract(a, b):
    return a - b
```

四個乾淨的 atomic commit，內容和整理前一模一樣。

### 驗證內容沒變

整理歷史**不應該改變最終的程式碼**——只改變「歷史長怎樣」。確認方式：

```bash
# 整理前先記下最終狀態的 tree（在做 rebase 前）：
#   git rev-parse feature/multiply^{tree}
# 整理後再比一次，tree hash 應該相同（內容完全一致）
git rev-parse feature/multiply^{tree}
```

如果 tree hash 一樣，代表你只改了歷史、沒改內容——完美。

**解答說明**：

- **`squash` vs `fixup`**：兩者都合併 commit，差別在 message——`squash` 讓你保留並合併被併 commit 的 message，`fixup` 直接丟棄。整理垃圾訊息（"wip"/"fix"）時用 `fixup`，因為那些訊息不值得保留。
- **為什麼 subtract 不用拆**：在這個練習裡它本來就是獨立的 commit（"oh also add subtract"），只需 `reword`。如果它和 multiply 混在同一個 commit，才需要 `edit` 把 commit 拆開（更進階，Ch 7）。
- **rebase 的安全網**：整個過程隨時 `git rebase --abort` 回到原點；做壞了 `git reflog` 找回原始 commit。所以放心試。
- **golden rule 預告**：這個 branch **還沒 push**，所以 rebase 它完全安全。如果已經 push 過、別人可能基於它工作，rebase 就要小心（Ch 6 的 golden rule）。整理的時機是「push / 發 PR **之前**」。

</details>

## 測試用例

| 檢查 | 預期 |
|---|---|
| `git log --oneline` 行數 | 4（含 Initial commit）|
| 有無 "wip"/"fix"/"typo" 訊息 | 無 |
| multiply 相關 commit 數 | 1（三個合一）|
| README 相關 commit 數 | 1（三個合一）|
| `cat calc.py` 內容 | 和整理前完全一樣 |
| `git rebase --abort` 後 | 回到 7 個雜亂 commit |

## 延伸挑戰（加分）

1. **commit 重排**：如果你想讓 subtract 在 multiply **之前**（邏輯順序），在 rebase -i 裡調換行的順序。注意：重排可能產生衝突（如果兩個 commit 改同一行），這時要解衝突（Ch 8 預習）。
2. **拆一個混合 commit**：故意做一個「同時改 calc.py 和 README」的 commit，用 `edit` + `git reset HEAD^` 把它拆成兩個 atomic commit（Ch 7 進階）。
3. **`--autosquash` 工作流**：開發時用 `git commit --fixup=<sha>` 標記「這是要併回某 commit 的修正」，最後 `git rebase -i --autosquash` 自動排好——體驗更流暢的整理流程（Ch 7）。
4. **寫一個含 body 的 commit message**：給 multiply 的 commit 加一段 body，說明「為什麼」（即使是練習，練習寫 why）。

## 自我檢核

- [ ] 我理解「整理歷史改變的是歷史長相，不是最終程式碼」
- [ ] 我會用 `git rebase -i` 的 squash/fixup/reword 重組 commit
- [ ] 我能判斷哪些 commit 該合併（同一件事的過程）、哪些該獨立（atomic）
- [ ] 我知道 rebase 隨時能 `--abort`、做壞了能用 reflog 救
- [ ] 我知道整理的正確時機是「push / 發 PR 之前」，且這個 branch 還沒被別人依賴

Part 1 完成——你有了協作的心智模型，也會把工作整理成見得了人的樣子。Part 2 正式補齊協作必備的中階 git：remote、merge、rebase、衝突解決——這些是你之前完全沒碰過、但協作天天用的。

→ [Ch 4 remote 深入](./04-remotes-deep-dive.md)
