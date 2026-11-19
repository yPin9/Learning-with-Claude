# 練習 A — 泛型容器與 callback 工具箱

> **目標**：把 Ch 7–14 學到的 `void *`、function pointer、`const` 與間接層級、stack frame、ASan 與 GDB 拼起來，寫一個以位元組為單位、不認識元素型別的動態陣列 `vec_t`（`push`／`get`／`pop`／`sort`／`foreach`／`find`／`free`），並驗證它在 ASan 下乾淨、在 GDB 裡看得見 callback 的 stack frame。

> **環境**：gcc 11.4（`-std=c11`）、clang 14、gdb 12.1，WSL2 Ubuntu 22.04 x86-64。下面所有輸出都是實際跑出來的；位址每次執行不同的地方會註明。

## 背景與動機

C 沒有 template，也沒有內建的容器。想要一個「可以裝任何東西的陣列」，標準庫給的最接近的東西是 `qsort` 與 `bsearch`：它們不知道元素是什麼，只知道**每個元素佔幾個位元組**，以及一個由你提供的**比較函式**。Linux 核心的 `sort()`、`bsearch()`、`list_sort()`，以及 Git 的 `ALLOC_GROW` 巨集管理的動態陣列，都是同一套手法。

這個練習把那套手法完整做一遍：

- 用 `void *` 與 `elem_size` 達成「型別不可知」，並理解**為什麼內部要用 `unsigned char *` 做指標算術**（`void *` 不能做算術，見 [Ch 9](./09-void-pointer-and-callbacks.md)）。
- 用 function pointer 做 callback：比較函式、走訪函式、解構函式。這三種 callback 的**參數型別**各自藏著一個「間接層級」的陷阱（元素是 `char *` 時，callback 收到的是 `char **`）。
- 自己寫一個**穩定的 merge sort**，讓 callback 被呼叫幾十萬次，同時用 GDB 觀察 callback 的 stack frame（呼應 [Ch 11](./11-process-layout-and-abi.md) 的 frame 結構）。
- 在 ASan 下驗證沒有洩漏、越界、use-after-free，並故意寫出三種常見誤用，看 ASan 怎麼報告（呼應 [Ch 14](./14-function-call-security.md)）。

> 如果你對 function pointer 的宣告語法或 `void *` 的轉換規則不熟，先回看 [Ch 9 — void *、指標的指標、function pointer 與 qualifier](./09-void-pointer-and-callbacks.md)。

## 任務規格

### 檔案

| 檔案 | 內容 |
|---|---|
| `vec.h` | 公開介面（型別、函式宣告） |
| `vec.c` | 實作 |
| `test_vec.c` | 你自己的測試（用 `CHECK` 巨集，失敗時印出行號） |

### 介面

```c
typedef struct vec vec_t;                                         /* 不透明型別：struct 內容只在 vec.c */
typedef int  (*vec_cmp_fn)(const void *a, const void *b);        /* a、b 指向「元素」 */
typedef void (*vec_visit_fn)(void *elem, void *ctx);             /* elem 指向元素，ctx 由呼叫者提供 */
typedef void (*vec_dtor_fn)(void *elem);                         /* elem 指向元素（不是元素的「值」！） */

vec_t *vec_new(size_t elem_size, vec_dtor_fn dtor);
int    vec_push(vec_t *v, const void *elem);
void  *vec_get(const vec_t *v, size_t i);
size_t vec_len(const vec_t *v);
int    vec_pop(vec_t *v, void *out);
int    vec_sort(vec_t *v, vec_cmp_fn cmp);
void   vec_foreach(vec_t *v, vec_visit_fn fn, void *ctx);
void  *vec_find(const vec_t *v, const void *key, vec_cmp_fn cmp);
void   vec_free(vec_t *v);
```

### 行為規則

| 函式 | 規則 |
|---|---|
| `vec_new` | `elem_size == 0` 回傳 `NULL`；`dtor` 可為 `NULL` |
| `vec_push` | **複製** `*elem` 的 `elem_size` 個位元組進陣列；容量不足時加倍成長；失敗（記憶體不足、大小計算溢位）回傳 `-1`，且**原本的陣列不受影響** |
| `vec_get` | 越界回傳 `NULL`；回傳的位址在下一次 `vec_push` 之後可能失效 |
| `vec_pop` | 空陣列回 `-1`；`out != NULL` 時複製給 `out`、**不呼叫 dtor**（所有權移交）；`out == NULL` 時呼叫 `dtor` 丟棄 |
| `vec_sort` | **穩定排序**（相等的元素維持原本的相對順序）；**不得使用 `qsort`**；空陣列與單元素陣列直接成功；失敗（暫存空間不足）回 `-1`，陣列不變 |
| `vec_foreach` | 依 index 由小到大呼叫 `fn(elem, ctx)` |
| `vec_find` | 線性搜尋，回傳第一個 `cmp(elem, key) == 0` 的元素位址，找不到回傳 `NULL` |
| `vec_free` | 先對每個元素呼叫 `dtor`（若有），再釋放內部緩衝區與 `vec_t`；`v == NULL` 什麼都不做 |

### 限制

- 標準 C11（`-std=c11 -Wall -Wextra -Wpedantic` 零警告），不依賴 GNU 擴充。
- 內部不得假設元素型別；不得用 `void *` 做指標算術（那是 GNU 擴充）。
- 要在 `-fsanitize=address,undefined` 下零報錯、零洩漏。

### 效能要求

- `vec_push` 均攤 O(1)；`vec_sort` 為 O(n log n) 比較次數；100,000 個 `int` 的排序在普通筆電上不應超過一秒。

## 期望輸出範例

一組自己寫的測試通過時的輸出（`gcc -std=c11 -Wall -Wextra -Wpedantic -O2 vec.c test_vec.c`）：

```
int: push / get / sort / foreach
empty / single
growth: 1000 次 push
  (info) 第 0 個元素的位址沒有改變
strings (char *, 帶 dtor)
struct + foreach ctx
random 100000 ints vs qsort，另驗證穩定性
全部通過
```

（`info` 那行取決於 allocator：`-O2` 下 `realloc` 原地擴張所以位址沒變，在 `-O0 -fsanitize=address` 下同一支程式印出「有改變」。**測試不應該斷言位址有沒有變**，這一行只記錄。）

```
輸入：陣列 [5, 3, 9, 1, 3, 7]（int），vec_sort 搭配 cmp_int
輸出：[1, 3, 3, 5, 7, 9]
```

```
邊界輸入：對空陣列呼叫 vec_sort / vec_pop(v, NULL)；vec_new(0, NULL)
輸出：vec_sort 回傳 0 且不崩潰；vec_pop 回傳 -1；vec_new 回傳 NULL
```

```
穩定性：{"Cindy",31}, {"Alan",25}, {"Bob",40}, {"Dora",25} 依 age 排序
輸出：Alan(25), Dora(25), Cindy(31), Bob(40)   ← Alan 與 Dora 同齡，維持原順序（Alan 在前）
```

## 如果你卡住了

1. **`void *` 不能 `+`**：`v->data + i * esz` 要成立，`data` 的型別必須是 `unsigned char *`（或 `char *`）。`void *` 做算術在標準 C 不合法；gcc 與 clang 都當作 GNU 擴充（視為 `char *`）接受，只有 `-Wpedantic` 會警告，所以它會在你沒注意時溜進程式碼。
2. **callback 收到的是「元素的位址」**：元素是 `int`，`cmp` 收到 `int *`（以 `const void *` 傳入）；元素是 `char *`，`cmp` 收到 `char **`。比較字串時 `strcmp(a, b)` 會錯，要先取一層 `*(char *const *)a`。
3. **`realloc` 失敗會怎樣？** `p = realloc(p, n)` 失敗回傳 `NULL`，原本的 `p` 就丟了（洩漏）。永遠先存進暫存指標，確認成功再覆蓋。
4. **merge sort 的「穩定」在哪裡決定**：合併兩半時，當左右元素**相等**，要先取左邊的。想想 `cmp(right, left) < 0` 與 `cmp(left, right) <= 0` 哪個才會翻轉相等元素的順序。
5. **`memcpy` 的來源與目的不能重疊**：合併時如果直接在原陣列上搬，可能重疊，得用暫存緩衝區（或改用 `memmove`）。

## 實作步驟建議

### Step 1：骨架與 `vec_new`／`vec_free`

寫 `struct vec { unsigned char *data; size_t len, cap, esz; vec_dtor_fn dtor; };`。`vec_new` 檢查 `elem_size`，`vec_free` 先處理 `NULL`。這一步就可以寫測試：建立、釋放、`vec_free(NULL)`。用 ASan 跑，確認沒有洩漏。

### Step 2：`vec_push` 與成長策略

寫一個 `grow()`：容量不足時 `new_cap = cap ? cap * 2 : 4`，**先檢查乘法不會溢位**（`new_cap > SIZE_MAX / esz`），再 `realloc` 到暫存指標。`push` 用 `memcpy(data + len * esz, elem, esz)`。測試 1000 次 push 後每個值都正確。

### Step 3：`vec_get`／`vec_len`／`vec_pop`／`vec_foreach`／`vec_find`

都是一兩行的函式，重點是**邊界**與 **dtor／所有權**規則。用字串（`char *`）元素測 `pop`：帶 `out` 與不帶 `out` 兩種路徑，用 ASan 確認沒有洩漏也沒有 double free。

### Step 4：`msort`（遞迴、穩定、callback）

寫 `static void msort(unsigned char *base, unsigned char *tmp, size_t n, size_t esz, vec_cmp_fn cmp)`：分成左右兩半各自遞迴，再合併進 `tmp`，最後 `memcpy` 回 `base`。`vec_sort` 負責配置 `tmp`（`len * esz`）。測試：對照 `qsort` 的結果、驗證穩定性。

### Step 5：整合、ASan、GDB

1. 用 `-fsanitize=address,undefined` 跑全部測試。
2. 故意寫三種誤用（見下面的「用 ASan 驗證」），確認你能看懂報告。
3. 用 GDB 在 `cmp_int` 下中斷點，看 backtrace 與 frame（見下面的「用 GDB 追 callback」）。

## 用 GDB 追 callback 的 stack frame

排序時 `cmp` 被 `msort` 呼叫，而 `msort` 是遞迴的。用 `-g -O0 -fno-omit-frame-pointer` 編譯後，在 `cmp_int` 下中斷點：

```
$ gdb -q ./test_vec_g
(gdb) break cmp_int
(gdb) run
Breakpoint 1, cmp_int (a=0x55555555b2e8, b=0x55555555b2e4) at test_vec.c:30
(gdb) bt
#0  cmp_int (a=0x55555555b2e8, b=0x55555555b2e4) at test_vec.c:30
#1  0x000055555555568b in msort (base=0x55555555b2e4 "\003", tmp=0x55555555b310 "", n=2, esz=4, cmp=0x555555555a34 <cmp_int>) at vec.c:90
#2  0x000055555555563b in msort (base=0x55555555b2e0 "\005", tmp=0x55555555b310 "", n=3, esz=4, cmp=0x555555555a34 <cmp_int>) at vec.c:85
#3  0x0000555555555605 in msort (base=0x55555555b2e0 "\005", tmp=0x55555555b310 "", n=6, esz=4, cmp=0x555555555a34 <cmp_int>) at vec.c:84
#4  0x000055555555586e in vec_sort (v=0x55555555b2b0, cmp=0x555555555a34 <cmp_int>) at vec.c:109
#5  0x0000555555555d24 in test_int_basic () at test_vec.c:73
#6  0x0000555555556ce5 in main () at test_vec.c:201
```

這張 backtrace 把整個設計攤開來看：

- **callback 就是一次普通的呼叫**：`cmp=0x555555555a34 <cmp_int>` 是 function pointer 的值，gdb 還貼心地標出符號名。從 `msort` 的角度，`cmp(...)` 是 [Ch 11](./11-process-layout-and-abi.md) 的 `call *%rax`（間接呼叫），`cmp_int` 的 frame 和任何函式一樣。
- **遞迴深度是 `log2(n)`**：n = 6 時，`msort` 的 n 依序是 6 → 3 → 2，在 frame #3、#2、#1。10⁶ 個元素也只有約 20 層，所以 merge sort 的遞迴**不會**造成 stack 問題（對照 [Ch 13](./13-recursion.md)：深度小才是分治遞迴的好處）。
- `base` 的值每層不同：`0x...b2e0`（整個陣列）、`0x...b2e4`（從第 1 個元素開始），差 4 = `sizeof(int)`。

```
(gdb) info frame
Stack level 0, frame at 0x7fffffffdf70:
 rip = 0x555555555a44 in cmp_int (test_vec.c:30); saved rip = 0x55555555568b
 called by frame at 0x7fffffffdfd0
 Saved registers:
  rbp at 0x7fffffffdf60, rip at 0x7fffffffdf68
(gdb) p *(int *)a
$3 = 9
(gdb) p *(int *)b
$4 = 3
(gdb) frame 1
(gdb) info locals
mid = 1
i = 0
j = 1
k = 0
(gdb) x/6gx $rsp
0x7fffffffdf60:	0x00007fffffffdfc0	0x000055555555568b
0x7fffffffdf70:	0x0000000000000000	0x0000555555555a34
(gdb) finish
Value returned is $5 = 1
```

對照：`saved rip = 0x55555555568b` 就是 backtrace #1 那一行的位址，也是 `x/6gx $rsp` 第二個 qword（`rbp` 在 `0x...df60` 存著舊的 rbp `0x7fffffffdfc0`，下一個位置是返回位址）。`a` 指向 `9`、`b` 指向 `3`，所以 `cmp_int` 回傳 `1`：左邊（`b` 這邊的元素 3）不應該被右邊（9）取代，與「相等或右邊較大時取左邊」的穩定性規則一致（`msort` 呼叫的順序是 `cmp(右, 左)`，所以第一個參數是右邊元素）。

**練習**：在 `cmp_int` 裡加上 `condition`，例如 `break cmp_int if *(int *)a == 7`，再 `bt` 看是在哪一層合併時比較到 7。

## 用 ASan 驗證

正確的實作應該在 `-fsanitize=address,undefined` 下乾淨通過：

```
$ gcc -std=c11 -Wall -Wextra -g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer vec.c test_vec.c -o test_vec_asan
$ ./test_vec_asan
...
全部通過
```

接著故意寫四種誤用（`bad.c`，用 `./bad_asan MODE` 選擇），確認你**認得**它們的報告。每一種都是真實專案裡反覆出現的錯：

**1. 忘了 `vec_free`（`leak`）**

```
==32887==ERROR: LeakSanitizer: detected memory leaks
Direct leak of 40 byte(s) in 1 object(s) allocated from:
    #1 ... in vec_new vec.c:20
    #2 ... in main bad.c:17
Indirect leak of 16 byte(s) in 1 object(s) allocated from:
    #1 ... in grow vec.c:38
    #2 ... in vec_push vec.c:48
SUMMARY: AddressSanitizer: 56 byte(s) leaked in 2 allocation(s).
```

`Direct` 是 `vec_t` 本身（`sizeof(struct vec)` = 40），`Indirect` 是它指向的緩衝區（4 個 `int` = 16 bytes）。「間接洩漏」就是被直接洩漏的物件所指向的記憶體。

**2. 保存 `vec_get` 的位址，之後又 `push`（`stale`）**

```c
int *p = vec_get(v, 0);                 /* 保存元素位址 */
for (int i = 0; i < 100; i++) vec_push(v, &i);   /* realloc 搬家 */
printf("*p = %d\n", *p);                /* use-after-free */
```

```
==32891==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000010 ...
READ of size 4 at 0x502000000010 thread T0
    #0 ... in main bad.c:28
0x502000000010 is located 0 bytes inside of 16-byte region [0x502000000010,0x502000000020)
freed by thread T0 here:
    #0 ... in __interceptor_realloc
    #1 ... in grow vec.c:38
```

注意：**被「釋放」的不是 `free` 呼叫，是 `realloc`**。`realloc` 在需要搬家時等於 malloc 新的、拷貝、free 舊的。這正是介面註解寫「位址在下一次 `push` 之後可能失效」的原因。在普通編譯下這個 bug **不一定現形**（測試時 `realloc` 常常原地擴張），上線後才爆，ASan 把它變成確定的錯誤。

**3. 解構函式寫成 `free(elem)`（`dtor`）**

```c
static void wrong_dtor(void *elem) { free(elem); }   /* 錯：elem 是「裝指標的那格」的位址 */
```

```
==32894==ERROR: AddressSanitizer: attempting double-free on 0x503000000040 in thread T0:
    #1 ... in vec_free vec.c:137
0x503000000040 is located 0 bytes inside of 32-byte region [0x503000000040,0x503000000060)
freed by thread T0 here:
    #1 ... in wrong_dtor bad.c:10
    #2 ... in vec_free vec.c:136
```

對 `elem` 呼叫 `free`，等於對 **vec 的資料緩衝區本身**（第 0 個元素的位址就是緩衝區起點）呼叫 `free`；之後 `vec_free` 又 `free(v->data)`，double free。正確的 dtor 是 `free(*(char **)elem)`：先取一層，拿到字串指標，再 free 它。這是 `void *` 泛型容器最常見的錯誤，「元素的位址」與「元素的值」差一層間接。

**4. 比較函式少了一層間接（`cmp`）**

```c
vec_sort(v, (vec_cmp_fn)strcmp);        /* 強制轉型：編譯器不吭聲 */
```

```
sorted: b a
```

**沒有任何診斷**，只是排序結果錯了（`"b"`、`"a"` 沒被排好）：`strcmp` 收到的是 `char **` 當作 `char *`，把「指標變數本身的位元組」當字串比較。這類錯誤 ASan 與 UBSan 都抓不到（`gcc` 的 UBSan 也沒有檢查函式型別是否相符），只能靠測試與 code review。強制轉型 `(vec_cmp_fn)strcmp` 是危險信號：把 function pointer 轉成不相容的型別再呼叫，在 C 標準是未定義行為（N1570 6.3.2.3p8）。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作（vec.h、vec.c、test_vec.c）</summary>

`vec.h`：

```c
#ifndef VEC_H
#define VEC_H

#include <stddef.h>

/* 泛型動態陣列：元素以「位元組複製」方式存放，元素大小在建立時決定。 */
typedef struct vec vec_t;

typedef int  (*vec_cmp_fn)(const void *a, const void *b);        /* a、b 指向「元素」 */
typedef void (*vec_visit_fn)(void *elem, void *ctx);             /* elem 指向元素，ctx 由呼叫者提供 */
typedef void (*vec_dtor_fn)(void *elem);                         /* elem 指向元素（不是元素的值！） */

/* 建立。elem_size 必須 > 0；dtor 可為 NULL。失敗回傳 NULL。 */
vec_t *vec_new(size_t elem_size, vec_dtor_fn dtor);

/* 附加一個元素（複製 *elem 的 elem_size 個位元組）。成功回傳 0，失敗回傳 -1（原本的陣列不受影響）。 */
int vec_push(vec_t *v, const void *elem);

/* 取第 i 個元素的位址；越界回傳 NULL。位址在下一次 vec_push 之後可能失效。 */
void *vec_get(const vec_t *v, size_t i);

size_t vec_len(const vec_t *v);

/* 移除最後一個元素。out 非 NULL：複製給 out（所有權交給呼叫者，不呼叫 dtor）；
 * out 為 NULL：呼叫 dtor 丟棄。空陣列回傳 -1。 */
int vec_pop(vec_t *v, void *out);

/* 穩定排序（merge sort，需要 len * elem_size 的暫存空間）。成功 0，記憶體不足 -1（陣列不變）。 */
int vec_sort(vec_t *v, vec_cmp_fn cmp);

/* 對每個元素呼叫 fn(elem, ctx)，依序從 index 0 到 len-1。 */
void vec_foreach(vec_t *v, vec_visit_fn fn, void *ctx);

/* 線性搜尋：回傳第一個 cmp(elem, key) == 0 的元素位址，找不到回傳 NULL。 */
void *vec_find(const vec_t *v, const void *key, vec_cmp_fn cmp);

/* 釋放：先對每個元素呼叫 dtor（若有），再釋放內部緩衝區與 vec 本身。v 為 NULL 時什麼都不做。 */
void vec_free(vec_t *v);

#endif
```

`vec.c`：

```c
#include "vec.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

struct vec {
    unsigned char *data;      /* 用 unsigned char * 才能做以位元組為單位的指標算術（void * 不行，見 Ch 9） */
    size_t len, cap, esz;
    vec_dtor_fn dtor;
};

vec_t *vec_new(size_t elem_size, vec_dtor_fn dtor)
{
    if (elem_size == 0) {
        errno = EINVAL;
        return NULL;
    }
    vec_t *v = malloc(sizeof *v);
    if (!v)
        return NULL;
    v->data = NULL;
    v->len = v->cap = 0;
    v->esz = elem_size;
    v->dtor = dtor;
    return v;
}

/* 確保至少還能放一個元素。容量加倍，並檢查 cap * 2 * esz 不會溢位。 */
static int grow(vec_t *v)
{
    if (v->len < v->cap)
        return 0;
    size_t new_cap = v->cap ? v->cap * 2 : 4;          /* 4：第一次配置的起始容量，只是個經驗值 */
    if (v->cap > SIZE_MAX / 2 || new_cap > SIZE_MAX / v->esz)
        return -1;
    unsigned char *p = realloc(v->data, new_cap * v->esz);   /* 先放進暫存指標：失敗時舊的 data 還在 */
    if (!p)
        return -1;
    v->data = p;
    v->cap = new_cap;
    return 0;
}

int vec_push(vec_t *v, const void *elem)
{
    if (grow(v) < 0)
        return -1;
    memcpy(v->data + v->len * v->esz, elem, v->esz);
    v->len++;
    return 0;
}

void *vec_get(const vec_t *v, size_t i)
{
    return i < v->len ? v->data + i * v->esz : NULL;
}

size_t vec_len(const vec_t *v)
{
    return v->len;
}

int vec_pop(vec_t *v, void *out)
{
    if (v->len == 0)
        return -1;
    void *last = v->data + (v->len - 1) * v->esz;
    if (out)
        memcpy(out, last, v->esz);
    else if (v->dtor)
        v->dtor(last);
    v->len--;
    return 0;
}

/* ---- 穩定的 merge sort：對 base[0..n) 排序，tmp 至少能放 n 個元素 ---- */
static void msort(unsigned char *base, unsigned char *tmp, size_t n, size_t esz, vec_cmp_fn cmp)
{
    if (n < 2)
        return;
    size_t mid = n / 2;
    msort(base, tmp, mid, esz, cmp);
    msort(base + mid * esz, tmp, n - mid, esz, cmp);

    size_t i = 0, j = mid, k = 0;
    while (i < mid && j < n) {
        /* 只有「右邊嚴格小於左邊」才先取右邊；相等時取左邊 -> 穩定 */
        if (cmp(base + j * esz, base + i * esz) < 0)
            memcpy(tmp + k++ * esz, base + j++ * esz, esz);
        else
            memcpy(tmp + k++ * esz, base + i++ * esz, esz);
    }
    if (i < mid)
        memcpy(tmp + k * esz, base + i * esz, (mid - i) * esz);
    if (j < n)
        memcpy(tmp + k * esz, base + j * esz, (n - j) * esz);
    memcpy(base, tmp, n * esz);
}

int vec_sort(vec_t *v, vec_cmp_fn cmp)
{
    if (v->len < 2)
        return 0;
    unsigned char *tmp = malloc(v->len * v->esz);
    if (!tmp)
        return -1;
    msort(v->data, tmp, v->len, v->esz, cmp);
    free(tmp);
    return 0;
}

void vec_foreach(vec_t *v, vec_visit_fn fn, void *ctx)
{
    for (size_t i = 0; i < v->len; i++)
        fn(v->data + i * v->esz, ctx);
}

void *vec_find(const vec_t *v, const void *key, vec_cmp_fn cmp)
{
    for (size_t i = 0; i < v->len; i++) {
        void *e = v->data + i * v->esz;
        if (cmp(e, key) == 0)
            return e;
    }
    return NULL;
}

void vec_free(vec_t *v)
{
    if (!v)
        return;
    if (v->dtor)
        for (size_t i = 0; i < v->len; i++)
            v->dtor(v->data + i * v->esz);
    free(v->data);
    free(v);
}
```

`test_vec.c`：

```c
#include "vec.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);         \
            failures++;                                                      \
        }                                                                    \
    } while (0)

/* strdup 是 POSIX（C23 才進標準），-std=c11 下不可用，自己寫一個 */
static char *dup_str(const char *s)
{
    size_t n = strlen(s) + 1;
    char *p = malloc(n);
    if (p)
        memcpy(p, s, n);
    return p;
}

static int cmp_int(const void *a, const void *b)
{
    int x = *(const int *)a, y = *(const int *)b;
    return (x > y) - (x < y);                 /* 不用 x - y：會溢位 */
}

static int cmp_str(const void *a, const void *b)
{
    return strcmp(*(char *const *)a, *(char *const *)b);   /* 元素是 char *，所以 a 是 char ** */
}

static void free_str(void *elem)
{
    free(*(char **)elem);                     /* elem 指向「裝著指標的那格」，不是字串本身 */
}

struct person { char name[16]; int age; };

static int cmp_person_age(const void *a, const void *b)
{
    return ((const struct person *)a)->age - ((const struct person *)b)->age;
}

static void sum_ages(void *elem, void *ctx)
{
    *(long *)ctx += ((struct person *)elem)->age;
}

struct pair { int key; int seq; };
static int cmp_pair_key(const void *a, const void *b)
{
    return cmp_int(&((const struct pair *)a)->key, &((const struct pair *)b)->key);
}

static void test_int_basic(void)
{
    puts("int: push / get / sort / foreach");
    vec_t *v = vec_new(sizeof(int), NULL);
    int in[] = { 5, 3, 9, 1, 3, 7 };
    for (size_t i = 0; i < sizeof in / sizeof in[0]; i++)
        CHECK(vec_push(v, &in[i]) == 0);
    CHECK(vec_len(v) == 6);
    CHECK(*(int *)vec_get(v, 2) == 9);
    CHECK(vec_get(v, 6) == NULL);              /* 越界 */
    CHECK(vec_sort(v, cmp_int) == 0);
    int want[] = { 1, 3, 3, 5, 7, 9 };
    for (size_t i = 0; i < 6; i++)
        CHECK(*(int *)vec_get(v, i) == want[i]);
    vec_free(v);
}

static void test_empty_and_single(void)
{
    puts("empty / single");
    vec_t *v = vec_new(sizeof(int), NULL);
    CHECK(vec_len(v) == 0);
    CHECK(vec_sort(v, cmp_int) == 0);          /* 空陣列排序不應崩潰 */
    CHECK(vec_pop(v, NULL) == -1);
    int x = 42;
    vec_push(v, &x);
    CHECK(vec_sort(v, cmp_int) == 0);
    CHECK(*(int *)vec_get(v, 0) == 42);
    int out = 0;
    CHECK(vec_pop(v, &out) == 0 && out == 42);
    CHECK(vec_len(v) == 0);
    vec_free(v);
    vec_free(NULL);                            /* free(NULL) 風格：合法 */
    errno = 0;
    CHECK(vec_new(0, NULL) == NULL);
}

static void test_growth_pointer_invalidation(void)
{
    puts("growth: 1000 次 push");
    vec_t *v = vec_new(sizeof(int), NULL);
    int first = 0;
    vec_push(v, &first);
    void *old = vec_get(v, 0);
    int moved = 0;
    for (int i = 1; i < 1000; i++) {
        vec_push(v, &i);
        if (vec_get(v, 0) != old)
            moved = 1;
    }
    CHECK(vec_len(v) == 1000);
    /* realloc 可能原地擴張、也可能搬家，行為取決於 allocator 與當時的 heap 狀態，所以只記錄、不斷言 */
    printf("  (info) 第 0 個元素的位址%s改變\n", moved ? "有" : "沒有");
    for (int i = 1; i < 1000; i++)
        CHECK(*(int *)vec_get(v, (size_t)i) == i);
    vec_free(v);
}

static void test_strings(void)
{
    puts("strings (char *, 帶 dtor)");
    vec_t *v = vec_new(sizeof(char *), free_str);
    const char *src[] = { "pear", "apple", "fig", "banana", "apple" };
    for (size_t i = 0; i < 5; i++) {
        char *s = dup_str(src[i]);              /* vec 保存的是指標，字串本身由我們配置、由 dtor 釋放 */
        vec_push(v, &s);
    }
    vec_sort(v, cmp_str);
    const char *want[] = { "apple", "apple", "banana", "fig", "pear" };
    for (size_t i = 0; i < 5; i++)
        CHECK(strcmp(*(char **)vec_get(v, i), want[i]) == 0);
    char *key = "fig";
    char **hit = vec_find(v, &key, cmp_str);   /* key 的型別也是 char *，所以傳 &key */
    CHECK(hit && strcmp(*hit, "fig") == 0);
    char *miss = "kiwi";
    CHECK(vec_find(v, &miss, cmp_str) == NULL);
    char *popped = NULL;
    CHECK(vec_pop(v, &popped) == 0 && strcmp(popped, "pear") == 0);
    free(popped);                              /* pop 帶 out：所有權交給我們 */
    CHECK(vec_pop(v, NULL) == 0);              /* pop 不帶 out：由 dtor 釋放 "fig" */
    vec_free(v);                               /* 剩下 3 個由 dtor 釋放 */
}

static void test_struct_and_ctx(void)
{
    puts("struct + foreach ctx");
    vec_t *v = vec_new(sizeof(struct person), NULL);
    struct person ps[] = { {"Cindy", 31}, {"Alan", 25}, {"Bob", 40}, {"Dora", 25} };
    for (size_t i = 0; i < 4; i++)
        vec_push(v, &ps[i]);
    vec_sort(v, cmp_person_age);
    CHECK(strcmp(((struct person *)vec_get(v, 0))->name, "Alan") == 0);   /* 25，先進先出 */
    CHECK(strcmp(((struct person *)vec_get(v, 1))->name, "Dora") == 0);   /* 25，穩定排序 */
    CHECK(((struct person *)vec_get(v, 3))->age == 40);
    long sum = 0;
    vec_foreach(v, sum_ages, &sum);
    CHECK(sum == 121);
    vec_free(v);
}

static void test_random_vs_qsort(void)
{
    puts("random 100000 ints vs qsort，另驗證穩定性");
    enum { N = 100000 };
    int *ref = malloc(N * sizeof *ref);
    vec_t *v = vec_new(sizeof(int), NULL);
    srand(12345);
    for (int i = 0; i < N; i++) {
        int x = rand() % 1000 - 500;           /* 範圍小，會有大量重複值 */
        ref[i] = x;
        vec_push(v, &x);
    }
    qsort(ref, N, sizeof *ref, cmp_int);
    CHECK(vec_sort(v, cmp_int) == 0);
    int same = 1;
    for (int i = 0; i < N; i++)
        if (*(int *)vec_get(v, (size_t)i) != ref[i]) { same = 0; break; }
    CHECK(same);
    free(ref);
    vec_free(v);

    vec_t *p = vec_new(sizeof(struct pair), NULL);
    for (int i = 0; i < N; i++) {
        struct pair e = { rand() % 50, i };
        vec_push(p, &e);
    }
    vec_sort(p, cmp_pair_key);
    int stable = 1;
    for (size_t i = 1; i < vec_len(p); i++) {
        struct pair *a = vec_get(p, i - 1), *b = vec_get(p, i);
        if (a->key > b->key || (a->key == b->key && a->seq > b->seq)) { stable = 0; break; }
    }
    CHECK(stable);
    vec_free(p);
}

int main(void)
{
    test_int_basic();
    test_empty_and_single();
    test_growth_pointer_invalidation();
    test_strings();
    test_struct_and_ctx();
    test_random_vs_qsort();
    if (failures)
        printf("%d 項失敗\n", failures);
    else
        puts("全部通過");
    return failures != 0;
}
```

**解答說明**：

- **`vec_t` 是不透明型別**：`vec.h` 只宣告 `typedef struct vec vec_t;`，`struct vec` 的欄位只在 `vec.c`。使用者改不了 `len`、`cap`，之後改內部實作（例如加 `capacity` 縮小策略）也不會破壞呼叫端；這是 C 做封裝的標準手法（[Ch 29](./29-oop-in-c.md) 會深入）。
- **`unsigned char *data`**：位元組為單位的指標算術。所有位置計算都是 `data + i * esz`，這個 `i * esz` 若溢位會算出錯誤位址，所以在成長時就用 `new_cap > SIZE_MAX / esz` 擋住（與 `calloc` 檢查 `n * size` 溢位是同一件事，見 [Ch 14](./14-function-call-security.md)）。
- **`realloc` 先存暫存指標**：`realloc` 失敗時舊緩衝區仍然有效，直接 `v->data = realloc(v->data, ...)` 會在失敗時丟失原指標並洩漏。
- **merge sort 的穩定性只取決於一行**：`cmp(右, 左) < 0` 才取右。如果寫成 `cmp(左, 右) > 0`，語意相同；如果寫成 `cmp(左, 右) <= 0` 取左、其餘取右，同樣穩定；**寫成 `cmp(右, 左) <= 0` 取右就不穩定了**（相等時右邊跑到前面）。
- **為什麼用 merge sort 而不是 quicksort**：quicksort 不穩定，且最壞情況 O(n²)；merge sort 穩定、最壞也是 O(n log n)，代價是 O(n) 的暫存空間。`qsort` 的 glibc 實作（2.35）就我所知也是以 merge sort 為主（記憶體不足時退回 quicksort），所以實務上 `qsort` 常常是穩定的，**但標準沒有保證**，不能依賴。
- **測試中的 `cmp_person_age` 用減法**：年齡是小的非負整數，減法安全；一般整數**不能**用 `x - y`（`INT_MIN - 1` 溢位，[Ch 22](./22-integer-overflow.md)），所以 `cmp_int` 用 `(x > y) - (x < y)`。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| `int` 陣列 `[5,3,9,1,3,7]`，排序 | `[1,3,3,5,7,9]` | 正常情況，含重複值 |
| 空陣列：`vec_sort`、`vec_pop(v, NULL)` | `0`、`-1`，不崩潰 | 邊界：長度 0 |
| 單元素陣列 `[42]` 排序、`pop` | 不變、`out == 42` | 邊界：長度 1 |
| `vec_new(0, NULL)` | `NULL`（`errno == EINVAL`） | 非法輸入 |
| 連續 1000 次 `push` | 每個值都能 `get` 到；`get(1000)` 為 `NULL` | 成長與越界 |
| 字串 `["pear","apple","fig","banana","apple"]` 排序 | `["apple","apple","banana","fig","pear"]` | 元素是 `char *`、callback 多一層間接 |
| `find "fig"`、`find "kiwi"` | 找到位址、`NULL` | `key` 也是元素型別 |
| `pop(v, &out)` 與 `pop(v, NULL)` | 前者移交所有權、後者呼叫 dtor | 所有權規則；ASan 無洩漏、無 double free |
| 4 個 `struct person`，依年齡排序 | 同齡者維持原順序 | 穩定性 |
| `foreach` 搭配 `ctx` 累加年齡 | `121` | 以 `ctx` 回傳結果 |
| 100,000 個 `int`（範圍 `[-500,500)`）對照 `qsort` | 結果完全相同 | 效能與正確性 |
| 100,000 個 `(key, seq)` 依 `key` 排序 | 同 `key` 的 `seq` 遞增 | 大量重複鍵下的穩定性 |

## 延伸挑戰（加分）

- **`vec_bsearch`**：對已排序的陣列做二分搜尋（`O(log n)`），介面與 `vec_find` 相同；注意 `key` 與元素同型別。
- **泛型 `vec_map`**：`vec_t *vec_map(const vec_t *src, size_t out_esz, void (*fn)(const void *in, void *out))`，產生一個新陣列；思考 `dtor` 該怎麼傳。
- **`vec_insert`／`vec_remove`**：在任意位置插入／刪除，用 `memmove`（會重疊！）；分析為什麼它們是 O(n)。
- **縮容策略**：`pop` 到只剩四分之一容量時縮小一半；為什麼不是「少於一半就縮」？（提示：反覆在邊界 push/pop 會怎樣。）
- **失敗注入**：用 `LD_PRELOAD` 或自己包一層 `xmalloc`，讓第 N 次配置失敗，驗證 `vec_push` 失敗時原陣列完好，且 `vec_sort` 失敗時陣列不變。
- **改寫成不用 `memcpy`**：針對 `esz` 為 1、4、8 的情況，用 `switch` 與型別化的賦值取代 `memcpy`，用 `gcc -O2 -S` 比較兩者；好的編譯器通常已經會把固定大小的 `memcpy` 內嵌，驗證這件事。

## 自我檢核

- [ ] 能不看解答，說明為什麼 `vec_t` 內部要用 `unsigned char *` 而不是 `void *`？
- [ ] 能解釋為什麼元素是 `char *` 時，`cmp` 的參數要轉成 `char *const *`，以及 `dtor` 為什麼要 `free(*(char **)elem)`？
- [ ] 能在 GDB 的 backtrace 裡指出 callback 的 frame，並說出 `msort` 的遞迴深度為什麼是 `O(log n)`？
- [ ] 能說出 `stale`、`dtor`、`cmp` 三種誤用各自會被哪些工具抓到、哪些抓不到？
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨（例如成長倍率、暫存空間策略、錯誤處理）。

Part 2 到此結束：你已經能用指標、function pointer 與對 stack 的理解寫出泛型的容器，並用工具驗證它。下一個 Part 往下一層走：這些元素最終放在記憶體階層的哪裡、為什麼 cache 的行為決定了遍歷的快慢。

→ [Ch 15 記憶體階層與虛擬記憶體](./15-memory-hierarchy.md)
