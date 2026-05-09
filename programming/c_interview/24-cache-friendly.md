# Ch 24 — Cache 友善程式設計

> 目標：理解 CPU cache 的工作原理，能識別 cache-unfriendly 的程式碼，並用 data layout 和 access pattern 優化。

## CPU Cache 架構

```
CPU Cores
├── Core 0
│   ├── L1 I-cache  32 KB  ~4 cycles
│   ├── L1 D-cache  32 KB  ~4 cycles
│   └── L2 cache   256 KB  ~12 cycles
└── Core 1
    ├── L1 I-cache  32 KB
    ├── L1 D-cache  32 KB
    └── L2 cache   256 KB
Shared L3 cache     8 MB    ~40 cycles
DRAM                        ~200 cycles
```

**Cache line**：L1/L2/L3 的最小傳輸單位，通常 **64 bytes**。存取任何一個 byte，整個 64-byte 的 cache line 都被載入。

---

## 空間局部性（Spatial Locality）

存取連續的記憶體比跳躍存取快，因為一次載入整個 cache line：

```c
// 差：column-major 走法（跳行）
for (int j = 0; j < N; j++)         // 外層 column
    for (int i = 0; i < N; i++)
        sum += mat[i][j];            // mat[i][j] 不連續，每次可能 cache miss

// 好：row-major 走法（連續）
for (int i = 0; i < N; i++)         // 外層 row
    for (int j = 0; j < N; j++)
        sum += mat[i][j];            // mat[i][j] 連續，大多數命中 cache
```

**實測差異**：N=1024 的矩陣，row-major 可能比 column-major 快 3–10 倍。

---

## 結構體佈局：AoS vs SoA

```c
// AoS（Array of Structures）：
typedef struct { float x, y, z, w; } Vec4;
Vec4 particles[10000];

// 若只需要 x, y（用於計算），每次存取 Vec4 的 x 也載入了 z, w（浪費 cache）

// SoA（Structure of Arrays）：
typedef struct {
    float x[10000];
    float y[10000];
    float z[10000];
    float w[10000];
} ParticleArray;
ParticleArray particles;

// 存取所有 x 值：particles.x[i], particles.x[i+1] ... 完全連續
// SIMD 也更容易（可以一次處理 4/8 個 x 值）
```

AoS 適合需要同時存取一個物件所有欄位的場景；SoA 適合對同一欄位批量處理的場景（大量粒子系統、資料庫 column store）。

---

## False Sharing

多核心程式的重大效能殺手：兩個 core 的資料在**同一個 cache line**，即使存取不同變數也會互相 invalidate：

```c
// 差：counter0 和 counter1 可能在同一個 cache line
struct {
    atomic_int counter0;   // 4 bytes
    atomic_int counter1;   // 4 bytes  ← 同一個 cache line！
} counters;

// Thread 0 寫 counter0，Thread 1 寫 counter1
// → 每次寫入都使另一個 core 的 cache line 失效（false sharing）

// 修正：padding 讓每個 counter 佔滿一個 cache line
struct {
    atomic_int counter0;
    char pad0[64 - sizeof(atomic_int)];   // 填到 64 bytes
    atomic_int counter1;
    char pad1[64 - sizeof(atomic_int)];
} counters;

// 或用 GCC 的對齊：
struct __attribute__((aligned(64))) Counter {
    atomic_int value;
};
struct Counter c0, c1;   // 各佔獨立的 cache line
```

---

## 時間局部性（Temporal Locality）

最近存取過的資料，很快再次存取的機率高。Loop tiling / cache blocking 把工作分成 cache-sized 的小塊：

```c
// 矩陣乘法 C = A * B，naive 版本：
for (int i = 0; i < N; i++)
    for (int j = 0; j < N; j++)
        for (int k = 0; k < N; k++)
            C[i][j] += A[i][k] * B[k][j];   // B 的存取是 column-major → cache miss

// Cache blocking（Tile Size = BS）：
#define BS 64   // 調整 BS 讓 A/B/C 的 tile 合起來進入 L1 cache
for (int ii = 0; ii < N; ii += BS)
    for (int jj = 0; jj < N; jj += BS)
        for (int kk = 0; kk < N; kk += BS)
            for (int i = ii; i < ii+BS && i < N; i++)
                for (int j = jj; j < jj+BS && j < N; j++)
                    for (int k = kk; k < kk+BS && k < N; k++)
                        C[i][j] += A[i][k] * B[k][j];
```

---

## Prefetch Hint

在資料被需要前先發出載入請求：

```c
// GCC builtin prefetch：
for (int i = 0; i < N; i++) {
    __builtin_prefetch(&arr[i + 16], 0, 1);  // 預取 16 步後的資料
    process(arr[i]);
}
// 第一參數：地址；第二參數：0=read, 1=write；第三參數：locality hint 0~3
```

現代 CPU 的 hardware prefetcher 對 sequential access 效果很好，通常不需要手動 prefetch。

---

## 效能量測

```bash
# Linux perf 統計 cache miss：
perf stat -e cache-misses,cache-references,L1-dcache-load-misses ./prog

# Valgrind Cachegrind（模擬 cache 行為）：
valgrind --tool=cachegrind ./prog
cg_annotate cachegrind.out.*   # 按行顯示 cache miss 數
```

---

## 實測：排序後的搜尋比未排序快

```c
// 著名的 CPU branch predictor 範例：
// 排序後，分支更可預測
for (int i = 0; i < N; i++)
    if (data[i] >= 128) sum += data[i];

// 未排序：~6ns/elem（分支預測頻繁失敗）
// 已排序：~2ns/elem（分支預測準確）
// 這個例子說明 branch predictor 和 cache 都影響效能
```

---

## 自我檢核

- [ ] 能說出 cache line 的大小（64 bytes）和其意義
- [ ] 知道 row-major vs column-major 存取的效能差異原因
- [ ] 能解釋 false sharing 並說出修正方法（padding 到 64 bytes）
- [ ] 知道 AoS vs SoA 各自的適用場景

→ [Ch 25 SIMD 與向量化](./25-simd-vectorization.md)
