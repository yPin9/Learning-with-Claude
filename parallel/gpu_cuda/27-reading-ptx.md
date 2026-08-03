# Ch 27 — 讀 PTX：GPU 的 LLVM IR

> **目標**：能讀懂一段 vector add 的 PTX，理解 PTX 設計哲學與限制
> **環境**：CUDA 12.x nvcc；用 `nvcc -ptx -arch=compute_75 foo.cu` 產生（Colab/Godbolt）

---

## 為什麼要讀 PTX

Ch 26 我們看了 nvcc 的編譯流程：CUDA C → PTX → SASS。大部分人停在 CUDA C 層就滿足了，但這樣你永遠不知道 compiler 在幫你做什麼、也不知道它沒幫你做什麼。

三個場景迫使我們讀 PTX：

1. **性能怪異**：你以為 `__restrict__` 讓 compiler 生成了 read-only cache 存取（`ld.global.nc`），但 PTX 告訴你 compiler 根本沒信任你的標記。
2. **精確度問題**：FP32 的結果莫名不可重現？PTX 裡一個 `fma.rn.f32` 和你以為的分開 `mul + add` 差了一個 rounding。
3. **inline PTX**：寫 `asm("...")`、呼叫 `__shfl_xor_sync` 這類 warp primitive，要能對照 PTX 確認語義正確。

讀 PTX 不是為了逐行最佳化，而是為了驗證 compiler 的決策和你的意圖一致。

---

## 先建立直覺：PTX 在哪個層級

```
CUDA C (.cu)
    │  nvcc frontend / LLVM
    ▼
PTX (.ptx)          ← 我們現在在這裡
    │  ptxas (NVIDIA proprietary)
    ▼
SASS / cubin (.cubin)
    │  driver loader
    ▼
GPU 執行
```

PTX 對應 LLVM IR 的位置：它是 NVIDIA GPU 的「虛擬組語」，比 SASS 高階（ptxas 還可以大幅重排、合併、spill），但比 CUDA C 低階（沒有 C++ 物件、沒有 template 展開）。

如果你學過 compilers 或 SSA 最佳化課程，PTX 會立刻有親切感——暫存器無限、SSA 風格、每條指令型別明確。

---

## PTX 設計哲學

### 虛擬 ISA，跨架構可移植

NVIDIA 可以讓同一份 PTX 跑在 Turing（sm_75）、Ampere（sm_86）、Hopper（sm_90）上。驅動或 ptxas 在 JIT 或 offline 時才把 PTX 翻成目標架構的 SASS。這讓 `.ptx` 檔案可以「未來相容」——代價是 ptxas 的自由度極大，PTX 和最終 SASS 的關係不是逐行對應。

這個設計和 LLVM bitcode 的思路相同：中間表示承諾語義，不承諾指令映射。

### 無限虛擬暫存器，按型別分組

PTX 的暫存器型別是強制的，不是可選的：

| 宣告 | 型別 | 用途 |
|------|------|------|
| `.reg .pred %p<N>` | 1-bit predicate | 條件判斷，對應 GPU 的 predicate 暫存器 |
| `.reg .b32 %r<N>` | 32-bit 整數 | 一般整數計算 |
| `.reg .b64 %rd<N>` | 64-bit 整數 | 指標、64-bit 整數 |
| `.reg .f32 %f<N>` | 32-bit 浮點 | FP32 計算 |
| `.reg .f64 %fd<N>` | 64-bit 浮點 | FP64 計算（`fp64` 吞吐通常是 fp32 的 1/32） |

`%p<2>` 表示宣告 2 個 predicate 暫存器 `%p0, %p1`。角括號內是數量，不是索引。

### SSA 風格

每個虛擬暫存器只被賦值一次（除了 `.local` spill slot 這類例外）。這讓 ptxas 可以自由做暫存器配置（register allocation），把哪個虛擬暫存器映射到哪個實體暫存器是 ptxas 的事，不是你的事。

### PTX 版本控制：.version 和 .target

每份 PTX 開頭的兩行不是裝飾：

```ptx
.version 8.2    // PTX ISA 功能集；CUDA 12.x 對應 PTX 8.x
.target sm_75   // 目標微架構；決定哪些指令合法
```

`.version` 是功能門控（feature gate）。在 `.version 6.4` 的 PTX 裡寫了 PTX 7.x 才引入的 `cp.async.ca.shared.global`，ptxas 直接報錯。

`.target` 控制架構相容性。PTX **往上相容**（`sm_75` 的 PTX 可以被 ptxas 重編為 `sm_86`）但往下不保證——如果 PTX 用了 `sm_80` 才有的指令（例如 `cp.async`），在 `sm_75` 的 ptxas 拒絕。CUDA runtime 的 JIT 機制就依賴這個特性：`.fatbin` 裡夾帶的 PTX 在載入時被 JIT 成當前 GPU 的 SASS，讓同一個 binary 跑在比編譯時更新的 GPU 上，代價是第一次 kernel 啟動有 JIT 延遲。

---

## Memory State Spaces：PTX 最獨特的地方

這是 PTX 和 x86 組語最大的差異。x86 就一個線性位址空間；PTX 有七個 state space，每個有獨立的語義和硬體對應。

```
.reg        虛擬暫存器（on-chip，但 ptxas 決定配置，可能 spill 到 .local）
.global     全域記憶體（DRAM，所有 thread 共享）
.shared     共享記憶體（per-CTA on-chip SRAM，對應 __shared__）
.local      per-thread 本地記憶體（實際是 DRAM！不是 on-chip！）
.const      常數記憶體（DRAM + 唯讀 cache，64KB per-SM）
.param      kernel 參數（launch 時傳入的指標或純量）
.tex        紋理（已 deprecated，用 .global + texture object 取代）
```

**.local 是最大的命名陷阱**：名字叫「local」，直覺上以為是 on-chip，實際上是 DRAM 上的 per-thread spill 區域。當 ptxas 發現你的 kernel 用的暫存器超過硬體實體暫存器數量（Turing 每個 thread 最多 255 個），多出來的會 spill 到 `.local`，也就是回 DRAM。你在 PTX 看到 `ld.local.f32`，代表發生了 register spill，這是需要關注的性能警訊。

**.param 只存在 kernel 邊界**：`ld.param.u64 %rd0, [vector_add_param_0]` 這個動作只在 kernel 入口做一次，把 host 傳過來的指標值搬進虛擬暫存器。之後就用 `.global` 存取真正的資料。

**.shared 是 `__syncthreads()` 的操作對象**：shared memory 只在同一個 CTA（block）內的 thread 之間共享。跨 block 的 shared memory 互相看不到。

---

## 完整 Vector Add PTX 逐行解析

先看 CUDA source：

```cuda
// vector_add.cu
__global__ void vector_add(const float* __restrict__ a,
                           const float* __restrict__ b,
                           float* __restrict__ c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}
```

產生 PTX 的指令：

```bash
nvcc -ptx -arch=compute_75 vector_add.cu -o vector_add.ptx
# 在 Colab 或裝了 CUDA 12.x 的環境執行
# Godbolt.org 也支援：選 nvcc 12.x，flags 填 -ptx -arch=compute_75
```

以下是對應的 PTX 示意片段。**注意：這是示意片段，實際輸出依 nvcc 12.x -arch=compute_75 產生，以讀者重現為準。Colab 執行：`nvcc -ptx -arch=compute_75 vector_add.cu -o vector_add.ptx`**

```ptx
//
// 檔案頭：PTX 元資訊
//
.version 8.2          // PTX ISA 版本（CUDA 12.x 對應 PTX 8.x）
.target sm_75         // 目標架構；sm_75 = Turing（RTX 20xx / T4）
.address_size 64      // 所有指標是 64-bit（現代 GPU 均如此）

//
// kernel 宣告
// .visible = 讓 linker 可見（等同 extern "C"）
// .entry   = 這是從 host 呼叫的 kernel（vs. .func = device-only function）
//
.visible .entry vector_add(
    .param .u64 vector_add_param_0,   // const float* a
    .param .u64 vector_add_param_1,   // const float* b
    .param .u64 vector_add_param_2,   // float* c
    .param .u32 vector_add_param_3    // int n（注意：u32，不是 u64）
)
{
    //
    // 暫存器宣告區
    // PTX 要求在 kernel 開頭集中宣告所有虛擬暫存器
    // 數量是 ptxas 配置的上界，不是真正用到的實體暫存器數
    //
    .reg .pred  %p<2>;    // 2 個 predicate 暫存器：%p0, %p1
    .reg .b32   %r<6>;    // 6 個 32-bit 整數：%r0 ~ %r5
    .reg .f32   %f<4>;    // 4 個 FP32：%f0 ~ %f3
    .reg .b64   %rd<9>;   // 9 個 64-bit（指標用）：%rd0 ~ %rd8

    //
    // 讀取 kernel 參數
    // .param space 是特殊的 read-only 空間，只在 kernel 入口存取
    // 把指標值從 .param 搬進 .reg，之後才能做指標運算
    //
    ld.param.u64   %rd0, [vector_add_param_0];  // a 的指標值 → %rd0
    ld.param.u64   %rd1, [vector_add_param_1];  // b 的指標值 → %rd1
    ld.param.u64   %rd2, [vector_add_param_2];  // c 的指標值 → %rd2
    ld.param.u32   %r2,  [vector_add_param_3];  // n → %r2

    //
    // 計算 i = blockIdx.x * blockDim.x + threadIdx.x
    //
    // PTX 用「特殊暫存器」（special registers）存取執行緒索引
    // 這些暫存器在執行期被硬體填入，不需要也不能手動寫
    //
    mov.u32        %r0, %ctaid.x;      // blockIdx.x  → %r0
    mov.u32        %r1, %ntid.x;       // blockDim.x  → %r1
    mov.u32        %r3, %tid.x;        // threadIdx.x → %r3

    // mad = multiply-add
    // mad.lo.s32 %dst, %a, %b, %c 計算 dst = a * b + c（取低 32 位）
    // s32 代表 signed 32-bit；.lo = 乘積取低 32 位（丟棄 overflow）
    mad.lo.s32     %r4, %r0, %r1, %r3; // i = blockIdx.x * blockDim.x + threadIdx.x

    //
    // 邊界檢查：if (i < n)
    //
    // setp = set predicate
    // setp.ge.s32 %p0, %r4, %r2 → %p0 = (i >= n)
    // 注意：我們測的是「越界」條件，結果為 true 時要跳過計算
    //
    setp.ge.s32    %p0, %r4, %r2;     // %p0 = (i >= n)

    // @%p0 表示「在 %p0 為 true 時執行」
    // 若 i >= n（越界），跳到 $L__BB0_2（kernel 結束）
    @%p0 bra       $L__BB0_2;

    //
    // 計算位址：byte_offset = i * sizeof(float) = i * 4
    //
    // mul.wide.s32 的特殊性：
    //   輸入兩個 s32，輸出一個 s64（位元加寬，避免 overflow）
    //   因為 i * 4 可能超過 s32 的範圍（2^31 ≈ 2G 個元素 × 4 bytes = 8GB）
    //
    mul.wide.s32   %rd3, %r4, 4;      // byte_offset = i * 4（s32 → s64）

    // 計算各陣列元素的 64-bit 位址
    add.s64        %rd4, %rd0, %rd3;  // &a[i] = a_base + byte_offset
    add.s64        %rd5, %rd1, %rd3;  // &b[i] = b_base + byte_offset
    add.s64        %rd6, %rd2, %rd3;  // &c[i] = c_base + byte_offset

    //
    // 載入 a[i] 和 b[i] 從 global memory
    //
    // ld.global.f32：從 .global state space 載入 f32
    // 若有 __restrict__ 且 compiler 信任，可能生成 ld.global.nc.f32
    // （.nc = non-coherent = 走 read-only cache，即 L1 texture cache）
    //
    ld.global.f32  %f0, [%rd4];       // a[i] → %f0
    ld.global.f32  %f1, [%rd5];       // b[i] → %f1

    //
    // 核心計算：c[i] = a[i] + b[i]
    //
    // add.f32 是獨立的加法（無 fused multiply-add）
    // nvcc 有時會把 a + b 轉成 fma.rn.f32 %f2, %f0, 1.0, %f1
    // 這裡保留 add.f32 的寫法，對應最直接的翻譯
    //
    add.f32        %f2, %f0, %f1;     // %f2 = a[i] + b[i]

    //
    // 寫回 c[i] 到 global memory
    //
    st.global.f32  [%rd6], %f2;       // global mem[&c[i]] = %f2

$L__BB0_2:
    // kernel 結束，等同 C 的 return
    // 硬體會回收這個 thread 的資源
    ret;
}
```

### 三個細節值得特別說明

**`mul.wide.s32` 為什麼存在**：如果用 `mul.lo.s32 %r5, %r4, 4` 再用 `cvt.s64.s32 %rd3, %r5`，當陣列大於 5 億個 float（2GB）時，`i * 4` 會溢出 s32。`mul.wide.s32` 一步到位，輸入 s32 × s32，輸出 s64，這是處理大陣列時的標準寫法。

**`__restrict__` 不保證 `ld.global.nc`**：`const float* __restrict__ a` 理論上告訴 compiler 「a 不和其他指標別名」，compiler 可以走 read-only cache。但是否真的生成 `ld.global.nc.f32` 取決於 nvcc 的分析結果——有時分析保守，有時不生成。用 `__ldg(a + i)` 可以強制走 read-only cache，對應的 PTX 就會有 `.nc` 修飾符。

**`add.f32` vs `fma.rn.f32`**：`a[i] + b[i]` 是純加法，nvcc 可能直接輸出 `add.f32`，也可能輸出 `fma.rn.f32 %f2, %f0, 1.0f, %f1`（乘以 1.0 的 FMA 語義上等價）。差別在於如果你的運算是 `a * b + c`，強烈建議用 `__fmaf_rn(a, b, c)` 明確要求 FMA——這樣 PTX 裡一定出現 `fma.rn.f32`，不依賴 compiler 的 contraction 決策。

---

## 常用 PTX 指令速覽

### 整數運算

| 指令 | 語義 |
|------|------|
| `add.s32 %r0, %r1, %r2` | r0 = r1 + r2（signed 32-bit） |
| `mad.lo.s32 %r0, %r1, %r2, %r3` | r0 = (r1 * r2 + r3) & 0xFFFFFFFF |
| `mul.wide.s32 %rd0, %r1, %r2` | rd0 = (s64)(r1) * (s64)(r2)（位元加寬） |
| `setp.ge.s32 %p0, %r1, %r2` | p0 = (r1 >= r2) |
| `cvt.s64.s32 %rd0, %r0` | 型別轉換：s32 → s64（sign-extend） |

### 浮點運算

| 指令 | 語義 |
|------|------|
| `add.f32 %f0, %f1, %f2` | f0 = f1 + f2 |
| `mul.f32 %f0, %f1, %f2` | f0 = f1 * f2 |
| `fma.rn.f32 %f0, %f1, %f2, %f3` | f0 = f1 * f2 + f3（FMA，`.rn` = round-to-nearest，**必填**） |
| `sqrt.rn.f32 %f0, %f1` | f0 = sqrt(f1)（`.rn` 同樣必填） |

注意：FP 指令幾乎都需要 rounding mode 後綴（`.rn`, `.rz`, `.rm`, `.rp`）。省略會導致 ptxas 組譯錯誤。

### 記憶體存取

| 指令 | 語義 |
|------|------|
| `ld.global.f32 %f0, [%rd0]` | 從 global memory 載入 f32 |
| `ld.global.nc.f32 %f0, [%rd0]` | 同上，但走 read-only cache（L1 tex cache） |
| `st.global.f32 [%rd0], %f0` | 寫到 global memory |
| `ld.shared.f32 %f0, [%rd0]` | 從 shared memory 載入 |
| `st.shared.f32 [%rd0], %f0` | 寫到 shared memory |
| `ld.local.f32 %f0, [%rd0]` | 從 local memory（DRAM spill 區）載入 ← 警訊！ |

### 控制流

| 指令 | 語義 |
|------|------|
| `bra $label` | 無條件跳轉 |
| `@%p0 bra $label` | p0 為 true 時跳轉 |
| `@!%p0 bra $label` | p0 為 false 時跳轉 |
| `ret` | kernel / device function 返回 |
| `bar.sync 0` | 等同 `__syncthreads()`（CTA 內所有 thread 同步） |

### 特殊暫存器

| 暫存器 | 對應 CUDA C | 穩定性 |
|--------|------------|--------|
| `%tid.x / .y / .z` | `threadIdx.x / .y / .z` | 常數 |
| `%ctaid.x / .y / .z` | `blockIdx.x / .y / .z` | 常數 |
| `%ntid.x / .y / .z` | `blockDim.x / .y / .z` | 常數 |
| `%nctaid.x / .y / .z` | `gridDim.x / .y / .z` | 常數 |
| `%laneid` | warp 內的 lane 索引（0–31） | 穩定 |
| `%warpid` | SM 內的 warp slot 索引 | 不穩定，只適合 profiling |
| `%nwarpid` | SM 上的 warp slot 總數 | 常數 |
| `%smid` | 當前 SM 的 ID | block 存活期內穩定 |
| `%nsmid` | SM 總數 | 常數 |

`%laneid` 是 warp shuffle、reduction 的核心，範圍永遠 0–31，執行期不變。`%warpid` 指的是 SM 內的 warp slot 索引，有些微架構在 warp 被 descheduled 再喚回後 slot 可能改變，不能用來做工作分配。`%smid` 在同一個 thread block 的生命週期內穩定（block 不會被遷移到別的 SM），適合 persistent kernel 的 SM-local 資料分流，或 profiling 用的 SM histogram。

---

## PTX vs LLVM IR 對比表

| 概念 | LLVM IR | PTX |
|------|---------|-----|
| 虛擬暫存器 | `%0, %1`（統一命名空間） | `%r0, %f0, %rd0`（按型別分組） |
| 型別系統 | `i32, float, ptr` | `.b32, .f32, .u64`（位元/有符號/無符號分開） |
| 載入 | `load i32, ptr %p` | `ld.global.f32 %f0, [%rd0]`（state space 必填） |
| 儲存 | `store float %v, ptr %p` | `st.global.f32 [%rd0], %f0` |
| 條件跳轉 | `br i1 %cond, label %a, label %b` | `setp.XX + @%p bra $label`（兩步驟） |
| 函式入口 | `define void @foo(...)` | `.entry`（kernel）/ `.func`（device fn） |
| FMA | `llvm.fma.f32` intrinsic | `fma.rn.f32`（rounding mode 必填） |
| 記憶體模型 | 單一位址空間 + address space 屬性 | state space 是指令的一部分 |
| 並行語義 | 無（需外部擴充） | 內建 `%tid`, `%ctaid`, `%ntid`, `bar.sync` |
| Predication | 用 `select` 指令 | `@%p` 前綴，直接 predicate 任意指令 |

最大的結構差異是 state space：LLVM IR 用 `addrspace(N)` 作為指標屬性，而 PTX 把 state space 直接嵌入每條存取指令（`ld.global` vs `ld.shared`）。這讓 PTX 的意圖更明確，但也讓指令更冗長。

---

## PTX 的限制：ptxas 還會大改

讀 PTX 時需要記住一件事：**你看到的 PTX 不是最終執行的東西**。ptxas 拿到 PTX 後會做大量轉換：

- **暫存器配置**：把虛擬暫存器映射到實體暫存器；超出的 spill 到 `.local`（DRAM）。
- **指令融合**：多條 PTX 指令可能變成一條 SASS 指令（或反過來）。
- **排程重排**：ptxas 會調整指令順序隱藏記憶體延遲（memory latency hiding）。
- **常數摺疊**：已知常數的計算在 ptxas 階段就計算掉。
- **Predicate optimization**：複雜的條件分支可能被轉成 `ISETP` + `SEL` 的 SASS 序列。

這代表兩件事：首先，PTX 的暫存器數量宣告不等於實際用的實體暫存器數（需要看 `ptxas -v` 的輸出才知道）。其次，你在 PTX 看到的結構只是語義保證，ptxas 在語義保持下可以任意重排。下一章（Ch 28）看 SASS 時才是真正的執行面貌。

---

## 踩雷

**1. `.local` 是 DRAM，不是 on-chip**

`.local` state space 的名字讓人以為是「本地（on-chip）記憶體」，實際上是「每個 thread 私有的 DRAM 區域」，用來存 register spill。`ld.local.f32` 的延遲和 `ld.global.f32` 一樣是幾百個 cycle。在 PTX 裡看到 `.local` 存取，代表 kernel 的暫存器壓力過大，要考慮減少 kernel 的並行 warp（降低 occupancy 換取更多實體暫存器），或分拆 kernel。

**2. FP 指令的 rounding mode 是必填的**

`fma.rn.f32 %f0, %f1, %f2, %f3` 的 `.rn`（round-to-nearest-even）不是可選修飾符，是必填部分。`fma.f32` 沒有 rounding mode 會讓 ptxas 報錯。同樣，`sqrt.rn.f32`、`div.rn.f32` 都需要。這和 x86 不同——x86 的 rounding mode 是 MXCSR 暫存器的全局設定，PTX 每條指令自帶。

**3. PTX 暫存器型別嚴格，不允許隱式轉換**

`.b32` 和 `.f32` 在 PTX 裡是不同型別。你不能把一個 `ld.global.b32 %r0, [%rd0]` 的結果直接用在 `add.f32` 裡。需要用 `mov.b32 %f0, %r0` 做位元重新解釋（bitcast），或 `cvt.f32.s32 %f0, %r0` 做數值轉換。搞混會在 ptxas 時得到型別不匹配錯誤。

**4. `mad.lo.s32` 和 `fma.rn.f32` 語義不同**

這兩個都是「multiply-add」，但一個是整數（`.lo` = 截斷低 32 位），一個是 FP32 FMA（融合乘加，只有一次 rounding）。PTX 裡看到 `mad.lo.s32` 是在做整數索引計算；`fma.rn.f32` 才是浮點 FMA。不要看到「mad」就以為是浮點 FMA。

**5. `setp` 的輸出只能給 predicate 指令用**

`setp.ge.s32 %p0, %r4, %r2` 把比較結果存在 predicate 暫存器 `%p0`。`%p0` 不能用在 `add.s32`、`ld.global` 這類非 predicate 指令的輸入，也不能和 `%r0` 互相賦值。要把 predicate 轉成整數要用 `selp.s32 %r0, 1, 0, %p0`（conditional select）。Predicate 暫存器只服務 `@%p bra` 和 `selp` 這類條件語義，不能直接參與算術。

---

## 進階

**用 `nvcc -v` 看完整編譯過程**：

```bash
nvcc -v -ptx -arch=compute_75 vector_add.cu 2>&1 | grep "#\$ "
```

可以看到 nvcc 實際呼叫的 `cicc`（LLVM-based frontend，CUDA C → PTX）和 `ptxas`（PTX → cubin）的完整命令列，包括所有隱藏的 flag。

**Godbolt.org 是讀 PTX 最快的環境**：選 nvcc 12.x，輸入 CUDA C，flags 填 `-ptx -arch=compute_75`，右邊直接看 PTX。不需要 GPU，不需要 CUDA 環境，任何瀏覽器都能用。要看 SASS 的話選 `nvcc (CUDA) + cuobjdump`（第 28 章的事）。

**Device function 的 PTX 有 ABI 呼叫約定**：

```cuda
__device__ float helper(float a, float b) { return a + b; }
```

對應的 PTX 是 `.func`（不是 `.entry`），返回值和參數都用括號包住：

```ptx
// 示意片段，以讀者重現為準
.func (.param .f32 func_retval0) helper(
    .param .f32 helper_param_0,
    .param .f32 helper_param_1
)
{
    .reg .f32 %f<3>;
    ld.param.f32  %f0, [helper_param_0];
    ld.param.f32  %f1, [helper_param_1];
    add.f32       %f2, %f0, %f1;
    st.param.f32  [func_retval0], %f2;
    ret;
}
```

呼叫端語法是 `call (%retval), helper, (%p0, %p1);`——即使只有一個返回值，括號不能省。ptxas 可以自由 inline device function，加 `__noinline__` 強制保留函式邊界，便於在 PTX 裡辨識呼叫點。參數傳遞走 `.param` frame，但 ptxas 夠聰明時會把 store/load 優化掉，直接用暫存器傳（類似 x86-64 SysV ABI 的前幾個參數走暫存器）。

**Inline PTX（asm volatile）**：

有些 GPU 指令在 CUDA C 沒有對應 intrinsic，或者你要精確控制生成的 PTX，就用 `asm volatile`。語法借用 GCC inline asm，constraint 字母對應 PTX 暫存器型別：`"r"` = `.b32`（32-bit 整數），`"l"` = `.b64`（64-bit），`"f"` = `.f32`，`"d"` = `.f64`，`"h"` = `.b16`；輸出前加 `=`，讀寫加 `+`。

```cuda
// 示意片段，以讀者重現為準
// 用 inline PTX 做 warp butterfly reduction（__shfl_xor_sync 的底層）
__device__ float warp_sum(float val) {
    for (int delta = 16; delta >= 1; delta >>= 1) {
        float tmp;
        asm volatile(
            "shfl.sync.bfly.b32 %0, %1, %2, 0x1f, 0xffffffff;"
            : "=f"(tmp)         // 輸出：f32 暫存器
            : "f"(val), "r"(delta)  // 輸入：val (f32)、delta (b32)
        );
        val += tmp;
    }
    return val;
}
```

適合用 inline PTX 的場景：存取 `prmt.b32`（byte permute）、`lop3.b32`（3-input logic op）、`dp4a.u32.u32`（INT8 dot product）等 CUDA C 沒有直接 intrinsic 的指令；或強制指定 `ld.global.cs`（cache streaming，不污染 L2）這類 compiler 不一定會選的記憶體存取修飾符。如果 inline PTX 指令有 memory side effect（例如 `st.global`），在 clobber list 加 `"memory"` 防止 compiler 把其他記憶體操作重排進去。

**`__ldg()` 強制 read-only cache**：

```cuda
float val = __ldg(&a[i]);   // 對應 PTX: ld.global.nc.f32 %f0, [%rd4]
```

`.nc` = non-coherent，走 L1 texture cache（read-only cache）。這條 cache 不需要 L2 coherence，latency 比 regular L1 低，bandwidth 有時更高。適合唯讀的大陣列，且確定不會被同一 kernel 的其他 thread 寫入的場景。

---

## 動手練習

**練習 1**：用 Godbolt.org（nvcc 12.x，`-ptx -arch=compute_75`）分別編譯：

```cuda
// 版本 A
__global__ void add_a(float* c, float a, float b) { *c = a + b; }

// 版本 B
__global__ void add_b(float* c, float a, float b) { *c = __fmaf_rn(a, 1.0f, b); }
```

比較兩份 PTX，確認 `add.f32` vs `fma.rn.f32` 的差異。

**練習 2**：故意觸發 `.local` spill。寫一個宣告 300 個 local float 的 kernel（超過 Turing 的 255 個暫存器上限），用 `nvcc -ptx` 看輸出，找 `ld.local` / `st.local` 的存取。再用 `ptxas -v` 查看 spill 的統計訊息。

**練習 3**：寫一個用 `__shared__` 的 kernel（例如簡單的 tiled vector add），在 PTX 裡找 `.shared` state space 的 `ld.shared.f32` 和 `st.shared.f32`，以及 `bar.sync 0`（`__syncthreads()`）。確認 `.shared` 的宣告語法（`.shared .align 4 .b8 shmem[4096];`）。

---

## 本章重點

- PTX 是 NVIDIA 的虛擬 ISA，位於 CUDA C 和 SASS 之間，設計目標是跨架構可移植性。
- 虛擬暫存器按型別分組（`.pred`, `.b32`, `.f32`, `.b64`），SSA 風格，數量無限（ptxas 負責配置）。
- Seven state spaces 是 PTX 最獨特的設計：`.global`（DRAM 共享）、`.shared`（on-chip per-CTA）、`.local`（DRAM per-thread spill，不是 on-chip！）、`.param`（kernel 參數）、`.const`（唯讀 cache）。
- FP 指令的 rounding mode 是必填部分（`.rn`, `.rz`, `.rm`, `.rp`），省略會組譯錯誤。
- PTX 不是最終執行的東西：ptxas 還會做暫存器配置、指令排程、融合，和 SASS 差距可能很大。
- `.version` 控制 PTX ISA 功能集，`.target` 控制目標微架構；PTX 往上相容，新版本指令在舊 `.target` 下不合法。
- `%laneid`（0–31，穩定）用於 warp shuffle/reduction；`%smid`（block 存活期穩定）用於 SM-local 資料分流；`%warpid`（不穩定）只適合 profiling。
- Device function（`.func`）的返回值和參數都用括號包住，呼叫語法是 `call (%retval), fname, (%args);`；`asm volatile` inline PTX 用 constraint 字母指定暫存器型別。

---

## 自我檢核

1. `.local` state space 對應的硬體是什麼？為什麼在 PTX 看到 `ld.local` 是性能警訊？
2. `mul.wide.s32 %rd3, %r4, 4` 和 `mul.lo.s32 %r5, %r4, 4` 在什麼情況下結果不同？
3. 為什麼 `fma.rn.f32` 的 `.rn` 不能省略，但 x86 的 `VFMADD213SS` 不需要在指令裡指定 rounding mode？
4. PTX 的 `setp.ge.s32 %p0, %r4, %r2` + `@%p0 bra $L` 和 LLVM IR 裡怎麼對應？
5. 同一份 `.ptx` 檔案能跑在 sm_80（Ampere）上嗎？代價是什麼？
6. `%warpid` 和 `%smid` 的穩定性差異為何？為什麼不能用 `%warpid` 做 warp 間工作分配？
7. Inline PTX constraint `"=f"` 和 `"r"` 分別對應哪種 PTX 暫存器型別？何時需要在 clobber list 加 `"memory"`？

---

## 延伸閱讀

1. **PTX ISA 官方文件**（[docs.nvidia.com/cuda/parallel-thread-execution](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html)）—— State Spaces, Types, and Variables 章節是理解 `.reg`/`.global`/`.shared`/`.local` 的第一手資料；Memory Consistency Model 章節解釋 `.global` 的可見性語義。

2. **Godbolt.org nvcc 支援**（[godbolt.org](https://godbolt.org)）—— 選 nvcc 12.x，flag 填 `-ptx -arch=compute_75`。即時對照 CUDA C 和 PTX，是學習 PTX 最低摩擦的環境，不需要本地 CUDA 安裝。

3. **"Dissecting the NVIDIA Turing T4 GPU"**（arXiv 1903.07486）—— 透過 micro-benchmark 測量 Turing 微架構的 throughput、latency、cache 行為，幫助理解為什麼 PTX 的 `ld.global.nc.f32`（走 L1 tex cache）比 `ld.global.f32` 快，以及 `bar.sync` 的 warp synchronization 代價。

4. **CUDA C++ Programming Guide — Inline PTX Assembly**（[docs.nvidia.com/cuda/cuda-c-programming-guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#inline-ptx-assembly)）—— 當你需要在 CUDA C 裡直接嵌入 PTX（`asm volatile("..." ...)`），這份文件說明 constraint string 語法和 operand 型別對應。是 PTX 知識的直接應用場景。

---

## 銜接 Ch 28

我們現在能讀 PTX，知道 compiler 在語義層做了什麼決定。但 PTX 和 GPU 真正執行的指令之間還有一道牆：ptxas 把 PTX 轉成 SASS 時，指令格式完全換掉——沒有 state space 語法、沒有虛擬暫存器、有 GPU 特有的 stall cycle 和 yield bit。

[Ch 28 讀 SASS](./28-reading-sass.md) 我們用 `cuobjdump --dump-sass` 看同一份 vector add 的最終執行形式，對照 PTX，理解 ptxas 做了哪些轉換，以及為什麼 SASS 才是性能分析的真正起點。
