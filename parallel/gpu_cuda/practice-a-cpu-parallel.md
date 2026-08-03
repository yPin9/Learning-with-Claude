# 練習 A — CPU 平行：OpenMP + AVX2 優化 SAXPY 與 Dot Product

> **前提章節**：Ch 3（平行概念）、Ch 4（SIMD）、Ch 5（OpenMP）、Ch 6（parallel patterns）  
> **環境**：gcc 14 + OpenMP + AVX2 + FMA，x86-64 Windows（MSYS2）或 Linux  
> **編譯**：`gcc -O2 -mavx2 -mfma -fopenmp solution.c -o solution.exe -lm`  
> **預計時間**：2–3 小時（不含延伸挑戰）

---

## 背景與動機

SAXPY（Single-precision A·X Plus Y）和 Dot Product 是 BLAS Level 1 的兩個最基本運算。它們在機器學習（SGD 更新）、信號處理（FIR 濾波器）、線性方程求解器裡無處不在。

更重要的是：這兩個操作都是**記憶體帶寬限制（memory-bound）**的典型代表，優化它們能讓你親身體驗「計算量翻倍但時間沒變」的挫折感，以及理解 Roofline（Ch 2）在現實中的意義。

你要同時扮演三個角色：
1. 先寫出正確的 naive 實作
2. 用 OpenMP 加多核
3. 用 AVX2 手寫向量化
4. 用 Roofline 分析為什麼加速有限

這正是之後 CUDA kernel 優化的思考框架——只是執行單位從 CPU core 換成 GPU SM、從 AVX2 lane 換成 warp lane。

---

## 任務規格

### 資料規模

- 主測試：`N = 1 << 26`（64M floats，每個陣列 256 MB）
- 小驗證：`N = 1024`（方便手算驗正）
- 執行緒：這台機器 16 threads（`omp_get_max_threads()`）

### 要實作的六個函式

**SAXPY**（`y[i] += a * x[i]`）：

| 函式名 | 要求 |
|--------|------|
| `saxpy_naive` | 單核心，純量，C for 迴圈 |
| `saxpy_omp` | OpenMP `parallel for`，讓編譯器自動向量化 |
| `saxpy_avx2_omp` | OpenMP + 手寫 `_mm256_fmadd_ps` |

**Dot Product**（`sum(a[i] * b[i])`）：

| 函式名 | 要求 |
|--------|------|
| `dot_naive` | 單核心，純量 |
| `dot_omp` | OpenMP `reduction(+:s)` |
| `dot_avx2_omp` | 每個 thread 用 AVX2 做 vector accumulate，再 `reduction` 彙總 |

### 驗正要求

- `saxpy_omp` 和 `saxpy_avx2_omp` 的結果必須和 `saxpy_naive` 在每個元素的絕對誤差 < `1e-4f`
- Dot product：`dot_omp` 和 `dot_avx2_omp` 的結果彼此誤差 < `1e-4f`（注意：`dot_naive` 在大 N 會有精度差異，這是預期行為，見踩雷 #3）

### 計時與輸出格式

每個函式至少跑 3 次，取最快的一次（排除啟動 overhead 和 cache 冷啟動）。輸出格式參考：

```
=== 練習 A 參考解答 ===
N=67108864 (67 M floats, 268 MB per array)
threads=16, REPS=3 (取最快)

--- SAXPY: y[i] = 2.5*x[i] + y[i], N=64M ---
  naive (1T, scalar)        time=X.XXXX s
  OpenMP (16T, auto-vec)    time=X.XXXX s
  AVX2+OpenMP (16T)         time=X.XXXX s
  speedup OMP:      X.XXx
  speedup AVX2+OMP: X.XXx
  verify: OK

--- Dot Product: sum(a[i]*b[i]), N=64M ---
  ...
  speedup AVX2+OMP: X.XXx
  (dot product: memory-bound，加速比受帶寬限制)

--- 記憶體帶寬分析 (SAXPY) ---
  naive : XX.X GB/s
  AVX2+OMP: XX.X GB/s
  (理論峰值帶寬約 40 GB/s，i7-10700)
```

---

## 期望輸出範例

**這台機器（i7-10700, 16T, 64M float, REPS=3 取最快）的真實輸出**：

```
=== 練習 A 參考解答 ===
N=67108864 (67 M floats, 268 MB per array)
threads=16, REPS=3 (取最快)

--- SAXPY: y[i] = 2.5*x[i] + y[i], N=64M ---
  naive (1T, scalar)        time=0.0397 s
  OpenMP (16T, auto-vec)    time=0.0350 s
  AVX2+OpenMP (16T)         time=0.0344 s
  speedup OMP:      1.13x
  speedup AVX2+OMP: 1.16x
  verify: OK

--- Dot Product: sum(a[i]*b[i]), N=64M ---
  naive (1T, scalar)        time=0.0679 s
  OpenMP reduction (16T)    time=0.0197 s
  AVX2+OpenMP (16T)         time=0.0199 s
  result: naive=16777216.0000  omp=33520696.0000  avx2=33520696.0000
  speedup OMP:      3.45x
  speedup AVX2+OMP: 3.42x
  (dot product: memory-bound，加速比受帶寬限制)

--- 記憶體帶寬分析 (SAXPY) ---
  naive : 20.3 GB/s
  AVX2+OMP: 23.4 GB/s
  (理論峰值帶寬約 40 GB/s，i7-10700)
```

注意：naive dot product 的結果是 `16777216`，不是正確答案 `33520696`。這不是你的 bug——這是 float 精度問題，見踩雷 #3。

---

## 如果卡住了

**提示 1：AVX2 編譯錯誤**  
確認編譯指令包含 `-mavx2 -mfma`，並 `#include <immintrin.h>`。`_mm256_fmadd_ps` 需要 FMA，只有 `-mavx2` 不夠。

**提示 2：`saxpy_avx2_omp` 的 OpenMP + intrinsics 組合**  
`#pragma omp parallel for` 裡面可以直接用 AVX2 intrinsics：pragma 控制迴圈分配，intrinsics 控制每次迭代做什麼。每個 thread 處理自己的一段 `[lo, hi)` 的 `i`，各自用 `_mm256_fmadd_ps` 處理 8 個 float。

**提示 3：dot product AVX2 水平 reduction 的順序**  
`_mm_hadd_ps(a, b)` 的輸出是 `[a0+a1, a2+a3, b0+b1, b2+b3]`——不是把 a 的全部加起來。需要對同一個 `__m128` 呼叫兩次 `hadd`，才能把 4 個元素折疊成 1 個。見 Ch 4 的圖解。

---

## 5 步實作建議

**Step 1：寫對 naive 版本，N=1024 先驗正**

用小 N 先確認算法邏輯正確（可以手算 `a[0]*b[0] + ...` 或和 stdlib 結果比較）。不要在 64M 的情況下 debug 算法。

```c
// 先把 N 設小
#define N 1024
// 初始化成簡單值
for (int i = 0; i < N; i++) { x[i] = 1.0f; y[i] = 0.0f; }
saxpy_naive(2.0f, x, y, N);
// 應該所有 y[i] = 2.0
```

**Step 2：加 OpenMP，驗正結果一致**

```c
// 只加這行，其他不變
#pragma omp parallel for schedule(static)
for (int i = 0; i < n; i++) y[i] += a * x[i];
```

確認 `saxpy_omp(coeff, x, y_omp, N)` 的結果和 `saxpy_naive` 在 1e-4 範圍內一致。

**Step 3：加 AVX2（先單線程，再加 OpenMP）**

先把 AVX2 版本在單線程跑對，再加 `#pragma omp parallel for`。這樣分兩步 debug。

```c
// 先不加 OpenMP
void saxpy_avx2_single(float a, const float *x, float *y, int n) {
    __m256 va = _mm256_set1_ps(a);
    int i;
    for (i = 0; i + 7 < n; i += 8) {
        __m256 vx = _mm256_loadu_ps(x + i);
        __m256 vy = _mm256_loadu_ps(y + i);
        vy = _mm256_fmadd_ps(va, vx, vy);
        _mm256_storeu_ps(y + i, vy);
    }
    for (; i < n; i++) y[i] += a * x[i];  // 收尾
}
```

驗正通過後，再包進 `#pragma omp parallel for`。

**Step 4：量測加速比**

加 `clock_gettime(CLOCK_MONOTONIC)` 或 `omp_get_wtime()` 計時，重複 3 次取最快。計算帶寬：SAXPY 每個 element 讀 2 個 float + 寫 1 個 = 12 bytes，所以：

```
BW = N * 12 bytes / time_sec / 1e9  (GB/s)
```

**Step 5：分析結果，和 Roofline 對照**

如果帶寬接近機器理論峰值（i7-10700 約 40 GB/s），你的優化已經到頂了——不是你的程式不夠好，而是受硬體帶寬限制。這和 GPU 優化時遇到 HBM 帶寬上限是完全一樣的問題（只是 GPU 帶寬高 10 倍）。

---

## 測試用例表格

| 測試 | N | 初始化 | 預期輸出 |
|------|---|--------|---------|
| SAXPY 小驗正 | 1024 | `x[i]=1.0f, y[i]=0.0f, a=2.0f` | 全部 `y[i]=2.0f` |
| SAXPY 邊界 | 17（非 8 的倍數）| `x[i]=i, y[i]=1.0f, a=1.0f` | `y[i] = 1.0+i`，每個都要對 |
| Dot product 小驗正 | 8 | `a[i]=1.0f, b[i]=1.0f` | `sum=8.0f` |
| Dot product 邊界 | 9 | `a=[1..9], b=[1..9]` | `sum=285.0f`（1²+2²+...+9²）|
| SAXPY 大規模 | 64M | 如上 | 如期望輸出欄，verify OK |
| Dot product 大規模 | 64M | `a[i]=(i%997)/997.0f, b[i]=1.0f` | `omp≈33520696`（naive 精度不足）|

---

## 參考解答

<details>
<summary>展開查看完整參考解答（先自己試過再看）</summary>

```c
/* 練習 A 參考解答 — SAXPY + dot product CPU 優化
   gcc -O2 -mavx2 -mfma -fopenmp practice_ref.c -o practice_ref.exe -lm
*/
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <omp.h>
#include <immintrin.h>
#include <math.h>

#define N    (1 << 26)   /* 64M floats = 256 MB per array */
#define REPS 3           /* 重複測量取最快 */

static double get_time_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* ---- SAXPY：y[i] = a*x[i] + y[i] ---- */

void saxpy_naive(float a, const float * __restrict__ x,
                 float * __restrict__ y, int n) {
    for (int i = 0; i < n; i++)
        y[i] += a * x[i];
}

void saxpy_omp(float a, const float * __restrict__ x,
               float * __restrict__ y, int n) {
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < n; i++)
        y[i] += a * x[i];
    /* 編譯器在 -O2 下會自動生成 AVX2 */
}

void saxpy_avx2_omp(float a, const float * __restrict__ x,
                    float * __restrict__ y, int n) {
    __m256 va = _mm256_set1_ps(a);
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < n - 7; i += 8) {
        __m256 vx = _mm256_loadu_ps(x + i);
        __m256 vy = _mm256_loadu_ps(y + i);
        vy = _mm256_fmadd_ps(va, vx, vy);      /* vy = a*vx + vy */
        _mm256_storeu_ps(y + i, vy);
    }
    /* 收尾：處理 n % 8 != 0 的部分 */
    for (int i = (n / 8) * 8; i < n; i++)
        y[i] += a * x[i];
}

/* ---- Dot Product ---- */

float dot_naive(const float * __restrict__ a,
                const float * __restrict__ b, int n) {
    float s = 0.0f;
    for (int i = 0; i < n; i++) s += a[i] * b[i];
    return s;
}

float dot_omp(const float * __restrict__ a,
              const float * __restrict__ b, int n) {
    float s = 0.0f;
    #pragma omp parallel for reduction(+:s) schedule(static)
    for (int i = 0; i < n; i++) s += a[i] * b[i];
    return s;
}

float dot_avx2_omp(const float * __restrict__ a,
                   const float * __restrict__ b, int n) {
    float total = 0.0f;
    #pragma omp parallel reduction(+:total)
    {
        int nt  = omp_get_num_threads();
        int tid = omp_get_thread_num();
        int chunk = n / nt;
        int lo = tid * chunk;
        int hi = (tid == nt - 1) ? n : lo + chunk;

        __m256 vsum = _mm256_setzero_ps();
        int i;
        for (i = lo; i + 7 < hi; i += 8)
            vsum = _mm256_fmadd_ps(
                _mm256_loadu_ps(a + i),
                _mm256_loadu_ps(b + i),
                vsum);

        /* 水平 reduction：8 lane → 1 float */
        __m128 lo128 = _mm256_castps256_ps128(vsum);
        __m128 hi128 = _mm256_extractf128_ps(vsum, 1);
        __m128 s128  = _mm_add_ps(lo128, hi128);
        s128 = _mm_hadd_ps(s128, s128);
        s128 = _mm_hadd_ps(s128, s128);
        float local = _mm_cvtss_f32(s128);

        /* 收尾 */
        for (; i < hi; i++) local += a[i] * b[i];
        total += local;
    }
    return total;
}

/* ---- 計時輔助 macro ---- */
#define TIME_IT(label, result_var, call)                 \
    do {                                                 \
        double _best = 1e9;                              \
        for (int _r = 0; _r < REPS; _r++) {             \
            double _t0 = get_time_sec();                 \
            (call);                                      \
            double _t1 = get_time_sec();                 \
            if (_t1 - _t0 < _best) _best = _t1 - _t0;  \
        }                                                \
        (result_var) = _best;                            \
        printf("  %-25s time=%.4f s\n", label, _best);  \
    } while (0)

int main(void) {
    printf("=== 練習 A 參考解答 ===\n");
    printf("N=%d (%.0f M floats, %.0f MB per array)\n",
           N, (double)N / 1e6, (double)N * 4 / 1e6);
    printf("threads=%d, REPS=%d (取最快)\n\n",
           omp_get_max_threads(), REPS);

    float *x  = (float*)_mm_malloc(N * sizeof(float), 32);
    float *y0 = (float*)_mm_malloc(N * sizeof(float), 32);
    float *y1 = (float*)_mm_malloc(N * sizeof(float), 32);
    float *y2 = (float*)_mm_malloc(N * sizeof(float), 32);
    float *fa = (float*)_mm_malloc(N * sizeof(float), 32);
    float *fb = (float*)_mm_malloc(N * sizeof(float), 32);
    if (!x || !y0 || !y1 || !y2 || !fa || !fb) {
        fprintf(stderr, "malloc failed\n"); return 1;
    }

    /* 初始化 */
    for (int i = 0; i < N; i++) {
        x[i]  = (float)(i % 1000) / 1000.0f;
        y0[i] = y1[i] = y2[i] = 1.0f;
        fa[i] = (float)(i % 997) / 997.0f;
        fb[i] = 1.0f;
    }
    float coeff = 2.5f;

    /* ---- SAXPY 測試 ---- */
    printf("--- SAXPY: y[i] = %.1f*x[i] + y[i], N=%dM ---\n",
           coeff, N >> 20);
    double t_naive, t_omp, t_avx2;
    TIME_IT("naive (1T, scalar)",     t_naive, saxpy_naive(coeff, x, y0, N));
    TIME_IT("OpenMP (16T, auto-vec)", t_omp,   saxpy_omp(coeff, x, y1, N));
    TIME_IT("AVX2+OpenMP (16T)",      t_avx2,  saxpy_avx2_omp(coeff, x, y2, N));
    printf("  speedup OMP:      %.2fx\n", t_naive / t_omp);
    printf("  speedup AVX2+OMP: %.2fx\n", t_naive / t_avx2);

    int ok = 1;
    for (int i = 0; i < N; i++)
        if (fabsf(y0[i] - y1[i]) > 1e-4f || fabsf(y0[i] - y2[i]) > 1e-4f) {
            printf("  MISMATCH at %d\n", i); ok = 0; break;
        }
    printf("  verify: %s\n\n", ok ? "OK" : "FAIL");

    /* ---- Dot Product 測試 ---- */
    printf("--- Dot Product: sum(a[i]*b[i]), N=%dM ---\n", N >> 20);
    double t_d0, t_d1, t_d2;
    float r0, r1, r2;
    TIME_IT("naive (1T, scalar)",      t_d0, r0 = dot_naive(fa, fb, N));
    TIME_IT("OpenMP reduction (16T)",  t_d1, r1 = dot_omp(fa, fb, N));
    TIME_IT("AVX2+OpenMP (16T)",       t_d2, r2 = dot_avx2_omp(fa, fb, N));
    printf("  result: naive=%.4f  omp=%.4f  avx2=%.4f\n", r0, r1, r2);
    printf("  speedup OMP:      %.2fx\n", t_d0 / t_d1);
    printf("  speedup AVX2+OMP: %.2fx\n", t_d0 / t_d2);
    printf("  (dot product: memory-bound，加速比受帶寬限制)\n\n");

    /* ---- 記憶體帶寬估算（SAXPY：讀 x + 讀 y + 寫 y = 12 bytes/elem）---- */
    printf("--- 記憶體帶寬分析 (SAXPY) ---\n");
    printf("  naive    : %.1f GB/s\n", (double)N * 12 / t_naive / 1e9);
    printf("  AVX2+OMP : %.1f GB/s\n", (double)N * 12 / t_avx2 / 1e9);
    printf("  (理論峰值帶寬約 40 GB/s，i7-10700)\n");

    _mm_free(x); _mm_free(y0); _mm_free(y1); _mm_free(y2);
    _mm_free(fa); _mm_free(fb);
    return 0;
}
```

**這台機器的真實輸出**（i7-10700, 16T, gcc 14.2.0, `-O2 -mavx2 -mfma -fopenmp`）：

```
=== 練習 A 參考解答 ===
N=67108864 (67 M floats, 268 MB per array)
threads=16, REPS=3 (取最快)

--- SAXPY: y[i] = 2.5*x[i] + y[i], N=64M ---
  naive (1T, scalar)        time=0.0397 s
  OpenMP (16T, auto-vec)    time=0.0350 s
  AVX2+OpenMP (16T)         time=0.0344 s
  speedup OMP:      1.13x
  speedup AVX2+OMP: 1.16x
  verify: OK

--- Dot Product: sum(a[i]*b[i]), N=64M ---
  naive (1T, scalar)        time=0.0679 s
  OpenMP reduction (16T)    time=0.0197 s
  AVX2+OpenMP (16T)         time=0.0199 s
  result: naive=16777216.0000  omp=33520696.0000  avx2=33520696.0000
  speedup OMP:      3.45x
  speedup AVX2+OMP: 3.42x
  (dot product: memory-bound，加速比受帶寬限制)

--- 記憶體帶寬分析 (SAXPY) ---
  naive    : 20.3 GB/s
  AVX2+OMP : 23.4 GB/s
  (理論峰值帶寬約 40 GB/s，i7-10700)
```

</details>

---

## 結果分析：為什麼 SAXPY 加速只有 1.16x？

這是這個練習最重要的發現，值得深挖。

### Roofline 分析（回顧 Ch 2）

SAXPY 的算術強度（Arithmetic Intensity, AI）：
```
每個元素：1 次 FMA（2 個 FLOPs）
每個元素：讀 x(4B) + 讀 y(4B) + 寫 y(4B) = 12 bytes

AI = 2 FLOP / 12 bytes ≈ 0.17 FLOP/byte
```

i7-10700 的峰值算力約 300 GFLOPS（AVX2 FMA），記憶體帶寬約 40 GB/s：

```
Roofline：
  min(AI × BW, Peak compute) = min(0.17 × 40, 300) = min(6.8, 300) = 6.8 GFLOPS

實際在 memory-bound region：帶寬是限制，不是計算
```

所以 SAXPY 的加速上限是：
```
16 threads 增加帶寬利用 ≈ 1–2x（DRAM 帶寬可能本來就被單 thread 吃滿）
AVX2 增加計算吞吐 ≈ 無效（不是計算瓶頸）
```

這就是為什麼多核 + AVX2 只有 1.16x，不是 16x 或 8x。**你的 naive 版本已經以 20 GB/s 跑資料，接近帶寬上限了。**

### Dot Product 為什麼多了一些？

Dot product 也是 memory-bound，但有一個差別：
- SAXPY 要**寫回 y**（讀 + 寫 = 12 bytes/elem）
- Dot product 只**讀 a 和 b**（8 bytes/elem），不寫

讀比寫快一些（寫回需要 write-invalidate cache line），所以 dot product 在 naive 時帶寬已被更充分利用，多核加速（3.45x）比 SAXPY 更明顯。

### Float 精度問題（bonus 發現）

注意 `dot_naive` 的結果是 `16777216`，而正確答案是 `33520696`——相差幾乎 2x。

`2^24 = 16777216` 是 `float` 的尾數能精確表示的最大整數。當累加器超過這個值後，更小的增量會因為捨入而消失：

```c
float acc = 16777216.0f;  // 2^24
acc += 0.5f;
// acc == 16777216.0f，沒變！(0.5 比 float 的 ULP 還小)
```

這個精度問題讓 naive 的 float 累加在 N=64M 時就「卡住」了。OpenMP 的 reduction 把 N 分成 16 份後每份只有 4M 個元素，每個 partial sum 還沒到精度牆，最後合併才加起來，所以結果正確。

**教訓**：大規模的 float 累加，要麼用 double，要麼用 Kahan compensated summation，要麼用 pairwise reduction（divide-and-conquer）。

---

## 踩雷集錦

**1. `_mm256_fmadd_ps` 的參數順序**

```c
_mm256_fmadd_ps(a, b, c)  → a*b + c  （a 和 b 相乘，加 c）
_mm256_fmadd_ps(va, vx, vy)  // ← 正確：vy = va*vx + vy
_mm256_fmadd_ps(vx, vy, va)  // ← 也對，但 va 被當成累加目標 → 語意錯
```

三個參數都是 FMA 的組成，但意義是 `a*b+c`，要確認第一個 `a` 是 scalar 廣播值 (`_mm256_set1_ps`)，第二個是 data，第三個是 accumulator。

**2. `#pragma omp parallel for` 裡面的 `__m256` 是 thread-private 的**

在 `parallel for` 的 body 裡宣告的 `__m256 va`，每個 thread 有自己的副本——這是 C 函式裡 local 變數的正常行為，不需要特別處理。但如果 `va` 是外部宣告的全域變數，就要小心。

**3. Dot product naive 的精度差異是預期行為**

`dot_naive` 在 N=64M 時結果錯誤是 float 精度問題，不是你的 bug。解法：
```c
// 改用 double 累加器
double dot_naive_d(const float *a, const float *b, int n) {
    double s = 0.0;
    for (int i = 0; i < n; i++) s += (double)a[i] * b[i];
    return s;
}
```

**4. SAXPY 收尾的 off-by-one**

```c
// 主迴圈：i < n-7，確保有完整的 8 個元素
for (i = 0; i + 7 < n; i += 8) { ... }

// 收尾：從最後一個完整 8-pack 的後面開始
for (int i = (n / 8) * 8; i < n; i++) y[i] += a * x[i];
```

如果寫成 `for (int i = n - n%8; ...)` 當 `n%8==0` 時也對，但 `(n/8)*8` 更直觀。

**5. `_mm_malloc` vs `malloc`**

`_mm_malloc` 是 Intel 提供的 32-byte 對齊分配，在 MSYS2 gcc 可以用。Linux 可以用 `aligned_alloc(32, size)` 或 `posix_memalign(&ptr, 32, size)`。`_mm_malloc` 分配的記憶體必須用 `_mm_free` 釋放，不能用 `free`。

---

## 自我檢核

完成練習後，確認你能回答：

1. SAXPY 的算術強度是多少 FLOP/byte？為什麼這個數字決定了加速比的上限？
2. `dot_naive` 在 N=64M 時為什麼結果錯誤？什麼修法可以讓結果正確？
3. 你的 `saxpy_avx2_omp` 用了 `_mm256_fmadd_ps`，如果只用 `-mavx2` 不加 `-mfma`，會發生什麼（答：`_mm256_fmadd_ps` 需要 FMA 硬體，編譯器會報錯或改用 `vmulps + vaddps`）？
4. 在 `dot_avx2_omp` 的水平 reduction 裡，`_mm256_extractf128_ps(vsum, 1)` 提取了哪個部分？它和 `_mm256_castps256_ps128(vsum)` 提取的部分有何不同？
5. 如果把 `N` 改成 `1024`（全部 fit 在 L1/L2 cache），加速比會有什麼變化？為什麼？

---

## 延伸挑戰

**挑戰 1：8 路 unrolled AVX2 SAXPY**

在 `saxpy_avx2_omp` 的基礎上，每次迭代同時處理 4 × 8 = 32 個 float（4 個 `__m256`）。這讓 CPU 的 OoO 引擎有更多 FMA 可以同時排程（ILP + SIMD）。量測是否有額外加速（memory-bound workload 上可能很小）。

```c
for (i = 0; i + 31 < n; i += 32) {
    __m256 vx0 = _mm256_loadu_ps(x + i);
    __m256 vx1 = _mm256_loadu_ps(x + i + 8);
    __m256 vx2 = _mm256_loadu_ps(x + i + 16);
    __m256 vx3 = _mm256_loadu_ps(x + i + 24);
    // ... 4 個 fmadd
}
```

**挑戰 2：非 memory-bound workload — 矩陣向量乘法（gemv）**

實作 `y = A*x`，其中 A 是 `M×N` 矩陣（M=N=4096）。這個操作的算術強度 ≈ `2MN / (MN + M + N) bytes ≈ 1 FLOP/byte`，比 SAXPY 高，更接近 compute ridge point。你應該看到更顯著的 AVX2 加速效果。

**挑戰 3：用 perf stat 量化 cache miss**

```bash
perf stat -e cache-misses,cache-references ./saxpy_naive.exe
perf stat -e cache-misses,cache-references ./saxpy_avx2_omp.exe
```

比較兩者的 cache miss 率。理論上 memory-bound workload 的 cache miss 率應該很高，因為資料比 cache 大。

**挑戰 4：N 掃描圖**

把 N 從 1K（全在 L1）掃到 64M（超出 L3），每個 N 量測 `saxpy_naive` 的帶寬（GB/s）。畫出帶寬 vs N 的折線圖（可以用 gnuplot 或 Python matplotlib）。你應該看到：L1 帶寬（~800 GB/s）→ L2 帶寬（~200 GB/s）→ L3 帶寬（~100 GB/s）→ DRAM 帶寬（~40 GB/s）的階梯。

---

CPU 平行地基就此完成：ILP（Ch 3）→ SIMD（Ch 4）→ OpenMP（Ch 5）→ patterns（Ch 6）→ 這個整合練習。

你已經有了完整的「平行思維」架構。接下來 Ch 7 進入 GPU 架構——你會發現 GPU 的 SM 就是一個「有 1024 個 AVX2 lane 的超寬 SIMD 機器」，warp 就是放大版的 AVX2，global memory 就是帶寬更寬的 DDR，shared memory 就是手動管理的 L1 cache。

→ [Ch 7 GPU 架構總覽：SIMT 與 throughput machine](./07-gpu-architecture-overview.md)
