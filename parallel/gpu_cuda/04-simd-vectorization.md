# Ch 4 — SIMD 向量化：AVX2 intrinsics vs 自動向量化

> **目標**：掌握 SIMD 的直覺和工具——從自動向量化的編譯器報告讀起，到手寫 AVX2 intrinsics；知道什麼時候自動向量化會失敗（aliasing / 對齊 / 非連續存取），以及如何修正；在這台機器上量測純量 vs 向量化的真實加速比。最後把 SIMD 和 GPU 的 SIMT 掛鉤：warp 就是放大版的 SIMD。

> **環境**：gcc 14 + AVX2 + FMA, x86-64 Windows（MSYS2）  
> **編譯方式**：`gcc -O2 -mavx2 -mfma foo.c -o foo.exe`

從 Ch 3 你知道 SIMD 是資料級平行（DLP），一條指令同時處理多個資料元素。這章把它變成你可以實際操作的工具。

---

## 一、SIMD 的直覺：一條指令，多個資料

最古老的 x86 SIMD 是 1996 年的 MMX（64-bit，整數）。現在的時間線：

```
1996  MMX        64-bit   整數
1999  SSE        128-bit  4×float
2001  SSE2       128-bit  2×double, 16×int8
2004  SSE3       128-bit  水平加法
2007  SSE4       128-bit  更多整數操作
2011  AVX        256-bit  8×float / 4×double
2013  AVX2       256-bit  整數擴展到 256-bit  ← 本章主角
2017  AVX-512    512-bit  16×float（需要 AVX-512 CPU）
```

這台機器支援 AVX2（Intel i7-10700）。確認指令：

```bash
grep -m1 flags /proc/cpuinfo | grep -o 'avx2'
# 或
gcc -march=native -Q --help=target | grep enabled | grep avx2
```

### 一個圖說清楚

純量（scalar）加法：
```
一次做 1 個 float：
  a[0] + b[0] = c[0]
```

AVX2 `_mm256_add_ps`：
```
一次做 8 個 float（256 bit / 32 bit per float = 8）：
  [a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7]]
+ [b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7]]
= [c[0],c[1],c[2],c[3],c[4],c[5],c[6],c[7]]

時間：和一次純量加法相同的時鐘週期數
```

理論加速：8x（float）或 4x（double）。

實際上能到多少，取決於是否被記憶體帶寬限制（memory-bound），以及有沒有水平 reduction 開銷。

---

## 二、自動向量化：讓編譯器來做

最簡單的入場方式：讓 gcc 自動把你的迴圈向量化。

```c
// autovec.c
float a[1024], b[1024], c[1024];

void add_arrays(int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] + b[i];
    }
}
```

```bash
gcc -O3 -march=native -fopt-info-vec autovec.c -o autovec.exe
```

**實際編譯輸出**（這台機器）：
```
autovec.c:7:23: optimized: loop vectorized using 32 byte vectors
autovec.c:7:23: optimized: loop vectorized using 16 byte vectors
autovec.c:7:23: optimized: loop vectorized using 32 byte vectors
```

編譯器說「using 32 byte vectors」就是 AVX2（256-bit = 32 bytes）。為什麼有多條訊息？gcc 會產生多個版本：
1. 主要路徑：AVX2（處理每次 8 個 float）
2. 清尾路徑：SSE（128-bit，處理剩餘不足 8 個的部分）
3. Scalar fallback（如果執行時對齊不對）

`-fopt-info-vec` 告訴你成功向量化的迴圈。如果想看**失敗的原因**，用：
```bash
gcc -O3 -march=native -fopt-info-vec-missed autovec.c -o autovec.exe 2>&1 | head -30
```

### 自動向量化需要滿足什麼條件

1. **迴圈是計數迴圈**（有已知的 trip count，或至少能靜態分析）
2. **迭代之間獨立**（i+1 不依賴 i 的結果）
3. **記憶體存取連續**（stride-1）
4. **沒有 aliasing**（見下節）

---

## 三、aliasing 阻礙向量化

這是最常見的失敗原因之一。

```c
// 編譯器不確定 a 和 c 是否指向同一塊記憶體
void add_alias(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] + b[i];  // 如果 c == a+1，向量化會得錯誤結果
    }
}
```

如果 `c` 和 `a` 重疊（例如 `c = a + 1`），向量化版本的行為和純量不同——它會讀入還沒被覆蓋的舊值。所以編譯器要嘛不向量化，要嘛插入執行時 overlap 檢查（稱為「loop versioning」）。

實際確認（`-fopt-info-vec` 的輸出）：
```
autovec.c:5:23: optimized: loop vectorized using 32 byte vectors
autovec.c:5:23: optimized:  loop versioned for vectorization because of possible aliasing
```

看到「loop versioned for vectorization because of possible aliasing」就代表編譯器不確定，插入了 runtime check，有一定的 overhead。

**修正方法**：加 `__restrict__` 告訴編譯器「我保證這些指標不重疊」：

```c
void add_restrict(float * __restrict__ a,
                  float * __restrict__ b,
                  float * __restrict__ c, int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] + b[i];
    }
}
```

加了 `__restrict__` 後，編譯器直接選最快路徑，省掉 runtime check。測這台機器：

```
no __restrict__ : time=0.0242 s
__restrict__    : time=0.0222 s   (約 8% 差異)
```

小陣列差異不明顯，大陣列 + 多次重複調用就會累積。

---

## 四、對齊（Alignment）對向量化的影響

AVX2 指令有兩種 load 形式：
- `_mm256_load_ps(p)`：要求 `p` 對齊到 32 bytes，若未對齊 → segfault 或 GP fault
- `_mm256_loadu_ps(p)`：不要求對齊（unaligned），效能略差（現代 CPU 差距很小）

自動向量化生成的程式碼通常選 `loadu` 並在迴圈前插入 peel 處理對齊問題。

明確要求 32-byte 對齊分配（最安全的方式）：

```c
// 使用 _mm_malloc（_mm_free 配對）
float *a = (float*)_mm_malloc(N * sizeof(float), 32);
float *b = (float*)_mm_malloc(N * sizeof(float), 32);
// ... 使用 ...
_mm_free(a);
_mm_free(b);
```

---

## 五、手寫 AVX2 Intrinsics：以 dot product 為例

有時自動向量化做不到，或你想確保向量化一定發生，或需要 FMA（fused multiply-add）——這時要手寫 intrinsics。

### Intel Intrinsics Guide

所有 AVX2 intrinsic 的參考：[intel.com/content/www/us/en/docs/intrinsics-guide/](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/)

命名規則：`_mm<width>_<op>_<type>`

```
_mm256_add_ps    → 256-bit, add, packed single (float)
_mm256_mul_pd    → 256-bit, multiply, packed double
_mm256_fmadd_ps  → 256-bit, fused multiply-add, packed single
_mm_hadd_ps      → 128-bit, horizontal add, packed single
```

### 核心資料型別

```c
#include <immintrin.h>

__m256   // 8 × float  (256-bit)
__m256d  // 4 × double (256-bit)
__m256i  // 256-bit integer (8×int32, 16×int16, etc.)
__m128   // 4 × float  (128-bit, SSE)
```

### 完整的 dot product 實作

純量版本：

```c
float dot_scalar(const float *a, const float *b, int n) {
    float sum = 0.0f;
    for (int i = 0; i < n; i++) sum += a[i] * b[i];
    return sum;
}
```

AVX2 手寫版本（含詳細說明）：

```c
#include <immintrin.h>

float dot_avx2(const float *a, const float *b, int n) {
    __m256 vsum = _mm256_setzero_ps();  // vsum = {0,0,0,0,0,0,0,0}
    int i;
    for (i = 0; i + 7 < n; i += 8) {
        __m256 va = _mm256_loadu_ps(a + i);  // 載入 a[i..i+7]
        __m256 vb = _mm256_loadu_ps(b + i);  // 載入 b[i..i+7]
        // vsum += va * vb（FMA：一條指令，比分開的 mul+add 快且精確）
        vsum = _mm256_fmadd_ps(va, vb, vsum);
    }

    // 水平求和：把 __m256 的 8 個 lane 加成 1 個 float
    //   [s0,s1,s2,s3,s4,s5,s6,s7] → s0+s1+...+s7
    __m128 lo = _mm256_castps256_ps128(vsum);      // 取低 128-bit [s0,s1,s2,s3]
    __m128 hi = _mm256_extractf128_ps(vsum, 1);   // 取高 128-bit [s4,s5,s6,s7]
    __m128 s  = _mm_add_ps(lo, hi);               // [s0+s4, s1+s5, s2+s6, s3+s7]
    s = _mm_hadd_ps(s, s);                         // [s0+s4+s1+s5, s2+s6+s3+s7, ...]
    s = _mm_hadd_ps(s, s);                         // [total, total, ...]
    float result = _mm_cvtss_f32(s);               // 取出 lane 0

    // 收尾：處理 n 不是 8 的倍數的部分
    for (; i < n; i++) result += a[i] * b[i];
    return result;
}
```

水平求和的 ASCII 圖：

```
vsum = [s0, s1, s2, s3, s4, s5, s6, s7]
         ╔═════╗           ╔═════╗
lo  = [s0, s1, s2, s3]    hi = [s4, s5, s6, s7]
         ╚═════╝           ╚═════╝
          _mm_add_ps(lo, hi)
s   = [s0+s4, s1+s5, s2+s6, s3+s7]
          _mm_hadd_ps(s, s)
s   = [s0+s4+s1+s5, s2+s6+s3+s7, ...]
          _mm_hadd_ps(s, s)
s   = [total, total, total, total]
          _mm_cvtss_f32(s)
result = total
```

### 實測加速比（16M float，這台機器）

```bash
gcc -O2 -mavx2 -mfma simd_demo.c -o simd_demo.exe && ./simd_demo.exe
```

**真實輸出**：
```
scalar : result=8373823.0000  time=0.0174 s
AVX2   : result=8380134.5000  time=0.0061 s  speedup=2.84x
```

理論是 8x，實際只有 2.84x——因為這個 workload 是 memory-bound（16M float = 64MB，超出 L3 cache，帶寬是瓶頸），不是 compute-bound。

注意 `result` 有細微差異（8373823 vs 8380134）——這是浮點結合律不同步的正常現象，不是 bug。

### 另一個測試：小陣列（fit in cache，compute-bound）

這台機器使用 N = 8M（fit in L3），重複 10 次，取平均：

```
naive (1T)       : r=15.403683  time=0.0083 s
AVX2 (1T)        : r=16.601738  time=0.0034 s  speedup=2.43x
OpenMP (16T)     : r=16.509283  time=0.0029 s  speedup=2.85x
AVX2+OpenMP(16T) : r=16.519384  time=0.0026 s  speedup=3.16x
```

即使 fit in cache，加速還是沒到理論 8x——dot product 的 FMA 計算密度有限，帶寬仍是主因。真正能看到接近 8x 的場景是計算密集型（如矩陣乘法，每個 float 被重複使用多次）。

---

## 六、FMA（Fused Multiply-Add）

FMA 是 AVX2 引入的關鍵指令：

```
一般做法：  result = (a * b) + c   → 兩條指令 + 中間結果舍入一次
FMA：       result = fmadd(a, b, c) → 一條指令，中間結果不舍入

精度更高：  中間乘積以完整精度保留
吞吐量更高：一條指令替代兩條
```

幾個 FMA variants：
```c
_mm256_fmadd_ps(a, b, c)   // a*b + c
_mm256_fmsub_ps(a, b, c)   // a*b - c
_mm256_fnmadd_ps(a, b, c)  // -(a*b) + c
```

需要加 `-mfma` 編譯器選項。

---

## 七、向量化失敗的常見原因

除了 aliasing，還有：

**非連續存取（stride > 1）**
```c
// 每次跳 2：stride-2，編譯器通常不向量化
for (int i = 0; i < n; i += 2) {
    c[i] = a[i] + b[i];
}
```
解法：重新排列資料（AoS → SoA）。

**結構陣列（Array of Structs）vs 結構陣列（Struct of Arrays）**
```c
// AoS (Array of Structs) → SIMD 很難
struct Point { float x, y, z; };
Point pts[N];  // 記憶體：x y z x y z x y z...
// 向量化 x 需要 stride-3 gather

// SoA (Struct of Arrays) → SIMD 友善
float xs[N], ys[N], zs[N];  // 記憶體：x x x... y y y... z z z...
// 向量化 x：stride-1，完美
```

**if-else 在迴圈內**
```c
for (int i = 0; i < n; i++) {
    if (a[i] > 0) c[i] = a[i];  // branch
    else c[i] = 0;
}
```
解法：用 blend/mask 指令取代 branch：
```c
for (int i = 0; i < n; i += 8) {
    __m256 va = _mm256_loadu_ps(a + i);
    __m256 zero = _mm256_setzero_ps();
    // 比較：產生 mask（全 1 表示 > 0，全 0 表示 <= 0）
    __m256 mask = _mm256_cmp_ps(va, zero, _CMP_GT_OS);
    // blend：mask 為 1 選 va，mask 為 0 選 zero
    __m256 vc = _mm256_blendv_ps(zero, va, mask);
    _mm256_storeu_ps(c + i, vc);
}
```

**迴圈攜帶依賴（loop-carried dependency）**
```c
// 每次迭代依賴前一次結果
c[i] = c[i-1] + a[i];  // 無法向量化（前向依賴）
```
除非是特定模式（如 prefix sum），否則無法向量化。

---

## 八、自動向量化 vs 手寫 Intrinsics：什麼時候選哪個

| 場景 | 選哪個 | 原因 |
|------|--------|------|
| 簡單的 element-wise 運算 | 自動向量化 | 編譯器幾乎都能做，加 `__restrict__` + `-O3 -march=native` |
| 需要 FMA、blend、gather | 手寫 | 自動向量化不一定生成這些指令 |
| 水平操作（hadd、reduction） | 手寫 | 自動向量化幾乎不做跨 lane 操作 |
| 跨平台移植（ARM / x86） | 自動向量化 | 手寫 intrinsics 是 x86 專有，移植麻煩 |
| 效能 critical path，有 profiling 依據 | 手寫 | 確定收益後才值得維護代價 |
| 非連續記憶體存取 | 手寫（gather/scatter） | `_mm256_i32gather_ps` 等 |

**原則**：先讓編譯器自動做，用 `-fopt-info-vec` 確認成功，用 benchmark 量測；只有在確認收益且自動向量化達不到時，才手寫 intrinsics。手寫的維護成本高、可讀性差、移植麻煩。

---

## 九、SIMD → GPU SIMT：這個章節的真正目的

SIMD 和 GPU 的 SIMT（Single Instruction, Multiple Threads）在概念上是同構的：

```
CPU SIMD (AVX2):
  1 條指令 × 8 個 lane × 1 個執行單元
  → 8 個 float 同時被處理

GPU SIMT (warp):
  1 條指令 × 32 個 lane × 1 個 warp scheduler
  → 32 個 thread 同時執行同一條指令
```

差別在：
- AVX2：8 個 lane 的資料型別是固定的（`__m256`）
- SIMT：32 個 lane 看起來是獨立的 thread，各有自己的 register，但執行的是同一條指令

理解了 SIMD 的 lane、mask、水平操作後，理解 GPU 的 warp divergence（Part 2 Ch 10）就容易很多——warp divergence 就是 SIMD mask 的 GPU 版本，當部分 lane 不執行時，那些執行單元就空著。

---

## 踩雷集錦

**1. `_mm256_load_ps` 但記憶體沒對齊**

對未對齊記憶體用 `_mm256_load_ps`（aligned load）→ 在 AVX 上會 segfault 或 general protection fault。解法：要嘛用 `_mm256_loadu_ps`，要嘛確保用 `_mm_malloc(size, 32)` 分配。

**2. 忘記包含 `<immintrin.h>`**

所有 x86 SIMD intrinsics 都在這個 header。忘記加 → `'__m256' undeclared` 等錯誤。

**3. 水平 reduction 寫錯**

`_mm_hadd_ps(a, b)` 的語意：
```
輸入：a = [a0,a1,a2,a3]  b = [b0,b1,b2,b3]
輸出：     [a0+a1, a2+a3, b0+b1, b2+b3]
```
不是把 a 的全部加起來——需要兩次 `hadd` 才能把 4 個元素全部加成一個。這是最常弄錯的地方。

**4. 忘記 `-mfma` 就用 `_mm256_fmadd_ps`**

`_mm256_fmadd_ps` 需要 `-mfma` 編譯器選項；`-mavx2` 不夠。可以一起加：`-mavx2 -mfma`，或直接用 `-march=native`（自動包含所有當前 CPU 支援的擴展）。

**5. 浮點結果和純量版本不一樣就以為有 bug**

向量化改變了浮點運算的結合順序，結果可能有細微差異（在最後幾個有效數字）。這是正常的浮點行為，不是 bug。如果結果差很多，才要懷疑邏輯錯誤。

---

## 動手練習

1. 修改本章的 `dot_avx2`，改用 4 路 unrolled AVX2（每次處理 4 × 8 = 32 個 float）。量測相比單路 AVX2 的差距。
2. 實作 AVX2 版的 SAXPY（`y[i] = a * x[i] + y[i]`）並和 scalar 版本比較。注意 SAXPY 是 memory-bound，對比 dot product 的行為有何不同。
3. 用 `-fopt-info-vec-missed` 找出你自己寫的一個迴圈無法被向量化的原因，修正它。

---

## 本章重點

- AVX2 一次處理 8 × float（256-bit），理論 8x 加速，實際受記憶體帶寬限制。
- 自動向量化需要：連續記憶體存取 + 無 aliasing + 迭代獨立。加 `__restrict__` + `-O3 -march=native` + `-fopt-info-vec` 確認。
- `_mm256_fmadd_ps` 是 dot product / GEMM 的核心。需要 `-mavx2 -mfma`。
- 水平 reduction（`hadd` × 2 + extract）是向量化 dot product 的難點。
- GPU 的 warp = 32-lane SIMD；理解了 AVX2 mask/blend，就理解了 warp divergence 的本質。
- 這台機器（i7-10700, 16M float）：scalar 17ms → AVX2 6ms，speedup 2.84x（memory-bound）。

---

## 自我檢核

1. `_mm256_add_ps` 和 `_mm256_add_pd` 各處理多少個元素？為什麼是不同數目？
2. 什麼叫 aliasing？為什麼它阻礙向量化？`__restrict__` 怎麼幫助？
3. 畫出 `_mm256_fmadd_ps(a, b, c)` 的操作圖（輸入 3 個 `__m256`，輸出 1 個 `__m256`）。
4. 水平求和（把 `__m256` 的 8 個 lane 加成一個 float）需要哪幾步？為什麼自動向量化通常不做這個？
5. 為什麼 dot product 的 AVX2 加速只有 2.84x 而不是理論 8x？在什麼條件下能接近 8x？

---

## 延伸閱讀

1. **Intel Intrinsics Guide** — [intel.com/content/www/us/en/docs/intrinsics-guide/](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/)  
   讀哪裡：搜尋你要用的指令名稱，看 operation description 和 performance data（latency / throughput）。前提：無。關聯：本章所有 intrinsics 的權威參考，要查什麼 intrinsic 做什麼事必看這裡。

2. **Agner Fog, "Optimizing subroutines in assembly language"** (agner.org/optimize)  
   讀哪裡：Chapter 12 「Using SIMD (vector) instructions」。前提：對彙編有基本概念。關聯：比 Intel 文件更有 tutorial 色彩，有很多手寫向量化的完整範例。

3. **Agner Fog, Instruction tables** (agner.org/optimize/instruction_tables.pdf)  
   讀哪裡：找你的 CPU 微架構（如 Icelake, Alderlake），查各 SIMD 指令的 latency 和 throughput。前提：知道 latency vs throughput 的差別。關聯：本章量測加速比的硬體背景，解釋為什麼 FMA 比分開的 mul+add 快。

4. **GCC Auto-Vectorization documentation**  
   `gcc.gnu.org/projects/tree-ssa/vectorization.html`  
   讀哪裡：「Auto-vectorization in GCC」整篇，特別是「What prevents vectorization」章節。前提：懂 C，用過 gcc。關聯：理解 `-fopt-info-vec-missed` 報告的含義，找自動向量化失敗的根本原因。

5. **"What Every Programmer Should Know About Memory" (Ulrich Drepper)**  
   讀哪裡：Part 6 「What Programmers Can Do」，Section 6.2 「Vectorization」。前提：讀過本文前半。關聯：從記憶體階層角度解釋 SIMD 為什麼在 memory-bound workload 上有限——本章數字的理論基礎。

---

現在你有了 SIMD 工具，但單核 SIMD 只能吃到一個核心。下一章把多核利用起來，同時也要面對多核心帶來的麻煩：race condition 和 false sharing。

→ [Ch 5 多執行緒：OpenMP / pthreads / race / false sharing](./05-multithreading-openmp.md)
