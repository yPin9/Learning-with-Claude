# Ch 21 — 手刻 unsafe 抽象：安全的 Vec

> **目標**：從零手刻一個 `MyVec<T>`——自己管 `alloc`/`realloc`/`dealloc`、自己 `ptr::write`/`ptr::read`、自己 `Drop`——並且把所有 `unsafe` 關在一組**安全**的公開 API 後面。學完你會知道：unsafe 抽象的價值不在「用了 unsafe」，而在「向外界承諾了哪些 invariant，且你證明自己在每條路徑上都維持了它們」。這是全課 unsafe 段落的畢業考。

> **環境**：`rustc 1.97.1` (stable) 與 `nightly` + Miri（`rustc 1.99.0-nightly`），x86-64 Linux（WSL2）。本章每段 Rust code 都真跑過，輸出照貼；Miri 報告直接貼原文。

## 為什麼需要這個？

你已經在 [Ch 16](./16-smart-pointers.md) 看過 `Box`/`Rc`/`Arc` 怎麼包裝原始指標，在 [Ch 17](./17-unsafe-basics.md)/[Ch 18](./18-unsafe-advanced.md) 學過 unsafe 的五種 superpower 與 `MaybeUninit`，在 [Ch 20](./20-memory-model-ub.md) 看過 Miri 怎麼抓 UB。這些都是「讀別人怎麼做」。這一章反過來：**你自己當那個寫 unsafe 的人**。

`Vec<T>` 是整個 Rust 生態最常用的容器，也是「safe API 包 unsafe 實作」的教科書範例。它的公開介面（`push`、`pop`、`v[i]`、`&v[..]`）完全安全——你在 safe Rust 裡怎麼折騰都不會 UB。但它內部全是裸指標運算、手動記憶體管理、`ptr::write` 到未初始化的記憶體。中間那層「怎麼把危險的內部包成安全的外部」就是這章要拆的東西。

在 C 裡你寫過無數次動態陣列：`malloc` 一塊、滿了 `realloc` 加倍、用完 `free`。那份邏輯你已經有了。差別在——C 的動態陣列**沒有型別安全**（`void*` 硬轉）、**沒有自動解構**（元素是 `struct` 帶指標時你得自己走一遍 free）、**沒有 Miri**（越界、double free、use-after-free 你只能靠 valgrind 或運氣）。Rust 讓你用一樣的底層邏輯，但把「維持 invariant」這件事變成可以被工具驗證的東西。

## 先建立直覺

一個 `Vec` 就是三個欄位：一根指標、一個容量、一個長度。

```
MyVec<i32>  (push 了 5 個，容量 8)

  ptr ──────────┐
  cap = 8       │
  len = 5       ▼
              ┌────┬────┬────┬────┬────┬╌╌╌╌┬╌╌╌╌┬╌╌╌╌┐
   heap:      │ 10 │ 20 │ 30 │ 40 │ 50 │ ?? │ ?? │ ?? │
              └────┴────┴────┴────┴────┴╌╌╌╌┴╌╌╌╌┴╌╌╌╌┘
              └───────── 已初始化 ─────────┘└─ 未初始化 ─┘
                  [0, len)                    [len, cap)
```

三個核心 invariant，違反任何一條就是 UB 的種子：

1. **`len <= cap`**：長度不能超過容量。越界寫是踩別人的記憶體。
2. **`[0, len)` 全部已初始化**；`[len, cap)` 是未初始化的原始記憶體，**絕不能當成有效的 `T` 去讀或 drop**。
3. **`ptr` 在 `cap > 0` 時指向一塊對 `T` 對齊、大小為 `cap * size_of::<T>()` 的有效 allocation**；`cap == 0` 時 `ptr` 是 dangling（懸空但對齊）的，此時不會被 deref。

C 的動態陣列有一模一樣的三條規則，只是沒人幫你檢查。Rust 的差別是：**這三條由你（unsafe 作者）負責維持，一旦維持住，編譯器與 borrow checker 就能保證外部使用者不可能打破它們**。這就是「unsafe 抽象」的契約。

> 如果你對 `NonNull`、`MaybeUninit`、`ptr::read`/`ptr::write` 還不熟，先回看 [Ch 18](./18-unsafe-advanced.md) 的相關節。

## 第一層：RawVec —— 只管記憶體，不管長度

我們把「配置/釋放記憶體」跟「追蹤長度」拆開。`RawVec<T>` 只負責一塊配置與它的容量；它不知道裡面初始化了幾個元素。標準庫真的就是這樣拆的（`alloc::raw_vec::RawVec`）。

```rust
use std::alloc::{self, Layout};
use std::mem;
use std::ptr::NonNull;

struct RawVec<T> {
    ptr: NonNull<T>,
    cap: usize,
}

impl<T> RawVec<T> {
    fn new() -> Self {
        // ZST（zero-sized type）：size_of::<T>() == 0 時，容量設成 usize::MAX，
        // 因為 ZST 不佔記憶體，可以「裝無限多個」而永遠不需要配置。詳見後面 ZST 節。
        let cap = if mem::size_of::<T>() == 0 { usize::MAX } else { 0 };
        RawVec { ptr: NonNull::dangling(), cap }
    }

    fn grow(&mut self) {
        // ZST 永遠不該走到 grow：它的 cap 是 usize::MAX，len 永遠追不上。
        assert!(mem::size_of::<T>() != 0, "capacity overflow");

        let (new_cap, new_layout) = if self.cap == 0 {
            (1, Layout::array::<T>(1).unwrap())
        } else {
            let new_cap = 2 * self.cap;                       // 倍增策略：攤還 O(1) push
            (new_cap, Layout::array::<T>(new_cap).unwrap())
        };
        // Rust 規定單一 allocation 不得超過 isize::MAX bytes，否則指標算術會 UB。
        assert!(new_layout.size() <= isize::MAX as usize, "allocation too large");

        let new_ptr = if self.cap == 0 {
            unsafe { alloc::alloc(new_layout) }
        } else {
            let old_layout = Layout::array::<T>(self.cap).unwrap();
            let old_ptr = self.ptr.as_ptr() as *mut u8;
            unsafe { alloc::realloc(old_ptr, old_layout, new_layout.size()) }
        };

        // alloc/realloc 失敗回傳 null；handle_alloc_error 會 abort（不是 panic），
        // 因為配置失敗時我們無法保證能安全 unwind。
        self.ptr = match NonNull::new(new_ptr as *mut T) {
            Some(p) => p,
            None => alloc::handle_alloc_error(new_layout),
        };
        self.cap = new_cap;
    }
}

impl<T> Drop for RawVec<T> {
    fn drop(&mut self) {
        // 只釋放「這塊記憶體」，不 drop 任何元素——那是 MyVec 的責任。
        if self.cap != 0 && mem::size_of::<T>() != 0 {
            let layout = Layout::array::<T>(self.cap).unwrap();
            unsafe { alloc::dealloc(self.ptr.as_ptr() as *mut u8, layout); }
        }
    }
}
```

幾個關鍵決策，逐一說為什麼：

- **為什麼 `NonNull<T>` 而不是 `*mut T`？** 三個原因。(1) `NonNull` 保證非空，讓 `Option<MyVec<T>>` 可以享受 niche optimization（[Ch 15](./15-memory-layout.md)）。(2) `NonNull<T>` 對 `T` **covariant**，`*mut T` 是 **invariant**——這對容器是對的變異數，本章最後一節專門講。(3) 語意上宣告「我保證這永遠不是 null」。
- **為什麼用 `Layout::array::<T>`？** 它一次算好 `size` 與 `align`，並在溢位時回傳 `Err`。手動 `size_of * cap` 會忘記對齊、會忘記溢位檢查。
- **`realloc` vs 重新 alloc+memcpy**：`realloc` 讓 allocator 有機會原地擴張（若後面剛好有空間），省掉一次搬移。C 的 `realloc` 同樣如此。

## 第二層：MyVec —— 在 RawVec 上維持 len invariant

```rust
use std::ops::{Deref, DerefMut};
use std::ptr;

pub struct MyVec<T> {
    buf: RawVec<T>,
    len: usize,
}

impl<T> MyVec<T> {
    pub fn new() -> Self {
        MyVec { buf: RawVec::new(), len: 0 }
    }

    fn ptr(&self) -> *mut T { self.buf.ptr.as_ptr() }
    fn cap(&self) -> usize { self.buf.cap }

    pub fn push(&mut self, elem: T) {
        if self.len == self.cap() { self.buf.grow(); }
        // 此刻 self.len < cap，slot [len] 是未初始化的原始記憶體。
        // ptr::write：把 elem 的 bytes 寫進去，且【不】drop 那塊記憶體舊有的內容
        //（因為那裡沒有有效的 T，用 =賦值 會先 drop 舊值 → 對未初始化記憶體 drop = UB）。
        unsafe { ptr::write(self.ptr().add(self.len), elem); }
        self.len += 1;   // 先寫成功、再遞增 len：維持「[0,len) 全初始化」
    }

    pub fn pop(&mut self) -> Option<T> {
        if self.len == 0 { return None; }
        self.len -= 1;   // 先遞減 len：這一步把 slot [len] 移出「已初始化」區
        // ptr::read：把該 slot 的 bytes 搬出來成一個 owned T，
        // 記憶體本身留著（未初始化了），下次 push 會覆寫。
        unsafe { Some(ptr::read(self.ptr().add(self.len))) }
    }

    pub fn len(&self) -> usize { self.len }
}
```

盯著 `push`/`pop` 裡「改 `len` 的那一行」——它的**順序**就是 invariant 2 的全部。`push` 先 `write` 再 `len += 1`：任何時刻只要 `len` 涵蓋到的 slot，就一定已經寫過了。`pop` 先 `len -= 1` 再 `read`：`read` 搬走的那個 slot 已經不在 `[0, len)` 裡了，所以之後不會有人再把它當有效值。順序反過來就是 bug（本章下面會用 Miri 演給你看）。

`Drop`、`Deref` 補齊：

```rust
impl<T> Drop for MyVec<T> {
    fn drop(&mut self) {
        // 逐一 drop 已初始化的元素。pop() 一路搬出來 drop（Option<T> 離開作用域自動 drop）。
        // 這是 exception-safe 的寫法：即使某個元素 drop 時 panic，其餘的仍會被處理。
        while self.pop().is_some() {}
        // 元素清完後，self.buf（RawVec）的 Drop 會被自動呼叫，釋放記憶體。
    }
}

impl<T> Deref for MyVec<T> {
    type Target = [T];
    fn deref(&self) -> &[T] {
        // 這是 MyVec 的槓桿：一旦轉成 &[T]，len()/iter()/get()/sort() 全都白拿。
        // 安全前提：ptr 有效、len 個元素都已初始化、對齊正確——正是我們的三條 invariant。
        unsafe { std::slice::from_raw_parts(self.ptr(), self.len) }
    }
}
impl<T> DerefMut for MyVec<T> {
    fn deref_mut(&mut self) -> &mut [T] {
        unsafe { std::slice::from_raw_parts_mut(self.ptr(), self.len) }
    }
}
```

`Deref` 到 `[T]` 是最划算的一步。你只要維持住「`ptr` 指向 `len` 個已初始化元素」，`&[T]` 上所有既有方法（`.iter()`、`.get()`、`.first()`、`v[i]` 的 `Index`、`.sort()`……）全部免費繼承。這就是為什麼你只寫了 `push`/`pop` 卻能 index 和 iterate。

### 真的跑一遍

把上面三段拼成一個檔（含一個 `main`），在 WSL 跑：

```rust
fn main() {
    let mut v: MyVec<i32> = MyVec::new();
    for i in 0..10 { v.push(i * i); }
    println!("len = {}, cap = {}", v.len(), v.cap());
    println!("v[3] = {}", v[3]);                     // 靠 Deref → slice Index
    println!("sum = {}", v.iter().sum::<i32>());     // 靠 Deref → slice iter
    v[0] = 999;                                      // 靠 DerefMut
    println!("after v[0]=999: {:?}", &v[..]);
    while let Some(x) = v.pop() { print!("{} ", x); }
    println!();

    let mut sv: MyVec<String> = MyVec::new();        // 帶 Drop 的元素
    sv.push(String::from("alpha"));
    sv.push(String::from("beta"));
    sv.push(String::from("gamma"));
    println!("strings: {:?}", &sv[..]);              // sv drop 時逐一釋放三個 String

    let mut zv: MyVec<()> = MyVec::new();            // ZST
    for _ in 0..5 { zv.push(()); }
    println!("ZST len = {}, cap = {}", zv.len(), zv.cap());
    println!("ZST pop = {:?}", zv.pop());
}
```

實際輸出（`rustc -O myvec.rs && ./myvec`）：

```
len = 10, cap = 16
v[3] = 9
sum = 285
after v[0]=999: [999, 1, 4, 9, 16, 25, 36, 49, 64, 81]
81 64 49 36 25 16 9 4 1 999 
strings: ["alpha", "beta", "gamma"]
ZST len = 5, cap = 18446744073709551615
ZST pop = Some(())
```

`cap = 16`：push 10 個，容量走 0→1→2→4→8→16，倍增策略。`ZST cap = usize::MAX`（即 `2^64 - 1`），永遠不配置記憶體，卻能 push/pop 正常。

## 用 Miri 證明它沒有 UB

輸出對不代表沒 UB——UB 很多時候「看起來對」。這正是 [Ch 20](./20-memory-model-ub.md) 講的 Miri 的用武之地。把同一份 code 丟進 cargo 專案跑 Miri：

```bash
cargo new myvec_miri
cp myvec.rs myvec_miri/src/main.rs
cd myvec_miri && cargo +nightly miri run
```

實際輸出（節錄尾段）：

```
    Finished `dev` profile [unoptimized + debuginfo] target(s)
     Running `...cargo-miri runner target/miri/.../myvec_miri`
len = 10, cap = 16
v[3] = 9
sum = 285
after v[0]=999: [999, 1, 4, 9, 16, 25, 36, 49, 64, 81]
81 64 49 36 25 16 9 4 1 999 
strings: ["alpha", "beta", "gamma"]
ZST len = 5, cap = 18446744073709551615
ZST pop = Some(())
```

Miri 跑完程式，**沒有印任何 UB 報告**——它在 Stacked/Tree Borrows 模型下逐指令檢查了每次配置、每次 `ptr::write`/`read`、每次 slice 建構、每次 dealloc，沒抓到違規。這是「乾淨」的意思。C 的動態陣列你拿不到這種保證：valgrind 只看得到你這次執行踩到的路徑，且看不懂 aliasing 規則。

### 反例：invariant 一破，Miri 立刻抓

把 `pop` 寫錯——搬走元素卻**忘了遞減 `len`**（一個很常見的手滑）：

```rust
// BUG 版：讀出來但 len 沒變，slot 還被算在「已初始化」裡
fn pop_buggy(&mut self) -> Option<T> {
    if self.len == 0 { return None; }
    unsafe { Some(ptr::read(self.buf.ptr.as_ptr().add(self.len - 1))) }
    // ↑ 少了 self.len -= 1
}
```

於是 `pop_buggy` 把 `String` 的所有權搬出來給呼叫者，但 `len` 還是 1。`MyVec` drop 時又對同一個 slot `drop_in_place` 一次——double free。先看普通執行：

```
popped: hello
free(): double free detected in tcache 2
```

glibc 的 malloc 剛好偵測到了，但這純屬運氣——換個 allocator 或換個型別就是靜默的記憶體損毀。再看 Miri：

```
error: Undefined Behavior: constructing invalid value of type &mut [u8]:
       encountered a dangling reference (use-after-free)
   --> .../library/core/src/ptr/mod.rs:823:24
    |
823 |     unsafe { drop_glue(&mut *to_drop) }
    |                        ^^^^^^^^^^^^^ Undefined Behavior occurred here
    ...
            2: <std::vec::Vec<u8> as std::ops::Drop>::drop
            ...
            5: std::ptr::drop_in_place::<std::string::String>
```

Miri 精準指到「對已經被搬走（其 heap buffer 已釋放）的 `String` 再次 drop」。它不靠運氣、不靠 allocator 恰好偵測——它靠 model。這就是 unsafe 抽象開發者手上最強的驗證工具：**你維持 invariant 的每一步都能被機器複查**。

## 對照：C 的手刻動態陣列

同樣的邏輯，C 版本長這樣：

```c
#include <stdlib.h>
#include <string.h>

typedef struct {
    int   *ptr;
    size_t cap;
    size_t len;
} IntVec;

void vec_init(IntVec *v) { v->ptr = NULL; v->cap = 0; v->len = 0; }

void vec_push(IntVec *v, int e) {
    if (v->len == v->cap) {
        size_t nc = v->cap ? v->cap * 2 : 1;
        int *np = realloc(v->ptr, nc * sizeof(int));  // 沒檢查 NULL、沒檢查溢位
        v->ptr = np;
        v->cap = nc;
    }
    v->ptr[v->len++] = e;
}

int vec_pop(IntVec *v) { return v->ptr[--v->len]; }   // 沒檢查 len==0

void vec_free(IntVec *v) { free(v->ptr); }            // 元素若是 struct 帶指標，這裡漏掉
```

底層邏輯一字不差：三欄位、倍增、`realloc`、`free`。但攤開差異：

| 面向 | C `IntVec` | Rust `MyVec<T>` |
|---|---|---|
| 型別安全 | `int` 寫死；泛型要 `void*` + `memcpy` + size 參數 | `MyVec<T>` 對任何 `T` 都型別安全 |
| 元素解構 | 元素是 `struct { char *name; }` 時，`vec_free` **不會**幫你 free `name`，得自己寫迴圈 | `Drop` 自動逐一 drop，`String` 的 heap buffer 自動釋放 |
| 越界 pop | `vec_pop` 在 `len==0` 時 `--len` 變 `SIZE_MAX`，下次 `ptr[huge]` 直接爆 | `pop` 回 `Option`，`None` 是型別強制的 |
| 移動語意 | `memcpy` 之後原地還有一份 bytes（shallow copy 陷阱） | `ptr::read` 搬走後 `len` 移出範圍，不會被 double-drop |
| 驗證 | valgrind（只看這次路徑，不懂 aliasing） | Miri（model-based，抓 aliasing/UB） |
| 外部誤用 | 呼叫者能直接改 `v->len` 破壞 invariant | `len` 是 private，safe API 外部**無法**破壞 invariant |

最後一列是關鍵。C 的 `IntVec` 把三個欄位攤在外面，任何人都能 `v.len = 999` 然後越界。Rust 的 `MyVec` 把 `buf`/`len` 設 private，外部只能透過 `push`/`pop` 動它——而這兩個方法由你證明過維持 invariant。**這就是「安全抽象」：unsafe 被關進一個外部再怎麼玩都無法打破的盒子。**

## 為什麼是「倍增」：攤還分析

`grow` 用 `2 * cap` 不是隨便選的。假設你 push N 個元素，容量從 1 開始倍增：擴容發生在 size 1、2、4、8、…、N/2、N，每次擴容要把舊的全部搬過去（`realloc` 最壞情況搬 `cap` 個）。總搬移量是 `1 + 2 + 4 + ... + N ≈ 2N`，即**攤還（amortized）O(1) 每次 push**——N 次 push 總共 O(N) 工作。

若改成「每次 +1」呢？push 到第 k 個要搬 `k-1` 個，總搬移量 `1 + 2 + ... + N = N²/2`，即每次 push 攤還 O(N)——災難。這就是為什麼所有動態陣列（C++ `std::vector`、Rust `Vec`、C 手刻）都用**乘法**成長而非加法。

倍率選 2 還是其他？std 的 `Vec` 用 2 倍。有人主張 1.5 倍能讓「釋放的舊區塊」在多次擴容後有機會被重用（2 倍時每次新配置都比之前所有釋放的總和還大，永遠塞不回舊洞）。這是有真實爭議的取捨——`folly::fbvector` 用 1.5，多數標準庫用 2。對本課的 `MyVec`，2 倍夠好，你只要知道「為什麼不是 +1」比「2 還是 1.5」更重要。

實測 std `Vec<u8>` 的擴容時機（在每次 push 後看 `capacity()` 有沒有變）：

```
push # 1 -> grow, cap 0 -> 8
push # 9 -> grow, cap 8 -> 16
push #17 -> grow, cap 16 -> 32
push #33 -> grow, cap 32 -> 64
```

注意 std 的 `Vec<u8>` 第一次不是配 1 而是配 **8**——對小元素型別，std 有個「首次配置至少放幾個」的下限（避免對 byte 陣列一開始瘋狂擴容）。之後才是乾淨的倍增 8→16→32→64。我們的 `MyVec` 為了教學簡單從 1 開始（0→1→2→4→…），攤還複雜度一樣是 O(1)，只是常數大一點、前幾次擴容較頻繁。這是「教學版」與「生產版」的一個具體差異，不影響正確性。

## MaybeUninit：`ptr::write` 背後的型別

前面用 `ptr::write` 往未初始化 slot 寫值，繞過了「對未初始化記憶體 drop」的 UB。但 `ptr::write` 是「相信程式員」的低階工具。更型別安全的表達是 `MaybeUninit<T>`（[Ch 18](./18-unsafe-advanced.md) 介紹過）：它是一個「可能未初始化的 `T`」的型別包裝，編譯器知道它不保證有效，不會對它自動 drop。

如果把 `RawVec` 的元素型別設計成 `NonNull<MaybeUninit<T>>`，`push` 就變成 `slot.write(elem)`（`MaybeUninit::write`），語意上更清楚「這裡從未初始化變成初始化」。std 的 `Vec` 內部其實用裸 `ptr::write`（為了跟舊 code 相容與細節控制），但**新寫的 unsafe 抽象普遍偏好 `MaybeUninit`**，因為它讓「未初始化」在型別層面可見，減少手滑。本章的 `MyVec` 用 `ptr::write` 是為了對照 C 的 `ptr[i] = v` 更直接；生產級寫法你會更常看到 `MaybeUninit`。兩者對 `MyVec` 的行為與 Miri 結果一致，差別在「哪一種讓錯誤更難犯」。

## 難點一：ZST（零大小型別）

`MyVec<()>`、`MyVec<[u8; 0]>`——元素大小為 0。問題：`Layout::array::<()>(n)` 大小是 0，`alloc(0)` 是 UB（Rust 規定不能配置 0-size layout）。而且 `ptr.add(n)` 對 ZST 是 no-op（`n * 0 == 0`），所有元素「疊在同一個位址」。

解法（也是 std 的做法）：ZST 根本不碰 heap。把 `cap` 設 `usize::MAX`，`push` 永遠不觸發 `grow`（`len` 永遠 `< usize::MAX`），`ptr` 始終是 dangling 的 `NonNull::dangling()`。`push` 的 `ptr::write` 對 ZST 是把「0 個 byte」寫到某個對齊位址——no-op 但合法。`pop` 的 `ptr::read` 同理讀出 0 個 byte 組成 `()`。上面的輸出 `ZST cap = 18446744073709551615` 就是這條路徑，且 Miri 認可它乾淨。

## 難點二：panic safety（元素 drop 時 panic）

`Drop` 那段用 `while self.pop().is_some() {}` 而不是「算好 len 一口氣 dealloc」，是為了 panic safety。想像 `T` 的 `Drop` 會 panic（例如某個測試型別）。逐一 pop 時，`Option<T>` 在每次迴圈末尾 drop 一個元素；若第 3 個 panic，unwind 會展開——而 `MyVec` 的其餘欄位（`buf`）仍會在 unwind 過程被正常 drop，釋放記憶體。

真正棘手的是 `push` 中途 panic 的情境：如果 `grow` 之後、`ptr::write` 之前發生 panic 會怎樣？答案是——不會有問題，因為 `len` 還沒遞增，那個 slot 還在 `[len, cap)`（未初始化區），沒人會去 drop 它。這就是「先 write 再 `len += 1`」順序的另一個好處：任何 panic 點都不會留下「`len` 涵蓋但未初始化」的破口。std 的 `Vec` 對更複雜的操作（`insert`、`extend`、`drain`）花了大量心思處理 panic safety，這裡我們只碰到最簡單的一角。

> 「exception safety」在 C++ 是同一個問題：`std::vector::push_back` 若元素 copy ctor 拋例外，vector 必須維持 strong exception guarantee（狀態不變）。Rust 的 panic 對應 C++ 的 exception，`Drop` 對應解構子，處理的核心關切完全一樣。

## 難點三：variance —— 為什麼一定是 `NonNull` 不能是 `*mut T`

這是全章最微妙的一點，直接連 [Ch 5](./05-lifetimes-advanced.md) 的 variance。「一個容器 `MyVec<T>` 對 `T` 該是什麼變異數？」答案是**covariant**：如果 `&'static str` 是 `&'a str` 的子型別（活得更久 = 子型別），那 `MyVec<&'static str>` 也該能當 `MyVec<&'a str>` 用——把一個裝著長命引用的 vec，當成裝短命引用的 vec，是安全的。

`NonNull<T>` 對 `T` covariant；`*mut T` 對 `T` **invariant**。用哪個，決定了你的 `MyVec` 有沒有正確的變異數。實測給你看：

```rust
use std::ptr::NonNull;

struct WithRawPtr<T> { ptr: *mut T }
struct WithNonNull<T> { ptr: NonNull<T> }

// 需要 covariance：把長命的當短命的用
fn shorten_raw<'a>(x: WithRawPtr<&'static str>) -> WithRawPtr<&'a str> { x }
fn shorten_nn<'a>(x: WithNonNull<&'static str>) -> WithNonNull<&'a str> { x }
```

`NonNull` 版單獨編譯——過（只有「never constructed」的 warning）。`*mut T` 版單獨編譯：

```
error: lifetime may not live long enough
 --> cov_raw.rs:2:74
  |
2 | fn shorten_raw<'a>(x: WithRawPtr<&'static str>) -> WithRawPtr<&'a str> { x }
  |                -- lifetime `'a` defined here    ^ returning this value requires that `'a` must outlive `'static`
  |
  = note: requirement occurs because of the type `WithRawPtr<&str>`, which makes the generic argument `&str` invariant
  = note: the struct `WithRawPtr<T>` is invariant over the parameter `T`
  = help: see <https://doc.rust-lang.org/nomicon/subtyping.html> for more information about variance
```

`rustc` 直接告訴你：`*mut T` 讓 struct 對 `T` invariant，於是這個安全的縮短就編不過。`NonNull<T>` 內部其實是 `*const T` 包一層，`*const T` 是 covariant 的，所以 `NonNull` 也 covariant——這正是容器要的。標準庫的 `Vec`、`Box` 都用 `Unique<T>`（`NonNull` + covariance + `PhantomData<T>` 表達 ownership），就是為了這個。

如果你的 `MyVec` 用 `*mut T`，它會被錯誤地標成 invariant，某些完全安全的生命週期縮短會莫名其妙編不過，而你會抓不到原因。這是「選對指標型別」在 unsafe 抽象裡的實際後果，不是學術細節。

## 踩雷集錦

1. **用 `=` 賦值寫入未初始化 slot**：`self.ptr().add(len) = elem`（假設能這樣寫）或 `*p = elem` 會先 **drop 舊值**——但那裡沒有有效的 `T`，對垃圾 bytes 呼叫 drop = UB。一定用 `ptr::write`，它只寫不 drop。
2. **`pop` 忘記先遞減 `len`**：本章 Miri 反例演過。搬走元素卻讓 `len` 仍涵蓋它 → drop 時 double free。順序是 invariant 的一部分，不是風格。
3. **grow 時忘了 `assert size <= isize::MAX`**：Rust 的指標算術（`add`/`offset`）在偏移超過 `isize::MAX` bytes 時是 UB。超大配置若不擋，`ptr.add(len)` 可能溢位成 UB。
4. **ZST 走進 `alloc`**：`Layout` 大小為 0 時呼叫 `alloc` 是 UB。ZST 必須完全繞過 heap（`cap = usize::MAX`）。忘了這點，`MyVec<()>` 一 push 就爆。
5. **用 `*mut T` 當內部指標**：不會 UB，但會讓 `MyVec` 變 invariant，喪失容器該有的 covariance（見上一節）。而且失去 non-null niche，`Option<MyVec<T>>` 白白多一個 tag byte。

## 進階：再往深一層

- **`IntoIter` 與 `Drain`**：真正的 `Vec` 還要能 `for x in v`（by-value 消耗）與 `v.drain(..)`。這需要一個 iterator 持有 `RawVec` 並用兩個指標（start/end）掃描，且要正確處理「iterator 中途被 drop 時，剩下沒 yield 的元素要 drop、記憶體要釋放」。這是 panic safety 的進階戰場，Rustonomicon 的「Implementing Vec」章一步步做完整個 `Vec`，強烈建議照著手打一遍。
- **`Send`/`Sync`**：`MyVec<T>` 該不該是 `Send`/`Sync`？裸指標讓 auto trait **不會**自動實作。`Vec<T>` 手動 `unsafe impl Send for Vec<T> where T: Send`。這在 [Ch 23](./23-threads-send-sync.md) 講。你的 `MyVec` 目前不是 `Send`，這對單執行緒沒差，但跨執行緒就得補。
- **allocator API**：Rust 有 unstable 的 `Allocator` trait，讓 `Vec<T, A>` 能換配置器（arena、bump）。你的 `RawVec` 硬綁全域 allocator；生產級容器會參數化它。

## 動手練習

1. 把 `pop` 的兩行順序對調（先 `read` 再 `len -= 1`），跑 `cargo +nightly miri run`，看 Miri 報什麼、報在哪一行。
2. 幫 `MyVec` 加一個 `insert(&mut self, index: usize, elem: T)`：要先 `grow`（若滿），再用 `ptr::copy`（會處理重疊，相當於 `memmove`）把 `[index, len)` 往後挪一格，才 `ptr::write`。跑 Miri 確認乾淨。
3. 把內部 `NonNull<T>` 改成 `*mut T`，觀察哪些既有 code 開始編不過或掉 niche（`std::mem::size_of::<Option<MyVec<i32>>>()`）。

## 本章重點整理

- unsafe 抽象 = 把裸指標/手動記憶體管理關進一組 safe API 後面，向外承諾一組 **invariant**，並在**每條路徑**上維持它們。
- `MyVec` 的三條 invariant：`len <= cap`、`[0,len)` 全初始化、`ptr` 有效且對齊。破任何一條就是 UB 種子。
- `push` 先 write 再 `len += 1`、`pop` 先 `len -= 1` 再 read——這個**順序**就是 invariant 的執行層落地，也是 panic safety 的基礎。
- Miri 能逐指令複查你有沒有維持 invariant；輸出對不等於沒 UB，Miri clean 才算數。
- 內部指標用 `NonNull<T>` 而非 `*mut T`：拿 covariance + non-null niche，這是容器的正確選擇。

## 自我檢核

- [ ] 不看筆記，能說出 `MyVec` 的三條 invariant，以及各自被打破時會發生什麼 UB
- [ ] 能解釋為什麼 `push` 用 `ptr::write` 而不是 `=`，以及為什麼 `len` 的增減順序不能反
- [ ] 如果面試官問「Miri clean 代表什麼、不代表什麼」，你能答出「model-based 複查了這次執行的所有 aliasing/記憶體操作，但只涵蓋跑到的路徑」
- [ ] 能說出 `NonNull<T>` vs `*mut T` 在 variance 與 niche 上的兩個具體差異
- [ ] 知道 ZST 為什麼要特判、panic safety 為什麼影響 `Drop`/`push` 的寫法

## 延伸閱讀

### 官方文件 / 權威來源

- **[The Rustonomicon — Implementing Vec](https://doc.rust-lang.org/nomicon/vec/vec.html)**
  - **讀哪裡**：整章（`vec-layout` 到 `vec-final`）。它從 `RawVec` 一路做到 `IntoIter`、`Drain`、ZST 全支援。
  - **和本章的關聯**：本章的 `MyVec` 就是這章的精簡版；讀它把我們略過的 `IntoIter`/`Drain`/panic safety 補完整。前提：讀完本課 Ch 17/18/20。
- **[std `alloc::raw_vec` 原始碼](https://github.com/rust-lang/rust/blob/master/library/alloc/src/raw_vec/mod.rs)**
  - **讀哪裡**：`RawVec::grow_amortized` 與 `current_memory`。看真正的 std 怎麼處理 ZST、溢位、amortized growth。
  - **學到什麼**：生產級 `RawVec` 比教學版多做的邊界檢查與 `Allocator` 參數化。

### 部落格 / 技術文章

- **[The Rustonomicon — Subtyping and Variance](https://doc.rust-lang.org/nomicon/subtyping.html)**
  - **這篇說什麼**：Rust 的 variance 完整規則表（`&T`/`&mut T`/`*const T`/`*mut T`/`NonNull`/`PhantomData` 各是什麼變異數）。
  - **讀哪裡**：variance 表格那一節，配本課 [Ch 5](./05-lifetimes-advanced.md) 一起看。
  - **為什麼值得讀**：本章「為什麼用 NonNull」的完整理論依據都在這，官方權威。

### 工具文件

- **[Miri README](https://github.com/rust-lang/miri)**
  - **讀哪裡**：「Using Miri」與「Common problems」段。
  - **和本章的關聯**：本章用 `cargo +nightly miri run` 驗證 `MyVec`；這裡告訴你 `MIRIFLAGS`、如何開 Tree Borrows、如何處理 Miri 不支援的 FFI（本課 [Ch 20](./20-memory-model-ub.md) 已鋪陳）。

寫完這章，你已經能從裸記憶體手刻一個被 Miri 認證的安全容器。下一章換一個維度：把 std 整個拿掉——no_std 環境，embedded 與 kernel 的世界。

→ [Ch 22 no_std：embedded 與 kernel 場景](./22-no-std.md)
