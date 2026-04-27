# Ch9: Lambdas

C++11 引入、之後每個標準都在補強。Lambda 讓你寫「匿名函式」，是現代 C++ 流暢度的關鍵。

## 9.1 最小範例

```cpp
auto square = [](int x) { return x * x; };
std::cout << square(5);   // 25
```

三個組成：
- `[]`：**捕獲列表**（capture list）
- `(int x)`：參數
- `{ return x * x; }`：函式體

## 9.2 和函式指標的差別

C 的函式指標：
```c
int square(int x) { return x * x; }
int (*f)(int) = square;
```

Lambda 的威力在**可以捕獲外部變數**：
```cpp
int multiplier = 3;
auto times = [multiplier](int x) { return x * multiplier; };
std::cout << times(5);    // 15
```

函式指標做不到這件事，C 要靠 `void* userdata` 繞。

## 9.3 捕獲列表詳解

```cpp
int a = 1, b = 2;

[]           // 什麼都不捕獲
[a]          // 捕獲 a（複製）
[&a]         // 捕獲 a（reference）
[a, &b]      // a 複製，b reference
[=]          // 捕獲所有用到的變數（複製）— 預設複製
[&]          // 捕獲所有用到的變數（reference）— 預設 reference
[=, &b]      // 預設複製，b 是 reference
[&, a]       // 預設 reference，a 是複製
[this]       // 捕獲 class 的 this pointer
```

### 範例
```cpp
void foo() {
    int x = 10;
    auto by_copy = [x]() { return x; };   // x 複製進來
    auto by_ref  = [&x]() { return x; };  // x 是 reference

    x = 100;
    std::cout << by_copy();   // 10 （捕獲時就複製了）
    std::cout << by_ref();    // 100（讀當下的 x）
}
```

### 陷阱：捕獲 reference 後 lifetime 過期
```cpp
auto make_lambda() {
    int x = 10;
    return [&x]() { return x; };   // ❌ x 即將消失，reference 懸垂
}
auto bad = make_lambda();
bad();   // UB
```

**規則**：lambda 可能活比被捕獲者久時（例如放進容器、當回呼），**別捕 reference**。

## 9.4 `[=]` 和 `[&]` 的危險

```cpp
struct Widget {
    int x;
    auto bad() {
        return [=]() { return x; };   // ⚠️ 看起來是 copy，其實捕了 this
    }
};
```

C++20 前 `[=]` 會**捕 this**（從 member 的隱式 `this->` 來），pointer 是 copy，物件本體還是被 reference。C++20 開始廢除這行為，要明確寫 `[=, this]` 或 `[*this]`（複製整個物件）。

**推薦**：**明確列出**你要捕的變數，別用 `[=]`/`[&]`。

## 9.5 `mutable`

預設捕的 copy 是 `const`，想改它要加 `mutable`：

```cpp
int counter = 0;
auto inc = [counter]() mutable { return ++counter; };

std::cout << inc();   // 1
std::cout << inc();   // 2
std::cout << counter; // 0（外部的 counter 沒動）
```

像是「lambda 帶了自己的狀態」。

## 9.6 回傳型別推斷

```cpp
auto f = [](int x) { return x * 1.0; };   // 回傳 double

// 明確指定
auto g = [](int x) -> double { return x; };
```

通常不用明確，編譯器會推。

## 9.7 Generic lambda (C++14)

參數用 `auto`：

```cpp
auto print = [](const auto& x) { std::cout << x << '\n'; };

print(42);
print("hi");
print(std::vector{1, 2, 3});   // 需要 vector 有 operator<<
```

這其實是：
```cpp
struct __lambda {
    template <typename T>
    void operator()(const T& x) const { std::cout << x << '\n'; }
};
```

### 明確 template 參數 (C++20)
```cpp
auto f = []<typename T>(const std::vector<T>& v) {
    return v.size();
};
```

## 9.8 `std::function`

Lambda 的型別是「**匿名、每個 lambda 獨一無二**」，不能直接寫在 signature。要「儲存任意可呼叫物」就用 `std::function`：

```cpp
#include <functional>

std::function<int(int, int)> op;

op = [](int a, int b) { return a + b; };
std::cout << op(1, 2);   // 3

op = [](int a, int b) { return a * b; };
std::cout << op(3, 4);   // 12
```

`std::function` 可以裝：
- Lambda
- Function pointer
- Functor (有 `operator()` 的 class)
- 已綁定的 member function

**`std::function` 有 overhead**（通常包 heap 分配 + 虛擬調用）。性能敏感時：
- **Template**：`template <typename F> void call(F f);`——零 overhead，但 signature 不直觀
- **Function pointer**：沒 state 時夠用

## 9.9 Lambda 底層

```cpp
auto f = [x, y](int z) { return x + y + z; };

// 大致等於
struct __unnamed {
    int x, y;
    int operator()(int z) const { return x + y + z; }
};
__unnamed f{x_value, y_value};
```

Lambda 就是編譯器幫你寫的 functor class。明白這點後很多細節就通了。

## 9.10 常用 pattern

### 當 STL 演算法的述詞
```cpp
std::vector<int> v{1, 2, 3, 4, 5};

auto it = std::find_if(v.begin(), v.end(),
    [](int x) { return x > 3; });

std::sort(v.begin(), v.end(),
    [](int a, int b) { return a > b; });    // 降序
```

### 區域輔助函式
```cpp
void process(std::vector<std::string>& lines) {
    auto trim = [](std::string& s) {
        // trim whitespace
    };

    for (auto& line : lines) trim(line);
}
```

### 即時呼叫 (IIFE)
```cpp
const int config_value = [] {
    // 複雜的一次性計算
    int x = compute();
    return x + 1;
}();    // 立刻呼叫，結果給 config_value
```

給 `const` 變數做複雜初始化很好用。

### 回呼 / event handler
```cpp
button.on_click([&counter] { ++counter; });
```

## 9.11 常見錯誤

### 錯誤 1：忘記 mutable
```cpp
auto f = [x = 0]() { return ++x; };   // ❌ 預設 const，不能改 x
auto g = [x = 0]() mutable { return ++x; };   // ✅
```

### 錯誤 2：在 async 工作捕 reference
```cpp
auto run_async() {
    std::string data = load();
    return std::async([&data] { return process(data); });
    // ❌ data 離開 scope，lambda 在別的 thread 跑，UB
}
```
應該 `[data]` by value 或 `[data = std::move(data)]`（C++14 init capture）。

### 錯誤 3：在 vector of lambdas 裡放 reference capture
```cpp
std::vector<std::function<void()>> handlers;
for (int i = 0; i < 10; ++i) {
    handlers.push_back([&i] { std::cout << i; });   // ❌ 都指向同一個 i
}
// i 離開 for 後，lambda 執行時讀懸垂 reference
```
應該 `[i]` by value。

## 9.12 Init capture (C++14)

可以在捕獲時計算表達式：

```cpp
auto p = std::make_unique<Foo>();
auto f = [p = std::move(p)]() { p->use(); };   // move 進 lambda
```

用於：
- Move 不能複製的物件進 lambda
- 建立 lambda 專有的變數

## 9.13 練習

1. 用 lambda + `std::sort` 把 `std::vector<std::pair<std::string, int>>` 按第二個元素降序。
2. 寫一個 `make_counter()` 回傳一個 lambda，每次呼叫回傳遞增整數（0, 1, 2, ...）。用 init capture。

<details>
<summary>2 的參考答案</summary>

```cpp
auto make_counter() {
    return [n = 0]() mutable { return n++; };
}

auto c = make_counter();
std::cout << c() << c() << c();   // 012
```
</details>

## 本章重點
- Lambda = 匿名可呼叫物 + 可捕獲外部變數
- 捕獲方式：`[x]` copy、`[&x]` ref、`[=]`/`[&]` 預設（少用）
- Lambda lifetime 超過捕獲變數時，用 by-value capture
- `mutable` 允許修改 copy 的捕獲
- `std::function` 儲存任意可呼叫物（但有 overhead）
- Template 參數更好（零 overhead，signature 難看）
- Init capture `[x = expr]` 是 C++14 後很重要的技巧
