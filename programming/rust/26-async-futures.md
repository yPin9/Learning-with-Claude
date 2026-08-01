# Ch 26 — async 原理一：Future 與 poll

> **目標**：把 `async fn` 從魔法還原成一台你看得懂的**狀態機（state machine）**。學完你能回答：`async fn` 編譯出來到底是什麼、`.await` 在做什麼、為什麼 Rust 的 future 是 lazy（不 poll 不動）、為什麼它是**零成本、無獨立 stack** 的抽象。這是整個 Part 4 async 段落的地基——地基沒打好，Ch 27 的 Waker/Pin、Ch 28 的 Tokio 全是背咒語。

> **環境**：`rustc 1.97.1` (stable)，x86-64 Linux（WSL2）。本章不需要任何外部 crate——`Future`、`Poll`、`Context`、`Waker` 全在 `std`/`core` 裡；手刻的 `block_on` 也只用 `std`。每段 code 都在此環境真跑過，輸出照貼。

## 為什麼需要這個？

你寫過 epoll 事件迴圈。長這樣：一個 `epoll_wait()` 拿回一批就緒的 fd，你對每個 fd 查「這條連線現在進行到哪個階段」——是還在讀 header？讀 body？寫回應？——然後推進它一步，存回新狀態，回去 `epoll_wait()`。那個「每條連線進行到哪」的狀態，在 C 裡是你**手動**維護的：一個 `struct conn { enum state; char *buf; size_t nread; ... }`，一個大 `switch (conn->state)`。

這套東西有個名字：**手寫狀態機**。它能做到單執行緒處理上萬條連線、每條連線不佔一根 8MB 的 OS thread stack。代價是：程式碼被撕成碎片。一個邏輯上「讀 header → 讀 body → 回應」的線性流程，被你拆成三個 `case`，中間的區域變數（讀到一半的 buffer、offset）全部得手動搬進 `struct` 存起來，因為函式一 return 回 event loop，stack 上的東西就沒了。這就是 callback hell 的根源：**控制流被 I/O 邊界切斷，你得自己接線**。

Rust 的 async 就是一句話：**編譯器幫你把那台狀態機生出來**。你寫線性的 `async fn`，`rustc` 把它拆成 `switch (state)`，把跨 `.await` 存活的區域變數自動搬進一個 struct。你拿回 C 手寫狀態機的全部效能好處（無獨立 stack、單執行緒高併發），但寫的是線性程式碼。這一章就是把這個「編譯器生的狀態機」拆開給你看。

## 先建立直覺

想像你在餐廳當唯一的服務生，但你**不准站著等**任何一件事。

- 「幫桌 3 上菜」→ 廚房還沒好 → 你不能站在出餐口等，你得記住「桌 3 等上菜」然後去做別的。
- 有人喊你 → 你查一下有什麼事就緒了 → 推進它。

`Future` 就是「一件還沒做完的事」。`poll` 就是「我來看看你好了沒」：

```
              ┌─────────────────────────────────────────┐
              │            Future::poll(&mut self)        │
              └─────────────────────────────────────────┘
                              │
                 ┌────────────┴────────────┐
                 ▼                          ▼
          Poll::Ready(T)              Poll::Pending
        「做完了，值是 T」        「還沒好，我記下了進度，
                                    好了會叫醒你（wake）」
```

關鍵：**Future 自己不會動**。它就是一坨狀態，躺在那裡。有個叫 **executor**（Ch 27）的東西不斷 `poll` 它，`poll` 一次它推進一步，回 `Pending` 就先擱著，回 `Ready` 就完成。這跟 epoll 迴圈一模一樣：`poll` = 你的 `switch(state)` 推一步，`Pending` = 「這條連線還沒好，回 event loop」，`Ready` = 「這條連線處理完了」。

> 如果你對 epoll 事件迴圈、「一條連線一個狀態機」的模型還不熟，本 repo 的 `systems/kernel_internals` 網路堆疊那部分、以及你自己寫過的 reactor，就是這章的 C 對照組。

## Future trait：async 的原子

先看它的定義。這是 `core::future::Future`（簡化掉一些細節，但形狀就是這樣）：

```rust
pub trait Future {
    type Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>;
}

pub enum Poll<T> {
    Ready(T),
    Pending,
}
```

三個要素：

- **`Output`**：這個 future 完成後產出的型別。`async fn foo() -> u32` 的 future，`Output = u32`。
- **`poll`**：推進一步。回 `Ready(值)` 或 `Pending`。
- **`Pin<&mut Self>` 和 `Context`**：先別管，Ch 27 專門講。這章我們用最簡的方式繞過它們，把注意力放在「poll 回 Ready/Pending 的迴圈」本身。

我們手寫一個最單純的 Future：poll N 次才 ready 的計數器。搭配一個**最簡到不能再簡**的 executor（`block_on`）把它跑完：

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

// 一個計數 N 次才 ready 的 Future
struct CountTo {
    n: u32,
    limit: u32,
}

impl Future for CountTo {
    type Output = u32;
    fn poll(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<u32> {
        self.n += 1;
        println!("  poll #{}", self.n);
        if self.n >= self.limit {
            Poll::Ready(self.n)
        } else {
            Poll::Pending
        }
    }
}

// 最簡 executor：一個什麼都不做的 waker，忙迴圈 poll 到 ready
fn dummy_raw_waker() -> RawWaker {
    fn no_op(_: *const ()) {}
    fn clone(_: *const ()) -> RawWaker { dummy_raw_waker() }
    let vtable = &RawWakerVTable::new(clone, no_op, no_op, no_op);
    RawWaker::new(std::ptr::null(), vtable)
}

fn block_on<F: Future>(mut fut: F) -> F::Output {
    let waker = unsafe { Waker::from_raw(dummy_raw_waker()) };
    let mut cx = Context::from_waker(&waker);
    // Future 必須被 pin 住才能 poll（Ch 27 解釋為什麼；這裡先照做）
    let mut fut = unsafe { Pin::new_unchecked(&mut fut) };
    loop {
        match fut.as_mut().poll(&mut cx) {
            Poll::Ready(v) => return v,
            Poll::Pending => { /* 忙等：真 executor 會在此掛起，見 Ch 27 */ }
        }
    }
}

fn main() {
    let result = block_on(CountTo { n: 0, limit: 3 });
    println!("done: {}", result);
}
```

真跑，輸出：

```
  poll #1
  poll #2
  poll #3
done: 3
```

這就是 async 的全部核心機制，沒有魔法。`block_on` 就是「反覆 `poll` 到 `Ready`」；`Pending` 在這個玩具版是忙等（浪費 CPU），真 executor 會在此把執行緒**掛起**，等 `wake()` 才回來 poll——那是 Ch 27 的主題。`dummy_raw_waker` 那坨先當黑盒子：它造了一個「被 wake 也什麼都不做」的 waker，因為我們這裡是忙等，不需要真的喚醒機制。

## `async fn` 是什麼：狀態機

現在核心問題：你寫 `async fn`，`rustc` 給你什麼？

答案：一個**實作了 `Future` 的匿名 struct/enum**，它的 `poll` 是一台狀態機。每個 `.await` 是一個**狀態轉移點（yield point）**——poll 到這裡如果底層還沒好，就存下當前進度、回 `Pending`；下次 poll 從這個點**接著跑**。

拿這個 async fn 當例子（有兩個 await 點）：

```rust
async fn task() {
    println!("A");
    YieldOnce.await;   // yield point 1
    println!("B");
    YieldOnce.await;   // yield point 2
    println!("C");
}
```

`rustc` 生的東西**概念上**等價於下面這個我們手寫的 enum。這不是猜的——這就是 async/await 的 desugar 原理，我把它手刻出來、真的跑，讓你看到「resumed poll 從上次的狀態接著跑」：

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

// 一個 yield 一次的 Future：第一次 poll 回 Pending，第二次回 Ready
struct YieldOnce { yielded: bool }
impl Future for YieldOnce {
    type Output = ();
    fn poll(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<()> {
        if self.yielded { Poll::Ready(()) }
        else { self.yielded = true; Poll::Pending }
    }
}

// 上面 `async fn task()` 編譯出來的狀態機，手寫版：
// 每個狀態 = 「跑到哪個 await 點了」。跨 await 存活的區域變數會被搬進對應 variant
// （這個例子沒有跨 await 的變數，所以 variant 只存還沒完成的子 future）。
enum TaskSM {
    Start,
    AfterFirst(YieldOnce),
    AfterSecond(YieldOnce),
    Done,
}

impl Future for TaskSM {
    type Output = ();
    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        let this = &mut *self;
        loop {
            match this {
                TaskSM::Start => {
                    println!("A");
                    *this = TaskSM::AfterFirst(YieldOnce { yielded: false });
                    // 不 return，繼續 loop 去 poll 第一個子 future
                }
                TaskSM::AfterFirst(y) => {
                    match Pin::new(y).poll(cx) {
                        Poll::Pending => return Poll::Pending,   // <- 存著進度，交還控制權
                        Poll::Ready(()) => {
                            println!("B");
                            *this = TaskSM::AfterSecond(YieldOnce { yielded: false });
                        }
                    }
                }
                TaskSM::AfterSecond(y) => {
                    match Pin::new(y).poll(cx) {
                        Poll::Pending => return Poll::Pending,
                        Poll::Ready(()) => {
                            println!("C");
                            *this = TaskSM::Done;
                        }
                    }
                }
                TaskSM::Done => return Poll::Ready(()),
            }
        }
    }
}

fn dummy_raw_waker() -> RawWaker {
    fn no_op(_: *const ()) {}
    fn clone(_: *const ()) -> RawWaker { dummy_raw_waker() }
    let vtable = &RawWakerVTable::new(clone, no_op, no_op, no_op);
    RawWaker::new(std::ptr::null(), vtable)
}

fn block_on<F: Future>(mut fut: F) -> F::Output {
    let waker = unsafe { Waker::from_raw(dummy_raw_waker()) };
    let mut cx = Context::from_waker(&waker);
    let mut fut = unsafe { Pin::new_unchecked(&mut fut) };
    let mut n = 0;
    loop {
        n += 1;
        println!("[executor] poll #{n}");
        if let Poll::Ready(v) = fut.as_mut().poll(&mut cx) { return v; }
    }
}

fn main() {
    block_on(TaskSM::Start);
    println!("size of state machine = {} bytes", std::mem::size_of::<TaskSM>());
}
```

真跑，輸出：

```
[executor] poll #1
A
[executor] poll #2
B
[executor] poll #3
C
size of state machine = 2 bytes
```

盯著這個輸出看，這是本章最重要的一段：

- **poll #1**：狀態機從 `Start` 開始，印 `A`，轉到 `AfterFirst`，poll 第一個 `YieldOnce` → `Pending` → **整個 task 回 `Pending`，控制權交還給 executor**。
- **poll #2**：狀態機**不是重頭跑**——`match this` 直接進到 `AfterFirst`。第一個 `YieldOnce` 這次回 `Ready`，印 `B`，轉到 `AfterSecond`，poll 第二個 → `Pending` → 回 `Pending`。
- **poll #3**：進 `AfterSecond`，`Ready`，印 `C`，`Done`，回 `Ready`。

**這就是 `.await` 的真相**：它是「poll 子 future，`Pending` 就 return `Pending`（把當前狀態存在 enum 裡），`Ready` 就繼續」的語法糖。`A`、`B`、`C` 只各印一次——沒有重複執行——因為狀態機記得上次停在哪。你在 C 裡靠 `switch(state)` 手做的事，`rustc` 幫你做了。

還有那個 `size = 2 bytes`：整台狀態機只有 2 bytes（兩個 `YieldOnce` 各 1 byte 的 `bool`，加上 enum 判別，經 niche 優化壓到 2）。它**沒有獨立的 stack**。這帶到下一節。

## future 的大小 = 跨 await 存活的變數

上一節的 `TaskSM` 是 2 bytes，因為它幾乎不存東西。真實的 future 有多大？答案就是 Ch 26 的核心規則的直接後果：**future 的大小等於「跨 await 存活的變數」的總和**（加上判別 tag）。用不到的變數不進狀態機，跨 await 要活著的變數才進。

實測給你看。一個空 future vs 一個跨 await 抓著 4KB buffer 的 future：

```rust
use std::mem::size_of_val;

async fn small() { }

async fn holds_big() {
    let buf = [0u8; 4096];      // 4KB 陣列
    yield_once().await;         // buf 在 await 之後還用 -> 必須跨 await 存活 -> 進狀態機
    let _ = buf[0];
}
async fn yield_once() {}

fn main() {
    let f1 = small();
    let f2 = holds_big();
    println!("small future    = {} bytes", size_of_val(&f1));
    println!("holds_big future= {} bytes", size_of_val(&f2));
}
```

真跑，輸出：

```
small future    = 1 bytes
holds_big future= 4098 bytes
```

`holds_big` 是 4098 bytes：4096 的 buffer + 2 bytes 的狀態管理。這個 buffer **被烤進了狀態機 struct**，因為它在 `.await` 前後都活著。如果你把 `let _ = buf[0];` 那行刪掉（buffer 在 await 之後不再用），future 會瞬間縮小——因為 rustc 判定它不需要跨 await 存活。

這帶出一個生產上的真實問題：**大 async fn 產生大 future**。每個 await 點的存活變數集合疊起來，決定了整台狀態機的大小。有人不小心在一個 async fn 裡跨多個 await 抓著好幾個大 buffer，future 膨脹到幾 KB，`Box::pin` 每個都在 heap 配一大塊，或者 spawn 時複製成本高。解法通常是把大 async fn 拆小，或把大 buffer 移到 await 之外/用完即丟。記住這條規則，你就能預測任何 async fn 的 future 大小。

## `async fn` vs `async {}`：都是造 future

到目前為止我們寫 `async fn`。還有一個形式：**`async {}` 區塊**。它是個**表達式**，求值成一個 future——`async fn foo() { body }` 其實就是 `fn foo() -> impl Future { async { body } }` 的糖。兩者生的都是實作 `Future` 的匿名狀態機。

```rust
async fn double(x: u32) -> u32 { x * 2 }

fn main() {
    // async {} 是表達式，值是一個 future；裡面可以 await 別的 future
    let fut = async {
        let a = double(3).await;
        let b = double(a).await;
        a + b
    };
    println!("result = {}", block_on(fut));   // block_on 定義同前，此處省略
}
```

真跑（補上前面的 `block_on`/waker 樣板），輸出：

```
result = 18
```

`async {}` 在實務上到處都是：`tokio::spawn(async move { ... })` 就是 spawn 一個 async 區塊；閉包裡要回 future 時寫 `|| async { ... }`。記法：**`async fn` 是「定義一個回傳 future 的函式」，`async {}` 是「當場造一個 future 值」**。兩者的狀態機生成規則、lazy 特性、跨 await 變數搬移全都一樣。`async move {}` 的 `move` 跟閉包的 `move`（[Ch 14](./14-closures.md)）同義：強制把用到的外部變數**所有權**搬進 future，而不是借用——spawn 到背景跑的 future 幾乎都要 `move`，因為它的生命週期會超過建立它的 scope。

## 底層機制：為什麼 async 是零成本、無 stack 的

三種「同時做很多事」的方案，比較它們的記憶體代價：

```
                     每個「併發單位」佔多少記憶體？
  ┌──────────────────────────────────────────────────────────────┐
  │ OS thread   │ 固定一根 stack，Linux 預設 8MB（見 ulimit -s）   │
  │             │ 開一萬條 = 保留 80GB 虛擬位址，context switch    │
  │             │ 進 kernel，1:1 對應 kernel 排程單位              │
  ├──────────────────────────────────────────────────────────────┤
  │ goroutine   │ growable stack，起始 ~8KB，需要就長大/縮小       │
  │ (Go)        │ runtime 自己排程，比 thread 輕，但仍有 stack      │
  ├──────────────────────────────────────────────────────────────┤
  │ Rust future │ 一個 struct/enum，大小 = 跨 await 存活變數的總和 │
  │             │ 沒有獨立 stack。上面那台 = 2 bytes。             │
  │             │ poll 時借用**呼叫者的** stack 往下跑             │
  └──────────────────────────────────────────────────────────────┘
```

Rust future 沒有自己的 stack。它 poll 的時候，是**借用 executor 呼叫 `poll` 那條 thread 的 stack** 往下執行，執行到 `.await` 遇到 `Pending` 就 return——stack 收回去，但「跨 await 要活著的變數」早就被 rustc 搬進 enum 存好了。這是 **stackless coroutine**（無堆疊協程）。goroutine 是 **stackful coroutine**（有堆疊協程）——它有自己一根會長大的 stack，所以能在任意深的呼叫層 yield，代價是每個 goroutine 都要那根 stack。

「零成本抽象（zero-cost abstraction）」在這裡的具體意思：

1. **不用你的東西不花錢**：沒 `.await` 的 async fn 幾乎等同同步函式。
2. **用的東西，你手寫也快不了多少**：那台狀態機，就是你在 C 裡手寫 `switch(state)` 會寫出來的東西——甚至更緊湊，因為 rustc 會算出「跨 await 存活變數的最小集合」，只存必要的。沒有 GC，沒有 runtime 幫每個 future 配 stack。

> **為什麼不選 stackful（像 Go）？** 不是 stackful 不好——它寫起來更自由（任意深度都能 yield）。Rust 選 stackless 是因為它要**無 runtime、能跑在 `no_std`/kernel**（[Ch 22](./22-no-std.md)）。stackful 需要一個能配置/切換 stack 的 runtime，這跟「Rust 能當 C 的替代品、跑在沒有 OS 的地方」的目標衝突。代價就是 Ch 27 的 `Pin`——自我引用的狀態機不能亂搬——以及 async 遞迴要 `Box`（Ch 29）。設計空間的取捨，全都源自「不要 runtime」這個決定。

## Future 是 lazy 的：不 poll 不動

這是 Rust future 跟很多語言的 Promise/Task 最大的行為差異，也是初學者最常踩的坑。**呼叫 `async fn` 不會執行它裡面任何一行**。它只是**建構**那個狀態機 struct，狀態停在 `Start`。要有人 `poll` 它（透過 `.await` 或丟給 executor）才會動。

證明給你看：

```rust
async fn say(msg: &str) -> usize {
    println!("[running] {}", msg);   // 這行只有被 poll 才會跑
    msg.len()
}

fn main() {
    println!("before creating future");
    let fut = say("hello");        // 呼叫了 async fn，但沒有 .await
    println!("future created, but nothing ran yet");
    drop(fut);                     // 直接丟掉，永遠不會被 poll
    println!("future dropped without ever polling");
}
```

真跑，輸出：

```
before creating future
future created, but nothing ran yet
future dropped without ever polling
```

**`[running] hello` 從來沒印出來**。`say("hello")` 只造了狀態機，沒跑。這跟 JavaScript 的 `Promise` 相反——JS 的 Promise 是 **eager**：`new Promise(executor)` 一建立，`executor` 就立刻開始跑了。Rust 是 **lazy**：future 是一份「待辦計畫」，不交給 executor 就永遠是計畫。

lazy 帶來的實際後果：`combinator`（`join!`、`select!`）可以在**不啟動**任何 future 的情況下組合它們，最後一次交給 executor。也帶來一個天天有人踩的坑——寫了 async fn 呼叫但忘了 `.await`，程式**靜默地什麼都沒做**（Ch 29 會看到編譯器的 `must_use` warning）。

## 對比與取捨

| 概念 | C 手寫狀態機 | Rust async | Go goroutine | JS Promise |
|---|---|---|---|---|
| 誰生狀態機 | 你，手動 `switch` | `rustc` 自動 | runtime（stackful） | 引擎 |
| 有獨立 stack | 否 | **否**（stackless） | 是（growable） | 否 |
| 跨 yield 的變數 | 你手動搬進 struct | rustc 自動搬進 enum | 在 goroutine stack 上 | 閉包捕獲 |
| lazy / eager | N/A | **lazy**（不 poll 不動） | eager（go 就跑） | **eager** |
| 需要 runtime | 否 | 否（future 本身）＊ | 是 | 是（event loop） |

＊future 本身不需要 runtime，但要真的跑起來需要一個 executor（Ch 27 手刻，Ch 28 用 Tokio）。差別是：executor 可以是你自己寫的 30 行 code，也可以是 `no_std` 的，不是語言綁死的重量級 runtime。

## 踩雷集錦

1. **「呼叫 async fn 它就開始跑了」——錯**：Rust future 是 lazy 的。`let f = foo();` 什麼都沒跑，只建了狀態機。要 `.await` 或交給 executor 才動。從 JS/C# 過來的人最容易帶著 eager 的直覺踩這個。

2. **「`.await` 會阻塞執行緒」——錯**：`.await` 不阻塞 thread。它是「poll 子 future，沒好就把控制權**讓回** executor，讓同一條 thread 去跑別的 task」。阻塞 thread 的是 `std::thread::sleep` 這種同步呼叫（Ch 28 會示範它怎麼卡死整個 executor）。

3. **「async 一定比同步快」——錯**：async 的價值是**在少量 thread 上處理大量 I/O 併發**。如果你的工作是 CPU-bound（純計算），async 只會加開銷（狀態機、poll、waker），沒有任何好處，該用 thread pool。async 換的是「不為每個併發任務付一根 stack」，不是「算得更快」。

4. **「future 有自己的 stack」——錯**：stackless。這是為什麼自我引用的 future 需要 `Pin`（Ch 27）、async 遞迴需要 `Box`（Ch 29）——因為狀態機是個固定大小的 struct，不能無限大，也不能亂搬。

5. **忘了 `.await`**：`some_async_fn();` 不加 `.await`，編譯器會給 `unused_must_use` warning，但**不會報錯**——程式編得過、跑起來那段邏輯靜默消失。看到「這段 async code 好像沒執行」，先檢查 `.await` 有沒有漏。

## 進階：再往深一層

- **`Poll::Pending` 之後由誰負責再 poll？** 這章的 `block_on` 是忙等，一直 poll。真 executor 不會——它 poll 到 `Pending` 就**掛起這條 thread**，直到 future 透過 `Context` 裡的 `Waker` 通知「我好了」。整個 `Waker`/`Context` 機制是 Ch 27 的主題，也是「async 為什麼不浪費 CPU」的關鍵。

- **跨 await 的變數具體怎麼被搬？** rustc 對 async fn 的 body 做**存活性分析（liveness analysis）**：只有「在某個 `.await` 點之前定義、之後還會用」的變數才需要進 enum。純區域、用完即丟的變數不進。所以 future 的大小 = 所有 await 點中，存活變數集合最大的那個。這也是為什麼「一個巨大的 async fn」可能產生意外龐大的 future（每個 await 點的存活變數疊起來）——生產上有人為此把大 async fn 拆小。

- **想看 rustc 真的生成什麼？** `cargo expand` 展不開 async（因為狀態機是 MIR 層的轉換，不是 macro）。要看得用 `-Z dump-mir` 或直接讀 `objdump` 反組譯（[Ch 34](./34-rust-binary-internals.md) 教你怎麼讀 Rust 生成碼）。本章手寫的 `TaskSM` 就是為了讓你不用做這件事也能理解形狀。

## 動手練習

1. 把 `CountTo` 的 `limit` 改成 1，重跑，看 `poll` 只印一次就 `Ready`——理解「limit=1 表示第一次 poll 就完成」。

2. 在 `TaskSM` 手寫狀態機裡，把 `AfterFirst` 分支的 `println!("B")` 移到 `TaskSM::Start` 分支（印 `A` 之後立刻印 `B`），重跑。觀察 `B` 現在跟 `A` 在同一次 poll 出現——理解「兩個 await 之間的同步 code 在同一次 poll 裡跑完」。

3. 寫一個 `Ready<T>` future：`poll` **第一次就**回 `Ready(值)`，永不 `Pending`。用 `block_on` 跑，確認 executor 只 poll 一次。這就是 `std::future::ready` 的實作。

## 本章重點整理

- `Future` 是「一件還沒做完的事」；`poll` 推進一步，回 `Ready(值)` 或 `Pending`。Future 自己不動，要 executor 反覆 poll。
- `async fn` / `async {}` 被 `rustc` 編譯成一台**狀態機**：每個 `.await` 是 yield point，跨 await 存活的變數被自動搬進狀態 enum。`.await` = 「poll 子 future，`Pending` 就存狀態 return，`Ready` 就繼續」。
- Rust future 是 **stackless**（無獨立 stack）、**lazy**（不 poll 不動）、**零成本**（就是你手寫的 `switch(state)`，甚至更緊湊）。

## 自我檢核

- [ ] 不看筆記，能不能用自己的話解釋「`async fn` 編譯成什麼」？（提示：一個實作 Future 的狀態機 enum）
- [ ] 能說出 `.await` 具體做了什麼（不是「等待」這種模糊說法，而是 poll 迴圈的哪一步）
- [ ] 能解釋為什麼 Rust future 沒有獨立 stack，以及這跟 goroutine 的差別
- [ ] 如果有人說「我呼叫了 async fn 但它沒跑」，你能立刻指出兩個最可能原因（沒 await / lazy）
- [ ] 知道在什麼情況下**不**該用 async（CPU-bound 工作）

## 延伸閱讀

### 官方文件 / RFC

- **[`std::future::Future` 文件](https://doc.rust-lang.org/std/future/trait.Future.html)**
  - **讀哪裡**：trait 定義與 "A future represents an asynchronous computation" 那段導言。
  - **和本章的關聯**：本章手寫的 `CountTo`/`TaskSM` 就是實作這個 trait；官方文件補充 `Output` 關聯型別與 `poll` 契約的精確措辭。

- **[Async Book — "Under the Hood: Executing Futures and Tasks"](https://rust-lang.github.io/async-book/02_execution/01_chapter.html)**
  - **讀哪裡**：2.1「The Future Trait」到 2.3「Executors」。2.1 節就是本章的核心；它用 `SocketRead` 當例子，跟本章的 `CountTo` 互補。
  - **前提知識**：懂 trait（[Ch 9](./09-traits.md)）。可先跳過 `Waker` 細節，那是 Ch 27。

### 部落格 / 技術文章

- **[「How Rust optimizes async/await」— Tyler Mandry](https://tmandry.gitlab.io/blog/posts/optimizing-await-1/)** （Rust async working group 成員）
  - **這篇說什麼**：rustc 怎麼把 async fn 的多個 future 佈局（layout）進一個狀態機、怎麼優化大小（本章結尾提到的「存活變數疊起來」問題的深入版）。
  - **為什麼值得讀**：作者是 async Rust 的核心貢獻者之一；這是「狀態機到底長怎樣、為什麼有時很大」最權威的第一手解釋。

- **[「Futures Explained in 200 Lines of Rust」— Carl Fredrik Samson](https://cfsamson.github.io/books-futures-explained/)**
  - **這篇說什麼**：從零手刻 Future + Waker + reactor + executor，跟本課 Ch 26–27 + 練習 D 幾乎同構。
  - **讀哪裡**：整本都值得，但先讀「A Proper Runtime」之前的章節就能對應本章。是本課這幾章最好的補充讀物。

### 書籍

- **《Rust for Rustaceans》— Jon Gjengset（No Starch Press, 2021）**
  - **讀哪幾章**：第 8 章「Async」。它從 `Future` trait 講到 `Pin`，深度和本課 Ch 26–27 對齊，且對 C/C++ 背景友善。
  - **這本書的定位**：本課 async 段落的最佳單本紙本補充。

async fn 已經被還原成狀態機，但我們一直用「忙等」的假 executor，還把 `Pin`、`Context` 當黑盒子。下一章把這兩個坑填了：真正的 executor 怎麼靠 `Waker` 掛起而不燒 CPU，以及自我引用的狀態機為什麼需要 `Pin` 保護。

→ [Ch 27 async 原理二：executor/Waker/Pin](./27-async-executor-pin.md)
