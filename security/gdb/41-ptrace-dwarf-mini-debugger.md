# Ch 41 — 用 ptrace + DWARF 寫 mini debugger

> **目標**：把 Part 8 的原理全部組裝起來，從零寫一個能用的 mini debugger——啟動程式、下斷點（INT3）、continue、single-step、讀暫存器/記憶體、用 DWARF 把位址翻成行號。寫完你會徹底理解 GDB 的本體，也為 Final Project（gef 風格插件）打下「我知道底層在幹嘛」的底氣。

> **環境**：Linux x86_64，Python 3.8+，`pip install pyelftools`，gcc。這章是原理深挖章 + 實作章。

## 為什麼要自己寫一個

你已經學了 ptrace（Ch 2）、DWARF（Ch 38）、斷點/step 機制（Ch 39）、位址（Ch 40）。但「懂」和「能做」之間有條鴻溝。自己寫一個 mini debugger 跨過這條鴻溝——當你親手 patch INT3、親手 $pc 退 1、親手查 line table，GDB 的每個行為你都會「啊，原來如此」。

這也是這門課定位「會用 → 能改」的「能改」核心：理解本體，才談得上改造。雖然 Final Project 是寫 GDB 插件（改 GDB），但寫過 mini debugger 你才真懂插件底下那層在做什麼。

我們用 Python（ctypes 呼叫 ptrace + pyelftools 讀 DWARF）——比 C 短、好讀，專注在邏輯而非 C 的繁瑣。

## 架構：mini debugger 的五個能力

```
   minidbg 要做的事（對照 Ch 1 的三件核心工作）
   ┌────────────────────────────────────────────┐
   │ 1. 啟動 inferior      (fork + TRACEME + exec)│ ← Ch 2, 3
   │ 2. 下斷點             (PEEK/POKE INT3)        │ ← Ch 4, 39
   │ 3. 執行控制           (CONT / SINGLESTEP)     │ ← Ch 5, 39
   │ 4. 狀態檢視           (GETREGS / PEEK 記憶體) │ ← Ch 7, 11
   │ 5. 符號翻譯           (讀 DWARF line table)   │ ← Ch 6, 38
   └────────────────────────────────────────────┘
```

每一塊你都學過原理，現在組裝。

## Part 1：ptrace 的 ctypes 包裝

先把 ptrace syscall 包成 Python 可呼叫（Ch 2 的 C 版翻成 Python）：

```python
# minidbg.py
import ctypes, os, signal, struct, sys

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long,
                        ctypes.c_void_p, ctypes.c_void_p]

# ptrace request 常數（from sys/ptrace.h）
TRACEME, PEEKTEXT, POKETEXT = 0, 1, 4
CONT, SINGLESTEP = 7, 9
GETREGS, SETREGS = 12, 13

def ptrace(request, pid, addr, data):
    ctypes.set_errno(0)
    res = libc.ptrace(request, pid, ctypes.c_void_p(addr), ctypes.c_void_p(data))
    err = ctypes.get_errno()
    if res == -1 and err != 0:
        raise OSError(err, os.strerror(err))
    return res

# x86-64 user_regs_struct 的欄位順序（from sys/user.h）
REG_NAMES = ["r15","r14","r13","r12","rbp","rbx","r11","r10","r9","r8",
             "rax","rcx","rdx","rsi","rdi","orig_rax","rip","cs","eflags",
             "rsp","ss","fs_base","gs_base","ds","es","fs","gs"]

class Regs(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in REG_NAMES]

def get_regs(pid):
    regs = Regs()
    ptrace(GETREGS, pid, 0, ctypes.addressof(regs))
    return regs

def set_regs(pid, regs):
    ptrace(SETREGS, pid, 0, ctypes.addressof(regs))
```

## Part 2：啟動 inferior

承 Ch 2/3 的 fork + TRACEME + exec：

```python
def launch(path, args):
    pid = os.fork()
    if pid == 0:
        # 子 process：宣告被 trace，換成目標程式
        ptrace(TRACEME, 0, 0, 0)
        os.execv(path, [path] + args)
        os._exit(1)
    # 父 process：等子 process exec 後的第一次 stop
    os.waitpid(pid, 0)
    return pid
```

## Part 3：讀寫記憶體（PEEK/POKE，含 byte 級）

承 Ch 39：POKE 是 word（8 byte）為單位，patch 1 byte 要 read-modify-write：

```python
def read_mem(pid, addr, size):
    """讀任意長度（用 /proc/pid/mem 較快，這裡用 PEEK 示範原理）"""
    data = b""
    for off in range(0, size, 8):
        word = ptrace(PEEKTEXT, pid, addr + off, 0)
        data += struct.pack("<q", word)
    return data[:size]

def read_byte(pid, addr):
    word = ptrace(PEEKTEXT, pid, addr, 0)
    return word & 0xff

def write_byte(pid, addr, byte):
    """patch 1 byte：read word → 改最低 byte → write word（Ch 39）"""
    word = ptrace(PEEKTEXT, pid, addr, 0)
    new_word = (word & ~0xff) | (byte & 0xff)
    ptrace(POKETEXT, pid, addr, new_word)
```

## Part 4：斷點（INT3，Ch 39 的完整實作）

這是核心。實作 Ch 39 的「下、命中退1、跨過自己」：

```python
INT3 = 0xCC

class Breakpoint:
    def __init__(self, pid, addr):
        self.pid = pid
        self.addr = addr
        self.original = None
        self.enabled = False

    def enable(self):
        self.original = read_byte(self.pid, self.addr)   # 存原始 byte
        write_byte(self.pid, self.addr, INT3)            # patch 0xCC
        self.enabled = True

    def disable(self):
        write_byte(self.pid, self.addr, self.original)   # 還原原始 byte
        self.enabled = False
```

continue 時要處理「停在自己的斷點上」（Ch 39 步驟 9-11）：

```python
def step_over_breakpoint(pid, breakpoints):
    """如果當前 $pc-1 是個斷點，正確跨過它"""
    regs = get_regs(pid)
    bp_addr = regs.rip - 1                    # INT3 後 pc 多 1
    bp = breakpoints.get(bp_addr)
    if bp and bp.enabled:
        regs.rip = bp_addr                    # $pc 退 1（Ch 39 步驟 6）
        set_regs(pid, regs)
        bp.disable()                          # 還原原指令
        ptrace(SINGLESTEP, pid, 0, 0)         # 執行原指令一步
        os.waitpid(pid, 0)
        bp.enable()                           # 重新 patch 回 INT3

def cont(pid, breakpoints):
    step_over_breakpoint(pid, breakpoints)    # 先跨過當前斷點（若有）
    ptrace(CONT, pid, 0, 0)
    _, status = os.waitpid(pid, 0)
    return status
```

`step_over_breakpoint` 是斷點實作最精妙的部分——它把 Ch 39 的 $pc 退 1、還原、single-step、重 patch 全做了。寫過這段，你對 GDB 的斷點再無神秘。

## Part 5：DWARF 符號翻譯（Ch 38）

用 pyelftools 讀 line table，把位址翻成行號：

```python
from elftools.elf.elffile import ELFFile

class SymbolInfo:
    def __init__(self, path):
        self.path = path
        self.line_map = []      # [(address, filename, line), ...]
        self.func_map = {}      # name -> address
        self._load()

    def _load(self):
        with open(self.path, "rb") as f:
            elf = ELFFile(f)
            # 函式名 → 位址（從 symbol table）
            symtab = elf.get_section_by_name(".symtab")
            if symtab:
                for sym in symtab.iter_symbols():
                    if sym["st_info"]["type"] == "STT_FUNC" and sym["st_value"]:
                        self.func_map[sym.name] = sym["st_value"]
            # 位址 → 行號（從 DWARF line program，Ch 38）
            if elf.has_dwarf_info():
                dw = elf.get_dwarf_info()
                for cu in dw.iter_CUs():
                    lp = dw.line_program_for_CU(cu)
                    if not lp: continue
                    files = lp["file_entry"]
                    for entry in lp.get_entries():
                        s = entry.state
                        if s and not s.end_sequence:
                            fname = files[s.file - 1].name.decode() if s.file else "?"
                            self.line_map.append((s.address, fname, s.line))
        self.line_map.sort()

    def addr_to_line(self, addr):
        """位址 → (檔案, 行)，用 line table 找最近的（Ch 38）"""
        best = None
        for a, fname, line in self.line_map:
            if a <= addr:
                best = (fname, line)
            else:
                break
        return best

    def func_addr(self, name):
        return self.func_map.get(name)
```

> 注意：這是**非 PIE** 的簡化版（位址直接用檔案位址）。PIE 程式（Ch 40）要從 `/proc/<pid>/maps` 讀 load bias，把 DWARF 位址 + bias 才是 runtime 位址。為了聚焦核心，這裡假設 `gcc -no-pie` 編譯目標。延伸挑戰處理 PIE。

## Part 6：主迴圈

把全部組裝成一個 REPL：

```python
def main():
    if len(sys.argv) < 2:
        print("usage: minidbg.py <program>"); sys.exit(1)
    path = os.path.abspath(sys.argv[1])
    syms = SymbolInfo(path)
    pid = launch(path, sys.argv[2:])
    breakpoints = {}
    print(f"[minidbg] launched {path} pid={pid}")

    while True:
        try:
            cmd = input("(minidbg) ").strip().split()
        except EOFError:
            break
        if not cmd: continue
        op = cmd[0]

        if op in ("break", "b"):
            name = cmd[1]
            addr = syms.func_addr(name) or int(cmd[1], 0)
            bp = Breakpoint(pid, addr)
            bp.enable()
            breakpoints[addr] = bp
            print(f"breakpoint at {addr:#x} ({name})")

        elif op in ("continue", "c"):
            status = cont(pid, breakpoints)
            if os.WIFEXITED(status):
                print(f"[exited {os.WEXITSTATUS(status)}]"); break
            regs = get_regs(pid)
            loc = syms.addr_to_line(regs.rip - 1)   # 停在斷點，pc 已退過
            print(f"stopped at {regs.rip-1:#x}" + (f" {loc[0]}:{loc[1]}" if loc else ""))

        elif op in ("stepi", "si"):
            step_over_breakpoint(pid, breakpoints)
            ptrace(SINGLESTEP, pid, 0, 0)
            os.waitpid(pid, 0)
            regs = get_regs(pid)
            print(f"rip = {regs.rip:#x}")

        elif op in ("regs", "info"):
            regs = get_regs(pid)
            for r in ["rip", "rsp", "rbp", "rax", "rdi", "rsi"]:
                print(f"  {r} = {getattr(regs, r):#x}")

        elif op in ("x", "mem"):
            addr = int(cmd[1], 0); n = int(cmd[2]) if len(cmd) > 2 else 16
            data = read_mem(pid, addr, n)
            print(" ".join(f"{b:02x}" for b in data))

        elif op in ("quit", "q"):
            break
        else:
            print("commands: break/continue/stepi/regs/x/quit")

if __name__ == "__main__":
    main()
```

## 跑起來

```bash
pip install pyelftools
gcc -g -O0 -no-pie test.c -o test       # -no-pie 配合簡化版
python3 minidbg.py ./test
```

```
[minidbg] launched /path/test pid=12345
(minidbg) break main
breakpoint at 0x401136 (main)
(minidbg) continue
stopped at 0x401136 test.c:5
(minidbg) regs
  rip = 0x401136
  ...
(minidbg) stepi
rip = 0x40113a
(minidbg) continue
[exited 0]
```

你寫了一個 debugger。它能下斷點、continue、single-step、看暫存器/記憶體、把位址翻成行號——GDB 的核心骨架。**現在 GDB 對你不再是黑盒。**

## 從 mini 到 GDB：缺了什麼

對照真實 GDB，你的 minidbg 缺：

| 缺的 | 哪章學過原理 | 難度 |
|---|---|---|
| 區域變數讀取（location expr）| Ch 38 | 中（解析 DWARF location）|
| source-level step（不只 stepi）| Ch 5, 38 | 中（single-step + 查 line range）|
| backtrace（unwinding）| Ch 10, 38 | 高（讀 CFI）|
| 型別/struct 顯示 | Ch 9, 38 | 高（DWARF type DIE）|
| PIE 支援 | Ch 40 | 低（加 load bias）|
| 硬體斷點/watchpoint | Ch 13, 39 | 中（debug register）|
| 多執行緒 | Ch 16 | 高 |
| 條件斷點 | Ch 12 | 低（求值後決定停不停）|

每一塊你都學過原理。GDB 就是把這些全部做完、做robust、跨架構、加一萬個功能。但核心——ptrace 控制 + DWARF 翻譯——就是你寫的這 200 行。

## 踩雷集錦

1. **忘記 $pc 退 1**：INT3 後 pc 多 1，不退回斷點位址，addr_to_line 會對到下一行、re-enable 會 patch 錯位址。
2. **continue 不跨過當前斷點**：停在斷點直接 CONT，會立刻又執行到 0xCC（其實是停在 0xCC 之後，但下次到這斷點要先還原）。`step_over_breakpoint` 必做。
3. **POKE 不是 byte 級**：直接 POKE 一個 byte 會蓋掉整個 word。read-modify-write（write_byte）。
4. **PIE 位址對不上**：簡化版假設 no-pie。PIE 要加 load bias（從 /proc/maps），否則斷點下到錯位址 → 程式照跑不停。
5. **pyelftools line program 的 file index**：DWARF 4/5 的 file 索引基準不同（0-based vs 1-based），`files[s.file - 1]` 要依版本調整。
6. **waitpid 節奏**：每次 CONT/SINGLESTEP 後必須 waitpid（Ch 2 踩雷）。漏了會錯亂。
7. **多 thread / fork**：簡化版只處理單 thread 單 process。真實程式 fork/clone 會讓它失控。

## 進階：再往深一層

- **讀區域變數**：解析 DWARF 的 `DW_AT_location`（Ch 38），對 `DW_OP_fbreg` 算 frame base + offset，read_mem 讀出來——讓 minidbg 能 `print x`。
- **source-level step**：single-step 迴圈 + 查 line table 比對「還在當前行範圍嗎」（Ch 5 的演算法）——讓 `step` 不只是 `stepi`。
- **backtrace**：讀 `.eh_frame` CFI（用 pyelftools 的 dwarf CFI 支援）一層層 unwind——最難但最有成就感。
- **/proc/pid/mem**：比 PEEK 一次一 word 快得多的批次讀記憶體（Ch 2 進階提過）。
- **PIE 支援**：開啟 inferior 後讀 `/proc/<pid>/maps` 拿 load bias，所有位址加 bias。
- **用 C 重寫**：Sy Brand 的「Writing a Linux Debugger」系列用 C++ + libelfin，是這個的完整版參考。
- **和 Final Project 的關係**：你現在懂了 debugger 本體。Final Project 不是再寫一個 debugger，而是**站在 GDB 上**寫插件（Part 5 的 Python API）——但有了 minidbg 的底氣，你會清楚知道每個 API（read_memory、Breakpoint、Frame）底下對應你親手寫過的什麼。

## 動手練習

1. 把本章的 `minidbg.py` 完整跑起來（`-no-pie` 目標），下斷點、continue、stepi、regs、x 全試一遍。
2. 加一個 `delete` 指令（disable 斷點）。
3. 加一個 `step` 指令（用 line table 做 source-level step：single-step 直到行號變）。
4. 加 PIE 支援：讀 `/proc/<pid>/maps` 拿 load bias，所有位址加 bias，改用 `gcc -g`（預設 PIE）的目標測試。
5. （進階）加 `print <var>`：解析 DWARF location expression，讀出區域變數的值。
6. （進階）加 `bt`：讀 CFI 做一層 backtrace。
7. 對照你的 minidbg 和 GDB 對同一程式的行為，列出 GDB 多做了什麼。

## 本章重點整理

- mini debugger 五大能力：啟動（fork+TRACEME+exec）、斷點（PEEK/POKE INT3）、執行控制（CONT/SINGLESTEP）、檢視（GETREGS/讀記憶體）、符號翻譯（DWARF line table）。
- 斷點實作的精髓：下=存原 byte+patch 0xCC；命中=pc 退 1；continue 跨過自己=還原+single-step+重 patch。
- PIE 要加 load bias（/proc/maps）；POKE 是 word 級，patch byte 要 read-modify-write。
- 缺的功能（變數、source step、backtrace、型別）原理都學過，是 DWARF 解析的延伸。
- 寫過 minidbg = 徹底理解 GDB 本體；Final Project 在這之上用 Python API 寫插件，你會知道每個 API 底下在做什麼。

## 自我檢核

- [ ] 不看 code，講得出下斷點、命中、continue 跨過斷點的完整步驟嗎？
- [ ] 為什麼命中斷點後 $pc 要退 1？continue 時為什麼要 single-step 一步？
- [ ] 為什麼 patch 1 byte 要 read-modify-write？
- [ ] PIE 程式的位址要怎麼處理？
- [ ] 你的 minidbg 和 GDB 差在哪？那些缺的功能各對應哪章的原理？

## 延伸閱讀

### 部落格 / 文章（核心參考）

- **[Writing a Linux Debugger](https://blog.tartanllama.com/writing-a-linux-debugger-setup/)** — Sy Brand（10 篇系列）
  - **這篇說什麼**：用 C++ + libelfin 從零寫一個完整的 ptrace+DWARF debugger，含斷點、step、變數讀取、backtrace、unwinding。
  - **讀哪裡**：全系列；本章是它的 Python 濃縮版，想做更完整（變數/backtrace）就跟這個。
  - **為什麼值得讀**：最完整、最清楚的「自寫 debugger」教材；Ch 41 的延伸挑戰全在裡面。

- **[How debuggers work (Part 1-3)](https://eli.thegreenplace.net/2011/01/23/how-debuggers-work-part-1)** — Eli Bendersky
  - **這篇說什麼**：ptrace、breakpoint、DWARF debug info 三篇，每篇都有可跑 code。
  - **和本章的關聯**：本章三大塊（ptrace/斷點/DWARF）的分篇詳解。

### 工具

- **[pyelftools 文件](https://github.com/eliben/pyelftools)**
  - **讀哪裡**:examples/ 目錄的 dwarf_* 範例。
  - **和本章的關聯**：本章 DWARF 解析用它；延伸挑戰（location、CFI）的 API 在這。

最後一章，俯瞰 GDB 本體的內部架構，並指出怎麼讀它的原始碼、怎麼貢獻——從「能改 GDB 的插件」到「能改 GDB 本身」。

→ [Ch 42 GDB 內部架構與如何貢獻](./42-gdb-internals-and-contributing.md)
