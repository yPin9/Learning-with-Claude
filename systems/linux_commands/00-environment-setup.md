# Ch 0 — 環境準備

> 目標：在你的機器上跑起一個能動手做的 Linux 環境，並確認基本工具齊全。

## 選哪種環境

三個選項，按推薦度排序：

**WSL2（Windows 用戶首選）**

```
Windows 10/11 → WSL2 → Ubuntu 22.04 LTS
```

安裝方式：

```powershell
# 在 PowerShell（管理員）執行
wsl --install -d Ubuntu-22.04
# 重開機後設定 username 和 password
```

WSL2 是真正的 Linux kernel（不是模擬），效能接近原生，網路和檔案系統整合也好。

**虛擬機器（需要完整 Linux 桌面體驗）**

VirtualBox + Ubuntu 22.04 LTS ISO，記憶體給 2GB 以上，磁碟 20GB 以上。

**雲端（不想裝本機）**

直接在 [killercoda.com](https://killercoda.com) 或 [play-with-docker.com](https://labs.play-with-docker.com) 用瀏覽器終端。免安裝，但關掉就消失。

## 確認環境

進入終端機後，確認以下工具存在：

```bash
bash --version    # GNU bash, version 5.x
uname -r          # Linux kernel 版本
ls --version      # coreutils 版本
grep --version
awk --version
sed --version
```

如果缺工具（WSL 可能缺幾個），用套件管理安裝：

```bash
sudo apt update && sudo apt install -y coreutils grep gawk sed findutils
```

## 終端機基本操作

幾個救命快速鍵，第一天就要記：

| 快速鍵 | 效果 |
|--------|------|
| `Ctrl+C` | 強制結束目前執行的程式 |
| `Ctrl+D` | 送出 EOF，等同 `exit` |
| `Ctrl+Z` | 暫停行程（送到背景），之後可以 `fg` 恢復 |
| `Ctrl+L` | 清螢幕（等同 `clear`） |
| `Ctrl+R` | 逆向搜尋歷史指令 |
| `Tab` | 自動補全（按兩次 Tab 列出所有候選）|
| `↑`/`↓` | 翻歷史指令 |

## Shell 是什麼

你打指令的地方叫 **shell**，它是一個普通的程式，負責接受輸入、解析指令、啟動其他程式：

```
你 → 鍵盤 → 終端機（Terminal）→ Shell（bash）→ Kernel → 硬體
```

`bash`（Bourne Again SHell）是 Linux 最常見的 shell，也是這整套課程的主角。確認你用的是 bash：

```bash
echo $SHELL
# /bin/bash

echo $BASH_VERSION
# 5.1.16(1)-release
```

## man：你最重要的工具

比 Google 更快、更準確：

```bash
man ls          # ls 的完整文件
man man         # man 自己的文件
man 2 open      # section 2 = 系統呼叫（open() syscall）
man 5 passwd    # section 5 = 檔案格式（/etc/passwd 格式）
```

在 `man` 裡按 `/` 搜尋，按 `q` 離開，按 `Space` 翻頁。

## 動手練習

```bash
# 1. 確認環境
uname -a        # 完整系統資訊
whoami          # 目前登入的使用者
id              # UID / GID 資訊

# 2. 試試 man
man ls          # 讀 SYNOPSIS 那段，理解 [OPTION] 的意思

# 3. 看看你的 shell 歷史
history | tail -20
```

## 自我檢核

- [ ] Linux 環境能正常進入終端機
- [ ] `bash --version` 顯示 5.x
- [ ] 知道 `Ctrl+C`、`Ctrl+D`、`Tab` 的用途
- [ ] 能用 `man` 查任意指令的文件

→ [Ch 1 一切皆檔案：VFS 與 inode](./01-everything-is-a-file.md)
