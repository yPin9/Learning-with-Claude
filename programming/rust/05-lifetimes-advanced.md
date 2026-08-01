# Ch 5 — Lifetime 進階：elision / HRTB / variance

> **目標**：搞懂為什麼多數函式不用寫 `'a`（elision 規則）、`'static` 兩種完全不同的意義、閉包吃引用回傳引用時撞到的 `for<'a>`（HRTB），以及本課最抽象的一節——variance（變異數）為什麼是記憶體安全的地基。學完你能讀懂 std 裡那些看起來像天書的 lifetime 標註。

> **環境**：`rustc 1.97.1`（stable）與 `rustc 1.99.0-nightly`，在 WSL2 / x86-64 Linux 上。variance 一節牽涉 unsound 反例，會用 nightly 對照。

上一章 [Ch 4 Lifetimes](./04-lifetimes.md) 我們把 lifetime 當成「借用的存活期標註」——你在函式簽章寫 `'a`，編譯器檢查引用不會活得比被借的東西久。那章你可能有個疑惑：**明明大部分函式我根本沒寫 `'a`，怎麼也過了？** 這章先回答這件事，再往上爬三層抽象。

這章分四塊，難度遞增：

1. **elision**：為什麼多數時候不用寫 `'a`（省得掉 vs 省不掉）。
2. **`'static`**：一個詞兩種意義，混淆會讓你看不懂錯誤訊息。
3. **HRTB `for<'a>`**：閉包吃引用回傳引用時會撞到的東西。
4. **variance**：本課最抽象的一節。給直覺為主，但這是 unsafe 之外 Rust 記憶體安全的另一根支柱。

---

## 為什麼需要這個？

C 沒有 lifetime 這層。你在 C 寫：

```c
char *first_word(char *s) { /* ... */ }
```

回傳的指標活多久？沒人知道。編譯器不管，全靠你腦子記著「這個指標指向 caller 傳進來的 buffer，caller 得保證 buffer 在我用完之前別 free」。記錯就是 use-after-free。

Rust 把「這個引用綁在哪個輸入的存活期上」寫進型別系統。問題是：**如果每個回傳引用的函式都要手寫 `'a`，程式碼會被 lifetime 標註淹沒**。Rust 早期（2015 之前）確實這樣，難用到勸退人。於是有了 **lifetime elision**——一套「絕大多數情況下編譯器能自己推出 `'a`」的規則，讓你只在真正歧義的地方才手寫。

而 `'static`、HRTB、variance 這三個，是當你開始寫泛型、寫回傳引用的閉包、把引用塞進容器時，一定會撞到的進階概念。不懂它們，`rustc` 的錯誤訊息會像外星文。

---

## 先建立直覺

三個進階概念，先各給一句心智圖像：

```
elision  ── 「編譯器有一套填空規則；能填出唯一答案時，你就不用寫」
'static  ── 兩個意思：(1) 引用活到程式結束  (2) 型別內部不含短命借用
HRTB     ── 「這個函式對『任何 lifetime』都成立」，不是對某個特定 'a
variance ── 「A 是 B 的子型別時，Wrapper<A> 是不是 Wrapper<B> 的子型別？」
```

最後一個 variance 你現在看不懂沒關係——那是這章末尾要花力氣建的直覺。先往下走。

---

## 一、Lifetime elision：為什麼你多數時候不用寫 `'a`

編譯器對「函式簽章裡沒寫的 lifetime」套三條規則去填。**這三條是機械式的，不是猜**——填得出唯一答案就過，填不出就要你手寫。

三條規則（來自 Rust Reference 的 lifetime elision 一節）：

1. **每個被省略的「輸入」引用參數，各給一個獨立的 lifetime。** `fn f(x: &T, y: &T)` 展開成 `fn f<'a, 'b>(x: &'a T, y: &'b T)`。
2. **如果剛好只有一個輸入 lifetime**（不管是不是省略的），它就是所有輸出引用的 lifetime。`fn f(x: &T) -> &U` 變成 `fn f<'a>(x: &'a T) -> &'a U`。
3. **如果有多個輸入 lifetime，但其中一個是 `&self` 或 `&mut self`**，那 `self` 的 lifetime 給所有輸出。這就是為什麼 method 幾乎不用寫 lifetime。

看一個三條規則都用到的例子：

```rust
// 三條 elision 規則都適用，不用寫 'a
fn first_word(s: &str) -> &str {
    match s.as_bytes().iter().position(|&b| b == b' ') {
        Some(i) => &s[..i],
        None => s,
    }
}

struct Parser<'a> {
    input: &'a str,
    pos: usize,
}

impl<'a> Parser<'a> {
    // 規則三：有 &self，輸出 lifetime 綁定 &self
    fn rest(&self) -> &str {
        &self.input[self.pos..]
    }
}

fn main() {
    println!("{}", first_word("hello world"));
    let p = Parser { input: "abcdef", pos: 3 };
    println!("{}", p.rest());
}
```

真跑：

```
$ rustc elide_ok.rs -o elide_ok && ./elide_ok
hello
def
```

`first_word` 只有一個輸入引用 `s`，規則二把它的 lifetime 給了輸出——編譯器知道回傳的 `&str` 借自 `s`。`rest` 有 `&self`，規則三把 `self` 的 lifetime 給輸出。你都沒寫 `'a`，但編譯器填出了唯一答案。

### 什麼時候 elision 失敗，逼你手寫

規則一給每個輸入獨立 lifetime。當有**兩個輸入引用、又要回傳引用**時，規則二不適用（不只一個輸入 lifetime）、規則三不適用（沒有 `self`）——編譯器不知道回傳的引用借自哪一個，只好報錯：

```rust
fn longest(x: &str, y: &str) -> &str {
    if x.len() > y.len() { x } else { y }
}
fn main() {
    println!("{}", longest("aa", "b"));
}
```

真跑（`rustc 1.97.1`）：

```
error[E0106]: missing lifetime specifier
 --> elide_fail.rs:1:33
  |
1 | fn longest(x: &str, y: &str) -> &str {
  |               ----     ----     ^ expected named lifetime parameter
  |
  = help: this function's return type contains a borrowed value, but the signature does not say whether it is borrowed from `x` or `y`
help: consider introducing a named lifetime parameter
  |
1 | fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
  |           ++++     ++          ++          ++
```

注意錯誤訊息說得很白：「不知道回傳值是借自 `x` 還是 `y`」。編譯器不猜，要你把 `x`、`y`、回傳值綁成同一個 `'a`。這正是設計哲學：**elision 只在答案唯一時生效；歧義時強迫你講清楚**，而不是隨便選一個可能錯的。

> C 對照：C 的 `char *longest(char *x, char *y)` 編得過，回傳指標的存活期完全不在型別裡。Rust 把這個「你腦中默認的假設」變成必須寫出來的簽章。

---

## 二、`'static` 的兩種意義

`'static` 是最容易被誤解的關鍵字，因為它在兩個位置意思**完全不同**。

### 意義一：`&'static T`——引用活到程式結束

`&'static str` 是「一個引用，它指向的東西活得跟整個程式一樣久」。字串字面量就是這種——它們躺在 binary 的唯讀資料段（`.rodata`），程式跑多久它們就在多久。

### 意義二：`T: 'static`（trait bound）——型別內部不含短命借用

當 `'static` 出現在**泛型 bound** `T: 'static`，它的意思是「型別 `T` 不含任何活得比 `'static` 短的借用」。**所有 owned 型別**（`String`、`Vec<u8>`、`i32`、任何不含引用欄位的 struct）都自動滿足 `T: 'static`——因為它們根本沒借別人的東西。

這個區別關鍵到值得一個會出事的例子：

```rust
use std::fmt::Debug;

// T: 'static 的意思是「T 內部不含活得比 'static 短的借用」，
// 不是「這個值一定活到程式結束」。owned 型別（String、Vec）全都滿足 T: 'static。
fn needs_static<T: 'static + Debug>(x: T) {
    println!("{:?}", x);
}

fn main() {
    // 'static 引用：字串字面量本體在唯讀資料段，活到程式結束
    let s: &'static str = "in .rodata";
    println!("{}", s);

    // String 是 owned，滿足 T: 'static，但它本身在 main 結束前就會被 drop
    let owned = String::from("owned, still 'static-bound");
    needs_static(owned); // move 進去，這裡就 drop 了 —— 證明 T: 'static ≠ 活到程式結束
}
```

真跑：

```
$ rustc static_two.rs -o s2 && ./s2
in .rodata
"owned, still 'static-bound"
```

`owned` 這個 `String` **不會**活到程式結束——它 move 進 `needs_static` 後在函式尾就被 drop 了。但它滿足 `T: 'static`，因為 `String` 內部沒借任何人的東西。這就是關鍵：`T: 'static` 是講「型別能不能活到 `'static`」，不是「這個值實際活了多久」。

反例——傳一個真的含短命借用的型別進去：

```rust
use std::fmt::Debug;
fn needs_static<T: 'static + Debug>(x: T) {
    println!("{:?}", x);
}
fn main() {
    let n = 42;
    let r = &n; // &n 的 lifetime 綁到 n，不是 'static
    needs_static(r); // T = &'a i32，'a 不是 'static → 拒絕
}
```

真跑：

```
error[E0597]: `n` does not live long enough
  --> static_fail.rs:9:13
   |
 8 |     let n = 42;
   |         - binding `n` declared here
 9 |     let r = &n; // &n 的 lifetime 綁到 n，不是 'static
   |             ^^ borrowed value does not live long enough
10 |     needs_static(r); // T = &'a i32，'a 不是 'static → 拒絕
   |     --------------- argument requires that `n` is borrowed for `'static`
11 | }
   | - `n` dropped here while still borrowed
   |
note: requirement that the value outlives `'static` introduced here
  --> static_fail.rs:3:20
   |
 3 | fn needs_static<T: 'static + Debug>(x: T) {
   |                    ^^^^^^^
```

`r` 的型別是 `&'a i32`，`'a` 綁在 `n` 上，而 `n` 在 `main` 結束前就 drop——所以 `&'a i32` **不滿足** `T: 'static`。

> **常見誤解**：「`T: 'static` 代表這個東西一輩子不會被釋放。」錯。`String` 滿足 `T: 'static` 但天天被 drop。正確理解：`T: 'static` 是「如果需要，這個型別**有能力**活到 `'static`，因為它不欠任何人（沒有借用會提早失效）」。這個 bound 常出現在 `thread::spawn`、`Box<dyn Any>`、跨執行緒傳遞的場景——因為那些場景無法保證短命借用的來源還活著。

---

## 三、HRTB：`for<'a>` higher-ranked trait bound

現在爬到第三層。先看你什麼時候會撞到它。

考慮一個函式，它吃一個閉包，這個閉包會被用來處理**函式內部自己產生的、lifetime 未知的引用**：

```rust
// 需要 HRTB：閉包吃一個「任意 lifetime」的引用，回傳同 lifetime 的引用。
// 呼叫端還沒決定引用活多久，所以 bound 必須是 for<'a>。
fn apply_to_both<F>(f: F)
where
    F: for<'a> Fn(&'a str) -> &'a str,
{
    let s1 = String::from("hello world");
    let s2 = String::from("foo");
    println!("{}", f(&s1));
    println!("{}", f(&s2));
}

fn main() {
    apply_to_both(|s| s.split(' ').next().unwrap_or(""));
}
```

真跑：

```
$ rustc hrtb.rs -o hrtb && ./hrtb
hello
foo
```

`for<'a> Fn(&'a str) -> &'a str` 讀作：「對**任何** lifetime `'a`，`f` 都能吃一個 `&'a str` 並回傳一個 `&'a str`」。這叫 **higher-ranked trait bound（HRTB，高階 trait 界限）**——「higher-ranked」是說 lifetime 的量詞（`for<'a>`，即「對所有 `'a`」）在 trait bound 內部，而不是在函式的泛型參數列表。

### 為什麼不能只寫一個 `'a`

你可能想：幹嘛這麼囉唆，寫 `fn apply_to_both<'a, F>(f: F) where F: Fn(&'a str) -> &'a str` 不行嗎？試試看：

```rust
fn apply_to_both<'a, F>(f: F)
where
    F: Fn(&'a str) -> &'a str,
{
    let s1 = String::from("hello world");
    println!("{}", f(&s1));
}
fn main() {
    apply_to_both(|s| s.split(' ').next().unwrap_or(""));
}
```

真跑：

```
error[E0597]: `s1` does not live long enough
 --> hrtb_fail.rs:7:22
  |
2 | fn apply_to_both<'a, F>(f: F)
  |                  -- lifetime `'a` defined here
...
6 |     let s1 = String::from("hello world");
  |         -- binding `s1` declared here
7 |     println!("{}", f(&s1));
  |                    --^^^-
  |                    | |
  |                    | borrowed value does not live long enough
  |                    argument requires that `s1` is borrowed for `'a`
8 | }
  | - `s1` dropped here while still borrowed
```

關鍵在：把 `'a` 放到函式的泛型參數，`'a` 就是**呼叫端**在呼叫時決定的一個**固定**值。但 `s1` 是函式**內部**才建立的，它的 lifetime 呼叫端根本不知道、也給不出來——`s1` 活得比呼叫端能提供的任何 `'a` 都短。`for<'a>` 把「對所有 `'a` 都成立」的責任放進 bound 內，於是函式內部才生的短命引用也涵蓋在內。

> **什麼時候你會自然撞到 HRTB？** 多數時候你不會手寫 `for<'a>`——因為 `Fn(&str) -> &str` 這種 closure trait bound 本身有 elision，編譯器自動幫你補成 `for<'a> Fn(&'a str) -> &'a str`。你會**看到** `for<'a>` 通常是在錯誤訊息裡，或是當你要把閉包存進 struct、或回傳 `impl Fn` 時。認得它、讀得懂就夠了。

---

## 四、Variance（變異數）：本課最抽象的一節

> **這是本課最抽象的一節。** 目標不是讓你能默寫 variance 表，而是給你一個直覺：為什麼 `&mut T` 對 `T` **不變（invariant）** 是記憶體安全的必要條件。第一次看不太懂很正常，做過 pwn 的人看 unsound 反例會比較有感。

### 先講「子型別」在 lifetime 上的意思

Rust 沒有 class 繼承，但在 lifetime 上有一種子型別關係：**如果 `'long` 至少活得跟 `'short` 一樣久，那 `&'long T` 是 `&'short T` 的子型別**。直覺：一個活很久的引用，可以安全地當成一個活得比較短的引用來用——你只是「少用了一點它的壽命」，不會出事。

`'static`（活到程式結束）是所有 lifetime 的子型別。所以 `&'static str` 可以塞進任何要 `&'a str` 的地方：

```rust
// &'a T 對 'a 協變：長 lifetime 的引用可當短 lifetime 用
fn print_it(s: &str) {
    println!("{}", s);
}
fn main() {
    let long: &'static str = "I live forever";
    // 'static 引用被當成較短的 'a 用 — 協變讓這行過
    print_it(long);
    {
        let owned = String::from("short-lived");
        let short: &str = &owned;
        print_it(short);
    }
}
```

真跑：

```
$ rustc variance_cov.rs -o vc && ./vc
I live forever
short-lived
```

`long` 是 `&'static str`，被當成 `print_it` 要的較短 `&str` 用，沒問題。這叫 **協變（covariant）**：`&'a T` 對 `'a` 是協變的——`'a` 越長越好用，長的可以退化成短的。

### 三種 variance

| variance | 意思 | 例子 |
|---|---|---|
| **協變 covariant** | 子型別關係「順著傳」：`'long <: 'short` ⇒ `&'long T <: &'short T` | `&'a T` 對 `'a`；`&'a T` 對 `T`；`Box<T>` 對 `T` |
| **逆變 contravariant** | 子型別關係「反著傳」 | 函式參數位置：`fn(&'short T)` 可當 `fn(&'long T)` 用 |
| **不變 invariant** | 沒有子型別關係，型別必須**完全一致** | `&'a mut T` 對 `T`；`Cell<T>`、`*mut T` 對 `T` |

逆變很罕見（只出現在函式參數位置），你讀得懂就好。真正救命的是**不變**。

### 為什麼 `&mut T` 對 `T` 必須不變——不變的話會 UAF

這是這節的核心。**假設** `&'a mut T` 對 `T` 是協變的（其實不是，我們來看假設它是會怎樣）。協變意味著：如果 `&'static str <: &'short str`，那 `&mut &'static str` 就能當成 `&mut &'short str` 用。

看這段——它想利用這個假設把一個短命引用偷渡進一個 `'static` 槽位：

```rust
// 如果 &mut T 對 T 是協變的，這段就會編過 —— 然後產生一個懸空引用。
// borrow checker 靠「&mut T 對 T 不變」擋掉它。看它怎麼罵。
fn evil<'a>(dst: &mut &'a str, src: &'a str) {
    *dst = src;
}

fn main() {
    let mut r: &'static str = "static";
    {
        let local = String::from("temporary");
        // 若允許把 &mut &'static str 當成 &mut &'a str（協變 T），
        // 就能把一個短命引用寫進 'static 槽位
        evil(&mut r, &local);
    }
    // local 已 drop，r 若指向它就是 UAF
    println!("{}", r);
}
```

推演一下這段**假如編過**會發生什麼：

1. `r` 型別是 `&'static str`，`&mut r` 型別是 `&mut &'static str`。
2. `evil` 要 `dst: &mut &'a str`。若 `&mut &'static str` 能協變成 `&mut &'a str`（`'a` = `local` 的短 lifetime），這行就過。
3. `evil` 內部 `*dst = src`，把指向 `local` 的短命引用寫進了 `r`。
4. 內層 block 結束，`local` 被 drop。
5. `println!("{}", r)` 讀 `r`——它現在指向已釋放的 `local`。**Use-after-free。**

Rust 靠「`&mut T` 對 `T` 不變」擋在第 2 步：`&mut &'static str` **不能**協變成 `&mut &'short str`，型別必須完全一致，於是 `'a` 被迫等於 `'static`，而 `&local` 給不出 `'static`——編譯器在這裡就拒絕。真跑：

```
error[E0597]: `local` does not live long enough
  --> variance_invar.rs:13:22
   |
 8 |     let mut r: &'static str = "static";
   |                ------------ type annotation requires that `local` is borrowed for `'static`
10 |         let local = String::from("temporary");
   |             ----- binding `local` declared here
...
13 |         evil(&mut r, &local);
   |                      ^^^^^^ borrowed value does not live long enough
14 |     }
   |     - `local` dropped here while still borrowed
```

錯誤訊息直接點出：`r` 是 `&'static`，逼得 `local` 得借出 `'static`，但它給不出。**「不變」的直覺就是：能透過 `&mut` 往裡「寫」的位置，讀寫兩個方向都得成立，任何一邊放寬子型別關係都會開一個洞。** 只讀（`&T`）可以協變是因為你不能往裡寫，塞不進短命的東西；一旦可寫（`&mut T`），協變就等於允許你把短命引用寫進長命槽位，必然懸空。

> C++ 對照：C++ 完全沒有這層靜態保證。`std::string_view` 就是個永遠協變、可隨意賦值的胖指標——你可以輕易讓一個 `string_view` 指向已析構的 `std::string`，編譯器一聲不吭，跑起來就是 UAF。Rust 把 variance 這套規則編進型別系統，用編譯期錯誤換掉這類 runtime 崩潰。這也是為什麼標準庫作者寫 unsafe 抽象（如手刻 `Vec`）時，必須親手用 `PhantomData` 標對 variance——標錯就會製造出上面那種 unsound 的洞。這點 [Ch 21 手刻 unsafe 抽象](./21-unsafe-abstractions.md) 會實作。

---

## 對比與取捨

| 概念 | 你要做的事 | 沒有它會怎樣 |
|---|---|---|
| elision | 多數時候什麼都不做 | 每個回傳引用的函式都要手寫 `'a`，程式碼淹沒在標註裡 |
| `&'static T` | 用於全域常數、字串字面量 | — |
| `T: 'static` | 跨執行緒 / `dyn Any` / 存進長命容器時會要求 | 無法表達「這型別不欠短命借用」 |
| HRTB `for<'a>` | 通常編譯器自動補；手寫用於存閉包進 struct | 吃「內部產生的引用」的閉包無法表達 |
| variance | 幾乎不用手管；寫 unsafe 抽象時用 `PhantomData` 標 | 標錯 → unsound，開 UAF 洞 |

---

## 踩雷集錦

1. **「`T: 'static` 代表這個值永遠不釋放」**：錯。`String: 'static` 但天天被 drop。`T: 'static` 講的是「型別內部不含短命借用」，不是「值的實際壽命」。混淆這點會讓你看不懂為什麼 `thread::spawn` 收了你的 `String` 卻不抱怨。

2. **「elision 失敗是編譯器不夠聰明」**：不是。多輸入引用回傳引用時，答案本來就歧義（借自哪個？），編譯器**拒絕替你猜**是刻意的安全設計。要你手寫 `'a` 是逼你講清楚借用關係，不是它偷懶。

3. **`for<'a>` 和 `<'a>` 位置不同意思差很多**：`fn f<'a>(...)` 的 `'a` 是呼叫端固定的一個值；`for<'a> Fn(...)` 是「對所有 `'a` 都成立」。把該用 HRTB 的地方寫成普通泛型 lifetime，會撞上「內部引用活不夠久」的錯誤（見上面 `hrtb_fail` 那段真實錯誤）。

4. **以為 `&mut T` 也能像 `&T` 一樣「長的當短的用」**：不行，`&mut T` 對 `T` 不變。這不是限制，是防 UAF 的必要條件。你若在手刻 unsafe 抽象時直接用 `*mut T`（它對 `T` 協變）當內部指標又沒用 `PhantomData` 修正，就可能不小心做出 unsound 的型別。

5. **把 variance 當成「要背的規則」**：日常寫 safe Rust 你**不需要**主動想 variance——編譯器自動推。它只在兩種場合浮出水面：讀懂某些奇怪的 lifetime 錯誤、以及寫 unsafe 抽象時。認得它、知道「`&mut` 不變是為了防 UAF」這個直覺就夠了。

---

## 進階：再往深一層

**variance 是怎麼被「計算」出來的？** 編譯器對每個型別參數，看它出現在哪些位置：出現在「只讀」位置（如 `&'a T` 的 `T`）→ 協變貢獻；出現在「函式參數」位置 → 逆變貢獻；同時出現在讀和寫（如 `&mut T` 的 `T`，或 `Cell<T>`）→ 不變。一個型別參數只要在任何位置是不變的，整體就是不變。這是 Rustonomicon「Subtyping and Variance」一節的內容。

**HRTB 的 `for<'a>` 目前只能量詞化 lifetime**：stable Rust 的 `for<'a>` 只作用於 lifetime。「量詞化型別」（概念上的 `for<T>`）是更強的東西，Rust 沒有這語法；相關需求靠 GAT（generic associated types，1.65 才穩定）等機制逐步逼近，這裡不展開。

**`PhantomData` 是你唯一能手動指定 variance 的工具**：`PhantomData<T>` 讓型別對 `T` 協變，`PhantomData<fn(T)>` 逆變，`PhantomData<*mut T>` 或 `PhantomData<Cell<T>>` 不變。手刻容器時，這是你告訴編譯器「我這個 raw pointer 在語意上等於擁有一個 `T`」的方式。[Ch 15 記憶體佈局](./15-memory-layout.md) 與 [Ch 21](./21-unsafe-abstractions.md) 會用到。

---

## 動手練習

1. 把第一節 `longest` 的錯誤修好：加上 `<'a>` 讓它編過。然後試著讓 `x`、`y` 用**不同** lifetime（`<'a, 'b>`）但回傳綁 `'a`，看什麼情況下會編不過——這幫你理解「回傳值到底綁在哪個輸入」。

2. 把 `T: 'static` 那個例子改成傳 `Vec<&str>`（一個裝短命引用的 Vec）進 `needs_static`，預測它會不會過，再跑一次驗證你的預測。

3. 進階：把 variance 的 `evil` 函式簽章從 `&mut &'a str` 改成 `&'a str`（去掉 `mut`），看它現在會不會過。想清楚為什麼——`&T` 協變 vs `&mut T` 不變的差別就在這裡。

---

## 本章重點整理

- **elision** 是三條機械規則：單輸入引用 / 有 `&self` 時編譯器能填出唯一 `'a`；多輸入回傳引用時歧義，逼你手寫。
- **`'static` 兩義**：`&'static T` 是「引用活到程式結束」；`T: 'static` 是「型別不含短命借用」，owned 型別全滿足，與值的實際壽命無關。
- **HRTB `for<'a>`** 表達「對所有 lifetime 都成立」，用於閉包吃內部產生的引用；多數時候編譯器自動補。
- **variance**：`&'a T` 對 `T` 協變、`&'a mut T` 對 `T` 不變。**不變是防 UAF 的必要條件**——可寫的位置若協變，就能把短命引用寫進長命槽位。

## 自我檢核

- [ ] 不看筆記，能說出 elision 三條規則、並解釋為什麼 `longest(x, y)` 會失敗而 `first_word(s)` 不會。
- [ ] 面試問「`T: 'static` 是什麼意思」，你能不能一句話講清楚它**不是**「活到程式結束」，並舉 `String` 為例？
- [ ] 能解釋為什麼 `&mut T` 對 `T` 不變是安全所需——用 UAF 反例，而不是背「規則就是這樣」。
- [ ] 知道日常寫 safe Rust 時 variance 為什麼「感覺不到」，以及它在哪兩種場合會浮出來。

## 延伸閱讀

### 官方文件 / Spec

- **[Rust Reference — Lifetime elision](https://doc.rust-lang.org/reference/lifetime-elision.html)**
  - **讀哪裡**：「Lifetime elision in functions」整節，就是本章第一節那三條規則的權威版本，還涵蓋 `impl` header 與 trait object 的 elision（本章沒展開）。
  - **和本章的關聯**：本章的 elision 規則直接來自這裡；遇到 elision 行為不符預期時，這是最終仲裁。

- **[The Rustonomicon — Subtyping and Variance](https://doc.rust-lang.org/nomicon/subtyping.html)**
  - **核心內容**：把 variance 從頭推一遍，包含逆變、`PhantomData` 怎麼標 variance、以及為什麼 `&mut` 不變。
  - **讀哪裡**：整節都值得，尤其結尾那張「各型別 variance 表」。前提：先讀懂本章的 UAF 反例，再看它會更有感。
  - **和本章的關聯**：本章 variance 一節是這章的簡化直覺版；要寫 unsafe 抽象前，這節是必修。

### 書籍

- **《Rust for Rustaceans》— Jon Gjengset（No Starch Press, 2021）**
  - **這本書的定位**：中階 Rust 最佳單本書，和本課定位幾乎重合。
  - **讀哪幾章**：Ch 1「Foundations」談 variance 與 lifetime 的部分、Ch 2「Types」——比 Nomicon 白話，用實際會遇到的場景解釋為什麼需要 HRTB。讀完本章再讀，銜接最順。

### 部落格 / 技術文章

- **[“Common Rust Lifetime Misconceptions”](https://github.com/pretzelhammer/rust-blog/blob/master/posts/common-rust-lifetime-misconceptions.md)** — pretzelhammer（GitHub, 2020）
  - **這篇說什麼**：逐條拆解 lifetime 的常見誤解，包含「`'static` 兩種意義」「lifetime 不是值的壽命」這幾個本章強調的點，例子密集。
  - **讀哪裡**：整篇，特別是誤解 #2（`'static` 引用 vs `T: 'static`）與 #9（HRTB）。
  - **為什麼值得讀**：這是社群公認講 lifetime 誤解講得最清楚的一篇，補足本章因篇幅沒展開的邊角案例。

下一章我們把 `&str`、slice 拆開看它們的記憶體佈局——你會發現它們是「胖指標」，這解釋了為什麼 `size_of::<&str>()` 是 16 而不是 8。

→ [Ch 6 Slice、str 與 String：胖指標佈局](./06-slices-str-string.md)
