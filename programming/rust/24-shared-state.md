# Ch 24 — 共享狀態：Mutex/RwLock/Arc

> **目標**：把「多執行緒共享**可變**狀態」這件事做對。搞懂 `Arc<Mutex<T>>` 這個經典組合——`Arc` 共享所有權、`Mutex` 提供互斥 + 內部可變性，並理解 Rust 把「鎖保護的資料」**綁進型別**（`Mutex<T>` 而非「一個 mutex + 一個沒關聯的 data」）帶來的關鍵優勢：**忘記鎖 = 拿不到資料**。對照 C++ 的 `shared_ptr<pair<mutex, data>>` 手動配對。真跑多執行緒累加計數器。搞懂 **Mutex 中毒（poisoning）**、`RwLock`（多讀單寫）、`MutexGuard` 的 **RAII 自動解鎖**（對照 C++ `lock_guard` 與 C 手動 `pthread_mutex_unlock`）。誠實面對：**Rust 不防 deadlock**——真跑一個鎖順序死結。最後補 `Condvar`、`Barrier` 與 `parking_lot` 生態。

> **環境**：Rust `rustc 1.97.1`（stable，edition 2015）在 x86-64 Linux（WSL2）；C++ 對照為 C++17/`std::mutex`。所有 Rust 執行輸出、panic 訊息、deadlock（用 `timeout` 觀察卡死）都是本機真跑，非推測。多執行緒輸出可能不定序，會標注。

## 為什麼需要這個？

[Ch 23](./23-threads-send-sync.md) 教會你 Send/Sync 怎麼在編譯期擋掉 data race——但那章的例子全是「各 thread 算各的、最後 join 收結果」，沒有真正**共享可變狀態**。真實世界不是這樣：多個 worker 要往同一個計數器加、同一個 cache 塞、同一個 job queue 拿。這一刻你就撞上並發的核心矛盾——**多個 thread 想同時改同一塊記憶體**，而 data race 又是 UB。

C/C++ 的答案是 mutex：改之前拿鎖、改完放鎖。你熟這套。但 C/C++ 有一個結構性問題：**鎖和它保護的資料是分開宣告的兩個東西**。

```c
pthread_mutex_t counter_lock;   // 鎖在這
long counter;                    // 資料在那，兩個沒有語言層級的關聯
```

編譯器**不知道** `counter_lock` 保護的是 `counter`。你可以拿了鎖不碰 `counter`（浪費），也可以不拿鎖直接改 `counter`（data race，編譯器一句話不說）。「拿對鎖才碰對資料」是一條**口頭約定**，靠 code review 和紀律維持。大型 C/C++ 專案裡，「這個欄位到底哪個鎖保護」的知識散落在註解、文件、老工程師的腦子裡，新人一改就 race。

Rust 的答案不一樣：它把鎖和資料**綁成一個型別** `Mutex<T>`——資料 `T` 住在 `Mutex` 裡面，你**只有拿到鎖（得到 `MutexGuard`）才能碰到 `T`**。「忘記拿鎖」在 Rust 不是紀律問題，是**你根本拿不到那個資料**。這一章就是把這個機制、它的邊界（poisoning、deadlock）講透。

## 先建立直覺

C/C++ 的鎖與資料：兩個獨立的東西，靠約定連起來。Rust 的 `Mutex<T>`：資料被鎖包在裡面，拿鎖是取資料的**唯一入口**。

```
   C/C++（鎖與資料分離）              Rust（資料綁在鎖裡）

   ┌─────────┐   ┌─────────┐         ┌───────────────────────┐
   │ mutex   │   │ data    │         │   Mutex<T>            │
   │         │   │         │         │   ┌───────────────┐   │
   └─────────┘   └─────────┘         │   │  data: T      │   │
        ↑             ↑              │   │  （鎖住時外面  │   │
     「說好」了才配對                 │   │   碰不到）     │   │
     忘了拿鎖照樣能碰 data           │   └───────────────┘   │
     → data race，編譯器沉默          └───────────────────────┘
                                            ↑
                                    lock() → MutexGuard
                                    是碰到 data 的唯一路徑
                                    沒 lock 就沒 guard 就沒 data
```

心智圖像：`Mutex<T>` 是一個「上鎖的保險箱，資料在箱子裡」。`lock()` 是「開箱」，回給你一把 `MutexGuard`——這把 guard 既是「箱子開著的證明」，也是「透過它才能摸到裡面資料」的把手。guard 一旦離開作用域被 drop，箱子自動上鎖（RAII）。你**沒有辦法**繞過 guard 直接摸資料——編譯器讓「摸資料」在型別上依賴「持有 guard」。

`Arc` 則解決另一個正交的問題：**這個保險箱要被多個 thread 共同擁有**。單一 owner 的 `Box` 不行（所有權只能給一個 thread），要 `Arc`（原子引用計數，[Ch 16](./16-smart-pointers.md)、[Ch 23](./23-threads-send-sync.md)）——多個 thread 各持一份 `Arc`，指向同一個 `Mutex<T>`，最後一個 drop 時才釋放。合起來就是 `Arc<Mutex<T>>`：**Arc 管「誰擁有」，Mutex 管「誰在改」**。

> 如果你對 `Arc` 為什麼能跨 thread（`Rc` 不行）還不熟，回看 [Ch 23 — 哪些型別不是 Send](./23-threads-send-sync.md) 的 `Rc` vs `Arc` 那節。

## `Arc<Mutex<T>>`：經典組合真跑

先把最經典的場景跑起來：10 個 thread，每個往同一個計數器加 1000 次，最後應該剛好 10000。

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let counter = Arc::new(Mutex::new(0));
    let mut handles = Vec::new();
    for _ in 0..10 {
        let c = Arc::clone(&counter);
        let h = thread::spawn(move || {
            for _ in 0..1000 {
                let mut n = c.lock().unwrap();  // 拿鎖，得到 MutexGuard
                *n += 1;
            }   // n（MutexGuard）在這 drop，自動解鎖
        });
        handles.push(h);
    }
    for h in handles {
        h.join().unwrap();
    }
    println!("counter = {}", *counter.lock().unwrap());
}
```

真跑 3 次（本機，**每次都剛好 10000**——因為 mutex 把加法序列化了）：

```
counter = 10000
counter = 10000
counter = 10000
```

對比 [Ch 23](./23-threads-send-sync.md) 那個 C 的 `counter++` 無鎖版本（每次印不同、都小於預期）——那裡丟更新，這裡不丟。差別就是 `lock()`：每次 `*n += 1` 之前都拿到鎖，同時只有一個 thread 能改。

拆解幾個關鍵點：

- `c.lock()` 回 `Result<MutexGuard, PoisonError>`（`.unwrap()` 假設沒中毒，稍後談中毒）。`MutexGuard` 是一個智慧指標，`Deref` 到 `T`（[Ch 12](./12-core-traits.md)），所以 `*n += 1` 直接改到裡面的 `i32`。
- **沒有顯式的 unlock**。guard `n` 在每次迴圈 iteration 結尾離開作用域被 drop，`Drop` 實作裡自動解鎖。這就是 RAII，下一節細講。
- `Arc::clone(&counter)` 不是 deep copy——只是 refcount +1（原子操作），10 個 thread 共享同一個 `Mutex<i32>`。

「鎖保護的資料綁進型別」的實際體感：你想改 `counter` 裡的值，**唯一路徑**就是 `.lock()`。沒有 `counter.value += 1` 這種繞過鎖的寫法可寫——那個 `0` 住在 `Mutex` 裡，`lock()` 是取它的唯一入口。忘記鎖在 Rust 不是「風險」，是「編譯器讓你根本寫不出繞過鎖的 code」。

## 對照 C++：手動配對 vs 型別綁定

C++ 標準做法，`std::mutex` + 被保護的資料，兩個成員分開放：

```cpp
#include <mutex>
#include <thread>
#include <vector>
#include <iostream>

struct Counter {
    std::mutex m;     // 鎖
    long value = 0;   // 資料——語言不強制「碰 value 前要鎖 m」
};

int main() {
    Counter c;
    std::vector<std::thread> ts;
    for (int i = 0; i < 10; i++) {
        ts.emplace_back([&c] {
            for (int j = 0; j < 1000; j++) {
                std::lock_guard<std::mutex> g(c.m);  // RAII 鎖
                c.value += 1;
                // 也可以在這裡「忘記」上一行，直接 c.value += 1 → data race，編譯器不管
            }
        });
    }
    for (auto& t : ts) t.join();
    std::cout << "value = " << c.value << "\n";
    return 0;
}
```

C++ 的 `std::lock_guard` 已經是 RAII（比 C 的手動 `pthread_mutex_unlock` 進步），但它防的是「忘記 unlock」，**不防「忘記 lock」**。上面那行註解點出核心問題：你可以在迴圈裡直接寫 `c.value += 1` 而不先建 `lock_guard`，編譯器完全不會抱怨——因為 `c.m` 和 `c.value` 是兩個沒有型別關聯的成員。「碰 `value` 前要鎖 `m`」是你自己的約定。

Rust 的 `Mutex<T>` 把這個約定變成型別強制：

| 面向 | C++ `mutex` + data（分離） | Rust `Mutex<T>`（綁定） |
|---|---|---|
| 鎖與資料的關係 | 兩個獨立成員，靠約定 | 資料住在 `Mutex` 裡 |
| 忘記 lock | 編譯過，data race（UB） | **拿不到資料**（沒 guard 就沒 `T`） |
| 忘記 unlock | `lock_guard` 防（RAII）；手動 `lock()` 會忘 | `MutexGuard` drop 自動解鎖，忘不了 |
| 「哪個鎖保護哪個資料」 | 散落註解/文件/腦子 | **型別本身寫明**：`Mutex<Data>` |
| 拿鎖後給錯資料 | 可能（拿了 `m` 卻改別的 struct 的 data） | 不可能（guard 只通到自己那份 `T`） |

這是 Rust 並發設計的一個核心洞見：**把不變量（invariant）編進型別，而不是靠紀律維持**。「碰這個資料前必須持有這個鎖」在 C++ 是註解，在 Rust 是 `Mutex<T>` 的型別結構。大型專案裡這個差別是災難與否的分水嶺。

> C++20 有 `std::synchronized_value`（實驗/提案）和一些第三方（如 folly 的 `Synchronized<T>`）在模仿 Rust 這個「鎖綁資料」的模式，說明這個設計確實優越——只是 Rust 把它做進了標準庫的預設型別。

## `MutexGuard` 是 RAII：對照 C++ lock_guard 與 C 手動 unlock

`MutexGuard` 的自動解鎖是 [Ch 12](./12-core-traits.md) `Drop` trait 的直接應用。看它保護你的三個層次，由差到好：

**C——手動 unlock，最容易忘：**

```c
pthread_mutex_lock(&m);
data += 1;
if (error) return;          // 忘了 unlock！鎖永遠不放 → deadlock
pthread_mutex_unlock(&m);
```

`pthread_mutex_lock`/`unlock` 是配對的手動呼叫。任何提前 return、break、goto、exception（C++）都可能跳過 unlock，鎖就洩漏，下一個要它的 thread 永遠等。這是 C 並發最常見的 bug 之一。

**C++——`lock_guard` 用 RAII 防忘記 unlock：**

```cpp
{
    std::lock_guard<std::mutex> g(m);   // 建構時 lock
    data += 1;
    if (error) return;                   // g 解構時自動 unlock，OK
}   // g 出作用域 → unlock
```

`lock_guard` 建構時鎖、解構時解鎖。無論怎麼離開作用域（return、exception），解構子一定跑，unlock 保證執行。這已經很好——但如前述，它**不防忘記 lock**。

**Rust——`MutexGuard` 同樣 RAII，且是碰資料的唯一入口：**

```rust
{
    let mut n = c.lock().unwrap();   // lock，回 guard
    *n += 1;
    // 任何 return / ? / panic，n 都會被 drop → 自動 unlock
}   // n 出作用域 → Drop → unlock
```

`MutexGuard` 和 `lock_guard` 在「RAII 自動解鎖」這點等價。Rust 多的那一層是：**你沒有 guard 就碰不到 `*n`**——因為那個資料在 `Mutex` 裡，`lock()` 產生的 guard 是唯一的 `Deref` 入口。C++ 的 `data` 和 `m` 分離，你能繞過 `g` 直接碰 `data`；Rust 不能。

一個實際的 RAII 差異：guard 的存活期決定臨界區長度。想**提早解鎖**，Rust 用 `drop(guard)` 顯式丟掉，或用 block 縮小作用域：

```rust
let result = {
    let n = c.lock().unwrap();
    *n * 2                        // 算完，guard 在 block 結尾 drop
};   // 這裡已解鎖
// 下面的重活不持鎖
heavy_computation(result);
```

這對照 C++ 縮小 `lock_guard` 作用域、或 C 手動安排 unlock 位置——邏輯一樣，但 Rust 用作用域自然表達，比手動 unlock 不易錯。

## Mutex 中毒（poisoning）：持鎖 thread panic 之後

Rust 的 `Mutex` 有一個 C++ 沒有的機制：**poisoning（中毒）**。若一個 thread 在**持有鎖時 panic**，這個 mutex 就被標記為「中毒」——之後任何 `lock()` 會回 `Err(PoisonError)`。

為什麼要這樣？因為持鎖 thread panic 意味著它**可能改資料改到一半**——臨界區被中斷，`Mutex` 保護的資料可能處於**不一致狀態**（例如更新了 `len` 還沒更新 `capacity`）。Rust 的態度是：與其讓後續 thread 拿到可能損壞的資料繼續跑，不如明確告訴它們「這裡出過事，資料可能壞了，你自己決定要不要信」。

真跑：一個 thread 持鎖時 `push` 一個元素後 panic，看主 thread 再 `lock()` 拿到什麼。

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let data = Arc::new(Mutex::new(vec![1, 2, 3]));
    let d = Arc::clone(&data);

    // 這個 thread 持鎖時 panic -> 毒化 mutex
    let h = thread::spawn(move || {
        let mut guard = d.lock().unwrap();
        guard.push(4);
        panic!("持鎖時炸了");
    });
    let _ = h.join();   // 吞掉 panic

    // 現在主 thread 再拿鎖：回 Err（poisoned）
    let result = data.lock();
    match result {
        Ok(g) => println!("拿到鎖: {:?}", *g),
        Err(poisoned) => {
            println!("Mutex 中毒了！");
            let g = poisoned.into_inner();   // 仍可強行取出資料
            println!("中毒但強取: {:?}", *g);
        }
    }
}
```

真跑（本機，stderr 的 panic 訊息 + stdout 的處理）：

```
thread '<unnamed>' (292027) panicked at t24b.rs:12:9:
持鎖時炸了
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
Mutex 中毒了！
中毒但強取: [1, 2, 3, 4]
```

兩個觀察：

1. `lock()` 回 `Err`——mutex 中毒了。這就是為什麼平常寫 `.lock().unwrap()`：`unwrap` 遇到中毒會 panic，等於「上游有 thread 掛了，我也不裝沒事」。
2. `poisoned.into_inner()` 仍能**強行取出**資料，印出 `[1, 2, 3, 4]`——那個 panic 前的 `push(4)` 確實生效了。中毒不代表資料不見，代表「資料可能不一致，你要自己確認能不能用」。這裡我們判斷 `push` 是原子的、資料沒壞，於是 `into_inner` 強取。

對照 C++：`std::mutex` **沒有中毒機制**。持鎖 thread 若 throw exception，`lock_guard` 的解構子照樣 unlock（RAII），下一個 thread 拿到鎖、拿到**可能損壞的資料**，一聲不吭繼續跑。Rust 的 poisoning 是一道額外的安全網：panic 跨鎖傳播成「這鎖可疑」的訊號。

> **認識論誠實**：poisoning 在 Rust 社群一直有爭議，被批評為「多數情況只是噪音」——很多人 panic 就是要整個程式掛掉，中毒檢查是累贅。所以有 `Mutex::clear_poison`（1.77+）可清中毒、`parking_lot::Mutex`（後述）**根本不做 poisoning**。這是一個「安全網 vs 噪音」沒有定論的設計選擇；知道它存在、知道 `into_inner`/`clear_poison` 能繞過即可。

## `RwLock`：多讀單寫

`Mutex` 是「同時只一個 thread 能進臨界區」，不分讀寫。但很多場景是**讀遠多於寫**（設定、cache、路由表）——如果讀也要互斥，多個純讀 thread 排隊就浪費了。`RwLock`（讀寫鎖）放寬：**多個讀者可同時持讀鎖，但寫者獨佔**。

```rust
use std::sync::{Arc, RwLock};
use std::thread;

fn main() {
    let data = Arc::new(RwLock::new(0));
    let mut handles = Vec::new();

    // 3 個讀者可同時持讀鎖
    for i in 0..3 {
        let d = Arc::clone(&data);
        handles.push(thread::spawn(move || {
            let r = d.read().unwrap();
            println!("讀者 {} 看到 {}", i, *r);
        }));
    }
    // 1 個寫者獨佔寫鎖
    {
        let d = Arc::clone(&data);
        handles.push(thread::spawn(move || {
            let mut w = d.write().unwrap();
            *w += 100;
            println!("寫者寫入 {}", *w);
        }));
    }
    for h in handles { h.join().unwrap(); }
    println!("最終 = {}", *data.read().unwrap());
}
```

真跑（本機，讀者/寫者的**相對順序不定**——取決於誰先搶到鎖；這次讀者都先跑）：

```
讀者 0 看到 0
讀者 1 看到 0
讀者 2 看到 0
寫者寫入 100
最終 = 100
```

`read()` 回 `RwLockReadGuard`（可多個同時存在），`write()` 回 `RwLockWriteGuard`（獨佔）。對照 C 的 `pthread_rwlock_t` / C++17 的 `std::shared_mutex`（`shared_lock` 讀、`unique_lock` 寫）——概念一樣，Rust 一樣把資料綁進 `RwLock<T>`。

`RwLock` vs `Mutex` 的取捨要誠實：

| | `Mutex<T>` | `RwLock<T>` |
|---|---|---|
| 並發度 | 讀寫都互斥 | 多讀可並發，寫獨佔 |
| 適用 | 讀寫差不多、臨界區短 | 讀 >> 寫 |
| 開銷 | 較低（單一狀態） | 較高（要追讀者計數） |
| 寫者飢餓 | 無此問題 | **可能**：讀者不斷來，寫者一直等 |

**別無腦選 `RwLock`**——它的內部狀態比 `Mutex` 複雜、單次上鎖開銷更高，只有在「讀真的遠多於寫、且臨界區夠長讓並發讀有意義」時才划算。臨界區極短（如加一個數）時，`Mutex` 甚至可能更快。而且標準庫 `RwLock` 的讀寫優先策略**依賴作業系統**（Linux 上可能 writer-preferring 也可能不是），寫者飢餓是真實風險。

## 誠實：Rust 不防 deadlock

這是本章最重要的認識論誠實一段。[Ch 23](./23-threads-send-sync.md) 說 Rust 保證「無 data race」——但**它不保證無 deadlock**。deadlock 是**邏輯錯誤**，不是記憶體安全問題，型別系統管不到。

經典的鎖順序死結：thread 1 先鎖 A 再要 B，thread 2 先鎖 B 再要 A，兩邊各持一把、各等對方的，永遠僵住。

```rust
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

fn main() {
    let a = Arc::new(Mutex::new(0));
    let b = Arc::new(Mutex::new(0));

    let a1 = Arc::clone(&a); let b1 = Arc::clone(&b);
    let t1 = thread::spawn(move || {
        let _ga = a1.lock().unwrap();          // t1 先鎖 a
        println!("t1 鎖了 a，等 b");
        thread::sleep(Duration::from_millis(100));
        let _gb = b1.lock().unwrap();          // 再鎖 b -> 卡住
        println!("t1 拿到 b（不會印）");
    });

    let a2 = Arc::clone(&a); let b2 = Arc::clone(&b);
    let t2 = thread::spawn(move || {
        let _gb = b2.lock().unwrap();          // t2 先鎖 b
        println!("t2 鎖了 b，等 a");
        thread::sleep(Duration::from_millis(100));
        let _ga = a2.lock().unwrap();          // 再鎖 a -> 卡住
        println!("t2 拿到 a（不會印）");
    });

    t1.join().unwrap();
    t2.join().unwrap();
    println!("結束（deadlock 時到不了這）");
}
```

真跑（本機，用 `timeout 3` 觀察它卡死）：

```
t1 鎖了 a，等 b
t2 鎖了 b，等 a
exit=124 (124=timeout=deadlock)
```

兩個 thread 各印了「鎖了 X，等 Y」就**再也沒下文**——`拿到`、`結束` 那幾行永遠印不出來，程式卡死，`timeout` 3 秒後強制殺掉（exit 124）。`rustc` 編譯這段**完全沒有 warning**。這就是 fearless concurrency 的邊界：**它保證你不會 data race，不保證你不會 deadlock**。deadlock 的四個必要條件（互斥、持有並等待、不可搶佔、環狀等待——[Coffman conditions](https://en.wikipedia.org/wiki/Deadlock)）在 Rust 一樣成立，避免它一樣要靠你——**全域固定的鎖順序**是最常用的紀律（永遠先鎖 A 再鎖 B）。

另一個更隱蔽的自我死結——**同一個 thread 對同一個 `Mutex` 連鎖兩次**（標準 `Mutex` 不可重入）：

```rust
use std::sync::Mutex;

fn main() {
    let m = Mutex::new(5);
    let g1 = m.lock().unwrap();
    println!("第一次鎖: {}", *g1);
    // 同一 thread 沒放 g1 就再鎖 -> 自己等自己
    let g2 = m.lock().unwrap();
    println!("第二次鎖: {}", *g2);
}
```

真跑：印出 `第一次鎖: 5` 後**卡死**（`timeout` exit 124）——第二次 `lock()` 在等第一把鎖放，但第一把鎖握在同一個 thread 手裡、正卡在第二次 `lock()`，自己等自己。Rust 標準 `Mutex` **不是可重入的（reentrant/recursive）**（對照 `PTHREAD_MUTEX_RECURSIVE`），這個坑在把「已持鎖的函式」再呼叫「也要拿同一把鎖的函式」時很容易踩到。

**Rust 對 deadlock 能給的**：deadlock 通常是「乾淨的卡死」而非記憶體損壞——它不會像 C 的 data race 那樣把資料改壞、產生詭異結果，而是明確地停在那，比較好 debug（`gdb` 看 backtrace、或 `parking_lot` 的 deadlock detection feature）。但「比較好查」不等於「不會發生」。

## `Condvar` 與 `Barrier`

兩個常用的同步原語，簡介。

**`Condvar`（條件變數）**：讓 thread「等到某條件成立」再繼續，而不是忙輪詢。它必須配一個 `Mutex`——`wait` 會**原子地釋放鎖並睡眠**，被 `notify` 喚醒後**重新拿鎖**。這對照 C 的 `pthread_cond_wait`/`pthread_cond_signal`，語意幾乎一樣。

```rust
use std::sync::{Arc, Mutex, Condvar};
use std::thread;

fn main() {
    // (Mutex 保護的旗標, Condvar)
    let pair = Arc::new((Mutex::new(false), Condvar::new()));
    let p2 = Arc::clone(&pair);

    let worker = thread::spawn(move || {
        let (lock, cvar) = &*p2;
        let mut ready = lock.lock().unwrap();
        while !*ready {
            // wait 會原子地：釋放鎖 + 睡，被喚醒後重新拿鎖
            ready = cvar.wait(ready).unwrap();
        }
        println!("worker: 收到通知，開工");
    });

    thread::sleep(std::time::Duration::from_millis(50));
    {
        let (lock, cvar) = &*pair;
        let mut ready = lock.lock().unwrap();
        *ready = true;
        cvar.notify_one();
        println!("main: 已通知");
    }
    worker.join().unwrap();
}
```

真跑：

```
main: 已通知
worker: 收到通知，開工
```

注意那個 `while !*ready` **迴圈**——不能寫成 `if`。因為 `Condvar` 有**假喚醒（spurious wakeup）**：`wait` 可能在沒人 `notify` 的情況下自己醒來（作業系統層級的特性，C 的 `pthread_cond_wait` 也一樣）。所以每次醒來要**重新檢查條件**，不成立就繼續 `wait`。這是 C 老手應該熟的坑，Rust 沒改變這個事實。

**`Barrier`（屏障）**：讓 N 個 thread「都到齊」才一起放行——常用於分階段的並行計算（所有 thread 完成階段一，才一起進階段二）。

```rust
use std::sync::{Arc, Barrier};
use std::thread;

fn main() {
    let barrier = Arc::new(Barrier::new(3));
    let mut handles = Vec::new();
    for i in 0..3 {
        let b = Arc::clone(&barrier);
        handles.push(thread::spawn(move || {
            println!("thread {} 階段一完成", i);
            b.wait();   // 三個都到齊才一起放行
            println!("thread {} 進入階段二", i);
        }));
    }
    for h in handles { h.join().unwrap(); }
}
```

真跑（本機，**每個階段內順序不定**，但「所有階段一」一定在「任何階段二」之前）：

```
thread 0 階段一完成
thread 1 階段一完成
thread 2 階段一完成
thread 2 進入階段二
thread 0 進入階段二
thread 1 進入階段二
```

三行「階段一」全部印完，才開始印「階段二」——`b.wait()` 擋住前兩個到達的 thread，直到第三個也到，三個一起放行。對照 C 的 `pthread_barrier_wait`。

## `parking_lot`：外部 crate 生態

標準庫的 `Mutex`/`RwLock`/`Condvar` 夠用，但生態裡有一個幾乎是事實標準的替代品：[`parking_lot`](https://docs.rs/parking_lot)（**外部 crate**）。它重新實作了這些原語，賣點：

- **更小**：`parking_lot::Mutex` 只有 1 byte（標準庫的在某些平台包了作業系統的 mutex，更大）。
- **更快**：無競爭時是純使用者態的 atomic 操作（類似 futex 的快路徑），不進 kernel。
- **無 poisoning**：`parking_lot::Mutex::lock()` 直接回 guard，不回 `Result`——不做中毒檢查，用起來少一層 `.unwrap()`。（對「poisoning 是噪音」派的人是優點。）
- **可選 deadlock 檢測**：開 feature 後能在執行期偵測死結。
- 額外提供公平鎖、可重入鎖等變體。

```rust
// Cargo.toml: parking_lot = "0.12"
use parking_lot::Mutex;

let m = Mutex::new(0);
let mut g = m.lock();   // 注意：沒有 .unwrap()，直接回 guard
*g += 1;
```

> **未實測**：本段 `parking_lot` code 需要外部 crate + `cargo`，本章的驗證環境用單檔 `rustc`，未實際跑此段。API 形態依 `parking_lot` 0.12 官方文件（見延伸閱讀）；`lock()` 不回 `Result` 是其文件明載的設計。要用請 `cargo add parking_lot` 後自行驗證。

什麼時候換 `parking_lot`：熱路徑上鎖頻繁、想要更小的鎖、嫌 poisoning 煩、需要 deadlock 檢測。什麼時候留標準庫：不想加依賴、poisoning 的安全網你要、標準庫已夠快。多數專案標準庫就夠；`parking_lot` 是「有明確理由再換」的優化選項。

## 對比與取捨

| 原語 | 用途 | C/C++ 對照 | 關鍵注意 |
|---|---|---|---|
| `Mutex<T>` | 互斥保護可變資料 | `std::mutex`+data / `pthread_mutex` | 資料綁型別；會中毒；不可重入 |
| `RwLock<T>` | 多讀單寫 | `std::shared_mutex` / `pthread_rwlock` | 讀>>寫才划算；可能寫者飢餓 |
| `Arc<T>` | 跨 thread 共享所有權 | `shared_ptr`（原子 refcount） | 只共享，不提供互斥 |
| `Condvar` | 等條件成立 | `pthread_cond`/`std::condition_variable` | 配 Mutex；`while` 防假喚醒 |
| `Barrier` | N thread 到齊放行 | `pthread_barrier` | 分階段並行 |
| `MutexGuard` | RAII 鎖 handle | `lock_guard`/`unique_lock` | drop 自動解鎖；`drop()` 提早放 |

整體取捨：Rust 的共享狀態原語在**安全性**上勝過 C/C++（鎖綁資料、忘鎖拿不到資料、poisoning 安全網、RAII 強制解鎖），代價是**多一點型別體操**（`Arc<Mutex<T>>` 這串包裝、`.lock().unwrap()` 的樣板）和 **poisoning 的爭議性噪音**。但它**不**在 deadlock 上給你任何額外保證——那仍是你的責任，和 C/C++ 一樣要靠鎖順序紀律。

## 踩雷集錦

1. **以為 Rust 會防 deadlock**：**不會**。Rust 保證無 data race（[Ch 23](./23-threads-send-sync.md)），但 deadlock 是邏輯錯誤，型別系統管不到（真跑鎖順序死結，`rustc` 零 warning）。避免靠**全域固定鎖順序**，和 C/C++ 一模一樣。

2. **同一 thread 對同一 `Mutex` 連鎖兩次**：標準 `Mutex` **不可重入**，第二次 `lock()` 自己等自己 → 卡死（真跑）。常見於「持鎖的函式呼叫也要拿同鎖的函式」。解法：重構臨界區、或傳 `&mut T` 進去而非重新鎖。

3. **臨界區裡拿著 guard 做重活/呼叫外部**：guard 沒 drop 前鎖一直握著，臨界區裡 `sleep`、做 I/O、呼叫可能再拿鎖的函式，都會拖長持鎖時間甚至 deadlock。用 block 或 `drop(guard)` **盡早解鎖**，把重活移出臨界區。

4. **`Condvar` 用 `if` 而非 `while` 檢查條件**：`wait` 有**假喚醒**——可能沒 notify 也醒。必須 `while !條件 { wait }` 迴圈重檢，`if` 會漏。C 的 `pthread_cond_wait` 同坑，這不是 Rust 特有。

5. **無腦全用 `RwLock` 以為一定比 `Mutex` 快**：`RwLock` 內部狀態更複雜、單次上鎖開銷更高，只有「讀 >> 寫且臨界區夠長」才划算。臨界區極短時 `Mutex` 可能更快。且標準庫 `RwLock` 讀寫優先策略依賴 OS，可能**寫者飢餓**。

6. **對 `.lock().unwrap()` 的 unwrap 沒意識到是在賭「不中毒」**：`lock()` 回 `Result` 是因為 mutex 可能中毒（持鎖 thread panic）。`unwrap` 的語意是「上游若掛了、鎖中毒，我也跟著 panic」——多數情況合理，但你若要在中毒後恢復，得 `match` 處理 `PoisonError`（用 `into_inner` 強取資料）。

## 進階：再往深一層

**`Mutex<T>` 的內部：它是 `UnsafeCell` + OS 原語 + poisoning 旗標。** `Mutex<T>` 內部有一個 `UnsafeCell<T>`（[Ch 16](./16-smart-pointers.md)、[Ch 20](./20-memory-model-ub.md) 的內部可變性地基——唯一能在 `&` 之下合法產生 `&mut` 的型別）、一個作業系統的鎖原語（Linux 上是 futex-based）、和一個 poisoning 旗標。`lock()` 拿到 OS 鎖後，`MutexGuard` 透過 `UnsafeCell::get()` 把 `*mut T` 轉成 `&mut T` 交給你——這步是 `unsafe`，但被 `Mutex` 的「同時只一個 guard」保證了 soundness。這就是「安全抽象包裝 unsafe」（[Ch 21](./21-unsafe-abstractions.md) 手刻 Vec 的同款哲學）在標準庫的體現：`Mutex` 對外 100% safe，內部靠 `unsafe` + 不變量。

**為什麼 `Mutex<T>` 需要 `T: Send` 才 `Sync`？** `Mutex<T>: Sync`（能跨 thread 共享 `&Mutex<T>`）的條件是 `T: Send`。因為透過共享的 `&Mutex<T>`，不同 thread 會**輪流拿到 `&mut T`**——等於 `T` 的值在 thread 間被移動存取，所以 `T` 必須 Send。這是 [Ch 23](./23-threads-send-sync.md) Send/Sync 推導規則在標準庫的一個精妙應用；`Mutex` 的 `unsafe impl Sync for Mutex<T> where T: Send` 就是這個條件的落地。

**面試常問**：「Rust 的 `Mutex` 和 C++ 的 `std::mutex` 最大的設計差別是什麼？」——標準答案：Rust 把**被保護的資料綁進 `Mutex<T>` 型別**，資料住在鎖裡，`lock()` 得到的 `MutexGuard` 是碰資料的唯一入口——所以「忘記拿鎖」在 Rust 是「拿不到資料」（編譯期擋），在 C++ 是 data race（`mutex` 和 data 是分離的兩個東西，靠約定）。附帶差異：Rust 有 poisoning（持鎖 panic 標記中毒），C++ 沒有。能講清「鎖綁資料」這個結構差異，代表你懂 Rust 並發安全的核心設計，不是背 API。

**再深一層**：「`Arc<Mutex<T>>` 為什麼是兩層包裝，能不能合成一層？」——`Arc` 管**所有權共享**（誰擁有這塊、何時釋放），`Mutex` 管**並發存取**（誰此刻能改），這是兩個正交的問題。`Arc<T>` 只給你 `&T`（共享不可變），不能改；`Mutex<T>` 給你可變但只有一個 owner。要「多 owner + 可變」就得疊：`Arc` 讓多 thread 各持一份、`Mutex` 讓它們安全輪流改。標準庫沒有合成的單一型別，因為分開更清楚地表達了「這兩件事是分開的」。

## 動手練習

1. **重現計數器**：把 `Arc<Mutex<i32>>` 那個 10-thread 計數器打進去跑，確認每次都 10000。再故意把 `let mut n = c.lock().unwrap();` 那行的 `.lock().unwrap()` 想辦法「繞過」——你會發現**寫不出**繞過鎖直接改的 code（`0` 住在 `Mutex` 裡，`lock` 是唯一入口）。體會「鎖綁資料」。

2. **觸發並處理中毒**：跑本章的 poisoning 例子，確認 `lock()` 回 `Err`、`into_inner` 能強取 `[1,2,3,4]`。再把主 thread 的 `match` 改成 `.lock().unwrap()`，看它因中毒也跟著 panic——理解 `.unwrap()` 在賭什麼。

3. **製造 deadlock 再解掉**：跑鎖順序死結（記得 `timeout` 否則卡住終端）。然後修它——讓 t1 和 t2 **都先鎖 a 再鎖 b**（統一鎖順序），確認死結消失、程式正常結束。體會「Rust 不防 deadlock，靠鎖順序紀律」。

4. **`RwLock` 讀寫並發**：改本章 `RwLock` 例子，讓讀者在讀鎖裡 `sleep` 一段時間，觀察多個讀者是否**真的同時**在睡（並發讀），而寫者要等所有讀者放鎖才進得去。

## 本章重點整理

- **`Arc<Mutex<T>>` = Arc 管共享所有權 + Mutex 管互斥可變性**，兩者正交。Rust 把「鎖保護的資料」**綁進 `Mutex<T>` 型別**：資料住鎖裡，`lock()` 得到的 `MutexGuard` 是碰資料的唯一入口——**忘記鎖 = 拿不到資料**（編譯期擋），對照 C++ `mutex`+data 分離、忘鎖 = data race。真跑計數器每次都 10000（mutex 序列化）。
- **`MutexGuard` 是 RAII**：drop 時自動解鎖，對照 C++ `lock_guard`（防忘 unlock）、C 手動 `pthread_mutex_unlock`（易忘）。多的一層：Rust 你**沒 guard 就碰不到資料**。想提早解鎖用 `drop(guard)` 或 block 縮小作用域。
- **Mutex 中毒（poisoning）**：持鎖 thread panic → mutex 標記中毒 → 之後 `lock()` 回 `Err`（真跑）。因為臨界區被中斷、資料可能不一致。`.lock().unwrap()` 等於「上游掛了我也不裝沒事」；`into_inner`/`clear_poison` 可繞過。C++ `std::mutex` 無此機制。
- **`RwLock`（多讀單寫）**、**`Condvar`（等條件，配 Mutex，`while` 防假喚醒）**、**`Barrier`（N thread 到齊放行）**——都真跑，都對照 pthread 對應原語。`RwLock` 別無腦選（開銷更高、可能寫者飢餓）。
- **誠實：Rust 不防 deadlock**。fearless concurrency 只保證無 data race，deadlock 是邏輯錯誤、型別系統管不到（真跑鎖順序死結 + 同 thread 重複鎖的自我死結，`rustc` 零 warning）。避免靠**全域固定鎖順序**，和 C/C++ 一樣。`parking_lot`（外部 crate）提供更小更快、無 poisoning、可選 deadlock 檢測的替代原語。

## 自我檢核

- [ ] 面試問「Rust `Mutex` 和 C++ `std::mutex` 最大設計差別」，能答「Rust 把資料綁進 `Mutex<T>`，`lock` 是碰資料唯一入口，忘鎖 = 拿不到資料（編譯期擋）；C++ 鎖與資料分離靠約定，忘鎖 = data race」。
- [ ] 不看筆記，能解釋 `Arc<Mutex<T>>` 為什麼要兩層（Arc 管所有權共享、Mutex 管並發存取，正交），以及為什麼不能合成一層。
- [ ] 能解釋 Mutex 中毒是什麼、何時發生（持鎖 panic）、為什麼要有（臨界區中斷 → 資料可能不一致），以及 `.lock().unwrap()` 的 `unwrap` 在賭什麼。
- [ ] 能說清「Rust 不防 deadlock」並舉一個鎖順序死結，講出避免方法（全域固定鎖順序），以及為什麼標準 `Mutex` 不可重入會造成自我死結。
- [ ] 知道 `Condvar` 為什麼要配 `while` 迴圈檢查條件（假喚醒），以及 `RwLock` 相對 `Mutex` 的取捨（讀>>寫才划算、可能寫者飢餓）。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / Spec

- **[std::sync::Mutex 文件](https://doc.rust-lang.org/std/sync/struct.Mutex.html)** 與 **[std::sync::RwLock](https://doc.rust-lang.org/std/sync/struct.RwLock.html)**
  - **讀哪裡**：`Mutex` 頁的「Poisoning」段（中毒的權威定義與 `into_inner`/`clear_poison` 用法）、`lock`/`try_lock` 的語意；`RwLock` 頁關於讀寫優先與飢餓的說明。
  - **學到什麼**：本章 poisoning、`RwLock` 取捨那幾節的權威來源；標準庫對「讀寫優先依賴平台」的官方措辭。
  - **前提**：懂本章 Mutex/RwLock 基本用法。當工具書查。

- **《The Rust Programming Language》(The Book) — 第 16 章「Shared-State Concurrency」** — Steve Klabnik & Carol Nichols（線上免費）
  - **讀哪裡**：16.3「Shared-State Concurrency」整節——用 `Arc<Mutex<T>>` 計數器（和本章第一個例子幾乎一樣）建立直覺。
  - **學到什麼**：本章計數器那節的入門版；The Book 把「為什麼要 `Arc` 又要 `Mutex`」講得很慢很清楚，本章節奏快時的補課。
  - **前提**：無；這是最基礎的講法。

### 書籍

- **《Rust Atomics and Locks》— 第 1 章「Basics of Rust Concurrency」與第 9 章「Building Our Own Locks」** — Mara Bos（O'Reilly, 2023，線上免費 marabos.nl/atomics）
  - **這本書的定位**：Rust 並發的權威單本書，作者 Mara Bos 是 Rust library team leader。第 1 章講 `Mutex`/`RwLock`/`Arc`/`Condvar` 的完整語意，第 9 章教你**從 futex 手刻一個 Mutex**——看懂 `Mutex` 內部到底是什麼。
  - **讀哪幾章**：第 1 章對應本章全部原語；第 9 章是本章「進階」那節（Mutex 內部 = UnsafeCell + OS 原語）的完整展開。第 4–8 章是 Ch 25 的 atomics 主參考。
  - **前提**：懂本章 + Ch 23。這是本課 Part 4 並發部分最推薦的單本延伸。

### 技術文章

- **[parking_lot 文件與 README](https://docs.rs/parking_lot/latest/parking_lot/)** — Amanieu d'Antras（外部 crate 官方文件）
  - **這篇說什麼**：本章 `parking_lot` 那節的權威來源——為什麼 `parking_lot::Mutex` 只有 1 byte、無競爭時純使用者態、不做 poisoning（`lock()` 不回 `Result`）、如何開 deadlock detection feature。
  - **讀哪裡**：crate 首頁的「Features」列表 + `Mutex` 的文件；對照標準庫 `Mutex` 看差異。
  - **為什麼值得讀**：`parking_lot` 是 Rust 並發生態幾乎事實標準的替代品，作者也是標準庫並發原語的重要貢獻者。想知道「標準庫 Mutex 的實作取捨」，看它的對照最直接。
  - **前提**：懂本章標準庫 Mutex；用它要會 `cargo add`。

搞懂了鎖怎麼用、怎麼綁資料、deadlock 為什麼型別系統擋不掉，下一章我們把鎖**拿掉**——進入 atomics 與無鎖的世界：`AtomicUsize`/`compare_exchange`、`Ordering`（Rust 直接採用 C++11 memory model，你的 `std::memory_order` 底子全用得上）、happens-before、ABA 問題。這是並發最硬、最需要「自己想對順序」的一層。

→ [Ch 25 無鎖與 atomics：Ordering](./25-atomics-lockfree.md)
