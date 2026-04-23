# Ch19: std::span 與 std::string_view

兩個「不擁有」的 view 型別。很輕、傳遞超省。

## 19.1 `std::string_view` (C++17)

**唯讀字串的輕量視圖**。內部就是一個 pointer + length。

```cpp
#include <string_view>

void print(std::string_view s) {
    std::cout << s << '\n';
}

print("hello");                 // ✅ 不複製（指向 literal）
print(std::string{"world"});    // ✅ 不複製
print(std::string{"hi"}.c_str()); // ✅ 
```

`std::string_view` 可以從 `const char*`、`std::string`、string literal 來。

### 為什麼要有？

傳統問題：
```cpp
void f(const std::string& s);

f("hello");    // ⚠️ 會建立暫時 std::string（分配、複製）
```

換成 `string_view` 就沒這個分配。**參數預設該用 `string_view`**，除非你要存起來。

### 操作

```cpp
std::string_view sv = "hello world";
sv.size();                 // 11
sv.substr(6);              // "world"（不分配）
sv.find("world");          // 6
sv.starts_with("hello");   // true (C++20)
sv.ends_with("world");
sv.remove_prefix(6);       // sv = "world"
sv.remove_suffix(5);       // 切掉後 5 char
```

介面像 `std::string` 唯讀子集。

### 大坑：懸垂

```cpp
std::string_view bad() {
    std::string s = "hello";
    return s;    // ❌ s 解構，view 懸垂
}

std::string_view worse = std::string{"hi"};   // ❌ 暫時物件解構
```

**`string_view` 不延長生命期**（和 `const T&` 不同）。規則：
- **函式參數安全**：caller 的字串在函式執行期間存在
- **不要保存 `string_view`**（當 member 或 return）除非你確定被 view 的字串壽命更長
- **不要從暫時 `std::string` 建立 `string_view`**

## 19.2 `std::span<T>` (C++20)

`string_view` 的通用版——「對連續記憶體的 view」：

```cpp
#include <span>

void sum(std::span<const int> data) {
    int total = 0;
    for (int x : data) total += x;
    std::cout << total;
}

int arr[] = {1, 2, 3};
std::vector<int> v{4, 5, 6};
std::array<int, 3> a{7, 8, 9};

sum(arr);         // ✅
sum(v);           // ✅
sum(a);           // ✅
sum({1, 2, 3});   // ✅ (initializer_list)
```

一個函式吃所有連續容器。

### 動態 vs 固定大小
```cpp
std::span<int> dyn;              // 動態大小
std::span<int, 5> fixed;         // 固定 5 個
```

固定大小在編譯期檢查長度。

### 操作

```cpp
span.size();
span.empty();
span[i];
span.front(); span.back();
span.data();                     // 底層 pointer

span.first(3);                   // 前 3 個
span.last(3);                    // 後 3 個
span.subspan(2, 3);              // 從 index 2 起 3 個
```

### 可寫

```cpp
void zero(std::span<int> data) {
    for (int& x : data) x = 0;
}
```

`std::span<int>` 可讀可寫，`std::span<const int>` 只讀。

## 19.3 取代 C 風格 `(T*, size_t)`

經典 C 介面：
```cpp
void process(int* data, size_t len);
```

現代 C++：
```cpp
void process(std::span<int> data);
```

好處：
- `span` 攜帶長度，函式不用兩個參數
- Iterator、range-based for 自動 work
- 可從多種容器隱式建立

## 19.4 `span` vs `vector&`

```cpp
void f1(const std::vector<int>& v);    // 只能吃 vector
void f2(std::span<const int> v);       // 吃 vector、array、C array、initializer_list
```

**參數用 span 更靈活**，但：
- `span` 拿不到 vector 的 `push_back`、`resize`（因為它不擁有）
- 如果函式需要改大小，還是用 `vector&`

## 19.5 結合 string_view 與 span

```cpp
std::string s = "hello";
std::string_view sv = s;
std::span<const char> sp{s};

// 字串就是連續 char，所以 string_view 和 span<const char> 可以互轉
```

## 19.6 性能

```cpp
void old(const std::string& s);   // 可能有暫時物件分配
void mod(std::string_view s);     // 零分配
```

在 hot path 上可以明顯省時間。但：
- **`string_view` 本身 16 bytes**（pointer + size），不算超輕
- 對超短字串 `const std::string&` 可能更好（small string optimization 在 stack）

實務上 `string_view` 幾乎總是贏。

## 19.7 Ranges 整合

span 和 string_view 都是 ranges：

```cpp
std::span<int> s = /* ... */;

for (int x : s | std::views::filter(...)) { /* ... */ }

std::ranges::sort(s);   // ✅
```

## 19.8 C API 介接

C API 通常長這樣：
```c
void c_func(const char* data, size_t len);
```

C++ 側：
```cpp
void cpp_func(std::span<const char> data) {
    c_func(data.data(), data.size());
}
```

乾淨橋接。

## 19.9 陷阱 / 反模式

### 陷阱 1：從暫時物件建 view
```cpp
auto bad() {
    std::string_view sv = get_string();  // 如果 get_string() 回 std::string by value
    return sv;                           // ❌ 暫時 string 解構，sv 懸垂
}
```

### 陷阱 2：存 view 為 member
```cpp
class Config {
    std::string_view name_;   // ⚠️ 小心：name_ 指的字串生命期誰負責？
public:
    Config(std::string_view n) : name_(n) {}
};

Config c{"hello"};   // string literal，永續 → 安全
Config c2{std::string{"hi"}};  // ❌ 暫時 string 解構，name_ 懸垂
```

存 member 要嘛：
- 用 `std::string`（擁有）
- 明確文件「caller 要保持字串活著」
- 接 `std::string_view` 建構但內部存 `std::string`

### 陷阱 3：對 `std::string_view` 做 `c_str()`
```cpp
sv.data();    // 不保證 null-terminated！
```

`string_view` 可能只是 substring。要 `c_str()` 先複製回 `std::string`。

## 19.10 實戰：字串處理函式

```cpp
bool starts_with_prefix(std::string_view s, std::string_view prefix) {
    return s.substr(0, prefix.size()) == prefix;
}

std::vector<std::string_view> split(std::string_view s, char delim) {
    std::vector<std::string_view> parts;
    while (!s.empty()) {
        auto pos = s.find(delim);
        if (pos == std::string_view::npos) {
            parts.push_back(s);
            break;
        }
        parts.push_back(s.substr(0, pos));
        s.remove_prefix(pos + 1);
    }
    return parts;
}
```

**零分配**的字串切割——輸入字串活著的期間都有效。

## 19.11 練習

1. 寫一個 `int sum(std::span<const int>)`，可用於 vector、array、C array。
2. 寫一個 trim 函式，輸入 `std::string_view`，回傳 `std::string_view`（切掉前後空白）。

## 本章重點
- `std::string_view`：唯讀字串的輕量 view
- `std::span<T>`：連續記憶體 view（const 或可寫）
- 兩者都**不擁有**、**不延長壽命**
- 參數用它們，取代 `const std::string&` 和 `(T*, size_t)`
- 陷阱：別保存 view 指向暫時物件；`data()` 不保證 null-terminated
- 零分配字串處理（split、trim、parse）的關鍵工具
