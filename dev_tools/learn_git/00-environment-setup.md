# Ch0: 環境設定

Windows 上用 git 有幾個**一次設定、終身受用**的坑要先填。

## 0.1 確認版本

```bash
git --version
```

**建議 2.30+**。新語法（`git switch`、`git restore`、`git maintenance`、`push --force-with-lease`）都需要。

MSYS2 升級：
```bash
pacman -S git
```

WSL2：
```bash
sudo apt update && sudo apt install git
```

## 0.2 身份

```bash
git config --global user.name "ypp"
git config --global user.email "ohtanishohei715@gmail.com"
```

`--global` 存到 `~/.gitconfig`（Windows 在 `%USERPROFILE%/.gitconfig`）。

對特定 repo 不同身份：
```bash
cd work-repo
git config user.email "ypp@company.com"   # 這個 repo 專用
```

**查目前設定**：
```bash
git config --list --show-origin         # 每個設定來自哪個檔案
git config user.email                    # 查單項
```

## 0.3 Line endings（Windows 必填）

三個系統的換行：
- Windows：`\r\n` (CRLF)
- Unix/Mac：`\n` (LF)

git 如果不配，你會在 diff 看到整檔「每行都變了」。

### 推薦設定（Windows 上）
```bash
git config --global core.autocrlf input
```

含義：
- `input`：checkout 用 LF，commit 時把 CRLF 轉 LF（**推薦**）
- `true`：checkout 自動轉 CRLF，commit 轉 LF
- `false`：完全不轉（存什麼就是什麼）

**為什麼選 `input`**：repo 統一 LF（跨平台乾淨），你本地編輯器自己決定要不要顯示 CRLF。

### 更現代的做法：`.gitattributes`
在 repo 根建 `.gitattributes`：
```
* text=auto eol=lf
*.sh text eol=lf
*.bat text eol=crlf
*.png binary
*.jpg binary
```

這會強制所有 text 檔在 repo 裡存 LF，`.bat` 特別保留 CRLF。**新 repo 建議都加**，比 `core.autocrlf` 可靠。

## 0.4 預設 branch 名

```bash
git config --global init.defaultBranch main
```

現代慣例是 `main`。`git init` 會用這個名字。

## 0.5 Credential helper

每次 push 都輸密碼很煩。

### Windows：使用 Git Credential Manager
MSYS2 的 git 會自動裝 GCM（Git Credential Manager）。查：
```bash
git config --global --get credential.helper
```

如果空的，裝一下：
```bash
git config --global credential.helper manager
```

第一次 push 會彈瀏覽器登入 GitHub，之後就無感。

### 或用 SSH（更推薦）
```bash
# 生 key（如果沒有）
ssh-keygen -t ed25519 -C "ohtanishohei715@gmail.com"

# 複製到剪貼簿
cat ~/.ssh/id_ed25519.pub

# 貼到 GitHub: Settings → SSH and GPG keys → New SSH key

# 測試
ssh -T git@github.com
```

SSH 比 HTTPS 的好處：不需要 token、不會因 token 過期壞掉。

### Remote URL 改用 SSH
```bash
git remote set-url origin git@github.com:user/repo.git
```

## 0.6 編輯器

`git rebase -i`、`git commit`（不帶 `-m`）會開編輯器。預設 MSYS2 可能是 vim，不熟 vim 的話：

```bash
git config --global core.editor "code --wait"      # VS Code
git config --global core.editor "nano"             # nano
git config --global core.editor "vim"              # vim（預設）
```

VS Code 需要 `code` 在 PATH（VS Code 安裝時選「加入 PATH」）。

## 0.7 常用 alias（省時）

```bash
git config --global alias.s status
git config --global alias.co checkout
git config --global alias.sw switch
git config --global alias.br branch
git config --global alias.ci commit
git config --global alias.lg "log --oneline --graph --decorate --all"
git config --global alias.last "log -1 HEAD --stat"
git config --global alias.unstage "restore --staged"
```

用法：
```bash
git s
git lg
```

個人化設定，看你習慣。

## 0.8 有用的雜項

```bash
# 預設 pull 用 rebase（推薦，Ch5 細講）
git config --global pull.rebase true

# push 預設只 push 當前 branch
git config --global push.default current

# push 時自動設定 upstream tracking
git config --global push.autoSetupRemote true

# diff 顯示 color words
git config --global diff.colorMoved zebra

# rebase 時自動 stash
git config --global rebase.autoStash true

# fetch 時自動剪枝（刪掉本地已不存在遠端的 remote branch）
git config --global fetch.prune true
```

## 0.9 測試環境

```bash
# 建個 sandbox 亂試
mkdir /tmp/git-sandbox && cd /tmp/git-sandbox
git init
echo "test" > a.txt
git add a.txt
git commit -m "initial"
git log
```

整個課程很多命令你需要「**跑一次才會懂**」——保持一個 sandbox 隨時玩。

## 0.10 GitHub CLI（`gh`）

課程會用到 `gh` 管 PR，不是必需但很方便：

```bash
# MSYS2
pacman -S github-cli

# WSL/Ubuntu
sudo apt install gh
```

登入：
```bash
gh auth login
```

之後可以 `gh pr create` / `gh pr view` / `gh pr checkout`，不用離開 terminal。

## 0.11 檢查檔案

看看設定對不對：

```bash
cat ~/.gitconfig
# 或
git config --global --list
```

典型輸出：
```
user.name=ypp
user.email=ohtanishohei715@gmail.com
core.autocrlf=input
core.editor=code --wait
init.defaultbranch=main
pull.rebase=true
push.default=current
push.autosetupremote=true
fetch.prune=true
credential.helper=manager
```

## 本章重點
- Windows 必設 `core.autocrlf=input` 或用 `.gitattributes`
- 用 SSH key 或 GCM 免密碼
- 建議開 `pull.rebase=true`、`push.autoSetupRemote=true`、`fetch.prune=true`
- 裝 `gh` 讓 PR 在 CLI 完成
- 留一個 sandbox 隨時亂試
