# 練習 B — 自製 malloc

> **目標**：把 Ch 15–17 學到的東西拼起來，從零寫一個能用的配置器：`mm_malloc`/`mm_free`/`mm_realloc`/`mm_calloc`，向「系統」要記憶體、用顯式空閒串列管理、first-fit 搜尋、切割與合併相鄰空間、所有回傳位址 16-byte 對齊，並通過含隨機壓力測試在內的整組測試。完成後你驗證了自己真的懂 chunk header、boundary tag、空閒串列與 `realloc` 的語意，而且會用一致性檢查器除錯指標錯誤。

> **環境**：WSL2 Ubuntu 22.04 x86_64，gcc 11.4，glibc 2.35。參考解答與所有輸出都在這個環境實際跑過。

## 背景與動機

[Ch 17](./17-malloc-internals.md) 拆解了 glibc 的 `malloc`，但讀懂和寫得出來是兩回事。配置器是指標操作最密集的程式之一：一個 header 算錯 8 bytes、一個串列指標忘了更新，錯誤不會當場爆炸，而是在幾千次操作之後以「莫名其妙的 segfault」或「資料被改掉」的形式出現。這正是為什麼 CS:APP 的 malloc lab 是系統程式課程的經典作業，也是為什麼面試常問「實作一個 malloc」。

你要寫的不是玩具：它的介面和 C 標準的配置函式一致，你可以拿真實的配置軌跡去測它，[練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 甚至可以用 `LD_PRELOAD` 把它換成整個程式的 `malloc`。

> 前置：[Ch 15](./15-memory-hierarchy.md) 的 brk/mmap、[Ch 16](./16-alignment-and-padding.md) 的對齊規則、[Ch 17](./17-malloc-internals.md) 的 chunk 結構與 boundary tag。指標轉型不熟，回看 [Ch 9 — void * 與指標的指標](./09-void-pointer-and-callbacks.md)。

## 任務規格

### 介面（`mm.h`）

```c
/* mm.h: 自製配置器的公開介面 */
#ifndef MM_H
#define MM_H
#include <stddef.h>

int    mm_init(void);                         /* 成功回傳 0 */
void  *mm_malloc(size_t size);
void   mm_free(void *ptr);
void  *mm_realloc(void *ptr, size_t size);
void  *mm_calloc(size_t nmemb, size_t size);
size_t mm_usable_size(const void *ptr);
int    mm_check(int verbose);                 /* 一致性檢查：0 = OK */
size_t mm_heap_size(void);                    /* 目前向系統拿了多少 bytes */

#endif
```

### 行為要求

| 函式 | 要求 |
|---|---|
| `mm_malloc(n)` | 回傳至少 `n` bytes 可用空間的指標，**位址為 16 的倍數**；`n == 0` 回傳 `NULL`（7.22.3 ¶1 允許的實作定義行為之一）；`n` 大到加上 overhead 會溢位、或 heap 長不出來時回傳 `NULL` |
| `mm_free(p)` | `p == NULL` 時什麼都不做；釋放後立即與相鄰空閒區塊合併 |
| `mm_realloc(p, n)` | `p == NULL` 等同 `mm_malloc(n)`；`n == 0` 時釋放 `p` 並回傳 `NULL`（glibc 的行為）；內容保留到新舊大小較小者；**失敗時 `p` 保持有效、內容不變**；能原地完成（縮小、後鄰空閒且夠大、後面就是 heap 尾端）時不得搬家 |
| `mm_calloc(n, s)` | `n * s` 溢位回傳 `NULL`；成功時內容全為 0，即使重用的是之前被寫髒的區塊 |
| `mm_usable_size(p)` | 回傳此區塊實際可用的 bytes（≥ 當初要求的大小） |
| `mm_check(v)` | 走訪整個 heap 與空閒串列，回傳發現的錯誤數；`v` 非零時印出每個錯誤 |

### 限制

- **不得呼叫** libc 的 `malloc`/`free`/`realloc`/`calloc`。記憶體只能透過 `mem_sbrk(incr)` 取得（下面提供的 `memlib.c`，以 `mmap` 模擬 `sbrk`）。
- 必須用**顯式空閒串列**（explicit free list）：空閒區塊用雙向串列串起來，搜尋時只走空閒區塊。搜尋策略用 **first-fit**。
- 區塊要能**切割**（剩餘部分夠大時）與**合併**（free 時前後鄰居空閒就合併）。
- 不必是 thread-safe。
- 效能：壓力測試 100 萬次操作在本機數秒內完成（參考解答約 1.4 秒，其中大部分時間花在測試程式自己填寫與驗證資料）。

### 提供的程式：memlib（模擬 sbrk）

為什麼不直接用 `sbrk(2)`？因為 glibc 的 `malloc` 也在用同一個 program break（就連 `printf` 都會 `malloc` 緩衝區），兩個配置器搶同一條界線會互相干擾——參考解答的說明裡有實測的失敗過程。所以我們用 `mmap` 先保留一大段虛擬位址，再在裡面模擬一個私有的 break。`MAP_NORESERVE` 讓這 1 GiB 只占位址空間，碰到才配置實體記憶體（[Ch 15](./15-memory-hierarchy.md) 的 demand paging）。

```c
/* memlib.h */
#ifndef MEMLIB_H
#define MEMLIB_H
#include <stddef.h>
#include <stdint.h>
int    mem_init(void);
void  *mem_sbrk(intptr_t incr);
void  *mem_heap_lo(void);
void  *mem_heap_hi(void);
size_t mem_heapsize(void);
#endif
```

```c
/* memlib.c: 模擬 sbrk。先用 mmap 保留一大段位址，再像 program break 一樣往上推 */
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <sys/mman.h>
#include "memlib.h"

#define MAX_HEAP (1UL << 30)                  /* 保留 1 GiB 虛擬位址；MAP_NORESERVE 不占實體記憶體 */

static char *heap_lo;                         /* heap 第一個 byte */
static char *brk_ptr;                         /* 目前的「break」 */
static char *heap_max;                        /* 保留區尾端 */

int mem_init(void)
{
    void *p = mmap(NULL, MAX_HEAP, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (p == MAP_FAILED) return -1;
    heap_lo = brk_ptr = p;
    heap_max = heap_lo + MAX_HEAP;
    return 0;
}

void *mem_sbrk(intptr_t incr)
{
    char *old = brk_ptr;
    if (incr < 0 || (size_t)incr > (size_t)(heap_max - brk_ptr))
        return (void *)-1;                    /* 和真正的 sbrk 一樣，失敗回傳 (void *)-1 */
    brk_ptr += incr;
    return old;
}

void  *mem_heap_lo(void)   { return heap_lo; }
void  *mem_heap_hi(void)   { return brk_ptr - 1; }
size_t mem_heapsize(void)  { return (size_t)(brk_ptr - heap_lo); }
```

`mmap` 回傳的位址一定是 page（4096）對齊，所以 heap 起點也是 16 的倍數。

### 提供的程式：測試（test.c）與 Makefile

測試程式是驗收標準，直接拿去用。它包含 6 組定向測試與 1 組隨機壓力測試：壓力測試在 2048 個槽位上隨機做 malloc / calloc / free / realloc，每個區塊填入「自己的指紋」（以隨機 tag 為起點的遞增 byte 序列），每次 free 或 realloc 前驗證指紋——任何重疊配置、`realloc` 沒搬好、合併時蓋到別人，都會讓指紋對不上。每 20000 次操作呼叫一次 `mm_check`。

<details>
<summary>點開 test.c（約 200 行）</summary>

```c
/* test.c: 練習 B 的測試程式。./test [seed] [ops] */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include "mm.h"

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("  FAIL %s:%d: ", __FILE__, __LINE__); \
                                              printf(__VA_ARGS__); printf("\n"); } } while (0)
#define HEAP_OK() CHECK(mm_check(1) == 0, "mm_check failed")

static int aligned16(const void *p) { return ((uintptr_t)p & 15) == 0; }

static void test_basic(void)
{
    puts("[basic] 各種大小的配置都要 16-byte 對齊、可寫滿、不互相重疊");
    size_t sizes[] = { 1, 7, 8, 15, 16, 17, 24, 100, 1000, 4096, 5000, 100000 };
    char *p[12];
    for (int i = 0; i < 12; i++) {
        p[i] = mm_malloc(sizes[i]);
        CHECK(p[i] && aligned16(p[i]), "malloc(%zu) = %p", sizes[i], (void *)p[i]);
        CHECK(mm_usable_size(p[i]) >= sizes[i], "usable %zu < %zu", mm_usable_size(p[i]), sizes[i]);
        memset(p[i], 'a' + i, sizes[i]);
    }
    for (int i = 0; i < 12; i++)
        for (size_t k = 0; k < sizes[i]; k++)
            if (p[i][k] != 'a' + i) { CHECK(0, "block %d byte %zu overwritten", i, k); break; }
    HEAP_OK();
    for (int i = 0; i < 12; i++) mm_free(p[i]);
    HEAP_OK();
}

static void test_edge(void)
{
    puts("[edge] malloc(0)、free(NULL)、realloc(NULL, n)、realloc(p, 0)、超大請求");
    CHECK(mm_malloc(0) == NULL, "malloc(0) should be NULL in this implementation");
    mm_free(NULL);
    char *p = mm_realloc(NULL, 50);
    CHECK(p != NULL, "realloc(NULL, 50) should act like malloc");
    CHECK(mm_realloc(p, 0) == NULL, "realloc(p, 0) frees and returns NULL");
    CHECK(mm_malloc(SIZE_MAX) == NULL, "malloc(SIZE_MAX)");
    CHECK(mm_malloc(SIZE_MAX - 8) == NULL, "malloc(SIZE_MAX-8) must not wrap");
    CHECK(mm_malloc((size_t)1 << 40) == NULL, "malloc(1 TiB) beyond reserved heap");
    HEAP_OK();
}

static void test_coalesce(void)
{
    puts("[coalesce] free 三個相鄰區塊後，應合併成一塊並可整塊重用");
    char *a = mm_malloc(100), *b = mm_malloc(100), *c = mm_malloc(100);
    char *guard = mm_malloc(100);              /* 擋住 c 後面，避免和尾端空閒區合併 */
    mm_free(a);
    mm_free(c);
    HEAP_OK();
    mm_free(b);                                /* 前後都空閒：情況 4 */
    HEAP_OK();
    char *big = mm_malloc(300);                /* 合併後有 3 * 128 = 384 bytes */
    CHECK(big == a, "expected coalesced block at %p, got %p", (void *)a, (void *)big);
    mm_free(big);
    mm_free(guard);
    HEAP_OK();
}

static void test_split(void)
{
    puts("[split] 大空閒塊被切割後，剩下的部分要能再被使用");
    char *a = mm_malloc(1000);
    char *guard = mm_malloc(16);
    mm_free(a);
    char *x = mm_malloc(100);
    char *y = mm_malloc(100);
    CHECK(x == a, "first-fit should reuse %p, got %p", (void *)a, (void *)x);
    CHECK(y == x + 128, "remainder should start at %p, got %p", (void *)(x + 128), (void *)y);
    HEAP_OK();
    mm_free(x); mm_free(y); mm_free(guard);
    HEAP_OK();
}

static void test_realloc(void)
{
    puts("[realloc] 內容保留、原地成長、搬家、縮小");
    char *a = mm_malloc(40);
    for (int i = 0; i < 40; i++) a[i] = (char)i;
    char *a2 = mm_realloc(a, 4000);            /* a 是最後一塊：應原地成長 */
    CHECK(a2 == a, "tail block should grow in place");
    for (int i = 0; i < 40; i++) CHECK(a2[i] == (char)i, "content lost at %d", i);
    char *blocker = mm_malloc(40);             /* 擋在 a2 後面 */
    char *a3 = mm_realloc(a2, 8000);           /* 只能搬家 */
    CHECK(a3 != a2, "should move");
    for (int i = 0; i < 40; i++) CHECK(a3[i] == (char)i, "content lost after move at %d", i);
    char *a4 = mm_realloc(a3, 10);             /* 縮小：原地 */
    CHECK(a4 == a3, "shrink should stay in place");
    HEAP_OK();
    mm_free(a4); mm_free(blocker);
    HEAP_OK();
}

static void test_calloc(void)
{
    puts("[calloc] 重用髒記憶體也要清零；n * size 溢位回傳 NULL");
    char *d = mm_malloc(256);
    memset(d, 0xAB, 256);
    mm_free(d);
    unsigned char *z = mm_calloc(64, 4);
    CHECK(z != NULL, "calloc(64, 4)");
    for (int i = 0; i < 256; i++) if (z[i]) { CHECK(0, "byte %d not zero", i); break; }
    CHECK(mm_calloc(SIZE_MAX / 4 + 2, 4) == NULL, "calloc overflow must fail");
    CHECK(mm_calloc((size_t)1 << 33, (size_t)1 << 33) == NULL, "calloc 2^33 * 2^33");
    mm_free(z);
    HEAP_OK();
}

/* ---- 隨機壓力測試：每塊填上自己的指紋，free/realloc 前驗證 ---- */
#define SLOTS 2048
struct slot { unsigned char *p; size_t n; unsigned char tag; };

static size_t rand_size(void)
{
    int r = rand() % 100;
    if (r < 70) return 1 + rand() % 256;
    if (r < 95) return 257 + rand() % 3840;
    return 4097 + rand() % 61440;
}

static int verify(struct slot *s)
{
    for (size_t k = 0; k < s->n; k++)
        if (s->p[k] != (unsigned char)(s->tag + k)) return 0;
    return 1;
}

static void fill(struct slot *s)
{
    for (size_t k = 0; k < s->n; k++) s->p[k] = (unsigned char)(s->tag + k);
}

static void test_stress(unsigned seed, long ops)
{
    printf("[stress] seed=%u ops=%ld\n", seed, ops);
    srand(seed);
    static struct slot s[SLOTS];
    memset(s, 0, sizeof s);
    size_t live = 0, peak_live = 0;
    long n_malloc = 0, n_free = 0, n_realloc = 0, bad = 0;
    clock_t t0 = clock();
    for (long i = 0; i < ops; i++) {
        struct slot *x = &s[rand() % SLOTS];
        int op = rand() % 10;
        if (!x->p) {                                       /* 空格：配置 */
            x->n = rand_size();
            x->tag = (unsigned char)rand();
            x->p = (op < 8) ? mm_malloc(x->n) : mm_calloc(1, x->n);
            if (!x->p || !aligned16(x->p)) { bad++; x->p = NULL; continue; }
            if (op >= 8)
                for (size_t k = 0; k < x->n; k++) if (x->p[k]) { bad++; break; }
            fill(x);
            live += x->n; n_malloc++;
        } else if (op < 6) {                               /* 有東西：free */
            if (!verify(x)) bad++;
            mm_free(x->p);
            live -= x->n; x->p = NULL; n_free++;
        } else {                                           /* 有東西：realloc */
            if (!verify(x)) bad++;
            size_t nn = rand_size();
            unsigned char *np = mm_realloc(x->p, nn);
            if (!np) { bad++; continue; }
            size_t keep = nn < x->n ? nn : x->n;
            for (size_t k = 0; k < keep; k++)
                if (np[k] != (unsigned char)(x->tag + k)) { bad++; break; }
            live = live - x->n + nn;
            x->p = np; x->n = nn; fill(x); n_realloc++;
        }
        if (live > peak_live) peak_live = live;
        if (i % 20000 == 0 && mm_check(1) != 0) { bad++; printf("  heap broken at op %ld\n", i); break; }
    }
    for (int k = 0; k < SLOTS; k++)
        if (s[k].p) { if (!verify(&s[k])) bad++; mm_free(s[k].p); s[k].p = NULL; }
    double sec = (double)(clock() - t0) / CLOCKS_PER_SEC;
    CHECK(bad == 0, "%ld corrupted / failed operations", bad);
    HEAP_OK();
    printf("  malloc=%ld free=%ld realloc=%ld  peak live=%zu KiB heap=%zu KiB  util=%.1f%%  %.2f s\n",
           n_malloc, n_free, n_realloc, peak_live >> 10, mm_heap_size() >> 10,
           100.0 * peak_live / mm_heap_size(), sec);
}

int main(int argc, char **argv)
{
    unsigned seed = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 10) : 1;
    long ops = argc > 2 ? atol(argv[2]) : 200000;
    if (mm_init() < 0) { puts("mm_init failed"); return 1; }
    test_basic();
    test_edge();
    test_coalesce();
    test_split();
    test_realloc();
    test_calloc();
    test_stress(seed, ops);
    printf("%s (%d failures)\n", failures ? "SOME TESTS FAILED" : "ALL TESTS PASSED", failures);
    return failures != 0;
}
```

</details>

```make
CFLAGS = -Wall -Wextra -O2 -g
test: test.o mm.o memlib.o
	$(CC) $(CFLAGS) -o $@ $^
clean:
	rm -f *.o test
```

注意 `test_coalesce` 與 `test_split` 檢查了**確切位址**，這些斷言假設本練習規定的區塊格式（header 8 + footer 8、對齊 16、LIFO 插入串列頭、first-fit）。你若選了不同的格式（例如省掉已配置區塊的 footer），這兩個位址斷言要跟著調整，但其他斷言都應該通過。

## 期望輸出範例

```
$ make && ./test
[basic] 各種大小的配置都要 16-byte 對齊、可寫滿、不互相重疊
[edge] malloc(0)、free(NULL)、realloc(NULL, n)、realloc(p, 0)、超大請求
[coalesce] free 三個相鄰區塊後，應合併成一塊並可整塊重用
[split] 大空閒塊被切割後，剩下的部分要能再被使用
[realloc] 內容保留、原地成長、搬家、縮小
[calloc] 重用髒記憶體也要清零；n * size 溢位回傳 NULL
[stress] seed=1 ops=200000
  malloc=75725 free=74417 realloc=49858  peak live=3986 KiB heap=10614 KiB  util=37.6%  0.28 s
ALL TESTS PASSED (0 failures)
```

壞掉的配置器會長這樣（參考解答故意在 `coalesce` 裡少一行 `list_remove`，用 `stdbuf -o0` 讓輸出在 segfault 前印出來）：

```
[basic] 各種大小的配置都要 16-byte 對齊、可寫滿、不互相重疊
free count mismatch: heap=1 list=2
  FAIL test.c:32: mm_check failed
[edge] malloc(0)、free(NULL)、realloc(NULL, n)、realloc(p, 0)、超大請求
free list cycle?
free count mismatch: heap=1 list=3
  FAIL test.c:46: mm_check failed
...
```

第一組測試就被 `mm_check` 抓到：heap 上只有 1 個空閒區塊，串列裡卻有 2 個——被合併掉的區塊還留在串列上。沒有 `mm_check` 的話，這個 bug 要到幾百次操作後才以 segfault 現身，而且當掉的位置離真正的錯誤很遠。

## 如果你卡住了

1. **畫圖。** 每個指標運算都先在紙上畫出區塊格式（header 在哪、payload 從哪開始、footer 在哪、下一塊的 header 在哪），標上 offset。配置器的 bug 九成是「差 8 bytes」。
2. **先寫 `mm_check`，再寫其他東西。** 走訪整個 heap 檢查 header 等於 footer、沒有兩個相鄰的空閒區塊、空閒區塊數和串列長度一致。每個操作後都呼叫它，bug 會在發生的那一刻被抓到。
3. **對齊卡住時**：payload 要在 16 的倍數，header 是 8 bytes，所以 header 必須在「16 的倍數 + 8」的位址。heap 起點是 16 的倍數，所以開頭要先墊 8 bytes。區塊大小也取 16 的倍數，之後每一塊就自動對齊了。
4. **合併的邊界情況煩人時**：在 heap 開頭放一個永遠「已配置」的假區塊（prologue），結尾放一個大小為 0、永遠「已配置」的 header（epilogue）。這樣合併時永遠不必檢查「我是不是第一塊/最後一塊」。
5. **`realloc` 很亂時**：先寫最笨的版本（`malloc` 新的、`memcpy`、`free` 舊的），讓所有測試除了「原地成長」那幾條都通過，再逐一加上原地縮小、吃掉後鄰、heap 尾端延展三種最佳化。

## 實作步驟建議

### Step 1：區塊格式與存取函式

決定格式：

```
  已配置：[ header | payload ............................ | footer ]
  空閒  ：[ header | next_free | prev_free | （未用）..... | footer ]
            8 B      8 B         8 B                         8 B

  header = footer = 區塊總大小（16 的倍數）| alloc 位元
  最小區塊 = 8 + 16 + 8 = 32 bytes（空閒時要放得下兩個指標）
```

寫一組小函式（或巨集）把指標運算封裝起來：`hdr(bp)`、`ftr(bp)`、`next_blk(bp)`、`prev_blk(bp)`、`size_of(hp)`、`alloc_of(hp)`。`bp`（block pointer）一律指向 payload，也就是使用者拿到的位址。之後所有程式碼都只透過這些函式碰記憶體，**不要**在主邏輯裡寫 `*(size_t *)((char *)bp - 8)`。

子目標：能畫出 `prev_blk(bp)` 是怎麼透過「前一塊的 footer」找到前一塊開頭的。

### Step 2：隱式串列版本（implicit free list）

先不管空閒串列，`find_fit` 直接從 heap 第一塊走到 epilogue，找第一個空閒且夠大的區塊。這就是 CS:APP 9.9 的隱式串列版本。寫好 `mm_init`（墊 8 bytes、prologue、epilogue）、`extend_heap`、`place`（含切割）、`mm_free`、`coalesce`（四種情況）。

```
  mm_init 之後的 heap：
  heap_lo
  │  0       8       16      24      32
  ▼  ┌───────┬───────┬───────┬───────┐
     │ pad 0 │ 16|1  │ 16|1  │  0|1  │
     └───────┴───────┴───────┴───────┘
               prologue        epilogue header
               hdr     ftr     （之後 extend_heap 會把它變成新區塊的 header）
                               第一個 payload 會落在 32：16 的倍數
```

子目標：除了 realloc 原地成長那幾條，basic/edge/coalesce/split/calloc 都通過（calloc 可以先用 malloc + memset）。隱式串列的 malloc 是 O(區塊總數)，壓力測試會慢，但應該正確。

### Step 3：換成顯式空閒串列

空閒區塊的 payload 前 16 bytes 存 `next_free`、`prev_free`。寫 `list_insert`（插在串列頭，LIFO）與 `list_remove`（O(1)，處理自己是頭的情況）。修改：

- `find_fit`：只走空閒串列。
- `place`：先 `list_remove`，切割後把剩餘塊 `list_insert`。
- `coalesce`：合併前把**被吃掉的鄰居**從串列移除，合併完把結果插入串列。

子目標：所有定向測試通過，`mm_check` 擴充為同時檢查串列（每個串列成員都標記為空閒、`next->prev == self`、數量和 heap 走訪一致）。

### Step 4：realloc 的三種原地情況

1. 新大小 ≤ 目前區塊：原地；剩餘 ≥ 32 bytes 就切下來 free 掉（記得和後鄰合併）。
2. 後鄰是 epilogue：`extend_heap` 補不足的部分，新長出來的空閒塊就是後鄰，轉成情況 3。
3. 後鄰空閒且兩塊加起來夠大：把後鄰從串列移除，合併，再把多餘的切掉。
4. 其他：`mm_malloc` 新的、`memcpy` 舊的 payload、`mm_free` 舊的。新的配置失敗時直接回傳 NULL，**不要**動舊區塊。

子目標：test_realloc 全部通過。

### Step 5：calloc 與溢位防護

- `adjust(size)`：`size + 16` 向上取整到 16，至少 32。先檢查 `size > SIZE_MAX - 32`，否則加法會繞回成一個很小的數字，配出一個小區塊——和 [Ch 17](./17-malloc-internals.md) 示範的 `malloc(n * size)` 是同一種漏洞。
- `mm_calloc`：先檢查 `size != 0 && nmemb > SIZE_MAX / size`，再 malloc + memset。

子目標：test_edge 與 test_calloc 的溢位斷言通過。

### Step 6：整合與壓力測試

跑多個 seed、大量操作，加 sanitizer 再跑一次：

```
for s in 1 2 3 42 2026; do ./test $s 1000000; done
gcc -Wall -Wextra -O1 -g -fsanitize=address,undefined test.c mm.c memlib.c -o test_san && ./test_san 7 300000
```

ASan 看不到你的 heap 內部（對它而言那是一整塊 mmap 區域），但能抓到測試程式或配置器本身越出那塊區域、以及 UBSan 抓得到的未對齊存取與整數問題。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作 mm.c</summary>

```c
/*
 * mm.c: 顯式空閒串列（explicit free list）+ first-fit + 立即合併 + boundary tag
 *
 * 區塊格式（每格 8 bytes，位址往右增加）：
 *
 *   已配置：[ header | payload ............................ | footer ]
 *   空閒  ：[ header | next_free | prev_free | (未用) ...... | footer ]
 *
 *   header/footer = 區塊總大小 | alloc 位元（大小是 16 的倍數，低 4 位元必為 0）
 *   payload 位址一律是 16 的倍數。最小區塊 32 bytes = header 8 + 兩個指標 16 + footer 8。
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "mm.h"
#include "memlib.h"

#define WSIZE      8                          /* header / footer 大小 */
#define DSIZE      16                         /* header + footer，也是對齊單位 */
#define ALIGNMENT  16                         /* 與 glibc x86-64 相同，滿足 max_align_t */
#define MIN_BLOCK  32
#define CHUNKSIZE  4096                       /* heap 不夠時至少長一個 page */

/* ---- 底層存取：集中在這幾個小函式，避免到處都是指標轉型 ---- */
static inline size_t get(const void *p)            { return *(const size_t *)p; }
static inline void   put(void *p, size_t v)        { *(size_t *)p = v; }
static inline size_t pack(size_t size, int alloc)  { return size | (size_t)alloc; }
static inline size_t size_of(const void *hp)       { return get(hp) & ~(size_t)0xF; }
static inline int    alloc_of(const void *hp)      { return (int)(get(hp) & 1); }

static inline char *hdr(void *bp)       { return (char *)bp - WSIZE; }
static inline char *ftr(void *bp)       { return (char *)bp + size_of(hdr(bp)) - DSIZE; }
static inline char *next_blk(void *bp)  { return (char *)bp + size_of(hdr(bp)); }
static inline char *prev_blk(void *bp)  { return (char *)bp - size_of((char *)bp - DSIZE); }

/* 空閒區塊的 payload 前 16 bytes 拿來放雙向串列的指標 */
static inline void *next_free(void *bp)           { return *(void **)bp; }
static inline void *prev_free(void *bp)           { return *((void **)bp + 1); }
static inline void  set_next(void *bp, void *v)   { *(void **)bp = v; }
static inline void  set_prev(void *bp, void *v)   { *((void **)bp + 1) = v; }

static char *heap_start;                      /* 第一個真正區塊的 payload（init 後） */
static void *free_head;                       /* 空閒串列頭 */
static int   inited;

/* ---- 空閒串列：LIFO 插入，O(1) 移除 ---- */
static void list_insert(void *bp)
{
    set_next(bp, free_head);
    set_prev(bp, NULL);
    if (free_head) set_prev(free_head, bp);
    free_head = bp;
}

static void list_remove(void *bp)
{
    void *p = prev_free(bp), *n = next_free(bp);
    if (p) set_next(p, n); else free_head = n;
    if (n) set_prev(n, p);
}

/* ---- 合併：四種情況（前後是否空閒） ---- */
static void *coalesce(void *bp)
{
    int prev_alloc = alloc_of((char *)bp - DSIZE);   /* 前一塊的 footer */
    int next_alloc = alloc_of(hdr(next_blk(bp)));
    size_t size = size_of(hdr(bp));

    if (!next_alloc) {                                /* 吃掉後一塊 */
        void *n = next_blk(bp);
        list_remove(n);
        size += size_of(hdr(n));
        put(hdr(bp), pack(size, 0));
        put(ftr(bp), pack(size, 0));
    }
    if (!prev_alloc) {                                /* 被前一塊吃掉 */
        void *p = prev_blk(bp);
        list_remove(p);
        size += size_of(hdr(p));
        bp = p;
        put(hdr(bp), pack(size, 0));
        put(ftr(bp), pack(size, 0));
    }
    list_insert(bp);
    return bp;
}

static void *extend_heap(size_t bytes)
{
    bytes = (bytes + ALIGNMENT - 1) & ~(size_t)(ALIGNMENT - 1);
    char *bp = mem_sbrk((intptr_t)bytes);
    if (bp == (void *)-1) return NULL;
    /* 新區塊的 header 蓋掉舊的 epilogue，尾端再補一個新 epilogue */
    put(hdr(bp), pack(bytes, 0));
    put(ftr(bp), pack(bytes, 0));
    put(hdr(next_blk(bp)), pack(0, 1));
    return coalesce(bp);
}

int mm_init(void)
{
    if (mem_init() < 0) return -1;
    char *p = mem_sbrk(4 * WSIZE);
    if (p == (void *)-1) return -1;
    put(p, 0);                                  /* 對齊用的填充：讓之後的 payload 落在 16 的倍數 */
    put(p + 1 * WSIZE, pack(DSIZE, 1));         /* prologue header */
    put(p + 2 * WSIZE, pack(DSIZE, 1));         /* prologue footer */
    put(p + 3 * WSIZE, pack(0, 1));             /* epilogue header */
    heap_start = p + 4 * WSIZE;
    free_head = NULL;
    inited = 1;
    return 0;
}

/* 使用者要 size bytes -> 區塊實際大小（含 header/footer，向上對齊 16，至少 32） */
static size_t adjust(size_t size)
{
    if (size > SIZE_MAX - DSIZE - ALIGNMENT) return 0;   /* 加 overhead 會溢位 */
    size_t a = (size + DSIZE + ALIGNMENT - 1) & ~(size_t)(ALIGNMENT - 1);
    return a < MIN_BLOCK ? MIN_BLOCK : a;
}

static void *find_fit(size_t asize)
{
    for (void *bp = free_head; bp; bp = next_free(bp))
        if (size_of(hdr(bp)) >= asize) return bp;          /* first-fit */
    return NULL;
}

/* 把空閒區塊 bp 配出 asize；剩下夠大就切成新的空閒區塊 */
static void place(void *bp, size_t asize)
{
    size_t csize = size_of(hdr(bp));
    list_remove(bp);
    if (csize - asize >= MIN_BLOCK) {
        put(hdr(bp), pack(asize, 1));
        put(ftr(bp), pack(asize, 1));
        void *rest = next_blk(bp);
        put(hdr(rest), pack(csize - asize, 0));
        put(ftr(rest), pack(csize - asize, 0));
        list_insert(rest);       /* 剩餘塊的後鄰原本就不會是空閒的，不必合併 */
    } else {
        put(hdr(bp), pack(csize, 1));
        put(ftr(bp), pack(csize, 1));
    }
}

void *mm_malloc(size_t size)
{
    if (!inited && mm_init() < 0) return NULL;
    if (size == 0) return NULL;                 /* 實作定義行為：本實作選擇回傳 NULL */
    size_t asize = adjust(size);
    if (asize == 0) return NULL;
    void *bp = find_fit(asize);
    if (!bp) {
        bp = extend_heap(asize > CHUNKSIZE ? asize : CHUNKSIZE);
        if (!bp) return NULL;
    }
    place(bp, asize);
    return bp;
}

void mm_free(void *bp)
{
    if (!bp) return;                            /* free(NULL) 什麼都不做 */
    size_t size = size_of(hdr(bp));
    put(hdr(bp), pack(size, 0));
    put(ftr(bp), pack(size, 0));
    coalesce(bp);
}

/* 已配置區塊 bp 縮成 asize，切下來的尾巴還給空閒串列（可能和後鄰合併） */
static void shrink(void *bp, size_t asize)
{
    size_t csize = size_of(hdr(bp));
    if (csize - asize < MIN_BLOCK) return;
    put(hdr(bp), pack(asize, 1));
    put(ftr(bp), pack(asize, 1));
    void *rest = next_blk(bp);
    put(hdr(rest), pack(csize - asize, 0));
    put(ftr(rest), pack(csize - asize, 0));
    coalesce(rest);
}

void *mm_realloc(void *ptr, size_t size)
{
    if (!ptr) return mm_malloc(size);
    if (size == 0) { mm_free(ptr); return NULL; }   /* glibc 的做法；C23 起此情況為 UB */
    size_t asize = adjust(size);
    if (asize == 0) return NULL;
    size_t csize = size_of(hdr(ptr));

    if (asize <= csize) {                       /* 1. 縮小或不變：原地 */
        shrink(ptr, asize);
        return ptr;
    }
    void *n = next_blk(ptr);
    if (size_of(hdr(n)) == 0) {                 /* 2. 後面就是 epilogue：長 heap 再合併 */
        if (!extend_heap(asize - csize)) return NULL;
        n = next_blk(ptr);                      /* extend_heap 產生的新空閒塊 */
    }
    if (!alloc_of(hdr(n)) && csize + size_of(hdr(n)) >= asize) {   /* 3. 吃掉後面的空閒塊 */
        list_remove(n);
        size_t total = csize + size_of(hdr(n));
        put(hdr(ptr), pack(total, 1));
        put(ftr(ptr), pack(total, 1));
        shrink(ptr, asize);
        return ptr;
    }
    void *np = mm_malloc(size);                 /* 4. 只能搬家 */
    if (!np) return NULL;                       /* 失敗時舊區塊保持原狀 */
    memcpy(np, ptr, csize - DSIZE);
    mm_free(ptr);
    return np;
}

void *mm_calloc(size_t nmemb, size_t size)
{
    if (size != 0 && nmemb > SIZE_MAX / size) return NULL;   /* nmemb * size 會溢位 */
    void *p = mm_malloc(nmemb * size);
    if (p) memset(p, 0, nmemb * size);
    return p;
}

size_t mm_usable_size(const void *ptr)
{
    return ptr ? size_of((const char *)ptr - WSIZE) - DSIZE : 0;
}

size_t mm_heap_size(void) { return inited ? mem_heapsize() : 0; }

/* ---- 一致性檢查：寫配置器時最重要的除錯工具 ---- */
int mm_check(int verbose)
{
    int err = 0;
    size_t nfree_heap = 0, nfree_list = 0;
    char *lo = mem_heap_lo(), *hi = mem_heap_hi();
    int prev_was_free = 0;

    for (char *bp = heap_start; size_of(hdr(bp)) > 0; bp = next_blk(bp)) {
        size_t sz = size_of(hdr(bp));
        if ((uintptr_t)bp % ALIGNMENT) { err++; if (verbose) printf("misaligned %p\n", (void *)bp); }
        if (sz < MIN_BLOCK || sz % ALIGNMENT) { err++; if (verbose) printf("bad size %zu at %p\n", sz, (void *)bp); break; }
        if (bp + sz > hi + 1) { err++; if (verbose) printf("block %p past heap end\n", (void *)bp); break; }
        if (get(hdr(bp)) != get(ftr(bp))) { err++; if (verbose) printf("hdr/ftr mismatch %p\n", (void *)bp); }
        if (!alloc_of(hdr(bp))) {
            nfree_heap++;
            if (prev_was_free) { err++; if (verbose) printf("uncoalesced %p\n", (void *)bp); }
        }
        prev_was_free = !alloc_of(hdr(bp));
    }
    for (void *bp = free_head; bp; bp = next_free(bp)) {
        nfree_list++;
        if ((char *)bp < lo || (char *)bp > hi) { err++; if (verbose) printf("list ptr %p out of heap\n", bp); break; }
        if (alloc_of(hdr(bp))) { err++; if (verbose) printf("allocated block %p in free list\n", bp); }
        if (next_free(bp) && prev_free(next_free(bp)) != bp) { err++; if (verbose) printf("broken links at %p\n", bp); }
        if (nfree_list > nfree_heap + 1) { err++; if (verbose) printf("free list cycle?\n"); break; }
    }
    if (nfree_heap != nfree_list) {
        err++;
        if (verbose) printf("free count mismatch: heap=%zu list=%zu\n", nfree_heap, nfree_list);
    }
    return err;
}
```

**解答說明**

- **為什麼每個區塊都有 footer**：合併時要從 `bp` 找到前一塊的開頭，唯一的線索是緊鄰在 `bp` 的 header 前面的「前一塊 footer」。代價是每塊多 8 bytes。glibc 的做法更省：只有**空閒**塊才寫 footer（寫在下一塊的 prev_size），並在每塊 header 用 P 位元記錄「前一塊是否使用中」——這是延伸挑戰之一。
- **常數的來源**：`WSIZE = 8` 是 `size_t` 與指標的大小；`ALIGNMENT = 16` 是 x86-64 的 `alignof(max_align_t)`（[Ch 16](./16-alignment-and-padding.md)）；`MIN_BLOCK = 32` 是空閒時要放 header、兩個指標、footer；`CHUNKSIZE = 4096` 是一個 page，heap 不夠時至少長這麼多，減少 `mem_sbrk` 次數。
- **`mm_init` 的 8 bytes 墊片**：`mmap` 給的起點是 4096 的倍數；墊 8 + prologue 16 + epilogue header 8 = 32，第一個 payload 就落在起點 + 32，是 16 的倍數。
- **`place` 切出的剩餘塊不必合併**：剩餘塊的後鄰就是原空閒塊的後鄰，而 heap 上**永遠不會有兩個相鄰的空閒塊**（每次 free 都立即合併），所以它一定是已配置的。`shrink` 則不同：已配置塊的後鄰可能是空閒的，所以要呼叫 `coalesce`。
- **`realloc` 失敗不破壞舊區塊**：情況 2 的 `extend_heap` 失敗時直接回傳 NULL，此時舊區塊完全沒被動過；情況 4 先 `mm_malloc` 成功才 `memcpy` 與 `mm_free`。
- **`mm_check` 的迴圈保護**：串列若因 bug 形成環，走訪會永遠停不下來，所以計數超過 heap 上的空閒塊數就中止。上面那個「少一行 `list_remove`」的故意 bug，正是被 `free list cycle?` 與 `free count mismatch` 抓到的。

**驗證結果**（本機實測）：

```
$ for s in 1 2 3 42 2026; do ./test $s 1000000 | grep -E "util|PASSED"; done
  malloc=375703 free=374446 realloc=249851  peak live=4050 KiB heap=10856 KiB  util=37.3%  1.36 s
ALL TESTS PASSED (0 failures)
  malloc=376185 free=374934 realloc=248881  peak live=4072 KiB heap=11035 KiB  util=36.9%  1.33 s
ALL TESTS PASSED (0 failures)
  malloc=376033 free=374747 realloc=249220  peak live=4236 KiB heap=10909 KiB  util=38.8%  1.34 s
ALL TESTS PASSED (0 failures)
  malloc=376134 free=374851 realloc=249015  peak live=4368 KiB heap=10626 KiB  util=41.1%  1.33 s
ALL TESTS PASSED (0 failures)
  malloc=375701 free=374399 realloc=249900  peak live=4505 KiB heap=10828 KiB  util=41.6%  1.40 s
ALL TESTS PASSED (0 failures)
$ ./test_san 7 300000 | tail -2           # -fsanitize=address,undefined
  malloc=113140 free=111841 realloc=75019  peak live=4065 KiB heap=10346 KiB  util=39.3%  2.07 s
ALL TESTS PASSED (0 failures)
```

**空間利用率約 37–42%**，也就是 heap 最大時只有四成是使用者真正在用的資料。這不是 bug，而是 first-fit + LIFO 串列在這種「大小差異很大（1 B 到 64 KiB）」負載下的典型外部碎片：大區塊被 free 後，被後來的小請求從頭切掉一點，剩下的部分就再也湊不出 64 KiB。而且這個 heap 只長不縮（沒有把頂端空閒區還給系統）。

**和 glibc 比速度**：把同一個「1024 槽位、16–512 bytes 隨機 malloc/free、400 萬次」的負載（[Ch 17](./17-malloc-internals.md) allocbench 的單執行緒版，不填資料）分別連結 mm.c 和一個呼叫 glibc 的轉接層：

```
mm       0.105 s  37.9 Mops/s  heap=408 KiB
glibc    0.071 s  56.0 Mops/s  heap=396 KiB
```

在小物件、活躍數量少的負載下，一個兩百多行的 first-fit 配置器只比 glibc 慢約三成，記憶體用量相當。first-fit 的弱點要到空閒串列很長時才顯現（搜尋是 O(空閒塊數)），glibc 的 bins 與 tcache 正是為了把搜尋變成 O(1)。

**為什麼不用真的 `sbrk`**：把 memlib 換成直接呼叫 `sbrk(2)` 的版本（`mem_sbrk` 在 break 被別人動過時拒絕延展），用 `strace -e trace=brk` 執行：

```
brk(NULL)                               = 0x62ddb10eb000
brk(NULL)                               = 0x62ddb10eb000
brk(0x62ddb10eb020)                     = 0x62ddb10eb020
brk(0x62ddb110c020)                     = 0x62ddb110c020
brk(0x62ddb110d000)                     = 0x62ddb110d000
--- SIGSEGV {si_signo=SIGSEGV, si_code=SEGV_MAPERR, si_addr=NULL} ---
```

第三行是我們的 `mm_init` 推了 0x20；第四、五行是 glibc 的 `malloc` 為 stdout 配置緩衝區時把 break 再推了 0x21000 並對齊到 page。之後我們的 `mem_sbrk` 發現 break 不是自己上次留下的位置，拒絕延展，`mm_malloc` 回傳 NULL，測試對 NULL 做 `memset` 就 segfault 了。（用 `stdbuf -o0` 讓 stdout 不緩衝，glibc 就不會 `malloc`，測試反而能通過。）兩個配置器共用一條 program break，就是這麼脆弱。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| `mm_malloc(1)`、`mm_malloc(100000)` 等 12 種大小 | 非 NULL、16 的倍數、`usable ≥ 要求`、寫滿不互蓋 | 正常路徑與對齊 |
| `mm_malloc(0)` | `NULL` | 實作定義行為（本規格選 NULL） |
| `mm_free(NULL)` | 無動作 | 7.22.3.3 |
| `mm_malloc(SIZE_MAX - 8)` | `NULL` | 加上 overhead 會溢位，不能繞回成小區塊 |
| `mm_malloc(1 << 40)` | `NULL` | 超過 memlib 保留的 1 GiB |
| 依序 free a、c、b（相鄰三塊）後 `mm_malloc(300)` | 回傳 a 的位址 | 四種合併情況中的「前後都空閒」 |
| free 1000-byte 區塊後連續 `mm_malloc(100)` 兩次 | 第二塊緊接在第一塊後 128 bytes | 切割 |
| 最後一塊 `mm_realloc(a, 4000)` | 位址不變、內容保留 | heap 尾端原地成長 |
| 後面被擋住時 `mm_realloc` 變大 | 位址改變、內容保留 | 搬家 |
| `mm_realloc(p, 10)` 縮小 | 位址不變 | 原地縮小 |
| `mm_realloc(p, 0)` | `NULL`，`p` 已釋放 | 本規格採 glibc 行為；C23 為 UB |
| `mm_calloc(SIZE_MAX/4 + 2, 4)` | `NULL` | 乘法溢位 |
| 重用寫髒的區塊做 `mm_calloc(64, 4)` | 256 bytes 全為 0 | calloc 必須真的清零 |
| 隨機壓力測試，多個 seed、100 萬次 | 指紋全部正確、`mm_check` 全部通過 | 綜合正確性 |

## 延伸挑戰（加分）

- **去掉已配置區塊的 footer**：在 header 的 bit 1 記錄「前一塊是否已配置」（glibc 的 P 位元），只有空閒塊寫 footer。最小區塊仍是 32，但每個已配置區塊省 8 bytes，`malloc(24)` 可以塞進 32 bytes 的區塊。測試中的兩個確切位址斷言要跟著改。
- **分離式空閒串列（segregated free lists）**：依大小分成多條串列（例如 32、48、64、…、2 的冪次區間），malloc 先到對應的串列找。量測速度與利用率的變化，對照 [Ch 17](./17-malloc-internals.md) 的 bins 設計。
- **比較放置策略**：同一份壓力測試，實作 next-fit、best-fit、以位址排序的串列（address-ordered first-fit），記錄利用率與時間。Wilson 等人 1995 年的綜述認為 address-ordered first-fit 與 best-fit 在真實程式上碎片都很低——用你的實驗驗證或反駁。
- **大區塊走 mmap、頂端空閒區歸還**：仿照 glibc，超過某個門檻直接 `mmap`，free 時 `munmap`；heap 頂端的空閒塊超過門檻時縮小 break。
- **thread-safe**：最簡單的版本加一把全域 `pthread_mutex`；進階版每個執行緒一個小快取（模仿 tcache），量測多執行緒吞吐量。
- **變成真正的 malloc**：把函式改名為 `malloc`/`free`/`realloc`/`calloc`，加上 `posix_memalign`、`aligned_alloc`、`malloc_usable_size`，編成共享程式庫，用 `LD_PRELOAD` 跑 `ls`、`python3`。這會撞上許多真實問題（遞迴呼叫、執行緒、`fork`），[練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 與 [Ch 40](./40-dynamic-linking.md) 會用到這些知識。

## 自我檢核

- [ ] 不看解答，能畫出你的區塊格式，並說明 `prev_blk` 為什麼需要 footer 嗎？
- [ ] 能解釋為什麼 heap 上「永遠不會有兩個相鄰的空閒塊」，以及這個不變量讓 `place` 省掉了什麼嗎？
- [ ] 你的 `realloc` 在每一條失敗路徑上都保證舊區塊不被破壞嗎？指出程式碼中對應的那幾行。
- [ ] 能說明利用率只有四成的原因，並提出兩種改善方法嗎？
- [ ] 能說出自己的實作和參考解答的差異（格式、搜尋策略、realloc 處理），並解釋各自取捨嗎？

Part 3 到這裡結束：你看過了記憶體階層、對齊、配置器，並親手寫了一個。接下來 Part 4 換個角度看記憶體裡的位元——數值怎麼表示、怎麼溢位、怎麼用位元運算操作它們。

→ [Ch 21 數值系統：進位制、二補數與同餘](./21-number-systems.md)
