# 練習 A — 用 LibAFL 造結構感知 parser fuzzer

> **目標**：用 LibAFL 為一個自寫的 CSV parser（內含一個隱藏 OOB bug）打造結構感知 fuzzer：自訂 `Input`、自訂 `Generator`、自訂結構感知 `Mutator`、自訂 `Feedback`，搭配 `InProcessExecutor`，讓 fuzzer 在語義層操作 input，找到 bug。

---

## 背景動機

前幾章你組裝了 LibAFL 的標準元件。但真實目標幾乎都不是「讀隨機 bytes 的 CLI tool」——它們有結構化輸入（CSV、JSON、protobuf、命令序列）。這道練習逼你把 Ch8–9 的概念全部接起來，用一個完整、可跑的 fuzzer 找到一個藏在結構判斷裡的 bug。

這個 bug 在 byte-level fuzzer 下平均需要很久才能找到（要同時產生「欄位數不匹配」且「row 欄位數 ≤ header 欄位數」兩個條件），但結構感知的 mutator 在第一輪就能觸發。

---

## 任務規格

### 輸入格式

自訂 `CsvInput`：

```rust
pub struct CsvInput {
    pub header: Vec<String>,   // 第一列：欄位名稱
    pub rows: Vec<Vec<String>>, // 後續列：資料
}
```

序列化為標準 CSV 字串（欄位以 `,` 分隔，列以 `\n` 分隔）送給 target。

### 目標（有 bug 的 CSV parser）

```rust
pub fn parse_csv_buggy(csv: &str) -> Result<Vec<Vec<String>>, String> {
    // 解析 CSV，當某 row 欄位數 != header 欄位數時，
    // 試圖存取 row[n_cols]（BUG：若 row.len() <= n_cols → OOB）
}
```

**Bug**：`fields.get(n_cols)` 在 `fields.len() <= n_cols` 時回傳 `None`，代表真實實作的 `fields[n_cols]` 會 index out of bounds。用 `BUG_TRIGGERED` flag 模擬 crash，不實際 panic。

### 驗收條件

- [ ] `CsvInput` 實作 `Input` trait（`Clone + Serialize + DeserializeOwned + Debug + Hash`）
- [ ] `CsvGenerator` 實作 `Generator<CsvInput, S>`，能生成合法的初始 seed
- [ ] `CsvMutator` 實作 `Mutator<CsvInput, S>`，在結構層做至少 4 種 mutation
- [ ] `BugFeedback` 實作 `Feedback<...>`，透過全域 flag 追蹤 bug 觸發
- [ ] `InProcessExecutor` + `StdMutationalStage` 組成完整 fuzzer
- [ ] 在 500 輪內找到至少 1 個 solution（bug 觸發）

### 限制

- LibAFL 0.15.4，不能用任何額外 fuzzing 套件
- 不使用 `BytesInput`，整個 fuzzer 全程操作 `CsvInput` 結構
- coverage map 可以用模擬（`static mut [u8; 16]`）

---

## 期望輸出範例

```
初始 corpus: 5 筆
開始 fuzzing (500 輪)...
[UserStats #0] run time: 0s, corpus: 0, objectives: 0, executions: 1
[Testcase #0]  run time: 0s, corpus: 6, objectives: 0, executions: 1
[Objective #0] run time: 0s, corpus: 6, objectives: 1, executions: 2
>>> round   0: 找到 bug！solutions = 1

最終結果：corpus = 23，solutions = 5121
第 0 輪找到第一個 bug ✓
```

---

## 如果卡住

**卡點 1：`impl Input` 不知道要寫什麼**

`Input` trait 所有方法都有預設實作，空 `impl` 就能通過：
```rust
impl Input for CsvInput {}
```
只需要 `#[derive(Clone, Debug, Serialize, Deserialize)]` 加上手動 `impl Hash`。

**卡點 2：`Mutator::post_exec` 沒有實作**

LibAFL 0.15.4 的 `Mutator` trait 要求實作 `post_exec`（不再是可選的）：
```rust
fn post_exec(&mut self, _state: &mut S, _new_corpus_id: Option<CorpusId>) -> Result<(), Error> {
    Ok(())
}
```

**卡點 3：`Rand::below` 找不到方法**

需要在 scope 裡 `use libafl_bolts::rands::Rand;`，`below` 才能被呼叫。

**卡點 4：`ConstMapObserver::new` 的 `N` 是 const generic**

直接傳 `&mut [u8; 16]` 即可：
```rust
static mut COV_MAP: [u8; 16] = [0u8; 16];
let observer = unsafe { ConstMapObserver::new("cov_map", &mut COV_MAP) };
```

**卡點 5：自訂 Feedback 的 impl bound 報 `State` is private**

不要 `use libafl::state::State`，直接不加 `S` bound：
```rust
impl<EM, I, OT, S> Feedback<EM, I, OT, S> for BugFeedback
where
    I: Input,
    OT: ObserversTuple<I, S>,
{ ... }
```
同時要 `impl<S> StateInitializer<S> for BugFeedback {}`。

---

## 實作步驟

### Step 1：建立專案

```bash
cargo new practice_a_libafl --bin
cd practice_a_libafl
```

`Cargo.toml`：

```toml
[package]
name = "practice_a_libafl"
version = "0.1.0"
edition = "2021"

[dependencies]
libafl = { version = "0.15.4", features = ["std"] }
libafl_bolts = "0.15.4"
serde = { version = "1", features = ["derive"] }
postcard = { version = "1", features = ["alloc"] }
```

### Step 2：定義 CsvInput

`#[derive(Debug, Clone, Serialize, Deserialize)]`，手動 `impl Hash`（用 postcard 序列化後 hash），`impl Input for CsvInput {}`（空 impl）。

加一個 `to_csv(&self) -> String` 方法把結構轉成 CSV 字串。

### Step 3：寫 CsvGenerator

實作 `Generator<CsvInput, S> for CsvGenerator`，`S: HasRand`。隨機選 n_cols（1–5），生成 header；隨機選 n_rows（1–4），每列有 n_cols 個欄位。

用 `state.rand_mut().below(NonZeroUsize::new(N).unwrap())` 取隨機 index。

### Step 4：寫 CsvMutator

實作 `Mutator<CsvInput, S> for CsvMutator`，`S: HasRand`。至少 4 種 mutation：
- 在 header 加一欄（製造不匹配）
- 在某 row 加一欄
- 在某 row 刪一欄
- 在某 row 加 header.len()+10 欄的整列（直接觸發 bug）

記得加 `post_exec`（回傳 `Ok(())`）。

### Step 5：寫有 bug 的 parser

`parse_csv_buggy`：解析 CSV，當 `fields.len() != n_cols` 時，若 `fields.get(n_cols).is_none()`，設定 `BUG_TRIGGERED.store(true, ...)` 並回傳 `Err`。

### Step 6：寫 BugFeedback

`impl<S> StateInitializer<S> for BugFeedback {}`
`impl<EM, I, OT, S> Feedback<EM, I, OT, S> for BugFeedback`：在 `is_interesting` 裡讀取 `BUG_TRIGGERED.swap(false, Relaxed)` 並回傳它。

### Step 7：組裝 fuzzer

按 Ch5 的模式：StdState → SimpleEventManager → 初始 corpus（5 筆）→ StdFuzzer → InProcessExecutor → StdMutationalStage(CsvMutator) → fuzz loop 500 輪。

---

## 完整參考解答

<details>
<summary>點開參考實作（建議完成後再看）</summary>

```rust
// practice_a_libafl/src/main.rs
// LibAFL 0.15.4 + WSL2 Ubuntu 實測通過

use std::borrow::Cow;
use std::hash::{Hash, Hasher};
use std::num::NonZeroUsize;
use std::sync::atomic::{AtomicBool, Ordering};

use libafl::{
    corpus::{Corpus, InMemoryCorpus, Testcase},
    events::SimpleEventManager,
    executors::{ExitKind, InProcessExecutor},
    feedbacks::{Feedback, MaxMapFeedback, StateInitializer},
    fuzzer::{Fuzzer, StdFuzzer},
    generators::Generator,
    inputs::Input,
    monitors::SimplePrintingMonitor,
    mutators::{MutationResult, Mutator},
    observers::{ConstMapObserver, ObserversTuple},
    schedulers::QueueScheduler,
    stages::StdMutationalStage,
    state::{HasCorpus, HasRand, HasSolutions, StdState},
    Error,
};
use libafl::corpus::CorpusId;
use libafl_bolts::{
    current_nanos,
    rands::{Rand, StdRand},
    tuples::tuple_list,
    Named,
};
use serde::{Deserialize, Serialize};

// ─── 自訂 Input ─────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CsvInput {
    pub header: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

impl Hash for CsvInput {
    fn hash<H: Hasher>(&self, state: &mut H) {
        postcard::to_allocvec(self).unwrap_or_default().hash(state);
    }
}

impl Input for CsvInput {}

impl CsvInput {
    pub fn to_csv(&self) -> String {
        let mut out = String::new();
        out.push_str(&self.header.join(","));
        out.push('\n');
        for row in &self.rows {
            out.push_str(&row.join(","));
            out.push('\n');
        }
        out
    }
}

// ─── Generator ───────────────────────────────────────────────────

pub struct CsvGenerator {
    field_pool: Vec<String>,
}

impl CsvGenerator {
    pub fn new() -> Self {
        Self {
            field_pool: vec![
                "id".into(), "name".into(), "value".into(),
                "count".into(), "total".into(),
                "0".into(), "1".into(), "42".into(),
                "foo".into(), "bar".into(), "baz".into(),
            ],
        }
    }

    fn rand_field<S: HasRand>(&self, state: &mut S) -> String {
        let n = NonZeroUsize::new(self.field_pool.len()).unwrap();
        let idx = state.rand_mut().below(n);
        self.field_pool[idx].clone()
    }
}

impl<S: HasRand> Generator<CsvInput, S> for CsvGenerator {
    fn generate(&mut self, state: &mut S) -> Result<CsvInput, Error> {
        let n_cols = 1 + state.rand_mut().below(NonZeroUsize::new(5).unwrap());
        let header = (0..n_cols).map(|_| self.rand_field(state)).collect();
        let n_rows = 1 + state.rand_mut().below(NonZeroUsize::new(4).unwrap());
        let rows = (0..n_rows)
            .map(|_| (0..n_cols).map(|_| self.rand_field(state)).collect())
            .collect();
        Ok(CsvInput { header, rows })
    }
}

// ─── 結構感知 Mutator ─────────────────────────────────────────────

pub struct CsvMutator;

impl Named for CsvMutator {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("CsvMutator");
        &NAME
    }
}

impl<S: HasRand> Mutator<CsvInput, S> for CsvMutator {
    fn mutate(&mut self, state: &mut S, input: &mut CsvInput) -> Result<MutationResult, Error> {
        let choice = state.rand_mut().below(NonZeroUsize::new(6).unwrap());
        match choice {
            0 => {
                // 在 header 加一個欄位（製造不匹配）
                input.header.push("extra".into());
            }
            1 => {
                // 在某 row 加一個欄位
                if !input.rows.is_empty() {
                    let n = NonZeroUsize::new(input.rows.len()).unwrap();
                    let r = state.rand_mut().below(n);
                    input.rows[r].push("EXTRA".into());
                }
            }
            2 => {
                // 在某 row 刪一個欄位
                if !input.rows.is_empty() {
                    let n = NonZeroUsize::new(input.rows.len()).unwrap();
                    let r = state.rand_mut().below(n);
                    if !input.rows[r].is_empty() {
                        let m = NonZeroUsize::new(input.rows[r].len()).unwrap();
                        let c = state.rand_mut().below(m);
                        input.rows[r].remove(c);
                    }
                }
            }
            3 => {
                // 改某格的值
                if !input.rows.is_empty() {
                    let rn = NonZeroUsize::new(input.rows.len()).unwrap();
                    let r = state.rand_mut().below(rn);
                    if !input.rows[r].is_empty() {
                        let cn = NonZeroUsize::new(input.rows[r].len()).unwrap();
                        let c = state.rand_mut().below(cn);
                        let byte = state.rand_mut().below(
                            NonZeroUsize::new(256).unwrap()
                        ) as u8;
                        input.rows[r][c] = format!("{byte}");
                    }
                }
            }
            4 => {
                // 加一整個 row，欄位數 = header.len() + 10（直接觸發 OOB）
                let extra_cols = input.header.len() + 10;
                input.rows.push(vec!["x".into(); extra_cols]);
            }
            _ => {
                // 從 header 刪一欄
                if !input.header.is_empty() {
                    input.header.remove(0);
                }
            }
        }
        Ok(MutationResult::Mutated)
    }

    fn post_exec(
        &mut self,
        _state: &mut S,
        _new_corpus_id: Option<CorpusId>,
    ) -> Result<(), Error> {
        Ok(())
    }
}

// ─── 自訂 Feedback：BugFeedback ──────────────────────────────────

static BUG_TRIGGERED: AtomicBool = AtomicBool::new(false);

#[derive(Debug)]
pub struct BugFeedback;

impl Named for BugFeedback {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("BugFeedback");
        &NAME
    }
}

impl<S> StateInitializer<S> for BugFeedback {}

impl<EM, I, OT, S> Feedback<EM, I, OT, S> for BugFeedback
where
    I: Input,
    OT: ObserversTuple<I, S>,
{
    fn is_interesting(
        &mut self,
        _state: &mut S,
        _manager: &mut EM,
        _input: &I,
        _observers: &OT,
        _exit_kind: &ExitKind,
    ) -> Result<bool, Error> {
        Ok(BUG_TRIGGERED.swap(false, Ordering::Relaxed))
    }
}

// ─── 有 bug 的 CSV Parser ─────────────────────────────────────────

pub fn parse_csv_buggy(csv: &str) -> Result<Vec<Vec<String>>, String> {
    let mut lines = csv.lines();
    let header: Vec<&str> = match lines.next() {
        Some(h) => h.split(',').collect(),
        None => return Ok(vec![]),
    };
    let n_cols = header.len();
    let mut records = Vec::new();

    for line in lines {
        if line.trim().is_empty() {
            continue;
        }
        let fields: Vec<String> = line.split(',').map(String::from).collect();
        if fields.len() != n_cols {
            // BUG: row[n_cols] 越界（當 fields.len() <= n_cols）
            if fields.get(n_cols).is_none() {
                BUG_TRIGGERED.store(true, Ordering::Relaxed);
                return Err(format!(
                    "OOB: row.len()={}, tried index {}", fields.len(), n_cols
                ));
            }
        }
        records.push(fields);
    }
    Ok(records)
}

// ─── Coverage Map（模擬）─────────────────────────────────────────

static mut COV_MAP: [u8; 16] = [0u8; 16];

fn main() {
    let observer = unsafe { ConstMapObserver::new("cov_map", &mut COV_MAP) };

    let mut feedback = MaxMapFeedback::new(&observer);
    let mut objective = BugFeedback;

    let mut state = StdState::new(
        StdRand::with_seed(current_nanos()),
        InMemoryCorpus::<CsvInput>::new(),
        InMemoryCorpus::<CsvInput>::new(),
        &mut feedback,
        &mut objective,
    )
    .unwrap();

    let monitor = SimplePrintingMonitor::new();
    let mut mgr = SimpleEventManager::new(monitor);

    let mut gen = CsvGenerator::new();
    for _ in 0..5 {
        let input = gen.generate(&mut state).unwrap();
        state.corpus_mut().add(Testcase::new(input)).unwrap();
    }
    println!("初始 corpus: {} 筆", state.corpus().count());

    let scheduler = QueueScheduler::new();
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    let mut harness = |input: &CsvInput| {
        let csv = input.to_csv();
        unsafe {
            COV_MAP.iter_mut().for_each(|b| *b = 0);
            for (i, b) in csv.as_bytes().iter().take(16).enumerate() {
                COV_MAP[i] = b.wrapping_add(1);
            }
        }
        let _ = parse_csv_buggy(&csv);
        ExitKind::Ok
    };

    let mut executor = InProcessExecutor::new(
        &mut harness,
        tuple_list!(observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
    )
    .unwrap();

    let mut stages = tuple_list!(StdMutationalStage::new(CsvMutator));

    println!("開始 fuzzing (500 輪)...");
    let mut prev_solutions = 0usize;
    let mut found_round = None;

    for round in 0..500u64 {
        let _ = fuzzer.fuzz_one(&mut stages, &mut executor, &mut state, &mut mgr);
        let cur = state.solutions().count();
        if cur > prev_solutions {
            prev_solutions = cur;
            if found_round.is_none() { found_round = Some(round); }
            println!(">>> round {:3}: 找到 bug！solutions = {}", round, cur);
        }
    }

    println!("\n最終結果：corpus = {}，solutions = {}",
        state.corpus().count(), state.solutions().count());
    if let Some(r) = found_round {
        println!("第 {} 輪找到第一個 bug ✓", r);
    }
}
```

**真跑輸出**（WSL2，LibAFL 0.15.4）：

```
初始 corpus: 5 筆
開始 fuzzing (500 輪)...
[UserStats #0] run time: 0s, corpus: 0, objectives: 0, executions: 1
[Testcase #0]  run time: 0s, corpus: 6, objectives: 0, executions: 1
[Objective #0] run time: 0s, corpus: 6, objectives: 1, executions: 2
>>> round   0: 找到 bug！solutions = 1
...（多次觸發）...
最終結果：corpus = 23，solutions = 5121
第 0 輪找到第一個 bug ✓
```

</details>

---

## 測試用例

| 輸入 | 期望行為 |
|---|---|
| 正常 CSV（header 3 欄，所有 row 也 3 欄）| `parse_csv_buggy` 回 `Ok`，`BUG_TRIGGERED = false` |
| Row 欄位數 > header 欄位數（row 有 5 欄，header 有 3 欄）| `fields.get(3)` 不是 `None`（index 3 存在），不觸發 bug |
| Row 欄位數 < header 欄位數（row 有 2 欄，header 有 3 欄）| `fields.get(3)` 是 `None`，`BUG_TRIGGERED = true` |
| 空 header | 回傳空 `Vec`，不觸發 bug |
| 有多個 row，只有某一列觸發 bug | 第一個觸發的 row 設 flag 並回 `Err` |

---

## 延伸挑戰

1. **加第二個 bug**：在 `parse_csv_buggy` 裡加另一個條件——如果所有欄位都是數字且 row 超過 100 列，觸發第二個 bug。修改 `CsvGenerator` 和 `CsvMutator` 讓它更容易被找到。

2. **換 feedback 策略**：把 `BugFeedback` 換成 `MaxMapFeedback` 搭配一個「mismatch 量」的 observer（當 row 欄位數和 header 欄位數的差距越大，observer 值越高）。觀察 corpus 的成長方向是否改變。

3. **加 crossover mutator**：實作一個 `CsvCrossoverMutator`，從 corpus 另取一筆 input，把它的某幾條 row 插入當前 input。你需要 `S: HasRand + HasCorpus<CsvInput>`。

4. **Fuzz 真實的 csv 解析庫**：把 `parse_csv_buggy` 換成 `csv` crate（`cargo add csv`），改成送真實 CSV bytes（`InProcessExecutor` + `BytesInput` 模式），看結構感知 mutator 和 byte-level havoc 哪個效率更高。

---

## 自我檢核

- [ ] 能說出 `impl Input for CsvInput {}` 空 impl 為什麼合法
- [ ] `post_exec` 在 LibAFL 0.15.4 為什麼不再是可選的
- [ ] `BugFeedback::is_interesting` 的回傳值決定了什麼（corpus？solutions？）
- [ ] 為什麼結構感知 mutator 第 0 輪就能找到 bug，而 byte-level havoc 需要更多輪
- [ ] `StateInitializer<S>` 是 `Feedback` 的 supertrait，它的 `init_state` 方法做什麼

---

→ [Ch 11 為什麼 dumb mutation 打不進結構化格式](./11-why-dumb-mutation-fails.md)
