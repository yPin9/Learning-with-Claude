# Ch20: Exceptions vs Error Codes

C 只能 error code，C++ 兩邊都能。本章梳理現代實務怎麼選。

## 20.1 Exception 基礎

### throw / try / catch
```cpp
#include <stdexcept>

void validate(int x) {
    if (x < 0) throw std::invalid_argument("x must be non-negative");
}

int main() {
    try {
        validate(-1);
    } catch (const std::invalid_argument& e) {
        std::cout << "caught: " << e.what() << '\n';
    } catch (const std::exception& e) {
        std::cout << "general: " << e.what() << '\n';
    } catch (...) {
        std::cout << "unknown\n";
    }
}
```

### 標準異常層次
```
std::exception
├── std::logic_error
│   ├── std::invalid_argument
│   ├── std::domain_error
│   ├── std::length_error
│   └── std::out_of_range
├── std::runtime_error
│   ├── std::range_error
│   ├── std::overflow_error
│   └── std::underflow_error
└── std::bad_alloc
```

**自訂異常繼承 `std::exception`**：
```cpp
class ParseError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;   // 繼承 constructor
};

throw ParseError{"bad input"};
```

### Catch 原則
- **Catch by reference**（`const T&`）——避免 slicing、避免複製
- **由具體到一般**排序（子類 catch 寫在父類前）
- `catch(...)` 接「任何」exception，但拿不到資訊

## 20.2 Exception 怎麼和 RAII 共舞

RAII 物件在 stack unwinding 時自動解構。

```cpp
void f() {
    auto file = std::ofstream{"a.txt"};   // RAII
    std::lock_guard lk{mtx};              // RAII
    
    do_something_that_throws();
    // throw 時：
    // 1. lk 解構 → unlock
    // 2. file 解構 → close
    // 3. exception 繼續往上
}
```

**這是 C++ exception 能 work 的關鍵**。C 沒有 RAII，throw 會 leak 一切。

## 20.3 `noexcept`

宣告函式「不會 throw」：
```cpp
int add(int a, int b) noexcept { return a + b; }

void clean_up() noexcept;
```

### 為什麼重要
1. **優化**：編譯器知道不會 throw，可省掉 unwinding 機制
2. **move operations**：標 noexcept 的 move 會被 `std::vector` 等容器優先用（Ch5）
3. **文件**：明確意圖

### 違規後果
```cpp
void f() noexcept {
    throw std::runtime_error{"oops"};   // ⚠️ 直接 std::terminate()
}
```

**違反 noexcept 會 terminate**，不是崩潰處理。所以標 noexcept 要有把握。

### 條件 noexcept
```cpp
template <typename T>
void f(T x) noexcept(std::is_nothrow_copy_constructible_v<T>) {
    T copy = x;
}
```

「這個函式 noexcept 當且僅當 T copy 不會 throw」。進階用法。

### 實務建議
- **Destructor**：**總是** `noexcept`（預設就是）
- **Move constructor / assignment**：儘量 `noexcept`
- **Swap**：通常 `noexcept`
- **簡單 getter**：可以 `noexcept`
- **其他**：看情況，不要濫標

## 20.4 Exception 的性能

**沒 throw 時**：現代實作（zero-cost exception handling）幾乎零 overhead。
**throw 時**：很貴（分配 exception、unwind stack、match handler），可能是同等 if-error 的 100-1000 倍。

結論：
- **正常 path 不受影響**
- **錯誤 path 貴**——不要拿 exception 當「流程控制」（像 Python 的 `for/else`）

## 20.5 何時用 exception？

### 適合 exception
- **無法本地處理的失敗**：OOM、文件系統錯、解析失敗
- **Constructor 失敗**：constructor 沒回傳值，唯一合理失敗方式是 throw
- **失敗很罕見**

### 不適合 exception
- **預期會發生的失敗**：user input 錯、找不到 key、parsing 常態性失敗
- **性能 critical 的 loop**
- **跨語言邊界**（Python bindings、C API）
- **embedded / 無 heap** 環境
- **Google / 遊戲業 code base**（常完全禁 exception）

## 20.6 Error code 的現代寫法

### `std::error_code` (C++11)
```cpp
#include <system_error>

void open(std::error_code& ec);

std::error_code ec;
open(ec);
if (ec) {
    std::cout << "error: " << ec.message() << '\n';
}
```

標準庫檔案系統 API 支援 `error_code` overload。

### `std::expected<T, E>` (C++23)
見 Ch18。這是現代首選。

```cpp
std::expected<int, ParseError> parse(std::string_view s);

if (auto r = parse("42"); r) {
    use(*r);
} else {
    log(r.error());
}
```

## 20.7 混合策略（實務常見）

```cpp
// API 公開用 expected（caller 可選擇處理）
std::expected<Config, std::string> load_config(std::string_view path);

// Constructor 用 throw（沒別的選擇）
class Connection {
public:
    Connection(std::string host) {
        if (!try_connect(host)) throw std::runtime_error{"can't connect"};
    }
};

// 內部遞迴/迴圈可以用 error code（性能）
```

## 20.8 Exception Safety 等級

Code 對 exception 的保證等級（Abrahams guarantees）：

1. **No-throw guarantee**：保證不 throw。最強。（`noexcept`）
2. **Strong guarantee**：throw 時，物件狀態不變（transactional）。
3. **Basic guarantee**：throw 時，物件合法但狀態可能變（no leak、no corruption）。
4. **No guarantee**：啥都不保證。避免。

**現代 C++ 的預設要至少做到 basic guarantee**。RAII 讓這件事容易。

### Strong guarantee 範例
```cpp
class Stack {
    std::vector<int> data_;
public:
    void push(int x) {
        data_.push_back(x);   // vector::push_back 有 strong guarantee
    }
};
```

### Copy-and-swap idiom 給 strong guarantee
```cpp
MyClass& operator=(MyClass other) {   // copy（可能 throw）
    swap(*this, other);               // noexcept
    return *this;
}                                      // 舊的隨 other 解構
```

## 20.9 從 destructor 或 noexcept 函式「逃脫」

```cpp
~Foo() {
    maybe_throw();   // ❌ 如果 throw，terminate
}

// 要麼吸收
~Foo() noexcept {
    try { maybe_throw(); } catch (...) { /* log */ }
}
```

解構式和 noexcept 函式要確保不逃 exception。

## 20.10 跨 thread / async 的 exception

```cpp
auto f = std::async([] {
    throw std::runtime_error{"bg"};
});

try {
    f.get();    // 在 get 時 exception 被 rethrow
} catch (const std::exception& e) { }
```

`std::future` 會捕 exception，在 `.get()` rethrow。

## 20.11 現代 C++ 錯誤處理決策樹

```
這個失敗是...
├─ 程式 bug (invariant 破壞)
│   └─ assert / terminate / throw (系統性回歸)
├─ 無法從本地恢復 (OOM / IO 災難)
│   └─ throw
├─ Constructor 失敗
│   └─ throw (無其他選擇)
└─ 使用者/資料的預期失敗
    ├─ 有 C++23 → std::expected
    ├─ 沒有     → std::optional、std::variant<T, Error>、out-param + bool
    └─ 性能極致 → 明確 error code
```

## 20.12 常見錯誤

### 錯誤 1：catch by value
```cpp
try { ... }
catch (std::exception e) { }     // ❌ slicing
catch (const std::exception& e) { }  // ✅
```

### 錯誤 2：catch 順序錯
```cpp
try { ... }
catch (const std::exception& e) { }     // 這裡吃掉一切
catch (const std::runtime_error& e) { } // ❌ 永遠不會執行
```

### 錯誤 3：空 catch
```cpp
try { ... } catch (...) { }      // ❌ 吞 exception 不留痕跡
```

### 錯誤 4：throw pointer
```cpp
throw new std::runtime_error{"bad"};   // ❌ 誰 delete？
throw std::runtime_error{"bad"};       // ✅
```

### 錯誤 5：用 exception 當流程控制
```cpp
try {
    for (auto x : v) if (x == target) throw Found{x};
} catch (const Found& f) { /* 處理 */ }
// ❌ 貴且難讀。用 std::find 或 std::ranges::find。
```

## 20.13 練習

1. 寫個 `class TempFile`，在 constructor 裡建檔，失敗 throw。在 destructor 裡刪檔，確保不 throw。
2. 重構一個返回 `bool` + out-param 的 C API 成 `std::expected<T, E>` 版本。

## 本章重點
- Exception + RAII 是 C++ 資源管理的基石
- Destructor 和 move 儘量 `noexcept`
- **預期失敗用 `expected`/`optional`**，**罕見/致命用 exception**
- Catch by const reference，子類先 catch
- 至少做到 basic exception safety
- 不同 code base 文化不同（有些全禁 exception），看團隊規範
