# Ch 14 — 型別檢查

> 目標：走訪 AST，推斷每個 expression 的型別、檢查型別相容性、必要時插入隱式轉型節點。

## 型別檢查要做什麼？

幾個常見任務：

1. **為每個表達式算出型別**：AST 節點的 `type` 欄位填上
2. **檢查運算子使用是否合法**：`int + string` 要報錯
3. **檢查函式呼叫的參數**：數量與型別要對
4. **檢查指派相容性**：`int x = "abc";` 要報錯
5. **插入隱式轉型**：`int + float` 要把 int 提升為 float
6. **檢查 return 型別**：函式 body 的 return 型別要和宣告一致
7. **檢查 condition 型別**：`if` / `while` 的條件是否為 bool/int

## 型別系統設計

先定好基本型別：

```c
typedef enum { TY_INT, TY_FLOAT, TY_STR, TY_VOID, TY_BOOL, TY_ERROR } Type;
```

`TY_ERROR` 是一個特殊「錯誤型別」，遇到已經報過錯的子表達式就標成這個，避免同樣的錯誤訊息噴一堆。

複合型別（陣列、函式）本課程先略，要的話就做成結構體：

```c
typedef struct TypeInfo {
    Type base;
    struct TypeInfo *elem;   /* 陣列的元素型別 */
    struct TypeInfo **params; /* 函式的參數型別 */
    int n_params;
    struct TypeInfo *ret;    /* 函式的回傳型別 */
} TypeInfo;
```

本章先用 enum，簡單直接。

## 骨架：遞迴下來填 type 欄位

```c
Type check(Ast *n);   /* 回傳此 AST 的型別 */

Type check_expr(Ast *n) {
    if (!n) return TY_VOID;
    switch (n->kind) {
    case AST_INT_LIT:   return n->type = TY_INT;
    case AST_FLOAT_LIT: return n->type = TY_FLOAT;
    case AST_STR_LIT:   return n->type = TY_STR;
    case AST_VAR_REF: {
        Symbol *s = lookup(n->name);
        if (!s) { error(n, "undefined '%s'", n->name); return n->type = TY_ERROR; }
        return n->type = s->type;
    }
    case AST_BIN:
        return check_bin(n);
    case AST_UNARY:
        return check_unary(n);
    case AST_CALL:
        return check_call(n);
    default:
        return n->type = TY_ERROR;
    }
}
```

### 二元運算檢查

```c
Type check_bin(Ast *n) {
    Type lt = check_expr(n->bin.l);
    Type rt = check_expr(n->bin.r);

    if (lt == TY_ERROR || rt == TY_ERROR) return n->type = TY_ERROR;

    Op op = n->bin.op;

    /* 算術：+ - * / % */
    if (op >= OP_ADD && op <= OP_MOD) {
        if (lt == TY_INT && rt == TY_INT) return n->type = TY_INT;
        if ((lt == TY_INT || lt == TY_FLOAT) && (rt == TY_INT || rt == TY_FLOAT)) {
            /* 隱式轉型：int → float */
            if (lt == TY_INT)   n->bin.l = insert_cast(n->bin.l, TY_FLOAT);
            if (rt == TY_INT)   n->bin.r = insert_cast(n->bin.r, TY_FLOAT);
            return n->type = TY_FLOAT;
        }
        /* 字串串接？ */
        if (op == OP_ADD && lt == TY_STR && rt == TY_STR) return n->type = TY_STR;
        error(n, "invalid operands for arithmetic: %s, %s", type_name(lt), type_name(rt));
        return n->type = TY_ERROR;
    }

    /* 比較：< > <= >= == != */
    if (op >= OP_LT && op <= OP_NE) {
        /* 兩邊要可比，這裡簡化：要麼都數字，要麼都字串 */
        if ((lt == TY_INT || lt == TY_FLOAT) && (rt == TY_INT || rt == TY_FLOAT))
            return n->type = TY_BOOL;
        if (lt == TY_STR && rt == TY_STR && (op == OP_EQ || op == OP_NE))
            return n->type = TY_BOOL;
        error(n, "invalid operands for comparison");
        return n->type = TY_ERROR;
    }

    /* 邏輯：&& || */
    if (op == OP_AND || op == OP_OR) {
        /* 允許 int 與 bool */
        if (lt == TY_INT || lt == TY_BOOL)
            if (rt == TY_INT || rt == TY_BOOL)
                return n->type = TY_BOOL;
        error(n, "invalid operands for logical");
        return n->type = TY_ERROR;
    }

    return n->type = TY_ERROR;
}
```

### 一元運算

```c
Type check_unary(Ast *n) {
    Type xt = check_expr(n->unary.x);
    if (xt == TY_ERROR) return n->type = TY_ERROR;

    switch (n->unary.op) {
    case OP_NEG:
        if (xt == TY_INT || xt == TY_FLOAT) return n->type = xt;
        error(n, "unary - on non-numeric");
        return n->type = TY_ERROR;
    case OP_NOT:
        if (xt == TY_INT || xt == TY_BOOL) return n->type = TY_BOOL;
        error(n, "unary ! on non-bool");
        return n->type = TY_ERROR;
    }
    return n->type = TY_ERROR;
}
```

### 函式呼叫

```c
Type check_call(Ast *n) {
    Symbol *f = lookup(n->call.name);
    if (!f || f->kind != SYM_FUN) {
        error(n, "'%s' is not a function", n->call.name);
        return n->type = TY_ERROR;
    }
    int i = 0;
    AstList *p = n->call.args;
    ParamList *q = f->params;
    while (p && q) {
        Type at = check_expr(p->node);
        if (!assign_compat(q->type, at)) {
            error(p->node, "arg %d: expected %s, got %s",
                  i+1, type_name(q->type), type_name(at));
        }
        p = p->next;
        q = q->next;
        i++;
    }
    if (p) error(n, "too many arguments to '%s'", n->call.name);
    if (q) error(n, "too few arguments to '%s'", n->call.name);
    return n->type = f->ret_type;
}
```

## 隱式轉型節點

需要一個新 AST 節點表示轉型：

```c
/* 新增到 AstKind */
AST_CAST,

/* union 欄位 */
struct { struct Ast *x; Type to; } cast;

/* 建構 */
Ast *insert_cast(Ast *x, Type to) {
    Ast *n = calloc(1, sizeof(Ast));
    n->kind = AST_CAST;
    n->cast.x = x;
    n->cast.to = to;
    n->type = to;
    return n;
}
```

這樣 `eval` 或 IR gen 走到 `AST_CAST` 節點就會做轉型，邏輯清楚。

## 指派與宣告檢查

```c
void check_stmt(Ast *n) {
    switch (n->kind) {
    case AST_DECL: {
        Type t = n->decl.type;
        if (n->decl.init) {
            Type et = check_expr(n->decl.init);
            if (!assign_compat(t, et))
                error(n, "init type mismatch: %s <- %s", type_name(t), type_name(et));
            else if (t != et)
                n->decl.init = insert_cast(n->decl.init, t);
        }
        if (!declare(n->decl.name, t))
            error(n, "redeclaration of '%s'", n->decl.name);
        break;
    }
    case AST_ASSIGN: {
        Symbol *s = lookup(n->assign.name);
        if (!s) { error(n, "undefined '%s'", n->assign.name); break; }
        Type et = check_expr(n->assign.value);
        if (!assign_compat(s->type, et))
            error(n, "assignment type mismatch");
        else if (s->type != et)
            n->assign.value = insert_cast(n->assign.value, s->type);
        break;
    }
    case AST_IF: {
        Type ct = check_expr(n->ifs.cond);
        if (ct != TY_BOOL && ct != TY_INT)
            error(n, "if condition must be bool or int");
        check_stmt(n->ifs.then);
        if (n->ifs.els) check_stmt(n->ifs.els);
        break;
    }
    /* ... while, block, return, ... */
    }
}
```

### assign_compat

```c
int assign_compat(Type dst, Type src) {
    if (dst == src) return 1;
    if (dst == TY_FLOAT && src == TY_INT) return 1;   /* int → float */
    if (dst == TY_INT && src == TY_BOOL) return 1;
    if (dst == TY_BOOL && src == TY_INT) return 1;
    return 0;
}
```

## return 檢查

每個函式定義要記錄它的 return type，檢查 body 裡所有 return：

```c
Type current_fun_ret;

void check_fun(Ast *n) {
    current_fun_ret = n->fun.ret_type;
    enter_scope();
    LIST_FOREACH(p, n->fun.params) {
        declare(p->node->decl.name, p->node->decl.type);
    }
    check_stmt(n->fun.body);
    exit_scope();
    /* 進階：檢查非 void 函式的所有路徑都 return */
}

void check_stmt_return(Ast *n) {
    if (n->ret.value) {
        Type rt = check_expr(n->ret.value);
        if (!assign_compat(current_fun_ret, rt))
            error(n, "return type mismatch");
    } else if (current_fun_ret != TY_VOID) {
        error(n, "must return a value");
    }
}
```

進階檢查：「所有路徑都有 return」這需要流程分析（data flow），課程範圍外。

## 錯誤報告小技巧

### 1. 多錯一次報完

不要遇到第一個錯就 exit。讓 `check` 繼續下去，把 `TY_ERROR` 散布出去即可（算術遇到 ERROR 就回 ERROR，不再報）。

### 2. 報錯時附位置

```c
void error(Ast *n, const char *fmt, ...) {
    fprintf(stderr, "line %d: ", n ? n->line : 0);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
    error_count++;
}
```

最後在 `main` 判斷 `error_count > 0` 決定要不要生 code。

### 3. 型別名字輔助函式

```c
const char *type_name(Type t) {
    switch (t) {
    case TY_INT: return "int";
    case TY_FLOAT: return "float";
    case TY_STR: return "str";
    case TY_VOID: return "void";
    case TY_BOOL: return "bool";
    default: return "<error>";
    }
}
```

訊息才會清爽。

## 動手練習

1. 實作 `check_expr`、`check_stmt`、`check_fun`。
2. 測試用例：
   ```
   int x = 3.14;           // 應報 "init type mismatch: int <- float" 或走 cast
   int y = "abc";          // 必定報錯
   if ("hello") ...        // condition 型別錯
   int f() { return; }     // must return a value
   int g() { return "x"; } // return type mismatch
   print(1, 2);            // 若 print 只收一個參數：too many args
   ```
3. 實作 `insert_cast` 並確保 `print_ast` 會顯示這些轉型節點。
4. 進階：改為允許 `TypeInfo` 結構體，支援一維陣列。

## 自我檢核

- [ ] 我能在 AST 走訪時正確填 `type` 欄位
- [ ] 我能處理運算子的型別規則，包括 int/float 混用
- [ ] 我能插入隱式轉型節點
- [ ] 我能檢查函式呼叫的參數數量與型別
- [ ] 我能檢查 return type
- [ ] 我能用 `TY_ERROR` 避免錯誤連鎖

→ [練習 C：AST 解釋器](./practice-c-ast-interpreter.md)
