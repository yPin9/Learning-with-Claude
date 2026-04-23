# Ch2: 三大區——workdir / index / HEAD

Git 最常讓初學者困惑的概念。理解這三區後，`reset`、`restore`、`add` 的各種 `--` 選項就不再是魔法。

## 2.1 三個世界

```
┌──────────────┐   git add   ┌──────────────┐   git commit   ┌──────────────┐
│   workdir    │ ──────────> │    index     │ ──────────────>│     HEAD     │
│  (你的檔案)  │             │   (staged)   │                │  (上次 commit)│
└──────────────┘             └──────────────┘                └──────────────┘
       ^                            ^                                ^
       │                            │                                │
     編輯                        git add                          已提交
```

三個區，三個狀態：
- **Workdir**（也叫 working tree）：**你現在看得到、編輯的檔案**
- **Index**（也叫 staging area / cache）：**下次 commit 會包的東西**
- **HEAD**：**上次 commit 的 tree**（「當前版本」的 snapshot）

## 2.2 用 `git status` 解剖

```bash
git status
```

典型輸出：
```
On branch main
Changes to be committed:
  (use "git restore --staged <file>..." to unstage)
        modified:   a.txt

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   b.txt

Untracked files:
        c.txt
```

對應：
- `Changes to be committed` = **index vs HEAD** 的差異（已 staged）
- `Changes not staged` = **workdir vs index** 的差異（未 staged）
- `Untracked` = workdir 有但 index 和 HEAD 都沒有

## 2.3 檔案可以同時在三區有「三個版本」

關鍵突破點：**同一個檔案，workdir / index / HEAD 可以都不一樣**。

```bash
echo "v1" > a.txt
git add a.txt
git commit -m "v1"

echo "v2" > a.txt      # workdir: v2, index: v1, HEAD: v1
git add a.txt          # workdir: v2, index: v2, HEAD: v1

echo "v3" > a.txt      # workdir: v3, index: v2, HEAD: v1
```

此時：
- `a.txt` 的 workdir 內容：`v3`
- `a.txt` 的 index 版本：`v2`
- `a.txt` 的 HEAD 版本：`v1`

`git status`：
```
Changes to be committed:
        modified:   a.txt         ← index vs HEAD 差異 (v2 vs v1)

Changes not staged for commit:
        modified:   a.txt         ← workdir vs index 差異 (v3 vs v2)
```

**同一個檔案，兩處都說它 modified**。搞懂這個是熟練 git 的里程碑。

## 2.4 `git diff` 的三種形式

對應三區，diff 也有三種：

```bash
git diff                # workdir vs index    （「還沒 add 的改動」）
git diff --staged       # index vs HEAD       （「已 add 待 commit」）
git diff HEAD           # workdir vs HEAD     （「和上次 commit 差多少」）
```

`--cached` 和 `--staged` 等價。

## 2.5 把改動移出移入

### Workdir → index
```bash
git add file.txt
git add .                  # 加當前目錄所有
git add -p                 # 互動式挑 hunk（超實用）
git add -u                 # 只加已追蹤檔案的改動
git add -A                 # 全加（含新檔案、刪除）
```

### Index → workdir（取消 staging）
```bash
git restore --staged file.txt      # 現代語法
git reset HEAD file.txt            # 舊語法，等效
```

**重點**：這只取消 staging，workdir 的改動**還在**。

### 扔掉 workdir 改動
```bash
git restore file.txt               # 用 index 覆蓋 workdir
git checkout -- file.txt           # 舊語法，等效
```

⚠️ **這不可逆**。workdir 改動直接消失。

### 扔掉 index 的改動
```bash
git restore --staged --worktree file.txt    # 扔 index 和 workdir
```

或兩段式：
```bash
git restore --staged file.txt    # 先 index → 變回未 staged
git restore file.txt             # 再扔 workdir
```

## 2.6 `git add -p` 是神器

想把一個檔案的改動**分成多個 commit**？

```bash
git add -p file.txt
```

會互動問每個 hunk：
```
Stage this hunk [y,n,q,a,d,s,e,?]?
```

- `y`：加這段
- `n`：不加
- `s`：把 hunk 再切小
- `e`：手動編輯 hunk
- `q`：退出

這讓你做 **atomic commit**（Ch9）——一個 commit 一件事，不混雜。

## 2.7 Index 其實存在哪？

`.git/index` 二進位檔。包含：
- 追蹤的每個檔案的 stat 資訊（mtime、size）+ 對應 blob hash
- 衝突資訊（三路 merge 中途）

stat 資訊讓 git 能快速判斷檔案有沒有改（不用重算 hash）。

看它（人類看不懂但可以看大小）：
```bash
ls -la .git/index
git ls-files --stage       # 解讀過的內容
```

## 2.8 `git commit` 的真相

`git commit` 做：
1. 把 index 寫成一個 tree object
2. 建 commit object（parent = HEAD、tree = 剛才那 tree）
3. 更新 HEAD 指新 commit

**Workdir 沒參與**。所以 `git add` 沒做到的改動不會進 commit。

這個分離看似囉嗦，但好處：
- 可以精準控制哪些改動一起 commit
- `add -p` 切割成 atomic commits
- 邊改邊 stage，不怕暴衝

### `commit -a` 的坑
```bash
git commit -a -m "stuff"
```

`-a` = 「先 add 所有已追蹤檔案的改動，再 commit」。**不含新檔案**。容易以為「一切都進去了」但新檔案沒被追蹤。

少用 `-a`，養成明確 `add` 的習慣。

## 2.9 圖解常見操作

### 正常流程
```
   編輯             git add              git commit
workdir ──────> workdir (不變) ────> workdir (不變)
                 index (更新)          index (不變)
                 HEAD  (不變)          HEAD  (更新)
```

### `git checkout <commit>`（或 `git switch`）
```
workdir (更新)
index   (更新)
HEAD    (更新)
```

三區全換到目標 commit 的狀態。

### `git reset --mixed <commit>`（預設）
```
workdir (不變)
index   (更新到目標)
HEAD    (更新到目標)
```

### `git reset --soft <commit>`
```
workdir (不變)
index   (不變)
HEAD    (更新到目標)
```

好用場景：「我 commit 太早了，想把這幾個 commit 的改動變回 staged」：
```bash
git reset --soft HEAD~3   # 把最近 3 個 commit 變回 staged 狀態
```

### `git reset --hard <commit>`
```
workdir (更新 ⚠️)
index   (更新)
HEAD    (更新)
```

**危險**：workdir 改動消失。但**只對 index 已知的檔案**——新檔案（untracked）不會被動。

## 2.10 實驗

```bash
mkdir /tmp/test && cd /tmp/test
git init

echo "v1" > a.txt
git add a.txt
git commit -m "v1"

echo "v2" > a.txt
git add a.txt
echo "v3" > a.txt

git status
# Changes to be committed: modified a.txt (v2 vs v1)
# Changes not staged:      modified a.txt (v3 vs v2)

git diff                   # 看 v3 vs v2
git diff --staged          # 看 v2 vs v1
git diff HEAD              # 看 v3 vs v1
```

你會「看到」三個版本共存。

## 2.11 練習

1. 同一個檔案改兩次，用 `add -p` 分成兩個不同的 commit。
2. 不小心 `git add` 了不該加的檔，用 `git restore --staged` 取消，不影響 workdir 的其他改動。
3. 寫了一堆，想分成三個 commit（bugfix、feature、docs），用 `git add -p` 練習。

## 2.12 常見誤解

### 誤解 1：`git add` 是「加進下次 commit 的檔案清單」
其實是**把當下的檔案內容快照存進 index**。之後再改，要再 add 一次（或改動不會進 commit）。

### 誤解 2：reset 和 checkout 一樣
`reset` 改 HEAD pointer；`checkout`（舊）/ `switch`（新）只移動 HEAD 不改歷史。混淆導致很多血案。Ch12 細講。

### 誤解 3：commit 會包含 workdir 所有改動
只包 **index** 的。`commit -am` 只補了已追蹤檔。新檔要先 `add`。

## 本章重點
- 三區：**workdir / index / HEAD**
- 同一檔案可同時在三區有三個版本
- `git diff` 的三種變體對應三區兩兩比較
- `git add -p` 拆分改動成 atomic commits
- `git reset` 有三種模式：soft（只 HEAD）、mixed（HEAD + index）、hard（全部）
- `git commit` 從 **index** 打包，不從 workdir
