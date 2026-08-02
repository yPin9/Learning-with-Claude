# Ch 6: Observer 與 Feedback — 決定哪些 Input 值得留下

> **目標**: 深挖 LibAFL 的 Observer/Feedback/Objective 三層架構。理解每一層的職責邊界、關鍵型別、組合宏的語義，以及為什麼這個設計可以讓你把 AFL 風格的 coverage feedback 換成任何你想得到的判斷邏輯。

---

## 為什麼需要這個

fuzzer 跑 input，目標程式執行。問題來了：這個 input 值不值得保留？

AFL 的答案是：看 coverage map。如果這次執行碰到了從沒碰過的 edge，就把 input 塞進 corpus。這直覺簡單，但實作上必須分三個問題回答：

1. **執行期間發生了什麼**（誰去收集 coverage map？）
2. **這次執行夠不夠「有趣」**（誰拿著 map 做判斷？）
3. **這次執行有沒有找到 bug**（誰決定要存進 crash dir？）

LibAFL 把這三個問題切成三層：Observer、Feedback、Objective。每層有清楚的職責、明確的介面、各自獨立的型別。

搞懂這三層，你才能把「換 feedback 邏輯」這件本來需要 fork AFL 原始碼的工作，變成寫幾十行 Rust。

---

## 先建立直覺

整體資料流長這樣：

```
                     ┌──────────────────────────────────────────┐
                     │             Fuzzer 主迴圈                │
                     └──────────────────┬───────────────────────┘
                                        │  generate/mutate input
                                        ▼
                     ┌──────────────────────────────────────────┐
                     │              Executor                    │
                     │  1. 呼叫 observer.pre_exec()             │
                     │  2. 執行目標 (harness / fork / QEMU)    │
                     │  3. 呼叫 observer.post_exec()            │
                     └──────────────────┬───────────────────────┘
                                        │  observers 現在有原始資料
                                        ▼
                     ┌──────────────────────────────────────────┐
                     │       Feedback::is_interesting()         │◄── 讀 observers
                     │  問：這個 input 要放進 corpus 嗎？       │
                     └──────────────────┬───────────────────────┘
                                        │  true → 存 corpus entry + metadata
                                        ▼
                     ┌──────────────────────────────────────────┐
                     │       Objective::is_interesting()        │◄── 讀 observers
                     │  問：這個 input 是 solution (bug) 嗎？  │
                     └──────────────────┬───────────────────────┘
                                        │  true → 存 objective corpus (crash dir)
                                        ▼
                                   下一輪迭代
```

關鍵點：
- Observer **只記錄事實**，不判斷
- Feedback **只判斷「有趣」**，不管 crash
- Objective **只判斷「解答」**，介面和 Feedback 相同但語義不同

---

## Part 1: Observer 深挖

### 1.1 Observer 的職責

Observer 是資料收集器。它的工作只有兩件：

- `pre_exec()`：執行前重置狀態（最常見的是把 map 清零）
- `post_exec()`：執行後把結果快照起來備用

Observer 不分析、不判斷、不寫 corpus。它就是眼睛。

LibAFL 的 `Observer<I, S>` trait 大概長這樣：

```rust
pub trait Observer<I, S>: Named {
    fn pre_exec(&mut self, _state: &mut S, _input: &I) -> Result<(), Error> {
        Ok(())
    }
    fn post_exec(
        &mut self,
        _state: &mut S,
        _input: &I,
        _exit_kind: &ExitKind,
    ) -> Result<(), Error> {
        Ok(())
    }
}
```

`Named` 是必要的，因為 Feedback 要用名字找到對應的 Observer。

### 1.2 Observer 的生命週期

```
    before harness run         harness runs         after harness run
          │                        │                       │
   pre_exec() 被呼叫          [target code]         post_exec() 被呼叫
          │                     在這裡跑                    │
          ▼                         │                       ▼
  ┌───────────────┐                 │              ┌─────────────────┐
  │ map 清零      │                 ▼              │ map 現在有資料  │
  │ [0,0,0,...,0] │    instrumentation 把          │ [0,1,0,3,1,...] │
  └───────────────┘    hit count 寫入 SHM 或       └─────────────────┘
                       signals[] array                      │
                                                            │
                                                Feedback 在這裡讀
```

### 1.3 關鍵 Observer 型別

**`ConstMapObserver<N>`**

包裝一個固定大小的 `[u8; N]` 陣列。baby_fuzzer 範例用的就是這個，因為 `SIGNALS` 是編譯期已知大小：

```rust
// Ch5 baby_fuzzer 的做法
static mut SIGNALS: [u8; 16] = [0; 16];
static mut SIGNALS_PTR: *mut u8 = unsafe { SIGNALS.as_mut_ptr() };

let observer = unsafe {
    ConstMapObserver::<_, 16>::from_mut_ptr(
        "signals",
        SIGNALS_PTR,
    )
};
```

`pre_exec` 會把整個 array 填零。`post_exec` 不做特別的事——map 已經在 harness 執行期間被 instrumentation 填好了。

**`StdMapObserver`**

包裝執行期才知道大小的 `&mut [u8]`。afl-cc instrumented binary 透過 shared memory 暴露 coverage map，map 大小是 `AFL_MAP_SIZE`（預設 65536），執行期才能確定。

```rust
// forkserver 情境
const MAP_SIZE: usize = 65536;
let mut shmem = shmem_provider.new_shmem(MAP_SIZE).unwrap();
let shmem_buf = shmem.as_slice_mut();

let edges_observer = unsafe {
    StdMapObserver::new("shared_mem", shmem_buf)
};
```

**`HitcountsMapObserver<O>`**

包裝另一個 map observer，在 `post_exec` 之後把每個 byte 轉換成 AFL hitcount 桶：

```
原始 hit count → 桶值
0              → 0    (沒跑到)
1              → 1
2              → 2
3              → 4
4-7            → 8
8-15           → 16
16-31          → 32
32-127         → 64
128+           → 128
```

為什麼要做這個轉換？因為「一個 edge 跑了 3 次」和「跑了 4 次」在語義上差不多，但「跑了 1 次」和「從沒跑過」的差異是本質的。Bucketing 把連續數值壓到 8 個桶，讓 `MaxMapFeedback` 只在真正有意義的時候才標記 interesting。

```rust
let edges_observer = HitcountsMapObserver::new(
    StdMapObserver::new("shared_mem", shmem_buf)
);
```

**`TimeObserver`**

記錄執行時間：

```rust
let time_observer = TimeObserver::new("time");
```

`pre_exec` 記下開始時間，`post_exec` 算出 duration。Feedback 層（`TimeFeedback`）再拿這個數字做判斷。

### 1.4 CanTrack trait

LibAFL 0.15.x 引入了 `CanTrack`。某些 Scheduler（例如 `IndexesLenTimeMinimizerScheduler`，用於 corpus minimization）需要 Observer 宣告它能追蹤什麼資訊：index 集合、novelties……等。

`StdMapObserver` 本身不一定實作了完整的 `CanTrack` bounds；`HitcountsMapObserver` 在包裝後會加上這些實作。如果你直接把 `StdMapObserver` 接 minimizer scheduler，編譯器會抱怨 trait bound 不滿足。解法是永遠用 `HitcountsMapObserver` 包一層——這也是 LibAFL 官方範例的標準做法。

---

## Part 2: Feedback 深挖

### 2.1 Feedback 的職責

Feedback 讀 Observer 的資料，回傳一個布林：這個 input 值得進 corpus 嗎？

關鍵方法：

```
is_interesting(state, manager, input, observers, exit_kind)
    → Result<bool, Error>
```

true = 塞進 evolution corpus，用來未來繼續 mutate。
false = 丟掉這個 input。

Feedback 也可以呼叫 `append_metadata()` 把額外資訊附在 corpus entry 上（例如哪些 edge 是新的），讓後續的 scheduler 做 minimization 時有資料可用。

**Feedback 不管 crash。** 它只管「有趣」。

### 2.2 關鍵 Feedback 型別

**`MaxMapFeedback`**

AFL 的核心 feedback。維護一個「歷史最大值 map」，每次執行後逐 byte 比較：

```
for i in 0..map_size:
    if coverage[i] > history[i]:
        history[i] = coverage[i]
        → 標記 interesting
```

只要 map 裡任何一個位置創了新高，就是 interesting。配合 `HitcountsMapObserver`，這就是 AFL 的 coverage-guided feedback 完整實作。

```rust
let mut feedback = MaxMapFeedback::new(&edges_observer);
```

**`TimeFeedback`**

把執行時間和歷史紀錄比較。如果這次明顯比正常慢（內部有移動平均），標記 interesting——慢可能代表走了新的程式路徑或遇到了特殊狀態。

```rust
let mut time_feedback = TimeFeedback::new(&time_observer);
```

**`ConstFeedback<true>` / `ConstFeedback<false>`**

除錯用。`ConstFeedback::<true>::new()` 永遠回傳 interesting，可以讓你先測試 fuzzer 基礎設施是否正常運作，不受 coverage feedback 的邏輯干擾。`ConstFeedback::<false>::new()` 永遠回傳 false，corpus 永遠不增長，用來確認「即使不保留 input，executor 也跑得起來」。

### 2.3 為什麼 Feedback 不直接持有 map，而是透過 Observer 名字查找？

Feedback 只持有 Observer 的**名字**（字串），在 `is_interesting()` 呼叫時透過 `observers.get_by_name()` 動態查找。這讓 observer 的所有權可以在 executor 那邊管理，而 feedback 只在需要的時候借用資料。

好處：Observer 和 Feedback 的生命週期解耦。你可以在不同地方建立它們，只要在最終組裝 fuzzer 時一起傳進去。

---

## Part 3: Objective 深挖

Objective 和 Feedback 使用完全相同的 trait 介面，但語義不同：

| | Feedback | Objective |
|---|---|---|
| 問題 | 這個 input 有趣嗎？ | 這個 input 是 solution？ |
| true 時的行動 | 存進 evolution corpus | 存進 objective corpus（crash dir）|
| 目的 | 讓 mutation 有方向 | 記錄找到的 bug |
| 典型實作 | MaxMapFeedback | CrashFeedback |

**`CrashFeedback`**

在 `is_interesting()` 裡看 `exit_kind`：如果是 `ExitKind::Crash`，回傳 true。

```rust
let mut objective = CrashFeedback::new();
```

**`TimeoutFeedback`**

如果 `exit_kind` 是 `ExitKind::Timeout`，回傳 true。

這個分離設計的好處：你可以把 objective 換成任何條件。想找特定錯誤碼？想找記憶體使用量超標的輸入？自己實作 objective，不需要動其他地方的程式碼。

---

## Part 4: Combinator 宏

LibAFL 提供四個宏把 Feedback 組合起來。

### 4.1 四個宏的語義

```rust
feedback_or!(a, b)
// 有趣 if a 有趣 OR b 有趣
// 兩個都會被呼叫，兩個都有機會 append_metadata

feedback_and!(a, b)
// 有趣 if a 有趣 AND b 有趣
// 兩個都會被呼叫

feedback_or_fast!(a, b)
// 有趣 if a 有趣 OR b 有趣
// 如果 a 已經有趣，b 的 is_interesting() 不會被呼叫
// → b 的 metadata 不會被 append

feedback_and_fast!(a, b)
// 有趣 if a 有趣 AND b 有趣
// 如果 a 不有趣，b 的 is_interesting() 不會被呼叫
```

### 4.2 實際組合範例

來自 LibAFL 的 `forkserver_simple` 範例：

```rust
// feedback: 新 coverage 或 明顯慢的執行
let mut feedback = feedback_or!(
    MaxMapFeedback::new(&edges_observer),
    TimeFeedback::new(&time_observer)
);

// objective: crash 而且帶來新 coverage
// 這讓「走到新路徑的 crash」比「重複路徑的 crash」優先
let mut objective = feedback_and_fast!(
    CrashFeedback::new(),
    MaxMapFeedback::new(&edges_observer)
);
```

`objective` 這個組合的邏輯：只有在 crash 同時帶來新 coverage 時才存進 objective corpus。這不是「忽略普通 crash」——`CrashFeedback` 仍然是第一個被評估的，crash 是必要條件。`feedback_and_fast!` 在這裡的意義是：在同時追求 unique crash 的情境下，只存那些探索了新程式路徑的 crash，避免 objective corpus 被大量重複 crash 淹沒。

### 4.3 組合流程圖

```
feedback_or!(MaxMapFeedback, TimeFeedback)

input 執行完畢
       │
       ├──────────────────────────────────┐
       ▼                                  ▼
MaxMapFeedback::is_interesting()    TimeFeedback::is_interesting()
  → true / false                      → true / false
       │                                  │
       └──────────────┬───────────────────┘
                      ▼
              true OR true  → interesting
              true OR false → interesting
              false OR true → interesting
              false OR false → not interesting


feedback_and_fast!(CrashFeedback, MaxMapFeedback)

input 執行完畢
       │
       ▼
CrashFeedback::is_interesting()
       │
   ┌───┴───────────────┐
 false                true
   │                    │
   ▼                    ▼
  丟掉         MaxMapFeedback::is_interesting()
                        │
                ┌───────┴───────────┐
              false                true
                │                    │
                ▼                    ▼
               丟掉          存 objective corpus
```

---

## Part 5: 自訂 Feedback 實作

### 5.1 情境

假設你的目標程式把某個內部計數器的值寫進 `SIGNALS[5]`。你想要：當 `SIGNALS[5]` 超過 100 時，把這個 input 標記為 interesting，無論整體 coverage 有沒有增加。

這不是標準的 "maximize any byte" 邏輯，需要自訂。

### 5.2 實作概念

**本段未實測，為理論預期行為。LibAFL 0.15.x 的 Feedback trait 簽名在 minor 版本間有調整，以下程式碼展示設計概念，實際編譯前請對照 [docs.rs/libafl](https://docs.rs/libafl) 確認確切泛型參數和 trait bound。**

驗證步驟：
1. `cargo add libafl --version "0.15"` 後執行 `cargo doc --open`
2. 搜尋 `Feedback` trait，確認 `is_interesting` 的完整簽名
3. 確認 `append_metadata` 和 `discard_metadata` 的參數列表

```rust
use libafl::{
    feedbacks::Feedback,
    observers::ObserversTuple,
    Error,
    executors::ExitKind,
    inputs::Input,
    corpus::Testcase,
};
use libafl_bolts::Named;

pub struct ThresholdFeedback {
    observer_name: String,
    threshold: u8,
    position: usize,
}

impl ThresholdFeedback {
    pub fn new(observer_name: &str, threshold: u8, position: usize) -> Self {
        Self {
            observer_name: observer_name.to_owned(),
            threshold,
            position,
        }
    }
}

impl Named for ThresholdFeedback {
    fn name(&self) -> &str {
        "ThresholdFeedback"
    }
}

// 概念性實作，實際泛型參數請查 docs.rs
impl<I, S> Feedback<I, S> for ThresholdFeedback
where
    I: Input,
{
    fn is_interesting<EM, OT>(
        &mut self,
        _state: &mut S,
        _manager: &mut EM,
        _input: &I,
        observers: &OT,
        _exit_kind: &ExitKind,
    ) -> Result<bool, Error>
    where
        OT: ObserversTuple<I, S>,
    {
        // 透過名字找到對應的 ConstMapObserver
        // 型別參數要和實際使用的 observer 型別一致
        let observer = observers
            .get_by_name::<ConstMapObserver<u8, 16>>(&self.observer_name)
            .ok_or_else(|| Error::unknown("observer not found"))?;

        let map = observer.map();
        if self.position < map.len() {
            Ok(map[self.position] > self.threshold)
        } else {
            Ok(false)
        }
    }

    fn append_metadata<OT>(
        &mut self,
        _state: &mut S,
        _observers: &OT,
        _testcase: &mut Testcase<I>,
    ) -> Result<(), Error>
    where
        OT: ObserversTuple<I, S>,
    {
        // 可以把 signals[position] 的值附到 corpus entry 上
        // 讓後續分析知道是因為什麼條件被保留的
        Ok(())
    }

    fn discard_metadata(
        &mut self,
        _state: &mut S,
        _input: &I,
    ) -> Result<(), Error> {
        // is_interesting() 回傳 false 時呼叫，清理暫存狀態
        // 這個 feedback 沒有暫存狀態，直接 Ok
        Ok(())
    }
}
```

實際使用：

```rust
// 假設 SIGNALS 由名叫 "signals" 的 ConstMapObserver 包裝
let threshold_feedback = ThresholdFeedback::new("signals", 100, 5);

// 和 MaxMapFeedback 組合：coverage 增加 OR 特定位置超門檻
let mut feedback = feedback_or!(
    MaxMapFeedback::new(&observer),
    threshold_feedback,
);
```

---

## Part 6: Observer-Feedback 關係圖與所有權

這是 LibAFL 架構最容易搞混的地方：構建順序為什麼不能亂？

```
構建階段順序：

Step 1. 建立 observer
   ┌─────────────────────────────────────┐
   │ let edges_observer = Hitcounts...;  │
   └───────────────────┬─────────────────┘
                       │
                       │  此時 edges_observer 在 stack 上
                       │
Step 2. 建立 feedback，傳入 &edges_observer
   ┌────────────────────────────────────────────┐
   │ let feedback = MaxMapFeedback::new(        │
   │     &edges_observer   // 借用              │
   │ );                                         │
   │ // feedback 記下 observer 的名字字串       │
   │ // 借用在這裡結束                          │
   └───────────────────┬────────────────────────┘
                       │
                       │  feedback 已不再借用 edges_observer
                       │
Step 3. observer MOVE 進 executor
   ┌────────────────────────────────────────────┐
   │ let executor = InProcessExecutor::new(     │
   │     &mut harness,                          │
   │     tuple_list!(edges_observer),  // move  │
   │     ...                                    │
   │ );                                         │
   │ // edges_observer 現在歸 executor 管       │
   └───────────────────┬────────────────────────┘
                       │
Step 4. 執行迴圈
   executor.pre_exec()  → observer.pre_exec() (清零 map)
   harness 跑           → 填 map
   executor.post_exec() → observer.post_exec() (快照)
   feedback.is_interesting(observers) → get_by_name("...") → 讀 map
```

所有權流向總結：

```
edges_observer 建立
    │
    ├── feedback 借用 → 只記下名字字串 → 借用結束
    │
    └── executor 持有 (move)
              │
              └── 每次執行後 feedback 用名字
                  透過 observers.get_by_name()
                  取得 immutable ref 讀 map
```

為什麼不能先建 executor 再建 feedback？因為你需要 `&edges_observer` 來初始化 feedback（讓它知道要找哪個 observer）。observer move 進 executor 之後，你手上就沒有 `&edges_observer` 了。Rust 的所有權系統在編譯期就會擋住這個錯誤。

---

## 底層機制：MaxMapFeedback 的歷史 map 維護

```
MaxMapFeedback 內部狀態：

  ┌─────────────────────────────────────────────────┐
  │  history_map: Vec<u8>  (全程累積的最大值)       │
  │  初始: [0, 0, 0, 0, 0, 0, ...]                 │
  └─────────────────────────────────────────────────┘

第 1 次執行，coverage map = [0,1,0,0,2,0,...]

  is_interesting():
    pos 1: 1 > 0 → history[1] = 1 → found_new = true
    pos 4: 2 > 0 → history[4] = 2 → found_new = true

  history_map 變成: [0,1,0,0,2,0,...]
  → interesting = true，input 進 corpus

第 2 次執行，coverage map = [0,1,0,0,2,0,...]  (完全一樣)

  is_interesting():
    沒有任何位置超過歷史最大值
  → interesting = false，input 丟掉

第 3 次執行，coverage map = [0,1,0,0,3,0,...]  (pos 4 從 bucket 2 升到 bucket 4)

  is_interesting():
    pos 4: 3 > 2 → history[4] = 3 → found_new = true

  history_map 變成: [0,1,0,0,3,0,...]
  → interesting = true，input 進 corpus
```

配合 `HitcountsMapObserver` 的 bucket 轉換，這裡的「3」實際上代表「原始 hitcount 3，落在 bucket 4」。MaxMapFeedback 看到的永遠是 bucket 值，不是原始計數。

---

## Pitfall 集合

### Pitfall 1：pre_exec 沒清零，coverage 跨 run 累積

如果你自己實作 Observer 但忘了在 `pre_exec` 清零 map：

```
第 1 次跑：path A → map[0] = 1, map[1] = 0
第 2 次跑：path B → map[0] 沒清，繼續累積 → map[0] = 2, map[1] = 1
```

MaxMapFeedback 看到 `map[0]` 從 1 升到 2，認為「這是新 hitcount bucket」，但那是舊 run 的殘留。你會得到大量假陽性，corpus 暴增，fuzzing throughput 崩潰。

修法：永遠在 `pre_exec` 呼叫 `map.fill(0)`。LibAFL 內建的 observer 都有做，問題只出在自訂 observer 上。

### Pitfall 2：把 `MapFeedback` 當型別用

`MapFeedback` 是 trait，`MaxMapFeedback` 才是你要的具體型別。常見錯誤：

```rust
// 錯誤：MapFeedback 是 trait，不能直接當型別
let feedback: MapFeedback = MaxMapFeedback::new(&observer);

// 正確
let feedback = MaxMapFeedback::new(&observer);

// 或明確標型別（泛型參數複雜，通常讓編譯器推導）
let feedback: MaxMapFeedback<_, _, _> = MaxMapFeedback::new(&observer);
```

這個錯誤的錯誤訊息通常是「expected type, found trait」，第一次看到時不太直覺。

### Pitfall 3：直接用 StdMapObserver 接需要 index tracking 的 Scheduler

```rust
// 想用 corpus minimizer
let scheduler = IndexesLenTimeMinimizerScheduler::new(
    QueueScheduler::new()
);

// 直接用 StdMapObserver → 編譯失敗
let observer = StdMapObserver::new("shared_mem", shmem_buf);
// 錯誤：trait bound `StdMapObserver<...>: CanTrack` not satisfied

// 正確：用 HitcountsMapObserver 包裝，它提供必要的 CanTrack impl
let observer = HitcountsMapObserver::new(
    StdMapObserver::new("shared_mem", shmem_buf)
);
```

`HitcountsMapObserver` 不只是做 bucket 轉換，它也是正確 `CanTrack` impl 的提供者。把它視為「接 scheduler 的標準介面層」。

### Pitfall 4：`feedback_or_fast!` 的短路副作用

```rust
let mut feedback = feedback_or_fast!(
    coverage_feedback,   // 如果這個 true
    metadata_feedback,   // 這個的 is_interesting() 不會被呼叫
);
```

如果 `coverage_feedback::is_interesting()` 回傳 true，`metadata_feedback::is_interesting()` 根本不執行。這意味著：

- `metadata_feedback` 的 `append_metadata()` 不會被呼叫，corpus entry 少了它應該附上的資訊
- 如果 `metadata_feedback` 有內部狀態需要重置，`discard_metadata()` 也不會被呼叫，可能累積錯誤狀態

如果你的第二個 feedback 有副作用（例如更新移動平均值、遞增計數器），`feedback_or_fast!` 的短路會讓這些統計資料偏差。

修法：只在確定第二個 feedback 是純查詢（無副作用）時才用 `_fast` 版本。否則用非短路的 `feedback_or!`，代價是多一次函數呼叫。

---

## 進階延伸

### 多層 Observer 架構

你可以同時掛多個 Observer，只要把它們都放進 tuple 傳給 executor：

```rust
let executor = InProcessExecutor::new(
    &mut harness,
    tuple_list!(
        edges_observer,   // coverage map
        time_observer,    // 執行時間
        // 自訂 observer 也可以加這裡
    ),
    &mut fuzzer, &mut state, &mut mgr,
)?;
```

Feedback 透過名字找 Observer，所以不同 Feedback 可以讀不同的 Observer，互不干擾。

### Differential Feedback

把兩個 target 的輸出都記在 Observer 裡，Feedback 比較兩者差異。這是差異測試（differential fuzzing）的基礎：

```
Observer A → 記錄 target_a 的輸出 (stdout/return value/memory state)
Observer B → 記錄 target_b 的輸出
DiffFeedback → 比較 A 和 B，不一樣就 interesting
```

LibAFL 有 `DifferentialFeedback` 提供這個模式，讓你在測試兩個功能等價但實作不同的程式時（例如 openssl vs mbedtls 的同一個 API），只要找到輸出不同的 input 就算 interesting。

### Sanity Check：用 ConstFeedback 驗基礎設施

在把真正的 feedback 接上去之前，先用 `ConstFeedback::<true>::new()` 跑幾輪。如果 corpus 沒有成長、執行次數沒有增加，問題出在 executor 或 scheduler，不是 feedback。這個技術可以把除錯範圍從整個 fuzzer 縮小到一個模組。

---

## 動手練習

**目標**: 在 Ch 5 的 baby_fuzzer 基礎上，加上 `TimeFeedback`，觀察 corpus 增長模式的變化。

**步驟**:

1. 在 `Cargo.toml` 確認 libafl 版本是 0.15.x。

2. 在 baby_fuzzer 的 `main.rs` 加入 `TimeObserver` 和 `TimeFeedback`：

```rust
use libafl::observers::TimeObserver;
use libafl::feedbacks::TimeFeedback;

let time_observer = TimeObserver::new("time");

// 把 time_observer 也加進 executor 的 observer tuple
let executor = InProcessExecutor::new(
    &mut harness,
    tuple_list!(signals_observer, time_observer),
    &mut fuzzer, &mut state, &mut mgr,
)?;

// feedback 改成 or 組合
let mut feedback = feedback_or!(
    MaxMapFeedback::new(&signals_observer),
    TimeFeedback::new(&time_observer),
);
```

3. 跑 1000 輪，記錄 corpus 大小。和只有 `MaxMapFeedback` 的版本比較。

4. 修改 harness，讓某些特定 byte pattern 的輸入執行 `std::thread::sleep(Duration::from_millis(10))`。確認 `TimeFeedback` 把這些 input 標記為 interesting，即使 coverage map 沒有新增。

**進階**: 實作 `ThresholdFeedback`，讓 baby_fuzzer 在 `SIGNALS[5]` 超過某個值時也保留 input。加進 `feedback_or!` 組合，觀察 corpus 的組成。

---

## 章節總結

- Observer 負責收集原始執行資料（map、時間），只記錄、不判斷
- Feedback 讀 Observer，決定 input 是否進 evolution corpus；`is_interesting()` 是核心方法
- Objective 介面和 Feedback 相同，但決定是否存為 solution（crash），兩者語義截然不同
- `MaxMapFeedback` 是 AFL coverage feedback 的直接實作，維護歷史最大值 map
- `HitcountsMapObserver` 把原始 hitcount 轉換成 AFL bucket，是接 MaxMapFeedback 和 minimizer scheduler 的標準前置層
- `feedback_or!` / `feedback_and!` 組合 feedback；`_fast` 版本短路，有副作用的 feedback 不能用短路版
- 建構順序：observer → feedback（傳入 &observer）→ executor（move observer）
- 自訂 Feedback 只需實作 `is_interesting` + `append_metadata` + `discard_metadata` 三個方法

---

## 自我檢核

- [ ] 能說出 Observer、Feedback、Objective 的職責差異，不混淆
- [ ] 知道為什麼 `pre_exec` 必須清零 map，不清零會有什麼後果
- [ ] 能解釋 HitcountsMapObserver 的 bucket 邏輯和它存在的兩個理由（bucket 轉換 + CanTrack）
- [ ] 知道 `feedback_or!` 和 `feedback_or_fast!` 的行為差異，以及何時不能用短路版
- [ ] 理解 observer 先建、feedback 傳 &observer、再 move 進 executor 的順序約束及原因
- [ ] 能說出 `MaxMapFeedback` 的 `history_map` 是如何更新的

---

## 延伸閱讀

1. **LibAFL 原始碼 `libafl/src/feedbacks/map.rs`**
   `MaxMapFeedback` 的完整實作，包含 history map 的初始化和逐 byte 比較邏輯。看懂這段，你對 AFL coverage feedback 的理解就是 source level，不再是黑盒子。
   [https://github.com/AFLplusplus/LibAFL/blob/main/libafl/src/feedbacks/map.rs](https://github.com/AFLplusplus/LibAFL/blob/main/libafl/src/feedbacks/map.rs)

2. **AFL++ technical paper (2021) — Fioraldi et al.**
   第 3 節解釋 hitcount bucketing 的設計決策，以及為什麼原始 AFL 的 8 個 bucket 在實踐中夠用——這是理解 `HitcountsMapObserver` 為什麼這樣設計的一手資料。
   [https://www.usenix.org/conference/woot20/presentation/fioraldi](https://www.usenix.org/conference/woot20/presentation/fioraldi)

3. **"Coverage-based Greybox Fuzzing as Markov Chain" — Böhme et al. (CCS 2016)**
   把 fuzzer 建模成 Markov chain，從理論上說明為什麼最大化 coverage 是好的 selection criterion，而不只是直覺。這是理解 Feedback 設計「為什麼有效」的必讀論文，也是整個 coverage-guided fuzzing 領域的理論地基。
   [https://dl.acm.org/doi/10.1145/2976749.2978428](https://dl.acm.org/doi/10.1145/2976749.2978428)

---

Observer 和 Feedback 決定哪些 input 值得留、哪些要丟。下一章看 Executor——fuzzer 如何實際跑目標，以及從 in-process 到 forkserver 再到 QEMU 的速度/隔離取捨。

→ [下一章](./07-executor-family.md)
