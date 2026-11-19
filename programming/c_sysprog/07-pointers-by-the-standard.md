# Ch 7 — 規格書裡的指標：object、type、incomplete type 與 lvalue

> **目標**：用 C11 規格書（N1570）的定義重新認識指標：分清 object、type、object type／function type／incomplete type、lvalue 與 modifiable lvalue，並用這套詞彙解釋 opaque pointer、`.` 與 `->`、「為什麼這行不能寫」等日常現象。

> **對應原文**：[你所不知道的 C 語言：指標篇](https://hackmd.io/@sysprog/c-pointer) 的「回頭看 C 語言規格書」「為何有 `.` 和 `->`」「forward declaration 搭配指標的技巧」「Lvalue 的作用」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、gdb 12.1。輸出中的位址每次執行不同。

## 為什麼需要這個？

[Ch 3](./03-pointer-basics.md) 用「位址、解參考、指標算術」重建了指標的操作直覺。這套直覺夠你寫程式，但一碰到下面這些問題就不夠用：

- 為什麼 `struct oltk *p;` 可以宣告，`struct oltk obj;` 卻編不過？
- 為什麼 `++(a++)` 被編譯器罵 `lvalue required`，`++(*p)` 卻可以？
- `const int x` 被我用轉型改掉了，為什麼 `-O0` 印 12、`-O2` 印 10？
- 函式回傳區域變數的位址，gcc 為什麼把它變成 `NULL`？

這些問題的答案都不在「指標是位址」這句話裡，而在規格書對 **object**、**type**、**lifetime**、**lvalue** 的定義裡。原文作者 jserv 指出，C99 規格書中 "object" 出現 735 次、"pointer" 出現 637 次，許多教材卻只談 pointer 不談 object。這就像只學「地址」卻不知道「房子」是什麼：你能抄地址，但說不出這個地址為什麼不能住人（房子拆了，lifetime 結束）、為什麼不能估面積（incomplete type）。

本章的策略是：每個詞先給直覺，再引規格原文，最後用一段會編譯失敗或行為詭異的程式碼驗證它。

## 先建立直覺

把記憶體想成一整排編了號的格子（位元組）。格子本身沒有意義，是「怎麼解讀」賦予它意義：

```
位址:   0x1000 0x1001 0x1002 0x1003 0x1004 ...
內容:   [ 07 ] [ 00 ] [ 00 ] [ 00 ] [ 00 ] ...
         \_________ ________/
                   V
      一塊 4 位元組的儲存區 = object（物件）
      用 int 解讀 → 7；用 float 解讀 → 9.8e-45
      解讀方式 = type（型別）

指標 p 自己也是一個 object（8 位元組），裡面存的值是 0x1000，
外加「我指向的東西要用 int 解讀」這份型別資訊：

   p: [ 00 10 00 00 00 00 00 00 ]  型別 int *
            │
            └──────► 0x1000 開始的 4 bytes，當成 int
```

這張圖帶出三個本章要精確化的概念：

1. **object** 是一段儲存區，不是「變數名稱」。名稱只是找到它的方法之一。
2. **type** 決定這段儲存區有多大、怎麼解讀、能做什麼運算。指標的型別記錄的是「所指 object 的型別」。
3. 一個 object 有**生命週期**：它不是永遠在那裡。

> 如果你對 `sizeof` 和整數型別寬度還不熟，先回看 [Ch 2 — 型別與物件](./02-types-and-objects.md)。

## object：一段能表示值的儲存區

N1570 **3.15** 的定義只有一句：

> **object**: region of data storage in the execution environment, the contents of which can represent values

注意兩個關鍵字：*region of data storage*（一段儲存區）與 *represent values*（能表示值）。這和物件導向的 object 無關：C 的 object 關心的是「資料表示法」，不是「萬物皆物件」。

「能表示值」要透過型別才成立。同樣是 7，`int` 和 `float` 的位元組完全不同：

```c
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static void dump(const char *name, const void *obj, size_t n)
{
    const unsigned char *b = obj;     /* 用字元型別逐位元組讀：規格允許 */
    printf("%-10s (%zu bytes):", name, n);
    for (size_t i = 0; i < n; i++)
        printf(" %02x", b[i]);
    printf("\n");
}

int main(void)
{
    int   i = 7;
    float f = 7.0f;
    dump("(int) 7", &i, sizeof i);
    dump("(float) 7", &f, sizeof f);

    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);   /* 合法的「重新解讀」 */
    printf("float 7.0 as uint32 = 0x%08x\n", bits);
    printf("i == f ? %d\n", i == f);  /* 比較前 i 先被轉成 float */
    return 0;
}
```

```
$ gcc -Wall -Wextra -g repr.c -o repr && ./repr
(int) 7    (4 bytes): 07 00 00 00
(float) 7  (4 bytes): 00 00 e0 40
float 7.0 as uint32 = 0x40e00000
i == f ? 1
```

`i == f` 為真，是因為 `==` 先做了 usual arithmetic conversions，把 `i` 的**值**轉成 `float` 再比；兩個 object 的**表示法**（位元組）從頭到尾都不一樣。這個區分在本課會一再出現：**值的轉換**（`(float) i`）和**表示法的重新解讀**（`memcpy`、`*(uint32_t *)&f`）是兩件事。後者若透過不相容的指標型別去做，會違反 effective type 規則，[Ch 9](./09-void-pointer-and-callbacks.md) 會細談。`0x40e00000` 怎麼對應到 7.0，留給 [Ch 25 IEEE 754](./25-ieee754.md)。

`dump` 裡用 `const unsigned char *` 讀任意 object 的位元組，這是規格特許的做法：N1570 6.5 §7 列出可以存取任何 object 的 lvalue 型別，其中包含字元型別。

## 儲存期與生命週期：object 什麼時候存在

N1570 **6.2.4** 說每個 object 都有 **storage duration（儲存期）**，它決定 object 的 **lifetime（生命週期）**。C11 有四種（C99 是三種，C11 加了 thread）：

| 儲存期 | 怎麼得到 | 生命週期 | 典型位置（實作細節，非規格） |
|---|---|---|---|
| static | 檔案範圍變數、`static` 區域變數、字串常量 | 整個程式執行期間，啟動前初始化一次 | `.data`／`.bss`／`.rodata` |
| thread | `_Thread_local` | 整條執行緒 | TLS 區塊 |
| automatic | 區塊內一般區域變數、多數 compound literal | 進入區塊到區塊結束 | stack 或暫存器 |
| allocated | `malloc` 家族 | `malloc` 到 `free` | heap |

6.2.4 §2 是整章最重要的一段之一：

> The lifetime of an object is the portion of program execution during which storage is guaranteed to be reserved for it. An object exists, has a constant address, and retains its last-stored value throughout its lifetime. If an object is referred to outside of its lifetime, the behavior is undefined. The value of a pointer becomes indeterminate when the object it points to (or just past) reaches the end of its lifetime.

最後一句很容易被忽略：object 死掉時，**指向它的指標的值**變成 indeterminate（不確定）。不只是「不能解參考」，連拿這個指標值去比較、印出，嚴格說都不再有保證。原文也提醒：中文的「初始化」聽起來像盤古開天，但 initialize 的英文意義很窄，只是「設定起始值」；object 的誕生（lifetime 開始）和它被初始化是兩件事。

用一支程式看兩種「在 lifetime 之外使用」：

```c
#include <stdio.h>
#include <stdlib.h>

static int *dangling_local(void)
{
    int local = 42;        /* automatic storage duration */
    return &local;         /* local 的生命週期在 return 時結束 */
}

int main(void)
{
    int *p = dangling_local();
    printf("p = %p\n", (void *) p);

    int *h = malloc(sizeof *h);   /* allocated storage duration */
    *h = 7;
    free(h);                      /* 生命週期結束；h 的值變成 indeterminate */
    printf("*h after free = %d\n", *h);   /* UB: use-after-free */
    return 0;
}
```

```
$ gcc -Wall -Wextra -g lifetime.c -o lifetime && ./lifetime
lifetime.c: In function ‘dangling_local’:
lifetime.c:7:12: warning: function returns address of local variable [-Wreturn-local-addr]
    7 |     return &local;         /* local 的生命週期在 return 時結束 */
      |            ^~~~~~
p = (nil)
*h after free = -1167624932
```

兩個現象都值得停下來看：

- **`p` 是 `(nil)`**。gcc 知道回傳的指標一離開函式就是 indeterminate，乾脆回傳 0。看 `-O0` 的組合語言就能確認，`&local` 根本沒被計算，回傳值被寫死成 0：

  ```
  dangling_local:
      ...
      movl    $42, -12(%rbp)      # local = 42
      movl    $0, %eax            # 回傳值 = 0，而不是 &local
      ...
      ret
  ```

  這是 gcc 的實作選擇（讓錯誤盡早以 null 解參考的形式爆出來），不是規格要求。換一個編譯器或版本，你可能拿到一個「看起來正常」的 stack 位址，而且解參考還讀得到 42，這才是最危險的情況。

- **`*h` 印出垃圾**，程式沒有當掉。use-after-free 在一般執行下常常「看起來沒事」。開 AddressSanitizer 才抓得到：

  ```
  $ gcc -g -fsanitize=address lifetime.c -o lifetime_asan && ./lifetime_asan
  ==1673==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000010 ...
  READ of size 4 at 0x502000000010 thread T0
      #0 0x643c3a257458 in main /home/ypp/c_sysprog_lab/p2a/ch7/lifetime.c:18
  ...
  freed by thread T0 here:
      #1 0x643c3a257421 in main /home/ypp/c_sysprog_lab/p2a/ch7/lifetime.c:17
  previously allocated by thread T0 here:
      #1 0x643c3a2573d0 in main /home/ypp/c_sysprog_lab/p2a/ch7/lifetime.c:15
  ```

> ASan 的用法在 [Ch 0](./00-environment-setup.md) 有完整介紹；heap 上的 object 怎麼被配置與回收，見 [Ch 17](./17-malloc-internals.md)。

順帶一提，原文在這裡強調「C 語言永遠只有 call-by-value」。`dangling_local` 回傳的是一個**指標值**（位址的副本），不是「把 local 傳出去」；指標值被複製出去了，它指向的 object 卻留在原地死掉。這個觀念到 [Ch 9](./09-void-pointer-and-callbacks.md) 的「指標的指標」會再用一次。

## 型別的分類：object type、function type、incomplete type

N1570 **6.2.5 §1**：

> Types are partitioned into **object types** (types that describe objects) and **function types** (types that describe functions). At various points within a translation unit an object type may be **incomplete** (lacking sufficient information to determine the size of objects of that type) or **complete** (having sufficient information).

畫成一棵樹（C11 的分法；C99 把 incomplete type 列為與 object type 並列的第三類，C11 改成 object type 的一種狀態）：

```
所有型別
├── object type（描述 object，有「大小」的概念）
│   ├── complete：大小已知        int, double, struct point{...}, int[10], int *
│   └── incomplete：大小未知      void, int[], struct oltk;（只有宣告）
│        ├── void：永遠無法補完（6.2.5 §19）
│        ├── 未知長度的陣列：之後宣告長度可補完
│        └── 未知內容的 struct/union：之後給出定義可補完
└── function type（描述函式，沒有 sizeof 可言）  int (int, char *)
```

幾個在後面章節會反覆用到的術語，也都在 6.2.5：

| 術語 | 條款 | 意義 |
|---|---|---|
| scalar type | §21 | 算術型別 + 指標型別。`if`、`!`、`&&`、`++` 都作用在 scalar 上，所以 `if (p)` 合法 |
| aggregate type | §21 | 陣列 + struct |
| derived declarator types | §24 | 陣列、函式、指標三種「從別的型別衍生出來」的型別，[Ch 8](./08-arrays-functions-pointers.md) 的主角 |
| pointer type derivation | §20 | 從 referenced type `T` 衍生出 "pointer to `T`" |

§20 對指標的描述值得逐字讀：

> A pointer type may be derived from a function type or an object type, called the **referenced type**. A pointer type describes an object whose value provides a reference to an entity of the referenced type.

兩件事：指標本身是 **object**（它有大小、有位址、可以被指）；它的值「提供對某個 entity 的參照」，這個 entity 可以是 object，也可以是函式。referenced type 可以是 incomplete 的，這正是 opaque pointer 的基礎。

## incomplete type 與 forward declaration

### 能做什麼、不能做什麼

編譯器對 incomplete type 的態度很一致：**凡是需要知道大小的事都不行，只用到位址的事都可以**。故意全部寫錯一次：

```c
struct oltk;                       /* 只有宣告：incomplete type */
extern int table[];                /* 大小未知的陣列：也是 incomplete type */

struct oltk *oltk_new(void);       /* OK：指向 incomplete type 的指標 */

void demo(struct oltk *o)
{
    struct oltk obj;               /* 錯：無法配置大小未知的物件 */
    unsigned long n = sizeof(struct oltk);   /* 錯：大小未知 */
    unsigned long m = sizeof table;          /* 錯：大小未知 */
    o++;                           /* 錯：指標算術需要知道元素大小 */
    (void) obj; (void) n; (void) m;
}
```

```
$ gcc -Wall -Wextra -c incomplete.c
incomplete.c:8:17: error: storage size of ‘obj’ isn’t known
incomplete.c:9:30: error: invalid application of ‘sizeof’ to incomplete type ‘struct oltk’
incomplete.c:10:30: error: invalid application of ‘sizeof’ to incomplete type ‘int[]’
incomplete.c:11:6: error: increment of pointer to an incomplete type ‘struct oltk’
```

注意 `o++` 也錯：指標加一要前進 `sizeof(*o)` 個位元組，不知道大小就算不出來。反過來，宣告 `struct oltk *`、傳遞它、比較它、存進別的 struct，都不需要大小，所以都合法。為什麼指標本身的大小是已知的？因為在一般平台上所有 object 指標都一樣寬（x86-64 是 8 bytes），規格則要求「指向 struct 的指標彼此有相同的表示法與對齊需求」（6.2.5 §28），所以 `struct anything *` 的大小不必等到 struct 補完才知道。

### opaque pointer：用 incomplete type 隱藏實作

原文舉的例子是 Openmoko 工廠測試用的小型繪圖系統 [oltk](https://github.com/openmoko/system-test-suite/tree/master/gta02-dm2/src/oltk)。`oltk.h` 只寫 `struct oltk;` 與 `struct oltk_button;`，然後公開一組全用指標的函式：

```c
struct oltk;
struct oltk_button;
struct oltk_button *oltk_button_add(struct oltk *oltk,
                                    int x, int y, int width, int height);
```

`struct oltk` 的真正定義只出現在 `oltk.c`。使用者拿到的永遠是指標，摸不到成員。把同樣的模式寫成一個可以跑的最小版本：

```c
/* counter.h */
#ifndef COUNTER_H
#define COUNTER_H
struct counter;                          /* 不透明型別：使用者看不到成員 */
struct counter *counter_new(const char *name);
void counter_inc(struct counter *c);
int  counter_get(const struct counter *c);
void counter_free(struct counter *c);
#endif
```

```c
/* counter.c */
#include <stdlib.h>
#include <string.h>
#include "counter.h"

struct counter {                         /* 定義只在實作檔：在這裡變成 complete type */
    char name[16];
    int  value;
};

struct counter *counter_new(const char *name)
{
    struct counter *c = calloc(1, sizeof *c);
    if (c)
        strncpy(c->name, name, sizeof c->name - 1);
    return c;
}
void counter_inc(struct counter *c)       { c->value++; }
int  counter_get(const struct counter *c) { return c->value; }
void counter_free(struct counter *c)      { free(c); }
```

```c
/* main.c */
#include <stdio.h>
#include "counter.h"

int main(void)
{
    struct counter *c = counter_new("clicks");
    if (!c) return 1;
    for (int i = 0; i < 3; i++)
        counter_inc(c);
    printf("value = %d\n", counter_get(c));
    counter_free(c);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g counter.c main.c -o main && ./main
value = 3
```

使用端想偷看成員，編譯器直接擋下：

```c
/* bad.c */
#include "counter.h"
int peek(struct counter *c) { return c->value; }   /* 使用端偷看成員 */
```

```
$ gcc -Wall -Wextra -c bad.c
bad.c:2:39: error: invalid use of undefined type ‘struct counter’
```

這個設計換來兩個好處：

1. **封裝**：`struct counter` 內部想加欄位、改順序，使用端不用改原始碼。
2. **二進位相容（ABI 相容）**：使用端編譯出來的機器碼只傳遞位址，從不依賴 `sizeof(struct counter)` 或成員偏移。函式庫升級成新的 `.so`，舊的執行檔不必重新編譯。反例是把 struct 定義放在標頭檔：使用端若在 stack 上宣告 `struct counter c;`，它的大小就被烙進使用端的機器碼，函式庫一加欄位，舊執行檔就會寫爆記憶體。

為什麼不用 `void *` 當 handle 就好？因為 `void *` 會丟掉型別檢查：`counter_inc(some_file_handle)` 會安靜地編過。用 `struct counter *`，傳錯型別編譯器會警告。這個「用 incomplete struct 指標取代 `void *`」的手法，Linux 核心標頭檔大量使用（許多標頭檔開頭就是一串 `struct page;`、`struct inode;` 之類的前置宣告，目的是減少 `#include` 相依）。順帶一提，標準函式庫的 `FILE` 在概念上也是 opaque 的，但 glibc 的 `<stdio.h>` 實際上看得到完整定義，規格只要求 `FILE` 是 object type，沒要求它 incomplete。

> 把 opaque pointer 推廣成封裝、vtable 與繼承，是 [Ch 29 物件導向 C](./29-oop-in-c.md) 的主題。

### 自我參照與互相參照

struct 在它自己的 `}` 之前也是 incomplete type，所以「自己包含自己」不行，「自己指向自己」可以：

```c
struct node { int v; struct node next; };
```

```
$ gcc -c selfbad.c
selfbad.c:1:34: error: field ‘next’ has incomplete type
```

兩個 struct 互相指時，先寫一行 forward declaration：

```c
#include <stdio.h>
struct employee;                   /* 先宣告，讓 department 能指向它 */
struct department {
    const char      *name;
    struct employee *head;         /* 指向 incomplete type：OK */
};
struct employee {
    const char        *name;
    struct department *dept;       /* 互相指 */
    struct employee   *next;       /* 自我參照：struct 在自己的 } 之前也是 incomplete */
};
int main(void)
{
    struct department d = { "kernel", NULL };
    struct employee a = { "alice", &d, NULL }, b = { "bob", &d, &a };
    d.head = &b;
    for (struct employee *e = d.head; e; e = e->next)
        printf("%s @ %s\n", e->name, e->dept->name);
    return 0;
}
```

```
$ gcc -Wall -Wextra mutual.c -o mutual && ./mutual
bob @ kernel
alice @ kernel
```

`struct node *next` 這種自我參照指標，是 [Ch 18 鏈結串列](./18-linked-list-pointer-to-pointer.md) 的起點。

## lvalue：能「定位」到 object 的運算式

### 名稱的由來

lvalue 這個詞可追溯到 1960 年代的 CPL 語言：CPL 把運算式分成 left-hand mode 與 right-hand mode，指的是它出現在指定運算的左邊或右邊。C 繼承了這個分類，但規格書明確地改變了解釋。N1570 6.3.2.1 的註腳 64：

> The name "lvalue" comes originally from the assignment expression E1 = E2, in which the left operand E1 is required to be a (modifiable) lvalue. It is perhaps better considered as representing an object "**locator value**". What is sometimes called "rvalue" is in this International Standard described as the "value of an expression".

所以把 lvalue 翻成「左值」會誤導：重點不是「能不能放在等號左邊」，而是「這個運算式能不能定位到一個 object」。C 標準根本沒有正式使用 rvalue 這個詞，它叫「運算式的值」。C++ 才把 rvalue 正式納入，到 C++11 又細分成 glvalue、prvalue、xvalue，這是另一套體系。

### 規格定義

N1570 **6.3.2.1 §1**：

> An lvalue is an expression (with an object type other than void) that potentially designates an object; if an lvalue does not designate an object when it is evaluated, the behavior is undefined. ... A **modifiable lvalue** is an lvalue that does not have array type, does not have an incomplete type, does not have a const-qualified type, and if it is a structure or union, does not have any member ... with a const-qualified type.

拆成兩層：

```
運算式
├── lvalue：定位得到 object（可以取位址）
│   ├── modifiable lvalue：可以被指定、++、--
│   └── 不可修改的 lvalue：陣列、incomplete type、const 限定、含 const 成員的 struct
└── 非 lvalue：只是一個值（a + 1、a++、函式回傳值、轉型結果……）
```

故意踩每一種錯誤：

```c
int a = 10;
const int c = 1;
int arr[3];

void demo(void)
{
    int *E = &a;
    ++(*E);          /* OK：*E 是 lvalue */
    ++(a++);         /* 錯：a++ 的結果不是 lvalue */
    &(a + 1);        /* 錯：a + 1 沒有位址可取 */
    c = 2;           /* 錯：c 是 lvalue，但不是 modifiable lvalue */
    arr = 0;         /* 錯：陣列是 lvalue，但不是 modifiable lvalue */
}
```

```
$ gcc -Wall -Wextra -c lvalue_bad.c
lvalue_bad.c:9:5: error: lvalue required as increment operand
lvalue_bad.c:10:5: error: lvalue required as unary ‘&’ operand
lvalue_bad.c:11:7: error: assignment of read-only variable ‘c’
lvalue_bad.c:12:9: error: assignment to expression with array type
```

四條錯誤訊息剛好對應四種情況。`a++` 的值是「a 加一之前的值」，那是一個暫時的數，不住在任何 object 裡，沒有地方可以寫回，所以 `++` 不能作用在它上面。

### lvalue 不一定有名字

註腳 64 自己就給了例子：「if E is a unary expression that is a pointer to an object, `*E` is an lvalue that designates the object to which E points」。以 `int *E = &a;` 為例：

| 運算式 | 是不是 lvalue | 定位到哪個 object |
|---|---|---|
| `E` | 是 | 指標 object `E` 本身（8 bytes，存著 `&a`） |
| `*E` | 是 | `E` 指向的 `int` object，也就是 `a` |
| `&E` | 否 | 它是一個值（`E` 的位址），沒有 object 存著它 |
| `E + 1` | 否 | 一個算出來的指標值 |

還有幾種沒有名字的 lvalue：

```c
#include <stdio.h>
struct s { const char *a; };
static void f(struct s *p) { printf("%s\n", p->a); }

int main(void)
{
    int a = 10, b = 20, flag = 1;
    *(flag ? &a : &b) = 99;            /* 條件運算子的結果不是 lvalue，但 *(指標) 是 */
    printf("a=%d b=%d\n", a, b);

    f(&(struct s){ "hello compound literal" });   /* compound literal 是無名的 lvalue */
    int *q = (int []){ 1, 2, 3 };      /* 也能取它的位址、改它 */
    q[1] = 42;
    printf("%d %d %d\n", q[0], q[1], q[2]);
    printf("%c\n", *"xyz");            /* 字串常量也是 lvalue（陣列物件） */
    return 0;
}
```

```
$ gcc -Wall -Wextra lvalue_ok.c -o lvalue_ok && ./lvalue_ok
a=99 b=20
hello compound literal
1 42 3
x
```

- compound literal（C99）`(struct s){...}` 會建立一個無名 object，所以可以 `&`。在函式內它是 automatic storage duration，生命週期到所在區塊結束。
- 字串常量 `"xyz"` 的型別是 `char[4]`，它是一個 static storage duration 的陣列 object，所以是 lvalue；它會 decay 成指標是後話（[Ch 8](./08-arrays-functions-pointers.md)），修改它是 UB（[Ch 10](./10-strings-and-null.md)）。
- 條件運算子在 C 裡**不**產生 lvalue，這點和 C++ 不同：

```c
int a, b, flag;
void g(void) { (flag ? a : b) = 1; }
```

```
$ gcc -c cond.c
cond.c:2:31: error: lvalue required as left operand of assignment
```

同一行在 C++ 是合法的。要在 C 達到同樣效果，就是上面 `*(flag ? &a : &b) = 99` 的寫法：讓 `*` 產生 lvalue。

### lvalue conversion：lvalue 什麼時候「變成值」

6.3.2.1 §2 描述另一半：除了當 `sizeof`、`_Alignof`、單元 `&`、`++`/`--` 的運算元，或 `.` 的左運算元、指定運算的左運算元之外，一個非陣列的 lvalue 會被轉換成「它所定位的 object 裡存的值」，並且不再是 lvalue。這叫 **lvalue conversion**。

`b = a;` 的 `a` 經過 lvalue conversion 變成數值 10；`b` 是指定運算的左運算元，保持 lvalue。同一個條款的 §3、§4 是陣列與函式的對應轉換（array-to-pointer、function-to-pointer），這兩條是 [Ch 8](./08-arrays-functions-pointers.md) 與 [Ch 9](./09-void-pointer-and-callbacks.md) 的主角。三條擺在一起看：

```
6.3.2.1 §2  lvalue（非陣列）  ──► 它存的值           「讀出來」
6.3.2.1 §3  陣列運算式       ──► 指向首元素的指標   array decay
6.3.2.1 §4  function designator ► 指向函式的指標     function decay
各有例外：sizeof、& 等
```

## `.` 與 `->` 的由來

### 規格上：`->` 是 `*` 加 `.` 的縮寫

N1570 **6.5.2.3** 定義兩個成員運算子：`.` 的左運算元是 struct/union，`->` 的左運算元是指向 struct/union 的指標。註腳 96 說得更直接：如果 `&E` 是合法的指標運算式，`E.MOS` 等同 `(&E)->MOS`。反過來，`pp->x` 等同 `(*pp).x`。

那為什麼要有兩個運算子？一個實際的理由是**運算子優先序**：`.` 是後置運算子，比前置的 `*` 綁得緊，所以 `*pp.x` 會被解析成 `*(pp.x)`，你必須寫 `(*pp).x`。串起來就更醜：

```c
(*(*(*list).next).next).value      /* 只用 * 和 . */
list->next->next->value            /* 用 -> */
```

Ritchie 在 [The Development of the C Language](https://www.bell-labs.com/usr/dmr/www/chist.html) 也承認，Sethi 觀察到如果間接運算子當初設計成後置，許多巢狀的宣告與運算式都會更簡單，「but by then it was too late to change」。`->` 可以看成是對前置 `*` 這個歷史決定的補救。

### 歷史上：早期 C 的成員名稱只是「偏移量」

原文給了另一個角度：從組合語言看，`p.x` 是「`p` 的位址」加上「`x` 的偏移量」，兩者缺一不可。Ritchie 的論文描述了早期 C 對這件事有多寬鬆：

> Beguiled by the example of PL/I, early C did not tie structure pointers firmly to the structures they pointed to, and permitted programmers to write `pointer->member` almost without regard to the type of pointer; such an expression was taken uncritically as a reference to a region of memory designated by the pointer, while the member name specified only an offset and a type.

也就是說，在 K&R 第一版之前，成員名稱是全域的「偏移量 + 型別」，`->` 的語意就是「拿左邊的值當位址，加上右邊的偏移，然後存取」，左邊甚至不必是那個 struct 的指標。直到 K&R 第一版之後，C 才支援 struct 的指定、以 struct 當參數與回傳值，並把成員名稱牢牢綁定到所屬的 struct（同一篇論文的說法）。在 struct 不能整個搬動的年代，「先取得整個 `*pp` 再取成員」沒有對應的機器操作；「位址加偏移後存取」才有，`->` 把這兩步綁成一個運算子。

> 原文說「早期 `(*pp).x` 無法翻譯為機器碼」；Ritchie 的論文並沒有這樣逐字講，比較穩妥的理解是上面這段：早期 struct 不是可以整體操作的值，`->` 直接對應「位址＋偏移」這個機器層次的操作。

### 今天：兩者產生一模一樣的機器碼

```c
struct point { int x, y; };

void set_arrow(struct point *pp) { pp->y = 1; }
void set_deref(struct point *pp) { (*pp).y = 1; }
```

```
$ gcc -O0 -c arrow.c && objdump -d --no-show-raw-insn arrow.o
0000000000000000 <set_arrow>:
   0:	endbr64
   4:	push   %rbp
   5:	mov    %rsp,%rbp
   8:	mov    %rdi,-0x8(%rbp)
   c:	mov    -0x8(%rbp),%rax
  10:	movl   $0x1,0x4(%rax)
  ...
000000000000001a <set_deref>:
  1a:	endbr64
  1e:	push   %rbp
  1f:	mov    %rsp,%rbp
  22:	mov    %rdi,-0x8(%rbp)
  26:	mov    -0x8(%rbp),%rax
  2a:	movl   $0x1,0x4(%rax)
  ...
```

連 `-O0` 都一樣。關鍵那一行 `movl $0x1,0x4(%rax)` 就是「位址 `%rax` 加偏移 4（`y` 在 `x` 之後，`x` 佔 4 bytes），寫入 1」。成員名稱在機器碼裡只剩一個數字偏移量，這和早期 C 的心智模型完全一致。

C++ 另有 `.*` 和 `->*`（成員指標運算子），`->*` 不是 `->` 加 `*`，那是另一個議題，本課不談。

## 底層機制：編譯器眼中的 object = 位址 + 型別

把本章的概念收斂成一條規則：**對編譯器來說，存取一個 object 需要兩樣東西：起始位址，以及型別（決定讀寫幾個位元組、怎麼解讀、能做什麼運算）**。lvalue 就是同時攜帶這兩樣資訊的運算式。

原文的練習題把這件事推到極端：「把 32-bit 整數 `0xaa6` 寫到絕對位址 `0x67a9`」。

```c
#include <stdint.h>
#include <stdio.h>
void poke(void)
{
    *(int32_t * const) 0x67a9 = 0xaa6;   /* 嵌入式慣用法：對固定位址寫入 */
}
int main(void)
{
    puts("about to poke 0x67a9 ...");
    fflush(stdout);
    poke();
    puts("never reached on Linux user space");
    return 0;
}
```

拆開看：`0x67a9` 是整數；`(int32_t * const)` 把它轉成「指向 `int32_t` 的指標」（轉型結果不是 lvalue，那個 `const` 在這裡沒有實際作用）；`*` 作用在指標上，得到一個**定位到位址 `0x67a9`、型別 `int32_t` 的 lvalue**；於是可以被指定。編譯器產生的就是一道寫入指令：

```
$ gcc -O2 -S -o - absaddr.c
poke:
	endbr64
	movl	$2726, 26537
```

`2726` 是 `0xaa6`，`26537` 是 `0x67a9`。`movl`（l = 4 bytes）的寬度來自型別 `int32_t`。在 Linux 使用者空間跑：

```
$ ./absaddr; echo "exit=$?"
about to poke 0x67a9 ...
Segmentation fault (core dumped)
exit=139
```

這個位址在行程的虛擬位址空間裡沒有對應的映射，CPU 觸發 page fault，核心送出 SIGSEGV（139 = 128 + 11，11 是 SIGSEGV 的編號）。在沒有 MMU、`0x67a9` 是某個硬體暫存器的微控制器上，同一行就是驅動程式的日常。這也說明規格為什麼把整數轉指標列為 **implementation-defined**（6.3.2.3 §5）：結果有沒有意義，完全看平台。另外 `0x67a9` 是奇數，不滿足 `int32_t` 的 4 位元組對齊，在要求對齊的架構上還會有對齊錯誤（[Ch 16](./16-alignment-and-padding.md)）。

> 行程的虛擬位址空間長什麼樣、哪些區域有映射，是 [Ch 11](./11-process-layout-and-abi.md) 與 [Ch 15](./15-memory-hierarchy.md) 的主題。

### const 剝奪的是 lvalue 的「可修改性」

最後一個實驗把 lvalue 和 UB 連起來：

```c
#include <stdio.h>
int main(void)
{
    const int x = 10;
    int *p = (int *) &x;   /* 轉型去除 const 限定 */
    *p = 12;               /* UB：改寫以 const 定義的物件 */
    printf("x = %d, *p = %d\n", x, *p);
    return 0;
}
```

```
-- -O0
x = 12, *p = 12
-- -O2
x = 10, *p = 12
```

`-O2` 的結果看起來自相矛盾：`x` 和 `*p` 是同一個 object，卻印出不同值。原因是 N1570 **6.7.3 §6**：「If an attempt is made to modify an object defined with a const-qualified type through use of an lvalue with non-const-qualified type, the behavior is undefined.」編譯器有權假設以 `const` 定義的 `x` 永遠是 10，於是 `printf` 的第一個參數直接帶入常數 10，根本不去記憶體讀。

注意條文的措辭是「object **defined with** a const-qualified type」：關鍵在原物件是不是以 `const` 定義。如果原物件不是 `const`，只是透過 `const int *` 看它，那把 `const` 轉掉再寫入是定義良好的（風格上仍不建議）。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| struct 定義放標頭檔 | 使用端可在 stack 配置、可 inline 存取成員、無額外 heap 配置 | 改欄位就破壞 ABI；使用端可任意改成員 | 小型、穩定的值型別（`struct timespec`、`struct point`） |
| opaque pointer（incomplete struct） | 封裝、ABI 穩定、型別安全 | 每次存取都要函式呼叫；物件必須由函式庫配置（通常在 heap） | 函式庫的公開介面（`sqlite3 *`、許多 `.so` 的 handle） |
| `void *` handle | 最通用，可跨不同型別 | 失去型別檢查，傳錯不會被發現 | callback 的 `user_data`（[Ch 9](./09-void-pointer-and-callbacks.md)） |
| 整數 handle（如 fd） | 可跨行程邊界傳遞、不暴露位址 | 需要一張查表，查表本身要防越界 | 系統呼叫介面（file descriptor） |

## 踩雷集錦

1. **「object 就是變數」**：變數名稱只是 lvalue 的一種。`malloc` 回來的區塊、compound literal、字串常量都是沒有名字的 object。反過來，`register` 變數或被最佳化進暫存器的區域變數，概念上仍是 object，只是你不能取它的位址。
2. **「指標指向的東西 free 了，指標本身還是好的」**：6.2.4 §2 說指標的**值**變成 indeterminate。`free(p); if (p == q) ...` 嚴格說已經不可靠。這也是為什麼 `free(p); p = NULL;` 只保護了 `p` 這一個變數，其他指向同一塊的別名仍是懸掛指標。
3. **「`const` 變數就是常數」**：在 C 裡 `const int n = 10;` 不是 integer constant expression，`case n:` 不能用、檔案範圍的 `int a[n];` 也不行（C++ 才可以）。`const` 的意思是「不能透過這個 lvalue 修改」，不是「編譯期常數」。
4. **在參數列裡第一次提到 struct tag**：

   ```c
   void use(struct foo *p);          /* 沒有先宣告 struct foo */
   struct foo { int v; };
   void use(struct foo *p) { p->v = 1; }
   ```

   ```
   protoscope.c:1:17: warning: ‘struct foo’ declared inside parameter list will not be visible outside of this definition or declaration
   protoscope.c:5:6: error: conflicting types for ‘use’; have ‘void(struct foo *)’
   ```

   第一行的 `struct foo` 作用域只在那個原型裡，和後面定義的 `struct foo` 是兩個不同的型別。錯誤訊息裡兩邊都叫 `void(struct foo *)`，看起來一樣卻衝突，非常困惑人。解法：在原型之前加一行 `struct foo;`。
5. **把 lvalue 翻成「左值」再用字面意思推理**：`const int c` 的 `c` 是 lvalue 但不能放在等號左邊；`*"xyz"` 是 lvalue，放在等號左邊編得過，執行時卻是 UB。判斷標準永遠是「它是否定位到一個 object」與「那個 object 能否被修改」。

## 進階：再往深一層

- **effective type 與 strict aliasing**：本章說 object 要靠型別解讀。規格進一步規定，一個 object 只能用「相容的型別」或字元型別去存取（N1570 6.5 §6–§7），否則 UB，編譯器據此做別名分析。[Ch 9](./09-void-pointer-and-callbacks.md) 的 `void **` 討論會用到；完整展開見 [c_interview 的 type punning 章](../c_interview/07-type-punning-aliasing.md)。
- **pointer provenance（指標出處）**：兩個指標位元相同，就一定「指向同一個東西」嗎？編譯器的答案是否定的：指標除了位址，還帶著「它來自哪個 object」的資訊，越界算出來的指標即使數值等於另一個 object 的位址，也不能拿來存取那個 object。WG14 正以技術規範（ISO/IEC TS 6010）的形式把這套模型寫清楚。John Regehr 的〈What's the difference between an integer and a pointer?〉是很好的入門。
- **compound literal 的生命週期陷阱**：在迴圈或 `if` 區塊內建立的 compound literal，在區塊外就死了。`p = &(struct s){...};` 寫在 `if` 裡、在外面用 `p`，與回傳區域變數位址是同一類錯誤。
- **面試題**：「`int *const p` 和 `const int *p` 差在哪？」這題的標準答法要用本章的詞彙：前者讓 `p` 這個 lvalue 不可修改，後者讓 `*p` 這個 lvalue 不可修改。[Ch 9](./09-void-pointer-and-callbacks.md) 會系統化整理 qualifier 的位置。

## 動手練習

1. 把 `lifetime.c` 的 `dangling_local` 改成先把 `&local` 存進一個 `volatile int *` 區域變數再回傳，觀察 gcc 還會不會換成 0，再開 `-fsanitize=address` 加上環境變數 `ASAN_OPTIONS=detect_stack_use_after_return=1`，看能不能抓到 stack-use-after-return。
2. 在 `counter.h` 裡改用 `typedef struct counter counter_t;`，讓使用端寫 `counter_t *`。確認 `counter_t c;` 仍然編不過，並解釋為什麼 typedef 不會讓 incomplete type 變 complete。
3. 寫一個 `struct config { const int version; int value; };`，試著對整個 struct 做指定 `a = b;`，讀 gcc 的錯誤訊息，對照 6.3.2.1 §1 中「含 const 成員的 struct 不是 modifiable lvalue」那一句。
4. 用 gdb 在 `constub.c` 的 `-O2` 版本上 `disassemble main`，找出 `printf` 的第二個參數（`%esi`）是怎麼被設成 10 的。

## 本章重點整理

- object 是「一段能表示值的儲存區」；type 決定它的大小與解讀方式。值的轉換和表示法的重新解讀是兩件事。
- object 有儲存期與生命週期；生命週期結束後，指向它的指標的值變成 indeterminate。gcc 甚至會把回傳區域位址改成回傳 0。
- 型別分成 object type 與 function type；object type 可能是 incomplete。凡需要大小的操作（配置、`sizeof`、指標算術、存取成員）都不能用在 incomplete type 上，只用位址的操作都可以，這就是 opaque pointer 的原理。
- lvalue 是「定位得到 object 的運算式」，不是「放在左邊的東西」；modifiable lvalue 另外排除陣列、incomplete、const。
- `->` 等於 `*` 加 `.`，存在的理由是前置 `*` 的優先序問題與早期 C「成員＝偏移量」的歷史；今天兩者產生相同的機器碼。

## 自我檢核

- [ ] 不看筆記，用規格書的詞彙解釋為什麼 `struct oltk *p;` 合法而 `struct oltk obj;` 不合法，並說出 `o++` 為什麼也不合法。
- [ ] 如果有人在面試問你「opaque pointer 為什麼能保持 ABI 相容」，你能從「使用端機器碼依賴了哪些數字」的角度回答嗎？
- [ ] 能舉出三個沒有名字的 lvalue，以及兩個「是 lvalue 但不能被指定」的例子嗎？
- [ ] 能解釋 `constub.c` 在 `-O2` 為什麼 `x` 和 `*p` 印出不同值，並指出決定這件事的條款嗎？
- [ ] 想一個你寫過的函式庫或模組：哪些 struct 應該改成 opaque？代價是什麼？

## 延伸閱讀

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570（C11）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**（[HTML 版](http://port70.net/~nsz/c/c11/n1570.html) 較好搜尋）
  - **讀哪裡**：3.15（object）、6.2.4（storage durations）、6.2.5 §1、§19–§28（型別分類）、6.3.2.1（lvalue 與三種轉換，連同註腳 64）、6.5.2.3（成員運算子，連同註腳 96）、6.7.3 §6（const）。
  - **能學到什麼**：本章每個術語的原始定義。讀完這幾段，你會發現很多「C 的怪規則」其實是同一套定義的推論。
  - **前提**：先讀 [Ch 4 讀規格書](./04-reading-the-standard.md) 了解 constraint、semantics、UB 的分類。

### 論文

- **[The Development of the C Language](https://www.bell-labs.com/usr/dmr/www/chist.html)** — Dennis Ritchie，HOPL-II（1993）
  - **核心貢獻**：C 的設計者親述 C 如何從無型別的 BCPL、B 演化而來。
  - **讀哪裡**：「Embryonic C」講陣列與指標的關係怎麼來的；「Portability」一節的後半講早期 `pointer->member` 不檢查型別、成員名稱只是偏移量；「Critique」講 Sethi 對前置 `*` 的批評。
  - **和本章的關聯**：本章 `.` 與 `->` 的歷史段落直接引自此文。

### 部落格 / 技術文章

- **[Understanding lvalues and rvalues in C and C++](https://eli.thegreenplace.net/2011/12/15/understanding-lvalues-and-rvalues-in-c-and-c)** — Eli Bendersky（2011）
  - **這篇說什麼**：用大量小例子說明 lvalue 與 rvalue 的區分、錯誤訊息的來源，以及 C++ 對這套分類的擴充。
  - **讀哪裡**：前半段的 C 部分；C++11 rvalue reference 的段落可以跳過。
  - **為什麼值得讀**：作者寫了一系列編譯器與連結器文章，用詞精準，例子都能跑。
- **[What's the difference between an integer and a pointer?](https://blog.regehr.org/archives/1621)** — John Regehr（2018）
  - **這篇說什麼**：從編譯器最佳化的角度說明「指標不只是整數」，帶出 provenance 的概念。
  - **讀哪裡**：整篇（不長）。
  - **為什麼值得讀**：Regehr 是猶他大學教授、UB 與編譯器測試的研究者；這篇是「進階」一節 provenance 的最佳起點。前提是讀完本章與 [Ch 3](./03-pointer-basics.md)。

### 書籍

- **《The C Programming Language》2nd ed.** — Kernighan & Ritchie（Prentice Hall，1988）
  - **讀哪幾章**：第 5 章（Pointers and Arrays）與第 6 章（Structures）。6.2「Structures and Functions」示範了 `->` 的典型用法。
  - **定位**：C89 時代的書，沒有 C99 之後的 compound literal、`_Thread_local` 等內容，但指標與 struct 的基本模型至今適用。

object 與型別的詞彙有了，下一章拿它來拆解 C 最惡名昭彰的兩件事：陣列和指標為什麼「好像一樣又不一樣」，以及 `char *(*(*a[])())()` 這種宣告該怎麼讀。

→ [Ch 8 陣列、函式、指標：derived declarator 與 array decay](./08-arrays-functions-pointers.md)
