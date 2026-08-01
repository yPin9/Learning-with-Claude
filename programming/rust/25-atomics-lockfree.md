# Ch 25 — 無鎖與 atomics：Ordering

> **目標**：把鎖拿掉，進入 atomics 與無鎖（lock-free）。掌握 `AtomicUsize`/`AtomicBool` 等的 `load`/`store`/`fetch_add`/`compare_exchange`，真跑一個**無 Mutex** 的原子計數器。逐一對照 **`Ordering`**（`Relaxed`/`Acquire`/`Release`/`AcqRel`/`SeqCst`）與 C++ `std::memory_order_*`——**Rust 直接採用 C++11 memory model**，你的 `std::atomic` 底子全用得上，這章講的是「一模一樣的地方」和「Rust 的措辭差異」。用 acquire-release 建立 **happens-before** 的真跑例子。挖無鎖之難：**ABA 問題**、`compare_exchange` vs `compare_exchange_weak`（spurious fail）——真跑一個 CAS 迴圈。最後誠實面對：atomics **不救你於邏輯錯誤**（順序仍要自己想對）、`fetch_add` 溢位是 **wrapping**、以及接回 [Ch 20](./20-memory-model-ub.md)——**Miri 抓得到 atomic 誤用嗎**（真跑，誠實說）。

> **環境**：Rust `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）；atomic 誤用檢測用 nightly `miri 0.1.0` 的弱記憶體模擬（`-Zmiri-many-seeds`）。x86-64 是**強記憶體序（TSO）**架構，很多 ordering 錯誤在本機 native 跑不出來——這點本章反覆強調，弱序證據靠 Miri 補。所有執行輸出、panic、Miri 報告都是本機真跑，非推測。多執行緒輸出可能不定序，會標注。C++ 對照為 C++11 `<atomic>`。

## 為什麼需要這個？

[Ch 24](./24-shared-state.md) 用 `Mutex` 把共享可變狀態做對了——但鎖有代價。拿鎖、放鎖要進作業系統（競爭時），一個熱路徑上的計數器如果每次 +1 都拿一次 mutex，開銷可能比那個加法本身大幾十倍。更糟的是鎖會**阻塞**：持鎖 thread 被 OS 換出去睡著了，等它的 thread 全都卡住——這對低延遲系統（交易、即時、kernel）是硬傷。

你在 C/C++ 做 lock-free 就是為了繞過這個：用 CPU 提供的**原子指令**（`lock xadd`、`cmpxchg`）直接對一塊記憶體做「不可分割的讀-改-寫」，不進 kernel、不阻塞。`std::atomic<int>`、`std::memory_order`、`compare_exchange_weak`、ABA 問題、happens-before——這些你都碰過，也知道它們是並發裡最難、最容易寫錯的一層（錯了不 crash，只是偶爾結果詭異）。

好消息：**Rust 的 atomics 幾乎就是 C++ 的 atomics**——因為 Rust 直接搬了 C++11 的 memory model。`Ordering::Acquire` 就是 `memory_order_acquire`，`compare_exchange_weak` 就是 `compare_exchange_weak`，happens-before 規則一模一樣。所以這章對你不是「學新東西」，是「把已有的 C++ atomic 知識映射到 Rust 語法，並看清哪裡 Rust 收緊了（溢位）、哪裡工具幫得上（Miri 抓 ordering bug）」。壞消息：Rust 沒讓 atomics 變簡單——**順序你還是得自己想對**，型別系統在這層幫不了你。

## 先建立直覺

鎖 vs 原子操作的心智圖像：

```
   Mutex 保護計數器 +1              Atomic fetch_add +1

   thread ──lock()──▶ [鎖]         thread ──┐
             │  （競爭時進 kernel）           │  一條 CPU 指令
          臨界區: 讀→改→寫                    ▼  lock xadd
             │                          [記憶體] 不可分割地讀-改-寫
          unlock()                          （不進 kernel、不阻塞）

   一整段臨界區被鎖序列化              單一操作本身就是原子的
   （能保護任意複雜的多步操作）         （只能保護單一變數的單一操作）
```

關鍵區別：`Mutex` 能保護**任意複雜的臨界區**（讀 A、算、寫 B、再寫 C 全在鎖裡），代價是拿放鎖的開銷與阻塞。atomic 只能對**單一變數做單一不可分割操作**（加一、CAS 換一個值），但快、不阻塞。無鎖資料結構（無鎖 queue、stack）就是把「多步操作」拆成「一連串單步 atomic + 重試迴圈」，用 CAS 把它們串起來——這是 atomic 難的根源：你要自己保證這串拆開的操作合起來仍正確。

第二個直覺，也是最重要的：**atomic 不只是「這個操作不可分割」，還管「不同 thread 看到記憶體操作的順序」**。現代 CPU 和編譯器會**重排**記憶體讀寫（為了效能）——你在 thread A 先寫 `data` 再寫 `flag`，thread B 可能看到 `flag` 先變、`data` 還沒變。`Ordering` 就是你用來限制這種重排、建立「happens-before」關係的旋鈕。這正是 C++ memory model 的核心，Rust 原封不動搬過來。

> 如果你對 memory model、`std::memory_order`、happens-before 這些概念在 C++ 那邊已經熟，本章你會讀得很快——大部分是映射。如果生疏，本課的姊妹課 `programming/c_interview` 的 lock-free 章節是理想前置。

## 原子計數器：無 Mutex 真跑

先把 [Ch 24](./24-shared-state.md) 那個 `Arc<Mutex<i32>>` 計數器，改成**無鎖**版本——`AtomicUsize` + `fetch_add`：

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

fn main() {
    let counter = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::new();
    for _ in 0..10 {
        let c = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            for _ in 0..1000 {
                // 無 Mutex！fetch_add 是原子的讀-改-寫
                c.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }
    for h in handles { h.join().unwrap(); }
    println!("counter = {}", counter.load(Ordering::Relaxed));
}
```

真跑 3 次（本機，**每次都剛好 10000**）：

```
counter = 10000
counter = 10000
counter = 10000
```

和 [Ch 24](./24-shared-state.md) 的 Mutex 版本結果一樣正確，但**沒有鎖**——`fetch_add(1, ...)` 直接編成一條原子加法指令（x86 上是 `lock xadd`），10 個 thread 各加各的，硬體保證每次讀-改-寫不可分割，不會像 [Ch 23](./23-threads-send-sync.md) 那個 C `counter++` 那樣丟更新。

幾個要點：

- `Arc<AtomicUsize>`：注意這裡**不用** `Mutex`。`AtomicUsize` 本身就是 Sync（多 thread 可共享 `&`），因為它的每個操作都是原子的、天生 thread-safe。還是要 `Arc` 共享所有權（[Ch 23](./23-threads-send-sync.md)），但內層不用鎖。
- `fetch_add(1, Ordering::Relaxed)`：原子地把 counter 加 1，回舊值。對照 C++ `counter.fetch_add(1, std::memory_order_relaxed)`——**幾乎逐字對應**。
- 這裡用 `Relaxed` 是因為**這個計數器只在乎「最後總數對」，不用它來同步別的資料**。純計數（沒有「看到計數就代表某資料已就緒」的隱含依賴）用 `Relaxed` 最快。ordering 的選擇是下一節的核心。

對照 C++：

```cpp
#include <atomic>
std::atomic<size_t> counter{0};
// thread body:
counter.fetch_add(1, std::memory_order_relaxed);
// 讀:
counter.load(std::memory_order_relaxed);
```

看到了嗎——`AtomicUsize`↔`atomic<size_t>`、`fetch_add`↔`fetch_add`、`Ordering::Relaxed`↔`memory_order_relaxed`。這不是巧合，是 Rust 刻意採用同一個 model。

## `Ordering`：逐一對照 C++ memory_order

`Ordering` 是 Rust atomic 的靈魂，也是你 C++ 底子最直接用得上的地方。五個變體，逐一對照 `std::memory_order`：

| Rust `Ordering` | C++ `std::memory_order` | 語意 | 用途 |
|---|---|---|---|
| `Relaxed` | `memory_order_relaxed` | 只保證這個操作原子，**不限制**與其他記憶體操作的相對順序 | 純計數、不用來同步別的資料 |
| `Acquire` | `memory_order_acquire` | **讀**操作：此讀之後的存取不能重排到它之前；能「看到」對應 Release 之前的所有寫 | 讀取「就緒旗標」 |
| `Release` | `memory_order_release` | **寫**操作：此寫之前的存取不能重排到它之後；把之前的寫「發布」給對應的 Acquire | 設定「就緒旗標」 |
| `AcqRel` | `memory_order_acq_rel` | 讀-改-寫操作兼具 Acquire（讀那半）+ Release（寫那半） | `fetch_add`/`compare_exchange` 要同步時 |
| `SeqCst` | `memory_order_seq_cst` | 最強：在 Acquire/Release 之上，再加一個**所有 SeqCst 操作的全域單一總順序** | 需要全域一致順序、或想保守不出錯 |

**Rust 和 C++ 在這層是同一套模型**（[Rust Reference — atomic memory model](https://doc.rust-lang.org/nomicon/atomics.html) 明說「Rust 完全繼承 C++20 的記憶體模型」）。一個 Rust 沒有的：C++ 曾有 `memory_order_consume`（消費序），實務上沒有編譯器正確實作、已被棄用，Rust 從一開始就**沒有** `consume`——這是唯一實質差異，而它其實是「C++ 也放棄了的東西」。

強弱的心智圖像：

```
   弱 ◀───────────────────────────────────────▶ 強
   Relaxed    Acquire/Release      AcqRel        SeqCst
   只原子      建立 happens-before   讀改寫兩半     全域總順序
   最快        建立一對同步關係       都要同步       最慢、最保守

   選擇原則：能用弱的就用弱的（快），
             但「想不清楚就用 SeqCst」是合理的保守起手式
             （對照 C++：std::atomic 的預設就是 seq_cst）
```

> **注意 Rust 與 C++ 的一個 API 差異**：C++ 的 `atomic` 操作**預設** `memory_order_seq_cst`（不寫就是最強最保守）。Rust **強制你每次都寫 `Ordering`**——沒有預設。這是刻意的：Rust 不讓你不經思考就用（可能過強而慢的）SeqCst，逼你每次明確選。代價是樣板多，好處是你不會「不小心用了 Relaxed 卻以為有同步」。

## happens-before：acquire-release 真跑

最重要的用法：用 **Release 寫 + Acquire 讀** 建立一對 **happens-before** 關係，讓一個 thread 的寫入「發布」給另一個 thread。這是 message passing 的核心模式。

場景：producer 先寫 `data`，再把 `ready` 旗標設 true；consumer 等到 `ready` 為 true，就**保證**能看到 `data` 的新值。

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;

fn main() {
    let data = Arc::new(AtomicUsize::new(0));
    let ready = Arc::new(AtomicBool::new(false));

    let d = Arc::clone(&data); let r = Arc::clone(&ready);
    let producer = thread::spawn(move || {
        d.store(42, Ordering::Relaxed);      // 先寫資料
        r.store(true, Ordering::Release);     // Release：保證上面的寫在此之前完成
    });

    let d2 = Arc::clone(&data); let r2 = Arc::clone(&ready);
    let consumer = thread::spawn(move || {
        // Acquire：一旦看到 ready=true，就保證看得到 store 前的所有寫入
        while !r2.load(Ordering::Acquire) {
            std::hint::spin_loop();
        }
        let v = d2.load(Ordering::Relaxed);
        println!("consumer 看到 data = {}", v);
    });

    producer.join().unwrap();
    consumer.join().unwrap();
}
```

真跑 3 次（本機，總是 42）：

```
consumer 看到 data = 42
consumer 看到 data = 42
consumer 看到 data = 42
```

機制（這是 C++ release-acquire 的同款規則）：

```
   producer                          consumer
   ────────                          ────────
   data.store(42, Relaxed)  ┐
                            │ 這些寫「不能」重排到 Release 之後
   ready.store(true, Release) ─── happens-before ───▶ ready.load(Acquire) == true
                                                      │ 這些讀「不能」重排到 Acquire 之前
                                                      data.load(Relaxed) 保證看到 42
```

`Release` store 就像一道「柵欄」：它之前的所有寫（`data=42`）不能被重排到它之後，而且會被「發布」。對應的 `Acquire` load 一旦讀到那個 Release 寫的值（`ready=true`），就建立 happens-before——consumer 保證看得到 producer 在 Release **之前**的所有寫（`data=42`）。即使 `data` 用的是 `Relaxed`，這個 happens-before 也涵蓋它。

**為什麼這裡 native 總是印 42、看不出弱序問題？** 因為 x86-64 是**強記憶體序（TSO，Total Store Order）**架構——它硬體上就幾乎不重排 store/load，所以就算你把 `Release`/`Acquire` 都改成 `Relaxed`，x86 上大概率還是印 42。**這是最危險的陷阱**：你的 ordering 寫錯了，在 x86 上跑一萬次都對，一搬到 ARM/RISC-V（弱記憶體序）就爆。下一節用 Miri 把這個看不見的 bug 逼出來。

## atomics 不救你於邏輯錯誤：Miri 逼出弱序 bug

這是本章最重要的誠實一段，也接回 [Ch 20](./20-memory-model-ub.md)。**atomic 保證每個操作不可分割，但「順序想對」是你的責任**——用錯 `Ordering`（例如該用 `Acquire` 卻用 `Relaxed`）不是 UB、不會 crash，只是**偶爾**（在弱序機器上）給出重排後的錯誤結果。x86 的強序讓你在本機根本測不出來。

把上面的 message passing 例子**故意寫錯**——`data` 和 `flag` 都用 `Relaxed`（順序不足），然後問：consumer 會不會「看到 flag=true 卻讀到 data 還是 0」？

```rust
// message passing 但全用 Relaxed（順序不足）：邏輯錯誤
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;

fn main() {
    for _ in 0..50 {
        let data = Arc::new(AtomicUsize::new(0));
        let flag = Arc::new(AtomicBool::new(false));
        let d = Arc::clone(&data); let f = Arc::clone(&flag);
        let p = thread::spawn(move || {
            d.store(42, Ordering::Relaxed);
            f.store(true, Ordering::Relaxed);  // 應該用 Release
        });
        let d2 = Arc::clone(&data); let f2 = Arc::clone(&flag);
        let c = thread::spawn(move || {
            if f2.load(Ordering::Relaxed) {    // 應該用 Acquire
                let v = d2.load(Ordering::Relaxed);
                if v != 42 { println!("看到 flag 卻 data={} (重排!)", v); }
            }
        });
        p.join().unwrap(); c.join().unwrap();
    }
    println!("done");
}
```

在 x86 native 跑：**永遠印 `done`，從不觸發那行警告**——強序架構藏住了 bug。但 Miri 有**弱記憶體模擬**（`-Zmiri-many-seeds` 跑多個交錯/重排種子），把它逼出來（本機真跑）：

```
$ MIRIFLAGS="-Zmiri-many-seeds=0..30" cargo +nightly miri run
看到 flag 卻 data=0 (重排!)
看到 flag 卻 data=0 (重排!)
看到 flag 卻 data=0 (重排!)
...
```

`看到 flag 卻 data=0`——consumer 讀到 `flag=true`，卻讀到 `data` 還是 0！因為兩個 store 都 `Relaxed`，允許 consumer 觀察到「`flag` 的寫先於 `data` 的寫」的重排順序。這在 x86 native 測不出來，Miri 的弱序模擬抓到了。

把 ordering 改對（`Release`/`Acquire`），同樣 Miri 30 個種子跑：

```
$ MIRIFLAGS="-Zmiri-many-seeds=0..30" cargo +nightly miri run
done (no reorder bug)
```

改對後 30 個種子都沒觸發 bug——release-acquire 的 happens-before 排除了那個重排。

**這對你的實務意義**，也是接 [Ch 20](./20-memory-model-ub.md) 的誠實結論：

1. **atomics 不讓你免於邏輯錯誤**。`Ordering` 選錯不是 data race UB（[Ch 20](./20-memory-model-ub.md) 那種）、不是記憶體損壞——是**執行結果邏輯錯誤**，型別系統完全管不到。順序你得自己想對。
2. **x86 強序是假朋友**。你的 ordering bug 在 x86 上可能一萬次都對，一上 ARM 就爆。**別用「本機跑起來對」證明 ordering 正確**——這正是 [Ch 20](./20-memory-model-ub.md) 那句「跑起來對從來不是 sound 的證據」在 atomic 世界的翻版。
3. **Miri 抓得到 atomic 誤用嗎？誠實答**：Miri 抓得到 atomic 與**非原子**存取混用的 **data race**（[Ch 20](./20-memory-model-ub.md) 真跑過），也能用弱記憶體模擬（`-Zmiri-many-seeds`）**逼出**上面這種「全原子但 ordering 太弱」的重排 bug——但它是**動態、非窮盡**的（[Ch 20](./20-memory-model-ub.md) 講過），靠種子涵蓋，不保證每個 ordering 錯誤都抓到。它是強力 lint，不是「ordering 正確性的形式證明」。ordering 正確性最終還是你的推理責任。

## CAS 迴圈、ABA、weak vs strong

`compare_exchange`（CAS，compare-and-swap）是無鎖資料結構的核心原語：「如果現在的值等於我預期的舊值，就換成新值；否則告訴我現在的實際值」。它是把「讀一個值、根據它算新值、寫回去」這個多步操作變原子的方法——透過**重試迴圈**。

真跑一個用 CAS 手刻的**原子 max**：多個 thread 各拿一個候選值，只在「候選比目前最大值大」時才寫入。

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

fn main() {
    // 用 compare_exchange 手刻一個原子 max：只在新值更大時才寫入
    let max = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::new();
    let inputs = [3usize, 7, 2, 9, 5, 9, 1, 8];
    for &v in inputs.iter() {
        let m = Arc::clone(&max);
        handles.push(thread::spawn(move || {
            let mut cur = m.load(Ordering::Relaxed);
            loop {
                if v <= cur { break; }             // 不比目前大，放棄
                match m.compare_exchange_weak(
                    cur, v,
                    Ordering::Relaxed, Ordering::Relaxed,
                ) {
                    Ok(_) => break,                // CAS 成功，寫進去了
                    Err(actual) => cur = actual,   // 有人搶先改了，用最新值重試
                }
            }
        }));
    }
    for h in handles { h.join().unwrap(); }
    println!("max = {}", max.load(Ordering::Relaxed));
}
```

真跑 3 次（本機，輸入最大值是 9，總是 9）：

```
max = 9
max = 9
max = 9
```

CAS 迴圈的骨架（無鎖程式的標準形狀）：

```
   loop {
       cur = load()                     讀目前值
       new = f(cur)                     根據它算新值（這裡：只在 v>cur 時才要寫）
       match compare_exchange(cur, new) {
           Ok  => break                 CAS 成功：期間沒人改，寫入完成
           Err(actual) => cur = actual  有人搶先改了 cur，拿最新值重來
       }
   }
```

`compare_exchange(cur, new, success_order, failure_order)` 收兩個 `Ordering`——成功時和失敗時的記憶體序（對照 C++ `compare_exchange_weak(expected, desired, success, failure)`，逐字對應）。

**`compare_exchange` vs `compare_exchange_weak`（spurious fail）**：這是 C++ 老手該熟的坑。`compare_exchange_weak` 允許**假失敗（spurious failure）**——即使當前值**確實等於**預期舊值，它也**可能**回 `Err`（沒換成）。為什麼要有這種「無理由失敗」的版本？因為在某些架構（ARM、RISC-V 的 LL/SC——load-linked/store-conditional）上，`weak` 能編成更有效率的單次 LL/SC，而 `strong`（`compare_exchange`）要在 spurious fail 時自己內部重試、多包一層迴圈。**在 CAS 迴圈裡用 `weak`**（本來就有重試迴圈，假失敗多轉一圈無妨，換取更快的單次操作）；**不在迴圈裡、只做一次 CAS 用 `strong`**（不想被假失敗坑）。這和 C++ 的建議完全一致。

**ABA 問題**——無鎖最惡名昭彰的坑。CAS 只比較「值是否等於預期」，但「值一樣」不代表「中間沒被動過」：

```
   thread 1 讀到 A，準備 CAS(A -> C)
   thread 2 趁隙：A -> B -> A（改成 B 又改回 A）
   thread 1 的 CAS(A -> C)：看到值還是 A，成功！
            但它不知道中間 A 已經「不是原來那個 A」了
```

對「就是一個數字」的 max 這種，ABA 無害（值一樣就是一樣）。但對**指標**（無鎖 stack/queue 用 CAS 換 head 指標）就致命：thread 2 把節點 A pop、free、又 malloc 回同一位址當新節點 push（位址還是 A），thread 1 的 CAS 成功了，卻把 head 指向一個已經 free/語意全變的節點 → UAF。這是 C++ 無鎖程式的經典災難，Rust 一樣會中——**Rust 的型別系統不防 ABA**，這是演算法層的問題。對策（tagged pointer 帶版本號、hazard pointer、epoch-based reclamation）Rust 和 C++ 通用，生態裡 `crossbeam`（[Ch 23](./23-threads-send-sync.md) 提過的外部 crate）的 epoch-based reclamation 就是解這個的。

## `fetch_add` 溢位是 wrapping

一個要記牢的語意：**atomic 的 `fetch_add`/`fetch_sub` 溢位是 wrapping（回繞），不 panic、不是 UB**——即使在 debug build。這和 [Ch 20](./20-memory-model-ub.md) 講的「普通整數 debug 溢位會 panic」不同。

真跑，`AtomicU8` 從 250 加 10（超過 u8 上限 255）：

```rust
use std::sync::atomic::{AtomicU8, Ordering};

fn main() {
    let a = AtomicU8::new(250);
    // fetch_add 溢位語意 = wrapping（回繞），不 panic
    let old = a.fetch_add(10, Ordering::Relaxed);   // 250 + 10 = 260 -> wrap 到 4
    println!("old = {}, new = {}", old, a.load(Ordering::Relaxed));
}
```

真跑：

```
old = 250, new = 4
```

`250 + 10 = 260`，超過 255，wrap 成 `260 - 256 = 4`。`fetch_add` 回舊值 250，新值是 wrap 後的 4，**沒有 panic**。

對照普通 `u8` 加法在 debug build（[Ch 20](./20-memory-model-ub.md) 講的）——runtime 溢位會 panic：

```rust
use std::hint::black_box;
fn main() {
    let x: u8 = black_box(250);
    let y = x + black_box(10);   // 非常數，runtime debug 溢位 panic
    println!("{}", y);
}
```

真跑（debug build）：

```
thread 'main' panicked at t25e.rs:4:13:
attempt to add with overflow
```

（`black_box` 是為了阻止編譯器在編譯期算出這個溢位——那會變成編譯錯誤 `this arithmetic operation will overflow`，而非 runtime panic。）

差異總結：**普通整數運算 debug panic / release wrapping；atomic 的 `fetch_add` 一律 wrapping（不分 debug/release）**。這是因為 atomic 操作直接對應硬體原子指令（`lock xadd`），硬體就是 wrapping，沒有「檢查溢位再 panic」的空間。你若需要 atomic 的溢位檢查，得自己用 `fetch_update` 或 CAS 迴圈手動檢查。這點 C++ 的 `atomic::fetch_add` 也是 wrapping（無號）——一致。

## 對比與取捨

| 面向 | Mutex（Ch 24） | Atomic（本章） |
|---|---|---|
| 保護範圍 | 任意複雜臨界區 | 單一變數單一操作 |
| 開銷 | 拿放鎖（競爭時進 kernel） | 一條原子指令，不進 kernel |
| 阻塞 | 會（持鎖 thread 睡，別人等） | 不阻塞（lock-free） |
| 難度 | 較低（想清楚鎖範圍即可） | **高**（ordering、ABA、CAS 迴圈自己想對） |
| 出錯後果 | deadlock（乾淨卡死，好查） | 邏輯錯誤（偶發、弱序才現、難查） |
| 適用 | 多步操作、臨界區複雜 | 熱路徑單一計數/旗標、低延遲 |

取捨要非常誠實：**lock-free 不是「更好的鎖」，是「用大量正確性風險換低延遲」**。除非你在熱路徑上量到鎖是瓶頸、或有硬性的無阻塞需求（kernel、即時），否則 `Mutex` 幾乎總是對的選擇——它好想、好查、不會被 x86 強序騙。真要寫無鎖資料結構，別自己從 CAS 手刻（ABA、reclamation 太容易錯），用 `crossbeam` 這類**已被驗證**的 crate。Rust 在這層給你的幫助**比 Mutex 那層少**：它擋得掉 atomic vs 非原子的 data race（Send/Sync + Miri），但**擋不掉 ordering 選錯、擋不掉 ABA**——那些是演算法正確性，型別系統無能為力。

## 踩雷集錦

1. **以為「本機 x86 跑起來對」就代表 ordering 正確**：x86-64 是強序（TSO）架構，幾乎不重排，你把 `Acquire`/`Release` 全改 `Relaxed` 大概率還是印對（真跑 message passing 全 Relaxed，native 從不觸發 bug，Miri 弱序模擬才逼出 `data=0`）。ordering bug 在 x86 藏著，一上 ARM/RISC-V 就爆。**別用本機執行證明 ordering 正確**——這是 [Ch 20](./20-memory-model-ub.md)「跑起來對不是 sound 證據」的 atomic 版。

2. **在 CAS 迴圈裡用 `compare_exchange`（strong）而非 `weak`**：迴圈本來就重試，`weak` 允許假失敗但在 ARM/RISC-V 上編成更快的單次 LL/SC。迴圈用 `weak`；只做一次 CAS（無迴圈）才用 `strong`（不想被假失敗坑）。用反了不會錯但可能慢。

3. **忘記 ABA 問題**：CAS 只比「值等不等於預期」，值一樣不代表中間沒被 `A→B→A`。對數字無害，對**指標**（無鎖 stack/queue）是 UAF 災難。Rust 型別系統**不防 ABA**——這是演算法問題。用 tagged pointer/hazard pointer/epoch reclamation（`crossbeam`）解。

4. **以為 atomic 的 `fetch_add` 溢位會 panic**：**不會**，一律 wrapping（不分 debug/release，真跑 250+10=4）。這和普通整數運算 debug panic 不同——atomic 直接對應硬體 wrapping 指令。要檢查溢位得自己用 `fetch_update`/CAS 迴圈。

5. **無腦用 `Relaxed` 當「反正快」的預設**：`Relaxed` 只保證操作原子，**不建立任何 happens-before**。用它同步別的資料（「看到這個 atomic 就代表那份 data 好了」）會出重排 bug。純計數/純旗標查詢（不隱含對別的資料的依賴）才用 `Relaxed`；要發布/同步資料用 `Release`/`Acquire`。想不清就 `SeqCst`（慢但保守）。

6. **以為 lock-free 一定比鎖快**：無競爭時 atomic 快，但高競爭下 CAS 迴圈會**反覆失敗重試**（活鎖式忙轉），可能比一把好 mutex 更慢更耗 CPU。lock-free 的價值是**無阻塞/低延遲尾端**，不是無腦吞吐更高。沒量測就假設它更快是迷思。

## 進階：再往深一層

**`fence`（記憶體屏障）。** 除了給單一 atomic 操作標 `Ordering`，還能用 `std::sync::atomic::fence(Ordering)` 下一道**獨立的屏障**，不綁在特定變數上。它對應 C++ 的 `std::atomic_thread_fence`。用途：把「一組 Relaxed 操作」用一道 fence 統一加上 acquire/release 語意，某些無鎖演算法用它比每個操作都標 ordering 更有效率。這是 atomic 進階領域，多數情況用不到——用到時你會知道自己在幹嘛。

**`SeqCst` 的全域總順序到底多強、多貴。** `SeqCst` 比 Acquire/Release 多的那層是：**所有 `SeqCst` 操作之間存在一個全域一致的單一總順序**，所有 thread 都同意這個順序。這解決 Acquire/Release 解不了的問題（經典的 [independent reads of independent writes / Dekker 演算法](https://en.wikipedia.org/wiki/Memory_ordering)）。代價：x86 上 `SeqCst` store 要用 `mfence` 或 `lock` 前綴（比普通 store 貴不少），弱序架構更貴。**別因為「不確定就全 SeqCst」而在熱路徑濫用**——想清楚你到底需不需要那個全域總順序，多數 message passing 用 Release/Acquire 就夠。

**面試常問**：「`Ordering::Relaxed` 和 `Acquire`/`Release` 差在哪？什麼時候能用 Relaxed？」——標準答案：`Relaxed` 只保證**單一操作原子性**，不建立任何跨 thread 的 happens-before/順序保證；`Release`(寫)/`Acquire`(讀) 成對建立 happens-before——Acquire 讀到 Release 寫的值後，保證看得到 Release **之前**的所有寫。**能用 Relaxed 的唯一場景**：這個 atomic 純粹自己算（如計數器總數），不用來「發布/同步其他資料」。一旦「看到這個 atomic 的值就意味著某份資料已就緒」，就必須 Release/Acquire。能講清「Relaxed 不給你 happens-before」這個界線，代表你懂 memory model，不是背名字。

**再深一層**：「Rust 為什麼強制每次寫 `Ordering`，C++ 卻有 seq_cst 預設？」——設計哲學差異。C++ 為了讓 atomic「開箱即用不出錯」，預設最強的 seq_cst（代價是可能過強而慢）。Rust 認為 atomic 是專家工具，**不該有讓你不經思考的預設**——強制每次明確選 `Ordering`，逼你想「我這裡到底需要多強」。這犧牲了便利（樣板多），換來「你不會不小心用了 Relaxed 卻以為有同步」。這是 Rust「顯式優於隱式」在並發原語上的體現。

## 動手練習

1. **無鎖計數器 + 換 Mutex 對照**：跑本章 `AtomicUsize` 計數器確認 10000。再把它換回 [Ch 24](./24-shared-state.md) 的 `Arc<Mutex<i32>>`，兩者都對——體會 atomic 版沒有鎖、`fetch_add` 就是一條指令。

2. **用 Miri 逼出你自己的 ordering bug**：把本章「全 Relaxed 的 message passing」打進一個 `cargo` 專案，先 x86 native 跑（永遠 `done`，看不出 bug），再 `MIRIFLAGS="-Zmiri-many-seeds=0..30" cargo +nightly miri run` 看它報 `data=0`。然後改成 `Release`/`Acquire`，確認 Miri 30 個種子都乾淨。親手體會「x86 強序騙你、Miri 弱序抓你」。

3. **CAS 迴圈手刻 max**：跑本章原子 max，確認結果是輸入最大值。然後把 `compare_exchange_weak` 改成 `compare_exchange`（strong），確認也對——想清楚兩者在 CAS 迴圈裡的差別（spurious fail）。

4. **驗證 `fetch_add` wrapping**：跑 `AtomicU8::new(250).fetch_add(10)` 看它回繞到 4 不 panic；再寫一個普通 `u8` 的 `250 + 非常數10`（用 `black_box`）看它 debug panic。對照兩者溢位語意差異。

## 本章重點整理

- **atomic = 用 CPU 原子指令對單一變數做不可分割的讀-改-寫，不進 kernel、不阻塞**。真跑 `AtomicUsize` + `fetch_add` 的無鎖計數器（每次 10000，無 Mutex）。適合熱路徑單一計數/旗標、低延遲；複雜臨界區還是用 Mutex（Ch 24）。
- **Rust 直接採用 C++11 memory model**：`Ordering::{Relaxed, Acquire, Release, AcqRel, SeqCst}` 逐一對應 `std::memory_order_*`，語意一模一樣。唯一差異：Rust 沒有 `consume`（C++ 也棄用了）、且 Rust **強制每次寫 `Ordering`**（C++ 預設 seq_cst）。
- **happens-before 靠 Release(寫)/Acquire(讀) 成對建立**：Acquire 讀到 Release 寫的值後，保證看得到 Release 之前的所有寫（真跑 message passing 印 42）。`Relaxed` **不**建立任何 happens-before，只保證單一操作原子——純計數才用。
- **無鎖之難：ordering 錯是邏輯錯誤不是 UB、x86 強序會騙你、ABA、weak vs strong CAS**。真跑：全 Relaxed 的 message passing 在 x86 native 永遠對，Miri 弱序模擬（`-Zmiri-many-seeds`）逼出 `data=0` 重排 bug。CAS 迴圈用 `compare_exchange_weak`（允許 spurious fail，ARM/RISC-V 更快）。ABA 對指標是 UAF 災難，型別系統**不防**。
- **誠實接 Ch 20**：atomics **不救你於邏輯錯誤**——順序自己想對是責任，型別系統管不到。`fetch_add` 溢位是 **wrapping**（不分 debug/release，真跑 250+10=4），不像普通整數 debug panic。Miri 抓得到 atomic vs 非原子的 data race、也能弱序模擬逼出 ordering 太弱的 bug，但**動態非窮盡**，是強力 lint 不是形式證明。

## 自我檢核

- [ ] 面試問「`Relaxed` 和 `Acquire`/`Release` 差在哪、何時能用 Relaxed」，能答「Relaxed 只保證單一操作原子、不建立 happens-before；Release/Acquire 成對建立 happens-before；只有『這 atomic 不用來同步別的資料』時才用 Relaxed」。
- [ ] 不看筆記，能把 `Ordering` 五個變體對應到 `std::memory_order_*`，並說出 Rust 與 C++ 的兩個差異（無 consume、強制寫 Ordering）。
- [ ] 能解釋為什麼「本機 x86 跑起來對」不能證明 ordering 正確（強序 TSO 藏 bug，ARM/RISC-V 才現），以及 Miri 弱序模擬怎麼補這個測試盲點。
- [ ] 能解釋 `compare_exchange_weak` 的 spurious fail 是什麼、為什麼 CAS 迴圈裡該用它，以及 ABA 問題為什麼對指標型無鎖結構是 UAF 災難（且 Rust 型別系統不防）。
- [ ] 知道 `fetch_add` 溢位是 wrapping（不 panic），和普通整數 debug 溢位 panic 不同，並能說出誠實結論：atomics 不讓你免於邏輯（ordering）錯誤。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 書籍

- **《Rust Atomics and Locks》— 第 2–3 章（Atomics / Memory Ordering）** — Mara Bos（O'Reilly, 2023，線上免費 marabos.nl/atomics）
  - **這本書的定位**：Rust 並發的權威單本書，作者是 Rust library team leader。第 2 章講所有 atomic 型別與操作，第 3 章**專章講 memory ordering**——Relaxed/Acquire/Release/SeqCst、happens-before、fence，是本章 ordering 那節的完整深化版。第 6–7 章手刻 `Arc` 和無鎖結構。
  - **讀哪幾章**：第 3 章「Memory Ordering」與本章直接對應且更深（有更多弱序 CPU 的具體例子）；第 8 章「Operating System Primitives」補 futex。
  - **前提**：懂本章 + Ch 24。這是本課 atomic/lock-free 最推薦的單本延伸，強烈建議讀完本章接第 3 章。

### 官方文件 / Spec

- **[The Rustonomicon — Atomics](https://doc.rust-lang.org/nomicon/atomics.html)**
  - **讀哪裡**：整節。開頭明說「Rust 完全繼承 C++20 的記憶體模型」（本章「Rust 採用 C++11 model」的權威依據），然後逐一講 Relaxed/Acquire-Release/SeqCst 的語意與適用。
  - **學到什麼**：Rust 官方對「為什麼不自己定義 memory model、直接用 C++ 的」的說明；`Ordering` 各變體的權威定義。
  - **前提**：懂本章 + 有 C++ memory model 概念更佳。

- **[std::sync::atomic 模組文件](https://doc.rust-lang.org/std/sync/atomic/)**
  - **讀哪裡**：模組首頁的 `Ordering` 說明、各 `AtomicXxx` 型別的 `compare_exchange`/`compare_exchange_weak`/`fetch_*` 方法簽章。
  - **學到什麼**：`compare_exchange` 兩個 `Ordering` 參數（success/failure）的精確語意、`weak` 的 spurious fail 官方措辭、`fetch_add` wrapping 語意。
  - **前提**：懂本章。當工具書查方法簽章。

### 技術文章 / 論文

- **[C++ memory model 與 std::memory_order 參考](https://en.cppreference.com/w/cpp/atomic/memory_order)** — cppreference
  - **這篇說什麼**：C++ `memory_order` 的權威參考。因為 Rust 直接採用這個 model，這頁對每個 order 的定義、release-acquire/release-consume/seq-cst 的形式化例子，**直接適用於 Rust**——把 Rust 的 `Ordering::Acquire` 讀成 `memory_order_acquire` 即可。
  - **讀哪裡**：「Explanation」段的 Relaxed / Release-Acquire / Sequentially-consistent 三小節，每個都有多 thread 例子。
  - **為什麼值得讀**：你的 C++ 底子在這裡直接變現；比 Rust 文件更多形式化例子。
  - **前提**：有 C++ atomic 基礎；讀時把 C++ 語法映射回 Rust。

- **[Preshing on Programming — memory ordering 系列](https://preshing.com/20120913/acquire-and-release-semantics/)** — Jeff Preshing
  - **這篇說什麼**：業界公認講 acquire/release、memory barrier、lock-free 最清楚的部落格系列。用大量圖解和真實 CPU（x86 TSO vs ARM 弱序）的例子解釋為什麼需要 ordering——正是本章「x86 強序騙你」那節的深化。
  - **讀哪裡**：「Acquire and Release Semantics」「Memory Barriers Are Like Source Control Operations」兩篇入門；想深入看「The Purpose of memory_order_consume」。
  - **為什麼值得讀**：作者是遊戲引擎的 lock-free 老手，把抽象的 memory model 講成看得見的 CPU 行為，語言無關但對 Rust/C++ 都適用。
  - **前提**：懂本章 ordering 基本概念；讀完會對「為什麼弱序機器需要 barrier」有畫面。

搞懂了 atomics、ordering、CAS 與無鎖之難，Part 4 的並發同步原語就完整了。下一章轉向另一種並發模型——**async/await**：不是開一堆 thread，而是用單/少數 thread 跑成千上萬個「協作式任務」。我們從最底層開始：`Future` 到底是什麼、`poll` 怎麼運作、它如何編譯成一個狀態機。

→ [Ch 26 async 原理一：Future 與 poll](./26-async-futures.md)
