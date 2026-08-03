# Ch 29 指令層級真相（Instruction-Level Truth）

> 延遲數字來自 Jia et al. arXiv 1903.07486 microbenchmark，以讀者實測為準，實際數值依 GPU 頻率與 CUDA 版本略有出入。

大多數人對 GPU 效能的直覺從 x86 OOO（Out-of-Order）移植過來，這個直覺在 GPU 上幾乎完全失效。本章把真相說清楚：GPU 用的是一套截然不同的策略，搞懂這個策略，才能真正理解 Ch 28 那些 stall count、wait mask 在做什麼。

---

## 29.1 為什麼 GPU 不用 Out-of-Order

x86 的 OOO 引擎做的事：在一個 ~200-instruction 的 reorder buffer 裡，動態追蹤資料相依，把不相依的指令重排到相依指令前面跑。register renaming 消除 WAW/WAR 假相依。這一切對「單一 thread 的一條執行流」非常有效。

GPU 的問題不同。一個 warp 有 32 個 thread，每條 warp 指令必須 in-order 執行（SIMT 保證）。所以 warp 內部的 OOO 沒有意義，GPU 不實作它。但 SM 裡同時有許多個 warp（Turing sm_75 最多 32 個 active warps per SM），**warp 切換本身就是 latency hiding 機制**。

原理很直接：warp A 發出一條 LDG（global memory load），要等 ~28 cycles 才能拿到資料。這 28 個 cycle 裡，scheduler 切換到 warp B、C、D……繼續發指令，LDG 的 latency 被其他 warp 的工作「填滿」。

代價是什麼？每個 warp 都需要一套完整的 register file（Turing 每個 SM 有 65536 個 32-bit register，被所有 active warp 瓜分）。warp 越多，每個 warp 能用的 register 越少。這個 trade-off 就是 Ch 20 的核心——本章把底層機制補完整。

**一句話總結**：x86 靠硬體動態重排一個 thread 的指令視窗，GPU 靠軟體靜態排好多個 warp 的發射順序。前者複雜的硬體換靈活性，後者簡單的硬體換吞吐。

---

## 29.2 延遲 vs 吞吐：最常被混淆的兩個數字

先把概念分清楚。**延遲**（latency）是一條指令從 issue 到結果可用的 cycle 數。**吞吐**（throughput）是單位時間能完成多少條指令。

這兩個數字**沒有倒數關係**，因為 FFMA 是 pipelined 指令。FFMA 延遲 4 cycles，但每個 cycle 都可以 issue 一條新的 FFMA。Turing sm_75 每個 SMSP（Sub-MultiProcessor）有 16 個 FP32 core，一個 SM 有 4 個 SMSP，所以峰值吞吐是 **64 FFMA/cycle/SM**，不是 1/4。

| 指令 | 延遲 (cycles) | 吞吐 (ops/cycle/SM) | 類型 |
|------|:---:|:---:|:---:|
| FFMA（FP32） | ~4 | 64（4 SMSP × 16） | 固定延遲 |
| IMAD（INT32） | ~4 | 64 | 固定延遲 |
| LDG L1 hit | ~28 | 受 bandwidth 限 | 變延遲 |
| LDG L2 hit | ~100 | 受 bandwidth 限 | 變延遲 |
| LDG DRAM | ~500+ | 受 bandwidth 限 | 變延遲 |
| LDS（無 bank conflict） | ~20 | 受 bandwidth 限 | 變延遲 |
| LDS（有 bank conflict） | 更長 | 降低 | 變延遲 |
| STS | ~20 | — | 變延遲 |

（microbenchmark 數值，來自 Jia et al. arXiv 1903.07486，以讀者實測為準）

「固定延遲」和「變延遲」的區分很重要，後面會說為什麼。

---

## 29.3 Latency Hiding 的完整量化分析

### 基本公式推導

填滿 pipeline 的核心條件：在任意一個 cycle，scheduler 必須能找到一條可以 issue 的指令。用符號表示：

```
in-flight 獨立操作數 ≥ latency（cycles）
```

這裡的「獨立操作數」可以拆解成兩個維度：

- **N**：active warp 數（每個 warp 各自是一條獨立執行流）
- **M**：每個 warp 內部的獨立 instruction chain 數（ILP）

因此填滿 pipeline 的條件是：

```
N × M ≥ latency
```

以 FFMA（latency = 4）為例，以下組合都能填滿：

| N（warp 數） | M（ILP） | N×M | Pipeline 使用率 |
|:---:|:---:|:---:|:---:|
| 1 | 1 | 1 | 25%（嚴重 stall） |
| 2 | 1 | 2 | 50% |
| 4 | 1 | 4 | 100% ✓ |
| 2 | 2 | 4 | 100% ✓ |
| 1 | 4 | 4 | 100% ✓ |
| 8 | 1 | 8 | 100%（多餘的 warp 沒有貢獻更多） |
| 4 | 4 | 16 | 100%（多餘 ILP，scheduler 有選擇餘地） |

「100%」是指 FFMA pipeline 滿載，不代表整個 SM 資源都被用到。當 N×M 超過 latency，scheduler 有多個可用指令可以選，這有助於吸收其他種類的 stall（例如 bank conflict），是謂 latency margin。

### 純 FFMA 情境詳細展開

**情境 1：單一 warp，完全依賴鏈（最差情況）**

```ptx
// 每條 FFMA 都依賴上一條的結果，N=1, M=1
ffma.rn  f2, f0, f1, f2   // cycle 0 issue，cycle 4 結果可用
// cycle 1, 2, 3: scheduler 找不到可 issue 的指令 → 3 cycle stall
ffma.rn  f2, f2, f3, f4   // cycle 4 issue（等 f2 ready）
// cycle 5, 6, 7: stall
ffma.rn  f2, f2, f5, f6   // cycle 8 issue
```

有效吞吐：1 FFMA / 4 cycles = 25%。

**情境 2：4 warp，每個 warp 各自依賴鏈（N=4, M=1）**

```
cycle 0: warp 0 issues FFMA → 結果 cycle 4 可用
cycle 1: warp 1 issues FFMA → 結果 cycle 5 可用
cycle 2: warp 2 issues FFMA → 結果 cycle 6 可用
cycle 3: warp 3 issues FFMA → 結果 cycle 7 可用
cycle 4: warp 0 的結果回來，再 issue 下一條
cycle 5: warp 1 的結果回來
cycle 6: warp 2 的結果回來
cycle 7: warp 3 的結果回來
cycle 8: warp 0 第三條...
```

每個 cycle 都有一個 warp 可以 issue，pipeline 100% 利用率。

**情境 3：單一 warp，4 條獨立 chain（N=1, M=4）**

```ptx
// 4 條獨立的累積運算，沒有交叉依賴
ffma.rn  f_a, f0,  f1,  f_a   // cycle 0: chain A
ffma.rn  f_b, f2,  f3,  f_b   // cycle 1: chain B（與 A 無相依）
ffma.rn  f_c, f4,  f5,  f_c   // cycle 2: chain C
ffma.rn  f_d, f6,  f7,  f_d   // cycle 3: chain D
ffma.rn  f_a, f8,  f9,  f_a   // cycle 4: chain A（f_a 這時已就緒）
ffma.rn  f_b, f10, f11, f_b   // cycle 5: chain B（f_b 就緒）
...
```

ptxas 排列這 4 條 chain 的交錯順序，讓 FFMA pipeline 100% 利用。不需要額外的 warp。

**對 LDG 的同樣分析（latency = ~28 cycles，L1 hit）**

要填滿等待 LDG 的空隙，需要 N × M ≥ 28。幾個可行組合：

| N（warp 數） | M（每 warp LDG 的獨立 pending 數） | N×M | 能否填滿 28-cycle latency |
|:---:|:---:|:---:|:---:|
| 4 | 1 | 4 | 14%（嚴重不足） |
| 8 | 1 | 8 | 29%（不足） |
| 16 | 1 | 16 | 57%（不足） |
| 28 | 1 | 28 | 100% ✓ |
| 14 | 2 | 28 | 100% ✓ |
| 7  | 4 | 28 | 100% ✓ |
| 4  | 7 | 28 | 100% ✓（每 warp prefetch 7 個 LDG） |

這說明為什麼 memory-bound kernel 需要更高 occupancy（更多 warp）或更激進的 prefetch（更多 in-flight LDG）。FFMA-bound kernel 只需要 4 個 warp 或 ILP=4 就夠，但 memory kernel 的要求嚴苛 7 倍。

---

## 29.4 固定延遲 vs 變延遲：scoreboard 的必要性

FFMA 延遲固定是 4 cycles，ptxas 知道這件事，所以可以在 SASS control code 的 stall count 欄位直接填 4（或者用 ILP 填滿而填 0）。硬體**不需要動態追蹤**——只要 stall count 是對的，scheduler 就知道什麼時候可以 issue 下一條依賴指令。

LDG 不同。L1 hit 是 ~28 cycles，L2 hit 是 ~100 cycles，DRAM 是 ~500+ cycles。在指令發射的時候，沒有人知道這次 load 會命中哪一層。這時候 stall count 無法靜態決定——硬體需要一個**動態機制**追蹤「這個 register 的資料還沒回來」。

### Scoreboard 的運作機制

Turing 的 scoreboard 是每個 SMSP 一組的硬體結構，負責追蹤「哪些 register 目前有 pending memory operation」。運作流程如下：

1. **LDG 發射**：scheduler 把這條 LDG 送到 LSU（Load/Store Unit）。同時，目標 register（例如 R4）被登記到 scoreboard，標記一個 barrier slot（例如 B0）為 pending。
2. **後續指令掃描**：scheduler 在選下一條 issue 的指令時，會檢查 SASS control code 裡的 `wait_mask`。若某條指令的 wait_mask 標記了 B0（`[B0:...]`），scheduler 就不會 issue 它，直到 B0 被清除。
3. **Load 完成**：LSU 把資料寫回 register file，scoreboard 清除 B0 的 pending 狀態。
4. **後續指令解鎖**：下一次 scheduler 檢查時，等 B0 的指令變成 eligible，可以 issue。

Turing 有 **6 個 scoreboard barrier slot**（B0–B5），支援最多 6 個同時 in-flight 的 memory operation per warp。超過 6 個需要手動插入等待（或者 ptxas 會保守地序列化）。

### SASS 中的 wait_mask 體現

回到 Ch 28 的 control code 格式。一條帶 scoreboard 等待的 SASS 指令長這樣（Turing 格式，control code 在方括號內）：

```sass
// 格式：[barrier_alloc : wait_mask : yield : read_barrier : write_barrier : stall_count]
/*0000*/  LDG.E.128  R4, [R2]            // 發射 LDG，B0 = pending
          // control code: [B0:-:-:-:-:4]
          //   B0 = 分配 scoreboard barrier 0 給這條 LDG
          //   stall_count = 4：下一條指令 4 cycle 後才能 issue

/*0010*/  LDG.E.128  R8, [R6]            // 第二個 LDG，B1 = pending
          // control code: [B1:-:-:-:-:4]

/*0020*/  IADD3  R2, R2, 0x10, RZ        // 更新指標，不依賴 R4/R8，自由 issue
          // control code: [-:-:-:-:-:1]

/*0030*/  IADD3  R6, R6, 0x10, RZ
          // control code: [-:-:-:-:-:1]

/*0040*/  FFMA   R12, R4, R8, R12        // 依賴 R4（B0）和 R8（B1）
          // control code: [-:B0|B1:-:-:-:1]
          //   wait_mask = B0|B1：等 B0 和 B1 都清除才能 issue
```

這個序列的時序：

- `0000`：LDG R4 發出，scoreboard B0 pending
- `0010`：LDG R8 發出（4 cycle stall 後），scoreboard B1 pending
- `0020`~`0030`：IADD3 填 latency
- `0040`：FFMA 等 `B0|B1`——兩個 LDG 都回來才能跑

如果 LDG 命中 L1（~28 cycles），而 IADD3 只填了 ~8 cycles，剩下 ~20 cycles 會是 `Stall Long Scoreboard`。想消除這個 stall，需要在 LDG 和 FFMA 之間插入更多工作，或者靠 warp switching 讓其他 warp 填這段時間。

---

## 29.5 Dual-Issue 的真相：SM vs SMSP 層級

這裡有一個常見的混淆，需要說清楚。

### Turing warp 無 dual-issue

Turing（sm_75）的 warp scheduler 每個 clock cycle 只能對同一個 warp issue **1 條指令**。不像某些 CPU 的 superscalar，同一個 warp 無法在同一 cycle 並行 issue 兩條指令。這是 **single-issue per warp per clock**。

如果你在某些文件或論壇看到「GPU 可以 dual-issue」，那要分清楚年代：Fermi（sm_20）架構有 dual-issue 設計，可以對同一個 warp 在一個 cycle issue 2 條指令（若不相依）。Kepler 以後這個機制被簡化，Turing 已無 dual-issue。

### SM-level vs SMSP-level

Turing 的一個 SM 有 **4 個 SMSP**（Sub-MultiProcessor，也叫 partition）。每個 SMSP 有自己的：

- Warp scheduler（1 個）
- 16 個 FP32 FMA core
- 16 個 INT32 ALU
- 獨立的 register file bank

這 4 個 SMSP **同時獨立運作**。所以：

| 層級 | 並行能力 |
|------|---------|
| 單一 SMSP | 每 cycle issue 1 條 warp 指令 |
| 單一 SM（4 SMSP） | 每 cycle 最多 issue 4 條指令（來自 4 個不同 SMSP 各自的 warp） |

換句話說，SM-level 的「每 cycle 64 FFMA」並不是因為一個 scheduler issue 了 64 條指令，而是 4 個 SMSP 各自 issue 1 條 FFMA，每條 FFMA 在 16 個 FP32 core 上同時執行（warp 的 SIMT 並行），4 × 16 = 64。

**重要推論**：若整個 SM 只有 4 個 active warp，最好的情況是每個 SMSP 拿到 1 個 warp，每 cycle 4 個 SMSP 各自 issue 1 條，SM 層面滿載。但若只有 1 個 warp，它只能在 1 個 SMSP 上跑，其他 3 個 SMSP 閒置，SM throughput 最多 25%。

### 不同 warp 之間的「並行」

雖然單一 warp 無 dual-issue，**同一個 SM 裡不同 warp 的指令可以在同一 cycle 跑在不同 SMSP 上**。這才是 SM-level 並行的正確理解方式：不是一個 warp 更快，而是多個 warp 讓多個 SMSP 都有事做。

---

## 29.6 LDG 的 prefetch 策略

變延遲指令最重要的優化手段是**提前發射**。把 LDG 盡量提前到真正需要資料的地方之前，讓 latency 在 shader 執行其他工作的時候「免費」被吸收掉。

典型 double-buffer 模式：

```cuda
// 簡化示意，實際需要 __pipeline_memcpy_async 或手動 PTX
// 先 prefetch 下一輪的資料
__prefetch_global(ptr + stride);

// 計算當前輪的資料（LDG latency 在這段時間被吸收）
float result = a * b + c;  // → FFMA

// 這裡再用 prefetch 的資料
float val = *ptr;
```

協作式 prefetch 在 sm_80（Ampere）之後有 `cp.async` PTX 指令，可以把 global→shared 的搬移非同步化。Turing 沒有這個，但 warp-level 的 LDG 提前發射同樣有效。

---

## 29.7 Register File 結構與配置限制

### 物理結構

Turing 每個 SM 有 **65536 個 32-bit register（64K × 32 bit = 256 KB per SM）**。這個 register file 被 4 個 SMSP 瓜分，每個 SMSP 持有 16384 個 register。

active warp 使用的 register 數從這個池子裡分配，**靜態分配在 kernel 啟動時決定**（ptxas 根據 kernel 的 register 使用量決定，並向上取整到特定粒度）。

### 配置計算

以一個 threadblock 配置 128 threads（4 warp），每個 thread 使用 32 個 register 為例：

```
register 用量 = 128 threads × 32 registers/thread = 4096 registers per block
SM 最多可以同時跑多少個 block：65536 / 4096 = 16 blocks
每個 block 有 4 warp，所以 max active warp = 16 × 4 = 64 warp
但 Turing SM 最多 32 active warp（硬體限制）
```

所以 register 數夠，但 warp 數量先撞到 SM 的 warp slot 上限（32）。

換另一個配置，每 thread 使用 64 register：

```
每 block = 128 × 64 = 8192 registers
可以同時跑的 block = 65536 / 8192 = 8 blocks
active warp = 8 × 4 = 32 warp（剛好到 SM 上限）
```

再若每 thread 使用 128 register：

```
每 block = 128 × 128 = 16384 registers
可以同時跑的 block = 65536 / 16384 = 4 blocks
active warp = 4 × 4 = 16 warp（register 成了限制因素）
```

這正是 occupancy 計算器的邏輯：register 用量、shared memory 用量、warp 上限三者取最小。

### Register Spill 的代價

當 ptxas 計算出 kernel 需要的 register 超過可用量（`--maxrregcount` 限制、或每 thread 超過 255 個 register），多出的 register 會 **spill** 到 `.local`（local memory，物理上是 DRAM 或 L2 cache）。

spill 的代價：

```
spill 到 local：LDG.E [local_ptr]
延遲：~28 cycles（L1 hit，若 L1 已滿則更長）
```

每次 spilled register 的讀寫都等同一次 memory load/store，而且 local memory 的位址是 per-thread 的（不能 coalesce），cache footprint 也大。

**正確策略**：找到讓 math latency 被 warp 或 ILP 填滿的最低 register 需求，而不是盲目壓低 register 數量。若 `--maxrregcount 32` 導致大量 spill，看 Nsight Compute 的 `l1tex__t_sectors_pipe_lsu_mem_local_op_ld` 指標——若 local load 很高，spill 正在殺效能，增加 register limit 反而更快。

---

## 29.8 Register Bank Conflict

Turing 的 register file 是分 bank 的物理結構。同一個 cycle 內，如果有兩條需要讀取**同一個 bank**的不同 register 的指令，就會發生 bank conflict，導致額外 stall。

bank 編號的計算是：

```
bank = register_index % num_banks
```

確切的 bank 數需要逆向工程才知道，一般文獻估計 Turing 有 4 個 bank。這意味著 R0、R4、R8、R12……都在同一個 bank，在密集 FFMA 程式碼裡有時會造成 conflict。

`.reuse` flag 是解法之一。SASS 裡長這樣：

```sass
FFMA R4, R0, R2.reuse, R4
```

`.reuse` 告訴硬體：「這個 R2 的值下次還會用到，請把它 cache 在 SMSP 的 L0 register cache（operand cache）裡。」下次讀 R2 就不需要再從 register file 讀，bypasses bank conflict。

ptxas 在高 occupancy + register-intensive kernel 下會自動加 `.reuse`，但不保證在所有情況下都最優。如果用 Nsight Compute 看到 register bank conflict 很高，可以考慮手動調整 register 分配策略（例如，把頻繁 reuse 的值保留在特定 register 而不是讓 compiler 隨意分配）。

---

## 29.9 FMA 是效能基準的具體計算

### T4 峰值 TFLOPS 推導

NVIDIA 的 Peak TFLOPS 規格永遠按 FMA 算，原因是每一條 FMA 做了**2 個 flops**（multiply + add）。

Turing T4 的 FP32 峰值計算：

```
每 SM 的 FP32 core 數 = 4 SMSP × 16 FP32 core = 64 cores
T4 有 40 個 SM（sm_75，Turing TU104 die）
FMA 每次做 2 flops（multiply + add）
T4 基礎時脈 = 585 MHz，boost = 1590 MHz

峰值 TFLOPS（boost）= 40 SM × 64 cores × 2 flops × 1590 MHz
                     = 40 × 64 × 2 × 1.59 × 10^9
                     = 8,110,080,000,000 flops/s
                     ≈ 8.1 TFLOPS
```

這和 NVIDIA 官方規格（8.1 TFLOPS FP32）吻合。

**為什麼所有 GPU 的 peak 規格都用 FMA 算？** 因為 FMA 是完整 FP32 pipeline 的利用率基準。在 GEMM 這類工作中，每個輸出元素 = 一連串的 multiply-and-accumulate，FFMA 是唯一能讓 FP32 unit 跑到 100% 的指令類型。用 FMUL 或 FADD 單獨算，吞吐只有 FMA 的一半（用了一半 pipeline）。所以 FMA TFLOPS 是 GPU 計算能力最有意義的單一數字。

### 應該用 FFMA，不要分開的 FMUL + FADD

理由：

- FFMA 單一指令，延遲 4 cycles，1 個 issue slot
- FMUL + FADD，兩條指令，延遲 4+4=8 cycles，2 個 issue slot，throughput 砍半
- 精度上，FMA 只做一次 rounding，比分開的 FMUL + FADD 精度更好（IEEE 754-2008 保證）

ptxas 預設會把 `a * b + c` 融合成 FFMA，除非你用 `--fmad=false`。`-use_fast_math` 也會強制啟用更激進的 FMA 融合。

確認 kernel 是否真的在用 FFMA：

```bash
cuobjdump --dump-sass your_kernel.cubin | grep FFMA | head -20
```

如果看到大量的 FMUL + FADD 而不是 FFMA，代表有些地方阻止了 FMA 融合（常見原因：`-fmad=false`、或者有 `volatile` 強制斷鏈、或者編譯器判斷精度需求）。

`__fmaf_rn(a, b, c)` 是 intrinsic，強制單精度 FMA，rounding 到 nearest-even。在 `-use_fast_math` 以外、又需要確保 FMA 不被拆散的場合使用。

---

## 29.10 LDG 的 prefetch 與 scoreboard 完整 SASS 序列

### 典型模式：LDG → independent work → FFMA

```sass
// 典型 memory+compute 混合 kernel 的 SASS 骨幹
// 以下是帶 control code 的完整序列（簡化）

/*0000*/  @P0 LDG.E.128  R4,  [R2]      // issue LDG，B0 pending，stall=4
/*0010*/  @P0 LDG.E.128  R8,  [R6]      // issue 第二個 LDG，B1 pending，stall=4
/*0020*/      IADD3      R14, R14, 0x1, RZ   // loop counter，stall=1
/*0030*/      ISETP.LT.U32 P0, PT, R14, R15, PT  // 更新 predicate，stall=1
/*0040*/      IADD3      R2,  R2, 0x10, RZ  // 指標 += 16 bytes，stall=1
/*0050*/      IADD3      R6,  R6, 0x10, RZ  // 指標 += 16 bytes，stall=13
          // 上面 stall=13：再等 ~13 cycle 讓 LDG 有機會 hit L1
/*0060*/      FFMA   R16, R4,  R8,  R16    // wait [B0:B1]，R4+R8 ready
/*0070*/      FFMA   R17, R5,  R9,  R17    // ILP：R5,R9 是 LDG.128 的另一段
/*0080*/      FFMA   R18, R6,  R10, R18    // ILP
/*0090*/      FFMA   R19, R7,  R11, R19    // ILP（4 條獨立 FFMA，M=4）
/*00a0*/  @P0 BRA    0x0                   // loop back
```

注意幾個細節：

1. `LDG.E.128` 一次讀 128 bit（4 個 float），目標是 R4–R7 和 R8–R11 兩組 4 個 register
2. 中間的 IADD3 / ISETP 是免費填 latency 的工作，不需要用 warp 切換
3. `stall=13` 在 IADD3 後面讓 scheduler 再等一段時間，確保 LDG 有時間完成
4. FFMA 群的 `wait [B0:B1]` 告訴 scheduler 等兩個 scoreboard 都清除
5. 4 條 FFMA 互相獨立（4 個 accumulator R16–R19），ILP=4，填滿 FFMA pipeline

如果這段 kernel 的 LDG 經常 L2 miss（~100 cycles），即使 stall=13 的等待也不夠。這時候就需要靠多 warp 的 warp switching 來填補 80+ cycles 的差距。

---

## 29.11 Nsight Compute 驗證這些現象

### 關鍵指標與指令

以下 ncu 指令可以抓到本章討論的各種現象：

```bash
# 基本 profiling，抓 scheduler 和 warp state
ncu --metrics \
  sm__inst_executed,\
  sm__warps_active.avg.pct_of_peak_sustained_active,\
  smsp__warp_issue_stalled_long_scoreboard_per_warp_active,\
  smsp__warp_issue_stalled_short_scoreboard_per_warp_active,\
  smsp__warp_issue_stalled_mio_throttle_per_warp_active \
  ./your_kernel

# 看 memory instruction 的 sector 計數（驗證 LDG 命中層）
ncu --metrics \
  l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum,\
  l1tex__t_requests_pipe_lsu_mem_global_op_ld.sum,\
  lts__t_sectors_op_read.sum,\
  dram__sectors_read.sum \
  ./your_kernel

# 看 local memory spill（register spill 指標）
ncu --metrics \
  l1tex__t_sectors_pipe_lsu_mem_local_op_ld.sum,\
  l1tex__t_sectors_pipe_lsu_mem_local_op_st.sum \
  ./your_kernel

# 看整體 occupancy 和 achieved warp 數
ncu --metrics \
  sm__warps_active.avg.pct_of_peak_sustained_active,\
  achieved_occupancy \
  ./your_kernel
```

### 各指標對應本章概念

| Nsight Compute 指標 | 對應概念 | 高值代表什麼 |
|---------------------|---------|-------------|
| `smsp__warp_issue_stalled_long_scoreboard_per_warp_active` | LDG 等 memory 的 scoreboard stall | memory latency 沒被填滿，增加 warp 或 prefetch |
| `smsp__warp_issue_stalled_short_scoreboard_per_warp_active` | 固定延遲指令（FFMA）的 stall | ILP 不足，同一 warp 需要更多獨立 chain |
| `smsp__warp_issue_stalled_mio_throttle_per_warp_active` | memory 指令發射頻寬限制 | LDG 發得太密，MIO queue 堵了 |
| `l1tex__t_sectors_pipe_lsu_mem_global_op_ld` | global load 的 L1 sector 次數 | 搭配 requests 算 hit rate |
| `l1tex__t_sectors_pipe_lsu_mem_local_op_ld` | local memory（spill）讀取 | 值 > 0 代表有 register spill，代價高 |
| `sm__inst_executed` | 實際執行的指令數 | 與 FFMA 預期數對比，看是否有多餘指令 |
| `achieved_occupancy` | 實際 active warp / 最大 warp | 低代表 occupancy 受限，看 register / shared mem |

### 診斷流程

```
1. 跑 ncu --set full（抓所有指標）
2. 看 "Top Stall Reasons" section：
   - Long Scoreboard 高 → memory latency hiding 不夠
   - Short Scoreboard 高 → FFMA ILP 不夠
   - MIO Throttle 高 → LDG 發射頻率超過 memory pipeline 容量
3. 看 achieved_occupancy：
   - 低 → 用 occupancy calculator 找出是 register / smem / warp slot 卡住
4. 看 local memory sectors：
   - 非零 → register spill，考慮放寬 --maxrregcount
5. 確認 sm__inst_executed 裡 FFMA 比例：
   - FFMA 比例低 → overhead 指令太多或 issue 效率差
```

---

## 29.12 完整範例：分析一個 FP32 dot product 的 SASS

假設有一段向量點積的 SASS（簡化）：

```sass
// warp 0, 第一輪
/*0030*/   LDG.E.128 R4, [R2]       // global load，變延遲，scoreboard B0 pending
/*0040*/   LDG.E.128 R8, [R6]       // global load，變延遲，scoreboard B1 pending
/*0050*/   ISETP.GT.U32 ...          // 無關指令，填 latency
/*0060*/   IADD3 R2, R2, 0x10, RZ   // 更新指標，無關
/*0070*/   IADD3 R6, R6, 0x10, RZ   // 更新指標，無關
/*0080*/   LDG.E.128 ... [B0:W1]    // wait scoreboard B0（等 R4 ready）
/*0090*/   FFMA R12, R4, R8, R12    // 現在 R4, R8 都 ready，FFMA 固定 4 cycle
/*00a0*/   FFMA R13, R5, R9, R13    // 和上面獨立（R5,R9 是不同的 pair），ILP
/*00b0*/   FFMA R14, R6, R10, R14   // ILP
/*00c0*/   FFMA R15, R7, R11, R15   // ILP
```

注意：

1. 兩個 LDG 盡量早發射，讓 load latency 在 ISETP/IADD3 期間被吸收
2. `[B0:W1]` 是 wait mask，等 LDG 完成
3. 四條 FFMA 互相獨立（四個 accumulator），ILP = 4，填滿 FFMA pipeline

這個模式是 high-performance FP32 kernel 的骨幹。

---

## 29.13 踩雷

### 錯把 latency 當 throughput 的倒數

常見誤解：「FFMA 延遲 4 cycles，所以每秒吞吐是 1/4。」錯。FFMA pipelined，每 cycle 都可以 issue 一條新的 FFMA，吞吐和延遲是獨立的兩個數字。

### 忘記 LDG 是變延遲

光靠 stall count 處理 LDG 是錯的，硬體需要 scoreboard。這不是你要手動管的事（ptxas 負責），但如果你用 CuAssembler 手寫 SASS 或者做 kernel fusion 修改 SASS，這個細節可以搞壞 register hazard，導致讀到垃圾值。

### 認為 register 用越少越好

Ch 20 說過：occupancy 不是越高越好，warp 數 × register 數 = register file 佔用。如果你為了「省 register」強迫 compiler 溢出（spill）到 local memory，溢出的 LDG 延遲 ~500+ cycles，比多用幾個 register 慘多了。正確策略是找到讓 math latency 被 warp 或 ILP 填滿的最低 register 需求，不是盲目壓低。

### 誤以為 Turing 有 dual-issue

Fermi 才有 dual-issue。Turing 每個 warp scheduler 每 cycle 只 issue 1 條指令。多個 SMSP 的並行是 SM-level 現象，不是 dual-issue。把這兩個概念混用，在分析 scheduler statistics 時會得到錯誤結論。

### 認為 `__fmaf_rn()` 和 `a * b + c` 永遠相同

預設編譯（`-fmad=true`）下，`a * b + c` 和 `__fmaf_rn(a, b, c)` 結果相同，都是 FMA。但如果是 `-fmad=false`（或某些特殊情境下 compiler 選擇不融合），`a * b + c` 等於 FMUL + FADD，有兩次 rounding，精度和效能都不同。只在需要精確 bit-reproducible 結果時用 `--fmad=false`，代價是吞吐腰斬。

---

## 小結

| 概念 | GPU 的做法 | 對比 x86 |
|------|-----------|----------|
| Latency hiding | Warp switching + ILP（靜態） | OOO + register renaming（動態） |
| 固定延遲（FFMA） | stall count 靜態設定 | 動態追蹤 |
| 變延遲（LDG） | scoreboard 動態等待（B0–B5） | load buffer + OOO bypass |
| Dual-issue | 無（Turing）；Fermi 有 | superscalar issue |
| Register | 所有 warp 共享 65536 個 32-bit reg | per-thread，OOO rename |
| Pipeline 填滿條件 | N × M ≥ latency | 靠 ROB 動態滿足 |

掌握這層，Ch 28 的那些 control code 數字就不再神秘——它們是 ptxas 在做靜態排程的產出，每個 stall count 和 wait mask 都對應一個具體的 latency 計算。

---

## 跨章連結

- 前一章：[Ch 28 讀 SASS](./28-reading-sass.md)（control code 的 stall count 機制）
- 下一章：[Ch 30 Tensor Core](./30-tensor-core.md)（專用矩陣 MMA 單元，超越 FFMA）
- 回連：[Ch 20 Occupancy vs ILP](./20-occupancy-vs-ilp.md)（latency hiding 策略）

---

## 延伸閱讀

1. **Jia et al. "Dissecting the NVIDIA Turing T4 GPU via Microbenchmarking"** (arXiv 1903.07486) — 本章延遲/吞吐數字的一手來源，方法論也值得學
2. **NVIDIA Turing Tuning Guide** (docs.nvidia.com/cuda/turing-tuning-guide) — 官方對 sm_75 優化的建議，配合本章數字對照閱讀
3. **CuAssembler** (github.com/cloudcores/CuAssembler) — 直接控制 SASS stall count 做實驗的工具，想真正驗證本章數字必備
4. **Dissecting GPU Memory Hierarchy** (Mei & Chu, SC'16) — 更系統的 memory latency microbenchmark，涵蓋多代架構的 L1/L2/DRAM 數字
5. **NVIDIA Nsight Compute Documentation** (docs.nvidia.com/nsight-compute) — 各 metric 的定義與 profiling workflow，本章 29.11 節指標的官方參考
