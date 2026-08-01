# Ch 36 — Fuzzing Rust：cargo-fuzz/AFL++

> **目標**：把 coverage-guided fuzzing 這套你在 C/C++ 二進位裡用過的技術，移植到 Rust 的工具鏈。學完你能：（1）用 `cargo-fuzz`（libFuzzer backend）對 Rust library 跑 coverage-guided fuzzing；（2）讀懂 libFuzzer 啟動輸出、找到 crash artifact 並重現；（3）理解 safe Rust fuzz 的目標和 C 有何本質差異（找 panic 而非記憶體 UB）；（4）在 unsafe 區塊搭配 ASan 找真正的記憶體漏洞；（5）懂 `cargo-afl`（AFL++ binding）和 cargo-fuzz 的取捨；（6）寫出有效的 fuzz target、避免常見的 target 設計錯誤。

> **環境**：`rustc 1.97.1`（stable + nightly），`cargo-fuzz 0.13.2`，x86-64 Linux（WSL2）。本章 `cargo-fuzz` 段落全部本機真跑；AFL++ binding（`cargo-afl`）部分標「流程說明，未本機實測 AFL++」。

## 為什麼要 fuzz Rust？

C/C++ 的 fuzzing 目標是記憶體 corruption：heap overflow、UAF、OOB write——找到就有機會拿 exploitable crash、往後寫 exploit。safe Rust 把這類 UB 消掉了，乍看之下 fuzz 失去了最重要的目標。

這個直覺是錯的。目標改變了，不是消失了：

```
   C/C++：找 memory corruption ─── exploitable crash
   safe Rust：找 panic (DoS)、邏輯 bug、unwrap/expect 失敗 ─── crash/DoS
   Rust unsafe 區塊：和 C 一樣，能找記憶體漏洞
```

`panic!`、`.unwrap()`、`.expect()`、越界索引（`v[i]`）——在 safe Rust 裡這些都產生 panic 而非 UB，但 panic 在很多場景是嚴重的漏洞：

- **解析器**（網路協定、設定格式、binary format）：攻擊者送一個畸形封包 → server 執行緒 panic → connection reset 甚至整個 process crash。
- **CLI 工具**：輸入一個邊緣案例 → crash → 被計入 DoS 攻擊向量。
- **WebAssembly 模組**：panic 在 wasm32 環境通常 abort，影響宿主程式。

在這些場景，「找到能讓程式 panic 的輸入」是 severity-medium 的漏洞報告，直接能上 bug bounty。

再說一個角落：**整數溢位**。Rust debug mode 下整數溢位會 panic（可由 fuzzer 觸發）；release mode 下是 wrapping arithmetic（不 panic，但可能是邏輯 bug，讓 fuzzer 難以察覺）。這個不對稱性是踩雷點，後面細說。

Rust unsafe 區塊的 fuzz 目標則和 C 完全一樣——加上 ASan（AddressSanitizer），能找 OOB、UAF、double-free。這和 [Ch 31](./31-unsafe-vuln-classes.md) 講的那四類漏洞直接對應；fuzz 是把「這段 unsafe 是否 sound」從人工推理變成「讓機器試幾百萬種輸入」的自動化驗證。

## 先建立直覺

Coverage-guided fuzzing 的核心迴圈，和 AFL/AFL++ 你可能已經接觸過的是同一套：

```
   corpus（初始輸入集）
         │
         ▼
   mutate input（flip bits, splice, add bytes...）
         │
         ▼
   run target with instrumented binary
         │
         ├── crash？ → 記錄為 artifact，繼續
         │
         ▼
   check coverage（有沒有走到新的 edge？）
         │
         ├── 有新 edge → 把這個輸入加進 corpus → 繼續 mutate
         │
         └── 無新 edge → 丟棄這個輸入，繼續 mutate
```

instrumentation 在 Rust 這邊由 LLVM 的 SanitizerCoverage（`-Zsanitizer=address` 系列的 coverage 部分）做；libFuzzer 是 in-process 的 fuzzer，和你的 target 跑在同一個 process 裡，靠回呼（`fuzz_target!` macro）驅動。相比之下 AFL++ 是 out-of-process（fork-based 或 persistent mode），各有優缺點（後面比較）。

不管用哪個引擎，關鍵心法都是：**corpus 品質決定 fuzzer 速度**。從空 corpus 開始跑，fuzzer 要花大量時間從隨機輸入摸索進去；給幾個有代表性的合法輸入當 seed，覆蓋率立刻起跳。

## `cargo-fuzz` 基本流程

### 安裝

```
cargo install cargo-fuzz         # cargo-fuzz 0.13.2
rustup toolchain install nightly # cargo-fuzz 內部需要 nightly
```

`cargo-fuzz` 依賴 nightly 是因為它要用 `-Zsanitizer=...` 系列的 unstable flag，以及 libfuzzer-sys 的連結方式需要 nightly 特性。stable toolchain 本身可以存在，執行時用 `cargo +nightly` 前綴。

### 初始化與建立 fuzz target

在你的 library crate 根目錄（有 `Cargo.toml` 的那層）執行：

```
cargo fuzz init
cargo fuzz add fuzz_target_1
```

這會在專案下建立 `fuzz/` 子目錄：

```
fuzz/
├── Cargo.toml          # fuzz 專用的 workspace member
└── fuzz_targets/
    └── fuzz_target_1.rs
```

fuzz target 的骨架：

```rust
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    // data 是 libFuzzer 產生/mutate 的 bytes
    // 把 bytes 轉成你的型別，呼叫你想測試的函式
    // 不要 return，讓 panic 自然傳播給 libFuzzer
});
```

`fuzz_target!` 的 `|data: &[u8]|` 簽名是固定的起點。`data` 是 libFuzzer 每次傳進來的原始 bytes，你要負責把它轉成 target 函式的輸入型別。最常見的第一步是 `std::str::from_utf8(data)` 把 bytes 過濾成合法 UTF-8。

`#![no_main]` 是必要的：這個二進位不走標準的 `main()` 入口，libFuzzer 自己掌控主迴圈。

### 執行

```
# 基本執行（一直跑到找到 crash 或手動中斷）
cargo +nightly fuzz run fuzz_target_1

# 限時 60 秒（適合 CI smoke test）
cargo +nightly fuzz run fuzz_target_1 -- -max_total_time=60

# 平行 4 個 job（吃滿 CPU）
cargo +nightly fuzz run fuzz_target_1 -- -jobs=4

# 限制輸入最大長度（預設 4096 bytes，解析器通常設小一點更有效率）
cargo +nightly fuzz run fuzz_target_1 -- -max_len=1024
```

`--` 後面的 flag 直接傳給 libFuzzer，不是 cargo 的 flag。

## 真實案例：fuzz `parse_pair` 到兩個 panic

### 被測 library

建一個刻意埋了兩個 panic bug 的 library：

```rust
// src/lib.rs

/// Parse "key=value" 字串，回傳 (key, value)。
/// BUG 1: 如果輸入沒有 '='，expect 在 line 4 panic。
/// BUG 2: 如果 value 部分是空字串（例如 "foo="），assert 在 line 8 panic。
pub fn parse_pair(s: &str) -> (&str, &str) {
    let idx = s.find('=').expect("no = found");   // line 4: BUG 1
    let key = &s[..idx];
    let val = &s[idx + 1..];
    assert!(!val.is_empty(), "value must not be empty");  // line 8: BUG 2
    (key, val)
}
```

這個 library 是故意設計成有兩個不同路徑的 panic，讓我們驗證 fuzzer 能不能分別找到它們。

### Fuzz target

```rust
// fuzz/fuzz_targets/fuzz_target_1.rs
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if let Ok(s) = std::str::from_utf8(data) {
        // 先過濾非 UTF-8 輸入，讓 fuzzer 把精力放在合法字串上
        let _ = fuzz_target_lib::parse_pair(s);
    }
});
```

注意這裡沒有 `catch_unwind`——讓 panic 直接傳播給 libFuzzer。這是故意的，原因後面說。

### cargo fuzz run 真實輸出

```
$ cargo +nightly fuzz run fuzz_target_1
   Compiling fuzz_target_lib v0.1.0 (/tmp/fuzz_demo/fuzz_target_lib)
   Compiling libfuzzer-sys v0.4.13
   Compiling fuzz_target_lib-fuzz v0.0.0 (.../fuzz)
    Finished `release` profile [optimized + debuginfo] target(s) in 7.51s
     Running `fuzz/target/x86_64-unknown-linux-gnu/release/fuzz_target_1 ...`
INFO: Running with entropic power schedule (0xFF, 100).
INFO: Seed: 1362353097
INFO: Loaded 1 modules   (388 inline 8-bit counters): 388 [0x630cd00dced0, 0x630cd00dd054),
INFO: Loaded 1 PC tables (388 PCs): 388 [0x630cd00dd058,0x630cd00de898),
INFO:        0 files found in /tmp/.../fuzz/corpus/fuzz_target_1
INFO: -max_len is not provided; libFuzzer will not generate inputs larger than 4096 bytes

thread '<unnamed>' (298513) panicked at /tmp/fuzz_demo/fuzz_target_lib/src/lib.rs:4:27:
no = found
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
==298513== ERROR: libFuzzer: deadly signal
    #0 0x630cd00200a1  (/tmp/.../fuzz_target_1+0xf10a1)
    #1 0x630cd00914be  (/tmp/.../fuzz_target_1+0x1624be)
    ...（更多 stack frames）
SUMMARY: libFuzzer: deadly signal
MS: 0 ; base unit: 0000000000000000000000000000000000000000

artifact_prefix='.../fuzz/artifacts/fuzz_target_1/';
Test unit written to .../crash-da39a3ee5e6b4b0d3255bfef95601890afd80709
```

逐行解讀這份輸出——這些資訊都有實際意義：

- **`entropic power schedule (0xFF, 100)`**：libFuzzer 用 entropic scheduling 決定哪些 corpus item 要多 mutate。這是預設策略，通常不用改。
- **`Seed: 1362353097`**：這個 run 的隨機種子。記下它可以重現完全相同的 mutation 序列——偵錯時有用。
- **`Loaded 1 modules (388 inline 8-bit counters)`**：instrumentation 注入了 388 個 coverage counter，對應 388 個可能的 edge。你的 library 越大，counter 越多。
- **`Loaded 1 PC tables (388 PCs)`**：PC table 用來記錄每個 counter 對應的程式位址，讓 libFuzzer 知道新 edge 在哪。
- **`0 files found in corpus`**：從空 corpus 開始。這就是為什麼 seed corpus 重要——空 corpus 時 libFuzzer 從零開始摸索。
- **`-max_len is not provided; libFuzzer will not generate inputs larger than 4096 bytes`**：預設最大輸入長度 4096 bytes，可以用 `-max_len=N` 調整。對字串解析器，設小一點通常跑更快。
- **`panicked at lib.rs:4:27: no = found`**：這就是 BUG 1，空輸入走進了 `expect("no = found")` 的那條路。
- **`ERROR: libFuzzer: deadly signal`**：libFuzzer 攔截到 SIGABRT（Rust 的 panic 在 libFuzzer 下最終觸發 abort），判定為 crash。
- **`MS: 0 ; base unit: 000...000`**：`MS: 0` 表示 mutation steps = 0，也就是「從未 mutate」的初始狀態（空輸入）就觸發了 crash。`base unit: 000...000` 是 SHA1 全零，表示 corpus 裡第零個輸入（空）。
- **`Test unit written to .../crash-da39a3ee5e6b4b0d3255bfef95601890afd80709`**：crash artifact 寫到這個路徑。

### 解讀 crash artifact

artifact 的檔名 `crash-da39a3ee5e6b4b0d3255bfef95601890afd80709` 裡的 hash 是觸發 crash 的輸入 bytes 的 SHA1。

`da39a3ee5e6b4b0d3255bfef95601890afd80709` 是**空字串的 SHA1**——這告訴你 crash input 就是長度為零的 byte slice。

追一下執行路徑：

```
空 bytes [] 
  → std::str::from_utf8(&[]) = Ok("")   # 空字串是合法 UTF-8
  → parse_pair("")
  → "".find('=') = None
  → None.expect("no = found") → panic!
```

這是最快能找到的 crash：fuzzer 還沒開始 mutate，第一次跑就拿空輸入進去，直接觸發。

### 重現 crash

找到 crash artifact 後，可以重現並加 backtrace：

```
$ cargo fuzz run fuzz_target_1 \
    fuzz/artifacts/fuzz_target_1/crash-da39a3ee5e6b4b0d3255bfef95601890afd80709
     Running `fuzz/target/.../release/fuzz_target_1 ... crash-da39...`
Running: fuzz/artifacts/.../crash-da39...
thread '<unnamed>' (298613) panicked at /tmp/.../src/lib.rs:4:27:
no = found
SUMMARY: libFuzzer: deadly signal
```

直接把 artifact 路徑傳給 `cargo fuzz run` 的第二個參數，它就只跑那一個輸入，方便 debug。

加 backtrace：

```
RUST_BACKTRACE=1 cargo fuzz run fuzz_target_1 <artifact-path>
```

### Minimize crash input

找到 crash 但輸入很長、很難讀懂時，用 `tmin` 把它縮到最小仍能觸發 crash 的版本：

```
cargo fuzz tmin fuzz_target_1 <artifact-path>
```

`tmin` 會反覆嘗試「縮短輸入」，每次確認縮短後的版本還是會 crash，最終給你最短的觸發輸入。對於長達幾百 bytes 的 crash input，minimize 後通常能縮到幾個 byte，直接看出問題所在。

### Seed corpus 找到 BUG 2

第一個 bug（BUG 1，no `=`）空輸入就撞到了。BUG 2（`assert!(!val.is_empty())`）需要輸入有 `=` 但 `=` 後面是空字串，例如 `"key="`。

從空 corpus 跑，libFuzzer 最終也會找到，但不一定快。手動放一個 seed 進去：

```
# 把 "key=" 作為 seed corpus（注意 -n 避免 echo 加換行）
mkdir -p fuzz/corpus/fuzz_target_1
echo -n "key=" > fuzz/corpus/fuzz_target_1/seed_empty_val

# 跑 30 秒（CI 用，或快速驗證）
cargo +nightly fuzz run fuzz_target_1 -- -max_total_time=30
```

有了 `"key="` 這個 seed，libFuzzer 第一次就走到 BUG 2 的路徑（`assert!(!val.is_empty())`），立刻產生第二個 crash artifact：

```
thread '<unnamed>' panicked at /tmp/.../src/lib.rs:8:5:
value must not be empty
SUMMARY: libFuzzer: deadly signal
Test unit written to .../crash-<hash2>
```

這就是 seed corpus 的價值：你用領域知識告訴 fuzzer「這種形狀的輸入值得深入探索」，它的 mutation 才能有效地找到藏在角落的 bug。

## Fuzz target 的寫法準則

### 不要 `catch_unwind`

最常見的錯誤是這樣寫：

```rust
// 錯：libFuzzer 看不到 crash
fuzz_target!(|data: &[u8]| {
    let _ = std::panic::catch_unwind(|| {
        let _ = parse_pair(std::str::from_utf8(data).unwrap_or(""));
    });
    // catch_unwind 吃掉了 panic，fuzzer 以為什麼都沒發生
});
```

`catch_unwind` 把 panic 攔截成 `Err`，libFuzzer 看到 target 正常返回，不記錄 crash，BUG 1 和 BUG 2 永遠找不到。

正確做法：讓 panic 傳播，libFuzzer 的 signal handler 會攔截到 SIGABRT，記錄 crash artifact：

```rust
// 正確：panic 自然傳播給 libFuzzer
fuzz_target!(|data: &[u8]| {
    if let Ok(s) = std::str::from_utf8(data) {
        let _ = parse_pair(s);
    }
});
```

### 過濾無效輸入，不要 panic 在格式轉換上

如果你的函式只接受 UTF-8，用 `if let Ok(s) = std::str::from_utf8(data)` 而非 `.unwrap()`：

```rust
// 錯：會在格式轉換就 panic，fuzzer 把時間全花在「無效 UTF-8」上
fuzz_target!(|data: &[u8]| {
    let s = std::str::from_utf8(data).unwrap(); // 大量 crash，全是 UTF-8 格式問題
    let _ = parse_pair(s);
});

// 正確：過濾掉無效 UTF-8，讓 fuzzer 聚焦在真正的業務邏輯
fuzz_target!(|data: &[u8]| {
    if let Ok(s) = std::str::from_utf8(data) {
        let _ = parse_pair(s);
    }
});
```

### 避免 `eprintln!` 在 fuzz target 裡

libFuzzer 每秒可能跑幾千次 target，每次都 `eprintln!` 會淹沒 fuzzer 的狀態輸出，也大幅拖慢速度。debug 輸出應該用環境變數 guard 或在 fuzz session 結束後從 artifact 重現時才印。

### `arbitrary` crate：結構化輸入

`data: &[u8]` 適合字串/二進位解析器。但如果你要 fuzz 一個接受結構化型別（struct、enum）的函式，手動從 bytes 裁出各個欄位很繁瑣，而且 fuzzer 大量產生的輸入大多格式不對，進不了真正的業務邏輯。

`arbitrary` crate 解決這個問題：它定義 `Arbitrary` trait，讓型別能從隨機 bytes 合理地建構出自己：

```rust
// Cargo.toml 加 arbitrary = { version = "1", features = ["derive"] }
// libfuzzer-sys 加 arbitrary = "1" feature

use arbitrary::Arbitrary;

#[derive(Arbitrary, Debug)]
struct Config {
    key: String,
    value: Option<String>,
    ttl: u32,
}

fuzz_target!(|cfg: Config| {
    // cfg 是從 data bytes 派生出來的合法 Config 實例
    // fuzzer 現在在 Config 的空間裡 mutate，不是在原始 bytes 裡
    let _ = process_config(&cfg);
});
```

`fuzz_target!` macro 支援任何實作 `Arbitrary` 的型別——不一定要是 `&[u8]`。讓 fuzzer 在「有意義的輸入空間」裡探索，效率比在原始 bytes 裡盲目 mutate 高很多。

## `cargo-afl`（AFL++ Rust binding）

（以下為流程說明，未本機實測 AFL++。）

`cargo-afl` 是 AFL++ 的 Rust binding，讓你用 AFL++ 的 fuzzer 引擎跑 Rust target：

```
# 安裝
cargo install cargo-afl

# 建 fuzz binary（會用 AFL++ instrumentation 編譯）
cargo afl build

# 跑（需要本機有安裝 AFL++）
cargo afl fuzz -i corpus -o findings ./target/debug/<your-bin>
```

fuzz binary 的入口點要呼叫 `afl::fuzz!` macro：

```rust
// AFL++ 版的 fuzz entry point（和 cargo-fuzz 的 fuzz_target! 對應）
fn main() {
    afl::fuzz!(|data: &[u8]| {
        if let Ok(s) = std::str::from_utf8(data) {
            let _ = parse_pair(s);
        }
    });
}
```

### cargo-fuzz vs cargo-afl 選哪個

```
   cargo-fuzz（libFuzzer in-process）
   ├── 不需要外部安裝 AFL++
   ├── 直接整合 cargo 生態，一行指令啟動
   ├── nightly required（nightly 的 sanitizer/coverage flag）
   ├── 搭配 ASan/MSan 最方便（-Zsanitizer=address 一個 flag）
   └── 適合：快速實驗、library fuzzing、CI smoke test

   cargo-afl（AFL++ out-of-process）
   ├── 不需要 nightly（stable toolchain 可用）
   ├── AFL++ 的 cmplog、laf-intel 等加速技術（對複雜比較很有效）
   ├── fork-based 或 persistent mode，更容易 fuzz binary-level target
   ├── 需要本機安裝 AFL++
   └── 適合：長期 fuzz campaign、需要 AFL++ 特定模式、跨語言 target
```

如果你對 AFL++ 的 cmplog 或 laf-intel（把複雜的 `cmp` 拆成逐 byte 比較，讓 fuzzer 更容易突破「magic number」的條件判斷）有需求，用 `cargo-afl`。大多數情況下，`cargo-fuzz` 開箱即用的整合度更好。

## Sanitizer 整合（unsafe Rust）

Safe Rust 不需要 ASan——safe code 沒有 UB，ASan 不會抓到任何東西（除非有 bug 在 unsafe 裡）。但 unsafe 區塊和 FFI 呼叫需要：

```
# 搭配 ASan 跑（抓 OOB、UAF、double-free）
RUSTFLAGS="-Zsanitizer=address" \
  cargo +nightly fuzz run fuzz_target_1
```

這會把 ASan 的 instrumentation 注入進去，heap overflow/UAF 會觸發 ASan 的報告而非 Rust 的 panic。對 [Ch 31](./31-unsafe-vuln-classes.md) 那四類 unsound unsafe（任意讀/寫、UAF、double-free、data race），fuzz + ASan 是「有沒有真的 exploitable」的自動化驗證。

同樣可以加 MemorySanitizer（MSan，抓 use-of-uninitialized-memory）或 UBSan（抓 C-level UB，對呼叫 C FFI 的 unsafe 有用）：

```
RUSTFLAGS="-Zsanitizer=memory"    cargo +nightly fuzz run fuzz_target_1
RUSTFLAGS="-Zsanitizer=undefined" cargo +nightly fuzz run fuzz_target_1
```

搭配 [Ch 32](./32-audit-unsafe.md) 的流程：`cargo geiger` 找出 unsafe 多的 crate → 人工 review 判 unsound → fuzz + ASan 驗證能不能真的觸發記憶體漏洞 → 如果能觸發，連同 crash artifact 一起提交 bug report。

## 把 fuzzing 放進 CI

每次 PR 限時跑幾十秒的 fuzz 是「smoke test」等級——不保證找到所有 bug，但能抓到明顯的、從空 corpus 幾秒就撞到的 crash（像上面的 BUG 1）。

GitHub Actions 範例（概念示意，未在 CI 環境實測，但每條指令本機驗證過）：

```yaml
# .github/workflows/fuzz.yml
name: fuzz-smoke
on: [push, pull_request]
jobs:
  fuzz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@nightly
      - name: Install cargo-fuzz
        run: cargo install cargo-fuzz
      - name: Fuzz smoke test (30s)
        run: cargo +nightly fuzz run fuzz_target_1 -- -max_total_time=30
```

幾個 CI 策略考量：

- **`-max_total_time=30`**：PR 限 30 秒；長期 fuzz 排程 job 可以跑幾小時，用 GitHub Actions scheduled trigger。
- **Corpus 持久化**：把 corpus artifact 存在 GitHub Actions Cache 或 S3，每次 CI 載入上次的 corpus，fuzzer 不用從頭摸索。
- **OSS-Fuzz**：Google 提供免費的 fuzzing infrastructure 給開源 Rust 專案。你的 library 如果是開源的，投件 OSS-Fuzz 可以讓 Google 的 fleet 24/7 幫你跑——比自己維護 CI fuzz 便宜得多。[oss-fuzz.com](https://google.github.io/oss-fuzz/) 有申請文件，支援 Rust（需要提供符合 OSS-Fuzz 規格的 fuzz target）。

## 踩雷集錦

**1. 以為 safe Rust fuzz 沒有意義**

「Safe Rust 沒有 UB，fuzz 到什麼？」——panic 就是目標。在 server/解析器場景，能讓服務崩潰的 panic 是 severity-medium 的安全漏洞，直接能上 CVE。不要因為「沒有記憶體 corruption」就跳過 fuzz。

**2. 在 fuzz target 裡用 `catch_unwind` 把 panic 全吃掉**

`catch_unwind` 讓 libFuzzer 永遠看不到 crash，fuzz 就算跑一個小時也不會回報任何 bug。正確做法是讓 panic 自然傳播——libFuzzer 的 signal handler 會攔截，記錄 crash artifact，這才是 fuzz 的產出。唯一例外是「你想區分『預期 panic』和『非預期 panic』」，這種情況可以對預期的錯誤路徑用 `Result` 回傳，只讓非預期的 panic 傳播。

**3. 只在 debug mode 下 fuzz**

`cargo +nightly fuzz run` 預設是 release profile（`Cargo.toml` 的 `[profile.release]` 生效）。手動切到 debug mode fuzz 有一個副作用：debug mode 下整數溢位 panic，release mode 下 wrapping。如果你只在 debug 跑，fuzzer 會記錄大量「整數溢位 panic」的 crash，但這些在 release binary（你實際 ship 的版本）裡不會 panic——而是安靜地 wrapping，可能造成邏輯 bug，但更難被 fuzzer 發現。建議：在 release mode（cargo-fuzz 預設）fuzz，另外用 `RUSTFLAGS="-C overflow-checks=on"` 在 release 也打開溢位檢查。

**4. Corpus 從空開始，然後抱怨 fuzzer 太慢**

空 corpus 下 libFuzzer 從隨機 bytes 開始摸索，要花很長時間才能走到有意義的業務邏輯路徑。放三到五個合法輸入當 seed，覆蓋率（新 edge 數量）立刻進展快幾倍。領域知識轉化成 corpus——這是你比純機器有優勢的地方。

**5. 從 crash artifact 的檔名看不出問題在哪**

`crash-da39a3ee5e6b4b0d3255bfef95601890afd80709` 這個 hash 不是亂數——它是輸入 bytes 的 SHA1。認識常見 hash 能快速診斷：`da39...` 是空字串的 SHA1，一看就知道是空輸入觸發的 crash；對長輸入的 crash，先跑 `cargo fuzz tmin` 把輸入縮短，再看最小觸發輸入的內容，通常幾個 byte 就能看出邊緣條件在哪。

## 進階：再往深一層

**結構化 fuzzing**：`arbitrary` + `#[derive(Arbitrary)]` 讓 fuzzer 在有意義的型別空間裡探索，比 `&[u8]` 原始 bytes 效率高很多，尤其適合「解析的輸入有複雜結構」的場景（例如 AST、packet）。可以和 `proptest` crate 結合使用——proptest 是 property-based testing，`arbitrary` 讓兩者共用型別的生成邏輯。

**Coverage 報告**：`cargo llvm-cov` 搭配 fuzz corpus 可以跑出 line-by-line coverage 報告，看 fuzzer 實際走到哪些路徑、還有哪些分支沒走到（那些就是 corpus 需要補充的地方）。

**Miri + fuzz 雙管齊下**：fuzz 找到 panic，確認在 unsafe 區塊裡後，用 Miri（[Ch 32](./32-audit-unsafe.md) 介紹）對 crash input 重跑——Miri 能指出 UB 的具體種類（OOB access、unaligned ptr dereference 等），比 ASan 的報告更精確，適合 debug 階段。

**oss-fuzz-gen**：Google 在試驗用 LLM 自動生成 OSS-Fuzz 的 fuzz target，不需要人工寫 `fuzz_target!` 函式。目前（2026）還在早期，但值得關注，可能改變「fuzz target 要人工維護」的模式。

連結回 [Ch 31](./31-unsafe-vuln-classes.md)：fuzz 找到的 panic，若 backtrace 顯示在 unsafe 區塊裡，接下來的流程是：（1）fuzz + ASan 確認是否真的觸發記憶體錯誤；（2）Miri 確認 UB 種類；（3）人工判斷是否可升級為 exploitable（從 panic/DoS 到任意讀寫）；（4）用 [Ch 32](./32-audit-unsafe.md) 的流程提交 RUSTSEC advisory。

## 本章重點整理

- Safe Rust fuzz 的目標是 panic（DoS），不是記憶體 corruption；unsafe 區塊搭 ASan 才能找記憶體漏洞。Panic 在 server/解析器場景是嚴重漏洞，不要因為「沒有 UB」就跳過 fuzz。
- `cargo-fuzz`（libFuzzer）：`cargo fuzz init` + `cargo fuzz add` 建 target，`cargo +nightly fuzz run` 執行，crash artifact 路徑包含輸入的 SHA1（可診斷），`cargo fuzz tmin` 最小化。
- Fuzz target 設計：讓 panic 傳播（不要 catch_unwind）、過濾格式無效的輸入（`if let Ok`）、用 `arbitrary` crate 做結構化輸入、不要在 target 裡 `eprintln!`。
- Corpus 是 fuzzer 的燃料：放有代表性的合法輸入當 seed，覆蓋率進展速度差幾倍；空 corpus 從隨機 bytes 摸索是最慢的方式。
- `cargo-afl`（AFL++）不需要 nightly，有 cmplog/laf-intel 等加速技術；`cargo-fuzz` 整合 cargo 生態更方便。快速實驗用 cargo-fuzz，長期 campaign 或需要 AFL++ 特定模式用 cargo-afl。
- CI：`-max_total_time=30` 做 PR smoke test；開源專案可以投件 OSS-Fuzz，讓 Google fleet 24/7 代跑。

## 自我檢核

- [ ] Coverage-guided fuzzing 的迴圈是什麼？「有新 edge → 加入 corpus」這個步驟解決了什麼問題？
- [ ] 為什麼 safe Rust 值得 fuzz？panic 在哪些場景是安全漏洞？
- [ ] `fuzz_target!` 裡為什麼不能用 `catch_unwind`？什麼情況下才能有限度地使用？
- [ ] crash artifact 的檔名 `crash-da39...` 代表什麼？怎麼用它快速判斷觸發輸入？
- [ ] `cargo fuzz tmin` 做什麼？什麼時候需要它？
- [ ] Seed corpus 為什麼重要？「空 corpus 跑很慢」的根本原因是什麼？
- [ ] safe Rust 什麼時候需要搭 ASan？怎麼開？
- [ ] `cargo-fuzz` 和 `cargo-afl` 的取捨，你怎麼選？

## 延伸閱讀

- **[The Rust Fuzz Book](https://rust-fuzz.github.io/book/)** — Rust Fuzz 社群官方文件
  - **讀哪裡**：「cargo-fuzz」章節的「Writing a Fuzz Target」與「Improving Coverage」兩節；「Fuzzing Resources」列出了 Rust 生態裡已知被 fuzz 發現的 CVE 清單，有真實案例。
  - **能學到什麼**：本章的 `fuzz_target!`、corpus 管理、`tmin`、`cmin`（corpus minimize，把 corpus 裡重複覆蓋同樣 edge 的輸入去掉）的完整細節；「Fuzzing with sanitizers」段說明 ASan/MSan/UBSan 各自抓什麼、怎麼開。
  - **為什麼值得讀**：這是 cargo-fuzz 的第一手文件，覆蓋本章沒展開的 `cargo fuzz coverage`（生成 coverage report）和 `cargo fuzz fmt`（輸出格式解讀）。

- **[libFuzzer 官方文件](https://llvm.org/docs/LibFuzzer.html)** — LLVM 官方
  - **讀哪裡**：「Options」段落，把 `-max_total_time`、`-jobs`、`-max_len`、`-seed`、`-entropic` 這些 flag 的語意全部讀一遍；「Output」段解釋 libFuzzer 每次輸出那一行數字（`#`、`NEW`、`pulse`、`DONE`）的意義。
  - **能學到什麼**：看懂 fuzzer 輸出的「速度（exec/s）」和「corpus 大小」，判斷 fuzzer 是在有效探索還是在打轉；理解 entropic scheduling 比舊版 ValueProfile 快在哪。
  - **為什麼值得讀**：cargo-fuzz 只是個包裝，底層就是 libFuzzer；讀原始文件能讓你解讀所有沒被 cargo-fuzz 包裝到的 flag，在跑大型 fuzz campaign 時調效能。

- **[Google Security Blog —「Fuzzing at Scale: OSS-Fuzz」](https://security.googleblog.com/2016/12/announcing-oss-fuzz-continuous-fuzzing.html)** 與 **[OSS-Fuzz GitHub](https://github.com/google/oss-fuzz)**
  - **讀哪裡**：OSS-Fuzz GitHub 的 `projects/` 目錄下找一個你認識的 Rust 專案（例如 `projects/rustls`、`projects/serde`），看它的 `Dockerfile` 和 `fuzz.sh`，理解一個符合 OSS-Fuzz 規格的 fuzz target 長什麼樣。再讀 `docs/getting-started.md` 看投件流程。
  - **能學到什麼**：OSS-Fuzz 的 Rust 支援在 2023 年後大幅改善，理解怎麼投件能讓你的開源 library 被 Google fleet 24/7 免費 fuzz；`ClusterFuzz`（OSS-Fuzz 的 backend）的 triage 流程也是業界參考標準。
  - **為什麼值得讀**：CI smoke test 只跑 30 秒，真正深度的 fuzz 需要幾天到幾週的持續跑——OSS-Fuzz 是獲得這個計算量最便宜的方式（免費），對開源 Rust 專案幾乎是 no-brainer。

---

→ [練習 E：逆向 Rust binary 與 audit unsafe crate](./practice-e-reverse-audit.md)
