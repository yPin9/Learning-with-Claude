# Ch 41 — kernel unsafe 與安全性

> **目標**：把前三章（[Ch 38–40](./38-kernel-abstractions.md)）看到的「安全封裝」翻過來看背面——**kernel 裡的 unsafe 到底跟 userland 差在哪、憑什麼比 C 安全、又還沒安全到哪**。三條主線：(1) kernel unsafe 的環境更嚴（更多 raw C 指標、中斷/鎖/preemption/原子上下文不能睡的 invariant、`// SAFETY:` 契約更關鍵）；(2) kernel crate 怎麼把 C 的生命週期/所有權規則**編碼進型別**（`ARef` 綁 refcount、guard 綁鎖、`Pin` 綁不可移動），舉一個安全抽象怎麼**防住 C 版會犯的 UAF/race**；(3) 誠實面對 RfL 現階段的限制（unstable feature、可用 API 子集、kernel 裡 panic = `BUG()`、無 unwind、alloc 失敗必須處理、bindgen 邊界）。全程用你 kernel_pwn 的攻擊視角當鏡子：這些型別擋住的，正是你平常在找的洞。

> **正確性聲明（先讀）**：本章多為**概念與型別層級論證**，斷言依 kernel `rust/kernel/` 原始碼、`Documentation/rust/` 與 RfL 官網（**2026-08，主線 `v7.2-rc5`**）。RfL API 未穩定、版本間會變（[Ch 37–40] 反覆強調）。真 kernel 行為（panic 在 kernel、原子上下文睡眠）**本機無法實測**，標「未實測，理論預期」並說明正確驗證環境。**能本機真跑**的是純 Rust 的 borrow checker / RAII / 所有權示範（`rustc 1.97.1`，WSL2）——它們展示的**編譯期保證**在 kernel 場景一模一樣，是本章論證的骨。

## 為什麼需要這個？

前三章你看到 RfL 用型別包掉一堆 C 的危險點。但如果你只停在「哦，它包得很好」，你會犯兩個錯：(1) **高估它**——以為 Rust driver「安全」到不用審，其實它底下全是 unsafe，安全性是靠人審過的 `// SAFETY:` 契約撐的，審錯了照樣爆；(2) **看不懂它憑什麼安全**——說不出「這個型別具體擋掉 C 版哪一行會犯的 UAF」，就只是背了結論。

你做 kernel_pwn，你是**攻擊方**。攻擊方看防禦最透徹的方式，是問：「這個防禦擋住了我平常用的哪個原語？我還能從哪裡進去？」這一章就是這麼寫的——每個 RfL 型別，我們問「它讓 C 版哪個 bug 寫不出來」，然後問「它的 unsafe 邊界在哪、我作為攻擊方該盯哪」。這比正面誇它安全有用得多，也才配得上你的背景。

具體回答：(1) 為什麼 kernel unsafe 比 userland unsafe 更難寫對（環境的 invariant 更多更嚴）；(2) `ARef`/guard/`Pin` 這些型別**怎麼把 C 的隱性規則變成編譯期檢查**，各擋掉哪個你打過的 bug 類；(3) RfL 現在**還不能**做什麼、痛點在哪——不吹，誠實。

## 先建立直覺：Rust 沒有消滅 unsafe，是把它「關進籠子」

一個常見誤解：「Rust driver 是 safe 的，所以沒有 unsafe。」錯。**kernel crate 內部 unsafe 多得是**——它整個工作就是呼叫 C。真相是 unsafe 的**分布**變了：

```
  C driver：unsafe 均勻抹在整份 code 上
  ┌────────────────────────────────────────────┐
  │ *ptr   kmalloc  mutex_lock  copy_from_user  │  每一行都可能是洞，
  │ list_add  kref_put  container_of  *arg      │  審計 = 審每一行
  └────────────────────────────────────────────┘

  Rust driver：unsafe 被擠進 kernel crate 的幾個封裝，driver 全 safe
  ┌─────────────────────────────┐   ┌──────────────────────────┐
  │ 你的 driver（100% safe）     │   │ kernel crate（少量 unsafe）│
  │ Mutex<T> / UserSlice /       │──▶│ 每個 unsafe 上有 // SAFETY:│
  │ ARef / KBox（都 safe API）   │   │ 契約，被人審過              │
  └─────────────────────────────┘   └──────────────────────────┘
        審計 = 審 driver 邏輯          審計 = 審那幾個 // SAFETY:
```

差別不是「有沒有 unsafe」，是「unsafe 集中在哪、要審多少」。C 你要審**整份 driver 的每一行指標操作**；Rust 你審的是**driver 的邏輯**（safe，borrow checker 已幫你擋掉記憶體錯）+ **kernel crate 那幾個封裝的 `// SAFETY:`**（一次審好、所有 driver 共用）。這是 [Ch 30](./30-security-boundary.md) 的威脅模型在 kernel 的版本：**攻擊面從「整份 code」縮到「unsafe 封裝的邊界」**。你作為攻擊方，該把火力集中在後者——這也是 RfL 安全模型的軟肋所在。

> 這章接 [Ch 17](./17-unsafe-basics.md)（unsafe 五種 superpower）、[Ch 20](./20-memory-model-ub.md)（UB/Stacked Borrows）、[Ch 30–32](./30-security-boundary.md)（威脅模型、unsafe 漏洞類、audit）。差別是場景換成 kernel，invariant 更嚴。不熟先回看。

## 第一條主線：kernel unsafe 為什麼更難寫對

userland 的 unsafe 已經難了（[Ch 17](./17-unsafe-basics.md)）。kernel 的 unsafe 在**同樣的記憶體規則**上，再疊四層 kernel 特有的 invariant——這些 borrow checker **看不到、管不到**，全靠 `// SAFETY:` 契約和你懂 kernel。

### 更多 raw pointer：你在跟 C 結構貼身肉搏

userland Rust 大多時候在 safe 世界，raw pointer 是偶爾。kernel crate 幾乎**每個抽象都貼著一個 C struct**——`struct file`、`struct mutex`、`struct device`、`struct miscdevice`。這些的記憶體佈局由 C 決定、可能被 C 就地改（[Ch 38](./38-kernel-abstractions.md) 的 `Opaque<T>`）。所以 kernel crate 裡 raw pointer 密度遠高於一般 crate，每個 deref 都是一個 `// SAFETY:` 點。

### 更嚴的 context invariant：原子上下文不能睡

這是 kernel 最要命、Rust 型別**最抓不到**的一類。kernel code 跑在不同 context，規則不同：

```
  context               能睡嗎？   配置旗標      典型場景
  ─────────────────────────────────────────────────────────
  process context       可以       GFP_KERNEL    syscall handler、你的 ioctl
  持 spinlock            不可       GFP_ATOMIC    臨界區
  中斷 handler (hardirq) 不可       GFP_ATOMIC    IRQ 處理
  softirq / tasklet      不可       GFP_ATOMIC    網路收包下半部
```

「不能睡」的意思是：不能呼叫任何**可能阻塞/排程**的東西——`mutex_lock`（會睡等鎖）、`GFP_KERNEL` 配置（會睡等記憶體回收）、`copy_from_user`（會睡等 page fault）。在原子上下文做這些 = kernel bug（`scheduling while atomic`，可能死鎖或 panic）。

**Rust 的型別系統對這個幾乎無能為力**——它不知道你現在在不在 spinlock 裡、在不在中斷裡。`Mutex::lock()` 的型別不會因為「你正持一個 spinlock」而編不過。這是 kernel Rust 誠實的限制：**記憶體安全 Rust 管得住，context 安全（原子上下文不睡）Rust 管不住，還是靠你懂 + `might_sleep()` 執行期檢查 + lockdep**。RfL 有在探索用型別表達部分 context（如某些 API 標 `# Context` 要求），但這遠不如記憶體安全那麼完整。

> 誠實標注：這是「Rust 讓 kernel 更安全」宣稱的邊界。它消滅**記憶體安全**類 bug（UAF、越界、data race）很強；對 **context/邏輯** 類 bug（原子上下文睡眠、鎖順序死鎖、中斷停用不對稱）幫助有限。別把前者的成功外推成「Rust 消滅所有 kernel bug」。

那 context 類 bug 靠什麼抓？和 C 一樣靠**執行期**機制：`might_sleep()`（標在「這函式可能睡」的地方，在原子上下文呼到會警告）、`CONFIG_DEBUG_ATOMIC_SLEEP`（開了會抓 atomic 上下文睡眠）、lockdep（`CONFIG_PROVE_LOCKING`，執行期建鎖依賴圖抓死鎖順序）。Rust driver 一樣跑在這些檢查底下——RfL **沒有**繞過它們，也沒有取代它們。所以在 kernel 開發，你的心智要分兩層：**記憶體安全交給 Rust 型別（編譯期）**、**context/鎖順序交給 kernel 的執行期 debug 工具 + 你的紀律**。這是誠實的分工，不是 Rust 全包。

### 更嚴的 aliasing：C 到處給你別名

kernel 的 C API 常常回給你「指向同一物件的多個指標」（一個 `struct device` 被多處引用、一個 buffer 被多個路徑碰），而且 C 會**就地改**這些物件。Rust 的核心規則是 `&mut` 獨占、`&` 底下的資料不可變（[Ch 03](./03-borrowing-references.md)、[Ch 20](./20-memory-model-ub.md) 的 Stacked/Tree Borrows）。「有 `&` 指著、同時有人透過裸指標寫它」在 Rust 是 **UB**——但這正是 C struct 的日常。kernel crate 的解法是把 C struct 包進 `Opaque<T>`（本質是 `UnsafeCell<MaybeUninit<T>>`，[Ch 18](./18-unsafe-advanced.md)）——`UnsafeCell` 是 Rust 裡唯一「允許透過 `&` 改底下記憶體」的型別（內部可變性）。少了它，kernel crate 根本無法合法地讓 C 就地改一個 Rust 持有的物件。

這件事本機能真跑驗證，而且**反例會被抓**。先看正確版（用 `UnsafeCell`，對照 `Opaque<T>`）：

```rust
// 本機真跑：C 就地改 Rust 持有的物件，為什麼必須 UnsafeCell（對照 Opaque<T>）
use std::cell::UnsafeCell;
struct CObject { counter: UnsafeCell<i32> }   // 對照 Opaque 包的 C struct
fn c_side_mutates(obj: &CObject) {            // 模擬 C 端拿裸指標就地改
    let p = obj.counter.get();                // *mut i32，來自 UnsafeCell（合法）
    // SAFETY: 此刻沒有 &mut 指向 counter；UnsafeCell 允許透過 & 取 *mut 並寫入。
    unsafe { *p += 1; }
}
fn main() {
    let obj = CObject { counter: UnsafeCell::new(0) };
    let (r1, r2) = (&obj, &obj);              // 多處共享借用，同時 C 就地改它
    c_side_mutates(r1);
    c_side_mutates(r2);
    println!("counter after two C-side mutations = {}", unsafe { *obj.counter.get() });
}
```

本機真跑輸出，且 `cargo +nightly miri run`（[Ch 20](./20-memory-model-ub.md) 的 UB 偵測器）**確認無 UB**：

```
counter after two C-side mutations = 2
```

現在看**反例**——把 `UnsafeCell` 拿掉、從 `&` 硬轉出 `*mut` 寫入。這正是「不懂 aliasing 的人會犯」的寫法，本機（nightly rustc）**直接編譯失敗**：

```rust
struct CObject { counter: i32 }               // 裸 i32，沒有 UnsafeCell
fn c_side_mutates(obj: &CObject) {
    let p = &obj.counter as *const i32 as *mut i32;   // 從 & 硬轉出 *mut
    unsafe { *p += 1; }                                // 透過 & 底下的記憶體寫 —— UB
}
```

本機真跑，rustc 拒編：

```
error: assigning to `&T` is undefined behavior, consider using an `UnsafeCell`
 --> src/main.rs:8:14
  |
7 |     let p = &obj.counter as *const i32 as *mut i32;
  |             -------------------------------------- casting happened here
8 |     unsafe { *p += 1; }
  |              ^^^^^^^
  = note: `#[deny(invalid_reference_casting)]` on by default
```

`invalid_reference_casting` lint（deny by default）直接抓——「assigning to `&T` is undefined behavior, consider using an `UnsafeCell`」。這說明兩件事：(1) Rust 對「`&` 底下被改」的 UB 認得很死，連 lint 都擋；(2) 所以 kernel crate **必須**用 `UnsafeCell`/`Opaque` 才能合法承載 C 的就地修改——這不是選擇，是 aliasing 規則逼出來的。kernel crate 寫錯這裡（在有別名時給出 `&mut`，或沒用 `UnsafeCell` 就讓 C 改）就是 UB，而且是 Miri 在 kernel 裡跑不了、很難抓的那種——**這也是你攻擊方可以盯的 kernel crate 內部漏洞點**。

### 結論：`// SAFETY:` 在 kernel 是生命線

上面三點加起來：kernel unsafe 的每個 `// SAFETY:` 契約要同時管住**記憶體有效性**（指標活著、對齊、初始化）+ **context**（這裡能不能睡、在不在對的鎖下）+ **aliasing**（此刻有沒有別的 `&mut`）。userland unsafe 通常只管第一項。這是為什麼 RfL 的 patch review 花大量精力在審 `// SAFETY:`——它比 userland 的更容易漏一個維度。你作為攻擊方，找 RfL driver 的洞，第一站就是這些 `// SAFETY:` 註解：**哪個契約的前提，在某條路徑上其實不成立？**

## 第二條主線：型別怎麼把 C 規則編碼成編譯期檢查

這是 RfL 的正面價值，也是你該真正理解的機制。挑三個型別，各對一個你 kernel_pwn 打過的 bug 類。

### `Pin`/`ARef` 綁 refcount → 防 UAF

C 的 refcount 物件（`kref`）UAF 怎麼發生？多 `put` 一次 → refcount 提前歸零 → 物件被釋放 → 別人手上的指標變懸空 → 用它 = UAF。你 kernel_pwn 的主戰場。

```c
/* C：refcount 靠手動配對，多一個 put 就 UAF */
struct dev *d = get_dev();       /* refcount = 1 */
use_dev(d);
put_dev(d);                      /* refcount = 0 → 釋放 */
use_dev(d);                      /* UAF！編譯器不擋，執行期才炸 */
```

RfL 的 `ARef<Device>`（[Ch 38](./38-kernel-abstractions.md)/[Ch 40](./40-rust-driver.md)）把 refcount 綁進**所有權**：`clone()` = refcount+1、`drop` = refcount-1，**編譯器保證配對**。你**無法**「用一個已經 drop 的 `ARef`」——它 drop 後，變數就失效，再碰編譯不過。這正是 borrow checker 擋 UAF 的機制，本機能真跑驗證（故意寫 use-after-drop，看它編不過）：

```rust
// 本機真跑（故意編不過）：borrow checker 把 use-after-free 擋在編譯期。
// 對照 C：free(p) 之後 *p 照樣編過、執行期才炸（UAF）。
fn main() {
    let heap = Box::new(0x41414141u32);
    let dangling: &u32 = &*heap;   // 借用 heap 指向的資料
    drop(heap);                    // 對照 kfree(p) / put_dev(d)：釋放
    println!("{:#x}", *dangling);  // 對照 *p：用已釋放的資料 —— 這行讓它編不過
}
```

本機（WSL2 `rustc 1.97.1`）真跑，`rustc` 直接拒絕編譯：

```
error[E0505]: cannot move out of `heap` because it is borrowed
 --> uaf_blocked.rs:6:10
  |
5 |     let dangling: &u32 = &*heap;
  |                          ------ borrow of `*heap` occurs here
6 |     drop(heap);
  |          ^^^^ move out of `heap` occurs here
7 |     println!("{:#x}", *dangling);
  |                       --------- borrow later used here
```

`error[E0505]`——borrow checker 看到「`heap` 還被 `dangling` 借著，你就想 `drop` 它」，直接擋。C 的等價寫法（`free(p); *p;`）編得過、跑起來才 UAF；Rust 這裡**根本產不出這個 binary**。`ARef` 在 kernel 是同一個機制：你 drop 了 `ARef`，就別想再用它指的東西——那個 UAF 原語，型別層級消失。

**攻擊方視角**：那 `ARef` 的洞在哪？在它的**建構**——`ARef::from_raw`（從 C 拿一個裸 `*mut Device` 建 `ARef`）是 unsafe，`// SAFETY:` 要求「這個指標指向一個 refcount 已 +1 的活物件」。如果 kernel crate 或某個 driver 的 unsafe code 在**refcount 沒 +1** 的情況下 `from_raw`，那 `ARef` drop 時會多 put 一次——UAF 回來了，只是縮到那一個 unsafe 點。你該盯的就是這種 `from_raw`。

### guard 綁鎖 → 防 data race / 忘鎖

C 的漏鎖 race：兩條路徑碰同一個共享資料，一條沒拿鎖（或拿錯鎖）。編譯器不擋（`d->value` 和 `d->lock` 型別上無關）。你 kernel_pwn 也常打這種——race 出一個 UAF 或 double-free 窗口。

```c
/* C：value 和 lock 型別上無關，你可以不拿鎖就寫 value，編譯器不擋 */
struct data { struct mutex lock; int value; };
d->value++;                      /* 忘了 mutex_lock！race，編譯器沉默 */
```

RfL 的 `Mutex<Inner>`（[Ch 38](./38-kernel-abstractions.md)/[Ch 40](./40-rust-driver.md)）把**資料關進鎖裡**——要碰 `Inner` 必須先 `lock()` 拿 guard，透過 guard 碰。**沒有 guard 就碰不到資料**，這不是紀律，是型別。本機真跑驗證「鎖把資料包住、繞不過去」：

```rust
// 本機真跑：鎖把資料包住 → 不拿鎖碰不到（std Mutex，形狀同 kernel Mutex）
use std::sync::Mutex;
struct Inner { value: i32 }
fn main() {
    let dev = Mutex::new(Inner { value: 0 });
    {
        let mut guard = dev.lock().unwrap();   // 拿鎖才拿得到 guard
        guard.value += 1;                      // 透過 guard 碰 value
        println!("with lock: value = {}", guard.value);
    } // guard drop → 自動 unlock

    // 想繞過鎖直接碰 value？拿不到 —— dev 是 Mutex<Inner>，Inner 藏在鎖後。
    // 下面這行若解除註解會編譯失敗（no field `value` on Mutex<Inner>）：
    // dev.value += 1;

    println!("final = {}", dev.lock().unwrap().value);
}
```

本機真跑輸出：

```
with lock: value = 1
final = 1
```

`dev.value += 1` 那行（註解掉的）**編不過**——`Mutex<Inner>` 沒有 `value` 欄位，`value` 藏在鎖後面，你**只能**先 `lock()`。C 的「忘了 `mutex_lock` 就 `d->value++`」在 Rust 結構上寫不出來：沒鎖就沒 guard，沒 guard 就碰不到 `value`。加上 guard 的 RAII（[Ch 40](./40-rust-driver.md)），連「error path 忘 unlock」也沒了。**一整類 race（忘鎖、拿錯鎖存取這塊資料）從編譯期消失。**

**攻擊方視角**：`Mutex<T>` 的洞在哪？(1) **鎖的粒度**——如果 driver 把兩個該一起保護的東西放不同 `Mutex`，Rust 擋不了「拿了 A 鎖去改本該 B 鎖保護的邏輯不變式」，那是邏輯 race，型別看不到；(2) **`SpinLock` 在錯 context**——前面說的原子上下文問題；(3) 拿到 guard 後把 guard 裡的**引用**洩漏出臨界區（Rust 的 lifetime 通常擋得住，但配合 unsafe 可能繞）。Rust 擋掉了「忘鎖」，但擋不掉「鎖設計錯」——後者是你該找的。

### `Pin` 綁不可移動 → 防 move 掉被 C 持有的物件

[Ch 38](./38-kernel-abstractions.md) 講過：kernel 物件常被 C 記住位址（嵌 `list_head`、`struct mutex` 的 lockdep、timer callback）。C 沒有 move 語意所以沒事；Rust 有 move，move 掉一個被 C 持有位址的物件 = 那個位址懸空 = list 損毀/UAF。`Pin<KBox<T>>`（[Ch 27](./27-async-executor-pin.md)/[Ch 38](./38-kernel-abstractions.md)）用型別標記「這東西釘住了、不准 move」，編譯期擋掉「把 pinned 物件 move 走」。這把「別 move 這個物件」從 C 的**沒有機制、純靠你知道**，變成 Rust 的**型別強制**。[Ch 38](./38-kernel-abstractions.md) 那個 `SelfRef` demo（`match? false`）就是這個 bug 的縮影，pin-init + `Pin` 是解法。

### 小結：三個型別 = 三類 CVE 的編譯期封殺

| C 的 bug 類（你 kernel_pwn 打的） | RfL 型別 | 怎麼擋 | 剩下的 unsafe 邊界（你該盯的） |
|---|---|---|---|
| refcount UAF（多 put/少 get） | `ARef<T>` / `Arc<T>` | clone/drop 自動配對，drop 後不能用 | `from_raw` 的 `// SAFETY:`（refcount 有沒 +1） |
| 漏鎖 / 拿錯鎖 data race | `Mutex<T>` guard | 資料關鎖裡，沒 guard 碰不到 | 鎖粒度設計、`SpinLock` context、邏輯 race |
| move 掉被 C 持有的物件 | `Pin<KBox<T>>` | 型別標記不可 move | pin-init 的 `// SAFETY:`、`Opaque` 邊界 |
| user 指標直接 deref（任意讀寫） | `UserSlice`（[Ch 40](./40-rust-driver.md)） | 碰不到裸指標，方法全 safe | `copy_*_user` 封裝的 `// SAFETY:` |

**每一行都是「編譯期封殺一類 bug」+「unsafe 縮到一個可稽核的點」。** 這就是 RfL 對你攻擊面的實際影響：正面戰場（driver 邏輯）你進不去了，你被逼到那幾個 unsafe 封裝的邊界——而那些被審得最兇。這是好的防禦：它不宣稱消滅所有 bug，它把 bug 趕到少數幾個被盯緊的地方。

### 一個具體的 driver bug：C 版 vs RfL 版

抽象講完，看一個具體的、你 kernel_pwn 一定見過的 pattern：**一個 ioctl 存了狀態、另一個 ioctl 釋放它、第三個 ioctl 用它——但釋放後沒清指標**。這是經典的 UAF driver 洞。

C 版（簡化，但這就是真實 CVE 的骨架）：

```c
/* C driver：state 存在 private_data，FREE 後忘了清 NULL，USE 就 UAF */
struct my_state { int *buf; };
static long my_ioctl(struct file *f, unsigned cmd, unsigned long arg) {
    struct my_state *st = f->private_data;
    switch (cmd) {
    case ALLOC: st->buf = kmalloc(64, GFP_KERNEL); break;
    case FREE:  kfree(st->buf); break;          /* BUG：沒有 st->buf = NULL; */
    case USE:   st->buf[0] = arg; break;        /* FREE 之後打這條 = UAF，任意寫 */
    }
    return 0;
}
```

攻擊：`ioctl(fd, ALLOC)` → `ioctl(fd, FREE)` → 用堆噴佔回那塊 → `ioctl(fd, USE, controlled)`，往被釋放的 chunk 寫。這條路你閉著眼睛都會打。C 編譯器對 `kfree(st->buf)` 後 `st->buf` 仍可用**完全沉默**。

RfL 版怎麼寫這個？狀態是 `Mutex<Inner>`，`buf` 是 `Option<KVec<u8>>`（或直接 `KVec`）。「釋放」在 Rust 不是 `kfree(ptr)`，是**把 `Option` 設回 `None`**（或讓 `KVec` drop）——**釋放和「指標還在不在」是同一件事**，型別上綁死：

```rust
// RfL 形狀（未實測，理論預期）：釋放 = 把 Option 設 None，之後 buf 就是 None，USE 拿不到
enum Cmd { Alloc, Free, Use(u8) }
fn ioctl_logic(inner: &mut Inner, cmd: Cmd) -> Result {
    match cmd {
        Cmd::Alloc  => { inner.buf = Some(KVec::new()); }
        Cmd::Free   => { inner.buf = None; }          // 釋放：KVec drop，且 buf 變 None
        Cmd::Use(v) => {
            let buf = inner.buf.as_mut().ok_or(EINVAL)?;  // buf 是 None → 回 EINVAL，不是 UAF
            buf.push(v, GFP_KERNEL)?;
        }
    }
    Ok(())
}
```

看 `Cmd::Free` 那行：`inner.buf = None` **同時**釋放記憶體（`KVec` 的 `Drop`）**和**把「指標」清成 `None`——**它們是同一個賦值，不可能只做一半**。C 的 bug（`kfree` 了但 `st->buf` 還指著）在 Rust 結構上不存在：沒有一個「釋放了但變數還指著舊記憶體」的中間狀態。`Cmd::Use` 拿 `buf` 要透過 `.as_mut().ok_or(EINVAL)?`——`buf` 是 `None` 就回 `EINVAL`，**不可能拿到懸空指標**。你那條 ALLOC→FREE→USE 的攻擊鏈，在 `USE` 這步就變成「拿到 `None`、回 `EINVAL`」，UAF 原語消失。

本機能真跑驗證這個「釋放 = 清空、之後拿不到」的形狀（用 `Option<Box>` 對照 `Option<KVec>`）：

```rust
// 本機真跑：釋放 = Option 設 None，之後 as_mut() 拿到 None，不是懸空指標
struct Inner { buf: Option<Box<[u8; 4]>> }
fn use_buf(inner: &mut Inner, v: u8) -> Result<(), &'static str> {
    let buf = inner.buf.as_mut().ok_or("EINVAL")?;   // None → 回 EINVAL
    buf[0] = v;
    Ok(())
}
fn main() {
    let mut inner = Inner { buf: Some(Box::new([0; 4])) };
    println!("ALLOC 後 USE: {:?}", use_buf(&mut inner, 0x41));  // Ok
    inner.buf = None;                                            // FREE：釋放且清空
    println!("FREE 後 USE:  {:?}", use_buf(&mut inner, 0x42));  // Err(EINVAL)，不是 UAF
}
```

本機真跑輸出：

```
ALLOC 後 USE: Ok(())
FREE 後 USE:  Err("EINVAL")
```

第二行——FREE 之後 USE 回 `Err("EINVAL")` 而不是往懸空記憶體寫。這就是「安全抽象防住 C 版會犯的 UAF」的具體長相：不是加了個檢查，是**釋放這個動作型別上就等於清空**，讓「釋放了但還指著」這個狀態根本無法表達。**攻擊方視角**：要在 RfL 版打回這個 UAF，你得讓 `Free` 和「清空」分離——而型別不給你這個機會（除非 driver 作者手動用 unsafe 存了一份裸指標繞過 `Option`，那又回到「盯 unsafe 邊界」）。

## 第三條主線：誠實面對 RfL 的限制與痛點

吹 RfL 安全很容易，但這門課對你沒用。這一節講它**現在還做不到什麼**——這些是真實摩擦，也是你判斷「該不該用 RfL」的依據。

### panic 在 kernel = `BUG()`，而且沒有 unwind

userland Rust panic 會 unwind stack、跑 destructor、通常只掛掉一個 thread。**kernel 裡完全不同**：kernel Rust 用 `panic = "abort"`（無 unwind），panic 直接走 kernel 的 `panic()`/`BUG()`——輕則 oops 殺掉當前 task，重則整台機器掛（尤其在持鎖或中斷時 panic）。所以：

- **`unwrap()`/`expect()`/`panic!` 在 kernel 是禁忌**（[Ch 39](./39-first-kernel-module.md) 踩雷 6 提過）。你在 RfL code 幾乎看不到它們——一切用 `?` 傳播 `Result`。
- **陣列越界 index、整數溢位（開檢查時）、`assert!`** 這些會 panic 的操作要格外小心。RfL 大量用回 `Result`/`Option` 的 API（`get()` 而非 `[]`）避開。
- **沒有 unwind 意味著沒有「panic 後清理」**——userland 靠 unwind 跑 destructor 釋放資源，kernel abort 不跑，所以「panic 安全」在 kernel 是「根本別 panic」。

這是誠實的限制：Rust 的一個安全網（panic 而非 UB）在 kernel 被削弱了——panic 本身就是嚴重事件。RfL 的策略是「盡量別 panic」，靠型別把錯誤變成 `Result`。

### alloc 失敗必須處理（fallible everywhere）

[Ch 38](./38-kernel-abstractions.md) 講的 fallible allocation 是**限制也是負擔**：你不能用方便的 `Box::new`/`vec!`/`String::from`（那些 infallible、失敗 abort），得用 `KBox::new(x, GFP_KERNEL)?`、`KVec::push(x, GFP_KERNEL)?`——每個配置多一個 `GFP_*` 參數、多一個 `?`。好處是 kernel 不會因配置失敗 abort；代價是 code 比 userland Rust 囉嗦，且**不能用大半個 `alloc`/`std` 生態**（它們假設 infallible alloc）。這是 `no_std` + fallible 的雙重約束（[Ch 22](./22-no-std.md)）。

### 只能用 API 子集 + unstable feature

RfL 依賴一票**unstable Rust feature**（`allocator_api`、`const` 相關、`asm` 等），所以綁特定 rustc 版本（[Ch 37](./37-rust-for-linux-overview.md)/[Ch 39](./39-first-kernel-module.md) 的版本約束）。而且：

- **能用的 `kernel` crate API 是 C API 的一個子集**——不是每個 C kernel 函式都有 Rust 安全封裝。要用還沒封裝的，你得自己寫 `unsafe` binding 呼叫 + 自己寫 `// SAFETY:`（等於暫時回到 C 的危險度，只是局部）。RfL 的抽象**還在長**，覆蓋面逐版本擴大但遠未完整。
- **API 未穩定**——你這版寫的 `Mutex`/`MiscDevice`/`UserSlice` 簽章，下版可能改（[Ch 40](./40-rust-driver.md) 的 `read`→`read_iter` 就是例子）。out-of-tree module 尤其痛（[Ch 39](./39-first-kernel-module.md)）。

### bindgen 邊界：型別對映不完美

RfL 用 `bindgen` 從 C header 生 raw Rust binding（[Ch 37](./37-rust-for-linux-overview.md) 架構圖第 2 層）。這條自動生成的邊界不完美：C 的某些型別（複雜 macro、bitfield、匿名 union、`__packed` 佈局）bindgen 處理得不好或不處理，要手動補。而且 bindgen 生的東西**全是 unsafe**——它只是把 C 型別搬過來，語意契約（誰擁有、能不能 NULL、生命週期）bindgen 不懂，全靠上層的 `kernel` crate 手寫 `// SAFETY:` 補回來。**bindgen 邊界是「C 的隱性契約還沒被型別化」的地帶**，也是 kernel crate 最容易出錯的層。

### 誠實總評

RfL 現在（2026-08、主線 `v7.2-rc5`）能讓你**寫一個記憶體安全的 driver**，但代價是：綁 unstable rustc、API 未穩定會變、只能用抽象子集、囉嗦的 fallible alloc、panic 是重罪、context 安全還是靠你懂。它**不是** C 的即插即用替代，它是「新寫的葉子程式（driver）」的一個更安全的選項（[Ch 37](./37-rust-for-linux-overview.md) 的定位）。把它當「消滅所有 kernel bug 的銀彈」是誤解；把它當「把記憶體安全類 bug 趕到少數可審 unsafe 點的工程」是準確的。

## 對比與取捨

| 面向 | C kernel | RfL kernel Rust | 誰贏 |
|---|---|---|---|
| 記憶體安全（UAF/越界/race） | 靠紀律 + KASAN/KCSAN 執行期抓 | 型別**編譯期**擋大部分 | Rust（顯著） |
| 原子上下文不睡 | 靠懂 + `might_sleep()`/lockdep | 型別幾乎管不到，一樣靠懂 | 平手（都靠人） |
| 鎖順序死鎖 | lockdep 執行期 | Rust 擋忘鎖，擋不了順序錯 | 略偏 C 生態成熟 |
| panic/錯誤 | 回負 errno，不 unwind | `Result`/`?`，panic=`BUG()` 更嚴 | Rust（錯誤傳播）/ C（panic 較不致命）各半 |
| API 覆蓋 | 完整（就是 kernel 本身） | 子集，還在長 | C（現階段） |
| 生態成熟度 | 數十年 | 幾年，unstable | C |
| 新 driver 的 bug 率 | 高（歷史 CVE 為證） | 記憶體類低很多 | Rust |

取捨一句話：**RfL 用「綁 unstable、API 未穩、覆蓋子集、寫法囉嗦」換「記憶體安全類 bug 編譯期消失」**。對新寫的、風險高的 driver，這筆交易划算；對要用冷門 C API、或要極致穩定 toolchain 的場景，現階段 C 還是務實選擇。這正是 kernel 社群「不重寫、只給新 driver 選項」的理由。

## 踩雷集錦

1. **以為「Rust driver 是 safe 的所以不用審」**：錯。它底下全是 unsafe，安全性靠 kernel crate 的 `// SAFETY:` 契約被人審過。審錯一個契約（前提在某路徑不成立）照樣 UB/UAF。差別是**要審的 unsafe 集中且少**，不是**沒有**。你作為攻擊方，該盯的正是這些封裝的 unsafe 邊界，尤其 `*_from_raw`。

2. **把「記憶體安全」外推成「所有 kernel bug 消失」**：Rust 強在記憶體安全類（UAF/越界/data race）。它對**原子上下文睡眠、鎖順序死鎖、邏輯 race、中斷停用不對稱**這些幫助有限——型別看不到 context。別宣稱 RfL 消滅所有 kernel bug，那不誠實也不準。

3. **在 kernel Rust 用 `unwrap()`/`panic!`**：kernel panic = `BUG()`，無 unwind，持鎖/中斷時 panic 可能整台掛。永遠用 `?` 傳播 `Result`、用 `get()` 而非 `[]`、避開會 panic 的操作。這跟 userland Rust「panic 只掛一個 thread」的直覺相反——別把 userland 習慣帶進來。

4. **忘了 fallible alloc 是硬約束，想用 `Box::new`/`vec!`**：kernel 裡配置必須可失敗（回 `Result`），用 `KBox::new(x, GFP_KERNEL)?`/`KVec`，不是 std 的 infallible 版。連帶不能用假設 infallible alloc 的大半 `std`/`alloc` 生態。這是 `no_std` + fallible 的雙重限制，不是 RfL 找麻煩。

5. **把 API 當穩定的背**：RfL API 未穩定、逐版本變（`read`→`read_iter` 是活例）。對著你那棵 kernel 的 `rust/kernel/` 原始碼和對應版 rustdoc 寫，別背某一版的簽章。out-of-tree module 還多一層「rustc 版 + kernel crate 版」匹配的痛。

6. **以為 `Mutex<T>` 擋掉所有 race**：它擋「忘鎖/沒鎖碰這塊資料」。它**擋不掉**：鎖粒度設計錯（兩個該一起保護的放不同鎖）、`SpinLock` 用在會睡的 context、拿了鎖但邏輯不變式跨臨界區被破壞。這些是你攻擊方該找的——Rust 把低階記憶體 race 收了，高階邏輯 race 還在。

## 進階：再往深一層

- **讀一個真實 `// SAFETY:` 並試著推翻它**：挑 [Ch 40](./40-rust-driver.md) 的 `read_raw`（`copy_from_user` 那個）或 `miscdevice.rs` open shim 的 `into_foreign`，讀它的 `// SAFETY:`，問「這前提在什麼路徑會不成立？」——這是 RfL patch reviewer 和你攻擊方共用的技能（[Ch 32](./32-audit-unsafe.md) 的 audit unsafe 在 kernel 版）。多數契約成立，但練這個眼力是找 RfL 洞的基本功。
- **`Opaque<T>` 與 aliasing 的深水**：kernel crate 用 `Opaque<T>`（`UnsafeCell<MaybeUninit<T>>`，[Ch 18](./18-unsafe-advanced.md)）包 C struct，讓「C 就地改、多處持指標」在 Rust 型別下合法。理解它為什麼**必須**是 `UnsafeCell`（否則多個 `&` 下 C 改它 = UB），你就懂了 kernel crate 怎麼在 Rust 的 aliasing 規則和 C 的現實之間搭橋。讀 `rust/kernel/types.rs` 的 `Opaque`。
- **context 型別化的前沿**：RfL 有在探索用型別/marker 表達部分 context 約束（例如某些操作要求 `# Context: Process context`，或 klint 之類的外部 lint 檢查 atomic-context 睡眠）。這是「把 context 安全也搬進編譯期」的方向，但還不成熟。關注 RfL 郵件列表和 klint 專案能看到這條線的進展。
- **面試/研究角度**：能講清楚「kernel unsafe 比 userland 多管哪兩個維度（context、aliasing）」「`ARef`/guard/`Pin` 各編碼了 C 的什麼規則、各擋哪類 CVE」「為什麼 RfL 不宣稱消滅所有 kernel bug（context/邏輯類）」「panic 在 kernel 為什麼是重罪」「攻擊 RfL driver 該盯哪（unsafe 封裝的 `// SAFETY:`、`from_raw`、鎖粒度）」，就是真懂 RfL 的安全模型——防守和攻擊兩面都懂。

## 動手練習

1. **本機重現 UAF 編譯失敗 + 對 C**：跑本章 `uaf_blocked.rs`，確認 `error[E0505]`。然後寫等價 C（`int *p = malloc(4); *p = 0x41414141; free(p); printf("%x", *p);`），用 `gcc -fsanitize=address` 編譯執行，看 ASan 抓到 heap-use-after-free。對照：Rust **編不出** binary，C **編得出、跑起來才被 ASan 抓**。這是「編譯期 vs 執行期」防禦的實感——也是你 kernel_pwn 為什麼 C kernel 有得打、Rust 這塊沒得打的原因。

2. **本機試 `Mutex` 的繞過失敗**：把本章 `lock_binds_data.rs` 裡註解掉的 `dev.value += 1;` 解除註解，`rustc` 編一次，讀它的錯誤（`no field value on type Mutex<Inner>`）。這證明「資料關鎖裡、沒 guard 碰不到」是型別強制。再想：C 的 `d->value++`（忘鎖）為什麼編得過？因為 `value` 和 `lock` 在 C 型別上無關。

3. **審一個 `// SAFETY:`**：打開 [Ch 40](./40-rust-driver.md) 引的 `read_raw` 或 open shim，讀它的 `// SAFETY:` 註解，用自己的話寫出「這個 unsafe 依賴哪幾個前提、每個前提由誰保證」。再問「如果 driver 作者用某種方式讓某個前提不成立，會發生什麼」。做完你會發現 RfL 的安全是**條件式**的——條件由審過的契約撐著，不是無條件。

4. **紙上比一個 C driver bug 的三種結局**：拿一個你熟的 C driver bug（漏鎖 race / refcount UAF / user 指標直接 deref），寫下它在三種情況的結局：(a) C 版——編過、生產爆炸、你 kernel_pwn 打它；(b) RfL 版寫在 safe code——編不過或型別擋掉；(c) RfL 版寫在某個 unsafe 封裝的 `// SAFETY:` 漏洞裡——縮到一個可審的點但仍可能爆。這個三分法是本章的核心，也是你評估「Rust 到底幫了多少」的框架。

## 本章重點整理

- kernel unsafe 比 userland 難寫對：同樣的記憶體規則上再疊 **context**（原子上下文不能睡，Rust 型別幾乎管不到）、**更多 raw C 指標**（每個抽象貼一個 C struct）、**更嚴 aliasing**（C 到處給別名）。每個 `// SAFETY:` 契約要同時管記憶體+context+aliasing——這是 RfL review 的重心，也是攻擊方該盯的邊界。
- Rust 沒消滅 unsafe，是把它從「均勻抹在整份 driver」**集中進 kernel crate 的少數封裝**，每個有審過的 `// SAFETY:`。攻擊面從「整份 code」縮到「unsafe 封裝邊界」（尤其 `*_from_raw`、鎖粒度）。
- 三個型別編碼三類 C 規則、封殺三類 CVE：`ARef`/`Arc` 綁 refcount（防多 put UAF）、`Mutex` guard 綁鎖（資料關鎖裡，防忘鎖 race）、`Pin` 綁不可移動（防 move 掉被 C 持有的物件）。加 `UserSlice`（[Ch 40](./40-rust-driver.md)）防 user 指標直接 deref。本機用 borrow checker（E0505）/RAII/所有權真跑驗證了這些**編譯期保證**的形狀。
- 誠實限制：panic 在 kernel = `BUG()` 且無 unwind（`unwrap` 是禁忌）、fallible alloc 是硬約束（不能用 infallible `Box`/`vec!`）、只能用 API 子集 + 綁 unstable rustc、API 未穩定會變、bindgen 邊界是「C 契約還沒型別化」的地帶。**Rust 強在記憶體安全類 bug，對 context/邏輯類幫助有限**——別外推成銀彈。
- 攻擊方框架：同一個 driver bug 三種結局——C 版編過生產爆炸、RfL safe code 編不過/擋掉、RfL 某個 unsafe `// SAFETY:` 漏洞裡縮到可審點但仍可能爆。RfL 的價值是把第一種趕成第三種（少數被盯緊的點），不是讓 bug 消失。

## 自我檢核

- [ ] 不看筆記，能說出 kernel unsafe 比 userland unsafe 多管哪兩個維度（context 不睡、C 的 aliasing），並解釋為什麼 Rust 型別對「原子上下文不能睡」幾乎無能為力。
- [ ] 能各舉一個型別（`ARef`/`Mutex` guard/`Pin`）說清它編碼了 C 的什麼規則、擋掉你 kernel_pwn 打過的哪類 bug，以及它剩下的 unsafe 邊界在哪（攻擊方該盯哪）。
- [ ] 能解釋「Rust 沒消滅 unsafe，是把它集中進 kernel crate」對攻擊面的具體影響，以及為什麼 `// SAFETY:` 契約是 RfL 安全的生命線。
- [ ] 能誠實列出 RfL 現階段至少三個限制（panic=BUG、fallible alloc、API 子集/unstable、bindgen 邊界），並說明為什麼不能宣稱它消滅所有 kernel bug。
- [ ] 能用「C 編過爆炸 / RfL safe 擋掉 / RfL unsafe 漏洞縮到可審點」三分法，分析一個具體 driver bug（漏鎖/UAF/user 指標 deref）。

## 延伸閱讀

### 官方文件 / 一手來源

- **kernel 樹 `Documentation/rust/`（[docs.kernel.org/rust](https://docs.kernel.org/rust/index.html)）** — RfL 安全模型權威
  - **讀哪裡**：`general-information.rst`（safe/unsafe 邊界、bindings vs abstractions 的分工）、`coding-guidelines.rst`（`// SAFETY:`/`# Safety` 的寫法規範——本章「契約」的官方要求）。
  - **學到什麼**：RfL 官方怎麼定義「哪些是 safe、哪些是 unsafe、契約怎麼寫」——本章第一、三主線的一手依據。
  - **前提**：讀完本章 + [Ch 17](./17-unsafe-basics.md) 的 unsafe 基礎；想看官方怎麼講安全邊界的下一步。

- **kernel 樹 `rust/kernel/types.rs`（`Opaque`）與 `rust/kernel/sync/`（`ARef`/`Arc`/`Mutex`）** — 型別如何編碼規則
  - **讀哪裡**：`types.rs` 的 `Opaque<T>`（為什麼是 `UnsafeCell`）、`ARef`/`AlwaysRefCounted` 的定義與 `// SAFETY:`；`sync/lock/` 的 guard 實作。
  - **學到什麼**：本章第二主線（`ARef`/guard/`Pin` 怎麼把 C 規則變型別）的真實原始碼——尤其 `from_raw` 那些 unsafe 建構的契約，正是攻擊方該讀的。
  - **前提**：懂本章三個型別的概念 + [Ch 18](./18-unsafe-advanced.md) 的 `UnsafeCell`/`MaybeUninit`。

### 論文 / 研究

- **[《Rust for Linux》相關的 kernel 記憶體安全數據]（Google Security Blog / Android 安全報告）** — memory-safety 的實證
  - **讀哪裡**：Google 關於 Android 引入 Rust 後記憶體安全漏洞比例下降的報告（本課 [Ch 37](./37-rust-for-linux-overview.md) 引過的那類數據）。
  - **學到什麼**：「Rust 消滅記憶體安全類 bug」不是宣稱而是有量化證據的——支撐本章「記憶體安全類 Rust 強、其他類有限」的誠實區分。
  - **前提**：[Ch 37](./37-rust-for-linux-overview.md) 的 RfL 動機；想看「到底幫了多少」的實證。

### 部落格 / 技術文章

- **[LWN.net 的 Rust-for-Linux 系列報導](https://lwn.net/Kernel/Index/#Development_tools-Rust)** — RfL 進展與爭議的最佳追蹤
  - **讀哪裡**：搜 LWN 的「Rust」標籤，尤其關於 API 穩定性爭議、`no_std`/panic 策略、context 型別化（klint）討論的文章。
  - **學到什麼**：本章「限制與痛點」那節的活來源——RfL 社群真實在吵什麼、哪些限制在被解決、哪些是根本取捨。LWN 是 kernel 開發最權威的中立報導。
  - **前提**：懂本章的限制清單；想追 RfL 現況與未來（也是 [Ch 42](./42-ecosystem-future.md) 的素材）。

你現在既懂 RfL 怎麼用型別把記憶體安全類 bug 擋在編譯期，也誠實知道它的限制與剩下的 unsafe 邊界。最後一章跳出「怎麼寫」，看 RfL 與 Rust 在系統程式的**生態與未來**：主線進度到哪、哪些真實 driver/子系統已經在用 Rust、Android/Windows 的 kernel Rust、Rust 在系統程式的整體版圖，以及你學完這門課該往哪走。

→ [Ch 42 生態與未來](./42-ecosystem-future.md)
