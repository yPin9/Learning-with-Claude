# Ch22: worktree

**本章重點章**（你選擇的深度目標之一）。Worktree 讓一個 repo 同時存在多個 workdir，每個 checkout 不同 branch。**不是**多個 clone。

## 22.1 問題

傳統 git：一個 repo = 一個 workdir。想同時在兩個 branch 工作要：
- 切 branch（要 stash/commit 當前）
- 或 clone 兩份（浪費磁碟）

Worktree：一份 `.git/`，多個 workdir。

## 22.2 基本用法

### 加 worktree
```bash
cd myrepo                              # 主 repo
git worktree add ../myrepo-feature feature
# 建 ../myrepo-feature 資料夾，checkout 在 feature branch
```

### 切過去工作
```bash
cd ../myrepo-feature
# 正常 git 操作：add, commit, push, switch
```

### 列
```bash
cd ../myrepo
git worktree list
# /path/to/myrepo            abc1234 [main]
# /path/to/myrepo-feature    def5678 [feature]
```

### 移除
```bash
git worktree remove ../myrepo-feature
# 或在被移除的 worktree 內
git worktree remove .
```

**未 commit 的改動會阻止移除**，要 `--force` 或先 commit/stash。

## 22.3 限制

### 同一 branch 只能在一個 worktree
```bash
cd myrepo         # 在 main
git worktree add ../myrepo-2 main
# fatal: 'main' is already checked out at '/path/to/myrepo'
```

解法：worktree 2 用不同 branch。

### 共享 `.git/`
所有 worktree 共用 objects、ref、config。Branch 刪了所有 worktree 都看不到。

### Detached 特殊情況
```bash
git worktree add ../myrepo-view --detach HEAD~5
# 在某歷史 commit 上 detached，自由探索
```

## 22.4 典型場景

### 場景 1：主 branch 和 feature 並行
```bash
# 主 worktree: main
cd ~/myrepo
git switch main

# 加一個 worktree 做 feature
git worktree add ../myrepo-feat feature/add-auth

# 兩個 terminal 分別：
cd ~/myrepo-feat     # 寫 feature
cd ~/myrepo          # 跑 main 的 service
```

不用切 branch，兩邊並行。

### 場景 2：review 別人 PR 時保留自己進度
```bash
# 自己 branch 上改到一半
git worktree add /tmp/review-pr-123 main
cd /tmp/review-pr-123
gh pr checkout 123
# 跑 test、看 code
cd -
# 自己工作完全沒中斷
rm -rf /tmp/review-pr-123    # 或 git worktree remove
```

### 場景 3：編譯耗時的專案
C++ / Rust / Unreal 大專案編譯慢。切 branch 後全 recompile。
```bash
# main 的 worktree 有 main 的 build cache
# feature 的 worktree 有 feature 的 build cache
# 切換零成本
```

### 場景 4：歷史探索
```bash
git worktree add /tmp/old-version v1.0
cd /tmp/old-version
# 跑一個舊版本
```

### 場景 5：hotfix + 當前工作
```bash
# 正在 feature 上工作
# main 突然有 prod bug
git worktree add ../hotfix main
cd ../hotfix
git switch -c hotfix/xxx
# ... 修 ...
git push
gh pr create
cd -
# feature 工作沒動
```

## 22.5 指令總覽

```bash
git worktree add <path> <branch>              # 加，用現有 branch
git worktree add <path> -b <new-branch>       # 加，同時建 branch
git worktree add <path> -b <new> <start>      # 指定 start point

git worktree add --detach <path> <commit>     # detached HEAD

git worktree list                             # 列
git worktree list --porcelain                 # 機器可讀
git worktree list --verbose

git worktree remove <path>                    # 移除
git worktree remove --force <path>            # 有未 commit 也強移

git worktree prune                            # 清理壞 worktree（path 被手動刪的）

git worktree move <path> <new-path>           # 搬 worktree 位置

git worktree lock <path>                      # 鎖（防止被自動 prune）
git worktree unlock <path>
```

## 22.6 Worktree 儲存位置

```
myrepo/                    ← 主 worktree
├── .git/
│   ├── worktrees/
│   │   ├── myrepo-feat/
│   │   │   ├── HEAD
│   │   │   ├── index
│   │   │   └── ...
│   │   └── hotfix/
│   └── ...
└── ...

myrepo-feat/                ← 其他 worktree
├── .git               ← 一個檔，指回 .git/worktrees/myrepo-feat
└── ...
```

非主 worktree 裡的 `.git` 是**檔案**不是目錄，內容：
```
gitdir: /path/to/myrepo/.git/worktrees/myrepo-feat
```

## 22.7 Worktree 特有的 ref

每個 worktree 有自己的 HEAD、index、reflog：
```
.git/worktrees/myrepo-feat/
├── HEAD
├── index
├── logs/HEAD       ← 這個 worktree 的 HEAD reflog
└── ...
```

但 **branches / tags / objects 共用**。

這就是為什麼「同 branch 不能在兩個 worktree」——branch 是共享的，HEAD pointer 只能指一個地方。

## 22.8 Bare repo + worktree（專業技巧）

不想有「主 worktree」？用 bare repo + 純 worktree 管理：

```bash
# 建 bare repo
git clone --bare git@github.com:user/repo.git myrepo.git
cd myrepo.git

# 加 worktree
git worktree add ../myrepo-main main
git worktree add ../myrepo-feature feature
```

結構：
```
~/project/
├── myrepo.git/               ← bare repo（純 .git/ 內容）
├── myrepo-main/              ← main 的 workdir
└── myrepo-feature/
```

**優點**：沒有「主 worktree」的特殊性，所有 branch 都是 worktree 平等。

**fetch 設定**：bare clone 預設 fetch refspec 只抓 branch，不 track 所有。可能要改：
```bash
cd myrepo.git
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
```

## 22.9 常見陷阱

### 陷阱 1：刪 worktree 資料夾但沒 `git worktree remove`
```bash
rm -rf ../myrepo-feat
git worktree list
# 還有 myrepo-feat（但 path 不存在）
git worktree prune        # 清掉殘留
```

### 陷阱 2：worktree 數量失控
建太多 worktree、不清，磁碟爆：
```bash
git worktree list
# 該清就清
```

### 陷阱 3：IDE 只開一個 worktree 當 repo
有些 IDE 不懂 worktree 機制，把 worktree 當成獨立 repo 開多次。改用 workspace 功能。

### 陷阱 4：對 worktree 用 `git clone`
不需要 clone——用 `worktree add` 就好。

### 陷阱 5：submodule 在 worktree 的複雜性
Submodule + worktree 是地獄。避免組合，要組合仔細測。

## 22.10 Worktree 和 CI/CD

CI 常用 `git checkout <ref>` 切 branch。用 worktree 加速：
```bash
# CI agent 持續跑，保留 .git/
git fetch
git worktree add ./build-123 origin/feature-123
cd build-123
# build
cd ..
git worktree remove build-123
```

獨立 build dir、不互相污染。

## 22.11 `worktree` vs `stash` / `switch`

| 需求 | 適合 |
|---|---|
| 改到一半臨時切 branch 一分鐘 | `stash` + `switch` |
| 兩個 branch 反覆並行（小時～天） | `worktree` |
| 跑耗時 build 不想中斷 | `worktree` |
| 簡短 hotfix 後回來 | 看情境；通常 `stash` 夠 |
| 同時 review 多個 PR | `worktree` |

## 22.12 實戰 alias

```bash
git config --global alias.wta 'worktree add'
git config --global alias.wtl 'worktree list'
git config --global alias.wtr 'worktree remove'
```

```bash
git wta ../project-feat feature
git wtl
git wtr ../project-feat
```

## 22.13 進階：每個 worktree 自己的 config

有時想對某 worktree 有不同設定（例如不同 user.email）：
```bash
cd ../myrepo-feat
git config --worktree user.email "work@example.com"
```

這需要 `extensions.worktreeConfig` 開啟：
```bash
git config extensions.worktreeConfig true
```

**不常用**，但存在。

## 22.14 結合 script 自動化

```bash
# ~/bin/wt-new
#!/bin/bash
# Usage: wt-new feature/xxx
branch=$1
name=$(basename "$branch")
path="../$(basename $(pwd))-$name"
git worktree add -b "$branch" "$path" origin/main
echo "cd $path"
```

一行命令建 worktree + 開 branch。

## 22.15 練習

```bash
mkdir /tmp/wt-test && cd /tmp/wt-test
git init
echo "a" > a.txt
git add a.txt
git commit -m "initial"
git branch feature

# 加 worktree
git worktree add ../wt-test-feat feature
cd ../wt-test-feat
# 確認在 feature branch
git branch --show-current
# feature

# 改東西 commit
echo "b" >> a.txt
git commit -am "feature change"

# 回主 worktree 看
cd ../wt-test
git log --all --oneline
# 可以看到 feature 的 commit 也在了（objects 共享）

# 列 worktree
git worktree list

# 移除
cd ..
git -C wt-test worktree remove wt-test-feat
```

延伸：
1. 建 bare clone + 幾個 worktree，管理 main 和兩個 feature。
2. 試把一個 worktree `git worktree lock` 再 prune，看 lock 的保護效果。
3. 寫一個 shell 腳本：`wt <branch>` 自動建 worktree 並 `cd` 過去。

## 22.16 本章重點
- `git worktree add <path> <branch>` 在另一目錄 checkout 另一 branch
- **同 branch 不能同時在兩個 worktree**
- `.git/` 共用（objects、ref、config），每個 worktree 有自己 HEAD/index
- 適合：並行 branch、review PR、hotfix、保留 build cache
- 主 worktree 以外的目錄中 `.git` 是**檔案**不是目錄
- Bare repo + worktree 是專業玩法（沒有「主」的概念）
- 用 `git worktree prune` 清理被手動刪的 worktree 殘留
