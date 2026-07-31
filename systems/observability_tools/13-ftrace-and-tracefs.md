# Ch 13 — ftrace 與 tracefs

> **目標**：理解 ftrace——Linux kernel 內建的函式 tracer，能 trace kernel 內部的函式呼叫、看一個 syscall 在 kernel 裡走了哪些函式、追蹤特定事件（tracepoint）。理解 tracefs（透過 /sys/kernel/tracing 控制 ftrace 的介面）、function tracer、function graph（看函式呼叫圖）。從「使用者空間的觀察」進到「kernel 內部」。這章是 kernel 層觀察的入口，也為 bpftrace（Ch 14）和 bpf 課鋪墊。

> **環境**：Linux，ftrace（kernel 內建）。需要 root（操作 /sys/kernel/tracing）。

## 為什麼需要 kernel 層的觀察？

前面的工具觀察「使用者空間」——strace 看 process 對 kernel 的請求（syscall 的邊界）、perf 看使用者空間的函式。但有時問題在 **kernel 內部**——一個 syscall 為什麼慢（它在 kernel 裡做了什麼）？kernel 的某個子系統在做什麼？某個 kernel 事件何時發生？這些需要看 kernel 內部的函式呼叫。

**ftrace** 是 Linux kernel **內建**的 tracer（不用裝額外工具，kernel 自帶）——它能 trace kernel 內部的函式、看 syscall 在 kernel 裡的執行路徑、追蹤 tracepoint（kernel 預設的觀察點）。理解 ftrace 讓你能觀察 kernel 內部（前面的工具到 syscall 邊界就停了，ftrace 能看 syscall 進 kernel 後做什麼）。它也是 bpftrace（Ch 14）和 eBPF（bpf 課）的前身/基礎——理解 ftrace，你就理解了 kernel tracing 的基礎機制。

## 先建立直覺:kernel 的內視鏡

```
觀察的邊界（前面的工具 vs ftrace）：

  使用者空間                    kernel 空間
    程式 ──syscall──▶ [ kernel 內部做事 ] ──▶ 回傳
       │                  │
   strace 看到這裡          ftrace 看這裡（kernel 內部）
   （syscall 邊界）         （syscall 進 kernel 後走哪些函式）
        │
  ftrace = kernel 的「內視鏡」
    看 kernel 內部的函式呼叫
    例：read syscall 進 kernel → vfs_read → ext4_read → ...
        strace 只看到 read 這個 syscall
        ftrace 看到 read 在 kernel 裡走了哪些函式
        │
  → ftrace 觀察「kernel 內部」（前面工具看不到的）
    回答「syscall 在 kernel 裡做什麼/為什麼慢」
```

關鍵心智：前面的工具到 syscall 邊界就停了（strace 看「呼叫了 read」，但看不到 read 進 kernel 後做什麼）。**ftrace 是 kernel 的「內視鏡」**——看 kernel 內部的函式呼叫（read 進 kernel 後走 vfs_read → ext4_read → …）。它觀察 kernel 內部，回答「syscall 在 kernel 裡做什麼、為什麼慢」。

> ftrace 觀察 kernel 內部，補上 strace（到 syscall 邊界）和 perf（使用者空間函式）看不到的層。它是 Ch 14（bpftrace）和 bpf 課的基礎。需要 root（操作 kernel 的 tracing 介面）。

## tracefs:控制 ftrace 的介面

```bash
# ftrace 透過 tracefs（檔案系統介面）控制 —— 「一切皆檔案」的又一例
# tracefs 掛在 /sys/kernel/tracing（或 /sys/kernel/debug/tracing）
sudo ls /sys/kernel/tracing/
# available_tracers   ← 有哪些 tracer
# current_tracer      ← 當前用哪個（寫入切換）
# trace               ← trace 的輸出（讀這個看結果）
# trace_pipe          ← 即時的 trace 輸出
# set_ftrace_filter   ← 只 trace 哪些函式
# events/             ← tracepoint 事件
# tracing_on          ← 開關

# 看有哪些 tracer
sudo cat /sys/kernel/tracing/available_tracers
# function function_graph blk mmiotrace nop ...
#   function：trace 函式被呼叫
#   function_graph：trace 函式呼叫圖（含進入/離開、耗時）

# ftrace 的操作方式：寫檔案控制、讀檔案看結果（一切皆檔案）
# 1. 選 tracer：echo function > current_tracer
# 2. 設過濾：echo <函式> > set_ftrace_filter
# 3. 開啟：echo 1 > tracing_on
# 4. 看結果：cat trace
# 5. 關閉：echo 0 > tracing_on
```

> **ftrace 透過 tracefs（寫檔案控制、讀檔案看結果）操作——這是「一切皆檔案」的又一例，也讓 ftrace 不需要特殊工具**。ftrace 內建在 kernel，透過 **tracefs**（掛在 `/sys/kernel/tracing`）這個檔案系統介面控制——你用 `echo` 寫入控制檔案、用 `cat` 讀結果檔案。關鍵檔案：`available_tracers`（有哪些 tracer）、`current_tracer`（寫入切換 tracer）、`trace`（讀它看結果）、`trace_pipe`（即時輸出）、`set_ftrace_filter`（只 trace 哪些函式）、`events/`（tracepoint）、`tracing_on`（開關）。操作流程：選 tracer（`echo function > current_tracer`）→ 設過濾 → 開啟（`echo 1 > tracing_on`）→ 看結果（`cat trace`）→ 關閉。這個「寫檔案控制、讀檔案看結果」的設計是 Linux「一切皆檔案」哲學的又一體現（呼應 /proc，Ch 7）——你不需要特殊工具，用最基本的 echo/cat 就能控制 kernel 的 tracer。這也讓 ftrace 在任何 Linux 都能用（不用裝東西）。實務上常用 **trace-cmd**（ftrace 的前端工具，把這些檔案操作包裝成命令）或 perf（也能用 ftrace 的功能），但理解底層的 tracefs 操作讓你知道它怎麼運作。主要的兩個 tracer：**function**（trace 函式被呼叫）、**function_graph**（trace 函式呼叫圖——含進入/離開、耗時，最有用）。

## function_graph:看 kernel 函式呼叫圖

```bash
# 用 trace-cmd（ftrace 的前端，更方便）trace 一個 syscall 在 kernel 裡的路徑
# sudo apt install trace-cmd

# trace 一個命令的 kernel 函式呼叫圖
sudo trace-cmd record -p function_graph -g 'sys_*' ls > /dev/null
sudo trace-cmd report | head -30
# 看到 ls 的 syscall 在 kernel 裡走了哪些函式

# 或直接用 tracefs（看某個函式被誰呼叫）
# 例：trace vfs_read（read syscall 在 kernel 的入口）
echo function_graph | sudo tee /sys/kernel/tracing/current_tracer
echo vfs_read | sudo tee /sys/kernel/tracing/set_graph_function
echo 1 | sudo tee /sys/kernel/tracing/tracing_on
cat /etc/hostname > /dev/null     # 觸發 read
echo 0 | sudo tee /sys/kernel/tracing/tracing_on
sudo cat /sys/kernel/tracing/trace | head -20
# vfs_read() {
#   rw_verify_area() { ... }
#   __vfs_read() {
#     ext4_file_read_iter() { ... }   ← read 在 kernel 裡走到 ext4！
#   }
# }   2.5 us                           ← 還顯示耗時！
# → 看到 read syscall 進 kernel 後的完整函式呼叫圖 + 每個函式耗時
```

```
function_graph 的輸出（kernel 函式呼叫圖）：

  vfs_read() {                  ← 進入 vfs_read
    rw_verify_area() { }   0.3us  ← 子函式 + 耗時
    __vfs_read() {
      ext4_file_read_iter() {     ← 巢狀呼叫
        ...
      } 1.8us
    } 2.0us
  } 2.5us                        ← vfs_read 總耗時
        │
  → 縮排顯示呼叫關係（誰呼叫誰）
    每個函式顯示耗時（找 kernel 裡的慢點）
    這是「syscall 在 kernel 裡做什麼、哪裡慢」的視角
```

> **function_graph 顯示「kernel 函式呼叫圖 + 每個函式耗時」——這是 debug「syscall 為什麼慢」的 kernel 層視角**。`function_graph` tracer 是 ftrace 最有用的——它顯示 kernel 函式的**呼叫圖**（縮排顯示誰呼叫誰）+ **每個函式的耗時**。上面的例子，trace `vfs_read`（read syscall 在 kernel 的入口）看到它走到 `__vfs_read` → `ext4_file_read_iter`（檔案系統的讀取）——這揭示了「read syscall 進 kernel 後的完整路徑」（strace 只看到 `read()` 這個 syscall，ftrace 看到它在 kernel 裡走了哪些函式）。而且**顯示耗時**——你能看出「kernel 裡哪個函式慢」。這對 debug「syscall 為什麼慢」極有用：strace `-T` 告訴你「這個 read 花了 5ms」，但**為什麼慢**？ftrace 的 function_graph 告訴你「read 在 kernel 裡走到某個函式花了 5ms」（如等鎖、等 IO、某個慢的 kernel 操作）。這是 strace 之後的「kernel 層深入」——當 syscall 慢但不知 kernel 裡為什麼慢時，用 ftrace。`trace-cmd`（ftrace 的前端工具）讓操作更方便（包裝了 tracefs 的檔案操作）。理解 function_graph，你能觀察 kernel 內部的執行路徑和耗時——這是前面工具看不到的層，也是理解 kernel 行為（為什麼某個操作慢、kernel 的某個子系統怎麼運作）的窗口。

## tracepoint:kernel 的預設觀察點

```bash
# tracepoint：kernel 開發者預先在關鍵位置放的「觀察點」
# （比 trace 任意函式更穩定、更有意義）
sudo ls /sys/kernel/tracing/events/
# block/ sched/ syscalls/ net/ ... ← 各子系統的 tracepoint

# 例：trace process 排程（sched）事件
sudo ls /sys/kernel/tracing/events/sched/
# sched_switch（context switch）sched_wakeup ...

# 用 trace-cmd trace 排程事件（看 process 何時被排程）
# sudo trace-cmd record -e sched:sched_switch sleep 1
# sudo trace-cmd report
# → 看到 CPU 在哪些 process 之間切換（context switch）

# 常用的 tracepoint 類別：
#   syscalls/  每個 syscall 的進入/離開
#   sched/     排程（context switch, wakeup）
#   block/     磁碟 IO
#   net/       網路
#   irq/       中斷

# tracepoint vs trace 任意函式：
#   tracepoint：kernel 開發者定義的「穩定觀察點」（跨版本穩定、有意義的參數）
#   任意函式：能 trace 任何 kernel 函式，但函式名/行為隨 kernel 版本變
```

> **tracepoint 是 kernel 開發者預設的「穩定觀察點」——比 trace 任意函式更穩定有意義，是 bpftrace/eBPF 的重要基礎**。**tracepoint** 是 kernel 開發者**預先在關鍵位置放的觀察點**——如 `sched:sched_switch`（process 切換）、`block:block_rq_issue`（磁碟 IO 發出）、`net:netif_receive_skb`（收到網路封包）。它們比「trace 任意 kernel 函式」更好：**穩定**（跨 kernel 版本穩定，是 kernel 的 ABI 一部分——而任意函式名可能隨版本改變）、**有意義的參數**（tracepoint 帶有用的參數，如 sched_switch 帶「從哪個 process 切到哪個」）。tracepoint 分類在 `/sys/kernel/tracing/events/` 下（sched/block/net/syscalls/irq…各子系統）。用途：觀察 kernel 的特定事件——「process 何時被排程」（sched）、「磁碟 IO 何時發出」（block）、「網路封包何時收到」（net）。這對理解系統行為（為什麼 context switch 這麼多、IO 的模式）很有用。**tracepoint 也是 bpftrace（Ch 14）和 eBPF（bpf 課）的重要基礎**——它們大量用 tracepoint 當觀察點（穩定、有意義）。所以理解 tracepoint，你為 Ch 14 和 bpf 課打好了基礎。ftrace 是「靜態的」kernel tracer（trace 函式/tracepoint，輸出固定格式），而 bpftrace（Ch 14）是「可程式化的」（你寫程式決定 trace 什麼、怎麼處理）——但兩者都建立在同樣的 kernel tracing 機制（函式 hook、tracepoint）上。ftrace 是基礎，bpftrace/eBPF 是更靈活的演進。

## 故意弄壞:trace 一個慢 syscall 的 kernel 路徑

```bash
# 用 ftrace 看「一個 syscall 為什麼慢」（kernel 層深入）
# 場景：strace 顯示某個 read 慢，但不知 kernel 裡為什麼

# 簡化示範：trace read syscall 在 kernel 的路徑和耗時
# （需要 root）
TRACE=/sys/kernel/tracing

# 設定 function_graph trace vfs_read
echo function_graph | sudo tee $TRACE/current_tracer > /dev/null
echo vfs_read | sudo tee $TRACE/set_graph_function > /dev/null
echo 1 | sudo tee $TRACE/tracing_on > /dev/null

# 觸發一個 read（讀一個檔案）
cat /etc/passwd > /dev/null

echo 0 | sudo tee $TRACE/tracing_on > /dev/null
sudo cat $TRACE/trace | grep -A20 vfs_read | head -25
# vfs_read() {
#   ...各 kernel 函式 + 耗時...
# } X us
# → 看到 read 在 kernel 裡的完整路徑和每個函式耗時
#   如果某個函式耗時異常 = kernel 裡的慢點

# 清理
echo nop | sudo tee $TRACE/current_tracer > /dev/null
echo | sudo tee $TRACE/set_graph_function > /dev/null

# 真實場景：
# strace -T 看到 read 慢（5ms）→ 用 ftrace function_graph 看
# read 在 kernel 裡哪個函式花了 5ms（等鎖？等 IO？某慢操作？）
# → 從「syscall 慢」深入到「kernel 裡哪個函式慢」
```

> **ftrace 讓你從「syscall 慢」（strace -T）深入到「kernel 裡哪個函式慢」——這是觀察的最深一層**。當 strace `-T` 顯示「這個 read 花了 5ms」但你想知道**為什麼**，ftrace 的 function_graph 帶你進 kernel 看——trace `vfs_read`（read 在 kernel 的入口），看它在 kernel 裡走了哪些函式、每個花多久。如果某個 kernel 函式耗時異常（如等鎖的函式、等 IO 的函式、某個慢的操作），就是 kernel 裡的慢點。這是觀察的**最深一層**——從使用者空間（strace 看 syscall、perf 看使用者函式）深入到 kernel 內部（ftrace 看 kernel 函式）。實務上，多數 debug 不需要到 kernel 層（strace/perf 就夠定位問題）——但當問題在 kernel（syscall 慢但使用者空間看不出為什麼、kernel 子系統的行為、某個 kernel 操作的耗時）時，ftrace 是工具。它也是理解 kernel 怎麼運作的窗口（看 syscall 在 kernel 裡的真實路徑）。注意 ftrace 主要給「需要看 kernel 內部」的進階場景——一般應用 debug 用前面的工具（strace/perf/valgrind）更直接。但知道「有 ftrace 能看 kernel 內部」，你的觀察能力就沒有死角——從使用者程式到 kernel 內部，都有對應的工具。下一章的 bpftrace 把 kernel 觀察推到「可程式化」的層次，更靈活。

## 動手練習

1. 探索 tracefs：`ls /sys/kernel/tracing/`，看 available_tracers、events/，理解 ftrace 的檔案介面

2. function_graph：用 trace-cmd 或 tracefs trace 一個 syscall（如 vfs_read）的 kernel 路徑

3. 看耗時：在 function_graph 輸出找每個 kernel 函式的耗時，理解「找 kernel 裡的慢點」

4. tracepoint：探索 events/sched/，理解 tracepoint 是「kernel 預設的觀察點」

5. 跑「故意弄壞」：trace 一個 read 的 kernel 路徑，理解「從 syscall 慢深入到 kernel 函式」

## 本章重點整理

- ftrace 是 kernel 內建的 tracer，看 kernel 內部的函式呼叫——補上 strace（syscall 邊界）/perf（使用者函式）看不到的 kernel 層
- 透過 tracefs（/sys/kernel/tracing，寫檔案控制讀檔案看結果）操作——一切皆檔案的又一例
- function_graph 顯示 kernel 函式呼叫圖 + 每個函式耗時——debug「syscall 在 kernel 裡為什麼慢」
- tracepoint 是 kernel 預設的穩定觀察點（sched/block/net），比任意函式穩定有意義，是 bpftrace/eBPF 的基礎
- ftrace 是觀察的最深一層（kernel 內部）；多數 debug 不需到此，但 kernel 問題時是工具

## 自我檢核

- [ ] 理解 ftrace 觀察 kernel 內部，補上前面工具看不到的層
- [ ] 知道 ftrace 透過 tracefs（檔案介面）操作
- [ ] 知道 function_graph 顯示 kernel 函式呼叫圖和耗時
- [ ] 理解 tracepoint 是 kernel 預設的穩定觀察點
- [ ] 知道何時需要 kernel 層觀察（syscall 慢但使用者空間看不出原因）

## 延伸閱讀

### 官方文件

- **[ftrace 文件](https://www.kernel.org/doc/html/latest/trace/ftrace.html)** — Linux kernel docs
  - **讀哪裡**：tracers（function/function_graph）、tracefs 介面
  - **為什麼值得讀**：ftrace 的權威文件

### 文章

- **[ftrace: trace your kernel functions](https://jvns.ca/blog/2017/03/19/getting-started-with-ftrace/)** — Julia Evans
  - **這篇說什麼**：ftrace 入門，怎麼用 tracefs trace kernel 函式
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把 ftrace 講得最易懂的入門

- **[trace-cmd 教學](https://lwn.net/Articles/410200/)** — LWN
  - **這篇說什麼**：trace-cmd（ftrace 前端）的用法
  - **為什麼值得讀**：比直接操作 tracefs 方便的前端工具

### 書籍

- **《Systems Performance》— Ch 14 (ftrace)** — Brendan Gregg
  - **讀哪幾章**：Ch 14（ftrace 完整）
  - **這本書的定位**：ftrace 和 kernel tracing 的權威

下一章看 bpftrace——可程式化的動態 trace，把 kernel 觀察推到「你寫程式決定 trace 什麼」的層次。本課只講 debug 視角（深入留給 bpf 課），但它展示了現代觀察的威力。

→ [Ch 14 bpftrace（debug 視角）](./14-bpftrace-debug-view.md)
