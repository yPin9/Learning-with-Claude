# Ch 3 — 傳統 kernel 觀測手段：printk、ftrace、perf、strace

> 目標：BPF 出現之前，人們怎麼觀察 kernel？認識 printk / strace / ftrace / perf 各自能做什麼、痛在哪。學完這章，BPF 提供的價值會顯得理所當然。

## 為什麼要學這些「舊工具」？

這章看似偏題 — 你不是來學 BPF 嗎，幹嘛學一堆 BPF 之前的東西？

兩個理由：

1. **這些工具今天還大量在用**。strace 還是 debug syscall 第一招、perf 還是效能分析的瑞士刀。BPF 沒有取代它們，只是補了它們做不到的地方。
2. **你會建立「對比直覺」**。看到 BPF 的 ringbuf、看到 verifier、看到 kprobe — 你會立刻明白「啊這是在解某某舊工具的某某痛點」。沒有對比，學 BPF 會像學一堆無動機的 API。

## 工具全景

| 工具 | 觀察什麼 | 機制 | 開銷 | 主要痛點 |
|---|---|---|---|---|
| **printk** | kernel 自己印的 log | kernel 內 buffer | 極低 | 只能看別人寫的、不能加自己的 |
| **strace** | 一個 process 的 syscall | `ptrace()` 中斷 | **極高（10–100x）** | 慢到不能上 production |
| **ltrace** | 一個 process 的 lib call | 同 ptrace | 極高 | 同上 |
| **ftrace** | kernel function 進出 | kernel 內建 tracer | 中（但有限制） | 純文字介面、彙總難 |
| **perf** | hw counter + 多種 event | `perf_event_open()` syscall | 低（取決用法） | 設計給「先採樣再分析」 |
| **SystemTap** | kernel 任意點 + 自定 logic | 編譯成 kernel module | 低 | **要編 kernel module**（生產禁忌） |

接下來逐個拆。

## printk — 最古老的調試手段

`printk()` 是 kernel 內的 `printf` — 寫到 kernel ring buffer，user space 用 `dmesg` 讀：

```bash
sudo dmesg | tail
# [42891.123] usb 1-2: new high-speed USB device number 5
# [42891.456] usb 1-2: New USB device found
```

如果你想在 kernel module 裡印自己的訊息：

```c
printk(KERN_INFO "my_module: got value=%d\n", x);
```

**痛點**：

- 只能由 kernel code 自己呼叫 — **你看不到「OS 沒主動印的東西」**。
- 沒有 filter / 聚合 — 全部丟到一個全域 buffer，洗版很快。
- 想加自己的 printk 必須改 kernel 或寫 module。

printk 的角色就是「kernel 的 syslog」 — 用來看驅動、看 oops，不是用來做 observability。

## strace — 看 syscall 但慢

最常用的工具，每個工程師都會：

```bash
strace -e trace=openat,read,write cat /etc/hostname
```

輸出每一個 syscall、參數、回傳值：

```
openat(AT_FDCWD, "/etc/hostname", O_RDONLY) = 3
read(3, "myhost\n", 131072)              = 7
write(1, "myhost\n", 7)                  = 7
close(3)                                 = 0
```

但 strace 是用 `ptrace()` 實作的 — **每次 syscall 進、出，都會中斷目標 process 兩次**，把 register 複製給 strace 看。後果就是：

```bash
# 沒 strace
time grep -r "x" /usr/include > /dev/null
# real    0m0.183s

# 套上 strace
time strace -c grep -r "x" /usr/include > /dev/null
# real    0m6.421s        ← 慢 35 倍
```

**這就是 strace 不能上 production 的根本原因**。如果你的 production server 有效能問題、想知道它在做什麼 syscall — 套上 strace 就先讓它慢 30 倍，可能直接觸發 timeout、健康檢查掛掉、自動重啟。

BPF 後來提供 `execsnoop` / `opensnoop` 等替代品，**開銷低於 1%**、可以一直開著。這是革命性的差別。

## ftrace — kernel 內建的 function tracer

ftrace 是 kernel 自己內建的 tracer，可以追任意 kernel function 的進出。介面是 `/sys/kernel/tracing/`：

```bash
cd /sys/kernel/tracing/
echo function > current_tracer
echo do_sys_openat2 > set_ftrace_filter
echo 1 > tracing_on

cat trace | head
# bash-12345  [001]  9876.123: do_sys_openat2 <-__x64_sys_openat
# cat -23456  [003]  9876.234: do_sys_openat2 <-__x64_sys_openat

echo 0 > tracing_on
echo > set_ftrace_filter
```

ftrace 還有 `function_graph` tracer，會印出 kernel 函式的呼叫樹，效果像 dtrace。

也提供 **trace_printk**（在 kernel code 裡寫的 printf 替代品，比 printk 快、會進 ftrace buffer）。

**痛點**：

- **介面是檔案系統，不適合複雜邏輯**。要做「呼叫超過 100ms 才印」這種條件，得另外寫 user space tool 來解析 trace 輸出 — 慢、難寫、容易漏。
- **彙總統計困難**。要做「哪個 function 平均耗時最高」要把全部 raw event dump 出來再算 — 開銷不低。
- **沒有 maps**。每個 event 都是獨立的，無法跨 event 維護狀態（如「記住每個 PID 上次進來的時間」）。

ftrace 後來變成 BPF 的好夥伴 — 大多 BPF tracing program 其實是 attach 到 ftrace 提供的 tracepoint / kprobe 機制上。BPF 沒有取代 ftrace，而是**用 ftrace 當底層、把 user 邏輯做成 sandbox**。

## perf — 效能分析的瑞士刀

`perf` 的兩大主軸：**performance counters**（CPU 硬體計數器）與 **events**（軟體事件 + tracepoint）。

### Counter mode

```bash
perf stat -e cycles,instructions,cache-misses,cache-references ls /tmp
#  Performance counter stats for 'ls /tmp':
#         5,234,891      cycles
#         9,876,543      instructions       #    1.89  insn per cycle
#            12,345      cache-misses       #   23.4% of all cache refs
#            52,789      cache-references
```

讀 hw counter — 開銷接近零，因為這是 CPU 硬體在計數。

### Sampling mode

```bash
sudo perf record -F 99 -g ./my-program
sudo perf report
```

每秒中斷 99 次、抓 stack trace、累積成 profile。**做 flamegraph 的傳統方式**：

```bash
sudo perf record -F 99 -g -- sleep 30
sudo perf script | stackcollapse-perf.pl | flamegraph.pl > out.svg
```

### 追 syscall

```bash
sudo perf trace -p $(pgrep nginx)
# 比 strace 快很多 — 因為它走 perf_event 而非 ptrace
```

**痛點**：

- **設計是「先採樣再離線分析」**。想做 streaming 處理（即時看到事件）較尷尬。
- **過濾只能用簡單的 expression**。要做「path 含某字串才印」這種條件靠 perf 沒辦法。
- **彙總要靠 user space 工具**。perf 自己不在 kernel 裡聚合資料。

很多人把 perf 跟 BPF 對立起來看 — 其實 **perf 是 BPF 的好朋友**：BPF 可以 attach 到 `perf_event`、可以送資料到 perf ring buffer，許多現代 BPF profiler（如 Parca）就是用 perf event 做 sampling、用 BPF 做 in-kernel 聚合。

## SystemTap — 一個雄心壯志的失敗

2005 年 Red Hat 推 SystemTap (`stap`)，目標是「Linux 上的 dtrace」。寫 `.stp` 腳本：

```stap
probe syscall.open {
    printf("%s opened %s\n", execname(), filename)
}
```

看起來跟 bpftrace 很像對吧 — 因為 bpftrace 就是受它啟發的。但 SystemTap 有個致命缺陷：

**它把 `.stp` 編譯成 kernel module、然後 `insmod` 進去跑**。

意思是：

- 寫錯一行 → 直接 panic kernel
- 上 production 要簽核（誰准你裝 module？）
- 跨 kernel 版本要重編
- 啟動慢（要 compile + insmod，幾十秒起跳）

SystemTap 的設計師當時做了「對的事」 — 提供 high-level DSL — 但**選錯了實作底層**。BPF 出來之後 SystemTap 基本被取代，這也是為什麼**「verifier + sandbox」這個設計選擇是 BPF 成功的關鍵**。

## 共同的痛點，BPF 怎麼解

把上述工具的痛點整理成一張表，看 BPF 各自怎麼破：

| 痛點 | 傳統工具 | BPF 的解法 |
|---|---|---|
| 開銷高、不能上 prod | strace, ltrace | kprobe + bpf_printk + ringbuf，開銷 <1% |
| 不能寫自定邏輯 | printk, ftrace 部分 | 任意 C code（受 verifier 限制） |
| 不能跨 event 維護狀態 | ftrace, perf | maps |
| 要寫 / 重編 kernel module | SystemTap | verifier 沙盒，動態載入 |
| 跨 kernel 版本痛苦 | 全部 | BTF + CO-RE |
| 介面笨拙 | ftrace 檔案系統 | 程式化 API（libbpf、cilium/ebpf） |

每一行都對應教材後面 1–2 章的內容。

## 一個常見誤解

「BPF 取代了 strace、ftrace、perf」 — **不全然**。

- **strace 還是最快的「快速掃一個程式做了什麼」的工具**。對個人 debug、cold path、unknown 行為，strace 仍是首選。
- **ftrace 是 BPF 的底層之一**。kprobe / tracepoint / fentry 全部是 ftrace 提供的 hook 點。
- **perf 是 BPF 的兄弟**。BPF profile 也是用 `perf_event_open` 拿 sampling event。

正確的 mental model：**BPF 是新的「上層」，下面還是要靠 ftrace/perf 提供 hook 與 sampling 機制**。它們不是對手，是共生。

## 動手練習

1. **量化 strace 的開銷**：選一個重 IO 的指令（`grep -r "x" /usr/include`），分別 `time` 跑、`time strace -c` 跑、`time perf trace` 跑，比三組數據。
2. **玩一次 ftrace**：照上面的步驟手動 enable function tracer、追 `do_sys_openat2`，觀察 raw 輸出，**體會「沒辦法做條件聚合」是多麻煩**。
3. **跑一次 perf flamegraph**：找一個 CPU-bound 程式（隨便寫個 busy loop），用 perf record + flamegraph 工具產生 SVG。Ch 18 會用 BPF 重現一次，到時你可以對照。

## 自我檢核

- [ ] 我能說出 strace 為什麼慢、為什麼不能上 production
- [ ] 我能說出 ftrace 與 BPF 的關係（不是對手，是底層）
- [ ] 我能說出 SystemTap 為什麼失敗、BPF 用什麼設計避開了那個坑
- [ ] 我能講出 perf 的兩大主軸（counter / event）
- [ ] 我能用一張表整理「傳統工具痛點 → BPF 解法」

下一章我們來看 BPF 程式「掛在哪裡」 — kprobe、uprobe、tracepoint、fentry 這四種 kernel hook 機制是 BPF 的「附著點」，每一種都有自己的開銷、穩定度、能拿到的資訊。

→ [Ch 4 Kernel 鉤子機制：kprobe / uprobe / tracepoint / fentry](./04-kernel-hooks.md)
