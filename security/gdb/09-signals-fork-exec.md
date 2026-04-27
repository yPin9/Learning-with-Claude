# Ch 9 — Signal、fork、exec

> 目標：搞懂 GDB 怎麼處理 signal（`handle`）、怎麼跟著 `fork` 進子 process、怎麼處理 `execve` 後的符號重建。

## Signal 的雙角色

一個 signal 到達 inferior 時，GDB 的角色有兩個選擇：

1. **攔下不傳給 inferior**：inferior 以為從未發生。
2. **傳給 inferior**：讓 inferior 自己的 signal handler 處理（或預設行為：crash）。

同時，GDB 要決定：

- **停下來通知你嗎？** 或靜悄悄處理？
- **印訊息嗎？**

所以每個 signal 有四個獨立的開關。

## `handle SIGNAL OPTIONS`

```
(gdb) handle SIGUSR1 nostop noprint pass
```

四個 option：

| Option | 意義 |
|---|---|
| `stop` / `nostop` | 收到時要不要停下來 |
| `print` / `noprint` | 要不要印訊息 |
| `pass` / `nopass`（等同 `noignore` / `ignore`） | 要不要傳給 inferior |

最常用的組合：

**忽略一個很吵的 signal**：
```
(gdb) handle SIGUSR1 nostop noprint pass
```
GDB 完全不干預，inferior 自己處理。

**每次都停下來看**：
```
(gdb) handle SIGPIPE stop print pass
```

**完全吞掉，不讓 inferior 看到**：
```
(gdb) handle SIGALRM nostop noprint nopass
```

### 看目前設定

```
(gdb) info signals
Signal        Stop      Print   Pass to program Description
SIGHUP        Yes       Yes     Yes             Hangup
SIGINT        Yes       Yes     No              Interrupt
SIGQUIT       Yes       Yes     Yes             Quit
SIGILL        Yes       Yes     Yes             Illegal instruction
...
SIGSEGV       Yes       Yes     Yes             Segmentation fault
SIGTERM       Yes       Yes     Yes             Terminated
...
```

`info handle` 同義。

## 預設行為的取捨

GDB 的預設：

- **Fatal signals（SIGSEGV、SIGBUS、SIGFPE、SIGILL）**：Stop、Print、**No pass** — 停給你看，但不傳給 inferior（否則它 crash，你就看不到現場了）。
- **SIGINT**：Stop（讓你能用 Ctrl-C 中斷 inferior），No pass。
- **SIGPIPE**：Stop, Print, Pass — 你 debug network 時這很煩。

所以 debug 網路程式常見第一步：

```
(gdb) handle SIGPIPE nostop noprint pass
```

## 手動送 signal 給 inferior

```
(gdb) signal SIGUSR1            # 送 SIGUSR1 然後 continue
(gdb) signal 0                  # 「繼續但不送任何 signal」
```

`signal 0` 在一種情境下有用：GDB 因為 signal 停下了，你 debug 完要繼續，但**不想讓 signal 傳到 inferior**。`signal 0` 等於 `continue` 但把 pending signal 丟掉。

## Signal 的觀察實作

寫個會發送 signal 的程式 `signaller.c`：

```c
#include <stdio.h>
#include <signal.h>
#include <unistd.h>

void handler(int sig) {
    printf("received signal %d\n", sig);
}

int main(void) {
    signal(SIGUSR1, handler);
    printf("pid = %d, raise SIGUSR1\n", getpid());
    raise(SIGUSR1);
    printf("done\n");
    return 0;
}
```

```
gcc -g signaller.c -o signaller
gdb -q ./signaller
(gdb) start
(gdb) r

pid = ..., raise SIGUSR1

Program received signal SIGUSR1, User defined signal 1.
0x00007ffff7e7d79b in __GI_raise () from /lib/x86_64-linux-gnu/libc.so.6
(gdb) bt
#0  0x00007ffff7e7d79b in __GI_raise () ...
#1  0x00005555555551d5 in main () at signaller.c:11

(gdb) c           ; 讓 signal 傳過去

received signal 10
done
[Inferior 1 (process ...) exited normally]
```

GDB 在 inferior `raise(SIGUSR1)` 時停下了（預設行為）。你 `continue` 讓 signal 傳到 handler。

設為忽略後重跑：

```
(gdb) handle SIGUSR1 nostop noprint pass
(gdb) r
pid = ..., raise SIGUSR1
received signal 10
done
```

這次 GDB 完全沒介入。

## fork — 子 process 怎麼處理

預設：GDB 繼續跟著 parent，**子 process 獨立跑**（沒有被 debug）。

用這個程式試 `fork_demo.c`：

```c
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>

int main(void) {
    printf("parent start, pid=%d\n", getpid());
    pid_t pid = fork();
    if (pid == 0) {
        printf("child, pid=%d\n", getpid());
        for (int i = 0; i < 3; i++) printf("child i=%d\n", i);
    } else {
        printf("parent: forked child pid=%d\n", pid);
        wait(NULL);
    }
    return 0;
}
```

```
gcc -g fork_demo.c -o fork_demo
gdb -q ./fork_demo
(gdb) start
(gdb) n ...                  ← next 幾次直到 fork 之後
```

### `set follow-fork-mode`

```
(gdb) set follow-fork-mode child       # fork 後跟子 process
(gdb) set follow-fork-mode parent      # 跟 parent（預設）
```

### `set detach-on-fork`

```
(gdb) set detach-on-fork on           # 沒跟的那個被 detach 自己跑（預設）
(gdb) set detach-on-fork off          # 兩個都保留，可切換
```

兩個搭起來：

- **預設**：follow=parent, detach=on → 跟 parent，child 自己跑。
- **想 debug child**：follow=child, detach=on → 跟 child，parent 被 detach 自己跑。
- **雙管齊下**：follow=parent, detach=off → 主要跟 parent，但 child 也保留，可 `info inferiors` 列出，用 `inferior 2` 切過去看。

實例：

```
(gdb) set detach-on-fork off
(gdb) set follow-fork-mode parent
(gdb) r
...
[New inferior 2 (process 12345)]      ← fork 後
(gdb) info inferiors
  Num  Description       Connection    Executable
* 1    process 12340     1 (native)    /tmp/fork_demo
  2    process 12345     1 (native)    /tmp/fork_demo

(gdb) inferior 2                       ← 切到 child
(gdb) bt                                ← 看 child 現在在哪
```

**這是 GDB 的多 inferior 功能**，一個 GDB session 同時管多個 process。

### `catch fork`

比 `follow-fork-mode` 更精細：在 `fork()` 發生前停下來，讓你決定要跟哪個。

```
(gdb) catch fork
(gdb) r
...
Catchpoint 1 (forked process 12345), 0x00007ffff7e4... in __GI__Fork ()
(gdb) set follow-fork-mode child
(gdb) c
```

## exec — execve 怎麼處理

`execve` 會把整個 process image 替換 — 新的 binary、新的 symbol table。GDB 預設會做合理的事：

1. 保留 inferior id，但**重載 symbols**（讀新 binary）。
2. 舊斷點會**在新 binary 裡重新解析**（根據函式名）。找不到的斷點變成 pending。

### `set follow-exec-mode`

```
(gdb) set follow-exec-mode new        # execve 後當成新 inferior
(gdb) set follow-exec-mode same       # 保持同一個 inferior（預設）
```

預設 `same` 對多數情況夠用。

### `catch exec`

```
(gdb) catch exec
(gdb) r
...
Catchpoint 1 (exec'd /tmp/new_program), 0x... in ...
(gdb) info proc exe
exe = '/tmp/new_program'
```

實用情境：debug 一個 shell script 或 test runner，你想在它 exec 到真正的程式時才介入。

## 多 inferior 管理

進階用法：一個 GDB session 管多個完全不相關的程式。

```
(gdb) add-inferior                     ; 建一個空 inferior
[New inferior 2]
(gdb) inferior 2                       ; 切過去
(gdb) file /path/to/another_program
(gdb) run
```

或用 `attach` 把一個跑中的 process 接進來：

```
(gdb) attach 12345
```

多 inferior + fork tracking 是 Ch 11（多執行緒）跟 Ch 12（remote）的前置概念。

## 另一個相關指令：`kill`

```
(gdb) kill                   ; 殺掉 inferior
(gdb) kill inferior 2        ; 殺特定
```

debug 一半不想跑完時，比退出 gdb 快。

## 常見坑

1. **`fork` 後 GDB 沒跟上**：忘記設 `follow-fork-mode`。或用 `catch fork` 先停下來看。
2. **SIGPIPE 導致 GDB 停下**：網路程式常遇到。`handle SIGPIPE nostop noprint pass` 解決。
3. **`signal 0` 意外放行**：如果當前是因為 SIGSEGV 停下，用 `c` 繼續 GDB 會問「要不要 pass signal？」預設 yes，inferior 就 crash。想不傳就 `signal 0`。
4. **exec 後斷點都不見了**：新 binary 的 symbol 可能 mangled 或位置不同。檢查 `info break`，pending 斷點會自動在新 symbol 出現時解析。
5. **`catch fork` 之後 continue 回報 SIGSTOP**：這是 GDB 內部機制，正常。繼續 `c` 就好。
6. **debug 的是 daemon，fork 後自己 detach 成背景 process**：GDB 看不到它了。需要改 daemon，或用 `set follow-fork-mode child` + `set detach-on-fork off` 搶在 detach 前抓住。

## 動手練習

1. 用 `signaller.c`，試 `handle SIGUSR1 stop` 與 `nostop` 各跑一次，觀察 GDB 介入與否。
2. 在 `raise(SIGUSR1)` 的 bt 停下時，試 `signal 0` 繼續 — 看 handler 有沒有被呼叫（答：沒有）。
3. 用 `fork_demo.c`，測試 `follow-fork-mode` 三種設定，確認你跟到正確的 process。
4. `set detach-on-fork off` + `info inferiors`，在 parent 跟 child 之間切。
5. 寫一個 exec 範例：一個程式 `execve("/bin/ls", ...)`。用 `catch exec` 停下，確認 symbol 換了。

## 範例：exec_demo.c

```c
#include <unistd.h>
#include <stdio.h>

int main(void) {
    printf("before exec, pid=%d\n", getpid());
    execl("/bin/ls", "ls", "-l", NULL);
    printf("this never prints\n");
    return 0;
}
```

```
(gdb) catch exec
(gdb) r
before exec, pid=...
Catchpoint 1 (exec'd /bin/ls), 0x... in _start ()
(gdb) info proc exe
exe = '/bin/ls'
(gdb) c
... ls 的輸出 ...
```

## 自我檢核

- [ ] 我能調整 `handle` 的四個 option（stop / print / pass）
- [ ] 我知道 `signal 0` 跟 `continue` 的差別
- [ ] 我能設 `follow-fork-mode` 跟 `detach-on-fork` 控制子 process 行為
- [ ] 我能用 `info inferiors` 看多個 inferior 並切換
- [ ] 我知道 execve 後斷點會被重新解析

下一章是 GDB 的黑魔法之一 — reverse debugging。讓程式**往回走**。

→ [Ch 10 Reverse debugging](./10-reverse-debugging.md)
