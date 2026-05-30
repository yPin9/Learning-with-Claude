# Ch 25 — 程式化 Breakpoint 與事件

> **目標**：用 Python 控制斷點與事件——`gdb.Breakpoint` 子類別、覆寫 `stop()` 做程式化條件、`FinishBreakpoint` 抓回傳值、`gdb.events` 在 stop/cont/new_objfile 等時機掛 callback。學完你能做出「會自己反應」的工具：自動記錄、自動 context、條件式攔截——這是 gef「停下來自動顯示」的底層機制。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼要程式化斷點與事件

到目前的斷點都是「使用者下、命中就停」。Python 讓斷點變成**可程式化的攔截點**：

- 命中時跑任意 Python 邏輯（記錄、分析、判斷）而非只是停
- 用 Python 表示式做條件（命令語言條件做不到的複雜判斷）
- 抓函式回傳值做事
- 在「程式停下來」「載入新 library」「inferior 結束」等**事件**時自動反應

gef 為什麼一停下來就自動印 context？因為它在 `stop` 事件掛了 callback。學完這章，你就懂那個魔法，也能自己做。

## `gdb.Breakpoint`：Python 裡的斷點

```python
import gdb

bp = gdb.Breakpoint("main")               # 等同 break main
bp = gdb.Breakpoint("file.c:42")
bp = gdb.Breakpoint("malloc", gdb.BP_BREAKPOINT)
wp = gdb.Breakpoint("global_var", gdb.BP_WATCHPOINT)   # watchpoint

# 屬性
bp.enabled = False
bp.condition = "x > 100"                  # 設條件
bp.ignore_count = 5
bp.hit_count                              # 命中次數
bp.delete()                               # 刪除

# 列出所有斷點
for b in gdb.breakpoints():
    print(b.number, b.location, b.hit_count)
```

`gdb.Breakpoint` 是命令列斷點的 Python 鏡像——可建立、查屬性、改條件。但它真正的威力在下面的子類別。

## 覆寫 `stop()`：程式化條件

繼承 `gdb.Breakpoint`、覆寫 `stop()`，你就能用**任意 Python 邏輯**決定「要不要真的停」：

```python
import gdb

class SmartBreak(gdb.Breakpoint):
    def stop(self):
        # 回傳 True → 停下來給使用者；False → 自動繼續
        frame = gdb.selected_frame()
        val = int(frame.read_var("value"))
        if val > 1000:
            print(f"!! anomaly: value={val}")
            return True          # 停下來
        return False             # 不停，繼續跑

SmartBreak("process")
```

`stop()` 回傳 `True` 就停、`False` 就自動繼續。這比命令語言的條件斷點強太多：

- 可以做**任意複雜判斷**（呼叫 Python 函式、查資料結構、累積狀態）
- 可以**有副作用**（記錄到檔案、計數、收集統計）而仍自動繼續
- 可以**跨命中累積狀態**（class 成員變數記住歷史）

範例：記錄一個函式所有呼叫的參數，但不停下來（純 logging）：

```python
class CallLogger(gdb.Breakpoint):
    def __init__(self, spec):
        super().__init__(spec)
        self.calls = []
    def stop(self):
        frame = gdb.selected_frame()
        self.calls.append(int(frame.read_var("id")))
        return False             # 永遠不停，只記錄

logger = CallLogger("process")
# ... run 程式 ...
# python print(logger.calls)    # 事後看所有呼叫的 id
```

> 重要：`stop()` 裡**不要**呼叫會改變執行狀態的指令（`continue`、`step`）——它是在「決定要不要停」的脈絡，呼叫這些會亂。要做執行控制請用事件（下面）或回傳值控制。

## `FinishBreakpoint`：抓函式回傳值

想在某函式 return 時做事（看回傳值）？`FinishBreakpoint` 在當前函式返回時觸發：

```python
import gdb

class MallocTracer(gdb.Breakpoint):
    def stop(self):
        # 在 malloc 入口，設一個 finish breakpoint 抓回傳值
        size = int(gdb.selected_frame().read_var("bytes"))   # malloc 的參數
        FinishMalloc(size)
        return False

class FinishMalloc(gdb.FinishBreakpoint):
    def __init__(self, size):
        super().__init__(internal=True)
        self.size = size
    def stop(self):
        ret = self.return_value          # malloc 的回傳值（指標）！
        print(f"malloc({self.size}) = {int(ret):#x}")
        return False

MallocTracer("malloc")
```

`FinishBreakpoint.return_value` 給你函式的回傳值（Ch 5 `finish` 的 Python 版）。「在入口記參數 + 在出口記回傳值」配對，是做 malloc/free 追蹤器、heap 分析的標準模式（Final Project heap 功能）。

## `gdb.events`：在事件時自動反應

GDB 在關鍵時刻發出事件，你可以掛 callback：

```python
import gdb

def on_stop(event):
    # 每次 inferior 停下來都會呼叫——gef 自動 context 的核心！
    print("=== stopped ===")
    # 這裡可以印 registers / stack / code...

gdb.events.stop.connect(on_stop)         # 註冊
# gdb.events.stop.disconnect(on_stop)    # 取消
```

主要事件：

| 事件 | 觸發時機 | 用途 |
|---|---|---|
| `events.stop` | inferior 停下來 | **自動 context**（gef 核心） |
| `events.cont` | inferior 繼續執行 | 清理/記錄 |
| `events.new_objfile` | 載入新 library | 自動載入對應 printer/設定 |
| `events.exited` | inferior 結束 | 印 exit code、清理 |
| `events.new_inferior` | 新 inferior | 多行程感知 |
| `events.breakpoint_created` | 斷點建立 | 同步自己的狀態 |
| `events.memory_changed` | 記憶體被改 | 監控 |
| `events.register_changed` | 暫存器被改 | 監控 |

`events.stop` 的 callback 收到的 `event` 物件含停的原因：

```python
def on_stop(event):
    if isinstance(event, gdb.BreakpointEvent):
        print("stopped at breakpoint", event.breakpoints)
    elif isinstance(event, gdb.SignalEvent):
        print("stopped by signal", event.stop_signal)
```

## 做一個迷你「自動 context」

把這章串起來——gef 體驗的核心，30 行做出雛形：

```python
# autoctx.py
import gdb

def show_context(event):
    try:
        frame = gdb.selected_frame()
    except gdb.error:
        return
    print("\033[1;34m── registers ──\033[0m")
    for r in ["rax", "rbx", "rsp", "rbp", "rip"]:
        print(f"  {r} = {int(frame.read_register(r)):#018x}")
    print("\033[1;34m── code ──\033[0m")
    gdb.execute("x/3i $pc")
    print("\033[1;34m── stack ──\033[0m")
    gdb.execute("x/4gx $sp")

class CtxOn(gdb.Command):
    """ctxon — enable auto context on every stop."""
    def __init__(self):
        super().__init__("ctxon", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        gdb.events.stop.connect(show_context)
        print("auto-context enabled")

class CtxOff(gdb.Command):
    """ctxoff — disable auto context."""
    def __init__(self):
        super().__init__("ctxoff", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        try: gdb.events.stop.disconnect(show_context)
        except Exception: pass
        print("auto-context disabled")

CtxOn(); CtxOff()
```

```
(gdb) source autoctx.py
(gdb) ctxon
(gdb) break main
(gdb) run
── registers ──    ← 每次停自動出現！
  rax = ...
── code ──
── stack ──
(gdb) next         ← 每按一次，context 自動刷新
```

這就是 gef 的核心體驗。Final Project 會把它做完整（彩色 telescope、區分記憶體區段、heap 摘要、backtrace），但機制就是這個 `events.stop.connect`。

## 踩雷集錦

1. **`stop()` 裡呼叫 continue/step**：會破壞 GDB 的執行狀態機。`stop()` 只該回傳 True/False，不做執行控制。
2. **event callback 裡拋例外**：會在每次事件時噴錯。包 try/except，且用 `set python print-stack full` 開發時除錯。
3. **重複 connect 同一個 callback**：每次 `ctxon` 都 connect，停一次印好幾遍。connect 前先 disconnect，或用旗標防重複。
4. **忘記 `FinishBreakpoint` 的 scope**：它綁在「設定當下的那個 frame」，那個 frame return 後觸發。如果函式遞迴或被 longjmp 跳過，可能不觸發（GDB 會給 `out_of_scope`）。
5. **`stop()` 的效能**：每次命中都跑 Python，熱點函式上會慢（同條件斷點，Ch 12）。logging 大量呼叫時注意。
6. **event 與 selected_frame**：`events.stop` callback 裡 `selected_frame()` 通常可用，但在某些 exited 事件後 inferior 沒了，要判斷。
7. **`internal=True` 的斷點**：FinishBreakpoint 常設 internal（不顯示在 `info breakpoints`、不干擾使用者）。忘了設會讓使用者看到一堆內部斷點。

## 進階：再往深一層

- **`gdb.Breakpoint` 的 `temporary=True`**：Python 版臨時斷點。
- **conditional FinishBreakpoint 鏈**：在入口斷點的 `stop()` 裡動態建立 FinishBreakpoint，做 enter/exit 配對追蹤（malloc/free、lock/unlock）。
- **`events.new_objfile` 自動載 printer**：library 載入時自動 register 對應的 pretty-printer——讓你的插件「認得」特定 library 並增強顯示。
- **`events.memory_changed` / `register_changed`**：做「狀態變更日誌」，記錄 debug 過程中所有手動修改。
- **非同步事件與 `gdb.post_event`**：在 callback 裡排程稍後執行的動作（避免在事件 context 裡做危險操作）。
- **gef/pwndbg 的事件管理**：它們有完整的事件訂閱框架，managed connect/disconnect，避免重複與洩漏。讀它們的 `gef.py` 的 event 處理。

## 動手練習

1. 用 `gdb.Breakpoint("main")` 建斷點，`gdb.breakpoints()` 列出，改它的 `condition`、`enabled`。
2. 寫一個 `SmartBreak`，覆寫 `stop()`，只在某參數 > 閾值時停，否則自動繼續。
3. 寫一個 `CallLogger`（`stop()` 回 False + 記錄參數到 list），run 完後印出所有呼叫記錄。
4. 用 `FinishBreakpoint` 抓 `malloc` 的回傳值，做一個 malloc 追蹤器（入口記 size、出口記回傳指標）。
5. source 本章的 `autoctx.py`，`ctxon` 後 step 幾步，體驗 gef 式自動 context。
6. 掛一個 `events.new_objfile` callback，debug 一個 `dlopen` plugin 的程式，觀察 library 載入時 callback 觸發。

## 本章重點整理

- `gdb.Breakpoint` 是斷點的 Python 鏡像；覆寫子類別的 `stop()` 用任意 Python 邏輯決定停不停（回 True/False）。
- `stop()` 回 False 可做「純記錄不停」的 logging 斷點，並跨命中累積狀態。
- `FinishBreakpoint.return_value` 抓函式回傳值；「入口記參數 + 出口記回傳」是追蹤器標準模式。
- `gdb.events.*` 在 stop/cont/new_objfile/exited 等時機掛 callback——`events.stop` 是 gef 自動 context 的核心。
- `stop()` 裡不要做執行控制；event callback 要防重複註冊、包 try/except。

## 自我檢核

- [ ] 覆寫 `stop()` 比命令語言條件斷點強在哪？回傳值控制什麼？
- [ ] 怎麼做一個「記錄每次呼叫但不停下來」的斷點？
- [ ] 怎麼用 Python 抓一個函式的回傳值？什麼模式適合做 malloc/free 追蹤？
- [ ] gef「一停下來就自動印 context」是靠什麼機制？怎麼自己做一個？
- [ ] event callback 有哪些常見坑（重複註冊、例外、執行控制）？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Breakpoints In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Breakpoints-In-Python.html)** 與 **[Events In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Events-In-Python.html)**
  - **讀哪裡**：Breakpoint 屬性與 stop()、FinishBreakpoint、所有 event 類型與其屬性。
  - **和本章的關聯**：本章核心的完整參考；event 物件的屬性查這裡。

### 原始碼

- **[gef 的 context 與 hook 機制](https://github.com/hugsy/gef)**
  - **讀哪裡**：搜 `events.stop` / `gef_on_stop` / `context`。
  - **和本章的關聯**：本章 autoctx 的工業級完整版；Final Project 直接對標。

### 部落格

- **[Automating GDB with Python breakpoints](https://interrupt.memfault.com/blog/automate-debugging-with-gdb-python-api)** — Memfault Interrupt
  - **這篇說什麼**：用 Python breakpoint/event 自動化嵌入式除錯。
  - **為什麼值得讀**：把 stop()/event 放進真實自動化場景，實戰感強。

下一章進入 Python API 最常被需要的功能：pretty-printer——讓 `print myvector` 顯示成漂亮的 `{1,2,3}` 而非一坨內部指標。

→ [Ch 26 Pretty-printer 框架](./26-pretty-printer-framework.md)
