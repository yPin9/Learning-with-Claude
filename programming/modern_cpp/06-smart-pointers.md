# Ch6: Smart Pointers

這章讓你永遠不再需要寫 `new` / `delete`。

## 6.1 三種 smart pointer

| 類別 | 所有權模型 | 大小 |
|---|---|---|
| `std::unique_ptr<T>` | 獨佔（不能複製，可以 move） | sizeof(T*) |
| `std::shared_ptr<T>` | 共享（引用計數） | 2 × sizeof(T*) |
| `std::weak_ptr<T>` | 非擁有 observer（配合 shared_ptr） | 2 × sizeof(T*) |

**預設用 `unique_ptr`**。需要共享才上 `shared_ptr`（通常你不需要）。

## 6.2 `std::unique_ptr`

獨佔式智慧指標。擁有一個物件，離開 scope 時自動 delete。

```cpp
#include <memory>

{
    auto p = std::make_unique<int>(42);
    std::cout << *p;       // 42
}   // 自動 delete
```

### `std::make_unique` 優於 `new`
```cpp
auto p = std::make_unique<Foo>(a, b, c);   // ✅ 推薦
std::unique_ptr<Foo> p(new Foo(a, b, c));  // 可以但多餘
```

`make_unique` 做了兩件事：
1. 型別只打一次（`Foo`）
2. 例外安全（`new` + 另一個會 throw 的函式參數混用可能 leak）

### 不能複製，只能 move
```cpp
std::unique_ptr<int> a = std::make_unique<int>(1);
std::unique_ptr<int> b = a;              // ❌ 錯：不能複製
std::unique_ptr<int> c = std::move(a);   // ✅ 轉移所有權
// a 現在是 nullptr
```

### 用法

```cpp
auto p = std::make_unique<Foo>();

p->method();        // 像 pointer 用
(*p).method();
Foo* raw = p.get(); // 拿底層 pointer（需要傳給 C API）
p.reset();          // 馬上 delete 並設 nullptr
p.reset(new Foo);   // 換一個（delete 舊的）
Foo* released = p.release();   // 放棄所有權，回傳 raw pointer（你要自己 delete）

if (p) { /* ... */ }           // 可以當 bool 用
```

### 陣列版本
```cpp
auto arr = std::make_unique<int[]>(100);
arr[0] = 42;
// 用 delete[] 釋放（自動處理）
```

但真的要陣列，通常 `std::vector` 更好。

### 自訂 deleter
包 C API 時常用：
```cpp
#include <cstdio>
#include <memory>

// 把 FILE* 包進 unique_ptr
auto fclose_deleter = [](FILE* f) { if (f) std::fclose(f); };
std::unique_ptr<FILE, decltype(fclose_deleter)> f{std::fopen("a.txt", "r"), fclose_deleter};
```

或用 function pointer：
```cpp
std::unique_ptr<FILE, int(*)(FILE*)> f{std::fopen("a.txt", "r"), std::fclose};
```

## 6.3 `std::shared_ptr`

引用計數。多個 shared_ptr 可以指同一物件，最後一個解構時 delete。

```cpp
auto p = std::make_shared<Foo>();
auto q = p;          // 引用計數 +1（= 2）
q.reset();           // -1（= 1）
// p 最後解構 → delete Foo
```

### `std::make_shared` 優於 `new`
```cpp
auto p = std::make_shared<Foo>(a, b);    // ✅
std::shared_ptr<Foo> p(new Foo(a, b));   // 可以但多一次分配
```

`make_shared` 把**物件和引用計數 block 合併一次分配**，比 `new` + `shared_ptr{...}` 少一次分配。

### 為什麼「預設別用 shared_ptr」？

- **貴**：原子計數、雙倍大小、cache 不友善
- **隱藏所有權**：誰是 owner 不明確
- **容易造成循環**（見下節）

只在**真的需要共享**時用。單一所有權永遠 `unique_ptr`。

## 6.4 循環引用問題

```cpp
struct Node {
    std::shared_ptr<Node> next;
};

auto a = std::make_shared<Node>();
auto b = std::make_shared<Node>();
a->next = b;
b->next = a;    // ❌ 循環
// a 和 b 的引用計數永遠不歸零 → leak
```

解法：有一方用 `std::weak_ptr`。

## 6.5 `std::weak_ptr`

非擁有觀察者，不影響引用計數。要用時 `.lock()` 取得 `shared_ptr`。

```cpp
struct Parent;
struct Child {
    std::weak_ptr<Parent> parent;   // 不擁有
};
struct Parent {
    std::shared_ptr<Child> child;
};

auto p = std::make_shared<Parent>();
p->child = std::make_shared<Child>();
p->child->parent = p;   // OK：weak 不計數

// 使用
if (auto parent = p->child->parent.lock()) {
    // lock() 回傳 shared_ptr，如果物件還活著
    parent->do_something();
}
```

典型場景：
- Parent-child 關係：parent 擁有 child（shared_ptr），child 反向觀察 parent（weak_ptr）
- Cache：cache 用 weak_ptr，不讓 cache 把物件「鎖死」

## 6.6 所有權語意該怎麼傳

函式簽名傳達「所有權意圖」：

```cpp
// 「你給我一個物件，我要擁有它」→ 傳 unique_ptr by value
void take(std::unique_ptr<Foo> p);

// 「我借用，不動所有權」→ 傳 raw pointer 或 reference
void observe(const Foo* p);
void observe(const Foo& p);

// 「我要參與共享所有權」→ 傳 shared_ptr by value
void share(std::shared_ptr<Foo> p);

// 「我可能保留一個 observer」→ 傳 weak_ptr
void watch(std::weak_ptr<Foo> p);
```

**原則：函式只「借用」物件時，用 raw pointer 或 reference**，不要傳 smart pointer。讓 caller 自己管所有權。

```cpp
// ❌ 過度
void process(const std::unique_ptr<Foo>& p);

// ✅ 正確
void process(const Foo& p);

// 呼叫端
auto p = std::make_unique<Foo>();
process(*p);
```

## 6.7 Smart pointer 與 polymorphism

`unique_ptr<Base>` 可以放 `Derived`：

```cpp
struct Animal { virtual ~Animal() = default; virtual void speak() = 0; };
struct Dog : Animal { void speak() override { std::cout << "woof"; } };

std::unique_ptr<Animal> p = std::make_unique<Dog>();
p->speak();
```

**Base 解構子必須 virtual**，否則只會呼叫 `Animal::~Animal()`。

## 6.8 陷阱集

### 陷阱 1：從 raw pointer 建兩個 shared_ptr
```cpp
Foo* raw = new Foo;
std::shared_ptr<Foo> a{raw};
std::shared_ptr<Foo> b{raw};   // ❌ 兩個獨立的 control block → double delete
```

正確：
```cpp
auto a = std::make_shared<Foo>();
std::shared_ptr<Foo> b = a;    // ✅ 共享 control block
```

### 陷阱 2：`shared_ptr<this>`
```cpp
struct Foo {
    std::shared_ptr<Foo> self() {
        return std::shared_ptr<Foo>(this);   // ❌ 新 control block
    }
};
```

解法：繼承 `std::enable_shared_from_this`：
```cpp
struct Foo : std::enable_shared_from_this<Foo> {
    std::shared_ptr<Foo> self() {
        return shared_from_this();   // ✅
    }
};
```

### 陷阱 3：`unique_ptr` 傳給函式卻想留著
```cpp
void f(std::unique_ptr<Foo> p);

auto p = std::make_unique<Foo>();
f(p);              // ❌ unique_ptr 不能 copy
f(std::move(p));   // ✅ 所有權轉給 f，你這邊 p 變 nullptr
```

### 陷阱 4：`new` 混 smart pointer
```cpp
f(std::shared_ptr<A>(new A), std::shared_ptr<B>(new B));
// 如果 new B 前 new A 之後 throw，new A 的記憶體 leak
// （因為 shared_ptr 建構還沒完成）
f(std::make_shared<A>(), std::make_shared<B>());   // ✅ 例外安全
```

**永遠用 `make_*`，別手動 new**。

## 6.9 哪個用哪個？決策樹

```
你需要 pointer 嗎？（不是每個物件都需要 heap 分配）
├─ 不需要 → 用 value type（struct 直接存、vector、optional...）
└─ 需要
   ├─ 單一擁有者？ → unique_ptr
   ├─ 多個擁有者？（真的需要嗎？）→ shared_ptr
   ├─ 只觀察 shared_ptr 的物件？ → weak_ptr
   └─ 借用別人擁有的？ → raw pointer 或 reference
```

## 6.10 Raw pointer 還有用嗎？

有。**當你只借用、不擁有時**，raw pointer 是最簡單表達這件事的方式。

```cpp
class Employee {
    Department* dept_;   // 借用（Department 由公司物件擁有）
public:
    Employee(Department* d) : dept_(d) {}
};
```

但 reference 通常更好（除非要能 rebind 或可為 null）。

## 6.11 練習

1. 把這段 C 風格 code 用 smart pointer 重寫：
```cpp
Node* head = new Node;
head->next = new Node;
head->next->next = new Node;
head->next->next->next = nullptr;

// ... 用 ...

delete head->next->next;
delete head->next;
delete head;
```

2. 實作簡單的 tree：parent 用 `shared_ptr` 擁有 children，children 用 `weak_ptr` 指回 parent。

## 本章重點
- 預設 `unique_ptr`，共享才 `shared_ptr`
- 永遠用 `make_unique` / `make_shared`
- 借用用 reference 或 raw pointer，不用 smart pointer
- `shared_ptr` 循環靠 `weak_ptr` 切斷
- Base class 要 polymorphic delete 時，解構子必 `virtual`
- `new` / `delete` 在現代 code 裡幾乎消失
