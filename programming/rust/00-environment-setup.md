# Ch 00 — 環境搭建與 C/C++ 對照心智

> **目標**：把 `rustup` / `cargo` / toolchain / target 這幾個概念一次釐清，裝好本課全程要用的 stable 1.97 + nightly 1.99 + Miri，並建立本課最核心的一張心智圖——拿你已經熟的 gcc/clang + make/cmake + 手動管記憶體，去對應 rustc + cargo + 編譯期管記憶體。裝完能跑出真實的 hello world 輸出。

> **環境**：本章以 `rustup 1.29` 安裝 `rustc 1.97.1` (stable) 與 `1.99.0-nightly` 在 **Linux / WSL2 (x86_64)** 上操作。本課**全程 Linux/WSL2**；Windows 原生 toolchain 不在範圍內（Part 6 的 kernel module 只能在 Linux 上跑，用 Windows 原生只會讓你多一層 MSVC ABI 的麻煩）。

## 為什麼需要這個？

你已經有一套 C/C++ 工具鏈的肌肉記憶：`apt install gcc`、`gcc -O2 a.c b.c -o app`、手寫 Makefile、`pkg-config` 找 header、出事了 `gdb`。這套東西是**拼裝**的——編譯器一個廠商、build system 一個工具、套件管理靠 distro 的 apt/yum（而且它管的是系統層的 `.so`，不是你這個專案的相依），格式化、linter、測試 runner 各自為政。

Rust 的工具鏈是**一體的**。這不是行銷話術，是設計決策的結果：Rust 從第一天就假設「一個語言該自帶版本管理器、build system、套件管理器、測試框架、文件產生器」。你要學的第一件事，不是 `let` 怎麼寫，而是這套一體工具鏈各個零件叫什麼、誰負責什麼——因為接下來每一章你都在跟它們打交道，搞不清楚 `rustup` 和 `cargo` 的分工，後面 nightly 切換、Miri、交叉編譯全部會卡。

## 先建立直覺：一條指令鏈 vs 三層工具

先看清楚 Rust 這邊有**三層**東西，各管各的，別搞混：

```
你打的指令            誰在管                  對應 C/C++ 世界的什麼
─────────────────────────────────────────────────────────────────
rustup               toolchain 管理器        沒有直接對應
  │                  （裝/切 rustc 版本、                 ≈ 手動裝多版 gcc
  │                    加 target、加元件）              + update-alternatives
  ▼
cargo                build system            ≈ make / cmake
  │  + 套件管理器                            + 你專案的相依管理
  │  + 測試 runner                           （C 世界根本沒有專案級的）
  ▼
rustc                編譯器本體              ≈ gcc / clang
  │
  ▼
target/…/binary      產物                    ≈ a.out / *.o / *.so
```

關鍵直覺：**你幾乎不會直接呼叫 `rustc`**。就像現代 C++ 專案你不會手打 `g++` 一長串 `-I -L -l`，你讓 cmake 去組。Rust 這邊 `cargo` 就是那個組指令的人，它在背後呼叫 `rustc`。而 `rustc` 這個指令本身，其實還不是真的編譯器——它是 `rustup` 裝的一個 **shim**（墊片）：

```
$ which rustc
/home/ypp/.cargo/bin/rustc      ← 這是 rustup 的 shim，不是真編譯器
```

這個 shim 會看你當前選的是 stable 還是 nightly，再去 `~/.rustup/toolchains/<版本>/` 裡找真正的 `rustc` 執行。這就是為什麼等一下 `rustc +nightly` 這種語法能運作——`+nightly` 是講給 shim 聽的，不是給真編譯器的參數。

## 安裝：一行指令與逐步驗證

官方安裝方式是 `rustup`。在 WSL2 (Ubuntu/Debian) 裡：

```bash
# 1. 裝 rustup（它會順便裝 stable 的 rustc + cargo）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# 一路預設（選 1），裝完會提示把 ~/.cargo/bin 加進 PATH

# 2. 讓當前 shell 認得（新開的 shell 會自動 source，這行是給當前 session）
source $HOME/.cargo/env
```

> `curl | sh` 這種裝法在資安人眼裡會皺眉。你可以先把 script 存下來讀過再跑：`curl --proto '=https' -sSf https://sh.rustup.rs -o rustup-init.sh`，看完再 `sh rustup-init.sh`。這不是形式，rustup-init 會動你的 shell profile 和 PATH，值得知道它做了什麼。

裝完先驗證 stable 這條線是通的。以下每一段都是在本課環境真跑出來的輸出：

```bash
$ rustc --version
rustc 1.97.1 (8bab26f4f 2026-07-14)

$ cargo --version
cargo 1.97.1 (c980f4866 2026-06-30)

$ rustup --version
rustup 1.29.0 (28d1352db 2026-03-05)
info: This is the version for the rustup toolchain manager, not the rustc compiler.
info: the currently active `rustc` version is `rustc 1.97.1 (8bab26f4f 2026-07-14)`
```

注意 `rustup --version` 那兩行 `info:`——rustup 自己特地提醒你「這是版本管理器的版本，不是編譯器的版本」。這正是新手最常混的點：`rustup` 的版本號（1.29）和 `rustc` 的版本號（1.97）是**兩件不相干的東西**。rustup 一年更新沒幾次，rustc 每六週一個新 stable。

### 加 nightly 與 Miri

本課 Part 3 之後要用 Miri 抓 UB，Miri 只在 nightly 上有。加 nightly toolchain 和 Miri 元件：

```bash
rustup toolchain install nightly       # 裝 nightly toolchain
rustup component add miri --toolchain nightly   # 在 nightly 上加 miri 元件
```

驗證 nightly 與 Miri：

```bash
$ rustc +nightly --version
rustc 1.99.0-nightly (ad3d0bc14 2026-07-31)

$ cargo +nightly miri --version
miri 0.1.0 (ad3d0bc141 2026-07-31)
```

`+nightly` 是 shim 語法：`rustc +nightly` = 「用 nightly 那套的 rustc」。不加 `+` 就是用 default toolchain（我們的 default 是 stable）。

一次看全部裝了什麼：

```bash
$ rustup show
Default host: x86_64-unknown-linux-gnu
rustup home:  /home/ypp/.rustup

installed toolchains
--------------------
stable-x86_64-unknown-linux-gnu (active, default)
nightly-x86_64-unknown-linux-gnu

active toolchain
----------------
name: stable-x86_64-unknown-linux-gnu
active because: it's the default toolchain
installed targets:
  x86_64-unknown-linux-gnu
  x86_64-unknown-none
```

（`x86_64-unknown-none` 是我下面加交叉編譯 target 時裝的，待會會講。）

## stable vs nightly：為什麼有兩條線？

C/C++ 沒有這個概念——你裝的 gcc 就是 gcc，頂多分 release 版本。Rust 刻意切三個 channel：

```
nightly ──每天一版──▶ beta（6 週後 promote）──▶ stable（再 6 週後 promote）
   │                                                    │
   │ 實驗性功能全開                                      │ 只有穩定功能
   │ #![feature(...)] 可用                              │ feature gate 全關
   │ Miri、部分 sanitizer、-Z 旗標                       │ 保證向後相容
```

- **stable**：六週一個版本（train model，跟 Chrome/Firefox 一樣）。只包含已經「穩定化」的功能，保證向後相容——你今天用 stable 編過的 code，未來的 stable 幾乎一定還能編（除非碰到 bug 修正或 soundness 修補）。本課日常寫 code 用它。
- **nightly**：每天一版。開放實驗性功能，要在檔案頂端寫 `#![feature(某功能)]` 去啟用。Miri、`-Z` 開頭的內部旗標、大部分 sanitizer 只在 nightly。本課只在**需要 Miri 或某個 nightly-only 功能時**切過去。
- **beta**：stable 的候選版，主要給 CI 做「下一版會不會 break 我」的預檢。本課用不太到。

心智對照：nightly 之於 stable，有點像 `gcc` 的 `-std=gnu2x`（實驗中的下一代標準）之於 `-std=c17`（已定稿）。差別是 Rust 把它做成整條獨立 toolchain，切換乾淨。

## 核心對照表：C/C++ 工具鏈 ↔ Rust 工具鏈

這張表是本章要你記住的東西。左邊你都會，右邊是它的對應。**背下右邊各自負責什麼，比背 Rust 語法更優先。**

| 任務 | C/C++ 世界 | Rust 世界 | 差異重點 |
|---|---|---|---|
| 編譯器本體 | `gcc` / `clang` | `rustc` | 你幾乎不直接呼叫 rustc |
| 裝/切編譯器版本 | 手動裝多版 + `update-alternatives` | `rustup` | Rust 內建，切版一行 |
| build system | `make` / `cmake` / `ninja` | `cargo build` | cargo 自帶，零設定就能建 |
| 專案相依管理 | 幾乎沒有（靠 distro 的 apt/vcpkg/conan） | `cargo` + `Cargo.toml` | 專案級鎖定版本（`Cargo.lock`） |
| 相依來源 | 系統 `.so` / header | crates.io（原始碼） | 預設抓原始碼自己編 |
| 建構產物位置 | 你自己指定（`-o`） | `target/debug/`、`target/release/` | cargo 固定約定 |
| 跑測試 | 自己接 `gtest` / `ctest` | `cargo test`（內建） | `#[test]` 寫在原始碼旁 |
| 格式化 | `clang-format`（要設定） | `cargo fmt`（`rustfmt`，官方單一風格） | 幾乎沒人吵風格 |
| 靜態檢查 | `clang-tidy` / `cppcheck` | `cargo clippy` | 官方 linter，訊息很具體 |
| 文件產生 | Doxygen（外掛） | `cargo doc`（內建，讀 `///` 註解） | 原始碼即文件 |
| 記憶體錯誤怎麼防 | 靠紀律 + ASan/Valgrind（**執行期**才抓到） | borrow checker（**編譯期**就擋） | 這是整門課的重點，Ch 1 展開 |
| 交叉編譯 | 換整套 cross toolchain（`arm-none-eabi-gcc`） | `rustup target add` + `--target` | 換 target 一行，同一個 rustc |

最後兩行是 Rust 相對 C/C++ 的根本差異：**記憶體安全從執行期挪到編譯期**，以及**交叉編譯不用換編譯器**。前者是 Ch 1 的主題，後者本章最後講。

## cargo 基本流程：new / build / run / test

`cargo` 是你 90% 時間打交道的指令。走一遍最小流程，全部真跑：

### `cargo new`：建專案骨架

```bash
$ cargo new ch0demo
    Creating binary (application) `ch0demo` package
```

它生出來的結構：

```bash
$ find . -type f | sort
./.gitignore          ← 順手幫你 git init 並加好 ignore
./Cargo.toml          ← 專案清單（相依、metadata）
./src/main.rs         ← 進入點，預設一個 hello world
```

`Cargo.toml` 長這樣：

```toml
[package]
name = "ch0demo"
version = "0.1.0"
edition = "2024"

[dependencies]
```

`edition = "2024"` 值得停一下。**edition（版本紀元）不是編譯器版本**，是「語言方言」——Rust 用 edition（2015/2018/2021/2024）做不破壞相容的語言演進：新 edition 可以改關鍵字、改預設行為，但舊 edition 的 code 永遠能繼續用同一個新 rustc 編。你可以在**同一個 build 裡**混用不同 edition 的 crate。C++ 的 `-std=c++20` 是最接近的類比，但 C++ 標準之間偶爾會 break 舊 code，Rust edition 保證不會。本課用預設的 2024。

### `cargo run`：一鍵編譯 + 執行

```bash
$ cargo run
   Compiling ch0demo v0.1.0 (/tmp/ch0demo)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.24s
     Running `target/debug/ch0demo`
Hello, world!
```

三件事一次做完：編譯（`dev` profile，等於 debug）、找到產物 `target/debug/ch0demo`、執行它。注意 `[unoptimized + debuginfo]`——預設是**沒開最佳化、帶除錯資訊**，對應 `gcc -O0 -g`。要 release 版：

```bash
$ cargo build --release
    Finished `release` profile [optimized] target(s) in 0.14s
$ ls -la target/release/ch0demo
-rwxr-xr-x 2 ypp ypp 444992 Aug  1 14:38 target/release/ch0demo
```

`--release` = `[optimized]`，對應 `gcc -O3` 那一檔。產物換到 `target/release/`。debug 和 release 分開放，互不覆蓋。

### `cargo test`：測試就在原始碼旁

C/C++ 你得另外接 gtest、寫 CMake target。Rust 把測試直接寫在原始碼裡，用 `#[test]` 標記：

```rust
fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn main() {
    println!("2 + 3 = {}", add(2, 3));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(add(2, 3), 5);
    }

    #[test]
    fn test_add_negative() {
        assert_eq!(add(-1, 1), 0);
    }
}
```

`#[cfg(test)]` 是條件編譯（conditional compilation）——這個 `mod tests` 只在 `cargo test` 時才編進去，正常 build 完全不含它，對應 C 的 `#ifdef TEST` 但乾淨得多。跑它：

```bash
$ cargo test
   Compiling ch0demo v0.1.0 (/tmp/ch0demo)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.19s
     Running unittests src/main.rs (target/debug/deps/ch0demo-b9cff5007f3c62d4)

running 2 tests
test tests::test_add ... ok
test tests::test_add_negative ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
```

不用寫 test runner、不用註冊測試、不用 main。cargo 幫你把所有 `#[test]` 函式收集起來跑。

### 不透過 cargo：直接 `rustc`

想確認「cargo 只是包了 rustc」，可以繞過它，就像你偶爾會手打 `gcc a.c` 跳過 make：

```bash
$ printf 'fn main(){ println!("hello from rustc"); }' > hw.rs
$ rustc hw.rs -o hw
$ ./hw
hello from rustc
$ file hw
hw: ELF 64-bit LSB pie executable, x86-64, ... dynamically linked,
   interpreter /lib64/ld-linux-x86-64.so.2, ... with debug_info, not stripped
```

看 `file` 的輸出：**動態連結、PIE、帶 debug_info、not stripped**。Rust binary 預設動態連結 glibc（不是常見誤解的「Rust 都是 static」），也預設帶符號——這點在 Part 5 逆向章會很有用，Rust binary 沒 strip 的話符號多到嚇人。

## rust-analyzer：編輯器裡的第二個編譯器

寫 Rust 不裝 rust-analyzer（LSP server）等於自廢武功。它對應 C/C++ 的 `clangd`，但更關鍵——因為 Rust 的 borrow/lifetime 錯誤，你會想在**打字當下**就看到，而不是等 `cargo build`。裝法：VS Code 直接裝 `rust-analyzer` 擴充，或編輯器接 LSP 指向 `rustup component add rust-analyzer` 裝的那顆。

它做的事：即時型別推導提示（inlay hints）、即時 borrow 錯誤標紅、跳定義、自動補 `use`。本課很多章你會先在編輯器裡看到紅線，才去 `cargo build` 確認——這是正常且高效的工作流。

## 交叉編譯：`rustup target add`（為 no_std / kernel 鋪路）

C 世界要交叉編譯到 ARM 裸機，你得裝**一整套**新 toolchain：`arm-none-eabi-gcc`、對應的 binutils、libc。換一個目標換一套。

Rust 這邊，同一個 `rustc` 就能吐不同架構的碼（LLVM 後端本來就多目標），你只要補上那個 target 的標準函式庫預編譯檔。示範加一個**裸機、無作業系統**的 target——這正是 Part 6 kernel / embedded 場景會用到的：

```bash
$ rustup target add x86_64-unknown-none
info: downloading component rust-std
```

`x86_64-unknown-none` 這個名字是 **target triple**，格式是 `<架構>-<廠商>-<系統>`：架構 `x86_64`、廠商 `unknown`（沒指定）、系統 `none`（沒有 OS——這就是 no_std/裸機的意思，Ch 22 展開）。加完再看：

```bash
$ rustup target list --installed
x86_64-unknown-linux-gnu
x86_64-unknown-none
```

之後 `cargo build --target x86_64-unknown-none` 就會編給裸機環境。**沒換編譯器，只多下載一份對應的 `rust-std`。** 這個「一個編譯器、多目標」的模型，是 Rust 在 embedded 和 kernel 場景比 C 好用一大截的原因之一。本章只要你知道概念和指令，實際 no_std 開發在 Ch 22。

## 對比與取捨

| 面向 | C/C++ 拼裝工具鏈 | Rust 一體工具鏈 | 取捨 |
|---|---|---|---|
| 上手 | 每個工具各自學、各自設定 | `cargo new` 到 `cargo test` 一條龍 | Rust 大勝，但你少了「自己控制每一步」的透明度 |
| build 設定彈性 | Makefile/CMake 想怎麼玩怎麼玩 | Cargo 約定優先，複雜 build 要寫 `build.rs` | 極度客製的 build，Cargo 反而綁手 |
| 相依管理 | 系統層，版本地獄 | 專案層鎖版（`Cargo.lock`） | Rust 大勝，但 crates.io 供應鏈風險是新攻擊面（Part 5） |
| 編譯速度 | 快（尤其 C） | 慢（單型化 + LLVM + 安全檢查），Ch 10 詳談 | C 勝，這是 Rust 的真實代價 |
| 多版本共存 | 麻煩 | `rustup` + `+toolchain` 語法乾淨 | Rust 大勝 |

## 踩雷集錦

1. **把 `rustup` 版本當成 `rustc` 版本**：`rustup --version` 顯示 1.29，有人就以為「我的 Rust 是 1.29」。錯。rustup 是**版本管理器**，rustc 才是**編譯器**；本課要的是 rustc 1.97。`rustup --version` 自己都印 info 提醒你了，別無視。

2. **以為 nightly 是「比 stable 新的 stable」**：不是「新版本」的關係，是**不同 channel**。nightly 開了實驗功能、可能有 regression、feature gate 隨時變。日常用 stable，只有明確需要 Miri 或某 `#![feature]` 時才 `+nightly`。把 nightly 當日常 default 是新手最容易養成的壞習慣，會讓你的 code 不小心依賴不穩定功能。

3. **在 Windows 原生裝 Rust 然後跟本課對不上**：Windows 原生 toolchain 用的是 MSVC ABI（`x86_64-pc-windows-msvc`），target triple、連結器、`file` 輸出全都跟本課的 `x86_64-unknown-linux-gnu` 不一樣，Part 6 的 kernel module 更是完全跑不了。本課**全程 WSL2/Linux**，一開始就在 WSL 裡裝，別在 PowerShell 裡 `rustup-init`。

4. **誤以為 Rust binary 都是 static、都 strip 過**：預設**動態連結** glibc、**帶符號未 strip**（上面 `file hw` 的輸出證明了）。要 static 得指定 `x86_64-unknown-linux-musl` target；要 strip 得自己 `strip` 或設 profile。這個預設在 Part 5 逆向時很關鍵——別假設你逆的 Rust binary 一定被 strip 過。

5. **`target/` 忘了它會很肥**：cargo 把每個相依的編譯產物、debug 和 release 兩份、測試產物全塞 `target/`，一個中型專案輕鬆上 GB。`cargo new` 幫你把它加進 `.gitignore` 是有原因的。要清就 `cargo clean`。

## 進階：再往深一層

- **`rustup override`**：可以針對某個目錄釘死用哪個 toolchain（`rustup override set nightly`），或在專案放 `rust-toolchain.toml` 讓所有協作者自動用同一版。本課後面的 nightly-only 章節，用 `+nightly` 臨時切就夠，不必 override。
- **`cargo` 子指令是可擴充的**：`cargo-miri`、`cargo-audit`、`cargo-fuzz`、`cargo-geiger` 都是外掛（binary 名叫 `cargo-xxx`，就能 `cargo xxx` 呼叫）。本課 Part 3/5 會逐一裝。這個 plugin 機制對應不了 C 世界任何東西——make 沒有這種生態。
- **`Cargo.lock` vs `Cargo.toml`**：`.toml` 寫你要的版本範圍（`serde = "1.0"` 是「>=1.0.0, <2.0.0」），`.lock` 記錄實際解析到的**精確版本**。binary 專案要 commit `Cargo.lock`（可重現建構）；library 專案傳統上不 commit（讓下游決定）。這個分工跟你在其他語言看過的 lockfile 一樣（`package-lock.json`、`Pipfile.lock`）。

## 動手練習

1. 在 WSL 裡跑完整條流程：`cargo new hello` → 改 `src/main.rs` 讓它印你的名字 → `cargo run` → `cargo build --release` → 比較 `target/debug/hello` 和 `target/release/hello` 的檔案大小（`ls -la`），想想為什麼 release 版檔案大小不同。
2. 故意把 `main.rs` 裡的分號刪一個，跑 `cargo build`，讀完整的錯誤訊息。記住 rustc 錯誤訊息的長相——本課後面會一直看到它。
3. 用 `rustc +nightly --version` 和 `rustc --version` 各跑一次，確認你能區分兩條 channel。再 `rustup show` 確認 nightly 和 Miri 都裝好了。
4. `rustup target add aarch64-unknown-linux-gnu`，再 `rustup target list --installed` 確認多了一個。這是 ARM64 target，Part 6 對照 ARM64 時會用到。

## 本章重點整理

- Rust 工具鏈是**三層**：`rustup`（管版本/target/元件）→ `cargo`（build + 套件 + 測試）→ `rustc`（編譯器，你幾乎不直接碰）。`rustc`/`cargo` 指令其實是 rustup 的 shim。
- **stable 日常用，nightly 只在需要 Miri / `#![feature]` 時切**；用 `+nightly` 臨時切換。本課環境：stable 1.97 + nightly 1.99 + Miri。
- `cargo new / build / run / test` 是核心迴圈；測試用 `#[test]` 寫在原始碼旁，`#[cfg(test)]` 只在測試時編入。
- 交叉編譯不換編譯器：`rustup target add <triple>` 加一份 std，`--target` 指定。這是 no_std / kernel 場景的基礎。
- 最重要的一張心智圖：C/C++ 靠**執行期**工具（ASan/Valgrind）+ 紀律防記憶體錯誤，Rust 把它挪到**編譯期**（borrow checker）——這是 Ch 1 的主題。

## 自我檢核

- [ ] 不看筆記，能不能講清楚 `rustup`、`cargo`、`rustc` 三者各自負責什麼、誰呼叫誰？
- [ ] 如果同事說「我的 Rust 是 1.29」，你能不能指出他哪裡搞混了？
- [ ] 為什麼本課日常用 stable 而不是 nightly？什麼情況才該切 nightly？
- [ ] 能不能用一句話對 C 背景的人解釋 `rustup target add` 相對於「裝一整套 cross toolchain」的差別？
- [ ] `file` 看一個預設 `cargo build` 出來的 binary，它是 static 還是 dynamic？strip 了沒？（不確定就回去跑一次）
- [ ] `edition` 和 `rustc` 版本是同一件事嗎？edition 對應 C++ 世界的什麼？

## 延伸閱讀

### 官方文件

- **[The Cargo Book](https://doc.rust-lang.org/cargo/)**
  - **讀哪裡**：先讀「Getting Started」和「Cargo Guide」兩章；「Reference → Manifest Format」在你要調 `Cargo.toml` 時再查。
  - **能學到什麼**：本章只帶了 new/build/run/test，Cargo Book 把 profile、feature flag、workspace（多 crate 專案）講全。
  - **前提**：跑過本章的 `cargo new` 流程就能讀。

- **[The rustup Book](https://rust-lang.github.io/rustup/)**
  - **讀哪裡**：「Concepts」整章（channels / toolchains / components / targets 的正式定義）、「Overrides」那節。
  - **能學到什麼**：把本章「三層工具鏈」講得更精確，尤其 `rust-toolchain.toml` 釘版和 override 的優先序規則。
  - **前提**：本章的 rustup 概念。

### 部落格 / 技術文章

- **[Rust Editions — The Edition Guide](https://doc.rust-lang.org/edition-guide/)**
  - **這篇說什麼**：edition 這個 Rust 獨有的相容性機制到底怎麼運作、2015/2018/2021/2024 各改了什麼、為什麼能在同一 build 混用不同 edition。
  - **讀哪裡**：「What are Editions?」開頭那節先讀，其餘當各 edition 差異的查詢手冊。
  - **為什麼值得讀**：這是官方對 edition 的權威說明；C++ 背景的人最容易把 edition 和編譯器版本搞混，這份把界線畫清楚。

### 書籍

- **《The Rust Programming Language》(The Book)** — Steve Klabnik & Carol Nichols（線上免費，doc.rust-lang.org/book）
  - **這本書的定位**：官方入門書。本課節奏太快、某個基礎語法沒補到時的補課來源。
  - **讀哪幾章**：第 1 章「Getting Started」對應本章的安裝與 `cargo`；後面章節等本課相應 Part 再回去對照。

裝好環境、跑出第一個 hello world 之後，下一章我們回答那個真正的問題：明明有 C++ 的 RAII 和 smart pointer 了，為什麼還需要 Rust？答案藏在「編譯期 vs 執行期」這條線上。

→ [Ch 01 為什麼是 Rust：給 C/C++ 人的定位](./01-why-rust.md)
