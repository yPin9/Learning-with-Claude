# Ch 29 — 物件導向 C：封裝、vtable 與繼承

> **目標**：能在純 C 中寫出封裝（不完整型別）、多型（vtable）、繼承（結構嵌入）三件事，並說清楚每一招背後的語言保證（規格書條款）與機械碼成本；能用 C 實作 Observer、Strategy 等設計模式，讀懂 oltk、通訊模組、Stack ADT 這類案例。

> **對應原文**：[你所不知道的 C 語言：物件導向程式設計篇](https://hackmd.io/@sysprog/c-oop) 的「先來範例」「物件導向是一種態度」「本篇用到的 C 語言機制」「案例探討：oltk」「Design Patterns in C」「案例分析：通訊系統的可擴充介面」「案例分析：Stack ADT」「案例分析：Server Framework in modern C」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14。

## 為什麼需要這個？

「C 不是物件導向語言，所以 C 寫不出物件導向程式」這句話混淆了兩件事：**語言特徵**與**設計方法**。物件導向的核心是一種組織程式的態度：把「一組資料」與「能處理這組資料的動作」看成一個整體，對外只暴露動作、隱藏資料的長相，並允許不同的實作以同一介面被使用。class、`virtual`、`private` 這些關鍵字只是讓編譯器替你檢查紀律的輔助工具。

證據就在你每天用的軟體裡。Linux 核心的 VFS 讓 `read(2)` 不用知道底下是 ext4、NFS 還是 `/dev/null`；SQLite 的 VFS 讓同一份資料庫引擎跑在 Unix、Windows 和記憶體上；CPython 的每個物件都以 `PyObject` 開頭，靠一張型別表決定 `repr()` 要呼叫誰；GTK 整套 GUI 建立在用 C 寫的 GObject 型別系統上。這些系統選擇 C，不是因為作者不知道 C++，而是需要 C 的 ABI 穩定性、可預測的記憶體布局、以及不依賴語言 runtime。

更實際的理由：**懂了 C 怎麼「手工」做出物件導向，你就知道 C++ 編譯器替你做了什麼**。vptr 放在物件的哪裡、虛擬呼叫是兩次載入加一次間接跳躍、為什麼解構子要是 virtual，這些面試常考的問題，在 C 裡全部攤在陽光下。

原文用一句話破題：「物件導向著重於思維，語法只是輔助」。連 bash（bash-oo-framework）和 Brainfuck 都有人寫出物件導向風格的程式，C 當然可以。

## 先建立直覺

把一個 C++ 物件想成一張表格，C 的版本就是把編譯器偷偷做的事情寫出來：

```
C++ 寫法                          C 的等價物（本章要親手做的）
──────────────────────────────    ──────────────────────────────────────────
class Shape {                     struct ShapeVtbl {              ← 每個「類別」一份，共用
  virtual double area();              double (*area)(const Shape *);
  virtual ~Shape();                   void (*destroy)(Shape *);
  std::string name;               };
};                                struct Shape {
                                      const struct ShapeVtbl *vptr; ← 編譯器藏起來的 vptr
                                      const char *name;
                                  };

class Circle : public Shape {     typedef struct {
  double r;                           Shape base;    ← 繼承 = 把基底放在第一個成員
};                                    double r;
                                  } Circle;

s->area();                        s->vptr->area(s);  ← this 指標要自己傳

private: int value;               標頭檔只寫 struct counter;   ← 不完整型別
                                  欄位只在 .c 檔裡看得到
```

三根柱子：

| OOP 概念 | C 的手法 | 依賴的語言保證 |
|---|---|---|
| 封裝 | 不完整型別（opaque pointer） | 可以有指向不完整型別的指標（N1570 6.2.5 §1、§22） |
| 多型 | 函式指標表（vtable） | 函式指標可以存放、比較、呼叫（6.3.2.3 §8、6.5.2.2） |
| 繼承 | 基底結構放在第一個成員 | 指向結構的指標可轉為指向第一個成員（6.7.2.1 §15） |

> 本章大量用到函式指標與 `void *`，如果還不熟，先回看 [Ch 9 — void *、指標的指標、function pointer](./09-void-pointer-and-callbacks.md)；不完整型別的規格定義見 [Ch 7 — 規格書裡的指標](./07-pointers-by-the-standard.md)。

## 第一步：資料與方法放在一起

最直接的作法：資料與函式指標放進同一個結構，函式的第一個參數明確傳入物件本身，扮演其他語言中隱含的 `this`/`self`。

```c
#include <stdio.h>
#include <stdlib.h>

typedef struct object Object;
typedef int (*func_t)(Object *);

struct object {
    int a, b;
    func_t add, sub;
};

static int add_impl(Object *self) { return self->a + self->b; }
static int sub_impl(Object *self) { return self->a - self->b; }

int init_object(Object **self)
{
    if (!(*self = malloc(sizeof(Object))))
        return -1;
    (*self)->a = 0, (*self)->b = 0;
    (*self)->add = add_impl, (*self)->sub = sub_impl;
    return 0;
}

/* the wrong version: changes only its own copy of the pointer */
int init_object_wrong(Object *self)
{
    self = malloc(sizeof(Object));
    return self ? 0 : -1;
}

int main(void)
{
    Object *o = NULL;
    if (init_object(&o) < 0)
        return 1;
    o->a = 9922, o->b = 5566;
    printf("add = %d, sub = %d\n", o->add(o), o->sub(o));
    printf("sizeof(Object) = %zu\n", sizeof(Object));
    free(o);

    Object *w = NULL;
    init_object_wrong(w);
    printf("after init_object_wrong: w = %p\n", (void *) w);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g obj.c -o obj && ./obj
add = 15488, sub = 4356
sizeof(Object) = 24
after init_object_wrong: w = (nil)
$ gcc -g -fsanitize=address obj.c -o obj_asan && ./obj_asan
...
Direct leak of 24 byte(s) in 1 object(s) allocated from:
SUMMARY: AddressSanitizer: 24 byte(s) leaked in 1 allocation(s).
```

`init_object` 為什麼要接收 `Object **`？C 只有 call-by-value，函式拿到的是引數的複本。`init_object_wrong` 把 `malloc` 的結果寫進自己的複本 `self`，呼叫端的 `w` 仍是 `NULL`，配置的 24 位元組永遠找不回來，ASan 的 LeakSanitizer 當場抓到。傳入 `&o` 才能改寫呼叫端的指標本身；回傳值則留給錯誤碼。這就是 [Ch 18](./18-linked-list-pointer-to-pointer.md) 談過的「指標的指標」最常見的用途。

這個版本的問題：

1. **每個物件都帶一份函式指標**：`sizeof(Object)` = 24，其中 16 位元組是兩個函式指標。一百萬個物件就多花 16 MB 存同樣的兩個位址。
2. **成員全部公開**：呼叫端可以任意改寫 `o->a`，甚至 `o->add = NULL`。這只是「資料與方法的結合」，還稱不上封裝。

下面兩節分別解決這兩個問題。

## 封裝：不完整型別

C 的封裝手法是**不完整型別**（incomplete type）：標頭檔只宣告 `struct counter` 存在，結構的內容只出現在實作檔。呼叫端只能持有指標並呼叫公開函式。

```c
/* counter.h */
#ifndef COUNTER_H
#define COUNTER_H
/* callers see only an incomplete type */
typedef struct counter Counter;

Counter *counter_new(int start);
void counter_increment(Counter *c);
int counter_value(const Counter *c);
void counter_free(Counter *c);
#endif
```

```c
/* counter.c: the only place that knows the layout */
#include <stdlib.h>
#include "counter.h"

struct counter {
    int value;
    unsigned long ops;      /* private bookkeeping, invisible to callers */
};

Counter *counter_new(int start)
{
    Counter *c = malloc(sizeof(*c));
    if (c)
        c->value = start, c->ops = 0;
    return c;
}

void counter_increment(Counter *c) { c->value++; c->ops++; }
int counter_value(const Counter *c) { return c->value; }
void counter_free(Counter *c) { free(c); }
```

```c
/* use_counter.c */
#include <stdio.h>
#include "counter.h"

int main(void)
{
    Counter *c = counter_new(41);
    counter_increment(c);
    printf("value = %d\n", counter_value(c));
#ifdef PEEK
    c->value = 0;                       /* not allowed: layout unknown here */
    Counter on_stack;                   /* not allowed: size unknown here */
    printf("%zu\n", sizeof(Counter));   /* not allowed */
#endif
    counter_free(c);
    return 0;
}
```

```
$ gcc -Wall -Wextra counter.c use_counter.c -o counter && ./counter
value = 42
$ gcc -DPEEK -c use_counter.c
use_counter.c:10:6: error: invalid use of incomplete typedef ‘Counter’ {aka ‘struct counter’}
use_counter.c:11:13: error: storage size of ‘on_stack’ isn’t known
use_counter.c:12:28: error: invalid application of ‘sizeof’ to incomplete type ‘Counter’ {aka ‘struct counter’}
$ clang -DPEEK -c use_counter.c
use_counter.c:10:6: error: incomplete definition of type 'struct counter'
use_counter.c:11:13: error: variable has incomplete type 'Counter' (aka 'struct counter')
use_counter.c:12:21: error: invalid application of 'sizeof' to an incomplete type 'Counter' (aka 'struct counter')
```

三個錯誤揭露了不完整型別的完整語意：在 `use_counter.c` 裡，`struct counter` 的**大小與成員都未知**，所以不能存取成員、不能在 stack 上宣告物件、不能 `sizeof`；但**指向它的指標**是完整的物件型別（所有結構指標大小與表示法相同，N1570 6.2.5 §28），可以存放、傳遞、比較。

### 為什麼這是真正的封裝

- **ABI 穩定**：`counter.c` 新增 `ops` 欄位，`use_counter.c` 不需要重新編譯，因為它從頭到尾不知道結構大小。這正是 C 函式庫能在不破壞 ABI 的前提下演進內部結構的原因。如果把結構定義放在標頭檔裡，加一個欄位就會讓所有已編譯的呼叫端讀錯偏移量。
- **不變式（invariant）有唯一守門人**：`value` 與 `ops` 永遠一起遞增，因為只有 `counter.c` 能碰它們。
- C++ 社群稱這種手法 **opaque pointer** 或 **pimpl idiom**（pointer to implementation）。

### 代價與誤解

- **必須配置在 heap**：呼叫端不知道大小，物件只能由實作檔配置。對大量小物件，這是額外的 `malloc` 成本與 cache 不友善。替代方案是公開大小但不公開欄位（例如提供 `counter_sizeof()` 讓呼叫端自行配置，或公開一個只有 `char opaque[N]` 的結構），代價是 ABI 又和大小綁在一起。
- **函式呼叫無法行內展開**：`counter_value()` 定義在另一個翻譯單元，除非開 LTO（`-flto`），每次存取都是一次真正的函式呼叫。
- **`FILE` 不是不完整型別**：標準函式庫的 `FILE *` 精神相近，但 C 標準把 `FILE` 規定為物件型別（N1570 7.21.1 §2），glibc 在 `<stdio.h>` 中公開了完整定義（`struct _IO_FILE`）。所以 `FILE` 只是「慣例上不要碰」，不是編譯器強制的封裝。

## 多型：vtable

第一個版本每個物件都帶函式指標。同一類物件的方法通常一樣，所以把方法集中到一張**只含函式指標的表**（vtable），每個類別一份，物件只存指向它的指標（vptr）。下面這個例子同時示範多型、繼承、覆寫、呼叫基底版本（super）與虛擬解構子：

```c
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

/* ---------- base class ---------- */
typedef struct Shape Shape;
struct ShapeVtbl {                         /* one table per class, shared by all objects */
    const char *class_name;
    double (*area)(const Shape *self);
    void (*describe)(const Shape *self);
    void (*destroy)(Shape *self);          /* "virtual destructor" */
};
struct Shape {
    const struct ShapeVtbl *vptr;          /* first member: the hidden vptr of C++ */
    const char *name;
};

static void shape_describe(const Shape *self)   /* base implementation */
{
    printf("%-8s %-6s area=%7.2f\n", self->vptr->class_name, self->name,
           self->vptr->area(self));
}
static inline double shape_area(const Shape *s) { return s->vptr->area(s); }
static inline void shape_describe_v(const Shape *s) { s->vptr->describe(s); }
static inline void shape_destroy(Shape *s) { s->vptr->destroy(s); }

/* ---------- derived class 1: Circle (base is first member) ---------- */
typedef struct { Shape base; double r; } Circle;
static double circle_area(const Shape *s) { const Circle *c = (const Circle *) s; return M_PI * c->r * c->r; }
static void circle_destroy(Shape *s) { printf("  ~Circle(%s)\n", s->name); free(s); }
static const struct ShapeVtbl circle_vtbl = {
    .class_name = "Circle", .area = circle_area,
    .describe = shape_describe, .destroy = circle_destroy,
};
Shape *circle_new(const char *name, double r)
{
    Circle *c = malloc(sizeof *c);
    if (!c) return NULL;
    *c = (Circle){ .base = { &circle_vtbl, name }, .r = r };
    return &c->base;                      /* upcast: free and always safe */
}

/* ---------- derived class 2: Rect ---------- */
typedef struct { Shape base; double w, h; } Rect;
static double rect_area(const Shape *s) { const Rect *r = (const Rect *) s; return r->w * r->h; }
static void rect_destroy(Shape *s) { printf("  ~Rect(%s)\n", s->name); free(s); }
static const struct ShapeVtbl rect_vtbl = {
    .class_name = "Rect", .area = rect_area,
    .describe = shape_describe, .destroy = rect_destroy,
};
Shape *rect_new(const char *name, double w, double h)
{
    Rect *r = malloc(sizeof *r);
    if (!r) return NULL;
    *r = (Rect){ .base = { &rect_vtbl, name }, .w = w, .h = h };
    return &r->base;
}

/* ---------- derived from Rect: Square overrides describe and calls "super" ---------- */
typedef struct { Rect rect; int tagged; } Square;
static void square_describe(const Shape *s)
{
    shape_describe(s);                     /* super.describe() */
    printf("         (a square, side %.1f)\n", ((const Rect *) s)->w);
}
static void square_destroy(Shape *s) { printf("  ~Square -> "); rect_destroy(s); }
static const struct ShapeVtbl square_vtbl = {
    .class_name = "Square", .area = rect_area,      /* inherited */
    .describe = square_describe, .destroy = square_destroy,
};
Shape *square_new(const char *name, double side)
{
    Square *q = malloc(sizeof *q);
    if (!q) return NULL;
    *q = (Square){ .rect = { .base = { &square_vtbl, name }, .w = side, .h = side } };
    return &q->rect.base;
}

int main(void)
{
    Shape *shapes[] = {
        circle_new("c1", 1.0), rect_new("r1", 3, 4), square_new("s1", 2.5),
    };
    double total = 0;
    for (size_t i = 0; i < 3; i++) {
        shape_describe_v(shapes[i]);
        total += shape_area(shapes[i]);
    }
    printf("total area = %.2f\n", total);
    printf("sizeof: Shape=%zu Circle=%zu Rect=%zu Square=%zu, vtbl=%zu\n",
           sizeof(Shape), sizeof(Circle), sizeof(Rect), sizeof(Square),
           sizeof(struct ShapeVtbl));
    for (size_t i = 0; i < 3; i++)
        shape_destroy(shapes[i]);
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra -fsanitize=address,undefined shape.c -o shape -lm && ./shape
Circle   c1     area=   3.14
Rect     r1     area=  12.00
Square   s1     area=   6.25
         (a square, side 2.5)
total area = 21.39
sizeof: Shape=16 Circle=24 Rect=32 Square=40, vtbl=32
  ~Circle(c1)
  ~Rect(r1)
  ~Square ->   ~Rect(s1)
```

ASan 與 UBSan 都沒有回報任何錯誤或洩漏。（`M_PI` 不是 ISO C 的一部分，`-std=gnu11` 下 `<math.h>` 才會定義。）

### 底層機制：記憶體布局

```
Square 物件 (heap, 40 bytes)                      square_vtbl (.rodata / .data.rel.ro)
┌──────────────────────────────┐ ◄── Shape *s     ┌──────────────────────────┐
│ base.vptr  ──────────────────┼────────────────► │ class_name → "Square"    │ +0
│ base.name  → "s1"            │  Rect 的開頭     │ area       → rect_area   │ +8  ← 繼承
├──────────────────────────────┤  也是 Shape      │ describe   → square_desc │ +16 ← 覆寫
│ w = 2.5                      │  的開頭           │ destroy    → square_dest │ +24 ← 覆寫
│ h = 2.5                      │                  └──────────────────────────┘
├──────────────────────────────┤
│ tagged (+ padding)           │       Square、Rect、Shape 三者的起始位址相同
└──────────────────────────────┘
```

**為什麼 `(const Rect *) s` 是合法的？** 關鍵是 N1570 6.7.2.1 §15（C99 為 §13，C23 為 N3220 6.7.3.2 §17）：

> A pointer to a structure object, suitably converted, points to its initial member (or if that member is a bit-field, then to the unit in which it resides), and vice versa. There may be unnamed padding within a structure object, but not at its beginning.

結構開頭**不會有 padding**，所以 `Square *`、`&square->rect`、`&square->rect.base` 三個指標指向同一個位址，轉型前後都指向真正存在的那個物件。這把「has-a」（Square 擁有一個 Rect）變成「is-a」（Square 可以當 Rect 用）。注意這條保證的方向是「轉回**真正的**型別」：把一個實際是 `Circle` 的 `Shape *` 轉成 `Rect *` 再讀 `w`，就是存取不存在的物件，是未定義行為。C 不會替你檢查 downcast，Ch 30 會看到 QEMU、GObject 怎麼在執行時期補上這個檢查。

### 底層機制：一次虛擬呼叫的成本

把 `shape_area` 單獨拿出來看組合語言：

```c
typedef struct Shape Shape;
struct ShapeVtbl { const char *class_name; double (*area)(const Shape *); };
struct Shape { const struct ShapeVtbl *vptr; };
double call_area(const Shape *s) { return s->vptr->area(s); }

static double sq_area(const Shape *s) { (void) s; return 4.0; }
static const struct ShapeVtbl sq_vtbl = { "Sq", sq_area };
double known_type(void) { Shape s = { &sq_vtbl }; return call_area(&s); }
```

```
$ gcc -O2 -S -fno-asynchronous-unwind-tables -fcf-protection=none vcall.c
call_area:
	movq	(%rdi), %rax        # rax = s->vptr          （第 1 次載入）
	jmp	*8(%rax)            # 跳到 vptr->area        （第 2 次載入 + 間接跳躍）
known_type:
	movsd	.LC0(%rip), %xmm0   # 直接回傳 4.0
	ret
```

- 虛擬呼叫 = **兩次記憶體載入 + 一次間接分支**。`area` 在 vtable 中的偏移量是 8（`class_name` 佔前 8 位元組），寫死在指令裡，這就是 C++ 編譯器替每個虛擬函式分配「槽位」的意思。因為是函式最後一個動作，gcc 把 `call` + `ret` 變成 `jmp`（尾端呼叫）。
- `known_type` 裡編譯器看得到 `s.vptr` 一定是 `&sq_vtbl`，於是**去虛擬化**（devirtualization）：直接行內展開 `sq_area`，整個函式只剩回傳常數。這也是把 vtable 宣告成 `static const` 的另一個好處：編譯器知道它不會被改。
- 真正的成本通常不在指令數，而在**無法行內展開**（後續最佳化全部失效）與**間接分支預測失誤**。在熱迴圈中對大量混雜型別的物件做虛擬呼叫，才需要擔心；Ch 31 會實測間接跳躍在現代 CPU 上的表現。

### vtable 為什麼要 `const`

```c
static const struct comm_ops tcp_ops = {.open = tcp_open, .close = tcp_close};
...
    ((struct comm_ops *) tcp.ops)->open = http_open;   /* try to patch a const table */
```

```
$ gcc -DWRITE_VTBL comm.c -o commw && ./commw
Opening TCP: bigiron.box.com:5000
Opening HTTP: https://example.com/
Segmentation fault (core dumped)
$ readelf -SW commw | grep data.rel.ro ; nm commw | grep tcp_ops
  [23] .data.rel.ro      PROGBITS        0000000000003da0 002da0 000020 00  WA  0   0 16
0000000000003da0 d tcp_ops
$ gcc -no-pie -fno-pie -DWRITE_VTBL comm.c -o commnp && nm commnp | grep tcp_ops
0000000000402040 r tcp_ops
```

寫入 `const` 物件本身是未定義行為（N1570 6.7.3 §6），在這裡的實際表現是 segmentation fault。有趣的是放在哪裡：預設的 PIE 執行檔中，`tcp_ops` 存的是函式**位址**，要等載入時重定位才知道，所以進了 `.data.rel.ro`（載入時可寫、重定位完成後由 RELRO 機制改為唯讀）；`-no-pie` 時位址在連結時就確定，直接放進 `.rodata`（`nm` 標記 `r`）。兩種情況執行時都是唯讀頁面。

這不只是正確性問題，也是安全問題。函式指標是攻擊者夢寐以求的目標：改寫一個可寫的函式指標，下一次呼叫就跳到攻擊者指定的位址（[Ch 14](./14-function-call-security.md) 談過控制流劫持）。Linux 核心的 `Documentation/security/self-protection.rst` 因此要求 operations 結構盡量宣告為 `const`，放進受記憶體權限保護的區域。重定位與 RELRO 的細節見 [Ch 40](./40-dynamic-linking.md)。

## 案例：通訊系統的可擴充介面

改寫自 Stack Overflow 的經典討論。通訊模組要同時支援 RS-232、TCP、HTTP，呼叫端希望用同一種方式操作：

```c
#include <stdio.h>

typedef struct comm Comm;
struct comm_ops {
    int (*open)(Comm *self, const char *spec);
    int (*close)(Comm *self);
};
struct comm {
    const struct comm_ops *ops;
};

static int tcp_open(Comm *self, const char *spec)
{
    (void) self;
    printf("Opening TCP: %s\n", spec);
    return 0;
}
static int tcp_close(Comm *self) { (void) self; return 0; }

static int http_open(Comm *self, const char *spec)
{
    (void) self;
    printf("Opening HTTP: %s\n", spec);
    return 0;
}
static int http_close(Comm *self) { (void) self; return 0; }

static const struct comm_ops tcp_ops = {.open = tcp_open, .close = tcp_close};
static const struct comm_ops http_ops = {.open = http_open, .close = http_close};

int main(void)
{
    Comm tcp = {.ops = &tcp_ops}, http = {.ops = &http_ops};
    Comm *links[] = {&tcp, &http};
    const char *specs[] = {"bigiron.box.com:5000", "https://example.com/"};

    /* Called in exactly the same manner */
    for (size_t i = 0; i < 2; i++) {
        links[i]->ops->open(links[i], specs[i]);
        links[i]->ops->close(links[i]);
    }
    return 0;
}
```

```
Opening TCP: bigiron.box.com:5000
Opening HTTP: https://example.com/
```

新增一種傳輸方式只要多寫一組函式和一張表，分派用的 `links[i]->ops->open(...)` 完全不用改，這就是「對擴充開放、對修改封閉」（open-closed principle）。操作表用 designated initializer（C99）填寫，沒列出的成員依 N1570 6.7.9 §19 比照 static 物件初始化為 null pointer，所以操作表可以只填實作了的方法，Linux 的 `file_operations` 正是如此（Ch 30）。libmodbus 用同樣的結構支援 Modbus 的 RTU 與 TCP 兩種後端，在建立 context 時選定。

## 案例：Stack ADT 與三種綁定時機

```c
#include <stdio.h>
#include <stdlib.h>

typedef int thing;
struct stack;

struct stack_type {
    void (*push)(struct stack *self, thing t);
    thing (*pop)(struct stack *self);
};

struct stack {
    const struct stack_type *type; /* the "class" this object belongs to */
    thing items[16];               /* what C++ would put after private: */
    int top;
};

static void stack_push(struct stack *self, thing t) { self->items[self->top++] = t; }
static thing stack_pop(struct stack *self) { return self->items[--self->top]; }

static const struct stack_type Stack = {.push = stack_push, .pop = stack_pop};

static struct stack *stack_new(void)
{
    struct stack *st = calloc(1, sizeof(*st));
    if (st)
        st->type = &Stack;
    return st;
}

int main(void)
{
    struct stack *st = stack_new();
    if (!st)
        return 1;
    stack_push(st, 1);         /* non-virtual call */
    Stack.push(st, 2);         /* call through the class table */
    st->type->push(st, 3);     /* virtual call through the object */
    while (st->top)
        printf("%d ", st->type->pop(st));
    printf("\n");
    free(st);
    return 0;
}
```

輸出 `3 2 1`。三種呼叫的差別在**綁定時機**：

| 寫法 | 何時決定呼叫哪個函式 | C++ 對應 |
|---|---|---|
| `stack_push(st, 1)` | 編譯時期 | 非虛擬成員函式、`Base::f()` 明確限定 |
| `Stack.push(st, 2)` | 編譯時期決定「哪張表」，執行時期讀表 | 很少直接對應；類似透過已知類別的 vtable 呼叫 |
| `st->type->push(st, 3)` | 執行時期，依物件所屬類別 | 虛擬函式呼叫 |

名稱空間方面，C 以前綴模擬：`stack_push(st, t)` 對應 C++ 的 `stack::push(t)`。範例為了精簡省略了堆疊滿溢與空堆疊檢查，`items[16]` 這個固定大小在真實程式中必須檢查。

## 案例：oltk 的 graphics backend

oltk（Openmoko 的小型 GUI toolkit）要同時支援 Linux framebuffer（fbdev）與 X11，讓開發者在桌面看到的畫面與裝置上一致。`gr_impl.h` 宣告了兩個 backend：

```c
struct gr_backend {
    const char *name;
    struct gr *(*open)(const char *, int width, int height, int nonblock);
};
extern struct gr_backend gr_x11_backend;
extern struct gr_backend gr_fb_backend;
```

`gr.c` 在編譯時期由 `HAVE_X11`、`HAVE_FB` 決定可用的 backend，初始化時逐一嘗試：

```c
static const struct gr_backend *backends[] = {
#ifdef HAVE_X11
    &gr_x11_backend,
#endif
#ifdef HAVE_FB
    &gr_fb_backend,
#endif
};
static const int n_backends = sizeof(backends) / sizeof(backends[0]);

struct gr *gr_open(const char *dev, int nonblock)
{
    struct gr *gr = NULL;
    for (int i = 0; i < n_backends; i++) {
        gr = backends[i]->open(dev, 480, 640, nonblock);
        if (gr)
            break;
        printf("gr_open: backend %d bad\n", i);
    }
    return gr;
}
```

`struct gr_backend` 的 `open` 是 **factory**（工廠方法）：負責建構並回傳 `struct gr` 物件。真正的虛擬函式在 `struct gr` 裡（`gr.h`）：

```c
struct gr {
    int width;
    int height;
    ...
    void (*close)(struct gr *);
    void (*update)(struct gr *, struct gr_rectangle *, int);
    void (*set_color)(struct gr *, unsigned int, struct gr_rgb *, unsigned int);
    int (*get_color)(struct gr *, unsigned int);
    int (*sample)(struct gr *gr, struct gr_sample *);
};
```

兩個平台實作都把 `struct gr` 放在自己結構的第一個成員：

```c
struct gr_fb  { struct gr gr; ... };
struct gr_x11 { struct gr gr; ... };
```

這就是前面 Shape 範例的同一套手法。注意 oltk 的方法指標放在**物件內**而不是共用 vtable：`gr` 物件整個程式只有一個，省下一次間接存取比省記憶體更重要。Ch 30 會看到 Linux 核心的 LWN 文章把這種取捨講得很清楚。

> **本段未實測**：oltk 需要 Openmoko 的建置環境與 X11/fbdev，以上依原文與原始碼閱讀整理；設計手法已在 Shape 範例中驗證。

## Design Patterns in C

1994 年 GoF（Gamma、Helm、Johnson、Vlissides）的《Design Patterns》在最後一章自己說：「這本書沒有提出任何前所未有的演算法或程式設計技術……它只是對現有設計成果的一種審視。」模式的價值不在程式碼，而在**問題與情境的命名**：「這裡適合用 Observer」比「這裡適合讓資料擁有者主動回呼一串函式指標」溝通效率高得多。

C 的優勢是能看清楚每個模式「剝掉語法後」剩下什麼。以下兩個是 C 系統程式最常見的模式，各附一個完整可跑的實作。原文另外帶讀了 [OOC-Design-Pattern](https://github.com/QMonkey/OOC-Design-Pattern) 專案，它為 GoF 23 種模式各寫一個 C 版本。

### Observer：主動通知，取代輪詢

情境（取自原文引用的 Teddy Chen 範例）：熱門商品「售完補貨中」，想知道何時到貨。輪詢（定期回網站查）浪費時間，搬去倉庫住（緊密耦合）很蠢。解法：購物網站（Subject）提供「貨到通知」，顧客（Observer）加入名單（attach），到貨時網站通知（notify），沒興趣了就退出（detach）。

```c
#include <stdio.h>
#include <stdlib.h>

/* ---- interfaces ---- */
typedef struct Observer Observer;
struct ObserverOps { void (*update)(Observer *self, const char *item, int stock); };
struct Observer { const struct ObserverOps *ops; };

#define MAX_OBSERVERS 8
typedef struct {                     /* Subject: the shop */
    const char *item;
    int stock;
    Observer *obs[MAX_OBSERVERS];
    size_t n;
} Product;

static int product_attach(Product *p, Observer *o)
{
    if (p->n == MAX_OBSERVERS) return -1;
    p->obs[p->n++] = o;
    return 0;
}
static void product_detach(Product *p, Observer *o)
{
    for (size_t i = 0; i < p->n; i++)
        if (p->obs[i] == o) { p->obs[i] = p->obs[--p->n]; return; }
}
static void product_restock(Product *p, int qty)
{
    int was_empty = p->stock == 0;
    p->stock += qty;
    if (was_empty && p->stock > 0)               /* notify only on the edge */
        for (size_t i = 0; i < p->n; i++)
            p->obs[i]->ops->update(p->obs[i], p->item, p->stock);
}

/* ---- concrete observer 1: e-mail customer ---- */
typedef struct { Observer base; const char *email; } EmailCustomer;
static void email_update(Observer *self, const char *item, int stock)
{
    EmailCustomer *c = (EmailCustomer *) self;
    printf("  mail to %-16s: %s is back (%d left)\n", c->email, item, stock);
}
static const struct ObserverOps email_ops = { email_update };

/* ---- concrete observer 2: a counter that knows nothing about e-mail ---- */
typedef struct { Observer base; int hits; } Stats;
static void stats_update(Observer *self, const char *item, int stock)
{
    (void) item, (void) stock;
    ((Stats *) self)->hits++;
}
static const struct ObserverOps stats_ops = { stats_update };

int main(void)
{
    Product ps5 = { .item = "PS5", .stock = 0 };
    EmailCustomer alice = { { &email_ops }, "alice@example.com" };
    EmailCustomer bob   = { { &email_ops }, "bob@example.com" };
    Stats stats = { { &stats_ops }, 0 };

    product_attach(&ps5, &alice.base);
    product_attach(&ps5, &bob.base);
    product_attach(&ps5, &stats.base);

    puts("restock 3:");  product_restock(&ps5, 3);
    puts("restock 2 (not empty before, no notify):");  product_restock(&ps5, 2);
    ps5.stock = 0;
    product_detach(&ps5, &bob.base);
    puts("sold out, bob detached, restock 1:");  product_restock(&ps5, 1);
    printf("stats observer saw %d notifications\n", stats.hits);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra observer.c -o observer && ./observer
restock 3:
  mail to alice@example.com: PS5 is back (3 left)
  mail to bob@example.com : PS5 is back (3 left)
restock 2 (not empty before, no notify):
sold out, bob detached, restock 1:
  mail to alice@example.com: PS5 is back (1 left)
stats observer saw 2 notifications
```

`Product` 只認識 `Observer` 介面，完全不知道 e-mail 或統計的存在；兩種完全無關的觀察者共用同一個通知機制。幾個實務重點：

- **只在邊緣（edge）通知**：「從 0 變成正數」才通知，避免每次補貨都打擾所有人。
- **通知中修改名單是經典 bug**：如果 `update` 裡呼叫 `product_detach` 移除自己，`product_restock` 的迴圈索引就會跳過下一個觀察者（`detach` 把最後一個搬到被刪的位置）。成熟的實作（例如 GObject signal）會在發送期間延後刪除或複製名單。
- **thundering herd**：通知同時湧向大量接收者，接收者又同時回頭存取資源，會在同一時間點壅塞，網路程式尤其常見。
- **生命週期**：觀察者被釋放前必須 `detach`，否則 Subject 持有懸置指標。這就是 Ch 30 的參照計數要解決的問題。

GObject 的 signal 是 Observer 的工業級實作：`g_signal_connect()` 掛上回呼，`g_signal_emit()` 依序呼叫，發出者不知道誰在監聽。

### Strategy：把演算法從物件身上抽離

情境：壓縮檔案物件可以用 zip、rar、7z 等演算法。全部寫進同一個物件用 `if-else` 選擇，物件會肥大、難以獨立測試、新增演算法要改舊程式碼；用繼承讓每個子類別重寫演算法，又會產生一堆只差一點點的類別。Strategy 的解法：為演算法定義介面，每種演算法一個具體實作，Context 持有指向介面的指標，執行時期抽換。

```c
#include <stdio.h>
#include <string.h>

/* Strategy interface: how to compress a buffer (toy algorithms) */
typedef struct {
    const char *name;
    size_t (*compress)(const char *in, char *out, size_t cap);
} Codec;

static size_t copy_compress(const char *in, char *out, size_t cap)
{
    size_t n = strlen(in);
    if (n + 1 > cap) return 0;
    memcpy(out, in, n + 1);
    return n;
}

static size_t rle_compress(const char *in, char *out, size_t cap)   /* "aaab" -> "3a1b" */
{
    size_t o = 0;
    for (size_t i = 0; in[i]; ) {
        size_t j = i;
        while (in[j] == in[i] && j - i < 9) j++;
        if (o + 2 >= cap) return 0;
        out[o++] = (char) ('0' + (j - i));
        out[o++] = in[i];
        i = j;
    }
    out[o] = '\0';
    return o;
}

static const Codec CODEC_COPY = { "copy", copy_compress };
static const Codec CODEC_RLE  = { "rle",  rle_compress };

/* Context: an archiver that holds a strategy and can swap it at run time */
typedef struct { const Codec *codec; } Archiver;

static void archive(const Archiver *a, const char *data)
{
    char buf[64];
    size_t n = a->codec->compress(data, buf, sizeof buf);
    printf("%-5s %-14s -> %-14s (%zu bytes)\n", a->codec->name, data, buf, n);
}

int main(void)
{
    Archiver a = { &CODEC_COPY };
    const char *inputs[] = { "aaaabbbccd", "abcdef" };
    for (int i = 0; i < 2; i++) archive(&a, inputs[i]);
    a.codec = &CODEC_RLE;                     /* swap the algorithm, Archiver unchanged */
    for (int i = 0; i < 2; i++) archive(&a, inputs[i]);
    /* choose by name, e.g. from a config file */
    const Codec *all[] = { &CODEC_COPY, &CODEC_RLE };
    for (size_t i = 0; i < 2; i++)
        if (!strcmp(all[i]->name, "rle")) printf("picked %s from table\n", all[i]->name);
    return 0;
}
```

```
copy  aaaabbbccd     -> aaaabbbccd     (10 bytes)
copy  abcdef         -> abcdef         (6 bytes)
rle   aaaabbbccd     -> 4a3b2c1d       (8 bytes)
rle   abcdef         -> 1a1b1c1d1e1f   (12 bytes)
picked rle from table
```

`abcdef` 經 RLE「壓縮」後反而變成 12 位元組：這正是要能抽換策略的理由，沒有一種演算法對所有輸入都最好。Strategy 是很多模式的原型；後面 Ch 30 的 SQLite VFS、Linux `file_operations` 本質上都是 Strategy。注意這裡的 Strategy 物件沒有狀態，所以一個 `const Codec` 全域實體就夠了，不需要 `malloc`。

### 其他模式在 C 中的樣子

| 模式 | C 的樣子 | 本課的實例 |
|---|---|---|
| Factory | 回傳基底指標的 `*_new()` 或 `open` 函式指標 | oltk 的 `gr_backend.open`、本章 `circle_new` |
| Template Method | 基底函式依序呼叫幾個函式指標「掛鉤」 | nginx 模組的 `postconfiguration`（Ch 30） |
| Decorator | 包住另一個同介面物件，轉呼叫並加料 | SQLite VFS shim（Ch 30） |
| Visitor | 以節點種類為索引的函式指標表；double dispatch | 編譯器走訪 AST（[Ch 38](./38-compiler-construction.md)） |
| Iterator | `for` 迴圈巨集或 `next()` 回呼 | `list_for_each_entry`（[Ch 19](./19-linux-list-head.md)） |
| Singleton | 檔案範圍的 `static` 物件 + 存取函式 | GObject 的 `get_type()` 用 `g_once` 保證只註冊一次 |

Mark Dominus 的〈Design patterns of 1972〉提出一個值得思考的觀點：今天的「模式」往往是語言缺少某個特徵的補丁。C 程式員在 1972 年就用「呼叫慣例」這個模式，因為組合語言沒有函式；今天的 Visitor 在有模式比對的語言裡是一行 `match`。

### 原文案例的語言細節：匿名成員與 `-fms-extensions`

OOC-Design-Pattern 用了一個非標準技巧讓介面方法看起來像被「繼承」：

```c
typedef struct { void (*handle)(void *self); } IObserver;

typedef struct {
    union {
        IObserver;              /* Microsoft extension: unnamed member of a typedef type */
        IObserver iobserver;    /* named alias for passing "the interface" around */
    };
    int id;
} Observer;
...
    o.handle(&o);               /* reach the interface member directly */
```

```
$ gcc -std=c11 -Wall anon.c
anon.c:7:18: warning: declaration does not declare anything
anon.c:18:6: error: ‘Observer’ has no member named ‘handle’
$ gcc -std=gnu11 -fms-extensions -Wall anon.c -o anon && ./anon
observer 7 handled
observer 7 handled
$ clang -fms-extensions anon.c -o anonc
anon.c:7:9: warning: anonymous structs are a Microsoft extension [-Wmicrosoft-anon-tag]
```

C11 的匿名結構（N1570 6.7.2.1 §13）只允許**不帶標籤、當場定義**的 `struct { ... };`，用 typedef 名稱宣告匿名成員是 Microsoft 擴充。這也是為什麼標準 C 的做法一律寫成具名的 `base` 成員，存取時多打 `.base`。

另一個原文提到的細節：`const int OBSERVER_SIZE = 10; Observer *observers[OBSERVER_SIZE];` 在 C 裡**不是**固定大小陣列，而是 VLA，因為 C 的 `const int` 不是整數常數運算式：

```
$ cat vla.c
const int N = 10;
int g[N];
$ gcc -std=c11 vla.c
vla.c:2:5: error: variably modified ‘g’ at file scope
$ gcc -std=c11 -Wvla -c -x c - <<<"void f(void){const int N=10; int a[N]; (void)a;}"
<stdin>:1:1: warning: ISO C90 forbids variable length array ‘a’ [-Wvla]
```

改用 `#define` 或 `enum { OBSERVER_SIZE = 10 };`。這是 C 與 C++ 的著名差異之一（C++ 的 `const int` 以常數初始化時是常數運算式）。

## 案例：Server Framework in modern C

原文的 [server-framework](https://github.com/embedded2016/server-framework) 是以 C99 寫成、物件導向風格的伺服器框架，由三層組成：`async`（執行緒池與工作佇列）、`reactor`（以 epoll/kqueue 監聽 fd 事件並分派）、`protocol-server`（以一組回呼描述協定）。使用者定義協定時填寫一個帶有 `on_open`、`on_data`、`on_close` 等回呼的結構，框架負責所有 I/O 與執行緒管理，這正是 Template Method 加 Strategy：框架決定流程，使用者提供步驟。

依原文，`./test-protocol-server` 會在 8080 埠啟動 echo server，`telnet localhost 8080` 連上後輸入的文字會原樣回傳，輸入 `bye` 結束。

> **本段未實測**：需要 clone 外部專案並開兩個終端機互動，屬於延伸動手項目。建議讀 `protocol-server.h` 中的 `struct Protocol`，對照本章的 vtable 概念。

## 對比與取捨

| 手法 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| 方法指標放在物件內 | 少一次間接；每個物件可個別換方法 | 每個物件多佔 8 位元組 × 方法數；方法指標可寫，安全性差 | 物件極少（oltk 的 `gr`）、需要逐物件客製 |
| 共用 `const` vtable | 省記憶體；可放唯讀區；編譯器可去虛擬化 | 多一次載入 | 物件多、方法固定（絕大多數情況） |
| 不完整型別封裝 | ABI 穩定、真正的資訊隱藏 | 必須 heap 配置、無法行內展開（除非 LTO） | 函式庫公開 API |
| 結構公開、靠命名慣例 | 可 stack 配置、可行內 | 封裝靠紀律 | 效能敏感的內部模組 |
| 基底放第一個成員 | 轉型零成本、有標準保證 | 只能單一繼承 | 主要的「is-a」關係 |
| `container_of`（基底不在首位） | 可多重嵌入、欄位可自由排列 | 需要 `offsetof` 計算、型別錯了編譯器不一定抓得到 | Linux 核心（Ch 30） |
| 直接用 C++ | 編譯器檢查一切 | ABI 不穩定、需 runtime、某些環境不可用 | 沒有上述限制時 |

## 踩雷集錦

1. **以為 C 的 OOP 需要框架或巨集**：一個結構、一張函式指標表、一個命名慣例就是完整的物件導向。巨集（GObject、QOM）是為了減少樣板，不是必要條件。先手寫幾次再引入框架。
2. **downcast 到錯誤的型別**：`(Rect *) shape` 只在 `shape` **真的**是 Rect（或其子類別）時合法。C 不檢查，錯了就是讀到別的物件的記憶體。需要時在 vtable 裡放型別識別（`class_name` 或型別 ID），轉型前先比對，這就是手工 RTTI。
3. **忘記虛擬解構子**：如果 `shape_destroy` 直接 `free(s)` 而不經 vtable，對 `Circle` 剛好正確（起始位址相同），但子類別若持有其他資源（另外 `malloc` 的緩衝區、開啟的 fd）就會洩漏。解構一律經由 vtable。
4. **vtable 不加 `const`**：浪費去虛擬化的機會，還在記憶體中留下一張可寫的函式指標表給攻擊者。
5. **在 `update` 回呼中修改觀察者名單**：迭代中刪除元素會跳過下一個或存取已釋放的物件。發送期間延後刪除，或複製一份名單再迭代。
6. **`const int` 當陣列大小**：在 C 裡會變成 VLA，在檔案範圍則直接編譯錯誤。用 `enum` 或 `#define`。

## 進階：再往深一層

- **多重介面**：一個物件要同時實作「可序列化」與「可比較」兩個介面時，基底只能有一個在第一個成員。解法是在物件內嵌入多個介面結構，介面方法收到內嵌成員的位址後，以 `container_of` 推回外層物件。Chris Wellons 的〈C Object Oriented Programming〉完整示範了這個手法，Ch 30 的 Linux 核心也大量使用。
- **C++ 的 vtable 布局**：Itanium C++ ABI（Linux 上 g++/clang++ 使用）規定 vptr 在物件開頭、vtable 前面還有 offset-to-top 與 RTTI 指標。用 `g++ -fdump-lang-class`（gcc 8 以後的旗標名稱）可以印出類別的 vtable 布局，和本章的手工版本對照。
- **面試題**：「C 怎麼實作多型？」完整答案依序是：函式指標表、vptr 作為第一個成員、6.7.2.1 §15 保證、虛擬呼叫的兩次載入加間接跳躍、`const` vtable 的安全意義、以及 Linux `file_operations` 這個實例。

## 動手練習

1. 在 Shape 範例中加入 `Triangle`，並在 vtable 加一個 `perimeter` 方法。確認 `main` 的迴圈不需要修改；故意忘記在 `rect_vtbl` 填 `perimeter`，看看呼叫時發生什麼（提示：designated initializer 讓它變成 `NULL`），再加上「`NULL` 表示預設實作」的檢查。
2. 把 `Counter` 改寫成「公開大小、不公開欄位」的版本：標頭檔提供 `typedef struct { _Alignas(8) unsigned char opaque[16]; } Counter;`，實作檔以 `_Static_assert` 確認真正的結構放得進去。比較兩種版本對 ABI 的影響。
3. 在 Observer 範例的 `email_update` 中呼叫 `product_detach` 移除自己，觀察 bob 是否還收得到通知；修改 `product_restock` 讓它在通知期間安全地支援 detach。
4. 對 `call_area` 用 `-O0` 與 `-O2` 各編譯一次，數一數指令差異；再把 `circle_vtbl` 的 `const` 拿掉，看 `known_type` 的去虛擬化是否還會發生。

## 本章重點整理

- 物件導向是設計態度：資料與操作的結合、隱藏表示法、以同一介面使用不同實作。C 用結構、不完整型別、函式指標就能做到。
- 封裝靠不完整型別：呼叫端只有指標，不知道大小與成員，換得 ABI 穩定，代價是 heap 配置與無法行內展開。
- 多型靠 vtable：每類別一張 `const` 函式指標表，物件以第一個成員 vptr 指向它；一次虛擬呼叫是兩次載入加一次間接跳躍，型別已知時編譯器可以去虛擬化。
- 繼承靠「基底放第一個成員」，依據是 N1570 6.7.2.1 §15；downcast 不受檢查。
- 設計模式在 C 中剝掉語法後只剩函式指標與資料結構，Observer 與 Strategy 是系統程式最常見的兩個。

## 自我檢核

- [ ] 不看筆記，能畫出一個「Square 繼承 Rect 繼承 Shape」物件的記憶體布局與 vtable，並指出覆寫與繼承的方法
- [ ] 能引用 6.7.2.1 §15 解釋為什麼 `(Rect *) shape` 合法，並說出它在什麼情況下會變成未定義行為
- [ ] 如果面試官問「虛擬函式呼叫的成本是什麼」，能從組合語言層級回答，並提到去虛擬化
- [ ] 能說明不完整型別為什麼讓函式庫可以在不重新編譯呼叫端的情況下新增欄位
- [ ] 能解釋 vtable 宣告為 `const` 的三個好處（正確性、最佳化、安全）
- [ ] 想一個你寫過、用了一長串 `if-else` 或 `switch` 依型別選擇行為的程式，說明怎麼改成 Strategy 或 vtable
- [ ] 知道什麼情況下**不**該在 C 裡硬做 OOP（例如效能關鍵、只有一種實作時）

## 延伸閱讀

### 書籍

- **《Object-Oriented Programming With ANSI-C》** — Axel-Tobias Schreiner（1993，[作者公開 PDF](https://www.cs.rit.edu/~ats/books/ooc.pdf)）
  - **這本書的定位**：整本書都在用 ANSI C 從零建立一套物件系統，包含類別描述子、動態連結的方法、例外
  - **讀哪幾章**：第 1–4 章（抽象資料型別、動態連結、繼承）與本章完全對應；第 6 章以後的 metaclass 是進階
  - **注意**：1993 年的書，C89 語法，但設計思想完全適用

- **《Design Patterns: Elements of Reusable Object-Oriented Software》** — Gamma、Helm、Johnson、Vlissides（Addison-Wesley，1994）
  - **讀哪幾章**：第 1 章導論（特別是「Program to an interface, not an implementation」那段）；Observer、Strategy、Decorator、Visitor 四個模式；以及常被跳過的第 6 章結論
  - **前提知識**：本章；書中範例是 C++ 與 Smalltalk，讀的時候自己翻成 C

### 部落格 / 技術文章

- **[C Object Oriented Programming](https://nullprogram.com/blog/2014/10/21/)** — Chris Wellons（nullprogram，2014）
  - **這篇說什麼**：以 `container_of` 與函式指標表實作一個物件同時擁有多個介面，正好補上本章「單一繼承」的限制
  - **讀哪裡**：整篇，程式碼不長
  - **前提知識**：本章 vtable 小節；`container_of` 見 [Ch 19](./19-linux-list-head.md)

- **[Object-oriented techniques in C](https://dmitryfrank.com/articles/oop_in_c)** — Dmitry Frank
  - **這篇說什麼**：嵌入式開發者視角，從封裝、繼承、多型逐步展開，並討論在資源受限環境下的取捨
  - **為什麼值得讀**：實務導向，有完整的嵌入式範例

- **[Design patterns of 1972](https://blog.plover.com/prog/design-patterns.html)** — Mark Dominus
  - **這篇說什麼**：「模式是語言缺陷的補丁」這個觀點的經典論述
  - **讀哪裡**：整篇（短文）；讀完回頭想想本章哪些手法在 C++ 是語言特徵

- **[Object-Oriented Programming in C](https://www.state-machine.com/oop)** — Quantum Leaps（Miro Samek）
  - **這篇說什麼**：嵌入式即時框架 QP 的作者寫的應用筆記，含封裝、繼承、多型的 C 實作與 C++ 對照組合語言
  - **前提知識**：本章

### 原始碼

- **[OOC-Design-Pattern](https://github.com/QMonkey/OOC-Design-Pattern)** — GoF 23 種模式的 C 實作
  - **讀法**：先讀 `lib/include/base.h` 的 `new`/`delete` 巨集，再挑 Observer、Strategy、Visitor 三個目錄對照本章；注意它依賴 `-fms-extensions`

> 和既有課的關係：函式指標的面試題整理見 [../c_interview/15-function-pointers.md](../c_interview/15-function-pointers.md)。

手工 OOP 的基本功到這裡就齊了。下一章把這些手法放回真實世界：Linux 核心的 `file_operations` 與 `kref`、CPython 的 `PyObject`、SQLite 的 VFS、FFmpeg 的 `AVClass`、QEMU 的 QOM 和 GObject，看各個專案在同一組建構件上做出了什麼不同的取捨。

→ [Ch 30 Linux 核心與開源專案的物件模型](./30-object-models-in-the-wild.md)
