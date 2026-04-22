# Ch 15 — Python API（一）：commands 與 breakpoints

> 目標：熟悉 GDB 的 Python 3 解釋器、用 Python 寫自訂 command、用 Python 子類化 Breakpoint 做智慧斷點、用 `gdb.parse_and_eval` 在 Python 裡存取 inferior 狀態。

## 為什麼要 Python？

Ch 14 的 `define` 有夠多限制：

- 沒有真正的資料結構（只有 convenience variables）
- 不能讀寫檔案、呼叫 HTTP、搭 JSON
- Debug 時沒 traceback、沒有好的錯誤訊息
- 寫 pretty printer 幾乎不可能

Python API 補上這些缺陷。它是 gdb 內建的一個完整 Python 3 環境，有存取 inferior 的特化 module `gdb`。

## 確認 Python 可用

```
(gdb) python print("hello from python")
hello from python

(gdb) python import sys; print(sys.version)
3.10.12 (...)
```

## 兩種啟動 Python 的方式

**單行**：

```
(gdb) python <expr>
```

**多行**（進 Python mode 直到 `end`）：

```
(gdb) python
> import gdb
> print(gdb.inferiors())
> print(gdb.selected_thread())
> end
```

**從檔案載入**：

```
(gdb) source myscript.py
```

**命令列**：

```
gdb -x myscript.py
```

## 第一個 Python command

存成 `hello.py`：

```python
import gdb

class HelloCmd(gdb.Command):
    """Say hello from GDB."""

    def __init__(self):
        super().__init__("hello", gdb.COMMAND_USER)

    def invoke(self, argument, from_tty):
        name = argument.strip() or "world"
        gdb.write(f"Hello, {name}!\n")

HelloCmd()
```

```
(gdb) source hello.py
(gdb) hello
Hello, world!
(gdb) hello Alice
Hello, Alice!
```

### 構造：每個 Python command 的骨架

1. 繼承 `gdb.Command`
2. `__init__` 裡 `super().__init__(name, class)` 註冊
3. 實作 `invoke(self, argument, from_tty)`
4. **在 module 底部 `實例化`** — 實例化那一刻 gdb 才會註冊

`name` 可以有階層：`"my info users"` 就是 `my info` 家族下的 `users` 子命令。

`class` 決定 gdb 的 `help` 分類：
- `gdb.COMMAND_USER` — 使用者定義
- `gdb.COMMAND_DATA` — 資料相關
- `gdb.COMMAND_STACK` — stack 操作
- `gdb.COMMAND_RUNNING` — 執行控制

其他在 gdb manual。

## 存取 inferior 狀態：`gdb.parse_and_eval`

這是 Python API 最常用的函式：

```python
v = gdb.parse_and_eval("n")              # 同 (gdb) p n
print(int(v))                             # 5

v = gdb.parse_and_eval("user_db->id")     # 複雜運算式都可以
print(int(v))
```

`v` 是 `gdb.Value` 物件，可以 `int()`、`float()`、`str()` 轉換，可以像 Python 物件一樣 `v['field']` 存取 struct field，也可以 `v[i]` 當陣列存取。

### 範例：印 linked list（Python 版）

```python
class PList(gdb.Command):
    """Print a linked list by walking 'next' pointers."""

    def __init__(self):
        super().__init__("plist", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        node = gdb.parse_and_eval(arg)
        i = 0
        while node != 0:
            id_val = int(node['id'])
            name = node['name'].string()
            gdb.write(f"[{i}] id={id_val} name={name}\n")
            node = node['next']
            i += 1

PList()
```

```
(gdb) plist user_db
[0] id=1042 name=Carol
[1] id=2 name=Bob
[2] id=1 name=Alice
```

比 Ch 14 的 `define plist` 乾淨 — 真正的迴圈、錯誤處理、字串操作都能做。

## Python Breakpoint

`gdb.Breakpoint` 是可以繼承的 class：

```python
class LogBreak(gdb.Breakpoint):
    def stop(self):
        req_id = int(gdb.parse_and_eval("req->id"))
        gdb.write(f"request {req_id}\n")
        return False           # False = 不要停，繼續

LogBreak("process_request")
```

`stop()` 會在斷點擊中時被呼叫。**return False** 表示「不要真的停下來」— 相當於 `silent` + `continue`。

這是 commands Ch 6 裡講過的功能的 Python 版，但可以寫任意邏輯：

```python
class ConditionalLog(gdb.Breakpoint):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.count = 0
        self.log = open("/tmp/gdb.log", "a")

    def stop(self):
        self.count += 1
        req_id = int(gdb.parse_and_eval("req->id"))
        if req_id > 1000:
            self.log.write(f"#{self.count} big request: {req_id}\n")
            self.log.flush()
        return False

ConditionalLog("process_request")
```

你有了 real Python：檔案 I/O、counter、條件分支、自己的 class state。

### internal breakpoint

讓斷點不出現在 `info break` 裡（完全透明）：

```python
LogBreak("process_request", internal=True)
```

這對「我想加 instrumentation 但不希望 user 看到」很有用。

## FinishBreakpoint：函式 return 時觸發

想知道某函式**什麼時候 return、return 什麼**：

```python
class MyFinish(gdb.FinishBreakpoint):
    def stop(self):
        gdb.write(f"returned: {self.return_value}\n")
        return True            # True = 停下來給 user

MyFinish()
```

執行 `MyFinish()` 時 gdb 自動選當前 frame 的 callee，在那個函式 return 時觸發。

## Watchpoint：`gdb.Breakpoint` 加 type 即可

```python
wp = gdb.Breakpoint("global_counter", type=gdb.BP_WATCHPOINT)
```

`BP_HARDWARE_WATCHPOINT` / `BP_READ_WATCHPOINT` / `BP_ACCESS_WATCHPOINT` 都有。

## `gdb.execute` — 執行任意 gdb command

Python 裡想跑 gdb command 直接：

```python
gdb.execute("info threads")
bt = gdb.execute("bt", to_string=True)    # 把輸出抓回來當字串
print(bt.split("\n")[0])
```

`to_string=True` 把 gdb 的輸出抓成 Python string 而不是直接印出。這讓你可以 parse gdb 的輸出做更多事（雖然有更直接的 API，下面會看到）。

## 走訪 inferior 結構：Inferior / Thread / Frame

```python
# 目前的 inferior
inf = gdb.selected_inferior()
print(inf.pid)

# 所有 inferiors
for inf in gdb.inferiors():
    print(inf.num, inf.pid, inf.progspace.filename)

# 目前 thread
th = gdb.selected_thread()
print(th.num, th.name, th.ptid)

# 所有 threads (current inferior)
for th in gdb.selected_inferior().threads():
    print(th.num, th.is_running(), th.is_stopped())

# 目前 frame
frame = gdb.selected_frame()
print(frame.name(), frame.function(), frame.pc())

# 走 frame
f = gdb.newest_frame()
while f is not None:
    print(f.name(), hex(f.pc()))
    f = f.older()              # 往外層
```

這些物件讓你不用去 parse `info threads` 的輸出 — 直接 iterate Python objects。

## 實戰範例：counter command

```python
import gdb
from collections import defaultdict

class CallCounter(gdb.Command):
    """Count function calls. Usage: count-calls FUNC [FUNC ...]"""

    def __init__(self):
        super().__init__("count-calls", gdb.COMMAND_USER)
        self._bps = []
        self._counts = defaultdict(int)

    def invoke(self, arg, from_tty):
        funcs = arg.split()
        for f in funcs:
            bp = _CounterBP(f, self._counts)
            self._bps.append(bp)
        gdb.write(f"Watching: {funcs}\n")

class _CounterBP(gdb.Breakpoint):
    def __init__(self, func, counts):
        super().__init__(func, internal=True)
        self.func = func
        self.counts = counts

    def stop(self):
        self.counts[self.func] += 1
        return False

CallCounter()

class CallReport(gdb.Command):
    """Show call counts collected by count-calls."""

    def __init__(self, counter):
        super().__init__("count-report", gdb.COMMAND_USER)
        self.counter = counter

    def invoke(self, arg, from_tty):
        for f, c in sorted(self.counter._counts.items(), key=lambda x: -x[1]):
            gdb.write(f"{c:>8} {f}\n")
```

使用：

```
(gdb) source counter.py
(gdb) count-calls square sum_of_squares
Watching: ['square', 'sum_of_squares']
(gdb) r
... 跑完 ...
(gdb) count-report
      15 square
       5 sum_of_squares
```

## Parameter 與 Convenience functions

讓 user 能在 `set` / `show` 調你的東西：

```python
class Verbose(gdb.Parameter):
    """Whether my_cmd is verbose."""

    set_doc = "Turn verbose output on/off."
    show_doc = "Show verbose setting."

    def __init__(self):
        super().__init__("my-verbose", gdb.COMMAND_USER, gdb.PARAM_BOOLEAN)
        self.value = False

verbose = Verbose()

# 在 command 裡讀：if verbose.value: ...
```

```
(gdb) set my-verbose on
(gdb) show my-verbose
Verbose setting is on.
```

**Convenience function**（`$foo(args)` 形式）：

```python
class Doubled(gdb.Function):
    """$doubled(x) returns x * 2."""

    def __init__(self):
        super().__init__("doubled")

    def invoke(self, x):
        return int(x) * 2

Doubled()
```

```
(gdb) p $doubled(21)
$1 = 42
```

## Event hooks

Python 可以訂閱 gdb 事件：

```python
def on_stop(event):
    if isinstance(event, gdb.BreakpointEvent):
        for bp in event.breakpoints:
            gdb.write(f"hit: {bp.location}\n")

gdb.events.stop.connect(on_stop)
```

其他事件：`cont`、`new_objfile`、`exited`、`thread_event` 等。

## 常見坑

1. **腳本 syntax error 但 gdb 不報**：`set python print-stack full` 讓 Python traceback 完整顯示。
2. **gdb.Value 的 truthiness**：`if v:` 可能不直觀。用 `int(v) != 0` 或 `v.is_optimized_out` 明確。
3. **優化掉的變數**：`gdb.parse_and_eval` 對 `<optimized out>` 會 throw `gdb.error`。try/except 一下。
4. **breakpoint 被 Python 保留**：Python object 被 GC 就 breakpoint 消失。把它存在 attribute / global list。
5. **多 inferior 時搞混**：`gdb.selected_inferior()` 可能不是你想的那個。顯式切換用 `gdb.execute("inferior 2")`。
6. **`gdb.execute("run", to_string=True)` 會 hang**：run 是 blocking 的。用 event hook 或非同步方式。
7. **script reload**：`source script.py` 重複跑會重複註冊同名 command（gdb 會 override）。自己管好 global state 避免重複斷點。

## 動手練習

1. 寫一個 `hello.py` 並 source 進 gdb。
2. 把 Ch 14 的 `plist` macro 改寫成 Python，能處理 list 為空、指標無效的情況。
3. 寫一個 `trace-args FUNC` 命令：對 FUNC 下 silent breakpoint，每次擊中印出所有 args 的值，然後 continue。
4. 寫一個 event hook，每次斷點命中時寫 log 到檔案，包含 timestamp、PC、thread id。
5. 用 `gdb.selected_frame` 寫一個自訂命令 `locals-json`：把當前 frame 的所有 local 變數以 JSON 格式輸出。
6. 寫一個 `FinishBreakpoint` 子類，追蹤某個函式的 return value 分布（count 不同的回傳值出現次數）。

## 自我檢核

- [ ] 我能寫 `gdb.Command` 子類註冊自訂命令
- [ ] 我能用 `gdb.parse_and_eval` 存取 inferior 變數
- [ ] 我能把 `gdb.Value` 當 struct / array / 指標操作
- [ ] 我能繼承 `gdb.Breakpoint` 做智慧斷點
- [ ] 我能用 `FinishBreakpoint` 捕捉 return value
- [ ] 我知道怎麼用 Inferior / Thread / Frame API 走訪狀態
- [ ] 我會用 `gdb.events` 訂閱事件

下一章講 Python API 的兩大殺招：pretty printers（讓複雜資料結構看得懂）跟 frame filters（讓 bt 輸出乾淨）。

→ [Ch 16 Python API（二）：pretty printers 與 frame filters](./16-python-api-pretty-printers.md)
