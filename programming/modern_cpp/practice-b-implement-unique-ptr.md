# Practice B: 自己實作 unique_ptr

**目標**：從零實作 `unique_ptr`，理解 smart pointer 底層。
**用到**：Ch4-7（ctor/dtor、move semantics、smart pointer、templates）

## 為什麼做這題

你每天用 `unique_ptr`，但實作它會讓你同時練到：
- Templates
- Rule of Five
- Move semantics
- Operator overloading
- `noexcept` 正確使用
- Deleter 自訂（進階）

## Step 1：最小版本

實作支援以下操作的 `MyUniquePtr<T>`：

```cpp
MyUniquePtr<int> p{new int{42}};
*p = 100;
p->some_method();          // 如果 T 有 method
int* raw = p.get();
if (p) { /* ... */ }        // 可當 bool
```

### 要求
- Constructor 接收 `T*`
- Destructor 自動 delete
- 禁用 copy
- 支援 move
- `operator*` / `operator->` / `get()` / `operator bool()`

### 骨架

```cpp
template <typename T>
class MyUniquePtr {
    T* ptr_ = nullptr;
public:
    // TODO: constructor, destructor
    // TODO: 禁 copy
    // TODO: move
    // TODO: operator*, operator->, get, operator bool
};
```

### 參考答案

<details>
<summary>展開</summary>

```cpp
template <typename T>
class MyUniquePtr {
    T* ptr_ = nullptr;
public:
    MyUniquePtr() noexcept = default;
    explicit MyUniquePtr(T* p) noexcept : ptr_(p) {}

    ~MyUniquePtr() { delete ptr_; }

    // 禁 copy
    MyUniquePtr(const MyUniquePtr&) = delete;
    MyUniquePtr& operator=(const MyUniquePtr&) = delete;

    // Move
    MyUniquePtr(MyUniquePtr&& other) noexcept
        : ptr_(other.ptr_) {
        other.ptr_ = nullptr;
    }

    MyUniquePtr& operator=(MyUniquePtr&& other) noexcept {
        if (this != &other) {
            delete ptr_;
            ptr_ = other.ptr_;
            other.ptr_ = nullptr;
        }
        return *this;
    }

    T& operator*() const { return *ptr_; }
    T* operator->() const noexcept { return ptr_; }
    T* get() const noexcept { return ptr_; }
    explicit operator bool() const noexcept { return ptr_ != nullptr; }

    // reset / release
    void reset(T* p = nullptr) noexcept {
        T* old = ptr_;
        ptr_ = p;
        delete old;
    }

    T* release() noexcept {
        T* p = ptr_;
        ptr_ = nullptr;
        return p;
    }
};
```

### 測試

```cpp
#include <iostream>

struct Foo {
    int x;
    Foo(int x_) : x(x_) { std::cout << "Foo(" << x << ")\n"; }
    ~Foo() { std::cout << "~Foo(" << x << ")\n"; }
    void greet() { std::cout << "hi " << x << '\n'; }
};

int main() {
    {
        MyUniquePtr<Foo> p{new Foo{42}};
        p->greet();
        std::cout << p->x << '\n';

        MyUniquePtr<Foo> q = std::move(p);
        q->greet();
        if (!p) std::cout << "p is empty\n";
    }   // q 解構，Foo 解構
}
```

期望輸出：
```
Foo(42)
hi 42
42
hi 42
p is empty
~Foo(42)
```
</details>

## Step 2：`make_unique`

```cpp
template <typename T, typename... Args>
MyUniquePtr<T> my_make_unique(Args&&... args) {
    return MyUniquePtr<T>{new T{std::forward<Args>(args)...}};
}
```

用 variadic + forward 完美轉發 constructor 參數。

```cpp
auto p = my_make_unique<Foo>(42);
auto v = my_make_unique<std::vector<int>>(10, 0);
```

## Step 3：`operator[]` 給陣列版本

C++ 標準 `unique_ptr<T[]>` 有陣列特化。略過不寫——寫起來複雜但觀念同。（想自己挑戰：用 specialization。）

## Step 4：自訂 Deleter（進階）

標準 `unique_ptr<T, Deleter>` 接受自訂 deleter。

```cpp
template <typename T, typename Deleter = std::default_delete<T>>
class MyUniquePtr {
    T* ptr_ = nullptr;
    Deleter deleter_;
public:
    // ...
    ~MyUniquePtr() { if (ptr_) deleter_(ptr_); }
};
```

這樣可以包 C API：

```cpp
auto file_deleter = [](FILE* f) { std::fclose(f); };
MyUniquePtr<FILE, decltype(file_deleter)> f{std::fopen("a.txt", "r"), file_deleter};
```

實作要注意 `Deleter` 可能是 function pointer（有大小）或 empty lambda（EBO 優化省空間）。

## Step 5：和 `std::unique_ptr` 比對

運行同樣測試，比較：
1. 行為是否一致
2. 生成的組語（godbolt.org 看 `-O2`）是否類似
3. 性能（micro benchmark）

`std::unique_ptr` 在 release mode 下應該是**零 overhead**——和 raw pointer 一樣快。你自己的實作也該能做到。

## Step 6：測試 move semantics

```cpp
void take(MyUniquePtr<Foo> p) {
    p->greet();
}

int main() {
    auto p = my_make_unique<Foo>(1);
    take(std::move(p));           // ✅ 轉移所有權
    // take(p);                    // ❌ 應該編譯錯（no copy）
    if (!p) std::cout << "moved\n";
}
```

## Step 7：放進 vector

```cpp
std::vector<MyUniquePtr<Foo>> v;
v.push_back(my_make_unique<Foo>(1));
v.push_back(my_make_unique<Foo>(2));
v.push_back(my_make_unique<Foo>(3));

for (auto& p : v) p->greet();
// v 解構：每個 unique_ptr 解構 → 每個 Foo 解構
```

`unique_ptr` move-only 的特性讓它完美適合放容器。

## 實作陷阱

### 陷阱 1：忘了 `noexcept`
```cpp
MyUniquePtr(MyUniquePtr&& other) noexcept   // 沒 noexcept → vector 退化 copy
```

### 陷阱 2：Move assignment 不檢查 self
```cpp
operator=(MyUniquePtr&& other) {
    delete ptr_;              // 如果 this == &other，下一行用到已 delete 的
    ptr_ = other.ptr_;
    other.ptr_ = nullptr;
}
```

### 陷阱 3：copy constructor 沒 delete
```cpp
// 沒寫 = delete，編譯器可能生成（或不生成），行為不明
```

### 陷阱 4：`operator->` 忘了 noexcept
這些都不 throw，加 noexcept 讓編譯器優化。

### 陷阱 5：`release()` 忘了清 `ptr_`
不清的話，destructor 會重複 delete。

## 進階挑戰

1. **EBO 優化**：當 Deleter 是 stateless（如 lambda 不捕獲），應該不佔空間。研究 `[[no_unique_address]]`（C++20）。
2. **Pointer-to-member arrow**：讓 `p->x` 在 T 沒 `operator->` 時也 work。`operator->` 的回傳值會被再次 `->`。
3. **轉換**：支援 `MyUniquePtr<Derived>` 轉 `MyUniquePtr<Base>`。

## 驗證工具

```bash
# 用 sanitizer 驗證無 leak / double free
g++ -std=c++20 -Wall -Wextra -fsanitize=address main.cpp && ./a.out
```

ASan 沒叫就代表 ownership 邏輯正確。

## 本練習重點
- `unique_ptr` 本質上就是：RAII + move-only + 一個 pointer
- `noexcept` 在 move 上的重要性
- `make_unique` 用 variadic template + `std::forward`
- 自訂 deleter 是包 C API 的金鑰
- Release mode 下 smart pointer 應該零 overhead
