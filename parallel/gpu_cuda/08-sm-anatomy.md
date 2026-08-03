# Ch 8 — SM 剖析

> **目標**：拆開一個 Streaming Multiprocessor（SM）的蓋子，弄清楚 warp scheduler、register file、shared memory、Tensor Core、SFU 各自在哪裡、做什麼、怎麼互相卡死對方。知道這些之後，Ch 10 的 warp divergence、Ch 11 的 occupancy、Ch 13 的 shared memory bank conflict 才有根基。
>
> **環境**：Turing T4（sm_75）為主線，Ampere A100（sm_80）和 Hopper H100（sm_90）作對比。CUDA 12，Linux/WSL 均可。

---

## 為什麼需要這章

前一章（[Ch 7 GPU 架構總覽](./07-gpu-architecture-overview.md)）把 GPU 看成「很多個 SM + 共用記憶體」。那個視角夠用來理解 grid/block 分配，但不夠用來解釋為什麼你的 kernel 跑得慢：

- 為什麼把 register 用到 64 個就卡 occupancy？
- 為什麼 shared memory 分 96KB 和 32KB 兩種切法？
- Tensor Core 和 FP32 core 到底能不能同時開？
- SFU 做 `sinf()` 比 FP32 便宜還是貴？

這些問題的答案都在 SM 內部。這章直接把 Turing SM 解剖給你看。

---

## 先建立直覺：Turing SM 的內部結構

一個 Turing SM 是 4 個 processing block（NVIDIA 文件也叫 sub-partition）拼起來的。每個 processing block 是一個獨立的執行單位：

```
┌─────────────────────────────────────────────────────────────────┐
│                      Turing SM (sm_75)                          │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │   Processing Block 0  │  │   Processing Block 1  │            │
│  │  ┌─────────────────┐ │  │  ┌─────────────────┐ │            │
│  │  │  Warp Scheduler │ │  │  │  Warp Scheduler │ │            │
│  │  │  Dispatch Unit  │ │  │  │  Dispatch Unit  │ │            │
│  │  └────────┬────────┘ │  │  └────────┬────────┘ │            │
│  │           │           │  │           │           │            │
│  │  ┌────────▼────────┐ │  │  ┌────────▼────────┐ │            │
│  │  │  16 FP32 Cores  │ │  │  │  16 FP32 Cores  │ │            │
│  │  │  16 INT32 Cores │ │  │  │  16 INT32 Cores │ │            │
│  │  │  2 Tensor Cores │ │  │  │  2 Tensor Cores │ │            │
│  │  │  4 SFU          │ │  │  │  4 SFU          │ │            │
│  │  │  LD/ST Unit     │ │  │  │  LD/ST Unit     │ │            │
│  │  └─────────────────┘ │  │  └─────────────────┘ │            │
│  │  Register File (16K) │  │  Register File (16K) │            │
│  └──────────────────────┘  └──────────────────────┘            │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │   Processing Block 2  │  │   Processing Block 3  │            │
│  │  ┌─────────────────┐ │  │  ┌─────────────────┐ │            │
│  │  │  Warp Scheduler │ │  │  │  Warp Scheduler │ │            │
│  │  │  Dispatch Unit  │ │  │  │  Dispatch Unit  │ │            │
│  │  └────────┬────────┘ │  │  └────────┬────────┘ │            │
│  │           │           │  │           │           │            │
│  │  ┌────────▼────────┐ │  │  ┌────────▼────────┐ │            │
│  │  │  16 FP32 Cores  │ │  │  │  16 FP32 Cores  │ │            │
│  │  │  16 INT32 Cores │ │  │  │  16 INT32 Cores │ │            │
│  │  │  2 Tensor Cores │ │  │  │  2 Tensor Cores │ │            │
│  │  │  4 SFU          │ │  │  │  4 SFU          │ │            │
│  │  │  LD/ST Unit     │ │  │  │  LD/ST Unit     │ │            │
│  │  └─────────────────┘ │  │  └─────────────────┘ │            │
│  │  Register File (16K) │  │  Register File (16K) │            │
│  └──────────────────────┘  └──────────────────────┘            │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │          L1 Cache / Shared Memory (96KB unified)        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  L2 Cache (via interconnect)             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

每個 SM 合計：
  FP32 core：4 × 16 = 64
  INT32 core：4 × 16 = 64
  Tensor Core：4 × 2 = 8（第二代）
  SFU：4 × 4 = 16
  Warp Scheduler：4 個（每 cycle 各 issue 1 warp）
  Register File：4 × 16K = 64K（32-bit register）
```

4 個 processing block 不是對稱地跑同一個 warp，而是各自持有不同的 warp。一個 SM 最多 32 個 active warp，平均每個 processing block 管 8 個 warp。

---

## Processing Block 解剖

### Warp Scheduler + Dispatch Unit

每個 processing block 有 1 個 warp scheduler 和 1 個 dispatch unit。

Warp scheduler 的工作：每個 clock cycle，從這個 block 持有的 eligible warp 裡選一個，送給 dispatch unit。Eligible 的定義是：warp 的 operand 都 ready（register 已寫回、沒有 RAW hazard、沒有等記憶體）。

Dispatch unit 的工作：把選出來的 warp 的一條指令分派給對應的執行單元（FP32、INT32、Tensor Core、SFU、LD/ST）。

Turing 的 dispatch unit 是 1 個，意思是每個 processing block 每 cycle 最多 issue 1 條指令給 1 個 warp。這和 Volta 相同，但 Volta 多了 independent thread scheduling。

### FP32 Core 和 INT32 Core

Turing 的一個特色：FP32 core 和 INT32 core 分開，可以同時執行。這叫做 dual-issue FP32+INT32。如果你的 kernel 裡有 loop counter（INT32 加法）和浮點運算，Turing 可以在同一個 cycle 把兩件事都做完，不需要等待對方的 pipeline 空出來。

```cuda
// 這種 pattern Turing 能雙發射：
for (int i = 0; i < N; i++) {          // INT32 add 在 INT32 pipe
    output[i] = input[i] * 2.0f;       // FP32 mul 在 FP32 pipe
}
```

之前的架構（Pascal 之前）FP32 和 INT32 共用同一組 ALU，做完 FP32 才能做 INT32。Turing 把它們分開是一個硬體上的進步，但要讓編譯器真的發出 dual-issue 指令，你的程式碼必須讓兩條指令之間沒有資料相依性。

### SFU（Special Function Unit）

每個 processing block 有 4 個 SFU。SFU 負責：
- 三角函數：`__sinf()`、`__cosf()`
- 指數/對數：`__expf()`、`__logf()`
- 倒數：`__frcp_rn()`（即 `1/x`）
- 倒數平方根：`__frsqrt_rn()`（即 `1/sqrt(x)`）

SFU 的精度是 23-bit mantissa，而 IEEE 754 FP32 要求 24-bit。差一個 bit 的精度。這意味著：

```cuda
// __sinf() 的誤差最多 2 ulp，sinf() 才完整精度
float a = __sinf(x);    // 快，但 23-bit 精度（CUDA intrinsic）
float b = sinf(x);      // 慢，但完整 FP32 精度（呼叫 libm 近似）
```

SFU 執行一條指令需要 16 個 clock cycle（throughput 是 FP32 core 的 1/4）。一個 warp 有 32 個 thread，4 個 SFU 執行一個 warp 的 SFU 指令需要 8 個 cycle（32 thread / 4 SFU = 8 步）。

### LD/ST Unit（Load/Store Unit）

每個 processing block 有 LD/ST unit，負責：
- 讀寫 L1 cache / shared memory
- 發出全域記憶體（global memory）存取請求，經過 L2 到 DRAM

LD/ST unit 不是 DRAM 的瓶頸本身，它是「轉接頭」：把 warp 的記憶體請求打包，送進 L1/共享記憶體；如果 L1 miss，往 L2 繼續找。

---

## Register File 深挖

### 數字的意義

Turing SM 的 register file 是 64K 個 32-bit register。把它展開：

```
64K × 32-bit = 64 × 1024 × 4 bytes = 256 KB

分佈：4 個 processing block，每個 16K 個 register
每個 processing block：16K reg × 4 bytes = 64 KB
```

從另一個角度看：一個 SM 最多有 1024 個 active thread（32 warp × 32 thread）。如果每個 thread 用 32 個 register：
```
1024 thread × 32 reg = 32768 個 register，不到 64K 的一半
```

所以 64K register 不是「讓你爆用 register」，而是提供足夠的空間讓 SM 同時持有多個 warp 的 register（這是 latency hiding 的關鍵，Ch 11 會深挖）。

### Thread 看到的是虛擬 Register

從 thread 的角度，CUDA 編譯器最多允許 255 個 register（`nvcc` 的 `--maxrregcount` 限制在 255）。但 thread 看到的 register 是「邏輯 register」：編譯器把 `r0`、`r1`… 分配給你，由硬體把這些邏輯 register 對應到 physical register file 的位置。

這個對應是靜態的，在 kernel 啟動時就決定好。如果一個 thread 用 N 個 register，SM 在排程這個 block 時就預留了 `32 × N` 個 physical register（32 個 thread/warp × N）。預留是整塊的，不動態縮放。

這就是 occupancy 受 register 數量影響的原因：register 多 → 預留空間大 → SM 能同時持有的 warp 少 → occupancy 低。Ch 11 用公式算。

### Register File 的 Bank Conflict

Register file 也有 bank 的概念。Turing 的每個 processing block 的 register file 分成多個 bank，如果同一個 cycle 裡兩個 thread 要讀同一個 bank 的不同 register，會有 bank conflict，導致串行存取。這個層次的 bank conflict 通常對應到 warp 內的 register 分配，nvcc 的 scheduler 會盡量避免，但在高度相依的計算裡可能出現。

（這和 shared memory bank conflict 不同，後者是你可以直接控制的，Ch 13 深挖。）

---

## Shared Memory / L1 Unified Cache

### 歷史：為什麼合在一起

Fermi（2010）：shared memory 和 L1 分開，各自 16KB 或 48KB（可設）。
Kepler（2012）：分離設計，shared memory 上限 48KB，L1 48KB。
Maxwell（2014）：把 shared memory 和 L1 **合在一個 SRAM bank** 裡，統一管理。
Turing（2018）：96KB unified，可動態切分。

合在一起的好處：硬體不需要維護兩個獨立的存取路徑；L1 miss 的 cache line 可以直接放進 shared memory 空間（由 SM 管理），不需要複製到另一個 SRAM。壞處是兩者都在搶同一塊 SRAM，設定不對會讓其中一個餓死。

### Turing 的 96KB 切分方式

Turing 提供兩種主要切法（還有 8KB/16KB/32KB/64KB/100KB 等多個選項，透過 `cudaFuncSetAttribute` 設定）：

```
配置 A（預設）：
  Shared Memory：32KB
  L1 Cache：64KB

配置 B（shared memory 優先）：
  Shared Memory：64KB
  L1 Cache：32KB

極端配置：
  Shared Memory：96KB（L1 幾乎不存在）
  ← 需要 kernel 完全依賴 shared memory，不靠 L1
```

設定方式：

```cuda
// 在 kernel 啟動前呼叫：
cudaFuncSetAttribute(
    my_kernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    64  // 64KB shared memory
);
```

或者用 `__launch_bounds__` 間接影響（第二個參數 minBlocksPerMultiprocessor 讓編譯器推算 register 上限，進而調整 shared memory 分配）。

### Shared Memory 的 Bank 結構（預告）

Turing 的 shared memory 有 32 個 bank，每個 bank 的寬度是 4 bytes，每 cycle 可以從每個 bank 提供一個 4-byte 值。一個 warp 的 32 個 thread 同時存取 shared memory 時，如果每個 thread 對應不同的 bank，則 32 次存取可以在 1 個 cycle 完成（zero conflict）。如果多個 thread 打同一個 bank，就串行（bank conflict）。

Bank 的計算：`bank_id = (address / 4) % 32`。

Ch 13 會用實際的 matrix transpose 例子把這個講透。這裡記住一個數字：**32 bank × 4 byte = 128 byte，這是 shared memory 每個 cycle 的理論峰值頻寬（per SM）**。

---

## Tensor Core（第二代，Turing）

Tensor Core 是矩陣乘加（MMA, Matrix Multiply-Accumulate）的專用硬體：

```
D = A × B + C

其中：
  A：m×k 矩陣
  B：k×n 矩陣
  C, D：m×n 累加矩陣
```

Turing 第二代 Tensor Core 支援：
- **FP16 輸入，FP32 累加**（最常用）
- **INT8 輸入，INT32 累加**
- **INT4 輸入，INT32 累加**（推理加速）

從 WMMA（Warp-level Matrix Multiply-Accumulate）API 的角度，一個 warp 協作計算一個 16×16×16 的矩陣乘加：

```cuda
#include <mma.h>
using namespace nvcuda;

// 宣告 fragment（warp 共同持有的矩陣分塊）
wmma::fragment<wmma::matrix_a, 16, 16, 16, half, wmma::row_major> a_frag;
wmma::fragment<wmma::matrix_b, 16, 16, 16, half, wmma::col_major> b_frag;
wmma::fragment<wmma::accumulator, 16, 16, 16, float> c_frag;

wmma::fill_fragment(c_frag, 0.0f);

// 從 shared memory 或 global memory 載入
wmma::load_matrix_sync(a_frag, a_ptr, 16);
wmma::load_matrix_sync(b_frag, b_ptr, 16);

// 執行矩陣乘加：一個 warp 的 32 個 thread 協作完成
wmma::mma_sync(c_frag, a_frag, b_frag, c_frag);

wmma::store_matrix_sync(c_ptr, c_frag, 16, wmma::mem_row_major);
```

每個 Tensor Core 在一個 clock cycle 完成 4×4×4 的 FP16 矩陣乘加（= 4×4×4×2 = 128 個 FP16 FMA）。Turing 每個 processing block 有 2 個 Tensor Core，每 SM 共 8 個。

Tensor Core 的吞吐量相對於 FP32 core：在 FP16 下，理論上 Tensor Core 的 FP16 FMA throughput 約是 FP32 core 的 8 倍（因為 Tensor Core 的矩陣操作密度高，但需要規則的 16×16×16 tile）。

---

## Turing vs Ampere vs Hopper 對比表

| 特性 | Turing sm_75（T4） | Ampere sm_80（A100） | Hopper sm_90（H100） |
|------|-------------------|---------------------|---------------------|
| Processing Block / SM | 4 | 4 | 4 |
| FP32 Core / SM | 64 | 128 | 128 |
| INT32 Core / SM | 64 | 64 | 64 |
| Tensor Core / SM | 8（第二代） | 4（第三代） | 4（第四代） |
| Warp / SM（最大） | 32 | 64 | 64 |
| Thread / SM（最大） | 1024 | 2048 | 2048 |
| Block / SM（最大） | 16 | 32 | 32 |
| Register File / SM | 64K × 32-bit | 64K × 32-bit | 64K × 32-bit |
| L1/Shared / SM | 96KB unified | 192KB unified | 256KB unified |
| Shared Memory 最大 | 96KB | 164KB | 228KB |
| Tensor Core 輸入格式 | FP16, INT8, INT4 | FP16, BF16, TF32, INT8 | FP16, BF16, TF32, FP8, INT8 |
| 獨立 Thread 排程 | 無（有 sub-warp） | 無 | 無 |
| Volta-style ITS | 無 | 無 | 無 |
| Thread Block Cluster | 無 | 無 | 有 |
| SM 數量（旗艦） | 40（T4） | 108（A100） | 132（H100 SXM5） |
| GPU 總 FP32 TFLOPS | ~8.1（FP16 下 130 TOPS） | ~19.5 | ~67 |

**幾個關鍵差異的解讀：**

Ampere 的 FP32 core 翻倍（64 → 128）：因為 Ampere 讓每個 processing block 裡一組 16 個 FP32 core 可以「假裝是 FP32+FP32 雙寬度」，或在 BF16 模式下每個 FP32 core 做 2 個 BF16 乘加，相當於 256 個 BF16 FMA/SM/cycle。

Ampere 的 Tensor Core 數量從 8 降到 4，但第三代 Tensor Core 每個更強：支援 TF32（19-bit 精度），並且 FP16/BF16 throughput 大幅提升。

Hopper 的 Thread Block Cluster：允許不同 SM 的 block 共享 distributed shared memory，透過 NVLINK Fabric 互連。這是 Ch 9 會提到的，但不深挖：它讓某些需要跨 SM 通訊的演算法不再需要先 global sync 再重新分配 block。

---

## 踩雷

**1. 把 register per thread 設太高，occupancy 崩掉**

每個 thread 超過 32 個 register，SM 能同時持有的 warp 就開始減少。超過 64 個 register，active warp 可能降到 16 或更少。`nvcc -O3` 預設不限 register 數，激進的 register 使用（unrolled loop + 大量暫存值）很容易踩到這個坑。

```bash
# 用 nvcc 查看 register 使用量：
nvcc -O3 --ptxas-options=-v kernel.cu
# 輸出：ptxas info: Used 64 registers, 48384 bytes smem, ...
```

用 `--maxrregcount=32` 強制限制，或在程式碼裡 `__launch_bounds__(256, 2)`。

**2. 以為 SFU 指令是「免費的」**

`sinf()`、`expf()` 等在 SFU 上執行，但 throughput 是 FP32 core 的 1/4（每 SM 4 個 SFU vs 64 個 FP32 core）。SFU-heavy 的 kernel 很容易讓 SFU 成為瓶頸，而 FP32 core 卻在閒置。Nsight Compute 的 `sm__inst_executed_pipe_xu` 計數器可以看到 SFU 的 issue rate。

**3. Shared memory 設定沒生效**

`cudaFuncSetAttribute` 必須在 kernel 啟動之前呼叫，而且要傳正確的 kernel function pointer（不是 string）。常見的錯誤是設定了之後 kernel 還是跑在 32KB shared memory 的設定上，因為呼叫了錯誤的 function pointer 或放在錯誤的時機點。用 `cudaFuncGetAttributes` 驗證設定。

**4. Tensor Core 要求 16 的倍數維度**

WMMA 要求矩陣維度是 16 的倍數（16×16×16 tile）。如果你的矩陣不是 16 的倍數，需要手動 padding。直接用 cuBLAS 或 CUTLASS 則由函式庫處理 padding，不需要自己管。

**5. 同一個 SM 的 4 個 processing block 不互相可見**

Shared memory 是整個 SM 共用的，一個 block 裡所有 thread（不管分佈在哪個 processing block）都能存取同一塊 shared memory。但 register file 是每個 processing block 自己的；block 內的 thread 根據 warp 編號被分配到不同的 processing block，不能直接讀另一個 processing block 的 register。這是顯而易見的，但偶爾會有人以為可以「warp 間共享 register」。

---

## 底層機制：Warp 排程的細節

Turing 的每個 processing block 的 warp scheduler 是 greedy scheduler：每個 cycle 從 eligible warp 裡選優先級最高的一個。優先級的排定通常是 round-robin，但 NVIDIA 的細節未公開。

關鍵是「eligible」的條件：
1. warp 的指令 PC 指向的指令，其所有來源 operand 都已 ready（register 寫回完成）
2. 目標執行單元（FP32、Tensor Core 等）不被 warp scoreboard 鎖住
3. 指令所需的執行單元這個 cycle 沒有被另一個 warp 佔用

FP32 的 pipeline latency 是 4 個 cycle（Volta/Turing）。意思是，如果 warp A 在 cycle 0 發出 FP32 add，它的結果在 cycle 4 才 ready。在這 4 個 cycle 裡，scheduler 可以選 warp B、C、D 執行，讓 FP32 core 不閒置。這就是 latency hiding 的核心機制，Ch 11 會把這個算成 occupancy 需求。

---

## 進階

### Instruction-Level Parallelism（ILP）的角色

即使只有 1 個 warp，Turing 也可以雙發射 FP32+INT32 指令（如果這兩條指令之間沒有資料相依）。所以 occupancy 低的 kernel 不一定就跑得慢：如果單個 warp 的 ILP 夠高，可以撐起整個 SM 的利用率。這個 trade-off 在 Ch 11 裡會用 roofline model 分析。

### Register File 的頻寬

Register file 的存取頻寬遠高於 L1 cache。T4 的 register file 每個 processing block 每 cycle 可以讀 2 個來源 operand 和寫 1 個目標 operand（對每個執行的指令）。以 64 FP32 core / SM、clock 1590 MHz 估算，register file read bandwidth 大約是幾 TB/s，這是 L1 cache 的數十倍。

### SASS vs PTX

Tensor Core 的 WMMA API 最終編譯成 SASS（Shader ASSembly）的 `HMMA` 指令。PTX 的 `wmma.mma.sync` 是抽象層；真正驅動 Tensor Core 的是 `HMMA.16816.F32` 這類 SASS 指令。用 `cuobjdump --dump-sass` 可以看到實際的指令流。

---

## 動手練習

### 練習 1：查看你的 kernel 的 register 使用量和 occupancy

```bash
# 編譯並查看 register 使用：
nvcc -O3 --ptxas-options=-v -arch=sm_75 your_kernel.cu -o out
# 尋找輸出中的 "Used N registers"

# 用 Nsight Compute 查 occupancy：
ncu --metrics sm__warps_active.avg.pct_of_peak_sustained_active ./out
```

### 練習 2：測量 SFU 的 throughput 差距

```cuda
// kernel A：純 FP32 加法
__global__ void fp32_kernel(float* out, float* in, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) out[i] = in[i] * 2.0f + 1.0f;
}

// kernel B：SFU 密集（sin）
__global__ void sfu_kernel(float* out, float* in, int N) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < N) out[i] = __sinf(in[i]);
}
```

用 `cudaEvent_t` 計時，比較兩者的執行時間差距。預期 SFU kernel 會慢 3-4 倍（SFU throughput 是 FP32 的 1/4）。

### 練習 3：調整 shared memory 切分比例並測量影響

```cuda
// 把下面的 shared_size 改成 32, 64, 96KB，觀察 L1 miss rate 變化：
cudaFuncSetAttribute(my_kernel,
    cudaFuncAttributePreferredSharedMemoryCarveout,
    shared_size_kb);

// 啟動 kernel 後用 Nsight Compute 看：
// l1tex__t_sector_hit_rate.pct
```

你應該會看到：增加 shared memory → L1 hit rate 下降（L1 變小了），但如果你的 kernel 主要靠 shared memory，整體 throughput 反而上升。

### 練習 4：WMMA 矩陣乘法

參考 CUDA samples 裡的 `cudaTensorCoreGemm`，改成用你的資料試跑，比較 Tensor Core 版本和純 FP32 GEMM 的速度差距（T4 上預期 Tensor Core 快 4-8 倍，取決於矩陣大小）。

---

## 本章重點

- Turing SM 是 4 個 processing block 的集合。每個 processing block 有獨立的 warp scheduler、16 FP32 + 16 INT32 + 2 Tensor Core + 4 SFU + LD/ST unit 和自己的 register file partition（16K register）。
- 64K register file 是 SM 的最大 on-chip 資源，register 用多 → 能同時 active 的 warp 少 → occupancy 受限。
- Shared memory 和 L1 cache 共用 96KB SRAM（Turing），切分比例透過 `cudaFuncSetAttribute` 設定。Shared memory 有 32 bank，bank conflict 在 Ch 13 深挖。
- SFU 做 sin/cos/exp/rcp，精度 23-bit（不是完整 FP32），throughput 是 FP32 core 的 1/4。
- Tensor Core 第二代（Turing）支援 FP16/INT8 矩陣乘加，WMMA API 提供 warp-level 的 16×16×16 tile。
- Ampere：FP32 core 翻倍、shared memory 翻倍、warp/SM 翻倍、Tensor Core 升第三代（TF32/BF16）。
- Hopper：加入 Thread Block Cluster（跨 SM 的 distributed shared memory），Tensor Core 第四代支援 FP8。

---

## 自我檢核（主動回憶）

完成這章後，合上筆記自問：

1. Turing SM 裡有幾個 warp scheduler？每個 cycle 每個 scheduler 能 issue 幾個 warp？
2. 如果一個 kernel 每個 thread 用 128 個 register，Turing SM 最多能 active 幾個 warp？（假設 SM 有 64K register）
3. Shared memory 的 32 bank 和 L1 cache 有什麼關係？調大 shared memory 一定讓 kernel 更快嗎？
4. `sinf()` 和 `__sinf()` 有什麼差？在什麼場合會在乎這個差距？
5. Ampere 的 Tensor Core 數量比 Turing 少（4 vs 8），但為什麼 Ampere 的 Tensor Core 整體 throughput 更高？
6. Thread Block Cluster 是 Hopper 的什麼新功能？它解決了什麼問題？

---

## 延伸閱讀

1. **NVIDIA Turing Architecture Whitepaper**（2018）— 圖 10 是 SM 的官方架構圖，和本章的 ASCII 圖對照閱讀。搜尋 "NVIDIA Turing GPU Architecture Whitepaper"。

2. **CUDA C++ Programming Guide, §Compute Capabilities**（docs.nvidia.com）— sm_75、sm_80、sm_90 各有一個表格列出完整規格：register/SM、shared memory/SM、warp/SM 上限、每 cycle throughput。這是最權威的數字來源。

3. **《Programming Massively Parallel Processors》，4th ed.（Kirk & Hwu），Ch 4 §GPU Architecture** — 用課本語言解釋 SIMT 和 SM 的關係，比 whitepaper 更適合初學，但數字偶爾落後硬體。

4. **"Dissecting the NVidia Turing T4 GPU via Microbenchmarking"（arXiv 1903.07486）** — 用微基準測量 T4 的 register file 延遲、shared memory 延遲、LD/ST latency，用實測驗證官方規格的數字。讀完這篇你會知道 whitepaper 沒說的那些延遲細節。

5. **NVIDIA Ampere Architecture Whitepaper**（2020）— 和 Turing whitepaper 並排看，找出 FP32 core 翻倍、shared memory 倍增、Tensor Core 第三代的演進邏輯。

---

## 銜接

這章把 SM 的「零件清單」講完了。知道零件之後，接下來兩章把它們在效能層面武器化：

**[Ch 9 記憶體階層](./09-memory-hierarchy.md)** — 從 register → shared memory → L1 → L2 → DRAM，把每一層的容量、延遲、頻寬數字記起來。知道 LD/ST unit 的另一端連著什麼。

**Ch 10**（warp divergence）— if/else 在 SM 裡實際發生什麼，predication 和 branch divergence 怎麼影響 processing block 的利用率。

**Ch 11**（occupancy）— 把 register 數量、shared memory 大小、block size 代進 SM resource budget 公式，算出實際 occupancy，再看 occupancy 和 throughput 的關係是不是線性的（通常不是）。

**Ch 13**（shared memory bank conflict）— 這章預告了 32 bank 的概念，Ch 13 用 matrix transpose 把整個 bank conflict 分析從頭到尾走一遍，並提供用 `%` 運算消除 conflict 的技巧。

→ [Ch 9 記憶體階層](./09-memory-hierarchy.md)
