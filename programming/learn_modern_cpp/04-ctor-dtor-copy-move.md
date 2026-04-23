# Ch4: 建構 / 解構 / 複製 / 移動 (Rule of 0/3/5)

C++ 物件的 5 個「特殊成員函式」：建構、解構、複製建構、複製賦值、**移動建構、移動賦值**（後兩個是 C++11 加的）。

本章先講前 4 個，Ch5 專門講 move。

## 4.1 建構子 (Constructor)

建構子是**建立物件時呼叫**的函式。可以有多個（overloading）。

```cpp
class Point {
    double x_, y_;
public:
    Point() : x_(0), y_(0) {}                 // 預設建構子
    Point(double x, double y) : x_(x), y_(y) {}   // 兩參數
    explicit Point(double v) : x_(v), y_(v) {}    // 單參數
};

Point p1;              // 預設建構
Point p2{3.0, 4.0};    // 兩參數
Point p3{5.0};         // 單參數（explicit）
```

### 成員初始化列表 (Member initializer list)
```cpp
Point(double x, double y) : x_(x), y_(y) {}
//                       ^^^^^^^^^^^^^^^^ 這個冒號後面的
```

**永遠用 initializer list**，別在函式體內賦值：
```cpp
// ❌ 不好
Point(double x, double y) {
    x_ = x;  // 這是 "建構完畢後再 assign"
    y_ = y;
}

// ✅ 好
Point(double x, double y) : x_(x), y_(y) {}
//                          ^ 直接初始化
```

差在哪？對基本型別沒差，但對有 constructor 的成員，initializer list 是 **one-step 初始化**，函式體內是 **default construct + assign**，多一步浪費。而且 `const` 成員和 reference 成員**只能**在 initializer list 初始化。

### `explicit` 重要
```cpp
class Duration {
public:
    Duration(int ms);   // 非 explicit
};

void wait(Duration d);

wait(500);   // ⚠️ 會隱式轉：500 → Duration(500)
```

這看起來方便其實危險。加 `explicit`：
```cpp
explicit Duration(int ms);

wait(500);            // ❌ 錯誤
wait(Duration{500});  // ✅ 明確
```

**規則：單參數 constructor 預設加 `explicit`**，除非你真的想要隱式轉換。

## 4.2 解構子 (Destructor)

```cpp
class File {
    FILE* fp_;
public:
    File(const char* path) : fp_(std::fopen(path, "r")) {}
    ~File() { if (fp_) std::fclose(fp_); }
};
```

規則：
- 一個 class 只能有一個解構子
- 解構子**不能 throw**（Ch3 講過）
- 如果 class 可能被 polymorphically 刪除（用 base pointer delete），base 的解構子要是 **`virtual`**

```cpp
class Base {
public:
    virtual ~Base() = default;   // 重要！
};

class Derived : public Base { /* ... */ };

Base* p = new Derived;
delete p;   // 如果 Base::~Base() 不是 virtual，只會呼叫 Base::~Base()，Derived 部分 leak
```

## 4.3 複製建構與複製賦值

C 沒這概念，C++ 物件預設會「**位元複製**」（naive memcpy），但這對管理資源的類別是災難：

```cpp
class BadString {
    char* data_;
    size_t len_;
public:
    BadString(const char* s) {
        len_ = std::strlen(s);
        data_ = new char[len_ + 1];
        std::strcpy(data_, s);
    }
    ~BadString() { delete[] data_; }
};

BadString a{"hello"};
BadString b = a;     // 預設複製：b.data_ == a.data_（兩個指向同一記憶體！）
// a, b 解構時都 delete 同一塊 → double free
```

解法：寫**複製建構子**和**複製賦值運算子**。

```cpp
class MyString {
    char* data_;
    size_t len_;
public:
    MyString(const char* s) { /* 如上 */ }

    // 複製建構子
    MyString(const MyString& other)
        : len_(other.len_), data_(new char[other.len_ + 1]) {
        std::strcpy(data_, other.data_);
    }

    // 複製賦值運算子
    MyString& operator=(const MyString& other) {
        if (this == &other) return *this;    // 自我賦值防護
        delete[] data_;                       // 釋放舊的
        len_ = other.len_;
        data_ = new char[len_ + 1];
        std::strcpy(data_, other.data_);
        return *this;
    }

    ~MyString() { delete[] data_; }
};
```

注意 `operator=`：
- 回傳 `MyString&`，為了支援 `a = b = c` 鏈式
- 檢查自我賦值（`a = a`）
- 釋放舊的再賦新的

### Copy-and-swap idiom（更優雅的 assignment 寫法）
```cpp
MyString& operator=(MyString other) {   // by value：自動複製進 other
    swap(*this, other);
    return *this;
}                                        // other 解構：釋放舊的（原來 *this 的內容）
```

簡潔、exception-safe、自動處理自我賦值。但需要自訂 `swap`。

## 4.4 Rule of Three / Rule of Five / Rule of Zero

**Rule of Three (pre-C++11)**：如果你要自訂其中一個（解構、複製建構、複製賦值），大概就要三個都自訂。因為它們通常是為了管理「raw 資源」。

**Rule of Five (C++11+)**：再加上 **移動建構、移動賦值**（Ch5）。

**Rule of Zero (現代首選)**：**什麼都不要自訂**。讓成員自己是 RAII 物件（`std::string`、`std::vector`、`std::unique_ptr`...），編譯器自動產生的五大就對了。

```cpp
// Rule of Zero：什麼都不寫
class Config {
    std::string name_;
    std::vector<int> ports_;
    std::unique_ptr<Logger> logger_;
public:
    Config(std::string n) : name_(std::move(n)) {}
    // 不寫解構、不寫複製、不寫移動
    // 編譯器產的版本自動對成員做正確的事
};
```

**95% 的 class 都該走 Rule of Zero**。只有包 C API 的 wrapper 才需要 Rule of Five。

## 4.5 `= default` 與 `= delete`

```cpp
class Foo {
public:
    Foo() = default;                        // 用編譯器預設的
    Foo(const Foo&) = delete;               // 禁用複製
    Foo& operator=(const Foo&) = delete;    // 禁用複製賦值
    ~Foo() = default;
};
```

- `= default`：明確要編譯器產生（可讀性）
- `= delete`：明確禁用，呼叫時編譯錯誤

### 什麼時候該 delete copy？

資源不能共享/複製時：`std::unique_ptr`、`std::mutex`、`std::thread`、檔案 handle wrapper。

## 4.6 編譯器自動產生的規則（要記）

如果你**沒寫**特殊成員函式，編譯器會**可能**自動生成：

| 你寫了... | 編譯器是否自動生成... |
|---|---|
| 無 | 全部五個都生成 |
| 自訂建構子 | 不影響其他（但無預設建構子） |
| 自訂解構子 | 複製仍生成；**移動不生成** ⚠️ |
| 自訂複製 | 移動不生成 ⚠️ |
| 自訂移動 | 複製被 delete ⚠️ |

**關鍵陷阱**：自訂解構子會「殺掉」移動。如果你有個 RAII wrapper 想支援 move，要明確 `= default` move operations。

```cpp
class MyResource {
    Handle h_;
public:
    MyResource() = default;
    ~MyResource() { release(h_); }

    // 明確開啟 move
    MyResource(MyResource&&) = default;
    MyResource& operator=(MyResource&&) = default;

    // 禁用 copy
    MyResource(const MyResource&) = delete;
    MyResource& operator=(const MyResource&) = delete;
};
```

（但實務上你該用 `std::unique_ptr<H, Deleter>` 就省這些。）

## 4.7 初始化方式一覽

C++ 有多種初始化語法，看起來像噪音但各有差別：

```cpp
int a;          // default-init（基本型別是未初始化！）
int b{};        // value-init（基本型別初始化為 0）
int c = 0;      // copy-init
int d(0);       // direct-init
int e{0};       // direct-list-init（**推薦**，禁止窄化轉換）

int f{3.14};    // ❌ 錯誤：3.14 窄化到 int
int g = 3.14;   // ⚠️ 可以（但丟 .14），舊語法不擋
```

**現代 C++ 慣例：`{}` 初始化**（Uniform Initialization）：
```cpp
std::vector<int> v{1, 2, 3};
std::string s{"hello"};
Point p{1.0, 2.0};
```

唯一陷阱：`std::vector` 的 `{n}` 容易和 `(n)` 搞混：
```cpp
std::vector<int> v1(5);      // 5 個 0
std::vector<int> v2{5};      // 1 個元素 "5"
std::vector<int> v3(5, 10);  // 5 個 10
std::vector<int> v4{5, 10};  // 2 個元素 5, 10
```

這是 `{}` 語法唯一要記的例外。

## 4.8 完整範例：一個 RAII wrapper 該長什麼樣

```cpp
#include <utility>

class FileDesc {
    int fd_ = -1;
public:
    FileDesc() = default;

    explicit FileDesc(int fd) : fd_(fd) {}

    ~FileDesc() { if (fd_ >= 0) ::close(fd_); }

    // 禁用複製
    FileDesc(const FileDesc&) = delete;
    FileDesc& operator=(const FileDesc&) = delete;

    // 支援移動
    FileDesc(FileDesc&& other) noexcept : fd_(other.fd_) {
        other.fd_ = -1;
    }
    FileDesc& operator=(FileDesc&& other) noexcept {
        if (this != &other) {
            if (fd_ >= 0) ::close(fd_);
            fd_ = other.fd_;
            other.fd_ = -1;
        }
        return *this;
    }

    int get() const { return fd_; }
    int release() { int tmp = fd_; fd_ = -1; return tmp; }
};
```

這是 Rule of Five 的典型模板。**只包 C API 時才需要這麼寫**；純 C++ 組合用 Rule of Zero。

## 4.9 本章重點

- Initializer list 優於函式體內賦值
- 單參數 constructor 預設 `explicit`
- Base class 有 polymorphic 使用時解構子要 `virtual`
- **Rule of Zero > Rule of Five > Rule of Three**
- 自訂解構子會自動 delete move，要手動 `= default`
- 預設用 `{}` 初始化（Uniform Init），但 `std::vector` 注意 `()` vs `{}`
