# Ch 2 — 接管一個 process 的根：ptrace

> **目標**：理解 GDB 控制 inferior 的唯一底層管道——`ptrace` 系統呼叫。這一章建立概念與直覺（細節留 Ch 39、Ch 41），但會給你一段**能跑的 50 行 mini tracer**，讓你親眼看到「GDB 其實沒那麼神」。

> **環境**：Linux x86_64，gcc 12+，GDB 13。ptrace 是 Linux/Unix 專屬；Windows 用的是完全不同的 Debugging API（本課不涵蓋）。

## 為什麼要懂 ptrace？

你可以一輩子用 GDB 而不知道 ptrace。但只要你想：

- 理解為什麼 attach 有時權限不足（`ptrace_scope`）
- 理解為什麼一個 process 不能被兩個 debugger 同時 trace
- 寫 anti-debugging / 反反偵測（資安、CTF）
- 自己寫 debugger（Ch 41）或 strace 類工具

…你就非懂 ptrace 不可。它是整個 Linux 除錯生態的地基。GDB、strace、ltrace、rr、各種 sandbox，全部建在它上面。

## 先建立直覺：一條受控的後門

正常情況下，process A **不能**讀寫 process B 的記憶體——這是 OS 的隔離保證，沒有它任何程式都能偷別人的密碼。

但 debug 的本質就是要打破這個隔離。OS 不能完全禁止（不然沒人能 debug），也不能完全開放（不然沒有安全）。解法是開一道**受控的後門**：

```
   一般 process 關係                     ptrace 建立的關係
   ┌─────┐    ┌─────┐                  ┌─────────┐  trace   ┌─────────┐
   │  A  │ ╳  │  B  │                  │ tracer  │ ───────> │ tracee  │
   └─────┘    └─────┘                  │ (GDB)   │ <─────── │(inferior)│
   彼此記憶體互不可見                   └─────────┘  stop 通知└─────────┘
                                        建立「親子」般的追蹤關係後，
                                        tracer 可以讀寫 tracee 的記憶體/暫存器，
                                        並在 tracee 每次「停」時被通知
```

關鍵詞：**tracer**（追蹤方，= GDB）與 **tracee**（被追蹤方，= inferior）。一旦建立這層關係，tracee 的每一次「停下來」（碰到 signal、syscall、被要求 single-step）都會喚醒 tracer，把控制權交給它。

## ptrace 的長相

```c
#include <sys/ptrace.h>
long ptrace(enum __ptrace_request request, pid_t pid, void *addr, void *data);
```

只有一個函式，靠第一個參數 `request` 切換功能。最常用的幾個：

| request | 作用 |
|---|---|
| `PTRACE_TRACEME` | tracee 自己呼叫：「我要被我的父 process trace」 |
| `PTRACE_ATTACH` / `PTRACE_SEIZE` | tracer 呼叫：接管一個已存在的 process |
| `PTRACE_CONT` | 讓 tracee 繼續執行 |
| `PTRACE_SINGLESTEP` | 讓 tracee 只執行一條機器指令就停 |
| `PTRACE_PEEKTEXT` / `PTRACE_PEEKDATA` | 讀 tracee 一個 word 的記憶體 |
| `PTRACE_POKETEXT` / `PTRACE_POKEDATA` | 寫 tracee 一個 word 的記憶體（← breakpoint 就靠這個 patch INT3） |
| `PTRACE_GETREGS` / `PTRACE_SETREGS` | 讀 / 寫 tracee 的所有暫存器 |
| `PTRACE_CONT` 帶 signal | 繼續，並送一個 signal 給 tracee |

整個 GDB 的執行控制，拆到最底就是這幾個 request 的組合。breakpoint = `POKETEXT` 寫 INT3 + `CONT` + 等 SIGTRAP；single-step = `SINGLESTEP`；`print x` = `PEEKDATA` 讀那段記憶體。**沒有魔法。**

## 兩種建立追蹤關係的方式

對應 Ch 1 講的 `run` vs `attach`：

### 方式一：自己生（對應 `run`）

```
父 process (未來的 tracer)
   │ fork()
   ├──────────────► 子 process
   │                   │ ptrace(PTRACE_TRACEME, ...)   ← 宣告「trace 我」
   │                   │ execve("./target")            ← 換成目標程式
   │                   │   ↑ exec 會觸發一次 stop，
   │ waitpid() <───────┘     父 process 在這裡接手
   │ 現在父是 tracer，子是 tracee
```

### 方式二：接管現有的（對應 `attach`）

```
tracer: ptrace(PTRACE_ATTACH, pid, ...)   ← 對一個正在跑的 PID 出手
        waitpid(pid, ...)                  ← 等它停下來
        ...操作...
        ptrace(PTRACE_DETACH, pid, ...)    ← 放手，tracee 繼續自由執行
```

## 親手寫一個 50 行的 mini tracer

光說不練沒感覺。下面這個程式啟動 `/bin/ls`，並**數它執行了幾條機器指令**——用的就是 GDB single-step 的同一個機制。

```c
// minitrace.c — 編譯：gcc -O0 minitrace.c -o minitrace
//             執行：./minitrace /bin/ls
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>     /* struct user_regs_struct */

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <prog>\n", argv[0]); return 1; }

    pid_t child = fork();
    if (child == 0) {
        /* 子 process：宣告被 trace，然後變身成目標程式 */
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execvp(argv[1], &argv[1]);
        perror("execvp");                 /* 只有 exec 失敗才會到這 */
        exit(1);
    }

    /* 父 process（tracer）*/
    int status;
    long steps = 0;
    waitpid(child, &status, 0);           /* 等子 process exec 後的第一次 stop */

    while (1) {
        /* 叫 tracee 執行「一條指令」就停 */
        ptrace(PTRACE_SINGLESTEP, child, NULL, NULL);
        waitpid(child, &status, 0);
        if (WIFEXITED(status)) break;     /* 程式結束了 */
        steps++;

        if (steps % 100000 == 0) {        /* 每 10 萬步偷看一次 PC */
            struct user_regs_struct regs;
            ptrace(PTRACE_GETREGS, child, NULL, &regs);
            printf("  [%ld steps] rip = 0x%llx\n", steps, regs.rip);
        }
    }
    printf("total instructions executed: %ld\n", steps);
    return 0;
}
```

跑起來：

```
$ gcc -O0 minitrace.c -o minitrace
$ ./minitrace /bin/echo hi
hi
  [100000 steps] rip = 0x7f...
  ...
total instructions executed: 387412
```

這 50 行裡，你已經用到了 GDB 的核心機制：`fork` + `TRACEME` + `execvp` 建立關係、`SINGLESTEP` 做執行控制、`GETREGS` 讀暫存器。**GDB 就是把這套放大、加上符號翻譯與一萬個功能。** Ch 41 會把這個 mini tracer 長成真的能下斷點、看 backtrace 的 mini debugger。

> 故意弄壞看看：把 `PTRACE_TRACEME` 那行註解掉再編。子 process 不再宣告被 trace，於是父的 `PTRACE_SINGLESTEP` 全部失敗（回傳 -1），程式直接全速跑完，step 數變成 0 或亂跳。這證明：**追蹤關係必須先建立，後面的控制才有效。**

## 一個 tracee 只能有一個 tracer

ptrace 關係是**獨佔**的。一個 process 同時只能被一個 tracer 追蹤。這解釋了幾個常見現象：

```
$ gdb -p 1234
...
ptrace: Operation not permitted.
```

可能是：

1. **已經被別人 trace**：例如它在另一個 GDB 裡、或被 strace 著。先 detach 那邊。
2. **`ptrace_scope` 限制**（最常見）：見下節。
3. **權限不足**：trace 別的使用者的 process 需要 root。

## ptrace_scope：現代 Linux 的安全鎖

`/proc/sys/kernel/yama/ptrace_scope` 控制「誰能 trace 誰」。這是 Yama LSM 加的保護，預設值常常擋住你 attach：

| 值 | 意思 |
|---|---|
| `0` | 經典行為：同 uid 的 process 可互相 trace |
| `1` | **預設（多數 distro）**：只能 trace 自己的**直系子孫**，attach 別的需 `CAP_SYS_PTRACE` |
| `2` | 只有 root（`CAP_SYS_PTRACE`）能 trace |
| `3` | 完全禁止 ptrace（連 root 也不行，要重開機才能改回） |

所以 `gdb -p` 一個自己起的、非子孫的 process 常常失敗。臨時放寬：

```bash
# 臨時（重開機失效）
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope

# 或單次用 sudo 跑 gdb（有 CAP_SYS_PTRACE）
sudo gdb -p 1234
```

> 認識論誠實：把 `ptrace_scope` 設成 0 會降低系統安全性（惡意程式更容易讀其他 process 記憶體竊取密鑰）。開發機臨時開無妨，正式機器別亂動。Ch 3 還會回來談 attach 權限。

## ptrace 與 signal 的關係

這是 ptrace 最容易暈的地方，但對 Ch 15（signal）很關鍵：**tracee 收到的 signal 會先被 tracer 攔截。**

當 tracee 收到任何 signal（包括 breakpoint 產生的 `SIGTRAP`），OS 不會直接把 signal 送給 tracee，而是：

1. 凍結 tracee
2. 喚醒 tracer，告訴它「tracee 想收到 signal N」
3. tracer 決定：吞掉它、原樣轉交、或換一個 signal 轉交（`PTRACE_CONT` 的第四個參數）

這就是為什麼 GDB 能用 `handle SIGSEGV nostop`（Ch 15）控制 signal 要不要傳給程式、要不要停。breakpoint 的 `SIGTRAP` 永遠被 GDB 吞掉（你的程式根本不知道有斷點），就是這個機制。

```
   tracee 觸發 SIGSEGV
        │
        ▼
   OS 攔下，凍結 tracee，通知 tracer (GDB)
        │
        ▼
   GDB 依 `handle` 設定決定:
     ├─ 停下來給你看 (預設 SIGSEGV: stop)
     ├─ 不停、把 signal 轉給 tracee (它的 handler 會跑)
     └─ 吞掉 (pass/nopass)
```

## 踩雷集錦

1. **忘記 `waitpid`**：每次 ptrace 讓 tracee 動（CONT/SINGLESTEP）之後，**必須** `waitpid` 等它停，否則父子節奏錯亂、ptrace 回 `ESRCH`。GDB 內部對每個 inferior 都嚴格維護這個「跑→等」節奏。
2. **PEEK/POKE 是以 word（8 byte）為單位**：不能只寫一個 byte。要 patch 一個 INT3（1 byte），得先 PEEK 出整個 word、改最低 byte、再 POKE 回去。Ch 39 會做這件事。
3. **把 attach 失敗歸咎於 GDB**：九成是 `ptrace_scope=1` 或權限。先 `cat /proc/sys/kernel/yama/ptrace_scope`。
4. **以為 ptrace 跨平台**：純 Linux/類 Unix。macOS 的 ptrace 殘缺（Apple 用 Mach exception + task port），Windows 完全另一套。本課所有 ptrace 內容只保證 Linux。
5. **trace 多執行緒程式以為一個 PID 就夠**：Linux 上每個 thread 是一個獨立的 tracee（用 TID）。GDB 對每個 thread 各自維護 ptrace 關係。Ch 16 細講。

## 進階：再往深一層

- **`PTRACE_SEIZE` vs `PTRACE_ATTACH`**：較新的 `SEIZE` 不會在 attach 當下硬塞一個 SIGSTOP，行為更乾淨，支援 `PTRACE_INTERRUPT`、group-stop 等。現代 GDB / strace 傾向用 SEIZE。
- **`PTRACE_O_*` options**：可設定「fork/clone/exec 時自動 trace 子代」（`PTRACE_O_TRACEFORK` 等），這是 GDB `follow-fork-mode`（Ch 17）的底層。
- **`PTRACE_GETSIGINFO`**：拿到 signal 的詳細資訊（例如 SIGSEGV 是存取哪個位址出錯），GDB 報「Cannot access memory at address 0x…」就靠它。
- **`process_vm_readv` / `/proc/<pid>/mem`**：比 PEEK 一次一 word 快得多的批次讀記憶體方式，現代 GDB 讀大塊記憶體優先用這些，PEEK 只當 fallback。

## 動手練習

1. 編譯本章的 `minitrace.c`，對不同程式跑，比較指令數（`/bin/true` vs `/bin/ls /usr`）。理解為什麼差這麼多。
2. 把 `PTRACE_SINGLESTEP` 改成 `PTRACE_CONT`，程式會直接跑完、step 數失準——體會 single-step 與 continue 的差別。
3. `cat /proc/sys/kernel/yama/ptrace_scope`，故意起一個 `sleep 999 &`，用非 sudo 的 `gdb -p` attach，觀察失敗訊息，再 `sudo` 一次成功。
4. 用 `strace -f ./minitrace /bin/echo hi 2>&1 | grep ptrace | head`——用 strace 觀察你的 tracer 怎麼呼叫 ptrace（strace 本身也用 ptrace，這是「trace 一個 tracer」的趣味場景）。

## 本章重點整理

- ptrace 是 Linux 上 debugger 控制 inferior 的**唯一**底層管道；GDB、strace、rr 全建在它上面。
- 建立追蹤關係有兩種：子 process `TRACEME`（對應 run）或 tracer `ATTACH`（對應 attach）。
- 一個 tracee 只能有一個 tracer；`ptrace_scope` 是現代 Linux 的安全鎖，常是 attach 失敗的原因。
- tracee 的 signal 會先被 tracer 攔截——這是 breakpoint（吞 SIGTRAP）與 `handle`（Ch 15）的基礎。

## 自我檢核

- [ ] 不看筆記，能不能畫出「fork + TRACEME + execve」建立追蹤關係的流程？
- [ ] breakpoint、single-step、`print x` 各自大致對應哪些 ptrace request？
- [ ] `gdb -p` 失敗時，你第一個會去檢查什麼？
- [ ] 為什麼說「tracee 的 signal 先到 tracer」是 GDB `handle` 功能的基礎？

## 延伸閱讀

### 官方文件

- **[man 2 ptrace](https://man7.org/linux/man-pages/man2/ptrace.2.html)**
  - **讀哪裡**：開頭的 overview、Stopped states、`ptrace(PTRACE_TRACEME)` 與 `PTRACE_SETOPTIONS` 各段。
  - **和本章的關聯**：本章每個 request 的精確語意都在這；Ch 39、Ch 41 寫 mini debugger 時這頁要常開著。
  - **注意**：signal 與 group-stop 那幾段很硬，先跳過，Ch 15 再回來。

### 部落格 / 文章

- **[How debuggers work: Part 2 (Breakpoints)](https://eli.thegreenplace.net/2011/01/27/how-debuggers-work-part-2-breakpoints)** — Eli Bendersky
  - **這篇說什麼**：用 ptrace PEEK/POKE 親手 patch INT3 實作一個 breakpoint。
  - **和本章的關聯**：把本章「POKETEXT 寫 INT3」變成可跑的 code；Ch 4、Ch 39 的預習。

- **[Playing with ptrace](https://www.linuxjournal.com/article/6100)** — Pradeep Padala, Linux Journal
  - **這篇說什麼**：經典入門，示範 PEEK/POKE 讀寫 tracee 記憶體與攔截 syscall。
  - **讀哪裡**：Part I 的記憶體讀寫；Part II 的 syscall 攔截對應 Ch 14 catchpoint。

### 進階

- **[strace 的設計](https://github.com/strace/strace)** 與 **[rr 怎麼用 ptrace](https://github.com/rr-debugger/rr/wiki/Technical-overview)**
  - **為什麼值得讀**：看 ptrace 在真實工具裡被推到極限的樣子；Ch 35 的 rr 預習。

懂了 GDB 怎麼「接管」一個 process，下一章我們從使用者角度把這件事做出來：怎麼啟動、附加、管理 inferior。

→ [Ch 3 啟動、附加、inferior 管理](./03-launching-attaching-inferiors.md)
