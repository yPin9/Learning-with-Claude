# 練習 B — 手算 Occupancy：讀 GPU 規格、找限制因子、提調整建議

> **目標**：不靠 CUDA Occupancy Calculator 或 Nsight，純粹用規格表和公式，手算四個典型情境的 occupancy，找出限制因子，並提出有根據的調整建議。
>
> **環境**：T4（sm_75）；本練習全程紙筆或心算，最後用速算表自我驗算。

---

## 背景動機

「只要跑 Nsight 看數字就好了吧？」

這個想法在除錯階段是對的，但在設計 kernel 的早期（還沒有程式碼可以跑）或在做架構決策（要用多少 shared memory？register 預算要怎麼分？）時，工具給不了答案——因為你連 kernel 都還沒寫。

手算 occupancy 讓我們能在寫程式之前預測瓶頸，做出有依據的設計決策，而不是寫完 → 跑 profiler → 發現問題 → 重構 → 重複這個浪費時間的循環。

更重要的是：profiler 告訴你「現在的 occupancy 是 X%」，但不告訴你「為什麼」以及「改哪裡最有效」。這個練習補的就是這個理解。

---

## 快速回顧公式

### T4（sm_75）每 SM 規格

| 資源 | T4 (sm_75) 每 SM |
|------|-----------------|
| 最大 warp 數 | 32 |
| 最大 thread 數 | 1024 |
| 最大 block 數 | 16 |
| Register file | 65,536 個 32-bit register |
| Shared memory（預設） | 32 KB |
| Shared memory（最大） | 64 KB（需設定 carveout） |
| Warp 大小 | 32 thread |

### Occupancy 計算流程

```
occupancy = active_warps_per_SM / max_warps_per_SM
          = active_warps / 32
```

三個限制因子，各自算出能容納的 active block 數，取最小值：

1. **Register bound**：
   ```
   regs_per_block = blockSize × regs_per_thread
   # 注意：需向上對齊到 256 的倍數（warp-level granularity）
   active_blocks_reg = ⌊65536 / regs_per_block_aligned⌋，上限 16
   ```

2. **Shared memory bound**（smem/block > 0 時才有意義）：
   ```
   active_blocks_smem = ⌊smem_per_SM / smem_per_block⌋，上限 16
   ```

3. **Block count bound**：
   ```
   active_blocks_max = 16
   ```

最終：
```
active_blocks = min(active_blocks_reg, active_blocks_smem, active_blocks_max)
warps_per_block = blockSize / 32
active_warps = active_blocks × warps_per_block
# 注意：active_warps 上限也是 32
occupancy = min(active_warps, 32) / 32
```

---

## 主要任務說明

下面有四個算例，難度遞增：

- **算例 A**：只有 register 是瓶頸，流程最乾淨，確認你的公式是對的
- **算例 B**：shared memory 才是瓶頸，register 不夠重要
- **算例 C**：block count 是瓶頸，看起來違反直覺
- **算例 D**：兩個資源同時逼近上限，需要比較哪個更緊

每個算例：先自己算，算完再展開解答核對。核對時不只看答案，要看每一步的算式是否和你的一致。

---

## 算例 A — Register Bound

### 情境：矩陣乘法 kernel

```cuda
// 矩陣乘法 kernel（簡化版）
__global__ void matmul(float *A, float *B, float *C, int N) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    float sum = 0.0f;
    for (int k = 0; k < N; k++) {
        sum += A[row * N + k] * B[k * N + col];
    }
    C[row * N + col] = sum;
}
```

- **blockDim** = (32, 8, 1) → 256 thread/block → 8 warp/block
- **regs_per_thread** = 48（用 `nvcc --ptxas-options=-v` 得到）
- **smem/block** = 0 KB（這個版本不用 shared memory）

### 任務

1. 在 register bound 下，每個 SM 最多能同時容納幾個 block？
2. 最大 active warp 數是多少？
3. Occupancy 是多少？（百分比）
4. 限制因子是哪個？（register / smem / block count）
5. 如果把 register 降到 32 個（加上 `--maxrregcount=32`），occupancy 變成多少？這樣做有什麼潛在代價？

### 「卡住了」提示

如果第 1 題算不出來，先算這個：
- 每個 block 用 register 數 = blockSize × regs_per_thread = 256 × 48 = 12,288
- 確認對齊：12,288 ÷ 256 = 48，整除，不需要向上取整，所以 regs_per_block_aligned = 12,288
- 每 SM register budget = 65,536
- 能放幾個 block = ⌊65,536 / 12,288⌋ = ?（這個除法計算出來是一個介於 5 和 6 之間的數）

如果第 5 題不確定「代價」是什麼，想想：register 少了，compiler 得把一部分暫時變數 spill 到哪裡？

---

## 算例 B — Shared Memory Bound

### 情境：2D stencil kernel（five-point stencil）

```cuda
// 2D stencil with halo
__global__ void stencil2d(float *in, float *out, int W, int H) {
    // tile size: 32×8，加上 halo 後 34×10
    __shared__ float tile[10][34];  // (8+2) × (32+2) × 4 bytes = 1360 bytes
    // ... 載入 halo，計算 stencil ...
}
```

- **blockDim** = (32, 8, 1) → 256 thread/block → 8 warp/block
- **regs_per_thread** = 24
- **smem/block** = 2 KB（取整，實際 1360 bytes，為簡化計算取 2048 bytes）
- **shared memory carveout**：預設 32 KB/SM

### 任務

1. Register bound：能放幾個 block？
2. Shared memory bound：能放幾個 block？
3. Block count bound：幾個 block？
4. 最終限制因子是哪個？（三個裡取最小）
5. Occupancy 是多少？
6. 如果把 carveout 設為 64 KB（`cudaFuncSetAttribute(..., cudaFuncAttributePreferredSharedMemoryCarveout, 100)`），能提升 occupancy 嗎？算算看。

### 「卡住了」提示

- Register bound：65,536 ÷ (256 × 24) = 65,536 ÷ 6,144 = ?（注意 6,144 能整除 256 嗎？）
- Shared memory bound：32,768 bytes ÷ 2,048 bytes/block = ?
- 第 6 題：carveout 變 64 KB 之後，smem bound = 65,536 ÷ 2,048 = ?；但要注意 block count 上限

---

## 算例 C — Block Count Bound

### 情境：長序列處理 kernel，刻意把 block 做小

```cuda
// 每個 block 處理 64 個元素，block 做小是為了讓 grid 足夠大
__global__ void process_seq(float *data, int N) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < N) {
        // 簡單運算，register 用量少
        data[idx] = data[idx] * 2.0f + 1.0f;
    }
}
```

- **blockDim** = (64, 1, 1) → 64 thread/block → 2 warp/block
- **regs_per_thread** = 16
- **smem/block** = 0 KB

### 任務

1. Register bound：能放幾個 block？（先算出來，你可能會嚇一跳）
2. Block count bound：最多幾個 block？
3. 最終限制因子是哪個？
4. Occupancy 是多少？
5. 要怎麼調整才能提升 occupancy？（提示：有兩個方向）

### 「卡住了」提示

- Register bound = ⌊65,536 / (64 × 16)⌋ = ⌊65,536 / 1,024⌋ = 64 blocks
- 但 block count 上限是 16
- 所以最終 active blocks = min(64, 16) = 16
- active warps = 16 × 2 = 32 warp
- occupancy = 32 / 32 = 100%？

等等，算到 100% 了，那為什麼題目說「要怎麼調整才能提升」？

重新想一下：100% occupancy 在這個情境下代表什麼？16 個 block，每個 block 只有 2 warp——這樣的 latency hiding 能力足夠嗎？有沒有更好的設計？

（提示：occupancy 高不代表效能一定好，這個案例的問題可能不是 occupancy。）

---

## 算例 D — 複合 Bound（register + shared memory 都逼近上限）

### 情境：向量化 kernel，需要同時調優 register 和 shared memory

```cuda
__global__ void vector_kernel(float4 *data, int N) {
    __shared__ float smem[128 * 8];  // 128 thread × 8 float = 4096 bytes ≈ 4 KB
    // 實際情境中 smem 用量更大，這裡設為 8 KB
    // ... 複雜運算，register 較多 ...
}
```

- **blockDim** = (128, 1, 1) → 128 thread/block → 4 warp/block
- **regs_per_thread** = 40
- **smem/block** = 8 KB（8,192 bytes）
- **carveout**：預設 32 KB/SM

### 任務

1. 分別計算三個限制因子下的 active block 數
2. 最終限制因子是哪個？（或是幾個同時）
3. Occupancy 是多少？
4. 有三個調整方向：
   - 方向一：把 blockSize 從 128 降到 64（register 和 smem 用量不變）
   - 方向二：把 regs_per_thread 從 40 降到 32（加 `--maxrregcount=32`）
   - 方向三：把 smem/block 從 8 KB 降到 4 KB（優化演算法）

   各自算出調整後的 occupancy，哪個最有效？

### 「卡住了」提示

算例 D 的關鍵是「複合 bound」——不只一個資源是瓶頸：

- Register bound：regs_per_block = 128 × 40 = 5,120；5,120 / 256 = 20，整除，aligned = 5,120；⌊65,536 / 5,120⌋ = ?
- Shared memory bound：⌊32,768 / 8,192⌋ = ?
- 兩個算出來的數字很接近（或相同）？這就是「複合 bound」的特徵

方向一的計算：blockSize = 64 → 2 warp/block，但 regs 和 smem 不變，只有 block 的 warp 數變少，active_blocks 可能變多...

---

## 延伸挑戰（較難）

你有一個生產環境的 kernel，已知：

- 實驗結果顯示 blockDim = 256 最快（比 128 或 512 都快）
- `nvcc --ptxas-options=-v` 輸出：
  ```
  ptxas info    : Used 72 registers, 12288 bytes smem, 400 bytes cmem[0]
  ```
- Carveout 用預設 32 KB

**問題 1**：在 T4 上的 occupancy 是多少？哪個是限制因子？

**問題 2**：有人建議把 carveout 設為 64 KB，讓 smem 可用量變成 12 KB → 12 KB（對，這個 kernel smem 已經用了 12 KB，但如果你要擴展演算法需要更多）。

實際情境是：你在考慮把演算法改成需要 24 KB smem/block，但這樣在 32 KB carveout 下放不下。

- 如果改成 24 KB smem/block，carveout 設 64 KB，occupancy 會變成多少？
- 這個交換值不值得？（沒有標準答案，說明你的推理）

**問題 3**：如果把 blockDim 改成 128（但 register 數 72 不變，smem 還是 12 KB），occupancy 會提升嗎？算算看。

---

## 參考解答

<details>
<summary>算例 A 解答（展開前先確認自己算完）</summary>

### 算例 A 完整解

**已知**：
- blockSize = 256，warps_per_block = 256 / 32 = 8
- regs_per_thread = 48
- smem/block = 0

**步驟 1：Register bound**

```
regs_per_block = 256 × 48 = 12,288
對齊確認：12,288 / 256 = 48，整除 → regs_per_block_aligned = 12,288

active_blocks_reg = ⌊65,536 / 12,288⌋ = ⌊5.333...⌋ = 5 blocks
（未超過 16 blocks 上限）
```

**步驟 2：Shared memory bound**

```
smem/block = 0 → 不構成限制（或視為 ∞ blocks）
active_blocks_smem = 16（退回 block count 上限）
```

**步驟 3：Block count bound**

```
active_blocks_max = 16
```

**步驟 4：取最小值**

```
active_blocks = min(5, 16, 16) = 5 blocks
```

**步驟 5：計算 occupancy**

```
active_warps = 5 × 8 = 40 warp
但 active_warps 上限是 32 → active_warps = min(40, 32) = 32
```

等等，這樣算出 100%？讓我們重新想：

問題出在「5 blocks × 8 warps = 40 warps > 32 warps」。這代表 register 允許放 5 個 block，但 warp 上限只允許 32 個 active warp，所以實際上只能放 ⌊32 / 8⌋ = 4 個 block。

**修正計算**：

```
active_warps_from_reg = 5 × 8 = 40 warp > 32 warp 上限
→ warp 上限反而才是真正的限制因子
→ active_blocks_warp_limited = ⌊32 / 8⌋ = 4 blocks
→ active_warps = 4 × 8 = 32 warp
→ occupancy = 32 / 32 = 100%
```

**答案**：occupancy = 100%，限制因子是 **warp count bound**（不是 register）。

Register 允許 5 個 block，但 5 × 8 = 40 > 32 warp，warp 上限先到，所以只能放 4 個 block，恰好達到 32 active warps。

**這樣夠好嗎？**

100% occupancy 聽起來很好。但 blockSize = 256 搭配 48 register 在 T4 上其實已經很緊，如果 register 再多一點（比如 64），active_blocks_reg 就會降到 4，但 4 × 8 = 32 warp 依然是 100%——所以在這個配置下，register 有點浪費優化空間。

**第 5 題：降到 32 register 的效果**

```
regs_per_block_aligned = 256 × 32 = 8,192（8,192 / 256 = 32，整除）
active_blocks_reg = ⌊65,536 / 8,192⌋ = 8 blocks
active_warps = 8 × 8 = 64 warp > 32 → warp 限制，只能放 4 blocks
active_warps = 4 × 8 = 32 warp → occupancy = 100%
```

occupancy 沒有變化！因為原本就已經是 warp-limited 了。

**代價**：register 少了，compiler 會把溢出的變數 spill 到 local memory（本質是 global memory，延遲幾百個 cycle）。如果 spill 量大，實際執行速度可能反而更慢。`--maxrregcount=32` 這個旗標要配合 `--ptxas-options=-v` 確認 spill 暫存器數量才能評估。

</details>

<details>
<summary>算例 B 解答（展開前先確認自己算完）</summary>

### 算例 B 完整解

**已知**：
- blockSize = 256，warps_per_block = 8
- regs_per_thread = 24
- smem/block = 2,048 bytes（2 KB）
- carveout = 32,768 bytes（32 KB）

**步驟 1：Register bound**

```
regs_per_block = 256 × 24 = 6,144
對齊確認：6,144 / 256 = 24，整除 → aligned = 6,144

active_blocks_reg = ⌊65,536 / 6,144⌋ = ⌊10.666...⌋ = 10 blocks
```

**步驟 2：Shared memory bound**

```
active_blocks_smem = ⌊32,768 / 2,048⌋ = ⌊16⌋ = 16 blocks
```

**步驟 3：Block count bound**

```
active_blocks_max = 16
```

**步驟 4：取最小值**

```
active_blocks = min(10, 16, 16) = 10 blocks
```

**步驟 5：計算 occupancy**

```
active_warps = 10 × 8 = 80 warp > 32 → warp 上限介入
active_blocks_warp_limited = ⌊32 / 8⌋ = 4 blocks
active_warps = 4 × 8 = 32 warp
occupancy = 32 / 32 = 100%
```

**答案**：occupancy = 100%，warp count 先到限制，register 和 shared memory 都不是問題。

**第 6 題：carveout 設 64 KB**

```
active_blocks_smem = ⌊65,536 / 2,048⌋ = 32 blocks（但上限 16）
→ active_blocks_smem = 16

active_blocks = min(10, 16, 16) = 10 blocks（register bound 沒變）
active_warps = 10 × 8 = 80 > 32 → 依然 warp-limited，4 blocks active
occupancy = 100%
```

把 carveout 從 32 KB 改成 64 KB，occupancy 完全沒有改變——因為 shared memory 從來就不是限制因子，register 也不是，warp count 才是。

**結論**：在這個情境下，任何資源優化都不會提升 occupancy，因為已經 100%。如果要提升效能，應該看記憶體存取模式（coalescing）和計算密度，而不是 occupancy。

</details>

<details>
<summary>算例 C 解答（展開前先確認自己算完）</summary>

### 算例 C 完整解

**已知**：
- blockSize = 64，warps_per_block = 64 / 32 = 2
- regs_per_thread = 16
- smem/block = 0

**步驟 1：Register bound**

```
regs_per_block = 64 × 16 = 1,024
對齊確認：1,024 / 256 = 4，整除 → aligned = 1,024

active_blocks_reg = ⌊65,536 / 1,024⌋ = 64 blocks
（超過 16 blocks 上限 → 截斷為 16）
active_blocks_reg_capped = 16 blocks
```

**步驟 2：Shared memory bound**

```
smem/block = 0 → 不構成限制
active_blocks_smem = 16（block count 上限）
```

**步驟 3：Block count bound**

```
active_blocks_max = 16
```

**步驟 4：取最小值**

```
active_blocks = min(16, 16, 16) = 16 blocks
```

**步驟 5：計算 occupancy**

```
active_warps = 16 × 2 = 32 warp
occupancy = 32 / 32 = 100%
```

**答案**：occupancy = 100%，限制因子是 **block count bound**（16 blocks/SM）。

**第 5 題：如何調整？**

已經 100% 了，那問題在哪裡？

問題是這個設計的 **latency hiding 能力很脆弱**：16 個 block，每個 block 只有 2 warp，如果其中一個 warp 在等記憶體，scheduler 只剩 1 個 warp 可以執行——而且這 1 個 warp 可能在同一個 block 裡，大機率也在等同樣的記憶體。

**調整方向一**：增大 blockSize

```
blockSize = 128 → 4 warp/block
active_blocks = 32 / 4 = 8 blocks（warp-limited），或 16（block-limited）
→ 取 min，warp 先到：16 × 4 = 64 > 32 → 依然 8 blocks active
active_warps = 8 × 4 = 32 warp → 100%

但每個 SM 現在只有 8 個 block（還是不多），scheduler 有更多 warp 可以切換。
```

**調整方向二**：降低 register 使用（不影響本例，因為 reg 根本不是瓶頸）

**真正的問題**：block size = 64 在這個情境下 occupancy 是 100%，但每個 SM 16 個 block 各有 2 warp，warp 切換空間很小。考慮用 blockSize = 256 讓每個 SM 的 warp 分布更集中，提升 latency hiding 效率。

</details>

<details>
<summary>算例 D 解答（展開前先確認自己算完）</summary>

### 算例 D 完整解

**已知**：
- blockSize = 128，warps_per_block = 128 / 32 = 4
- regs_per_thread = 40
- smem/block = 8,192 bytes（8 KB）
- carveout = 32,768 bytes（32 KB）

**步驟 1：Register bound**

```
regs_per_block = 128 × 40 = 5,120
對齊確認：5,120 / 256 = 20，整除 → aligned = 5,120

active_blocks_reg = ⌊65,536 / 5,120⌋ = ⌊12.8⌋ = 12 blocks
```

**步驟 2：Shared memory bound**

```
active_blocks_smem = ⌊32,768 / 8,192⌋ = ⌊4⌋ = 4 blocks
```

**步驟 3：Block count bound**

```
active_blocks_max = 16
```

**步驟 4：取最小值**

```
active_blocks = min(12, 4, 16) = 4 blocks
→ 限制因子：shared memory bound
```

**步驟 5：計算 occupancy**

```
active_warps = 4 × 4 = 16 warp
occupancy = 16 / 32 = 50%
```

**答案**：occupancy = 50%，限制因子是 **shared memory bound**。

---

**方向一：blockSize 從 128 降到 64**

```
warps_per_block = 64 / 32 = 2

Register bound：
  regs_per_block = 64 × 40 = 2,560
  active_blocks_reg = ⌊65,536 / 2,560⌋ = 25 blocks → 截斷為 16

Shared memory bound：
  smem/block 不變 = 8,192 bytes
  active_blocks_smem = ⌊32,768 / 8,192⌋ = 4 blocks

Block count bound：16

active_blocks = min(16, 4, 16) = 4 blocks
active_warps = 4 × 2 = 8 warp
occupancy = 8 / 32 = 25%
```

**方向一反而讓 occupancy 從 50% 降到 25%！** blockSize 縮小讓每個 block 的 warp 數少了一半，但 smem bound 沒有改變，還是只能放 4 個 block。

---

**方向二：regs_per_thread 從 40 降到 32**

```
warps_per_block = 4（不變）

Register bound：
  regs_per_block = 128 × 32 = 4,096
  active_blocks_reg = ⌊65,536 / 4,096⌋ = 16 blocks

Shared memory bound（不變）：4 blocks

active_blocks = min(16, 4, 16) = 4 blocks
active_warps = 4 × 4 = 16 warp
occupancy = 16 / 32 = 50%
```

**方向二無效**：smem 才是真正的瓶頸，register 減少不影響結果。

---

**方向三：smem/block 從 8 KB 降到 4 KB**

```
Shared memory bound：
  active_blocks_smem = ⌊32,768 / 4,096⌋ = 8 blocks

Register bound：12 blocks（不變）

active_blocks = min(12, 8, 16) = 8 blocks
active_warps = 8 × 4 = 32 warp
occupancy = 32 / 32 = 100%
```

**方向三最有效**：smem 減半，occupancy 從 50% 直接跳到 100%。

**結論**：這個算例的關鍵教訓是「先找對限制因子，再決定優化方向」。盲目降低 register 完全沒用；縮小 blockSize 反而有害；只有攻擊真正的瓶頸（shared memory）才有效。

</details>

<details>
<summary>延伸挑戰解答（展開前先確認自己算完）</summary>

### 延伸挑戰完整解

**已知**：
- blockSize = 256，warps_per_block = 8
- regs_per_thread = 72
- smem/block = 12,288 bytes（12 KB）
- carveout = 32 KB（預設）

---

**問題 1：原始 occupancy**

```
Register bound：
  regs_per_block = 256 × 72 = 18,432
  對齊：18,432 / 256 = 72，整除
  active_blocks_reg = ⌊65,536 / 18,432⌋ = ⌊3.555...⌋ = 3 blocks

Shared memory bound：
  active_blocks_smem = ⌊32,768 / 12,288⌋ = ⌊2.666...⌋ = 2 blocks

Block count bound：16

active_blocks = min(3, 2, 16) = 2 blocks
active_warps = 2 × 8 = 16 warp
occupancy = 16 / 32 = 50%
```

**限制因子：shared memory bound**（2 < 3）

---

**問題 2：擴展 smem 到 24 KB，carveout 設 64 KB**

```
Shared memory bound：
  active_blocks_smem = ⌊65,536 / 24,576⌋ = ⌊2.666...⌋ = 2 blocks

Register bound（不變）：3 blocks

active_blocks = min(3, 2, 16) = 2 blocks
active_warps = 2 × 8 = 16 warp
occupancy = 16 / 32 = 50%
```

**occupancy 沒有變化**（依然是 smem-limited，2 blocks）。

可用 smem 從 12 KB 增加到 24 KB，允許演算法做更多計算。

**這個交換值不值得？**

如果更多 smem 能讓演算法避免 global memory 存取（比如 tile 更大的資料塊），那 occupancy 維持 50% 但每個 warp 做更多有效工作，整體 throughput 可能更好。如果只是為了存 scratch space 而不減少 global memory 存取，那沒有理由這樣做。

---

**問題 3：blockDim 改成 128，register 72 不變，smem 12 KB 不變**

```
warps_per_block = 128 / 32 = 4

Register bound：
  regs_per_block = 128 × 72 = 9,216
  對齊：9,216 / 256 = 36，整除
  active_blocks_reg = ⌊65,536 / 9,216⌋ = ⌊7.11...⌋ = 7 blocks

Shared memory bound（carveout 32 KB）：
  active_blocks_smem = ⌊32,768 / 12,288⌋ = 2 blocks

Block count bound：16

active_blocks = min(7, 2, 16) = 2 blocks
active_warps = 2 × 4 = 8 warp
occupancy = 8 / 32 = 25%
```

**blockDim 從 256 降到 128，occupancy 從 50% 降到 25%！**

原因：smem bound 依然鎖定在 2 blocks，但每個 block 的 warp 數從 8 降到 4，所以 active warps 少了一半。

**結論**：實驗中 blockDim=256 比 128 快，從 occupancy 角度看現在說得通了——128 的 occupancy 只有 25%，scheduler 的 warp 切換空間嚴重不足。

</details>

---

## 自我檢核（主動回憶）

不要看解答，從記憶中回答以下問題。如果卡住，先算出來再對答案。

1. **基本公式**：T4 每 SM 最多 32 active warp，最多 16 block，register file 多大？shared memory 預設多少？

2. **Register 對齊**：為什麼 register 要以 256 為單位向上對齊？不對齊的話會怎樣？

3. **Warp count 上限的優先序**：register bound 算出 5 blocks，每個 block 8 warp，所以 active warps 是多少？（不是 40）

4. **Shared memory carveout**：把 carveout 從 32 KB 改成 64 KB，一定會提升 occupancy 嗎？什麼情況下不會？

5. **複合 bound**：什麼叫做「複合 bound」？怎麼判斷是不是複合 bound？

6. **100% occupancy 的陷阱**：算例 C 的 occupancy 是 100%，但這個設計有什麼潛在問題？

7. **優化方向的選擇**：算例 D 中，降低 register 為什麼沒用？這說明了什麼原則？

---

## 測試用例速算表

用來驗算你的手算能力。先自己填，再對照下方答案。

carveout 均為 32 KB，smem = 0 除非特別標注。

| blockSize | regs/thread | smem/block | active_blocks_reg | active_blocks_smem | active_blocks | active_warps | occupancy |
|-----------|-------------|------------|-------------------|--------------------|---------------|--------------|-----------|
| 32 | 32 | 0 | ? | 16 | ? | ? | ? |
| 64 | 32 | 0 | ? | 16 | ? | ? | ? |
| 128 | 32 | 0 | ? | 16 | ? | ? | ? |
| 256 | 32 | 0 | ? | 16 | ? | ? | ? |
| 256 | 64 | 0 | ? | 16 | ? | ? | ? |
| 256 | 32 | 8 KB | ? | ? | ? | ? | ? |

<details>
<summary>速算表解答</summary>

以下是完整計算過程：

**Row 1：blockSize=32，regs=32，smem=0**
```
regs_per_block = 32 × 32 = 1,024，對齊 OK
active_blocks_reg = ⌊65,536 / 1,024⌋ = 64 → 截斷為 16
active_blocks = min(16, 16, 16) = 16 blocks
active_warps = 16 × 1 = 16 warp
occupancy = 16 / 32 = 50%
```

**Row 2：blockSize=64，regs=32，smem=0**
```
regs_per_block = 64 × 32 = 2,048，對齊 OK
active_blocks_reg = ⌊65,536 / 2,048⌋ = 32 → 截斷為 16
active_blocks = 16 blocks
active_warps = 16 × 2 = 32 warp
occupancy = 32 / 32 = 100%
```

**Row 3：blockSize=128，regs=32，smem=0**
```
regs_per_block = 128 × 32 = 4,096，對齊 OK
active_blocks_reg = ⌊65,536 / 4,096⌋ = 16
active_blocks = 16 blocks
active_warps = 16 × 4 = 64 warp > 32 → warp-limited → 8 blocks active
active_warps = 8 × 4 = 32 warp
occupancy = 32 / 32 = 100%
```

**Row 4：blockSize=256，regs=32，smem=0**
```
regs_per_block = 256 × 32 = 8,192，對齊 OK
active_blocks_reg = ⌊65,536 / 8,192⌋ = 8
active_blocks = min(8, 16, 16) = 8 blocks
active_warps = 8 × 8 = 64 warp > 32 → warp-limited → 4 blocks active
active_warps = 4 × 8 = 32 warp
occupancy = 32 / 32 = 100%
```

**Row 5：blockSize=256，regs=64，smem=0**
```
regs_per_block = 256 × 64 = 16,384，對齊 OK
active_blocks_reg = ⌊65,536 / 16,384⌋ = 4
active_blocks = min(4, 16, 16) = 4 blocks
active_warps = 4 × 8 = 32 warp
occupancy = 32 / 32 = 100%
```

（剛好！register 是限制因子，但 4 blocks × 8 warp = 32 warp，恰好達到 warp 上限）

**Row 6：blockSize=256，regs=32，smem=8 KB**
```
regs_per_block = 256 × 32 = 8,192
active_blocks_reg = ⌊65,536 / 8,192⌋ = 8

smem_per_block = 8,192 bytes
active_blocks_smem = ⌊32,768 / 8,192⌋ = 4

active_blocks = min(8, 4, 16) = 4 blocks
active_warps = 4 × 8 = 32 warp
occupancy = 32 / 32 = 100%
```

（shared memory 是限制因子，但 4 blocks 剛好讓 warp 達到 100%）

| blockSize | regs/thread | smem/block | active_blocks_reg | active_blocks_smem | active_blocks | active_warps | occupancy |
|-----------|-------------|------------|-------------------|--------------------|---------------|--------------|-----------|
| 32 | 32 | 0 | 16（cap） | 16 | 16 | 16 | **50%** |
| 64 | 32 | 0 | 16（cap） | 16 | 16 | 32 | **100%** |
| 128 | 32 | 0 | 16 | 16 | 8（warp-limited） | 32 | **100%** |
| 256 | 32 | 0 | 8 | 16 | 4（warp-limited） | 32 | **100%** |
| 256 | 64 | 0 | 4 | 16 | 4 | 32 | **100%** |
| 256 | 32 | 8 KB | 8 | 4 | 4（smem-limited） | 32 | **100%** |

注意：Row 1 的 blockSize=32 只有 1 warp/block，16 blocks × 1 warp = 16 warp，無法達到 32 active warps，所以 occupancy 只有 50%。這說明 block 太小不是好事。

</details>

---

## 下一步

完成本練習後，你應該能對任意 kernel 配置（blockSize + regs + smem）快速估算 occupancy 和限制因子。

- 前置章節：[Ch 11 佔用率](./11-occupancy.md)（理論和公式推導）
- 下一步：[Ch 12 第一個 kernel](./12-first-kernel.md)（把這些估算帶進真實的 kernel 設計決策）

**最後一個提醒**：occupancy 是工具，不是目標。100% occupancy 的 kernel 可能比 50% occupancy 的慢，因為記憶體存取模式、計算密度、warp divergence 都會影響實際效能。手算 occupancy 的價值在於讓你理解資源分配，而不是讓你追求數字上的最高分。
