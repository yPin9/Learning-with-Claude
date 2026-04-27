# Ch 21 — core dump 與 signal trap

> 目標：搞懂 core dump 怎麼產生、systemd-coredump / coredumpctl / 老 path 配置、用 gdb 開 core 還原 backtrace + register + 變數，以及怎麼自己寫 signal handler dump 上下文。

## core dump 是什麼

程式被 fatal signal 殺時（SIGSEGV / SIGABRT / SIGFPE / SIGBUS / SIGILL），kernel 把 process 的 **memory + register + thread state** dump 成檔案。事後可以 gdb 開「在 crash 那瞬間程式長什麼樣」。

**比加 log 強的地方**：crash 前不需要做任何事，事後拿到完整現場。

## 開啟 core dump

預設多數 distro 限制 core 大小為 0（不產生）。要開：

```bash
ulimit -c            # 看當前限制
ulimit -c unlimited  # 開無限大
ulimit -c 1000000    # 1GB

# 永久（per user）
echo "* soft core unlimited" | sudo tee -a /etc/security/limits.conf
echo "* hard core unlimited" | sudo tee -a /etc/security/limits.conf
```

## core 寫到哪

兩條路：**老 path（kernel 直接寫）** 跟 **systemd-coredump（pipe 給 daemon）**。

### 老 path

```bash
cat /proc/sys/kernel/core_pattern
# core
```

`core` 表示寫到 cwd 的 `core` 檔案。可以 customize：

```bash
sudo sysctl -w kernel.core_pattern='/tmp/core.%e.%p.%t'
```

格式 specifier：

| % | 意義 |
|---|---|
| `%e` | executable name |
| `%E` | executable path（/ 變 !） |
| `%p` | PID |
| `%u` | UID |
| `%g` | GID |
| `%s` | signal |
| `%t` | time |
| `%h` | hostname |
| `%c` | coredump RLIMIT |

### systemd-coredump

modern distro 預設用：

```bash
cat /proc/sys/kernel/core_pattern
# |/lib/systemd/systemd-coredump %P %u %g %s %t %c %h
```

`|prog` 是 「kernel pipe core 給這 program」。systemd-coredump 接住、壓縮、寫到 `/var/lib/systemd/coredump/`。

優點：
- 自動分類（含 metadata）
- 自動壓縮
- `coredumpctl` 一個工具管理

## coredumpctl 用法

```bash
coredumpctl                         # 列所有
coredumpctl -1                      # 最近一個
coredumpctl info PID                # 某 PID 的 detail
coredumpctl info /path/to/binary
coredumpctl gdb                     # 最近一個 + gdb
coredumpctl debug                   # 同上
coredumpctl dump PID > core         # extract
coredumpctl info -1                 # 最後一個
```

```
$ coredumpctl
TIME                            PID UID GID SIG     COREFILE EXE
Sun 2025-01-01 12:34:56 UTC    1234 1000 1000 SIGSEGV present  /home/me/myprog
Sun 2025-01-01 12:35:01 UTC    5678 1000 1000 SIGSEGV present  /usr/bin/firefox
```

```
$ coredumpctl info -1
           PID: 1234 (myprog)
           UID: 1000
           GID: 1000
        Signal: 11 (SEGV)
     Timestamp: Sun ... (5min ago)
  Command Line: ./myprog arg1
    Executable: /home/me/myprog
 ...
       Storage: /var/lib/systemd/coredump/core.myprog.1000.xxx.zst (present)
       Message: Process 1234 (myprog) of user 1000 dumped core.
                
                Stack trace of thread 1234:
                #0  0x000... in main () at myprog.c:42
                #1  0x000... in __libc_start_main () from /lib/...
```

直接看到 backtrace，**不用開 gdb**！

## gdb 開 core

```bash
gdb /path/to/binary /path/to/core
# 或 coredumpctl 自動：
coredumpctl gdb -1
```

進去後：

```gdb
(gdb) bt                  # backtrace
(gdb) bt full             # 含 local variable
(gdb) frame 2             # 切到第 2 個 frame
(gdb) info locals         # 看 local
(gdb) info args           # 看 function args
(gdb) info registers      # 全部 register
(gdb) p var_name          # 印變數
(gdb) p *struct_ptr       # deref struct
(gdb) x/16x address       # 印 memory hex
(gdb) thread apply all bt # 所有 thread 的 backtrace
```

`bt full` 是查 crash 第一招：

```
#0  0x000055555555515a in process (x=0x7fff...) at myprog.c:8
        ptr = 0x0
        result = 0
#1  0x000055555555518c in main (argc=1, argv=...) at myprog.c:15
        x = {value = 42, ptr = 0x0}
```

`ptr = 0x0` —— 一目了然，null deref。

## 一個常見踩雷：core 不見

設了 `ulimit -c unlimited` 還是沒 core？檢查：

1. **cwd 寫得進嗎**：有些 daemon cwd 是 `/`，沒寫權限 → core 寫不出
2. **systemd 路徑**：core 在 `/var/lib/systemd/coredump/`，不在 cwd
3. **container**：limit 在 host 設的不算，要 docker run `--ulimit core=-1`
4. **suid binary**：kernel 預設不 dump suid binary core（怕泄露）。`/proc/sys/fs/suid_dumpable=2` 可開
5. **process 自己 RLIMIT_CORE 設 0**：用 `prctl` / `setrlimit`

debug：

```bash
ulimit -c
cat /proc/sys/kernel/core_pattern
ls -la /proc/PID/limits | grep -i core
```

## signal handler 自己寫 dump

不依賴 kernel core，自己寫 signal handler 印 stack：

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <signal.h>
#include <execinfo.h>
#include <stdlib.h>
#include <unistd.h>

void handler(int sig) {
    void *buf[64];
    int n = backtrace(buf, 64);

    fprintf(stderr, "=== signal %d ===\n", sig);
    backtrace_symbols_fd(buf, n, STDERR_FILENO);

    // 重啟 default handler 讓 core dump 還是會產
    signal(sig, SIG_DFL);
    raise(sig);
}

int main(void) {
    struct sigaction sa = {0};
    sa.sa_handler = handler;
    sa.sa_flags = SA_RESTART;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);

    int *p = NULL;
    *p = 42;        // 觸發 SIGSEGV
    return 0;
}
```

```bash
gcc -g -rdynamic sig.c -o sig
./sig
# === signal 11 ===
# ./sig(handler+0x18) [0x401234]
# /lib/x86_64-linux-gnu/libc.so.6(...) [0x...]
# ./sig(main+0x12) [0x401189]
# Segmentation fault (core dumped)
```

`-rdynamic` 讓 backtrace_symbols 看得到 function name。

## signal handler 的安全規則

signal handler 是 **async signal context**，能 call 的 function 受限（`man signal-safety`）。不能：

- printf / fprintf（**會 deadlock if 在 stdio buffer 內被打斷**）
- malloc / free
- 多數 lib function

可以：

- write
- _exit
- raise / kill
- 簡單算術

實務上 handler 應該：

1. 收到 signal
2. 用 `write(2, ...)` 印基本 info
3. backtrace + backtrace_symbols_fd（這個是 async-signal-safe）
4. 重設 handler、raise

## 一個常見場景：Java / Python 跟 native crash

Java 程式 SEGV 時 JVM 自己處理 signal（hs_err_pidXXX.log）。Python C extension 也類似。

對 Java：`hs_err_pid*.log` 含 native stack。
對 Python：用 `faulthandler` module，`python3 -X faulthandler myprog.py` 自動 dump traceback on signal。

## 一個常見場景：「我的 server 偶爾 segfault 但找不出原因」

1. 開 ulimit + systemd-coredump
2. 等 crash
3. `coredumpctl gdb -1`
4. `bt full`
5. 對應 source 看哪個 pointer 是 NULL / wild

加上 ASan build 一份 staging 環境跑，更早抓。

## 一個常見場景：「core 太大」

production binary 幾 GB heap 的，core 也幾 GB。

對策：

```bash
echo 0x33 > /proc/PID/coredump_filter
```

`coredump_filter` bitmask 控制哪些 mapping dump：

| bit | 內容 |
|---|---|
| 0 | anonymous private |
| 1 | anonymous shared |
| 2 | file-backed private |
| 3 | file-backed shared |
| 4 | ELF header pages |
| 5 | huge page private |
| 6 | huge page shared |

預設只 dump anonymous（heap / stack），不 dump 大型 mmap'd file。

或限制大小：

```bash
ulimit -c 100000      # 100MB
```

但被截斷的 core 可能不能 gdb 開。

## 動手練習

**1. 故意 crash + 開 core**

```c
int main() {
    int *p = NULL;
    *p = 42;
}
```

```bash
ulimit -c unlimited
gcc -g crash.c -o crash
./crash
# Segmentation fault (core dumped)
coredumpctl gdb -1
(gdb) bt
```

**2. 看 core 大小**

```bash
ls -lh /var/lib/systemd/coredump/
```

對應 `coredumpctl info` 看 process 多大。

**3. 自寫 signal handler**

照上面 sig.c 跑。改成在 handler 裡只用 write，看是否還能 dump backtrace。

**4. dump 一個 running process 的 core**

```bash
gcore PID
ls -lh core.PID
gdb /path/to/binary core.PID
(gdb) bt
```

**不用 crash 也能 dump**。對「卡住」的 process 看現場有用。

**5. 用 gdb attach + 自己 trigger core**

```bash
gdb -p PID
(gdb) generate-core-file /tmp/manual.core
(gdb) detach
```

## 自我檢核

- [ ] 設 `ulimit -c unlimited` 開 core
- [ ] 知道 systemd-coredump 跟老 path 兩種 mode
- [ ] 用 coredumpctl gdb 跑過、`bt full` 看現場
- [ ] 寫過 signal handler dump backtrace
- [ ] 知道 signal handler 內 async-signal-safe 限制
- [ ] 用過 gcore dump running process

Part 7 完。下一個是整合 final project：偵探破案。

→ [Final Project：偵探破案](./final-project-broken-daemon.md)
