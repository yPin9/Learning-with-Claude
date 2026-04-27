# Ch18: std::optional / std::variant 與錯誤處理

C++17 加了 `std::optional`、`std::variant`。C++23 加了 `std::expected`。這章整理「回傳值型別安全」的工具。

## 18.1 `std::optional<T>`：「可能沒有」

想表達「函式可能沒結果」：
- C 風格：回傳 `T*`，null 表示沒有
- 舊 C++：回傳特殊值（`-1`、`""`）
- **現代**：`std::optional<T>`

```cpp
#include <optional>
#include <string>

std::optional<int> parse_int(const std::string& s) {
    try {
        return std::stoi(s);
    } catch (...) {
        return std::nullopt;
    }
}

auto r = parse_int("42");
if (r) {                       // 或 r.has_value()
    std::cout << *r;           // 或 r.value()
}
```

### 使用
```cpp
std::optional<int> x;            // 空
std::optional<int> y = 42;       // 有值
std::optional<int> z{};          // 空
std::optional<int> w = std::nullopt;

if (x) { /* 有值 */ }
if (x.has_value()) { /* 同上 */ }

*x;                // 取值（undefined 如果空）
x.value();         // 取值（throws std::bad_optional_access 如果空）
x.value_or(0);     // 取值或預設

x = 10;            // 設值
x.reset();         // 清空
x = std::nullopt;  // 清空
```

### 函式鏈（C++23）
```cpp
std::optional<int> opt = 5;

auto result = opt
    .transform([](int x) { return x * 2; })           // 有值就套函式
    .and_then([](int x) -> std::optional<int> {       // chain 另一個 optional
        if (x > 0) return x;
        return std::nullopt;
    })
    .or_else([] { return std::optional{-1}; });       // 沒值就替換
```

**gcc 13+** 才有這些 monadic 方法。超好用——像 Rust 的 `Option`。

## 18.2 `std::variant<T, U, ...>`：Type-safe union

C 的 union 沒型別追蹤，用錯 field 就 UB。`std::variant` 是 type-safe union：

```cpp
#include <variant>

std::variant<int, std::string, double> v;
v = 42;            // 現在裝 int
v = "hello";       // 現在裝 string
v = 3.14;          // 現在裝 double

std::holds_alternative<int>(v);    // false（現在是 double）
std::get<double>(v);               // 3.14
std::get<int>(v);                  // throws std::bad_variant_access

v.index();                          // 2（double 是第 3 個）
```

### `std::visit`
```cpp
std::visit([](const auto& val) {
    std::cout << val << '\n';
}, v);
```

Visitor 是通用介面來「**處理所有可能的型別**」。可以用 overload 組：

```cpp
struct Visitor {
    void operator()(int x)         { std::cout << "int " << x; }
    void operator()(const std::string& s) { std::cout << "str " << s; }
    void operator()(double d)      { std::cout << "float " << d; }
};
std::visit(Visitor{}, v);
```

或 C++17 的 overload trick：
```cpp
template <typename... Ts>
struct overload : Ts... { using Ts::operator()...; };

std::visit(overload{
    [](int x)                    { /* ... */ },
    [](const std::string& s)     { /* ... */ },
    [](double d)                 { /* ... */ }
}, v);
```

Variant 取代 OO polymorphism 的 closed hierarchy 案例（「Shape 就是 Circle | Square | Triangle」）。

## 18.3 `std::expected<T, E>` (C++23)

Rust 的 `Result<T, E>` 進 C++。**gcc 14+** 才有。

```cpp
#include <expected>

std::expected<int, std::string> parse_int(std::string_view s) {
    try {
        return std::stoi(std::string{s});
    } catch (const std::exception& e) {
        return std::unexpected(e.what());
    }
}

auto r = parse_int("42");
if (r) {
    std::cout << *r;
} else {
    std::cout << "error: " << r.error();
}
```

Monadic 方法同 optional：
```cpp
auto r = parse_int(input)
    .transform([](int x) { return x * 2; })
    .or_else([](std::string err) -> std::expected<int, std::string> {
        log(err);
        return 0;
    });
```

這是**現代 C++ 錯誤處理的終極形式**。

## 18.4 三者的選擇

| 情境 | 用 |
|---|---|
| 函式可能沒有結果（找不到、空） | `std::optional<T>` |
| 一個變數可能是幾種不相容的型別 | `std::variant<T, U, ...>` |
| 函式成功或失敗（帶錯誤訊息） | `std::expected<T, E>` (C++23) / `std::variant<T, Error>` |

## 18.5 C++20 仍然可以模擬 expected

在 gcc 13（沒 C++23）上用 variant：

```cpp
struct Error { std::string message; };

std::variant<int, Error> parse_int(std::string_view s) {
    try {
        return std::stoi(std::string{s});
    } catch (const std::exception& e) {
        return Error{e.what()};
    }
}

auto r = parse_int("42");
if (std::holds_alternative<int>(r)) {
    std::cout << std::get<int>(r);
} else {
    std::cout << std::get<Error>(r).message;
}
```

或直接用 tl::expected library（`tl/expected.hpp`），語義和 std 一樣。

## 18.6 `optional` 的坑

### 陷阱 1：`std::optional<bool>` 和 `std::optional<T*>`
```cpp
std::optional<bool> b = false;
if (b) { /* b 是 true（因為 has_value） */ }
if (*b) { /* b 的內容 */ }
```

容易搞混「*有沒有值*」和「*值是不是 truthy*」。謹慎使用 `optional<bool>`。

### 陷阱 2：`optional<T&>` 不被支援
```cpp
std::optional<int&> r;   // ❌ 編譯錯
```

想要「可能是空的 reference」用 `T*`（raw pointer）或 `std::optional<std::reference_wrapper<T>>`（醜）。

### 陷阱 3：對 empty 做 `*`
```cpp
std::optional<int> o;
int x = *o;   // UB
```

## 18.7 `variant` 的坑

### 陷阱 1：`std::get<T>(v)` 錯型別 throw
```cpp
std::variant<int, std::string> v = "hi";
int x = std::get<int>(v);    // throws
```

用 `std::holds_alternative<int>(v)` 先檢查，或 `std::get_if<int>(&v)`（回 pointer，空就是 null）。

### 陷阱 2：valueless_by_exception
如果 variant 在賦值時 throw，它可能進入 valueless 狀態：
```cpp
v.valueless_by_exception();    // true 的話很糟
```

保證 `T` 的 move 是 noexcept 可避免。

## 18.8 實例：parser

```cpp
struct Token { /* ... */ };

std::expected<Token, std::string> parse_token(std::string_view s);

auto parse_sequence(std::string_view input) -> std::expected<std::vector<Token>, std::string> {
    std::vector<Token> tokens;
    while (!input.empty()) {
        auto tok = parse_token(input);
        if (!tok) return std::unexpected(tok.error());
        tokens.push_back(std::move(*tok));
        input = advance(input);
    }
    return tokens;
}
```

沒 exception、沒 null pointer、錯誤傳播明確。

## 18.9 `std::any` 提一下

完全動態型別：
```cpp
#include <any>

std::any a = 42;
a = std::string{"hello"};
a = 3.14;

std::any_cast<double>(a);    // 3.14
```

`std::any` 可以裝**任何**型別。很少用——通常你知道候選，用 `variant` 更安全。

## 18.10 對比 Rust

| Rust | C++ |
|---|---|
| `Option<T>` | `std::optional<T>` |
| `Result<T, E>` | `std::expected<T, E>` (C++23) |
| `enum` with variants | `std::variant<T, U, ...>` |
| `match` | `std::visit` + overload |
| `?` operator | Monadic `and_then` |

Rust 用起來順——因為設計時就考慮。C++ 是「補上來」的，語法醜一點。

## 18.11 何時還是該 throw exception？

- **程式 bug**：assertion、invariant violation → throw（或 terminate）
- **真的罕見**的運行時錯誤：throw
- **預期會發生的失敗**（找不到、解析失敗、權限不夠）：**`optional` 或 `expected`**
- **性能敏感的 hot path**：別 throw（exception 本身貴）

Google 風格 code base 常完全禁用 exception，全用 expected。

## 18.12 練習

1. 實作 `std::optional<int> divide(int a, int b)`：b 是 0 回 nullopt。
2. 用 `std::variant<Circle, Square, Triangle>` + `std::visit` 寫一個 `double area(Shape)`。
3. （C++23）用 `std::expected` 寫一個 config loader：回 `std::expected<Config, std::string>`。

## 本章重點
- `std::optional<T>`：可能沒值
- `std::variant<T, U, ...>`：type-safe union
- `std::expected<T, E>`（C++23, gcc 14+）：現代錯誤處理
- 沒 C++23 時用 `std::variant<T, Error>` 模擬
- Monadic 方法（`transform` / `and_then` / `or_else`）chain 很乾淨
- Exception 留給真的異常，可預期失敗用 `expected`
