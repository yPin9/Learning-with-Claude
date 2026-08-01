# Ch 37 — Rust-for-Linux 概覽

> **目標**：搞清楚 Rust-for-Linux（下稱 RfL）到底是什麼、進主線的時間線與現況、Rust 在 kernel 裡的**架構位置**（它包在哪一層、不碰哪一層）、為什麼 kernel 要引入 Rust（用 memory-safety CVE 的真實數據論證）、為什麼**不是**全部重寫，以及你要動手前需要的 toolchain 與 build 環境。學完你會有一張「C core ↔ bindgen ↔ kernel crate ↔ Rust driver」的心智圖，知道 Part 6 接下來三章要把哪一塊拆開看。

> **環境考據**：RfL 的版本與里程碑一直在動，本章所有版本／時間斷言都標了來源與參照時點（**2026-08 查證**）。凡「未實測」的（完整 kernel build、跑在真機）都明講，正確驗證環境與步驟見 [Ch 39](./39-first-kernel-module.md)。本機（WSL2，`rustc 1.97.1` stable + `1.99.0-nightly`）能驗的是純 Rust 片段，本章末尾那段錯誤映射示範就是本機真跑的。

## 為什麼需要這個？

你做過 kernel_pwn，讀過 Linux 源碼（本 repo `systems/kernel_internals`）。那你比誰都清楚一件事：**kernel 是 C 寫的，而 C 在 kernel 這種規模下，記憶體安全是靠人的紀律撐著的**。一個 `kfree` 之後還有人持有指標（use-after-free）、一個 refcount 少 `put` 一次（泄漏）或多 `put` 一次（提前釋放）、兩條路徑對同一個 `list_head` 沒拿鎖就動（race）——這些就是你平常在打的洞。它們不是「粗心」，是 C 這個語言**結構上沒有能力**在編譯期擋掉這類錯。

歷史上 kernel 對這件事的回應是「更多工具」：KASAN（抓 UAF/越界）、KCSAN（抓 data race）、lockdep（抓死鎖）、syzkaller（fuzzing）、無數的 `sparse` annotation。這些都是**執行期或動態分析**——它們在 bug 已經被寫進去、跑到那條路徑時才抓到。Rust 的提案是換個層次：**讓一整類 bug 在編譯期就無法被表達**。ownership 擋 UAF、borrow 擋 aliasing race、`Drop` 擋 refcount 泄漏、型別擋「拿到沒初始化的記憶體」。

RfL 就是「把 Rust 這套編譯期保證，接進 Linux kernel」的工程。它不是要取代 C——是要讓**新寫的、風險最高的葉子程式（driver）**能用一個編譯器會幫你擋錯的語言寫。這一章先看清楚全貌：它在哪、包了什麼、為什麼這樣切，你才知道後面兩章的 `kernel` crate 抽象和第一個 module 在整張圖的哪個位置。

## 先建立直覺：Rust 只住在葉子，核心還是 C

先破一個常見誤解：**RfL 不是「用 Rust 重寫 Linux」**。scheduler、mm、VFS、RCU、中斷處理——kernel 的核心全部還是、而且短期內都會是 C。Rust 進來的位置是**葉子**：一個 driver、一個檔案系統模組、一個 binder 之類的子系統元件。它呼叫核心提供的服務（配記憶體、拿鎖、註冊裝置），但它自己是被核心呼叫的那一端。

想像 kernel 是一棵樹：

```
                     ┌─────────────────────────┐
                     │   C core（樹幹與樹枝）    │
                     │  scheduler, mm, VFS,     │   ← 全是 C，Rust 不碰
                     │  RCU, irq, block, net... │
                     └───────────┬─────────────┘
                                 │ 提供服務：kmalloc / mutex_lock /
                                 │            register_chrdev ...
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
      ┌───────────┐      ┌───────────┐      ┌───────────┐
      │ C driver  │      │ C driver  │      │ Rust      │  ← 葉子：可以是 C
      │ (e1000e)  │      │ (nvme,C)  │      │ driver    │     也可以是 Rust
      └───────────┘      └───────────┘      └───────────┘
                                                  │
                                        它透過 kernel crate
                                        （安全抽象層）跟 C core 對話
```

關鍵洞察：Rust driver **不直接** call C 的 `kmalloc`、也不直接碰 `struct mutex`。它 call 的是 `kernel` crate 提供的**安全封裝**——`KVec::push(.., GFP_KERNEL)`、`Mutex<T>` 的 RAII guard。那層封裝內部才是 unsafe 的 FFI 呼叫（[Ch 19](./19-ffi.md) 那套過橋機制），但它把破口收在幾行裡，對 driver 作者暴露的是編譯器能重新罩住的安全介面。這正是你在 [Ch 19](./19-ffi.md) 學的「把 unsafe C API 包成 safe wrapper」——只是規模放大到整個 kernel API。

> 如果你對「safe wrapper 把 unsafe 收進幾行封裝」還沒感覺，回看 [Ch 19](./19-ffi.md) 的「把 unsafe C API 包成安全 wrapper」一節。RfL 的 `kernel` crate 就是這個模式做到極致的成品。

## 架構：C core ↔ bindgen ↔ kernel crate ↔ Rust driver

把上面那張樹放大到 driver 這一根葉子的內部，你會看到四層。這是 RfL 全部技術細節的骨架，記住它，後面兩章都在填這張圖的某一塊：

```
┌──────────────────────────────────────────────────────────────┐
│  (4) 你的 Rust driver                                          │
│      module! { ... }  impl kernel::Module { fn init(...) }     │  ← 你寫的，全 safe
│      用 KVec / Mutex<T> / Arc<T> / MiscDeviceRegistration      │
└───────────────────────────┬──────────────────────────────────┘
                            │ 只呼叫安全 API
┌───────────────────────────▼──────────────────────────────────┐
│  (3) kernel crate（安全抽象層）rust/kernel/*.rs                 │
│      把 C API 包成安全 Rust：                                   │
│      - Result/Error（對照回傳 -ENOMEM）                         │
│      - KVec/KBox（對照 kmalloc，且會失敗→fallible alloc）       │
│      - Mutex<T>/SpinLock<T>（RAII guard，對照手動 lock/unlock） │
│      - Arc<T>（對照 kref/refcount_t）                           │
│      內部有 unsafe + `// SAFETY:` 契約                          │
└───────────────────────────┬──────────────────────────────────┘
                            │ 呼叫自動生成的 raw binding
┌───────────────────────────▼──────────────────────────────────┐
│  (2) bindgen 生成的 raw FFI（rust/bindings/，自動生成）         │
│      bindgen 讀 kernel 的 C header，吐出 extern "C" 宣告 +     │
│      repr(C) struct。全是 unsafe、跟 C 一對一、沒有安全性可言   │
└───────────────────────────┬──────────────────────────────────┘
                            │ C ABI（System V AMD64，你 pwn 背過的）
┌───────────────────────────▼──────────────────────────────────┐
│  (1) C core                                                    │
│      kmalloc(), mutex_lock(), kref_get(), register_chrdev()... │
└──────────────────────────────────────────────────────────────┘
```

由下往上讀這四層：

1. **C core**：現有的 kernel，一行沒改。提供 `kmalloc`、`mutex_lock`、`register_chrdev` 這些函式。
2. **bindgen 生成的 raw binding**（`rust/bindings/`）：build 時 `bindgen`（你在 [Ch 19](./19-ffi.md) 用過的那個工具）讀 kernel 的 C header，機器生成對應的 Rust `extern "C"` 宣告與 `repr(C)` struct。這層跟 C **一對一**，全是 unsafe，沒有任何抽象——它只是「讓 Rust 呼得到 C 符號」。手抄幾千個宣告不可能，所以用機器生成（呼應 [Ch 19](./19-ffi.md) 「別手抄 header」那節）。
3. **kernel crate（安全抽象層）**（`rust/kernel/`）：這是 RfL 的**心臟**，也是 [Ch 38](./38-kernel-abstractions.md) 整章的主題。它拿第 2 層那些 unsafe 的原始 binding，包成安全的 Rust 型別——`KVec`（會失敗的 `kmalloc`）、`Mutex<T>`（RAII guard）、`Arc<T>`（kref）。每個 unsafe 呼叫上面都掛一個 `// SAFETY:` 註解，寫明「為什麼這裡的 unsafe 是 sound 的」。這層有人審、有人維護，driver 作者信任它。
4. **你的 Rust driver**：只用第 3 層的安全 API，理想上一行 unsafe 都不用寫（[Ch 40](./40-rust-driver.md) 會看到偶爾還是要，但範圍極小）。這是你 Part 6 最後要交付的東西。

這張圖回答了「Rust 怎麼可能安全地寫 kernel」這個問題：**不安全的部分被隔離在第 2、3 層，且第 3 層用 `// SAFETY:` 契約審過；第 4 層（你）在 safe Rust 的世界裡工作**。破口沒有消失（FFI 邊界永遠是破口），但它被收攏、被審計、被封裝了。

## 為什麼 kernel 要 Rust：memory-safety CVE 的數據

「C 不安全」是句空話，要看數字。這裡的數字都可查證：

- **Chromium 專案**分析 2015 年以來 912 個 high/critical severity 安全 bug，**約 70% 是記憶體安全問題**，其中一半是 use-after-free（來源：Chromium security 官方頁面，2020）。
- **Microsoft** 說過去 12 年，其產品的安全更新中**約 70%** 在處理記憶體安全漏洞（來源：Microsoft security 工程師 2019 演講）。
- **Android**：Google 統計 in-the-wild 被利用的漏洞中 **78% 是記憶體安全違規**；Android 平台程式碼中**超過 70% 是 memory-unsafe 語言**寫的（來源：Google Online Security Blog, 2024）。

70% 這個數字在 Chrome、Microsoft、Android 三個獨立的大型 C/C++ 專案上一致出現——這不是巧合，是 C/C++ 這類語言的結構性後果。kernel 是同一類程式：巨大、C 寫、高度並行、直接碰記憶體。它的 CVE 分布也是同一個形狀——你打過的 kernel exploit，絕大多數是 UAF、越界寫、race 觸發的 UAF（`systems/vm_escape`、`security/kernel_pwn` 這些課裡的靶子都是這類）。

Rust 的論點很直接：**這 70% 裡的絕大部分，在 safe Rust 裡編譯期就寫不出來**。

拿一個你打過的經典 kernel UAF 形狀來看具體怎麼被擋。C 裡這種 bug 到處是：

```c
/* C：一條路徑 free，另一條還在用——經典 UAF，編譯器完全不管 */
struct obj *o = lookup(id);
if (some_condition) {
    kfree(o);               /* 釋放了 */
    /* ... 但下面忘了 return，或另一路徑還持有 o ... */
}
use(o);                     /* UAF：o 可能已經被 free */
```

同樣邏輯在 safe Rust 裡：一旦 `o` 被 move（交出所有權去 drop/free），編譯器就**不讓你再碰 `o`**——`use(o)` 會直接編不過（`use of moved value`）。你在 [Ch 2](./02-ownership-move.md) 學的 ownership、[Ch 3](./03-borrowing-references.md) 學的 borrow checker，在 kernel 場景就是這樣把 UAF 擋在編譯期。這對你特別有感：你花時間找、利用的那些 UAF/double-free 原語，在 safe Rust 的 driver 裡從一開始就不存在——攻擊面直接少一大塊。（`unsafe` 塊裡仍可能有，那是 [Ch 41](./41-kernel-unsafe-safety.md) 的主題。）

| Bug 類別 | C kernel 怎麼中招 | safe Rust 怎麼擋 |
|---|---|---|
| use-after-free | `kfree(p)` 後還 deref `p` | ownership：`p` move/drop 後編譯器不讓你再用 |
| double-free | 兩條路徑都 `kfree` 同一塊 | ownership：只有一個 owner 能 drop |
| 越界讀寫 | `arr[i]`，`i` 沒檢查 | slice index 帶邊界檢查，越界 panic 不越界 |
| data race | 兩 thread 不拿鎖動同一資料 | `Send`/`Sync` + `Mutex<T>`：沒拿鎖拿不到 `&mut` |
| refcount 泄漏/提前釋 | 少/多 `kref_put` | `Arc<T>` 的 clone/drop 自動配對 refcount |
| 未初始化記憶體 | `kmalloc` 拿到髒記憶體直接讀 | 型別強制初始化；`MaybeUninit` 才能繞（[Ch 18](./18-unsafe-advanced.md)） |

注意：這**不是**說 Rust kernel code 沒有 bug。邏輯錯誤、死鎖、`unsafe` 塊裡的錯、硬體互動的錯——這些 Rust 一樣會有（[Ch 41](./41-kernel-unsafe-safety.md) 專門講 Rust kernel code 的**剩餘**破口）。而且事實已經證明：Linux 6.18 併入的 Rust binder driver，在 6.18+ 就有一個 race condition CVE（CVE-2025-68260，2026 查證）。Rust 消滅的是「記憶體安全」這一類，不是「所有 bug」。但那一類佔了 70%，值得。

### 編譯期擋 vs 執行期抓：Rust 相對於既有 sanitizer 的位置

你可能會問：kernel 已經有 KASAN、KCSAN、syzkaller 了，為什麼還需要 Rust？關鍵在**它們作用的時間點不同**。

```
  C kernel 的防線（全是「bug 已經寫進去了」之後才動作）：
     寫 code ──▶ 編譯 ──▶ 執行到那條路徑 ──▶ sanitizer 抓到 ──▶ 你 debug
                                    ▲
                          KASAN/KCSAN/syzkaller 在這裡
                          （要「跑到」才抓得到，跑不到的路徑抓不到）

  Rust safe code 的防線：
     寫 code ──▶ 編譯 ✗ 直接編不過 ──▶ 你當場改
                    ▲
              borrow checker 在這裡
              （不用跑到，一整類寫法根本編不出來）
```

- **KASAN**（Kernel Address Sanitizer）：抓 UAF、越界——但它是**執行期**的，且要那條有 bug 的路徑**真的被執行到**才抓得到。你的測試沒覆蓋到的路徑、只有特定 race timing 才觸發的 UAF，KASAN 可能整年跑不到。而且 KASAN 有顯著效能與記憶體開銷，production 不開。
- **KCSAN**（Kernel Concurrency Sanitizer）：抓 data race——同樣是執行期取樣式偵測，要兩條 thread 剛好在監測窗口內衝突才抓到，會漏。
- **syzkaller**：fuzzing，靠亂打找 crash——強，但本質是「碰運氣觸發」，覆蓋不到的就是覆蓋不到。

Rust 的 borrow checker 是**編譯期**的：它不需要「跑到」那條路徑，它證明「這種寫法在**任何**執行下都不會 UAF/race」，證不出來就編不過。這是**窮盡 vs 取樣**的差別——sanitizer 是取樣（跑到才抓），型別系統是窮盡（所有路徑都保證）。

這不是說 Rust 讓 sanitizer 沒用了。**`unsafe` 塊裡的錯 Rust 編譯器擋不了，還是要靠 KASAN/Miri（[Ch 20](./20-memory-model-ub.md)）抓**；邏輯 bug、死鎖也還是要 fuzzing。正確的理解是：**Rust 把「safe code 裡的記憶體安全」這一大塊從 sanitizer 的責任範圍移到編譯期**，讓 sanitizer 能集中火力在剩下的 `unsafe` 邊界與非記憶體 bug。兩者互補，不是取代。這也解釋了為什麼 RfL 的 `unsafe` 塊要配 Miri/KASAN 測試——編譯期擋不到的那一小塊，仍歸執行期工具管。

### 為什麼不是全部重寫？

一個尖銳的反問：既然 C 這麼危險，為什麼不把 kernel 全用 Rust 重寫？三個硬理由：

1. **成本天文數字且風險巨大**：Linux kernel 是三千多萬行 C，跑在地球上絕大多數伺服器、手機、嵌入式裝置上，累積了三十年的正確性與效能調校。重寫等於把這些全部歸零重來一遍——每一行重寫都是新引入 bug 的機會。**成熟穩定的 C code 本身就是資產**，動它是負收益。
2. **C 不會消失，Rust 要能跟 C 共存**：kernel 的核心 API 是 C 定義的，全球維護者絕大多數寫 C。RfL 的設計前提就是**漸進**——Rust 和 C 在同一個 kernel 裡並存、互相呼叫，而不是二選一。這也是為什麼架構圖第 1 層（C core）完全不動。
3. **收益集中在新程式與高風險葉子**：memory-safety bug 集中在**新寫的、複雜的、被攻擊面大的** driver（GPU、網路、檔案系統）。把 Rust 用在這些葉子，投報率最高；把穩定二十年的 C 核心重寫，投報率是負的。所以策略是「新 driver 優先考慮 Rust，舊 C 不動」。

這個「漸進、共存、只在葉子」的策略，是 RfL 能被主線接受的政治與工程基礎。理解它，你才不會誤以為 RfL 是要「顛覆」kernel——它是要**加固邊緣**。

## 歷史與現況（時間線，標來源）

誠實但簡短地講一下來龍去脈（**2026-08 查證**，來源見延伸閱讀）：

- **2020-2021**：RfL 作為 RFC 提出，Miguel Ojeda 主導。核心爭議：kernel 維護者對「多一個語言＝多一份維護負擔」的疑慮，以及對 Rust toolchain 穩定性、build 複雜度的擔憂。
- **Linux 6.1（2022-12）**：**基礎設施進主線**。這一版併入的是「地基」——build 系統支援、`kernel` crate 的骨架、少數 sample module。此時還不能拿它寫真正的 driver，是「實驗」狀態。
- **Linux 6.12（2024）**：Android 16 的 6.12 kernel 出貨時，帶了 Rust 寫的 ashmem 模組——RfL code 開始跑在**數以百萬計的真實裝置**上。
- **Linux 6.13（2025-01）**：Greg Kroah-Hartman 稱為「the tipping point」的版本，併入了 misc driver 的 Rust binding——讓真正的 misc driver 能用 Rust 寫（這正是 [Ch 40](./40-rust-driver.md) 的基礎）。
- **Linux 6.18（2025）**：Android binder driver（Rust 版）併入主線。ARM Mali GPU 的 Tyr driver 能開 GNOME、跑基本遊戲。
- **Linux 6.19（2026）**：NVIDIA 的 Nova GPU driver 開始落地初步支援。
- **2025-12 Maintainers Summit**：經過近五年，Rust 的「實驗」狀態被評估為**成功並結束實驗**（declared a success）。維護者承諾持續維護 Rust 支援、把 Rust patch 納入正常 review 流程、接受新的 Rust driver。DRM（繪圖）子系統計畫在約一年內要求新 driver 用 Rust。

一年內 kernel 裡的 Rust code 量成長了約五倍。這不再是「會不會成」的問題，是「哪些子系統、多快」的問題。

**政治背景（簡短且誠實）**：這條路不是全員鼓掌通過的。有資深維護者公開表達過對「Rust for Linux 增加維護負擔、C 維護者被要求配合 Rust 邊界」的強烈反對，2024 年有過知名的維護者請辭與公開爭執。這是真實的張力，不是花邊——它反映一個現實：把新語言塞進一個三十年的 C 專案，技術問題只是一半，另一半是社群、責任歸屬、誰維護 Rust 邊界的問題。Linus Torvalds 整體支持 RfL，但也強調 Rust 不能拖累 C 開發。你該知道這背景，寫 RfL code 時對「Rust 邊界不該給 C 維護者添亂」這條隱形規則會更有感。

## Toolchain 需求與 build 環境

RfL 對 toolchain 比一般 Rust 專案挑剔得多，因為它在 kernel 這種特殊環境跑。你需要（**依 kernel `Documentation/rust/quick-start.rst`，2026-08 查證**）：

| 元件 | 作用 | 對照 |
|---|---|---|
| **rustc**（特定版本） | 編 Rust kernel code | 對照 kernel 對 gcc/clang 的版本要求 |
| **rust-src** | `core`/`alloc` 的原始碼 | kernel build 要 cross-compile `core`（[Ch 22](./22-no-std.md) 的 `-Z build-std` 那套） |
| **bindgen** | 從 C header 生 raw binding | 就是架構圖第 2 層的生成器 |
| **rustfmt** | 格式化 Rust kernel code | 對照 `clang-format` |
| **clippy** | Rust linter，額外警告 | 對照 `sparse`/`smatch` |
| **`CONFIG_RUST`** | kernel config 開關 | 只有偵測到合用的 Rust toolchain 才出現 |
| **LLVM toolchain**（`make LLVM=1`） | RfL 主要走 LLVM | Rust 後端是 LLVM，跟 C 用同一套 codegen 較一致 |

**版本挑剔的原因（重要，認識論誠實）**：RfL 用了一些 Rust 尚未穩定的功能，早期甚至釘死在某個 nightly。到 2025 已經能用 **stable** rustc 編（最低版本要求約 **1.78**，Summit 當時 current 用到 **1.92**；**這兩個數字是 2025-12 Summit 的說法，之後只會往上**）。kernel 對「known-good / minimum」rustc 版本的**精確**釘定寫在原始碼樹的 `scripts/min-tool-version.sh`（以你 clone 的 kernel 版本為準——這個值一直在變，別背，去查你手上那棵樹）。官方 quick-start 的建議做法是在 kernel 目錄下 `rustup override set stable`，讓這個目錄用指定 rustc 而不動你的預設 toolchain。

設定步驟（**依官方 quick-start，本機未跑完整 build——見下方說明**）：

```bash
# 1. 裝 rustup + 對的 rustc（在 kernel 原始碼樹目錄下）
rustup override set stable          # 或用你 kernel 樹要求的版本
rustup component add rust-src       # core/alloc 原始碼，build 要 cross-compile core
rustup component add rustfmt clippy

# 2. 裝 bindgen（版本也可能被 kernel 樹釘定）
cargo install --locked bindgen-cli

# 3. 在 kernel 樹裡確認 Rust 可用
make LLVM=1 rustavailable
#    這個 target 跑的是 Kconfig 判斷 RUST_IS_AVAILABLE 的同一套邏輯；
#    不可用時它會告訴你「為什麼不可用」（缺哪個元件/版本不符）。

# 4. menuconfig 開 CONFIG_RUST（General setup 底下；
#    只有 rustavailable 通過才會出現這個選項），然後 make LLVM=1
```

> **未實測聲明**：本機（WSL2）**沒有** kernel build tree（`/lib/modules/$(uname -r)/build` 不存在，實測確認），也沒有為了這門課去 build 一個完整的 Rust-enabled kernel——那是一個以小時計、吃數 GB 磁碟的重量級操作。所以上面 `make LLVM=1 rustavailable` / `make LLVM=1` 這幾步**未在本機實測**，命令與流程照官方 `Documentation/rust/quick-start.rst`（2026-08 查證）寫。完整、可跑的 build+QEMU 流程與**預期輸出**留到 [Ch 39](./39-first-kernel-module.md) 詳述，並在那裡明確標哪些是理論預期。本章能本機真跑的只有純 Rust 邏輯片段（見下一節）。

## 本機能驗的：kernel 錯誤處理的「形狀」

kernel module 本身跑不了，但 RfL 的很多**純 Rust 邏輯**（不碰 C binding 的部分）能用一般 `rustc` 驗。這裡驗一個核心概念的形狀：kernel 的 `Result`/`Error` 怎麼把 Rust 的 `?` 傳播接到 C 的「回傳負 errno」慣例——[Ch 38](./38-kernel-abstractions.md) 會深挖真正的 `kernel::error`，這裡先用一個**模擬其形狀**的 std 程式建立直覺（真正的 `kernel::error::Error` 內部不同，但 `?` 傳播 + 轉負 errno 的形狀一致）：

```rust
// 模擬 kernel::error 的形狀。真正的 kernel 用 kernel::error::{Result, Error, code}，
// 這裡用純 rustc 能編的等價結構展示「? 傳播」與「errno 映射」的概念。
#[derive(Debug, Clone, Copy, PartialEq)]
struct Error(i32);              // 包一個負 errno，對照 kernel::error::Error

#[allow(non_snake_case)]
mod code {                       // 對照 kernel::error::code::*
    use super::Error;
    pub const ENOMEM: Error = Error(-12);   // -ENOMEM
    pub const EINVAL: Error = Error(-22);   // -EINVAL
}

type Result<T = ()> = core::result::Result<T, Error>;

// 一個會失敗的配置，對照 KVec::push(.., GFP_KERNEL)?
fn fake_alloc(n: usize) -> Result<Vec<i32>> {
    if n > 1000 { return Err(code::ENOMEM); }   // 配太多 → 失敗
    Ok((0..n as i32).collect())
}

fn init(n: usize) -> Result<usize> {
    let v = fake_alloc(n)?;          // ? 傳播 Error，跟 kernel init 裡一模一樣
    if v.is_empty() { return Err(code::EINVAL); }
    Ok(v.len())
}

// 對照 from_result：把 Result 轉成 C int（成功 0，失敗負 errno）
fn to_c_int(r: Result<usize>) -> i32 {
    match r { Ok(_) => 0, Err(Error(e)) => e }
}

fn main() {
    println!("init(3)    -> {:?}, c_int={}", init(3), to_c_int(init(3)));
    println!("init(0)    -> {:?}, c_int={}", init(0), to_c_int(init(0)));
    println!("init(9999) -> {:?}, c_int={}", init(9999), to_c_int(init(9999)));
    assert_eq!(to_c_int(init(9999)), -12);   // ENOMEM
    assert_eq!(to_c_int(init(0)), -22);      // EINVAL
    println!("errno mapping OK");
}
```

本機（WSL2 `rustc 1.97.1`）真跑輸出：

```
init(3)    -> Ok(3), c_int=0
init(0)    -> Err(Error(-22)), c_int=-22
init(9999) -> Err(Error(-12)), c_int=-12
errno mapping OK
```

這段是**真跑過的**。它示範的形狀就是 RfL 的日常：kernel Rust code 用 `Result<T>` + `?` 寫錯誤傳播（像寫一般 Rust），到 C 邊界時 `kernel` crate 幫你把 `Err(Error)` 轉成 C 期待的負 errno 回傳值。對照 C：你在 C driver 裡要手動 `return -ENOMEM;`、手動一層層檢查回傳值 `if (ret < 0) goto err;`——Rust 用 `?` 把這套 boilerplate 消掉，而且編譯器強制你不能忽略錯誤（`Result` 沒處理會警告）。

## 對比：C module 開發 vs Rust module 開發

先給你一張全景對照，後面兩章會把每一格拆開：

| 面向 | C kernel module | Rust kernel module（RfL） |
|---|---|---|
| 進入/退出點 | `module_init(fn)` / `module_exit(fn)` 巨集 | `module!` 巨集 + `impl kernel::Module`（[Ch 39](./39-first-kernel-module.md)） |
| 印訊息 | `printk(KERN_INFO ...)` / `pr_info()` | `pr_info!()`（同名對照，[Ch 39](./39-first-kernel-module.md)） |
| 配記憶體 | `kmalloc(size, GFP_KERNEL)`，回 NULL 要自己檢查 | `KVec::push(x, GFP_KERNEL)?`，失敗回 `Err` 型別強制處理 |
| 錯誤傳遞 | `return -ENOMEM; if(ret<0) goto err;` | `Result<T>` + `?`（上一節） |
| 上鎖 | `mutex_lock(&m); ... mutex_unlock(&m);`（手動配對，會忘） | `let g = m.lock();`（RAII guard，離開作用域自動解，[Ch 38](./38-kernel-abstractions.md)） |
| refcount | `kref_get` / `kref_put`（手動配對） | `Arc<T>` clone/drop 自動配對（[Ch 38](./38-kernel-abstractions.md)） |
| 誰擋 UAF/race | KASAN/KCSAN（執行期）+ 你的紀律 | 編譯期 ownership/borrow |
| build | `Kbuild` + `make` | `Kbuild` + `make LLVM=1`（[Ch 39](./39-first-kernel-module.md)） |

這張表就是 Part 6 的地圖。每一列右邊那格「Rust 怎麼做」，是 [Ch 38](./38-kernel-abstractions.md)（抽象）與 [Ch 39](./39-first-kernel-module.md)（第一個 module）+ [Ch 40](./40-rust-driver.md)（driver）要逐一填的內容。

## 踩雷集錦

1. **以為 RfL 是「用 Rust 重寫 kernel」**：不是。核心（scheduler/mm/VFS/RCU）永遠是 C，Rust 只在葉子（driver、子系統元件）。誤解這點會讓你對 RfL 的定位、能力邊界、政治處境全部想錯。它是**加固邊緣**，不是**顛覆核心**。

2. **以為 Rust 進 kernel = 沒有 bug 了**：Rust 消滅的是**記憶體安全**這一類（佔 CVE 約 70%），不是全部。邏輯 bug、死鎖、`unsafe` 塊裡的錯、硬體互動錯照樣有——6.18 的 Rust binder 就出過 race CVE（CVE-2025-68260）。RfL 的價值是把最大的一類 bug 移到編譯期，不是萬靈丹。

3. **以為 Rust driver 直接呼叫 `kmalloc`/`mutex_lock`**：不。driver 呼叫的是 `kernel` crate 的**安全封裝**（`KVec`/`Mutex<T>`），封裝內部才是 unsafe FFI。搞錯這層，你會不理解「為什麼 Rust 能安全地做 kernel 事」——答案就在架構圖的第 3 層（安全抽象層），不在第 4 層（你的 driver）。

4. **以為 toolchain 隨便一個 rustc 都能編**：RfL 對 rustc/bindgen 版本挑剔，且**版本一直在往上抬**（最低約 1.78，實際用到 1.92+）。精確要求在你 clone 的那棵 kernel 樹的 `scripts/min-tool-version.sh`，不是固定值。用錯版本 `make rustavailable` 會直接告訴你不可用。別背版本號，去查你手上那棵樹。

5. **以為 CONFIG_RUST 是預設開的**：不是。它只有在 `make rustavailable` 通過（toolchain 齊全且版本對）時才會在 menuconfig 出現。很多人第一步就卡在「menuconfig 找不到 CONFIG_RUST」，根因是 toolchain 沒配好——先跑 `make LLVM=1 rustavailable` 看它抱怨什麼。

6. **忽略政治背景，寫出「給 C 維護者添亂」的 Rust 邊界**：RfL 有一條隱形規則——Rust 這邊的抽象不該逼 C 維護者為了 Rust 去改 C。理解 2024 年那些爭執的根源，你寫 abstraction 時會更懂「為什麼 kernel crate 要盡量單向依賴 C，而不是反過來」。

## 進階：再往深一層

- **`rust/kernel/` 的原始碼結構**：真正想懂 RfL，最終要讀 kernel 樹裡的 `rust/kernel/` 目錄（安全抽象層）與 `rust/bindings/`（生成的 raw binding）。`rust/kernel/lib.rs` 是入口，`sync.rs`、`error.rs`、`alloc/` 是 [Ch 38](./38-kernel-abstractions.md) 的主角。搭配 `rust.docs.kernel.org` 的預生成 rustdoc 看 API。
- **`pin-init` 已獨立成 crate**：RfL 的 in-place 初始化框架（[Ch 38](./38-kernel-abstractions.md) 的主題之一）已抽出成獨立的 `pin-init` crate，也能在 kernel 外用。想理解「為什麼 kernel 物件需要 in-place 初始化」，這是核心。
- **`rustc_codegen_gcc` 與 GCC 後端**：RfL 主要走 LLVM（`make LLVM=1`），但也有用 GCC 後端（`rustc_codegen_gcc` / `gccrs`）的實驗性支援——這對「Rust 要能跟 kernel 既有的 GCC build 共存」很重要，官方 quick-start 標為 very experimental。
- **面試/研究角度**：能講清楚「為什麼 70% 這個數字讓 kernel 願意接受 Rust 的維護成本」「為什麼是葉子而不是核心」「Rust 消滅哪一類 bug、不消滅哪一類」，就是對 RfL 有真實理解，而不是跟風。

## 動手練習

1. **查你手上 kernel 樹的 rustc 版本要求**：clone 一份 Linux（或看你系統的 `/usr/src`），打開 `scripts/min-tool-version.sh`，找 `rustc` 那段，看它釘的是哪個版本。對照 `rustc --version`，判斷你的 toolchain 夠不夠新。這一步讓你體會「版本要求是活的、綁 kernel 版本」。

2. **把本章的錯誤映射 demo 改壞**：把 `fake_alloc` 的門檻 `1000` 拿掉、讓它永遠成功，觀察 `init(9999)` 的 `c_int` 變成 0。再故意在 `init` 裡忘記 `?`（改成 `let v = fake_alloc(n).unwrap();`），想想這在真 kernel 裡的後果——`unwrap` panic 在 kernel 裡是 `BUG()`，比 C 的「回錯誤碼往上傳」嚴重得多。這是為什麼 kernel Rust code 幾乎不用 `unwrap`。

3. **畫出你熟悉的一個 C driver 的四層圖**：挑一個你讀過的簡單 C driver（如某個 misc device），想像把它改成 Rust——哪些 `kmalloc`/`mutex_lock`/`register_*` 呼叫會變成 `kernel` crate 的哪個安全型別？畫出它的「C core ↔ bindgen ↔ kernel crate ↔ Rust driver」四層。這個練習做完，[Ch 40](./40-rust-driver.md) 你會讀得很快。

## 本章重點整理

- RfL = 把 Rust 的**編譯期記憶體安全保證**接進 Linux kernel，**只用在葉子（driver/子系統元件）**，核心永遠是 C。基礎設施 Linux 6.1（2022-12）進主線，2025-12 Summit 宣告「實驗成功並結束實驗」。
- 架構四層：**C core ↔ bindgen 生的 raw binding ↔ kernel crate（安全抽象層）↔ 你的 Rust driver**。不安全的部分被隔離在中間兩層並用 `// SAFETY:` 審計，driver 作者在 safe Rust 工作。
- 為什麼要 Rust：記憶體安全 bug 佔 C/C++ 專案 CVE **約 70%**（Chrome/Microsoft/Android 三方一致），safe Rust 讓 UAF/double-free/越界/race/refcount 錯這幾類編譯期就寫不出來。但**不消滅**邏輯 bug、死鎖、unsafe 塊裡的錯。
- 為什麼不全重寫：成本天文數字且風險巨大、C 不會消失要共存、收益集中在新寫的高風險葉子。策略是漸進、共存、只在葉子。
- Toolchain 挑剔且版本一直往上：rustc（最低約 1.78）+ rust-src + bindgen + rustfmt + clippy + `CONFIG_RUST` + LLVM。精確版本在你那棵 kernel 樹的 `scripts/min-tool-version.sh`。

## 自我檢核

- [ ] 不看筆記，能畫出「C core ↔ bindgen ↔ kernel crate ↔ Rust driver」四層，並說出每層是 safe 還是 unsafe、誰審計不安全的部分。
- [ ] 有人問「Rust 進 kernel 是要重寫 Linux 嗎」，能回答「不是」並解釋為什麼是葉子、為什麼不全重寫（三個理由）。
- [ ] 能引用記憶體安全 bug 約佔 70% 的數據（至少一個來源），並說出 Rust 消滅哪幾類 bug、**不**消滅哪幾類。
- [ ] 能對照 C module 開發，說出 `module_init`/`printk`/`kmalloc`/`mutex_lock`/`kref` 在 Rust 這邊各對應什麼。
- [ ] 知道 RfL toolchain 為什麼挑版本、精確版本要求去哪查（不是固定值）、`CONFIG_RUST` 為什麼可能看不到。

## 延伸閱讀

### 官方文件 / 一手來源

- **[Rust for Linux 官網](https://rust-for-linux.com/)** 與 **kernel 樹 `Documentation/rust/`（[docs.kernel.org/rust](https://docs.kernel.org/rust/index.html)）**
  - **讀哪裡**：官網首頁的專案定位；`Documentation/rust/quick-start.rst`（toolchain 與 build，本章第 8 節的一手依據）、`general-information.rst`、`arch-support.rst`。
  - **學到什麼**：本章所有 build/toolchain 斷言的權威來源；`make rustavailable`、`CONFIG_RUST` 的精確語意。**注意**：版本號一直在變，以你查閱當下為準。
  - **前提**：讀完本章對架構的全貌理解；這裡是把「怎麼實際 build」補齊的地方（配合 [Ch 39](./39-first-kernel-module.md)）。

- **kernel 樹 `scripts/min-tool-version.sh`**（在你 clone 的 Linux 原始碼裡）
  - **讀哪裡**：`rustc` 與 `bindgen` 那兩段。
  - **學到什麼**：你手上這棵 kernel 樹**精確**要求的 rustc/bindgen 版本——本章刻意不寫死數字，就是要你來這裡查活的值。

### 部落格 / 技術文章（現況與數據）

- **[Google Online Security Blog — Eliminating Memory Safety Vulnerabilities at the Source](https://security.googleblog.com/2024/09/eliminating-memory-safety-vulnerabilities-Android.html)** — Google 安全團隊（2024）
  - **這篇說什麼**：Android 記憶體安全數據的一手來源（78% in-the-wild 漏洞是記憶體安全、平台程式碼 70%+ 是 memory-unsafe 語言），以及「safe coding / 新程式用 memory-safe 語言」的策略——正是 RfL「只在葉子/新程式用 Rust」策略的理論背景。
  - **讀哪裡**：全篇，特別是統計圖表那幾段。
  - **為什麼值得讀**：本章「為什麼要 Rust」的 70% 論證的權威出處，且解釋了「漸進導入」為什麼在數學上有效（bug 集中在新程式）。

- **[The state of the kernel Rust experiment — LWN.net](https://lwn.net/Articles/1050174/)** — LWN（2025-12 Summit 報導）
  - **這篇說什麼**：2025-12 Maintainers Summit 對 RfL「實驗結束、宣告成功」的一手報導，含 Miguel Ojeda 的說法、DRM 子系統的時程、版本要求（1.78/1.92）、政治張力的中立記述。
  - **讀哪裡**：全篇不長。
  - **為什麼值得讀**：LWN 是 kernel 圈最權威的技術媒體，本章「歷史與現況」的時間線與政治背景以它為準；比任何二手部落格可靠。

### 官方文件 / 數據來源

- **[Chromium Security — Memory safety](https://www.chromium.org/Home/chromium-security/memory-safety/)** — Chromium 專案
  - **讀哪裡**：「the memory safety problem」那節（912 個 bug、70%、一半是 UAF 的統計）。
  - **學到什麼**：本章 70% 論證的第一個獨立來源（Chrome）。配合上面 Google/Android 那篇，你就有兩個獨立大型 C++ 專案的一致數據，論證才站得住。
  - **前提**：無，這是純數據頁。

RfL 的全貌與「為什麼」清楚了，接下來要拆的是架構圖的**第 3 層**——那個把 C 的 unsafe 世界包成 safe Rust 的 `kernel` crate。下一章深挖它的核心抽象：錯誤處理、`Arc`/refcount、`KVec`/`KBox` 的 fallible allocation，以及 kernel 物件為什麼需要 `pin_init!` 這套 in-place 初始化。這是你能讀懂、進而能寫 Rust driver 的關鍵地基。

→ [Ch 38 kernel 抽象：kernel crate 與 pin-init](./38-kernel-abstractions.md)
