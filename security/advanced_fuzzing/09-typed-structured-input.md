# Ch 9 — 型別化與結構化輸入（深挖章）

> **目標**：徹底搞清楚 LibAFL 的 `Input` trait 不只是 `Vec<u8>`；能自訂結構化 input 型別（如命令序列、簡單 AST）、搭配自訂 `Generator` 和自訂 mutator，讓 mutation 發生在語義層而非 byte 層。親手跑一個結構感知 fuzzer 片段，觀察它直接在結構層操作 input。
>
> **環境**：LibAFL 0.15.4、Rust 1.75+、WSL2 Ubuntu

---

## 為什麼 BytesInput 不夠

`BytesInput` 就是 `Vec<u8>` 的薄包裝。對大多數 blob 格式它夠用，但想想這些場合：

**場合 A：你在 fuzz 一個命令直譯器**。合法輸入是一串指令，每條指令是一個 opcode + 若干參數。丟隨機 bytes，99% 的情況開頭 opcode 就不合法，直譯器一行都沒跑進去就返回了。你需要的 mutation 是「換一個合法 opcode」、「隨機改一個參數值」——這些操作在 byte 層做代價極高。

**場合 B：你在 fuzz 一個 config parser，已知 config 結構是 key=value 配對的列表**。任意翻 byte 大概率會先炸 UTF-8 解析，根本進不到 parse key/value 的邏輯。

**場合 C：你在做 differential fuzzing，要同時把同一筆 input 送給兩個實作**。兩邊都需要 serialize，你卻不想維護兩套 `Vec<u8>` 表示。

LibAFL 的 `Input` trait 是泛型的，不強制底層是 bytes。自訂 input 型別讓你把 mutation 提升到語義層。

---

## 先建立直覺

```
byte-level fuzzer                   structured fuzzer
─────────────────────────────────   ──────────────────────────────────
Input: Vec<u8>                      Input: Vec<Cmd>
 [0x03, 0x41, 0xFF, 0x00, ...]       [Cmd::Read{addr:0x40},
                                       Cmd::Write{addr:0x80, val:1}]

Mutation: 翻 bit                    Mutation: 換 opcode
          刪 byte                             改 addr 的某個 bit
          插入隨機 byte                       複製一條指令
                                              刪一條指令

送進 target: 原樣送                 送進 target: serialize → bytes
                                    (你控制 serialize 邏輯)
```

關鍵洞見：**mutation 發生在哪一層，決定了 fuzzer 能探索的語義空間**。byte-level 的 mutation 可以找到 parser 本身的 bug（格式解析錯誤）；structured-level 的 mutation 才能有效探索 interpreter 的語義 bug（邏輯錯誤、狀態機問題）。

---

## Input trait 的真實要求

```rust
#[cfg(feature = "std")]
pub trait Input: Clone + Serialize + serde::de::DeserializeOwned + Debug + Hash {
    fn to_file<P: AsRef<Path>>(&self, path: P) -> Result<(), Error> { ... }
    fn from_file<P: AsRef<Path>>(path: P) -> Result<Self, Error> { ... }
    fn generate_name(&self, _id: Option<CorpusId>) -> String { ... }
}
```

就這樣。只要你的型別能：
1. `Clone`
2. `Serialize + DeserializeOwned`（serde）
3. `Debug`
4. `Hash`

就能 `impl Input for YourType {}`，不需要額外方法（`to_file`/`from_file` 有預設實作，用 `postcard` 序列化到磁碟）。

---

## 相關 trait：HasTargetBytes 與 HasMutatorBytes

| Trait | 作用 | 誰實作 |
|---|---|---|
| `Input` | corpus 存取、磁碟序列化 | 你的自訂型別 |
| `HasTargetBytes` | 「怎麼把 input 餵給 target」 | 你的型別，或靠 `InputConverter` |
| `HasMutatorBytes` | 讓標準 byte-level mutator 能操作 | `BytesInput`、需要就實作 |

**自訂 structured input 通常不實作 `HasMutatorBytes`**——那是 byte-level mutator 的介面。你的 structured mutator 直接操作你的型別欄位，不需要走 byte 這一層。

把 input 送給 target 有兩種方式：
1. **實作 `HasTargetBytes`**：target 直接消費 bytes，你的 `target_bytes()` 負責 serialize。
2. **自訂 Executor 的 harness**：harness closure 直接拿到你的 input，自行決定怎麼送。（Ch 7 的 `InProcessExecutor` 就是這樣——harness 是泛型的。）

---

## 核心概念：Generator trait

除了 mutator，structured input 還需要一個 `Generator`——負責從零生成初始語料（seed）：

```rust
pub trait Generator<I, S> {
    fn generate(&mut self, state: &mut S) -> Result<I, Error>;
}
```

`RandBytesGenerator` 是 LibAFL 內建的、生成隨機 bytes 的 generator。你需要為自訂 input 型別寫自己的 generator。

Generator 在 fuzzer 初始化時用來填充初始 corpus。之後的 corpus 成長靠 mutation。如果你有實體 seed 檔案，也可以直接 load 而不用 generator。

---

## 實作：命令序列 Input

我們把一個簡單的「命令序列 fuzzer」從頭做完，包含：自訂 Input、Generator、Mutator，以及讓 harness 消費它的邏輯。

**目標系統**（假想的有 bug 的 VM）：

```
指令集：
  READ  addr:u8          → 讀記憶體 addr
  WRITE addr:u8 val:u8   → 寫記憶體 addr = val
  ADD   dst:u8 src:u8    → 寫 mem[dst] = mem[dst] + mem[src]
  JUMP  offset:i8        → 跳 offset 條指令

Bug：addr >= 200 的 WRITE 觸發越界寫入
```

建立專案：

```bash
cargo new ch9_structured_input --bin
cd ch9_structured_input
```

`Cargo.toml`：

```toml
[package]
name = "ch9_structured_input"
version = "0.1.0"
edition = "2021"

[dependencies]
libafl = "0.15.4"
libafl_bolts = "0.15.4"
serde = { version = "1", features = ["derive"] }
postcard = { version = "1", features = ["alloc"] }
```

`src/main.rs`：

```rust
use std::borrow::Cow;
use std::hash::{Hash, Hasher};
use std::num::NonZeroUsize;

use libafl::{
    corpus::{CorpusId, InMemoryCorpus},
    feedbacks::CrashFeedback,
    generators::Generator,
    inputs::{HasTargetBytes, Input},
    mutators::{MutationResult, Mutator},
    state::{HasCorpus, HasRand, StdState},
    Error,
};
use libafl_bolts::{
    rands::{Rand, StdRand},
    Named,
    ownedref::OwnedSlice,
};
use serde::{Deserialize, Serialize};

// ─── 定義結構化 Input ───────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum Cmd {
    Read  { addr: u8 },
    Write { addr: u8, val: u8 },
    Add   { dst: u8, src: u8 },
    Jump  { offset: i8 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CmdSeqInput {
    pub cmds: Vec<Cmd>,
}

// 手動實作 Hash
impl Hash for CmdSeqInput {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let serialized = postcard::to_allocvec(self).unwrap_or_default();
        serialized.hash(state);
    }
}

// Input trait：derive 都滿足，空 impl 即可
impl Input for CmdSeqInput {}

// HasTargetBytes：serialize 成 bytes 送給外部 target
impl HasTargetBytes for CmdSeqInput {
    fn target_bytes(&self) -> OwnedSlice<'_, u8> {
        let bytes = postcard::to_allocvec(self).unwrap_or_default();
        OwnedSlice::from(bytes)
    }
}

// ─── Generator ───────────────────────────────────────────────────

pub struct CmdSeqGenerator {
    max_len: usize,
}

impl CmdSeqGenerator {
    pub fn new(max_len: usize) -> Self {
        Self { max_len }
    }
}

impl<S: HasRand> Generator<CmdSeqInput, S> for CmdSeqGenerator {
    fn generate(&mut self, state: &mut S) -> Result<CmdSeqInput, Error> {
        let rand = state.rand_mut();
        let len = 1 + rand.below(NonZeroUsize::new(self.max_len).unwrap_or(NonZeroUsize::new(1).unwrap()));
        let mut cmds = Vec::with_capacity(len);
        for _ in 0..len {
            let cmd = match rand.below(NonZeroUsize::new(4).unwrap()) {
                0 => Cmd::Read  { addr: rand.below(NonZeroUsize::new(256).unwrap()) as u8 },
                1 => Cmd::Write { addr: rand.below(NonZeroUsize::new(256).unwrap()) as u8,
                                  val:  rand.below(NonZeroUsize::new(256).unwrap()) as u8 },
                2 => Cmd::Add   { dst: rand.below(NonZeroUsize::new(256).unwrap()) as u8,
                                  src: rand.below(NonZeroUsize::new(256).unwrap()) as u8 },
                _ => Cmd::Jump  { offset: (rand.below(NonZeroUsize::new(128).unwrap()) as i8)
                                          .wrapping_sub(64) },
            };
            cmds.push(cmd);
        }
        Ok(CmdSeqInput { cmds })
    }
}

// ─── 結構感知 Mutator ────────────────────────────────────────────

/// 4 種結構級別的 mutation：
/// 0. InsertCmd：在隨機位置插一條新指令
/// 1. DeleteCmd：刪一條指令
/// 2. MutateCmd：改某條指令的一個欄位
/// 3. SwapCmds：交換兩條指令的位置
pub struct CmdSeqMutator;

impl Named for CmdSeqMutator {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("CmdSeqMutator");
        &NAME
    }
}

impl<S: HasRand> Mutator<CmdSeqInput, S> for CmdSeqMutator {
    fn mutate(
        &mut self,
        state: &mut S,
        input: &mut CmdSeqInput,
    ) -> Result<MutationResult, Error> {
        if input.cmds.is_empty() {
            let addr = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
            input.cmds.push(Cmd::Read { addr });
            return Ok(MutationResult::Mutated);
        }

        let choice = state.rand_mut().below(NonZeroUsize::new(4).unwrap());
        match choice {
            0 => {
                // InsertCmd
                let pos = state.rand_mut().below(
                    NonZeroUsize::new(input.cmds.len() + 1).unwrap()
                );
                let addr = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                let val  = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                let new_cmd = Cmd::Write { addr, val };
                input.cmds.insert(pos, new_cmd);
            }
            1 => {
                // DeleteCmd（保留至少 1 條）
                if input.cmds.len() > 1 {
                    let pos = state.rand_mut().below(
                        NonZeroUsize::new(input.cmds.len()).unwrap()
                    );
                    input.cmds.remove(pos);
                }
            }
            2 => {
                // MutateCmd：改某條指令的欄位
                let pos = state.rand_mut().below(
                    NonZeroUsize::new(input.cmds.len()).unwrap()
                );
                match &mut input.cmds[pos] {
                    Cmd::Read  { addr } => {
                        *addr = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                    }
                    Cmd::Write { addr, val } => {
                        if state.rand_mut().below(NonZeroUsize::new(2).unwrap()) == 0 {
                            *addr = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                        } else {
                            *val = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                        }
                    }
                    Cmd::Add { dst, src } => {
                        if state.rand_mut().below(NonZeroUsize::new(2).unwrap()) == 0 {
                            *dst = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                        } else {
                            *src = state.rand_mut().below(NonZeroUsize::new(256).unwrap()) as u8;
                        }
                    }
                    Cmd::Jump { offset } => {
                        *offset = (state.rand_mut().below(NonZeroUsize::new(128).unwrap()) as i8)
                            .wrapping_sub(64);
                    }
                }
            }
            _ => {
                // SwapCmds
                if input.cmds.len() >= 2 {
                    let i = state.rand_mut().below(NonZeroUsize::new(input.cmds.len()).unwrap());
                    let j = state.rand_mut().below(NonZeroUsize::new(input.cmds.len()).unwrap());
                    if i != j {
                        input.cmds.swap(i, j);
                    }
                }
            }
        }
        Ok(MutationResult::Mutated)
    }

    fn post_exec(&mut self, _state: &mut S, _new_corpus_id: Option<CorpusId>) -> Result<(), Error> {
        Ok(())
    }
}

// ─── 假想的 target 「VM」 ────────────────────────────────────────

fn execute_vm(input: &CmdSeqInput) -> bool {
    // 回傳 true = crash
    let mut mem = [0u8; 256];
    let mut pc = 0i32;
    let limit = input.cmds.len() as i32;
    let mut steps = 0;

    while pc >= 0 && pc < limit && steps < 1000 {
        steps += 1;
        match &input.cmds[pc as usize] {
            Cmd::Read { addr } => {
                let _ = mem[*addr as usize];
            }
            Cmd::Write { addr, val } => {
                if *addr >= 200 {
                    return true; // BUG: 越界
                }
                mem[*addr as usize] = *val;
            }
            Cmd::Add { dst, src } => {
                let v = mem[*src as usize];
                mem[*dst as usize] = mem[*dst as usize].wrapping_add(v);
            }
            Cmd::Jump { offset } => {
                pc += *offset as i32;
                continue;
            }
        }
        pc += 1;
    }
    false
}

fn main() {
    let rand = StdRand::with_seed(0xdeadbeef);
    let corpus: InMemoryCorpus<CmdSeqInput> = InMemoryCorpus::new();
    let solutions: InMemoryCorpus<CmdSeqInput> = InMemoryCorpus::new();
    let mut feedback = CrashFeedback::new();
    let mut obj = CrashFeedback::new();

    let mut state = StdState::new(
        rand,
        corpus,
        solutions,
        &mut feedback,
        &mut obj,
    )
    .unwrap();

    // 用 Generator 生成初始 seed
    let mut gen = CmdSeqGenerator::new(6);
    let seed = gen.generate(&mut state).unwrap();
    println!("=== 生成的初始 seed ===");
    for (i, cmd) in seed.cmds.iter().enumerate() {
        println!("  [{i}] {cmd:?}");
    }

    // 展示自訂 mutator 的效果
    let mut mutator = CmdSeqMutator;
    let mut input = seed.clone();
    println!("\n=== 10 次結構級別 mutation ===");
    let mut crash_found = false;
    for round in 0..10 {
        let prev_len = input.cmds.len();
        let result = mutator.mutate(&mut state, &mut input).unwrap();
        let crashed = execute_vm(&input);
        println!(
            "round {:2}: {:?}  len {} → {}{}",
            round,
            result,
            prev_len,
            input.cmds.len(),
            if crashed { "  *** CRASH ***" } else { "" }
        );
        if crashed {
            crash_found = true;
        }
    }

    // 刻意構造一個 crash case 驗證 VM 的 bug
    let crash_case = CmdSeqInput {
        cmds: vec![Cmd::Write { addr: 200, val: 0xFF }],
    };
    assert!(execute_vm(&crash_case), "預期的 crash 沒有觸發");
    println!("\n刻意構造的 crash case 正確觸發 ✓");
    println!("crash input: {:?}", crash_case.cmds);

    // HasTargetBytes 展示
    let tb = crash_case.target_bytes();
    println!("postcard serialized: {} bytes", tb.len());
}
```

**真跑輸出**（WSL2，seed=0xdeadbeef）：

```
=== 生成的初始 seed ===
  [0] Write { addr: 177, val: 44 }
  [1] Jump { offset: 55 }

=== 10 次結構級別 mutation ===
round  0: Mutated  len 2 -> 3
round  1: Mutated  len 3 -> 4
round  2: Mutated  len 4 -> 4
round  3: Mutated  len 4 -> 4
round  4: Mutated  len 4 -> 5
round  5: Mutated  len 5 -> 5
round  6: Mutated  len 5 -> 6  *** CRASH ***
round  7: Mutated  len 6 -> 5  *** CRASH ***
round  8: Mutated  len 5 -> 6  *** CRASH ***
round  9: Mutated  len 6 -> 7  *** CRASH ***

刻意構造的 crash case 正確觸發 v
crash input: [Write { addr: 200, val: 255 }]
postcard serialized: 4 bytes
```

第 6 輪開始出現 crash——因為 InsertCmd 插入了 `Write { addr >= 200 }` 就能觸發 VM bug。這展示了結構感知 fuzzer 的威力：**直接在語義層操作指令序列**，不需要 byte-level mutation 碰巧湊出正確的 addr 值。

---

## 底層機制：為什麼不要 HasMutatorBytes

`HasMutatorBytes` 暴露的是 `&mut [u8]`，讓標準 byte-level mutator（`BitFlipMutator` 等）能在不知道 input 型別的情況下操作它。你的 `CmdSeqInput` 如果實作了 `HasMutatorBytes`，那 byte-level mutator 就會去翻 `postcard::to_allocvec(&self)` 的結果——翻了之後你還得 deserialize 回來，大概率 deserialize 失敗，等同於讓 mutation 無效。

**結構感知 mutator 的正確做法：直接操作型別欄位，不走 byte 這一層。**

---

## 底層機制：Input 與 Corpus 的互動

```
Corpus 存的是 Testcase<I>
│
├── Testcase::input: Option<I>   ← 你的 CmdSeqInput
├── Testcase::id: CorpusId
└── Testcase::metadata: ...      ← CalibrationMeta、PowerScheduleMeta 等

Corpus::on_add() 觸發 Feedback::append_metadata()
  → 把這次執行的 coverage map 存進 Testcase metadata
```

`Input::to_file()` 預設用 `postcard` 序列化到磁碟，所以你的自訂 input corpus 在磁碟上存的是 postcard bytes，不是人看得懂的格式。如果你想要 human-readable corpus，override `to_file` 和 `from_file`。

`generate_name()` 預設是 `{hash:016x}`，重寫它可以讓 corpus 目錄更好讀。

---

## 組合：自訂 Input + 標準 Mutator

有時你的自訂 input 底層就是一個 `Vec<u8>`（比如 hex 解碼後的 bytes），你只是想在外面包一層型別強制 invariant。這種情況可以對你的型別實作 `HasMutatorBytes`，讓標準 byte mutator 仍能操作底層 bytes，同時自訂 mutator 操作語義層：

```rust
pub struct HeaderPayloadInput {
    pub header: [u8; 4],   // 固定 magic，不可被 mutator 碰
    pub payload: Vec<u8>,  // 自由 bytes，可以用標準 mutator
}

impl HasMutatorBytes for HeaderPayloadInput {
    fn mutator_bytes(&self) -> &[u8] {
        &self.payload   // 只暴露 payload 給 byte-level mutator
    }
    fn mutator_bytes_mut(&mut self) -> &mut [u8] {
        &mut self.payload
    }
}
```

然後 `BitFlipMutator` 等就只操作 `payload`，`header` 自動受保護。

---

## 對比取捨

| 策略 | 適合目標 | 需要自訂的東西 |
|---|---|---|
| `BytesInput` + havoc | blob parser：PNG/ELF/zip | 幾乎不需要 |
| `BytesInput` + `HasMutatorBytes` 部分暴露 | 有 magic header 的協定 | 一個 impl block |
| 自訂 struct + 自訂 Mutator（本章） | 命令直譯器、VM、狀態機 | Input/Generator/Mutator 三份 |
| 自訂 struct + `InputConverter` | 需要送兩種格式給兩個 target | 額外一個 converter |
| 文法 fuzzing（Ch 13） | 程式語言、HTML、SQL | 文法定義 |

自訂結構化 input 的主要代價是「第一次設置時的 boilerplate」——但後續的 mutation 效率通常好一個數量級，值得。

---

## 踩雷

**誤解 1：`impl Input` 只有 BytesInput 能用**

完全錯。`Input` trait 的 supertrait 只有 `Clone + Serialize + DeserializeOwned + Debug + Hash`。只要這四個 derive 都加了，任何型別都能 `impl Input {}`（空實作，因為所有方法都有預設）。

**誤解 2：自訂 input 一定要實作 HasTargetBytes**

不一定。如果你的 harness closure 直接接受 `&CmdSeqInput`（`InProcessExecutor` 的 harness 是泛型的），根本不需要 `HasTargetBytes`。它只有在 executor 要把 input「轉成 bytes 送給外部 process」時才必要（比如 `ForkserverExecutor`）。

**誤解 3：結構化 mutator 比 byte-level mutator 更難發現 bug**

不對，這取決於目標。對於**語義層 bug**（邏輯錯誤、狀態機問題），結構化 mutator 快得多——因為每次 mutation 都保持 input 語義合法，能進到更深的程式邏輯。對於**格式解析 bug**（parser crash），byte-level 才是對的。大多數現代 fuzzer 是兩層都跑。

---

## 進階延伸

**MutatedTransform**：`StdMutationalStage` 的型別簽名是 `StdMutationalStage<E, EM, I1, I2, M, S, Z>`，其中 `I1: MutatedTransform<I2, S>`。這讓你可以讓 mutator 在 `I1`（一種 input 格式）上操作，但實際執行時送 `I2`（另一種）給 executor——比如你的 mutator 在 AST 層操作，但 executor 接收 bytes。不需要手寫 converter。

**EncodedInput**：LibAFL 的 `EncodedInput`（`src/inputs/encoded.rs`）是一個 `Vec<u32>` 包裝，其中每個 `u32` 是一個 token ID。`TokenInputEncoderDecoder` 負責 token ↔ bytes 的轉換。這是語法感知 fuzzer（如 Grimoire）的底層表示。

**Part 2 預告**：文法 fuzzing（Ch 11–15）把「定義合法 input 的語法規則」作為 mutation 的約束。你在本章學的自訂 `Generator` 和自訂 `Mutator` 模式是文法 fuzzer 的底層——文法 fuzzer 只是把「生成/變形規則」從 hardcode 的 Rust 邏輯換成了可配置的文法定義。

---

## 動手練習

1. 為 `CmdSeqInput` 加一個 `CrossoverMutator`：從 corpus 另取一筆 input，把它的某幾條指令插入當前 input 的隨機位置。（提示：你的 mutator 需要 `S: HasCorpus<CmdSeqInput> + HasRand`。）
2. 讓 `CmdSeqGenerator` 生成的指令序列保證 WRITE 的 addr 一律 < 200（刻意讓 generator 生成安全 seed）。觀察 fuzzer 還能不能找到 bug（提示：需要 mutation 把 addr 推過 200）。
3. 在 `execute_vm` 裡加第二個 bug：連續兩條 `ADD` 且 `dst == 0xFF` 觸發另一個 crash。修改 `CmdSeqMutator` 讓它更容易構造出這個情境。

---

## 本章重點

- `Input` trait 的實際要求：`Clone + Serialize + DeserializeOwned + Debug + Hash`，空 impl 即可
- `HasTargetBytes` 和 `HasMutatorBytes` 是可選的，根據 executor 種類決定是否需要
- `Generator<I, S>` 負責初始 corpus 生成，自訂 generator 讓你從第一筆 seed 就保持語義合法
- 結構感知 mutator 直接操作型別欄位，不走 byte 層，對語義 bug 的探索效率遠高於 byte-level
- 兩種思路可以組合：用 `HasMutatorBytes` 只暴露 payload 部分，結合 byte-level + 結構-level 兩套 mutator

---

## 自我檢核

- [ ] 能說出 `Input` trait 的 supertrait 要求，以及為什麼空 impl 就夠了
- [ ] `HasTargetBytes` 和 `HasMutatorBytes` 的區別，各自在什麼情境下需要
- [ ] 為什麼結構感知 mutator 對語義 bug 效率更高
- [ ] `Generator<I, S>` 的職責是什麼，它和 `Mutator` 的分工在哪裡
- [ ] 如果你的自訂 input 需要被兩個不同的 executor 用，你有哪些選擇

---

## 延伸閱讀

1. **LibAFL inputs/mod.rs 原始碼**（`libafl/src/inputs/mod.rs`）
   - 讀 `Input` trait 的 `#[cfg(feature = "std")]` 和 `#[cfg(not(feature = "std"))]` 兩個版本，理解 no_std 環境的限制。再看 `HasMutatorBytes` 的實作，理解為什麼 `Vec<u8>` 和 `&mut Vec<u8>` 都實作了它——這是 `BytesSubInput` 的基礎。
   - 關聯：本章 `HasMutatorBytes` 的使用方式

2. **"Grimoire: Synthesizing Structure while Fuzzing" — Blazytko et al., USENIX Security 2019**
   - Grimoire 從 byte-level coverage 觀察推斷 input 的隱含結構（不需要事先定義文法），自動合成 token 和樹狀結構。讀 §3 理解 generalization 步驟——這是從「無結構 fuzzing」到「有結構 fuzzing」的自動橋接，和本章手工定義 Input 型別是對稱的兩條路。
   - 關聯：Ch 13 文法 fuzzing 的前置思維

3. **"NAUTILUS: Fishing for Deep Bugs with Grammars" — Aschermann et al., NDSS 2019**
   - NAUTILUS 用 context-free grammar 生成語義合法的 input，並做 trie-based substring mutation。讀 §2 理解「grammar 作為 input 型別」的形式化——本章的 `CmdSeqInput` 是一個退化的文法（只有一層 `Vec<enum>`），NAUTILUS 的文法是真正的 CFG。
   - 關聯：Ch 13 文法 fuzzing，LibAFL `NautilusInput` 型別

---

→ [Ch 10 分散式 LibAFL](./10-libafl-distributed.md)
