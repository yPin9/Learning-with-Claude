# Ch 8 — llvm-mca：靜態分析 throughput / bottleneck

> 目標：用 `llvm-mca`（LLVM Machine Code Analyzer）靜態分析一段 asm 的 pipeline utilization、throughput、bottleneck。這是找出 hot loop 的 micro-optimization opportunity 的神器。

## 什麼是 llvm-mca

**llvm-mca = LLVM Machine Code Analyzer**。吃一段 asm、模擬 target 的 pipeline、印出 throughput 跟 bottleneck 報告。

**不是實際跑**。不量時間。算的是：

- 平均每 cycle 幾條指令（IPC）
- 哪些 pipeline resource 最 loaded
- 每條指令的 latency
- Resource pressure breakdown

## 為什麼要 static analyzer

`perf` 是 dynamic、告訴你「跑時發生什麼」。但：

- 不知道「**為什麼**慢」—— pipeline 被卡在哪？
- 不能 what-if 分析 ——「如果少一條 MUL 會多快？」
- 需要硬體跑

`llvm-mca` 補這些：

- 精確分析 pipeline resource usage
- 可以試 "改一條 asm 看效果"
- 不用真跑（QEMU-user 都行）

**perf + llvm-mca 是 performance 分析的黃金組合**。

## 基本用法

```asm
# loop.s
.text
.globl loop
loop:
    add a0, a0, a1
    mul a2, a3, a4
    ret
```

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 loop.s
```

輸出：

```
Iterations:        100
Instructions:      300
Total Cycles:      203
Total uOps:        300

Dispatch Width:    2
uOps Per Cycle:    1.48
IPC:               1.48
Block RThroughput: 2.0
```

翻譯：

- 跑 100 iter (default)
- 總共 200 cycle + 小量 overhead
- IPC 1.48（不滿 2，有 resource contention）
- Block reciprocal throughput = 2.0 （每次 iteration 2 cycle）

## Resource Pressure

加 `-view` 看 detail：

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 -bottleneck-analysis loop.s
```

```
Resource pressure per iteration:
[0]     [1]     [2]     [3]
0.50    0.50    0.50    1.00

Resources:
[0] - SiFive7IntPipe (ALU) 
[1] - SiFive7IntPipe (ALU)
[2] - SiFive7FloatDiv
[3] - SiFive7IntDiv/Mul

Instruction Info:
[1]: 1 uOps, Latency 1, RThroughput 0.50   add a0, a0, a1
[2]: 1 uOps, Latency 3, RThroughput 1.00   mul a2, a3, a4
```

**讀法**：

- 每 iter mul 佔用 IntDiv/Mul unit 1 cycle → bottleneck
- add 只佔 half (can share with 2 ALU pipes)

Reciprocal throughput 2.0 = 1 mul × 1.0 cycle + some add overhead。**MUL 是瓶頸**。

## Region markers

通常你只想分析特定 hot loop，不是整個 function。用 `LLVM-MCA-BEGIN` / `LLVM-MCA-END` 標記：

```asm
# main.s
.text
entry:
    # prologue
    addi sp, sp, -16
    sd   ra, 0(sp)
    li   a5, 100
.LBB0_1:
    # LLVM-MCA-BEGIN hot_loop
    add  a0, a0, a1
    mul  a2, a3, a4
    # LLVM-MCA-END
    addi a5, a5, -1
    bnez a5, .LBB0_1
    # epilogue
    ld   ra, 0(sp)
    addi sp, sp, 16
    ret
```

llvm-mca 只分析 MCA-BEGIN / END 之間。

## 從 C code 到 llvm-mca

典型工作流：

1. 寫 C code
2. Compile `-S -O2` 產 `.s`
3. 在 hot loop 外加 MCA markers
4. 跑 llvm-mca 分析

```c
// dot.c
#include <stdint.h>
int64_t dot(int *a, int *b, int n) {
    int64_t sum = 0;
    for (int i = 0; i < n; i++) sum += (int64_t)a[i] * b[i];
    return sum;
}
```

```bash
clang --target=riscv64 -march=rv64gc -O2 -S dot.c -o dot.s
# Edit dot.s to add MCA markers around the loop
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 dot.s
```

**注意 clang 加 `# LLVM-MCA-BEGIN` comment 方式**。某些 MCA 版本認 `#` 前綴的 assembler directive。

## 進階：`-timeline-view`

```bash
llvm-mca -timeline -iterations=3 loop.s
```

輸出：

```
Timeline view:
                    012
Index     0123456789  

[0,0]     DeeER    .    .   add a0, a0, a1
[0,1]     DeeeER   .    .   mul a2, a3, a4
[1,0]      D==eeER .    .   add a0, a0, a1
[1,1]      D===eeeER    .   mul a2, a3, a4
...
```

每條指令在每 cycle 的狀態：

- `D` Dispatch
- `e` Execute
- `E` Execute end
- `R` Retire
- `=` Waiting

**視覺化 pipeline stall**。看到一長串 `=` 就知道等資源。

## 跟 perf 的分工

| 工具 | 型態 | 優勢 | 限制 |
|------|------|------|------|
| perf | Dynamic | 真實 behavior、含 memory system | 需硬體、noise |
| llvm-mca | Static | 精確 pipeline analysis、無 hardware | 不模擬 memory、假設 perfect branch predict |

**典型流程**：

1. perf 找 hot function
2. 看 hot function 的 asm
3. 抓 hot loop 丟給 llvm-mca
4. llvm-mca 指出 bottleneck（e.g., 某 resource saturated）
5. 改 code / compiler flag → 重複

## llvm-mca 的 scheduling model

llvm-mca 根據 `-mcpu=` 參考對應 LLVM 的 scheduling model（`RISCVSchedXxx.td`，Ch 13 of `learn_compiler_backend`）。

**這意味著 llvm-mca 的 accuracy 依賴 scheduling model 的正確性**。

- SiFive U74：model 相對準確（成熟）
- SiFive P870：2024 後才成熟
- T-Head C910：不在 upstream LLVM？可能用 generic

看 CPU 的 sched model：

```bash
ls llvm/lib/Target/RISCV/RISCVSched*.td
```

選 `-mcpu=` 時檢查 LLVM 是否支援：

```bash
llc -mtriple=riscv64 -mcpu=help | grep -i sifive
```

## 實例：Compare 兩個 asm variants

我想比較 `addi + mul` 跟 `sh1add`（Zba）。

```asm
# version1.s
add a0, a0, a1
mul a2, a3, 3       ; multiply by 3
```

vs

```asm
# version2.s (Zba)
sh1add a2, a3, a3   ; (a3 << 1) + a3 = a3 * 3
```

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-p670 version1.s
llvm-mca -mtriple=riscv64 -mcpu=sifive-p670 version2.s -mattr=+zba
```

對比 reciprocal throughput、知道 Zba version 快多少。

## Intel 的 IACA (deprecated)

歷史上類似工具。Intel 出的，2019 官方停維護。llvm-mca 已取代。

## 限制

llvm-mca 假設很多 ideal condition：

- 100% branch prediction 正確
- Cache 永遠 hit (L1)
- No register spill / reload
- Perfect instruction fetch

**所以 llvm-mca 給的 throughput 是 upper bound**。實際常常慢於 llvm-mca 預估。

對 "inner loop that fits in L1" analysis 很準。對包含 memory access 的 code 參考就好。

## 分析 RVV

```asm
# vadd.s
vsetvli t0, a0, e32, m1
vle32.v v0, (a1)
vle32.v v1, (a2)
vadd.vv v0, v0, v1
vse32.v v0, (a3)
```

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-x280 -mattr=+v vadd.s
```

**注意 -mcpu=** 的選擇。要 core 有 V extension 的 scheduling model。

## 動手練習

1. 寫一段 RISC-V asm 含 mul、add、branch，丟 llvm-mca 分析。
2. 從 C code compile 成 `.s`，加 MCA markers，分析 inner loop。
3. 對同一 C function，用 `-O2` 跟 `-O3` 產兩版 asm，llvm-mca 對比。
4. 用 `-timeline` view 看一段 asm 的 pipeline flow。
5. 對比 SiFive U74 跟 P670 的 `-mcpu=` 參數對同 asm 的 analysis 差異。

## 常見誤會

1. **「llvm-mca 量時間」**：不。算 cycle（理論 pipeline）。
2. **「llvm-mca 包含 memory」**：不（假設 L1 hit）。
3. **「llvm-mca 準」**：假設理想條件、upper bound。
4. **「all CPU 都有 scheduling model」**：沒。新 CPU 可能要等 model release。
5. **「llvm-mca 代替 perf」**：互補、不替代。

## 自我檢核

- [ ] 我能用 llvm-mca 跑一段 .s、解讀 IPC / throughput
- [ ] 我知道 resource pressure 代表什麼
- [ ] 我能用 `-timeline` 看 pipeline stall
- [ ] 我知道 llvm-mca 依賴 scheduling model
- [ ] 我能 combine perf + llvm-mca 分析 bottleneck

下一章看 flamegraph — 視覺化 profile 的 best practice。

→ [Ch 9 Flame graph 與 on-CPU profiling](./09-flamegraph.md)
