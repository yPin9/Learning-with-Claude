# Ch0: 環境設定

## 0.1 選 gcc 版本

| gcc 版本 | C++20 狀態 |
|---|---|
| gcc 10 | coroutines 可用，concepts/ranges 部分可用 |
| gcc 11 | ranges 標準庫補齊 |
| gcc 12 | `std::format` 加入 |
| gcc 13+ | **推薦**。C++20 幾乎完整，C++23 部分支援 |
| gcc 14+ | C++23 更完整，modules 改善 |

用 `g++ --version` 看版本。本課程以 **gcc 13+** 為準。

## 0.2 Windows 上的 gcc

你是 Windows，有幾個選擇：

### 選項 A：MSYS2（推薦）
```bash
# 安裝 MSYS2 後在 MSYS2 UCRT64 terminal：
pacman -S mingw-w64-ucrt-x86_64-gcc
g++ --version
```

### 選項 B：WSL2 + Ubuntu
```bash
wsl --install
# 進入 Ubuntu 後：
sudo apt install g++-13
```
Ubuntu 24.04 內建 gcc 13。

### 選項 C：Docker
```bash
docker run -it --rm -v ${PWD}:/work -w /work gcc:13 bash
```

**本課程範例以 MSYS2 UCRT64 環境為準**。WSL 也 OK。

## 0.3 基本編譯指令

```bash
# 最簡單
g++ -std=c++20 hello.cpp -o hello

# 日常開發推薦
g++ -std=c++20 -Wall -Wextra -Wpedantic -O2 hello.cpp -o hello

# 除錯版本
g++ -std=c++20 -Wall -Wextra -g -O0 -fsanitize=address,undefined hello.cpp -o hello
```

常用 flag：
- `-std=c++20`：啟用 C++20
- `-Wall -Wextra -Wpedantic`：開警告（**強烈建議**，C++ 很多坑靠警告抓）
- `-O2` / `-O0`：優化 / 不優化
- `-g`：包含除錯資訊（給 gdb 用）
- `-fsanitize=address,undefined`：記憶體錯誤與 UB 檢查（開發時必開）

## 0.4 Hello World

建立 `hello.cpp`：

```cpp
#include <iostream>

int main() {
    std::cout << "Hello, modern C++!\n";
    return 0;
}
```

編譯執行：
```bash
g++ -std=c++20 -Wall hello.cpp -o hello
./hello
```

注意幾個 C 差異：
- `<iostream>` 不加 `.h`
- `std::cout` 取代 `printf`
- `"\n"` 比 `std::endl` 快（endl 會 flush buffer）

C++20 可以改寫成：
```cpp
#include <format>
#include <iostream>

int main() {
    std::cout << std::format("Hello, {}!\n", "modern C++");
}
```

（`std::format` 需 gcc 13+。留意 `return 0` 可以省略——`main` 的預設回傳值是 0。）

## 0.5 CMake（真實專案都用這個）

小練習可以直接 `g++`，真實專案用 CMake。最小 `CMakeLists.txt`：

```cmake
cmake_minimum_required(VERSION 3.20)
project(my_cpp_proj LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)  # 關掉 gcc 專屬擴充，用純標準

add_executable(hello hello.cpp)
target_compile_options(hello PRIVATE -Wall -Wextra -Wpedantic)
```

建置：
```bash
cmake -B build -S .
cmake --build build
./build/hello
```

## 0.6 建議的專案目錄結構

```
my_proj/
├── CMakeLists.txt
├── src/
│   └── main.cpp
├── include/
│   └── my_proj/
│       └── foo.hpp
└── tests/
    └── test_foo.cpp
```

## 0.7 線上試跑

不想裝環境時用：
- [godbolt.org](https://godbolt.org)（Compiler Explorer，**每個 C++ 程序員都在用**）
- [wandbox.org](https://wandbox.org)

godbolt 可以即時看組語輸出，對理解 C++ 很有幫助。

## 0.8 驗證環境

```cpp
// check.cpp
#include <iostream>
#include <format>
#include <ranges>
#include <concepts>

int main() {
    std::cout << __cplusplus << '\n';           // 期望 202002L
    std::cout << std::format("gcc {}.{}.{}\n",
        __GNUC__, __GNUC_MINOR__, __GNUC_PATCHLEVEL__);
}
```

```bash
g++ -std=c++20 check.cpp -o check && ./check
```

輸出應該是 `202002`（代表 C++20）和你的 gcc 版本。如果 `<format>` 找不到，升級 gcc。

## 本章重點
- 用 **gcc 13+**，MSYS2 UCRT64 或 WSL2 都行
- 必開 `-Wall -Wextra`，開發時再加 `-fsanitize`
- 真實專案用 CMake，最小骨架三行
- **把 godbolt.org 加進書籤**，學 C++ 會一直用
