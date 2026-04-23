# Ch5: Move Semantics 與 rvalue references

C++11 最大的創舉。理解這章，你就理解現代 C++「為什麼沒有性能懲罰」。

## 5.1 動機：不必要的複製

```cpp
std::vector<int> make_big() {
    std::vector<int> v(1'000'000);
    // ... 填入資料 ...
    return v;
}

std::vector<int> data = make_big();   // 這裡做了幾次複製？
```

naive 的想法：
1. `v` 在 `make_big` 裡建立
2. 回傳時複製一份到 caller
3. 賦值給 `data` 時再複製

**複製百萬個 int 兩次**太貴。C++11 前有 RVO（Return Value Optimization）能消除部分，但不完美。

C++11 的解法：**move**——不複製，而是「**偷走**」內部資源。

```cpp
std::vector<int> data = make_big();
// v 的 heap 資料指標被「轉移」到 data，只是幾個指標的 swap
// v 進入 "valid but unspecified" 狀態，等著被解構
```

## 5.2 lvalue vs rvalue

**lvalue**：有名字的、能取位址的東西。
**rvalue**：暫時物件、運算結果。

```cpp
int x = 10;       // x 是 lvalue
int y = x + 1;    // x+1 是 rvalue，y 是 lvalue

std::string s = "hi";     // s 是 lvalue
std::string t = s + "!";  // s+"!" 是 rvalue
```

粗略判斷：**你可以放在 `=` 左邊的就是 lvalue**（名字來源）。

## 5.3 rvalue reference (`T&&`)

C++11 引入 `T&&`，**只能綁到 rvalue**。

```cpp
void f(int& x);     // 只接 lvalue
void f(int&& x);    // 只接 rvalue

int a = 10;
f(a);       // 呼叫 f(int&)
f(10);      // 呼叫 f(int&&)
f(a + 1);   // 呼叫 f(int&&)
```

這讓我們可以**根據參數是否為「暫時物件」分派不同行為**。

## 5.4 Move constructor / Move assignment

```cpp
class MyString {
    char* data_;
    size_t len_;
public:
    MyString(const char* s) { /* 分配+複製 */ }

    ~MyString() { delete[] data_; }

    // Copy (貴)
    MyString(const MyString& o)
        : len_(o.len_), data_(new char[o.len_ + 1]) {
        std::strcpy(data_, o.data_);
    }

    // Move (便宜！)
    MyString(MyString&& o) noexcept
        : data_(o.data_), len_(o.len_) {
        o.data_ = nullptr;    // 重要：讓 o 處於「空」狀態
        o.len_ = 0;
    }
};
```

Move 的精髓：
- **偷**來源的 pointer / handle
- **把來源的 pointer 設為 null**（或某個明確「空」狀態），讓來源解構時什麼都不做
- 標 `noexcept`（很重要，後面講）

Move assignment 類似：
```cpp
MyString& operator=(MyString&& o) noexcept {
    if (this != &o) {
        delete[] data_;       // 釋放自己舊的
        data_ = o.data_;      // 偷 o 的
        len_ = o.len_;
        o.data_ = nullptr;
        o.len_ = 0;
    }
    return *this;
}
```

## 5.5 `std::move`

`std::move` 不搬任何東西，只是**把 lvalue cast 成 rvalue reference**，讓 overload resolution 選 move 版本。

```cpp
std::string a = "hello";
std::string b = a;              // copy（a 是 lvalue）
std::string c = std::move(a);   // move（cast 成 rvalue）
// a 現在處於 "valid but unspecified" 狀態，
// 可以賦新值或解構，但不該讀它的內容
```

`std::move` 字面上可以理解成 `static_cast<T&&>`。名字取得爛，應該叫 `std::rvalue_cast`。

## 5.6 什麼時候該 move？

**規則 1：把一個之後不再用的物件「交給」別人**。
```cpp
std::string s = "hello";
std::vector<std::string> v;
v.push_back(std::move(s));   // 之後不會再用 s
```

**規則 2：constructor 參數 by value + std::move**。
```cpp
class Config {
    std::string name_;
public:
    Config(std::string name) : name_(std::move(name)) {}
    //     ^^^^^^^^^^^^^^^^^^^ by value
};

Config c1{"server"};           // rvalue：一次 move 進 name（便宜）
std::string n = "db";
Config c2{n};                  // lvalue：複製進 name，再 move（一次 copy + 一次 move）
Config c3{std::move(n)};       // 兩次 move
```

這種「**by value + move**」pattern 在現代 C++ 非常普遍，一個 constructor 同時支援 copy 和 move 兩種調用，不用寫兩個 overload。

**規則 3：回傳 local 變數，不要 `std::move`**。
```cpp
std::string bad() {
    std::string s = "hi";
    return std::move(s);   // ❌ 阻止了 RVO，反而變慢
}

std::string good() {
    std::string s = "hi";
    return s;              // ✅ 編譯器自動 RVO 或 move
}
```

## 5.7 `noexcept` 與 move

**Move operations 強烈建議標 `noexcept`**。

原因：`std::vector` 重新分配時（`push_back` 爆容量），要把舊元素搬到新空間。如果搬到一半 throw，vector 會處於壞狀態。所以：

- Element 的 move 是 `noexcept` → 用 move（快）
- Element 的 move **不是** `noexcept` → **退而用 copy**（慢但安全）

所以忘了加 `noexcept` 就是性能懸崖。

```cpp
class Foo {
public:
    Foo(Foo&&) noexcept = default;             // ✅
    Foo& operator=(Foo&&) noexcept = default;  // ✅
};
```

## 5.8 「Valid but unspecified」狀態

Moved-from 的物件**還存在、還會解構**，但你不該對它的內容有假設。

```cpp
std::string s = "hello";
std::string t = std::move(s);

// s 現在合法但內容未定。這些操作都「可以做」：
s = "world";            // ✅ 重新賦值
s.clear();              // ✅ 操作它
std::cout << s.size();  // ✅ 讀它（但內容不保證）

// 不該做的：
std::cout << s;         // ⚠️ 可能是空字串，可能是 "hello"，實作定
```

**自己的 class 寫 move 時，移走後把來源設到一個明確狀態**（例如 data_ = nullptr），不要留不明狀態。

## 5.9 Forwarding reference (universal reference)

Template 裡的 `T&&` 有特殊含義——可以綁 lvalue 也可以綁 rvalue：

```cpp
template <typename T>
void forward(T&& x) {   // 這裡的 T&& 是 "forwarding reference"
    // ...
}

int a = 10;
forward(a);         // T = int&,  x 是 int&
forward(10);        // T = int,   x 是 int&&
```

這用於「**完美轉發**」——把參數原樣轉給另一個函式：

```cpp
template <typename T>
void wrapper(T&& x) {
    inner(std::forward<T>(x));   // 是 lvalue 就傳 lvalue，是 rvalue 就傳 rvalue
}
```

`std::forward<T>` 和 `std::move` 都是 cast，差別在 forward 保留原本的值類別。

**入門階段你主要讀得懂就好**，自己寫 template 不一定用上。

## 5.10 常見陷阱

### 陷阱 1：在回傳時 `std::move`
```cpp
std::vector<int> f() {
    std::vector<int> v = {1,2,3};
    return std::move(v);   // ❌ 阻止 RVO
}
```

### 陷阱 2：對已經 move 的物件操作
```cpp
auto s = std::string("hi");
auto t = std::move(s);
std::cout << s.size();     // ⚠️ 未定義內容
s.append("world");         // 😐 合法但通常不是你想要的
```

### 陷阱 3：忘了 noexcept
```cpp
MyString(MyString&&) = default;  // 忘了 noexcept
// 放進 vector 可能會退化成 copy
```

### 陷阱 4：把 `const T&&` 當 rvalue reference
```cpp
void f(const std::string&& s);   // ❌ 無意義
// 不能 move 一個 const，這個 signature 幾乎只是「拒絕 lvalue」
```

## 5.11 Move 的整體圖

| 操作 | 做什麼 |
|---|---|
| `T a = b;` （b 是 lvalue） | copy |
| `T a = std::move(b);` | move |
| `T a = f();` （f 回傳 T） | move 或 RVO |
| `v.push_back(x)` | 依 x 是 l/rvalue |
| `v.push_back(std::move(x))` | move |
| `v.emplace_back(args...)` | 直接在 vector 裡建構（無 copy/move） |

## 5.12 `emplace` 系列

很多容器有 `emplace_*`：**就地建構**，跳過臨時物件。

```cpp
std::vector<std::pair<int, std::string>> v;

v.push_back({1, "hello"});          // 先建 pair，再 move 進 vector
v.emplace_back(1, "hello");         // 直接在 vector 裡建 pair
```

對大型物件有意義，小型沒差。先會用 `push_back`，進階時再換 `emplace_back`。

## 5.13 心態總結

- Move 是 C++ 能在「物件有生命週期」的限制下保持性能的關鍵
- 預設用 copy，有明確「搬走」意圖才用 `std::move`
- 包資源的 class 要寫 `noexcept` 的 move constructor
- Rule of Zero：用標準庫容器，move 自動正確

## 5.14 練習

實作一個 `MyVector<T>`（不用管 T 是啥特殊的），支援：
- 預設建構、帶 size 建構
- 解構（delete[]）
- Copy constructor（深複製）
- Move constructor（noexcept，偷 pointer）
- `push_back(const T&)` 和 `push_back(T&&)`

跑這個 demo 確認 move 真的有發生：
```cpp
MyVector<int> a(1000);
MyVector<int> b = std::move(a);   // 應該瞬間完成
```

## 本章重點
- `T&&` rvalue reference 綁暫時物件
- `std::move` 不搬東西，只是 cast
- Move = 偷 pointer + 把來源清空
- Move operations 標 `noexcept`
- 回傳 local 變數不要 `std::move`
- Rule of Zero 最安全：讓成員自動處理
