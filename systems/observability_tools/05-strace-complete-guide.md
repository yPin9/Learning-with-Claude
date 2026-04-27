# Ch 5 — strace 完整指南

> 目標：把 strace 用到專業級。filter、follow fork、attach、stack trace、解 fd、summary —— 一次學完。

## strace 是什麼（再次澄清）

跟你 Ch 4 寫的 mystrace 同個機制（ptrace），但功能完整：

- 認識所有 syscall 跟所有 flag bitmask
- 多 architecture（x86 / ARM / ...）
- follow fork / clone
- 各種 filter 跟 output 格式
- 內建 timing、stack trace、解 fd 路徑
- 新版有 seccomp 加速（少 5-10x slowdown）

## 五大用法

### 1. 跑命令

```bash
strace ./prog arg1 arg2
strace -o trace.log ./prog       # 寫檔
strace -o trace.log -f ./prog    # follow fork
```

### 2. attach 已存在的 process

```bash
strace -p 1234                   # attach 一個
strace -p 1234 -p 5678           # 多個
strace -f -p 1234                # 連 thread 都 trace
```

按 Ctrl-C detach（tracee 不會死）。

### 3. filter syscall

```bash
strace -e trace=openat,read,write ./prog
strace -e trace=file ./prog              # 所有檔案相關
strace -e trace=network ./prog           # 所有網路
strace -e trace=process ./prog           # fork/exec/wait
strace -e trace=signal ./prog            # signal
strace -e trace='!futex,brk' ./prog      # 除了這些
```

`-e trace=<group>` 的 group 名稱在 `man strace` 有：file / process / network / signal / ipc / desc / memory / stat。

### 4. summary 模式

```bash
strace -c ./prog
```

```
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 35.44    0.000125          12        10           openat
 18.02    0.000064           7         9           read
 10.13    0.000036           5         7           close
 ...
------ ----------- ----------- --------- --------- ----------------
100.00    0.000353                    79         3 total
```

**用來找熱點 syscall**。發現你的 server 90% 時間在 futex（lock 競爭）、或 80% 時間在 read（IO bound），都是用這個看出來。

### 5. 配合 attach 跟 timer

```bash
timeout 5 strace -p 1234 -c -f
```

attach 5 秒、收集 summary、自動 detach。**production 限時觀察的標準動作**。

## 關鍵 flag 一覽

| Flag | 作用 |
|---|---|
| `-f` | follow fork / clone（trace child + thread） |
| `-ff` | 跟 -o 配，每個 PID 寫一個檔 |
| `-p PID` | attach |
| `-o FILE` | 寫檔（不混 stderr） |
| `-e trace=...` | filter syscall |
| `-e read=N` | 把 fd N 的 read 內容也印出來 |
| `-e write=N` | 同上 write |
| `-e signal=...` | filter signal |
| `-c` | 只印 summary |
| `-C` | 同 -c 但繼續印每個 syscall |
| `-T` | 每個 syscall 加 elapsed time |
| `-tt` | timestamp（micro） |
| `-r` | relative timestamp |
| `-y` | fd 顯示對應路徑 |
| `-yy` | -y 加上 socket 顯示 ip:port |
| `-x` | 字串用 hex 印 |
| `-s N` | 字串最大長度（預設 32） |
| `-k` | 每個 syscall 加 user stack trace |
| `-K` | kernel stack trace（要 CONFIG_KALLSYMS） |
| `-i` | 印發 syscall 的 instruction pointer |
| `-v` | 印完整 struct（不省略） |
| `-A` | append 到 -o 檔案 |
| `-D` | 把 strace 自己 daemonize |

最常用的組合：

```bash
strace -f -e trace=openat,read,write -y -tt -o trace.log -p PID
```

## -y / -yy 解 fd

不用 `-y`：

```
read(7, "...", 4096) = 100
```

7 是什麼？要去 `ls /proc/PID/fd/7` 查。

加 `-y`：

```
read(7</etc/passwd>, "...", 4096) = 100
```

直接告訴你是 `/etc/passwd`。

加 `-yy`：

```
recvfrom(7<TCP:[10.0.0.5:12345->10.0.0.9:443]>, ...) = ...
```

連 socket 對端都印。**debug 卡死的 server 一定要加 -yy**。

## -k stack trace

```bash
strace -k -e openat ./prog
```

```
openat(AT_FDCWD, "/etc/passwd", O_RDONLY) = 3
 > /usr/lib64/libc.so.6(open64+0x4d) [0x...]
 > /usr/lib64/libc.so.6(__GI_open+0x...) [0x...]
 > ./prog(read_passwd+0x12) [0x401234]
 > ./prog(main+0x45) [0x401345]
 > /usr/lib64/libc.so.6(__libc_start_main+0xe7) [0x...]
```

每個 syscall 為什麼被呼叫一目了然。**比加 printf 強百倍**。

stack trace 需要 binary 有 debug info（`-g` build）才好看。release build 看到的是 `+0x...` offset，要配 `addr2line` 翻成 source 行（Ch 11 講）。

## follow fork 的兩種模式

```bash
strace -f -o trace.log ./multi-process-prog
```

所有 PID 混在一個檔，每行前面有 `[pid 1234]` 標識：

```
[pid 1234] openat(AT_FDCWD, "...", ...) = 3
[pid 1235] read(0, ...) = 0
[pid 1234] close(3) = 0
```

```bash
strace -ff -o trace ./multi-process-prog
# 產生 trace.1234, trace.1235, ... 一個 PID 一檔
```

**多 process 程式 debug 用 `-ff`**，每個 PID 獨立檔案，分析容易。

## 一個常見場景：「程式啟動失敗」

```bash
strace -f -e trace=openat,access,execve ./broken
```

執行檔啟動會 open 配置檔、so library。看 strace 的 `-1 ENOENT` 就知道找錯路徑：

```
openat(AT_FDCWD, "/etc/myapp.conf", O_RDONLY) = -1 ENOENT (No such file or directory)
```

90% 的「程式啟動就 fail」屬於這類。

## 一個常見場景：「程式跑著但卡住」

```bash
PID=$(pgrep myserver)
sudo strace -p $PID -f -y -tt
```

看到 tracee 停在哪個 syscall。常見：

- `read(7</tmp/socket>)` — 等 socket 對端
- `futex(...)` — 等 mutex
- `select(...)` — 等多個 fd
- `accept(3<TCP:...>)` — 等新連線

知道卡在哪個 fd，配 `lsof` / `ss` 看 fd 是誰。Ch 8 / 9 接著講。

## 一個常見場景：「找誰在 access 某個檔案」

```bash
sudo strace -e openat -f -p PID 2>&1 | grep "filename"
```

或用 inotify / fatrace（更專業，Ch 8 簡單帶過）。

## 一個常見踩雷：strace 改變 timing

race condition、性能問題用 strace 觀察會大幅改變行為：

- 慢 5-100x，原本 race 的兩個 thread 不再 race
- I/O pattern 被打亂
- timeout 都過了

**race 用 helgrind / TSan，性能用 perf**，strace 不適合這類。

## 一個常見踩雷：strace 看不到 vDSO

```bash
strace -e clock_gettime ./prog
# 啥都看不到（程式呼叫了 clock_gettime 但 strace 沒抓到）
```

clock_gettime 走 vDSO，沒進 kernel，ptrace 攔不到。要看就用 ltrace（lib 層）。

## 一個常見踩雷：strace 對 setuid binary 不能 attach

```bash
strace -p $(pgrep nginx)
# strace: attach: ptrace(PTRACE_SEIZE, ...): Operation not permitted
```

nginx worker 是其他 user。strace 自己也要 root 或 `CAP_SYS_PTRACE`。

```bash
sudo strace -p $(pgrep nginx)
```

通常 fix。

## 進階：seccomp 加速

新版 strace 加 `--seccomp-bpf`：

```bash
strace --seccomp-bpf -e openat,read ./prog
```

用 seccomp filter 讓 kernel **只在你關心的 syscall 才 stop tracee**，其他 syscall 自由跑過。可以從 100x slowdown 降到 ~5x。

production debugging 強烈建議用。

## 動手練習

**1. 用 `-c` 找熱點**

寫一個會做大量 small read 的程式（每次 1 byte），跑 `strace -c`。看到 read 占絕大比例。改成大 buffer (4096 byte) 重跑，read 比例驟降。

**2. 用 `-y` 看 fd 對應**

寫一個程式 open 5 個檔案、隨機 read。strace 加 `-y`，每個 read 都看得到讀的是哪個檔。

**3. 用 `-k` 看 stack**

寫程式：

```c
void deep_func(void) { open("/tmp/x", 0); }
void mid(void) { deep_func(); }
int main() { mid(); }
```

```bash
gcc -g -no-pie deep.c -o deep
strace -k ./deep 2>&1 | grep -A 5 "/tmp/x"
```

看 stack。

**4. 用 `-e signal`**

```c
#include <signal.h>
#include <unistd.h>
int main() {
    signal(SIGUSR1, SIG_IGN);
    pause();
}
```

```bash
./prog &
PID=$!
sudo strace -e signal -p $PID &
kill -USR1 $PID
```

看 SIGUSR1 怎麼被印出來。

**5. 用 `-f` 看 fork**

```c
#include <unistd.h>
#include <sys/wait.h>
int main() {
    if (fork() == 0) { execlp("ls", "ls", NULL); }
    wait(NULL);
}
```

```bash
strace ./prog              # 沒 -f，看不到 ls 的 syscall
strace -f ./prog            # 有 -f，連 ls 一起 trace
strace -ff -o t ./prog      # 兩個獨立檔
ls t.*
```

## 自我檢核

- [ ] 知道 `-f` / `-p` / `-c` / `-y` / `-yy` / `-k` / `-tt` / `-T` 各做什麼
- [ ] `-e trace=` 能配出一組需要的 syscall
- [ ] 跑過 `-c` 找出某 process 的 syscall 分布
- [ ] 知道 strace 改變 timing、不適合 race / perf
- [ ] 知道 seccomp-bpf 加速能少很多 overhead

下一章看 ltrace —— strace 的 lib call 對應。

→ [Ch 6 ltrace 與動態連結](./06-ltrace-and-dynamic-linking.md)
