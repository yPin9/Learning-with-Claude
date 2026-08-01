# Ch 18 — unsafe 進階：transmute/MaybeUninit/union

> **目標**：把 unsafe 的三件重武器玩熟——`mem::transmute`（把任意型別的 bit 原封不動重新詮釋成另一型別，比 union 更暴力、最危險）、`MaybeUninit<T>`（正確處理未初始化記憶體，取代已廢除的 `mem::uninitialized`）、`union`（Rust 版，無 tag、存取要 unsafe、帶 Drop 要 `ManuallyDrop`）。再補一整套 raw pointer 工具：`ptr::read`/`write`/`copy`/`copy_nonoverlapping`（= C 的 memcpy/memmove）、`read_unaligned`、`ptr::null`、`NonNull`。這章不是要你天天用這些——是把 Ch 21 手刻 `Vec` 的工具箱備齊。全程對照 C：`transmute` ↔ C 的 type punning（union/memcpy/pointer cast）與其 strict-aliasing UB；`MaybeUninit` ↔ C 的 `malloc` 未初始化就用；Rust union ↔ C union。

> **環境**：Rust 以 `rustc 1.97.1`（stable，edition 2015）、`cc (Ubuntu 11.4.0)` 在 x86-64 Linux（WSL2）。所有 Rust/C 編譯錯誤、警告、程式輸出都是本機真跑，非推測。UB 段落會標明「值不可信」，Miri 精準抓在 [Ch 20](./20-memory-model-ub.md)。

## 為什麼需要這個？

[Ch 17](./17-unsafe-basics.md) 建立了 unsafe 的線與五種 superpower，也玩了裸指標的解引用。但那些還不夠你手刻一個 `Vec`——你缺三塊拼圖，這章補齊。

第一塊：**怎麼把一坨 bit 從一個型別搬到另一個型別**。C 裡你隨手就做：`*(unsigned*)&some_float`、`union { float f; int i; }`、`memcpy`。這在 C 是日常，在 Rust 對應到 `transmute` 和 `union`——但 Rust 把它們標成 unsafe，而且 `transmute` 是整個語言最危險的單一操作，因為它繞過**所有**型別檢查，直接說「這塊記憶體現在是那個型別，別問」。

第二塊：**怎麼處理「還沒初始化」的記憶體**。C 的 `malloc` 給你一塊垃圾，你當它是有效的 `struct` 直接寫——沒人管。Rust 不行：一個型別為 `T` 的變數，Rust 假設它**永遠**持有一個合法的 `T` 值，違反這假設就是 instant UB（尤其 `bool`、enum、reference 這種「不是所有 bit pattern 都合法」的型別）。手刻資料結構時你一定會碰到「先配一塊記憶體、之後才填值」，這時要用 `MaybeUninit<T>` 明確告訴編譯器「這裡現在**不是**合法的 `T`，別假設」。

第三塊：**一套完整的 raw pointer 記憶體操作**。Ch 17 的 `*p` 只是解引用，但手刻容器要的是「把值搬出來不 drop」（`ptr::read`）、「寫進去不 drop 舊值」（`ptr::write`）、「整塊複製」（`copy_nonoverlapping` = memcpy）、「從未對齊位址讀」（`read_unaligned`）。這些是 `Vec::push`、`Vec::pop`、`Vec` 擴容底層真正在呼叫的東西。

搞懂這三塊，你就有了 Ch 21 手刻 sound 抽象的全部原料。也同時看清：這些操作每一個都是 C 裡你閉著眼做、但其實踩在 UB 邊緣的動作，Rust 只是把邊緣標出來了。

## 先建立直覺

三件武器，一句話各自定位：

```
  transmute<A, B>(a)        MaybeUninit<T>              union { a: A, b: B }
  ┌──────────────────┐     ┌──────────────────┐       ┌──────────────────┐
  │ 同一坨 bit，      │     │ 一塊「可能還不是  │       │ 同一塊記憶體，   │
  │ 從型別 A 改看成 B │     │ 合法 T」的記憶體  │       │ 可用 A 或 B 詮釋 │
  │                  │     │                  │       │                  │
  │ 要求 size(A)==   │     │ 編譯器不假設它    │       │ 無 tag（不記錄   │
  │      size(B)     │     │ 持有合法值        │       │ 現在是哪個）     │
  │                  │     │ assume_init 前    │       │ 讀哪個欄位由你   │
  │ 繞過所有型別檢查  │     │ 讀 = UB           │       │ 負責（讀錯=垃圾）│
  └──────────────────┘     └──────────────────┘       └──────────────────┘
    最暴力：任意 A→B         管「未初始化」的正解        C union 的 Rust 版
```

關鍵直覺，三個都圍繞同一件事：**Rust 平常保證「型別為 T 的東西一定是合法的 T」，這三個工具都是在暫時放棄這個保證，換取底層控制力**。`transmute` 放棄「A 和 B 型別不同」；`MaybeUninit` 放棄「這塊記憶體現在是合法 T」；`union` 放棄「這塊記憶體只有一種型別」。放棄保證的代價：你接手證明「我沒把它用在會 UB 的地方」。

> 對照 C：C 從來沒有這個保證，所以這三件事在 C 裡沒有對應的「特殊語法」——`*(int*)&x`、垃圾 malloc、union 就是普通 C。Rust 把它們圈成 unsafe + 特殊 API，是因為 Rust 的其餘部分**依賴**這個保證成立。

## transmute：最暴力的 bit 重解讀

`mem::transmute::<A, B>(a)` 拿一個 `A` 的值，把它的 bit **原封不動**當成 `B` 回傳。不做任何轉換、不檢查值合不合法，只要求一件事：`size_of::<A>() == size_of::<B>()`。

先看一個**技術上合法、但你其實不該這樣寫**的用途——`f32` 和它的 IEEE 754 bit pattern 互轉：

```rust
use std::mem;
fn main() {
    // 合法用途：f32 <-> u32 bit 重解讀（雖然該用 to_bits/from_bits）
    let pi = 3.1415927_f32;
    let bits: u32 = unsafe { mem::transmute(pi) };
    println!("transmute f32->u32 bits = 0x{:08x}", bits);
    let back: f32 = unsafe { mem::transmute(bits) };
    println!("transmute u32->f32 back = {}", back);
    // 對照安全做法
    println!("to_bits()           = 0x{:08x}", pi.to_bits());
    println!("from_bits()         = {}", f32::from_bits(bits));
}
```

真跑（**編譯器直接勸退你**，這 warning 是重點）：

```
warning: unnecessary transmute
 --> t18a.rs:5:30
  |
5 |     let bits: u32 = unsafe { mem::transmute(pi) };
  |                              ^^^^^^^^^^^^^^^^^^
  |
  = note: `#[warn(unnecessary_transmutes)]` on by default
help: replace this with
  |
5 -     let bits: u32 = unsafe { mem::transmute(pi) };
5 +     let bits: u32 = unsafe { f32::to_bits(pi) };
  |
...
transmute f32->u32 bits = 0x40490fdb
transmute u32->f32 back = 3.1415927
to_bits()           = 0x40490fdb
from_bits()         = 3.1415927
```

`0x40490fdb` 就是 `3.1415927` 的 IEEE 754 單精度 bit pattern（跟 Ch 17 union 那個值一樣）。四種寫法結果相同，但注意 rustc 1.97 內建的 `unnecessary_transmutes` lint 直接告訴你「這個 transmute 沒必要，用 `f32::to_bits`」。這是 Rust 的態度：**能用安全 API 表達的，就別用 transmute**。`f32::to_bits`/`from_bits` 做的是同一件事，但簽章是 safe——它們內部封裝了這個 transmute 並保證 sound，你連 `unsafe` 都不用寫。

**size 不符會怎樣？編譯期直接擋。** 這是 transmute 唯一的靜態保護：

```rust
use std::mem;
fn main() {
    // size 不符：u32 (4 bytes) -> u64 (8 bytes)
    let x: u32 = 1;
    let y: u64 = unsafe { mem::transmute(x) };
    println!("{}", y);
}
```

真跑（編不過，照抄）：

```
error[E0512]: cannot transmute between types of different sizes, or dependently-sized types
 --> t18b.rs:5:27
  |
5 |     let y: u64 = unsafe { mem::transmute(x) };
  |                           ^^^^^^^^^^^^^^
  |
  = note: source type: `u32` (32 bits)
  = note: target type: `u64` (64 bits)
```

`transmute` 唯一保證的是 size 相等（`E0512`）——它**不**檢查目標值合不合法。這就是危險所在：`transmute::<u8, bool>(2)` size 相等（都 1 byte），編譯過，但 `bool` 只有 `0`/`1` 合法，值 `2` 是**非法的 bool** → UB。這種 UB 編譯期抓不到，見下面踩雷集錦的實測。

**對照 C 的 type punning：三種寫法，一種是 UB。** C 做「同一坨 bit 換型別看」有三條路，Rust 的 `transmute` 對應最危險那條：

```c
#include <stdio.h>
#include <string.h>
int main(void) {
    float pi = 3.1415927f;
    // 1. union punning（C 合法，C++ 是 UB 但編譯器多半容忍）
    union { float f; unsigned u; } un;
    un.f = pi;
    printf("union punning : 0x%08x\n", un.u);
    // 2. memcpy（永遠合法，無 strict-aliasing 問題）
    unsigned via_memcpy;
    memcpy(&via_memcpy, &pi, sizeof pi);
    printf("memcpy        : 0x%08x\n", via_memcpy);
    // 3. pointer cast（違反 strict aliasing = UB）
    unsigned via_cast = *(unsigned *)&pi;
    printf("pointer cast  : 0x%08x\n", via_cast);
    return 0;
}
```

真跑（`gcc -O2 -Wall`）：

```
punning.c:15:26: warning: dereferencing type-punned pointer will break strict-aliasing rules [-Wstrict-aliasing]
   15 |     unsigned via_cast = *(unsigned *)&pi;
--- run ---
union punning : 0x40490fdb
memcpy        : 0x40490fdb
pointer cast  : 0x40490fdb
```

三種都印 `0x40490fdb`（跟 Rust 一致），但第三種 `*(unsigned*)&pi` gcc 給了 `-Wstrict-aliasing` warning：**用一個型別的指標去讀另一個型別的物件，違反 strict aliasing 規則，是 C 的 UB**。編譯器在高優化下有權假設 `float*` 和 `unsigned*` 不會指向同一塊，於是可能重排/快取讀取，讓你讀到舊值。C 的「正確」做法是 memcpy（永遠合法）或 union（C 合法、C++ 灰區）。

Rust 的 `transmute` 語意上最接近哪個？**最接近 memcpy**——它是 bitwise copy，不涉及「用錯型別指標讀同一物件」的 aliasing 問題（transmute 是 by-value，把 bit 搬進一個新的 `B`）。所以 transmute 本身不會踩 strict-aliasing UB，它的危險在別處：**產生非法值**（如非法 bool/enum）和**破壞不變量**（如 transmute 出兩個都擁有同一 heap 指標的 `Box`）。這是 Rust 和 C 的 UB 分佈不同的地方——同一個底層動作，危險點不在同一處。

## MaybeUninit：正確處理未初始化記憶體

先講**為什麼舊的 `mem::uninitialized` 是災難**。它在 Rust 1.0 就有，語意是「給我一個型別 `T` 的值，但別初始化它」——聽起來像 C 的未初始化變數，但在 Rust 是 **instant UB**，因為它創造了一個「型別是 `T` 但持有垃圾 bit」的值，而 Rust 全域假設「型別 `T` 的東西一定是合法 `T`」。對 `bool`（只有 0/1 合法）、enum（只有列舉的 discriminant 合法）、reference（不能為 null、不能懸空）這種型別，垃圾 bit 直接違反不變量——UB 在 `uninitialized()` 回傳的那一刻就發生了，不用等你用它。

Rust 1.39 廢除 `mem::uninitialized`，1.97 還加了執行期防護。真跑一個 `bool`：

```rust
fn main() {
    let b: bool = unsafe { std::mem::uninitialized() };
    println!("b = {}", b);
}
```

真跑（照抄，兩個 warning 都是教材）：

```
warning: `std::mem::uninitialized`: use `mem::MaybeUninit` instead
 --> t18f.rs:2:38
warning: the type `bool` does not permit being left uninitialized
 --> t18f.rs:2:28
  |
2 |     let b: bool = unsafe { std::mem::uninitialized() };
  |                            ^^^^^^^^^^^^^^^^^^^^^^^^^
  |                            this code causes undefined behavior when executed
  |                            help: use `MaybeUninit<T>` instead, and only call `assume_init` after initialization is done
  |
  = note: booleans must be either `true` or `false`
--- run ---
b = true
```

`invalid_value` lint 白紙黑字：`this code causes undefined behavior when executed`、`booleans must be either true or false`。這個 `bool` 版本剛好跑出 `true` 沒 crash——但它已經是 UB，值不可信。換個**保證非法**的型別（reference 不能是垃圾），1.97 的執行期防護會直接 abort：

```rust
fn main() {
    #[allow(deprecated, invalid_value)]
    let r: &i32 = unsafe { std::mem::uninitialized() };
    println!("got ref {:p}", r);
}
```

真跑：

```
thread 'main' panicked at library/core/src/panicking.rs:
attempted to leave type `&i32` uninitialized, which is invalid
thread caused non-unwinding panic. aborting.
```

`attempted to leave type &i32 uninitialized, which is invalid`——編譯器在 `uninitialized::<&i32>()` 裡插了一個「這型別不允許未初始化」的 panic guard，直接攔下。這就是為什麼 `mem::uninitialized` 該從你的字典裡刪掉。

**正解：`MaybeUninit<T>`。** 它的型別本身就宣告「我是一塊 `T` 大小/對齊的記憶體，但可能還不是合法的 `T`」。編譯器對 `MaybeUninit<T>` **不**做「持有合法值」的假設，所以放垃圾在裡面完全合法。你只有在真的初始化完之後，才呼叫 `assume_init()`（unsafe）把它「認證」成合法 `T`——這時責任在你：你簽字保證它真的初始化好了。

```rust
use std::mem::MaybeUninit;
fn main() {
    // 單值 MaybeUninit
    let mut x = MaybeUninit::<i32>::uninit();
    x.write(42);
    let v = unsafe { x.assume_init() };
    println!("single: {}", v);
}
```

真跑：`single: 42`。

`MaybeUninit::<i32>::uninit()` 給你一塊未初始化的 `i32` 記憶體（合法，因為型別是 `MaybeUninit<i32>` 不是 `i32`）；`.write(42)` 填值；`assume_init()` 才拿出 `i32`。在 `write` 之前 `assume_init` 就是 UB——你認證了一個沒填的值。

**手刻資料結構的典型場景：建一個未初始化陣列、逐格填、再整批認證。** 這正是 `Vec` 擴容時對 buffer 做的事的縮影：

```rust
use std::mem::MaybeUninit;
use std::mem;
fn main() {
    // 用 MaybeUninit 建一個未初始化陣列，逐格填，再 assume_init
    const N: usize = 4;
    let mut arr: [MaybeUninit<u32>; N] = unsafe { MaybeUninit::uninit().assume_init() };
    for i in 0..N {
        arr[i].write((i * i) as u32);
    }
    // 全部初始化後才把整個陣列 transmute 成 [u32; N]
    let init: [u32; N] = unsafe { mem::transmute::<_, [u32; N]>(arr) };
    println!("{:?}", init);
}
```

真跑：`[0, 1, 4, 9]`。

這裡兩個 unsafe 疊用要看懂：外層 `MaybeUninit::uninit().assume_init()` 造出一個 `[MaybeUninit<u32>; N]`——這步是 sound 的，因為「一個 `MaybeUninit` 陣列」本身不需要初始化（陣列的每個元素都是「可以不初始化」的型別）。填完後用 `transmute` 把 `[MaybeUninit<u32>; N]` 變 `[u32; N]`——因為 `MaybeUninit<T>` 和 `T` 佈局完全相同（same size/align），這個 transmute 只是「拿掉 MaybeUninit 這層標記」。**前提**：迴圈真的填滿了全部 N 格；漏一格，transmute 後那格就是非法的 `u32`（雖然 `u32` 每個 bit pattern 都合法，但換成 `bool`/enum 陣列就會 UB）。

> 對照 C：C 的 `int arr[4];` 未初始化直接用，讀到垃圾但編譯器不管；`malloc(4*sizeof(int))` 更是給你一塊垃圾當 `int*` 用。Rust 逼你用 `MaybeUninit` 明講「這裡還沒好」，好處是編譯器和 Miri 能追蹤「哪塊還沒初始化」，抓出「讀了沒填的格子」這種 C 完全不管的 bug。

## union：Rust 版，和 enum 的根本差異

Rust 的 `union` 和 C 一樣：所有欄位共用同一塊記憶體，size = 最大欄位的 size。和 Rust 的 `enum` 的**根本差異**：enum 有 **tag（discriminant）**記錄「現在是哪個變體」，存取時編譯器檢查 tag、安全；union **沒有 tag**，不記錄現在存的是哪個欄位，所以讀欄位是 **unsafe**——你可能讀到「當初沒寫進這欄位」的垃圾（type punning），這責任編譯器沒法幫你擔。

```rust
use std::mem::ManuallyDrop;
union MyUnion {
    i: u32,
    f: f32,
    // 帶 Drop 的型別在 union 裡要包 ManuallyDrop
    s: ManuallyDrop<String>,
}
fn main() {
    let u = MyUnion { i: 0x40490fdb };
    unsafe {
        println!("as i = 0x{:08x}", u.i);
        println!("as f = {}", u.f);       // 把 i 的 bit 重新當 f32 讀
    }
    // 存放 String（需要 ManuallyDrop，且要手動 drop）
    let mut u2 = MyUnion { s: ManuallyDrop::new(String::from("hello union")) };
    unsafe {
        println!("as s = {}", *u2.s);
        ManuallyDrop::drop(&mut u2.s);   // 手動釋放，否則 leak
    }
}
```

真跑：

```
as i = 0x40490fdb
as f = 3.1415927
as s = hello union
```

三點要看懂：

- **讀 `u.f` 是 type punning**：我們寫的是 `i: 0x40490fdb`，讀 `u.f` 把同一坨 bit 當 `f32` 詮釋，得 `3.1415927`。這跟 transmute 是同一件事，只是透過 union 欄位做。讀「沒寫進去的欄位」在 Rust union 是**允許的**（不像 C++ 那樣理論上 UB），只要你讀出來的值對目標型別合法——但如果 union 有 `bool` 欄位而你寫進去的 bit 是 `2`，讀 `bool` 就 UB。
- **帶 Drop 的型別要 `ManuallyDrop`**：`String` 有解構子（要 free heap buffer）。但 union 不知道「現在存的是不是 String」，沒法自動 drop（drop 錯欄位 = 災難）。所以 Rust 規定：union 裡放有 Drop 的型別，必須包 `ManuallyDrop<T>`——它關掉自動 drop，你得手動 `ManuallyDrop::drop`。忘了手動 drop = memory leak（不是 UB，但是 bug）。
- **沒有 tag**：這個 union 從頭到尾不記錄「現在裡面是 i 還是 f 還是 s」。你若寫 `i` 卻讀 `s`（把整數當 String 的指標），就是把垃圾當 heap 指標——馬上 UB/crash。**union 的安全完全靠你自己記住現在存的是哪個欄位**，這正是 enum 用 tag 幫你自動管的事。

**什麼時候用 union 而非 enum？** 幾乎只有兩個場景：(1) **FFI**——對接 C 的 union（Ch 19）；(2) 極端記憶體敏感、你能手動保證 tag 一致、且省下 enum tag 那幾個 byte 有意義的場合（罕見）。日常一律用 enum：多幾 byte tag 換編譯器幫你管安全，划算。

## raw pointer 工具箱：read/write/copy/unaligned

Ch 17 教了 `*p` 解引用。手刻容器還要更精細的記憶體操作，全在 `std::ptr`。一次過：

```rust
use std::ptr;
fn main() {
    // ptr::read / write
    let mut x = 10i32;
    let p = &mut x as *mut i32;
    unsafe {
        let v = ptr::read(p);          // 讀出（bitwise copy，不動原記憶體的「所有權」概念）
        println!("read = {}", v);
        ptr::write(p, 99);             // 寫入（不 drop 舊值）
    }
    println!("x = {}", x);

    // copy_nonoverlapping = memcpy（來源目標不重疊）
    let src = [1u8, 2, 3, 4];
    let mut dst = [0u8; 4];
    unsafe {
        ptr::copy_nonoverlapping(src.as_ptr(), dst.as_mut_ptr(), 4);
    }
    println!("dst = {:?}", dst);

    // read_unaligned：從未對齊位址讀
    let buf = [0u8, 0x01, 0x02, 0x03, 0x04];  // 從 offset 1 開始是未對齊的 u32
    let unaligned_ptr = unsafe { buf.as_ptr().add(1) as *const u32 };
    let val = unsafe { unaligned_ptr.read_unaligned() };
    println!("read_unaligned u32 = 0x{:08x}", val);

    // null 與 NonNull
    let np: *const i32 = ptr::null();
    println!("null ptr is_null: {}", np.is_null());
    let nn = std::ptr::NonNull::new(&x as *const i32 as *mut i32);
    println!("NonNull constructed: {}", nn.is_some());
}
```

真跑：

```
read = 10
x = 99
dst = [1, 2, 3, 4]
read_unaligned u32 = 0x04030201
null ptr is_null: true
NonNull constructed: true
```

逐個對照 C 與說明各自的責任：

- **`ptr::read(p)`**：從 `p` 讀出一個 `T`，做的是 **bitwise copy**，且**不管所有權**——它產生一個 `T` 的副本，但原位置的 bit 沒動。這跟 `let v = *p`（對非 Copy 型別會 move/報錯）不同：`read` 是給非 Copy 型別「把值搬出來」用的低階原語。危險：read 之後原位置和新值**都認為自己擁有那份資料**（double ownership），對 `String` 這種會導致 double free——所以 read 通常配「之後不再用原位置」使用（如 `Vec::pop` read 出最後一個元素後把 len 減 1，邏輯上放棄原位置）。
- **`ptr::write(p, v)`**：把 `v` 寫進 `p`，**不 drop 舊值**。對比 `*p = v`（會先 drop 舊值再寫）：如果 `p` 指向未初始化記憶體（如剛配好的 buffer），你**必須**用 `write`——用 `*p = v` 會嘗試 drop 那塊未初始化的垃圾（把垃圾當 String 去 free）→ UB。這是 `MaybeUninit::write` 和 `ptr::write` 的核心用途：往未初始化的地方放值。
- **`copy_nonoverlapping` = C 的 `memcpy`**；還有 `ptr::copy` = C 的 `memmove`（允許來源目標重疊）。名字的 nonoverlapping 就是 memcpy 的「來源目標不可重疊」前提——違反是 UB。`Vec` 擴容、`Vec::insert`/`remove` 移動元素全靠這兩個。
- **`read_unaligned`**：一般解引用（`*p`、`ptr::read`）**要求指標對齊**（`u32` 要 4-byte 對齊），未對齊解引用是 UB。但解析網路封包/檔案格式時，你常拿到「buffer offset 1 的地方有個 u32」這種未對齊位址。`read_unaligned` 是專門處理這個的——它用逐 byte 讀的方式，容忍未對齊。上面 `buf` 的 offset 1 是 `[0x01,0x02,0x03,0x04]`，小端序讀成 `0x04030201`。**普通 `ptr::read` 在這裡會 UB**（Ch 20 會用 Miri 抓對齊違規）。
- **`NonNull<T>`**：一個「保證非 null」的裸指標 wrapper。標準庫的 `Box`、`Vec`、`Rc` 內部都用它，因為「保證非 null」讓 `Option<NonNull<T>>` 能享受 niche optimization（[Ch 15](./15-memory-layout.md)：null 當 `None` 的位模式，`Option<NonNull<T>>` 和 `*mut T` 同大小）。`NonNull::new` 回傳 `Option`——傳 null 進去得 `None`，強迫你處理 null 的情況。

## 對比與取捨

| 工具 | 做什麼 | C 對應 | 主要危險 | 什麼時候用 |
|---|---|---|---|---|
| `transmute<A,B>` | bit 原封重解讀 | `*(B*)&a` / memcpy | 產生非法值、破壞不變量 | 最後手段；優先找專用 API（`to_bits`）|
| `MaybeUninit<T>` | 標記「未初始化」 | 垃圾 malloc / 未初始化變數 | `assume_init` 前讀 = UB | 配一塊記憶體、之後才填 |
| `union` | 一塊記憶體多型別 | C union | 讀錯欄位、忘記手動 drop | FFI；極端省空間 |
| `ptr::read/write` | 搬值不管所有權/不 drop | 手動 memcpy 單元素 | double ownership / drop 未初始化 | 手刻容器的 push/pop |
| `copy_nonoverlapping` | 整塊複製 | memcpy | 重疊、對齊、越界 | 擴容、批次搬移 |
| `read_unaligned` | 未對齊讀 | packed struct 讀 | （它就是為了避免對齊 UB）| 解析 packet/檔案格式 |

取捨的總原則：**這些都是「有專用安全 API 就別用」的低階原語**。`transmute` 幾乎總能被更窄的 API 取代（`to_bits`、`as` cast、`from_ne_bytes`）；`MaybeUninit` 手刻容器才需要（一般用 `Vec::with_capacity` + `push`）；union 幾乎只為 FFI。它們存在是為了讓標準庫和你的 Ch 21 `Vec` 能實作出來，不是給日常用的。

## 踩雷集錦

1. **以為 `transmute` 只是「型別轉換」**：它不是 `as` cast（`as` 會做數值轉換，如 `300u32 as u8 = 44`）。`transmute` **一個 bit 都不改**，只換型別標籤。`transmute::<u32, u8>` 甚至編不過（size 不符）。把 transmute 當成「更強的 as」是重大誤解——它是「把這坨 bit 硬說成另一型別」，不做任何值的調整。

2. **`transmute` 出非法值，以為「能印出東西」就沒事**：`transmute::<u8, bool>(2)` size 相等、編譯過，但 `bool` 只有 0/1 合法，值 2 是非法 bool = UB。真跑這個（本機實測）：

   ```rust
   let b: bool = unsafe { std::mem::transmute(2u8) };
   println!("invalid bool: {}", b);      // debug 印 "true"
   if b { println!("true branch"); } else { println!("false branch"); }
   println!("b as u8 = {}", b as u8);
   ```

   debug 版輸出：`invalid bool: true` / `false branch` / `b as u8 = 0`——**`println` 說 true、`if` 走 false、`as u8` 給 0，三個自相矛盾**。release 版又不一樣（印 false、走 true branch）。這不是「值錯了」，是編譯器在「bool 一定是 0/1」的假設下各處優化，非法值讓每處假設得出不同結論。這正是 UB「不是一個確定的錯誤值，而是整個程式失去意義」的鐵證。

3. **`MaybeUninit` 忘了填就 `assume_init`**：`assume_init()` 是你簽字「這塊真的初始化好了」。沒 `write` 就 `assume_init` = 認證一個垃圾值，等於舊 `mem::uninitialized` 的 UB。尤其陣列場景：迴圈漏填一格、或提早 break，那格就是未初始化的垃圾被認證成合法值。Miri（Ch 20）抓得到「讀了未初始化記憶體」。

4. **union 讀錯欄位 / 忘記手動 drop**：union 沒 tag，你寫 `i` 讀 `s`（把整數當 String 指標）→ 把垃圾當 heap 指標 → UB。而放 `ManuallyDrop<String>` 卻忘了 `ManuallyDrop::drop` → memory leak。union 的一切安全都靠你人腦記帳「現在存的是哪個欄位」——這是它比 enum 危險的根源。

5. **對未對齊位址用普通 `ptr::read`/`*p`**：`*p` 和 `ptr::read` 都**假設指標對齊**，對未對齊位址用它們是 UB（即使 x86 硬體容忍未對齊存取、跑起來沒 crash——UB 不等於 crash）。解析二進位格式時遇到未對齊欄位，一律 `read_unaligned`/`write_unaligned`。這條 x86 上特別陰險，因為 x86 硬體允許未對齊，你會「跑起來都對」直到 Miri 或換 ARM 才爆。

## 進階：再往深一層

**`transmute` 的替代品清單（優先用這些）**：`f32::to_bits`/`from_bits`（浮點 bit）、`u32::from_ne_bytes`/`to_ne_bytes`（byte 陣列與整數）、`as` cast（數值轉換）、`slice::from_raw_parts`（造 slice）、`&*(p as *const B)`（reference 重解讀，仍 unsafe 但比 transmute 窄）。Rustonomicon 直說 transmute 是「the most horribly unsafe thing you can do in all of Rust」——1.97 的 `unnecessary_transmutes` lint 就是在系統性地把常見 transmute 用途導向這些安全替代。真需要 transmute 的場合越來越少。

**`MaybeUninit` 的 `assume_init` vs `assume_init_read` vs `assume_init_ref`**：`assume_init(self)` 消耗並取出值（move out）；`assume_init_read(&self)` 做 bitwise copy 取出（原 MaybeUninit 還在，小心 double ownership）；`assume_init_ref(&self)`/`assume_init_mut` 取出參考（不 move）。手刻容器時這幾個的選擇對應「我要 move 出來還是借用」，選錯會 double free 或 use-after-move。

**`transmute` 與生命週期**：`transmute` 能把 `&'a T` 變 `&'static T`（延長生命週期）——這是 transmute **最危險**的用途之一，等於騙編譯器「這個借用永遠有效」，之後 use-after-free 而編譯器不擋。Rustonomicon 特別警告：**用 transmute 改生命週期是雷區之王**。需要處理生命週期時找別的辦法（`Pin`、明確的 unsafe 契約），別用 transmute 硬凹。

**面試常問**：「`transmute::<u8, bool>(2)` 會發生什麼？」——標準答案：編譯通過（size 相等），執行是 UB（非法 bool 值），行為不可預測（debug/release、不同優化下 `println`/`if`/`as` 可能各自得出不同結論）。能講到「UB 不是產生一個錯誤值，是整個程式在該點失去定義」代表你真懂 UB，不是把它當「bug」。

## 動手練習

1. **transmute 三態實測**：把踩雷 2 的 `transmute::<u8,bool>(2)` 例子跑一遍，分別 `rustc`（debug）和 `rustc -O`（release），對照 `println`/`if`/`as u8` 三處的輸出差異，體會「同一個 UB 在不同優化下各處自相矛盾」。再試 `transmute::<u32,u8>`（觀察 E0512 編不過）。

2. **MaybeUninit 陣列 + 故意漏填**：把本章 `[MaybeUninit<u32>; 4]` 的迴圈改成只填 0..3（漏最後一格），`transmute` 成 `[u32; 4]` 印出來。`u32` 每個 bit pattern 都合法，所以「看起來沒事」（印出一個垃圾值）。再把型別換成 `[MaybeUninit<bool>; 4]`，漏一格，用 Miri（Ch 20）跑，看它報「讀未初始化記憶體」。

3. **手刻一個 `swap`**：用 `ptr::read`/`ptr::write`（不用 `mem::swap`、不用 `let tmp = ...`）寫一個 `fn my_swap<T>(a: *mut T, b: *mut T)`。想清楚為什麼要用 `read`/`write` 而不能用 `*a = *b`（對非 Copy 型別 `*a = *b` 會 move + drop，語意不對）。這是 `mem::swap` 的真實實作骨架。

## 本章重點整理

- **`transmute<A,B>` = bit 原封重解讀**：唯一靜態保護是 `size(A)==size(B)`（`E0512`），**不**檢查值合法性。是全 Rust 最危險操作；1.97 的 `unnecessary_transmutes` lint 主動導向 `to_bits`/`from_bits` 等安全替代。對照 C 三種 type punning：語意最接近 memcpy（by-value copy，不踩 strict-aliasing），危險在產生非法值/破壞不變量。
- **`MaybeUninit<T>` 取代已廢除的 `mem::uninitialized`**：舊的對 `bool`/enum/reference 是 instant UB（1.97 有 `invalid_value` lint + reference 執行期 abort guard）。`MaybeUninit` 明確標「這塊還不是合法 T」，`write` 填值、`assume_init`（unsafe）才認證成 `T`。手刻容器配一塊記憶體晚填值的正解。
- **Rust union 無 tag**：和 enum 的根本差異（enum 有 discriminant 幫你管、安全；union 讀欄位 unsafe，讀錯 = 垃圾）。帶 Drop 的型別要包 `ManuallyDrop` 並手動 drop。幾乎只為 FFI 用。
- **raw pointer 工具箱**：`ptr::read`（搬值、不管所有權，小心 double ownership）、`ptr::write`（寫值、不 drop 舊值，往未初始化處放值的正解）、`copy_nonoverlapping`=memcpy / `copy`=memmove、`read_unaligned`（未對齊讀，避免對齊 UB）、`NonNull`（保證非 null，吃 niche 優化）。這是 Ch 21 手刻 `Vec` 的原料。
- **總原則**：這些都是「有專用安全 API 就別用」的低階原語，存在是為了讓標準庫和你的 Ch 21 抽象實作得出來，不是日常用的。

## 自我檢核

- [ ] 能說出 `transmute` 和 `as` cast 的差異（transmute 不改 bit、as 做數值轉換），以及 transmute 唯一的靜態檢查是什麼。
- [ ] 面試問「`transmute::<u8,bool>(2)` 會怎樣」，能答「編譯過、執行 UB、各處自相矛盾」並解釋為什麼 UB 不是「一個錯誤值」。
- [ ] 能解釋為什麼舊 `mem::uninitialized` 對 `bool`/reference 是 instant UB，而 `MaybeUninit` 怎麼修掉它。
- [ ] 不看筆記，能講 Rust union 和 enum 的根本差異（tag），以及帶 Drop 型別為什麼要 `ManuallyDrop`。
- [ ] 知道 `ptr::write` 和 `*p = v` 的差別（drop 不 drop 舊值），以及為什麼往未初始化記憶體要用 `write`。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件

- **《The Rustonomicon》「Transmutes」與「Uninitialized Memory / Checked Uninitialized Memory」** — （[doc.rust-lang.org/nomicon/transmutes.html](https://doc.rust-lang.org/nomicon/transmutes.html)、[.../uninitialized.html](https://doc.rust-lang.org/nomicon/uninitialized.html)）
  - **讀哪裡**：「Transmutes」整節（它稱 transmute 為「the most horribly unsafe thing」並列出所有陷阱，含生命週期那個雷）；「Checked Uninitialized Memory」講 `MaybeUninit` 的正確 pattern。
  - **學到什麼**：本章 transmute 危險點、MaybeUninit 用法的權威版；Nomicon 對「為什麼 transmute 生命週期最危險」講得比本章深。
  - **前提**：懂 Ch 17 的 UB/soundness；這是 unsafe 深水區的正門。

- **`std::mem::MaybeUninit` 與 `std::mem::transmute` 的 std 文件** — （[doc.rust-lang.org/std/mem/union.MaybeUninit.html](https://doc.rust-lang.org/std/mem/union.MaybeUninit.html)、[.../fn.transmute.html](https://doc.rust-lang.org/std/mem/fn.transmute.html)）
  - **讀哪裡**：`MaybeUninit` 文件頂部的「out-of-bounds/uninitialized」說明 + 陣列初始化的官方範例（和本章 `[MaybeUninit<u32>; N]` 對應）；`transmute` 文件的「Alternatives」一節列出所有該優先用的替代 API。
  - **學到什麼**：`assume_init` 系列（`_read`/`_ref`/`_mut`）的精確語意；transmute 的官方替代品清單。
  - **前提**：懂本章基本用法；這是查 API 細節時的權威來源。

### 官方文件（Reference）

- **《The Rust Reference》「Type layout」與「Behavior considered undefined」** — （[doc.rust-lang.org/reference/type-layout.html](https://doc.rust-lang.org/reference/type-layout.html)、[.../behavior-considered-undefined.html](https://doc.rust-lang.org/reference/behavior-considered-undefined.html)）
  - **讀哪裡**：UB 頁的「Producing an invalid value」（本章非法 bool/enum 的定義依據）和「union field access」；layout 頁確認 `MaybeUninit<T>` 與 `T` 同佈局的保證（本章 transmute 陣列的正確性依據）。
  - **學到什麼**：「什麼值算非法」的權威清單、`MaybeUninit<T>` 佈局保證的條文出處。
  - **前提**：懂本章 UB 概念；當「這樣 transmute 到底 sound 嗎」有疑問時來查。

### 技術文章

- **「Alias-based formulation / What The Hardware Does」相關——Ralf Jung 的部落格「A Formal Look at Pointer Provenance」** — Ralf Jung（[ralfj.de/blog](https://www.ralfj.de/blog/)）
  - **這篇說什麼**：Miri 主要作者、Rust 記憶體模型的核心研究者，解釋 transmute/裸指標背後「什麼是 UB、為什麼」的形式化基礎，含 provenance（Ch 20 正題）。
  - **讀哪裡**：先讀 provenance 系列的第一篇建立「指標不只是位址」的直覺；transmute 相關的細節留到 Ch 20 讀。
  - **為什麼值得讀**：本章講 transmute 危險是操作層面，Ralf 的文章講「語言層面為什麼這是 UB」——想寫生產級 unsafe（Ch 21、kernel）遲早要懂這層。
  - **前提**：懂本章 + Ch 17；這是理論深水區，選讀。

搞懂了 transmute/MaybeUninit/union 和 raw pointer 工具箱，下一章跨到 Rust 的另一個 unsafe 大場景——FFI：真的呼叫 C 函式、真的把 Rust 函式匯出給 C 呼叫、`CString`/`CStr` 字串來回傳遞、把 unsafe C API 包成安全 Rust wrapper。這章的 `ptr`/`transmute`/union 全會在 FFI 邊界再出現一次。

→ [Ch 19 FFI：與 C 互操作與安全 wrapper](./19-ffi.md)
