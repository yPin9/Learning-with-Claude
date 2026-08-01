# Ch 09 — Trait：Rust 的抽象核心

> **目標**：把 trait 理解成「共享行為的介面」，並看清它同時扮演了 C++ 的三個角色——`concept`（編譯期約束）、`virtual`（多型）、純虛類別（介面）——以及對照 C 的「手刻函式指標表 vtable」；會定義／實作 trait、寫預設方法、用 trait 當泛型約束（`T: Trait`）；理解孤兒規則（orphan rule）為什麼存在（連貫性 coherence），真跑一個違反的 `E0117`；分得清 associated type 和泛型參數的差異；會用 supertrait 和 blanket impl。

> **環境**：Rust 範例以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）跑過。C/C++ 對照用 `gcc 13`。

## 為什麼需要這個？

你在 C 裡想做「一組不同型別、共享同一套行為」——例如「所有能算面積的形狀」——只有一條路：**手刻一張函式指標表（vtable）**，外加一根指向資料的指標。

```c
struct ShapeVTable {
    double (*area)(const void *self);   // 函式指標
};
struct Shape {
    const struct ShapeVTable *vtable;   // 你自己記得配對正確的表
    const void *data;                   // type-erased 資料
};
struct Circle { double r; };
static double circle_area(const void *self) {
    const struct Circle *c = self;
    return 3.14159 * c->r * c->r;
}
static const struct ShapeVTable CIRCLE_VT = { circle_area };
```

用起來（`gcc vt.c && ./vt`，`r = 2.0`）：

```
12.57
```

這套能動，但每個問題都要你手工兜：`data` 是 `const void *`——**型別擦掉了，`circle_area` 裡那個 `self = data` 的 cast 沒有任何檢查**，你把 `CIRCLE_VT` 配到一根其實指向 `Rectangle` 的 `data`，編譯器不會攔，runtime 讀垃圾。vtable 和 data 的配對、函式簽章的一致、生命週期——全靠紀律。C++ 用 `virtual` 把這張表自動化了（compiler 幫你生 vtable），但 C++ 的 `virtual` 只解決「執行期多型」這一塊；「編譯期約束一個型別必須具備某些能力」是另一個工具（`concept`，C++20 才有）。

Rust 的 **trait** 把這幾件事統一成一個機制：它是「一組型別必須實作的方法簽章」——既能當**編譯期泛型約束**（像 concept），又能當**執行期多型**（像 virtual，Ch 11 講 `dyn`），而且型別安全全程由編譯器保證，不用你手兜 vtable。這章聚焦「trait 是什麼、怎麼用來抽象」，動態分派的底層留 Ch 11。

## 先建立直覺

把 trait 想成一份**契約書**：它列出「想成為某類東西，你必須提供這些方法」。任何型別簽了這份契約（`impl Trait for Type`），就能被當作那類東西使用。

```
        trait Summary（契約）
        ┌────────────────────────────┐
        │ 必須提供：                   │
        │   fn summarize_author()      │
        │ 可選（有預設）：              │
        │   fn summarize()             │
        └────────────────────────────┘
            ▲               ▲
    impl Summary        impl Summary
      for Tweet           for Article
            │               │
   「Tweet 簽了約」   「Article 簽了約」

   → 任何要求「會 Summary 的東西」的函式，Tweet 和 Article 都能傳進去
```

和 C 手刻 vtable 的關鍵差別：**契約由型別系統強制**。你 `impl Summary for Tweet` 但漏了某個必填方法，編譯不過；你把一個沒 impl `Summary` 的型別傳給要求 `Summary` 的函式，編譯不過。C 那個「vtable 配錯 data」的整類錯誤，在 trait 這裡型別系統直接堵死。

> 如果你熟 C++：trait 最接近的東西是「**帶方法的純虛介面** + **concept**」的合體。當泛型約束用時（`T: Summary`）像 concept，是編譯期檢查、單型化、零成本；當 `dyn Summary` 用時（Ch 11）像 `virtual`，是執行期 vtable dispatch。同一個 trait，兩種用法。

## 定義與實作 trait

先看最小的一份契約，一個必填方法、一個帶預設實作的方法：

```rust
trait Summary {
    fn summarize_author(&self) -> String;    // 必填：只有簽章，沒有本體

    fn summarize(&self) -> String {           // 預設方法：有本體，實作者可不覆寫
        format!("(read more from {}…)", self.summarize_author())
    }
}

struct Tweet { user: String, text: String }
impl Summary for Tweet {
    fn summarize_author(&self) -> String { format!("@{}", self.user) }
    // 不覆寫 summarize，用預設
}

struct Article { headline: String }
impl Summary for Article {
    fn summarize_author(&self) -> String { String::from("staff") }
    fn summarize(&self) -> String { self.headline.clone() }   // 覆寫預設
}

fn main() {
    let t = Tweet { user: "rustlang".into(), text: "hi".into() };
    let a = Article { headline: "Rust in kernel".into() };
    println!("{}", t.summarize());   // 用預設 summarize
    println!("{}", a.summarize());   // 用 Article 覆寫的
    let _ = t.text;                  // 避免 unused 警告
}
```

真跑：

```
(read more from @rustlang…)
Rust in kernel
```

拆解：

- `fn summarize_author(&self) -> String;` 結尾是分號、沒有 `{}`——這是**必填方法**，實作者一定要提供，否則 `impl` 編不過。對應 C++ 的純虛函式 `virtual … = 0;`。
- `fn summarize(&self) -> String { … }` 有本體——這是**預設方法**。實作者可以不寫（用預設），也可以覆寫。`Tweet` 沒覆寫（用 `(read more…)`），`Article` 覆寫成回傳 headline。這對應 C++ 的非純虛函式（有預設實作的 virtual）。預設方法能呼叫必填方法（`self.summarize_author()`），這是 trait 組合行為的常見手法。

`impl Summary for Tweet` 讀作「為 `Tweet` 實作 `Summary` 契約」。這行把 `Tweet` 和 `Summary` 綁在一起，之後任何要 `Summary` 的地方都能用 `Tweet`。

## trait 當泛型約束：`T: Trait`

trait 最核心的用途是**約束泛型**——「這個函式接受任何型別 `T`，只要 `T` 實作了某個 trait」。這是 trait 作為抽象工具的正題：

```rust
trait Summary {
    fn summarize(&self) -> String;
}
struct Tweet { user: String }
impl Summary for Tweet {
    fn summarize(&self) -> String { format!("@{}", self.user) }
}
struct Article { headline: String }
impl Summary for Article {
    fn summarize(&self) -> String { self.headline.clone() }
}

// notify 接受「任何實作了 Summary 的型別 T」
fn notify<T: Summary>(item: &T) {
    println!("Breaking! {}", item.summarize());
}

fn main() {
    notify(&Tweet { user: "rustlang".into() });
    notify(&Article { headline: "Rust in kernel".into() });
}
```

真跑：

```
Breaking! @rustlang
Breaking! Rust in kernel
```

`fn notify<T: Summary>(item: &T)` 的 `T: Summary` 就是約束（bound）：`T` 可以是任何型別，**但必須實作 `Summary`**。函式本體裡因此能安全呼叫 `item.summarize()`——編譯器已經保證 `T` 有這個方法。

這正是 C++20 `concept` 的角色：編譯期約束一個泛型參數必須滿足某些要求。差別是——C++ 在 concept 之前（C++17 以下），template 的約束是**隱式**的：你寫 `template<typename T> void notify(T item) { item.summarize(); }`，如果傳進來的型別沒有 `summarize`，錯誤在**實例化那一刻**才爆，而且訊息常常是幾百行的模板展開地獄。Rust 的 `T: Summary` 是**顯式**的：約束寫在簽章上，違反時（下面看）錯誤直接指向 bound 本身，清楚得多。

**違反約束會怎樣**——傳一個沒 impl `Summary` 的型別：

```rust
trait Area { fn area(&self) -> f64; }
struct Circle { r: f64 }
impl Area for Circle { fn area(&self) -> f64 { 3.14159 * self.r * self.r } }

fn print_area<T: Area>(x: &T) { println!("{:.2}", x.area()); }

struct NoArea;
fn main() {
    print_area(&NoArea);      // NoArea 沒 impl Area
}
```

真跑：

```
error[E0277]: the trait bound `NoArea: Area` is not satisfied
  --> tr6.rs:10:16
   |
10 |     print_area(&NoArea);
   |     ---------- ^^^^^^^ unsatisfied trait bound
   |     |
   |     required by a bound introduced by this call
   |
help: the trait `Area` is not implemented for `NoArea`
  ...
note: required by a bound in `print_area`
  --> tr6.rs:6:18
   |
 6 | fn print_area<T: Area>(x: &T) { println!("{:.2}", x.area()); }
   |                  ^^^^ required by this bound in `print_area`
```

`E0277` 直接說 `NoArea: Area` is not satisfied，還指出 `print_area` 的 `T: Area` bound 是要求來源。對比 C++17 模板：同樣的錯誤在 C++ 裡是 `item.area()` 那行「no member named 'area'」，深埋在實例化的展開裡，且不會告訴你「是哪個約束沒滿足」。Rust 把約束提前到簽章、錯誤指向約束本身——這是 concept 想解決、Rust 從一開始就內建的問題。

## 孤兒規則（orphan rule）：為什麼有些 impl 被禁

現在碰一個新手常撞牆、但設計上非常重要的規則。試著給標準函式庫的 `Vec<i32>` 實作標準函式庫的 `Display`：

```rust
use std::fmt;
impl fmt::Display for Vec<i32> {          // trait 和型別都不是我的
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "custom vec")
    }
}
fn main() {}
```

真跑：

```
error[E0117]: only traits defined in the current crate can be implemented for types defined outside of the crate
 --> tr2.rs:3:1
  |
3 | impl fmt::Display for Vec<i32> {
  | ^^^^^^^^^^^^^^^^^^^^^^--------
  |                       |
  |                       `Vec` is not defined in the current crate
  |
  = note: impl doesn't have any local type before any uncovered type parameters
  = note: for more information see https://doc.rust-lang.org/reference/items/implementations.html#orphan-rules
  = note: define and implement a trait or new type instead
```

`E0117`。**孤兒規則**要求：`impl Trait for Type` 中，**`Trait` 或 `Type` 至少有一個必須定義在你自己的 crate 裡**。`Display` 是標準函式庫的、`Vec` 也是標準函式庫的，兩個都不是你的——這個 impl 是「孤兒」，被禁。

為什麼要這條規則？因為 **coherence（連貫性）**：Rust 保證「對於任何 (trait, type) 組合，全世界最多只有一份 impl」。想像沒有孤兒規則：crate A 給 `Vec<i32>` impl 了 `Display` 印成 `"custom vec"`，crate B 也給 `Vec<i32>` impl 了 `Display` 印成別的。你的程式同時依賴 A 和 B，`vec.to_string()` 該用哪份？**衝突無解。** 孤兒規則從源頭防止這種衝突：因為每個 (trait, type) 至少有一半是「某個 crate 自己的」，那個 crate 對這份 impl 有唯一的話語權，不可能兩個互不相干的 crate 各自給同一組合寫 impl。

這是 Rust 用**紀律換保證**的典型：你失去了「隨手給別人的型別加別人的 trait」的自由，換來「不會有兩份衝突的 impl」的鐵保證。C++ 沒有這個約束（你可以到處特化別人的 template），代價就是 ODR 違反和連結期的地雷。

繞過的正解是 **newtype**（Ch 8 的 tuple struct 派上用場）：把 `Vec<i32>` 包進你自己的型別，那個型別就是你的了：

```rust
use std::fmt;
struct MyVec(Vec<i32>);                   // 我自己的型別
impl fmt::Display for MyVec {             // 合法：MyVec 是本 crate 的
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "custom vec of {} items", self.0.len())
    }
}
fn main() {
    println!("{}", MyVec(vec![1, 2, 3]));
}
```

真跑：

```
custom vec of 3 items
```

## Associated type vs 泛型參數

trait 有兩種「帶型別」的方式，新手常搞混：**泛型參數**（`trait Foo<T>`）和 **associated type**（`trait Foo { type T; }`）。差別的核心是——**一個型別能不能對同一個 trait 有多份 impl**。

先看**泛型參數**：允許同一型別實作多次，每次帶不同的型別參數。

```rust
trait ConvertTo<T> {
    fn convert(&self) -> T;
}
struct Celsius(f64);
impl ConvertTo<f64> for Celsius {         // 轉成 f64
    fn convert(&self) -> f64 { self.0 * 9.0/5.0 + 32.0 }
}
impl ConvertTo<String> for Celsius {      // 也能轉成 String——同型別、兩份 impl
    fn convert(&self) -> String { format!("{}C", self.0) }
}
fn main() {
    let c = Celsius(100.0);
    let f: f64 = c.convert();             // 靠回傳型別標註選 impl
    let s: String = c.convert();
    println!("{f} / {s}");
}
```

真跑：

```
212 / 100C
```

`Celsius` 對 `ConvertTo` 有**兩份** impl（`ConvertTo<f64>` 和 `ConvertTo<String>`），呼叫時靠 `let f: f64` / `let s: String` 的型別標註決定用哪份。泛型參數是「輸入型別」——由使用端挑。

再看 **associated type**：每個型別對這個 trait 只能有**一份** impl，`type Item` 由實作者一次定死。

```rust
trait Container {
    type Item;                             // associated type：實作者填一次
    fn get(&self, i: usize) -> Option<&Self::Item>;
    fn first(&self) -> Option<&Self::Item> { self.get(0) }   // 預設方法能用 Self::Item
}
struct IntBag { data: Vec<i32> }
impl Container for IntBag {
    type Item = i32;                       // 定死 Item = i32
    fn get(&self, i: usize) -> Option<&i32> { self.data.get(i) }
}
fn main() {
    let b = IntBag { data: vec![10, 20] };
    println!("{:?}", b.first());
}
```

真跑：

```
Some(10)
```

如果你想給 `IntBag` 再寫第二份 `Container` impl 把 `Item` 定成別的型別，會撞 `E0119`：

```rust
trait Producer { type Output; fn produce(&self) -> Self::Output; }
struct Gen;
impl Producer for Gen { type Output = i32; fn produce(&self) -> i32 { 1 } }
impl Producer for Gen { type Output = String; fn produce(&self) -> String { String::from("x") } }
fn main() {}
```

真跑：

```
error[E0119]: conflicting implementations of trait `Producer` for type `Gen`
 --> tr5.rs:4:1
  |
3 | impl Producer for Gen { type Output = i32; fn produce(&self) -> i32 { 1 } }
  | --------------------- first implementation here
4 | impl Producer for Gen { type Output = String; fn produce(&self) -> String { String::from("x") } }
  | ^^^^^^^^^^^^^^^^^^^^^ conflicting implementation for `Gen`
```

`E0119`：`Gen` 對 `Producer` 只能有一份 impl。這就是差別的本質——**associated type 是「輸出型別」，由實作端一次決定，所以每型別只一份 impl**；泛型參數是「輸入型別」，由呼叫端挑，所以可多份。

最重要的實例是 `Iterator`：

```rust
// 標準函式庫（簡化）：
// trait Iterator {
//     type Item;                               // 用 associated type，不是泛型參數
//     fn next(&mut self) -> Option<Self::Item>;
// }
```

`Iterator` 用 `type Item` 而非 `trait Iterator<Item>`，是刻意的：一個型別「迭代出什麼」應該是**固定的**——`Vec<i32>` 的 iterator 就是產出 `i32`，不該存在「`Vec<i32>` 的 iterator 也能產出 `String`」的第二份 impl。用 associated type 把 `Item` 定死，寫 `fn sum(it: impl Iterator<Item = i32>)` 這種約束時也更乾淨（不用到處帶著 `<Item>` 參數）。**判準：型別由「使用端該挑」用泛型參數；由「實作端該定死、每型別只一種」用 associated type。**

## Supertrait 與 blanket impl

**Supertrait**：一個 trait 可以要求「實作我之前，你得先實作另一個 trait」。語法是 `trait Sub: Super`：

```rust
use std::fmt::Display;

trait Named: Display {                     // 要 impl Named，必先 impl Display
    fn label(&self) -> String {
        format!("[{}]", self)              // 因為保證有 Display，可以 {} 格式化 self
    }
}
struct Id(u32);
impl Display for Id {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "id#{}", self.0)
    }
}
impl Named for Id {}                       // Id 已有 Display，可以 impl Named

fn main() {
    println!("{}", Id(7).label());
}
```

真跑：

```
[id#7]
```

`trait Named: Display` 的 `: Display` 表示 `Display` 是 `Named` 的 supertrait。好處是 `Named` 的預設方法 `label` 裡可以直接 `format!("{}", self)`——因為型別系統保證「凡是 `Named` 的東西必然也是 `Display`」。這對應 C++ 的介面繼承（`class Named : public Display`）。

**Blanket impl**：為「所有滿足某條件的型別」一次性實作一個 trait。標準函式庫最經典的是 `impl<T: Display> ToString for T`——任何實作 `Display` 的型別自動獲得 `.to_string()`。自己寫一個：

```rust
use std::fmt::Display;

trait Loud {
    fn shout(&self) -> String;
}
// 為「所有實作 Display 的 T」一次 impl Loud
impl<T: Display> Loud for T {
    fn shout(&self) -> String {
        format!("{}!!!", self)
    }
}
fn main() {
    println!("{}", 42.shout());            // i32 有 Display，自動有 shout
    println!("{}", "hey".shout());         // &str 也有
}
```

真跑：

```
42!!!
hey!!!
```

`impl<T: Display> Loud for T` 讀作「對於任何實作了 `Display` 的型別 `T`，都給它 `Loud`」。這一行讓 `i32`、`&str`、`String`……所有能 `Display` 的型別瞬間都有 `.shout()`。標準函式庫的 `ToString`、`Into`（從 `From` 自動得來）都是這樣實作的。blanket impl 的威力也是孤兒規則存在的原因之一——如果沒有 coherence 保證，兩個 crate 各寫一個衝突的 blanket impl，整個型別系統會塌。

## 對比與取捨

| 抽象手段 | 檢查時機 | 型別安全 | 執行期成本 | 對應 C/C++ |
|---|---|---|---|---|
| C 手刻 vtable（函式指標表 + `void*`） | 無（全靠紀律） | 無（`void*` cast 不檢查） | 一次間接呼叫 | — |
| C++ template（無 concept） | 實例化時 | 有，但錯誤訊息糟 | 零（單型化） | — |
| C++20 concept | 呼叫/實例化時 | 有，訊息改善 | 零 | ≈ `T: Trait` |
| C++ `virtual` | 編譯期簽章 + runtime dispatch | 有 | vtable 間接呼叫 | ≈ `dyn Trait`（Ch 11） |
| **Rust trait as `T: Trait`** | 編譯期（簽章上的 bound） | 有，錯誤指向 bound | 零（單型化，Ch 10） | concept |
| **Rust trait as `dyn Trait`** | 編譯期 + runtime | 有 | vtable 間接呼叫 | `virtual`（Ch 11） |

取捨：trait 把 C 手刻 vtable 的「配對正確性靠紀律」和 C++ template「約束隱式、錯誤難讀」兩個痛點一次解決——約束顯式寫在簽章、由型別系統強制、錯誤指向約束本身。代價是孤兒規則限制了「隨手給別人型別加別人 trait」的自由，以及你得學會 associated type vs 泛型參數的取捨。多數情況這些限制反而幫你——它們是 coherence 保證的來源。

## 踩雷集錦

1. **以為 trait 只是「介面」（interface）**：不完全。純虛介面只解決執行期多型（`dyn`），但 trait 更常見的用法是**編譯期泛型約束**（`T: Trait`），這時它像 concept、零成本、單型化，跟 vtable 一點關係都沒有。把 trait 只理解成 Java interface 會讓你在 Ch 10（單型化）困惑「為什麼沒有 vtable」。

2. **撞了孤兒規則就以為是編譯器 bug**：`E0117`「只能給本 crate 的 trait 或本 crate 的型別寫 impl」是**刻意**的 coherence 保證，不是限制你。想給外部型別加外部 trait 的行為，用 newtype 包一層（`struct MyVec(Vec<i32>)`）。理解它防的是「兩個 crate 給同一 (trait,type) 寫衝突 impl」這個無解狀況。

3. **該用 associated type 卻用了泛型參數（或反之）**：判準是「這個型別對 trait 該有幾份 impl」。`Iterator` 的 `Item` 用 associated type，因為「`Vec<i32>` 迭代出什麼」該固定；`From<T>` 的 `T` 用泛型參數，因為「一個型別能從很多種型別轉換來」。用錯會讓你要嘛沒法寫多份 impl（該用泛型卻用了 associated），要嘛約束處處要帶型別參數（該用 associated 卻用了泛型）。

4. **忘了 trait 得在 scope 裡才能呼叫它的方法**：`item.summarize()` 要能編譯，`Summary` 這個 trait 必須 `use` 進當前 scope（除非是同檔定義）。常見錯誤是用了某個 crate 的型別、方法卻報「method not found」，其實是忘了 `use that_crate::TheTrait`。這和 C++ 的 ADL 行為不同——Rust 要你顯式把 trait 帶進來。

5. **預設方法覆寫了就完全取代，不會「呼叫父類再擴充」**：`Article` 覆寫 `summarize` 後，預設的實作**完全不執行**，不像有些語言能 `super.summarize()`。想要「先跑預設再加東西」得自己在覆寫版裡重新組合（或把共用邏輯抽成另一個必填方法讓預設呼叫）。

## 進階：再往深一層

**`impl Trait` 作為參數與回傳型別。** `fn notify(item: &impl Summary)` 是 `fn notify<T: Summary>(item: &T)` 的語法糖，用於「我不需要給這個型別參數命名」時。回傳位置的 `-> impl Iterator<Item = i32>` 則表示「回傳某個實作了 Iterator 的具體型別，但我不告訴你是哪個」——常用來回傳閉包或複雜的 iterator 鏈（Ch 12、Ch 14 會用）。

**trait 方法的 `self` 三態。** 方法第一參數可以是 `&self`（借用）、`&mut self`（可變借用）、`self`（取得所有權，消費物件）。這直接連到 Part 1 的 ownership：`fn into_inner(self) -> T` 這種「消費 self」的方法，呼叫後原物件就不能用了。設計 trait 時選哪個 `self` 形態，是在決定「呼叫這個方法會不會拿走物件」。

**面試常問**：「Rust 的 trait 和 Java/C++ 的 interface 有什麼不同？」——三點：（1）trait 能為**已存在**的型別（包括基本型別如 `i32`）事後 impl，interface 通常要在型別定義時就宣告；（2）trait 有預設方法和 associated type/const，比傳統 interface 表達力強；（3）trait 既能編譯期靜態分派（`T: Trait`，零成本）又能執行期動態分派（`dyn Trait`），interface 通常只有後者。孤兒規則是為了在（1）的自由下仍保住 coherence。

## 動手練習

1. **手刻 C vtable vs Rust trait**：把本章開頭那個 C 的 `ShapeVTable` 擴充成支援 `Circle` 和 `Rectangle` 兩種形狀，故意把 `Rectangle` 的 data 配上 `CIRCLE_VT`（配錯表），跑一次看它算出垃圾但編譯無警告。再用 Rust `trait Shape { fn area(&self) -> f64; }` + 兩個 `impl` 寫等價的，體會「配錯」在 Rust 根本寫不出來。

2. **觸發並修好孤兒規則**：試著 `impl std::fmt::Display for Vec<i32>`，確認 `E0117`；然後用 newtype `struct MyVec(Vec<i32>)` 修好它，讓 `println!("{}", MyVec(vec![1,2,3]))` 能跑。

3. **associated type vs 泛型參數的抉擇**：寫一個 `trait Parser { type Output; fn parse(&self, s: &str) -> Self::Output; }`，給一個 `IntParser`（`Output = i32`）實作。然後試著給 `IntParser` 再寫一份 `Output = f64` 的 impl，看 `E0119`。想清楚：如果 `Parser` 的型別參數改成泛型（`trait Parser<Output>`），這第二份 impl 就合法了——這正是兩者的分野。

## 本章重點整理

- **trait 是共享行為的契約**：列出型別必須實作的方法簽章。它統一了 C++ 的 `concept`（編譯期約束）、`virtual`（執行期多型）、純虛介面三個角色，也取代了 C 手刻 vtable + `void*` 的不安全做法。
- **`T: Trait` 是最核心用法**：約束泛型參數必須實作某 trait，編譯期檢查、單型化、零成本（像 concept 但約束顯式、錯誤指向 bound，`E0277`）。
- **孤兒規則（`E0117`）保 coherence**：`impl Trait for Type` 的 trait 或 type 至少一個要是本 crate 的，防止兩個 crate 給同一組合寫衝突 impl。繞過用 newtype。
- **associated type vs 泛型參數**：前者「輸出型別、每型別一份 impl」（`Iterator::Item`），後者「輸入型別、可多份 impl」（`From<T>`）；用錯 `E0119` 或約束變囉嗦。
- **supertrait**（`trait Sub: Super`）要求先實作另一 trait；**blanket impl**（`impl<T: Display> ToString for T`）為所有滿足條件的型別一次實作，是 `ToString`/`Into` 的實作方式。

## 自我檢核

- [ ] 面試問「trait 和 Java interface 差在哪」，能答出「能事後為既存型別（含 `i32`）impl、有預設方法/associated type、且能靜態或動態分派」三點。
- [ ] 不看筆記，能解釋孤兒規則存在的理由（coherence：防止兩 crate 給同一 (trait,type) 寫衝突 impl），並說出用 newtype 繞過。
- [ ] 能說出什麼時候該用 associated type、什麼時候該用泛型參數，並用 `Iterator::Item` vs `From<T>` 舉例。
- [ ] 知道 `T: Trait`（靜態、零成本）和 `dyn Trait`（動態、vtable）是同一個 trait 的兩種用法，且理解本章只講前者。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 10.2「Traits: Defining Shared Behavior」** — （[doc.rust-lang.org/book/ch10-02-traits.html](https://doc.rust-lang.org/book/ch10-02-traits.html)）
  - **讀哪裡**：整節，尤其「Default Implementations」「Traits as Parameters」「Using Trait Bounds」。孤兒規則在「Implementing a Trait on a Type」小節有提。
  - **學到什麼**：本章 trait 定義、預設方法、`T: Trait` 的官方對應版，用 `Summary`/`Tweet` 例子（本章沿用），講得更慢。
  - **前提**：懂前面的泛型基礎（The Book Ch 10.1）；associated type 在 Ch 19.2 才深入。

- **《Rust for Rustaceans》Ch 2「Types」與 Ch 3「Designing Interfaces」** — Jon Gjengset（No Starch Press, 2021）
  - **讀哪裡**：Ch 2 講 trait 與 coherence（孤兒規則的完整推導）、associated type vs 泛型參數的權衡；Ch 3 講怎麼設計好用的 trait 介面。
  - **學到什麼**：本章「為什麼有孤兒規則」「associated type vs 泛型」的更深版本，含實務設計判準。這是本課定位最重合的一本書。
  - **前提**：懂本章基本 trait；此書假設你已會安全 Rust。

### 官方參考

- **《The Rust Reference》「Implementations - Orphan rules」** — （[doc.rust-lang.org/reference/items/implementations.html](https://doc.rust-lang.org/reference/items/implementations.html)）
  - **讀哪裡**：「Trait Implementation Coherence」與「Orphan rules」兩段，正是本章 `E0117` 的形式化定義（含 "uncovered type parameter" 這種本章沒展開的細節）。
  - **學到什麼**：孤兒規則的精確條文——為什麼 `impl<T> ForeignTrait<LocalType> for T` 有時合法有時不合法，比本章的直覺版更嚴謹。
  - **前提**：懂本章孤兒規則直覺；Reference 是條文，配本章例子讀。

### 技術文章

- **「Rust's Built-in Traits, the When, How & Why」** — llogiq（[llogiq.github.io/2015/07/30/traits.html](https://llogiq.github.io/2015/07/30/traits.html)）
  - **這篇說什麼**：系統整理標準函式庫的核心 trait（`From`/`Into`、`Display`、`Iterator` 等）分別用泛型參數還是 associated type、為什麼，正對本章「兩者抉擇」那節。
  - **讀哪裡**：從「associated types」那段開始，看它逐個標準 trait 的設計選擇。
  - **為什麼值得讀**：作者是活躍的 Rust 貢獻者（clippy lint 作者之一），從標準函式庫實際設計反推判準，比抽象講原理更有感。

trait 定義了抽象，但「用 `T: Trait` 的泛型函式，編譯後到底變成什麼」還沒交代——那是下一章**單型化（monomorphization）**的正題：編譯器為每個具體型別生一份特化碼，這就是 `T: Trait` 零成本的技術根源，也解釋了為什麼它和 `dyn Trait` 的 vtable 完全不同。

→ [Ch 10 泛型與單型化](./10-generics-monomorphization.md)
