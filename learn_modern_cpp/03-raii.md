# Ch3: RAII

RAII = **Resource Acquisition Is Initialization**。中文勉強譯為「資源取得即初始化」，但名字很爛——重點不在「初始化」，而是「**物件解構時自動釋放資源**」。

**這是整個 C++ 的核心觀念**。沒理解 RAII，就沒理解 C++。

## 3.1 問題：C 的資源管理很痛

```c
// C 風格：打開檔案、讀取、關閉
FILE* f = fopen("data.txt", "r");
if (!f) return -1;

char* buf = malloc(1024);
if (!buf) {
    fclose(f);       // 記得關
    return -1;
}

if (fread(buf, 1, 1024, f) < 0) {
    free(buf);       // 記得放
    fclose(f);       // 記得關
    return -1;
}

// ... 做事 ...

free(buf);           // 記得放
fclose(f);           // 記得關
return 0;
```

**痛點**：每個 error path 都要記得清理，漏一個就是 leak。goto cleanup 也不完美。Exception 還沒出現。

## 3.2 RAII 的想法

把「資源持有」封裝成**物件**。物件建構時取得資源、解構時釋放資源。利用 C++「**物件離開 scope 自動解構**」的機制，資源自動歸還。

```cpp
#include <fstream>
#include <vector>

int read_file() {
    std::ifstream f{"data.txt"};     // 建構：開檔
    if (!f) return -1;

    std::vector<char> buf(1024);      // 建構：分配記憶體

    f.read(buf.data(), 1024);
    // ... 做事 ...

    return 0;
    // 離開 scope 時：
    //   buf 解構 → 自動 free
    //   f   解構 → 自動 fclose
}
```

**沒有任何 cleanup code**。早退、exception、正常 return 都自動清。

## 3.3 例子：自己寫一個 File wrapper

```cpp
#include <cstdio>
#include <stdexcept>

class File {
    FILE* fp_;
public:
    File(const char* path, const char* mode) {
        fp_ = std::fopen(path, mode);
        if (!fp_) throw std::runtime_error("open failed");
    }

    ~File() {
        if (fp_) std::fclose(fp_);
    }

    // 禁止複製（複製的話會 double-close）
    File(const File&) = delete;
    File& operator=(const File&) = delete;

    FILE* get() { return fp_; }
};

void use() {
    File f{"data.txt", "r"};
    char buf[128];
    std::fread(buf, 1, 128, f.get());
    // scope 結束，~File() 自動 fclose
}
```

這個 4 行建構、3 行解構、4 行禁複製，就是一個安全的 RAII wrapper。

## 3.4 為什麼禁止複製？

```cpp
File f1{"a.txt", "r"};
File f2 = f1;           // 沒禁止的話：兩個 File 都持有同一個 FILE*
// f2 解構 → fclose
// f1 解構 → fclose 同一個 → UB！
```

三種選擇：
1. **禁止複製**（上面的做法）——當資源不適合共享
2. **深複製**——複製時建立新資源（`std::vector`、`std::string` 走這條）
3. **Move-only**（Ch5）——可以「轉交」但不能複製（`std::unique_ptr`、`std::thread` 走這條）
4. **共享**（Ch6）——引用計數（`std::shared_ptr`）

## 3.5 標準庫裡都是 RAII

現代 C++ 標準庫物件絕大多數是 RAII：

| 類別 | 管的資源 |
|---|---|
| `std::string` | char buffer |
| `std::vector<T>` | T 的 array |
| `std::unique_ptr<T>` | 單一所有權的 T |
| `std::shared_ptr<T>` | 共享所有權的 T |
| `std::ifstream` / `ofstream` | FILE handle |
| `std::lock_guard` / `std::unique_lock` | mutex |
| `std::jthread` (C++20) | 執行緒 |

**你不需要自己寫 RAII wrapper**，除非包 C API。用標準庫就自動符合。

## 3.6 Lock guard 範例（超重要）

```cpp
#include <mutex>

std::mutex m;
int counter = 0;

void increment() {
    std::lock_guard<std::mutex> lk{m};   // 建構：lock
    ++counter;
    // 離開 scope：lk 解構 → unlock
    // 就算 ++counter throw 也會 unlock
}
```

C 的 pthread 寫法要手動 `pthread_mutex_unlock`，錯過一個 path 就 deadlock。C++ 靠 RAII 根本不會錯。

C++17 甚至不用寫模板參數：
```cpp
std::lock_guard lk{m};   // CTAD：自動推 std::mutex
```

## 3.7 解構順序

Scope 內多個物件解構順序：**後建構的先解構**（LIFO）。

```cpp
void f() {
    File a{"a.txt", "r"};   // 1. 建構 a
    File b{"b.txt", "r"};   // 2. 建構 b
    // 3. 解構 b
    // 4. 解構 a
}
```

這個規則重要在「A 依賴 B」時：A 要晚建構、早解構。

成員變數也一樣：**宣告順序**決定建構順序，解構順序相反。

```cpp
class Foo {
    Resource1 r1_;
    Resource2 r2_;   // r1_ 先建構，r2_ 後建構
};
// 解構時 r2_ 先走，r1_ 後走
```

## 3.8 RAII 不只管記憶體

RAII 可以管任何成對的 acquire/release：

- **Mutex**：lock / unlock
- **Socket**：open / close
- **Transaction**：begin / commit or rollback
- **Indentation**：push / pop
- **計時**：start / 輸出耗時

小範例：scope 計時器
```cpp
#include <chrono>
#include <iostream>
#include <string_view>

class ScopeTimer {
    std::string_view label_;
    std::chrono::steady_clock::time_point start_;
public:
    explicit ScopeTimer(std::string_view l)
        : label_(l), start_(std::chrono::steady_clock::now()) {}

    ~ScopeTimer() {
        auto dur = std::chrono::steady_clock::now() - start_;
        auto us = std::chrono::duration_cast<std::chrono::microseconds>(dur).count();
        std::cout << label_ << ": " << us << "us\n";
    }
};

void work() {
    ScopeTimer t{"work"};
    // ... 做事 ...
    // scope 結束自動印時間
}
```

## 3.9 避免 RAII 常見錯誤

### 錯誤 1：解構函式丟 exception
```cpp
~File() {
    if (std::fclose(fp_) != 0) {
        throw std::runtime_error("close failed");  // ❌ 絕對不要
    }
}
```

解構中丟 exception 在 stack unwinding 時會直接 `std::terminate`。**解構函式絕不能 throw**。

### 錯誤 2：在 constructor 丟 exception 但已分配部分資源
```cpp
class Foo {
    Resource* r1_;
    Resource* r2_;
public:
    Foo() {
        r1_ = new Resource;
        r2_ = new Resource;   // 如果這裡 throw，r1_ leak！
    }
    ~Foo() { delete r1_; delete r2_; }
};
```

解法：讓成員**自己是 RAII 物件**（例如 `std::unique_ptr<Resource>`）。然後 constructor throw 時，已建構的成員會自動解構。

### 錯誤 3：「Two-phase init」
```cpp
File f;                 // 無效物件
f.open("a.txt", "r");   // 第二階段
```

這破壞 RAII。**建構就應該建成有效物件**，失敗就 throw。

## 3.10 心態轉換：別想「何時釋放」

你寫 C 時腦袋會有 checklist：「我 malloc 了，要記得 free」。

寫 C++ 時換個問法：「**這個資源的 owner 是誰？**」。Owner 物件解構，資源自然釋放。你要設計的是「所有權（ownership）」，不是「釋放時機」。

## 3.11 練習

C 版本：
```c
char* read_all(const char* path, size_t* out_len) {
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    rewind(f);
    char* buf = malloc(n);
    if (!buf) { fclose(f); return NULL; }
    fread(buf, 1, n, f);
    fclose(f);
    *out_len = n;
    return buf;
}
// caller 要記得 free
```

改寫成 RAII 版本（**一個分支都不要有手動清理**）。

<details>
<summary>參考答案</summary>

```cpp
#include <fstream>
#include <string>

std::string read_all(const std::string& path) {
    std::ifstream f{path, std::ios::binary};
    if (!f) throw std::runtime_error("open failed");
    return {std::istreambuf_iterator<char>(f), {}};
}
```

- `ifstream` RAII 管檔案
- `std::string` RAII 管 buffer
- caller 拿到 `std::string`，scope 結束自動釋放
- 錯誤用 exception，不用 out-param
</details>

## 本章重點
- RAII：物件建構時取得資源、解構時釋放
- 核心工具是「物件離開 scope 自動解構」這件事
- 標準庫容器、smart pointer、lock guard 都是 RAII
- 解構順序 LIFO
- 解構函式絕不能 throw
- 設計思維：想 ownership，不想 release timing
