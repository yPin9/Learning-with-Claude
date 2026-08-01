# Ch 17 — unsafe 基礎：五種 superpower

> **目標**：破除「`unsafe` = 關掉借用檢查」這個最普遍的誤解——`unsafe` 不關掉任何檢查，它只是解鎖五種**編譯器沒法幫你驗證安全性**的操作，並把「這五件事我來保證正確」的責任轉給你。逐一過這五種 superpower（解引用裸指標、呼叫 unsafe fn、存取/修改 `static mut`、實作 unsafe trait、存取 union 欄位）；把裸指標 `*const T`/`*mut T` 玩到對照 C 指標（可 null、可別名、可算術，建立安全、解引用要 unsafe）；建立「soundness（健全性）」這個 C 從來沒有的概念——unsafe 區塊裡違反不變量就是 UB，責任在你（真跑一個 UB 讓下一章 Miri 抓）；最後講 unsafe 的正確用法：把 unsafe 包在安全 API 裡、維持 invariant，以及 `unsafe fn` 的契約。對照 C：C 沒有這條「安全/不安全」的線，整個語言都在線的危險側。

> **環境**：Rust 以 `rustc 1.97.1`（stable，預設 edition 2015）在 x86-64 Linux（WSL2）；UB 示範會在 [Ch 20](./20-memory-model-ub.md) 用 `cargo +nightly miri run` 抓（Miri 0.1.0 nightly 2026-07-31）。所有編譯錯誤、程式輸出、UB 的隨機值、Miri 報告，都是本機真跑，非推測。

## 為什麼需要這個？

你做了那麼多年 C，一句話就懂 unsafe 的價值——但也最容易誤解它。

C 的世界裡，**沒有安全與不安全的分界線**。每一次 `*p`、每一次 `arr[i]`、每一次 `malloc`/`free`，你都在做「可能 UB 的操作」，但編譯器一視同仁，不會告訴你「這行危險、那行安全」。整個語言都在危險側，靠你的紀律和 review 撐著。UAF、越界、double free、data race——這些 bug 在 C 裡沒有語法上的標記，藏在任何一行看起來無害的程式碼裡。

Rust 的做法相反：它把絕大多數程式碼放在**安全側**（borrow checker、型別系統保證無 UB），然後用一個關鍵字 `unsafe` 把「編譯器沒法驗證安全性的操作」圈出來。這是一條**顯性的線**。看到 `unsafe` 就知道「這裡有編譯器擔保不了的假設，出事往這裡查」。audit 一個 10 萬行的 Rust crate，你只要盯那幾百行 `unsafe`——這是 C 給不了的定位能力。

但這條線帶來一個必須先破除的誤解：**`unsafe` 不是「關掉借用檢查」的開關**。很多從 C 來的人以為 `unsafe { }` 裡面 Rust 就變回 C 了，可以為所欲為。錯得離譜。`unsafe` 區塊裡，借用檢查照跑、型別檢查照跑、所有權照跑——它**只**多解鎖五個特定操作，這五個操作是編譯器「無法自動證明安全」的。你在 unsafe 裡寫的 99% 程式碼跟 safe 裡一模一樣受檢查，只有那五種操作是你接手保證。

搞懂這條線畫在哪、五種 superpower 是什麼、你接手的責任（soundness）具體是什麼——這是後面所有 unsafe 章節（transmute、FFI、手刻 Vec、kernel）的地基。

## 先建立直覺

把 `unsafe` 想成一份**責任轉移合約**，不是一個「危險模式開關」。

```
   safe Rust                          unsafe 區塊
   ┌────────────────────┐            ┌────────────────────────────┐
   │ 編譯器保證：         │            │ 編譯器仍保證：              │
   │  - 無 UAF           │            │  - 借用檢查（照跑）         │
   │  - 無資料競爭        │   進入     │  - 型別檢查（照跑）         │
   │  - 無越界            │  ───────▶  │  - 所有權（照跑）           │
   │  - 型別安全          │            │                            │
   │  所有操作都被驗證     │            │ 但這五種操作編譯器驗不了，  │
   └────────────────────┘            │ 由「你」保證正確：          │
                                     │  1. 解引用裸指標            │
                                     │  2. 呼叫 unsafe fn          │
                                     │  3. 存取/改 static mut      │
                                     │  4. 實作 unsafe trait       │
                                     │  5. 存取 union 欄位         │
                                     └────────────────────────────┘
```

關鍵直覺：`unsafe` **不擴大**你能違反的規則，它只**允許**五種特定操作，而且把「這五種操作的安全性」的舉證責任從編譯器移到你身上。你簽了這份合約，就等於說「這五件事我用人腦驗證過了，編譯器你放行」。簽了但沒真的驗證好 → UB，跟 C 一樣，但至少 bug 圈在 `unsafe` 那幾行。

> 對照你的 C 直覺：在 C，你**每一行**都簽了這份合約（整個語言都是 unsafe），只是沒有語法標記提醒你。Rust 只在 `unsafe` 那幾行要你簽——線畫得清楚，責任範圍就小。

## 五種 superpower，逐一實測

`unsafe` 解鎖的**只有**這五種操作。一次全上：

```rust
// superpower 2: 呼叫 unsafe fn
unsafe fn dangerous() -> i32 { 99 }

// superpower 3: 存取/修改 static mut
static mut COUNTER: u32 = 0;

// superpower 4: 實作 unsafe trait
unsafe trait Contract { fn ok(&self) -> bool; }
struct S;
unsafe impl Contract for S { fn ok(&self) -> bool { true } }

// superpower 5: 存取 union 欄位
union U { i: u32, f: f32 }

fn main() {
    unsafe {
        println!("unsafe fn: {}", dangerous());
        COUNTER += 1;
        println!("static mut COUNTER = {}", COUNTER);
    }
    let s = S;
    println!("unsafe trait: {}", s.ok());
    let u = U { i: 0x40490fdb };   // 這是 3.1415927 的 IEEE754 bit pattern
    unsafe {
        println!("union 讀 i = 0x{:x}", u.i);
        println!("union 讀 f = {}   (同一塊記憶體重新詮釋)", u.f);
    }
}
```

真跑（有個 warning，等下講）：

```
unsafe fn: 99
static mut COUNTER = 1
unsafe trait: true
union 讀 i = 0x40490fdb
union 讀 f = 3.1415927   (同一塊記憶體重新詮釋)
```

（第一種 superpower——解引用裸指標——下一節單獨深挖。）逐個看你接手了什麼責任：

- **呼叫 `unsafe fn`（superpower 2）**：`dangerous` 被標 `unsafe fn`，代表「呼叫我之前有你必須滿足的前提條件（契約），否則 UB」。呼叫它就得在 `unsafe` 塊裡，等於你聲明「我讀過它的契約、我保證滿足了」。標準庫一堆這種函式（`slice::get_unchecked` 要你保證 index 不越界、`String::from_utf8_unchecked` 要你保證是合法 UTF-8）。
- **存取/改 `static mut`（superpower 3）**：全域可變狀態。危險在於**多 thread 同時讀寫 = data race = UB**，編譯器沒法保證你只在單 thread 用它。這個東西在現代 Rust 幾乎不該用（下面的 warning 就在勸退），有 `Atomic*`、`Mutex`、`OnceLock` 等安全替代。
- **實作 `unsafe trait`（superpower 4）**：某些 trait（`Send`、`Sync`、`GlobalAlloc`）帶有「實作者必須維持的記憶體安全不變量」，編譯器沒法自動驗證你的實作有維持，所以要 `unsafe impl`——你簽字保證。`unsafe impl Send for MyType {}` 就是你對編譯器說「我保證這型別跨 thread 傳是安全的」。
- **存取 `union` 欄位（superpower 5）**：`union` 所有欄位共用同一塊記憶體。讀 `u.f` 時，你把當初以 `u.i` 寫進去的 bit pattern **重新詮釋**成 `f32`——`0x40490fdb` 這個整數的 bit pattern 剛好是浮點數 `3.1415927`（IEEE 754 單精度）。危險在於你可能讀到「當初沒寫進這個欄位」的垃圾（type punning）。這正是 C 的 union，Rust 保留它主要為了 FFI。

**那個 warning 是重點教材，不是雜訊**。編譯 `static mut` 那段時：

```
warning: creating a shared reference to mutable static
  --> u3.rs:19:45
   |
19 |         println!("static mut COUNTER = {}", COUNTER);
   |                                             ^^^^^^^ shared reference to mutable static
   |
   = note: shared references to mutable statics are dangerous; it's undefined behavior if the static
     is mutated or if a mutable reference is created for it while the shared reference lives
   = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
```

Rust 團隊認定 `static mut` 太容易誤用，2024 edition 起對「取它的參考」發 `static_mut_refs` lint（本例在預設 edition 2015 是 warning，2024 edition 是 deny）。這是 Rust 主動把一個 unsafe 老坑標成陷阱的例子——連 unsafe 內部都有「更 unsafe 的次等做法」被勸退。實務上：需要全域可變就用 `AtomicU32`、`Mutex<T>`、`OnceLock<T>`，別碰 `static mut`。

## 裸指標：*const T 與 *mut T

第一種 superpower——解引用裸指標——值得單獨挖，因為它是 unsafe 的核心，也是你 C 直覺最直接對應的地方。

Rust 有兩種裸指標：`*const T`（唯讀）和 `*mut T`（可寫）。它們就是 C 的指標——**可 null、可別名、可做算術、生命週期不被追蹤**。關鍵分界：**建立裸指標是安全的（不需要 `unsafe`），解引用才需要 `unsafe`**。理由很直白：建立一個指標值不會碰記憶體，解引用才會真的去讀那塊記憶體，那才是可能 UB 的動作。

```rust
fn main() {
    let x = 42i32;
    // 建立裸指標：安全，不需要 unsafe
    let p: *const i32 = &x;
    println!("裸指標建立 OK: p={:p}", p);
    // 解引用：需要 unsafe
    unsafe {
        println!("*p = {}", *p);
    }
    // 別名：兩個裸指標指同一位址，合法（C 也是）
    let q: *const i32 = &x;
    println!("p 和 q 別名同一位址: {}", p == q);
    // 指標算術
    let arr = [10, 20, 30];
    let base = arr.as_ptr();
    unsafe {
        println!("base[2] = {}", *base.add(2));   // 對照 C 的 base[2] / *(base+2)
    }
}
```

真跑：

```
裸指標建立 OK: p=0x7ffd3ebc2f84
*p = 42
p 和 q 別名同一位址: true
base[2] = 30
```

`let p: *const i32 = &x` 不需要 `unsafe`——建立指標值是安全的。`*p` 要包在 `unsafe` 裡。兩個裸指標 `p`、`q` 指向同一位址（**別名**）完全合法——這跟 safe Rust 的參考天差地別（safe 側同一時間不能有 `&x` 和 `&mut x`），裸指標**不受借用規則約束**，你可以有任意多個 `*mut T` 指同一塊（跟 C 一樣）。`base.add(2)` 是指標算術，對照 C 的 `base + 2` / `base[2]`。

**`*mut T`：透過裸指標寫。** `*const T` 只能讀，`*mut T` 能寫。寫一樣要 `unsafe`，因為那才真的改記憶體：

```rust
fn main() {
    let mut x = 10i32;
    let pm: *mut i32 = &mut x;      // 建立可寫裸指標：安全
    unsafe {
        *pm = 99;                    // 透過裸指標寫：unsafe
    }
    println!("x = {}", x);

    // 用 *mut 直接改一個 Vec 的元素（繞過 index 語法）
    let mut v = vec![1, 2, 3];
    let base: *mut i32 = v.as_mut_ptr();
    unsafe {
        *base.add(1) = 200;          // 等同 v[1] = 200，但走裸指標
    }
    println!("v = {:?}", v);
}
```

真跑：

```
x = 99
v = [1, 200, 3]
```

`*base.add(1) = 200` 透過裸指標把 `v` 的第 1 個元素改成 200——完全繞過 `v[1]` 的 bounds check。這是「快但危險」的路：沒有越界檢查（你保證 `add(1)` 不越界）、沒有借用檢查（你保證此時沒別的參考在讀 `v`）。手刻 `Vec`（Ch 21）就是靠這種裸指標讀寫實作 `push`/`get`，外面再包 bounds check 變成 sound 的安全 API。

**忘了包 `unsafe` 會怎樣？** 編譯器直接擋：

```rust
fn main() {
    let x = 42i32;
    let p: *const i32 = &x;
    let y = *p;             // 沒有 unsafe 包起來 -> 編譯錯誤
    println!("{}", y);
}
```

真跑（編不過，照抄）：

```
error[E0133]: dereference of raw pointer is unsafe and requires unsafe function or block
 --> u2.rs:4:13
  |
4 |     let y = *p;             // 沒有 unsafe 包起來 -> 編譯錯誤
  |             ^^ dereference of raw pointer
  |
  = note: raw pointers may be null, dangling or unaligned; they can violate aliasing rules and cause
    data races: all of these are undefined behavior
```

`dereference of raw pointer is unsafe and requires unsafe function or block`——編譯器不讓你在 safe 側解引用裸指標。那句 note 把裸指標的四大危險列全了：可能 null、可能懸空（dangling）、可能未對齊、可能違反別名規則造成 data race——**全都是 UB**。這正是為什麼解引用要你簽字：這四件事編譯器沒法幫你保證，你得自己確認指標有效、對齊、無 race。

裸指標的常見來源：`&x as *const T`（從參考轉）、`.as_ptr()`/`.as_mut_ptr()`（從容器拿）、`ptr::null()`（造 null）、FFI 邊界（C 函式回傳的指標）。你之後手刻資料結構（Ch 21）、做 FFI（Ch 19）全靠它。

## 底層機制：soundness 與 UB，責任在你

現在建立 Rust 最重要、也是 C 沒有的概念：**soundness（健全性）**。

一段 unsafe 程式碼是 **sound（健全）** 的，意思是：**不管呼叫者從安全側怎麼用它，都不可能觸發 UB**。反之，如果存在某種「安全的用法」能讓你的 unsafe 程式碼產生 UB，它就是 **unsound（不健全）**——即使那個 UB 平常不會發生。soundness 是二元的、絕對的：一個 unsafe 抽象要嘛對所有可能輸入都安全，要嘛就是壞的，沒有「大部分情況安全」這種中間地帶。

這條線 C 完全沒有。C 裡「這個函式傳錯參數會 crash」是家常便飯，沒人會說 `strcpy` 是「unsound」的——整個 C 都預設呼叫者要小心。Rust 把標準提高到：**只要你的 API 暴露成 safe（不用 `unsafe` 就能呼叫），你就必須保證它 sound**，否則你破壞了整個 Rust「safe 程式碼不可能 UB」的承諾。

**真跑一個 UB：解引用懸空裸指標。** 這是最經典的 unsafe 犯罪——回傳一個指向 local 變數的裸指標，函式返回後 local 就死了，指標懸空：

```rust
fn dangle() -> *const i32 {
    let local = 12345;      // 存在 stack frame 上
    &local as *const i32    // 回傳指向 local 的裸指標 -- local 馬上就要死
}                            // local 在這裡離開 scope，指標懸空

fn main() {
    let p = dangle();
    unsafe {
        // 解引用懸空指標 = UB。這裡「碰巧」印出還沒被覆蓋的舊值
        println!("*p = {}   <-- UB，值不可信", *p);
    }
}
```

真跑（rustc 1.97 有個 lint 先警告你，然後跑出垃圾值）：

```
warning: function returns a dangling pointer to dropped local variable `local`
 --> u4.rs:3:5
  |
3 |     &local as *const i32
  |     ------^^^^^^^^^^^^^^
  = note: a dangling pointer is safe, but dereferencing one is undefined behavior

*p = 32765   <-- UB，值不可信
```

`*p` 印出 `32765`——一個垃圾值（local 早就死了，這是 stack 上還沒被覆蓋的殘留）。注意那句 lint：`a dangling pointer is safe, but dereferencing one is undefined behavior`——**建立**懸空指標安全（沒碰記憶體），**解引用**才 UB。

**UB 的可怕在於「不可預測」，不是「一定 crash」。** 同一支程式，debug 版印 `32765`，我再用 `-O`（release）編一次，印出**不同的垃圾**：

```
$ rustc -O u4.rs -o u4o && ./u4o
*p = 23744   <-- UB，值不可信
```

`32765` vs `23744`——兩次不同的值。這是 UB 的本質：編譯器假設「你不會做 UB」，於是在這個假設下自由優化，一旦你真的做了 UB，行為就完全不受你掌控——可能印垃圾、可能 crash、可能「看起來正常」直到某天在別的優化等級/別的機器上爆炸。C 老手對這個「UB 隨優化等級變臉」的現象不陌生，Rust 的 UB 同樣殘酷，差別只在 Rust 把它圈在 `unsafe` 裡。

**對照 C：同樣的 UB，C 只給 warning、然後可能直接 segfault。** 一模一樣的懸空指標寫成 C：

```c
int *dangle(void) {
    int local = 12345;
    return &local;          // 回傳 local 位址
}
int main(void) {
    int *p = dangle();
    printf("*p = %d\n", *p); // 解引用懸空指標：UB
    return 0;
}
```

真跑（`gcc uaf.c -o uaf && ./uaf`）：

```
uaf.c:5:12: warning: function returns address of local variable [-Wreturn-local-addr]
    5 |     return &local;
      |            ^~~~~~
--- run ---
（無輸出，程式 segfault，exit code 139）
```

C 只給一句 `-Wreturn-local-addr` warning（預設不是 error，很多專案 warning 淹沒在噪音裡），然後執行時直接 segfault（exit 139 = SIGSEGV）。兩個關鍵差異：(1) **C 沒有 `unsafe` 標記**——這行危險程式碼跟旁邊無害的程式碼長得一樣，沒有語法提醒；(2) C 的 UB 同樣不可預測（這台機器 segfault，換個編譯/環境可能印垃圾）。Rust 把同一個 UB 圈在 `unsafe { *p }` 裡，一眼定位，還能用 Miri 精準抓——這是 Rust 相對 C 在 UB 治理上的實質優勢。

**這個 UB 下一章會被 Miri 抓。** Miri（Ch 20）是一個解釋器，逐指令執行你的程式並檢查每個記憶體操作是否 UB。上面這支程式跑 `cargo +nightly miri run` 的結果（本機真跑，Ch 20 詳講）：

```
error: Undefined Behavior: constructing invalid value of type &i32: encountered a
       dangling reference (use-after-free)
 --> src/main.rs:7:34
  |
7 |     unsafe { println!("*p = {}", *p); }
  |                                  ^^ Undefined Behavior occurred here
```

Miri 精準指出「dangling reference (use-after-free)」發生在解引用那行。這是 unsafe 開發的救命工具：肉眼看不出的 UB，Miri 幫你抓。**寫 unsafe 就該配 Miri 跑**——這是本課 Part 3 的核心工作流，Ch 20 全面展開。

## unsafe 的正確用法：包在安全 API 裡

到這裡你可能覺得 unsafe 很危險該少碰——對，但完全避開 unsafe 就沒法寫 `Vec`、`Rc`、做 FFI、寫 kernel driver。正確的態度不是「不用」，是「用對」。核心手法一句話：**把 unsafe 包在安全 API 裡，在裡面維持不變量（invariant），對外暴露一個不可能被誤用的介面。**

看標準庫怎麼做——`slice::split_at` 把一個 slice 切兩半，內部用 unsafe（裸指標造兩個 sub-slice），但對外是**完全安全**的，因為它在 unsafe 之前先 `assert` 了不變量：

```rust
// 把 unsafe 包在安全 API 裡：呼叫者不用寫 unsafe，因為 wrapper 維持了不變量。
fn split<T>(s: &[T], mid: usize) -> (&[T], &[T]) {
    assert!(mid <= s.len());                  // 不變量：mid 不越界（先檢查）
    let ptr = s.as_ptr();
    unsafe {
        // 有了上面的 assert，這兩個 from_raw_parts 保證不越界 -> sound
        (
            std::slice::from_raw_parts(ptr, mid),
            std::slice::from_raw_parts(ptr.add(mid), s.len() - mid),
        )
    }
}

fn main() {
    let v = [1, 2, 3, 4, 5];
    let (a, b) = split(&v, 2);       // 呼叫者完全不碰 unsafe
    println!("left={:?} right={:?}", a, b);
}
```

真跑：

```
left=[1, 2] right=[3, 4, 5]
```

`split` 的簽章沒有 `unsafe`——呼叫者 `split(&v, 2)` 不用簽任何字。unsafe 收在函式內部，而且**在解引用/造 slice 之前先 `assert!(mid <= s.len())`**：這個 assert 就是維持不變量的動作。有了它，裡面的 `from_raw_parts` 保證不會越界，所以整個 `split` 對**任何** `mid` 都 sound（越界的 `mid` 會 panic，不會 UB）。這就是 sound 的 unsafe 抽象：內部有 unsafe，但因為維持了不變量，外部無論怎麼呼叫都不可能 UB。

對比一個 **unsound** 的寫法（別這樣）：如果把 `assert!` 拿掉、簽章仍是 safe，那 `split(&v, 999)` 就會造出越界 slice → UB，而呼叫者沒寫任何 `unsafe` 就踩到了 UB。這破壞了 Rust 的承諾，是 unsafe 程式碼最嚴重的罪。**判準：你的 safe 函式內部有 unsafe，就要問「有沒有某個安全的呼叫方式能讓它 UB」——有就是 unsound，要嘛補不變量檢查、要嘛把 `unsafe` 標到簽章上（`unsafe fn`）把責任推回呼叫者。**

**`unsafe fn` 的契約。** 如果你的函式**沒法**在內部保證 sound（例如它就是需要呼叫者傳一個「保證不越界的 index」），那就標成 `unsafe fn`，並在文件裡寫清楚**呼叫者必須滿足什麼**（`# Safety` 段）。標準庫的 `get_unchecked`：

```rust
// 標準庫風格：unsafe fn + Safety 契約
/// # Safety
/// 呼叫者必須保證 `idx < slice.len()`，否則 UB。
unsafe fn get_unchecked_demo(s: &[i32], idx: usize) -> i32 {
    // 沒有 bounds check —— 快，但把「不越界」的責任推給呼叫者
    unsafe { *s.as_ptr().add(idx) }
}

fn main() {
    let v = [10, 20, 30];
    let x = unsafe { get_unchecked_demo(&v, 1) };  // 呼叫者簽字：我保證 1 < 3
    println!("{}", x);
}
```

真跑：`20`。

`unsafe fn` 的意思是「我把一部分不變量的責任推給你，呼叫我前你必須讀 `# Safety` 並滿足它」。呼叫端的 `unsafe { }` 就是簽字。這是 unsafe 契約在兩端的分工：**要嘛我（callee）內部保證 sound 給你 safe API，要嘛我標 `unsafe fn` 把契約寫清楚讓你（caller）簽字**。完整的手刻 sound 抽象（一個真的 `Vec`）在 Ch 21。

## 對比與取捨

| 面向 | C | safe Rust | unsafe Rust |
|---|---|---|---|
| 安全/不安全的線 | 無（整個語言都危險側） | 全在安全側 | 顯性圈出五種操作 |
| 解引用指標 | 隨時可（無標記） | 用參考，編譯器保證有效 | 裸指標解引用要 `unsafe` |
| 別名規則 | 無（除 `restrict`） | aliasing XOR mutability 強制 | 裸指標不受約束（同 C） |
| 出 bug 定位 | 藏在任何一行 | 不可能有記憶體 UB | 圈在 `unsafe` 那幾行 |
| 責任歸屬 | 永遠是你 | 編譯器 | `unsafe` 塊內是你，塊外是編譯器 |
| soundness 概念 | 無此標準 | — | 硬標準：safe API 必須 sound |

取捨要誠實：**unsafe 不是 C 的「完整能力」，是 C 能力的一小塊被顯性圈出來 + 附帶 soundness 責任。** 你不是「在 Rust 裡寫回 C」，你是在「明確標記的邊界內，接手編譯器驗不了的那幾件事，並且對它 sound 負全責」。用得好，你得到 C 的底層控制力 + Rust 的定位能力（bug 圈在 unsafe）；用得差，你有 C 的所有 UB + 一個讓你以為安全的假象。

## 踩雷集錦

1. **以為 `unsafe { }` 關掉借用檢查**：最普遍的誤解。`unsafe` **不關**任何檢查——借用、型別、所有權在 unsafe 塊裡照跑。它只解鎖五種特定操作（解引用裸指標、呼叫 unsafe fn、`static mut`、unsafe trait、union）。你在 unsafe 裡寫 `let a = &mut x; let b = &mut x;` 照樣編不過。正確認識：`unsafe` = 「解鎖五個操作 + 我簽字保證它們正確」，不是「回到 C」。

2. **以為「能跑出正確結果」就代表 unsafe 寫對了**：UB 的行為不可預測——你的懸空指標解引用今天印對的值，明天換優化等級/換機器就印垃圾或 crash（真跑：debug 印 32765、release 印 23744）。「跑起來對」不是 sound 的證據。要證明 sound 得靠推理（對所有輸入都安全）+ Miri 檢查，不是靠「我試了幾次都對」。

3. **建立裸指標和解引用搞混哪個要 unsafe**：**建立**裸指標（`&x as *const T`、`.as_ptr()`）是安全的，不碰記憶體；**解引用**（`*p`）才要 `unsafe`，因為那才真的讀寫記憶體。忘了這條會以為連造指標都要 unsafe（多此一舉）或以為解引用不用（編不過）。

4. **寫 unsafe fn 卻不寫 `# Safety` 契約**：`unsafe fn` 的意義是「把一部分不變量責任推給呼叫者」，那你就**必須**在文件寫清楚呼叫者要滿足什麼（`# Safety` 段）。不寫等於給了呼叫者一把沒有說明書的槍。反過來，一個內部有 unsafe 但能自己保證 sound 的函式，**不該**標 `unsafe fn`（那會逼呼叫者白簽字）。

5. **暴露 unsound 的 safe API**：內部有 unsafe 的 safe 函式，如果存在某個安全呼叫方式能觸發 UB（如上面拿掉 assert 的 `split`），它就 unsound——這是 unsafe 程式碼最嚴重的罪，因為它破壞了「safe Rust 不可能 UB」的全局承諾。每寫一個「內含 unsafe 的 safe 函式」，都要自問「有沒有安全的用法能讓它 UB」。

## 進階：再往深一層

**unsafe 的「最小化」原則。** `unsafe` 塊要盡量小——只圈住真正需要 superpower 的那幾行，前後的準備（如 `assert`、算 index）留在 safe 側。塊越小，你要人腦驗證 sound 的範圍越小，audit 越省力。一個包了 50 行的 `unsafe { }` 遠比五個各兩行的 `unsafe { }` 難 audit。標準庫的 unsafe 幾乎都是「三兩行 + 前面一個不變量檢查」。

**`// SAFETY:` 註解慣例。** 社群慣例：每個 `unsafe` 塊前面寫一行 `// SAFETY: ...` 說明「為什麼這裡是 sound 的」（滿足了什麼前提）。這不是裝飾——它是你對「我驗證過」的書面舉證，也是 reviewer 的檢查點。標準庫、Rust-for-Linux 都強制這個慣例。寫 unsafe 不寫 `// SAFETY:` 在正經專案裡過不了 review。

**面試常問**：「`unsafe` 到底關掉了什麼？」——標準答案：**什麼都沒關掉**。它只**解鎖**五種編譯器無法驗證安全性的操作，並把這五種操作的 soundness 責任轉給程式設計師；借用檢查、型別檢查、所有權在 unsafe 內照常運作。能一口氣糾正「unsafe = 關檢查」這個誤解、講清楚 soundness 是「對所有安全呼叫都不可能 UB」，代表你真的懂這條線，不是把它當 C 的後門。

## 動手練習

1. **五種 superpower 各觸發一次**：把本章五種操作各寫一個最小例子（裸指標解引用、呼叫自訂 `unsafe fn`、改 `static mut`、`unsafe impl` 一個自訂 unsafe trait、讀 union 欄位），全塞進一支程式跑過。再把其中一個的 `unsafe` 拿掉，看編譯器怎麼罵（照抄它的 E0133/相關錯誤）。

2. **造 UB 給 Miri 抓（連 Ch 20）**：把本章的懸空指標例子存成 `main.rs`，先 `rustc` 跑幾次看垃圾值（可能每次不同），再（若裝了 nightly + Miri）`cargo +nightly miri run` 看 Miri 精準報 use-after-free。體會「肉眼/普通執行看不出、Miri 抓得到」的差距。

3. **修 unsound 成 sound**：寫一個 safe 函式 `fn nth<T: Copy>(s: &[T], i: usize) -> T`，內部用 `*s.as_ptr().add(i)`（unsafe）。先**不**加 bounds check，想清楚「什麼安全呼叫能讓它 UB」（`nth(&v, 999)`），再加 `assert!(i < s.len())` 讓它 sound。體會「補一個不變量檢查」怎麼把 unsound 變 sound。

## 本章重點整理

- **`unsafe` 不關掉任何檢查**：借用/型別/所有權在 unsafe 塊裡照跑，它只**解鎖五種操作**（解引用裸指標、呼叫 unsafe fn、存取/改 `static mut`、實作 unsafe trait、存取 union），並把這五種的安全性責任轉給你。破除「unsafe = 回到 C」的誤解。
- **裸指標 `*const T`/`*mut T` = C 的指標**：可 null、可別名、可算術、不受借用規則約束；**建立安全、解引用要 `unsafe`**（真跑 E0133，note 列出 null/dangling/unaligned/race 四大 UB 來源）。
- **soundness = 對所有安全呼叫都不可能 UB**：這是 C 沒有的硬標準。safe API 內含 unsafe 就必須 sound，否則破壞「safe Rust 不可能 UB」的全局承諾。UB 不可預測（真跑：懸空指標 debug/release 印不同垃圾），下一章 Miri 精準抓（use-after-free）。
- **正確用法 = 把 unsafe 包在安全 API 裡、維持不變量**：`split` 先 `assert` 不變量再 unsafe → sound；沒法內部保證就標 `unsafe fn` + 寫 `# Safety` 契約把責任推給呼叫者。`// SAFETY:` 註解 + 最小化 unsafe 塊是社群鐵律。
- **對照 C**：C 沒有這條線，整個語言都在危險側、沒有 soundness 標準；Rust 把危險圈進 `unsafe` 那幾行，audit 只需盯那幾行——這是 Rust 相對 C 的定位優勢。

## 自我檢核

- [ ] 面試問「`unsafe` 關掉了什麼」，能答「什麼都沒關，只解鎖五種操作 + 轉移 soundness 責任」，並列出那五種。
- [ ] 不看筆記，能解釋「建立裸指標」和「解引用裸指標」哪個要 `unsafe`、為什麼。
- [ ] 能用自己的話定義 soundness，並判斷一個「內含 unsafe 的 safe 函式」是 sound 還是 unsound。
- [ ] 知道 UB 為什麼「跑起來對」不代表寫對了（不可預測、隨優化/平台變臉），以及該用 Miri 驗證。
- [ ] 能說出 unsafe 的兩種正確分工：內部保證 sound 給 safe API，vs 標 `unsafe fn` + `# Safety` 契約推給呼叫者。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件

- **《The Rustonomicon》「Meet Safe and Unsafe」與「What Unsafe Rust Can Do」** — （[doc.rust-lang.org/nomicon/meet-safe-and-unsafe.html](https://doc.rust-lang.org/nomicon/meet-safe-and-unsafe.html)）
  - **讀哪裡**：「Meet Safe and Unsafe」建立 safe/unsafe 分工的心智模型、「What Unsafe Rust Can Do」列出本章那五種 superpower 的權威版。這是 unsafe Rust 的官方聖經。
  - **學到什麼**：本章五種 superpower、soundness 概念的權威來源；Nomicon 對「safe 程式碼不可能 UB」這個承諾為什麼重要講得比本章深。
  - **前提**：懂本章五種操作 + soundness；Nomicon 假設你會 safe Rust，是進 unsafe 的正門。

- **《The Rust Reference》「Behavior considered undefined」** — （[doc.rust-lang.org/reference/behavior-considered-undefined.html](https://doc.rust-lang.org/reference/behavior-considered-undefined.html)）
  - **讀哪裡**：整頁的 UB 清單——解引用懸空/未對齊指標、違反別名、data race、產生非法值等。這是 Rust 官方「什麼算 UB」的定義性清單。
  - **學到什麼**：本章那個「懸空指標解引用是 UB」只是清單一條；這頁讓你知道完整的 UB 有哪些，寫 unsafe 時對照自查。
  - **前提**：懂本章 UB 概念；這是條文，遇到「這樣算不算 UB」的疑問時來這裡查。

### 官方文件（The Book）

- **《The Rust Programming Language》(The Book) Ch 20「Unsafe Rust」**（章號依版本，約在 Advanced Features）— （[doc.rust-lang.org/book/ch20-01-unsafe-rust.html](https://doc.rust-lang.org/book/ch20-01-unsafe-rust.html)）
  - **讀哪裡**：整節，尤其「Five Actions Only Available in Unsafe」——The Book 用的正是「五種 superpower」這個框架（本章沿用），還有「Creating a Safe Abstraction over Unsafe Code」對應本章 `split` 那節。
  - **學到什麼**：本章的官方入門對應，節奏較慢，適合當本章的補課或第二遍。
  - **前提**：懂前面章節的參考與所有權；The Book 對 unsafe 的介紹比 Nomicon 溫和。

### 技術文章

- **「The Unsafe Rust Guidelines / Unsafe Code Guidelines Reference（UCG）」** — Rust UCG Working Group（[rust-lang.github.io/unsafe-code-guidelines/](https://rust-lang.github.io/unsafe-code-guidelines/)）
  - **這篇說什麼**：Rust 官方工作組正在制定「unsafe 程式碼到底能依賴什麼、不能依賴什麼」的精確規則——例如記憶體佈局保證、別名模型（Stacked/Tree Borrows，Ch 20 正題）。是本章 soundness 概念的形式化前沿。
  - **讀哪裡**：先看「Introduction」建立這份文件在解決什麼問題；別名模型的部分留到 Ch 20 讀。
  - **為什麼值得讀**：本章講 soundness 是「不可能 UB」，但「什麼精確算 UB」在 unsafe 邊界有很多灰區，UCG 就是在釘死這些灰區；想寫生產級 unsafe（Ch 21、kernel）遲早要碰。
  - **前提**：懂本章 UB + soundness；這是深水區，選讀。

搞懂了 unsafe 的五種 superpower、裸指標、soundness 這條線，下一章我們深入 unsafe 的重武器——`transmute`（把任意型別的 bit 重新詮釋成另一型別，比 union 更暴力）、`MaybeUninit`（安全地處理未初始化記憶體）、`union` 的進階用法。這些是手刻高效能抽象和 FFI 的必備，但每一個都是 UB 的高發區，配 Miri 用。

→ [Ch 18 unsafe 進階：transmute/MaybeUninit/union](./18-unsafe-advanced.md)
