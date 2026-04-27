# Ch 13 — 符號表與作用域

> 目標：實作支援巢狀作用域的符號表，能正確處理 shadow、函式參數、區域變數。

## 符號表是什麼？

編譯器在語義分析與程式碼生成階段，需要回答：

- 這個識別字指向什麼？變數、函式、還是型別？
- 它的型別是什麼？
- 如果是變數，它在記憶體的哪個位置（offset）？
- 它是在哪個作用域宣告的？

這些資訊存在 **符號表** (symbol table) 裡。

## 作用域的結構

大部分語言都有巢狀作用域：

```c
int x;                    // 全域
void f(int y) {           // 參數 y
    int z;                // 區域
    {                     // 巢狀區塊
        int z;            // shadow 外層 z
    }
}
```

查找規則：**從最內層往外找，找到第一個就用**。

## 實作：堆疊式作用域

最直觀的做法：作用域 = 一個 hash table，所有作用域串成堆疊。

```c
typedef struct Symbol {
    char *name;
    Type type;
    int offset;          /* 在 stack frame 中的位置 */
    struct Symbol *next; /* hash bucket 鍵結 */
} Symbol;

#define HASH_SIZE 128

typedef struct Scope {
    Symbol *table[HASH_SIZE];
    struct Scope *parent;
} Scope;

Scope *current_scope = NULL;

static unsigned hash(const char *s) {
    unsigned h = 0;
    while (*s) h = h * 31 + (unsigned char)*s++;
    return h % HASH_SIZE;
}
```

### 進入/離開作用域

```c
void enter_scope(void) {
    Scope *s = calloc(1, sizeof(Scope));
    s->parent = current_scope;
    current_scope = s;
}

void exit_scope(void) {
    Scope *s = current_scope;
    current_scope = s->parent;
    /* 釋放 s->table 裡的 Symbol 們 */
    for (int i = 0; i < HASH_SIZE; i++) {
        Symbol *sym = s->table[i];
        while (sym) { Symbol *nxt = sym->next; free(sym->name); free(sym); sym = nxt; }
    }
    free(s);
}
```

### 宣告與查找

```c
int declare(const char *name, Type type) {
    unsigned h = hash(name);
    /* 檢查當前 scope 是否已有同名 */
    for (Symbol *p = current_scope->table[h]; p; p = p->next)
        if (strcmp(p->name, name) == 0) return 0;   /* 重複宣告 */
    Symbol *s = malloc(sizeof(Symbol));
    s->name = strdup(name);
    s->type = type;
    s->offset = 0;  /* 之後 code gen 填 */
    s->next = current_scope->table[h];
    current_scope->table[h] = s;
    return 1;
}

Symbol *lookup(const char *name) {
    unsigned h = hash(name);
    for (Scope *s = current_scope; s; s = s->parent)
        for (Symbol *p = s->table[h]; p; p = p->next)
            if (strcmp(p->name, name) == 0) return p;
    return NULL;
}
```

`lookup` 往外爬 parent 鏈，自然實現了「內層優先」。

## 何時 enter / exit？

通常在語義分析走訪 AST 時：

```c
void analyze(Ast *n) {
    switch (n->kind) {
    case AST_BLOCK:
        enter_scope();
        LIST_FOREACH(p, n->block) analyze(p->node);
        exit_scope();
        break;
    case AST_FUN_DEF:
        enter_scope();   /* 函式參數的作用域 */
        LIST_FOREACH(p, n->fun.params) analyze(p->node);
        analyze(n->fun.body);   /* body 再自己 enter 一次 */
        exit_scope();
        break;
    case AST_DECL:
        if (!declare(n->decl.name, n->decl.type))
            error(n, "redeclaration of '%s'", n->decl.name);
        if (n->decl.init) analyze(n->decl.init);
        break;
    case AST_VAR_REF: {
        Symbol *s = lookup(n->name);
        if (!s) error(n, "undefined variable '%s'", n->name);
        else n->type = s->type;
        break;
    }
    /* ... */
    }
}
```

注意：函式的參數作用域跟 body 通常**分開處理**，因為 function body 本身是個 `AST_BLOCK` 會再 `enter_scope`。

## 函式 vs 變數：分開還是合一？

兩種設計：

### 設計 A：統一符號表（C 風格）

```c
typedef struct Symbol {
    enum { SYM_VAR, SYM_FUN, SYM_TYPE } kind;
    Type type;
    /* 函式特有 */
    AstList *params;
    Type ret_type;
} Symbol;
```

所有識別字用同一個表，`enter_scope` / `lookup` 統一。

### 設計 B：分名空間

C 其實有多個名稱空間：變數、標籤（goto label）、struct/union 成員、tag 名稱。嚴格的語言會為每種分開維護符號表。

小語言先用設計 A。

## 全域符號表

整個程式的頂層作用域就是全域。初始化時：

```c
void symtab_init(void) {
    current_scope = NULL;
    enter_scope();   /* global scope */
    /* 可以這裡預先宣告內建函式，例如 print */
    declare_fun("print", TY_VOID, ...);
}
```

## 前向宣告 / 遞迴函式

C 允許：
```c
int f(int x);          /* 前向宣告 */
int g(int x) { return f(x - 1); }   /* 可以用 f */
int f(int x) { ... }   /* 定義 */
```

實作上，`f` 在看到宣告時就登記到符號表，定義時不重新宣告只補完 body。

類似地，**互相遞迴**需要先全部登記、再解析 body：

```c
void analyze_program(Ast *prog) {
    /* pass 1: 登記所有函式名稱 */
    LIST_FOREACH(p, prog->block) {
        if (p->node->kind == AST_FUN_DEF)
            declare_fun(p->node->fun.name, p->node->fun.ret_type, p->node->fun.params);
    }
    /* pass 2: 分析 body */
    LIST_FOREACH(p, prog->block) analyze(p->node);
}
```

## 符號表的 debug 技巧

印出當前符號表內容：

```c
void print_symtab(void) {
    int level = 0;
    for (Scope *s = current_scope; s; s = s->parent, level++) {
        printf("-- scope level %d --\n", level);
        for (int i = 0; i < HASH_SIZE; i++)
            for (Symbol *p = s->table[i]; p; p = p->next)
                printf("  %s : type=%d\n", p->name, p->type);
    }
}
```

在 `enter_scope` / `exit_scope` 呼叫處加 printf，能看出 scope 生命周期。

## 陣列、struct 成員

等你開始支援 struct / class，成員名稱要放在**型別**關聯的子符號表，不跟普通變數混。

```c
typedef struct TypeStructInfo {
    struct { char *name; Type type; int offset; } *members;
    int member_count;
} TypeStructInfo;
```

這超出 MiniC 範圍，但知道有這一層即可。

## 動手練習

1. 實作上面的 Scope + Symbol，寫出 `enter_scope` / `exit_scope` / `declare` / `lookup`。
2. 寫一個測試：
   ```c
   enter_scope();
   declare("x", TY_INT);
   enter_scope();
   declare("x", TY_FLOAT);   /* shadow */
   assert(lookup("x")->type == TY_FLOAT);
   exit_scope();
   assert(lookup("x")->type == TY_INT);
   exit_scope();
   assert(lookup("x") == NULL);
   ```
3. 把符號表整合到 Ch 12 的 AST 分析流程裡。
4. 加入 `AST_BLOCK` 的作用域進出，驗證 shadow 行為。
5. 實作互相遞迴函式所需的「兩 pass」流程。

## 自我檢核

- [ ] 我能用堆疊式 scope + hash bucket 實作符號表
- [ ] 我能正確處理 shadow：內層優先，離開後外層復原
- [ ] 我知道函式前向宣告與互相遞迴要先登記再解析
- [ ] 我能在走訪 AST 時正確 enter / exit scope

→ [Ch 14 型別檢查](./14-type-checking.md)
