# Ch 16 — data alignment、padding 與 _Alignas

> **目標**：搞懂「對齊」從硬體到規格書的完整來龍去脈。學完後你能手算任何 struct 的大小與 padding、知道怎麼重排成員省空間、會用 C11/C23 的 `alignof`/`alignas`/`aligned_alloc`，並且說得出 `packed` 與未對齊存取在 x86 和 RISC 架構上各要付什麼代價。

> **對應原文**：[你所不知道的 C 語言：記憶體管理、對齊及硬體特性篇](https://hackmd.io/@sysprog/c-memory) 的「data alignment」「C11/C23 的對齊設施」「Unaligned memory access」等節。

> **環境**：WSL2 Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G），gcc 11.4、clang 14、glibc 2.35、pahole 1.25。C23 段落用 Docker `gcc:14` 映像（gcc 14.4、glibc 2.41）。RISC-V 組合語言用 clang 14 的 `--target=riscv64-unknown-elf` 交叉產生，只看組語、沒有在 RISC-V 硬體上執行。

## 為什麼需要這個？

```c
struct s1 { char c; int a; };
```

`char` 1 byte、`int` 4 bytes，加起來 5，`sizeof(struct s1)` 卻是 8。多出來的 3 bytes 是編譯器塞的 **padding（填充）**。為什麼要浪費？

歷史上，很多處理器**根本不接受**沒對齊的存取。1980 年代的 RISC 設計（MIPS、SPARC、早期 ARM）為了讓硬體簡單、一個時脈完成一次載入，規定 4-byte 的載入指令只能用 4 的倍數位址；違反就觸發例外，作業系統要嘛把程式殺掉（`SIGBUS`），要嘛用軟體慢慢模擬。x86 從 8086 一路相容下來，硬體自己處理未對齊存取，但歷史上會慢。C 語言要能在這些機器上都跑，所以規格書把「每個型別要放在什麼位址」定為**實作定義**，交給各平台的 ABI 決定；編譯器照著 ABI 自動插 padding，讓你寫的 struct 在每個平台都合法、都快。

今天這個議題沒有消失，反而換了形式：網路封包與檔案格式欄位沒對齊、SIMD 指令要求 16/32/64 bytes 對齊、多執行緒程式要把變數對齊到 cache line 以免互相干擾、無鎖資料結構把對齊騰出來的低位元拿來夾帶旗標。這些都是本章會實際跑過的例子。

## 先建立直覺

想像記憶體匯流排一次搬 4 bytes，而且只能從 4 的倍數位址開始搬（真實硬體的寬度是 8 bytes 或一整條 64-byte cache line，道理相同）：

```
  位址:   0    1    2    3  | 4    5    6    7
         ┌────┬────┬────┬────┼────┬────┬────┬────┐
         │ x0 │ x1 │ x2 │ x3 │    │    │    │    │   int 放在 0：一次搬完
         └────┴────┴────┴────┼────┴────┴────┴────┘
         └──── 第一次搬 ────┘

         ┌────┬────┬────┬────┼────┬────┬────┬────┐
         │    │ y0 │ y1 │ y2 │ y3 │    │    │    │   int 放在 1：
         └────┴────┴────┴────┼────┴────┴────┴────┘
         └──── 第一次搬 ────┘ └──── 第二次搬 ────┘    搬兩次，丟掉不要的 byte，
                                                       再把 y0-y2 和 y3 拼起來
```

**對齊（alignment）**：一個物件的位址是某個 2 的冪次 N 的倍數，就說它「N-byte 對齊」。編譯器確保每個型別的物件都落在它要求的倍數上，CPU 每次存取就只會搬一次。padding 就是為了守住這個規則而留的空位。

這個圖在「匯流排寬度固定」的模型下成立。現代 x86 的實際情況更複雜（下面會實測：在 L1 cache 內，未對齊讀取幾乎不慢），但「跨越邊界要多做一次存取」這個核心仍然適用於 cache line 與 page 的邊界。

## 規格書怎麼說

> 規格書的查法回看 [Ch 4 — 讀規格書](./04-reading-the-standard.md)；物件與型別的定義見 [Ch 7](./07-pointers-by-the-standard.md)。

- **N1570 6.2.8 ¶1**：完整物件型別有**對齊需求（alignment requirement）**，限制該型別物件能放在哪些位址；對齊值是「可以連續放置物件的位址間距（bytes）」，為實作定義。
- **6.2.8 ¶2–4**：小於等於 `_Alignof(max_align_t)` 的叫**基本對齊（fundamental alignment）**，所有實作都要支援；更大的叫**擴充對齊（extended alignment）**，是否支援由實作定義。每個合法的對齊值都是 2 的非負整數次方。
- **6.7.2.1 ¶14**：struct 的每個非 bit-field 成員以「適合其型別的、實作定義的方式」對齊。
- **6.7.2.1 ¶15**：成員位址依宣告順序遞增；struct 內**可以有**未命名的 padding，**但開頭不行**（所以指向 struct 的指標轉型後就指向第一個成員）。
- **6.7.2.1 ¶17**：struct 或 union 的**結尾可以有** padding。
- **6.3.2.3 ¶7**：把物件指標轉成另一種物件指標型別時，如果結果沒有正確對齊，**行為未定義**——注意，光是「轉型」就已經是 UB，不必等到解參考。

「實作定義」落到哪裡？gcc 文件寫得很直接：struct 成員的對齊 **Determined by ABI**。x86-64 System V ABI 規定的基本型別對齊，用程式驗證：

```
alignof: char=1 short=2 int=4 long=8 double=8 long double=16 void*=8 max_align_t=16
```

規則很整齊：**基本型別的對齊等於它的大小**（`long double` 在 x86-64 是 80-bit 擴充精度，存成 16 bytes、對齊 16）。這張表是 ABI 的一部分，換到 32 位元 x86（i386 SysV ABI）時 `double` 和 `long long` 在 struct 裡只對齊到 4——同一份 struct 宣告，在兩個平台上大小不同，這是跨平台二進位格式出錯的經典來源。

## padding 怎麼算

struct 的排法用三條規則就能完全推出來：

1. 每個成員放在「≥ 目前 offset、且是自己對齊值倍數」的最小 offset。
2. 整個 struct 的對齊值 = 成員中最大的對齊值。
3. `sizeof(struct)` 向上補到 struct 對齊值的倍數（尾端 padding）。

第 3 條的理由：陣列元素緊密相鄰，`arr[1]` 的位址是 `arr[0]` 加 `sizeof`。如果 `sizeof` 不是對齊值的倍數，`arr[1]` 就會沒對齊。

```c
/* pad.c: struct padding 的計算、重排與 offsetof */
#include <stdio.h>
#include <stddef.h>
#include <stdalign.h>

struct bad  { char c; double d; int i; short s; char c2; };
struct good { double d; int i; short s; char c; char c2; };
struct tail { int i; char c; };                /* 尾端 padding */
struct s1   { char c; int a; };

#define SHOW(T, m) printf("  %-6s offset=%2zu size=%zu\n", #m, offsetof(T, m), sizeof(((T *)0)->m))

int main(void)
{
    printf("struct bad : sizeof=%zu alignof=%zu\n", sizeof(struct bad), alignof(struct bad));
    SHOW(struct bad, c); SHOW(struct bad, d); SHOW(struct bad, i);
    SHOW(struct bad, s); SHOW(struct bad, c2);
    printf("struct good: sizeof=%zu alignof=%zu\n", sizeof(struct good), alignof(struct good));
    SHOW(struct good, d); SHOW(struct good, i); SHOW(struct good, s);
    SHOW(struct good, c); SHOW(struct good, c2);
    printf("struct tail: sizeof=%zu (int 4 + char 1 + 3 bytes tail padding)\n", sizeof(struct tail));
    struct tail arr[2];
    printf("  &arr[0]=%p &arr[1]=%p\n", (void *)&arr[0], (void *)&arr[1]);
    printf("struct s1  : sizeof=%zu\n", sizeof(struct s1));
    printf("alignof: char=%zu short=%zu int=%zu long=%zu double=%zu long double=%zu void*=%zu max_align_t=%zu\n",
           alignof(char), alignof(short), alignof(int), alignof(long), alignof(double),
           alignof(long double), alignof(void *), alignof(max_align_t));
    return 0;
}
```

（`sizeof(((T *)0)->m)` 不會真的解參考空指標：`sizeof` 的運算元不被求值，N1570 6.5.3.4 ¶2。）

```
struct bad : sizeof=24 alignof=8
  c      offset= 0 size=1
  d      offset= 8 size=8
  i      offset=16 size=4
  s      offset=20 size=2
  c2     offset=22 size=1
struct good: sizeof=16 alignof=8
  d      offset= 0 size=8
  i      offset= 8 size=4
  s      offset=12 size=2
  c      offset=14 size=1
  c2     offset=15 size=1
struct tail: sizeof=8 (int 4 + char 1 + 3 bytes tail padding)
  &arr[0]=0x7ffea8016f00 &arr[1]=0x7ffea8016f08
struct s1  : sizeof=8
```

用三條規則手算 `struct bad`：`c` 在 0；`d` 要 8 的倍數，跳到 8（中間 7 bytes padding）；`i` 在 16；`s` 在 20；`c2` 在 22；成員結束在 23，struct 對齊 8，補到 24。同樣五個成員，**依對齊值由大到小排**的 `struct good` 只要 16 bytes，一個 padding 都沒有。一百萬個元素的陣列，差 8 MB。

畫成記憶體圖：

```
struct bad (24 bytes)
 0       8               16      20  22 23 24
 ┌─┬───────┬───────────────┬───────┬───┬─┬─┐
 │c│ pad 7 │       d       │   i   │ s │c2│p│
 └─┴───────┴───────────────┴───────┴───┴─┴─┘

struct good (16 bytes)
 0               8       12  14 15 16
 ┌───────────────┬───────┬───┬─┬─┐
 │       d       │   i   │ s │c│c2│
 └───────────────┴───────┴───┴─┴─┘
```

### 讓工具幫你找洞

不必每次手算。gcc 的 `-Wpadded` 會在每個插 padding 的地方警告：

```
pad.c:6:30: warning: padding struct to align ‘d’ [-Wpadded]
    6 | struct bad  { char c; double d; int i; short s; char c2; };
      |                              ^
pad.c:6:8: warning: padding struct size to alignment boundary [-Wpadded]
```

更好用的是 **pahole**（dwarves 套件，Linux 核心開發者 Arnaldo Carvalho de Melo 寫的），它讀 DWARF 除錯資訊畫出布局，連 cache line 都幫你算：

```
$ gcc -g -c pad.c -o pad.o && pahole -C bad pad.o
struct bad {
	char                       c;                    /*     0     1 */

	/* XXX 7 bytes hole, try to pack */

	double                     d;                    /*     8     8 */
	int                        i;                    /*    16     4 */
	short int                  s;                    /*    20     2 */
	char                       c2;                   /*    22     1 */

	/* size: 24, cachelines: 1, members: 5 */
	/* sum members: 16, holes: 1, sum holes: 7 */
	/* padding: 1 */
	/* last cacheline: 24 bytes */
};
```

Linux 核心開發者常用 pahole 檢查 `struct sk_buff`、`struct task_struct` 這種一個系統裡有成千上萬份的結構，省下的每個 byte 都會乘上很大的倍數。

### 重排之前先想清楚

「依對齊值由大到小排」是省空間的機械規則，但有三種情況**不該**照做：

- **struct 是 ABI 或檔案/網路格式的一部分**：改順序等於改格式，舊的二進位、舊的檔案全部讀錯。
- **熱欄位要擺在一起**：一起被頻繁存取的成員放在同一條 cache line，比省 4 bytes 重要。Linux 核心會刻意把 `struct` 的熱欄位排在前面，甚至用 `____cacheline_aligned` 切開。
- **第一個成員有特殊意義**：6.7.2.1 ¶15 保證 struct 指標可以轉成第一個成員的指標。C 的「繼承」手法靠這點（[Ch 29](./29-oop-in-c.md)）。

## offsetof

`offsetof(type, member)` 定義在 `<stddef.h>`（N1570 7.19），回傳成員相對於 struct 開頭的 byte 偏移量。你可能看過它的「經典實作」：

```c
#define my_offsetof(T, m) ((size_t)&((T *)0)->m)
```

在空指標上做成員存取再取位址，嚴格說是未定義行為；它在主流編譯器上能動，是因為編譯器把它摺成常數。現代 gcc/clang 的 `<stddef.h>` 用的是 `__builtin_offsetof`，請直接用標準的 `offsetof`。Linux 核心的 `container_of`（從成員指標倒推出整個 struct 的位址）就是建立在 `offsetof` 之上，[Ch 19](./19-linux-list-head.md) 會詳細拆解。

## C11 的對齊設施

C11 以前，指定對齊只能用編譯器擴充（gcc 的 `__attribute__((aligned(N)))`、MSVC 的 `__declspec(align(N))`）。C11 把它標準化：

| 設施 | 標頭 | 條款（N1570） | 用途 |
|---|---|---|---|
| `_Alignof(type)` / `alignof` | `<stdalign.h>` 提供 `alignof` 巨集 | 6.5.3.4、7.15 | 查詢型別的對齊值 |
| `_Alignas(N)` / `alignas` | 同上 | 6.7.5 | 要求物件或成員更嚴格的對齊 |
| `max_align_t` | `<stddef.h>` | 7.19 | 對齊最嚴格的基本型別；`malloc` 必須滿足它 |
| `aligned_alloc(align, size)` | `<stdlib.h>` | 7.22.3.1 | 動態配置指定對齊的記憶體 |

POSIX 另有 `posix_memalign`，比 C11 早，到處都有。

```c
/* alignfac.c: C11 對齊設施與 glibc 的實際行為 */
#define _POSIX_C_SOURCE 200112L
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <stdalign.h>
#include <errno.h>
#include <string.h>

struct counter {
    alignas(64) long hits;          /* 每個成員獨占一條 cache line */
    alignas(64) long misses;
};

static alignas(4096) char page_buf[4096];

static unsigned lowbits(const void *p) { return (unsigned)((uintptr_t)p & 0xfff); }

int main(void)
{
    printf("sizeof(struct counter)=%zu alignof=%zu offsetof(misses)=%zu\n",
           sizeof(struct counter), alignof(struct counter), offsetof(struct counter, misses));
    printf("page_buf low 12 bits = %#x\n", lowbits(page_buf));

    void *a = aligned_alloc(64, 100);          /* C11 原文要求 size 為 alignment 倍數；DR 460 放寬 */
    printf("aligned_alloc(64, 100)   = %p (mod 64 = %lu)\n", a, (unsigned long)((uintptr_t)a % 64));
    void *b = aligned_alloc(24, 100);          /* 24 不是 2 的冪 */
    printf("aligned_alloc(24, 100)   = %p  errno=%s\n", b, b ? "-" : strerror(errno));
    void *c = NULL;
    int rc = posix_memalign(&c, 24, 100);      /* POSIX 要求 2 的冪且為 sizeof(void*) 倍數 */
    printf("posix_memalign(.., 24,..) = %d (%s)\n", rc, strerror(rc));
    rc = posix_memalign(&c, 4096, 100);
    printf("posix_memalign(.., 4096,..) -> %p rc=%d\n", c, rc);

    for (int i = 0; i < 4; i++) {
        void *m = malloc(1 + i * 7);
        printf("malloc(%2d) = %p  mod 16 = %lu\n", 1 + i * 7, m, (unsigned long)((uintptr_t)m % 16));
    }
    free(a); free(b); free(c);
    return 0;
}
```

`gcc -std=c11 -Wall -Wextra alignfac.c -o alignfac && ./alignfac`：

```
sizeof(struct counter)=128 alignof=64 offsetof(misses)=64
page_buf low 12 bits = 0
aligned_alloc(64, 100)   = 0x63265438b300 (mod 64 = 0)
aligned_alloc(24, 100)   = 0x63265438b3c0  errno=-
posix_memalign(.., 24,..) = 22 (Invalid argument)
posix_memalign(.., 4096,..) -> 0x63265438c000 rc=0
malloc( 1) = 0x63265438b450  mod 16 = 0
malloc( 8) = 0x63265438b470  mod 16 = 0
malloc(15) = 0x63265438b490  mod 16 = 0
malloc(22) = 0x63265438b4b0  mod 16 = 0
```

幾個要點：

- **`malloc` 一律 16-byte 對齊**：連 `malloc(1)` 也是。N1570 7.22.3 ¶1 要求回傳的指標「適合指派給任何具基本對齊需求的物件指標」，也就是要滿足 `alignof(max_align_t)` = 16。glibc 內部的 `MALLOC_ALIGNMENT` 定義為 `2 * SIZE_SZ` 與 `__alignof__(long double)` 取大者，在 x86-64 是 16（原文也在 aarch64 驗證過同樣是 16）。
- **`alignas` 讓 struct 變大**：兩個 `long` 本來 16 bytes，各自對齊到 64 之後變成 128 bytes。這是刻意的，用途見下面的 false sharing 實驗。
- **`aligned_alloc(64, 100)` 成功**：C11 原文（7.22.3.1 ¶2）要求 size 必須是 alignment 的整數倍，100 不是 64 的倍數，照原文是 UB。缺陷報告 **DR 460** 把這條限制移除了，C17/C23 跟進，glibc 也一直接受。
- **`aligned_alloc(24, 100)` 在 glibc 2.35 成功了**，但 24 不是合法的對齊值。這是 glibc 的歷史行為：2.38 以前會把非 2 的冪次的對齊值**悄悄向上取到下一個 2 的冪**（24 → 32）。glibc 2.38 起改為遵守標準、回傳 NULL 並設 `errno = EINVAL`。用 Docker 的 glibc 2.41 驗證：

```
glibc 2.41: aligned_alloc(24, 100) = (nil) errno=Invalid argument
```

  同一行程式，換個 glibc 版本結果就不同——這正是「依賴實作的寬容」的風險。`posix_memalign` 則一直都會拒絕（回傳 `EINVAL`，注意它是**回傳**錯誤碼、不設 `errno`）。

### 規格書不准你「放鬆」對齊

N1570 6.7.5 ¶4 是一條限制式（constraint）：一個宣告裡的對齊指定，不得比該型別原本的對齊還寬鬆。違反限制式，編譯器必須給診斷：

```c
#include <stdalign.h>
alignas(1) int x;          /* 比 int 自然的 4 還寬鬆：違反 6.7.5p4 */
int main(void) { return x; }
```

```
lessstrict.c:2:16: error: ‘_Alignas’ specifiers cannot reduce alignment of ‘x’
    2 | alignas(1) int x;          /* 比 int 自然的 4 還寬鬆：違反 6.7.5p4 */
      |                ^
```

想要「放鬆」對齊，標準 C 沒有辦法，只能靠 `packed` 這類擴充（下面會看到代價）。

## C23：對齊從巨集變成關鍵字

C23 做了三件事：

1. **`alignas`、`alignof` 升格為關鍵字**（N3220 6.7.6 與 6.5.4.4），不必再 `#include <stdalign.h>`。
2. **新增 `memalignment(p)`**（N3220 7.24.9.1）：回傳指標位址實際滿足的最大對齊值（一個 2 的冪），空指標回傳 0。原文提到的 [N2974 Queryable pointer alignment](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2974.pdf) 提案就是它的來源。
3. **新增 `free_sized(p, size)` 與 `free_aligned_sized(p, align, size)`**（N3220 7.24.3.4、7.24.3.5）：呼叫端把當初的大小交回去，配置器可以省掉查表、也能拿來檢查呼叫端是否傳錯。規格明訂 `aligned_alloc` 的結果不能交給 `free_sized`，`malloc` 的結果不能交給 `free_aligned_sized`，大小不符就是 UB；但實作也可以忽略大小、直接呼叫 `free`。

關鍵字的部分用 gcc 14 實測：

```c
/* c23kw.c: C23 的 alignas/alignof 已是關鍵字 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>

struct vec { alignas(32) float x[8]; };      /* 沒有 #include <stdalign.h> */

int main(void)
{
    printf("__STDC_VERSION__ = %ldL\n", (long)__STDC_VERSION__);
    printf("alignof(struct vec) = %zu, sizeof = %zu\n", alignof(struct vec), sizeof(struct vec));
    errno = 0;
    void *b = aligned_alloc(24, 100);
    printf("glibc %d.%d: aligned_alloc(24, 100) = %p errno=%s\n",
           __GLIBC__, __GLIBC_MINOR__, b, errno ? strerror(errno) : "0");
    free(b);
    return 0;
}
```

```
$ docker run --rm -v "$PWD":/w -w /w gcc:14 sh -c 'gcc -std=c23 -Wall -Wextra c23kw.c -o /tmp/a && /tmp/a'
__STDC_VERSION__ = 202000L
alignof(struct vec) = 32, sizeof = 32
glibc 2.41: aligned_alloc(24, 100) = (nil) errno=Invalid argument
```

同一份檔案用 `-std=c11` 編，`alignas` 就是個未知的識別字：

```
c23kw.c:8:14: error: expected specifier-qualifier-list before 'alignas'
```

注意 gcc 14 在 `-std=c23` 下的 `__STDC_VERSION__` 是 `202000L`，不是正式的 `202311L`（gcc 15 才改）。用版本巨集判斷 C23 時要寫 `>= 202000L` 之類的寬鬆條件。

`memalignment`、`free_sized`、`free_aligned_sized` 三個函式在 gcc:14 映像的 glibc 2.41 上編譯失敗（`implicit declaration of function 'memalignment'`）：這是**函式庫**的事，和編譯器版本無關。glibc 的 NEWS 檔記載這三個函式在 **glibc 2.43** 才加入。**這三個函式的行為本段未實測，為依 N3220 的理論預期**（需 glibc 2.43 以上；可用 `#ifdef __STDC_VERSION_STDLIB_H__` 搭配實際編譯測試確認你的環境）。

## 實戰一：用 alignas(64) 消除 false sharing

[Ch 15](./15-memory-hierarchy.md) 講過 cache 以 64-byte 的 line 為單位。多核心系統用 **cache coherence 協定**（例如 MESI）保證每顆核心看到一致的資料：一顆核心要寫某條 line，必須先讓其他核心手上的那條 line 失效。如果兩個執行緒各寫**各自的**變數、但兩個變數剛好在**同一條** line，line 會在兩顆核心之間來回失效、來回搬運——邏輯上沒有共享，硬體上卻在共享。這叫 **false sharing**。

```c
/* falseshare.c: 兩個執行緒各自遞增自己的計數器；計數器擠在同一條 cache line 時會互相干擾 */
#include <stdio.h>
#include <pthread.h>
#include <stdalign.h>
#include <time.h>

#define ITERS 200000000L

struct packed_ctr { volatile long a; volatile long b; };                 /* a、b 相鄰：同一條 line */
struct padded_ctr { alignas(64) volatile long a; alignas(64) volatile long b; };  /* 各占一條 line */

static struct packed_ctr pc;
static struct padded_ctr dc;

static void *inc(void *arg) { volatile long *p = arg; for (long i = 0; i < ITERS; i++) (*p)++; return NULL; }

static double run(volatile long *x, volatile long *y)
{
    struct timespec t0, t1;
    pthread_t th1, th2;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    pthread_create(&th1, NULL, inc, (void *)x);
    pthread_create(&th2, NULL, inc, (void *)y);
    pthread_join(th1, NULL);
    pthread_join(th2, NULL);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    return (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
}

int main(void)
{
    printf("same line  (&a=%p &b=%p): %.3f s\n", (void *)&pc.a, (void *)&pc.b, run(&pc.a, &pc.b));
    printf("padded     (&a=%p &b=%p): %.3f s\n", (void *)&dc.a, (void *)&dc.b, run(&dc.a, &dc.b));
    return 0;
}
```

`gcc -O2 -pthread falseshare.c -o fs && ./fs`，連跑兩次：

```
same line  (&a=0x5868578d5100 &b=0x5868578d5108): 1.924 s
padded     (&a=0x5868578d5080 &b=0x5868578d50c0): 0.054 s
same line  (&a=0x64b323dd4100 &b=0x64b323dd4108): 2.243 s
padded     (&a=0x64b323dd4080 &b=0x64b323dd40c0): 0.062 s
```

位址差 8（同一條 line）和差 64（不同 line），速度差 **35 倍以上**。這裡用 `volatile` 是為了強迫每次迭代都真的讀寫記憶體（`gcc -S` 可確認內層迴圈是 `movq (%rdi),%rax; addq $1,%rax; movq %rax,(%rdi)`），**不是**多執行緒同步的正確工具——真正的共享計數器要用 C11 atomics（[Ch 20](./20-lock-free-linked-list.md)）。padded 版本快得驚人（每次迭代不到 0.3 ns），是因為 Zen 3 能把「存了馬上又讀同一位址」的相依鏈在管線內部轉送；這個數字在其他 CPU 上會不同，但「同一條 line 會慢一個數量級以上」的結論是普遍的。

Linux 核心的 `____cacheline_aligned_in_smp` 巨集、C++17 的 `std::hardware_destructive_interference_size` 都是為了這件事存在。

## 實戰二：packed 的代價

有時候你**需要**沒有 padding 的布局，例如對應一個網路協定的標頭。gcc 提供 `__attribute__((packed))` 和 `#pragma pack(1)`：

```c
/* packed.c: packed struct 的大小、成員位址，以及取成員位址的陷阱 */
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>

struct __attribute__((packed)) hdr {
    uint8_t  type;
    uint32_t len;          /* offset 1，未對齊 */
    uint16_t port;
};

struct normal {
    uint8_t  type;
    uint32_t len;
    uint16_t port;
};

uint32_t get_len(struct hdr *h)       { return h->len; }      /* 編譯器知道未對齊 */
uint32_t read_u32(const uint32_t *p)  { return *p; }          /* 編譯器假設已對齊 */

int main(void)
{
    struct hdr h = { 1, 0x11223344, 80 };
    printf("sizeof(hdr)=%zu offsetof(len)=%zu | sizeof(normal)=%zu offsetof(len)=%zu\n",
           sizeof(struct hdr), offsetof(struct hdr, len),
           sizeof(struct normal), offsetof(struct normal, len));
    printf("get_len=%#x\n", get_len(&h));
    printf("read_u32=%#x\n", read_u32(&h.len));   /* 把未對齊的位址交給一般指標 */
    return 0;
}
```

```
$ gcc -Wall -Wextra -g packed.c -o packed && ./packed
packed.c: In function ‘main’:
packed.c:28:39: warning: taking address of packed member of ‘struct hdr’ may result in an unaligned pointer value [-Waddress-of-packed-member]
   28 |     printf("read_u32=%#x\n", read_u32(&h.len));   /* 把未對齊的位址交給一般指標 */
      |                                       ^~~~~~
sizeof(hdr)=7 offsetof(len)=1 | sizeof(normal)=12 offsetof(len)=4
get_len=0x11223344
read_u32=0x11223344
```

7 bytes 對 12 bytes，packed 確實省空間；在 x86 上兩種讀法也都「跑得出正確答案」。但 gcc 的警告點出了關鍵：`&h.len` 的型別是 `uint32_t *`，而 `uint32_t *` 這個型別**承諾**指向的位址是 4 的倍數。把未對齊位址塞進去，就是 6.3.2.3 ¶7 的 UB。UBSan 會在執行期抓到：

```
$ gcc -O2 -fsanitize=alignment packed.c -o packed_ub && ./packed_ub
packed.c:19:48: runtime error: load of misaligned address 0x7fff640cbad2 for type 'const uint32_t', which requires 4 byte alignment
0x7fff640cbad2: note: pointer points here
 00 00  8e 01 44 33 22 11 50 00  00 11 95 22 57 11 23 8c  00 00 00 00 00 00 00 00  90 9d 42 bb 4b 70
              ^
```

（可以在傾印裡看到 `01 44 33 22 11 50 00`：`type=1`、little-endian 的 `0x11223344`、`port=80`，中間沒有 padding。）

`get_len` 和 `read_u32` 的差別要看組語才明白。在 x86-64 上兩者一樣是一條 `movl`，因為 x86 允許未對齊載入：

```
get_packed:                     get_normal:
	movl	1(%rdi), %eax       	movl	4(%rdi), %eax
	ret                         	ret
```

換成不支援（或不保證支援）未對齊存取的 RISC-V（`clang --target=riscv64-unknown-elf -march=rv64gc -O2 -S`）：

```
get_packed:                     get_normal:
	lbu	a1, 2(a0)                	lw	a0, 4(a0)
	lbu	a2, 1(a0)                	ret
	lb	a3, 4(a0)
	lbu	a0, 3(a0)
	slli	a1, a1, 8
	or	a1, a1, a2
	slli	a2, a3, 8
	or	a0, a0, a2
	slli	a0, a0, 16
	or	a0, a0, a1
	ret
```

編譯器**知道** packed 成員可能沒對齊，所以拆成 4 次單 byte 載入再拼起來——11 條指令對 2 條。這就是 packed 的真正代價：**不是在 x86 上，而是在嚴格對齊的架構上**。更危險的是 `read_u32` 這種情況：函式拿到的是普通 `uint32_t *`，編譯器照常產生 `lw`，在不支援未對齊存取的硬體上就是 `SIGBUS`，或由核心逐條模擬（每次存取慢上百倍）。

## 未對齊存取：硬體到底怎麼處理？

### x86 其實可以讓它當掉

x86 的 EFLAGS 暫存器第 18 位元是 **AC（Alignment Check）**。當作業系統開啟 CR0.AM、且程式在使用者態把 AC 設成 1，未對齊存取就會觸發 alignment check 例外，Linux 把它轉成 `SIGBUS`。我們可以在自己的行程裡打開它：

```c
/* actrap.c: 打開 x86 EFLAGS.AC (bit 18)，讓使用者態的未對齊存取變成 SIGBUS */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>

int main(void)
{
    char buf[16] __attribute__((aligned(16)));
    memset(buf, 0x41, sizeof buf);
    volatile uint32_t *p = (volatile uint32_t *)(buf + 1);   /* 刻意未對齊 */

    printf("AC=0: *(uint32_t *)(buf+1) = %#x\n", *p);
    fflush(stdout);
    __asm__ volatile("pushf\n\torl $0x40000, (%%rsp)\n\tpopf" ::: "memory", "cc");
    write(1, "AC=1: about to read buf+1\n", 26);   /* 不用 printf：glibc 內部也可能有未對齊存取 */
    uint32_t v = *p;
    __asm__ volatile("pushf\n\tandl $~0x40000, (%%rsp)\n\tpopf" ::: "memory", "cc");
    printf("value=%#x\n", v);
    return 0;
}
```

`0x40000` 就是 1 << 18，AC 位元。執行：

```
AC=0: *(uint32_t *)(buf+1) = 0x41414141
AC=1: about to read buf+1
bash: line 1: 11033 Bus error               (core dumped) ./actrap
```

一個小插曲：第一版程式在 AC=1 之後用 `printf` 印訊息，結果**還沒走到我們的讀取就先 Bus error 了**——glibc 的 `printf`/`memcpy` 等最佳化過的函式內部本來就會做未對齊存取（在 x86 上這是合法且快的技巧）。所以 AC 位元在實務上幾乎沒人用：整個 x86 軟體生態都假設未對齊存取不會出錯。這個實驗只是讓你親眼看到「硬體其實有能力拒絕」。

### 那 x86 上未對齊到底慢不慢？

```c
/* unalbench.c 的核心迴圈：以不同起始偏移量讀 8 KiB（裝得進 L1）的 uint64_t */
for (int r = 0; r < REPS; r++)
    for (size_t i = 0; i + 8 <= BYTES - 64; i += 8) {
        uint64_t v;
        memcpy(&v, base + off + i, 8);   /* 合法的未對齊讀取寫法 */
        s += v;
    }
```

```
offset  0: 0.273 ns/load
offset  1: 0.276 ns/load
offset  4: 0.272 ns/load
offset  8: 0.268 ns/load
offset 60: 0.275 ns/load
```

在 Zen 3、資料都在 L1 的條件下，**看不出差別**——即使 offset 1 時每 8 次載入就有一次跨越 cache line 邊界。現代 x86 核心的 load 單元能在一次存取中處理跨 line 的情況，代價被管線藏住了。這和 Daniel Lemire 2012 年的實驗結論一致。但這不是普遍真理：

- **跨 page 邊界**的未對齊存取要做兩次 TLB 查詢，成本明顯較高；
- **atomic 操作**跨 cache line（**split lock**）會鎖住整個記憶體匯流排，拖累系統所有核心，新版 Linux 甚至能偵測並懲罰這種行為（`split_lock_detect`）；
- 舊 CPU、ARM 的某些實作、以及 SIMD 的 aligned 指令（如 `movaps` 碰到未對齊位址直接 `SIGSEGV`）都不同。

### 正確的未對齊讀取寫法：memcpy

原文給了一個 `unaligned_get32`，用對齊的 32-bit 讀取加位移來組出未對齊的值。它的註解寫「假設硬體架構為 32-bits」——這個假設很關鍵，把它原封不動放到 64 位元上跑：

```c
uint32_t v = *(uint32_t *) (csrc & 0xfffffffc);         /* 原文：32 位元遮罩 */
```

```
p = 0x55bcd45af011, mask result = 0xd45af010
Segmentation fault (core dumped)
```

`0xfffffffc` 是 32 位元常數，和 64 位元的 `uintptr_t` 做 `&` 時被零擴充，**把位址的高 32 位元清掉了**，變成一個不存在的位址。改成 `csrc & ~(uintptr_t)0x3` 就能跑（輸出 `0x55443322`，即從 byte 1 開始的 4 個 byte）。這是 [Ch 22](./22-integer-overflow.md) 會深入談的「常數寬度」陷阱的一個實例。

即使修好了，這種寫法還有別的問題：它用 `uint32_t *` 讀 `char` 陣列，牽涉 strict aliasing（[Ch 34](./34-undefined-behavior.md)），而且比需要的慢。**現代 C 的標準答案是 `memcpy`**：

```c
#include <stdint.h>
#include <string.h>
uint32_t load32(const void *p) { uint32_t v; memcpy(&v, p, sizeof v); return v; }
```

`memcpy` 對任何位址都合法，而且編譯器懂它。x86-64 `gcc -O2`：

```
load32:
	endbr64
	movl	(%rdi), %eax
	ret
```

一條指令，沒有函式呼叫。RISC-V 上則自動變成 4 次 byte 載入（和上面 `get_packed` 一樣）。**同一份原始碼，在允許未對齊的平台上最快、在嚴格對齊的平台上正確**——這就是為什麼 Linux 核心的 `get_unaligned()`、各種序列化程式庫內部都是這種寫法。

## 延伸：對齊騰出的低位元

既然 8-byte 對齊的物件位址最低 3 位元恆為 0，這 3 個位元就能拿來存東西，用前再遮掉。這叫 **pointer tagging**。原文的 concurrent-ll 案例把節點指標的最低位元設為 1，代表「此節點已被邏輯刪除」；glibc 的 chunk header 把 size 欄位的低 3 位元當旗標（[Ch 17](./17-malloc-internals.md)）。lock-free 的完整用法在 [Ch 20](./20-lock-free-linked-list.md)。要記得這是依賴實作對齊保證的技巧，ISO C 沒有明文授權把指標當整數做位元運算再轉回來。

## 對比與取捨

| 做法 | 空間 | x86 速度 | 嚴格對齊架構 | 可攜性 | 適用 |
|---|---|---|---|---|---|
| 預設布局（編譯器 padding） | 有浪費 | 最快 | 最快 | 標準 C | 一般資料結構 |
| 手動重排成員 | 省 | 最快 | 最快 | 標準 C | 大量實例的結構 |
| `alignas(64)` | 更浪費 | 多執行緒時大幅改善 | 同左 | C11 | 每執行緒計數器、鎖 |
| `packed` / `#pragma pack` | 最省 | 通常無差 | 拆成 byte 存取，慢數倍 | 編譯器擴充 | 對應外部二進位格式 |
| `memcpy` 讀寫欄位 | 依格式 | 等同直接存取 | 正確 | 標準 C | 解析網路/檔案格式的首選 |

## 踩雷集錦

1. **「sizeof struct = 成員 sizeof 加總」**：錯，要加上內部與尾端 padding。結構寫進檔案或送上網路之前，先用 `pahole` 或 `offsetof` 確認布局，並記得 padding 的內容是未指定的（可能洩漏 stack 上的舊資料——Linux 核心有不少資訊洩漏 CVE 是 struct padding 沒清零就 `copy_to_user`）。
2. **「x86 允許未對齊，所以沒關係」**：硬體允許，C 語言不允許。把未對齊位址轉成 `int *` 已經是 UB，編譯器可以據此最佳化（例如假設低位元為 0），而且同一份程式到 ARM/RISC-V 上可能 `SIGBUS`。要嘛用 `memcpy`，要嘛用 `-fsanitize=alignment` 檢查。
3. **對 packed 成員取位址**：`&pkt->len` 得到的是一個「看起來正常、其實沒對齊」的 `uint32_t *`。gcc 9 起有 `-Waddress-of-packed-member` 預設警告，不要忽略它。
4. **`aligned_alloc` 的參數限制隨版本變**：非 2 的冪次的對齊值，glibc 2.38 以前悄悄放行、2.38 起回傳 NULL。永遠檢查回傳值，對齊值用 2 的冪次。
5. **以為 `alignas` 能讓物件「更緊」**：`alignas` 只能讓對齊更嚴格（6.7.5 ¶4），要放鬆只能靠 `packed` 這類擴充。

## 進階：再往深一層

- **stack 對齊**：x86-64 SysV ABI 要求 `call` 指令之前 `%rsp` 是 16 的倍數。手寫組語或在 signal handler、`makecontext` 裡自己配 stack 時弄錯，會在 SSE 指令（如 `movaps`）上 segfault。[Ch 11](./11-process-layout-and-abi.md) 有 stack frame 的完整圖。
- **SIMD 對齊**：AVX 的 `vmovaps` 要求 32-byte 對齊，`vmovups` 不要求。現代 CPU 上兩者對已對齊資料速度相同，所以編譯器多半用 `u` 版本；但若你用 intrinsics 寫 `_mm256_load_ps`，傳入的位址就必須對齊，否則當掉。
- **bit-field 的布局**更不可攜：位元配置順序、能否跨越儲存單位都是實作定義（N1570 6.7.2.1 ¶11）。需要精確位元布局時，用整數加位移遮罩自己做（[Ch 23](./23-bitwise-basics.md)）。
- **面試題**：「`struct { char a; int b; char c; }` 和 `struct { char a; char c; int b; }` 各多大？為什麼？在 32 位元 x86 上 `struct { char a; double b; }` 多大？」（12、8；在 i386 SysV ABI 下是 12，因為 struct 裡的 `double` 只對齊到 4。）

## 動手練習

1. 寫出下面三個 struct，先手算 sizeof 與每個成員的 offset，再用 `offsetof` 與 `pahole` 驗證：`struct { short a; char b; int c; char d; }`、`struct { char a; long double b; }`、`struct { char a[3]; struct { int x; char y; } in; char z; }`。
2. 把 falseshare.c 的 padded 版改成 `alignas(32)`，預測結果再實測。再改成在 `a`、`b` 之間塞 `char pad[56]` 而不用 `alignas`，兩種做法有什麼差異？（提示：struct 本身的起始位址是否對齊到 64？）
3. 把 packed.c 的 `get_len` 用 `clang --target=aarch64-linux-gnu -O2 -S` 和 `--target=armv6-none-eabi` 各產生一次組語，比較 ARMv8 與舊 ARM 的處理方式。
4. 實作 `uint32_t load_be32(const void *p)`，用 `memcpy` 加 `__builtin_bswap32` 讀取大端序（network byte order）的 32 位元整數，看 `-O2` 產生的組語是否只有 `movl` + `bswap`。

## 本章重點整理

- 對齊是硬體需求（一次存取不跨邊界）在語言層的投影；規格書定為實作定義，由 ABI 決定，x86-64 上基本型別的對齊等於其大小。
- struct 布局三條規則：成員對齊到自己的倍數、struct 對齊等於最大成員對齊、大小補到對齊倍數；依對齊值由大到小排可消除 padding。
- C11 提供 `alignof`/`alignas`/`max_align_t`/`aligned_alloc`，C23 讓前兩者成為關鍵字並新增 `memalignment`、`free_sized`（glibc 2.43 才有）。
- `packed` 在 x86 幾乎免費，在嚴格對齊架構上變成逐 byte 存取；對 packed 成員取位址是 UB 的溫床。
- 讀未對齊資料的可攜寫法是 `memcpy`，編譯器會把它最佳化成單一指令。

## 自我檢核

- [ ] 不看本章，能說出 struct 布局的三條規則，並解釋「尾端 padding」為什麼非有不可嗎？
- [ ] 如果有人說「我們只跑在 x86 上，未對齊沒差」，你能從 C 標準、編譯器最佳化、atomic split lock 三個角度反駁嗎？
- [ ] 能解釋為什麼 `aligned_alloc(24, 100)` 在兩個 glibc 版本上結果不同嗎？
- [ ] 能從 false sharing 實驗的位址（差 8 對差 64）推論出 cache coherence 在做什麼嗎？
- [ ] 想一個你看過的二進位格式（ELF header、TCP header、BMP 檔頭），你會用 packed struct 還是 `memcpy` 解析它？為什麼？

## 延伸閱讀

### 部落格 / 技術文章

- **[The Lost Art of Structure Packing](http://www.catb.org/esr/structure-packing/)** — Eric S. Raymond
  - **這篇說什麼**：struct 布局的完整實務指南，包含 bit-field、重排策略、可讀性與 cache 局部性的取捨。
  - **讀哪裡**：第 5–9 節（Padding、Structure alignment and padding、Bitfields、Structure reordering）。
  - **為什麼值得讀**：本章「padding 怎麼算」的延伸，例子比本章多，且討論了何時**不該**重排。
- **[Data alignment for speed: myth or reality?](https://lemire.me/blog/2012/05/31/data-alignment-for-speed-myth-or-reality/)** — Daniel Lemire（2012）
  - **這篇說什麼**：在 x86 上實測未對齊存取的成本，結論與本章 unalbench 相同；留言區有大量不同 CPU 的數據與反例。
  - **讀哪裡**：本文加上作者後續文章的連結。
  - **為什麼值得讀**：作者是 simdjson 等高效能程式庫的作者，示範了怎麼用實驗回答效能問題。

### 官方文件 / Spec

- **[Unaligned Memory Accesses](https://docs.kernel.org/core-api/unaligned-memory-access.html)** — Linux kernel 文件（Daniel Drake、Johannes Berg）
  - **讀哪裡**：整篇；特別是「Why unaligned access is bad」與「Avoiding unaligned accesses」，介紹 `get_unaligned()`/`put_unaligned()`。原文的參考資料之一。
- **[System V Application Binary Interface, AMD64 Architecture Processor Supplement](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：3.1.2 節「Data Representation」的型別大小/對齊表，以及「Aggregates and Unions」段落——本章三條 padding 規則的權威來源。
- **ISO/IEC 9899 草案**：N1570 的 6.2.8、6.7.2.1 ¶14–17、6.7.5、7.22.3.1；N3220 的 6.7.6、7.24.3.4–5、7.24.9.1。讀的時候注意 C11 與 C23 對 `aligned_alloc` 參數要求的差異（DR 460）。

### 書籍

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：3.9.3 節「Data Alignment」，有 x86-64 的對齊規則與練習題（Practice Problem 3.44、3.45 是手算 struct 布局的好練習）。

對齊規則決定了 `malloc` 回傳的指標一定是 16 的倍數，也決定了配置器能把 size 欄位的低位元拿來當旗標。下一章我們打開 glibc 的 `malloc`，看它怎麼利用這些性質管理整個 heap。

→ [Ch 17 malloc/free 實作：glibc ptmalloc 與其他配置器](./17-malloc-internals.md)
