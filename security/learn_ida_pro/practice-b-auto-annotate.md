# 練習 B — Stripped binary 自動 annotate

> 目標：給一個 stripped binary，寫 script 自動掃所有「用到 strings 的 function」、依字串內容猜功能、改 sub_xxx 為有意義的名字。Ch 7–12 全用上。

## 任務規格

輸入一個中等大小（300–1000 functions）的 stripped binary，你寫的 script 要在一次執行內完成：

1. **依 string reference 給 function 取一個 `fn_xxx` 名字**（Ch 9 技巧）
2. **找出所有 top-10 呼叫頻率最高的 function**，加 repeatable comment：`// hot: called by N sites`
3. **標出所有引用 dangerous sink 的 function**（strcpy/system/gets）：repeatable comment
4. **偵測所有 stack string 並加註解**（Ch 12 技巧）
5. **輸出一份 markdown 報告**到當前目錄：
   - `ida_triage_report.md`
   - 內容：top 20 重命名、top 10 hot function、所有 sink xref 路徑、找到的 stack string

跑完 IDB 看起來應該從「一堆 sub_」變成「滿是 fn_ + comment 的可讀樣子」。

## 實作步驟建議

### Step 1：骨架 + dry-run 開關

把所有主要動作抽成 function，最上面放 `DRY_RUN`。每個動作都先 dry-run 驗證。

```python
DRY_RUN = True

def main():
    renames = []
    hot_funcs = []
    sink_paths = []
    stack_strings = []

    renames += rename_by_strings()
    hot_funcs = find_hot_functions(top_n=10)
    mark_hot_functions(hot_funcs)
    sink_paths = mark_and_trace_sinks()
    stack_strings = recover_all_stack_strings()

    write_report(renames, hot_funcs, sink_paths, stack_strings)

main()
```

### Step 2：實作 `rename_by_strings`

Ch 9 已經給 baseline code。改一下讓它 `return` 一個 `[(ea, old, new, reason), ...]` list，dry-run 模式時不 apply，但仍 return。

### Step 3：找 hot function

對每個 function 數 `len(list(XrefsTo(ea, 0)))`，取 top N：

```python
def find_hot_functions(top_n=10):
    counts = []
    for ea in idautils.Functions():
        n = sum(1 for x in idautils.XrefsTo(ea, 0)
                if x.type in (ida_xref.fl_CN, ida_xref.fl_CF))
        counts.append((n, ea))
    counts.sort(reverse=True)
    return counts[:top_n]

def mark_hot_functions(hot_list):
    for n, ea in hot_list:
        msg = f"hot: called by {n} sites"
        if DRY_RUN:
            print(f"[DRY] comment {ea:#x}: {msg}")
        else:
            ida_bytes.set_cmt(ea, msg, True)
```

### Step 4：標 sink + 反推 path

用 Ch 10 的 reverse BFS，每個 sink 收最短的 3 條路徑。

### Step 5：掃 stack string

用 Ch 12 的 `recover_stack_strings_in_func`，收集所有結果到 list。

### Step 6：寫 markdown 報告

```python
def write_report(renames, hot_funcs, sink_paths, stack_strings):
    with open("ida_triage_report.md", "w", encoding="utf-8") as f:
        f.write("# IDA Triage Report\n\n")
        f.write(f"Binary: `{ida_nalt.get_input_file_path()}`\n\n")

        f.write("## Top 20 Renames\n\n")
        f.write("| Address | Old | New | Reason |\n|---|---|---|---|\n")
        for ea, old, new, reason in renames[:20]:
            f.write(f"| {ea:#x} | `{old}` | `{new}` | {reason} |\n")

        f.write("\n## Top 10 Hot Functions\n\n")
        f.write("| Rank | Address | Name | Call sites |\n|---|---|---|---|\n")
        for i, (n, ea) in enumerate(hot_funcs, 1):
            f.write(f"| {i} | {ea:#x} | `{ida_name.get_name(ea)}` | {n} |\n")

        f.write("\n## Sink Paths\n\n")
        for sink, paths in sink_paths.items():
            f.write(f"### `{sink}`\n\n")
            for p in paths:
                chain = " → ".join(ida_name.get_name(e) for e in p)
                f.write(f"- {chain}\n")
            f.write("\n")

        f.write("## Stack Strings\n\n")
        for ea, offset, s in stack_strings[:50]:
            f.write(f"- `{ea:#x}` @ rsp+{offset:#x}: `{s!r}`\n")
```

### Step 7：測試

1. DRY_RUN = True 跑一次，看 output 和報告合理。
2. 存 IDB backup（`Ctrl+S` 再複製 `.i64` 為 `.i64.safe`）。
3. DRY_RUN = False 真跑。
4. 打開 IDA 看 Navigator band、Names window、Functions window 變化。

## 完整參考解答

**寫完再看！** 自己先做過一遍，參考版只是其中一種寫法。

<details>
<summary>點開參考實作</summary>

```python
# auto_triage.py
import idautils
import ida_funcs, ida_name, ida_bytes, ida_xref, ida_ua, ida_nalt
import idaapi
import re
import collections

DRY_RUN = False
MAX_NAME_LEN = 40

# ----------------- helpers -----------------

def sanitize(s):
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_NAME_LEN]

def is_library(f):
    return bool(f.flags & ida_funcs.FUNC_LIB)

def strings_in_func(func_ea):
    out = []
    for ea in idautils.FuncItems(func_ea):
        for ref in idautils.DataRefsFrom(ea):
            s = ida_bytes.get_strlit_contents(ref, -1, 0)
            if s and 4 <= len(s) <= 60:
                out.append(s.decode("utf-8", errors="replace"))
    return out

def pick_seed(ss):
    best = None
    best_score = 0
    for s in ss:
        letters = sum(c.isalpha() for c in s)
        if letters < 3:
            continue
        score = letters / max(len(s), 1)
        if score > best_score:
            best, best_score = s, score
    return best

def do_rename(ea, new_name):
    if DRY_RUN:
        print(f"[DRY] rename {ea:#x}: {ida_name.get_name(ea)} -> {new_name}")
        return True
    return ida_name.set_name(ea, new_name, ida_name.SN_NOWARN | ida_name.SN_NOCHECK)

def do_comment(ea, text):
    if DRY_RUN:
        print(f"[DRY] cmt {ea:#x}: {text}")
        return
    ida_bytes.set_cmt(ea, text, True)

# ----------------- rename -----------------

def rename_by_strings():
    results = []
    for ea in idautils.Functions():
        f = ida_funcs.get_func(ea)
        if is_library(f):
            continue
        old = ida_name.get_name(ea)
        if not old.startswith("sub_"):
            continue
        ss = strings_in_func(ea)
        seed = pick_seed(ss)
        if not seed:
            continue
        new = f"fn_{sanitize(seed)}"
        if new == "fn_":
            continue
        if do_rename(ea, new):
            results.append((ea, old, new, f"string: {seed!r}"))
    return results

# ----------------- hot -----------------

def find_hot_functions(top_n=10):
    out = []
    for ea in idautils.Functions():
        n = sum(1 for x in idautils.XrefsTo(ea, 0)
                if x.type in (ida_xref.fl_CN, ida_xref.fl_CF))
        out.append((n, ea))
    out.sort(reverse=True)
    return out[:top_n]

def mark_hot_functions(hot_list):
    for n, ea in hot_list:
        do_comment(ea, f"hot: called by {n} sites")

# ----------------- sinks -----------------

SINKS = ["strcpy", "strcat", "sprintf", "gets", "system", "popen"]

def reverse_reach_paths(sink_ea, max_depth=6, max_paths=3):
    parent = {sink_ea: None}
    frontier = [sink_ea]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        next_front = []
        for f in frontier:
            for x in idautils.XrefsTo(f, 0):
                if x.type not in (ida_xref.fl_CN, ida_xref.fl_CF):
                    continue
                caller_func = ida_funcs.get_func(x.frm)
                if not caller_func:
                    continue
                c = caller_func.start_ea
                if c not in parent:
                    parent[c] = f
                    next_front.append(c)
        frontier = next_front

    paths = []
    for c in parent:
        if c == sink_ea or parent[c] is None:
            continue
        p = []
        cur = c
        while cur is not None:
            p.append(cur)
            cur = parent[cur]
        paths.append(p)
    paths.sort(key=len)
    return paths[:max_paths]

def mark_and_trace_sinks():
    all_paths = {}
    for name in SINKS:
        for cand in (name, f"_{name}", f".{name}"):
            ea = ida_name.get_name_ea(idaapi.BADADDR, cand)
            if ea != idaapi.BADADDR:
                do_comment(ea, f"DANGER sink: {name}")
                paths = reverse_reach_paths(ea)
                all_paths[name] = paths
                break
    return all_paths

# ----------------- stack strings -----------------

def recover_stack_strings_in_func(func_ea):
    by_off = {}
    for ea in idautils.FuncItems(func_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0:
            continue
        if ida_ua.print_insn_mnem(ea).lower() != "mov":
            continue
        dst, src = insn.ops[0], insn.ops[1]
        if dst.type != ida_ua.o_displ or src.type != ida_ua.o_imm:
            continue
        size = {ida_ua.dt_byte:1, ida_ua.dt_word:2, ida_ua.dt_dword:4, ida_ua.dt_qword:8}.get(dst.dtype, 0)
        if size == 0:
            continue
        val = src.value
        base = dst.addr
        if base > 0x100000: base -= 0x100000000
        for i in range(size):
            b = (val >> (i*8)) & 0xFF
            if 0x20 <= b < 0x7F or b == 0:
                by_off.setdefault(base + i, []).append((b, ea))

    if not by_off: return []

    offs = sorted(by_off)
    groups, current, prev = [], [], None
    for o in offs:
        if prev is None or o == prev + 1:
            current.append(o)
        else:
            if len(current) >= 4: groups.append(current)
            current = [o]
        prev = o
    if len(current) >= 4: groups.append(current)

    out = []
    for grp in groups:
        chars = bytes(by_off[o][-1][0] for o in grp)
        s = chars.rstrip(b"\x00").decode("latin1", errors="replace")
        anchor = by_off[grp[0]][0][1]
        do_comment(anchor, f'stackstr: "{s}"')
        out.append((anchor, grp[0], s))
    return out

def recover_all_stack_strings():
    results = []
    for func_ea in idautils.Functions():
        results.extend(recover_stack_strings_in_func(func_ea))
    return results

# ----------------- report -----------------

def write_report(renames, hot_funcs, sink_paths, stack_strings):
    with open("ida_triage_report.md", "w", encoding="utf-8") as f:
        f.write("# IDA Triage Report\n\n")
        f.write(f"- Binary: `{ida_nalt.get_input_file_path()}`\n")
        f.write(f"- Renames: {len(renames)}\n")
        f.write(f"- Stack strings: {len(stack_strings)}\n\n")

        f.write("## Top 20 Renames\n\n| EA | Old | New | Reason |\n|---|---|---|---|\n")
        for ea, old, new, reason in renames[:20]:
            f.write(f"| {ea:#x} | `{old}` | `{new}` | {reason} |\n")

        f.write("\n## Top 10 Hot Functions\n\n| # | EA | Name | Callers |\n|---|---|---|---|\n")
        for i, (n, ea) in enumerate(hot_funcs, 1):
            f.write(f"| {i} | {ea:#x} | `{ida_name.get_name(ea)}` | {n} |\n")

        f.write("\n## Sink Paths\n\n")
        for sink, paths in sink_paths.items():
            f.write(f"### `{sink}`\n\n")
            if not paths:
                f.write("- (no reachable caller)\n\n")
                continue
            for p in paths:
                chain = " → ".join(ida_name.get_name(ea) for ea in p)
                f.write(f"- {chain}\n")
            f.write("\n")

        f.write("## Stack Strings (first 50)\n\n")
        for ea, offset, s in stack_strings[:50]:
            f.write(f"- `{ea:#x}` rsp+{offset:#x}: `{s!r}`\n")

# ----------------- main -----------------

def main():
    import ida_kernwin
    ida_kernwin.show_wait_box("Triage in progress...")
    try:
        renames = rename_by_strings()
        hot = find_hot_functions()
        mark_hot_functions(hot)
        sinks = mark_and_trace_sinks()
        strs = recover_all_stack_strings()
        write_report(renames, hot, sinks, strs)
    finally:
        ida_kernwin.hide_wait_box()
    print(f"done. renames={len(renames)} stackstrs={len(strs)}")

main()
```

跑法：把 script 存 `auto_triage.py`，打開一個 stripped binary 的 IDB，`Alt+F7` load script。

</details>

## 測試用例

用這些 binary 驗證：

1. **自己編的 C 程式**（C 源碼有 `strcpy`、`system`、一堆 stack string），`-O0 -s` 編譯 strip 掉 symbol。
2. **`/bin/ls`** 的 stripped 版（`strip ls`）。
3. **真實 malware 的 unpacked payload**（MalwareBazaar 取）— 僅限沙箱環境。

驗收標準：

- 報告有東西、table 齊全
- 至少 10% 的 `sub_` 被改名
- Sink paths 合理（system 的 caller 應該能追回 main）
- Stack string 偵測到至少一組（若 binary 有的話）

## 自我檢核

- [ ] script 能在 dry-run 和 real 兩種模式下跑
- [ ] 用了 Ch 7–12 各章的技巧（rename / xref / stack string）
- [ ] 寫出了 markdown 報告
- [ ] 能在 IDA UI 看到改名 + 註解
- [ ] 知道哪裡會 false positive / miss

Part 2 結束。你已經能寫有生產力的 script 了。Part 3 開始用 Decompiler API 進到 pseudocode 層級。

→ [Ch 13 Hex-Rays API 入門](./13-hexrays-api.md)
