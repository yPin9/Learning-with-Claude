# Ch 14 — 閉包：Fn / FnMut / FnOnce

> **目標**：搞懂閉包（closure）的三個 trait——`Fn`（借 `&`）/`FnMut`（借 `&mut`）/`FnOnce`（move 消耗），編譯器怎麼決定用哪個；閉包底層是編譯器自動生的**匿名 struct + `impl Fn*`**，用 `size_of` 證明「捕獲越多越大、無捕獲 0 bytes」；對照 C++ lambda 和 C 的函式指標 + `void*`。掌握回傳閉包（`impl Fn` vs `Box<dyn Fn>`）和 move 進 thread 的常見陷阱。

> **環境**：`rustc 1.97.1`，x86-64 Linux（WSL2）。本章所有 `size_of` 輸出、錯誤訊息都在此實跑。

## 為什麼需要這個？

C 裡「傳一段行為給函式」只能用函式指標 + `void*` userdata：

```c
// C：函式指標 + void* 傳「上下文」
void for_each(int *arr, int n, void (*f)(int, void *), void *ctx);

int threshold = 10;
void count_above(int x, void *ctx) {
    if (x > *(int*)ctx) (*(int*)ctx)++;  // 醜、要手動轉型、易錯
}
for_each(data, n, count_above, &threshold);
```

`void*` 沒有型別安全，轉型隨時可能錯。而且「捕獲環境變數」要你手動打包一個 struct 塞進 `ctx`。C++ lambda（C++11）補上這一塊：`[threshold](int x){ return x > threshold; }`——`[threshold]` 是 capture list，明確列出要捕獲什麼。

Rust 的閉包更進一步：**捕獲什麼、怎麼捕獲（借用還是移動），編譯器自動推導**，而且推導出來的結果決定了這個閉包實作三個 trait（`Fn`/`FnMut`/`FnOnce`）中的哪些。這章要拆穿閉包的底層——它一點都不魔法，就是一個編譯器幫你生的匿名 struct。理解這個，「為什麼這個閉包 move 之後外面不能用」「為什麼這個閉包只能 call 一次」這些困惑就全解開了。

## 先建立直覺

閉包 = **一段程式碼 + 它捕獲的環境**。「捕獲的環境」不是憑空存在的，它得存在某個地方——存在一個編譯器自動生的匿名 struct 裡，捕獲的每個變數就是這個 struct 的一個欄位。

```
你寫的閉包：                     編譯器生成的（概念上）：
                                 struct __Closure_1 {
let n = 10;                         n: i32,          // 捕獲 n
let add = move |x| x + n;    ─▶  }
                                 impl Fn(i32) -> i32 for __Closure_1 {
                                     fn call(&self, x: i32) -> i32 {
                                         x + self.n   // n 從欄位取
                                     }
                                 }
```

呼叫 `add(5)` = 呼叫這個匿名 struct 的 `Fn::call` 方法。捕獲越多變數，struct 欄位越多、越大；一個什麼都不捕獲的閉包（`|x| x + 1`），struct 沒有欄位——**零大小（0 bytes）**。

三個 trait 對應「怎麼用捕獲的環境」：

```
Fn      ── call(&self)      ── 只讀捕獲的變數（借 &），可呼叫多次
FnMut   ── call_mut(&mut self) ── 改捕獲的變數（借 &mut），可呼叫多次
FnOnce  ── call_once(self)  ── 消耗捕獲的變數（move 出去），只能呼叫一次
```

> 對照 C++ lambda：`[n](int x){ return x+n; }` 也是編譯器生一個匿名 struct（叫 closure type），捕獲的 `n` 是成員，`operator()` 是呼叫。Rust 的 `Fn`/`FnMut`/`FnOnce` 大致對應 C++ 的 `const operator()` / `mutable operator()` / 「move-only、呼叫消耗自己」。核心機制**幾乎一模一樣**，這是你已有的直覺。

## 底層：閉包就是匿名 struct，用 size_of 證明

先把最有說服力的證據跑出來。如果閉包真的是「捕獲變數當欄位的 struct」，那捕獲越多它就越大、無捕獲就是 0 bytes：

```rust
use std::mem::size_of_val;

fn main() {
    let no_capture = || 42;                    // 捕獲 0 個
    let x: u64 = 7;
    let cap_one = move || x + 1;               // 捕獲 1 個 u64
    let y: u64 = 9;
    let z: u64 = 11;
    let cap_two = move || x + y + z;            // 捕獲 3 個 u64
    let big: [u64; 4] = [1, 2, 3, 4];
    let cap_array = move || big[0] + big[3];    // 捕獲 4 個 u64 的陣列

    println!("no_capture  = {} bytes", size_of_val(&no_capture));
    println!("cap_one     = {} bytes", size_of_val(&cap_one));
    println!("cap_two     = {} bytes", size_of_val(&cap_two));
    println!("cap_array   = {} bytes", size_of_val(&cap_array));

    // 函式指標對照
    fn plain(a: u64) -> u64 { a + 1 }
    let fp: fn(u64) -> u64 = plain;
    println!("fn pointer  = {} bytes", size_of_val(&fp));

    // 呼叫一下避免被優化掉
    println!("results: {} {} {} {}",
        no_capture(), cap_one(), cap_two(), cap_array());
}
```

實跑輸出：

```
no_capture  = 0 bytes
cap_one     = 8 bytes
cap_two     = 24 bytes
cap_array   = 32 bytes
fn pointer  = 8 bytes
results: 42 8 27 5
```

看數字：

- `no_capture` = **0 bytes**：什麼都不捕獲，匿名 struct 沒有欄位，零大小型別（ZST）。這是 Rust 的抽象是零成本的鐵證——一個無捕獲閉包在記憶體上不佔任何空間。
- `cap_one` = 8：捕獲一個 `u64`（8 bytes）。
- `cap_two` = 24：捕獲三個 `u64`（3 × 8 = 24）。
- `cap_array` = 32：捕獲 `[u64; 4]`（4 × 8 = 32）。
- `fn pointer` = 8：一個函式指標永遠 8 bytes（一個位址），**不管**它指向的函式多複雜。

**閉包 vs 函式指標的關鍵差別**：函式指標永遠 8 bytes，因為它只是「跳到哪個位址」；閉包大小取決於捕獲多少，因為它還要**帶著捕獲的資料**。這就是為什麼閉包能捕獲環境而裸函式指標不能——閉包把環境揹在自己身上。

## 三個 trait：編譯器怎麼決定用哪個

編譯器看閉包**怎麼使用**捕獲的變數，自動決定它實作哪些 trait：

- 只**讀**捕獲的變數 → `Fn`（也自動是 `FnMut` + `FnOnce`，因為能讀就能改成也接受 &mut/消耗）。
- **改**捕獲的變數 → `FnMut`（也是 `FnOnce`）。
- 把捕獲的變數**移出去**（消耗掉）→ `FnOnce`（只有這個）。

這是一個包含關係：`Fn ⊆ FnMut ⊆ FnOnce`。能當 `Fn` 用的一定能當 `FnMut`/`FnOnce`；反之不行。

```rust
fn call_fn<F: Fn()>(f: F) { f(); f(); }              // &self，可 call 多次
fn call_fnonce<F: FnOnce() -> String>(f: F) { println!("{}", f()); }

fn main() {
    // Fn：只借用（讀）捕獲的變數
    let name = String::from("rust");
    let greet = || println!("hello {}", name);   // 只讀 name -> Fn
    call_fn(greet);

    // FnMut：可變借用（改）捕獲的變數
    let mut count = 0;
    let mut inc = || { count += 1; println!("count = {}", count); };  // 改 count -> FnMut
    inc();
    inc();

    // FnOnce：把捕獲的值移出去（消耗）
    let owned = String::from("consumed");
    let take = move || owned;   // 把 owned move 出去 -> FnOnce（只能 call 一次）
    call_fnonce(take);

    // 回傳閉包
    let adder = make_adder(10);
    println!("adder(5) = {}", adder(5));
    let boxed = make_adder_boxed(100);
    println!("boxed(5) = {}", boxed(5));
}

fn make_adder(n: i32) -> impl Fn(i32) -> i32 {
    move |x| x + n
}
fn make_adder_boxed(n: i32) -> Box<dyn Fn(i32) -> i32> {
    Box::new(move |x| x + n)
}
```

實跑輸出：

```
hello rust
hello rust
count = 1
count = 2
consumed
adder(5) = 15
boxed(5) = 105
```

逐個看：

- `greet` 只讀 `name`，所以是 `Fn`，`call_fn` 能 call 它兩次（"hello rust" 印兩遍）。
- `inc` 改 `count`，所以是 `FnMut`，注意閉包本身要 `let mut`，因為呼叫它會改到它捕獲的狀態。
- `take` 把 `owned`（一個 `String`）move 出去回傳，一旦 call 過 `owned` 就沒了，所以是 `FnOnce`——只能 call 一次。

**`move` 關鍵字**：預設閉包**盡量借用**（只在需要時才移動）。加 `move` 強制它**移動**所有捕獲的變數進閉包。上面 `take` 和 `make_adder` 都用了 `move`。什麼時候需要 `move`？當閉包的生命週期會超過被捕獲變數的作用域時——最經典的是把閉包丟進 thread 或回傳閉包（下面詳談）。

## 回傳閉包：impl Fn vs Box<dyn Fn>

每個閉包都是**獨一無二的匿名型別**（就算兩個閉包長得一樣，型別也不同）。所以你不能寫 `fn f() -> ClosureType`——你根本說不出那個型別的名字。兩個解法：

```rust
// 方法一：impl Fn —— 靜態分派，零成本，但一個函式只能回傳一種閉包
fn make_adder(n: i32) -> impl Fn(i32) -> i32 {
    move |x| x + n
}

// 方法二：Box<dyn Fn> —— 動態分派，可回傳不同閉包，但要堆配置 + 間接呼叫
fn make_adder_boxed(n: i32) -> Box<dyn Fn(i32) -> i32> {
    Box::new(move |x| x + n)
}
```

- **`impl Fn`**：「回傳某個實作 `Fn` 的型別，但我不告訴你確切是哪個」。編譯器知道確切型別（靜態分派、可 inline、零成本），呼叫方只知道它是 `Fn`。限制：**一個函式的所有 return path 必須回傳同一個閉包型別**（因為 `impl Trait` 底下是單一具體型別）。
- **`Box<dyn Fn>`**：閉包裝箱成 trait object（[Ch 11](./11-trait-objects-dispatch.md)），動態分派。能在不同 `if` 分支回傳**不同**的閉包（因為都抹型別成 `dyn Fn`），代價是一次堆配置 + 呼叫走 vtable。

取捨跟 [Ch 11](./11-trait-objects-dispatch.md) 的靜態 vs 動態分派完全一致：**預設 `impl Fn`（零成本），需要在不同分支回傳不同閉包、或要存進同一個 `Vec<Box<dyn Fn>>` 時才用 `Box<dyn Fn>`**。

## 閉包當參數：怎麼收

寫一個吃閉包的高階函式，有兩種收法，跟回傳閉包是對稱的：

```rust
// 泛型 bound（靜態分派，零成本，預設就用這個）
fn twice<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 { f(f(x)) }

// 無捕獲閉包可以退化成 fn 指標
fn apply(f: fn(i32) -> i32, x: i32) -> i32 { f(x) }

fn main() {
    let no_cap = |x: i32| x + 1;
    let fp: fn(i32) -> i32 = no_cap;   // OK：無捕獲 -> 可轉 fn 指標
    println!("via fn ptr: {}", apply(fp, 10));
    println!("direct:     {}", apply(|x| x * 3, 10));

    let n = 5;
    println!("twice(+n):  {}", twice(|x| x + n, 1));  // 捕獲了 n，仍是 Fn
}
```

實跑輸出：

```
via fn ptr: 11
direct:     30
twice(+n):  11
```

三種收閉包的簽名，對應三種分派：

| 參數型別 | 分派 | 能收什麼 | 成本 |
|---|---|---|---|
| `f: F where F: Fn(...)`（泛型 bound） | 靜態，單型化，可 inline | 任何 `Fn`（含有捕獲的） | 零 |
| `f: &dyn Fn(...)` | 動態，走 vtable | 任何 `Fn`，異質 | 一次間接 call |
| `f: fn(...)`（函式指標） | 間接 call | **只有無捕獲**閉包或裸函式 | 一次間接 call，無法帶環境 |

**預設用泛型 bound**（`F: Fn`），零成本可 inline。需要異質（把不同閉包塞同一個 `Vec`）→ `Box<dyn Fn>` / `&dyn Fn`。跟 C 互通、對方要的是裸函式指標（`fn`）→ 只能傳無捕獲閉包，要帶環境得走 C 的 `void*` userdata 通道（[Ch 19](./19-ffi.md)）。

`apply(fp, 10)` 那行證明無捕獲閉包 `no_cap` 能明確轉成 `fn(i32) -> i32`。有捕獲的閉包不行——你把上面 `twice` 的 `|x| x + n` 硬塞給 `apply` 會編不過，因為它揹著 `n`，塞不進一個純位址的函式指標。

## 對照 C / C++

| 面向 | C 函式指標 + void* | C++ lambda | Rust 閉包 |
|---|---|---|---|
| 捕獲環境 | 手動打包進 `void*` | capture list `[x, &y]` | 編譯器自動推導 |
| 型別安全 | 無（`void*` 轉型） | 有 | 有 |
| 底層 | 函式指標 + 資料指標（分開） | 匿名 struct + `operator()` | 匿名 struct + `impl Fn*` |
| 無捕獲時大小 | 8（fn ptr）+ 8（ctx）| 可 1 byte（空 struct）或退化成 fn ptr | **0 bytes** |
| 借用 vs 移動 | 全手動 | `[x]`(copy) / `[&x]`(ref) / `[=]`/`[&]` | `Fn`/`FnMut`/`FnOnce` 自動推導 + `move` |
| 呼叫次數限制 | 無 | 無（除非 move-only 捕獲） | `FnOnce` 只能一次，編譯期強制 |

Rust 和 C++ lambda 的底層機制最像（都是匿名 struct + 呼叫運算子）。最大的差別是 Rust 用 `Fn`/`FnMut`/`FnOnce` 三個 trait 把「這個閉包怎麼碰捕獲的東西」**編碼進型別系統**，讓「只能 call 一次」這種約束能在編譯期強制。C++ 沒有這層——一個 move 了捕獲值的 lambda 你 call 第二次是 UB 或邏輯錯，編譯器不一定擋。

## 陷阱：move 進 thread 之後外部不能用

最常見的閉包踩雷：把值 `move` 進閉包（或 thread）後，外面再用那個值——所有權已經轉移了：

```rust
fn main() {
    let data = vec![1, 2, 3];
    let closure = move || println!("moved: {:?}", data);
    closure();
    // move 之後 data 的所有權在閉包裡，外面不能再用：
    println!("outer: {:?}", data);   // E0382 use after move
}
```

真實錯誤：

```
error[E0382]: borrow of moved value: `data`
 --> ch14trap.rs:6:29
  |
2 |     let data = vec![1, 2, 3];
  |         ---- move occurs because `data` has type `Vec<i32>`, which does not implement the `Copy` trait
3 |     let closure = move || println!("moved: {:?}", data);
  |                   -------                         ---- variable moved due to use in closure
  |                   |
  |                   value moved into closure here
...
6 |     println!("outer: {:?}", data);   // E0382 use after move
  |                             ^^^^ value borrowed here after move
  |
help: consider cloning the value before moving it into the closure
```

`rustc` 說得清清楚楚：`data`（`Vec`，非 `Copy`）在第 3 行被 move 進閉包，第 6 行又想借用——不行。這在 thread 場景特別常見：

```rust
// 把 data move 進 thread 是必須的（thread 生命週期可能超過 main 的區域變數），
// 但 move 之後 main 裡的 data 就不能用了
let handle = std::thread::spawn(move || {
    // 這裡能用 data
});
// 這裡不能再用 data
```

為什麼 thread 一定要 `move`？因為 thread 可能比建立它的作用域活得久，如果閉包只是**借用** `data`，`data` 在作用域結束就沒了，thread 卻還在跑——就是懸空引用（dangling reference），Rust 靠 `move` 強制轉移所有權來禁止這件事（[Ch 23](./23-threads-send-sync.md) 會深入）。解法：需要保留就先 `.clone()` 一份給閉包（`rustc` 的 help 也這樣建議）。

## 踩雷集錦

1. **忘記 `FnOnce` 只能呼叫一次**：一個 move 了非 `Copy` 值出去的閉包是 `FnOnce`，call 第二次編不過。如果你需要 call 多次，別把值移出去——改成借用（讓它是 `Fn`/`FnMut`），或 clone。

2. **`FnMut` 閉包忘記 `let mut`**：`let inc = || count += 1;` 然後 `inc()` 會編不過，因為呼叫 `FnMut` 需要 `&mut` 那個閉包，閉包 binding 本身要 `mut`。改成 `let mut inc = ...`。

3. **想回傳閉包卻寫不出型別**：閉包型別無法命名。用 `impl Fn(...)  -> ...`（單一閉包）或 `Box<dyn Fn(...) -> ...>`（多種閉包 / 存進集合）。

4. **在不同 `if` 分支回傳不同閉包還用 `impl Fn`**：`impl Trait` 底下是**單一**具體型別，兩個分支是兩個不同閉包型別，編不過。這種要用 `Box<dyn Fn>`。

5. **捕獲 `&mut` 撞借用檢查**：閉包捕獲一個 `&mut x`，在閉包還活著時你又想碰 `x`——借用衝突。閉包持有 `&mut` 期間，`x` 被獨佔借走了（[Ch 3](./03-borrowing-references.md) 的別名規則）。縮小閉包的作用域或用 scope 讓借用早點結束。

6. **以為 `move` 就是「複製」**：`move` 是**轉移所有權**，不是複製。對非 `Copy` 型別，`move` 進閉包後外面就不能用了（上面 E0382）。對 `Copy` 型別（`i32` 等），`move` 實際上是複製，外面還能用——因為 `Copy` 型別 move = 複製。

## 進階：再往深一層

**閉包的 desugar 你可以親眼看**：閉包 `|x| x + n` 概念上等於一個 struct + `impl Fn`。標準庫沒有讓你手寫 `impl Fn`（`Fn`/`FnMut`/`FnOnce` 的手動實作是 nightly-only unstable feature），但你可以手刻一個「行為像閉包」的 struct 來理解機制：

```rust
// 進階：手刻一個「捕獲 n」的 callable struct，模擬閉包的本質
struct Adder { n: i32 }        // 捕獲的變數當欄位
impl Adder {
    fn call(&self, x: i32) -> i32 { x + self.n }  // 對應 Fn::call
}
fn main() {
    let add5 = Adder { n: 5 };  // 相當於 let add5 = |x| x + 5;（但 n 明確存欄位）
    println!("{}", add5.call(3));  // 相當於 add5(3)
}
```

這個手刻版就是編譯器對 `move |x| x + n` 做的事——把 `n` 存欄位、把函式體變成一個吃 `&self` 的方法。真正的閉包多了自動推導 trait、自動決定借用/移動，但**骨架就是這個**。

**`fn` 指標和閉包的關係**：無捕獲的閉包可以自動轉成 `fn` 指標（`let f: fn(u64) -> u64 = |x| x + 1;` 合法）。有捕獲的不行——`fn` 指標裝不下環境。這也解釋了為什麼 FFI（[Ch 19](./19-ffi.md)）給 C 的 callback 只能是無捕獲閉包或裸函式（C 的函式指標揹不了 Rust 閉包的環境，要靠額外的 `void*` userdata 通道傳）。

**閉包捕獲的粒度**（Rust 2021+）：Rust 2021 edition 起，閉包**只捕獲用到的欄位**而非整個 struct（disjoint closure captures）。`|| obj.field` 只借 `obj.field`，不借整個 `obj`，減少借用衝突。這是 edition 差異，2018 及更早會捕獲整個 `obj`。

## 動手練習

1. 改上面 `size_of` 程式：加一個捕獲 `String`（`size_of::<String> = 24`）的閉包，驗證它至少 24 bytes。
2. 寫一個 `FnMut` 閉包當「計數器工廠」：`fn counter() -> impl FnMut() -> u32`，每次呼叫回傳遞增的數字（提示：`move` 一個 `count` 進去，`move |...| { count += 1; count }`）。
3. 故意寫一個 `FnOnce` 閉包（move 出一個 `String`）然後 call 兩次，看它怎麼罵你。
4. 把一個 `Vec` move 進 `thread::spawn` 的閉包，然後在 `main` 裡再用那個 `Vec`，親眼看 E0382，讀懂 `rustc` 建議的 clone 修法。

## 本章重點整理

- 閉包 = 一段程式碼 + 捕獲的環境，底層是**編譯器自動生的匿名 struct（捕獲變數當欄位）+ impl Fn/FnMut/FnOnce**。
- `size_of`：無捕獲 = **0 bytes**（ZST），捕獲越多越大；函式指標永遠 8 bytes（揹不了環境）。
- `Fn`（讀，借 `&`，多次）⊆ `FnMut`（改，借 `&mut`，多次）⊆ `FnOnce`（消耗，move，一次）；編譯器看使用方式自動推導。
- `move` 強制轉移所有權（不是複製）；thread 一定要 `move`（防懸空引用）；move 非 `Copy` 值後外部不能用（E0382）。
- 回傳閉包：`impl Fn`（單一閉包、零成本）vs `Box<dyn Fn>`（多種閉包、動態分派），取捨同 Ch 11 靜態 vs 動態。

## 自我檢核

- [ ] 不看筆記，能不能解釋「為什麼無捕獲閉包是 0 bytes、捕獲一個 u64 是 8 bytes」，並連到「閉包是匿名 struct」？
- [ ] 給你一個閉包，你能判斷它是 `Fn`/`FnMut`/`FnOnce` 中的哪個嗎？依據是什麼？
- [ ] 面試問「閉包和函式指標差在哪」，你能講出「閉包揹環境所以大小可變、函式指標永遠一個位址」嗎？
- [ ] 為什麼 `thread::spawn` 的閉包幾乎總是要 `move`？不 move 會怎樣？
- [ ] `impl Fn` 和 `Box<dyn Fn>` 各在什麼情況該用？跟 Ch 11 的哪個取捨是同一件事？

## 延伸閱讀

### 官方文件

- **[The Rust Book — Ch 13.1: Closures](https://doc.rust-lang.org/book/ch13-01-closures.html)**
  - **讀哪裡**：整節，特別是 "Capturing References or Moving Ownership" 和 "Moving Captured Values Out of Closures and the Fn Traits" 兩段。
  - **和本章的關聯**：本章 `Fn`/`FnMut`/`FnOnce` 推導的官方版；它的 workout tracker 例子把三個 trait 的差異講得很具體。

- **[Rust Reference — Closure types](https://doc.rust-lang.org/reference/types/closure.html)**
  - **讀哪裡**：整節，尤其 "Capture modes" 和 "Call traits and coercions"。
  - **和本章的關聯**：本章「閉包是匿名 struct」的規範來源；這裡精確定義捕獲模式的推導規則和無捕獲閉包轉 `fn` 指標的條件。

### 部落格 / 技術文章

- **[Finding Closure in Rust](https://huonw.github.io/blog/2015/05/finding-closure-in-rust/)** — Huon Wilson
  - **這篇說什麼**：從底層拆解閉包 desugar 成 struct + trait 的全過程，跟本章的手刻 `Adder` 是同一思路但更完整。
  - **前提知識**：懂本章的三個 trait；這篇會帶你看更接近編譯器實際生成的形式。
  - **為什麼值得讀**：作者是前 Rust 核心團隊成員，這篇是「閉包底層到底是什麼」講得最透的一篇經典。雖然寫於 Rust 1.0 前後，閉包的核心 desugar 機制至今未變。

### 書籍

- **《Programming Rust, 2nd ed.》** — Blandy, Orendorff, Tindall（O'Reilly, 2021）
  - **這本書的定位**：系統向、對 C++ 使用者友善。
  - **讀哪幾章**：Chapter 14（Closures）逐一對照本章的 `Fn`/`FnMut`/`FnOnce`、閉包佈局、回傳閉包，還把閉包和 C++ lambda 的記憶體佈局並排比較，正好接本章的 C/C++ 對照。

閉包是型別系統這個 Part 的收尾。你現在手上有 enum、pattern matching、trait、泛型、trait object、核心 trait、錯誤處理、閉包——足夠拼出一個完整的泛型資料結構了。下一個練習就要你把這些整合起來：實作一個泛型容器並為它做 `Iterator` + `IntoIterator`，再串上 adapter 驗證。

→ [練習 B：泛型資料結構與自訂 trait](./practice-b-generic-iterator.md)
