# Ch 15 — Signal 與非同步控制

> **目標**：徹底搞懂 GDB 怎麼介入 signal——`handle` 設定每個 signal 的 stop/pass/print、Ctrl-C 怎麼運作、怎麼把 signal 轉交給程式的 handler、以及 async mode 與 non-stop mode 是什麼、何時用。signal 是 debug 訊號處理程式、模擬中斷、控制 inferior 的關鍵。

> **環境**：GDB 13/14，Linux x86_64。signal 行為與 OS 緊密相關，本章以 Linux 為準。

## 為什麼 signal 對 debug 這麼重要

承 Ch 2 的關鍵事實：**tracee 的所有 signal 都先經過 tracer（GDB）。** 這給了 GDB 巨大的控制權，但也帶來困惑：

- 為什麼 GDB 一遇 SIGSEGV 就停，但 SIGALRM 不停？
- 為什麼我在 GDB 裡按 Ctrl-C 是停住程式，不是殺掉它？
- 我的程式有 SIGUSR1 handler，但在 GDB 裡 signal 好像沒進到 handler？
- debug 一個用 signal 做計時/IPC 的程式，怎麼控制 signal 流？

這些全是 signal 與 ptrace 互動的結果。搞懂這章，你能精確控制「哪些 signal 停、哪些放行、哪些轉交程式」。

## 先建立直覺：GDB 是 signal 的守門員

```
   程式即將收到 signal（SIGSEGV / SIGUSR1 / ...）
        │
        ▼
   OS 攔下，凍結 tracee，通知 GDB（守門員）
        │
        ▼
   GDB 查 `handle` 設定，對每個 signal 決定三件事：
        ├─ stop?   要不要停下來給你看
        ├─ print?  要不要印一行通知
        └─ pass?   要不要把 signal 真的轉交給程式（讓它的 handler 跑）
```

每個 signal 有這三個獨立開關。理解這三個開關，signal debug 就通了。

## `info handle` / `handle`：signal 的三開關

```
(gdb) info handle                    # 看所有 signal 的設定（簡寫 i handle）
Signal  Stop  Print  Pass to program  Description
SIGSEGV  Yes   Yes    Yes              Segmentation fault
SIGALRM  No    No     Yes              Alarm clock
SIGINT   Yes   Yes    No               Interrupt
SIGTRAP  Yes   Yes    No               Trace/breakpoint trap
SIGUSR1  Yes   Yes    Yes              User defined signal 1
...
```

讀這張表：

- **SIGSEGV**：Stop=Yes（停）、Print=Yes（印）、Pass=Yes（也轉給程式）。所以崩潰時 GDB 停下來，你 `continue` 的話 signal 才真的送給程式（觸發它的 handler 或預設終止）。
- **SIGALRM**：Stop=No、Print=No、Pass=Yes。GDB 不打擾你，直接放行給程式——所以用 alarm 的程式在 GDB 裡正常運作。
- **SIGTRAP**：Pass=No——這是 breakpoint 用的，GDB 永遠吞掉不轉給程式（所以你的程式不知道有斷點）。

改設定：

```
(gdb) handle SIGUSR1 nostop noprint   # SIGUSR1 不停、不印（但仍 pass 給程式）
(gdb) handle SIGSEGV nopass           # SIGSEGV 不轉給程式（debug 時不想讓它真的崩）
(gdb) handle SIGALRM stop print       # 讓 SIGALRM 改成會停會印
(gdb) handle SIG34 nostop noprint pass # 即時 signal
```

關鍵字：`stop`/`nostop`、`print`/`noprint`、`pass`(=`noignore`)/`nopass`(=`ignore`)。

## 經典應用一：debug 自己的 signal handler

```c
// signal_demo.c — gcc -g -O0
#include <stdio.h>
#include <signal.h>
#include <unistd.h>
void on_usr1(int sig) { printf("got USR1\n"); }   // 你的 handler
int main(void) {
    signal(SIGUSR1, on_usr1);
    while (1) pause();                              // 等 signal
    return 0;
}
```

跑起來，另一個 terminal `kill -USR1 <pid>`。預設 GDB 會在收到 SIGUSR1 時停下來（Stop=Yes），打斷你。如果你想讓 signal 順利進到 `on_usr1`：

```
(gdb) handle SIGUSR1 nostop noprint    # 不要打擾，直接放行
(gdb) break on_usr1                     # 但我想在 handler 裡停
(gdb) continue
... kill -USR1 ... 
Breakpoint 1, on_usr1 (sig=10) ...      # signal 順利轉交，停在 handler 裡
```

`nostop noprint`（不被 signal 本身打斷）+ `break handler`（在 handler 裡才停）= debug signal handler 的標準配方。前提是 Pass=Yes（signal 真的轉交），否則 handler 根本不會跑。

## 經典應用二：手動送 signal 給程式

```
(gdb) signal SIGUSR1                  # 從 GDB 直接送一個 signal 給 inferior
(gdb) signal 0                        # continue 但「不」轉交當前 pending 的 signal（吞掉它）
```

`signal SIGUSR1` 讓你不靠外部 `kill` 就能觸發程式的 signal 處理——測試 handler 很方便。

`signal 0` 特別有用：當 GDB 停在某個 signal（例如 SIGSEGV），你想「繼續但不要把這個 SIGSEGV 送給程式」（避免它真的崩），用 `signal 0` 繼續並吞掉該 signal。對比 `continue` 會把 pending signal 一起轉交。

## Ctrl-C：在 GDB 裡的真相

程式在 GDB 裡跑（`continue` 後），你按 Ctrl-C：

- **不是** 送 SIGINT 殺掉程式
- 而是 GDB 攔截，把 inferior **停下來**讓你重新取得控制權

這是因為 GDB 把 SIGINT 設成 Stop=Yes、Pass=No（看 `info handle SIGINT`）——Ctrl-C 變成「暫停 debug」鍵。如果你真的想送 SIGINT 給程式（測試它的 SIGINT handler），用 `signal SIGINT` 或 `handle SIGINT pass`。

## async mode 與 non-stop mode

預設下，inferior 在跑（`continue`）時，GDB prompt 是「卡住」的——你不能下指令，直到程式停下來。**async mode** 改變這點：

```
(gdb) set non-stop on                # 必須在 run 之前設
```

兩個容易混的模式：

### all-stop（預設）

任何一個 thread 停（碰斷點），**所有 thread 都凍結**。你看到的是一個一致的快照。簡單、直觀，但無法「只停一個 thread 讓其他繼續跑」。

### non-stop mode

只有觸發事件的 thread 停，**其他 thread 繼續跑**。可以：

- 在多執行緒服務裡只停一個 worker 檢查，不打斷整個服務
- 對還在跑的 thread 下指令（在 async 下 prompt 不卡）

```
(gdb) set pagination off
(gdb) set non-stop on
(gdb) run
... thread 3 碰斷點停了，thread 1/2 還在跑 ...
(gdb) thread 3
(gdb) print x                        # 檢查 thread 3，同時 1/2 仍在服務
(gdb) interrupt                      # 手動停某個（或全部）thread
(gdb) continue &                     # 背景 continue（async），prompt 不卡
```

non-stop 是 debug 線上多執行緒服務的利器（Ch 16 會配合），但較複雜、易混亂——初學先用預設 all-stop，需要「只停一個」時再開。

> 認識論誠實：non-stop mode 的支援程度依 target 而異。本機 native Linux 支援良好；某些遠端 stub、舊核心可能不完整。且它改變了你對「停下來時看到的狀態」的假設（其他 thread 還在動，狀態是流動的），心智負擔較高。

## 踩雷集錦

1. **以為 GDB 裡 Ctrl-C 會殺掉程式**：不會，是停住它。要送 SIGINT 給程式用 `signal SIGINT`。
2. **handler 永遠進不去**：你 `handle SIGUSR1 nopass` 把 signal 吞了，handler 當然不跑。要 debug handler 得 Pass=Yes。
3. **`continue` 意外把 SIGSEGV 送給程式導致它崩**：停在 SIGSEGV 後直接 `continue` 會轉交 signal。想繼續但不崩，用 `signal 0` 或先 `handle SIGSEGV nopass`。
4. **SIGALRM/SIGCHLD 打斷 debug 很煩**：很多程式狂發這些。`handle SIGALRM SIGCHLD nostop noprint` 讓它們安靜放行。
5. **non-stop 在 run 之後才設無效**：`set non-stop on` 要在 `run` 之前。
6. **non-stop 下以為看到一致狀態**：其他 thread 還在跑，你 print 的全域變數可能下一刻就被改。理解這個流動性。
7. **把 `catch signal`（Ch 14）和 `handle` 搞混**：`handle` 是 signal 的主要設定機制；`catch signal` 是把 signal 包成 catchpoint（可加 commands/計數）。一般用 `handle`。

## 進階：再往深一層

- **底層**：GDB 用 `PTRACE_CONT` 的第 4 個參數把 signal「注入」回 tracee（pass），或不帶（吞掉）。`info handle` 的 Pass 欄就是控制這個。Ch 2 講過的「signal 先到 tracer」在這裡開花。
- **`$_siginfo`**：收到 signal 時，`print $_siginfo` 看詳細資訊——SIGSEGV 時的出錯位址（`$_siginfo._sifields._sigfault.si_addr`）、signal 來源等。debug「為什麼 SIGSEGV」時很有用。
- **即時 signal（SIGRTMIN+n）**：`handle SIG34` 等，pthread 內部與某些 framework 用。預設常設 nostop noprint pass。
- **`set non-stop` + `target remote`**：遠端 debug（Ch 36）時 non-stop 對「只觀察一個 thread」特別有價值。
- **scheduler-locking**（Ch 16）：和 async/non-stop 配合控制 stepping 時其他 thread 動不動。
- **signal 與 reverse debugging**：record（Ch 34）下，signal 也被記錄，可以 reverse 回到 signal 之前。

## 動手練習

1. `info handle` 看完整 signal 表，找出 SIGSEGV、SIGALRM、SIGTRAP、SIGINT 的三開關設定，解釋為什麼各是那樣。
2. 對 `signal_demo.c`，預設跑，外部 `kill -USR1`，看 GDB 停在 SIGUSR1；再 `handle SIGUSR1 nostop noprint` + `break on_usr1`，看 signal 順利進 handler。
3. 用 `signal SIGUSR1` 從 GDB 內部觸發 handler，不靠外部 kill。
4. 寫一個解 NULL 的程式，停在 SIGSEGV 後 `print $_siginfo`，找出出錯位址。再用 `signal 0` continue（吞掉 signal）對比直接 `continue`（轉交導致崩）。
5. （多執行緒，配合 Ch 16）寫一個多 thread 程式，`set non-stop on` 後只停一個 thread，確認其他還在跑。

## 本章重點整理

- tracee 的 signal 先經 GDB；每個 signal 有三開關：stop（停）/print（印）/pass（轉交程式）。
- `info handle` 看設定，`handle SIG nostop noprint nopass ...` 改設定。
- debug signal handler 的配方：`handle SIG nostop noprint`（不被打斷）+ `break handler`（在 handler 內停）+ Pass=Yes。
- `signal SIG` 手動送 signal；`signal 0` continue 並吞掉 pending signal（避免崩）。
- GDB 裡 Ctrl-C 是「停住」不是「殺掉」。
- non-stop mode：只停觸發的 thread，其他續跑——debug 線上多執行緒服務，但較複雜。

## 自我檢核

- [ ] signal 的三個開關各控制什麼？SIGSEGV 和 SIGALRM 的設定為什麼不同？
- [ ] 想 debug 自己的 SIGUSR1 handler，要怎麼設定才不被打斷又能停在 handler 裡？
- [ ] 停在 SIGSEGV 後，怎麼「繼續但不要讓程式真的崩」？
- [ ] GDB 裡按 Ctrl-C 發生什麼？想真的送 SIGINT 給程式怎麼做？
- [ ] all-stop 和 non-stop mode 差在哪？non-stop 適合什麼場景、有什麼心智負擔？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Signals](https://sourceware.org/gdb/current/onlinedocs/gdb/Signals.html)**
  - **讀哪裡**：`handle`、`info handle`、`signal` 命令、三開關語意。
  - **和本章的關聯**：本章核心的權威來源。

- **[GDB Manual: Non-Stop Mode](https://sourceware.org/gdb/current/onlinedocs/gdb/Non_002dStop-Mode.html)** 與 **[Background Execution](https://sourceware.org/gdb/current/onlinedocs/gdb/Background-Execution.html)**
  - **讀哪裡**：non-stop 的設定與限制、`continue &` 等背景執行。
  - **和本章的關聯**：async/non-stop 的完整說明。

### 參考

- **[man 7 signal](https://man7.org/linux/man-pages/man7/signal.2.html)**
  - **讀哪裡**：signal 列表、預設行為、可否被捕捉。
  - **和本章的關聯**：理解每個 signal 的本質，才懂 GDB 的預設為什麼那樣設。

### 部落格

- **[Debugging signal handlers with GDB](https://www.gnu.org/software/libc/manual/html_node/Signal-Handling.html)** 配 glibc 文件
  - **為什麼值得讀**：signal handler 的限制（async-signal-safe）與 debug 時的注意事項。

下一章進入並行 debug 的第一塊：多執行緒——thread 切換、scheduler-locking、thread-specific 斷點，以及為什麼多執行緒 bug 那麼難抓。

→ [Ch 16 多執行緒除錯](./16-multithreaded-debugging.md)
