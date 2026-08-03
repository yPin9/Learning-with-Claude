# Ch 40 — Softmax / LayerNorm / Reduction Kernel

> **目標**：理解 memory-bound reduction kernel 的設計模式；實作 numerically stable softmax（3-pass 與 online 1-pass）與 Welford LayerNorm；為 Ch 41 FlashAttention 打底。
> **環境**：CUDA 12.x, Colab T4 (sm_75)

← [Ch 39 Convolution](./39-convolution.md)

---

## 40.1 這類 kernel 的共同特徵：memory-bound + reduction

GEMM（Ch34-35）是 compute-bound：幾乎所有時間花在乘法累加，memory 帶寬只是配角。Softmax、LayerNorm、Reduction 完全相反——它們是 **memory-bound**：運算量輕（幾個 exp/div），但每個輸出都必須先把整條 row（或整個向量）讀進來才能算。

典型瓶頸：

```
GEMM      : arithmetic intensity ≈ 500 FLOP/Byte  → compute-bound
Softmax   : arithmetic intensity ≈ 10  FLOP/Byte  → memory-bound
LayerNorm : arithmetic intensity ≈ 8   FLOP/Byte  → memory-bound
```

這個差距決定了設計哲學：

| 關切點 | GEMM | Softmax/LayerNorm |
|--------|------|-------------------|
| 主要瓶頸 | ALU 吞吐 | DRAM 帶寬 |
| 優化手段 | tiling, tensor core | pass 數量、kernel fusion |
| 中間結果 | 留在 register/shmem | 盡量不落地 global memory |

共同核心是 **reduction**：把一個向量的所有元素折疊成一個（或幾個）純量，然後再廣播回去。Ch 22（[atomics & reduction](./22-atomics-reduction.md)）處理了 reduction 的 warp/block 機制；本章把它焊接進具體的模型 kernel。

---

## 40.2 直覺：一個 block 負責一個 row

Softmax 和 LayerNorm 都以 **row** 為單位獨立操作——每個 token 的 logit 向量對其他 token 沒有依賴。自然的映射：

```
矩陣  [B, N]  (B=batch*heads, N=sequence length 或 hidden_dim)

每個 block  →  處理一條 row（長度 N）
threadIdx.x →  負責這條 row 的部分元素

block 0 → row 0 ─── thread 0,1,2,...,T-1 → x[0], x[1], ..., x[N-1]
block 1 → row 1
block 2 → row 2
...
```

一條 row 處理需要兩件事：
1. **全域統計量**（max、sum、mean、variance）→ block 內 reduction
2. **逐元素更新**（subtract, exp, divide, normalize）→ 每條 thread 讀寫自己的元素

block 裡的 thread 數目（BLOCK_SIZE）應是 warp 的倍數，通常選 128 或 256。當 N > BLOCK_SIZE，每個 thread 要處理多個元素（stride loop）。

---

## 40.3 Numerically Unstable Softmax：先看它怎麼炸

「直接算」的 softmax：

```
a_i = exp(x_i) / Σ exp(x_j)
```

問題：FP32 最大可表示值約 3.4×10^38，`exp(x)` 在 `x ≈ 88` 就溢出了。

```python
# 在 Colab 跑這段，先不用 CUDA
import numpy as np
x = np.array([100.0, 101.0, 102.0], dtype=np.float32)
raw = np.exp(x)
print(raw)   # [inf inf inf]  → NaN 的前身
print(raw / raw.sum())  # [nan nan nan]
```

實測失敗案例（Colab 預期，未在本機實測）：

```c
// naive_softmax_kernel — 不減 max
__global__ void naive_softmax(const float* __restrict__ x,
                               float* __restrict__ out,
                               int N)
{
    int row = blockIdx.x;
    const float* row_x = x + row * N;
    float* row_o = out + row * N;

    // Step 1: sum of exp
    float sum = 0.0f;
    for (int i = threadIdx.x; i < N; i += blockDim.x)
        sum += expf(row_x[i]);          // x[i]=100 → expf(100)=inf

    // block reduction for sum (省略實作，假設 block_sum() 已完成)
    sum = block_sum(sum);               // sum = inf

    // Step 2: normalize
    for (int i = threadIdx.x; i < N; i += blockDim.x)
        row_o[i] = expf(row_x[i]) / sum;  // inf / inf = NaN
}
```

**輸出全 NaN**。在 Transformer 訓練時，一旦 attention logit 偶爾衝到 100+，整個模型就梯度消失。

---

## 40.4 Numerically Stable 3-Pass Softmax

修法：在算 exp 之前先減去 row 的最大值 m。

```
softmax(x_i) = exp(x_i) / Σ exp(x_j)
             = exp(x_i - m) / Σ exp(x_j - m)   （分子分母同除 exp(m)）
```

x_i - m ≤ 0，所以 exp(x_i - m) ∈ (0, 1]，絕對不會 overflow。

三趟實作：

```
Pass 1: m = max_i(x_i)          ← reduction
Pass 2: d = Σ exp(x_i - m)      ← reduction
Pass 3: a_i = exp(x_i - m) / d  ← element-wise
```

完整 kernel（BLOCK_SIZE=256，每個 block 處理一個 row）：

```c
#define BLOCK_SIZE 256

// 輔助：block 內 float reduction（取最大值）
__device__ float block_reduce_max(float val)
{
    // warp-level reduction（見 Ch 22）
    for (int offset = 16; offset > 0; offset >>= 1)
        val = fmaxf(val, __shfl_xor_sync(0xffffffff, val, offset));

    __shared__ float warp_max[BLOCK_SIZE / 32];
    int lane  = threadIdx.x & 31;
    int warpId = threadIdx.x >> 5;

    if (lane == 0) warp_max[warpId] = val;
    __syncthreads();

    // 第 0 個 warp 收集所有 warp 的結果
    val = (threadIdx.x < (BLOCK_SIZE / 32)) ? warp_max[threadIdx.x] : -FLT_MAX;
    if (warpId == 0) {
        for (int offset = 16; offset > 0; offset >>= 1)
            val = fmaxf(val, __shfl_xor_sync(0xffffffff, val, offset));
    }
    // 廣播給所有 thread
    __shared__ float result;
    if (threadIdx.x == 0) result = val;
    __syncthreads();
    return result;
}

// 輔助：block 內 float reduction（加總）
__device__ float block_reduce_sum(float val)
{
    for (int offset = 16; offset > 0; offset >>= 1)
        val += __shfl_xor_sync(0xffffffff, val, offset);

    __shared__ float warp_sum[BLOCK_SIZE / 32];
    int lane   = threadIdx.x & 31;
    int warpId = threadIdx.x >> 5;

    if (lane == 0) warp_sum[warpId] = val;
    __syncthreads();

    val = (threadIdx.x < (BLOCK_SIZE / 32)) ? warp_sum[threadIdx.x] : 0.0f;
    if (warpId == 0) {
        for (int offset = 16; offset > 0; offset >>= 1)
            val += __shfl_xor_sync(0xffffffff, val, offset);
    }
    __shared__ float result;
    if (threadIdx.x == 0) result = val;
    __syncthreads();
    return result;
}

// 3-pass stable softmax
__global__ void softmax_3pass(const float* __restrict__ x,
                               float* __restrict__ out,
                               int N)
{
    int row = blockIdx.x;
    const float* row_x = x + (long long)row * N;
    float*       row_o = out + (long long)row * N;

    // --- Pass 1: 找 row max ---
    float local_max = -FLT_MAX;
    for (int i = threadIdx.x; i < N; i += BLOCK_SIZE)
        local_max = fmaxf(local_max, row_x[i]);
    float m = block_reduce_max(local_max);

    // --- Pass 2: 算 Σ exp(x - m) ---
    float local_sum = 0.0f;
    for (int i = threadIdx.x; i < N; i += BLOCK_SIZE)
        local_sum += expf(row_x[i] - m);
    float d = block_reduce_sum(local_sum);

    // --- Pass 3: 寫出 softmax ---
    for (int i = threadIdx.x; i < N; i += BLOCK_SIZE)
        row_o[i] = expf(row_x[i] - m) / d;
}
```

呼叫方式：

```c
// B 條 rows，每條長 N
softmax_3pass<<<B, BLOCK_SIZE>>>(d_x, d_out, N);
```

3-pass 的問題：Pass 1 和 Pass 2 各需要一次完整的 global memory 讀取，合計讀 2N 個 float 才能算出 m 和 d，Pass 3 再讀一次。三趟共讀 3N，寫 N。

---

## 40.5 Online 1-Pass Softmax（Milakov & Gimelshein 2018）

**論文**：*Online normalizer calculation for softmax*（arXiv:1805.02867）

核心想法：能否在單次掃描中同時維護 running max `m` 和 numerically stable 的 running sum `d`？

**問題在哪**：當我們看到第 i 個元素，發現新的最大值時，之前累積的 d 是用舊的 m 算的，基準不同，不能直接相加。

**解法**：correction factor。假設 running max 從 m_old 更新為 m_new = max(m_old, x_i)：

```
舊的 d 是   Σ_{j<i} exp(x_j - m_old)
現在要改成  Σ_{j<i} exp(x_j - m_new)
           = Σ_{j<i} exp(x_j - m_old) * exp(m_old - m_new)
           = d_old * exp(m_old - m_new)
```

所以更新規則：

```
m_new = max(m_old, x_i)
d_new = d_old * exp(m_old - m_new) + exp(x_i - m_new)
```

當 x_i < m_old 時，m_new = m_old，correction factor = exp(0) = 1，d 正常累加。
當 x_i > m_old 時，m_new = x_i，correction factor = exp(m_old - x_i) < 1，把舊的 d 縮小到新基準。

視覺化掃描過程（N=5，x = [1, 5, 2, 8, 3]）：

```
i=0: x=1,  m=1,   d = exp(1-1)           = 1.000
i=1: x=5,  m=5,   d = 1.000*exp(1-5)     + exp(5-5)  = 0.018 + 1 = 1.018
i=2: x=2,  m=5,   d = 1.018*exp(5-5)     + exp(2-5)  = 1.018 + 0.050 = 1.068
i=3: x=8,  m=8,   d = 1.068*exp(5-8)     + exp(8-8)  = 0.053 + 1 = 1.053
i=4: x=3,  m=8,   d = 1.053*exp(8-8)     + exp(3-8)  = 1.053 + 0.0067 = 1.060

最終：a_i = exp(x_i - 8) / 1.060
```

**1-pass kernel**（只讀一次 global memory 算出 m 和 d）：

```c
__global__ void softmax_1pass(const float* __restrict__ x,
                               float* __restrict__ out,
                               int N)
{
    int row = blockIdx.x;
    const float* row_x = x + (long long)row * N;
    float*       row_o = out + (long long)row * N;

    // --- Online accumulation ---
    float m = -FLT_MAX;  // running max
    float d = 0.0f;      // running sum（已校正到基準 m）

    for (int i = threadIdx.x; i < N; i += BLOCK_SIZE) {
        float xi = row_x[i];
        float m_new = fmaxf(m, xi);
        d = d * expf(m - m_new) + expf(xi - m_new);
        m = m_new;
    }

    // --- Block reduction：合併各 thread 的 (m, d) ---
    // 需要 custom reduction：先比 m，再校正 d
    // warp-level
    for (int offset = 16; offset > 0; offset >>= 1) {
        float m_peer = __shfl_xor_sync(0xffffffff, m, offset);
        float d_peer = __shfl_xor_sync(0xffffffff, d, offset);
        float m_new  = fmaxf(m, m_peer);
        d = d * expf(m - m_new) + d_peer * expf(m_peer - m_new);
        m = m_new;
    }

    __shared__ float sm[BLOCK_SIZE / 32];  // shared max
    __shared__ float sd[BLOCK_SIZE / 32];  // shared d
    int lane   = threadIdx.x & 31;
    int warpId = threadIdx.x >> 5;

    if (lane == 0) { sm[warpId] = m; sd[warpId] = d; }
    __syncthreads();

    if (warpId == 0) {
        m = (threadIdx.x < (BLOCK_SIZE / 32)) ? sm[threadIdx.x] : -FLT_MAX;
        d = (threadIdx.x < (BLOCK_SIZE / 32)) ? sd[threadIdx.x] : 0.0f;
        for (int offset = 16; offset > 0; offset >>= 1) {
            float m_peer = __shfl_xor_sync(0xffffffff, m, offset);
            float d_peer = __shfl_xor_sync(0xffffffff, d, offset);
            float m_new  = fmaxf(m, m_peer);
            d = d * expf(m - m_new) + d_peer * expf(m_peer - m_new);
            m = m_new;
        }
    }
    __shared__ float final_m, final_d;
    if (threadIdx.x == 0) { final_m = m; final_d = d; }
    __syncthreads();
    m = final_m; d = final_d;

    // --- Pass 2: 寫輸出（仍需讀一次，但少了 pass 1 獨立讀 max）---
    for (int i = threadIdx.x; i < N; i += BLOCK_SIZE)
        row_o[i] = expf(row_x[i] - m) / d;
}
```

1-pass 讀 global memory 次數：第一趟讀 N（算 m+d）+ 第二趟讀 N（寫輸出）= **2N**，相較 3-pass 的 3N，省了 33% 的讀取。當 N 很大時（如 LLM 的 4096+ context），這個差異直接反映在延遲上。

**這就是 FlashAttention tiled softmax 的核心**——Ch 41 會把這個 online 算法搬進 tile 循環。

---

## 40.6 Warp Reduction 加速回顧（回連 Ch 22）

Ch 22（[atomics & reduction](./22-atomics-reduction.md)）介紹的 warp shuffle reduction：

```c
// 32 個 thread 內做 tree reduction（加總）
for (int offset = 16; offset > 0; offset >>= 1)
    val += __shfl_xor_sync(0xffffffff, val, offset);
```

五輪 XOR shuffle 就能把 32 個 float 折疊成一個，零 shared memory 使用，零 __syncthreads()。

在本章的 softmax kernel 中，block reduction 分兩層：
1. **Warp reduction**：32 個 thread 用 shuffle 合併 → 每個 warp 出一個代表值
2. **跨 warp 合併**：各 warp 的代表值存進 shared memory → 第 0 個 warp 再做一輪 reduction

BLOCK_SIZE=256 → 8 個 warp → 兩層合計 5+3=8 輪運算，對比 naive 的 255 次加法。

1-pass softmax 的 reduction 複雜一些：不是單純的 sum 或 max，而是「帶校正的 (m, d) 合併」——每次合併兩個 (m, d) 對時，都要套 correction factor。這就是 40.5 中 warp reduction 那段程式碼的邏輯。

---

## 40.7 LayerNorm：兩趟法 vs Welford 單趟

LayerNorm 公式：

```
y_i = (x_i - μ) / sqrt(σ² + ε)  * γ_i + β_i
```

其中 μ = mean(x)，σ² = variance(x)，γ 和 β 是可學習的 scale/shift（per-dimension）。

### 40.7.1 兩趟法

```
Pass 1: μ = (1/N) Σ x_i                    ← reduction
Pass 2: σ² = (1/N) Σ (x_i - μ)²            ← reduction
Pass 3: y_i = (x_i - μ) / sqrt(σ² + ε) * γ_i + β_i
```

缺點：Pass 1 和 Pass 2 各讀一遍 global memory（共 2N 次讀取），才能進入 Pass 3。

### 40.7.2 Welford Online Algorithm（Welford 1962）

Welford 的貢獻是一個數值穩定的**單趟**算法，同時維護三個 accumulator：

- `count`：已看過的元素數
- `mean`：當前 running mean
- `M2`：當前累積平方偏差（用來算 variance）

每個新樣本 x 更新：

```
count  += 1
delta   = x - mean         // x 與「舊 mean」的偏差
mean   += delta / count    // 先更新 mean
delta2  = x - mean         // x 與「新 mean」的偏差
M2     += delta * delta2   // 累積
```

**為什麼需要兩個 delta？**

直覺：我們要計算的是 `Σ (x_i - mean_N)²`，但 mean_N 在每次看到新元素後都會改變。如果只用一個 delta，就會把用舊 mean 的偏差和用新 mean 的偏差混用，產生 catastrophic cancellation（尤其當所有 x_i 都很接近時）。

Welford 的等式保證：

```
M2_n = Σ_{i=1}^{n} (x_i - mean_i) * (x_i - mean_{i-1})
     = Σ_{i=1}^{n} (x_i - mean_n)²    ← 等同於把最終 mean 代入的版本
```

這個等式的成立依賴 `delta * delta2`——一個用舊 mean，一個用新 mean，兩者的積正好消去了 mean 漂移帶來的誤差項。

最終：
- population variance = M2 / count
- sample variance = M2 / (count - 1)

LayerNorm 一般用 population variance（除以 N，不是 N-1）。

### 40.7.3 Welford LayerNorm Kernel

```c
#define LN_BLOCK_SIZE 256
#define LN_EPS 1e-5f

__global__ void layernorm_welford(const float* __restrict__ x,
                                   const float* __restrict__ gamma,
                                   const float* __restrict__ beta,
                                   float* __restrict__ out,
                                   int N)
{
    int row = blockIdx.x;
    const float* row_x = x + (long long)row * N;
    float*       row_o = out + (long long)row * N;

    // --- Welford online accumulation per thread ---
    float count = 0.0f;
    float mean  = 0.0f;
    float M2    = 0.0f;

    for (int i = threadIdx.x; i < N; i += LN_BLOCK_SIZE) {
        float xi = row_x[i];
        count  += 1.0f;
        float delta  = xi - mean;
        mean   += delta / count;
        float delta2 = xi - mean;    // 注意：用更新後的 mean
        M2     += delta * delta2;
    }

    // --- Block reduction：合併各 thread 的 Welford (count, mean, M2) ---
    // Welford 的 parallel 合併公式（Chan et al.）：
    // 兩組 (n_a, mean_a, M2_a) 和 (n_b, mean_b, M2_b) 合併為：
    //   n_c   = n_a + n_b
    //   delta = mean_b - mean_a
    //   mean_c = mean_a + delta * n_b / n_c
    //   M2_c  = M2_a + M2_b + delta^2 * n_a * n_b / n_c

    // Warp-level reduction
    for (int offset = 16; offset > 0; offset >>= 1) {
        float count_b = __shfl_xor_sync(0xffffffff, count, offset);
        float mean_b  = __shfl_xor_sync(0xffffffff, mean,  offset);
        float M2_b    = __shfl_xor_sync(0xffffffff, M2,    offset);

        float count_c = count + count_b;
        if (count_c > 0.0f) {
            float delta = mean_b - mean;
            mean  = mean + delta * (count_b / count_c);
            M2   += M2_b + delta * delta * (count * count_b / count_c);
            count = count_c;
        }
    }

    __shared__ float s_count[LN_BLOCK_SIZE / 32];
    __shared__ float s_mean [LN_BLOCK_SIZE / 32];
    __shared__ float s_M2   [LN_BLOCK_SIZE / 32];
    int lane   = threadIdx.x & 31;
    int warpId = threadIdx.x >> 5;

    if (lane == 0) {
        s_count[warpId] = count;
        s_mean [warpId] = mean;
        s_M2   [warpId] = M2;
    }
    __syncthreads();

    // 第 0 個 warp 跨 warp 合併
    if (warpId == 0) {
        count = (threadIdx.x < (LN_BLOCK_SIZE / 32)) ? s_count[threadIdx.x] : 0.0f;
        mean  = (threadIdx.x < (LN_BLOCK_SIZE / 32)) ? s_mean [threadIdx.x] : 0.0f;
        M2    = (threadIdx.x < (LN_BLOCK_SIZE / 32)) ? s_M2   [threadIdx.x] : 0.0f;

        for (int offset = 16; offset > 0; offset >>= 1) {
            float count_b = __shfl_xor_sync(0xffffffff, count, offset);
            float mean_b  = __shfl_xor_sync(0xffffffff, mean,  offset);
            float M2_b    = __shfl_xor_sync(0xffffffff, M2,    offset);

            float count_c = count + count_b;
            if (count_c > 0.0f) {
                float delta = mean_b - mean;
                mean  = mean + delta * (count_b / count_c);
                M2   += M2_b + delta * delta * (count * count_b / count_c);
                count = count_c;
            }
        }
    }

    __shared__ float final_mean, final_var;
    if (threadIdx.x == 0) {
        final_mean = mean;
        final_var  = M2 / count;   // population variance
    }
    __syncthreads();
    float row_mean = final_mean;
    float row_rstd = rsqrtf(final_var + LN_EPS);   // 用倒數平方根，省一次除法

    // --- 歸一化 + scale/shift ---
    for (int i = threadIdx.x; i < N; i += LN_BLOCK_SIZE)
        row_o[i] = (row_x[i] - row_mean) * row_rstd * gamma[i] + beta[i];
}
```

呼叫：

```c
// B 條 rows，每條長 N；gamma, beta 長度 N
layernorm_welford<<<B, LN_BLOCK_SIZE>>>(d_x, d_gamma, d_beta, d_out, N);
```

**Welford vs 兩趟法對比**：

| | 兩趟法 | Welford 單趟 |
|--|--------|-------------|
| Global memory 讀取 | 2N（pass 1 + pass 2） | 1N |
| 數值穩定性 | 可能 catastrophic cancellation | 穩定 |
| 實作複雜度 | 低 | 高（parallel merge 公式） |
| 適用場景 | N 小、精度要求低 | 大 N、生產級 kernel |

---

## 40.8 Kernel Fusion 的動機與限制

Softmax、LayerNorm 是 memory-bound kernel——主要時間花在 DRAM 讀寫，不是計算。把多個 memory-bound kernel 合併（fuse）成一個，就能省掉中間結果的 DRAM 落地。

常見 fusion 場景：

```
案例 1：LayerNorm + Residual Add
原本：
  y = layernorm(x)               → 寫 y 到 DRAM
  z = y + residual               → 讀 y 再讀 residual 再寫 z

Fused：
  z_i = layernorm(x)_i + residual_i   → 省一次 y 的讀寫

案例 2：Softmax + Dropout
原本：
  a = softmax(logit)             → 寫 a 到 DRAM
  b = dropout(a, mask)           → 讀 a 再讀 mask 再寫 b

Fused：
  b_i = softmax(logit)_i * mask_i / (1 - p)  → a 從不落地
```

**限制**：
- 兩個 kernel 必須對同一組 data 做相同 mapping（不然沒辦法 fuse 進同一個 thread block）
- Pass 之間有 synchronization barrier（全 block 同步）的地方不能省
- 過度 fusion 會讓 kernel 太複雜，register spill 反而更慢

Ch 43 會系統性地介紹 kernel fusion 工具與策略（`torch.compile`、Triton fusion）。本章先記住這個設計動機。

---

## 40.9 踩雷

**1. `__shfl_xor_sync` mask 寫錯**

寫成 `0xffffffff` 是假設整個 warp 都 active。如果 N 不是 BLOCK_SIZE 的整數倍，最後一個 warp 可能有些 lane 沒有參與計算但仍在 reduction 路徑上，這時要確保 inactive thread 帶入 identity element（max → -FLT_MAX，sum → 0），不然 reduction 結果錯誤。

**2. exp 的 register pressure**

1-pass softmax 的 Pass 1 對每個元素呼叫 `expf` 一次，Pass 2 又呼叫一次。如果 N 很大而 BLOCK_SIZE 小，每個 thread 處理很多元素，register 壓力上升。可以在 Pass 1 時把 x_i 存回 shared memory（犧牲 shmem 換省一次 global 讀取），但 shmem 有 48KB 上限（T4），N > 12288 時存不下 FP32。

**3. Welford merge 的除法 guard**

`M2 += delta * delta * (count * count_b / count_c)` 裡，如果 count_c = 0（兩個 thread 都沒有 active 元素），就會除零。加上 `if (count_c > 0.0f)` guard 是必要的，不是防禦性程式設計過度。

**4. rsqrtf vs sqrtf + 除法**

LayerNorm 最後 `/ sqrt(var + eps)` 用 `rsqrtf(var + eps)` 搭配乘法替代。在 GPU 上 `rsqrtf` 是硬體指令，比 `sqrtf` + 除法快 2-4 倍；但精度略低（約 ULP 2 誤差），在訓練場景可接受，推論場景也基本無影響。

**5. gridDim 超出限制**

`softmax_3pass<<<B, BLOCK_SIZE>>>`，B 是 batch 大小。T4 的 gridDim.x 上限是 2^31 - 1，不是問題；但 blockDim 上限是 1024。如果 BLOCK_SIZE > 1024，kernel 啟動會靜默失敗——`cudaGetLastError()` 才會抓到。

---

## 40.10 動手練習

1. **實作並驗證 3-pass softmax**：在 Colab 上寫 `softmax_3pass` kernel，測試輸入 `x = [100.0, 101.0, 102.0]`，驗證輸出與 `torch.softmax` 的差距 < 1e-5。（Colab 預期，未在本機實測）

2. **1-pass vs 3-pass 延遲對比**：用 `torch.cuda.Event` 計時，測 N = 512 / 2048 / 8192 三種 row 長度，各跑 1000 次，記錄平均延遲和標準差。預期 1-pass 在大 N 時有 20-30% 優勢。

3. **Welford 數值穩定性測試**：構造一組 mean = 1e8、variance = 1.0 的數值（即 x_i = 1e8 + N(0,1)），分別用兩趟法和 Welford 算 variance，對比與 numpy float64 參考值的差距。

4. **LayerNorm + residual add fusion**：在 Welford kernel 的最後一趟輸出時加上 `+ residual[row * N + i]`，並用 `torch.nn.LayerNorm` + 手動相加驗證。

---

## 40.11 本章重點

- Softmax / LayerNorm 是 memory-bound kernel，優化重點在減少 global memory 讀取次數，不在提高計算吞吐。
- 不減 max 的 softmax 在 x > 88 時 overflow → NaN，必須用 max-subtracted 版本。
- 3-pass stable softmax：Pass 1 找 max，Pass 2 算 sum，Pass 3 歸一化，共讀 3N 個 float。
- Online 1-pass softmax（Milakov & Gimelshein）：correction factor `exp(m_old - m_new)` 讓 running sum 在 max 更新時正確縮放，總讀取降到 2N；這是 FlashAttention 的核心機制。
- Welford online algorithm：兩個 delta（一個對舊 mean，一個對新 mean）的積等效於 Σ(x_i - mean_N)²，數值穩定且單趟完成 mean + variance。
- Block reduction 分兩層：warp shuffle（無 shared memory 同步開銷）+ 跨 warp 的 shared memory 匯集。
- Kernel fusion 把多個 memory-bound kernel 合成一個，省掉中間結果的 DRAM 讀寫；Ch 43 深入。

---

## 40.12 自我檢核

1. 為什麼 `exp(100.0f)` 在 FP32 下會產生 `inf`？減去 row max 後，exp 的輸入範圍被限制在哪個區間？

2. Online 1-pass softmax 中，correction factor `exp(m_old - m_new)` 的值：(a) 當新元素比 running max 小時等於多少？(b) 當新元素更大時，correction factor > 1 還是 < 1？為什麼？

3. Welford 算法用兩個 delta：`delta = x - mean_old` 和 `delta2 = x - mean_new`。如果改成 `M2 += (x - mean_new)^2`（只用新 mean），結果會有什麼問題？

4. BLOCK_SIZE = 256 時，block reduction 需要幾個 warp？跨 warp 合併時，只讓 warp 0 做，理由是什麼（而非讓 thread 0 做）？

5. `rsqrtf(var + eps)` 和 `1.0f / sqrtf(var + eps)` 在 GPU 上的差異是什麼？什麼場景下後者會更好？

---

## 40.13 延伸閱讀

- **Milakov & Gimelshein (2018)**：*Online normalizer calculation for softmax*，arXiv:1805.02867。Online 1-pass 算法的原始論文，兩頁，值得直接看。
- **Welford (1962)**：*Note on a method for calculating corrected sums of squares and products*，Technometrics 4(3):419-420。兩頁，original algorithm。
- **Chan, Golub, LeVeque (1979)**：*Updating formulae and a pairwise algorithm for computing sample variances*。Welford 的 parallel merge 公式來源。
- **FlashAttention（Dao et al., 2022）**：arXiv:2205.14135。Ch 41 的主題，以本章的 online softmax 為基礎，在 tiled attention 中實現 O(N) memory。
- **NVIDIA cuDNN source**：LayerNorm kernel 實作，展示如何在 production 場景處理 FP16/BF16 和 backward pass。
- **PyTorch `torch.nn.functional.layer_norm`**：trace 到底層可以看到 `layer_norm_kernel` 的 CUDA 實作，是真實 production kernel 的好範例。

---

→ [Ch 41 FlashAttention](./41-flash-attention.md)
