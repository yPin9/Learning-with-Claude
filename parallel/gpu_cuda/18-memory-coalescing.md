# Ch 18 — Memory Coalescing：存取全域記憶體的正確姿勢

> **目標**：理解 GPU global memory 的 transaction 機制，能辨識 coalesced / un-coalesced 存取模式，並用 shared memory tiling 與 SoA 佈局消除 bottleneck。
>
> **環境**：CUDA 12.x, Colab T4 (sm_75, Turing)

**前置章節**：
- [Ch 9 記憶體階層](./09-memory-hierarchy.md) — global / L2 / L1 / shared memory 層次
- [Ch 10 warp 與 SIMT 執行](./10-warp-simt-execution.md) — 32 個 thread 同步執行的本質
- [Ch 17 shared memory 與 tiling](./17-shared-memory-tiling.md) — tiling 的基礎寫法
- [Practice C — 矩陣乘法優化](./practice-c-matmul.md)（前一練習）

---

## 18.1 為什麼需要

[Ch 9](./09-memory-hierarchy.md) 建立了記憶體階層的概念：global memory（DRAM）容量最大，延遲最高（約 600–800 clock cycle），頻寬有限（A100 2 TB/s、T4 300 GB/s）。

[Ch 10](./10-warp-simt-execution.md) 告訴我們：一個 warp 內 32 個 thread 在同一個 clock 執行相同指令。這個事實在計算上是優勢，在記憶體上則是雙面刃——32 個 thread **同時**發出 load，GPU memory controller 要在同一個時間點伺服 32 個請求。

關鍵問題是：**這 32 個 load 能不能被合併成一次或少數幾次 DRAM transaction？**

能合併 → coalesced（合併存取），memory bus 使用率接近峰值頻寬。
不能合併 → un-coalesced（分散存取），同樣佔用 memory bus 的時間但搬到有用資料的比例極低，等同在用低速模式跑記憶體密集型 kernel。

在典型的矩陣計算、影像處理、科學模擬中，global memory 存取佔 kernel 執行時間 40–80%。coalescing 不對，再怎麼調 occupancy 或 instruction throughput 都是白費。這章就是解決這個問題。

---

## 18.2 建立直覺：transaction 與 sector

### 18.2.1 硬體單位

Volta / Turing（sm_70 / sm_75）的 memory subsystem 運作方式如下：

- **L2 cache line**：128 bytes（16 個 float）
- **memory transaction 的粒度（sector）**：32 bytes（8 個 float）
  - 一條 128-byte cache line = 4 個 32-byte sector
- 一個 warp 發出 global load 時，memory controller 以 **sector** 為最小單位去 L2 / DRAM 取資料

> CUDA C++ Programming Guide, "Device Memory Accesses" 章：「On devices of compute capability 7.x, global memory accesses by a warp are cached in L1 and L2. Caching in L1 is controlled on a per-access basis using modifiers. A global memory request for a warp is split into one 128-byte aligned segment … serviced in 32-byte sector transactions.」

### 18.2.2 最佳情境：完全 coalesced

32 個 thread，每個讀一個 float（4 bytes），共 128 bytes，且起始位址 128-byte 對齊：

```
Thread ID:   T0   T1   T2  ...  T31
Address:    [0]  [1]  [2]  ... [31]   (float array, 0-indexed)

Memory layout (bytes):
|<---------- 128 bytes = 1 cache line = 4 sectors ----------->|
| sector 0 (32B) | sector 1 (32B) | sector 2 (32B) | sector 3 (32B) |
| T0 T1 T2 T3 T4 T5 T6 T7 | T8 ... T15 | T16 ... T23 | T24 ... T31 |

Transaction count: 4 sector transactions → 1 L2 cache line miss
Bandwidth utilization: 128B requested / 128B transferred = 100%
```

這就是我們追求的目標。

### 18.2.3 Stride-2 存取

32 個 thread，每個讀 `arr[tid * 2]`：

```
Thread ID:   T0   T1   T2   T3  ...  T31
Address:    [0]  [2]  [4]  [6]  ... [62]

Memory layout (bytes, float = 4B):
|<-- sector 0 (B0-B31) -->|<-- sector 1 (B32-B63) -->| ...
| T0  _  T1  _  T2  _  T3  _ |  T4  _  T5  _  T6  _  T7  _ | ...
  ^        ^        ^                                            (每隔一個 float 有資料)

需要覆蓋的位址範圍：byte 0 到 byte 248（62 * 4 + 4 = 252B）
Sector 數：ceil(252 / 32) = 8 sectors（最多）
有效資料：32 * 4 = 128B
Bandwidth utilization: 128B / 256B = 50%（每個 sector 只用到半邊）
```

### 18.2.4 隨機存取（最壞情況）

```
Thread ID:   T0    T1    T2   ...  T31
Address:   [471] [23]  [892] ... [17]   (完全隨機)

每個 thread 可能落在不同的 32-byte sector，甚至不同 cache line。
最壞情況：32 個 sector transaction，搬來 1024B，有效資料只有 128B。
Bandwidth utilization: 128B / 1024B = 12.5%
```

### 18.2.5 三種模式對照圖

```
=== Coalesced (stride=1, aligned) ===

warp threads:  [T0][T1][T2]...[T31]
                |   |   |       |
memory:        [■][■][■].......[■]   ← 連續，1 cache line = 4 sectors
                |<-------- 128B ------->|
                1 cache line miss, 4 sector transactions

=== Strided (stride=2) ===

warp threads:  [T0]   [T1]   [T2]   ... [T31]
                |       |       |            |
memory:        [■][ ][■][ ][■][ ]...     [■][ ]
                |<---------- ~256B ----------->|
                ~8 sector transactions, 50% 浪費

=== Random access ===

warp threads:  [T0]  [T1]  [T2]  [T3]  ... [T31]
                |      |     |     |            |
memory:       [■]  [■]        [■]       [■]  ... (散落各處)
               最壞 32 sector transactions, ~12.5% 有效頻寬
```

---

## 18.3 核心機制詳解

### 18.3.1 Memory Controller 的運作流程

1. 一個 warp 執行 `ld.global` 指令
2. Hardware 收集 32 個 thread 的目標位址
3. 位址按 32-byte sector 邊界分組（每個位址 `>> 5` 取 sector index）
4. 每個不重複的 sector index → 一個 sector transaction 送往 L2
5. L2 miss → 再送往 DRAM（以 cache line = 128B 為單位 fetch）
6. 資料回來後，每個 thread 從其對應 sector 中取自己的 4/8 bytes

**關鍵結論**：warp 內 32 個 thread 存取的位址若映射到 K 個不同 sector，就需要 K 次 sector transaction。K 越小越好，理想是 4（一條完整 cache line）。

### 18.3.2 對齊的重要性

即使 32 個 thread 存取連續位址，若起始位址不對齊 128 bytes，可能橫跨兩條 cache line：

```
不對齊範例（起始 byte 64）：
cache line 0: [byte 0   ... byte 127]
cache line 1: [byte 128 ... byte 255]

warp 存取: byte 64 到 byte 191 → 跨兩條 cache line
→ 8 sector transactions（而非 4）
```

CUDA `cudaMalloc` 分配的基底位址保證 256-byte 對齊，所以只要存取模式本身是連續的，對齊通常不是問題。手動計算偏移量時要注意。

### 18.3.3 L1 cache 的角色

T4（sm_75）的 L1 data cache 以 128-byte cache line 服務，對 global load 預設啟用（compute capability 7.x）。

- 第一次 miss → L2 fetch（128B）→ 存入 L1
- 同一 warp 後續存取同一 cache line → L1 hit，免費
- 不同 warp 若存取相同 cache line（temporal reuse）也能受益

這代表：coalescing 的收益在「每條 cache line 的有效資料率」，L1 hit rate 是另一個維度的指標，兩者要一起看（Nsight Compute 的 `l1tex__t_sector_hit_rate` 和 `memory_l2_theoretical_sectors_global` 都要關注）。

---

## 18.4 矩陣轉置：標準教學範例

矩陣轉置是 coalescing 最經典的教學場景，因為它**幾乎不可能同時 coalesced 讀又 coalesced 寫**（如果直接操作 global memory 的話）。

### 18.4.1 問題設定

將 `N×N` 矩陣 A（row-major）轉置寫入矩陣 B（row-major）。

```
A[i][j] → B[j][i]
```

假設 `N=4096`，使用 `BLOCK_DIM=32` 的方形 tile。

### 18.4.2 Naive 做法的問題

```cpp
// naive_transpose.cu
__global__ void naiveTranspose(const float* __restrict__ A,
                                float* __restrict__ B,
                                int N) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;  // column of A
    int y = blockIdx.y * blockDim.y + threadIdx.y;  // row of A
    if (x < N && y < N) {
        B[x * N + y] = A[y * N + x];
    }
}
```

分析 warp 的存取模式（假設 `blockDim = (32, 32)`）：

```
同一 warp 內：threadIdx.x 從 0 到 31，threadIdx.y 相同（設 ty）

讀 A：A[ty * N + tx]，tx = 0..31 → 連續 → COALESCED ✓
寫 B：B[tx * N + ty]，tx = 0..31 → stride = N（跨 N 個 float）→ UN-COALESCED ✗
```

寫入方向是 column-major scatter：32 個 thread 各寫到 B 的不同 row（相距 `N * sizeof(float)` bytes），觸發最多 32 個 sector transaction。

### 18.4.3 Shared Memory Tiling 修法

核心思路：
1. **Coalesced 讀** A 的一個 tile 到 shared memory
2. `__syncthreads()`
3. **在 shared memory 裡做轉置**（改變讀取索引，無記憶體成本）
4. **Coalesced 寫** 到 B 的對應 tile

```
Tile 在 shared memory 的轉置：
讀進來：smem[ty][tx] = A[row][col]   → 按 row 讀 A（coalesced）
寫出去：B[row'][col'] = smem[tx][ty] → 同一 warp 的 tx 連續，smem[tx][ty] 的 ty 固定 tx 變
                                       但寫 B 時要讓 B 的 col 方向連續
```

完整 kernel（CUDA 12.x 正確語法）：

```cpp
// tiled_transpose.cu
// TILE_DIM 必須整除 N
#define TILE_DIM 32
#define BLOCK_ROWS 8   // 每個 block 有 32*8 = 256 threads；TILE_DIM*BLOCK_ROWS threads per block

// 讀 tile：32 threads per row，每 thread 負責 TILE_DIM/BLOCK_ROWS 個 row
// 寫 tile：同樣 pattern，但對 transposed 的位置寫出

__global__ void tiledTranspose(const float* __restrict__ A,
                                float* __restrict__ B,
                                int N) {
    // +1 padding 防止 bank conflict（Ch 19 詳述）
    __shared__ float smem[TILE_DIM][TILE_DIM + 1];

    int x = blockIdx.x * TILE_DIM + threadIdx.x;
    int y = blockIdx.y * TILE_DIM + threadIdx.y;

    // Phase 1：Coalesced 讀 A → smem
    // 一個 block 有 TILE_DIM * BLOCK_ROWS threads，需要走 TILE_DIM/BLOCK_ROWS 輪
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < N && (y + j) < N) {
            // 同一 warp 內 threadIdx.x 連續 → A[y+j][x] 的 x 方向連續 → COALESCED 讀
            smem[threadIdx.y + j][threadIdx.x] = A[(y + j) * N + x];
        }
    }

    __syncthreads();

    // 計算轉置後的輸出位置
    // 輸出 tile 的左上角：transposed block position
    x = blockIdx.y * TILE_DIM + threadIdx.x;  // 注意 blockIdx.y/x 互換
    y = blockIdx.x * TILE_DIM + threadIdx.y;

    // Phase 2：從 smem 讀出（轉置索引）→ Coalesced 寫 B
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < N && (y + j) < N) {
            // smem[threadIdx.x][threadIdx.y + j]：讀 smem 的 column 方向
            // 但寫入 B[(y+j)*N + x]，x 方向連續 → COALESCED 寫
            B[(y + j) * N + x] = smem[threadIdx.x][threadIdx.y + j];
        }
    }
}

// Host 端呼叫
void runTranspose(float* d_A, float* d_B, int N) {
    dim3 block(TILE_DIM, BLOCK_ROWS);
    dim3 grid(N / TILE_DIM, N / TILE_DIM);
    tiledTranspose<<<grid, block>>>(d_A, d_B, N);
}
```

### 18.4.4 逐步拆解 smem 轉置

讓我們用一個 4×4 的微型例子看清楚 index 的變換（TILE_DIM=4 僅作說明，實際用 32）：

```
A（row-major）:       目標 B（= A 的轉置）:
 0  1  2  3            0  4  8 12
 4  5  6  7            1  5  9 13
 8  9 10 11            2  6 10 14
12 13 14 15            3  7 11 15

Phase 1 讀到 smem（smem[ty][tx] = A[row][col]）:
smem:
 0  1  2  3
 4  5  6  7
 8  9 10 11
12 13 14 15
（smem 長得和 A 一樣）

Phase 2 寫出：B[(y+j)*N + x] = smem[tx][ty+j]
讀 smem[tx][ty]，tx 是 threadIdx.x（warp 內 0..3），ty 是 threadIdx.y（固定）
→ 讀 smem 的 column：smem[0][0], smem[1][0], smem[2][0], smem[3][0] = 0,4,8,12
→ 寫到 B[0][0..3] = [0,4,8,12]  ✓

這樣 B 的每一 row 都從 smem 的一個 column 填入，
smem 的 column read + B 的 row write = 兩端都 coalesced（smem 存取請見 Ch 19 bank 分析）
```

### 18.4.5 效能對比

| 做法 | 讀 sector tx | 寫 sector tx | 總 sector tx（N=4096） |
|------|-------------|-------------|------------------------|
| Naive | 4（coalesced）| 32（scatter）| 約 18× 理想值 |
| Tiled（+smem）| 4 | 4 | 約 1× 理想值 |

實際數字取決於 L2 hit rate 與 DRAM 排隊，但 Nsight Compute 的 `Memory Throughput` 指標在 tiled 版本通常能達到 T4 理論頻寬的 60–85%（理論預期；實測請在 Colab 用 Nsight Compute 驗證）。

---

## 18.5 AoS vs SoA

### 18.5.1 問題描述

假設我們有 N 個粒子，每個粒子有 `(x, y, z, mass)` 四個屬性。

**AoS（Array of Structures，結構陣列）**：

```cpp
struct Particle {
    float x, y, z, mass;
};
Particle particles[N];  // 每個 Particle 佔 16 bytes

// 在 kernel 裡只存取 x：
float xi = particles[tid].x;
```

分析：`particles[tid].x` 的 byte 位址 = `tid * 16 + 0`。

Warp 內 tid = 0..31 的 x 位址：
```
tid=0:  byte 0    (offset  0 in element 0)
tid=1:  byte 16   (offset  0 in element 1)
tid=2:  byte 32   (offset  0 in element 2)
...
tid=31: byte 496  (offset  0 in element 31)

範圍：byte 0 到 byte 499，stride = 16B（即 4 個 float）
Sector 數：ceil(500 / 32) = 16 sectors（最壞情況）
有效資料：32 * 4 = 128B
Bandwidth utilization：128B / 512B = 25%
```

**SoA（Structure of Arrays，陣列結構）**：

```cpp
struct ParticlesSoA {
    float* x;
    float* y;
    float* z;
    float* mass;
};
// 或分開：
float xs[N], ys[N], zs[N], masses[N];

// 在 kernel 裡只存取 x：
float xi = xs[tid];
```

分析：`xs[tid]` 的 byte 位址 = `tid * 4`。

Warp 內 tid = 0..31 的 x 位址：
```
tid=0:  byte 0
tid=1:  byte 4
...
tid=31: byte 124

範圍：byte 0 到 byte 127（正好 128B，4 sectors）
Bandwidth utilization：100% ✓
```

### 18.5.2 對比表格

| 面向 | AoS | SoA |
|------|-----|-----|
| 存取單一欄位（GPU kernel）| 差（stride = sizeof(struct)）| 佳（stride = 1）|
| 存取整個結構（CPU serial）| 佳（cache 友善）| 差（多個陣列分散）|
| 可讀性 | 直覺，近似物件導向 | 需要拆解，不直覺 |
| 欄位選擇性使用 | 仍搬入全部欄位（浪費 L1 空間）| 只搬需要的欄位（L1 使用率高）|
| 向量化存取（float4）| 可以，但載入整個 struct | 可以，獨立向量化每個欄位 |
| 典型使用情境 | CPU-side 資料管理 | GPU kernel 計算 |

**建議**：GPU kernel 幾乎都應該用 SoA 或 AoSoA（Array of Structures of Arrays，分 chunk 的混合形式）。

### 18.5.3 AoSoA（混合形式）

當每次 kernel 確實需要同時用到多個欄位（例如 SIMD 寬度的 chunk），可以用 AoSoA：

```cpp
// 以 8 為 chunk size（AVX2 width）
// GPU 用 32 或 16
#define CHUNK 32
struct ParticleChunk {
    float x[CHUNK];
    float y[CHUNK];
    float z[CHUNK];
    float mass[CHUNK];
};
ParticleChunk chunks[N / CHUNK];

// kernel 存取 chunks[tid/CHUNK].x[tid%CHUNK]
// 同一 warp 的 tid 若在同一 chunk → 連續存取 x[0..31] → coalesced
```

---

## 18.6 踩雷

### 18.6.1 Padding struct 讓 AoS 對齊但還是 stride-N

```cpp
struct __align__(16) Particle {
    float x, y, z, mass;
};
```

`__align__(16)` 確保每個 `Particle` 的起始位址 16-byte 對齊，對 CPU SIMD 有幫助。但 warp 存取 `particles[tid].x` 的 stride 仍然是 `sizeof(Particle) = 16` bytes = stride-4（以 float 算）。對齊解決不了 AoS 的 coalescing 問題。

### 18.6.2 二維陣列的 pitch

手動分配 `float A[M][N]` 然後傳指標給 kernel，如果 `N` 不是 32 的倍數（以 float 算，即 128 bytes 的倍數），每一 row 的結尾和下一 row 的開頭之間沒有對齊，導致跨 row 存取時每 row 的 coalescing 效率都有損耗。

正確做法：用 `cudaMallocPitch`：

```cpp
float* d_A;
size_t pitch;  // 實際每 row 的 byte 數（已 padding 到對齊邊界）
cudaMallocPitch(&d_A, &pitch, N * sizeof(float), M);

// kernel 內取元素：
float val = *((float*)((char*)d_A + row * pitch) + col);
// 或用 pitched pointer macro，等同上式
```

`pitch` 保證每 row 起始位址 aligned，讓每個 thread block 的第一個 row 存取都是 coalesced 的基礎。手動 padding 也可以，但要自己算 `paddedN = ((N + 31) / 32) * 32`。

### 18.6.3 Constant memory / texture 不套 coalescing 思路

`__constant__` memory 走獨立的 constant cache（8–64 KB），廣播給 warp 裡所有 thread 讀**同一位址**效率最高；若每個 thread 讀**不同位址**（序列化），效率極差。這和 global memory 的 coalescing（每個 thread 讀不同連續位址最佳）恰好相反。

Texture memory（`cudaTextureObject_t`）有 2D locality cache，優化的是 2D 空間鄰近性，不是 1D 連續性。把 texture 用在需要 coalesced 1D 存取的場景不一定有幫助，反而可能因為 cache 競爭而降效。

**原則**：global memory → 看 coalescing；constant memory → 看廣播 vs 序列化；texture → 看 2D locality。三種 cache 的最優存取模式不同，不要混用思路。

### 18.6.4 自然對齊要求

CUDA 要求所有資料型別「自然對齊」（natural alignment）：`float` 需要 4-byte 對齊，`float2` 需要 8-byte 對齊，`float4` 需要 16-byte 對齊。

常見違反：

```cpp
// 危險！如果 base 不是 16-byte 對齊，float4 存取是 undefined behavior
float4* ptr = (float4*)(some_float_ptr + 1);  // 位址 = base + 4，不是 16 的倍數
float4 val = *ptr;  // 可能 crash 或靜默給錯誤結果
```

`cudaMalloc` 保證 256-byte 對齊，所以 base 沒問題。問題在手算 offset 時沒有保持對齊。

### 18.6.5 誤把 `__ldg` 當作萬能解法

`__ldg`（load via read-only cache）繞過 L1 data cache，走獨立的 read-only cache（texture cache）。它的確可以在某些隨機存取模式下提升效能（因為 read-only cache 有不同的 eviction policy），但它**不能消除 un-coalesced 存取的 sector transaction 數量增加的問題**。

`__ldg` 的真正用途是：當資料確定在 kernel 執行期間不會被修改時，給 compiler 提示用 read-only cache，可以提高 cache 使用率（避免 coherence overhead）。不要把它當作「coalescing 修不好就用 __ldg 蒙混過去」的萬能藥。

---

## 18.7 進階技巧

### 18.7.1 `__ldg`：read-only cache 載入

```cpp
__global__ void kernel(const float* __restrict__ A, float* B, int N) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < N) {
        // 明確使用 read-only cache
        float val = __ldg(&A[tid]);
        B[tid] = val * 2.0f;
    }
}
```

`__restrict__` 關鍵字告訴 compiler 指標沒有別名，配合 `__ldg` 效果最好。CUDA 12 中，標記了 `__restrict__` 的 `const` 指標在某些情況下 compiler 會自動使用 `ldg` 指令，不需要手寫 `__ldg`。

使用時機：
- 資料在 kernel 執行中確定唯讀（look-up table、常數係數、只讀輸入陣列）
- 存取有一定的 reuse（read-only cache 能提升 hit rate）

### 18.7.2 float4 向量化存取

每個 thread 一次載入 4 個 float，等同每個 thread 消耗 16 bytes：

```cpp
__global__ void scaleFloat4(const float4* __restrict__ A,
                              float4* B,
                              int N4,     // N / 4
                              float scale) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < N4) {
        float4 val = A[tid];  // 一次 ld.global.v4.f32，16 bytes
        val.x *= scale;
        val.y *= scale;
        val.z *= scale;
        val.w *= scale;
        B[tid] = val;         // 一次 st.global.v4.f32
    }
}

// 呼叫：
// float* d_A, *d_B（cudaMalloc 保證對齊）
// scaleFloat4<<<(N/4+255)/256, 256>>>((float4*)d_A, (float4*)d_B, N/4, 2.0f);
```

效益：
- 同樣 32 thread warp，float 版載入 128B / warp，float4 版也是 128B / warp，**transaction 數不變**
- 真正收益在於：**減少 instruction count**（原本 32 個 ld，現在邏輯上還是 32 個但每個是 v4）和**降低 memory pipeline 壓力**，讓 scheduler 有更多空間排程其他 warp
- float4 對 store 的收益更明顯（合併 4 個 st 為 1 個 v4 st）

**前提**：base address 必須 16-byte 對齊，`cudaMalloc` 分配的滿足此要求。

### 18.7.3 向量化 + SoA 組合

```cpp
// SoA with float4 access：每個 thread 處理 4 個粒子的 x 座標
__global__ void updateX(const float4* __restrict__ xs,
                         float4* new_xs,
                         const float4* __restrict__ vxs,
                         float dt,
                         int N4) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < N4) {
        float4 x  = xs[tid];
        float4 vx = vxs[tid];
        x.x += vx.x * dt;
        x.y += vx.y * dt;
        x.z += vx.z * dt;
        x.w += vx.w * dt;
        new_xs[tid] = x;
    }
}
```

這是 particle system、fluid simulation 的標準 GPU 寫法：SoA 保證 coalescing，float4 降低 instruction overhead。

---

## 18.8 動手練習

**練習 A**：在 Colab 實測 naive vs tiled 矩陣轉置

1. 實作 `naiveTranspose` 和 `tiledTranspose`（使用本章程式碼）
2. 對 `N = 4096` 的 `float` 矩陣跑 100 次，取平均時間（用 `cudaEvent`）
3. 計算實際頻寬：`2 * N * N * sizeof(float) / time_ms / 1e6` GB/s（讀 + 寫各一次）
4. 對比 T4 的理論峰值頻寬 300 GB/s，算出效率

**練習 B**：用 Nsight Compute 量 sector transactions

```bash
# Colab 終端機（需要 T4 runtime）
ncu --metrics l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum,\
l1tex__t_sectors_pipe_lsu_mem_global_op_st.sum \
./transpose 4096
```

比較 naive 與 tiled 版本的 load/store sector 數，驗證本章分析是否吻合。

**練習 C**：AoS to SoA 改寫

把以下 AoS kernel 改成 SoA 版本並量測頻寬差異：

```cpp
struct Vec3 { float x, y, z; };
__global__ void normAoS(const Vec3* pts, float* norms, int N) {
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid < N) {
        float x = pts[tid].x;
        float y = pts[tid].y;
        float z = pts[tid].z;
        norms[tid] = sqrtf(x*x + y*y + z*z);
    }
}
```

預期：SoA 版本的 Nsight Compute `Memory Throughput` 應顯著高於 AoS 版（理論預期；實測請在 Colab 驗證）。

---

## 18.9 本章重點

- 一個 warp 的 32 thread 同時發出 load。Memory controller 以 **32-byte sector** 為單位服務；一條 L2 cache line = **128 bytes = 4 sectors**。
- **Coalesced**：32 thread 存取連續 128B（stride-1）→ 4 sector transactions，頻寬使用率 100%。
- **Un-coalesced**：stride-2 → ~8 sectors（50%）；隨機 → 最多 32 sectors（12.5%）。Mark Harris 的 NVIDIA Dev Blog 引用典型 GPU 上 strided 比 coalesced **慢 10x–18x**（理論預期，實測請在 Colab 用 Nsight Compute 驗證）。
- **矩陣轉置修法**：naive 轉置的寫是 scatter（un-coalesced）；用 shared memory tile 可以讓讀和寫都 coalesced，代價是一個 `__syncthreads()` 和 smem 空間。
- **AoS → SoA**：GPU kernel 幾乎一律用 SoA，只存取需要的欄位，頻寬使用率從 25% 提升到 100%（4 欄位 struct 情境）。
- **進階**：`__ldg` 用於確定唯讀的資料；float4 向量化存取降低 instruction count；兩者不能取代 coalescing 本身。
- **Nsight Compute 量化**：`l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum` 直接顯示 load 的 sector transaction 數；理想值 = `(存取 byte 數) / 32`。

---

## 18.10 自我檢核

1. 一個 warp 的 32 個 thread 各讀 `float arr[tid * 3]`（stride-3 存取），最多需要幾個 sector transaction？為什麼不是 32 個？

2. 矩陣轉置的 tiled 版本中，`smem[threadIdx.x][threadIdx.y + j]` 這個讀取是在 shared memory 裡做轉置，還是在 global memory 裡？為什麼這樣做能讓寫入 B 變成 coalesced？

3. AoS struct 有 6 個 float 欄位，warp 只需要第 3 個欄位（offset 8 bytes）。Bandwidth 浪費了多少百分比？如果改成 SoA，浪費是多少？

4. 有人說「只要用 `__ldg` 就不需要管 coalescing」，這個說法錯在哪裡？`__ldg` 真正的使用時機是什麼？

5. 用 `cudaMallocPitch` 分配的 2D 陣列，每 row 的 `pitch` 可能比 `N * sizeof(float)` 大。這個額外的 padding 有什麼作用？如果不 padding，哪種存取 pattern 的效能會變差？

---

## 18.11 延伸閱讀

1. **Mark Harris, "How to Access Global Memory Efficiently in CUDA C/C++"**
   - 位置：developer.nvidia.com Dev Blog
   - 學什麼：stride 存取的 10x–18x 效能差距實測數據；coalescing 規則的歷史沿革（Fermi → Kepler → Maxwell → Volta）
   - 前提：了解 warp 基本概念（[Ch 10](./10-warp-simt-execution.md)）

2. **Mark Harris, "An Efficient Matrix Transpose in CUDA C/C++"**
   - 位置：developer.nvidia.com Dev Blog
   - 學什麼：矩陣轉置從 naive 到 tiled 的完整演進；`+1` padding 消除 bank conflict 的原因（配合 [Ch 19](./19-bank-conflict.md)）；用 `cudaEventRecord` 精確量時間的方法
   - 前提：本章讀完

3. **CUDA C++ Programming Guide, Section "Device Memory Accesses"**
   - 位置：docs.nvidia.com/cuda/cuda-c-programming-guide，"Device Memory Accesses" 節
   - 學什麼：各 compute capability 世代的 sector size / cache line size 精確規格；`ld.global` / `st.global` 各 modifier（`.ca` / `.cg` / `.cs` / `.cv`）的語義
   - 前提：了解 sm_xx 編號意義

4. **CUDA C++ Best Practices Guide, Chapter "Memory Optimizations"**
   - 位置：docs.nvidia.com/cuda/cuda-c-best-practices-guide，"Memory Optimizations" 章
   - 學什麼：coalescing、pitched memory、texture / constant 使用時機的官方最佳實踐；涵蓋本章和 Ch 19–21 的系統性說明
   - 前提：本章 + [Ch 17](./17-shared-memory-tiling.md)

5. **Nsight Compute metric：`l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum`**
   - 位置：Nsight Compute UI → "Source" 頁或 `ncu --query-metrics | grep sector`；官方說明在 docs.nvidia.com/nsight-compute/ProfilingGuide，"Memory Workload Analysis" 節
   - 學什麼：如何解讀 load/store 的 sector 數，換算出「實際 vs 理想 sector 比」（越接近 1 越好）；搭配 `l1tex__t_requests_pipe_lsu_mem_global_op_ld.sum` 算出每個 request 平均幾個 sector
   - 前提：會在 Colab 執行 `ncu`（需要 `!pip install` 或直接用 `/usr/local/cuda/bin/ncu`）

---

→ [Ch 19 bank conflict 深挖](./19-bank-conflict.md)

矩陣轉置的 `smem[TILE_DIM][TILE_DIM + 1]` 裡那個神秘的 `+1` 是怎麼回事？上面 kernel 裡故意留著它但沒解釋——那是消除 shared memory bank conflict 的關鍵，Ch 19 完整拆解。
