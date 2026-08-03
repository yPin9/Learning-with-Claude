# Ch 5 — 多執行緒：OpenMP / pthreads / race condition / false sharing

> **目標**：把多核心的力量接進你的 C 程式；同時正面對抗多執行緒的三個麻煩：race condition（競態條件）、false sharing（偽共用）、同步開銷。所有問題都用真跑的程式碼展示——race condition 跑出錯誤再修對，false sharing 跑出變慢再用 padding 修好。

> **環境**：gcc 14 + OpenMP 4.5, x86-64 Windows（MSYS2），16 threads  
> **編譯方式**：`gcc -O2 -fopenmp foo.c -o foo.exe`

從 Ch 4 你有了 SIMD（單核多資料）。這章加入多核——16 個核心同時推進，各自有獨立的 register 和 L1/L2 cache，共享 L3 和 RAM。這個共享的記憶體空間是加速的來源，也是問題的來源。

---

## 一、為什麼 OpenMP？不是應該學 pthread？

pthread（POSIX Threads）是多執行緒的底層 API：

```c
// pthread 版本 —— 概念清楚但繁瑣
pthread_t threads[16];
struct arg { int *arr; int lo; int hi; double result; } args[16];

void *sum_worker(void *a) {
    struct arg *p = (struct arg*)a;
    double s = 0;
    for (int i = p->lo; i < p->hi; i++) s += p->arr[i];
    p->result = s;
    return NULL;
}

// 呼叫端要 pthread_create × 16 + pthread_join × 16 + 手動計算 offset
```

OpenMP 讓同一件事變成：

```c
double s = 0;
#pragma omp parallel for reduction(+:s)
for (int i = 0; i < n; i++) s += arr[i];
```

**OpenMP 是 pthread 的高層抽象**，底層就是 pthread（或平台原生 thread）。學 OpenMP 的原因：
1. 從串列程式到平行程式的改動極小，先驗證邏輯正確
2. 適合 data-parallel 的科學計算 / HPC 模式（就是 CUDA kernel 的精神前身）
3. 可以用 `OMP_NUM_THREADS` 環境變數控制執行緒數，方便實驗

我們之後也會展示等價的 pthread 實作，讓你知道 OpenMP 幫你做了什麼。

---

## 二、OpenMP 基礎

### 2.1 parallel for

```c
#pragma omp parallel for
for (int i = 0; i < n; i++) {
    // 這個 body 被自動分配給多個 thread
}
```

OpenMP runtime 會：
1. 在 `#pragma` 前 fork 出 N-1 個 thread（加上主 thread = N 個）
2. 把迴圈迭代分配給這 N 個 thread
3. 在迴圈結束後 join（等所有 thread 完成）

指定執行緒數（如果不指定就用 `OMP_NUM_THREADS` 環境變數或 CPU 核心數）：

```c
#pragma omp parallel for num_threads(8)
for (int i = 0; i < n; i++) { ... }
```

確認執行緒數：

```c
#pragma omp parallel
{
    #pragma omp single
    printf("actual threads = %d\n", omp_get_num_threads());
}
```

**這台機器輸出**：
```
actual threads = 16
```

### 2.2 schedule：如何分配迭代

```c
// static：把 n 個迭代靜態分成 T 塊，thread i 拿第 i 塊
// 最低開銷，迭代均勻時最優
#pragma omp parallel for schedule(static)

// dynamic：每個 thread 完成一塊就來拿下一塊（chunk_size 預設 1）
// 適合不均勻工作（如稀疏矩陣的行有不同非零元素數量）
#pragma omp parallel for schedule(dynamic, 16)

// guided：塊大小從大到小遞減，平衡動態和靜態
#pragma omp parallel for schedule(guided)
```

大多數情況用 `static`。只有明確知道負載不均衡才用 `dynamic`（有明顯的 scheduling overhead）。

### 2.3 資料共享：private vs shared

```c
int x = 0;  // shared，所有 thread 看到同一個 x
int y;      // shared by default

#pragma omp parallel for private(y)
for (int i = 0; i < n; i++) {
    y = expensive_compute(i);  // private：每個 thread 有自己的 y
    arr[i] = y * 2;
}
```

`private(y)` 讓每個 thread 有自己的 `y` 副本，不同 thread 之間不互相干擾。

迴圈計數器（如 `i`）在 `parallel for` 中自動是 private。

---

## 三、Race Condition：故意跑出錯誤

這是多執行緒最危險的問題。我們直接跑出來看。

### 3.1 沒保護的累加

```c
// race_demo.c
// gcc -O0 -fopenmp race_demo.c -o race_demo.exe

#define N 10000000

int main() {
    long count;
    int runs = 5;

    printf("=== 有 race condition（未保護累加）===\n");
    for (int r = 0; r < runs; r++) {
        count = 0;
        #pragma omp parallel for num_threads(16) schedule(static, 1)
        for (int i = 0; i < N; i++) {
            count++;  // DATA RACE：多個 thread 同時讀改寫同一個 long
        }
        printf("  run %d: count=%ld  (差值=%ld)\n", r+1, count, (long)N - count);
    }
    ...
}
```

**真實輸出**（這台機器，每次跑都不同）：
```
=== 有 race condition（未保護累加）===
  run 1: count=778391  (差值=9221609)
  run 2: count=802648  (差值=9197352)
  run 3: count=878856  (差值=9121144)
  run 4: count=787276  (差值=9212724)
  run 5: count=926767  (差值=9073233)
```

我們的正確答案是 10,000,000，但實際結果只有不到 10%。**每次都不一樣，且每次都錯**。

### 3.2 為什麼 `count++` 不安全？

`count++` 看起來是一條語句，但它是三條機器指令：

```asm
mov rax, [count]   ; 1. 讀取 count 的值到 rax
add rax, 1         ; 2. 加 1
mov [count], rax   ; 3. 把結果寫回 count
```

假設 Thread A 和 Thread B 同時執行，count 初始值為 0：

```
時間  Thread A                    Thread B
──────────────────────────────────────────────────────
 1    mov rax, [count]  → rax=0
 2                                 mov rax, [count]  → rax=0
 3    add rax, 1        → rax=1
 4    mov [count], rax  → count=1
 5                                 add rax, 1        → rax=1
 6                                 mov [count], rax  → count=1  ← 應該是 2！
```

兩個 thread 都讀到 0，各自加 1，各自寫回 1。結果是 count=1，但應該是 2——一次增量丟失了。有 16 個 thread 同時做這件事，丟失的增量很可觀。

### 3.3 修正方法一：`#pragma omp atomic`

```c
#pragma omp parallel for num_threads(16)
for (int i = 0; i < N; i++) {
    #pragma omp atomic
    count++;  // 原子操作：RMW（read-modify-write）不可被中斷
}
```

`atomic` 讓 `count++` 變成硬體原子指令（x86 上是 `lock add [count], 1`）。保證正確，但有顯著效能開銷（每次都要鎖匯流排或至少 cache line 鎖）。

### 3.4 修正方法二：`reduction`（推薦）

```c
long count = 0;
#pragma omp parallel for reduction(+:count) num_threads(16)
for (int i = 0; i < N; i++) {
    count++;
}
```

`reduction(+:count)` 的做法：
1. 每個 thread 有自己的 private `count` 副本，初始化為 `+` 的單位元（0）
2. 每個 thread 只更新自己的 private 副本（完全沒有競爭）
3. 迴圈結束後，所有 private 副本被 reduce 到 master thread 的 `count`

**真實輸出**：
```
=== reduction 修正 ===
  reduction: count=10000000  (正確=10000000)
```

**效能比較**：

| 方法 | 正確性 | 效能 | 使用場景 |
|------|--------|------|---------|
| 無保護 | 錯誤 | 最快（但結果錯） | 永遠不用 |
| `atomic` | 正確 | 慢（每次都鎖） | 更新次數不多、邏輯複雜 |
| `reduction` | 正確 | 最快（無競爭） | 標準累加/乘積/min/max |
| mutex（`omp_set_lock`） | 正確 | 最慢 | 複雜邏輯（如 push 到共享列表）|

`reduction` 支援的運算子：`+`, `*`, `-`, `&`, `|`, `^`, `&&`, `||`, `max`, `min`。

---

## 四、False Sharing：看起來沒有競爭，但其實有

False sharing 是多執行緒效能問題中最隱蔽的一個，因為**程式邏輯上完全正確**，只是慢得離奇。

### 4.1 Cache Line 的基本知識

現代 CPU 的 cache 以 **cache line（快取行）** 為最小單位操作，x86 上是 **64 bytes**。

即使你只讀/寫 1 個 byte，CPU 也會把包含它的整條 64-byte cache line 載入 L1 cache。

**Cache 一致性（Cache Coherence）**：多核系統用 MESI 協議保證所有核心看到同一份資料。規則：
- 一個核心要**寫**某個 cache line → 必須先讓其他核心的副本 invalid（失效）
- 其他核心下次要讀這條 line → cache miss，重新從 L3 或記憶體載入

### 4.2 False Sharing 的場景

```c
// 四個 thread 各自計數，看似獨立
long counters[4];   // 4 × 8 bytes = 32 bytes < 64 bytes = 1 條 cache line！

#pragma omp parallel num_threads(4)
{
    int tid = omp_get_thread_num();
    for (long i = 0; i < N; i++) {
        counters[tid]++;  // 每個 thread 只碰自己的 counter
    }
}
```

邏輯上每個 thread 只寫自己的 `counters[tid]`，但這四個 counter 都在同一條 cache line 裡！

```
cache line (64 bytes):
[counters[0]] [counters[1]] [counters[2]] [counters[3]] [padding...]
   Thread 0      Thread 1      Thread 2      Thread 3
```

- Thread 0 寫 `counters[0]` → 讓其他所有核心的 cache line 失效
- Thread 1 要寫 `counters[1]` → cache miss（line 被 Thread 0 invalid 了）→ 重新載入
- Thread 1 寫完 → 讓 Thread 0、2、3 的 cache line 失效
- 如此循環，16 個 thread 不停地互相讓對方的 cache line 失效（Ping-Pong）

這個現象叫 False Sharing——資料邏輯上沒有共享，但因為在同一條 cache line 而造成的硬體層面的競爭。

### 4.3 真跑：展示 false sharing 的效能損失

```c
// false_sharing.c
// gcc -O2 -fopenmp false_sharing.c -o false_sharing.exe

#define N 500000000LL
#define THREADS 4

// 版本1: false sharing - 4 個 counter 在同一條 cache line
volatile long counters_bad[THREADS];

// 版本2: padded - 每個 counter 獨佔一條 cache line (64 bytes)
typedef struct { volatile long val; char pad[56]; } padded_long;
volatile padded_long counters_good[THREADS];
```

**真實輸出**（這台機器，多次執行，平均值）：
```
false sharing : total=500000000  time=0.291 s
padded        : total=500000000  time=0.223 s  speedup=1.30x

false sharing : total=500000000  time=0.306 s
padded        : total=500000000  time=0.213 s  speedup=1.43x

false sharing : total=500000000  time=0.312 s
padded        : total=500000000  time=0.214 s  speedup=1.46x
```

加 padding 後穩定快 30–46%，但注意 volatile 本身有開銷，實際場景的 false sharing 效應往往更明顯（尤其是高競爭場景）。

### 4.4 修正：讓每個 counter 獨佔一條 cache line

```c
// 方法1：struct padding
typedef struct {
    long val;
    char pad[56];  // 64 - sizeof(long) = 64 - 8 = 56 bytes padding
} padded_long;
padded_long counters[THREADS];

// 方法2：C11 aligned
_Alignas(64) long counters[THREADS][8];  // 每個 [i] 佔 8 long = 64 bytes

// 方法3：local 累加 + 最後合併（最乾淨）
#pragma omp parallel
{
    long local = 0;
    // ... 在本地累加 ...
    #pragma omp atomic
    global_total += local;  // 只在最後 atomic 一次
}
```

**最乾淨的解法是方法3**：用 OpenMP 的 `private` 或手動 local 變數，完全避免跨 core 存取同一 cache line，reduction 就是幫你自動做這件事的。

### 4.5 什麼時候真的要 padding

| 場景 | 需要 padding？ |
|------|--------------|
| OpenMP reduction | 不需要（框架處理） |
| 每個 thread 有自己的狀態結構 | 看 struct 大小，可能需要 |
| 全域陣列被不同 thread 以 stride 存取 | 需要，或重新排列資料 |
| 生產者 / 消費者的 ring buffer head/tail | 強烈需要（常見效能 bug）|

---

## 五、pthreads：理解 OpenMP 底下在做什麼

用 pthread 實作等效的 parallel reduction，讓你看到 OpenMP 幫你做了多少事：

```c
// pthread_reduction.c
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#define N     10000000
#define NTHREADS 16

long partial[NTHREADS];  // 每個 thread 的部分結果

struct args { int tid; int lo; int hi; };

void *worker(void *arg) {
    struct args *a = (struct args*)arg;
    long local = 0;
    for (int i = a->lo; i < a->hi; i++) local++;
    partial[a->tid] = local;  // 注意：不同 tid 寫不同 index，沒有 false sharing 嗎？
    // partial[0..15] 是 16×8 = 128 bytes，跨 2 條 cache line
    // 若 NTHREADS > 8，仍有 false sharing，但在這裡每個 thread 只寫一次，影響小
    return NULL;
}

int main() {
    pthread_t threads[NTHREADS];
    struct args args[NTHREADS];
    int chunk = N / NTHREADS;

    for (int i = 0; i < NTHREADS; i++) {
        args[i].tid = i;
        args[i].lo  = i * chunk;
        args[i].hi  = (i == NTHREADS-1) ? N : (i+1)*chunk;
        pthread_create(&threads[i], NULL, worker, &args[i]);
    }
    for (int i = 0; i < NTHREADS; i++) pthread_join(threads[i], NULL);

    // reduce
    long total = 0;
    for (int i = 0; i < NTHREADS; i++) total += partial[i];
    printf("total = %ld (expected %d)\n", total, N);
    return 0;
}
```

這 60 行做的事，OpenMP 用 3 行做到：
```c
long total = 0;
#pragma omp parallel for reduction(+:total)
for (int i = 0; i < N; i++) total++;
```

pthread 的價值在於：更細粒度控制（自訂 barrier、condition variable、自訂工作竊取策略），以及在 C 標準庫環境下不依賴 OpenMP 擴展。

---

## 六、OpenMP 的其他常用功能

### 6.1 parallel sections（不同 thread 做不同工作）

```c
#pragma omp parallel sections
{
    #pragma omp section
    {
        // Thread A 做這段
        compute_part_a(data);
    }
    #pragma omp section
    {
        // Thread B 同時做這段
        compute_part_b(data);
    }
}
```

### 6.2 barrier 和 single

```c
#pragma omp parallel
{
    // 所有 thread 先做 phase 1
    do_phase1(omp_get_thread_num());

    #pragma omp barrier  // 等所有 thread 完成 phase 1

    // 只有一個 thread 做 setup（其他等它完成後再繼續）
    #pragma omp single
    {
        setup_for_phase2();
    }
    // 隱含 barrier：single block 結束後所有 thread 繼續

    do_phase2(omp_get_thread_num());
}
```

### 6.3 omp_get_wtime：精確計時

```c
double t0 = omp_get_wtime();
// ... your code ...
double t1 = omp_get_wtime();
printf("%.4f seconds\n", t1 - t0);
```

### 6.4 nested parallelism

OpenMP 支援巢狀 parallel 區域，但預設關閉。實際上很少真的需要，因為 OpenMP 的 thread pool 通常在最外層就把核心用滿了。

---

## 七、OpenMP vs pthreads：選哪個

| 面向 | OpenMP | pthreads |
|------|--------|----------|
| 學習曲線 | 低 | 高 |
| data-parallel 迴圈 | 理想 | 繁瑣 |
| 細粒度控制（自訂 barrier、CV）| 有限 | 完整 |
| 移植性 | 好（C/C++/Fortran HPC 標準）| POSIX（Windows 需要 winpthreads 或改用 Win32 API）|
| thread-safe data structure | 不提供 | 不提供（要自己寫） |
| C++ 標準替代 | `std::thread` + `<algorithm>` parallel | `std::thread` + atomic |
| GPU CUDA 對應 | OpenMP offload（`omp target`）| 無 |

對本課的目的（建立平行思維，然後移植到 CUDA），OpenMP 是最對的工具。

---

## 踩雷集錦

**1. -O2 把 race condition 優化掉了**

加了 `-O2`，編譯器可能把 `count++` 優化成 register 操作（根本不回寫記憶體直到迴圈結束），race condition 的錯誤反而消失或變小。**想真實看到 race 的影響，要用 `-O0` 或加 `volatile`**。這也解釋了為什麼本章的 race demo 用 `-O0 -fopenmp`。

**2. reduction 子句裡變數要初始化**

```c
long result;  // 未初始化！
#pragma omp parallel for reduction(+:result)
```

`reduction(+:result)` 的 private 副本初始化為 `0`（`+` 的單位元），但最後 reduce 時會把所有 private 副本加到**原始的 result**。如果 `result` 未初始化，結果是 UB。要先寫 `long result = 0;`。

**3. `volatile` 和多執行緒的關係**

`volatile` **不是**同步機制。它只告訴編譯器「每次都去記憶體讀」，不提供任何 atomicity 或 ordering 保證。在多 thread 程式裡用 `volatile` 替代 atomic 或 mutex → 錯的。要用 `_Atomic`（C11）或 OpenMP atomic/reduction，或 pthread mutex。

**4. `#pragma omp parallel for` vs `#pragma omp parallel`**

```c
// 這是平行執行的迴圈（常見用法）
#pragma omp parallel for
for (...) { ... }

// 這只是 parallel region，所有 thread 都執行這個 block（包含整個 for）
// 沒有 for 的分配，每個 thread 跑完整的 for → N倍工作量
#pragma omp parallel
for (...) { ... }  // ← bug：每個 thread 都跑全部 N 次迭代
```

忘記加 `for` 是初學者最常犯的錯。

**5. false sharing 在 debug build 消失**

`-O0` 下，local 變數不一定在 register 裡，cache line 的模式不同，false sharing 效應可能和 `-O2` 差很多。做效能測量一定要用 `-O2`。

---

## 動手練習

1. 把 Ch 4 的 `dot_avx2` 和 OpenMP 結合，實作 `dot_omp_avx2`（每個 thread 處理一段，各自做 AVX2 水平 reduction，最後再彙總）。量測比 `dot_avx2`（單核）快多少。

2. 實作一個 parallel histogram：把一個 `int` 陣列按值域 [0, 255] 分 256 個桶，計算每個桶的出現次數。naive 版本（直接更新全域 hist[]）會有 race；用 private histogram + merge 修正。量測兩版本的差異。

3. 設計一個 false sharing 最嚴重的場景（每個 thread 高頻寫入相鄰 cache line 位置），量測和 padded 版本的差距。試試 THREADS = 2, 4, 8, 16，差距如何變化？

---

## 本章重點

- OpenMP `#pragma omp parallel for` 把迴圈分給 N 個 thread；`reduction(+:x)` 讓每個 thread 有 private 副本，最後合併，安全又快。
- `count++` 是 read-modify-write 三步，多 thread 同時做 → race condition。這台機器實測：10M 迭代，結果只對 8–15%。`reduction` 修正後 100% 正確。
- False sharing：邏輯上不共享，但在同一條 cache line → 互相 invalid。修法：`char pad[56]` 讓每個 counter 獨佔 cache line，或用 local 變數 + 最後一次 atomic。
- pthreads 是 OpenMP 底下的基礎，給你更細粒度的控制，代價是更多樣板程式碼。
- `volatile` ≠ atomic，不能用來替代同步。

---

## 自我檢核

1. `count++` 為什麼不是原子操作？把它分解成機器指令說明。
2. OpenMP `reduction(+:s)` 的實作機制是什麼？它和 `atomic` 的效能差別源自哪裡？
3. 解釋 false sharing：為什麼兩個 thread 寫完全不同的 `long` 變數還是會互相影響？
4. `cache line` 是幾個 bytes？如果要讓一個 struct 獨佔一條 cache line，需要 padding 到多少？
5. 在什麼情況下應該用 pthread 而不是 OpenMP？

---

## 延伸閱讀

1. **OpenMP API Specification** (openmp.org/specifications)  
   讀哪裡：「3. OpenMP Directives」中的 `parallel`, `for`, `reduction`, `atomic`, `barrier`。前提：懂 C。關聯：本章所有 pragma 的權威定義，碰到不確定的語意去查這裡。

2. **"What Every Programmer Should Know About Memory" (Ulrich Drepper, Part 5)**  
   讀哪裡：Section 5「What Programmers Can Do」→ 5.2「Critical Word Load and Cache Line Sharing」。前提：讀過 Part 2（cache 基礎）。關聯：false sharing 的完整理論解釋和更多例子，本章 false sharing 段落的學術背景。

3. **《Computer Systems: A Programmer's Perspective》Ch 12**  
   「Concurrent Programming」：Semaphore, mutex, condition variable, race condition, deadlock。前提：知道基本 C 和 process 概念。關聯：從 OS 角度解釋同步原語，是本章 pthreads 部分的理論補充。

4. **Intel VTune Profiler — Memory Access Analysis**  
   docs.intel.com/content/www/us/en/docs/vtune-profiler/  
   讀哪裡：「False Sharing Detection」。前提：有 Intel VTune 或 perf 工具。關聯：學會用工具找 false sharing 熱點，比憑感覺更有效率。

5. **Herb Sutter, "C++ and Beyond 2012: Atomic Weapons"** (YouTube)  
   讀哪裡：整個演講（2部分，各90分鐘）。前提：懂 C++ 和多執行緒概念。關聯：深挖記憶體順序（memory ordering）、acquire/release、data race 的精確定義——本章只觸碰表面，要真正搞懂 C11 atomics 和多執行緒的正確性，這個演講是必看的。

---

我們現在能寫正確且快速的多執行緒程式了。下一章把這些工具整合到更高層次：五個平行 pattern，每個都有直覺 + CPU 實作 + GPU 對應。

→ [Ch 6 平行 pattern 導論：map / reduce / scan / stencil / gather-scatter](./06-parallel-patterns.md)
