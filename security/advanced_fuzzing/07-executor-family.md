# Ch 7: Executor 家族 (工具章 + 深挖)

> **目標**: 理解 LibAFL 四大 Executor（InProcess / Forkserver / Qemu / Frida）的工作原理、速度與隔離取捨，能依目標選對 Executor 並正確連接 Observer、SHM、feedback。
>
> **環境**:
> - LibAFL 0.15.4（`libafl` + `libafl_bolts` + `libafl_cc` for forkserver）
> - afl++（`apt install afl++`，提供 `afl-clang-fast`、`afl-cc`）
> - WSL2 Ubuntu 22.04+
> - QemuExecutor：`libafl_qemu` crate（需額外 build，本章標記理論段）
> - FridaExecutor：`libafl_frida` crate（需額外設定，本章標記理論段）

---

## 為什麼需要 Executor 抽象

Fuzzer 最核心的問題是：**怎麼把一筆 input 送進目標，並得回「跑完了還是崩了」的結果？**

這個問題看起來不難——呼叫函式、啟動子行程、或者用模擬器跑——但每種方案的速度、隔離性、覆蓋率來源都不一樣，而且切換方案時你不想重寫整個 fuzzer。

LibAFL 把這層抽象叫做 **Executor**。Executor 封裝「執行一次目標」的全部細節，對上面的 fuzzer loop 只暴露一個語義：

```
run_target(input) → ExitKind::{Ok, Crash, Timeout}
```

上層的 fuzzer（Stage、Scheduler、Feedback）完全不知道底下用的是直接呼叫、fork、QEMU 還是 Frida。

---

## 先建立直覺

四種 Executor 在「誰跑目標」這個維度上是完全不同的選擇：

```
                 目標是否有原始碼？
                         │
              ┌──────────┴──────────┐
              是                    否
              │                     │
   速度優先？              平台是 Android/iOS？
     ├── 是 → InProcessExecutor        ├── 是 → FridaExecutor
     └── 否 → ForkserverExecutor       └── 否 → QemuExecutor


速度排名（粗估）:
  InProcess  > Forkserver  >>  Qemu  >  Frida
  100k+ /s     1–10k /s      100–1k/s  10–100/s

隔離排名:
  InProcess  <  Forkserver  ≈  Qemu  >  Frida (Frida in-process!)
  最差                                   比你想的更差
```

---

## Executor Trait 的核心契約

LibAFL 的 `Executor` trait 要求實作：

```rust
pub trait Executor<EM, I, S, Z>: UsesState<State = S> {
    fn run_target(
        &mut self,
        fuzzer: &mut Z,
        state: &mut Self::State,
        mgr: &mut EM,
        input: &Self::Input,
    ) -> Result<ExitKind, Error>;
}
```

可選的附加 trait：
- `HasObservers`：Executor 管理一組 Observer，讓 feedback 能讀取覆蓋率等資訊
- `HasTimeout`：Executor 支援 timeout 設定

`ExitKind` 只有三種：
- `Ok`：正常結束
- `Crash`：偵測到崩潰（SIGSEGV、abort、sanitizer abort 等）
- `Timeout`：超出時限

這三個值是 fuzzer 做 triage 和 objective 判斷的依據。

---

## InProcessExecutor

### 工作原理

InProcessExecutor 是最單純的：**在同一個行程內呼叫 harness 函式**。沒有 fork，沒有 spawn，就是 function call。

LibAFL 在這個 Executor 初始化時安裝 signal handler（SIGSEGV、SIGBUS、SIGABRT 等），並在每次 `run_target()` 前設置 `setjmp` 跳回點。如果目標崩潰：

1. Signal handler 觸發
2. `longjmp` 跳回 fuzzer
3. `run_target()` 回傳 `ExitKind::Crash`

Fuzzer 繼續運行，存下這筆 crash input，然後處理下一個。

```
LibAFL main loop
    │
    ▼
run_target()
    │
    ├─ setjmp 設置跳回點
    ├─ pre_exec() on all observers  (e.g., 清零 coverage map)
    │
    ├─ call harness(&input)
    │      └─ 目標程式碼在此執行，填寫 coverage map
    │         [SIGSEGV / SIGBUS 等 handler 已裝載]
    │              ┌── 正常結束 → 繼續
    │              └── 崩潰 → signal handler → longjmp
    │                              │
    │                              ▼
    │                     ExitKind::Crash ──────┐
    │                                            │
    └─ post_exec() on all observers              │
         (讀取 coverage map 數據)                │
    │                                            │
    ▼                                            │
feedback.is_interesting()?  ◀───────────────────┘
```

### 速度與隔離

**速度**：極快。每秒數萬到數十萬次執行，取決於 harness 本身的複雜度。沒有 IPC、沒有 fork syscall、沒有 SHM 讀寫延遲。

**隔離**：**完全沒有**。如果 crash 發生在 signal handler 無法捕捉的情況（例如 heap metadata 損壞到 malloc 內部就爆、或 signal handler 本身 re-enter），整個 fuzzer 行程就掛了。另一個常見問題：目標在全域變數或靜態資料裡留下狀態，下次執行會受上次影響——harness 必須自己清理。

### 建構方式（baby_fuzzer 驗證）

```rust
// harness: 一個 FnMut，接收 input，回傳 ExitKind
let mut harness = |input: &BytesInput| {
    let target = input.target_bytes();
    let buf = target.as_slice();
    // 呼叫你的 library 函式
    unsafe { parse_input(buf.as_ptr(), buf.len()) };
    ExitKind::Ok
};

// Observer 先建好
let observer = unsafe {
    HitcountsMapObserver::new(StdMapObserver::new("edges", &mut EDGES_MAP))
};

let mut executor = InProcessExecutor::new(
    &mut harness,           // FnMut(&BytesInput) -> ExitKind
    tuple_list!(observer),  // observer tuple
    &mut fuzzer,
    &mut state,
    &mut mgr,
)
.expect("Failed to create the Executor");
```

### 關鍵坑：panic 與 abort

InProcessExecutor 能捕捉 signal，但 Rust 的 panic 預設走 stack unwinding。如果 harness 內部 panic，unwinding 可能繞過 signal handler，讓 fuzzer 的內部狀態損壞。

強制 panic = abort：

```toml
# Cargo.toml
[profile.dev]
panic = "abort"

[profile.release]
panic = "abort"
```

這樣 panic 會直接送 SIGABRT，signal handler 能接住，回傳 `ExitKind::Crash`。

---

## ForkserverExecutor

### 工作原理

ForkserverExecutor 仿照 AFL 的 forkserver 協定。核心思路是：

- 目標 binary 用 `afl-clang-fast` 編譯，插入 forkserver stub 和 coverage instrumentation
- 第一次啟動 binary 時，它完成初始化（載入動態庫、全域建構子等），然後進入 **forkserver loop** 等待指令
- 對每一個 input：fuzzer 送「fork」指令 → 目標 fork 一個 child → child 處理 input 後結束 → parent 讀 exit status → fuzzer 讀 SHM 得到 coverage

```
Fuzzer process                    Target (forkserver)
     │                                  │
     │  ──write 4 bytes to ctrl pipe──▶ [__afl_fork_wait_loop]
     │                                  │
     │                           fork() ┤
     │                        parent    │  child
     │  ◀──write status pipe──  [wait]  │  run input
     │                                  │  寫入 SHM (coverage bitmap)
     │                                  │  exit / crash / timeout
     │  read SHM (coverage bits) ◀──────┘
     │  read exit status
     │
     ▼
feedback.is_interesting()?
```

好處是：每次 input 都在新行程執行，crash 不影響 fuzzer；而且 binary 初始化成本只付一次（載入 .so、全域建構子等），fork 比 execve 快得多。

**速度**：中等。每秒 1k–10k 次，取決於目標複雜度和 fork syscall 成本。

**隔離**：好。每個 input 有獨立行程，崩潰只殺 child。

### 實戰：從 C target 到跑起 ForkserverExecutor

以下步驟在有 afl++ 工具的環境下可執行，WSL2 `apt install afl++` 即可。

**Step 1：寫一個有 bug 的 C target**

```c
// target.c
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

void parse(const char *buf, size_t len) {
    char tmp[8];
    if (len >= 4 && memcmp(buf, "FUZZ", 4) == 0) {
        // Bug: 沒有長度檢查，len-4 > 8 時 stack overflow
        memcpy(tmp, buf + 4, len - 4);
    }
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    rewind(f);
    char *buf = malloc(sz + 1);
    fread(buf, 1, sz, f);
    fclose(f);
    parse(buf, sz);
    free(buf);
    return 0;
}
```

**Step 2：用 afl-clang-fast 編譯**

```bash
# -fsanitize=address 讓 ASan 幫你偵測更多錯誤
afl-clang-fast -o target_fuzz target.c -fsanitize=address

# 確認 forkserver stub 已插入
strings target_fuzz | grep -c "afl"   # 應該大於 0
```

**Step 3：在 Rust fuzzer 中建構 ForkserverExecutor**

```rust
use libafl::{
    executors::forkserver::ForkserverExecutor,
    feedbacks::{CrashFeedback, MaxMapFeedback, TimeFeedback},
    inputs::BytesInput,
    observers::{HitcountsMapObserver, StdMapObserver, TimeObserver},
    prelude::*,
};
use libafl_bolts::shmem::{ShMemProvider, UnixShMemProvider};
use std::time::Duration;

// 1. 建立 SHM provider 和 coverage map
let mut shmem_provider = UnixShMemProvider::new().unwrap();
const MAP_SIZE: usize = 65536;  // AFL 預設 bitmap 大小
let mut shmem = shmem_provider.new_shmem(MAP_SIZE).unwrap();

// 關鍵：在 executor 建立前把 SHM ID 寫進環境變數
// child process 會讀這個來找到 shared memory
shmem.write_to_env("__AFL_SHM_ID").unwrap();
let shmem_buf = shmem.as_slice_mut();

// 2. 建立 observers
let edges_observer = unsafe {
    HitcountsMapObserver::new(StdMapObserver::new("edges", shmem_buf))
};
let time_observer = TimeObserver::new("time");

// 3. 建立 feedback（注意：必須在 edges_observer move 前建立）
let mut feedback = feedback_or!(
    MaxMapFeedback::new(&edges_observer),
    TimeFeedback::new(&time_observer)
);
let mut objective = feedback_and_fast!(
    CrashFeedback::new(),
    MaxMapFeedback::new(&edges_observer)
);

// 4. 建立 executor
let mut executor = ForkserverExecutor::builder()
    .program("./target_fuzz")              // afl-cc 插樁過的 binary
    .debug_child(false)
    .shmem_provider(&mut shmem_provider)
    .parse_afl_cmdline(vec!["@@".to_string()])  // @@ 替換為 input 路徑
    .coverage_map_size(MAP_SIZE)
    .timeout(Duration::from_millis(5000))
    .build(tuple_list!(time_observer, edges_observer))
    .unwrap();
```

`@@` 是 AFL 的約定：fuzzer 把當前 input 寫到臨時檔案，把路徑填入 `@@` 位置，再傳給 target。

---

## 底層機制：SHM 和 coverage map 的流動

```
afl-clang-fast 插入的 instrumentation（偽碼）:

void __afl_trace(uint32_t cur_loc) {
    // cur_loc 是編譯期決定的 edge ID
    __afl_area_ptr[cur_loc ^ prev_loc >> 1]++;
    prev_loc = cur_loc >> 1;
}

// __afl_area_ptr 指向 MAP_SIZE bytes 的 shared memory
// fuzzer 和 child 共享這塊記憶體
```

```
每次 run_target() 的資料流:

fuzzer                    SHM (65536 bytes)          child
  │                            │                       │
  │  清零 SHM ─────────────── map = {0}               │
  │                            │                       │
  │  ─────────── fork ─────────────────────────────▶  │
  │                            │          執行 input   │
  │                            │  ◀── 填寫 edge bits ─ │
  │                            │          exit/crash   │
  │  讀取 SHM ─────────────── map = {邊覆蓋計數}       │
  │  HitcountsMapObserver                              │
  │  MaxMapFeedback                                    │
  │  決定 interesting?                                 │
```

---

## QemuExecutor

**本段未實測，為理論預期行為。** 需要 `libafl_qemu` crate，該 crate 依賴完整 QEMU build，編譯時間長，且需要 Linux 環境。

驗證步驟：
```bash
# 1. Clone LibAFL
git clone https://github.com/AFLplusplus/LibAFL
cd LibAFL
# 2. 查看 QEMU fuzzer 範例
ls fuzzers/binary_only/
# 3. 按照範例的 README 安裝 QEMU 依賴
#    通常需要: python3, ninja, flex, bison 等
# 4. cargo build（時間很長，30–60 分鐘）並執行範例確認
```

### 工作原理

QemuExecutor 使用 QEMU user-mode emulation 執行目標 binary。QEMU 在翻譯 guest binary 的 Translation Block（TB）時，LibAFL 注入 hook，每遇到新的基本塊就更新 coverage map。

```
Guest binary (e.g., ARM ELF)
    │
    ▼
QEMU 翻譯 TB（基本塊）
    │  LibAFL hook 注入:
    │     coverage_map[tb_hash ^ prev_tb]++
    ▼
在 x86_64 host 上執行翻譯後的機器碼
    │
    ▼
ExitKind（正常 / 崩潰 / timeout）
```

**速度**：慢。翻譯開銷 + JIT 暖機，大約比 InProcessExecutor 慢 10–100 倍，落在每秒 100–1000 次。

**使用場景**：
- 閉源 binary（沒有原始碼，不能重新編譯）
- 嵌入式韌體（ARM、MIPS binary，需要跨架構執行）
- 需要 syscall hooking 但不想 root 的情境

---

## FridaExecutor

**本段未實測，為理論預期行為。** 需要 `libafl_frida` crate 和對應 Frida gadget 設定。

### 工作原理

FridaExecutor 使用 Frida 動態插樁框架。Frida 的 **Stalker** 引擎在執行期追蹤每一個基本塊的執行，LibAFL 的 frida 整合在 Stalker callback 中更新 coverage map。

```
目標行程（in-process）
    │
Frida Stalker 接管執行流
    │   每個 basic block 執行前:
    │       coverage_map[bb_hash ^ prev_bb]++
    ▼
實際執行目標指令
    │
如果目標崩潰 → 整個 Frida session 結束
（不像 ForkserverExecutor，沒有行程隔離）
```

**速度**：通常比 QEMU 還慢（Stalker 開銷大），每秒 10–100 次。

**使用場景**：
- macOS / iOS 上的閉源目標（Frida 對 Apple 生態支援最好）
- Android app fuzzing
- 目標使用了複雜 syscall 或核心機制，QEMU user-mode 無法正確模擬的情況

### 重要提醒

Frida **不是**沙箱 VM。它注入到目標行程內部執行。目標如果 hard crash（例如 SIGSEGV 被 Frida 接管前就觸發），整個 Frida session 跟著死。要達到 crash 安全，必須在 Frida runner 外層再包一層行程隔離（例如用 fork 包住每次 Frida 執行）。

---

## 四種 Executor 比較

| Executor | 速度 | 隔離 | 需原始碼 | 平台限制 | 典型用途 |
|---|---|---|---|---|---|
| InProcessExecutor | 最快（100k+/s） | 無 | 是（harness） | 無 | library fuzzing、libfuzzer 替代 |
| ForkserverExecutor | 中（1–10k/s） | 好（獨立 process） | 需 afl-cc 插樁 | Linux/macOS | CLI 工具、伺服器、afl 相容目標 |
| QemuExecutor | 慢（100–1k/s） | 好 | 否（閉源 binary） | Linux（user-mode QEMU） | 嵌入式 binary、跨架構 |
| FridaExecutor | 最慢（10–100/s） | 中（in-process Frida） | 否 | Android/iOS/macOS 主力 | mobile app、Apple 生態 |

---

## 常見坑

### 坑 1：InProcessExecutor + panic unwinding

只要 harness 裡有 panic 路徑（包括 Rust 標準庫裡的 `unwrap()`、邊界檢查等），預設 unwinding 模式下 panic 不會走 signal handler。Fuzzer 可能因為未捕捉的 panic 直接崩潰，或者更壞的情況：fuzzer 內部狀態被 partial unwinding 搞亂、繼續跑但結果不可信。

修法：`Cargo.toml` 的 dev 和 release profile 都設 `panic = "abort"`。

### 坑 2：ForkserverExecutor 的 SHM 時序

`shmem.write_to_env("__AFL_SHM_ID")` **必須在 executor 建立前呼叫**。原因：executor 建立時會啟動 target binary 進入 forkserver loop，child process 在那個時間點讀環境變數找 SHM。如果你把 `write_to_env` 放在 executor 建立之後，child 早已啟動，找不到 `__AFL_SHM_ID`，forkserver 初始化失敗，整個 build 過程不會報錯但執行期馬上炸。

### 坑 3：Observer 的 ownership 順序

ForkserverExecutor 的 builder 呼叫 `.build(tuple_list!(time_observer, edges_observer))` 時，observer 被 **move** 進 executor。但 `MaxMapFeedback::new(&edges_observer)` 是借用 observer（by name reference，實際用 observer 的名字字串查找，但型別系統仍需要 observer 存活）。

建構順序必須是：

```
1. 建立 edges_observer
2. 用 edges_observer 建立 feedback（借用關係確立）
3. move edges_observer 進 executor
```

如果先 move 再建 feedback，編譯就會失敗。LibAFL 範例裡的順序是刻意的，不要隨意調換。

### 坑 4：以為 Frida 有行程隔離

看到 FridaExecutor 能 fuzz 閉源 binary，直覺上以為跟 ForkserverExecutor 一樣安全。實際上 Frida 注入到目標行程內部，一旦目標 hard crash，Frida 和你的 fuzzer logic 都在同一個行程裡，session 直接死掉。這和 InProcessExecutor 的情況類似，只是多了 Frida 的那層間接。需要 crash 安全的 Frida fuzzing，必須自己在外層包 fork 或重啟機制。

---

## 動手練習

目標：用 ForkserverExecutor 跑起來，讓 fuzzer 找到 `target.c` 裡的 stack overflow。

1. 安裝環境：
   ```bash
   sudo apt install afl++
   ```

2. 編譯目標（使用上面的 target.c）：
   ```bash
   afl-clang-fast -o target_fuzz target.c -fsanitize=address
   ```

3. 建立初始 corpus：
   ```bash
   mkdir corpus
   echo "AAAA" > corpus/seed1
   echo "FUZZ" > corpus/seed2
   ```

4. 建立 LibAFL 0.15.4 的 Rust fuzzer（參考 `forkserver_simple` 範例），設定 `program("./target_fuzz")`、`parse_afl_cmdline(vec!["@@"])`、timeout 5 秒。

5. `cargo run --release` 跑起 fuzzer，觀察：
   - LibAFL 輸出的 corpus 數量是否在增加
   - 多久後出現第一個 crash（含 "FUZZ" + 超過 8 bytes 的 payload 應很快出現）
   - crash 的 input 檔案內容是什麼

6. 進階：把 `ForkserverExecutor` 換成 `InProcessExecutor`，寫一個直接呼叫 `parse()` 的 harness，比較兩者在相同機器上的每秒執行次數（executions/sec）。

---

## 章節總結

- **InProcessExecutor**：最快，無隔離，適合你能控制的 library target；panic 必須設成 abort。
- **ForkserverExecutor**：中速，每 input 獨立行程，需要 afl-cc 插樁；SHM 時序和 observer ownership 是常見陷阱。
- **QemuExecutor**：慢但能跑閉源 binary 和跨架構；build 複雜，真跑前要確認依賴。
- **FridaExecutor**：彈性最高（Apple 生態、Android），但 Stalker 開銷大，且沒有行程隔離，crash 安全需要額外處理。

選 Executor 的核心邏輯：先問有無原始碼，再問速度要求，最後看目標平台。

---

## 自我檢核

- [ ] 我能說出 `ExitKind` 三個值分別代表什麼情況
- [ ] 我知道 InProcessExecutor 為什麼要設 `panic = "abort"`
- [ ] 我能解釋 forkserver 協定的兩個 pipe 各傳什麼
- [ ] 我知道 `shmem.write_to_env()` 必須在 executor 建立前呼叫的原因
- [ ] 我能解釋為什麼 FridaExecutor 不提供行程隔離

---

## 延伸閱讀

1. **LibAFL `forkserver_simple` 原始碼**（GitHub: AFLplusplus/LibAFL，`fuzzers/forkserver_simple/`）——0.15.x ForkserverExecutor 最完整的建構範例，所有欄位的使用方式以此為準。

2. **AFL technical whitepaper**（Michal Zalewski，`lcamtuf.coredump.cx/afl/technical_details.txt`）——LibAFL forkserver 協定直接繼承自 AFL 的設計。看原始文件理解為什麼用兩個 pipe、為什麼用 SHM 而不是 pipe 傳 coverage，比只讀 LibAFL 文件更扎實。

3. **LibAFL Book: QEMU mode**（`aflplus.plus/libafl-book/`）——QemuExecutor 和 FridaExecutor 的官方教材，含 dependency setup、build 步驟、和 InProcessExecutor 的架構對比。配合 `fuzzers/binary_only/` 下的範例一起看。

---

四種 Executor 各解一類問題。選好 Executor，接下來要問的是：input 要怎麼變形——下一章看 Mutator 和 Stage 的設計，以及如何為特定目標客製化 mutation 策略。

→ [下一章](./08-mutator-stage.md)
