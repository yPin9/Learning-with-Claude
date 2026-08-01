# Ch 39 — 第一個 Rust kernel module

> **目標**：把 [Ch 38](./38-kernel-abstractions.md) 的抽象拼成一個**完整、可讀**的最小 Rust kernel module——`module!` 巨集（name/author/description/license）、實作 `kernel::Module` trait 的 `init`（對照 C `module_init`/`module_exit`）、`pr_info!`（對照 `printk`）。然後走一遍 build 系統（`Kbuild`/`Makefile`、`make LLVM=1`、in-tree `samples/rust` 與 out-of-tree 兩條路）與在 QEMU 跑 `insmod`/`rmmod`/看 `dmesg` 的完整流程。全程對照 C hello-world module。

> **正確性聲明（重要，先讀）**：build 一個完整的 Rust-enabled kernel 太重（以小時計、吃數 GB 磁碟），本機（WSL2）也沒有 kernel build tree（`/lib/modules/$(uname -r)/build` 不存在，[Ch 37](./37-rust-for-linux-overview.md) 實測確認）。所以**本章所有 `make`/`insmod`/`rmmod`/`dmesg` 的執行與輸出一律標「未實測，理論預期」**——命令與預期輸出依官方 `samples/rust/`、`Documentation/rust/`、Kbuild 文件（**2026-08 查證**）寫，並在最後明確給出「什麼環境、怎麼真正驗證」。本章**能本機真跑**的只有 module 裡的**純 Rust 邏輯**（`init` 建 `KVec` 那段的等價邏輯），那段標明是 `rustc 1.97.1` 真跑的。API（`module!`/`Module`/`pr_info!`）依主線原始碼，未穩定會變。

## 為什麼需要這個？

你在 C 寫過 hello-world module——`printk` 一行、`module_init`/`module_exit` 各一個函式、`MODULE_LICENSE("GPL")`、`insmod` 進去 `dmesg` 看訊息、`rmmod` 出來。那是每個 kernel 開發者的第一個 module。這一章做同一件事的 Rust 版，目的不是「換個語法」，而是讓你看清楚：[Ch 38](./38-kernel-abstractions.md) 那些抽象**組裝起來**是什麼樣子，以及 Rust module 和 C module 在**結構**上（不只語法）差在哪。

具體要回答三個問題：(1) Rust 版的「進入點/退出點」怎麼寫，為什麼是一個 trait 而不是兩個函式？(2) 一個 module 的生命週期（載入→運行→卸載）在 Rust 型別系統裡怎麼表達？(3) build 和跑的流程跟 C 差多少？搞懂這章，[Ch 40](./40-rust-driver.md) 寫真正有功能的 driver（字元裝置）就只是往這個骨架上加肉。

## 先建立直覺：module 是一個「有生死的物件」

C 的 module 心智模型是**兩個獨立函式**：

```
  C module 的生命週期（兩個獨立函式，狀態靠全域變數傳遞）
  ┌──────────────┐                          ┌──────────────┐
  │ module_init  │  ── 載入時呼叫 ──▶ 運行 ──│ module_exit  │
  │ (int, 回0/負)│                          │ (void)       │
  └──────────────┘                          └──────────────┘
        │                                          ▲
        └── 配的資源存在「全域變數」───────────────┘
            （init 配、exit 記得手動釋放，漏了就 leak）
```

Rust 的 module 心智模型是**一個物件的生與死**：

```
  Rust module 的生命週期（一個物件，資源是它的欄位）
  ┌────────────────────────────────────────────────┐
  │  struct MyModule { numbers: KVec<i32>, ... }     │
  │                                                  │
  │  init() ──建構──▶ 回傳 MyModule 物件             │  ← 載入：init 回傳物件
  │                   （kernel 持有它，運行期活著）  │
  │                                                  │
  │  卸載 ──▶ Drop::drop(&mut MyModule) 自動呼叫     │  ← 卸載：物件被 drop
  │           （欄位自動釋放，忘不了）               │
  └────────────────────────────────────────────────┘
```

關鍵差異：**C 的 init/exit 是兩個沒有型別關聯的函式，資源靠全域變數在它們之間傳遞，exit 要記得手動釋放；Rust 的 module 是一個物件，`init` 建構它、kernel 持有它、卸載時它被 `Drop`——資源是它的欄位，自動隨物件釋放**。這正是 [Ch 38](./38-kernel-abstractions.md) 「型別接管契約」的直接應用：module 的「卸載時要清理」從「你記得寫 exit」變成「物件 drop 自動做」。

## 最小 Rust module 的完整結構

看主線 kernel 樹 `samples/rust/rust_minimal.rs` 的**真實完整原始碼**（**2026-08 查證，逐字**）：

```rust
// SPDX-License-Identifier: GPL-2.0

//! Rust minimal sample.

use kernel::prelude::*;

module! {
    type: RustMinimal,
    name: "rust_minimal",
    authors: ["Rust for Linux Contributors"],
    description: "Rust minimal sample",
    license: "GPL",
    params: {
        test_parameter: i64 {
            default: 1,
            description: "This parameter has a default of 1",
        },
    },
}

struct RustMinimal {
    numbers: KVec<i32>,
}

impl kernel::Module for RustMinimal {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        pr_info!("Rust minimal sample (init)\n");
        pr_info!("Am I built-in? {}\n", !cfg!(MODULE));
        pr_info!(
            "test_parameter: {}\n",
            *module_parameters::test_parameter.value()
        );

        let mut numbers = KVec::new();
        numbers.push(72, GFP_KERNEL)?;
        numbers.push(108, GFP_KERNEL)?;
        numbers.push(200, GFP_KERNEL)?;

        Ok(RustMinimal { numbers })
    }
}

impl Drop for RustMinimal {
    fn drop(&mut self) {
        pr_info!("My numbers are {:?}\n", self.numbers);
        pr_info!("Rust minimal sample (exit)\n");
    }
}
```

逐塊拆，全程對照 C：

### `module!` 巨集：對照 C 的 module metadata

```rust
module! {
    type: RustMinimal,                              // 哪個型別是這個 module
    name: "rust_minimal",                           // module 名（/sys/module/rust_minimal）
    authors: ["Rust for Linux Contributors"],       // 對照 MODULE_AUTHOR
    description: "Rust minimal sample",              // 對照 MODULE_DESCRIPTION
    license: "GPL",                                  // 對照 MODULE_LICENSE("GPL")
    params: { test_parameter: i64 { default: 1, ... } },  // 對照 module_param
}
```

對照 C 的等價 metadata：

```c
MODULE_AUTHOR("Rust for Linux Contributors");
MODULE_DESCRIPTION("C minimal sample");
MODULE_LICENSE("GPL");
static long test_parameter = 1;
module_param(test_parameter, long, 0644);
```

`module!` 是一個**過程巨集**（[Ch 9](./09-traits.md)/[Ch 10](./10-generics-monomorphization.md) 提過巨集），它展開成一堆 kernel 需要的樣板：module 的 `.modinfo` 段（放 metadata）、C 期待的 `init_module`/`cleanup_module` 符號（橋接到你的 `Module` trait）、module param 的註冊。你寫宣告式的 `module!{...}`，巨集幫你生出這些 C ABI 層要的東西——這是 [Ch 37](./37-rust-for-linux-overview.md) 架構圖「kernel crate 把 C 樣板包起來」的又一例。

`type: RustMinimal` 這行是關鍵：它告訴 `module!` 巨集「`RustMinimal` 這個型別實作了 `kernel::Module`，它就是這個 module 的本體」。

### `impl kernel::Module` 的 `init`：對照 `module_init`

```rust
impl kernel::Module for RustMinimal {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        pr_info!("Rust minimal sample (init)\n");
        // ... 建 KVec ...
        Ok(RustMinimal { numbers })     // 回傳建好的 module 物件
    }
}
```

對照 C：

```c
static int __init rust_minimal_init(void) {
    printk(KERN_INFO "C minimal sample (init)\n");
    /* ... kmalloc、setup ... */
    return 0;                            /* 回 0 成功，負 errno 失敗 */
}
module_init(rust_minimal_init);
```

三個結構性差異：

1. **`init` 回傳 `Result<Self>` 而不是 `int`**：C 回 `0`/負 errno，Rust 回 `Ok(建好的物件)` 或 `Err(Error)`。成功時 kernel **拿到並持有這個物件**（存在 module 的狀態裡），運行期它一直活著。這是「module 是物件」的核心——`init` 是它的建構子。
2. **失敗自動清理**：`numbers.push(..)?` 如果配置失敗，`?` 回 `Err`，此時**已經建好的部分（前面 push 進去的）會自動 drop**。C 的 init 失敗要手動 `goto err; kfree(...);`，Rust 靠 ownership + `Drop` 自動（[Ch 38](./38-kernel-abstractions.md) 講過）。
3. **沒有獨立的 `exit` 函式**：卸載邏輯在 `Drop`（下一塊），不是另一個函式。C 的 `module_init`/`module_exit` 是兩個沒有型別關聯的函式；Rust 是一個型別的建構（`init`）與解構（`Drop`）。

`_module: &'static ThisModule` 是 kernel 傳進來的「這個 module 自己」的 handle（`&'static` 因為它活整個 module 生命週期），用來註冊子系統等（本例沒用到，所以 `_` 前綴）。

### `Drop`：對照 `module_exit`

```rust
impl Drop for RustMinimal {
    fn drop(&mut self) {
        pr_info!("My numbers are {:?}\n", self.numbers);
        pr_info!("Rust minimal sample (exit)\n");
        // self.numbers（KVec）在這之後自動釋放，不用手動 kfree
    }
}
```

對照 C：

```c
static void __exit rust_minimal_exit(void) {
    printk(KERN_INFO "C minimal sample (exit)\n");
    kfree(numbers);                      /* 手動釋放 init 配的東西 */
}
module_exit(rust_minimal_exit);
```

`rmmod` 卸載 module 時，kernel drop 那個 `RustMinimal` 物件，`Drop::drop` 被呼叫。注意 `drop` 裡**只寫了印訊息**——`self.numbers`（`KVec`）的釋放是**自動**的（`KVec` 自己的 `Drop` 在 `RustMinimal` 的 `Drop` 之後跑）。C 要手動 `kfree(numbers)`，漏了就是 module 卸載後的 leak。Rust 的 module 資源是物件欄位，隨物件死亡自動釋放。

### `pr_info!`：對照 `printk` / `pr_info`

```rust
pr_info!("Rust minimal sample (init)\n");
```

對照 C：

```c
printk(KERN_INFO "C minimal sample (init)\n");
/* 或現代 C 慣用的： */
pr_info("C minimal sample (init)\n");
```

`pr_info!` 是 Rust 巨集，對照 C 的 `pr_info()`（即 `printk(KERN_INFO ...)`）。RfL 提供整套對照 `printk` log level 的巨集（**依 `samples/rust/rust_print_main.rs`，2026-08 查證**）：

| Rust 巨集 | C 對照 | log level |
|---|---|---|
| `pr_emerg!` | `pr_emerg` / `KERN_EMERG` | 0（系統無法用） |
| `pr_alert!` | `pr_alert` | 1 |
| `pr_crit!` | `pr_crit` | 2 |
| `pr_err!` | `pr_err` | 3（錯誤） |
| `pr_warn!` | `pr_warn` | 4（警告） |
| `pr_notice!` | `pr_notice` | 5 |
| `pr_info!` | `pr_info` | 6（一般資訊） |
| `pr_cont!` | `pr_cont` | 接續上一行（不換行） |

`pr_info!` 用的是 Rust 的格式化（`{}`、`{:?}`，[Ch 12](./12-core-traits.md) 的 `Display`/`Debug`），比 C 的 `printk` 格式字串安全——型別不匹配編譯期抓，不像 C 的 `%d` 配錯型別是 UB。

## 本機能驗的：init 的純 Rust 邏輯

module 本身跑不了（無 kernel build tree），但 `init` 裡**建 `KVec`、回傳物件、`Drop` 印訊息**這套**純 Rust 邏輯**的形狀能用一般 `rustc` 驗。這裡用 std 等價物（`Vec` + `try_reserve` 模擬 fallible `KVec::push`）真跑，證明邏輯結構成立：

```rust
// 本機真跑：模擬 rust_minimal 的 init 純邏輯。
// KVec -> Vec、KVec::push(x, GFP_KERNEL)? -> try_reserve + push、pr_info! -> println!。
// kernel 特有的 module!/Module/ThisModule 部分本機編不了，故省略。
#[derive(Debug)]
struct AllocError;

struct RustMinimal {
    numbers: Vec<i32>,          // 對照 KVec<i32>
}

fn init() -> Result<RustMinimal, AllocError> {
    let mut numbers: Vec<i32> = Vec::new();
    for &n in &[72, 108, 200] {
        numbers.try_reserve(1).map_err(|_| AllocError)?;  // fallible，對照 push(.., GFP_KERNEL)?
        numbers.push(n);
    }
    Ok(RustMinimal { numbers })
}

impl Drop for RustMinimal {
    fn drop(&mut self) {
        println!("My numbers are {:?}", self.numbers);   // 對照 pr_info!
        println!("Rust minimal sample (exit)");
    }
}

fn main() {
    println!("Rust minimal sample (init)");
    let m = init().expect("init failed");
    println!("built, len = {}", m.numbers.len());
    // m 在 main 結束時 drop → 印 exit 訊息，對照 module 卸載
}
```

本機（WSL2 `rustc 1.97.1`）真跑輸出：

```
Rust minimal sample (init)
built, len = 3
My numbers are [72, 108, 200]
Rust minimal sample (exit)
```

這證明了 module 的**邏輯骨架**成立：`init` 建物件、運行期物件活著、結束時 `Drop` 自動印 exit 訊息並釋放 `numbers`。真的 kernel module 的 `dmesg` 輸出會是同樣的形狀（見下方「在 QEMU 跑」的預期輸出），差別只在 `KVec`/`pr_info!`/`module!` 的 kernel 實作——那些本機編不了。

## build 系統：in-tree 與 out-of-tree

有兩條 build module 的路。**以下所有 `make` 命令未在本機實測**（無 kernel build tree），依 kernel Kbuild 文件與 `samples/rust/` 慣例（2026-08）。

### 路線一：in-tree（放進 `samples/rust`，最簡單）

`rust_minimal.rs` 本身就在 `samples/rust/`。要 build 它，在**已設好 Rust 的 kernel 樹**裡（[Ch 37](./37-rust-for-linux-overview.md) 的 toolchain 步驟做完），menuconfig 開對應的 config 選項，然後 build：

```bash
# 在 kernel 原始碼樹裡（未實測，理論預期，依 Kbuild 文件）
make LLVM=1 menuconfig
#   Kernel hacking → Sample kernel code → Rust samples →
#   [M] Minimal (CONFIG_SAMPLE_RUST_MINIMAL=m，選 m 編成可載入模組)
make LLVM=1 modules            # build 所有 =m 的模組（含 rust_minimal）
#   產物：samples/rust/rust_minimal.ko
```

`samples/rust/` 的 `Makefile` 已經幫你寫好了 module 的 build 規則（把 `.rs` 交給 Rust 編譯流程），你只要開 config。這是**第一次跑 RfL module 最省事的路**——不用自己寫 build 檔。

### 路線二：out-of-tree（自己的目錄，寫 Kbuild）

真實開發通常 module 在自己的目錄，對著一個已 build 的 kernel 編。out-of-tree Rust module 的 build 檔（**未實測，依 Kbuild 文件形狀**）：

```makefile
# Kbuild（或直接寫在 Makefile）
obj-m := my_rust_module.o
# Rust module 的 .rs 檔名要對應 obj-m 的名字（my_rust_module.rs）
```

```makefile
# Makefile：對著已 build 的 kernel 樹跑 Kbuild
KDIR ?= /lib/modules/$(shell uname -r)/build
default:
	$(MAKE) -C $(KDIR) M=$(PWD) LLVM=1 modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) LLVM=1 clean
```

```bash
make          # 產出 my_rust_module.ko（未實測）
```

> **誠實提醒**：out-of-tree Rust module 比 C 挑剔——它需要那棵 kernel 是**用相同 rustc 版本、開了 `CONFIG_RUST` build 的**，且 module 用到的 `kernel` crate API 要和那棵 kernel 的版本相符（API 未穩定，[Ch 38](./38-kernel-abstractions.md) 強調過）。C module 只要 kernel header 相容就好，Rust 這邊多一層「rustc 版本 + kernel crate 版本」的約束。這是現階段 RfL out-of-tree 開發的真實摩擦。

### `make LLVM=1` 為什麼重要

RfL 主要走 LLVM toolchain（`make LLVM=1` 讓 kernel 用 clang/lld 而非 gcc/ld）。原因：Rust 的後端是 LLVM，讓整個 kernel（C 部分也用 clang）和 Rust 部分走同一套 LLVM，codegen 與 LTO 較一致、較少 ABI 對不上的意外。用 GCC build C + LLVM build Rust 也有實驗性支援，但 `make LLVM=1` 是官方主推、最少驚喜的路。

## 在 QEMU 跑：load / unload / dmesg（未實測，理論預期）

**這整段標「未實測，理論預期」**——依官方 `samples/rust/` 的預期行為與 kernel module 標準流程（2026-08）。本機沒 build Rust kernel（太重），給你完整的正確步驟與預期輸出。

正確的驗證環境是：**build 一個開了 `CONFIG_RUST` + `CONFIG_SAMPLE_RUST_MINIMAL=m` 的 kernel，在 QEMU 裡開機，把 `.ko` 帶進去 `insmod`**。流程（理論預期）：

```bash
# 1. build Rust-enabled kernel（未實測，以小時計）
make LLVM=1 -j$(nproc)                    # build bzImage
make LLVM=1 modules                        # build modules（含 rust_minimal.ko）

# 2. 用 QEMU 開機（帶一個 initramfs 或 rootfs，把 .ko 放進去）
qemu-system-x86_64 -kernel arch/x86/boot/bzImage \
    -initrd my_initramfs.cpio.gz \
    -append "console=ttyS0" -nographic

# 3. 在 QEMU guest 裡（未實測，理論預期輸出）
insmod rust_minimal.ko test_parameter=5
dmesg | tail
```

`insmod` 後 `dmesg` 的**預期輸出**（依 `rust_minimal.rs` 的 `pr_info!` 內容，理論預期）：

```
[  ...] rust_minimal: Rust minimal sample (init)
[  ...] rust_minimal: Am I built-in? false
[  ...] rust_minimal: test_parameter: 5
```

- `(init)` 來自 `init` 開頭的 `pr_info!`。
- `Am I built-in? false` 來自 `!cfg!(MODULE)`——因為我們 `insmod` 進去（是可載入模組，不是 built-in），所以 `false`。
- `test_parameter: 5` 是我們 `insmod` 時傳的 `test_parameter=5`（覆蓋 default 1）。

`rmmod` 卸載（理論預期）：

```bash
rmmod rust_minimal
dmesg | tail
```

**預期輸出**（來自 `Drop::drop` 的 `pr_info!`）：

```
[  ...] rust_minimal: My numbers are [72, 108, 200]
[  ...] rust_minimal: Rust minimal sample (exit)
```

`My numbers are [72, 108, 200]` 印的是 `init` 裡 push 進 `KVec` 的三個值——證明那個 `KVec` 從 `init` 一路活到 `rmmod`（module 物件被 kernel 持有整個生命週期），卸載時在 `Drop` 印出來然後自動釋放。這個輸出形狀，正是本機那段純 Rust 邏輯 demo 跑出來的 `My numbers are [72, 108, 200]` / `(exit)`——邏輯一致，只是換成真 kernel 環境。

> **為什麼本機沒跑**：build 一個 Rust-enabled kernel 要 clone Linux 原始碼、裝對版本的 rustc/bindgen、開 `CONFIG_RUST`、`make LLVM=1` 編整個 kernel（數十分鐘到數小時、數 GB 磁碟），再配 QEMU + rootfs。這對「示範一個 hello module」是不成比例的重量。上面的命令與輸出照官方 sample 的行為寫，你在真環境跑會得到這個形狀（時間戳、確切位址會不同）。要親手驗證，最省事的是 RfL 官方提供的或社群整理的「已配好 Rust 的 kernel + QEMU」腳本（見延伸閱讀）。

## 完整對照：C hello module vs Rust module

把整個流程並排（C 是本機可 build 的標準 module，Rust 是理論預期）：

| 步驟 | C module | Rust module（RfL） |
|---|---|---|
| metadata | `MODULE_LICENSE`/`MODULE_AUTHOR` 巨集 | `module!{ license, authors, ... }` |
| 進入點 | `module_init(fn)`，`fn` 回 `int` | `impl kernel::Module { fn init(..) -> Result<Self> }` |
| 退出點 | `module_exit(fn)`，另一個 `void` 函式 | `impl Drop for T`（同一物件的解構） |
| 印訊息 | `pr_info("...")` | `pr_info!("...")` |
| 配記憶體 | `kmalloc`，回 NULL 自己檢查 | `KVec::push(x, GFP_KERNEL)?` |
| init 失敗清理 | 手動 `goto err; kfree;` | ownership + `Drop` 自動 |
| build | `obj-m` + `make -C $KDIR M=$PWD modules` | 同左 + `LLVM=1`（且 kernel 要開 `CONFIG_RUST`） |
| 產物 | `xxx.ko` | `xxx.ko`（同格式，可 `insmod`） |
| 載入/卸載 | `insmod`/`rmmod`/`dmesg` | 完全相同 |

最重要的一列是「退出點」：**C 是兩個沒有型別關聯的函式，Rust 是同一個物件的建構與解構**。這個結構差異一路影響到「資源怎麼管」——C 靠全域變數 + 手動釋放，Rust 靠物件欄位 + 自動 `Drop`。

## 踩雷集錦

1. **`license` 寫錯或不寫 → module 拒載或污染 kernel**：`module!` 的 `license` 必須是 kernel 認得的字串（`"GPL"`、`"GPL v2"`、`"MIT"` 等）。寫成非 GPL 相容的（或寫錯字），module 載入時 kernel 會標記為 tainted（污染），且**用不到只給 GPL module 的 symbol**（很多 kernel API 是 `EXPORT_SYMBOL_GPL`）。C 的 `MODULE_LICENSE` 同樣規則。Rust 這邊少了 `license` 那行直接編不過（`module!` 巨集要求）。

2. **rustc 版本和 kernel 樹要求不符 → 編不過或 `rustavailable` 失敗**：out-of-tree module 用的 rustc 必須符合那棵 kernel 的 `scripts/min-tool-version.sh`（[Ch 37](./37-rust-for-linux-overview.md)）。版本太舊會缺 feature 編不過，太新有時也會踩到 kernel crate 沒跟上的 API。先 `make LLVM=1 rustavailable` 確認。這是 Rust module 比 C module 多出來的一層版本約束。

3. **kernel 沒開 `CONFIG_RUST` → 根本不能 build Rust module**：這是最常見的第一關卡死。`CONFIG_RUST` 沒開（或因 toolchain 不齊根本沒出現在 menuconfig），任何 Rust module 都 build 不了。根因通常在 toolchain（[Ch 37](./37-rust-for-linux-overview.md) 踩雷 5）——先跑 `make LLVM=1 rustavailable` 看它抱怨缺什麼。

4. **忘了 `make LLVM=1` → 用到 gcc 而非 clang/LLVM**：RfL 主推 LLVM。漏了 `LLVM=1`，kernel 用 gcc build C 部分，和 Rust（LLVM 後端）的組合是實驗性的、可能出意外。除非你明確知道在做 GCC 後端實驗，否則一律帶 `LLVM=1`。

5. **以為 `init` 回 `Ok(())` 就好**：不是。`init` 回 `Result<Self>`——`Ok` 裡要放**建好的 module 物件**（`Ok(RustMinimal { numbers })`），不是 `Ok(())`。kernel 要拿這個物件持有它整個生命週期。回 `Ok(())` 型別對不上（`Self` 不是 `()`）編不過。這是「module 是物件」的直接後果。

6. **在 `init`/`Drop` 裡用會 abort 的操作（如 `unwrap`/`panic!`）**：kernel 裡 panic = `BUG()`，比 C 嚴重得多。`init` 裡別 `unwrap`，用 `?` 傳播 `Err`（載入失敗 kernel 會優雅處理）。`Drop` 裡更不能 panic（卸載途中炸掉很難收拾）。這是為什麼你在 RfL code 幾乎看不到 `unwrap`——它跟 kernel「絕不能因小錯倒下」的原則衝突。

## 進階：再往深一層

- **`module!` 巨集展開成什麼**：想真懂，用 `cargo expand`（在能編的環境）或讀 `rust/macros/module.rs`（`module!` 的實作）看它展開後的 code——它生成 `__init`/`__exit` 的 C ABI 橋接函式、`.modinfo` 段的 metadata、module param 的 `kernel_param_ops`。這是 [Ch 37](./37-rust-for-linux-overview.md) 「kernel crate 把 C 樣板包起來」最具體的一例。
- **module param 的型別安全**：`params: { test_parameter: i64 { default: 1 } }` 生成的 param 是型別化的（`i64`），透過 `module_parameters::test_parameter.value()` 讀。對照 C 的 `module_param(x, long, 0644)` + 裸全域變數——Rust 版把 param 包成有型別、有存取控制的東西。
- **`ThisModule` 的用途**：本例 `_module` 沒用到，但真正的 driver 會用它註冊子系統（把 module 自己的 handle 傳給 `register_*`）。[Ch 40](./40-rust-driver.md) 註冊 misc device 時會看到 `ThisModule` 派上用場。
- **面試/研究角度**：能講清楚「為什麼 Rust module 是一個物件而非兩個函式」「`init` 回 `Result<Self>` 的 `Self` 為什麼是 module 本體」「卸載時的清理怎麼從手動變自動」「Rust module 比 C 多出哪層 build 約束」，就是真的理解 RfL module 模型，而不是照抄 sample。

## 動手練習

1. **本機改 init 邏輯**：把本章那段真跑的純 Rust 邏輯 demo 的 `[72, 108, 200]` 改成你要的值，或改成從 0 push 到 N（N 當參數）。故意讓 `try_reserve` 失敗（`build(usize::MAX/8)`），觀察 `init` 回 `Err` 而不是 abort——對照真 kernel 裡 `init` 回 `Err` 時 module 載入失敗但 kernel 不倒。

2. **紙上把 C hello module 改寫成 Rust**：拿一個你寫過的 C hello-world module（`printk` + `module_init`/`exit` + 一個 `kmalloc` 的 buffer），在紙上改寫成 RfL 形狀。重點畫出：C 的兩個函式 + 全域變數 → Rust 的一個 struct + `init` + `Drop`；C 的 `kfree` in exit → Rust 的自動 drop。數一數消掉了幾個手動釋放點。

3. **（有環境的話）真跑一次**：若你願意 build 一個 Rust-enabled kernel（見延伸閱讀的 QEMU 腳本），照本章「在 QEMU 跑」的步驟 `insmod rust_minimal.ko test_parameter=5`，`dmesg` 對照本章的預期輸出，`rmmod` 看 `My numbers are [72, 108, 200]`。這是把本章從「理論預期」變成「你親手驗過」的唯一方法——若你做了，回頭把本章那些「未實測」標記在你的筆記裡改成「已驗」。

## 本章重點整理

- Rust module 是**一個有生死的物件**：`init`（對照 `module_init`）是它的建構子、回 `Result<Self>`（成功回建好的物件，kernel 持有）；`Drop`（對照 `module_exit`）是它的解構子，卸載時自動呼叫、欄位自動釋放。C 是兩個沒有型別關聯的函式 + 全域變數 + 手動釋放。
- `module!` 巨集生成 metadata（`license`/`authors`，對照 `MODULE_*`）+ C ABI 橋接 + module param；`pr_info!` 等對照 `printk` 的 log level 巨集，用 Rust 型別安全的格式化。
- build 兩條路：in-tree（放 `samples/rust`，開 `CONFIG_SAMPLE_RUST_*` config 最省事）、out-of-tree（自己寫 `obj-m` Kbuild，對著已 build 的 kernel `make ... LLVM=1 modules`）。Rust module 比 C 多「rustc 版本 + kernel crate 版本 + `CONFIG_RUST`」的約束。
- 產物是同格式的 `.ko`，`insmod`/`rmmod`/`dmesg` 流程和 C 完全一樣。`insmod` 後 `dmesg` 看 `(init)`，`rmmod` 後看 `Drop` 印的訊息。
- 本章 module build/insmod/QEMU 全部**未實測、理論預期**（本機無 kernel build tree、build Rust kernel 太重）；能本機真跑的只有 `init` 的純 Rust 邏輯骨架（`rustc 1.97.1` 驗過）。API 依 2026-08 主線，未穩定會變。

## 自我檢核

- [ ] 不看筆記，能寫出（或讀懂）一個最小 Rust module 的四個部分：`module!`、`struct`、`impl kernel::Module { init }`、`impl Drop`，並說出每個對照 C 的什麼。
- [ ] 能解釋為什麼 `init` 回 `Result<Self>` 而不是 `int`、`Ok` 裡為什麼要放 module 物件而非 `()`。
- [ ] 能說出 Rust module 的卸載清理怎麼從 C 的「手動寫 exit + kfree」變成「物件 drop 自動」，以及這消滅了什麼 bug。
- [ ] 能講出 build Rust module 比 C 多哪幾層約束（rustc 版本、kernel crate 版本、`CONFIG_RUST`、`LLVM=1`）。
- [ ] 知道 `insmod` 後 `dmesg` 該看到什麼、`rmmod` 後該看到什麼，以及為什麼 `My numbers are [...]` 證明 `KVec` 活了整個 module 生命週期。

## 延伸閱讀

### 官方文件 / 一手來源

- **[samples/rust/rust_minimal.rs](https://github.com/torvalds/linux/blob/master/samples/rust/rust_minimal.rs) 與同目錄其他 sample**（主線 kernel 樹）
  - **讀哪裡**：`rust_minimal.rs` 全檔（本章逐字引用的來源）、`rust_print_main.rs`（完整的 `pr_*` log level 示範）、`Makefile`（in-tree module 的 build 規則長怎樣）。
  - **學到什麼**：本章 module 結構的**真實、當前版本**原始碼；`module!` 的完整欄位、`params` 的用法、`pr_*` 全家族。
  - **前提**：讀完本章對 module 四部分的理解；帶著「對照 C 的什麼」的問題去讀最有效。

- **kernel 樹 `Documentation/rust/` 與 `Documentation/kbuild/modules.rst`**（[docs.kernel.org](https://docs.kernel.org/rust/index.html)）
  - **讀哪裡**：`Documentation/rust/general-information.rst`（Rust module 概念、bindings vs abstractions）；`Documentation/kbuild/modules.rst`（out-of-tree module 的 `obj-m`/`M=$PWD` 標準流程，本章 out-of-tree 那段的依據）。
  - **學到什麼**：本章 build 系統斷言的權威來源；out-of-tree module 的完整 Kbuild 規則（本章只給骨架）。
  - **前提**：懂本章 in-tree/out-of-tree 兩條路；要實際 build 時的必查。

### 部落格 / 實作指南

- **[Rust for Linux 官網的 "Contributing" 與 QEMU 指引](https://rust-for-linux.com/)** — RfL 官方
  - **讀哪裡**：官網上「how to build and run」相關頁面，以及社群整理的 QEMU + 已配 Rust 的 kernel 快速啟動流程。
  - **學到什麼**：把本章「未實測、理論預期」的 build+QEMU 流程變成你能真跑的具體腳本——這是驗證本章預期輸出的正路。
  - **前提**：讀完 [Ch 37](./37-rust-for-linux-overview.md) 的 toolchain 步驟 + 本章 build 概念；你願意花時間 build 一個 Rust kernel 時來這裡。

- **《The Linux Kernel Module Programming Guide》（LKMPG）的 hello module 章** — 社群維護（[sysprog21.github.io/lkmpg](https://sysprog21.github.io/lkmpg/)）
  - **讀哪裡**：「Hello World」那幾章（C module 的 `module_init`/`module_exit`/`printk`/`insmod`/`rmmod`/`dmesg` 完整流程）。
  - **學到什麼**：本章對照的 **C 那半邊**的權威、可真跑版本——你若對 C module 流程生疏，先跑一遍 LKMPG 的 hello module，再回來看 Rust 版的對照會非常有感。
  - **前提**：無（這是 C module 入門）；當本章的 C 對照基準。

module 的骨架、build、跑的流程都清楚了。下一章往這個骨架上加真正的功能——寫一個**字元裝置 / misc device** driver：實作 `file_operations` 的 Rust 對應（`open`/`read`/`write`），用 `copy_to_user`/`copy_from_user` 的安全封裝跟 user space 交換資料，並用 [Ch 38](./38-kernel-abstractions.md) 的 `Mutex<T>` 保護裝置狀態。那才是「Rust 能寫真 driver」的實證。

→ [Ch 40 Rust driver：字元/misc device](./40-rust-driver.md)
