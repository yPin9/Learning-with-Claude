# Ch 28 — Tokio 實戰與 epoll 連結

> **目標**：把 Ch 26–27 手刻的玩具 executor 換成業界標準的生產級 runtime——Tokio。學完你能：用 `#[tokio::main]` 起 runtime、`spawn` task、用 `join!`/`select!` 組合併發、寫一個真的 async TCP echo server，並且**理解 Tokio 底層怎麼用 epoll 把「fd 就緒」翻成「wake future」**——這條線直接接上你既有的 epoll 知識。最後看 async 最致命的生產陷阱：在 async 裡跑阻塞操作會卡死整條 executor thread。

> **環境**：`rustc 1.97.1` (stable)、`tokio 1.53.1`（`features = ["full"]`），x86-64 Linux（WSL2）。每段 code 都在此環境**真跑過**（`cargo run`），輸出照貼。若你的環境裝不了 tokio（無網路），本章的 runtime 核心概念在 Ch 27 的手刻 executor 已驗證，Tokio 版標注會註明。

## 為什麼需要這個？

Ch 27 你手刻的 executor 能跑，但它只能 `block_on` **一個** future、只會用「背景 thread 睡完 wake」模擬 I/O、單執行緒、沒有 task 佇列。要拿去撐一台真的伺服器，你還缺：

- **多 task 排程**：同時跑成千上萬個 future，公平地輪流 poll。
- **真 I/O reactor**：不是「背景 thread 睡」，而是一個 `epoll_wait()` 迴圈，監控所有 socket fd，就緒了才 wake 對應 future。
- **多執行緒 work-stealing**：把 task 分散到多核心，閒的 worker 去偷忙的 worker 的 task。
- **一整套 async I/O 型別**：`TcpListener`、`TcpStream`、`sleep`、`timeout`……全部是 future。

自己把這些做到生產品質是好幾人年的工程。Tokio 就是那個做好了的東西——Rust 生態事實上的 async runtime 標準，`hyper`（HTTP）、`tonic`（gRPC）、大量 CNCF 專案都建在它上面。這章不重新發明它，而是**用你 Ch 26–27 的原理視角去讀它**：你已經知道 executor 迴圈長怎樣，現在看生產級的怎麼把每個部件做大。

## 先建立直覺：Tokio = 你的手刻 executor ×1000

```
     ┌───────────────────────── Tokio Runtime ─────────────────────────┐
     │                                                                  │
     │   Worker thread 0      Worker thread 1     ...  Worker thread N   │
     │   ┌──────────┐         ┌──────────┐            ┌──────────┐      │
     │   │ task 佇列 │◄──steal─│ task 佇列 │            │ task 佇列 │      │
     │   │ poll poll │         │ poll poll │            │ poll poll │      │
     │   └────┬─────┘         └────┬─────┘            └────┬─────┘      │
     │        │                    │                       │            │
     │        └────────────┬───────┴───────────────────────┘            │
     │                     ▼                                            │
     │             ┌───────────────┐   Pending 的 future 在等 I/O        │
     │             │   Reactor     │   ┌───────────────────────────┐    │
     │             │ epoll_wait()  │──►│ fd 就緒 → wake 對應 future  │    │
     │             │ (mio crate)   │   └───────────────────────────┘    │
     │             └───────────────┘                                    │
     └──────────────────────────────────────────────────────────────────┘
```

把 Ch 27 的迴圈放大：多個 worker thread，每個有自己的 task 佇列（就是 Ch 27 那個 ready channel 的強化版），閒的 worker 去偷別人的 task（work-stealing）。所有在等 I/O 的 future，它們的 `Waker` 註冊在一個共用的 **reactor**——reactor 跑 `epoll_wait()`，某個 fd 就緒就 `wake()` 對應 future，把它塞回某個 worker 的佇列。你 Ch 27 手刻的每個部件，這裡都有對應的生產版。

## 第一個 Tokio 程式：#[tokio::main] 與併發

`#[tokio::main]` 是一個 macro：它把你的 `async fn main` 包成「建一個 runtime，然後 `block_on(你的 async main)`」。等價於你手動 `Runtime::new().unwrap().block_on(async { ... })`——就是 Ch 27 那個 `block_on`，只是生產版。

先看併發組合子。`join!` 同時跑多個 future、全部完成才回；`select!` 誰先完成用誰，其餘 drop：

```rust
use tokio::time::{sleep, Duration, Instant};

async fn work(id: u32, ms: u64) -> u32 {
    sleep(Duration::from_millis(ms)).await;   // async sleep：不阻塞 thread
    println!("task {id} done after {ms}ms");
    id
}

#[tokio::main]
async fn main() {
    let start = Instant::now();
    // join!：併發跑三個，總時間 ~= max(100,200,150) 而不是總和 450
    let (a, b, c) = tokio::join!(work(1, 100), work(2, 200), work(3, 150));
    println!("join! results = {a},{b},{c}, elapsed = {:?}", start.elapsed());

    // spawn：把 future 丟到 runtime 背景跑，回一個 JoinHandle
    let start = Instant::now();
    let h1 = tokio::spawn(work(10, 120));
    let h2 = tokio::spawn(work(11, 80));
    let r1 = h1.await.unwrap();
    let r2 = h2.await.unwrap();
    println!("spawn results = {r1},{r2}, elapsed = {:?}", start.elapsed());

    // select!：誰先完成用誰，其餘分支被 drop（取消）
    tokio::select! {
        v = work(20, 50)  => println!("select! winner = {v} (50ms)"),
        v = work(21, 500) => println!("select! winner = {v} (500ms)"),
    }
}
```

真跑，輸出：

```
task 1 done after 100ms
task 3 done after 150ms
task 2 done after 200ms
join! results = 1,2,3, elapsed = 201.275793ms
task 11 done after 80ms
task 10 done after 120ms
spawn results = 10,11, elapsed = 120.681795ms
task 20 done after 50ms
select! winner = 20 (50ms)
```

三個關鍵觀察：

- **`join!` 的 elapsed 是 201ms 不是 450ms**：三個 `work` 是**併發**跑的（不是平行——除非多 worker thread，但這裡它們都在 await sleep，一條 thread 就能交錯推進）。這就是 Ch 26 的 lazy + Ch 27 的 poll 迴圈在起作用：`join!` 把三個 future 交給同一次 poll 迴圈輪流推進，誰的 timer 到了誰醒。
- **`spawn` 的 elapsed 是 120ms**（兩個 task 的 max）：`spawn` 把 future 丟進 runtime 背景，回一個 `JoinHandle`，`.await` 它拿結果。這跟 `join!` 的差別：`spawn` 出去的 task 是**獨立排程單位**，可以被 work-stealing 分到別的 worker thread 真正平行跑；`join!` 的分支綁在當前 task 裡。
- **`select!` 只有 `task 20 done`，沒有 `task 21 done`**：50ms 的分支先完成，`select!` 立刻回，把 500ms 那個 future **drop 掉**——它的 `work(21, 500)` 永遠跑不到 `println!("task 21 done")`。這個「輸掉的分支被取消」是 Ch 29 cancellation 陷阱的引子。

## spawn vs join!：什麼時候用哪個

| | `tokio::spawn` | `join!` / `select!` |
|---|---|---|
| 排程單位 | 獨立 task，可跨 thread 平行 | 綁在當前 task，同一 thread 併發 |
| 生命週期 | 獨立，可比父 task 活更久 | 綁父 task，父結束就結束 |
| 需要 `Send` | 是（可能被 work-steal 到別 thread） | 否（不離開當前 thread） |
| 回傳 | `JoinHandle<T>`，`.await` 取值 | 直接是各 future 的值 |
| 典型場景 | 「處理這條連線」丟背景跑 | 「同時等這幾件事」在原地組合 |

記法：**`spawn` 是「發射後不管，之後再收」，`join!` 是「原地一起等」**。`spawn` 需要 `Send`（Ch 29 會看到 `!Send` future 無法 spawn 的錯誤），`join!` 不用。

## 底層：Tokio 的 reactor 就是 epoll

這是本章對你（懂 epoll 的人）最有價值的一節。你的 async I/O 操作——`TcpStream::read().await`——底層到底發生什麼？

```
  你寫：   let n = socket.read(&mut buf).await;
                          │
   ┌──────────────────────┴───────────────────────────────────────┐
   │ 1. read() 這個 future 被 poll                                  │
   │ 2. 底層對 fd 做非阻塞 read()（O_NONBLOCK）                      │
   │ 3. 若 EAGAIN（還沒資料）→ 把這個 fd + Waker 註冊進 reactor      │
   │    的 epoll instance（epoll_ctl EPOLL_CTL_ADD/MOD），回 Pending │
   │ 4. executor park，reactor thread 跑 epoll_wait()               │
   │ 5. 對端送資料 → kernel 標 fd 可讀 → epoll_wait() 返回           │
   │ 6. reactor 查出「這個 fd 對應哪個 Waker」→ wake()               │
   │ 7. executor 回來重 poll 這個 read() future                     │
   │ 8. 這次非阻塞 read() 拿到資料 → 回 Ready(n)                     │
   └──────────────────────────────────────────────────────────────┘
```

看出來了嗎——**這就是你手寫 epoll reactor 的流程，一比一**。差別只在：C 裡「fd 就緒 → 找出對應的 connection state → 推進 state machine」那一步，Tokio 換成「fd 就緒 → 找出對應的 Waker → wake → executor 重 poll 那台 async 狀態機」。你在 C 裡手動維護的 `fd → conn` 對照表，Tokio 內部是 `fd → Waker` 對照表。狀態機從你手寫變成 rustc 生成（Ch 26），事件通知從你手寫 `switch` 變成 poll 迴圈（Ch 27）。

Tokio 的 reactor 是靠 [`mio`](https://docs.rs/mio) crate 實作的——`mio` 是跨平台的 I/O event 抽象，在 Linux 上就是 `epoll`，在 macOS 上是 `kqueue`，在 Windows 上是 IOCP。所以「Tokio 底層是 epoll」這句話精確的說法是：**在 Linux 上，Tokio 透過 mio 用 epoll（`epoll_create1` + `epoll_ctl` + `epoll_wait`）做 I/O 就緒通知**。這點我沒有在本章對 Tokio 做 strace 驗證，是根據 mio/tokio 的公開架構文件；你想親眼確認，可以對下面的 echo server 跑 `strace -e epoll_create1,epoll_ctl,epoll_wait cargo run`，會看到 `epoll_wait` 迴圈。

> 你的 epoll 底層知識（`systems/kernel_internals`、你自己寫過的 reactor）在這裡完全用得上：edge-triggered vs level-triggered、`EPOLLONESHOT`、`thundering herd`——Tokio 內部（mio）就在處理這些。理解 Tokio 效能特性時，這些概念直接對應。

## 真跑：async TCP echo server

把上面全部串起來——一個真的 TCP echo server + client，全 async：

```rust
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

#[tokio::main]
async fn main() {
    // 綁 127.0.0.1:0，讓 OS 挑一個空 port
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    println!("listening on {addr}");

    // server：每個連線 spawn 一個 task 做 echo（一個連線一個 task，不互相阻塞）
    tokio::spawn(async move {
        loop {
            let (mut sock, peer) = listener.accept().await.unwrap();
            println!("[server] accepted {peer}");
            tokio::spawn(async move {
                let mut buf = [0u8; 1024];
                loop {
                    let n = sock.read(&mut buf).await.unwrap();
                    if n == 0 { break; }  // 對方關閉連線
                    sock.write_all(&buf[..n]).await.unwrap();
                }
            });
        }
    });

    // client：連上去、送、收
    let mut cli = TcpStream::connect(addr).await.unwrap();
    cli.write_all(b"hello epoll").await.unwrap();
    let mut buf = [0u8; 1024];
    let n = cli.read(&mut buf).await.unwrap();
    println!("[client] echoed back: {:?}", std::str::from_utf8(&buf[..n]).unwrap());
}
```

真跑，輸出：

```
listening on 127.0.0.1:33819
[server] accepted 127.0.0.1:44824
[client] echoed back: "hello epoll"
```

`accept().await`、`read().await`、`write_all().await` 每一個都是「非阻塞 syscall + EAGAIN 就註冊進 epoll 等 wake」的 future。整個 server 用**一條**（或少數幾條）thread 就能處理海量連線，因為每個連線在等 I/O 時不佔 thread——它只是一個 Pending 的 future，Waker 掛在 epoll 上。這就是 async 的全部賣點：**用手寫 epoll reactor 的效能，寫線性的 `read/write` code**。

`0u8; 1024` 這個 1024 是 echo buffer 大小，隨便選的合理值（一次 read 最多搬 1KB）；`127.0.0.1:0` 的 `0` 是「讓 OS 挑 port」的慣例，避免測試撞到已佔用的 port。

## task 之間怎麼通訊：async channel 與 timeout

spawn 出去的 task 各跑各的，要協作就得傳訊息。Tokio 的 `tokio::sync` 提供 async 版的 channel——和 `std::sync::mpsc` 的差別是它的 `send`/`recv` 是 `.await`（滿了/空了會讓出，不阻塞 thread）。加上 `timeout`（包住 future，超時就取消），這是 async 程式最常用的兩個工具：

```rust
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout, Duration};

#[tokio::main]
async fn main() {
    // 有界 async channel，容量 8：producer 送、consumer 收
    let (tx, mut rx) = mpsc::channel::<u32>(8);
    tokio::spawn(async move {
        for i in 0..3 {
            tx.send(i).await.unwrap();   // channel 滿了會 await（背壓 backpressure）
            println!("[producer] sent {i}");
        }
    });
    while let Some(v) = rx.recv().await {   // 所有 tx drop 後 recv 回 None，迴圈結束
        println!("[consumer] got {v}");
    }

    // timeout：包住一個 future，超時就 drop 它（取消）、回 Err
    let slow = async { sleep(Duration::from_millis(300)).await; "done" };
    match timeout(Duration::from_millis(100), slow).await {
        Ok(v)  => println!("finished: {v}"),
        Err(_) => println!("timed out after 100ms (slow future was cancelled)"),
    }
}
```

真跑，輸出：

```
[consumer] got 0
[producer] sent 0
[producer] sent 1
[producer] sent 2
[consumer] got 1
[consumer] got 2
timed out after 100ms (slow future was cancelled)
```

幾個要點：

- **容量 8** 是這個有界 channel 的緩衝大小——選 8 是隨意的小值示範背壓。producer 送超過容量、consumer 還沒收時，`send().await` 會**掛起**（backpressure，反壓），避免快的 producer 撐爆記憶體。這是 async channel 相對於「無限緩衝」的價值。
- **輸出交錯**（consumer got 0 在 producer sent 0 之前印）是因為 `send().await` 一放進 channel、consumer 就被 wake 收走了，兩個 task 併發推進，print 順序不保證。
- **`timeout` 就是 `select!` 的糖**：它 `select!` 你的 future 和一個 timer，timer 先到就 drop 你的 future。所以 `slow` 那個 300ms 的 future 在 100ms 時被取消——這正是 Ch 29 cancellation 的機制。`timeout` 回 `Result`：`Ok(值)` 或 `Err(Elapsed)`。

`tokio::sync` 還有 `oneshot`（一次性回傳，spawn task 回結果常用）、`broadcast`（一對多）、`watch`（狀態變更通知）、`Mutex`/`RwLock`（async 鎖，guard 是 Send、等鎖時讓出）。選哪個看通訊形狀，但它們的共同點都是「等待時 `.await` 讓出，不阻塞 worker thread」。

## 生產陷阱：在 async 裡阻塞 = 卡死整條 thread

這是 async Rust 最容易踩、後果最嚴重的坑。**`.await` 讓出控制權，但同步阻塞（`std::thread::sleep`、阻塞 I/O、重 CPU 計算、`std::sync::Mutex` 的鎖競爭）不會**——它們會霸佔 executor 的 worker thread，讓那條 thread 上排隊的**所有其他 task 全部餓死**。

故意示範。用 `current_thread`（單執行緒）runtime 放大效果——一個 task 用 `std::thread::sleep` 卡 300ms，另一個本該 50ms 就醒的 task 被拖累：

```rust
use tokio::time::{sleep, Duration, Instant};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let start = Instant::now();
    let blocker = tokio::spawn(async move {
        println!("[blocker] std::thread::sleep(300ms) -- 卡住整條 executor thread");
        std::thread::sleep(Duration::from_millis(300));   // 錯誤示範！同步阻塞
        println!("[blocker] done at {:?}", start.elapsed());
    });
    let victim = tokio::spawn(async move {
        sleep(Duration::from_millis(50)).await;  // async sleep，本該 50ms 就跑完
        println!("[victim] woke at {:?} (本該 ~50ms)", start.elapsed());
    });
    let _ = tokio::join!(blocker, victim);
}
```

真跑，輸出：

```
[blocker] std::thread::sleep(300ms) -- 卡住整條 executor thread
[blocker] done at 300.171ms
[victim] woke at 351.6532ms (本該 ~50ms)
```

**victim 應該 50ms 醒，實際 351ms 才醒**——被 blocker 的 `std::thread::sleep` 整整拖了 300ms。原因：單執行緒 runtime 只有一條 thread，blocker 一 `std::thread::sleep`，那條 thread 就進 kernel 睡了，executor 沒有 thread 可以去 poll victim。等 blocker 醒了、task 完成，executor 才輪到 victim。在多執行緒 runtime 上這個特定例子會被別的 worker 救回（victim 跑到別條 thread），但如果阻塞的 task 數 ≥ worker 數，一樣全部卡死。

**正確做法**：把阻塞/重 CPU 的工作丟給 `spawn_blocking`——它在一個獨立的 blocking thread pool 上跑，不佔 async worker：

```rust
use tokio::time::{sleep, Duration, Instant};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let start = Instant::now();
    let blocker = tokio::spawn(async {
        // 正確：把阻塞工作丟到 blocking thread pool，不霸佔 async worker
        tokio::task::spawn_blocking(|| {
            std::thread::sleep(Duration::from_millis(300));
        }).await.unwrap();
    });
    let victim = tokio::spawn(async move {
        sleep(Duration::from_millis(50)).await;
        println!("[victim] woke at {:?} (本該 ~50ms)", start.elapsed());
    });
    let _ = tokio::join!(blocker, victim);
    println!("total {:?}", start.elapsed());
}
```

真跑，輸出：

```
[victim] woke at 51.381701ms (本該 ~50ms)
total 300.560103ms
```

victim 這次 **51ms 就醒**——正常了。阻塞工作被隔離到 blocking pool，async worker thread 全程自由，victim 準時被 poll。整個程式仍然要 300ms 才結束（那個阻塞工作本身要 300ms），但它**不再拖累**其他 task。

規則背下來：**async fn 裡只能有 `.await` 和快速的同步 code。任何會阻塞或跑很久的（檔案 I/O 用 `std::fs`、重計算、`std::sync::Mutex` 高競爭、呼叫阻塞的 C library）→ `spawn_blocking`。**

## multi_thread vs current_thread

```rust
#[tokio::main]                                    // 預設 = multi_thread
#[tokio::main(flavor = "current_thread")]         // 單執行緒
#[tokio::main(flavor = "multi_thread", worker_threads = 4)]  // 指定 worker 數
```

預設的 `multi_thread` runtime，worker thread 數等於邏輯 CPU 數。我實測（`Handle::current().metrics().num_workers()`）：這台 16 核機器上預設起 16 個 worker，和 `std::thread::available_parallelism()` 回報的 16 一致。

| flavor | worker thread | work-stealing | task 需 `Send` | 適用 |
|---|---|---|---|---|
| `multi_thread`（預設） | = CPU 數 | 有 | 是 | 伺服器、吞吐量優先 |
| `current_thread` | 1 | 無 | 否＊ | 測試、`!Send` 資料、嵌入、`spawn_local` 場景 |

＊`current_thread` 上用 `tokio::spawn` 仍要 `Send`；要 spawn `!Send` future 得用 `LocalSet` + `spawn_local`（Ch 29 提）。

**work-stealing** 簡介：每個 worker 有自己的本地 task 佇列（快，無鎖）。當一個 worker 的佇列空了，它去**偷**別的 worker 佇列尾端的 task 來跑。這讓 task 自動負載平衡，不需要中央調度瓶頸。代價就是 task 必須 `Send`（因為可能從一條 thread 被偷到另一條）——這直接導致 Ch 29 的 `!Send` future 無法 spawn。

## 踩雷集錦

1. **在 async 裡用 `std::thread::sleep` / 阻塞 I/O / 重計算**：卡死整條 worker thread，餓死同 thread 上所有 task（本章實測）。用 `tokio::time::sleep`（async）、`spawn_blocking`（重工作）、`tokio::fs`（async 檔案 I/O）。

2. **在 async 裡持有 `std::sync::Mutex` 跨 `.await`**：兩個問題。(1) 若鎖競爭激烈，等鎖會阻塞 worker thread。(2) `std::sync::MutexGuard` 不是 `Send`，跨 await 持有會讓 future `!Send`，無法 spawn（Ch 29 詳談）。短臨界區用 `std::sync::Mutex` 但**不要跨 await**；要跨 await 持鎖用 `tokio::sync::Mutex`（它的 guard 是 `Send`，且等鎖時 await 讓出）。

3. **以為 `spawn` 的 task 會等父 task**：`tokio::spawn` 出去的 task 生命週期獨立。父 task（或 `main`）結束，runtime 關閉，還沒跑完的 spawned task 直接被砍。要等它，`.await` 它的 `JoinHandle`。

4. **`select!` 的分支有副作用卻假設它一定完成**：`select!` 只有一個分支贏，其餘被 drop（取消）。如果輸掉的分支做了一半有狀態的事（讀了一半的 buffer），那些狀態沒了。這是 cancellation safety，Ch 29 主題。

5. **`block_on` 巢狀呼叫**：在一個已經跑在 Tokio runtime 上的 async context 裡再呼叫 `Runtime::block_on` 或 `Handle::block_on`，會 panic（`Cannot start a runtime from within a runtime`）。要在 async 裡執行同步等待用別的機制，不要巢狀 runtime。

## 進階：再往深一層

- **`io_uring`**：Tokio 主線目前（`1.x`）在 Linux 用 epoll。`io_uring`（更新的 Linux async I/O 介面，減少 syscall 次數、支援真正的 async 磁碟 I/O）有實驗性的 [`tokio-uring`](https://github.com/tokio-rs/tokio-uring)，但還沒併回主線。這是 async runtime 效能演進的前沿；你懂 epoll 的下一步就是懂 io_uring 的 submission/completion queue 模型。

- **`LocalSet` 與 `spawn_local`**：要在 async 裡用 `!Send` 的資料（`Rc`、某些 C library 的 handle），`multi_thread` runtime 不讓你 spawn。`tokio::task::LocalSet` 提供 `spawn_local`，把 task 綁死在當前 thread（不會被 work-steal），繞過 `Send` 要求。代價是失去多核平行。Ch 29 會再碰。

- **runtime metrics 與 tracing**：生產上診斷「task 為什麼卡住」用 [`tokio-console`](https://github.com/tokio-rs/console)——它像 `top` 但看的是 async task，能抓出「哪個 task 佔著 worker 不放（很可能就是踩了阻塞陷阱）」。搭配 `tracing` crate 做結構化日誌。這是 async 生產維運的標配工具。

## 動手練習

1. 把本章 echo server 的 client 部分改成連續送三筆不同資料、各自收回，確認 echo 正確。理解「一個連線上多次 read/write 都是 await」。

2. 把阻塞陷阱示範的 `#[tokio::main(flavor = "current_thread")]` 改成 `#[tokio::main]`（多執行緒），重跑「BAD」版，觀察 victim 這次可能準時（被別的 worker 救回）。再把 blocker 從 1 個增加到 17 個（超過 worker 數），看 victim 又被拖累——理解「多執行緒不是免死金牌，阻塞 task 數超過 worker 數一樣卡」。

3. 對 echo server 跑 `strace -f -e trace=epoll_create1,epoll_ctl,epoll_wait cargo run 2>&1 | head`，親眼看 Tokio 呼叫 epoll。（`-f` 追蹤子 thread。）

## 本章重點整理

- Tokio 是生產級 executor：`#[tokio::main]` 起 runtime，`spawn` 丟獨立 task，`join!` 原地併發、`select!` 競速（輸家被取消）。核心迴圈就是 Ch 27 手刻版的放大。
- 底層 reactor 透過 `mio` 在 Linux 用 **epoll** 做 I/O 就緒通知：`fd 就緒 → wake 對應 future → executor 重 poll`，和你手寫 epoll reactor 一比一對應。
- **最致命的陷阱**：在 async 裡做同步阻塞或重計算會霸佔 worker thread、餓死其他 task（本章實測 victim 從 50ms 被拖到 351ms）。解法是 `spawn_blocking`。

## 自我檢核

- [ ] 不看筆記，能不能解釋 `tokio::spawn` 和 `join!` 的差別（排程單位、Send 要求）？
- [ ] 如果面試官問「Tokio 底層怎麼實現 async I/O」，你能用你的 epoll 知識回答嗎？（fd 就緒 → wake future）
- [ ] 能說出「為什麼在 async fn 裡呼叫 `std::thread::sleep` 是嚴重 bug」，以及正確做法
- [ ] 知道 `select!` 輸掉的分支會發生什麼（被 drop / 取消）
- [ ] 能解釋 work-stealing 為什麼要求 spawned task 是 `Send`

## 延伸閱讀

### 官方文件

- **[Tokio Tutorial](https://tokio.rs/tokio/tutorial)** — Tokio 官方教學
  - **讀哪裡**：「Spawning」「Shared state」「Channels」「Select」幾章。它用一個 mini Redis 貫穿，從 `spawn` 到 `select!` 到優雅關閉，是本章每個概念的官方擴充版。
  - **前提知識**：本章 + Ch 26–27。讀完本章直接接這份 tutorial 是最順的路徑。

- **[`tokio::runtime` 模組文件](https://docs.rs/tokio/latest/tokio/runtime/index.html)**
  - **讀哪裡**：模組頂部關於 multi-thread vs current-thread scheduler 的說明，以及 `Builder` 的選項。
  - **和本章的關聯**：本章 flavor 表格的權威來源；想調 worker 數、blocking pool 大小時的參考。

### 部落格 / 技術文章

- **[「Making the Tokio scheduler 10x faster」— Carl Lerche](https://tokio.rs/blog/2019-10-scheduler)**（Tokio 作者）
  - **這篇說什麼**：Tokio work-stealing scheduler 的設計與優化——本地佇列、竊取策略、為什麼這樣做快。本章 work-stealing 那段的深度第一手來源。
  - **為什麼值得讀**：作者是 Tokio 主要作者；這是理解「Tokio 為什麼快」最權威的文章，且對懂系統的人剛好。

- **[「Async: What is blocking?」— Alice Ryhl](https://ryhl.io/blog/async-what-is-blocking/)**（Tokio 維護者）
  - **這篇說什麼**：把本章「阻塞陷阱」講到極致——什麼算 blocking、`spawn_blocking` vs `block_in_place`、CPU-bound 該怎麼處理。
  - **讀哪裡**：整篇。這是 async Rust 最重要的實務文章之一，踩過阻塞坑的人都該讀。

### 官方文件 / 原始碼

- **[`mio` crate 文件](https://docs.rs/mio/)**
  - **讀哪裡**：頂層說明 + `Poll` 型別（mio 的 `Poll`，不是 Future 的 poll）。它就是 Tokio 底下那層 epoll/kqueue/IOCP 抽象。
  - **和本章的關聯**：本章說「Tokio 底層用 epoll」，`mio` 就是實作那句話的 crate；想追到 `epoll_wait` 呼叫點就讀它的 `sys/unix`。

Tokio 讓你寫線性的 async code 跑生產級併發，但 async 有一組**只在 Rust 出現、且很反直覺**的陷阱：future 被 drop 就是取消（cancellation）、跨 await 持有 `Rc`/`MutexGuard` 讓 future `!Send`、async 遞迴要 Box。下一章把這些坑一次踩過，讓你看到真實的 E0277 長怎樣。

→ [Ch 29 async 陷阱：cancellation 與 !Send](./29-async-pitfalls.md)
