# Ch 12 — perf 基礎

> 目標：學會 perf stat / record / report / top / annotate / sched / flame graph —— Linux 上效能分析的標準工具。

## perf 是什麼

Linux kernel 內建的 profiling / tracing framework，user-space tool 叫 `perf`。能力：

- **counter**：CPU hardware event（cycles, instructions, cache miss, branch miss）
- **sampling profiler**：定期 snapshot 每個 CPU 在跑什麼 function
- **tracing**：tracepoint / kprobe / uprobe，事件驅動
- **schedule analysis**：誰在等 CPU、誰被搶走

**perf 是 modern profiling 第一選擇**。比 gprof / oprofile 都強。

## 子命令一覽

```bash
perf stat           # 計算 hardware counter
perf top            # 即時看誰吃 CPU（像 top 但 function 層）
perf record         # 採樣存檔
perf report         # 看採樣
perf annotate       # source / asm 對照看時間
perf list           # 列所有可用 event
perf sched          # scheduler 分析
perf trace          # 像 strace 但用 perf 機制
perf bench          # 內建 microbenchmark
perf script         # 把 perf.data 變成文字 / 給 flame graph
```

## perf stat — 數一次 counter

最簡單的 perf 用法：

```bash
perf stat -- ./myprog
```

```
 Performance counter stats for './myprog':

         1234.56 msec task-clock                #    0.998 CPUs utilized
              23      context-switches          #   18.633 /sec
               1      cpu-migrations            #    0.810 /sec
              45      page-faults               #   36.452 /sec
   3,456,789,012      cycles                    #    2.800 GHz
   5,678,901,234      instructions              #    1.64  insn per cycle
     901,234,567      branches                  #  730.123 M/sec
       2,345,678      branch-misses             #    0.26% of all branches

      1.237 seconds time elapsed
```

關鍵 metric：

- **cycles**：CPU clock 跑了多少
- **instructions**：執行了多少條
- **insn per cycle (IPC)**：每 cycle 平均執行幾條 — **效能黃金指標**
- **branch-misses %**：分支預測錯比例

IPC：

- `> 2.0` 是 hot path 跑得很順
- `0.5 - 1.0` 普通
- `< 0.5` 很慢，可能 cache miss / branch miss / memory bound

```bash
perf stat -d -- ./myprog       # 加 detailed (cache, etc)
perf stat -e cycles,L1-dcache-loads,L1-dcache-load-misses -- ./myprog
perf stat -p PID sleep 5       # attach 5 秒
perf stat -a sleep 5           # 全機
perf stat -r 5 -- ./myprog     # 跑 5 次取平均
```

## perf top — 即時 hot function

```bash
sudo perf top
sudo perf top -p PID
sudo perf top --stdio
```

像 `top` 但顯示**哪個 function 在燒 CPU**。預設整機，按 `F` toggle 各種模式。

```
Overhead  Shared Object       Symbol
  12.34%  myprog              [.] hot_function
   8.90%  libc.so.6           [.] __memcpy_avx_unaligned
   5.67%  [kernel]            [k] __schedule
```

`[.]` user space、`[k]` kernel。

## perf record + report — 採樣分析

```bash
perf record -F 99 ./myprog          # 99 Hz 採樣
perf record -F 99 -p PID sleep 30   # attach
perf record -F 99 -a sleep 30       # 全機
perf record -g -F 99 ./myprog       # 加 call graph
perf record --call-graph dwarf ./myprog   # 用 DWARF unwind
```

跑完產生 `perf.data`：

```bash
perf report
```

互動式 UI。常用快捷鍵：

- `Enter` 展開
- `a` annotate（看 asm）
- `+` toggle children
- `/` 搜尋
- `q` 離開

```
Samples: 12K of event 'cycles', Event count (approx.): 12345678
Overhead  Command    Shared Object       Symbol
+   23.45%  myprog    myprog              [.] hot_function
+    8.90%  myprog    libc.so.6           [.] __memcpy_avx_unaligned
+    5.67%  myprog    [kernel]            [k] __schedule
```

## call graph

`-g` 加 call graph，**強烈建議**。預設用 frame pointer：

```bash
perf record -g ./myprog
perf report
```

開啟某 function 看「誰 call 它 / 它 call 誰」。

frame pointer 不準（gcc 預設 `-fomit-frame-pointer`）時用 DWARF：

```bash
perf record --call-graph dwarf ./myprog    # 比 frame pointer 慢但準
perf record --call-graph lbr ./myprog       # CPU LBR (Intel)，需新 CPU
```

debug build 應該加 `-fno-omit-frame-pointer` 配 perf。

## perf annotate

進到 function 看「每行 asm 多熱」：

```bash
perf annotate hot_function
```

```
       │     for (int i = 0; i < n; i++) {
  0.12 │      mov    %eax, %ebx
       │     a[i] = b[i] * c[i];
 23.45 │      mov    (%rdi,%rax,4), %edx
 12.34 │      imul   (%rsi,%rax,4), %edx
 45.67 │      mov    %edx, (%rdx,%rax,4)
       │     }
  3.21 │      add    $1, %rax
```

百分比是這條指令燒了多少 cycle。**找 hotspot 的 ground truth**。

## 常用 event 一覽

```bash
perf list | head -50
```

幾百個。最常用：

| Event | 意義 |
|---|---|
| `cycles` | CPU clock |
| `instructions` | 指令數 |
| `cache-misses` | 任意 cache miss |
| `cache-references` | cache 訪問 |
| `L1-dcache-load-misses` | L1 data cache miss |
| `LLC-load-misses` | last level cache miss（最痛） |
| `branch-misses` | 分支預測錯 |
| `dTLB-load-misses` | TLB miss |
| `iTLB-load-misses` | instruction TLB miss |
| `page-faults` | page fault |
| `context-switches` | 切換 |
| `cpu-migrations` | 跨 core 移動 |
| `task-clock` | 程式佔的 CPU time |

tracepoint：

```bash
perf list | grep 'sched:'
# sched:sched_switch
# sched:sched_wakeup
# ...
```

## flame graph

最直觀的 profile 視覺化。Brendan Gregg 發明。

```bash
# 採樣
perf record -F 99 -g -p PID sleep 30
perf script > out.perf

# 用 FlameGraph repo
git clone https://github.com/brendangregg/FlameGraph.git
./FlameGraph/stackcollapse-perf.pl out.perf > out.folded
./FlameGraph/flamegraph.pl out.folded > out.svg

# 開 browser 看 out.svg
```

每根「火焰」是一個 function，**寬度 = 時間佔比**，**高度 = call stack 深度**。一眼看出哪段 hot。

## perf trace — 像 strace 但快

```bash
sudo perf trace -p PID
```

跟 strace 一樣印 syscall，但用 ftrace + perf event 機制，**overhead 低很多**。

filter：

```bash
sudo perf trace -e 'openat,read' -p PID
sudo perf trace --max-events 100 -p PID
sudo perf trace --duration 1 -p PID    # 只印 > 1ms 的 syscall
```

production 觀察 syscall 用 perf trace > strace。

## perf sched — 看誰在等

```bash
sudo perf sched record sleep 5
sudo perf sched latency
```

```
Task                  Runtime ms  Switches Average delay ms Maximum delay ms
myprog:1234              123.456     1234           0.567           12.345
postgres:5678             45.678      234           0.123            2.345
```

`Average delay` 是「task 被 wake 後到實際跑」的等待時間。**高 = scheduler 競爭嚴重**。

```bash
sudo perf sched timehist           # 一條條 timeline
sudo perf sched map                # 視覺化每個 CPU 在跑誰
```

## kernel.perf_event_paranoid

`perf` 對 kernel event 預設限制。`/proc/sys/kernel/perf_event_paranoid` 值：

- `3` — 完全不准 user
- `2` — 只 kernel 自己可用
- `1` — user 看 user / kernel events
- `0` — user 看 hardware events
- `-1` — 全部

設定：

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
```

或永久：`/etc/sysctl.d/perf.conf`。

## 一個常見踩雷：採樣率太高

```bash
perf record -F 99999 ./myprog
```

99999Hz 採樣會嚴重 overhead。常用 99 / 999。**不用 100 / 1000 因為避開 timer 同頻 alias**。

## 一個常見踩雷：沒 frame pointer，call graph 是亂的

```bash
gcc -O2 myprog.c -o myprog        # 預設 -fomit-frame-pointer
perf record -g ./myprog
perf report                        # call graph 看起來怪怪
```

修：

```bash
gcc -O2 -fno-omit-frame-pointer -g myprog.c -o myprog
```

或用 DWARF unwind：

```bash
perf record --call-graph dwarf ./myprog
```

DWARF 慢但準。

## 一個常見踩雷：perf annotate 沒 source

```bash
perf annotate hot_function
# 只看到 asm，沒 C source
```

binary 要 `-g` build，且 source file 還在原路徑（perf 會 read source file）。

## 動手練習

**1. perf stat hello world**

```bash
perf stat -- /bin/ls /
```

看 IPC、page faults。跑 `/bin/ls /var/log` 對比 IPC 差異。

**2. 找 hot function**

寫個故意慢的：

```c
// slow.c
#include <stdio.h>
int hot(int n) {
    int sum = 0;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            sum += i * j;
    return sum;
}
int main() {
    printf("%d\n", hot(10000));
}
```

```bash
gcc -O0 -g -fno-omit-frame-pointer slow.c -o slow
perf stat -- ./slow
sudo perf record -g -F 999 ./slow
sudo perf report
```

`hot` 應該占 99%。

**3. cache miss demo**

```c
// cache.c — 對比 row-major vs column-major
#include <stdio.h>
#include <stdlib.h>
#define N 4096
int main() {
    int (*a)[N] = malloc(sizeof(int[N][N]));
    long sum = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            sum += a[i][j];        // row-major (cache friendly)
    printf("%ld\n", sum);
}
```

```bash
gcc -O2 cache.c -o row
perf stat -e cycles,LLC-loads,LLC-load-misses ./row
```

換成 `a[j][i]`（column）再跑，LLC-load-misses 會暴增。

**4. flame graph**

```bash
sudo perf record -F 99 -g -- ./slow
sudo perf script > out.perf
git clone https://github.com/brendangregg/FlameGraph.git
./FlameGraph/stackcollapse-perf.pl out.perf > out.folded
./FlameGraph/flamegraph.pl out.folded > flame.svg
xdg-open flame.svg
```

**5. perf trace 對照 strace**

```bash
time strace -c /bin/ls /usr > /dev/null
time sudo perf trace -s /bin/ls /usr > /dev/null
```

perf trace 通常快很多。

## 自我檢核

- [ ] perf stat 看得懂 IPC、cache miss、branch miss
- [ ] perf record / report / annotate 跑得通
- [ ] 知道 -g 跟 --call-graph dwarf 差別
- [ ] 跑過 flame graph
- [ ] 知道 perf_event_paranoid 是什麼
- [ ] 知道採樣 frequency 用 99 不用 100

下一章看 ftrace —— kernel 內部的 function tracer。

→ [Ch 13 ftrace / tracefs](./13-ftrace-and-tracefs.md)
