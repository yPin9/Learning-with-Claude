# Rust 學習筆記：給 C/C++ 系統與資安工程師的現代系統語言

> 給已經懂 C/C++20、做過 kernel/pwn/RE，想把 Rust 納入系統與資安武器庫的工程師。**不是新手課**。

這門課不教你「Rust 是什麼、變數怎麼宣告」——那些你看 The Book 半天就會。它教的是：ownership/borrow/lifetime 底層到底在管什麼、unsafe 的真實邊界在哪、記憶體佈局與 FFI 怎麼跟 C ABI 對齊、async 狀態機怎麼編譯出來、Rust binary 在反組譯器裡長什麼樣、怎麼 audit 別人的 unsafe，最後用 Rust-for-Linux 寫一個真的、在 QEMU 跑得起來的 kernel module。

全程用 **C/C++ 對照**當鷹架：你已經有的記憶體模型、RAII、move 語意、vtable、memory_order 直覺，這裡全部接得上。

## 為什麼學這個？

- **系統語言的世代交替正在發生**：Rust-for-Linux 進了主線，Android/Windows kernel 都在引入 Rust，新的 systems tooling（ripgrep、uv、Zed、大量 CNCF 專案）幾乎清一色 Rust。不會 Rust 的系統工程師，五年內會像今天不會 Git 的人。
- **它逼你把 C 的隱性知識顯性化**：borrow checker 擋掉的每一個錯，都對應一類你在 C 裡靠紀律避免的 bug（UAF、iterator invalidation、data race）。學 Rust 會讓你的 C 也變強。
- **資安雙向價值**：防守方——理解 memory-safe 語言的保證與**破口**（unsafe/邏輯漏洞）；攻擊方——紅隊工具鏈正在集體轉 Rust，逆向 Rust binary、audit unsafe crate 是新的必備技能。

## 先修知識

- **C**（熟）：指標、記憶體佈局、UB、stack/heap、ABI。這是硬需求。
- **C++**（會讀寫）：RAII、move 語意、smart pointer、template、vtable、`std::atomic`。我們大量拿它對照；不熟也能跟，但會少一根鷹架。
- **作業系統與底層**（有概念）：virtual memory、thread、syscall、epoll。Part 4/6 會用到；本 repo 的 `systems/kernel_internals`、`programming/c_interview` 是理想前置。
- 沒有也沒關係：Rust 本身（我們從零建，但節奏快）、async 經驗、kernel module 開發經驗。

## 課程地圖

### Part 0 — 定位（Ch 0–1）
- [Ch 0 環境搭建與 C/C++ 對照心智](./00-environment-setup.md)
- [Ch 1 為什麼是 Rust：給 C/C++ 人的定位](./01-why-rust.md)

### Part 1 — 所有權模型（Ch 2–7）
- [Ch 2 Ownership 與 move 語意](./02-ownership-move.md)
- [Ch 3 Borrowing：& / &mut 與別名規則](./03-borrowing-references.md)
- [Ch 4 Lifetimes：借用的存活期](./04-lifetimes.md)
- [Ch 5 Lifetime 進階：elision / HRTB / variance](./05-lifetimes-advanced.md)
- [Ch 6 Slice、str 與 String：胖指標佈局](./06-slices-str-string.md)
- [Ch 7 borrow checker 底層：NLL 與 Polonius](./07-borrow-checker-internals.md)
- [練習 A：把 C 資料結構改寫成 Rust](./practice-a-c-to-rust.md)

### Part 2 — 型別系統與抽象（Ch 8–14）
- [Ch 8 Struct、Enum 與 Pattern Matching](./08-struct-enum-pattern.md)
- [Ch 9 Trait：Rust 的抽象核心](./09-traits.md)
- [Ch 10 泛型與單型化](./10-generics-monomorphization.md)
- [Ch 11 Trait Object 與動態分派](./11-trait-objects-dispatch.md)
- [Ch 12 核心 trait：Deref/Drop/Copy/Iterator](./12-core-traits.md)
- [Ch 13 錯誤處理：Result/Option/?](./13-error-handling.md)
- [Ch 14 閉包：Fn / FnMut / FnOnce](./14-closures.md)
- [練習 B：泛型資料結構與自訂 trait](./practice-b-generic-iterator.md)

### Part 3 — 記憶體佈局與 unsafe（Ch 15–22）
- [Ch 15 記憶體佈局：repr 與 niche optimization](./15-memory-layout.md)
- [Ch 16 智慧指標底層：Box/Rc/Arc/RefCell](./16-smart-pointers.md)
- [Ch 17 unsafe 基礎：五種 superpower](./17-unsafe-basics.md)
- [Ch 18 unsafe 進階：transmute/MaybeUninit/union](./18-unsafe-advanced.md)
- [Ch 19 FFI：與 C 互操作與安全 wrapper](./19-ffi.md)
- [Ch 20 記憶體模型與 UB：Stacked/Tree Borrows、Miri](./20-memory-model-ub.md)
- [Ch 21 手刻 unsafe 抽象：安全的 Vec](./21-unsafe-abstractions.md)
- [Ch 22 no_std：embedded 與 kernel 場景](./22-no-std.md)
- [練習 C：用 Miri 抓 UB 與包一個 C library](./practice-c-miri-ffi.md)

### Part 4 — 並發與非同步（Ch 23–29）
- [Ch 23 執行緒與 Send/Sync](./23-threads-send-sync.md)
- [Ch 24 共享狀態：Mutex/RwLock/Arc](./24-shared-state.md)
- [Ch 25 無鎖與 atomics：Ordering](./25-atomics-lockfree.md)
- [Ch 26 async 原理一：Future 與 poll](./26-async-futures.md)
- [Ch 27 async 原理二：executor/Waker/Pin](./27-async-executor-pin.md)
- [Ch 28 Tokio 實戰與 epoll 連結](./28-tokio.md)
- [Ch 29 async 陷阱：cancellation 與 !Send](./29-async-pitfalls.md)
- [練習 D：手刻 mini async executor](./practice-d-mini-executor.md)

### Part 5 — 資安研究向（Ch 30–36）
- [Ch 30 Rust 的安全邊界與威脅模型](./30-security-boundary.md)
- [Ch 31 unsafe 漏洞類與 RUSTSEC 案例](./31-unsafe-vuln-classes.md)
- [Ch 32 audit unsafe：cargo-geiger/cargo-audit](./32-audit-unsafe.md)
- [Ch 33 逆向 Rust binary：特徵與 mangling](./33-reversing-rust-binary.md)
- [Ch 34 Rust binary 內部：組語樣貌](./34-rust-binary-internals.md)
- [Ch 35 用 Rust 寫資安工具](./35-rust-security-tooling.md)
- [Ch 36 Fuzzing Rust：cargo-fuzz/AFL++](./36-fuzzing-rust.md)
- [練習 E：逆向 Rust binary 與 audit unsafe crate](./practice-e-reverse-audit.md)

### Part 6 — Rust-for-Linux 與 kernel（Ch 37–42）
- [Ch 37 Rust-for-Linux 概覽](./37-rust-for-linux-overview.md)
- [Ch 38 kernel 抽象：kernel crate 與 pin-init](./38-kernel-abstractions.md)
- [Ch 39 第一個 Rust kernel module](./39-first-kernel-module.md)
- [Ch 40 Rust driver：字元/misc device](./40-rust-driver.md)
- [Ch 41 kernel unsafe 與安全性](./41-kernel-unsafe-safety.md)
- [Ch 42 生態與未來](./42-ecosystem-future.md)
- [Final Project：用 Rust-for-Linux 寫字元裝置 kernel module](./final-project-kernel-module.md)

## 學習方式建議

1. **每章都跑**：本課全程在 Linux（WSL2 亦可）用 `rustc 1.97` / nightly 驗證。裝好 `rustup`、`cargo`、nightly + Miri。
2. **故意觸怒 borrow checker**：把書上能編過的 code 改到編不過，讀懂它的錯誤訊息——`rustc` 的錯誤訊息是全業界最好的教材之一。
3. **用 C 對照**：每學一個機制，先問「這在 C 裡我怎麼做、會出什麼錯」，再看 Rust 怎麼在編譯期擋掉。
4. **開 Miri 與反組譯**：Part 3 之後養成 `cargo +nightly miri run` 抓 UB、`objdump`/Ghidra 看生成碼的習慣。

## 精選資料庫

### 必讀基礎

- **《The Rust Programming Language》(The Book)** — Steve Klabnik & Carol Nichols（線上免費 doc.rust-lang.org/book）
  - 官方入門書。本課節奏太快時的補課來源；Ch 4（ownership）、Ch 10（泛型/trait/lifetime）、Ch 15（smart pointer）與本課 Part 1–3 直接對應。
- **《The Rustonomicon》** — 官方（doc.rust-lang.org/nomicon）
  - unsafe Rust 的權威文件，本課 Part 3 的主要參考；講 UB、變異數（variance）、drop 順序、如何寫健全的 unsafe 抽象。

### 推薦書籍

- **《Rust for Rustaceans》** — Jon Gjengset（No Starch Press, 2021）
  - 中階 Rust 的最佳單本書；lifetime variance、trait 物件、unsafe、async 都有深度章節，和本課定位幾乎重合。
- **《Programming Rust, 2nd ed.》** — Blandy, Orendorff, Tindall（O'Reilly, 2021）
  - 系統向、對 C++ 使用者友善，記憶體佈局與並發章節很紮實。

### 官方文件 / Spec

- **[Rust Reference](https://doc.rust-lang.org/reference/)** — 語言參考手冊，`repr`、記憶體佈局、UB 定義的最終仲裁。
- **[Rust-for-Linux 官網與 kernel 文件](https://rust-for-linux.com/)** — Part 6 與 final 的主要依據；`Documentation/rust/` 是 kernel 內的第一手資料。

### 讀完本課之後

- **[std 原始碼](https://github.com/rust-lang/rust/tree/master/library)**（讀 `Vec`、`Arc`、`Rc` 的實作——本課教你怎麼讀）
- **本 repo 的 `systems/kernel_internals`**（Rust driver 背後的 C 那半邊）與 **`security/afl_plus_plus`**（Fuzzing Rust 的引擎那半邊）
