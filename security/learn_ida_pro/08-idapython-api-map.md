# Ch 8 — 核心 API 地圖

> 目標：建立 `ida_bytes` / `ida_funcs` / `ida_name` / `ida_xref` / `ida_ua` / `ida_segment` / `ida_hexrays` 的心智地圖，下次寫 script 知道去哪個模組找函式。

## 心智地圖

```
┌─────────────────────────────────────────────────────────┐
│                        IDB                              │
├───────────────┬──────────────┬──────────────┬──────────┤
│   Bytes       │   Names      │   Funcs      │  Xrefs   │
│   (raw data)  │   (symbol)   │   (boundary) │  (graph) │
├───────────────┼──────────────┼──────────────┼──────────┤
│  ida_bytes    │  ida_name    │  ida_funcs   │ ida_xref │
└───────┬───────┴──────┬───────┴──────┬───────┴────┬─────┘
        │              │              │            │
        ▼              ▼              ▼            ▼
┌────────────────────────────────────────────────────────┐
│             idautils  (高階 iterator)                  │
└────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              指令解碼  /  型別  /  UI                   │
├───────────────┬──────────────┬──────────────┬──────────┤
│  ida_ua       │ ida_typeinf  │ ida_kernwin  │ ida_     │
│  (decode insn)│ (struct/type)│ (UI / hotkey)│ hexrays  │
└───────────────┴──────────────┴──────────────┴──────────┘
```

每個模組只對應一塊 IDB 的語意。

## `ida_bytes` — 最底層的讀寫

**用途**：讀寫 byte-level 資料、查 data flag（是 code 還是 data）、patch。

```python
import ida_bytes

# 讀
ida_bytes.get_byte(ea)                      # 1 byte
ida_bytes.get_word(ea)                      # 2 bytes (native endian)
ida_bytes.get_dword(ea)                     # 4 bytes
ida_bytes.get_qword(ea)                     # 8 bytes
ida_bytes.get_bytes(ea, size)               # bytes object
ida_bytes.get_strlit_contents(ea, -1, 0)    # 自動偵測長度的字串

# 寫（會改 IDB 裡的 byte，通常不會動原檔）
ida_bytes.patch_byte(ea, 0x90)              # NOP
ida_bytes.patch_bytes(ea, b"\x90\x90\x90")

# data 屬性
ida_bytes.is_code(ea)                       # 是指令?
ida_bytes.is_data(ea)                       # 是 data?
ida_bytes.is_unknown(ea)                    # 還沒分析?
ida_bytes.is_loaded(ea)                     # 這個位址在任何 segment 裡?

# Undefine / define
ida_bytes.del_items(ea, 0, size)            # 相當於按 U
ida_bytes.create_byte(ea, size)             # 相當於 D
ida_bytes.create_dword(ea)                  # D 但指定 dword
ida_bytes.create_strlit(ea, length, STRTYPE_C)  # 相當於 A
```

**踩雷**：`patch_byte` 改的是 IDB 裡的 snapshot。Debug 時 patch 要用 `dbg_write_memory`（`ida_dbg`）才會進 target process。

## `ida_name` — 符號名

```python
import ida_name

# 讀
ida_name.get_name(ea)                       # symbol at this ea (空字串若沒 symbol)
ida_name.get_ea_name(ea, ida_name.GN_VISIBLE)
                                            # 顯示用的名字（含 alias 處理）

# 寫
ida_name.set_name(ea, "my_func")            # 設一般 name（會自動避免重複）
ida_name.set_name(ea, "my_func", ida_name.SN_NOWARN)
                                            # SN_NOWARN: 重複時不跳警告
ida_name.force_name(ea, "my_func")          # 強制覆寫

# 找 name -> ea
ida_name.get_name_ea(0, "printf")           # 0 = search base，通常 BADADDR 或 0
```

**命名規則**：IDA 不接受 `.` / `@` 等字元，會自動取代成 `_`。你傳 `"My Func"` 進來會變 `My_Func`。想知道為什麼名字沒生效，通常是這個。

## `ida_funcs` — function 邊界

```python
import ida_funcs

# 讀
f = ida_funcs.get_func(ea)                  # 回 func_t or None
if f:
    print(f.start_ea, f.end_ea, f.size())
    print(f.flags)                          # FUNC_LIB, FUNC_STATICDEF, ...

ida_funcs.get_func_name(ea)                 # shortcut, 等同 ida_name.get_name

# 新增 / 移除
ida_funcs.add_func(start_ea, end_ea)        # 手動把一段 code 標成 function
ida_funcs.del_func(ea)                      # 把 function 標記移除（指令還在）

# 全部 function 迭代用 idautils
import idautils
for ea in idautils.Functions():
    ...
```

**常見 `f.flags`**：

- `FUNC_NORET` (0x1)：不 return（`exit`、`abort`）
- `FUNC_LIB` (0x4)：library function（FLIRT 命中）
- `FUNC_STATIC` (0x8)：static
- `FUNC_THUNK` (0x80)：thunk（PLT entry）

## `ida_xref` — 交叉引用

```python
import ida_xref
import idautils

# 用 idautils 比較方便
for xref in idautils.XrefsTo(ea, 0):        # 誰參照這裡
    print(f"{xref.frm:#x} -> {xref.to:#x}  type={xref.type}")

for xref in idautils.XrefsFrom(ea, 0):      # 這裡參照誰
    ...

# Code xref / Data xref 分開
for xref in idautils.CodeRefsTo(ea, 1):     # 1 = include flow xrefs
    ...
for xref in idautils.DataRefsTo(ea):        # data ref
    ...
```

`xref.type` 的值（XREF_...）：

| 常數 | 意義 |
|---|---|
| `fl_CN` | call near |
| `fl_CF` | call far |
| `fl_JN` | jump near |
| `fl_JF` | jump far |
| `fl_F`  | ordinary flow |
| `dr_O`  | data offset |
| `dr_W`  | data write |
| `dr_R`  | data read |

**手動加 xref**：`ida_xref.add_cref(frm, to, fl_CN)`。這在還原 indirect call 時有用（`call [rax]` 你分析出 rax 其實永遠是某個 function，就手動加 xref）。

## `ida_ua` — 指令解碼

**用途**：把一個位址的指令拆成 mnemonic + operands。

```python
import ida_ua

insn = ida_ua.insn_t()
length = ida_ua.decode_insn(insn, ea)
if length > 0:
    print(ida_ua.print_insn_mnem(ea))       # 'mov', 'call', ...
    print(insn.itype)                       # 處理器特定 instruction id
    # 每個 operand
    for op in insn.ops:
        if op.type == ida_ua.o_void:
            break
        print(op.type, op.reg, op.value, op.addr)
```

**operand type**：

| type | 意義 |
|---|---|
| `o_reg` (1)  | register |
| `o_mem` (2)  | direct memory ref |
| `o_phrase` (3) | `[reg]` / `[reg+reg]` |
| `o_displ` (4)  | `[reg+offset]` — 分析 struct 常看這個 |
| `o_imm` (5)    | immediate |
| `o_near` (7)   | near branch target |

**很常用的 helper**：

```python
ida_ua.print_operand(ea, op_idx)            # 拿 operand 的文字表現
ida_ua.print_insn_mnem(ea)                  # 拿 mnemonic
```

## `ida_segment` — 區段

```python
import ida_segment
import idautils

for seg_ea in idautils.Segments():
    s = ida_segment.getseg(seg_ea)
    print(s.start_ea, s.end_ea, ida_segment.get_segm_name(s), s.perm)
```

`s.perm` 是 bitmask：`SEGPERM_READ` / `SEGPERM_WRITE` / `SEGPERM_EXEC`。

**新增 segment**（firmware 分析時補 MMIO）：

```python
ida_segment.add_segm(0, start, end, "MMIO_GPIOA", "DATA")
```

## `ida_typeinf` — 型別（9.x 的主力）

9.x 把 struct / enum / typedef 全部統一進 `ida_typeinf`。舊的 `ida_struct` / `ida_enum` 仍在，但新 code 建議用 `ida_typeinf`。

```python
import ida_typeinf

# 解析一段 C declaration 變 tinfo_t
tif = ida_typeinf.tinfo_t()
ida_typeinf.parse_decl(tif, None, "struct MyStruct { int a; char b; };", ida_typeinf.PT_SIL)

# 套到某個位址
ida_typeinf.apply_tinfo(ea, tif, ida_typeinf.TINFO_DEFINITE)

# 改 function prototype
funcdata = ida_typeinf.func_type_data_t()
# ... (fill in args, rettype) ...
```

這部分 API 有點繞，Ch 11 做 struct 自動推斷時會實戰練熟。

## `ida_kernwin` — UI

```python
import ida_kernwin

# 跳到某位址
ida_kernwin.jumpto(0x401000)

# 對話框
ida_kernwin.ask_yn(1, "繼續嗎？")           # 1 = default Yes
ida_kernwin.ask_str("", 0, "輸入名稱:")

# 訊息（在 Output window）
ida_kernwin.msg(f"processed {n} funcs\n")

# Wait box（長跑時）
ida_kernwin.show_wait_box("Processing...")
try:
    # heavy work
    if ida_kernwin.user_cancelled():
        return
finally:
    ida_kernwin.hide_wait_box()
```

## `ida_hexrays` — Decompiler API（Ch 13 深入）

```python
import ida_hexrays
ida_hexrays.init_hexrays_plugin()           # 保險起見先叫

cfunc = ida_hexrays.decompile(ea)
if cfunc:
    print(cfunc)                            # 印出 pseudocode
    # 遍歷 ctree...
```

## 常用組合招

### 迭代所有指令

```python
import idautils
for ea in idautils.Heads():                 # 所有 defined item 的起始位址
    ...
```

### 找所有 call XXX 的地方

```python
target = ida_name.get_name_ea(idaapi.BADADDR, "strcpy")
for xref in idautils.XrefsTo(target, 0):
    if xref.type in (ida_xref.fl_CN, ida_xref.fl_CF):
        print(f"strcpy called from {xref.frm:#x}")
```

### 批次改名

```python
for ea in idautils.Functions():
    if ida_funcs.get_func(ea).flags & ida_funcs.FUNC_LIB:
        continue                            # 跳過 library function
    name = ida_name.get_name(ea)
    if name.startswith("sub_"):
        new = guess_name(ea)                # 你自己的規則
        if new:
            ida_name.set_name(ea, new, ida_name.SN_NOWARN)
```

## 常見踩雷

- **`BADADDR`**：IDA 用的 sentinel（0xFFFFFFFF 或 0xFFFFFFFFFFFFFFFF）。遇到 `get_name_ea` 等 API 回這個表示「沒找到」。
- **有些 API 的 flag 要 bitwise or**：`SN_NOWARN | SN_NOCHECK`，不是兩個分開傳。
- **pyi stub 標 `Optional` 但實際回空字串**：IDA 的 Python 綁定不一致。`get_name` 沒 name 時回 `""` 不是 `None`。
- **長跑 script 卡 UI**：用 `ida_kernwin.show_wait_box()`，或把 script 改成 Processor API hook。

## 動手練習

1. 寫一個 script：列出所有 string literal 的 `ea` 和內容，按長度排序。
2. 寫一個 script：找所有「被呼叫但自己不呼叫任何東西」的 leaf function。
3. 用 `ida_ua.decode_insn` 解一條 `mov [rbp-0x10], rax`，確認能拿出 `o_displ` operand 的 offset `-0x10`。
4. 用 `ida_bytes.patch_bytes` 把某個 `je short L1` 改成 `jmp short L1`（`74 XX` → `EB XX`），看 IDA View 立刻變化。

## 自我檢核

- [ ] 知道 `ida_bytes` / `ida_name` / `ida_funcs` / `ida_xref` 各自負責什麼
- [ ] 能用 `idautils.Functions()` / `XrefsTo` / `Heads` 做遍歷
- [ ] 知道 `ida_ua.decode_insn` 的輸出結構
- [ ] 知道 9.x 的 struct/type 主力是 `ida_typeinf`
- [ ] 會用 `ida_kernwin.show_wait_box` 包長跑 script

下一章寫第一個有生產力的 script — 批次改名與自動註解。

→ [Ch 9 批次改名與自動註解](./09-batch-rename-comment.md)
