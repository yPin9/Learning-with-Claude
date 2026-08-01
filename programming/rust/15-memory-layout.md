# Ch 15 — 記憶體佈局：repr 與 niche optimization

> **目標**：徹底搞懂 Rust 型別在記憶體裡怎麼擺——預設的 `repr(Rust)` 為什麼可以**重排欄位**（跟 C 保證順序相反）、`repr(C)`/`repr(packed)`/`repr(transparent)`/`repr(u8)` 各改了什麼；用 `size_of`/`align_of`/`offset_of!` 把佈局實測出來；理解 **niche optimization** 這個 Rust 獨有的把戲——`Option<&T>` 為什麼跟 `&T` 一樣大（把 `None` 塞進指標的無效值 null），對照 C 手動用 sentinel（NULL、-1）省一個 flag 的老招；最後看 ZST（zero-sized type）與 `PhantomData` 的零佔用佈局。這章是後面 unsafe、FFI、手刻資料結構的地基——你不知道東西怎麼擺，就不可能安全地繞過編譯器。

> **環境**：Rust 以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）。所有 `size_of`/`align_of`/`offset_of!` 輸出、`transmute` 出來的 bit pattern，都是本機真跑，非推測。`offset_of!` 在 Rust 1.77 穩定；本章全部用 stable。

## 為什麼需要這個？

你在 C 裡對記憶體佈局有絕對的掌控權，也有絕對的責任。寫下：

```c
struct S { uint8_t a; uint64_t b; uint8_t c; };
```

你**知道** `a` 在 offset 0、`b` 在 offset 8（前面塞了 7 bytes padding 讓 `b` 對齊到 8）、`c` 在 offset 16，整個 struct 24 bytes。C 標準保證欄位**照宣告順序**擺，你也知道編譯器會插 padding 對齊。這是 C 的契約：佈局可預測，代價是你得自己排好欄位順序才不會浪費空間——把 `a`、`c` 兩個 `uint8_t` 塞在一起可以省 8 bytes，這種手工 packing 是 C 老手的肌肉記憶。

Rust 預設**不給你這個保證**。預設佈局 `repr(Rust)` 下，編譯器可以**自由重排欄位**，你不能假設任何順序。這聽起來像是失控，其實是一個交易：你放棄「知道欄位順序」，換來「編譯器自動幫你排到最省 padding」。而當你真的需要固定佈局（跟 C 講話、mmap 一塊硬體暫存器、序列化）——你用 `repr(C)` 把契約要回來。

更進一步，Rust 編譯器知道每個型別的**有效值範圍**（`bool` 只有 0/1 合法、`&T` 永遠非 null、`NonZeroU32` 永遠非 0），於是它能玩一個 C 玩不到的把戲：把 enum 的 tag 藏進這些「無效值」裡，讓 `Option<&T>` 不用多花一個 byte 記「是 Some 還是 None」。這叫 **niche optimization**，是 Rust 型別系統餵給佈局優化器的免費資訊。

這章就是把這兩件事——「佈局誰說了算」和「編譯器怎麼利用型別資訊省空間」——挖到眼見為憑。

## 先建立直覺

先給兩張心智圖像。

**第一張：欄位重排。** 想像三個大小不一的盒子要塞進一個櫃子，櫃子有對齊格線（大盒子必須放在格線上）。照你給的順序放（小、大、小），會在小盒子後面留一大堆空格好讓大盒子對齊；但如果允許**重排**（大、小、小），空格就少很多。C 強迫你照原順序放，Rust 預設讓編譯器挑最省空間的順序：

```
   repr(C)：照宣告順序，被迫留 padding
   ┌──┬───────┬────────────────┬──┬───────┐
   │a │ pad×7 │       b        │c │ pad×7 │   sizeof = 24
   └──┴───────┴────────────────┴──┴───────┘
     0        8                16

   repr(Rust)：編譯器重排 (b, a, c)，padding 變少
   ┌────────────────┬──┬──┬─────┐
   │       b        │a │c │ pad │              sizeof = 16
   └────────────────┴──┴──┴─────┘
     0               8  9 10
```

**第二張：niche（縫隙）。** 一個 `&T` 佔 8 bytes，可以表示 2^64 個 bit pattern，但其中一個——全 0（null）——對 `&T` 來說是**非法**的（Rust 的參考永遠非 null）。這個非法值就是一個「縫隙（niche）」。`Option<&T>` 要多記一件事：「這到底是 Some 還是 None」。與其多花一個 byte 存這個 flag，編譯器直接把 `None` **編碼成那個非法的 null**——反正合法的 `Some(&x)` 永遠不會是 null，不會撞。於是 `Option<&T>` 跟 `&T` 一樣是 8 bytes，`None` 就是 `0x0`。

```
   &T 的 bit 空間（8 bytes）：
   0x0 ─────────────────────────────── 0xFFFF...
    ▲
    │ 這格 &T 用不到（null 非法）
    └── niche：Option 拿來當 None
        其餘所有值 = Some(那個指標)
```

這正是你在 C 裡手動做的事：一個「指標或沒有」的欄位，你不會另外開一個 `bool has_value`，你直接約定「NULL 代表沒有」。Rust 把這個手工約定變成型別系統自動做的優化。

> 如果你對「型別的有效值範圍」還沒感覺，記住這句：`bool`、`char`、參考、`NonZero*` 這些型別的合法 bit pattern **不是**填滿整個 bit 空間的，剩下的縫隙就是 niche 的來源。

## 佈局三工具：size_of / align_of / offset_of!

先把量測工具擺出來。這三個是你之後 debug 佈局問題的全部家當：

- `size_of::<T>()`：型別佔幾 bytes（含尾端 padding）。
- `align_of::<T>()`：型別的對齊需求，位址必須是這個值的倍數。
- `offset_of!(T, field)`：某欄位在 struct 內的 byte offset（Rust 1.77 穩定，對應 C 的 `offsetof` 巨集）。

先看預設 `repr(Rust)` 真的重排欄位，而且比手排的 `repr(C)` 小。**同樣五個欄位、同樣順序**，只差一個 `#[repr(C)]`：

```rust
use std::mem::{size_of, align_of};

// 手排的糟糕順序：大小交錯造成 padding
#[repr(C)]
struct BadC { a: u8, b: u64, c: u8, d: u64, e: u8 }

// repr(Rust) 讓編譯器自由重排
struct GoodRust { a: u8, b: u64, c: u8, d: u64, e: u8 }

fn main() {
    println!("BadC(repr C)      size={} align={}", size_of::<BadC>(), align_of::<BadC>());
    println!("GoodRust(reorder) size={} align={}", size_of::<GoodRust>(), align_of::<GoodRust>());
}
```

真跑：

```
BadC(repr C)      size=40 align=8
GoodRust(reorder) size=24 align=8
```

**40 vs 24**。一樣的欄位，`repr(C)` 因為被迫照 `u8, u64, u8, u64, u8` 的順序擺，每個 `u8` 後面都得 padding 7 bytes 好讓下一個 `u64` 對齊到 8——三個 `u8` 各浪費 7 bytes，尾端再補齊。`repr(Rust)` 把兩個 `u64` 排前面、三個 `u8` 擠一起，padding 剩不到幾 bytes。這 16 bytes 的差距在你有一百萬個這種 struct 時就是 16 MB。

用 `offset_of!` 把 `repr(C)` 的浪費看清楚——offset 全是 8 的倍數，中間都是洞：

```rust
use std::mem::{size_of, align_of, offset_of};

#[repr(C)]
struct BadC { a: u8, b: u64, c: u8, d: u64, e: u8 }

fn main() {
    println!("offset a={}", offset_of!(BadC, a));
    println!("offset b={}", offset_of!(BadC, b));
    println!("offset c={}", offset_of!(BadC, c));
    println!("offset d={}", offset_of!(BadC, d));
    println!("offset e={}", offset_of!(BadC, e));
    println!("size={} align={}", size_of::<BadC>(), align_of::<BadC>());
}
```

真跑：

```
offset a=0
offset b=8
offset c=16
offset d=24
offset e=32
size=40 align=8
```

`a` 在 0，但 `b` 跳到 8（0..8 中間 `a` 只用 1 byte，7 bytes 是 padding），`c` 在 16、`d` 在 24、`e` 在 32、尾端補到 40。這張 offset 表跟你在 C 裡對同樣 struct 跑 `offsetof` 會拿到一模一樣的結果——因為 `repr(C)` 的字面意思就是「照 C 的規則佈局」。

**重點**：`repr(Rust)` 的重排順序是**不保證的**，編譯器版本之間可能不同。你不能對 `repr(Rust)` 的 struct 用 `offset_of!` 去假設某個固定 offset 然後拿去做 FFI——要固定佈局就得標 `repr(C)`。這是後面 Ch 19 FFI 的鐵律。

## repr 家族：把佈局契約要回來

`repr` 屬性告訴編譯器「用哪套佈局規則」。逐一實測，同樣三個欄位 `{ a: u8, b: u64, c: u8 }`：

```rust
use std::mem::{size_of, align_of};

struct Plain      { a: u8, b: u64, c: u8 }
#[repr(C)]      struct AsC      { a: u8, b: u64, c: u8 }
#[repr(packed)] struct Packed   { a: u8, b: u64, c: u8 }
#[repr(transparent)] struct Wrap(u64);

fn main() {
    println!("Plain(repr Rust)  size={} align={}", size_of::<Plain>(), align_of::<Plain>());
    println!("AsC(repr C)       size={} align={}", size_of::<AsC>(), align_of::<AsC>());
    println!("Packed            size={} align={}", size_of::<Packed>(), align_of::<Packed>());
    println!("Wrap(transparent) size={} align={}", size_of::<Wrap>(), align_of::<Wrap>());
    println!("bare u64          size={} align={}", size_of::<u64>(), align_of::<u64>());
}
```

真跑：

```
Plain(repr Rust)  size=16 align=8
AsC(repr C)       size=24 align=8
Packed            size=10 align=1
Wrap(transparent) size=8 align=8
bare u64          size=8 align=8
```

逐個拆解：

- **`repr(Rust)`（Plain）= 16**：編譯器重排成 `b, a, c`，兩個 `u8` 擠一起，只在尾端補一點 padding。最省。
- **`repr(C)`（AsC）= 24**：照 C 規則，`a`(0) + pad(1..8) + `b`(8..16) + `c`(16) + pad(17..24)。跟 C 完全一致，可以拿去跟 C struct 對接。
- **`repr(packed)`（Packed）= 10、align=1**：把**所有 padding 拔掉**，`a`(0) + `b`(1..9) + `c`(9)，整個 struct 對齊降到 1。省空間到極致，但代價致命：`b` 現在放在 offset 1，是**未對齊**的。這是下面「packed 的風險」要講的重點。
- **`repr(transparent)`（Wrap）= 8**：這是「單一非零大小欄位的 newtype」專用。`Wrap(u64)` 保證跟裡面的 `u64` 佈局**完全一樣**——size 8、align 8，跟 bare `u64` 一模一樣。用途：你想要一個型別上的 newtype（型別安全）但又要跟裡面的東西 ABI 相容（FFI 時當同一個東西傳）。C 沒有這個概念，最接近的是 `typedef`，但 `typedef` 沒有型別安全。

**`repr(packed)` 的風險——不是嚇唬人。** 在 packed struct 裡，`b` 這個 `u64` 放在未對齊的位址。x86-64 上未對齊讀寫「大多能跑但慢」，但在 ARM 等架構上會直接 fault，而且不管哪個平台，**建立一個指向未對齊欄位的參考本身就是 UB**（即使你從沒解引用它）。Rust 直接在編譯期擋下來：

```rust
#[repr(packed)]
struct Packed { a: u8, b: u64 }

fn main() {
    let p = Packed { a: 1, b: 2 };
    let r = &p.b;          // 取未對齊欄位的參考 -> 錯誤
    println!("{}", r);
}
```

真跑（編不過，照抄）：

```
error[E0793]: reference to field of packed struct is unaligned
 --> l8.rs:6:13
  |
6 |     let r = &p.b;          // 取未對齊欄位的參考 -> 錯誤
  |             ^^^^
  |
  = note: this struct is 1-byte aligned, but the type of this field may require higher alignment
  = note: creating a misaligned reference is undefined behavior (even if that reference is never dereferenced)
  = help: copy the field contents to a local variable, or replace the reference with a raw pointer and use `read_unaligned`/`write_unaligned`
```

看那句 `creating a misaligned reference is undefined behavior (even if that reference is never dereferenced)`——這正是 C 裡的隱形殺手（在 packed struct 上取 `&field` 傳給函式），C 編譯器不會攔你，Rust 直接編不過，逼你用 `read_unaligned`/`write_unaligned`（Ch 18 會用到）。這是「Rust 把 C 的隱性 UB 顯性化」的又一例。

**`repr(u8)` 給 enum 定 discriminant。** enum 的「現在是哪個變體」需要一個 tag（discriminant）。預設 Rust 挑最小夠用的整數；你可以用 `repr(u8)` 強制它是 `u8`，並手動指定每個變體的值——這在跟 C enum 對接、或做二進位協定時是硬需求：

```rust
use std::mem::size_of;

#[repr(u8)]
enum Color { Red = 1, Green = 2, Blue = 255 }

enum E3 { A, B, C }        // 預設：三個無資料變體

#[repr(C)]
enum CEnum { X, Y }

fn main() {
    println!("repr(u8) Color size={}", size_of::<Color>());
    println!("default E3 size={}", size_of::<E3>());
    println!("repr(C) CEnum size={}", size_of::<CEnum>());
    println!("Blue as u8 = {}", Color::Blue as u8);
}
```

真跑：

```
repr(u8) Color size=1
default E3 size=1
repr(C) CEnum size=4
Blue as u8 = 255
```

`repr(u8)` 讓 discriminant 就是 1 byte，`Color::Blue as u8` 拿回你指定的 `255`——這就是 C 的 `enum { RED=1, ... }` 對應物，可以直接 cast 成整數丟進 protocol。`repr(C)` 的 enum 則用 C 的 enum ABI（在這平台是 `int`，4 bytes）。預設 `repr(Rust)` 的三變體 enum 只要 1 byte（一個 `u8` 就能編三個值）。

## 底層機制：niche optimization，眼見為憑

現在挖 Rust 佈局最漂亮的一招。編譯器知道某些型別**用不到全部的 bit pattern**——這些沒用到的值叫 **niche**。當這種型別被包進 enum（最常見是 `Option<T>`）時，enum 的 discriminant 不另外找地方存，直接**借用那個 niche 值**。

一口氣量一整排，看誰有 niche、誰沒有：

```rust
use std::mem::size_of;
use std::ptr::NonNull;
use std::num::NonZeroU32;

fn main() {
    println!("&i32              = {}", size_of::<&i32>());
    println!("Option<&i32>      = {}", size_of::<Option<&i32>>());
    println!("*const i32        = {}", size_of::<*const i32>());
    println!("Option<*const i32>= {}   (裸指標無 niche)", size_of::<Option<*const i32>>());
    println!("NonNull<i32>      = {}", size_of::<NonNull<i32>>());
    println!("Option<NonNull>   = {}", size_of::<Option<NonNull<i32>>>());
    println!("bool              = {}", size_of::<bool>());
    println!("Option<bool>      = {}", size_of::<Option<bool>>());
    println!("NonZeroU32        = {}", size_of::<NonZeroU32>());
    println!("Option<NonZeroU32>= {}", size_of::<Option<NonZeroU32>>());
    println!("u32               = {}", size_of::<u32>());
    println!("Option<u32>       = {}   (u32 無 niche，要多一個 tag)", size_of::<Option<u32>>());
}
```

真跑：

```
&i32              = 8
Option<&i32>      = 8
*const i32        = 8
Option<*const i32>= 16   (裸指標無 niche)
NonNull<i32>      = 8
Option<NonNull>   = 8
bool              = 1
Option<bool>      = 1
NonZeroU32        = 4
Option<NonZeroU32>= 4
u32               = 4
Option<u32>       = 8   (u32 無 niche，要多一個 tag)
```

這張表把整個機制講完了：

- **`Option<&i32>` == `&i32` == 8**：`&T` 非 null，null 是 niche，`None` 塞進 null。零額外空間。
- **`Option<*const i32>` == 16，比 `*const i32`(8) 大一倍**：裸指標**可以**是 null（合法值），所以沒有 niche，`Option` 只能老實多加一個 tag 欄位，再對齊到 16。這一行是整個機制的反證——同樣是指標，`&T` 有 niche、`*const T` 沒有，差別就在「null 合不合法」。
- **`Option<NonNull>`、`Option<NonZeroU32>` 都不變大**：`NonNull`（非 null 指標）和 `NonZeroU32`（非 0）刻意保留了一個 niche 值給你用。這就是它們存在的意義之一。
- **`Option<bool>` == `bool` == 1**：`bool` 只有 0/1 合法，256 個值裡有 254 個 niche，`None` 隨便挑一個（實際上編譯器用 2）。
- **`Option<u32>` == 8，比 `u32`(4) 大**：`u32` 的每個 bit pattern 都是合法值，**沒有 niche**，只能多加 tag，對齊後從 4 漲到 8。

**證明 `None` 的 bit pattern 真的是 null。** 用 `transmute`（Ch 18 主角，這裡先用）把 `Option<&i32>` 直接看成一個 `usize`：

```rust
use std::mem::transmute;

fn main() {
    let some: Option<&i32> = Some(&42);
    let none: Option<&i32> = None;
    unsafe {
        let some_bits: usize = transmute(some);
        let none_bits: usize = transmute(none);
        println!("Some(&42) bits = 0x{:x}", some_bits);
        println!("None      bits = 0x{:x}   (null，就是塞進 niche 的無效值)", none_bits);
    }
}
```

真跑：

```
Some(&42) bits = 0x5a1af8607554
None      bits = 0x0   (null，就是塞進 niche 的無效值)
```

`None` 的 bit pattern 就是 `0x0`——編譯器把 `None` 編碼成 null 指標。`Some(&42)` 是那個真實位址。這正是 C 程式設計師手動做的事：一個回傳 `T*` 的函式，用 `NULL` 表示「找不到」，呼叫端 `if (p != NULL)`。Rust 的 `Option<&T>` 在**佈局上跟這個一模一樣**，但在**型別上**強迫你處理 `None` 分支（不能忘記 null check）。這是「零成本抽象」的字面體現：`Option<&T>` 的安全性檢查是型別系統給的，執行期佈局跟你手寫的 nullable 指標零差別。

**多層 niche：縫隙會用完。** niche 有幾個值，就能白嫖幾層 enum。`bool` 有 254 個 niche，指標只有 1 個（null）——差別在這裡爆出來：

```rust
use std::mem::size_of;
fn main() {
    println!("bool                 = {}", size_of::<bool>());
    println!("Option<bool>         = {}", size_of::<Option<bool>>());
    println!("Option<Option<bool>> = {}", size_of::<Option<Option<bool>>>());
    println!("O<O<O<bool>>>        = {}", size_of::<Option<Option<Option<bool>>>>());
    println!("Option<&i32>         = {}", size_of::<Option<&i32>>());
    println!("Option<Option<&i32>> = {}", size_of::<Option<Option<&i32>>>());
}
```

真跑：

```
bool                 = 1
Option<bool>         = 1
Option<Option<bool>> = 1
O<O<O<bool>>>        = 1
Option<&i32>         = 8
Option<Option<&i32>> = 16
```

`bool` 有 254 個 niche，包三層 `Option` 還是 1 byte（每層拿走一個 niche 當自己的 `None`，254 個綽綽有餘）。但 `&i32` 只有**一個** niche（null），第一層 `Option` 就把它用光了，`Option<Option<&i32>>` 沒 niche 可借，只能退回加 tag——從 8 漲到 16。這個對比很少人講清楚：niche 不是「有沒有」的二元，是「有幾個」的計數。

## ZST 與 PhantomData：佔用零 bytes 的型別

Rust 允許**零大小型別（ZST，zero-sized type）**：`()`（unit）、空 struct、`[T; 0]`、`PhantomData<T>`。它們 `size_of` 是 0，但仍是有型別、有位址概念的東西。C 沒有真正的 ZST（C 標準裡 struct 至少 1 byte，空 struct 是 GNU 擴充）。

```rust
use std::mem::{size_of, align_of};
use std::marker::PhantomData;

struct Empty;                        // 空 struct = ZST
struct WithPhantom { ptr: *const u8, _marker: PhantomData<u32> }
struct JustPtr { ptr: *const u8 }

fn main() {
    println!("()                size={} align={}", size_of::<()>(), align_of::<()>());
    println!("Empty             size={} align={}", size_of::<Empty>(), align_of::<Empty>());
    println!("PhantomData<u32>  size={} align={}", size_of::<PhantomData<u32>>(), align_of::<PhantomData<u32>>());
    println!("[u8; 0]           size={}", size_of::<[u8; 0]>());
    println!("WithPhantom       size={}", size_of::<WithPhantom>());
    println!("JustPtr           size={}   (PhantomData 不佔空間，兩者相同)", size_of::<JustPtr>());
    let v: Vec<()> = vec![(); 1000];
    println!("Vec<()> len={} 但沒有 heap 配置", v.len());
}
```

真跑：

```
()                size=0 align=1
Empty             size=0 align=1
PhantomData<u32>  size=0 align=1
[u8; 0]           size=0
WithPhantom       size=8
JustPtr           size=8   (PhantomData 不佔空間，兩者相同)
Vec<()> len=1000 但沒有 heap 配置
```

`()`、`Empty`、`PhantomData<u32>`、`[u8; 0]` 全都是 0 bytes。關鍵觀察：`WithPhantom`（有一個指標 + 一個 `PhantomData<u32>`）跟 `JustPtr`（只有指標）**一樣大 8 bytes**——`PhantomData` 不佔任何空間。那它幹嘛存在？

`PhantomData<T>` 是給編譯器看的**佈局零成本標記**：它讓一個 struct 在**型別層面**「假裝擁有一個 `T`」，好讓 ownership、variance（Ch 5）、drop check 這些分析正確運作，但在**記憶體層面**完全不佔空間。最典型的用途：你手刻一個 `MyVec<T>`，內部只存一個 `*mut T`（裸指標，不帶 ownership 語意），但你要告訴編譯器「這個 struct 邏輯上擁有一堆 `T`」——加一個 `PhantomData<T>` 欄位就對了（Ch 21 手刻 Vec 會用到）。這是 C 完全沒有的東西：型別資訊與記憶體佈局解耦。

`Vec<()>` 那行更有意思：一千個 `()`，`len` 是 1000，但**完全不配置 heap**——因為每個元素 0 bytes，一千個還是 0 bytes，`Vec` 聰明到不去 alloc。這在 Rust 裡是把「集合當計數器/set」用的常見手法（`HashSet<K>` 內部就是 `HashMap<K, ()>`，value 用 ZST 不佔空間）。

## 對比與取捨

| repr | 欄位順序 | padding | 對齊 | 主要用途 |
|---|---|---|---|---|
| `repr(Rust)`（預設） | 編譯器自由重排 | 自動最小化 | 正常 | 一般 Rust 內部型別，最省空間 |
| `repr(C)` | 照宣告順序 | 照 C 規則 | 正常 | FFI、mmap、序列化、需固定佈局 |
| `repr(packed)` | 照宣告順序 | **全拔掉** | 降為 1（或指定） | 極省空間 / wire format；未對齊 access 風險 |
| `repr(transparent)` | 單一非零欄位 | 同內層 | 同內層 | newtype 要 ABI 相容（FFI） |
| `repr(u8)` 等 | enum discriminant | — | — | 固定 tag 型別，跟 C enum / 協定對接 |

| 手法 | C 怎麼做 | Rust 怎麼做 |
|---|---|---|
| 省 struct 空間 | 手動重排欄位（老手肌肉記憶） | 預設自動重排；不用管 |
| nullable 指標省 flag | 約定 NULL = 沒有 | `Option<&T>` niche，型別強制 null check |
| 固定佈局對外 | 這是預設 | 顯式 `#[repr(C)]` |
| 型別標記不佔空間 | 無（要嘛佔 1 byte 要嘛沒有） | `PhantomData<T>`（0 bytes） |

取捨的核心：**Rust 預設優化空間、放棄可預測性；C 預設可預測、放棄自動優化。** 兩邊都對，只是預設值不同。當你需要 C 的可預測性，`repr(C)` 一個屬性就要回來——這比 C「想要自動優化只能自己排」的方向好走，因為顯式標註 `repr(C)` 的地方剛好就是需要小心的地方（FFI 邊界），強迫你意識到「這裡佈局是契約」。

## 踩雷集錦

1. **假設 `repr(Rust)` 的欄位順序 / offset**：預設佈局的欄位順序**不保證**，編譯器版本間可能變。你不能對 `repr(Rust)` struct 用 `offset_of!` 拿到一個 offset 然後 hard-code 進 FFI 或序列化——那是定時炸彈。要固定佈局就標 `repr(C)`。正確認識：`repr(Rust)` = 「我不在乎順序，你（編譯器）幫我排最省的」，一旦你開始在乎順序，就不該用它。

2. **以為 `Option<T>` 一定比 `T` 大**：很多人（尤其 C 背景）直覺 `Option` 一定多一個 flag。錯——有 niche 的型別（`&T`、`Box<T>`、`NonNull`、`NonZero*`）的 `Option` **零額外開銷**。只有無 niche 的型別（`u32`、裸指標、填滿整個 bit 空間的型別）的 `Option` 才會變大。判準：問「這型別有沒有一個非法 bit pattern」。

3. **`repr(packed)` 上取 `&field` 或直接讀寫**：packed struct 的欄位可能未對齊，取參考是 UB（Rust 編譯期擋，見上面的錯誤）。要存取先 copy 到 local 變數，或用裸指標 + `read_unaligned`/`write_unaligned`（Ch 18）。別以為「x86 未對齊也能跑」就沒事——參考的 UB 跟能不能跑無關，是語言層面的約定，優化器會據此假設對齊而做出你意想不到的事。

4. **`repr(transparent)` 用在多欄位 struct**：`repr(transparent)` 只能用在「恰好一個非零大小欄位」的型別（其餘欄位必須是 ZST）。想給一個雙欄位 struct 標 `transparent` 會編不過。它的用途很窄：newtype wrapper 要跟內層 ABI 相同。

5. **忘記 `PhantomData` 是給編譯器看的、不是給執行期**：`PhantomData<T>` 不佔空間、不產生任何執行期行為，你不能靠它「持有」一個真的 `T`。它純粹是型別/variance/drop-check 的標記。把它當成能存資料的欄位是誤解。

## 進階：再往深一層

**niche 不只 `Option` 用。** 任何「一個帶 niche 的型別 + 少數幾個無資料變體」的 enum 都吃 niche：`Result<&T, ()>`（真跑是 8 bytes，`Err(())` 塞進 null）、你自訂的 `enum { Ref(&T), Nothing }`。編譯器對整個 enum 家族做 niche 分析，不是 `Option` 特權。但變體一多、niche 不夠用，就退回加 tag——例如三個無資料變體加一個 `&T`，一個 niche（null）不夠編兩個額外狀態，就會變大。

**`size_of` 含尾端 padding，`offset_of!` 給的是「欄位起點」。** 一個常見誤區：`offset_of!(T, last_field) + size_of::<field_ty>()` 不一定等於 `size_of::<T>()`，因為 struct 尾端可能有 padding（讓陣列裡下一個元素對齊）。要精確理解佈局，size、align、offset 三個都要看，不能只看一個推另一個。

**面試/audit 常問**：「`Option<Box<T>>` 為什麼零成本？」——`Box<T>` 是非 null 指標（有 niche），`None` 編碼成 null，所以 `Option<Box<T>>` 跟 `Box<T>` 一樣 8 bytes。這讓 Rust 的「nullable owned 指標」跟 C++ 的 `T*`（可能 null 的裸擁有指標）佈局相同，但 Rust 強制你處理 `None`、且 `Box` 自動 drop。這個問題背後是整個 niche 機制，答得出來代表你真的懂佈局，不是背 API。

```rust
use std::mem::size_of;
fn main() {
    // Box 也 non-null，一樣有 niche
    println!("Box<i32>         = {}", size_of::<Box<i32>>());
    println!("Option<Box<i32>> = {}", size_of::<Option<Box<i32>>>());
}
```

真跑：`Box<i32> = 8`、`Option<Box<i32>> = 8`——證實。

## 動手練習

1. **重排量化**：定義一個 `struct { a: u8, b: u32, c: u8, d: u64, e: u16 }`，一次 `repr(C)` 一次 `repr(Rust)`，用 `size_of` 比大小、用 `offset_of!` 印出 `repr(C)` 版每個欄位的 offset，手算 padding 花在哪。再手動把 `repr(C)` 版的欄位重排成「大到小」，看能不能追平 `repr(Rust)` 的大小。

2. **niche 獵人**：對這些型別跑 `size_of::<T>()` 和 `size_of::<Option<T>>()`，預測哪些相等（有 niche）、哪些變大（無 niche），再跑驗證：`&T`、`*const T`、`bool`、`char`、`u8`、`NonZeroU8`、`&mut T`、`fn()`（函式指標）。`char` 和 `fn()` 的結果會不會出乎你意料？想想為什麼。

3. **niche 用完**：寫一個三變體 enum `enum E<'a> { Ref(&'a i32), A, B }`，量 `size_of`，跟 `Option<&i32>`（兩變體）比。解釋為什麼三變體含一個 `&` 的 enum 比兩變體大——一個 niche（null）只能編一個額外狀態。

## 本章重點整理

- **`repr(Rust)`（預設）自由重排欄位**，自動最小化 padding（真跑：同樣五欄位比 `repr(C)` 小 16 bytes）；順序不保證，要固定佈局用 `repr(C)`。
- **repr 家族各司其職**：`repr(C)`（照 C 規則，FFI 用）、`repr(packed)`（拔 padding，未對齊風險，取 `&field` 是 UB 被編譯期擋）、`repr(transparent)`（newtype ABI 相容）、`repr(u8)`（固定 enum discriminant）。
- **niche optimization = 把 enum tag 塞進型別的無效值**：`Option<&T>` == `&T`（`None` = null，真跑 bit pattern 是 `0x0`）；無 niche 的型別（`u32`、裸指標）的 `Option` 才變大。niche 有幾個值就能白嫖幾層 enum（`bool` 254 個，指標 1 個）。
- **對照 C 的 sentinel**：`Option<&T>` 佈局跟 C 手動用 NULL 表示「沒有」一模一樣，但型別系統強制你 null check——安全性零執行期成本。
- **ZST（`()`、空 struct、`PhantomData<T>`）佔 0 bytes**：`PhantomData` 是「有型別、無記憶體」的標記，給 ownership/variance/drop-check 用，是後面手刻 unsafe 抽象的必備工具。

## 自我檢核

- [ ] 面試問「`Option<&T>` 為什麼跟 `&T` 一樣大」，能一口氣講清 niche optimization，並說出哪些型別**沒有** niche（`Option` 會變大）。
- [ ] 不看筆記，能解釋 `repr(Rust)` 和 `repr(C)` 的差別，以及什麼情況**必須**用 `repr(C)`。
- [ ] 能說出 `repr(packed)` 的具體風險，以及為什麼取 packed 欄位的參考是 UB。
- [ ] 知道 `PhantomData<T>` 佔幾 bytes、它存在的理由，以及它跟「真的存一個 `T`」的差別。
- [ ] 能用 `size_of`/`align_of`/`offset_of!` 三個工具實測一個 struct 的佈局，並解釋 padding 花在哪。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 參考

- **《The Rust Reference》「Type Layout」** — （[doc.rust-lang.org/reference/type-layout.html](https://doc.rust-lang.org/reference/type-layout.html)）
  - **讀哪裡**：整頁，尤其「Representations」小節（`repr(Rust)`/`C`/`packed`/`transparent`/primitive repr 的形式化規則）與「Niche」的說明。這是佈局行為的最終仲裁。
  - **學到什麼**：本章實測的每個 `repr` 背後的精確規則、`repr(Rust)` 到底允許編譯器做什麼（以及不保證什麼）。
  - **前提**：懂本章的 size/align/offset 三概念；Reference 是條文，配本章例子讀才不會乾。

- **《The Rustonomicon》「Data Layout」章（repr(Rust) / Exotically Sized Types / repr(C)）** — （[doc.rust-lang.org/nomicon/data.html](https://doc.rust-lang.org/nomicon/data.html)）
  - **讀哪裡**：「repr(Rust)」講欄位重排、「Exotically Sized Types」講 ZST 與 DST（動態大小型別）、「Alternative representations」講各種 repr。
  - **學到什麼**：本章 ZST 那節的完整版，含 DST（`[T]`、`str`、`dyn Trait`）的胖指標佈局——是 Ch 6 slice 和 Ch 11 trait object 的佈局根源。
  - **前提**：這是 unsafe Rust 的權威文件，假設你懂基本佈局；本章正是它的入門鋪墊。

### 技術文章

- **《The Rust Performance Book》「Type sizes」** — Nicholas Nethercote（[nnethercote.github.io/perf-book/type-sizes.html](https://nnethercote.github.io/perf-book/type-sizes.html)）
  - **這篇說什麼**：從效能角度講怎麼量型別大小（`-Zprint-type-sizes` nightly flag）、怎麼縮小 enum、niche 在實戰裡怎麼幫你省記憶體。作者是 rustc 效能團隊核心，這本 perf-book 是官方性質的效能手冊。
  - **讀哪裡**：「Type sizes」整節，特別是 `-Zprint-type-sizes` 的用法——那是本章 `size_of` 的加強版，會逐欄位印出佈局與 padding。
  - **為什麼值得讀**：本章教你「看」佈局，這篇教你「在真實 crate 裡找出佈局肥大的型別並瘦身」，是實戰接口。

### 官方 issue / 歷史

- **rust-lang/rust #46213「Niche-filling enum optimization」討論串** — （[github.com/rust-lang/rust/issues/46213](https://github.com/rust-lang/rust/issues/46213)）
  - **讀哪裡**：開頭的問題描述與後續對「哪些 enum 能吃 niche」的實作討論。
  - **學到什麼**：niche optimization 從「只有 `Option<&T>` 特例」演進到「通用 niche-filling」的過程——本章講的多層 niche、任意 enum 吃 niche，就是這個通用化的成果。
  - **前提**：懂本章 niche 概念；這是想知道「編譯器內部怎麼決定用哪個 niche」的深水區，選讀。

搞懂了型別怎麼擺、指標大小是多少、niche 怎麼省空間，下一章我們把這些佈局知識用在**智慧指標**上——`Box`/`Rc`/`Arc`/`RefCell` 內部到底長什麼樣，為什麼 `Box<T>` 就是一個裸指標、`Rc` 的計數藏在 heap 的哪裡、`Option<Box<T>>` 為什麼零成本（答案就是這章的 niche）。

→ [Ch 16 智慧指標底層：Box/Rc/Arc/RefCell](./16-smart-pointers.md)
