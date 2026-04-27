# Ch 13 — ftrace / tracefs

> 目標：搞清楚 ftrace 是什麼、它能看什麼、跟 strace / perf 怎麼互補。學 trace-cmd 跟 raw tracefs 操作。

## ftrace 是什麼

Linux kernel 內建的「function tracer」框架。能力：

- 追每個 kernel function 的 entry / exit
- 各種 tracepoint（pre-defined event）
- kprobe / uprobe（動態插 trace 點）
- latency tracker（找 wakeup 延遲、interrupt off duration）

**比 strace 強的地方**：能看 kernel 內部，不是 syscall 邊界。**比 perf 強的地方**：精確的「每個事件」紀錄而非採樣。

**比較吃 overhead**：function tracer 全開能讓系統慢 10x。

## 兩種介面

1. **raw tracefs**：`/sys/kernel/tracing/`（舊版叫 `debugfs/tracing`）。直接 echo / cat 控制，最底層
2. **`trace-cmd`** + **`kernelshark`**：包好的 CLI / GUI

學底層用 raw，平常用 trace-cmd。

## 基本路徑

```bash
sudo ls /sys/kernel/tracing/
# available_events   current_tracer       tracing_on
# available_tracers  events/              uprobe_events
# buffer_size_kb     kprobe_events        ...
# trace              trace_pipe
```

關鍵檔案：

| 檔案 | 用法 |
|---|---|
| `available_tracers` | 看有哪些 tracer |
| `current_tracer` | 設要用哪個 (echo 進去) |
| `available_events` | 列所有 tracepoint |
| `set_event` | 設要追哪個 event |
| `tracing_on` | 1/0 開關 |
| `trace` | 讀 trace buffer |
| `trace_pipe` | streaming 讀（會清掉） |
| `buffer_size_kb` | per-CPU buffer |

## 用 raw tracefs：trace 一個 syscall

```bash
sudo -i
cd /sys/kernel/tracing

echo > trace                                       # clear
echo function > current_tracer
echo do_sys_openat2 > set_ftrace_filter            # 只看這個 function
echo 1 > tracing_on

# 在另一個 terminal:
ls /tmp

echo 0 > tracing_on
cat trace
```

```
# tracer: function
#
# entries-in-buffer/entries-written: 5/5
#
#                                _-----=> irqs-off
#                               / _----=> need-resched
#                              | / _---=> hardirq/softirq
#                              || / _--=> preempt-depth
#                              ||| / _-=> migrate-disable
#                              |||| /     delay
#           TASK-PID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |   |||||     |         |
              ls-1234    [002] .....  1234.567: do_sys_openat2 <-__x64_sys_openat
              ls-1234    [002] .....  1234.568: do_sys_openat2 <-__x64_sys_openat
              ...
```

每行一個 function entry，含 PID / CPU / timestamp。`<-` 之後是 caller。

## 兩個重要 tracer

```bash
cat available_tracers
# hwlat blk mmiotrace function_graph wakeup_dl wakeup_rt wakeup function nop
```

最常用：

- **`function`**：每個 function 一行 entry
- **`function_graph`**：tree view，含 elapsed time

```bash
echo function_graph > current_tracer
echo do_sys_openat2 > set_graph_function

# trigger
ls /tmp

cat trace | head -30
```

```
# tracer: function_graph
#
# CPU  DURATION                  FUNCTION CALLS
# |     |   |                     |   |   |   |
 2)               |  do_sys_openat2() {
 2)               |    getname() {
 2)   1.234 us   |      kmem_cache_alloc();
 2)   0.567 us   |      strncpy_from_user();
 2)   2.345 us   |    }
 2)               |    do_filp_open() {
 2)               |      path_openat() {
 2)   1.123 us   |        link_path_walk.part.0();
 ...
```

「樹狀 + duration」，**找 kernel 慢的標準工具**。

## tracepoint

預先 instrument 好的 event。比 function tracer 穩定（API 保證）：

```bash
cat available_events | head
# bcache:bcache_alloc
# block:block_bio_complete
# block:block_rq_insert
# ...
# sched:sched_switch
# sched:sched_wakeup
# syscalls:sys_enter_openat
# syscalls:sys_exit_openat
```

用法：

```bash
echo > trace
echo nop > current_tracer

echo 1 > events/syscalls/sys_enter_openat/enable
echo 1 > tracing_on

ls /tmp

echo 0 > tracing_on
cat trace
```

```
ls-1234 [002] ...  1234.567: sys_openat(dfd: ffffff9c, filename: ..., flags: ...)
```

每個 syscall 進入會印。

## trace-cmd — 高階 wrapper

```bash
# list events
sudo trace-cmd list -e | head

# 簡單採集
sudo trace-cmd record -e syscalls:sys_enter_openat -P PID
# ... 跑你想觀察的事 ...
# Ctrl-C
sudo trace-cmd report

# function tracer
sudo trace-cmd record -p function -l do_sys_openat2 ls /tmp

# function graph
sudo trace-cmd record -p function_graph -g do_sys_openat2 ls /tmp
sudo trace-cmd report
```

比 raw 簡單多了。產生 `trace.dat` file。

`kernelshark` 是 GUI viewer：

```bash
sudo trace-cmd record -e syscalls -P PID
kernelshark trace.dat
```

## kprobe — 動態插 trace 點

ftrace 預先列在 `available_events` 的是 static tracepoint。要 trace 沒 tracepoint 的 function，用 kprobe：

```bash
echo 'p:my_open do_sys_openat2 file=+0(%si):string' > kprobe_events
echo 1 > events/kprobes/my_open/enable
echo 1 > tracing_on

ls /tmp

cat trace
```

`p:my_open do_sys_openat2 file=+0(%si):string`：

- `p:` 是 kprobe（vs `r:` retprobe）
- `my_open` 是給這 probe 取的名字
- `do_sys_openat2` 是 hook 的 function
- `file=+0(%si):string` 是「從 register %si 拿 pointer，dereference 取 string，叫 file」

bpftrace 比 raw kprobe 寫得順。下一章。

## uprobe — user-space function

trace user-space function（自家 binary 或 .so）：

```bash
echo 'p:my_main /home/me/myprog:0x401234' > uprobe_events
echo 1 > events/uprobes/my_main/enable
echo 1 > tracing_on
```

也是 bpftrace 用的底層。

## 一個常見場景：「為什麼某 syscall 這麼慢」

```bash
sudo trace-cmd record -p function_graph -g __x64_sys_read sleep 5
# 在另 terminal 觸發 read
sudo trace-cmd report | less
```

看 read 內部走哪、每段 elapsed time。

## 一個常見場景：「kernel 啟動誰調用 schedule」

```bash
echo > trace
echo function > current_tracer
echo schedule > set_ftrace_filter
echo function-trace > trace_options
echo 1 > tracing_on
sleep 1
echo 0 > tracing_on
cat trace | head
```

## 一個常見踩雷：忘了清 buffer

```bash
echo > trace                # 清
echo > set_ftrace_filter    # 清 filter
echo nop > current_tracer   # reset
echo 0 > tracing_on
```

每次實驗前 reset，不然舊資料混進來。

## 一個常見踩雷：tracing_on 沒開

```bash
echo function > current_tracer
echo do_sys_openat2 > set_ftrace_filter
ls /tmp
cat trace            # 啥都沒有
```

`tracing_on` 沒開 (= 0)，啥都不錄。**第一個 check 它**。

## 一個常見踩雷：太多 trace 拖垮系統

```bash
echo function > current_tracer
echo 1 > tracing_on        # 全 kernel function 都印
# 系統幾乎卡住
```

**function tracer 一定要 set_ftrace_filter**，別開全部。

## ftrace vs perf vs bpftrace

| 工具 | 機制 | 適合 |
|---|---|---|
| ftrace | kernel 內 buffer | 精確 trace、function graph |
| perf | sample + event | profile、CPU time、hot function |
| bpftrace | eBPF | 自寫腳本、aggregate、複雜邏輯 |

實務上 bpftrace 用得多。ftrace 適合「我要精確紀錄每個 event」、「我要看 function graph 含 timing」。

## 動手練習

**1. 跑 raw ftrace**

```bash
sudo -i
cd /sys/kernel/tracing
echo > trace
echo function > current_tracer
echo schedule > set_ftrace_filter
echo 1 > tracing_on
sleep 1
echo 0 > tracing_on
head trace
```

**2. function_graph 一個 syscall**

```bash
echo > trace
echo function_graph > current_tracer
echo __x64_sys_openat > set_graph_function
echo 1 > tracing_on

# 另 terminal:
cat /etc/passwd > /dev/null

echo 0 > tracing_on
head -50 trace
```

**3. tracepoint 看 sched_switch**

```bash
echo > trace
echo nop > current_tracer
echo 1 > events/sched/sched_switch/enable
echo 1 > tracing_on
sleep 1
echo 0 > tracing_on
head trace
```

看 task 切換的 record。

**4. trace-cmd 包裝版**

```bash
sudo trace-cmd record -e sched:sched_switch sleep 1
sudo trace-cmd report | head
```

**5. 對著 kernelshark 看**

```bash
kernelshark trace.dat
```

GUI，可以放大縮小看每個 CPU 跑誰。

## 自我檢核

- [ ] 知道 ftrace 跟 strace 觀察層不同
- [ ] 用 raw tracefs trace 過一個 function
- [ ] 跑過 function_graph、看得懂 duration
- [ ] 用 trace-cmd record + report
- [ ] 知道 kprobe / uprobe 是動態插的，跟 tracepoint 差別
- [ ] 知道 function tracer 不加 filter 會炸

下一章看 bpftrace —— ftrace 的「現代腳本化」版本。

→ [Ch 14 bpftrace 從 debug 角度](./14-bpftrace-debug-view.md)
