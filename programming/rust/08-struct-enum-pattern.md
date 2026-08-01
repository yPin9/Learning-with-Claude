# Ch 08 — Struct、Enum 與 Pattern Matching

> **目標**：把 Rust 的 `struct` 對上 C 的 `struct`、把 Rust 的 `enum` 對上 C 的「`union` + 手動 tag」並理解它為什麼安全太多；知道 `Option<T>`/`Result<T,E>` 只是普通 enum、不是編譯器魔法；會用 `match`（含解構、guard、`@`、範圍）並理解窮盡性檢查（真跑 `E0004`）；看得懂帶資料 enum 的記憶體佈局（`size_of` 真跑）與 niche optimization 的預告（`Option<&T>` 為何和 `&T` 同大小）。

> **環境**：Rust 範例以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）跑過。C 對照用 `gcc 13`。記憶體佈局數字都是本機 `size_of` 真實輸出——**Rust 的 struct/enum 佈局預設是未指定的（`repr(Rust)`），不同版本/架構可能不同**，這章的數字反映的是本環境行為，看重的是「為什麼是這個量級」而非「保證永遠是這個值」（`repr` 的完整規則在 Ch 15）。

## 為什麼需要這個？

你在 C 裡表達「一個值是這幾種型別之一」時，工具箱只有 `union` + 手動 tag：

```c
enum Tag { INT, FLT };
struct Value {
    enum Tag tag;                 // 你自己記哪個成員 active
    union { int i; float f; } u;  // 同一塊記憶體，兩種解讀
};
```

`union` 讓 `i` 和 `f` 共用同一塊記憶體，`tag` 是你手動維護的「現在哪個成員有效」標記。問題是：**編譯器完全不強制 tag 和 union 的一致性**。你設了 `tag = INT`、寫了 `u.i`，然後手滑讀 `u.f`，編譯器一句話都不說：

```c
struct Value v;
v.tag = INT;
v.u.i = 42;
printf("as float (wrong): %f\n", v.u.f);   // 把 int 的 bit 當 float 解讀
```

真跑（`gcc vp.c && ./vp`）：

```
as float (wrong): 0.000000
```

整數 `42` 的 bit pattern 當成 IEEE 754 float 解讀，印出來是接近 0 的垃圾。沒有警告、沒有崩潰，只是靜默的錯誤解讀。這是一整類 bug 的溫床：忘了更新 tag、更新了 tag 忘了改 union、在 `switch (tag)` 裡漏一個 case。C 對這些**零防護**——tagged union 的正確性全靠你的紀律。

Rust 的 `enum` 就是為了解決這個問題設計的：它把 tag（Rust 叫 **discriminant**）綁進型別本身，你**無法**手動讀錯 variant，`match` 還會強制你窮盡所有 variant。這章講 struct（存資料的容器）、enum（sum type / tagged union）、以及拆開它們的工具 pattern matching。

## 先建立直覺

先分清楚兩個對偶概念：

- **struct 是 product type（積型別）**：`struct Point { x: i32, y: i32 }` 同時擁有 `x` **和** `y`。可能的值數量是 `|i32| × |i32|`——像笛卡兒積，所以叫 product。這對應 C 的 `struct`，你已經很熟。
- **enum 是 sum type（和型別）**：`enum Shape { Circle(f64), Rectangle(f64, f64) }` 是 `Circle` **或** `Rectangle`，任一時刻只是其中一個。可能的值數量是 `|Circle 的值| + |Rectangle 的值|`——所以叫 sum。這對應 C 的 tagged union，但安全。

```
  product（struct）：同時擁有        sum（enum）：擇一擁有

  ┌───────────────┐               ┌──────────────────────────┐
  │ x: i32        │               │ discriminant: 現在是哪個   │
  │ y: i32        │               ├──────────────────────────┤
  └───────────────┘               │  Circle(f64)             │  ← 同一塊空間
    兩個都在                        │  或 Rectangle(f64,f64)    │     依 tag 解讀
                                   └──────────────────────────┘
```

關鍵直覺：**Rust 的 enum = C 的 `union` + tag，但 tag 由編譯器管、由型別系統強制。** 你拿到一個 `Shape`，唯一能安全取出裡面資料的方法是 `match`——而 `match` 會逼你處理每一個 variant。你沒辦法像 C 那樣「假設現在是 Circle」直接讀，除非你 match 到了 `Circle` 那個 arm，這時編譯器才把 `f64` 交給你。錯誤的解讀在型別層次就不可能發生。

## Struct：三種形態

Rust 的 struct 有三種寫法，對應不同的使用情境：

```rust
// 1. 具名欄位 struct（named-field）——最常見，等同 C struct
struct Point { x: i32, y: i32 }

// 2. tuple struct——欄位沒名字，用位置存取。適合「薄包裝」
struct Wrapper(i32, i32);

// 3. unit struct——沒有欄位。當標記型別（marker）用
struct Marker;

fn main() {
    let p = Point { x: 3, y: 7 };
    println!("{} {}", p.x, p.y);

    let w = Wrapper(1, 2);
    println!("{} {}", w.0, w.1);      // 用 .0 .1 存取

    let _m = Marker;                  // 佔 0 bytes，純型別標記
}
```

真跑：

```
3 7
1 2
```

三者的定位：

- **具名 struct**：和 C 的 `struct` 一對一，欄位有名字。日常主力。
- **tuple struct**：常用來做 **newtype**——`struct Meters(f64)` 把一個 `f64` 包成一個新型別，讓「公尺」和「秒」在型別上不能互換（C 裡兩者都是 `double`，可以亂加）。`Wrapper.0` 這種存取對應 C 沒有的東西。
- **unit struct**：0 bytes，通常配合 trait 當作「只帶行為、不帶資料」的標記型別用（Ch 9 會用到）。

> 記憶體佈局上，具名 struct 和 C struct 一樣是欄位依序擺放 + 對齊 padding，**但 Rust 預設會重排欄位**（`repr(Rust)`）來省 padding，C 不會。要和 C ABI 對齊得標 `#[repr(C)]`。細節在 Ch 15，這裡先知道「別假設 Rust struct 的欄位順序和你宣告的一樣」。

## Enum：sum type，把 tag 綁進型別

現在看 enum 怎麼把最開頭那個 C tagged union 的災難擋掉。每個 variant 可以攜帶不同的資料：

```rust
enum Msg {
    Quit,                          // 無資料（像 C enum 的一個常數）
    Move { x: i32, y: i32 },       // 具名欄位（像內嵌一個 struct）
    Write(String),                 // 一個 tuple 欄位
    Color(u8, u8, u8),             // 三個 tuple 欄位
}

fn process(m: Msg) {
    match m {
        Msg::Quit => println!("quit"),
        Msg::Move { x, y } => println!("move to {x},{y}"),
        Msg::Write(s) => println!("write: {s}"),
        Msg::Color(r, g, b) => println!("color {r} {g} {b}"),
    }
}

fn main() {
    process(Msg::Move { x: 1, y: 2 });
    process(Msg::Write(String::from("hi")));
    process(Msg::Color(255, 0, 0));
}
```

真跑：

```
move to 1,2
write: hi
color 255 0 0
```

對照 C：`Msg` 的四個 variant 就是 C 裡那個 `union` 的四種成員，`Msg::Quit`/`Msg::Move`/… 對應那個 `enum Tag`。差別是——你**無法**在沒 match 到 `Color` 的情況下讀那三個 `u8`。要拿 `Color` 的資料，唯一的路是 `Msg::Color(r, g, b) => …` 這個 arm，而進到這個 arm 就代表 discriminant 確實是 `Color`。**「讀錯 variant」在 Rust 裡不是 bug，是編譯不過的型別錯誤。** 那個把 `int` 讀成 `float` 的 C 災難，在 Rust 根本寫不出來。

## Option 與 Result：不是魔法，就是 enum

很多人以為 `Option`、`Result` 是內建的特殊型別。**不是。** 它們就是標準函式庫裡用你剛學的 enum 定義的普通型別：

```rust
// 標準函式庫裡（簡化）就長這樣：
// enum Option<T> { None, Some(T) }
// enum Result<T, E> { Ok(T), Err(E) }

fn find_even(v: &[i32]) -> Option<i32> {
    for &x in v {
        if x % 2 == 0 { return Some(x); }
    }
    None
}

fn main() {
    match find_even(&[1, 3, 4, 5]) {
        Some(n) => println!("found {n}"),
        None => println!("none"),
    }
    match find_even(&[1, 3, 5]) {
        Some(n) => println!("found {n}"),
        None => println!("none"),
    }
}
```

真跑：

```
found 4
none
```

這件事的重量：C 表達「可能沒有值」用的是 magic value（`NULL`、`-1`、`errno`）——而 magic value 沒有型別層次的強制力，你可以忘記檢查 `NULL` 直接解參考。Rust 用 `Option<T>` 這個**普通 enum** 表達，`match`（下面講）強制你同時處理 `Some` 和 `None`。「忘記檢查 null」這個著名的十億美元錯誤在 Rust 變成「編譯器要你處理 `None` 那個 arm」。`Result<T,E>` 同理，把 C 的 `errno` / 回傳碼那套「可能失敗」用型別表達（完整錯誤處理是 Ch 13）。

重點是：這不是編譯器對某個特殊型別開的後門，是 enum + match 這套通用機制的自然結果。你自己也能定義一個一模一樣的 `MyOption`。

## 窮盡性檢查：漏一個 arm 就編不過

`match` 最強的保證是**窮盡性（exhaustiveness）**：你必須處理 enum 的每一個 variant，漏一個就編譯錯誤。這正是 C 的 `switch` 沒有的——C 的 `switch` 漏 case 只是安靜地什麼都不做。

故意漏掉 `West`：

```rust
enum Dir { North, South, East, West }

fn name(d: Dir) -> &'static str {
    match d {
        Dir::North => "N",
        Dir::South => "S",
        Dir::East  => "E",
        // 故意漏 West
    }
}
fn main() {}
```

真跑：

```
error[E0004]: non-exhaustive patterns: `Dir::West` not covered
 --> se2.rs:4:11
  |
4 |     match d {
  |           ^ pattern `Dir::West` not covered
  |
note: `Dir` defined here
 --> se2.rs:1:6
  |
1 | enum Dir { North, South, East, West }
  |      ^^^                       ---- not covered
  = note: the matched value is of type `Dir`
help: ensure that all possible cases are being handled by adding a match arm with a wildcard pattern or an explicit pattern as shown
  |
7 ~         Dir::East  => "E",
8 ~         Dir::West => todo!(),
  |
```

`E0004` 直接點名 `Dir::West` not covered。這個檢查的實用價值在**維護**：假設半年後有人給 `Dir` 加了一個 `Northeast` variant，所有沒有 `_` 通配的 `match` 會**全部編譯失敗**，逼你回去每一處決定該怎麼處理新 variant。C 的 `switch` 加了新 enum 常數後，舊的 switch 照樣編過、悄悄走 default 或什麼都不做——bug 就這樣溜進 production。

> 這也是為什麼有經驗的 Rust 開發者**不隨便加 `_ => …` 通配 arm**：`_` 會吃掉未來新增的 variant，讓你失去這個「加 variant 時編譯器提醒你」的保護。只在真的想「其餘全部一樣處理」時才用 `_`。

## match 只是 pattern matching 的一種入口

`match` 底層是 **pattern（模式）** 機制。pattern 不只用在 `match`——`let`、函式參數、`if let`、`while let`、`let else` 都在做 pattern matching。先看 pattern 的幾種能力。

**解構（destructuring）**：把複合值拆開，直接綁定內部欄位到變數。

```rust
struct Point { x: i32, y: i32 }
fn main() {
    let p = Point { x: 3, y: 7 };
    let Point { x, y } = p;              // 解構，x=3 y=7
    println!("destructured: x={x} y={y}");
}
```

真跑：

```
destructured: x=3 y=7
```

**guard、範圍、`@` 綁定**：

```rust
fn classify(n: i32) -> &'static str {
    match n {
        0 => "zero",
        1..=9 => "small",                                            // 範圍 pattern
        big @ 10..=99 => { println!("(captured {big})"); "medium" }  // @ 綁定 + 範圍
        _ if n < 0 => "negative",                                    // guard
        _ => "large",
    }
}
fn main() {
    println!("{}", classify(5));
    println!("{}", classify(42));
    println!("{}", classify(-3));
}
```

真跑：

```
small
(captured 42)
medium
negative
```

三個新工具：

- **範圍 pattern `1..=9`**：匹配 1 到 9（含）。C 的 `switch` 沒有這個（GCC 有 `case 1 ... 9:` 擴充，但非標準）。
- **`@` 綁定**：`big @ 10..=99` 意思是「匹配 10–99 的範圍，同時把值綁到 `big`」。你既要做範圍測試、又要拿到那個值時用。
- **guard `_ if n < 0`**：在 pattern 後面加 `if` 條件。pattern 匹配 **且** 條件成立才進這個 arm。注意 guard 不參與窮盡性分析——編譯器不知道你的 `if` 涵蓋了什麼，所以有 guard 的 arm 後面通常還需要 fallback。

## if let / while let / let else：match 的輕量版

當你只關心一種 pattern、其他都不管時，寫完整 `match` 太囉嗦。三個簡化語法：

```rust
fn main() {
    // if let：只處理 Some，忽略 None
    let opt = Some(10);
    if let Some(v) = opt {
        println!("if let got {v}");
    }

    // while let：pattern 匹配就一直迴圈，這裡持續 pop 到空
    let mut stack = vec![1, 2, 3];
    while let Some(top) = stack.pop() {
        println!("pop {top}");
    }
}
```

真跑：

```
if let got 10
pop 3
pop 2
pop 1
```

`while let Some(top) = stack.pop()` 讀作「只要 `pop()` 回傳 `Some`，就把裡面的值綁到 `top` 跑迴圈；一旦回 `None`（stack 空了）就停」。這個模式在寫「消費一個容器直到空」時極常見。

**let else（Rust 1.65+ 穩定）** 是相對新的工具，解決「pattern 沒匹配就提早 return/break」的情境：

```rust
fn parse(s: &str) -> i32 {
    let Ok(n) = s.parse::<i32>() else {
        println!("parse failed for {s:?}, defaulting to -1");
        return -1;                      // else 分支必須發散（return/break/panic）
    };
    n * 2                               // 這行之後 n 在正常控制流裡可用
}
fn main() {
    println!("{}", parse("21"));
    println!("{}", parse("abc"));
}
```

真跑：

```
42
parse failed for "abc", defaulting to -1
-1
```

`let Ok(n) = … else { … }` 的語意：如果右邊匹配 `Ok(n)`，`n` 綁定並繼續往下；如果不匹配（是 `Err`），跑 `else` 區塊，而 `else` 區塊**必須發散**（`return`、`break`、`continue` 或 `panic!`，不能正常往下走）。好處是 `n` 在 `else` 之後是在**正常的變數作用域**裡，不像 `if let` 會把後續邏輯縮進大括號一層。這是「happy path 靠左對齊、失敗提早退出」風格的關鍵語法。

## 底層機制：enum 的記憶體佈局

回到最開頭的問題——enum 在記憶體裡到底長什麼樣？直覺答案是 **discriminant + 最大 variant 的 union**：一塊足以裝下最大 variant 的空間，加上一個標記「現在是哪個 variant」的 discriminant。

```
  enum Shape { Circle(f64), Rectangle(f64, f64), Point }

  ┌────────────┬──────────────────────────────────┐
  │discriminant│  payload（大到能裝下最大 variant） │
  │  (tag)     │  Rectangle 要 2×f64 = 16 bytes     │
  └────────────┴──────────────────────────────────┘
        ↑ 加對齊 padding 後 = 24 bytes（本環境）
```

真跑量：

```rust
use std::mem::size_of;

enum Shape { Circle(f64), Rectangle(f64, f64), Point }
enum Simple { A, B, C }

fn main() {
    println!("size_of::<f64>()             = {}", size_of::<f64>());
    println!("size_of::<Shape>()           = {}", size_of::<Shape>());
    println!("size_of::<Simple>()          = {}", size_of::<Simple>());
    println!("size_of::<Option<u8>>()      = {}", size_of::<Option<u8>>());
    println!("size_of::<u8>()              = {}", size_of::<u8>());
    println!("size_of::<&i32>()            = {}", size_of::<&i32>());
    println!("size_of::<Option<&i32>>()    = {}", size_of::<Option<&i32>>());
    println!("size_of::<Option<Box<i32>>>()= {}", size_of::<Option<Box<i32>>>());
}
```

真跑（本環境）：

```
size_of::<f64>()             = 8
size_of::<Shape>()           = 24
size_of::<Simple>()          = 1
size_of::<Option<u8>>()      = 2
size_of::<u8>()              = 1
size_of::<&i32>()            = 8
size_of::<Option<&i32>>()    = 8
size_of::<Option<Box<i32>>>()= 8
```

逐一拆解：

- **`Shape` = 24**：最大 variant 是 `Rectangle(f64, f64)` = 16 bytes。discriminant 需要 1 byte，但因為 payload 要對齊到 8（`f64` 的對齊需求），discriminant 加 padding 佔一整個 8-byte 槽 → 8 + 16 = 24。這就是「discriminant + 最大 variant」加對齊的直接結果。
- **`Simple` = 1**：三個 variant 都不帶資料，只需要 discriminant 區分三種狀態，1 byte 足夠（能表示 0–255，遠夠 3 種）。這對應 C 的 `enum`——C 的 `enum` 預設也是一個整數。
- **`Option<u8>` = 2**：`Some(u8)` 要 1 byte 存 `u8` + 1 byte discriminant 區分 Some/None = 2。符合「discriminant + payload」的樸素模型。

## Niche optimization：Option<&T> 為什麼和 &T 同大小

看那兩行「反常」的：`Option<&i32>` = 8，和 `&i32` 一模一樣；`Option<Box<i32>>` = 8，和裸 `Box` 一樣。按樸素模型，`Option` 應該多一個 discriminant，該是 16 才對。為什麼沒有？

答案是 **niche optimization（生態位優化）**。「niche」指一個型別的合法值裡**用不到的那些 bit pattern**。`&i32`（引用）和 `Box<i32>`（擁有的堆指標）保證**永遠非 null**——`0x0` 這個 bit pattern 對它們是不合法的、用不到的。編譯器就拿這個「用不到的 0」來當 `None`：

```
  Option<&i32> 的佈局（利用 niche）：

   bit pattern = 0x0000000000000000  →  代表 None      ← 借用「非法值」當 tag
   bit pattern = 任何非零位址          →  代表 Some(&i32)

   不需要額外的 discriminant byte！Option<&i32> 就是一個指標大小。
```

所以 `Option<&i32>` 不用多存 discriminant——它直接用「指標是不是 0」來區分 `Some`/`None`。這就是為什麼 Rust 裡 `Option<&T>` 是零成本的：包一層 `Option` 不增加任何空間或執行成本，卻拿到了型別強制的 null 檢查。**這正好是「C 用 null 表示沒有、但沒有型別強制」和「Rust 用 `Option` 表示沒有、有型別強制、又不多花空間」兩全其美的技術根源。**

`Option<u8>` 沒有這個好處（= 2 而非 1），是因為 `u8` 的 256 個值全部合法，沒有 niche 可借，只能老實加 discriminant。哪些型別有 niche（引用、`Box`、`NonZeroU32`、`bool`、`char` 等）、niche 怎麼在巢狀 enum 裡層層利用，是 Ch 15 的正題。這裡你只要記住結論：**帶「保證非零」指標的 `Option` 不多花空間，這不是巧合，是編譯器有意的佈局優化。**

## 對比與取捨

| 面向 | C tagged union（`union` + 手動 tag） | Rust `enum` |
|---|---|---|
| tag 與 payload 一致性 | 靠工程師紀律，編譯器零檢查 | 型別強制，讀錯 variant 編不過 |
| 漏處理某個 case | `switch` 漏 case 安靜走過 | `match` 漏 arm → `E0004` 編譯錯誤 |
| 加新 variant | 舊 switch 照編過，悄悄漏處理 | 所有無 `_` 的 match 全部編譯失敗，逼你更新 |
| 「可能沒有值」 | magic value（`NULL`/`-1`），無強制 | `Option<T>`，match 強制處理 None |
| 記憶體 | union 大小 + 你自己塞的 tag | discriminant + 最大 variant，且可能 niche 優化省掉 discriminant |
| 取值 | 直接讀 union 成員（可能讀錯） | 只能經由匹配到的 arm 取得，型別正確 |

取捨很清楚：Rust 用「你必須 `match`、必須窮盡」換來「不可能讀錯 variant、不可能漏 case」。代價是語法上比 C 直接讀 union 成員囉嗦一點，以及你得接受「加 variant 會讓一堆 match 編不過」（但這是**特性**不是 bug——它在幫你找出所有需要更新的地方）。

## 踩雷集錦

1. **以為 `Option`/`Result` 是編譯器內建的特殊型別**：不是。它們是標準函式庫裡用普通 `enum` 定義的（`enum Option<T> { None, Some(T) }`）。`?` 運算子、`match` 對它們的支援都是通用機制，不是為它們開的後門。理解這點你才會敢自己定義類似的 enum，而不是把它們當黑箱。

2. **濫用 `_ =>` 通配吃掉窮盡性檢查**：新手看到 `E0004` 常直接加個 `_ => {}` 了事。這會讓你失去「加新 variant 時編譯器提醒你」的最大價值——以後有人加了 variant，這個 match 照編過、悄悄走 `_`，bug 溜進 production。只在真的「其餘全部同樣處理」時才用 `_`，能列 variant 就列。

3. **以為 enum 的 discriminant 值是你能假設的**：`repr(Rust)` 下 discriminant 的實際數值、大小、佈局都是**未指定**的，且開了 niche optimization 後可能根本沒有獨立的 discriminant byte（如 `Option<&T>`）。想把 enum 傳給 C 或依賴具體數值，必須標 `#[repr(...)]`（Ch 15）。別 `transmute` 一個 `repr(Rust)` enum 去讀它的「tag」，那是 UB。

4. **`match` guard 不參與窮盡性分析**：`n if n < 0 => …` 這種 arm，編譯器**不會**因為你「邏輯上涵蓋了負數」就認為窮盡。guard 對編譯器是黑箱，它只看 pattern 部分。所以帶 guard 的 arm 幾乎總是需要一個沒有 guard 的 fallback arm，否則 `E0004`。

5. **`let else` 的 `else` 分支忘了發散**：`let Ok(n) = x else { println!("bad"); };`——這編不過，因為 `else` 必須 `return`/`break`/`continue`/`panic!`，不能正常往下走。理由：如果 pattern 沒匹配又不退出，`n` 就沒被綁定，後面用到 `n` 就是用未初始化的值。編譯器強制你在 `else` 裡結束這條路徑。

## 進階：再往深一層

**enum 可以遞迴，但需要間接層。** 想用 enum 定義一棵樹或鏈結串列：

```rust
enum List {
    Cons(i32, Box<List>),   // Box 提供間接層，打破無限大小
    Nil,
}
use List::*;
fn main() {
    let list = Cons(1, Box::new(Cons(2, Box::new(Nil))));
    let mut cur = &list;
    while let Cons(v, next) = cur {
        print!("{v} ");
        cur = next;
    }
    println!();
}
```

真跑：

```
1 2 
```

如果 `Cons` 直接寫成 `Cons(i32, List)` 而非 `Box<List>`，編譯器會報 `E0072`「recursive type has infinite size」——因為要算 `List` 的大小得先知道 `List` 的大小，無限遞迴。`Box` 是一根指標（固定 8 bytes），打破這個循環。這對應 C 裡「struct 裡放指向自己的指標」（`struct node { int v; struct node *next; }`），只是 Rust 用 `Box` 明確表達所有權（Ch 16）。

**手動指定 discriminant。** 像 C enum 一樣，你可以給無資料 enum 指定數值，這在寫 protocol / FFI 常量時有用：

```rust
#[repr(u8)]
enum Opcode {
    Nop = 0x00,
    Load = 0x10,
    Store = 0x20,
}
fn main() {
    println!("{}", Opcode::Store as u8);   // as 轉換取得 discriminant 值
}
```

真跑：

```
32
```

`#[repr(u8)]` 讓 discriminant 確定是 1 byte 且數值可控，`Opcode::Store as u8` 取出 `0x20`（十進位 32）。這時 enum 才是「數值可依賴的」——沒標 `repr` 的 enum 別這樣用。

**面試常問**：「Rust 的 enum 和 C 的 enum 有什麼不同？」——C 的 `enum` 只是「有名字的整數常數集合」，不能攜帶資料；Rust 的 `enum` 是完整的 sum type，每個 variant 可帶不同型別的資料，本質是「型別安全的 tagged union」。C 要表達帶資料的變體只能手動 `union` + tag，而且沒有窮盡性保護。

## 動手練習

1. **重現 C 的 tagged union bug 再看 Rust 擋掉**：把本章開頭的 `struct Value`（`union { int i; float f; }`）用 `gcc` 編出來，設 `tag=INT; u.i=42` 然後讀 `u.f`，確認印出垃圾且無警告。再用 Rust `enum Value { Int(i32), Flt(f32) }` + `match` 寫等價邏輯，體會「你根本無法在 match 到 `Int` 時去讀 `f32`」。

2. **觸發 E0004**：定義 `enum TrafficLight { Red, Yellow, Green }`，寫一個 `match` 只處理 `Red` 和 `Green`，跑一次看 `E0004` 指名 `Yellow` not covered。然後**不要**用 `_` 補，改成明確加 `Yellow` arm——體會為什麼列出來比通配好。

3. **驗證 niche optimization**：`println!` 出 `size_of::<Option<&u8>>()`、`size_of::<&u8>()`、`size_of::<Option<Box<u8>>>()`、`size_of::<Option<i32>>()`（最後這個沒 niche，應該比 `i32` 大）。並排看哪些 `Option` 是零額外成本、哪些不是，想清楚差別在「payload 有沒有可借的 niche」。

## 本章重點整理

- **struct 是 product type**（同時擁有所有欄位），三形態：具名 / tuple / unit；**enum 是 sum type**（擇一擁有），對應 C 的「`union` + 手動 tag」但把 tag 綁進型別、由編譯器強制。
- `Option<T>`/`Result<T,E>` 不是編譯器魔法，就是標準函式庫裡的普通 enum；它們把 C 的 magic value（`NULL`/`errno`）換成型別強制的「可能沒有 / 可能失敗」。
- `match` 的窮盡性檢查（`E0004`）保證你處理每個 variant，加新 variant 時逼所有 match 更新——C 的 `switch` 沒有這個保護。慎用 `_` 通配。
- pattern 是通用機制：解構、範圍、`@` 綁定、guard；`if let`/`while let`/`let else` 是 match 的輕量入口，`let else` 的 `else` 必須發散。
- enum 佈局 = discriminant + 最大 variant（`Shape` 真跑 24 bytes）；帶「保證非零」指標的 `Option`（`Option<&T>`/`Option<Box<T>>`）靠 niche optimization 和裸指標同大小（真跑 8 bytes），零額外成本（細節 Ch 15）。

## 自我檢核

- [ ] 面試問「Rust enum 和 C enum 差在哪」，能答出「Rust enum 是能帶資料的 sum type / 型別安全 tagged union，C enum 只是整數常數集」。
- [ ] 不看筆記，能解釋為什麼 `Option<&i32>` 和 `&i32` 同大小（niche：借用非法的 null bit pattern 當 None），而 `Option<u8>` 不行。
- [ ] 能說出 `match` 窮盡性檢查在「加新 variant」時帶來的具體維護價值，以及為什麼濫用 `_` 會毀掉它。
- [ ] 知道 `let else` 的 `else` 分支為什麼**必須**發散，而不是可以正常往下走。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 6「Enums and Pattern Matching」+ Ch 18「Patterns and Matching」** — （[doc.rust-lang.org/book/ch06-00-enums.html](https://doc.rust-lang.org/book/ch06-00-enums.html)）
  - **讀哪裡**：Ch 6 全部（enum、`Option`、`match`、`if let`）；Ch 18 講 pattern 的完整能力（解構、`@`、guard、範圍）。
  - **學到什麼**：本章內容的官方對應版；The Book 用 `Coin`/`IpAddr` 例子講得更慢，卡住時回來補。
  - **前提**：懂前面章節的 ownership（`match` 會 move 或借用被匹配的值）。

- **《The Rust Reference》「Type layout」的 enum 小節** — （[doc.rust-lang.org/reference/type-layout.html](https://doc.rust-lang.org/reference/type-layout.html)）
  - **讀哪裡**：「Representations」與「Discriminant elision on `Option`-like enums」兩段，正是本章 niche optimization 的權威定義。
  - **學到什麼**：`repr(Rust)` 為何不保證佈局、`Option`-like enum 的 discriminant 何時被省略——本章 `Option<&T>` = 8 的正式規則。
  - **前提**：懂本章 enum 佈局的直覺；Reference 是形式化描述，配本章的 `size_of` 實測讀更好懂。Ch 15 會深入。

### 技術文章

- **「Peeking inside a Rust enum」** — Amos / fasterthanlime（[fasterthanli.me/articles/peeking-inside-a-rust-enum](https://fasterthanli.me/articles/peeking-inside-a-rust-enum)）
  - **這篇說什麼**：用實際的記憶體 dump 一步步拆開 Rust enum 的 bit 佈局，包含 discriminant 和 niche 怎麼擺，補足本章「畫圖說明」沒真的 dump 記憶體那一塊。
  - **讀哪裡**：從頭讀，重點是他 dump `Option` 和多 variant enum 記憶體那幾段。
  - **為什麼值得讀**：Amos 是社群公認把底層佈局講最具體的作者之一，每個宣稱都配真實記憶體內容，不靠腦補。

### 官方參考

- **《Rust by Example》「Enums」與「match」** — （[doc.rust-lang.org/rust-by-example/custom_types/enum.html](https://doc.rust-lang.org/rust-by-example/custom_types/enum.html)）
  - **讀哪裡**：enum 章的 `use`/`C-like`/`testcase_linked_list` 小節，以及 match/flow-of-control 章的 `guard`/`binding`（`@`）小節。
  - **學到什麼**：每個語法點都有可跑的最小例子，適合當本章 pattern 各種寫法的速查表。
  - **前提**：懂本章基本 enum 與 match；這裡是補充範例密度，不重複講原理。

pattern matching 和 enum 讓你能安全地表達「多種形態的資料」並拆開它們，但每個 enum 的行為還是綁在具體型別上。下一章的 **trait** 才是 Rust 抽象的核心——讓不同型別共享同一套行為介面，這是後面泛型、trait object、迭代器全部的地基。

→ [Ch 09 Trait：Rust 的抽象核心](./09-traits.md)
