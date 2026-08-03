# Ch 37 — Triton：用 Python 寫 Fused GPU Kernel

> **目標**：理解 Triton 為什麼紅（`torch.compile` 後端、低門檻 fused kernel）；掌握 block-level 程式設計模型（`tl.program_id`、`tl.load`、`tl.store`、`tl.arange`、`tl.mask`）；能讀懂和寫出 vector add 和 fused softmax 兩個範例；理解 Triton 透過 MLIR 編到 PTX 的路徑；知道 Triton 和手寫 CUDA / CUTLASS 的取捨點。

> **環境**：CUDA 12.x, Colab T4 (sm_75)；Triton 2.x（`pip install triton`，預裝在帶 GPU 的 Colab）。程式輸出均為「Colab 預期，未在本機實測」，附 Colab 執行步驟。效能數字標「文獻/官方數字」或「理論預期，實測請驗證」。

---

## 為什麼 Triton 這麼紅？

如果你追蹤 2023–2025 年的 DL 系統圈，會發現 Triton 被提到的頻率遠超其歷史地位。原因有三：

**1. `torch.compile` 把 Triton 帶進了每個 PyTorch 使用者的工作流**

PyTorch 2.0 引入 `torch.compile`，預設 backend 是 `inductor`。Inductor 的 GPU kernel 生成目標是 Triton——它會把你的 PyTorch 模型自動生成對應的 Triton kernel，融合操作，然後讓 Triton 編到 PTX。你不用自己寫 Triton，但你的模型在 GPU 上跑的是 Triton 生成的程式碼。

**2. 寫 fused kernel 的門檻比 CUDA 低很多**

在 CUDA 裡寫一個 fused softmax（不產生中間 tensor）要處理：shared memory layout、warp-level reduction、bank conflict、多 pass 協調。這些細節和業務邏輯混在一起，Debug 很痛苦。Triton 的 block-level 模型把這些大部分藏起來——你只描述「每個 block 做什麼」，coalescing、shared memory 使用、warp 排程由 Triton 處理。

**3. 效能接近手寫 CUDA（對許多 workload）**

Triton 的目標不是「寫起來方便但效能差」，而是「在語意上更高層，同時讓編譯器做底層調優」。對 memory-bound kernel（softmax、layer norm、elementwise fusion），Triton 生成的程式碼效能和精調 CUDA 相當甚至更好（文獻/官方數字：Triton 論文報告在多個 benchmark 上超過 cuBLAS 的 baseline 實作，但 CUTLASS 的精調版本仍然更快）。

---

## 心智模型：Thread → Warp → Block → Grid vs Triton 的 Block-Level 模型

CUDA 的程式設計模型是 **thread-centric**（以 thread 為單位）：

```
你寫：一個 thread 做什麼（threadIdx.x 是誰、讀哪個元素）
CUDA 提供：warp 管理、SM 排程、memory 系統
你要自己管理：shared memory 佈局、bank conflict、warp synchronization
```

Triton 的程式設計模型是 **block-centric**（以 block 為單位）：

```
你寫：一個 block 做什麼（block 讀哪一段資料、怎麼計算）
Triton 負責：把 block 內的操作映射到 warp/thread、coalescing、shared memory 使用
你不需要管：threadIdx、warpSize、__syncthreads、bank conflict
```

更具體：在 Triton 裡，你的基本操作單位是 **tensor 切片（slice）**，不是單個 scalar。你說「這個 block 讀 BLOCK_SIZE 個元素的向量」，Triton 把這個向量操作翻譯成正確的 thread 安排。

---

## 核心 API：四個最重要的 `tl.*` 函式

### `tl.program_id(axis)` — 我是第幾個 block？

```python
import triton
import triton.language as tl

@triton.jit
def my_kernel(x_ptr, output_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    # 每個 kernel 實例（Triton 叫 "program"）有一個 ID
    pid = tl.program_id(axis=0)   # axis=0 對應第一個 grid 維度
```

`tl.program_id(0)` 等效於 CUDA 的 `blockIdx.x`，但概念上更乾淨——一個 Triton program 就是一個工作單元，不用管它內部由幾個 thread 組成。

### `tl.arange(start, end)` — 建立區間索引

```python
    # 建立這個 block 要處理的元素索引
    # pid=0 處理 [0, BLOCK_SIZE)，pid=1 處理 [BLOCK_SIZE, 2*BLOCK_SIZE)，...
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    # offsets 是一個長度 BLOCK_SIZE 的整數向量，不是 Python 的 range
    # 它在 GPU 上由多個 thread 並行持有
```

`tl.arange(0, BLOCK_SIZE)` 產生 `[0, 1, 2, ..., BLOCK_SIZE-1]` 的整數向量。重要：`end` 必須是 compile-time 常數（`BLOCK_SIZE: tl.constexpr` 的原因）。

### `tl.load(ptr, mask, other)` — 帶 mask 的向量 load

```python
    # mask：防止越界存取
    mask = offsets < n_elements

    # 從記憶體 load 資料（pointers of a tensor）
    x = tl.load(x_ptr + offsets, mask=mask, other=0.0)
    # 等效概念：for i in range(BLOCK_SIZE): x[i] = (mask[i]) ? x_ptr[offsets[i]] : 0.0
    # 實際上：由多個 thread 並行執行，Triton 保證 coalescing
```

`mask` 對應 CUDA 裡的 boundary check（`if(i < n)` 那行）。超出邊界的 lane 被填入 `other`（不影響正確性）。

### `tl.store(ptr, value, mask)` — 帶 mask 的向量 store

```python
    output = x * 2.0   # 向量操作，和 numpy 語法一樣

    tl.store(output_ptr + offsets, output, mask=mask)
    # 只有 mask 為 True 的位置才真正寫入
```

---

## Vector Add：完整的 Hello World

```python
# Colab 執行步驟：
# 1. Runtime → Change runtime type → GPU (T4)
# 2. !pip install triton（Colab 通常預裝）
# 3. 貼到 cell 並執行

import torch
import triton
import triton.language as tl

@triton.jit
def add_kernel(x_ptr, y_ptr, output_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    pid = tl.program_id(axis=0)
    offsets = pid * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    mask = offsets < n_elements

    x = tl.load(x_ptr + offsets, mask=mask)
    y = tl.load(y_ptr + offsets, mask=mask)

    output = x + y
    tl.store(output_ptr + offsets, output, mask=mask)


def add(x: torch.Tensor, y: torch.Tensor):
    output = torch.empty_like(x)
    n_elements = output.numel()

    # Grid：有多少個 program（block）
    BLOCK_SIZE = 1024
    grid = lambda meta: (triton.cdiv(n_elements, meta['BLOCK_SIZE']),)
    # triton.cdiv 是 ceiling division：ceil(n_elements / BLOCK_SIZE)

    add_kernel[grid](x, y, output, n_elements, BLOCK_SIZE=BLOCK_SIZE)
    return output


# 驗證
torch.manual_seed(0)
x = torch.rand(1_000_000, device='cuda')
y = torch.rand(1_000_000, device='cuda')
output_triton = add(x, y)
output_torch  = x + y
print(f"Max diff: {(output_triton - output_torch).abs().max().item()}")
# 預期輸出：Max diff: 0.0（或接近 0 的 float 精度誤差）
# （Colab 預期，未在本機實測）
```

注意幾個 Triton 特有的寫法：

1. **`@triton.jit`**：裝飾器，告訴 Triton 這個函式是 GPU kernel，要做 JIT 編譯
2. **`BLOCK_SIZE: tl.constexpr`**：compile-time 常數，必須在 kernel launch 時傳入；Triton 為每個不同的 BLOCK_SIZE 生成不同的 kernel（類似 CUDA 的 template parameter）
3. **`add_kernel[grid]`**：用 `[]` 傳入 grid，不是 `<<<>>>`；grid 是一個 callable（lambda）或 tuple
4. **`meta['BLOCK_SIZE']`**：在 grid lambda 裡，`meta` 是 `constexpr` 引數的字典，讓你根據 kernel 的 constexpr 引數計算 grid 大小

---

## Fused Softmax：展示 Triton 的核心價值

Softmax 是 Triton 展示 fusion 能力的經典例子。標準 PyTorch softmax 至少需要兩次 kernel：
1. 找 max（reduction kernel）
2. 計算 `exp(x - max)` 並 sum，再除

兩次 kernel 之間要把中間結果寫到 global memory 再讀回來，memory-bound。Triton 可以把這兩個 pass 合進一個 kernel（每個 block 負責一行），因為每行的 max 可以在 shared memory（Triton 自動管理）裡完成。

```python
@triton.jit
def softmax_kernel(
    output_ptr, input_ptr,
    input_row_stride, output_row_stride,
    n_cols,
    BLOCK_SIZE: tl.constexpr
):
    # 每個 program 負責一行（row）
    row_idx = tl.program_id(0)

    # 計算這一行的起始指標
    row_start_ptr = input_ptr + row_idx * input_row_stride

    # 建立列索引
    col_offsets = tl.arange(0, BLOCK_SIZE)
    mask = col_offsets < n_cols

    # 1. Load 整行資料
    row = tl.load(row_start_ptr + col_offsets, mask=mask, other=-float('inf'))

    # 2. 減去 max（數值穩定性：避免 exp 溢位）
    row_max = tl.max(row, axis=0)     # reduction，回傳純量
    row = row - row_max

    # 3. exp
    numerator = tl.exp(row)

    # 4. sum
    denominator = tl.sum(numerator, axis=0)  # reduction，回傳純量

    # 5. 正規化
    softmax_output = numerator / denominator

    # 6. Store
    output_row_start_ptr = output_ptr + row_idx * output_row_stride
    tl.store(output_row_start_ptr + col_offsets, softmax_output, mask=mask)


def softmax(x: torch.Tensor):
    n_rows, n_cols = x.shape

    # BLOCK_SIZE 要能放下整行（必須是 2 的冪次）
    BLOCK_SIZE = triton.next_power_of_2(n_cols)

    # 為了讓 shared memory 放得下，限制最大 BLOCK_SIZE
    # T4 每個 SM 48 KB shared memory，float32 下約 12K 個元素
    num_warps = 4
    if BLOCK_SIZE >= 2048: num_warps = 8
    if BLOCK_SIZE >= 4096: num_warps = 16

    output = torch.empty_like(x)

    softmax_kernel[(n_rows,)](   # grid = (n_rows,)，每個 program 處理一行
        output, x,
        x.stride(0), output.stride(0),   # stride 是「下一行的起始 offset」
        n_cols,
        num_warps=num_warps,             # 提示 Triton 每個 block 用幾個 warp
        BLOCK_SIZE=BLOCK_SIZE,
    )
    return output


# 驗證
torch.manual_seed(0)
x = torch.randn(1823, 781, device='cuda')  # 非整數倍，測試 mask 的效果
y_triton = softmax(x)
y_torch  = torch.softmax(x, axis=1)
assert torch.allclose(y_triton, y_torch, rtol=1e-3), "Softmax 不一致！"
print("Softmax 驗證通過")
# （Colab 預期，未在本機實測）
```

這個 fused softmax 的優點：
- 整行的計算（max + exp + sum + normalize）在一個 kernel 裡完成
- 行內的資料只讀一次（Triton 把它放在寄存器和 shared memory，不回寫 global memory）
- Triton 自動安排 `tl.max`、`tl.sum` 的 reduction（warp shuffle + shared memory），你不需要手寫

---

## Triton 的編譯路徑：Python → MLIR → PTX

```
Python 的 @triton.jit 函式
  │
  ▼ Triton 前端（AST 解析）
Triton IR（Triton 自定義 IR，描述 block-level 操作）
  │
  ▼ Triton 最佳化 pass
  - 向量化（block 操作 → warp/thread 分配）
  - 共享記憶體分配（tl.max / tl.sum 的中間結果）
  - 指令排程
  │
  ▼ MLIR（Multi-Level Intermediate Representation）
  - GPU dialect → nvvm dialect（針對 NVIDIA）
  │
  ▼ LLVM IR
  │
  ▼ PTX
  │
  ▼ SASS（由 ptxas 編譯）
```

這條路徑意味著：

1. **Triton 生成的 PTX 可以用 `triton.compile` 的 `ptx` 選項查看**
2. **Triton 本身不依賴 nvcc**，它直接用 LLVM 後端
3. **可攜性**：AMD 後端是把 Triton IR 翻譯成 AMD AMDGPU LLVM IR，走 ROCm 路徑；原則上也可以做其他 backend

---

## 自動調優：`@triton.autotune`

Triton 的 BLOCK_SIZE 和 num_warps 選擇是效能關鍵。Triton 提供 autotune 裝飾器：

```python
@triton.autotune(
    configs=[
        triton.Config({'BLOCK_SIZE': 128}, num_warps=4),
        triton.Config({'BLOCK_SIZE': 256}, num_warps=4),
        triton.Config({'BLOCK_SIZE': 512}, num_warps=8),
        triton.Config({'BLOCK_SIZE': 1024}, num_warps=8),
    ],
    key=['n_elements'],   # 當 n_elements 改變時重新 tune
)
@triton.jit
def add_kernel(x_ptr, y_ptr, output_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
    ...   # 和前面相同
```

第一次呼叫時，Triton 會嘗試所有 config，記住最快的那個（暫存到 `~/.triton/cache/`）。後續呼叫直接用最優 config。

這是 Triton 的關鍵優勢之一：把 autotuning 內建，不需要像手寫 CUDA 那樣要麼靠人工分析、要麼寫複雜的 benchmark 框架。

---

## Triton vs 手寫 CUDA vs CUTLASS

| | Triton | 手寫 CUDA | CUTLASS |
|--|--|--|--|
| 門檻 | 低（Python，block-level）| 高（C++，thread-level）| 極高（C++ template 地獄）|
| Elementwise / fusion | ✓ 接近最優 | ✓ 最優 | 不適合（設計針對 GEMM）|
| GEMM / Tensor Core | ✓ 接近（Triton GEMM 教程）| ✓ 可以做，但難 | ✓ 最優（CUTLASS 的設計目標）|
| Custom memory layout（如 swizzle）| 有限 | ✓ 完全控制 | ✓ 完全控制 |
| Autotuning 內建 | ✓ `@triton.autotune` | 需手寫 | 提供 profiler |
| 可視性（PTX / SASS）| 可查看 | 直接控制 | 可查看 |
| 適用場景 | fused elementwise、softmax、attention、自訂激活、快速原型 | 需要精確控制 layout、warp-level 操作的極限優化 | GEMM / Conv 極限效能 |
| 生產可用性 | 高（PyTorch inductor 已在用）| 高 | 高（cuBLAS 部分 kernel 基於 CUTLASS）|

**什麼時候 Triton 比 CUDA 更好選擇**：

你需要寫一個 fused kernel（把 GELU + LayerNorm + bias add 合成一個 kernel），在 CUDA 裡這需要手動管理 shared memory、寫 warp reduce、測 bank conflict。在 Triton 裡，你只需要描述「讀向量、做計算、寫向量」，reduction 自動處理。開發速度快 3–5 倍（主觀估計），效能差異通常在 5% 以內（理論預期）。

**什麼時候必須手寫 CUDA 或用 CUTLASS**：

你需要極致 GEMM 效能（flash attention 的 MMA 指令控制、swizzle 記憶體佈局）。Triton 的 block-level 抽象在這裡開始漏出底層（雖然 Triton 2.x 加了 `tl.dot` 走 Tensor Core，但精調水準仍低於 CUTLASS）。[Ch 38 GEMM 深挖](./38-gemm-deep-dive.md) 的內容就是手寫 CUDA 才能做到的。

---

## 踩雷清單

**錯誤直覺 1：Triton 的 `tl.arange` 可以用 runtime 值作為範圍。**
正確：`tl.arange(start, end)` 的 `start` 和 `end` 都必須是 **compile-time 常數**。這意味著 `BLOCK_SIZE` 必須是 `tl.constexpr`，不能用 `n_elements` 直接作為 `end`（因為 n_elements 是 runtime 值）。如果 n_elements 不是 BLOCK_SIZE 的倍數，用 `mask` 處理邊界，不是縮小 `tl.arange`。

**錯誤直覺 2：Triton kernel 裡可以用 Python 的 for loop 來遍歷資料。**
正確：`@triton.jit` 函式裡的 for loop 是被 Triton 靜態展開（unrolled）的，不是動態的 Python loop。Triton 不支援 kernel 裡的動態長度 loop（基於 runtime 值的 range）。動態長度的 loop 要用 `tl.while_loop` 或把 loop 展開到 grid 維度（讓更多 program 分擔）。

**錯誤直覺 3：Triton 自動管理所有的 shared memory，不用擔心記憶體大小。**
正確：Triton 確實自動分配 shared memory，但你的 BLOCK_SIZE 不能無限大。每個 SM 的 shared memory 有限（T4 是 48 KB），BLOCK_SIZE × sizeof(dtype) 不能超過。Triton 在 JIT 時如果 shared memory 不夠會報錯，但錯誤訊息不總是清楚。實際上，T4 的 BLOCK_SIZE 上限約 4096 個 float32（16 KB，留餘量給 reduction 中間結果）。

**錯誤直覺 4：`torch.compile` 用 Triton 生成的 kernel 可以直接用 Nsight Compute profile。**
正確：Triton 生成的 kernel 的函式名是自動生成的（如 `triton__0d1d2d3d4d...`），Nsight Compute 能 profile 它，但要把 kernel 名對應到你的 Triton 程式碼需要一點工夫。`torch._inductor.config.debug = True` 或 `TORCH_LOGS="+inductor"` 可以印出生成的 Triton 程式碼，幫助你對應。

**錯誤直覺 5：Triton 在 AMD GPU 上和 NVIDIA GPU 上效能一樣好。**
正確：Triton 的 NVIDIA 後端最成熟，AMD（ROCm）後端在 Triton 2.x 是 experimental 狀態。部分 Triton 特性（如 `tl.dot` 的 Tensor Core 利用）在 AMD GPU 上的映射可能不完整，效能可能顯著落後。在 AMD 上用 Triton 要測實際效能，不能假設和 NVIDIA 相同。

---

## 進階：`tl.dot` 走 Tensor Core

Triton 也可以寫矩陣乘法，用 `tl.dot`（回連 [Ch 30 Tensor Core](./30-tensor-core.md)）：

```python
@triton.jit
def matmul_kernel(
    a_ptr, b_ptr, c_ptr,
    M, N, K,
    stride_am, stride_ak,
    stride_bk, stride_bn,
    stride_cm, stride_cn,
    BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr,
):
    pid_m = tl.program_id(0)   # 負責 C 的哪個 tile 的 row block
    pid_n = tl.program_id(1)   # 負責 C 的哪個 tile 的 col block

    # 建立 A 和 B 的 tile 指標
    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    offs_k = tl.arange(0, BLOCK_K)

    a_ptrs = a_ptr + (offs_m[:, None] * stride_am + offs_k[None, :] * stride_ak)
    b_ptrs = b_ptr + (offs_k[:, None] * stride_bk + offs_n[None, :] * stride_bn)

    accumulator = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)

    for k in range(0, K, BLOCK_K):   # 靜態 step 的 loop 可以用
        a = tl.load(a_ptrs)
        b = tl.load(b_ptrs)
        accumulator += tl.dot(a, b)   # tl.dot 在 sm_75+ 走 Tensor Core
        a_ptrs += BLOCK_K * stride_ak
        b_ptrs += BLOCK_K * stride_bk

    offs_cm = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_cn = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    c_ptrs = c_ptr + (offs_cm[:, None] * stride_cm + offs_cn[None, :] * stride_cn)
    mask = (offs_cm[:, None] < M) & (offs_cn[None, :] < N)
    tl.store(c_ptrs, accumulator, mask=mask)
```

這個 kernel 的效能在 T4 上大約能達到 cuBLAS 的 80–90%（文獻/官方數字，視 M/N/K 而定）。`tl.dot` 在 sm_75+ 的硬體上自動走 Tensor Core 路徑（和 `cublasGemmEx` 一樣的硬體，但 Triton 的 tiling 策略不如 CUTLASS 精細）。

更完整的 Triton GEMM 教程在官方文件的 tutorials 裡，有 softmax、GEMM、attention 三個完整範例，包含 autotuning 設定。

---

## 動手練習

**Colab 執行步驟：**
1. Runtime → Change runtime type → GPU (T4)
2. `!pip install triton`（或確認已安裝：`import triton; print(triton.__version__)`）
3. Triton 2.x 在 Python 3.10+ 和 CUDA 12.x 下穩定

練習 A：跑本章的 vector add 範例，驗證輸出正確。然後改寫成 vector multiply（`z[i] = x[i] * y[i]`）和 fused multiply-add（`z[i] = x[i] * y[i] + c`，c 是純量），觀察修改的程式碼量。

練習 B：用 `@triton.autotune` 對 vector add 做自動調優，測試 BLOCK_SIZE = [128, 256, 512, 1024] 四種設定。用 `triton.testing.Benchmark` 量測各自的 bandwidth（GB/s）。

練習 C（進階）：用 `torch.compile` 對一個簡單的 `x.softmax(-1)` 呼叫 compile，設 `TORCH_LOGS="+inductor"` 查看自動生成的 Triton kernel 程式碼，和本章手寫的 softmax kernel 對比結構差異。

---

## 本章重點

- Triton 是 block-level 的 GPU 程式設計語言，用 Python 寫，底層走 MLIR → PTX
- 核心 API：`tl.program_id`（block 的 ID）、`tl.arange`（區間索引，需 constexpr 範圍）、`tl.load`（帶 mask 的向量 load）、`tl.store`（帶 mask 的向量 store）
- `BLOCK_SIZE: tl.constexpr` 必須在 launch 時傳入，Triton 為每個值分別 JIT 編譯
- 核心優勢：不需要手管 shared memory、warp reduction、bank conflict，編譯器處理
- `@triton.autotune` 內建自動調優，找最佳 BLOCK_SIZE 和 num_warps
- `torch.compile` 的 inductor backend 用 Triton 生成 kernel
- Elementwise / fused softmax 效能接近手寫 CUDA；極致 GEMM 仍輸 CUTLASS

## 自我檢核（主動回憶）

1. Triton 的 `tl.program_id(0)` 對應 CUDA 的哪個概念？
2. 為什麼 `tl.arange` 的範圍必須是 compile-time 常數？
3. Triton 的 softmax kernel 如何把「找 max」和「計算 exp/sum」做成一個 kernel？中間結果存在哪裡？
4. `@triton.autotune` 的 `key` 引數有什麼作用？
5. 什麼類型的 kernel 選 Triton 比手寫 CUDA 更好？什麼時候反過來？

## 延伸閱讀

1. **Triton 官方 Tutorials** — [triton-lang.org/main/getting-started/tutorials](https://triton-lang.org/main/getting-started/tutorials/index.html)：vector addition、fused softmax、matrix multiplication、low-memory dropout 四個完整範例，每個都有詳細的逐行解說和 performance benchmark
2. **Triton 論文：Tillet et al., MAPL 2019** — 「Triton: An Intermediate Language and Compiler for Tiled Neural Network Computations」：設計思路的原始描述，解釋 block-level 模型為什麼讓自動最佳化更容易
3. **PyTorch Inductor + Triton** — [pytorch.org/docs/stable/torch.compiler_inductor_backend](https://pytorch.org/docs/stable/torch.compiler_inductor_backend.html)：torch.compile 如何生成 Triton kernel，TORCH_LOGS 的使用，inductor 的 debug 工具
4. **Triton GitHub** — [github.com/triton-lang/triton](https://github.com/triton-lang/triton)：原始碼、issue 追蹤、`python/tutorials/` 下的完整 benchmark 程式碼（包含和 cuBLAS、手寫 CUDA 的效能對比圖）
5. **「Understanding GPU Programming with Triton」** — [pgupta.info/blog/2025/12/triton](https://www.pgupta.info/blog/2025/12/triton)：從硬體到程式碼的整合說明，補充了 MLIR 編譯路徑的更多細節

---

Triton 讓你用更少的程式碼寫出高效的 elementwise 和 reduction kernel，但 GEMM 的極限仍在 CUTLASS 和手寫 CUDA 的領域。

→ [Ch 38 GEMM 深挖](./38-gemm-deep-dive.md)
