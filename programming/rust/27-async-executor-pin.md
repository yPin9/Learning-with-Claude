# Ch 27 — async 原理二：executor/Waker/Pin

> **目標**：填掉 Ch 26 刻意跳過的兩個黑盒子。學完你能回答：真正的 executor 怎麼靠 **`Waker`** 掛起執行緒而不燒 CPU、`Context` 為什麼要傳進 `poll`、以及自我引用的 async 狀態機為什麼**移動會壞**、`Pin`/`Unpin` 怎麼保證它不被移動。這章是 async 原理的重點章——`Pin` 是 Rust 型別系統裡最讓人卡的一塊，而卡的原因幾乎都是「不知道它在保護什麼」。我們從「它在保護什麼」開始。

> **環境**：`rustc 1.97.1` (stable)，x86-64 Linux（WSL2）。全程只用 `std`（`std::task::Wake`、`std::pin`、`std::sync::mpsc`）。每段 code 都在此環境真跑過，輸出照貼，包括一段**故意觸發 UB** 的示範（會印出 kernel 給的 `Bad address`）。

## 為什麼需要這個？

Ch 26 的 `block_on` 有個難看的地方：poll 到 `Pending` 就**忙等**——一個 `loop` 死命重 poll，把一整顆 CPU 燒到 100%。真正的 async runtime 一秒鐘可能只 poll 幾次，CPU 大部分時間在睡。差別在哪？

差在**誰負責喚醒**。忙等的意思是「我不知道你什麼時候好，所以我一直問」。有效率的做法是「你好了**主動叫我**，在那之前我去睡」。這正是你 epoll 的直覺：`epoll_wait()` 會把 thread 掛起（進 kernel 睡），fd 就緒時 kernel 才喚醒它。Rust async 需要一個對應機制，讓「資源就緒」能反向通知 executor「該 poll 這個 future 了」。這個機制就是 **`Waker`**。

第二個坑更隱蔽。Ch 26 裡我們每次都寫 `unsafe { Pin::new_unchecked(&mut fut) }` 才能 poll，還說「先照做」。為什麼 `poll` 的 receiver 是 `Pin<&mut Self>` 而不是普通的 `&mut Self`？因為 async 狀態機**可能自我引用**——它捕獲一個指向自己另一個欄位的參考（跨 `.await` 的 `&`）。這種結構一旦被移動（move），內部那根指標就變成懸空的。`Pin` 是型別系統層級的「這東西被固定住了，不准移動」的承諾。這章把這件事從頭講清楚，包括**真的跑一段移動它、然後看它爆炸**的 code。

## 先建立直覺：Waker 是一張「叫我」的名片

```
   ┌──────────┐   poll(cx)    ┌──────────────┐
   │ executor │ ───────────►  │  Future      │
   │          │               │  「還沒好」   │
   │          │ ◄─────────── │  存下 cx 裡的 │
   │  去睡覺   │   Pending     │  Waker 名片   │
   └──────────┘               └──────────────┘
        ▲                            │
        │                            │ 資源就緒（thread / epoll / timer）
        │       waker.wake()         ▼
        └──────────────────  ┌──────────────┐
          「該 poll 它了！」   │ 底層 I/O 完成 │
                             └──────────────┘
```

`Context` 就是一個信封，裡面裝一張 `Waker`——一張「好了打這支電話」的名片。executor `poll` future 時把這張名片遞進去。future 若還沒好（`Pending`），就把名片**收起來存好**，然後告訴 executor「你先去睡」。等底層資源就緒（背景 thread 跑完、epoll 回報 fd 就緒、timer 到期），那邊的 code 拿出存好的名片打電話（`wake()`），executor 被叫醒，回來重 poll 這個 future。

CPU 在等待期間是**真的在睡**的，不是忙等。這就是「async 為什麼能在單執行緒上撐住上萬條 idle 連線而不燒 CPU」的機制。

## Waker：手刻一個會掛起的 executor

我們把 Ch 26 的忙等 `block_on` 升級成「poll 到 Pending 就 park，等 wake 才回來」。造 `Waker` 有兩條路：底層的 `RawWaker`/`RawWakerVTable`（Ch 26 用過的黑盒子，手動填函式指標表），和高層的 `std::task::Wake` trait（`Rust 1.51+` 穩定，用 `Arc<T>` 自動幫你造 vtable）。生產 code 用後者，我們也用後者。

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::thread;
use std::time::Duration;

// 一個「定時器」Future：spawn 一個 thread 睡 dur，時間到就 wake()
struct TimerFuture {
    state: Arc<Mutex<TimerState>>,
}
struct TimerState {
    completed: bool,
    waker: Option<Waker>,   // 存下 executor 遞進來的名片
}

impl TimerFuture {
    fn new(dur: Duration) -> Self {
        let state = Arc::new(Mutex::new(TimerState { completed: false, waker: None }));
        let thread_state = state.clone();
        thread::spawn(move || {
            thread::sleep(dur);                      // 模擬底層 I/O 需要時間
            let mut s = thread_state.lock().unwrap();
            s.completed = true;
            if let Some(w) = s.waker.take() {
                println!("[timer] fired, waking executor");
                w.wake();                            // 打電話：叫 executor 回來 poll
            }
        });
        TimerFuture { state }
    }
}

impl Future for TimerFuture {
    type Output = ();
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        let mut s = self.state.lock().unwrap();
        if s.completed {
            Poll::Ready(())
        } else {
            // 還沒好：存下 waker，資源就緒時才叫醒我，而不是忙等
            s.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}

// 用 Wake trait 造 Waker：wake() 把「該 poll 了」的訊號塞回 ready channel
struct MyWaker {
    tx: mpsc::Sender<()>,
}
impl Wake for MyWaker {
    fn wake(self: Arc<Self>) {
        self.tx.send(()).ok();
    }
}

fn block_on<F: Future>(mut fut: F) -> F::Output {
    let (tx, rx) = mpsc::channel();
    let waker: Waker = Arc::new(MyWaker { tx }).into();   // Arc<impl Wake> -> Waker
    let mut cx = Context::from_waker(&waker);
    let mut fut = unsafe { Pin::new_unchecked(&mut fut) };
    loop {
        match fut.as_mut().poll(&mut cx) {
            Poll::Ready(v) => return v,
            Poll::Pending => {
                println!("[executor] Pending, parking until wake()");
                rx.recv().unwrap();   // 阻塞在 channel 上（thread 進 kernel 睡），不是忙等！
                println!("[executor] woken, re-polling");
            }
        }
    }
}

fn main() {
    println!("start");
    block_on(TimerFuture::new(Duration::from_millis(300)));
    println!("timer done");
}
```

真跑，輸出：

```
start
[executor] Pending, parking until wake()
[timer] fired, waking executor
[executor] woken, re-polling
timer done
```

對照 Ch 26 的忙等版，差別是那句 `rx.recv().unwrap()`：executor poll 到 `Pending` 後**阻塞在 channel 上**——這條 thread 進 kernel 睡，CPU 空出來——直到背景 timer thread 呼叫 `wake()`（往 channel 送一個訊號）才被喚醒。整個 300ms 期間 CPU 幾乎不動。這就是真 executor 的骨架。生產級 runtime（Tokio）把「背景 thread 睡」換成「epoll 等 fd 就緒」，把單一 future 換成「一個 task 佇列」，但**核心迴圈就是這個**：poll → Pending 就 park → wake 喚醒 → 重 poll。

> **`Context` 為什麼是一個 struct 而不是直接傳 `Waker`？** 因為它是**可擴充的信封**。目前它主要裝 `Waker`，但 Rust 團隊保留了在不破壞 API 的前提下往 `Context` 加東西的空間（例如未來的 async runtime 元資料）。這是「留擴充餘地」的 API 設計，不是本章重點，知道就好。

## Pin：async 狀態機為什麼不能亂搬

現在講 `Pin`。要理解它，先得看到**問題本身**：自我引用的結構被移動會爆炸。

### 問題：移動打斷自我引用

async fn 若在 `.await` 之前借了一個區域變數、`.await` 之後還用它，那個 `&` 就會被存進狀態機——而它指向的目標**也在同一台狀態機裡**。這叫自我引用（self-referential）：struct 的一個欄位是指向自己另一個欄位的指標。

我們手刻一個自我引用 struct，然後移動它，看內部指標怎麼變懸空：

```rust
// 自我引用 struct：b 是一根指向自己 a 欄位位址的裸指標
struct SelfRef {
    a: String,
    b: *const String,   // 指向自己的 a
}

impl SelfRef {
    fn new(txt: &str) -> Self {
        let mut s = SelfRef { a: txt.to_string(), b: std::ptr::null() };
        s.b = &s.a;      // b 指向 a（注意：此時 s 還在 new 的 stack frame 上）
        s                // return 會把 s move 出去 —— b 已經指錯了！
    }
}

fn main() {
    let s1 = SelfRef::new("hello");
    println!("s1.a addr = {:p}", &s1.a);
    println!("s1.b      = {:p}", s1.b);   // 已經和 &s1.a 不同：new 的 return 就 move 過一次

    // 再把 s1 move 到 s2：整個 struct 搬到新位址，但 b 還指向舊位址！
    let s2 = s1;
    println!("--- after move ---");
    println!("s2.a addr = {:p}", &s2.a);  // a 搬到新位址
    println!("s2.b      = {:p}", s2.b);   // b 還是舊位址 -> 懸空
    unsafe {
        println!("s2.a  value       = {}", s2.a);
        println!("*s2.b value (UB!) = {}", *s2.b);  // deref 懸空指標：UB
    }
}
```

真跑（`cargo run`），輸出：

```
s1.a addr = 0x7ffd6c914908
s1.b      = 0x7ffd6c914820
--- after move ---
s2.a addr = 0x7ffd6c914970
s2.b      = 0x7ffd6c914820
s2.a  value       = hello
*s2.b value (UB!) = 
thread 'main' (273315) panicked at library/std/src/io/stdio.rs:1166:9:
failed printing to stdout: Bad address (os error 14)
```

拆解這個輸出，它就是 `Pin` 存在的全部理由：

- **`s1.b` 從一開始就不等於 `&s1.a`**：`new()` 裡設好 `b = &s.a` 後，`return s` 又把整個 struct move 出去（到 `main` 的 `s1`），`b` 立刻指向已經失效的 `new` stack frame。這說明自我引用結構**連從函式回傳都不能安全做**。
- **`s2` 這次 move 後**：`a` 搬到 `0x...4970`，但 `b` 還是舊的 `0x...4820`——它指向 `s1` 曾經在的位址，那裡現在是垃圾。
- **`*s2.b` 是 UB**：deref 一根懸空指標。這次 kernel 給了我們 `Bad address (os error 14)`（`EFAULT`）——`b` 指向的位址已經不是有效映射的記憶體。這是**真的觀察到的 UB 後果**，不是理論。（UB 的行為不保證每次都這樣；換個 allocator 或優化等級，它可能靜默印出垃圾字串——這正是 UB 可怕的地方，見 [Ch 20](./20-memory-model-ub.md)。）

async 狀態機的自我引用比這更真實：`async { let x = ...; let r = &x; foo().await; use(r); }` 裡，`r` 指向 `x`，兩者都在狀態機 enum 裡。如果 executor 把這個 future 從一個位址 move 到另一個（例如從 stack 搬進 `Box`，或在 task 佇列裡搬動），`r` 就懸空——一模一樣的 bug，只是編譯器生的。

### 解法：Pin 是「不准移動」的型別承諾

```
   一般值            Pin<Box<T>>（T: !Unpin）
   ┌──────┐          ┌──────────────────┐
   │  可自 │          │ 值被釘在 heap 上  │
   │由移動 │          │ 位址固定不變      │
   │       │          │ 安全 API 拿不到    │
   │       │          │ &mut T 來 move 它 │
   └──────┘          └──────────────────┘
```

`Pin<P>`（`P` 是某種指標，如 `Box<T>` 或 `&mut T`）是一層包裝，語意是：**它指向的 `T` 從此不會再被移動，直到它被 drop**。有了這個保證，自我引用才安全——位址固定，內部指標就永遠有效。

關鍵：`Pin` 不是靠執行期檢查，而是靠**型別系統 + 你（unsafe 作者）的承諾**。要拿到被 pin 的 `&mut T` 去 move 它，你得走 `unsafe { Pin::get_unchecked_mut }`——safe API 不給你這條路。

看正確做法：把自我引用結構 pin 在 heap 上（`Box::pin`），位址固定後才設內部指標：

```rust
use std::marker::PhantomPinned;
use std::pin::Pin;

struct SelfRef {
    a: String,
    b: *const String,
    _pin: PhantomPinned,   // 標記：讓這型別 !Unpin，一旦 pin 就不能再 move
}

impl SelfRef {
    fn new(txt: &str) -> Pin<Box<Self>> {
        let s = SelfRef { a: txt.to_string(), b: std::ptr::null(), _pin: PhantomPinned };
        let mut boxed = Box::pin(s);          // 先 pin 在 heap 上，位址從此固定
        let a_ptr: *const String = &boxed.a;
        unsafe {
            let mut_ref = Pin::as_mut(&mut boxed);
            // 現在 a 的位址已固定，設 b 指向它才安全
            Pin::get_unchecked_mut(mut_ref).b = a_ptr;
        }
        boxed
    }
}

fn main() {
    let s = SelfRef::new("hello");
    println!("a addr = {:p}", &s.a);
    println!("b       = {:p}", s.b);              // b == &a，一致
    println!("a  = {}", s.a);
    println!("*b = {}", unsafe { &*s.b });        // 安全 deref：位址被 Pin 固定住了
    println!("ok, address stable, self-ref valid");
}
```

真跑，輸出：

```
a addr = 0x61bbfeff1ae0
b       = 0x61bbfeff1ae0
a  = hello
*b = hello
ok, address stable, self-ref valid
```

這次 `a addr == b`，deref 安全。差別就在：值被 `Box::pin` 釘在 heap 上，位址從此不變，我們才在那之後設內部指標。

## `Unpin`：大多數型別不甩 Pin

看到這你可能會問：那我平常寫 `i32`、`String`、`Vec` 也要煩惱 Pin 嗎？不用。`Unpin` 這個 auto trait 就是來標記「這型別**沒有**自我引用，move 它完全安全，Pin 對它形同虛設」的。

`Unpin` 是 **auto trait**：絕大多數型別自動實作它（`i32`、`String`、`Vec<T>`、你自己的普通 struct……）。對 `T: Unpin`，`Pin<&mut T>` 可以無條件退化回 `&mut T`（`Pin::get_mut`），因為根本不需要保護——move 它沒問題。只有真正自我引用的型別（async 狀態機、我們上面用 `PhantomPinned` 標記的 `SelfRef`）才是 `!Unpin`，才真的受 Pin 約束。

證明 `!Unpin` 型別無法被 safe 地 move 出 Pin：

```rust
use std::marker::PhantomPinned;
use std::pin::Pin;

struct SelfRef { a: String, _pin: PhantomPinned }

fn main() {
    let s = SelfRef { a: String::from("x"), _pin: PhantomPinned };
    let pinned: Pin<Box<SelfRef>> = Box::pin(s);
    // 想把 pinned 裡的值搬出來 -> 對 !Unpin 型別，Pin::into_inner 不給用
    let _moved: SelfRef = *Pin::into_inner(pinned);
}
```

編譯，`rustc` 直接擋（真實輸出，節錄）：

```
error[E0277]: `PhantomPinned` cannot be unpinned
  --> src/main.rs:10:44
   |
10 |     let _moved: SelfRef = *Pin::into_inner(pinned);
   |                            --------------- ^^^^^^ within `SelfRef`, the trait `Unpin` is not implemented for `PhantomPinned`
   |
   = note: consider using the `pin!` macro
           consider using `Box::pin` if you need to access the pinned value outside of the current scope
note: required by a bound in `Pin::<Ptr>::into_inner`
```

`Pin::into_inner`（把值 move 出來）要求 `T: Unpin`。`SelfRef` 因為含 `PhantomPinned` 而是 `!Unpin`，所以編譯器拒絕。**這就是 Pin 的保護在型別層級生效的樣子**：不是執行期報錯，是根本編不過。這也解釋了 Ch 26 為什麼每次 poll 前要 `Pin::new_unchecked`——`poll` 的 receiver 是 `Pin<&mut Self>`，因為 future 可能 `!Unpin`，語言強制你在 poll 之前先把它 pin 住，向編譯器承諾「我不會再 move 它」。

## 連結：這一切怎麼組成 Tokio 與 epoll

把三章串起來：

```
  你寫的 async fn ── rustc ──► 狀態機 (可能 !Unpin，Ch 26)
                                      │
                       executor 把它 Box::pin 住（固定位址，本章）
                                      │
                       executor 反覆 poll，遞進 Context{Waker}（本章）
                                      │
              Pending ──► future 存下 Waker，executor park
                                      │
        底層 I/O 就緒 ──► wake() ──► executor 回來重 poll
                                      │
                    Tokio：底層那個「park / 等 I/O 就緒」
                    用 epoll (Linux) / io_uring 實作（Ch 28）
```

本章的 `TimerFuture` 用「背景 thread 睡完 wake」模擬「底層資源就緒」。生產級 runtime（Ch 28 的 Tokio）把這一步換成真正的 I/O multiplexing：一個叫 **reactor** 的元件跑 `epoll_wait()`，當某個 socket fd 就緒，reactor 就 `wake()` 對應的 future。你的 epoll 知識（`systems/kernel_internals`）直接接上：epoll 回報「fd 可讀」→ reactor 把它翻譯成「wake 這個 future」→ executor 回來 poll → future 的 `read().await` 這次回 `Ready`。

## 兩種 pin 的方式：`Box::pin` 與 `pin!`

前面用 `Box::pin` 把自我引用結構釘在 **heap** 上。但如果你只是要在一個函式裡 pin 一個 future、poll 完就丟（像 `block_on`），配一塊 heap 太浪費——`std::pin::pin!` 巨集（`Rust 1.68+` 穩定）讓你把值釘在**當前 stack frame** 上，零 heap 配置：

```rust
use std::pin::pin;

fn block_on<F: Future>(fut: F) -> F::Output {
    let mut fut = pin!(fut);       // 在 stack 上 pin，得到 Pin<&mut F>，不需要 Box
    // ... 造 waker、包成 Context，然後 loop { fut.as_mut().poll(&mut cx) } ...
}
```

實測（補上完整 waker 樣板、跑一個 `Countdown{n:3}`，程式碼略）輸出 `countdown done (stack-pinned, no Box)`。`pin!` 的原理：它把值放進一個 stack 上的臨時變數，然後給你一個指向它的 `Pin<&mut _>`——因為那個臨時變數的 scope 綁在當前函式，Rust 能保證你拿到 `Pin` 之後不可能再 move 它（你根本碰不到那個臨時變數本體）。

取捨：`pin!` 省一次 heap 配置，但被 pin 的值**綁在當前 stack frame**——不能回傳、不能存進比它活更久的結構。要「pin 一個 future 且它的生命週期超過當前函式」（例如存進一個 task 佇列，練習 D 就是），得用 `Box::pin`（heap 上，位址獨立於任何 stack frame）。記法：**函式內用完即丟 → `pin!`；要存起來/回傳/放進資料結構 → `Box::pin`。**

## 兩種造 Waker 的方式：`Wake` trait 與 `RawWaker`

本章的 executor 用 `std::task::Wake` trait（`Rust 1.51+`）造 Waker——`impl Wake for MyType`，然後 `Arc<MyType>::into()`。這是**高層路徑**：你只寫 `wake` 的邏輯，`std` 幫你把 `Arc` 包成底層的函式指標表。

底下還有一層**底層路徑**：`RawWaker` + `RawWakerVTable`。這才是 `Waker` 真正的內部表示——一個 `data: *const ()`（指向你的喚醒狀態）加一張 vtable（四個函式指標：`clone`、`wake`、`wake_by_ref`、`drop`）。Ch 26 的 `dummy_raw_waker` 就是走這條路造了個 no-op waker。手動走這條路要自己管 `data` 指標的引用計數（用 `Arc::into_raw`/`from_raw`），非常容易出錯，`unsafe` 遍地。

```
   Waker  ─────►  RawWaker { data: *const (),  vtable: &RawWakerVTable }
                                 │                        │
                    你的喚醒狀態的裸指標        ┌──────────┴──────────┐
                    （通常是 Arc<T> into_raw）  clone  wake  wake_by_ref  drop
```

`Wake` trait 就是這張 vtable 的安全包裝：`Arc<impl Wake>` 自動生成正確的四個函式（`clone` = `Arc::clone`、`drop` = `Arc::drop`、`wake` = 呼叫你的 `wake`）。**規則**：能用 `Wake` trait 就用它（安全、少寫一半 code）；只有在 `no_std`（沒有 `Arc`）或有特殊需求時才手刻 `RawWaker`。練習 D 我們用 `Wake` trait，理由就是這個。

## 對比與取捨

| 機制 | 忙等（Ch 26 玩具版） | Waker + park（本章 / 真 executor） |
|---|---|---|
| Pending 時 CPU | 100%（死命重 poll） | 幾乎 0%（thread 進 kernel 睡） |
| 誰觸發下次 poll | 迴圈自己 | future 存的 `Waker` 被 `wake()` |
| 對應 C 概念 | spin loop | epoll_wait + 事件通知 |

| 概念 | 意義 | C/C++ 對照 |
|---|---|---|
| `Pin<P<T>>` | 保證 `T` 不再被移動 | 沒有直接對應；C 靠紀律「這個物件別 memcpy」 |
| `Unpin` | 標記「move 安全」，多數型別自動有 | 類似「trivially relocatable」（C++ 有提案但未標準化） |
| `PhantomPinned` | 手動讓型別 `!Unpin` | 沒有對應 |

## 踩雷集錦

1. **「`Pin` 會在執行期阻止移動」——錯**：`Pin` 沒有任何執行期成本或檢查。它是純型別層級的承諾。對 `Unpin` 型別它形同透明；對 `!Unpin` 型別，它靠「不給你 safe 的 `&mut T`」在編譯期擋住 move。

2. **「所有 future 都是 `!Unpin`，都要小心 Pin」——過度擔心**：只有真正含跨 await 自我引用的 async 狀態機才 `!Unpin`。很多 future（`async {}` 沒有跨 await 借用的、手寫的簡單 future）其實是 `Unpin`。而且你平常用 Tokio 幾乎碰不到 `Pin`——`tokio::spawn` 和 `.await` 都幫你處理好了。`Pin` 是**寫 executor / 手刻 future** 時才浮上檯面的東西（練習 D 你會碰到）。

3. **忘了存 `Waker` 就回 `Pending`**：future 回 `Pending` 卻沒把 `cx.waker()` 存起來，等於「掛了電話又不留名片」——executor park 之後**永遠不會被叫醒**，整個 task 卡死（hang）。這是手寫 future 最常見的死鎖 bug。回 `Pending` 之前，先確認「有人會 wake 我」。

4. **每次 poll 都該更新存的 Waker**：executor 可能用不同的 Waker 重 poll 你（例如 future 在 task 之間搬動）。正確做法是**每次** `Pending` 都 `cx.waker().clone()` 存最新的，不是只在第一次存。本章 `TimerFuture` 每次 poll 都覆寫 `s.waker`，就是這個原因。

5. **`Pin::get_unchecked_mut` 是 `unsafe` 不是裝飾**：用它就是在向編譯器承諾「我拿到 `&mut T` 之後不會 move `T`」。真的 move 了就是 UB。只在你能證明「這條路徑不會移動被 pin 的值」時用它。

## 進階：再往深一層

- **`Pin<&mut T>` 的 projection**：當你手寫 `!Unpin` 的複合 future，需要「從 `Pin<&mut Outer>` 拿到某個欄位的 `Pin<&mut Field>`」，這叫 pin projection，規則很細（哪些欄位是 structurally pinned）。手寫容易出錯，生態用 [`pin-project`](https://docs.rs/pin-project/) crate 的 macro 幫你安全生成。本課不展開，但你手刻複雜 future 時會需要它。

- **`std::pin::pin!` 巨集**：`Rust 1.68+` 穩定，讓你在 stack 上 pin 一個值而不需要 `Box`（省一次 heap 配置）。`let fut = pin!(some_future);` 得到 `Pin<&mut _>`。適合 `block_on` 這種「pin 一個 future 到 stack 就好」的場景。本章為了教學用 `Box::pin`（語意更清楚），但知道有 stack 版。

- **為什麼 `poll` 用 `Pin<&mut Self>` 而不是把整個 async 設計成 `Unpin`？** 因為要 `Unpin`，就得禁止 async fn 跨 await 借用區域變數——那會讓一大票自然的 async code 寫不出來（`let buf = ...; sock.read(&mut buf).await;`）。Rust 選擇讓狀態機可以自我引用（保住寫 code 的自由），代價是引入 `Pin` 這套機制。又是一次「保住表達力，把複雜度推給型別系統」的取捨。

## 動手練習

1. 把本章 `TimerFuture::poll` 裡存 Waker 的那行 `s.waker = Some(cx.waker().clone());` **刪掉**（改成什麼都不做直接回 `Pending`），重跑。觀察程式**永遠卡住**（timer thread 呼叫 `wake()` 時 `s.waker` 是 `None`，沒人叫醒 executor）。這是踩雷 3 的實地版。

2. 把 `SelfRef` 移動示範裡的 `let s2 = s1;` 那段拿掉，只留 `new` 回傳的 `s1`，看 `s1.b` 是否等於 `&s1.a`。理解「連從函式 return 都算一次 move」。

3. 給一個 `Unpin` 型別（如 `struct Plain(i32);`，不含 `PhantomPinned`）試 `Pin::into_inner(Box::pin(Plain(3)))`，確認它**編得過**——對照 `!Unpin` 版的 E0277，體會 `Unpin` 的作用。

## 本章重點整理

- **executor 核心迴圈**：poll → `Pending` 就把 thread park（進 kernel 睡，不燒 CPU）→ future 存的 `Waker` 被 `wake()` → 回來重 poll。`Context` 是裝 `Waker` 的信封，遞進每次 poll。
- **`Pin` 保證值不被移動**，讓自我引用的 async 狀態機安全。它是純型別層級的承諾，靠「不給 safe 的 `&mut T`」在編譯期擋 move，零執行期成本。
- **`Unpin`** 標記「move 安全」，多數型別自動有；只有真正自我引用的型別（async 狀態機、`PhantomPinned` 標記的）是 `!Unpin`，才真的受 `Pin` 約束。

## 自我檢核

- [ ] 不看筆記，能不能解釋 `Waker` 解決了忙等版的什麼問題？（提示：誰負責觸發下次 poll）
- [ ] 如果面試官問「為什麼 async 能在單執行緒撐住上萬條 idle 連線不燒 CPU」，你會怎麼回答？
- [ ] 能用「移動打斷內部指標」這個具體場景解釋 `Pin` 在保護什麼，而不只是說「Pin 固定記憶體」
- [ ] 知道 `Unpin` 是什麼、為什麼 `i32`/`String` 是 `Unpin` 而 async 狀態機可能不是
- [ ] 能說出「手寫 future 回 Pending 前一定要做什麼」（存 Waker），不做會怎樣（永久 hang）

## 延伸閱讀

### 官方文件

- **[`std::pin` 模組文件](https://doc.rust-lang.org/std/pin/index.html)**
  - **讀哪裡**：模組層級的長篇說明，特別是 "Example: self-referential struct" 一節——它用的例子和本章的 `SelfRef` 幾乎一樣，是 `Pin` 語意的最權威來源。
  - **前提知識**：懂裸指標與 `unsafe`（[Ch 17](./17-unsafe-basics.md)）。文件偏硬，配本章的可跑範例一起讀。

- **[`std::task::Wake` trait 文件](https://doc.rust-lang.org/std/task/trait.Wake.html)**
  - **讀哪裡**：頁面上的完整範例——一個用 `Arc<impl Wake>` 造 `Waker` 的最小 executor，跟本章 `MyWaker` 同構，可對照。

### 部落格 / 技術文章

- **[「Pin, Unpin, and why Rust needs them」— Cliffle (Cliff L. Biffle)](https://cliffle.com/blog/async-inversion/)** 以及 **[fasterthanlime「Pin and suffering」](https://fasterthanli.me/articles/pin-and-suffering)**
  - **這兩篇說什麼**：從「async 狀態機為什麼自我引用」一路推到「所以需要 Pin」，比官方文件更循序漸進。fasterthanli.me 那篇用大量圖，是把 Pin 講得最不痛苦的一篇。
  - **為什麼值得讀**：Pin 是勸退點，這兩篇是公認的「終於看懂了」讀物。讀完本章卡住就看它們。

- **[「Async/Await」系列 — withoutboats](https://without.boats/blog/)**
  - **這篇說什麼**：`boats` 是 async Rust / `Pin` 設計的核心 RFC 作者之一。他的部落格解釋了**為什麼**這樣設計（設計決策的第一手來源），而不只是「怎麼用」。
  - **讀哪裡**：找 "Why async Rust" 和關於 Pin 的幾篇；適合本章「進階：為什麼不設計成 Unpin」那段想更深入的人。

### 官方文件 / RFC

- **[Async Book — "Executing Multiple Futures at a Time" 與 "Wakeups"](https://rust-lang.github.io/async-book/02_execution/03_wakeups_run_the_executor.html)**
  - **讀哪裡**：2.3 節手把手做一個 `TimerFuture` + executor，跟本章的範例幾乎一模一樣但用 `RawWaker`（底層路徑）。對照本章的 `Wake` trait（高層路徑），你會同時懂兩條造 Waker 的路。

Waker 和 Pin 都拆完了，你手上有一台能掛起、能喚醒的迷你 executor。下一章換生產級的：Tokio 怎麼把這套機制做成能跑真 TCP、work-stealing 排程、底層接 epoll 的 runtime。

→ [Ch 28 Tokio 實戰與 epoll 連結](./28-tokio.md)
