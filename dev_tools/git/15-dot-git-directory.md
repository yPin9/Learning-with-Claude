# Ch15: `.git/` 目錄探險

打開 `.git/` 看看。這裡沒魔法，都是檔案。

## 15.1 總覽

```bash
$ ls .git/
HEAD
ORIG_HEAD
FETCH_HEAD
MERGE_HEAD        # 只在 merge 中途存在
branches/         # 幾乎不用
config
description       # GitWeb 用，沒用到可忽略
hooks/
index
info/
logs/
objects/
packed-refs
refs/
```

逐一拆解。

## 15.2 `HEAD`

指當前位置。通常：
```bash
$ cat .git/HEAD
ref: refs/heads/main
```

或 detached：
```
9688662abc1234...
```

`git switch` 改這個檔的內容。

## 15.3 `refs/`

所有 ref 的實體位置。

```
refs/
├── heads/         # 本地 branch
│   ├── main
│   ├── feature
│   └── ...
├── remotes/       # remote-tracking
│   └── origin/
│       ├── main
│       └── ...
├── tags/          # tag
│   ├── v1.0
│   └── ...
└── stash          # 當前 stash（如果有）
```

每個檔是純文字，一行 hash：
```bash
$ cat .git/refs/heads/main
9688662abc1234...
```

`git branch feature` 就是在 `refs/heads/` 建一個檔。`git branch -D feature` 就是刪那個檔。

## 15.4 `packed-refs`

大 repo 有成千上萬個 tag 時，每個一個檔會浪費 inode。Git 會把它們打包到這個檔：

```bash
$ cat .git/packed-refs
# pack-refs with: peeled fully-peeled sorted 
9688662abc... refs/heads/main
aabbccdd... refs/tags/v1.0
^ccddeeff... (peeled tag 指向的 commit)
```

Loose ref（`refs/heads/main`）如果存在會**優先**於 packed 版本——所以改 ref 是新建 loose ref。

自動維護，別手動改。

## 15.5 `objects/`

所有 commit、tree、blob 存這裡。Ch14 講過。

```
objects/
├── 12/
│   └── 34abcdef...
├── ab/
│   └── cd1234...
├── info/
└── pack/
    ├── pack-xxx.pack
    └── pack-xxx.idx
```

## 15.6 `index`

二進位檔，你 staged 的檔案。Ch2 的「index」就是這個。

看它：
```bash
git ls-files --stage
# 100644 ce01362... 0  file.txt
# 100644 abc1234... 0  src/main.cpp
```

每行：mode + blob hash + stage + path。

Stage 欄位在 merge 衝突時才非 0（代表 base/ours/theirs 版本）。

## 15.7 `logs/`

Reflog 就在這：

```
logs/
├── HEAD                    # 整個 HEAD 的 reflog
└── refs/
    ├── heads/main          # main branch 的 reflog
    ├── heads/feature
    └── remotes/origin/main # 遠端追蹤的 reflog
```

看：
```bash
cat .git/logs/HEAD
# <prev-hash> <new-hash> <author> <timestamp> <message>
# 0000000 abc1234 ypp <email> 1713123456 +0800  commit (initial): first
# abc1234 def5678 ypp <email> 1713123457 +0800  commit: second
```

`git reflog` 就是漂亮印出這個檔。

## 15.8 `config`

本 repo 的 config：
```bash
$ cat .git/config
[core]
    repositoryformatversion = 0
    filemode = false
    bare = false
[remote "origin"]
    url = git@github.com:user/repo.git
    fetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
    remote = origin
    merge = refs/heads/main
```

三層 config：
- **System**：`/etc/gitconfig`（全機器）
- **Global**：`~/.gitconfig`（使用者）
- **Local**：`.git/config`（此 repo）

後者覆蓋前者。

查來源：
```bash
git config --show-origin --list
```

## 15.9 `hooks/`

Hook 腳本放這。Ch20 專章。

```bash
$ ls .git/hooks/
applypatch-msg.sample
commit-msg.sample
post-update.sample
pre-applypatch.sample
pre-commit.sample
pre-push.sample
pre-rebase.sample
pre-receive.sample
prepare-commit-msg.sample
update.sample
```

`.sample` 後綴 = 不會執行。改檔名去掉 `.sample` + `chmod +x` 才啟用。

## 15.10 `info/`

`info/exclude`：**不提交到 repo 的 .gitignore**（只本機）：
```
# 本機偏好
my-notes.md
.local-config
```

和 `.gitignore`（會被提交）不同。適合你自己的 scratch 檔。

## 15.11 `FETCH_HEAD` / `MERGE_HEAD` / `ORIG_HEAD`

特殊 ref，特殊時機：
- **`FETCH_HEAD`**：最近 fetch 的 ref（`git pull` 會 merge 它）
- **`MERGE_HEAD`**：merge 中途，指被合併的 commit
- **`CHERRY_PICK_HEAD`**：cherry-pick 中途
- **`REVERT_HEAD`**：revert 中途
- **`REBASE_HEAD`**：rebase 中途
- **`ORIG_HEAD`**：destructive op 前的 HEAD（reset 救命用）

各自在對應操作的中途存在，操作完成或 `--abort` 後消失。

## 15.12 `rebase-merge/` / `rebase-apply/`

Rebase 中途的狀態：
```
.git/rebase-merge/
├── git-rebase-todo    # 待處理的 commit 清單（pick, squash, ...）
├── done               # 已處理的
├── onto               # 新 base commit
├── head-name          # 原本的 branch
└── ...
```

可以直接 edit `git-rebase-todo` 改計畫（對應 `git rebase --edit-todo`）。

## 15.13 `description`

GitWeb 用的 repo 描述。你應該沒用到。

## 15.14 `shallow`

Shallow clone（`git clone --depth=1`）時的 marker：
```bash
$ cat .git/shallow
abc1234def...
```

記錄「這裡是歷史被切斷的點」。普通 clone 沒這檔。

## 15.15 `maintenance.log`（現代 git）

`git maintenance` 執行的 log（Ch16）。

## 15.16 探索練習

```bash
mkdir /tmp/dotgit && cd /tmp/dotgit
git init

echo "a" > a.txt
git add a.txt
git commit -m "first"

# 看整個結構
find .git -not -path '*/\.*' -type f | head -30

# 看 index
xxd .git/index | head

# 看 HEAD
cat .git/HEAD

# 看 main ref
cat .git/refs/heads/main

# 看 commit object
git cat-file -p $(cat .git/refs/heads/main)

# 看 reflog
cat .git/logs/HEAD
```

## 15.17 自己改 `.git/` 安全嗎？

**少數情況可以**：
- 改 `config`：OK，等同 `git config`
- 新增 `info/exclude`：OK
- 建 `refs/heads/newbranch`：OK，等同 `git branch newbranch`
- 改 `HEAD` 指到別的 ref：能 work 但建議用 `git switch`

**不要直接改**：
- `objects/` 下的東西（內容 addressable，動了 hash 就錯）
- `index`（二進位格式，容易腐壞）
- `packed-refs`（格式複雜）

## 15.18 備份 repo 的最簡方式

整個 `.git/` 目錄複製就好：
```bash
cp -r .git /backup/location/repo.git.backup
```

要救：
```bash
rm -rf .git
cp -r /backup/location/repo.git.backup .git
git checkout -- .
```

所有歷史、branch、commit、reflog 都在 `.git/` 裡。

## 15.19 Bare repo

在 server 上的 repo 只有 `.git/` 的內容，沒 workdir：
```
myrepo.git/
├── HEAD
├── branches/
├── config
├── description
├── hooks/
├── info/
├── objects/
├── packed-refs
└── refs/
```

就是 `.git/` 的內容平攤。用 `git init --bare` 建。

```bash
git init --bare myrepo.git
```

GitHub 上的 repo 就是 bare repo。

## 15.20 本章重點
- `.git/` 結構透明、可讀
- `HEAD` 是當前位置（symbolic ref 或 hash）
- `refs/heads/*` 是 branch 實體，純文字
- `refs/remotes/origin/*` 是 remote-tracking
- `logs/` 是 reflog 實體
- `hooks/` 是客製化 hook 位置（Ch20）
- `FETCH_HEAD` / `MERGE_HEAD` / `ORIG_HEAD` 是特殊 ref
- 備份 `.git/` = 備份整個 repo 歷史
- Bare repo 就是 `.git/` 平攤
