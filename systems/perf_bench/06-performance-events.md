# Ch 6 — Performance events：IPC / cache miss / branch miss

> 目標：認識硬體 performance counter 的事件清單、知道每個事件意義、能組合成 higher-level metric（IPC、cache hit rate、branch miss rate）。

## PMU 是什麼

**PMU (Performance Monitoring Unit)**：CPU 內建的計數器。一組硬體 counter 每發生特定事件就 +1。

```
cycles                 計 cycle 總數
instructions           計 retired 指令數
cache-misses           計 cache miss
branch-misses          計 branch misprediction
...
```

Linux kernel 把 PMU 抽象成 `perf_event_open` syscall。`perf` 工具是 user-space wrapper。

每 CPU 有固定數量的 counter（典型 4-8 個 general-purpose + 幾個 fixed）。同時量超過 → `perf` 自動 time-multiplex（你的 1 秒量測，前半 event A、後半 event B），counter 顯示比例。

## 核心事件

所有 platform 共通：

```
cycles               CPU cycle 總數
instructions         retired 指令數
branches             branch 指令數
branch-misses        branch mispredict 數
cache-references     cache access 數（通常 last-level cache）
cache-misses         last-level cache miss 數
```

組合：

```
IPC              = instructions / cycles
branch miss rate = branch-misses / branches
cache miss rate  = cache-misses / cache-references
```

`perf stat` 預設就印這些。

## 更細的 cache events

各層 cache 個別量：

```
L1-dcache-loads              L1 data cache load 總數
L1-dcache-load-misses        L1 data cache load miss
L1-dcache-stores             L1 data store
L1-icache-loads              L1 instruction cache access
L1-icache-load-misses        L1 I-cache miss

LLC-loads                    Last-level cache loads
LLC-load-misses              LLC miss

dTLB-loads / dTLB-load-misses
iTLB-loads / iTLB-load-misses
```

不同 CPU 的具體 event 名字略有差。`perf list` 看 support 清單。

## RISC-V 的 performance counter

RISC-V spec 定義：

- `cycle` CSR：cycle counter
- `instret` CSR：retired 指令數
- `time` CSR：wall time
- `hpmcounter3-31`：可 programmable counter（event 依實作）

用 `csrr` 指令讀：

```asm
csrr t0, cycle
csrr t1, instret
```

Linux perf 在 RISC-V 的支援 2024+ 成熟。典型 `perf stat` event：

- `cycles` / `instructions`: 用 CSR
- `cache-misses`: 用 hpmcounter (SiFive U74 有專屬 event ID)
- `branch-misses`: 同上

不同 SiFive core 支援的 event 不同。`perf list` 查 target 有什麼。

## `perf stat` 基本

```bash
perf stat ./program
```

輸出：

```
 Performance counter stats for './program':

        623.12 msec task-clock                #    0.999 CPUs utilized
             2      context-switches         #    3.210 /sec
             0      cpu-migrations           #    0.000 /sec
            45      page-faults              #   72.219 /sec
 1,870,234,120      cycles                   #    3.001 GHz
 4,523,412,005      instructions             #    2.42  insn per cycle
   842,123,442      branches                 #  1.351 G/sec
    12,345,234      branch-misses            #    1.47% of all branches

   0.623487341 seconds time elapsed
```

**注意 IPC = 2.42**。對一般 workload 這是好數字。

## 自訂 event 組合

```bash
perf stat -e cycles,instructions,L1-dcache-load-misses,branch-misses ./program
```

用逗號分隔。event 數 > counter 數 → multiplexing。

## Counting mode vs Sampling mode

兩種用 PMU 的方式：

### Counting (`perf stat`)

單純數總和。精確、overhead 小。不知道 event 發生在哪。

### Sampling (`perf record`)

每 N event 中斷一次、記下當時 PC + callstack。可以知道 hot code 在哪、但有 sampling bias。

Ch 7 專講 perf record。

## Important ratio 解讀

### IPC

```
IPC > 3: Great, compute-bound 且 parallel 好
IPC 1-3: Normal
IPC 0.5-1: 可能 memory bound
IPC < 0.5: 嚴重 memory 或 branch issue
```

### Branch miss rate

```
< 1%: Great
1-5%: Typical
> 10%: Branch-heavy data-dependent code, 可能要 cmov
```

### L1-D cache miss rate

```
< 5%: Great (in working set)
5-20%: Typical
> 50%: Memory bound, cache-unfriendly
```

### LLC miss rate

```
<10%: Great
10-50%: 工作 set 接近 LLC 容量
>50%: DRAM-bound，很可能是 memory bandwidth bottleneck
```

## IPC 低的 diagnosis flow

IPC 0.5 → 你要找原因。步驟：

```
1. L1-D miss rate 高？   → memory latency
2. LLC miss rate 高？    → DRAM bound
3. Branch miss 高？     → branch pattern
4. 都還好？              → maybe long-latency instruction (div, FPU)
```

每個 bottleneck 對應不同優化。

## 硬體 vs 軟體 event

```bash
perf list | head -40
```

```
cache-misses                                       [Hardware event]
cache-references                                   [Hardware event]
...
context-switches                                   [Software event]
page-faults                                        [Software event]
migrations                                         [Software event]
...
```

- **Hardware**：PMU 直接量、精確
- **Software**：kernel 邏輯 count、如 context switch
- **Tracepoint**：kernel 內部 event（I/O、syscall）

## Top-down methodology

Intel 推的框架（AMD 跟進，RISC-V 社群在 adopt）。把 pipeline slots 分類：

```
-- Pipeline slot usage --
Retiring          Do useful work
Bad Speculation   Speculation wasted
Front-End Bound   fetch 不到指令
Back-End Bound    execute 卡住
```

Intel CPU 上：

```bash
perf stat -M TopdownL1 ./program
```

會直接印這 4 個 ratio。Hot optimization target：

- Retiring 高 → 不用改
- BE Bound 高 → 優化 memory / instruction latency
- FE Bound 高 → 優化 branch / I-cache
- Bad Spec 高 → 優化 branch pattern

## 實例：分析 memcpy

```c
void mymemcpy(void *dst, const void *src, size_t n) {
    memcpy(dst, src, n);
}

int main() {
    char *a = malloc(100*1024*1024);   // 100 MB
    char *b = malloc(100*1024*1024);
    for (int i = 0; i < 10; i++)
        mymemcpy(a, b, 100*1024*1024);
    return 0;
}
```

```bash
perf stat -e cycles,instructions,L1-dcache-load-misses,LLC-load-misses ./a.out
```

可能看到：

```
cycles              5,000,000,000
instructions         2,000,000,000      (IPC 0.4)
L1-dcache-load-misses 95,000,000        (massive, 100MB working set)
LLC-load-misses       45,000,000        (also high)
```

**Diagnosis**：memory bandwidth bound、IPC 低是 fundamentel、不是 compiler 問題。

唯一改進：用 non-temporal store (bypass cache)，compiler 可能已產 `memcpy` 用 REP MOVSB / RVV。

## perf list

看 support 的 event：

```bash
perf list
```

印幾百個 event。Interesting：

- Hardware events (common)
- Cache events (L1, LLC)
- Pipe events (on modern CPU)
- Tracepoint events

特定硬體 event（例 RISC-V SiFive）有 vendor-specific name。

## Overhead 考慮

- **Counting (`perf stat`)**：幾乎 0 overhead
- **Sampling (`perf record`)**：1-5% overhead（依 sample rate）
- **Multiplexing**：略 overhead，accuracy 影響小

所以 `perf stat` 是 daily work 的 bread-and-butter。

## Raw event

進階：perf 可以指定 raw event ID：

```bash
perf stat -e r1234 ./program     # raw event 0x1234
```

某 vendor-specific event 沒 name、只能用 raw number。查 hardware manual 找 ID。

## 常用 perf stat 組合

```bash
# Detailed: 一次看 IPC + cache + branch
perf stat -d ./program

# More detail (level 2)
perf stat -dd ./program

# 計算某指定 code region (用 -I interval)
perf stat -I 1000 ./program       # 每秒一次 snapshot

# Per-thread
perf stat -p <pid>
perf stat -a                      # system-wide
```

## `perf top`：即時 top-like tool

```bash
sudo perf top
```

類似 `top` 但是 per-function、基於 sampling。實時看 hot function。

快速判斷「現在 CPU 在做什麼」神器。

## 常見誤會

1. **「Cache miss 越少越好」**：一般對。但 miss 0 可能 working set 太小（not representative）。
2. **「高 IPC 一定好」**：多數情況對。但 IPC 高也可能 benchmark 不代表真實。
3. **「所有 event 精確」**：Sample 有 bias。Multiplexing 有 inaccuracy。raw count 相對精確。
4. **「perf 數字跨 machine 可比較」**：不建議。不同 CPU 同名 event 可能 count 不同東西。
5. **「branch-misses % 低 = good」**：到某 point 邊際效益。branch miss < 0.5% 通常無需再優化。

## 動手練習

1. 跑 `perf stat ls`、解讀每個數字。
2. 寫 cache-friendly 跟 unfriendly loop，perf stat 對比 miss。
3. 寫 branch-heavy 程式（random 資料 if-else），測 branch miss rate。
4. 用 `perf list | grep cache` 看你的 CPU 支援什麼 cache event。
5. 跑一個 compute-heavy benchmark，解釋 IPC 數字。

## 自我檢核

- [ ] 我能說出 6 個核心 perf event 的意義
- [ ] 我能用 perf stat 量 IPC、branch miss rate、L1 miss rate
- [ ] 我知道 counting vs sampling 的差異
- [ ] 我看到 IPC 0.5 能給 diagnosis flow
- [ ] 我知道 top-down methodology 的四個 bucket

下一章深入 perf record / perf report — 找 hot function 的主要工具。

→ [Ch 7 perf record / perf report 實戰](./07-perf-tool.md)
