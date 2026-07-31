# 程式行為觀察與 debugging 工具：從 strace 到偵探破案

> 給懂一點 C、想徹底搞懂「程式現在到底在幹嘛」、debug 不再瞎猜的工程師。

這門課把 Linux 上「能看見程式行為」的工具一次教透：strace / ltrace / lsof / perf / valgrind / sanitizers / ftrace / bpftrace。但它不只教「怎麼用工具」——它教你**這些工具底層怎麼運作**：你會親手用 ptrace 寫一個 mini-strace、用 LD_PRELOAD 攔截 library 呼叫、用 ptrace 做 process 注入。讀完你不只會用工具 debug，還理解「為什麼工具看得到這些」，並能在工具不夠時自己造一個。最後用一個藏了 5 個 bug 的壞掉 daemon，逼你綜合整套工具偵探破案。

## 為什麼學這個？

- **debug 能力是工程師的硬實力**：「程式卡住了」「記憶體一直漲」「為什麼這麼慢」「為什麼 segfault」——這些只有「看得見程式行為」的人能解，瞎猜的人只能加 printf
- **理解底層 = 不被工具限制**：知道 strace 怎麼用 ptrace、ltrace 怎麼攔截 PLT、valgrind 怎麼插樁——你就能在工具失效時自己想辦法，甚至自己造工具
- **這是所有系統工作的放大鏡**：後端 debug、效能優化、資安分析、逆向工程——全都靠「觀察程式實際做什麼」。這套工具是你的眼睛
- **職涯角度**：能熟練用 strace/perf/valgrind debug，是區分資深和初級工程師的硬指標，也是面試系統職位的常見考點

## 先修知識

- **C 語言**（程度：會指標、struct、知道 malloc/free、寫過幾個 C 程式；不需要寫過系統程式）
- **一點 OS 概念**（程度：知道 process、知道 syscall 大概是什麼；課程 Ch 2 會把 process/syscall/fd/signal 補完整）
- **Linux 命令列**（程度：會基本操作；建議先有 linux_commands 課的基礎）
- 不需要：debugger 經驗、系統程式設計經驗、組合語言（少數章節有，會解釋）

## 課程地圖

### Part 1 — 基礎與 ptrace（Ch 0–4）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 觀察工具全景](./01-observation-tools-overview.md)
- [Ch 2 process / syscall / fd / signal 模型](./02-process-syscall-fd-model.md)
- [Ch 3 ptrace 深入：debugger 的基礎](./03-ptrace-syscall-deep-dive.md)
- [Ch 4 動手寫 mini-strace v1](./04-mini-strace-v1.md)

### Part 2 — strace 與 ltrace（Ch 5–6）
- [Ch 5 strace 完整指南](./05-strace-complete-guide.md)
- [Ch 6 ltrace 與動態連結](./06-ltrace-and-dynamic-linking.md)
- [練習 A：用 strace 抓 bug](./practice-a-strace-bug-hunt.md)

### Part 3 — 系統狀態觀察（Ch 7–10）
- [Ch 7 /proc 檔案系統導覽](./07-proc-filesystem-tour.md)
- [Ch 8 lsof 與 fd 視角](./08-lsof-and-fd-view.md)
- [Ch 9 ss 與 tcpdump](./09-ss-and-tcpdump.md)
- [Ch 10 sysstat 家族（vmstat/iostat/pidstat/sar）](./10-sysstat-family.md)
- [練習 B：fd 劫持調查](./practice-b-fd-hijack-investigation.md)

### Part 4 — ELF 靜態分析（Ch 11）
- [Ch 11 ELF 靜態檢視（nm/objdump/readelf）](./11-elf-static-inspection.md)

### Part 5 — 現代 tracing（Ch 12–14）
- [Ch 12 perf 基礎](./12-perf-fundamentals.md)
- [Ch 13 ftrace 與 tracefs](./13-ftrace-and-tracefs.md)
- [Ch 14 bpftrace（debug 視角）](./14-bpftrace-debug-view.md)

### Part 6 — 記憶體與正確性（Ch 15–18）
- [Ch 15 valgrind memcheck](./15-valgrind-memcheck.md)
- [Ch 16 valgrind helgrind/drd（並發）](./16-valgrind-helgrind-drd.md)
- [Ch 17 valgrind profiling（callgrind/cachegrind）](./17-valgrind-profiling.md)
- [Ch 18 sanitizers（ASan/TSan/UBSan/MSan）](./18-sanitizers.md)
- [練習 C：多執行緒 bug 獵殺](./practice-c-multithreaded-hunt.md)

### Part 7 — 進階自製工具（Ch 19–21）
- [Ch 19 ptrace 進階：process 注入](./19-ptrace-advanced-injection.md)
- [Ch 20 LD_PRELOAD 攔截器](./20-ld-preload-interceptor.md)
- [Ch 21 core dump 與 signal](./21-coredump-and-signals.md)

### Final Project
- [Final Project：偵探破案 — 修好壞掉的 daemon](./final-project-broken-daemon.md)

## 學習方式建議

1. **每個工具都對著「壞掉的程式」用**：每章配一個故意弄壞的 C 程式，用工具找出問題。看工具的輸出比讀說明書有效一百倍
2. **理解「工具怎麼看到的」**：不只學「strace 顯示 syscall」，而是學「strace 用 ptrace 攔截 syscall」。Part 1 和 Part 7 讓你親手造工具，這是本課和一般工具教學的根本差別
3. **建立「分層觀察」的習慣**：syscall 層（strace）→ library 層（ltrace）→ 系統狀態（/proc/lsof）→ 效能（perf）→ 記憶體（valgrind）。不同問題在不同層觀察
4. **故意製造 bug 再觀察**：寫一個會 leak 的程式用 valgrind 看、寫一個 race 用 helgrind 看、製造 zombie 用 /proc 看——主動製造問題比被動等問題有效

## 精選資料庫

### 必讀基礎

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch, 2010）
  - 本課的底層聖經；process/syscall/fd/signal/ptrace 的權威。理解工具底層必備
- **[man7.org](https://man7.org/linux/man-pages/)** — Michael Kerrisk 維護的 man pages
  - 每個 syscall（ptrace/openat/...）的權威文件；本課反覆指向特定 man page

### 推薦部落格 / 文章

- **[Julia Evans (jvns.ca)](https://jvns.ca/)** — Julia Evans
  - 把 strace、/proc、debug 工具講得最清楚易懂；她的 debugging zine 是本課很多概念的最佳補充
- **[Brendan Gregg's blog](https://www.brendangregg.com/)** — Brendan Gregg
  - 系統效能觀測的權威；perf/ftrace/bpftrace 和「觀測方法論」的延伸，Part 5 必讀

### 推薦書

- **《BPF Performance Tools》** — Brendan Gregg（perf/ftrace/bpftrace 的進階，接 bpf 課）
- **《Systems Performance》** — Brendan Gregg（觀測方法論的權威，把工具放進效能分析的框架）

### 讀完本課之後

- **bpf 課**（把 bpftrace/eBPF 推到極致，kernel 層觀測）
- **gdb / debugger 課**（互動式 debug，本課的 ptrace 知識是 debugger 的底層）

---

> 本課所有指令以 Linux（Ubuntu 22.04+ / Debian 12+）為準，gcc/clang。ptrace/perf 部分需要對應權限（會標注）。每章配可編譯執行的 C 範例。
