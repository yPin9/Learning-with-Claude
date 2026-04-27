# Ch 11 — Struct 自動推斷腳本

> 目標：從 `[reg+offset]` 的存取 pattern 自動收集 offset、開 struct、套回 code。把 Ch 5 的手動流程自動化。

## 演算法

```
輸入：一個 function 和一個 register（或 LVAR）— 懷疑是 struct pointer
輸出：一個 struct layout，套到這個 pointer 上

步驟：
  1. 迭代 function 所有指令
  2. 找到 operand type 是 o_displ 的（即 [reg+offset]）
  3. 檢查 base register 是不是我們懷疑的
  4. 收集 (offset, access_size) 組
  5. 合併：同一 offset 可能多個 access size → 取最大 access type 當 field
  6. 找「連續空洞」決定 padding
  7. 輸出 C struct 宣告
  8. parse_decl + apply_tinfo 套上去
```

## 先寫：收集 access pattern

```python
import idautils
import ida_funcs, ida_ua, ida_bytes
import idaapi

def collect_accesses(func_ea, base_reg_name):
    """
    對 func_ea 這個 function，追蹤 base_reg_name 上的所有 [reg+X] 存取。
    回傳 [(offset, size, ea), ...]
    """
    f = ida_funcs.get_func(func_ea)
    if not f:
        return []

    results = []
    for ea in idautils.FuncItems(func_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0:
            continue

        for op in insn.ops:
            if op.type == ida_ua.o_void:
                break
            if op.type != ida_ua.o_displ:
                continue

            # 用 print_operand 取文字表現裡的 register 名（簡單粗暴）
            op_txt = ida_ua.print_operand(ea, op.n).lower()
            if base_reg_name.lower() not in op_txt:
                continue

            # op.addr 是 displacement
            offset = op.addr
            # 某些平台 offset 會 sign-extend，需裁成 unsigned 合理範圍
            if offset > 0x100000:
                offset -= 0x100000000
            size = operand_access_size(op)
            results.append((offset, size, ea))

    return results

def operand_access_size(op):
    """從 operand dtype 估 access size"""
    dtype_size = {
        ida_ua.dt_byte:    1,
        ida_ua.dt_word:    2,
        ida_ua.dt_dword:   4,
        ida_ua.dt_qword:   8,
        ida_ua.dt_tbyte:   10,
        ida_ua.dt_float:   4,
        ida_ua.dt_double:  8,
    }
    return dtype_size.get(op.dtype, 4)
```

**這個版本的限制**（你該知道）：

- 用 `print_operand` 字串比對找 base register 是 hacky — 對 AT&T / Intel syntax、對 suffix（`rbx` 而 `[ebx+0x4]` 的 32-bit 版）表現不同。生產用法應該用 `op.reg` 加上 processor module 的 reg table 做精確比對。這裡為可讀性簡化。
- 沒處理 index register `[base + index*scale + disp]`。真實 code 有大量這種 — 要另外統計 scale。
- 沒處理「base reg 傳給別的 reg，後者再當 base」— 需要小型 dataflow。

先吃基本版，後面練習會讓你強化。

## 推 layout：把 access 列合併成 struct

```python
def fields_from_accesses(accesses):
    """
    accesses: [(offset, size, ea), ...]
    回傳 [(offset, size, suggested_name), ...] 排序過
    """
    # 同 offset 取最大 size
    by_off = {}
    for off, sz, _ea in accesses:
        if off < 0:
            continue                        # negative offset 常見（下方 stack frame），這章不處理
        by_off[off] = max(by_off.get(off, 0), sz)

    # 排序並去重疊（例如 offset=0x8 size=8 會覆蓋 offset=0xC size=4，簡化版跳過重疊檢查）
    fields = []
    for off in sorted(by_off):
        sz = by_off[off]
        fields.append((off, sz, f"field_{off:X}"))
    return fields

def size_to_ctype(sz):
    return {1: "uint8_t", 2: "uint16_t", 4: "uint32_t", 8: "uint64_t"}.get(sz, "uint8_t")

def render_struct(name, fields):
    """輸出 C struct 宣告，含 padding"""
    lines = [f"struct {name} {{"]
    prev_end = 0
    for off, sz, fname in fields:
        if off > prev_end:
            pad_size = off - prev_end
            lines.append(f"    uint8_t pad_{prev_end:X}[{pad_size}];")
        ctype = size_to_ctype(sz)
        lines.append(f"    {ctype} {fname};  // 0x{off:X}")
        prev_end = off + sz
    lines.append("};")
    return "\n".join(lines)
```

用法：

```python
accesses = collect_accesses(0x401200, "rbx")
fields = fields_from_accesses(accesses)
print(render_struct("Auto_401200", fields))
```

輸出 like：

```c
struct Auto_401200 {
    uint64_t field_0;  // 0x0
    uint32_t field_8;  // 0x8
    uint8_t pad_C[4];
    uint64_t field_10;  // 0x10
    uint8_t field_18;  // 0x18
};
```

## 套到 IDB

```python
import ida_typeinf

def create_and_apply_struct(struct_name, struct_decl_c, target_ea, as_pointer=True):
    """
    struct_decl_c: 完整 C 宣告（含 `struct X { ... };`）
    target_ea: 要套到哪個位址（通常是 function 的 LVAR ea，這裡暫以 ea 為例）
    """
    # 1. parse_decls 把 C 宣告塞進 local types
    ida_typeinf.idc_parse_types(struct_decl_c, 0)

    # 2. 建 tinfo 指向這個 struct
    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, struct_name):
        print(f"failed to find {struct_name} in local types")
        return False

    if as_pointer:
        ptr_tif = ida_typeinf.tinfo_t()
        ptr_tif.create_ptr(tif)
        tif = ptr_tif

    # 3. apply 到 ea（此例把它當 global variable — 實戰中常要改是 apply 到 LVAR 或 function arg）
    ida_typeinf.apply_tinfo(target_ea, tif, ida_typeinf.TINFO_DEFINITE)
    return True
```

**套到 LVAR 是另一段故事**：需要打開 pseudocode 對應的 `cfunc_t`，修改 `lvar_t` 的 type。這屬於 Hex-Rays API，Ch 13 專門講。為了讓這一章可以單跑，我們先實戰到 global / function prototype。

## 完整 pipeline 實戰

```python
def auto_struct_for_func(func_ea, base_reg, struct_name):
    """
    對 func_ea 的 base_reg 存取自動推 struct 並 print。
    不自動 apply — 印出來你確認後再手動套，或把 apply 那行打開。
    """
    accesses = collect_accesses(func_ea, base_reg)
    if not accesses:
        print("no accesses found")
        return

    fields = fields_from_accesses(accesses)
    decl = render_struct(struct_name, fields)
    print(decl)
    print()
    print(f"// found {len(accesses)} accesses, {len(fields)} distinct offsets")

    # 需要自動 apply 時打開
    # create_and_apply_struct(struct_name, decl, func_ea, as_pointer=True)

auto_struct_for_func(0x401200, "rbx", "Auto_RBX")
```

## 強化：偵測 nested struct

如果 offset 0x18 永遠接著是 offset 0x18 + 0x0、0x18 + 0x4、0x18 + 0x8 的存取（透過另一個 register 拿到 0x18 值之後又開始 `[reg2+X]`），那 0x18 可能是 pointer 到 nested struct。

偵測邏輯：

1. 收集每個 `mov reg2, [base+X]` — 記 `base+X` 產生的 alias reg2。
2. 追 reg2 當 base 的 access — 那是 nested struct 的 field。
3. 在主 struct 的 X offset 型別寫成 `NestedStruct *`。

這個完整寫要 taint analysis 概念，留做練習。

## 強化：型別 hint 從 compare 拿

如果看到 `cmp [base+0x8], 0x41414141`，那 0x8 顯然是 4 bytes int，且可能有 magic number — 可以在 comment 裡留「looks like magic」。

`cmp [base+0x0], 0` + `jz` → 0x0 是 nullable pointer 或 bool。

寫 script 時可以加 hint：

```python
def hint_for_access(ea, offset):
    insn = ida_ua.insn_t()
    if ida_ua.decode_insn(insn, ea) <= 0:
        return None
    mnem = ida_ua.print_insn_mnem(ea).lower()
    if mnem == "cmp":
        # 對照另一 operand 是否是常數
        for op in insn.ops:
            if op.type == ida_ua.o_imm:
                return f"compared against 0x{op.value:X}"
    return None
```

這種 hint 放進 struct field 的 comment 會很實用。

## 踩雷 & 邊界

- **stack frame 的 `[rbp-X]`**：offset 是負的。那是 stack var，不是 heap struct，要分開處理（IDA 本身有 stack frame 自動分析，別重造）。
- **同一個 function 裡 base register 被重新賦值**：你以為 `rbx` 都指同一個 struct，其實中間有 `mov rbx, rax`，後半段 `rbx` 是別的 object。這需要 basic block + live range 分析。
- **struct 欄位其實是 union**：同一 offset 不同 size 被讀寫，`by_off[off] = max(...)` 會丟失資訊。進階版要記下所有 access 的 size 和 context。
- **SIMD / 16-byte access**：`movups [rbx+0x20], xmm0` 是 16 bytes write。要在 `dtype_size` 表補 `dt_xword = 16`。

## 動手練習

1. 把 `collect_accesses` 改用 `op.reg` 與 processor module 的 register table 精確比對 base reg（去掉 print_operand 字串比對）。
2. 加 index+scale 支援：同一 base 若有 `[base + index*scale + disp]` 存取，表示這個 offset 是 array 的開頭，輸出成 `type field[N]`。
3. 加 hint_for_access：cmp、test、bt 遇到時把推斷放進 comment。
4. 最難：偵測 nested struct pointer — 如 `mov rcx, [rbx+0x8]; mov edx, [rcx+0x4]` 代表 0x8 是 pointer 到另一個 struct。

## 自我檢核

- [ ] 能用 `ida_ua.decode_insn` 找 `o_displ` operand
- [ ] 能組合 collect + render + apply 完整 pipeline
- [ ] 知道 stack frame 的負 offset 不是這個演算法的範圍
- [ ] 知道 union / SIMD / nested 是邊界 case
- [ ] 能輸出 C struct 宣告

下一章處理字串和常數解混淆 — 實戰 XOR / stack string 還原。

→ [Ch 12 字串 / 常數解混淆](./12-string-const-deobfuscation.md)
