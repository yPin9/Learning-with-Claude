# Ch 6 — Slice、str 與 String：胖指標佈局

> **目標**：看穿 `&[T]` 和 `&str` 是「胖指標（fat pointer）」= (ptr, len) 共 16 bytes，畫得出 `String`（ptr, len, cap，24 bytes）的記憶體佈局，理解為什麼 UTF-8 讓你不能 `s[0]`、切在字元中間會 panic，以及 slice 為什麼受 borrow checker 管。全程對照 C 的 `char*`（無長度、靠 `\0`）與 C++ 的 `std::string` / `std::string_view`。

> **環境**：`rustc 1.97.1`，WSL2 / x86-64 Linux。`size_of` 數值是 64-bit target 的結果，32-bit 上指標與 `usize` 都是 4 bytes，所有 16/24 會減半。

前兩章講的是 lifetime 這種「抽象規則」。這章反過來，我們貼著記憶體看：Rust 的字串與切片在記憶體裡到底長什麼樣。做過 pwn/RE 的你對「指標就是 8 bytes」有很深的直覺——這章第一件事就是打破它：**Rust 有些引用是 16 bytes。**

---

## 為什麼需要這個？

C 的字串是一場災難的根源，你很清楚：

```c
char *s = "hello";
size_t n = strlen(s);   // O(n)：從頭掃到 '\0'
```

C 的 `char*` **只帶位址，不帶長度**。長度靠結尾的 `\0` 哨兵標記，於是：

- 每次要長度都得 `strlen`，O(n)。
- 字串中間不能有 `\0`（binary data 就 GG）。
- buffer overflow 的溫床：`strcpy` 不知道目標多大，寫爆為止。

C++ 的 `std::string` 好一點（自帶 size），`std::string_view` 是「借用一段字元」的輕量視圖。Rust 的 `&str` / `&[T]` 在概念上最接近 `string_view` / `span`——但 Rust 把「這個視圖借了誰、能活多久」也編進型別（上一章的 lifetime）。這章聚焦**佈局**，安全性那半邊由 borrow checker 補上。

---

## 先建立直覺

普通引用 `&i32` 就是一個位址，8 bytes。但 `&[i32]`（slice，切片）不一樣——切片是「一段連續元素」，你光有起點位址不知道它多長。所以 Rust 的 slice 引用**多帶一個長度欄位**：

```
&i32   （thin pointer，瘦指標）:
  ┌──────────┐
  │  ptr     │  8 bytes
  └──────────┘

&[i32]  （fat pointer，胖指標）:
  ┌──────────┬──────────┐
  │  ptr     │  len      │  8 + 8 = 16 bytes
  └──────────┴──────────┘
     │
     ▼
  ┌────┬────┬────┬────┐
  │ 1  │ 2  │ 3  │ 4  │   實際資料在別處
  └────┴────┴────┴────┘
```

「胖」就是胖在這個 `len`。`&str` 一樣是 (ptr, len)——len 是**位元組數**，不是字元數（後面會看到這個區別會咬人）。

---

## 一、slice 是胖指標：`size_of` 證明

先讓數字說話。`std::mem::size_of::<T>()` 回傳型別在記憶體裡佔幾個 byte：

```rust
use std::mem::size_of;

fn main() {
    println!("&i32       = {}", size_of::<&i32>());
    println!("&[i32]     = {}", size_of::<&[i32]>());
    println!("&str       = {}", size_of::<&str>());
    println!("*const i32 = {}", size_of::<*const i32>());
    println!("Box<[i32]> = {}", size_of::<Box<[i32]>>());
    println!("String     = {}", size_of::<String>());
    println!("Vec<i32>   = {}", size_of::<Vec<i32>>());
    println!("Box<i32>   = {}", size_of::<Box<i32>>());

    // 證明 &str 的兩個欄位就是 (ptr, len)
    let s: &str = "héllo"; // é 是 2 bytes，總長 6 bytes
    println!("s.len() (bytes) = {}", s.len());
    let ptr = s.as_ptr();
    let len = s.len();
    println!("ptr = {:p}, len = {}", ptr, len);
}
```

真跑：

```
$ rustc sizes.rs -o sizes && ./sizes
&i32       = 8
&[i32]     = 16
&str       = 16
*const i32 = 8
Box<[i32]> = 16
String     = 24
Vec<i32>   = 24
Box<i32>   = 8
s.len() (bytes) = 6
ptr = 0x58afcee1fca0, len = 6
```

逐行看：

- `&i32` = 8：普通引用，一個位址。
- `&[i32]` = 16、`&str` = 16：**胖指標**，(ptr, len)。
- `*const i32` = 8：raw pointer 到 sized 型別，瘦的。
- `Box<[i32]>` = 16：連 `Box` 指到 slice 也是胖的——「胖」是被指向的型別（`[i32]` 是 unsized）決定的，不是引用種類。
- `Box<i32>` = 8：指到 sized 型別，瘦的。

最後那個 `"héllo"`：`é` 在 UTF-8 是 2 bytes（`0xC3 0xA9`），所以 5 個字元的字串 `len()` 是 6——**len 數的是 byte，不是字元**。記住這件事，第三節整節都在講它的後果。

> C 對照：C 的 `char*` 就是那個瘦的 8 bytes，長度得靠 `strlen` O(n) 掃。Rust 的 `&str` 用 8 bytes 換來 O(1) 拿長度、字串中間可含任意 byte（沒有 `\0` 哨兵）。這是空間換時間 + 安全的典型取捨。

---

## 二、String vs &str vs str：owned / borrowed / unsized

這三個名字長得像，關係卻是理解 Rust 字串的核心。先給一張圖：

```
String（owned，可增長，在 heap）:
  ┌──────────┬──────────┬──────────┐
  │  ptr     │  len     │  cap     │   24 bytes（stack 上）
  └──────────┴──────────┴──────────┘
     │
     ▼  heap
  ┌────┬────┬────┬────┬────┬ ─ ─ ─ ┐
  │ 'h'│ 'e'│ 'l'│ 'l'│ 'o'│  (未用) │  cap 個 byte，len 個已用
  └────┴────┴────┴────┴────┴ ─ ─ ─ ┘

&str（borrowed，胖指標，指向某段已存在的 bytes）:
  ┌──────────┬──────────┐
  │  ptr     │  len     │   16 bytes
  └──────────┴──────────┘
     │
     ▼  可指向 heap（借 String）、.rodata（借字面量）、stack…

str（unsized，「一段 UTF-8 bytes」本身，沒有固定大小）:
  你永遠不會直接持有一個 str 變數；只透過 &str / Box<str> / String 摸它
```

三者關係，一句話各講清楚：

- **`String`**：**擁有（owned）** 一段 heap 上的 UTF-8 bytes，可增長。三欄位 (ptr, len, cap)，24 bytes（見上面真跑輸出）。等價於 C++ 的 `std::string`。
- **`&str`**：**借用（borrowed）** 一段已存在的 UTF-8 bytes 的胖指標視圖，16 bytes，不擁有、不能增長。等價於 C++ 的 `std::string_view`。
- **`str`**：**unsized（無固定大小）** 型別，代表「一段 UTF-8 bytes」這個東西本身。你不能有 `let x: str`——編譯器不知道要在 stack 上留幾個 byte。你只能透過指標（`&str`、`Box<str>`）或擁有它的容器（`String`）碰它。

`String` 為什麼要 `cap`（capacity）？因為它能增長。`push_str` 時如果 len 到頂，就重新配置更大的 heap buffer——這跟 `Vec` 一模一樣。事實上上面輸出 `String` 和 `Vec<i32>` 都是 24 bytes 不是巧合：**`String` 內部就是一個 `Vec<u8>`，額外保證內容是合法 UTF-8**。

```rust
// String 就是「保證是 UTF-8 的 Vec<u8>」——這個等價關係在 std 原始碼裡是字面上的
// pub struct String { vec: Vec<u8> }
```

> C 對照：C 沒有這種「擁有 vs 借用」的型別區分。`char *` 到底是「我 malloc 的、我要負責 free」還是「別人給我看的、我別亂動」，型別上看不出來，全靠註解和慣例。這正是 double-free / UAF 的根源之一。Rust 用 `String`（owned，drop 時 free）vs `&str`（borrowed，drop 時什麼都不做）把這個區分編進型別。

一個把三者串起來的例子：

```rust
fn takes_str(s: &str) -> usize { s.len() }  // 借用，什麼都不擁有

fn main() {
    let owned: String = String::from("hello");  // owned，heap
    let literal: &str = "world";                // 借用 .rodata 的字面量
    let borrowed: &str = &owned;                // 借用 owned 的 heap bytes（自動 Deref）

    println!("{}", takes_str(&owned));   // &String 自動轉 &str
    println!("{}", takes_str(literal));
    println!("{}", takes_str(borrowed));
}
```

`&owned`（型別 `&String`）能傳給要 `&str` 的函式，是因為 `String` 實作了 `Deref<Target = str>`，編譯器自動插入解引用。這叫 deref coercion，[Ch 12 核心 trait](./12-core-traits.md) 會細講。

---

## 三、UTF-8：為什麼你不能 `s[0]`，切在字元邊界外會 panic

Rust 的 `str` 保證內容是**合法 UTF-8**。UTF-8 是變長編碼：ASCII 字元 1 byte，其他字元 2–4 bytes。這個設計決定了幾個「反直覺」的行為。

### 你不能 `s[0]`

C 裡 `s[0]` 拿第一個 byte，天經地義。Rust 直接不給你 index：

```rust
fn main() {
    let s = String::from("hello");
    let c = s[0]; // 不能對 str index
    println!("{}", c);
}
```

真跑：

```
error[E0277]: the type `str` cannot be indexed by `{integer}`
 --> str_index.rs:3:15
  |
3 |     let c = s[0]; // 不能對 str index
  |               ^ string indices are ranges of `usize`
  |
  = help: the trait `SliceIndex<str>` is not implemented for `{integer}`
  = note: you can use `.chars().nth()` or `.bytes().nth()`
          for more information, see chapter 8 in The Book: <https://doc.rust-lang.org/book/ch08-02-strings.html#indexing-into-strings>
```

為什麼禁？因為 `s[0]` 有歧義：你要第一個 **byte** 還是第一個 **字元**？對 UTF-8 這兩者可能不同。而且如果 `s[i]` 給你第 `i` 個 byte，你可能拿到一個字元的「半個 body」，那是垃圾。Rust 的選擇是：**乾脆禁掉單點 index，逼你明說要 `bytes()` 還是 `chars()`**。錯誤訊息本身就把兩個替代方案（`.chars().nth()` / `.bytes().nth()`）告訴你了。

### bytes vs chars

```rust
fn main() {
    let s = "café";
    println!("bytes len = {}", s.len());
    println!("chars count = {}", s.chars().count());

    print!("bytes: ");
    for b in s.bytes() {
        print!("{:02x} ", b);
    }
    println!();

    print!("chars: ");
    for c in s.chars() {
        print!("{} ", c);
    }
    println!();

    // 正確的字元邊界切法
    if let Some((i, _)) = s.char_indices().nth(3) {
        println!("4th char starts at byte {}", i);
        println!("s[..{}] = {}", i, &s[..i]);
    }
}
```

真跑：

```
$ rustc utf8_iter.rs -o ui && ./ui
bytes len = 5
chars count = 4
bytes: 63 61 66 c3 a9 
chars: c a f é 
4th char starts at byte 3
s[..3] = caf
```

`"café"` 有 4 個字元但 5 個 byte（`é` = `0xC3 0xA9`）。`bytes()` 給你原始位元組，`chars()` 給你解碼後的 Unicode scalar values。`char_indices()` 給你「(byte 位移, 字元)」配對——這是你要「切在字元邊界」時的正確工具。

### 切在字元中間 → runtime panic

range index（`&s[a..b]`）是允許的——**但 `a`、`b` 必須落在字元邊界上**，否則 runtime panic：

```rust
fn main() {
    let s = "café"; // c a f é ; é = U+00E9 = 2 bytes (0xC3 0xA9)，總長 5 bytes
    println!("len = {}", s.len());
    // 切在 byte 4 = é 的中間 → panic
    let bad = &s[0..4];
    println!("{}", bad);
}
```

真跑：

```
$ rustc char_boundary.rs -o cb && ./cb
len = 5

thread 'main' (236550) panicked at char_boundary.rs:5:17:
end byte index 4 is not a char boundary; it is inside 'é' (bytes 3..5 of string)
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

`é` 佔 byte 3..5，你切 `[0..4]` 剛好砍在它中間——Rust 在 runtime 檢查邊界並 panic，訊息精確到「你切進了 `é`（bytes 3..5）」。這是刻意的：與其讓你拿到半個字元的垃圾 bytes（C/C++ 的 `string_view` 會沉默地給你），不如當場 panic。要安全切，先用 `char_indices()` 找邊界，或用 `s.is_char_boundary(i)` 檢查。

> **為什麼不編譯期擋？** 因為 `a`、`b` 通常是 runtime 才知道的變數，編譯器沒法靜態驗證。這是 Rust 少數選擇 runtime panic 而非編譯期拒絕的地方——安全性保住了（不會拿到壞資料），代價是這類 bug 要跑到才發現。

---

## 四、slice 是借用：受 borrow checker 管

胖指標 `&[T]` / `&str` 本質是**引用**，所以上一章那套借用規則全部適用。切一段 slice = 借用整個容器：

```rust
fn main() {
    let mut v = vec![1, 2, 3, 4, 5];
    let slice = &v[1..3]; // 不可變借用整個 v
    v.push(6);            // 想同時可變借用 v → 衝突
    println!("{:?} {:?}", slice, v);
}
```

真跑：

```
error[E0502]: cannot borrow `v` as mutable because it is also borrowed as immutable
 --> slice_borrow.rs:4:5
  |
3 |     let slice = &v[1..3]; // 不可變借用整個 v
  |                  - immutable borrow occurs here
4 |     v.push(6);            // 想同時可變借用 v → 衝突
  |     ^^^^^^^^^ mutable borrow occurs here
5 |     println!("{:?} {:?}", slice, v);
  |                           ----- immutable borrow later used here
```

這正是 Rust 從根本擋掉 **iterator invalidation** 的機制。在 C++ 裡：

```cpp
std::vector<int> v = {1,2,3,4,5};
int* p = &v[1];
v.push_back(6);   // 可能觸發 realloc，p 變懸空
std::cout << *p;  // UAF，UB
```

`push_back` 可能讓 `vector` realloc 到新的 heap 位址，舊的 `p` 就懸空了——編譯器不吭聲，跑起來可能 crash 可能讀到垃圾。Rust 在編譯期就拒絕：只要 `slice` 還借著 `v`，你就不能 `push`（`push` 需要 `&mut v`）。**這是 [Ch 3 借用規則](./03-borrowing-references.md) 的「不可變借用存在時不能可變借用」直接套到 slice 上的結果。**

---

## 對比與取捨

| 型別 | 大小(64-bit) | 擁有? | 可增長? | C/C++ 對應 |
|---|---|---|---|---|
| `&i32` | 8 | 否 | — | `const int*` |
| `&[T]` | 16 (ptr,len) | 否 | 否 | `std::span<const T>` |
| `&str` | 16 (ptr,len) | 否 | 否 | `std::string_view` |
| `String` | 24 (ptr,len,cap) | 是 | 是 | `std::string` |
| `Vec<T>` | 24 (ptr,len,cap) | 是 | 是 | `std::vector<T>` |
| `Box<str>` | 16 (ptr,len) | 是 | 否 | （擁有但固定長的字串） |

---

## 踩雷集錦

1. **以為 `&str` 是 8 bytes**：做 FFI 或 `transmute` 時這會咬死你。`&str` 是 16 bytes 的胖指標，不能直接當 `char*` 傳給 C——要傳 `.as_ptr()`（拿瘦指標）並另外傳 `.len()`，或用 `CString`（帶 `\0` 結尾）。[Ch 19 FFI](./19-ffi.md) 會細談。

2. **`s.len()` 是 byte 數不是字元數**：`"café".len()` 是 5 不是 4。要字元數用 `s.chars().count()`（注意這是 O(n)，要掃過整串解碼）。更麻煩的是「使用者感知的字元」（grapheme cluster，如 emoji + skin tone modifier）連 `chars().count()` 都不對，那要 `unicode-segmentation` crate。

3. **`&s[i..j]` 切在字元中間會 runtime panic，不是編譯錯**：這是少數逃過編譯期檢查的地方。處理非 ASCII 輸入時務必先 `char_indices()` 或 `is_char_boundary()` 確認邊界，否則使用者打個中文你的程式就 panic。

4. **把 slice 借用當成「複製了一份」**：`let s = &v[1..3]` 沒有複製任何元素，`s` 借著 `v`。只要 `s` 活著，`v` 就被凍住（不能 `&mut`）。這跟 C++ 的 `string_view` 一樣是視圖，但 Rust 用 borrow checker 保證視圖不會比來源活得久。

5. **`String` 和 `&str` 到處要轉來轉去很煩，於是全用 `String`**：反模式。函式參數收 `&str`（能同時接受 `String` 和 `&str`、字面量），回傳擁有權時才用 `String`。全用 `String` 會逼呼叫端到處 `.clone()`，多一堆無謂的 heap 配置。

---

## 進階：再往深一層

**胖指標的第二個欄位不一定是 len**。slice 的胖指標帶 len，但 **trait object**（`&dyn Trait`）的胖指標第二欄帶的是 **vtable 指標**（指向該具體型別的方法表）。兩者都是 16 bytes，但語意不同。這是 [Ch 11 Trait Object 與動態分派](./11-trait-objects-dispatch.md) 的主題——到時你會發現 `size_of::<&dyn Trait>()` 也是 16。

**`String` 的 `cap` 增長策略**：`push` 觸發 realloc 時，Rust 的 `Vec`（`String` 內部）通常把容量**加倍**（amortized O(1) 攤還）。你可以用 `String::with_capacity(n)` 預先配置避免多次 realloc，或 `s.capacity()` 觀察。這跟 C++ `std::vector` 的成長策略同理，[Ch 21 手刻 Vec](./21-unsafe-abstractions.md) 會親手實作這套。

**`&str` 的 `as_ptr()` 指向哪**：字面量 `"abc"` 的 bytes 在 binary 的 `.rodata`（唯讀），所以你 `objdump -s -j .rodata your_binary` 真的找得到你的字串字面量——這對逆向 Rust binary 是重要線索（[Ch 33 逆向 Rust binary](./33-reversing-rust-binary.md)）。而 `String` 的 bytes 在 heap。

---

## 動手練習

1. 把第一節的 `size_of` 程式加一行印 `size_of::<&dyn std::fmt::Debug>()`，猜它是幾，再跑驗證（提示：trait object 也是胖指標）。

2. 寫一個函式 `fn safe_prefix(s: &str, n: usize) -> &str`，回傳「前 `n` 個**字元**」（不是 byte）而且絕不 panic。用 `char_indices()`。測 `"café"` 取前 3 個字元應得 `"caf"`，取前 10 個應得整串。

3. 故意觸發 char boundary panic：拿一個含中文的字串（如 `"你好"`，每字 3 bytes），切 `&s[0..1]`，看 panic 訊息怎麼告訴你切進了哪個字。

---

## 本章重點整理

- `&[T]` 和 `&str` 是**胖指標** (ptr, len)，16 bytes；`&i32` 是瘦指標 8 bytes。「胖」由被指向的 unsized 型別決定。
- `String`（owned，24 bytes，ptr+len+cap，內部是 `Vec<u8>`）／ `&str`（borrowed 視圖，16 bytes）／ `str`（unsized 本體）三者是擁有/借用/無大小的關係。
- `str` 保證 UTF-8：`len()` 是 byte 數、不能 `s[0]`（E0277）、切在字元邊界外 runtime panic。用 `bytes()`/`chars()`/`char_indices()` 明確操作。
- slice 是借用，受 borrow checker 管——這從根本擋掉 C++ 的 iterator invalidation（realloc 懸空）。

## 自我檢核

- [ ] 不看筆記，能畫出 `String`、`&str`、`&[i32]` 三者的記憶體佈局，並說出各佔幾 bytes、為什麼。
- [ ] 有人問你「Rust 為什麼不能 `s[0]`」，你能用 UTF-8 變長編碼解釋，而不只是說「Rust 不給」。
- [ ] 能說出 `&str` 對應 C++ 的哪個型別、`String` 對應哪個，以及 Rust 多了什麼保證。
- [ ] 能解釋為什麼 `let s = &v[1..3]; v.push(6);` 編不過，並連到 C++ 的什麼 bug。

## 延伸閱讀

### 官方文件 / Spec

- **[The Rust Reference — Types: Slice / str / Trait objects](https://doc.rust-lang.org/reference/types.html)**
  - **讀哪裡**：「Slice types」「Textual types (str)」「Trait objects」三小節——講清楚哪些型別是 DST（dynamically sized type，即 unsized）、胖指標帶什麼。
  - **和本章的關聯**：本章講的「為什麼 `str` 不能直接持有」「胖指標第二欄是什麼」的權威定義在這裡。

- **[std::string::String 文件](https://doc.rust-lang.org/std/string/struct.String.html)** 與 **[std::primitive.str](https://doc.rust-lang.org/std/primitive.str.html)**
  - **讀哪裡**：`String` 頁開頭的「Representation」段直接畫了 (ptr, len, cap)；`str` 頁的 `is_char_boundary`、`char_indices`、`get` 方法。
  - **和本章的關聯**：本章第二、三節的所有方法在這裡有完整簽章與範例；要安全切字串時查 `get`（回傳 `Option`，不 panic 的版本）。

### 部落格 / 技術文章

- **[“Rust’s String Types, Explained”](https://blog.thoughtram.io/string-vs-str-in-rust/)** — thoughtram（本文常被引為 `String` vs `&str` 的入門解釋）
  - **這篇說什麼**：從「為什麼有這麼多字串型別」切入，補足本章沒展開的 `Cow<str>`、`OsString`、`CString` 全家族。
  - **讀哪裡**：整篇；重點在它把「什麼時候該用哪個」講得比官方文件白話。
  - **前提**：讀完本章的 owned/borrowed 區分再看，會更快。

### 書籍

- **《Programming Rust, 2nd ed.》— Blandy, Orendorff, Tindall（O'Reilly, 2021）**
  - **這本書的定位**：系統向、對 C++ 使用者友善，記憶體佈局章節紮實。
  - **讀哪幾章**：Ch 3「Fundamental Types」的 String/str 一節、Ch 17「Strings and Text」整章——後者把 UTF-8、grapheme cluster、正規化講得很完整，是本章第三節的延伸。

下一章我們把 borrow checker 拆開，看它在 MIR 上到底怎麼判斷借用衝突——你會理解為什麼有些「看起來安全」的 code 過不了，以及 NLL 和 Polonius 到底解決了什麼。

→ [Ch 7 borrow checker 底層：NLL 與 Polonius](./07-borrow-checker-internals.md)
