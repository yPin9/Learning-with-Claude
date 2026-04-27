# Ch11: STL Algorithms 與 Iterators

STL 算法庫讓你用宣告式的方式處理容器，取代手寫迴圈。本章先講經典 `std::sort` 系列，Ranges（Ch14）是它的現代化版本。

## 11.1 Iterator：容器的通用介面

Iterator 抽象了「指向容器某個元素」這件事。像 C 的 pointer 但更廣義。

```cpp
std::vector<int> v{1, 2, 3};

auto it = v.begin();   // 指第一個
++it;                  // 指第二個
*it;                   // 2

for (auto it = v.begin(); it != v.end(); ++it) {
    std::cout << *it;
}
```

`end()` 是「尾端**之後**」，不是最後一個。所以是半開區間 `[begin, end)`。

### Iterator 類別
- **Input**：只能往前讀一次
- **Output**：只能往前寫一次
- **Forward**：可多次 pass，只能往前
- **Bidirectional**：可雙向（`std::list`）
- **Random access**：可 `it + n` 跳（`std::vector`、`std::deque`、`std::array`）
- **Contiguous** (C++17)：保證連續記憶體

入門：**知道不同容器的 iterator 能力不同**。`std::list` 的 iterator 不能 `it + 3`，vector 的可以。

### const iterator
```cpp
std::vector<int> v;

auto it = v.begin();       // iterator
auto cit = v.cbegin();     // const_iterator（只讀）

const std::vector<int> cv;
auto it2 = cv.begin();     // 自動是 const_iterator
```

## 11.2 經典算法庫概覽

`<algorithm>` 標準庫有百來個函式。全部過一遍沒意義，挑最常用的。

```cpp
#include <algorithm>
#include <numeric>   // 另一堆
```

## 11.3 查找

```cpp
std::vector<int> v{3, 1, 4, 1, 5};

auto it = std::find(v.begin(), v.end(), 4);
if (it != v.end()) { /* 找到 */ }

auto it2 = std::find_if(v.begin(), v.end(),
    [](int x) { return x > 3; });

bool any = std::any_of(v.begin(), v.end(), [](int x){ return x > 4; });
bool all = std::all_of(v.begin(), v.end(), [](int x){ return x > 0; });
bool none = std::none_of(v.begin(), v.end(), [](int x){ return x > 100; });

auto n = std::count(v.begin(), v.end(), 1);   // 2
auto n2 = std::count_if(v.begin(), v.end(), [](int x){ return x > 2; });
```

## 11.4 排序

```cpp
std::vector<int> v{3, 1, 4, 1, 5};

std::sort(v.begin(), v.end());   // 升序
std::sort(v.begin(), v.end(), std::greater<>{});   // 降序
std::sort(v.begin(), v.end(), [](int a, int b){ return a > b; });

// 保持相同元素相對順序
std::stable_sort(v.begin(), v.end());

// 只需要前 N 個有序
std::partial_sort(v.begin(), v.begin() + 3, v.end());

// 第 k 個位置放 k-th 小元素（其他只保證分區）
std::nth_element(v.begin(), v.begin() + 2, v.end());
```

## 11.5 修改

```cpp
std::vector<int> v{1, 2, 3, 4, 5};

std::reverse(v.begin(), v.end());

std::fill(v.begin(), v.end(), 0);

std::transform(v.begin(), v.end(), v.begin(),
    [](int x) { return x * 2; });   // 原地 *2

std::vector<int> out(v.size());
std::transform(v.begin(), v.end(), out.begin(),
    [](int x) { return x * x; });   // out = v 每個元素的平方

std::copy(v.begin(), v.end(), out.begin());
std::copy_if(v.begin(), v.end(), std::back_inserter(out),
    [](int x) { return x > 0; });
```

## 11.6 移除

**重要**：容器算法不真的移除元素，只把要保留的推到前面，回傳「要刪掉之後」的 iterator。真的刪要配 `.erase()`。

```cpp
std::vector<int> v{1, 2, 3, 2, 1};

auto new_end = std::remove(v.begin(), v.end(), 2);
// v: {1, 3, 1, ?, ?}，new_end 指向第一個 ?
v.erase(new_end, v.end());   // 真的刪掉

// 或 erase-remove idiom（一行）
v.erase(std::remove(v.begin(), v.end(), 2), v.end());

// 或 C++20
std::erase(v, 2);        // 乾淨！
std::erase_if(v, [](int x){ return x < 0; });
```

**C++20 起用 `std::erase` / `std::erase_if`**。

## 11.7 數值算法 `<numeric>`

```cpp
#include <numeric>

std::vector<int> v{1, 2, 3, 4, 5};

int sum = std::accumulate(v.begin(), v.end(), 0);   // 15
int product = std::accumulate(v.begin(), v.end(), 1, std::multiplies<>{});

// C++17
int sum2 = std::reduce(v.begin(), v.end());          // 可並行
int sum3 = std::transform_reduce(v.begin(), v.end(), 0, std::plus<>{},
    [](int x) { return x * x; });   // sum of squares

std::iota(v.begin(), v.end(), 10);   // v = {10, 11, 12, 13, 14}
```

### ⚠️ `accumulate` 型別陷阱
```cpp
std::vector<long long> v{1'000'000'000'000LL};
auto sum = std::accumulate(v.begin(), v.end(), 0);
// ❌ 累加器是 int！會溢位
auto sum_ok = std::accumulate(v.begin(), v.end(), 0LL);   // ✅
```

## 11.8 集合操作（兩個有序範圍）

```cpp
std::vector<int> a{1, 2, 3, 4}, b{3, 4, 5, 6};
std::vector<int> out;

std::set_union(a.begin(), a.end(), b.begin(), b.end(), std::back_inserter(out));
// out: {1, 2, 3, 4, 5, 6}

std::set_intersection(a.begin(), a.end(), b.begin(), b.end(), std::back_inserter(out));
// out: {3, 4}

std::set_difference(a.begin(), a.end(), b.begin(), b.end(), std::back_inserter(out));
// out: {1, 2}
```

## 11.9 Permutation / 隨機

```cpp
#include <random>

std::vector<int> v{1, 2, 3, 4, 5};

std::random_device rd;
std::mt19937 gen{rd()};
std::shuffle(v.begin(), v.end(), gen);

std::next_permutation(v.begin(), v.end());   // 下一個字典序
std::prev_permutation(v.begin(), v.end());
```

**別用 C 的 `rand()`**——低品質、不建議。用 `<random>`。

## 11.10 `std::back_inserter` 和輸出 iterator

```cpp
std::vector<int> src{1, 2, 3};
std::vector<int> dst;

std::copy(src.begin(), src.end(), dst.begin());   // ❌ dst 是空的！
std::copy(src.begin(), src.end(), std::back_inserter(dst));   // ✅
```

`back_inserter` 回傳一個會 `push_back` 的 iterator。其他：
- `std::inserter(container, pos)`：在 pos 插入
- `std::front_inserter`
- `std::ostream_iterator<int>(std::cout, " ")`：寫到輸出流

## 11.11 執行政策（C++17 並行算法）

```cpp
#include <execution>

std::sort(std::execution::par, v.begin(), v.end());        // 並行
std::sort(std::execution::par_unseq, v.begin(), v.end()); // 並行 + 向量化
std::sort(std::execution::seq, v.begin(), v.end());       // 序列（預設）
```

一行加速，但注意：
- gcc 需連結 `-ltbb`（Threading Building Blocks）
- 述詞不能有 data race

## 11.12 迴圈 vs 算法：什麼時候用哪個？

**用算法的好處**：
- 更少 bug（off-by-one、邊界錯）
- 意圖更明確（`count_if` 比 for 迴圈易讀）
- 可能更快（並行、向量化）

**用迴圈的好處**：
- 邏輯複雜時讀得比 `transform + filter + accumulate` 直觀
- Debug 容易

一般建議：簡單意圖（查找、計數、排序、轉換）用算法，複雜流程寫 for。C++20 Ranges（Ch14）讓算法 chaining 更好寫，屆時天秤更偏算法。

## 11.13 Ranges 預告（C++20）

```cpp
#include <ranges>

std::vector<int> v{1, 2, 3, 4, 5};

std::ranges::sort(v);                      // 不用 begin/end
auto it = std::ranges::find(v, 3);

// Pipe syntax
auto squared = v
    | std::views::filter([](int x){ return x % 2 == 0; })
    | std::views::transform([](int x){ return x * x; });
```

Ch14 細講。Ranges 是 STL 算法的未來。

## 11.14 完整範例

```cpp
#include <algorithm>
#include <iostream>
#include <numeric>
#include <vector>

int main() {
    std::vector<int> v{5, 2, 8, 1, 9, 3, 7, 4, 6};

    // 排序
    std::sort(v.begin(), v.end());

    // 找大於 5 的個數
    auto n = std::count_if(v.begin(), v.end(),
        [](int x){ return x > 5; });
    std::cout << "count > 5: " << n << '\n';

    // 加總
    auto sum = std::accumulate(v.begin(), v.end(), 0);
    std::cout << "sum: " << sum << '\n';

    // 轉成平方
    std::vector<int> sq(v.size());
    std::transform(v.begin(), v.end(), sq.begin(),
        [](int x) { return x * x; });

    // 移除偶數
    sq.erase(std::remove_if(sq.begin(), sq.end(),
        [](int x) { return x % 2 == 0; }), sq.end());
    // C++20: std::erase_if(sq, [](int x){ return x % 2 == 0; });

    for (int x : sq) std::cout << x << ' ';
    std::cout << '\n';
}
```

## 11.15 練習

1. 用 `std::sort` + lambda 對 `std::vector<std::pair<std::string, int>>` 按第二個元素降序、第二優先級按第一個字母升序。
2. 用 STL 算法實作：讀 vector of int，輸出去重複後的降序前 5 名。

## 本章重點
- Iterator 是容器的統一介面
- 常用算法：`find` / `count` / `sort` / `transform` / `accumulate` / `erase-remove`
- **C++20 用 `std::erase` / `std::erase_if`**，比 erase-remove idiom 乾淨
- `accumulate` 小心累加器型別
- 用 `<random>`，不要 `rand()`
- Ranges 是 STL 算法的下一代（Ch14）
