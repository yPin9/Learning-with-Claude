# Ch12: constexpr / consteval / if constexpr

C++ 的編譯期計算機制。從 C++11 加入、每個標準都在加強。C 只有 `#define` 能做常量運算，C++ 把它搬到語言本身。

## 12.1 `const` vs `constexpr`

```cpp
const int a = 10;          // 「不會變」，但不一定編譯期已知
constexpr int b = 10;      // 「編譯期就知道」

int n;
std::cin >> n;
const int c = n;           // ✅ 執行期決定值，但之後不變
constexpr int d = n;       // ❌ 錯誤：n 不是 constexpr
```

`constexpr` **蘊含 const**，但多了「編譯期可算」的保證。

## 12.2 `constexpr` 函式

```cpp
constexpr int factorial(int n) {
    return n <= 1 ? 1 : n * factorial(n - 1);
}

constexpr int f5 = factorial(5);    // 編譯期算：120

int x;
std::cin >> x;
int fx = factorial(x);              // ✅ 也可以在執行期呼叫
```

**`constexpr` 函式可以在編譯期或執行期呼叫**，取決於參數。C++14 後函式體幾乎沒限制（可以有變數、迴圈、if）。

```cpp
constexpr int sum(int n) {
    int s = 0;
    for (int i = 1; i <= n; ++i) s += i;   // 迴圈 OK
    return s;
}
```

## 12.3 用途：替代 `#define`

```cpp
// C 風格
#define PI 3.14159
#define MAX_SIZE 1024

// C++ 風格
constexpr double pi = 3.14159;
constexpr int max_size = 1024;
```

差別：
- `#define` 無型別、無 scope、debugger 看不到
- `constexpr` 有型別、有 scope、是正常符號

**新 code 別用 `#define` 定常數**，用 `constexpr`。

## 12.4 編譯期陣列長度、template 參數

```cpp
constexpr int N = 10;
int arr[N];                     // OK：N 是編譯期
std::array<int, N> arr2;        // OK

template <int Size>
struct Buffer { /* ... */ };

Buffer<N> b;                    // OK
```

## 12.5 `consteval`（C++20）

**強制**編譯期執行。

```cpp
consteval int square(int x) { return x * x; }

constexpr int a = square(5);    // ✅
int n = 3;
int b = square(n);              // ❌ 錯誤：n 不是 constant expression
```

- `constexpr`：**可以**在編譯期，也可以執行期
- `consteval`：**必須**編譯期

用途：宣告「這個計算一定要在編譯期完成」的意圖。例如 compile-time regex、format string 檢查。

## 12.6 `constinit`（C++20）

保證變數**編譯期初始化**，但之後可以改。

```cpp
constinit int counter = 0;   // 編譯期初始化
counter = 5;                 // ✅ 之後可以改
```

主要為了解決「靜態初始化順序問題」（static initialization order fiasco）。入門少用。

## 12.7 `if constexpr`（C++17）：編譯期分支

在 template 裡，根據型別選不同分支：

```cpp
template <typename T>
std::string to_string(T value) {
    if constexpr (std::is_integral_v<T>) {
        return std::to_string(value);
    } else if constexpr (std::is_floating_point_v<T>) {
        char buf[32];
        std::snprintf(buf, 32, "%.2f", value);
        return buf;
    } else {
        return std::string{value};  // 假設能轉 string
    }
}
```

**關鍵**：`if constexpr` 的 else 分支在當前型別**不會被編譯**。普通 `if` 兩個分支都要編譯通過。

```cpp
template <typename T>
void f(T x) {
    if (std::is_integral_v<T>) {
        x.length();   // ❌ 就算 T 是 int 不會走這支，仍要編譯
    }
}
// 用 if constexpr 就可以
```

這取代了過去的 SFINAE 和 tag dispatch 絕大多數用途。

## 12.8 `constexpr` 標準庫

很多 C++20 標準庫函式變成 `constexpr`：

```cpp
#include <algorithm>
#include <array>

constexpr std::array<int, 5> make_squares() {
    std::array<int, 5> a{};
    for (int i = 0; i < 5; ++i) a[i] = i * i;
    return a;
}

constexpr auto squares = make_squares();   // 整個編譯期算出來
```

C++20 甚至 `std::vector`、`std::string` 的很多操作也是 `constexpr`（但有限制）。

## 12.9 Compile-time 查表

經典例子：CRC 查表、hash、三角函式表。

```cpp
constexpr std::array<uint32_t, 256> make_crc_table() {
    std::array<uint32_t, 256> table{};
    for (uint32_t i = 0; i < 256; ++i) {
        uint32_t crc = i;
        for (int j = 0; j < 8; ++j) {
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1));
        }
        table[i] = crc;
    }
    return table;
}

constexpr auto crc_table = make_crc_table();   // 編譯期生成
```

執行期不花時間建表。

## 12.10 常見用法 Pattern

### Pattern 1：取代 `#define` 常數
```cpp
constexpr int buffer_size = 4096;
constexpr double earth_gravity = 9.80665;
```

### Pattern 2：編譯期驗證輸入
```cpp
template <int N>
constexpr int pow2() {
    static_assert(N >= 0, "exponent must be non-negative");
    int r = 1;
    for (int i = 0; i < N; ++i) r *= 2;
    return r;
}
```

### Pattern 3：編譯期型別分派
```cpp
template <typename T>
void log(T value) {
    if constexpr (std::is_pointer_v<T>) {
        std::cout << "pointer: " << (value ? *value : 0);
    } else {
        std::cout << "value: " << value;
    }
}
```

### Pattern 4：Compile-time strings
```cpp
consteval bool check_format(const char* s) {
    // 在編譯期檢查格式字串是否合法
    while (*s) {
        if (*s == '%') {
            ++s;
            if (*s != 'd' && *s != 's') return false;
        }
        ++s;
    }
    return true;
}

static_assert(check_format("%d items"));
```

## 12.11 `static_assert`

編譯期斷言。

```cpp
static_assert(sizeof(void*) == 8, "requires 64-bit");
static_assert(factorial(5) == 120);

template <typename T>
void f(T x) {
    static_assert(std::is_arithmetic_v<T>, "T must be numeric");
    // ...
}
```

C++17 起訊息可省略。

## 12.12 限制與常見錯誤

### 錯誤 1：在 constexpr 裡做不被允許的事
C++11 constexpr 函式只能 `return` 單一表達式。C++14 後限制大放寬，但**不能**：
- Virtual function（C++20 允許！）
- `try-catch`（C++20 開始允許，條件嚴格）
- 動態 allocation 的結果「逃出」constexpr 上下文

### 錯誤 2：認為 `constexpr` 就一定編譯期
```cpp
constexpr int f(int x) { return x * 2; }

int y;
std::cin >> y;
int r = f(y);       // 執行期呼叫，完全 OK
```

要強制編譯期：用 `consteval` 或放到 `constexpr` 變數裡。

### 錯誤 3：編譯期大量計算爆編譯時間
寫 `constexpr` 排序 10000 元素的陣列可能讓 compile 很慢。Compile-time 有成本。

## 12.13 Immediate function (C++20 `consteval`)

```cpp
consteval auto make_prime_table() {
    // 編譯期算質數表
    std::array<int, 100> table{};
    // ...
    return table;
}
```

適合「這個結果一定要在編譯期算好」，例如解析格式字串。

## 12.14 `std::format` 的編譯期檢查

C++20 `std::format` 是個好例子：

```cpp
std::format("{} items", 10);          // OK
std::format("{:.2f}", "hello");       // ❌ 編譯期錯誤（格式不符）
```

能做到的原因是格式字串被當成 `consteval` 參數檢查。Ch17 細講。

## 12.15 練習

1. 用 `constexpr` 生成一個 100 個質數的陣列，編譯期算好。
2. 寫一個 `constexpr` 函式 `count_bits(uint32_t)` 回傳 1 的個數，並用 `static_assert` 驗證 `count_bits(0b1011) == 3`。

<details>
<summary>2 的參考答案</summary>

```cpp
constexpr int count_bits(uint32_t x) {
    int n = 0;
    while (x) { n += x & 1; x >>= 1; }
    return n;
}
static_assert(count_bits(0b1011) == 3);
```
</details>

## 本章重點
- `constexpr`：**能**編譯期，也可以執行期
- `consteval`（C++20）：**必須**編譯期
- `if constexpr`：template 裡的編譯期分支
- `static_assert` 做編譯期檢查
- 別用 `#define` 當常數，用 `constexpr`
- C++20 後很多標準庫函式變成 constexpr
