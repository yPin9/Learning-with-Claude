# Ch 43 — PyTorch 底層：Custom CUDA Extension 與 Kernel Fusion

> **目標**：從零寫一個 fused bias+GELU CUDA extension，理解 PyTorch dispatcher 架構、tensor accessor 選擇策略、以及何時該放棄 `torch.compile` 改自己下場。
>
> **環境**：Colab T4 / V100（`nvcc --version` ≥ 11.0；`torch.__version__` ≥ 2.0）

---

## 為什麼要繞過 Python 直接寫 CUDA？

PyTorch 已經很快了。`torch.relu(x + bias)` 兩行就能跑。問題是它實際上觸發了**兩個 kernel launch**：一個 add，一個 relu。每次 kernel launch 有約 **5μs** 的固定 overhead（driver 排隊 + SM 初始化），兩次加起來 10μs。

更致命的是 **HBM 往返**：第一個 kernel 把結果寫回 HBM（全域記憶體），第二個 kernel 再讀回來。一個 4096×4096 的 float32 tensor 是 64 MB，A100 的 HBM 頻寬是 2 TB/s，所以光讀寫就要浪費 **~32μs**。對於 sequence length=4096、hidden=4096 的 transformer 模型，這類 elementwise 融合點多達數十個，累積下來就是幾毫秒的白送。

Kernel fusion（核函數融合）把多個 elementwise 操作合進同一個 kernel：input 從 HBM 讀一次、做所有計算、寫一次。overhead 從 N×5μs 降到 1×5μs，HBM 往返從 N 次降到 1 次。

---

## PyTorch 怎麼把 Python 呼叫送到 CUDA Kernel

路徑：**Python → C++ dispatcher → ATen kernel → CUDA kernel**

```
Python: torch.relu(x)
         ↓
C++ ATen dispatcher
  - 看 tensor 的 device / dtype / layout
  - 派發到正確的後端實作
         ↓
ATen kernel (e.g. at::relu_cuda)
  - 檢查 contiguous、dispatch floating type
  - 計算 block/grid dim
         ↓
CUDA kernel (.cu)  ← 我們自己寫的落在這層
```

Dispatcher 的核心是 `DispatchKey`。每個 tensor 帶著一組 key（CUDA、AutogradCUDA、BatchedNestedTensor…），dispatcher 依序查表找到第一個有實作的後端。我們不需要深入 dispatcher 的 C++ 模板機制，只需要知道：**只要透過 `torch::Tensor` interface 暴露函數，dispatcher 就能幫我們處理 autograd 以外的事**。

Autograd 另算：如果你的 fused kernel 需要 backward，要麼用 `torch::autograd::Function`，要麼讓外層 PyTorch 的 autograd graph 自動 compose（常見做法是 forward 手寫、backward 讓 PyTorch 自動微分）。

---

## 完整範例：Fused Bias + GELU

GELU（Gaussian Error Linear Unit）在 GPT 系列模型的 FFN 層大量使用：

```
output = GELU(x + bias)
GELU(x) = x * 0.5 * (1 + tanh(√(2/π) * (x + 0.044715 * x³)))
```

PyTorch 原生：`out = F.gelu(x + bias)` 觸發兩個 kernel。我們要把它壓成一個。

### 1. Kernel：`fused_bias_gelu_kernel.cu`

```cuda
// fused_bias_gelu_kernel.cu
#include <cuda.h>
#include <cuda_runtime.h>
#include <torch/extension.h>  // 引入 ATen + pybind11

// tanh 近似版 GELU，比 erf 版快 ~10%
// 公式：x * 0.5 * (1 + tanh(0.7978845608 * (x + 0.044715 * x^3)))
template <typename scalar_t>
__device__ __forceinline__ scalar_t gelu_tanh(scalar_t x) {
    // 全部用 scalar_t 讓模板在 float32/float64 都正確
    const scalar_t c0 = static_cast<scalar_t>(0.7978845608028654);  // sqrt(2/pi)
    const scalar_t c1 = static_cast<scalar_t>(0.044715);
    scalar_t inner = c0 * (x + c1 * x * x * x);
    return x * static_cast<scalar_t>(0.5) * (static_cast<scalar_t>(1.0) + tanh(inner));
}

// 主 kernel：每個 thread 處理一個元素
// input  [N, hidden_size]
// bias   [hidden_size]         (broadcast 在 kernel 內做)
// output [N, hidden_size]
template <typename scalar_t>
__global__ void fused_bias_gelu_kernel(
    const scalar_t* __restrict__ input,   // __restrict__ 告訴 nvcc 這些指標不 alias
    const scalar_t*  __restrict__ bias,
    scalar_t* __restrict__ output,
    const int rows,
    const int cols
) {
    // 把 2D grid/block 對映到 1D 元素索引
    const int row = blockIdx.y * blockDim.y + threadIdx.y;
    const int col = blockIdx.x * blockDim.x + threadIdx.x;

    if (row >= rows || col >= cols) return;  // 邊界 guard

    const int idx = row * cols + col;
    // bias 只有 [cols]，每 row 重複使用
    scalar_t val = input[idx] + bias[col];
    output[idx] = gelu_tanh(val);
}

// C++ 包裝函數：PyTorch tensor 進，tensor 出
torch::Tensor fused_bias_gelu_cuda(
    torch::Tensor input,   // [batch * seq_len, hidden_size]
    torch::Tensor bias     // [hidden_size]
) {
    // 確認在 CUDA 上
    TORCH_CHECK(input.is_cuda(), "input must be a CUDA tensor");
    TORCH_CHECK(bias.is_cuda(),  "bias must be a CUDA tensor");

    // 確認 contiguous，否則 data_ptr 的步長假設不成立
    input = input.contiguous();
    bias  = bias.contiguous();

    const int rows = input.size(0);   // batch * seq_len
    const int cols = input.size(1);   // hidden_size
    auto output = torch::empty_like(input);  // 預先分配輸出

    // 2D grid：col 方向 X，row 方向 Y
    dim3 threads(32, 8);   // 256 threads/block
    dim3 blocks(
        (cols + threads.x - 1) / threads.x,
        (rows + threads.y - 1) / threads.y
    );

    // AT_DISPATCH_FLOATING_TYPES：自動在 float32 / float64 間切換
    // 如果你的模型用 float16，要改成 AT_DISPATCH_FLOATING_TYPES_AND_HALF
    // 如果還要支援 bfloat16，改成 AT_DISPATCH_FLOATING_TYPES_AND2(kBFloat16, kHalf, ...)
    AT_DISPATCH_FLOATING_TYPES(input.scalar_type(), "fused_bias_gelu_cuda", ([&] {
        fused_bias_gelu_kernel<scalar_t><<<blocks, threads>>>(
            input.data_ptr<scalar_t>(),   // 用 data_ptr<>，data<> 已棄用
            bias.data_ptr<scalar_t>(),
            output.data_ptr<scalar_t>(),
            rows,
            cols
        );
    }));

    // 同步：確認 kernel 結束（開發期加，production 可視情況移除）
    C10_CUDA_KERNEL_LAUNCH_CHECK();

    return output;
}
```

**關鍵設計決策**：

- `__restrict__` 讓 nvcc 做更激進的 load 排程，因為確認指標不互相 alias。
- `__forceinline__` 確保 `gelu_tanh` 被 inline 進 kernel，避免 device function call overhead。
- `2D grid` 讓 col 維度在 X 方向連續存取，對應 coalesced memory access（row-major layout 下相鄰 thread 存取相鄰 col）。

### 2. C++ binding：`fused_bias_gelu.cpp`

```cpp
// fused_bias_gelu.cpp
#include <torch/extension.h>

// 宣告 CUDA 函數（定義在 .cu 檔）
torch::Tensor fused_bias_gelu_cuda(torch::Tensor input, torch::Tensor bias);

// pybind11 模組定義
// TORCH_EXTENSION_NAME 是 setup.py 傳進來的模組名稱 macro
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def(
        "fused_bias_gelu",         // Python 側呼叫的函數名
        &fused_bias_gelu_cuda,     // 對應的 C++ 函數指標
        "Fused Bias + GELU (CUDA)" // docstring
    );
}
```

這個 .cpp 唯一的工作是橋接 pybind11 與 CUDA 實作。把 CUDA 程式碼留在 .cu 讓 nvcc 處理，C++ binding 用 g++ 處理，分工乾淨。

### 3. 安裝：`setup.py`

```python
# setup.py
from setuptools import setup
from torch.utils.cpp_extension import CUDAExtension, BuildExtension

setup(
    name='fused_bias_gelu_ext',
    ext_modules=[
        CUDAExtension(
            name='fused_bias_gelu_ext',          # import 時的模組名
            sources=[
                'fused_bias_gelu.cpp',
                'fused_bias_gelu_kernel.cu',
            ],
            extra_compile_args={
                'cxx':  ['-O2'],
                'nvcc': [
                    '-O2',
                    '-arch=sm_75',    # T4 是 Turing (sm_75)；V100 是 sm_70
                    '--use_fast_math',  # 用近似版 tanh，比標準版快 ~30%
                ],
            },
        ),
    ],
    cmdclass={'build_ext': BuildExtension},
)
```

編譯：

```bash
# AOT (ahead-of-time) 編譯，產生 .so
python setup.py build_ext --inplace
```

或者用 JIT 版（開發期更方便，第一次跑約 30 秒）：

```python
# jit_load.py
import torch
from torch.utils.cpp_extension import load

fused_ext = load(
    name='fused_bias_gelu_ext',
    sources=['fused_bias_gelu.cpp', 'fused_bias_gelu_kernel.cu'],
    extra_cuda_cflags=['-O2', '--use_fast_math'],
    verbose=True,  # 印出編譯指令，出錯時很有用
)
```

### 4. Python 呼叫與正確性驗證

```python
import torch
import torch.nn.functional as F

# 假設你已經 import fused_ext（AOT 或 JIT 皆可）
import fused_bias_gelu_ext as fused_ext

def test_correctness():
    torch.manual_seed(42)
    batch, hidden = 1024, 4096
    device = 'cuda'

    x    = torch.randn(batch, hidden, device=device, dtype=torch.float32)
    bias = torch.randn(hidden, device=device, dtype=torch.float32)

    # 原生 PyTorch（兩個 kernel：add + gelu）
    ref = F.gelu(x + bias, approximate='tanh')

    # 我們的 fused kernel（一個 kernel）
    out = fused_ext.fused_bias_gelu(x, bias)

    # 用 tanh 近似版，float32 下精度差應該極小
    max_err = (out - ref).abs().max().item()
    print(f"max absolute error: {max_err:.2e}")  # 期望 < 1e-5

    torch.testing.assert_close(out, ref, atol=1e-4, rtol=1e-4)
    print("correctness check passed")

test_correctness()
```

若想看 speedup：

```python
import torch.utils.benchmark as benchmark

def bench(fn, *args, label=''):
    t = benchmark.Timer(
        stmt='fn(*args)',
        globals={'fn': fn, 'args': args},
        label=label,
    )
    return t.blocked_autorange(min_run_time=1)

x    = torch.randn(1024, 4096, device='cuda', dtype=torch.float32)
bias = torch.randn(4096, device='cuda', dtype=torch.float32)

r_ref  = bench(lambda: F.gelu(x + bias, approximate='tanh'), label='PyTorch native')
r_fuse = bench(lambda: fused_ext.fused_bias_gelu(x, bias),   label='Fused CUDA')

print(r_ref)
print(r_fuse)
# T4 上典型結果：native ~45μs，fused ~25μs（節省約 40%）
```

---

## Tensor Accessor：`packed_accessor32` vs `data_ptr`

兩種存取 tensor 資料的方式，選錯了要麼 crash、要麼慢。

### `data_ptr<scalar_t>()`

```cpp
scalar_t* ptr = tensor.data_ptr<scalar_t>();
// 然後自己做 idx = row * stride + col
```

- 適合：形狀固定、stride 已知（或確認 contiguous）、需要最大彈性的 kernel。
- 我們的 fused bias+GELU 就用這個，因為我們手動計算 `row * cols + col`。
- **一定要先 `.contiguous()`**，否則 stride 可能不是你預期的。

### `packed_accessor32<scalar_t, N, torch::RestrictPtrTraits>()`

```cpp
// N = tensor 維度數
auto acc = tensor.packed_accessor32<scalar_t, 2, torch::RestrictPtrTraits>();
// kernel 內：
scalar_t val = acc[row][col];  // 自動處理 stride，安全
```

- 適合：stride 複雜（transpose 後、non-contiguous）、或想要 `[]` 語法更清楚。
- `packed_accessor32` 的 32 指用 int32 表示 index（最多 2^31 元素）；超過 2GB 的 tensor 要用 `packed_accessor64`。
- 帶 `torch::RestrictPtrTraits` 等效 `__restrict__`。
- 性能：比 `data_ptr` 略有 overhead（多一層 stride 計算），但現代 nvcc 通常能 optimize 掉。

**選擇原則**：kernel 設計確定 tensor 是 contiguous 且形狀固定 → `data_ptr`；形狀動態或可能 non-contiguous → `packed_accessor32`。

---

## AT_DISPATCH 的正確姿勢

```cpp
// 只支援 float32 / float64
AT_DISPATCH_FLOATING_TYPES(tensor.scalar_type(), "my_kernel", ([&] {
    my_kernel<scalar_t><<<blocks, threads>>>(tensor.data_ptr<scalar_t>(), n);
}));

// 要加 float16（transformer 推論常見）
AT_DISPATCH_FLOATING_TYPES_AND_HALF(tensor.scalar_type(), "my_kernel", ([&] {
    my_kernel<scalar_t><<<blocks, threads>>>(tensor.data_ptr<scalar_t>(), n);
}));

// 要加 bfloat16 + float16（現代 LLM 訓練的 dtype 組合）
AT_DISPATCH_FLOATING_TYPES_AND2(
    at::kBFloat16, at::kHalf,
    tensor.scalar_type(), "my_kernel", ([&] {
        my_kernel<scalar_t><<<blocks, threads>>>(tensor.data_ptr<scalar_t>(), n);
    })
);
```

注意：`scalar_t` 只在 `([&] { ... })` 這個 lambda 的作用域內有效，它是 macro 幫你 `typedef` 的。lambda 外面不能用。

Float16/BFloat16 的 kernel 模板化要小心：CUDA 的 `tanh`、`sqrt` 等 math 函數在 `__half` 上有對應版本，但行為略異。`--use_fast_math` 會自動選 fast path，但精度可能再降。如果你的 fused kernel 要支援 half precision，建議在 float32 驗證正確後，再單獨驗 float16 的精度邊界。

---

## `torch.compile` / TorchInductor 自動 Fusion vs 手寫 Extension

PyTorch 2.0 引入的 `torch.compile` 背後是 **TorchInductor**，它會：
1. 把你的 Python/PyTorch ops 轉成計算圖（FX graph）
2. 分析相鄰的 pointwise op，自動 fuse 成一個 Triton kernel（CPU 上生成 C++ loop）
3. 生成的 Triton kernel 在 T4/A100 上通常已接近最優

Ch 37 我們學過 Triton 手寫 kernel；TorchInductor 就是把這個流程自動化。對 `F.gelu(x + bias)` 這種簡單 fusion，`torch.compile` 大概能給你 **80-90% 的手寫 CUDA 效果**。

```python
@torch.compile
def fused_bias_gelu_torch(x, bias):
    return F.gelu(x + bias, approximate='tanh')

# 第一次呼叫：編譯 ~幾秒（JIT）
# 之後呼叫：接近手寫 CUDA 速度
out = fused_bias_gelu_torch(x, bias)
```

**取捨**：

| | `torch.compile` | 手寫 CUDA Extension |
|---|---|---|
| 開發成本 | 幾乎零 | 高（.cu + .cpp + setup.py + 驗證） |
| 效果 | 80-90% 最優 | 可達最優甚至超越 |
| 可控性 | 低（黑盒） | 完全控制 memory layout / register |
| 適用場景 | 大多數 elementwise fusion | 特殊 memory pattern / latency-critical |
| 除錯難度 | 高（Inductor IR 不易讀） | 低（直接看 .cu） |

---

## 何時該放棄 `torch.compile`，自己下場

四種情境手寫 extension 明確勝出：

**1. Inductor 生成的 kernel 不夠好**

用 Ch 25 的 `nsight-systems` 或 `torch.profiler` 看 kernel 時間。如果 Inductor 生成的 Triton kernel 比手寫 CUDA 慢超過 20%，且 bottleneck 確認是 kernel 本身（不是 launch overhead），就手寫。

**2. 需要 warp shuffle / 特殊 memory pattern**

Triton 有 `tl.reduce` 可以做 warp-level reduction，但 `__shfl_xor_sync` 這類 warp shuffle primitive 的控制精度更高。Cross-warp 通訊、warp-level scan（prefix sum）等場景，手寫 CUDA 更直接。

**3. 需要控制 shared memory layout**

LayerNorm、Softmax 這類 reduction kernel 的關鍵是 shared memory bank conflict 最小化。Inductor 的 Triton codegen 有時無法精準控制 shared memory 佈局（padding 策略），手寫可以針對特定 hidden size 做精確優化。

**4. Latency-critical path**

推論場景下，如果某個 op 出現在 auto-regressive decode 的 critical path 上，每個 μs 都算。手寫 extension 可以把 kernel launch overhead、stream 管理、memory 分配都壓到最低，`torch.compile` 的圖編譯路徑反而會引入額外 overhead。

---

## 踩雷清單

**1. ABI 相容性：PyTorch 版本鎖死**

cpp_extension 編譯出的 `.so` 綁定到特定 PyTorch 的 C++ ABI。換 PyTorch 版本（e.g., 2.0 → 2.1）**必須重新編譯 extension**，否則 import 時 crash（symbol not found 或 segfault）。在 Colab 每次重啟都要確認版本一致。

**2. CUDA Compute Capability 不對**

`-arch=sm_75` 編出來的 kernel 不能在 V100（sm_70）上跑，反之亦然。要麼明確指定 arch，要麼用 `-gencode arch=compute_70,code=sm_70 -gencode arch=compute_75,code=sm_75` 同時支援多個架構（編譯變慢，但 `.so` 可移植）。

**3. Python GIL 與多執行緒**

從多個 Python thread 同時呼叫 CUDA extension 是安全的（CUDA stream 是 per-thread），但如果你的 C++ 程式碼修改了共享狀態（e.g., global cache），就要加鎖或改用 thread-local storage。

**4. Non-contiguous tensor 的 data_ptr 陷阱**

`tensor.T`（轉置）在 PyTorch 是 view，stride 改了但資料沒動。如果直接把 `.T` 的 `data_ptr` 傳進 kernel 並用行優先假設存取，結果是亂的。解法：進 kernel 前永遠先 `.contiguous()`，或改用 `packed_accessor32` 自動處理 stride。

**5. Stream 同步的隱性假設**

CUDA kernel 是非同步的。kernel launch 返回後，kernel 可能還沒跑完。如果你在 C++ 端 launch 後立刻做 CPU 端的 `tensor.item()`（或 `memcpy`），PyTorch 會隱性插入同步點，但自己管 raw CUDA stream 時就要明確 `cudaStreamSynchronize`。開發期加 `C10_CUDA_KERNEL_LAUNCH_CHECK()` 能抓到 launch error。

---

## 動手練習

在 Colab T4 試跑以下步驟：

1. 建立三個檔案：`fused_bias_gelu_kernel.cu`、`fused_bias_gelu.cpp`、`setup.py`（內容照本章範例）。

2. 用 JIT load 編譯（不需要 `python setup.py`）：
   ```python
   from torch.utils.cpp_extension import load
   fused_ext = load(
       name='fused_bias_gelu_ext',
       sources=['fused_bias_gelu.cpp', 'fused_bias_gelu_kernel.cu'],
       extra_cuda_cflags=['-O2', '--use_fast_math'],
       verbose=True,
   )
   ```

3. 跑正確性測試（`torch.testing.assert_close`，atol=1e-4）。

4. 用 `torch.utils.benchmark.Timer` 量 native vs fused 的速度差，換不同 batch size（128、512、2048）觀察 speedup 趨勢。

5. 嘗試把 `AT_DISPATCH_FLOATING_TYPES` 改成 `AT_DISPATCH_FLOATING_TYPES_AND_HALF`，並用 `x.half()` 測試 float16 路徑。比較 float32 和 float16 的最大誤差。

6. 故意不加 `.contiguous()`，用 `x.T` 當 input 傳進 kernel，觀察結果是否錯誤，印出 max error。

---

## 本章重點

- PyTorch 的呼叫路徑是 Python → ATen dispatcher → CUDA kernel；custom extension 插在最後一層。
- Kernel fusion 的價值：消滅 N 倍 launch overhead（每次 ~5μs）和 HBM 往返，合成一次 read-compute-write。
- 完整 extension 需要三個檔案：`.cu`（kernel）、`.cpp`（pybind binding）、`setup.py` 或 JIT load。
- `AT_DISPATCH_FLOATING_TYPES` 處理 float32/64；加 float16 用 `_AND_HALF`；加 bfloat16 用 `_AND2(kBFloat16, kHalf, ...)`。
- `data_ptr<>` 配合 contiguous tensor 最快；`packed_accessor32` 處理 non-contiguous 更安全。
- `torch.compile` / TorchInductor 能給 80-90% 效果，零開發成本；需要 warp-level 控制或 latency 極致時才手寫。
- 踩雷首要：ABI 版本鎖、arch mismatch、non-contiguous 假設、stream 同步。

---

## 自我檢核

1. 解釋為什麼 `F.gelu(x + bias)` 涉及兩次 HBM 讀寫，fused kernel 只需一次。
2. `AT_DISPATCH_FLOATING_TYPES` 和 `AT_DISPATCH_FLOATING_TYPES_AND2(kBFloat16, kHalf, ...)` 的使用場景差在哪？
3. 什麼情況下 `packed_accessor32` 比 `data_ptr` 更安全？
4. `torch.compile` 自動 fusion 在哪三種情境下仍不如手寫 CUDA extension？
5. 如何在 JIT load 時確認 kernel 的 CUDA arch 和 GPU 相容？

---

## 延伸閱讀

- [PyTorch Custom C++ and CUDA Extensions](https://pytorch.org/tutorials/advanced/cpp_extension.html) — 官方 tutorial，涵蓋 AOT/JIT/autograd binding
- [ATen/ATen.h 原始碼](https://github.com/pytorch/pytorch/blob/main/aten/src/ATen/ATen.h) — dispatcher 機制的 C++ 入口，看 `DispatchKey` 和 `TypeDefault`
- [TorchInductor 設計文件](https://dev-discuss.pytorch.org/t/torchinductor-a-pytorch-native-compiler-with-define-by-run-ir/747) — Inductor 如何把 FX graph fusion 成 Triton kernel
- [CUTLASS](https://github.com/NVIDIA/cutlass) — NVIDIA 官方 production-grade kernel template library，比裸 CUDA 更結構化的高效能 kernel 寫法

---

→ [練習 F：手寫 fused kernel](./practice-f-fused-kernel.md)
