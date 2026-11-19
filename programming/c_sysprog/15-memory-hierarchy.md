# Ch 15 — 記憶體階層與虛擬記憶體

> **目標**：打破「記憶體是一條平坦、每格存取成本相同的大陣列」這個心智模型。學完後你能用 cache line、局部性、TLB、page、demand paging、overcommit 解釋同一段 C 程式為什麼會慢 17 倍，也知道 `malloc` 回傳的指標背後到底拿到了什麼。

> **對應原文**：[你所不知道的 C 語言：記憶體管理、對齊及硬體特性篇](https://hackmd.io/@sysprog/c-memory) 的「你可能沒想過的 Memory」「重新看 Heap」等節；以及 [函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「藏在 Heap 裡的細節」。

> **環境**：本章所有數字都在同一台機器上實測：AMD Ryzen 7 5700G（Zen 3，8 核 16 執行緒），L1d 32 KiB/核（8-way）、L2 512 KiB/核（8-way）、L3 16 MiB 共用，cache line 64 bytes（`getconf -a | grep CACHE`）；Windows 11 上的 WSL2（Hyper-V 虛擬機，kernel 6.18），Ubuntu 22.04、gcc 11.4、glibc 2.35、page 大小 4096 bytes。**效能數字只對這台機器成立**，換一台機器比例會變，但趨勢不會。

## 為什麼需要這個？

[Ch 3](./03-pointer-basics.md) 教指標時，我們把記憶體畫成一排編了號的格子：位址 `0x1000` 放一個 byte，`0x1001` 放下一個。這個模型對「理解指標」夠用，對「理解效能」卻完全錯誤。看這兩段程式：

```c
for (i = 0; i < N; i++) for (j = 0; j < N; j++) sum += a[i][j];   /* A */
for (j = 0; j < N; j++) for (i = 0; i < N; i++) sum += a[i][j];   /* B */
```

兩段做的加法次數一模一樣，演算法複雜度一樣是 O(N²)。在本機 `N = 4096`、`-O2` 下，A 跑 0.005 秒，B 跑 0.089 秒——**差 17 倍**。差別只在「存取記憶體的順序」。

這個現象的根源是一段歷史：1980 年代初期，CPU 時脈和 DRAM 存取速度差不多，一次記憶體存取就是一兩個時脈，早期的 8086 根本沒有 cache。之後二十多年，CPU 效能每年成長數十個百分點，DRAM 延遲每年只改善個位數百分比（Hennessy & Patterson《Computer Architecture》的經典對照圖就是這個量級），兩者的差距被稱為 **memory wall**（Wulf 與 McKee 1995 年的用語）。今天一次 DRAM 存取要上百奈秒，CPU 在這段時間可以執行數百條指令。硬體的解法是在 CPU 和 DRAM 之間塞進好幾層越來越小、越來越快的 **cache（快取）**，再加上一層 **虛擬記憶體（virtual memory）** 讓每個行程以為自己獨占整個位址空間。

C 語言把這一切藏起來：規格書只說物件有位址、可以存取，完全不談 cache 和 page。但 C 程式設計師離硬體最近，寫出 cache 不友善的程式，付出代價的也是我們。

## 先建立直覺

想像你在圖書館寫報告：

- 手上正在讀的那一頁 = **暫存器（register）**，零成本
- 桌上攤開的幾本書 = **L1 cache**，伸手就拿到（約 1 ns）
- 身旁推車上的書 = **L2**（約 4 ns）
- 同一層樓的書架 = **L3**（十幾 ns）
- 地下書庫，要填單請館員去拿 = **DRAM**（上百 ns）
- 別館，要等館際互借 = **磁碟 / swap**（數十微秒到毫秒）

館員去書庫時**不會只拿一頁，而是整本書搬上來**——這就是 cache line：CPU 從 DRAM 搬資料的最小單位是 64 bytes，不是 1 byte。所以讀完 `a[0]` 再讀 `a[1]` 幾乎免費（同一本書），但跳著讀每本書的第一頁，每次都要跑一趟書庫。

```
            容量        延遲（本機實測，見下節）
  ┌──────┐
  │ reg  │  ~數百 B     0
  ├──────┴─┐
  │  L1d   │  32 KiB    ~1 ns
  ├────────┴─┐
  │   L2     │  512 KiB  ~4-12 ns
  ├──────────┴──┐
  │    L3       │  16 MiB  ~15-40 ns
  ├─────────────┴─────┐
  │      DRAM         │  22 GiB  ~110-200 ns（含 TLB miss）
  └───────────────────┘
       越往下：越大、越慢、越便宜
```

這個比喻在「容量與延遲的階層」上成立；它**不成立**的地方是：真正的 cache 由硬體自動管理，你不能指定「把這本書放桌上」，只能靠存取模式間接影響它。

## 量測一：用 pointer chasing 畫出階層

要量「一次載入」的延遲，最大的敵人是 CPU 的平行性：現代 CPU 會同時發出多個載入、硬體預取器（prefetcher）會猜你下一步要哪裡。破解方法是 **pointer chasing**：把節點串成一個隨機順序的環，每次載入的位址都來自上一次載入的結果，CPU 沒辦法提前發出下一個請求，也猜不到位址。

```c
/* chase.c: 隨機 pointer chasing，量測不同工作集大小下每次載入的延遲 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define LINE 64                         /* cache line 大小，見 getconf LEVEL1_DCACHE_LINESIZE */
#define STEPS (20 * 1000 * 1000)

struct node { struct node *next; char pad[LINE - sizeof(struct node *)]; };

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(void)
{
    for (size_t bytes = 4 << 10; bytes <= (256u << 20); bytes *= 2) {
        size_t n = bytes / sizeof(struct node);
        struct node *v = aligned_alloc(LINE, n * sizeof(struct node));
        size_t *perm = malloc(n * sizeof(size_t));
        for (size_t i = 0; i < n; i++) perm[i] = i;
        for (size_t i = n - 1; i > 0; i--) {          /* Fisher-Yates 洗牌 */
            size_t j = (size_t)rand() % (i + 1);
            size_t t = perm[i]; perm[i] = perm[j]; perm[j] = t;
        }
        for (size_t i = 0; i < n; i++)                /* 串成一個隨機順序的環 */
            v[perm[i]].next = &v[perm[(i + 1) % n]];

        struct node *p = &v[perm[0]];
        for (size_t i = 0; i < n; i++) p = p->next;   /* 暖身 */
        double t0 = now();
        for (int i = 0; i < STEPS; i++) p = p->next;  /* 每次載入都依賴上一次的結果 */
        double t = now() - t0;
        printf("%9zu KiB  %6.2f ns/load  (%p)\n", bytes >> 10,
               t * 1e9 / STEPS, (void *)p);           /* 印 p 防止迴圈被最佳化掉 */
        free(perm);
        free(v);
    }
    return 0;
}
```

每個節點刻意做成 64 bytes（一條 cache line），確保每一步都碰到不同的 line。`gcc -O2 chase.c -o chase && ./chase`：

```
        4 KiB    1.46 ns/load  (0x5943aceb4440)
        8 KiB    1.03 ns/load  (0x5943aceb8380)
       16 KiB    1.10 ns/load  (0x5943acebc580)
       32 KiB    2.62 ns/load  (0x5943acec2ac0)
       64 KiB    3.67 ns/load  (0x5943acec7840)
      128 KiB    4.47 ns/load  (0x73337e5f3f40)
      256 KiB    7.92 ns/load  (0x73337e5e7b00)
      512 KiB   12.70 ns/load  (0x73337e5c5880)
     1024 KiB   15.01 ns/load  (0x73337e60c340)
     2048 KiB   16.24 ns/load  (0x73337e199740)
     4096 KiB   38.68 ns/load  (0x73337e08f600)
     8192 KiB  112.73 ns/load  (0x73337de7fe80)
    16384 KiB  131.43 ns/load  (0x73337d2d9980)
    32768 KiB  139.92 ns/load  (0x73337d610040)
    65536 KiB  139.92 ns/load  (0x73337b220480)
   131072 KiB  159.27 ns/load  (0x73337825efc0)
   262144 KiB  223.20 ns/load  (0x7333731f1c40)
```

（位址每次執行不同。）讀這張表的方法：

- **≤ 16 KiB**：約 1 ns，全部落在 L1d（32 KiB）。4 KiB 那列偏高是量測起頭的雜訊。
- **32 KiB–512 KiB**：爬升到 3–13 ns。工作集剛好 32 KiB 時已經開始和其他資料（stack、程式碼用到的資料）搶 L1，所以不是剛好在 32 KiB 斷崖。
- **1–2 MiB**：約 15 ns，L3 的範圍。
- **4 MiB 之後**：陡升到上百 ns。注意 L3 有 16 MiB，但延遲在 4–8 MiB 就暴增——這不全是 DRAM 的錯，還有 **TLB miss**（下面會解釋）。
- **256 MiB**：223 ns，DRAM 延遲 + 頁表走訪（page walk）的成本。

還有一個彩蛋：位址在 128 KiB 那列從 `0x5943...` 跳到 `0x7333...`。前者是 `brk` 延展的 heap，後者是 `mmap` 區域——glibc 對大於 128 KiB 的配置改用 `mmap`。本章最後一節和 [Ch 17](./17-malloc-internals.md) 會回來解釋。

> **認識論誠實**：這是在 Hyper-V 虛擬機裡量的。虛擬機的 TLB miss 要走「兩層頁表」（guest 的頁表 + hypervisor 的 nested page table），成本比實體機高。實體 Linux 上大工作集的數字通常會低一些。表格反覆執行的波動約 ±15%。

## 底層機制：cache 怎麼找到你的資料？

### cache line 與位址切分

L1d 有 32 KiB、8-way set associative、64 bytes 一條 line，算一下：32768 / 64 = 512 條 line，分成 512 / 8 = 64 個 **set**，每個 set 可放 8 條 line。CPU 拿到一個（實體）位址時，把它切成三段：

```
  位址（低位在右）
  ┌──────────────────────┬──────────┬──────────┐
  │         tag          │ set index│  offset  │
  │  （剩下的高位元）     │  6 bits  │  6 bits  │
  └──────────────────────┴──────────┴──────────┘
                           2^6 = 64 sets   2^6 = 64 bytes/line

  查詢流程：用 set index 選出一個 set → 和 set 裡 8 條 line 的 tag 比對
           → 命中（hit）就用 offset 取出 byte；沒命中（miss）就往下一層要整條 line
```

這解釋了三件事：

1. **為什麼搬運單位是 64 bytes**：offset 的 6 bits 就是 line 內的位置。讀 `a[0]`（4 bytes）時，`a[0]`–`a[15]` 一起被搬進來。
2. **為什麼有「衝突」**：位址相差 64 × 64 = 4096 的倍數，set index 一樣，只能搶同一個 set 的 8 個位子。跳著存取 2 的冪次的距離，容易造成 **conflict miss**。
3. **cache miss 的三種成因（3C）**：**compulsory**（第一次碰，不可避免）、**capacity**（工作集比 cache 大）、**conflict**（容量還夠，但都擠在同一個 set）。

寫入時多數現代 CPU 用 **write-back**：先只改 cache 裡的 line 並標記為 dirty，等被趕出去才寫回下一層。所以「寫一個 byte」實際上也要先把整條 line 讀進來（write-allocate）。

### 硬體預取器

CPU 會偵測「你在循序或固定間隔地走」，提前把下幾條 line 搬進來。這是循序存取快的第二個原因：不只一條 line 攤平了 16 次存取，連那條 line 的延遲都被提前藏起來了。pointer chasing 要隨機化，正是為了打敗它。

## 局部性：row-major vs column-major

**局部性（locality）** 分兩種：**時間局部性**（剛用過的很快會再用）與**空間局部性**（用了這裡，很快會用旁邊）。cache 的整個設計都在押寶這兩件事。

C 的多維陣列是 **row-major**：N1570 6.5.2.1 ¶3–4 規定 `a[i][j]` 就是 `*(*(a + i) + j)`，`a[i]` 是一整列（一個 `int[N]` 陣列），所以同一列的元素在記憶體裡相鄰，下一列接在後面。

```
  int a[3][4] 的記憶體排列（每格 4 bytes）
  位址 →
  ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
  │a00 │a01 │a02 │a03 │a10 │a11 │a12 │a13 │a20 │a21 │a22 │a23 │
  └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
   row-major 走法：→ → → → → →（每步 +4 bytes）
   column-major 走法：a00 → a10 → a20 → a01 ...（每步 +16 bytes；N=4096 時每步 +16 KiB）
```

> 陣列與指標的關係如果還模糊，回看 [Ch 8 — 陣列、函式、指標](./08-arrays-functions-pointers.md) 的「array decay」一節。

```c
/* rowcol.c: row-major vs column-major 走訪同一個 N x N 的 int 陣列 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define N 4096                 /* 4096 * 4096 * 4 bytes = 64 MiB，遠大於 L3 (16 MiB) */

static int a[N][N];

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(void)
{
    for (int i = 0; i < N; i++)            /* 先寫一遍，讓 page 都真的配置好 */
        for (int j = 0; j < N; j++)
            a[i][j] = i + j;

    long long sum = 0;
    double t0 = now();
    for (int i = 0; i < N; i++)            /* row-major：j 在內層，位址連續 */
        for (int j = 0; j < N; j++)
            sum += a[i][j];
    double t1 = now();
    for (int j = 0; j < N; j++)            /* column-major：i 在內層，每步跳 N*4 bytes */
        for (int i = 0; i < N; i++)
            sum += a[i][j];
    double t2 = now();

    printf("row-major   : %.3f s\n", t1 - t0);
    printf("column-major: %.3f s\n", t2 - t1);
    printf("ratio       : %.1fx   (sum=%lld)\n", (t2 - t1) / (t1 - t0), sum);
    return 0;
}
```

本機各跑三次，取有代表性的一次：

```
== -O0
row-major   : 0.017 s
column-major: 0.074 s
ratio       : 4.3x   (sum=137405399040)
== -O2
row-major   : 0.005 s
column-major: 0.089 s
ratio       : 16.9x   (sum=137405399040)
```

（`-O0` 三次的比值落在 4.1–4.9 倍，`-O2` 落在 16.7–17.2 倍。）

為什麼 `-O2` 差距反而更大？`-O0` 時每次加法都要從 stack 載入 `i`、`j`、`sum` 再存回去，迴圈本身的開銷蓋過了記憶體成本，兩種走法都慢。`-O2` 把 `sum` 和指標都放進暫存器，`gcc -O2 -S rowcol.c` 可以看到兩個內層迴圈幾乎一模一樣，都只有 5 條指令：

```
.L11:                          # row-major 內層
	movslq	(%rax), %rdx
	addq	$4, %rax           # 指標每次 +4 bytes
	addq	%rdx, %r12
	cmpq	%rcx, %rax
	jne	.L11

.L14:                          # column-major 內層
	movslq	(%rax), %rdx
	addq	$16384, %rax       # 指標每次 +16384 bytes（一整列）
	addq	%rdx, %r12
	cmpq	%rcx, %rax
	jne	.L14
```

指令數相同，唯一差別是那個常數 4 和 16384。row-major 每 16 次載入才碰一條新 line，而且是預取器最愛的循序模式；column-major 每次載入都是新的 line，只用到其中 4 bytes，剩下 60 bytes 白搬。迴圈開銷被最佳化掉之後，剩下的就是純粹的記憶體成本，差距因此被放大。（gcc 11 在 `-O2` 不做自動向量化，要 `-O3` 或 gcc 12 以後才會；向量化會讓 row-major 更快、差距更大，見 [Ch 37](./37-compiler-optimization.md)。）這是個值得記住的規律：**最佳化越好，記憶體存取模式的影響越凸顯**。

## 量測二：stride 存取

把局部性量化：對 64 MiB 的陣列，每隔 `stride` 個 `int` 做一次讀改寫。

```c
/* stride.c: 對 64 MiB 的 int 陣列，每隔 stride 個元素做一次讀改寫 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define LEN (16 * 1024 * 1024)       /* 16M 個 int = 64 MiB */

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(void)
{
    int *arr = malloc(sizeof(int) * LEN);
    for (long i = 0; i < LEN; i++) arr[i] = (int)i;

    printf("%8s %10s %10s %12s\n", "stride", "bytes", "time(ms)", "ns/access");
    for (long stride = 1; stride <= 4096; stride *= 2) {
        double best = 1e9;
        for (int rep = 0; rep < 5; rep++) {          /* 取 5 次最好的一次 */
            double t0 = now();
            for (long i = 0; i < LEN; i += stride)
                arr[i] *= 3;
            double t = now() - t0;
            if (t < best) best = t;
        }
        long accesses = LEN / stride;
        printf("%8ld %10ld %10.2f %12.2f\n", stride, stride * 4,
               best * 1e3, best * 1e9 / accesses);
    }
    free(arr);
    return 0;
}
```

`gcc -O2`：

```
  stride      bytes   time(ms)    ns/access
       1          4      11.16         0.67
       2          8      10.42         1.24
       4         16       8.90         2.12
       8         32       8.12         3.87
      16         64       8.22         7.84
      32        128       4.80         9.16
      64        256       2.70        10.30
     128        512       2.22        16.96
     256       1024       0.98        15.01
     512       2048       0.53        16.13
    1024       4096       0.22        13.52
    2048       8192       0.10        11.96
    4096      16384       0.03         7.12
```

最有教育意義的是 **time(ms)** 欄：stride 從 1 到 16（4 → 64 bytes），存取次數少了 16 倍，**總時間幾乎不變**（11 → 8 ms）。因為不管 stride 是多少，只要 ≤ 64 bytes，每條 cache line 都要搬一次，總共要搬的 line 數一樣：64 MiB / 64 = 1M 條。成本是「搬 line」，不是「做乘法」。stride 超過 64 bytes 之後，每次存取各搬一條不同的 line，總時間才開始跟著存取次數減半。

最後幾列 ns/access 又變小，是量測方法的副作用：stride 4096 時只碰 4096 條 line（256 KiB），裝得進 L2，「取 5 次最好」的後幾次全是 cache hit。讀 benchmark 一定要問：**我量到的是我以為的東西嗎？**

## 虛擬記憶體與 page

到目前為止我們說的「位址」，其實有兩種。你在程式裡印出來的 `0x7333...` 是**虛擬位址（virtual address）**；DRAM 晶片認得的是**實體位址（physical address）**。中間由 CPU 的 **MMU（Memory Management Unit）** 依照作業系統維護的**頁表（page table）** 翻譯。

翻譯的單位是 **page（頁）**：x86-64 Linux 預設 4096 bytes（`getconf PAGESIZE`）。4096 = 2¹²，所以位址的低 12 bits 是 page 內的 offset，翻譯時原封不動；高位元才查表。x86-64 目前用 48-bit 虛擬位址（`lscpu` 的 `Address sizes: 48 bits virtual`），分四層頁表：

```
  48-bit 虛擬位址
  ┌─────────┬─────────┬─────────┬─────────┬──────────────┐
  │ PML4 9b │ PDPT 9b │  PD 9b  │  PT 9b  │  offset 12b  │
  └────┬────┴────┬────┴────┬────┴────┬────┴──────────────┘
       │         │         │         │
  CR3 ─┴─> 表 ───┴─> 表 ───┴─> 表 ───┴─> 實體 page 框（frame）+ offset

  每層 9 bits = 512 個 8-byte 項目 = 剛好一個 4 KiB page
  一次 TLB miss 最多要讀 4 次記憶體才拿到翻譯結果
```

每次存取都走四層表太慢，所以 MMU 有一個專用的小 cache：**TLB（Translation Lookaside Buffer）**，記住最近用過的「虛擬 page → 實體 frame」對應。TLB 項目有限——就我所知 Zen 3 的 L2 DTLB 約 2048 項，乘上 4 KiB 剛好覆蓋 8 MiB。這和 pointer chasing 在 4–8 MiB 暴增的位置吻合：超過 TLB 能覆蓋的範圍，每次隨機存取除了 cache miss，還多一次 page walk。

### 驗證：huge page 能減少 TLB miss

如果一個 TLB 項目能覆蓋 2 MiB 而不是 4 KiB，同樣的 TLB 就能覆蓋 512 倍的範圍。Linux 的 **Transparent Huge Pages（THP）** 能在 2 MiB 對齊的區域使用 2 MiB page。本機 THP 設定是 `madvise`（`cat /sys/kernel/mm/transparent_hugepage/enabled` 顯示 `always [madvise] never`），要對區域呼叫 `madvise(p, len, MADV_HUGEPAGE)` 才會啟用。把 chase.c 改成 2 MiB 對齊配置、依環境變數決定是否 `madvise`（完整改法見本章「動手練習」），同一次執行的結果：

```
                 4 KiB page        THP (MADV_HUGEPAGE)
     4096 KiB    29.06 ns/load     14.77 ns/load
     8192 KiB   119.05 ns/load     84.08 ns/load
    65536 KiB   160.22 ns/load    130.80 ns/load
   262144 KiB   180.47 ns/load    132.47 ns/load
```

大工作集下延遲降了約 20–30%；重跑幾次，各列波動不小（例如 8 MiB 那列 THP 版跑出過 27 ns 和 98 ns），但「THP 版較快」的方向每次都一樣。剩下的延遲才是 DRAM 本身。這也是資料庫、JVM 常建議開 huge page 的原因——但 THP 也有代價（記憶體浪費、背景整理 khugepaged 造成延遲抖動），所以很多資料庫反而建議關掉 `always` 模式。這是要量測後才能決定的取捨。

## Demand paging：malloc 給的是一張支票

原文有個好比喻：**虛擬記憶體像銀行**。銀行不會把每個存戶的錢都放在金庫裡，賭的是大家不會同時領錢；Linux 也不會在 `malloc` 時就準備好實體記憶體，賭的是你不會全部用到。真正用到時才配置，叫 **demand paging**：

```c
/* demand.c: malloc 拿到的是「承諾」，碰到 page 時才真的配置實體記憶體 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

static void report(const char *tag)
{
    char line[256];
    long rss_kb = -1;
    FILE *f = fopen("/proc/self/status", "r");
    while (fgets(line, sizeof line, f))
        if (strncmp(line, "VmRSS:", 6) == 0) rss_kb = atol(line + 6);
    fclose(f);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    printf("%-22s VmRSS=%8ld KiB  minor faults=%ld\n", tag, rss_kb, ru.ru_minflt);
}

int main(void)
{
    size_t sz = 1UL << 30;                      /* 1 GiB */
    report("start");
    char *p = malloc(sz);
    report("after malloc(1 GiB)");
    for (size_t i = 0; i < sz / 2; i += 4096)   /* 每個 4 KiB page 寫 1 byte，只碰前半 */
        p[i] = 1;
    report("after touching 512 MiB");
    free(p);
    report("after free");

    char *huge = malloc(64UL << 30);            /* 64 GiB，遠超過 RAM + swap */
    printf("malloc(64 GiB) = %p\n", (void *)huge);
    free(huge);
    return 0;
}
```

```
start                  VmRSS=    1360 KiB  minor faults=609
after malloc(1 GiB)    VmRSS=    1688 KiB  minor faults=614
after touching 512 MiB VmRSS=  525972 KiB  minor faults=131685
after free             VmRSS=    1684 KiB  minor faults=131685
malloc(64 GiB) = 0x7aadf43ff010
```

逐行讀：

- `malloc(1 GiB)` 之後 **RSS（Resident Set Size，實際占用的實體記憶體）** 幾乎沒變。核心只在行程的位址空間登記了「這段虛擬位址是你的」。
- 碰前半之後，minor fault 多了 131685 − 614 = 131071 次，約等於 512 MiB / 4 KiB = 131072 個 page。每個 page 第一次被碰時 CPU 觸發 **page fault**，核心這才找一個實體 frame、清零、填進頁表。不需要讀磁碟的 fault 叫 **minor fault**。
- `free` 之後 RSS 立刻掉回去：1 GiB 的配置走的是 `mmap`，`free` 直接 `munmap` 還給核心。
- 64 GiB 的 `malloc` 居然成功了。本機 RAM 22 GiB、swap 6 GiB。

### overcommit：核心願意開多少空頭支票？

最後一行取決於 `/proc/sys/vm/overcommit_memory`：

| 值 | 名稱 | 行為 |
|---|---|---|
| 0 | heuristic（Linux 預設） | 明顯離譜的請求（遠超過 RAM + swap）直接拒絕，其餘放行 |
| 1 | always | 永遠答應，用到不夠才算帳 |
| 2 | never（嚴格記帳） | 總承諾量不得超過 `CommitLimit`（swap + RAM × `overcommit_ratio`%） |

本機的 WSL2 預設是 **1**（`cat /proc/sys/vm/overcommit_memory` 印出 `1`），這和一般 Ubuntu 的預設 0 不同。以 root 暫時切換模式，重跑同一支程式的最後一行（跑完已改回 1）：

```
== overcommit_memory=0
malloc(64 GiB) = (nil)
== overcommit_memory=2
malloc(64 GiB) = (nil)
== overcommit_memory=1
malloc(64 GiB) = 0x7d3c253ff010
```

在 0 和 1 模式下，`malloc` 成功**不代表**之後用得到。真的寫到實體記憶體耗盡時，核心的 **OOM killer** 會挑一個行程（通常是占最多記憶體的）送 `SIGKILL`——這時 `malloc` 早就回傳過了，程式沒有任何機會檢查錯誤。這就是原文說的「支票能否兌現要到動用時才知道」。

### 另一張支票：copy-on-write

`fork()` 複製整個行程，但核心不會真的複製全部記憶體：父子行程先共用相同的實體 page，標記為唯讀；任何一方寫入時觸發 page fault，核心才複製那一頁。這叫 **copy-on-write（COW）**。原文提到的 `mmap(MAP_PRIVATE)` 也是同一套機制。

```c
/* cow.c: fork 之後父子共用 page，寫入才複製（copy-on-write） */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/resource.h>

static long minflt(void) { struct rusage r; getrusage(RUSAGE_SELF, &r); return r.ru_minflt; }

int main(void)
{
    size_t sz = 64UL << 20;                    /* 64 MiB = 16384 個 4 KiB page */
    char *buf = malloc(sz);
    memset(buf, 1, sz);                        /* 父行程先把 page 都配好 */
    pid_t pid = fork();
    if (pid == 0) {
        long f0 = minflt();
        long sum = 0;
        for (size_t i = 0; i < sz; i += 4096) sum += buf[i];       /* 只讀 */
        long f1 = minflt();
        for (size_t i = 0; i < sz; i += 4096) buf[i] = 2;          /* 寫入 */
        long f2 = minflt();
        printf("child: read 16384 pages -> %ld faults, write 16384 pages -> %ld faults (sum=%ld)\n",
               f1 - f0, f2 - f1, sum);
        fflush(stdout);
        _exit(0);
    }
    waitpid(pid, NULL, 0);
    printf("parent still sees buf[0] = %d\n", buf[0]);
    return 0;
}
```

```
child: read 16384 pages -> 0 faults, write 16384 pages -> 16384 faults (sum=16384)
parent still sees buf[0] = 1
```

讀不觸發 fault（共用），寫入每頁剛好一次 fault（複製），父行程看到的資料不受影響。

Linux 另外提供幾個控制 page 行為的 API，原文有列出：`mlock` 要求某段記憶體不被換出（受 `RLIMIT_MEMLOCK` 限制，核心不一定同意）、`madvise` 告訴核心存取模式（如 `MADV_RANDOM` 關掉預讀、`MADV_HUGEPAGE` 如上）。這些是 Linux 特有的，不屬於 C 標準。

## 重新看 heap：brk 與 mmap

> 行程位址空間的整體布局（text、data、bss、heap、mmap 區、stack）在 [Ch 11 — 行程記憶體布局](./11-process-layout-and-abi.md) 講過，這裡只聚焦 heap。

先釐清名詞：這裡的 heap（台灣譯「堆積」）和資料結構的 heap（priority queue）**無關**。Knuth 在 TAOCP 第一卷提到大約 1975 年起有人把「可用記憶體池」叫做 heap，取的是英文「一堆東西」的原意。

`malloc` 底層向核心要記憶體有兩條路：

1. **`brk` / `sbrk`**：移動 **program break**（data 段尾端的那條界線）。heap 就是 break 往上推出來的連續區域。
2. **`mmap`**：在位址空間另找一塊獨立區域。

```c
/* brkmmap.c: 小配置走 brk 延展的 heap，大配置走獨立的 mmap */
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void)
{
    void *brk0 = sbrk(0);
    char *small = malloc(100);
    void *brk1 = sbrk(0);
    char *big = malloc(200 * 1024);             /* 200 KiB > 預設 mmap threshold 128 KiB */
    void *brk2 = sbrk(0);

    printf("program break 起點   : %p\n", brk0);
    printf("malloc(100)          : %p\n", (void *)small);
    printf("break 之後           : %p  (+%ld KiB)\n", brk1,
           ((char *)brk1 - (char *)brk0) / 1024);
    printf("malloc(200 KiB)      : %p\n", (void *)big);
    printf("break 之後           : %p  (沒動)\n", brk2);
    free(big);
    free(small);
    return 0;
}
```

用 `strace -e trace=brk,mmap,munmap ./brkmmap` 同時看系統呼叫（省略前面動態連結器的 `mmap`）：

```
brk(NULL)                               = 0x6154b0334000
brk(0x6154b0355000)                     = 0x6154b0355000
mmap(NULL, 208896, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x77f86507a000
munmap(0x77f86507a000, 208896)          = 0
program break 起點   : 0x6154b0334000
malloc(100)          : 0x6154b03342a0
break 之後           : 0x6154b0355000  (+132 KiB)
malloc(200 KiB)      : 0x77f86507a010
break 之後           : 0x6154b0355000  (沒動)
```

幾個值得追問的數字：

- **為什麼要 100 bytes，break 卻推了 132 KiB？** glibc 每次延展 heap 會多要 `M_TOP_PAD`（預設 128 KiB，見 `man 3 mallopt`），再向上取整到 page，省得每次 `malloc` 都做一次系統呼叫。系統呼叫要切換到核心態，成本比一般函式呼叫高很多。
- **為什麼第一塊在 break 起點 + `0x2a0`？** 前面 `0x290` bytes 是 glibc 的 per-thread cache 管理結構（tcache），加上 16 bytes chunk header。[Ch 17](./17-malloc-internals.md) 會拆開它。
- **為什麼 `mmap` 了 208896 bytes？** 200 KiB = 204800，加 16 bytes header 後向上取整到 4096 的倍數 = 208896。`mmap` 只能以 page 為單位。
- **`free(big)` 立刻 `munmap`**：mmap 出來的區塊可以單獨還給核心；brk heap 只能從頂端縮，中間有洞也還不回去。

### 為什麼大陣列放 stack 會 segfault？

原文舉了一個例子：`long arr[n + 1]` 這個 VLA 在 n = 10⁷ 時 segfault，改用 `malloc` 卻正常。

```
$ ulimit -s
8192
$ ./vla 1000000            # 8 MB 以內
stack ok, arr[n]=0
$ ./vla                    # n = 10^7，要 80 MB stack
Segmentation fault (core dumped)
$ ./vla 10000000 heap
heap ok, arr[n]=0
```

stack 的上限由 `RLIMIT_STACK` 控制（本機 `ulimit -s` 是 8192 KiB），VLA 只是把 stack pointer 往下移 80 MB，接下來一寫就踩到 stack 區域外沒有映射的位址，核心送 `SIGSEGV`。這裡要修正一個常見說法：**不是作業系統「事先衡量要求夠不夠」而拒絕**，VLA 配置當下沒有任何檢查，是寫入時才出事；而 heap 版能跑，是因為 `malloc` 走 `mmap`、加上 overcommit 放行。stack 的 segfault 某種程度是「誠實的錯誤」，heap 版在記憶體真的不夠時反而會被 OOM killer 無預警殺掉。VLA 在 C11 起是選用功能（`__STDC_NO_VLA__`），細節見 [Ch 8](./08-arrays-functions-pointers.md)。

## 對比與取捨

| 記憶體來源 | 生命週期 | 配置成本 | 大小限制 | 何時用 |
|---|---|---|---|---|
| stack（區域變數、VLA、`alloca`） | 函式返回即結束 | 移動 stack pointer，近乎零 | `ulimit -s`，通常 8 MiB | 小、短命、大小可預期的資料 |
| static / 全域 | 整個程式 | 零（載入時決定） | 編譯期固定 | 單例、查表 |
| `malloc` → brk heap | 到 `free` 為止 | 數十 ns（多數不進核心） | 位址空間 + overcommit | 一般小型動態配置 |
| `malloc` → mmap | 到 `free` 為止 | 系統呼叫 + 每頁一次 page fault | 同上 | 大塊（≥ 128 KiB，門檻會動態調整） |
| 直接 `mmap` | 到 `munmap` | 同上 | 同上 | 需要控制對齊、保護屬性、檔案映射 |

## 踩雷集錦

1. **「O(N²) 就是 O(N²)，常數不重要」**：同樣 O(N²)，存取順序就能差 17 倍。複雜度分析假設每次記憶體存取成本相同，這個假設在現代硬體上不成立。熱點迴圈要同時看演算法和存取模式。
2. **「`malloc` 沒回傳 NULL，記憶體就拿到了」**：在 overcommit 模式 0 和 1 下，`malloc` 只拿到虛擬位址。真正的失敗發生在第一次寫入、而且是以 OOM killer 的形式出現，沒有錯誤碼可檢查。需要保證時，用 `mlock` 或在啟動時預先碰過（prefault）記憶體，並考慮 `overcommit_memory=2`。
3. **「`free` 之後 RSS 就會下降」**：只有 mmap 出來的大區塊會立刻還；brk heap 中間的空洞還不回去，頂端的空閒區要超過 `M_TRIM_THRESHOLD` 才會縮。看到「free 了但 RSS 沒降」通常不是 leak。
4. **benchmark 量到別的東西**：stride 實驗最後幾列看起來「變快」，其實是工作集小到裝進 L2。寫微基準要確認工作集大小、暖身、防止編譯器刪掉迴圈（印出結果或用 `volatile`）、重複多次取穩定值。
5. **把虛擬機或筆電上的數字當成通則**：WSL2 的 TLB miss 走巢狀頁表，筆電會降頻，不同 CPU 的 cache 大小不同。效能結論一定要附上機器條件。

## 進階：再往深一層

- **slab allocator**：原文提到程式配置大量小型、不連續的物件（例如樹的節點）會造成碎片和 cache 不友善。Jeff Bonwick 1994 年在 SunOS 提出 slab allocator：同一種型別的物件集中放在同一組 page，配置時直接拿已經初始化好的物件。Linux 核心的 SLAB/SLUB 就是後代，使用者空間的 memory pool 也是同樣想法（見 `../c_interview/13-memory-pool.md`）。
- **false sharing**：兩個執行緒各寫自己的變數，但變數落在同一條 cache line，line 會在兩顆核心之間來回搬。[Ch 16](./16-alignment-and-padding.md) 會實測並用 `alignas(64)` 解決。
- **NUMA**：多插槽伺服器上，每顆 CPU 有自己的近端記憶體，存取遠端記憶體更慢。本機是單一 NUMA node（`lscpu` 的 `NUMA node(s): 1`），所以本章沒涉及。
- **硬體計數器**：要確認「慢是因為 cache miss」，正規做法是 `perf stat -e cache-misses,dTLB-load-misses`。本機 WSL2 的 kernel 沒有對應的 `linux-tools` 套件，`perf` 無法使用，所以本章改用延遲推論；在實體 Linux 上建議實際量一次計數器。
- **軟體預取**：gcc 的 `__builtin_prefetch(addr)` 可以提示 CPU 提前載入。只在存取模式不規則、但你能提前算出位址時有用（例如走訪鏈結串列時預取 `next->next`）。預取錯了會浪費頻寬，要量測。

## 動手練習

1. 把 rowcol.c 的 `N` 改成 4000 和 4097，看 column-major 的時間怎麼變。課本式的預測是：N = 4096 時每列起點相距 16 KiB，會落在同一批 cache set 造成 conflict miss，非 2 的冪次應該比較快。但本機實測三者的 column-major 都在 0.087–0.103 s，看不出差別。想想預測為什麼失準。提示：L1 的 set index（位址 bit 6–11）落在 page offset 內，但 L1 只有 512 條 line，本來就裝不下一整行的 4096 條；L2/L3 用**實體位址**選 set，而相鄰的虛擬 page 被核心對應到哪個實體 frame 幾乎是隨機的，虛擬位址上整齊的 16 KiB 間距到了實體位址就不整齊了。
2. 修改 chase.c 做 huge page 實驗：把 `aligned_alloc(LINE, ...)` 換成 `aligned_alloc(2u << 20, ...)`（大小至少 2 MiB），配置後若 `getenv("HUGE")` 為真就呼叫 `madvise(v, size, MADV_HUGEPAGE)`，再 `memset` 一次讓 page 真正配置。比較 `./chase` 和 `HUGE=1 ./chase`，執行時用 `grep AnonHugePages /proc/meminfo` 觀察。
3. 寫一支程式 `malloc` 2 GiB 但不碰它，用 `/proc/self/status` 的 `VmSize` 和 `VmRSS` 兩欄比較，解釋兩者差在哪裡。
4. 用 `strace -e trace=brk,mmap ./your_prog` 找出你自己的程式在哪一個 `malloc` 大小開始改用 `mmap`。

## 本章重點整理

- CPU 和 DRAM 之間有多層 cache，延遲從約 1 ns 到上百 ns；搬運單位是 64-byte 的 cache line。
- 循序存取同時受益於 cache line 與硬體預取；跳躍存取每次都付整條 line 的成本。最佳化會放大兩者差距。
- 虛擬位址經 MMU 用四層頁表翻譯成實體位址，TLB 快取翻譯結果；TLB 覆蓋不到的大工作集，隨機存取還要多付 page walk。
- Linux 用 demand paging 和 overcommit：`malloc` 成功只代表拿到虛擬位址，第一次寫入才配置實體 page。
- glibc 的 `malloc` 小配置走 brk heap，大配置（預設 ≥ 128 KiB）走 mmap；兩者歸還記憶體的方式不同。

## 自我檢核

- [ ] 不看本章，能用「cache line」解釋為什麼 stride 1 到 16 的總時間幾乎一樣嗎？
- [ ] 如果面試官問「`malloc` 回傳非 NULL，為什麼程式還是會因為記憶體不足死掉？」，你能從 demand paging 一路講到 OOM killer 嗎？
- [ ] 能畫出 48-bit 虛擬位址被切成哪幾段、TLB 在哪個環節介入嗎？
- [ ] 手上的程式有沒有「看起來無害的迴圈順序」可能是 column-major 走法？找一個實際例子。
- [ ] 能說出至少兩種情況，benchmark 量到的不是你以為在量的東西嗎？

## 延伸閱讀

### 論文 / 長文

- **[What Every Programmer Should Know About Memory](https://people.freebsd.org/~lstewart/articles/cpumemory.pdf)** — Ulrich Drepper（Red Hat，2007）
  - **核心貢獻**：從 DRAM 的電路一路講到 cache、TLB、NUMA，以及程式設計師能做什麼。作者是當時的 glibc 維護者。
  - **讀哪裡**：第 3 節（CPU caches）與第 6 節（What programmers can do）；第 2 節 DRAM 電路可以先跳過。也有 [LWN 連載版](https://lwn.net/Articles/250967/)。
  - **和本章的關聯**：本章 pointer chasing 與 stride 實驗就是第 3 節實驗的簡化版。硬體數字已經老了（2007 年），但原理沒變。

### 部落格 / 技術文章

- **[What a C programmer should know about memory](https://marek.vavrusa.com/memory/)** — Marek Vavruša（2015）
  - **這篇說什麼**：原文直接引用的文章，從 C 程式設計師角度談虛擬記憶體、stack 配置、slab、demand paging、`mmap`/`madvise`。
  - **讀哪裡**：整篇；「Understanding virtual memory - the plot thickens」與「Demand paging explained」兩節對應本章 overcommit 與 COW。
  - **為什麼值得讀**：程式碼範例多，且都是 Linux 實務 API。
- **[Gallery of Processor Cache Effects](http://igoro.com/archive/gallery-of-processor-cache-effects/)** — Igor Ostrovsky（2010）
  - **這篇說什麼**：用 7 個短實驗展示 cache line、L1/L2 大小、指令層級平行、cache associativity、false sharing。
  - **讀哪裡**：Example 1–5。前提是懂本章的 cache line 與 set 概念。
  - **為什麼值得讀**：每個實驗都只有幾行，適合自己在 C 重做一次當練習。

### 官方文件

- **[Overcommit Accounting](https://docs.kernel.org/mm/overcommit-accounting.html)** — Linux kernel 文件
  - **讀哪裡**：整頁（很短）；三種模式的精確定義和 `CommitLimit` 的算法。
  - **注意**：模式 0 的 heuristic 細節隨版本調整，以你核心版本的文件為準。
- **`man 3 mallopt`**（在 Ubuntu 上直接執行）
  - **讀哪裡**：`M_MMAP_THRESHOLD`、`M_TOP_PAD`、`M_TRIM_THRESHOLD` 三段；本章 132 KiB 與 128 KiB 兩個數字的出處。

### 書籍

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（Pearson，2015）
  - **這本書的定位**：本章的教科書版本，習題設計得很好。
  - **讀哪幾章**：第 6 章（記憶體階層，6.4–6.6 的 cache 與 memory mountain）、第 9 章 9.1–9.8（虛擬記憶體、頁表、TLB、Linux VM）。前提：懂二進位與基本 C。
- **《Operating Systems: Three Easy Pieces》** — Remzi & Andrea Arpaci-Dusseau（[線上免費](https://pages.cs.wisc.edu/~remzi/OSTEP/)）
  - **讀哪幾章**：第 18–20 章（Paging、TLBs、Smaller Tables）與第 21–22 章（Swapping）。每章附有可以跑的模擬器。

搞懂了 cache line 與 page，下一個問題自然浮現：資料在一條 line、一個 page 裡怎麼擺放？為什麼 `struct { char c; int a; }` 是 8 bytes 而不是 5？

→ [Ch 16 data alignment、padding 與 _Alignas](./16-alignment-and-padding.md)
