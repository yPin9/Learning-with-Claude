# Ch 3 — ptrace 深入：debugger 的基礎

> **目標**：理解 ptrace——讓「一個 process 控制和觀察另一個 process」的 syscall，是 strace、gdb 等所有 debugger/tracer 的底層。理解它怎麼 attach、怎麼在 syscall 攔截、怎麼讀寫被追蹤 process 的記憶體和暫存器。這章是 Ch 4（親手寫 mini-strace）的前置，也讓你理解「工具怎麼看到 syscall」的祕密——揭開 debugger 的魔法。

> **環境**：Linux，C（ptrace 是 C API）。x86-64 為例（暫存器名）。

## 為什麼 ptrace 是 debugger 的基礎？

你用 strace 看 syscall、用 gdb 設斷點單步——這些「觀察和控制另一個 process」的能力從哪來？答案是 **ptrace**（process trace）這個 syscall。它讓一個 process（tracer，如 strace/gdb）能附加到另一個 process（tracee，被觀察的），在它執行 syscall 時暫停它、讀寫它的記憶體和暫存器、控制它的執行。

理解 ptrace 是本課和一般工具教學的根本差別——你不只「會用 strace」，還理解「strace 怎麼用 ptrace 看到 syscall」。這揭開了 debugger 的魔法。Ch 4 你會親手用 ptrace 寫一個 mini-strace，Ch 19 用它做 process 注入。這章先把 ptrace 的機制講清楚——它是「控制另一個 process」這個超能力的來源。

## 先建立直覺:傀儡師控制傀儡

```
ptrace = 傀儡師（tracer）控制傀儡（tracee）

  tracer（如 strace/gdb）             tracee（被追蹤的程式）
    │                                   │
    ├─ attach / fork+TRACEME ──────────▶│ 成為「被控制」的狀態
    │                                   │
    │  tracee 每次「事件」（syscall/    │
    │  signal）都「暫停」，通知 tracer  │
    │                                   ▼
    │◀──── tracee 停在這 ──────────────│ (停止，等 tracer 指令)
    │                                   │
    ├─ 讀 tracee 的暫存器/記憶體 ───────▶│ （看它在做什麼）
    ├─ 改 tracee 的暫存器/記憶體 ───────▶│ （控制/注入）
    ├─ 「繼續」（PTRACE_CONT/SYSCALL）──▶│ 繼續執行到下一個事件
        │
  → tracer 能「暫停 tracee、看它的內部、改它、再放它走」
    這就是 debugger 的全部能力（設斷點、看變數、單步...都基於此）
```

關鍵心智：ptrace 讓 tracer（strace/gdb）像「傀儡師」控制 tracee（被追蹤的程式）——tracee 每次「事件」（syscall、signal）都暫停並通知 tracer，tracer 能讀寫它的暫存器和記憶體、再讓它繼續。**「暫停 → 觀察/修改 → 繼續」**這個循環，就是所有 debugger 能力（設斷點、看變數、單步、改值）的基礎。

> ptrace 觀察的是 Ch 2 的 process、syscall、signal。如果對這些不熟，回看 [Ch 2](./02-process-syscall-fd-model.md)。ptrace 是 C 層的 syscall，這章有 C 程式碼。

## ptrace 的基本操作

```c
// ptrace 的核心請求（PTRACE_*）：
#include <sys/ptrace.h>

// 建立追蹤關係（兩種方式）：
// 方式 1：被追蹤者自己宣告（child 自願被 trace）
ptrace(PTRACE_TRACEME, 0, NULL, NULL);   // child 說「trace 我」
// 然後 exec → parent 成為 tracer

// 方式 2：tracer 附加到已存在的 process
ptrace(PTRACE_ATTACH, pid, NULL, NULL);  // attach 到 pid（strace -p 用這個）

// 控制執行：
ptrace(PTRACE_CONT, pid, NULL, NULL);    // 繼續執行（到下個 signal/事件）
ptrace(PTRACE_SYSCALL, pid, NULL, NULL); // 繼續，但「下次 syscall 進/出時暫停」★
ptrace(PTRACE_SINGLESTEP, pid, ...);     // 單步執行一條指令（gdb 的 step）

// 讀寫 tracee 的狀態：
ptrace(PTRACE_GETREGS, pid, NULL, &regs);  // 讀暫存器（看 syscall 號/參數）
ptrace(PTRACE_SETREGS, pid, NULL, &regs);  // 改暫存器（注入/修改）
ptrace(PTRACE_PEEKDATA, pid, addr, NULL);  // 讀 tracee 記憶體的一個 word
ptrace(PTRACE_POKEDATA, pid, addr, data);  // 寫 tracee 記憶體
```

```
PTRACE_SYSCALL 是 strace 的核心：

  PTRACE_SYSCALL：「繼續執行，但下次 syscall 邊界暫停」
        │
  tracee 執行 → 要進入一個 syscall → 暫停（syscall-entry）
    → tracer 被通知 → 讀暫存器看「是哪個 syscall、什麼參數」
    → PTRACE_SYSCALL 繼續 → syscall 執行完要返回 → 暫停（syscall-exit）
    → tracer 讀暫存器看「回傳值」
    → PTRACE_SYSCALL 繼續 → 下一個 syscall...
        │
  → strace 就是反覆 PTRACE_SYSCALL，在每個 syscall 邊界
    讀暫存器解析「哪個 syscall、參數、回傳值」並印出
    這就是 Ch 4 你要寫的！
```

> **`PTRACE_SYSCALL`（在 syscall 邊界暫停）是 strace 的核心機制——理解它你就理解了 strace 怎麼運作**。ptrace 有很多請求，但 strace 的關鍵是 **`PTRACE_SYSCALL`**——它讓 tracee「繼續執行，但下次進入或離開 syscall 時暫停」。流程：tracee 跑到要進入某 syscall → 暫停（syscall-entry stop）→ tracer 被通知，用 `PTRACE_GETREGS` **讀暫存器**看「是哪個 syscall、參數是什麼」（syscall 號在某個暫存器、參數在其他暫存器，x86-64 是 rax 放號、rdi/rsi/rdx... 放參數）→ `PTRACE_SYSCALL` 繼續 → syscall 執行完要返回 → 再暫停（syscall-exit stop）→ tracer 讀暫存器看「回傳值」（在 rax）→ 繼續。**strace 就是反覆做這個**——在每個 syscall 邊界暫停、讀暫存器解析 syscall 和參數、印出來。這就是「strace 怎麼看到 syscall」的祕密——它不是什麼魔法，而是用 ptrace 在每個 syscall 邊界攔截並讀暫存器。`PTRACE_GETREGS`（讀暫存器）和 `PTRACE_PEEKDATA`（讀記憶體，因為 syscall 參數常是指標，要讀指向的內容如檔名字串）是讀取 tracee 狀態的關鍵。理解這個機制，Ch 4 你就能親手寫出 mini-strace——它正是這個循環的實現。

## syscall 在暫存器裡長什麼樣（x86-64）

```
x86-64 的 syscall 呼叫慣例（讀暫存器要知道）：

  syscall 號：    rax（orig_rax 在 ptrace 裡）
  參數 1-6：      rdi, rsi, rdx, r10, r8, r9
  回傳值：        rax（syscall 執行後）
        │
  例：write(fd, buf, count) 的 syscall
    rax = 1（write 的 syscall 號）
    rdi = fd
    rsi = buf（指標！要 PEEKDATA 讀它指向的內容）
    rdx = count
    執行後 rax = 寫入的 bytes 數（回傳值）
        │
  → tracer 在 syscall-entry 讀 rax 知道是哪個 syscall
    讀 rdi/rsi/rdx 知道參數
    在 syscall-exit 讀 rax 知道回傳值
    （syscall 號 → 名字的對照表要自己查，如 1=write, 0=read）
```

```bash
# 用 strace 看 write 的暫存器層真相（對照上面的理論）
echo "test" | strace -e trace=write cat 2>&1 | grep write
# write(1, "test\n", 5) = 5
#   → 翻譯後：write(fd=1, buf="test\n", count=5) 回傳 5
#   暫存器層：rax=1(write), rdi=1, rsi=指向"test\n", rdx=5, 回傳 rax=5
#   strace 幫你把暫存器值翻譯成可讀的（fd 名、字串內容、syscall 名）
```

> **strace 顯示的 `write(1, "test\n", 5) = 5` 背後是「讀暫存器 rax/rdi/rsi/rdx + 解析」——它把暫存器的原始值翻譯成人類可讀**。x86-64 的 syscall 呼叫慣例：syscall 號在 **rax**、參數依序在 **rdi/rsi/rdx/r10/r8/r9**、回傳值在 **rax**（執行後）。所以 `write(fd, buf, count)` 的暫存器是 rax=1（write 的號）、rdi=fd、rsi=buf（指標）、rdx=count。tracer 在 syscall-entry 讀這些暫存器就知道「程式要呼叫 write、參數是什麼」。關鍵細節：**參數常是指標**（如 write 的 buf、open 的檔名）——暫存器裡只有位址，要 `PTRACE_PEEKDATA` 去**讀那個位址指向的內容**（才能顯示字串）。這是 mini-strace 比「只讀暫存器」多的一步——要解參考指標讀字串。strace 顯示的可讀輸出（`write(1, "test\n", 5)`）是它做了很多翻譯：syscall 號 → 名字（1→write，查對照表）、fd → 可能的名字、指標 → 讀出的字串、回傳值。理解暫存器層的真相，你就知道 strace 在做什麼翻譯，也知道 Ch 4 寫 mini-strace 要處理什麼（讀暫存器、查 syscall 名、解參考指標讀字串）。注意不同架構暫存器名不同（ARM 是 x0-x7），但機制一樣。

## 一個最小的 ptrace 範例

```c
// minitrace_concept.c — ptrace 的最小骨架（Ch 4 會完整版）
#include <stdio.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    pid_t child = fork();
    if (child == 0) {
        // child：宣告被 trace，然後 exec 目標程式
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execvp(argv[1], &argv[1]);    // 執行 ./prog
    } else {
        // parent（tracer）：
        int status;
        waitpid(child, &status, 0);   // 等 child 停在 exec 後

        // 反覆在 syscall 邊界暫停
        while (1) {
            // 繼續到下一個 syscall 邊界
            ptrace(PTRACE_SYSCALL, child, NULL, NULL);
            waitpid(child, &status, 0);
            if (WIFEXITED(status)) break;   // child 結束了

            // 讀暫存器，看是哪個 syscall
            struct user_regs_struct regs;
            ptrace(PTRACE_GETREGS, child, NULL, &regs);
            // regs.orig_rax = syscall 號
            printf("syscall: %lld\n", regs.orig_rax);
            // （Ch 4 會把號翻成名字、讀參數）
        }
    }
    return 0;
}
```

```bash
# 編譯並跑（看它印出 syscall 號）
gcc -o minitrace_concept minitrace_concept.c
./minitrace_concept /bin/echo hi
# syscall: 59 (execve)
# syscall: 12 (brk)
# syscall: 257 (openat)
# ...
# → 你剛剛寫了一個「印出 syscall 號」的 mini-tracer！
```

> **這個 30 行的 C 程式就是 strace 的雛形——fork+TRACEME，然後反覆 PTRACE_SYSCALL + GETREGS**。骨架很簡單：`fork` 出 child，child 用 `PTRACE_TRACEME` 宣告「trace 我」然後 `exec` 目標程式；parent（tracer）`waitpid` 等 child 停在 exec 後，然後進入循環——`PTRACE_SYSCALL`（繼續到下個 syscall 邊界）+ `waitpid`（等它停）+ `PTRACE_GETREGS`（讀暫存器，`orig_rax` 是 syscall 號）+ 印出。這就是 strace 的核心循環！跑起來它會印出目標程式做的每個 syscall 號。Ch 4 會把它完整化——把 syscall 號翻成名字（59→execve，查對照表）、讀參數（rdi/rsi/rdx）、解參考指標讀字串（如 open 的檔名）、處理 syscall-entry/exit 配對、處理子 process（`-f`）。但核心機制就是這個 30 行——**理解了它，你就理解了 strace 的本質**。這是本課的精髓：不把 strace 當黑盒子，而是理解「它就是用 ptrace 在 syscall 邊界讀暫存器」，甚至自己寫一個。當你親手寫過 mini-strace，你對 strace 的理解和「只會用 strace」的人有質的差別——你知道它的能力來自哪、限制在哪、出問題時為什麼。

## ptrace 的限制與陷阱

```
ptrace 的限制（理解工具的邊界）：

  1. 一個 tracee 只能被「一個」tracer 追蹤：
     已經被 strace 的 process，gdb 不能同時 attach
        │
  2. ptrace 會「改變被追蹤程式的行為」：
     trace 讓程式變慢（每個 syscall 都暫停）
     → 某些 race condition 在 trace 下「消失」（Heisenbug）
        │
  3. 權限限制（Yama ptrace_scope，Ch 0）：
     不能隨便 ptrace 別人的 process（安全）
        │
  4. 反調試（anti-debugging）：
     有些程式偵測「自己被 trace」（ptrace TRACEME 自己會失敗）
     → 惡意軟體用這個躲避分析
        │
  → ptrace 強大但有邊界
    理解這些，你知道「為什麼有時 trace 不了/結果不對」
```

> **ptrace 會「改變被追蹤程式的行為」——這造成 Heisenbug（觀察改變了被觀察者），是觀察的根本限制**。ptrace 強大但有重要限制：(1) **一個 tracee 只能被一個 tracer 追蹤**——已被 strace 的 process，gdb 不能同時 attach（要先 detach）；(2) **trace 改變程式行為**——每個 syscall 都暫停讓程式**變慢很多**，這會影響時序敏感的 bug：某些 **race condition 在 strace 下「消失」**（trace 改變了時序，race 不再觸發）——這是經典的 **Heisenbug**（觀察行為改變了被觀察的對象，像量子力學的測不準）。所以「加了 strace 就不出錯，拿掉就出錯」是真實現象，要意識到 trace 的干擾；(3) **權限限制**（Yama，Ch 0）；(4) **反調試**——有些程式（特別是惡意軟體）偵測「自己被 trace」（如自己呼叫 ptrace TRACEME，如果已被 trace 會失敗）來躲避分析。理解這些限制，你知道「為什麼有時 trace 不了或結果不對」——race 在 trace 下消失（Heisenbug，要用其他方法如 TSan，Ch 18）、惡意軟體反調試（要更進階的手段）、權限不足（要 sudo/調 ptrace_scope）。這是「觀察的代價」——觀察本身會影響系統，這對效能 trace（perf 的低開銷設計就是為了減少干擾）和並發 debug（Heisenbug）特別重要。理解觀察工具的這個根本限制，你才能正確解讀它們的結果。

## 動手練習

1. 跑最小 tracer：編譯 minitrace_concept.c，trace 一個簡單命令，看它印出 syscall 號

2. 對照 strace：同一個程式用你的 mini-tracer（印號）和真 strace（印名字+參數），看差別（Ch 4 補上翻譯）

3. 讀暫存器：在 mini-tracer 裡印出 regs.rdi/rsi/rdx（參數），對照 strace 的參數

4. 體會 Heisenbug：寫一個簡單的 race（兩 thread 改同變數），看它在 strace 下行為是否改變

5. 看權限：試 `strace -p <別人的PID>`，理解 ptrace_scope 的限制（Ch 0）

## 本章重點整理

- ptrace 讓 tracer 控制 tracee（strace/gdb 的底層）：暫停 → 讀寫暫存器/記憶體 → 繼續，是所有 debugger 能力的基礎
- PTRACE_SYSCALL（在 syscall 邊界暫停）是 strace 的核心：每個 syscall 進/出時暫停，讀暫存器解析
- x86-64：syscall 號在 rax、參數在 rdi/rsi/rdx/r10/r8/r9、回傳值在 rax；指標參數要 PEEKDATA 讀內容
- 30 行 C（fork+TRACEME + 反覆 PTRACE_SYSCALL+GETREGS）就是 strace 的雛形——Ch 4 完整化
- ptrace 限制：一 tracee 一 tracer、trace 改變行為（Heisenbug，race 可能消失）、權限、反調試

## 自我檢核

- [ ] 能解釋 ptrace 怎麼讓一個 process 控制另一個（暫停/讀寫/繼續）
- [ ] 理解 PTRACE_SYSCALL 是 strace 的核心，怎麼在 syscall 邊界攔截
- [ ] 知道 syscall 號和參數在哪些暫存器（x86-64）
- [ ] 能看懂最小 tracer 的程式碼，理解它是 strace 的雛形
- [ ] 知道 ptrace 的限制，特別是 Heisenbug（trace 改變行為）

## 延伸閱讀

### 官方文件

- **[ptrace(2) man page](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — Linux man-pages
  - **讀哪裡**：PTRACE_SYSCALL、PTRACE_GETREGS、PTRACE_PEEKDATA 那幾個請求
  - **為什麼值得讀**：ptrace 的權威，Ch 4 寫 mini-strace 的參考

### 文章

- **[Playing with ptrace](https://www.linuxjournal.com/article/6100)** — Linux Journal（經典）
  - **這篇說什麼**：用 ptrace 寫 tracer 和注入的經典教學
  - **讀哪裡**：Part I（syscall trace）
  - **為什麼值得讀**：本章和 Ch 4/19 的權威教學，把 ptrace 用法講透

- **[Writing a Linux Debugger](https://blog.tartanllama.xyz/writing-a-linux-debugger-setup/)** — TartanLlama
  - **這篇說什麼**：用 ptrace 從零寫一個 debugger 的系列
  - **為什麼值得讀**：理解 debugger（不只 tracer）怎麼用 ptrace，更深入

### 書籍

- **《The Linux Programming Interface》— Ch 不直接涵蓋 ptrace，但 process/signal 章是基礎**
  - **替代**：man ptrace + 上面的文章是 ptrace 的主要學習資源

下一章是 Part 1 的高潮——親手把這章的 ptrace 知識寫成一個完整的 mini-strace，能顯示 syscall 名字和參數。寫過它，你對 strace 的理解就脫胎換骨。

→ [Ch 4 動手寫 mini-strace v1](./04-mini-strace-v1.md)
