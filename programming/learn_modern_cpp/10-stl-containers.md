# Ch10: STL Containers

STL (Standard Template Library) 是 C++ 比 C 最實用的優勢。本章把常用容器過一遍，重點在**什麼時候該選哪個**。

## 10.1 快速選擇表

| 需求 | 用這個 |
|---|---|
| 動態陣列（預設首選） | `std::vector` |
| 固定大小陣列 | `std::array` |
| 字串 | `std::string` |
| Key-value 查表（有序） | `std::map` |
| Key-value 查表（無序，快） | `std::unordered_map` |
| Set（有序） | `std::set` |
| Set（無序，快） | `std::unordered_set` |
| 雙端佇列 | `std::deque` |
| Queue / Stack / Priority queue | `std::queue` / `std::stack` / `std::priority_queue` |
| 雙向鏈結串列 | `std::list`（**你大概不需要**） |
| 單向鏈結串列 | `std::forward_list`（**你更不需要**） |

**首選 `std::vector`**。99% 情況 vector 就是對的。

## 10.2 `std::vector`

動態陣列，連續記憶體。**預設容器**。

```cpp
#include <vector>

std::vector<int> v;              // 空
std::vector<int> v2{1, 2, 3};    // 初值
std::vector<int> v3(10);         // 10 個 0
std::vector<int> v4(10, -1);     // 10 個 -1
```

### 常用操作
```cpp
v.push_back(42);           // 塞後面 (amortized O(1))
v.emplace_back(42);        // 就地建構
v.pop_back();              // 拿掉最後

v.size();                  // 元素數
v.empty();                 // 是否空
v.capacity();              // 已分配空間（>= size）
v.reserve(1000);           // 預分配，避免重複 realloc
v.clear();                 // 清空但不釋放 capacity

v[0];                      // 不檢查邊界
v.at(0);                   // 檢查邊界（throw out_of_range）
v.front(); v.back();

v.insert(v.begin() + 2, 99);    // 插入（O(n)）
v.erase(v.begin() + 2);         // 移除（O(n)）
```

### 性能要點
- **push_back** 大多時候 O(1)，容量爆掉時重新分配（複製或 move 所有元素）
- 中間 insert/erase 是 O(n)
- 連續記憶體 → cache 超好 → 實際比 `std::list` 快很多，即使理論 O(n)

### 預先 `reserve`
```cpp
std::vector<int> v;
v.reserve(1000);           // 一次分配
for (int i = 0; i < 1000; ++i) v.push_back(i);
```

知道大小時預留可以省重新分配。

### Iterator 失效
操作會讓 iterator/reference 失效：
- `push_back` 造成重新分配 → **所有** iterator 失效
- `insert` / `erase` → 操作點之後的 iterator 失效

```cpp
for (auto& x : v) {
    if (x == 0) v.push_back(-1);   // ❌ 可能讓正在用的 iterator 失效
}
```

## 10.3 `std::array`

**編譯期大小**的陣列。比 C array 多了 STL 介面。

```cpp
#include <array>

std::array<int, 5> a{1, 2, 3, 4, 5};

a.size();       // 5（永遠）
a[0];
a.front(); a.back();

for (int x : a) { /* ... */ }
```

用在已知大小的場合。比 C 陣列好：
- 知道自己大小（`.size()`）
- 可以複製、可以回傳
- 邊界檢查（`.at()`）

## 10.4 `std::string`

```cpp
#include <string>

std::string s = "hello";
s += " world";
s.append("!");
s.size(); s.length();
s.empty();
s[0]; s.at(0);
s.substr(0, 5);            // "hello"
s.find("world");           // 回傳 index 或 std::string::npos
s.replace(0, 5, "hi");

// 連接
std::string greet = "Hello, " + name + "!";

// 轉換
int n = std::stoi("42");
double d = std::stod("3.14");
std::string str = std::to_string(42);
```

### `std::string_view`（C++17）

**不擁有**的字串 view，輕量傳遞字串。Ch19 細講。預告：

```cpp
void print(std::string_view s) {
    std::cout << s << '\n';
}

print("hello");          // 不複製
print(std::string{"world"});
```

**參數預設用 `std::string_view`**，除非你要保存字串。

## 10.5 `std::map` 與 `std::unordered_map`

### `std::map`（紅黑樹，有序）
```cpp
#include <map>

std::map<std::string, int> ages{
    {"Alice", 30},
    {"Bob", 25}
};

ages["Carol"] = 40;          // 插入或更新
ages.insert({"Dave", 35});
ages.emplace("Eve", 28);

auto it = ages.find("Alice");
if (it != ages.end()) {
    std::cout << it->second;
}

// C++20
if (ages.contains("Alice")) { /* ... */ }

ages.erase("Bob");
```

### `std::unordered_map`（Hash table，快）
```cpp
#include <unordered_map>

std::unordered_map<std::string, int> m;
m["key"] = 42;
```

介面幾乎一樣。差別：

| | `std::map` | `std::unordered_map` |
|---|---|---|
| 底層 | 紅黑樹 | Hash table |
| 查找 | O(log n) | O(1) 平均 |
| 有序 | ✅ | ❌ |
| 迭代順序 | key 排序 | 不定 |
| 需要 | `operator<` | `std::hash<Key>` |

**預設用 `unordered_map`**（更快）。需要有序或 key 沒 hash 才用 `map`。

### `operator[]` 陷阱
```cpp
std::map<std::string, int> m;
int x = m["missing_key"];   // ⚠️ 會插入一個 default 值！

if (m.find("key") != m.end()) { ... }   // 查找用 find 或 contains
```

### C++17：`try_emplace` 與 `insert_or_assign`
```cpp
m.try_emplace("key", 42);         // 只在不存在時插入（省建構）
m.insert_or_assign("key", 42);    // 總是設成 42
```

## 10.6 `std::set` / `std::unordered_set`

集合（不重複元素）。

```cpp
std::set<int> s{3, 1, 4, 1, 5};   // {1, 3, 4, 5}
s.insert(9);
s.contains(3);     // C++20
s.erase(1);
```

`unordered_set` 對應 hash 版本。選擇邏輯同 map。

## 10.7 `std::deque`

雙端佇列，兩端都可以 O(1) push/pop。

```cpp
std::deque<int> dq;
dq.push_back(1);
dq.push_front(0);
```

底層通常是「多個固定大小 chunk 的 array」，不是連續記憶體（pointer 取得的位址不連續）。

用途：需要雙端操作時。`std::queue` 預設底層就是 deque。

## 10.8 `std::list` 和為何你不要用

雙向鏈結串列。

理論上 insert/erase 是 O(1)，實際上：
- **cache 極差**（每個節點都在不同 heap 位置）
- **每個節點多兩個 pointer**
- 連走訪都比 vector 慢

**除非**你有非常特殊的需求（例如需要穩定的 iterator 在任何操作下都不失效），否則**用 `std::vector`**。

業界常見說法：「`std::list` 幾乎沒有對的使用情境」。

## 10.9 Container adapter：stack / queue / priority_queue

這些不是獨立容器，是**包裝器**。

```cpp
#include <stack>
#include <queue>

std::stack<int> st;
st.push(1); st.push(2);
st.top();        // 2
st.pop();

std::queue<int> q;
q.push(1); q.push(2);
q.front();       // 1
q.pop();

std::priority_queue<int> pq;   // 預設最大堆
pq.push(3); pq.push(1); pq.push(4);
pq.top();   // 4
```

## 10.10 自訂型別作為 key

```cpp
struct Point { int x, y; };

// std::map 需要 operator<
bool operator<(const Point& a, const Point& b) {
    return std::tie(a.x, a.y) < std::tie(b.x, b.y);
}

std::map<Point, std::string> m;
```

C++20：用 `<=>`（spaceship operator）一次搞定所有比較：
```cpp
struct Point {
    int x, y;
    auto operator<=>(const Point&) const = default;
};
```

### `unordered_map` 需要 hash
```cpp
struct Point { int x, y; bool operator==(const Point&) const = default; };

template <>
struct std::hash<Point> {
    size_t operator()(const Point& p) const {
        return std::hash<int>{}(p.x) ^ (std::hash<int>{}(p.y) << 1);
    }
};

std::unordered_map<Point, std::string> m;
```

## 10.11 容器元素要求

- **Vector / deque**：元素要可 copy 或 move
- **Map / set**：要可比較（`<` 或 `<=>`）
- **Unordered**：要可 hash 和 `==`

放 `std::unique_ptr<T>` 進 vector 完全 OK（move-only）。

## 10.12 C++20 的新容器？

C++20 沒新容器，但加了：
- `.contains()` 給 map/set
- `erase_if()` 自由函式給容器
- String 的 `.starts_with()`、`.ends_with()`

```cpp
std::string s = "hello.txt";
s.ends_with(".txt");       // true

std::erase_if(v, [](int x) { return x < 0; });
```

## 10.13 練習

1. 讀文字檔每個單字，用 `unordered_map<string, int>` 計數，按次數降序輸出前 10 名。
2. 寫一個 LRU cache（`unordered_map` + `list`）。

## 本章重點
- **預設 `std::vector`**，有需求再換
- 要查表用 `unordered_map`，需要有序才 `map`
- **別用 `std::list`**
- `std::array` 是 fixed-size 的好選擇
- 函式參數用 `std::string_view`，保存用 `std::string`
- C++20 的 `.contains()` 比 `.find() != end()` 乾淨
