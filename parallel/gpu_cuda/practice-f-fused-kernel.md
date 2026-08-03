# 練習 F — 手寫 Fused Kernel：Bias + GELU + Dropout

**所屬 Part**：Part 8 深度學習 kernel 設計（Ch 38–45）
**前置**：已完成 Ch 40（elementwise reduction pattern）、Ch 43（kernel fusion 動機），熟悉 `torch.utils.cpp_extension.load()` JIT 編譯流程
**環境**：Colab T4 (sm_75, CUDA 12.x)；本練習所有期望數字以 T4 為準，作者環境 Windows 無 nvcc，所有輸出為「Colab 預期行為」

---

## 背景動機

Transformer FFN layer 的核心計算通常長這樣：

```
y = dropout(GELU(x @ W1 + bias), p=p_drop)
```

`x @ W1` 是 GEMM，貴但 compute-bound，tensor core 吃飽。麻煩的是後面三個 elementwise op——Bias add、GELU、Dropout——每一個單獨跑都是 memory-bound：讀一次 HBM，算幾個 FLOP，寫回去，下一個 kernel 再讀一次。

以 N×D 矩陣（N 個 token，D = hidden dim）為例：

| 版本 | Kernel 數 | HBM 讀 | HBM 寫 | 總流量 |
|------|-----------|---------|---------|--------|
| Unfused | 3 | 3 × N×D | 3 × N×D | 6 × N×D |
| Fused | 1 | 1 × N×D | 1 × N×D | 2 × N×D |

三倍的 HBM 流量差。N=4096、D=4096、FP32 的情況下，unfused 版光是搬資料就要 `6 × 4096 × 4096 × 4 bytes ≈ 384 MB`；fused 版只需要 128 MB。T4 的記憶體帶寬約 300 GB/s——省掉的 256 MB 就是接近 1 ms 的裸帶寬上限。實際加速因為 kernel launch overhead 和 L2 partial hit 通常在 2–4× 之間。

這不是學術玩具：FlashAttention（Ch 41）、cuBLAS LT epilogue、torch.compile 的 inductor 都在用同樣的思路。我們手寫這個 kernel 的目的是讓你知道 fusion 的「機制」，而不只是「效果」。

Ch 40 告訴你 reduction kernel 為什麼不能輕易 fuse（需要 cross-row synchronization）。Elementwise 沒有這個限制：每個輸出元素只依賴自己位置的輸入，每個 thread 獨立運作，不需要 shared memory、不需要 `__syncthreads`。這讓 fusion 幾乎是「免費」的——正確性不會變，程式碼量也不會暴增，換來的是帶寬減半。Ch 43 從理論角度分析過 fusion 的收益與限制；本練習是動手實作那一面。

---

## 精確規格

**輸入：**
- `x`：shape `(N, D)`，dtype `float32`，CUDA device tensor
- `bias`：shape `(D,)`，dtype `float32`，CUDA device tensor
- `p_drop`：Python `float`，dropout 機率，範圍 `[0.0, 1.0)`

**輸出：**
- `y`：shape `(N, D)`，dtype `float32`，CUDA device tensor，in-place 寫入 `x` 或分配新 tensor 均可

**操作定義（依序）：**

1. **Bias add**：`z = x + bias`（broadcast bias 沿 row 維度）
2. **GELU**（tanh 近似）：

```
gelu(z) = 0.5 * z * (1 + tanh(sqrt(2/π) * (z + 0.044715 * z^3)))
```

其中 `sqrt(2/π) ≈ 0.7978845608`

3. **Dropout**（training mode）：
   - Bernoulli mask：每個元素獨立以機率 `p_drop` 被 zero out
   - 未被 zero out 的元素 scale by `1.0 / (1.0 - p_drop)`
   - 當 `p_drop == 0.0` 時退化為 identity（不得改變數值）

**正確性門檻：**
- 與 PyTorch CPU 計算比對（dropout 用固定 seed）
- Bias add 與 GELU 部分：`atol=1e-5, rtol=1e-4`（FP32 精度）
- Dropout mask 部分：驗證 zero 的比例落在 `[p_drop ± 0.05]` 以內（統計正確性）

**效能預期（Colab T4）：**
- Unfused 版：3 kernel launches（torch bias add、GELU、Dropout 各一）
- Fused 版：1 kernel launch
- N=4096, D=4096 時，fused 版預期比 unfused 版快 1.5–3×（帶寬受限的典型區間）

---

## 預期輸出（在 Colab）

成功的跑法應該看到：

```
[正確性驗證]
bias+gelu 最大誤差: 3.2e-07  ✓ (atol=1e-5)
dropout zero 比例: 0.2991  (target=0.30)  ✓

[計時結果 N=4096 D=4096]
unfused: 0.412 ms  (3 kernel launches)
fused:   0.157 ms  (1 kernel launch)
speedup: 2.62x

[kernel 數量驗證]
unfused kernels: 3
fused kernels:   1
```

計時數字每次跑會有 ±5–10% 的抖動，不用追求完全一致。重要的是 fused 版穩定快於 unfused，以及正確性驗證通過。如果 fused 版反而更慢，請先確認矩陣夠大（D < 256 時 overhead 可能蓋過收益）再懷疑 kernel 邏輯。

---

## 「卡住了」提示（漸進式，先讀第一條就好）

**提示 1：在 Colab 裡怎麼 JIT 編譯自訂 kernel**

不需要 `setup.py`，用 `torch.utils.cpp_extension.load()` 即時編譯：

```python
from torch.utils.cpp_extension import load
import os

fused_bgd = load(
    name="fused_bgd",
    sources=["fused_bgd.cu", "fused_bgd.cpp"],
    extra_cuda_cflags=["-O2", "-arch=sm_75"],
    verbose=True,
)
```

第一次跑會花 20–60 秒編譯；之後 PyTorch 會快取編譯結果，重啟 runtime 才需要重新編譯。`.cu` 和 `.cpp` 放在當前目錄即可，`%%writefile` magic 是最快的寫法。

**提示 2：GELU tanh 近似的 CUDA C 寫法**

`tanhf`、`sqrtf` 是 CUDA device function，直接在 kernel 裡用：

```cuda
__device__ __forceinline__ float gelu_tanh(float x) {
    const float k = 0.7978845608f;  // sqrt(2/pi)
    const float a = 0.044715f;
    float inner = k * (x + a * x * x * x);
    return 0.5f * x * (1.0f + tanhf(inner));
}
```

`__forceinline__` 讓編譯器內聯這個函式到 kernel 裡，避免 register spill。不加也行，差距很小。

**提示 3：curand_state 的初始化方式**

Dropout 需要亂數。在 kernel 內用 curand 的標準寫法：

```cuda
#include <curand_kernel.h>

__global__ void kernel(..., unsigned long long seed) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    curandState state;
    curand_init(seed, idx, 0, &state);  // seed, sequence, offset
    float r = curand_uniform(&state);   // (0, 1]
    // r < p_drop → zero out
}
```

`curand_init` 的開銷不小——如果是反覆呼叫的場景（training loop），應該把 `curandState` 存在 global memory 讓 kernel 之間複用。本練習為了簡化，每次呼叫重新初始化，接受這個效能代價。

**提示 4：怎麼算 grid/block 讓每個 thread 處理一個元素**

N×D 個元素，展平成一維，每個 thread 算一個：

```cuda
int total = N * D;
int block_size = 256;
int grid_size = (total + block_size - 1) / block_size;
kernel<<<grid_size, block_size>>>(...)
```

在 kernel 內：

```cuda
int idx = blockIdx.x * blockDim.x + threadIdx.x;
if (idx >= N * D) return;  // 邊界保護
int row = idx / D;         // 哪個 token
int col = idx % D;         // 哪個 feature（對應 bias index）
```

這是最簡單的 1D 展平方式，coalescing 自然成立（相鄰 thread 訪問相鄰 column）。

**提示 5：為什麼不需要 `__syncthreads()`**

`__syncthreads()` 的用途是讓同一個 block 內的所有 thread 等齊，確保 shared memory 寫入對其他 thread 可見。我們的 fused kernel 沒有用 shared memory——每個 thread 獨立讀 global memory 的一個元素、算完、寫回，完全不互相依賴。加 `__syncthreads()` 不只沒用，在某些情況下還會導致死鎖（如果 grid 裡有些 thread 因為 `idx >= N*D` 提早 return，而其他 thread 卡在 `__syncthreads()`）。

需要 `__syncthreads()` 的情況是：thread A 寫了 `shmem[i]`，thread B 需要讀 `shmem[i]`——必須同步才能保證 B 讀到 A 寫的值。本練習不存在這種相依，所以完全不需要。

---

## 5 步實作路線

按這個順序做，每一步先驗正確性再往下走，不要一次全寫完才跑。

**Step 1：只做 Bias Add，驗證正確**

先把 kernel 框架寫好：`curand_init`、index 計算、bias broadcast（`bias[col]`）。只做加法，不做 GELU 也不做 Dropout。和 `x + bias` 的 PyTorch 結果比對，確認最大誤差 < 1e-6。這步只是確認 indexing 對了。

**Step 2：加上 GELU，驗證正確**

在 bias add 之後接上 `gelu_tanh()` device function。和 PyTorch 的 `F.gelu(x + bias, approximate='tanh')` 比對，誤差應在 1e-5 以內。如果誤差偏大，先確認常數：`0.7978845608f` 和 `0.044715f`，以及 `tanhf` 而不是 `tanh`（後者在 kernel 裡可能 fallback 到 double）。

**Step 3：加上 Dropout，驗證機率分佈**

加入 `curand_state` 初始化和 Bernoulli mask。正確性驗證方式：設 `p_drop = 0.3`，跑 N=65536, D=64，統計輸出裡 zero 的比例，應落在 `[0.25, 0.35]`。同時驗證未 zero out 的元素是否有被 scale `1/(1-p)` 放大。

**Step 4：計時對比 unfused vs fused**

Unfused 版用純 PyTorch：`y = F.dropout(F.gelu(x + bias, approximate='tanh'), p=p_drop, training=True)`。用 `torch.cuda.Event` 計時兩版，各跑 100 次取平均。注意要有 warm-up（先跑 10 次不計時），否則第一次 kernel launch 的 CUDA context 初始化會污染數字。

**Step 5：用 Nsight 或 nvprof 確認 kernel 數**

在 Colab 裡：

```python
with torch.autograd.profiler.profile(use_cuda=True) as prof:
    # unfused
    y_unfused = F.dropout(F.gelu(x + bias, approximate='tanh'), p=p_drop, training=True)
print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=10))
```

看 CUDA events 裡出現幾個 kernel。Unfused 應看到至少 3 個 kernel（bias/GELU/Dropout 各自的 PyTorch 內建 kernel）；fused 應看到 1 個。

---

## 參考解答

<details>
<summary>展開完整可執行程式碼（建議先自己做完 Step 1–3 再看）</summary>

### fused_bgd.cu

```cuda
// fused_bgd.cu
// Fused kernel: Bias Add + GELU (tanh approx) + Dropout (training mode)
// Compile with: nvcc -O2 -arch=sm_75 --compiler-options=-fPIC -shared

#include <cuda_runtime.h>
#include <curand_kernel.h>
#include <math.h>

// -----------------------------------------------------------------------
// GELU (tanh approximation)
// gelu(x) = 0.5 * x * (1 + tanh(sqrt(2/pi) * (x + 0.044715 * x^3)))
// -----------------------------------------------------------------------
__device__ __forceinline__ float gelu_tanh(float x) {
    const float k  = 0.7978845608028654f;  // sqrt(2.0f / M_PI)
    const float a  = 0.044715f;
    float inner    = k * (x + a * x * x * x);
    return 0.5f * x * (1.0f + tanhf(inner));
}

// -----------------------------------------------------------------------
// Fused kernel: one thread per element
// x    : (N, D) float32
// bias : (D,)   float32
// y    : (N, D) float32  (output)
// N, D : dimensions
// p    : dropout probability (training mode)
// seed : curand seed
// -----------------------------------------------------------------------
__global__ void fused_bias_gelu_dropout_kernel(
    const float* __restrict__ x,
    const float* __restrict__ bias,
    float*       __restrict__ y,
    int N,
    int D,
    float p_drop,
    unsigned long long seed
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    int total = N * D;
    if (idx >= total) return;

    // Decode (row, col)
    int col = idx % D;

    // Step 1: Bias add
    float val = x[idx] + bias[col];

    // Step 2: GELU
    val = gelu_tanh(val);

    // Step 3: Dropout (Bernoulli mask, scale by 1/(1-p))
    if (p_drop > 0.0f) {
        curandState state;
        curand_init(seed, (unsigned long long)idx, 0ULL, &state);
        float r = curand_uniform(&state);  // uniform in (0, 1]
        if (r <= p_drop) {
            val = 0.0f;
        } else {
            val *= 1.0f / (1.0f - p_drop);
        }
    }

    y[idx] = val;
}

// -----------------------------------------------------------------------
// Launcher (called from .cpp binding)
// -----------------------------------------------------------------------
void launch_fused_bgd(
    const float* x,
    const float* bias,
    float*       y,
    int N,
    int D,
    float p_drop,
    unsigned long long seed
) {
    int total      = N * D;
    int block_size = 256;
    int grid_size  = (total + block_size - 1) / block_size;

    fused_bias_gelu_dropout_kernel<<<grid_size, block_size>>>(
        x, bias, y, N, D, p_drop, seed
    );
    // Caller handles CUDA error checking
}
```

### fused_bgd.cpp

```cpp
// fused_bgd.cpp
// PyTorch C++ binding for the fused kernel

#include <torch/extension.h>
#include <cuda_runtime.h>
#include <stdexcept>
#include <ctime>

// Forward declaration of CUDA launcher
void launch_fused_bgd(
    const float* x,
    const float* bias,
    float*       y,
    int N, int D,
    float p_drop,
    unsigned long long seed
);

// PyTorch-facing function
torch::Tensor fused_bias_gelu_dropout(
    torch::Tensor x,      // (N, D) float32 cuda
    torch::Tensor bias,   // (D,)   float32 cuda
    float p_drop,
    long long seed        // -1 = use time-based seed
) {
    // Input validation
    TORCH_CHECK(x.is_cuda(),    "x must be a CUDA tensor");
    TORCH_CHECK(bias.is_cuda(), "bias must be a CUDA tensor");
    TORCH_CHECK(x.dtype() == torch::kFloat32,    "x must be float32");
    TORCH_CHECK(bias.dtype() == torch::kFloat32, "bias must be float32");
    TORCH_CHECK(x.dim() == 2,    "x must be 2D (N, D)");
    TORCH_CHECK(bias.dim() == 1, "bias must be 1D (D,)");
    TORCH_CHECK(x.size(1) == bias.size(0),
                "x.size(1) must equal bias.size(0)");
    TORCH_CHECK(p_drop >= 0.0f && p_drop < 1.0f,
                "p_drop must be in [0, 1)");

    // Contiguous memory
    auto x_c    = x.contiguous();
    auto bias_c = bias.contiguous();

    int N = (int)x_c.size(0);
    int D = (int)x_c.size(1);

    // Allocate output
    auto y = torch::empty_like(x_c);

    // Resolve seed
    unsigned long long use_seed = (seed < 0)
        ? (unsigned long long)std::time(nullptr)
        : (unsigned long long)seed;

    launch_fused_bgd(
        x_c.data_ptr<float>(),
        bias_c.data_ptr<float>(),
        y.data_ptr<float>(),
        N, D, p_drop, use_seed
    );

    // Check for kernel errors
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        throw std::runtime_error(
            std::string("CUDA kernel error: ") + cudaGetErrorString(err)
        );
    }

    return y;
}

// Register the function as a PyTorch extension
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("fused_bias_gelu_dropout", &fused_bias_gelu_dropout,
          "Fused Bias Add + GELU (tanh) + Dropout kernel",
          py::arg("x"),
          py::arg("bias"),
          py::arg("p_drop") = 0.0f,
          py::arg("seed")   = -1LL);
}
```

### Colab 完整跑法

把以下儲存格依序執行：

**儲存格 1：寫 kernel 檔案**

```python
# 儲存格 1：寫 .cu 和 .cpp 到 Colab 磁碟
# (把上面的 fused_bgd.cu 和 fused_bgd.cpp 貼進 %%writefile 魔術命令)

%%writefile fused_bgd.cu
// ... (貼上完整 fused_bgd.cu 內容) ...
```

```python
%%writefile fused_bgd.cpp
// ... (貼上完整 fused_bgd.cpp 內容) ...
```

**儲存格 2：JIT 編譯**

```python
# 儲存格 2：JIT 編譯（第一次約 30-60 秒）
import torch
from torch.utils.cpp_extension import load

fused_bgd_ext = load(
    name="fused_bgd",
    sources=["fused_bgd.cu", "fused_bgd.cpp"],
    extra_cuda_cflags=["-O2", "-arch=sm_75", "-lcurand"],
    verbose=True,
)
print("編譯完成")
```

**儲存格 3：正確性驗證**

```python
# 儲存格 3：正確性驗證
import torch
import torch.nn.functional as F

torch.manual_seed(42)
N, D = 1024, 768
p_drop = 0.3
SEED = 12345

x    = torch.randn(N, D, device='cuda')
bias = torch.randn(D, device='cuda')

# Fused kernel 輸出
y_fused = fused_bgd_ext.fused_bias_gelu_dropout(x, bias, p_drop, SEED)

# PyTorch CPU reference（dropout 用固定 seed）
x_cpu    = x.cpu()
bias_cpu = bias.cpu()

# Step 1+2: bias + gelu (確定性，可直接對比)
y_ref_no_drop = F.gelu(x_cpu + bias_cpu, approximate='tanh')

# 正確性驗證：先比對 GELU 部分（找 fused 結果中未被 dropout 的元素）
# 策略：p_drop=0 時驗精確度
y_no_drop = fused_bgd_ext.fused_bias_gelu_dropout(x, bias, 0.0, SEED)
y_ref_nd  = F.gelu(x.cpu() + bias.cpu(), approximate='tanh').to('cuda')

max_err = (y_no_drop - y_ref_nd).abs().max().item()
print(f"[Bias+GELU 正確性]")
print(f"  最大誤差: {max_err:.2e}  {'✓' if max_err < 1e-4 else '✗ 超出容忍'}")

# Dropout 比例驗證（大矩陣才有統計意義）
N_big, D_big = 65536, 64
x_big    = torch.ones(N_big, D_big, device='cuda')
bias_big = torch.zeros(D_big, device='cuda')

y_big = fused_bgd_ext.fused_bias_gelu_dropout(x_big, bias_big, p_drop, SEED)
zero_ratio = (y_big == 0).float().mean().item()
print(f"\n[Dropout 統計正確性]")
print(f"  zero 比例: {zero_ratio:.4f}  (target={p_drop})")
ok = abs(zero_ratio - p_drop) < 0.05
print(f"  {'✓' if ok else '✗ 超出 ±0.05 容忍'}")

# Scale 驗證（非 zero 元素應被放大 1/(1-p)）
expected_val = 1.0 / (1.0 - p_drop)  # gelu(0+0)=0 → 用 p=0 然後跑無 dropout 對比
# 改用 x=1 bias=0，gelu(1)=0.8412 的情況
x_ones = torch.ones(1024, 64, device='cuda')
b_zero = torch.zeros(64, device='cuda')
y_scale = fused_bgd_ext.fused_bias_gelu_dropout(x_ones, b_zero, p_drop, SEED)
y_nogelu_nodrop = F.gelu(x_ones, approximate='tanh')  # reference: gelu(1)
nonzero_mask = y_scale != 0
if nonzero_mask.any():
    actual_scale = (y_scale[nonzero_mask] / y_nogelu_nodrop[nonzero_mask]).mean().item()
    print(f"\n[Scale 驗證]")
    print(f"  實際 scale: {actual_scale:.4f}  (期望 {expected_val:.4f})")
    print(f"  {'✓' if abs(actual_scale - expected_val) < 0.01 else '✗'}")
```

**儲存格 4：計時對比**

```python
# 儲存格 4：Unfused vs Fused 計時對比
import torch
import torch.nn.functional as F

def bench(fn, n_warmup=10, n_run=100):
    """用 CUDA Event 計時，回傳平均毫秒"""
    # Warm-up
    for _ in range(n_warmup):
        fn()
    torch.cuda.synchronize()

    start = torch.cuda.Event(enable_timing=True)
    end   = torch.cuda.Event(enable_timing=True)

    start.record()
    for _ in range(n_run):
        fn()
    end.record()

    torch.cuda.synchronize()
    return start.elapsed_time(end) / n_run  # ms

configs = [
    (1024, 768,  "N=1024  D=768  (BERT-base FFN input)"),
    (4096, 4096, "N=4096  D=4096 (LLaMA-style)"),
    (4096, 512,  "N=4096  D=512  (小 head)"),
]

p_drop = 0.1

for N, D, label in configs:
    x    = torch.randn(N, D, device='cuda')
    bias = torch.randn(D, device='cuda')

    def unfused():
        return F.dropout(
            F.gelu(x + bias, approximate='tanh'),
            p=p_drop, training=True
        )

    def fused():
        return fused_bgd_ext.fused_bias_gelu_dropout(x, bias, p_drop, 42)

    t_unfused = bench(unfused)
    t_fused   = bench(fused)
    speedup   = t_unfused / t_fused

    print(f"\n{label}")
    print(f"  unfused: {t_unfused:.3f} ms")
    print(f"  fused:   {t_fused:.3f} ms")
    print(f"  speedup: {speedup:.2f}x")
```

**儲存格 5：kernel 數量驗證（PyTorch profiler）**

```python
# 儲存格 5：確認 kernel launch 數量
import torch
import torch.nn.functional as F

N, D = 4096, 4096
p_drop = 0.1
x    = torch.randn(N, D, device='cuda')
bias = torch.randn(D, device='cuda')

print("=== Unfused ===")
with torch.profiler.profile(
    activities=[torch.profiler.ProfilerActivity.CUDA],
    record_shapes=False,
) as prof:
    y = F.dropout(F.gelu(x + bias, approximate='tanh'), p=p_drop, training=True)
    torch.cuda.synchronize()

events = [e for e in prof.key_averages() if e.self_cuda_time_total > 0]
print(f"CUDA kernel events: {len(events)}")
for e in events:
    print(f"  {e.key}  ({e.self_cuda_time_total/1000:.3f} ms)")

print("\n=== Fused ===")
with torch.profiler.profile(
    activities=[torch.profiler.ProfilerActivity.CUDA],
    record_shapes=False,
) as prof:
    y = fused_bgd_ext.fused_bias_gelu_dropout(x, bias, p_drop, 42)
    torch.cuda.synchronize()

events = [e for e in prof.key_averages() if e.self_cuda_time_total > 0]
print(f"CUDA kernel events: {len(events)}")
for e in events:
    print(f"  {e.key}  ({e.self_cuda_time_total/1000:.3f} ms)")
```

</details>

---

## 測試用例

下面三個測試用例覆蓋常見情境和邊界，自己動手之前先想清楚每個會有什麼特殊行為。

**測試 1：典型 BERT-base FFN 尺寸**

```python
N, D, p_drop = 1024, 768, 0.1
# BERT-base 的 FFN intermediate = 3072，這是輸入尺寸
# 驗證點：正確性（atol=1e-5）、dropout zero 比例 [0.05, 0.15]
```

**測試 2：大矩陣（帶寬測試的主角）**

```python
N, D, p_drop = 4096, 512, 0.3
# N 夠大讓 GPU 飽和，D=512 是常見 head dim
# 驗證點：speedup 應 > 1.5x；zero 比例 [0.25, 0.35]
```

**測試 3：邊界 — p_drop=0.0 和 p_drop 接近 1.0**

```python
# 測試 3a: p_drop=0.0 → 必須退化為純 bias+gelu，沒有任何元素被歸零
N, D = 512, 256
y = fused_bgd_ext.fused_bias_gelu_dropout(x, bias, 0.0, 42)
y_ref = F.gelu(x + bias, approximate='tanh')
assert (y.cpu() - y_ref.cpu()).abs().max() < 1e-5, "p_drop=0 不應改變值"
assert (y == 0).sum() == 0 or (y_ref == 0).sum() == (y == 0).sum(), \
    "p_drop=0 不應額外引入 zero"

# 測試 3b: p_drop=0.9 → 90% 的元素應被歸零
y_high = fused_bgd_ext.fused_bias_gelu_dropout(x, bias, 0.9, 42)
zero_ratio = (y_high == 0).float().mean().item()
assert 0.85 < zero_ratio < 0.95, f"p_drop=0.9 時 zero 比例應在 [0.85, 0.95]，實際 {zero_ratio:.3f}"
print("邊界測試通過")
```

---

## 延伸挑戰

做完基本題之後，這三個方向難度依序上升。

**挑戰 1：支援 FP16（`__half`）**

把 kernel 的 float 換成 `__half`，使用 `__half2float()` / `float2half_rn()` 或直接用 `half2` SIMD 指令一次算兩個元素。GELU 的 `tanhf` 要換成 `__htanh()`（如果存在）或先轉 float 再算再轉回 half。驗證方式：與 `x.half()` 的 PyTorch 計算結果比對（atol=1e-2，half 精度較低）。預期額外加速：FP16 帶寬翻倍，理論上 throughput 再快 1.5–2×。

**挑戰 2：加入 LayerNorm（需要 reduction）**

在 GELU 之後加一層 LayerNorm（對每個 row 做 normalize）。難點：每個 row 需要計算 mean 和 variance，這要 warp-level reduction（`__shfl_down_sync`）。單純的 elementwise 設計不再夠用——需要每個 block 負責一個 row，block 內做 reduction。這是 Ch 40 的核心，此時 fusion 的難度從「組合 elementwise」升到「跨 reduction 邊界 fusion」。回頭讀 Ch 40 再動手。

**挑戰 3：用 Triton 重寫**

同樣的 bias+GELU+dropout，用 Triton（Python）重寫。Triton 的優勢：不需要手寫 curand，用 `tl.rand` 即可；不需要寫 .cpp binding；code 量少一半。比較兩個版本的效能和 code 可讀性。Triton 的入口在 Ch 37，fusion 的寫法在 `@triton.jit` 裡直接順序撰寫，編譯器自動 fuse。

---

## 自我檢核

完成後用這六條確認你真的做對了，而不只是程式跑了沒報錯：

- [ ] `p_drop=0.0` 時，fused kernel 的輸出與 `F.gelu(x + bias, approximate='tanh')` 逐元素誤差 < 1e-5——確認 GELU 數學正確
- [ ] `p_drop=0.3`，N×D = 65536 × 64 時，zero 元素比例落在 `[0.25, 0.35]`——確認 Bernoulli mask 機率正確
- [ ] 非 zero 元素有被乘以 `1/(1-p_drop)` ——確認 training mode scaling 正確
- [ ] profiler 確認 fused 版只有 1 個 CUDA kernel event，unfused 版有 3 個——確認 fusion 實際有效，而不是 PyTorch 偷偷合併了
- [ ] N=4096, D=4096 時 speedup ≥ 1.5×——確認大矩陣有明顯加速（小矩陣可能反而慢，因為 curand_init overhead 不小）
- [ ] 跑 1000 次正確性驗證（不同 seed），沒有任何一次 dropout zero 比例超出 `[p_drop ± 0.05]`——確認亂數品質穩定

---

→ [Final Project：優化 GEMM](./final-project-optimized-gemm.md)
