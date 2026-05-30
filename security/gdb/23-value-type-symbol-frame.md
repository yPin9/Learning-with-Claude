# Ch 23 — Value / Type / Symbol / Frame 物件模型

> **目標**：吃透 Python API 的四大核心物件——`gdb.Value`（inferior 裡的值）、`gdb.Type`（型別）、`gdb.Symbol`（符號）、`gdb.Frame`（呼叫框）。學完你能在 Python 裡程式化地走訪任意資料結構、解讀型別、查符號、爬 stack——這是寫一切進階插件（pretty-printer、heap 分析、context）的根基。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼這四個物件是地基

Ch 22 給了你入口（`parse_and_eval`），這章給你那入口拿到的東西的**完整操作手冊**。pretty-printer 要操作 Value、解讀 Type；heap 分析要 cast Value、read_memory；context 視窗要走訪 Frame、查 Symbol。這四個物件你會用到吐——所以一次學透，後面就順。

```
   gdb.Value   ← inferior 裡的一個值（最常用）
      │ .type → gdb.Type      ← 它的型別
      │ .address → gdb.Value  ← 它的位址
   gdb.Symbol  ← 一個符號（變數/函式名 ↔ 位置）
      │ .value() → gdb.Value
   gdb.Frame   ← call stack 的一層
      │ .read_var() → gdb.Value
      │ .read_register() → gdb.Value
```

## `gdb.Value`：操作 inferior 的值

```c
// obj_demo.c — gcc -g -O0
typedef struct Node { int val; char tag[4]; struct Node *next; } Node;
Node n3 = {3, "ccc", 0};
Node n2 = {2, "bbb", &n3};
Node n1 = {1, "aaa", &n2};
Node *head = &n1;
int arr[5] = {10,20,30,40,50};
int main(void){ return 0; }
```

```python
import gdb
v = gdb.parse_and_eval("head")        # gdb.Value，型別 Node*

# 轉成 Python 型別
int(v)                                # 指標的整數值（位址）
hex(int(v))

# 解參與取欄位（這是核心！）
node = v.dereference()                # *head  → Node
node["val"]                           # node.val → gdb.Value(int)
int(node["val"])                      # → 1
v["val"]                              # 對指標直接 [] = 自動解參再取欄位（node->val）
int(v["next"]["val"])                 # head->next->val → 2

# 陣列
a = gdb.parse_and_eval("arr")
int(a[2])                             # arr[2] → 30
[int(a[i]) for i in range(5)]         # [10,20,30,40,50]

# 字串
tag = node["tag"]                     # char[4]
tag.string()                          # → "aaa"（讀成 Python str）
```

`Value` 的關鍵操作：

| 操作 | 意思 |
|---|---|
| `int(v)` / `float(v)` | 轉 Python 數值 |
| `v.dereference()` | `*v`（解指標） |
| `v["field"]` | 取欄位（指標自動解參） |
| `v[i]` | 陣列/指標索引 |
| `v.string()` | 讀成 Python 字串（char*） |
| `v.type` | 它的 `gdb.Type` |
| `v.address` | 它的位址（一個 Value） |
| `v.cast(t)` | 轉型成 `gdb.Type` t |
| `bytes(v.bytes)` | 原始 byte（GDB 內部表示） |

## 走訪 linked list（Python 版）

對比命令語言（Part 4），Python 的威力：

```python
import gdb

def walk(head_expr):
    node = gdb.parse_and_eval(head_expr)
    result = []                       # Python list！命令語言沒有
    while int(node) != 0:
        result.append({
            "val": int(node["val"]),
            "tag": node["tag"].string(),
            "addr": int(node),
        })
        node = node["next"]
    return result                     # 回傳結構化資料，可排序/過濾/輸出 JSON

for n in walk("head"):
    print(f"{n['addr']:#x}: val={n['val']} tag={n['tag']}")
```

走訪結果是 Python list of dict——你可以排序、過濾、轉 JSON、畫圖。這是命令語言永遠做不到的，也是為什麼複雜分析必須用 Python。

## `gdb.Type`：操作型別

```python
import gdb

t = gdb.lookup_type("Node")           # 用名字查型別
t.sizeof                              # 大小（bytes）
t.name                                # "Node"
t.code                                # 型別種類（TYPE_CODE_STRUCT 等）

# 走訪 struct 的所有欄位（pretty-printer 核心！）
for field in t.fields():
    print(field.name, field.type, field.bitpos // 8)   # 名字、型別、offset

# 常用型別構造
gdb.lookup_type("int")                # 基本型別
t.pointer()                           # Node*
gdb.lookup_type("char").pointer()     # char*

# 從 Value 拿型別
v = gdb.parse_and_eval("head")
v.type                                # Node *
v.type.target()                       # Node（指標指向的型別）
v.type.strip_typedefs()               # 剝掉 typedef
```

`type.code` 告訴你是哪種型別，常見值：

| code | 意思 |
|---|---|
| `gdb.TYPE_CODE_STRUCT` | struct |
| `gdb.TYPE_CODE_PTR` | 指標 |
| `gdb.TYPE_CODE_ARRAY` | 陣列 |
| `gdb.TYPE_CODE_INT` | 整數 |
| `gdb.TYPE_CODE_ENUM` | enum |
| `gdb.TYPE_CODE_UNION` | union |
| `gdb.TYPE_CODE_FUNC` | 函式 |

pretty-printer（Ch 26）要靠 `type.code` 判斷怎麼顯示、靠 `type.fields()` 走訪欄位。這是核心中的核心。

## cast：把記憶體套上型別（Python 版）

承 Ch 9 的轉型，Python 版是 heap 分析的命脈：

```python
import gdb

# 把一個位址當某型別看
node_t = gdb.lookup_type("Node")
addr = 0x555555558040
v = gdb.Value(addr).cast(node_t.pointer())   # (Node *)addr
print(int(v["val"]))

# 從 read_memory 的 raw bytes 解讀（heap chunk 分析）
inf = gdb.selected_inferior()
raw = inf.read_memory(addr, node_t.sizeof)
# 或直接 cast：
v2 = gdb.Value(raw, node_t)          # 把 bytes 當 Node 解讀（GDB 9+）
```

`gdb.Value(addr).cast(T.pointer())` 把裸位址套成型別指標——Final Project 的 heap 分析就是反覆做這件事：拿到一個 chunk 位址，cast 成 `malloc_chunk *`，讀出 size/fd/bk。

## `gdb.Symbol`：查符號

```python
import gdb

sym = gdb.lookup_global_symbol("head")
sym.name                              # "head"
sym.type                              # Node *
sym.value()                           # 拿到它的 gdb.Value
sym.is_variable, sym.is_function

# 在當前 frame 的 scope 查（含區域變數）
frame = gdb.selected_frame()
sym2, is_field = gdb.lookup_symbol("local_var")   # 回傳 (Symbol, bool)

# 反查位址 → 符號
block = gdb.block_for_pc(int(gdb.parse_and_eval("$pc")))
print(block.function)                 # 當前 pc 在哪個函式
```

`Symbol` 讓你用名字查到型別/位址/值，比 parse 文字（`info symbol`）可靠。`block_for_pc` 反查位址在哪個函式——context 視窗顯示「現在在哪」的正規做法。

## `gdb.Frame`：操作 call stack

```python
import gdb

frame = gdb.selected_frame()          # 當前 frame
frame.name()                          # 函式名
frame.pc()                            # program counter
frame.read_register("rsp")            # 讀暫存器（這個 frame 的）
frame.read_var("x")                   # 讀這個 frame 的區域變數 x → Value！
frame.function()                      # gdb.Symbol of the function
frame.older()                         # 往外一層（呼叫者）
frame.newer()                         # 往內一層

# 走訪整個 stack（context 視窗的 backtrace）
f = gdb.newest_frame()                # 最內層 (#0)
while f is not None:
    print(f.name(), hex(f.pc()))
    f = f.older()                     # 一路往 main 走
```

`frame.read_var("x")` 是讀區域變數的正規做法（自動處理 scope、frame）。`frame.older()`/`newer()` 走 stack，配合 `frame.read_register` 做 context 視窗的暫存器顯示與 backtrace。

## 一個綜合範例：印當前 frame 的所有區域變數

把四個物件串起來：

```python
# locals.py
import gdb

class DumpLocals(gdb.Command):
    """Dump all locals in the current frame with types."""
    def __init__(self):
        super().__init__("dumplocals", gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        frame = gdb.selected_frame()
        block = frame.block()                 # 當前 scope
        seen = set()
        while block:
            for sym in block:                 # 走訪 block 裡的符號
                if sym.is_variable and sym.name not in seen:
                    seen.add(sym.name)
                    val = sym.value(frame)    # Symbol + Frame → Value
                    print(f"{sym.name:>16} : {sym.type} = {val}")
            if block.function:                # 到函式 scope 就停
                break
            block = block.superblock
DumpLocals()
```

```
(gdb) source locals.py
(gdb) break some_func
(gdb) run
(gdb) dumplocals
               x : int = 5
            node : Node * = 0x555555558040
```

這用到了 Frame（拿 block）、Block（走訪符號）、Symbol（取型別/值）、Value（顯示）——四大物件協作。`info locals` 的 Python 自製版，但你能完全控制格式、過濾、排序。

## 踩雷集錦

1. **`int(value_of_struct)` 失敗**：只有純量（int/ptr）能 `int()`。struct/陣列要走欄位/索引。
2. **`v["field"]` 對非 struct/指標用會報錯**：先 `v.type.code` 確認是 STRUCT/PTR。
3. **`string()` 對非 char* 亂讀**：`string()` 預設讀到 `\0`，對非字串記憶體會讀出一堆垃圾或拋 MemoryError。確認真的是字串。
4. **`lookup_type` 找不到型別**：型別名要精確（`struct Node` vs `Node`），且要有對應 DWARF。typedef 名通常可，但要在當前 context 可見。
5. **frame API 在 inferior 沒跑時拋例外**：`selected_frame()` 需要有活的 inferior。包 try/except 或先檢查。
6. **`read_var` vs `parse_and_eval` 的 scope**：`frame.read_var("x")` 嚴格在那個 frame；`parse_and_eval("x")` 在「當前 selected frame」。寫多 frame 工具時用 `read_var` 指定 frame 較精確。
7. **Value 的生命週期**：Value 綁在某個 inferior 狀態，inferior 繼續執行/重啟後，舊 Value 可能失效（lazy value 重讀會變）。

## 進階：再往深一層

- **lazy value**：Value 預設是 lazy 的——直到你真的用它（`int()`、`string()`）才去 inferior 讀記憶體。`v.fetch_lazy()` 強制讀取。理解 lazy 對寫高效 pretty-printer 重要（避免讀不需要的記憶體）。
- **`Value.bytes`**（GDB 14+）/ `Value.address`：拿原始 byte 或位址，做底層分析。
- **`Type.template_argument(n)`**：對 C++ template 型別取第 n 個 template 參數——`std::vector<int>` 的 printer 靠這個拿到 `int`（Ch 30）。
- **`Type.fields()` 的 anonymous union/巢狀**：複雜 struct 的欄位走訪要處理匿名 union、巢狀 struct。
- **`gdb.Value` 的算術在 inferior 型別語意下**：`ptr + 1` 是指標算術（加 sizeof），不是 +1。要 byte 級加用 `int(ptr)+1` 再 cast 回去。
- **`gdb.block_for_pc` + `Block.is_global/is_static`**：分析符號可見性、做 scope 感知的工具。
- **convenience variable 橋接**：`gdb.convenience_variable("foo")` 讀、`gdb.set_convenience_variable("foo", v)` 寫——Python 與命令語言共享變數。

## 動手練習

1. 對 `obj_demo.c`，用 Python 走訪 `head` 整個 list，收集成 list of dict 並印出（本章 `walk` 範例）。
2. 用 `gdb.lookup_type("Node").fields()` 印出 Node 每個欄位的名字、型別、offset。
3. 用 `gdb.Value(某node位址).cast(Node*)` 把一個裸位址套回 Node 並讀 val——practice cast。
4. 用 `frame.older()` 迴圈走完整個 backtrace，印每層的函式名與 pc。
5. source 本章的 `dumplocals.py`，在某函式裡跑 `dumplocals`，對比內建 `info locals`。
6. 對一個 char 陣列用 `.string()` 讀成字串；再故意對一個 int 陣列用 `.string()`，觀察它怎麼亂讀（理解風險）。

## 本章重點整理

- 四大物件：`Value`（inferior 的值）、`Type`（型別）、`Symbol`（符號）、`Frame`（呼叫框）。
- Value 核心操作：`int()`、`dereference()`、`v["field"]`、`v[i]`、`string()`、`.type`、`.cast(t)`——走訪資料結構的工具。
- Type：`lookup_type`、`.fields()`（走訪欄位）、`.code`（判斷種類）、`.target()`、`.template_argument()`——pretty-printer 的根。
- `Value(addr).cast(T.pointer())` 把裸位址套型別——heap 分析的命脈。
- Frame：`read_var`、`read_register`、`older()`/`newer()`、`newest_frame()`——走 stack 做 context/backtrace。

## 自我檢核

- [ ] 怎麼在 Python 裡走訪一個 linked list 並收集成結構化資料？比命令語言強在哪？
- [ ] `v.dereference()`、`v["f"]`、`v[i]`、`v.string()` 各做什麼？
- [ ] pretty-printer 要怎麼用 Type 判斷型別種類、走訪欄位？
- [ ] 把一個 heap 位址當成某結構來讀，Python 怎麼寫？
- [ ] 怎麼用 Frame 走完整個 backtrace？怎麼讀某 frame 的區域變數？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Values From Inferior](https://sourceware.org/gdb/current/onlinedocs/gdb/Values-From-Inferior.html)**、**[Types In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Types-In-Python.html)**、**[Symbols In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Symbols-In-Python.html)**、**[Frames In Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Frames-In-Python.html)**
  - **讀哪裡**：四節對應本章四大物件，逐一細查。
  - **和本章的關聯**：本章是這四節的整合導讀；寫插件時當 reference 反覆查。

### 原始碼

- **[pwndbg 的 typeinfo / memory 模組](https://github.com/pwndbg/pwndbg/tree/dev/pwndbg)**
  - **讀哪裡**：它怎麼包裝 Value/Type/cast 成好用的工具函式。
  - **和本章的關聯**：看工業級程式怎麼用這四個物件；Final Project 可借鏡。

下一章把這些物件包進「自訂指令」——`gdb.Command` 與 `gdb.Parameter`，做出有參數、有補全、有子命令的真正插件指令。

→ [Ch 24 自訂 Command 與 Parameter](./24-python-commands-and-parameters.md)
