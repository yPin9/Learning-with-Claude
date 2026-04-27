# Ch 13 — Hex-Rays API 入門（ctree / vdui）

> 目標：知道 `cfunc_t` / `ctree_visitor_t` / `vdui_t` 在幹嘛，能遍歷偽代碼 AST、讀取當前游標位置、改 LVAR 名與型別。

## 為什麼要碰 Hex-Rays API

Part 2 所有腳本都在 disasm 層做事（decode 指令、改 name、加 comment）。有些事只有在 pseudocode 層才方便：

- **改某個 function 裡第 3 個 LVAR 的名字**：disasm 層沒這概念，pseudocode 層才有 `lvar_t`。
- **遍歷 if / while / switch 結構**：disasm 是一條條指令，只有 pseudocode 有結構資訊。
- **根據語義而非語法找 pattern**：例如「找所有 `strcpy(buf, argv[N])`」，不管編譯器怎麼 layout 指令。
- **對 pseudocode 加 C 風格註解**（`/`）。

Hex-Rays 的世界觀長這樣：

```
┌─────────────────────────────────────────┐
│       Raw bytes (binary)                │
└─────────────────┬───────────────────────┘
                  │ disasm
                  ▼
┌─────────────────────────────────────────┐
│       Assembly instructions             │  ← Part 2 的世界
│       (ida_ua / idautils)               │
└─────────────────┬───────────────────────┘
                  │ Hex-Rays lift
                  ▼
┌─────────────────────────────────────────┐
│       Microcode (mba_t)                 │  ← 進階，本章略提
│       多層優化 passes                   │
└─────────────────┬───────────────────────┘
                  │ structuring
                  ▼
┌─────────────────────────────────────────┐
│       Ctree (cfunc_t)                   │  ← 本章主體
│       類 C AST，pseudocode 的資料模型    │
└─────────────────┬───────────────────────┘
                  │ print
                  ▼
┌─────────────────────────────────────────┐
│       Pseudocode text (畫面上看到的)    │
└─────────────────────────────────────────┘
```

我們主要玩 Ctree 層。Microcode 留給更深入的主題（編譯混淆清除、symbolic execution）。

## 核心物件

### `cfunc_t` — 一個 function 的偽代碼物件

```python
import ida_hexrays

cfunc = ida_hexrays.decompile(0x401200)
if cfunc is None:
    print("decompile failed")
else:
    print(str(cfunc))                  # 印出偽代碼
    print(cfunc.entry_ea)              # function 起始
    print(cfunc.lvars)                 # lvars_t，所有 local variable
    print(cfunc.body)                  # cinsn_t，function body 的 AST root
```

### `lvar_t` — 一個 local variable

```python
for lv in cfunc.lvars:
    print(lv.name, lv.type(), lv.location, lv.is_arg_var)
```

### `citem_t` / `cexpr_t` / `cinsn_t` — AST 節點

```
citem_t (base)
├── cexpr_t (expression)    ← 有回傳值
│       如 a + b, *p, foo(1,2)
└── cinsn_t (statement)     ← 無回傳值
        如 if(...){...}, return, while(...), compound block
```

每個 node 有 `op`（操作類型），例如：

| op (cexpr) | 意義 |
|---|---|
| `cot_num` | 常數 |
| `cot_var` | 變數 ref |
| `cot_call` | function call |
| `cot_asg` | 賦值 |
| `cot_add` | + |
| `cot_obj` | global object ref |

| op (cinsn) | 意義 |
|---|---|
| `cit_if` | if |
| `cit_while` | while |
| `cit_for` | for |
| `cit_switch` | switch |
| `cit_return` | return |
| `cit_block` | `{ ... }` |

### `vdui_t` — 當前 pseudocode 視窗的 UI context

```python
vu = ida_hexrays.get_widget_vdui(ida_kernwin.get_current_widget())
if vu:
    vu.refresh_view(True)              # 重畫 pseudocode
    print(vu.cfunc)                    # 當前 function 的 cfunc_t
    print(vu.item)                     # 當前游標下的 ctree_item_t
```

`vdui_t` 只在 UI 操作時用（寫 hotkey plugin 會用到）；純分析腳本用 `decompile(ea)` 就夠。

## 遍歷 Ctree：`ctree_visitor_t`

找所有 function call：

```python
import ida_hexrays

class CallFinder(ida_hexrays.ctree_visitor_t):
    def __init__(self):
        ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)
        self.calls = []

    def visit_expr(self, expr):
        if expr.op == ida_hexrays.cot_call:
            # expr.x 是被呼叫的 callee (cexpr_t)
            # expr.a 是 arg list
            callee_ea = expr.x.obj_ea if expr.x.op == ida_hexrays.cot_obj else None
            self.calls.append((expr.ea, callee_ea, len(expr.a)))
        return 0                        # 繼續遍歷

cfunc = ida_hexrays.decompile(0x401200)
finder = CallFinder()
finder.apply_to(cfunc.body, None)

for ea, callee, argc in finder.calls:
    print(f"[{ea:#x}] call to {callee:#x}  ({argc} args)")
```

`visit_expr` / `visit_insn` 回傳 0 繼續、非 0 停止。

## 實戰 1：找所有 `strcpy(dst, src)` 並看 src 是不是 tainted

```python
class StrcpyFinder(ida_hexrays.ctree_visitor_t):
    def __init__(self, strcpy_ea):
        ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)
        self.strcpy_ea = strcpy_ea
        self.hits = []

    def visit_expr(self, expr):
        if expr.op != ida_hexrays.cot_call:
            return 0
        if expr.x.op != ida_hexrays.cot_obj:
            return 0
        if expr.x.obj_ea != self.strcpy_ea:
            return 0

        args = expr.a
        if len(args) < 2:
            return 0
        dst, src = args[0], args[1]
        # dst 通常是 local buffer（cot_ref + cot_var）
        # src 若是 argv[N] 或 recv buffer 的 deref，算潛在 tainted
        src_str = self._expr_repr(src)
        self.hits.append((expr.ea, src_str))
        return 0

    def _expr_repr(self, expr):
        return ida_hexrays.tag_remove(expr.print1(None))

strcpy_ea = ida_name.get_name_ea(idaapi.BADADDR, "strcpy")
for func_ea in idautils.Functions():
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        continue
    fr = StrcpyFinder(strcpy_ea)
    fr.apply_to(cfunc.body, None)
    for call_ea, src in fr.hits:
        print(f"{call_ea:#x}: strcpy(...,  {src})")
```

這樣可以一次性列出所有 strcpy call 的第二個 arg，直接看哪些像從外部來。手動 audit 省大量時間。

## 實戰 2：重命名 LVAR

```python
def rename_lvar(func_ea, old_name, new_name):
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        return False

    for lv in cfunc.lvars:
        if lv.name == old_name:
            # 建 lvar_saved_info_t 並呼叫 modify_user_lvars
            lsi = ida_hexrays.lvar_saved_info_t()
            lsi.ll = lv
            lsi.name = new_name
            if ida_hexrays.modify_user_lvar_info(func_ea, ida_hexrays.MLI_NAME, lsi):
                return True
    return False
```

改 LVAR 型別同理，改成 `ida_hexrays.MLI_TYPE` 並設 `lsi.type`。

## 實戰 3：改 LVAR 型別（搭 Ch 11 的 struct 自動推斷）

Ch 11 我們留了一個 TODO：推出 struct 後怎麼套到 LVAR。答案在這：

```python
def apply_struct_to_lvar(func_ea, lvar_name, struct_name):
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        return False

    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, struct_name):
        return False

    ptr_tif = ida_typeinf.tinfo_t()
    ptr_tif.create_ptr(tif)

    for lv in cfunc.lvars:
        if lv.name == lvar_name:
            lsi = ida_hexrays.lvar_saved_info_t()
            lsi.ll = lv
            lsi.type = ptr_tif
            return ida_hexrays.modify_user_lvar_info(func_ea, ida_hexrays.MLI_TYPE, lsi)
    return False
```

## Microcode 簡介（知道存在就好）

Microcode 是 Hex-Rays 在產 pseudocode 前的 IR。每條 assembly 指令會展成數條 microcode op，再經過 20+ 個 optimization pass 才成為 pseudocode。

用處：
- **反混淆**：有些 control-flow obfuscation 在 pseudocode 已經被某些 pass 部分還原，剩下的要在 microcode 層做 custom pass 才能清乾淨。
- **Symbolic execution** / **constraint solving** 的底層。

入口：

```python
mba = ida_hexrays.gen_microcode(
    ida_hexrays.mba_ranges_t(ida_funcs.get_func(ea)),
    ida_hexrays.mmat_generated     # 選哪個 maturity level
)
```

不同 maturity level（`mmat_*`）代表不同 pass 階段 — `generated` 最早期，`glbopt3` 最後期。寫 custom pass 會要選對 level。

這一章不深入。知道「pseudocode 上的高階動作做不了時，往 microcode 下潛」即可。

## 常見踩雷

- **`decompile()` 回 None**：這個 function Hex-Rays 放棄了（大概是 indirect call / obfuscation / 不支援的 idiom）。你的 analyzer 要處理 None 情況。
- **ctree 遍歷遞迴太深 stack overflow**：極大 function 才遇到。`CV_FAST` 是非遞迴遍歷，比 `CV_NORMAL` 安全。
- **改 LVAR 後畫面沒更新**：在 UI context 下要 `vu.refresh_view(True)`。純分析腳本跑 `ida_hexrays.decompile(ea)` 已經是獨立物件，不需要 refresh。
- **`expr.print1(None)` 會帶 IDA color tag**：`ida_hexrays.tag_remove(s)` 去掉。

## 動手練習

1. 寫一個 visitor 列出當前 function 裡所有 `while` / `for` loop 的起始位址（`cit_while` / `cit_for`）。
2. 寫 visitor 找所有 `cot_asg`（賦值），列出 target 是 struct field 的（`cot_memref` / `cot_memptr`）。
3. 寫一個 `rename_all_vX_by_type`：掃 cfunc.lvars，把所有 `v1` / `v2` 這種 Hex-Rays 預設名、且 type 是 `FILE *` 的，批次改名為 `fp`。
4. 寫 visitor 找「return value 沒被檢查」的 call：`call` 之後的 `cinsn_t` 直接是下一個 statement 而非 `cit_if` 檢查回傳值。

## 自我檢核

- [ ] 能 `decompile(ea)` 拿 cfunc_t
- [ ] 知道 cexpr_t / cinsn_t 差別，會用 op 常數判別
- [ ] 會寫 ctree_visitor_t 遍歷
- [ ] 能用 `modify_user_lvar_info` 改 LVAR 名與型別
- [ ] 知道 microcode 是 pseudocode 之下還有一層，但本章不深入

最後一章把腳本包成 action + hotkey，真正變成你日常工具。

→ [Ch 14 把 script 包成一鍵觸發](./14-actions-and-hotkeys.md)
