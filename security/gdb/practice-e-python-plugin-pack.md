# 練習 E — 寫一套 Python 插件（heap 視覺化）

> **目標**：綜合 Part 5（Command、Value/Type、Breakpoint/event、pretty-printer、xmethod、彩色），寫出你的第一個真正的 GDB 插件——一個 heap 視覺化工具。它能列出 glibc heap 的 chunk、追蹤 malloc/free、用 telescope 看記憶體。完成後你就具備了 Final Project（完整 gef 風格套件）的全部基礎能力。

## 背景與動機

heap 是 C/C++ 程式最常出問題的地方（use-after-free、double-free、overflow），也是 pwn 的主戰場。但原生 GDB 看 heap 很痛苦——你得手動 `x` 一個個 chunk、自己算 size、自己解 fd/bk 鏈。gef/pwndbg 的 `heap chunks`、`heap bins` 指令把這變成一行。這個練習讓你寫一個簡版——你會用到 Part 5 幾乎每個 API，做完就懂那些神級工具的底層，也準備好做 Final Project。

## 先理解 glibc heap chunk 佈局

要分析 heap，先懂 glibc malloc 的 chunk 結構（簡化版）：

```
   一個 allocated chunk（malloc 回傳的指標往前 16 bytes 是 header）
   ┌────────────────────────┐  ← chunk 起始（mem_ptr - 16）
   │ prev_size (8 bytes)    │    前一個 chunk 的 size（若前一個 free）
   ├────────────────────────┤
   │ size (8 bytes)         │    本 chunk 大小 | 低 3 bit 是 flags
   │   ...|A|M|P            │    P=prev_inuse, M=mmap, A=non_main_arena
   ├────────────────────────┤  ← malloc() 回傳的指標指這（user data 起點）
   │ user data ...          │
   │                        │
   └────────────────────────┘

   size 的低 3 bit 是 flag，真正大小 = size & ~0x7
   chunk 之間相鄰：next_chunk = chunk + (size & ~0x7)
```

關鍵事實：
- malloc 回傳的指標，往前 0x10 是 chunk header（prev_size + size）。
- `size` 欄位的低 3 bit 是 flag，真實 size 要 `& ~0x7`。
- chunk 在記憶體裡相鄰排列，從 heap 起點可以一個個走（`chunk += size`）。

## 任務規格

實作一個 prefix command `myheap`，含三個子命令：

**`myheap chunks [count]`** — 從 heap 起點列出 chunk：每個 chunk 的位址、size、flags（prev_inuse 等）、前幾個 byte 的內容。

**`myheap trace`** — 開關 malloc/free 追蹤：開啟後，每次 malloc/free 自動記錄（size、回傳指標、釋放的指標），用於抓 double-free / UAF。

**`tel <addr> [n]`** — telescope：dump 記憶體，每個 slot 嘗試解讀成符號/指標，並上色（指標、stack、heap、code 不同色）。

### 目標程式

```c
// heapdemo.c — gcc -g -O0 heapdemo.c -o heapdemo
#include <stdlib.h>
#include <string.h>
int main(void) {
    char *a = malloc(0x18);  strcpy(a, "AAAA");
    char *b = malloc(0x28);  strcpy(b, "BBBB");
    char *c = malloc(0x38);  strcpy(c, "CCCC");
    free(b);
    char *d = malloc(0x18);  // 可能重用 b 的空間
    free(a); free(c); free(d);
    return 0;                // ← break 在這之前分析
}
```

### 驗收標準

- [ ] `myheap chunks` 能列出至少 a/b/c 三個 chunk，size 正確（含 header 與對齊）
- [ ] chunk 的 prev_inuse flag 正確解析
- [ ] `myheap trace` 能記錄每次 malloc 的 size+回傳值、每次 free 的指標
- [ ] `tel` 能解讀指標、區分記憶體區段並上色
- [ ] 插件用 prefix command 組織，有 help、有設定開關
- [ ] 程式碼模組化、可 source、防壞記憶體（try/except）

## 如果你卡住了

1. **heap 起點在哪？** glibc 的 main arena 第一個 chunk 通常在 `&main_arena` 之後，但簡單做法：對 `__curbrk` 或用 `info proc mappings` 找 `[heap]` 段的起點。或更簡單：從第一個 malloc 回傳的指標 `- 0x10` 當起點。
2. **怎麼讀 chunk size？** `inf.read_memory(chunk_addr + 8, 8)` 讀 size 欄位，`& ~0x7` 去 flag，`& 1` 是 prev_inuse。
3. **怎麼判斷一個值是指標還是數字？** 查它是否落在 `info proc mappings` 的某個段內。先抓 mappings 建一個範圍表。
4. **malloc/free 追蹤怎麼抓 size 和回傳值？** 對 malloc 下 Breakpoint，`stop()` 裡讀 `$rdi`（size），設 FinishBreakpoint 抓回傳值（Ch 25）。free 讀 `$rdi`（被釋放的指標）。

## 實作步驟建議

### Step 1：telescope（最基礎，先做）

寫 `tel <addr> [n]`：read_memory 每 8 byte、用 `info symbol` 解讀、判斷區段、上色。這是 Ch 24 telescope 範例的加強版。

### Step 2：記憶體區段表

寫一個 helper：解析 `info proc mappings`（或用 Python API）建立 `[(start, end, name, perms)]`，提供 `classify(addr)` → "heap"/"stack"/"code"/"libc"/None。telescope 上色靠它。

### Step 3：heap chunk 走訪

寫 `myheap chunks`：找 heap 起點，迴圈讀 chunk header、解析 size/flags、印出、`chunk += size` 走下一個，直到走出 heap 段。

### Step 4：malloc/free 追蹤

寫 `myheap trace`：用 Breakpoint + FinishBreakpoint（Ch 25）掛 malloc/free，記錄到一個 list，可偵測 double-free（同一指標 free 兩次）。

### Step 5：整合成 prefix command

把全部包成 `myheap` prefix + 子命令（Ch 24），加 `set myheap-color` 開關（Parameter），模組化成可 source 的檔案。

## 完整參考解答

**自己做到 Step 3 再看。**

<details>
<summary>點開插件實作</summary>

```python
# myheap.py — 一個迷你 heap 分析插件
import gdb

# ---------- 顏色 ----------
class C:
    RED="\033[31m"; GRN="\033[32m"; YEL="\033[33m"; BLU="\033[34m"
    MAG="\033[35m"; CYN="\033[36m"; BOLD="\033[1m"; RST="\033[0m"

USE_COLOR = True
def col(s, c):
    return f"{c}{s}{C.RST}" if USE_COLOR else str(s)

# ---------- 記憶體區段分類 ----------
def get_mappings():
    out = gdb.execute("info proc mappings", to_string=True)
    maps = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].startswith("0x"):
            try:
                start = int(parts[0], 16); end = int(parts[1], 16)
                name = parts[-1] if not parts[-1][0].isdigit() else ""
                maps.append((start, end, name))
            except ValueError:
                pass
    return maps

def classify(addr, maps):
    for start, end, name in maps:
        if start <= addr < end:
            if "heap" in name: return ("heap", C.GRN)
            if "stack" in name: return ("stack", C.MAG)
            if "libc" in name: return ("libc", C.YEL)
            if name and name[0] == "/": return ("code", C.RED)
            return ("mapped", C.CYN)
    return (None, C.RST)

# ---------- telescope ----------
class Telescope(gdb.Command):
    """tel <addr> [count] — dump memory, resolve & colorize pointers."""
    def __init__(self):
        super().__init__("tel", gdb.COMMAND_DATA, gdb.COMPLETE_EXPRESSION)
    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if not argv:
            raise gdb.GdbError("usage: tel <addr> [count]")
        base = int(gdb.parse_and_eval(argv[0]))
        count = int(argv[1]) if len(argv) > 1 else 8
        inf = gdb.selected_inferior()
        maps = get_mappings()
        for i in range(count):
            a = base + i*8
            try:
                val = int.from_bytes(inf.read_memory(a, 8), "little")
            except gdb.MemoryError:
                print(f"{a:#018x}: {col('<unmapped>', C.RED)}"); break
            kind, color = classify(val, maps)
            tag = ""
            if kind:
                tag = col(f"<{kind}>", color)
                sym = self.symbolize(val)
                if sym: tag += " " + col(sym, C.BLU)
            print(f"{col(f'{a:#018x}', C.CYN)}|+{i*8:04x}: "
                  f"{col(f'{val:#018x}', color)}  {tag}")
    def symbolize(self, val):
        try:
            s = gdb.execute(f"info symbol {val:#x}", to_string=True).strip()
            if "No symbol" not in s:
                return s.split(" in ")[0]
        except gdb.error:
            pass
        return ""
Telescope()

# ---------- heap ----------
class MyHeap(gdb.Command):
    """myheap — heap analysis toolkit. Subcommands: chunks, trace."""
    def __init__(self):
        super().__init__("myheap", gdb.COMMAND_USER, prefix=True)
    def invoke(self, arg, from_tty):
        print("usage: myheap <chunks|trace>")

class HeapChunks(gdb.Command):
    """myheap chunks [count] — list heap chunks."""
    def __init__(self):
        super().__init__("myheap chunks", gdb.COMMAND_USER)
    def heap_start(self):
        # 從 info proc mappings 找 [heap]
        for start, end, name in get_mappings():
            if "heap" in name:
                return start, end
        raise gdb.GdbError("no [heap] mapping (program may not have malloc'd yet)")
    def invoke(self, arg, from_tty):
        count = int(arg) if arg.strip() else 50
        start, end = self.heap_start()
        inf = gdb.selected_inferior()
        addr = start
        i = 0
        while addr < end and i < count:
            try:
                prev_size = int.from_bytes(inf.read_memory(addr, 8), "little")
                size_field = int.from_bytes(inf.read_memory(addr+8, 8), "little")
            except gdb.MemoryError:
                break
            size = size_field & ~0x7
            prev_inuse = size_field & 1
            mmapped = (size_field >> 1) & 1
            if size == 0:
                break
            # 讀前 8 byte user data 預覽
            try:
                preview = bytes(inf.read_memory(addr+16, 8))
                preview_s = preview.decode("latin-1").replace("\x00", ".")
            except gdb.MemoryError:
                preview_s = "?"
            flags = []
            if prev_inuse: flags.append("P")
            if mmapped: flags.append("M")
            print(f"{col(f'chunk {addr:#x}', C.BOLD)}  "
                  f"size={col(f'{size:#x}', C.GRN)}  "
                  f"flags=[{','.join(flags)}]  "
                  f"data='{preview_s}'")
            addr += size
            i += 1
HeapChunks()

# ---------- malloc/free trace ----------
class TraceState:
    enabled = False
    log = []
    live = {}     # addr -> size

class MallocFinish(gdb.FinishBreakpoint):
    def __init__(self, size):
        super().__init__(internal=True)
        self.size = size
    def stop(self):
        ret = int(self.return_value)
        TraceState.log.append(("malloc", self.size, ret))
        TraceState.live[ret] = self.size
        print(col(f"[malloc] size={self.size:#x} -> {ret:#x}", C.GRN))
        return False

class MallocBP(gdb.Breakpoint):
    def stop(self):
        if not TraceState.enabled: return False
        size = int(gdb.selected_frame().read_register("rdi"))
        try:
            MallocFinish(size)
        except ValueError:
            pass
        return False

class FreeBP(gdb.Breakpoint):
    def stop(self):
        if not TraceState.enabled: return False
        ptr = int(gdb.selected_frame().read_register("rdi"))
        if ptr == 0: return False
        if ptr not in TraceState.live:
            print(col(f"[free] {ptr:#x}  *** DOUBLE-FREE or invalid! ***", C.RED))
        else:
            print(col(f"[free] {ptr:#x} (size {TraceState.live[ptr]:#x})", C.YEL))
            del TraceState.live[ptr]
        TraceState.log.append(("free", ptr))
        return False

class HeapTrace(gdb.Command):
    """myheap trace — toggle malloc/free tracing."""
    _bps = []
    def __init__(self):
        super().__init__("myheap trace", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        TraceState.enabled = not TraceState.enabled
        if TraceState.enabled and not HeapTrace._bps:
            HeapTrace._bps = [MallocBP("malloc"), FreeBP("free")]
        print(f"malloc/free trace {'ON' if TraceState.enabled else 'OFF'}")
HeapTrace()

MyHeap()
print("myheap loaded: myheap chunks | myheap trace | tel <addr>")
```

用法：

```
(gdb) source myheap.py
(gdb) break main
(gdb) run
(gdb) myheap trace          # 開啟追蹤
(gdb) continue              # 看 malloc/free log，double-free 會標紅
... 程式結束或斷在某處 ...
(gdb) myheap chunks         # 列出所有 chunk
(gdb) tel $rsp 8            # telescope stack
```

**解答說明**：

- **telescope** 用了 read_memory（Ch 22）+ classify（解析 mappings）+ symbolize（info symbol）+ 彩色（Ch 28）。這是 gef telescope 的核心結構。
- **heap chunks** 直接解析 glibc chunk 佈局：讀 size 欄位、`& ~0x7` 去 flag、`& 1` 取 prev_inuse、`addr += size` 走下一個。這就是 `pwndbg heap` 的簡化版。
- **malloc/free trace** 用 Breakpoint + FinishBreakpoint（Ch 25）配對抓 size 與回傳值，並用一個 `live` dict 偵測 double-free。
- **整合**用 prefix command（Ch 24）。

**這個練習用到的 Part 5 API**：Command/prefix（Ch 24）、Value/read_memory（Ch 22-23）、Breakpoint/FinishBreakpoint（Ch 25）、彩色（Ch 28）、parse_and_eval。幾乎涵蓋整個 Part 5——做完這個，Final Project 就是把它擴大、加上 context 自動顯示、pretty-printer、frame filter、bin 分析。

</details>

## 測試用例

| 操作 | 預期 |
|---|---|
| `myheap chunks` | 列出 a/b/c chunk，size 含 header 對齊（0x18→0x20 等） |
| `myheap trace` + continue | 印出每次 malloc/free |
| 製造 double-free（`free(a); free(a);`） | trace 標紅 DOUBLE-FREE |
| `tel $sp 8` | 解讀並上色 stack 上的指標 |
| `tel &main 4` | 顯示 code 段位址（紅色）與符號 |

## 延伸挑戰（加分）

1. **`myheap bins`**：解析 glibc 的 tcache / fastbins / unsorted bin，顯示 free chunk 鏈（pwn 必備）。需要讀 `tcache_perthread_struct` 與 `main_arena`。
2. **自動 context**：用 events.stop（Ch 25）讓程式每次停下時自動顯示 `tel $sp` + registers——往 Final Project 的 context 邁進。
3. **UAF 偵測**：trace 模式下，記錄 freed chunk，若之後有 `tel` / 存取落在 freed 區域就警告。
4. **pretty-printer 整合**：為程式裡的某結構寫 printer（Ch 26），讓 chunk 的 user data 顯示成結構。
5. **設定開關**：加 `set myheap-color off`（Parameter，Ch 24），讓彩色可關（pipe/logging 時）。
6. **對照 pwndbg**：裝 pwndbg，用它的 `heap`/`bins`/`telescope` 對比你的輸出，列出它多做了什麼——這份清單是 Final Project 的功能藍圖。

## 自我檢核

- [ ] 我能用 read_memory + cast 解析 glibc chunk 佈局
- [ ] 我能用 Breakpoint + FinishBreakpoint 配對追蹤 malloc/free
- [ ] 我能解析 info proc mappings 並用它分類/上色記憶體位址
- [ ] 我能用 prefix command + Parameter 組織一個多功能插件
- [ ] 我理解這個插件和 gef/pwndbg 的關係，知道 Final Project 要再加什麼

Part 5 完成——你已經能寫真正的 GDB 插件。Part 6 轉向真實世界的 binary：C++ 深度除錯、STL/Rust/Go pretty-printer、以及最難的——除錯最佳化過的 release binary。

→ [Ch 29 C++ 深度除錯](./29-cpp-deep-debugging.md)
