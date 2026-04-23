# Ch2: References 與 const 正確性

## 2.1 Reference 是什麼？

C 只有 pointer。C++ 多了 **reference**——可以想成「**必定非 null、不能換目標的別名**」。

```cpp
int x = 10;
int& r = x;     // r 是 x 的別名
r = 20;         // x 現在是 20
```

和 pointer 的對比：

| 特性 | Pointer | Reference |
|---|---|---|
| 可以是 null | ✅ | ❌ |
| 可以換目標 | ✅ | ❌（一旦綁定就鎖死） |
| 需要解參考語法 | `*p` | 直接用 |
| 可以宣告後再賦值 | ✅ | ❌（**必須初始化**） |

```cpp
int& r;         // ❌ 錯誤：reference 必須初始化
int* p;         // ✅ 可以（但是 uninitialized）
```

## 2.2 為什麼要 reference？

主要兩個用途：**傳參數不複製**、**給函式回傳「可被修改的東西」**。

### 用法 A：避免複製
```cpp
void print(const std::string& s) {   // 不複製，也不能改
    std::cout << s << '\n';
}

std::string name = "Alice";
print(name);   // 沒複製
```

C 這樣寫要用 `const char*` 或 `const struct*`。C++ 的 `const T&` 更自然。

### 用法 B：讓函式修改 caller 的變數
```cpp
void swap(int& a, int& b) {
    int tmp = a;
    a = b;
    b = tmp;
}

int x = 1, y = 2;
swap(x, y);     // 直接傳，不用 &x, &y
```

## 2.3 三種參數傳遞

```cpp
void by_value(std::string s);           // 複製一份
void by_ref(std::string& s);            // 參考（可改原本的）
void by_const_ref(const std::string& s); // 參考但不能改（推薦預設）
```

**經驗法則**：
- 小型、便宜複製的型別（`int`、`double`、`char`）→ by value
- 大型或不便宜複製的型別（`string`、`vector`、自訂 class）→ **`const T&`**
- 需要修改 caller 的變數 → `T&`
- 想要接管所有權 → by value 或 `T&&`（Ch5 的 move）

## 2.4 `const` 完整指南

C 的 `const` 使用偏弱。C++ 的 `const` 是**設計工具**，要用得精準。

### 變數的 const
```cpp
const int x = 10;
x = 20;         // ❌ 錯誤
```

### Pointer 的 const（容易混）
```cpp
int a = 1, b = 2;
const int* p1 = &a;       // 指向 const int（不能透過 p1 改 a）
int* const p2 = &a;       // const pointer（不能改 p2 指向誰）
const int* const p3 = &a; // 兩者都 const

p1 = &b;       // ✅ 可以換指向
*p1 = 5;       // ❌ 錯誤
*p2 = 5;       // ✅ 可以
p2 = &b;       // ❌ 錯誤
```

讀法（**從右往左讀**）：
- `const int*` → pointer to (const int)
- `int* const` → (const pointer) to int

### Reference 的 const
```cpp
const int& r = x;   // reference to const int
int& const r = x;   // ❌ 語法錯（reference 本來就不能改目標）
```

所以 reference 只有一種：`const T&`。

## 2.5 函式的 `const`

成員函式尾端的 `const`：**我不會修改這個物件**。

```cpp
class Counter {
    int value = 0;
public:
    int get() const { return value; }   // const 方法
    void increment() { ++value; }       // 非 const
};

void print(const Counter& c) {
    c.get();         // ✅ const 物件可以呼叫 const 方法
    c.increment();   // ❌ 不能呼叫非 const 方法
}
```

**規則**：任何「只讀」的 method 都該標 `const`。這叫 **const correctness**，是 C++ 基本功。

## 2.6 常見 const 陷阱

### 陷阱 1：回傳 reference 指到區域變數
```cpp
const std::string& bad() {
    std::string s = "hello";
    return s;                // ❌ s 即將被解構，回傳的是懸垂 reference
}
```

### 陷阱 2：const_cast（**不要用**）
```cpp
const int x = 10;
int& r = const_cast<int&>(x);  // ⚠️ UB if x was declared const
r = 20;
```

`const_cast` 基本上是 code smell。看到要質疑。

### 陷阱 3：`const` + pointer 混淆
```cpp
void f(const std::vector<int*>& v) {
    v.push_back(nullptr);  // ❌ v 是 const，不能 push_back
    *v[0] = 42;            // ✅ pointer 本身不是 const，可以透過它改
}
```

`const std::vector<int*>` = const container of (mutable pointers)。

## 2.7 Reference 生命週期陷阱

```cpp
std::vector<int> make_vec() { return {1, 2, 3}; }

const int& r = make_vec()[0];   // 🤔 暫時物件在哪裡？
std::cout << r;                  // 看起來可以，但...
```

規則：**`const T&` 綁到 rvalue（暫時物件）時，會延長暫時物件壽命到 reference 的 scope 結束**。

```cpp
{
    const auto& first = make_vec()[0];  // ❌ 小心！
    // make_vec() 整個 vector 不被延長，只有 [0] 是 reference 到其中一個元素
    // vector 已經被解構，first 是懸垂 reference
}
```

這題很 tricky，記住：**subscript 或 member access 不會延長外層物件**。安全版本：
```cpp
auto vec = make_vec();
const auto& first = vec[0];   // 這樣就 OK
```

## 2.8 Universal reference 預告

你會看到 `T&&`，意思在兩種情境下不同：
- Template 裡的 `T&&`（T 是模板參數）→ universal reference（Ch5）
- 一般 `int&&`、`std::string&&` → rvalue reference（Ch5）

先別擔心，Ch5 細講。

## 2.9 一個完整範例

```cpp
#include <iostream>
#include <string>
#include <vector>

class Config {
    std::string name_;
    std::vector<int> ports_;
public:
    Config(std::string n, std::vector<int> p)
        : name_(std::move(n)), ports_(std::move(p)) {}

    // 只讀方法全部標 const
    const std::string& name() const { return name_; }
    const std::vector<int>& ports() const { return ports_; }
    size_t port_count() const { return ports_.size(); }

    // 修改方法不標 const
    void add_port(int p) { ports_.push_back(p); }
};

void dump(const Config& c) {    // 傳 const ref
    std::cout << c.name() << ": ";
    for (int p : c.ports()) std::cout << p << ' ';
    std::cout << '\n';
}

int main() {
    Config cfg("server", {80, 443});
    dump(cfg);
    cfg.add_port(8080);
    dump(cfg);
}
```

注意：
- 參數都用 `const T&` 或 by value + `std::move`（Ch5）
- 所有唯讀方法都是 `const`
- getter 回傳 `const T&` 避免複製

## 本章重點
- Reference 是「一定非 null 的別名」，綁定後不能換
- 預設參數傳遞：大型用 `const T&`，小型 by value
- 所有唯讀方法都標 `const` — const correctness 是現代 C++ 基本功
- `const_cast` 看到要警戒
- 暫時物件的生命週期延長有陷阱，別踩
