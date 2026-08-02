# Ch 4 — LibAFL 哲學：fuzzer 是可組合元件

> **目標**：理解 LibAFL 把 fuzzer 拆解成 12 種可替換元件的設計思路，弄清每個元件的職責邊界，特別是最容易搞混的 Feedback vs. Objective 二分法，最後能看懂 baby_fuzzer 的型別簽名，知道 Rust 型別系統如何在編譯期把接線錯誤攔下來。

---

## 為什麼需要「可組合」這個想法

AFL++ 是一個單體程式。你下載它、編譯它、跑它。想要改一個元件？你 fork 整個 repo，在幾萬行 C 裡找到對應的函式，改完再驗證你沒有打亂其他東西。這個模型對「跑 AFL++ 的人」很友善，對「研究新 fuzzing 技術的人」是惡夢。

研究者遇到的問題很具體：

- 我想換一個更好的種子排程演算法，但排程邏輯和覆蓋率計算、bitmap 操作全部纏在一起。
- 我想讓 fuzzer 同時追蹤覆蓋率和記憶體使用量，但 AFL++ 的 feedback 機制只設計給一種觀測值。
- 我想在 LibFuzzer in-process 模式和 fork-server 模式之間切換，但兩個程式的架構差異太大，測試結果沒法直接比較。

LibAFL 的答案是：**把 fuzzer 定義成一組可獨立替換的型別，讓 Rust 編譯器替你驗證接線正確**。這不是「彈性架構」這種空話，而是一個明確的技術賭注：用泛型參數和 trait bound 取代執行期的 if/switch，讓任何接線錯誤在 `cargo build` 就爆掉，而不是在跑了三天的 fuzzing session 裡默默失效。

---

## 先建立直覺：一張全景圖

在看任何 Rust 程式碼之前，先把整個資料流畫出來。

```
                    ┌──────────────────────────────────────────┐
                    │                 State                    │
                    │           (StdState)                     │
                    │                                          │
                    │   ┌────────────────┐  ┌───────────────┐ │
                    │   │    Corpus      │  │  Obj. Corpus  │ │
                    │   │ (interesting   │  │  (crashes /   │ │
                    │   │   inputs)      │  │   solutions)  │ │
                    │   └───────▲────────┘  └──────▲────────┘ │
                    │           │                   │          │
                    │   ┌───────┴────────┐  ┌──────┴────────┐ │
                    │   │   Feedback     │  │  Objective    │ │
                    │   │ (interesting?) │  │  (solution?)  │ │
                    │   └───────▲────────┘  └──────▲────────┘ │
                    └───────────┼────────────────────┼─────────┘
                                │                    │
                                └──────────┬─────────┘
                                           │ 讀觀測資料
                               ┌───────────▼──────────┐
                               │      Observer(s)      │
                               │  (map, time, ...)     │
                               └───────────▲───────────┘
                                           │ 執行後填值
┌────────────┐  input   ┌──────────────────▼─────────────────┐
│ Scheduler  │─────────▶│            Executor                 │
│ (which     │          │   (InProcess / Forkserver)          │
│  entry?)   │          │   runs harness, notifies observers  │
└──────▲─────┘          └─────────────────────────────────────┘
       │ corpus entry
       │
┌──────┴─────┐
│   Stage    │
│ (mutation  │◀──── Mutator (HavocScheduledMutator, ...)
│  rounds)   │
└────────────┘

   EventManager: stats 輸出 / logging / 多核心 sync
   StdFuzzer: 把上面全部串起來的編排者
```

從這張圖讀出幾個關鍵流向：

1. **Scheduler** 從 Corpus 選一個 input 交給 **Stage**
2. **Stage** 叫 **Mutator** 變異這個 input，產出新 input
3. **Executor** 跑 harness，同時讓 **Observer** 記錄原始資料（coverage bitmap、執行時間…）
4. **Feedback** 讀 Observer，判斷這個 input 是否「有趣」，有趣就加入 Corpus
5. **Objective** 讀 Observer，判斷這個 input 是否「是解」（crash、特定條件），是就加入 Objective Corpus
6. **State** 是所有持久資料的容器：Corpus、Objective Corpus、RNG、metadata 全在裡面

---

## 12 個元件，逐一拆解

### 1. Input

一次跑 target 的「原料」。最常用的是 `BytesInput`，就是 `Vec<u8>`。你也可以定義結構化 input（JSON AST、網路封包結構），只要實作 `Input` trait。

```rust
use libafl::inputs::BytesInput;

let input = BytesInput::new(vec![0x41, 0x42, 0x43]);
```

Input 本身不帶任何 fuzzing 邏輯，它只是「被操作的資料」。型別可以是任意的，只要 Mutator 知道怎麼改它。

---

### 2. Corpus

Corpus 儲存「值得繼續 fuzz 的 input」，也就是 Feedback 認為有趣的那些。

```
InMemoryCorpus<BytesInput>   ── 全放記憶體，重啟後消失，適合短跑測試
OnDiskCorpus<BytesInput>     ── 序列化到磁碟，長跑用，支援 resume
```

State 持有兩個 Corpus：一個給 Feedback 寫入（coverage 有趣的 input），一個給 Objective 寫入（crash）。這兩個 Corpus 的型別可以不同，例如主 Corpus 在記憶體、Objective Corpus 寫磁碟。

---

### 3. State（StdState）

State 是所有可序列化持久資料的唯一容器。它持有：

- Corpus（有趣 input）
- Objective Corpus（解）
- RNG（StdRand）
- 每個 input 的 metadata（執行次數、上次 fuzz 時間、能量…）

```rust
let mut state = StdState::new(
    StdRand::with_seed(current_nanos()),
    InMemoryCorpus::<BytesInput>::new(),   // corpus
    InMemoryCorpus::<BytesInput>::new(),   // objective corpus
    &mut feedback,
    &mut objective,
)?;
```

State 是 checkpointing 的單位。如果你要讓 fuzzer 支援 snapshot/restore，你序列化的就是 State。建構 State 時傳入 `&mut feedback` 和 `&mut objective` 是讓它們有機會初始化自己在 State 內部儲存的 metadata（例如 MaxMapFeedback 需要在 State 裡存 history map）。

---

### 4. Observer

Observer **只負責觀測，不做判斷**。它在每次 target 執行後，把原始資料填進自己的欄位裡，等 Feedback 和 Objective 來讀。

```
ConstMapObserver       ── 觀測一塊固定大小的 byte 陣列（例如 coverage bitmap）
HitcountsMapObserver   ── 同上，但把 hit count 壓縮成 bucket 值（AFL 經典手法）
TimeObserver           ── 記錄這次執行花了多久
```

Observer 本身不決定「這次執行有沒有新覆蓋率」，它只是填資料的容器。判斷交給 Feedback。

LibAFL 0.15.x 的 API：

```rust
// coverage map 是一個靜態陣列，透過裸指標建立 observer
static mut MAP_FEEDBACK_STATE: [u8; MAP_SIZE] = [0u8; MAP_SIZE];

let observer = unsafe {
    ConstMapObserver::from_mut_ptr(
        "coverage",
        nonnull_raw_mut!(MAP_FEEDBACK_STATE),
        MAP_SIZE,
    )
};
```

名字字串（`"coverage"`）是這個 observer 的唯一識別符，Feedback 用它來找到正確的 observer。

---

### 5. Feedback

Feedback 讀 Observer 的資料，回答一個問題：**這個 input 值得加入 Corpus 嗎？**

```
MaxMapFeedback    ── 讀 coverage map，如果有任何 edge 的計數超過目前最大值，就「有趣」
TimeFeedback      ── 讀 TimeObserver，把執行時間當成有趣度的輔助指標
```

`is_interesting()` 返回 true → Corpus 得到這個 input，Scheduler 之後會再挑它出來繼續 fuzz。

```rust
let mut feedback = MaxMapFeedback::new(&observer);
```

`MaxMapFeedback::new` 拿的是 observer 的 reference，不是 clone。這個 borrow 關係被 Rust 型別系統追蹤，確保 observer 的生命週期涵蓋 feedback 的生命週期。建構 feedback 必須在 observer move 進 executor 之前完成。

---

### 6. Objective

Objective 讀 Observer 的資料，回答另一個問題：**這個 input 是我要找的「解」嗎？**

```
CrashFeedback      ── target crash 了就是解
TimeoutFeedback    ── target 超時了就是解
```

「解」不進 Corpus，而是進 **Objective Corpus**（對應 AFL++ 的 `crashes/` 目錄）。

```rust
let mut objective = CrashFeedback::new();
```

`CrashFeedback` 不需要讀任何 Observer，它直接問 Executor「這次執行有沒有 crash」。但你也可以寫一個讀 Observer 資料的 Objective，例如「如果 coverage 觸碰到某個特定 edge 就算找到了」。

---

### 7. Feedback vs. Objective：最容易搞混的邊界

這是新手最常踩的坑，用一個獨立節說清楚。

```
執行 target
     │
     ├── Feedback::is_interesting() ──▶ true  ──▶ 加入 Corpus（繼續 fuzz 用）
     │                                   false ──▶ 丟棄
     │
     └── Objective::is_interesting() ──▶ true  ──▶ 加入 Objective Corpus（你要的結果）
                                         false ──▶ 丟棄
```

兩者都可以觀測同一個 Observer，但目的完全不同：

| | Feedback | Objective |
|---|---|---|
| 問的問題 | 這個 input 值得繼續 fuzz？ | 這個 input 是我要的解？ |
| 寫入目的地 | Corpus（主語料庫） | Objective Corpus |
| 典型實作 | MaxMapFeedback（新覆蓋率） | CrashFeedback（crash） |
| 對應 AFL++ | `queue/` | `crashes/` |
| 對 fuzzer 的作用 | 驅動探索、找更多路徑 | 終止條件 / 結果收集 |

一個 fuzzer 裡，Feedback 是驅動探索的引擎，Objective 是你真正在找的東西。兩者職責不互換。

---

### 8. Mutator

Mutator 把一個 input 轉換成另一個 input。`HavocScheduledMutator` 是 AFL 經典 havoc 模式的 LibAFL 實作：隨機選一堆 mutation operator（bitflip、splice、interesting values…），連續施加多次。

```rust
use libafl::mutators::{StdScheduledMutator, havoc_mutations};

let mutator = StdScheduledMutator::new(havoc_mutations());
```

`havoc_mutations()` 返回一組預設的 `BytesInput` mutator 集合。你可以插入自訂 operator，或整個換掉換成只做特定操作的 mutator。Mutator 是無狀態或帶極少狀態的，它的輸出只依賴輸入的 input 和 RNG。

---

### 9. Stage

Stage 驅動「對同一個 corpus entry 要做幾輪 mutation」。最常用的是 `StdMutationalStage`：對一個 input 跑固定或動態次數的「mutate → execute → feedback」迴圈。

```rust
use libafl::stages::StdMutationalStage;

let mut stages = tuple_list!(StdMutationalStage::new(mutator));
```

你可以把多個 Stage 串成 tuple，Fuzzer 會依序執行每個 Stage。例如 `tuple_list!(MinimizationStage::new(...), StdMutationalStage::new(...))` 先縮小 input 再 fuzz。Stage 的順序在編譯期固定，不是執行期的 if 判斷。

---

### 10. Executor

Executor 負責實際執行 target harness，並且在執行後通知所有 Observer 填值。

```
InProcessExecutor    ── harness 是當前 process 裡的一個函式（最快，crash 會死整個 fuzzer）
ForkserverExecutor   ── 透過 forkserver 協定跑外部 binary（穩定，多一次 fork 成本）
```

```rust
let mut executor = InProcessExecutor::new(
    &mut harness,
    tuple_list!(observer),   // observer 在這裡被 move 進去
    &mut fuzzer,
    &mut state,
    &mut mgr,
)?;
```

`tuple_list!(observer)` 把 observer 的所有權轉移給 executor。這就是為什麼 Feedback 要在 executor 建構之前用 reference 拿到 observer。執行後 executor 呼叫每個 observer 的 `post_exec()` 方法填值，然後 Feedback 和 Objective 才去讀這些值。

---

### 11. Scheduler

Scheduler 決定「下次從 Corpus 拿哪個 input 出來 fuzz」。

```
QueueScheduler                       ── FIFO，最簡單、最可預測
IndexesLenTimeMinimizerScheduler     ── 優先選短的、執行快的 input（加速迴圈）
WeightedScheduler                    ── 依 energy 加權（AFLfast 概念）
```

換 Scheduler 不需要動其他任何元件，因為它的介面只有「給我下一個 corpus entry 的 index」。不同 Scheduler 的差異完全在選擇策略，和 mutation、execution、feedback 邏輯完全解耦。

---

### 12. EventManager 和 StdFuzzer

**EventManager** 處理 stats 輸出、logging、多核心間的 event 同步：

```
SimpleEventManager         ── 單核心，只做 stats 輸出
LlmpRestartingEventManager ── 多核心，透過 LLMP（lock-less message passing）協定做 broker/client 架構
```

**StdFuzzer** 是最外層的編排者。它持有 Scheduler、Feedback、Objective，並提供 `fuzz_loop()` 把整個迴圈跑起來：

```rust
let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);
fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;
```

`fuzz_loop` 是不停跑的主迴圈，每次迭代呼叫一次 `fuzz_one()`，依序走過 Scheduler → Stage → Executor → Feedback/Objective → EventManager。

---

## AFL++ 單體 vs. LibAFL 可組合

用一個具體場景說明差異：你要 fuzz 一個有多種輸入格式的 server，需要同時追蹤程式覆蓋率和一個自訂的「有效封包計數」。

**AFL++ 的做法**：patch `afl-fuzz.c`，加一個全域變數，在 `save_if_interesting()` 裡加條件。這個 patch 和 AFL++ 的 upstream 更新幾乎不相容，每次上游改動都要重新 merge，而且你沒辦法輕易地換回「只追蹤覆蓋率」的版本來做對照實驗。

**LibAFL 的做法**：

```
1. 寫 CustomPacketObserver，在每次執行後填入「有效封包數」
2. 寫 CustomPacketFeedback，如果有效封包數 > 前次最大值就返回有趣
3. 把 feedback 改成：
   tuple_list!(
       MaxMapFeedback::new(&cov_observer),
       CustomPacketFeedback::new(&pkt_observer),
   )
4. cargo build
```

型別系統驗證你有把 `pkt_observer` 傳給 executor，驗證兩個 Feedback 的 Observer 型別一致，驗證生命週期正確。接線錯誤在編譯期就爆，不是在跑了一小時後發現 feedback 一直返回 false。要換回「只追蹤覆蓋率」版本，把 tuple 改回單個 `MaxMapFeedback` 就好，不用動 harness、mutator 或任何其他東西。

---

## Rust 型別系統如何強制正確接線

LibAFL 用泛型參數把元件圖編碼進型別。`StdFuzzer` 的核心型別簽名大概是：

```rust
pub struct StdFuzzer<CS, F, OF, OT>
where
    CS: Scheduler,
    F:  Feedback<S>,
    OF: Feedback<S>,
    OT: ObserversTuple<I, S>,
```

當你寫 `StdFuzzer::new(scheduler, feedback, objective)` 時，編譯器確認：

- `feedback` 實作了 `Feedback<S>` trait，且其關聯的 Observer 名稱和 executor 的 observer tuple 裡的名稱一致
- `objective` 同上
- `scheduler` 實作了 `Scheduler`

如果你把 observer 傳給 executor 但忘了傳給 feedback，你得到一個型別錯誤，而不是一個在執行期永遠返回 false 的 feedback。

`tuple_list!` 巨集把一堆異質型別串成一個 Rust tuple 的遞迴結構，讓它們能被統一迭代，同時又保留每個元素的具體型別，讓 trait dispatch 在編譯期解析而不是執行期動態派發。這和 C++ 的 variadic templates 思路相似，但 Rust 的 lifetime checker 還能替你驗證 observer reference 的有效性。

---

## 底層機制：一次完整執行的時序

```
fuzz_loop() 呼叫 fuzzer.fuzz_one()
│
├─ scheduler.next(&mut state)
│     └─ 返回 corpus_id: CorpusId
│
├─ 依序執行所有 stages
│     └─ StdMutationalStage::perform()
│           ├─ 計算這次要 mutate 幾次 (num_mutations)
│           └─ 迴圈 num_mutations 次：
│                 │
│                 ├─ 複製 input，呼叫 mutator.mutate(&mut state, &mut input)
│                 │
│                 ├─ executor.run_target(&mut fuzzer, &mut state, &mut mgr, &input)
│                 │     ├─ 呼叫 harness(input.bytes())
│                 │     └─ 呼叫所有 observer.post_exec()  ← 填值
│                 │
│                 ├─ feedback.is_interesting(&mut state, &mut mgr, &input, &observers, exit_kind)
│                 │     └─ 讀 observer 資料
│                 │     └─ true → state.corpus_mut().add(input, metadata)
│                 │
│                 └─ objective.is_interesting(&mut state, &mut mgr, &input, &observers, exit_kind)
│                       └─ true → state.solutions_mut().add(input, metadata)
│                       └─ true → mgr.fire(Event::Objective { ... })
│
└─ mgr.process_events(&mut state)  ← stats 輸出、多核心 sync
```

---

## 進階：把元件換掉意味著什麼

| 想做的事 | 換哪個元件 | 換成什麼 |
|---|---|---|
| fuzz 有 forkserver 的 binary | Executor | `ForkserverExecutor` |
| 追蹤 edge coverage 而非 block | Observer | `HitcountsMapObserver` |
| 優先 fuzz 執行快的 input | Scheduler | `IndexesLenTimeMinimizerScheduler` |
| 找 OOM 而非 crash | Objective | 自訂讀 `/proc/self/status` 的 Objective |
| 用結構化 mutation（例如 protobuf） | Input + Mutator | 自訂 Input type + 對應 Mutator |
| 多核心水平擴展 | EventManager | `LlmpRestartingEventManager` |
| 同時追蹤兩種 feedback | Feedback | `tuple_list!(FeedbackA, FeedbackB)` |

每次替換後，`cargo build` 就是你的正確性驗證。換完之後和原本版本跑相同 benchmark，結果差異是真實的演算法差異，不是實作差異。這就是為什麼 FuzzBench 上的 LibAFL-based fuzzer 結果比較可信：同樣的 harness、同樣的 executor，只換 scheduler，控制變數乾淨。

---

## 比較表：AFL++ 單體 vs. LibAFL 可組合

| 面向 | AFL++ | LibAFL |
|---|---|---|
| 架構 | 單體 C binary | Rust library，型別參數化 |
| 替換元件 | fork + patch，難維護 | 換型別，cargo build 驗證 |
| 新增 feedback 維度 | 改全域狀態 | 加一個 trait impl + tuple 插入 |
| 接線錯誤偵測時機 | 執行期（常見靜默失效） | 編譯期（型別錯誤） |
| 入門門檻 | 低（binary 直接跑） | 高（需要懂 Rust 泛型） |
| 研究可重現性 | 低（各 fork 不相容） | 高（元件獨立替換，控制變數清晰） |
| 執行效能 | 極高（C，多年手工優化） | 接近（Rust zero-cost abstraction） |
| 多核心 | afl-whatsup + 多 instance | LLMP 內建，單程式多核 |

---

## 陷阱集

### 陷阱 1：把 Feedback 當 Objective 用（或反過來）

**錯誤直覺**：「CrashFeedback 是判斷 crash 的，放在 feedback 參數就好了。」

**真相**：放在 feedback 參數的 `CrashFeedback` 會把每個 crash input 加進「繼續 fuzz 的 corpus」，而不是「解的 corpus」。你的 fuzzer 會把 crash 當種子一直 fuzz，但永遠不會把 crash 存下來，`solutions/` 目錄會是空的。更糟的是不會有編譯錯誤，因為 `CrashFeedback` 確實實作了 `Feedback` trait，型別系統不會攔你。

**正確做法**：crash 相關的判斷放 `objective` 參數，coverage 相關放 `feedback` 參數。`StdState::new` 的第四個參數是 feedback，第五個是 objective，順序不可調換。

---

### 陷阱 2：Observer 的生命週期和建構順序

**錯誤直覺**：「observer 建立之後，讓 feedback 和 executor 各拿一份 clone 就好了。」

**真相**：`MaxMapFeedback::new(&observer)` 拿的是 reference。`InProcessExecutor::new(..., tuple_list!(observer), ...)` 拿的是所有權（move）。你必須先建 feedback（拿 reference），再把 observer move 進 executor。如果順序反了，borrow checker 會報錯。如果你試圖 clone observer，兩份 clone 會有獨立的 bitmap，Feedback 讀到的不是 Executor 填的那份，fuzzer 會跑起來但 coverage 永遠不增加。

**正確順序**：

```rust
let observer = /* 建立 observer */;
let mut feedback = MaxMapFeedback::new(&observer);   // 先拿 reference
// ... 建立 state（需要 &mut feedback）...
let mut executor = InProcessExecutor::new(           // 再 move 進 executor
    &mut harness,
    tuple_list!(observer),
    ...
);
```

---

### 陷阱 3：用「單體設定檔」思維操作 LibAFL

**錯誤直覺**：「我要設定 mutation rate，應該有一個 config struct 或環境變數可以調。」

**真相**：LibAFL 沒有中央設定檔。mutation rate 是 Mutator 型別的建構函式參數；種子排程策略是 Scheduler 型別；覆蓋率計算方式是 Observer 型別。「設定」在 LibAFL 裡是「選擇不同的型別」。如果你找不到某個旋鈕，你要問的問題是「哪個元件負責這件事」，然後換掉那個元件的實作，或者為那個型別實作你自己的 trait。

---

### 陷阱 4：Observer 必須同時出現在 Executor 和 Feedback 裡

**錯誤直覺**：「Feedback 讀 Observer 的資料，所以只要讓 Feedback 持有 Observer 就夠了，不需要傳給 Executor。」

**真相**：Observer 需要在每次 `executor.run_target()` 之後被通知填值。這個通知是 Executor 呼叫的（`observer.post_exec()`），所以 Observer 必須在 Executor 的 observer tuple 裡。Feedback 讀的是 Observer 裡已經填好的值，所以它需要一個指向同一個 Observer 實例的 reference。兩邊都需要「接觸到同一個 Observer」，但持有方式不同：Executor 持有所有權，Feedback 持有 reference。

---

## 延伸練習

拿 baby_fuzzer 的原始碼（`libafl/fuzzers/baby_fuzzer/src/main.rs`），做以下觀察：

1. 找出 `tuple_list!` 在哪裡被呼叫，它各自連接了哪些元件
2. 確認 `StdState::new` 的第四、第五個參數分別是什麼，對照本章 Feedback vs. Objective 說明
3. 試著把 `InMemoryCorpus` 換成 `OnDiskCorpus("/tmp/corpus")`，看看需要加什麼 import，以及編譯錯誤提示了什麼

不用真的跑（下一章才動手實作），只看型別簽名和編譯錯誤訊息，練習從 Rust 的錯誤裡讀出元件接線問題的位置。

---

## 章節摘要

- LibAFL 把 fuzzer 拆成 12 個可替換元件：Input、Corpus、State、Observer、Feedback、Objective、Mutator、Stage、Executor、Scheduler、EventManager、StdFuzzer
- Feedback 判斷「值得繼續 fuzz」→ 寫入 Corpus；Objective 判斷「是解」→ 寫入 Objective Corpus；兩者平行呼叫，不互換，型別系統不會替你攔接反的情況
- Observer 只記錄原始資料，不做判斷；判斷是 Feedback / Objective 的事；Observer 必須同時存在於 Executor（所有權）和 Feedback（reference），建構順序是 observer → feedback → executor
- Rust 型別系統把元件接線錯誤提前到編譯期；`tuple_list!` 把異質元件串成 compile-time heterogeneous tuple
- 換任何單一元件不需要動其他部分，這是 LibAFL 相較於 AFL++ fork/patch 的核心優勢，也是讓研究結果可對照比較的前提

---

## 自我檢核

- [ ] 我能不看圖說出 Feedback 和 Objective 的差異，以及各自的輸出去哪裡
- [ ] 我知道 Observer 為什麼要同時傳給 Executor 和 Feedback，以及持有方式為何不同
- [ ] 我能解釋為什麼 LibAFL 把「設定旋鈕」實作成「型別替換」而不是 config struct
- [ ] 我知道 `StdState::new` 的 feedback 和 objective 參數的正確位置和作用
- [ ] 我能說出三個在 LibAFL 中不需要動其他元件就能替換的場景

---

## 延伸閱讀

1. **LibAFL: A Library to Build Modular and Reusable Fuzzers** — Fioraldi 等人，CCS 2022。
   這是 LibAFL 的設計論文，第 3 節詳細說明元件抽象的形式定義，第 5 節有各元件和 AFL++ 對應關係的實驗資料。讀完後你會理解為什麼「可組合」不只是工程方便性，而是讓研究結果可重現的技術前提。各元件的 trait 定義來自這篇論文的形式化描述。
   https://dl.acm.org/doi/10.1145/3548606.3560602

2. **LibAFL Book** — 官方文件。
   第 2 章「Core Concepts」和本章內容對應，但提供更多 API 細節和真實範例。重點看「Feedbacks and Objectives」一節，它對 `is_interesting()` 的生命週期有比本章更詳細的說明。Chapter 4「Executors」解釋 in-process 和 forkserver 的取捨，補本章 Executor 一節的深度。
   https://aflplus.plus/libafl-book/

3. **baby_fuzzer 原始碼** — LibAFL 官方 GitHub。
   本章所有 API 範例都對應到這份 ~150 行的程式碼。把它和本章的資料流圖對照著讀：每個 `let mut xxx = ...` 對應圖上的一個元件，`StdFuzzer::new` 的參數順序對應資料流的方向。這份程式碼是理解整個 LibAFL 架構的最小完整範例，下一章的動手實作就從這裡出發。
   https://github.com/AFLplusplus/LibAFL/tree/main/fuzzers/baby_fuzzer

---

下一章動手實作：從 `cargo new` 到第一個能找到 crash 的 in-process fuzzer，把本章的每個元件逐一接上去，在 `cargo build` 的型別錯誤裡驗證你對接線規則的理解。

→ [Ch 5 — 動手實作：baby_fuzzer 從零到找到第一個 crash](./05-libafl-first-fuzzer.md)
