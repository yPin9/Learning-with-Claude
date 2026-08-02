# 練習 E — Snapshot fuzz 一個不可重置目標

> **目標：** 實作一個「帶有不可重置副作用」的 target，然後用 snapshot 思維解決它——依環境不同，選擇 CRIU process snapshot、LibAFL fork-based snapshot executor、或 QEMU snapshot 模式。練習重點是設計 harness 和理解 snapshot 的確定性保證，不是搭環境。

---

## 背景

Ch 28–32 說明了 fork server 的根本限制：副作用無法隔離，kernel-side 狀態不在 fork 的範圍，closed binary 沒辦法注入 fork server。

本練習用一個人工設計的「不可重置 target」讓你感受這個問題，然後用三種方式解決它（依環境能力選擇）：

- **方案 A**：CRIU process snapshot（WSL2 可能可跑）
- **方案 B**：LibAFL `InProcessExecutor` + `fork` snapshot（無需特殊硬體）
- **方案 C**：QEMU snapshot 模式 + LibAFL `QemuExecutor`（需要 KVM，**[未實測]**）

三個方案解同一個問題，但在不同層面做 snapshot。建議**先做方案 B**（最容易跑通），理解概念後再視硬體情況嘗試 A 或 C。

---

## 任務規格

### Target 的設計

你要實作一個帶有累積副作用的 target 函數 `stateful_target.c`：

```c
// stateful_target.c
// 帶有不可重置副作用的 target
// 這個 target 有一個全域計數器，每次呼叫都累加
// 只有在計數器達到某個值時，某個 bug 才會觸發
// 這模擬了「需要多次互動才能觸發的 stateful bug」

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

// 全域副作用狀態（模擬不可重置的 kernel-side state）
static int global_call_count = 0;
static uint8_t accumulated_state[256] = {0};

// target 函數：帶副作用
int stateful_target(const uint8_t *data, size_t size) {
    if (size < 4) return 0;

    // 副作用 1：累積呼叫次數
    global_call_count++;

    // 副作用 2：把輸入 XOR 進 accumulated_state
    for (size_t i = 0; i < size && i < 256; i++) {
        accumulated_state[i] ^= data[i];
    }

    // 只有在第 3 次呼叫後，且 accumulated_state[0] 是特定值時，才觸發 bug
    // 這模擬了「需要特定狀態積累才能觸發」的 bug
    if (global_call_count >= 3 && accumulated_state[0] == 0xAA) {
        // bug：越界讀取
        volatile uint8_t oob = accumulated_state[data[0] + 200];  // 可能越界
        (void)oob;
    }

    // 正常路徑的處理
    uint32_t magic = *(uint32_t *)data;
    if (magic == 0xDEADBEEF) {
        // 正常的 crash（當 global_call_count == 1 時）
        if (global_call_count == 1) {
            abort();  // 這個 crash 很容易找到
        }
    }

    return 0;
}
```

**觀察**：如果用普通 fork server 跑這個 target：
- Fork server 在 `global_call_count = 0` 時 fork
- 每個子進程都從 `global_call_count = 0` 開始
- `accumulated_state` 每個子進程都是 `{0}`
- 因此「需要 `global_call_count >= 3`」的 bug 路徑**永遠不會被觸發**

這就是你要解決的問題。

### 問題一：確認 fork server 的失效

在做 snapshot 方案之前，先確認問題確實存在。

---

## 期望輸出

**問題一的確認**（afl-fuzz 跑 1 分鐘）：
```
# afl++ 的 corpus 只包含觸發 0xDEADBEEF 路徑的輸入
# 沒有 crash 涉及 global_call_count >= 3 的路徑
# 即使 crash 很快被找到，count>=3 的 bug 不在 crash 列表裡
```

**方案 B 的 LibAFL snapshot fuzzer 執行輸出**（概念性）：
```
[LibAFL] snapshot fuzzer starting...
[LibAFL] snapshot point: global_call_count = 3, accumulated_state[0] = 0x00
[LibAFL] corpus size: 1 initial seed
[LibAFL] exec/s: ~5000
[LibAFL] Found new coverage at exec #42
[LibAFL] CRASH: accumulated_state[0] = 0xAA, oob read at offset 255+data[0]
[LibAFL] Total execs: 10000, crashes: 1
```

---

## 卡住提示

**卡點一：「我的 LibAFL fuzzer 編不過，找不到正確的 Cargo.toml 依賴」**

LibAFL 的 API 隨版本變動很快。建議用 LibAFL 的 `examples/` 目錄的 fuzzer 為起點修改，不要從空白 `main.rs` 開始。找 `fork_qemu` 或 `inprocess` 範例，把 target 換成你的函數。

**卡點二：「CRIU checkpoint 失敗，說缺少某個 kernel feature」**

WSL2 的 CRIU 支援不完整是已知問題。如果 `criu check` 有 Warning，試著加 `--features=!fdinfo-version` 或查 CRIU 的 WSL2 compatibility issue。替代方案：直接跳到方案 B（LibAFL fork snapshot），概念完全等價。

**卡點三：「LibAFL 的 `InProcessExecutor` 怎麼做 snapshot？它好像只是在同進程裡跑」**

`InProcessExecutor` 本身沒有 snapshot，它依賴外層的 `TimeoutExecutor` 和 `CrashFeedback` 偵測 crash，然後 fork 出新進程繼續。如果你要「真正的 snapshot」（帶副作用的 reset），需要用 `InProcessForkExecutor`——它 fork 出子進程跑每個輸入，子進程 exit 後父進程的狀態不變，相當於「在 fork point 存了 snapshot」。

**卡點四：「如何讓 snapshot 在 global_call_count = 3 之後存下來？」**

方案 B 的關鍵：在 fuzzer 啟動時，先手動呼叫 `stateful_target()` 三次（用固定的「前置輸入」），讓 `global_call_count = 3`，然後把 `InProcessForkExecutor` 的 fork 點設在**這之後**。之後每個 fuzz 輸入從 `global_call_count = 3` 的狀態 fork 出來，能觸發 `count >= 3` 的路徑。

---

## 實作步驟

### Step 0：先確認問題存在（fork server 失效）

```c
// fuzz_harness_naive.c - 給 afl++ 的普通 harness
#include "stateful_target.h"

int main(int argc, char *argv[]) {
    // AFL 的標準 harness
    __AFL_INIT();
    uint8_t buf[1024];
    ssize_t n = read(0, buf, sizeof(buf));
    if (n > 0) {
        stateful_target(buf, n);
    }
    return 0;
}
```

```bash
# 編譯
AFL_HARDEN=1 afl-clang-fast -o fuzz_naive fuzz_harness_naive.c stateful_target.c

# 建立種子
echo -ne '\xEF\xBE\xAD\xDE' > seeds/seed1  # 0xDEADBEEF，觸發普通路徑
echo -ne '\xAA\x00\x00\x00' > seeds/seed2

# 跑 1 分鐘
timeout 60 afl-fuzz -i seeds -o out_naive -- ./fuzz_naive

# 檢查：crash 只有 0xDEADBEEF 那個，沒有 count>=3 的路徑
ls out_naive/default/crashes/
```

### Step 1：方案 B — LibAFL InProcessForkExecutor

建立 Rust 專案：

```bash
cargo new --bin snapshot_fuzzer
cd snapshot_fuzzer
```

`Cargo.toml`：

```toml
[package]
name = "snapshot_fuzzer"
version = "0.1.0"
edition = "2021"

[dependencies]
libafl = { version = "0.13", features = ["fork"] }
libafl_bolts = "0.13"

[lib]
name = "stateful_target"
crate-type = ["staticlib"]
```

把 `stateful_target.c` 接入（透過 build.rs 或 extern）。

核心 fuzzer 邏輯（`src/main.rs` 骨架）：

```rust
// src/main.rs
// [概念骨架，需要對應 LibAFL 版本調整 API]

use libafl::prelude::*;
use libafl_bolts::prelude::*;

extern "C" {
    fn stateful_target(data: *const u8, size: usize) -> i32;
    // 前置呼叫，讓狀態到達 count=3
    fn setup_snapshot_state();
}

fn main() {
    // Step 1: 讓 target 到達「有趣的起點」
    // 在 fork 之前手動呼叫 3 次，讓 global_call_count = 3
    unsafe {
        // 呼叫三次固定輸入，讓副作用累積到目標狀態
        let init_input = [0x00u8; 4];
        stateful_target(init_input.as_ptr(), init_input.len());
        stateful_target(init_input.as_ptr(), init_input.len());
        stateful_target(init_input.as_ptr(), init_input.len());
        // 現在 global_call_count = 3，accumulated_state = init_state
    }

    // Step 2: 建立 LibAFL executor，在當前狀態（count=3）fork
    // InProcessForkExecutor 在 fork() 後執行每個輸入
    // parent 的狀態（count=3）永遠不變

    let harness = |input: &BytesInput| {
        let data = input.target_bytes();
        unsafe {
            stateful_target(data.as_slice().as_ptr(), data.as_slice().len());
        }
        ExitKind::Ok
    };

    // [建立 observer, feedback, executor, scheduler, stages...]
    // 參考 LibAFL examples/baby_fuzzer 的骨架
    println!("Snapshot fuzzer: starting from global_call_count=3");
    // [完整 fuzzer loop]
}
```

### Step 2：驗證 snapshot 的效果

在 fuzzer 找到 crash 之後：

```bash
# 用 crash input 重現（注意：要手動呼叫 3 次前置，才能重現）
cat crash_input | ./reproduce_crash
# 如果 reproduce 只有 1 次呼叫就 crash，說明 snapshot 設得不對
# 如果 reproduce 需要 3 次前置呼叫才 crash，說明 snapshot 正確
```

### Step 3（選做）：方案 A — CRIU process snapshot

```bash
# Step 3a: 寫一個 helper 程式，先呼叫 3 次 target，然後等待 checkpoint
cat > setup_and_wait.c << 'EOF'
#include "stateful_target.h"
#include <stdio.h>
#include <unistd.h>

int main() {
    uint8_t init[4] = {0};
    // 呼叫 3 次，讓副作用積累
    stateful_target(init, 4);
    stateful_target(init, 4);
    stateful_target(init, 4);
    printf("State set up. global_call_count=3. PID=%d\n", getpid());
    printf("Ready for checkpoint. Press Enter to continue...\n");
    getchar();  // 在這裡等，給 CRIU 做 checkpoint
    // CRIU restore 後從這裡繼續
    printf("Resumed from checkpoint.\n");
    // 讀 stdin 作為 fuzz 輸入
    uint8_t buf[1024];
    ssize_t n = read(0, buf, sizeof(buf));
    if (n > 0) stateful_target(buf, n);
    return 0;
}
EOF
gcc -o setup_and_wait setup_and_wait.c stateful_target.c

# Step 3b: 跑 helper，等它輸出 "Ready for checkpoint"，然後 CRIU checkpoint
./setup_and_wait &
PID=$!
# 等 "Ready for checkpoint" 出現
sleep 1
sudo criu dump -t $PID -D /tmp/criu-snapshot --shell-job
echo "Checkpoint done at count=3"

# Step 3c: 寫腳本：restore → 注入一個 fuzz 輸入 → 觀察結果 → 重複
for seed in seeds/*; do
    sudo criu restore -D /tmp/criu-snapshot --shell-job < $seed
done
```

**[CRIU process snapshot 步驟在 WSL2 上可能因 pty 或 namespace 支援不完整而失敗。如果失敗，記錄 `criu dump` 的錯誤訊息，並用方案 B 替代。]**

### Step 4：效能對比

```bash
# 方案 B（LibAFL fork snapshot）的 exec/s
# 通常在 5,000–50,000/s，視 target 複雜度

# 普通 afl-fuzz（fork server）的 exec/s
# 對相同 target 通常在 1,000–10,000/s

# 差異：fork snapshot 因為 parent 狀態不變，
# 每次 fork 的 parent 是「已經在 count=3 的狀態」
# 而普通 fork server 的 parent 是「count=0 的狀態」
# 所以 snapshot 找得到的 bug，fork server 在同樣時間內找不到
```

---

## 完整參考解答

<details>
<summary>展開完整參考解答</summary>

### stateful_target.c（完整版）

```c
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

static int global_call_count = 0;
static uint8_t accumulated_state[256] = {0};

// 讓外部可以重置（只有方案 B 需要，但練習用）
void reset_target_state(void) {
    global_call_count = 0;
    memset(accumulated_state, 0, sizeof(accumulated_state));
}

// 前置設定：讓 count 到達 3
void setup_snapshot_state(void) {
    uint8_t init[4] = {0};
    for (int i = 0; i < 3; i++) {
        global_call_count++;
        for (int j = 0; j < 4; j++) {
            accumulated_state[j] ^= init[j];
        }
    }
    // 現在 global_call_count = 3, accumulated_state = {0,0,0,...}
}

int stateful_target(const uint8_t *data, size_t size) {
    if (size < 4) return 0;

    global_call_count++;

    for (size_t i = 0; i < size && i < 256; i++) {
        accumulated_state[i] ^= data[i];
    }

    if (global_call_count >= 3 && accumulated_state[0] == 0xAA) {
        // bug：越界讀取，如果 data[0] > 55，就讀到 accumulated_state 之外
        volatile uint8_t oob = accumulated_state[data[0] + 200];
        (void)oob;
    }

    uint32_t magic;
    memcpy(&magic, data, 4);
    if (magic == 0xDEADBEEF) {
        if (global_call_count == 1) {
            abort();
        }
    }

    return global_call_count;
}
```

### LibAFL snapshot fuzzer（簡化骨架，使用 baby_fuzzer 風格）

```rust
// Cargo.toml
// libafl = "0.13"
// libafl_bolts = "0.13"

// src/main.rs
use std::path::PathBuf;
use libafl::{
    corpus::{InMemoryCorpus, OnDiskCorpus},
    events::SimpleEventManager,
    executors::InProcessExecutor,
    feedback_or_fast,
    feedbacks::{CrashFeedback, MaxMapFeedback},
    fuzzer::{Fuzzer, StdFuzzer},
    inputs::BytesInput,
    monitors::SimpleMonitor,
    mutators::{havoc_mutations, StdScheduledMutator},
    observers::StdMapObserver,
    schedulers::QueueScheduler,
    stages::StdMutationalStage,
    state::StdState,
    Error,
};
use libafl_bolts::{
    rands::StdRand,
    tuples::tuple_list,
};

// 注意：這個骨架使用 InProcessExecutor，不是真正的 fork snapshot
// 真正的 fork snapshot 需要 InProcessForkExecutor（API 依版本而定）
// 這個版本示範「在 fork point 之前設定狀態」的概念

extern "C" {
    fn stateful_target(data: *const u8, size: usize) -> i32;
    fn setup_snapshot_state();
    fn reset_target_state();
}

// 全域 coverage map（模擬 AFL 的 bitmap）
static mut SIGNALS: [u8; 65536] = [0; 65536];
static mut SIGNALS_PTR: *mut u8 = unsafe { SIGNALS.as_mut_ptr() };

fn main() {
    // Step 1: 設定 snapshot 前的狀態（等同於在 fork point 前執行）
    unsafe {
        setup_snapshot_state();
        // 現在 global_call_count = 3
    }

    let monitor = SimpleMonitor::new(|s| println!("{s}"));
    let mut mgr = SimpleEventManager::new(monitor);

    let observer = unsafe {
        StdMapObserver::from_mut_ptr("signals", SIGNALS_PTR, 65536)
    };

    let mut feedback = MaxMapFeedback::new(&observer);
    let mut objective = CrashFeedback::new();

    let mut state = StdState::new(
        StdRand::with_seed(42),
        InMemoryCorpus::<BytesInput>::new(),
        OnDiskCorpus::new(PathBuf::from("./crashes")).unwrap(),
        &mut feedback,
        &mut objective,
    ).unwrap();

    // 初始種子
    let initial_inputs = vec![
        BytesInput::new(vec![0xAAu8, 0x38, 0x00, 0x00]),  // accumulated_state[0]=0xAA, data[0]=0x38 → OOB
        BytesInput::new(vec![0x00u8; 4]),
    ];
    for input in initial_inputs {
        state.corpus_mut().add(input.into()).unwrap();
    }

    let scheduler = QueueScheduler::new();
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    let mut harness = |input: &BytesInput| {
        let data = input.target_bytes();
        let slice = data.as_slice();
        unsafe {
            // 在 InProcess 模式下，如果有 abort()，這裡會 crash
            // 真正的 fork snapshot 會在子進程 crash，父進程繼續
            stateful_target(slice.as_ptr(), slice.len());
        }
        libafl::executors::ExitKind::Ok
    };

    let mut executor = InProcessExecutor::new(
        &mut harness,
        tuple_list!(observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
    ).unwrap();

    let mutator = StdScheduledMutator::new(havoc_mutations());
    let mut stages = tuple_list!(StdMutationalStage::new(mutator));

    // Fuzz loop
    fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)
        .expect("Error in fuzz loop");
}
```

**關鍵說明**：

上述骨架使用 `InProcessExecutor`，它在同一個進程裡跑每個輸入。`global_call_count` 在每次輸入後**不會重置**（因為我們沒有呼叫 `reset_target_state()`），這正是 snapshot 的效果——每個輸入都在「count 繼續累積」的狀態下執行。

真正的 `InProcessForkExecutor` 會 fork，子進程跑輸入，父進程的狀態不變——這才是正確的 snapshot 語意（每次輸入都從同一個 count=3 開始）。本骨架因 API 依賴版本，用 in-process 示範概念；讀者應參考 LibAFL 對應版本的 `InProcessForkExecutor` 文件。

### CRIU 方案的腳本

```bash
#!/bin/bash
# criu_snapshot_fuzz.sh
# [此腳本在 WSL2 上可能部分失敗，標注預期行為]

TARGET_BIN="./setup_and_wait"
CRIU_DIR="/tmp/criu-snapshot"
SEEDS_DIR="./seeds"
CRASHES_DIR="./crashes"

mkdir -p $CRIU_DIR $CRASHES_DIR

# Step 1: 啟動 target，讓它到達 count=3 後等待
$TARGET_BIN &
TARGET_PID=$!
echo "Target PID: $TARGET_PID"
sleep 1  # 等 target 印出 "Ready for checkpoint"

# Step 2: Checkpoint
echo "Checkpointing PID $TARGET_PID..."
sudo criu dump -t $TARGET_PID -D $CRIU_DIR --shell-job
if [ $? -ne 0 ]; then
    echo "[ERROR] CRIU checkpoint failed. See output above."
    echo "This may happen in WSL2 due to missing kernel features."
    echo "Use LibAFL fork snapshot (方案 B) instead."
    exit 1
fi
echo "Checkpoint successful."

# Step 3: Fuzz loop
for seed in $SEEDS_DIR/*; do
    echo "Testing: $seed"
    # Restore + inject input
    sudo criu restore -D $CRIU_DIR --shell-job < $seed
    EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo "CRASH found with input: $seed"
        cp $seed $CRASHES_DIR/crash_$(basename $seed)
    fi
done
echo "Done. Crashes in $CRASHES_DIR"
```

</details>

---

## 測試用例表

| 輸入 | 前置 count | 預期路徑 | 預期結果 |
|------|-----------|---------|---------|
| `[0x00,0x00,0x00,0x00]` × 1 | 0 | `count = 1` → 未觸發 count≥3 路徑 | return 1 |
| `[0xEF,0xBE,0xAD,0xDE]` × 1 | 0 | `count = 1, magic = 0xDEADBEEF` → abort | SIGABRT |
| `[0xAA,0x38,0x00,0x00]` × 1（前置 count=3）| 3 | `count = 4, accum[0] = 0xAA, data[0] = 0x38` → OOB | SIGSEGV/UBSAN |
| `[0xAA,0x01,0x00,0x00]` × 1（前置 count=3）| 3 | `count = 4, accum[0] = 0xAA, data[0] = 0x01` → OOB（56+1=57，在範圍內）| return 4 |
| `[0xAA,0xFF,0x00,0x00]` × 1（前置 count=3）| 3 | `count = 4, accum[0] = 0xAA, data[0] = 0xFF` → OOB（200+255=455，越界）| SIGSEGV |

---

## 延伸挑戰

**挑戰 1：設計 incremental snapshot**

修改 fuzzer，讓它在「找到一個有趣的輸入（產生新 coverage）」後，把當時的進程狀態儲存起來，作為下一輪 fuzzing 的起點。這是 incremental snapshot 的基本概念。

提示：可以用 `fork()` + 父進程等待，讓父進程維護「有趣狀態的集合」，對每個有趣狀態 fork 出子進程繼續 fuzz。

**挑戰 2：實際觀察 fork server 的失效**

在相同 target 上同時跑：
1. 普通 `afl-fuzz`（fork server 模式）
2. 方案 B 的 snapshot fuzzer

記錄兩個 fuzzer 1 分鐘後各自找到什麼 crash，確認普通 fork server 確實找不到 `count>=3` 的 crash。

**挑戰 3：為一個真實的有副作用 target 設計 harness**

選一個真實的帶副作用 target（比如 SQLite 的多次查詢、Redis 的多次命令序列），設計 snapshot harness，讓 fuzzer 能探索「連續多次操作」的空間。

---

## 自我檢核

- [ ] 能解釋為什麼普通 fork server 找不到 `count >= 3` 的 bug？
- [ ] LibAFL 的 `InProcessForkExecutor` 和 `InProcessExecutor` 在 snapshot 語意上有什麼差異？
- [ ] CRIU checkpoint 和 LibAFL fork snapshot 各自的適用場景是什麼？
- [ ] Incremental snapshot 能解決 full snapshot 打不到的哪類問題？
- [ ] 如果 target 的副作用是「寫入 kernel-side 資料結構」，只有哪種 snapshot 能 reset？
