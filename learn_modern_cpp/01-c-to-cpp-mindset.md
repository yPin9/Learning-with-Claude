# Ch1: C → C++ 的心態與陷阱

C++ 號稱「C 的超集」，實際上只在**語法層面**大致成立，**心態**完全不同。本章把 C 程序員最常踩的坑一次列清。

## 1.1 最大心態差異：物件有生命週期

C 的心態：「變數就是一塊記憶體，我負責分配和釋放」。

C++ 的心態：「變數是**物件**，它有**建構/解構**，會自動在 scope 結束時清理」。

```cpp
{
    std::string s = "hello";  // 建構：可能分配記憶體
    // ... 用 s ...
}                              // 解構自動執行：釋放記憶體
```

沒有 `free(s)`、沒有 `s.destroy()`。**這是 C++ 的核心賣點**，叫 RAII（Ch3 細講）。

如果你還在想「什麼時候要 free」，你就還在 C 的腦袋。

## 1.2 不要用 `malloc` / `free`

```cpp
// C 習慣，別這樣
int* p = (int*)malloc(sizeof(int) * 10);
free(p);

// 原始 C++ 寫法（也別這樣）
int* p = new int[10];
delete[] p;

// 現代 C++
std::vector<int> v(10);  // 完！自動管理
```

`new` / `delete` 在現代 C++ 裡**幾乎用不到**——用 `std::vector`、`std::string`、`std::unique_ptr` 取代。看到 `new` 就要懷疑。

## 1.3 `void*` 是不好的訊號

C 靠 `void*` 做泛型（例如 `qsort`）。C++ 用 **templates**（Ch7）。`void*` 在 C++ code 幾乎不該出現。

```c
// C 風格
void qsort(void*, size_t, size_t, int(*)(const void*, const void*));
```

```cpp
// C++ 風格
std::sort(v.begin(), v.end());                  // 型別安全
std::sort(v.begin(), v.end(), std::greater<>{});
```

## 1.4 隱式轉換——C++ 比 C 更嚴格

C 允許很多隱式轉換，C++ 把幾個危險的關掉：

```cpp
int* p = malloc(4);         // ❌ 錯誤：void* 不能隱式轉 int*
int* p = (int*)malloc(4);   // ✅ 可以（但不該這樣寫）
int* p = new int;           // ✅ C++ 風格（但你通常不該 new）
```

還有：
```cpp
enum Color { RED, GREEN };
int x = RED;                // ✅ 舊式 enum 會轉
Color c = 0;                // ❌ 反過來不行（C++ 比 C 嚴）

enum class Fruit { APPLE };
int y = Fruit::APPLE;       // ❌ enum class 完全不轉（推薦用這個）
```

**用 `enum class` 取代 `enum`**，避免命名衝突和意外轉型。

## 1.5 Header vs Translation Unit vs ODR

這個 C 也有，但 C++ 更嚴格。

- **Header (`.hpp` / `.h`)**：被 `#include` 進別的檔案
- **Source (`.cpp`)**：被編譯成一個 Translation Unit
- **ODR (One Definition Rule)**：每個非 inline 的函式/變數在整個程式中只能有**一個**定義

常見錯誤：
```cpp
// my.hpp
int counter = 0;   // ❌ 每個 include 它的 .cpp 都定義一次，linker error
```

正確做法：
```cpp
// my.hpp
extern int counter;     // 宣告
inline int other = 0;   // inline 變數 (C++17) 允許多份定義，linker 合併

// my.cpp
int counter = 0;        // 定義只在一個地方
```

**現代 C++ 建議**：小工具函式用 `inline`，大東西放 `.cpp`。或直接用 modules (Ch15)。

## 1.6 Header guard / `#pragma once`

```cpp
// 傳統方式（C 也這樣）
#ifndef MY_HEADER_HPP
#define MY_HEADER_HPP
// ...
#endif

// 現代（非標準但所有主流編譯器都支援）
#pragma once
```

新專案用 `#pragma once`，簡潔不會打錯。

## 1.7 `NULL` 已死，用 `nullptr`

```cpp
void f(int);
void f(char*);

f(NULL);     // ⚠️ 在 C++ 裡 NULL 常常就是 0，會呼叫 f(int)！
f(nullptr);  // ✅ 明確是 pointer，呼叫 f(char*)
```

`nullptr` 是 C++11 加的關鍵字，有專屬型別 `std::nullptr_t`。**永遠用 `nullptr`**。

## 1.8 `struct` 和 `class` 幾乎一樣

```cpp
struct A { int x; };    // 預設 public
class B { int x; };     // 預設 private
```

只差預設存取權限。慣例：**純資料結構用 `struct`，有封裝的類別用 `class`**。

C 的 `typedef struct Foo Foo;` 在 C++ 不用——struct name 直接就是 type name。

## 1.9 函式重載 (Overloading)

C++ 允許同名函式不同參數：
```cpp
int max(int a, int b);
double max(double a, double b);
```

編譯器用 **name mangling** 把它們變成不同符號。所以 C++ 呼叫 C library 要包 `extern "C"`：
```cpp
extern "C" {
    #include <zlib.h>   // C library
}
```

## 1.10 預設參數

```cpp
void connect(const char* host, int port = 8080, int timeout_ms = 5000);

connect("example.com");                // 用預設
connect("example.com", 9090);          // 覆蓋 port
connect("example.com", 9090, 10000);
```

別濫用，但是處理「選用參數」比 C 的 NULL 判斷乾淨。

## 1.11 命名空間 (Namespace)

```cpp
namespace my_lib {
    int compute(int);
}

my_lib::compute(42);
```

取代 C 那套 `mylib_compute()` 前綴。

**別在 header 寫 `using namespace std;`**——會污染所有 include 你的 header 的檔案。`.cpp` 內部用可以，小範圍更好：
```cpp
void f() {
    using std::vector;   // 只在這函式生效
    vector<int> v;
}
```

## 1.12 一張對照表

| C 習慣 | C++ 做法 |
|---|---|
| `malloc`/`free` | `std::vector`、`std::unique_ptr` |
| `char*` 字串 | `std::string` / `std::string_view` |
| `printf` | `std::cout`、`std::format`（C++20） |
| `void*` 泛型 | Template |
| 函式指標 | Lambda、`std::function` |
| `struct` + 函式 | `class` + method（有時） |
| `#define` 常數 | `constexpr` |
| `#define` 函式 | `inline` function / template |
| `NULL` | `nullptr` |
| `enum` | `enum class` |
| 手動錯誤碼 | Exception 或 `std::optional`/`std::expected` |

## 1.13 不要被「C 寫得出來就這樣寫」綁架

你會看到老代碼這樣寫：
```cpp
char buf[256];
sprintf(buf, "result: %d", n);
printf("%s\n", buf);
```

能動，但這在現代 C++ 就是**爛 code**。應該：
```cpp
std::cout << std::format("result: {}\n", n);
```

心態上，把「C 的寫法」當成遺物，不是 baseline。

## 1.14 快速練習

寫一個讀標準輸入每行、反轉後印出的程式。**不要用 `char[]` buffer 和 `fgets`**。

<details>
<summary>參考答案</summary>

```cpp
#include <algorithm>
#include <iostream>
#include <string>

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::ranges::reverse(line);
        std::cout << line << '\n';
    }
}
```

重點：沒有 buffer 大小、沒有 `free`、沒有 pointer 操作。
</details>

## 本章重點
- 物件有生命週期，scope 結束自動清理（RAII 的預告）
- 看到 `malloc`/`free`/`new`/`delete`/`void*` 要警戒
- `nullptr` 取代 `NULL`，`enum class` 取代 `enum`
- 別在 header 寫 `using namespace std;`
- 「能像 C 寫」不代表「該像 C 寫」
