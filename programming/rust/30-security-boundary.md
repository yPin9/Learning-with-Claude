# Ch 30 — Rust 的安全邊界與威脅模型

> **目標**：把「safe Rust 到底保證什麼、不保證什麼」講到你能對客戶、對主管、對自己講清楚。學完你能畫出一個 Rust 專案的**信任邊界圖**，指出每一條可能出問題的線（safe/unsafe、FFI、`build.rs`/proc-macro、依賴樹），並且知道「記憶體安全」這四個字**縮小了哪一塊攻擊面、完全沒碰哪一塊**。這是整個 Part 5 的地基：後面三章（unsafe 漏洞類、audit、逆向）都在這張圖上的某一條線上。

> **環境**：Rust 以 `rustc 1.97.1 (8bab26f4f 2026-07-14)`（stable）與 nightly `rustc 1.99.0-nightly (ad3d0bc14 2026-07-31)` 在 x86-64 Linux（WSL2）；本章所有 native 執行輸出、編譯錯誤與 debug/release 行為差異都是本機真跑，非推測。引用的 Android/Chromium 數據標明來源。

## 為什麼需要這個？

你做過 pwn 和 RE。你對「記憶體安全漏洞」的直覺是很具體的：UAF 之後劫持 vtable、堆疊溢位蓋返回位址、double-free 打 tcache、data race 撞出一個 exploitable 的中間狀態。這些是你的飯碗，也是你評估一個 C/C++ 專案風險時第一個掃的東西。

現在有人拿一個 Rust 專案來，問你：「這安全嗎？」

錯誤的答案有兩種，而且都很常見。第一種：「Rust 是記憶體安全的，所以安全。」——這是把「記憶體安全」當成「安全」，會讓你漏掉一整片攻擊面（邏輯漏洞、DoS、SSRF、供應鏈）。第二種：「Rust 也有 unsafe，所以跟 C 一樣不安全。」——這是把「有破口」當成「沒有保證」，會讓你錯估 audit 的**面積**：C 你要盯全部，Rust 你只要盯 unsafe 加依賴。

這一章的目的，是讓你給出**第三種答案**：「safe Rust 消掉了這幾類記憶體安全 UB，攻擊面縮到 unsafe 區塊、FFI 邊界、編譯期程式碼、依賴樹這幾條線上；但邏輯漏洞、panic/DoS、整數溢位造成的邏輯錯誤、應用層注入這些它一個都沒管。我掃的時候先看這張信任邊界圖。」

要給出這個答案，你得先精確知道邊界在哪。這一章就是把邊界一條一條劃清楚。

## 先建立直覺

先看一張圖。這是本章的核心——一個 Rust 專案的**信任邊界（trust boundary）**。每一個框是一塊「你選擇信任」的東西，框跟框之間的線就是攻擊面。

```
                    ┌─────────────────────────────────────────┐
                    │          你寫的 safe Rust               │
                    │  borrow checker + 型別系統保證：        │
                    │  無 UAF / double-free / OOB / data race │  ← 記憶體安全 UB 被擋在這裡
                    │                                          │
                    │  但仍可能有：邏輯漏洞、panic、整數溢位   │  ← 這些沒被擋
                    │  wrapping 邏輯錯誤、unwrap DoS、SSRF...   │
                    └───────────┬──────────────────┬──────────┘
                                │                  │
                 ┌──────────────▼───┐    ┌─────────▼─────────────┐
                 │  unsafe 區塊     │    │  FFI 邊界（extern "C"）│
                 │  你手動維持的     │    │  對面是 C，全語言無保證 │
                 │  invariant       │    │  Rust 只能信任你的宣告 │
                 │  → Ch 31         │    │  → Ch 19              │
                 └──────────────────┘    └───────────────────────┘

  ══════════════════ 上面是「執行期」的線，下面是「編譯期」的線 ══════════════════

     ┌───────────────────────────────────────────────────────────────┐
     │  依賴樹（每個 crate 都可能有自己的 unsafe / FFI / build.rs）   │
     │  ┌─────────┐   ┌─────────┐   ┌──────────────────────────────┐  │
     │  │ crate A │   │ crate B │   │ build.rs / proc-macro        │  │
     │  │ 的unsafe│   │ 的 FFI  │   │ 編譯期在你機器上跑任意程式碼! │  │
     │  └─────────┘   └─────────┘   └──────────────────────────────┘  │
     └───────────────────────────────────────────────────────────────┘
                     ↑ Part 5 後三章與供應鏈稽核的主戰場
```

盯著這張圖記住三件事：

1. **最上面那個大框是 Rust 的招牌成就**：在框內，一整類記憶體安全 UB 消失了。這是型別系統 + borrow checker 的成果，不是口號。
2. **框內還有一行小字**：「但仍可能有邏輯漏洞、panic...」——記憶體安全 ≠ 沒有漏洞。這行小字是本章下半場。
3. **框外那些線才是攻擊者真正的入口**：unsafe、FFI、依賴、`build.rs`。Rust 沒有讓這些消失，只是把它們**圈起來、標記出來**，讓你知道要盯哪裡。這就是「攻擊面縮小」的真正意思——不是消失，是**可定位**。

## safe Rust 到底保證什麼？

先把招牌成就講精確。**safe Rust 保證：沒有未定義行為（UB）中的記憶體安全那一類。** 具體是這幾類（你在 C 裡靠紀律避免的那些）：

| 漏洞類 | C 裡怎麼發生 | safe Rust 為什麼擋掉 |
|---|---|---|
| use-after-free | free 後還留指標 | ownership + drop：值被 move 走後編譯器不讓你再用它 |
| double-free | 兩條路徑都 free | 每個值只有一個 owner，drop 只跑一次 |
| buffer overflow / OOB | 陣列下標不檢查 | slice 存取有 bounds check（panic 而非 UB） |
| use of uninitialized | 宣告後沒賦值就讀 | 編譯器要求 use 前必被初始化 |
| iterator invalidation | 邊 iterate 邊改容器 | borrow checker：iterate 期間容器被 `&` 借走，不能 `&mut` |
| data race | 多執行緒無同步存取 | `Send`/`Sync` + borrow checker：共享必唯讀或有同步（Ch 23） |

這裡的關鍵字是 **UB**。C 的記憶體錯誤之所以致命，是因為它們是 UB——編譯器假設它們不發生，於是可以做出「跑起來像對的、但語意上已經崩壞」的優化，攻擊者利用這個縫隙。safe Rust 的保證是：**你寫不出這些 UB**，因為 borrow checker 在編譯期就把對應的程式碼擋掉（[Ch 3](./03-borrowing-references.md)、[Ch 7](./07-borrow-checker-internals.md) 的別名規則），或型別系統要求你處理（[Ch 13](./13-error-handling.md) 的 `Option`/`Result`）。

看一個具體的：C 裡的經典 UAF，換到 Rust 連編譯都過不了。

```rust
fn main() {
    let s = String::from("owned data");
    let r = &s;            // 借用 s
    drop(s);               // 想手動 drop 掉 s
    println!("{}", r);     // 還想用 r —— 這在 C 就是 UAF
}
```

編譯：

```
error[E0505]: cannot move out of `s` because it is borrowed
 --> src/main.rs:4:10
  |
2 |     let s = String::from("owned data");
  |         - binding `s` declared here
3 |     let r = &s;            // 借用 s
  |             -- borrow of `s` occurs here
4 |     drop(s);               // 想手動 drop 掉 s
  |          ^ move out of `s` occurs here
5 |     println!("{}", r);     // 還想用 r —— 這在 C 就是 UAF
  |                    - borrow later used here
```

（本機真跑，`rustc 1.97.1`。）在 C 裡這段會編過、會 run、可能還印出看起來正常的舊值——然後在某個生產環境的某個時刻爆掉。Rust 把它擋在編譯期，訊息還直接告訴你「borrow later used here」。這就是招牌成就的本體。

## 但「記憶體安全 ≠ 沒有漏洞」

現在講框內那行小字。這是本章最重要的部分，也是資安人最容易被「Rust 很安全」這句話麻痺的地方。**safe Rust 消掉的是記憶體安全 UB，不是漏洞。** 下面逐類看它沒管的東西——能跑的都真跑給你看。

### 整數溢位：debug panic，release 靜默 wrapping

這是最經典、最該親手跑一遍的例子。Rust 的整數溢位行為是**組態相依**的，而這個差異能直接變成邏輯漏洞。

先看行為。用 `std::hint::black_box` 把值藏過編譯期的常數溢位 lint（否則 `200u8 + 100u8` 這種編譯期能算出來的會被 `#[deny(arithmetic_overflow)]` 直接擋下，看不到 runtime 行為）：

```rust
use std::hint::black_box;

fn main() {
    // black_box 讓編譯器看不出這兩個值，於是走 runtime 溢位行為
    let a: u8 = black_box(200);
    let b: u8 = black_box(100);
    let c = a + b; // 300，超過 u8 上限 255
    println!("a + b = {}", c);
}
```

同一份 code，兩種 build，兩種結果：

```
--- debug run (cargo run) ---
thread 'main' (280393) panicked at src/main.rs:8:13:
attempt to add with overflow

--- release run (cargo run --release) ---
a + b = 44
```

（本機真跑。）看清楚：

- **debug** 模式下，溢位會 **panic**（`overflow-checks` 預設開）。程式當場掛掉——這是一種 DoS，但至少不會給你錯的值。
- **release** 模式下，溢位**靜默 wrapping**（`300 mod 256 = 44`），程式繼續跑，帶著一個錯的值往下走。

release 的 wrapping **不是 UB**——它是「two's complement wrapping」，行為完全定義好（這點 Rust 比 C 的有號溢位 UB 好：C 的 `signed` 溢位是 UB，Rust 明確定義為 wrapping）。但「定義好」不等於「安全」。想像這是一段付款程式：

```rust
use std::hint::black_box;

fn main() {
    let price: u32 = black_box(10);
    let qty: u32 = black_box(500_000_000); // 五億件
    let total = price * qty;               // 5e9 > u32::MAX (約 42.9 億)
    println!("total = {}", total);
    println!("u32::MAX = {}", u32::MAX);
}
```

release 執行：

```
total = 705032704
u32::MAX = 4294967295
```

（本機真跑，release。）本該收 50 億的訂單，wrapping 之後只收 7 億。這是一個純粹的**邏輯漏洞**，記憶體安全一點問題都沒有，borrow checker 一個字都不會說。正確做法是 `checked_mul`（回傳 `Option`，溢位給 `None`）或 `saturating_mul`。這類 bug 在真實世界出過事——`http` crate 的 `HeaderMap::reserve()` 就有過一個容量計算 `next_power_of_two()` 在 release 溢位到 0、造成 DoS 的漏洞（RUSTSEC-2019-0033，見延伸閱讀），它明確不是記憶體損毀，是邏輯溢位。

> 這裡有個認識論誠實要標：release 預設 `overflow-checks = false`，但你**可以**在 `Cargo.toml` 的 `[profile.release]` 加 `overflow-checks = true` 把 release 也變成 panic。所以「release 一定 wrapping」是預設值，不是語言鐵律。稽核別人的 release binary 時，這個 profile 設定要確認。

### panic / unwrap 濫用：可觸發的 DoS

Rust 的錯誤處理有兩條路：`Result`（可恢復）和 `panic!`（不可恢復，直接 unwind 或 abort）。`.unwrap()` / `.expect()` 是「我賭這不會是 `None`/`Err`，賭輸就 panic」。

```rust
fn parse_port(s: &str) -> u16 {
    s.parse().unwrap() // 賭 s 一定是合法的 u16
}

fn main() {
    let user_input = "not_a_number"; // 想像這來自網路請求
    let port = parse_port(user_input);
    println!("{}", port);
}
```

執行：

```
thread 'main' (283915) panicked at src/main.rs:2:14:
called `Result::unwrap()` on an `Err` value: ParseIntError { kind: InvalidDigit }
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
```

（本機真跑。）如果 `parse_port` 的輸入來自攻擊者（HTTP header、封包欄位），攻擊者送一個非法字串就能讓這個 thread panic。在一個 server 裡，一個沒被 catch 的 panic 輕則掐掉一條連線、重則整個 process 倒掉——這是一個**遠端可觸發的 DoS**。它完全是記憶體安全的：沒有 UB、沒有記憶體損毀，只是程式**停了**。稽核 Rust 服務時，「使用者可控輸入路徑上有沒有 `unwrap`/`expect`/直接陣列下標」是必查項。

「重則整個 process 倒掉」取決於一個關鍵設定：**panic 策略是 unwind 還是 abort**。這個差異直接決定攻擊者能不能把「一條連線掛掉」升級成「整個服務掛掉」，值得真跑一次看清楚：

```rust
use std::panic;

fn main() {
    // catch_unwind 能攔截 panic —— 但只在 unwind 模式下有效
    let result = panic::catch_unwind(|| {
        let v = vec![1, 2, 3];
        v[10] // 越界 -> panic
    });
    match result {
        Ok(x) => println!("got {}", x),
        Err(_) => println!("caught a panic, process survives"),
    }
    println!("still running");
}
```

預設策略是 **unwind**（panic 時逐層展開堆疊、跑 destructor），`catch_unwind` 能攔住它：

```
--- 預設 (panic = unwind) ---
thread 'main' panicked at src/main.rs:7:10:
index out of bounds: the len is 3 but the index is 10
caught a panic, process survives
still running
```

（本機真跑。）panic 被攔下，process 活著繼續跑。web framework（`actix`、`axum` 等）就是靠這個把「一個 handler panic」隔離成「這一條請求 500」，不影響其他連線。

但如果 `Cargo.toml` 設了 `panic = "abort"`（很多 embedded/追求體積的專案會這樣設，因為省掉 unwinding 的程式碼），同一份 code：

```
--- panic = "abort" ---
thread 'main' panicked at src/main.rs:7:10:
index out of bounds: the len is 3 but the index is 10
（process 直接被 SIGABRT 殺掉，"caught a panic" 與 "still running" 都不會印）
```

（本機真跑。）`catch_unwind` **完全無效**——abort 模式下 panic 直接呼叫 `abort()` 殺整個 process，沒有攔截機會。稽核時這是必問的一題：**這個服務的 panic 策略是什麼？** abort 模式下，任何一個使用者可觸發的 panic 都是整個 process 的 DoS，嚴重度比 unwind 模式高一個等級。

### 資源耗盡：memory-safe 也能打爆記憶體

```rust
fn main() {
    let n: usize = 100_000_000_000; // 假設來自請求的一個長度欄位
    let buf: Vec<u8> = Vec::with_capacity(n); // 想配 100 GB
    println!("{}", buf.capacity());
}
```

這段 code 型別完全正確、記憶體完全安全，但它會嘗試配一塊你機器上根本沒有的記憶體，結果是配置失敗 → abort（或在有 overcommit 的系統上被 OOM killer 收掉）。攻擊者控制 `n`，就能用一個小小的請求觸發一次記憶體耗盡。這類「用可控的 size/count 去驅動配置」是 memory-safe 語言一樣中招的 DoS，Rust 不會幫你擋。

### 應用層漏洞：SQL injection、SSRF、path traversal

這一類最該講清楚，因為它跟語言完全無關。你在 Rust 裡照樣可以：

```rust
// 示意（非可跑，需要 DB 連線）：字串拼接 SQL = injection
let query = format!("SELECT * FROM users WHERE name = '{}'", user_input);
// 使用者送 user_input = "'; DROP TABLE users; --" 就中了
```

SQL injection、SSRF（拿使用者給的 URL 直接去 fetch）、path traversal（拿使用者給的檔名去開檔）、命令注入、XXE——這些是**應用邏輯層**的漏洞，它們跟「記憶體怎麼管」是兩個宇宙。Rust 的型別系統對「這個字串是不是被污染的使用者輸入」一無所知（沒有內建 taint tracking）。防這些的方法跟在別的語言一樣：parameterized query、allowlist、正規化路徑。**寫 Rust 不會讓你自動免疫這些。**

### side channel：時序、快取

密碼學相關的 side channel（timing attack、cache attack）在 Rust 裡一樣存在。`==` 比較兩個 `&[u8]` 是短路的（第一個 byte 不同就回傳），拿它比對 MAC/token 就有 timing leak。Rust 沒有魔法讓比較變成常數時間——你得用 `subtle` 這類 crate 的 `ConstantTimeEq`。記憶體安全跟時序安全是正交的。

把這一節收斂成一張表——這是你 audit 一個 Rust 專案時該貼在牆上的分類：

| 漏洞類別 | safe Rust 管嗎？ | 為什麼 |
|---|---|---|
| UAF / double-free / OOB / 未初始化 | **管**（safe 側消掉） | borrow checker + ownership + bounds check |
| data race | **管**（safe 側消掉） | `Send`/`Sync` + borrow checker |
| 整數溢位造成的邏輯錯誤 | **不管** | release 靜默 wrapping（非 UB，但值錯） |
| panic / DoS | **不管** | `unwrap`/越界/`assert` 可被輸入觸發 |
| 資源耗盡（OOM/檔案數/連線數） | **不管** | 型別系統不限制配置量 |
| 注入（SQL/命令/path/SSRF/XXE） | **不管** | 無內建 taint tracking |
| side channel（timing/cache） | **不管** | 記憶體安全與時序安全正交 |
| 邏輯漏洞（授權繞過、狀態機錯誤） | **不管** | 這是應用邏輯，不是語言層 |

一句話：**Rust 把「記憶體安全 UB」這個類別（表格上半）從你的威脅模型裡幾乎刪掉了，但威脅模型裡剩下的每一格（表格下半）——邏輯、可用性、注入、side channel——它一格都沒動。** 稽核時，上半你可以（相對地）放心，下半你要跟在任何語言一樣認真守。

## 威脅模型：四條線逐條看

回到那張信任邊界圖。招牌成就守住的是「你寫的 safe Rust」那個大框內部。攻擊者真正的入口是**框外的四條線**。這四條線就是 Rust 專案威脅模型的骨架，也是 Part 5 後三章的地圖。

### 線一：safe / unsafe 邊界

`unsafe` 區塊裡，borrow checker 的某些檢查被關掉（可以解引用裸指標、呼叫 unsafe 函式等，五種 superpower 見 [Ch 17](./17-unsafe-basics.md)）。**這一區重新引入了記憶體安全 UB 的可能性。** 關鍵在：一段 unsafe 的 bug 不一定停在 unsafe 內部——如果它被包在一個安全的 API 裡，而這個 API 沒維持好它承諾的 invariant，那麼**完全 safe 的呼叫者**就能觸發 UB。這叫 **unsound**，是 Ch 31 的主題。這條線的 audit 面積是「專案裡所有 `unsafe` 關鍵字」，通常遠小於整個 codebase。

### 線二：FFI 邊界

`extern "C"` 那一刻，你就跨進了 C 的宇宙（[Ch 19](./19-ffi.md)）。對面那塊 C 程式碼**沒有任何 Rust 的保證**——它可能 UAF、可能回傳 dangling 指標、可能對你傳過去的 buffer 越界寫。Rust 端只能**信任你在 `unsafe extern` 宣告裡寫的型別簽章是對的**。FFI 是把「別人的、無保證的 C」引進來的官方通道，任何跟 C library 綁定的 Rust 專案，這條線都是實打實的攻擊面。

### 線三：`build.rs` 與 proc-macro —— 編譯期任意執行

這條線是很多資安人一開始會忽略、但威脅等級極高的一條。

`build.rs` 是一個 build script：**它在你 `cargo build` 時，以你的使用者身分，在你的機器上，執行任意 Rust 程式碼。** proc-macro 也一樣——它是編譯期執行的程式碼。這意味著：

```
你以為的：cargo build 只是「編譯」。
實際上：  cargo build = 下載一堆 crate + 執行它們每一個的 build.rs
          + 展開它們每一個的 proc-macro，全部用你的權限，在你的機器上。
```

一個惡意 crate 的 `build.rs` 可以偷你的 SSH key、植入後門、往 CI 的 artifact 塞東西——而這一切發生在**編譯期**，你的 runtime sandbox（如果有）根本管不到。這不是理論：供應鏈攻擊的核心手法之一就是「污染一個熱門 crate 或它的某個 transitive 依賴」。所以「我只是 build 一下，又沒 run」在 Rust（跟 npm、pip 一樣）是**錯的安全假設**。這條線是 Ch 32 供應鏈稽核的核心動機。

### 線四：依賴樹

現代 Rust 專案動輒上百個 transitive 依賴。**上面三條線（unsafe、FFI、build.rs）在你的每一個依賴、以及依賴的依賴裡，全部重新出現一次。** 你信任的不是「這個直接依賴的作者」，而是「整棵依賴樹上每一個 crate 的每一個作者，過去與未來」。一個被盜的 crates.io 帳號、一個被 typosquat 的套件名、一個維護者跑路後被人接手投毒的套件——每一個都是這條線上的洞。

把四條線疊起來，你的 audit 策略就出來了：**盯 unsafe（Ch 31 教怎麼判 sound）、盯 FFI 邊界、盯 build.rs/proc-macro、盯依賴樹的已知漏洞與 unsafe 用量（Ch 32 教工具）。** safe Rust 那個大框內部，你可以（相對地）少花力氣——這正是「攻擊面縮小、audit 面積變小」的實際兌現。

## 對比：C 的信任邊界圖長什麼樣？

把同一張圖畫成 C 的版本，你就懂 Rust 到底幫你省了什麼：

```
       ┌─────────────────────────────────────────────┐
       │              你寫的 C 程式碼                  │
       │  整片都是「unsafe」——每一行都可能 UAF /       │
       │  OOB / double-free / data race / 未初始化讀   │
       │  ★沒有語法標記告訴你「危險在這幾行」★         │
       └─────────────────────────────────────────────┘
       + FFI（跟 Rust 一樣）
       + 依賴（跟 Rust 一樣，但沒有 crates.io + advisory-db 這套統一稽核）
       + Makefile / configure 腳本（等同 build.rs 的編譯期執行風險）
```

差別不在「有沒有破口」——兩邊都有 FFI、都有依賴、都有編譯期腳本。差別在**記憶體安全 UB 的分佈**：

- **C**：整個 codebase 都是紅色。你 audit 記憶體安全，理論上要看**每一行**。工具（ASan、valgrind、靜態分析）幫你逼近，但沒有一個能給你「這段保證沒有記憶體 UB」的確定答案。
- **Rust**：safe 那一大片是綠色（型別系統保證），紅色被壓縮到 `unsafe` 關鍵字圈起來的那幾百行。你 audit 記憶體安全，只要看那幾百行加依賴樹。

這就是「攻擊面縮小」的精確意思：不是漏洞歸零，是**記憶體安全風險從「全域、不可見」變成「局部、有標記、可定位」**。這種「可定位」不是口頭說說——`cargo geiger` 能直接把依賴樹裡每個 crate 的 unsafe 用量數出來（Ch 32），這在 C 的世界沒有對應工具，因為 C 沒有「unsafe 標記」可數。

## 誠實：Rust 縮小了多少攻擊面？（引數據）

口號要有數字撐。以下是可查證的來源，不是我編的。

Google 在 2024 年 9 月的官方安全部落格〈Eliminating Memory Safety Vulnerabilities at the Source〉裡公布：**Android 的記憶體安全漏洞佔比，從 2019 年的 76% 掉到 2024 年的 24%**（同期記憶體安全漏洞的絕對數量從 223 個降到不到 50 個）。他們把這歸功於一個策略：**新程式碼優先用 memory-safe 語言（Rust 為主）寫**——注意，不是把舊 C/C++ 全部改寫，而是「新增的部分用安全語言」，讓記憶體安全漏洞隨時間自然衰減。Google 也提到，記憶體安全漏洞曾在 Chromium 觀察到約 70% 的佔比（即多數瀏覽器漏洞是記憶體安全問題），是業界的普遍基線。

怎麼讀這個數字，決定你是不是真的懂：

- **它證明了什麼**：在大型真實專案裡，把新程式碼換成 memory-safe 語言，能讓記憶體安全漏洞這個**類別**大幅衰減。這是招牌成就的實證，不是行銷。
- **它沒證明什麼**：那掉下去的 76%→24% 是**記憶體安全**那一類漏洞。剩下的 24%（以及邏輯、DoS、注入等**非**記憶體安全漏洞）Rust 沒碰。漏洞總量不會歸零，只是換了組成。
- **陷阱**：不要拿這個數字宣稱「Rust 專案更安全 X%」。它說的是「記憶體安全漏洞佔比下降」，不是「總漏洞下降 X%」，更不是「你這個 Rust 專案安全」。你這個專案安不安全，取決於你怎麼處理本章那四條線。

一句總結這一節：**Rust 大幅縮小了記憶體安全攻擊面（有 Google 的真實數據撐腰），但它不是銀彈——它把你的注意力從「到處都可能記憶體損毀」解放出來，好讓你專心對付剩下那些它管不到的漏洞。**

## 踩雷集錦

1. **「Rust 是安全的，所以我的 Rust 專案安全」**：錯把「記憶體安全」當「安全」。記憶體安全是一個**類別**，不是全部。你的專案裡的邏輯漏洞、`unwrap` DoS、SQL injection、SSRF，Rust 一個都不管。正確認識：Rust 縮小了一塊攻擊面，剩下的攻擊面你照樣要自己守。

2. **「有 unsafe 就跟 C 一樣不安全」**：錯把「有破口」當「沒有保證」。差別在**面積**——C 你要盯全部，Rust 你盯 unsafe + FFI + 依賴。把 audit 面積從「整個 codebase」縮到「幾百行 unsafe」是巨大的實務差異，不是「一樣爛」。

3. **「release 的整數溢位是 UB」**：不是。release 的整數溢位是**明確定義的 two's complement wrapping**（這點比 C 的有號溢位 UB 好），不是 UB、不會記憶體損毀。但它會給你**錯的值**，造成邏輯漏洞。危險等級是「邏輯錯誤」而非「記憶體損毀」，但別因此掉以輕心——付款金額算錯照樣是事故。

4. **「cargo build 只是編譯，很安全，我還沒 run」**：大錯。`build.rs` 與 proc-macro 在**編譯期以你的權限執行任意程式碼**。`cargo build` 一個含惡意依賴的專案，等於在你機器上跑了那個依賴的任意程式碼。「build 不 run」在 Rust 不成立，跟 `npm install` 會跑 postinstall script 是同一類風險。

5. **「依賴只要看直接依賴就好」**：漏掉 transitive 依賴。上百個 transitive 依賴裡任何一個的 unsafe/FFI/build.rs 都在你的信任邊界內。攻擊者最愛打的正是「深藏在依賴樹底層、沒人在看」的那個小 crate。

## 進階：再往深一層

**能力（capability）視角看 Rust 的邊界。** 一個更精確的思考框架：把每一條線看成「這裡授予了某種 capability」。safe Rust 給的 capability 受型別系統約束（你不能拿到一個懸空的 `&T`）；`unsafe` 授予「解除某些約束」的 capability；FFI 授予「呼叫外部無約束程式碼」的 capability；`build.rs` 授予「編譯期任意執行」的 capability。安全審計本質上就是**盤點每一條授予危險 capability 的線，確認它被正確約束**。Rust 的價值在於它把大部分危險 capability 用 `unsafe`/`extern` 這些關鍵字**顯性標記**出來——grep 得到，就 audit 得到。

**Rust 的安全宣稱有沒有被形式化證明？** 部分有。RustBelt 專案（見延伸閱讀）用 Coq 對 Rust 型別系統的一個核心子集（λRust）做了形式化的 soundness 證明，並且證明了 `Rc`、`RefCell`、`Mutex` 等標準庫的 unsafe 抽象在他們的模型下是 sound 的。這是「safe Rust 保證記憶體安全」這句話目前最接近數學證明的支撐。但它是**模型層級**的證明，不是「rustc 這個實作沒 bug」的證明——編譯器本身還是可能有實作漏洞（歷史上有過 soundness bug 的 issue）。認識論誠實：保證來自「型別系統設計 + 部分形式化證明 + 大量實務驗證」，不是「rustc 被完全驗證過」。

## 動手練習

1. 把整數溢位那個付款範例，改用 `checked_mul` 重寫，讓它在溢位時回傳 `None` 而不是給錯的值。跑一次確認 500_000_000 那組輸入現在得到 `None`。

2. 找一個你手邊的 Rust 專案（或 `cargo new` 一個加幾個依賴），跑 `cargo tree`，數一數 transitive 依賴有幾個。想一想：這幾個 crate 的作者你認識幾個？它們裡面有幾個有 `build.rs`？（下一章不做這個，Ch 32 的 `cargo geiger`/`cargo audit` 會把這件事自動化。）

3. 挑本章列的五類「非記憶體安全漏洞」（整數溢位、panic DoS、資源耗盡、注入、side channel），對你正在做或看過的一個 Rust 專案，各想一個「這個專案哪裡可能中這一類」。這個練習逼你把「Rust 很安全」的直覺換成「這張威脅模型圖」的直覺。

## 本章重點整理

- safe Rust 保證的是**記憶體安全 UB 那一類**消失（UAF/double-free/OOB/未初始化/iterator invalidation/data race），這是 borrow checker + 型別系統的成果，有 Google Android 76%→24% 的真實數據撐腰。
- **記憶體安全 ≠ 沒有漏洞**：邏輯漏洞、panic/DoS、整數溢位（release 靜默 wrapping 造成邏輯錯誤）、資源耗盡、注入、side channel，Rust 一個都不管。
- 威脅模型的骨架是**四條線**：safe/unsafe 邊界（Ch 31）、FFI 邊界（Ch 19）、`build.rs`/proc-macro 的編譯期任意執行、依賴樹。攻擊面沒消失，只是被**顯性標記、可定位**，audit 面積從「整個 codebase」縮到「unsafe + 依賴」。
- Rust 不是銀彈；它把你從「到處記憶體損毀」解放出來，好讓你專心對付它管不到的那些漏洞。

## 自我檢核

- [ ] 不看筆記，能不能畫出那張「Rust 專案信任邊界」圖，並說出四條線各是什麼、對應後面哪一章？
- [ ] 如果面試官問「Rust 是記憶體安全的，所以 Rust 專案很安全，對嗎？」，能不能給出「第三種答案」——既不吹捧也不貶低，精確講清楚縮小了哪塊、沒碰哪塊？
- [ ] 能不能解釋「release 整數溢位不是 UB 但仍是漏洞」這句話為什麼兩個子句都對？
- [ ] 能不能說清楚為什麼「cargo build 但沒 run 所以很安全」是錯的？
- [ ] 引 Android 76%→24% 這個數字時，能不能同時說清楚它**證明了什麼、沒證明什麼**？

## 延伸閱讀

### 官方 / 權威來源

- **[Google Online Security Blog:〈Eliminating Memory Safety Vulnerabilities at the Source〉](https://security.googleblog.com/2024/09/eliminating-memory-safety-vulnerabilities-Android.html)** — Jeff Vander Stoep & Alex Rebert（Google，2024/09）
  - **這篇說什麼**：本章 76%→24% 那組數據的第一手來源。核心論點是「新程式碼用 memory-safe 語言」比「改寫舊程式碼」更有效地衰減記憶體安全漏洞，並用 Android 的實測數據支撐。
  - **讀哪裡**：整篇不長；重點看那張漏洞佔比隨年份下降的折線圖，以及「Safe Coding」那一段的論證。
  - **和本章的關聯**：本章「誠實：縮小了多少」那節完全依據這篇。引用數字時務必連它「證明什麼／沒證明什麼」一起讀。

- **[The Rustonomicon —〈Meet Safe and Unsafe〉](https://doc.rust-lang.org/nomicon/meet-safe-and-unsafe.html)** — Rust 官方
  - **讀哪裡**：「Meet Safe and Unsafe」與「Working with Unsafe」兩節。
  - **能學到什麼**：把本章「safe/unsafe 邊界」那條線講到語言規範層級——safe 到底是什麼、unsafe 解除了哪些約束。Ch 31 的地基。
  - **前提**：讀過 [Ch 17](./17-unsafe-basics.md) 的五種 superpower。

### 論文

- **[RustBelt: Securing the Foundations of the Rust Programming Language](https://plv.mpi-sws.org/rustbelt/popl18/paper.pdf)** — Jung, Jourdan, Krebbers, Dreyer（POPL 2018）
  - **核心貢獻**：第一個對 Rust 型別系統核心（λRust）做**機器驗證的 soundness 證明**，並證明 `Rc`/`RefCell`/`Mutex` 等標準庫 unsafe 抽象在其模型下 sound。
  - **讀哪裡**：Section 1（intro，講清楚「為什麼 unsafe 的存在讓『Rust 安全』這句話需要證明」）與 Section 2（λRust 概觀）。後面的 Iris/separation logic 很數學，資安向讀者可跳過細節、只抓結論。
  - **和本章的關聯**：本章「進階」提到的「Rust 的安全宣稱有沒有被證明」就是這篇。它是「型別系統設計 → 記憶體安全保證」這條因果鏈最硬的支撐。

### RUSTSEC 案例（非記憶體安全漏洞的真實例子）

- **[RUSTSEC-2019-0033: Integer overflow in HeaderMap::reserve()](https://rustsec.org/advisories/RUSTSEC-2019-0033.html)** — `http` crate
  - **讀什麼**：advisory 正文對 `next_power_of_two()` 在 release 溢位到 0、造成無限探測 DoS 的描述。
  - **和本章的關聯**：本章「整數溢位」那節說的「wrapping 造成邏輯漏洞、且明確不是記憶體損毀」，這是真實世界的實例。它是 DoS，不是 memory corruption——正好印證「記憶體安全 ≠ 沒漏洞」。

### 書籍

- **《Rust for Rustaceans》第 9 章 Unsafe** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust 最佳單本書，和本課定位幾乎重合。
  - **讀哪幾章**：第 9 章把 safe/unsafe 邊界、invariant、soundness 講得比多數資料清楚；讀完接 Ch 31 剛好。

下一章我們把鏡頭拉近到「線一：safe/unsafe 邊界」——看一段 unsafe 怎麼把記憶體安全 bug 重新引進來，怎麼用 Miri 抓，並對照真實的 RUSTSEC advisory 編號。

→ [Ch 31 unsafe 漏洞類與 RUSTSEC 案例](./31-unsafe-vuln-classes.md)
