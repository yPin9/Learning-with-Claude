# Ch 32 — 技巧 I：語法與資源管理

> **目標**：認得開源專案裡反覆出現的十幾種 C 慣用法（Plan 9 嵌入、transparent union、tagged union、designated initializer、compound literal、C23 `auto`、階層式配置、藏在指標前的標頭、小緩衝區最佳化、cleanup 屬性、記憶體串流、彈性陣列成員、陣列參數、函式屬性、單成員結構強型別），每一種都能說出它靠規格書哪一條成立、編譯器實際做了什麼、在哪裡會壞掉。

> **對應原文**：[你所不知道的 C 語言：技巧篇](https://hackmd.io/@sysprog/c-trick) 的「從矩陣操作談起」「GCC 支援 Plan 9 C Extension」「GCC transparent union」「tagged union」「明確初始化特定結構的成員」「以 compound literal 模擬具名與選擇性參數」「階層式記憶體配置」「把標頭藏在指標前面」「小緩衝區最佳化」「以 cleanup 屬性綁定資源生命週期」「把記憶體當成 FILE」「C23 的 auto」「彈性陣列成員與 `__counted_by`」「可變長度陣列與陣列參數」「函式屬性」「單一成員結構體作為強型別」等節

> **環境**：WSL Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G），gcc 11.4、clang 14、glibc 2.35。C23 `auto` 以 Docker `gcc:14`（GCC 14.4.0）執行，`counted_by` 以 Docker `gcc:15`（GCC 15.3.0）執行，段落內標明。

## 為什麼需要這個？

C 語言很小。沒有建構子與解構子、沒有具名參數、沒有泛型容器、沒有 RAII、沒有「這個整數其實是 UID」的型別系統。可是 Linux 核心、systemd、Redis、SQLite 這些專案都需要這些東西，所以它們各自用 C 現有的零件拼出替代品：用 designated initializer 模擬具名參數、用 `offsetof` 把標頭藏在指標前面、用 GCC 的 cleanup 屬性做 RAII、用只有一個成員的結構體做強型別。

這些拼法第一次看到時像魔術，讀者常卡在兩個地方：一是**看不懂它為什麼合法**（`circle circle;` 怎麼能編譯？`s[-1]` 不是越界嗎？），二是**不知道它在哪裡會壞**（SDS 字串不能 `free`、小緩衝區不能淺複製、cleanup 變數不能被 `goto` 跳過）。這一章把每個技巧都拆成三件事：規格書依據、實際機器行為、失敗模式。

和其他章節的分工：X macro 與 statement expression 在 [Ch 27](./27-preprocessor-basics.md)、[Ch 28](./28-preprocessor-advanced.md)；vtable 與繼承的完整手法在 [Ch 29](./29-oop-in-c.md)；指標手法與效能技巧在下一章 [Ch 33](./33-tricks-pointers-and-performance.md)。

## 先建立直覺

把這一章的技巧想成「在 C 的骨架上外接器官」，按照它們解決的問題分成四群：

```
                ┌─────────────────────────────────────────┐
  型別與介面    │ Plan 9 嵌入 / transparent union /         │  讓「繼承」「多型」
                │ tagged union / 單成員結構強型別            │  寫起來像樣
                ├─────────────────────────────────────────┤
  初始化語法    │ designated initializer / compound literal │  讓呼叫端可讀、
                │ / 具名參數巨集 / C23 auto                 │  不怕欄位順序
                ├─────────────────────────────────────────┤
  記憶體布局    │ 階層式配置 / 標頭藏在指標前 / 小緩衝區    │  一個指標背後
                │ / 彈性陣列成員 + counted_by               │  藏更多資訊
                ├─────────────────────────────────────────┤
  資源與契約    │ cleanup 屬性 / open_memstream /           │  讓編譯器幫你
                │ 陣列參數 [static N] / format、nonnull、   │  檢查與收尾
                │ [[nodiscard]]                             │
                └─────────────────────────────────────────┘
```

共同的心智模型只有一句：**C 編譯器只知道型別與位元組，它不知道你的意圖**。每個技巧都是想辦法把意圖編碼成編譯器看得懂的東西（型別、屬性、記憶體布局），讓它替你檢查或替你做事。一旦編碼不完整（例如 `counted_by` 的長度欄位沒設好），編譯器就會依照錯誤的編碼「忠實地」做錯事。

## 型別與介面：讓 C 有一點繼承與多型

### 從矩陣說起：結構體 + 函式指標

jserv 的 [matrix_oo](https://github.com/jserv/matrix_oo) 把矩陣資料和「乘法」「比較」的函式指標放在同一個結構體裡，換一套 SIMD 實作時只改初始化的那一行。這是 C 表達多型的基本形式，完整的 vtable 設計留給 [Ch 29](./29-oop-in-c.md)。這一節關心更底層的問題：**一個「子類別」的指標要怎麼交給接受「父類別」指標的函式？**

### GCC 的 Plan 9 擴充：不具名嵌入

Plan 9 的 C 編譯器允許把一個結構體**不具名**地嵌進另一個，並且自動把「指向外層」的指標轉成「指向內嵌成員」的指標。GCC 以 `-fplan9-extensions` 支援：

```c
#include <stdio.h>

typedef struct S {
    int i;
} S;

typedef struct T {
    S;                /* 不具名嵌入：Plan 9 的「繼承」 */
    int extra;
} T;

static void bar(S *s) { printf("bar sees i = %d\n", s->i); }

int main(void)
{
    T t = { { 42 }, 7 };
    bar(&t);          /* 隱含轉換成指向內嵌 S 的指標 */
    bar(&t.S);        /* 以型別名稱存取內嵌成員 */
    printf("t.i = %d (直接存取內嵌成員)\n", t.i);
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra -fplan9-extensions plan9.c -o plan9 && ./plan9
bar sees i = 42
bar sees i = 42
t.i = 42 (直接存取內嵌成員)
```

拿掉這個旗標，同一份程式碼在 ISO C 下完全不成立。這正是理解這個技巧的最好方式：看它在各種模式下怎麼壞。

```
$ gcc -std=gnu11 -Wall -Wextra plan9.c          # 沒有任何擴充
plan9.c:8:6: warning: declaration does not declare anything
plan9.c:17:9: warning: passing argument 1 of ‘bar’ from incompatible pointer type [-Wincompatible-pointer-types]
plan9.c:18:11: error: ‘T’ has no member named ‘S’
plan9.c:19:46: error: ‘T’ has no member named ‘i’
$ gcc -std=gnu11 -Wall -Wextra -fms-extensions plan9.c   # MS 擴充：只給一半
plan9.c:17:9: warning: passing argument 1 of ‘bar’ from incompatible pointer type [-Wincompatible-pointer-types]
plan9.c:18:11: error: ‘T’ has no member named ‘S’
$ clang -std=gnu11 -fplan9-extensions plan9.c
clang: error: unknown argument: '-fplan9-extensions'
```

三種模式的差別整理成表：

| 模式 | `t.i` 直接存取 | `&t.S` | `bar(&t)` 隱含轉換 |
|---|---|---|---|
| ISO C11 | 錯誤 | 錯誤 | 只警告，實際上是重新解讀指標 |
| `-fms-extensions`（gcc、clang 都有） | 可以 | 錯誤 | 只警告，實際上是重新解讀指標 |
| `-fplan9-extensions`（只有 gcc） | 可以 | 可以 | 真正轉換到內嵌成員 |

「重新解讀指標」的意思是：`bar(&t)` 在沒有 Plan 9 擴充時，編譯器把 `T *` 原封不動當成 `S *` 傳過去。它碰巧能動，是因為 `S` 位於 `T` 的開頭；把 `S` 移到 `extra` 之後就讀錯資料。C11 6.7.2.1 §13 標準化的匿名結構體只允許**不帶 tag** 的 `struct { int i; };`，不包含「嵌入一個既有型別」這種寫法。Linux 核心和多數可攜專案因此不用它；它的價值在於讓你看清楚「繼承」在 C 裡的真面目：**一個放在開頭的成員，加上一次指標轉型**。

### transparent union：讓參數接受多種指標型別

GCC 與 Clang 都支援 [`transparent_union`](https://gcc.gnu.org/onlinedocs/gcc/Common-Type-Attributes.html) 型別屬性。函式參數宣告成帶這個屬性的 union 時，呼叫端可以直接傳 union 中任一成員型別的值，不用轉型；ABI 上則和傳第一個成員一樣。把型別標籤放在每個結構體的第一個欄位，就能做出依標籤分派的多型函式：

```c
#include <assert.h>
#include <stdio.h>

typedef enum { TYPE_A = 0, TYPE_B } GenericType;
typedef struct A { GenericType type; int a_elements; } A;
typedef struct B { GenericType type; double b_elements; } B;

typedef union GenericPtr {
    GenericType *type;
    A *A;
    B *B;
} __attribute__((__transparent_union__)) GenericPtr;

static void show(GenericPtr p)
{
    switch (*p.type) {
    case TYPE_A: printf("A %d\n", p.A->a_elements); break;
    case TYPE_B: printf("B %.1f\n", p.B->b_elements); break;
    default: assert(0);
    }
}

int main(void)
{
    A a = { TYPE_A, 10 };
    B b = { TYPE_B, 2.5 };
    show(&a);              /* A * 不用轉型 */
    show(&b);              /* B * 也不用 */
#ifdef BAD
    int x = 3;
    show(&x);              /* int * 不在 union 裡 */
#endif
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra tunion.c -o tu && ./tu
A 10
B 2.5
$ gcc -std=gnu11 -DBAD tunion.c
tunion.c:31:10: error: incompatible type for argument 1 of ‘show’
$ clang -std=gnu11 -DBAD tunion.c
tunion.c:31:10: error: passing 'int *' to parameter of incompatible type 'GenericPtr' (aka 'union GenericPtr')
```

注意這裡比 `void *` 好的地方：`void *` 什麼都收，`GenericPtr` 只收列出來的三種，傳錯型別是**編譯錯誤**。glibc 早年曾用這個屬性宣告 `wait()`，讓它同時接受 `int *` 和 BSD 的 `union wait *`。C11 之後，同類的需求多半改用標準的 `_Generic`（見 [Ch 27 的「`_Generic`」節](./27-preprocessor-basics.md)），它在編譯時期分派，而 transparent union 是在執行時期看標籤分派。

### tagged union：成員名稱與型別名稱相同

transparent union 傳的都是指標。另一種常見設計把標籤放在外層、用 C11 匿名 union 放資料，整個值大小固定，可以複製、放進陣列、以值傳遞：

```c
#include <assert.h>
#include <stdio.h>

typedef struct circle { double r; } circle;
typedef struct rect { double w, h; } rect;

typedef struct shape {
    enum { type_circle, type_rect } tag;
    union {             /* C11 匿名 union */
        circle circle;  /* 成員名稱 = 型別名稱 */
        rect rect;
    };
} shape;

#define TSWITCH(s) \
    for (shape *_s = &(s); _s; _s = NULL) switch (_s->tag)
#define MATCH(T)                     \
    case type_##T:;                  \
        assert(_s->tag == type_##T); \
        T *T = &_s->T

static double area(shape s)
{
    TSWITCH(s) {
    MATCH(circle);
#ifdef FALL
        (void) circle;    /* 忘了 return：落入下一個 case */
#else
        return 3.14159 * circle->r * circle->r;
#endif
    MATCH(rect);
        return rect->w * rect->h;
    }
    return 0;
}

int main(void)
{
    shape v[] = {
        { .tag = type_circle, .circle = { 1.0 } },
        { .tag = type_rect, .rect = { 2.0, 3.0 } },
    };
    for (int i = 0; i < 2; i++)
        printf("%g\n", area(v[i]));
    printf("sizeof(shape) = %zu\n", sizeof(shape));
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -pedantic tagged.c -o tg && ./tg     # clang 14 輸出相同、皆無警告
3.14159
6
sizeof(shape) = 24
```

這段程式碼有三個地方第一次看會覺得「這也能編？」，每個都有條款撐腰：

1. **`circle circle;` 合法**：C11 6.2.3 §1 把識別字分成四個名稱空間（name space）：label、struct/union 的 tag、每個結構體自己的成員、其他一般識別字。`struct circle` 的 `circle` 是 tag，`.circle` 是成員，typedef `circle` 是一般識別字，三者互不衝突。
2. **`T *T = &_s->T` 合法**：C11 6.2.1 §7 規定識別字的範圍從它的宣告子（declarator）**結束之後**才開始。所以讀到型別 `circle` 時它還是 typedef；初始化子裡的 `_s->circle` 是成員名。代價是從這行開始到區塊結束，typedef `circle` 被區域變數遮蔽。
3. **`case type_##T:;` 多出的分號不能省**：C11 6.8.1 規定 label 後面必須接敘述，而宣告不是敘述；空敘述 `;` 讓後面的宣告合法。C23 才允許 label 直接接宣告。

`assert` 看似多餘（`switch` 已經分派了），它防的是**前一個 case 忘了 return 而落入下一個 case**。開 `-DFALL` 實驗：

```
$ gcc -std=c11 -Wall -Wextra -DFALL tagged.c -o tg2 && ./tg2
tagged.c:25:11: warning: this statement may fall through [-Wimplicit-fallthrough=]
tg2: tagged.c:31: area: Assertion `_s->tag == type_rect' failed.
Aborted (core dumped)
```

GCC 的 `-Wimplicit-fallthrough`（`-Wextra` 已含）在編譯時期就抓到了；`assert` 是執行時期的第二道網。真實專案多半不用這種巨集，而是用「檢查標籤的存取巨集」。Lua 的每個值都是 tagged union（[`lobject.h`](https://github.com/lua/lua/blob/master/lobject.h) 的 `TValue`），讀取一律走 `#define ivalue(o) check_exp(ttisinteger(o), val_(o).i)`，除錯建置時 `check_exp` 就是 `assert`，和上例 `MATCH` 裡的 `assert` 是同一件事。

## 初始化語法：讓呼叫端說清楚自己要什麼

### designated initializer：索引寫在程式碼上

C99 的 designated initializer（C11 6.7.9 §6–§7）可以指定結構成員，也可以指定陣列索引，沒列到的一律補零。GCC 與 Clang 另外支援 `[first ... last]` 的範圍寫法：

```c
#include <stdio.h>

enum city { Taipei, Tainan, Taichung, CITY_MAX };

static const int zipcode[CITY_MAX] = {
    [Taichung] = 400,      /* 順序隨意，索引寫在程式碼上 */
    [Taipei]   = 100,
    [Tainan]   = 700,
};

static const char *lookup[] = { [0] = "Zero", [1] = "One", [4] = "Four" };

typedef void (*handler)(int);
static void h_default(int c) { printf("  default(%d)\n", c); }
static void h_digit(int c)   { printf("  digit('%c')\n", c); }

/* GNU 範圍 designator：先整段預設，再覆寫 */
static handler table[128] = {
    [0 ... 127] = h_default,
    ['0' ... '9'] = h_digit,
};

struct point { int x, y, z; };

int main(void)
{
    printf("sizeof lookup / sizeof *lookup = %zu\n", sizeof lookup / sizeof *lookup);
    for (int i = 0; i < 5; i++)
        printf("lookup[%d] = %s\n", i, lookup[i] ? lookup[i] : "(null)");
    printf("Tainan zip = %d\n", zipcode[Tainan]);
    table['7']('7');
    table['x']('x');

    struct point *p = &(struct point){ 0 };
    *p = (struct point){ .y = 5 };          /* 一次初始化，其餘補零 */
    printf("p = {%d, %d, %d}\n", p->x, p->y, p->z);
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra desig.c -o d && ./d
desig.c:20:21: warning: initialized field overwritten [-Woverride-init]
...（'0' 到 '9' 每一格各一則，共 10 則）
sizeof lookup / sizeof *lookup = 5
lookup[0] = Zero
lookup[1] = One
lookup[2] = (null)
lookup[3] = (null)
lookup[4] = Four
Tainan zip = 700
  digit('7')
  default(120)
p = {0, 5, 0}
```

三個觀察：`lookup` 沒寫長度，長度由最大索引 4 決定成 5；範圍寫法在 `-Wextra` 下對每個被覆寫的元素各警告一次（`-pedantic` 再加一則「ISO C forbids specifying range of elements」）；最後兩行示範對 `malloc` 或指標所指的結構體，可以**把 compound literal 指定給解參考的指標**，一次設定指定欄位並把其餘補零，不用一個欄位一個欄位寫。

designated initializer 也是 C 與 C++ 分道揚鑣的例子：C++20 才納入，而且要求依宣告順序、不支援陣列索引 `[2] = x`、不支援巢狀 `.a.b = x`。標頭檔若要給 C++ 用，要記得這一點。

### compound literal 模擬具名、選擇性參數

C99 的 compound literal（C11 6.5.2.5）產生一個無名物件。搭配可變參數巨集，讓呼叫看起來像有預設值的具名參數：

```c
#include <stdio.h>

struct line_opts {
    int width;
    const char *color;
    _Bool dashed;
};

static void draw_line_impl(int x0, int y0, int x1, int y1, struct line_opts o)
{
    printf("(%d,%d)-(%d,%d) width=%d color=%s dashed=%d\n",
           x0, y0, x1, y1, o.width, o.color, o.dashed);
}

/* 預設值寫在前面，呼叫端的 designator 寫在後面覆寫 */
#define draw_line(x0, y0, x1, y1, ...)                       \
    draw_line_impl(x0, y0, x1, y1,                           \
                   (struct line_opts){ .width = 1,           \
                                       .color = "black",     \
                                       __VA_ARGS__ })

int main(void)
{
    draw_line(0, 0, 10, 10);
    draw_line(0, 0, 10, 10, .color = "red");
    draw_line(0, 0, 10, 10, .dashed = 1, .width = 3);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra named.c -o n && ./n
named.c:25:38: warning: initialized field overwritten [-Woverride-init]
named.c:26:51: warning: initialized field overwritten [-Woverride-init]
(0,0)-(10,10) width=1 color=black dashed=0
(0,0)-(10,10) width=1 color=red dashed=0
(0,0)-(10,10) width=3 color=black dashed=1
$ clang -std=c11 -Wall named.c
named.c:25:38: warning: initializer overrides prior initialization of this subobject [-Winitializer-overrides]
named.c:26:51: warning: initializer overrides prior initialization of this subobject [-Winitializer-overrides]
```

依據是 C11 6.7.9 §19：初始化依列表順序進行，後出現的初始值覆寫先前針對同一子物件者。同一條的註腳 151 另有一句容易漏看：**被覆寫的初始值可能完全不被求值**，因此預設值不能帶副作用（寫成 `.width = next_id()` 就可能根本不呼叫）。診斷方面，clang 在 `-Wall` 就警告，gcc 要 `-Wextra`；採用這個手法的專案通常在巨集裡用 `_Pragma("GCC diagnostic ignored \"-Woverride-init\"")` 局部關閉。

另一種設計完全避開覆寫：約定「欄位為 0 代表用預設值」，由被呼叫端補。[sokol_gfx.h](https://github.com/floooh/sokol/blob/master/sokol_gfx.h) 全面採用這個約定，呼叫端寫 `sg_make_buffer(&(sg_buffer_desc){ .data = SG_RANGE(v) })`，實作端用 `#define _sg_def(val, def) (((val) == 0) ? (def) : (val))` 補值。限制是 0 不能同時是合法的非預設值，sokol 因此在列舉中把 0 保留為 `_SG_..._DEFAULT`。傳 compound literal 的位址是安全的：C11 6.5.2.5 §5 規定函式內的 compound literal 具有 automatic storage duration，活到所在區塊結束，涵蓋整個呼叫；**但不可以把這個位址存起來，在區塊結束後使用**。

### C23 `auto` 與舊式 `var` 巨集

C23 之前，GNU C 用 `typeof` 模擬型別推導：`#define var(left, right) __typeof__(right) left = (right)`。C17 以前的 `auto` 只是「區塊範圍物件的預設儲存類別」，寫了等於沒寫；C23 的 N3220 6.7.10（Type inference）讓 `auto x = expr;` 從初始化運算式推導型別，而且推導前要先做 **lvalue 轉換、陣列轉指標、函式轉指標**。這句話正是 `auto` 與 `typeof` 的差別：

```c
#include <stdio.h>

#define var(left, right) __typeof__(right) left = (right)
#define TYPE_OF(x) _Generic(&(x), int *: "int", const int *: "const int", \
                            int **: "int *", double *: "double",          \
                            long *: "long", int (*)[3]: "int [3]")

int main(void)
{
    const int ci = 1;
    int arr[3] = { 1, 2, 3 };

    auto a = 3.5;       /* double */
    auto b = ci;        /* int：lvalue 轉換丟掉 const */
    auto p = arr;       /* int *：陣列退化成指標 */
    auto n = 1L << 40;  /* long */
    var(c, ci);         /* const int：typeof 保留限定詞 */
    __typeof__(arr) q;  /* int [3]：typeof 保留陣列型別 */

    printf("a: %s, b: %s, p: %s, n: %s\n",
           TYPE_OF(a), TYPE_OF(b), TYPE_OF(p), TYPE_OF(n));
    printf("c: %s, q: %s\n", TYPE_OF(c), TYPE_OF(q));
    (void) q;
    return 0;
}
```

```
$ docker run --rm -v "$PWD:/w" -w /w gcc:14 sh -c 'gcc -std=c23 -Wall -Wextra -pedantic auto23.c -o a23 && ./a23'
a: double, b: int, p: int *, n: long
c: const int, q: int [3]
$ docker run ... gcc:14 gcc -std=c17 auto17.c        # auto17.c: int main(void) { auto x = 1; return x - 1; }
auto17.c:1:23: error: type defaults to 'int' in declaration of 'x' [-Wimplicit-int]
```

為什麼 `TYPE_OF` 要對 `&(x)` 分派而不是對 `x`？因為 `_Generic` 的控制運算式本身也會做 lvalue 轉換（[Ch 27](./27-preprocessor-basics.md) 講過），直接寫 `_Generic(x, ...)` 就看不到 `const`。在 C17 模式下 `auto x = 1;` 依賴「隱含 int」，C99 已經移除它，GCC 14 起直接報錯。Linux 核心目前以 `gnu11` 建置，v6.19 在 `include/linux/compiler_types.h` 加入「`__STDC_VERSION__ < 202311L` 時 `#define auto __auto_type`」，讓核心程式碼提早以 C23 的寫法使用 GCC 的 `__auto_type`（這段沿用原文的說明，未在核心原始碼中重新確認版本號）。

## 記憶體布局：讓一個指標背後藏更多資訊

### 階層式配置：從 nalloc 到 talloc

假設矩陣大小可變：結構體本身、列指標陣列、每一列資料都要分別 `malloc`，釋放時要一塊一塊 `free`，錯誤路徑上更容易漏。**階層式配置器**讓每塊記憶體記住自己的親代，釋放親代就釋放整棵子樹。jserv 的 [nalloc](https://github.com/jserv/nalloc) 是教學版，工業級實作是 Samba 的 [talloc](https://talloc.samba.org/talloc/doc/html/index.html)：Samba 用「連線」「請求」組織記憶體，請求處理完一次 `talloc_free()` 整個 context，不用追蹤其中每個字串。

```
  使用者看到的樹                  實際的連結方式 (first-child / next-sibling)

        request                          request
        /     \                             │ child
      conn   reply                        reply ── conn      (新的子節點插在最前)
       │                                            │ child
      peer                                         peer
```

下面是約 100 行、保留 talloc 主要語意的最小版本。標頭以 `alignas(max_align_t)` 的彈性陣列成員對齊資料區，使用者指標用 `offsetof` 推回標頭，和 [Ch 19 的 `container_of`](./19-linux-list-head.md) 是同一手法：

```c
#include <stdalign.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct hdr {
    struct hdr *parent, *child, *next, *prev;
    int (*dtor)(void *);
    const char *name;
    alignas(max_align_t) char data[];   /* 使用者拿到的是這裡 */
};

#define HDR(p) ((struct hdr *) ((char *) (p) - offsetof(struct hdr, data)))

static void link_under(struct hdr *h, struct hdr *parent)
{
    h->parent = parent;
    h->prev = NULL;
    h->next = parent ? parent->child : NULL;
    if (h->next)
        h->next->prev = h;
    if (parent)
        parent->child = h;
}

static void unlink_from_parent(struct hdr *h)
{
    if (h->prev)
        h->prev->next = h->next;
    else if (h->parent)
        h->parent->child = h->next;
    if (h->next)
        h->next->prev = h->prev;
}

void *h_alloc(const void *ctx, size_t size, const char *name)
{
    if (size > SIZE_MAX - sizeof(struct hdr))
        return NULL;
    struct hdr *h = calloc(1, sizeof(*h) + size);
    if (!h)
        return NULL;
    h->name = name;
    link_under(h, ctx ? HDR(ctx) : NULL);
    return h->data;
}

void h_set_destructor(void *p, int (*dtor)(void *)) { HDR(p)->dtor = dtor; }

void *h_steal(const void *new_ctx, void *p)
{
    struct hdr *h = HDR(p);
    unlink_from_parent(h);
    link_under(h, new_ctx ? HDR(new_ctx) : NULL);
    return p;
}

int h_free(void *p)
{
    if (!p)
        return -1;
    struct hdr *h = HDR(p);
    if (h->dtor && h->dtor(p) < 0)
        return -1;                       /* 解構函式否決釋放 */
    while (h->child) {                   /* 先跑自己的解構，再釋放子樹 */
        void *kid = h->child->data;
        if (h_free(kid) < 0)             /* 子節點拒絕：往上一層搬 */
            h_steal(h->parent ? h->parent->data : NULL, kid);
    }
    unlink_from_parent(h);
    printf("  free %s\n", h->name);
    free(h);
    return 0;
}

struct conn { int fd; char *peer; };

static int conn_dtor(void *p)
{
    struct conn *c = p;
    printf("  close fd %d (peer still readable: %s)\n", c->fd, c->peer);
    return 0;
}

int main(void)
{
    printf("sizeof(struct hdr) = %zu, offsetof data = %zu\n",
           sizeof(struct hdr), offsetof(struct hdr, data));
    void *request = h_alloc(NULL, 0, "request");
    struct conn *c = h_alloc(request, sizeof(*c), "conn");
    c->fd = 3;
    c->peer = strcpy(h_alloc(c, 16, "peer"), "10.0.0.1");
    char *reply = strcpy(h_alloc(request, 16, "reply"), "200 OK");

    void *session = h_alloc(NULL, 0, "session");
    h_steal(session, reply);             /* 回應要活得比請求久 */
    h_set_destructor(c, conn_dtor);

    puts("h_free(request):");
    h_free(request);
    printf("reply survives: %s\n", reply);
    puts("h_free(session):");
    h_free(session);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra -fsanitize=address halloc.c -o halloc && ./halloc
sizeof(struct hdr) = 48, offsetof data = 48
h_free(request):
  close fd 3 (peer still readable: 10.0.0.1)
  free peer
  free conn
  free request
reply survives: 200 OK
h_free(session):
  free reply
  free session
```

clang 14 加 ASan 的輸出逐位元組相同，沒有任何 ASan 錯誤。幾個設計決策值得說明：

- **為什麼解構函式先跑、子節點後放**：解構函式常需要讀子節點（例子裡 `conn_dtor` 印出 `peer`），所以 talloc 的 `_tc_free_internal()` 也是這個順序。
- **為什麼要 `steal`**：「請求要釋放，但回應要留給連線」是伺服器的日常。沒有 `steal`，只能複製一份或乾脆不用階層。
- **`sizeof(struct hdr)` 是 48**：六個 8 位元組欄位剛好 48，又剛好是 `max_align_t`（16）的倍數，所以 `alignas` 沒有多墊位元組。這也是代價：每塊記憶體多 48 位元組。talloc 的完整標頭在 LP64 上對齊後是 96 位元組（依原文對 `talloc.c` 的計算），所以 talloc 另提供 `talloc_pool()` 預先配置大塊再切，接近 arena 配置器。
- **工業版還做了什麼**：talloc 的 `flags` 含隨機 magic 偵測 double free 與標頭被覆寫；`talloc_reference()` 允許多個親代。這些都是教學版省略的。

### 把標頭藏在指標前面：SDS 與 stb_ds

talloc 的 `HDR()` 已經示範「使用者指標指向資料區、標頭在前面看不到」。把同一招用在字串與動態陣列上，得到的是**可以直接交給既有 API 的指標**。Redis 的 SDS（Simple Dynamic Strings）型別就是 `typedef char *sds;`：可以直接傳給 `printf("%s")`，但長度是 O(1) 取得，內容可以含 `'\0'`。[stb_ds.h](https://github.com/nothings/stb/blob/master/stb_ds.h) 的動態陣列（stretchy buffer）也一樣，`int *a` 就是普通的 C 陣列指標。

```
            malloc 回傳的位址              使用者拿到的指標 a
                  │                              │
                  ▼                              ▼
                  ┌──────────┬──────────┬────┬────┬────┬─────
                  │ len (8B) │ cap (8B) │a[0]│a[1]│a[2]│ ...
                  └──────────┴──────────┴────┴────┴────┴─────
                  ◄──── vec_hdr(a) = (vec_hdr *)a - 1 ────►
```

自己寫一個迷你版，順便驗證兩個陷阱：

```c
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- 迷你 stretchy buffer：標頭藏在陣列指標前面 ---- */
typedef struct { size_t len, cap; } vec_hdr;   /* 16 bytes：保留 malloc 的 16 位元組對齊 */

#define vec_hdr(a)  ((vec_hdr *) (a) - 1)
#define vec_len(a)  ((a) ? vec_hdr(a)->len : 0)
#define vec_cap(a)  ((a) ? vec_hdr(a)->cap : 0)
#define vec_push(a, v) \
    (vec_grow((void **) &(a), sizeof *(a)), (a)[vec_hdr(a)->len++] = (v))
#define vec_free(a) ((a) ? free(vec_hdr(a)) : (void) 0, (a) = NULL)

static void vec_grow(void **pa, size_t elem)
{
    size_t len = vec_len(*pa), cap = vec_cap(*pa);
    if (len < cap)
        return;
    size_t ncap = cap ? cap * 2 : 4;
    if (ncap > (SIZE_MAX - sizeof(vec_hdr)) / elem)   /* 乘法與加法都要防溢位 */
        abort();
    vec_hdr *h = realloc(*pa ? vec_hdr(*pa) : NULL, sizeof(vec_hdr) + ncap * elem);
    if (!h)
        abort();
    if (!*pa)
        h->len = 0;
    h->cap = ncap;
    *pa = h + 1;
}

/* ---- packed 與否：sizeof 與 offsetof 是否一致 ---- */
struct plain  { uint32_t len, alloc; unsigned char flags; char buf[]; };
struct __attribute__((packed)) packed { uint32_t len, alloc; unsigned char flags; char buf[]; };

int main(int argc, char **argv)
{
    (void) argv;
    int *a = NULL;                    /* 空陣列就是 NULL */
    for (int i = 0; i < 10; i++) {
        vec_push(a, i * i);
        if (vec_len(a) == vec_cap(a))
            printf("len=%zu cap=%zu (full)\n", vec_len(a), vec_cap(a));
    }
    printf("a[9] = %d, (uintptr_t) a %% 16 = %lu\n", a[9], (unsigned long) ((uintptr_t) a % 16));

    printf("plain : sizeof=%zu offsetof(buf)=%zu\n", sizeof(struct plain), offsetof(struct plain, buf));
    printf("packed: sizeof=%zu offsetof(buf)=%zu\n", sizeof(struct packed), offsetof(struct packed, buf));

    if (argc > 1)
        free(a);                      /* 錯：a 不是 malloc 回傳的位址 */
    else
        vec_free(a);
    printf("after free: a = %p\n", (void *) a);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra hidehdr.c -o hh && ./hh
len=4 cap=4 (full)
len=8 cap=8 (full)
a[9] = 81, (uintptr_t) a % 16 = 0
plain : sizeof=12 offsetof(buf)=9
packed: sizeof=9 offsetof(buf)=9
after free: a = (nil)
$ ./hh bad
free(): invalid size
Aborted (core dumped)
$ gcc -std=c11 -g -fsanitize=address hidehdr.c -o hha && ./hha bad
==6564==ERROR: AddressSanitizer: attempting free on address which was not malloc()-ed: 0x507000000110 in thread T0
```

**為什麼 `vec_push` 必須是巨集**：擴充時 `realloc` 可能搬家，必須改寫呼叫者手上的 `a`；寫入 `(a)[...] = (v)` 和 `sizeof *(a)` 又需要元素型別，函式只拿得到 `void *`。stb_ds 的文件直接寫明代價："the pointer must be an lvalue, and the pointer to the data structure is not stable"。

**為什麼 SDS 要 `packed`**：SDS 用 `s[-1]` 讀 `flags`、用 `sizeof(header)` 從 `s` 往回推標頭，這要求「`sizeof` 等於 `offsetof(buf)`」。C11 6.7.2.1 §18 允許含彈性陣列成員的結構體尾端多墊填充：上面的實測 `plain` 是 `offsetof = 9` 但 `sizeof = 12`，用 `sizeof` 回推就差 3 位元組；`packed` 之後兩者都是 9。`s[-1]` 本身合法，是因為 `s` 指向同一塊配置的中間，指標運算沒有越出物件（C11 6.5.6 §8）。

這類設計的三個陷阱：

- **不能 `free(s)`**：指標指向配置區塊的中間。上面 glibc 2.35 印出 `free(): invalid size`（訊息內容依殘值而異，原文環境是 `invalid pointer`），ASan 直接點名。
- **任何可能擴充的操作都讓舊指標失效**：SDS 用 `s = sdscatlen(s, ...)` 的回傳值約定，stb_ds 用巨集改寫變數；但存在別處的副本會懸空。巨集還會多次求值引數，`vec_push(v[i++], x)` 這種寫法不可用。
- **對齊**：SDS 的 packed 標頭讓資料區落在奇數位址（1、3、5 位元組標頭），對 `char` 無妨，換成 `uint64_t` 陣列就是未對齊存取。這個迷你版把標頭做成 16 位元組，保住 `malloc` 的 16 位元組對齊（上面實測 `a % 16 = 0`），stb_ds 的標頭是 32 位元組，同樣的考量。

### 小緩衝區最佳化：先用堆疊，不夠再配置

字串與暫存資料**多數很短、偶爾很長**。每次都 `malloc` 浪費在常見情況；固定陣列又在罕見情況截斷。小緩衝區最佳化（small buffer optimization）在結構體裡內嵌一塊固定大小的陣列，指標先指向它，不夠時才換成 heap。陣列大小在編譯時期固定，堆疊用量不隨輸入成長，這是它和 VLA、`alloca` 的根本差別。

glibc 內部的 `include/scratch_buffer.h`（glibc 2.22 加入，不對外安裝）就是這樣：`struct scratch_buffer { void *data; size_t length; union { max_align_t __align; char __c[1024]; } __space; };`，`realpath` 的實作同時用三個，在堆疊上佔約 3 KiB，換得常見路徑長度完全不配置記憶體。

```c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct sbuf {
    char *data;       /* 指向 local[] 或 heap */
    size_t len, cap;
    char local[16];
};

static void sbuf_init(struct sbuf *b)
{
    b->data = b->local;
    b->len = 0;
    b->cap = sizeof b->local;
    b->local[0] = '\0';
}

static void sbuf_free(struct sbuf *b)
{
    if (b->data != b->local)
        free(b->data);
}

static int sbuf_append(struct sbuf *b, const char *s, size_t n)
{
    if (n >= b->cap - b->len) {                 /* 需要 len + n + 1 位元組 */
        if (n > SIZE_MAX - b->len - 1)
            return -1;
        size_t need = b->len + n + 1;
        size_t cap = b->cap <= SIZE_MAX / 2 && b->cap * 2 > need ? b->cap * 2 : need;
        char *p;
        if (b->data == b->local) {              /* 第一次搬家：realloc 不能用在堆疊陣列 */
            if (!(p = malloc(cap)))
                return -1;
            memcpy(p, b->local, b->len + 1);
        } else if (!(p = realloc(b->data, cap))) {
            return -1;
        }
        b->data = p;
        b->cap = cap;
    }
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = '\0';
    return 0;
}

static char *greet_bad(void)
{
    struct sbuf b;
    sbuf_init(&b);
    sbuf_append(&b, "hi", 2);
    return b.data;                              /* 回傳指向 b.local 的指標：懸空 */
}

int main(int argc, char **argv)
{
    (void) argv;
    static const char *words[] = {"alpha ", "beta ", "gamma ", "delta ", "epsilon"};
    struct sbuf b;
    sbuf_init(&b);
    for (size_t i = 0; i < sizeof words / sizeof *words; i++) {
        if (sbuf_append(&b, words[i], strlen(words[i])))
            return 1;
        printf("len=%2zu cap=%2zu %s\n", b.len, b.cap, b.data == b.local ? "stack" : "heap");
    }
    printf("\"%s\"\n", b.data);
    sbuf_free(&b);

    struct sbuf a, c;
    sbuf_init(&a);
    sbuf_append(&a, "abc", 3);
    c = a;                                      /* 淺複製：c.data 仍指向 a.local */
    a.local[0] = 'X';
    printf("c.data=\"%s\" (c.data == a.local: %d)\n", c.data, c.data == a.local);

    if (argc > 1) {
        char *s = greet_bad();
        printf("greet_bad: \"%s\"\n", s);
    }
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra sbuf.c -o sb && ./sb
len= 6 cap=16 stack
len=11 cap=16 stack
len=17 cap=32 heap
len=23 cap=32 heap
len=30 cap=32 heap
"alpha beta gamma delta epsilon"
c.data="Xbc" (c.data == a.local: 1)
$ gcc -std=c11 -O0 -g -fsanitize=address sbuf.c -o sba
$ ASAN_OPTIONS=detect_stack_use_after_return=1 ./sba bad
==7010==ERROR: AddressSanitizer: stack-use-after-return on address 0x7ff4e0e00048 ...
```

`-O2 -Wall -Wextra` 對 `greet_bad` **沒有任何警告**：編譯器看不出 `b.data` 指向 `b` 自己。三個陷阱：

- **回傳內嵌陣列**：如上。正確作法是像 SQLite 的 `sqlite3StrAccumFinish`，交出所有權時若內容仍在內嵌陣列，就複製到 heap。GCC 11 的 ASan 要開 `detect_stack_use_after_return=1` 才抓得到（gcc 11 預設關閉這項偵測）。
- **淺複製**：`data` 指向自己的 `local`，`c = a` 之後 `c.data` 仍指向 `a.local`，改 `a` 就改到 `c`；`a` 先離開範圍，`c` 就懸空。以值回傳、`realloc` 一個由這種結構組成的陣列，都是淺複製。C++ 的 `std::string` 靠複製/移動建構子修正這個指標，C 沒有建構子，只能禁止複製或改存旗標（SQLite 的 `StrAccum` 就用旗標 `SQLITE_PRINTF_MALLOCED`）。
- **堆疊用量累加**：每個物件都佔完整的內嵌陣列，遞迴時要算進總量。

C++ 標準程式庫的 `std::string` 是這個技巧的極致版本，叫短字串最佳化（SSO）：libstdc++ 32 位元組可存 15 字元、libc++ 24 位元組可存 22 字元，Facebook 的 `folly::fbstring` 讓最後一個位元組同時當「剩餘空間」與結尾的 `'\0'`，23 個字元時剩餘空間為 0，恰好就是 NUL。數字取自原文在各標準程式庫上的實測，本章未在 C++ 環境重測。

### 彈性陣列成員與 `__counted_by`

C99 之前常見在結構尾端宣告 `char data[1]`，再多配置空間越界存取，嚴格來說是未定義行為；GNU C 的 `char data[0]` 在任何版本的 ISO C 都不合法。C99 起，結構最後一個成員可以是不完整陣列 `char data[]`，叫**彈性陣列成員**（flexible array member，C11 6.7.2.1 §18）：`sizeof` 忽略它，存取時視為「剛好容得下配置空間的最長陣列」。標頭與資料一次配置、一次釋放，資料緊接在標頭後面，對快取也友善。

它的弱點是**長度只存在程式設計者的腦中**。GCC 15 與 Clang 18 起支援 `counted_by` 屬性，把長度綁到同一結構的另一個成員，讓 `-fsanitize=bounds` 和 `__builtin_dynamic_object_size()` 用得上：

```c
#include <stdio.h>
#include <stdlib.h>

struct msg {
    size_t len;
#ifdef NOCB
    char data[];
#else
    char data[] __attribute__((counted_by(len)));
#endif
};

__attribute__((noinline)) static struct msg *msg_new(size_t n)
{
    struct msg *m = malloc(sizeof(*m) + n * sizeof(m->data[0]));
    if (m)
        m->len = n;
    return m;
}

int main(int argc, char **argv)
{
    (void) argv;
    struct msg *m = msg_new(4);
    printf("sizeof(struct msg) = %zu\n", sizeof(struct msg));
    printf("bdos(m->data) = %zu\n", __builtin_dynamic_object_size(m->data, 1));
    fflush(stdout);
    for (int i = 0; i < 4 + argc; i++)   /* argc == 1：多寫一格 */
        m->data[i] = 'a';
    puts("loop finished");
    free(m);
    return 0;
}
```

`msg_new` 標 `noinline`，避免編譯器直接從 `malloc` 的引數推出大小，確保看到的是 `counted_by` 的效果。在 Docker `gcc:15`（GCC 15.3.0）：

```
$ gcc -std=c11 -O2 -fsanitize=bounds cby.c -o c1 && ./c1
sizeof(struct msg) = 8
bdos(m->data) = 4
cby.c:29:16: runtime error: index 4 out of bounds for type 'char [*]'
loop finished
$ gcc -std=c11 -O2 -fsanitize=bounds -DNOCB cby.c -o c2 && ./c2
sizeof(struct msg) = 8
bdos(m->data) = 18446744073709551615
loop finished
$ gcc -std=c11 -O2 -fsanitize=bounds -fsanitize-trap=bounds cby.c -o c3 && ./c3; echo "exit=$?"
sizeof(struct msg) = 8
bdos(m->data) = 4
Illegal instruction (core dumped)
exit=132
```

| | `bdos(m->data)` | 寫入 `data[4]` |
|---|---|---|
| 有 `counted_by(len)` | 4 | 預設回報後繼續；`-fsanitize-trap` 時 `ud2` 觸發 SIGILL（132） |
| 無 `counted_by` | `SIZE_MAX`（大小未知） | 沒有偵測 |

本機的 gcc 11 與 clang 14 太舊：gcc 11 印出 `'counted_by' attribute directive ignored`，而且連 `__builtin_dynamic_object_size` 都沒有（GCC 12 才加入）；clang 14 印出 `unknown attribute 'counted_by' ignored`。

`counted_by` 也帶來新規則：**計數成員要在存取陣列之前設好，而且不可大於實際配置的元素數**，否則檢查依據的就是錯誤長度。Linux 核心的 `__counted_by()` 巨集在不支援的編譯器展開為空；配置端用 `include/linux/overflow.h` 的 `struct_size(p, member, count)` 計算總大小，`count` 不是常數時以飽和運算避免回繞，手寫的 `sizeof(*p) + n * sizeof(p->data[0])` 沒有這層保護（溢位檢查見 [Ch 22](./22-integer-overflow.md)）。另一條規則：C11 6.7.2.1 §3 禁止「含彈性陣列成員的結構」當作其他結構的成員，核心因此用 `struct_group_tagged` 把標頭另外命名，讓別的結構只嵌入標頭部分。

### VLA 與陣列參數：把長度交給編譯器

C99 的可變長度陣列（VLA）讓陣列維度是執行時期的值。它有兩種完全不同的用途，要分開看：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* VLA 型別參數：C[i][j] 直接可用，n 在執行時期才決定 */
static double trace(size_t n, double m[n][n])
{
    double t = 0;
    for (size_t i = 0; i < n; i++)
        t += m[i][i];
    return t;
}

int main(int argc, char **argv)
{
    size_t n = 3, kib = argc > 1 ? strtoul(argv[1], NULL, 10) : 3;
    double (*m)[n] = malloc(sizeof(double[n][n]));   /* heap 上的 n x n：型別是 VLA，物件不在堆疊 */
    if (!m)
        return 1;
    for (size_t i = 0; i < n; i++)
        for (size_t j = 0; j < n; j++)
            m[i][j] = (double) (i == j);
    printf("n=%zu sizeof(double[n][n])=%zu trace=%g\n", n, sizeof(double[n][n]), trace(n, m));
    free(m);

    char stackbuf[kib * 1024];                        /* 堆疊上的 VLA：大小由輸入決定 */
    memset(stackbuf, 1, sizeof stackbuf);
    printf("stack VLA of %zu bytes ok (%d)\n", sizeof stackbuf, stackbuf[0]);
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra vla.c -o vla
$ ./vla 4
n=3 sizeof(double[n][n])=72 trace=3
stack VLA of 4096 bytes ok (1)
$ ./vla 7000
n=3 sizeof(double[n][n])=72 trace=3
stack VLA of 7168000 bytes ok (1)
$ ./vla 100000; echo "exit=$?"            # ulimit -s 是 8192 KiB
n=3 sizeof(double[n][n])=72 trace=3
Segmentation fault (core dumped)
exit=139
```

**VLA 型別**（`double m[n][n]` 參數、`double (*m)[n]` 指標）是有用的型別資訊：`sizeof` 在執行時期算出 72，`m[i][j]` 自動換算索引，記憶體可以在 heap。**堆疊上的 VLA 物件**則和 `alloca` 一樣，大小由輸入決定、超過堆疊上限時沒有回報錯誤的管道，只能 SIGSEGV。（第一次寫這個實驗時只寫了 `stackbuf[0] = 1`，`-O2` 把整個陣列最佳化掉，100 MB 也「成功」——量測堆疊用量的實驗要真的碰到記憶體。）Linux 核心在 v4.20 移除了所有堆疊 VLA，v6.16 改用 `-Wvla-larger-than=1` 只擋堆疊 VLA、允許 VLA 型別參數（依原文說明）。MSVC 至今不支援 VLA。

陣列參數另有一項 C99 的資訊：方括號裡的 `static`。C11 6.7.6.3 §7 規定 `int a[static n]` 的引數必須指向**至少 n 個元素**，因此也不得為 NULL。ABI 不變，參數仍是指標，但編譯器可以據此診斷：

```c
#include <stddef.h>
#include <stdio.h>

void digest(unsigned char out[static 16]);      /* 至少 16 個元素，不得為 NULL */
long sum(size_t n, const int a[static n]);      /* 長度是前一個參數 */
void fill(int a[8]);
void scale(size_t n, int a[n]);

void digest(unsigned char out[static 16])
{
    for (int i = 0; i < 16; i++)
        out[i] = (unsigned char) i;
}

long sum(size_t n, const int a[static n])
{
    long s = 0;
    for (size_t i = 0; i < n; i++)
        s += a[i];
    return s;
}

void fill(int a[]) { a[0] = 0; }                  /* 宣告寫 a[8] */
void scale(size_t n, int *a) { a[0] *= (int) n; } /* 宣告寫 a[n] */

void show_size(int a[16]) { printf("sizeof a in callee = %zu\n", sizeof a); }

int main(void)
{
    unsigned char small[8];
    int v[4] = {1, 2, 3, 4};
    int big[16] = {0};
    digest(small);              /* 太短 */
    digest(NULL);               /* [static] 不可為 NULL */
    printf("%ld\n", sum(5, v)); /* 說有 5 個，其實 4 個 */
    show_size(big);
    return 0;
}
```

```
$ gcc -std=c11 -O0 -Wall -Wextra -c arrparam.c
arrparam.c:23:15: warning: argument 1 of type ‘int[]’ with mismatched bound [-Warray-parameter=]
arrparam.c:24:27: warning: argument 2 of type ‘int *’ declared as a pointer [-Wvla-parameter]
arrparam.c:26:73: warning: ‘sizeof’ on array function parameter ‘a’ will return size of ‘int *’ [-Wsizeof-array-argument]
arrparam.c:33:5: warning: ‘digest’ accessing 16 bytes in a region of size 8 [-Wstringop-overflow=]
arrparam.c:34:5: warning: argument 1 to ‘unsigned char[static 16]’ is null where non-null expected [-Wnonnull]
arrparam.c:35:5: warning: ‘sum’ reading 20 bytes from a region of size 16 [-Wstringop-overread]
$ gcc -std=c11 -O2 -Wall -Wextra -c arrparam.c       # 同一份程式，開最佳化
（只剩前三則）
$ clang -std=c11 -O2 -Wall -Wextra -c arrparam.c
arrparam.c:26:73: warning: sizeof on array function parameter will return size of 'int *' instead of 'int[16]' [-Wsizeof-array-argument]
arrparam.c:33:5: warning: array argument is too small; contains 8 elements, callee requires at least 16 [-Warray-bounds]
arrparam.c:34:5: warning: null passed to a callee that requires a non-null argument [-Wnonnull]
```

一個值得記住的現象：**gcc 11 在 `-O2` 下反而少了三則呼叫端警告**。原因是函式定義就在同一檔案、被內嵌進 `main` 之後，呼叫不存在了，依附在呼叫上的檢查也跟著消失；把定義移到別的檔案（只留原型）再編，`-O2` 三則都回來。警告依賴最佳化與內嵌決策，這是為什麼 CI 最好用兩種最佳化等級各編一次。gcc 獨有 `-Wvla-parameter` 與「`sum` 讀 20 位元組但只有 16」這種用 VLA 維度推出的診斷；clang 14 沒有。NetBSD 核心的 AES 介面 `aes_setenckey128(struct aesenc *, const uint8_t[static 16])` 就是把金鑰長度寫進型別。限制：C++ 不接受方括號裡的 `static`；而且 `[static N]` 隱含非 NULL，函式內的 `if (!p)` 可能被刪掉——和下一節 `nonnull` 的問題相同。

## 資源與契約：讓編譯器替你收尾與檢查

### cleanup 屬性：C 版的 RAII

C 沒有解構子。GCC 與 Clang 的變數屬性 `__attribute__((cleanup(f)))` 讓變數離開作用域時，編譯器自動以**變數的位址**呼叫 `f`。GCC 文件明定三件事：只能用在函式內的 automatic 變數；同一作用域的多個 cleanup 變數依「最後定義者最先清理」執行；cleanup 函式若以 `longjmp` 之類方式不正常返回，行為未定義（`longjmp` 不做 stack unwinding 的後果見 [Ch 12](./12-setjmp-and-coroutines.md)）。

實務上第一個要解決的是**所有權轉移**：資源要交給呼叫者時，必須讓 cleanup 函式看到 NULL 而什麼都不做。下例以 systemd 的風格寫：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define _cleanup_(f) __attribute__((cleanup(f)))
#define TAKE_PTR(p)                    \
    ({                                 \
        __typeof__(p) *_pp = &(p);     \
        __typeof__(p) _v = *_pp;       \
        *_pp = NULL;                   \
        _v;                            \
    })

static void freep(void *p)            /* 收到的是「指向變數的指標」 */
{
    void **pp = p;
    if (*pp)
        printf("  free(%s)\n", (char *) *pp);
    free(*pp);
}
#define _cleanup_free_ _cleanup_(freep)

static void fclosep(FILE **fp)
{
    if (*fp) {
        puts("  fclose");
        fclose(*fp);
    }
}

static char *load(const char *path, int fail)
{
    _cleanup_(fclosep) FILE *f = fopen(path, "r");
    if (!f)
        return NULL;
    _cleanup_free_ char *a = strdup("a");
    _cleanup_free_ char *b = strdup("b");
    if (fail)
        return NULL;        /* b、a、f 依反向順序釋放 */
    return TAKE_PTR(a);     /* 所有權交給呼叫者 */
}

int main(void)
{
    puts("fail path:");
    load("/etc/hosts", 1);
    puts("success path:");
    char *r = load("/etc/hosts", 0);
    printf("caller owns \"%s\"\n", r);
    free(r);
    return 0;
}
```

```
$ gcc -std=gnu11 -O2 -Wall -Wextra cleanup.c -o cl && ./cl
fail path:
  free(b)
  free(a)
  fclose
success path:
  free(b)
  fclose
caller owns "a"
```

clang 14 輸出相同，但多一則 `unused variable 'b' [-Wunused-variable]`：`b` 只被 cleanup 用到，clang 14 不把那算作「使用」。三個專案各自包裝這個屬性：

- **systemd**：`_cleanup_free_` + `TAKE_PTR()`（註解寫明取法自 Rust 的 `Option::take()`），以及 `DEFINE_TRIVIAL_CLEANUP_FUNC` 批次產生 `func##p` 包裝函式，慣例是釋放函式回傳空值（`mfree()` 回傳 NULL），清理後變數自動歸零。
- **GLib**（2.44 起）：`g_autofree`、`g_autoptr(TypeName)`、`g_steal_pointer()`。在不支援 cleanup 的 MSVC 上這些巨集根本不定義，誤用會**編譯失敗**而不是默默洩漏。
- **Linux 核心**（v6.5 起，`include/linux/cleanup.h`）：`__free(kfree)`、`no_free_ptr()`、`return_ptr()`，以及把 lock 綁到作用域的 `guard(mutex)(&lock)`、`scoped_guard()`。

cleanup 函式的參數是「指向變數的指標」，所以 `cleanup(free)` 是錯的：

```c
#include <stdlib.h>
#include <string.h>
int main(void)
{
    __attribute__((cleanup(free))) char *s = strdup("oops");   /* 錯：free 收到 &s */
    return s[0] == 'o' ? 0 : 1;
}
```

```
$ gcc -std=gnu11 -O2 -Wall -Wextra cbad.c -o cb && ./cb; echo "exit=$?"
cbad.c:5:42: warning: ‘free’ called on unallocated object ‘s’ [-Wfree-nonheap-object]
Segmentation fault (core dumped)
exit=139
$ clang -std=gnu11 -O2 -Wall -Wextra cbad.c        # clang 14：沒有任何警告
```

第二個陷阱是 **`goto` 跳過 cleanup 變數的初始化**。Linux 的 cleanup.h 文件明文禁止在同一函式混用 `goto` 與 cleanup 變數，理由在這裡：

```c
#include <stdio.h>
#include <stdlib.h>
static void freep(void *p)
{
    printf("freep(%p)\n", *(void **) p);
    free(*(void **) p);
}
void f(int c)
{
    if (c)
        goto out;
    __attribute__((cleanup(freep))) void *p = malloc(1);
out:
    return;
}
int main(void) { f(1); return 0; }
```

```
$ clang -std=gnu11 -Wall -Wextra cgoto.c
cgoto.c:11:9: error: cannot jump from this goto statement to its label
cgoto.c:12:43: note: jump bypasses initialization of variable with __attribute__((cleanup))
$ gcc -std=gnu11 -O0 -Wall -Wextra cgoto.c -o cg      # 沒有任何警告
$ stdbuf -o0 ./cg
freep(0x7fff0fd60429)
free(): invalid pointer
Aborted (core dumped)
$ gcc -std=gnu11 -O2 -Wall -Wextra cgoto.c
cgoto.c:6:5: warning: ‘p’ may be used uninitialized in this function [-Wmaybe-uninitialized]
cgoto.c:6:5: warning: ‘p’ is used uninitialized [-Wuninitialized]
$ gcc -std=gnu11 -O0 -Wjump-misses-init cgoto.c
cgoto.c:11:9: warning: jump skips variable initialization [-Wjump-misses-init]
```

clang 拒絕編譯；gcc 照常編譯，產生的程式對**未初始化的 `p`** 呼叫 `freep`。本機 `-O0` 讀到的殘值是一個堆疊位址（每次執行不同），`free` 當場中止；原文環境讀到的殘值恰好是 0，程式印出 `freep((nil))` 正常結束，錯誤被掩蓋。同一個 bug，在不同機器上一個當掉一個正常，這就是讀未初始化值的典型樣貌（[Ch 34](./34-undefined-behavior.md) 細談）。注意 `stdbuf -o0`：不加的話 `freep(...)` 那行留在全緩衝的 stdout 裡，隨 SIGABRT 一起消失——這個現象在 [Ch 35](./35-stream-io-and-signals.md) 會反覆出現。

標準化方面，WG14 正以技術規格 ISO/IEC TS 25755 發展 `defer`，它綁定任意敘述，不必替每種資源先寫包裝函式；依原文，Clang 22 以 `-fdefer-ts` 實作，本機的編譯器都不支援，本段未實測。`goto` 清理鏈的傳統寫法見 [Ch 31](./31-goto-and-control-flow.md)。

### 把記憶體當成 FILE：open_memstream 與 fmemopen

很多既有函式只認得 `FILE *`。POSIX.1-2008 的 `open_memstream` 開一個只寫的串流，內容寫進會自動成長的 `malloc` 緩衝區；`fmemopen` 把既有記憶體包成可讀的串流。前者讓同一個輸出函式既能寫 `stdout` 也能產生字串，不必先用 `snprintf` 算長度；後者讓只認得 `FILE *` 的剖析器處理記憶體裡的字串：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct kv { char key[16]; int val; };

/* 只認得 FILE * 的輸出函式：stdout、檔案、記憶體都能寫 */
static void dump(FILE *f, const struct kv *t, size_t n)
{
    for (size_t i = 0; i < n; i++)
        fprintf(f, "%s=%d\n", t[i].key, t[i].val);
}

/* 只認得 FILE * 的剖析器 */
static size_t parse(FILE *f, struct kv *t, size_t max)
{
    size_t n = 0;
    while (n < max && fscanf(f, "%15[^=]=%d\n", t[n].key, &t[n].val) == 2)
        n++;
    return n;
}

int main(void)
{
    const struct kv in[] = {{"width", 640}, {"height", 480}, {"depth", 24}};
    char *buf = NULL;
    size_t size = 12345;                        /* 故意放垃圾值，看何時被更新 */

    FILE *f = open_memstream(&buf, &size);
    if (!f)
        return 1;
    dump(f, in, 3);
    printf("before fflush: buf=%p size=%zu\n", (void *) buf, size);
    if (fflush(f) != 0)
        return 1;
    printf("after fflush:  size=%zu strlen=%zu\n", size, strlen(buf));
    fprintf(f, "%s=%d\n", "bpp", 3);
    if (ferror(f) | fclose(f))
        return 1;
    printf("after fclose:  size=%zu\n%s", size, buf);

    struct kv out[8];
    FILE *g = fmemopen(buf, size, "r");         /* 把同一塊記憶體讀回來 */
    if (!g)
        return 1;
    size_t n = parse(g, out, 8);
    fclose(g);
    printf("parsed %zu entries, last: %s=%d\n", n, out[n - 1].key, out[n - 1].val);
    free(buf);                                  /* buffer 歸呼叫者 */
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra memstream.c
memstream.c:29:15: warning: implicit declaration of function ‘open_memstream’ [-Wimplicit-function-declaration]
...
$ gcc -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -fsanitize=address memstream.c -o ms && ./ms
before fflush: buf=(nil) size=12345
after fflush:  size=30 strlen=30
after fclose:  size=36
width=640
height=480
depth=24
bpp=3
parsed 4 entries, last: bpp=3
```

第一次編譯失敗是常見踩雷：`-std=c11` 是嚴格 ISO 模式，glibc 不宣告 POSIX 函式，要自己定義 feature test macro（[Ch 35](./35-stream-io-and-signals.md) 會看到同一個機制改變 `signal` 的語意）。第二個觀察是規則本身：**`buf` 與 `size` 只在 `fflush` 或 `fclose` 之後才保證有效**，之前是什麼由實作決定——glibc 2.35 在第一次 `fflush` 前保持 `buf` 為 NULL、`size` 原封不動是 12345。串流仍開著時寫入可能讓 `buf` 被 `realloc` 搬走，不能保留舊值；`size` 不含結尾 `'\0'`；`fclose` 後緩衝區歸呼叫者 `free`。

systemd 在 2023 年把這組規則包成 `src/basic/memstream-util.c` 的三個函式：`memstream_init()` 開串流並用 `__fsetlocking(f, FSETLOCKING_BYCALLER)` 關掉內部 lock（前提是只有一個執行緒用它）、`memstream_finalize()` 補 `'\0'`、`fflush` 並檢查錯誤後再關閉，最後用 `TAKE_PTR` 交出緩衝區、`memstream_done()` 搭配 cleanup 屬性處理失敗路徑。把前面兩節的技巧組合起來，正是真實專案的樣子。

### 函式屬性：format、nonnull、`[[nodiscard]]`

函式屬性讓編譯器**在呼叫端**檢查介面契約。`format(printf, m, n)` 表示第 m 個參數是 printf 格式字串、可變參數從第 n 個開始，自訂的日誌函式因此得到和 `printf` 一樣的型別檢查；`nonnull(i)` 宣告第 i 個指標參數不得為 NULL；`warn_unused_result` 要求使用回傳值：

```c
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

__attribute__((format(printf, 2, 3)))
static void log_msg(int level, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "<%d> ", level);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}

__attribute__((nonnull(1)))
static size_t safe_len(const char *s)
{
    if (!s)          /* 編譯器可能刪掉這個檢查 */
        return 0;
    return strlen(s);
}

/* snprintf 回傳「想寫」的長度 */
static int fmt_id(char *buf, size_t size, const char *user, unsigned id)
{
    int n = snprintf(buf, size, "%s#%u", user, id);
    if (n < 0 || (size_t) n >= size)
        return -1;   /* 截斷或編碼錯誤 */
    return n;
}

int main(int argc, char **argv)
{
    (void) argv;
    if (argc > 9) {                         /* 只為了診斷，不會執行 */
        log_msg(1, "pid=%d\n", "42");       /* 型別錯 */
        log_msg(1, "%s\n");                 /* 少一個引數 */
    }
    char buf[8];
    printf("%d\n", fmt_id(buf, sizeof buf, "ab", 7));
    printf("%d\n", fmt_id(buf, sizeof buf, "jserv", 12345));
    fflush(stdout);
    printf("%zu\n", safe_len(argc > 5 ? "x" : NULL));
    return 0;
}
```

```
$ gcc -std=c11 -O2 -Wall -Wextra attrs.c -o at
attrs.c:36:26: warning: format ‘%d’ expects argument of type ‘int’, but argument 3 has type ‘char *’ [-Wformat=]
attrs.c:37:22: warning: format ‘%s’ expects a matching ‘char *’ argument [-Wformat=]
attrs.c:43:21: warning: argument 1 null where non-null expected [-Wnonnull]
attrs.c:18:8: warning: ‘nonnull’ argument ‘s’ compared to NULL [-Wnonnull-compare]
attrs.c:26:37: warning: ‘%u’ directive output truncated writing 5 bytes into a region of size 2 [-Wformat-truncation=]
$ clang -std=c11 -O2 -Wall -Wextra attrs.c
attrs.c:18:10: warning: nonnull parameter 's' will evaluate to 'true' on first encounter [-Wpointer-bool-conversion]
attrs.c:36:32: warning: format specifies type 'int' but the argument has type 'char *' [-Wformat]
attrs.c:37:22: warning: more '%' conversions than data arguments [-Wformat-insufficient-args]
```

前兩行輸出是 `4` 與 `-1`：`snprintf` 回傳「如果緩衝區夠大會寫出的字元數」（不含 NUL），`"jserv#12345"` 要 11 個字元，`n >= size` 就代表截斷。gcc 甚至在編譯時期就用 `-Wformat-truncation` 預告了。

`nonnull` 對最佳化的影響比診斷更深。最後一行在 `argc == 1` 時傳 NULL，這是未定義行為。實測結果：

| 編譯器與選項 | `safe_len(NULL)` |
|---|---|
| gcc 11、clang 14，`-O0` | 印出 `0` |
| gcc 11、clang 14，`-O2` | 函式內的 `if (!s)` 被刪，`strlen(NULL)` → SIGSEGV（139） |
| clang 14 `-O2 -fno-delete-null-pointer-checks` | 印出 `0` |
| gcc 11 `-O2 -fno-delete-null-pointer-checks` | **仍然 SIGSEGV** |

最後一列不是 bug：GCC 文件寫明，函式定義上的 `nonnull` 所引發的最佳化「目前無法以移除屬性以外的方式停用」。結論是 **`nonnull` 與防禦性 NULL 檢查不要放在同一個函式**：要嘛相信契約、把檢查交給呼叫端的診斷，要嘛拿掉屬性、在本體檢查。

`warn_unused_result` 和 C23 的 `[[nodiscard]]` 用途相同，但有一處語意差異：

```c
__attribute__((warn_unused_result)) int gnu_try(void);
[[nodiscard("check for -ENOMEM")]] int c23_try(void);
void use(void)
{
    gnu_try();
    c23_try();
    (void) gnu_try();
    (void) c23_try();
}
```

```
$ gcc -std=c2x -c nodiscard.c
nodiscard.c:6:5: warning: ignoring return value of ‘c23_try’, declared with attribute ‘nodiscard’: "check for -ENOMEM" [-Wunused-result]
nodiscard.c:5:5: warning: ignoring return value of ‘gnu_try’ declared with attribute ‘warn_unused_result’ [-Wunused-result]
nodiscard.c:7:12: warning: ignoring return value of ‘gnu_try’ declared with attribute ‘warn_unused_result’ [-Wunused-result]
$ clang -std=c2x -c nodiscard.c
nodiscard.c:5:5: warning: ignoring return value of function declared with 'warn_unused_result' attribute [-Wunused-result]
nodiscard.c:6:5: warning: ignoring return value of function declared with 'nodiscard' attribute: check for -ENOMEM [-Wunused-result]
```

C23 6.7.13.3 把「明確轉型為 `void`」排除在建議診斷之外，所以 `(void) c23_try()` 兩邊都安靜；但 **gcc 的 `warn_unused_result` 連 `(void)` 都不放過**（第 7 行）。glibc 的 `read`、`write` 在 `_FORTIFY_SOURCE` 下被標成 `__wur`（即 `warn_unused_result`），所以你在 gcc 上無法用 `(void) read(...)` 消掉警告——這是設計，逼你處理 short read（[Ch 35](./35-stream-io-and-signals.md)）。

### 單成員結構：用型別系統擋住語意錯誤

`typedef` 只建立別名，`typedef uint32_t uid_t` 與 `typedef uint32_t gid_t` 可以互相指定而毫無診斷。把數值包進只有一個成員的結構，就得到編譯器會檢查的獨立型別：

```c
#include <stdint.h>
#include <stdio.h>

typedef struct { uint32_t val; } kuid;
typedef struct { uint32_t val; } kgid;
typedef struct { uint64_t ns; } duration;

#define KUID(v) ((kuid){ (v) })
static inline int uid_eq(kuid a, kuid b) { return a.val == b.val; }

static int may_access(kuid owner, kuid who)
{
    return uid_eq(owner, who) || uid_eq(who, KUID(0));
}

#ifdef MISUSE
static void misuse(kgid g, kuid u)
{
    may_access(g, u);         /* 把 gid 傳給要 uid 的參數 */
    duration d = { 5 };
    d = d * 2;                /* 包裝後沒有算術運算 */
    if (u == KUID(0)) { }     /* 結構不能用 == */
}
#endif

int main(void)
{
    printf("sizeof(kuid) = %zu\n", sizeof(kuid));
    printf("%d %d\n", may_access(KUID(1000), KUID(1000)),
                      may_access(KUID(1000), KUID(1001)));
    return 0;
}
```

```
$ gcc -std=c11 -Wall strongtd.c -o st && ./st
sizeof(kuid) = 4
1 0
$ gcc -std=c11 -DMISUSE strongtd.c
strongtd.c:19:16: error: incompatible type for argument 1 of ‘may_access’
strongtd.c:21:11: error: invalid operands to binary * (have ‘duration’ and ‘int’)
strongtd.c:22:11: error: invalid operands to binary == (have ‘kuid’ and ‘kuid’)
```

clang 14 在相同三行報錯。依據是 C11 6.7.2.3 §5：不帶 tag 的結構宣告各自宣告一個獨立型別，即使成員完全相同。包裝不帶執行時期成本——在 x86-64 System V ABI 下，只含一個整數成員的結構以暫存器傳遞（ABI 細節見 [Ch 11](./11-process-layout-and-abi.md)）：

```
$ cat wrap.c
typedef struct { uint32_t val; } kuid;
uint32_t f(kuid a) { return a.val + 1; }
uint32_t g(uint32_t a) { return a + 1; }
$ gcc -O2 -fcf-protection=none -S -o - wrap.c
f:
	leal	1(%rdi), %eax
	ret
g:
	leal	1(%rdi), %eax
	ret
```

兩個函式的機器碼一模一樣。Linux 核心在 v3.5 隨 user namespace 引入 `kuid_t`/`kgid_t`（`include/linux/uidgid_types.h` 的 `typedef struct { uid_t val; } kuid_t;`）：`kuid_t` 是核心內的全域 UID，使用者空間的 `uid_t` 依 namespace 對映，兩者若能互相指定，忘了 `make_kuid()` 就是權限漏洞，包成結構讓這類錯誤無法編譯。頁表項 `pte_t`、`pmd_t`、`pud_t` 也是同一手法，取原始值要明寫 `pte_val()`，程式碼審查時一目了然。代價是失去所有運算子，適合語意上本來就不該任意運算的值：識別碼、頁表項、帶單位的物理量。

## 對比與取捨

| 技巧 | 解決什麼 | 可攜性 | 主要代價 |
|---|---|---|---|
| Plan 9 嵌入 | 隱含轉成父類別指標 | 只有 gcc | 換編譯器就不能編 |
| transparent union | 一個參數收多種指標 | gcc、clang | 擴充語法；`_Generic` 是標準替代 |
| tagged union + 存取巨集 | 值語意的多型 | C11 | 每次存取要檢查標籤 |
| designated init / compound literal | 欄位順序無關、具名參數 | C99 | 覆寫警告、預設值不可有副作用 |
| C23 `auto` | 型別推導 | C23（gcc 13+、clang 18+） | 會丟 `const`、陣列退化 |
| 階層式配置 | 一次釋放整棵子樹 | 純 C | 每塊多一個標頭 |
| 標頭藏在指標前 | 指標可直接給既有 API | 純 C（SDS 用 packed） | 不能 `free`、擴充後舊指標失效 |
| 小緩衝區 | 常見情況不配置 | 純 C | 不能淺複製、不能回傳內嵌陣列 |
| cleanup 屬性 | 自動收尾 | gcc、clang | 不能被 `goto` 跳過、MSVC 沒有 |
| FAM + `counted_by` | 一次配置 + 邊界檢查 | FAM 是 C99；`counted_by` 需 gcc 15、clang 18 | 長度欄位要先設好 |
| `[static N]`、format、nonnull | 呼叫端診斷 | C99 / gcc、clang | `nonnull` 會刪掉本體的 NULL 檢查 |
| 單成員結構 | 語意型別檢查 | 純 C | 失去運算子 |

## 踩雷集錦

1. **以為 `bar((S *) t)` 和 Plan 9 的隱含轉換一樣**：轉型只是重新解讀位址，`S` 不在開頭就讀錯；Plan 9 擴充是真正轉到內嵌成員。可攜程式要嘛把基底放在第一個成員，要嘛用 `container_of` 的反方向計算。
2. **在預設值裡放副作用**：`(struct opts){ .id = next_id(), __VA_ARGS__ }` 裡的 `next_id()` 被覆寫時可能根本不執行（C11 6.7.9 註腳 151）。預設值只放常數。
3. **把 SDS / stretchy buffer 的指標交給 `free`**：它指向配置區塊的中間，glibc 會中止程式。用該函式庫自己的 `sdsfree`、`arrfree`。
4. **複製含小緩衝區的結構**：`c = a` 之後 `c.data` 指向 `a.local`。要嘛提供複製函式修正指標，要嘛改存「資料在 heap 嗎」的旗標，不存自我指標。
5. **`cleanup(free)`**：cleanup 函式收到的是 `&p`，`free(&p)` 釋放的是堆疊位址；gcc 11 有警告、clang 14 沒有。永遠寫一個 `freep(void *pp)` 包裝。
6. **`goto` 跳過 cleanup 變數**：clang 拒絕編譯，gcc 安靜地對未初始化的指標呼叫清理函式。同一函式內二選一。
7. **在 `fflush` 之前讀 `open_memstream` 的 `buf`/`size`**：值由實作決定，glibc 是 NULL 和原本的垃圾值。
8. **`nonnull` 加上本體的 `if (!p)`**：gcc 的 `-fno-delete-null-pointer-checks` 也救不回來。

## 進階：再往深一層

- **讀 talloc 的 `_tc_free_children_internal()`**：子節點的解構函式否決釋放時，talloc 依序嘗試改掛到仍持有 reference 的擁有者、被釋放節點的上層，最後才是 null context。和本章簡化版的「只往上一層搬」比較，就能理解工業級配置器的細節在哪裡。
- **cleanup 與 dead code elimination 的合作**：Linux 的 `DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))` 明知 `kfree(NULL)` 合法還先判斷，是為了讓編譯器在 `return_ptr(p)` 之後看到「先把 p 設 NULL、再檢查 p」，經值傳播後整個清理呼叫消失，成功路徑只剩 `return p`。可以自己寫一個版本，用 `gcc -O2 -S` 確認清理呼叫是否被刪除。
- **面試題**：「C 怎麼做 RAII？」好的回答要涵蓋 cleanup 屬性、`goto` 清理鏈、以及 TS 25755 的 `defer`，並說出三者在可攜性與 `goto` 互動上的差異。

```c
/* 延伸練習：用 _Pragma 讓具名參數巨集在 gcc 與 clang 都不出警告 */
#define draw_line(x0, y0, x1, y1, ...)                                  \
    _Pragma("GCC diagnostic push")                                      \
    _Pragma("GCC diagnostic ignored \"-Woverride-init\"")               \
    draw_line_impl(x0, y0, x1, y1, (struct line_opts){                  \
        .width = 1, .color = "black", __VA_ARGS__ })                    \
    _Pragma("GCC diagnostic pop")
```

（這段是給讀者的練習起點，clang 的警告名稱是 `-Winitializer-overrides`，需要另外處理；本段未逐一實測。）

## 動手練習

1. 把 tagged union 例子的 `MATCH` 改成不加分號的 `case type_##T:`，用 `-std=c11` 與 Docker `gcc:14 -std=c23` 各編一次，比較錯誤訊息。
2. 在迷你 stretchy buffer 加 `vec_pop` 與 `vec_last`，然後故意寫 `vec_push(v[i++], x)`，用 `gcc -E` 看巨集展開後 `i++` 出現幾次。
3. 對 `sbuf` 寫一個 `sbuf_copy(struct sbuf *dst, const struct sbuf *src)`，正確處理「來源在內嵌陣列」與「來源在 heap」兩種情況，用 ASan 驗證。
4. 把 `attrs.c` 的 `nonnull` 移到一個獨立的原型宣告，定義上不加，重做 `-O2 -fno-delete-null-pointer-checks` 的實驗，看 gcc 是否仍刪除檢查。

## 本章重點整理

- C 的「繼承」就是「放在開頭的成員 + 指標轉型」；Plan 9 擴充、transparent union、tagged union 是讓這件事更安全或更好寫的三種包裝。
- designated initializer 與 compound literal 讓初始化不依賴欄位順序；覆寫規則（6.7.9 §19）讓具名參數巨集可行，但預設值不可有副作用。
- 「把資訊藏在指標前面」（talloc、SDS、stb_ds）讓指標能直接交給既有 API，代價是不能 `free`、擴充後指標失效、要注意對齊。
- cleanup 屬性是 C 最接近 RAII 的機制，所有權轉移靠 `TAKE_PTR` 歸零，`goto` 是它的天敵。
- 屬性與陣列參數讓編譯器檢查契約，但 `nonnull` 同時授權編譯器刪掉你的防禦程式碼；診斷結果還會隨最佳化等級變動。

## 自我檢核

- [ ] 不看筆記，能解釋 `circle circle;` 與 `T *T = &_s->T` 各自依賴哪條規格（名稱空間、範圍起點）
- [ ] 能說出 `open_memstream` 的 `buf` 什麼時候才有效，以及 systemd 為什麼關閉串流後還要檢查 `buf`
- [ ] 如果同事寫了一個帶 `local[32]` 的結構並用 `=` 複製它，你能在 code review 中說清楚會出什麼錯、怎麼修
- [ ] 能解釋為什麼 SDS 的標頭要 `packed`，而迷你 stretchy buffer 的標頭要 16 位元組
- [ ] 知道什麼情況下**不**該用 `nonnull`，以及 gcc 與 clang 在 `-fno-delete-null-pointer-checks` 下的差異
- [ ] 想一個你寫過、用 `typedef int` 表示 ID 的程式，說出改成單成員結構後哪些錯誤會變成編譯錯誤

## 延伸閱讀

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570（C11）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.2.1 §7（範圍起點）、6.2.3（名稱空間）、6.5.2.5（compound literal）、6.7.2.1 §13、§18（匿名成員、彈性陣列成員）、6.7.6.3 §7（`[static]`）、6.7.9 §19 與註腳 151（初始化覆寫）
  - **前提**：會查條款編號即可（[Ch 4](./04-reading-the-standard.md)）；這幾條都很短，值得逐字讀
- **[GCC Common Function / Variable / Type Attributes](https://gcc.gnu.org/onlinedocs/gcc/Common-Attributes.html)**
  - **讀哪裡**：`cleanup`、`counted_by`、`format`、`nonnull`、`warn_unused_result`、`transparent_union` 各條；特別是 `nonnull` 段落裡「無法停用最佳化」那一句
  - **為什麼值得讀**：本章所有擴充的權威定義，版本差異也記在這裡
- **[Linux 核心 `include/linux/cleanup.h`](https://github.com/torvalds/linux/blob/master/include/linux/cleanup.h)**
  - **讀哪裡**：檔案開頭的長註解，說明 `__free`、`guard`、`no_free_ptr` 的設計與「不要和 goto 混用」的規則
  - **前提**：讀過本章 cleanup 一節即可

### 部落格 / 技術文章

- **[Handles are the better pointers](https://floooh.github.io/2018/06/17/handles-vs-pointers.html)** — Andre Weissflog（2018）
  - **這篇說什麼**：sokol 作者解釋「0 代表預設值」與 handle 設計的動機，與本章具名參數一節、下一章 handle 一節直接相關
  - **讀哪裡**：整篇，約 15 分鐘
- **[Implementing smart pointers for the C programming language](https://snai.pe/posts/c-smart-pointers)** — Snaipe
  - **這篇說什麼**：用 cleanup 屬性實作 `unique_ptr`/`shared_ptr`（libcsptr），並坦白指出 cleanup 不能附在參數與回傳值上的根本限制
  - **為什麼值得讀**：看清楚 cleanup 屬性能走多遠、哪裡是牆
- **LWN：[Safer flexible arrays for the kernel](https://lwn.net/Articles/908817/)、[Bounded flexible arrays in C](https://lwn.net/Articles/936728/)**
  - **這篇說什麼**：Linux 核心把 `[0]`、`[1]` 改寫成 `[]`、導入 `-fstrict-flex-arrays=3` 與 `__counted_by` 的過程
  - **前提**：本章彈性陣列成員一節

### 原始碼

- **[Redis `src/sds.h`](https://github.com/redis/redis/blob/unstable/src/sds.h)、[stb_ds.h](https://github.com/nothings/stb/blob/master/stb_ds.h)、[talloc.c](https://github.com/samba-team/samba/blob/master/lib/talloc/talloc.c)**
  - **讀哪裡**：sds.h 的 `sdshdr8` 與 `SDS_HDR`；stb_ds.h 開頭的 NOTES；talloc.c 的 `struct talloc_chunk` 註解與 `_tc_free_internal()`
  - **能學到什麼**：本章三個記憶體布局技巧的工業級版本

### 書籍

- **《Modern C》** — Jens Gustedt（Manning；作者網站提供免費 PDF，較新版本涵蓋 C23，版次與年份請以出版社頁面為準）
  - **這本書的定位**：WG14 成員寫的現代 C，對 compound literal、VLA 型別、C23 `auto`/`typeof` 的解說比本章更系統化
  - **讀哪幾章**：Level 2 中講初始化與陣列的章節、以及 C23 新特徵的部分（章節編號隨版本變動，以目錄為準）

> 和 `programming/c_interview/` 的關係：結構、union 與 bit-field 的面試速查見 [../c_interview/04-struct-union-bitfield.md](../c_interview/04-struct-union-bitfield.md)，記憶體池的實作題見 [../c_interview/13-memory-pool.md](../c_interview/13-memory-pool.md)。

這一章的技巧多半在「讓程式更好寫、更安全」。下一章轉向另一類技巧：在指標的位元裡藏資料、用 handle 取代指標、以及「這樣寫比較快」的說法——每一條都要實測。

→ [Ch 33 技巧 II：指標手法與效能實測](./33-tricks-pointers-and-performance.md)
