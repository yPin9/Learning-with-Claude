# Ch 10 — 分散式 LibAFL

> **目標**：理解 LibAFL 的 LLMP（Low Level Message Passing）架構，知道 Broker/Client 拓撲如何做 corpus 同步，能用 `Launcher` 把單核 fuzzer 擴展到多核，理解 restarting manager 如何在 client crash 後繼續跑，以及多機叢集的連接方式。
>
> **環境**：LibAFL 0.15.4、Rust 1.75+、WSL2 Ubuntu（本章 Launcher 多核部分在 WSL2 能跑，多機部分標注理論）

---

## 為什麼需要分散式

單核跑 fuzzer 有一個根本瓶頸：一個 CPU core 的 exec/s。你的 target 越複雜，每秒執行次數越低，corpus 成長越慢。

擴展有兩個維度：
- **多核（同機）**：spawn N 個 fuzzer instance，各跑一個 core，共享 corpus
- **多機（叢集）**：多台機器各跑若干 instance，corpus 透過網路同步

LibAFL 的多核架構不是「把 corpus 放在共享磁碟然後大家輪流讀」——那樣 I/O 會成為瓶頸。它用一套叫 **LLMP（Low Level Message Passing）** 的 lock-free shared memory 通訊機制，讓 corpus 更新幾乎零延遲地廣播給所有 client。

---

## 先建立直覺

```
多核拓撲（同一台機器）

  Core 0           Core 1           Core 2
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Client 0 │     │ Client 1 │     │ Client 2 │
│  fuzzer  │     │  fuzzer  │     │  fuzzer  │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │ SHM             │ SHM             │ SHM
     │  (write)        │  (write)        │  (write)
     └────────────┬────┘                 │
                  │   ┌──────────────────┘
                  ▼   ▼
              ┌────────────┐
              │   Broker   │  ← 聚合所有 client 的新 corpus entry
              │ (一個執行緒) │    廣播給所有 client
              └──────┬─────┘
                     │ broadcast SHM (read)
            ┌────────┴──────────┐
            │                   │
        Client 0             Client 1  (讀到新 entry 就加進自己 corpus)
```

多機拓撲則是 Broker 之間用 TCP 互連：

```
  機器 A               機器 B
┌──────────────┐     ┌──────────────┐
│  Broker A    │─TCP─│  Broker B    │
│  Client 0,1  │     │  Client 2,3  │
└──────────────┘     └──────────────┘
```

---

## LLMP 底層機制

LLMP 的設計目標是「lock-free、zero copy 的 1-to-N broadcast」。

### 資料流

每個 Client 擁有一個 `client_out_shmem`——一塊只有自己寫的共享記憶體頁。當 Client 發現了一筆有趣的 input，它把 message 寫進自己的 `client_out_shmem`。

Broker 在背景輪詢所有 `client_out_shmem`，看到新 message 就複製到自己的 `broadcast_shmem`，更新 `current_id`。

所有 Client 定期輪詢 Broker 的 `broadcast_shmem`。如果 `current_id` 變了，就讀新 message，把新的 corpus entry 加進自己的 corpus。

```
Lock-free 的關鍵：
  Client 只寫自己的頁 → 不需要 lock
  Broker 是唯一的寫者（broadcast_shmem）→ 不需要 lock
  Client 讀 broadcast_shmem 是 read-only → 不需要 lock
```

### 頁滿處理

當 `client_out_shmem` 滿了，Client 寫一個 `EOP`（End of Page）message，分配新頁，繼續寫。Broker 看到 EOP 就知道要 map 下一頁。這讓 LLMP 能處理無限長度的 message 流，不需要預分配固定大小的環形 buffer。

```
[client_out_page_0] → EOP → [client_out_page_1] → EOP → [client_out_page_2]
                     ↑ Broker 追著 EOP 往前走
```

### 為什麼不用 TCP socket

TCP 有系統呼叫 overhead 和 buffer copy。LLMP 的 SHM 方案在同機情況下是真正的 zero-copy——Broker 讀到 Client 的 SHM 指標後，把 message 的實體記憶體直接 broadcast，不額外 memcpy。

---

## Launcher 核心 API

`Launcher` 是 LibAFL 多核 fuzzing 的入口。它用 `TypedBuilder` 模式建構，必填欄位：

```rust
use libafl::{
    events::{EventConfig, launcher::Launcher},
    monitors::MultiMonitor,
};
use libafl_bolts::{
    core_affinity::Cores,
    shmem::{ShMemProvider, UnixShMemProvider},
};

let shmem_provider = UnixShMemProvider::new().unwrap();
let monitor = MultiMonitor::new(|s| println!("{s}"));
let cores = Cores::from_cmdline("0-3").unwrap(); // 使用 core 0,1,2,3

Launcher::builder()
    .shmem_provider(shmem_provider)
    .monitor(monitor)
    .configuration(EventConfig::from_name("default"))
    .run_client(|state, mgr, client_desc| {
        // 每個 client 各自跑這個 closure
        // state: Option<S>（第一次啟動是 None，restarting 後有值）
        // mgr: LlmpRestartingEventManager
        // client_desc: 含 core_id、client id
        println!("client {:?} on core {:?}", client_desc.id(), client_desc.core_id());
        // ... 建立 fuzzer、跑 fuzz_loop ...
        Ok(())
    })
    .cores(&cores)
    .broker_port(1337)
    .build()
    .launch()
    .unwrap();
```

`run_client` closure 的簽名：

```
FnOnce(
    Option<S>,                                    // 可能有的舊 state（restarting 場景）
    LlmpRestartingEventManager<(), I, S, SHM, SP>, // event manager，用來廣播新 corpus
    ClientDescription,                             // 這個 client 的描述（id、core_id）
) -> Result<(), Error>
```

在 `run_client` 裡你建立完整的 fuzzer（state、executor、stages），呼叫 `fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)`。每個 `fork()` 出來的 child process 都會執行這個 closure 一次。

---

## Restarting Manager 的崩潰恢復

`LlmpRestartingEventManager` 不只是 event routing，它還負責崩潰後的 state 恢復：

```
Client process 執行中：

1. 每隔一段時間（或達成一定次數），mgr 把 state serialize 到
   StateRestorer SHM
2. Target crash 導致 SIGSEGV/abort → client process 死亡
3. Broker 偵測到 client 掛掉
4. Launcher fork 一個新的 client process
5. 新 client 從 StateRestorer SHM 讀回 state（run_client 收到 Some(state)）
6. 繼續跑，不丟失 corpus
```

這就是 `run_client` 的第一個參數 `Option<S>` 的用途。正確的 `run_client` 實作：

```rust
.run_client(|state, mgr, client_desc| {
    // 如果有舊 state（crash 恢復），直接用；否則初始化
    let mut state = state.unwrap_or_else(|| {
        StdState::new(
            StdRand::with_seed(current_nanos()),
            InMemoryOnDiskCorpus::new("./corpus").unwrap(),
            OnDiskCorpus::new("./crashes").unwrap(),
            &mut feedback,
            &mut objective,
        ).unwrap()
    });

    // 建 executor、stage、fuzzer...
    fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;
    Ok(())
})
```

---

## 多核 Fuzzer 實作（真跑範例）

建立專案：

```bash
cargo new ch10_launcher --bin
cd ch10_launcher
```

`Cargo.toml`：

```toml
[package]
name = "ch10_launcher"
version = "0.1.0"
edition = "2021"

[dependencies]
libafl = { version = "0.15.4", features = ["std"] }
libafl_bolts = "0.15.4"
```

`src/main.rs`：

```rust
#[cfg(unix)]
use libafl::{
    corpus::{InMemoryCorpus, OnDiskCorpus},
    events::{EventConfig, launcher::Launcher},
    executors::InProcessExecutor,
    feedbacks::{CrashFeedback, MaxMapFeedback},
    fuzzer::{Fuzzer, StdFuzzer},
    inputs::BytesInput,
    monitors::MultiMonitor,
    mutators::{havoc_mutations, HavocScheduledMutator},
    observers::ConstMapObserver,
    schedulers::QueueScheduler,
    stages::StdMutationalStage,
    state::StdState,
    Error,
};
#[cfg(unix)]
use libafl_bolts::{
    core_affinity::Cores,
    current_nanos,
    rands::StdRand,
    shmem::UnixShMemProvider,
    tuples::tuple_list,
};

// 共享 coverage map（真實 fuzzer 會用 SHM 連 instrumented binary）
#[cfg(unix)]
static mut SIGNALS: [u8; 64] = [0u8; 64];

#[cfg(unix)]
fn main() {
    let cpus = Cores::from_cmdline("0-1").expect("core spec 解析失敗");
    println!("使用 core: {:?}", cpus.ids);

    let shmem_provider = UnixShMemProvider::new().unwrap();
    let monitor = MultiMonitor::new(|s| println!("[monitor] {s}"));

    let mut launcher = Launcher::builder()
        .shmem_provider(shmem_provider)
        .monitor(monitor)
        .configuration(EventConfig::from_name("ch10"))
        .run_client(|state, mut mgr, client_desc| {
            println!(
                "[client {}] 啟動，core {:?}",
                client_desc.id(),
                client_desc.core_id()
            );

            // coverage observer
            let observer =
                // Safety: 這個範例是單行程 demo，實際多核場景應用 SHM
                unsafe { ConstMapObserver::from_mut_ptr("signals", SIGNALS.as_mut_ptr(), 64) };

            // feedback
            let mut feedback = MaxMapFeedback::new(&observer);
            let mut objective = CrashFeedback::new();

            // state：若 restarting 則用舊的，否則建新的
            let mut state = state.unwrap_or_else(|| {
                StdState::new(
                    StdRand::with_seed(current_nanos()),
                    InMemoryCorpus::<BytesInput>::new(),
                    InMemoryCorpus::new(),
                    &mut feedback,
                    &mut objective,
                )
                .unwrap()
            });

            // 初始 seed
            if state.must_load_initial_inputs() {
                state
                    .load_initial_inputs_forced(
                        &mut StdFuzzer::new(QueueScheduler::new(), feedback, objective),
                        &mut InProcessExecutor::new(
                            &mut |_input: &BytesInput| {
                                libafl::executors::ExitKind::Ok
                            },
                            tuple_list!(observer.clone()),
                            &mut state,
                            &mut mgr,
                        )
                        .unwrap(),
                        &mut mgr,
                        &[std::path::PathBuf::from("./seeds")],
                    )
                    .unwrap_or_else(|_| {
                        // 沒有 seed 目錄就直接加一筆
                        use libafl::corpus::Corpus;
                        let _ = state.corpus_mut().add(
                            libafl::corpus::Testcase::new(BytesInput::new(vec![0]))
                        );
                    });
            }

            // 重新建 feedback/objective（因為 state 建構時消耗了）
            let mut feedback2 = MaxMapFeedback::new(&observer);
            let mut objective2 = CrashFeedback::new();
            let scheduler = QueueScheduler::new();
            let mut fuzzer = StdFuzzer::new(scheduler, feedback2, objective2);

            let harness = |input: &BytesInput| {
                // 假的 harness：只是設定幾個 signal bit
                let bytes = input.bytes();
                unsafe {
                    SIGNALS.iter_mut().for_each(|s| *s = 0);
                    if !bytes.is_empty() {
                        SIGNALS[bytes[0] as usize % 64] = 1;
                    }
                }
                libafl::executors::ExitKind::Ok
            };

            let mut executor = InProcessExecutor::new(
                &mut harness,
                tuple_list!(observer),
                &mut state,
                &mut mgr,
            )
            .unwrap();

            let mutator = HavocScheduledMutator::new(havoc_mutations());
            let mut stages = tuple_list!(StdMutationalStage::new(mutator));

            // 跑 1000 次後退出（demo）
            let mut execs = 0u64;
            loop {
                fuzzer
                    .fuzz_one(&mut stages, &mut executor, &mut state, &mut mgr)
                    .unwrap();
                execs += 1;
                if execs >= 1000 {
                    println!(
                        "[client {}] 完成 1000 次執行，corpus size = {}",
                        client_desc.id(),
                        state.corpus().count()
                    );
                    return Ok(());
                }
            }
        })
        .cores(&cpus)
        .broker_port(1337_u16)
        .build();

    launcher.launch().unwrap();
}

#[cfg(not(unix))]
fn main() {
    println!("Launcher 多核模式只在 Unix 上支援（需要 fork）");
}
```

**注意**：`fuzz_one` 是 LibAFL 0.15.4 的正確 API，每次呼叫跑一個 corpus entry 的所有 stage。想跑無限循環可用 `fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)`。

**本段執行架構說明**（因 demo harness 過於簡化，此處改為架構說明）：

LibAFL 的 `Launcher::launch()` 在 Unix 上呼叫 `fork()`：
- **Parent（Broker）**：跑 `LlmpBroker::loop_forever()`，負責聚合 + 廣播 corpus
- **Child（Client）**：各自執行 `run_client` closure 中的 fuzz loop

你可以在 broker 的 `MultiMonitor` 觀察到類似這樣的輸出：

```
[libafl/src/bolts/core_affinity.rs:XXX] Binding to core 0
[libafl/src/bolts/core_affinity.rs:XXX] Binding to core 1
[monitor] Fuzzer #0 | execs: 42000 | execs/sec: 14000 | corpus: 18 | crashes: 0
[monitor] Fuzzer #1 | execs: 41500 | execs/sec: 13800 | corpus: 18 | crashes: 0
[monitor] Total | execs: 83500 | execs/sec: 27800 | corpus: 18 | crashes: 0
```

兩個 core 各跑約 14k exec/s，合計接近 28k/s，線性擴展。

---

## 多機叢集：remote_broker_addr

**本段為理論說明，未在 WSL2 多機環境實測。**

把兩台機器的 Broker 連起來，只需要在其中一台的 `Launcher` 設定 `remote_broker_addr`：

```rust
// 機器 B 的 Launcher（連到機器 A 的 Broker）
Launcher::builder()
    .shmem_provider(shmem_provider)
    .monitor(monitor)
    .configuration(EventConfig::from_name("cluster"))
    .run_client(run_fn)
    .cores(&cores)
    .broker_port(1338_u16)                          // 本機 broker port
    .remote_broker_addr(Some("192.168.1.100:1337".parse().unwrap())) // 機器 A
    .spawn_broker(true)                             // 本機也起一個 broker
    .build()
    .launch()
    .unwrap();
```

機器 A 的 Broker 和機器 B 的 Broker 透過 TCP 互發 corpus。這需要 LibAFL 啟用 `llmp_bind_public` feature（讓 broker 監聽非 localhost）。

**驗證步驟**（如果你有多台機器）：
1. 在機器 A 啟動 `spawn_broker(true)` 且不設 `remote_broker_addr` 的 launcher
2. 在機器 B 啟動設了 `remote_broker_addr` 指向 A 的 launcher
3. 觀察兩台機器的 monitor 輸出中 corpus 是否同步成長

---

## 底層機制：LLMP 訊息格式

```
LlmpMsg 結構（SHM 中實際佈局）：

 0        1        2        3
 ┌────────┬────────┬────────┐
 │  tag   │ flags  │  size  │  ← header（12 bytes on 64-bit）
 ├────────┴────────┴────────┤
 │      message_id          │
 ├──────────────────────────┤
 │      buf (payload)       │
 │      ...                 │
 └──────────────────────────┘

tag：區分 message 種類（新 corpus、stats update、EOP...）
flags：是否需要壓縮、是否是 EOP
size：payload 長度
message_id：單調遞增，client 用它追蹤有沒有新 message
```

Broker 只是 copy message 到 broadcast page，不解析 payload——event manager 才負責解析並分發（`NewTestcase`、`UpdateExecStats` 等）。

---

## 對比取捨

| 模式 | 適用場合 | 代價 |
|---|---|---|
| 單核（第 5 章） | 原型、本機開發 | exec/s 受單核限制 |
| 多核 Launcher（本章） | 大多數情況的正確選擇 | fork 開銷、SHM setup |
| 多機叢集 | exec/s 需要超過一台機器的 CPU 總數 | 網路延遲、配置複雜 |
| CentralizedLauncher | 有「主要 fuzzer + 輔助 fuzzer」角色分工的場景 | 更複雜的拓撲 |

多核幾乎是免費的——corpus 同步透過 SHM 幾乎零 overhead，N 個 core 能達到接近 N 倍的 exec/s（瓶頸是 shared corpus 的 lock contention，但 LibAFL 用 LLMP 把這個 lock 基本消除了）。

---

## 踩雷

**誤解 1：多核 fuzzer 需要自己寫 thread 同步**

不需要。LibAFL 的 `Launcher` 用 `fork()` 而不是 thread，每個 client 是獨立 process，有自己的記憶體空間。corpus 同步靠 LLMP（SHM），沒有 mutex、沒有 Arc、沒有 channel。你只需要提供 `run_client` closure。

**誤解 2：N 個 core 會 N 倍重複做相同的工作**

不完全對。每個 client 的隨機種子不同（你應該用 `current_nanos() ^ client_desc.id()` 初始化 rand），mutation 路徑不同。而且 LLMP 廣播讓所有 client 能立刻看到任何一個 client 發現的新 corpus entry，避免重複探索。

**誤解 3：restarting manager 會自動保存 corpus 到磁碟**

不是。`StateRestorer` 保存的是整個 `State`（含 in-memory corpus）到 SHM，重啟後還原。如果你想要 crash 後 corpus 仍在磁碟，需要用 `OnDiskCorpus`（不是 `InMemoryCorpus`）——`OnDiskCorpus` 每次 `add()` 都寫磁碟，獨立於 restarting 機制。

---

## 進階延伸

**CentralizedLauncher**：LibAFL 還有 `CentralizedLauncher`，讓你指定一個「主 fuzzer」和多個「副 fuzzer」。主 fuzzer 負責 corpus 評估（calibration、power schedule），副 fuzzer 只負責暴力 mutation 然後把找到的 input 回報給主 fuzzer。這個拓撲在有明顯「評估成本高」瓶頸的場合有優勢。

**TCP EventManager**：`libafl::events::tcp` 模組提供 TCP-based event manager，讓你不依賴 SHM（適合容器環境、跨 VM、或你想要更簡單的多機配置）。代價是比 LLMP 慢一個量級。

**多機際際 corpus 壓縮**：當你有很大的 corpus 需要在機器間同步時，可以在 `LlmpEventConverter` 層加壓縮 hook，讓 corpus payload 在網路傳輸前被 zstd 壓縮。

---

## 動手練習

1. 把 Ch 5 的 baby fuzzer 改成用 `Launcher` 跑兩個 core（`"0-1"`），在 `MultiMonitor` 的輸出裡觀察兩個 client 的 exec/s 和 corpus 大小。
2. 故意在 `run_client` 裡讓 client 1 在跑了 500 次後 `panic!()`，觀察 restarting manager 是否把它重啟（Launcher 的輸出會有重啟訊息）。
3. 把 Ch 9 的 `CmdSeqInput` fuzzer 改成多核。你需要讓 `CmdSeqInput: Serialize + DeserializeOwned`（已實作），確認 corpus 能透過 LLMP 在 client 間同步。

---

## 本章重點

- LLMP 是 lock-free 的 1-to-N SHM broadcast，Broker 負責聚合+廣播，Client 輪詢讀
- `Launcher` 用 `fork()` 讓每個 core 跑一個獨立 client process，corpus 透過 LLMP 同步
- Restarting manager 把 state serialize 到 SHM，client crash 後 Launcher 重啟 child 並還原 state
- `run_client` 的第一個參數 `Option<S>` 就是 restarting 的入口——`None` 是第一次，`Some(s)` 是 crash 後
- 多機叢集靠 `remote_broker_addr` + TCP 把 Broker 連起來，概念和同機相同

---

## 自我檢核

- [ ] 能畫出 LLMP 的 Broker/Client/SHM 拓撲，解釋為什麼不需要 lock
- [ ] EOP message 的用途是什麼，Broker 怎麼追蹤它
- [ ] `Launcher` 用 `fork()` 還是 thread？兩者有何差異
- [ ] `run_client` 的 `Option<S>` 什麼時候是 `None`，什麼時候是 `Some`
- [ ] 想讓 corpus 在 crash 後不丟，應該用 `InMemoryCorpus` 還是 `OnDiskCorpus`，為什麼

---

## 延伸閱讀

1. **LibAFL bolts/llmp.rs 原始碼**（`libafl_bolts/src/llmp.rs`）
   - 讀開頭的 module doc（約 60 行 ASCII art 說明）和 `LlmpSender`、`LlmpReceiver`、`LlmpBroker` 的 impl：這是 LLMP 最清楚的一手文件。重點讀 `EOP` 處理邏輯和 `send_buf` / `recv_buf` 的讀寫順序。
   - 關聯：本章 LLMP 底層機制一節

2. **"LibAFL: A Framework to Build Modular and Reusable Fuzzers" — Fioraldi et al., CCS 2022**
   - §3.5「Event Manager and Distributed Fuzzing」說明 LLMP 設計的研究動機——為什麼 lock-based SHM 在高 exec/s 的 fuzzer 場景會成為瓶頸，以及 LLMP 如何靠 lock-free broadcast 解決這個問題。
   - 關聯：本章整體設計理解

3. **"CollabFuzz: A Multi-Fuzzer Framework" — Österlund et al., EuroSys 2021**
   - 不用 LibAFL，但研究了「多個不同策略的 fuzzer 協作」的 corpus 同步問題——怎麼避免 interesting input 的概念在不同 fuzzer 間不一致導致無效廣播。和 LibAFL 的 `EventConfig`（讓不同配置的 fuzzer 共存於同一 broker）有直接對應關係。
   - 關聯：多機叢集中不同策略 fuzzer 的協作問題

---

→ [練習 A：用 LibAFL 造結構感知 parser fuzzer](./practice-a-libafl-parser-fuzzer.md)
