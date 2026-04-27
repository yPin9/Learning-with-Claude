# Ch13: Concepts (C++20)

Concepts 是 C++20 的旗艦特性之一。核心功能：**對 template 參數加「條件」**，讓錯誤訊息可讀、讓 overload 可控。

## 13.1 問題：沒有 Concepts 時的悲劇

```cpp
template <typename T>
T max(T a, T b) {
    return a > b ? a : b;
}

struct Foo {};
max(Foo{}, Foo{});   // 💥 一堆錯誤，大概 50 行，指向 algorithm 內部
```

編譯器的訊息是「找不到 `operator>`」，但要你自己從一堆模板展開裡挖。

## 13.2 Concepts 基本語法

`concept` 定義「型別要滿足什麼」：

```cpp
#include <concepts>

template <typename T>
concept Addable = requires(T a, T b) {
    a + b;         // 這個表達式要合法
};

template <Addable T>
T add(T a, T b) { return a + b; }

add(1, 2);               // ✅
add(std::string{}, std::string{});  // ✅
struct Foo {};
add(Foo{}, Foo{});       // ❌ 錯誤訊息：Foo 不滿足 Addable
```

錯誤訊息變成「**Foo 不滿足 Addable 因為 `a + b` 不能編譯**」——精準。

## 13.3 標準庫提供的 Concepts

`<concepts>` 提供一大堆預定義 concept：

```cpp
#include <concepts>

std::integral<T>          // T 是整數型別
std::floating_point<T>    // T 是浮點
std::signed_integral<T>
std::unsigned_integral<T>

std::same_as<T, U>        // T == U
std::derived_from<T, Base>
std::convertible_to<From, To>

std::default_initializable<T>
std::copyable<T>
std::movable<T>

std::equality_comparable<T>  // 支援 == 和 !=
std::totally_ordered<T>      // 支援 <, <=, >, >=

std::invocable<F, Args...>   // F 可用 Args... 呼叫
std::predicate<F, Args...>   // 可呼叫且回傳 bool

std::ranges::range<T>        // 有 begin/end
std::ranges::sized_range<T>  // 有 size()
```

用法：
```cpp
template <std::integral T>
T square(T x) { return x * x; }

square(5);      // ✅
square(5.5);    // ❌ double 不是 integral
```

## 13.4 四種用 concept 的語法

```cpp
// 1. 當成 typename 用
template <std::integral T>
T f(T x);

// 2. requires 子句（靈活，可組合）
template <typename T>
requires std::integral<T>
T g(T x);

// 3. 尾端 requires
template <typename T>
T h(T x) requires std::integral<T>;

// 4. Abbreviated function template（最簡潔）
std::integral auto i(std::integral auto x) { return x; }
```

第 4 種最現代，第 2 種最靈活（可組合多個條件）。

## 13.5 組合 Concepts

```cpp
template <typename T>
concept Numeric = std::integral<T> || std::floating_point<T>;

template <typename T>
concept Summable = std::integral<T> && requires(T a, T b) { a + b; };

// 在 requires 裡用 &&, ||, !
template <typename T>
requires std::integral<T> && std::movable<T>
T foo(T x);
```

## 13.6 `requires` 表達式

詳細描述型別要支援什麼：

```cpp
template <typename T>
concept Container = requires(T c) {
    c.size();                          // 要能呼叫 size()
    c.begin();                         // 要能呼叫 begin()
    c.end();

    { c.size() } -> std::convertible_to<std::size_t>;   // 回傳可轉成 size_t

    typename T::value_type;            // 要有 nested type
};

template <Container C>
void print(const C& c);
```

四種 requirement：
1. **Simple**：`c.size()` — 表達式要合法
2. **Type**：`typename T::value_type` — 要有該 type
3. **Compound**：`{ c.size() } -> std::convertible_to<std::size_t>` — 合法且回傳符合條件
4. **Nested**：`requires std::copyable<T>` — 需要另一個 requirement

## 13.7 實用範例

### 範例 1：型別安全的數學函式
```cpp
template <std::floating_point T>
T distance(T x1, T y1, T x2, T y2) {
    return std::sqrt((x2-x1)*(x2-x1) + (y2-y1)*(y2-y1));
}

distance(1.0, 2.0, 3.0, 4.0);   // ✅
distance(1, 2, 3, 4);            // ❌ int 不是 floating_point
```

### 範例 2：Overload 按型別選擇
```cpp
template <std::integral T>
void print(T x) { std::cout << "int: " << x; }

template <std::floating_point T>
void print(T x) { std::cout << "float: " << x; }

template <typename T>
requires std::same_as<T, std::string>
void print(T x) { std::cout << "string: " << x; }

print(42);       // int: 42
print(3.14);     // float: 3.14
print("hi"s);    // string: hi
```

比舊的 SFINAE / tag dispatch 乾淨多了。

### 範例 3：自訂 concept
```cpp
template <typename T>
concept Drawable = requires(T t) {
    t.draw();
};

struct Circle { void draw() { /* ... */ } };
struct Square { void draw() { /* ... */ } };
struct Point  { /* 沒 draw() */ };

void render(Drawable auto obj) { obj.draw(); }

render(Circle{});   // ✅
render(Point{});    // ❌ 不滿足 Drawable
```

這是「靜態鴨子型別」。不用繼承 base class，有對的方法就行。

## 13.8 Concepts vs Inheritance

```cpp
// 傳統 OO
class Drawable {
public:
    virtual void draw() = 0;
    virtual ~Drawable() = default;
};

class Circle : public Drawable { /* ... */ };

void render(const Drawable& d) { d.draw(); }   // 動態分派
```

VS

```cpp
// Concept
template <typename T>
concept Drawable = requires(T t) { t.draw(); };

void render(Drawable auto& d) { d.draw(); }    // 靜態分派
```

| | Inheritance | Concept |
|---|---|---|
| 型別檢查 | 編譯期（必須繼承） | 編譯期（結構匹配） |
| 分派 | 動態（vtable） | 靜態（編譯期） |
| 需要改原型別？ | ✅ 要繼承 base | ❌ 不用 |
| 異質集合（vector of various） | ✅ | 需 `std::variant` 或 type erasure |

Concept 更彈性、更快；需要「同時裝多種類別在 vector 裡」時 OO 還是有用。

## 13.9 與 Ranges 的整合

Ch14 會看到 Ranges 把 iterator 類別全用 concept 表達：

```cpp
template <std::ranges::range R>
void print(R&& r) { /* ... */ }

print(std::vector{1,2,3});      // ✅
print("hello");                  // ✅ char array 也是 range
```

## 13.10 常見陷阱

### 陷阱 1：concept 名稱和已有 type 衝突
```cpp
template <typename T>
concept Integer = std::integral<T>;   // 自訂 Integer
```
別取名和標準庫衝突（`Integer`、`Container` 都容易撞）。

### 陷阱 2：requires 裡的表達式是「能不能編譯」，不驗證行為
```cpp
template <typename T>
concept HasAdd = requires(T a) { a + a; };
```
這只檢查語法合法，不保證 `+` 有合理語意。這叫「syntactic concept」，C++ concept 目前只能檢查語法。

### 陷阱 3：忘了 `#include <concepts>`
```cpp
template <std::integral T>   // ❌ 沒 include 就沒 std::integral
```

## 13.11 自訂 concept 的步驟

1. 找出重複出現的型別要求（「這裡和那裡都要 T 支援 + 和 size()」）
2. 定一個名字
3. 寫 `concept`
4. 取代原本的 `typename`

```cpp
// 本來
template <typename T>
requires requires(T a, T b) { a + b; a - b; a * b; }
T compute(T a, T b);

// 重構
template <typename T>
concept Arithmetic = requires(T a, T b) { a + b; a - b; a * b; };

template <Arithmetic T>
T compute(T a, T b);
```

## 13.12 練習

1. 定一個 concept `Printable`：型別要能 `std::cout << t`。寫一個 `print_all(container)` 只接「元素是 Printable」的容器。
2. 用 concept + overload，寫 `double to_double(T)`：對整數直接 cast，對 string 用 `std::stod`。

## 本章重點
- Concepts = 對 template 參數的「要求」
- 帶來兩個好處：**可讀錯誤訊息** + **可控 overload**
- 標準庫 `<concepts>` 提供一大堆常用 concept
- 四種寫法，推薦 `requires` 子句或 abbreviated 函式
- 取代 SFINAE / tag dispatch 絕大多數用途
- 「靜態鴨子型別」：有方法就行，不用繼承
