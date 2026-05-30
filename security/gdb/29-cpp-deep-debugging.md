# Ch 29 — C++ 深度除錯

> **目標**：掌握 C++ 特有的除錯難題——name mangling、template 實例化、虛擬函式與 vtable、多型的動態型別（RTTI）、exception、以及 STL 物件的內部結構。C++ 比 C 難 debug 一個檔次，這章把那些「為什麼 GDB 顯示這麼怪」一一拆解。

> **環境**：GDB 13/14，Linux x86_64，g++ / clang++，`-g -O0 -std=c++17`。

## 為什麼 C++ debug 是另一個世界

C 的 debug 相對直接：函式就是函式、變數就是變數。C++ 在中間塞了一大堆編譯器魔法：

- 函式名被 **mangle** 成 `_ZN3Foo3barEi`
- 一個 template 變成幾十個實例
- `virtual` 呼叫透過 vtable 間接跳轉
- 物件的「宣告型別」和「實際型別」可能不同（多型）
- exception 改變控制流
- STL 容器的內部是一坨指標

不懂這些，你會一直困惑「GDB 為什麼這樣顯示」。這章把 C++ 的編譯器魔法攤開，讓你看穿 GDB 的輸出。

## Name mangling：函式名的真身

```cpp
// cpp_demo.cpp — g++ -g -O0 -std=c++17
#include <string>
struct Widget {
    int id;
    virtual int area() const { return id * id; }    // virtual
    virtual ~Widget() {}
};
struct Button : Widget {
    int area() const override { return id * 2; }     // override
};
int compute(int x) { return x + 1; }
int compute(double x) { return (int)x; }             // overload
template<typename T> T maxOf(T a, T b){ return a>b?a:b; }
int main() {
    Widget *w = new Button();   w->id = 5;
    int a = w->area();          // 虛擬呼叫 → Button::area
    int c = compute(3);
    int m = maxOf<int>(3, 7);
    return a + c + m;
}
```

overload 的 `compute(int)` 和 `compute(double)` 在 binary 裡是不同符號：

```
(gdb) info functions compute
0x...  int compute(double)
0x...  int compute(int)        # GDB 自動 demangle 顯示
(gdb) break compute            # overload！GDB 會問你要哪個
[0] cancel
[1] compute(double)
[2] compute(int)
> 2
(gdb) break 'compute(int)'     # 或直接指定（引號）
```

mangled 真身：`echo _Z7computei | c++filt` → `compute(int)`。GDB 預設 demangle 顯示（Ch 6），但 overload 下斷要消歧義。

## Template：一個變多個

template 不是一個函式，是一個「產生函式的模板」。每個型別實例化一份：

```
(gdb) info functions maxOf
0x...  int maxOf<int>(int, int)        # 只實例化了 int 版
(gdb) break maxOf                       # 若有多個實例 → <MULTIPLE>（Ch 4）
(gdb) ptype maxOf<int>
type = int (int, int)
```

template 的坑：

- 只有**被實例化**的版本才在 binary 裡（沒用到的型別不存在，沒法下斷）。
- 一個 `std::vector<std::pair<int, std::string>>` 的型別名超長，GDB 顯示一大串。
- 下斷點要用完整的實例化名，或用 `rbreak`（Ch 4）regex 抓一批。

## 虛擬函式與 vtable

`w->area()` 是 virtual call——不是直接跳到某函式，而是透過物件的 **vtable**（虛擬函式表）間接跳轉。GDB 能幫你看穿：

```
(gdb) break main
(gdb) run
(gdb) next 直到 w 賦值
(gdb) print w
$1 = (Widget *) 0x... 
(gdb) print *w
$2 = {_vptr.Widget = 0x... <vtable for Button+16>, id = 5}   # vptr 指向 Button 的 vtable！
(gdb) info vtbl w               # 印出 w 的完整 vtable
vtable for 'Widget' @ 0x... (subobject @ 0x...):
[0]: 0x... Button::area() const
[1]: 0x... Button::~Button()
...
```

關鍵觀察：

- `print *w` 顯示 `_vptr.Widget = ... <vtable for Button+16>`——即使 `w` 宣告型別是 `Widget*`，vptr 指向 **Button** 的 vtable，這證明實際物件是 Button。
- `info vtbl w` 列出 vtable 每個 slot 指向哪個函式——看穿虛擬分派、debug 「為什麼呼叫了錯的覆寫版本」。

## 動態型別：`set print object on`

承上，`w` 宣告是 `Widget*` 但實際指向 Button。預設 GDB 用**宣告型別**，但開 RTTI 後能顯示**實際型別**：

```
(gdb) print w
$3 = (Widget *) 0x...           # 預設：宣告型別 Widget*
(gdb) set print object on
(gdb) print w
$4 = (Button *) 0x...           # 現在顯示實際型別 Button*！
(gdb) whatis w                  # 宣告型別仍是 Widget*
type = Widget *
(gdb) ptype w                   # 但 ptype 可看實際
```

`set print object on`（Ch 9 提過）讓 GDB 用 RTTI 判斷多型物件的實際型別。debug 多型/繼承體系時必開——否則你看到一堆基底類別指標，不知道實際是哪個衍生類別。強烈建議寫進 `.gdbinit`。

## Exception debug

承 Ch 14 的 `catch throw`：

```
(gdb) catch throw                    # throw 時停
(gdb) catch catch                    # catch 時停
(gdb) run
Catchpoint 1 (exception thrown), 0x... in __cxa_throw
(gdb) bt                              # throw 從哪來
(gdb) print *(std::exception*)$rsi   # 看 exception 物件（需知道型別）
```

debug 「未捕捉的 exception 導致 terminate」：`catch throw` 找 throw 點，比在 `std::terminate` 才停有用太多。GDB 13+ 還支援 `catch throw TYPE` 只攔特定 exception 型別。

## STL 物件：靠 pretty-printer

```
(gdb) print myvector
$5 = std::vector of length 3, capacity 4 = {10, 20, 30}    # 漂亮！
(gdb) print mymap
$6 = std::map with 2 elements = {[1] = "one", [2] = "two"}
```

這些漂亮顯示是 **libstdc++ 附帶的 pretty-printer**（Ch 26 的 auto-load）。如果你看到的是一坨內部指標（`{_M_impl = {...}}`），表示 printer 沒載入——Ch 30 專門解決。

不靠 printer 手動看 STL 內部（printer 壞掉時的硬功夫）：

```
(gdb) print myvector._M_impl._M_start          # vector 的資料起點
(gdb) print *myvector._M_impl._M_start@3        # 當陣列印（Ch 7 的 @）
(gdb) print myvector._M_impl._M_finish - myvector._M_impl._M_start   # 算 size
```

理解 STL 內部結構（vector 是三個指標 start/finish/end_of_storage），在 printer 失效或寫自訂 printer 時是必備。

## 一個完整的 C++ debug 流程

```
(gdb) set print object on            # 多型顯示實際型別
(gdb) set print pretty on            # struct 換行
(gdb) break Button::area
(gdb) run
Breakpoint 1, Button::area (this=0x...) at cpp_demo.cpp:9    # this 指標！
(gdb) print *this                    # 看物件
$1 = {<Widget> = {_vptr.Widget = ..., id = 5}, <No data fields>}
(gdb) print this->id                 # 或 print id（this-> 可省）
(gdb) info args                      # C++ 方法的 this 是隱藏的第一個參數
```

C++ 方法有隱藏的 `this` 參數（Ch 11 的 `$rdi` 在 method 裡是 this）。`print *this` 看整個物件、`info args` 看 this 與其他參數。

## 踩雷集錦

1. **`break funcname` 對 overload 失敗或問你選**：用 `break 'compute(int)'`（完整簽章 + 引號）消歧義。
2. **template 函式下斷找不到**：該型別沒被實例化（沒用到），binary 裡不存在。或要用完整實例名/`rbreak`。
3. **多型物件看到基底類別**：沒開 `set print object on`，GDB 用宣告型別。開了才看到實際衍生型別。
4. **STL 顯示一坨內部指標**：pretty-printer 沒載入（Ch 30）。可能是 GDB 找不到 libstdc++ 的 python 腳本、或 safe-path 問題。
5. **`this` 是 `<optimized out>`**：最佳化把 this 丟暫存器又覆蓋（Ch 32）。`-O0` 重編。
6. **C++ 符號名超長刷螢幕**：巢狀 template（`std::map<std::string, std::vector<...>>`）。`set print frame-arguments scalars` 精簡，或 frame filter（Ch 27）。
7. **解構函式有兩三個版本**：C++ 編譯器產生多個 dtor（D0/D1/D2，complete/base/deleting）。`info functions ~Widget` 看到多個是正常的。

## 進階：再往深一層

- **Itanium C++ ABI**：mangling 規則、vtable 佈局、RTTI 結構都由它定義。理解它，你能手動解 vtable、手動讀 RTTI（exploit / 無 printer 時）。
- **vtable hijacking（資安）**：改 vptr 指向偽造的 vtable 是經典 C++ exploit。GDB 看 `_vptr` 是否被改、`info vtbl` 驗證——這串到 kernel_pwn / pentest 課程。
- **`maint print vtbl`**：maintenance 版的 vtable 檢視。
- **xmethod（Ch 28）救最佳化**：`-O2` 下 `print vec.at(3)` 不能 inferior call（inline 了），xmethod 用 Python 算。
- **lambda 與 closure**：lambda 是匿名的 functor class，GDB 顯示成 `{__lambda...}`，捕獲的變數是它的成員。`ptype` 一個 lambda 看捕獲。
- **`std::function` / 虛擬分派的間接**：debug callback 地獄時，追 `std::function` 內部的 target。
- **coroutine（C++20）**：協程的 frame 在 heap，debug 較特殊，GDB 14+ 有改進支援。

## 動手練習

1. 對 `cpp_demo.cpp`，`info functions compute` 看兩個 overload，用 `break 'compute(int)'` 精確下斷。
2. `print *w` 觀察 `_vptr` 指向 Button 的 vtable；`info vtbl w` 列出 vtable。
3. `set print object on` 前後各 `print w`，看宣告型別 vs 實際型別的差異。
4. 在 `Button::area` 裡 `print *this`、`info args`，理解隱藏的 this 參數。
5. 手動看一個 `std::vector` 的內部（`_M_start`、`_M_finish`），用 `@` 印出元素、算 size——不靠 printer。
6. 寫一個會 throw 的程式，`catch throw` + `bt` 找 throw 點。
7. 故意改 `w` 的 `_vptr`（`set var` 一個假位址），`info vtbl` 看 GDB 怎麼顯示——理解 vtable hijacking。

## 本章重點整理

- C++ 在中間塞了編譯器魔法：mangling、template 實例化、vtable、RTTI、exception。
- overload 下斷要消歧義（`break 'f(int)'`）；template 只有被實例化的版本存在。
- `print *obj` 的 `_vptr` 揭示實際型別；`info vtbl` 列出虛擬函式表。
- `set print object on` 用 RTTI 顯示多型物件的實際型別（debug 繼承必開）。
- C++ 方法有隱藏 `this`（method 裡的 `$rdi`）；`print *this` / `info args` 看物件。
- STL 漂亮顯示靠 libstdc++ pretty-printer（Ch 30）；失效時手動讀內部指標。

## 自我檢核

- [ ] overload 函式怎麼精確下斷？為什麼有些 template 函式下不了斷？
- [ ] 怎麼從一個基底類別指標看出物件的「實際」型別？要開什麼？
- [ ] `info vtbl` 給你什麼？vtable hijacking 是什麼、怎麼用 GDB 觀察？
- [ ] C++ 方法的 `this` 在哪？怎麼看？
- [ ] STL 容器顯示成一坨指標時，可能是什麼問題？怎麼手動看內容？

## 延伸閱讀

### 官方文件

- **[GDB Manual: C Plus Plus](https://sourceware.org/gdb/current/onlinedocs/gdb/C-Plus-Plus-Expressions.html)** 與 **[Print Settings — set print object](https://sourceware.org/gdb/current/onlinedocs/gdb/Print-Settings.html)**
  - **讀哪裡**：C++ 表示式、overload 消歧義、`set print object`、`info vtbl`。
  - **和本章的關聯**：本章 C++ 功能的權威。

### 規格

- **[Itanium C++ ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi.html)**
  - **讀哪裡**：Mangling（§5.1）、Virtual Tables（§2.5）、RTTI。
  - **和本章的關聯**：mangling/vtable/RTTI 的權威；資安/逆向要手解這些時必讀。
  - **注意**：很硬，當 reference 查需要的部分。

### 部落格 / 文章

- **[Demangling in GDB and how vtables work](https://shaharmike.com/cpp/vtable-part1/)** — Shahar Mike（C++ vtable 系列）
  - **這篇說什麼**：vtable 的記憶體佈局與虛擬分派機制，含 GDB 觀察。
  - **為什麼值得讀**：把本章的 vtable 講到 byte 級；理解多型底層的最佳資源。

下一章專攻 STL 的漂亮顯示——啟用、客製、修復 pretty-printer，並為自訂容器寫 printer。

→ [Ch 30 C++ STL pretty-printer 實戰](./30-stl-pretty-printers.md)
