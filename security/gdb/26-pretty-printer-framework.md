# Ch 26 — Pretty-printer 框架

> **目標**：學會寫 pretty-printer——讓 `print myobj` 顯示成人看得懂的樣子，而非一坨內部指標。掌握 printer class 的 `to_string()` / `children()` / `display_hint()`、printer 的註冊與查找、auto-load 機制。學完你能為任何自訂型別（與 STL，Ch 30）寫出漂亮的顯示。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`（C；C++ 在 Ch 30）。

## 為什麼需要 pretty-printer

`print` 一個複雜結構，原生輸出常常難看到沒法用：

```
(gdb) print mylist
$1 = {head = 0x5555...2a0, size = 3, capacity = 8, _internal = {...}}
# 你想看的是 [10, 20, 30]，不是內部欄位！
```

pretty-printer 讓你自訂某型別**怎麼被 `print`**。寫好後：

```
(gdb) print mylist
$1 = MyList of length 3 = {10, 20, 30}     # 你定義的漂亮顯示
```

C++ 的 `std::vector` 能 `print` 出 `{1, 2, 3}` 而非內部三個指標，就是 libstdc++ 附帶的 pretty-printer 在工作（Ch 30）。學會寫 printer，你能讓任何專案的核心資料結構在 debug 時一目了然——這是團隊 debug 體驗的巨大提升，也是 Final Project 的一塊。

## 先建立直覺：一個翻譯層

```
   print myobj
        │
        ▼
   GDB 問所有註冊的 printer：「你們誰認得這個型別？」
        │
        ├─ printer A: 不認得 (return None)
        ├─ printer B: 認得！→ 回傳一個 printer 物件
        │
        ▼
   GDB 呼叫該 printer 物件的 to_string() / children()
        │
        ▼
   顯示你定義的漂亮輸出
```

兩個角色：

1. **printer 物件**：知道怎麼把「一個特定值」變成字串（`to_string()`）和子元素（`children()`）。
2. **lookup function**：GDB 拿一個 Value 問「誰認得它」，回傳對應的 printer 物件或 None。

## 範例型別

```c
// pp_demo.c — gcc -g -O0
#include <stdlib.h>
typedef struct {
    int *data;
    int size;
    int capacity;
} IntVec;

IntVec make(int n) {
    IntVec v = { malloc(n*sizeof(int)), n, n*2 };
    for (int i = 0; i < n; i++) v.data[i] = (i+1) * 10;
    return v;
}
int main(void) {
    IntVec v = make(3);            // 想 print 成 {10, 20, 30}
    return v.size;
}
```

原生 `print v`：`{data = 0x..., size = 3, capacity = 6}`——看不到內容。

## 寫一個 printer

```python
# pp_intvec.py
import gdb

class IntVecPrinter:
    """Pretty-printer for IntVec."""
    def __init__(self, val):
        self.val = val                       # 要印的 gdb.Value

    def to_string(self):
        # 回傳「主體」字串（顯示在 = 右邊）
        size = int(self.val["size"])
        cap = int(self.val["capacity"])
        return f"IntVec(size={size}, cap={cap})"

    def children(self):
        # 回傳 (name, value) 的可迭代——讓 GDB 印出元素
        size = int(self.val["size"])
        data = self.val["data"]
        for i in range(size):
            yield (f"[{i}]", data[i])        # data[i] 是 gdb.Value

    def display_hint(self):
        return "array"                       # 提示 GDB 用陣列格式 {..., ...}

def lookup(val):
    # GDB 拿每個要 print 的 Value 來問
    if val.type.strip_typedefs().tag == "IntVec" or str(val.type) == "IntVec":
        return IntVecPrinter(val)
    return None                              # 不認得就回 None

gdb.pretty_printers.append(lookup)           # 註冊到全域
```

```
(gdb) source pp_intvec.py
(gdb) break main
(gdb) run
(gdb) next                                   # 讓 v 賦值
(gdb) print v
$1 = IntVec(size=3, cap=6) = {[0] = 10, [1] = 20, [2] = 30}
```

成功——`print v` 現在顯示型別摘要 + 元素內容。

## 三個方法的角色

| 方法 | 回傳 | 作用 |
|---|---|---|
| `to_string()` | 字串 | `=` 右邊的「主體」描述 |
| `children()` | iterable of (name, Value) | 子元素（陣列元素、struct 欄位） |
| `display_hint()` | 字串 | 提示顯示格式 |

`display_hint()` 的回傳值影響呈現：

- `"array"`：用 `{...}` 顯示 children（陣列風格）
- `"map"`：children 兩兩成對（key, value, key, value...），顯示成 map
- `"string"`：把 `to_string()` 當字串顯示（加引號）
- `None`：預設

只有 `to_string()` 是必須的；`children()` 和 `display_hint()` 視型別需要。簡單純量型別只要 `to_string()`。

## 註冊：全域 vs objfile vs 用 RegexpCollection

上面用 `gdb.pretty_printers.append(lookup)` 註冊到全域。更好的方式是用 GDB 提供的工具類別，支援按型別名 regex 比對、可開關：

```python
import gdb
import gdb.printing

def build_pp():
    pp = gdb.printing.RegexpCollectionPrettyPrinter("mylib")
    pp.add_printer("IntVec", "^IntVec$", IntVecPrinter)   # 名稱、regex、class
    return pp

gdb.printing.register_pretty_printer(
    gdb.current_objfile() or None,           # None = 全域；objfile = 只對該 binary
    build_pp(),
    replace=True,
)
```

`RegexpCollectionPrettyPrinter` 的好處：

- 用 regex 比對型別名（`^std::vector<.*>$`）
- 可 `info pretty-printer` 列出、`disable pretty-printer mylib` 關閉
- 多個 printer 集中管理

註冊到三種範圍：

| 範圍 | 何時用 |
|---|---|
| 全域 `gdb.pretty_printers` | 隨處可用 |
| objfile（`objfile.pretty_printers`） | 只對特定 binary/library |
| progspace | 整個 program space |

## auto-load：讓 printer 自動出現

承 Ch 19，你發布一個 library 時，附帶 `libfoo.so-gdb.py`，GDB debug 該 library 時自動載入它、註冊 printer。這就是 STL printer 自動生效的原理（Ch 30）。

```python
# libfoo.so-gdb.py  （放在 library 旁邊或 safe-path）
import gdb.printing
# ... 定義 printer ...
gdb.printing.register_pretty_printer(gdb.current_objfile(), build_pp())
```

`gdb.current_objfile()` 在 auto-load 時是「正在載入的那個 objfile」，把 printer 綁到它——使用者 debug 你的 library 就自動獲得漂亮顯示，零設定。這是「發布有 debug 體驗的 library」的關鍵（Final Project 進階）。

## 處理棘手情況

**指標與遞迴**：printer 印一個含指標的結構，小心無窮遞迴（list 有環）。`children()` 自己控制走訪深度。

**讀記憶體失敗**：`children()` 走訪指標時可能讀到壞記憶體。包 try/except `gdb.MemoryError`，顯示 `<invalid>` 而非讓整個 print 崩潰。

```python
def children(self):
    try:
        size = int(self.val["size"])
        data = self.val["data"]
        for i in range(min(size, 1000)):     # 上限防爆
            yield (f"[{i}]", data[i])
    except gdb.MemoryError:
        yield ("<error>", "cannot read")
```

**lazy / 效能**：`print` 大結構時 children 可能很多。`set print elements N` 會限制 GDB 跟 printer 要幾個 children，但你自己也該設上限。

## 踩雷集錦

1. **lookup function 回傳 class 而非 instance**：要 `return IntVecPrinter(val)`（實例），不是 `return IntVecPrinter`。
2. **型別比對太鬆/太緊**：`str(val.type)` 可能含 `const`/`volatile`/typedef。用 `val.type.strip_typedefs()`、`.unqualified()`、`.tag` 處理。比對失敗 printer 就不生效。
3. **`children()` 回傳的 value 不是 gdb.Value**：要 yield `(name, gdb.Value)`，不是 Python int。要顯示 Python 計算值得包成 Value 或放進 to_string。
4. **printer 拋例外讓 print 全壞**：printer 裡的 exception 會讓 `print` 失敗或顯示醜陋錯誤。包好 try/except，`set python print-stack full` 除錯。
5. **無窮遞迴/超大輸出**：環狀結構或巨大容器，children 要設深度/數量上限。
6. **改了 printer 沒重載**：重新 `source` 並注意 `replace=True`（否則重複註冊）。
7. **`display_hint` 拼錯**：只有 `"array"`/`"map"`/`"string"` 有特殊意義，打錯就當預設。

## 進階：再往深一層

- **template printer（C++）**：用 `val.type.template_argument(0)` 拿 `std::vector<T>` 的 T，寫泛型 printer（Ch 30）。
- **`gdb.printing.PrettyPrinter` 基底**：更結構化的 printer 集合管理。
- **`children()` 的 map hint 配對**：回傳 `[("[key0]", k0), ("[value0]", v0), ...]`，GDB 顯示成 `{k0 -> v0}`。寫 map/dict printer 時用。
- **與 `set print pretty` 的關係**：`set print pretty on`（Ch 7）控制換行排版；pretty-printer 控制內容。兩者疊加。
- **xmethod（Ch 28）**：printer 管「怎麼顯示」，xmethod 管「怎麼呼叫方法/索引」——`print vec[5]` 不真的呼叫 `operator[]` 而用 Python 算。互補。
- **效能：lazy children**：用 generator（`yield`）而非一次建好全 list，配合 GDB 的 `print elements` 限制，避免印超大容器時卡死。

## 動手練習

1. 對 `pp_demo.c` 的 IntVec，source 本章的 `pp_intvec.py`，`print v` 看漂亮輸出。
2. 改 `to_string()` 讓它顯示 `<IntVec len=3>`，改 `display_hint()` 在 `"array"` 與 `None` 間切換，觀察輸出差異。
3. 用 `RegexpCollectionPrettyPrinter` 重寫註冊，然後 `info pretty-printer` 看它、`disable pretty-printer mylib IntVec` 關掉再開。
4. 故意讓 IntVec 的 `data` 指向壞位址（`set var v.data = 0x1`），看 printer 怎麼處理（加 try/except 前後對比）。
5. 寫一個 linked list 的 printer，用 `children()` 走訪節點（小心設深度上限防環）。
6. （進階）把你的 printer 放進 `pp_demo-gdb.py`，測試 auto-load（需設 safe-path，Ch 19）。

## 本章重點整理

- pretty-printer 自訂某型別「怎麼被 print」；STL 的漂亮顯示就是 printer。
- printer 物件三方法：`to_string()`（主體，必須）、`children()`（子元素 iterable of (name, Value)）、`display_hint()`（`array`/`map`/`string`）。
- lookup function 拿 Value 判斷型別、回傳 printer **實例**或 None；註冊到 `gdb.pretty_printers` 或用 `RegexpCollectionPrettyPrinter`。
- auto-load（`libfoo.so-gdb.py` + `gdb.current_objfile()`）讓 printer 隨 library 自動生效。
- 走訪指標要防環、防壞記憶體（try/except MemoryError）、設數量上限。

## 自我檢核

- [ ] pretty-printer 的三個方法各做什麼？哪個是必須的？
- [ ] `display_hint()` 的 `array`/`map`/`string` 各影響什麼顯示？
- [ ] lookup function 要回傳什麼？常見的型別比對陷阱是什麼？
- [ ] STL 的 `std::vector` 怎麼「自動」print 成 `{1,2,3}`？跟 auto-load 什麼關係？
- [ ] printer 走訪指標時要防哪些災難？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Pretty Printing](https://sourceware.org/gdb/current/onlinedocs/gdb/Pretty-Printing.html)** 與 **[Writing a Pretty-Printer](https://sourceware.org/gdb/current/onlinedocs/gdb/Writing-a-Pretty_002dPrinter.html)**
  - **讀哪裡**：API（to_string/children/display_hint）、Selecting Pretty-Printers、完整範例。
  - **和本章的關聯**：本章核心的權威 + 一個完整可抄的範例。

### 原始碼

- **[libstdc++ 的 STL printers](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/python/libstdcxx/v6/printers.py)**
  - **讀哪裡**：`class StdVectorPrinter`、`StdMapPrinter`。
  - **和本章的關聯**：最權威的 printer 範例；Ch 30 會直接用它，這裡先看它怎麼寫。

### 部落格

- **[Writing GDB pretty-printers](https://sourceware.org/gdb/wiki/PythonGdbTutorial)** — GDB Wiki tutorial
  - **為什麼值得讀**：step-by-step 帶你寫第一個 printer，補 manual 的不足。

下一章是 backtrace 與 stack 的客製化：frame filter（美化/過濾 backtrace）與 unwinder（在 stack 損壞時重建 frame）——gef 漂亮 backtrace 的來源。

→ [Ch 27 Frame filter / decorator / Unwinder](./27-frame-filters-and-unwinders.md)
