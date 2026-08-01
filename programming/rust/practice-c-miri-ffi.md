# 練習 C — 用 Miri 抓 UB 與包一個 C library

> **目標**：把 Part 3（[Ch 15–22](./15-memory-layout.md)）最實用的兩把武器一起操一遍——**Miri** 抓看不見的 UB、**FFI** 把 C library 包成安全 Rust。完成後你驗證了兩件事：(1) 你能在一段「編過、跑起來也不 crash」的 unsafe code 裡定位並修掉真正的 UB；(2) 你能把一組 raw `extern "C"` 介面封成一個外部再怎麼用都不會 UB 的安全 API。這正是日後 audit 別人 unsafe crate、以及自己包 C 依賴時每天在做的事。

> **環境**：`rustc 1.97.1`（stable）跑 FFI 部分、`nightly` + Miri（`cargo +nightly miri run`）跑 UB 部分，x86-64 Linux（WSL2）。本練習所有 code 與 Miri 報告都真跑過照貼。

## 背景與動機

兩個場景，都是真的：

- **場景 A**：你在 review 一個同事的 PR，裡面有一段 unsafe——`split_at_mut` 的手刻版、一個從裸指標建 slice 的函式。它編過了、CI 綠了、你本機跑也沒 crash。但它藏著 aliasing UB，某天在 `-O3` 或換個編譯器版本會爆成資料損毀。你怎麼在**合併之前**抓到它？答案是 Miri。
- **場景 B**：你要用一個只有 C 版本的 library（壓縮、密碼學、硬體驅動）。`bindgen` 幫你生了一堆 `unsafe extern "C"` 宣告，但那些全是裸指標、要手動 free、回傳錯誤碼而非 `Result`。直接讓整個專案到處寫 `unsafe` 是災難。正確做法是包一層——把 unsafe 關進一個小模組，對外只露安全 API。

這個練習兩段任務各打一個場景。

## 任務 A：用 Miri 抓出並修正 UB

### 規格

給你下面這段 code。它**編得過、跑起來不 crash、輸出看起來合理**。但它有兩處 UB。你的任務：

1. 用 `cargo +nightly miri run` 跑它，讀懂 Miri 的報告。
2. 定位兩處 UB，說明各是哪一類（aliasing 違規？越界？未對齊？）。
3. 修正它們，讓 `cargo +nightly miri run` **完全乾淨**（無任何 UB 報告），且輸出語意合理。

```rust
use std::slice;

// 想手刻一個「把 slice 切兩半、各自可變」的函式（類似 split_at_mut）
fn split_bad(v: &mut [i32]) -> (&mut [i32], &mut [i32]) {
    let len = v.len();
    let ptr = v.as_mut_ptr();
    let mid = len / 2;
    unsafe {
        (
            slice::from_raw_parts_mut(ptr, mid),
            slice::from_raw_parts_mut(ptr, len - mid),
        )
    }
}

fn read_oob() -> i32 {
    let arr = [10i32, 20, 30];
    let p = arr.as_ptr();
    unsafe { *p.add(5) }
}

fn main() {
    let mut data = [1, 2, 3, 4];
    let (a, b) = split_bad(&mut data);
    a[0] = 100;
    b[0] = 200;
    println!("a={:?} b={:?}", a, b);
    println!("oob = {}", read_oob());
}
```

### 驗收標準

- 修正後 `cargo +nightly miri run` 無 UB 報告。
- `split_bad` 修正後回傳的兩片**不重疊**，各自獨佔一段記憶體（這才是 `split_at_mut` 的語意）。
- `read_oob` 修正後只讀陣列界內的元素。
- 你能用一句話說清楚每處 UB 屬於哪一類、Miri 是靠什麼模型抓到的。

## 任務 B：把一個 C library 包成安全 Rust

### 規格

給你一個 C library（`csrc/xstack.c` + `xstack.h`），提供：

- 一個 int32 **stack**：`xstack_new(cap)` / `xstack_free` / `xstack_push`（回 0/-1）/ `xstack_pop`（回 0/-1，值透過 out 參數）/ `xstack_len`。
- 一個 **checksum**：`xchecksum(data, len)` 算 Fletcher-16。
- 一個 **greeting**：`xgreeting(name)` 吃 `const char*` 回 `const char*`。

C 標頭：

```c
typedef struct XStack XStack;   /* opaque：外界看不到內部欄位 */

XStack  *xstack_new(size_t capacity);
void     xstack_free(XStack *s);
int      xstack_push(XStack *s, int32_t v);    /* 0 成功、-1 滿 */
int      xstack_pop(XStack *s, int32_t *out);  /* 0 成功、-1 空 */
size_t   xstack_len(const XStack *s);
uint16_t xchecksum(const uint8_t *data, size_t len);
const char *xgreeting(const char *name);       /* 回 static buffer，不需 free */
```

你的任務：寫一個安全的 Rust wrapper，要求：

| 要求 | 具體 |
|---|---|
| unsafe 封印 | `extern "C"` 宣告藏在私有 `mod ffi` 裡；對外公開 API **零 `unsafe`** |
| RAII | `Stack` 用 `Drop` 自動呼叫 `xstack_free`，呼叫者不可能忘記釋放 |
| 錯誤用型別表達 | `new` 回 `Option<Stack>`（對應 C 的 NULL）、`push` 回 `Result<(), StackFull>`、`pop` 回 `Option<i32>` |
| slice 安全 | `checksum(&[u8])`：長度由 slice 保證，呼叫者無法傳錯 len |
| 字串安全 | `greeting(&str) -> Result<String, NulError>`：用 `CString` 轉入、`CStr` 轉出，含 `\0` 的輸入回 `Err` 而非 UB |
| opaque type | `XStack` 用 `_private: [u8; 0]` 表示，Rust 端不假設它的佈局 |

### 驗收標準

- 一個 `main` 能完整操作 stack（push 到滿、pop 到空）、算兩個 checksum、打招呼（含一個非法 `\0` 輸入），輸出正確。
- 對外 API 的使用者程式碼裡**一個 `unsafe` 都沒有**。
- `cargo run` 通過。

## 期望輸出範例

任務 A 修正後（Miri）：

```
a=[100, 2] b=[200, 4]
in-bounds = 30
```

任務 B：

```
len = 3
push 4th: Err(StackFull)
pop = Some(30)
pop = Some(20)
empty? false
greeting = Hello, Rust!
bad name is_err = true
checksum(hello world) = 0x1a60
checksum(empty) = 0x0000
```

## 如果你卡住了

**任務 A**：

1. 先別急著改，先跑 `cargo +nightly miri run`，把 Miri 指的行號與訊息看懂。它會用 `retag` / `borrow stack` 這種字眼——那是 Stacked Borrows 模型的語言（見 [Ch 20](./20-memory-model-ub.md)）。
2. `split_bad` 兩片都從 `ptr` 開始，想想：兩個 `&mut [i32]` 覆蓋**同一塊**記憶體，違反了 Rust 最核心的哪條規則？（提示：[Ch 3](./03-borrowing-references.md) 的別名規則。）
3. `read_oob` 那個 `p.add(5)`——陣列只有 3 個元素，`add(5)` 產生的指標指向哪？就算不 deref，光是「用指標算術造出一個越過 allocation 尾端的指標」在 Rust 就已經是 UB。

**任務 B**：

1. 先用 `bindgen` 心智模型手寫 `extern "C"`：C 的 `size_t` → Rust `usize`，`int` → `c_int`，`int32_t` → `i32`，`const char*` → `*const c_char`，opaque `XStack*` → `*mut ffi::XStack`。
2. RAII 的關鍵是 `impl Drop`——把 `xstack_free` 放進去，`Stack` 一離開作用域就自動釋放。這對應 C++ 的解構子、對應本課 [Ch 12](./12-core-traits.md) 的 `Drop`。
3. `push`/`pop`/`new` 回傳的 C 錯誤碼（-1/NULL）不要往外漏，就地翻譯成 `Result`/`Option`。這是「把 C 的 out-of-band 錯誤慣例翻成 Rust 型別」的核心動作。
4. `greeting`：Rust `&str` **不是** null-terminated，不能直接當 `char*` 傳。用 `CString::new(name)` 造一個 C 相容的 null-terminated 字串；它在 `name` 含內嵌 `\0` 時回 `Err`（因為 C 字串不能有內嵌 `\0`）。回來的 `*const c_char` 用 `CStr::from_ptr` 讀，`.to_string_lossy().into_owned()` 複製成 owned `String`（不要把 C 的 static buffer 生命週期帶進 Rust）。

## 實作步驟建議

### Step 1（任務 A）：跑 Miri，讀懂報告

不改任何 code，先 `cargo +nightly miri run`。把它報的 UB 種類與行號抄下來。它一次只報第一個遇到的 UB，修掉一個再跑會露出下一個。

### Step 2（任務 A）：修 aliasing

`split_bad` 的第二片要從 `ptr.add(mid)` 開始，長度 `len - mid`。這樣兩片就不重疊，兩個 `&mut` 各自獨佔——Stacked Borrows 就不會被違反。

### Step 3（任務 A）：修越界

`read_oob` 改成讀界內 index（如 `p.add(2)`）。再跑 Miri 確認全乾淨。

### Step 4（任務 B）：建專案骨架

`cargo new`，把 C 檔放 `csrc/`，寫一個 `build.rs` 編 C 成 static lib 並連結。私有 `mod ffi` 放 `extern "C"` 宣告與 opaque `XStack`。

### Step 5（任務 B）：包安全層

`Stack` 結構（持 `*mut ffi::XStack`）+ `with_capacity`/`push`/`pop`/`len` + `impl Drop`。再加 `checksum(&[u8])` 與 `greeting(&str)`。寫 `main` 跑一輪驗收。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>任務 A 參考解答（含 Miri 報告）</summary>

**原始版跑 Miri 的實際報告**（第一個 UB：aliasing）：

```
error: Undefined Behavior: trying to retag from <391> for Unique permission at
       alloc169[0x0], but that tag does not exist in the borrow stack for this location
  --> src/main.rs:9:9
   |
 9 | /         (
10 | |             slice::from_raw_parts_mut(ptr, mid),
11 | |             ...
12 | |             slice::from_raw_parts_mut(ptr, len - mid),
13 | |         )
   | |         ^ this error occurs as part of retag at alloc169[0x0..0x8]
   |           while retagging field .0
   |
help: <391> was created by a Unique retag at offsets [0x0..0x8]
  --> src/main.rs:10:13
help: <391> was later invalidated at offsets [0x0..0x8] by a Unique retag
  --> src/main.rs:12:13
```

翻譯：第 10 行造的第一片 `&mut`（tag `<391>`）覆蓋 offset `[0x0..0x8]`；第 12 行造第二片時，因為它**也**從 offset 0 開始，把第一片的 tag 從 borrow stack 上「invalidate」掉了。於是第一片變成非法引用——這就是 aliasing UB。第二個 UB 是 `read_oob` 的 `p.add(5)`：越過 allocation 尾端。

**修正版**：

```rust
use std::slice;

// 修正 1：第二片從 ptr.add(mid) 開始，兩片不重疊 → 兩個 &mut 各自獨佔一段
fn split_good(v: &mut [i32]) -> (&mut [i32], &mut [i32]) {
    let len = v.len();
    let ptr = v.as_mut_ptr();
    let mid = len / 2;
    unsafe {
        (
            slice::from_raw_parts_mut(ptr, mid),
            slice::from_raw_parts_mut(ptr.add(mid), len - mid),
        )
    }
}

// 修正 2：index 在界內
fn read_in_bounds() -> i32 {
    let arr = [10i32, 20, 30];
    let p = arr.as_ptr();
    unsafe { *p.add(2) }   // index 2 在 [0,3)
}

fn main() {
    let mut data = [1, 2, 3, 4];
    let (a, b) = split_good(&mut data);
    a[0] = 100;
    b[0] = 200;
    println!("a={:?} b={:?}", a, b);
    println!("in-bounds = {}", read_in_bounds());
}
```

修正後 `cargo +nightly miri run` 的實際輸出（無任何 UB 報告）：

```
a=[100, 2] b=[200, 4]
in-bounds = 30
```

**兩處 UB 的分類**：
- `split_bad`：**aliasing 違規**——兩個 `&mut` 指向重疊記憶體，違反「同一時刻至多一個 `&mut`」。Miri 靠 **Stacked Borrows** 模型（追蹤每個指標的 borrow tag，見 [Ch 20](./20-memory-model-ub.md)）抓到。
- `read_oob`：**越界指標算術 / 越界讀**——`p.add(5)` 造出的指標越過 allocation，光是造出來就 UB，deref 更是。Miri 靠追蹤每個 allocation 的界限抓到。

**為什麼普通執行抓不到**：兩處 UB 在這次執行裡剛好沒撞爛任何東西（`add(5)` 讀到的是 stack 上相鄰的合法 bytes、重疊 `&mut` 只是各寫各的 offset 0）。這正是 UB 最危險的地方——**「這次沒爆」不代表「沒有 UB」**，換個優化等級/編譯器版本就可能爆。Miri 是 model-based，不靠「這次有沒有撞爛」，所以抓得到。

</details>

<details>
<summary>任務 B 參考解答（C library + build.rs + 安全 wrapper，含輸出）</summary>

**`csrc/xstack.h`**：

```c
#ifndef XSTACK_H
#define XSTACK_H
#include <stddef.h>
#include <stdint.h>

typedef struct XStack XStack;

XStack  *xstack_new(size_t capacity);
void     xstack_free(XStack *s);
int      xstack_push(XStack *s, int32_t v);    /* 0 成功、-1 滿 */
int      xstack_pop(XStack *s, int32_t *out);  /* 0 成功、-1 空 */
size_t   xstack_len(const XStack *s);
uint16_t xchecksum(const uint8_t *data, size_t len);
const char *xgreeting(const char *name);
#endif
```

**`csrc/xstack.c`**：

```c
#include "xstack.h"
#include <stdlib.h>
#include <stdio.h>

struct XStack { int32_t *buf; size_t cap; size_t len; };

XStack *xstack_new(size_t capacity) {
    if (capacity == 0) return NULL;
    XStack *s = malloc(sizeof(XStack));
    if (!s) return NULL;
    s->buf = malloc(capacity * sizeof(int32_t));
    if (!s->buf) { free(s); return NULL; }
    s->cap = capacity; s->len = 0;
    return s;
}
void xstack_free(XStack *s) { if (!s) return; free(s->buf); free(s); }
int xstack_push(XStack *s, int32_t v) {
    if (s->len == s->cap) return -1;
    s->buf[s->len++] = v; return 0;
}
int xstack_pop(XStack *s, int32_t *out) {
    if (s->len == 0) return -1;
    *out = s->buf[--s->len]; return 0;
}
size_t xstack_len(const XStack *s) { return s->len; }

uint16_t xchecksum(const uint8_t *data, size_t len) {
    uint16_t sum1 = 0, sum2 = 0;              /* Fletcher-16 */
    for (size_t i = 0; i < len; i++) {
        sum1 = (sum1 + data[i]) % 255;
        sum2 = (sum2 + sum1)    % 255;
    }
    return (sum2 << 8) | sum1;
}
const char *xgreeting(const char *name) {
    static char buf[128];
    snprintf(buf, sizeof buf, "Hello, %s!", name ? name : "world");
    return buf;
}
```

**`build.rs`**（不用 `cc` crate，直接呼叫 `cc`/`ar`，避開網路依賴）：

```rust
use std::env;
use std::process::Command;

fn main() {
    let out = env::var("OUT_DIR").unwrap();
    let ok = Command::new("cc")
        .args(["-c", "-O2", "-fPIC", "csrc/xstack.c", "-o"])
        .arg(format!("{}/xstack.o", out))
        .status().unwrap().success();
    assert!(ok);
    let ok = Command::new("ar")
        .args(["crus"])
        .arg(format!("{}/libxstack.a", out))
        .arg(format!("{}/xstack.o", out))
        .status().unwrap().success();
    assert!(ok);
    println!("cargo:rustc-link-search=native={}", out);
    println!("cargo:rustc-link-lib=static=xstack");
    println!("cargo:rerun-if-changed=csrc/xstack.c");
    println!("cargo:rerun-if-changed=csrc/xstack.h");
}
```

**`src/main.rs`**（安全 wrapper）：

```rust
use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_int};

// ===== 原始 FFI 宣告：全部 unsafe，藏在私有 module，不對外 export =====
mod ffi {
    use std::os::raw::{c_char, c_int};
    #[repr(C)]
    pub struct XStack { _private: [u8; 0] }   // opaque：Rust 端不假設內部佈局
    unsafe extern "C" {
        pub fn xstack_new(capacity: usize) -> *mut XStack;
        pub fn xstack_free(s: *mut XStack);
        pub fn xstack_push(s: *mut XStack, v: i32) -> c_int;
        pub fn xstack_pop(s: *mut XStack, out: *mut i32) -> c_int;
        pub fn xstack_len(s: *const XStack) -> usize;
        pub fn xchecksum(data: *const u8, len: usize) -> u16;
        pub fn xgreeting(name: *const c_char) -> *const c_char;
    }
}

// ===== 對外安全 API：零 unsafe，錯誤用型別表達 =====
#[derive(Debug)]
pub struct StackFull;

pub struct Stack { raw: *mut ffi::XStack }

impl Stack {
    pub fn with_capacity(cap: usize) -> Option<Stack> {
        let raw = unsafe { ffi::xstack_new(cap) };
        if raw.is_null() { None } else { Some(Stack { raw }) }   // NULL → None
    }
    pub fn push(&mut self, v: i32) -> Result<(), StackFull> {
        let rc = unsafe { ffi::xstack_push(self.raw, v) };
        if rc == 0 { Ok(()) } else { Err(StackFull) }
    }
    pub fn pop(&mut self) -> Option<i32> {
        let mut out = 0i32;
        let rc = unsafe { ffi::xstack_pop(self.raw, &mut out) };
        if rc == 0 { Some(out) } else { None }
    }
    pub fn len(&self) -> usize { unsafe { ffi::xstack_len(self.raw) } }
    pub fn is_empty(&self) -> bool { self.len() == 0 }
}

impl Drop for Stack {
    // RAII：離開作用域自動 free，呼叫者不可能洩漏
    fn drop(&mut self) { unsafe { ffi::xstack_free(self.raw) } }
}

pub fn checksum(data: &[u8]) -> u16 {
    // len 由 slice 保證正確，呼叫者無法傳錯
    unsafe { ffi::xchecksum(data.as_ptr(), data.len()) }
}

pub fn greeting(name: &str) -> Result<String, std::ffi::NulError> {
    let c_name = CString::new(name)?;                      // 內嵌 \0 → Err
    let p = unsafe { ffi::xgreeting(c_name.as_ptr()) };
    // C 回 static buffer；複製成 owned String，不把 C 的生命週期帶進 Rust
    Ok(unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned())
}

fn main() {
    let mut s = Stack::with_capacity(3).expect("cap>0");
    s.push(10).unwrap();
    s.push(20).unwrap();
    s.push(30).unwrap();
    println!("len = {}", s.len());
    println!("push 4th: {:?}", s.push(40));   // 滿了
    println!("pop = {:?}", s.pop());
    println!("pop = {:?}", s.pop());
    println!("empty? {}", s.is_empty());

    println!("greeting = {}", greeting("Rust").unwrap());
    println!("bad name is_err = {}", greeting("a\0b").is_err());

    println!("checksum(hello world) = 0x{:04x}", checksum(b"hello world"));
    println!("checksum(empty) = 0x{:04x}", checksum(b""));

    let _: c_int = 0;
    let _: c_char = 0;
}
```

`cargo run` 的實際輸出：

```
len = 3
push 4th: Err(StackFull)
pop = Some(30)
pop = Some(20)
empty? false
greeting = Hello, Rust!
bad name is_err = true
checksum(hello world) = 0x1a60
checksum(empty) = 0x0000
```

**解答說明**：
- `mod ffi` 私有 → 外部程式碼看不到那些 `extern` 函式，只能透過安全 API。這是「封印 unsafe」的關鍵動作。
- `Stack` 的 `raw` 是 private，且建構時保證非 null（`with_capacity` 過濾掉 NULL）。之後 `push`/`pop`/`len` 都能安全假設 `raw` 有效——這是我們維持的 invariant，跟 [Ch 21](./21-unsafe-abstractions.md) 的 `MyVec` 同一個道理。
- `Drop` 讓釋放不可能被忘記。C 的使用者要記得 `xstack_free`，Rust 使用者不用——編譯器保證。
- `greeting` 用 `CString`/`CStr` 處理 Rust ↔ C 字串的邊界。Rust `&str` 不是 null-terminated 且允許內嵌 `\0`；C 字串兩者相反。`CString::new` 在內嵌 `\0` 時回 `Err(NulError)`，把一個潛在 UB 變成型別安全的錯誤。
- `0x1a60` 是 `"hello world"` 的 Fletcher-16 值，由 C 端算出（不是我編的，是跑出來的）；空 slice checksum 為 `0x0000`（迴圈不執行，兩個 sum 都 0）。

</details>

## 測試用例

任務 A：

| 情況 | 修正前 | 修正後 |
|---|---|---|
| `cargo run`（普通執行） | 看起來正常輸出 | 正常輸出 |
| `cargo +nightly miri run` | 報 Stacked Borrows aliasing UB | 乾淨無報告 |
| `split` 兩片是否重疊 | 重疊（UB） | 不重疊 |
| `read` index | 5（越界） | 2（界內） |

任務 B：

| 操作 | 輸入 | 預期輸出 | 說明 |
|---|---|---|---|
| push 到滿 | cap=3, push 4 次 | 第 4 次 `Err(StackFull)` | C 的 -1 翻成 `Result` |
| pop 到空 | 連續 pop | 空時回 `None` | C 的 -1 翻成 `Option` |
| `with_capacity(0)` | cap=0 | `None` | C 回 NULL |
| checksum 正常 | `b"hello world"` | `0x1a60` | Fletcher-16 |
| checksum 邊界 | `b""` | `0x0000` | 空輸入 |
| greeting 正常 | `"Rust"` | `"Hello, Rust!"` | CString→C→CStr |
| greeting 非法 | `"a\0b"`（內嵌 `\0`） | `Err(NulError)` | 不 UB，回錯誤 |

## 延伸挑戰（加分）

- **任務 A 進階**：把修好的 `split_good` 再用 `MIRIFLAGS="-Zmiri-tree-borrows" cargo +nightly miri run` 跑一次（Tree Borrows 模型，[Ch 20](./20-memory-model-ub.md) 提過），確認在更新的別名模型下也乾淨。
- **任務 B 進階一**：幫 `Stack` 實作 `Iterator`（pop 到空為止），讓 `for x in stack` 能用——注意所有權（by-value 消耗 vs 借用）。
- **任務 B 進階二**：把 wrapper 拆成一個 `-sys` crate（純 FFI 宣告）+ 一個高階 crate（安全 API），這是 Rust 生態包 C library 的標準兩層結構（`openssl-sys` + `openssl`）。
- **任務 B 進階三**：`Stack` 現在不是 `Send`/`Sync`（裸指標讓 auto trait 不自動實作）。想清楚它**該不該**是 `Send`——若底層 C 是執行緒安全的才 `unsafe impl Send`，否則保持不是。這連 [Ch 23](./23-threads-send-sync.md)。

## 自我檢核

- [ ] 能解釋為什麼一段 unsafe「編過、跑起來不 crash」仍可能有 UB，以及 Miri 為什麼抓得到而普通執行抓不到
- [ ] 能分辨 aliasing 違規、越界、未對齊三類 UB，並知道 Miri 報告裡 `retag`/`borrow stack` 對應的是哪個模型
- [ ] 能把一組 raw `extern "C"`（裸指標 + 錯誤碼 + 手動 free）封成 RAII + `Result`/`Option` 的安全 API，且對外零 `unsafe`
- [ ] 知道 `CString`/`CStr` 各自處理 Rust↔C 字串邊界的哪一半，內嵌 `\0` 為什麼要回 `Err`
- [ ] 能說出自己的 wrapper 維持了哪些 invariant（例如 `raw` 恆非 null），以及它跟 [Ch 21](./21-unsafe-abstractions.md) `MyVec` 的封印邏輯是同一套

做完這個練習，Part 3（記憶體佈局與 unsafe）就真的內化了：你不只讀得懂別人的 unsafe，還能用 Miri 驗它、能把 C 依賴安全地帶進 Rust。下一 Part 進入並發與非同步——`Send`/`Sync` 會把「哪些 unsafe 抽象能跨執行緒」這個問題正式攤開。

→ [Ch 23 執行緒與 Send/Sync](./23-threads-send-sync.md)
