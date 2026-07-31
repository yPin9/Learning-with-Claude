# Ch 0 — 環境搭建

> **目標**：準備一個能安全實驗、能觀察底層的 Linux 環境，安裝本課的觀測工具（strace/ltrace/lsof），並學會用 strace 看一個指令底層做了什麼 syscall——這是本課把「指令」和「kernel」連起來的核心工具。

> **環境**：Ubuntu 22.04 / Debian 12，bash 5.x，coreutils 9.x，strace 5.x。多數內容在任何近代 Linux 通用；版本相關處會標注。

## 為什麼命令列學習也需要「搭環境」？

你可能想：「Linux 命令列？開個終端機不就好了？」確實，跑指令不用準備什麼。但本課不只教「怎麼用指令」，而是教「指令底層對 kernel 做了什麼」。要看到底層，需要**觀測工具**——`strace`（看 syscall）、`lsof`（看開啟的檔案）、`/proc`（看 process 狀態）。

而且，本課會大量「故意弄壞」——刪錯檔案、設錯權限、製造 zombie process。這些實驗在你日常用的系統上做有風險。所以我們先準備一個能安全玩壞的環境。

## 先建立直覺：本課的工具分三層

```
你打的指令（ls, cp, grep...）
        │
   ┌────┴──────────────────────────────────┐
   │  觀測層（本課的 X 光機）                 │
   │   strace：看指令呼叫了哪些 syscall      │
   │   ltrace：看指令呼叫了哪些 library 函式  │
   │   lsof：  看 process 開了哪些檔案/fd     │
   │   /proc： kernel 暴露的 process/系統狀態 │
   └────┬──────────────────────────────────┘
        ▼
   kernel（真正做事的：開檔、配記憶體、排程...）
```

這三層觀測工具是本課的招牌——每學一個指令，我們就用它們看「這個指令底層到底做了什麼」。`ls` 不再是黑盒子，而是「一連串 `openat`、`getdents64`、`statx`、`write` syscall」。

## Step 1：取得一個可實驗的 Linux

如果你已經在 Linux，跳過。否則：

```bash
# 選項 A：WSL2（Windows 上最方便）
wsl --install -d Ubuntu

# 選項 B：VM（VirtualBox/multipass，最接近真實且能玩壞）
multipass launch 22.04 --name cmdlab
multipass shell cmdlab

# 選項 C：容器（快，但某些底層觀測受限）
docker run -it --name cmdlab ubuntu:22.04 bash
# 容器內 strace 可能需要 --cap-add SYS_PTRACE
```

> 為什麼推薦 VM 而非容器：本課會玩 process、signal、mount、權限——這些在容器裡受限（容器共享 host kernel、namespace 隔離）。學習階段用 VM 最能看到完整的底層行為。容器章節（如果有）才用容器。

## Step 2：安裝觀測工具

```bash
sudo apt update
sudo apt install -y \
    strace ltrace lsof \
    coreutils findutils grep sed gawk \
    procps psmisc \
    file tree man-db manpages-dev \
    shellcheck

# 確認版本
strace --version       # strace 5.x
bash --version         # GNU bash 5.x
ls --version           # coreutils 9.x（GNU coreutils）
```

各工具角色：

| 工具 | 提供什麼 | 為什麼需要 |
|---|---|---|
| `strace` | 追蹤 syscall | 本課的 X 光機，看指令底層 |
| `ltrace` | 追蹤 library 呼叫 | 看更高層的函式呼叫 |
| `lsof` | 列出開啟的檔案/fd | 看 process 開了什麼 |
| `procps` | ps/top/free/uptime | process 觀測 |
| `psmisc` | killall/fuser/pstree | process 管理 |
| `manpages-dev` | syscall 的 man page（第 2 節）| 查 syscall 文件 |
| `shellcheck` | shell 腳本靜態檢查 | Part 8 用 |

## Step 3：第一次用 strace 看穿一個指令

這是本課最重要的技能。用 `strace` 看 `ls` 底層做了什麼：

```bash
# strace 追蹤 ls 的所有 syscall
strace ls /tmp 2>&1 | head -40
```

你會看到大量 syscall。挑關鍵的解讀：

```
execve("/usr/bin/ls", ["ls", "/tmp"], 0x...) = 0   ← kernel 載入並執行 ls
                                                      （shell 用 execve 啟動 ls）
openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY) = 3  ← 載入動態連結器需要的東西
openat(..., "/lib/x86_64-linux-gnu/libc.so.6", ...) = 3  ← 載入 libc
...
openat(AT_FDCWD, "/tmp", O_RDONLY|O_DIRECTORY) = 3   ← 開啟 /tmp 目錄！
getdents64(3, ...) = ...                              ← 讀目錄內容（Ch 5）
statx(...)                                            ← 取得每個檔案的 metadata（Ch 4）
write(1, "file1  file2  ...\n", ...) = ...           ← 把結果寫到 stdout（fd 1）
close(3)                                              ← 關閉目錄
exit_group(0)                                         ← 結束
```

> 看到了嗎？`ls /tmp` 不是魔法，它是：`execve` 啟動 → 載入 libc → `openat` 開目錄 → `getdents64` 讀目錄 → `statx` 取每個檔案資訊 → `write` 印出來。每個 syscall 我們後面都會深入（openat→Ch 4、getdents64→Ch 5、fd→Ch 19）。**這個「用 strace 看穿指令」的習慣，是本課把命令列從黑盒子變透明的鑰匙。**

常用的 strace 選項：

```bash
strace -f ls           # -f：追蹤 fork 出的子 process
strace -e openat ls    # -e：只看特定 syscall（這裡只看 openat）
strace -c ls           # -c：統計各 syscall 的次數和耗時（不印每一個）
strace -p 1234         # -p：attach 到已存在的 process（PID 1234）
strace -tt ls          # -tt：每個 syscall 加時間戳
strace -o trace.log ls # -o：輸出到檔案
```

## Step 4：建立實驗用的 sandbox 目錄

本課會大量建立/刪除/弄壞檔案。建一個專屬實驗目錄：

```bash
mkdir -p ~/cmdlab
cd ~/cmdlab
# 之後所有「故意弄壞」的實驗都在這裡做，不影響系統
```

## Step 5：確認 /proc 可用

`/proc` 是 kernel 把 process 和系統狀態暴露成的虛擬檔案系統——本課反覆用它看底層：

```bash
# /proc 裡每個數字目錄是一個 process（以 PID 命名）
ls /proc | head
# 1  2  3  ...  self  cpuinfo  meminfo ...

# 看你目前 shell 的資訊（/proc/self 指向當前 process）
cat /proc/self/status | head    # process 狀態（state、PID、記憶體...）
ls -l /proc/self/fd             # 當前 process 開的 file descriptor（Ch 19）

# 系統資訊
cat /proc/cpuinfo | grep "model name" | head -1
cat /proc/meminfo | head -3
```

> `/proc` 不是真的磁碟檔案——它是 kernel 即時生成的。`cat /proc/self/status` 每次都不同（反映當下狀態）。這個「把 kernel 內部狀態暴露成檔案」的設計是 Unix「一切皆檔案」哲學的體現（Ch 1 詳述），也是本課觀測底層的主要視窗之一。

## 故意弄壞：在 sandbox 裡安全實驗

本課的核心手法是「故意弄壞看現象」。在 `~/cmdlab` 裡，這很安全：

```bash
cd ~/cmdlab

# 例：建立一個檔案，用 strace 看 rm 怎麼刪它
echo "hello" > victim.txt
strace -e unlink,unlinkat rm victim.txt
# unlinkat(AT_FDCWD, "victim.txt", 0) = 0
#   ↑ rm 底層就是呼叫 unlinkat！（Ch 11 詳述）
```

這種「用 strace 看穿指令的底層動作」會貫穿全課。

## 踩雷集錦

1. **在容器裡 strace 失敗（Operation not permitted）**：容器預設沒有 `SYS_PTRACE` capability。`docker run --cap-add SYS_PTRACE` 或學習階段用 VM

2. **strace 輸出太多看不懂**：第一次看 strace 會被淹沒。用 `-e` 只看你關心的 syscall（如 `-e openat,read,write`），或 `-c` 看統計。不用每個 syscall 都懂

3. **用了 BusyBox 版的指令（如 Alpine）**：Alpine 等用 BusyBox 的精簡版指令，行為和 GNU coreutils 有差異（選項、輸出格式）。本課以 GNU coreutils 為準，用 Ubuntu/Debian 最一致

4. **在系統目錄做「故意弄壞」實驗**：在 `~/cmdlab` 做實驗。在 `/`、`/etc` 等亂搞可能搞壞系統。永遠在 sandbox

5. **strace 拖慢程式很多**：strace 攔截每個 syscall，會讓程式慢幾倍到幾十倍。它是 debug 工具，不是日常用。觀測完就好，不要 strace 跑生產負載

## 進階：strace 之外的觀測工具

strace 是本課主力，但還有更現代的工具（後續課程或進階會碰到）：

```bash
# ltrace：追蹤 library 函式（比 syscall 更高層）
ltrace ls 2>&1 | head        # 看 ls 呼叫了哪些 libc 函式

# lsof：看一個 process 開了什麼
lsof -p $$                   # $$ 是當前 shell 的 PID

# 更現代的：bpftrace/eBPF（如果你學過 bpf 課程）
# sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat { printf("%s\n", str(args->filename)); }'
```

> strace 用 `ptrace` syscall 實作（攔截每個 syscall），overhead 高。現代的 eBPF 工具（bpftrace）overhead 低得多，適合生產觀測。但學習階段 strace 的「一行一行看 syscall」最直觀。如果你修過 eBPF 課程，會發現本課的 strace 觀測和 eBPF 的 syscall tracing 是同一件事的不同工具。

## 動手練習

1. 用 `strace ls ~/cmdlab` 看 ls 的完整 syscall 序列。找出 `openat`（開目錄）、`getdents64`（讀目錄）、`write`（輸出）。記下你看不懂的 syscall（後面章節會解釋）

2. 用 `strace -c ls /usr/bin` 看 ls 一個大目錄時，哪個 syscall 被呼叫最多次（提示：每個檔案要 stat 一次）

3. 探索 /proc：`cat /proc/self/status`、`ls -l /proc/self/fd`、`cat /proc/self/cmdline | tr '\0' ' '`。理解 /proc/self 是「當前 process」的視窗

4. 故意弄壞：在 `~/cmdlab` 建一個檔案，用 `strace -e openat cat <file>` 看 cat 怎麼開檔讀檔。對比 `strace -e openat echo hi`（echo 不開檔，是 shell builtin）

## 本章重點整理

- 本課不只教指令用法，更教「指令底層對 kernel 做了什麼 syscall」
- 核心觀測工具：strace（看 syscall）、ltrace（看 library 呼叫）、lsof（看開啟的檔案）、/proc（kernel 狀態視窗）
- `strace <命令>` 是本課的 X 光機：把指令拆解成一連串 syscall，看穿黑盒子
- 用 VM 而非容器學習（process/signal/mount/權限在容器受限）；在 ~/cmdlab sandbox 安全做「故意弄壞」實驗
- /proc 是 kernel 即時生成的虛擬檔案系統，是觀測 process/系統狀態的主要視窗

## 自我檢核

- [ ] 能用 strace 看一個指令的 syscall 序列，並指出開檔（openat）、讀寫（read/write）等關鍵 syscall
- [ ] 知道 strace 的 `-f`/`-e`/`-c`/`-p` 各做什麼
- [ ] 知道為什麼學習階段用 VM 而非容器
- [ ] 能用 /proc/self 看當前 process 的狀態和開啟的 fd
- [ ] 理解 strace 的 overhead（debug 用，不跑生產）

## 延伸閱讀

### 部落格 / 文章

- **[Strace zine / strace posts](https://jvns.ca/blog/2015/04/14/strace-zine/)** — Julia Evans
  - **這篇說什麼**：用最易懂的方式講 strace 怎麼用、能看到什麼
  - **讀哪裡**：strace zine 和她其他 strace 文章
  - **為什麼值得讀**：Julia Evans 把 strace 講得比任何文件都清楚，本課全程用 strace，這是最佳入門

### 官方文件

- **[strace(1) man page](https://man7.org/linux/man-pages/man1/strace.1.html)**
  - **讀哪裡**：OPTIONS 的 `-e`、`-f`、`-c`、`-p`
  - **學什麼**：strace 的完整選項；本課用了核心的幾個
  - **前提**：本章

- **[proc(5) man page](https://man7.org/linux/man-pages/man5/proc.5.html)**
  - **讀哪裡**：/proc/[pid]/status、/proc/[pid]/fd 那幾節
  - **學什麼**：/proc 暴露的所有資訊；本課反覆用 /proc 觀測
  - **前提**：無

→ [Ch 1 命令列的本質](./01-shell-essence.md)
