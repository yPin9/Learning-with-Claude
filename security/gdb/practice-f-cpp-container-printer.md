# 練習 F — 替自訂 C++ 容器寫 printer + xmethod

> **目標**：綜合 Part 6（C++ debug、STL printer、xmethod）與 Part 5（pretty-printer 框架），為一個自訂 C++ template 容器寫出完整的 debug 支援——pretty-printer（漂亮顯示）+ xmethod（`size()`/`operator[]` 用 Python 算）+ auto-load（自動生效）。完成後你的容器 debug 體驗會和 `std::vector` 一模一樣。

## 背景與動機

你在團隊裡寫了一個高效能的自訂容器（ring buffer、small vector、intrusive list…）。同事 debug 時 `print mycontainer` 看到一坨內部指標，罵聲連連。專業的做法是**附帶 pretty-printer + xmethod**，讓你的容器 debug 起來和 STL 一樣舒服。這個練習讓你做這件事——這是「寫給人用的程式庫」的標誌，也是 Final Project 「為特定資料結構提供 debug 支援」的演練。

## 目標容器

```cpp
// ringbuf.cpp — g++ -g -O0 -std=c++17 ringbuf.cpp -o ringbuf
#include <cstddef>
#include <stdexcept>
#include <cstdio>

template <typename T>
class RingBuffer {
    T*     data_;
    size_t cap_;
    size_t head_;     // 最舊元素的索引
    size_t size_;     // 目前元素數
public:
    explicit RingBuffer(size_t cap)
        : data_(new T[cap]), cap_(cap), head_(0), size_(0) {}
    ~RingBuffer() { delete[] data_; }

    void push(const T& v) {
        size_t tail = (head_ + size_) % cap_;
        data_[tail] = v;
        if (size_ < cap_) size_++;
        else head_ = (head_ + 1) % cap_;   // 滿了就覆蓋最舊
    }
    T& operator[](size_t i) {
        if (i >= size_) throw std::out_of_range("ring");
        return data_[(head_ + i) % cap_];
    }
    size_t size() const { return size_; }
    size_t capacity() const { return cap_; }
};

int main() {
    RingBuffer<int> rb(4);
    rb.push(10); rb.push(20); rb.push(30);
    rb.push(40); rb.push(50);   // 50 覆蓋 10，環繞
    printf("%zu\n", rb.size()); // ← break here：此時邏輯順序是 20,30,40,50
    return 0;
}
```

關鍵：RingBuffer 的**儲存順序 ≠ 邏輯順序**。環繞後，記憶體裡是 `{50,20,30,40}`（50 覆蓋了 head 位置），但邏輯上 `rb[0..3]` 是 `{20,30,40,50}`。printer 必須處理這個環形索引——這正是「為什麼自訂容器需要自訂 printer」的核心。

## 任務規格

實作三樣，放進一個 `ringbuf-gdb.py`：

1. **pretty-printer**：`print rb` 顯示 `RingBuffer of length 3 = {20, 30, 40, 50}`（**邏輯順序**，不是記憶體順序）。
2. **xmethod**：`print rb[2]` 和 `print rb.size()` 用 Python 算（不 inferior call），且 `-O2` 下也能用。
3. **泛型**：對 `RingBuffer<int>`、`RingBuffer<double>` 等都生效（regex 比對 template）。
4. **（加分）auto-load**：放成 `ringbuf-gdb.py` 讓它自動載入。

### 驗收標準

- [ ] `print rb` 顯示**邏輯順序**的元素（環繞正確），不是記憶體順序
- [ ] `print rb.size()` 用 xmethod，回傳正確值
- [ ] `print rb[2]` 用 xmethod，回傳邏輯索引 2 的元素（不是記憶體索引 2）
- [ ] `RingBuffer<double>` 也能正確顯示（泛型）
- [ ] `-O2` 編譯時 `print rb[2]` 仍可用（xmethod 不靠 inferior call）
- [ ] 處理空 buffer、損壞狀態（防崩潰）

## 期望輸出範例

```
(gdb) print rb
$1 = RingBuffer of length 4 = {20, 30, 40, 50}     # 邏輯順序！

(gdb) print rb.size()
$2 = 4

(gdb) print rb[0]
$3 = 20            # 邏輯索引 0 = 最舊 = 20（不是記憶體的 50）

(gdb) print rb[2]
$4 = 40
```

## 如果你卡住了

1. **怎麼算邏輯順序？** 元素 i 的記憶體位置 = `(head_ + i) % cap_`。printer 的 children 要對 `i in range(size_)` 算這個。
2. **怎麼拿 template 型別 T？** `val.type.template_argument(0)`（Ch 23）。但其實 printer 走訪 `data_[idx]` 時 GDB 已知道元素型別，不一定需要顯式拿 T。
3. **xmethod 的 operator[] 怎麼寫？** XMethodWorker 的 `__call__(self, obj, i)` 回傳 `obj["data_"][(head+i)%cap]`。
4. **regex 怎麼比對 template？** `^RingBuffer<.*>$`（Ch 30）。
5. **-O2 下測試 printer？** printer 讀的是欄位（data_/head_/size_/cap_），這些是物件記憶體，即使最佳化通常還在（除非整個物件被優化掉）。xmethod 取代 operator[] 的 inferior call。

## 實作步驟建議

### Step 1：pretty-printer（環形順序）

寫 `RingBufferPrinter`，`children()` 用 `(head_ + i) % cap_` 算邏輯順序。

### Step 2：測試環繞

確認 push 超過 cap 環繞後，printer 顯示正確的邏輯順序（不是記憶體順序）。

### Step 3：xmethod size() 與 operator[]

寫 XMethodMatcher + Worker，`size()` 回 `size_`，`operator[]` 回邏輯索引的元素。

### Step 4：泛型 + 註冊

用 `RegexpCollectionPrettyPrinter` regex 比對 `RingBuffer<.*>`，註冊 printer 與 xmethod。測 `RingBuffer<double>`。

### Step 5：auto-load + 健壯性

包 try/except 防壞狀態；放成 `ringbuf-gdb.py` 測 auto-load。

## 完整參考解答

**自己做到 Step 3 再看。**

<details>
<summary>點開完整實作</summary>

```python
# ringbuf-gdb.py — RingBuffer 的 pretty-printer + xmethod
import gdb
import gdb.printing
import gdb.xmethod

# ---------- pretty-printer ----------
class RingBufferPrinter:
    def __init__(self, val):
        self.val = val
    def to_string(self):
        try:
            size = int(self.val["size_"])
            cap = int(self.val["cap_"])
            return f"RingBuffer of length {size} (cap {cap})"
        except gdb.error:
            return "RingBuffer <invalid>"
    def children(self):
        try:
            size = int(self.val["size_"])
            cap  = int(self.val["cap_"])
            head = int(self.val["head_"])
            data = self.val["data_"]
            for i in range(min(size, cap)):       # 防壞：上限 cap
                idx = (head + i) % cap            # 環形 → 邏輯順序！
                yield (f"[{i}]", data[idx])
        except gdb.error:
            yield ("<error>", "cannot read")
    def display_hint(self):
        return "array"

def build_pp():
    pp = gdb.printing.RegexpCollectionPrettyPrinter("ringbuf")
    pp.add_printer("RingBuffer", "^RingBuffer<.*>$", RingBufferPrinter)
    return pp

# ---------- xmethod: size() ----------
class SizeWorker(gdb.xmethod.XMethodWorker):
    def get_arg_types(self): return None
    def get_result_type(self, obj): return gdb.lookup_type("unsigned long")
    def __call__(self, obj):
        return obj["size_"]

# ---------- xmethod: operator[] ----------
class IndexWorker(gdb.xmethod.XMethodWorker):
    def get_arg_types(self):
        return gdb.lookup_type("unsigned long")    # 一個 size_t 參數
    def get_result_type(self, obj, idx):
        # 元素型別 = data_ 指標的 target
        return obj["data_"].type.target()
    def __call__(self, obj, idx):
        size = int(obj["size_"]); cap = int(obj["cap_"]); head = int(obj["head_"])
        i = int(idx)
        if i >= size:
            raise gdb.GdbError("RingBuffer index out of range")
        real = (head + i) % cap
        return obj["data_"][real]                  # 邏輯索引 → 記憶體索引

class RingBufferMatcher(gdb.xmethod.XMethodMatcher):
    def __init__(self):
        super().__init__("RingBuffer")
        self.methods = []
    def match(self, class_type, method_name):
        # class_type.tag 對 template 是 "RingBuffer<int>" 之類
        if class_type.tag is None or not class_type.tag.startswith("RingBuffer<"):
            return None
        if method_name == "size":
            return SizeWorker()
        if method_name == "operator[]":
            return IndexWorker()
        return None

# ---------- 註冊 ----------
def register(objfile):
    gdb.printing.register_pretty_printer(objfile, build_pp(), replace=True)
    gdb.xmethod.register_xmethod_matcher(objfile, RingBufferMatcher(), replace=True)

# auto-load 時 objfile 是當前載入的；手動 source 時用 None（全域）
register(gdb.current_objfile())
print("ringbuf-gdb: pretty-printer + xmethods registered")
```

用法（手動）：

```
(gdb) source ringbuf-gdb.py
(gdb) break ringbuf.cpp:38
(gdb) run
(gdb) print rb
$1 = RingBuffer of length 4 (cap 4) = {[0] = 20, [1] = 30, [2] = 40, [3] = 50}
(gdb) print rb.size()
$2 = 4
(gdb) print rb[0]
$3 = 20
(gdb) print rb[2]
$4 = 40
```

**解答說明**：

- **環形順序是核心**：`children()` 和 `operator[]` 都用 `(head + i) % cap` 把邏輯索引轉成記憶體索引。這就是「為什麼自訂容器需要自訂 printer」——GDB 不可能猜到你的環形邏輯，記憶體順序 `{50,20,30,40}` 對使用者毫無意義。
- **xmethod 取代 operator[]**：`IndexWorker.__call__` 直接算，不呼叫真的 `operator[]`（那會 inferior call、會檢查邊界 throw、`-O2` 下還 inline 掉）。`get_result_type` 用 `data_.type.target()` 拿元素型別。
- **泛型**：regex `^RingBuffer<.*>$` + `class_type.tag.startswith("RingBuffer<")` 對所有 `RingBuffer<T>` 生效。
- **健壯性**：`min(size, cap)` 防壞狀態、try/except 防讀崩。
- **auto-load**：`gdb.current_objfile()` 在 auto-load 時是載入的 objfile；放成 `ringbuf-gdb.py`（對應 binary 名）+ safe-path（Ch 19）就自動生效。

**用到的 API**：pretty-printer 框架（Ch 26）、RegexpCollectionPrettyPrinter（Ch 30）、xmethod（Ch 28）、template_argument/type.target（Ch 23）、auto-load（Ch 19）。這個練習把 Part 5/6 的容器 debug 支援整套串起來。

</details>

## 測試用例

| 操作 | 預期 |
|---|---|
| `print rb`（環繞後）| 邏輯順序 `{20,30,40,50}`，非記憶體順序 |
| `print rb.size()` | 4（xmethod） |
| `print rb[0]` | 20（邏輯索引，最舊） |
| `print rb[2]` | 40 |
| `RingBuffer<double>` | 正確顯示（泛型） |
| `-O2` 編譯 `print rb[1]` | 仍可用（xmethod） |
| 空 buffer | `length 0 = {}`，不崩 |

## 延伸挑戰（加分）

1. **iterator 支援**：如果 RingBuffer 有 `begin()`/`end()`，讓 printer 用 iterator 走訪（更像 STL 的做法）。
2. **map hint**：做一個自訂的 key-value 容器，用 `display_hint() == "map"` 顯示成 `{k -> v}`。
3. **巢狀**：`RingBuffer<std::vector<int>>`——確認 printer 巢狀展開正確（內層用 STL printer）。
4. **type printer**：用 type printer 讓 `RingBuffer<int, std::allocator<int>>` 這種長型別名顯示成簡短的 `RingBuffer<int>`。
5. **auto-load 實戰**：真的設好 safe-path，把 `.py` 放對位置，重開 GDB 確認零設定自動生效——體驗發布「自帶 debug 支援的程式庫」。
6. **對照 STL**：把你的 RingBuffer printer/xmethod 和 libstdc++ 的 `StdVectorPrinter`/`VectorWorker` 並排讀，看工業級寫法的差異。

## 自我檢核

- [ ] 我理解為什麼自訂容器的「記憶體順序 ≠ 邏輯順序」需要自訂 printer
- [ ] 我能寫泛型（template）pretty-printer，用 regex 比對 `Name<.*>`
- [ ] 我能寫 xmethod 取代 `size()`/`operator[]`，避免 inferior call
- [ ] 我能用 auto-load 讓 debug 支援零設定生效
- [ ] 我能讓自訂容器的 debug 體驗等同 STL

Part 6 完成——你能 debug 真實世界的 C++/Rust/Go 與最佳化 binary。Part 7 轉向「不在你面前的」程式：core dump 事後分析、reverse debugging 時間旅行、rr、遠端與嵌入式。

→ [Ch 33 Core dump 事後分析](./33-core-dumps.md)
