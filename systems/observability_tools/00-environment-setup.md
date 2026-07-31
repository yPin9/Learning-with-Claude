# Ch 0 — 環境搭建

> **目標**：把整門課要用的觀察工具一次裝齊、確認你能用 ptrace（本課核心手法的底層）、建立一個編譯和實驗 C 程式的環境。讀完你有一個能跑所有後續實驗的環境，以及對「接下來會用到的工具」的初步認識。

> **環境**：Ubuntu 22.04+ / Debian 12+（其他 distro 套件名略異）。需要 sudo（部分工具如 perf、ptrace 別人的 process 需要權限）。gcc/clang。

## 為什麼環境要先搞定？

這門課的核心信條是「**對著壞掉的程式用工具**」——每一章都配一個故意寫壞的 C 程式，你用工具找出問題。要做到這個，你需要：能編譯 C 程式（gcc/clang）、裝齊觀察工具（strace/ltrace/lsof/perf/valgrind…）、以及能用 ptrace（很多工具的底層，本課還會親手用它寫 mini-strace）。

這章把這些一次備齊。值得花時間做對——後面每一章都依賴這裡裝的工具和編譯環境。特別是 ptrace 的權限設定（有些系統預設限制 ptrace，會讓 strace/gdb 無法 attach 別的 process），先搞定免得後面卡住。

## 先建立直覺:觀察的分層

```
觀察程式行為的分層（本課的地圖）：

  問「程式在做什麼」有很多層次，每層有對應工具：
        │
  syscall 層：程式對 kernel 做什麼請求？
    → strace（你的主力工具）
        │
  library 層：程式呼叫了哪些 library 函式？
    → ltrace
        │
  系統狀態層：process/fd/網路/記憶體的當前狀態？
    → /proc, lsof, ss, ps, vmstat
        │
  效能層：時間花在哪、哪裡慢？
    → perf, ftrace
        │
  記憶體/正確性層：leak？race？UB？
    → valgrind, sanitizers
        │
  → 不同問題在不同層觀察
    本課帶你掌握每一層，並理解工具怎麼運作
```

關鍵心智：觀察程式行為是**分層**的——syscall 層（strace 看程式對 kernel 的請求）、library 層（ltrace）、系統狀態層（/proc/lsof/ss）、效能層（perf/ftrace）、記憶體層（valgrind/sanitizers）。不同問題在不同層觀察。本課帶你掌握每一層，且不只「會用」，還理解「工具怎麼看到的」。

## 安裝工具包

一次裝齊整門課需要的工具：

```bash
sudo apt update

# 編譯與開發（C 範例需要）
sudo apt install -y \
    build-essential \    # gcc, make 等
    clang \              # clang（sanitizers 用，Ch 18）
    gdb \                # debugger（對照用）
    libc6-dbg            # glibc debug symbols（valgrind 用）

# 核心觀察工具
sudo apt install -y \
    strace \             # syscall trace（主力，Ch 5）
    ltrace \             # library call trace（Ch 6）
    lsof \               # 看開啟的檔案/fd（Ch 8）
    iproute2 \           # ss（Ch 9）
    tcpdump \            # 抓封包（Ch 9）
    procps \             # ps, top, vmstat（Ch 10）
    sysstat              # iostat, pidstat, sar, mpstat（Ch 10）

# 效能與 tracing
sudo apt install -y \
    linux-tools-common linux-tools-generic \   # perf（Ch 12）
    bpftrace             # bpftrace（Ch 14）
# 注意：perf 可能要裝對應 kernel 版本的 linux-tools-$(uname -r)

# 記憶體與正確性
sudo apt install -y \
    valgrind             # memcheck/helgrind/callgrind（Ch 15-17）

# ELF 分析（Ch 11）
sudo apt install -y binutils    # nm, objdump, readelf, strings

# 驗證關鍵工具裝好了
strace --version
ltrace --version
valgrind --version
perf --version 2>/dev/null || echo "perf 需要 linux-tools-$(uname -r)"
```

```bash
# 建一個課程實驗目錄
mkdir -p ~/obslab
cd ~/obslab
echo "obslab ready"
```

## 確認 ptrace 可用

ptrace 是很多工具（strace/gdb）的底層，也是本課要親手用的（Ch 3-4 寫 mini-strace）。先確認它能用：

```bash
# 寫一個最簡單的 C 程式測試
cat > hello.c <<'EOF'
#include <stdio.h>
int main() {
    printf("Hello, observability!\n");
    return 0;
}
EOF
gcc -o hello hello.c

# 用 strace 看它（strace 用 ptrace 攔截 syscall）
strace ./hello
# 你會看到一堆 syscall：execve, brk, openat, write...
# write(1, "Hello, observability!\n", 22) = 22   ← printf 底層是 write！
# → 如果這個能跑，ptrace 對「自己啟動的 process」可用

# === ptrace attach 別人的 process（可能受限）===
# 有些系統限制「ptrace 別的 process」（Yama 安全機制）
cat /proc/sys/kernel/yama/ptrace_scope
# 0 = 不限制（能 ptrace 任何自己的 process）
# 1 = 只能 ptrace 子 process（預設，較嚴格）
# 2+ = 更嚴格

# 如果是 1，attach 別的 process（strace -p）可能要 sudo
# 暫時放寬（重開機失效，僅實驗用）：
# sudo sysctl kernel.yama.ptrace_scope=0
```

```
strace 看 ./hello 的輸出解讀：

  execve("./hello", ...)        ← 載入並執行程式
  brk(NULL)                     ← 記憶體配置（malloc 底層）
  openat(... "libc.so.6" ...)   ← 載入 C 標準函式庫
  mmap(...)                     ← 映射記憶體
  write(1, "Hello...", 22) = 22 ← printf 底層！寫到 fd 1（stdout）
  exit_group(0)                 ← 程式結束
        │
  → 你的 printf 底層是 write syscall
    strace 讓你看到「程式對 kernel 的每個請求」
    這就是本課的核心：看見程式的「真實行為」
```

> **`strace ./hello` 讓你看到「printf 底層其實是 write syscall」——這是本課的核心震撼：程式的「真實行為」是一串 syscall**。你寫 `printf("Hello\n")`，但程式對 kernel 的真實請求是 `write(1, "Hello\n", 6)`——strace 揭開了這層。這就是「觀察程式行為」的精髓——不是看你**以為**程式在做什麼（讀原始碼），而是看它**實際**對 kernel 做了什麼（strace）。`ptrace_scope`（Yama 安全機制）控制「能不能 ptrace 別的 process」——預設 1（只能 ptrace 自己的子 process），所以 `strace -p <別人的PID>` 可能要 sudo。本課大多 trace「自己啟動的程式」（`strace ./prog`），不受這限制。但 Ch 3-4 寫 mini-strace、Ch 19 做注入時會深入 ptrace 的權限。先確認 `strace ./hello` 能跑（能看到 syscall），你的環境就 OK。如果連這都不行，可能是容器環境限制了 ptrace（Docker 預設限制 SYS_PTRACE capability，要 `--cap-add SYS_PTRACE` 或 `--privileged`）。

## 各工具的權限需求

```
本課工具的權限需求（先知道，免得後面卡住）：

  不需要特殊權限（trace 自己的程式）：
    strace ./prog, ltrace ./prog, valgrind ./prog
    lsof（看自己的）, /proc/self/*
        │
  可能需要 sudo（trace/觀察別人的 process）：
    strace -p <別人的PID>     （ptrace_scope 限制）
    lsof -p <別人的PID>
    cat /proc/<別人>/...      （部分需要）
        │
  需要 sudo / 特殊設定：
    perf（kernel.perf_event_paranoid 控制）
    tcpdump（CAP_NET_RAW，Ch 9）
    ftrace（/sys/kernel/tracing 需要 root，Ch 13）
    bpftrace（需要 root 或 CAP_BPF，Ch 14）
        │
  → 大多數實驗 trace 自己的程式，不用 sudo
    觀察別人 process 或 kernel 層才需要權限
```

```bash
# perf 的權限（如果 perf 報權限錯誤）
cat /proc/sys/kernel/perf_event_paranoid
# 3/2 = 嚴格（一般使用者受限）
# -1 = 不限制
# 放寬（實驗用）：sudo sysctl kernel.perf_event_paranoid=-1

# 確認你能編譯帶各種選項的程式（後面會用）
gcc -g -O0 hello.c -o hello_debug          # -g debug symbols, -O0 不優化
clang -fsanitize=address hello.c -o hello_asan 2>/dev/null && echo "ASan OK"  # Ch 18
```

> **大多數實驗 trace「自己啟動的程式」不需要 sudo——只有觀察別人的 process 或 kernel 層才需要權限**。這降低了學習門檻——你可以放心地 `strace ./myprog`、`valgrind ./myprog`、`ltrace ./myprog` 而不用 sudo。需要權限的是：trace 別人的 process（`strace -p`，ptrace_scope 限制）、perf（perf_event_paranoid 控制）、tcpdump（CAP_NET_RAW，Ch 9）、ftrace/bpftrace（kernel 層，需 root）。先知道這些權限需求，後面遇到「permission denied」時就知道是權限問題（放寬對應的 sysctl 或用 sudo）。編譯選項也先熟悉：`-g`（debug symbols，讓工具能顯示函式名/行號，valgrind/gdb 用）、`-O0`（不優化，debug 時行為和原始碼一致，優化過的程式 trace 起來會「跳行」）、`-fsanitize=...`（sanitizers，Ch 18）。本課的 C 範例大多用 `-g -O0` 編譯（方便觀察），這是 debug 編譯的標準選項。

## 動手練習

1. 裝齊工具：跑安裝命令，逐一驗證（strace/ltrace/valgrind/perf 的 --version）

2. 第一個 strace：`gcc -o hello hello.c; strace ./hello`，找出 write syscall（你的 printf 底層）

3. 認識權限：看 `cat /proc/sys/kernel/yama/ptrace_scope` 和 `perf_event_paranoid`，理解權限設定

4. 對比優化：`gcc -O0` vs `gcc -O2` 編譯同個程式，之後（Ch 5）你會看到優化影響 trace 的可讀性

5. 探索 /proc/self：`cat /proc/self/status`、`ls /proc/self/fd`（你自己的 process 狀態，Ch 7 深入）

## 本章重點整理

- 觀察程式行為是分層的：syscall（strace）、library（ltrace）、系統狀態（/proc/lsof）、效能（perf）、記憶體（valgrind）
- 本課核心手法：對著壞掉的 C 程式用工具、理解「工具怎麼看到的」（不只會用）、分層觀察
- `strace ./hello` 揭開「printf 底層是 write syscall」——程式的真實行為是一串 syscall
- ptrace 是很多工具的底層；ptrace_scope（Yama）控制能否 trace 別人的 process（自己的不受限）
- 大多數實驗 trace 自己的程式不用 sudo；perf/tcpdump/ftrace/bpftrace 等 kernel 層才需權限

## 自我檢核

- [ ] 工具裝齊，能跑 strace/ltrace/valgrind
- [ ] 能用 strace 看一個程式的 syscall，找出 printf 底層的 write
- [ ] 知道觀察的分層，每層對應什麼工具
- [ ] 知道哪些操作需要 sudo（trace 別人/kernel 層），哪些不用
- [ ] 能用 gcc 編譯帶 -g -O0 的 debug 版程式

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 1-3** — Kerrisk
  - **讀哪幾章**：Ch 2（基本概念）、Ch 3（syscall 與 library function）
  - **這本書的定位**：本課的底層聖經；現在讀前幾章建立 syscall/library 的概念
  - **前提**：會 C

### 文章

- **[strace 介紹](https://jvns.ca/blog/2015/04/14/strace-zine/)** — Julia Evans
  - **這篇說什麼**：strace 為什麼強大、怎麼用
  - **讀哪裡**：整篇（短）
  - **為什麼值得讀**：把「strace 看見 syscall」的價值講得最生動，本課核心手法的最佳入門

### 官方文件

- **[ptrace(2) man page](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — Linux man-pages
  - **讀哪裡**：開頭的概覽（細節留待 Ch 3）
  - **為什麼值得讀**：ptrace 的權威，本課 Ch 3-4 會深入

下一章我們建立整門課的地圖——觀察工具的全景，看每個工具在「觀察程式行為」的哪個位置，以及怎麼選對工具。

→ [Ch 1 觀察工具全景](./01-observation-tools-overview.md)
