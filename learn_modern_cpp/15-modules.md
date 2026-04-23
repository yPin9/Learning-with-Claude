# Ch15: Modules (C++20)

取代 `#include` 的模組系統。語法漂亮、編譯快、沒有 macro leak。

**但實務上**：gcc 支援還粗糙（2026 年中期），大型專案還在 hold。本章目標是「**讀得懂，偶爾玩玩**」，正式專案仍以 header 為主，直到生態成熟。

## 15.1 `#include` 的問題

```cpp
#include <vector>
#include <string>
```

預處理器**把整個 header 檔案貼進來**：
- 編譯慢：每個 TU 重新 parse 所有 include
- macro 污染：`#define MAX 100` 全域蔓延
- ODR 陷阱：同一 header 被兩次 include 要 guard
- 順序敏感：include 次序會影響行為

## 15.2 Module 語法

### 定義 module
```cpp
// math.cppm  (module interface file)
export module math;    // 宣告這是 module math 的介面

export int add(int a, int b) { return a + b; }
export int sub(int a, int b) { return a - b; }

// 沒 export 的是私有，不可從外部存取
int internal_helper(int x) { return x * 2; }
```

### 使用 module
```cpp
// main.cpp
import math;

int main() {
    return add(1, 2);
}
```

沒 `#include`、沒 header guard、沒 namespace pollution。

## 15.3 gcc 編譯 modules

**需要 gcc 11+**，推薦 14+。編譯命令比較囉嗦：

```bash
# Step 1: 編譯 module interface
g++ -std=c++20 -fmodules-ts -c math.cppm

# Step 2: 編譯使用者
g++ -std=c++20 -fmodules-ts main.cpp math.o -o main
```

gcc 的 module 會在 `gcm.cache/` 留下中間產物。

**注意**：gcc 的 modules 在 C++20 標準模式下是「實驗」狀態（`-fmodules-ts`），build system 支援也有限。CMake 3.28+ 正式支援。

## 15.4 匯出 vs 內部

```cpp
export module math;

// 匯出——外部可見
export int public_func();
export class PublicClass { };

// 內部——只在這 module 裡可見
int private_func();
class InternalHelper { };

// 整段匯出
export {
    int f1();
    int f2();
    class C { };
}
```

## 15.5 Module 組成部分

```cpp
// math.cppm
export module math;          // primary interface

import std;                  // 引入標準庫 module (C++23 才標準化)

// 也可以拆成 partition：
// math-utils.cppm
export module math:utils;   // "math" 的 partition "utils"

// math.cppm
export module math;
export import :utils;        // 把 utils 轉匯出
```

Partitions 讓大 module 可以拆多檔。

## 15.6 Header unit：過渡方案

無法立刻換 module 時，可以把 header 當 module 用：

```cpp
import <vector>;        // 把標準 header 當 module 匯入
import <string>;
import "myheader.hpp";  // 自己的 header
```

好處：部分享受 module 的編譯速度，不用改 header。
壞處：macro 行為還是會出問題。

## 15.7 `import std;`（C++23）

```cpp
import std;             // 匯入整個標準庫

int main() {
    std::cout << "hello\n";
}
```

C++23 正式加入。**gcc 14+ 才支援**。在它普及前，標準庫還是得用 `#include` 或 `import <header>;`。

## 15.8 Module 與舊 code 共存

現階段現實：
- 新專案核心可以嘗試 module
- 第三方庫通常還是 header-only 或 header + .so
- STL 用 `#include` 或 `import <header>;` 最穩

一個漸進策略：
1. 先把你的程式碼從 `#include "xxx.hpp"` 換成 module
2. 第三方和 STL 照舊 include
3. 等 toolchain 成熟再全換

## 15.9 Macro 不會穿越 module 邊界

```cpp
// math.cppm
export module math;

#define MY_PI 3.14159       // 這個 MY_PI 不會影響 import 的人

export double pi() { return MY_PI; }
```

```cpp
// main.cpp
import math;
std::cout << MY_PI;         // ❌ 錯：MY_PI 不存在
```

這是 module 最大賣點之一——**macro 不逃**。

## 15.10 完整小範例

```cpp
// greeter.cppm
export module greeter;

import <iostream>;
import <string>;

export class Greeter {
    std::string name_;
public:
    explicit Greeter(std::string name) : name_(std::move(name)) {}

    void greet() const {
        std::cout << "Hello, " << name_ << "!\n";
    }
};

export void say_hi(const std::string& name) {
    Greeter g{name};
    g.greet();
}
```

```cpp
// main.cpp
import greeter;
import <string>;

int main() {
    say_hi("Alice");
}
```

```bash
g++ -std=c++20 -fmodules-ts -x c++ -c greeter.cppm -o greeter.o
g++ -std=c++20 -fmodules-ts main.cpp greeter.o -o main
./main
```

## 15.11 CMake 與 modules

CMake 3.28+ 原生支援：

```cmake
cmake_minimum_required(VERSION 3.28)
project(myproj LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_SCAN_FOR_MODULES ON)

add_library(greeter)
target_sources(greeter
    PUBLIC
        FILE_SET cxx_modules TYPE CXX_MODULES FILES
            greeter.cppm
)

add_executable(main main.cpp)
target_link_libraries(main PRIVATE greeter)
```

生態還在演進，不同 IDE / 編輯器支援也不一。

## 15.12 什麼時候該試 module？

**現在（2026）值得試**：
- 新專案、小專案
- 你想體驗未來
- 編譯時間是瓶頸

**現在先等**：
- 大型現有專案（移植成本高）
- 用大量 header-only 第三方庫
- 團隊 toolchain 不統一

**必讀**：即使不寫，別人的 code 可能有，要看得懂 `export`、`import`、`module` 語法。

## 15.13 常見錯誤

### 錯誤 1：忘了 `export`
```cpp
export module math;
int add(int, int);          // ❌ 沒 export，外部看不到
```

### 錯誤 2：在 module 裡 `#include`
可以，但建議改 `import`。`#include` 在 module 裡會把 macro 封進 module 片段，減少污染。

### 錯誤 3：module interface 和 implementation 分離亂
```cpp
// math.cppm
export module math;
export int add(int, int);

// math.cpp
module math;                // 這是 implementation partition
int add(int a, int b) { return a + b; }
```

### 錯誤 4：`.cppm` 副檔名
gcc 預設不認 `.cppm`，可能需要 `-x c++`。或用 `.cpp` 都行（只要內容對）。

## 15.14 練習

1. 把 Ch4 的 `FileDesc` class 改寫成 module 形式，寫個 main.cpp 用它。
2. 比較同一個程式 `#include <iostream>` 版本和 `import <iostream>;` 版本的編譯時間。

## 本章重點
- Module 取代 `#include`，解決編譯速度、macro 污染
- 語法：`export module X;` + `import X;`
- gcc 需 `-fmodules-ts`（或 13+ 更穩）
- Macro 不跨 module 邊界——大賣點
- `import std;`（C++23）最終會取代 `#include <vector>` 等
- 2026 年實務：新專案可試，舊專案先等
- **即使不寫也要看得懂**，未來 code 會大量用
