# Ch22: 除錯與品質工具

C++ 最常讓人踩坑的是 UB、memory error、data race。本章整理現代 C++ 除錯工具鏈。

## 22.1 Sanitizers（**最重要**）

Sanitizers 是 gcc/clang 內建的 runtime 檢查器，開 flag 就啟用。**開發時必開**。

### AddressSanitizer (ASan)
抓記憶體錯誤：use-after-free、out-of-bounds、leak。

```bash
g++ -std=c++20 -g -O1 -fsanitize=address main.cpp -o main
./main
```

範例：
```cpp
int main() {
    int* p = new int[10];
    p[11] = 42;            // out-of-bounds
    delete[] p;
    p[0] = 1;              // use-after-free
}
```

ASan 會印出漂亮的 stack trace，指出哪行。

### UndefinedBehaviorSanitizer (UBSan)
抓 UB：signed overflow、null dereference、不對齊存取。

```bash
g++ -fsanitize=undefined main.cpp
```

### ThreadSanitizer (TSan)
抓 data race。和 ASan **互斥**（不能同時用）。

```bash
g++ -fsanitize=thread main.cpp
```

### LeakSanitizer (LSan)
ASan 順便啟用，抓 memory leak。

### 全家桶
開發組合：
```bash
g++ -std=c++20 -g -O1 -fsanitize=address,undefined main.cpp
```

**性能**：比 debug 版慢 2-3 倍，不適合 release。

**Windows MSYS2 注意**：不是所有 sanitizer 都支援（ASan 可，TSan 要 WSL）。

## 22.2 警告 flag

```bash
g++ -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wnon-virtual-dtor main.cpp
```

- `-Wall`：基本警告
- `-Wextra`：多幾個
- `-Wpedantic`：非標準擴充警告
- `-Wshadow`：變數遮蔽警告
- `-Wconversion`：隱式轉換（可能炸）
- `-Wnon-virtual-dtor`：base class 沒 virtual dtor

把警告當 error：
```bash
g++ -Werror ...
```

**預設開 `-Wall -Wextra -Wpedantic`**。

## 22.3 Debug vs Release builds

```bash
# Debug
g++ -std=c++20 -g -O0 -DDEBUG main.cpp -o main.debug

# Release
g++ -std=c++20 -O2 -DNDEBUG main.cpp -o main.release

# Release with debug info (profiling)
g++ -std=c++20 -O2 -g main.cpp -o main.prof
```

- `-O0`：不優化，debug 友善
- `-O2`：常用 release 優化
- `-O3`：更激進，有時變慢
- `-g`：debug info
- `-DNDEBUG`：關 `assert()`

CMake 預設：
- `CMAKE_BUILD_TYPE=Debug` → `-O0 -g`
- `CMAKE_BUILD_TYPE=Release` → `-O2 -DNDEBUG`
- `CMAKE_BUILD_TYPE=RelWithDebInfo` → `-O2 -g`

## 22.4 gdb

```bash
gdb ./main
(gdb) run arg1 arg2
(gdb) break main.cpp:42
(gdb) continue
(gdb) next         # 下一行
(gdb) step         # 進入函式
(gdb) print x      # 印變數
(gdb) backtrace    # stack trace
(gdb) quit
```

記得 `-g` 才有 debug info。`-O0` 除錯體驗好；`-O2` 會變數被優化掉。

### Pretty printer
gdb 裝 Python pretty printer 能漂亮印 `std::vector`、`std::string`、`std::map` 等。現代 gdb 內建。

### TUI 模式
```bash
(gdb) layout src    # 看 source code
(gdb) layout asm    # 看組語
```

或直接 `gdb -tui` 啟動。

## 22.5 clang-format

自動格式化。設定 `.clang-format`：
```yaml
BasedOnStyle: Google
IndentWidth: 4
ColumnLimit: 100
```

用：
```bash
clang-format -i src/*.cpp   # 就地改
```

設 git pre-commit hook 或 CI 自動跑。

## 22.6 clang-tidy

靜態分析 + 自動 fix。

```bash
clang-tidy main.cpp -- -std=c++20
clang-tidy --fix main.cpp -- -std=c++20
```

設 `.clang-tidy`：
```yaml
Checks: >
  bugprone-*,
  performance-*,
  modernize-*,
  readability-*,
  -readability-magic-numbers
```

- `modernize-*`：把老 C++ 自動改成現代寫法（`NULL` → `nullptr`、auto for loop 等）
- `bugprone-*`：抓 bug pattern
- `performance-*`：抓效能問題

**新專案**一開始就開 clang-tidy + CI check，避免技術債堆積。

## 22.7 cppcheck

另一個靜態分析器，和 clang-tidy 互補。

```bash
cppcheck --std=c++20 --enable=all src/
```

## 22.8 Valgrind

記憶體檢查老牌工具。現代 C++ 上 ASan 通常更快更好，但 Valgrind 在沒法重編譯時還很有用。

```bash
valgrind --leak-check=full ./main
```

## 22.9 Benchmarking

### `std::chrono`
```cpp
#include <chrono>

auto start = std::chrono::steady_clock::now();
heavy_work();
auto dur = std::chrono::steady_clock::now() - start;
auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(dur).count();
std::cout << ms << "ms\n";
```

### Google Benchmark
專業 micro-benchmark library：

```cpp
#include <benchmark/benchmark.h>

static void BM_Sort(benchmark::State& state) {
    std::vector<int> v(state.range(0));
    for (auto _ : state) {
        std::iota(v.begin(), v.end(), 0);
        std::sort(v.begin(), v.end());
    }
}
BENCHMARK(BM_Sort)->Range(8, 8<<10);
BENCHMARK_MAIN();
```

自動處理 warm-up、多次運行、反優化防禦。

## 22.10 Profiling

### perf (Linux)
```bash
g++ -O2 -g main.cpp -o main
perf record ./main
perf report       # 互動式火焰圖
```

### gprof
```bash
g++ -pg main.cpp -o main
./main
gprof ./main gmon.out
```

### Compiler Explorer / godbolt.org
看組語輸出，超有幫助。

## 22.11 Assertion

```cpp
#include <cassert>

void process(int x) {
    assert(x >= 0 && "x must be non-negative");   // Release 被 NDEBUG 關掉
    // ...
}
```

C++23 `std::contract` 還在開發中，目前用 `assert`。

自訂更友善：
```cpp
#define ENSURE(cond) \
    do { if (!(cond)) { \
        std::cerr << __FILE__ << ":" << __LINE__ << " " #cond << '\n'; \
        std::terminate(); \
    } } while(0)
```

## 22.12 單元測試

常用框架：
- **GoogleTest / GoogleMock**：主流，Google 背景
- **Catch2**：header-only，語法漂亮
- **doctest**：超快編譯

Catch2 範例：
```cpp
#define CATCH_CONFIG_MAIN
#include <catch2/catch_all.hpp>

TEST_CASE("addition works") {
    REQUIRE(1 + 1 == 2);
    REQUIRE_FALSE(false);
}
```

## 22.13 CI 建議

新專案 CI pipeline 至少包含：
1. `-Wall -Wextra -Werror` 編譯
2. ASan + UBSan 跑單元測試
3. TSan 跑（並行 code 的話）
4. `clang-tidy` + `clang-format --check`
5. Release build 跑測試（有些 bug 只在 optimization 出現）

## 22.14 實戰：一個 bug 的捕獲

```cpp
// bug.cpp
#include <vector>
#include <iostream>

int main() {
    std::vector<int> v(10);
    for (int i = 0; i <= 10; ++i) v[i] = i;    // off-by-one
    std::cout << v[0];
}
```

```bash
g++ -std=c++20 -g -O1 -fsanitize=address bug.cpp -o bug
./bug
```

```
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address ...
WRITE of size 4 at ... thread T0
    #0 ... in main bug.cpp:6
    ...
```

秒抓。

## 22.15 IDE / 編輯器設定

- **VS Code** + C/C++ extension + clangd
- **CLion**（JetBrains）：完整 C++ IDE
- **vim/neovim** + clangd + coc.nvim
- **Emacs** + eglot + clangd

**都裝 clangd**——它做好 90% 的 IDE 事（自動完成、error check、goto definition）。

clangd 需要 `compile_commands.json`：
```bash
cmake -B build -S . -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
ln -s build/compile_commands.json .
```

## 22.16 練習

1. 故意寫一個 use-after-free 的 code，用 ASan 找出來。
2. 設定專案的 CMakeLists.txt：Debug build 啟用 sanitizers，Release 不。
3. 寫一個 clang-tidy config，開啟 modernize checks，在一個老 C 風格的檔案上跑，看修改建議。

## 本章重點
- **開發時必開 `-fsanitize=address,undefined`**
- **必開 `-Wall -Wextra -Wpedantic`**
- 用 clang-tidy / clang-format 做靜態品質
- Debug 用 gdb，Release 用 perf profiling
- `-O0 -g` 除錯、`-O2 -g` profiling、`-O2 -DNDEBUG` release
- CI 至少跑 sanitizers + warnings-as-errors
- 單元測試框架建議 Catch2 或 GoogleTest
