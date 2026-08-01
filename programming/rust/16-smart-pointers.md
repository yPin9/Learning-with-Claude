# Ch 16 — 智慧指標底層：Box/Rc/Arc/RefCell/Cell

> **目標**：把 Rust 標準庫四大智慧指標挖到底層——`Box<T>`（唯一擁有的 heap 指標，對照 C++ `unique_ptr`，真跑證明就是一個裸指標大小 + heap alloc）、`Rc<T>`/`Arc<T>`（引用計數，對照 `shared_ptr`，畫出 heap 上 RcBox 的佈局、用 `strong_count` 觀察增減、用 `Weak` 修好一個真的會洩漏的循環引用）、`Cell`/`RefCell`（內部可變性，執行期借用檢查，真跑 double borrow 的 panic）。理解為什麼 `Rc` 不是 `Send`（預告 Ch 23）、`UnsafeCell` 是這一切的底層原語（預告 Ch 20/21），以及 `Rc<RefCell<T>>` 這個常見組合的代價。

> **環境**：Rust 以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）。所有 `size_of`、`{:p}` 位址、`strong_count`/`weak_count`、drop 順序輸出、RefCell panic 訊息，都是本機真跑，非推測。

## 為什麼需要這個？

C++ 給了你 `unique_ptr` 和 `shared_ptr`，你已經知道「智慧指標 = 一個管理生命週期的 RAII wrapper」。Rust 的 `Box`/`Rc`/`Arc` 是同一族概念，但有兩個關鍵差異值得你重新學一遍。

**第一，Rust 的智慧指標是 ownership 系統的一部分，不是可選的紀律。** C++ 裡你**可以**用 `shared_ptr`，但沒人逼你——一個 `T*` 裸指標照樣能亂傳、double free、UAF。Rust 裡，一旦你要把資料放 heap、或要多個擁有者，你**必須**經過這些智慧指標，而它們的規則（`Box` 唯一擁有、`Rc` 計數、`RefCell` 執行期借用檢查）由編譯器強制。

**第二，Rust 有一個 C++ 沒有正面對應物的東西：內部可變性（interior mutability）。** Rust 的核心鐵律是「aliasing XOR mutability」——同一時間，要嘛多個 `&`（共享，唯讀），要嘛一個 `&mut`（獨佔，可寫），不能又共享又可寫。這條規則在編譯期擋掉一大類 bug（iterator invalidation、data race）。但有些正當需求會撞牆：一個 `Rc<T>` 有多個擁有者（共享），你卻想改裡面的東西（可變）——編譯期的借用檢查會拒絕。`Cell`/`RefCell` 就是「合法地繞過編譯期檢查、把檢查搬到執行期（或省掉）」的工具，而它們底下都是同一個原語 `UnsafeCell`——編譯器唯一承認的「這塊記憶體可以透過 `&` 被改」的例外。

這章把這四個智慧指標的**內部佈局**和**執行期行為**都挖出來，讓你不只會用，還能在 debug 時知道記憶體裡發生了什麼、在 audit 時知道 `Rc<RefCell<T>>` 的代價在哪。

## 先建立直覺

三張心智圖像，對應三組工具。

**`Box<T>`：把資料搬到 heap 的最薄 wrapper。** 想像 `Box` 就是 C 的 `malloc` + 自動 `free`——它在 heap 配一塊放 `T`，自己在 stack 上只留一個指標，離開 scope 自動釋放。它是**唯一擁有者**，不能複製（複製會有兩個擁有者搶著 free），只能 move。這就是 `unique_ptr`。

**`Rc<T>`：帶計數的共享擁有。** 想像一份文件被幾個人共用，每個人手上有一把鑰匙，牆上有一個計數牌記「現在幾把鑰匙」。拿一把新鑰匙（`clone`）計數 +1，還鑰匙（drop）計數 -1，計數歸零時文件才被銷毀。這個計數牌和文件都在 heap 上，鑰匙（`Rc` 本身）在 stack 上只是個指標。這就是 `shared_ptr`。

```
   stack                    heap (一塊 RcBox)
   ┌──────┐               ┌───────────────────────────┐
   │ rc_a │──────────────▶│ strong: 3   ← 幾個 Rc      │
   ├──────┤          ┌───▶│ weak:   0   ← 幾個 Weak    │
   │ rc_b │──────────┤    │ data:  T    ← 真正的資料    │
   ├──────┤          │    └───────────────────────────┘
   │ rc_c │──────────┘
   └──────┘   三個 Rc 指向同一個 RcBox，strong=3
```

**`RefCell<T>`：把借用檢查從編譯期搬到執行期。** 想像一個借書櫃檯，牆上一個借閱牌：可以同時借出很多「唯讀副本」，或借出一本「可寫的正本」，但兩者不能並存。你來借的時候櫃檯查牌，違規就當場翻臉（panic），而不是像編譯器那樣事前擋你。代價：多一次執行期檢查、錯誤從編譯期延到執行期。

> 如果你對「aliasing XOR mutability」這條規則還沒內化，回看 [Ch 3 — Borrowing](./03-borrowing-references.md) 的 `&`/`&mut` 別名規則。`Cell`/`RefCell` 整章都在講「怎麼在保持安全的前提下有限度地違反它」。

## Box：唯一擁有的 heap 指標

`Box<T>` 是最簡單的智慧指標：heap 配一塊放 `T`，自己就是一個裸指標大小。真跑證明：

```rust
fn main() {
    let b = Box::new(42i32);
    println!("Box<i32> size = {}   (就是一個裸指標大小)", std::mem::size_of::<Box<i32>>());
    println!("*const i32 size = {}", std::mem::size_of::<*const i32>());
    // Box 指向的資料在 heap，位址遠離 stack 變數
    let x_on_stack = 7i32;
    println!("stack var  addr = {:p}", &x_on_stack);
    println!("box target addr = {:p}   (heap)", &*b);
    println!("box 內容 = {}", *b);
}
```

真跑：

```
Box<i32> size = 8   (就是一個裸指標大小)
*const i32 size = 8
stack var  addr = 0x7fff86b92414
box target addr = 0x581c0e62ad60   (heap)
box 內容 = 42
```

`Box<i32>` 是 8 bytes——跟裸指標一樣，沒有任何額外欄位。stack 變數位址 `0x7fff...`（stack 高位），box 指向的資料位址 `0x5581...`（heap 低位，離 stack 十萬八千里），證明 `Box::new` 真的做了 heap 配置。`*b` 解引用拿到裡面的 `42`。

跟 C++ 對照：`std::unique_ptr<int> p = std::make_unique<int>(42);` 做的事一模一樣——heap 配一個 int、`p` 是一個指標、離開 scope 自動 `delete`。差別在**Rust 的 `Box` 不能複製**（`Copy` 沒實作，`Clone` 要顯式呼叫且會另外 heap 配一塊），這對應 `unique_ptr` 的 move-only 語意，但 Rust 是編譯器強制的：

```rust
fn main() {
    let a = Box::new(String::from("x"));
    let b = a;                    // move，a 失效
    // println!("{}", a);         // 取消註解會編譯錯誤：value moved
    println!("{}", b);
}
```

`let b = a` 是 move（把 ownership 轉給 `b`），`a` 之後就不能用了——編譯器保證只有一個擁有者，離開 scope 只 free 一次，沒有 C++ 那種「不小心 copy 了 `unique_ptr`」的編譯錯誤要靠你記住 `std::move`，Rust 預設就是 move。

`Box` 的另一個關鍵用途：**遞迴型別**。`struct Node { next: Node }` 大小無限大編不過，`struct Node { next: Option<Box<Node>> }` 就好了——`Box` 把遞迴部分放 heap，struct 本身只存一個指標（固定大小）。而且 `Option<Box<Node>>` 因為 niche optimization（Ch 15）跟 `Box<Node>` 一樣 8 bytes，`None` 就是 null——這正是 C 的 `struct Node { struct Node *next; }` 用 NULL 結尾的鏈結串列，佈局零差別。

## Rc：引用計數與 RcBox 佈局

`Rc<T>`（Reference Counted）給你**多個擁有者共享同一份資料**。`Rc::clone` 不複製資料，只把計數 +1、回傳另一個指向同一 RcBox 的指標。真跑觀察計數增減：

```rust
use std::rc::Rc;

fn main() {
    let a = Rc::new(String::from("shared"));
    println!("建立 a       strong={}", Rc::strong_count(&a));
    let b = Rc::clone(&a);
    println!("clone 出 b   strong={}", Rc::strong_count(&a));
    {
        let c = Rc::clone(&a);
        println!("clone 出 c   strong={}", Rc::strong_count(&a));
        println!("a/b/c 指向同一 heap: {:p} {:p} {:p}", &*a, &*b, &*c);
    }
    println!("c drop 後    strong={}", Rc::strong_count(&a));
}
```

真跑：

```
建立 a       strong=1
clone 出 b   strong=2
clone 出 c   strong=3
a/b/c 指向同一 heap: 0x557bf7b72af0 0x557bf7b72af0 0x557bf7b72af0
c drop 後    strong=2
```

三個 `Rc` 的 `&*` 位址**完全相同**（`0x557b...`）——它們指向同一塊 heap 資料，`clone` 沒有複製 String。計數從 1 漲到 3，`c` 離開內層 scope 自動 drop、計數掉回 2。當計數歸零，RcBox 連同裡面的 `String` 才被釋放。

**RcBox 的內部佈局。** `Rc<T>` 在 stack 上是一個指標，指向 heap 上一塊叫 `RcBox`（標準庫內部名稱）的結構，它有三部分：

```
   heap 上的 RcBox<T>：
   ┌─────────────────────────────────────┐
   │ strong: Cell<usize>   ← 強引用計數    │  offset 0
   │ weak:   Cell<usize>   ← 弱引用計數    │  offset 8
   │ value:  T             ← 你的資料      │  offset 16
   └─────────────────────────────────────┘
        Rc<T>（stack）指向這裡的起點
```

`Rc::clone(&a)` 做的事：把 `strong` 那個 `usize` 從 3 加到 4，回傳一個新的 `Rc` 指標指向同一個 RcBox。drop 一個 `Rc`：`strong` -1，若歸零則 drop `value` 並（等 `weak` 也歸零時）釋放整塊。用 `Cell<usize>` 存計數的原因下面會講——`Rc` 共享（多個 `&`）卻要能改計數，這正是內部可變性的場景。

跟 C++ 的 `shared_ptr` 對照：`shared_ptr` 也是「stack 上一個指標（實際是兩個：一個指資料、一個指控制塊）+ heap 上一個控制塊存 strong/weak 計數」。概念完全同源。一個差異：`Rc` 的計數是**非原子**的（普通 `usize` 加減），`shared_ptr` 的計數是**原子**的（可跨 thread）。這帶來下面的 `Rc` vs `Arc`。

## Rc vs Arc：非原子 vs 原子，以及為什麼 Rc 不是 Send

`Rc` 的計數用普通整數加減，快，但**不是 thread-safe**——兩條 thread 同時 `clone`/drop 同一個 `Rc`，計數的 `+1`/`-1` 會 race（讀-改-寫不是原子），計數算錯 → double free 或洩漏。`Arc`（Atomically Reference Counted）把計數換成原子操作，可跨 thread，代價是每次 clone/drop 都有原子指令的開銷。

Rust 不是靠「你要記得多 thread 用 `Arc`」——它用型別系統**編譯期擋住**。`Rc` 沒有實作 `Send`（不能搬到別的 thread），所以你想把 `Rc` 送進 `thread::spawn` 會直接編不過：

```rust
use std::rc::Rc;
use std::thread;
fn main() {
    let a = Rc::new(5);
    let b = Rc::clone(&a);
    thread::spawn(move || {       // 想把 Rc 搬進另一條 thread
        println!("{}", b);
    });
}
```

真跑（編不過，照抄關鍵段）：

```
error[E0277]: `Rc<i32>` cannot be sent between threads safely
 --> b9.rs:6:19
  |
6 |       thread::spawn(move || {       // 想把 Rc 搬進另一條 thread
  |       ------------- ^------
  ...
  = help: within `{closure@...}`, the trait `Send` is not implemented for `Rc<i32>`
```

`Rc<i32> cannot be sent between threads safely`——`the trait Send is not implemented for Rc<i32>`。編譯器在你寫錯的當下就攔下，不是等執行期 race 出隨機 crash。把 `Rc` 換成 `Arc` 就編得過。這是 Rust「fearless concurrency」的核心機制之一，`Send`/`Sync` 的完整故事在 Ch 23，這裡先記住：**`Rc` = 單 thread、快；`Arc` = 跨 thread、有原子開銷；選錯編譯器會罵你，不會讓你 race。**

實務判準：預設用 `Rc`（大多資料結構在單 thread 內共享），只有真的要跨 thread 才升級 `Arc`——別無腦全用 `Arc`，那是白付原子開銷。

## Weak：打破循環引用

`Rc` 有 `shared_ptr` 同樣的老問題：**循環引用會洩漏**。A 持有 B 的 `Rc`、B 持有 A 的 `Rc`，兩邊 strong 計數永遠 ≥ 1，誰也降不到 0，記憶體永遠不釋放。先真跑一個洩漏——用 `Drop` 當觀察器（drop 時印字），看它**不會**印：

```rust
use std::rc::Rc;
use std::cell::RefCell;

struct Node {
    name: String,
    next: RefCell<Option<Rc<Node>>>,
}
impl Drop for Node {
    fn drop(&mut self) { println!("  drop {}", self.name); }
}

fn main() {
    let a = Rc::new(Node { name: "A".into(), next: RefCell::new(None) });
    let b = Rc::new(Node { name: "B".into(), next: RefCell::new(None) });
    *a.next.borrow_mut() = Some(Rc::clone(&b)); // A -> B
    *b.next.borrow_mut() = Some(Rc::clone(&a)); // B -> A  形成環
    println!("A strong={}  B strong={}", Rc::strong_count(&a), Rc::strong_count(&b));
    println!("離開 scope（下面應該看不到 drop）...");
}
```

真跑：

```
=== Rc 循環：兩個節點互指 ===
A strong=2  B strong=2
離開 scope（下面應該看不到 drop）...
=== 上面沒有 drop 輸出 = 洩漏 ===
```

`A strong=2 B strong=2`（各自被 main 的變數 + 對方各持一個），離開 scope 後 main 的變數 drop 掉，計數降到 1，但**互相持有的那一份讓計數卡在 1，永遠降不到 0**——所以兩個 `Node` 的 `drop` 從沒被呼叫。這就是洩漏，`Rc` 不會幫你偵測循環（`shared_ptr` 也不會）。

**修法：其中一個方向改用 `Weak`。** `Weak<T>` 是「不增加 strong 計數」的弱引用——它指向 RcBox 但不擁有資料，不會阻止資料被 drop。要存取時用 `upgrade()` 試著拿回一個 `Rc`（若資料還活著回 `Some`，已被 drop 回 `None`）。典型場景：雙向鏈結串列/樹，「往下/往前」用 strong `Rc`、「往回/往上」用 `Weak`：

```rust
use std::rc::{Rc, Weak};
use std::cell::RefCell;

struct Node {
    name: String,
    next: RefCell<Option<Rc<Node>>>,   // 強引用往前
    prev: RefCell<Weak<Node>>,         // 弱引用往回，不算 strong
}
impl Drop for Node {
    fn drop(&mut self) { println!("  drop {}", self.name); }
}

fn main() {
    let a = Rc::new(Node { name: "A".into(), next: RefCell::new(None), prev: RefCell::new(Weak::new()) });
    let b = Rc::new(Node { name: "B".into(), next: RefCell::new(None), prev: RefCell::new(Weak::new()) });
    *a.next.borrow_mut() = Some(Rc::clone(&b));       // A --strong--> B
    *b.prev.borrow_mut() = Rc::downgrade(&a);         // B --weak--> A
    println!("A strong={} weak={}", Rc::strong_count(&a), Rc::weak_count(&a));
    println!("B strong={} weak={}", Rc::strong_count(&b), Rc::weak_count(&b));
    if let Some(p) = b.prev.borrow().upgrade() {      // 從 weak 拿回 strong
        println!("B 從 weak upgrade 回 A: {}", p.name);
    }
    println!("離開 scope：");
}
```

真跑：

```
A strong=1 weak=1
B strong=2 weak=0
B 從 weak upgrade 回 A: A
離開 scope：
  drop A
  drop B
```

看 `A strong=1 weak=1`——A 被 main 持有一個 strong，B 對 A 的是 weak（不算 strong）。這次**兩個 drop 都印出來了**：離開 scope 後 A 的 strong 降到 0，A 被 drop；A 一 drop，它持有的 `next`（對 B 的 strong）也 drop，B 的 strong 降到 0，B 也被 drop。循環被 `Weak` 打斷了。`upgrade()` 那行示範了怎麼從 weak 安全地拿回資料——它先檢查資料還在不在，這是 `Weak` 相對於裸指標的安全保證。

回到 RcBox 佈局：weak 計數就是為此存在的。`weak` 那個欄位記「有幾個 `Weak` 指著」；strong 歸零時 drop `value`，但要等 weak 也歸零才釋放整塊 RcBox（否則 `Weak::upgrade` 會存取已釋放記憶體）。這是為什麼 RcBox 要同時存兩個計數。

## Cell 與 RefCell：內部可變性

現在正面處理「共享卻想改」這件事。上面 `Node` 的 `next: RefCell<Option<Rc<Node>>>` 已經偷用了 `RefCell`——因為 `Rc<Node>` 是共享的（多個 `&`），你不能直接對它 `&mut` 去改 `next`（借用規則擋你）。`RefCell` 讓你在共享的資料上「合法地改」。

Rust 提供兩個內部可變性工具，適用不同情況：

**`Cell<T>`：整體 get/set/replace，無借用追蹤。** 適合 `Copy` 的小型別。它不給你「裡面那個 `T` 的參考」，只讓你**整份換掉**——所以永遠不會有「借出去又被改」的問題，不需要執行期檢查：

```rust
use std::cell::Cell;

fn main() {
    let c = Cell::new(10);
    c.set(20);
    println!("Cell get = {}", c.get());
    let old = c.replace(30);
    println!("replace old={} new={}", old, c.get());
}
```

真跑：

```
Cell get = 20
replace old=20 new=30
```

`Cell` 只有 `get`（拿一份 copy）、`set`（整份換）、`replace`（換並回傳舊值）這種整體操作，沒有「借出內部參考」。這就是為什麼它零執行期成本、也零 panic 風險——你根本借不出去，也就沒有 aliasing 問題。上面 RcBox 的計數用 `Cell<usize>` 正是這個原因：計數就是個 `Copy` 的整數，整體讀寫，`Cell` 完美適用。

**`RefCell<T>`：借出內部參考，執行期檢查借用規則。** 當你需要「借出 `&T` 或 `&mut T` 來操作內部」（例如 `RefCell<Vec<T>>` 要 `push`），`Cell` 的整體換就不夠了。`RefCell` 允許借出參考，但在**執行期**追蹤「現在借出了幾個唯讀、有沒有借出可寫」，違規就 panic：

```rust
use std::cell::RefCell;

fn main() {
    let rc = RefCell::new(vec![1, 2, 3]);
    {
        let mut m = rc.borrow_mut();   // 借出可寫
        m.push(4);
    } // borrow_mut 在此釋放
    println!("RefCell = {:?}", rc.borrow());
    let r1 = rc.borrow();              // 多個唯讀借用 OK
    let r2 = rc.borrow();
    println!("同時 r1.len={} r2.len={}", r1.len(), r2.len());
}
```

真跑：

```
RefCell = [1, 2, 3, 4]
同時 r1.len=4 r2.len=4
```

`borrow_mut()` 借出一個可寫的 `RefMut`，用完（離開內層 scope）自動歸還；之後 `borrow()` 借出多個唯讀的 `Ref` 可以並存。這跟編譯期借用規則一樣（多個 `&` 或一個 `&mut`），只是檢查搬到執行期。

**違規就 panic——這是 `RefCell` 的靈魂，真跑給你看。** 同時借出兩個可寫：

```rust
use std::cell::RefCell;

fn main() {
    let rc = RefCell::new(5);
    let _a = rc.borrow_mut();        // 第一個可變借用還活著
    let _b = rc.borrow_mut();        // 第二個 -> 執行期 panic
    println!("走不到這裡");
}
```

真跑（panic，exit code 101）：

```
thread 'main' (246703) panicked at b6.rs:6:17:
RefCell already borrowed
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

`RefCell already borrowed`——第二個 `borrow_mut` 在執行期發現「已經有一個借用了」，直接 panic。另一種違規（已有可寫借用時再借唯讀）訊息略不同：

```rust
use std::cell::RefCell;
fn main() {
    let rc = RefCell::new(5);
    let _a = rc.borrow_mut();
    let _b = rc.borrow();   // 可變借用還在時再取不可變 -> panic
}
```

真跑：

```
thread 'main' (246793) panicked at b6b.rs:5:17:
RefCell already mutably borrowed
```

`RefCell already mutably borrowed`。兩種訊息對應兩種違規方向：`already borrowed`（已有可寫、再借可寫）vs `already mutably borrowed`（已有可寫、再借唯讀），看到哪個就知道是哪種借用撞了。不想 panic 想優雅處理的話，用 `try_borrow()`/`try_borrow_mut()`，它回 `Result` 而非 panic：

```rust
use std::cell::RefCell;
fn main() {
    let rc = RefCell::new(5);
    let a = rc.borrow_mut();
    let res = rc.try_borrow_mut();          // 回 Result，不 panic
    match res {
        Ok(_) => println!("拿到"),
        Err(e) => println!("try_borrow_mut 失敗: {}", e),
    }
    drop(a);
}
```

真跑：`try_borrow_mut 失敗: already borrowed`。

**`RefCell` 的空間代價**：它比裸資料多一個借用計數欄位（一個 `isize` 記「現在借出狀態」）：

```rust
use std::cell::{Cell, RefCell};
use std::mem::size_of;
fn main() {
    println!("i32            = {}", size_of::<i32>());
    println!("Cell<i32>      = {}   (無額外欄位)", size_of::<Cell<i32>>());
    println!("RefCell<i32>   = {}   (多一個 borrow flag: isize)", size_of::<RefCell<i32>>());
}
```

真跑：

```
i32            = 4
Cell<i32>      = 4   (無額外欄位)
RefCell<i32>   = 16   (多一個 borrow flag: isize)
```

`Cell<i32>` 跟 `i32` 一樣 4 bytes（零開銷），`RefCell<i32>` 是 16（i32 對齊到 8 + `isize` 的借用旗標 8）。這就是 `RefCell` 執行期檢查的空間成本：多一個計數欄位，加上每次 borrow 的執行期讀寫檢查。

## 底層原語：UnsafeCell

`Cell`、`RefCell`、`Mutex`、`Atomic*`——所有內部可變性型別，底下都是同一個東西：`UnsafeCell<T>`。它是編譯器**唯一承認**的「可以透過 `&`（共享參考）改裡面的 `T`」的型別。一般型別的 `&T` 保證那塊記憶體不會被改（編譯器可據此做優化，如把值快取進暫存器）；`UnsafeCell<T>` 是這條保證的**唯一合法例外**——它告訴優化器「這塊記憶體可能透過共享參考被改，別亂假設」。

`Cell`/`RefCell` 就是在 `UnsafeCell` 外面包一層安全 API：`Cell` 用「只能整體換」保證安全，`RefCell` 用「執行期借用計數」保證安全。它們把裸 `UnsafeCell` 的 unsafe 收在內部，對外給你一個不會出錯的介面——這正是 Ch 17 之後「把 unsafe 包在安全 API 裡」的範本。你手刻資料結構要用內部可變性時（Ch 21），最終也是繞到 `UnsafeCell`。這裡先建立這個地圖：**內部可變性一切的根 = `UnsafeCell`**，完整故事在 Ch 20（記憶體模型）和 Ch 21（手刻抽象）。

## 組合：Rc<RefCell<T>>

`Rc` 給共享擁有但**只讀**（多個 `Rc` 只能拿到 `&T`，不能改），`RefCell` 給內部可變但**單一擁有**。兩個一疊——`Rc<RefCell<T>>`——就是「多個擁有者、且都能改」，這是 Rust 裡表達「一份可變狀態被多處共享」（圖、觀察者模式、樹的父子互指）的標準組合：

```rust
use std::rc::Rc;
use std::cell::RefCell;

fn main() {
    let shared = Rc::new(RefCell::new(vec![1, 2, 3]));
    let owner2 = Rc::clone(&shared);
    let owner3 = Rc::clone(&shared);

    owner2.borrow_mut().push(4);   // 從 owner2 改
    owner3.borrow_mut().push(5);   // 從 owner3 改
    println!("strong_count = {}", Rc::strong_count(&shared));
    println!("內容 = {:?}", shared.borrow());
}
```

真跑：

```
strong_count = 3
內容 = [1, 2, 3, 4, 5]
```

三個 `Rc` 共享同一個 `RefCell<Vec>`，任何一個都能 `borrow_mut().push(...)` 改內容。`Rc` 負責「多擁有者 + 計數」，`RefCell` 負責「共享下仍能改 + 執行期借用檢查」。

**這個組合的代價要誠實講**：(1) 每次 `borrow`/`borrow_mut` 有執行期檢查開銷；(2) 借用違規從編譯期錯誤變成**執行期 panic**——你把 borrow checker 的保證從「編不過」降級成「跑起來炸」，測試沒覆蓋到的路徑可能上線才 panic；(3) 只能單 thread（`Rc` 不是 `Send`），跨 thread 要換成 `Arc<Mutex<T>>`（Ch 24），代價更大。所以 `Rc<RefCell<T>>` 是「當 ownership 圖真的無法用純借用表達時」的工具，不是預設選擇——能用 `&mut` 或重構資料結構避開，就別疊這兩層。

## 對比與取捨

| 型別 | 擁有語意 | 可變性 | thread | C++ 對應 | 主要代價 |
|---|---|---|---|---|---|
| `Box<T>` | 唯一擁有 | 透過 `&mut` | 隨 `T` | `unique_ptr` | 一次 heap alloc |
| `Rc<T>` | 多擁有者（計數） | 唯讀（`&T`） | 單 thread（!Send） | `shared_ptr`（非原子） | 計數 + 循環洩漏風險 |
| `Arc<T>` | 多擁有者（原子計數） | 唯讀 | 跨 thread | `shared_ptr`（原子） | 原子指令開銷 |
| `Cell<T>` | — | 整體 get/set | 單 thread | 無正面對應 | 只能 Copy 型別整體換 |
| `RefCell<T>` | — | 借出參考 | 單 thread | 無正面對應 | 執行期檢查 + panic 風險 |
| `Rc<RefCell<T>>` | 多擁有者 + 可變 | 借出參考 | 單 thread | 手工 `shared_ptr`+mutable | 上面兩者代價疊加 |

選擇順序（實務心法）：能只用 `&`/`&mut`（純借用）就別上智慧指標；要放 heap / 遞迴型別用 `Box`；要多擁有者用 `Rc`（跨 thread 才 `Arc`）；共享下要改才上 `RefCell`（`Copy` 小值用 `Cell` 更輕）；跨 thread 共享可變是 `Arc<Mutex<T>>`（Ch 24）。層數越多，執行期成本和出錯面越大。

## 踩雷集錦

1. **`Rc::clone` 以為在深拷貝資料**：`Rc::clone(&a)` 只把計數 +1、複製一個指標，**不複製底層資料**（真跑：三個 `Rc` 的 `&*` 位址相同）。這跟一般型別的 `.clone()`（深拷貝）語意不同。社群慣例寫 `Rc::clone(&a)` 而非 `a.clone()`，就是為了讓讀者一眼看出「這是廉價的計數 +1，不是深拷貝」。

2. **循環引用洩漏、以為 `Rc` 會自動處理**：`Rc`（和 C++ `shared_ptr`）**不做循環偵測**，A↔B 互持會永久洩漏（真跑：drop 不觸發）。凡是可能形成環的結構（雙向鏈結、父子互指、觀察者），「往回」的那個方向用 `Weak`。這是 audit `Rc`-heavy 程式碼時的第一個檢查點。

3. **`RefCell` 借用違規是執行期 panic，不是編譯錯誤**：`RefCell` 把借用檢查搬到執行期，違規（如同時兩個 `borrow_mut`）不是編不過，是**跑起來 panic**（`RefCell already borrowed`）。你等於把 borrow checker 的保證降級成「測試沒跑到就不知道」。用 `RefCell` 的地方，借用範圍要盡量短、盡快歸還，減少撞車面。

4. **`Rc` 塞進 thread 想省事**：`Rc` 不是 `Send`，送進 `thread::spawn` 直接編不過（真跑那個 E0277）。別想著「加個 `unsafe` 繞過」——非原子計數在多 thread 會 race 出 double free。跨 thread 就老實用 `Arc`。

5. **無腦全用 `Arc`/`Rc<RefCell>`**：看到共享就 `Arc<Mutex<T>>`、看到要改就 `Rc<RefCell<T>>`，是新手把 GC 語言習慣搬過來的常見過度設計。多數情況下純借用（`&`/`&mut`）或 `Box` 就夠，且零執行期成本。智慧指標的每一層都有代價，疊之前先問「能不能用更簡單的表達」。

## 進階：再往深一層

**`Rc::clone` 的計數操作為什麼用 `Cell`？** RcBox 裡的 `strong`/`weak` 是 `Cell<usize>`——因為 `Rc::clone(&self)` 只有 `&self`（共享參考），要改計數就需要內部可變性。這是「內部可變性」在標準庫最核心的自用場景：`Rc` 本身就是靠 `Cell` 實作的。理解這點，`Cell`/`RefCell` 從「奇怪的工具」變成「共享下要改，就必然需要它」。

**`Rc::get_mut` 與 `Rc::make_mut`（copy-on-write）**：當 strong 計數為 1（獨佔）時，`Rc::get_mut` 能安全地給你 `&mut T`（沒別人共享，改它安全）。`Rc::make_mut` 更進一步：計數 > 1 時 clone 出一份獨佔的再給 `&mut`（copy-on-write）。這讓 `Rc` 在「多數時候唯讀、偶爾要改」的場景能避免 `RefCell` 的執行期開銷。

**面試常問**：「`Rc<RefCell<T>>` 和 `Arc<Mutex<T>>` 差在哪，何時用哪個？」——`Rc<RefCell<T>>` 是單 thread（`Rc` !Send、`RefCell` !Sync），借用檢查在執行期、違規 panic；`Arc<Mutex<T>>` 是跨 thread，用鎖序列化存取、違規是死鎖或阻塞而非 panic。單 thread 共享可變用前者（輕），跨 thread 用後者（Ch 24）。能一口氣講清「兩層各自負責什麼、代價在哪」代表你懂這套組合，不是背 pattern。

## 動手練習

1. **追計數**：把 Rc 那段擴充——建 `a`，clone 出 `b`、`c`，在不同 scope 各自 drop，每一步印 `Rc::strong_count`。手動預測每一行的計數，再跑驗證。加一個 `Rc::downgrade` 造一個 `Weak`，印 `weak_count`，觀察 weak 不影響 strong。

2. **重現洩漏再修好**：把本章的循環 `Node` 例子跑一遍確認 drop **不**觸發（洩漏），再把其中一個方向改成 `Weak`，確認 drop 觸發（修好）。用 `RUST_BACKTRACE` 跑 `RefCell` double borrow，看 panic 的 backtrace 指到哪一行——這是你未來 debug RefCell 借用衝突的實戰。

3. **Cell vs RefCell 選型**：實作一個計數器結構，內部一個 `usize`，提供 `increment()`（只 `&self`）。先用 `Cell` 實作（整體 get/set），再用 `RefCell` 實作（borrow_mut）。比較兩者的 `size_of`，說明為什麼這個場景 `Cell` 更合適（`usize` 是 `Copy`、整體換就夠、零 panic 風險）。

## 本章重點整理

- **`Box<T>` = 唯一擁有的 heap 指標**：就是一個裸指標大小（真跑 8 bytes）+ 一次 heap alloc，對照 `unique_ptr`，move-only 由編譯器強制；遞迴型別和 `Option<Box<T>>`（niche、null 結尾）的基礎。
- **`Rc<T>`/`Arc<T>` = 引用計數共享**：heap 上 RcBox 存 strong/weak 計數 + 資料（`Cell<usize>` 計數），`clone` 只 +1 不複製資料（真跑三個 `Rc` 同位址）。`Rc` 非原子單 thread（!Send，真跑編譯錯誤）、`Arc` 原子跨 thread。
- **循環引用會洩漏**：`Rc` 不偵測環（真跑 drop 不觸發），「往回」方向用 `Weak`（不算 strong、`upgrade` 安全取回）打破循環（真跑 drop 恢復）。
- **內部可變性 = 合法繞過「aliasing XOR mutability」**：`Cell`（整體 get/set，零開銷零 panic，Copy 值）vs `RefCell`（借出參考、執行期借用檢查、違規 panic `RefCell already borrowed`）；底層都是 `UnsafeCell`（唯一合法的「透過 `&` 可改」原語，Ch 20/21 深挖）。
- **`Rc<RefCell<T>>` = 多擁有者 + 可變**，標準 pattern，但代價（執行期檢查、panic 風險、單 thread）疊加；不是預設，是純借用表達不了時的工具。

## 自我檢核

- [ ] 能畫出 RcBox 的 heap 佈局（strong/weak 計數 + data），並解釋 `Rc::clone` 和 drop 各改了什麼。
- [ ] 不看筆記，能說明為什麼 `Rc` 不是 `Send`、什麼時候必須換 `Arc`，以及編譯器怎麼擋你。
- [ ] 能解釋循環引用怎麼洩漏、`Weak` 怎麼修，以及 `upgrade()` 為什麼回 `Option`。
- [ ] 能區分 `Cell` 和 `RefCell` 的適用場景，並說出 `RefCell` double borrow 會發生什麼（執行期 panic，哪兩種訊息）。
- [ ] 知道 `UnsafeCell` 是所有內部可變性的底層原語，以及為什麼它是「透過 `&` 可改」的唯一合法例外。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 15「Smart Pointers」** — （[doc.rust-lang.org/book/ch15-00-smart-pointers.html](https://doc.rust-lang.org/book/ch15-00-smart-pointers.html)）
  - **讀哪裡**：15.1（`Box`）、15.4（`Rc`）、15.5（`RefCell` + 內部可變性）、15.6（`Rc<RefCell>` 循環 + `Weak`）。本章的例子和 The Book 的 `List`/`Node` 例子同源。
  - **學到什麼**：本章每個型別的官方入門對應，尤其 15.6 用 tree 例子完整演示循環洩漏 + `Weak` 修法，是本章那節的擴充版。
  - **前提**：懂 Ch 3 借用規則；The Book 節奏比本課慢，適合當補課。

- **《The Rustonomicon》「Interior Mutability」與 std 文件的 `std::cell` 模組頁** — （[doc.rust-lang.org/std/cell/index.html](https://doc.rust-lang.org/std/cell/index.html)）
  - **讀哪裡**：`std::cell` 模組頁開頭的「Introducing mutability inside of something immutable」整段，以及 `UnsafeCell` 的文件——講清楚 `UnsafeCell` 為什麼是「透過 `&` 可改」的唯一合法出口。
  - **學到什麼**：本章「底層原語 `UnsafeCell`」那節的權威版；為什麼編譯器對一般 `&T` 假設不可變、`UnsafeCell` 怎麼豁免這個假設。
  - **前提**：懂本章 `Cell`/`RefCell` 的 API；這頁是它們的實作契約。

### 技術文章 / 原始碼

- **std 原始碼 `library/alloc/src/rc.rs`（`RcBox` 定義與 `clone`/`drop` 實作）** — （[github.com/rust-lang/rust/blob/master/library/alloc/src/rc.rs](https://github.com/rust-lang/rust/blob/master/library/alloc/src/rc.rs)）
  - **讀哪裡**：搜 `struct RcInner`（或 `RcBox`，版本間名稱有變）看 strong/weak/value 三欄位的定義；看 `impl Clone for Rc` 怎麼把 strong +1、`impl Drop for Rc` 怎麼判斷歸零。
  - **學到什麼**：本章 RcBox 佈局圖的真實原始碼——你會看到計數真的是 `Cell<usize>`、drop 真的分「value 何時 drop」和「整塊 RcBox 何時釋放」兩階段（跟 weak 計數的關係）。
  - **前提**：懂本章佈局圖 + 一點 `UnsafeCell`；本課 Ch 33 之後會教你系統性讀 std 原始碼，這裡先當範本。

- **「Rc<RefCell> and Arc<Mutex> patterns / Rust for Rustaceans Ch 1（Foundations）的 interior mutability 段」** — Jon Gjengset（No Starch Press, 2021）
  - **讀哪裡**：Ch 1 講型別佈局與 `Cell`/`RefCell`/`UnsafeCell` 關係的那幾頁；Ch 上關於 `Rc`/`Arc` 共享的討論。
  - **學到什麼**：中階視角把本章這些型別的取捨（何時 `Rc` 何時 `Arc`、內部可變性的成本）講得比 The Book 深，和本課定位重合。
  - **前提**：懂本章基本 API；此書假設有系統程式背景。

搞懂了智慧指標怎麼在安全 API 底下用 heap、計數、`UnsafeCell` 這些機制，下一章我們掀開最後一層蓋子——**`unsafe`**。`Box`/`Rc`/`RefCell` 內部全都是 unsafe 撐起來的，下一章教你 unsafe 的五種 superpower、裸指標怎麼玩、以及「soundness（健全性）」這條 C 從來沒有的線畫在哪。

→ [Ch 17 unsafe 基礎：五種 superpower](./17-unsafe-basics.md)
