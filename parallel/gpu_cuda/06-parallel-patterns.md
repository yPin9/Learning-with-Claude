# Ch 6 — 平行 pattern 導論：map / reduce / scan / stencil / gather-scatter

> **目標**：認識五個平行計算的核心 pattern——map / reduce / scan / stencil / gather-scatter。每個 pattern 給直覺 + CPU OpenMP 實作 + 在 GPU 上的對應（後面 CUDA 章節會重寫）。scan（prefix sum）的 Blelloch work-efficient 演算法在這裡埋伏筆，CUDA Ch 22 會完整實作。

> **環境**：gcc 14 + OpenMP, x86-64 Windows（MSYS2）  
> **編譯方式**：`gcc -O2 -fopenmp patterns.c -o patterns.exe`

學了 SIMD（Ch 4）和 OpenMP（Ch 5），你有了工具。但工具要配合「模式」才能有效使用。計算科學家發現，幾乎所有可平行化的計算都是幾個基本 pattern 的組合。認識這些 pattern 有雙重好處：

1. 遇到新問題，你能快速辨認「這是 reduce」或「這是 stencil」，知道平行化的難點在哪
2. 這些 pattern 在 GPU 上有對應的高效實作，CUDA 的 kernel 很多時候就是這些 pattern 的組合

---

## 一、Pattern 概覽

```
輸入                    Pattern           輸出
──────────────────────────────────────────────────────────
[x0,x1,...,xN-1]  →   MAP f       →  [f(x0),f(x1),...,f(xN-1)]
                                       （輸出與輸入等長，逐元素獨立）

[x0,x1,...,xN-1]  →   REDUCE ⊕   →  x0⊕x1⊕...⊕xN-1
                                       （多到一，需要結合律）

[x0,x1,...,xN-1]  →   SCAN ⊕     →  [x0, x0⊕x1, x0⊕x1⊕x2, ...]
                                       （prefix，輸出等長但有依賴）

[x0,x1,...,xN-1]  →   STENCIL    →  [f(x0..x2), f(x1..x3), ...]
                                       （每個輸出依賴輸入的鄰居）

index→source      →   GATHER     →  [src[idx[0]], src[idx[1]], ...]
source←(idx,val)  →   SCATTER    →  dst[idx[i]] = val[i]
                                       （不規則記憶體存取）
```

---

## 二、Pattern 1：MAP — 逐元素獨立變換

### 直覺

每個輸出元素只依賴對應的一個輸入元素，完全獨立。這是最易平行化的 pattern。

```
輸入: [1,  2,  3,  4,  5,  6,  7,  8]
         f   f   f   f   f   f   f   f    ← 8 個 f 完全獨立，可同時做
輸出: [f(1),f(2),...,f(8)]
```

### CPU OpenMP 實作

```c
// SAXPY: y[i] = a * x[i] + y[i]  （標準 map 例子）
void saxpy(float a, const float *x, float *y, int n) {
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < n; i++) {
        y[i] += a * x[i];
    }
}

// 更一般的 map（函數指標版）
void map_f(float *out, const float *in, int n,
           float (*f)(float)) {
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < n; i++) {
        out[i] = f(in[i]);
    }
}
```

**真實輸出**（N=4M，這台機器）：
```
map (SAXPY, N=4M): y[0]=1.00  y[N-1]=1.0600  time=0.0029 s
```

### GPU 對應

CUDA kernel 幾乎就是 map 的直接表達：
```cuda
// 後面 Ch 12 會完整展開這個
__global__ void saxpy(float a, float *x, float *y, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) y[i] += a * x[i];  // 每個 thread 做一個元素
}
```

GPU 的設計哲學就是「極大量 map」——啟動幾千個 thread，每個做一小塊 map 工作。

### MAP 的記憶體存取特性

- **讀**：stride-1（連續），cache 友善，SIMD 友善
- **寫**：stride-1（連續），同上
- **計算密度**：低（SAXPY 每次讀 2 個 float 寫 1 個，做 1 次 FMA）→ memory-bound

SAXPY 在這台機器實測 BW 約 15–25 GB/s，接近記憶體帶寬上限，不是計算上限。

---

## 三、Pattern 2：REDUCE — 多到一，樹狀合併

### 直覺

把 N 個元素用某個二元運算（`+`, `max`, `and`, ...）合成 1 個值。

```
輸入: [1,  2,  3,  4,  5,  6,  7,  8]

Level 0 (原始):  1   2   3   4   5   6   7   8
Level 1 (兩兩):   3       7      11      15
Level 2 (兩兩):       10              26
Level 3 (結果):               36

樹狀合併：O(log N) 層，每層可以完全平行
```

為什麼需要**結合律（associativity）**？因為平行 reduce 會改變結合順序：
```
Sequential:  ((((1+2)+3)+4)+5)...
Parallel:    (1+2) + (3+4) + (5+6) + (7+8)  ← 不同結合順序
```
整數加法完全沒問題（結合律成立）。浮點加法：結合律**不成立**，但誤差通常可接受。矩陣乘法：結合律成立，但交換律不成立。

### CPU OpenMP 實作

```c
// 整數 sum reduction
long reduce_sum_int(const int *x, int n) {
    long sum = 0;
    #pragma omp parallel for reduction(+:sum)
    for (int i = 0; i < n; i++) sum += x[i];
    return sum;
}

// float max reduction
float reduce_max(const float *x, int n) {
    float m = x[0];
    #pragma omp parallel for reduction(max:m)
    for (int i = 0; i < n; i++) {
        if (x[i] > m) m = x[i];
    }
    return m;
}
```

**真實輸出**（N=4M）：
```
reduce (sum, N=4M): sum=2076178.56  time=0.0008 s
```

### 自己實作樹狀 reduce（理解 GPU 版本的基礎）

OpenMP 的 `reduction` 是黑盒。這裡展示手動實作樹狀 reduce，後面 CUDA shared memory reduce 的直接前身：

```c
// 兩階段 reduce：各 thread 先算自己的 partial sum，再合併
double tree_reduce(const float *x, int n) {
    int nt;
    double *partial;

    #pragma omp parallel
    {
        #pragma omp single
        {
            nt = omp_get_num_threads();
            partial = (double*)calloc(nt, sizeof(double));
        }
        int tid = omp_get_thread_num();
        int chunk = n / nt;
        int lo = tid * chunk;
        int hi = (tid == nt-1) ? n : lo + chunk;

        double local = 0.0;
        for (int i = lo; i < hi; i++) local += x[i];
        partial[tid] = local;
        #pragma omp barrier

        // Thread 0 做最後合併（在 GPU 上這步也是 tree reduce）
        #pragma omp single
        {
            double total = 0;
            for (int i = 0; i < nt; i++) total += partial[i];
            printf("total = %.2f\n", total);
            free(partial);
        }
    }
    return 0;  // 簡化版，完整版要傳回值
}
```

### GPU 對應

CUDA 的 reduce 是最精妙的 kernel 之一（Ch 20 會深挖）：
- 分兩階段：每個 thread block 在 shared memory 裡做 tree reduce → 部分結果
- Host 再對部分結果做一次 reduce

---

## 四、Pattern 3：SCAN（Prefix Sum）— 有依賴但可平行

### 直覺

Prefix sum（前綴和）是看起來最難平行化的 pattern，因為 `out[i]` 依賴 `out[i-1]`：

```
輸入: [1, 2, 3, 4, 5]
輸出: [1, 3, 6, 10, 15]   (inclusive)
或:  [0, 1, 3, 6, 10]    (exclusive，out[0]=0，右移一位)
```

純序列實作：
```c
void scan_seq(const float *in, double *out, int n) {
    out[0] = in[0];
    for (int i = 1; i < n; i++) out[i] = out[i-1] + in[i];
}
```
這個 `out[i] = out[i-1] + in[i]` 的依賴鏈看起來無法平行——但其實可以。

### Work-Efficient Parallel Scan（Blelloch 1990）

這是本章最重要的演算法。Blelloch 演算法用兩次 tree pass 做到：
- **Work**: O(N)（和序列相同）
- **Span**: O(log N)（平行深度）

```
輸入: [1, 2, 3, 4, 5, 6, 7, 8]

Phase 1 — Up-sweep（Reduce 樹）:
Level 0: [1,  2,  3,  4,  5,  6,  7,  8 ]  (原始)
Level 1: [1,  3,  3,  7,  5, 11,  7, 15 ]  (相鄰兩兩相加，stride=1)
Level 2: [1,  3,  3, 10,  5, 11,  7, 26 ]  (stride=2)
Level 3: [1,  3,  3, 10,  5, 11,  7, 36 ]  (stride=4，arr[7]=總和)

Phase 2 — Down-sweep（分配 prefix）:
設 arr[7]=0（exclusive scan 起點）
Level 3: [...,...,..., 0,...,...,...,28 ]
Level 2: [...,..., 0,...,..., 10,...,28 ]
Level 1: [ 0,..., 3,...,10,...,21,...,28 ]
Level 0: [ 0, 1, 3, 6,10,15,21,28]  ← exclusive scan 完成
```

CPU 上的兩階段平行 scan 實作（簡化版，每 thread 算自己的 partial sum + offset）：

```c
void scan_parallel(const float *in, double *out, int n) {
    int nt;
    double *partial;

    #pragma omp parallel
    {
        #pragma omp single
        {
            nt = omp_get_num_threads();
            partial = (double*)calloc(nt+1, sizeof(double));
        }
        int tid = omp_get_thread_num();
        int chunk = n / nt;
        int lo = tid * chunk;
        int hi = (tid == nt-1) ? n : lo + chunk;

        // Pass 1: 各 thread 計算自己區段的 total
        double local_sum = 0.0;
        for (int i = lo; i < hi; i++) local_sum += in[i];
        partial[tid+1] = local_sum;

        #pragma omp barrier
        #pragma omp single
        {
            // Sequential prefix sum over partial[]（小規模，O(threads)）
            for (int i = 1; i <= nt; i++) partial[i] += partial[i-1];
        }
        #pragma omp barrier  // 等 prefix sum 完成

        // Pass 2: 各 thread 以自己的 offset 做 local sequential scan
        double offset = partial[tid];  // Thread tid 的起始 prefix
        out[lo] = offset + in[lo];
        for (int i = lo+1; i < hi; i++) out[i] = out[i-1] + in[i];

        #pragma omp single
        free(partial);
    }
}
```

**真實輸出**（N=4M，double 精度，verify OK）：
```
scan seq (N=4M):    out[N-1]=2076178.56  time=0.0072 s
scan par (N=4M):    out[N-1]=2076178.56  time=0.0040 s  speedup=1.82x
scan verify: OK
```

加速約 1.8x（16 個 thread 但 scan 有同步開銷），CPU 上的效益有限。GPU 上的 scan 加速更顯著（warp-level shuffle 指令讓 intra-warp scan 無需 shared memory 存取）。

### SCAN 的用途

看起來抽象，但幾乎無處不在：
- **Histogram equalization**（影像處理）
- **Stream compaction**（GPU 上過濾元素：把符合條件的元素壓縮到連續記憶體，保持順序）
- **Radix sort**（每位的計數陣列做 scan）
- **Sparse matrix-vector multiply**（計算 row offset）
- **Particle simulation**（計算鄰居列表的起始位置）

---

## 五、Pattern 4：STENCIL — 鄰居依賴的計算

### 直覺

每個輸出元素依賴輸入的**局部鄰域**（stencil pattern）。典型例子：

```
1D 3-point stencil（1D 熱傳導方程離散化）：
out[i] = 0.5 * (in[i-1] + in[i+1])

2D 5-point stencil（2D Laplacian）：
out[y][x] = 0.25 * (in[y-1][x] + in[y+1][x] +
                    in[y][x-1] + in[y][x+1])
```

```
. . . . .
. . X . .   ← X 的輸出只依賴它的上下左右 4 個 input
. X O X .   （O 是輸出位置，X 是它依賴的鄰居）
. . X . .
. . . . .
```

### CPU OpenMP 實作

```c
// 2D 5-point Jacobi stencil
// gcc -O2 -fopenmp stencil.c -o stencil.exe
void stencil_2d(const float *in, float *out, int nx, int ny) {
    #pragma omp parallel for schedule(static)
    for (int y = 1; y < ny-1; y++) {
        for (int x = 1; x < nx-1; x++) {
            out[y*nx+x] = 0.25f * (
                in[(y-1)*nx+x] + in[(y+1)*nx+x] +
                in[y*nx+(x-1)] + in[y*nx+(x+1)]
            );
        }
    }
}
```

**真實輸出**（4096×4096，16 threads）：
```
2D 5-point stencil: 4096x4096 grid, threads=16
stencil: out[2*NX+2]=94.0000  time=0.0096 s
```

### Stencil 的平行難點

1. **邊界條件（Boundary Conditions）**：最外圍的格子沒有完整的鄰居，需要特殊處理（Dirichlet 邊界 / Neumann 邊界 / periodic）。

2. **寫衝突？沒有**：每個 `out[y][x]` 只被一個 thread 寫，不同 thread 各自負責自己的行（`#pragma omp parallel for` 按行分）。

3. **Temporal blocking**（時間步驟多次迭代的優化）：每次迭代讀整個 `in` 陣列，快取反覆失效。多次迭代的 stencil 通常要 tiling 或 wavefront 才能有好的 cache reuse。

4. **`in` 和 `out` 必須不同**：stencil 不能 in-place（否則寫了 `out[y][x]` 後，`in[y+1][x]` 已被污染）。每個時間步驟 swap `in` 和 `out`（double buffering）。

### GPU 對應

GPU 的 stencil 有一個關鍵技巧：把鄰居資料載入 shared memory（CUDA 術語），讓整個 thread block 的鄰居存取都在 shared memory 裡（遠比 global memory 快）。Ch 18 會完整實作這個。

---

## 六、Pattern 5：GATHER / SCATTER — 不規則存取

### GATHER：不規則讀

```
索引陣列 idx:   [3, 7, 1, 5, 0]
來源陣列 src:   [a, b, c, d, e, f, g, h]
輸出:          [src[3], src[7], src[1], src[5], src[0]]
             = [d,      h,      b,      f,      a     ]
```

每個輸出從 `src` 的**不同**位置讀取（由 `idx` 決定）。

```c
// CPU gather
void gather(const float *src, const int *idx, float *dst, int n) {
    #pragma omp parallel for
    for (int i = 0; i < n; i++) {
        dst[i] = src[idx[i]];  // 不規則讀取
    }
}
```

平行化容易（不同 `i` 讀不同位置，沒有寫衝突）。但效能差：不規則記憶體存取 → cache miss 率高 → memory-bound。

### SCATTER：不規則寫

```
值陣列 val:     [v0, v1, v2, v3]
索引陣列 idx:   [3,  7,  1,  5]
輸出陣列 dst:   dst[3]=v0, dst[7]=v1, dst[1]=v2, dst[5]=v3
```

```c
// CPU scatter（注意：如果 idx 有重複，有 write race！）
void scatter(float *dst, const int *idx, const float *val, int n) {
    for (int i = 0; i < n; i++) {
        dst[idx[i]] = val[i];  // 如果 idx[i] == idx[j]，誰寫的不確定
    }
    // 沒法直接 #pragma omp parallel for，因為 dst[idx[i]] 可能衝突
}
```

Scatter 不能直接平行化（寫衝突），必須確保 `idx` 沒有重複，或用 atomic scatter，或用 sort + reduce-by-key 的方式重組。

### 應用：Stream Compaction（GPU 上極常用）

「從 N 個元素中選出符合條件的子集，保持順序」：

```
輸入:    [1,  0,  5,  0,  3,  0,  7,  2]
條件:    [T,  F,  T,  F,  T,  F,  T,  T]
輸出:    [1,  5,  3,  7,  2]
```

實作步驟（scan-based compaction）：
1. MAP：計算 predicate（`pred[i] = condition(in[i])? 1 : 0`）
2. SCAN：對 predicate 做 prefix sum（`addr[i] = sum(pred[0..i-1])`）→ 得到每個選中元素的輸出位置
3. SCATTER：把符合條件的元素寫到 `out[addr[i]]`

這個模式在 GPU path tracing（過濾活躍光線）、粒子物理模擬（過濾活躍粒子）裡無處不在。

---

## 七、五 Pattern 總覽與 GPU 對應

| Pattern | 平行度 | 記憶體存取 | 主要難點 | CUDA 對應章節 |
|---------|--------|-----------|---------|-------------|
| MAP | 完美 | 連續（stride-1）| 無 | Ch 12（第一個 kernel）|
| REDUCE | 需要 log N 層 | 連續 | 需要 shared memory tree | Ch 20 |
| SCAN | 需要 log N 層 | 連續 | 兩 pass，需要全域同步 | Ch 22 |
| STENCIL | 除邊界外完美 | 局部連續（可利用 shared mem）| halo exchange / 邊界條件 | Ch 18 |
| GATHER | 完美 | 不規則讀 | cache miss，GPU 有 gather 指令 | Ch 19 |
| SCATTER | 需要原子或保證無衝突 | 不規則寫 | 寫衝突（需要 atomics 或重組）| Ch 19 |

---

## 踩雷集錦

**1. SCAN 的 inclusive vs exclusive 搞混**

- Inclusive scan：`out[i] = in[0] + in[1] + ... + in[i]`（包含自己）
- Exclusive scan：`out[i] = in[0] + in[1] + ... + in[i-1]`（不包含自己，`out[0]=0`）

絕大多數的「輸出位置計算」（stream compaction、radix sort）需要 exclusive scan，因為你要知道「在我之前有多少個 True」。用錯了，輸出會差一格。

**2. STENCIL in-place 修改**

```c
// 錯的：寫了 out[y][x] 後，下一個 out[y][x+1] 讀到的 in[y][x] 已是新值
for (int y=1;y<ny-1;y++)
    for (int x=1;x<nx-1;x++)
        a[y*nx+x] = 0.25*(a[(y-1)*nx+x]+a[(y+1)*nx+x]+...);  // bug!
```

解法：始終用兩個 buffer（`in` 和 `out`），每個時間步驟 swap。

**3. REDUCE 的運算子必須滿足結合律**

如果 `⊕` 不滿足結合律（如矩陣乘法的「先左後右」這種有方向性的運算），平行 reduce 會給錯誤結果。OpenMP `reduction` 只支援真正的結合律運算（`+`, `*`, `max`, `min`, `&`, `|`, `^`）。

**4. SCATTER 的寫衝突**

```c
// 如果 idx 有重複值，scatter 結果不確定
dst[idx[0]] = val[0];  // idx[0] == idx[2] 的話...
dst[idx[2]] = val[2];  // 誰贏了？
```

在 GPU 上，多個 thread 同時 scatter 到同一個位置，必須用 `atomicAdd` / `atomicMax` 等，且語意要對（不是所有 scatter 都能用 atomics 解決，有時要先 sort by key 再 reduce）。

**5. SCAN 的浮點精度**

Blelloch 的 tree scan 改變了加法順序，浮點誤差和序列版本可能有差異。若精度很重要（如 double-precision 金融計算），要用 compensated summation（Kahan summation）或改用 pairwise summation。

---

## 動手練習

1. 把 REDUCE 的 `max` 改成「同時找 max 和它的 index」（`argmax`）。OpenMP 的 `reduction` 不直接支援自訂 struct，你要用 critical section 或 `omp declare reduction`。
2. 實作一個 CPU 版的 stream compaction（如：從 1M 個隨機整數中選出偶數，保持順序）。用 scan-based 方式（MAP 計算 predicate → SCAN 計算 output address → SCATTER 填充）。驗證結果正確，並量測各步驟的時間比例。
3. 實作 2D stencil 的兩層時間步（10 次 Jacobi 迭代），使用 double buffering（swap `in` 和 `out` 指標）。確認收斂（最大殘差逐漸縮小）。

---

## 本章重點

- 五個平行 pattern：MAP（完美平行）→ REDUCE（樹狀，log N 層）→ SCAN（prefix sum，兩 pass）→ STENCIL（鄰居依賴）→ GATHER/SCATTER（不規則存取）。
- MAP 是最直接的 CUDA kernel 形態；REDUCE 和 SCAN 在 GPU 上需要 shared memory 和 warp-level 操作；STENCIL 用 shared memory 做 halo cache；SCATTER 需要 atomics。
- SCAN 的 Blelloch 演算法：Up-sweep（reduce 樹）+ Down-sweep（分配前綴）= O(N) work + O(log N) span。
- Stream compaction = MAP + SCAN + SCATTER，GPU 路徑追蹤裡常見。
- 這台機器實測：scan par (16T) 比 seq 快 1.82x（有同步開銷）；GPU 上 warp shuffle 讓 intra-warp scan 更高效（Ch 22）。

---

## 自我檢核

1. 為什麼 REDUCE 需要運算子滿足結合律？用一個不滿足結合律的例子說明會出什麼問題。
2. Blelloch scan 的 Up-sweep phase 在做什麼？Down-sweep phase 在做什麼？
3. Stream compaction 的三個步驟是什麼？用「從陣列中選出 > 0 的元素」為例說明。
4. SCATTER 為什麼不能直接 `#pragma omp parallel for`？
5. STENCIL 為什麼要用 double buffering（in-place 為什麼不對）？

---

## 延伸閱讀

1. **Guy Blelloch, "Prefix Sums and Their Applications" (1990)**  
   tech report CMU-CS-90-190，Google 可找到 PDF。  
   讀哪裡：Section 1–3（prefix scan 定義、work-efficient 演算法、應用）。前提：無（數學符號基礎即可）。關聯：本章 scan 演算法的原始文獻，GPU scan 的理論基礎。

2. **NVIDIA "Optimizing Parallel Reduction in CUDA" (Mark Harris)**  
   developer.nvidia.com/gpugems/gpugems3/part-vi-gpu-computing/chapter-39-parallel-prefix-sum-scan-cuda  
   讀哪裡：整篇。前提：本章 + 基本 CUDA 概念（Ch 12 之後）。關聯：直接對應本章 reduce/scan 在 GPU 上的實作，Ch 20/22 的參考文章。

3. **《Programming Massively Parallel Processors》(Kirk & Hwu) Ch 10–11**  
   「Parallel Patterns: Parallel Prefix Sum」和「Sparse Matrix Computation」。  
   前提：懂 CUDA 基礎。關聯：本章所有 pattern 的 GPU 版本，尤其 scan 的 shared memory 實作，是本書最值得讀的幾章之一。

4. **John Owens et al., "GPU Computing"** (Proceedings of the IEEE, 2008)  
   讀哪裡：Section 3 「GPU Computing Primitives」——map/reduce/scan/sort 的定義和 GPU 實作概述。前提：無特別要求。關聯：這篇論文把本章的五個 pattern 系統化描述，是 GPU computing 研究的重要文獻。

5. **CUB Library（NVIDIA）** — github.com/NVIDIA/cub  
   讀哪裡：`cub::DeviceReduce`, `cub::DeviceScan`, `cub::DeviceSelect::If`（stream compaction）的文件和範例。前提：懂 CUDA。關聯：本章所有 pattern 在工業界的正式實作，Ch 20 之後會用到。

---

CPU 平行的地基打好了：ILP（Ch 3）、SIMD（Ch 4）、多執行緒（Ch 5）、pattern（Ch 6）。到練習 A 把學到的東西整合實戰。之後 Ch 7 開始進入 GPU 架構——你會發現 GPU 就是把這章的 pattern 在幾千個執行單元上同時跑。

→ [練習 A：CPU 平行 — OpenMP reduction + AVX](./practice-a-cpu-parallel.md)
