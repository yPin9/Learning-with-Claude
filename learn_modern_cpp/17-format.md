# Ch17: std::format (C++20)

終於有格式化字串了。`printf` 不型別安全、`iostream` 又慢又囉嗦——`std::format` 是兩者的現代取代。

gcc 13+ 支援 `<format>`。gcc 23+ 支援 `std::print`/`std::println` (C++23)。

## 17.1 最小範例

```cpp
#include <format>
#include <iostream>

int main() {
    std::string s = std::format("Hello, {}!", "world");
    std::cout << s << '\n';

    std::cout << std::format("{} + {} = {}\n", 1, 2, 3);
}
```

用 `{}` 當 placeholder，參數照順序塞進去。

## 17.2 vs `printf` vs `iostream`

| | `printf` | `iostream` | `std::format` |
|---|---|---|---|
| 型別安全 | ❌ | ✅ | ✅ |
| 速度 | 快 | 慢 | 快（~printf） |
| 可讀性 | 中 | 差（`<<` 串一堆） | **好** |
| 編譯期檢查 | ❌ | ✅ | ✅（格式字串編譯期驗證） |
| 擴充自訂型別 | ❌ | 寫 `operator<<` | 寫 `std::formatter` |

`std::format` 是現代 C++ 的明顯贏家。

## 17.3 索引 & 具名

```cpp
std::format("{0} {1} {0}", "a", "b");    // "a b a"
```

位置可以重用。C++ 不支援具名參數（不像 Python 的 `{name}`）。

## 17.4 格式規格

和 Python f-string 幾乎一樣：

```cpp
std::format("{:d}", 42);      // 整數 "42"
std::format("{:x}", 255);     // 16 進位 "ff"
std::format("{:#x}", 255);    // "0xff"
std::format("{:08x}", 255);   // "000000ff"
std::format("{:b}", 5);       // 二進位 "101"
std::format("{:o}", 8);       // 八進位 "10"

std::format("{:.3f}", 3.14159);   // "3.142"
std::format("{:.3e}", 3.14159);   // "3.142e+00"
std::format("{:g}", 0.0001);

std::format("{:10}", "hi");       // "hi        "（右 padding）
std::format("{:>10}", "hi");      // "        hi"（右對齊）
std::format("{:^10}", "hi");      // "    hi    "（置中）
std::format("{:*^10}", "hi");     // "****hi****"

std::format("{:+}", 42);          // "+42"
std::format("{: }", 42);          // " 42"（正數加空格）
```

### 組合

```cpp
std::format("{:>10.3f}", 3.14);   // "     3.140"
std::format("{:08.3f}", 3.14);    // "0003.140"
```

## 17.5 動態寬度 / 精度

```cpp
int width = 10, prec = 3;
std::format("{:{}.{}f}", 3.14159, width, prec);  // "     3.142"
```

`{}` 當子參數塞進去。

## 17.6 格式化自訂型別

寫 `std::formatter<YourType>`：

```cpp
#include <format>

struct Point { int x, y; };

template <>
struct std::formatter<Point> {
    // 最簡單：不接受格式規格
    constexpr auto parse(std::format_parse_context& ctx) {
        return ctx.begin();
    }

    auto format(const Point& p, std::format_context& ctx) const {
        return std::format_to(ctx.out(), "({}, {})", p.x, p.y);
    }
};

int main() {
    Point p{3, 4};
    std::cout << std::format("Point: {}\n", p);   // "Point: (3, 4)"
}
```

更進階可以解析格式規格：
```cpp
std::format("{:<10}", p);   // 接受對齊規格
```

範例（較複雜）留給進階。

## 17.7 `std::format_to` / `std::vformat`

```cpp
std::string s;
std::format_to(std::back_inserter(s), "x={}, y={}", 1, 2);

// 輸出到 cout（省記憶體）
std::format_to(std::ostreambuf_iterator<char>(std::cout),
    "x={}, y={}", 1, 2);

// 動態格式字串 (runtime)
std::string fmt = "{}";
std::string out = std::vformat(fmt, std::make_format_args(42));
```

直接 `std::format(runtime_string, ...)` 會編譯錯——格式字串要 compile-time。要用 `std::vformat`。

## 17.8 C++23 `std::print` / `std::println`

```cpp
#include <print>

std::print("Hello, {}!\n", "world");
std::println("Hello, {}!", "world");   // 自動加 \n
```

比 `std::cout << std::format(...)` 乾淨，gcc 14+ 有。

## 17.9 編譯期格式檢查

```cpp
std::format("{}", 42);          // OK
std::format("{:d}", "hello");   // ❌ 編譯期錯：string 不是 integer
std::format("{}", 1, 2);        // ⚠️ 多餘參數，沒事但浪費
std::format("{} {}", 1);        // ❌ 編譯期錯：缺參數
```

編譯期抓的比 `printf` 好，因為 `printf` 只能靠 `__attribute__((format))` 外掛。

## 17.10 chrono 支援

```cpp
#include <chrono>
#include <format>

auto now = std::chrono::system_clock::now();
std::cout << std::format("{:%Y-%m-%d %H:%M:%S}\n", now);

using namespace std::chrono_literals;
auto dur = 3h + 25min;
std::cout << std::format("{:%H:%M}\n", dur);
```

C++20 把 chrono 整合進 format，終於不用 `std::put_time` / `strftime` 了。

## 17.11 Locale

```cpp
std::format("{:L}", 1000000);    // "1,000,000"（依 locale）
```

加 `L` 啟用 locale-aware 分隔符。

## 17.12 實用對照表

```cpp
// printf                    vs    std::format
printf("%d", x);             /*=*/  std::format("{}", x);
printf("%5d", x);            /*=*/  std::format("{:5}", x);
printf("%-5d", x);           /*=*/  std::format("{:<5}", x);
printf("%05d", x);           /*=*/  std::format("{:05}", x);
printf("%x", x);             /*=*/  std::format("{:x}", x);
printf("%.3f", x);           /*=*/  std::format("{:.3f}", x);
printf("%s", s.c_str());     /*=*/  std::format("{}", s);
printf("%p", p);             /*=*/  std::format("{}", (void*)p);
```

## 17.13 性能

`std::format` 大致和 `printf` 同量級，比 `iostream` 快。Embedded/極端性能可以考慮 `fmt` library（`std::format` 就是從它來的，持續更新更快）。

## 17.14 常見錯誤

### 錯誤 1：runtime 格式字串
```cpp
std::string fmt = read_fmt();
std::format(fmt, 42);    // ❌ fmt 不是 compile-time

std::vformat(fmt, std::make_format_args(42));   // ✅
```

### 錯誤 2：格式字串和參數對不上
```cpp
std::format("{}", 1, 2);     // 多餘參數：浪費但能編
std::format("{} {}", 1);     // 少參數：編譯錯
```

### 錯誤 3：把 `std::string` 傳 `printf`
```cpp
std::string s = "hi";
printf("%s", s);          // ❌ UB（要 s.c_str()）
std::format("{}", s);     // ✅ format 懂 std::string
```

## 17.15 練習

1. 寫一個 log 函式 `log(level, fmt, args...)`：第一個參數是等級（字串或 enum），之後用 format 格式化。時間用 chrono。
2. 給一個 `struct Date { int y, m, d; }` 實作 `std::formatter<Date>`，支援 `{:iso}` 和 `{:us}` 兩種格式規格。

## 本章重點
- `std::format` = printf 的型別安全、現代化取代
- `{}` placeholder，可選格式規格類似 Python f-string
- 編譯期檢查格式字串
- 自訂型別：特化 `std::formatter<T>`
- Runtime 格式字串：`std::vformat`
- C++23 `std::print` / `std::println` 更乾淨
- 別再用 `printf`（除非 embed without libstdc++）
