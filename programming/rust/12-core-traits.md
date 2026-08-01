# Ch 12 — 核心 trait：Deref / Drop / Copy / Clone / From / Iterator

> **目標**：把標準庫裡最該懂的幾個 trait 一次講透——`Copy`/`Clone`（淺 vs 顯式深，對照 C++ rule of five）、`Drop`（解構子與 drop 順序，接續 [Ch 11](./11-trait-objects-dispatch.md) vtable 裡的 drop_in_place）、`Deref`/`DerefMut`（deref coercion 怎麼發生）、`From`/`Into`（接 [Ch 13](./13-error-handling.md) 的 `?`）、`Iterator`（`for` 迴圈的真面目）。

> **環境**：`rustc 1.97.1`，x86-64 Linux（WSL2）。本章所有輸出都在此實跑。

## 為什麼需要這個？

C++ 裡有一組「特殊成員函式」你天天在打交道：copy constructor、copy assignment、move constructor、destructor。編譯器會偷偷幫你生，生錯了就 double free、就 UAF。Rule of three / five 就是為了讓你記得「這五個要嘛全寫、要嘛全不寫」。

Rust 把這些「特殊行為」全部拆成**明確的 trait**：

| C++ 特殊成員 | Rust 對應 trait |
|---|---|
| copy constructor（值語意複製） | `Copy`（bitwise）/ `Clone`（顯式深複製） |
| destructor `~T()` | `Drop` |
| move constructor | 不需要——Rust 的 move 是 bitwise、無法自訂 |
| `operator*` / `operator->` | `Deref` / `DerefMut` |
| 隱式轉型建構子 | `From` / `Into` |

差別在**顯性 vs 隱性**。C++ 的複製、轉型很多是隱式發生的（一個不小心就深拷貝整個 `vector`）；Rust 逼你把每一個都寫出來或 `#[derive]` 出來，沒有隱式深拷貝這回事。這章就是把這組 trait 一個個拆開，看它們各自管什麼、底層怎麼運作。

## Copy vs Clone：淺與顯式深

先建立直覺。想像賦值 `let b = a;` 發生什麼：

```
Copy（實作了 Copy trait 的型別，如 i32、Point{x,y}）：
   a ──[bitwise 複製 N bytes]──▶ b
   a 之後還能用（複製，不是移動）

Clone 但非 Copy（如 Vec、String）：
   a.clone() ──[跟著指標把 heap 資料也複製一份]──▶ b
   要顯式呼叫 .clone()，不會自動發生
```

`Copy` = 「這個型別可以用純 memcpy 複製，複製後兩份互不相干」。只有**全部欄位都是 `Copy`、且沒有 `Drop`** 的型別才能是 `Copy`（因為 `Copy` 和 `Drop` 互斥——會 double free 的東西不能無腦 memcpy）。

`Clone` = 「複製這個型別要跑自訂邏輯」，可能牽涉 heap 分配。`Clone` 是 `Copy` 的 supertrait：所有 `Copy` 型別都是 `Clone`，但反之不然。

```rust
#[derive(Clone, Copy, Debug)]
struct Point { x: i32, y: i32 }

#[derive(Clone, Debug)]   // 注意：只有 Clone，沒有 Copy
struct Buffer { data: Vec<u8> }

fn main() {
    // Copy：bitwise，原值還能用
    let p1 = Point { x: 1, y: 2 };
    let p2 = p1;            // 複製，不是移動
    println!("p1 = {:?}, p2 = {:?}", p1, p2); // 兩個都能用

    // Clone（非 Copy）：顯式、深
    let b1 = Buffer { data: vec![1, 2, 3] };
    let b2 = b1.clone();    // 深複製 heap 上的 buffer
    println!("b1 = {:?}, b2 = {:?}", b1, b2);
}
```

實跑輸出：

```
p1 = Point { x: 1, y: 2 }, p2 = Point { x: 1, y: 2 }
b1 = Buffer { data: [1, 2, 3] }, b2 = Buffer { data: [1, 2, 3] }
```

`Point` 是 `Copy`，`let p2 = p1` 後 `p1` 依然能用。`Buffer` 含 `Vec`（管 heap），不能 `Copy`——如果它能無腦 memcpy，`b1` 和 `b2` 會指向同一塊 heap，drop 兩次就 double free。這正是 C++ 裡「shallow copy 的預設 copy constructor 會炸」的問題，Rust 從型別系統層面禁掉：`Vec` 不是 `Copy`，所以含 `Vec` 的東西也不能 `Copy`，你只能顯式 `.clone()`（深）或 move（轉移所有權）。

> 對照 C++：`Point` 相當於一個沒有自訂 copy ctor 的 POD，複製就是 memcpy。`Buffer` 相當於一個 `std::vector` 包裝——C++ 的 copy ctor 會**隱式**深拷貝（一不小心就複製整個 vector），Rust 逼你寫出 `.clone()`，讓「這裡有一次深拷貝」在程式碼裡看得見。

## Drop：解構子與 drop 順序

`Drop` 就是 C++ 的解構子 `~T()`。物件離開作用域時，Rust 自動呼叫它的 `drop`。這也是 [Ch 11](./11-trait-objects-dispatch.md) vtable 裡 `drop_in_place` 指向的東西——對 trait object 而言，正確的解構函式要靠 vtable 才找得到。

關鍵是**順序**。有兩條規則，很多人搞混：

- **同一作用域的區域變數**：**反宣告順序**（後宣告的先 drop，像 stack 後進先出）。
- **struct 的欄位**：**宣告順序**（第一個欄位先 drop）。

跑出來看：

```rust
use std::mem;

struct Noisy(&'static str);
impl Drop for Noisy {
    fn drop(&mut self) { println!("dropping {}", self.0); }
}

struct Pair { first: Noisy, second: Noisy }
impl Drop for Pair {
    fn drop(&mut self) { println!("dropping Pair"); }
}

fn main() {
    println!("-- locals: reverse order --");
    let _a = Noisy("a");
    let _b = Noisy("b");
    let _c = Noisy("c");

    println!("-- struct fields: declaration order --");
    let _p = Pair { first: Noisy("first"), second: Noisy("second") };

    println!("-- mem::drop forces early drop --");
    let d = Noisy("d");
    mem::drop(d);
    println!("after mem::drop(d)");
}
```

實跑輸出：

```
-- locals: reverse order --
-- struct fields: declaration order --
-- mem::drop forces early drop --
dropping d
after mem::drop(d)
dropping Pair
dropping first
dropping second
dropping c
dropping b
dropping a
```

逐行對照：

- `mem::drop(d)` 立刻 drop `d`（在 "after mem::drop(d)" 之前印出 "dropping d"）。
- `main` 結束時，變數反序 drop：先 `_p`（Pair），再 `_c`、`_b`、`_a`。
- `Pair` 自己的 `Drop::drop` **先於**它的欄位跑（"dropping Pair" 在 "dropping first" 之前），欄位再照宣告順序 drop（first 先於 second）。這是規則：**先跑型別自己的 `drop`，再遞迴 drop 各欄位**。

**不能手動呼叫 `.drop()`**。想提早釋放要用 `std::mem::drop`（一個吃走所有權的自由函式）。手動叫 `x.drop()` 會 double drop（你叫一次，作用域結束又叫一次），所以 Rust 直接禁止：

```rust
struct Noisy(&'static str);
impl Drop for Noisy {
    fn drop(&mut self) { println!("dropping {}", self.0); }
}
fn main() {
    let a = Noisy("a");
    let b = Noisy("b");
    a.drop(); // 手動呼叫 -> E0040
    println!("end");
}
```

真實錯誤：

```
error[E0040]: explicit use of destructor method
 --> ch12drop.rs:8:7
  |
8 |     a.drop(); // 手動呼叫 -> E0040
  |       ^^^^ explicit destructor calls not allowed
  |
help: consider using `drop` function
  |
8 -     a.drop(); // 手動呼叫 -> E0040
8 +     drop(a); // 手動呼叫 -> E0040
  |
```

`rustc` 直接告訴你「用 `drop(a)` 這個函式」。`drop(a)` 吃走 `a` 的所有權，函式體是空的，`a` 在函式結束時自然 drop 一次——就這麼簡單，沒有 double drop。

> 對照 C++ RAII / rule of five：C++ 你要記得「有 raw pointer 成員就要寫解構子，寫了解構子就要處理 copy/move」。Rust 把這拆乾淨：`Drop` 管釋放，`Clone` 管複製，move 是語言內建（bitwise、不可自訂）。而且 `Drop` 和 `Copy` 互斥，從型別系統上就不可能出現「memcpy 一個有解構子的東西然後 drop 兩次」的 C++ 經典 bug。

## Deref / DerefMut 與 deref coercion

`Deref` 讓你的型別能用 `*` 解引用，也讓 `.` 方法呼叫能「穿透」到內層型別。最重要的後果是 **deref coercion（解引用強制轉換）**：`&String` 自動變 `&str`、`&Vec<T>` 自動變 `&[T]`——你天天在用，但可能沒意識到它是 `Deref` 在背後運作。

先看它發生：

```rust
use std::ops::Deref;

struct MyBox<T>(T);
impl<T> MyBox<T> {
    fn new(x: T) -> MyBox<T> { MyBox(x) }
}
impl<T> Deref for MyBox<T> {
    type Target = T;
    fn deref(&self) -> &T { &self.0 }
}

fn takes_str(s: &str) {
    println!("got &str of len {}", s.len());
}
fn takes_slice(s: &[i32]) {
    println!("got &[i32] of len {}", s.len());
}

fn main() {
    // deref coercion: &String -> &str
    let s: String = String::from("hello");
    takes_str(&s);

    // &Vec<i32> -> &[i32]
    let v: Vec<i32> = vec![1, 2, 3];
    takes_slice(&v);

    // 自訂 MyBox<String> -> &String -> &str（兩段 coercion 串起來）
    let b = MyBox::new(String::from("world"));
    takes_str(&b);

    // 這些引用其實是不同寬度的指標
    println!("size_of::<&str>()   = {}", std::mem::size_of::<&str>());
    println!("size_of::<&[i32]>() = {}", std::mem::size_of::<&[i32]>());
    println!("size_of::<&i32>()   = {}", std::mem::size_of::<&i32>());
}
```

實跑輸出：

```
got &str of len 5
got &[i32] of len 3
got &str of len 5
size_of::<&str>()   = 16
size_of::<&[i32]>() = 16
size_of::<&i32>()   = 8
```

`takes_str(&s)` 傳的是 `&String`，函式要 `&str`——編譯器看到 `String: Deref<Target = str>`，自動插一個 `.deref()` 把 `&String` 變 `&str`。這件事在**編譯期**完成，零執行期成本，只是插了一次呼叫。`MyBox<String>` 那行更精彩：`&MyBox<String>` → `&String`（我們的 `Deref`）→ `&str`（`String` 的 `Deref`），編譯器連續套兩層 coercion。

> 橫向連結：`&str` 和 `&[i32]` 都是 16 bytes 胖指標（ptr + len），這跟 [Ch 6](./06-slices-str-string.md) 講的 slice 佈局、[Ch 11](./11-trait-objects-dispatch.md) 講的 trait object 胖指標是同一族概念。`&i32` 是 8 bytes 瘦指標。deref coercion 從 `&String` 到 `&str` 不只是「換個型別」，是從瘦指標（`&String` 本身指向 String 結構）走到胖指標（`&str` = ptr+len）。

**誤用 Deref 做繼承是反模式。** 有人想模擬 C++ 繼承：定義 `struct Dog { base: Animal }` 然後 `impl Deref<Target = Animal> for Dog`，這樣 `dog.animal_method()` 就能「繼承」。**別這樣做。** `Deref` 的語意是「這個型別*就是*一個智慧指標，指向 Target」，不是「這個型別*繼承自* Target」。濫用它會讓方法解析變得詭異、讓讀者困惑（`Dog` 看起來像指標？），而且遇到 `Target` 的方法和 `Dog` 自己的方法同名時行為難以預期。要共用行為用 trait，要共用資料用組合（composition）+ 明確的委派方法。Rust 官方文件明確把「用 Deref 模擬繼承」列為反模式。

## From / Into：型別轉換的基礎（接 `?`）

`From<T>` 定義「怎麼從 `T` 造出 `Self`」。`Into<T>` 是它的鏡像，而且**你只要實作 `From`，`Into` 自動就有**（標準庫有一條 blanket impl：`impl<T, U: From<T>> Into<U> for T`）。所以慣例是**只實作 `From`**。

```rust
fn main() {
    // From：明確從一個型別造另一個
    let s: String = String::from("abc");
    let n: i64 = i64::from(42i32);   // i32 -> i64（無損擴展）

    // Into：反向寫法，同一件事
    let m: i64 = 7i32.into();        // 型別靠上下文推導

    println!("s = {}, n = {}, m = {}", s, n, m);
}
```

實跑輸出：

```
s = abc, n = 42, m = 7
```

為什麼這章要講 `From`？因為它是 **`?` 運算子做錯誤轉換的基礎**（[Ch 13](./13-error-handling.md) 主題）。當你寫 `let x = might_fail()?;`，如果 `might_fail` 的錯誤型別是 `E1`、但外層函式回傳 `Result<_, E2>`，`?` 會自動呼叫 `E2::from(e1)` 把錯誤轉過去——只要你實作了 `impl From<E1> for E2`。這是 Rust 錯誤處理能優雅串起異質錯誤的關鍵機制，下一章會用到。

順帶一提幾個相關的比較/雜湊 trait，你會常 `#[derive]` 它們：`PartialEq`/`Eq`（相等）、`PartialOrd`/`Ord`（排序）、`Hash`（當 `HashMap` 的 key）。`Partial` 版本存在是因為浮點數有 `NaN`——`NaN != NaN`，所以 `f64` 只有 `PartialEq`/`PartialOrd`，沒有 `Eq`/`Ord`。想把型別當 `HashMap` key 就要 `#[derive(PartialEq, Eq, Hash)]`。

## Iterator：`next()` 就是全部

`Iterator` 是 Rust 最優雅的 trait 之一。它的核心只有一個方法：

```rust
trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
    // ... map/filter/sum 等幾十個方法都有預設實作，全建立在 next 上
}
```

`next()` 回傳 `Some(x)` 表示還有下一個，`None` 表示到底了。`map`、`filter`、`sum`、`collect`……全部是預設方法，全部只靠反覆呼叫 `next()`。你自己實作 iterator 只需要寫 `next()`，其他免費。

先建立直覺：**`for` 迴圈是語法糖**。它 desugar 成「呼叫 `IntoIterator::into_iter` 拿到 iterator，然後 `loop` 反覆 `next()` 直到 `None`」。我們把糖和手動版並排跑，證明它們一樣：

```rust
struct Counter { count: u32, max: u32 }
impl Iterator for Counter {
    type Item = u32;
    fn next(&mut self) -> Option<u32> {
        if self.count < self.max {
            self.count += 1;
            Some(self.count)
        } else {
            None
        }
    }
}

fn main() {
    // for 迴圈 = IntoIterator + loop + next 的語法糖
    println!("-- for sugar --");
    let v = vec![10, 20, 30];
    for x in &v {
        print!("{} ", x);
    }
    println!();

    println!("-- manual desugar --");
    let mut it = (&v).into_iter();
    loop {
        match it.next() {
            Some(x) => print!("{} ", x),
            None => break,
        }
    }
    println!();

    // 自訂 iterator + 串接 adapter
    println!("-- adapters (lazy) --");
    let c = Counter { count: 0, max: 5 };
    let sum: u32 = c.map(|x| x * 2).filter(|x| x % 3 == 0).sum();
    println!("sum = {}", sum);

    // 證明惰性：adapter 建好時什麼都不做，直到被消耗
    println!("-- laziness --");
    let adapter = Counter { count: 0, max: 3 }.map(|x| { println!("mapping {}", x); x });
    println!("adapter built, nothing printed yet");
    let total: u32 = adapter.sum();
    println!("total = {}", total);
}
```

實跑輸出：

```
-- for sugar --
10 20 30 
-- manual desugar --
10 20 30 
-- adapters (lazy) --
sum = 6
-- laziness --
adapter built, nothing printed yet
mapping 1
mapping 2
mapping 3
total = 6
```

三個重點：

1. **`for x in &v` 和手動 `(&v).into_iter()` + `loop { next() }` 輸出完全一樣**——因為前者就是後者的語法糖。`&v` 用的是 `impl IntoIterator for &Vec<T>`（產出 `&T`）；`v` 直接用會是 `impl IntoIterator for Vec<T>`（產出 `T`，消耗 vector）。
2. **adapter 惰性（lazy）**：看 "laziness" 那段——`adapter` 建好後，"adapter built" 先印出來，`mapping 1/2/3` 一個都沒印。直到 `.sum()` 這個**消費者（consumer）**去拉資料，`map` 的閉包才逐個跑。`sum` 那行的計算：Counter 產 1..5，`map` 乘 2 得 2,4,6,8,10，`filter` 留 3 的倍數只剩 6，總和 6。
3. **零成本**：這串 `map().filter().sum()` 編譯後跟你手寫一個 `for` 迴圈裡 `if x*2 % 3 == 0 { sum += x*2 }` 幾乎生一樣的機器碼，沒有中間 collection、沒有額外配置。

> 對照 C++ ranges（C++20）：Rust 的 iterator adapter 和 C++20 的 `std::ranges` views 是同一哲學——惰性、可組合、零額外配置。Rust 這套從 1.0（2015）就有，C++ 到 C++20（2020）才用 ranges 補上。差別是 Rust 的 iterator 是 trait 方法鏈（`.map().filter()`），C++ ranges 用 `|` 管道運算子（`v | views::transform | views::filter`）。底層都是「每個 adapter 包住上游、`next` 時逐個拉」。

## 對比與取捨：Copy vs Clone vs Move

| 操作 | 語意 | 原值之後 | 成本 | C++ 對應 |
|---|---|---|---|---|
| `let b = a;`（Copy 型別） | bitwise 複製 | 還能用 | memcpy N bytes | copy ctor (POD) |
| `let b = a;`（非 Copy 型別） | move，轉移所有權 | **不能用**（moved） | bitwise（不複製 heap） | move ctor |
| `let b = a.clone();` | 顯式深複製 | 還能用 | 可能配置 heap | copy ctor (deep) |

Rust 的 move 是 bitwise 且**無法自訂**——這跟 C++ 的 move ctor 很不一樣。C++ move 可以有自訂邏輯（例如把來源指標設 null）；Rust 的 move 就是把 bytes 搬過去、來源標記為「已 moved 不可用」，編譯期靜態追蹤，執行期什麼都不做。所以 Rust 沒有 move ctor 這個 trait——不需要。

## 踩雷集錦

1. **以為 `let b = a` 對所有型別都是複製**：只有 `Copy` 型別是複製。非 `Copy`（`String`/`Vec`/自訂 struct 預設）是 **move**，`a` 之後不能用（[Ch 2](./02-ownership-move.md) 的所有權）。想要兩份都能用要嘛型別是 `Copy`、要嘛顯式 `.clone()`。

2. **手動呼叫 `.drop()`**：E0040。用 `std::mem::drop(x)`。記住 `drop` 是個吃所有權的自由函式，不是方法。

3. **搞錯 drop 順序**：區域變數**反序**、struct 欄位**正序**、型別自己的 `Drop::drop` 在欄位之前。如果你的 `Drop` 邏輯依賴某個欄位還活著，注意欄位是在你的 `drop` 跑完之後才 drop 的，所以在 `drop` 裡存取欄位是安全的。

4. **用 `Deref` 模擬繼承**：反模式。`Deref` 是給智慧指標用的（`Box`/`Rc`/`Arc` 都 impl 它），不是給你當 base class。共用行為用 trait，共用資料用組合。

5. **忘了 iterator 是惰性的**：`v.iter().map(|x| expensive(x));` 這行**什麼都沒做**——沒有消費者（`collect`/`sum`/`for`/`count`……）就不會執行。`rustc` 通常會警告 `unused #[must_use]`，但邏輯上要記得：adapter 只是「描述要做什麼」，消費者才「真的做」。

6. **`f64` 不是 `Ord`**：因為 `NaN`。想排序浮點 `Vec` 用 `sort_by(|a, b| a.partial_cmp(b).unwrap())`，或用 `total_cmp`（Rust 1.62+）。直接 `.sort()` 一個 `Vec<f64>` 編不過。

## 進階：再往深一層

**`IntoIterator` 的三種身分**：一個型別通常實作三次 `IntoIterator`——`for x in v`（消耗，產 `T`）、`for x in &v`（借用，產 `&T`）、`for x in &mut v`（可變借用，產 `&mut T`）。慣例上還會提供 `.iter()`（= `(&v).into_iter()`）和 `.iter_mut()`。下一個練習（練習 B）就要你把這幾個都實作出來。

**`Drop` 與 panic**：如果一個型別在 `drop` 裡 panic，而此時正在因為另一個 panic 而 unwind，會直接 abort（double panic）。所以 `Drop::drop` 裡別做可能 panic 的事。這跟 C++ 「解構子不要拋例外」的鐵律同源。

**`Cow`（Clone on Write）**：`std::borrow::Cow<T>` 是「借用或擁有」的智慧列舉，用 `Deref` 讓你透明地讀，只在要改時才 clone。是「Copy vs Clone」取捨的進階工具，處理「大部分時候不用複製、偶爾才要」的場景。

```rust
// 進階：手動 desugar 一次 map，看惰性 adapter 的內部結構
struct MyMap<I, F> { iter: I, f: F }
impl<I: Iterator, F: FnMut(I::Item) -> u32> Iterator for MyMap<I, F> {
    type Item = u32;
    fn next(&mut self) -> Option<u32> {
        // 每次 next 才拉上游一個、套一次 f —— 這就是「惰性」的本質
        self.iter.next().map(|x| (self.f)(x))
    }
}
// MyMap 包住上游 iter，next 時才逐個轉換；標準庫的 Map 就是這樣（外加更多優化）
```

## 動手練習

1. 給 `Buffer`（含 `Vec`）加 `#[derive(Copy)]`，看它怎麼罵你（提示：`Vec` 不是 `Copy`，會 E0204）。
2. 在 `Pair` 的 `Drop::drop` 裡印出 `self.first.0`，確認欄位在你的 `drop` 跑時還活著（欄位在型別 `drop` 之後才 drop）。
3. 把 `Counter` 的 `map().filter().sum()` 改成 `for` 迴圈手寫，確認結果一樣，體會 adapter 只是把迴圈拆成鏈。
4. 給 `MyBox` 加 `impl DerefMut`，然後試著 `*mybox += 1`，看可變 deref coercion。

## 本章重點整理

- `Copy` = bitwise 複製、複製後原值可用、與 `Drop` 互斥；`Clone` = 顯式（可能深）複製。含 heap 資源（`Vec`/`String`）的型別不能 `Copy`。
- `Drop` = 解構子。順序：區域變數反序、struct 欄位正序、型別自己的 `drop` 先於欄位。不能手動叫 `.drop()`（E0040），用 `mem::drop`。
- `Deref` 驅動 deref coercion：`&String`→`&str`、`&Vec`→`&[T]` 是編譯期自動插入的 `.deref()`。別拿它模擬繼承。
- `From` 定義轉換，`Into` 自動跟著有；`From` 是 `?` 做錯誤轉換的基礎（接 Ch 13）。
- `Iterator` 只需實作 `next()`；`for` 是 `IntoIterator` + `next` 的語法糖；adapter 惰性、零成本，對照 C++20 ranges。

## 自我檢核

- [ ] 不看筆記，能不能說出「為什麼含 `Vec` 的型別不能 `#[derive(Copy)]`」，並連到 double free？
- [ ] 給你一個有三個 `Drop` 欄位的 struct，你能不能寫出它 drop 時的完整順序？
- [ ] 面試問「deref coercion 是什麼、什麼時候發生」，你能舉 `&String`→`&str` 的例子並解釋是編譯期還是執行期嗎？
- [ ] 為什麼慣例是只實作 `From` 不實作 `Into`？
- [ ] `v.iter().map(f)` 這行單獨寫會執行 `f` 嗎？為什麼？

## 延伸閱讀

### 官方文件

- **[The Rust Book — Ch 13.2: Processing a Series of Items with Iterators](https://doc.rust-lang.org/book/ch13-02-iterators.html)**
  - **讀哪裡**：整節，尤其 "Iterators Are Lazy" 和 "Implementing the Iterator Trait" 兩段。
  - **和本章的關聯**：本章 Counter 例子的官方版；補充了更多 adapter（`zip`、`filter_map`）和零成本抽象的 benchmark 討論。

- **[The Rust Book — Ch 15.2: Treating Smart Pointers Like Regular References with Deref](https://doc.rust-lang.org/book/ch15-02-deref.html)**
  - **讀哪裡**：`MyBox` 例子那段和 "Implicit Deref Coercions" 一節。
  - **和本章的關聯**：本章 `MyBox` 就是照它改的；它把 deref coercion 的三種 mutability 組合（`&T`→`&U`、`&mut T`→`&mut U`、`&mut T`→`&U`）講得比本章細。

### 部落格 / 技術文章

- **[Rust's Iterators are Inductive Functors](https://blog.polybdenum.com/2022/06/25/rust-s-iterators-are-inductive-functors.html)** — polybdenum
  - **這篇說什麼**：從型別理論角度看 iterator adapter 為什麼能零成本組合。
  - **前提知識**：懂本章的 adapter 惰性；不需要範疇論背景。
  - **為什麼值得讀**：解釋「為什麼一長串 `.map().filter()` 編出來跟手寫迴圈一樣快」的原理層面，補強本章只給結論的部分。

- **[Rust API Guidelines — C-CONV (conversion traits)](https://rust-lang.github.io/api-guidelines/interoperability.html)**
  - **讀哪裡**：`From`/`Into`/`TryFrom` 的慣例那幾條。
  - **和本章的關聯**：本章說「只實作 `From`」的官方依據，還說明何時該用 `TryFrom`（可能失敗的轉換）。

### 書籍

- **《Programming Rust, 2nd ed.》** — Blandy, Orendorff, Tindall（O'Reilly, 2021）
  - **這本書的定位**：系統向、對 C++ 使用者友善，和本章對照 C++ 的角度契合。
  - **讀哪幾章**：Chapter 13（Utility Traits）幾乎逐一對應本章的 `Drop`/`Deref`/`From`/`Copy`；Chapter 15（Iterators）是 iterator 的深入版。

下一章我們把 `From`（剛學的）和 `Option`/`Result`（enum，[Ch 8](./08-struct-enum-pattern.md) 學過）拼起來，看 Rust 怎麼把「錯誤處理」做成純粹的值傳遞——沒有 exception，錯誤就是型別，`?` 運算子怎麼展開你會親眼看到。

→ [Ch 13 錯誤處理：Result/Option/?](./13-error-handling.md)
