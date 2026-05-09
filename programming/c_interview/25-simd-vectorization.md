# Ch 25 — SIMD 與自動向量化

> 目標：理解 SIMD 的基本概念，能寫出讓編譯器自動向量化的程式碼，以及在需要時用 intrinsics 手動向量化。

## SIMD 是什麼

**SIMD（Single Instruction Multiple Data）**：一條指令同時操作多個資料。

```
標量加法（Scalar）：
[a0] + [b0] = [c0]   一次一個

SSE2（128-bit）：
[a0 a1 a2 a3] + [b0 b1 b2 b3] = [c0 c1 c2 c3]   一次四個 float

AVX2（256-bit）：
[a0 a1 a2 a3 a4 a5 a6 a7] + [b0 b1 b2 b3 b4 b5 b6 b7]   一次八個 float
```

理論加速比 = SIMD 寬度 / 元素大小。256-bit / 32-bit float = 8×。

---

## 自動向量化（Auto-Vectorization）

現代編譯器（`-O2` / `-O3`）可以自動把簡單的迴圈轉換成 SIMD 指令：

```c
// 這個迴圈很容易被向量化：
void add_arrays(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++)
        c[i] = a[i] + b[i];
}
```

```bash
gcc -O3 -march=native -fopt-info-vec-optimized add.c
# 輸出：add.c:4:5: optimized: loop vectorized using 8 byte vectors
```

**阻礙向量化的常見原因**：

```c
// 1. 別名問題：編譯器不確定 a、b、c 是否 overlap
//    解法：加 restrict 關鍵字
void add_arrays(float * restrict a, float * restrict b,
                float * restrict c, int n) { ... }

// 2. 不規律的 access pattern：
for (int i = 0; i < n; i++)
    c[i] = a[idx[i]] + b[i];   // gather operation，較難向量化

// 3. Loop-carried dependency：
for (int i = 1; i < n; i++)
    a[i] = a[i] + a[i-1];      // a[i] 依賴 a[i-1]，不能並行
```

---

## restrict 關鍵字（C99）

告訴編譯器：在這個函式的 scope 內，這個指標是存取該記憶體的唯一方式：

```c
void copy(int * restrict dst, const int * restrict src, size_t n) {
    for (size_t i = 0; i < n; i++)
        dst[i] = src[i];
    // 編譯器可以假設 dst 和 src 不 overlap → 向量化
}

// 不加 restrict：編譯器必須保守，假設可能 overlap → 不能向量化
```

`restrict` 是你對編譯器的**承諾**——若實際上 overlap，行為是 UB。

---

## SSE/AVX Intrinsics（手動向量化）

有時編譯器無法自動向量化，需要手動用 intrinsics：

```c
#include <immintrin.h>   // SSE/AVX

// 標量版本：
float dot_scalar(float *a, float *b, int n) {
    float sum = 0;
    for (int i = 0; i < n; i++)
        sum += a[i] * b[i];
    return sum;
}

// SSE 版本（一次處理 4 個 float）：
float dot_sse(float *a, float *b, int n) {
    __m128 vsum = _mm_setzero_ps();    // 128-bit 暫存器，4 個 float，全零

    int i;
    for (i = 0; i + 4 <= n; i += 4) {
        __m128 va = _mm_loadu_ps(&a[i]);   // 非對齊載入 4 float
        __m128 vb = _mm_loadu_ps(&b[i]);
        vsum = _mm_add_ps(vsum, _mm_mul_ps(va, vb));
    }

    // 把 vsum 的 4 個 float 加起來（horizontal sum）：
    __m128 shuf = _mm_shuffle_ps(vsum, vsum, _MM_SHUFFLE(2, 3, 0, 1));
    vsum = _mm_add_ps(vsum, shuf);
    shuf = _mm_shuffle_ps(vsum, vsum, _MM_SHUFFLE(0, 1, 2, 3));
    vsum = _mm_add_ps(vsum, shuf);
    float result = _mm_cvtss_f32(vsum);

    // 處理尾端（n 不是 4 的倍數）：
    for (; i < n; i++)
        result += a[i] * b[i];

    return result;
}
```

---

## AVX2 常用 Intrinsics

| Intrinsic | 說明 |
|-----------|------|
| `_mm256_loadu_ps(p)` | 非對齊載入 8 float |
| `_mm256_add_ps(a, b)` | 8 float 加法 |
| `_mm256_mul_ps(a, b)` | 8 float 乘法 |
| `_mm256_fmadd_ps(a,b,c)` | a*b+c（FMA，一條指令）|
| `_mm256_cmp_ps(a,b,op)` | 比較，回傳 mask |
| `_mm256_blendv_ps(a,b,m)` | 依 mask 選 a 或 b |
| `_mm256_store_ps(p,v)` | 對齊寫出（需 32-byte 對齊）|
| `_mm256_storeu_ps(p,v)` | 非對齊寫出 |

---

## 實際應用：memcpy 的向量化

libc 的 memcpy 對大塊資料會用 `rep movsq`（硬體支援）或 AVX 向量。手動實作：

```c
// 簡化版（展示概念）：
void *fast_memcpy(void *dst, const void *src, size_t n) {
    char       *d = (char *)dst;
    const char *s = (const char *)src;

    // 用 256-bit AVX 複製（需要 -mavx2）：
    while (n >= 32) {
        __m256i v = _mm256_loadu_si256((const __m256i *)s);
        _mm256_storeu_si256((__m256i *)d, v);
        s += 32; d += 32; n -= 32;
    }
    while (n--) *d++ = *s++;
    return dst;
}
```

---

## 編譯 Flag

```bash
# 啟用所有本機 CPU 支援的 SIMD 指令：
gcc -O3 -march=native prog.c

# 只啟用特定指令集（可移植性更好）：
gcc -O3 -msse4.2 prog.c
gcc -O3 -mavx2 prog.c

# 查看支援的指令集：
gcc -march=native -Q --help=target | grep -E "sse|avx"
```

---

## 自我檢核

- [ ] 能解釋 SIMD 的原理（一條指令操作多個資料）
- [ ] 知道 `restrict` 對向量化的作用（消除 alias 假設）
- [ ] 知道 loop-carried dependency 為什麼阻礙向量化
- [ ] 知道 `_mm256_fmadd_ps` 對浮點 dot product 的意義（一條指令完成 a*b+c）

→ [Ch 26 Branchless 程式設計](./26-branchless.md)
