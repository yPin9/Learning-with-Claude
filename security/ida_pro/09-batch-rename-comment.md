# Ch 9 — 批次改名與自動註解

> 目標：依 string ref / import / pattern 自動找 function 並 rename + 加 repeatable comment。第一個真正有生產力的 script。

## 為什麼這招這麼重要

stripped binary 打開後 800 個 `sub_XXXXXX`。手動改名不可能。但其中很多 function 有**明確特徵**：

- 引用 `"cannot allocate memory"` → 大概是 allocator wrapper
- 呼叫 `malloc` + `memcpy` + `free` → 大概是某個 clone helper
- 第一個指令是 `endbr64; push rbp; mov rbp, rsp; sub rsp, 0x50; ... call __stack_chk_fail` → 有 stack protector
- 裡面有 `"GET /..."` + `"Host: %s"` → HTTP client

這些特徵用 script 抓比手工找一百倍快。這一章三個實戰：

1. 依 string ref 批次改名
2. 依 import 呼叫 pattern 批次改名
3. 加 repeatable comment 標記 hot function

## 實戰 1：依 string 改名

邏輯：對每個 function，看它 reference 了哪些字串。字串短、有意義 → 當成名字 seed。

```python
import idautils
import ida_funcs, ida_name, ida_bytes
import re

MAX_NAME_LEN = 40

def sanitize_for_name(s):
    """把字串清成 valid C identifier 片段"""
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_NAME_LEN]

def strings_in_func(func_ea):
    out = []
    for ea in idautils.FuncItems(func_ea):
        for ref in idautils.DataRefsFrom(ea):
            s = ida_bytes.get_strlit_contents(ref, -1, 0)
            if s and 4 <= len(s) <= 60:
                out.append(s.decode("utf-8", errors="replace"))
    return out

def pick_seed(strings):
    """從多個候選挑最像 function name 的一個"""
    # 偏好：含英文字母且字母比例高的
    scored = []
    for s in strings:
        letters = sum(c.isalpha() for c in s)
        if letters < 3:
            continue
        ratio = letters / max(len(s), 1)
        scored.append((ratio, len(s), s))
    if not scored:
        return None
    scored.sort(reverse=True)                       # 高 ratio 先
    return scored[0][2]

def rename_by_strings():
    renamed = 0
    for func_ea in idautils.Functions():
        cur = ida_name.get_name(func_ea)
        if not cur.startswith("sub_"):
            continue                                # 已命名的跳過
        if ida_funcs.get_func(func_ea).flags & ida_funcs.FUNC_LIB:
            continue                                # library function 跳過

        seed = pick_seed(strings_in_func(func_ea))
        if not seed:
            continue

        new_name = f"fn_{sanitize_for_name(seed)}"
        if not new_name or new_name == "fn_":
            continue

        if ida_name.set_name(func_ea, new_name, ida_name.SN_NOWARN | ida_name.SN_NOCHECK):
            renamed += 1

    print(f"renamed {renamed} functions")

rename_by_strings()
```

跑完後 800 個 `sub_` 可能變成：

```
sub_401200      →  fn_cannot_allocate_memory
sub_401340      →  fn_license_check_failed
sub_4015A0      →  fn_opening_file_s
...
```

名字醜沒關係 — 比 `sub_401200` 好一百倍，你知道該看哪個。看過之後再手動改成正式名稱。

**關鍵選擇**：

- **prefix `fn_`**：讓你一眼看出哪些是 script 自動改的（方便事後 review / 批次改回）。
- **SN_NOWARN | SN_NOCHECK**：避免跳 dialog，避免因重名 fail。
- **FUNC_LIB 跳過**：FLIRT 命中的 `printf` 不要被你改成 `fn_s_c`。

## 實戰 2：依 import 組合改名

看 function 呼叫了哪些 import，組合就是行為簽名。

```python
import idautils, ida_funcs, ida_name, ida_bytes, ida_xref

def imports_called(func_ea):
    """收集這個 function 直接呼叫的 import name set"""
    imps = set()
    for ea in idautils.FuncItems(func_ea):
        for xref in idautils.XrefsFrom(ea, 0):
            if xref.type in (ida_xref.fl_CN, ida_xref.fl_CF):
                name = ida_name.get_name(xref.to)
                if name:
                    # 很多 import 會顯示成 "_strcpy" 或 ".strcpy"，拿掉 prefix
                    clean = name.lstrip("_.")
                    imps.add(clean)
    return imps

RULES = [
    # (name_if_all_present, {imports needed})
    ("alloc_and_copy",      {"malloc", "memcpy"}),
    ("realloc_grow",        {"realloc", "memcpy"}),
    ("open_read_close",     {"open", "read", "close"}),
    ("http_get_request",    {"socket", "connect", "send", "recv"}),
    ("rsa_decrypt",         {"RSA_new", "RSA_private_decrypt"}),
    ("aes_ecb_block",       {"AES_set_decrypt_key", "AES_decrypt"}),
    ("sha256_digest",       {"SHA256_Init", "SHA256_Update", "SHA256_Final"}),
]

def rename_by_imports():
    renamed = 0
    for func_ea in idautils.Functions():
        if not ida_name.get_name(func_ea).startswith("sub_"):
            continue

        imps = imports_called(func_ea)
        for target_name, needed in RULES:
            if needed.issubset(imps):
                new = f"fn_{target_name}"
                if ida_name.set_name(func_ea, new, ida_name.SN_NOWARN | ida_name.SN_NOCHECK):
                    renamed += 1
                break

    print(f"renamed {renamed} functions")

rename_by_imports()
```

這招對 malware 特別有效，因為 malware 的行為 API 組合很標準化（持久化、注入、加密 ransomware payload 都是固定 API pattern）。

## 實戰 3：Repeatable comment 標記 sink

VulnResearch 場景：把危險 sink 加 repeatable comment，所有呼叫到它的地方都會看到警示。

```python
import idautils, ida_name, ida_bytes, ida_funcs, ida_nalt
import idaapi

SINKS = {
    "strcpy":   "DANGER: buffer overflow if src > dst size",
    "strcat":   "DANGER: buffer overflow",
    "sprintf":  "DANGER: no size check",
    "gets":     "DANGER: always unsafe",
    "memcpy":   "CHECK: is size from untrusted input?",
    "system":   "DANGER: command injection",
    "popen":    "DANGER: command injection",
    "exec":     "DANGER: arg injection",
}

def comment_sinks():
    for name, msg in SINKS.items():
        # 可能是 "strcpy" 或 "_strcpy"，兩個都試
        for candidate in (name, f"_{name}", f".{name}"):
            ea = ida_name.get_name_ea(idaapi.BADADDR, candidate)
            if ea != idaapi.BADADDR:
                # 加 repeatable comment（所有 xref 也會看到）
                ida_bytes.set_cmt(ea, msg, True)
                print(f"commented {candidate} @ {ea:#x}: {msg}")
                break

comment_sinks()
```

跑完之後在 pseudocode 看到：

```c
strcpy(buf, argv[1]);         // DANGER: buffer overflow if src > dst size
```

這個 repeatable comment 不用每個 call site 貼一次，只要在 import entry 貼一次，所有 xref 自動顯示。

## 把三個 script 合起來

實際工作流是：

```python
# stripped_quick_triage.py

def triage():
    print("=== rename by imports ===")
    rename_by_imports()
    print("=== rename by strings ===")
    rename_by_strings()
    print("=== comment sinks ===")
    comment_sinks()

triage()
```

`Alt+F7` 跑一次，800 個 `sub_` 可能有 200 個直接變成有意義名字，剩下 600 個至少有 repeatable comment 做線索。

## 保險：先備份 IDB

跑任何會動 IDB 的 script 前，先 `Ctrl+S` 存檔。萬一規則錯了改錯一大堆：關閉 IDA → 把 `.i64.bak` 改回 `.i64` → 重開。

## Log / dry-run 模式

script 跑壞最痛。加個 dry-run 開關：

```python
DRY_RUN = True

def safe_set_name(ea, new):
    if DRY_RUN:
        print(f"[DRY] {ea:#x}  {ida_name.get_name(ea)} -> {new}")
        return True
    return ida_name.set_name(ea, new, ida_name.SN_NOWARN | ida_name.SN_NOCHECK)
```

DRY_RUN 跑一次，檢查 output 合理後再 `DRY_RUN = False` 真正 apply。你會感謝這個習慣。

## 常見踩雷

- **改完反而變 `sub_XXX_0` / `sub_XXX_1`**：`SN_NOWARN` 有效但 IDA 自動加後綴避重名。處理法：加更多 context 當 prefix（例如加 ea）讓名字更獨特。
- **改到 library function**：先用 `ida_funcs.FUNC_LIB` flag 濾掉。
- **非 UTF-8 字串解成亂碼**：`decode("utf-8", errors="replace")`，不要 `strict`。
- **script 做一半當機**：用 `try/except`，不要讓一個 function 的錯誤 kill 整個 pass。
- **script 執行後 undo 無效**：IDA 的 undo buffer 對 script 批次不是一行一個 entry，可能一次全 undo 掉或局部失效。依靠 `.i64.bak` 更可靠。

## 動手練習

1. 改 `rename_by_strings`：只改 function size `< 200 bytes` 的（小 function 通常角色單純，命名準確）。
2. 擴充 `RULES`：加 3 個你熟的 API 組合（例如 `CryptGenRandom` + `memcpy` 代表 `make_random_buf`）。
3. 加一個規則：function 若呼叫自己（recursive）加 `fn_recursive_` prefix。
4. 寫 output 到 `.csv`：`ea, old_name, new_name, reason` — 事後可以 diff / audit。

## 自我檢核

- [ ] 能寫出「依 string / 依 imports」兩種 rename script
- [ ] 知道 `FUNC_LIB` flag 要濾掉
- [ ] 會用 repeatable comment 一次標記 sink
- [ ] 有 dry-run 習慣
- [ ] 知道 `.i64.bak` 是救命機制

下一章把 script 的能力擴到 xref / call graph，做 reachability 分析。

→ [Ch 10 Xref 與 call graph 分析](./10-xref-callgraph.md)
