# Ch3: log / diff 的真實用法

大家都會 `git log` 和 `git diff`，但 80% 的強大功能沒用上。這章把實務上最有價值的選項過一遍。

## 3.1 log 的基本變體

```bash
git log                    # 完整
git log --oneline          # 每個 commit 一行
git log --graph            # ASCII graph
git log --all              # 所有 branch
git log --decorate         # 顯示 branch / tag ref 名字
```

日常 alias：
```bash
git config --global alias.lg "log --oneline --graph --decorate --all"
```

之後 `git lg` 看整個 repo 的 DAG。

## 3.2 過濾 commits

### 按作者
```bash
git log --author="ypp"
git log --author="ypp\|alice"        # 多個（regex）
```

### 按 commit message
```bash
git log --grep="fix bug"
git log --grep="fix" --grep="bug" --all-match    # 兩個關鍵字都要
```

### 按時間
```bash
git log --since="2 weeks ago"
git log --until="2024-01-01"
git log --since="2024-03-01" --until="2024-04-01"
```

### 按檔案
```bash
git log -- path/to/file.txt              # 只顯示動到這檔的 commit
git log -- 'src/**/*.cpp'                # glob
git log --follow -- file.txt             # 跟著 rename
```

### 按分支範圍
```bash
git log main..feature              # feature 上但 main 沒有的
git log feature..main              # main 上但 feature 沒有的
git log main...feature             # 兩邊各自獨有的（對稱差集）
```

## 3.3 殺手鐧：`log -S` 和 `-G`

### `-S`：找哪個 commit 引入/刪除某字串
```bash
git log -S "old_function_name"
```

找所有**改變了這個字串出現次數**的 commit。殺手級功能——找「這個函式是什麼時候被加的 / 刪的」。

### `-G`：正規表達式
```bash
git log -G "regex_pattern"
```

比 `-S` 慢但更強（任何 diff 命中 regex 都算）。

### `-L`：追一行 / 一個函式的演化
```bash
git log -L :function_name:src/main.cpp       # 追一個函式
git log -L 10,20:src/main.cpp                # 追第 10-20 行
```

逐 commit 顯示「這段 code 每次被改」的 diff。Bug hunting 神器。

## 3.4 看 diff 的方式

```bash
git log -p                   # 每個 commit 都展開 diff
git log -p -- file.txt       # 只某檔
git log --stat               # 看每 commit 改了哪些檔，多少行
git log --shortstat          # 只摘要
git log --name-only          # 只看檔案名
git log --name-status        # M/A/D 狀態 + 檔名
```

## 3.5 自訂 log 格式

```bash
git log --pretty=format:"%h %an %s"
```

常用 placeholder：
- `%h` / `%H` 短 / 長 hash
- `%an` / `%ae` author name / email
- `%s` subject（第一行訊息）
- `%b` body
- `%ad` / `%ar` author date / relative date
- `%d` decoration（branch/tag）

組合：
```bash
git log --pretty=format:"%C(yellow)%h%Creset %C(cyan)%ad%Creset %s %C(green)(%an)%Creset" --date=short
```

## 3.6 `git show`

看單一 commit 的細節：

```bash
git show HEAD
git show abc1234
git show HEAD:path/to/file.txt     # 看這 commit 時某檔的內容
git show HEAD^:path/to/file.txt    # 上一個 commit
```

## 3.7 diff 的 power 用法

### 字級 diff
```bash
git diff --word-diff
git diff --word-diff=color
```

短行改一個字時超有用。

### 強化 move detection
```bash
git diff --color-moved=zebra
git diff --color-moved-ws=allow-indentation-change
```

把「搬移」的 code 和「新增」的 code 分色——review 大重構時必開。
（可設全域：`git config --global diff.colorMoved zebra`）

### 忽略空白
```bash
git diff -w                        # 忽略所有空白差異
git diff --ignore-space-change     # 忽略空白數量差異
git diff --ignore-blank-lines      # 忽略空行差異
```

### 不同 branch / commit 之間
```bash
git diff main..feature             # feature 相對 main 的改動
git diff main feature              # 等效
git diff HEAD~3                    # 和 3 個 commit 前比
git diff HEAD~3 HEAD               # 明確兩端
```

### 單檔 diff
```bash
git diff main -- file.txt
git diff HEAD~2 HEAD -- file.txt
```

### `--stat`
```bash
git diff --stat main
```

快速摘要「這 branch 動了哪些檔、各多少行」。

## 3.8 `git blame`

```bash
git blame file.txt
git blame -L 10,20 file.txt        # 只看第 10-20 行
git blame -L :function_name:file.c # 只看某函式
```

每行標注「哪個 commit 加的、誰加的」。配合 `-w` 忽略空白改動：
```bash
git blame -w file.txt
```

### 追溯過去 — `-C` 和 `-M`
```bash
git blame -M file.txt     # 追蹤 rename / 檔內搬移
git blame -CCC file.txt   # 追蹤跨檔複製（三個 C 最激進）
```

找「這行原本在哪」的神器——歷經多次 rename / split 還能挖出源頭。

## 3.9 實用 log 例子

### 今天做了什麼
```bash
git log --since=yesterday --author="ypp" --oneline
```

### 某檔最近 5 次改動
```bash
git log -5 --oneline -- file.txt
```

### 看整個 repo 的 graph
```bash
git log --oneline --graph --decorate --all
```

### 列出 contributor
```bash
git shortlog -s -n
```

按 commit 數排序的作者列表。

### 查某 commit 在哪些 branch
```bash
git branch --contains abc1234
git tag --contains abc1234
```

### 在一個 range 找 merge commits
```bash
git log --merges main..feature
```

### 排除 merge commits
```bash
git log --no-merges
```

## 3.10 `--first-parent`

在有很多 merge commit 的 branch：
```bash
git log --first-parent main
```

只跟著 main 的第一個 parent 走，忽略 merged feature branches 的內部 commit。看「main 發生了什麼」用這個。

## 3.11 `git log` 跟 `tig`（可選）

```bash
# MSYS2
pacman -S tig

tig                    # 互動式 log browser
tig status             # 比 git status 強
tig blame file.txt     # 互動式 blame
```

純 terminal TUI，箭頭鍵操作。你要的話可以試，不強制。

## 3.12 `git reflog`

這其實是 log 的一種，但重要到單獨一章（Ch11）：
```bash
git reflog
```

列出 **HEAD 移動過的每個位置**，含刪除 branch、rebase、reset 後的舊狀態。**救命神器**。

## 3.13 實戰

### 場景 1：找哪個 commit 刪掉某個函式
```bash
git log -S "old_function_name" --oneline
```

### 場景 2：想知道某行 code 怎麼來的
```bash
git blame -w file.txt
# 看某行的 commit hash
git show abc1234
```

### 場景 3：review 某 feature branch 的所有改動
```bash
git log main..feature --oneline
git diff main...feature                  # 注意三個點
git diff main...feature --stat
```

### 場景 4：這個月 release 哪些 PR
```bash
git log v1.0..v1.1 --merges --oneline
```

### 場景 5：找誰最後改過某行
```bash
git blame -L 42,42 file.cpp
```

## 3.14 常見坑

### 坑 1：`..` vs `...`
```bash
git log A..B     # reachable from B but not from A
git log A...B    # symmetric difference

git diff A..B    # 等同 git diff A B（兩點等效）
git diff A...B   # diff from merge-base of A and B, to B
```

**log 和 diff 的 `...` 含義不同**，是歷史包袱。記不住就查。

### 坑 2：`git log file.txt` vs `git log -- file.txt`
加 `--` 消除歧義（filename 不會被當 branch）。重構 rename 後這很重要。

### 坑 3：`--follow` 只對單檔
```bash
git log --follow -- file.txt      # OK
git log --follow -- dir/           # ❌ 不 work
```

## 3.15 練習

1. 用 `log -S` 找出「`main` 函式的 return 0」是哪個 commit 引入。
2. 看最近 7 天所有 merge commit。
3. 看 `README.md` 最近 5 次改動的 diff。
4. 用 `blame -w -M` 追溯某個檔案裡一行的原始 commit。

## 本章重點
- `log --oneline --graph --decorate --all` 是萬用 alias
- `-S` / `-G` / `-L` 是「找 code 歷史」的殺手鐧
- `diff --color-moved` 讓重構 review 可讀
- `blame -w -M -C` 追溯 code 演化極限
- `log A..B` vs `log A...B` vs `diff A...B` 含義不同，查文件
- 追單檔加 `--`，避免歧義
