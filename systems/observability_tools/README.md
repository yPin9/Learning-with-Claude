# 程式行為觀察與 debugging 工具學習筆記：從 strace 到偵探破案

> 給會 C 語言、想徹底理解程式在跑什麼、debug bug 不再瞎猜的工程師。

這系列把 Linux 上「能看到程式行為」的工具一次教完：strace / ltrace / lsof / perf / valgrind / sanitizers / ftrace / bpftrace，配上自己用 ptrace 寫 mini-strace、用 LD_PRELOAD 攔截 library。讀完你會知道「這隻程式現在在幹嘛」這個問題從哪裡找答案。

## 為什麼學這個？

- **printf debug 的盡頭**：當 printf 印不到、stack 看不出來、bug 偶爾出現，這些工具是唯一出路
- **理解 library / kernel 邊界**：strace 看 syscall、ltrace 看 libc，你會看清自己的程式跟 OS 的對話
- **抓「不該發生卻發生」的 bug**：race condition、UAF、leak、被偷的 fd —— 全部有對應工具
- **看大型開源軟體**：要 patch nginx / postgres / kubelet，沒這套你連啟動流程都追不出來

## 課程地圖

### Part 1 — 基礎與 ptrace 核心
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 觀察工具全景](./01-observation-tools-overview.md)
- [Ch 2 process / syscall / signal / fd 模型](./02-process-syscall-fd-model.md)
- [Ch 3 ptrace(2) 完整剖析](./03-ptrace-syscall-deep-dive.md)
- [Ch 4 動手：mini-strace v1](./04-mini-strace-v1.md)

### Part 2 — strace / ltrace
- [Ch 5 strace 完整指南](./05-strace-complete-guide.md)
- [Ch 6 ltrace 與動態連結](./06-ltrace-and-dynamic-linking.md)
- [練習 A：用 strace 抓真實 bug](./practice-a-strace-bug-hunt.md)

### Part 3 — Process / File / Network 觀察
- [Ch 7 /proc 完整漫遊](./07-proc-filesystem-tour.md)
- [Ch 8 lsof 與 fd 視角](./08-lsof-and-fd-view.md)
- [Ch 9 ss / tcpdump — 網路觀察](./09-ss-and-tcpdump.md)
- [Ch 10 sysstat 家族](./10-sysstat-family.md)
- [練習 B：fd 劫持事件調查](./practice-b-fd-hijack-investigation.md)

### Part 4 — 靜態檢視
- [Ch 11 ELF 靜態檢視 (nm / readelf / objdump / addr2line / ldd)](./11-elf-static-inspection.md)

### Part 5 — Performance 與 modern tracing
- [Ch 12 perf 基礎](./12-perf-fundamentals.md)
- [Ch 13 ftrace / tracefs](./13-ftrace-and-tracefs.md)
- [Ch 14 bpftrace 從 debug 角度](./14-bpftrace-debug-view.md)

### Part 6 — Memory 與 correctness
- [Ch 15 valgrind memcheck](./15-valgrind-memcheck.md)
- [Ch 16 valgrind helgrind / drd](./16-valgrind-helgrind-drd.md)
- [Ch 17 valgrind callgrind / massif / cachegrind](./17-valgrind-profiling.md)
- [Ch 18 Sanitizers (ASan / UBSan / TSan / MSan)](./18-sanitizers.md)
- [練習 C：multithreaded race + leak hunt](./practice-c-multithreaded-hunt.md)

### Part 7 — 進階：自製工具
- [Ch 19 ptrace 進階：注入與 register 操作](./19-ptrace-advanced-injection.md)
- [Ch 20 動手:LD_PRELOAD interceptor](./20-ld-preload-interceptor.md)
- [Ch 21 core dump 與 signal trap](./21-coredump-and-signals.md)

### Part 8 — 整合
- [Final Project：偵探破案](./final-project-broken-daemon.md)

## 學習方式建議

1. **每個工具寫一支故意有 bug 的 C 小程式**：bug 自己藏的，你才會記住工具怎麼挖出來
2. **同一個 bug 用不同工具看一次**：例如 leak 既能用 valgrind 也能用 ASan，對照速度跟訊息差異
3. **遇到任何詭異 bug 第一個動作：`strace -f -e trace=...`**：90% 的線索在這
4. **不要只跑工具看輸出**：訓練自己**先預測**會看到什麼，再跑，對不上的地方就是知識缺口

## 參考資料

- 《Brendan Gregg, Systems Performance》— perf / ftrace / observability 的聖經
- Julia Evans 的 zine "How to Debug" / "strace zine" — 短小精悍
- `strace(1)`、`ptrace(2)`、`valgrind(1)` 的 manual page — 不要跳過 SEE ALSO
- Brendan Gregg 的 Linux observability tools 海報：http://www.brendangregg.com/linuxperf.html
