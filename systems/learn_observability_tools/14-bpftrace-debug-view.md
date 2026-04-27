# Ch 14 — bpftrace 從 debug 角度

> 目標：用 bpftrace 寫小腳本回答 debug 問題。不深入 eBPF 內部（那是另一門課），只看「拿來查 bug」這條軸。

## bpftrace 是什麼

eBPF 上的 awk-like script 語言。一行能寫出原本要 100 行 C kernel module 才能做的觀察。

跟 ftrace 比：
- ftrace 是 kernel 內 function tracer + tracepoint
- bpftrace **編譯成 eBPF bytecode 在 kernel 跑**，能 aggregate / 算 stats / 印自定義 output

跟 perf 比：
- perf 是 sample + event counter
- bpftrace 是 event-driven、自定義邏輯

對 debug 的價值：**「這個 case 發生時印 X」一行就寫得出來**。

## 安裝 + 確認

```bash
sudo bpftrace --version
# bpftrace v0.18.0

sudo bpftrace -l | head
# software:alignment-faults:
# software:bpf-output:
# tracepoint:alarmtimer:alarmtimer_cancel
# ...
```

需要 root 或 `CAP_BPF` + 對應 kernel feature。

## 5 個一行神技

debug 90% 場景靠這 5 個 idiom：

### 1. 看誰開了某檔案

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat / str(args->filename) == "/etc/passwd" / { printf("%s opened by %s (pid %d)\n", str(args->filename), comm, pid); }'
```

只在 openat 第一個參數 == `/etc/passwd` 時印。

### 2. 看誰連 80 port

```bash
sudo bpftrace -e 'kprobe:tcp_v4_connect { printf("%s connecting (pid %d)\n", comm, pid); }'
```

任何 process call `tcp_v4_connect` 就印。

### 3. 計算 syscall 分布

```bash
sudo bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm, args->id] = count(); }'
```

按 (process name, syscall id) 累計。Ctrl-C 印 histogram。

### 4. 抓 ENOENT 的 open

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_exit_openat / args->ret < 0 / { printf("%s pid %d failed openat -> %d\n", comm, pid, args->ret); }'
```

只印失敗的。

### 5. read 的 latency 分布

```bash
sudo bpftrace -e '
kprobe:vfs_read { @start[tid] = nsecs; }
kretprobe:vfs_read /@start[tid]/ {
    @latency_ns = hist(nsecs - @start[tid]);
    delete(@start[tid]);
}'
```

每次 read 的耗時做成 histogram。

## bpftrace 語法核心

```
probe1 [, probe2 ...] / filter / { actions }
```

| 元件 | 例 |
|---|---|
| Probe type | `tracepoint:` `kprobe:` `kretprobe:` `uprobe:` `uretprobe:` `usdt:` `profile:` `interval:` |
| Filter (可省) | `/ pid == 1234 /` |
| Actions | `printf(...)` `@map[key] = ...` |

內建變數：

| 名 | 意義 |
|---|---|
| `pid` | process ID |
| `tid` | thread ID |
| `comm` | process name (16 char) |
| `nsecs` | 現在 ns |
| `cpu` | CPU index |
| `uid` | user ID |
| `args` | tracepoint / kfunc 參數 struct |
| `arg0` `arg1` ... | kprobe 參數 |
| `retval` | kretprobe 回傳值 |
| `func` | function 名 |
| `kstack` / `ustack` | kernel / user stack |

## 內建函式

```
printf("...")
print(@map)
count()
sum(x)
hist(x)
lhist(x, min, max, step)
delete(@map[key])
str(addr)
ksym(addr)        # kernel address → name
usym(addr)        # user address → name
ntop(addr)        # IP 印 string
exit()
```

## 實用 script 集

### 看 process exec

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_execve {
    printf("%s exec %s\n", comm, str(args->filename));
}'
```

### 看每次 fork

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_exit_clone /args->ret > 0/ {
    printf("%s pid=%d cloned new pid=%d\n", comm, pid, args->ret);
}'
```

### file IO 量 by process

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_exit_read /args->ret > 0/ { @read[comm] = sum(args->ret); }
tracepoint:syscalls:sys_exit_write /args->ret > 0/ { @write[comm] = sum(args->ret); }'
```

跑一段時間 Ctrl-C：

```
@read[ssh-server]: 12345
@read[firefox]: 23456
@write[postgres]: 1234567
```

### 找 user function 被呼叫多少次

```bash
sudo bpftrace -e 'uprobe:/usr/bin/myprog:my_function { @ = count(); }'
```

### 找 user function latency

```bash
sudo bpftrace -e '
uprobe:/usr/bin/myprog:my_function { @start[tid] = nsecs; }
uretprobe:/usr/bin/myprog:my_function /@start[tid]/ {
    @latency = hist(nsecs - @start[tid]);
    delete(@start[tid]);
}'
```

### 卡住的 process 在哪

```bash
sudo bpftrace -e 'profile:hz:99 /pid == 1234/ { @[ustack] = count(); }'
```

99Hz sample 該 PID 的 user stack。Ctrl-C 看哪個 stack 最常出現。**同 perf record 但更靈活**。

### 看 packet drop

```bash
sudo bpftrace -e '
kprobe:kfree_skb { @[kstack] = count(); }'
```

kernel drop packet 時 call `kfree_skb`，看是哪個路徑 drop。

## bcc tools — 預製腳本

bcc (BPF Compiler Collection) 提供一堆現成工具：

```bash
ls /usr/sbin/*-bpfcc 2>/dev/null | head
# argdist-bpfcc        biolatency-bpfcc   biotop-bpfcc
# btrfsdist-bpfcc      btrfsslower-bpfcc  cachestat-bpfcc
# capable-bpfcc        cpudist-bpfcc      cpuunclaimed-bpfcc
# ...
```

百來個。常用：

```bash
sudo opensnoop-bpfcc          # 即時看誰開檔
sudo execsnoop-bpfcc          # 即時看誰 exec
sudo biolatency-bpfcc         # block IO latency histogram
sudo cachestat-bpfcc          # page cache hit ratio
sudo runqlat-bpfcc            # CPU run queue latency
sudo tcptop-bpfcc             # 像 top 但 TCP byte
sudo tcpconnect-bpfcc         # 看新 TCP 連線
```

`opensnoop-bpfcc` 是 strace 的「全機 + 低 overhead」版：

```bash
sudo opensnoop-bpfcc
# UID    PID    COMM      FD ERR PATH
# 0     1234    sshd      4   0 /var/log/auth.log
# 1000  5678    bash      3   0 /etc/inputrc
# ...
```

跟著做事即時印，**production 觀察首選**。

## 一個常見踩雷：tracepoint args 跟 kprobe args 不一樣

```bash
# tracepoint：用 args->field（按 tracepoint format）
tracepoint:syscalls:sys_enter_openat { str(args->filename) }

# kprobe：用 arg0, arg1, ...（按 register / ABI）
kprobe:do_sys_openat2 { str(arg1) }
```

tracepoint 穩定（kernel ABI 保證），kprobe 隨 kernel 版本變。能用 tracepoint 就用。

## 一個常見踩雷：probe 裝太多會炸

```bash
sudo bpftrace -e 'kprobe:* { @ = count(); }'    # 別這樣！
```

每個 kernel function entry 都裝 probe，系統會卡。**永遠 filter 範圍**。

## 一個常見踩雷：str() 對沒 deref 的指標 crash

```bash
sudo bpftrace -e 'kprobe:f { printf("%s\n", str(arg0)); }'
# arg0 不一定是 valid string pointer
```

加 check：

```bash
/ arg0 != 0 /
```

或用 `bpf_probe_read_user_str`（bpftrace 自動，但路徑要對）。

## 一個常見踩雷：「permission denied」

```bash
$ bpftrace -l
# Error: ...
```

需要 root。`sudo bpftrace` 或設 `CAP_BPF`。

## 動手練習

**1. opensnoop 等價**

寫一行 bpftrace 印每個 openat 的 filename + comm + pid。

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat {
    printf("%-16s %d %s\n", comm, pid, str(args->filename));
}'
```

**2. 統計每個 process 的 syscall 數**

```bash
sudo bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }'
```

跑 10 秒 Ctrl-C 看 top。

**3. 抓某 process 的 read latency**

寫程式做大量 read，bpftrace 印 histogram。

**4. uprobe 自己的程式**

```c
// myprog.c
#include <stdio.h>
#include <unistd.h>
__attribute__((noinline))
void slow_function(int x) {
    usleep(x * 1000);
}
int main() {
    while (1) {
        slow_function(rand() % 100);
        sleep(1);
    }
}
```

```bash
gcc -O0 -g myprog.c -o myprog
./myprog &

sudo bpftrace -e '
uprobe:./myprog:slow_function { @start[tid] = nsecs; }
uretprobe:./myprog:slow_function /@start[tid]/ {
    @lat = hist(nsecs - @start[tid]);
    delete(@start[tid]);
}'
```

**5. 試 bcc tools**

```bash
sudo opensnoop-bpfcc
sudo execsnoop-bpfcc
sudo biolatency-bpfcc 5 1
sudo cachestat-bpfcc 1
```

## 自我檢核

- [ ] 寫得出 「probe / filter / action」格式的 bpftrace one-liner
- [ ] 知道 tracepoint args vs kprobe args 不同
- [ ] 用 hist() / count() / sum() 做 aggregation
- [ ] 跑過 opensnoop-bpfcc / execsnoop-bpfcc
- [ ] 知道 bpftrace 比 strace 適合 production

Part 5 結束。下個 Part 進記憶體跟 correctness 工具 — valgrind 跟 sanitizer。

→ [Ch 15 valgrind memcheck](./15-valgrind-memcheck.md)
