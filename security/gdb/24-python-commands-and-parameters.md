# Ch 24 — 自訂 Command 與 Parameter

> **目標**：用 `gdb.Command` 寫出有參數解析、Tab 補全、子命令（prefix command）的專業級自訂指令，用 `gdb.Parameter` 加可設定的選項（`set`/`show`）。學完你的插件指令會像 GDB 內建指令一樣好用——這是 gef/pwndbg 每個指令的骨架。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼要做「正規」的指令

Ch 22 你寫過一個 `gdb.Command`。但真正能用的指令需要：解析參數（不只 `arg` 一坨字串）、Tab 補全（提升體驗）、`help` 說明、子命令組織（`gef heap`、`gef stack` 這種）、可調的設定（`set` 開關）。這章把這些補齊。一個專業的指令和一個玩具指令，差別全在這。

## `gdb.Command` 的骨架

```python
import gdb

class HelloCmd(gdb.Command):
    """Say hello. Usage: hello [name]

    這個 docstring 會變成 `help hello` 的內容。
    """
    def __init__(self):
        super().__init__(
            "hello",                  # 指令名
            gdb.COMMAND_USER,         # 分類（影響 help 歸類）
            gdb.COMPLETE_NONE,        # 補全方式
        )

    def invoke(self, arg, from_tty):
        # arg: 使用者打的參數（單一字串）
        # from_tty: 是否從終端互動輸入（vs 腳本）
        name = arg.strip() or "world"
        gdb.write(f"hello, {name}\n")

HelloCmd()                            # 一定要實例化才會註冊！
```

三個重點：

1. **docstring = help**：第一行尤其重要（`help` 列表顯示）。
2. **`invoke(self, arg, from_tty)`**：核心邏輯。`arg` 是參數字串。
3. **必須實例化**（`HelloCmd()`）——只定義 class 不會註冊。

## 指令分類（COMMAND_*）

第二個參數決定 `help` 把指令歸到哪類：

| 常數 | 用途 |
|---|---|
| `gdb.COMMAND_USER` | 自訂指令（最常用） |
| `gdb.COMMAND_RUNNING` | 執行控制類 |
| `gdb.COMMAND_DATA` | 資料檢視類 |
| `gdb.COMMAND_STACK` | stack 類 |
| `gdb.COMMAND_BREAKPOINTS` | 斷點類 |
| `gdb.COMMAND_SUPPORT` | 雜項支援 |

選對分類，`help user` / `help data` 就能找到你的指令。

## 解析參數：`gdb.string_to_argv`

`arg` 是一坨字串，要自己拆。GDB 提供 shell-like 的拆分：

```python
def invoke(self, arg, from_tty):
    argv = gdb.string_to_argv(arg)    # 像 shell 一樣拆（處理引號）
    if len(argv) < 1:
        raise gdb.GdbError("usage: mycmd <addr> [count]")   # 正規的錯誤！
    addr = gdb.parse_and_eval(argv[0])
    count = int(argv[1]) if len(argv) > 1 else 8
    ...
```

兩個重點：

- `gdb.string_to_argv` 處理引號、空白，比自己 `arg.split()` 健壯。
- `raise gdb.GdbError("...")` 是回報使用者錯誤的正規方式——它只印錯誤訊息，**不**印 Python traceback（不像一般 exception 會嚇到使用者）。用法錯誤一律用 `GdbError`。

## Tab 補全

補全大幅提升指令好用度。第三個建構參數設補全類型：

```python
class BreakFuncCmd(gdb.Command):
    def __init__(self):
        super().__init__("bf", gdb.COMMAND_BREAKPOINTS,
                         gdb.COMPLETE_SYMBOL)    # Tab 補全函式/符號名！
    def invoke(self, arg, from_tty):
        gdb.execute(f"break {arg}")
```

內建補全類型：

| 常數 | 補全什麼 |
|---|---|
| `gdb.COMPLETE_NONE` | 不補全 |
| `gdb.COMPLETE_SYMBOL` | 符號名（函式/變數） |
| `gdb.COMPLETE_FILENAME` | 檔名 |
| `gdb.COMPLETE_LOCATION` | location（檔案:行、函式） |
| `gdb.COMPLETE_COMMAND` | GDB 指令名 |
| `gdb.COMPLETE_EXPRESSION` | 表示式 |

### 自訂補全

要動態補全（例如補全自己管理的一組名稱），覆寫 `complete`：

```python
class ThemeCmd(gdb.Command):
    THEMES = ["dark", "light", "solarized"]
    def __init__(self):
        super().__init__("theme", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        ...
    def complete(self, text, word):
        # 回傳符合 word 開頭的候選 list
        return [t for t in self.THEMES if t.startswith(word)]
```

## prefix command：子命令組織

gef 有 `gef heap`、`gef config`…這種子命令結構。做法是先建一個 **prefix command**，再把子命令掛在它下面：

```python
import gdb

class MyTool(gdb.Command):
    """My debugging toolkit. Subcommands: stack, heap, regs."""
    def __init__(self):
        super().__init__("mytool", gdb.COMMAND_USER,
                         prefix=True)            # ← 關鍵：這是 prefix
    def invoke(self, arg, from_tty):
        gdb.write("use: mytool <stack|heap|regs>\n")

class MyToolStack(gdb.Command):
    """mytool stack — show the stack."""
    def __init__(self):
        super().__init__("mytool stack", gdb.COMMAND_USER)   # 名字含空格 = 子命令
    def invoke(self, arg, from_tty):
        gdb.execute("x/8gx $sp")

class MyToolRegs(gdb.Command):
    """mytool regs — show registers."""
    def __init__(self):
        super().__init__("mytool regs", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        gdb.execute("info registers")

MyTool()
MyToolStack()
MyToolRegs()
```

```
(gdb) mytool stack
(gdb) mytool regs
(gdb) mytool <Tab>          # 補全出 stack / regs
```

`prefix=True` 建立命名空間，子命令名字含空格（`"mytool stack"`）。這是組織大型插件（幾十個指令）的標準做法，Final Project 你會用它把所有功能歸到一個前綴下。

## `gdb.Parameter`：可設定的選項

讓你的插件有 `set`/`show` 開關（例如「context 要不要上色」）：

```python
import gdb

class ColorParam(gdb.Parameter):
    """Whether to colorize output."""
    def __init__(self):
        super().__init__("mytool-color",          # set mytool-color on/off
                         gdb.COMMAND_USER,
                         gdb.PARAM_BOOLEAN)
        self.value = True
    def get_set_string(self):
        return f"mytool color set to {self.value}"
    def get_show_string(self, svalue):
        return f"mytool color is {svalue}"

color = ColorParam()
```

```
(gdb) set mytool-color off
mytool color set to False
(gdb) show mytool-color
mytool color is off
(gdb) python print(color.value)        # 在程式裡讀設定
False
```

參數型別：`PARAM_BOOLEAN`、`PARAM_INTEGER`、`PARAM_STRING`、`PARAM_ENUM`、`PARAM_ZINTEGER` 等。插件用它做使用者可調的設定（顏色、顯示行數、功能開關），gef 的 `gef config` 就是一堆 Parameter。

## 一個完整的實用指令：telescope

把這章串起來——一個有參數、補全、會解讀指標的 telescope（Final Project context 的核心）：

```python
# telescope.py
import gdb

class Telescope(gdb.Command):
    """telescope <addr> [count] — dump memory, resolving pointers/symbols."""
    def __init__(self):
        super().__init__("tel", gdb.COMMAND_DATA, gdb.COMPLETE_EXPRESSION)
    def invoke(self, arg, from_tty):
        argv = gdb.string_to_argv(arg)
        if not argv:
            raise gdb.GdbError("usage: tel <addr> [count]")
        base = int(gdb.parse_and_eval(argv[0]))
        count = int(argv[1]) if len(argv) > 1 else 8
        inf = gdb.selected_inferior()
        ptr_t = gdb.lookup_type("unsigned long")
        for i in range(count):
            addr = base + i * 8
            try:
                raw = inf.read_memory(addr, 8)
                val = int.from_bytes(raw, "little")
            except gdb.MemoryError:
                gdb.write(f"{addr:#018x}: <unmapped>\n"); continue
            # 嘗試把值解讀成符號
            annotation = self.symbolize(val)
            gdb.write(f"{addr:#018x}|+{i*8:04x}: {val:#018x}  {annotation}\n")
    def symbolize(self, val):
        try:
            s = gdb.execute(f"info symbol {val:#x}", to_string=True).strip()
            if "No symbol" not in s:
                return f"<{s.split(' in ')[0]}>"
        except gdb.error:
            pass
        return ""
Telescope()
```

```
(gdb) tel $sp 6
0x7fffffffe2a0|+0000: 0x00007ffff7da1234  <__libc_start_main+128>
0x7fffffffe2a8|+0008: 0x0000000000000000  
...
```

這就是 gef telescope 的雛形——讀記憶體、解讀指標成符號。Final Project 會加上「遞迴跟隨指標」「上色」「區分 stack/heap/code」。

## 踩雷集錦

1. **定義了 class 但忘記實例化**：`class FooCmd(...)` 後沒有 `FooCmd()`，指令不會出現。
2. **重複 source 導致重複註冊**：再次 `source` 會再實例化，GDB 可能警告或行為怪。開發時用 `try: ... except`，或檢查指令是否已存在。
3. **用一般 Exception 報使用者錯誤**：會噴 Python traceback 嚇人。使用者用法錯誤一律 `raise gdb.GdbError(...)`。
4. **`arg.split()` 而非 `string_to_argv`**：自己 split 不處理引號，`mytool "a b"` 會拆錯。
5. **prefix command 的子命令名字打錯**：子命令名必須是 `"prefix sub"`（含空格、完全對應），否則掛不上。
6. **`complete` 回傳非 list**：要回傳字串 list（或 COMPLETE_* 常數）。回傳 None 會無補全。
7. **Parameter 的 `value` 沒初始化**：`__init__` 裡要設 `self.value`，否則 show 出錯。

## 進階：再往深一層

- **`gdb.COMPLETE_EXPRESSION` + 自訂 complete 混用**：先解析已輸入的部分再決定補全候選，做 context-aware 補全。
- **指令別名**：`gdb.execute("alias t = tel")` 或用 `class` 註冊多個名字。
- **`dont_repeat()`**：在 invoke 裡呼叫 `self.dont_repeat()`，讓使用者按 Enter 不重複執行（對有副作用的指令重要）。
- **PARAM_ENUM**：限定值域的參數（如 `set mytool-style {compact,full}`），配補全很專業。
- **指令回傳值給其他指令**：透過 convenience variable（`gdb.set_convenience_variable`）把結果傳出去，讓指令可組合。
- **整合 argparse**：複雜參數可在 invoke 裡用 Python 的 `argparse` 解析 `string_to_argv` 的結果——gef 大量這樣做，支援 `-h`、flags、選項。

## 動手練習

1. 寫一個 `hello [name]` 指令，無參數時印 "hello world"。
2. 寫一個 `bf <func>` 指令，用 `COMPLETE_SYMBOL` 補全函式名，內部 `break`。
3. 做一個 `mytool` prefix command，掛上 `mytool stack`、`mytool regs` 兩個子命令，測試 Tab 補全。
4. 加一個 `gdb.Parameter`（`mytool-count`，整數），讓 `mytool stack` 讀它決定印幾行。
5. source 本章的 `telescope.py`，對 `$sp` 跑 `tel`，看它解讀出符號。
6. 故意用法錯誤，比較 `raise Exception(...)`（噴 traceback）vs `raise gdb.GdbError(...)`（乾淨）的差別。

## 本章重點整理

- `gdb.Command`：docstring=help、`invoke(self, arg, from_tty)`=邏輯、必須實例化才註冊。
- 參數解析用 `gdb.string_to_argv`（處理引號）；使用者錯誤用 `raise gdb.GdbError`（不噴 traceback）。
- 補全：建構參數設 `COMPLETE_SYMBOL/FILENAME/...`，或覆寫 `complete()` 自訂。
- prefix command（`prefix=True` + 子命令名含空格）組織大型插件的命名空間（gef 模式）。
- `gdb.Parameter` 提供 `set`/`show` 可調選項（顏色、行數、開關）。

## 自我檢核

- [ ] `gdb.Command` 的三要素是什麼？為什麼一定要實例化？
- [ ] 怎麼解析帶引號的參數？使用者用法錯誤該用什麼回報、為什麼？
- [ ] 怎麼讓指令支援 Tab 補全函式名？怎麼自訂補全？
- [ ] gef 的 `gef heap` 這種子命令結構怎麼實作？
- [ ] 怎麼給插件加一個 `set` 可調的開關？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Commands In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Commands-In-Python.html)** 與 **[Parameters In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Parameters-In-Python.html)**
  - **讀哪裡**：Command 的建構參數、COMPLETE_/COMMAND_ 常數、Parameter 的型別與 get_set/show_string。
  - **和本章的關聯**：本章核心的完整參考。

### 原始碼

- **[gef 的 GenericCommand 基底類別](https://github.com/hugsy/gef)**
  - **讀哪裡**：搜 `class GenericCommand`，看它怎麼用 argparse 包裝 invoke、怎麼做 prefix。
  - **和本章的關聯**：本章每個概念的工業級實作；Final Project 可直接借鏡架構。

下一章把指令的觸發從「使用者打字」變成「程式事件」：用 Python 控制 breakpoint、掛 event handler，做出會自動反應的工具。

→ [Ch 25 程式化 Breakpoint 與事件](./25-python-breakpoints-and-events.md)
