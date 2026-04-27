# Ch 1 — 觀察工具全景

> 目標：建立整門課的 mental map。每個工具看哪一層、什麼時機選哪一個，先看一輪再深入。

## 三層觀察

把要觀察的東西按「**離 process 多遠**」分三層：

```
              你的程式
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
   靜態 binary  動態執行   系統環境
   (ELF)        (runtime)  (周邊)

   ─────────    ─────────  ──────────
   nm           strace     /proc
   readelf      ltrace     lsof
   objdump      ptrace     ss
   addr2line    perf       tcpdump
   ldd          ftrace     iotop
                bpftrace   sysstat
                valgrind   htop
                sanitizer
                gdb
```

三層各看什麼：

- **靜態 binary**：程式還沒跑時能看的 — 它有什麼 symbol、call 哪些 lib、什麼 section、debug info 在不在
- **動態執行**：程式跑著時內部行為 — 呼叫了什麼 syscall、開了什麼檔、配置了多少記憶體
- **系統環境**：跨 process 視角 — 整台機器在做什麼，這個 process 在裡面是什麼角色

debug 時三層都用，順序通常是「**動態 → 系統 → 靜態**」 —— 先看跑起來怎樣，再看跟外界互動，最後翻 binary 找原因。

## 觀察點：使用者程式跟系統的對話

把 Ch 0 那張圖細化：

```
   ┌─────────────────────────────────────┐
   │       你的 C 程式                   │
   │   main() { fp = fopen(...); ... }   │
   └─────────────────┬───────────────────┘
                     │ 呼叫 lib function (fopen, malloc, printf)
                     ▼
   ┌─────────────────────────────────────┐
   │       libc / 其他 .so               │  ← ltrace 看這層
   │  fopen → 內部用 open syscall        │
   └─────────────────┬───────────────────┘
                     │ syscall instruction
═════════════════════╪═══════════════════ user / kernel 邊界
                     ▼
   ┌─────────────────────────────────────┐
   │       Linux kernel                  │  ← strace 在這條線上
   │   sys_openat → vfs_open → ext4_...  │  ← ftrace / bpftrace 看 kernel 內部
   │                                     │  ← perf 看硬體 event (cycles, miss)
   └─────────────────────────────────────┘
```

每一層都有對應工具：

| 想看什麼 | 用什麼 |
|---|---|
| C source 哪一行被執行 | `gdb` step、看 source |
| 呼叫了哪些 lib function | `ltrace` |
| 跨 user/kernel 邊界的 syscall | `strace` |
| kernel 內部走了什麼 path | `ftrace` / `bpftrace` |
| CPU 在做什麼（hot function、cache miss） | `perf` |
| 記憶體 leak / UAF / race | `valgrind` / sanitizer |
| process 開了什麼 fd | `lsof` / `/proc/PID/fd` |
| 網路連線、封包 | `ss` / `tcpdump` |
| 整台機器負載 | `htop` / `vmstat` / `iostat` |

## 同一個 bug 的不同視角

舉例：你的程式 `./server` 「卡住不動」。各工具會告訴你不同的事：

```
$ strace -p $(pgrep server)
read(4, ...)                            ← 卡在 read，4 是某 fd

$ ls -l /proc/$(pgrep server)/fd/4
4 -> socket:[1234]                      ← 那個 fd 是 socket

$ ss -tnp | grep 1234
ESTAB 0 0  10.0.0.5:443  10.0.0.9:5432  ← 連到哪

$ sudo tcpdump -i any host 10.0.0.9
（沒任何 packet）                        ← 對方根本沒回

$ perf top -p $(pgrep server)
（CPU 用量 0%）                          ← 不是 busy loop
```

四個工具拼出來的故事：「卡在等 10.0.0.9:5432 的 reply，但對方沒回」。每個工具看一塊，**沒有單一工具能告訴你全部**。學會挑工具就是這套課的精華。

## 該用哪個？決策樹

簡化版：

```
  程式有問題
       │
       ▼
  能跑嗎？──── 不能 ────► gdb / coredump / sanitizer (Ch 21)
       │
      能
       │
       ▼
  輸出對嗎？─── 對 ────► 是性能問題嗎？─── 是 ──► perf / ftrace (Ch 12-13)
       │                              │
      不對                           不是
       │                              │
       ▼                              ▼
  跟外界互動？─ 否 ──► gdb / printf      在外面看（健康嗎？）
       │                              │
      是                              ▼
       │                          /proc / lsof / ss (Ch 7-9)
       ▼
  syscall / lib call ─ syscall ──► strace (Ch 5)
                       │
                      lib
                       │
                       ▼
                   ltrace (Ch 6)
```

這張圖你會在後面每個練習都翻出來。

## 觀察的成本：別免費用

每個工具都有 overhead，差距巨大：

| 工具 | 大致 overhead | 何時不該用 |
|---|---|---|
| `strace` | 程式慢 5-100x | 高 throughput production |
| `ltrace` | 類似 strace | 同上 |
| `perf record` (sampling) | 1-5% | 一般 OK |
| `ftrace` (function tracer 全開) | 高（每 fn entry/exit） | 只開短時間 |
| `bpftrace` | 1-5%（多數 case） | 一般 OK |
| `valgrind` | 程式慢 10-50x | 永遠別在 prod 跑 |
| `tcpdump` | 視 traffic 量 | 高頻 traffic 要 filter |
| `lsof` | 一次性 snapshot | 高頻 poll 不好 |
| `ASan` 編譯 | 程式慢 2x | 跑 test 跟 staging，prod 視 cost |

「在 production 抓 bug」跟「在開發環境抓 bug」工具選擇不同。**production 用 perf / bpftrace / 短的 strace -p**，valgrind 跟 long-running strace 留給 staging。

## 一個常見誤解：「strace 萬能」

新手很容易愛上 strace，它確實強，但有限制：

- **看不到 lib 內部邏輯**：`fopen` 印出來只看到對應的 `openat`，不知道 fopen 在 libc 內部跑了什麼 stream buffer 邏輯 — 那是 ltrace 的事
- **看不到 kernel 內部路徑**：syscall 進去後它做了什麼，strace 不知道 — 那是 ftrace / bpftrace
- **看不到 cache miss / branch miss**：performance counter 是 perf 的領域
- **看不到 race condition**：兩個 thread 同時跑都進 syscall，strace 看到順序但看不出 race — 那是 helgrind / TSan

**沒有單一神器**。這門課的目的就是讓你知道每個工具的位置與限制。

## 一個常見誤解：「printf debug 比工具快」

短期對。長期不對。printf debug 的問題：

- 改 source、重編、重跑：循環時間長
- 印太多看不出重點，印太少漏線索
- 改變了 timing：race condition 被 printf 改掉
- production binary 你沒 source / 不能加 printf

`strace -e trace=openat ./prog` 不用改 source 就能看到所有 open。一旦工具會用，「不改 source 就能觀察」會變預設思維。

## 一個常見誤解：「debugger 一定要 step-by-step」

step debug 是 gdb 一種模式，不是唯一。多數 production debugging 是：

- bug 已經發生 → core dump 事後分析
- bug 還在發生 → strace / gdb 直接 attach
- bug 偶爾發生 → bpftrace + 觸發條件

step debug 適合「我寫的 algorithm 邏輯不對」。這套課重點是「跑起來才出現的問題」 —— 不太用 step。

## 動手練習

**1. 試這張表的每個工具**

寫一個會跑 30 秒的程式 `test.c`：

```c
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>

int main(void) {
    int fd = open("/tmp/test.log", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    for (int i = 0; i < 30; i++) {
        char buf[64];
        int n = snprintf(buf, sizeof(buf), "iter %d\n", i);
        write(fd, buf, n);
        sleep(1);
    }
    close(fd);
    return 0;
}
```

```bash
gcc test.c -o test
./test &
PID=$!
echo "PID = $PID"

# 各種觀察，每個跑幾秒按 Ctrl-C 停
strace -p $PID -e trace=write,nanosleep
ltrace -p $PID
ls -l /proc/$PID/fd/
lsof -p $PID
cat /proc/$PID/status | head
sudo perf top -p $PID

wait $PID
```

對照看每個輸出的差異，**心裡記下每個工具看到了什麼、看不到什麼**。

**2. 五個情境，先猜再翻答案**

下面 5 個 bug 情境，先想要先用什麼，再翻答案對：

1. 程式吃 100% CPU、看不出在幹嘛
2. 程式不時 crash，stack trace 不一致
3. 程式變慢但 CPU 用量正常
4. 程式 fopen 一個檔案，回傳成功但 read 拿到空
5. 兩個 process 互卡 (deadlock)

<details>
<summary>建議的工具優先順序</summary>

1. `perf top -p PID` 看 CPU sample；`strace -c` 看 syscall 分布占比
2. `ulimit -c unlimited` 開 core，crash 後 `gdb -c core ./prog`；rebuild 加 `-fsanitize=address` 重跑
3. `strace -c` 看是不是太多 syscall；`perf stat` 看 IPC（cycles per instruction）；`iotop` 看是不是磁碟卡
4. `strace -e trace=openat,read,readv -y` —— `-y` 顯示 fd 對應路徑，看是不是 fopen 開了不同的檔
5. `gdb -p PID1` 跟 `gdb -p PID2` 各看 backtrace；`/proc/PID/stack` 看 kernel 堆疊；`bt full` 看 lock 結構

每題對應的工具後面章節都會展開，現在只是建立直覺。

</details>

## 自我檢核

- [ ] 講得出三層（static / dynamic / system）各包含哪些工具
- [ ] 知道 strace 看 syscall、ltrace 看 lib function、ftrace 看 kernel function
- [ ] 知道每個工具大致 overhead、哪些不能在 production 跑
- [ ] 五個情境能說出第一個試什麼工具
- [ ] 知道為什麼「沒有單一神器」

下一章補基礎：process、syscall、signal、fd 模型。後面所有工具都建在這上面。

→ [Ch 2 process / syscall / signal / fd 模型](./02-process-syscall-fd-model.md)
