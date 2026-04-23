# Ch7: Templates 入門

Templates 是 C++ 的泛型機制。C 靠 `void*` + 函式指標，C++ 靠 template——**編譯期**產生型別安全的 code。

本章目標：會讀、會用、**不深入元編程**（TMP/SFINAE 不講）。

## 7.1 Function template

```cpp
template <typename T>
T max(T a, T b) {
    return a > b ? a : b;
}

int x = max(1, 2);            // T = int
double y = max(1.5, 2.5);     // T = double
std::string s = max(std::string{"a"}, std::string{"b"});  // T = string
```

`typename` 也可以寫 `class`（完全等價，但 `typename` 比較現代）。

編譯器看到呼叫 `max(1, 2)` 時，**產生**一份 `int max(int, int)`——叫 **template instantiation**。每組型別都會產生一份 code（所以 C++ 編譯慢）。

### Type deduction 失敗
```cpp
max(1, 2.0);   // ❌ 錯：T 是 int 還是 double？

max<double>(1, 2.0);   // ✅ 明確指定
max(1.0, 2.0);         // ✅ 強制一致
```

### 回傳型別也可以泛型
```cpp
template <typename T, typename U>
auto add(T a, U b) {    // auto 讓編譯器推
    return a + b;
}

auto r = add(1, 2.5);   // r 是 double
```

## 7.2 Class template

```cpp
template <typename T>
class Box {
    T value_;
public:
    Box(T v) : value_(std::move(v)) {}
    const T& get() const { return value_; }
    void set(T v) { value_ = std::move(v); }
};

Box<int> b1{42};
Box<std::string> b2{"hello"};
```

C++17 支援 **Class Template Argument Deduction (CTAD)**：
```cpp
Box b3{42};             // ✅ C++17：自動推 Box<int>
std::vector v{1, 2, 3}; // ✅ C++17：自動推 vector<int>
std::pair p{1, "hi"};   // ✅
```

所以現在很少看到 `std::vector<int> v{...}`——大家直接寫 `std::vector v{...}`。

## 7.3 模板參數可以是**值**（非型別參數）

```cpp
template <typename T, std::size_t N>
class Array {
    T data_[N];
public:
    std::size_t size() const { return N; }
    T& operator[](std::size_t i) { return data_[i]; }
};

Array<int, 10> a;
Array<double, 100> b;
```

這就是 `std::array` 大致的實作。N 是 compile-time 常數。

## 7.4 多個型別參數 + 預設

```cpp
template <typename Key, typename Value, typename Compare = std::less<Key>>
class Map { /* ... */ };

Map<int, std::string> m;                     // 用預設 Compare
Map<int, std::string, std::greater<>> m2;    // 指定
```

## 7.5 Template 為什麼寫在 header

**Template 的定義必須被 caller 看到**，因為編譯器要 instantiate。所以 template 幾乎都寫在 header（`.hpp`）裡。

```cpp
// box.hpp
template <typename T>
class Box {
    T v_;
public:
    Box(T v) : v_(std::move(v)) {}
    const T& get() const { return v_; }
};
// ☝ 整個定義都在 header
```

如果你硬把 template 定義放 `.cpp`，連結時會找不到 symbol（除非用 explicit instantiation，進階技巧）。

Modules (Ch15) 會解決這問題，但目前主流做法還是 header-only。

## 7.6 `auto` 參數（C++20 的甜頭）

C++20 允許函式直接用 `auto` 當參數——等效於 template：

```cpp
// C++20
auto max(auto a, auto b) {
    return a > b ? a : b;
}

// 等效於
template <typename T1, typename T2>
auto max(T1 a, T2 b) {
    return a > b ? a : b;
}
```

寫 template 的最少語法。下一章（Concepts）會加強：
```cpp
auto max(std::integral auto a, std::integral auto b) { return a > b ? a : b; }
```

## 7.7 Variadic template（看得懂就好）

可變參數個數：

```cpp
template <typename... Args>
void print_all(Args... args) {
    ((std::cout << args << ' '), ...);   // C++17 fold expression
    std::cout << '\n';
}

print_all(1, "hi", 3.14);   // 1 hi 3.14
```

- `Args...` 是 **parameter pack**
- `(args, ...)` 是 fold expression，展開成 `(((arg1, arg2), arg3), ...)`

入門只要**看懂 `Args...` 是「任意多個參數」的意思**，不用自己寫。用到時翻文件。

## 7.8 Template 錯誤訊息

Template 錯誤訊息「**又臭又長**」是 C++ 的惡名。範例：

```cpp
std::vector<int> v;
std::sort(v.begin(), v.end(), [](auto a, auto b){ return a; });   // ❌ 回傳 int 不是 bool
```

錯誤訊息可能幾百行，從 `<algorithm>` 內部一路展開。看錯誤的技巧：
1. **先看你 code 的那一行**（通常在最開頭或最結尾）
2. 再看「`required from here`」指向哪行
3. 中間的 `std::__` 開頭都是內部，先忽略

C++20 **Concepts**（Ch13）就是來解決這問題——把「這個型別要滿足什麼」寫清楚，錯誤訊息變短。

## 7.9 Specialization（看得懂就好）

對特定型別寫不同實作：

```cpp
template <typename T>
std::string to_string(T v) { return std::to_string(v); }

// 特化：bool 要特別處理
template <>
std::string to_string<bool>(bool v) { return v ? "true" : "false"; }
```

入門階段你自己寫特化的機會不多，但會讀到標準庫的 `std::hash<std::string>` 這類特化。

## 7.10 常見用法全景

```cpp
// 1. 泛型容器
template <typename T>
class Stack {
    std::vector<T> data_;
public:
    void push(T v) { data_.push_back(std::move(v)); }
    T pop() { T v = std::move(data_.back()); data_.pop_back(); return v; }
    bool empty() const { return data_.empty(); }
};

Stack<int> si;
Stack<std::string> ss;

// 2. 泛型演算法（標準庫風格）
template <typename Iter, typename Pred>
Iter find_if_not(Iter first, Iter last, Pred pred) {
    for (; first != last; ++first)
        if (!pred(*first)) return first;
    return last;
}

// 3. 泛型工廠
template <typename T, typename... Args>
std::unique_ptr<T> make(Args&&... args) {
    return std::unique_ptr<T>(new T(std::forward<Args>(args)...));
}
```

## 7.11 什麼時候該寫 template？

- 你要寫一個**演算法**或**容器**，不想綁死元素型別
- 你發現自己在 copy-paste code、只改型別
- 別為了「也許未來會泛型」提前寫 template——YAGNI

很多 C++ 程序員寫得太多 template，code 變難讀又編譯慢。**具體 > 泛型**，有需要再推廣。

## 7.12 編譯期 vs 執行期

Template 是**編譯期**機制：
- 實例化在編譯期，每組型別獨立一份 code
- 錯誤在編譯期被抓
- 執行期零 overhead（不像 Java generics 有 type erasure）

這就是為什麼 C++ template 既泛型又高效——不是動態分派，是靜態 code 生成。

## 7.13 練習

1. 寫一個 `template <typename T> class Optional` 的最小版本（放一個 `T` 或空）。
2. 寫一個 `min(a, b, c, ...)` 接任意多個相同型別的參數，回傳最小值（用 variadic 或 initializer_list）。

## 本章重點
- `template <typename T>` 開頭宣告 template
- 編譯器**實例化**每組實際型別
- Template 定義幾乎都在 header
- C++17 CTAD 讓你少打型別
- C++20 `auto` 參數是 template 的糖
- Variadic 和 specialization 先能讀就好
- 別為假設的未來需求寫 template
