# Final Project — Malware Unpacker Helper

> 目標：寫一支實戰 script — 自動抓混淆的字串、還原 struct、在每個關鍵 function 加註解，最後掛到 hotkey 一鍵執行。把 Part 2–3 全整合。

## 為什麼挑這個題目

Unpacker helper 是一個本身有生產力、寫完會繼續用的工具。

情境：你拿到一包 malware，unpack 後的 payload 有典型特徵 — 混淆字串、動態 API resolve、自訂 struct。手動清一次要半天。這支 script 目標是把 **80% 的常規動作自動化**，留下你真正需要人腦判斷的 20%。

## 任務規格

一個 IDAPython plugin（`.py` 檔），放進 `plugins/` 目錄後每次 IDA 啟動自動載入。提供三個 action：

| Hotkey | 動作 | 作用 |
|---|---|---|
| `Ctrl-Shift-U` | `Unpack: full pass` | 跑整套 triage：解字串、標 hot function、標 sink、重命名 |
| `Ctrl-Shift-A` | `Unpack: auto-struct here` | 在當前 pseudocode LVAR 上跑 struct 推斷 |
| `Ctrl-Shift-R` | `Unpack: report` | 輸出當前分析報告 markdown |

## 功能拆解

### 1. 字串混淆還原

涵蓋兩類：

- **Stack string**：連續 mov immediate 到 `[rsp+X]`（Ch 12 code）
- **已知 decrypt function 的 call**：使用者先 rename 某 function 為 `decrypt_string_XX`，script 掃所有 xref 並試著解（支援簡單 XOR key 從 arg 拿的情況）

加 repeatable comment 到解密結果。

### 2. 動態 API resolve 識別

特徵：一堆 `mov rax, [r_hashtable + N]` 呼叫 — 那 `r_hashtable` 是 resolved API table。

偵測：掃 function 找連續 3 次以上 `call qword ptr [rax+0xXX]` 或 `call [rdi+0xXX]` — indirect call 到同一 base 的不同 offset。標記這個 function 為 `api_table_user_`。

### 3. 關鍵 API import 組合 tagging

繼承 Ch 9 的 RULES 做法，加更多 malware 常見組合：

```
+ inject: VirtualAllocEx + WriteProcessMemory + CreateRemoteThread
+ persist: RegSetValueExA + SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run
+ recon:   GetComputerName + GetUserName + GetSystemInfo
+ c2:      InternetOpenA + InternetConnectA + HttpSendRequestA
+ ransom:  CryptEncrypt + SetFileAttributesA + FindFirstFileA
```

命中的 function 重命名為 `fn_<tag>_...` 並加顯著 comment。

### 4. Struct 自動推斷（on demand）

Ctrl-Shift-A 觸發，對當前 LVAR 跑 Ch 11 邏輯，套到 LVAR（Ch 13 技術）。

### 5. 報告

Ctrl-Shift-R 輸出 `unpacker_report.md`：
- Binary 路徑、SHA256
- 解密字串列表（前 100 條）
- API 組合命中的 function
- 動態 API resolve 的 function
- 推斷出的 struct（從 Local Types 裡撈）

## 實作步驟建議

### Step 1：plugin 骨架

```python
# unpacker_helper.py (放 user plugins/)
import ida_idaapi, ida_kernwin

ACTIONS = []                                  # (name, handler_cls) — 方便 term 時 unregister

class UnpackerPlugin(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_FIX
    comment = "Unpacker triage helper"
    help = ""
    wanted_name = "UnpackerHelper"
    wanted_hotkey = ""

    def init(self):
        register_all_actions()
        return ida_idaapi.PLUGIN_KEEP

    def run(self, arg): pass

    def term(self):
        unregister_all_actions()

def PLUGIN_ENTRY():
    return UnpackerPlugin()
```

### Step 2：三個 action handler

每個用 Ch 14 的模式註冊。

### Step 3：把 Ch 9 / Ch 12 的邏輯拆成獨立 function

`do_batch_rename()`、`do_stackstr_recovery()`、`do_decrypt_xrefs()`、... 每個 return 一個 summary dict，最後匯總。

### Step 4：動態 API resolve 偵測

```python
def detect_api_table_users():
    """找頻繁用 [reg+offset] indirect call 的 function"""
    hits = []
    for func_ea in idautils.Functions():
        indirect_bases = collections.Counter()
        for ea in idautils.FuncItems(func_ea):
            insn = ida_ua.insn_t()
            if ida_ua.decode_insn(insn, ea) <= 0:
                continue
            if ida_ua.print_insn_mnem(ea).lower() != "call":
                continue
            op = insn.ops[0]
            if op.type == ida_ua.o_displ:
                # 粗略把 register 名字當 base key
                base = ida_ua.print_operand(ea, 0)
                indirect_bases[base] += 1
        for base, cnt in indirect_bases.items():
            if cnt >= 3:
                hits.append((func_ea, base, cnt))
    return hits

def tag_api_table_users(hits):
    for func_ea, base, cnt in hits:
        cur = ida_name.get_name(func_ea)
        if cur.startswith("sub_"):
            ida_name.set_name(func_ea, f"fn_api_resolver_{func_ea:X}",
                              ida_name.SN_NOWARN | ida_name.SN_NOCHECK)
        ida_bytes.set_cmt(func_ea, f"dynamic API table user: {cnt} indirect calls via {base}", True)
```

### Step 5：struct 推斷 on-demand

抓 Ch 13 的 `get_current_lvar` + Ch 11 的 `collect_accesses` + `render_struct`，套起來。`collect_accesses` 需要 base reg name — 從 `lv.location` 判斷，若不是 register 變數（在 stack 上）就跳 warning。

### Step 6：markdown 報告

沿用 Practice B 的 `write_report`，多加一個「dynamic API resolvers」table 和「decrypted strings」table。

### Step 7：SHA256 binary

```python
import hashlib, ida_nalt
def binary_sha256():
    path = ida_nalt.get_input_file_path()
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
```

## 期望跑起來的樣子

1. IDA 啟動，自動載入 plugin，Output window 看到：
   ```
   [UnpackerHelper] registered 3 actions
   ```
2. 打開一個 unpacked malware payload。
3. `Ctrl-Shift-U`：跑 60 秒，輸出：
   ```
   [UnpackerHelper] stack strings: 47
   [UnpackerHelper] decrypted calls: 23
   [UnpackerHelper] API combos matched: 8
   [UnpackerHelper] API table users: 3
   [UnpackerHelper] renames: 142
   ```
4. 在 pseudocode 看 `v3` 是 struct pointer，游標擺上去，`Ctrl-Shift-A` → struct 自動出現在 Local Types，pseudocode 立刻還原。
5. `Ctrl-Shift-R`：IDA 當前目錄多了 `unpacker_report.md`。

## 完整參考實作

**自己寫過再看**。參考只是其中一種寫法。

<details>
<summary>點開參考實作（完整檔案）</summary>

```python
# unpacker_helper.py
import hashlib, re, collections
import ida_idaapi, ida_kernwin, ida_hexrays
import ida_funcs, ida_name, ida_bytes, ida_xref, ida_ua
import ida_nalt, ida_typeinf
import idautils, idaapi

# =========== config ===========
DRY_RUN = False
MAX_NAME_LEN = 40

API_COMBOS = [
    ("inject",   {"VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread"}),
    ("persist",  {"RegSetValueExA", "RegCreateKeyExA"}),
    ("recon",    {"GetComputerNameA", "GetUserNameA"}),
    ("c2_http",  {"InternetOpenA", "InternetConnectA", "HttpSendRequestA"}),
    ("ransom",   {"CryptEncrypt", "FindFirstFileA"}),
    ("alloc",    {"malloc", "memcpy"}),
    ("hash",     {"SHA256_Init", "SHA256_Update", "SHA256_Final"}),
]

# =========== helpers ===========
def sanitize(s):
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_NAME_LEN]

def do_rename(ea, new):
    if DRY_RUN:
        print(f"[DRY] {ea:#x} -> {new}"); return True
    return ida_name.set_name(ea, new, ida_name.SN_NOWARN | ida_name.SN_NOCHECK)

def do_cmt(ea, text):
    if DRY_RUN:
        print(f"[DRY] cmt {ea:#x}: {text}"); return
    ida_bytes.set_cmt(ea, text, True)

# =========== pass: stack strings ===========
def recover_stackstrs_in_func(func_ea):
    by_off = {}
    for ea in idautils.FuncItems(func_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0: continue
        if ida_ua.print_insn_mnem(ea).lower() != "mov": continue
        dst, src = insn.ops[0], insn.ops[1]
        if dst.type != ida_ua.o_displ or src.type != ida_ua.o_imm: continue
        sz = {ida_ua.dt_byte:1, ida_ua.dt_word:2, ida_ua.dt_dword:4, ida_ua.dt_qword:8}.get(dst.dtype, 0)
        if sz == 0: continue
        val = src.value
        base = dst.addr
        if base > 0x100000: base -= 0x100000000
        for i in range(sz):
            b = (val >> (i*8)) & 0xFF
            if 0x20 <= b < 0x7F or b == 0:
                by_off.setdefault(base+i, []).append((b, ea))
    if not by_off: return []
    offs = sorted(by_off); groups=[]; cur=[]; prev=None
    for o in offs:
        if prev is None or o == prev+1: cur.append(o)
        else:
            if len(cur)>=4: groups.append(cur)
            cur=[o]
        prev=o
    if len(cur)>=4: groups.append(cur)
    out=[]
    for grp in groups:
        chars = bytes(by_off[o][-1][0] for o in grp)
        s = chars.rstrip(b"\x00").decode("latin1", errors="replace")
        anchor = by_off[grp[0]][0][1]
        do_cmt(anchor, f'stackstr: "{s}"')
        out.append((anchor, grp[0], s))
    return out

# =========== pass: API combos ===========
def imports_called(func_ea):
    imps = set()
    for ea in idautils.FuncItems(func_ea):
        for x in idautils.XrefsFrom(ea, 0):
            if x.type in (ida_xref.fl_CN, ida_xref.fl_CF):
                n = ida_name.get_name(x.to)
                if n: imps.add(n.lstrip("_."))
    return imps

def pass_api_combos():
    hits = []
    for func_ea in idautils.Functions():
        if not ida_name.get_name(func_ea).startswith("sub_"): continue
        imps = imports_called(func_ea)
        for tag, needed in API_COMBOS:
            if needed.issubset(imps):
                new = f"fn_{tag}_{func_ea:X}"
                if do_rename(func_ea, new):
                    do_cmt(func_ea, f"API combo hit: {tag} [{', '.join(sorted(needed))}]")
                    hits.append((func_ea, tag))
                break
    return hits

# =========== pass: dynamic API resolve ===========
def pass_api_table_users():
    hits = []
    for func_ea in idautils.Functions():
        counter = collections.Counter()
        for ea in idautils.FuncItems(func_ea):
            insn = ida_ua.insn_t()
            if ida_ua.decode_insn(insn, ea) <= 0: continue
            if ida_ua.print_insn_mnem(ea).lower() != "call": continue
            op = insn.ops[0]
            if op.type == ida_ua.o_displ:
                counter[ida_ua.print_operand(ea, 0)] += 1
        for base, cnt in counter.items():
            if cnt >= 3:
                cur = ida_name.get_name(func_ea)
                if cur.startswith("sub_"):
                    do_rename(func_ea, f"fn_api_resolver_{func_ea:X}")
                do_cmt(func_ea, f"dynamic API user: {cnt} indirect calls via {base}")
                hits.append((func_ea, base, cnt))
                break
    return hits

# =========== pass: string-based rename ===========
def strings_in_func(func_ea):
    out = []
    for ea in idautils.FuncItems(func_ea):
        for ref in idautils.DataRefsFrom(ea):
            s = ida_bytes.get_strlit_contents(ref, -1, 0)
            if s and 4 <= len(s) <= 60:
                out.append(s.decode("utf-8", errors="replace"))
    return out

def pass_strings_rename():
    renames = []
    for ea in idautils.Functions():
        f = ida_funcs.get_func(ea)
        if f.flags & ida_funcs.FUNC_LIB: continue
        if not ida_name.get_name(ea).startswith("sub_"): continue
        ss = strings_in_func(ea)
        if not ss: continue
        best = max(ss, key=lambda s: sum(c.isalpha() for c in s) / max(len(s),1))
        if sum(c.isalpha() for c in best) < 3: continue
        new = f"fn_{sanitize(best)}"
        if new == "fn_": continue
        if do_rename(ea, new):
            renames.append((ea, new, best))
    return renames

# =========== struct recovery (on demand) ===========
def collect_accesses(func_ea, base_reg_name):
    results = []
    for ea in idautils.FuncItems(func_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0: continue
        for op in insn.ops:
            if op.type == ida_ua.o_void: break
            if op.type != ida_ua.o_displ: continue
            op_txt = ida_ua.print_operand(ea, op.n).lower()
            if base_reg_name.lower() not in op_txt: continue
            off = op.addr
            if off > 0x100000: off -= 0x100000000
            sz = {ida_ua.dt_byte:1, ida_ua.dt_word:2, ida_ua.dt_dword:4, ida_ua.dt_qword:8}.get(op.dtype, 4)
            results.append((off, sz, ea))
    return results

def render_struct(name, fields):
    lines = [f"struct {name} {{"]
    prev_end = 0
    for off, sz in fields:
        if off > prev_end:
            lines.append(f"    uint8_t pad_{prev_end:X}[{off-prev_end}];")
        t = {1:"uint8_t",2:"uint16_t",4:"uint32_t",8:"uint64_t"}.get(sz, "uint8_t")
        lines.append(f"    {t} field_{off:X};")
        prev_end = off + sz
    lines.append("};")
    return "\n".join(lines)

def auto_struct_for_current_lvar():
    vu = ida_hexrays.get_widget_vdui(ida_kernwin.get_current_widget())
    if not vu:
        ida_kernwin.warning("not in pseudocode view")
        return
    vu.get_current_item(ida_hexrays.USE_KEYBOARD)
    item = vu.item
    if item.citype == ida_hexrays.VDI_EXPR and item.e.op == ida_hexrays.cot_var:
        lv = vu.cfunc.get_lvars()[item.e.v.idx]
    elif item.citype == ida_hexrays.VDI_LVAR:
        lv = item.get_lvar()
    else:
        ida_kernwin.warning("not on an LVAR"); return

    if not lv.location.is_reg():
        ida_kernwin.warning("LVAR is not in a register (stack vars not supported in this helper)")
        return

    # 簡化：用 lv.name 當 hint，實戰應該從 reg id 翻 reg table
    reg_hint = lv.name
    func_ea = vu.cfunc.entry_ea
    accesses = collect_accesses(func_ea, reg_hint)
    if not accesses:
        ida_kernwin.warning(f"no [{reg_hint}+X] accesses found"); return

    by_off = {}
    for off, sz, _ in accesses:
        if off < 0: continue
        by_off[off] = max(by_off.get(off, 0), sz)
    fields = sorted(by_off.items())

    struct_name = f"Auto_{func_ea:X}_{lv.name}"
    decl = render_struct(struct_name, fields)
    ida_typeinf.idc_parse_types(decl, 0)

    tif = ida_typeinf.tinfo_t()
    if not tif.get_named_type(None, struct_name):
        ida_kernwin.warning("parse ok but not found in types"); return
    ptr = ida_typeinf.tinfo_t()
    ptr.create_ptr(tif)

    lsi = ida_hexrays.lvar_saved_info_t()
    lsi.ll = lv
    lsi.type = ptr
    if ida_hexrays.modify_user_lvar_info(func_ea, ida_hexrays.MLI_TYPE, lsi):
        vu.refresh_view(True)
        ida_kernwin.msg(f"[UnpackerHelper] applied {struct_name}\n")
    else:
        ida_kernwin.warning("apply failed")

# =========== report ===========
LAST_SUMMARY = {}

def write_report():
    path = "unpacker_report.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Unpacker Report\n\n")
        f.write(f"- Binary: `{ida_nalt.get_input_file_path()}`\n")
        try:
            with open(ida_nalt.get_input_file_path(), "rb") as b:
                f.write(f"- SHA256: `{hashlib.sha256(b.read()).hexdigest()}`\n")
        except: pass
        f.write(f"- SummaryRenames: {LAST_SUMMARY.get('renames',0)}\n")
        f.write(f"- SummaryStackstrs: {LAST_SUMMARY.get('stackstrs',0)}\n")
        f.write(f"- SummaryAPICombos: {LAST_SUMMARY.get('api_combos',0)}\n")
        f.write(f"- SummaryAPIResolvers: {LAST_SUMMARY.get('api_resolvers',0)}\n\n")

        if "combo_hits" in LAST_SUMMARY:
            f.write("## API Combo Hits\n\n| EA | Tag | Name |\n|---|---|---|\n")
            for ea, tag in LAST_SUMMARY["combo_hits"]:
                f.write(f"| {ea:#x} | {tag} | `{ida_name.get_name(ea)}` |\n")
            f.write("\n")

        if "resolver_hits" in LAST_SUMMARY:
            f.write("## Dynamic API Resolvers\n\n| EA | Base | Count |\n|---|---|---|\n")
            for ea, base, cnt in LAST_SUMMARY["resolver_hits"]:
                f.write(f"| {ea:#x} | `{base}` | {cnt} |\n")
            f.write("\n")

    ida_kernwin.msg(f"[UnpackerHelper] wrote {path}\n")

# =========== full pass ===========
def run_full_pass():
    ida_kernwin.show_wait_box("Unpacker: full pass...")
    try:
        stack = 0
        for ea in idautils.Functions():
            stack += len(recover_stackstrs_in_func(ea))
            if ida_kernwin.user_cancelled(): break
        combos = pass_api_combos()
        resolvers = pass_api_table_users()
        renames = pass_strings_rename()

        LAST_SUMMARY.update({
            "stackstrs": stack,
            "api_combos": len(combos),
            "api_resolvers": len(resolvers),
            "renames": len(renames),
            "combo_hits": combos,
            "resolver_hits": resolvers,
        })
        ida_kernwin.msg(f"[UnpackerHelper] done. renames={len(renames)} stackstrs={stack} combos={len(combos)} resolvers={len(resolvers)}\n")
    finally:
        ida_kernwin.hide_wait_box()

# =========== actions ===========
class FullPassHandler(ida_kernwin.action_handler_t):
    def activate(self, ctx): run_full_pass(); return 1
    def update(self, ctx): return ida_kernwin.AST_ENABLE_ALWAYS

class StructHandler(ida_kernwin.action_handler_t):
    def activate(self, ctx): auto_struct_for_current_lvar(); return 1
    def update(self, ctx):
        if ctx.widget_type == ida_kernwin.BWN_PSEUDOCODE:
            return ida_kernwin.AST_ENABLE_FOR_WIDGET
        return ida_kernwin.AST_DISABLE_FOR_WIDGET

class ReportHandler(ida_kernwin.action_handler_t):
    def activate(self, ctx): write_report(); return 1
    def update(self, ctx): return ida_kernwin.AST_ENABLE_ALWAYS

ACTION_DEFS = [
    ("unpacker:full",   "Unpack: full pass",         FullPassHandler, "Ctrl-Shift-U"),
    ("unpacker:struct", "Unpack: auto-struct here",  StructHandler,   "Ctrl-Shift-A"),
    ("unpacker:report", "Unpack: report",            ReportHandler,   "Ctrl-Shift-R"),
]

def register_all_actions():
    for name, label, cls, hk in ACTION_DEFS:
        try: ida_kernwin.unregister_action(name)
        except: pass
        desc = ida_kernwin.action_desc_t(name, label, cls(), hk, label, -1)
        ida_kernwin.register_action(desc)
    ida_kernwin.msg(f"[UnpackerHelper] registered {len(ACTION_DEFS)} actions\n")

def unregister_all_actions():
    for name, *_ in ACTION_DEFS:
        try: ida_kernwin.unregister_action(name)
        except: pass

# =========== plugin entry ===========
class UnpackerPlugin(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_FIX
    comment = "Unpacker triage helper"
    help = ""
    wanted_name = "UnpackerHelper"
    wanted_hotkey = ""
    def init(self): register_all_actions(); return ida_idaapi.PLUGIN_KEEP
    def run(self, arg): pass
    def term(self): unregister_all_actions()

def PLUGIN_ENTRY():
    return UnpackerPlugin()
```

放 `%APPDATA%\Hex-Rays\IDA Pro\plugins\unpacker_helper.py`，重啟 IDA 即生效。

</details>

## 自我檢核

- [ ] 有 plugin_t 骨架、`PLUGIN_ENTRY` / `init` / `term` 齊全
- [ ] 三個 action 都能註冊、有 hotkey、update() 正確
- [ ] 整合至少 4 個 Part 2 的技巧（stack string、API combos、rename、xref）
- [ ] 用 Part 3 的 Hex-Rays API 做 LVAR 套 struct
- [ ] 能輸出 markdown 報告
- [ ] 放 plugins 目錄後 IDA 啟動自動載入

## 驗收

丟進實際 binary 測：

1. **自己寫的 malware 模擬器**：一個 C 程式故意做 stack string + 呼叫 `VirtualAllocEx` 組合。
2. **某個 CTF reverse 題的 binary**。
3. **MalwareBazaar 下載的 unpacked payload**（沙箱）。

每個 binary 觀察：

- Full pass 後 navigator band 的 `Regular function`（淺藍）比例是否明顯增加（因為很多 `sub_` 變成 `fn_...`）
- Pseudocode 打開主 function，是不是大部分 LVAR 有 comment 或推斷後的 type
- 報告 md 打開資訊是否清楚

## 收尾 — 你學完了什麼

```
Part 1  →  不用滑鼠跑完整條逆向動線
Part 2  →  對 stripped binary 的常見痛點寫自動化
Part 3  →  進 pseudocode 層級，把 script 包成常駐工具
```

下一步建議：

- 讀 Hex-Rays blog 的 plugin 範例，看他們怎麼做 microcode pass
- 翻 GitHub `IDAPython` / `idapyswitch` 搜尋別人寫的工具（Lighthouse、HexRaysCodeXplorer、Ghihorn）
- 自己 maintain 一個「個人 IDA tools 倉」，之後每個專案都能立刻上手

最後，記得：**工具只是放大你的分析能力，不能取代判斷**。script 失手時你要看得出不對勁，那個判斷來自 Part 1 打下的肌肉記憶。

完課。
