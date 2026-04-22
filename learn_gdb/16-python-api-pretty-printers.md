# Ch 16 — Python API（二）：pretty printers 與 frame filters

> 目標：寫 pretty printer 讓自訂 data structure 在 `print` / bt 裡變得可讀，寫 frame filter 把 bt 輸出裡的雜訊過濾掉。

## 為什麼要 pretty printer

C++ `std::vector<int>` 在 gdb 裡預設 print 長這樣：

```
$1 = {<std::_Vector_base<int, std::allocator<int> >> =
{_M_impl = {<std::allocator<int>> = {<__gnu_cxx::new_allocator<int>> =
{<No data fields>}, <No data fields>}, _M_start = 0x555..., _M_finish = 0x555...,
_M_end_of_storage = 0x555...}}, <No data fields>}
```

看完 headache。但多數發行版的 libstdc++ 都有內建 pretty printer（`/usr/share/gcc-*/python/libstdcxx/`）會自動載入，你實際看到的是：

```
$1 = std::vector of length 3, capacity 4 = {1, 2, 3}
```

這就是 pretty printer 的工作。

**自己的 data structure** 沒有 printer，每次 print 都是 raw — 你要學會為自己的型別寫 printer。

## 最小 pretty printer

範例 C：

```c
typedef struct Point {
    double x;
    double y;
} Point;
```

預設 print：

```
(gdb) p p
$1 = {x = 3.14, y = 2.71}
```

還可以。想改成 `(x, y)` 簡寫：

```python
# point_printer.py
import gdb

class PointPrinter:
    """Pretty print struct Point."""

    def __init__(self, val):
        self.val = val

    def to_string(self):
        x = float(self.val['x'])
        y = float(self.val['y'])
        return f"Point({x:.2f}, {y:.2f})"

def lookup(val):
    if str(val.type.strip_typedefs()) == 'struct Point':
        return PointPrinter(val)
    return None

gdb.pretty_printers.append(lookup)
```

```
(gdb) source point_printer.py
(gdb) p p
$1 = Point(3.14, 2.71)
```

### Pretty printer 的 interface

一個 printer class 要有：

- **`to_string()`**（必要）：回傳一個字串或 `gdb.Value`，代表這個物件的「主要」印法。
- **`children()`**（可選）：回傳 iterator，yield `(name, value)` pair，描述子元素（gdb 會印得像 struct 一樣展開）。
- **`display_hint()`**（可選）：回 `"array"` / `"map"` / `"string"`，提示 gdb 怎麼排版。

## 印一個 linked list 的 pretty printer

```c
typedef struct User {
    int id;
    char name[32];
    struct User *next;
} User;
```

```python
class UserListPrinter:
    def __init__(self, val):
        self.val = val

    def to_string(self):
        # count length
        count = 0
        node = self.val
        while int(node) != 0:
            count += 1
            node = node['next']
        return f"User list of length {count}"

    def children(self):
        node = self.val
        i = 0
        while int(node) != 0:
            yield (f"[{i}]", node.dereference())
            node = node['next']
            i += 1

    def display_hint(self):
        return "array"

def lookup(val):
    t = val.type.strip_typedefs()
    if t.code == gdb.TYPE_CODE_PTR and str(t.target().strip_typedefs()) == 'struct User':
        return UserListPrinter(val)
    return None

gdb.pretty_printers.append(lookup)
```

```
(gdb) p user_db
$1 = User list of length 3 = {
  [0] = {id = 1042, name = "Carol", next = 0x...},
  [1] = {id = 2, name = "Bob", next = 0x...},
  [2] = {id = 1, name = "Alice", next = 0x0}
}
```

**display_hint = "array"** 讓 gdb 把 children 排成陣列。用 `"map"` 會排成 key=value（預期 children 成對 yield）。

## 更複雜：RegexpCollectionPrettyPrinter

對多個型別分別指定 printer，用 regexp 匹配 type name：

```python
import gdb.printing

pp = gdb.printing.RegexpCollectionPrettyPrinter("my_proj")
pp.add_printer("Point",    r"^struct Point$",    PointPrinter)
pp.add_printer("UserList", r"^struct User \*$",  UserListPrinter)
gdb.printing.register_pretty_printer(None, pp)
```

優勢：

- 統一管理多個 printer
- `info pretty-printer` 能列出所有註冊的 printer
- user 可以用 `disable pretty-printer <name>` 暫時關閉

## 自動載入：objfile pretty printers

你希望**你的 library 被載入時，gdb 自動載入對應 printer**。機制：

在 binary 的 `.debug_gdb_scripts` segment 加一個指向 python script 的路徑。或者更常用的辦法是 **auto-load**：

- 在 `/usr/share/gdb/auto-load/YOUR/LIBRARY_PATH.so-gdb.py` 放 script
- gdb 載入 `libyourlib.so` 時就會 source 那個 .py

這是 libstdc++ 的做法。

對個人專案，通常放 `~/.gdbinit` 裡 `source /path/to/printer.py` 就夠。

## Frame filter：精簡 bt 輸出

C++ / Rust template 讓 bt 常常爆長：

```
#0  std::__detail::__variant::__raw_idx_visit<...> at variant:1345
#1  std::visit<...> at variant:1789
#2  boost::asio::detail::completion_handler<...> at asio/detail/impl/...
#3  boost::asio::detail::reactive_socket_recv_op<...> at ...
#4  process_request(Request&) at server.cpp:42
```

99% 的時候你只關心 `process_request`，其他是 framework 內部。Frame filter 可以把「系統層」隱藏：

```python
import gdb
from gdb.FrameDecorator import FrameDecorator

class HideStdFilter:
    def __init__(self):
        self.name = "hide_std"
        self.priority = 100
        self.enabled = True
        gdb.frame_filters[self.name] = self

    def filter(self, frame_iter):
        for frame in frame_iter:
            f = frame.inferior_frame()
            fn = f.name() or ""
            if fn.startswith("std::") or fn.startswith("boost::"):
                continue                # 完全過濾
            yield frame

HideStdFilter()
```

結果：

```
(gdb) bt
#0  process_request(Request&) at server.cpp:42
#1  main at server.cpp:100
```

乾淨。

### FrameDecorator：改顯示而不是完全隱藏

```python
class ShortenedFrame(FrameDecorator):
    def function(self):
        name = super().function()
        if name and len(name) > 60:
            return name[:30] + "..." + name[-27:]
        return name

class ShortenFilter:
    def __init__(self):
        self.name = "shorten_names"
        self.priority = 100
        self.enabled = True
        gdb.frame_filters[self.name] = self

    def filter(self, frame_iter):
        return (ShortenedFrame(f) for f in frame_iter)
```

把超長 template name 截短顯示，但不隱藏 frame。

### 管理 frame filter

```
(gdb) info frame-filter
(gdb) disable frame-filter global hide_std
(gdb) enable frame-filter global hide_std
```

## 實戰：複雜資料的 printer

一個雙向 linked list + hash map 的範例，省略實作，概念如下：

```python
class DListPrinter:
    def __init__(self, val):
        self.val = val
    def to_string(self):
        head = self.val['head']
        size = int(self.val['size'])
        return f"DList(size={size})"
    def children(self):
        node = self.val['head']
        i = 0
        while int(node) != 0:
            yield (f"[{i}]", node.dereference()['data'])
            node = node['next']
            i += 1
    def display_hint(self):
        return "array"

class HashMapPrinter:
    def __init__(self, val):
        self.val = val
    def to_string(self):
        n = int(self.val['size'])
        cap = int(self.val['capacity'])
        return f"HashMap(size={n}, cap={cap})"
    def children(self):
        buckets = self.val['buckets']
        cap = int(self.val['capacity'])
        for i in range(cap):
            entry = buckets[i]
            while int(entry) != 0:
                yield (entry['key'].string(), entry['value'])
                entry = entry['next']
    def display_hint(self):
        return "map"
```

寫 printer 的實用原則：

- **先確認正確性**：`p your_value` 看出來對不對
- **處理 empty / NULL**：`if int(val) == 0: return "(null)"`
- **避免無窮遞迴**：linked list 有 cycle 時會死迴圈，加長度上限
- **效能**：大型結構只印前 N 個 element：`if i >= 100: yield ("...", "..."); break`

## display_hint 完整選項

| hint | 效果 |
|---|---|
| `"string"` | gdb 把 to_string 當字串引號輸出 |
| `"array"` | children 展開成陣列樣式 |
| `"map"` | children 成對輸出 key = value |
| `None` | 預設 struct 樣式 |

## Auto-loading 自己的 printer

在專案 `.gdbinit` 加：

```
python
import sys
sys.path.insert(0, "/path/to/project/gdb_scripts")
import my_printers
my_printers.register()
end
```

比 `source /path/to/file.py` 更模組化。

## 常見坑

1. **type 比對字串太脆**：gcc 不同版本會讓 `struct Foo` 變成 `Foo`。用 `val.type.strip_typedefs().code` 配合 `TYPE_CODE_STRUCT` 才穩。
2. **template 名稱爆炸**：C++ template 的 type 字串可能很長、含空白，用 regex 時 escape 好。
3. **`children()` yield 太慢**：每個 child 都要 gdb eval，大型結構會卡幾秒。考慮 generator pagination。
4. **print 被 pretty printer 改到看不到原始 struct**：偶爾你想看 raw layout。臨時關閉 — `print -raw-values on -- your_var`。
5. **Frame filter 衝突**：多個 filter 都加到 global 會依 priority 順序 chain。`priority` 大的先跑。
6. **filter 把你要看的 frame 藏掉**：`bt -no-filters` 顯示未過濾的原始 bt。

## 動手練習

練習目標是 Practice D 的鋪墊。這裡先小試幾個：

1. 給 Ch 4 的 `struct Point`（或任何你常用的簡單 struct）寫 pretty printer，source 進 gdb 看效果。
2. 給 linked list 寫 printer（用 `display_hint = "array"`）。
3. 寫個 frame filter 過濾「名字包含 `__` 的內部函式」。
4. 給一個 hash map 結構寫 printer，使用 `display_hint = "map"`。
5. 對 C++ 專案，寫 printer 給你自己的 `MyString` / `MyMap` 等 class。

## 延伸閱讀

- GDB manual §23.2 Python API：<https://sourceware.org/gdb/current/onlinedocs/gdb/Python-API.html>
- libstdc++ 的 printer 實作（學習範例）：`/usr/share/gcc-*/python/libstdcxx/v6/printers.py`
- Meta / Google 內部 debugger extensions 常公開：搜尋 "gdb pretty printer github" 有大量範例。

## 自我檢核

- [ ] 我能寫 pretty printer 的 `to_string` + `children` + `display_hint`
- [ ] 我知道 `RegexpCollectionPrettyPrinter` 怎麼批次管理 printer
- [ ] 我能用 frame filter 過濾或改寫 bt frame
- [ ] 我會處理 NULL / empty / 迴圈這些 edge case
- [ ] 我能把 printer module 放到專案，用 `~/.gdbinit` 自動載入

下一個是 Practice D：把前兩章的東西整合起來，為一個真實 data structure 寫完整的 printer + frame filter + 自動化 workflow。

→ [練習 D：pretty printer + 自動化 workflow](./practice-d-pretty-printer.md)
