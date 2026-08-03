# Ch 31 現代特性：cp.async、TMA 與 Thread Block Cluster

> **硬體對應**：本章所有新特性都有明確的架構下限。cp.async 是 Ampere（sm_80）起，TMA 和 Thread Block Cluster 是 Hopper（sm_90）起。**T4（sm_75）完全不支援本章任何內容**，這不是筆誤，也不是作者的偏好——是架構邊界。

上一章（Ch 30）把 Tensor Core 的吞吐量數字列出來了：A100 FP16 GEMM 峰值 312 TFLOPS。問題是：光有計算單元不夠，你還需要把資料送進去。資料搬移路徑是 **DRAM → L2 → L1/Shared Memory → Register File → Tensor Core**，每一層都是潛在瓶頸。Turing（sm_75）的搬移機制在這條路徑上有一個根本性的效率問題，本章說的三個特性都是為了修這個問題而生的。

---

## 31.1 問題的根源：Turing 的 LDG + STS 路徑

在 Turing（也就是 T4 所在的世代），把資料從 global memory 搬到 shared memory 的標準路徑是這樣的：

```ptx
LDG.E.SYS  R0, [R2]    ; 1. global memory → register（~28–500 cycle latency）
STS.128    [smem], R0   ; 2. register → shared memory（~20 cycle）
```

這個路徑有兩個具體問題：

**問題 1：Register bandwidth 浪費。** LDG 把資料打到 register，STS 再從 register 打到 shared memory。中間的 register 在這段時間內被佔用——它不能被其他計算指令使用，因為你必須等 STS 完成才能釋放這個 register 給別人。Tensor Core 的 WMMA 指令需要大量 register（每個 fragment 就是一堆 register），這種「搬資料的過客 register」會和計算的 register 搶空間。

**問題 2：Warp scheduler 的計算機會被壓縮。** LDG 是 variable-latency 指令（Ch 29 說過），warp 在等 LDG 完成期間，理論上可以被切換去做別的事。但如果 register file 已經很緊張（occupancy 高、每個 warp 的 register 少），scheduler 能切換到的其他 warp 本身也在等類似的 LDG，互相堵死。

本質上：**搬資料的工作不應該讓 SM 的計算核心「陪等」**，但 LDG+STS 做不到這件事，因為資料必須先停在 register 上。

---

## 31.1.1 量化：T4 的 ridge point 計算

「Turing 世代開始感受到搬資料壓力」需要數字支撐。T4 規格：GDDR6 DRAM bandwidth ~320 GB/s，FP16 Tensor Core 峰值 65 TFLOPS。

**Ridge point**（計算剛好跟上 memory bandwidth 的 AI 邊界）：

```
Ridge = 65 TFLOPS / 320 GB/s ≈ 203 FLOP/Byte
```

GEMM 方陣的 arithmetic intensity ≈ N/3（FP16），N = 600 以上就超過 203，乍看應該輕鬆 compute-bound。**但 T4 的瓶頸不在 DRAM，在 register file bandwidth。** 每個 SM 的 register file 理論頻寬約 86 GB/s（~64 bytes/cycle × 1350 MHz），低於 shared memory 自身的 170 GB/s/SM。

LDG→register→STS 的路徑讓 register file 成為中間人：tile 資料必須先落地 register，再寫進 shared memory，register bandwidth 直接限制了搬移速率。Tensor Core 還沒等到資料就閒下來。

cp.async（Ampere sm_80+ 特性，T4 sm_75 不支援）解決這件事：DMA unit 直接把 DRAM 資料搬進 shared memory，register file 完全不介入。

---

## 31.2 cp.async：讓硬體自己搬資料

### 機制

cp.async 是 Ampere（sm_80+）新增的 PTX 指令，它的語義是：**硬體 DMA unit 直接把 global memory 的資料搬到 shared memory，完全繞過 register file。** Thread 發出 cp.async 之後可以立刻繼續執行下一條指令，不需要等搬移完成。

PTX 語法：

```ptx
; Cache All (.ca)：大小只能是 4、8、或 16 bytes
cp.async.ca.shared.global [dst_smem], [src_gmem], 4;
cp.async.ca.shared.global [dst_smem], [src_gmem], 8;
cp.async.ca.shared.global [dst_smem], [src_gmem], 16;

; zero-fill variant：如果 src_size < 16，剩餘位元填 0
cp.async.ca.shared.global [dst_smem], [src_gmem], 16, src_size;

; Cache Global (.cg)：大小只能是 16 bytes，不支援 4/8
cp.async.ca.shared.global [dst_smem], [src_gmem], 16;
cp.async.cg.shared.global [dst_smem], [src_gmem], 16;
```

**注意 .ca 和 .cg 的差異**：`.ca`（cache all）允許 L1 cache，大小 4/8/16 bytes 都可以；`.cg`（cache global）繞過 L1、只用 L2，大小**強制是 16 bytes**，用 4 或 8 會在 PTX 編譯階段出錯。大多數 GEMM 實作用 `.cg`，因為 L1 對 GEMM 這種 streaming 存取模式幫助有限。

### 同步：commit_group 和 wait_group

cp.async 是非同步的，你需要機制知道「資料搬完了沒」。PTX 提供 group 機制：

```ptx
cp.async.commit_group;      ; 關閉當前 async-copy group，後續的 cp.async 進新 group
cp.async.wait_group 1;      ; 等到最多 1 個 group 仍在 pending（其他全部完成）
cp.async.wait_all;          ; 等所有 async copies 完成
```

`wait_group N` 的語義要說清楚：它的意思是「等到 pending group 數量 ≤ N」，不是「等 N 個 group 完成」。如果你想確保某個 group 完成，要呼叫 `wait_group 0`（等到沒有 pending group），或者在 double-buffer 場景用 `wait_group 1`（允許下一個 group 仍在跑）。這個語義很多人第一次讀會搞錯。

### CUDA C++ 高階 API

直接寫 PTX 很麻煩，CUDA 12 提供兩種 C++ 封裝：

```cpp
// 方式 1：cooperative_groups（適合整個 block 協同搬一塊資料）
#include <cooperative_groups/memcpy_async.h>

auto block = cooperative_groups::this_thread_block();
cooperative_groups::memcpy_async(block, dst_smem, src_gmem, sizeof(float) * n);
// ... 繼續計算，不會在這裡卡住 ...
cooperative_groups::wait(block);   // 這裡才等
```

```cpp
// 方式 2：pipeline API（適合 double-buffer，控制粒度更細）
#include <cuda/pipeline>

auto pipe = cuda::make_pipeline();
cuda::memcpy_async(dst_smem, src_gmem, cuda::aligned_size_t<16>{size}, pipe);
pipe.producer_commit();   // 相當於 commit_group
// ... 計算 ...
pipe.consumer_wait();     // 相當於 wait_group 0
pipe.consumer_release();
```

pipeline API 的 `producer_commit` / `consumer_wait` 就是 `commit_group` / `wait_group` 的 C++ 包裝，但 API 的 stage 管理更清楚，double-buffer 寫起來不容易出錯。

---

## 31.3 Double Buffer Pipelining：完整 Kernel 骨架（Ampere sm_80+ 特性）

cp.async 的非同步性讓「搬下一個 tile 的同時計算當前 tile」成為可能，這個技術叫 software pipelining，最常見形式是 double buffer：

```
iteration 1: [compute tile 0]  [prefetch tile 1]
iteration 2: [compute tile 1]  [prefetch tile 2]
iteration 3: [compute tile 2]  [prefetch tile 3]
```

Stage N 的計算和 Stage N+1 的 prefetch 同時進行，硬體 DMA 搬資料、SM 計算核心做矩陣乘法，兩件事互不干擾。以下是可在 A100/RTX 3090+ 直接編譯的完整骨架（nvcc -arch=sm_80 -std=c++17）：

```cpp
// 編譯需要 CUDA 11.4+，libcu++ 標頭 <cuda/pipeline>
#include <cuda/pipeline>
#include <cooperative_groups.h>
#include <mma.h>
using namespace nvcuda;

constexpr int BM = 128, BN = 128, BK = 32, STAGES = 2;

// __align__(128)：cp.async.cg 要求 dst 128-byte 對齊（至少 16-byte，但 128 更安全）
__shared__ __align__(128) __half smem_A[STAGES][BM * BK];
__shared__ __align__(128) __half smem_B[STAGES][BK * BN];

__global__ void pipeline_gemm_kernel(
    const __half* __restrict__ A, const __half* __restrict__ B,
    float* __restrict__ C, int M, int N, int K)
{
    // pipeline_shared_state 放 smem，block 內所有 thread 共享同一個 stage 計數器
    __shared__ cuda::pipeline_shared_state<cuda::thread_scope_block, STAGES> shared_state;
    auto pipe = cuda::make_pipeline(
        cooperative_groups::this_thread_block(), &shared_state);

    const int tile_row = blockIdx.y * BM, tile_col = blockIdx.x * BN;
    const int K_tiles  = K / BK;
    const int tid      = threadIdx.x + threadIdx.y * blockDim.x;

    wmma::fragment<wmma::accumulator, 16, 16, 16, float> acc_frag;
    wmma::fill_fragment(acc_frag, 0.0f);

    // Prologue：每個 thread 搬 16 bytes（一個 cp.async.cg），拼出整個 stage 0
    pipe.producer_acquire();
    cuda::memcpy_async(smem_A[0] + tid * 8,
                       A + (tile_row + tid * 8 / BK) * K + tid * 8 % BK,
                       cuda::aligned_size_t<16>{16}, pipe);
    cuda::memcpy_async(smem_B[0] + tid * 8,
                       B + (tid * 8 / BN) * N + tile_col + tid * 8 % BN,
                       cuda::aligned_size_t<16>{16}, pipe);
    pipe.producer_commit();

    for (int k = 0; k < K_tiles; ++k) {
        int curr = k % STAGES, next = (k + 1) % STAGES;

        // 預取下一 tile（非同步，硬體 DMA 送出即返回）
        if (k + 1 < K_tiles) {
            pipe.producer_acquire();
            cuda::memcpy_async(smem_A[next] + tid * 8,
                               A + (tile_row + tid * 8 / BK) * K
                                 + (k + 1) * BK + tid * 8 % BK,
                               cuda::aligned_size_t<16>{16}, pipe);
            cuda::memcpy_async(smem_B[next] + tid * 8,
                               B + ((k + 1) * BK + tid * 8 / BN) * N
                                 + tile_col + tid * 8 % BN,
                               cuda::aligned_size_t<16>{16}, pipe);
            pipe.producer_commit();
        }

        // consumer_wait()：底層 = cp.async.wait_group(STAGES-1)
        // 等到 pending group 數 ≤ STAGES-1，即 curr stage 完成
        pipe.consumer_wait();
        __syncthreads();   // consumer_wait 只保證搬移完成，不保證所有 thread 到齊

        wmma::fragment<wmma::matrix_a, 16, 16, 16, __half, wmma::row_major> a_frag;
        wmma::fragment<wmma::matrix_b, 16, 16, 16, __half, wmma::col_major> b_frag;
        wmma::load_matrix_sync(a_frag, smem_A[curr], BK);
        wmma::load_matrix_sync(b_frag, smem_B[curr], BN);
        wmma::mma_sync(acc_frag, a_frag, b_frag, acc_frag);

        pipe.consumer_release();
        __syncthreads();
    }

    wmma::store_matrix_sync(C + tile_row * N + tile_col, acc_frag, N, wmma::mem_row_major);
}
```

三個必須理解的設計點：
- `pipeline_shared_state<thread_scope_block, STAGES>` 必須在 smem，不能在 local memory。它維護 STAGES 個 slot 的 pending 計數，是整個 double-buffer 狀態機的核心。
- `producer_acquire` / `producer_commit` 包住搬資料，`consumer_wait` / `consumer_release` 包住計算。library 自動對應哪個 slot 給哪個 k，不需要手算。
- `__syncthreads()` 在 `consumer_wait()` 之後不可省略。`consumer_wait` 保證 DMA 完成（記憶體語義），但不是 block-level barrier，不能保證所有 thread 都看到更新後的 smem。

---

## 31.3.2 PTX 層 pipeline 同步機制詳解

`cuda::pipeline` 的 `producer_commit` / `consumer_wait` 在 PTX 層展開是這樣的：

```ptx
; producer_commit() → 關閉當前 group
cp.async.commit_group;

; consumer_wait() 在 STAGES=2 的情況下展開為：
; 允許至多 1 個 group pending（就是剛 commit 的 next-stage prefetch）
cp.async.wait_group 1;
```

double-buffer 的 group pending 狀態用時間軸表示：

```
時間軸（Prologue 已填好 Group 0，主迴圈從 k=0 開始）

k=0: producer commit → [Group 1: tile-1] pending
     consumer wait_group 1 → Group 0 done（pending=1 ≤ 1，通過）
     COMPUTE tile-0   ← 和 Group 1 的 prefetch 重疊
k=1: producer commit → [Group 2: tile-2] pending
     consumer wait_group 1 → Group 1 done（pending=1 ≤ 1）
     COMPUTE tile-1   ← 和 Group 2 的 prefetch 重疊
...
```

關鍵洞察：`wait_group 1` 讓「compute tile-k」和「prefetch tile-(k+1)」真正同時進行。如果誤寫成 `wait_group 0`，每次都要等到包含 next-stage 的所有 group 完成，prefetch 的異步性就消失了，退回到同步搬移。如果誤寫成 `wait_group 2`（STAGES=2 時），tile-k 的資料可能還在搬移中就開始計算，讀到未定義值。

`wait_group N` 的安全用法規則：**N = STAGES - 1**。double-buffer 用 1，triple-buffer 用 2，以此類推。

---

## 31.3.3 async barrier（cuda::barrier）的具體用法

除了 `cuda::pipeline`，CUDA 12 提供另一個同步原語：`cuda::barrier`。它的本質是 **mbarrier** 的 C++ 封裝，Hopper TMA 用同一套機制，所以理解它是日後看 TMA 程式碼的地基。（Ampere sm_80+ 特性，T4 sm_75 不支援。）

```cpp
#include <cuda/barrier>

__global__ void barrier_example(const float* src, float* dst) {
    // barrier 必須放 smem；init 只能由一個 thread 執行
    __shared__ cuda::barrier<cuda::thread_scope_block> bar;
    __shared__ float smem[1024];

    if (threadIdx.x == 0) init(&bar, blockDim.x);
    __syncthreads();   // 讓 init 對所有 thread 可見

    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    // 把 bar 傳進 memcpy_async：DMA 完成時自動對 bar arrive（計數 -1）
    cuda::memcpy_async(smem + threadIdx.x, src + idx, sizeof(float), bar);

    // arrive_and_wait：我完成自己的工作 + 等所有 thread 都 arrive（計數歸零）
    bar.arrive_and_wait();
    // 到這裡，所有 DMA 都完成，smem 安全可讀
    dst[idx] = smem[threadIdx.x] * 2.0f;
}
```

`cuda::barrier` 和 `cuda::pipeline` 的場景差異：`pipeline` 管多 stage 的 prefetch 計數，需要 acquire/commit/wait/release 四個動作；`barrier` 只是「所有人到齊再繼續」，語義更扁平。A100 GEMM 用 `pipeline`，H100 TMA kernel 直接用底層 mbarrier，因為 TMA PTX 指令的 `mbarrier::complete_tx` qualifier 就是對 mbarrier 做 arrive，和 `cuda::barrier` 是同一套硬體計數器。

---

## 31.4 TMA（Tensor Memory Accelerator）：更極端的自主搬移

### cp.async 還不夠的地方

cp.async 的粒度是「一個 thread 搬一小塊資料」。在 GEMM kernel 裡，通常是 block 裡的所有 thread 分工合力搬一個 tile，每個 thread 負責幾個 element。這需要仔細計算每個 thread 的 offset，也需要整個 block 協同 commit/wait。程式碼複雜，也有一些協調開銷。

Hopper（sm_90）的 TMA 採用更激進的設計：**只需要一個 leader thread 描述整個 tensor 的搬移，硬體自動處理 2D/3D/4D/5D tensor 的 stride、padding、swizzle，其他 thread 繼續做計算。**

### CUtensorMap descriptor

TMA 在開始之前需要在 host 端建立一個 descriptor（`CUtensorMap`），描述 tensor 的形狀、stride、元素大小、swizzle 模式等資訊。這個 descriptor 傳進 kernel，GPU 端只需要提供座標。

```cpp
// Host 端建立 descriptor（示意）
CUtensorMap tensorMap;
cuTensorMapEncodeTiled(
    &tensorMap,
    CU_TENSOR_MAP_DATA_TYPE_FLOAT16,
    2,                    // rank（2D tensor）
    tensor_ptr,           // global memory 基底
    {M, K},               // global tensor shape
    {TILE_M, TILE_K},     // tile shape
    {1, 1},               // stride（以 element 為單位）
    CU_TENSOR_MAP_INTERLEAVE_NONE,
    CU_TENSOR_MAP_SWIZZLE_128B,   // 自動 bank conflict 避免
    CU_TENSOR_MAP_L2_PROMOTION_L2_256B,
    CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE
);
```

CUtensorMap 只能在 host 建立，不能在 kernel 裡動態建立。這個限制比 cp.async 嚴格很多——cp.async 只需要兩個指標就能發出，TMA 需要提前規劃整個 tensor 的存取模式。

### PTX 層級（大致語義）

TMA 用 mbarrier 做同步，不用 cp.async 的 commit_group/wait_group 機制：

```ptx
; 初始化 mbarrier
mbarrier.init.shared.b64     [mbar], thread_count;

; 聲明預期的搬移大小（tx_bytes）
mbarrier.arrive.expect_tx.shared::cta.b64  state, [mbar], tx_bytes;

; 只有 leader thread 發 TMA（elect.sync 選出 leader）
elect.sync %pred, 0xffffffff;
@%pred cp.async.bulk.tensor.2d.shared::cluster.global.mbarrier::complete_tx::bytes
    [dst_smem], [tensorMap, {x_coord, y_coord}], [mbar];

; 等待完成
mbarrier.try_wait.parity.shared::cta.b64  %p, [mbar], %parity;
```

PTX 層級的 TMA 程式碼比 cp.async 複雜得多。FlashAttention-3 直接用 CUTLASS 的 TMA wrappers，不是裸 PTX。本課不深入 TMA 實作——它的完整使用需要理解 mbarrier 的 phase/parity 機制，那是另一個大主題。

### TMA 的優勢在哪裡

TMA 真正的威力在兩個地方：

**第一，leader-only issue。** 整個 block 的 512 或 1024 個 thread 裡，只有一個 thread 負責描述搬移，其餘 thread 從搬資料的工作中解放出來，可以全力計算。cp.async 下所有 thread 都要參與分工。

**第二，原生多維度支援。** TMA 原生理解 2D/3D tensor 的 stride，不需要程式碼手算 offset。更重要的是 **im2col 模式**：convolution 的 im2col 展開可以在硬體層面做，搬進 shared memory 的資料已經是展開後的格式，kernel 裡完全不需要做 im2col 變換。這對 CNN 的 direct conv 實作意義重大。

TMA 是 H100 FP8 GEMM 和 FlashAttention-3 的核心。FlashAttention-3 在 H100 上能跑到 740 TFLOPS FP16，TMA 是讓搬資料跟不上計算的瓶頸消失的關鍵技術之一。

---

## 31.5 Thread Block Cluster 和 DSMEM（Hopper）

一個 thread block 的 shared memory 是 SM 本地的，跨 block 交換資料只能繞回 global memory，有成本。Hopper 引入 **Thread Block Cluster**：把多個 block 組成 cluster，cluster 內的 block 保證同時駐留在相鄰 SM 上，任何 thread 可透過 `mapa` 指令直接讀寫其他 SM 的 shared memory（**Distributed Shared Memory，DSMEM**）。

### Cluster 宣告語法（Hopper sm_90+ 特性）

CUDA 12 有兩種指定 cluster 大小的方式，常一起記：

```cpp
// ── 方式 1：__cluster_dims__ attribute（編譯期靜態，nvcc 把大小編入 PTX）──
// 限制：大小必須是編譯期常數
__global__ __cluster_dims__(2, 2, 1)   // 2×2×1 = 4 個 block 一個 cluster
void my_cluster_kernel(float* A, float* B, float* C) {
    auto cluster = cooperative_groups::this_cluster();
    __shared__ float smem[1024];

    // cluster.sync()：整個 cluster 的 barrier，範圍大於 __syncthreads
    cluster.sync();

    // DSMEM：直接讀 rank-1 block 的 smem，不需要繞回 global memory
    float* remote = cluster.map_shared_rank(smem, 1);
    float  val    = remote[threadIdx.x];   // 跨 SM 讀取
    (void)val;
}
// grid 必須是 cluster_dims 的整數倍，否則 cudaErrorInvalidClusterSize
my_cluster_kernel<<<grid, block>>>(A, B, C);

// ── 方式 2：cudaLaunchKernelEx（runtime，cluster 大小可在 runtime 決定）──
// 適合同一 binary 要根據 GPU 型號選不同 cluster 大小的場景
cudaLaunchConfig_t cfg  = {};
cfg.gridDim              = grid;
cfg.blockDim             = block;
cudaLaunchAttribute attr;
attr.id                  = cudaLaunchAttributeClusterDimension;
attr.val.clusterDim      = {2, 2, 1};
cfg.attrs                = &attr;
cfg.numAttrs             = 1;
cudaLaunchKernelEx(&cfg, my_cluster_kernel, A, B, C);
```

合法的 cluster 大小：x × y × z ≤ 8（Hopper 硬體上限）。常見設定是 `{2,1,1}`、`{4,1,1}`、`{2,2,1}`。Cluster 太大會強制更多 SM 同時駐留，降低整體 occupancy，適得其反。

### DSMEM 的實際用途

DSMEM 讓整個 cluster 的 shared memory 形成一個虛擬的大型共享空間。對 GEMM 的意義：傳統上每個 block 獨立載入 A/B tile，相鄰 block 可能載入同一份資料（冗餘）；用 DSMEM，cluster 裡的 block 可以分工各載入一部分，再互相存取，每份資料只從 global memory 讀一次。

**硬體限制**：cluster 大小最多 8 個 block（Hopper），DSMEM 的跨 SM 存取延遲比本地 shared memory 高，需要仔細規劃存取模式才能有效益。

這個特性的詳細應用同樣超出本課範圍，留到 Part 7 的 GEMM 實作章節。

---

## 31.6 這些特性如何服務 GEMM 和 Attention

把上面三個特性放在一起看，你會看到一個清晰的設計哲學：

```
DRAM
  ↓  [TMA（H100）或 cp.async（A100）—— 硬體自己搬，不佔 register]
Shared Memory
  ↓  [warp-level load，MMA 指令]
Register File → Tensor Core
  ↑
  計算和下一個 tile 的搬移同時進行（software pipeline）
```

**A100（cp.async）的 FlashAttention-2 架構**：

- 每個 block 負責計算 attention 輸出的一個 row block
- Q/K/V tiles 透過 cp.async 搬進 shared memory
- double buffer：計算當前 K/V tile 的 attention 的同時，prefetch 下一個 K/V tile
- Tensor Core（BF16/FP16）做 Q×K^T 和 softmax 之後的 P×V

**H100（TMA）的 FlashAttention-3 架構**：

- TMA 的 leader-only issue 讓搬資料的成本降到接近零
- 同時跑兩個 warpgroup：一個做 matmul，一個做 softmax（warp specialization）
- H100 支援 FP8，讓吞吐再翻倍
- 結果：740 TFLOPS FP16 / 1500 TFLOPS FP8（H100 SXM5）

從 Turing 的 LDG+STS 到 Ampere 的 cp.async 到 Hopper 的 TMA，是同一個問題的三個世代答案：**計算核心不應該等資料搬移**。

---

## 31.7 誠實對照表

| 特性 | T4 (sm_75) | A100 (sm_80) | H100 (sm_90) |
|------|:---:|:---:|:---:|
| cp.async | 無 | 有 | 有 |
| cuda::pipeline API | 無 | 有 | 有 |
| cuda::barrier / mbarrier | 無 | 有 | 有 |
| TMA | 無 | 無 | 有 |
| Thread Block Cluster | 無 | 無 | 有 |
| DSMEM | 無 | 無 | 有 |
| __cluster_dims__ attribute | 無 | 無 | 有 |
| Tensor Core FP16 | 有 | 有 | 有 |
| Tensor Core BF16 | 無 | 有 | 有 |
| Tensor Core TF32 | 無 | 有 | 有 |
| Tensor Core FP8 | 無 | 無 | 有 |
| Shared Mem 最大 | 64 KB | 164 KB | 228 KB |

T4 沒有 BF16、TF32 Tensor Core 這件事經常被忽略。BF16 在訓練場景比 FP16 穩定（不容易 overflow），A100 訓練框架（PyTorch AMP）預設用 BF16 就是因為這個。T4 用 AMP 得用 FP16，數值稍不穩定是真的。

---

## 31.8 踩雷記錄

**踩雷 1：cp.async.cg 只支援 16 bytes。**

`.cg`（cache global）強制 16-byte 搬移。如果你把 `.ca` 的程式碼改成 `.cg` 然後搬 4 bytes，PTX assembler 會報錯。反過來，如果你的 tile element 大小不是 16 bytes 的倍數，用 `.ca` 加 zero-fill variant 處理邊界：

```ptx
cp.async.ca.shared.global [dst], [src], 16, actual_bytes;
; actual_bytes < 16 時，剩餘空間填 0
```

**踩雷 2：wait_group 的語義是「至多 N 個 group pending」，不是「等 N 個 group」。**

double-buffer 的標準寫法：

```ptx
; 等到 stage k 的 group 完成，允許 stage k+1 的 prefetch 繼續跑
cp.async.wait_group 1;   ; 至多 1 個 group 仍 pending（stage k+1）
```

如果誤寫成 `wait_group 2`，stage k 的資料可能還沒到就開始計算，讀到的是舊資料或垃圾值。這種 bug 不會 crash，只會讓結果靜默錯誤，非常難 debug。

**踩雷 3：Shared memory 對齊要求。**

`.cg` 模式（16-byte 搬移）要求 dst 指標至少 16-byte 對齊，官方建議 128-byte（避免跨 cache line）。`__shared__ char smem[]` 預設 1-byte 對齊，搬進去的結果不可預期，改用 `__shared__ __align__(128) __half smem[]`。

**踩雷 4：TMA 的 CUtensorMap 只能在 host 建立。**

CUtensorMap 是 host 端的 API，需要在 kernel launch 之前建立，然後傳進 kernel（通常透過 constant memory 或 kernel 參數）。你不能在 kernel 內部動態改變 tensor 形狀然後重建 descriptor。這個限制讓 TMA 不適合 tensor 形狀在 kernel 執行期間才確定的場景，cp.async 在這方面更靈活。

---

## 小結

cp.async 解決的是「register 被搬資料的路徑佔用」這個根本問題。TMA 把這個邏輯推到極致：連分工都省了，一個 thread 描述，硬體搬完。Thread Block Cluster + DSMEM 則處理跨 SM 的資料共享，把「shared memory 只屬於一個 SM」的限制打破。

這三個特性都服務同一個目標：讓 Tensor Core 的吞吐量不被資料搬移路徑拖累。A100 的 312 TFLOPS FP16 峰值，沒有 cp.async 是跑不到的；H100 的 FP8 峰值，沒有 TMA 是餵不飽的。

下一章（練習 E）會用 Nsight Compute 的 PTX/SASS 對照實驗，驗證 cp.async 在 SASS 層面的長相，以及它和 LDG+STS 在 stall 計數上的差異。Part 7 的 GEMM 實作會把 cp.async double buffer 真正用起來。

---

## 延伸閱讀

1. **NVIDIA Ampere Tuning Guide**（[docs.nvidia.com/cuda/ampere-tuning-guide](https://docs.nvidia.com/cuda/ampere-tuning-guide/)）—— cp.async 使用指南，包含對齊要求和 pipeline depth 的建議。

2. **CUDA C++ Programming Guide：Pipeline 章節**（[docs.nvidia.com/cuda/cuda-c-programming-guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)）—— `cuda::pipeline` 和 `cooperative_groups::memcpy_async` 的完整 API 文件，包含 pipeline 的 producer/consumer 模型說明。

3. **FlashAttention-3: Fast and Accurate Attention with Asynchrony and Low-precision**（Dao et al., 2024）—— TMA 在真實 production kernel 裡的用法，warp specialization 架構，以及 FP8 的數值穩定性處理方式。H100 GEMM/Attention 的 state of the art。

4. **NVIDIA Hopper Architecture Whitepaper**（[resources.nvidia.com](https://resources.nvidia.com/en-us-tensor-core)）—— TMA、Thread Block Cluster、DSMEM 的官方架構說明，包含硬體設計動機和效能數字。

5. **libcu++ 文件：cuda::pipeline 和 cuda::barrier**（[nvidia.github.io/libcudacxx](https://nvidia.github.io/libcudacxx/)）—— `pipeline_shared_state`、`producer_acquire/commit`、`consumer_wait/release`、`cuda::barrier::init` 和 `arrive_and_wait` 的完整 API 規格與語義說明。

---

← [Ch 30 Tensor Core](./30-tensor-core.md)　|　[練習 E：PTX/SASS 對照實驗](./practice-e-ptx-sass.md) →
