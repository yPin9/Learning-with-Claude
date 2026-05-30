# Ch 30 — C++ STL pretty-printer 實戰

> **目標**：讓 STL 容器在 GDB 裡漂亮顯示——啟用 libstdc++ 的內建 printer、診斷「為什麼沒生效」、客製顯示、為 template 容器寫泛型 printer。這是 Ch 26 pretty-printer 框架在最常見場景（STL）的實戰落地。

> **環境**：GDB 13/14，Linux x86_64，g++（libstdc++）、選配 clang++（libc++），`-g -O0 -std=c++17`。

## 為什麼 STL printer 值得專章

C++ 工程師 90% 的時間在跟 STL 容器打交道。沒有 pretty-printer，`print myvec` 是這樣：

```
$1 = {<std::_Vector_base<int, std::allocator<int>>> = {
  _M_impl = {<std::allocator<int>> = {...}, _M_start = 0x..., 
  _M_finish = 0x..., _M_end_of_storage = 0x...}}, <No data fields>}
```

有 printer 是這樣：

```
$1 = std::vector of length 3, capacity 4 = {10, 20, 30}
```

天差地遠。這章確保你的 STL 永遠是後者，並教你客製與擴充。

## libstdc++ printer 怎麼來的

承 Ch 19 的 auto-load 與 Ch 26 的 printer 框架：libstdc++ 附帶一組 Python printer（`libstdcxx/v6/printers.py`），透過 `libstdc++.so.6.x.x-gdb.py` 在你 debug C++ 程式時自動載入。

確認它載入了：

```
(gdb) info pretty-printer
global pretty-printers:
  builtin ...
libstdc++-v6:
  std::vector
  std::map
  std::string
  ...                          # 看到這串 = printer 已載入
```

如果 `info pretty-printer` 沒有 `libstdc++-v6`，printer 沒載入——下節診斷。

## 診斷：printer 沒生效

最常見的痛點。`print myvec` 顯示一坨內部指標 = printer 沒載入。排查順序：

```
(gdb) info pretty-printer        # 1. 有沒有 libstdc++-v6？沒有 → 沒載入
(gdb) info auto-load python-scripts   # 2. -gdb.py 載入了嗎？被拒絕？
(gdb) show auto-load safe-path    # 3. safe-path 包含 libstdc++ 的路徑嗎？
```

常見原因與解法：

1. **safe-path 拒絕**（Ch 19）：`-gdb.py` 在不信任路徑。`add-auto-load-safe-path /usr/lib`（或 distro 的 libstdc++ 路徑）。
2. **GDB 找不到 python 模組**：libstdc++ 的 `printers.py` 路徑不在 GDB 的 python path。distro 通常設好；自編 GDB/libstdc++ 可能要手動：
   ```
   (gdb) python
   import sys
   sys.path.insert(0, "/usr/share/gcc-12/python")
   from libstdcxx.v6.printers import register_libstdcxx_printers
   register_libstdcxx_printers(None)
   end
   ```
3. **static link / 奇怪環境**：static 連結的程式沒有 libstdc++.so，auto-load 找不到。手動 register（如上）。
4. **用的是 libc++（clang）不是 libstdc++**：libc++ 有自己的 printer（在 LLVM 裡），要另外裝/載入。

## 看各種 STL 容器

printer 生效後：

```cpp
// stl_demo.cpp — g++ -g -O0 -std=c++17
#include <vector>
#include <map>
#include <string>
#include <memory>
#include <unordered_map>
int main() {
    std::vector<int> v = {10, 20, 30};
    std::map<std::string,int> m = {{"a",1},{"b",2}};
    std::string s = "hello";
    auto sp = std::make_shared<int>(42);
    std::unordered_map<int,int> um = {{1,100},{2,200}};
    return v.size();   // break here
}
```

```
(gdb) print v
$1 = std::vector of length 3, capacity 3 = {10, 20, 30}
(gdb) print m
$2 = std::map with 2 elements = {["a"] = 1, ["b"] = 2}
(gdb) print s
$3 = "hello"
(gdb) print sp
$4 = std::shared_ptr<int> (use count 1, weak count 0) = {get() = 0x... }
(gdb) print *sp
$5 = 42
(gdb) print um
$6 = std::unordered_map with 2 elements = {[2] = 200, [1] = 100}
```

巢狀也能展開：

```
(gdb) print nested        # vector<map<string, vector<int>>>
$7 = std::vector of length 2 = {std::map with 1 element = {...}, ...}
```

## 配合 xmethod：最佳化下仍能索引

承 Ch 28：`-O2` 下 `print v[1]` 不能 inferior call（`operator[]` inline 了）。libstdc++ 的 xmethod 讓你仍能：

```
(gdb) print v[1]          # 用 xmethod 算，不呼叫真方法
$8 = 20
(gdb) print v.size()      # xmethod
$9 = 3
(gdb) print sp.get()      # shared_ptr 的 get()
```

xmethod 也是 auto-load 來的（`xmethods.py`）。`info xmethod` 看載入的 xmethod。

## 客製 STL 顯示

控制 printer 行為：

```
(gdb) set print pretty on            # 巢狀容器換行縮排
(gdb) set print elements 20          # 大容器只印前 20 個
(gdb) set print elements 0           # 印全部（小心超大容器）
(gdb) disable pretty-printer global libstdc++-v6   # 暫時關掉看原始內部
(gdb) print v                        # 現在看到原始 _M_start... 結構
(gdb) enable pretty-printer global libstdc++-v6
```

`disable pretty-printer` 暫時關掉看原始結構——debug printer 本身、或 printer 顯示可疑時驗證底層。

## 為自訂容器寫 printer（template 泛型）

承 Ch 26，幫你自己的 template 容器寫 printer。關鍵是用 `template_argument` 拿型別參數：

```cpp
// 假設你有 template<typename T> class RingBuffer { T* buf; int head, size, cap; };
```

```python
# ring_printer.py
import gdb
import gdb.printing

class RingBufferPrinter:
    def __init__(self, val):
        self.val = val
    def to_string(self):
        return f"RingBuffer of length {int(self.val['size'])}"
    def children(self):
        size = int(self.val["size"])
        cap = int(self.val["cap"])
        head = int(self.val["head"])
        buf = self.val["buf"]
        for i in range(size):
            idx = (head + i) % cap            # 環形索引邏輯
            yield (f"[{i}]", buf[idx])
    def display_hint(self):
        return "array"

def build():
    pp = gdb.printing.RegexpCollectionPrettyPrinter("mylib")
    # regex 比對 template：RingBuffer<任何型別>
    pp.add_printer("RingBuffer", "^RingBuffer<.*>$", RingBufferPrinter)
    return pp

gdb.printing.register_pretty_printer(None, build(), replace=True)
```

```
(gdb) source ring_printer.py
(gdb) print my_ring
$1 = RingBuffer of length 3 = {[0] = 5, [1] = 8, [2] = 13}
```

重點：

- regex `^RingBuffer<.*>$` 比對所有 `RingBuffer<T>` 實例（泛型）。
- `children()` 實作環形索引邏輯——printer 可以有任意計算，把複雜內部結構翻成直覺的元素序列。
- 要拿 T 型別：`self.val.type.template_argument(0)`（Ch 23）。

這正是練習 F 要做的——為自訂容器寫 printer + xmethod，讓它 debug 體驗等同 STL。

## 踩雷集錦

1. **printer 沒生效顯示一坨指標**：`info pretty-printer` 沒 libstdc++-v6。多半是 safe-path 或 python path 問題（見診斷節）。
2. **static binary 沒 printer**：沒有 libstdc++.so 可 auto-load。手動 register。
3. **大容器 print 卡死**：百萬元素的 vector，`set print elements 200`（預設）會截斷，但 `set print elements 0` 會印爆。設合理上限。
4. **巢狀容器顯示混亂**：`set print pretty on` 改善排版。
5. **clang 的 libc++ 用 libstdc++ printer 失敗**：兩套 STL 實作內部不同。libc++ 要用 LLVM 的 printer。
6. **自訂 printer 的 regex 沒比對到 template**：忘了 `<.*>`，`^RingBuffer$` 比對不到 `RingBuffer<int>`。
7. **printer 走訪壞掉的容器崩潰**：debug 一個記憶體損壞的 vector，printer 讀 `_M_start..._M_finish` 可能讀到天文數字長度。包 try/except + 上限（Ch 26）。

## 進階：再往深一層

- **`std::variant` / `std::optional` / `std::any` 的 printer**：C++17 的這些型別 libstdc++ 也有 printer，顯示當前持有的型別/值。
- **debug 損壞的容器**：當 vector 的 `_M_finish < _M_start`（被踩壞），printer 可能算出負/巨大長度。手動 `disable pretty-printer` 看原始三指標診斷。
- **libc++ printers**：clang 生態的 STL printer，在 LLVM source 的 `libcxx/utils/gdb/`。跨編譯器專案要兩套都備。
- **template_argument 的多參數**：`std::map<K,V>` 用 `template_argument(0)` 拿 K、`(1)` 拿 V，寫 map printer。
- **printer 的效能**：大容器用 generator（lazy）+ `print elements` 限制，避免一次建好全部。
- **與 Final Project**：你的插件可以為「程式特定的核心資料結構」附帶 printer，讓使用者 debug 時直接看到語意化內容——這是高品質 debug 工具的標誌。

## 動手練習

1. 對 `stl_demo.cpp`，`info pretty-printer` 確認 libstdc++-v6 載入；如果沒有，照診斷節修好。
2. `print` vector/map/string/shared_ptr/unordered_map，看漂亮顯示。
3. `disable pretty-printer global libstdc++-v6` 後 `print v`，看原始 `_M_start...` 內部結構，再 enable 回來——理解 printer 在翻譯什麼。
4. `-O2` 重編，確認 `print v[1]` 靠 xmethod 仍能用（`info xmethod` 看）。
5. 寫一個自己的 template 容器（如 RingBuffer），為它寫泛型 printer（regex 比對 `<.*>`），`print` 出漂亮結果。
6. 故意 `set var v._M_impl._M_finish = v._M_impl._M_start + 0x1000000`（偽造超大長度），看 printer 怎麼反應，理解損壞容器的風險。

## 本章重點整理

- STL 漂亮顯示靠 libstdc++ 附帶的 pretty-printer（auto-load）；`info pretty-printer` 確認載入。
- 沒生效多半是 safe-path 或 python path 問題；static binary 要手動 register。
- `disable pretty-printer` 暫時看原始內部結構（診斷用）；`set print elements` 控制大容器截斷。
- xmethod 讓最佳化下仍能 `print v[i]`/`v.size()`。
- 自訂 template 容器：regex `^Name<.*>$` 比對泛型，`children()` 實作走訪邏輯，`template_argument` 拿型別參數。

## 自我檢核

- [ ] `print myvec` 顯示一坨指標，你的排查步驟是什麼？
- [ ] STL printer 是怎麼「自動」載入的？static binary 為什麼可能失效？
- [ ] 怎麼暫時看 STL 容器的原始內部結構？什麼時候需要？
- [ ] 最佳化下 `print v[3]` 怎麼還能用？
- [ ] 為自訂 template 容器寫 printer，regex 和 children 各要注意什麼？

## 延伸閱讀

### 官方文件 / 原始碼

- **[libstdc++ printers.py](https://gcc.gnu.org/git/?p=gcc.git;a=blob;f=libstdc%2B%2B-v3/python/libstdcxx/v6/printers.py)**
  - **讀哪裡**：`StdVectorPrinter`、`StdMapPrinter`、`SharedPointerPrinter`。
  - **和本章的關聯**：最權威的 STL printer 範例；寫自訂 printer 的最佳教材。

- **[GDB Manual: Pretty Printing — selecting/disabling](https://sourceware.org/gdb/current/onlinedocs/gdb/Pretty-Printing.html)**
  - **讀哪裡**：info/enable/disable pretty-printer。
  - **和本章的關聯**：診斷與管理 printer 的指令。

### 部落格 / 文章

- **[Debugging STL containers in GDB](https://sourceware.org/gdb/wiki/STLSupport)** — GDB Wiki
  - **這篇說什麼**：手動啟用 STL printer 的步驟（自編環境救命）。
  - **和本章的關聯**：診斷節的權威來源。

下一章把多語言支援延伸到 Rust 與 Go——它們的 runtime 與型別系統帶來各自的 debug 挑戰與工具。

→ [Ch 31 Rust 與 Go 除錯](./31-rust-and-go-debugging.md)
