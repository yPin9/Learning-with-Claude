# 練習 C — AST 解釋器

> 目標：把練習 B 的「直接在 action 求值」升級為「先建完整 AST、做語義檢查、再走訪執行」。支援函式定義與呼叫。

## 任務規格

把前面幾章整合：
- Ch 11 的 AST 設計
- Ch 12 的 parser 建 AST
- Ch 13 的符號表
- Ch 14 的型別檢查

然後**再加一個 evaluator**，能執行 AST。

最終產品是一個能跑這樣程式碼的直譯器：

```c
int fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

int main() {
    int i = 0;
    while (i < 10) {
        print fib(i);
        i = i + 1;
    }
    return 0;
}
```

跑起來會印 0 1 1 2 3 5 8 13 21 34。

## 專案結構

```
minic-interp/
├── ast.h / ast.c            # AST 定義與建構
├── symtab.h / symtab.c      # 符號表
├── typecheck.h / typecheck.c # 型別檢查
├── eval.h / eval.c          # 解釋器
├── lexer.l
├── parser.y
├── main.c                   # 總協調
└── Makefile
```

## Evaluator 設計

解釋器需要**執行期**的環境：
- 變數綁定（跟語義分析的符號表分開，因為要存「值」不只是型別）
- 函式表

### Value 型別

```c
typedef struct Value {
    Type type;
    union {
        int i;
        double f;
        char *s;
    };
} Value;
```

### 執行期環境

```c
typedef struct Env {
    struct { char *name; Value val; } *slots;
    int n, cap;
    struct Env *parent;
} Env;

Env *env_new(Env *parent);
void env_free(Env *e);
void env_bind(Env *e, const char *name, Value v);
Value *env_lookup(Env *e, const char *name);
```

注意：`env_lookup` 回傳**指標**，因為 assignment 要能直接改變數。

### 函式表

```c
typedef struct FunEntry {
    char *name;
    Ast *def;   /* AST_FUN_DEF 節點 */
    struct FunEntry *next;
} FunEntry;

FunEntry *funs = NULL;
```

因為函式定義不在 lexical scope 內處理（它們是全域的），另外存。

### 主 eval 函式

```c
Value eval(Ast *n, Env *env) {
    switch (n->kind) {
    case AST_INT_LIT:   return (Value){.type=TY_INT, .i=n->i};
    case AST_FLOAT_LIT: return (Value){.type=TY_FLOAT, .f=n->f};
    case AST_STR_LIT:   return (Value){.type=TY_STR, .s=n->s};
    case AST_VAR_REF: {
        Value *v = env_lookup(env, n->name);
        if (!v) runtime_error(n, "undefined: %s", n->name);
        return *v;
    }
    case AST_BIN:   return eval_bin(n, env);
    case AST_UNARY: return eval_unary(n, env);
    case AST_CAST:  return eval_cast(n, env);
    case AST_CALL:  return eval_call(n, env);

    case AST_ASSIGN: {
        Value v = eval(n->assign.value, env);
        Value *slot = env_lookup(env, n->assign.name);
        if (slot) *slot = v;
        else env_bind(env, n->assign.name, v);
        return v;
    }

    case AST_DECL: {
        Value v = {0};
        v.type = n->decl.type;
        if (n->decl.init) v = eval(n->decl.init, env);
        env_bind(env, n->decl.name, v);
        return v;
    }

    case AST_IF: {
        Value c = eval(n->ifs.cond, env);
        if (value_true(c)) return eval(n->ifs.then, env);
        else if (n->ifs.els) return eval(n->ifs.els, env);
        return (Value){.type=TY_VOID};
    }

    case AST_WHILE:
        while (value_true(eval(n->wh.cond, env))) {
            Value r = eval(n->wh.body, env);
            if (r.type == TY_RETURN_SIGNAL) return r;
        }
        return (Value){.type=TY_VOID};

    case AST_BLOCK: {
        Env *child = env_new(env);
        Value r = {.type=TY_VOID};
        LIST_FOREACH(p, n->block) {
            r = eval(p->node, child);
            if (r.type == TY_RETURN_SIGNAL) break;
        }
        env_free(child);
        return r;
    }

    case AST_RETURN: {
        Value v = n->ret.value ? eval(n->ret.value, env) : (Value){.type=TY_VOID};
        v.type = TY_RETURN_SIGNAL;   /* 用型別欄位夾帶訊號 */
        return v;
    }

    case AST_EXPR_STMT:
        return eval(n->expr_stmt, env);
    }
    return (Value){.type=TY_VOID};
}
```

### Return 訊號的技巧

上面用 `TY_RETURN_SIGNAL` 當特殊標記。更乾淨的做法是用 `setjmp/longjmp`：

```c
jmp_buf return_buf;
Value return_value;

case AST_RETURN:
    return_value = n->ret.value ? eval(n->ret.value, env) : (Value){0};
    longjmp(return_buf, 1);
```

```c
case AST_CALL: {
    ...
    if (setjmp(return_buf) == 0) {
        eval(fn->body, new_env);
        return (Value){.type=TY_VOID};   /* 沒 return */
    } else {
        return return_value;
    }
}
```

longjmp 路徑效率更好但要小心記憶體。本練習兩種都可以。

### eval_call

```c
Value eval_call(Ast *n, Env *env) {
    /* 內建函式 print 特判 */
    if (strcmp(n->call.name, "print") == 0) {
        LIST_FOREACH(p, n->call.args) {
            Value v = eval(p->node, env);
            print_value(v);
        }
        printf("\n");
        return (Value){.type=TY_VOID};
    }

    /* 找函式定義 */
    FunEntry *f = fun_find(n->call.name);
    if (!f) runtime_error(n, "no such function: %s", n->call.name);

    /* 建新 env，parent 指向全域 */
    Env *fe = env_new(global_env);
    AstList *pa = f->def->fun.params;
    AstList *pv = n->call.args;
    while (pa && pv) {
        Value v = eval(pv->node, env);   /* 求值用 caller 的 env */
        env_bind(fe, pa->node->decl.name, v);
        pa = pa->next;
        pv = pv->next;
    }

    /* 執行 body */
    Value r = eval(f->def->fun.body, fe);
    env_free(fe);

    if (r.type == TY_RETURN_SIGNAL) r.type = /* 還原本值型別 */;
    return r;
}
```

## 內建函式

`print` 是最實用的內建函式，直接在 `eval_call` 特判。也可以用一個「內建函式表」：

```c
typedef Value (*Builtin)(AstList *args, Env *env);

struct { const char *name; Builtin fn; } builtins[] = {
    {"print", builtin_print},
    {"input", builtin_input},
    {NULL, NULL}
};
```

## main.c 協調流程

```c
int main(int argc, char **argv) {
    if (argc > 1) yyin = fopen(argv[1], "r");

    /* pass 1: parse */
    if (yyparse() != 0 || !root) return 1;

    /* pass 2: register funs */
    LIST_FOREACH(p, root->block) {
        if (p->node->kind == AST_FUN_DEF) fun_register(p->node);
    }

    /* pass 3: type check */
    symtab_init();
    check_program(root);
    if (error_count > 0) {
        fprintf(stderr, "%d errors, aborting.\n", error_count);
        return 1;
    }

    /* pass 4: run main() */
    Ast main_call = { .kind=AST_CALL, .call={.name="main", .args=NULL} };
    eval(&main_call, global_env);
    return 0;
}
```

## 測試用例

**階乘**：
```c
int fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

int main() {
    print(fact(5));   // 120
    return 0;
}
```

**互相遞迴**：
```c
int is_even(int n);
int is_odd(int n) { if (n == 0) return 0; return is_even(n - 1); }
int is_even(int n) { if (n == 0) return 1; return is_odd(n - 1); }

int main() {
    print(is_even(10));   // 1
    print(is_odd(7));     // 1
    return 0;
}
```

**作用域**：
```c
int main() {
    int x = 1;
    {
        int x = 2;
        print(x);   // 2
    }
    print(x);       // 1
    return 0;
}
```

**型別錯誤**：
```c
int main() {
    int x = "hello";   // 應該在 type check 階段報錯
    return 0;
}
```

## 除錯建議

1. 每個 pass 都先印 debug 資訊，確認輸出正確再接下一個 pass。
2. AST 印成樹狀是你的朋友，最底層 bug 看它就對了。
3. Env 也寫個 `print_env`，在 call / block 進出時印一次。

## 進階挑戰

1. **for 迴圈**：實作 `for (init; cond; update) body`
2. **字串操作**：`+` 串接、`length(s)`
3. **break / continue**：用訊號（像 return 那樣）實作
4. **簡單陣列**：`int a[10]; a[0] = 1;`
5. **Closure**：允許函式回傳函式（進階中的進階）

## 自我檢核

- [ ] 我的直譯器能跑 fibonacci、factorial、互相遞迴
- [ ] 我能正確處理作用域（shadow、區塊）
- [ ] 型別錯誤會在 type check 階段被抓到
- [ ] Return 能跳出多層巢狀
- [ ] 我能解釋解釋期 Env 和語義分析期 Scope 的差別

做完這個練習，你已經具備寫小型直譯式語言的完整能力。最後一章是整合性的 Final Project。

→ [Final Project：MiniC 前端](./final-project-minic.md)
