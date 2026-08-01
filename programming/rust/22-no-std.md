# Ch 22 — no_std：embedded 與 kernel 場景

> **目標**：搞懂 `#![no_std]` 到底拿掉了什麼、留下什麼；知道 `core`/`alloc`/`std` 三層各自裝了什麼、常用型別在哪層；能自己寫出一個能編過 `x86_64-unknown-none` 的最小 no_std lib，補上 `#[panic_handler]` 與自訂 `#[global_allocator]`（一個真的 bump allocator）。學完你會理解：為什麼 embedded、bootloader、kernel（包含 [Part 6](./37-rust-for-linux-overview.md) 的 Rust-for-Linux）**必須**是 no_std，以及這跟 C 的 freestanding 是同一件事的兩種語言表達。

> **環境**：`rustc 1.97.1`（stable），target `x86_64-unknown-none`（已裝：`rustup target add x86_64-unknown-none`），edition 2021。本章每段能編的 code 都真的 `rustc --target x86_64-unknown-none` 編過，錯誤示範也真跑照貼。C 對照用 `gcc -ffreestanding -nostdlib`。

## 為什麼需要這個？

你在 C 裡寫過裸機或 kernel code 嗎？寫 bootloader、寫 kernel module、寫跑在 Cortex-M 上沒有 OS 的韌體時，你不能 `#include <stdio.h>`、不能 `malloc`、不能 `printf`——因為那些東西背後需要一個作業系統（syscall、動態連結、libc runtime）。那個環境叫 **freestanding**：只有語言本身與最基本的內建，沒有標準函式庫的 OS 相依部分。你用 `gcc -ffreestanding -nostdlib`，自己提供 `memcpy`/`memset`，自己寫 `_start`。

Rust 的 `std` 假設底下有一個作業系統：`std::fs`、`std::net`、`std::thread`、`std::println!`——全部要 syscall。`std::alloc` 的預設 allocator 要 `malloc`（在多數平台上）。當你要跑在**沒有 OS 的地方**——裸機 MCU、bootloader、kernel 內部——`std` 的一大半根本沒有底層可以呼叫。

`#![no_std]` 就是 Rust 版的 `-ffreestanding -nostdlib`。它告訴編譯器：「別連 `std`，我這裡沒有 OS。」這不是 niche 需求——**整個 Rust-for-Linux、整個 embedded Rust 生態（`cortex-m`、`embassy`、`rp2040-hal`）、所有 Rust 寫的 bootloader/UEFI app/kernel 都建立在 `#![no_std]` 上**。本課 Part 6 的 kernel module 之所以能用 Rust，前提就是這一章。

## 先建立直覺：std 是 core 上的一層 OS 皮

Rust 標準庫不是鐵板一塊，是**三層**堆疊：

```
┌─────────────────────────────────────────────┐
│  std        需要 OS：檔案、網路、執行緒、    │  ← 桌面/伺服器程式
│             println!、預設 heap allocator    │
├─────────────────────────────────────────────┤
│  alloc      需要 heap（你得提供 allocator）：│  ← 有 heap 的 no_std
│             Box, Vec, String, Rc, Arc,BTreeMap│    （kernel、大型 embedded）
├─────────────────────────────────────────────┤
│  core       零依賴，語言的骨：Option,Result, │  ← 所有 Rust code 的地基
│             slice, iterator, Ordering,       │    （裸機、bootloader）
│             整數/浮點運算, fmt (無配置版)     │
└─────────────────────────────────────────────┘
      ▲
      │ 每一層都 re-export 下一層。std::vec::Vec 其實就是 alloc::vec::Vec，
      │ std::option::Option 其實就是 core::option::Option。
```

關鍵洞察：**`std` 幾乎是 `core` + `alloc` + 「一層 OS 皮」**。你平常寫 `use std::vec::Vec`，`Vec` 的實作根本在 `alloc` 裡；`std` 只是把它 re-export 出來、外加接上作業系統的預設 allocator。`Option`、`Result`、`slice`、iterator——這些跟 OS 完全無關的東西，全部住在 `core` 裡。

所以 no_std 不是「Rust 的殘廢版」。你失去的是**需要 OS 的那些**（檔案、網路、執行緒、`println!`、預設 heap）；你保留的是**整個語言的表達力**（型別系統、trait、iterator、`Option`/`Result`、泛型、pattern matching 全在 `core`）。

> 這跟 C 的落差很大。C 的 freestanding 幾乎什麼函式庫都沒有——連 `memcpy` 都要自己給。Rust 的 `core` 是個**很豐富**的無配置函式庫：整個 iterator adapter 鏈、`Option`/`Result` combinator、`core::fmt`（格式化，只是需要你提供輸出目標）都在裡面。

## `#![no_std]` 拿掉了什麼

`#![no_std]` 這個 crate 層級屬性做的事：

1. **不連結 `std` crate**，改成連結 `core`（`alloc` 要自己 `extern crate alloc`）。
2. 不再有 `std` 提供的預設 **panic runtime**——你得自己給 `#[panic_handler]`。
3. 不再有預設 **global allocator**——要用 `Box`/`Vec` 就得自己給 `#[global_allocator]`。
4. `std` 的 prelude 換成 `core` 的 prelude（`Vec`/`String`/`Box` 不再自動在作用域裡，因為它們在 `alloc`）。

先看最小的可編 no_std lib——只需要 `#[panic_handler]`：

```rust
#![no_std]

use core::panic::PanicInfo;

// no_std 沒有預設的 panic 行為（std 版會印訊息 + unwind/abort）。
// 你必須告訴編譯器「panic 發生時怎麼辦」。裸機上常見就是停住或重啟。
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}   // 最簡：卡死。真實韌體可能會亮個 LED、寫 log、reset。
}

pub fn add(a: u32, b: u32) -> u32 {
    a.wrapping_add(b)   // wrapping_add 在 core，不需要 std
}
```

真的編（target `x86_64-unknown-none` 是一個沒有 OS 的裸 x86-64 target）：

```bash
$ rustc --target x86_64-unknown-none --crate-type lib minlib.rs -o libminlib.rlib
$ ls -l libminlib.rlib
```

實際結果：

```
OK: 15390 bytes rlib
```

編過了。這個 lib 不依賴任何作業系統，可以連進 bootloader 或 kernel。

### 失敗示範：忘記 panic_handler

`#[panic_handler]` 不是可選的。對 lib crate（rlib）它會被延遲到最終連結才要求，但對一個 `#![no_main]` 的執行檔，缺了立刻報錯：

```rust
#![no_std]
#![no_main]

#[no_mangle]
pub extern "C" fn _start() -> ! {   // 自己當進入點，不用 std 的 runtime
    loop {}
}
```

編它：

```
$ rustc --target x86_64-unknown-none nopanic_bin.rs -o nopanic_bin
error: `#[panic_handler]` function required, but not found

error: aborting due to 1 previous error
```

補上 panic_handler 後就連得出一個 freestanding ELF：

```rust
#![no_std]
#![no_main]
use core::panic::PanicInfo;
#[panic_handler]
fn panic(_: &PanicInfo) -> ! { loop {} }
#[no_mangle]
pub extern "C" fn _start() -> ! { loop {} }
```

```
$ rustc --target x86_64-unknown-none withpanic_bin.rs -o withpanic_bin
OK: linked freestanding binary 2192 bytes
$ file withpanic_bin
withpanic_bin: ELF 64-bit LSB pie executable, x86-64, ..., static-pie linked, not stripped
```

`#![no_main]` 是說「別用 Rust 標準的 `main` 進入點與 runtime 初始化」，`_start` + `#[no_mangle]` 是你自己接管進入點——這正是 bootloader/kernel 的模式。

### 失敗示範：no_std 裡碰 std

no_std 裡用 `std::` 開頭的東西直接找不到：

```rust
#![no_std]
use core::panic::PanicInfo;
#[panic_handler]
fn p(_: &PanicInfo) -> ! { loop {} }
pub fn f() -> std::string::String { std::string::String::new() }
```

```
error[E0433]: cannot find module or crate `std` in this scope
 --> usestd.rs:5:15
  |
5 | pub fn f() -> std::string::String { std::string::String::new() }
  |               ^^^ use of unresolved module or unlinked crate `std`
  |
  = help: you might be missing a crate named `std`
```

`String` 在 `alloc`，不在 `std`（`std` 只是 re-export 它）。要用得先 `extern crate alloc` 並提供 allocator——下一節。

## 加回 heap：`alloc` + 自訂 `#[global_allocator]`

`core` 沒有 `Box`/`Vec`/`String`，因為它們需要動態配置，而 no_std 環境**預設沒有 allocator**。要用它們，兩步：

1. `extern crate alloc;`——把 `alloc` crate 拉進來（它跟 `core` 一樣隨編譯器附帶，不用加依賴）。
2. 提供一個 `#[global_allocator]`——實作 `GlobalAlloc` trait，告訴 Rust「記憶體從哪來、怎麼還」。

kernel/embedded 沒有 `malloc` 可以呼叫，得自己管一塊記憶體。最簡單的配置器是 **bump allocator**：拿一塊靜態緩衝區，一根指標往前推，配置就是「指標對齊後前進」，`dealloc` 什麼都不做（只能整塊重置）。它不能回收單一配置，但對「初始化階段配一批、之後不還」的場景（很多 embedded/早期 kernel boot）夠用，而且極快。

```rust
#![no_std]

extern crate alloc;

use alloc::boxed::Box;
use alloc::vec::Vec;
use core::alloc::{GlobalAlloc, Layout};
use core::cell::UnsafeCell;
use core::panic::PanicInfo;
use core::sync::atomic::{AtomicUsize, Ordering};

const HEAP_SIZE: usize = 64 * 1024;   // 64 KiB 靜態 heap，編進 .bss

struct BumpAllocator {
    heap: UnsafeCell<[u8; HEAP_SIZE]>,   // 我們自己管的記憶體
    next: AtomicUsize,                    // 下一個可用的 offset
}

// 我們保證對 heap 的存取是同步的（下面用 atomic CAS），所以能宣告 Sync。
// 單核 kernel/embedded 常見假設；真多核搶配置時 CAS 迴圈仍安全。
unsafe impl Sync for BumpAllocator {}

unsafe impl GlobalAlloc for BumpAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let base = self.heap.get() as *mut u8;
        let align = layout.align();
        let mut cur = self.next.load(Ordering::Relaxed);
        loop {
            // 把 cur 往上對齊到 align 的倍數（align 一定是 2 的冪，所以能用位元遮罩）
            let aligned = (cur + align - 1) & !(align - 1);
            let new_next = aligned + layout.size();
            if new_next > HEAP_SIZE {
                return core::ptr::null_mut();   // OOM：回 null，Vec/Box 會走 alloc error
            }
            match self.next.compare_exchange_weak(
                cur, new_next, Ordering::Relaxed, Ordering::Relaxed,
            ) {
                Ok(_) => return unsafe { base.add(aligned) },
                Err(actual) => cur = actual,     // 有人搶先，重試
            }
        }
    }

    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
        // bump allocator 不做個別釋放。這是它的取捨：換來零 fragmentation 與極速配置。
    }
}

// 就是這一行讓 alloc crate 的 Box/Vec/String 知道記憶體從哪來。
#[global_allocator]
static ALLOCATOR: BumpAllocator = BumpAllocator {
    heap: UnsafeCell::new([0; HEAP_SIZE]),
    next: AtomicUsize::new(0),
};

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! { loop {} }

// 有了 global allocator，這些就能用了
pub fn make_boxed(x: i32) -> Box<i32> {
    Box::new(x)
}

pub fn sum_vec(n: u32) -> u64 {
    let mut v: Vec<u32> = Vec::new();
    for i in 0..n { v.push(i); }
    v.iter().map(|&x| x as u64).sum()
}
```

編它（target none）：

```
$ rustc --target x86_64-unknown-none --crate-type lib bumplib.rs -o libbumplib.rlib
OK: compiled, 139568 bytes
```

編過了——一個 no_std lib，自帶 heap，能用 `Box`/`Vec`。

### 這個 allocator 真的能用嗎？在 host 上證明

no_std lib 沒有 `println!`，在 target none 上也沒有 OS 能讓我們印東西驗證。要證明配置邏輯正確，把**同一份 allocator** 當成一般 std 程式的 `#[global_allocator]`——這樣整個程式（含 `Box`/`Vec`/`String`）的所有配置都走我們的 bump allocator，用 `println!`（走 stdout syscall）印結果：

```rust
// host 版：把上面的 BumpAllocator 原封不動當 std 程式的 global_allocator
#[global_allocator]
static A: BumpAllocator = /* ... 同上 ... */;

fn main() {
    let b = Box::new(42i32);
    println!("boxed = {}, at bump offset near start", *b);
    let mut v: Vec<u32> = Vec::new();
    for i in 0..100 { v.push(i); }
    println!("vec sum(0..100) = {}", v.iter().sum::<u32>());
    let s = String::from("no_std bump allocator works");
    println!("string = {:?}, used = {} bytes", s, A.next.load(Ordering::Relaxed));
}
```

實際輸出（`cargo run`）：

```
boxed = 42, at bump offset near start
vec sum(0..100) = 4950
vec sum(0..100) = 4950
string = "no_std bump allocator works", used = 2615 bytes
```

`Box::new(42)`、`Vec::push` ×100、`String::from` 全部走我們的 bump allocator，共用了 2615 bytes（含 `Vec` 倍增留下的空洞——bump 不回收，所以每次 grow 的舊配置就浪費掉了，這是 bump 的固有代價）。這證明 `GlobalAlloc` 實作是對的。

## 三層對照表：常用型別在哪一層

寫 no_std 前，你得知道你想用的東西住哪層。用錯層 = 編不過。

| 型別 / 功能 | `core` | `alloc` | `std` | 備註 |
|---|:---:|:---:|:---:|---|
| `Option`, `Result` | 有 | | | 純語言結構，無配置 |
| `&[T]`, slice 方法 | 有 | | | iterator/`sort_unstable` 都在 |
| iterator + 所有 adapter | 有 | | | `map`/`filter`/`fold`... |
| `Ordering`, atomic | 有 | | | `core::sync::atomic` |
| `core::fmt` (格式化) | 有 | | | 需你提供 `Write` 目標 |
| `Box<T>` | | 有 | | 需 global allocator |
| `Vec<T>`, `String` | | 有 | | 同上 |
| `Rc`, `Arc`, `BTreeMap` | | 有 | | 同上 |
| `HashMap` | | | 有 | 需隨機 seed（OS entropy） |
| `println!`, `File`, `TcpStream` | | | 有 | 需 syscall |
| `std::thread`, `Mutex` | | | 有 | 需 OS 執行緒 |

記憶法：**跟 OS 無關又不配置 → core；要 heap 但跟 OS 無關 → alloc；要 OS → std**。`HashMap` 之所以在 `std` 而非 `alloc`，是因為它預設用 OS 提供的隨機種子防 HashDoS——沒有 OS 就沒種子。`BTreeMap` 不需要隨機，所以在 `alloc`，no_std 可用。

## 對照：C 的 freestanding

Rust 的 no_std 對應 C 的 `-ffreestanding -nostdlib`。同一個問題兩種表達：

```c
/* -ffreestanding -nostdlib：沒有 libc，連 memcpy/memset 都要自己給 */
typedef unsigned long size_t;

void *memset(void *s, int c, size_t n) {
    unsigned char *p = s;
    while (n--) *p++ = (unsigned char)c;
    return s;
}
void *memcpy(void *d, const void *s, size_t n) {
    unsigned char *dp = d; const unsigned char *sp = s;
    while (n--) *dp++ = *sp++;
    return d;
}

void _start(void) {          /* 自己當進入點 */
    char buf[16];
    memset(buf, 0, sizeof buf);
    for (;;) {}
}
```

```
$ gcc -ffreestanding -nostdlib -static free.c -o free
OK: free: ELF 64-bit LSB executable, x86-64
```

差異攤開：

| 面向 | C freestanding | Rust no_std |
|---|---|---|
| 開關 | `-ffreestanding -nostdlib`（連結器旗標） | `#![no_std]`（語言屬性） |
| 進入點 | 自己寫 `_start`（`-nostartfiles`） | `#![no_main]` + `_start` 或用 `cortex-m-rt` 的 `#[entry]` |
| `memcpy`/`memset` | **自己提供**（編譯器會生成呼叫它們的 code） | 編譯器內建或 `compiler_builtins` 提供，通常不用自己寫 |
| 動態配置 | 自己寫 allocator，或直接不用 heap | `#[global_allocator]` + `alloc` crate |
| panic/錯誤 | 沒有例外機制；`abort`/`__stack_chk_fail` 自理 | `#[panic_handler]`（型別強制你提供） |
| 保留了什麼 | 幾乎只有語言本身 | **整個 `core`**：iterator、`Option`、trait、泛型 |

最大的實際差異在最後一列。C freestanding 幾乎是「裸語言」，你連字串處理都得手寫。Rust no_std 的 `core` 是一個**功能豐富的無配置函式庫**——你在裸機上照樣能用 iterator 鏈、`Result` 錯誤傳遞、trait 抽象、泛型。這讓 no_std Rust 的表達力遠高於 freestanding C，也是 Rust-for-Linux 敢用 Rust 寫 kernel code 的底氣之一。

## no_std 在真實生態怎麼用

誠實講一下你之後會碰到的具體形態，別以為 no_std 就只有上面的手搓：

- **embedded（Cortex-M）**：不會手寫 `_start`。用 `cortex-m-rt` 提供 `#[entry]` 巨集、reset handler、vector table；用 `cortex-m` crate 存取 NVIC/SysTick；HAL crate（如 `stm32f4xx-hal`、`rp2040-hal`）包好周邊。allocator 常常**根本不用**——很多 MCU 韌體純靜態配置，連 `alloc` 都不引，只用 `core` + 固定大小陣列。
- **async embedded**：`embassy` 是 no_std 的 async runtime，在 MCU 上跑 `async`/`await`（呼應本課 [Part 4](./26-async-futures.md)），不需要 OS 執行緒。
- **kernel / Rust-for-Linux**（本課 [Part 6](./37-rust-for-linux-overview.md)）：`#![no_std]` 是硬性的——kernel 沒有 userspace libc。它不用 `alloc` 的預設，而是接上 kernel 自己的 `kmalloc`（透過 kernel crate 的 allocator 抽象）。panic 接到 kernel 的 `BUG()`/`panic()`。
- **UEFI / bootloader**：`uefi` crate 提供 no_std 的 UEFI 服務封裝；`bootloader` crate 用 no_std 寫 x86 開機流程。

共通點：`#![no_std]` 是地基，上面各領域有各自的 runtime crate 幫你處理進入點、中斷、allocator。你很少從零手搓 `_start`，但**你必須理解底下發生什麼**——這章就是那個底。

## 踩雷集錦

1. **以為 no_std = 沒有 `Vec`**：錯。`Vec`/`Box`/`String` 在 `alloc`，只要你 `extern crate alloc` 並提供 `#[global_allocator]` 就能用。真正沒有的是 `std`（OS 相依）那半。
2. **忘記 `#[panic_handler]`**：lib crate 可能編過（延遲到最終連結），但一到執行檔就 `error: #[panic_handler] function required`。它是型別強制的，不是可選。
3. **在 no_std 用 `HashMap`**：它在 `std` 不在 `alloc`（要 OS entropy 防 HashDoS）。no_std 要 map 用 `alloc::collections::BTreeMap` 或 `heapless` 的定容 map。
4. **`#[global_allocator]` 的 `alloc` 回 null 卻沒處理**：OOM 時回 `null_mut()`，`Box`/`Vec` 會呼叫 `alloc::alloc::handle_alloc_error`（預設 abort）。你的 allocator 該在 OOM 時回 null，不能回一個無效非 null 指標——那是 UB。
5. **bump allocator 的記憶體被當可回收**：`dealloc` 是 no-op，所以每次 `Vec` grow 留下的舊配置永久浪費（上面 host 測試的 2615 bytes 就含這種空洞）。bump 適合「配一批、不還」的階段，不適合長時間反覆配置/釋放。
6. **忘記 `panic = "abort"`**：預設 panic 策略可能是 unwind，但 no_std 通常沒有 unwind runtime。多數 no_std 專案在 `Cargo.toml` 設 `panic = "abort"`（本章單檔 `rustc` 示範用 `x86_64-unknown-none` target 已內建 abort 策略，故未顯式設；用 cargo 專案時要注意）。

## 進階：再往深一層

- **`#[alloc_error_handler]`**：舊版要自己提供 alloc 錯誤處理器，現在 stable 上 `handle_alloc_error` 預設 abort，多數情況不用管。若要自訂（例如記 log 再 reset），需要 nightly。
- **`core::fmt::Write` 做 no_std 的 println**：`core::fmt` 有格式化能力，但需要你提供輸出目標。embedded 常見做法是實作 `Write` 把字元推到 UART，然後用 `write!(uart, "...")`。這讓你在沒有 `std::io` 的地方也能格式化輸出。
- **`heapless`**：一個 no_std crate，提供**定容**（編譯期固定大小、不需 allocator）的 `Vec`、`String`、`Deque`、`HashMap`。很多 embedded 專案連 `alloc` 都不引，全用 `heapless` 的定容容器，換取確定性記憶體用量與零配置失敗風險。
- **`build-std` / target spec**：跑更冷門的 target 時，可能連預編的 `core` 都沒有，得用 `-Z build-std` 從源碼重編 `core`/`alloc`。這是 embedded/kernel 進階工具鏈的常態。

## 動手練習

1. 把最小 no_std lib 的 `#[panic_handler]` 刪掉，改編成 `#![no_main]` 執行檔（`rustc --target x86_64-unknown-none`），確認你看到 `error: #[panic_handler] function required`。
2. 把 bump allocator 的 `HEAP_SIZE` 改成 `256`，然後 `sum_vec(1000)`——觀察 OOM（`alloc` 回 null → `handle_alloc_error` abort）。在 host 版跑最直觀。
3. 查 `alloc::collections::BTreeMap` 與 `std::collections::HashMap` 的文件，說出為什麼前者能進 `alloc` 而後者只能在 `std`。

## 本章重點整理

- `#![no_std]` = Rust 版的 `-ffreestanding -nostdlib`：不連 `std`，改連 `core`；拿掉需要 OS 的一切（檔案/網路/執行緒/`println!`/預設 heap）。
- 標準庫是三層：`core`（零依賴、語言骨、iterator/`Option`/slice）、`alloc`（需 allocator：`Box`/`Vec`/`String`/`BTreeMap`）、`std`（需 OS）。`std` 大半是 `core`+`alloc` 再加 OS 皮。
- no_std 必備：`#[panic_handler]`（型別強制）；要 heap 再加 `extern crate alloc` + `#[global_allocator]`（可自寫 bump allocator）。
- no_std 不是殘廢版——整個 `core` 的表達力（trait/泛型/iterator）都在，遠比 C freestanding 豐富。
- 真實生態：embedded 用 `cortex-m`/`embassy`/`heapless`，kernel/Rust-for-Linux 接 kernel 的 `kmalloc`，這一章是它們共同的地基。

## 自我檢核

- [ ] 不看筆記，能說出 `core`/`alloc`/`std` 三層各裝什麼、`Vec` 與 `Option` 分別在哪層、為什麼 `HashMap` 只能在 `std`
- [ ] 能解釋為什麼 embedded/kernel/bootloader **必須**是 no_std，而不只是「習慣這樣」
- [ ] 能說出 no_std 要用 `Box`/`Vec` 需要哪兩步，缺 `#[panic_handler]` 會怎樣
- [ ] 能對照講出 `#![no_std]` 與 C 的 `-ffreestanding -nostdlib` 相同與不同之處
- [ ] 知道 bump allocator 的取捨（零 fragmentation/極速 vs 不能個別回收），以及它適合什麼場景

## 延伸閱讀

### 官方文件 / 權威來源

- **[The Embedonomicon](https://docs.rust-embedded.org/embedonomicon/)** — Rust embedded working group
  - **讀哪裡**：「A `no_std` binary」到「Memory layout」章。它從零建一個裸機 Rust 程式，講 `#![no_std]`/`#![no_main]`、reset handler、記憶體佈局。
  - **和本章的關聯**：本章的 `#![no_main]` + `_start` 手搓版，這裡用 `cortex-m-rt` 做成生產形態。前提：讀完本章與 [Ch 17](./17-unsafe-basics.md)。
- **[Rust `alloc` crate 文件](https://doc.rust-lang.org/alloc/)** 與 **[`GlobalAlloc` trait](https://doc.rust-lang.org/core/alloc/trait.GlobalAlloc.html)**
  - **讀哪裡**：`GlobalAlloc` 的 safety 契約段落；`alloc` crate 的模組列表（看哪些型別在這層）。
  - **學到什麼**：本章 bump allocator 實作 `GlobalAlloc` 的完整 safety 要求（對齊、null 語意、`dealloc` 前提）。

### 部落格 / 技術文章

- **[Writing an OS in Rust — "A Freestanding Rust Binary" 與 "Heap Allocation"](https://os.phil-opp.com/freestanding-rust-binary/)** — Philipp Oppermann
  - **這篇說什麼**：手把手用 no_std Rust 寫一個 x86-64 OS kernel，第一篇就是 freestanding binary，後面有一整篇專講在 kernel 裡實作 allocator（含 bump/linked-list/fixed-block 三種）。
  - **讀哪裡**：至少讀「A Freestanding Rust Binary」與「Heap Allocation」「Allocator Designs」三篇。
  - **為什麼值得讀**：目前寫得最清楚的 no_std kernel 教學，本章的 bump allocator 在那裡有更完整（能回收）的版本對照。

### 生態 crate 文件

- **[`heapless` crate](https://docs.rs/heapless/)**
  - **讀哪裡**：`Vec`、`String`、`spsc::Queue` 的文件首頁。
  - **和本章的關聯**：本章示範「自帶 allocator 用 `alloc::Vec`」；`heapless` 是另一條路——完全不用 allocator 的定容容器，很多 embedded 專案的實際選擇。

no_std 這章補完，Part 3（記憶體佈局與 unsafe）就到底了。接下來練習 C 會把 Part 3 的 Miri 與 FFI 兩把武器一起操一遍：抓一段隱藏 UB，再把一個 C library 包成安全 Rust。做完你才算真的握住 unsafe 這個邊界。

→ [練習 C：用 Miri 抓 UB 與包一個 C library](./practice-c-miri-ffi.md)
