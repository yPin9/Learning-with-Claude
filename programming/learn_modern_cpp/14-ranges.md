# Ch14: Ranges 與 Views (C++20)

Ranges 是 STL 的現代化改版。同樣演算法，語法乾淨三倍。

## 14.1 為什麼 Ranges？

經典 STL 問題：
1. 每個呼叫都寫 `v.begin(), v.end()`，囉嗦
2. 組合演算法要寫一堆中間容器
3. 沒 pipe 語法

```cpp
// 經典 STL：想要「v 裡偶數的平方，排序後取前 3 個」
std::vector<int> tmp1;
std::copy_if(v.begin(), v.end(), std::back_inserter(tmp1),
    [](int x){ return x % 2 == 0; });

std::vector<int> tmp2(tmp1.size());
std::transform(tmp1.begin(), tmp1.end(), tmp2.begin(),
    [](int x){ return x * x; });

std::sort(tmp2.begin(), tmp2.end());

std::vector<int> result(3);
std::copy_n(tmp2.begin(), 3, result.begin());
```

Ranges 版本：
```cpp
auto result = v
    | std::views::filter([](int x){ return x % 2 == 0; })
    | std::views::transform([](int x){ return x * x; });
// 排序和取前 3 需要實體化（見後）
```

## 14.2 Ranges 的三個概念

1. **Range**：任何有 `begin()`/`end()` 的東西（vector、string、array、even initializer_list）
2. **Algorithm**：`std::ranges::sort(v)`——不用寫 begin/end
3. **View**：**惰性求值**的 range，用 pipe 組合

```cpp
#include <ranges>
namespace rg = std::ranges;
namespace vw = std::views;
```

## 14.3 Range 版本的 algorithms

```cpp
std::vector<int> v{3, 1, 4, 1, 5, 9, 2, 6};

rg::sort(v);                      // 取代 std::sort(v.begin(), v.end())
auto it = rg::find(v, 5);
auto n = rg::count(v, 1);
bool has_big = rg::any_of(v, [](int x){ return x > 8; });

rg::transform(v, v.begin(), [](int x){ return x * 2; });
```

**幾乎所有 `std::` 算法都有 `std::ranges::` 版本**。建議 C++20 新 code 直接用 ranges 版。

### Projections（特有功能）

```cpp
struct Person { std::string name; int age; };
std::vector<Person> people{/* ... */};

rg::sort(people, {}, &Person::age);   // 按 age 排
rg::find(people, "Alice", &Person::name);
```

第三參數是 projection：把元素映射成你要比較/找的東西。大幅減少 lambda。

## 14.4 Views：惰性 range

**View 不擁有資料、不複製**，只是「看」的方式。pipe (`|`) 組合 views。

```cpp
std::vector<int> v{1, 2, 3, 4, 5, 6};

auto even = v | vw::filter([](int x){ return x % 2 == 0; });
// even 是一個 view，還沒實際計算

for (int x : even) std::cout << x;    // 實際迭代時才執行 filter
// 輸出：246
```

### 常用 views
```cpp
vw::filter(pred)                // 篩選
vw::transform(func)             // 映射
vw::take(n)                     // 前 n 個
vw::take_while(pred)            // while 條件成立
vw::drop(n)                     // 跳過前 n 個
vw::drop_while(pred)
vw::reverse                     // 反轉
vw::elements<I>                 // 拿 tuple/pair 的第 I 個
vw::keys                        // 等於 elements<0>
vw::values                      // 等於 elements<1>
vw::split(delim)                // 切割
vw::join                        // 把 range of ranges 攤平
vw::iota(start)                 // 0, 1, 2, ...
vw::iota(start, end)            // start..end（不含 end）
vw::single(x)                   // [x]
vw::empty<T>                    // 空 range
```

### 組合範例

```cpp
// 1. 前 10 個平方數
auto squares = vw::iota(1)
             | vw::transform([](int x){ return x * x; })
             | vw::take(10);

// 2. map 的 keys
std::map<std::string, int> m{{"a", 1}, {"b", 2}};
for (const auto& k : m | vw::keys) std::cout << k;

// 3. 取奇數、乘 3、前 5 個
auto r = v | vw::filter([](int x){ return x % 2; })
           | vw::transform([](int x){ return x * 3; })
           | vw::take(5);

for (int x : r) std::cout << x << ' ';
```

## 14.5 Views 是惰性的

```cpp
auto v = std::vector{1, 2, 3, 4, 5};

auto view = v | vw::transform([](int x){
    std::cout << "transforming " << x << '\n';
    return x * 2;
});

// 這裡還沒印任何東西

for (int x : view) std::cout << "got " << x << '\n';
// 這才開始：
// transforming 1
// got 2
// transforming 2
// got 4
// ...
```

好處：不用中間容器、記憶體省、early termination 自動生效。
壞處：每次迭代都重算——要多次用，**實體化**。

## 14.6 實體化：`to<vector>` (C++23)

把 view 存成真的容器：

```cpp
auto r = v | vw::filter(/*...*/) | vw::transform(/*...*/);

// C++23
auto vec = r | std::ranges::to<std::vector>();

// C++20（手動）
std::vector<int> vec(r.begin(), r.end());
```

gcc 14 才支援 `std::ranges::to`。gcc 13 用手動版本。

## 14.7 Range concepts

配合 Ch13：
- `std::ranges::range<R>`：有 begin/end
- `std::ranges::sized_range<R>`：+ size
- `std::ranges::random_access_range<R>`：隨機存取
- `std::ranges::contiguous_range<R>`：連續記憶體
- `std::ranges::view<R>`：是 view

寫泛型函式很好用：
```cpp
void print(std::ranges::range auto&& r) {
    for (const auto& x : r) std::cout << x << ' ';
    std::cout << '\n';
}

print(v);
print(v | vw::reverse);
print(std::vector{1,2,3});
```

## 14.8 與經典 STL 混用

```cpp
auto view = v | vw::filter([](int x){ return x > 0; });

std::vector<int> out(view.begin(), view.end());  // OK

std::sort(view.begin(), view.end());             // ❌ view 不是 container，可能不支援 sort
std::ranges::sort(out);                          // ✅
```

有些 view 不能 sort（filter view 不是 random access）。想排序先實體化。

## 14.9 字串切割範例

```cpp
#include <string>
#include <string_view>

std::string text = "foo,bar,baz,qux";

auto parts = text
    | vw::split(',')
    | vw::transform([](auto r) {
          return std::string_view(r.begin(), r.end());
      });

for (auto p : parts) std::cout << p << '\n';
```

比 C 的 `strtok` 安全一萬倍。

## 14.10 Range-based for 更強了

```cpp
for (auto&& [i, x] : vw::enumerate(v)) {   // C++23
    std::cout << i << ": " << x << '\n';
}
```

C++23 `vw::enumerate` 帶索引迭代（gcc 14+）。C++20 土炮版：
```cpp
for (size_t i = 0; auto&& x : v) {
    std::cout << i++ << ": " << x << '\n';
}
```

（配合 `i++` 在 for 的 init statement 裡。）

## 14.11 實戰：把文字裡的數字加總

```cpp
#include <iostream>
#include <ranges>
#include <string>
#include <sstream>

int main() {
    std::string text = "1 2 3 4 5 6 7 8 9 10";
    std::istringstream iss{text};

    auto nums = std::views::istream<int>(iss);  // 把 stream 當 range

    int sum = 0;
    for (int n : nums | vw::filter([](int x){ return x % 2 == 0; })) {
        sum += n;
    }

    std::cout << "even sum: " << sum << '\n';
}
```

## 14.12 Ranges 的陷阱

### 陷阱 1：懸垂 view
```cpp
auto bad() {
    std::vector<int> v{1,2,3};
    return v | vw::filter(/*...*/);   // ❌ v 解構，view 懸垂
}
```

### 陷阱 2：View 的 iterator 失效
Views 本身是 lightweight object，但它參照的底層容器改動可能讓它失效。

### 陷阱 3：gcc 支援度
gcc 13：大多 view 可用
gcc 14+：支援 `std::ranges::to`、`enumerate`、`chunk`、`slide` 等 C++23 views

如果編譯錯，確認 gcc 版本。

## 14.13 練習

1. 有 `std::vector<Person>`（name + age），用 ranges 取「成年人按 name 排序，回傳 name list」。
2. 生成費氏數列前 N 個（用 view）。

<details>
<summary>2 提示</summary>

沒有現成的 fibonacci view，最簡單用 `vw::iota` + `vw::transform` 並實作 fib 函式（或用 lambda 帶狀態，需 mutable）。或實體化到 vector 後用 accumulate。
</details>

## 本章重點
- `std::ranges::algo(container)` 取代 `std::algo(begin, end)`
- Views + pipe 語法組合乾淨
- Views **惰性**、**不擁有**、**可組合**
- `std::ranges::to<vector>()`（C++23）實體化
- Projections 省 lambda
- gcc 13 支援絕大多數 C++20 ranges，gcc 14+ 補 C++23 views
