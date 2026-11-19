# Ch 33 — 技巧 II：指標手法與效能實測

> **目標**：會用並能說出邊界條件的四種指標手法（低位元藏資料、`ERR_PTR`、索引＋世代的 handle、可攜的未對齊讀取與位元組序），能自己推導「除以常數」的 magic number 並在 gcc 組語中認出它，並養成一個習慣：任何「這樣寫比較快」的說法都先量測、標明機器與編譯選項，再下結論。

> **對應原文**：[你所不知道的 C 語言：技巧篇](https://hackmd.io/@sysprog/c-trick) 的「在指標低位元藏資料」「在指標中傳回錯誤碼：ERR_PTR」「以 handle 取代指標」「以可攜的方式讀取未對齊資料與位元組序」「整數轉字串：一次二位數」「減少分支：一次比較完成範圍檢查」「除以常數」「無分支的飽和與 2 的冪」「以哨兵值省去邊界檢查」「迴圈展開：Duff's device 與現代處理器」「再檢驗常見的效能建議」「刻意晦澀的程式碼」等節

> **環境**：WSL2 Ubuntu 22.04 x86_64，AMD Ryzen 7 5700G（Zen 3，8 核 16 執行緒），Linux 6.18（WSL 核心），gcc 11.4、clang 14、glibc 2.35。**所有效能數字都在這台機器上以 `taskset -c 3` 固定在單一邏輯核量測**，WSL2 是虛擬機，背景負載會造成數個百分點的浮動；請把數字當成「同一次執行中的相對比例」，不要當成絕對值。

## 為什麼需要這個？

上一章的技巧讓程式更好寫；這一章的技巧多半宣稱「省空間」或「更快」。這類宣稱有兩個共同的問題：

1. **依賴實作定義行為**：把旗標塞進指標低位元、把錯誤碼塞進指標值，都假設了「指標轉整數之後，低位元對應位址的低位元」這件 ISO C 沒保證的事。
2. **效能宣稱會過期**：很多流傳至今的 C 效能建議來自 1990 年代的 32 位元 Arm 編譯器指南。今天的 gcc、clang 已經自動做了大部分改寫，手動做反而可能妨礙向量化。Duff's device 在現代編譯器上可以比純迴圈慢十倍以上——本章會實測給你看。

所以這一章的方法論比技巧本身重要：**每個技巧先證明正確（窮舉或大量隨機測試），再看組語，最後量測；三者缺一不可。**

## 先建立直覺

```
  一個 64 位元的指標值，哪些位元其實是「空的」？

   63            48 47                                    3  2  1  0
  ┌────────────────┬──────────────────────────────────────┬──┬──┬──┐
  │ 正規位址的高位  │          真正用來定址的位元          │ 0│ 0│ 0│ ← 8 位元組對齊的物件
  │ (全 0 或全 1)  │                                      │  │  │  │   低 3 位元恆為 0
  └────────────────┴──────────────────────────────────────┴──┴──┴──┘
   ↑ ARM TBI / x86 LAM                                     ↑ rbtree 的顏色、XArray 的
     可以藏 tag                                              「這是整數不是指標」

  定址空間最頂端 [-4095, -1]：核心保證沒有物件 → ERR_PTR 把 -errno 放這裡
```

這張圖就是前半章的地圖：低位元因為**對齊**而空著、高位元因為**正規位址**的規則而冗餘、最頂端一頁因為**作業系統保證不對映**而可以挪用。後半章的效能技巧則有另一張心智圖：

```
   你寫的 C  ──►  編譯器的改寫（除法→乘法、迴圈→向量、if→cmov）  ──►  CPU（亂序、分支預測）
                  ▲                                                   ▲
                  └─ 手動技巧常常「搶先」做這一步，                   └─ 指令數少 ≠ 比較快
                     卻擋住了編譯器做得更好的版本
```

## 指標手法一：在低位元藏資料

物件若以 4 位元組以上對齊，指向它的指標最低兩個位元恆為 0，可以拿來放旗標（對齊規則見 [Ch 16](./16-alignment-and-padding.md)）。Linux 核心的紅黑樹以此把「親代指標」和「顏色」合併成一個欄位，`include/linux/rbtree_types.h`：

```c
struct rb_node {
    unsigned long  __rb_parent_color;
    struct rb_node *rb_right;
    struct rb_node *rb_left;
} __attribute__((aligned(sizeof(long))));
/* The alignment might seem pointless, but allegedly CRIS needs it */
```

取指標用 `& ~3`，寫入用 `(unsigned long)p + color`。註解裡的 CRIS 是 Axis 的嵌入式架構，`long` 只要求 1 位元組對齊，不強制對齊的話低位元就不保證可用。XArray 則反過來用最低位元區分「指標」與「直接存放的小整數」（page cache 的 shadow entry 就是這樣存）。以可攜的 C 寫出兩者：

```c
#include <assert.h>
#include <stdalign.h>
#include <stdint.h>
#include <stdio.h>

struct node {
    uintptr_t parent_color;    /* 親代指標 | 顏色位元 */
    struct node *left, *right;
};
static_assert(alignof(struct node) >= 4, "need two spare low bits");

enum { RED = 0, BLACK = 1 };

static inline struct node *parent(const struct node *n)
{
    return (struct node *) (n->parent_color & ~(uintptr_t) 3);
}
static inline int color(const struct node *n) { return n->parent_color & 1; }
static inline void set_parent_color(struct node *n, struct node *p, int c)
{
    n->parent_color = (uintptr_t) p | (uintptr_t) c;
}

/* XArray 風格：奇數 = 小整數，偶數 = 指標 */
static inline void *mk_value(uintptr_t v) { return (void *) ((v << 1) | 1); }
static inline int is_value(const void *e) { return (uintptr_t) e & 1; }
static inline uintptr_t to_value(const void *e) { return (uintptr_t) e >> 1; }

struct node_fat { struct node_fat *parent, *left, *right; int color; };

int main(void)
{
    struct node root = {0}, kid = {0};
    set_parent_color(&kid, &root, BLACK);
    printf("sizeof(struct node) = %zu, sizeof(struct node_fat) = %zu\n",
           sizeof(struct node), sizeof(struct node_fat));
    printf("&root = %p, kid.parent_color = %#lx\n", (void *) &root,
           (unsigned long) kid.parent_color);
    printf("parent ok: %d, color: %d\n", parent(&kid) == &root, color(&kid));

    void *slots[] = { &root, mk_value(42) };
    for (int i = 0; i < 2; i++)
        if (is_value(slots[i]))
            printf("slot %d: value %lu\n", i, (unsigned long) to_value(slots[i]));
        else
            printf("slot %d: pointer, same as &root: %d\n", i, slots[i] == &root);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra tagptr.c -o tp && ./tp      # 位址每次執行不同
sizeof(struct node) = 24, sizeof(struct node_fat) = 32
&root = 0x7fff44e4c4f0, kid.parent_color = 0x7fff44e4c4f1
parent ok: 1, color: 1
slot 0: pointer, same as &root: 1
slot 1: value 42
```

省下的是 8 位元組：三個指標加一個 `int` 顏色，因對齊填充成 32 位元組；合併後 24 位元組。每個排程實體、每個 hrtimer 都內嵌 `rb_node`，省下的空間乘上物件數量。XArray 的 `xa_mk_value` 左移一位會丟掉最高位元，所以它拒絕負值，可存的整數只有 `BITS_PER_LONG - 1` 位元。

**這不是可攜的 ISO C**。C11 6.3.2.3 §5–§6 規定整數與指標互轉的結果由實作定義；`uintptr_t`（7.20.1.4）只保證 `void *` 轉過去再轉回來相等，**沒有保證整數的低位元對應位址的低位元**。主流平坦定址架構恰好如此，這是額外的假設；`static_assert` 只能驗證物件的對齊，驗證不了指標的整數表示。另一類做法用高位元：Arm64 的 Top Byte Ignore（TBI）讓硬體忽略位址最高 8 位元，Arm MTE 就建在 TBI 上（用位元 59–56 放 tag），自訂標記若覆寫這些位元會干擾 MTE；CHERI 一類 capability 架構則完全不允許任意改寫指標位元。

## 指標手法二：`ERR_PTR` 在指標裡傳回錯誤碼

C 函式只有一個回傳值。回傳指標的函式失敗時通常回 NULL，原因另放 `errno`。Linux 核心把錯誤碼直接編進指標，`include/linux/err.h`：

```c
#define MAX_ERRNO   4095
#define IS_ERR_VALUE(x) unlikely((unsigned long)(void *)(x) >= (unsigned long)-MAX_ERRNO)

static __always_inline void * __must_check ERR_PTR(long error)
{
    return (void *) error;
}
```

`ERR_PTR(-ENOMEM)` 把負的錯誤碼原樣轉成指標；`IS_ERR()` 用**一次無號比較**判斷值是否落在 `[-4095, -1]`——轉成無號之後就是定址空間最頂端的 4095 個位址。這段範圍不會有合法物件：錯誤碼都小於 4096，而核心保證最後一頁永不對映。系統呼叫的回傳值用同一約定，glibc 的 `sysdeps/unix/sysv/linux/sysdep.h` 以 `(unsigned long) val > -4096UL` 判斷系統呼叫失敗。

```c
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ERRNO 4095
#define IS_ERR_VALUE(x) ((unsigned long) (void *) (x) >= (unsigned long) -MAX_ERRNO)

static inline void *ERR_PTR(long error) { return (void *) error; }
static inline long PTR_ERR(const void *ptr) { return (long) ptr; }
static inline bool IS_ERR(const void *ptr) { return IS_ERR_VALUE(ptr); }
static inline bool IS_ERR_OR_NULL(const void *ptr) { return !ptr || IS_ERR_VALUE(ptr); }
static inline int PTR_ERR_OR_ZERO(const void *ptr) { return IS_ERR(ptr) ? (int) PTR_ERR(ptr) : 0; }

struct conf { char name[16]; int port; };

static struct conf *conf_open(const char *name, int port)
{
    if (strlen(name) >= sizeof(((struct conf *) 0)->name))
        return ERR_PTR(-ENAMETOOLONG);
    if (port <= 0 || port > 65535)
        return ERR_PTR(-EINVAL);
    struct conf *c = malloc(sizeof(*c));
    if (!c)
        return ERR_PTR(-ENOMEM);
    strcpy(c->name, name);
    c->port = port;
    return c;
}

int main(int argc, char **argv)
{
    (void) argv;
    const char *names[] = { "web", "a-name-far-too-long", "db" };
    int ports[] = { 80, 8080, 0 };
    for (int i = 0; i < 3; i++) {
        struct conf *c = conf_open(names[i], ports[i]);
        if (argc > 1 ? !c : IS_ERR(c)) {        /* 帶參數時故意用錯的檢查 */
            printf("%-20s -> %p, errno %ld (%s)\n", names[i], (void *) c,
                   -PTR_ERR(c), strerror((int) -PTR_ERR(c)));
            continue;
        }
        printf("%-20s -> ok, port %d\n", c->name, c->port);
        free(c);
    }
    printf("NULL: IS_ERR %d, IS_ERR_OR_NULL %d\n", IS_ERR(NULL), IS_ERR_OR_NULL(NULL));
    printf("lowest error pointer: %p\n", ERR_PTR(-MAX_ERRNO));
    printf("PTR_ERR_OR_ZERO(ERR_PTR(-EINVAL)) = %d\n", PTR_ERR_OR_ZERO(ERR_PTR(-EINVAL)));
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra errptr.c -o ep && ./ep
web                  -> ok, port 80
a-name-far-too-long  -> 0xffffffffffffffdc, errno 36 (File name too long)
db                   -> 0xffffffffffffffea, errno 22 (Invalid argument)
NULL: IS_ERR 0, IS_ERR_OR_NULL 1
lowest error pointer: 0xfffffffffffff001
PTR_ERR_OR_ZERO(ERR_PTR(-EINVAL)) = -22
$ ./ep bad | cat; echo "exit=${PIPESTATUS[0]}"
exit=139
```

`./ep bad` 用 `if (!c)` 取代 `IS_ERR(c)`：`ERR_PTR(-ENAMETOOLONG)` 不是 NULL，通過檢查，`c->name` 解參考 `0xffffffffffffffdc` 而 SIGSEGV。注意連第一行 `web -> ok` 都沒印出來——輸出接管線時 stdout 全緩衝，行程被 signal 殺掉時緩衝區一起消失（[Ch 35](./35-stream-io-and-signals.md)）。

**兩種失敗慣例混用**是 ERR_PTR 最常見的 bug，而且雙向都會發生：對回傳 `ERR_PTR` 的函式檢查 NULL（上例），或對只回傳 NULL 的 `kmalloc()` 檢查 `IS_ERR()`（失敗時 NULL 不在錯誤範圍，檢查形同虛設）。型別系統表達不了「這個 `void *` 可能是錯誤碼」，核心只能靠工具：Coccinelle 腳本 `scripts/coccinelle/null/eno.cocci` 專找配置函式之後的 `IS_ERR()`，Dan Carpenter 的 Smatch 追蹤每個函式可能的回傳值並回報 "dereferencing possible ERR_PTR()"。核心提交紀錄中有大量 "checking IS_ERR() instead of NULL" 為題的修正。

和低位元藏資料對照：tagged pointer 依賴**對齊**，ERR_PTR 依賴**定址空間頂端沒有物件**；兩者都依賴實作定義的整數－指標轉換。核心只支援平坦定址、指標與 `long` 同寬的架構，所以這些假設在核心裡成立。

## 指標手法三：用 handle（索引＋世代）取代指標

Use-after-free 的本質是：物件釋放後指標值依 C11 6.2.4 §2 成為 indeterminate，但程式還拿著它；記憶體重新配置給別的物件後，舊指標讀寫到的是新物件。Andre Weissflog 在〈[Handles are the better pointers](https://floooh.github.io/2018/06/17/handles-vs-pointers.html)〉提出另一種介面：物件存在系統自己管理的陣列，對外只發整數 handle，低位元是槽位索引，高位元是該槽位的**世代計數**（generation）。槽位每重用一次世代加一，查詢時比對 handle 的世代與槽位目前的世代，不符就代表 handle 過期。

```
 handle (32 bit):   ┌──── generation (16) ────┬──── slot index (16) ────┐
                    │          0x0002          │          0x0001          │  = 0x00020001
                    └─────────────────────────┴─────────────────────────┘
 pool.slots[1].id == 0x00020001 ?  → 是：有效；否：已釋放或已被別人重用
```

```c
#include <stdint.h>
#include <stdio.h>

typedef struct { uint32_t id; } buf_h;      /* 0 永遠不是合法 id */

enum { SLOT_SHIFT = 16, SLOT_MASK = (1 << SLOT_SHIFT) - 1, POOL = 8 };

struct buffer { uint32_t id; int size; };   /* id == 0：槽位空著 */

static struct {
    struct buffer slots[POOL];
    uint16_t gen[POOL];
    int free_list[POOL], top;
} pool;

static void pool_init(void)
{
    for (int i = POOL - 1; i >= 1; i--)     /* 槽位 0 保留 */
        pool.free_list[pool.top++] = i;
}

static buf_h buf_create(int size)
{
    if (pool.top == 0)
        return (buf_h){ 0 };
    int i = pool.free_list[--pool.top];
    if (++pool.gen[i] == 0)                 /* 世代回繞：跳過 0 */
        pool.gen[i] = 1;
    uint32_t id = ((uint32_t) pool.gen[i] << SLOT_SHIFT) | (uint32_t) i;
    pool.slots[i] = (struct buffer){ .id = id, .size = size };
    return (buf_h){ id };
}

static struct buffer *buf_lookup(buf_h h)
{
    uint32_t i = h.id & SLOT_MASK;
    if (i == 0 || i >= POOL)
        return NULL;                        /* 格式不對的 handle */
    struct buffer *b = &pool.slots[i];
    return b->id == h.id ? b : NULL;        /* 已釋放或已重用：世代不符 */
}

static void buf_destroy(buf_h h)
{
    struct buffer *b = buf_lookup(h);
    if (!b)
        return;                             /* 重複 destroy 無害 */
    b->id = 0;
    pool.free_list[pool.top++] = (int) (b - pool.slots);
}

int main(void)
{
    pool_init();
    buf_h a = buf_create(64);
    struct buffer *pa = buf_lookup(a);      /* 對照組：留一個原始指標 */
    printf("a = 0x%08x (slot %u, gen %u)\n", a.id, a.id & SLOT_MASK, a.id >> SLOT_SHIFT);

    buf_destroy(a);
    buf_h b = buf_create(128);              /* 重用 a 的槽位 */
    printf("b = 0x%08x (slot %u, gen %u)\n", b.id, b.id & SLOT_MASK, b.id >> SLOT_SHIFT);

    printf("stale pointer sees size %d (b's data)\n", pa->size);
    printf("lookup(a) = %p\n", (void *) buf_lookup(a));
    buf_destroy(a);                         /* 被忽略：a 已過期 */
    printf("b still alive: %d\n", buf_lookup(b) != NULL);
    printf("zero handle: %p\n", (void *) buf_lookup((buf_h){ 0 }));

    /* 世代回繞：同一槽位重用 65535 次後，舊 handle 復活 */
    buf_h old = b;
    for (int k = 0; k < 65535; k++) {
        buf_destroy(b);
        b = buf_create(1);
    }
    printf("after 65535 reuses: b = 0x%08x, old handle valid again: %d\n",
           b.id, buf_lookup(old) != NULL);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra -fsanitize=address,undefined handle.c -o hd && ./hd
a = 0x00010001 (slot 1, gen 1)
b = 0x00020001 (slot 1, gen 2)
stale pointer sees size 128 (b's data)
lookup(a) = (nil)
b still alive: 1
zero handle: (nil)
after 65535 reuses: b = 0x00020001, old handle valid again: 1
```

這個輸出說明了三件事：

1. **ASan 抓不到 `pa`**：槽位在靜態陣列，記憶體從未歸還給配置器，對 ASan 而言一切合法；`pa->size` 卻讀到 `b` 的 128。這是「邏輯層的 use-after-free」，記憶體工具的盲點。
2. **handle 的偵測是決定性的**：過期的 `a` 必定得到 NULL，重複 destroy 也不會誤刪 `b`。
3. **但有上限**：世代只有 16 位元（跳過 0 後是 65535 種），最後一行顯示重用 65535 次後舊 handle **復活**了。sokol_gfx.h 的原始碼留著 FIXME 承認目前直接讓計數溢位；Weissflog 建議在即將溢位時停用該槽位。

其他好處來自間接層本身：系統可以任意搬移底層陣列，handle 是 32 位元整數便於複製；物件集中在連續陣列利於快取。代價是每次存取多一次索引與比較，而且只有經過 `buf_lookup` 的存取受保護，把結果長期存起來就回到原始指標的問題。ECS 函式庫 flecs 的實體 ID 用 32 位元索引＋16 位元世代；反例是 Linux 的 PID：沒有世代，只靠 `idr_alloc_cyclic()` 循環配置延後重用，所以 `kill(pid)` 可能送錯對象，Linux v5.3 的 `pidfd_open()` 改用 file descriptor 指涉行程，才從介面層面解決。

## 指標手法四：可攜地讀取未對齊資料與位元組序

網路封包、檔案格式裡的多位元組整數，位址未必對齊，位元組序（endianness）由格式而非主機決定。最直覺的 `*(const uint32_t *) p` 有兩個問題：C11 6.3.2.3 §7 規定指標轉換結果若未對齊於目標型別，**轉換本身**就是未定義行為；即使對齊，以 `uint32_t` 存取 `unsigned char` 陣列也違反 6.5 §7 的 effective type 規則（[Ch 7](./07-pointers-by-the-standard.md)）。`memcpy` 同時避開兩者：

```c
#define _DEFAULT_SOURCE
#include <endian.h>
#include <stdalign.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

uint32_t load_cast(const unsigned char *p) { return *(const uint32_t *) p; }

uint32_t load_native(const unsigned char *p)
{
    uint32_t v;
    memcpy(&v, p, sizeof v);   /* 主機位元組序、任意對齊 */
    return v;
}

uint32_t read_le32(const unsigned char *p)
{
    return (uint32_t) p[0] | (uint32_t) p[1] << 8 |
           (uint32_t) p[2] << 16 | (uint32_t) p[3] << 24;
}

uint32_t read_be32(const unsigned char *p)
{
    return (uint32_t) p[0] << 24 | (uint32_t) p[1] << 16 |
           (uint32_t) p[2] << 8 | (uint32_t) p[3];
}

float read_float(const unsigned char *p)
{
    float f;
    memcpy(&f, p, sizeof f);
    return f;
}

int main(int argc, char **argv)
{
    (void) argv;
    alignas(8) unsigned char pkt[] = {0xAA, 0x12, 0x34, 0x56, 0x78, 0x00, 0x00, 0x80, 0x3F};
    const unsigned char *p = pkt + 1;  /* 對 uint32_t 而言未對齊 */

    if (argc > 1) {
        printf("cast:   %08x\n", (unsigned) load_cast(p));
        return 0;
    }
    printf("native: %08x\n", (unsigned) load_native(p));
    printf("le32:   %08x\n", (unsigned) read_le32(p));
    printf("be32:   %08x\n", (unsigned) read_be32(p));
    printf("bswap:  %08x\n", (unsigned) __builtin_bswap32(load_native(p)));
    printf("be32toh:%08x\n", (unsigned) be32toh(load_native(p)));
    printf("float:  %g\n", read_float(pkt + 5));
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra unaligned.c -o ua && ./ua
native: 78563412
le32:   78563412
be32:   12345678
bswap:  12345678
be32toh:12345678
float:  1
$ gcc -std=c11 -O0 -fsanitize=alignment unaligned.c -o uas && ./uas x
unaligned.c:8:53: runtime error: load of misaligned address 0x7fff0f8d8d69 for type 'const uint32_t', which requires 4 byte alignment
cast:   78563412
```

x86-64 是 little-endian，`native` 依主機序得到 `78563412`；位移＋OR 的 `read_le32`/`read_be32` 結果與主機無關，不需要任何條件編譯。轉型版本在 x86-64 上「照樣能動」（硬體容許未對齊載入），UBSan 卻指出這是未定義行為——硬體容許不代表編譯器容許，編譯器可以依型別的對齊假設選用要求對齊的指令。

這些寫法的成本呢？看 `gcc -O2` 的組語：

```
load_cast:                load_native:              read_le32:
	movl (%rdi), %eax         movl (%rdi), %eax         movl (%rdi), %eax
	ret                       ret                       ret
read_be32:                                          read_be32 (gcc -O1):
	movl (%rdi), %eax                                   movzbl (%rdi), %eax
	bswap %eax                                          sall   $24, %eax
	ret                                                 movzbl 1(%rdi), %edx
read_be32 (gcc -O2 -march=haswell):                     ... 共 10 道指令
	movbel (%rdi), %eax
	ret
```

固定大小的 `memcpy` 被當成內建函式降為一道載入；四次位元組載入加位移、OR 被辨識成一次 32 位元載入（必要時加一道 `bswap`），`-march=haswell` 甚至合併成一道 `movbe`。但這項辨識有條件：**gcc 11 在 `-O1` 不做**，保留 10 道指令；clang 14 在 `-O1` 就合併成 `movl` + `bswapl`。所以「可攜寫法沒有成本」這句話的完整版是：**在 gcc `-O2` 或 clang `-O1` 以上沒有成本**。

開源專案的取捨各不相同：LZ4 以 `LZ4_FORCE_MEMORY_ACCESS` 提供 memcpy / packed 結構 / 直接轉型三種讀法，註解坦白說直接轉型是 "lie to the compiler about data alignment"，只在 GCC + ARMv6 這種情況選它；xxHash 預設用 `__attribute__((aligned(1), may_alias))` 的型別同時降低對齊並豁免 aliasing；SQLite 的資料庫檔是 big-endian，`sqlite3Get4byte` 在 little-endian GCC 上用 `memcpy` + `__builtin_bswap32`。Linux 核心的 `get_unaligned_be32()` 原本以 packed 結構實作，依原文所述在 v7.0 改為 `__builtin_memcpy`，commit 訊息寫明 packed 結構 "violates strict aliasing rules"。

## 效能技巧一：整數轉字串，一次兩位數

逐位數轉換每產生一個十進位數字就做一次 `/ 10` 與 `% 10`。一次處理兩位數可把除法次數減半，再用 200 位元組的表一次複製兩個字元。lwan 網頁伺服器的 `int-to-str.c` 註明取自 Andrei Alexandrescu 的作法，{fmt} 程式庫的 `digits2()` 是同一張表。下面比較三種寫法，輸入是 1000 萬個 64 位元亂數，位元長度大致均勻分布在 1–64，**量測前先逐一比對三者輸出一致**：

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define BUFSZ 21 /* UINT64_MAX 有 20 位數 + NUL */

static const char digits2[201] =
    "0001020304050607080910111213141516171819"
    "2021222324252627282930313233343536373839"
    "4041424344454647484950515253545556575859"
    "6061626364656667686970717273747576777879"
    "8081828384858687888990919293949596979899";

/* 每一位數一次除法，從尾巴往前寫 */
static char *u64_naive(uint64_t v, char buf[static BUFSZ])
{
    char *p = buf + BUFSZ - 1;
    *p = '\0';
    do {
        *--p = (char) ('0' + v % 10);
        v /= 10;
    } while (v);
    return p;
}

/* 每二位數一次除法，查表一次複製二個字元 */
static char *u64_2digits(uint64_t v, char buf[static BUFSZ])
{
    char *p = buf + BUFSZ - 1;
    *p = '\0';
    while (v >= 100) {
        unsigned i = (unsigned) (v % 100) * 2;
        v /= 100;
        p -= 2;
        memcpy(p, &digits2[i], 2);
    }
    if (v < 10) {
        *--p = (char) ('0' + v);
    } else {
        p -= 2;
        memcpy(p, &digits2[v * 2], 2);
    }
    return p;
}

static char *u64_snprintf(uint64_t v, char buf[static BUFSZ])
{
    snprintf(buf, BUFSZ, "%llu", (unsigned long long) v);
    return buf;
}

static uint64_t xorshift64(uint64_t *s)
{
    uint64_t x = *s;
    x ^= x << 13; x ^= x >> 7; x ^= x << 17;
    return *s = x;
}

#define N 10000000
static uint64_t vals[N];

static double bench(const char *name, char *(*fn)(uint64_t, char *))
{
    char buf[BUFSZ];
    unsigned long sum = 0;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < N; i++)
        sum += (unsigned char) fn(vals[i], buf)[0];
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double ns = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / N;
    printf("%-10s %6.2f ns/call (checksum %lu)\n", name, ns, sum);
    return ns;
}

int main(void)
{
    uint64_t s = 88172645463325252ull;
    for (int i = 0; i < N; i++) {           /* 位元長度大致均勻分布於 1..64 */
        uint64_t x = xorshift64(&s);
        int shift = (int) (x % 64);
        vals[i] = xorshift64(&s) >> shift;
    }
    char a[BUFSZ], b[BUFSZ], c[BUFSZ];
    for (int i = 0; i < N; i++) {
        if (strcmp(u64_naive(vals[i], a), u64_2digits(vals[i], b)) ||
            strcmp(u64_naive(vals[i], a), u64_snprintf(vals[i], c))) {
            printf("mismatch at %llu\n", (unsigned long long) vals[i]);
            return 1;
        }
    }
    printf("UINT64_MAX -> %s\n", u64_2digits(UINT64_MAX, a));
    for (int r = 0; r < 2; r++) {
        bench("naive", u64_naive);
        bench("2digits", u64_2digits);
        bench("snprintf", u64_snprintf);
    }
    return 0;
}
```

`-std=c11 -D_POSIX_C_SOURCE=200809L -O2` 編譯（嚴格 c11 模式下 glibc 不宣告 `clock_gettime`），三次執行各取第二輪：

| 編譯器 | naive | 2digits | snprintf |
|---|---|---|---|
| gcc 11 `-O2` | 16.4–17.5 ns | 12.9–13.6 ns | 58.0–61.7 ns |
| clang 14 `-O2` | 16.3–16.7 ns | 12.9–13.5 ns | 58.3–66.8 ns |

兩位數查表比逐位快約 20%，比 `snprintf` 快約 4.5 倍。收益不如想像中大的原因在下一節：**編譯器早已把 `/ 10` 改寫成乘法與位移**，在 Zen 3 上很便宜；`snprintf` 的成本主要在格式字串解析與 locale 處理，不在數字轉換本身。

lwan 的負數處理另有邊界問題：`uint_to_string((size_t)-value, ...)` 在 `value == SSIZE_MIN` 時 `-value` 溢位，是未定義行為。修正是先轉無號再取負：`-(size_t)value`，無號運算依 C11 6.2.5 §9 取模，對 `SSIZE_MIN` 也得到正確的絕對值（[Ch 22](./22-integer-overflow.md)）。

## 效能技巧二：一次比較完成範圍檢查

`x >= lo && x <= hi` 有兩個條件分支。流傳的單一比較寫法有三種，正確性各不相同：

```c
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

static bool ref(int x, int lo, int hi) { return x >= lo && x <= hi; }

/* 有號減法：二數相距太遠就溢位 */
static bool or_signed(int x, int lo, int hi) { return ((x - lo) | (hi - x)) >= 0; }

/* 沒有 UB，但轉回 int32_t 是實作定義，且寬區間會誤判 */
static bool or_unsigned(int x, int lo, int hi)
{
    return (int32_t) (((uint32_t) x - (uint32_t) lo) | ((uint32_t) hi - (uint32_t) x)) >= 0;
}

/* lo <= hi 時對所有 int 都正確 */
static bool in_range(int x, int lo, int hi)
{
    return (unsigned) x - (unsigned) lo <= (unsigned) hi - (unsigned) lo;
}

/* 網路上流傳的「半個平移」寫法：xmax 若是右邊界就錯 */
static bool half_shift(int x, int xmin, int xmax) { return (unsigned) (x - xmin) < (unsigned) xmax; }

int main(int argc, char **argv)
{
    (void) argv;
    if (argc > 1) { /* 搭配 -fsanitize=undefined */
        printf("%d\n", or_signed(INT_MIN, 0, 10));
        return 0;
    }
    printf("half_shift(25, 10, 20)      = %d, expected %d\n", half_shift(25, 10, 20), 25 >= 10 && 25 < 20);
    printf("or_unsigned(1e9, -2e9, 2e9) = %d, expected %d\n",
           or_unsigned(1000000000, -2000000000, 2000000000), ref(1000000000, -2000000000, 2000000000));
    uint64_t s = 88172645463325252ull;
    long bad = 0;
    for (long i = 0; i < 100000000; i++) {
        s ^= s << 13; s ^= s >> 7; s ^= s << 17;
        int a = (int) (uint32_t) s, b = (int) (uint32_t) (s >> 32);
        s ^= s << 13; s ^= s >> 7; s ^= s << 17;
        int64_t near = (int64_t) a + (int64_t) (s % 64) - 32; /* 一半落在邊界附近 */
        int x = (i & 1) ? (int) (uint32_t) s
                        : near > INT_MAX ? INT_MAX : near < INT_MIN ? INT_MIN : (int) near;
        int lo = a < b ? a : b, hi = a < b ? b : a;
        bad += in_range(x, lo, hi) != ref(x, lo, hi);
    }
    printf("in_range: %ld mismatches in 1e8 random cases\n", bad);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra range.c -o rg && ./rg       # clang 14 輸出相同
half_shift(25, 10, 20)      = 1, expected 0
or_unsigned(1e9, -2e9, 2e9) = 0, expected 1
in_range: 0 mismatches in 1e8 random cases
$ gcc -std=c11 -O0 -fsanitize=undefined range.c -o rgu && ./rgu x
range.c:9:71: runtime error: signed integer overflow: 10 - -2147483648 cannot be represented in type 'int'
```

逐一說明為什麼：

- **`half_shift`** 只平移了一半：右側應該是 `xmax - xmin`。`25 - 10 = 15 < 20` 被判為在區間內。
- **OR 技巧**只看符號位元：區間寬度超過 `INT_MAX` 時，區間內的 `x` 與邊界相差超過 $2^{31}-1$，符號位元被設起，誤判為區間外。有號版本還多了溢位 UB。
- **`in_range`** 的原理是模 $2^{32}$ 算術：把 `x - lo` 視為 $[0, 2^{32})$ 中的值，它落在 $[0, hi - lo]$ 若且唯若 $x \in [lo, hi]$，前提是 `lo <= hi`。關鍵是**先轉無號再相減**；常見的 `(unsigned)(x - lo)` 先做有號減法，溢位問題還在。

邊界是常數時，現代編譯器自己就會做這個轉換；邊界是變數時不會，因為它無法排除 `lo > hi`。看組語（`-O2 -masm=intel`）：

```
range_const:  (x >= 10 && x <= 100)       draw_pixel1:  (x >= min && x < max)
  gcc:   sub edi, 10                         gcc / clang 都是兩個分支：
         cmp edi, 90                           cmp edi, esi
         setbe al                              jl  ...
  clang: add edi, -10                          cmp edi, edx
         cmp edi, 91                           jl / jge ...
         setb al
                                           draw_pixel2:  (無號寫法)
                                             mov eax, edi
                                             sub edx, esi
                                             sub eax, esi
                                             cmp eax, edx
                                             jb / jae ...      ← 一個分支
```

手動改寫的真正意義是**把 `lo <= hi` 這個編譯器不知道的前提寫進程式碼**。它是否更快則取決於分支是否難以預測、以及編譯器是否已用 `ccmp`、SIMD 等手法自行合併——原文在 Threadripper 與 Ampere eMAG 上的量測顯示，同一份原始碼在不同編譯器版本下的分支數從 1 到 4 都有，快慢可差 1.6 倍以上，無法由指令數推得。Linux 核心 v6.6 的 `in_range32(val, start, len)` 正是無號減法寫法，註解明言它在 `start + len` 溢位時和直觀寫法答案不同；Spectre v1 緩解用的 `array_index_mask_nospec()` 則用 OR 技巧的形式，目的不是減少分支，而是讓推測執行時索引也被遮罩夾住。

## 效能技巧三：除以常數變乘法（推導 magic number）

整數除法指令的延遲通常是乘法的數倍（確切週期數依微架構而異，可查 Agner Fog 的 instruction tables；本章不量測單道指令，下面直接量整個迴圈）。所以編譯器遇到「除以常數」時，會改寫成「乘以某個 magic number 再右移」。我們來自己推導這個數。

**目標**：對所有 32 位元無號整數 $n$，找 $m$ 與 $s$ 使 $\lfloor n/d \rfloor = \lfloor n \cdot m / 2^{s} \rfloor$。

**直覺**：$m / 2^s$ 是 $1/d$ 的近似值。取 $m = \lceil 2^s / d \rceil$（向上取整，確保不會少算），令誤差 $e = m \cdot d - 2^s$（$0 \le e < d$）。則

$$\frac{n \cdot m}{2^s} = \frac{n}{d} + \frac{n \cdot e}{d \cdot 2^s}$$

多出來的那一項只要小於 $1/d$，就不會跨過下一個整數（$n/d$ 的小數部分最多是 $(d-1)/d$）。也就是要 $n \cdot e < 2^s$ 對所有 $n < 2^{32}$ 成立，充分條件是 $e \le 2^{s-32}$。令 $s = 32 + \ell$，條件就是 **$e \le 2^\ell$**。這正是 Granlund 與 Montgomery 在 PLDI 1994〈Division by Invariant Integers using Multiplication〉給的條件。

寫程式找最小的 $\ell$，並**窮舉全部 $2^{32}$ 個輸入**驗證：

```c
/* 推導「無號 32 位元除以常數 d」的 magic number，並窮舉驗證 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    static const uint32_t ds[] = { 3, 7, 10, 60, 255, 1000 };
    int verify = argc > 1;
    for (unsigned k = 0; k < sizeof ds / sizeof ds[0]; k++) {
        uint32_t d = ds[k];
        for (unsigned l = 0; l <= 32; l++) {
            /* m = ceil(2^(32+l) / d)，用 128 位元避免溢位 */
            unsigned __int128 p = (unsigned __int128) 1 << (32 + l);
            unsigned __int128 m = (p + d - 1) / d;
            unsigned __int128 e = m * d - p;       /* 誤差 e = m*d - 2^(32+l) */
            if (e <= ((unsigned __int128) 1 << l)) {
                printf("d=%-5u l=%-2u shift=%-2u m=0x%llx%s e=%llu\n", d, l, 32 + l,
                       (unsigned long long) m, m >> 32 ? " (33 bits!)" : "",
                       (unsigned long long) e);
                if (verify) {
                    long bad = 0;
                    for (uint64_t n = 0; n <= UINT32_MAX; n++)
                        bad += (uint32_t) (((unsigned __int128) n * m) >> (32 + l)) != n / d;
                    printf("         exhaustive check over 2^32 inputs: %ld wrong\n", bad);
                }
                break;
            }
        }
    }
    return 0;
}
```

```
$ gcc -O2 magic.c -o magic && time ./magic v
d=3     l=1  shift=33 m=0xaaaaaaab e=1
         exhaustive check over 2^32 inputs: 0 wrong
d=7     l=3  shift=35 m=0x124924925 (33 bits!) e=3
         exhaustive check over 2^32 inputs: 0 wrong
d=10    l=3  shift=35 m=0xcccccccd e=2
         exhaustive check over 2^32 inputs: 0 wrong
d=60    l=5  shift=37 m=0x88888889 e=28
         exhaustive check over 2^32 inputs: 0 wrong
d=255   l=7  shift=39 m=0x80808081 e=127
         exhaustive check over 2^32 inputs: 0 wrong
d=1000  l=6  shift=38 m=0x10624dd3 e=56
         exhaustive check over 2^32 inputs: 0 wrong

real	0m44.985s
```

現在拿這張表去對 gcc 的輸出（`divk.c` 裡每個函式就是一行 `return x / d;`，`-O2`）：

```
div255:                              div10:
	movl  %edi, %eax                     movl  %edi, %eax
	movl  $2155905153, %edx  # 0x80808081  movl  $3435973837, %edx  # 0xcccccccd
	imulq %rdx, %rax                     imulq %rdx, %rax
	shrq  $39, %rax          # 32 + 7    shrq  $35, %rax          # 32 + 3
	ret                                  ret
div7:                                divvar:  (x / d，d 是參數)
	movl  %edi, %eax                     movl  %edi, %eax
	imulq $613566757, %rax, %rax         xorl  %edx, %edx
	shrq  $32, %rax          # t = hi(n*m')  divl  %esi      # 真正的除法指令
	subl  %eax, %edi         # n - t     ret
	shrl  %edi               # (n-t)/2
	addl  %edi, %eax         # + t
	shrl  $2, %eax           # >> 2  (總共 32+1+2 = 35)
	ret
```

`0x80808081` 右移 39、`0xcccccccd` 右移 35 和推導完全一致。最有意思的是 `d = 7`：magic number `0x124924925` 有 **33 位元**，放不進 32 位元暫存器做「32×32→64」乘法。gcc 的解法是只乘低 32 位元 `m' = m - 2^{32} = 0x24924925 = 613566757`，得到 $t = \lfloor n m' / 2^{32} \rfloor$，再用 $\lfloor (n - t)/2 \rfloor + t$ 補回那個 $2^{32}$ 的貢獻（這樣寫可以避免 $n + t$ 溢位），最後右移剩下的 2 位。有號除法 `sdiv10` 則多一步：用 `sarl $31` 取出符號、最後減掉，因為 C 的有號除法向零截斷，而右移向負無限大取整。

連 `-O0` 都用乘法（`imulq` + `shrq $32` + `shrl $7`）——這不是最佳化選項決定的，而是 gcc 前端就會做的改寫。

### 那手寫的「快速除以 255」呢？

影像合成常要除以 255（alpha blending 把兩個 8 位元值的乘積正規化）。網路上流傳兩種「比 `/ 255` 快」的寫法，先驗證正確性：

```c
#include <stdint.h>
#include <stdio.h>

#define div_255_fast(x) (((x) + (((x) + 257) >> 8)) >> 8)

static inline uint32_t fast_div_255_accurate(uint32_t n)   /* 網路流傳版 */
{
    uint64_t M = (((uint64_t) 1) << 40) / 255 + 1;
    return (M * n) >> 40;
}

static inline uint32_t div255_mulhi(uint32_t n)             /* 編譯器的作法 */
{
    return (uint32_t) (((uint64_t) n * 0x80808081u) >> 39);
}

int main(void)
{
    uint32_t x = 0;
    while (div_255_fast(x) == x / 255)
        x++;
    printf("div_255_fast: exact on [0, %u], %u -> %u (want %u)\n", x - 1, x,
           div_255_fast(x), x / 255);

    long acc_bad = 0, mulhi_bad = 0;
    uint32_t acc_first = 0;
    for (uint64_t i = 0; i <= UINT32_MAX; i++) {
        uint32_t n = (uint32_t) i, q = n / 255;
        if (fast_div_255_accurate(n) != q && !acc_bad++)
            acc_first = n;
        mulhi_bad += div255_mulhi(n) != q;
    }
    printf("fast_div_255_accurate: %ld wrong, first at 0x%08x -> %u (want %u)\n", acc_bad,
           acc_first, fast_div_255_accurate(acc_first), acc_first / 255);
    printf("(n * 0x80808081) >> 39: %ld wrong\n", mulhi_bad);
    return 0;
}
```

```
div_255_fast: exact on [0, 65789], 65790 -> 257 (want 258)
fast_div_255_accurate: 16777216 wrong, first at 0xff000000 -> 0 (want 16777216)
(n * 0x80808081) >> 39: 0 wrong
```

`div_255_fast` 精確範圍是 $[0, 65789]$，涵蓋 alpha blending 的最大乘積 $255 \times 255 = 65025$，可以用。名為 "accurate" 的版本卻在最後 $2^{24}$ 個輸入全錯：它的 $M = \lfloor 2^{40}/255 \rfloor + 1 = 4311810306$ 略大於 $2^{32}$，$n \ge \mathtt{0xFF000000}$ 時 $M \cdot n$ 超過 $2^{64}$ 在 `uint64_t` 上回繞。倒數本身沒錯，錯在乘積寬度。而 `0x80808081` 就是我們剛推導出的數。

再量測速度（65536 個「兩個 8 位元值的乘積」，每個函式重複 1000 次取 5 輪最小值；`x / var` 的除數放在全域變數，編譯器無法當常數）：

| 編譯器與選項 | `x / 255` | `div_255_fast` | `x / var`（真除法） |
|---|---|---|---|
| gcc 11 `-O2`（不向量化） | 32.0 ms | 32.8 ms | 92.8 ms |
| gcc 11 `-O3`（向量化） | 8.0 ms | 7.8 ms | 92.8 ms |
| clang 14 `-O2`（向量化） | 9.8 ms | 5.9 ms | 92.9 ms |
| clang 14 `-O2 -fno-vectorize -fno-slp-vectorize` | 18.7 ms | 23.9 ms | 93.8 ms |

讀法：(1) 純量時編譯器的 `/ 255` 不比手寫慢，clang 純量下手寫反而慢 28%（三道相依運算比一道乘法加位移長）；(2) 向量化之後兩者都快 2–4 倍，clang 向量化後手寫版少約 40% 時間——推測原因是 SSE2 沒有 32 位元的「取乘積高半部」指令，`/ 255` 得用 `pmuludq` 加重排模擬，而手寫版只有加法與位移（未逐指令分析）；(3) **真正慢的是除數不是常數**：每次約 1.4 ns（92.8 ms / 6553.6 萬次），而且 SSE2 沒有整數除法，迴圈無法向量化，比向量化的 `/ 255` 慢 9–12 倍。注意 gcc 11 的 `-O2` 不做自動向量化（GCC 12 才在 `-O2` 啟用便宜的向量化），這也是為什麼同一份程式換 gcc 版本就可能差好幾倍。

手寫公式真正有用的地方是**手寫 SIMD intrinsics**：編譯器不會替你改寫 intrinsics 裡的除法。除數在執行時期才決定、但之後不變時，可以用 [libdivide](https://github.com/ridiculousfish/libdivide) 在初始化時算出 magic number——它做的就是上面那支 `magic.c` 的事。

## 效能技巧四：無分支的飽和與 2 的冪

流傳甚廣的位元運算夾值寫法：

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

static inline int32_t clamp_to_0(int32_t x) { return ((-x) >> 31) & x; }
static inline int32_t clamp_to_255(int32_t x) { return (((255 - x) >> 31) | x) & 255; }
static inline int32_t relu_bits(int32_t x) { return x & ~(x >> 31); }      /* 無溢位版本 */
static inline int32_t clamp_u8(int32_t x) { return x < 0 ? 0 : x > 255 ? 255 : x; }

int main(void)
{
    static const int32_t t[] = {INT_MIN, INT_MIN + 1, -256, -1, 0, 1, 255, 256, INT_MAX};
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; i++)
        printf("%11d: clamp_to_0 %11d relu_bits %10d | clamp_to_255 %3d clamp_u8 %3d\n",
               t[i], clamp_to_0(t[i]), relu_bits(t[i]), clamp_to_255(t[i]), clamp_u8(t[i]));
    return 0;
}
```

```
$ gcc -std=c11 -O2 clamp.c -o cp && ./cp
-2147483648: clamp_to_0 -2147483648 relu_bits          0 | clamp_to_255 255 clamp_u8   0
-2147483647: clamp_to_0           0 relu_bits          0 | clamp_to_255 255 clamp_u8   0
       -256: clamp_to_0           0 relu_bits          0 | clamp_to_255   0 clamp_u8   0
         -1: clamp_to_0           0 relu_bits          0 | clamp_to_255 255 clamp_u8   0
          0: clamp_to_0           0 relu_bits          0 | clamp_to_255   0 clamp_u8   0
          1: clamp_to_0           1 relu_bits          1 | clamp_to_255   1 clamp_u8   1
        255: clamp_to_0         255 relu_bits        255 | clamp_to_255 255 clamp_u8 255
        256: clamp_to_0         256 relu_bits        256 | clamp_to_255 255 clamp_u8 255
 2147483647: clamp_to_0  2147483647 relu_bits 2147483647 | clamp_to_255 255 clamp_u8 255
$ clang -std=c11 -O2 clamp.c -o cp && ./cp | head -2
-2147483648: clamp_to_0           0 relu_bits          0 | clamp_to_255   0 clamp_u8   0
-2147483647: clamp_to_0           0 relu_bits          0 | clamp_to_255   1 clamp_u8   0
```

四種組合 gcc/clang × `-O0`/`-O2` 中，只有 clang `-O2` 的前兩列和其他三種不同。

問題一層一層看：

1. `>> 31` 把負數變全 1 依賴「有號右移是算術位移」，C11 6.5.7 §5 規定這是**實作定義**（gcc、clang 都定義為算術位移，見 [Ch 23](./23-bitwise-basics.md)）。
2. `-x` 在 `x == INT_MIN`、`255 - x` 在 `x < INT_MIN + 255` 時**溢位，是未定義行為**——表裡前兩列正是這些輸入，gcc 與 clang 推出不同答案，連 `-fwrapv` 讓溢位回繞後 `clamp_to_0(INT_MIN)` 仍是 `INT_MIN`，沒夾到 0。
3. `clamp_to_255` 根本沒處理下界：`-1` 變 255。

UBSan 點名兩處：

```
clamp.c:5:56: runtime error: negation of -2147483648 cannot be represented in type 'int32_t' (aka 'int'); cast to an unsigned type to negate this value to itself
clamp.c:6:63: runtime error: signed integer overflow: 255 - -2147483648 cannot be represented in type 'int'
```

正確又無分支的寫法有兩個：`relu_bits`（`x & ~(x >> 31)`，用 x 自己的符號產生遮罩，沒有溢位，只剩右移的實作定義）與直接寫條件運算子 `clamp_u8`——gcc、clang 都會編成 `cmov`，沒有分支，**而且兩種未定義/實作定義都沒有**。結論：想要無分支，先寫清楚的條件運算子、看組語；編譯器沒做到再手寫位元版本。

向上取到 2 的冪（二倍成長容器、buddy 配置器的基本操作）也有同類邊界：

```c
static uint32_t next_pow2_or(uint32_t x)
{
    x--; x |= x >> 1; x |= x >> 2; x |= x >> 4; x |= x >> 8; x |= x >> 16;
    return x + 1;
}
static uint32_t next_pow2_clz(uint32_t x)
{
    if (x <= 1) return 1;
    if (x > UINT32_C(1) << 31) return 0;                 /* 2^32 放不下 */
    return UINT32_C(1) << (32 - __builtin_clz(x - 1));
}
```

```
         0: or          0  clz          1
         1: or          1  clz          1
         3: or          4  clz          4
      1000: or       1024  clz       1024
2147483648: or 2147483648  clz 2147483648
2147483649: or          0  clz          0
4294967295: or          0  clz          0
```

OR 擴散版「先減 1、把最高位的 1 往右擴散成全 1、再加 1」邏輯正確，但 `x == 0` 時 `x--` 回繞成全 1，結果是 0 而不是 1。`__builtin_clz(0)` 本身未定義，所以 clz 版要先處理 `x <= 1`。C23 在 `<stdbit.h>` 標準化為 `stdc_bit_ceil()`，定義 `stdc_bit_ceil(0) == 1`、放不下時回 0；依原文 glibc 2.39 起提供，本機 glibc 2.35 沒有，**本段未實測**。Linux 的 `roundup_pow_of_two()` 註解明定 `n == 0` 時結果未定義。

## 效能技巧五：哨兵值省去邊界檢查

循序搜尋每一步做兩次比較：到尾端了嗎？等於鍵值嗎？Knuth 在 TAOCP 第 3 卷 §6.1 的 Algorithm Q 在第 N+1 筆放入鍵值本身當哨兵（sentinel），迴圈保證會停，只比較鍵值。代價：陣列要多一個可寫位置、`const` 資料不能用、多執行緒以不同鍵同時搜尋同一陣列會有 data race。

```c
/* Algorithm S：每個元素比二次（邊界、鍵值） */
__attribute__((noinline)) static size_t search_plain(int *a, size_t n, int key)
{
    for (size_t i = 0; i < n; i++)
        if (a[i] == key)
            return i;
    return n;
}

/* Algorithm Q：a[n] 是可寫的備用格，放哨兵 */
__attribute__((noinline)) static size_t search_sentinel(int *a, size_t n, int key)
{
    a[n] = key;
    size_t i = 0;
    while (a[i] != key)
        i++;
    return i; /* i == n：找不到 */
}

/* 哨兵 + 展開四次：仍然只用一個哨兵 */
__attribute__((noinline)) static size_t search_sentinel4(int *a, size_t n, int key)
{
    a[n] = key;
    for (size_t i = 0;; i += 4) {
        if (a[i] == key) return i;
        if (a[i + 1] == key) return i + 1;
        if (a[i + 2] == key) return i + 2;
        if (a[i + 3] == key) return i + 3;
    }
}
```

（完整程式含正確性比對與計時器，`n = 4096`、搜尋不存在的鍵、5 輪取最佳。計時迴圈裡放 `__asm__ volatile("" ::: "memory")`，否則 clang 會認定 `search_plain` 只讀記憶體、參數不變，把呼叫提出迴圈，量到接近 0。）

```
== gcc -O2                         == clang -O2
n=4096 mismatches=0                n=4096 mismatches=0
plain     0.243–0.257 ns/elem      plain     0.249–0.286 ns/elem
sentinel  0.240–0.246 ns/elem      sentinel  0.240–0.288 ns/elem
sentinel4 0.121 ns/elem            sentinel4 0.118–0.128 ns/elem
```

看 gcc 的迴圈：`search_plain` 每個元素是 `addq`/`cmpq`/`je`/`cmpl`/`jne` 五道指令，`search_sentinel` 只有 `addq`/`cmpl`/`jne` 三道。可是兩者**一樣快**——約 0.24 ns，在 4 GHz 上下的 Zen 3 正好約一個時脈週期一個元素，和「每輪一次跳回分支」相符：寬發射的亂序處理器有餘裕執行多出的比較，瓶頸是每輪一次的迴圈分支。展開四次後每四個元素才跳回一次，時間減半。

所以單獨的哨兵在今天的高階處理器上不一定有收益；它的價值在於**迴圈不可能越界**，所以展開時不必處理尾端。哨兵最常見的真實用途是詞法分析器：re2c 手冊的 "Handling the end of input" 第一種方法就是 sentinel，ninja 的 lexer 用 `std::string` 結尾保證存在的 `'\0'` 當哨兵；代價是 `'\0'` 不能出現在 `.ninja` 檔中（原文實測插入 NUL 後其後內容被整段忽略）。C 字串本身就是以 `'\0'` 為哨兵的設計（[Ch 10](./10-strings-and-null.md)）。

## 效能技巧六：Duff's device 在現代處理器上

Duff's device 的語法與它在 coroutine 上的延伸已在 [Ch 31](./31-goto-and-control-flow.md) 介紹。1983 年 Tom Duff 在 Lucasfilm 用它把 `short` 陣列寫進記憶體映射的 I/O 暫存器：展開 8 次減少迴圈判斷，餘數直接跳進展開後迴圈的中段。它是合法的 C——C11 6.8.4.2 §4 說 `switch` 會讓控制 "jump to, into, or past" switch body，`into` 就是依據。

Duff 本人在 1988 年的信裡已經寫了警告："Transformations like this can only be justified by measuring the resulting code... Don't try to be smarter than an over-clever C compiler"。我們來量：

```c
void xor_plain(uint8_t *restrict dst, const uint8_t *restrict src, size_t n)
{
    for (size_t i = 0; i < n; i++)
        dst[i] = src[i] ^ 0x80;
}

void xor_duff(uint8_t *restrict dst, const uint8_t *restrict src, size_t n)
{
    if (!n)
        return;
    size_t k = (n + 7) / 8;
    switch (n % 8) {
    case 0: do { *dst++ = *src++ ^ 0x80;
    case 7:      *dst++ = *src++ ^ 0x80;
    case 6:      *dst++ = *src++ ^ 0x80;
    case 5:      *dst++ = *src++ ^ 0x80;
    case 4:      *dst++ = *src++ ^ 0x80;
    case 3:      *dst++ = *src++ ^ 0x80;
    case 2:      *dst++ = *src++ ^ 0x80;
    case 1:      *dst++ = *src++ ^ 0x80;
            } while (--k > 0);
    }
}
```

兩個函式放在獨立的 `kern.c`，量測程式經 `volatile` 函式指標呼叫（避免依已知的 n 特化），量測前先對 n = 0–299 逐一比對輸出、確認不越界寫入。每種長度呼叫到總量 $2^{28}$ 位元組，5 輪取最佳：

| 編譯器 | n=7 plain / duff | n=64 plain / duff | n=4099 plain / duff（ns/byte） |
|---|---|---|---|
| gcc 11 `-O2` | 0.405 / 0.407 | 0.268 / 0.326 | 0.259 / 0.323 |
| gcc 11 `-O3` | 0.494 / 0.385 | 0.042 / 0.329 | **0.016 / 0.330** |
| clang 14 `-O2` | 0.403 / 0.482 | 0.041 / 0.402 | **0.021 / 0.472** |

在 4099 位元組，**純迴圈比 Duff's device 快 20 倍以上**（gcc `-O3`、clang `-O2`）。原因寫在編譯器報告裡：

```
$ clang -O2 -c kern.c -Rpass=loop-vectorize -Rpass-missed=loop-vectorize
kern.c:6:5: remark: vectorized loop (vectorization width: 16, interleaved count: 2) [-Rpass=loop-vectorize]
$ gcc -O3 -c kern.c -fopt-info-vec-optimized
kern.c:6:26: optimized: loop vectorized using 16 byte vectors
kern.c:6:26: optimized: loop vectorized using 8 byte vectors
```

純迴圈被向量化成一次 32 位元組；Duff's device **根本不出現在報告裡**，連 "missed" 都沒有。迴圈最佳化以自然迴圈（natural loop）為單位，要求迴圈只有一個入口；Duff 的 `do`/`while` 有 8 個入口（`case 0` 是迴圈頭，`case 7`–`case 1` 直接跳進中段），構成不可歸約（irreducible）的控制流程圖，整條迴圈最佳化管線直接跳過它，結果是一次一個位元組。只有在編譯器本來就不向量化時（gcc 11 `-O2`），兩者才不相上下——而這時改成 `-O3` 就能得到更好的結果。極短長度（n=7）時 Duff 在 gcc `-O3` 下略快，因為向量化的純迴圈要先經過純量的前置處理；若熱點真的是幾十位元組以下的短操作，應該先量測再考慮依長度分派到專用路徑，而不是在整個程式散布手工展開。

歷史印證：Ted Ts'o 在 2000 年的 LKML 郵件轉述 Jim Gettys 的說法，XFree86 4.0 移除所有 Duff's device 後伺服器縮小半 MB 而且更快（這是轉述，郵件中沒有量測資料）。Linux v5.18 的 fbdev 補丁把手工展開 8 次的填充迴圈換成 `memset_l()`，提交說明量到 166,603 cycles 降為 26,586 cycles。Duff's device 的原型在**組合語言**中仍然合理：RISC-V 的 `arch/riscv/lib/memset.S` 註解寫著 "Duff's device with 32 XLEN stores per iteration"——組語沒有編譯器替你向量化。

## 再檢驗常見的效能建議

許多 C 效能建議源自 32 位元 Arm 時代的編譯器指南。在本機 gcc 11 / clang 14 `-O2` 重新檢驗幾條（完整的 15 條清單見原文）：

| 建議 | 結論 | 本機依據 |
|---|---|---|
| 以 `register` 宣告熱門變數 | 已過時 | 最佳化編譯器不參考它；唯一可觀察的語意是禁止取址：`&x` → `address of register variable ‘x’ requested` |
| `val + val + val` 比 `val * 3` 快 | 無差別 | 兩者都是 `leal (%rdi,%rdi,2), %eax` |
| `printf("...\n")` 改 `puts` | 編譯器代勞 | `printf("hello\n")` 編成 `jmp puts@PLT` |
| 除以 2 的冪時用 `unsigned` | **成立** | `div32u` 一道 `shrl`；`div32s` 要 `testl`/`leal 31`/`cmovns`/`sarl` 四道；`% 32` 有號要 6 道 |
| `x / 3.0` 改 `x * (1.0/3.0)` | **不等價** | 1–1000 的整數中 332 個結果不同 |
| 以展開迴圈計算位元數 | 已過時 | 用 `__builtin_popcount`；依原文，x86-64 基準指令集沒有 `popcnt`，要加 `-mpopcnt` 或 `-march=x86-64-v2` 才是單一指令（本機未重測這一列） |

有號除以 32 為什麼要四道指令？C 的有號除法**向零截斷**，算術右移卻**向負無限大取整**：`-1 >> 5 == -1` 而 `-1 / 32 == 0`。編譯器必須先對負數加 31 再右移，這就是 `leal 31(%rdi)` + `cmovns` 的用途。如果值必然非負，改用 `unsigned`，或用 `__builtin_unreachable` 告訴編譯器範圍——這是**語意**上的資訊，編譯器自己推不出來。

浮點倒數的實驗：

```c
#include <stdio.h>
int main(void)
{
    volatile double third = 1.0 / 3.0;
    int shown = 0, diff = 0;
    for (int i = 1; i <= 1000; i++) {
        volatile double x = i;
        double a = x / 3.0, b = x * third;
        if (a != b) {
            diff++;
            if (shown++ < 3)
                printf("x=%d: x/3.0=%.17g x*(1.0/3.0)=%.17g\n", i, a, b);
        }
    }
    printf("%d of 1000 integers differ\n", diff);
    return 0;
}
```

```
x=5: x/3.0=1.6666666666666667 x*(1.0/3.0)=1.6666666666666665
x=7: x/3.0=2.3333333333333335 x*(1.0/3.0)=2.333333333333333
x=10: x/3.0=3.3333333333333335 x*(1.0/3.0)=3.333333333333333
332 of 1000 integers differ
```

`1.0/3.0` 本身已捨入一次，再乘又捨入一次，兩次捨入不保證等於一次除法的正確捨入（[Ch 26](./26-floating-point-errors.md)）。所以 gcc 預設保留 `divsd`；`x / 4.0` 則自動變 `mulsd`（0.25 可精確表示，結果必定相同）；加 `-freciprocal-math` 後 `x / 3.0` 也變 `mulsd`，等於你宣告接受上述誤差。

這份清單的規律：**仍然成立的建議都與語意有關**（有號與無號、浮點捨入、程式設計者知道而編譯器不知道的不變量）；純粹改寫語法形式的建議（`register`、`val*3`、`puts`），編譯器早就代勞了。

## 刻意晦澀的程式碼：Underhanded C 與蝌蚪運算子

[Underhanded C Contest](https://www.underhanded-c.org/)（2005–2015，Scott Craver 主辦）和 IOCCC 相反：程式碼要**清楚、易讀、看似可信**，卻暗藏指定的惡意行為，被發現時最好能辯稱無心。兩個得獎手法：

- **2008 年**（題目：遮蔽 PPM 影像的矩形區域）：John Meacham 選用以 ASCII 十進位文字存畫素的 P3 格式，把被遮蔽畫素的每個數字字元換成 `'0'`。畫面全黑，但 `7` 變 `0`、`255` 變 `000`——**位數洩漏了亮度的數量級**。
- **2015 年**（題目：比對核彈頭能譜）：Linus Åkesson 在自訂標頭寫 `typedef double float_t;`，另一個檔案引入 `<math.h>`，其中 `float_t` 是 `float`。兩個編譯單元沒有同時看到兩個定義，編譯器不報錯，一邊傳 `double` 陣列、一邊以 `float` 解讀，比對時只看到能譜前半段而且數值範圍被壓扁。

兩者都沒有晦澀語法，靠的是審查者不會追究的假設：檔案格式的文字表示、跨編譯單元的型別一致性（後者正是 [Ch 39](./39-static-linking.md) 連結器不檢查型別的後果）。這是資安程式碼審查最需要的直覺。

對照之下，「蝌蚪運算子」只是看起來怪。Raymond Chen 在 2015 年的愚人玩笑文宣稱 `-~y` 等於 `y + 1`、`~-y` 等於 `y - 1`，原理是二補數恆等式 `-x == ~x + 1`（C23 起標準保證二補數，[Ch 21](./21-number-systems.md)）：

```c
#include <limits.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    (void) argv;
    int ys[] = { 3, 0, -1, -5 };
    for (int i = 0; i < 4; i++) {
        int y = ys[i];
        printf("y = %2d: -~y = %2d, ~-y = %2d, ~y = %2d\n", y, -~y, ~-y, ~y);
    }
    int a = 1, b = 2;
    int c = a++ + ++b;            /* 分詞器不會選的切法，但它本身合法 */
    printf("c = %d, a = %d, b = %d\n", c, a, b);
    int y = INT_MAX - 1 + argc;   /* 執行時期才是 INT_MAX */
    printf("%d\n", -~y);
    return 0;
}
```

```
y =  3: -~y =  4, ~-y =  2, ~y = -4
y =  0: -~y =  1, ~-y = -1, ~y = -1
y = -1: -~y =  0, ~-y = -2, ~y =  0
y = -5: -~y = -4, ~-y = -6, ~y =  4
c = 4, a = 2, b = 3
-2147483648
$ clang -O0 -fsanitize=undefined tadpole.c && ./a.out 2>&1 | grep runtime
tadpole.c:16:20: runtime error: negation of -2147483648 cannot be represented in type 'int'; cast to an unsigned type to negate this value to itself
$ gcc -O0 -fsanitize=undefined tadpole.c && ./a.out 2>&1 | grep runtime
tadpole.c:16:5: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
```

邊界和 `y + 1` 一樣：`y == INT_MAX` 時 `~y` 是 `INT_MIN`，再取負溢位。有趣的是兩個 UBSan 的訊息：clang 回報「取負」，gcc 回報「`2147483647 + 1`」——gcc 在插入檢查之前就已經把 `-~y` 正規化成 `y + 1` 了。蝌蚪運算子沒有任何效能理由，只增加閱讀成本。

`a+++++b` 則常被誤認為「同一值修改兩次」的未定義行為。它其實在**詞法分析**就確定是錯：C11 6.4 §4 規定取最長的前置處理記號（maximal munch），6.4 §6 的 EXAMPLE 2 就是這個例子——切成 `a ++ ++ + b`，對 `a++` 這個非左值再做後置遞增，違反 6.5.2.4 §1 的限制條件，編譯器必須診斷：

```
$ gcc -std=c11 -c munch.c
munch.c:3:15: error: lvalue required as increment operand
$ clang -std=c11 -c munch.c
munch.c:3:15: error: expression is not assignable
```

而手動切成 `a++ + ++b`，兩個副作用作用在不同物件上，完全合法，輸出 `c = 4, a = 2, b = 3`。真正的 unsequenced UB 是 `a++ + a++`，見下一章。

## 對比與取捨

| 技巧 | 依賴的假設 | 偵測失敗的工具 | 何時不要用 |
|---|---|---|---|
| 低位元藏資料 | 對齊 + 平坦定址 + 整數表示 | `static_assert(alignof)`（部分） | CHERI、MTE/TBI 會用到那些位元的平台 |
| `ERR_PTR` | 定址空間頂端無物件 | Coccinelle、Smatch | 公開 API（使用者容易混用 NULL 慣例） |
| handle + 世代 | 槽位世代不回繞（或回繞時停用） | 查詢函式本身 | 需要長期保存原始指標的熱路徑 |
| `memcpy` / 位移讀取 | 無（可攜） | UBSan `-fsanitize=alignment` 抓轉型版 | 幾乎沒有理由不用 |
| 手寫除法公式 | 輸入範圍（窮舉驗證） | 窮舉測試 | 純量 C 程式碼（編譯器已做） |
| 單一比較範圍檢查 | `lo <= hi` | 隨機測試 + UBSan | 邊界是常數時（編譯器已做） |
| 哨兵 | 有可寫的額外一格 | — | `const` 資料、多執行緒共享 |
| Duff's device | 編譯器不向量化 | 編譯器向量化報告 | 現代編譯器上的 C 程式碼 |

## 踩雷集錦

1. **「`uintptr_t` 保證低位元對應位址」**：它只保證來回轉換相等。低位元藏資料是 ABI 層的假設，要寫在註解與 `static_assert` 裡。
2. **對 `ERR_PTR` 函式檢查 NULL，或對 NULL 函式檢查 `IS_ERR`**：雙向都會出事，而且型別系統幫不上忙。介面文件要寫明失敗慣例。
3. **以為 handle 能偵測所有過期**：世代會回繞，本章實測 65535 次重用後舊 handle 復活。
4. **相信函式名稱**：`fast_div_255_accurate` 在 1600 萬個輸入上是錯的。任何位元技巧，能窮舉就窮舉，不能就跑上億次隨機測試加邊界值。
5. **用指令數推論速度**：`search_plain` 五道指令和 `search_sentinel` 三道指令一樣快；Duff's device 指令「更少」卻慢 20 倍。只有量測算數。
6. **在不同最佳化等級下得到不同結論**：gcc 11 `-O2` 不向量化、`-O3` 會；gcc `-O1` 不合併位元組載入、clang `-O1` 會。量測報告一定要寫編譯器版本與旗標。
7. **benchmark 被編譯器刪掉**：結果陣列宣告為 `static` 又不讀，迴圈會被當死碼刪光；純函式呼叫會被提出迴圈。用外部連結的輸出、`asm volatile("" ::: "memory")` 屏障，並印出 checksum。

## 進階：再往深一層

- **有號除以常數的 magic number**：把 `magic.c` 擴充到 `int32_t`，處理向零截斷（負數要加 1 或減掉符號位），對照 `sdiv10` 的 `sarl $31` / `subl`。Hacker's Delight 第 10 章有完整推導。
- **用 `perf stat` 驗證瓶頸**：WSL2 預設沒有硬體效能計數器；在原生 Linux 上用 `perf stat -e branches,branch-misses,cycles,instructions` 跑哨兵實驗，確認「每元素一個週期、瓶頸在迴圈分支」的推論。本章因環境限制未做這項量測。
- **讀編譯器的向量化報告**：`-Rpass-analysis=loop-vectorize`（clang）與 `-fopt-info-vec-missed`（gcc）會告訴你迴圈為什麼沒向量化。面試中被問到「這段程式怎麼加速」，先說「我會先看向量化報告」比直接說「展開迴圈」高明得多。

```c
/* 進階練習起點：除數在執行時期決定、但之後不變 —— 自己實作迷你 libdivide */
struct udiv { uint64_t m; unsigned shift; };      /* 只處理 m < 2^32 的情況 */
struct udiv udiv_init(uint32_t d);               /* 用本章的 e <= 2^l 條件找 l */
static inline uint32_t udiv_do(uint32_t n, struct udiv u) { return (uint32_t) ((n * u.m) >> u.shift); }
```

## 動手練習

1. 用 `magic.c` 找出 `d = 100` 的 magic number，再寫 `uint32_t f(uint32_t x){return x/100;}` 用 `gcc -O2 -S` 對照。
2. 把 handle 範例的世代改成 8 位元，算出幾次重用後舊 handle 會復活，再用程式驗證。
3. 把 `xor_duff` 加上 `-O3 -fopt-info-vec-missed`（gcc），看它是否出現在報告中；再把它改寫成「主迴圈每次 8 個 + 尾端迴圈」（不跳進中段），看是否能被向量化。
4. 在 `range.c` 加一個 `in_range64(int64_t x, int64_t lo, int64_t hi)` 的版本，跑同樣的隨機測試。

## 本章重點整理

- 指標的低位元（對齊）、高位元（正規位址）、頂端一頁（OS 不對映）都可以挪用，但全部依賴實作定義的整數－指標轉換，要明文寫下假設。
- handle 用一次間接換來決定性的過期偵測，連 ASan 看不到的「邏輯 UAF」都能抓，但世代有限。
- `memcpy` 與位移＋OR 是讀取未對齊資料與固定位元組序的可攜寫法，現代編譯器在 `-O2` 下沒有額外成本。
- 除以常數的 magic number 可以推導：$m = \lceil 2^{32+\ell}/d \rceil$，條件 $m d - 2^{32+\ell} \le 2^{\ell}$；33 位元的情況編譯器用「減、半、加」補回。
- 效能技巧的有效性取決於編譯器版本、最佳化等級與處理器；Duff's device 在現代編譯器上會擋住向量化而慢 20 倍。量測、看組語、窮舉驗證，三者缺一不可。

## 自我檢核

- [ ] 不看筆記，從「$m/2^s$ 近似 $1/d$」出發，推導出 $e \le 2^{\ell}$ 這個條件
- [ ] 能解釋為什麼 `x / 7` 的組語比 `x / 10` 多了 `subl`/`shrl`/`addl` 三道指令
- [ ] 如果面試官問「為什麼 ASan 抓不到 handle 範例中的 `pa->size`」，你能怎麼回答？
- [ ] 能說出兩種「位元技巧比 `if` 更差」的情況（溢位 UB、擋住編譯器）
- [ ] 想一個你看過的「這樣寫比較快」的建議，說出你會用什麼實驗、在什麼條件下驗證它
- [ ] 能說明 Duff's device 為什麼不會被向量化（自然迴圈、單一入口）

## 延伸閱讀

### 論文

- **[Division by Invariant Integers using Multiplication](https://doi.org/10.1145/178243.178249)** — Torbjörn Granlund, Peter L. Montgomery，PLDI（1994）
  - **核心貢獻**：給出以乘法與位移取代除以常數的演算法與正確性證明，gcc 至今採用
  - **讀哪裡**：Section 4（無號除法）的定理與 Figure 4.1；有號部分（Section 5）可先跳過
  - **和本章的關聯**：本章 `magic.c` 的條件 $e \le 2^{\ell}$ 就是論文的定理；`d = 7` 的「33 位元」處理也在論文中

### 部落格 / 技術文章

- **[Handles are the better pointers](https://floooh.github.io/2018/06/17/handles-vs-pointers.html)** — Andre Weissflog（2018）
  - **這篇說什麼**：從遊戲引擎的經驗說明 handle + 世代的設計、與 ECS 的關係、世代溢位的處置
  - **讀哪裡**：整篇；"Memory safety" 一節對應本章 handle 實驗
- **[Integer to string conversion](https://web.archive.org/web/20260628001453/https://tia.mat.br/posts/2014/06/23/integer_to_string_conversion.html)** — Leandro Pereira（lwan 作者）
  - **這篇說什麼**：比較多種整數轉字串寫法，包括本章的兩位數查表
  - **前提**：本章「效能技巧一」
- **[Duff's device 原始郵件](https://www.lysator.liu.se/c/duffs-device.html)** — Tom Duff（1983 / 1988 重貼）
  - **為什麼值得讀**：作者本人寫下「只能以量測正當化」的警告，四十年後依然適用；短，五分鐘讀完

### 官方文件 / 原始碼

- **Linux 核心 [`include/linux/err.h`](https://github.com/torvalds/linux/blob/master/include/linux/err.h)、[`include/linux/rbtree_types.h`](https://github.com/torvalds/linux/blob/master/include/linux/rbtree_types.h)、[`include/linux/xarray.h`](https://github.com/torvalds/linux/blob/master/include/linux/xarray.h)**
  - **讀哪裡**：err.h 全檔（很短）；xarray.h 開頭描述 entry 編碼的註解
  - **能學到什麼**：本章前兩個指標手法的原版
- **[re2c 手冊：Handling the end of input](https://re2c.org/manual/manual_c.html)**
  - **讀哪裡**：sentinel 與 "sentinel with bounds checks" 兩節
  - **和本章的關聯**：哨兵在詞法分析器中的四種變體與取捨

### 書籍

- **《Hacker's Delight》（2nd ed.）** — Henry S. Warren（Addison-Wesley，2012）
  - **這本書的定位**：位元技巧的標準參考書
  - **讀哪幾章**：第 2 章（基本位元運算）、第 3 章（2 的冪邊界）、第 4 章（算術邊界與範圍檢查）、第 10 章（除以常數，有號與無號的完整推導）；出版超過 10 年，但內容是數學，不會過時

> 和 `programming/c_interview/` 的關係：無分支技巧的面試速查見 [../c_interview/26-branchless.md](../c_interview/26-branchless.md)，向量化見 [../c_interview/25-simd-vectorization.md](../c_interview/25-simd-vectorization.md)。

這一章反覆遇到同一個詞：未定義行為。`clamp_to_0` 在兩個編譯器上答案不同、`nonnull` 讓檢查消失、Duff 的跳躍合法而 `a++ + a++` 不合法——下一章把未定義行為本身攤開來看。

→ [Ch 34 未定義行為：分類、編譯器推論與偵測](./34-undefined-behavior.md)
