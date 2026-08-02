# Ch 5 - 第一個 LibAFL Fuzzer

> **目標**: 從零開始用 LibAFL 組裝一個完整的 in-process fuzzer，理解每個零件的職責與所有權流動，能在幾秒內自動找到 `"abc"` 觸發的 crash。
>
> **環境**:
> - LibAFL 0.15.4 (`libafl` + `libafl_bolts`)
> - Rust 1.75+, edition 2021
> - WSL2 Ubuntu 22.04 / 任何 Linux (cargo 可用)
> - env_logger 0.11 (logging)
> - 不需要 `afl-cc` — 本章 coverage map 完全手動模擬

---

## 為什麼需要這個

前幾章講了 coverage-guided fuzzing 的理論：feedback 驅動 corpus 成長，mutator 不斷變形輸入，scheduler 選出最有價值的種子。LibAFL 把這些概念拆成正交的 Rust trait，讓你像搭積木一樣組合。

但「理論上可以組合」和「真的跑起來」之間有一條溝。這條溝的名字叫 **所有權** — LibAFL 裡 observer、feedback、state、executor 之間有非常講究的借用 / 移動順序，順序錯了編譯報錯，順序對了幾秒找到 crash。

這章用官方的 `baby_fuzzer` 範例（LibAFL main branch 內的 `fuzzers/baby_fuzzer`）把每一行拆開說清楚。這是真實可跑的程式碼，不是虛構示例。

---

## 先建立直覺

在看程式碼前，先把整個 fuzzer 的資料流畫出來：

```
┌─────────────────────────────────────────────────────────────┐
│                        fuzz_loop                            │
│                                                             │
│  scheduler                                                  │
│  選出 corpus 裡一個 input                                   │
│        │                                                    │
│        v                                                    │
│  mutator (HavocScheduledMutator)                            │
│  對 input 做隨機 byte 操作                                  │
│        │                                                    │
│        v                                                    │
│  executor (InProcessExecutor)                               │
│  呼叫 harness(input)                                        │
│        │                                                    │
│        ├── harness 執行中 ──► signals_set(idx)              │
│        │                     寫入 SIGNALS[idx] = 1         │
│        │                                                    │
│        v                                                    │
│  observer (ConstMapObserver)                                │
│  執行後讀 SIGNALS[0..16]                                    │
│        │                                                    │
│        v                                                    │
│  feedback (MaxMapFeedback)                                  │
│  比對這次 map vs 歷史 max map                               │
│  有新覆蓋？→ input 加進 corpus                              │
│        │                                                    │
│        v                                                    │
│  objective (CrashFeedback)                                  │
│  harness panic？→ input 存到 crashes/ 目錄                  │
└─────────────────────────────────────────────────────────────┘
```

整個流程是一個大迴圈。每次迭代：選種子 → 變形 → 執行 → 讀 coverage → 判斷是否有趣。

---

## 專案結構

```
baby_fuzzer/
├── Cargo.toml
└── src/
    └── main.rs
```

### Cargo.toml

```toml
[package]
name = "baby_fuzzer"
version = "0.1.0"
edition = "2021"

[profile.dev]
panic = "abort"

[profile.release]
panic = "abort"
lto = true
codegen-units = 1
opt-level = 3

[dependencies]
env_logger = "0.11"
libafl = { version = "0.15", features = ["tui_monitor"] }
libafl_bolts = "0.15"
log = "0.4"
```

**注意 `panic = "abort"`**。這不是最佳化選項，是 `InProcessExecutor` 的硬需求，稍後說明原因。

---

## 完整原始碼

```rust
#[cfg(windows)]
use std::ptr::write_volatile;
use std::{path::PathBuf, ptr::write};

#[cfg(feature = "tui")]
use libafl::monitors::tui::TuiMonitor;
#[cfg(not(feature = "tui"))]
use libafl::monitors::SimpleMonitor;

use libafl::{
    corpus::{InMemoryCorpus, OnDiskCorpus},
    events::SimpleEventManager,
    executors::{ExitKind, InProcessExecutor},
    feedbacks::{CrashFeedback, MaxMapFeedback},
    fuzzer::{Fuzzer, StdFuzzer},
    generators::RandPrintablesGenerator,
    inputs::{BytesInput, HasTargetBytes},
    mutators::{havoc_mutations::havoc_mutations, scheduled::HavocScheduledMutator},
    observers::ConstMapObserver,
    schedulers::QueueScheduler,
    stages::mutational::StdMutationalStage,
    state::StdState,
};

use libafl_bolts::{
    current_nanos, nonnull_raw_mut, nonzero, rands::StdRand, tuples::tuple_list, AsSlice,
};

const SIGNALS_LEN: usize = 16;
static mut SIGNALS: [u8; SIGNALS_LEN] = [0; SIGNALS_LEN];
static mut SIGNALS_PTR: *mut u8 = &raw mut SIGNALS as _;

fn signals_set(idx: usize) {
    unsafe { write(SIGNALS_PTR.add(idx), 1) };
}

pub fn main() {
    env_logger::init();

    let mut harness = |input: &BytesInput| {
        let target = input.target_bytes();
        let buf = target.as_slice();
        signals_set(0);
        if !buf.is_empty() && buf[0] == b'a' {
            signals_set(1);
            if buf.len() > 1 && buf[1] == b'b' {
                signals_set(2);
                if buf.len() > 2 && buf[2] == b'c' {
                    #[cfg(unix)]
                    panic!("Artificial bug triggered =)");
                    #[cfg(windows)]
                    unsafe {
                        write_volatile(std::ptr::null_mut::<u32>(), 0);
                    }
                }
            }
        }
        ExitKind::Ok
    };

    let observer = unsafe {
        ConstMapObserver::from_mut_ptr("signals", nonnull_raw_mut!(SIGNALS))
    };
    let mut feedback = MaxMapFeedback::new(&observer);
    let mut objective = CrashFeedback::new();

    let mut state = StdState::new(
        StdRand::with_seed(current_nanos()),
        InMemoryCorpus::new(),
        OnDiskCorpus::new(PathBuf::from("./crashes")).unwrap(),
        &mut feedback,
        &mut objective,
    ).unwrap();

    #[cfg(not(feature = "tui"))]
    let mon = SimpleMonitor::new(|s| println!("{s}"));
    #[cfg(feature = "tui")]
    let mon = TuiMonitor::builder()
        .title("Baby Fuzzer")
        .enhanced_graphics(false)
        .build();

    let mut mgr = SimpleEventManager::new(mon);
    let scheduler = QueueScheduler::new();
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    let mut executor = InProcessExecutor::new(
        &mut harness,
        tuple_list!(observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
    ).expect("Failed to create the Executor");

    let mut generator = RandPrintablesGenerator::new(nonzero!(32));

    state
        .generate_initial_inputs(
            &mut fuzzer,
            &mut executor,
            &mut generator,
            &mut mgr,
            8,
        )
        .expect("Failed to generate the initial corpus");

    let mutator = HavocScheduledMutator::new(havoc_mutations());
    let mut stages = tuple_list!(StdMutationalStage::new(mutator));

    fuzzer
        .fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)
        .expect("Error in the fuzzing loop");
}
```

---

## 逐段解說

### 1. 手動 coverage map：SIGNALS

```rust
const SIGNALS_LEN: usize = 16;
static mut SIGNALS: [u8; SIGNALS_LEN] = [0; SIGNALS_LEN];
static mut SIGNALS_PTR: *mut u8 = &raw mut SIGNALS as _;

fn signals_set(idx: usize) {
    unsafe { write(SIGNALS_PTR.add(idx), 1) };
}
```

這是整個 fuzzer 最核心的概念，用 16 個 byte 模擬 AFL 的 edge bitmap。

AFL 在編譯時用 `afl-clang-fast` 插樁，在每個 branch 邊插入一行 `map[edge_id]++`。這裡不插樁真實程式，改用 **手動呼叫** `signals_set(idx)` 達到同樣效果：「執行到這裡，就設 SIGNALS[idx] = 1」。

`SIGNALS_PTR` 是多餘的嗎？不是。`ConstMapObserver::from_mut_ptr` 需要一個 `NonNull<u8>` raw pointer，用 `&raw mut SIGNALS` 取到靜態陣列的位址，存到指標裡方便後面傳入。

`std::ptr::write` 而非 `SIGNALS[idx] = 1`：因為 `SIGNALS` 是 `static mut`，直接讀寫需要 unsafe，且 `write` 明確表達這是指標運算下的記憶體寫入，不被最佳化消除。

### 2. harness：三層巢狀分支

```rust
let mut harness = |input: &BytesInput| {
    let target = input.target_bytes();
    let buf = target.as_slice();
    signals_set(0);                        // 永遠到達
    if !buf.is_empty() && buf[0] == b'a' {
        signals_set(1);                    // buf[0] == 'a'
        if buf.len() > 1 && buf[1] == b'b' {
            signals_set(2);                // buf[0..1] == "ab"
            if buf.len() > 2 && buf[2] == b'c' {
                panic!("Artificial bug triggered =)");
            }
        }
    }
    ExitKind::Ok
};
```

這個結構設計得很刻意：

```
輸入               signals_set 呼叫    coverage map 狀態
--------           ----------------    -----------------
"xyz"         →    (0)                 [1,0,0,0,...]
"a..."        →    (0,1)               [1,1,0,0,...]
"ab..."       →    (0,1,2)             [1,1,1,0,...]
"abc..."      →    (0,1,2) + panic     crash!
```

每多走進一層 if，coverage map 就多一個新 byte 被設成 1。`MaxMapFeedback` 看到新 bit 就把輸入加進 corpus。這形成一個 **coverage 梯度**，引導 fuzzer 從 `"a..."` 逐步發現 `"ab..."` 再發現 `"abc..."`。

沒有這個梯度的話，fuzzer 要靠純隨機湊出三個連續正確 byte，機率是 1/256^3 ≈ 1/16,000,000。有梯度的話，找到 `"a"` 後只需再找 `"b"`，找到 `"ab"` 後只需再找 `"c"`，每步概率 1/256，幾秒內必達。

### 3. Observer：把 SIGNALS 暴露給 fuzzer 框架

```rust
let observer = unsafe {
    ConstMapObserver::from_mut_ptr("signals", nonnull_raw_mut!(SIGNALS))
};
```

`ConstMapObserver` 是一個泛型結構，持有一個指向固定大小陣列的 raw pointer。它的工作很單純：每次 executor 執行完 harness，框架呼叫 observer 的 `post_exec`，observer 就讀一次 SIGNALS 的當前值，存起來供 feedback 比對。

`nonnull_raw_mut!(SIGNALS)` 是 libafl_bolts 的 macro，把 `&raw mut SIGNALS` 轉成 `NonNull<[u8; 16]>`，繞過 null check，滿足 `from_mut_ptr` 的型別需求。

`"signals"` 是這個 observer 的名字，用於後續識別。多個 observer 共存時（例如 coverage map + timing observer），靠名字區分。

**`unsafe` 是必要的**：跨執行路徑共享 raw pointer，Rust 無法靜態證明安全，由我們保證 SIGNALS 的生命週期夠長（`static`）且單執行緒存取。

### 4. Feedback 與 Objective

```rust
let mut feedback = MaxMapFeedback::new(&observer);
let mut objective = CrashFeedback::new();
```

**`MaxMapFeedback`**：持有 observer 的 **借用**（注意是 `&observer` 不是 move）。每次執行後，它把 observer 目前看到的 map 與歷史上各 byte 的最大值比較。任何一個 byte 創了新高，這個輸入就被標記為「有趣」，加進 corpus。

`MaxMap` 而非 `BitMap`：AFL 的 edge bitmap 用 byte 記計數（`map[edge_id]++`），maxmap 追蹤每個 slot 的歷史最大值。這能捕捉「同一條邊被走更多次」這個新穎性。本範例 `signals_set` 只寫 1，等效於 bitmap 語義。

**`CrashFeedback`**：監聽 harness 是否回傳 `ExitKind::Crash`（或 panic 被轉換成 crash）。一旦觸發，輸入被存到 objective corpus（我們設為磁碟上的 `./crashes/`）。

### 5. StdState：fuzzer 的持久化記憶

```rust
let mut state = StdState::new(
    StdRand::with_seed(current_nanos()),
    InMemoryCorpus::new(),
    OnDiskCorpus::new(PathBuf::from("./crashes")).unwrap(),
    &mut feedback,
    &mut objective,
).unwrap();
```

`StdState` 的建構子簽名：`(rng, corpus, objective_corpus, &mut feedback, &mut objective)`。

為什麼 state 需要 `&mut feedback` 和 `&mut objective`？**初始化 metadata**。`MaxMapFeedback` 需要在 state 裡分配一塊記憶體儲存歷史 max map；`CrashFeedback` 也可能有自己的 metadata。這個初始化動作在 `StdState::new` 裡完成，所以需要可變借用。

初始化完後，這兩個借用 **釋放**，feedback 和 objective 稍後可以被 move 進 fuzzer。

- `InMemoryCorpus`：正常種子存 RAM，效能好但重跑不繼承。
- `OnDiskCorpus("./crashes")`：crash 觸發時把輸入寫到磁碟，方便事後重現。

### 6. EventManager 與 Fuzzer

```rust
let mut mgr = SimpleEventManager::new(mon);
let scheduler = QueueScheduler::new();
let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);
```

`SimpleEventManager`：最簡單的事件處理器，接收 fuzzer 框架發出的事件（找到新路徑、找到 crash）並呼叫 monitor 印出統計。

`QueueScheduler`：最簡單的排程器，FIFO 輪轉 corpus 裡的種子，不做優先度計算。

`StdFuzzer::new(scheduler, feedback, objective)`：**這裡 feedback 和 objective 被 move 進 fuzzer**。從這行之後，你不能再用原本的 `feedback` 和 `objective` 變數。

### 7. InProcessExecutor：在同一個 process 裡跑 harness

```rust
let mut executor = InProcessExecutor::new(
    &mut harness,
    tuple_list!(observer),
    &mut fuzzer,
    &mut state,
    &mut mgr,
).expect("Failed to create the Executor");
```

`InProcessExecutor` 直接在當前 process 的 thread 裡呼叫 harness closure，沒有 fork 或 subprocess。

**`tuple_list!(observer)` 把 observer move 進 executor**。從這行之後 `observer` 變數失效。executor 持有 observer，每次執行後自己呼叫 `observer.post_exec()` 更新 map 快照。

`&mut harness`：閉包以可變借用傳入，executor 持有借用而非所有權。

建構 executor 時需要 `&mut fuzzer, &mut state, &mut mgr`：用於設定 signal/crash handler 並讀取配置。

### 8. 初始種子生成

```rust
let mut generator = RandPrintablesGenerator::new(nonzero!(32));

state
    .generate_initial_inputs(
        &mut fuzzer, &mut executor, &mut generator, &mut mgr, 8,
    )
    .expect("Failed to generate the initial corpus");
```

`RandPrintablesGenerator` 生成隨機可印字元（ASCII 32–126）的 `BytesInput`，長度最多 32 byte。`nonzero!(32)` 是 macro，確保傳入 `NonZeroUsize`（型別系統層面禁止長度為 0）。

`generate_initial_inputs(..., 8)` 跑 8 次 generator，每個輸入都執行一遍 harness，依 feedback 決定是否加進 corpus。這確保 fuzzer 開始時 corpus 不是空的。

### 9. Mutator 與 Stages

```rust
let mutator = HavocScheduledMutator::new(havoc_mutations());
let mut stages = tuple_list!(StdMutationalStage::new(mutator));
```

`havoc_mutations()` 回傳一個包含所有 havoc 操作的 tuple：random bit flip、byte 插入、byte 刪除、splice、dictionary 插入等。AFL++ 的 havoc 模式就是這些操作的隨機組合。

`HavocScheduledMutator` 每次被呼叫時，從這些操作裡隨機選 N 個依序執行（N 本身也是隨機的），產生變形後的輸入。

`StdMutationalStage` 把 mutator 包裝成一個 stage：對 corpus 裡每個選出的種子，跑若干次 mutate + execute 的循環。

`tuple_list!` 是 LibAFL 用 Rust 的 heterogeneous tuple type 實現「stages 列表」的方式——可以在 compile time 把不同型別的 stage 串在一起，不用 `dyn trait`，保留 monomorphization 的效能。

### 10. 主迴圈

```rust
fuzzer
    .fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)
    .expect("Error in the fuzzing loop");
```

`fuzz_loop` 正常情況下永遠不返回。每次迭代：scheduler 選種子 → stages 依序執行（mutate + run + feedback） → 更新 corpus → 印統計。

---

## 底層機制：coverage map 的生命週期

```
執行前                    harness 執行中              執行後
─────────────────         ──────────────────          ────────────────────
observer.pre_exec()       harness 呼叫                observer.post_exec()
清零 SIGNALS              signals_set(0)              讀 SIGNALS[0..16]
SIGNALS = [0,0,0,...]    SIGNALS[0] = 1             存到 observer 內部快照
                          signals_set(1)                      │
                          SIGNALS[1] = 1                      v
                                │                   feedback.is_interesting()
                                v                   比對快照 vs max_map
                          ExitKind::Ok              SIGNALS[1] 是新的？
                          (或 panic → Crash)        → yes → 加進 corpus
                                                    max_map[1] = 1
```

重要細節：**executor 在每次執行前需要重置 SIGNALS**，否則上一次的殘留 signal 會污染這次的 coverage 判斷。`InProcessExecutor` 在呼叫 harness 前透過 observer 的 `pre_exec` hook 清零 map。`ConstMapObserver` 的 `pre_exec` 實作就是 `memset(ptr, 0, len)`。

---

## 建置與執行

```bash
# 在 WSL2 Ubuntu 上
cargo build

# 執行（RUST_LOG=info 看更多 log）
RUST_LOG=info cargo run 2>&1
```

預期輸出（前幾秒，實際格式依 SimpleMonitor 實作略有差異）：

```
[Stats] #1    new  cov: 1 corp: 2/2b  exec/s: inf  rss: 0mb
[Stats] #2    new  cov: 2 corp: 3/4b  exec/s: inf  rss: 0mb
[Stats] #3    new  cov: 3 corp: 4/7b  exec/s: inf  rss: 0mb
[Stats] #4    OBJECTIVE  obj: 1
```

`cov` 數字遞增代表 corpus 找到了新路徑，`OBJECTIVE` 代表 crash 被存到 `./crashes/`。具體的統計欄位名稱和格式依 LibAFL 版本略有不同，但 corpus 成長和 objective 出現的行為是確定的。

執行完後：

```bash
ls crashes/
# 應有一個 hex 命名的檔案
cat crashes/<hash>
# 內容應含 "abc"（可能後面跟著隨機 byte）
```

---

## 比較：手動 coverage vs 真實插樁

| 特性 | baby_fuzzer（本章） | afl-cc 插樁 |
|------|-------------------|-------------|
| coverage 來源 | 手動 `signals_set(idx)` | 編譯器自動在每個 branch 邊插入 |
| map 大小 | 16 byte | 65536 byte（預設） |
| edge ID | 手動指定 | 編譯時隨機 XOR 產生 |
| 精度 | 粗（只標記你手動設的點） | 細（每條控制流邊都記錄） |
| 需要工具鏈 | 不需要 | 需要 `afl-clang-fast` 或 `afl-gcc-fast`（需要 afl-clang-fast 或 afl-gcc，WSL2 `apt install afl++` 可取得） |
| 用途 | 學習 / 測試 LibAFL 本身 | 對真實 C/C++ target 模糊測試 |

---

## 常見坑

**坑 1：忘記設 `panic = "abort"`**

`InProcessExecutor` 靠攔截 signal（SIGSEGV/SIGABRT）偵測 crash。Rust 預設的 panic 是 **unwind**，這會在 harness 裡啟動 stack unwinding，跳過 LibAFL 的 crash handler，最終行為未定義（可能卡死、可能把 executor 的 state 搞爛）。

設 `panic = "abort"` 讓 panic 直接觸發 SIGABRT，LibAFL 的 signal handler 接住，回傳 `ExitKind::Crash`，輸入正確被存到 `crashes/`。

這個設定要同時加在 `[profile.dev]` 和 `[profile.release]`，不然 debug build 測試時問題藏著，release build 才爆。

**坑 2：observer move 進 executor 後不可再用**

```rust
let observer = unsafe { ConstMapObserver::from_mut_ptr(...) };
let mut feedback = MaxMapFeedback::new(&observer);  // 借用
// ...
let mut executor = InProcessExecutor::new(
    &mut harness,
    tuple_list!(observer),  // <- observer 在這裡被 move
    ...
);
// 從這行之後 observer 已無效，不能再用它
```

`MaxMapFeedback::new(&observer)` 借用 observer 只是為了讀它的 metadata（map 大小），借用在 `new` 之後立刻結束。之後 observer 可以自由 move。

這個設計有點反直覺：feedback 在執行期間如何讀 map？答案是 feedback 透過 LibAFL 的 `HasObservers` 機制，在 stage 執行時從 executor 那裡借到 observer 的引用。所有權在 executor，存取在 feedback，用 Rust 的借用規則在執行期動態協調。

**坑 3：StdState 必須在 StdFuzzer 之前建立**

```rust
// 正確順序
let mut feedback = MaxMapFeedback::new(&observer);
let mut objective = CrashFeedback::new();
let mut state = StdState::new(
    ..., &mut feedback, &mut objective   // 初始化 metadata（借用）
).unwrap();
// 借用結束
let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);  // move
```

如果先建 `fuzzer`（move feedback/objective），再建 `state`（需要 `&mut feedback`），Rust 編譯器會報「use of moved value」。順序是固定的：**借用初始化 metadata → 釋放借用 → move 進 fuzzer**。

**坑 4：InProcessExecutor crash 會影響整個 fuzzer process**

`InProcessExecutor` **不 fork**。harness 和 fuzzer 框架共用同一個 process。LibAFL 的 signal handler 在 SIGSEGV/SIGABRT 時攔截、記錄輸入、然後繼續跑，但如果 crash 破壞了 fuzzer 框架的內部狀態（例如 harness 把 LibAFL 的 heap 搞爛），繼續跑的結果是未定義的。

對真實 target 用 `InProcessExecutor` 的前提：target 崩潰只影響自己管理的記憶體，不亂寫 fuzzer 框架的資料結構。如果不能保證，改用 `ForkserverExecutor`（fork 一個 child 跑 harness，parent 繼續跑）或 `InProcessForkExecutor`（LibAFL 自己管 fork）。

---

## 進階延伸

### 把 SIGNALS 換成真實插樁 map

真實使用時，把 `SIGNALS` 換成 AFL 的 shared memory map：

```rust
// 需要 afl-clang-fast 編譯 target，WSL2: apt install afl++
use libafl_bolts::shmem::{ShMem, ShMemProvider, UnixShMemProvider};

let mut shmem_provider = UnixShMemProvider::new().unwrap();
let mut shmem = shmem_provider.new_shmem(MAP_SIZE).unwrap();
// 把 shmem 的 id 寫進環境變數讓 afl-cc 插樁的 target 讀到
std::env::set_var("__AFL_SHM_ID", shmem.id().to_string());
let map_ptr = shmem.as_mut_ptr();
// 用 ConstMapObserver::from_mut_ptr 一樣接進 feedback
```

**本段未實測，為理論預期行為。** 驗證步驟：用 `afl-clang-fast` 編譯一個 C target，設好 SHM 環境變數，確認 target 執行後 shmem map 有被寫入（用 `hexdump` 印出非零 byte）。

### 加入 Corpus 持久化

```rust
// 把 InMemoryCorpus 換成 OnDiskCorpus，重跑時繼承上次結果
OnDiskCorpus::new(PathBuf::from("./corpus")).unwrap()
```

重跑前用 `state.load_initial_inputs` 從磁碟讀回上次的 corpus，不用從頭跑。

### 加入 CmpLog（比較日誌）

LibAFL 支援 `CmpLogObserver`，記錄 harness 裡所有比較操作的兩個運算元，然後 `RedQueen` mutator 直接把「被比較的值」插進輸入，解決 magic byte 問題。適合 target 有大量 `if (x == 0xdeadbeef)` 的場景。**本段未實測，為理論預期行為。** 需要搭配 `libafl_targets` 的 CmpLog 插樁支援。

---

## 動手練習

目標：在 baby_fuzzer 基礎上改造，讓 fuzzer 能找到 4 byte 的觸發條件 `"abcd"`。

步驟：
1. 在 harness 裡加第四層：`buf[3] == b'd'` → `signals_set(3)` + panic
2. 確認 `SIGNALS_LEN` 夠大（目前是 16，足夠）
3. 執行 fuzzer，觀察 corpus 成長到 5 個種子（`""`、`"a..."`、`"ab..."`、`"abc..."`、`"abcd..."`）
4. 確認 `./crashes/` 裡的檔案含有 `"abcd"`

進一步挑戰：把觸發條件改成數字比較 `buf[0] == 0x41 && buf[1] == 0xBE && buf[2] == 0xEF`（magic bytes），觀察沒有 CmpLog 時 fuzzer 要跑多少個 execution 才能找到。和純隨機猜測的理論值（256^3 次）做比較，確認 coverage 梯度帶來的加速量。

---

## 章節總結

本章從零組裝了一個完整的 LibAFL in-process fuzzer：

- **SIGNALS 陣列** 模擬 AFL 的 edge coverage bitmap，`signals_set(idx)` 是手動插樁
- **ConstMapObserver** 把 raw pointer 包裝成框架能讀的 observer，`pre_exec` 清零，`post_exec` 快照
- **MaxMapFeedback** 追蹤 map 最大值，有新覆蓋就把輸入加進 corpus，形成 coverage 梯度
- **CrashFeedback** 捕捉 panic，存到磁碟
- **StdState 的建構順序**：先借用 feedback/objective 初始化 metadata，再 move 進 fuzzer
- **InProcessExecutor** 直接在同 process 跑 harness，速度快但不隔離

所有權流動順序：`observer` 借給 `feedback` 初始化 → 釋放 → move 進 `executor`；`feedback` 借給 `state` 初始化 → 釋放 → move 進 `fuzzer`。這個順序不能顛倒。

---

## 自我檢核

- [ ] 能從空白目錄 `cargo run` 跑起 baby_fuzzer，看到 corpus 成長和 OBJECTIVE 出現
- [ ] 知道 `panic = "abort"` 缺少時 crash 會發生什麼（unwind 跳過 signal handler）
- [ ] 能解釋 `tuple_list!(observer)` 為什麼讓 observer 變數之後不能再用
- [ ] 能說出 `StdState::new` 需要 `&mut feedback` 的原因（初始化 metadata）
- [ ] 理解 coverage 梯度為什麼比純隨機快得多，能算出無梯度時的期望執行次數
- [ ] 知道 `InProcessExecutor` 不 fork 的含義與限制

---

## 延伸閱讀

1. **LibAFL baby_fuzzer 官方原始碼** — `fuzzers/baby_fuzzer/src/main.rs`（LibAFL GitHub main branch）。本章所有程式碼直接取自此處，是最可靠的參考，版本演進看 git log。

2. **LibAFL Book: Getting Started** — 官方書（docs.rs/libafl 或 GitHub Pages），詳細說明 `Observer → Feedback → State → Fuzzer → Executor` 的架構設計決策與替換選項，覆蓋所有 executor 和 corpus 類型的比較。

3. **"AFL++: Combining Incremental Steps of Fuzzing Research"**（Fioraldi et al., WOOT 2020）— 說明 coverage feedback 如何驅動 corpus 成長的理論基礎，直接對應本章 coverage 梯度的概念，以及 havoc mutations 的設計動機。

---

第一個 fuzzer 跑起來了，但 SIGNALS 這個手動 coverage map 是怎麼工作的？下一章深挖 Observer 和 Feedback 的內部機制，以及如何自訂 Feedback 抓更複雜的條件。

→ [下一章：Observer 與 Feedback 機制深挖](./06-observer-feedback.md)
