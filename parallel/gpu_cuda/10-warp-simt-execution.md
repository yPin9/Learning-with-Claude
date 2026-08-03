# Ch 10 — Warp 與 SIMT 執行

> **目標**：理解 GPU 最核心的執行模型——warp 是什麼、SIMT lockstep 怎麼運作、warp divergence 如何拖慢效能、以及 Volta 之後的 Independent Thread Scheduling 帶來哪些新規則。
>
> **環境**：CUDA 12.x，T4（Turing sm_75），WSL2 + `nvcc -arch=sm_75`，`nsys`/`ncu` 可選用於驗證。

---

## 為什麼需要這章

前幾章我們談執行緒階層（thread/block/grid）和記憶體階層（registers/shared memory/global memory）。但有一個關鍵環節被跳過了：GPU 怎麼在硬體上真正執行這些執行緒？

GPU 不是把每個 thread 當成獨立的小 CPU 核心來跑。SM（Streaming Multiprocessor）內部的執行單位是 **warp**——32 個 thread 綁在一起，在同一個 cycle 執行同一條指令。這個「32 個人同步行動」的設計，決定了：

- 為什麼 memory coalescing 那麼重要（32 個 thread 同時存取記憶體）
- 為什麼 warp divergence 會讓效能砍半
- 為什麼你需要 `__syncwarp()` 而不只是 `__syncthreads()`
- 為什麼 Volta 之後的 GPU 程式碼需要特別注意 race condition

不理解 warp 執行模型，就無法真正讀懂 profiler 的輸出，也無法解釋「明明核心看起來沒問題，但跑起來只有 30% 效率」。

---

## 先建立直覺

想像一個大型舞蹈表演：舞台上有 32 位舞者（thread），他們全部聽同一個指揮（warp scheduler）的號令，在同一拍（cycle）做同一個動作（指令）。但每位舞者站在不同位置、手上拿不同道具（不同的資料）。

這就是 SIMT：**Single Instruction, Multiple Threads**。

如果有舞者提前離開舞台（執行了不同的 branch），其他人只能暫停等他回來，或者他乾脆戴上面具（mask off）假裝執行但什麼都不做。

---

## 核心內容

### Warp 的定義

**warp = 32 個 thread**，是 GPU 排程的最小單位。

重要性質：
- 一個 warp 內的 thread 必定在同一個 block 內
- Warp ID（在 block 內）= `threadIdx.x / 32`（一維情況）
- 一個 block 的 warp 數量 = `ceil(blockDim.x * blockDim.y * blockDim.z / 32)`

```cuda
__global__ void print_warp_id(void) {
    int global_tid = blockIdx.x * blockDim.x + threadIdx.x;
    int warp_id_in_block = threadIdx.x / 32;
    int lane_id = threadIdx.x % 32;  // 在 warp 內的位置，0~31

    if (lane_id == 0) {
        printf("Block %d, Warp %d: global threads %d~%d\n",
               blockIdx.x, warp_id_in_block,
               global_tid, global_tid + 31);
    }
}
```

**為什麼是 32？**

這是 NVIDIA 的歷史工程決策，不是自然定律。選 32 是在幾個張力間取平衡：

- 太小（如 8）：SIMT 效益不夠，硬體利用率低
- 太大（如 64）：divergence overhead 更嚴重，register 分配更複雜
- 32 剛好讓一個 SM 在等記憶體時可以有足夠多的 warp 來輪換填空（latency hiding）

AMD 的 RDNA 選了 32（wave32），GCN 選了 64（wavefront64），這兩個都是合理選擇，只是取捨不同。

---

### SIMT Lockstep 執行

CPU 和 GPU 的核心差異：

```
=== CPU（多核，每核獨立）===

Core 0:  FETCH  DECODE  EXEC-ADD  WRITEBACK  ...（自己的 PC，自己的資料）
Core 1:  FETCH  DECODE  EXEC-MUL  WRITEBACK  ...（可能執行完全不同的程式）
Core 2:  FETCH  DECODE  EXEC-LD   WRITEBACK  ...

=== GPU Warp（32 個 thread，lockstep）===

Cycle:      1      2      3      4      5
T0:        ADD    MUL    LD     ST     ADD
T1:        ADD    MUL    LD     ST     ADD   ← 同一條指令
T2:        ADD    MUL    LD     ST     ADD   ← 但各自的暫存器資料不同
...
T31:       ADD    MUL    LD     ST     ADD

         ↑ 整個 warp 共用一個指令流（單 PC），
           每個 thread 有自己的 register file
```

這代表：
- **單一 fetch/decode**：一條指令的 fetch 和 decode 代價，被 32 個 thread 共同分攤
- **資料並行**：每個 thread 有自己的暫存器（T0 的 `r0`、T1 的 `r0` 是不同的物理暫存器）
- **完全同步**：在沒有 divergence 的情況下，warp 內的 32 個 thread 永遠在同一 cycle 執行同一指令

```cuda
// 這個 kernel：每個 thread 做 a[i] = b[i] + c[i]
// 硬體層面：warp 內 32 個 thread 同時各自讀 b[lane]、c[lane]，
//           各自執行加法，各自寫回 a[lane]
__global__ void vec_add(float *a, const float *b, const float *c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        a[i] = b[i] + c[i];  // 32 個 thread 同一 cycle 執行這行
    }
}
```

---

### Zero-Overhead Warp Switching（延遲隱藏）

這是 GPU 高吞吐量的核心機制。

**問題**：global memory 存取延遲高達 400~800 個 cycle（T4 上實測）。如果只有一個 warp，這段時間 SM 完全閒著。

**解法**：SM 同時維護多個 active warp，一個 warp 在等記憶體時，立刻切換到下一個 ready warp。

```
=== 多 Warp 填滿記憶體延遲（時間向右）===

           發射 LD      等待 L2/global memory 回應（~400 cycles）    資料回來
           |            |<---------------------------------------->|  |
Warp 0:  [EXEC][EXEC][ LD ][ . . . . . . . . . waiting . . . . . . ][EXEC][EXEC]
Warp 1:          [EXEC][EXEC][ LD ][ . . . . . waiting . . . ][EXEC][EXEC]
Warp 2:                  [EXEC][EXEC][ LD ][ . . waiting . ][EXEC][EXEC]
Warp 3:                          [EXEC][EXEC][ LD ][ wait ][EXEC][EXEC]
...
         ↑ Warp scheduler 每個 cycle 選一個 ready warp 發射指令
           SM 的計算管線幾乎不空轉
```

**關鍵**：GPU 的 context switch 是 **zero-overhead**。

CPU 在 context switch 時需要把目前 thread 的 register 存到記憶體（save），載入下一個 thread 的 register（restore），這本身就要幾百個 cycle。

GPU 完全不需要這樣做。每個 warp 的 register 是**靜態分配在 SM 的 register file**中，它們一直都在那裡，warp scheduler 切換時只是換一個「現在發射哪個 warp」的指標——不需要 save/restore，切換代價接近零。

這就是為什麼 Ch 9 說「registers 是最快的記憶體」——它們從來不會被踢出去。

```
=== CPU vs GPU 的 context switch ===

CPU Thread Switch：
  1. 儲存 CPU registers 到 stack/PCB    （記憶體存取，~數十 ns）
  2. 切換 PC, stack pointer, page table  （TLB flush 可能）
  3. 載入下一個 thread 的 registers     （記憶體存取，~數十 ns）
  → 代價：幾百到幾千個 cycle

GPU Warp Switch：
  1. Warp scheduler 把發射指標指向下一個 ready warp
  → 代價：0~1 cycle
```

---

## Warp Divergence（執行緒分歧）

### 什麼是 Divergence

當 warp 內的 32 個 thread 在 if/else/switch 裡走了不同路徑，就發生 **divergence**。

由於整個 warp 必須執行同一條指令，硬體的解法是**序列化**：

```
=== 無 Divergence（理想情況）===

Cycle:  1    2    3    4    5    6
T0-31: [A0] [A1] [A2] [A3] [A4] [A5]   ← 全員同時執行路徑 A，每 cycle 100% 效率

=== 有 Divergence（if/else）===

情境：T0~T15 走 if-branch（路徑 A），T16~T31 走 else-branch（路徑 B）

Pass 1（執行 if-branch）：
Cycle:  1    2    3    4
T0-15: [A0] [A1] [A2] [A3]   ← 執行 if 路徑
T16-31:[--] [--] [--] [--]   ← mask OFF，這些 thread 不做任何有效工作

Pass 2（執行 else-branch）：
Cycle:  5    6    7    8
T0-15: [--] [--] [--] [--]   ← mask OFF，這些 thread 不做任何有效工作
T16-31:[B0] [B1] [B2] [B3]   ← 執行 else 路徑

總 cycle：8（本來只需要 4）
效率：50%（最壞情況）
```

Divergence 不是「其中一半 thread 消失了」——所有 thread 最終都會執行完自己的路徑，只是被迫序列化，等候時間浪費掉了。

### 哪種 Divergence 更糟？

```cuda
// 案例一：threadIdx.x < 16
if (threadIdx.x < 16) {
    // 路徑 A：T0~T15
} else {
    // 路徑 B：T16~T31
}
// → 2 個分支，序列化 2 次。效率 50%。
//   但 T0~T15 同步、T16~T31 同步，至少每個 pass 是 coherent 的。

// 案例二：threadIdx.x % 2 == 0
if (threadIdx.x % 2 == 0) {
    // 路徑 A：T0, T2, T4, ..., T30（偶數）
} else {
    // 路徑 B：T1, T3, T5, ..., T31（奇數）
}
// → 同樣是 2 個分支，效率也是 50%。
```

這兩個案例在效能上幾乎一樣——都是 2 個分支，2-pass 序列化。

**真正糟糕的情況**是分支數量多：

```cuda
// 案例三：每個 thread 走不同路徑
switch (threadIdx.x % 8) {
    case 0: /* 路徑 A */ break;
    case 1: /* 路徑 B */ break;
    // ...
    case 7: /* 路徑 H */ break;
}
// → 最多 8 個分支，序列化 8 次，效率降到 12.5%（最壞）
```

### Compiler 的 Predicated Instruction 優化

對於短小的 if/else，compiler 有時可以把它轉成 **predicated instruction**（謂詞指令），完全避免 divergence：

```
// 原始程式碼
if (x > 0) y = a; else y = b;

// 理想的 predicated 形式（PTX 層面）
setp.gt.f32 p, x, 0;   // 設定謂詞暫存器 p = (x > 0)
selp.f32 y, a, b, p;   // y = p ? a : b
```

條件賦值在一個 cycle 內完成，不需要 branch，32 個 thread 全部同時執行，無 divergence。

但這只在 **if/else 都很短、沒有副作用**（memory store、函式呼叫等）時有效。一旦 if-body 有幾十行，compiler 就只能選擇真正的 branch，divergence 就回來了。

```cuda
// Compiler 通常能優化成 predicated：
float val = (threadIdx.x < 16) ? a[i] : b[i];

// Compiler 無法優化（路徑太長）：
if (threadIdx.x < 16) {
    // 50 行計算
} else {
    // 50 行計算
}
```

---

## 底層機制

### Active Mask 與 Warp Intrinsics

在 diverged 的 warp 內，「現在哪些 thread 是 active」這個資訊存在 **active mask**（32-bit bitmask）中。bit `i` 為 1 代表 lane `i` 目前是 active 的。

```cuda
// 取得當前的 active mask
unsigned int mask = __activemask();
// 如果 warp 完全沒有 divergence，mask = 0xFFFFFFFF（所有 32 位都是 1）

// 利用 ballot 做 warp-level reduction：不需要 shared memory
// __ballot_sync(mask, predicate)：在 active threads 中廣播 predicate
// 回傳一個 32-bit mask，bit i = 1 代表 lane i 的 predicate 為 true

__global__ void count_positives(const float *data, int *count, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    float val = (i < n) ? data[i] : 0.0f;

    // 在 warp 內，哪些 thread 的值是正的？
    unsigned int ballot = __ballot_sync(0xFFFFFFFF, val > 0.0f);
    int warp_count = __popc(ballot);  // 數 1 的個數

    if (threadIdx.x % 32 == 0) {
        atomicAdd(count, warp_count);
    }
}
```

### Warp-Level Voting Functions

```cuda
// __any_sync(mask, predicate)：warp 內任一 thread 的 predicate 為 true → 回傳非零
// __all_sync(mask, predicate)：warp 內所有 thread 的 predicate 為 true → 回傳非零

__global__ void check_bounds(const float *data, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    float val = (i < n) ? data[i] : 0.0f;

    // 如果 warp 內有任何越界值，觸發警告
    if (__any_sync(0xFFFFFFFF, val > 1000.0f)) {
        if (threadIdx.x % 32 == 0) {
            printf("Warp %d contains out-of-range value!\n", threadIdx.x / 32);
        }
    }
}
```

### Volta+ Independent Thread Scheduling（ITS）

這是 Volta 架構最重要的執行模型改變，T4（Turing）也繼承了這個設計。

**Pascal 及更早（舊模型）**：

```
Warp 有一個共享的 PC（Program Counter）

所有 32 個 thread 必須走同一條指令流
Divergence 只能透過 mask 解決：
  → 走 if 的 thread 執行，走 else 的 mask off
  → 然後走 else 的執行，走 if 的 mask off
  → 兩段走完後，PC 繼續往後

隱含保證：同一 warp 的 thread 在沒有 divergence 時，
           永遠在同一時間執行同一行程式碼
```

**Volta/Turing/Ampere/Hopper（ITS 模型）**：

```
每個 thread 有自己的 PC 和 call stack

Warp scheduler 可以 interleave diverged threads
不再是「A 完全執行完，再執行 B」
而是可能「A 執行一點，B 執行一點，A 再執行一點...」

優點：
  - 某些 divergence 模式可以減少序列化代價
  - Thread 可以有更細緻的控制流程
  - 支援更複雜的同步模式

危險：
  - 舊程式碼依賴「同 warp 的 thread 隱含同步」的假設，
    在 Volta+ 上可能成為 race condition
```

**舊程式碼在 Volta+ 上的 Race Condition**：

```cuda
// 危險的舊寫法（Pascal 上可能偶然正確，Volta+ 上是 bug）
__global__ void dangerous_reduction(float *data, float *result) {
    int lane = threadIdx.x % 32;
    __shared__ float smem[32];

    smem[lane] = data[blockIdx.x * 32 + lane];

    // 假設 warp 內的 thread 是同步的（Pascal 上通常成立）
    // 但 Volta+ 的 ITS 不保證這個！
    if (lane < 16) smem[lane] += smem[lane + 16];  // ← smem[lane+16] 可能還沒更新完
    if (lane < 8)  smem[lane] += smem[lane + 8];
    if (lane < 4)  smem[lane] += smem[lane + 4];
    if (lane < 2)  smem[lane] += smem[lane + 2];
    if (lane < 1)  smem[lane] += smem[lane + 1];

    if (lane == 0) *result = smem[0];
}

// 正確的 Volta+ 寫法：明確使用 __syncwarp()
__global__ void safe_reduction(float *data, float *result) {
    int lane = threadIdx.x % 32;
    __shared__ float smem[32];

    smem[lane] = data[blockIdx.x * 32 + lane];
    __syncwarp();  // 確保 smem 的寫入對整個 warp 可見

    if (lane < 16) smem[lane] += smem[lane + 16];
    __syncwarp();
    if (lane < 8)  smem[lane] += smem[lane + 8];
    __syncwarp();
    if (lane < 4)  smem[lane] += smem[lane + 4];
    __syncwarp();
    if (lane < 2)  smem[lane] += smem[lane + 2];
    __syncwarp();
    if (lane < 1)  smem[lane] += smem[lane + 1];
    __syncwarp();

    if (lane == 0) *result = smem[0];
}
```

更好的寫法是直接用 warp shuffle（Ch 15 詳述），完全跳過 shared memory：

```cuda
// 用 shuffle 做 warp reduction（最佳實踐）
__global__ void shuffle_reduction(float *data, float *result) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    float val = data[i];

    // Warp reduce：每輪把右半邊的值加到左半邊
    for (int offset = 16; offset > 0; offset >>= 1) {
        val += __shfl_down_sync(0xFFFFFFFF, val, offset);
    }
    // Lane 0 持有 warp sum
    if (threadIdx.x % 32 == 0) atomicAdd(result, val);
}
```

### `__syncwarp(mask)` 的語意

```cuda
__syncwarp(0xFFFFFFFF);  // 同步 warp 內所有 32 個 thread
__syncwarp(mask);         // 只同步 mask 指定的 thread subset

// 保證：在 __syncwarp() 之後，
// - mask 內所有 thread 都到達這個點
// - 這些 thread 之前對 shared memory 的寫入，對 mask 內所有 thread 可見
```

`__syncwarp()` 比 `__syncthreads()` 輕量：它只同步一個 warp 內的 thread，不是整個 block。

---

### Warp Scheduler 的每個 Cycle

Turing SM 有 4 個 **processing block**，每個 processing block 有 1 個 warp scheduler。

每個 cycle，warp scheduler 做的事：

```
1. 從 active warp 列表中選一個 "ready" 的 warp
   Ready = 所有 source operand 都已就緒（沒有 pending memory，沒有 RAW 相依性）

2. 從該 warp 取出下一條要執行的指令

3. 把指令發射到對應的執行管線：
   - FP32 pipeline  ← 浮點運算
   - INT32 pipeline ← 整數運算
   - SFU            ← 特殊函式（sin/cos/exp/rcp/sqrt）
   - LD/ST unit     ← 記憶體存取
   - Tensor Core    ← WMMA 矩陣運算（sm_75 支援 TC 第 2 代）

4. Dual-issue（Turing 支援）：
   某些情況下，同一個 cycle 可以同時發射兩條不相依的指令
   例如：FP32 加法 + INT32 比較，或 FP32 加法 + LD 請求
```

```
=== Turing SM（sm_75）架構示意 ===

SM
├── Processing Block 0
│   ├── Warp Scheduler          ← 每 cycle 選 ready warp，可 dual-issue
│   ├── Register File（~16K 個 32-bit register）
│   ├── 16x FP32 ALU（CUDA Core）
│   ├── 16x INT32 ALU
│   ├── 2x  Tensor Core（4x4 矩陣乘加，FP16/INT8）
│   ├── 4x  SFU
│   └── 8x  LD/ST
├── Processing Block 1          ← 結構相同
├── Processing Block 2
├── Processing Block 3
└── L1 Cache / Shared Memory（96KB，可設定比例）

每個 SM：
  - 最多 32 個 active warp（Turing 設計值）
  - 4 個 warp scheduler，各自獨立排程
  - 每個 processing block 管 8 個 warp
```

Volta 每個 processing block 有 **2 個** warp scheduler，所以每 SM 最多管 64 個 active warp。Turing 降回 1 個（以換取面積給 Tensor Core），所以最多 32 個。這直接影響 latency hiding 能力，是 Ch 11（佔用率）的主題。

---

## 對比取捨

| 特性 | Pascal（sm_6x）及更早 | Volta（sm_70）/ Turing（sm_75）/ 之後 |
|---|---|---|
| Warp PC | 所有 thread 共享 1 個 | 每個 thread 各自 1 個 |
| Call Stack | 共享 | 每個 thread 各自 |
| Divergence 處理 | 純 mask-off，嚴格序列化 | ITS：可 interleave diverged threads |
| Implicit warp sync | 存在（隱含保證） | 不存在（必須顯式 `__syncwarp()`） |
| `__syncwarp()` | 可選（通常冗餘） | **必要**（warp 內共享記憶體存取後） |
| Warp/SM 最大數 | 64（Pascal） | 32（Turing）/ 64（Volta）/ 48（Ampere）|
| Dual-issue | 有限支援 | 更完整支援（FP32+INT32/LD） |
| Tensor Core | 無 | 第 2 代（Turing）/ 第 3 代（Ampere）|

| Divergence 類型 | 效率影響 | 對策 |
|---|---|---|
| if/else，2 分支，50/50 split | 50% | 調整 thread 分配讓分支對齊 warp |
| if/else，2 分支，但 per-warp coherent | 0%（無 divergence） | 確保分支邊界在 warp 邊界上 |
| switch，N 分支 | ~1/N | 避免 warp 內資料跨多 case |
| 短 if，無副作用 | 0%（compiler predicated） | 讓 compiler 優化；小心寫法 |
| 迴圈次數不同 | 取決於 loop count 分布 | 讓同 warp 的 thread 有相近的迴圈次數 |

---

## 踩雷

**踩雷一：Volta+ 上依賴隱含 warp 同步**

`if (lane < 16) smem[lane] += smem[lane + 16];` 後面沒有 `__syncwarp()`，在 Pascal 上跑了幾年沒問題，搬到 Turing 就出現不確定結果。Debug 極其困難，因為它可能 90% 時間正確，10% 時間給出錯誤答案。只要用了 warp 內 shared memory 交換，就加 `__syncwarp()`。

**踩雷二：誤以為 `__syncthreads()` 可以取代 `__syncwarp()`**

`__syncthreads()` 同步的是整個 block，代價更高，且在 diverged 路徑裡呼叫 `__syncthreads()` 是未定義行為（死鎖）。如果只需要 warp 內同步，用 `__syncwarp()`；如果需要 block 內同步，用 `__syncthreads()`；不要混用。

**踩雷三：把 warp divergence 當成 block divergence 思考**

不同 block 走不同路徑完全沒問題——它們在不同 SM 上獨立執行。Divergence 的代價只存在於**同一個 warp 的 32 個 thread 之間**。`if (blockIdx.x % 2 == 0)` 不會造成任何 warp divergence。

**踩雷四：`__activemask()` 不是 `__ballot_sync(0xFFFFFFFF, true)`**

`__activemask()` 回傳目前 hardware-active 的 thread mask，這在 ITS 下可能不等於「語意上應該 active」的 thread 集合。在 diverged 路徑裡呼叫 `__activemask()` 的結果是未定義的（spec 不保證它回傳什麼）。安全做法是明確傳入你知道是 active 的 mask 給 `__ballot_sync`、`__shfl_sync` 等函式。

**踩雷五：誤以為 zero-overhead warp switch 代表 warp 越多越好**

Active warp 數量受 register 和 shared memory 使用量限制。如果 kernel 每個 thread 用了 64 個 register，SM 只能放 32K/64 = 512 個 thread = 16 個 warp（遠少於硬體上限 32）。register 用太多反而讓 latency hiding 能力下降。這是 Ch 11（佔用率）的核心問題。

---

## 進階

### Warp-Level Primitive 的完整生態（預覽）

除了 `__syncwarp`、`__ballot_sync`、`__activemask`，Volta+ 支援完整的 warp-level primitive 家族（Ch 15 詳述）：

```cuda
// Shuffle：warp 內 thread 直接互傳暫存器值（不用 shared memory）
float val_from_lane3 = __shfl_sync(mask, my_val, 3);       // broadcast from lane 3
float val_from_prev  = __shfl_up_sync(mask, my_val, 1);    // 取 lane-1 的值
float val_from_next  = __shfl_down_sync(mask, my_val, 1);  // 取 lane+1 的值
float val_xor        = __shfl_xor_sync(mask, my_val, 1);   // butterfly pattern

// Vote（已見過）
unsigned ballot = __ballot_sync(mask, predicate);
int     any_val = __any_sync(mask, predicate);
int     all_val = __all_sync(mask, predicate);

// Match（Volta+ 新增）
unsigned match_mask = __match_any_sync(mask, my_val);  // 找出 warp 內 value 相同的 thread
```

這些 intrinsic 的效能遠高於等效的 shared memory 實作，因為它們直接走 warp 內部的 operand network，不經過 L1 cache。

### 利用 warp coherence 優化 divergence

當你知道 divergence 不可避免，可以調整資料排列，讓同一個 warp 的 thread 盡量走相同路徑：

```cuda
// 糟糕：資料按原始順序，divergence 很嚴重
// Thread 0,1,2,...,31 的資料類型隨機分散 → warp 內什麼類型都有

// 較好：按類型排序資料，讓同類型的資料集中在相鄰 thread
// Thread 0~15 處理 type A，Thread 16~31 處理 type B
// → warp 的前半後半各自 coherent

// 最好：確保 warp 邊界對齊分支邊界
// if (i < N/2)  ← 讓分界點落在 warp 大小的整數倍
```

---

## 動手練習

**練習 1：量化 divergence 代價**

```cuda
// 分別實作以下兩個 kernel，用 cuda event 計時：
// A：所有 thread 執行相同路徑（以 if (false) 模擬）
// B：warp 內 thread 各半走不同路徑

__global__ void no_divergence(float *a, float *b, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;

    float x = a[i];
    // 刻意讓所有 thread 走同一路：
    // 讓分支條件永遠為真或永遠為假
    if (blockIdx.x % 2 == 0) {  // 整個 block 走同一路，不是 warp 內 diverge
        b[i] = x * 2.0f;
    } else {
        b[i] = x + 1.0f;
    }
}

__global__ void with_divergence(float *a, float *b, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;

    float x = a[i];
    if (threadIdx.x % 2 == 0) {  // warp 內 diverge：偶數/奇數 thread 走不同路
        b[i] = x * 2.0f;
    } else {
        b[i] = x + 1.0f;
    }
}
```

跑大 n（如 1<<25），計時差異應在 ~1.8~2.0x 左右（取決於路徑長度）。

**練習 2：`__syncwarp` 的必要性驗證**

在 sm_75 上，寫一個 warp-level shared memory reduction，分別有/無 `__syncwarp()`，用 `cuda-memcheck --tool racecheck` 檢查是否有 race：

```bash
nvcc -arch=sm_75 -lineinfo -g reduction.cu -o reduction
cuda-memcheck --tool racecheck ./reduction
```

**練習 3：用 `__ballot_sync` 實作 warp-level prefix sum（no shared memory）**

```cuda
// 提示：用 shuffle 實作 inclusive scan
// 每個 lane 的輸出 = 自己的值 + 左邊所有 lane 的值之和
// 只用 __shfl_up_sync，不用 shared memory
__global__ void warp_prefix_sum(const int *in, int *out, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;

    int val = in[i];
    // TODO: 用 __shfl_up_sync 完成 warp-level inclusive prefix sum
    for (int offset = 1; offset < 32; offset <<= 1) {
        int neighbour = __shfl_up_sync(0xFFFFFFFF, val, offset);
        if (threadIdx.x % 32 >= offset) val += neighbour;
    }
    out[i] = val;
}
```

---

## 本章重點

- **Warp = 32 個 thread**，是 GPU 硬體排程的最小單位。Warp 內的 thread 共享指令流（SIMT），但各自擁有 register 資料
- **Zero-overhead warp switching** 是 GPU 隱藏記憶體延遲的核心機制：SM 同時持有多個 active warp 的 register，切換代價接近零
- **Warp divergence** 發生在 if/else 讓同一 warp 的 thread 走不同路徑，導致序列化執行，最壞情況效率降至 1/N（N 為分支數）
- **`__activemask()`** 和 **`__ballot_sync()`** 讓你在程式碼層面感知 warp 的 active 狀態，實現 warp-level reduction 等操作
- **Volta/Turing（ITS）**：每個 thread 有獨立 PC 和 call stack，打破了舊的隱含 warp 同步保證。依賴 implicit warp sync 的舊程式碼在 Volta+ 上有 race condition 風險
- **`__syncwarp(mask)`** 是 Volta+ 上 warp 內 shared memory 操作後的必要同步點，比 `__syncthreads()` 輕量且語意正確

---

## 自我檢核

1. 一個 block 有 128 個 thread，會形成幾個 warp？如果 blockDim = 100，呢？（最後一個 warp 的 thread 數量是什麼？）

2. 為什麼 GPU 的 warp switching 是「zero-overhead」？CPU 的 context switch 代價從哪裡來，GPU 為什麼沒有這個代價？

3. `if (threadIdx.x < 16)` 和 `if (threadIdx.x % 16 == 0)` 哪個會造成更嚴重的 warp divergence？（假設 warp 大小 32）

4. Volta+ 的 Independent Thread Scheduling 讓你在舊程式碼裡少了哪個隱含保證？如何修復？

5. 為什麼在 diverged 路徑裡呼叫 `__syncthreads()` 是未定義行為（可能死鎖）？

6. Compiler 在什麼條件下會把 `if/else` 轉成 predicated instruction 而非真正的 branch？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide §SIMT Architecture / §Independent Thread Scheduling**（docs.nvidia.com）— 官方 spec，ITS 那節說明了 Volta 改變的精確語意，包含 `__syncwarp` 的記憶體序保證

2. **NVIDIA Volta Architecture Whitepaper**（2017）— *Independent Thread Scheduling* 小節直接解釋了為什麼 NVIDIA 在 Volta 改變 warp 模型，以及 GPU 如何在 diverged thread 間 interleave 執行，比 Programming Guide 更有設計動機說明

3. **《Programming Massively Parallel Processors》4th Ed., Ch 4 §SIMT Execution**（Kirk & Hwu）— 從教學角度把 SIMT 和 SPMD 的差異講得很清楚，附有 divergence 的逐步 cycle 追蹤，適合對照本章圖表

4. **"Dissecting the NVidia Turing T4 GPU via Microbenchmarking"**（Jia et al., arXiv 1903.07486）— 用 microbenchmark 實測 T4（sm_75）的 warp 指令延遲、dual-issue 行為、memory pipeline 各細節，是了解「硬體實際行為 vs 文件說法」差距的第一手資料

5. **NVIDIA Developer Blog: "Using CUDA Warp-Level Primitives"**（2018, developer.nvidia.com）— 涵蓋 Volta 新增的 `*_sync` 後綴 warp intrinsic 全集（`__shfl_sync`/`__ballot_sync`/`__activemask` 等），並說明為何舊的無 mask 版本被棄用

---

前一章：[Ch 9 記憶體階層](./09-memory-hierarchy.md)

**本章處理的是 GPU 執行模型最核心的機制**——warp 不只是個數字，它決定了你每一行 CUDA 程式碼在硬體上的真實行為。下一章把這個基礎用在更量化的問題上：

[Ch 11 佔用率（Occupancy）](./11-occupancy.md) 將把 warp 數量、register 用量、shared memory 用量三個因素整合進一個計算公式，解釋「為什麼加一個 register 可以讓核心速度腰斬」，以及如何在三者間找到最佳平衡。

---

*往後 Ch 15 會深入 warp-level primitives（shuffle/vote/match）的完整用法，把本章的 warp intrinsic 前菜擴展成完整工具箱。*
