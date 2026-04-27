# Ch 11 — 設計 AST

> 目標：學會用 C 的 struct + tagged union 設計可擴充、可遞迴走訪的 AST。

## 為什麼需要 AST？

在練習 B 我們已經用了簡易 AST，但當語言變大，AST 的設計會影響整個編譯器的複雜度。好的 AST 應該：

1. **精簡**：不保留語法噪音（分號、括號、`{}`）
2. **可歸納處理**：結構清晰，讓 visitor 函式好寫
3. **可擴充**：加新節點不破壞舊 code
4. **攜帶位置資訊**：錯誤定位靠它

## C 實作 AST 的兩個主流做法

### 做法 A：Tagged Union（我們用這個）

```c
typedef enum { AST_NUM, AST_BIN, AST_IF, ... } AstKind;

typedef struct Ast {
    AstKind kind;
    int line;            // 位置資訊
    union {
        int ival;
        struct { struct Ast *l, *r; int op; } bin;
        struct { struct Ast *cond, *then, *els; } ifs;
    };
} Ast;
```

- **優點**：記憶體緊湊，`switch(kind)` 處理清爽
- **缺點**：union 用錯 tag 會 UB，要靠紀律

### 做法 B：每種節點一個 struct（OO 風格）

```c
typedef struct AstNum { AstKind k; int v; } AstNum;
typedef struct AstBin { AstKind k; struct Ast *l, *r; int op; } AstBin;
```

配合一個「基礎指標」類型：

```c
typedef struct { AstKind k; int line; } Ast;   /* 共用前綴 */
```

然後 `(AstBin *)node` 轉型取欄位。

- **優點**：每種節點大小不同，有時更省記憶體；加欄位不影響別的
- **缺點**：cast 太多，容易出錯

**本課程用做法 A**，學起來比較有結構感。

## 節點分類建議

設計 AST 前先把節點分成幾大類：

### 1. 字面量（leaf）

- `AST_INT_LIT`：整數常數
- `AST_FLOAT_LIT`：浮點常數
- `AST_STR_LIT`：字串常數
- `AST_BOOL_LIT`：布林常數（如果支援）

### 2. 變數參考

- `AST_VAR_REF`：讀取變數
- `AST_ARRAY_IDX`：陣列索引（如果支援）

### 3. 運算

- `AST_BIN`：二元運算（含 op 欄位區分 `+` `-` `*` `==` 等）
- `AST_UNARY`：一元運算

### 4. 控制流

- `AST_IF`
- `AST_WHILE`
- `AST_FOR`（如果支援）
- `AST_RETURN`
- `AST_BREAK`、`AST_CONTINUE`

### 5. 語句

- `AST_ASSIGN`
- `AST_DECL`：變數宣告
- `AST_BLOCK`：`{ ... }` 區塊
- `AST_EXPR_STMT`：表達式當語句

### 6. 函式

- `AST_FUN_DEF`：函式定義
- `AST_CALL`：函式呼叫
- `AST_RETURN`：return

### 7. 頂層

- `AST_PROGRAM`：整個程式

## 一個完整設計範例

```c
// ast.h

typedef enum {
    /* 字面量 */
    AST_INT_LIT, AST_FLOAT_LIT, AST_STR_LIT,
    /* 變數 */
    AST_VAR_REF,
    /* 運算 */
    AST_BIN, AST_UNARY,
    /* 控制流 */
    AST_IF, AST_WHILE, AST_RETURN,
    /* 語句 */
    AST_ASSIGN, AST_DECL, AST_BLOCK, AST_EXPR_STMT,
    /* 函式 */
    AST_FUN_DEF, AST_CALL,
    /* 頂層 */
    AST_PROGRAM,
} AstKind;

typedef enum {
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD,
    OP_LT, OP_GT, OP_LE, OP_GE, OP_EQ, OP_NE,
    OP_AND, OP_OR,
    OP_NEG, OP_NOT,
} Op;

typedef enum { TY_INT, TY_FLOAT, TY_STR, TY_VOID } Type;

/* AST 節點列表：用連結串列存 */
typedef struct AstList {
    struct Ast *node;
    struct AstList *next;
} AstList;

typedef struct Ast {
    AstKind kind;
    int line, col;
    Type type;                /* 語義分析填入 */
    union {
        /* 字面量 */
        int   i;
        double f;
        char *s;

        /* 變數 */
        char *name;

        /* 二元 / 一元 */
        struct { Op op; struct Ast *l, *r; } bin;
        struct { Op op; struct Ast *x; }     unary;

        /* 控制流 */
        struct { struct Ast *cond, *then, *els; } ifs;
        struct { struct Ast *cond, *body; }       wh;
        struct { struct Ast *value; }             ret;

        /* 語句 */
        struct { char *name; struct Ast *value; }        assign;
        struct { Type type; char *name; struct Ast *init; } decl;
        AstList *block;     /* 也用於 program */
        struct Ast *expr_stmt;

        /* 函式 */
        struct {
            Type ret_type;
            char *name;
            AstList *params;   /* list of AST_DECL */
            struct Ast *body;  /* AST_BLOCK */
        } fun;
        struct {
            char *name;
            AstList *args;
        } call;
    };
} Ast;
```

## 建構函式

為每種節點寫一個 `mk_xxx`：

```c
Ast *mk_int(int v, int line) {
    Ast *n = calloc(1, sizeof(Ast));
    n->kind = AST_INT_LIT;
    n->line = line;
    n->i = v;
    return n;
}

Ast *mk_bin(Op op, Ast *l, Ast *r, int line) {
    Ast *n = calloc(1, sizeof(Ast));
    n->kind = AST_BIN;
    n->line = line;
    n->bin.op = op;
    n->bin.l = l;
    n->bin.r = r;
    return n;
}

Ast *mk_if(Ast *c, Ast *t, Ast *e, int line) {
    Ast *n = calloc(1, sizeof(Ast));
    n->kind = AST_IF;
    n->line = line;
    n->ifs.cond = c;
    n->ifs.then = t;
    n->ifs.els = e;
    return n;
}
```

用 `calloc` 確保所有欄位歸零，避免「沒填的指標不是 NULL」的 bug。

## AstList 輔助

```c
AstList *list_cons(Ast *head, AstList *tail) {
    AstList *n = malloc(sizeof(*n));
    n->node = head;
    n->next = tail;
    return n;
}

AstList *list_append(AstList *list, Ast *node) {
    AstList *head = list_cons(node, NULL);
    if (!list) return head;
    AstList *p = list;
    while (p->next) p = p->next;
    p->next = head;
    return list;
}

#define LIST_FOREACH(p, list) \
    for (AstList *p = (list); p; p = p->next)
```

`list_append` 是 O(n)，大 list 會慢。實務上會用「尾指標」優化，但小語言先這樣。

## 走訪器 (Visitor) 骨架

大部分處理都是遞迴走訪：

```c
void visit(Ast *n) {
    if (!n) return;
    switch (n->kind) {
    case AST_INT_LIT:   /* ... */ break;
    case AST_VAR_REF:   /* ... */ break;
    case AST_BIN:
        visit(n->bin.l);
        visit(n->bin.r);
        /* ... */
        break;
    case AST_IF:
        visit(n->ifs.cond);
        visit(n->ifs.then);
        if (n->ifs.els) visit(n->ifs.els);
        break;
    case AST_BLOCK:
    case AST_PROGRAM:
        LIST_FOREACH(p, n->block) visit(p->node);
        break;
    /* ... */
    }
}
```

這個模板會出現在**求值、型別檢查、印 dot 圖、生 IR** 等好幾個地方，掌握它是關鍵。

## AST 的印出（Debug 神器）

能把 AST 印成樹狀是除錯的救命稻草：

```c
void print_ast(Ast *n, int indent) {
    if (!n) { printf("%*s<null>\n", indent, ""); return; }
    printf("%*s", indent, "");
    switch (n->kind) {
    case AST_INT_LIT: printf("INT %d\n", n->i); break;
    case AST_VAR_REF: printf("VAR %s\n", n->name); break;
    case AST_BIN:
        printf("BIN op=%d\n", n->bin.op);
        print_ast(n->bin.l, indent + 2);
        print_ast(n->bin.r, indent + 2);
        break;
    case AST_IF:
        printf("IF\n");
        print_ast(n->ifs.cond, indent + 2);
        print_ast(n->ifs.then, indent + 2);
        print_ast(n->ifs.els,  indent + 2);
        break;
    /* ... */
    }
}
```

另一個進階做法是輸出 Graphviz 的 dot 格式，Final Project 會用到。

## 記憶體管理

### 策略 1：一次 free 整棵樹

```c
void free_ast(Ast *n) {
    if (!n) return;
    switch (n->kind) {
    case AST_BIN: free_ast(n->bin.l); free_ast(n->bin.r); break;
    case AST_STR_LIT: free(n->s); break;
    case AST_VAR_REF: free(n->name); break;
    /* ... */
    }
    free(n);
}
```

這個做法清晰但寫起來繁瑣，且容易漏。

### 策略 2：Arena allocator（推薦）

把所有節點的記憶體放在一個大 buffer 裡，最後一次 free。

```c
typedef struct { char *buf; size_t used, cap; } Arena;

void *arena_alloc(Arena *a, size_t n) {
    if (a->used + n > a->cap) { /* 擴容 */ }
    void *p = a->buf + a->used;
    a->used += n;
    return p;
}

void arena_free(Arena *a) { free(a->buf); }
```

對短命編譯器特別合適，一次分配、一次釋放，沒記憶體洩漏可能。

Go、Rust 編譯器、LLVM 都廣泛用 arena。

## 動手練習

1. 照上面設計實作 `ast.h` / `ast.c`，包含 `mk_xxx` 建構函式和 `print_ast`。
2. 手工構造一個 AST 表示 `if (x > 0) y = x + 1; else y = 0;`，呼叫 `print_ast` 驗證。
3. 寫一個 `count_nodes(Ast *)` 函式遞迴算總節點數。
4. 如果你有心，試試 arena allocator 版本。

## 自我檢核

- [ ] 我能設計 tagged union 表示不同種類節點
- [ ] 我能寫出 `mk_xxx` 建構函式和對應的 free/visit
- [ ] 我知道 `AstList` 或陣列怎麼表示「多個子節點」
- [ ] 我知道 arena 的概念

→ [Ch 12 在動作中建構 AST](./12-building-ast.md)
