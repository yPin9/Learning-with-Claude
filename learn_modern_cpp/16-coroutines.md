# Ch16: Coroutines (C++20)

Coroutine = 可以暫停和恢復的函式。C++20 加入，但**只加了語法，沒給好用的標準庫**——你要嘛自己寫「promise type」，要嘛用第三方（cppcoro / libunifex）。

本章目標：**理解語意、會用別人寫好的、寫個最小 generator**。

## 16.1 動機

傳統寫生產-消費非同步流程很痛：
- Thread：貴，同步麻煩
- Callback：巢狀回呼地獄
- Async / Future：更好但組合仍繁瑣

Coroutine 讓你**用同步語法寫非同步 code**：

```cpp
Task fetch_and_process() {
    auto data = co_await fetch_url("...");
    auto parsed = parse(data);
    co_await save(parsed);
}
```

沒 callback、沒 `.then()`，就像一般函式。

## 16.2 三個關鍵字

```cpp
co_await expr;    // 暫停等 expr 完成
co_yield value;   // 產生一個值（generator 用）
co_return value;  // coroutine 結束
```

**含任一個關鍵字的函式**就是 coroutine。

## 16.3 gcc 編譯

需要 `-fcoroutines`：
```bash
g++ -std=c++20 -fcoroutines main.cpp -o main
```

gcc 11+ 算可用，gcc 13+ 穩定。

## 16.4 Coroutine 的硬底層

C++ 的 coroutine 設計是**低階**的——你要提供一個 "promise type" 告訴它怎麼運作。這不是給應用層寫的，而是給 library 作者。

一般應用會：
1. 用 `std::generator`（C++23，gcc 14+）
2. 用 cppcoro 這類第三方
3. 自己寫最小 generator（下面會示範）

### 最小 generator (C++20 DIY)

```cpp
#include <coroutine>
#include <iostream>
#include <utility>

template <typename T>
struct Generator {
    struct promise_type {
        T value_;

        Generator get_return_object() {
            return Generator{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        std::suspend_always initial_suspend() { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void unhandled_exception() { std::terminate(); }

        std::suspend_always yield_value(T v) {
            value_ = std::move(v);
            return {};
        }
        void return_void() {}
    };

    std::coroutine_handle<promise_type> h_;

    explicit Generator(std::coroutine_handle<promise_type> h) : h_(h) {}
    ~Generator() { if (h_) h_.destroy(); }

    Generator(const Generator&) = delete;
    Generator(Generator&& o) noexcept : h_(std::exchange(o.h_, {})) {}

    bool next() {
        h_.resume();
        return !h_.done();
    }
    T& value() { return h_.promise().value_; }
};

// 使用
Generator<int> range(int start, int end) {
    for (int i = start; i < end; ++i) {
        co_yield i;
    }
}

int main() {
    auto gen = range(0, 5);
    while (gen.next()) {
        std::cout << gen.value() << ' ';
    }
}
// 輸出：0 1 2 3 4
```

**這個樣板 code 非常多**，所以實務上大家等 `std::generator`。

## 16.5 `std::generator` (C++23)

C++23 正式加入標準 generator。gcc 14+ 支援：

```cpp
#include <generator>
#include <iostream>

std::generator<int> fib() {
    int a = 0, b = 1;
    while (true) {
        co_yield a;
        auto next = a + b;
        a = b;
        b = next;
    }
}

int main() {
    int i = 0;
    for (int x : fib()) {
        if (i++ == 10) break;
        std::cout << x << ' ';
    }
}
// 輸出：0 1 1 2 3 5 8 13 21 34
```

這才是正常人該寫的 coroutine code。

## 16.6 `co_await` 和 Task

真正強大的是非同步。範例概念（簡化，不給完整實作——會很長）：

```cpp
Task<std::string> fetch(std::string url) {
    auto conn = co_await open_connection(url);
    auto response = co_await conn.read();
    co_return response;
}

Task<void> main_task() {
    auto page1 = co_await fetch("http://a.com");
    auto page2 = co_await fetch("http://b.com");
    std::cout << page1 << page2;
}
```

每個 `co_await` 點：
1. 暫停 coroutine，把 handle 存起來
2. 註冊「當 awaitable 完成，resume 這 handle」
3. 控制權交回 caller

**你需要 Task、awaitable 類別**——標準庫沒有，要用 cppcoro 或自己寫。

## 16.7 Awaitable 三個函式

`co_await expr` 展開成：

```cpp
auto awaitable = expr;
if (!awaitable.await_ready()) {
    awaitable.await_suspend(current_coroutine_handle);
    // [暫停於此]
}
return awaitable.await_resume();
```

三個函式：
- **await_ready**：如果返 true，不暫停，直接 await_resume
- **await_suspend**：如何暫停，通常把 handle 存去某處
- **await_resume**：恢復時回傳什麼

`std::suspend_always` 和 `std::suspend_never` 是兩個預定義的：always 永遠暫停、never 永不暫停。

## 16.8 執行流程

```
co_await X;
```

行為：
1. 當前 coroutine 暫停
2. 控制權回到 caller（或排入排程器）
3. X 完成時，某人（通常是 X 的內部邏輯）呼叫 handle.resume()
4. coroutine 從 await 點繼續

Coroutine 本質是**把函式切成多段「resumable 狀態機」**，編譯器幫你做狀態機轉換。

## 16.9 Coroutine 的記憶體模型

Coroutine 的局部變數存在 **coroutine frame**（heap 分配）裡，這樣暫停也不會丟。

```cpp
Task foo() {
    std::string s = "hello";
    co_await something();
    std::cout << s;    // s 在 heap 裡，還在
}
```

所以 coroutine 比一般函式「貴一點」（一次 heap alloc），但比 thread 便宜一萬倍。

## 16.10 實務選擇

現在 (2026) 的實務建議：

| 想做 | 建議 |
|---|---|
| 生成器（lazy sequence） | C++23 `std::generator`（gcc 14+） |
| 非同步 I/O | cppcoro 或 asio 的 coroutine 支援 |
| 大型並行系統 | libunifex / Boost.Asio / executors（C++26 標準化中） |
| 學語法 | DIY 最小 Generator |

**不建議自己從零寫 Task 類別**，除非學術目的——一堆細節坑。

## 16.11 與 Ranges 整合

C++23 generator 整合 ranges：

```cpp
#include <generator>
#include <ranges>

std::generator<int> primes() {
    // 生成質數...
}

auto first_10 = primes() | std::views::take(10);
for (int p : first_10) std::cout << p << ' ';
```

## 16.12 Coroutine 的限制

- **不能是 constructor / destructor / consteval**
- **不能回傳 `auto` 推 void 的問題**（有 `co_return` 的要明確型別）
- Virtual function 不能直接是 coroutine（可以呼叫 coroutine）
- **Debug 很難**，lldb / gdb 的 coroutine 支援還在改善

## 16.13 常見錯誤

### 錯誤 1：`co_await` 對象不是 awaitable
```cpp
Task f() {
    co_await 42;    // ❌ int 不是 awaitable
}
```

### 錯誤 2：lifetime 問題
```cpp
Generator<int> make_gen(int x) {
    co_yield x;    // OK，x 被 capture 進 frame
}

Generator<int> bad() {
    std::vector<int> v{1,2,3};
    for (int x : v | std::views::filter(/*...*/)) {
        co_yield x;    // OK：v 在 frame
    }
}

Task bad2() {
    auto& r = get_ref_to_something();
    co_await something();    // 如果 r 指的東西期間消失...懸垂
}
```

Coroutine 的 lifetime 分析比一般函式複雜，要小心。

## 16.14 練習

1. 用上面的最小 `Generator` 寫一個產生奇數的 generator：`Generator<int> odds()`。
2. 用 C++23 `std::generator`（有 gcc 14+ 就行）做一個 Collatz 序列產生器。

## 本章重點
- Coroutine = 可暫停/恢復的函式
- 三個關鍵字：`co_await`、`co_yield`、`co_return`
- gcc 需 `-fcoroutines`
- C++20 標準庫**沒有**好用的 Task / Generator——靠第三方或 C++23
- **C++23 `std::generator`** 是第一個真正好用的標準 coroutine 工具（gcc 14+）
- 學習階段重點是語意理解，實作細節太多坑，用 library
