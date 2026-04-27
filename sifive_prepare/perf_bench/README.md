# Performance Analysis 與 Benchmarking：從 perf 到 compiler optimization

> 給已經會 C/C++、讀完 `compiler_backend`、想把「效能」變成可以量化討論的能力的工程師。目標是看到 benchmark 數字能解讀、看到 hot function 能提出 compiler-level 改進、能跟硬體團隊討論 IPC 跟 cache behavior。

這是 SiFive job spec 第二條 responsibility 的對口課程：**"Work with SiFive's benchmarking teams to analyze performance results and suggest new compiler optimizations."** 不會分析效能，你無法 suggest optimization；不會 benchmark，你無法 validate 改進。

## 為什麼學這個？

- **效能討論需要工具 + 語言**：「這段 code 很慢」沒用。「這個 function 的 IPC 0.3、L2 miss rate 40%」有用。
- **Compiler optimization 的反饋迴路**：改 compiler 後要怎麼驗證有效？怎麼量化？這是 SiFive 工程師每天要做的。
- **SiFive 的 core 要跑 SPEC / Coremark / Embench**：這些 benchmark 的結構不懂，就沒法跟 benchmarking team 對話。
- **業界迷思多**：「-O3 比 -O2 快」「LTO 總是有用」「AVX/RVV 自動 vectorize 就快」—— 大多是錯的。這課讓你有能力自己驗證。

## 本課與前幾門的關係

```
riscv           (ISA)
     ↓
elf_linking     (binary format)
     ↓
compiler_backend (compiler 如何產出 code)
     ↓
perf_bench     (如何測 compiler 產出的 code 是否夠快)  ← 你在這裡
     ↓
yocto          (如何把好 code 部署)
```

**建議先讀完 Ch 13 of `compiler_backend`（scheduling model）**。本課很多 chapter 直接對應 compiler 層的概念。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建：perf / llvm-mca / valgrind / 其他](./00-environment-setup.md)

### Part 1 — Benchmark 哲學
- [Ch 1 Micro vs macro benchmark：選哪個、怎麼避免錯誤](./01-micro-vs-macro.md)
- [Ch 2 SPEC CPU：業界 benchmark 之王](./02-spec-cpu.md)
- [Ch 3 Coremark / Embench：RISC-V 與嵌入式主力](./03-coremark-embench.md)
- [Ch 4 統計基本功：geomean、CI、noise 控制](./04-statistical-basics.md)

### Part 2 — 硬體事件與效能計數器
- [Ch 5 CPU 微架構速成：pipeline / OoO / ROB / cache](./05-microarch-primer.md)
- [Ch 6 Performance events：IPC / cache miss / branch miss](./06-performance-events.md)

### Part 3 — Profiling 工具
- [Ch 7 perf record / perf report 實戰](./07-perf-tool.md)
- [Ch 8 llvm-mca：靜態分析 throughput / bottleneck](./08-llvm-mca.md)
- [Ch 9 Flame graph 與 on-CPU profiling](./09-flamegraph.md)

### Part 4 — Compiler-centric 效能分析
- [Ch 10 Compiler flag scan：-O2 vs -O3 真相、-march 選擇](./10-compiler-flags.md)
- [Ch 11 PGO / BOLT / Propeller：profile-guided 全家族](./11-pgo-bolt-propeller.md)
- [Ch 12 LTO 效果量測](./12-lto-measurement.md)
- [Ch 13 Vectorization report 閱讀](./13-vectorization-reports.md)
- [Ch 14 從 hot loop 倒推「該加什麼 optimization」](./14-hot-loop-thinking.md)

### Part 5 — 實戰
- [練習 A：寫一份 Coremark 效能報告](./practice-a-coremark-report.md)
- [練習 B：用 llvm-mca 分析一段 hot loop](./practice-b-llvm-mca-case.md)
- [Final Project：Performance case study + compiler optimization proposal](./final-project-perf-case-study.md)

## 學習方式建議

1. **實測取代空談**：這門課最忌「讀書不跑 benchmark」。每章都要動手。
2. **用真 hardware 或 QEMU**：如果有 RISC-V 硬體（VisionFive 2 / LicheePi 4A / SiFive HiFive Unmatched 等）最好。沒有就 qemu-system + 加 `-cpu rv64,+v` 等。
3. **小 benchmark 循環**：改 code → 測 → 分析 → 改 → 測。幾十次 iteration 才磨出 sensitivity。
4. **保留實驗 log**：每次測量結果記錄（日期、machine、compiler version、flag、數字）。三個月後回來看才知道是進是退。
5. **讀 benchmarking 論文/報告**：Agner Fog 的 x86 manual、Brendan Gregg 的 perf 書、SiFive 公開的 P870 白皮書等。

## 本課不涵蓋什麼

- **Full SPEC benchmark run**：跑一次要幾小時、license 也要錢。示範 `-noreportable` 快跑 + 方法論。
- **GPU profiling**：CUDA / Metal 另一個大世界，不碰。
- **網路 / I/O 瓶頸**：專注 CPU-bound 情境。database / web 另有方法論。
- **Power / energy analysis**：相關但獨立。嵌入式的話可選看 Ch 5 輕觸。
- **DTrace / eBPF 深度**：`bpf` 有 cover，這裡不重複。

## 參考資料

**書：**
- 《Systems Performance》— Brendan Gregg（全面、實用）
- 《Every Computer Performance Book》— Bob Wescott
- 《Computer Architecture: A Quantitative Approach》— Hennessy & Patterson
- 《Software Optimization Resources》— Agner Fog 的 x86 系列（免費）

**工具文件：**
- `perf(1)` manual：Linux 最強工具
- `llvm-mca(1)` manual：LLVM 的 machine code analyzer
- SPEC CPU 官方：<https://www.spec.org/cpu2017/>
- Coremark 官方：<https://www.eembc.org/coremark/>
- Embench：<https://www.embench.org/>

**Blog / 社群：**
- Brendan Gregg's blog：<https://www.brendangregg.com/>
- Agner Fog：<https://www.agner.org/optimize/>
- LLVM Dev Meeting "Performance" talks
- Denis Bakhvalov's 《Performance Analysis and Tuning on Modern CPUs》（免費 PDF）

**benchmark codebases：**
- <https://github.com/eembc/coremark>
- <https://github.com/embench/embench-iot>
- <https://github.com/riscv-collab/riscv-benchmarks>
