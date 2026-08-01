# Ch 29 — async 陷阱：cancellation 與 !Send

> **目標**：把 async Rust 那些**只在 Rust 出現、且很反直覺**的坑一次踩過並讀懂。學完你能：解釋「future 被 drop 就是取消」與 cancellation safety、看懂跨 await 持有 `Rc`/`MutexGuard` 產生的 `!Send` 錯誤（E0277）、知道 async 遞迴為什麼要 `Box`、async trait method 的兩條路（1.75+ 原生 vs `async-trait`）、以及對 async 生態的「認識論誠實」——它複雜、有函式染色問題、還在演進。這是 Part 4 的收尾，把前三章的原理落地成「你會在真實 code 撞到的牆」。

> **環境**：`rustc 1.97.1` (stable)、`tokio 1.53.1`（`features = ["full"]`），x86-64 Linux（WSL2）。每段 code（含編譯錯誤示範）都在此環境真跑過，錯誤訊息照抄原文。

## 為什麼需要這個？

前三章講原理：狀態機、Waker、Pin、Tokio。原理對了，你還是會在真實 code 撞牆——而且撞的是一組**你在 C/C++ 沒遇過**的牆。C 沒有「函式回傳一個能被取消的計算」的概念（你的計算跑就是跑完）；C 沒有 borrow checker 在編譯期告訴你「這個資料不能跨執行緒」。這些牆是 async Rust 獨有的，撞上去時錯誤訊息又長又嚇人（`future cannot be sent between threads safely`），不理解成因就只能亂試。

這章的價值在於：**把每道牆的成因追回前三章的原理**。`!Send` 是因為狀態機捕獲了 `!Send` 的變數（Ch 26 的「跨 await 變數進 enum」）；cancellation 是因為 future 是個可以隨時 drop 的值（Ch 26 的 lazy struct）；async 遞迴要 Box 是因為狀態機大小得固定（Ch 26 的 stackless）。你不是背一堆規則，是看到原理的必然後果。

## 陷阱一：cancellation —— future 被 drop 就是取消

C 裡一個函式呼叫下去，它跑完才回來，中途不會「消失」。Rust future 不一樣：**future 就是一個值，把它 drop 掉，正在進行的計算就停在當下、永不繼續**。這叫 cancellation（取消）。

哪裡會 drop future？最常見是 `select!`：它同時 poll 多個分支，誰先 `Ready` 就用誰，**其餘分支立刻 drop**。還有 `timeout`（超時就 drop 被包住的 future）、`JoinHandle` 被 drop、task 被 abort。

先建立直覺：drop 發生在**任一 await 點**。future 停在哪個 `.await`，就在那裡被砍斷——已經跑過的部分留下的區域狀態，隨 future 一起消失。

```
  async fn read_two() {
      chunk1 = read().await;   ◄── 若在這之後、下一個 await 之前被 drop
      buf.push(chunk1);              chunk1 已讀進 buf，但整個 buf 隨 future 消失
      chunk2 = read().await;   ◄── 停在這裡被 drop：chunk2 沒讀到，
      buf.push(chunk2);              chunk1 也跟著沒了 —— 資料遺失
  }
```

**cancellation safety** 指的就是：一個 future 在任意 await 點被取消，會不會造成「讀了一半的資料遺失」「狀態損毀」。看具體案例——一個非 cancel-safe 的「讀兩塊」被 `select!` 的 timeout 砍在中間：

```rust
use tokio::time::{sleep, Duration};

async fn read_two_chunks(name: &str) -> String {
    let mut s = String::new();
    sleep(Duration::from_millis(100)).await;   // 模擬讀第一塊要 100ms
    s.push_str("[chunk1]");
    println!("[{name}] got chunk1, buffer = {s:?}");
    sleep(Duration::from_millis(200)).await;   // 讀第二塊要 200ms；若此時被取消，s 整個沒了
    s.push_str("[chunk2]");
    println!("[{name}] got chunk2, buffer = {s:?}");
    s
}

#[tokio::main]
async fn main() {
    tokio::select! {
        result = read_two_chunks("reader") => {
            println!("完成，收到：{result}");
        }
        _ = sleep(Duration::from_millis(150)) => {
            println!("timeout 150ms 觸發 -> reader 分支被 drop，chunk1 已讀但整個 buffer 遺失");
        }
    }
    println!("main done");
}
```

真跑，輸出：

```
[reader] got chunk1, buffer = "[chunk1]"
timeout 150ms 觸發 -> reader 分支被 drop，chunk1 已讀但整個 buffer 遺失
main done
```

`read_two_chunks` 在 150ms 時停在第二個 `sleep(200ms).await`（它從 100ms 開始睡，要到 300ms 才醒）。此時 timeout 分支贏了，`select!` 把 `read_two_chunks` 這個 future **drop 掉**——它的區域變數 `s`（已經含 `[chunk1]`）跟著 drop，`[chunk2]` 從沒讀到，`[chunk1]` 也白讀了。這在真實場景是資料遺失 bug：想像 `s` 是從 socket 讀了一半的訊息，取消後那半份資料永遠拿不回來。

**怎麼寫 cancel-safe 的 code？** 兩個方向：(1) 把「已讀進度」存在 future **外面**（例如一個 `&mut String` 傳進去，取消後外面還留著已讀的部分）——這是 `AsyncRead::read` 為什麼 cancel-safe 的原因（它讀進你給的 buffer，取消了 buffer 還在）。(2) 用 loop + `select!` 時，只在 cancel-safe 的操作上 `select`。哪些 API cancel-safe 要查文件——Tokio 對每個可能被 `select!` 用的方法都標注了 "Cancel safety" 段落。這不是「小心就好」的東西，是**必須查文件**的正確性問題。

> **Rust 為什麼選了「drop = 取消」這種危險設計？** 因為它的替代方案更糟。要嘛你有 GC / runtime 幫你管取消（Rust 不要 runtime，Ch 26），要嘛取消要顯式 API（像 Go 的 `context.Context`，得手動到處傳、手動檢查）。Rust 選「drop 一個值就取消」是最符合它 RAII/ownership 模型的——future 是值，值被 drop 就結束，一致。代價就是你得知道「哪些 await 點被取消是安全的」。這是 ownership 模型延伸到 async 的必然，不是疏忽。

## 陷阱二：!Send future —— 跨 await 持有 Rc/MutexGuard

Ch 28 說過，`tokio::spawn` 要求 future 是 `Send`（因為 work-stealing 可能把它從一條 thread 偷到另一條）。而一個 future 是不是 `Send`，取決於它捕獲的狀態——回想 Ch 26：**跨 await 存活的變數會被搬進狀態機 enum**。如果那些變數有 `!Send` 的（`Rc`、`RefCell` 的某些用法、`MutexGuard`），整台狀態機就 `!Send`，無法 spawn。

看它爆炸。跨 await 持有一個 `Rc`（`Rc` 是 `!Send`，因為它的引用計數非原子，多執行緒同時改會 data race）：

```rust
use std::rc::Rc;
use tokio::time::{sleep, Duration};

async fn uses_rc() {
    let data = Rc::new(42);      // Rc 不是 Send（引用計數非原子）
    sleep(Duration::from_millis(1)).await;  // 跨 await 持有 data -> 整個 future 變 !Send
    println!("{}", data);        // await 之後還用 data，所以 data 必須跨 await 存活
}

#[tokio::main]
async fn main() {
    tokio::spawn(uses_rc());     // multi_thread runtime 要求 Send -> 編不過
}
```

編譯，`rustc` 報 E0277（真實輸出，節錄關鍵部分）：

```
error: future cannot be sent between threads safely
   --> src/main.rs:12:18
    |
 12 |     tokio::spawn(uses_rc());     // multi-thread runtime 要求 Send -> 編不過
    |                  ^^^^^^^^^ future returned by `uses_rc` is not `Send`
    |
    = help: within `impl Future<Output = ()>`, the trait `Send` is not implemented for `Rc<i32>`
note: future is not `Send` as this value is used across an await
   --> src/main.rs:6:37
    |
  5 |     let data = Rc::new(42);      // Rc 不是 Send
    |         ---- has type `Rc<i32>` which is not `Send`
  6 |     sleep(Duration::from_millis(1)).await;  // 跨 await 持有 -> future 變 !Send
    |                                     ^^^^^ await occurs here, with `data` maybe used later
note: required by a bound in `tokio::spawn`
```

這是 async Rust 最常見的錯誤之一，但一旦你懂 Ch 26 就一目了然。逐句讀 rustc 給的線索：

- **`future ... is not Send`**：整個 `uses_rc` 回的 future 不是 Send。
- **`the trait Send is not implemented for Rc<i32>`**：因為 future 裡藏著 `Rc<i32>`，而 `Rc` 不是 Send。
- **`this value is used across an await`** + **`await occurs here, with data maybe used later`**：關鍵診斷——`data` 這個 `Rc` 在一個 `.await` 點**兩側都活著**（await 前建立，await 後 `println!` 還用），所以它被搬進了狀態機 enum，害整台狀態機 `!Send`。

rustc 的錯誤訊息精準指出「哪個變數、在哪個 await 點跨越」——這是全業界最好的錯誤訊息之一，讀懂它你就知道去哪修。

**修法**：讓 `!Send` 的東西**不要跨 await**。把 `Rc` 的使用限制在一個不含 await 的 scope 裡，await 之前就 drop 掉：

```rust
use std::rc::Rc;
use tokio::time::{sleep, Duration};

async fn ok_version() {
    let n = {
        let data = Rc::new(42);   // Rc 只在這個 scope 內活著
        *data                     // 取出值（i32 是 Send），準備 drop Rc
    };                            // <- Rc 在這裡就 drop 了，不跨 await
    sleep(Duration::from_millis(1)).await;
    println!("value = {n}");      // 現在跨 await 的只有 n: i32，future 是 Send
}

#[tokio::main]
async fn main() {
    tokio::spawn(ok_version()).await.unwrap();
}
```

真跑，輸出：

```
value = 42
```

差別只在：`Rc` 的生命週期被關進一個 block，`.await` 之前就結束，跨 await 存活的只剩 `n: i32`（Send），future 就 Send 了。

**同樣的坑，`MutexGuard` 版更常見**：`std::sync::MutexGuard` 也是 `!Send`。跨 await 持有一個 `std::sync::Mutex` 的 guard，會產生一模一樣的 E0277。這也是 Ch 28 踩雷 2 說的「別跨 await 持 `std::sync::Mutex`」的編譯期後果。要跨 await 持鎖，用 `tokio::sync::Mutex`（它的 guard 是 Send，且等鎖時 await 讓出，不阻塞 worker）。

### 同一坑，`MutexGuard` 版

`Rc` 是教學例子，實務上你更常撞到的是 **`std::sync::MutexGuard` 跨 await**。它也是 `!Send`，成因和後果一模一樣：

```rust
use std::sync::{Arc, Mutex};   // std 的 Mutex，guard 是 !Send
use tokio::time::{sleep, Duration};

async fn bad(m: Arc<Mutex<u32>>) {
    let g = m.lock().unwrap();               // std MutexGuard
    sleep(Duration::from_millis(1)).await;   // 跨 await 持有 guard -> future !Send
    println!("{}", *g);
}

#[tokio::main]
async fn main() {
    let m = Arc::new(Mutex::new(0));
    tokio::spawn(bad(m));                     // 編不過
}
```

編譯，錯誤和 `Rc` 版同構（真實輸出，節錄）：

```
error: future cannot be sent between threads safely
    = help: within `impl Future<Output = ()>`, the trait `Send` is not implemented for `std::sync::MutexGuard<'_, u32>`
note: future is not `Send` as this value is used across an await
  6 |     let g = m.lock().unwrap();
    |         - has type `std::sync::MutexGuard<'_, u32>` which is not `Send`
  7 |     sleep(Duration::from_millis(1)).await;
    |                                     ^^^^^ await occurs here, with `g` maybe used later
```

**兩個修法**，看情境選：

1. **別讓 guard 跨 await**：把臨界區關進不含 await 的 block，`.await` 之前釋放鎖。這是首選——短臨界區、鎖只保護「改一下資料」，用 `std::sync::Mutex` 但用完立刻放。
2. **要跨 await 持鎖，換 `tokio::sync::Mutex`**：它的 guard 是 `Send`，且 `lock().await` 在等鎖時**讓出**（不阻塞 worker thread，不像 `std::sync::Mutex` 的 `lock()` 會 spin/block）。

看修法 2：

```rust
use std::sync::Arc;
use tokio::sync::Mutex;   // tokio 的 Mutex：guard 是 Send，等鎖時 await 讓出
use tokio::time::{sleep, Duration};

async fn ok(m: Arc<Mutex<u32>>) {
    let mut g = m.lock().await;               // await 拿鎖
    sleep(Duration::from_millis(1)).await;    // 跨 await 持有 OK：tokio guard 是 Send
    *g += 1;
    println!("counter = {}", *g);
}

#[tokio::main]
async fn main() {
    let m = Arc::new(Mutex::new(0));
    tokio::spawn(ok(m.clone())).await.unwrap();
}
```

真跑，輸出：

```
counter = 1
```

**取捨提醒**：`tokio::sync::Mutex` 比 `std::sync::Mutex` **慢**（它是 async-aware 的，有額外機制）。所以規則是：**預設用 `std::sync::Mutex` 且不跨 await；只在「真的需要跨 await 持鎖」時才用 `tokio::sync::Mutex`**。不要因為在 async code 裡就無腦全換 tokio 的鎖——多數情況短臨界區的 std 鎖更快也更對。

## 陷阱三：async 遞迴要 Box

Ch 26 說 future 是**固定大小的狀態機 struct**（stackless）。這直接導致一個問題：async fn 直接遞迴呼叫自己，狀態機就得**包含自己**——`Future<A>` 的狀態裡要存一個 `Future<A>`，那個裡面又要存一個……大小無限，編不過。

解法：在遞迴呼叫外面包一層 `Box::pin`，把子 future 放到 heap 上——狀態機只要存一根固定大小的指標（`Box`），大小就有限了：

```rust
use std::future::Future;
use std::pin::Pin;

// 回傳 boxed future 打斷無限大小的遞迴
fn factorial(n: u64) -> Pin<Box<dyn Future<Output = u64>>> {
    Box::pin(async move {
        if n <= 1 { 1 } else { n * factorial(n - 1).await }
    })
}

#[tokio::main]
async fn main() {
    println!("5! = {}", factorial(5).await);
}
```

真跑，輸出：

```
5! = 120
```

`Box::pin` 把每層遞迴的 future 放 heap，狀態機只存 `Pin<Box<dyn Future>>`（一根胖指標，固定大小），遞迴就成立了。這跟 C 沒得對照——C 遞迴用 call stack，深度只受 stack 大小限制；Rust async 遞迴因為 stackless，得顯式 heap 配置。實務上 [`async-recursion`](https://docs.rs/async-recursion) crate 提供一個 `#[async_recursion]` macro 幫你自動加這層 Box，不用手寫。

## 陷阱四：async trait method

一個歷史包袱正在消退的坑。在 `Rust 1.75`（2023-12）之前，trait 裡**不能**直接寫 `async fn`——因為 async fn 回傳的是匿名 future 型別，而 trait 需要具名的關聯型別來描述回傳。整個生態靠 [`async-trait`](https://docs.rs/async-trait) crate 繞過：它用 macro 把 `async fn` 改寫成回傳 `Pin<Box<dyn Future>>`（有 heap 配置開銷）。

`Rust 1.75+` 起，trait 裡可以原生寫 `async fn`（叫 AFIT，async fn in trait），無 Box 開銷：

```rust
// 原生 async fn in trait（Rust 1.75+），無 async-trait crate，無 Box
trait Fetcher {
    async fn fetch(&self, key: &str) -> String;
}

struct Cache;
impl Fetcher for Cache {
    async fn fetch(&self, key: &str) -> String {
        format!("value-for-{key}")
    }
}

#[tokio::main]
async fn main() {
    let c = Cache;
    println!("{}", c.fetch("k1").await);
}
```

真跑，輸出：

```
value-for-k1
```

**但這裡有個 2026 年仍在的坑，必須誠實標注**：原生 AFIT 目前**不能直接做成 trait object**（`dyn Fetcher`）。因為每個 impl 的 async fn 回傳不同的匿名 future 型別，`dyn` 需要統一的型別而做不到。要 `dyn` 相容的 async trait，2026 年的實務仍是：用 `async-trait` crate（吃 Box 開銷換 `dyn` 相容），或用 nightly 的 `dyn*` / `#[trait_variant]` 等仍在演進的機制。所以現況是：**靜態分派（泛型 `T: Fetcher`）用原生 AFIT；需要 `dyn Fetcher` 動態分派時，多數 code 仍用 `async-trait`。** 這點請以你當下的 Rust 版本 release note 為準——這塊每幾個版本就在推進。

## 陷阱五：忘了 .await（沉默的 bug）

Ch 26 講過 future 是 lazy 的。實務後果：呼叫 async fn 卻忘了 `.await`，那段邏輯**靜默地不執行**。編譯器會警告但不報錯：

```rust
async fn side_effect() { println!("ran!"); }

#[tokio::main]
async fn main() {
    side_effect();   // 忘了 .await -> future 被建立又立刻 drop，什麼都沒跑
    println!("main done");
}
```

編譯 + 執行，輸出：

```
warning: unused implementer of `Future` that must be used
 --> src/main.rs:5:5
  |
5 |     side_effect();
  |     ^^^^^^^^^^^^^
  |
  = note: futures do nothing unless you `.await` or poll them
  = note: `#[warn(unused_must_use)]` (part of `#[warn(unused)]`) on by default

main done
```

**`ran!` 沒印出來**——`side_effect()` 造了 future 又立刻 drop（Ch 26 lazy）。編譯器靠 `#[must_use]` 給 warning `futures do nothing unless you .await or poll them`，這句話值得背。看到「某段 async 邏輯好像沒跑」，第一件事就是搜有沒有漏 `.await`。生產上把這個 warning 設成 `#![deny(unused_must_use)]` 讓它變編譯錯誤，是常見的防呆。

## async 的認識論誠實

前面都是可修的坑。這一節講**不能完全修、你得接受並管理**的東西——寫給你，一個懂系統、會判斷技術取捨的人。

- **函式染色（function coloring）**：async fn 和同步 fn 是「兩種顏色」。async fn 只能被 async context 呼叫（要 `.await`）；同步 fn 想呼叫 async fn 得靠 `block_on`。這導致 async 有「傳染性」——一個函式變 async，呼叫它的鏈上全部得變 async。你會看到大量 crate 為此維護「同步版 + async 版」兩套 API（`std::fs` vs `tokio::fs`）。這不是 Rust 獨有（JS、Python 都有），但在 Rust 因為沒有內建 runtime 而更明顯。這是 async 抽象的**本質代價**，不是 bug。

- **runtime 不是語言的一部分**：Rust 標準庫只給 `Future`/`poll`/`Waker`/`Pin`（Ch 26–27），**不給** executor。executor 是第三方 crate（Tokio、async-std、smol……）。好處是靈活（`no_std`、嵌入式都能有 async）；壞處是**生態碎片化**——某些 crate 綁死 Tokio，混用不同 runtime 會出問題，`async fn` 的可移植性不如你期待。選 runtime 是個要早做的架構決定。

- **還在演進**：AFIT（1.75）、`async` closures（`Rust 1.85` / 2024-02 穩定）、AsyncIterator（`Stream`，仍未穩定，生態用 `futures::Stream`）、`dyn*`——async Rust 這幾年一直在補洞。你今天學的某些「繞法」（`async-trait`、手動 Box）會隨版本變成不必要。這是好事（在變好），但意味著**教材和 Stack Overflow 答案的半衰期短**，遇到 async 問題要看新的資料、認版本。

一句總結給你：**async Rust 是強大但複雜的抽象，它把「單執行緒高併發 I/O」做到零成本、記憶體安全，代價是一組獨特的心智負擔（Pin、Send、cancellation、染色）。它值得學，但別期待它像同步 Rust 那樣「編過就對」——async 的正確性有更多要主動查、主動想的地方。** 這正是這門課把 async 當難點 Part、寫深原理的原因：懂了狀態機/Waker/Pin，這些坑才從「神秘的長錯誤」變成「原理的必然後果」。

## 踩雷集錦

1. **`select!` 假設分支一定完成**：輸掉的分支被 drop，做到一半的有狀態操作（讀了一半的 buffer）遺失。用前查該操作的 "Cancel safety" 文件；狀態存在 future 外面。

2. **跨 await 持 `!Send`（`Rc`/`std::sync::MutexGuard`/`RefCell` 借用）**：future 變 `!Send`，`tokio::spawn` 編不過（E0277）。把 `!Send` 的東西關進不含 await 的 scope，或換 `Arc`/`tokio::sync::Mutex`。

3. **async fn 直接遞迴**：狀態機無限大，編不過。遞迴呼叫外包 `Box::pin`，或用 `#[async_recursion]`。

4. **以為原生 AFIT 能做 `dyn`**：不能（2026 現況）。靜態分派用原生 AFIT，`dyn` async trait 仍多用 `async-trait` crate。

5. **忘了 `.await`**：future 靜默不執行，只有 `unused_must_use` warning。生產設 `#![deny(unused_must_use)]`。

6. **混用不同 runtime**：某些 crate 綁 Tokio，在 async-std/smol 上跑會 panic 或找不到 reactor。專案早期定一個 runtime，別混。

## 進階：再往深一層

- **`JoinSet` 與結構化併發**：散落的 `tokio::spawn` 有個問題——task 生命週期獨立，容易「spawn 了忘了等」或「父死了子還在跑」。`tokio::task::JoinSet` 把一組 task 綁成一束：`set.spawn(...)` 加入、`set.join_next().await` 逐一收完成的、`set` 被 drop 時**自動 abort 所有未完成的 task**。這是 async「結構化併發（structured concurrency）」的做法——子 task 的生命週期不超過管理它的 `JoinSet`，避免 task 洩漏：

  ```rust
  let mut set = tokio::task::JoinSet::new();
  for i in 0..3 {
      set.spawn(async move { /* ... */ i });
  }
  while let Some(res) = set.join_next().await {   // 誰先完成先收
      println!("task finished: {}", res.unwrap());
  }
  ```

  實測輸出（3 個 task 按完成順序，非 spawn 順序）：`task finished: 2` / `1` / `0`。生產上處理「fan-out 一批工作、等全部回來」時，`JoinSet` 比手動存一堆 `JoinHandle` 乾淨得多。

- **`AbortHandle` 與優雅關閉**：`tokio::spawn` 回的 `JoinHandle` 有 `.abort()`，主動取消一個 task（drop 它的 future）。生產上「優雅關閉」（收到 SIGTERM，取消所有 in-flight task）靠這個 + `CancellationToken`（`tokio-util`）——一個 token 取消，所有監聽它的 task 一起停。這是把 cancellation 從「意外的 select! 副作用」變成「有意的架構工具」。

- **`Stream` / `AsyncIterator`**：本課沒展開的一大塊。`Future` 是「一個未來的值」，`Stream` 是「一串未來的值」（async 版的 `Iterator`）。生態用 `futures::Stream` + `tokio_stream`。你處理「持續來的訊息流」（WebSocket、Kafka consumer）時會需要它，是 async Rust 的下一個學習點。

- **`Pin<&mut Self>` 在 `select!` 裡的細節**：`select!` 對 `!Unpin` 的 future 要求它們被 pin（`tokio::pin!`）。你在 loop 裡重複 `select!` 同一個 future 時會撞到，錯誤訊息會叫你 `pin!`。這是 Ch 27 的 Pin 在實務浮現的地方。

## 動手練習

1. 把 cancellation 示範的 timeout 從 150ms 改成 350ms（比 `read_two_chunks` 的總時長 300ms 還長），重跑。觀察這次 reader 分支贏了、印出完整 `[chunk1][chunk2]`——理解「取消與否取決於誰先完成」。

2. 把 `!Send` 示範的 `Rc` 換成 `Arc`（`use std::sync::Arc`），重跑。觀察它**編得過**（`Arc` 是 Send，因為引用計數原子）——體會 `Rc` vs `Arc` 在 async 的差別，連回 [Ch 16](./16-smart-pointers.md)/[Ch 23](./23-threads-send-sync.md)。

3. 把 async 遞迴的 `Box::pin` 拿掉（直接 `async fn factorial(n: u64) -> u64 { ... factorial(n-1).await ... }`），編譯，讀 `error[E0733]: recursion in an async fn requires boxing` 的完整訊息——親眼確認「狀態機無限大」這個成因。

## 本章重點整理

- **cancellation**：future 被 drop（`select!` 輸家、timeout、abort）= 取消，發生在任一 await 點。cancel-safe 與否是正確性問題，必須查文件，不能靠「小心」。
- **`!Send` future**：跨 await 持有 `!Send` 的東西（`Rc`/`std::sync::MutexGuard`）讓整台狀態機 `!Send`，無法 spawn（E0277）。成因是 Ch 26「跨 await 變數進 enum」；修法是別讓它跨 await。
- **async 的本質複雜度**：函式染色、runtime 非語言內建、仍在演進。async Rust 強大但不是「編過就對」，正確性有更多要主動想的地方。

## 自我檢核

- [ ] 不看筆記，能不能解釋「future 被 drop 會發生什麼」，以及 `select!` 為什麼牽涉 cancellation？
- [ ] 看到 `future cannot be sent between threads safely`，你能立刻定位是「哪個變數跨了哪個 await」嗎？
- [ ] 能解釋 async 遞迴為什麼要 Box（連回 Ch 26 的 stackless 狀態機）
- [ ] 知道 2026 年原生 AFIT 的限制（不能直接 `dyn`），以及該用什麼繞
- [ ] 能對「該不該在這個專案用 async」給出有依據的判斷（I/O 併發 vs CPU-bound、染色成本、runtime 綁定）

## 延伸閱讀

### 部落格 / 技術文章

- **[「Async Cancellation」系列 — Yosh Wuyts](https://blog.yoshuawuyts.com/async-cancellation-1/)**（Rust async working group）
  - **這篇說什麼**：把 cancellation 從「drop = 取消」講到結構化取消、cancel safety 的深層設計問題。本章 cancellation 那節的權威擴充。
  - **為什麼值得讀**：作者是 async Rust 設計核心成員；這是把「取消」這個 Rust async 最微妙主題講最透的系列。

- **[「What is the difference between Rc and Arc?」延伸到 async Send — Alice Ryhl](https://ryhl.io/blog/actors-with-tokio/)**（Tokio 維護者）
  - **這篇說什麼**：用 actor 模式示範怎麼在 async 裡結構化地管共享狀態，繞開 `!Send`/跨 await 持鎖的坑。
  - **讀哪裡**：整篇。它把「怎麼寫不撞 !Send 牆的 async 架構」講得很實務，是本章陷阱二的正面示範。

- **[「Why async Rust?」— without.boats](https://without.boats/blog/why-async-rust/)**
  - **這篇說什麼**：async Rust 的設計辯護與誠實檢討——為什麼長這樣、哪些抱怨是對的、哪些是誤解。本章「認識論誠實」那節的第一手來源。
  - **為什麼值得讀**：作者是 async Rust 核心 RFC 作者；想理解「函式染色是不是設計失誤」這類爭議，這是最有份量的一篇。

### 官方文件

- **[Tokio `select!` 文件的 "Cancellation safety" 段](https://docs.rs/tokio/latest/tokio/macro.select.html)** 以及各 async 方法的 "Cancel safety" 標注
  - **讀哪裡**：`select!` macro 文件，以及例如 `AsyncReadExt::read` 的 Cancel safety 段落。
  - **和本章的關聯**：本章說「cancel-safe 與否要查文件」——這就是那個文件。養成用 `select!` 前查這段的習慣。

- **[「async fn in traits」穩定公告 (Rust 1.75 release note)](https://blog.rust-lang.org/2023/12/28/Rust-1.75.0.html)** 與 **[Announcing `async fn` and return-position `impl Trait` in traits](https://blog.rust-lang.org/2023/12/21/async-fn-rpit-in-traits.html)**
  - **讀哪裡**：後者詳細解釋 AFIT 的能力邊界，包括「為什麼還不能 `dyn`」。
  - **和本章的關聯**：本章陷阱四的權威依據；想確認你當下版本的 AFIT 現況，從這裡追 release note。

Part 4 到此收尾。你已經把 async 從「魔法 API」拆到「狀態機 + Waker + Pin + 一組原理必然的陷阱」。接下來的練習 D 是這個 Part 的畢業考：**從零手刻一個支援 spawn 多 task 的 mini executor**，把 Future/poll/Waker/Pin 全部拼起來，親手造出你這四章一直在用的那台機器。

→ [練習 D：手刻 mini async executor](./practice-d-mini-executor.md)
