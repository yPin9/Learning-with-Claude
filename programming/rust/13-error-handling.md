# Ch 13 — 錯誤處理：Result / Option / ?

> **目標**：徹底搞懂 Rust「錯誤即值」的模型——`Result<T, E>` / `Option<T>` 是 enum、`?` 運算子怎麼展開（early return + `From::from`）、panic 與 Result 的分界、unwind 與 abort 的差別，以及怎麼設計自訂錯誤型別。對照 C 的 errno/回傳碼和 C++ 的 exception，看清 Rust 為什麼不要 exception。

> **環境**：`rustc 1.97.1`，x86-64 Linux（WSL2）。本章所有輸出、panic 行為、exit code 都在此實跑。

## 為什麼需要這個？

C 的錯誤處理是一團「約定」。函式回傳 `-1` 或 `NULL` 代表失敗，真正的原因塞在全域 `errno`，你**可以選擇不檢查**——而大部分 bug 就是這麼來的：`malloc` 回傳 `NULL` 沒檢查、`read` 回傳 `-1` 沒檢查，程式帶著壞狀態繼續跑，幾百行後才炸。

```c
FILE *f = fopen("config", "r");   // 失敗回 NULL
fread(buf, 1, 100, f);            // 沒檢查 f，直接用 -> segfault
```

C++ 用 exception 補救：拋出去、往上傳、被 `catch` 接住。問題是——**exception 是隱形的控制流**。看一個函式簽名 `int compute()`，你根本不知道它會不會拋、拋什麼。exception safety 是 C++ 最難的主題之一（strong/basic/nothrow guarantee），而且 exception 有執行期成本（unwind table、拋出時的 stack unwinding），很多效能敏感或嵌入式專案乾脆 `-fno-exceptions` 全禁。

Rust 選了第三條路：**錯誤是值（errors are values）**。一個可能失敗的函式回傳 `Result<T, E>`——要嘛 `Ok(T)`（成功值）、要嘛 `Err(E)`（錯誤值）。錯誤寫在型別簽名裡，**編譯器逼你處理它**（不處理 `Result` 會警告）。沒有隱形控制流、沒有 unwind 成本（在 happy path 上）、錯誤路徑跟正常路徑一樣是普通的資料流。這章講清楚這套怎麼運作。

## 先建立直覺

想像每個可能失敗的函式，回傳的不是「值」，而是「一個信封」。信封上貼著兩種標籤之一：

```
Result<T, E>：
┌─────────────┐        ┌─────────────┐
│ Ok(value)   │   或   │ Err(error)  │
└─────────────┘        └─────────────┘
   成功，裡面是 T          失敗，裡面是 E

Option<T>：
┌─────────────┐        ┌─────────────┐
│ Some(value) │   或   │ None        │
└─────────────┘        └─────────────┘
   有值                    沒有（不是錯誤，是「缺席」）
```

你**必須先拆信封**才能拿到裡面的值——用 `match`、用 `if let`、用 `?`、用 `.unwrap()`。拆的時候，兩種情況你都得面對（或明確選擇 panic）。這就是「型別強制你面對錯誤」：`Result<T, E>` 不是 `T`，你不能把它當 `T` 用，編譯器擋著。

> 橫向連結：`Result` 和 `Option` 都是 enum（[Ch 8](./08-struct-enum-pattern.md)）。`Result<T, E>` 就是 `enum Result<T, E> { Ok(T), Err(E) }`，`Option<T>` 就是 `enum Option<T> { Some(T), None }`。你已經會 `match` enum 了，錯誤處理就是把這套用在這兩個特定 enum 上。

## `?` 運算子：怎麼展開

手寫錯誤處理很囉嗦。每呼叫一個可能失敗的函式，你都要 `match` 一次、失敗就 early return：

```rust
// 沒有 ? 的世界，每一步都要手動 match
fn run_verbose(s: &str) -> Result<i32, ParseErr> {
    let n = match parse(s) {
        Ok(n) => n,
        Err(e) => return Err(e),   // 手動 early return
    };
    Ok(n * 2)
}
```

`?` 就是這段的語法糖：**成功就取出值繼續、失敗就 early return 那個錯誤**。而且——這是關鍵——early return 時它會呼叫 `From::from` 把錯誤型別**自動轉換**成外層函式要的錯誤型別。這就是為什麼 [Ch 12](./12-core-traits.md) 的 `From` 是錯誤處理的地基。

跑一個具體例子，內層錯誤是 `ParseErr`、外層函式回傳 `AppErr`，`?` 自動轉換：

```rust
use std::fmt;

// 兩個不同的錯誤型別
#[derive(Debug)]
struct ParseErr;
#[derive(Debug)]
struct AppErr(String);

// ? 用 From::from 把 ParseErr 轉成 AppErr
impl From<ParseErr> for AppErr {
    fn from(_: ParseErr) -> AppErr { AppErr("parse failed".into()) }
}
impl fmt::Display for AppErr {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "AppErr: {}", self.0)
    }
}

fn parse(s: &str) -> Result<i32, ParseErr> {
    s.parse::<i32>().map_err(|_| ParseErr)
}

// 回傳型別是 AppErr，但 parse 回傳 ParseErr —— ? 幫你轉
fn run(s: &str) -> Result<i32, AppErr> {
    let n = parse(s)?;      // ? = early return + From::from
    Ok(n * 2)
}

fn main() {
    match run("21") {
        Ok(v) => println!("ok: {}", v),
        Err(e) => println!("err: {}", e),
    }
    match run("xyz") {
        Ok(v) => println!("ok: {}", v),
        Err(e) => println!("err: {}", e),
    }

    // Option 也支援 ?
    fn first_char(s: &str) -> Option<char> {
        let c = s.chars().next()?;      // None 就 early return None
        Some(c.to_ascii_uppercase())
    }
    println!("{:?}", first_char("hi"));
    println!("{:?}", first_char(""));
}
```

實跑輸出：

```
ok: 42
err: AppErr: parse failed
Some('H')
None
```

拆解 `let n = parse(s)?;` 展開成什麼：

```
let n = parse(s)?;
   │
   ▼  等價於
let n = match parse(s) {
    Ok(v)  => v,                          // 成功：取出值
    Err(e) => return Err(AppErr::from(e)), // 失敗：轉型 + early return
};
```

`"xyz"` 那次：`parse("xyz")` 失敗回 `Err(ParseErr)`，`?` 呼叫 `AppErr::from(ParseErr)` 轉成 `AppErr("parse failed")`，early return。整個函式從沒碰到 `Ok(n * 2)`。這就是「錯誤即值」的美：錯誤傳遞是普通的 return，沒有魔法、沒有隱形控制流，你 grep `?` 就知道哪裡可能提早返回。

`Option` 的 `?` 同理：`None` 就 early return `None`，`Some(v)` 就取出 `v`。`first_char("")` 那次 `.next()` 回 `None`，`?` 直接讓函式返回 `None`。

## panic vs Result：可恢復 vs 不可恢復

Rust 把「出錯」分兩類，用兩套機制：

| | 可恢復（recoverable） | 不可恢復（unrecoverable） |
|---|---|---|
| 機制 | `Result<T, E>` / `Option<T>` | `panic!` |
| 語意 | 「這件事可能失敗，呼叫方該決定怎麼辦」 | 「程式進入了不該進入的狀態，繼續下去沒意義」 |
| 例子 | 檔案不存在、parse 失敗、網路逾時 | 陣列越界、`unwrap` 一個 `None`、assert 失敗、邏輯 bug |
| 傳播 | 靠 `?` 往上傳，最終被 `match` 處理 | unwind stack（或 abort），通常直接結束程式 |

**分界原則**：如果錯誤是「預期中、呼叫方能合理應對」的（找不到檔案就用預設值、parse 失敗就報錯給使用者），用 `Result`。如果錯誤代表「程式邏輯壞了、繼續跑只會產生垃圾」（陣列越界、不變式被破壞），`panic!`。panic 不是給你當一般錯誤處理用的——它是「這裡本該不可能發生」的最後防線。

幾個從 `Result`/`Option` 拿值的方法：

```rust
fn main() {
    let v = vec![1, 2, 3];
    // unwrap_or：失敗給預設值，不 panic
    let a: i32 = "10".parse().unwrap_or(0);
    let b: i32 = "xx".parse().unwrap_or(0);
    println!("a={}, b={}", a, b);

    // expect：失敗 panic，並附上你的訊息
    let ok: Option<&i32> = v.get(1);
    println!("v[1] = {}", ok.expect("index 1 must exist"));

    // 這會 panic：越界索引
    println!("about to panic");
    let _boom = v[10];
    println!("never reached");
}
```

實跑輸出（stdout + stderr 交錯）：

```
a=10, b=0
v[1] = 2
about to panic
thread 'main' (245702) panicked at ch13panic.rs:14:18:
index out of bounds: the len is 3 but the index is 10
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

- `unwrap_or(0)`：`"xx"` parse 失敗，給預設 `0`，不 panic。
- `expect("...")`：`None` 時 panic 並印你給的訊息（比 `unwrap` 的無腦訊息好，永遠用 `expect` 不用 `unwrap`）。
- `v[10]`：越界，panic，訊息告訴你 len=3、index=10、在哪一行。這是**不可恢復**——越界索引是邏輯 bug，該讓它炸，不是回個 `Result` 讓你假裝沒事。

## unwind vs abort：panic 之後發生什麼

panic 預設走 **unwind**：像 C++ exception 一樣，往上「解開」stack，一路呼叫每個區域變數的 `Drop`（[Ch 12](./12-core-traits.md)），最後結束該 thread。這讓資源能正確釋放。但 unwind 有成本：需要 unwind table、panic 時要走一遍解棧。

另一個選項是 **abort**：panic 直接 `SIGABRT` 掛掉，不解棧、不跑 Drop。用 `-C panic=abort` 或 `Cargo.toml` 的 `panic = "abort"` 開。嵌入式、`no_std`、或不想要 unwind 成本的場景會用。

兩者的 exit code 不一樣，實跑證明：

```
# 預設 unwind
$ ./ch13panic; echo $?
... panic 訊息 ...
101                    # unwind panic 的 exit code 是 101

# panic=abort（rustc -C panic=abort）
$ ./ch13panic_abort; echo $?
Aborted (core dumped)
134                    # 134 = 128 + 6(SIGABRT)
```

- unwind：exit code **101**（Rust 約定的 panic exit code）。
- abort：exit code **134** = 128 + SIGABRT(6)，而且 "core dumped"。

> 對照 C++：Rust 的 unwind panic 機制**底層跟 C++ exception 用同一套** unwinding（在 Linux 上都是 DWARF-based unwinding / `libunwind`）。差別是語意——C++ 鼓勵你 `catch` 並恢復，Rust 的 panic **不是**設計來被接住恢復的（雖然有 `std::panic::catch_unwind`，但那是給 FFI 邊界防止 unwind 跨語言用的，不是一般錯誤處理）。Rust 的哲學：可恢復的錯誤用 `Result`，panic 是「這裡不該發生」，接住它通常代表你的錯誤該用 `Result` 才對。

對照 C 的 errno / 回傳碼：C 沒有 unwind、沒有 Drop，錯誤靠回傳碼 + 手動 `goto cleanup` 釋放資源（Linux kernel 的經典模式）。Rust 的 `?` + `Drop` 把「錯誤傳播」和「資源清理」都自動化了：`?` 負責傳播，離開作用域的 `Drop` 負責清理，不用 `goto cleanup`。

## 自訂錯誤型別與 `Box<dyn Error>`

小程式可以每個模組定一個錯誤 enum。但當一個函式要串起好幾種不同來源的錯誤（parse 錯誤、IO 錯誤、自訂錯誤），一個個寫 `From` 很煩。`Box<dyn Error>`（[Ch 11](./11-trait-objects-dispatch.md) 的 trait object）是懶人解：**任何實作了 `std::error::Error` 的錯誤都能塞進 `Box<dyn Error>`**，`?` 靠標準庫的 blanket `From` impl 自動裝箱。

```rust
use std::error::Error;
use std::fmt;

#[derive(Debug)]
struct ConfigErr(String);
impl fmt::Display for ConfigErr {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "config error: {}", self.0)
    }
}
impl Error for ConfigErr {}   // 空實作即可（Error 的方法都有預設）

// Box<dyn Error>：異質錯誤透過同一條 ? 鏈往上傳
fn load(s: &str) -> Result<i32, Box<dyn Error>> {
    if s.is_empty() {
        return Err(Box::new(ConfigErr("empty".into())));
    }
    let n: i32 = s.parse()?;   // ParseIntError -> Box<dyn Error>，? 自動裝箱
    Ok(n)
}

fn main() {
    for input in ["42", "", "oops"] {
        match load(input) {
            Ok(n) => println!("[{:?}] -> ok {}", input, n),
            Err(e) => println!("[{:?}] -> {}", input, e),
        }
    }
}
```

實跑輸出：

```
["42"] -> ok 42
[""] -> config error: empty
["oops"] -> invalid digit found in string
```

`load` 裡兩種錯誤——`ConfigErr`（自訂）和 `ParseIntError`（標準庫，`s.parse()` 產生）——都變成 `Box<dyn Error>` 往上傳。`?` 對 `s.parse()?` 那行自動把 `ParseIntError` 裝箱，因為標準庫有 `impl<E: Error> From<E> for Box<dyn Error>`。缺點是型別資訊被抹掉（呼叫方拿到的是抹型別的 `Box<dyn Error>`，要 `downcast` 才知道具體是哪種），所以 `Box<dyn Error>` 適合應用程式頂層 / 快速原型，函式庫應該定明確的錯誤 enum 讓呼叫方能精確處理。

**生態工具（外部 crate，不在標準庫）**：實務上手寫 `From` + `Display` + `Error` 很煩，兩個 crate 幾乎是業界標準：

- **`thiserror`**（外部 crate）：給**函式庫**用。用 `#[derive(Error)]` 自動生 `Display`/`Error`/`From`，讓你定精確的錯誤 enum 而不用手寫 boilerplate。
- **`anyhow`**（外部 crate）：給**應用程式**用。提供 `anyhow::Error`（本質是加強版 `Box<dyn Error>`，帶 backtrace 和 context 鏈），`fn main() -> anyhow::Result<()>` 讓你在 `main` 直接用 `?`。

分工原則：**函式庫用 `thiserror`（明確型別）、應用程式用 `anyhow`（懶人抹型別）**。這兩個是 dtolnay（Rust 生態最多產的作者之一）寫的，幾乎所有現代 Rust 專案都在用。本章不展開它們的 API，知道它們是什麼、什麼時候用即可。

## 不用拆信封就能轉換：combinator

不是每次都要 `match` 或 `?` 才能操作 `Result`/`Option`。標準庫給了一堆 combinator，讓你**不拆開信封**就對裡面的值做轉換——這跟 [Ch 12](./12-core-traits.md) 的 iterator adapter 是同一種「鏈式、惰性思考」的風格。這對寫出乾淨的錯誤處理很重要，值得單獨練熟。

```rust
fn main() {
    let x: Option<i32> = Some(5);
    println!("map:        {:?}", x.map(|v| v * 2));           // Some(10)
    println!("and_then:   {:?}", x.and_then(|v| if v > 0 { Some(v) } else { None }));
    println!("filter:     {:?}", x.filter(|&v| v > 10));      // None（5 不 > 10）
    println!("or:         {:?}", None.or(Some(99)));          // Some(99)
    println!("unwrap_or_else: {}", None::<i32>.unwrap_or_else(|| 7));

    // Result 的 combinator
    let r: Result<i32, String> = Ok(3);
    println!("map:        {:?}", r.clone().map(|v| v + 1));   // Ok(4)：只動 Ok 值
    println!("map_err:    {:?}", "z".parse::<i32>().map_err(|e| format!("bad: {}", e)));
    println!("ok():       {:?}", r.clone().ok());             // Some(3)：Result -> Option

    // ? 對 Option 一樣好用：串接多個回傳 Option 的步驟
    fn second_word(s: &str) -> Option<&str> {
        s.split_whitespace().nth(1)
    }
    println!("{:?}", second_word("hello rust world"));
    println!("{:?}", second_word("hi"));
}
```

實跑輸出：

```
map:        Some(10)
and_then:   Some(5)
filter:     None
or:         Some(99)
unwrap_or_else: 7
map:        Ok(4)
map_err:    Err("bad: invalid digit found in string")
ok():       Some(3)
Some("rust")
None
```

幾個最常用的，記住它們的分工：

| combinator | 作用 | 什麼時候用 |
|---|---|---|
| `.map(f)` | 轉換 `Ok`/`Some` 裡的值，錯誤原封不動 | 成功值要變形 |
| `.map_err(f)` | 轉換 `Err` 裡的錯誤，成功值不動 | 手動轉錯誤型別（`?` 的手動版） |
| `.and_then(f)` | `f` 回傳另一個 `Result`/`Option`，攤平（flatten） | 鏈式的「成功才繼續下一步」 |
| `.unwrap_or(d)` / `.unwrap_or_else(f)` | 失敗給預設值 | 有合理 fallback，不想 panic |
| `.ok()` | `Result<T, E>` → `Option<T>`（丟掉錯誤） | 只在乎有沒有值、不在乎為什麼失敗 |
| `.or(alt)` / `.or_else(f)` | 失敗就換另一個 `Result`/`Option` | 有備援來源 |

`.map_err` 是 `?` 的手動搭檔：當內外層錯誤型別**沒有** `From` impl、你又不想寫一個，就 `foo().map_err(|e| MyErr::from_whatever(e))?`——先手動轉再 `?`。

**`unwrap_or` vs `unwrap_or_else`**：`unwrap_or(compute_default())` 的 `compute_default()` **每次都會算**（就算 `Ok` 用不到）；`unwrap_or_else(|| compute_default())` 只在真的失敗時才算。預設值昂貴（例如要配置、要讀檔）時用 `_else` 版，這是常被忽略的效能細節。

## 對比與取捨：三種錯誤處理哲學

| 模型 | 錯誤怎麼傳 | 能不能忽略 | 執行期成本 | 隱形控制流 |
|---|---|---|---|---|
| C errno / 回傳碼 | 手動檢查回傳值 | **能**（不檢查就過） | 零 | 無 |
| C++ exception | `throw` / `catch` | 不能忽略（會傳到頂層 terminate） | 拋出時有 unwind 成本 | **有**（看簽名不知道會不會拋） |
| Rust Result / ? | `?` 傳播（普通 return） | 不能（`Result` 沒處理會警告） | 零（happy path）；錯誤路徑就是普通資料流 | 無（`?` 看得見） |

Rust 拿到了三者的好處：像 C 一樣零成本、像 C++ 一樣不能被無視、又沒有 C++ 的隱形控制流。代價是**囉嗦**——每個可能失敗的地方都要 `?` 或 `match`，錯誤型別要設計。這個囉嗦是刻意的：Rust 認為「讓錯誤在型別簽名裡看得見、逼你面對」值得這點囉嗦。

## 踩雷集錦

1. **到處 `.unwrap()`**：`unwrap` 是「我保證這不會失敗，錯了就 panic」。原型可以，生產 code 濫用 = 到處埋 panic 地雷。至少用 `.expect("為什麼我確定這裡不會 None")` 把你的假設寫下來，panic 時才知道哪個假設破了。

2. **把 `panic!` 當一般錯誤處理**：panic 是「不該發生的事發生了」。使用者輸入錯誤、檔案不存在這種**預期內**的失敗用 `Result`，不要 `panic!`。反過來，陣列越界、`unwrap` 一個你邏輯上保證是 `Some` 的東西——那才是 panic 的場合。

3. **以為 `Option` 是錯誤**：`None` 不是錯誤，是「合法的缺席」。`HashMap::get` 找不到 key 回 `None`——這不是錯誤，是正常結果。錯誤（「這件事本該成功但失敗了」）用 `Result`；缺席用 `Option`。

4. **忘了 `?` 需要 `From`**：`?` 自動轉錯誤型別靠 `From`。如果內層錯誤型別到外層錯誤型別沒有 `From` impl，`?` 編不過（會提示你缺 `From`）。要嘛實作 `From`，要嘛用 `.map_err(...)` 手動轉，要嘛用 `Box<dyn Error>`/`anyhow` 這種吃所有 `Error` 的容器。

5. **在 `main` 想用 `?` 卻回傳 `()`**：`?` 只能用在回傳 `Result`/`Option`（或實作 `Try`）的函式裡。想在 `main` 用 `?`，把 `main` 簽名改成 `fn main() -> Result<(), Box<dyn Error>>`（或 `anyhow::Result<()>`）。

## 進階：再往深一層

**`?` 的底層是 `Try` trait**：`?` 不是只對 `Result`/`Option` 硬編碼，它展開成 `Try::branch` 的呼叫（目前 `Try` trait 還是 nightly-only unstable，但 `?` 對 `Result`/`Option` 早已 stable）。這讓 `?` 未來能支援自訂的「可短路」型別。知道有這層即可，日常不碰。

**錯誤要不要 backtrace**：`std::backtrace::Backtrace`（Rust 1.65+ stable）可以在自訂錯誤型別裡存一個 backtrace，`RUST_BACKTRACE=1` 時填。`anyhow` 自動幫你做。函式庫錯誤要不要帶 backtrace 是設計取捨——帶了好 debug，但每次建錯誤都抓 backtrace 有成本。

**`must_use`**：`Result` 標了 `#[must_use]`，忽略一個 `Result`（不 `?`、不 `match`、不 `let _ =`）會編譯警告。這是 Rust「逼你面對錯誤」的機制之一。

```rust
// 進階：手動 match 展開一個巢狀 ?，看它在做什麼
fn chain(a: &str, b: &str) -> Result<i32, std::num::ParseIntError> {
    let x: i32 = a.parse()?;   // 這兩個 ? 各是一次 match + early return
    let y: i32 = b.parse()?;   // 錯誤型別都是 ParseIntError，不需 From 轉換
    Ok(x + y)
}
// chain("3", "4") -> Ok(7); chain("3", "z") -> Err(...) 在第二個 ? 返回
```

## 動手練習

1. 把 `run` 的 `?` 改回手寫 `match ... return Err(...)`，確認行為一樣，體會 `?` 省了多少字。
2. 故意讓 `run` 的內層錯誤型別沒有到外層的 `From` impl，看 `?` 怎麼罵你（提示：會叫你 `?` couldn't convert the error）。
3. 把 `main` 改成 `fn main() -> Result<(), Box<dyn Error>>`，在裡面用 `?`，看 `main` 回傳 `Err` 時的 exit code（提示：也是 panic-like 的行為嗎？不是——`main` 回 `Err` 印錯誤並 exit 1）。
4. 用 `-C panic=abort` 編一個會 panic 的程式，`echo $?` 確認 exit code 是 134 而非 101。

## 本章重點整理

- `Result<T, E>` / `Option<T>` 是 enum；錯誤是**值**，寫在型別簽名裡，編譯器逼你處理。
- `?` = 「成功取值、失敗 early return」+ 失敗時用 `From::from` 自動轉錯誤型別（所以 `From` 是地基）。
- panic（不可恢復、邏輯 bug）vs Result（可恢復、預期內失敗）是刻意的分界。用 `expect` 不用 `unwrap`。
- panic 預設 unwind（跑 Drop、exit 101）；`panic=abort` 直接 SIGABRT（exit 134、不跑 Drop）。Rust unwind 底層與 C++ exception 同源，但語意是「不該被接住恢復」。
- `Box<dyn Error>` / `anyhow`（app）/ `thiserror`（lib）是實務錯誤型別工具；後兩者是外部 crate。

## 自我檢核

- [ ] 不看筆記，能不能把 `let n = f()?;` 手動展開成 `match`（含 `From::from` 那步）？
- [ ] 面試問「什麼時候該 panic、什麼時候該回 Result」，你能給出明確判準嗎？
- [ ] `Option` 和 `Result` 的語意差在哪？`HashMap::get` 找不到 key 為什麼回 `Option` 不回 `Result`？
- [ ] unwind 和 abort 的 exit code 各是多少？為什麼 Rust 的 unwind 跟 C++ exception 底層同源卻語意不同？
- [ ] `thiserror` 和 `anyhow` 各適合函式庫還是應用程式？為什麼？

## 延伸閱讀

### 官方文件

- **[The Rust Book — Ch 9: Error Handling](https://doc.rust-lang.org/book/ch09-00-error-handling.html)**
  - **讀哪裡**：9.2（Recoverable Errors with Result）整節，特別是 "A Shortcut for Propagating Errors: the ? Operator" 和 "To panic! or Not to panic!"。
  - **和本章的關聯**：本章 `?` 展開和 panic/Result 分界的官方版；9.3 的分界討論值得反覆讀。

- **[std::error::Error 文件](https://doc.rust-lang.org/std/error/trait.Error.html)**
  - **讀哪裡**：trait 定義和 `source()` 方法（錯誤鏈）那段。
  - **和本章的關聯**：本章 `Box<dyn Error>` 為什麼能吃所有錯誤，關鍵在這個 trait 和它的 blanket `From` impl。

### 部落格 / 技術文章

- **[Error Handling in Rust](https://blog.burntsushi.net/rust-error-handling/)** — Andrew Gallant (BurntSushi)
  - **這篇說什麼**：Rust 錯誤處理的深度長文，從 `Option`/`Result` 到自訂錯誤型別、`Box<dyn Error>` 一路推導。
  - **前提知識**：懂本章的 `?` 和 `From`；這篇比本章深，適合當進階讀物。
  - **為什麼值得讀**：作者是 ripgrep/regex crate 作者，這篇是 Rust 錯誤處理的經典參考，很多人推薦的「就讀這篇」。雖然寫於 `?` 尚叫 `try!` 的年代，錯誤設計的原則完全沒過時。

- **[thiserror / anyhow 的定位（dtolnay 的 README）](https://github.com/dtolnay/anyhow)**
  - **讀哪裡**：`anyhow` README 開頭的 "Details" 和它跟 `thiserror` 的 comparison 那段。
  - **和本章的關聯**：本章說「lib 用 thiserror、app 用 anyhow」的一手依據，作者親自說明兩者的設計分工。

### 書籍

- **《Rust for Rustaceans》** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust，和本課定位重合。
  - **讀哪幾章**：Chapter 4（Error Handling）談「什麼時候用 enum、什麼時候用 `Box<dyn Error>`」、錯誤型別的設計取捨，比本章更深入函式庫作者視角。

下一章進最後一塊型別系統拼圖——閉包（closure）。你會看到閉包其實是編譯器自動生的匿名 struct + `Fn`/`FnMut`/`FnOnce` trait，捕獲越多它越大、無捕獲的閉包 0 bytes，我們用 `size_of` 證明給你看。

→ [Ch 14 閉包：Fn / FnMut / FnOnce](./14-closures.md)
