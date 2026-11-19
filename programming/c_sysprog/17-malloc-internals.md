# Ch 17 — malloc/free 實作：glibc ptmalloc 與其他配置器

> **目標**：打開 `malloc` 這個黑盒子。學完後你能從記憶體傾印認出 glibc 的 chunk 結構、說出一次 `malloc`/`free` 依序經過哪些 bin、解釋 arena 與 mmap threshold 的設計理由，並能比較 jemalloc、tcmalloc、mimalloc 的取捨；同時把 C 標準對 `malloc`/`calloc`/`realloc`/`free` 的語意講精確。

> **對應原文**：[你所不知道的 C 語言：記憶體管理、對齊及硬體特性篇](https://hackmd.io/@sysprog/c-memory) 的「glibc 的 malloc/free 實作」「C 標準配置函式與其他開放原始碼實作」等節；[函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「藏在 Heap 裡的細節」「如何偵測 double free 呢？」等節。

> **環境**：WSL2 Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G，8 核 16 執行緒），gcc 11.4、**glibc 2.35**。本章引用的原始碼行為以 [glibc 2.35 的 malloc/malloc.c](https://github.com/bminor/glibc/blob/glibc-2.35/malloc/malloc.c) 為準；glibc 的 malloc 每隔幾版就有變動（2.26 加 tcache、2.32 加 safe-linking、2.34 拿掉 hook、2.42 擴大 tcache），不同版本的細節會不同。配置器比較用的 jemalloc 5.2.1、gperftools tcmalloc 2.9.1、mimalloc 2.0.5 是 Ubuntu 22.04 套件庫的版本，以 `apt-get download` 下載解開後用 `LD_PRELOAD` 載入，沒有安裝進系統。

## 為什麼需要這個？

`free(p)` 只收一個指標，沒有大小。那 `free` 怎麼知道要還多少？下一次 `malloc` 又怎麼從一堆歸還的碎塊裡挑一塊合適的？這兩個問題的答案就是配置器（allocator）的全部工作。

一個通用配置器同時要滿足幾個互相拉扯的目標：

- **快**：`malloc`/`free` 是程式裡被呼叫最頻繁的函式之一，每次多花 10 ns 都會被放大。
- **省**：減少**碎片（fragmentation）**。碎片分兩種：**內部碎片**是配出去的區塊比要求的大（對齊、header）；**外部碎片**是空閒空間總量夠，但被切成一堆小塊，沒有一塊夠大。
- **可擴展**：多執行緒同時 `malloc` 時，不能所有人搶同一把鎖。
- **耐用**：程式寫錯（double free、越界）時盡量偵測到，而不是默默把 heap 弄壞。

這些目標沒有一個配置器能同時做到最好，所以才會有那麼多實作。演進脈絡大致是：

1. **K&R malloc**（1978/1988，《The C Programming Language》8.7 節）：一條環狀的空閒串列，first-fit。幾十行程式，單執行緒時代夠用。
2. **dlmalloc**（Doug Lea，1987 起）：引入 boundary tag、依大小分類的 bins、和相鄰空閒塊合併。之後二十年幾乎所有 Unix 配置器的基礎。
3. **ptmalloc**（Wolfram Gloger）：在 dlmalloc 上加入多個 arena，讓不同執行緒用不同的 heap 以減少鎖競爭。ptmalloc2 被併入 glibc，這就是今天 Linux 上預設的 `malloc`。
4. **glibc 2.26（2017）加入 tcache**：每個執行緒自己的小快取，常見的小配置完全不用鎖。
5. 同一時期，大廠為了伺服器的多核心延展性各自造輪子：**jemalloc**（FreeBSD、Facebook）、**tcmalloc**（Google）、**mimalloc**（Microsoft）。

> [Ch 15](./15-memory-hierarchy.md) 已經看過 `malloc` 怎麼用 `brk`/`mmap` 向核心拿記憶體；[Ch 16](./16-alignment-and-padding.md) 說明了為什麼回傳位址一定是 16 的倍數。本章站在這兩章的基礎上往配置器內部走。

## 先建立直覺

想像一個倉庫管理員管理一長條貨架（heap）：

- 每個出租出去的格位，**前面貼一張標籤**寫著格位大小（chunk header）。客人還格位時只要交回鑰匙（指標），管理員看標籤就知道多大——這回答了「`free` 怎麼知道大小」。
- 還回來的格位不會馬上清掉，而是依大小**分類掛在不同的掛勾上**（bins），下次有人要類似大小的直接給。
- 相鄰兩格都空了，就**拆掉中間隔板合併**成一格大的，避免貨架被切得零零碎碎。
- 貨架最末端還沒切過的那一大段叫 **top chunk**，所有掛勾都沒有合適的，就從這裡切一塊。top 不夠了，再向房東（核心）多租一段（`brk`）。
- 特別大的貨物不放貨架，直接另租一間倉庫（`mmap`），還的時候整間退租。
- 每個員工（執行緒）口袋裡還有幾張常用大小的格位鑰匙（tcache），自己拿自己還，不用排隊找管理員。

```
 glibc main arena 的 heap（低位址在左）

 brk 起點
 │
 ▼
 ┌──────────────────┬───────┬───────┬────────┬───────┬──────────────────────────┐
 │ tcache_perthread │ chunk │ chunk │ chunk  │ chunk │       top chunk          │
 │ struct (0x290)   │ 使用中│ 空閒  │ 使用中 │ 空閒  │  （還沒切過的剩餘空間）  │
 └──────────────────┴───────┴───┬───┴────────┴───┬───┴──────────────────────────┘
                                 │                │                              ▲
                                 └─ 掛在某個 bin ─┘                         program break
```

## C 標準只規範語意

在拆 glibc 之前，先確定**標準保證了什麼、沒保證什麼**。N1570 7.22.3（C23 是 N3220 7.24.3）：

- **¶1**：連續配置之間的順序與是否相鄰**未指定**；成功時回傳的指標對任何基本對齊的型別都合適；每次配置得到的物件互不重疊。**要求大小為 0 時，行為是實作定義**：回傳空指標，或當成某個非零大小、但回傳的指標不能拿來存取物件。
- **`malloc`（7.22.3.4）**：配置的內容值是不確定的（indeterminate）。
- **`calloc`（7.22.3.2）**：配置 `nmemb` 個、每個 `size` bytes 的陣列，**所有位元清為零**。C23 明文規定：`nmemb * size` 會在 `size_t` 上繞回時必須回傳空指標。C11 沒有這句話，但「配置一個 nmemb × size 的陣列」本身就不能用繞回後的小數字敷衍，主流實作一直都有檢查。
- **`free`（7.22.3.3）**：`ptr` 為空指標時什麼都不做；`ptr` 不是配置函式回傳的值、或已經被 `free`/`realloc` 釋放過，**行為未定義**。
- **`realloc`（7.22.3.5）**：釋放舊物件、回傳一個指定大小的新物件，內容保留到新舊大小的較小者；`ptr` 為空指標時等同 `malloc`；**配置失敗時舊物件不釋放、值不變**。C23 另外規定 `realloc(p, 0)`（`p` 非空）是**未定義行為**——C11/C17 時代各家實作對它的處理不一致（有的釋放並回傳 NULL，有的回傳一個小區塊），委員會乾脆不再定義它。

用程式把這些語意和 glibc 的實際行為對起來：

```c
/* realloc.c: realloc 何時原地成長、何時搬家；calloc 的溢位檢查 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>

int main(void)
{
    /* 1. 後面緊鄰 top chunk：原地成長 */
    char *a = malloc(100);
    char *a2 = realloc(a, 1000);
    printf("realloc(a,1000) : %p -> %p  %s\n", (void *)a, (void *)a2, a == a2 ? "in place" : "moved");

    /* 2. 後面被別人占住：只能搬家 */
    char *b = malloc(100);
    char *guard = malloc(100);              /* 擋在 b 後面 */
    strcpy(b, "hello");
    char *b2 = realloc(b, 1000);
    printf("realloc(b,1000) : %p -> %p  %s, content=\"%s\"\n", (void *)b, (void *)b2,
           b == b2 ? "in place" : "moved", b2);

    /* 3. 縮小：通常原地 */
    char *b3 = realloc(b2, 10);
    printf("realloc(b2,10)  : %p -> %p\n", (void *)b2, (void *)b3);

    /* 4. 失敗時舊區塊仍有效 —— 這就是 p = realloc(p, n) 的危險 */
    errno = 0;
    char *c = realloc(b3, SIZE_MAX / 2);
    printf("realloc(huge)   : %p errno=%s, old block still valid: \"%s\"\n",
           (void *)c, strerror(errno), b3);

    /* 5. calloc 會檢查 n * size 溢位；手算的 malloc(n * size) 會悄悄繞回 */
    size_t n = SIZE_MAX / 4 + 2, sz = 4;    /* n*sz 在 64 位元上繞回成 4 */
    errno = 0;
    void *z = calloc(n, sz);
    printf("calloc(%#zx, 4)  = %p errno=%s\n", n, z, strerror(errno));
    void *m = malloc(n * sz);
    printf("malloc(n*4)=malloc(%zu) = %p   <- 只拿到 %zu bytes!\n", n * sz, m, n * sz);

    /* 6. realloc(p, 0) 的 glibc 行為（C23 起是 UB，不要寫） */
    char *d = malloc(10);
    char *d2 = realloc(d, 0);
    printf("realloc(d, 0)   = %p\n", (void *)d2);

    free(a2); free(guard); free(b3); free(m);
    return 0;
}
```

```
realloc(a,1000) : 0x615487a3e2a0 -> 0x615487a3e2a0  in place
realloc(b,1000) : 0x615487a3f6a0 -> 0x615487a3f780  moved, content="hello"
realloc(b2,10)  : 0x615487a3f780 -> 0x615487a3f780
realloc(huge)   : (nil) errno=Cannot allocate memory, old block still valid: "hello"
calloc(0x4000000000000001, 4)  = (nil) errno=Cannot allocate memory
malloc(n*4)=malloc(4) = 0x615487a3fb70   <- 只拿到 4 bytes!
realloc(d, 0)   = (nil)
```

（嚴格說，`realloc` 成功後再讀舊指標 `a`、`b` 的值做比較也是 UB——舊指標的值在物件釋放後變成不確定（N1570 6.2.4 ¶2）。這裡只為了觀察 glibc 的行為才這樣寫，正式程式不要這樣做。）

第 5 點是真實的資安問題：`malloc(n * size)` 的乘法溢位後只配到 4 bytes，程式卻以為拿到了 n 個元素，接下來的寫入就是 heap overflow。這也是 Nathaniel Smith 那篇〈Why does calloc exist?〉指出的 `calloc` 兩個存在理由之一。另一個理由是效能：大塊 `calloc` 走 `mmap`，而核心給的新 page 本來就是零，glibc 知道這點，可以**跳過 memset**；`malloc` + `memset` 則會把每一頁都碰一遍，等於提前觸發所有 page fault（[Ch 15](./15-memory-hierarchy.md) 的 demand paging）。整數溢位的完整討論在 [Ch 22](./22-integer-overflow.md)。

## chunk：每塊記憶體前面的標籤

glibc 管理的單位叫 **chunk**。定義在 malloc.c（2.35 第 1153 行）：

```c
struct malloc_chunk {
  INTERNAL_SIZE_T      mchunk_prev_size;  /* Size of previous chunk (if free).  */
  INTERNAL_SIZE_T      mchunk_size;       /* Size in bytes, including overhead. */

  struct malloc_chunk* fd;         /* double links -- used only if free. */
  struct malloc_chunk* bk;

  /* Only used for large blocks: pointer to next larger size.  */
  struct malloc_chunk* fd_nextsize; /* double links -- used only if free. */
  struct malloc_chunk* bk_nextsize;
};
```

這個 struct 不是「每個 chunk 都完整擁有這六個欄位」，而是一個**疊在記憶體上的視角**：哪些欄位有效取決於 chunk 的狀態。x86-64 上 `INTERNAL_SIZE_T` 是 8 bytes（`SIZE_SZ = 8`）：

```
  已配置的 chunk                              空閒的 chunk（small/large/unsorted bin）

  chunk → ┌──────────────────────────┐        chunk → ┌──────────────────────────┐
          │ prev_size（前一塊空閒時   │                │ prev_size                │
          │ 才有效，否則借給前一塊）  │                ├──────────────────────────┤
          ├──────────────────────┬─┬─┤                │ size               │A│M│P│
          │ size                 │A│M│P│              ├──────────────────────────┤
  mem  →  ├──────────────────────┴─┴─┤                │ fd → 同一 bin 的下一塊   │
  (malloc │                          │                ├──────────────────────────┤
  回傳的  │ 使用者資料               │                │ bk → 同一 bin 的上一塊   │
  指標)   │                          │                ├──────────────────────────┤
          │                          │                │ fd_nextsize / bk_nextsize│
          │                          │                │ （只有 large bin 用）    │
  next →  ├──────────────────────────┤                ├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
          │ 下一塊的 prev_size 欄位： │                │ ...                      │
          │ 本塊使用中時，這 8 bytes │        next →  ├──────────────────────────┤
          │ 也算本塊的可用空間       │                │ prev_size = 本塊大小     │ ← boundary tag
          └──────────────────────────┘                └──────────────────────────┘
```

要點：

- **`malloc` 回傳的指標 = chunk 位址 + 16**（`chunk2mem`）。`free(p)` 減 16 就找到 header，讀出 size——「`free` 不用給大小」的答案。
- **size 一定是 16 的倍數**，最低 3 位元必為 0，所以被拿來當旗標（[Ch 16](./16-alignment-and-padding.md) 末尾的 pointer tagging 同一招）：**P（PREV_INUSE，bit 0）** 前一塊使用中；**M（IS_MMAPPED，bit 1）** 這塊是單獨 mmap 來的；**A（NON_MAIN_ARENA，bit 2）** 這塊屬於非主 arena。
- **boundary tag**：空閒 chunk 會在「下一塊的 prev_size」寫上自己的大小。下一塊被 free 時，看自己的 P 位元知道前一塊空閒，再讀 prev_size 就能往回找到前一塊的開頭來合併——不用從頭掃描。這是 Knuth 在 TAOCP 第一卷描述的技巧，dlmalloc 把它發揚光大。
- **空閒時，使用者資料區被拿來放 fd/bk 指標**。所以最小 chunk 要容納 prev_size、size、fd、bk 四個 8-byte 欄位 = 32 bytes（`MINSIZE`）。
- **prev_size 欄位的空間共用**：本塊使用中時，下一塊的 prev_size 沒有意義，就借給本塊存使用者資料。所以每個使用中的 chunk 實際只有 8 bytes overhead。

偷看 size 欄位驗證上面每一條：

```c
/* chunk.c: 偷看 glibc chunk header 的 size 欄位與 malloc_usable_size */
#include <stdio.h>
#include <stdlib.h>
#include <malloc.h>
#include <stdint.h>

static void show(size_t req)
{
    char *p = malloc(req);
    size_t raw = *(size_t *)((uintptr_t)p - sizeof(size_t));  /* 使用者指標前 8 bytes 是 size 欄位 */
    printf("malloc(%6zu) -> %p  size field=%#8zx  chunk=%6zu  A=%zu M=%zu P=%zu  usable=%zu\n",
           req, (void *)p, raw, raw & ~(size_t)7,
           (raw >> 2) & 1, (raw >> 1) & 1, raw & 1, malloc_usable_size(p));
    /* 故意不 free，讓每次都切新的 chunk */
}

int main(void)
{
    size_t reqs[] = { 0, 1, 24, 25, 40, 100, 1000, 200000 };
    for (size_t i = 0; i < sizeof reqs / sizeof reqs[0]; i++)
        show(reqs[i]);
    return 0;
}
```

（讀 `p` 前面的 8 bytes 在標準 C 裡是越界存取，這是觀察 glibc 內部用的手段，不是正當寫法。用 `((size_t *)p)[-1]` 寫的話 gcc 11 會給 `-Wuninitialized` 警告；先轉成 `uintptr_t` 再減可以避開這個靜態分析。）

```
malloc(     0) -> 0x6546366be2a0  size field=    0x21  chunk=    32  A=0 M=0 P=1  usable=24
malloc(     1) -> 0x6546366bf2d0  size field=    0x21  chunk=    32  A=0 M=0 P=1  usable=24
malloc(    24) -> 0x6546366bf2f0  size field=    0x21  chunk=    32  A=0 M=0 P=1  usable=24
malloc(    25) -> 0x6546366bf310  size field=    0x31  chunk=    48  A=0 M=0 P=1  usable=40
malloc(    40) -> 0x6546366bf340  size field=    0x31  chunk=    48  A=0 M=0 P=1  usable=40
malloc(   100) -> 0x6546366bf370  size field=    0x71  chunk=   112  A=0 M=0 P=1  usable=104
malloc(  1000) -> 0x6546366bf3e0  size field=   0x3f1  chunk=  1008  A=0 M=0 P=1  usable=1000
malloc(200000) -> 0x7c585399d010  size field= 0x31002  chunk=200704  A=0 M=1 P=0  usable=200688
```

逐條解讀：

- **chunk 大小的公式**：`request2size(req)` = `max(MINSIZE, (req + 8 + 15) & ~15)`——加上 size 欄位的 8 bytes，向上取整到 16。所以 1–24 bytes 都是 32 的 chunk（可用 24 = 32 − 8，借用了下一塊的 prev_size），25 bytes 就跳到 48。
- **`malloc(0)` 也回傳一個有效指標**：glibc 選擇了 7.22.3 ¶1 的第二種實作定義行為，給你一個最小的 chunk。
- **`0x21` = 32 | P**：P 位元是 1，因為前一塊在使用中。
- **`malloc(0)` 和 `malloc(1)` 之間差了 0x1030**：第一個 `printf` 讓 stdio 為 stdout 配置了緩衝區。stdout 接的是管線，緩衝區大小是 4096（`st_blksize`），chunk 是 4096 + 16 = 0x1010；加上 `malloc(0)` 的 0x20，剛好 0x1030。你的程式裡看不見的 `malloc`，比你想像的多。
- **20 萬 bytes 走 mmap**：M = 1；chunk 大小 200704 = 49 個 page；mmap 的 chunk 不和別人相鄰，P 沒有意義。
- **第一個 chunk 在 heap 開頭 + 0x2a0**：前面那個 0x290 的 chunk 是 tcache 的管理結構，下一節會看到它。

## bins：空閒 chunk 的分類掛勾

free 掉的 chunk 依大小與時間掛進不同的串列，這些串列統稱 **bins**。原文給的是 32 位元的數字，以下是 x86-64、glibc 2.35 的值（括號內是 malloc.c 的巨集）：

| bin | 數量 | 範圍（chunk 大小） | 串列 | 合併 | 誰能用 |
|---|---|---|---|---|---|
| **tcache** | 64 個（`TCACHE_MAX_BINS`），每個最多 7 塊（`TCACHE_FILL_COUNT`） | 32–1040，間距 16（使用者請求 ≤ 1032 bytes） | 單向，LIFO | 不合併 | 每執行緒私有，**不用鎖** |
| **fastbin** | 10 個 | 預設 ≤ 0x80（128，`DEFAULT_MXFAST = 64 * SIZE_SZ / 4`），間距 16 | 單向，LIFO | 不立即合併 | arena 共用，要鎖 |
| **unsorted** | 1 個（bin 1） | 任意 | 雙向 | 已合併 | 剛 free 的 small/large chunk 先放這 |
| **small** | 62 個（bin 2–63） | < 1024（`MIN_LARGE_SIZE`），間距 16，每個 bin 大小固定 | 雙向，FIFO | 會合併 | arena 共用 |
| **large** | 63 個（bin 64–126） | ≥ 1024，間距從 64 開始逐段放大（512、4096、32768、262144），最後一個收所有更大的 | 雙向，依大小排序 | 會合併 | arena 共用 |

設計邏輯是「越常見、越小的請求，路徑越短」：

- **tcache 與 fastbin 不合併**：小物件常常是「free 了馬上又要一樣大的」，合併再切割是白做工。代價是碎片，所以 fastbin 的 chunk 會在 `malloc_consolidate` 時批次合併（例如要配置 large chunk 而 fastbin 有東西時）。fastbin 的 chunk 在 free 時**保持使用中標記**（不清除下一塊的 P 位元），所以鄰居不會把它當成可合併的空閒塊。
- **small bin 每個 bin 只有一種大小**：找到 bin 就一定合用，O(1)。
- **large bin 一個 bin 涵蓋一段範圍**：bin 內依大小排序，用 `fd_nextsize`/`bk_nextsize` 跳過同大小的重複項，找 **best-fit**（最小的夠用者）。
- **unsorted bin 是緩衝區**：free 時不急著分類，先丟進 unsorted；下次 `malloc` 掃描它，剛好大小就直接用，否則才歸位到 small/large bin。這是一種 lazy 策略——很多 chunk 在被分類之前就又被要回去了。

### tcache 實測：LIFO、safe-linking、7 塊上限

```c
/* tcache.c: 觀察 tcache 的 LIFO 重用、safe-linking 與 7 個上限 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

int main(void)
{
    void *heap_base = (char *)malloc(1) - 0x2a0;   /* 第一個 chunk 前面是 0x290 的 tcache_perthread_struct */
    printf("tcache_perthread_struct chunk size field = %#zx\n",
           *(size_t *)((uintptr_t)heap_base + 8));

    char *p[9];
    for (int i = 0; i < 9; i++) p[i] = malloc(40);           /* 都落在 0x30 這個 size class */
    for (int i = 0; i < 9; i++) printf("p[%d]=%p\n", i, (void *)p[i]);

    for (int i = 0; i < 9; i++) free(p[i]);                  /* 前 7 個進 tcache，後 2 個進 fastbin */

    uintptr_t fd = *(uintptr_t *)p[1];                       /* p[1] 的 next 欄位（被保護過） */
    uintptr_t decoded = fd ^ ((uintptr_t)p[1] >> 12);        /* PROTECT_PTR: next ^ (pos >> 12) */
    printf("p[1] stored fd = %#lx, decoded = %#lx (p[0]=%p)\n",
           (unsigned long)fd, (unsigned long)decoded, (void *)p[0]);

    for (int i = 0; i < 9; i++) {
        char *q = malloc(40);
        printf("malloc #%d -> %p\n", i, (void *)q);
    }
    return 0;
}
```

```
tcache_perthread_struct chunk size field = 0x291
p[0]=0x569a6a7d52d0
p[1]=0x569a6a7d5300
p[2]=0x569a6a7d5330
p[3]=0x569a6a7d5360
p[4]=0x569a6a7d5390
p[5]=0x569a6a7d53c0
p[6]=0x569a6a7d53f0
p[7]=0x569a6a7d5420
p[8]=0x569a6a7d5450
p[1] stored fd = 0x569f03dbf505, decoded = 0x569a6a7d52d0 (p[0]=0x569a6a7d52d0)
malloc #0 -> 0x569a6a7d53f0
malloc #1 -> 0x569a6a7d53c0
malloc #2 -> 0x569a6a7d5390
malloc #3 -> 0x569a6a7d5360
malloc #4 -> 0x569a6a7d5330
malloc #5 -> 0x569a6a7d5300
malloc #6 -> 0x569a6a7d52d0
malloc #7 -> 0x569a6a7d5450
malloc #8 -> 0x569a6a7d5420
```

- **0x290 的管理結構**：`tcache_perthread_struct` 有 64 個 `uint16_t` 計數（128 bytes）加 64 個指標（512 bytes）= 640 = 0x280，加 16 bytes header = 0x290。這就是 [Ch 15](./15-memory-hierarchy.md) 看到「第一塊在 break + 0x2a0」的原因。
- **LIFO**：p[0]–p[6] 依序 free 進 tcache，`malloc` 時從 p[6] 倒著拿回來。最近 free 的最先被重用——它最可能還在 cache 裡。
- **7 塊上限**：p[7]、p[8] 進了 fastbin。`malloc #7` 從 fastbin 拿到 p[8]（fastbin 也是 LIFO）；同時 glibc 把 fastbin 裡剩下的同大小 chunk（p[7]）搬進 tcache，所以 `malloc #8` 從 tcache 拿到 p[7]。
- **safe-linking**：p[1] 的 next 欄位存的不是 p[0] 的位址，而是 `p[0] ^ (&p[1]->next >> 12)`。這是 glibc 2.32 加入的 `PROTECT_PTR` 巨集：

```c
#define PROTECT_PTR(pos, ptr) \
  ((__typeof (ptr)) ((((size_t) pos) >> 12) ^ ((size_t) ptr)))
```

  攻擊者若能覆寫 free chunk 的 next 指標（UAF 或 heap overflow 的經典手法），在不知道 heap 位址（ASLR）的情況下，寫進去的值解碼後會是垃圾位址，而且 glibc 解碼後還會檢查對齊，大幅提高利用難度。原文「函式呼叫篇」的 gdb 傾印裡，fd 欄位看起來像 `0x0000000000000405` 這種怪數字，就是這個原因。

## malloc 與 free 的完整流程

原文的流程圖沿用經典 `_int_malloc`，省略了 tcache。加上 tcache 之後，glibc 2.35 的 `malloc(n)` 路徑：

```
 malloc(n)
   │  n 換算成 chunk 大小 nb（加 8、對齊 16、至少 32）
   ▼
 ┌─────────────────────┐ 有
 │ tcache[nb] 有貨？   ├────────────────────────────────────────────▶ 回傳（無鎖）
 └─────────┬───────────┘
           │ 沒有：取得 arena 的鎖，進 _int_malloc
           ▼
 ┌─────────────────────┐ 有
 │ nb ≤ fastbin 上限且 ├──▶ 取出；同大小的剩餘 chunk 搬進 tcache ──▶ 回傳
 │ fastbin[nb] 有貨？  │
 └─────────┬───────────┘
           ▼
 ┌─────────────────────┐ 有
 │ nb 屬 small 範圍且  ├──▶ 取出；同樣把剩餘的搬進 tcache ─────────▶ 回傳
 │ smallbin[nb] 有貨？ │
 └─────────┬───────────┘
           │ 若 nb 屬 large 範圍且 fastbin 有東西：malloc_consolidate（合併 fastbin，放進 unsorted）
           ▼
 ┌──────────────────────────────────────┐
 │ 掃描 unsorted bin（最多 10000 個）： │ 剛好大小 ──▶ 回傳（或先塞進 tcache）
 │  · 小請求且只剩 last_remainder：切它 │
 │  · 其餘 chunk 歸位到 small/large bin │
 └─────────┬────────────────────────────┘
           ▼
 ┌─────────────────────┐ 有
 │ small/large bin 中  ├──▶ 切割，剩餘部分放回 unsorted ────────────▶ 回傳
 │ best-fit？          │
 └─────────┬───────────┘
           ▼
 ┌─────────────────────┐ 夠
 │ top chunk 夠大？    ├──▶ 從 top 切 ──────────────────────────────▶ 回傳
 └─────────┬───────────┘
           │ 不夠：nb ≥ mmap threshold → 直接 mmap；否則 brk 延展 top（thread arena 則擴大其 heap）
           ▼
         回傳（或 NULL）
```

`free(p)` 的路徑：

1. **快速檢查**：位址對齊嗎？size 合理嗎？（不合理就 `free(): invalid pointer` 中止）
2. **M 位元為 1**：`munmap` 整塊，並可能上調 mmap threshold（見下節）。
3. **tcache 對應 bin 未滿 7 塊**：放進 tcache，結束（無鎖）。
4. **chunk ≤ fastbin 上限**：放進 fastbin，不合併。
5. **其他**：和前一塊（看 P 位元 + prev_size）、後一塊（看再下一塊的 P 位元）合併；如果後一塊是 top chunk，就併入 top。合併結果放進 unsorted bin。
6. **top chunk 太大**（超過 `M_TRIM_THRESHOLD`，預設 128 KiB）：用 `brk` 縮回去，把記憶體還給核心。

**last remainder**：從較大的 chunk 切出小請求時，剩下的部分被記為 last remainder。下一個小請求優先切它，讓連續的小配置在位址上相鄰，增進局部性。

## arena：多執行緒的解法

ptmalloc 對多執行緒的回答是**多個 arena**。arena 是「一套獨立的 bins + top chunk + 一把鎖」，以 `struct malloc_state` 表示：

- **main arena**：只有一個，管理 `brk` heap，`malloc_state` 本身是 libc 裡的靜態變數。
- **thread arena**：其他執行緒第一次 `malloc` 時，glibc 為它找一個沒人用的 arena，沒有就新建。thread arena 用 `mmap` 保留一塊 heap（x86-64 上 `HEAP_MAX_SIZE` = 2 × `DEFAULT_MMAP_THRESHOLD_MAX` = 64 MiB），起初只把前段設為可讀寫，不夠再逐步開放，用完再 mmap 新的 heap，以 `heap_info` 串起來。
- **arena 數量上限**：64 位元是 8 × 核心數（`NARENAS_FROM_NCORES`），32 位元是 2 × 核心數。超過之後執行緒開始共用 arena，又回到搶鎖。

```c
/* arena.c: 不同執行緒的 malloc 落在不同 arena；用 malloc_stats/mallinfo2 觀察 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <pthread.h>
#include <malloc.h>

static void *worker(void *arg)
{
    char *p = malloc(1000);
    size_t raw = *(size_t *)((uintptr_t)p - 8);
    printf("thread %ld: p=%p  NON_MAIN_ARENA=%zu\n", (long)(intptr_t)arg, (void *)p, (raw >> 2) & 1);
    return p;                                /* 不 free，留著給 malloc_stats 看 */
}

int main(void)
{
    char *m = malloc(1000);
    printf("main    : p=%p  NON_MAIN_ARENA=%zu\n", (void *)m,
           (*(size_t *)((uintptr_t)m - 8) >> 2) & 1);
    pthread_t t[2];
    for (long i = 0; i < 2; i++) {
        pthread_create(&t[i], NULL, worker, (void *)(intptr_t)i);
        pthread_join(t[i], NULL);            /* 依序跑，避免輸出交錯 */
    }
    struct mallinfo2 mi = mallinfo2();
    printf("mallinfo2: arena=%zu uordblks=%zu fordblks=%zu hblks=%zu\n",
           mi.arena, mi.uordblks, mi.fordblks, mi.hblks);
    fflush(stdout);
    malloc_stats();                          /* 印到 stderr */
    return 0;
}
```

```
main    : p=0x5a48d8c692a0  NON_MAIN_ARENA=0
thread 0: p=0x7cae88000b70  NON_MAIN_ARENA=1
thread 1: p=0x7cae88000f60  NON_MAIN_ARENA=1
mallinfo2: arena=270336 uordblks=10336 fordblks=260000 hblks=0
Arena 0:
system bytes     =     135168
in use bytes     =       6064
Arena 1:
system bytes     =     135168
in use bytes     =       4272
Total (incl. mmap):
system bytes     =     270336
in use bytes     =      10336
max mmap regions =          0
max mmap bytes   =          0
```

- 執行緒的 chunk 在 `0x7cae...`（mmap 區），A 位元為 1。`free` 時 glibc 靠這個位元知道該把 chunk 還給哪個 arena。
- thread 1 的位址緊接在 thread 0 後面：thread 0 結束時把 arena 放回空閒清單，thread 1 接手了同一個 arena。所以只有 Arena 0 和 Arena 1。
- `mallinfo2`（glibc 2.33 加入）取代舊的 `mallinfo`：舊版的欄位是 `int`，heap 超過 2 GiB 就溢位。

per-thread arena 的代價是**記憶體膨脹**：每個 arena 各自保留空閒空間，執行緒多的伺服器 RSS 會比預期大很多。常見的調整是設環境變數 `MALLOC_ARENA_MAX=2`（或 tunable `glibc.malloc.arena_max`），以些許鎖競爭換取記憶體。這也是很多人把伺服器換成 jemalloc 的原因之一。

## mmap threshold：會自己調整的門檻

大於門檻的請求直接 `mmap`，好處是 `free` 時能立刻還給核心、不在 heap 留下大洞；壞處是每次都是系統呼叫加 page fault，且核心給的 page 要清零。預設門檻 128 KiB，但它是**動態**的：

```c
/* thresh.c: mmap threshold 會在 free 掉 mmap chunk 後動態上調 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static int is_mmapped(void *p) { return (int)((*(size_t *)((uintptr_t)p - 8) >> 1) & 1); }

int main(void)
{
    size_t sz = 200 * 1024;
    void *a = malloc(sz);
    printf("1st malloc(200 KiB): %p  IS_MMAPPED=%d\n", a, is_mmapped(a));
    free(a);                                   /* free 一個 mmap chunk：threshold 調成它的大小 */
    void *b = malloc(sz);
    printf("2nd malloc(200 KiB): %p  IS_MMAPPED=%d\n", b, is_mmapped(b));
    free(b);
    return 0;
}
```

```
$ ./thresh
1st malloc(200 KiB): 0x7dbe0abef010  IS_MMAPPED=1
2nd malloc(200 KiB): 0x5c972b8be2b0  IS_MMAPPED=0
$ GLIBC_TUNABLES=glibc.malloc.mmap_threshold=131072 ./thresh
1st malloc(200 KiB): 0x78a8afcf0010  IS_MMAPPED=1
2nd malloc(200 KiB): 0x78a8afcf0010  IS_MMAPPED=1
```

第一次 200 KiB 走 mmap；free 之後 glibc 推論「這個大小是程式常用的暫時配置」，把門檻上調到 200 KiB（同時把 trim threshold 設為兩倍），第二次就改從 brk heap 配置，省下系統呼叫。上調的上限是 `DEFAULT_MMAP_THRESHOLD_MAX`，x86-64 上 4 × 1024 × 1024 × `sizeof(long)` = 32 MiB。只要明確設定過 `M_MMAP_THRESHOLD`、`M_TOP_PAD`、`M_TRIM_THRESHOLD`、`M_MMAP_MAX` 任一個（`mallopt`、環境變數 `MALLOC_MMAP_THRESHOLD_`、或 `GLIBC_TUNABLES`），動態調整就關閉，如第二組輸出所示。這些規則都寫在 `man 3 mallopt`。

## glibc 的安全檢查：double free 是怎麼被抓到的

> buffer overflow 與 ROP 等利用手法見 [Ch 14](./14-function-call-security.md)。heap 版本的利用（tcache poisoning、fastbin dup、house of 系列）都建立在本章的資料結構上；本課只講機制與防禦，實驗請在自己的環境進行。

```c
/* dfree.c: glibc 對 double free 的偵測（依 argv 選擇情境） */
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    int mode = argc > 1 ? atoi(argv[1]) : 0;
    if (mode == 0) {                       /* 情境 0：tcache 裡連續 free 兩次 */
        char *a = malloc(40);
        free(a);
        free(a);
    } else if (mode == 1) {                /* 情境 1：填滿 tcache 後在 fastbin 連續 free 兩次 */
        char *p[8];
        for (int i = 0; i < 8; i++) p[i] = malloc(40);
        for (int i = 0; i < 7; i++) free(p[i]);
        free(p[7]);
        free(p[7]);
    } else {                               /* 情境 2：free 一個不是 malloc 回傳的位址 */
        char *a = malloc(40);
        free(a + 16);
    }
    puts("not detected");
    return 0;
}
```

情境 2 在編譯期就被 gcc 11 抓到一次：

```
dfree.c:20:9: warning: ‘free’ called on pointer ‘a’ with nonzero offset 16 [-Wfree-nonheap-object]
```

執行期三種情境各自的訊息：

```
== mode 0
free(): double free detected in tcache 2
Aborted (core dumped)
== mode 1
double free or corruption (fasttop)
Aborted (core dumped)
== mode 2
free(): invalid pointer
Aborted (core dumped)
```

每個訊息都對應 malloc.c 裡的一個檢查：

- **tcache**：chunk 進 tcache 時，第二個欄位（`key`）寫入一個每個行程隨機產生的值 `tcache_key`（glibc 2.34 起用 `getrandom` 產生；更早的版本存的是 tcache 結構的位址）。`free` 時若發現 `key == tcache_key`，就掃描整條 tcache bin 確認是否真的重複。原文 gdb 傾印裡每個 free chunk 第二格都是同一個 `0x7765b1be7986a3f4`，就是它。
- **fastbin**：只檢查「要 free 的 chunk 是不是 bin 的**第一個**」（fasttop）。所以 `free(a); free(b); free(a);` 這種中間隔一個的 double free **不會**被抓到——這正是經典的 fastbin dup 手法的起點。glibc 的檢查是「便宜的那些」，不是完整的記憶體安全。
- **invalid pointer**：位址沒對齊到 16，或 size 欄位不合理。

要完整偵測，還是要靠 AddressSanitizer 或 Valgrind（`../c_interview/14-asan-valgrind.md`）。

## 觀察工具

| 工具 | 用途 | 備註 |
|---|---|---|
| `malloc_usable_size(p)` | 這塊實際能用多少 bytes | 只能拿來觀察，**不要**依賴它多寫——那是 UB，且 ASan 會報錯 |
| `malloc_stats()` | 各 arena 的系統/使用中 bytes，印到 stderr | 可以在 gdb 裡 `call malloc_stats()` 看執行中的程式 |
| `mallinfo2()` | 結構化統計 | glibc 2.33+ |
| `malloc_info(0, fp)` | 輸出 XML，含每個 bin 的統計 | 最詳細 |
| `malloc_trim(pad)` | 主動把空閒記憶體還給核心 | 也會對 thread arena 用 `madvise(MADV_DONTNEED)` |
| `GLIBC_TUNABLES=glibc.malloc.*` | 調整 tcache_count、mmap_threshold、arena_max 等 | 免改程式，`ld.so --list-tunables` 列出全部 |

glibc 2.34 起移除了 `__malloc_hook`、`__free_hook` 等 hook 變數（它們既是除錯介面，也是利用時常見的覆寫目標）。想攔截 `malloc`，現在的做法是 `LD_PRELOAD` 一個自己的共享程式庫，[Ch 40](./40-dynamic-linking.md) 與 [練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 會實作。

## 其他配置器

C 標準只規範介面，同一組 `malloc`/`free` 有很多實作。它們共同的主題是：**讓大部分操作只碰執行緒（或 CPU）私有的資料**。

- **[jemalloc](https://github.com/jemalloc/jemalloc)**（Jason Evans，2005 年為 FreeBSD 開發，後由 Facebook 維護）：小物件依**size class** 分類，放在固定大小的 run/slab 裡；多個 arena，執行緒以輪替方式分配到 arena；每個執行緒還有 tcache。強調低碎片、長時間執行時 RSS 穩定，以及豐富的統計與 profiling 介面。
- **[tcmalloc](https://github.com/google/tcmalloc)**（Google）：前端快取、中間的 transfer cache、後端的 page heap 三層。新版（GitHub 上的 google/tcmalloc）預設用 **per-CPU cache**，靠 Linux 的 rseq（restartable sequences）讓同一顆 CPU 上的操作免鎖；舊版（gperftools 裡的 tcmalloc，也就是 Ubuntu 套件 `libtcmalloc-minimal4`）用 per-thread cache。
- **[mimalloc](https://github.com/microsoft/mimalloc)**（Daan Leijen 等，Microsoft Research，2019）：核心想法是 **free list sharding**——不是每個 size class 一條大串列，而是**每個 page（mimalloc 的 page 通常 64 KiB）各自一條空閒串列**，而且再分成「本執行緒 free 的」和「其他執行緒 free 的」兩條，後者用 atomic 操作推入、由擁有者批次收回。串列短、局部性好、跨執行緒 free 不搶鎖。
- **mallocng**（musl libc 1.2.1 起的預設配置器）：設計目標是嚴格的 out-of-band metadata（元資料不和使用者資料相鄰，越界寫不會直接蓋到 header）與小記憶體占用，刻意犧牲一些極端情境的吞吐量。

### 實測：同一個工作負載換四個配置器

T 個執行緒，每個各自持有 1024 個槽位，隨機選槽 free 舊的、配置新的（16–512 bytes），每執行緒 400 萬次操作：

```c
/* allocbench.c: T 個執行緒各自做大量小型 malloc/free（隨機大小 16..512 bytes） */
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <time.h>

#define OPS   4000000
#define SLOTS 1024                              /* 每個執行緒同時持有最多 1024 塊 */

static void *work(void *arg)
{
    unsigned seed = (unsigned)(size_t)arg;
    void **slot = calloc(SLOTS, sizeof(void *));
    for (long i = 0; i < OPS; i++) {
        unsigned r = rand_r(&seed);
        unsigned k = r % SLOTS;
        free(slot[k]);                           /* free(NULL) 合法 */
        slot[k] = malloc(16 + (r >> 10) % 497);
        ((char *)slot[k])[0] = 1;                /* 碰一下，避免被當作沒用 */
    }
    for (int k = 0; k < SLOTS; k++) free(slot[k]);
    free(slot);
    return NULL;
}

int main(int argc, char **argv)
{
    int T = argc > 1 ? atoi(argv[1]) : 1;
    pthread_t th[64];
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, work, (void *)(size_t)(i + 1));
    for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double s = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
    printf("T=%2d  %.3f s  %.1f Mops/s\n", T, s, (double)T * OPS / s / 1e6);
    return 0;
}
```

用 `LD_PRELOAD=/path/to/libjemalloc.so.2 ./allocbench 8` 這種方式切換配置器，不必重新編譯：

```
glibc     T= 1  0.144 s  27.8 Mops/s
glibc     T= 8  0.217 s  147.8 Mops/s
glibc     T=16  0.243 s  263.3 Mops/s
jemalloc  T= 1  0.076 s  52.9 Mops/s
jemalloc  T= 8  0.146 s  219.3 Mops/s
jemalloc  T=16  0.233 s  274.7 Mops/s
tcmalloc  T= 1  0.044 s  91.2 Mops/s
tcmalloc  T= 8  0.092 s  349.4 Mops/s
tcmalloc  T=16  0.149 s  428.3 Mops/s
mimalloc  T= 1  0.585 s  6.8 Mops/s
mimalloc  T= 8  0.778 s  41.2 Mops/s
mimalloc  T=16  1.074 s  59.6 Mops/s
```

**先別急著下結論**，這張表有三個陷阱，每一個都值得記住：

1. **mimalloc 慢得不合理**。用 `MIMALLOC_VERBOSE=1` 執行，輸出裡有一行 `mimalloc: debug level : 2`——Ubuntu 22.04 的 `libmimalloc2.0` 套件是**開了完整內部斷言的除錯建置**。這不是 mimalloc 的真實效能，要比較請自己用 release 模式編譯。這是「benchmark 量到別的東西」的完美範例。
2. **這是「每個執行緒只碰自己的記憶體」的理想負載**，tcache 類設計最吃香。真實伺服器常有「A 執行緒配置、B 執行緒釋放」的模式（例如生產者/消費者佇列），結果可能完全不同。
3. **只量了速度，沒量記憶體占用**。jemalloc 的賣點之一是長時間執行下的低碎片，這個 4 秒的測試看不出來。

所以這張表**能**支持的結論只有：在這台機器、這個負載下，glibc 的 tcache 已經讓單執行緒小配置很快，而 tcmalloc（gperftools 版）在多執行緒時延展性最好。選配置器是要用**你自己的工作負載**量測的工程決策。

## 對比與取捨

| 配置器 | 多執行緒策略 | 強項 | 弱項 / 注意 |
|---|---|---|---|
| glibc ptmalloc2 | 多 arena + per-thread tcache | 預設、無需部署、相容性最好、有基本安全檢查 | 執行緒多時記憶體膨脹；跨 arena 的碎片 |
| jemalloc | 多 arena + tcache + size class | 低碎片、RSS 穩定、統計與 heap profiling 完整 | 需額外部署；設定項多 |
| tcmalloc（google） | per-CPU cache（rseq） | 高並行吞吐、Google 規模驗證 | per-CPU 模式依賴新版 Linux；舊的 gperftools 版行為不同 |
| mimalloc | per-page free list sharding | 程式碼精簡、局部性好、跨執行緒 free 便宜 | 發行版套件可能是除錯建置 |
| musl mallocng | 全域鎖 + 隔離的元資料 | 小、安全性強化 | 多執行緒吞吐較低 |

## 踩雷集錦

1. **「`free` 之後記憶體就還給作業系統了」**：大部分情況下，chunk 只是回到 bins 或 tcache，留給下一次 `malloc`。只有 mmap 的大區塊與 heap 頂端超過 trim threshold 的部分會真的歸還。
2. **`p = realloc(p, n);`**：`realloc` 失敗回傳 NULL 時，舊區塊**沒有**被釋放，但你剛把唯一的指標蓋成 NULL——記憶體洩漏。正確寫法是先存到暫存變數：`void *t = realloc(p, n); if (!t) { /* 處理錯誤，p 仍有效 */ } else p = t;`。
3. **依賴 `malloc_usable_size` 多寫幾個 byte**：`malloc(25)` 的 usable size 是 40，但寫到第 26 個 byte 在 C 的語意裡就是越界。換了配置器、開了 ASan、或 glibc 改了 chunk 策略，程式就壞了。
4. **以為 glibc 能抓到所有 double free**：fastbin 只檢查 bin 頂端，`free(a); free(b); free(a);` 抓不到；tcache 的 key 檢查也能被刻意繞過。glibc 的檢查是「便宜的保險」，記憶體安全要靠 sanitizer 與程式設計紀律（`free` 後把指標設為 NULL，`free(NULL)` 合法）。
5. **`realloc(p, 0)` 當作 `free`**：glibc 會釋放並回傳 NULL，但其他實作可能回傳一個小區塊；C23 乾脆把它定為 UB。要釋放就呼叫 `free`。
6. **只用一個 benchmark 就換配置器**：見上面 mimalloc 的例子。至少要用真實工作負載量吞吐量、尾端延遲與 RSS 三項。

## 進階：再往深一層

- **glibc 2.42 的 tcache 擴充**：2.42 的 NEWS 記載 tcache 現在也能快取大區塊（以 `glibc.malloc.tcache_max` 調整，上限 4 MiB），且小尺寸路徑更快。Ubuntu 22.04 的 2.35 沒有這些改變——讀網路上的 heap 文章時，先確認它對應哪個 glibc 版本。
- **C23 的 `free_sized`**：呼叫端把大小交回，配置器可以跳過查 header，或用來檢查大小是否一致（N3220 7.24.3.4）。glibc 2.43 加入，jemalloc、tcmalloc 早就有類似的 `sdallocx`/sized delete 介面（C++14 的 sized deallocation 是同一個想法）。
- **slab 與 memory pool**：如果你的程式大量配置同一種大小的物件，專用的 object pool（固定大小、無 header、批次釋放）通常比任何通用配置器都快且省。見 `../c_interview/13-memory-pool.md`，Linux 核心的 SLUB 是工業級範例。
- **heap 利用的研究資源**：想從防禦角度理解這些資料結構怎麼被濫用，shellphish 的 [how2heap](https://github.com/shellphish/how2heap) 依 glibc 版本整理了各種手法的最小 PoC，搭配本章的 chunk 圖讀最有效。請只在自己的環境中編譯執行。

## 動手練習

1. 在 gdb 裡跑 tcache.c，在第二個 `for` 迴圈前停下，用 `x/8gx p[0]-16` 看 chunk header 和 fd/key 欄位。把 `p[2]` 的 fd 手動解碼，確認它指向 `p[1]`。
2. 把 tcache.c 的 `malloc(40)` 改成 `malloc(2000)`（超出 tcache 與 fastbin 範圍），並在最後多配置一塊當作隔板（否則最後一塊會直接併回 top chunk），free 其中一塊之後印出 fd 與 bk——這次是 unsorted bin 的雙向串列，fd/bk 會指向 libc 裡 `main_arena` 內的 bin 表頭（沒有 safe-linking）。注意：如果你用 `printf` 印，`printf` 第一次配置 stdout 緩衝區時會掃描 unsorted bin、把這兩塊歸位到 large bin，本機在 gdb 裡看到的是 `main_arena + 1344`；在 `printf` 之前用 gdb 看，才會是 unsorted bin 的表頭（`main_arena + 96`）。用 `info symbol <位址>` 確認。
3. 用 `GLIBC_TUNABLES=glibc.malloc.tcache_count=0 ./tcache` 關掉 tcache，看 `malloc` 的重用順序怎麼變。
4. 寫一個程式：16 個執行緒各配置 100 MiB 的小物件後全部 free，比較 `MALLOC_ARENA_MAX=1` 和預設值時 `malloc_stats()` 的 system bytes 與執行時間。

## 本章重點整理

- glibc 以 chunk 管理記憶體：使用者指標前 16 bytes 是 prev_size 與 size，size 的低 3 位元是 P/M/A 旗標；空閒時資料區存放串列指標，boundary tag 讓合併不用掃描。
- 空閒 chunk 依大小進 tcache（每執行緒、無鎖）、fastbin、unsorted、small、large bin；越常見的小請求路徑越短，代價是碎片。
- 多執行緒靠多個 arena 分散鎖競爭，代價是記憶體膨脹；大請求走 mmap，門檻會隨程式行為動態上調。
- glibc 有便宜的安全檢查（tcache key、fasttop、safe-linking），但不是完整的記憶體安全。
- jemalloc/tcmalloc/mimalloc 的共同方向是讓操作只碰執行緒或 CPU 私有的資料；選擇要靠真實工作負載的量測。

## 自我檢核

- [ ] 不看本章，能畫出一個已配置 chunk 和一個空閒 chunk 的欄位圖，並說明 `malloc(24)` 為什麼只占 32 bytes 嗎？
- [ ] 如果面試官問「`free` 怎麼知道要釋放多大？」，你能從 chunk header 一路講到 boundary tag 合併嗎？
- [ ] 能解釋為什麼 `free(a); free(a);` 會被抓到，而 `free(a); free(b); free(a);`（填滿 tcache 之後）可能不會嗎？
- [ ] 你的伺服器 RSS 比實際使用量大很多，你會先檢查哪三件事？
- [ ] 能說出 allocbench 那張表裡至少兩個「不能直接下結論」的理由嗎？

## 延伸閱讀

### 論文

- **[Dynamic Storage Allocation: A Survey and Critical Review](https://www.cs.hmc.edu/~oneill/gc-library/Wilson-Alloc-Survey-1995.pdf)** — Paul R. Wilson, Mark S. Johnstone, Michael Neely, David Boles，IWMM（1995）
  - **核心貢獻**：配置器領域的經典綜述，把策略（first-fit、best-fit、segregated fit）和機制分開討論，並指出碎片問題要用真實程式的配置軌跡評估，而非隨機合成負載。
  - **讀哪裡**：第 1–2 節（配置器的任務、內部與外部碎片、deferred coalescing）與第 3 節的分類法（3.4 Sequential Fits、3.6 Segregated Free Lists）；第 4 節的文獻史回顧很長，可以跳讀。
  - **和本章的關聯**：bins、boundary tag、deferred coalescing 都在這篇有系統性的分析；也是練習 B 的理論背景。
- **[Mimalloc: Free List Sharding in Action](https://www.microsoft.com/en-us/research/publication/mimalloc-free-list-sharding-in-action/)** — Daan Leijen, Benjamin Zorn, Leonardo de Moura，APLAS（2019）
  - **核心貢獻**：提出 free list sharding，用很少的程式碼達到和 jemalloc、tcmalloc 同級或更好的效能。
  - **讀哪裡**：第 2 節（設計）與第 4 節（評估）。前提：懂本章的 bins 與 tcache。
- **[A Scalable Concurrent malloc(3) Implementation for FreeBSD](https://people.freebsd.org/~jasone/jemalloc/bsdcan2006/jemalloc.pdf)** — Jason Evans，BSDCan（2006）
  - **核心貢獻**：jemalloc 的原始設計文件，解釋多 arena、size class 與 cache 友善配置的理由。
  - **讀哪裡**：前半部的設計目標與演算法說明（arena 指派、size class、run 的管理）；後半的 benchmark 是 2006 年的硬體，可以略讀。

### 部落格 / 技術文章

- **[MallocInternals](https://sourceware.org/glibc/wiki/MallocInternals)** — glibc wiki
  - **這篇說什麼**：glibc 維護者寫的 malloc 內部導覽，chunk、arena、bins、tcache 都有圖。
  - **讀哪裡**：整篇；比直接讀 malloc.c 容易入門，讀完再對照原始碼。
- **[Safe-Linking – Eliminating a 20 year-old malloc() exploit primitive](https://research.checkpoint.com/2020/safe-linking-eliminating-a-20-year-old-malloc-exploit-primitive/)** — Eyal Itkin（Check Point Research，2020）
  - **這篇說什麼**：safe-linking 提案者本人解釋設計動機、`PROTECT_PTR` 的運作與對各種利用手法的影響。
  - **為什麼值得讀**：本章 tcache 實驗中解碼 fd 的那一行，原理全在這篇。
- **[Why does calloc exist?](https://vorpus.org/blog/why-does-calloc-exist/)** — Nathaniel J. Smith（2016）
  - **這篇說什麼**：`calloc` 的兩個存在理由：溢位檢查與利用核心零頁的效能優勢，含實測。原文直接引用。

### 官方文件 / 原始碼

- **[glibc 2.35 malloc/malloc.c](https://github.com/bminor/glibc/blob/glibc-2.35/malloc/malloc.c)**
  - **讀哪裡**：檔案開頭的長註解（設計概觀）、第 1153 行起的 `malloc_chunk` 與其後的 chunk 說明、`_int_malloc` 與 `_int_free` 兩個函式。本章的巨集數值都出自這個檔案。
- **[google/tcmalloc 設計文件](https://google.github.io/tcmalloc/design.html)**
  - **讀哪裡**：「Overview」與「Per-CPU Mode」兩節，了解 per-CPU cache 與 rseq。

### 書籍

- **《The C Programming Language》2nd ed.** — Kernighan & Ritchie（1988）
  - **讀哪幾章**：8.7 節「Example – A Storage Allocator」，不到兩頁的 first-fit 配置器，是練習 B 的起點。
- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：9.9 節「Dynamic Memory Allocation」，從隱式串列講到分離式串列，附完整的隱式串列實作——練習 B 的直接前導。

配置器內部到處是串列：tcache 的單向串列、bins 的雙向串列、靠指標的指標完成的插入與刪除。下一章我們回到最基本的鏈結串列，看「指標的指標」怎麼讓這些操作變得優雅。

→ [Ch 18 鏈結串列與指標的指標](./18-linked-list-pointer-to-pointer.md)
