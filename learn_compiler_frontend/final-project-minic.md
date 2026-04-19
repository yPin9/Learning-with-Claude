# Final Project — MiniC 前端

> 目標：整合所有章節，做一個完整的 MiniC 編譯器前端。產出：AST 視覺化（Graphviz）+ 三位址碼（TAC）中介碼。

## 規格

### 語言特性

**型別**
- `int`、`float`、`void`
- 一維陣列（選配）

**表達式**
- 算術：`+ - * / %`
- 比較：`< > <= >= == !=`
- 邏輯：`&& || !`
- 位元（選配）：`& | ^ ~ << >>`
- 指派：`=`（右結合）
- 一元：`-` `!`
- 括號
- 函式呼叫

**語句**
- 宣告：`int x;`, `int x = 10;`
- 指派：`x = expr;`
- `if (...) stmt [else stmt]`
- `while (...) stmt`
- `return [expr];`
- `break;` / `continue;`（選配）
- 區塊：`{ ... }`

**函式**
- 定義、呼叫
- 前向宣告
- 互相遞迴

**其他**
- 行與區塊註解
- 錯誤恢復（用 `error` token）

### 輸出

1. **AST 檔**：`minic input.mc --ast=tree.dot`，產 Graphviz 檔
2. **IR 檔**：`minic input.mc --ir=out.tac`，產三位址碼
3. **錯誤報告**：行號、列號、明確訊息

## Graphviz AST 視覺化

Graphviz 的 dot 格式簡單：

```
digraph AST {
    node [shape=box, fontname="monospace"];
    n0 [label="Program"];
    n1 [label="FunDef: main"];
    n0 -> n1;
    n2 [label="Block"];
    n1 -> n2;
    n3 [label="Return"];
    n2 -> n3;
    n4 [label="Int 42"];
    n3 -> n4;
}
```

產生 PNG：
```bash
dot -Tpng tree.dot -o tree.png
```

### 實作

```c
static int node_id = 0;

int emit_ast_dot(FILE *f, Ast *n) {
    int id = node_id++;
    if (!n) {
        fprintf(f, "  n%d [label=\"<null>\"];\n", id);
        return id;
    }
    fprintf(f, "  n%d [label=\"%s\"];\n", id, ast_label(n));
    switch (n->kind) {
    case AST_BIN: {
        int l = emit_ast_dot(f, n->bin.l);
        int r = emit_ast_dot(f, n->bin.r);
        fprintf(f, "  n%d -> n%d;\n", id, l);
        fprintf(f, "  n%d -> n%d;\n", id, r);
        break;
    }
    case AST_IF: {
        int c = emit_ast_dot(f, n->ifs.cond);
        int t = emit_ast_dot(f, n->ifs.then);
        fprintf(f, "  n%d -> n%d [label=\"cond\"];\n", id, c);
        fprintf(f, "  n%d -> n%d [label=\"then\"];\n", id, t);
        if (n->ifs.els) {
            int e = emit_ast_dot(f, n->ifs.els);
            fprintf(f, "  n%d -> n%d [label=\"else\"];\n", id, e);
        }
        break;
    }
    /* ... 各種節點 ... */
    }
    return id;
}

void dump_ast_dot(FILE *f, Ast *root) {
    fprintf(f, "digraph AST {\n  node [shape=box, fontname=\"monospace\"];\n");
    emit_ast_dot(f, root);
    fprintf(f, "}\n");
}
```

## 三位址碼（TAC）

TAC 是一種簡單的中介表示，每條指令最多三個位址（兩個來源、一個目的）。

### 指令格式

```
t1 = a + b           ; 算術
t2 = -x              ; 一元
t3 = t1 < t2         ; 比較
x = t3               ; 指派
ifFalse t3 goto L1   ; 條件跳轉
goto L2              ; 無條件跳轉
L1:                  ; 標籤
param a              ; 壓參數
call foo, 2, t4      ; 呼叫 (n 個參數、結果存 t4)
return t4            ; 回傳
```

### 資料結構

```c
typedef enum {
    TAC_ADD, TAC_SUB, TAC_MUL, TAC_DIV, TAC_MOD,
    TAC_NEG, TAC_NOT,
    TAC_LT, TAC_GT, TAC_LE, TAC_GE, TAC_EQ, TAC_NE,
    TAC_ASSIGN,
    TAC_LABEL, TAC_GOTO, TAC_IFFALSE, TAC_IFTRUE,
    TAC_PARAM, TAC_CALL, TAC_RETURN,
    TAC_COPY,
} TacOp;

typedef struct { char *name; } Tac_Operand;

typedef struct TacInstr {
    TacOp op;
    char *dst;
    char *a, *b;      /* 來源 1、2 */
    int label;
    int nargs;        /* for call */
    struct TacInstr *next;
} TacInstr;

typedef struct {
    TacInstr *head, *tail;
    int next_temp;
    int next_label;
} TacList;
```

### 產生 TAC：visitor 模式

```c
const char *gen_expr(TacList *list, Ast *n) {
    /* 回傳結果所在的 temp 或 變數名 */
    switch (n->kind) {
    case AST_INT_LIT: {
        char *buf = strbuf_printf("%d", n->i);   /* 或用字面量直接 */
        return buf;
    }
    case AST_VAR_REF:
        return n->name;
    case AST_BIN: {
        const char *l = gen_expr(list, n->bin.l);
        const char *r = gen_expr(list, n->bin.r);
        char *t = new_temp(list);
        emit_tac(list, op_to_tac(n->bin.op), t, l, r);
        return t;
    }
    case AST_CALL: {
        int nargs = 0;
        LIST_FOREACH(p, n->call.args) {
            const char *a = gen_expr(list, p->node);
            emit_tac(list, TAC_PARAM, NULL, a, NULL);
            nargs++;
        }
        char *t = new_temp(list);
        emit_call(list, n->call.name, nargs, t);
        return t;
    }
    }
}

void gen_stmt(TacList *list, Ast *n) {
    switch (n->kind) {
    case AST_ASSIGN: {
        const char *v = gen_expr(list, n->assign.value);
        emit_tac(list, TAC_COPY, strdup(n->assign.name), v, NULL);
        break;
    }
    case AST_IF: {
        const char *c = gen_expr(list, n->ifs.cond);
        int L_else = new_label(list);
        int L_end  = new_label(list);
        emit_iffalse(list, c, L_else);
        gen_stmt(list, n->ifs.then);
        emit_goto(list, L_end);
        emit_label(list, L_else);
        if (n->ifs.els) gen_stmt(list, n->ifs.els);
        emit_label(list, L_end);
        break;
    }
    case AST_WHILE: {
        int L_top = new_label(list);
        int L_end = new_label(list);
        emit_label(list, L_top);
        const char *c = gen_expr(list, n->wh.cond);
        emit_iffalse(list, c, L_end);
        gen_stmt(list, n->wh.body);
        emit_goto(list, L_top);
        emit_label(list, L_end);
        break;
    }
    case AST_RETURN: {
        const char *v = n->ret.value ? gen_expr(list, n->ret.value) : NULL;
        emit_return(list, v);
        break;
    }
    case AST_BLOCK:
        LIST_FOREACH(p, n->block) gen_stmt(list, p->node);
        break;
    }
}
```

### TAC 印出

```c
void print_tac(FILE *f, TacList *list) {
    for (TacInstr *p = list->head; p; p = p->next) {
        switch (p->op) {
        case TAC_ADD: fprintf(f, "%s = %s + %s\n", p->dst, p->a, p->b); break;
        case TAC_COPY: fprintf(f, "%s = %s\n", p->dst, p->a); break;
        case TAC_LABEL: fprintf(f, "L%d:\n", p->label); break;
        case TAC_GOTO:  fprintf(f, "goto L%d\n", p->label); break;
        case TAC_IFFALSE: fprintf(f, "ifFalse %s goto L%d\n", p->a, p->label); break;
        case TAC_PARAM: fprintf(f, "param %s\n", p->a); break;
        case TAC_CALL:  fprintf(f, "%s = call %s, %d\n", p->dst, p->a, p->nargs); break;
        case TAC_RETURN: fprintf(f, "return %s\n", p->a ? p->a : ""); break;
        /* ... */
        }
    }
}
```

## 範例：輸入 → TAC

輸入：

```c
int fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}
```

預期 TAC（大致）：

```
fact:
    t1 = n <= 1
    ifFalse t1 goto L1
    return 1
L1:
    t2 = n - 1
    param t2
    t3 = call fact, 1
    t4 = n * t3
    return t4
```

## 命令列介面

```c
int main(int argc, char **argv) {
    const char *input = NULL;
    const char *ast_out = NULL;
    const char *ir_out = NULL;

    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--ast=", 6) == 0) ast_out = argv[i] + 6;
        else if (strncmp(argv[i], "--ir=", 5) == 0) ir_out = argv[i] + 5;
        else input = argv[i];
    }

    if (input) yyin = fopen(input, "r");

    if (yyparse() != 0 || !root) return 1;
    register_funs(root);
    symtab_init();
    check_program(root);
    if (error_count > 0) return 1;

    if (ast_out) {
        FILE *f = fopen(ast_out, "w");
        dump_ast_dot(f, root);
        fclose(f);
    }

    if (ir_out) {
        TacList list = {0};
        LIST_FOREACH(p, root->block) {
            if (p->node->kind == AST_FUN_DEF) {
                emit_label_str(&list, p->node->fun.name);
                gen_stmt(&list, p->node->fun.body);
            }
        }
        FILE *f = fopen(ir_out, "w");
        print_tac(f, &list);
        fclose(f);
    }

    return 0;
}
```

## 完整測試集

建一個 `tests/` 目錄：

```
tests/
├── 01-arith.mc       # 基本算術
├── 02-control.mc     # if/while
├── 03-funs.mc        # 函式
├── 04-recursion.mc   # 遞迴
├── 05-mutual.mc      # 互相遞迴
├── 06-scope.mc       # 作用域
├── 07-err-type.mc    # 型別錯誤
├── 08-err-syntax.mc  # 語法錯誤
```

寫個 bash 測試腳本：

```bash
for f in tests/*.mc; do
    echo "=== $f ==="
    ./minic "$f" --ast="${f%.mc}.dot" --ir="${f%.mc}.tac"
    # 比對輸出
done
```

## 進度檢查表

做完 MiniC 前端，你應該：

- [ ] 一套完整的 `.l` + `.y` + 語義 + IR 的 code base
- [ ] 至少 8 個測試案例都能跑通
- [ ] 能產生 Graphviz 圖，視覺化 AST
- [ ] 能產生 TAC，且 TAC 結構正確（if/while 的跳躍標籤對）
- [ ] 語法錯誤、未宣告變數、型別不符都有明確訊息

## 延伸方向（做完之後）

想再進一步的話，這些是自然的下一步：

1. **後端**：把 TAC 翻成組合語言（x86 或 ARM），或直接用 LLVM IR
2. **SSA 形式**：把 TAC 轉成 SSA，支援常見最佳化
3. **錯誤訊息強化**：像 Rust/Elm 那樣顯示程式碼片段、波浪線指示錯誤位置
4. **更豐富的型別系統**：泛型、型別推論（Hindley-Milner）
5. **REPL**：讓你的語言支援互動模式
6. **換工具鏈**：改用 ANTLR 或手寫 recursive descent parser，比較 yacc 的優缺點

## 結語

至此整個「編譯器前端 + 簡單 IR」的課程完結。你手上的 code base 大概 1500–3000 行 C，但涵蓋了真實編譯器的核心結構。

這套技能可以直接應用在：
- 寫 DSL（配置語言、查詢語言）
- 讀懂 gcc/clang/bison 的錯誤訊息
- 理解你每天用的語言為什麼這樣設計
- 繼續進入編譯器後端與最佳化的世界

祝寫出一個好編譯器。

← [回到總目錄](./README.md)
