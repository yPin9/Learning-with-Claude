# Ch 0 — 環境搭建：perf / llvm-mca / valgrind / 其他

> 目標：裝齊 performance analysis 所需的工具、理解每個工具的分工、用一個簡單範例驗證整套環境。

## 工具地圖

效能工具分四類：

### 1. Profiler（動態、量執行時 behavior）

| 工具 | 角色 | 何時用 |
|---|---|---|
| **perf** | Linux 最強 profiler | 主力 |
| **valgrind (callgrind)** | 指令級 simulation | 無硬體 counter 時 |
| **gprof** | 老派 profiler | 傳統 code、簡單場景 |
| **perf script** / **eBPF** | advanced trace | 複雜 scenario |

### 2. Static analyzer（靜態、不跑）

| 工具 | 角色 | 何時用 |
|---|---|---|
| **llvm-mca** | LLVM 的 pipeline analyzer | 分析一段 hot code 的 throughput |
| **IACA** | Intel 出品（已 deprecated） | x86 微指令分析 |

### 3. Benchmark harness

| 工具 | 角色 |
|---|---|
| **SPEC CPU2017** | 標準 benchmark suite（商用）|
| **Coremark** | 免費、embedded 愛用 |
| **Embench** | RISC-V 社群主推 |
| **Google benchmark** | C++ micro-benchmark framework |
| **hyperfine** | CLI 時間量測 |

### 4. 視覺化

| 工具 | 角色 |
|---|---|
| **FlameGraph** | 視覺化 on-CPU profile |
| **kcachegrind** | callgrind 的 GUI |
| **hotspot** | perf 的 GUI（KDE）|
| **Perfetto** | Google 出的 trace 視覺化 |

本課主力：**perf + llvm-mca + FlameGraph**。

## 安裝

### Ubuntu 24.04

```bash
sudo apt update
sudo apt install -y \
    linux-tools-common linux-tools-$(uname -r) \
    valgrind \
    llvm-18 \
    cpuid \
    hyperfine \
    time \
    sysstat \
    gnuplot

# FlameGraph
git clone https://github.com/brendangregg/FlameGraph
export PATH=$PWD/FlameGraph:$PATH
```

### Fedora

```bash
sudo dnf install -y perf valgrind llvm hyperfine
```

### macOS

macOS 沒 perf（用 Instruments 或 DTrace）。建議 Linux VM 或 cloud 做這門課。

## 驗證

```bash
perf --version             # Linux perf
llvm-mca --version          # LLVM MCA
valgrind --version
hyperfine --version
```

全部要有輸出。

## perf 第一次跑：常見權限問題

```bash
perf stat ls
```

可能出現：

```
Error: You may not have permission to collect stats.
Consider tweaking /proc/sys/kernel/perf_event_paranoid
```

解法（臨時）：

```bash
sudo sysctl kernel.perf_event_paranoid=-1
sudo sysctl kernel.kptr_restrict=0
```

永久修改：編輯 `/etc/sysctl.d/99-perf.conf`：

```
kernel.perf_event_paranoid=-1
kernel.kptr_restrict=0
```

**這會降低 security**（允許 unprivileged user 讀 kernel addr）。**生產機別動**，dev 機可以。

## 第一個範例：perf stat

```bash
echo "int main(){ long s=0; for(long i=0;i<1000000000;i++) s+=i; return s; }" > loop.c
gcc -O2 loop.c -o loop
perf stat ./loop
```

輸出：

```
 Performance counter stats for './loop':

              0.52 msec task-clock                #    0.819 CPUs utilized
                 0      context-switches          #    0.000 /sec
                 0      cpu-migrations            #    0.000 /sec
                45      page-faults               #   86.610 K/sec
         2,079,385      cycles                    #    4.003 GHz
         1,024,321      instructions              #    0.49  insn per cycle
           204,213      branches                  #  393.115 M/sec
             8,212      branch-misses             #    4.02% of all branches

       0.000634221 seconds time elapsed

       0.000627000 seconds user
       0.000000000 seconds sys
```

**wait, only 1M instructions for a 1G-iteration loop?** → compiler optimized the loop away (`-O2`). 

改 `volatile`：

```c
// loop.c
int main() {
    volatile long s = 0;
    for (long i = 0; i < 1000000000; i++) s += i;
    return s;
}
```

重跑：

```bash
gcc -O2 loop.c -o loop
perf stat ./loop
```

現在會看到 ~3 billion instructions（每 iter 3 條）、IPC ≈ 3。

**這是 benchmark 的第一課：知道 compiler 在幹嘛、別量測被優化掉的東西**。

## 第一個範例：perf record + perf report

```bash
perf record -F 997 -g ./loop
perf report
```

`-F 997`: sampling frequency 997Hz（避開常用頻率減少 sampling bias）
`-g`: 收 call graph

`perf report` 進 TUI 看哪個 function 吃 CPU。

退出：`q`。

## 第一個範例：llvm-mca

`llvm-mca` 吃一段 asm、模擬 pipeline、算 throughput：

```bash
echo "
.text
.globl main
main:
    mov \$0, %rax
    mov \$1000, %rcx
loop:
    add %rcx, %rax
    dec %rcx
    jnz loop
    ret
" > loop.s

llvm-mca -mcpu=skylake loop.s
```

輸出：

```
Iterations:        100
Instructions:      400
Total Cycles:      403
Total uOps:        500

Dispatch Width:    6
uOps Per Cycle:    1.24
IPC:               0.99
```

每 iter 4 條指令、4 個 cycle → IPC ~1。對 skylake（4-wide）來說非最佳。

RISC-V 版：

```bash
echo "
loop:
    add a0, a0, a1
    addi a1, a1, -1
    bnez a1, loop
    ret
" > loop.s

llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 loop.s
```

## FlameGraph 第一次

```bash
perf record -F 997 -g ./loop
perf script > out.perf
stackcollapse-perf.pl out.perf > out.folded
flamegraph.pl out.folded > flame.svg
```

`flame.svg` 在瀏覽器打開。寬條 = 熱 function。

## Valgrind + callgrind

```bash
valgrind --tool=callgrind ./loop
callgrind_annotate callgrind.out.*
```

慢（valgrind 是 sim）但**不需要硬體 counter**。VM / container 裡 perf 不 work 時 valgrind 救場。

## Hyperfine：快速量時間

```bash
hyperfine --warmup 3 --runs 10 './loop'
```

輸出：

```
Benchmark 1: ./loop
  Time (mean ± σ):     621.3 ms ±   7.8 ms    [User: 618.2 ms, System: 2.1 ms]
  Range (min … max):   613.4 ms … 638.1 ms    10 runs
```

含平均、標準差、min/max。**比 `time` 嚴謹**。Ch 4 會細講統計。

## 對跨平台 benchmark 建議

如果你 host 是 x86、target 是 RISC-V：

### 方案 A: QEMU-user + perf

```bash
qemu-riscv64 -cpu rv64 ./hello_rv64
```

**perf 不能用 QEMU-user 上**（需要硬體 counter）。用 `time` 或 callgrind 替代。

### 方案 B: QEMU-system + perf

```bash
qemu-system-riscv64 -cpu rv64 -kernel ... -append "..."
# 進系統後用 perf
```

**perf 在 QEMU-system 可能 work 但 counter 不準**（被 emulation 污染）。數據參考即可。

### 方案 C: 真機

VisionFive 2 / LicheePi 4A / HiFive Unmatched / QEMU + TCG（有些 PMU 支援）。

**SiFive 工程師日常有硬體 dev board**。個人學習階段 QEMU 夠用但數字別信絕對值、看 trend。

## 工具鏈交互圖

```
C code
 │
 ├─► gcc/clang -O2 -g
 │     │
 │     ▼
 │   binary
 │     │
 │     ├─► perf record / stat       ← dynamic
 │     ├─► valgrind                 ← dynamic sim
 │     ├─► hyperfine                ← wall time
 │     └─► objdump -d → llvm-mca   ← static
 │
 └─► gcc -fopt-info-vec            ← compiler optimization report
```

## 常見誤會

1. **「perf 一定要 root」**：設 `perf_event_paranoid` 後 user mode 可用。
2. **「llvm-mca 量時間」**：不量 wall-clock、算 cycle（理論值）。
3. **「一次測量就準」**：絕對不。多次測 + 統計。Ch 4 專講。
4. **「`time ./prog` 就夠」**：對快 binary 不準（clock resolution）。用 hyperfine 或 loop 放大。
5. **「編 -O2 跟 -O3 差不多」**：大部分對，但某些 benchmark 差 10%+。自己測。

## 動手練習

1. 裝齊工具、跑通 `perf stat ./loop`。
2. 故意讓 code 優化掉 vs 不優化，對比 perf 數字差異。
3. 用 llvm-mca 分析一小段 `.s`，算每個 iter 的 IPC。
4. 用 hyperfine 比 `-O0` vs `-O2` 的 loop.c 執行時間。
5. 產生一張 FlameGraph，瀏覽器看。

## 自我檢核

- [ ] 我裝好 perf + llvm-mca + FlameGraph + hyperfine
- [ ] 我能跑 `perf stat` 看 IPC / cache miss
- [ ] 我能解釋 `perf_event_paranoid` 權限問題
- [ ] 我知道 dynamic vs static vs simulator 三類工具差異
- [ ] 我能區分 host perf 跟 QEMU perf 的 reliability

下一章進入 benchmark 哲學 — micro vs macro 選擇，這是避免踩雷的起點。

→ [Ch 1 Micro vs macro benchmark](./01-micro-vs-macro.md)
