# Final Project — 從 Naive 到接近 cuBLAS：優化 SGEMM + Profiling 報告

**前置**：完成 Ch 01–43。本 final project 把全課的優化工具鏈集中在一個目標：把 FP32 GEMM 從 memory-bound naive 版本推到接近 cuBLAS 效能。  
**環境**：CUDA 12.x, Colab T4 (sm_75)。所有 Nsight Compute 數字標「**Colab 預期，未在本機實測**」。

---

## 第 1 節：專案目標

實作 SGEMM：C = α·(A @ B) + β·C，FP32，M = N = K = 4096。從 naive 出發完成 5 個里程碑，每個里程碑做三件事：用 Nsight Compute 量測、在 roofline 上標落點（Ch 02）、看 PTX/SASS 確認編譯器行為（Ch 27–28）。

**FLOP 計算**：2 × 4096³ ≈ 137.4 GFLOP。T4 FP32 峰值 = 8.1 TFLOPS；cuBLAS ~7.0 TFLOPS（Colab 預期，未在本機實測）→ ~19.6 ms。

**驗收目標**：>= 70% cuBLAS = 4.9 TFLOPS 及格；>= 90% cuBLAS = 6.3 TFLOPS 優秀。

**章節回連**

| 技術 | 章節 |
|------|------|
| Roofline 分析 | Ch 02 |
| Coalescing | Ch 18 |
| Shared memory / bank conflict | Ch 19 |
| Nsight Compute | Ch 25 |
| PTX / SASS | Ch 27–28 |
| GEMM 理論（主要參考） | Ch 38 |
| PyTorch extension 掛法 | Ch 43 |

---

## 第 2 節：5 個里程碑

### 里程碑 1 — Naive Kernel

每個 thread 計算 C 的一個元素，直接讀 global memory。B 的存取是 column-stride strided（相鄰 thread 的 col 相差 1，但讀的是 B[k\*N+col]——不同行的同一列，stride = N）——在 K 維每次迴圈都是 cache miss。

**連回**：Ch 02（roofline），Ch 18（non-coalesced 代價）  
**期望效能（Colab 預期，未在本機實測）**：~1300 ms，~0.1 TFLOPS

```cuda
// milestone1_naive.cu
__global__ void sgemm_naive(float* A, float* B, float* C,
                             int M, int N, int K, float alpha, float beta) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= M || col >= N) return;

    float acc = 0.0f;
    // TODO: for (int k = 0; k < K; k++) acc += A[row*K+k] * B[k*N+col];
    // TODO: C[row*N+col] = alpha * acc + beta * C[row*N+col];
}
// dim3 block(32, 32);  dim3 grid((N+31)/32, (M+31)/32);
```

**驗收**：numpy float64 atol=1e-2 通過；Nsight Global Load Efficiency < 50%。

---

### 里程碑 2 — Coalesced Access

調整 block 佈局，確認 `blockIdx.x` 對應 N 維（column），讓同一 warp 的 32 個 thread 讀 B[k\*N+col] 時 col 連續（coalesced）。kernel 邏輯和 M1 相同，重點在 thread indexing 正確。

**連回**：Ch 18（coalescing），Ch 25（Nsight Memory Workload Analysis -> Global Load Transactions per Request）  
**期望效能（Colab 預期，未在本機實測）**：~650 ms，~0.2 TFLOPS

```cuda
// milestone2_coalesced.cu
__global__ void sgemm_coalesced(float* A, float* B, float* C,
                                  int M, int N, int K, float alpha, float beta) {
    int col = blockIdx.x * blockDim.x + threadIdx.x;  // N 方向，相鄰 thread col 連續
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    if (row >= M || col >= N) return;

    float acc = 0.0f;
    // TODO: for (int k = 0; k < K; k++) {
    //     acc += A[row*K+k] * B[k*N+col];
    //     // B[k*N+col]: col 連續 → coalesced
    //     // A[row*K+k]: 同 warp 各 thread row 不同但 k 相同 → 廣播，非 coalesced
    // }
    // TODO: C[row*N+col] = alpha * acc + beta * C[row*N+col];
}
// dim3 block(32, 32);  dim3 grid((N+31)/32, (M+31)/32);
```

**驗收**：Nsight Global Load Efficiency > 90%。

---

### 里程碑 3 — Shared Memory Tiling

把 A 的 BM×BK tile 和 B 的 BK×BN tile 搬進 shared memory，block 內所有 thread 從 SMEM 做點積。每個 GMEM 元素只讀一次。

BM=BN=64、BK=8：block 用 (BK, BM) = (8, 64) = 512 threads，每個 thread 負責載入多個 Bs 元素。SMEM 消耗 = (BM×BK + BK×BN) × 4 = (512 + 512) × 4 = 4 KB。

**連回**：Ch 19（bank conflict，padding 技巧），Ch 38（tiled GEMM 理論）  
**期望效能（Colab 預期，未在本機實測）**：~60 ms，~2.3 TFLOPS

```cuda
// milestone3_smem.cu
#define BM 64
#define BN 64
#define BK 8

__global__ void sgemm_smem(float* A, float* B, float* C,
                             int M, int N, int K, float alpha, float beta) {
    __shared__ float As[BM][BK + 1];  // +1 padding 解 bank conflict（Ch 19）
    __shared__ float Bs[BK][BN + 1];

    int brow = blockIdx.y, bcol = blockIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;  // block: (BN/BK, BM) = (8, 64)

    float acc = 0.0f;
    for (int t = 0; t < (K + BK - 1) / BK; t++) {
        // TODO: 協作載入 As（ty in [0,BM), tx in [0,BK)）
        //   int aRow = brow*BM + ty, aCol = t*BK + tx;
        //   As[ty][tx] = (aRow < M && aCol < K) ? A[aRow*K + aCol] : 0.0f;
        // TODO: 協作載入 Bs（每個 thread 負責 BN/BK 個 Bs 元素）
        //   for (int j = 0; j < BN/BK; j++) { int bCol = bcol*BN + tx*(BN/BK) + j; ... }
        __syncthreads();

        // TODO: for (int k = 0; k < BK; k++) acc += As[ty][k] * Bs[k][tx*(BN/BK)+j_相應];
        // （實際上這裡需要重新設計 thread 對 C 的映射，參考第 5 節參考實作）
        __syncthreads();
    }
    // TODO: 寫回 C，加越界保護
}
// dim3 block(BN/BK, BM);  // (8, 64) = 512 threads
// dim3 grid((N+BN-1)/BN, (M+BM-1)/BM);
```

**驗收**：Nsight Shared Memory Efficiency > 80%；加 padding 前後 bank conflict 數量有明顯差異。

---

### 里程碑 4 — Register Tiling（Thread-level GEMM）

每個 thread 負責 TM×TN = 8×8 = 64 個輸出元素。每個 K-step 從 SMEM 讀 TM 個 A 值 + TN 個 B 值進暫存器，做 64 次 FMA。算術強度從 1 FMA/2 reads 提升到 64 FMA/16 reads = 4 FMA/read（Ch 38.3 推導）。

Block: (BN/TN, BM/TM) = (16, 16) = 256 threads，負責 BM×BN = 128×128 輸出。

**連回**：Ch 38（register tiling），Ch 27（PTX 看 FFMA 數量，`--ptxas-options=-v` 看 register 用量）  
**期望效能（Colab 預期，未在本機實測）**：~25 ms，~5.5 TFLOPS，~79% cuBLAS

```cuda
// milestone4_reg_tile.cu
#define BM 128
#define BN 128
#define BK 8
#define TM 8
#define TN 8

__global__ void sgemm_reg_tile(float* A, float* B, float* C,
                                int M, int N, int K, float alpha, float beta) {
    __shared__ float As[BM][BK];
    __shared__ float Bs[BK][BN];

    int brow = blockIdx.y, bcol = blockIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;  // ty in [0, BM/TM=16), tx in [0, BN/TN=16)
    int tid = ty * blockDim.x + tx;

    float regC[TM][TN] = {0.0f};  // 64 個 register accumulator

    for (int t = 0; t < (K + BK - 1) / BK; t++) {
        // 256 threads 協作載入 As（BM*BK=1024，每人搬 4 個）
        for (int i = 0; i < BM * BK / (blockDim.x * blockDim.y); i++) {
            int idx = tid + i * (blockDim.x * blockDim.y);
            int r = idx / BK, c = idx % BK;
            int aRow = brow * BM + r, aCol = t * BK + c;
            As[r][c] = (aRow < M && aCol < K) ? A[aRow * K + aCol] : 0.0f;
        }
        // TODO: 協作載入 Bs（BK*BN=1024，每人搬 4 個，同上模式）
        __syncthreads();

        float regA[TM], regB[TN];
        #pragma unroll
        for (int k = 0; k < BK; k++) {
            // TODO: for (int m=0;m<TM;m++) regA[m] = As[ty*TM+m][k];
            // TODO: for (int n=0;n<TN;n++) regB[n] = Bs[k][tx*TN+n];
            // TODO: for (m) for (n) regC[m][n] += regA[m] * regB[n];
        }
        __syncthreads();
    }
    // TODO: 寫回 C（threadRow=brow*BM+ty*TM, threadCol=bcol*BN+tx*TN），加越界保護
}
// dim3 block(BN/TN, BM/TM);  // (16, 16)
// dim3 grid((N+BN-1)/BN, (M+BM-1)/BM);
```

**驗收**：`--ptxas-options=-v` 看 register usage 接近 255，無 spill；PTX 看到大量 `fma.rn.f32`；Occupancy >= 50%。

---

### 里程碑 5 — Double Buffering / Software Pipelining

M4 的瓶頸：每個 tile 的 flow 是「load GMEM → wait → compute → load next → wait」，GMEM 延遲（T4 ~200 ns）完全暴露在 Long Scoreboard stall 裡。

解法：兩組 SMEM buffer 交替，在計算 buffer[cur] 時預取 buffer[nxt]，讓 GMEM 延遲和計算重疊。T4（sm_75）無 `cp.async`，用同步 load 模擬結構；真正的 pipeline 在 sm_80+ 用 `cuda::pipeline`。

**連回**：Ch 38.5（double buffering），Ch 28（SASS 看 stall），Ch 25（Warp State Statistics -> Long Scoreboard）  
**期望效能（Colab 預期，未在本機實測）**：~20 ms，~6.9 TFLOPS，~99% cuBLAS

```cuda
// milestone5_dbl_buf.cu
#define BM 128
#define BN 128
#define BK 16   // BK 加大，讓計算量能覆蓋預取延遲
#define TM 8
#define TN 8

__global__ void sgemm_dbl_buf(float* A, float* B, float* C,
                               int M, int N, int K, float alpha, float beta) {
    __shared__ float As[2][BM * BK];  // double buffer
    __shared__ float Bs[2][BK * BN];

    int brow = blockIdx.y, bcol = blockIdx.x;
    int tid = threadIdx.y * blockDim.x + threadIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;
    float regC[TM][TN] = {0.0f};
    int cur = 0;

    // TODO: 預取 tile 0 到 buffer[0]（同步 load）
    // TODO: __syncthreads()

    for (int t = 0; t < (K + BK - 1) / BK - 1; t++) {
        int nxt = 1 - cur;
        // TODO: 預取 tile (t+1) 到 buffer[nxt]
        //   sm_75 fallback: 直接同步 load
        //   sm_80+: pipe.producer_acquire(); cuda::memcpy_async(...); pipe.producer_commit();

        // 計算 buffer[cur]
        #pragma unroll
        for (int k = 0; k < BK; k++) {
            float regA[TM], regB[TN];
            // TODO: load regA from As[cur], regB from Bs[cur], then outer product into regC
        }

        // TODO: 等待 nxt 完成（sm_75: __syncthreads(); sm_80+: pipe.consumer_wait()）
        cur = nxt;
    }
    // TODO: 計算最後一個 tile（buffer[cur]，不再預取）
    // TODO: alpha/beta 寫回 C，加越界保護
}
// dim3 block(BN/TN, BM/TM);  // (16, 16)
// dim3 grid((N+BN-1)/BN, (M+BM-1)/BM);
```

**驗收**：Nsight Long Scoreboard stall 比 M4 下降 30%+（Colab 預期，未在本機實測）；效能 > cuBLAS 85%。

---

## 第 3 節：Profiling 報告模板

**Nsight Compute 各指標位置**：
- Global Load Efficiency → Memory Workload Analysis -> Global Load Transactions per Request
- Shared Memory Efficiency → Memory Workload Analysis -> Shared Memory
- Bank Conflicts → Memory Workload Analysis -> Shared Memory Bank Conflicts
- Occupancy → Occupancy Analysis -> Achieved Occupancy
- Stall: Long Scoreboard → Warp State Statistics

```bash
ncu --target-processes all \
    --metrics sm__throughput.avg.pct_of_peak_sustained_elapsed,\
l1tex__t_bytes_pipe_lsu_mem_global_op_ld.sum,\
l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_ld.sum,\
smsp__sass_thread_inst_executed_op_ffma_pred_on.sum \
    ./sgemm_benchmark
```

| 里程碑 | 時間 (ms) | TFLOPS | % cuBLAS | Global Load Eff. | Shared Mem Eff. | Bank Conflicts | Occupancy | Stall: Long SB |
|--------|-----------|--------|----------|------------------|-----------------|----------------|-----------|----------------|
| M1 Naive | ~1300 | ~0.1 | ~1.5% | ~25% | — | — | ~50% | ~60% |
| M2 Coalesced | ~650 | ~0.2 | ~3% | ~95% | — | — | ~55% | ~65% |
| M3 Smem Tiling | ~60 | ~2.3 | ~33% | ~95% | ~75% | 中量 | ~40% | ~45% |
| M4 Reg Tiling | ~25 | ~5.5 | ~79% | ~95% | ~85% | 少量 | ~55% | ~35% |
| M5 Dbl Buffer | ~20 | ~6.9 | ~99% | ~95% | ~85% | 少量 | ~60% | ~15% |
| cuBLAS ref | ~19.6 | ~7.0 | 100% | — | — | — | — | — |

所有數字：Colab 預期，未在本機實測。

**兩個反直覺觀察**

1. M3→M4 Occupancy 下降（~40%→~55% 是巧合，實際上 register 壓力可能讓 M4 Occupancy 更低）：register tiling 讓每 thread register 用量暴增到接近 255，SM 能住的 block 數減少。但 ILP 提升彌補了 Occupancy 下降——低 Occupancy 但 compute-bound 仍然快（Ch 25 有完整討論）。

2. M2 Long Scoreboard 比 M1 更高：coalescing 讓 GMEM 請求合併，但 DRAM 延遲沒變，更高的 bandwidth utilization 反而讓更多 warp 在等待 DRAM 回傳。M3 引入 SMEM tiling 才真正解決這個問題。

---

## 第 4 節：正確性驗證

K=4096 次 FP32 加法，累積誤差量級 ≈ K × 1.2e-7 ≈ 5e-4，建議 atol=1e-2。

```python
# verify.py — 在 Colab 執行
import numpy as np
import torch
import time

def verify_sgemm(kernel_fn, M=4096, N=4096, K=4096, alpha=1.0, beta=0.0):
    """kernel_fn(A, B, C, M, N, K, alpha, beta) -> None，in-place 更新 C（CUDA tensor）"""
    rng = np.random.default_rng(42)
    A_np = rng.standard_normal((M, K)).astype(np.float32)
    B_np = rng.standard_normal((K, N)).astype(np.float32)

    # float64 ground truth
    ref = alpha * (A_np.astype(np.float64) @ B_np.astype(np.float64))

    A = torch.from_numpy(A_np).cuda()
    B = torch.from_numpy(B_np).cuda()
    C = torch.zeros(M, N, dtype=torch.float32, device='cuda')

    kernel_fn(A, B, C, M, N, K, alpha, beta)
    torch.cuda.synchronize()

    result = C.cpu().numpy().astype(np.float64)
    max_err = np.max(np.abs(result - ref))
    print(f"max|err| = {max_err:.2e}  {'PASS' if max_err < 1e-2 else 'FAIL'}")
    return max_err < 1e-2

def benchmark_ms(kernel_fn, M=4096, N=4096, K=4096, warmup=5, iters=20):
    """回傳（中位數 ms, TFLOPS）"""
    A = torch.randn(M, K, dtype=torch.float32, device='cuda')
    B = torch.randn(K, N, dtype=torch.float32, device='cuda')
    C = torch.zeros(M, N, dtype=torch.float32, device='cuda')
    for _ in range(warmup):
        kernel_fn(A, B, C, M, N, K, 1.0, 0.0)
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        kernel_fn(A, B, C, M, N, K, 1.0, 0.0)
        torch.cuda.synchronize()
        ts.append((time.perf_counter() - t0) * 1000)
    ms = float(np.median(ts))
    tflops = 2 * M * N * K / (ms * 1e-3) / 1e12
    print(f"{ms:.1f} ms | {tflops:.2f} TFLOPS")
    return ms, tflops

# cuBLAS baseline
def cublas_fn(A, B, C, M, N, K, alpha, beta):
    C.copy_(alpha * (A @ B) + beta * C)

verify_sgemm(cublas_fn)
benchmark_ms(cublas_fn)
```

---

## 第 5 節：各里程碑參考實作

<details>
<summary>M3 — Shared Memory Tiling 完整實作（帶 padding）</summary>

Block 設成 (BN, 1) = (64, 1)，每個 thread 負責 C 的一列（BM 個輸出），迴圈搬 As/Bs。更常見的做法是 block(BN/BK, BM) = (8, 64)，每個 thread 負責 C 的一個元素，as/bs 載入用 ty < BK 做 guard。下面給最直接的「每 thread 算 1 個 C 元素」版本，block = (BN, BM) 但實際上必須 <= 1024 threads，故 BM=BN=32：

```cuda
// 正確可運行版本：BM=BN=32，BK=8
#define BM32 32
#define BN32 32
#define BK8  8

__global__ void sgemm_smem_32(float* A, float* B, float* C,
                                int M, int N, int K, float alpha, float beta) {
    __shared__ float As[BM32][BK8 + 1];
    __shared__ float Bs[BK8][BN32 + 1];

    int brow = blockIdx.y, bcol = blockIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;  // ty in [0,32), tx in [0,32)

    float acc = 0.0f;
    for (int t = 0; t < (K + BK8 - 1) / BK8; t++) {
        // 每個 thread 載入 As 的一個元素（tx in [0,32), 但 BK8=8 → tx<BK8 才寫）
        if (tx < BK8) {
            int aRow = brow * BM32 + ty, aCol = t * BK8 + tx;
            As[ty][tx] = (aRow < M && aCol < K) ? A[aRow * K + aCol] : 0.0f;
        }
        // 每個 thread 載入 Bs 的一個元素（ty in [0,32), 但 BK8=8 → ty<BK8 才寫）
        if (ty < BK8) {
            int bRow = t * BK8 + ty, bCol = bcol * BN32 + tx;
            Bs[ty][tx] = (bRow < K && bCol < N) ? B[bRow * N + bCol] : 0.0f;
        }
        __syncthreads();
        #pragma unroll
        for (int k = 0; k < BK8; k++)
            acc += As[ty][k] * Bs[k][tx];
        __syncthreads();
    }
    int crow = brow * BM32 + ty, ccol = bcol * BN32 + tx;
    if (crow < M && ccol < N)
        C[crow * N + ccol] = alpha * acc + beta * C[crow * N + ccol];
}
// dim3 block(BN32, BM32);  // (32, 32) = 1024 threads
// dim3 grid((N+BN32-1)/BN32, (M+BM32-1)/BM32);
```

</details>

<details>
<summary>M4 — Register Tiling 完整實作</summary>

```cuda
// BM=128, BN=128, BK=8, TM=8, TN=8, block=(16,16)=256 threads
#define BM 128
#define BN 128
#define BK 8
#define TM 8
#define TN 8

__global__ void sgemm_reg_tile_ref(float* A, float* B, float* C,
                                    int M, int N, int K, float alpha, float beta) {
    __shared__ float As[BM][BK];
    __shared__ float Bs[BK][BN];

    int brow = blockIdx.y, bcol = blockIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;
    int tid = ty * blockDim.x + tx;

    int threadRow = brow * BM + ty * TM;
    int threadCol = bcol * BN + tx * TN;
    float regC[TM][TN] = {0.0f};

    for (int t = 0; t < (K + BK - 1) / BK; t++) {
        // 協作載入 As（BM*BK=1024, 256 threads, 每人 4 個）
        for (int i = 0; i < BM * BK / (blockDim.x * blockDim.y); i++) {
            int idx = tid + i * (blockDim.x * blockDim.y);
            int r = idx / BK, c = idx % BK;
            int aRow = brow * BM + r, aCol = t * BK + c;
            As[r][c] = (aRow < M && aCol < K) ? A[aRow * K + aCol] : 0.0f;
        }
        // 協作載入 Bs（BK*BN=1024, 256 threads, 每人 4 個）
        for (int i = 0; i < BK * BN / (blockDim.x * blockDim.y); i++) {
            int idx = tid + i * (blockDim.x * blockDim.y);
            int r = idx / BN, c = idx % BN;
            int bRow = t * BK + r, bCol = bcol * BN + c;
            Bs[r][c] = (bRow < K && bCol < N) ? B[bRow * N + bCol] : 0.0f;
        }
        __syncthreads();

        #pragma unroll
        for (int k = 0; k < BK; k++) {
            float regA[TM], regB[TN];
            #pragma unroll
            for (int m = 0; m < TM; m++) regA[m] = As[ty * TM + m][k];
            #pragma unroll
            for (int n = 0; n < TN; n++) regB[n] = Bs[k][tx * TN + n];
            #pragma unroll
            for (int m = 0; m < TM; m++)
                #pragma unroll
                for (int n = 0; n < TN; n++)
                    regC[m][n] += regA[m] * regB[n];  // 64 次 FFMA
        }
        __syncthreads();
    }

    #pragma unroll
    for (int m = 0; m < TM; m++)
        #pragma unroll
        for (int n = 0; n < TN; n++) {
            int crow = threadRow + m, ccol = threadCol + n;
            if (crow < M && ccol < N)
                C[crow * N + ccol] = alpha * regC[m][n] + beta * C[crow * N + ccol];
        }
}
// dim3 block(BN/TN, BM/TM);  // (16, 16)
// dim3 grid((N+BN-1)/BN, (M+BM-1)/BM);
```

`As[ty * TM + m][k]`：thread (ty,tx) 從 SMEM 的 As 取第 `ty*TM+m` 行、第 `k` 列，對應 C 中該 thread 負責的 TM 行裡第 m 行。`regC[m][n]` 是 row-major flatten，外積 `regA[m] * regB[n]` 對應到 ptxas 展開後的 64 個 FFMA 指令。

</details>

<details>
<summary>M5 — Double Buffering 完整骨架（sm_75 同步版）</summary>

```cuda
// BM=128, BN=128, BK=16, TM=8, TN=8
#define BM 128
#define BN 128
#define BK 16
#define TM 8
#define TN 8

__global__ void sgemm_dbl_buf_ref(float* A, float* B, float* C,
                                   int M, int N, int K, float alpha, float beta) {
    __shared__ float As[2][BM * BK];  // 2 * 128 * 16 * 4B = 16 KB
    __shared__ float Bs[2][BK * BN];  // 2 * 16 * 128 * 4B = 16 KB （總 32 KB/block）

    int brow = blockIdx.y, bcol = blockIdx.x;
    int tid = threadIdx.y * blockDim.x + threadIdx.x;
    int ty = threadIdx.y, tx = threadIdx.x;
    float regC[TM][TN] = {0.0f};
    const int LD = BM * BK / (blockDim.x * blockDim.y);  // 每人搬幾個（As/Bs 各 LD 個）

    // 預取 tile 0 到 buffer[0]
    int cur = 0;
    for (int i = 0; i < LD; i++) {
        int idx = tid + i * (blockDim.x * blockDim.y);
        int r = idx / BK, c = idx % BK;
        int aRow = brow * BM + r, aCol = c;
        As[0][r * BK + c] = (aRow < M && aCol < K) ? A[aRow * K + aCol] : 0.0f;
    }
    for (int i = 0; i < LD; i++) {
        int idx = tid + i * (blockDim.x * blockDim.y);
        int r = idx / BN, c = idx % BN;
        int bRow = r, bCol = bcol * BN + c;
        Bs[0][r * BN + c] = (bRow < K && bCol < N) ? B[bRow * N + bCol] : 0.0f;
    }
    __syncthreads();

    int numTiles = (K + BK - 1) / BK;
    for (int t = 0; t < numTiles - 1; t++) {
        int nxt = 1 - cur;
        // 預取下一個 tile（sm_75 同步版，延遲無法真正隱藏）
        for (int i = 0; i < LD; i++) {
            int idx = tid + i * (blockDim.x * blockDim.y);
            int r = idx / BK, c = idx % BK;
            int aRow = brow * BM + r, aCol = (t + 1) * BK + c;
            As[nxt][r * BK + c] = (aRow < M && aCol < K) ? A[aRow * K + aCol] : 0.0f;
        }
        for (int i = 0; i < LD; i++) {
            int idx = tid + i * (blockDim.x * blockDim.y);
            int r = idx / BN, c = idx % BN;
            int bRow = (t + 1) * BK + r, bCol = bcol * BN + c;
            Bs[nxt][r * BN + c] = (bRow < K && bCol < N) ? B[bRow * N + bCol] : 0.0f;
        }
        // 計算 buffer[cur]
        #pragma unroll
        for (int k = 0; k < BK; k++) {
            float regA[TM], regB[TN];
            #pragma unroll
            for (int m = 0; m < TM; m++) regA[m] = As[cur][(ty * TM + m) * BK + k];
            #pragma unroll
            for (int n = 0; n < TN; n++) regB[n] = Bs[cur][k * BN + tx * TN + n];
            #pragma unroll
            for (int m = 0; m < TM; m++)
                #pragma unroll
                for (int n = 0; n < TN; n++)
                    regC[m][n] += regA[m] * regB[n];
        }
        __syncthreads();
        cur = nxt;
    }
    // 最後一個 tile
    #pragma unroll
    for (int k = 0; k < BK; k++) {
        float regA[TM], regB[TN];
        #pragma unroll
        for (int m = 0; m < TM; m++) regA[m] = As[cur][(ty * TM + m) * BK + k];
        #pragma unroll
        for (int n = 0; n < TN; n++) regB[n] = Bs[cur][k * BN + tx * TN + n];
        #pragma unroll
        for (int m = 0; m < TM; m++)
            #pragma unroll
            for (int n = 0; n < TN; n++)
                regC[m][n] += regA[m] * regB[n];
    }
    #pragma unroll
    for (int m = 0; m < TM; m++)
        #pragma unroll
        for (int n = 0; n < TN; n++) {
            int crow = brow * BM + ty * TM + m, ccol = bcol * BN + tx * TN + n;
            if (crow < M && ccol < N)
                C[crow * N + ccol] = alpha * regC[m][n] + beta * C[crow * N + ccol];
        }
}
// dim3 block(BN/TN, BM/TM);  dim3 grid((N+BN-1)/BN, (M+BM-1)/BM);
```

sm_75（T4）上，預取部分實際上是同步 load，BK 從 8 增加到 16 才是效能提升的主因（每次 tile 計算量翻倍，SMEM 頻寬壓力降低）。真正的 async copy 需要 sm_80+：`#include <cuda/pipeline>` + `cuda::memcpy_async`，T4 上加 `#if __CUDA_ARCH__ >= 800` guard。

</details>

---

## 第 6 節：延伸方向（選做）

**A — Tensor Core WMMA**（Ch 30）：把 M4 替換成 `wmma::mma_sync`，FP16 輸入。T4（sm_75）支援 `m16n16k16`。理論峰值從 8.1 TFLOPS 跳到 65 TFLOPS。需額外的 FP32→FP16 轉換，精度用 atol=1e-1 驗收。

**B — FP16 GEMM**（Ch 42）：整個 kernel 換 `__half2`，一條指令處理兩個 FP16 值（HFMA2）。量 throughput 對比 FP32，驗收 atol=1e-1。

**C — Triton 重寫對比**（Ch 37）：用 Triton `tl.dot` 實作等效 tiled matmul（約 50 行），對比 CUDA 版本的開發時間、效能、可讀性。Triton autotuning 通常可達 85%+ cuBLAS。

**D — Python Autotuning**：枚舉 BM/BN/BK/TM/TN 的組合空間，找 T4 上最佳參數。篩選條件：block size ≤ 1024 threads，SMEM ≤ 48 KB/block，BM×BK + BK×BN 不超過 32 KB（留 double buffer 空間）。

---

## 第 7 節：自評 Rubric

| 項目 | 0 | 1 | 2 |
|------|---|---|---|
| **正確性** | 任一里程碑 atol 未通過 | M1–M3 通過 | 全部 5 個里程碑 atol < 1e-2 |
| **Profiling** | 未用 Nsight 量 | 量了 2 個以上里程碑 | 每個里程碑 Nsight 數字填入第 3 節表格 |
| **Roofline** | 未畫 | 畫了但只標 1–2 個 | 5 個里程碑全標，X 軸是 ops/byte |
| **PTX/SASS** | 未看 | M3 或 M4 其中一個 | M3 確認 SMEM load 指令，M4 確認 FFMA 展開，M5 看 stall 分佈 |
| **最終效能** | < 50% cuBLAS | 50–70%（M4 到達） | > 70%（M5 到達） |
| **延伸方向** | 未完成 | 完成第 6 節任一項 | 完成兩項以上，附效能對比 |

滿分 12 分。10 分以上代表真正掌握了 CUDA 效能工程的核心流程。

---

## 第 8 節：延伸閱讀

1. **CUTLASS**（https://github.com/NVIDIA/cutlass）：生產級 GEMM template library。CUTLASS 3.x 引入 CuTe layout algebra，把 tile indexing 從手算變成可組合的 type-level 操作，是補上剩餘 1–5% 效能差距的正確方向。

2. **CuTe**（CUTLASS 3.x 子模組）：用 C++ type system 描述 tensor layout 和 copy 操作，讓 warp-level MMA indexing 可組合。讀懂 CuTe 才能有效率地讀 FlashAttention 3 的 kernel。

3. **Simon Boehm's blog — "How to Optimize a CUDA Matmul Kernel for cuBLAS-like Performance"**（https://siboehm.com/articles/22/CUDA-MMM）：本課 Ch 38 和本 final project 的主要技術參考，A100 benchmark 完整。

4. **Triton community tutorials**（https://triton-lang.org/main/getting-started/tutorials/03-matrix-multiplication.html）：官方 matmul tutorial，對照本 project 的 CUDA 版本讀，理解 compiler-managed tiling 和 hand-coded tiling 的設計取捨。

5. **Programming Massively Parallel Processors（PMPP）第 4 版**（Kirk & Hwu, 2022）：Ch 5（Memory Architecture）和 Ch 16 對 register file / SMEM / L1 hierarchy 有最完整的教科書說明，補足本課硬體細節的空白。

---

## 第 9 節：全課總結

你現在站在哪裡。

從 Ch 01 到這裡，你把 CUDA 從 thread hierarchy（warp/block/grid）一路推到生產 kernel 的設計流程。具體說：

**你懂 memory hierarchy 的代價差距。** GMEM latency ~200 ns vs SMEM ~20 ns vs register 0 cycle。這不是數字，是你設計 tile 形狀的第一性原則——為什麼 BK=8 能讓 kernel 從 0.1 跳到 2.3 TFLOPS，就是因為把 GMEM 讀取次數從 O(M·N·K) 壓到 O(M·N·K / BK)。

**你懂 Nsight Compute 說的話。** Long Scoreboard 60% 表示 warp 在等 GMEM；bank conflict 表示 SMEM 被序列化；Occupancy 30% 不一定是問題，要看是 register-limited 還是SMEM-limited。Ch 25 建立的框架讓你能從數字直接定位瓶頸，不靠猜。

**你懂算術強度是設計槓桿，不是測量結果。** Ch 02 的 roofline 告訴你目標在哪：T4 FP32 的轉折點在 ~27 FLOP/Byte。Ch 38 的 register tiling 是達到這個轉折點的機制：每次從 SMEM 把一行 A 和一行 B 拉進 register，做 TM×TN = 64 次 FMA，讓「讀進來的值」被最大化複用。M3→M4 的 2.3→5.5 TFLOPS 跳躍，來自算術強度從 ~8 FLOP/Byte 推到 ~32 FLOP/Byte。

**你懂 PTX/SASS 是驗證工具，不是黑盒。** `#pragma unroll` 加了，但 ptxas 到底有沒有展開？`__restrict__` 加了，到底產生沒產生 `.nc`？你現在能直接去看。Practice E 和 Ch 27–28 給了這個能力，讓你在調優 kernel 時不靠猜測。

**你懂高層工具背後的邏輯。** Ch 37 的 Triton `tl.dot` 對應的就是 SMEM tiling + register tiling；Ch 41 的 FlashAttention 用 online softmax + tile 把 attention 從兩次 pass 壓成一次，根本原因是 HBM bandwidth 是瓶頸而不是 compute；Ch 42 的 FP16 把 bandwidth 需求減半但引入精度代價；Ch 43 讓你把手寫 kernel 接進真實訓練框架而不是只跑獨立 benchmark。

**下一步是什麼。**

CUTLASS 3.x 的 CuTe layout algebra 是最值得投入的下一個技術點——它把你這裡手算的 `ty*TM+m` 之類的 indexing 變成可組合的 type-level 表達，讓 warp-level 和 instruction-level 的 tile 設計可以系統化而非靠手算驗算。讀懂 CuTe 之後，去讀 FlashAttention 3 的 kernel source——那是目前最值得逐行閱讀的生產 CUDA 程式碼，把 double buffering、Tensor Core、warp specialization 全部組合在一起。Triton 社群在快速發展，compiler-managed tiling 對很多場景比手寫 CUDA 更實際，特別是你需要在不同 GPU 架構上跑的場景。

你現在有能力讀這些東西，不需要靠猜，因為你懂底層發生了什麼。
