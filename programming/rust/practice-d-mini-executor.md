# 練習 D — 手刻 mini async executor

> **目標**：把 Ch 26–29 學到的 Future / poll / Waker / Pin 全部拼起來，**從零手刻一個單執行緒 async executor**——`Task` 佇列、真正的 `Waker`（用 `Wake` trait 或 `RawWaker`）、`block_on`、`spawn`、poll 迴圈——並跑通幾個自訂 Future（counter、timer）驗證併發。完成後你就親手造出了這四章一直在用的那台機器：你會知道 Tokio 底下那個 executor 不是魔法，就是你手上這 80 行 code 的生產放大版。這是 Part 4 的畢業考。

> **環境**：`rustc 1.97.1` (stable)，x86-64 Linux（WSL2）。**全程只用 `std`，不需要 Tokio 或任何外部 crate**——這正是重點：你要證明 async 的核心機制在標準庫裡就齊了，executor 是你能自己寫的東西。參考解答在本環境真跑過，輸出照貼。

## 背景與動機

Ch 27 你手刻過一個 `block_on`，但它只能跑**一個** future。Ch 28 你用 Tokio 的 `spawn` 同時跑很多 task。這中間的跳躍——「從跑一個 future，到用一個 task 佇列 + Waker 跑很多 task」——就是「executor」這個東西的本質。這個練習要你把那個跳躍親手做一遍。

為什麼值得做？因為 async Rust 的所有「神秘感」都集中在 executor：Waker 到底怎麼把「future 好了」變成「重新 poll」？多個 task 怎麼公平輪流？Pending 的 task 去哪了、怎麼回來？你讀十篇文章不如自己寫一次——寫完，Tokio 的行為對你就再也不神秘了。這也是面試 async Rust 時最能證明「你真的懂」的東西：能手刻 executor 的人，不會是背 API 的人。

## 任務規格

實作一個單執行緒 async executor，API 如下：

| 項目 | 規格 |
|---|---|
| `Executor::new()` | 建立一個空的 executor（內含 task ready 佇列） |
| `Executor::spawn(fut)` | 接受 `impl Future<Output = ()> + Send + 'static`，包成 `Task` 丟進 ready 佇列 |
| `Executor::run(self)` | 跑主迴圈：從 ready 佇列取 task、poll 它、`Pending` 就等它自己 wake 回來、`Ready` 就完成。所有 task 完成後 `run` 返回 |
| `Waker` | 必須是**真的** Waker：task 的 `wake()` 要能把自己塞回 ready 佇列。用 `std::task::Wake` trait（推薦）或 `RawWaker`/`RawWakerVTable` |

**限制**：

- 只能用 `std`（`std::future`、`std::task`、`std::pin`、`std::sync`、`std::thread`、`std::collections`）。**不准用 Tokio、futures、async-std 等任何 async crate**。
- 單執行緒 executor（`run` 在呼叫它的那條 thread 上跑 task）。Waker 可以來自別的 thread（timer 的背景 thread），但 poll 只在 `run` 的 thread 上發生。
- 要能**同時跑多個 task 併發**——不是跑完一個才跑下一個，而是交錯推進。

**驗證用的自訂 Future**（你也要實作）：

1. `Counter { limit }`：poll `limit` 次才 `Ready`，每次 `Pending` 前 `wake_by_ref()` 要求立刻再排一次。用來測 poll 迴圈與多 task 交錯。
2. （進階）`Timer { ms }`：spawn 一個背景 thread 睡 `ms` 毫秒，到期 `wake()`。用來測「真正掛起、由外部 thread 喚醒」。

## 驗收標準

做完之後，逐條檢查——每一條都是「你真的懂了 executor」的證據，不是「湊出輸出」：

- [ ] `Executor::run` 能跑完並**乾淨返回**（不 panic、不 hang），即使 spawn 0 個 task。
- [ ] spawn 兩個 counter，輸出是**交錯**的（A、B、A、B、A），不是 A 全跑完才 B。這證明多 task 併發。
- [ ] Waker 是**真的**：counter 的 `wake_by_ref()` 真的把 task 塞回佇列讓它被重 poll（不是靠 `run` 迴圈自己重試）。
- [ ] （進階）timer 版：兩個 task 都 `Pending` 時，`run` 的 `recv()` **阻塞**（CPU 幾乎 0%），由背景 thread 的 `wake()` 喚醒——不是忙等。可以跑 timer 版時開 `top` 看 CPU 佔用確認。
- [ ] 全程**沒有** Tokio / futures / async-std 等外部 async crate（只 `std`）。
- [ ] 你能對照你的實作和參考解答，說出每個設計決策的理由（為什麼 `Mutex`、為什麼 drop sender、為什麼 `Pin<Box>`）。

## 期望輸出範例

spawn 兩個 counter（A 跑 3 次、B 跑 2 次），它們應該**交錯** poll（證明併發，不是 A 全跑完才跑 B）：

```
[A] poll #1
[B] poll #1
[A] poll #2
[B] poll #2
[A] poll #3
all tasks finished
```

進階 timer 版（fast 100ms、slow 300ms），應該按時間順序完成，且等待期間不燒 CPU：

```
[fast] sleeping 100ms
[slow] sleeping 300ms
[fast] woke after 100ms
[slow] woke after 300ms
done
```

## 如果你卡住了

1. **`Task` 該存什麼？** 至少兩樣：那個 future（型別是 `Pin<Box<dyn Future<Output=()> + Send>>`——為什麼要 `Pin<Box<...>>`？回想 Ch 27：future 可能 `!Unpin`，poll 前要 pin 住，`Box` 讓它大小固定又有穩定位址），以及一個「把自己塞回 ready 佇列」的管道（一個 `Sender`）。

2. **`Waker` 怎麼「把自己塞回佇列」？** `std::task::Wake` trait 要你實作 `fn wake(self: Arc<Self>)`。讓你的 `Task` 自己實作 `Wake`——`wake` 裡就 `self.sender.send(self.clone())`。然後 `let waker: Waker = task.clone().into();` 就能從 `Arc<Task>` 造出 `Waker`。

3. **ready 佇列用什麼？** `std::sync::mpsc::channel()` 最省事：`Sender` 給 Waker 用（塞 task 進來），`Receiver` 給 `run` 迴圈用（取 task 出來）。`run` 迴圈就是 `while let Ok(task) = rx.recv() { poll it }`。

4. **`run` 什麼時候該停？** 當沒有任何 task 會再進佇列時，`rx.recv()` 應該回 `Err`（channel 關閉）。技巧：`Executor` 自己也持有一份 `Sender`，`run` 一開始就把它 drop 掉——這樣當所有 task 都完成（不再持有 Sender）時，channel 自動關閉，`recv()` 回 `Err`，迴圈結束。

5. **poll 一個 task 的完整動作？** 從佇列取出 `Arc<Task>` → 用它造 `Waker` → 包成 `Context` → 鎖住它的 future（`Mutex`，因為 `Wake::wake` 需要 `Arc<Task>: Send + Sync`）→ `future.as_mut().poll(&mut cx)` → `Ready` 就不管它（讓 Arc drop），`Pending` 就等它之後自己 wake 回來。

6. **撞到 `cannot be shared between threads safely`？** 這是你八成會踩的第一個 error。如果 `Task` 的 future 欄位**沒**用 `Mutex` 包（直接 `future: Pin<Box<dyn Future<...>>>`），在 `task.clone().into()`（造 Waker）那行會爆：

   ```
   error[E0277]: `(dyn Future<Output = ()> + Send + 'static)` cannot be shared between threads safely
      |
   18 |     let _waker: Waker = task.clone().into();
      |                                      ^^^^ ... cannot be shared between threads safely
      = help: the trait `Sync` is not implemented for `(dyn Future ...)`
   ```

   成因：`Arc<T>::into::<Waker>()` 要求 `T: Send + Sync`（Waker 本身是 Send + Sync）。`dyn Future` 不是 `Sync`，所以 `Task` 不是 `Sync`。修法就是用 `Mutex<Pin<Box<dyn Future>>>`——`Mutex<T>` 對任何 `T: Send` 都是 `Sync`（它用鎖提供內部同步）。看到這個 error，去 `Task` 的 future 欄位包上 `Mutex`。

## 實作步驟建議

### Step 1：先讓 `block_on` 跑通單一 future

先不做 spawn/多 task。把 Ch 27 的 `block_on` 搬過來，用 `Wake` trait 造 Waker（不要用 `RawWaker` 的 no_op 版），跑一個 `Counter`。確認 poll 迴圈 + 真 Waker 能動。

### Step 2：定義 `Task` 與讓它實作 `Wake`

`Task` 存 `Mutex<Pin<Box<dyn Future<...>>>>` + `Sender<Arc<Task>>`。實作 `impl Wake for Task { fn wake(self: Arc<Self>) { self.sender.send(self.clone()).ok(); } }`。這是整個 executor 的靈魂：wake = 重新入佇列。

### Step 3：`Executor` 的 `spawn` 與 ready 佇列

`Executor { ready: Receiver<Arc<Task>>, sender: Sender<Arc<Task>> }`。`spawn` 把 future 包成 `Arc<Task>` 塞進佇列。

### Step 4：`run` 主迴圈

drop 掉 executor 自己那份 sender（見卡點 4），然後 `while let Ok(task) = ready.recv()`：造 Waker、poll。`Ready` 不管、`Pending` 等 wake。

### Step 5：測 counter 交錯 + 進階測 timer

spawn 兩個 counter 驗證交錯輸出。進階：實作 `Timer`（背景 thread + wake），spawn 兩個不同時長，驗證按時完成、等待不燒 CPU。

## 完整參考解答

**寫完再看！不要偷看**，否則這個 Part 的畢業考就白考了。

<details>
<summary>點開參考實作（counter 版）</summary>

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};

// ---- Task：一個被 spawn 的 future + 自己回排程的能力 ----
struct Task {
    // Mutex 是因為 Wake 要求 Task: Send + Sync，而 future 本身不 Sync
    future: Mutex<Pin<Box<dyn Future<Output = ()> + Send>>>,
    sender: Sender<Arc<Task>>,
}

impl Wake for Task {
    fn wake(self: Arc<Self>) {
        // 被喚醒 = 把自己塞回 ready 佇列，等 run 迴圈再 poll 我
        self.sender.send(self.clone()).ok();
    }
}

// ---- Executor：一個 ready 佇列 + spawner ----
struct Executor {
    ready: Receiver<Arc<Task>>,
    sender: Sender<Arc<Task>>,
}

impl Executor {
    fn new() -> Self {
        let (sender, ready) = channel();
        Executor { ready, sender }
    }

    fn spawn(&self, fut: impl Future<Output = ()> + Send + 'static) {
        let task = Arc::new(Task {
            future: Mutex::new(Box::pin(fut)),
            sender: self.sender.clone(),
        });
        self.sender.send(task).ok();   // 一 spawn 就進 ready 佇列
    }

    fn run(self) {
        // drop 掉自己那份 sender：當所有 task 完成（不再持有 sender），
        // channel 關閉，recv() 回 Err，迴圈結束
        let Executor { ready, sender } = self;
        drop(sender);
        while let Ok(task) = ready.recv() {
            let waker: Waker = task.clone().into();   // Arc<Task> -> Waker
            let mut cx = Context::from_waker(&waker);
            let mut fut = task.future.lock().unwrap();
            // Pending 就丟著，等它 wake() 自己塞回佇列；Ready 就結束（Arc drop）
            let _ = fut.as_mut().poll(&mut cx);
        }
    }
}

// ---- 自訂 Future：counter，poll N 次，每次 Pending 前 wake 自己 ----
struct Counter { count: u32, limit: u32, name: &'static str }
impl Future for Counter {
    type Output = ();
    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        self.count += 1;
        println!("[{}] poll #{}", self.name, self.count);
        if self.count >= self.limit {
            Poll::Ready(())
        } else {
            cx.waker().wake_by_ref();  // 立刻要求再排一次（塞回佇列）
            Poll::Pending
        }
    }
}

fn main() {
    let ex = Executor::new();
    ex.spawn(Counter { count: 0, limit: 3, name: "A" });
    ex.spawn(Counter { count: 0, limit: 2, name: "B" });
    ex.run();
    println!("all tasks finished");
}
```

真跑，輸出：

```
[A] poll #1
[B] poll #1
[A] poll #2
[B] poll #2
[A] poll #3
all tasks finished
```

**解答說明**：

- **交錯的原因**：A、B 都在 spawn 時進佇列（順序 A、B）。`run` 取出 A，poll #1 → A 呼叫 `wake_by_ref()` 把自己塞回佇列**尾端**（此時佇列是 `[B, A]`）→ 回 Pending。`run` 取出 B，poll #1 → B 塞回尾端（`[A, B]`）→ Pending。如此輪流，所以輸出是 A、B、A、B、A。這證明了「多 task 併發」不是「跑完一個才跑下一個」。
- **為什麼 `Mutex<Pin<Box<...>>>`**：`Wake` trait 要求 `Self: Send + Sync`（因為 `Waker` 是 `Send + Sync`）。`Task` 要 `Sync`，但裡面的 `dyn Future` 不 `Sync`，所以用 `Mutex` 包起來提供 `Sync`。`Pin<Box<>>` 是因為 future 可能 `!Unpin`（Ch 27），poll 前要 pin；`Box` 讓它大小固定、位址穩定。
- **為什麼 `run` 一開始 drop sender**：這是「所有 task 完成後自動停」的關鍵。若不 drop，executor 永遠持有一份 sender，channel 永不關閉，`recv()` 在最後一個 task 完成後會**永久阻塞**——程式 hang。drop 掉後，channel 的存活繫於「還有沒有 task（每個 task 持一份 sender clone）」，最後一個 task 完成、Arc drop、sender drop、channel 關閉、`recv()` 回 Err、乾淨結束。

</details>

<details>
<summary>點開進階實作（加 Timer，真正掛起 + 外部 thread 喚醒）</summary>

```rust
use std::future::Future;
use std::pin::Pin;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::thread;
use std::time::Duration;

struct Task {
    future: Mutex<Pin<Box<dyn Future<Output = ()> + Send>>>,
    sender: Sender<Arc<Task>>,
}
impl Wake for Task {
    fn wake(self: Arc<Self>) { self.sender.send(self.clone()).ok(); }
}

struct Executor { ready: Receiver<Arc<Task>>, sender: Sender<Arc<Task>> }
impl Executor {
    fn new() -> Self { let (s, r) = channel(); Executor { ready: r, sender: s } }
    fn spawn(&self, fut: impl Future<Output = ()> + Send + 'static) {
        let t = Arc::new(Task { future: Mutex::new(Box::pin(fut)), sender: self.sender.clone() });
        self.sender.send(t).ok();
    }
    fn run(self) {
        let Executor { ready, sender } = self;
        drop(sender);
        while let Ok(task) = ready.recv() {
            let waker: Waker = task.clone().into();
            let mut cx = Context::from_waker(&waker);
            let mut fut = task.future.lock().unwrap();
            let _ = fut.as_mut().poll(&mut cx);
        }
    }
}

// Timer：背景 thread 在時間到時 wake，executor 真正掛起（recv 阻塞）而非忙等
struct Timer { state: Arc<Mutex<(bool, Option<Waker>)>> }
impl Timer {
    fn new(ms: u64) -> Self {
        let state = Arc::new(Mutex::new((false, None::<Waker>)));
        let st = state.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(ms));
            let mut g = st.lock().unwrap();
            g.0 = true;                       // 標記完成
            if let Some(w) = g.1.take() { w.wake(); }   // 叫醒 executor
        });
        Timer { state }
    }
}
impl Future for Timer {
    type Output = ();
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        let mut g = self.state.lock().unwrap();
        if g.0 {
            Poll::Ready(())
        } else {
            g.1 = Some(cx.waker().clone());   // 存 Waker，時間到才叫我
            Poll::Pending
        }
    }
}

async fn timed(name: &'static str, ms: u64) {
    println!("[{name}] sleeping {ms}ms");
    Timer::new(ms).await;
    println!("[{name}] woke after {ms}ms");
}

fn main() {
    let ex = Executor::new();
    ex.spawn(timed("fast", 100));
    ex.spawn(timed("slow", 300));
    ex.run();
    println!("done");
}
```

真跑，輸出：

```
[fast] sleeping 100ms
[slow] sleeping 300ms
[fast] woke after 100ms
[slow] woke after 300ms
done
```

**解答說明**：

- 這版證明了「真正掛起」：兩個 task 都 spawn、都 poll 一次（各印 `sleeping`），然後都回 `Pending`——此時 ready 佇列**空了**，`run` 的 `ready.recv()` **阻塞**（thread 進 kernel 睡，CPU 不動）。100ms 後 fast 的背景 thread `wake()`，往 channel 送 task，`recv()` 醒來 poll fast → 這次 `Timer` 的 `completed` 是 true → `Ready`，印 `woke`。slow 同理在 300ms。
- 對比 counter 版：counter 用 `wake_by_ref()` **立刻**塞回佇列（忙碌輪轉，測交錯）；timer 用外部 thread **延遲** wake（真正掛起，測 Waker 的跨 thread 喚醒）。兩個一起跑，你的 executor 就同時驗證了「多 task 交錯」和「Pending 掛起 + 外部喚醒」——這正是 Tokio 核心做的兩件事。
- 這個 executor 和 Tokio 的差距：單執行緒（無 work-stealing）、timer 用 thread-per-timer（Tokio 用一個 timer wheel）、I/O 用 thread 模擬（Tokio 用 epoll）。但**核心迴圈完全相同**：poll → Pending 掛起 → wake 重排 → 重 poll。

</details>

## 額外驗證：多 task 蒐集結果

counter 和 timer 驗證「交錯」和「掛起」。再加一個測試證明「多 task 各自算東西、全部跑完」——spawn 4 個 task，每個 yield 一次（證明真的是 async、有經過 Pending/wake 循環）後把 `i*i` 寫進共享的結果集：

```rust
fn main() {
    let ex = Executor::new();
    let results = Arc::new(Mutex::new(Vec::new()));
    for i in 0..4 {
        let r = results.clone();
        ex.spawn(async move {
            Yield { done: false }.await;      // yield 一次：走一遍 Pending -> wake -> 重 poll
            r.lock().unwrap().push(i * i);
        });
    }
    ex.run();
    let mut v = results.lock().unwrap().clone();
    v.sort();
    println!("collected results: {:?}", v);
}
// Yield：第一次 poll 回 Pending + wake_by_ref，第二次回 Ready
struct Yield { done: bool }
impl Future for Yield {
    type Output = ();
    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<()> {
        if self.done { Poll::Ready(()) }
        else { self.done = true; cx.waker().wake_by_ref(); Poll::Pending }
    }
}
```

真跑（配上 Executor 骨架），輸出：

```
collected results: [0, 1, 4, 9]
```

四個 task 都完成了，`[0, 1, 4, 9]` = `[0², 1², 2², 3²]`。這證明你的 executor 能正確調度任意數量的 task 到完成，且每個都真的經過了 async 的 Pending/wake 循環（`Yield` 強迫它們至少 yield 一次）。如果你的實作只印出部分結果或 hang，回頭檢查 `run` 的結束條件（drop sender）和 Waker 的重入佇列邏輯。

## 測試用例

| 輸入 / 場景 | 預期輸出 | 說明 |
|---|---|---|
| spawn 一個 `Counter{limit:1}` | `poll #1` 後立刻 `all tasks finished` | limit=1 第一次 poll 就 Ready |
| spawn A(limit 3) + B(limit 2) | A、B 交錯，共 5 行 poll | 驗證併發交錯，非序列 |
| spawn 0 個 task 就 `run` | 直接印 `all tasks finished` | drop sender 後 channel 立刻關，recv 回 Err |
| spawn timer(100) + timer(300) | 按 100、300 順序 woke | 驗證外部 thread 喚醒 + 掛起 |
| Counter 的 `poll` **不**呼叫 `wake_by_ref` 就回 Pending | 程式 hang（永久阻塞在 recv） | 踩雷：Pending 不 wake = 死鎖，親自體驗 |

最後一列請一定要試——把 `cx.waker().wake_by_ref();` 註解掉，跑起來程式會卡死。這是 Ch 27 踩雷 3「回 Pending 前一定要安排 wake」的實地驗證，也是手寫 future/executor 最常見的 bug。

## 延伸挑戰（加分）

- **加 timer wheel**：現在每個 `Timer` 開一條 thread（`thread-per-timer`），一萬個 timer 就一萬條 thread，不 scalable。改成一條 timer thread + 一個按到期時間排序的資料結構（`BinaryHeap` 當 min-heap，或分槽的 timer wheel），到期批次 wake。這是 Tokio timer 的真實做法。

- **接真 epoll**：把 timer 換成真 I/O——用 `std` 的非阻塞 `TcpStream`（`set_nonblocking(true)`）+ 直接呼叫 `libc::epoll_create1`/`epoll_ctl`/`epoll_wait`（`unsafe` FFI，Ch 19），做一個「fd 就緒 → wake future」的 reactor。做完你就手刻了一個 Tokio 的最小版，`read().await` 底層走 epoll。這是把 Ch 28「Tokio 底層是 epoll」從讀來的知識變成你寫過的 code。

- **加 `JoinHandle`**：讓 `spawn` 回傳一個能 `.await` 拿 task 結果的 handle（`spawn(fut) -> JoinHandle<T>`，`T` 是 future 的 `Output`）。要用一個 oneshot channel（可以自己用 `Arc<Mutex<Option<T>>>` + Waker 手刻）把結果從 task 傳回 handle。這是 Tokio `spawn` 回傳值的機制。

- **公平性與 budget**：真 executor 會限制單個 task 連續 poll 的次數（budget），避免一個一直 `wake_by_ref` 的 task 餓死別人。給你的 `run` 加一個 per-task poll budget，體會 Tokio 的 "coop" 機制在解什麼問題。

## 自我檢核

- [ ] 能不看解答，說出 `Task` 為什麼要存一個 `Sender`（wake = 把自己塞回佇列）
- [ ] 能解釋 `run` 開頭 drop sender 的作用（讓所有 task 完成後 channel 關閉、乾淨結束），不做會怎樣（hang）
- [ ] 能說出 counter 版（`wake_by_ref` 立刻重排）和 timer 版（外部 thread 延遲 wake）分別在測 executor 的哪個能力
- [ ] 能指出你的 mini executor 和 Tokio 的三個主要差距（單執行緒/timer wheel/epoll）
- [ ] 能說出「Pending 不安排 wake」會導致什麼，並在你自己的 code 裡驗證過那個 hang

寫完這個練習，你不只是「會用」async——你造過那台機器。Part 5 起，課程轉向資安研究：Rust 的安全邊界在哪、unsafe 漏洞怎麼 audit、Rust binary 在反組譯器裡長什麼樣。async 這台狀態機的知識在你逆向 Rust binary、看到編譯器生成的 poll 函式時，還會再回來。

→ [Ch 30 Rust 的安全邊界與威脅模型](./30-security-boundary.md)
