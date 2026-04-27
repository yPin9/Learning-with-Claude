# 練習 B — 用 llvm-mca 分析一段 hot loop

> 目標：挑一個實際 hot loop、用 llvm-mca 分析 pipeline behavior、找出 bottleneck、propose compiler / source improvement。

## 為什麼做這個

Ch 8 介紹 llvm-mca 概念。本練習是動手 apply。做完你對 llvm-mca 的 output 有熟悉度。

## Workflow

```
1. Pick a C function as subject
2. Compile -S -O2
3. 編輯 .s 加 LLVM-MCA markers 在 hot loop
4. 跑 llvm-mca 分析
5. 解讀結果
6. 改 C 或 flag，重新 analyze
7. 對比
```

## 候選 hot loop

選一個：

### Option A: Dot product

```c
int64_t dot(const int *a, const int *b, int n) {
    int64_t sum = 0;
    for (int i = 0; i < n; i++)
        sum += (int64_t)a[i] * b[i];
    return sum;
}
```

經典 SIMD 候選。

### Option B: Matrix multiply inner

```c
void mmul_inner(const int *A, const int *B, int *C, int n) {
    for (int k = 0; k < n; k++) {
        int a = A[k];
        for (int j = 0; j < n; j++)
            C[j] += a * B[k*n + j];
    }
}
```

測 memory + compute。

### Option C: Popcount

```c
int popcount_buffer(const uint64_t *buf, int n) {
    int total = 0;
    for (int i = 0; i < n; i++)
        total += __builtin_popcountll(buf[i]);
    return total;
}
```

測 Zbb 的效果。

### Option D: CRC inner loop

```c
uint32_t crc_update(uint32_t crc, uint8_t byte) {
    crc ^= byte;
    for (int i = 0; i < 8; i++)
        crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1));
    return crc;
}
```

Bitmanip-heavy、Zbb 也能幫。

選一個、或自己提。本練習 walk through **Option C (popcount)**。

## Step 1: Compile 產 asm

```c
// popcnt.c
#include <stdint.h>
int popcount_buffer(const uint64_t *buf, int n) {
    int total = 0;
    for (int i = 0; i < n; i++)
        total += __builtin_popcountll(buf[i]);
    return total;
}
```

```bash
clang --target=riscv64 -march=rv64gc -O2 -S popcnt.c -o popcnt_gc.s
clang --target=riscv64 -march=rv64gc_zbb -O2 -S popcnt.c -o popcnt_zbb.s
```

兩版：一有 Zbb、一沒。

## Step 2: 看 asm 差異

```bash
diff popcnt_gc.s popcnt_zbb.s
```

沒 Zbb 版：popcount 用 SWAR 算法（多條 shift + and + add）。

有 Zbb 版：一條 `cpop` 指令。

## Step 3: 加 MCA markers

編輯 `popcnt_gc.s`，找到 inner loop（`.LBB0_2:` 之類 label），在前加 `# LLVM-MCA-BEGIN`、後加 `# LLVM-MCA-END`：

```asm
.LBB0_2:
    # LLVM-MCA-BEGIN popcnt_loop
    ld      a4, 0(a0)
    li      a5, ...
    and     a6, a4, a5
    ...
    add     a1, a1, a6
    addi    a2, a2, -1
    addi    a0, a0, 8
    bnez    a2, .LBB0_2
    # LLVM-MCA-END
```

同樣改 `popcnt_zbb.s`：

```asm
.LBB0_2:
    # LLVM-MCA-BEGIN popcnt_loop
    ld      a4, 0(a0)
    cpop    a4, a4
    add     a1, a1, a4
    addi    a2, a2, -1
    addi    a0, a0, 8
    bnez    a2, .LBB0_2
    # LLVM-MCA-END
```

## Step 4: 跑 llvm-mca

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 popcnt_gc.s
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 -mattr=+zbb popcnt_zbb.s
```

或挑 SiFive P670：

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-p670 popcnt_gc.s
llvm-mca -mtriple=riscv64 -mcpu=sifive-p670 -mattr=+zbb popcnt_zbb.s
```

## Step 5: 解讀 output

沒 Zbb 版：

```
Iterations:        100
Instructions:      1500
Total Cycles:      1023
Total uOps:        1500

IPC:               1.47
Block RThroughput: 10.2
```

每 iter 約 10 cycle、15 條 instructions。

有 Zbb 版：

```
Iterations:        100
Instructions:      500
Total Cycles:      250
Total uOps:        500

IPC:               2.00
Block RThroughput: 2.5
```

每 iter 約 2.5 cycle、5 條 instructions。

**Zbb 版快 4×**。

## Step 6: Bottleneck analysis

加 `-bottleneck-analysis`：

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 \
    -bottleneck-analysis popcnt_gc.s
```

看到：

```
Cycles with backend pressure > 50%: 95%
Throughput bottlenecks:
  Resource Pressure: 92%
  Data Dependencies: 8%

Critical sequence based on the simulation:
   ld a4, 0(a0)      ← load 依賴
   and a6, a4, ...
   add a1, a1, a6    ← accumulator 依賴
   ...
```

**Bottleneck**：一連串 dependency，pipeline 被 serialize。

Zbb 版 bottleneck：基本就是 load + cpop + add 一個 chain，dependency 也短。

## Step 7: Timeline view

```bash
llvm-mca -mtriple=riscv64 -mcpu=sifive-u74 -timeline -iterations=3 popcnt_gc.s
```

看 pipeline visual。`=` 代表 waiting。看哪幾條 stall。

## Step 8: 寫分析 report

```
# llvm-mca Analysis: popcount inner loop

## Subject
popcount_buffer() - hot inner loop

## Configurations
| Config | Iter cycle | IPC | Instructions/iter |
|--------|-----------|-----|---------|
| -march=rv64gc (SWAR) | 10.2 | 1.47 | 15 |
| -march=rv64gc_zbb (cpop) | 2.5 | 2.00 | 5 |

## Bottleneck (no Zbb)
Long dependency chain in SWAR algorithm.
Resource pressure high on IntALU.

## Bottleneck (with Zbb)
Only 3-op dependency chain (load → cpop → add).
Near-optimal for single-issue ALU.

## Conclusion
Zbb extension gives ~4x speedup for popcount-heavy workload.
Worth enabling in -march for any target with Zbb hardware.
```

## 進階：加 RVV

```bash
clang --target=riscv64 -march=rv64gcv -O3 -S popcnt.c -o popcnt_rvv.s
```

看 compiler 是否 auto-vectorize、用 `vcpop.v` 或 scalar loop。

分析 vector version 的 MCA 需要 RVV-aware scheduling model（SiFive X280 之類）。

## 進階：跟 ARM 比

用 `--target=aarch64-linux-gnu` 編同 code，`llvm-mca -mcpu=cortex-a72`。看 ARM 的 IPC / bottleneck。

有時 compiler 產生完全不同 asm（ARM 的 `cnt`）、效能可能接近 RVV。這 comparison 對寫 proposal 很有用。

## 輸出 JSON 用 script 處理

```bash
llvm-mca --json popcnt_gc.s > mca.json
```

寫 Python 解析 JSON、自動產 report table。

## 挑戰：自行設計 benchmark

不用 popcount。自選一個 workload：

- 你關心的 domain（audio DSP、crypto、image processing）
- 或學校作業的 hot function

走相同 workflow：compile、MCA、analyze。

## 報告 template

```markdown
# llvm-mca Case Study: [Workload Name]

## Subject
[Brief description]

## Methodology
- LLVM version: xxx
- Target: riscv64 -mcpu=...
- MCA iterations: default 100

## Configurations
[list]

## Results
[table with cycles, IPC, bottleneck]

## Analysis
[detailed reasoning]

## Recommendation
- Compiler-level fix
- Or source code restructure
- Or accept as hardware limitation
```

## 自我檢核

- [ ] 我選一個 hot loop、compile 成 asm
- [ ] 加 LLVM-MCA markers
- [ ] 跑 llvm-mca 分析，看懂 IPC + resource pressure
- [ ] 用 timeline view 找 bottleneck
- [ ] 寫成 markdown report

## 下一步

→ [Final Project：Performance case study](./final-project-perf-case-study.md)
