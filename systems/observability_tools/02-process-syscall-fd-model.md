# Ch 2 — process / syscall / fd / signal 模型

> **目標**：補完「被觀察的對象」的模型——process（程式的執行實例）、syscall（程式向 kernel 請求服務的方式）、fd（檔案描述符，process 操作 I/O 的把手）、signal（kernel/process 之間的非同步通知）。這些是後面所有工具觀察的對象。理解它們，strace（看 syscall）、lsof（看 fd）、/proc（看 process 狀態）才有意義。本課指定「當作只會 C + 一點 OS」，這章把基礎補齊。

> **環境**：Linux，C 範例（gcc -g -O0）。搭配 strace/proc 觀察。

## 為什麼要先懂這些模型？

後面的工具都在觀察 process 的某個面向——strace 看它的 syscall、lsof 看它的 fd、/proc 看它的狀態、signal 工具看它收到的信號。如果你不知道「process 是什麼、syscall 是什麼、fd 是什麼」，看工具的輸出就像看天書。

這章補完這些基礎模型。本課假設你會 C、知道 process 大概是什麼，但可能對「syscall 怎麼運作」「fd 到底是什麼」「signal 怎麼打斷程式」不夠清楚。把這些搞懂，你看 strace 的輸出（一堆 syscall）、lsof 的輸出（一堆 fd）、/proc 的內容（process 狀態）才能理解。這是 Part 1 的基礎，後面所有觀察都建立在這裡。

> 如果你學過 linux_commands 課，這章的 process/fd/signal 概念有重疊（那課的 Ch 14-19）。這裡從「觀察」的角度重講，並補上 syscall 的機制。如果完全熟悉，可以快速瀏覽。

## 先建立直覺:程式 vs process

```
程式（program）vs process：

  程式：磁碟上的「檔案」（一堆指令，靜態的）
    /usr/bin/ls 是個程式（ELF 檔案，Ch 11）
        │
  process：程式的「執行實例」（跑起來的，動態的）
    執行 ls → 建立一個 process（有 PID、記憶體、fd、狀態...）
    同一個程式能跑出多個 process（開兩個 ls = 兩個 process）
        │
  process 有什麼（觀察的對象）：
    PID：身分證號
    記憶體空間：程式碼 + 資料 + heap + stack
    fd 表：開啟的檔案/socket（Ch 8 lsof 看這個）
    狀態：R/S/D/Z（Ch 7 /proc 看這個）
    signal 處理：怎麼回應各種 signal
        │
  → process 是「被觀察的對象」
    工具觀察的是 process 的各個面向
```

關鍵心智：**程式**是磁碟上的靜態檔案，**process** 是它的執行實例（動態的，有 PID、記憶體、fd、狀態）。後面的工具觀察的就是 process 的各個面向——strace 看它的 syscall、lsof 看它的 fd、/proc 看它的狀態。理解 process「有什麼」，你就知道工具在觀察什麼。

## syscall:程式向 kernel 請求服務

```
syscall（系統呼叫）：程式請 kernel 做事的唯一方式

  程式不能直接做特權操作（讀檔案、開網路、配記憶體...）
  那些是 kernel 的職責 → 程式要「請 kernel 做」
        │
  syscall 是「請求的介面」：
    程式：「kernel，幫我讀這個檔案」→ read() syscall
    kernel：執行讀取，回結果
        │
  使用者空間 vs kernel 空間：
    程式跑在「使用者空間」（受限，不能直接碰硬體）
    syscall 是「進入 kernel 空間」的門
    （透過特殊指令 syscall/int 0x80 切換）
        │
  → 程式做的「有意義的事」最終都是 syscall：
    讀寫檔案 = read/write
    開網路 = socket/connect
    配記憶體 = brk/mmap
    建 process = fork/clone
    → strace 攔截這些 syscall = 看見程式的「真實行為」
```

```bash
# 看一個程式做了哪些 syscall（strace 的本質）
cd ~/obslab
strace -c ls    # -c 統計各 syscall 被呼叫幾次
# % time   calls  syscall
#   ...     12    openat       ← 開檔案
#   ...     8     read
#   ...     5     write        ← 輸出
#   ...     3     mmap         ← 記憶體
# → ls 的「真實工作」就是這些 syscall

# 程式碼層的 read vs syscall 層的 read
# C: read(fd, buf, n) → 直接對應 read syscall
# C: fread(...) → 底層是 read syscall（library 包裝）
# C: printf(...) → 底層是 write syscall
```

> **程式做的「有意義的事」最終都是 syscall——這是 strace 能看見「真實行為」的原因**。程式跑在「使用者空間」（受限，不能直接碰硬體/檔案/網路），那些特權操作是 **kernel** 的職責。程式要做這些事，必須透過 **syscall**（系統呼叫）「請 kernel 做」——讀檔案是 `read`、開網路是 `socket`/`connect`、配記憶體是 `brk`/`mmap`、建 process 是 `fork`/`clone`。syscall 是「使用者空間進入 kernel 空間的門」（透過特殊 CPU 指令切換）。**這就是為什麼 strace 這麼強大**——它攔截程式的所有 syscall，等於看到程式做的所有「有意義的事」（任何 I/O、記憶體、process 操作都是 syscall）。你的 `printf` 底層是 `write`、`fopen` 底層是 `openat`、`malloc` 底層可能是 `brk`/`mmap`——strace 揭開這些。`strace -c`（統計各 syscall 次數）給你程式行為的「總覽」。理解「程式行為 = 一串 syscall」，你就懂了 strace 的本質，也理解了為什麼它是 debug 的主力——當你想知道「程式實際在做什麼」，看它的 syscall 就對了。

## fd:process 操作 I/O 的把手

```
fd（file descriptor，檔案描述符）：process 操作 I/O 的「把手」

  process 不直接操作檔案/socket，而是透過「fd」（一個小整數）
    open("file") → 回傳 fd 3
    read(3, ...) → 從 fd 3 讀（kernel 知道 3 對應哪個檔案）
        │
  fd 是 process「開啟檔案表」的索引：
    fd 0：stdin（標準輸入）
    fd 1：stdout（標準輸出）
    fd 2：stderr（標準錯誤）
    fd 3+：你開的檔案/socket/pipe...
        │
  fd 不只是檔案——「一切皆檔案」：
    一般檔案、socket（網路）、pipe、裝置、甚至 epoll
    都用 fd 操作（統一的介面）
        │
  → lsof（Ch 8）看的就是「process 開了哪些 fd」
    /proc/<pid>/fd 也能看（Ch 7）
    很多 bug 是 fd 相關（fd 洩漏、操作錯 fd）
```

```bash
# 看一個 process 的 fd（/proc 或 lsof）
cat > sleeper.c <<'EOF'
#include <unistd.h>
#include <fcntl.h>
int main() {
    int fd = open("/tmp/test.txt", O_CREAT|O_WRONLY, 0644);  // fd 3
    sleep(60);    // 開著 fd 睡 60 秒（方便觀察）
    return 0;
}
EOF
gcc -o sleeper sleeper.c
./sleeper &
SLEEPER_PID=$!

# 看它的 fd（/proc，Ch 7）
ls -l /proc/$SLEEPER_PID/fd
# 0 -> /dev/pts/0    (stdin)
# 1 -> /dev/pts/0    (stdout)
# 2 -> /dev/pts/0    (stderr)
# 3 -> /tmp/test.txt (你開的檔案！)

# 用 lsof 看（Ch 8）
lsof -p $SLEEPER_PID
kill $SLEEPER_PID
```

> **fd 是 process「開啟檔案表」的索引，是 I/O 操作的統一把手——lsof 和 /proc/fd 觀察的就是它**。process 不直接操作檔案/socket，而是透過 **fd**（一個小整數）——`open("file")` 回傳 fd 3，之後 `read(3, ...)` 從 fd 3 讀（kernel 知道 3 對應哪個檔案）。fd 0/1/2 是約定的 stdin/stdout/stderr，fd 3+ 是你開的。關鍵是「**一切皆檔案**」——一般檔案、socket（網路連線）、pipe、裝置，甚至 epoll，**全用 fd 操作**（統一介面）。所以「process 開了哪些 fd」這個資訊極有價值——它告訴你 process 在操作哪些檔案、開了哪些網路連線、有沒有 fd 洩漏。**lsof**（Ch 8）和 **/proc/<pid>/fd**（Ch 7）就是觀察 fd 的工具。很多 bug 是 fd 相關——fd 洩漏（一直開不關，最後 "too many open files"）、操作錯 fd、檔案被刪但 fd 還開著（佔空間）。練習 B 就是 fd 劫持調查。理解 fd 是 I/O 的把手，你看 lsof 的輸出（一堆 fd → 檔案/socket）就懂了它在告訴你什麼。這也呼應 linux_commands 課的 fd 概念，從「觀察」角度它是 debug I/O 問題的核心視角。

## signal:非同步的通知

```
signal（信號）：kernel/process 之間的「非同步通知」

  signal 是「打斷程式的通知」：
    程式正在跑 → 收到 signal → 暫停去處理 signal → 回來繼續
    （非同步：隨時可能發生，不是程式主動要的）
        │
  常見 signal：
    SIGSEGV（11）：segfault（記憶體存取錯誤）← 崩潰的常見原因
    SIGINT（2）：Ctrl-C（中斷）
    SIGTERM（15）：請求終止（kill 預設）
    SIGKILL（9）：強制終止（不可攔截）
    SIGCHLD：子 process 結束（parent 收到）
    SIGPIPE：寫一個沒人讀的 pipe
        │
  process 能「處理」signal（signal handler）：
    收到 SIGINT → 執行你註冊的 handler（如清理後退出）
    或用預設行為（SIGSEGV 預設 = 崩潰 + core dump）
        │
  → strace 能看到 process 收到的 signal（--- SIGSEGV ---）
    debug 崩潰時，看它收到什麼 signal 是關鍵
```

```bash
# 看一個程式收到的 signal（strace 顯示）
cat > crasher.c <<'EOF'
#include <stdio.h>
int main() {
    int *p = NULL;
    *p = 42;       // 寫 NULL → SIGSEGV！
    return 0;
}
EOF
gcc -g -O0 crasher.c -o crasher

strace ./crasher 2>&1 | tail -5
# ...
# --- SIGSEGV {si_signo=SIGSEGV, si_code=SEGV_MAPERR, si_addr=NULL} ---
# +++ killed by SIGSEGV (core dumped) +++
# → strace 看到它收到 SIGSEGV（寫 NULL 觸發），且崩潰位址是 NULL
#   這直接指出「解參考 NULL 指標」的 bug
```

> **signal 是「非同步打斷程式的通知」——strace 能看到 process 收到的 signal，這對 debug 崩潰很關鍵**。signal 是 kernel/process 之間的非同步通知——程式正跑著，隨時可能收到 signal（不是程式主動要的），暫停去處理再回來。常見的：**SIGSEGV**（segfault，記憶體存取錯誤，崩潰的頭號原因）、**SIGINT**（Ctrl-C）、**SIGTERM**（kill 預設，請求終止）、**SIGKILL**（強制終止，不可攔截）、**SIGCHLD**（子 process 結束）、**SIGPIPE**（寫沒人讀的 pipe）。process 能註冊 **signal handler** 處理特定 signal，或用預設行為（SIGSEGV 預設 = 崩潰+core dump）。**strace 能看到 process 收到的 signal**（顯示 `--- SIGSEGV ---`）——這對 debug 崩潰極有用：上面的例子，strace 直接顯示程式收到 SIGSEGV、崩潰位址是 NULL，立刻指出「解參考 NULL 指標」的 bug。這比「程式 segfault 了」這個模糊訊息有用得多——strace 告訴你**收到什麼 signal、什麼位址、在做什麼之後**。signal 也是 Ch 21（core dump）的核心——崩潰時的 signal 決定了 core dump 的產生。理解 signal 是「非同步通知」，你 debug「程式莫名其妙死了」時就知道去看「它收到什麼 signal」（strace 或 core dump）。

## fork/exec:process 怎麼誕生

```
process 怎麼誕生（fork + exec，理解後面 trace 子 process）：

  fork()：複製當前 process（一變二）
    parent 和 child 幾乎一樣（複製記憶體/fd...）
    fork 回傳：parent 收到 child 的 PID，child 收到 0
        │
  exec()：把當前 process「換成」另一個程式
    exec("ls") → 當前 process 的記憶體被 ls 的程式碼取代
    （PID 不變，但跑的是 ls 了）
        │
  典型模式（shell 執行命令）：
    fork() → child
    child: exec("命令") → 變成那個命令
    parent: wait() → 等 child 結束
        │
  → 這影響 trace：
    strace -f 才會 trace fork 出的子 process（重要！）
    不加 -f 只 trace 主 process，漏掉子 process 的行為
```

> **fork（複製 process）+ exec（替換程式）是 process 誕生的方式——這影響 strace 的 `-f`（trace 子 process）**。process 不是憑空出現的——它由 **fork**（複製當前 process，一變二）+ **exec**（把 process 換成另一個程式）產生。典型模式：shell 執行命令時 `fork` 出 child、child `exec` 成那個命令、parent `wait` 等它結束。這個機制對 trace 有重要影響：**strace 預設只 trace 主 process，不 trace 它 fork 出的子 process**——要加 **`-f`**（follow forks）才會跟著 trace 子 process。這是 strace 最常見的坑——你 trace 一個會 fork 子 process 的程式（如 shell script、會開子 process 的服務），不加 `-f` 就漏掉了子 process 的所有行為（而問題往往在子 process）。所以 trace 會 fork 的程式時記得 `strace -f`。理解 fork/exec，你也理解了 Ch 4 寫 mini-strace 時為什麼要處理子 process、Ch 19 注入時 process 的關係。這呼應 linux_commands 課的 fork/exec（那課的 Ch 15），這裡從「trace」角度——你要 trace 完整的程式行為，就要懂它怎麼生子 process 並用 `-f` 跟著 trace。

## 故意弄壞:綜合觀察一個 process

```bash
# 一個程式，從 process/syscall/fd/signal 各角度觀察
cd ~/obslab
cat > multi.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
int main() {
    printf("PID: %d\n", getpid());     // 我的 PID
    int fd = open("/tmp/multi.txt", O_CREAT|O_WRONLY, 0644);  // fd（syscall: openat）
    write(fd, "data\n", 5);            // syscall: write
    pid_t child = fork();              // 生子 process
    if (child == 0) {                  // child
        execlp("echo", "echo", "from child", NULL);  // exec
    }
    sleep(30);                         // 睡著（方便觀察）
    return 0;
}
EOF
gcc -o multi multi.c

# syscall 視角（記得 -f trace 子 process！）
strace -f ./multi 2>&1 | grep -E 'openat|write|clone|execve' | head
# openat(...) = 3            ← 開檔案
# write(3, "data\n", 5)      ← 寫
# clone(...) / fork          ← 生子 process
# execve("echo"...)          ← 子 process exec（-f 才看得到！）

# 跑起來，從其他角度看
./multi &
PID=$!
sleep 1
# fd 視角（Ch 7/8）
ls -l /proc/$PID/fd          # 看它開的 fd
# process 狀態視角（Ch 7）
cat /proc/$PID/status | grep State   # State: S (sleeping，因為 sleep)
kill $PID
```

> 這個實驗綜合了本章的所有模型——**process**（getpid 的 PID）、**syscall**（openat/write/clone/execve）、**fd**（/proc/fd 看開的檔案）、**signal**（kill 送 SIGTERM）、**fork/exec**（生子 process 並 exec echo）。關鍵觀察：`strace -f`（加 -f）才看得到子 process exec echo 的行為——這驗證了「trace 會 fork 的程式要用 -f」。從不同角度看同一個 process（syscall 用 strace、fd 用 /proc/fd、狀態用 /proc/status）展示了「process 有很多面向，不同工具看不同面向」。這建立了後面所有觀察的基礎——你現在知道 process「有什麼」（PID/記憶體/fd/狀態/signal 處理），所以後面 strace（看 syscall）、lsof（看 fd）、/proc（看狀態）、Ch 21（看 signal/core dump）觀察的都是這些面向。把這個模型建立好，後面的工具就都有了意義。

## 動手練習

1. 看 syscall：`strace -c ls`，看 ls 做了哪些 syscall，理解「程式行為 = syscall」

2. 看 fd：寫一個開檔案後 sleep 的程式，用 `ls -l /proc/<pid>/fd` 看它的 fd（0/1/2 + 你開的）

3. 看 signal：寫一個會 segfault 的程式，`strace` 它，看 `--- SIGSEGV ---`，理解崩潰的 signal

4. fork 與 -f：寫一個 fork 子 process 的程式，對比 `strace` 和 `strace -f`，看 -f 才看到子 process

5. 綜合觀察：跑「故意弄壞」的 multi.c，從 syscall/fd/狀態各角度觀察同一個 process

## 本章重點整理

- 程式（靜態檔案）vs process（執行實例，有 PID/記憶體/fd/狀態/signal）——工具觀察的是 process 的各面向
- syscall 是程式請 kernel 做事的唯一方式；程式的「有意義行為」都是 syscall（read/write/openat/mmap/fork）——strace 看見它們
- fd 是 process 操作 I/O 的把手（開啟檔案表的索引）；一切皆檔案（檔案/socket/pipe 都用 fd）——lsof/proc/fd 觀察它
- signal 是非同步通知（SIGSEGV 崩潰/SIGINT Ctrl-C/SIGTERM 終止）；strace 看得到收到的 signal（debug 崩潰）
- fork（複製）+ exec（替換）產生 process；strace -f 才 trace 子 process（trace 會 fork 的程式必加）

## 自我檢核

- [ ] 能說出 process「有什麼」（PID/記憶體/fd/狀態/signal），這些是工具觀察的對象
- [ ] 理解 syscall 是什麼，為什麼「程式行為 = syscall」讓 strace 強大
- [ ] 知道 fd 是 I/O 的把手，lsof/proc/fd 觀察它
- [ ] 知道 signal 是非同步通知，strace 怎麼幫 debug 崩潰
- [ ] 知道 fork/exec 怎麼生 process，為什麼 strace 要 -f

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 6, 24-26, 20-22** — Kerrisk
  - **讀哪幾章**：Ch 6（process）、Ch 24-26（fork/exec/wait）、Ch 20-22（signal）、Ch 5（fd/file I/O）
  - **這本書的定位**：這些模型的權威；本課的底層全部來自這裡
  - **前提**：會 C

### 文章

- **[What is a syscall](https://jvns.ca/blog/2014/03/02/what-happens-if-you-write-a-tcp-stack-in-python/) / Julia Evans 的 syscall 文章**
  - **這篇說什麼**：syscall 怎麼運作、使用者空間 vs kernel
  - **為什麼值得讀**：把 syscall 機制講得易懂

### 官方文件

- **[syscalls(2)](https://man7.org/linux/man-pages/man2/syscalls.2.html)** — Linux man-pages
  - **讀哪裡**：syscall 列表（看有哪些 syscall）
  - **為什麼值得讀**：所有 syscall 的索引，trace 時查某個 syscall 是什麼用

下一章深入 ptrace——debugger 和 strace 的底層機制。理解 ptrace 怎麼讓一個 process 控制和觀察另一個，你就理解了「工具怎麼看到 syscall」的祕密。

→ [Ch 3 ptrace 深入：debugger 的基礎](./03-ptrace-syscall-deep-dive.md)
