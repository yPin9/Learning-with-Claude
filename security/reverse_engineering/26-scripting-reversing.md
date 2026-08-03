# Ch 26 腳本化逆向：讓工具替你工作

> **目標**：掌握 Ghidra Script、IDAPython、r2pipe、angr 四種腳本化手段，能把重複性分析自動化，並能用符號執行自動解 crackme 類題目。

> **環境**：
> - Ghidra 11.x（Java Script / Python 3 via Jython）——標「讀者自行重現」
> - IDAPython（IDA Free 8.x 或 IDA Pro）——標「讀者自行重現」
> - r2pipe：`pip install r2pipe`，需先裝 radare2
> - angr：`pip install angr`（Linux WSL 環境；首次安裝可能需 10–20 分鐘）
> - crackme 範例：`gcc -O0 -o crackme crackme.c`（見 §3）

---

## 為什麼需要？

手動逆向的天花板是「分析員的耐心」。一個 stripped binary 有 3000 個函式，要一個個看，光是識別字串處理函式就能花掉半天。腳本化的核心命題很簡單：**把你的分析直覺寫成程式，讓程式跑遍整個 binary**。

具體場景：

- 批次分析時：一批韌體映像，每個都要找 strcpy/sprintf 的危險呼叫。
- 模式識別時：所有以特定 magic bytes 開頭的函式都是解密器，要統一命名。
- 路徑探索時：crackme 的 hash 比較，手算要 1 小時，angr 5 秒解完。
- 差異分析時：patch 前後的二進位，自動找到被改動的函式（見 Ch 27）。

腳本 = 放大鏡：一個好腳本能讓你用 5 秒做到手動要 1 小時的分析。這不是偷懶，是把你的時間花在真正需要人類判斷的地方。

---

## 先建立直覺

腳本化逆向的心智模型是「查詢語言 + 副作用」：

```
binary IR (Ghidra DB / IDA DB / r2 state)
    │
    ▼
Query: 找所有滿足條件 X 的地址
    │
    ▼
Action: 對這些地址做 rename / comment / export / patch
```

四種工具的定位不同：

| 工具 | 強項 | 弱項 | 適用場景 |
|------|------|------|---------|
| Ghidra Script | 完整 Decompiler API，免費 | Java API 冗長 | 批次分析、命名、呼叫圖 |
| IDAPython | 最成熟的生態系 | 需要 IDA Pro | 商業逆向、插件豐富 |
| r2pipe | 輕量、可 CI/CD | disasm 精度偶有問題 | 快速 scan、自動化報告 |
| angr | 路徑探索、符號執行 | 速度慢、複雜程式難收斂 | crackme、漏洞路徑、taint |

---

## 1. Ghidra Script：批次命名與模式搜尋

> 讀者自行重現（需 Ghidra 11.x + Script Manager）

### 1.1 基本架構

Ghidra Script 可以用 Java 或 Python（Jython 3）寫。在 Script Manager 點「New Script」選 Python 即可。腳本入口是 `run()` 函式；`currentProgram` 是全域物件，代表目前開啟的 binary。

```python
# ghidra_rename_magic.py
# 功能：把所有呼叫含有特定 magic bytes 參數的函式命名為 decode_XXX
# 讀者自行重現

from ghidra.program.model.listing import FunctionIterator
from ghidra.program.model.symbol import SourceType

MAGIC = b'\xDE\xAD\xBE\xEF'
counter = [0]

def find_magic_in_refs(func):
    """檢查函式內是否有以 MAGIC 開頭的常數參數"""
    listing = currentProgram.getListing()
    body = func.getBody()
    for addr in body.getAddresses(True):
        cu = listing.getCodeUnitAt(addr)
        if cu is None:
            continue
        for i in range(cu.getNumOperands()):
            scalar = cu.getScalar(i)
            if scalar is None:
                continue
            val = scalar.getUnsignedValue()
            # 比對 DWORD magic
            magic_val = int.from_bytes(MAGIC, 'little')
            if val == magic_val:
                return True
    return False

def run():
    fm = currentProgram.getFunctionManager()
    funcs = fm.getFunctions(True)  # True = forward

    renamed = []
    for func in funcs:
        if find_magic_in_refs(func):
            old_name = func.getName()
            new_name = "decode_{:03d}".format(counter[0])
            counter[0] += 1
            func.setName(new_name, SourceType.USER_DEFINED)
            renamed.append((old_name, new_name, func.getEntryPoint()))

    for old, new, addr in renamed:
        print("[+] {} @ {} => {}".format(old, addr, new))
    print("[*] Total renamed: {}".format(len(renamed)))
```

### 1.2 抽取呼叫關係

```python
# ghidra_callgraph.py
# 讀者自行重現：列出所有函式的直接被呼叫者，輸出 CSV

import csv, os

def run():
    fm = currentProgram.getFunctionManager()
    refs_mgr = currentProgram.getReferenceManager()
    out_path = "/tmp/callgraph.csv"

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["caller", "callee", "call_site"])

        for func in fm.getFunctions(True):
            entry = func.getEntryPoint()
            # 取得所有從這個函式 body 發出的 CALL reference
            body = func.getBody()
            for addr in body.getAddresses(True):
                for ref in refs_mgr.getReferencesFrom(addr):
                    if ref.getReferenceType().isCall():
                        callee_func = fm.getFunctionAt(ref.getToAddress())
                        callee_name = callee_func.getName() if callee_func else "unk"
                        writer.writerow([
                            func.getName(),
                            callee_name,
                            str(ref.getFromAddress())
                        ])

    print("[*] Written to", out_path)
```

執行後得到一份 CSV，用 Python networkx 或 Gephi 就能畫出呼叫圖，一眼看出哪個函式是「中樞」（被最多地方呼叫）。

### 1.3 在 Headless 模式下批次跑

```bash
# 讀者自行重現
$GHIDRA_HOME/support/analyzeHeadless \
    /tmp/ghidra_proj MyProject \
    -import ./firmware.bin \
    -postScript ghidra_callgraph.py \
    -scriptPath /path/to/scripts \
    -deleteProject
```

Headless 模式讓你把 Ghidra 當命令列工具用，適合 CI/CD pipeline 或批次分析幾十個樣本。

---

## 2. IDAPython：最成熟的逆向腳本生態

> 讀者自行重現（需 IDA Free 8.x 或 IDA Pro）

IDAPython 的 API 更直覺，函式名都是動詞開頭，文件也比 Ghidra 豐富。

### 2.1 批次識別危險呼叫

```python
# ida_find_dangerous_calls.py
# 讀者自行重現：找所有對 strcpy / sprintf 的 xref，標上 comment

import idc, idaapi, idautils

DANGEROUS = ["strcpy", "sprintf", "gets", "strcat", "scanf"]

def find_xrefs_to(name):
    addr = idc.get_name_ea_simple(name)
    if addr == idc.BADADDR:
        return []
    return list(idautils.CodeRefsTo(addr, flow=False))

def run():
    results = []
    for func_name in DANGEROUS:
        for xref_addr in find_xrefs_to(func_name):
            caller = idaapi.get_func(xref_addr)
            caller_name = idc.get_func_name(xref_addr) if caller else "global"
            comment = "[WARN] calls {}".format(func_name)
            idc.set_cmt(xref_addr, comment, 0)
            results.append((hex(xref_addr), caller_name, func_name))
            print("[!] {:s} in {:s} -> {:s}".format(
                hex(xref_addr), caller_name, func_name))

    print("[*] Total dangerous calls found:", len(results))

run()
```

### 2.2 批次命名（依參數 pattern）

```python
# ida_rename_by_pattern.py
# 讀者自行重現：把所有第一個參數是特定常數的函式標記

import idc, idautils, idaapi

TARGET_CONST = 0xDEADBEEF
renamed_count = 0

for func_ea in idautils.Functions():
    name = idc.get_func_name(func_ea)
    # 跳過已命名的
    if not name.startswith("sub_"):
        continue

    # 掃函式體找 push TARGET_CONST 或 mov arg, TARGET_CONST
    end_ea = idc.find_func_end(func_ea)
    ea = func_ea
    while ea < end_ea:
        if idc.get_operand_value(ea, 0) == TARGET_CONST or \
           idc.get_operand_value(ea, 1) == TARGET_CONST:
            new_name = "handler_{:x}".format(func_ea)
            idc.set_name(func_ea, new_name, idc.SN_NOWARN)
            print("[+] Renamed {:s} -> {:s}".format(name, new_name))
            renamed_count += 1
            break
        ea = idc.next_head(ea)

print("[*] Total renamed:", renamed_count)
```

---

## 3. angr 符號執行：自動解 crackme

這節是本章核心。符號執行的概念在 symex_taint 課已有完整推導，這裡直接進應用。

### 3.1 目標程式

先準備 crackme.c：

```c
/* crackme.c */
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 2) { puts("usage: ./crackme <flag>"); return 1; }
    char *s = argv[1];
    if (strlen(s) != 8) { puts("wrong"); return 1; }
    if (s[0] != 'C' || s[1] != 'T' || s[2] != 'F') { puts("wrong"); return 1; }
    unsigned h = 0;
    for (int i = 0; i < 8; i++) h = (h * 31) ^ (unsigned char)s[i];
    if (h == 0x42d9f3a1u) { puts("correct!"); return 0; }
    puts("wrong");
    return 1;
}
```

編譯：

```bash
gcc -O0 -o crackme crackme.c
```

手算這個 hash 逆運算並不直觀——`h = (h*31)^c` 的每一步都把前一個位元組的影響擴散進去，要暴力搜也得 256^5（後五個位元組未固定）= 1.1 兆次。angr 把這件事變成 5 秒的問題。

### 3.2 angr 求解腳本

```python
#!/usr/bin/env python3
# solve_crackme.py
# 用 angr 符號執行自動求解 crackme
# 若已安裝 angr（pip install angr），直接執行即可
# 未安裝：見「預期輸出」區段

import angr
import claripy
import sys

BINARY = "./crackme"
FLAG_LEN = 8


def solve():
    # 建立 angr project
    proj = angr.Project(BINARY, auto_load_libs=False)

    # 建立 8 個符號 byte，代表我們輸入的字串
    flag_chars = [claripy.BVS("flag_{}".format(i), 8) for i in range(FLAG_LEN)]
    flag_sym = claripy.Concat(*flag_chars + [claripy.BVV(0, 8)])  # null-terminated

    # 設定初始狀態：模擬 argv[1] = 我們的符號字串
    state = proj.factory.entry_state(
        args=[BINARY, flag_sym],
        add_options={
            angr.options.ZERO_FILL_UNCONSTRAINED_MEMORY,
            angr.options.ZERO_FILL_UNCONSTRAINED_REGISTERS,
        }
    )

    # 加入已知條件：每個字元是可列印 ASCII（縮小搜尋空間）
    for c in flag_chars:
        state.solver.add(c >= 0x20)
        state.solver.add(c <= 0x7e)

    # 建立 Simulation Manager
    simgr = proj.factory.simulation_manager(state)

    # 找到「puts("correct!")」的地址
    # 找到「puts("wrong")」最後一個呼叫的地址（avoid）
    # 用字串搜尋找到對應地址
    correct_str_addr = None
    wrong_str_addr   = None
    for s_obj in proj.loader.main_object.memory.find(b"correct!"):
        correct_str_addr = s_obj
    for s_obj in proj.loader.main_object.memory.find(b"wrong"):
        wrong_str_addr = s_obj

    # 用 CFG 找 puts("correct!") 的呼叫點（find）
    # 簡化做法：直接搜 binary 中 "correct!" 引用的地址
    # 更穩健的方式：用 cfg.kb.functions 找包含該字串 xref 的基本塊
    cfg = proj.analyses.CFGFast(normalize=True)

    find_addrs  = set()
    avoid_addrs = set()

    for node in cfg.graph.nodes():
        block = proj.factory.block(node.addr, size=node.size)
        for const in block.vex.constants:
            if correct_str_addr is not None and const.value == correct_str_addr:
                find_addrs.add(node.addr)
            if wrong_str_addr is not None and const.value == wrong_str_addr:
                avoid_addrs.add(node.addr)

    # fallback：如果 CFG 沒找到，直接 explore 到 exit code 0
    if not find_addrs:
        print("[*] CFG xref fallback: exploring to deadended state")
        simgr.explore(find=lambda s: b"correct!" in s.posix.dumps(1),
                      avoid=lambda s: b"wrong" in s.posix.dumps(1))
    else:
        print("[*] find addrs:", [hex(a) for a in find_addrs])
        print("[*] avoid addrs:", [hex(a) for a in avoid_addrs])
        simgr.explore(find=list(find_addrs), avoid=list(avoid_addrs))

    if simgr.found:
        found_state = simgr.found[0]
        # 從符號狀態求解每個字元的具體值
        solution = bytes(
            found_state.solver.eval(c, cast_to=int)
            for c in flag_chars
        )
        print("[+] Flag found:", solution.decode("ascii", errors="replace"))
        return solution
    else:
        print("[-] No solution found")
        print("    deadended:", len(simgr.deadended))
        print("    unsat:", len(simgr.unsat))
        return None


if __name__ == "__main__":
    solve()
```

> **若未安裝 angr**（標未實測，完整腳本如上）：
>
> 預期輸出格式：
> ```
> [*] find addrs: ['0x401234']
> [*] avoid addrs: ['0x401210', '0x401220', '0x401250']
> [+] Flag found: CTFa1b2X
> ```
> 實際 flag 值依編譯結果而定；angr 求解的是讓 h==0x42d9f3a1u 且前三字元為 CTF 的字串。

### 3.3 理解 angr 在做什麼

angr 的符號執行把每個輸入位元組換成 SMT bitvector 變數，然後沿著程式的控制流傳播這些符號值。每個條件跳躍（`jne`、`jg` 等）都被分岔成兩個分支，各自帶著對應的路徑條件（path constraint）。

當你設定 `find=正確路徑地址`，angr 會在所有走到那個地址的狀態上呼叫 Z3 SMT solver，問「有沒有一組具體值能讓所有 path constraint 同時成立？」——這就是 `solver.eval(c)` 在做的事。

crackme 的 hash 迴圈 `h = (h*31)^c` 在 angr 裡是一條對 8 個 BVS 的算術約束，Z3 一次就解開，而你不需要手寫逆函式。

### 3.4 更快的 angr：hook 掉 libc

```python
# 對於有 strlen / strcmp 的程式，hook 掉可以加速
@proj.hook(proj.loader.find_symbol("strcmp").rebased_addr, length=0)
def strcmp_hook(state):
    s1_ptr = state.regs.rdi
    s2_ptr = state.regs.rsi
    # 讀兩個字串的符號內容並加約束
    s1 = state.memory.load(s1_ptr, 32)
    s2 = state.memory.load(s2_ptr, 32)
    state.solver.add(s1 == s2)
    state.regs.rax = 0  # 回傳 0 = equal
```

Hook 讓你把 libc 函式換成 angr 能直接理解的語意，避免符號執行鑽進 glibc 複雜實作而爆炸。

---

## 4. r2pipe：輕量自動化分析

r2pipe 讓你用 Python 控制 radare2，比直接寫 r2 腳本更好維護。

```bash
pip install r2pipe  # 需已裝 radare2
```

### 4.1 列出所有函式與基本資訊

```python
#!/usr/bin/env python3
# r2_list_functions.py
# 真跑：需已安裝 r2pipe + radare2

import r2pipe
import json

BINARY = "./crackme"

def main():
    r2 = r2pipe.open(BINARY, flags=["-2"])  # -2 = silence stderr
    r2.cmd("aaa")  # 完整分析（aa = basic, aaa = 加 string/calls）

    # 列出所有函式
    funcs = json.loads(r2.cmd("aflj"))  # aflj = list functions as JSON
    print("[*] Total functions:", len(funcs))
    print("{:<20} {:<10} {:<8} {}".format("name", "addr", "size", "calls"))
    print("-" * 60)
    for f in sorted(funcs, key=lambda x: x.get("cc", 0), reverse=True)[:20]:
        print("{:<20} {:<10} {:<8} {}".format(
            f["name"][:20],
            hex(f["offset"]),
            f.get("size", 0),
            f.get("cc", 0)   # cyclomatic complexity
        ))

    r2.quit()

if __name__ == "__main__":
    main()
```

### 4.2 批次 disassemble 並搜尋 pattern

```python
#!/usr/bin/env python3
# r2_find_pattern.py
# 真跑：找所有 call 指令的目標，標記危險函式呼叫

import r2pipe
import json

BINARY     = "./crackme"
DANGER_SET = {"sym.imp.strcpy", "sym.imp.sprintf",
              "sym.imp.gets",   "sym.imp.scanf"}

def main():
    r2 = r2pipe.open(BINARY, flags=["-2"])
    r2.cmd("aaa")

    funcs   = json.loads(r2.cmd("aflj"))
    hits    = []

    for func in funcs:
        addr = func["offset"]
        size = func.get("size", 0)
        if size == 0:
            continue

        # 求函式內所有指令
        r2.cmd("s {}".format(hex(addr)))
        insns = json.loads(r2.cmd("pdfj"))  # disasm function as JSON
        if not insns or "ops" not in insns:
            continue

        for op in insns["ops"]:
            if op.get("type") != "call":
                continue
            target = op.get("jump")
            # 查呼叫目標的符號名
            sym = r2.cmd("fd {}".format(hex(target))).strip() if target else ""
            if any(d in sym for d in DANGER_SET):
                hits.append({
                    "caller": func["name"],
                    "site":   hex(op["offset"]),
                    "callee": sym,
                })

    print("[*] Dangerous calls found: {}".format(len(hits)))
    for h in hits:
        print("  [!] {:s} @ {:s} -> {:s}".format(
            h["caller"], h["site"], h["callee"]))

    r2.quit()

if __name__ == "__main__":
    main()
```

### 4.3 匯出 CFG 為 dot 圖

```python
#!/usr/bin/env python3
# r2_export_cfg.py
# 真跑：把 main 的 CFG 輸出成 graphviz dot，再用 dot 轉 PNG

import r2pipe

BINARY = "./crackme"

def main():
    r2 = r2pipe.open(BINARY, flags=["-2"])
    r2.cmd("aaa")
    r2.cmd("s main")

    # agfd = generate DOT for current function CFG
    dot = r2.cmd("agfd")
    with open("/tmp/crackme_cfg.dot", "w") as f:
        f.write(dot)

    print("[*] Written to /tmp/crackme_cfg.dot")
    print("[*] Render: dot -Tpng /tmp/crackme_cfg.dot -o /tmp/cfg.png")
    r2.quit()

if __name__ == "__main__":
    main()
```

---

## 對比與取捨

| 維度 | Ghidra Script | IDAPython | r2pipe | angr |
|------|--------------|-----------|--------|------|
| 成本 | 免費 | IDA Pro 昂貴 | 免費 | 免費 |
| Decompiler 整合 | 完整 | 完整（Hex-Rays） | 無（只 disasm） | 無（IR 層） |
| 路徑探索 | 無 | 無（需外掛） | 無 | 核心功能 |
| 速度（大 binary） | 慢（JVM 啟動） | 快 | 最快 | 最慢 |
| 生態插件 | 成長中 | 最豐富 | 中等 | 學術界 |
| CI/CD 適用 | Headless 可用 | IDA CLI 可用 | 最適合 | 可但慢 |
| 符號執行 | 無 | 無 | 無 | 強 |
| 學習曲線 | 中（Java API） | 低（Python） | 低 | 高（SMT 概念） |

---

## 踩雷集錦

**1. angr 路徑爆炸**

angr 對含有迴圈的程式很容易爆（路徑數指數成長）。症狀是 `simgr.explore()` 跑幾分鐘還沒結果，`len(simgr.active)` 越來越大。

修法：
- 設 `veritesting=True`（合併同構路徑）
- 加 `step_func` 限制 active states 數量
- Hook 掉複雜的 libc 函式（見 §3.4）
- 用 `simgr.explore(num_find=1)` 找到一個就停

**2. r2pipe `pdfj` 回傳 null**

`r2.cmd("pdfj")` 在函式太大或 r2 分析失敗時回傳 `"null"` 而非合法 JSON，`json.loads("null")` 得到 Python `None`，下一步 `insns["ops"]` 就 TypeError。

修法：永遠加護欄：
```python
raw = r2.cmd("pdfj")
insns = json.loads(raw)
if not insns or not isinstance(insns, dict):
    continue
```

**3. Ghidra Headless 的 Script Path 問題**

`analyzeHeadless` 的 `-scriptPath` 只接受絕對路徑，而且腳本必須在那個目錄下直接存在（不能是子目錄）。忘記這件事會得到一個沈默失敗：Ghidra 分析完成但腳本完全沒跑。

修法：加 `-scriptlog /tmp/script.log` 並檢查 log 確認腳本有被載入。

**4. angr `claripy.Concat` 方向**

`claripy.Concat(*flag_chars)` 是 MSB-first（最高位在左）。如果你要讓符號字串對應到記憶體佈局（x86 小端），方向要留意。對 argv 這類以 null 結尾的字串，concat 後要補 `BVV(0, 8)` 否則 strlen 的符號執行會無限延伸。

**5. IDAPython API 版本破壞性變更**

IDA 7.x 和 8.x 的 IDAPython API 有不相容之處（例如 `idc.GetMnem` 在 8.x 改成 `idc.print_insn_mnem`）。網路上大量範例是舊 API，直接複製貼上會 `AttributeError`。

修法：查 IDA SDK 文件而不是 Stack Overflow；或用 `getattr(idc, 'GetMnem', idc.print_insn_mnem)(ea)` 做兼容。

---

## 進階：再往深一層

### 自動化差異分析（接 Ch 27）

把 r2pipe 的函式清單 + 每個函式的 hash（用 `rahash2 -a md5` 或 r2 的 `ph md5 $FS @addr`）存成 JSON，對兩個版本做 diff，就能快速定位被 patch 的函式——這是 patch-diff 自動化的基礎（Ch 27 展開）。

### angr + taint 分析

angr 有 `taint` 插件可以標記 source（如 `read` 的回傳值）並追蹤到 sink（如 `strcpy` 的第一個參數）。結合路徑探索，可以自動問「有沒有一條路徑讓使用者輸入不經過長度檢查就到達 strcpy？」——這是靜態 taint 做不到的（symex_taint 課 Ch 7 有完整案例）。

### LibAFL 的 Havoc 腳本化

如果目標是 fuzzing 而非求解，LibAFL（接 advanced_fuzzing 課）也有 Python binding，可以把 angr 找到的路徑條件轉成種子語料集，讓 fuzzer 在更有效的角落開始——這是 hybrid fuzzing 的核心思路。

### Ghidra 的 Sleigh 語言

Ghidra 的 IR 叫 P-code，底層的指令集描述語言叫 Sleigh。如果你在分析新的指令集（嵌入式 MCU、DSP），可以寫 Sleigh 外掛讓 Ghidra 理解新指令集，然後所有腳本自動適用——這是 Ghidra 最被低估的能力。

---

## 本章重點整理

- 腳本化逆向的核心是「把分析直覺轉成程式，讓程式跑遍 binary」，一個好腳本能把 1 小時的手工分析壓縮到 5 秒。
- Ghidra Script（Java/Python）能存取完整 Decompiler IR，適合批次命名和呼叫圖分析；Headless 模式讓它能進入 CI/CD。
- IDAPython 生態最成熟，API 直覺，但依賴 IDA 授權；跨版本 API 破壞是主要陷阱。
- r2pipe 最輕量，適合快速掃描和自動化報告，缺點是沒有 Decompiler 整合。
- angr 用符號執行把輸入建模為 SMT bitvector，自動求解讓程式走到特定路徑的輸入值；path explosion 是最大限制，hook libc 和設 veritesting 是主要緩解手段。
- 四種工具常組合使用：r2pipe 快掃定位目標 → Ghidra/IDA 精讀 → angr 解困難約束。

---

## 自我檢核

1. 用 Ghidra Script 列出所有被呼叫超過 10 次的函式，解釋為什麼「高被呼叫次數」是識別工具函式的啟發式方法。
2. 修改 `solve_crackme.py`，把 find/avoid 改成 lambda 形式（讀 stdout 判斷），解釋兩種方式各自的適用場景和速度差異。
3. 用 r2pipe 寫一個腳本，對任意 ELF 輸出「函式名稱、進入點地址、大小、cyclomatic complexity」的表格，並找出 complexity 最高的函式。
4. angr 的「路徑爆炸」在什麼條件下最容易發生？舉一個 crackme 的結構設計，讓 angr 的 naive explore 一定爆炸，再說明如何改寫腳本讓它收斂。
5. 解釋為什麼 `claripy.Concat` 要加 `BVV(0, 8)` 作為 null terminator，以及不加會導致什麼症狀。

---

## 延伸閱讀

1. **angr 官方文件** — [angr.io/api-doc](https://angr.io/api-doc)：Simulation Manager、Exploration Techniques（DFS/BFS/Veritesting）、Hook API 完整說明。
2. **Ghidra API Javadoc** — Ghidra 安裝目錄下 `docs/GhidraAPI_javadoc.zip`：`FunctionManager`、`ReferenceManager`、`DecompInterface` 是最常用的三個入口。
3. **「The angr Book」** — [docs.angr.io](https://docs.angr.io)：從 CTF crackme 到真實 CVE 路徑探索的完整教程，Ch 4（Symbolic Memory）和 Ch 6（Veritesting）是解決路徑爆炸的關鍵章節。
4. **r2pipe Python examples** — radare2 GitHub wiki [r2pipe](https://github.com/radareorg/radare2-r2pipe)：包含批次分析、CFG 匯出、與 Frida 整合的真實範例。
5. **「Practical Binary Analysis」Ch 8–9**（Dennis Andriesse）：用 Pin 和自訂工具做動態分析腳本化，與本章靜態腳本互補。

---

腳本化是逆向工程從「技藝」走向「工程」的關鍵一步：你不再是用工具的人，而是在造工具的人。下一章把這個思路用在兩個版本的二進位之間——自動找出被修改的函式，從補丁還原漏洞的根因。

→ [Ch 27 Patch-Diff 從補丁還原漏洞](./27-patch-diffing.md)
