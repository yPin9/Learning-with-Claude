# Ch 15 — 函式指標、Callback、C 語言模擬 vtable

> 目標：掌握函式指標的宣告與使用，能用它實作 callback API、策略模式、以及 C 語言的多型（vtable 模擬）。

## 函式指標的語法

```c
// 宣告：int (*fp)(int, int)
// 讀法：fp 是指標，指向「接受兩個 int、返回 int」的函式

int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }

int (*fp)(int, int);    // 宣告
fp = add;               // 賦值（也可以寫 &add，完全等價）
int r = fp(3, 4);       // 呼叫，等價於 (*fp)(3, 4)
```

**typedef 讓宣告更清楚**：

```c
typedef int (*BinaryOp)(int, int);

BinaryOp ops[] = { add, mul };
for (int i = 0; i < 2; i++)
    printf("%d\n", ops[i](3, 4));   // 7, 12
```

**容易搞混的宣告**：

```c
int (*fp)(int);     // fp 是函式指標，指向 int(int) 的函式
int *fp(int);       // fp 是函式，接受 int，返回 int*（完全不同！）
int (*arr[5])(int); // arr 是陣列，5 個函式指標，每個指向 int(int) 函式
```

---

## Callback 模式

標準庫的 `qsort` 就是 callback 的典型：

```c
#include <stdlib.h>

int cmp_int_asc(const void *a, const void *b) {
    int ia = *(const int *)a;
    int ib = *(const int *)b;
    return (ia > ib) - (ia < ib);   // 安全的三路比較，避免溢位（不要用 ia - ib）
}

int arr[] = {5, 2, 8, 1, 9};
qsort(arr, 5, sizeof(int), cmp_int_asc);
// arr 現在是 {1, 2, 5, 8, 9}
```

**設計自訂 callback API**：

```c
typedef void (*EventHandler)(int event_id, void *userdata);

typedef struct {
    EventHandler on_connect;
    EventHandler on_message;
    EventHandler on_disconnect;
    void *userdata;   // 讓呼叫者傳入自己的 context（核心設計）
} EventLoop;

void event_loop_run(EventLoop *loop) {
    // ... 觸發事件時 ...
    if (loop->on_message)
        loop->on_message(MSG_EVENT, loop->userdata);
}

// 使用者側：
typedef struct { int count; } MyContext;

void my_on_message(int event_id, void *userdata) {
    MyContext *ctx = (MyContext *)userdata;
    ctx->count++;
    printf("Got message #%d\n", ctx->count);
}

MyContext ctx = {0};
EventLoop loop = {
    .on_message = my_on_message,
    .userdata   = &ctx,
};
```

`void *userdata` 讓 callback 可以存取自己的狀態，否則只能用全域變數——這是最重要的設計模式。

---

## 策略模式（Strategy Pattern）

```c
typedef struct {
    size_t (*hash)(const char *key, size_t len);
    int    (*compare)(const char *a, const char *b);
} HashTableOps;

size_t djb2(const char *key, size_t len) {
    size_t h = 5381;
    for (size_t i = 0; i < len; i++)
        h = (h << 5) + h + (uint8_t)key[i];
    return h;
}

static const HashTableOps default_ops = {
    .hash    = djb2,
    .compare = strcmp,
};

static const HashTableOps case_insensitive_ops = {
    .hash    = djb2_lower,    // key 轉小寫後 hash
    .compare = strcasecmp,
};

// 建立 hash table 時傳入策略
HashTable *ht = ht_create(1024, &case_insensitive_ops);
```

---

## 模擬 vtable（虛函式表）

C++ 的 vtable 本質是函式指標陣列。C 裡用 struct 模仿：

```c
// 「基底類別」Shape
typedef struct Shape Shape;

typedef struct {
    double (*area)(const Shape *self);
    double (*perimeter)(const Shape *self);
    void   (*draw)(const Shape *self);
    void   (*destroy)(Shape *self);
} ShapeVtable;

struct Shape {
    const ShapeVtable *vtable;   // 第一個欄位必須是 vtable 指標
};

// 「子類別」Circle
typedef struct {
    Shape  base;      // 第一個欄位必須是 Shape（讓 Circle* 可安全 cast 成 Shape*）
    double radius;
} Circle;

static double circle_area(const Shape *self) {
    const Circle *c = (const Circle *)self;
    return 3.14159265 * c->radius * c->radius;
}

static double circle_perimeter(const Shape *self) {
    const Circle *c = (const Circle *)self;
    return 2.0 * 3.14159265 * c->radius;
}

static void circle_draw(const Shape *self) {
    const Circle *c = (const Circle *)self;
    printf("Circle(r=%.2f)\n", c->radius);
}

static void circle_destroy(Shape *self) {
    free(self);
}

static const ShapeVtable circle_vtable = {
    .area      = circle_area,
    .perimeter = circle_perimeter,
    .draw      = circle_draw,
    .destroy   = circle_destroy,
};

Circle *circle_new(double radius) {
    Circle *c        = malloc(sizeof(Circle));
    c->base.vtable   = &circle_vtable;   // 綁定 vtable（等同 C++ 的虛函式表）
    c->radius        = radius;
    return c;
}

// 多型呼叫：
void print_area(const Shape *s) {
    printf("Area: %f\n", s->vtable->area(s));   // 動態分派，不需要知道具體型別
}

// 用法：
Circle *c   = circle_new(5.0);
print_area((Shape *)c);          // 輸出 78.539...

Shape *s    = (Shape *)c;
s->vtable->draw(s);              // 輸出 Circle(r=5.00)
s->vtable->destroy(s);           // free
```

Linux kernel 的 `struct file_operations`、`struct inode_operations` 就是這個模式。

---

## Dispatch Table

```c
typedef void (*CommandFn)(const char *arg);

void cmd_help(const char *arg)    { printf("Usage: ...\n"); }
void cmd_version(const char *arg) { printf("v1.0\n"); }
void cmd_quit(const char *arg)    { exit(0); }

typedef struct {
    const char *name;
    CommandFn   fn;
    const char *help;
} Command;

static const Command commands[] = {
    { "help",    cmd_help,    "Show this help" },
    { "version", cmd_version, "Print version"  },
    { "quit",    cmd_quit,    "Exit"            },
    { NULL, NULL, NULL }   // 哨兵
};

void dispatch(const char *name, const char *arg) {
    for (const Command *c = commands; c->name; c++) {
        if (strcmp(c->name, name) == 0) {
            c->fn(arg);
            return;
        }
    }
    fprintf(stderr, "Unknown command: %s\n", name);
}
```

比 `if-else if` 鏈好維護。加新指令只需在陣列加一行。

---

## 常見陷阱

```c
// 透過不相容函式指標呼叫 → UB
void foo(int x)  { printf("%d\n", x); }
void (*fp)(void) = (void (*)(void))foo;
fp();   // UB：引數數量/型別不符

// 函式指標的比較
if (fp == NULL) ...     // OK
if (fp == add) ...      // OK
if (fp1 < fp2) ...      // UB：函式指標不能做大小比較
```

---

## 自我檢核

- [ ] 能不用 typedef 宣告「接受兩個 int 返回 int 的函式指標」
- [ ] 知道 `void *userdata` 在 callback API 的作用（帶呼叫者 context）
- [ ] 能用 struct + 函式指標模擬 vtable，解釋 Linux kernel file_operations 的原理
- [ ] 知道 qsort 的比較函式為什麼用三路比較（`a>b) - (a<b`）而不用 `a - b`

→ [Ch 16 x86-64 呼叫慣例與 ABI](./16-calling-convention-abi.md)
