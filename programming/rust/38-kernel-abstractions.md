# Ch 38 — kernel 抽象：kernel crate 與 pin-init

> **目標**：拆開 [Ch 37](./37-rust-for-linux-overview.md) 架構圖第 3 層——`kernel` crate 這個把 C 的 unsafe 世界包成 safe Rust 的抽象層。學完你能讀懂並解釋 RfL 的四大核心抽象：(1) 錯誤處理 `Result`/`Error`/`kernel::error`（對照 C 回傳 `-ENOMEM`）；(2) `Arc<T>` 引用計數（對照 `kref`/`refcount_t`）；(3) `KBox`/`KVec` 的 **fallible allocation**（對照 `kmalloc` 會回 NULL）；(4) 同步原語 `Mutex<T>`/`SpinLock<T>` 的 RAII guard（對照 C 手動 `mutex_lock`/`unlock`）。並且理解為什麼 kernel 物件需要 `pin_init!`/`#[pin_data]` 這套 **in-place 初始化**——連回 [Ch 27](./27-async-executor-pin.md) 的 `Pin`。最後看清 `// SAFETY:` 契約怎麼把 C 的 unsafe 封起來。

> **環境考據**：RfL 的 `kernel` crate API **未穩定、版本間會變**。本章的 API 樣貌依 kernel 主線原始碼樹 `rust/kernel/` 與 `rust.docs.kernel.org` 的預生成文件、`samples/rust/`（**2026-08 查證**）。真正的 kernel module 跑不了（本機無 build tree，見 [Ch 37](./37-rust-for-linux-overview.md)），所以每個 kernel API 片段都標「未實測，理論預期（依原始碼）」；能用純 `rustc` 驗**形狀**的（RAII guard、refcount、fallible alloc、self-ref move hazard）都在本機（WSL2 `rustc 1.97.1`）真跑並標明。

## 為什麼需要這個？

你在 [Ch 37](./37-rust-for-linux-overview.md) 看到，Rust driver 不直接呼叫 `kmalloc`/`mutex_lock`，而是透過 `kernel` crate 的安全封裝。這一章就是問：**那個封裝長什麼樣？它憑什麼「安全」？**

答案不是魔法。C 的 kernel API 每一個都有一組**隱性契約**——`kmalloc` 可能回 NULL（你必須檢查）、`mutex_lock` 之後**必須**有配對的 `mutex_unlock`（漏了就死鎖）、`kref_get` 之後**必須**有配對的 `kref_put`（漏了泄漏、多了 UAF）。C 不強制你遵守這些契約，靠的是 code review 和你的紀律。**`kernel` crate 做的事，就是把每個這種隱性契約，用 Rust 的型別系統變成編譯器強制的顯性規則。**

- `kmalloc` 回 NULL → 包成 `Result`，型別強制你處理失敗（不能假裝配置一定成功）。
- `mutex_lock`/`unlock` 配對 → 包成 RAII guard，離開作用域自動 unlock，**編譯器保證不會漏**。
- `kref_get`/`put` 配對 → 包成 `Arc<T>`，clone/drop 自動配對 refcount。

這就是為什麼理解這一層，比會用 `module!` 巨集重要得多。`module!` 只是入口；**這一層才是 RfL「用 Rust 就能安全寫 kernel」這句話的全部技術內容**。你 [Ch 16](./16-smart-pointers.md) 學過 `Rc`/`Arc`/`RefCell` 的底層、[Ch 12](./12-core-traits.md) 學過 `Drop`、[Ch 13](./13-error-handling.md) 學過 `Result`/`?`、[Ch 27](./27-async-executor-pin.md) 學過 `Pin`——這一章是把那些全部搬到 kernel 場景，你會發現它們就是為了這種場景設計的。

## 先建立直覺：契約從「你記得」變成「型別記得」

C kernel 開發的日常，是一連串「你必須記得」：

```
C driver 作者的腦內清單（漏一項就是 CVE）：
  □ kmalloc 之後檢查回傳是不是 NULL
  □ 每個 mutex_lock 都要有配對的 unlock（每條 error path 也要）
  □ 每個 kref_get 都要有配對的 kref_put
  □ 這塊記憶體 free 之後，別再有人持有指標
  □ 這個物件被 C 的 list 掛著，別把它 move 到別的位址
```

Rust 的 `kernel` crate 把這張清單從「你的腦」搬到「型別系統」：

```
Rust driver 作者：型別幫你記
  ✓ 配置回 Result<T> ── 不處理 Err 編譯器警告你
  ✓ lock() 回 Guard ── 離開作用域自動 unlock，忘不了
  ✓ Arc<T> clone/drop ── refcount 自動配對，多不了少不了
  ✓ ownership ── free（drop）後編譯器不讓你再碰
  ✓ Pin<T> ── 型別標記「這東西不准 move」
```

每一項左邊是「靠紀律」，右邊是「靠型別」。這章接下來就是逐項拆開右邊那五個 ✓ 怎麼實作出來的。核心手法你都學過：`Result`（[Ch 13](./13-error-handling.md)）、`Drop`（[Ch 12](./12-core-traits.md)）、`Arc`（[Ch 16](./16-smart-pointers.md)）、ownership（[Ch 2](./02-ownership-move.md)）、`Pin`（[Ch 27](./27-async-executor-pin.md)）。RfL 沒有發明新的語言機制，它是把既有機制**用在對的地方**。

## 抽象一：錯誤處理 `Result` / `Error` / `kernel::error`

C 的 kernel 函式回傳 `int`：`0`（或正值）代表成功，**負的 errno**（`-ENOMEM = -12`、`-EINVAL = -22`）代表失敗。呼叫端要一層層檢查：

```c
/* C kernel 慣例：回負 errno，呼叫端手動檢查、手動往上傳 */
static int my_init(void) {
    char *buf = kmalloc(1024, GFP_KERNEL);
    if (!buf)                       /* 忘了檢查 → NULL deref */
        return -ENOMEM;
    int ret = do_setup(buf);
    if (ret < 0) {                  /* 忘了檢查 → 帶著壞狀態往下走 */
        kfree(buf);                 /* 忘了 free → leak */
        return ret;                 /* 手動把 errno 往上傳 */
    }
    /* ... */
    return 0;
}
```

RfL 的 `kernel::error` 把這套包成 Rust 的 `Result`（**未實測，API 依 `rust/kernel/error.rs`，2026-08**）：

- `kernel::error::Error`：包一個負 errno 的整數錯誤型別。
- `kernel::prelude::Result<T>`（`= Result<T, Error>`）：kernel 版的 `Result`，預設 `T = ()`。
- `kernel::error::code::*`：`ENOMEM`、`EINVAL`、`EBUSY`... 這些常數（是 `Error` 值，不是裸 int）。
- `?` 運算子：跟一般 Rust 一樣傳播 `Error`。
- `kernel` crate 在 C 邊界用 `from_result` 之類的 helper，把 `Result` 轉回 C 期待的「成功 0 / 失敗負 errno」——**這層轉換是 `kernel` crate 做的，你的 driver 只寫 `Result` + `?`**。

同一段邏輯的 Rust 樣貌（**未實測，理論預期**）：

```rust
// Rust kernel code：用 Result<T> + ?，錯誤傳播自動、不能忽略
use kernel::prelude::*;

fn my_init() -> Result {
    let mut buf = KVec::<u8>::new();
    buf.resize(1024, 0, GFP_KERNEL)?;   // 配置失敗 → 自動回 Err(ENOMEM)，? 傳播
    do_setup(&mut buf)?;                 // do_setup 回 Err → ? 自動往上傳，buf 自動 drop
    Ok(())                               // buf 離開作用域自動 kfree，忘不了
}
```

三個對照 C 的關鍵勝利：

1. **配置失敗不能被忽略**：`?` 強制你要嘛處理 `Err`、要嘛往上傳。C 的 `if (!buf)` 你可以「忘了寫」，Rust 的 `?` 你不寫就編不過（回傳型別對不上）。
2. **錯誤傳播不用手抄**：C 要手寫 `return ret;`，Rust 的 `?` 自動。而且 `Result` 帶 `#[must_use]`，你拿到一個 `Result` 不處理，編譯器警告。
3. **error path 的清理自動**：C 的 `kfree(buf); return ret;` 那兩行——每條 error path 都要重複，漏一條就 leak。Rust 靠 `Drop`：`buf` 離開作用域（不管是正常結束還是 `?` 提前返回）都自動釋放。這消滅了 C 裡最惡名昭彰的 `goto err_free;` 迷宮。

> 本機能驗這個形狀（見 [Ch 37](./37-rust-for-linux-overview.md) 末尾那段真跑的 errno 映射 demo）。真正的 `kernel::error::Error` 內部與那個 demo 的 `struct Error(i32)` 不同（它還帶 `EFAULT` 等特殊處理），但 `?` 傳播 + 轉負 errno 給 C 的形狀一致。

## 抽象二：`Arc<T>` 引用計數（對照 `kref`/`refcount_t`）

kernel 物件常常被多方持有——一個 device 被多個 fd 引用、一個 buffer 被多個 request 共享。C 用 `kref`（或裸 `refcount_t`）手動管：

```c
/* C：kref 手動配對，漏一個就是 leak（少 put）或 UAF（多 put）*/
struct my_obj { struct kref refcount; int data; };
void obj_get(struct my_obj *o) { kref_get(&o->refcount); }
void obj_release(struct kref *r) {
    struct my_obj *o = container_of(r, struct my_obj, refcount);
    kfree(o);                       /* refcount 歸零才呼到這 */
}
void obj_put(struct my_obj *o) { kref_put(&o->refcount, obj_release); }
```

每次多一個持有者要 `obj_get`，每次放手要 `obj_put`，**手動配對**。漏一個 `put` → refcount 永不歸零 → leak；多一個 `put` → 提前歸零 → 別人手上的指標變 UAF。這是 kernel CVE 的大宗。

RfL 的 `kernel::sync::Arc<T>` 把這套自動化（**API 依 `rust/kernel/sync/arc.rs`，2026-08 查證**）。它對照的正是 `kref`/`refcount_t`。和你 [Ch 16](./16-smart-pointers.md) 學的 `std::sync::Arc` 有幾個 kernel 特有差異：

| 面向 | `std::sync::Arc` | `kernel::sync::Arc`（RfL） |
|---|---|---|
| refcount 機制 | 內建 atomic | 對照 kernel `refcount_t`/`kref` |
| 建構 | `Arc::new(x)`（配置不會失敗，OOM 直接 abort） | `Arc::new(x, GFP_KERNEL)?`（**fallible**，回 `Result`） |
| 內容是否 pinned | 否 | **永遠 pinned**（配合 in-place init，見後面 pin-init 節） |
| C 互操作 | 無 | 實作 `ForeignOwnable`：`into_raw`/`from_raw` 給 C 持有 |

用起來的形狀（**未實測，理論預期**）：

```rust
use kernel::sync::Arc;
use kernel::prelude::*;

struct Shared { data: i32 }

fn demo() -> Result {
    let a = Arc::new(Shared { data: 42 }, GFP_KERNEL)?;  // refcount=1，配置可能失敗→?
    let b = a.clone();                                    // refcount=2（對照 kref_get）
    // 用 a、b...
    Ok(())
    // b、a 依序離開作用域 → 各自 refcount-1（對照 kref_put）
    // 歸零時自動 drop Shared（對照 obj_release + kfree）
}
```

`clone()` 是 `kref_get`，`drop`（離開作用域）是 `kref_put`，refcount 歸零自動釋放——**編譯器保證 clone 和 drop 配對，你漏不了也多不了**。C 那套 `obj_get`/`obj_put`/`obj_release`/`container_of` 全部消失，變成型別自帶的行為。

這個形狀能用 `std::sync::Arc` 在本機真跑驗證（機制對照一致，只差 kernel 版的 fallible 建構）：

```rust
// 本機真跑：std Arc 的 refcount 對照 kernel Arc / kref 的形狀
use std::sync::Arc;
struct Shared { id: u32 }
impl Drop for Shared {
    fn drop(&mut self) { println!("Shared {} freed (refcount hit 0)", self.id); }
}
fn main() {
    let a = Arc::new(Shared { id: 7 });      // refcount=1  (C: kref_init)
    let b = Arc::clone(&a);                   // refcount=2  (C: kref_get)
    println!("strong_count = {}", Arc::strong_count(&a));
    drop(b);                                  // refcount=1  (C: kref_put)
    println!("strong_count = {}", Arc::strong_count(&a));
    // a drop at end → 0 → Drop runs (C: kref_put → obj_release → kfree)
}
```

本機（`rustc 1.97.1`）真跑輸出：

```
strong_count = 2
strong_count = 1
Shared 7 freed (refcount hit 0)
```

看那行 `Shared 7 freed`——它在 `a` 離開 `main` 時自動印出，我從沒手動呼任何 `put`。這就是 `kref` 那套配對邏輯被型別接管的樣子。

## 抽象三：`KBox` / `KVec` 與 fallible allocation

這是 kernel Rust 和一般 Rust 最大的分野，也是最容易被忽略的一點。

一般 Rust 的 `Box::new(x)`、`Vec::push(x)`：**配置失敗會 abort 整個程式**。在 userspace 這通常沒問題（OOM 本來就近乎絕境）。但在 **kernel 裡，abort = panic = `BUG()` = 可能整台機器掛掉**。kernel 絕不能因為一次配置失敗就倒——它必須**優雅地處理配置失敗**，回一個錯誤碼給呼叫者。這就是 **fallible allocation（可失敗配置）**：配置回 `Result`，失敗回 `Err(ENOMEM)`，而不是 abort。

C 的 `kmalloc` 天生就是 fallible 的——它回指標，失敗回 NULL，你檢查。RfL 需要 Rust 版的容器也是這個語意。所以它**不用** `alloc` crate 的 `Box`/`Vec`（那些會 abort），而是自己的：

| C | RfL 型別 | 語意 |
|---|---|---|
| `kmalloc(size, GFP_KERNEL)` | `KBox::new(x, GFP_KERNEL)` | 單一堆配置，回 `Result` |
| `kmalloc` + 陣列 + 手動 grow | `KVec<T>` + `push(x, GFP_KERNEL)` | 動態陣列，每次可能 grow 的操作都回 `Result` |
| `GFP_KERNEL` / `GFP_ATOMIC` | `GFP_KERNEL` / `GFP_ATOMIC`（`Flags` 型別） | 配置旗標，決定能不能睡、從哪個 zone 拿 |

關鍵差異在**每個可能配置的操作都吃一個 `Flags` 參數並回 `Result`**。看 `samples/rust/rust_minimal.rs` 的真實 code（**這是主線 kernel 樹裡的真實 sample，2026-08 查證**）：

```rust
// 摘自 samples/rust/rust_minimal.rs（主線 kernel 樹）
let mut numbers = KVec::new();
numbers.push(72, GFP_KERNEL)?;      // 注意：push 吃 GFP_KERNEL，且回 Result（?）
numbers.push(108, GFP_KERNEL)?;     // grow 失敗會回 Err(ENOMEM)，不 abort
numbers.push(200, GFP_KERNEL)?;
```

對照一般 Rust 的 `v.push(72)`（不吃 flag、不回 Result、失敗 abort），差別一目了然：**RfL 的 `push` 簽章逼你面對「這次配置可能失敗」和「你想用哪種配置旗標」兩件事**。`GFP_KERNEL`（可睡眠、正常配置）vs `GFP_ATOMIC`（不可睡眠，例如在中斷或持 spinlock 時用）——這是 kernel 記憶體配置的核心概念，C 裡你也要選，Rust 把它做進型別。

`GFP_KERNEL` 這個名字不是 magic：`GFP` = Get Free Page 的旗標家族，`GFP_KERNEL` 是「一般 kernel context 的配置，允許睡眠等待記憶體回收」。`GFP_ATOMIC` 是「不能睡眠的 context（中斷處理、持 spinlock），配置不到就立刻失敗」。選錯（在中斷裡用 `GFP_KERNEL`）在 C 裡是難查的 bug，Rust 一樣要你選對——這是 kernel 領域知識，不是 Rust 幫得了的。

fallible allocation 的**形狀**能在本機用 `Vec::try_reserve`（std 唯一的 fallible 配置 API）真跑驗證：

```rust
// 本機真跑：try_reserve 對照 KVec::push(.., GFP_KERNEL)? 的 fallible 形狀
// try_reserve 是 std 少數不 abort 而回 Err 的配置 API，語意跟 kernel 一致
#[derive(Debug)]
struct AllocError;

fn build(n: usize) -> Result<Vec<u64>, AllocError> {
    let mut v: Vec<u64> = Vec::new();
    v.try_reserve(n).map_err(|_| AllocError)?;   // fallible：不 abort，回 Err
    for i in 0..n as u64 { v.push(i); }
    Ok(v)
}

fn main() {
    match build(5) {
        Ok(v)  => println!("built len={}, sum={}", v.len(), v.iter().sum::<u64>()),
        Err(_) => println!("alloc failed -> 會回 -ENOMEM 給 C"),
    }
    match build(usize::MAX / 8) {                // 巨大請求
        Ok(v)  => println!("built len={}", v.len()),
        Err(_) => println!("alloc failed -> 會回 -ENOMEM 給 C"),
    }
}
```

本機真跑輸出：

```
built len=5, sum=10
alloc failed -> 會回 -ENOMEM 給 C
```

第二行證明了 fallible 的意義：`build(usize::MAX/8)` 要配置天量記憶體，`try_reserve` **回 `Err` 而不是 abort**——如果用 `Vec::reserve`（infallible），這裡會直接 abort 整個程式。kernel 的 `KVec::push` 就是這個語意：配不到回 `Err(ENOMEM)`，讓 `?` 把錯誤優雅地傳回，kernel 不倒。

## 抽象四：同步原語 `Mutex<T>` / `SpinLock<T>` 的 RAII guard

這是 RfL 最漂亮的抽象之一，直接命中 C kernel 最常見的 bug 類型。

C 的鎖是**手動配對**的：

```c
/* C：lock/unlock 手動配對。每條 return path 都要記得 unlock，漏一條就死鎖 */
struct my_data { struct mutex lock; int value; };
int increment(struct my_data *d) {
    mutex_lock(&d->lock);
    d->value++;
    if (d->value > 100) {
        mutex_unlock(&d->lock);     /* 這條 error path 要記得 unlock */
        return -EINVAL;
    }
    mutex_unlock(&d->lock);         /* 正常 path 也要 */
    return 0;
}
```

三個 C 的問題：(1) 每條 return path 都要手動 `mutex_unlock`，漏一條就死鎖；(2) `d->value` 和 `d->lock` 在型別上**沒有關聯**——你可以不拿鎖就存取 `d->value`，編譯器不擋；(3) 忘記解鎖是執行期才炸（死鎖），不是編譯期。

RfL 的 `Mutex<T>` 用兩個手法解決（**API 依 `rust/kernel/sync/`，2026-08**）：

1. **鎖包住資料**：`Mutex<Inner>`——要碰 `Inner` **必須先拿鎖**。鎖和資料在型別上綁死，繞不過去（對照你 [Ch 24](./24-shared-state.md) 學的 `std::sync::Mutex<T>`，同一個設計）。
2. **RAII guard**：`lock()` 回一個 guard，透過 guard 存取資料；**guard 離開作用域自動 unlock**（`Drop`）。你**忘不了**解鎖，因為那不是你做的，是型別做的。

Rust 樣貌（**未實測，理論預期**）：

```rust
use kernel::sync::Mutex;
use kernel::prelude::*;

struct Inner { value: i32 }

// d: &Mutex<Inner>
fn increment(d: &Mutex<Inner>) -> Result {
    let mut guard = d.lock();        // 對照 mutex_lock；回 guard
    guard.value += 1;                // 透過 guard 存取（繞不過鎖）
    if guard.value > 100 {
        return Err(EINVAL);          // guard 在此 drop → 自動 unlock，忘不了
    }
    Ok(())                           // guard 在此 drop → 自動 unlock
}
```

看那條 error path：`return Err(EINVAL)` 之前**沒有** `unlock`——因為 `guard` 離開作用域時 `Drop` 自動解鎖。C 那條 `mutex_unlock(&d->lock); return -EINVAL;` 的手動配對消失了。這消滅了「某條 error path 忘了 unlock 導致死鎖」這整類 bug。

這個 guard 行為能在本機用 `std::sync::Mutex` 真跑驗證（RfL 的 `Mutex` 內部是 kernel `struct mutex`，但 guard-drops-unlocks 的形狀一致）：

```rust
// 本機真跑：RAII lock guard 的形狀（std Mutex），對照 kernel Mutex
use std::sync::Mutex;
struct Inner { value: i32 }
fn bump(m: &Mutex<Inner>) -> i32 {
    let mut guard = m.lock().unwrap();   // C: mutex_lock(&m)
    guard.value += 1;
    guard.value
    // guard 在這裡 drop → 自動 unlock。C: 你必須手寫 mutex_unlock(&m)
}
fn main() {
    let m = Mutex::new(Inner { value: 0 });
    for _ in 0..3 { println!("value = {}", bump(&m)); }
}
```

本機真跑輸出：

```
value = 1
value = 2
value = 3
```

`bump` 裡沒有任何 unlock 呼叫，但每次呼叫的臨界區都正確地在 `guard` drop 時結束。這就是 RAII guard——臨界區的範圍 = guard 的生存期，由作用域界定，編譯器管理。

`SpinLock<T>` 是同一個模式，只是底層是 kernel 的 `spinlock_t`（不睡眠、忙等，用在中斷 context 或極短臨界區）。選 `Mutex` 還是 `SpinLock` 是 kernel 領域決策（能不能睡眠），但兩者的 RAII guard 介面一致。

## pin-init：為什麼 kernel 物件需要 in-place 初始化

這是本章最需要「先建立直覺」的一塊，因為它反直覺。

### 問題：有些物件不能被 move

一般 Rust 物件可以自由 move（[Ch 2](./02-ownership-move.md)）——`let b = a;` 把 `a` 的內容搬到 `b` 的位址，`a` 失效。move 只是 memcpy + 標記舊的失效。但有些 kernel 物件**move 之後會壞掉**，因為有東西記住了它的**位址**：

- **自我引用**：物件內部有指標指向自己的另一個欄位。move 到新位址後，那個指標還指著舊位址（死的）。
- **被 C 的 intrusive list 掛著**：kernel 的 `list_head` 是 intrusive 的——`list_head` 嵌在你的 struct 裡，前後節點的指標指向**你這個 struct 的位址**。你一 move，前後節點的指標就指向舊位址，整條 list 爛掉。
- **被 C 持有裸指標**：你把物件註冊給 kernel（例如一個 `struct mutex` 初始化後，lockdep 記了它的位址；或一個 timer 記了 callback 資料的位址）。move 之後 C 手上的指標懸空。

C 怎麼處理？C **根本不 move 物件**——它 `kmalloc` 一塊記憶體，然後**在那塊記憶體原地**初始化（`mutex_init(&obj->lock)`、`INIT_LIST_HEAD(&obj->list)`）。物件從生到死都在同一個位址。C 沒有「move 語意」這回事，所以沒這個問題。

Rust 有 move 語意，這在 userspace 是優點，但碰到「不能 move 的物件」就是麻煩。這正是 [Ch 27](./27-async-executor-pin.md) 的 `Pin` 要解決的問題——`Pin<P>` 是型別層級的標記，說「這東西被釘在原地，不准 move」。

> 如果你對 `Pin` 為什麼存在、`!Unpin` 是什麼還沒把握，回看 [Ch 27](./27-async-executor-pin.md) 的「Pin 與自我引用」一節。RfL 的 pin-init 就是 `Pin` 在 kernel 場景的落地。

### 先看 move 為什麼壞掉（本機真跑）

用一個自我引用的 struct 示範「naive 建構 + move」怎麼產生懸空的自我指標：

```rust
// 本機真跑：自我引用的 struct 被 move 之後，自我指標就懸空。
// 這正是 kernel 物件的問題（嵌在 C list、被 C 持有指標），motivates pin_init!。
struct SelfRef {
    data: [u8; 4],
    ptr_to_data: *const u8,   // 指向同一個 struct 裡的 data
}
impl SelfRef {
    fn new() -> Self {
        let mut s = SelfRef { data: [1,2,3,4], ptr_to_data: std::ptr::null() };
        s.ptr_to_data = &s.data as *const u8;   // 指向自己的 data 欄位
        s
        // BUG：return by value 把 s MOVE 到新位址；ptr_to_data 還指著舊（死）位址
    }
}
fn main() {
    let s = SelfRef::new();
    let field_addr = &s.data as *const u8 as usize;
    let stored_ptr = s.ptr_to_data as usize;
    println!("current field addr = {:#x}", field_addr);
    println!("stored self-ptr    = {:#x}", stored_ptr);
    println!("match? {}", field_addr == stored_ptr);
}
```

本機真跑輸出（位址每次執行不同，但兩者**不相等**是重點）：

```
current field addr = 0x7ffe407776c8
stored self-ptr    = 0x7ffe407776a8
match? false
```

`match? false`——存起來的自我指標指向 `0x...76a8`（`new` 裡 `s` 的舊位址），但 move 之後 `data` 真正在 `0x...76c8`。那個自我指標**懸空**了。在 kernel 裡這不是印錯位址而已，是 list 損毀、UAF、機器掛掉。

### 解法：在最終位址原地初始化，不 move

問題的根源是「先在 A 建構、再 move 到 B」。解法是 **in-place 初始化**：先配好最終的記憶體（B），然後**直接在 B 上初始化**，中間沒有 move。這正是 C 的做法（`kmalloc` 完在原地 `INIT_*`），RfL 用 `pin_init!` 巨集把它做成安全、可組合的 Rust。

RfL 的 pin-init 框架三個要件（**API 依 `rust/kernel/` 與 pin-init crate，2026-08 查證**）：

1. **`#[pin_data]`**：標在 struct 上，標記哪些欄位是 `#[pin]`（不可 move，例如 `Mutex`——它內部有 lockdep 記的位址）。
2. **`pin_init!` / `try_pin_init!`**：一個巨集，描述「怎麼在原地初始化這個 struct」，回傳一個 **initializer**（一個「知道怎麼在給定位址上把自己建好」的東西），而不是一個已經建好、需要 move 的值。
3. **`KBox::pin_init` / `Arc::pin_init`**：拿 initializer，配一塊 kernel 記憶體，在**那塊記憶體原地**執行初始化，回傳 `Pin<KBox<T>>` / `Arc<T>`（永遠 pinned，不能 move）。

看 `samples/rust/rust_misc_device.rs` 的真實用法（**主線 kernel 樹真實 sample，2026-08 查證**）：

```rust
// 摘自 samples/rust/rust_misc_device.rs（主線 kernel）
use kernel::{new_mutex, sync::Mutex};

#[pin_data(PinnedDrop)]          // 標記：這 struct 有 #[pin] 欄位，且有 pinned drop
struct RustMiscDevice {
    #[pin]                       // Mutex 不可 move（內部有 kernel 記的位址）
    inner: Mutex<Inner>,
    dev: ARef<Device>,
}

// 在原地初始化：try_pin_init! 產生 initializer，KBox::try_pin_init 在配好的記憶體上執行它
fn open(/* ... */) -> Result<Pin<KBox<Self>>> {
    KBox::try_pin_init(
        try_pin_init! {
            RustMiscDevice {
                inner <- new_mutex!(Inner { value: 0_i32, /* ... */ }),  // <- 是「原地初始化這個欄位」
                dev: dev,                                                // : 是「這個值直接放進去」
            }
        },
        GFP_KERNEL,               // fallible：配置失敗回 Err
    )
}
```

讀這段的關鍵：

- **`<-` vs `:`**：`inner <- new_mutex!(...)` 的 `<-` 表示「這個欄位要**原地初始化**」（因為 `Mutex` 不可 move）；`dev: dev` 的 `:` 是普通的「把這個值放進去」（`ARef` 可以 move）。這個語法區分是 pin-init 的核心。
- **`new_mutex!`**：產生一個「在原地把 `Mutex` 建好」的 initializer，而不是一個建好的 `Mutex`（後者需要 move，不行）。
- **回傳 `Pin<KBox<Self>>`**：整個物件被釘在 `KBox` 配的那塊記憶體上，型別標記 `Pin` 保證它此後不能被 move——可以安全地嵌 list、給 C 持有指標。

對照 C：C 的 `kmalloc` + `mutex_init(&obj->lock)` 就是「配記憶體 + 原地初始化鎖」。pin-init 是這個模式的 Rust 版，多了型別安全（`Pin` 保證不 move）和 fallible（`GFP_KERNEL` + `Result`）。**它不是為了複雜而複雜——它是把 C 一直在做的「原地初始化」這件事，用型別系統表達出來並保證安全。**

## `// SAFETY:` 契約：unsafe 邊界怎麼被封起來

最後看這一層的「安全」到底建立在什麼上。`kernel` crate 內部**充滿 unsafe**——它要呼叫 bindgen 生的 raw C binding（[Ch 37](./37-rust-for-linux-overview.md) 架構圖第 2 層），那些全是 unsafe。差別在：**每一個 unsafe 塊上面都有一個 `// SAFETY:` 註解，寫明「為什麼這裡的 unsafe 是 sound 的」**——這是 RfL（和整個 Rust 生態）的鐵律。

概念形狀（**未實測，示意 `rust/kernel/` 的慣例**）：

```rust
pub fn lock(&self) -> Guard<'_, T> {
    // SAFETY: `self.mutex` 指向一個已初始化的 kernel struct mutex
    // （建構時保證，見 Mutex::new 的 pin-init）。此處呼叫 C 的 mutex_lock
    // 在此 context 是合法的，且回傳後我們持有鎖直到 Guard drop。
    unsafe { bindings::mutex_lock(self.mutex.get()) };
    // ... 回傳 Guard，其 Drop 會呼 mutex_unlock
}
```

`// SAFETY:` 不是裝飾。它是**契約的書面化**：這行 unsafe 依賴哪些前提（`self.mutex` 已初始化、context 合法），為什麼這些前提在這裡成立。審 RfL patch 的人，很大一部分工作就是審這些 `// SAFETY:` 註解對不對——前提真的成立嗎？有沒有路徑會破壞它？（你 [Ch 32](./32-audit-unsafe.md) 學的 audit unsafe，在 kernel 就是這件事。）

這就是為什麼架構圖第 3 層是「有人審、有人維護、driver 作者信任它」：**安全性不是自動的，是靠一堆被人類審過的 `// SAFETY:` 契約撐起來的**。driver 作者（你）在 safe Rust 工作，享受這層的保證；但這層本身的正確性，是 kernel Rust 維護者用 `// SAFETY:` 審計換來的。誠實地說：**這層 API 未穩定、版本間會變**——你今天讀的 `Mutex`/`Arc`/`KVec` 簽章，下個版本可能微調。這是 RfL 現階段的現實，寫 code 時要對著你那棵 kernel 樹的 `rust/kernel/` 和 rustdoc，不要背 API。

## 對比與取捨

| 抽象 | C 手動做法 | RfL 型別做法 | 型別擋掉的 bug |
|---|---|---|---|
| 錯誤處理 | 回負 errno，手動 `if(ret<0)` + `goto err` | `Result<T>` + `?`，`Drop` 自動清理 | 忘檢查、忘傳播、error path 忘 free |
| 引用計數 | `kref_get`/`put` 手動配對 + `container_of` | `Arc<T>` clone/drop 自動配對 | refcount leak（少 put）、UAF（多 put） |
| 堆配置 | `kmalloc`，回 NULL 自己檢查 | `KBox`/`KVec` + `GFP_*`，回 `Result` | 忘檢查 NULL、abort 掉整台機器 |
| 上鎖 | `mutex_lock`/`unlock` 手動配對 | `Mutex<T>` + RAII guard | error path 忘 unlock（死鎖）、沒鎖就存取 |
| 不可 move 物件 | `kmalloc` + 原地 `INIT_*`（無 move 語意） | `pin_init!` + `Pin<KBox<T>>` | move 掉自我引用/被 C 持有的物件 |

總原則：**RfL 的每個抽象，都是把一個 C 的隱性契約（你必須記得的事）變成型別系統強制的顯性規則（型別幫你記）。** 代價是這套抽象本身複雜（pin-init 尤其），且 API 未穩定；收益是最大宗的一類 kernel bug 在編譯期就寫不出來。

## 踩雷集錦

1. **以為 kernel `Arc`/`Box`/`Vec` 跟 std 的一樣**：不一樣。kernel 版是 **fallible**（`Arc::new(x, GFP_KERNEL)?`、`KVec::push(x, GFP_KERNEL)?`）——每個配置吃 `Flags` 且回 `Result`。std 版配置失敗 abort，在 kernel 裡 abort = `BUG()` = 可能整台掛。搞混會寫出「以為配置一定成功」的 code，或找不到「為什麼 push 要傳 GFP_KERNEL」。

2. **選錯 `GFP_KERNEL` / `GFP_ATOMIC`**：`GFP_KERNEL` 可睡眠（等記憶體回收），**不能**在中斷 context 或持 spinlock 時用；那些場合要 `GFP_ATOMIC`（不睡、配不到立刻失敗）。這是 kernel 領域知識，Rust 幫不了你選——選錯在 C 是難查 bug，在 Rust 一樣。

3. **以為 pin-init 是「多此一舉的複雜」**：不是。它解決的是**真問題**——kernel 物件（含 `Mutex` 自己）常常不能 move（嵌 C list、被 C 持有位址、自我引用）。少了 in-place 初始化，move 一個 `Mutex` 就會讓 kernel 記的位址懸空。本機那個 `SelfRef` demo（`match? false`）就是這個問題的縮影。

4. **在 pin-init 裡搞混 `<-` 和 `:`**：`<-` 是「原地初始化這個欄位」（用於 `#[pin]` 的不可 move 欄位，如 `Mutex`）；`:` 是「把這個值直接放進去」（用於可 move 的欄位）。用錯（對 `Mutex` 用 `:`）會編不過，因為 `Mutex` 需要原地建構。

5. **忘記 RAII guard 的臨界區 = guard 的生存期**：guard 活多久，鎖就持有多久。如果你不小心讓 guard 活太久（例如 `let g = m.lock();` 之後一大段跟鎖無關的 code），臨界區就被撐長，可能拖慢或死鎖。想早點解鎖就早點讓 guard drop（用 `{}` 縮小作用域，或 `drop(g)`）。這跟 C 手動 `unlock` 的位置控制是同一件事，只是換成控制 guard 的作用域。

6. **把 `kernel` crate API 當穩定的背**：它**未穩定、版本間會變**。本章的 `Mutex`/`Arc`/`KVec`/`pin_init!` 簽章依 2026-08 主線，下個版本可能微調。寫 code 對著你那棵 kernel 樹的 `rust/kernel/` 原始碼和 `rust.docs.kernel.org` 的對應版本 rustdoc，別背。

## 進階：再往深一層

- **`InPlaceInit` trait 與 initializer 的本質**：pin-init 的核心是「initializer」這個抽象——一個實作了「在給定的 `*mut T` 上把 `T` 建好」的東西（`PinInit<T, E>` trait）。`pin_init!` 巨集展開成這種 initializer。想真懂，讀 pin-init crate 的 `PinInit`/`Init` trait 定義，理解「為什麼傳遞 initializer 而不是傳遞值」能避免 move。
- **`ForeignOwnable`：Rust 物件交給 C 持有**：`Arc<T>` 實作 `ForeignOwnable`——`into_raw()` 把 Rust 的所有權「凍結」成一個 C 能持有的裸指標（refcount 不變，但 Rust 這邊不再管它），`from_raw()` 拿回。這是「Rust 物件註冊給 C 子系統，之後 C 在 callback 裡把它交還」的關鍵（[Ch 40](./40-rust-driver.md) 的 driver 會大量用到）。對照 [Ch 19](./19-ffi.md) 的 `into_raw`/`from_raw` 所有權轉移。
- **`Opaque<T>` 與 C struct 的封裝**：`kernel` crate 用 `Opaque<T>` 包 C 的 struct（如 `struct mutex`）——它是一個「Rust 不看內部、只持有、允許 C 讀寫」的 wrapper，內部用 `UnsafeCell` + `MaybeUninit`（[Ch 18](./18-unsafe-advanced.md)）。理解它才懂 `kernel` crate 怎麼安全地持有一個「內部佈局由 C 決定、且會被 C 就地改」的物件。
- **面試/研究角度**：能講清楚「fallible allocation 為什麼是 kernel 的硬需求」「pin-init 解決什麼真問題」「RAII guard 怎麼消滅 error-path 忘 unlock」「`// SAFETY:` 契約在 RfL 安全模型裡的角色」，就是真的懂這一層，而不是只會抄 sample。

## 動手練習

1. **改壞 RAII guard demo，體會臨界區範圍**：把本機那個 `bump` 改成在 `guard` 還活著時、同一個 thread 再呼一次 `m.lock()`（`std::sync::Mutex` 不可重入），觀察死鎖。這讓你體會「guard 活著 = 鎖持有中」，以及為什麼要控制 guard 作用域。（kernel `Mutex` 同理，重入自己持有的 mutex 是 bug。）

2. **把 self-ref demo 改成「不 move」**：把本機 `SelfRef` demo 改成先 `Box::new` 一個未設 `ptr_to_data` 的 struct，**在 Box 裡**（固定位址）才設自我指標，然後印出來看 `match?` 變 `true`。這手動模擬了 pin-init「在最終位址原地初始化」的核心思想——先有固定位址，再設指向自己的指標。

3. **對照重寫一段 C**：找一段你熟悉的、有 `mutex_lock`/`unlock` + `kmalloc` + error path `goto err` 的 C kernel 函式，在紙上把它改寫成 RfL 形狀（`Mutex<T>` guard + `KBox`/`KVec` + `Result`/`?`）。數一數 C 版有幾個手動配對點（unlock、free）、Rust 版消掉了幾個。這個對照做完，你對「型別接管契約」會有實感。

## 本章重點整理

- `kernel` crate 是 [Ch 37](./37-rust-for-linux-overview.md) 架構圖第 3 層：把 C 的每個**隱性契約**（要記得檢查 NULL / 配對 unlock / 配對 put / 別 move）變成**型別系統強制的顯性規則**。它本身充滿 unsafe，靠 `// SAFETY:` 契約審計撐起安全性。
- **錯誤處理**：`Result<T>` + `?`（對照回負 errno + 手動 `goto err`），`Drop` 自動清理 error path，配置失敗不能被忽略。
- **`Arc<T>`**：對照 `kref`/`refcount_t`，clone/drop 自動配對 refcount（消滅 leak/UAF），且是 fallible 建構（`GFP_KERNEL` + `?`）、永遠 pinned。
- **`KBox`/`KVec` fallible allocation**：每個配置吃 `Flags`（`GFP_KERNEL`/`GFP_ATOMIC`）且回 `Result`——kernel 不能因配置失敗 abort，必須優雅回 `ENOMEM`。這是 kernel Rust 和一般 Rust 最大的分野。
- **`Mutex<T>`/`SpinLock<T>`**：鎖包住資料（不拿鎖碰不到）+ RAII guard（離開作用域自動 unlock），消滅「error path 忘 unlock」整類死鎖。
- **pin-init**（`#[pin_data]` + `pin_init!` + `Pin<KBox<T>>`）：kernel 物件常不可 move（嵌 C list、被 C 持有位址、自我引用），需要**在最終位址原地初始化**。`<-` 是原地初始化欄位、`:` 是放值。連回 [Ch 27](./27-async-executor-pin.md) 的 `Pin`。

## 自我檢核

- [ ] 不看筆記，能說出 `kernel` crate 四大抽象各對照 C 的什麼（errno/kref/kmalloc/mutex），以及各自的型別擋掉哪類 bug。
- [ ] 能解釋「fallible allocation」是什麼、為什麼是 kernel 的硬需求、跟一般 Rust 的 `Vec::push` 差在哪。
- [ ] 能解釋為什麼 kernel 物件常常「不能 move」（舉至少兩個原因），以及 pin-init 怎麼解決（在最終位址原地初始化），連到 `Pin`。
- [ ] 能說出 RAII lock guard 怎麼消滅「error path 忘 unlock」，以及「臨界區範圍 = guard 生存期」的意思。
- [ ] 知道 `// SAFETY:` 契約在 RfL 安全模型裡扮演什麼角色，以及為什麼「這層 API 未穩定、別背」。

## 延伸閱讀

### 官方文件 / 一手來源

- **kernel 樹 `rust/kernel/`（原始碼）與 [rust.docs.kernel.org](https://rust.docs.kernel.org/kernel/)（預生成 rustdoc）**
  - **讀哪裡**：`rust/kernel/sync/arc.rs`（`Arc`）、`rust/kernel/sync/`（`Mutex`/`SpinLock`）、`rust/kernel/error.rs`（`Error`/`Result`/`code`）、`rust/kernel/alloc/`（`KBox`/`KVec`/`Flags`）。rustdoc 上看對應型別的 API 與 `// SAFETY` 註解。
  - **學到什麼**：本章每個抽象的**真實、當前版本**簽章與 safety 契約——本章刻意標「未實測、API 會變」，這裡是核對活 API 的地方。
  - **前提**：讀完本章對五大抽象的概念理解；帶著「這型別對照 C 的什麼」的問題去讀原始碼最有效。

- **[samples/rust/](https://github.com/torvalds/linux/tree/master/samples/rust)（主線 kernel 樹）** — 官方 sample
  - **讀哪裡**：`rust_minimal.rs`（`KVec::push` + `GFP_KERNEL`）、`rust_misc_device.rs`（`#[pin_data]` + `try_pin_init!` + `Mutex` + `<-` 語法）、`rust_print_main.rs`（`pr_*`）。
  - **學到什麼**：本章 code 片段的完整、可對照的來源；`<-` vs `:`、`new_mutex!`、`Pin<KBox<Self>>` 的真實用法。
  - **前提**：懂本章 pin-init 與 fallible alloc；這是把片段補成完整 module 的橋（也是 [Ch 39](./39-first-kernel-module.md) 的素材）。

### 專案文件 / 部落格

- **[pin-init crate 文件](https://docs.rs/pin-init/)** — RfL pin-init（已獨立成 crate）
  - **讀哪裡**：crate 首頁的 motivation 段（「為什麼需要 in-place 初始化」）與 `pin_init!` 巨集說明；`PinInit`/`Init` trait 的定義。
  - **學到什麼**：pin-init 為什麼傳遞 initializer 而不是值、`<-` 語法的語意、`#[pin_data]` 展開成什麼——本章「進階」那節的 `InPlaceInit` 底層。
  - **前提**：懂本章 pin-init 概念 + [Ch 27](./27-async-executor-pin.md) 的 `Pin`；想真懂 in-place init 機制的下一步。

- **《The Rustonomicon》「Subtyping and Variance」與 `Pin` 相關章** — 官方（[doc.rust-lang.org/nomicon](https://doc.rust-lang.org/nomicon/)）
  - **讀哪裡**：`Pin` 與 `Unpin` 的說明（本課 [Ch 27](./27-async-executor-pin.md) 也涵蓋，這裡是權威版）。
  - **學到什麼**：`Pin<P>` 的型別層級保證為什麼能防止 move、`!Unpin` 的意義——pin-init 保證「不可 move」的底層依據。
  - **前提**：懂 [Ch 27](./27-async-executor-pin.md)；這是把 `Pin` 從 async 場景推廣到 kernel 物件的理論橋。

五大抽象與 pin-init 拆完，你已經能**讀懂** RfL 的 sample module 了。下一章把它們拼起來：從零看一個最小 Rust kernel module 的完整結構——`module!` 巨集、`impl kernel::Module` 的 `init`（對照 `module_init`）、`pr_info!`（對照 `printk`），然後是 build 系統（`Kbuild` + `make LLVM=1`）與在 QEMU 跑 `insmod`/`rmmod` 看 `dmesg` 的完整流程（那段會誠實標「未實測，理論預期」並給正確步驟）。

→ [Ch 39 第一個 Rust kernel module](./39-first-kernel-module.md)
