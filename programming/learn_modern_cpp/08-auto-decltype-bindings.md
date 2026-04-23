# Ch8: auto、decltype、結構化綁定

小章但實用。這三個特性會大幅改變你寫 C++ 的手感。

## 8.1 `auto`：讓編譯器推型別

```cpp
auto x = 10;              // int
auto y = 3.14;            // double
auto s = "hello";         // const char*  （⚠️ 不是 std::string！）
auto z = std::string{"hi"};   // std::string

std::vector<int> v;
auto it = v.begin();       // std::vector<int>::iterator  （救命！）
```

### 幾個規則
- `auto` 推型別像是「把 `=` 右邊的值拿去初始化一個變數」
- 推出來的型別**不會是 reference**（除非你寫 `auto&`）
- `const` 也會丟掉（除非你寫 `const auto`）

```cpp
int x = 10;
const int& r = x;

auto a = r;         // int（不是 const int&）
const auto& b = r;  // const int&
auto& c = r;        // const int&（const 會保留，因為你要綁 reference）
```

### 什麼時候用 `auto`？

**建議用**：
- 型別非常長（iterator、lambda 等）
- 型別已經很明顯（`auto x = std::make_unique<Foo>();`）
- 寫 generic code

**不建議用**：
- 型別對讀者很重要卻不明顯（`auto n = get_count();` 是 int？size_t？）
- 函式回傳型別不熟悉時

一個經驗法則（Herb Sutter 提倡的「AAA」）：**Almost Always Auto**——只要右邊能確定型別就用 auto。但社群有爭議，看團隊風格。

### `auto` 與 `{}` 初始化的小坑
```cpp
auto a{1};        // C++17 起是 int；C++11 曾是 initializer_list<int>
auto b = {1};     // 是 initializer_list<int>
auto c{1, 2};     // ❌ 錯：auto 的 direct-list-init 只能一個元素
auto d = {1, 2};  // initializer_list<int>
```

實務上用 `auto x = expr;` 最不容易踩雷。

## 8.2 `auto` 作為函式回傳型別

```cpp
auto add(int a, int b) {
    return a + b;    // 回傳 int
}

auto make_vec() {
    return std::vector<int>{1, 2, 3};
}
```

優點：改 return type 一個地方就好。
缺點：看 header 看不出回傳型別，IDE/文件友善度降低。

**API header 建議不用**，內部函式可以。

### Trailing return type
```cpp
auto divide(int a, int b) -> double {
    return double(a) / b;
}
```

當回傳型別依賴參數時有用：
```cpp
template <typename T, typename U>
auto add(T a, U b) -> decltype(a + b) {
    return a + b;
}
```

C++14 後大多情況可以省掉，用單純 `auto`。

## 8.3 `decltype`

拿某個運算式的**型別**（不是值）。

```cpp
int x = 10;
decltype(x) y = 20;           // int

decltype(x + 1) z = 5;        // int

std::vector<int> v;
decltype(v)::iterator it;     // vector<int>::iterator
```

兩個有趣的規則：
- `decltype(name)` 拿宣告型別
- `decltype(expression)` 可能多了 reference（取決於 value category）

```cpp
int x = 10;
decltype(x) a;      // int
decltype((x)) b = x;  // int&！（表達式 (x) 是 lvalue）
```

這個細節通常只在 template metaprogramming 用到，入門不用深究。

### `decltype(auto)`
```cpp
decltype(auto) f() {
    int x = 10;
    return (x);   // decltype((x)) == int&，❌ 回傳 local 的 reference
}
```

比 `auto` 更「保留 reference 語意」。**少用**，很多坑。

## 8.4 結構化綁定 (Structured Bindings)

C++17 最甜的特性之一。一次拆解多個值。

### 拆 pair / tuple
```cpp
std::pair<int, std::string> p{42, "hello"};

auto [id, name] = p;     // id = 42, name = "hello"
```

### 拆 map 迭代
```cpp
std::map<std::string, int> ages{{"Alice", 30}, {"Bob", 25}};

for (const auto& [name, age] : ages) {
    std::cout << name << ": " << age << '\n';
}
```

對比 C++17 前：
```cpp
for (const auto& pair : ages) {
    std::cout << pair.first << ": " << pair.second << '\n';
}
```

### 拆 struct
```cpp
struct Point { int x; int y; };
Point p{3, 4};

auto [x, y] = p;     // x = 3, y = 4
```

### 拆 array
```cpp
int arr[] = {1, 2, 3};
auto [a, b, c] = arr;
```

### 用 reference 避免複製
```cpp
std::map<std::string, BigObject> m;

for (auto& [key, value] : m) {      // 不複製
    value.modify();
}

for (const auto& [key, value] : m) { // 只讀
    read(value);
}
```

### 和 `if` / `switch` 初始化結合 (C++17)
```cpp
if (auto [it, inserted] = m.insert({"key", 42}); inserted) {
    std::cout << "new entry\n";
} else {
    std::cout << "already there: " << it->second << '\n';
}
```

## 8.5 Range-based for

```cpp
std::vector<int> v{1, 2, 3};

for (int x : v)           { /* 複製 */ }
for (int& x : v)          { /* 可修改 */ }
for (const int& x : v)    { /* 只讀不複製 */ }
for (auto x : v)          { /* 複製 */ }
for (auto& x : v)         { /* 可修改 */ }
for (const auto& x : v)   { /* 推薦預設 */ }
```

**慣例：`const auto&` 是最安全的預設**，不會誤複製、不會誤改。

### C++20 init statement
```cpp
for (auto v = make_vec(); auto x : v) {
    // v 只在迴圈內存在
}
```

## 8.6 `if constexpr`（預告）

和 `auto` 配合很香，Ch12 詳講。預告：

```cpp
template <typename T>
void process(T value) {
    if constexpr (std::is_integral_v<T>) {
        // 只在 T 是整數時編譯
    } else {
        // 只在 T 不是整數時編譯
    }
}
```

## 8.7 實用 pattern 匯總

### Pattern 1：好讀的 iterator 迴圈
```cpp
// 舊
for (std::vector<MyType>::const_iterator it = v.begin(); it != v.end(); ++it) {
    it->do_thing();
}

// 新
for (const auto& x : v) {
    x.do_thing();
}
```

### Pattern 2：拆複雜回傳值
```cpp
std::tuple<int, std::string, bool> parse(const std::string& input);

auto [code, message, success] = parse(input);
```

### Pattern 3：結構化綁定 + 結構
```cpp
struct Response { int status; std::string body; };

Response fetch();

auto [status, body] = fetch();
```

這讓「小型多值回傳」變得舒服，不用每次寫 getter。

## 8.8 什麼時候別用 auto？

```cpp
// ⚠️ 讀者不知道 parse 回傳啥
auto result = parse(input);

// 好一點：表達意圖
ParseResult result = parse(input);
```

```cpp
// ⚠️ 看似無害但其實有坑
auto sum = std::accumulate(v.begin(), v.end(), 0);
// accumulate 回傳型別是第三個參數的型別（int）
// 如果 v 裡裝 long long，就會溢位！

// 明確：
long long sum = std::accumulate(v.begin(), v.end(), 0LL);
```

## 8.9 練習

1. 改寫下面 code 用 `auto` + 結構化綁定：
```cpp
std::map<int, std::string> m;
for (std::map<int, std::string>::iterator it = m.begin(); it != m.end(); ++it) {
    std::cout << it->first << ": " << it->second << '\n';
}
```

2. 寫一個函式回傳 `std::tuple<bool, int, std::string>`（success、code、message），caller 用結構化綁定接。

## 本章重點
- `auto` 推值，`auto&` 推 reference，`const auto&` 最安全的迴圈預設
- `decltype(x)` 拿 x 的型別
- 結構化綁定 `auto [a, b] = pair` 拆 pair/tuple/struct
- `for (const auto& [k, v] : map)` 是 modern map 迭代範式
- `if (init; cond)` 限縮變數 scope
