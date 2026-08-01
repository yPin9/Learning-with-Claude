# Ch 19 — FFI：與 C 互操作與安全 wrapper

> **目標**：把 Rust 和 C 縫在一起。學完你能：用 `extern "C"` 呼叫一支真的 C 函式並 link 起來；反向用 `#[no_mangle] pub extern "C"` 把 Rust 匯出給 C `main` 呼叫；搞懂 `c_int`/`c_char`/`repr(C)` struct 怎麼和 C ABI 對齊；用 `CString`/`CStr` 做字串來回傳遞（誰配、誰 free 講清楚）；用 `libc` crate 直呼 glibc、用 `bindgen` 自動生 binding；最後把一個 unsafe C API 包成 RAII 管生命週期、invariant 檢查在門口的**安全 Rust wrapper**。全程 C 對照——這是你 C 底子最直接派上用場的一章。

> **環境**：Rust `rustc 1.97.1`（stable，edition 2015）、`cc (Ubuntu 11.4.0)`、`bindgen 0.69.5`、`libc 0.2.189`，x86-64 Linux（WSL2）。所有 C/Rust 編譯、link、輸出、backtrace 都是本機真跑，非推測。link 方式一律「C 編成 `.o` → `ar` 打包 `.a` → `rustc -L . -l static=xxx`」，或反向 Rust `--crate-type=staticlib` 給 C link。平台相依處（`c_char` signedness）會標明。

## 為什麼需要這個？

世界上跑得最久、最關鍵的 code——glibc、OpenSSL、SQLite、libpng、整個 Linux kernel、你公司那個十五年沒人敢動的 C 核心——全是 C。一個新語言要活下來，第一個硬需求就是：**能不能無痛用掉現有的 C 生態**？做不到就是死路。Rust 的答案是 FFI（Foreign Function Interface，外部函式介面）——一套「Rust 和其他遵守 C ABI 的語言互相呼叫」的機制。

這裡有個你從 C/C++ 帶來的直覺要先校正。在 C 裡呼叫另一個 C 函式，你只是 `#include` 一個 header、link 一個 `.o`，沒有「邊界」的概念。Rust 不一樣：Rust 的所有安全保證——ownership、borrow、lifetime、「型別 `T` 一定是合法 `T`」——**只在 Rust 自己的世界成立**。跨過 FFI 邊界進 C，這些保證全部失效，因為 C 根本不知道它們存在——不檢查 null、不管誰 free、不擋 double free、不保證回傳的指標活著。

所以核心矛盾是：**FFI 邊界是 Rust 安全保證的破口**。這條線上每件事都在 `unsafe` 裡，責任從編譯器轉回你手上——型別對不對齊、生命週期誰負責、null 誰檢查、記憶體誰 free，全是你的事。這章下半場（安全 wrapper）就是教：怎麼把破口**封裝**起來，讓它只存在 wrapper 內幾行 unsafe，對外提供編譯器能重新罩住的安全介面。這正是標準庫在做的事——`std` 底層全是 FFI 呼 syscall，但你用 `File`、`Mutex` 時完全 safe。

[Ch 18](./18-unsafe-advanced.md) 備齊的 `ptr::read`/`write`、`transmute`、`repr(C)`、`NonNull`，這章會在 FFI 邊界一個個再現。

## 先建立直覺

想像 Rust 和 C 是兩個國家，中間隔一條河。河的兩岸各自有自己的法律（Rust：borrow checker 執法；C：無政府）。FFI 就是河上唯一那座橋。

```
      Rust 這岸                 橋 (C ABI)                C 那岸
  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
  │ borrow checker   │    │ 過橋前把行李換成   │    │ 沒有任何檢查      │
  │ 全程執法          │───▶│ C 認得的規格：    │───▶│ 你給什麼吃什麼    │
  │ 型別/生命週期安全 │    │ - repr(C) 佈局    │    │ null? free 兩次?  │
  │                  │    │ - C ABI 傳參      │    │ 越界? 全不管      │
  │ 進 unsafe{} 才能  │◀───│ - #[no_mangle]    │◀───│ 回傳的東西可能是   │
  │ 上橋             │    │   符號名          │    │ 垃圾/懸空/null    │
  └──────────────────┘    └──────────────────┘    └──────────────────┘
       安全區                過橋 = unsafe             無保證區
```

過橋要遵守橋的規格（C ABI）：你的行李（資料型別）要用 C 認得的方式打包（`repr(C)`、`c_int` 這些），符號名要用 C 找得到的方式命名（`#[no_mangle]`）。過橋這動作本身在 Rust 這岸永遠是 `unsafe`——因為編譯器沒法追到對岸去驗證你。過橋回來時，對岸交給你的東西（回傳值、被改過的 buffer）編譯器也一律不信任，得你自己驗。

> 對照 C/C++：你在 C++ 裡呼叫 C 也要處理 name mangling（所以有 `extern "C" { }`），這個直覺可以直接搬過來。Rust 的 `extern "C"` 就是同一件事——「這個符號用 C 的規則命名和呼叫」。差別只在 Rust 額外把「上橋」標成 `unsafe`。

## 呼叫 C 函式：extern "C" 與真的 link 起來

從最基本的開始：寫一支 C 函式，在 Rust 宣告它、link 它、`unsafe` 呼叫它。C 端一支普通到不能再普通的 GCD：

```c
// c_side.c
#include <stdint.h>
int32_t c_gcd(int32_t a, int32_t b) {
    while (b != 0) {
        int32_t t = b;
        b = a % b;
        a = t;
    }
    return a;
}
```

Rust 端用 `extern "C"` 區塊**宣告**這個函式的簽章（只宣告，不定義——定義在 C 那邊）：

```rust
// main.rs
use std::os::raw::c_int;
extern "C" {
    fn c_gcd(a: c_int, b: c_int) -> c_int;
}
fn main() {
    let g = unsafe { c_gcd(48, 36) };
    println!("gcd(48, 36) = {}", g);
    let g2 = unsafe { c_gcd(1071, 462) };
    println!("gcd(1071, 462) = {}", g2);
}
```

link 三步：C 編成 object → `ar` 打包成靜態庫 → `rustc` 用 `-L`（庫路徑）+ `-l static=cside`（連 `libcside.a`）連進來。注意 `rustc` **不吃** `.o` 當位置參數（會報 `multiple input filenames provided`），一定要走 `-l` 那條：

```
$ cc -c c_side.c -o c_side.o
$ ar rcs libcside.a c_side.o        # 產出 libcside.a
$ rustc main.rs -L . -l static=cside -o demo1
$ ./demo1
gcd(48, 36) = 12
gcd(1071, 462) = 21
```

`gcd(48,36)=12`、`gcd(1071,462)=21`——Rust 真的呼到了 C 的迴圈。三個關鍵：

- **`extern "C"`**：`"C"` 是 ABI 字串，指定用 C 的 calling convention（見下節）。宣告了函式但沒有 body——Rust 相信符號會在 link 期由別的 object 提供。
- **`unsafe` 是強制的**：呼叫任何 `extern` 函式都要 `unsafe`。Rust 沒法驗證那支 C 函式真的照你宣告的簽章行事、不搞破壞。「我相信這 C 函式簽章正確、行為 sound」這保證的責任丟給你，`unsafe` 就是你簽字的地方。
- **`c_int` 不是 `i32`**：x86-64 Linux 上 `c_int` 就是 `i32`，但你該寫 `c_int`——它表達「對齊的是 C 的 `int`」的**意圖**，在 `int` 非 32-bit 的平台上自動對。可讀性 + 可攜性。

**故意寫錯：簽章對不上，編譯器擋不了。** 如果我把 Rust 宣告改成 `fn c_gcd(a: c_int) -> c_int`（少一個參數），Rust 編得過、link 得過，執行時第二個參數變成暫存器裡的垃圾——這是 FFI 最陰險的地方：**簽章 mismatch 是你的責任，編譯器完全信任你的宣告**。C header 至少還會在你 include 時對一次；Rust 這邊的 `extern` 宣告和 C 定義是兩份獨立的真相，對不上沒人管。這正是 `bindgen`（後面）存在的理由——讓機器從 header 生宣告，別讓人手抄出錯。

## 底層機制：C ABI 與 calling convention

`extern "C"` 到底在指定什麼？答案是 **calling convention（呼叫慣例）**：函式呼叫時，參數放哪、回傳值放哪、誰負責清 stack、哪些暫存器要保留。這是一套 CPU 架構 + OS 定義的規約，Rust 和 C 都得遵守同一套才能互相呼叫。

x86-64 Linux 用的是 **System V AMD64 ABI**。它的整數/指標參數傳遞規則（你 pwn/RE 時背過的）：

```
  參數傳遞（整數/指標類），依序放入這些暫存器：
    第1個  →  rdi
    第2個  →  rsi
    第3個  →  rdx
    第4個  →  rcx
    第5個  →  r8
    第6個  →  r9
    第7個起 →  push 到 stack
  回傳值   →  rax（第二個回傳值用 rdx）
  浮點參數 →  xmm0..xmm7
```

所以 `c_gcd(48, 36)`：Rust 端把 `48` 放進 `rdi`、`36` 放進 `rsi`，`call c_gcd`，C 從 `rdi`/`rsi` 取參數、算完把結果放 `rax`、`ret`。Rust 從 `rax` 讀回傳值。兩邊都照 System V 規則走，所以無縫——**FFI 能運作的根本原因，就是雙方對「參數放哪」有共識**。

這也解釋了為什麼簽章 mismatch 是災難但編譯器抓不到：如果 Rust 以為只有一個參數，它只設 `rdi`，`rsi` 是上一次呼叫留下的垃圾，而 C 照樣去讀 `rsi`——沒有 crash，只有錯誤的值。ABI 層面沒有型別，只有暫存器和 stack 位置。

> 對照 C/C++：這跟 C++ 呼叫 C 要 `extern "C"` 是同一層問題，但 C++ 那邊主要是 name mangling（符號名）差異；calling convention 在同平台的 C/C++/Rust 之間其實一致（都是 System V）。Rust 支援的 ABI 字串還有 `"system"`（Windows 上是 stdcall）、`"C-unwind"`（允許 unwind 跨邊界，見踩雷 5）等，但 `"C"` 是最常用的。

## 反向：把 Rust 匯出給 C 呼叫

FFI 是雙向的。你也能寫 Rust 函式，編成庫，讓 C 的 `main` 來 link 呼叫——這是「用 Rust 逐步替換 C code base」的關鍵手法（新模組用 Rust 寫，塞回原本的 C 程式）。

要讓 C 找得到 Rust 函式，需要兩件事：`#[no_mangle]`（關掉 Rust 的 name mangling，讓符號名就是 `rust_fib` 而不是 `_ZN...` 那串）、`pub extern "C"`（公開 + 用 C ABI）：

```rust
// rustlib.rs
use std::os::raw::c_int;
#[no_mangle]
pub extern "C" fn rust_fib(n: c_int) -> c_int {
    if n < 2 { return n; }
    let (mut a, mut b) = (0, 1);
    for _ in 2..=n {
        let c = a + b;
        a = b;
        b = c;
    }
    b
}
```

C 端只要 `extern` 宣告一下就能呼叫：

```c
// c_main.c
#include <stdio.h>
#include <stdint.h>
extern int32_t rust_fib(int32_t n);
int main(void) {
    for (int i = 0; i < 10; i++)
        printf("fib(%d) = %d\n", i, rust_fib(i));
    return 0;
}
```

這次反過來：Rust 編成 **staticlib**（`--crate-type=staticlib` 產出 `.a`），由 C 的 `cc` 來 link。Rust staticlib 帶了整個 Rust runtime（panic handler 等），link 時要補 `-lpthread -ldl`：

```
$ rustc --crate-type=staticlib rustlib.rs -o librustlib.a
$ cc c_main.c -L . -l rustlib -o demo2 -lpthread -ldl
$ ./demo2
fib(0) = 0
fib(1) = 1
fib(2) = 1
fib(3) = 2
fib(4) = 3
fib(5) = 5
fib(6) = 8
fib(7) = 13
fib(8) = 21
fib(9) = 34
```

C 的 `main` 真的在跑 Rust 寫的 `rust_fib`。少了 `#[no_mangle]`，符號名會是被 mangle 過的一長串，C 的 `extern int32_t rust_fib` 就 link 不到（`undefined reference`）。`#[no_mangle]` 是這裡的關鍵開關——它讓符號表裡出現乾淨的 `rust_fib`，跟 C 的 `extern "C"` 效果對稱。

## 型別對應與字串來回：CString / CStr

過橋要帶對規格的行李。Rust 和 C 的型別對應表（`std::os::raw` 或 `libc` 提供）：

| C 型別 | Rust 對應 | 說明 |
|---|---|---|
| `int` | `c_int`（= `i32` on x86-64） | 用別名表達「這是 C int」 |
| `unsigned` | `c_uint` | |
| `char` | `c_char`（`i8` on x86-64、`u8` on ARM！） | signedness 平台相依 |
| `size_t` | `usize` | |
| `void *` | `*mut c_void` / `*mut T` | |
| `const char *` | `*const c_char` | C 字串，見下 |
| `struct {...}` | `#[repr(C)] struct` | 佈局要對齊 |

`c_char` 的 signedness 平台相依（你在 C 也踩過 `char` 到底 signed 不 signed 的坑）。實測 `std::os::raw::c_char::MIN != 0`，x86-64 Linux 真跑印 `c_char is signed? true`（`MIN=-128, MAX=127`，即 `i8`）；ARM 上會是 `u8`。Rust 的 `c_char` 別名跟著目標平台自動變號——寫 `c_char` 而非硬寫 `i8`，換到 ARM 才不會對不上 C 的 `char`。

**`repr(C)` 是 struct 過橋的護照。** Rust 預設的 struct 佈局是**未指定的**（編譯器有權重排欄位省 padding，[Ch 15](./15-memory-layout.md)）。要和 C 交換 struct，必須加 `#[repr(C)]`——它保證欄位順序、對齊、padding 都照 C 規則：

```rust
use std::os::raw::c_int;
#[repr(C)]
#[derive(Debug)]
struct Point { x: c_int, y: c_int, dist: f64 }
extern "C" {
    fn c_make_point(x: c_int, y: c_int) -> Point;
}
fn main() {
    let p = unsafe { c_make_point(3, 4) };
    println!("{:?}", p);
    println!("sizeof Point (Rust) = {}", std::mem::size_of::<Point>());
}
```

C 端對應的 `typedef struct { int32_t x; int32_t y; double dist; } Point;`，`c_make_point` 填 `dist = x*x + y*y` 後把 struct **by value** 回傳。真跑，兩邊 `sizeof` 一致（都 16：兩個 i32 = 8 + double = 8，剛好對齊無 padding）：

```
Point { x: 3, y: 4, dist: 25.0 }
sizeof Point (Rust) = 16
sizeof Point (C) = 16
```

拿掉 `#[repr(C)]`，Rust 可能把 `dist`（對齊需求最大）排到最前面省 padding，佈局就和 C 對不上，讀出來全錯——而且**不會報錯**，因為 ABI 層沒有型別檢查。這是 FFI struct 最常見的靜默 bug。

**字串是 FFI 最容易出事的地方**，因為 Rust 和 C 對「字串」的定義根本不同：

```
  Rust String / &str          C 字串 (const char *)
  ┌────────────────┐         ┌────────────────┐
  │ ptr + len       │         │ ptr             │
  │ 長度另外存      │         │ 靠結尾的 \0 標記 │
  │ 不保證 NUL 結尾 │         │ 沒 \0 = 讀到爆   │
  │ UTF-8           │         │ 任意 bytes      │
  └────────────────┘         └────────────────┘
```

橋接靠兩個型別：`CString`（**擁有**一塊 NUL 結尾的 buffer，Rust → C 用）、`CStr`（**借用**一個 C 字串，C → Rust 讀用，類比 `&str`）。一次跑全套來回：

```rust
use std::ffi::{CString, CStr};
use std::os::raw::c_char;
extern "C" {
    fn c_shout(s: *mut c_char) -> *mut c_char;   // 就地大寫化
    fn c_greeting() -> *const c_char;            // 回傳 C 擁有的靜態字串
    fn c_strlen(s: *const c_char) -> usize;
}
fn main() {
    // Rust -> C：CString 建一個 NUL 結尾 buffer，把 ptr 借給 C 讀
    let msg = CString::new("Rustacean").unwrap();
    let len = unsafe { c_strlen(msg.as_ptr()) };
    println!("c_strlen(\"Rustacean\") = {}", len);

    // Rust -> C（可變）：into_raw 交出所有權拿到裸指標，C 就地改
    let mutable = CString::new("hello world").unwrap();
    let raw = mutable.into_raw();
    let shouted = unsafe {
        let p = c_shout(raw);
        CStr::from_ptr(p).to_string_lossy().into_owned()
    };
    println!("c_shout result = {}", shouted);
    let _reclaim = unsafe { CString::from_raw(raw) };   // 收回所有權讓 Rust 釋放

    // C -> Rust：C 回它自己擁有的字串，Rust 只借來讀（不能 free）
    let g = unsafe { CStr::from_ptr(c_greeting()) };
    println!("c_greeting = {}", g.to_str().unwrap());
}
```

真跑：

```
c_strlen("Rustacean") = 9
c_shout result = HELLO WORLD
c_greeting = hello from C
```

**這段的所有權（誰 free）是全章最重要的觀念，逐項拆**：

- **`CString::new("Rustacean")`**：Rust 配一塊 heap buffer（內容 `Rustacean\0`），`CString` **擁有**它。`as_ptr()` 借出 `*const c_char` 給 C 讀——只是**借**，`msg` 還活著，函式結束 Rust 自動 free，C 不准 free。
- **`into_raw()` / `from_raw()` 的所有權轉移**：C 要**持有**或需要 `*mut`（可寫）時，`into_raw()` 把 buffer **所有權交出去**，Rust 不再自動 free。用完必須 `from_raw()` 收回，Rust 才會 drop——否則 **memory leak**。就是 `malloc`/`free` 配對的直覺：所有權在誰手上誰負責 free。
- **`CStr::from_ptr(c_greeting())`**：C 回一個**它自己擁有**的字串（這裡 static 永遠活著）。Rust 用 `CStr::from_ptr` **借**來讀，`from_ptr` 是 `unsafe`——Rust 沒法驗證指標非 null、真的 NUL 結尾、讀的期間活著。**Rust 絕不能 free 這塊**（不是 Rust 配的）。誰配誰 free；搞錯方向就是 double free 或 free 錯 allocator。

**故意觸發失敗：C 字串不能含內部 NUL。** `CString::new` 會檢查——因為 C 字串靠 `\0` 標結尾，內容中間有 `\0` 就會被 C 當成提早結束，語意全毀。Rust 在建構時就攔下：

```rust
use std::ffi::CString;
fn main() {
    let bad = CString::new("ab\0cd");   // 中間有 \0
    match bad {
        Ok(s)  => println!("ok: {:?}", s),
        Err(e) => println!("CString::new failed: {}", e),
    }
}
```

真跑：

```
CString::new failed: nul byte found in provided data at position: 2
```

`CString::new` 回傳 `Result`，逼你處理「這字串放不進 C 字串」的情況——這是 wrapper 在門口幫你擋掉一類 bug 的縮影。C 那邊你 `strcpy` 一個含 `\0` 的字串進去，沒人會告訴你出事了。

## libc crate 與 bindgen：別手抄 header

前面所有 `extern` 宣告都是手寫的。兩個工具讓你不用手抄。

**`libc` crate**：現成的 C 標準庫 + POSIX binding——`c_int` 這些型別別名、`O_RDONLY` 這些常數、`getpid`/`open`/`mmap` 這些函式宣告，全都幫你宣告好了。直接呼 glibc：

```rust
// Cargo.toml 加：libc = "0.2"
fn main() {
    unsafe {
        let pid = libc::getpid();                 // 直接呼叫 glibc getpid(2)
        println!("getpid() via libc = {}", pid);
        let key = std::ffi::CString::new("HOME").unwrap();
        let val = libc::getenv(key.as_ptr());     // getenv 回 *const c_char
        if !val.is_null() {
            let s = std::ffi::CStr::from_ptr(val).to_string_lossy();
            println!("getenv(HOME) = {}", s);
        }
    }
}
```

真跑（`cargo run`，offline 用已快取的 `libc 0.2.189`）：

```
getpid() via libc = 265149
getenv(HOME) = /home/ypp
```

`libc::getpid()` 就是走 FFI 呼 glibc 的 `getpid`，仍是 `unsafe`（所有 C 函式都是）。`getenv` 示範了「C 回一個它管的 `char*`」——你得自己 `is_null` 檢查 + `CStr::from_ptr` 包，`libc` 不會幫你把 C 慣例翻成 Rust 慣例，它只給你原始的 C 介面。

**`bindgen`**：從 C header **自動生成** Rust `extern` 宣告和 `repr(C)` struct。當你要接一個大型 C 庫（OpenSSL、libpng），手抄幾百個宣告是災難且易錯——`bindgen` 讀 header，用 libclang 解析，吐出對應的 Rust。真跑一個小 header：

```c
// mathlib.h
#include <stdint.h>
typedef struct { int32_t num; int32_t den; } Fraction;
Fraction frac_add(Fraction a, Fraction b);
int32_t frac_gcd(int32_t a, int32_t b);
```

```
$ bindgen mathlib.h -o bindings.rs
```

生出來的 `bindings.rs`（擷取關鍵部分）：

```rust
#[repr(C)]
pub struct Fraction {
    pub num: i32,
    pub den: i32,
}
extern "C" {
    pub fn frac_add(a: Fraction, b: Fraction) -> Fraction;
}
extern "C" {
    pub fn frac_gcd(a: i32, b: i32) -> i32;
}
```

`bindgen` 自動加了 `#[repr(C)]`、把 `int32_t` 對應成 `i32`、把 struct by-value 的簽章正確生出來——這些手抄很容易錯的細節它全對。實務上配 `build.rs` 在 build 時自動跑 `bindgen`，header 一改 binding 就跟著更新，杜絕「Rust 宣告和 C header 不同步」這個 FFI 頭號 bug 來源。

> **注意**：`bindgen` 需要系統裝 `libclang`（它靠 clang 解析 C）。本章的 `bindgen 0.69.5` 在 WSL 上實測可跑；若你的環境沒 libclang，`bindgen` 會報找不到 clang，得先 `apt install libclang-dev`。

## 把 unsafe C API 包成安全 wrapper

這是全章的高潮，也是 Rust FFI 的真正價值所在。前面所有呼叫都在 `unsafe` 裡、都要呼叫端自己小心。**安全 wrapper 的目標：把 unsafe 收進封裝內部幾行，對外提供一個編譯器能重新罩住的 100% safe 介面**——讓破口只在 wrapper 內，用的人回到安全世界。

三條設計原則：

1. **生命週期靠 RAII（`Drop`）管**：C 資源「開了要關」（`new`/`free` 配對），對應 Rust 的 `Drop`——離開作用域自動 free，杜絕忘記 free 和 double free。這正是你 C++ RAII 的直覺。
2. **invariant 檢查放建構子/門口**：C 可能回 null、index 可能越界——這些檢查放進 wrapper 的建構子和方法，一次檢查，之後內部 code 就能假設 invariant 成立。
3. **裸指標藏在私有欄位**：外部拿不到裸指標，就沒法繞過你的檢查亂搞。

拿一個典型的「有生命週期的 C 資源」——一個要 `new`/`free` 的 buffer（`[C]` 那兩行是 C 端印到 stderr 的痕跡，證明 new/free 真被呼到）：

```c
// buf_c.c
typedef struct { int32_t *data; size_t len; } CBuf;
CBuf *cbuf_new(size_t n) {
    CBuf *b = malloc(sizeof(CBuf));
    b->data = calloc(n, sizeof(int32_t));
    b->len = n;
    fprintf(stderr, "[C] cbuf_new(%zu)\n", n);
    return b;
}
void cbuf_set(CBuf *b, size_t i, int32_t v) { if (i < b->len) b->data[i] = v; }
int32_t cbuf_get(CBuf *b, size_t i) { return (i < b->len) ? b->data[i] : -1; }
void cbuf_free(CBuf *b) { fprintf(stderr, "[C] cbuf_free\n"); free(b->data); free(b); }
```

Rust wrapper。注意 `CBuf` 用**不透明型別**（`[u8; 0]`）——Rust 完全不看 C struct 內部，只持有指標：

```rust
use std::os::raw::c_int;
use std::ptr::NonNull;

// 不透明 C 型別：Rust 只持有指標，不碰內部佈局
#[repr(C)]
struct CBuf { _private: [u8; 0] }
extern "C" {
    fn cbuf_new(n: usize) -> *mut CBuf;
    fn cbuf_set(b: *mut CBuf, i: usize, v: c_int);
    fn cbuf_get(b: *mut CBuf, i: usize) -> c_int;
    fn cbuf_free(b: *mut CBuf);
}

// 安全 wrapper：裸指標私有，invariant 在門口，生命週期靠 Drop
pub struct Buf { ptr: NonNull<CBuf>, len: usize }
impl Buf {
    pub fn new(n: usize) -> Self {
        let p = unsafe { cbuf_new(n) };
        // invariant：null 直接 panic，之後所有方法都能假設非 null
        let ptr = NonNull::new(p).expect("cbuf_new returned null");
        Buf { ptr, len: n }
    }
    pub fn set(&mut self, i: usize, v: i32) {
        assert!(i < self.len, "index out of bounds");   // 邊界檢查在門口
        unsafe { cbuf_set(self.ptr.as_ptr(), i, v) };
    }
    pub fn get(&self, i: usize) -> i32 {
        assert!(i < self.len, "index out of bounds");
        unsafe { cbuf_get(self.ptr.as_ptr(), i) }
    }
}
impl Drop for Buf {
    fn drop(&mut self) {
        unsafe { cbuf_free(self.ptr.as_ptr()) };        // RAII：自動釋放
    }
}

fn main() {
    let mut b = Buf::new(3);
    b.set(0, 10);
    b.set(2, 99);
    println!("b[0]={}, b[1]={}, b[2]={}", b.get(0), b.get(1), b.get(2));
    // b 離開 main 時 Drop 自動呼 cbuf_free，不用手動釋放
}
```

真跑（stderr + stdout 合併）：

```
[C] cbuf_new(3)
b[0]=10, b[1]=0, b[2]=99
[C] cbuf_free
```

`[C] cbuf_new(3)` 在開頭、`[C] cbuf_free` 在結尾——**我從沒手動呼 `cbuf_free`，是 `Drop` 在 `b` 離開 `main` 時自動呼的**。這就是 RAII 管 C 資源生命週期。對外，`Buf` 是個純 safe 型別：`new`/`set`/`get` 都不用 `unsafe`，越界會 panic 不會記憶體損毀，忘記 free 不可能（編譯器保證 `Drop` 會跑）。unsafe 只剩 wrapper 內那四行 `extern` 呼叫。**這就是「把破口封起來」的具體長相**，也是 `std::fs::File` 對 `open`/`close`、`Box` 對 `malloc`/`free` 在做的同一件事。

用 `NonNull` 而非 `*mut CBuf` 有兩個好處：建構子 `NonNull::new` 強迫你處理 null（回 `Option`）；且 `NonNull` 讓 `Option<Buf>` 能吃 niche 優化（[Ch 18](./18-unsafe-advanced.md) 提過）。

## callback：把 Rust 函式傳給 C

很多 C API 收 function pointer（`qsort`、事件迴圈、`pthread_create`）。你要傳 Rust 函式給 C 當 callback——這個 Rust 函式必須是 `extern "C"`（用 C ABI，C 才呼得動）。C 端對每個元素呼叫 callback 累加：

```c
// cb_c.c
#include <stdint.h>
#include <stddef.h>
typedef int32_t (*transform_fn)(int32_t);
int64_t c_sum_transform(const int32_t *arr, size_t n, transform_fn cb) {
    int64_t total = 0;
    for (size_t i = 0; i < n; i++) total += cb(arr[i]);
    return total;
}
```

```rust
use std::os::raw::c_int;
// 傳給 C 的 callback 必須 extern "C"
extern "C" fn square(x: c_int) -> c_int { x * x }
extern "C" {
    fn c_sum_transform(
        arr: *const c_int, n: usize,
        cb: extern "C" fn(c_int) -> c_int,   // 型別是 extern "C" fn
    ) -> i64;
}
fn main() {
    let data = [1, 2, 3, 4, 5];
    let sum = unsafe { c_sum_transform(data.as_ptr(), data.len(), square) };
    println!("sum of squares 1..=5 = {}", sum);   // 1+4+9+16+25 = 55
}
```

真跑：`sum of squares 1..=5 = 55`。

關鍵：`square` 標了 `extern "C"`，函式指標型別是 `extern "C" fn(c_int) -> c_int`——C 用 C ABI 呼它，兩邊 calling convention 一致才呼得通。傳 `data.as_ptr()` 給 C 讀陣列，C 迴圈對每個元素回呼 `square`。

**重要限制：能傳給 C 的只有 `extern "C" fn`，不能直接傳捕獲環境的 Rust 閉包**（capturing closure）——閉包帶著捕獲的狀態，不是單純一個函式指標，C 的 function pointer 型別裝不下。要傳「帶狀態的 callback」的標準模式是「function pointer + `void* user_data`」：C API 收裸函式指標 + 一個 `void*` context，你把狀態塞進 `user_data`、在 trampoline 裡取回。GTK、libuv 這種 C 事件庫到處是這模式。

## 對比與取捨

| 場景 | 用什麼 | 為什麼 |
|---|---|---|
| 呼叫一支 C 函式 | `extern "C" { fn ... }` + `unsafe` | 宣告簽章，link 進來 |
| 匯出 Rust 給 C | `#[no_mangle] pub extern "C"` + staticlib | 關 mangling，C 找得到符號 |
| 交換 struct | `#[repr(C)]` | 保證佈局和 C 一致 |
| Rust → C 字串 | `CString`（擁有）/ `.as_ptr()`（借） | NUL 結尾，所有權在 Rust |
| C → Rust 字串 | `CStr::from_ptr`（借，unsafe） | 只讀不 free，C 擁有 |
| 大型 C 庫 | `bindgen` 生 binding | 別手抄，杜絕不同步 |
| C 標準庫/POSIX | `libc` crate | 現成宣告 |
| 有生命週期的 C 資源 | wrapper + `Drop` (RAII) | 自動 free，封住破口 |
| 傳 callback | `extern "C" fn`（不捕獲） | C ABI；帶狀態用 `void* user_data` |

總原則：**FFI 呼叫本身永遠 unsafe，但你的責任是把它封裝成 safe 介面**。一個好的 FFI wrapper crate（`-sys` crate + 安全封裝 crate 的兩層結構是 Rust 生態的慣例），對用戶而言和純 Rust 庫無異。

## 踩雷集錦

1. **以為過了橋 Rust 還在保護你**：FFI 邊界之後 borrow checker、lifetime、「型別 T 合法」全部失效。C 回一個指標，Rust 不知道它活多久、非不非 null、指向的資料合不合法——**這些全是你的責任**。最常見錯誤：`CStr::from_ptr(p)` 之後 `p` 指的 C 記憶體被 C free 掉了，你手上的 `&CStr` 變懸空。FFI 邊界是「認識論斷點」：對岸的一切編譯器都不信任，你也不該信任，每個假設都要自己驗。

2. **struct 忘了 `#[repr(C)]`**：Rust 預設佈局未指定，可能重排欄位省 padding，和 C 對不上。而且**靜默出錯**——ABI 層沒型別，你讀 `x` 讀到的其實是 C 的 `dist` 的一半 bit。凡是跨 FFI 的 struct 一律 `#[repr(C)]`。這條和「簽章 mismatch」是 FFI 兩大靜默 bug，都靠 `bindgen` 自動生來避免。

3. **字串所有權搞錯，double free 或 leak**：`CString::into_raw()` 交出所有權後不 `from_raw()` 收回 = leak；把 C 用 `malloc` 配的字串拿去給 Rust `CString::from_raw`（不同 allocator）= UB。鐵律：**誰配誰 free，用同一個 allocator**。C 配的用 C 提供的 free 函式（可能要你的 wrapper 呼 C 的 `free_string`），Rust 配的 Rust 收。搞不清誰擁有，先問「這塊記憶體是誰 `malloc`/`Box::new` 的」。

4. **`CStr::from_ptr` 對 null 或非 NUL 結尾指標**：`from_ptr` 假設指標非 null 且在某處有 `\0`。傳 null → 解引用 null；傳一塊沒有 `\0` 的 buffer → 一路讀到段錯誤（或讀出隔壁記憶體）。C 函式回 `char*` 時，**先 `is_null()` 檢查**再 `from_ptr`——這就是 wrapper 該在門口做的 invariant 檢查。

5. **panic 跨 FFI 邊界**：Rust 的 `panic` 預設會 unwind stack。如果你的 `extern "C"` 函式（被 C 呼叫的 callback 或匯出函式）內部 panic，unwind 跨進 C 的 stack frame——C 不懂 Rust 的 unwind，過去是 **UB**。現在（1.97）Rust 在 `extern "C"` 邊界把「試圖 unwind 出去」轉成 **abort**。真跑一個會 panic 的 callback：

   ```rust
   extern "C" fn bad(x: c_int) -> c_int {
       if x == 3 { panic!("boom in callback"); }
       x * x
   }
   ```

   被 C 的 `c_sum_transform` 呼到 `x==3` 時（截取關鍵行）：

   ```
   thread 'main' panicked at panic_cb.rs:3:17:
   boom in callback
   thread 'main' panicked at library/core/src/panicking.rs:225:5:
   panic in a function that cannot unwind
   ...
   thread caused non-unwinding panic. aborting.
   ```

   `panic in a function that cannot unwind` → 直接 abort（exit code 134 = 128+SIGABRT(6)）。這比舊版的 UB 好（至少確定行為），但整個程式死掉。**正解：`extern "C"` 函式內部用 `std::panic::catch_unwind` 把 panic 攔在 Rust 這側，轉成錯誤碼回傳給 C**，別讓 panic 有機會跨橋。

## 進階：再往深一層

**`-sys` crate 兩層慣例**：Rust 生態接 C 庫的標準做法是拆兩層——`foo-sys` crate 只放 `bindgen` 生的原始 unsafe binding（`extern` 宣告 + `build.rs` link 那個 C 庫），上面再包一個 `foo` crate 提供安全 API（RAII wrapper + 型別轉換）。這樣底層 binding 和安全封裝分離，好維護。看 `openssl-sys`/`openssl`、`libgit2-sys`/`git2` 就是這結構。你自己接 C 庫時照抄這模式。

**`build.rs` + `cc` crate 自動編 C**：本章手動 `cc -c`/`ar`，生產專案用 `build.rs`（cargo 的建構腳本）配 `cc` crate 在 `cargo build` 時自動編 C 源碼並 link，或配 `bindgen` 自動生 binding。`cc::Build::new().file("c_side.c").compile("cside")` 一行就搞定編譯 + 打包 + 告訴 cargo link。這是實務標配，本章為了無外部依賴示範才手動 link。

**`catch_unwind` 的 FFI 邊界防護**：承踩雷 5，健壯的 `extern "C"` 匯出函式應該長這樣：把 body 包進 `std::panic::catch_unwind(|| { ... })`，捕到 panic 就回一個 C 能理解的錯誤碼（如 `-1` 或設 errno），絕不讓 panic 逃出去。這是寫「給 C 呼叫的 Rust 庫」的必備防護，尤其你的 Rust code 可能因為 index 越界、`unwrap` on `None` 意外 panic。

**面試常問**：「Rust FFI 呼叫為什麼一定要 unsafe？」——因為編譯器沒法驗證 C 那側：簽章對不對、回傳指標活不活、有沒有搞破壞，全在 Rust 的分析範圍外。`unsafe` 是你簽字「我驗過這些契約」。進階答案要能講到：FFI 是 Rust 安全保證的邊界，wrapper 的價值就是把這個不可驗證的破口封裝成一個可驗證的安全介面，讓 unsafe 的範圍最小化（收進幾行封裝內）。

## 動手練習

1. **簽章 mismatch 的靜默災難**：把本章 `c_gcd` 的 Rust 宣告故意改成 `fn c_gcd(a: c_int) -> c_int`（少第二個參數），編譯、執行，看它印出什麼。體會「ABI 層沒型別檢查、mismatch 不報錯只給垃圾值」。再把型別改成 `f64` 參數（浮點走 xmm 暫存器而非整數暫存器），觀察結果更離譜——這是 calling convention 具體長什麼樣的實感。

2. **字串來回 + 所有權**：寫一個 C 函式 `char *c_reverse(const char *s)`，它 `malloc` 一塊新記憶體回傳反轉的字串。在 Rust 端呼叫它、用 `CStr::from_ptr` 讀出來，然後**思考誰該 free 這塊**（C `malloc` 的 → Rust 不能用 `CString::from_raw`，得呼 C 提供的 free）。故意漏掉 free，用 `valgrind` 確認 leak（Miri 不支援真 FFI，見 Ch 20）。

3. **把 callback 包成收閉包的 safe 介面**：在本章 `c_sum_transform` 上包一層 Rust `fn sum_transform(data: &[i32], f: extern "C" fn(i32) -> i32) -> i64`，讓呼叫端不用寫 `unsafe`。想一想為什麼參數還是得是 `extern "C" fn` 而不能是 `impl Fn`——這是理解「為什麼 C 收不了捕獲閉包」的關鍵。

## 本章重點整理

- **`extern "C"` 呼叫 C**：宣告簽章（不定義），呼叫一律 `unsafe`。link 走 `rustc -L . -l static=xxx`（`rustc` 不吃 `.o` 位置參數）。能運作的根本是雙方共用 System V AMD64 ABI（參數 rdi/rsi/rdx...，回傳 rax）。
- **`#[no_mangle] pub extern "C"` 匯出 Rust 給 C**：關 name mangling 讓符號名乾淨，Rust 編 staticlib 由 C link（補 `-lpthread -ldl`）。是「用 Rust 替換 C」的手法。
- **型別對應**：`c_int`/`c_char`（signedness 平台相依）/`repr(C)` struct（護照，忘了就靜默錯）。字串靠 `CString`（擁有，Rust→C）/`CStr`（借，C→Rust 唯讀）；`into_raw`/`from_raw` 轉移所有權，誰配誰 free 用同 allocator。`libc` 給現成宣告、`bindgen` 從 header 自動生 binding 杜絕不同步。
- **安全 wrapper**：把 unsafe 收進封裝幾行，對外 100% safe。三原則：RAII（`Drop`）管生命週期、invariant 檢查在門口（null/邊界）、裸指標私有。這是 `File`/`Box`/`-sys` crate 在做的事。
- **FFI 是 Rust 安全保證的破口**：過橋後型別/lifetime/null/free 全是你的責任，編譯器不信任對岸。callback 要 `extern "C" fn`（不能傳捕獲閉包，帶狀態用 `void* user_data`）；panic 不能跨邊界（1.97 會 abort，正解是 `catch_unwind` 攔在 Rust 側）。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼呼叫 `extern "C"` 函式一定要 `unsafe`」，並講到「編譯器無法驗證對岸」這層。
- [ ] 面試問「Rust 和 C 怎麼互相呼叫」，能講清楚兩個方向（`extern "C"` 呼 C、`#[no_mangle]` 匯出）和底層是同一套 System V ABI。
- [ ] 能說出 `CString` 和 `CStr` 的分工，以及 `into_raw`/`from_raw` 的所有權轉移為什麼是 double free/leak 的雷區。
- [ ] 看到一個 unsafe C API，能說出把它包成安全 wrapper 的三個原則，並解釋 `Drop` 在裡面扮演的角色。
- [ ] 知道為什麼不能把捕獲閉包直接傳給 C，以及 panic 跨 FFI 邊界會發生什麼（1.97 的行為）。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件

- **《The Rustonomicon》「Foreign Function Interface」章** — 官方（[doc.rust-lang.org/nomicon/ffi.html](https://doc.rust-lang.org/nomicon/ffi.html)）
  - **讀哪裡**：整章。特別是「Calling Rust code from C」（對應本章反向匯出）、「Destructors」（RAII wrapper）、「Callbacks from C code to Rust functions」（本章 callback + `user_data` 模式的完整版）、「FFI and unwinding」（panic 跨邊界那條踩雷的權威說明）。
  - **學到什麼**：本章每個主題的權威深化版；`user_data` 帶狀態 callback 的完整範例本章只點到，這裡有完整 code。
  - **前提**：懂 Ch 17/18 的 unsafe 與裸指標；這是 unsafe FFI 的正門。

- **《The Rust Reference》「External blocks」與「ABI」** — 官方（[doc.rust-lang.org/reference/items/external-blocks.html](https://doc.rust-lang.org/reference/items/external-blocks.html)）
  - **讀哪裡**：`extern` 區塊語法、可用的 ABI 字串清單（`"C"`/`"system"`/`"C-unwind"` 的差異）、`#[link]` 屬性（在 code 裡指定 link 哪個庫，本章用 `rustc -l` 命令列做）。
  - **學到什麼**：`extern "C-unwind"` 何時該用（允許 unwind 跨邊界的新 ABI）、`#[link(name=..., kind=...)]` 的精確語意。
  - **前提**：懂本章基本 `extern "C"`；當你要控制 link 細節或選 ABI 時來查。

### 官方指南

- **《The `bindgen` User Guide》** — Rust 官方 rust-lang/rust-bindgen（[rust-lang.github.io/rust-bindgen](https://rust-lang.github.io/rust-bindgen/)）
  - **讀哪裡**：「Tutorial」整章（配 `build.rs` 自動生 binding 的標準流程，本章手動跑 `bindgen` 的生產版）；「Customizing」講怎麼控制生成（blocklist、newtype enum 等）。
  - **學到什麼**：把本章手動的 `bindgen mathlib.h` 變成 `build.rs` 自動化；接真實大型 C 庫時怎麼調教 `bindgen`（很多庫 raw 生出來要客製）。
  - **前提**：懂本章 `bindgen` 基本用法 + cargo 專案結構；要接真實 C 庫時的必讀。

### 書籍

- **《Rust for Rustaceans》第 12 章「Foreign Function Interfaces」** — Jon Gjengset（No Starch Press, 2021）
  - **這本的定位**：中階 Rust 最佳單本書，和本課定位重合。第 12 章專講 FFI。
  - **讀哪幾章**：第 12 章。它把 `repr` 對齊、`-sys` crate 兩層慣例、字串所有權、`bindgen`/`cbindgen`（反向：從 Rust 生 C header）講得比本章更系統；`panic` 跨邊界與 `catch_unwind` 也有深入討論。
  - **前提**：懂本章全部 + Ch 15 記憶體佈局；想寫生產級 FFI wrapper crate 時的下一步。

搞懂了 FFI 這個「Rust 安全保證的破口」，一個尖銳的問題浮現：跨過這條線之後，到底什麼算 UB？「型別 T 一定是合法 T」「借用不能懸空」這些規則在 unsafe/FFI 裡具體怎麼定義、怎麼被違反、怎麼被工具抓到？下一章進 Rust 的記憶體模型正題——Stacked Borrows / Tree Borrows 這套 unsafe code 的形式化規則，以及用 Miri 精準抓出你這章 FFI code 裡潛在的 UB。

→ [Ch 20 記憶體模型與 UB](./20-memory-model-ub.md)
