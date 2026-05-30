# Ch 22 — Python API 入門

> **目標**：進入這門課的重頭戲。理解 GDB 內嵌 Python 的架構、`gdb` 模組的全貌、`gdb.execute` / `gdb.parse_and_eval` 兩大入口，以及命令語言（Part 4）與 Python 的橋接。學完你能在 GDB 裡跑 Python、讀寫 inferior、為後面六章（自訂指令、breakpoint、pretty-printer、unwinder、Final Project）打好地基。

> **環境**：GDB 13/14（內嵌 Python 3.8+），Linux x86_64。確認 `gdb --config | grep -i python` 或 `python print("ok")` 可用。

## 為什麼 Python API 是這門課的轉捩點

Part 1–4 你學會「用 GDB」。從這章起，你學「**改 GDB**」。

gef、pwndbg、Voltron、gdb-dashboard——所有讓你羨慕的神級工具，本質都是 GDB Python API 的應用。它們沒有任何 GDB 沒給的超能力，只是把 API 用到極致。學完 Part 5，你看它們的原始碼會發現「啊，這我也會寫」——然後 Final Project 你就真的寫一個出來。

Python API 把 GDB 從「除錯器」變成「可程式化的除錯平台」。這是「會用」和「能改」的分水嶺。

## 先建立直覺：Python 跑在 GDB 內部

關鍵心智模型：**Python 直譯器是內嵌在 GDB process 裡的**，不是外部呼叫。

```
   ┌─────────────────────────────────────────┐
   │            GDB process                    │
   │  ┌─────────────┐    ┌──────────────────┐ │
   │  │ GDB 核心    │◄──►│ 內嵌 Python 直譯器│ │
   │  │ (C++)       │    │  import gdb       │ │
   │  └──────┬──────┘    └──────────────────┘ │
   │         │ ptrace                          │
   └─────────┼─────────────────────────────────┘
             ▼
        inferior（被 debug 的程式）
```

Python 透過 `import gdb` 這個模組和 GDB 核心溝通。你在 Python 裡 `gdb.parse_and_eval("x")` 就等於在命令列 `print x`——同一個求值器，只是回傳一個 Python 物件讓你程式化處理。

## 三種跑 Python 的方式

```
# 方式 1：單行內嵌
(gdb) python print("hello from python")
hello from python

# 方式 2：多行區塊（end 結束）
(gdb) python
>import gdb
>for i in range(3):
>    print("line", i)
>end

# 方式 3：載入腳本檔（最常用於開發）
(gdb) source myscript.py
```

開發插件時用方式 3：把程式碼寫在 `.py` 檔，`source` 載入，改了重新 `source`。`.gdbinit` 裡 `source` 它就能每次啟動自動載入（Ch 19）。

## `gdb.execute`：執行任何 GDB 命令

最簡單的橋接——在 Python 裡執行 GDB 命令字串：

```python
import gdb

gdb.execute("break main")
gdb.execute("run")
gdb.execute("info registers")
```

更有用的是把命令輸出**抓成字串**（`to_string=True`）：

```python
out = gdb.execute("info registers rax", to_string=True)
print("captured:", out)          # 拿到輸出去做字串處理
```

`gdb.execute(..., to_string=True)` 是「我懶得用結構化 API，先抓文字 parse」的捷徑。但要注意——**靠 parse 文字輸出很脆弱**（GDB 輸出格式可能變、locale 影響）。能用結構化 API（下面的 `parse_and_eval`、Value 物件）就別 parse 文字。gef/pwndbg 早期大量 parse 文字，後來逐步改用結構化 API，就是吃過這個虧。

## `gdb.parse_and_eval`：求值並拿到 Value 物件

這是 Python API 的核心入口。它求值一個表示式（和 `print` 同一個求值器），回傳一個 **`gdb.Value`** 物件——你可以在 Python 裡操作它：

```python
import gdb

v = gdb.parse_and_eval("global_counter")
print(int(v))                    # gdb.Value → Python int
print(v.type)                    # 它的型別

pc = gdb.parse_and_eval("$pc")   # 暫存器也行
print(hex(int(pc)))

node = gdb.parse_and_eval("head")     # 一個指標
print(int(node["val"]))               # 解參取欄位：node->val
print(int(node["next"]["val"]))       # 串接：node->next->val
```

`gdb.Value` 是你和 inferior 記憶體互動的主要物件——它代表「inferior 裡的一個值」，懂型別、能解參、能取欄位、能做算術。Ch 23 整章專講它。

對比命令語言（Part 4）的 `set $node = $node->next`，Python 版是 `node = node["next"]`——但 Python 給你完整的程式語言：list、dict、字串、函式、class、檔案 I/O、外部函式庫。這就是為什麼複雜工具都用 Python。

## `gdb` 模組的全貌

`gdb` 模組提供的東西，後面六章會逐一深入，先建立地圖：

| 類別 | 重點成員 | 章節 |
|---|---|---|
| 求值/執行 | `execute`、`parse_and_eval` | 本章 |
| 值與型別 | `Value`、`Type`、`lookup_type`、`Symbol`、`lookup_symbol` | Ch 23 |
| 自訂指令 | `Command`、`Parameter` | Ch 24 |
| 斷點/事件 | `Breakpoint`、`events`、`FinishBreakpoint` | Ch 25 |
| Pretty-printer | `printing`、`pretty_printers` | Ch 26 |
| Frame | `Frame`、`selected_frame`、`newest_frame`、frame filter | Ch 27 |
| Unwinder/Xmethod/TUI | `unwinder`、`xmethod`、`TuiWindow` | Ch 27, 28 |
| Inferior/記憶體 | `inferiors`、`selected_inferior()`、`Inferior.read_memory` | 本章 |
| Convenience | `convenience_variable`、`register_convenience_function` | Ch 28 |

## 直接讀寫 inferior 記憶體

不透過表示式，直接讀一塊記憶體（pretty-printer、heap 分析必備）：

```python
import gdb

inf = gdb.selected_inferior()
addr = int(gdb.parse_and_eval("$sp"))

# 讀 64 bytes（回傳 Python bytes/memoryview）
buf = inf.read_memory(addr, 64)
print(bytes(buf).hex())

# 寫記憶體
inf.write_memory(addr, b"\x90\x90")   # patch 兩個 NOP

# 搜尋記憶體
# inf.search_memory(start, length, pattern)
```

`read_memory` / `write_memory` 是你做 stack telescope、heap chunk 解析（Final Project）的底層工具。比 `gdb.execute("x/...")` parse 文字乾淨太多。

## 一個迷你完整範例

把這章串起來——一個「印出當前所有暫存器（hex）」的 Python 片段：

```python
# regs.py — source 它，然後跑 myregs
import gdb

def show_regs():
    frame = gdb.selected_frame()
    for reg in ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "rip"]:
        val = frame.read_register(reg)        # 結構化讀暫存器，不 parse 文字
        print(f"{reg:>3} = {int(val):#018x}")

class MyRegs(gdb.Command):
    """Show GP registers in hex."""
    def __init__(self):
        super().__init__("myregs", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        show_regs()

MyRegs()   # 註冊指令
```

```
(gdb) source regs.py
(gdb) break main
(gdb) run
(gdb) myregs
rax = 0x0000000000000000
rbx = 0x00007fffffffe3d8
...
```

你剛剛寫了第一個 GDB 插件指令。`frame.read_register`（Ch 23/27）是結構化讀暫存器、`gdb.Command`（Ch 24）是自訂指令——這兩個你接下來會用無數次。

## 踩雷集錦

1. **GDB 沒編 Python 支援**：`python ...` 報 "not supported"。需要 `--with-python` 編譯的 GDB（Ch 0）。distro 版通常有。
2. **過度依賴 `execute(to_string=True)` parse 文字**：脆弱、慢、受 locale/版本影響。能用 `parse_and_eval` / Value / `read_memory` 就別 parse 文字。
3. **`gdb.Value` 不是 Python int**：要 `int(v)` 轉換才能做 Python 運算；直接 `v + 1` 是 Value 的算術（在 inferior 型別語意下），未必是你要的。
4. **在沒有 inferior 時呼叫 frame API**：`gdb.selected_frame()` 在程式還沒 run 時拋例外。要判斷 `gdb.selected_inferior().pid != 0` 或包 try/except。
5. **Python 例外讓指令默默失敗**：`set python print-stack full`（Ch 19）才看得到完整 traceback，否則只看到一行模糊錯誤。
6. **改了 `.py` 沒重新 source**：和命令語言一樣，改完要重新 `source`（或重開 GDB）。注意重複 `source` 可能重複註冊指令/printer。

## 進階：再往深一層

- **`gdb.Architecture`**：`frame.architecture()` 拿到架構物件，查暫存器群組、反組譯——寫跨架構工具時用。
- **`gdb.lookup_global_symbol` / `lookup_symbol`**：用名字查符號拿到 `Symbol` 物件（Ch 23），比 parse 文字可靠。
- **`gdb.post_event`**：把一個 callable 排到 GDB 的事件迴圈執行，做非同步/延遲動作。
- **`gdb.write` / `gdb.flush`**：用 GDB 的輸出通道印東西（會進 logging、TUI 正確處理），比 `print` 更「正規」。
- **錯誤型別**：`gdb.error`、`gdb.MemoryError`（讀到無效記憶體）——寫健壯插件要 catch 這些（讀壞指標很常見）。
- **`gdb.selected_inferior().architecture()` / `.threads()`**：列舉 thread、查架構，做多執行緒感知的工具。

## 動手練習

1. 確認你的 GDB 有 Python：`python import sys; print(sys.version)`。
2. `python print(int(gdb.parse_and_eval("1+2*3")))`——用 GDB 當計算機，體會 parse_and_eval 回傳 Value。
3. 對一個有全域變數的程式，`python v = gdb.parse_and_eval("global_var"); print(int(v), v.type)`。
4. 寫並 source 本章的 `regs.py`，跑 `myregs`。
5. 用 `gdb.selected_inferior().read_memory($sp, 32)` 讀 stack 頂 32 bytes，`bytes(...).hex()` 印出。
6. 故意在 Python 裡製造例外（`gdb.parse_and_eval("nonexistent")`），先看模糊錯誤，再 `set python print-stack full` 看完整 traceback。

## 本章重點整理

- Python 直譯器內嵌在 GDB process 裡，透過 `import gdb` 與核心溝通；gef/pwndbg 全建在此。
- 三種跑法：`python` 單行、`python ... end` 區塊、`source file.py`（開發用）。
- 兩大入口：`gdb.execute`（執行命令，可 `to_string` 抓輸出）、`gdb.parse_and_eval`（求值回傳 `gdb.Value`）。
- 優先用結構化 API（parse_and_eval、Value、read_memory、read_register），別 parse 文字輸出（脆弱）。
- `gdb.Value` 是與 inferior 記憶體互動的核心物件（Ch 23 詳述）。

## 自我檢核

- [ ] Python 在 GDB 裡是外部呼叫還是內嵌？透過什麼模組溝通？
- [ ] `gdb.execute` 和 `gdb.parse_and_eval` 各回傳什麼？何時用哪個？
- [ ] 為什麼說「parse 文字輸出」是壞習慣？有哪些結構化替代？
- [ ] `gdb.Value` 怎麼轉成 Python int？直接對它做 `+` 有什麼風險？
- [ ] 看不到 Python 完整錯誤訊息時，要設什麼？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Python API](https://sourceware.org/gdb/current/onlinedocs/gdb/Python-API.html)**
  - **讀哪裡**：Basic Python（execute、parse_and_eval、write）、Inferiors In Python（read/write_memory）。
  - **和本章的關聯**：整個 Part 5 的權威；本章是它的導覽。後面每章對應其中一節。

- **[GDB Manual: Python — Values From Inferior](https://sourceware.org/gdb/current/onlinedocs/gdb/Values-From-Inferior.html)**
  - **讀哪裡**：gdb.Value 概覽（Ch 23 會深入）。

### 部落格 / 文章

- **[GDB Python API tutorial](https://developers.redhat.com/blog/2017/11/10/gdb-python-api)** — Red Hat Developers
  - **這篇說什麼**：用實例帶 Python API 的常見用法。
  - **為什麼值得讀**：官方 manual 偏 reference，這篇偏 tutorial，互補。

### 原始碼

- **[gef 的入口架構](https://github.com/hugsy/gef/blob/main/gef.py)**
  - **讀哪裡**：檔案開頭的 import gdb 與整體結構（先別細看指令）。
  - **和本章的關聯**：看一個真實插件怎麼起手；Final Project 標竿。

下一章深入 Python API 最核心的物件：`gdb.Value` 與 `Type`/`Symbol`/`Frame`——它們是你讀懂 inferior 一切資料的鑰匙。

→ [Ch 23 Value / Type / Symbol / Frame 物件模型](./23-value-type-symbol-frame.md)
