# Ch 28 — Xmethod、彩色輸出、Python TUI window

> **目標**：補完 Python API 的最後幾塊——xmethod（讓 `print obj.method()` / `obj[i]` 用 Python 算而非真的呼叫）、convenience function（自訂 `$_foo()`）、彩色輸出、以及 Python TUI window（寫完全自訂的視窗）。學完你手上的 Python API 武器庫就齊全了，可以動手做 Final Project 的完整插件。

> **環境**：GDB 13/14，Linux x86_64。Python TUI window 需要 GDB 10+。

## 為什麼還需要這些

前面六章你會了指令、斷點、事件、printer、frame filter。這章補三個常被需要的進階能力：

1. **xmethod**：對最佳化過或 inferior call 危險的情境，`print vec[5]` 不真的呼叫 `operator[]`（可能崩、可能有副作用、可能 inline 掉了），而用 Python 直接算出來。
2. **彩色輸出 + convenience function**：讓你的工具好看、可組合。
3. **Python TUI window**：寫一個完全自訂的視窗——顯示 heap、自訂 telescope、任何東西。這是 Final Project 「視覺化」的頂點。

## Xmethod：用 Python 取代成員方法

C++ debug 時，`print myvec.at(3)` 或 `print myvec.size()` 會 inferior call（Ch 8）——真的在 inferior 裡呼叫該方法。問題：

- 方法可能被 inline 掉了（最佳化），根本沒得呼叫
- 呼叫有副作用、可能崩、core dump 不能呼叫
- 太慢

**xmethod** 讓你用 Python「模擬」這個方法：GDB 攔截 `print vec.size()`，改用你的 Python 邏輯算（直接讀 `_M_finish - _M_start`），不真的呼叫。

```python
# xm_demo.py — 為 IntVec 提供 size() 的 xmethod
import gdb
import gdb.xmethod

class IntVecSizeWorker(gdb.xmethod.XMethodWorker):
    def get_arg_types(self):
        return None                          # 無參數
    def get_result_type(self, obj):
        return gdb.lookup_type("int")
    def __call__(self, obj):
        # obj 是那個 IntVec value；直接讀欄位算，不呼叫真的方法
        return obj["size"]

class IntVecSizeMatcher(gdb.xmethod.XMethodMatcher):
    def __init__(self):
        super().__init__("IntVecSize")
    def match(self, class_type, method_name):
        if method_name == "size" and class_type.tag == "IntVec":
            return IntVecSizeWorker()
        return None

gdb.xmethod.register_xmethod_matcher(None, IntVecSizeMatcher())
```

```
(gdb) print v.size()        # 不真的呼叫，用 Python 算
$1 = 3
```

libstdc++ 為 `std::vector::size()`、`operator[]`、`std::shared_ptr::get()` 等都提供 xmethod，所以你在最佳化的 C++ 程式裡仍能 `print vec[3]`——這就是它在背後工作。寫自訂容器的 xmethod，能讓 debug 體驗和 STL 一樣好（Ch 30、練習 F）。

> xmethod vs pretty-printer：printer 管「整個物件怎麼**顯示**」（`print vec` → `{1,2,3}`）；xmethod 管「物件的**方法/索引**怎麼算」（`print vec[5]`、`print vec.size()`）。兩者互補，常一起為一個型別提供。

## Convenience function：自訂 `$_foo()`

承 Ch 8 的 `$_strlen()` 等內建 convenience function，你可以自訂——做出能在「表示式/條件斷點」裡用的函式：

```python
# cfunc_demo.py
import gdb

class ListLen(gdb.Function):
    """$_listlen(head) — length of a linked list."""
    def __init__(self):
        super().__init__("_listlen")
    def invoke(self, head):
        n = 0
        node = head
        while int(node) != 0:
            n += 1
            node = node["next"]
            if n > 100000: break             # 防環
        return n

ListLen()
```

```
(gdb) print $_listlen(head)
$1 = 3
(gdb) break process if $_listlen(mylist) > 50    # 在條件斷點裡用！
```

convenience function 最大價值：能用在**條件斷點**和**表示式**裡。命令語言/Python 的複雜判斷，包成 `$_foo()` 就能塞進 `break ... if $_foo(...)`——把 Python 的力量注入條件斷點。

## 彩色輸出

讓工具好看（gef 的紅綠藍）。GDB 沒有專屬的顏色 API，直接用 ANSI escape code：

```python
import gdb

class Color:
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; BOLD = "\033[1m"; RESET = "\033[0m"

def colorize(text, color):
    return f"{color}{text}{Color.RESET}"

gdb.write(colorize("registers", Color.BOLD + Color.BLUE) + "\n")
gdb.write(f"rax = {colorize(hex(0x1234), Color.GREEN)}\n")
```

進階做法：依值的「種類」上色——指標藍色、stack 位址綠色、code 位址紅色、字串黃色。這需要判斷一個值是什麼（查 `info proc mappings` 看它落在哪段），是 gef telescope 的招牌，Final Project 會做。

> 注意：彩色輸出在重導向到檔案（`set logging`）或不支援 ANSI 的環境會變成亂碼 escape。健壯的插件會偵測 `sys.stdout.isatty()` 或提供 `set mytool-color off`（Ch 24 的 Parameter）。GDB 14+ 也有內建的 `set style` 系統可參考。

## Python TUI window：完全自訂的視窗

GDB 10+ 讓你用 Python 寫**自訂 TUI 視窗**——顯示任何你要的內容（heap 摘要、自訂 telescope、watch 清單）。這是 Final Project 視覺化的頂點。

```python
# tuiwin_demo.py — 一個顯示自訂內容的 TUI 視窗
import gdb

class HeapWindow:
    def __init__(self, tui_window):
        self.win = tui_window
        self.win.title = "heap-summary"
    def render(self):
        # 每次需要重繪時被呼叫
        self.win.erase()
        width = self.win.width
        try:
            frame = gdb.selected_frame()
            self.win.write(f"PC: {hex(int(frame.pc()))}\n")
            self.win.write(f"SP: {hex(int(frame.read_register('rsp')))}\n")
            # 這裡可以放 heap chunk 摘要、watch 變數等
        except gdb.error:
            self.win.write("no inferior\n")
    def close(self):
        pass

# 註冊一個 TUI window factory
gdb.register_window_type("heap", lambda w: HeapWindow(w))
```

```
(gdb) tui new-layout myheap src 2 heap 1 cmd 1   # 把自訂視窗放進 layout
(gdb) layout myheap
```

`register_window_type("名字", factory)` 註冊一個視窗類型，factory 收到 `TuiWindow` 物件。視窗類別要有 `render()`（重繪）、`close()`，可選 `hscroll`/`vscroll`/`click`。然後用 `tui new-layout`（Ch 18）把它放進畫面。

這讓你的插件能有 gef 那種「固定顯示自訂資訊」的視窗，而非只是 print。Final Project 的 context 可以選擇用這個（固定視窗）或用 events.stop 印出（往下捲）——兩種風格。

## 把武器庫整合：Final Project 預覽

到這裡你的 Python API 武器庫齊全了。Final Project 的插件會用到：

| 功能 | 用到的 API | 章 |
|---|---|---|
| `myplugin` 主指令 + 子命令 | Command + prefix | Ch 24 |
| 設定開關（顏色/行數） | Parameter | Ch 24 |
| 停下來自動顯示 context | events.stop | Ch 25 |
| heap chunk 解析 | Value.cast + read_memory | Ch 23 |
| telescope（解讀指標） | read_memory + symbolize + 彩色 | Ch 23, 28 |
| 漂亮 backtrace | frame filter | Ch 27 |
| 容器漂亮顯示 | pretty-printer | Ch 26 |
| 在條件斷點用的判斷 | convenience function | Ch 28 |
| 固定資訊視窗（選配） | TUI window | Ch 28 |

每一塊你都學過了。練習 E 先做一個小型整合，Final Project 做完整版。

## 踩雷集錦

1. **xmethod 與 inferior call 混淆**：有 xmethod 時 `print vec.size()` 不呼叫真方法。如果你「想」呼叫真方法（測副作用），xmethod 反而擋路——可 `disable xmethod`。
2. **xmethod matcher 沒 match 到**：`class_type.tag` 比對要精確，template 型別要處理（Ch 30）。
3. **convenience function 在條件斷點每次命中都跑**：和條件斷點一樣有效能成本（Ch 12），熱點上小心。
4. **彩色輸出在 logging/pipe 變亂碼**：偵測 isatty 或給開關。
5. **TUI window 的 render 拋例外**：會讓視窗壞掉或 GDB 報錯。包 try/except。
6. **TUI window 在非 TUI 模式無效**：`register_window_type` 註冊了，但要 `tui enable` + 放進 layout 才看得到。
7. **render 裡做重量級計算**：render 可能頻繁被呼叫，別在裡面做慢操作（大量 read_memory），會卡頓。

## 進階：再往深一層

- **xmethod 的 `operator[]` / `operator*`**：為自訂智慧指標、容器提供索引/解參，debug 體驗等同 STL（練習 F）。
- **GDB 14+ 的 `set style` 系統**：內建的語意化上色（address/function/string 各有 style），比手刻 ANSI 更整合，且自動處理 isatty。
- **TUI window 的互動**：覆寫 `click(x, y, button)`（滑鼠）、`hscroll`/`vscroll`（捲動），做可互動視窗。
- **`gdb.Color`（GDB 14+）**：較新版本提供顏色物件，整合 style 系統。
- **convenience function 回傳 Value**：可回傳結構/指標，讓它在更複雜的表示式裡組合。
- **多視窗 dashboard**：註冊多個 window type（regs/stack/heap/backtrace），用 `tui new-layout` 拼成完整 dashboard——gef/gdb-dashboard 的進階形態。

## 動手練習

1. 為 `pp_demo.c` 的 IntVec 寫 `size()` 與 `operator[]` 的 xmethod，`print v.size()`、`print v[1]` 不呼叫真方法。
2. 寫 `$_listlen(head)` convenience function，用在 `print $_listlen(head)` 和條件斷點 `break ... if $_listlen(mylist) > 2`。
3. 寫一個 `colorize` 工具，做一個會「指標藍、數字綠」的暫存器顯示指令。
4. （需 TUI）寫一個 Python TUI window 顯示 `$pc`/`$sp` 與 backtrace 前三層，用 `tui new-layout` 放進畫面。
5. 偵測 `sys.stdout.isatty()`，讓彩色輸出在 pipe 時自動關閉。
6. 對照：裝 gef，找出它哪些功能用 xmethod、哪些用 convenience function、它的 context 是用 TUI window 還是 events.stop 印出。

## 本章重點整理

- xmethod 用 Python 取代成員方法/索引（`print vec[5]`/`vec.size()`），避免 inferior call 的崩潰/副作用/inline 問題；與 pretty-printer 互補。
- convenience function（`gdb.Function` → `$_foo()`）能用在**條件斷點與表示式**裡，把 Python 力量注入條件。
- 彩色輸出用 ANSI escape；健壯做法偵測 isatty 或給開關；GDB 14+ 有 `set style` 系統。
- Python TUI window（`register_window_type` + `render()`）做完全自訂視窗——Final Project 視覺化頂點。
- 到這裡 Python API 武器庫齊全，可動手做完整插件。

## 自我檢核

- [ ] xmethod 和 pretty-printer 各管什麼？為什麼最佳化的 C++ 需要 xmethod？
- [ ] convenience function 最大的價值是能用在哪裡？
- [ ] 彩色輸出怎麼避免在 pipe/logging 變亂碼？
- [ ] Python TUI window 怎麼註冊、怎麼放進畫面？
- [ ] 一個完整 gef 式插件會用到本 Part 哪些 API？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Xmethods In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Xmethods-In-Python.html)**、**[Xmethod API](https://sourceware.org/gdb/current/onlinedocs/gdb/Xmethod-API.html)**、**[Functions In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Functions-In-Python.html)**、**[TUI Windows In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/TUI-Windows-In-Python.html)**
  - **讀哪裡**：四節對應本章四主題；TUI window 那節的 render/erase/write/scroll API 是 Final Project 必備。
  - **和本章的關聯**：本章核心的完整權威。

### 原始碼

- **[libstdc++ 的 xmethods](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/python/libstdcxx/v6/xmethods.py)**
  - **讀哪裡**：`class VectorWorkerBase`、`operator[]` 的 worker。
  - **和本章的關聯**：xmethod 的權威範例；練習 F 直接對標。

- **[gef.py 的顏色與 TUI](https://github.com/hugsy/gef)**
  - **讀哪裡**：搜 `Color` 類別與 context 顯示。
  - **和本章的關聯**：彩色輸出與 context 的工業實作；Final Project 標竿。

Python API 學完了。用練習 E 把這 Part 的能力整合成你的第一個真正插件，為 Final Project 暖身。

→ [練習 E：寫一套 Python 插件（heap 視覺化）](./practice-e-python-plugin-pack.md)
