# Ch 30 — GDB Python 進階用法

> 目標：把 GDB 從互動 debugger 升級到「自動化 debug 平台」。寫 pretty-printer 把難看 struct 印漂亮、寫 hook 自動跑 routine、寫 frame filter 隱藏 noise frame、寫 inferior call 控制 target。

## 為什麼要 Python in GDB

互動 debug 寫多了你會發現：

- 每次 stop 都要打一連串相同命令（`info reg`, `info locals`, `bt`）
- 看一個 list / hashmap 要手動走 next pointer
- 想統計「進 foo 函式幾次」要算
- 寫 conditional breakpoint 在 hot loop 慢

GDB 從 7.x 起內建 Python interpreter，`(gdb) python ...` 直接寫 script 自動化。**所有 GDB 命令、變數、frame、breakpoint 都有對應 Python API**。

## 第一個 Python hello

```
(gdb) python print("hello from gdb")
hello from gdb

(gdb) python print(gdb.parse_and_eval("x"))   # 印 GDB context 中的變數 x
42

(gdb) python gdb.execute("info reg")          # 跑 GDB 命令
```

寫長 script 用 `python` block：

```
(gdb) python
> for i in range(5):
>     print(f"i = {i}")
> end
```

或寫 `.gdbinit` / 獨立 `.py` 檔。

## Pretty-printer：把 struct 印漂亮

預設 GDB 印一個 `struct list_node *head`：

```
(gdb) p head
$1 = (struct list_node *) 0x20001234
```

不直觀。寫 pretty-printer 顯示成 list：

```python
class ListPrinter:
    def __init__(self, val):
        self.val = val

    def to_string(self):
        return "list"

    def children(self):
        node = self.val
        i = 0
        while node:
            yield (f"[{i}]", node['data'])
            node = node['next']
            i += 1

def lookup_pp(val):
    if str(val.type) == 'struct list_node *':
        return ListPrinter(val)
    return None

gdb.pretty_printers.append(lookup_pp)
```

存到 `mylist.py`，GDB 啟動 `(gdb) source mylist.py`：

```
(gdb) p head
$2 = list = {[0] = 1, [1] = 2, [2] = 3, [3] = 4}
```

漂亮。**Linux kernel debug 必用**：debug Linux ARM kernel 時的 `struct task_struct` printer 把 PID / state / cmdline 直接印給你看。

## Hook：每次 stop 自動跑

```python
def stop_handler(event):
    # 每次 target stop 自動印 PC
    pc = gdb.parse_and_eval("$pc")
    print(f"Stopped at PC = {pc}")

    # 印當前 frame 的所有 local
    gdb.execute("info locals")

gdb.events.stop.connect(stop_handler)
```

`gdb.events` 提供：

- `stop`：每次停下
- `cont`：每次 continue
- `exited`：target exit
- `new_objfile`：load 新 ELF
- `breakpoint_created`：設 breakpoint
- ...

寫 `.gdbinit` 或 auto-load script，每次 GDB 啟動自動 attach。

## Inferior call：在 GDB 裡跑 target 函式

GDB 可以**叫 target CPU 跑某個函式**：

```python
result = gdb.parse_and_eval("compute_checksum(buffer, 100)")
print(f"Checksum: {result}")
```

Python API：

```python
fn = gdb.parse_and_eval("compute_checksum")
buf = gdb.parse_and_eval("buffer")
result = fn(buf, 100)
```

GDB 暫停 target → push 參數 → 設 PC = compute_checksum → resume → 等 ret → 拿回 result → 還原 state → 繼續原本 PC。

對 bare-metal 危險：函式可能有 side effect、改 register、踩 stack。**用前確認**。但對 「動態打 bug → 試 patch」非常強。

## Frame filter：隱藏 noise

C++ template 或 Linux kernel，backtrace 滿是 `__rcu_*`、`raw_spin_lock_irqsave_*` — 看不到真正邏輯。寫 frame filter：

```python
class HideKernelFilter:
    def __init__(self):
        self.name = "hide_kernel"
        self.priority = 100
        self.enabled = True
        gdb.frame_filters[self.name] = self

    def filter(self, frame_iter):
        for frame in frame_iter:
            name = frame.function() or ""
            if not name.startswith("__rcu_") and \
               not name.startswith("raw_spin_"):
                yield frame

HideKernelFilter()
```

之後 `bt` 自動隱藏指定 frame。

## 寫一個 Watch + hook：抓「誰改了我的變數」

最常見 ARM debug 場景：「為什麼 `state` 從 1 變成了 5？」 watchpoint + Python hook：

```python
# watch_state.py
import gdb

def on_stop(event):
    if isinstance(event, gdb.BreakpointEvent):
        for bp in event.breakpoints:
            if bp.is_watchpoint():
                pc = gdb.parse_and_eval("$pc")
                state = gdb.parse_and_eval("state")
                print(f"PC={pc}, state={state}")
                # 印 caller 來源
                gdb.execute("bt 5")

gdb.events.stop.connect(on_stop)
gdb.execute("watch state")
gdb.execute("continue")
```

每次 state 被寫，自動 print PC + backtrace + state 值。**比手動 hover 快 10 倍**。

## Custom command

```python
class PrintRegsByName(gdb.Command):
    def __init__(self):
        super().__init__("regs-by-name", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        for r in arg.split(","):
            v = gdb.parse_and_eval(f"${r.strip()}")
            print(f"{r}: {v}")

PrintRegsByName()
```

之後 GDB 內：

```
(gdb) regs-by-name x0,x1,x29,sp,pc
x0: 0x12
x1: 0x34
x29: 0x40000fc0
sp:  0x40000fb0
pc:  0x40000040
```

對「我關心固定一組 register」場景很方便。

## 實用範例：dump page table

抓 AArch64 EL1 stage-1 的 page table：

```python
import gdb

def dump_pt(ttbr_value, level=0, va_prefix=0):
    if level == 4:
        return
    table_addr = ttbr_value & 0x0000FFFFFFFFF000
    inferior = gdb.selected_inferior()
    raw = inferior.read_memory(table_addr, 4096)
    for i in range(512):
        entry = int.from_bytes(raw[i*8:(i+1)*8], "little")
        if not (entry & 1):
            continue
        va = va_prefix | (i << (12 + 9*(3-level)))
        if (entry & 3) == 1:    # block descriptor
            print(f"L{level} block: VA=0x{va:016x} -> PA=0x{entry & 0xFFFFFFFFF000:016x}")
        elif (entry & 3) == 3 and level < 3:    # table
            dump_pt(entry, level+1, va)
        elif (entry & 3) == 3 and level == 3:   # page
            print(f"L3 page: VA=0x{va:016x} -> PA=0x{entry & 0xFFFFFFFFF000:016x}")

class DumpPT(gdb.Command):
    def __init__(self):
        super().__init__("dump-pt", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        ttbr = int(gdb.parse_and_eval("$TTBR0_EL1"))
        dump_pt(ttbr)

DumpPT()
```

`(gdb) dump-pt` 印整個 page table。debug MMU 問題的神器。

## 寫 conditional breakpoint 但不慢

普通 GDB conditional breakpoint 在 hot loop 中慢（每次都 wire round-trip）。改用 Python：

```python
class ConditionalBP(gdb.Breakpoint):
    def __init__(self, spec, condition):
        super().__init__(spec, internal=True)
        self.cond = condition

    def stop(self):
        # 在 GDB 內評估，不發 packet
        return eval(self.cond, {"gdb": gdb})

ConditionalBP("foo", "int(gdb.parse_and_eval('x')) > 100")
```

stop() 返回 False → GDB 自動 continue（不通知 user）。**比 packet round-trip 條件評估快很多**，但仍要 trap 進 GDB 一次。對極 hot loop 還是 slow，但比純 GDB conditional 快 5-10 倍。

## CMSIS-SVD：自動展開 register

寫 ARM Cortex-M debug 時，看 NVIC / SCB / GPIO register 經常要查 reference manual。**CMSIS-SVD** 是 XML 格式的 register 描述，能讓 GDB 自動展開：

```bash
pip install cmsis-svd
gdb-multiarch
(gdb) python
>>> from cmsis_svd.parser import SVDParser
>>> parser = SVDParser.for_xml_file("STM32F407.svd")
>>> # 寫 GDB extension 讀 SVD、提供 svd-print 命令
>>> end
```

完整工具：**PyCortexMDebug** 已經做好這個，安裝後：

```
(gdb) source pycortexmdebug/svd_gdb.py
(gdb) svd_load STM32F407.svd
(gdb) svd
Available peripherals: NVIC, RCC, GPIOA, USART2, ...

(gdb) svd USART2
USART_SR  = 0x000000C0
USART_DR  = 0x00000000
USART_BRR = 0x0000016C    BRR.DIV_Mantissa = 22, BRR.DIV_Fraction = 12
...
```

**寫 STM32 driver bring-up 必裝**。免去翻 reference manual 的時間。

## 一個常見誤解

「GDB Python 是不是只給高手用？」

不是。**任何重複動作 ≥ 3 次就值得寫 Python**。連最簡單的：

```python
gdb.events.stop.connect(lambda e: gdb.execute("info reg"))
```

一行就有「每次 stop 自動印 register」的便利。寫 GDB Python 不需要學一大堆 API，邊做邊查就好。

## 自我檢核

- [ ] 我能寫一個 pretty-printer 印 linked list
- [ ] 我能用 `gdb.events.stop.connect` 自動印 PC
- [ ] 我能用 inferior call 在 GDB 裡跑 target 函式
- [ ] 我能寫一個 frame filter 隱藏特定函式
- [ ] 我能用 PyCortexMDebug + SVD 自動展開 STM32 register
- [ ] 我能寫一個 GDB custom command 做我自己的 routine

到這裡 Part 4 章節結束。下一個是練習 C — 拿一個故意埋了 race + memory ordering bug 的 Cortex-M 程式，用 GDB + OpenOCD + ITM 抓出來。

→ [練習 C：race + memory ordering bug 抓蟲實況](./practice-c-race-bug-hunt.md)
