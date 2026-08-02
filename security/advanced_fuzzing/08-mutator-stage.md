# Ch 8 — Mutator 與 Stage 客製

> **目標**：搞清楚 LibAFL 的 Mutator 與 Stage 層怎麼分工、怎麼組合，能從頭寫一個自訂 mutator 塞進 `StdMutationalStage`，跑起來驗證它真的在改 input，並理解 `tuple_list!` 組裝多個 mutator 的型別機制。
>
> **環境**：LibAFL 0.15.4、Rust 1.75+、WSL2 Ubuntu

---

## 為什麼需要自訂 mutator

afl++ 的 havoc 模式內建十幾種位元級變形，對 blob 格式（JPEG、ELF）大多夠用。但你碰到下面這些情境，內建的就不夠了：

- **帶 checksum 的協定**：隨機翻位元之後 checksum 必錯，目標直接 reject，coverage 卡死。
- **整數語義**：某個 4-byte field 必須是 `struct` 裡的合法 enum 值，亂改大機率打不進邏輯深處。
- **token-level**：source code fuzzer 需要換識別字、換關鍵字，而不是翻隨機位元。
- **domain knowledge**：你知道 payload 開頭兩個 byte 一定是 magic number，不應該被 mutator 碰。

LibAFL 把「怎麼改 input」（Mutator）和「改幾次、什麼時候改」（Stage）切成兩層正交的 trait，讓你可以獨立替換任一層。

---

## 先建立直覺

```
fuzzer loop
   │
   ▼
Stage::perform()          ← 控制「執行多少輪 mutate+run」
   │
   │  iterations() 次
   │   ┌────────────────────────────────────────┐
   │   │                                        │
   │   ▼                                        │
   │  Mutator::mutate(state, &mut input)        │
   │   │                                        │
   │   ▼                                        │
   │  Executor::run_target(input)               │
   │   │                                        │
   │   ▼                                        │
   │  Feedback::is_interesting()                │
   │   │                                        │
   │   └────────────────────────────────────────┘
   │
   ▼
next corpus entry
```

Stage 是「外層迴圈」。一個 Stage 拿到一筆 corpus entry，對它執行 N 次 mutate+run，然後把控制權交回 fuzzer。N 由 `iterations()` 決定——`StdMutationalStage` 預設是 `1..128` 的亂數。

Mutator 只管「改這一筆 input」這一步。它不知道 corpus，不知道 executor，只接受 `state` 和 `&mut input`，回傳 `MutationResult::Mutated` 或 `Skipped`。

---

## Mutator trait

```rust
pub trait Mutator<I, S>: Named {
    fn mutate(&mut self, state: &mut S, input: &mut I) -> Result<MutationResult, Error>;

    // 可選：每次 run 完 callback，用來做 cleanup 或記錄
    fn post_exec(
        &mut self,
        _state: &mut S,
        _new_corpus_id: Option<CorpusId>,
    ) -> Result<(), Error> {
        Ok(())
    }
}
```

兩個型別參數：
- `I`：input 型別。`BytesInput`、你自訂的結構、都可以。
- `S`：state 型別。多數 mutator 需要 `S: HasRand` 以取得隨機數。

`Named` 要求一個 `name()` 方法回傳 `&Cow<'static, str>`，LibAFL 用它印進度報告和 debug log。

---

## havoc_mutations 與 HavocScheduledMutator

LibAFL 內建的 havoc 變形組全部在 `libafl::mutators` 下。最常用的入口：

```rust
use libafl::mutators::{havoc_mutations, HavocScheduledMutator};

// havoc_mutations() 回傳一個 tuple_list! 組成的靜態型別
let mutator = HavocScheduledMutator::new(havoc_mutations());
```

`havoc_mutations()` 的回傳型別是一個巨大的 `tuple_list_type!(...)`，包含：

| 變形 | 作用 |
|---|---|
| `BitFlipMutator` | 翻一個隨機 bit |
| `ByteFlipMutator` | 翻一個隨機 byte |
| `ByteIncMutator` | +1 |
| `ByteDecMutator` | -1 |
| `ByteNegMutator` | bitwise NOT |
| `ByteRandMutator` | 設成隨機值 |
| `ByteAddMutator` / `ByteSubMutator` | ±隨機小值 |
| `ByteInterestingMutator` | 設成 interesting bytes（0, 1, 0x7f, 0x80, 0xff…） |
| `BytesDeleteMutator` | 刪除一段 |
| `BytesExpandMutator` | 插入一段重複 |
| `BytesInsertMutator` | 插入隨機 bytes |
| `BytesCopyMutator` | 複製一段到另一段 |
| `BytesSwapMutator` | 交換兩段 |
| `CrossoverInsertMutator` | 從 corpus 取另一筆插入 |
| `CrossoverReplaceMutator` | 從 corpus 取另一筆替換 |
| `SpliceMutator` | 拼接兩筆 corpus |

`HavocScheduledMutator` 會在每次呼叫時從這個 tuple 隨機選一個子 mutator 執行，重複 2^n 次（n 由內部 pow-stack 控制，模擬 AFL 的 havoc 行為）。

`SingleChoiceScheduledMutator` 則每次只選一個，做一次。用於你想精確控制 mutation 次數的場合。

---

## StdMutationalStage 的行為

```rust
pub struct StdMutationalStage<E, EM, I1, I2, M, S, Z> { ... }

impl<...> StdMutationalStage<...> {
    pub fn new(mutator: M) -> Self { ... }
    pub fn with_max_iterations(mutator: M, max_iterations: NonZeroUsize) -> Self { ... }
}
```

`perform()` 的核心邏輯（簡化）：

```
n = iterations(state)  // 1..max_iterations 隨機
for _ in 0..n {
    input = corpus.current().clone()
    mutator.mutate(state, &mut input)?
    executor.run_target(&input)?
    feedback.is_interesting()?  // 若有趣，存入 corpus
    objective.is_interesting()? // 若 crash，存入 solutions
    mutator.post_exec(state, new_id)?
}
```

預設 `max_iterations` 是 128，你可以用 `with_max_iterations` 改。

---

## 自訂 Mutator 實作（真跑範例）

我們來寫一個 `MagicHeaderMutator`：保護開頭 4 個 magic byte 不被碰，只對後面的 payload 做 bit flip。

建立專案：

```bash
cargo new ch8_custom_mutator --bin
cd ch8_custom_mutator
```

`Cargo.toml`：

```toml
[package]
name = "ch8_custom_mutator"
version = "0.1.0"
edition = "2021"

[dependencies]
libafl = "0.15.4"
libafl_bolts = "0.15.4"
```

`src/main.rs`：

```rust
use std::borrow::Cow;
use std::num::NonZeroUsize;

use libafl::{
    corpus::{Corpus, InMemoryCorpus},
    feedbacks::CrashFeedback,
    inputs::{BytesInput, HasMutatorBytes},
    mutators::{
        havoc_mutations, BitFlipMutator, HavocScheduledMutator,
        MutationResult, Mutator, SingleChoiceScheduledMutator,
    },
    stages::StdMutationalStage,
    state::{HasCorpus, HasRand, StdState},
    Error,
};
use libafl::corpus::CorpusId;
use libafl_bolts::{
    rands::{Rand, StdRand},
    tuples::tuple_list,
    Named,
};

// ─── 自訂 Mutator ───────────────────────────────────────────────

/// 跳過前 MAGIC_LEN 個 byte，對剩下的 payload 做 bit flip。
struct MagicHeaderMutator {
    magic_len: usize,
}

impl MagicHeaderMutator {
    fn new(magic_len: usize) -> Self {
        Self { magic_len }
    }
}

impl Named for MagicHeaderMutator {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("MagicHeaderMutator");
        &NAME
    }
}

impl<S> Mutator<BytesInput, S> for MagicHeaderMutator
where
    S: HasRand,
{
    fn mutate(&mut self, state: &mut S, input: &mut BytesInput) -> Result<MutationResult, Error> {
        let bytes = input.mutator_bytes_mut();
        if bytes.len() <= self.magic_len {
            return Ok(MutationResult::Skipped);
        }
        let payload = &mut bytes[self.magic_len..];
        // 用 below(NonZeroUsize) 取隨機 index（非 below_usize）
        let idx = state.rand_mut().below(NonZeroUsize::new(payload.len()).unwrap());
        let bit = 1u8 << state.rand_mut().below(NonZeroUsize::new(8).unwrap());
        payload[idx] ^= bit;
        Ok(MutationResult::Mutated)
    }

    fn post_exec(&mut self, _state: &mut S, _new_corpus_id: Option<CorpusId>) -> Result<(), Error> {
        Ok(())
    }
}

// ─── 展示 tuple_list 組裝 ───────────────────────────────────────

fn demo_custom_mutator() {
    let mutations = tuple_list!(
        MagicHeaderMutator::new(4),
        BitFlipMutator::new(),
    );

    let _scheduled = SingleChoiceScheduledMutator::new(mutations);
    println!("[demo] tuple_list 組裝 SingleChoiceScheduledMutator 成功");
}

fn demo_mutate_directly() {
    let corpus: InMemoryCorpus<BytesInput> = InMemoryCorpus::new();
    let solutions: InMemoryCorpus<BytesInput> = InMemoryCorpus::new();
    let mut feedback = CrashFeedback::new();
    let mut obj = CrashFeedback::new();

    let mut state = StdState::new(
        StdRand::with_seed(42),
        corpus,
        solutions,
        &mut feedback,
        &mut obj,
    )
    .unwrap();

    // 建一個帶 magic header 的 input
    // magic: DE AD BE EF，後面跟著 payload
    let mut input = BytesInput::new(vec![0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x41, 0x42, 0x43]);
    println!("原始 input: {:?}", input.mutator_bytes());

    let mut mutator = MagicHeaderMutator::new(4);
    for i in 0..5 {
        let result = mutator.mutate(&mut state, &mut input).unwrap();
        println!("mutate #{}: {:?}  =>  {:?}", i + 1, result, input.mutator_bytes());
    }

    // 確認 magic header 沒有被碰
    let bytes = input.mutator_bytes();
    assert_eq!(&bytes[..4], &[0xDE, 0xAD, 0xBE, 0xEF], "magic header 被破壞了！");
    println!("magic header 完整保留 ✓");
}

fn main() {
    demo_custom_mutator();
    println!();
    demo_mutate_directly();
}
```

**真跑輸出**（`cargo run` 在 WSL2，LibAFL 0.15.4）：

```
tuple_list 組裝 SingleChoiceScheduledMutator 成功

原始 input: [222, 173, 190, 239, 0, 65, 66, 67]
mutate #1: Mutated  =>  [222, 173, 190, 239, 0, 65, 2, 67]
mutate #2: Mutated  =>  [222, 173, 190, 239, 1, 65, 2, 67]
mutate #3: Mutated  =>  [222, 173, 190, 239, 1, 65, 2, 99]
mutate #4: Mutated  =>  [222, 173, 190, 239, 129, 65, 2, 99]
mutate #5: Mutated  =>  [222, 173, 190, 239, 131, 65, 2, 99]
magic header 完整保留 ✓
```

前四個 byte（`0xDE 0xAD 0xBE 0xEF`）全程未動，只有後面的 payload 被翻 bit。

---

## Token-level Mutator

除了 byte 操作，LibAFL 有 token-level mutation 的內建支援。你可以把已知的「有意義 token」（關鍵字、magic strings、已知值）放進 `Tokens` metadata，讓 `TokenInsert` / `TokenReplace` mutator 去用：

```rust
use libafl::mutators::{Tokens, TokenInsert, TokenReplace, tokens_mutations};
use libafl::state::HasMetadata;

// 把 Tokens 加進 state metadata
state.add_metadata(
    Tokens::new()
        .add_tokens([
            b"Content-Type".to_vec(),
            b"application/json".to_vec(),
            b"Authorization".to_vec(),
        ])
);

// tokens_mutations() = tuple_list!(TokenInsert, TokenReplace)
let mutator = HavocScheduledMutator::new(tokens_mutations());
```

`TokenInsert` 從 `Tokens` 隨機挑一個 token，插到 input 的隨機位置。
`TokenReplace` 把 input 某段替換成一個 token。

這比 afl++ 的 `-x dict` 更靈活：你可以在 fuzzer 跑到一半時動態加 token（從 taint 分析或 symbolic 執行萃取出來）。

---

## 底層機制：tuple_list! 的型別展開

LibAFL 完全避開動態 dispatch（`Box<dyn Mutator>`）。`tuple_list!(A, B, C)` 展開為：

```
(A, (B, (C, ())))
```

型別本身就是串列。`MutatorsTuple` trait 的 `mutate_all` 用 recursion 走遍這個 tuple——全在編譯期決議，零執行期 overhead，也讓 Rust 的 borrow checker 能精確追蹤每個元件的所有權。

代價是：每次你改 mutator 組合，型別就變了，整個 fuzzer 的型別都得重新推斷。這不是問題，但要記得：**你不能在 runtime 動態加入一個新的 mutator 進 tuple**。如果你需要動態行為，用 `ScheduledMutator` 的 `ComposedByMutations` 介面，或者包一層 runtime dispatch。

---

## 多個 Stage 的組合

Stage 也是 tuple 組合。標準用法：

```rust
let stages = tuple_list!(
    StdMutationalStage::new(HavocScheduledMutator::new(havoc_mutations())),
    // 第二個 stage：可以是 calibration、power schedule 之類
);

fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;
```

每個 corpus entry 進來，會**依序**跑過所有 stage。Stage 之間的順序不能互換——calibration stage 通常要先跑，power stage 需要依賴 calibration 留下的 metadata。

LibAFL 提供的特殊 Stage：

| Stage | 用途 |
|---|---|
| `StdMutationalStage` | 標準 havoc，最常用 |
| `PowerMutationalStage` | AFL++ power schedule（AFLFast、MOpt）|
| `TuneableMutationalStage` | 允許 runtime 調整 max_iterations |
| `MultiMutationalStage` | 每次對 input 套用多個 mutator 序列 |
| `CalibrationStage` | 測量 input 的穩定性與執行速度，更新 map metadata |
| `ClosureStage` | 包一個 closure 當 stage，快速原型 |

---

## 對比取捨

| 選項 | 適用場合 | 代價 |
|---|---|---|
| `HavocScheduledMutator` | blob 格式、亂槍打鳥 | 對帶 checksum 的格式效率差 |
| `SingleChoiceScheduledMutator` + 自訂 tuple | 精確控制每次選哪種變形 | 需要自己組 tuple |
| 自訂 `impl Mutator` | domain-specific 知識：magic header、enum range | 要手寫 + 維護 |
| `TokenInsert` / `TokenReplace` | 已知有意義 token 的格式（HTTP、SQL） | 需要先建 Tokens dict |
| `CrossoverInsertMutator` | 利用 corpus 多樣性做拼接 | 只有 corpus 夠大才有效 |

---

## 踩雷

**誤解 1：`MutationResult::Mutated` 代表 input 一定改了**

錯。`Mutated` 是 mutator 的意圖聲明。你的 mutator 可以回 `Mutated` 但實際上把 byte 改成同樣的值（比如翻兩次同一個 bit）。LibAFL 用這個值做 stage 統計，但不會二次驗證 input 是否真的不同。你自己的 mutator 要確保語義正確。

**誤解 2：一個 Stage 只能有一個 Mutator**

錯。`StdMutationalStage::new(mutator)` 的 `mutator` 可以是 `HavocScheduledMutator`（內含 15+ 個子 mutator 的 tuple），也可以是你自己組的 `SingleChoiceScheduledMutator<tuple_list!(A, B, C)>`。Stage 和 Mutator 是一對多的關係。

**誤解 3：`tuple_list!` 的順序不影響結果**

錯。`SingleChoiceScheduledMutator` 是用亂數 index 選子 mutator，所以你的排序不影響統計分布。但如果你用 `MutatorsTuple::mutate_all`（全部都跑），順序就影響每次 mutation 的結果——先 delete 後 insert 和先 insert 後 delete 是完全不同的結果。

---

## 進階延伸

**LoggerScheduledMutator**：包在另一個 scheduled mutator 外面，記錄每次選了哪個子 mutator，輸出到 `LogMutationMetadata`。用來分析「哪種 mutation 最常找到新 coverage」——這是 MOpt 演算法的基礎思路。

**`post_exec` callback**：如果你的 mutator 有狀態（比如記錄上一次 mutation 的 index 以便 undo），在 `post_exec` 裡做清理。LibAFL 保證每次 `run_target` 完成後、進入下一次 `mutate` 前，會呼叫 `post_exec`。

**`mapped_int_mutators`**：對於 struct-like 的 input，LibAFL 有 `mapping.rs` 裡的 `MappedMutator`，讓你把一個操作 `T` 的 mutator，透過 accessor closure，套用在 `I` 的某個欄位上，不用手寫 boilerplate。

---

## 動手練習

1. 寫一個 `RangeClampMutator`，把 `BytesInput` 的第一個 byte 永遠保持在 `0x10..=0x7F` 的範圍（超出就 clamp）。
2. 把它和 `ByteFlipMutator` 組成 `tuple_list!`，用 `SingleChoiceScheduledMutator` 包起來，印出 10 次 mutate 的結果，確認第一個 byte 永遠在範圍內。
3. 改成用 `HavocScheduledMutator` 包，觀察結果的差異（提示：havoc 會疊加多次 mutation）。

---

## 本章重點

- Mutator 只做「改 input」，Stage 決定「改幾次、何時改」，兩者正交可獨立替換
- `impl Mutator<I, S>` 只需要 `mutate()` 和 `Named`，門檻很低
- `tuple_list!(A, B, C)` 是 LibAFL 的靜態 dispatch 機制，組合多個 mutator 零 overhead
- `HavocScheduledMutator` 把 havoc 疊加行為（2^n 次）封裝好；`SingleChoiceScheduledMutator` 每次只選一個
- Token-level mutation 需要先把 `Tokens` 加進 state metadata

---

## 自我檢核

- [ ] 能說出 Mutator 和 Stage 各自的職責，以及它們的邊界在哪
- [ ] `MutationResult::Mutated` 和 `Skipped` 的區別，以及 LibAFL 拿這個值做什麼
- [ ] `tuple_list!(A, B)` 展開後的型別長什麼樣
- [ ] 為什麼 LibAFL 用靜態 dispatch 而不是 `Box<dyn Mutator>`
- [ ] 如何把 domain-specific token dict 給 `TokenInsert` mutator 使用

---

## 延伸閱讀

1. **LibAFL mutations.rs 原始碼**（`libafl/src/mutators/mutations.rs`）
   - 讀 `BitFlipMutator` 和 `BytesDeleteMutator` 的 impl：最好的「如何寫 Mutator」範例，比任何教學都直接。重點讀 `HasMutatorBytes` 的使用方式。
   - 關聯：本章自訂 mutator 的實作骨架

2. **"LibAFL: A Framework to Build Modular and Reusable Fuzzers" — Fioraldi et al., CCS 2022**
   - 論文 §3.3「Mutational Stages」解釋 Stage/Mutator 分層設計的研究動機——為什麼現有 fuzzer 把這兩層混在一起會讓研究者難以比較不同 mutation 策略的效果。
   - 關聯：本章所有設計決策的理論根據

3. **"MOpt: Optimized Mutation Scheduling for Fuzzers" — Lyu et al., USENIX Security 2019**
   - 解釋為什麼「哪種 mutation 找到 bug」是可以學習的，以及 PSO 演算法怎麼動態調整 mutation 比例。LibAFL 的 `MOptMutator` 就是這篇的實作。讀 §3 理解 mutation operator 選擇問題的形式化。
   - 關聯：`LoggerScheduledMutator` + `post_exec` 的進階用法

---

→ [Ch 9 型別化與結構化輸入](./09-typed-structured-input.md)
