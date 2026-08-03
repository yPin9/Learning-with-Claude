# Ch 0 — 環境搭建

> **目標**：把這門課需要的所有工具裝好、Cargo workspace 骨架跑起來、第一個測試通過，讓後面每章都能直接動手，不卡在環境問題。
>
> **環境**：WSL 2（Ubuntu 22.04 或 24.04）、Rust stable 1.87.x、cargo（隨 rustup 一起裝）。課程不在 Windows native 環境驗證——檔案系統語意差異太大，後面的 `O_DIRECT` / `mmap` / `fallocate` 等呼叫在 WSL 下比較貼近真實 Linux 行為。

---

## 為什麼從零手刻一個資料庫

市面上說「造輪子學原理」的課很多，但資料庫（database）這個題目特別適合系統底層的人：
每一層都有具體的 I/O 語意、對齊限制、鎖的粒度，沒有抽象層可以躲。

我們的目標是一個**單機關聯式資料庫（single-node relational database）**，支援 SQL 子集、有預寫日誌（Write-Ahead Log，WAL）、有頁式儲存（page-based storage）、有 B-Tree 索引。
整門課不是在學資料庫「使用」，而是在學它**每一個設計決策背後的取捨**——為什麼 page 是 4 KiB、為什麼不直接用 `HashMap` 當索引、WAL 的 fsync 要放在哪。

Rust 在這裡是認真的選擇，不是噱頭：
- 沒有 GC 暫停，對延遲（latency）可預測；
- 所有權系統強迫你在寫程式時就思考 buffer 的生命週期；
- `unsafe` 塊像手術刀，讓你在需要時做指標算術，但邊界清楚。

---

## Rust toolchain

### 安裝 rustup

如果 WSL 環境是乾淨的，先裝 rustup：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# 選 1 (Proceed with standard installation)
source "$HOME/.cargo/env"
```

安裝完確認版本：

```bash
rustup show
```

輸出會列出 active toolchain，確認是 `stable-x86_64-unknown-linux-gnu` 類的條目，以及 rustc / cargo 的版本號（1.87.x）。

### 釘住版本

課程的每一章都在同一個 workspace 下疊加，Rust 版次（edition）和 toolchain 版本要固定，不然六個月後你回來重編可能行為不同。在 workspace 根目錄建立 `rust-toolchain.toml`：

```toml
[toolchain]
channel = "stable"
components = ["rustfmt", "clippy"]
```

這樣 `cargo build` 時 rustup 會自動確保用正確的 stable，而且這個檔案進 git，整個 repo 的工具鏈版本就被鎖住了。

如果你的機器上裝了多個 toolchain，在 workspace 根目錄執行：

```bash
rustup override set stable
```

之後這個目錄下所有 cargo 指令都會走 stable。

---

## 專案結構規劃

### 為什麼用 Cargo workspace

這個課程的 DB 專案會逐章長大。如果用單一 crate，你會遇到兩個痛點：

1. **編譯邊界消失**：storage engine 改一行，整個 binary 重編。
2. **無法獨立測試**：你想跑 WAL 的 unit test，卻會帶著 query engine 一起編，錯誤訊息互相干擾。

Cargo workspace（工作區）讓我們把不同層的程式碼拆成獨立的 **crate**（函式庫單元），各自有 `Cargo.toml`，但共享同一個 `Cargo.lock` 和 `target/` 目錄。好處：

- 各 crate 獨立編譯快取，修改一層不會讓另一層失效。
- 每個 crate 都能 `cargo test -p <crate>` 單獨跑測試。
- 依賴關係是顯式的：`query_engine` 想用 `storage_engine`，就要在自己的 `Cargo.toml` 裡寫 `storage_engine = { path = "../storage_engine" }`，沒有隱藏耦合。

### Crate 分層

```
minidb/                          ← workspace 根目錄
├── Cargo.toml                   ← workspace manifest
├── rust-toolchain.toml
├── storage_engine/              ← 頁管理、B-Tree、buffer pool
│   ├── Cargo.toml
│   └── src/
│       └── lib.rs
├── wal/                         ← Write-Ahead Log
│   ├── Cargo.toml
│   └── src/
│       └── lib.rs
├── query_engine/                ← SQL 解析、執行計畫
│   ├── Cargo.toml
│   └── src/
│       └── lib.rs
├── common/                      ← 共用型別、錯誤定義
│   ├── Cargo.toml
│   └── src/
│       └── lib.rs
└── minidb/                      ← 最終可執行 binary，把各層組起來
    ├── Cargo.toml
    └── src/
        └── main.rs
```

依賴方向只能單向往下：`query_engine` → `storage_engine` → `common`；`wal` → `common`。禁止循環依賴，Cargo 會在編譯期報錯。

---

## 建立 workspace 骨架

下面的指令和程式碼要能真正編譯，先把目錄結構建好：

```bash
mkdir -p minidb/{storage_engine,wal,query_engine,common,minidb}/src
cd minidb
```

### workspace 根目錄的 Cargo.toml

```toml
[workspace]
members = [
    "storage_engine",
    "wal",
    "query_engine",
    "common",
    "minidb",
]
resolver = "2"

[workspace.package]
version = "0.1.0"
edition = "2021"
authors = ["你的名字"]

[workspace.dependencies]
# 共用第三方依賴統一寫這裡，各 crate 用 { workspace = true } 引用
thiserror = "2"
tracing = "0.1"
```

`resolver = "2"` 是 Rust 2021 edition 的預設功能解析器（feature resolver），課程的 crate 全部用 edition 2021，這行要有。

### common/Cargo.toml

```toml
[package]
name = "common"
version.workspace = true
edition.workspace = true

[dependencies]
thiserror = { workspace = true }
```

### common/src/lib.rs

```rust
//! 跨 crate 共用的基本型別與錯誤定義。

/// 資料庫的統一錯誤型別。
/// 各 crate 的內部錯誤應透過 `#[from]` 轉換成這個型別。
#[derive(Debug, thiserror::Error)]
pub enum DbError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("corrupted page: {reason}")]
    CorruptedPage { reason: String },

    #[error("not found: {key}")]
    NotFound { key: String },
}

pub type DbResult<T> = Result<T, DbError>;

/// 頁面大小常數（4 KiB）。課程全程固定，不做可配置。
pub const PAGE_SIZE: usize = 4096;

/// 頁面識別碼（Page ID，頁碼），從 0 開始。
pub type PageId = u64;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_size_is_power_of_two() {
        assert!(PAGE_SIZE.is_power_of_two());
    }

    #[test]
    fn db_error_display() {
        let e = DbError::NotFound {
            key: "foo".to_string(),
        };
        assert_eq!(e.to_string(), "not found: foo");
    }
}
```

### storage_engine/Cargo.toml

```toml
[package]
name = "storage_engine"
version.workspace = true
edition.workspace = true

[dependencies]
common = { path = "../common" }
thiserror = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
tempfile = "3"

[dev-dependencies.criterion]
version = "0.5"
features = ["html_reports"]

[[bench]]
name = "page_rw"
harness = false
```

### storage_engine/src/lib.rs

```rust
//! Storage engine：頁式儲存、buffer pool、B-Tree 索引。
//! 後續章節會在這個 crate 內逐步擴充。

use common::{DbResult, PAGE_SIZE, PageId};

/// 一個固定大小的頁面（page），代表磁碟上的最小讀寫單位。
#[derive(Debug)]
pub struct Page {
    pub id: PageId,
    pub data: Box<[u8; PAGE_SIZE]>,
}

impl Page {
    /// 建立一個全零的空白頁面。
    pub fn new(id: PageId) -> Self {
        Self {
            id,
            data: Box::new([0u8; PAGE_SIZE]),
        }
    }

    /// 把整個頁面寫入 `dst`，`dst` 必須至少有 `PAGE_SIZE` 位元組。
    pub fn write_to(&self, dst: &mut [u8]) -> DbResult<()> {
        if dst.len() < PAGE_SIZE {
            return Err(common::DbError::CorruptedPage {
                reason: format!(
                    "destination buffer too small: {} < {}",
                    dst.len(),
                    PAGE_SIZE
                ),
            });
        }
        dst[..PAGE_SIZE].copy_from_slice(self.data.as_ref());
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_page_is_zeroed() {
        let p = Page::new(0);
        assert!(p.data.iter().all(|&b| b == 0));
    }

    #[test]
    fn write_to_buffer_too_small_returns_error() {
        let p = Page::new(1);
        let mut buf = vec![0u8; 10];
        assert!(p.write_to(&mut buf).is_err());
    }
}
```

### wal/Cargo.toml

```toml
[package]
name = "wal"
version.workspace = true
edition.workspace = true

[dependencies]
common = { path = "../common" }
thiserror = { workspace = true }
tracing = { workspace = true }
```

### wal/src/lib.rs

```rust
//! Write-Ahead Log（WAL，預寫日誌）。
//! 保證崩潰後可以重播（replay）已提交的操作。

use common::{DbResult, PageId};

/// WAL 的單一記錄型別。後續章節會大幅擴充。
#[derive(Debug, Clone)]
pub enum WalRecord {
    /// 某個頁面的完整寫入。
    WritePage { page_id: PageId, data: Vec<u8> },
    /// 事務提交點（commit point）。
    Commit { txn_id: u64 },
}

/// WAL 管理器的佔位骨架，第 N 章實作。
pub struct WalManager;

impl WalManager {
    pub fn new() -> DbResult<Self> {
        Ok(WalManager)
    }
}

impl Default for WalManager {
    fn default() -> Self {
        WalManager
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wal_record_clone() {
        let r = WalRecord::Commit { txn_id: 42 };
        let r2 = r.clone();
        matches!(r2, WalRecord::Commit { txn_id: 42 });
    }
}
```

### query_engine/Cargo.toml

```toml
[package]
name = "query_engine"
version.workspace = true
edition.workspace = true

[dependencies]
common = { path = "../common" }
storage_engine = { path = "../storage_engine" }
thiserror = { workspace = true }
tracing = { workspace = true }
```

### query_engine/src/lib.rs

```rust
//! Query engine：SQL 解析、邏輯計畫、實體計畫、執行器。
//! 後續章節逐步填入。

pub mod placeholder {
    //! 佔位模組，讓 `cargo check` 通過。後續章節會替換成真實實作。
    pub fn engine_version() -> &'static str {
        "minidb 0.1.0"
    }
}

#[cfg(test)]
mod tests {
    use super::placeholder;

    #[test]
    fn version_string_not_empty() {
        assert!(!placeholder::engine_version().is_empty());
    }
}
```

### minidb/Cargo.toml（binary）

```toml
[package]
name = "minidb"
version.workspace = true
edition.workspace = true

[dependencies]
common = { path = "../common" }
storage_engine = { path = "../storage_engine" }
wal = { path = "../wal" }
query_engine = { path = "../query_engine" }
tracing = { workspace = true }
tracing-subscriber = "0.3"
```

### minidb/src/main.rs

```rust
fn main() {
    tracing_subscriber::fmt::init();
    tracing::info!("minidb starting");
    println!("minidb 0.1.0 — environment OK");
}
```

### 跑起來

```bash
# 在 minidb/ 根目錄
cargo build
cargo test --workspace
cargo run -p minidb
```

全部通過就代表骨架正確。`cargo test --workspace` 會跑所有 crate 的 `#[test]`，現在應該看到 6 個測試全綠。

---

## 測試策略

這個 DB 專案後面會有大量複雜狀態，測試層次要從一開始就想清楚。

### Unit test（`#[test]`）

放在 `src/` 裡面，`#[cfg(test)] mod tests { ... }` 包起來。
**用在**：純函式、型別轉換、邊界值、錯誤路徑。
不碰檔案系統，不碰網路，執行毫秒級。

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_id_zero_is_valid() {
        let p = Page::new(0);
        assert_eq!(p.id, 0);
    }
}
```

### Integration test（`tests/` 目錄）

放在 crate 根目錄的 `tests/` 下，每個 `.rs` 就是一個獨立的 integration test binary。
**用在**：跨模組互動、真實 I/O（搭配 `tempfile`）、完整的讀寫-重啟-回放流程。
注意：integration test 只能存取 crate 的公開介面（`pub`），這會強迫你把 API 設計清楚。

```
storage_engine/
└── tests/
    └── page_roundtrip.rs   ← 測試頁面寫入磁碟後讀回的正確性
```

### Doctest（文件測試）

在 doc comment 裡的 ` ```rust ` 區塊，`cargo test` 會自動跑。
**用在**：公開 API 的使用範例，同時當文件、同時當測試，永遠不會過期。

```rust
/// 建立一個全零頁面。
///
/// ```
/// use storage_engine::Page;
/// let p = Page::new(5);
/// assert_eq!(p.id, 5);
/// ```
pub fn new(id: PageId) -> Self { ... }
```

---

## Criterion benchmark

效能敏感的路徑（頁面讀寫、B-Tree 查找、WAL 刷盤）要用 criterion（基準測試框架）量化，不然你不知道每章的「最佳化」是在改善還是退步。

我們在 `storage_engine/Cargo.toml` 裡已經加好了：

```toml
[dev-dependencies.criterion]
version = "0.5"
features = ["html_reports"]

[[bench]]
name = "page_rw"
harness = false
```

建立 `storage_engine/benches/page_rw.rs`：

```rust
use criterion::{Criterion, criterion_group, criterion_main};
use storage_engine::Page;

fn bench_page_new(c: &mut Criterion) {
    c.bench_function("Page::new", |b| {
        b.iter(|| {
            let _p = Page::new(criterion::black_box(0));
        });
    });
}

fn bench_write_to(c: &mut Criterion) {
    let page = Page::new(0);
    let mut buf = vec![0u8; 4096];
    c.bench_function("Page::write_to", |b| {
        b.iter(|| {
            page.write_to(criterion::black_box(&mut buf)).unwrap();
        });
    });
}

criterion_group!(benches, bench_page_new, bench_write_to);
criterion_main!(benches);
```

跑 benchmark：

```bash
cargo bench -p storage_engine
```

第一次跑完 criterion 會在 `target/criterion/` 下產生 HTML 報告，可以用瀏覽器開來看火焰圖。

---

## cargo 工具鏈

### clippy

```bash
cargo clippy --workspace -- -D warnings
```

`-D warnings` 把所有警告升級成錯誤，CI 中必加。clippy 的 lint 涵蓋了大量 Rust 慣用法問題：不必要的 clone、可以用 `?` 展開的 match、lifetime 可以省略等。
建議在 workspace 根目錄加 `.cargo/config.toml`：

```toml
[build]
# 開發期也開啟 clippy lint，不用每次手動加 flag
# （或者在 CI 的 Makefile/GitHub Actions 裡統一跑）
```

課程中每章結束前都會跑一次 clippy，不讓技術債累積。

### rustfmt

```bash
cargo fmt --all
```

不要爭格式問題，全部交給 `rustfmt`。在 workspace 根目錄加 `rustfmt.toml` 可以調整少數選項（例如 `max_width = 100`），但預設值在這個課程夠用。

### cargo-expand

展開巨集（macro），debug 的利器：

```bash
cargo install cargo-expand
cargo expand -p common
```

`thiserror` 幫我們產生的 `Display` / `From` 實作展開後一目了然，後面談 error handling 時會用到。

### cargo-nextest（選用）

nextest 是比原生 `cargo test` 快很多的測試執行器（test runner），特別是 workspace 大了之後：

```bash
cargo install cargo-nextest
cargo nextest run --workspace
```

它對每個測試用獨立 process，測試之間不共享全域狀態，更接近真實隔離。如果你的機器 CPU 夠多，這個值得裝。

---

## 如何組織「逐步長大」的 DB 專案

### Feature flag 隔離未完成的程式碼

每章會引入新的子系統，但我們不想讓還沒完成的程式碼影響其他章的 `cargo build`。Cargo 的 feature flag（功能旗標）可以做到這件事：

```toml
# storage_engine/Cargo.toml
[features]
default = []
btree = []        # Ch 5 開始啟用
buffer_pool = []  # Ch 7 開始啟用
```

程式碼裡：

```rust
#[cfg(feature = "btree")]
pub mod btree;
```

開發某一章時：

```bash
cargo test -p storage_engine --features btree
```

不加 feature 時，`btree` 模組不參與編譯，不影響早期章節的測試。

### Integration test 對應章節

建議每一章對應一個 integration test 檔，命名清楚：

```
storage_engine/tests/
├── ch03_page_rw.rs
├── ch05_btree_insert.rs
└── ch07_buffer_pool.rs
```

這樣你知道「這個 integration test 是第幾章實作完才能通過」，回來複習時也能快速定位。每個測試檔的第一行加：

```rust
// 對應章節：Ch 5 — B-Tree 基本操作
// 前置：storage_engine 的 Page 讀寫（Ch 3）
```

---

## 踩雷：錯誤直覺 vs 正確認識

**1. 「`Box<[u8; PAGE_SIZE]>` 跟 `Vec<u8>` 差不多，用 Vec 比較方便」**
錯。`Vec<u8>` 是三字組（pointer + len + cap），在 heap 上配置後仍然有一層間接。`Box<[u8; PAGE_SIZE]>` 固定大小，編譯期就知道 4096，可以讓對齊（alignment）和 `copy_from_slice` 更直接。更重要的：後面做 `O_DIRECT` 對齊 I/O 時，`Vec` 的配置器不保證對齊到 512 bytes，`Box<[u8; N]>` 加上自訂 allocator 才是正解。

**2. 「Cargo workspace 的 `Cargo.lock` 在各 crate 裡各自一份」**
錯。整個 workspace 共享唯一一份根目錄的 `Cargo.lock`。這表示所有 crate 用的第三方依賴版本是一致的，不會出現 A crate 用 `serde 1.0.190`、B crate 用 `serde 1.0.200` 的狀況。

**3. 「unit test 夠了，integration test 是多餘的」**
錯。unit test 測的是「函式對不對」；integration test 測的是「各模組接起來對不對」。資料庫的 bug 大量藏在邊界：WAL 寫完、crash、storage engine 重新讀回，這個流程只有 integration test 能覆蓋到。

**4. 「`criterion::black_box` 是裝飾用的，加不加無所謂」**
錯。`black_box` 告訴編譯器「這個值是不透明的，你不能把整個 benchmark 函式最佳化掉」。沒有它，rustc 可能發現迴圈裡的計算結果沒人用，直接消除，讓你量到的是「空迴圈」的時間，毫無意義。

**5. 「clippy 的建議是風格建議，可以選擇性忽略」**
課程裡，我們對 clippy 的態度是：`-D warnings`，不討論，全修。原因是 clippy 抓到的往往不是風格問題，而是隱藏的效能陷阱（例如不必要的 `.clone()`）或是 panic 路徑（例如 `.unwrap()` 在正式路徑上）。養成習慣從一開始就修，遠比後期整批清警告容易。

---

## 本章重點整理

- WSL + Rust stable 1.87.x 是課程唯一驗證環境，`rust-toolchain.toml` 鎖定版本。
- Cargo workspace 把 DB 拆成 `common` / `storage_engine` / `wal` / `query_engine` / `minidb` 五個 crate，依賴方向單向，各自獨立編譯和測試。
- 測試三層：unit test 測純邏輯、integration test 測模組接合、doctest 測公開 API 範例。
- criterion 量化效能敏感路徑，`black_box` 防止編譯器消除 benchmark。
- clippy `-D warnings` + rustfmt 是強制門檻，不是選項。
- Feature flag 隔離未完成的子系統，integration test 檔名對應章節，讓整個 repo 在任何時間點都能乾淨 `cargo build`。

---

## 自我檢核

- [ ] `cargo test --workspace` 全部通過，我能說出現在有幾個 crate、幾個測試。
- [ ] 我能解釋 Cargo workspace 的 `Cargo.lock` 放在哪裡、為什麼只有一份。
- [ ] 我能說出 unit test / integration test / doctest 各自適合測什麼場景。
- [ ] 我知道 `criterion::black_box` 的作用，以及拿掉它的後果。
- [ ] 我能解釋為什麼 `Box<[u8; PAGE_SIZE]>` 在對齊 I/O 場景比 `Vec<u8>` 好。
- [ ] 我能在不看文件的情況下，把一個新 crate 加入 workspace 並讓它依賴 `common`。

---

## 延伸閱讀

1. **The Cargo Book — Workspaces**（`doc.rust-lang.org/cargo/reference/workspaces.html`）
   看「workspace inheritance」那節，了解 `version.workspace = true` 背後的機制；關聯：本章 workspace Cargo.toml 的設計依據。

2. **Database Internals（Alex Petrov, O'Reilly 2019）** — 第一章 "Introduction and Overview"
   這本是整門課的理論對照；先讀第一章，建立「儲存引擎 vs 事務管理器」的大局觀，之後每章都會回頭對照。

3. **CMU 15-445 Intro to Database Systems — Lecture 1（Andy Pavlo）**（`15445.courses.cs.cmu.edu`）
   看前 30 分鐘，Pavlo 說清楚「為什麼不能用 OS 的檔案系統直接當資料庫」，這個問題的答案貫穿後面的頁管理、WAL 設計。

4. **Criterion.rs 文件**（`bheisler.github.io/criterion.rs/book/`）
   讀「Timing Loops」和「Avoiding Optimizer」兩節，理解 `black_box` 和 `Bencher::iter` 的正確用法；課程後面每個效能章節都會寫 benchmark。

5. **Rust API Guidelines**（`rust-lang.github.io/api-guidelines/`）
   瀏覽「Naming」和「Documentation」兩章，之後寫 `pub` API 時按這套規範，clippy 和 doctest 才會對你友善。

---

→ [下一章](./01-database-panorama.md)
