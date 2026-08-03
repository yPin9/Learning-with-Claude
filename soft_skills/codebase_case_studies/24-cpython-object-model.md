# Ch 24 — object model：PyObject / type / refcount + cyclic GC

> **目標**：讀懂 CPython 的 object model——「everything is a `PyObject*`」到底在 C 裡怎麼實現。三件事：(1) 一個值 = `PyObject`（`ob_refcnt` + `ob_type`）；(2) 行為由 `ob_type` 指的 `PyTypeObject` 的 `tp_*` slot（一張巨大 vtable）決定，`a + b` 就是查 `nb_add` slot；(3) 記憶體管理是「引用計數為主 + cyclic GC 補環」的混合策略。對照 Lua 的 tagged union，看清 CPython 選 boxed object 的取捨。

> **目標codebase**：CPython `v3.13.1`（commit `0671451`）

## 為什麼需要這個？

Ch 23 的 eval loop 追到 `PyNumber_Add`，就停在「它會查 `a` 的型別、找加法 slot、呼叫過去」。這一句話裡藏著整個 object model：什麼叫「查型別」？slot 是什麼？值本身長怎樣？

Lua（Part 1 Ch 5）給了你一個對照組：Lua 的值是 **tagged union** `TValue`——一個 `Value`（union，裝 int/double/指標）加一個 tag（型別標籤）。判斷型別看 tag，取值看 union。輕、快、無需配置。

CPython 走完全相反的路：**每個值都是 heap 上一個獨立物件**（boxed），前面掛引用計數和一個型別指標。連整數 `3` 都是一個 `PyLongObject`。這看起來浪費，但換來一致性：所有值都能被當 `PyObject*` 統一處理，型別系統與 C 擴充機制都建立在這個統一表示上。這章看清這個取捨，也把 Ch 23 停下的地方追到底。

## 先建立直覺：值 = 資料 + 一個指向 vtable 的指標

一張圖概括整個 object model：

```
   一個 Python 值（例如整數 3）在 heap 上：

   PyLongObject (實際型別)
   ┌──────────────────────────┐
   │ ob_refcnt   引用計數      │◄─┐  PyObject 前綴
   │ ob_type ───────────┐     │  │  （每個值都有）
   ├────────────────────┼─────┤◄─┘
   │ long_value  = 3    │     │  型別專屬資料
   └────────────────────┼─────┘
                        │
                        ▼
   PyTypeObject "int"（PyLong_Type，全程序唯一一份）
   ┌──────────────────────────────────┐
   │ tp_name = "int"                   │
   │ tp_basicsize                      │
   │ tp_as_number ──► { nb_add=long_add, nb_subtract=long_sub, ... }
   │ tp_getattro, tp_call, tp_hash ... │   ← 一張巨大的函式指標表（vtable）
   └──────────────────────────────────┘
```

**兩個核心觀念**：
1. 每個值（不論 int、str、list）都以 `PyObject` 開頭：`ob_refcnt` + `ob_type`。
2. `ob_type` 指向一個 `PyTypeObject`，那是型別的「vtable」——`3 + 4` 怎麼算、`len(x)` 怎麼算，全是查這張表對應的 slot。同型別的所有物件共享同一份 `PyTypeObject`。

## 核心一：PyObject——每個值的共同前綴

`PyObject` 定義在 `Include/object.h`：

```c
struct _object {
    union {
       Py_ssize_t ob_refcnt;
       ...
    };
    PyTypeObject *ob_type;
};
```
（`Include/object.h:163`，v3.13.1，非 free-threaded build；已省略 union 內的 split 欄位）

只有兩個概念欄位：
- `ob_refcnt`：引用計數。3.13 裡它是個 `union`（配合下面講的 immortal objects）。
- `ob_type`：指向這個值的型別物件。

「everything is a `PyObject*`」怎麼落實？靠 `PyObject_HEAD` 巨集當每個具體型別的第一個成員：

```c
#define PyObject_HEAD                   PyObject ob_base;
```
（`Include/object.h:60`）

看整數的實際型別 `PyLongObject`：

```c
struct _longobject {
    PyObject_HEAD
    _PyLongValue long_value;
};
```
（`Include/cpython/longintrepr.h:98-101`，v3.13.1）

`PyObject_HEAD` 展開成 `PyObject ob_base;`——所以 `PyLongObject` 的頭 16 bytes（64-bit）就是一個 `PyObject`。C 的結構佈局保證：`PyLongObject*` 可以安全地 cast 成 `PyObject*`，因為它們共享起始位址。**這就是「everything is a `PyObject*`」的物理實現**——不是抽象概念，是 struct 佈局的把戲。任何函式接 `PyObject*`，就能收下任何型別的值，再靠 `ob_type` 分辨它到底是什麼。

取型別用 `Py_TYPE`：

```c
static inline PyTypeObject* Py_TYPE(PyObject *ob) {
    return ob->ob_type;
}
```
（`Include/object.h:335`，v3.13.1）

## 核心二：PyTypeObject——型別即 vtable

`ob_type` 指的 `PyTypeObject` 是 object model 的引擎。它自己也是一個物件（`PyObject_VAR_HEAD` 開頭——型別的型別是 `type`），但重點是它裝了一大堆函式指標 slot。看它的頭幾十個 slot：

```c
struct _typeobject {
    PyObject_VAR_HEAD
    const char *tp_name;
    Py_ssize_t tp_basicsize, tp_itemsize;
    destructor tp_dealloc;
    ...
    PyNumberMethods *tp_as_number;     /* 數值運算 slot 群 */
    PySequenceMethods *tp_as_sequence; /* 序列運算 slot 群 */
    PyMappingMethods *tp_as_mapping;   /* 映射運算 slot 群 */
    hashfunc tp_hash;
    ternaryfunc tp_call;
    ...
    getattrofunc tp_getattro;          /* 屬性存取 */
    ...
    traverseproc tp_traverse;          /* cyclic GC 用 */
    inquiry tp_clear;                  /* cyclic GC 用 */
    ...
};
```
（`Include/cpython/object.h:147-189`，v3.13.1，節錄）

這就是 **vtable pattern**：型別的所有行為以函式指標形式掛在這張表上。`tp_hash` 是「怎麼算 hash」、`tp_call` 是「被 `()` 呼叫時做什麼」、`tp_getattro` 是「`obj.attr` 怎麼取」。運算子相關的 slot 再細分成子表：`tp_as_number` 指向一個 `PyNumberMethods`，裡面是 `nb_add`、`nb_subtract`…：

```c
typedef struct {
    binaryfunc nb_add;
    binaryfunc nb_subtract;
    binaryfunc nb_multiply;
    ...
} PyNumberMethods;
```
（`Include/cpython/object.h:60-105`，v3.13.1，節錄）

`int` 的 `PyNumberMethods` 就填了 `long_add`：

```c
static PyNumberMethods long_as_number = {
    (binaryfunc)long_add,       /*nb_add*/
    (binaryfunc)long_sub,       /*nb_subtract*/
    ...
};
```
（`Objects/longobject.c:6548-6550`，v3.13.1）

**這就是 protocol/vtable 的精髓**：`PyNumber_Add` 不知道也不在乎它加的是 int 還是 float，它只做一件事——查 `Py_TYPE(v)->tp_as_number->nb_add`，呼叫過去。是 int 就跑到 `long_add`，是 float 就跑到 `float_add`。這是 C 語言在沒有 class/virtual 的情況下實現多型的標準手法，你在 Lua（Ch 5 的 metatable）、nginx（Part 3 的 module handler 函式指標）都見過同一個 idiom，Ch 26 會把它收成一張 pattern 卡。

## 核心三：接上 Ch 23——`a + b` 怎麼查到 slot

現在把 Ch 23 停下的地方追完。`PyNumber_Add`（`Objects/abstract.c:1139`）：

```c
PyObject *
PyNumber_Add(PyObject *v, PyObject *w)
{
    PyObject *result = BINARY_OP1(v, w, NB_SLOT(nb_add), "+");
    if (result != Py_NotImplemented) {
        return result;
    }
    Py_DECREF(result);
    PySequenceMethods *m = Py_TYPE(v)->tp_as_sequence;
    if (m && m->sq_concat) {          /* + 對序列是「串接」 */
        ...
    }
    return binop_type_error(v, w, "+");
}
```
（`Objects/abstract.c:1139-1155`，v3.13.1，節錄）

`NB_SLOT(nb_add)` 是關鍵——它不是傳 slot 的值，是傳 slot 的**偏移量**：

```c
#define NB_SLOT(x) offsetof(PyNumberMethods, x)
#define NB_BINOP(nb_methods, slot) \
        (*(binaryfunc*)(& ((char*)nb_methods)[slot]))
```
（`Objects/abstract.c:910-912`，v3.13.1）

`NB_SLOT(nb_add)` = `nb_add` 在 `PyNumberMethods` 裡的 byte 偏移。`binary_op1` 拿這個偏移，去 `Py_TYPE(v)->tp_as_number` 這張表的那個偏移位置取出函式指標：

```c
static PyObject *
binary_op1(PyObject *v, PyObject *w, const int op_slot, ...)
{
    binaryfunc slotv;
    if (Py_TYPE(v)->tp_as_number != NULL) {
        slotv = NB_BINOP(Py_TYPE(v)->tp_as_number, op_slot);
    }
    ...
```
（`Objects/abstract.c:926-934`，v3.13.1，節錄）

**為什麼用偏移而非直接寫 `->nb_add`？** 因為 `binary_op1` 是所有二元運算共用的（加、減、乘、除…都呼叫它），只是傳不同的 slot 偏移。用 `offsetof` 把「要哪個 slot」參數化，一個函式服務所有運算子——這是 C 裡「用偏移量做 slot 分派」的經典技巧（`reading_code` Ch 23 讀 indirection 的一種）。

`slotv` 對 int 就是 `long_add`：

```c
long_add(PyLongObject *a, PyLongObject *b)
{
    CHECK_BINOP(a, b);
    return _PyLong_Add(a, b);
}
```
（`Objects/longobject.c:3784-3788`，v3.13.1）

**完整鏈追完**：`BINARY_OP` opcode → `_PyEval_BinaryOps[NB_ADD]` = `PyNumber_Add` → `binary_op1(NB_SLOT(nb_add))` → `Py_TYPE(v)->tp_as_number->nb_add` = `long_add` → `_PyLong_Add`。這條鏈練習 E 要你用 gdb 親手走一遍。

### 真跑：gdb 印出完整 call stack

build 後在 `long_add` 下中斷點，跑 `def f(a,b): return a+b; f(3,4)`：

```
$ gdb -q ./python -ex "break long_add" -ex "run /tmp/addtest.py" -ex "bt 4"
Breakpoint 1, long_add (a=..., b=...) at Objects/longobject.c:3785
#0  long_add (...) at Objects/longobject.c:3785
#1  binary_op1 (..., op_slot=0, op_name=0x... "+") at Objects/abstract.c:961
#2  PyNumber_Add (...) at Objects/abstract.c:1141
#3  _PyEval_EvalFrameDefault (...) at Python/generated_cases.c.h:132
```

（WSL2 / Python 3.13.1 debug build 真 gdb 輸出節錄。）這條 stack 就是上面那條鏈的實體證據：`generated_cases.c.h:132`（BINARY_OP body）→ `PyNumber_Add` → `binary_op1`（`op_slot=0` 正是 `nb_add` 的偏移）→ `long_add`。

## 底層機制：記憶體管理——refcount 為主，cyclic GC 補環

CPython 的記憶體回收是**兩層混合**，這是它跟 Lua（純標記-清除 GC）最大的取捨差異。

### 第一層：引用計數（主力）

`ob_refcnt` 記「有多少個引用指向我」。`Py_INCREF` 加一、`Py_DECREF` 減一，減到 0 就立刻回收。看 `Py_DECREF`（非 debug 分支）：

```c
static inline Py_ALWAYS_INLINE void Py_DECREF(PyObject *op)
{
    if (_Py_IsImmortal(op)) {
        return;
    }
    _Py_DECREF_STAT_INC();
    if (--op->ob_refcnt == 0) {
        _Py_Dealloc(op);
    }
}
```
（`Include/object.h:940-953`，v3.13.1）

減到 0 呼叫 `_Py_Dealloc`，它去查型別的解構 slot：

```c
_Py_Dealloc(PyObject *op)
{
    PyTypeObject *type = Py_TYPE(op);
    destructor dealloc = type->tp_dealloc;
    ...
```
（`Objects/object.c:2895-2898`，v3.13.1）

又是 vtable——收屍也是查 `tp_dealloc` slot。**引用計數的好處**：回收即時（refcount 歸零馬上釋放，記憶體壓力小、行為可預測），且不需要「stop the world」掃全部物件。這是 CPython 相對 Lua 純 GC 的一個取捨面。

**3.13 的 immortal objects（PEP 683）**：`Py_DECREF` 開頭先檢查 `_Py_IsImmortal`。`None`、`True`、`False`、小整數這些「永生」物件的 refcnt 被設成一個哨兵值（`_Py_IMMORTAL_REFCNT`），`INCREF`/`DECREF` 對它們直接 return 不動計數。為什麼？這些物件被引用無數次，每次 INCREF/DECREF 都寫記憶體，在多核（尤其 free-threaded）下是 cache 爭用的災難。設成永生後就不再碰它們的 refcnt。這也是為什麼前面 gdb 裡 `a=3` 顯示成 `<_PyRuntime+...>`——小整數 `3` 是預先建好的永生單例。

### 第二層：cyclic GC（補引用計數的盲點）

引用計數有一個致命盲點：**環**。`a.ref = b; b.ref = a`，兩者互指，即使外部再沒人引用它們，refcnt 也永遠 ≥ 1，永遠不歸零，記憶體洩漏。CPython 用一個分代標記式 GC（`Python/gc.c`）專門收這種環。

核心演算法在 `gc.c` 的註解裡寫得很清楚：

```
gc_refs
    At the start of a collection, update_refs() copies the true refcount
    to gc_refs, for each object in the generation being collected.
    subtract_refs() then adjusts gc_refs so that it equals the number of
    times an object is referenced directly from outside the generation
    being collected.
```
（`Python/gc.c:163-171`，v3.13.1）

拆解這個「試除法」：
1. `update_refs()`：把每個容器物件的真 refcnt 複製到一個臨時 `gc_refs`。
2. `subtract_refs()`：走訪每個物件的內部引用（透過型別的 `tp_traverse` slot），把「來自 GC 集合內部的引用」從 `gc_refs` 扣掉。
3. 扣完後，`gc_refs > 0` 的物件表示「還有集合外的引用指著它」→ 存活；`gc_refs == 0` 的表示「所有引用都來自集合內部」→ 可能是垃圾環。
4. 從存活物件出發做可達性標記，標不到的就是不可達的環，回收（透過 `tp_clear` slot 打斷環，再 refcount 歸零釋放）。

分代：物件分三代（`NUM_GENERATIONS 3`，`Include/internal/pycore_gc.h:223`），新物件在第 0 代、熬過收集的升代。**分代假設**：越老的物件越可能繼續活著，所以老代收得越少越省——這跟你在其他 GC 系統看過的分代假設同源。

**型別怎麼參與 GC**：只有「可能形成環」的容器型別（list、dict、有 `__dict__` 的實例…）才需要 GC。它們在 `PyTypeObject` 填 `tp_traverse`（怎麼走訪我引用的物件）和 `tp_clear`（怎麼打斷我的引用），並帶 `Py_TPFLAGS_HAVE_GC` flag。int、float 這種不含引用的葉子型別不參與 cyclic GC——它們不可能造環，純靠 refcount 就夠。

## 對比與取捨

| 面向 | Lua（tagged union） | CPython（boxed object） |
|---|---|---|
| 值表示 | `TValue` = union + tag，值可內嵌 | 每個值是 heap 上獨立物件，`PyObject` 前綴 |
| 小整數 | 直接放 union，零配置 | `PyLongObject`（但小整數永生單例快取） |
| 型別判斷 | 讀 tag（一個 byte） | 讀 `ob_type` 指標 → `PyTypeObject` |
| 行為分派 | metatable（可選） | `tp_*` slot vtable（必有） |
| 記憶體管理 | 純標記-清除 GC | **refcount 為主 + cyclic GC 補環** |
| 回收時機 | GC 週期才收 | refcount 歸零即時收；環等 GC |
| 取捨 | 輕、快、cache 友善；但值不統一 | 一致（everything is PyObject*）、C 擴充友善；但配置多、指標追逐 |

**核心取捨**：Lua 賭「值輕量、避免配置」，代價是值表示不統一（讀碼時到處要看 tag）。CPython 賭「值統一成 `PyObject*`」，代價是連 `3` 都要配置一個物件、到處指標追逐。CPython 用「小整數/None 等永生單例快取」補償最常見的配置熱點。這條「記憶體管理策略光譜」（純 GC ↔ 純 refcount ↔ 混合）是 Ch 26 要收的 pattern。

## 踩雷集錦

1. **錯誤直覺：整數 `3` 在 C 裡就是個 `int`。** → 正確認識：`3` 是一個 heap 上的 `PyLongObject`（`struct _longobject`，`PyObject_HEAD` + `long_value`）。CPython 沒有「原生整數值」這種東西，全都是 boxed 物件。只是小整數（-5～256）是預建的永生單例，省掉重複配置。
2. **錯誤直覺：`PyObject_HEAD` 是某種繼承。** → 正確認識：C 沒有繼承。`PyObject_HEAD` 展開成 `PyObject ob_base;`，靠「把 `PyObject` 當第一個成員」讓 struct 佈局共享起始位址，於是 `PyLongObject*` 能 cast 成 `PyObject*`。這是 struct 佈局把戲，不是語言層繼承。
3. **錯誤直覺：`PyNumber_Add` 裡有一堆 `if (是int) ... else if (是float) ...`。** → 正確認識：它不做型別分類，只查 `Py_TYPE(v)->tp_as_number->nb_add` 這個 slot 並呼叫。多型靠 vtable slot 實現，加法邏輯分散在各型別的 `nb_add`（`long_add`/`float_add`），不集中在 `PyNumber_Add`。
4. **錯誤直覺：有了引用計數就不需要 GC。** → 正確認識：引用計數收不掉**環**（互指的物件 refcnt 永遠 ≥ 1）。CPython 額外跑一個分代標記式 cyclic GC（`Python/gc.c`）專門收環。兩者分工：refcount 收非環（即時），GC 收環（週期性）。
5. **錯誤直覺：所有物件都參與 cyclic GC。** → 正確認識：只有可能形成環的**容器型別**（list、dict、自訂實例…）帶 `Py_TPFLAGS_HAVE_GC`、填 `tp_traverse`/`tp_clear`，才被 GC 追蹤。int、float、str 這種不含物件引用的葉子型別不參與——它們造不出環，純 refcount 就夠。

## 進階：再往深一層

- **free-threaded build 的 refcount（3.13 PEP 703）**：`Include/object.h:207` 的第二個 `struct _object`（`Py_GIL_DISABLED` 分支）是為「無 GIL」設計的：`ob_ref_local`（當前執行緒私有計數）+ `ob_ref_shared`（跨執行緒共享計數，原子操作）+ per-object `ob_mutex`。這是為了讓 refcount 在多核不成為序列化瓶頸——把「大多數引用來自建立它的執行緒」這個觀察拆成 local/shared 兩本帳。讀懂它，你就懂 immortal objects 為什麼在無 GIL 下更重要（永生物件完全不碰計數）。
- **`tp_traverse` 是 GC 能運作的關鍵合約**：cyclic GC 不知道任意 C 型別內部有哪些 `PyObject*` 引用，全靠每個容器型別實作 `tp_traverse`（一個「請對我引用的每個物件呼叫這個 callback」的函式）誠實回報。寫 C 擴充時漏實作 `tp_traverse`，你的型別就會讓 GC 看不見它持有的引用，造成洩漏或誤收。這是「型別必須遵守的 GC 合約」，接 `reading_code` Ch 24 讀狀態機/協定。
- **slot 與 `__dunder__` 的雙向映射**：Python 層的 `__add__` 和 C 層的 `nb_add` 互為表裡，`Objects/typeobject.c` 的 `slotdefs` 表維護這個映射——當你在 Python 定義 `class X: def __add__(self, o): ...`，CPython 在建型別時把一個 wrapper 填進 `nb_add` slot；反過來，C 型別的 `nb_add` 也會被包成 Python 可見的 `__add__`。這張 `slotdefs` 表（`typeobject.c` 裡上百行）是「C vtable ↔ Python dunder」的黏合劑，是 CPython 型別系統最精巧的一塊，屬按需深潛。

## 本章重點整理

- 每個 Python 值都是 heap 上以 `PyObject`（`ob_refcnt` + `ob_type`）開頭的物件；`PyObject_HEAD` 巨集靠 struct 佈局讓所有型別能 cast 成 `PyObject*`——「everything is a `PyObject*`」的物理實現。
- 行為由 `ob_type` 指的 `PyTypeObject` 的 `tp_*` slot 決定（vtable pattern）；`a + b` → `PyNumber_Add` → 查 `tp_as_number->nb_add` slot（用 `offsetof` 偏移分派）→ `long_add`。
- 記憶體管理兩層：**引用計數**（`Py_INCREF`/`DECREF`，歸零即時回收）為主 + **分代 cyclic GC**（`Python/gc.c`，`update_refs`/`subtract_refs` 試除法）補引用計數收不掉的環。
- 3.13 特性：immortal objects（PEP 683，永生單例不動計數）、free-threaded refcount（PEP 703，local/shared 雙計數）。
- 對照 Lua tagged union：CPython 賭「值統一 boxed」換一致性與 C 擴充友善，代價是配置多、指標追逐，用永生單例快取補償熱點。

## 自我檢核

- [ ] `PyObject_HEAD` 怎麼讓 `PyLongObject*` 能安全 cast 成 `PyObject*`？（提示：struct 佈局，不是繼承）
- [ ] `PyNumber_Add` 裡有沒有 `if 是int else 是float` 的分類？它實際上做什麼？
- [ ] `NB_SLOT(nb_add)` 傳的是 slot 的值還是偏移量？為什麼 `binary_op1` 要用偏移？
- [ ] 引用計數收不掉什麼？cyclic GC 用什麼演算法補（提示：copy refcount → subtract 內部引用）？
- [ ] 為什麼 int/float 不參與 cyclic GC，但 list/dict 要？（提示：造不造得出環）

## 延伸閱讀

- **[CPython devguide — "Garbage collector design"](https://devguide.python.org/internals/garbage-collector/)（官方）。**
  - **讀哪裡**：整頁，尤其「Reference counting」與「Handling cyclic references」兩節。
  - **學到什麼**：`gc.c` 的 `update_refs`/`subtract_refs` 演算法的官方導讀——把本章的試除法補成完整規格。
  - **前提**：理解引用計數的環洩漏問題。
- **[PEP 683 — Immortal Objects](https://peps.python.org/pep-0683/)（Eric Snow 等）。**
  - **讀哪裡**：Motivation 與 "Reference Counting" 段。
  - **學到什麼**：為什麼 3.13 要讓 `None`/小整數等永生、怎麼用哨兵 refcnt 實現——本章 immortal 段的權威來源。
  - **前提**：懂引用計數的多核 cache 爭用問題。
- **[`Objects/typeobject.c` 的 `slotdefs` 表](https://github.com/python/cpython/blob/v3.13.1/Objects/typeobject.c)（repo 內）。**
  - **讀哪裡**：搜 `slotdefs[]`，看 `nb_add` ↔ `__add__` 那幾行。
  - **學到什麼**：C 層 `tp_*` slot 與 Python 層 `__dunder__` 的雙向黏合——object model 最精巧的一塊，本章「進階」的深潛入口。
  - **前提**：理解本章的 slot/vtable 機制。

object model 這關過了，你手上有一條從 bytecode 到型別實作的完整鏈。但這條鏈是我替你標好座標的。下一章換你當主角：面對 CPython 這個 83 萬行的真·大專案，怎麼用系統化的分而治之，自己從零定位一個你沒讀過的語意。

→ [Ch 25 大型專案的分而治之實戰](./25-cpython-divide-and-conquer.md)
