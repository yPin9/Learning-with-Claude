# Ch 33 — 逆向 Rust binary：特徵與 mangling

> **目標**：用 C/C++20 + pwn/RE 的已有直覺快速切入 Rust binary 的逆向。學完你能：（1）用 `strings`、`readelf`、`nm` 在 30 秒內確認「這是 Rust binary」；（2）讀懂 v0 mangling 格式（`_R` 開頭），用 `rustfilt` demangle；（3）理解 Rust binary 為什麼比同功能 C binary 大 10 倍；（4）知道 `strip` 之後還留下哪些 Rust 特徵；（5）對著 IDA/Ghidra 有基本應對策略。

> **環境**：rustc 1.97.1 (8bab26f4f 2026-07-14)，GNU Binutils 2.38，x86-64 Linux（WSL2）。所有 `strings`/`nm`/`objdump`/`readelf` 輸出均本機真跑；IDA/Ghidra 部分標「概念，未本機實測」。

---

## 為什麼需要這個？

你不會永遠只逆向 C/C++ binary。以下場景都需要認識 Rust binary 的特徵：

- **紅隊工具分析**：`ripgrep`、`bat`、`fd`、越來越多 CLI 安全工具用 Rust 寫。你要快速判斷工具行為和信任邊界。
- **供應鏈分析**：從 binary 推斷使用的 crate 版本，配合 RUSTSEC 確認有無已知 CVE。
- **CVE 定位**：拿到廠商 binary（含 Rust 元件），需要找到漏洞函式的位置，寫 PoC 或 patch。
- **CTF RE**：CTF 出現 Rust binary 越來越頻繁，不認識 v0 mangling 就看不懂 symbol。

和 C++ binary 相比，Rust binary 有幾個顯著差異：靜態連結讓體積暴增、mangling scheme 完全不同（v0，非 Itanium）、panic message 嵌入 source path 成為獨特的殘留特徵、泛型單型化（monomorphization）讓 symbol 爆量。知道這些，30 秒辨識一個 Rust binary 不是誇飾。

---

## 先建立直覺

一眼辨識 Rust binary 的五條線索，從最明顯到最微妙：

```
  ┌─ 線索一 ──────────────────────────────────────────────────────┐
  │  strings | grep "/rustc/"                                     │
  │  → 出現 /rustc/<40 hex>/ 路徑                                 │
  │  原理：panic 時印出 source file + line，路徑寫死在 binary 裡   │
  └───────────────────────────────────────────────────────────────┘
  ┌─ 線索二 ──────────────────────────────────────────────────────┐
  │  readelf -p .comment                                          │
  │  → "rustc version X.Y.Z (commithash date)"                   │
  │  → "Linker: LLD ..."                                         │
  └───────────────────────────────────────────────────────────────┘
  ┌─ 線索三 ───────────────────────────────────────────────────────┐
  │  nm binary | grep "^_R"                                        │
  │  → v0 mangling 符號，純 Rust 才有                              │
  │  （舊 Rust 用 _ZN，現代 Rust 2018 edition+ 預設 v0）           │
  └────────────────────────────────────────────────────────────────┘
  ┌─ 線索四 ───────────────────────────────────────────────────────┐
  │  ls -lh binary → 幾 MB，比同功能 C binary 大一個數量級         │
  │  原因：std + 所有依賴靜態連結進去                               │
  └────────────────────────────────────────────────────────────────┘
  ┌─ 線索五 ───────────────────────────────────────────────────────┐
  │  nm binary | wc -l → symbol 數量 1000+                        │
  │  即使功能只有幾十行，monomorphization 產生大量特化符號          │
  └────────────────────────────────────────────────────────────────┘
```

下面逐條用真實輸出驗證。

---

## 特徵一：strings 的告白

`strings` 是最便宜的偵測手段。對一個從未見過的 binary，第一步就是：

```bash
strings binary | grep -E "/rustc/|panicked at|\.rs:"
```

對本次的 `demo_debug` binary（用前面的 `src_main.rs` 編譯），輸出包含：

```
/rust/deps/hashbrown-0.17.1/src/raw.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/rt.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/../../backtrace/src/symbolize/gimli/stash.rs
) panicked at 
/rust/deps/rustc-demangle-0.1.27/src/legacy.rs
```

每一條都在說話：

**`/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/rt.rs`**
這是 Rust compiler 的 commit hash（40 個十六進位字元）加上 stdlib 的相對路徑。路徑格式固定：`/rustc/<hash>/library/<crate>/src/<file>.rs`。hash 對應特定的 rustc 版本，可以在 `rustup` 或 `releases.rs` 查到確切的 compiler 版本。這條路徑嵌入的原因是：panic 發生時，backtrace 要印出 source location，compiler 把路徑直接寫死在 binary 裡。

**`/rust/deps/hashbrown-0.17.1/src/raw.rs`**
第三方 crate 的路徑，格式：`/rust/deps/<crate>-<version>/src/<file>.rs`。這直接洩漏了依賴版本。`hashbrown 0.17.1` 是 `HashMap` 的底層實作——這個 binary 用了 `HashMap`，印證了 source code 的 `use std::collections::HashMap`。**供應鏈分析的核心手法之一**：從 binary 的 strings 拿到完整 crate 版本列表，配合 RUSTSEC 查有無已知 CVE。

**`) panicked at `**
Rust 的 panic message 固定格式：`thread '<name>' panicked at '<message>', <file>:<line>`。這個 substring 只要出現，幾乎可以確認是 Rust binary。C/C++ 不會有這個字串（除非你手刻）。

**`.comment` section 的 rustc version**：

```bash
readelf -p .comment demo_debug
```

```
String dump of section '.comment':
  [     0]  Linker: LLD 22.1.6 (/checkout/src/llvm-project/llvm dcc3606807c989700e0ac1cac18c31741bcd40d9)
  [    5f]  rustc version 1.97.1 (8bab26f4f 2026-07-14)
  [    8b]  GCC: (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
```

三條資訊：
1. **LLD 22.1.6**：Rust 預設用 LLD，不是 GNU ld。C 專案通常是 ld 或 lld，但 `rustc version` 那一行是決定性的。
2. **rustc version 1.97.1 (8bab26f4f 2026-07-14)**：compiler 版本，用來確認 stable/nightly、精確 release date。
3. **GCC: (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0**：系統 GCC，因為 Rust 連結時會用到系統 linker 的部分元件（這條不代表 binary 是 C 寫的）。

`.comment` 沒有 strip 掉，大部分發行版的 Rust binary 都保留這段，這是一級確認指標。

---

## 特徵二：符號 mangling

### v0 vs legacy Itanium

Rust 有兩套 mangling scheme：

| 格式 | 前綴 | 適用 | 範例 |
|------|------|------|------|
| Legacy（Itanium-based） | `_ZN` | 舊 Rust（2018 edition 前）；部分 crate 仍用 | `_ZN4core3fmt9Arguments6new_v117h...E` |
| v0（RFC 2603） | `_R` | Rust 1.37+，Rust 2018 edition+ 預設 | `_RNvCseCUiVmLUaYH_8src_main4main` |

**Rust 2018 edition 之後，`rustc` 預設使用 v0 mangling**，所以現代 Rust binary 裡你會以 `_R` 符號為主。`_ZN` 也可能出現，但那通常是 FFI 或者連結進來的 C++ 元件。

與 C++ Itanium mangling 的差異：C++ 的 `_ZN3foo3barEv` 遵循 [Itanium ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling)，用數字前綴表示 identifier 長度（`3foo` = "foo"，3 個字元），最後一個 `E` 結束 namespace。Rust v0 的格式完全不同，有獨立設計的編碼方案。

### v0 mangling 格式解剖

RFC 2603 定義的 v0 格式（稱為 "Rust v0 symbol mangling"）：

```
_R <path> [instantiating-crate] [vendor-specific-suffix]
```

- 開頭固定 `_R`，後接 path encoding。
- **path**：遞迴結構，對應 Rust 的 module path（crate::module::item）。
- **instantiating-crate**（可選）：泛型實例化的 crate，用 `C` 字元開頭的 crate identifier 表示。
- **vendor-specific-suffix**（可選）：額外的 hash 或後綴。

Crate identifier 格式：`Cs<base62hash>_<crate_name>`
- `Cs` 是固定前綴（"crate" "stable"）。
- `<base62hash>` 是 64-bit disambiguator 的 base-62 編碼，用來區分同名 crate 的不同版本。
- `<crate_name>` 是 crate 的名稱。

Path 內的各種 tag（選取最常見的幾個）：
- `N` = nested path（module/item）；`v` = value item（函式/靜態）；`C` = crate root
- `M` = inherent impl（`impl Foo { fn bar }`）
- `X` = trait impl（`impl Trait for Foo`）
- `I` = generic args（type params）
- `R` = reference；`r` = mutable reference；`u` = unit（`()`）

### 真實符號解讀

用 `nm` 抓出 binary 的符號：

```bash
nm demo_debug | grep "^[0-9a-f].*_R" | head -5
```

```
000000000001ce30 t _RINvMNtCs4NRVxsYgnAr_4core3stre10split_onceReECseCUiVmLUaYH_8src_main
000000000001cfb0 t _RINvMNtCs4NRVxsYgnAr_4core3stre12trim_matchesNvMNtNtB5_4char7methodsc13is_whitespaceECseCUiVmLUaYH_8src_main
```

第一條逐段拆解：

```
_R                             → v0 mangling 前綴
  I                            → 開始 generic args（這是個泛型實例化）
    Nv                         → nested value（函式）
    M                          → inherent impl（<str>:: 的方法）
    Nt                         → nested type（module path）
    Cs4NRVxsYgnAr_4core        → crate: "core"，hash = 4NRVxsYgnAr
    3str                       → module/type: "str"（3 個字元）
    e                          → special: refers back to a previous encoding
    10split_once               → function: "split_once"（10 個字元）
    Re                         → type arg: &str（R = reference，e = str 的 back-reference）
  E                            → 結束 generic args
  CseCUiVmLUaYH_8src_main      → instantiating crate: "src_main"，hash = eCUiVmLUaYH
```

`rustfilt` demangle 直接給你可讀版本：

```bash
echo "_RINvMNtCs4NRVxsYgnAr_4core3stre10split_onceReECseCUiVmLUaYH_8src_main" | rustfilt
```

```
<str>::split_once::<&str>
```

Rust 原始碼裡的 `line.split_once("=")` 就是這個函式，`"="` 的型別是 `&str`，所以 type param 是 `<&str>`。

### nm + rustfilt 批次 demangle

```bash
nm demo_debug | rustfilt | head -10
```

```
<str>::split_once::<&str>
<str>::trim_matches::<<char>::is_whitespace>
<str>::trim_start_matches::<&str>
<[u8]>::copy_within::<core::ops::range::RangeInclusive<usize>>
<std::sync::once_lock::OnceLock<std::sync::reentrant_lock::...>>::initialize::...
<core::fmt::rt::Argument>::new_display::<alloc::string::String>
```

幾個觀察：

1. **`<str>::split_once::<&str>`** — source code 裡 `line.split_once("=")` 的單型化版本，type param 固化為 `&str`。
2. **`<str>::trim_matches::<<char>::is_whitespace>`** — `k.trim()` 展開後是 `trim_matches` + `is_whitespace` closure 的組合，monomorphization 把 closure 的型別寫進 symbol。
3. **`<[u8]>::copy_within::<...>`** — 連 `HashMap` 底層用到的 byte-copy 都被特化進來，symbol 帶著精確的 range type。

這些 symbol 不只是函式名稱，更是**函式 + 型別參數的完整簽名**，比 C++ Itanium mangling 攜帶更多語意資訊（C++ 的 vtable thunk 通常看不到 template arg 的完整型別路徑）。

---

## 特徵三：binary 結構

### 體積與 symbol 數量

```bash
ls -lh demo_debug demo_stripped
```

```
-rwxr-xr-x 1 ypp ypp 4.3M  demo_debug
-rwxr-xr-x 1 ypp ypp 394K  demo_stripped
```

`src_main.rs` 的 source code 不到 25 行，卻產出 4.3MB 的 debug binary。原因：**Rust 靜態連結整個 std**，包括：
- `core`、`alloc`、`std`：stdlib 本體
- `hashbrown`：`HashMap` 的底層實作
- `backtrace`/`rustc-demangle`：panic backtrace 支援
- 所有這些 crate 的 DWARF debug info

symbol 數量對比：

```
debug binary:   1276 symbols
release binary: 1050 symbols（部分函式 inline 後 symbol 消失）
```

1276 個 symbol，source code 只有一個 `parse_kv` 和一個 `lookup`——其他 1274 個來自 std 和依賴的靜態連結。**symbol 爆量是 Rust binary 的標誌，不代表邏輯複雜。** 這和 C binary 動態連結 libc 截然不同——C 的 `ls -l` 只有幾十個 symbol，因為 libc 在外面。

### monomorphization 造成的符號膨脹

Rust 的泛型靠 monomorphization 實作：編譯器為每一個型別參數組合產出獨立的機器碼。`HashMap<String, String>` 和 `HashMap<String, u32>` 是**兩份不同的機器碼**，兩個不同的 symbol。

這在 symbol 裡清晰可見：

```
<str>::split_once::<&str>
<str>::trim_matches::<<char>::is_whitespace>
<str>::trim_start_matches::<&str>
```

三個都是 `str` 的方法，但 type param 不同，對應三個獨立的函式實體。C++ template 也有相同行為，但 Rust 更積極 inline，導致特化版本比你預期多很多。

### main() wrapper：真正的邏輯藏在後面

```bash
objdump -d demo_debug | grep -A15 "^[0-9a-f]* <main>:"
```

```
0000000000020cd0 <main>:
   20cd0:	50                   	push   %rax
   20cd1:	48 89 f2             	mov    %rsi,%rdx
   20cd4:	48 63 f7             	movslq %edi,%rsi
   20cd7:	48 8d 3d 92 d4 ff ff 	lea    -0x2b6e(%rip),%rdi        # 1e170 <_RNvCseCUiVmLUaYH_8src_main4main>
   20cde:	31 c9                	xor    %ecx,%ecx
   20ce0:	e8 fb 8d ff ff       	call   19ae0 <_RINvNtCs2AWtUsOyxgP_3std2rt10lang_startuECseCUiVmLUaYH_8src_main>
```

這段等於：

```c
int main(int argc, char **argv) {
    // 把真正的 main 函式指標傳給 lang_start
    return lang_start(rust_main, argc, argv, 0);
}
```

幾個重點：
- **`main()` 只有 6 行**，全部在做初始化。真正的程式邏輯在 `_RNvCseCUiVmLUaYH_8src_main4main`（demangle = `src_main::main`）。
- **`lea -0x2b6e(%rip), %rdi`**：把 `src_main::main` 的位址（`0x1e170`）載入 `rdi`，作為函式指標傳給 `lang_start`。
- **`_RINvNtCs2AWtUsOyxgP_3std2rt10lang_startuE...`**（demangle = `std::rt::lang_start::<()>`）：Rust runtime 初始化函式，負責設定 panic handler、thread-local storage、signal handler，然後呼叫真正的 `main`。
- `xor %ecx, %ecx`：第四個參數 = 0，對應 `lang_start` 的 `sigpipe` 參數（`0` = 預設處理）。

**RE 實踐**：在 IDA/Ghidra 裡，`main()` 的 cross-reference 只會帶你到 `lang_start`，你需要繼續追 `lang_start` 的第一個參數（`rdi`），才能找到真正的程式邏輯入口。Ghidra 在分析 Rust binary 時如果有 symbol，會直接在 `lang_start` 的呼叫圖裡顯示 `src_main::main`。

---

## strip 之後還剩什麼？

strip 把 symbol table 和 debug info 全部移除：

```bash
nm demo_stripped
```

```
nm: demo_stripped: no symbols
```

體積從 4.3M → 394K（減少 91%），symbol 歸零。但 `strings` 仍然有料：

```bash
strings demo_stripped | grep "/rustc/"
```

```
/rust/deps/hashbrown-0.17.1/src/raw.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/rt.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/../../backtrace/src/symbolize/gimli/stash.rs
) panicked at 
```

**`/rustc/<hash>/` 路徑在 strip 後仍然存在。** 這是 Rust 特有的殘留特徵。

原因：這些路徑不在 `.symtab`（symbol table）或 `.debug_info`（DWARF debug info）裡。它們在 **`.rodata`**（唯讀資料段），是 panic message 的一部分。Rust 的 panic 機制在 source code 裡呼叫 `panic!` 時，會把 source file path 和 line number 靜態嵌入字串，存在 `.rodata`。`strip` 只移除 debug section 和 symbol table，不動 `.rodata`。

對比：
- **C binary strip 後**：通常沒有任何 source path 殘留，因為 C 的 assert/error message 只有你自己手刻的字串。
- **Rust binary strip 後**：所有 `panic!`/`unwrap`/`expect`/`index-out-of-bounds` 路徑的 source file 名稱全部保留在 `.rodata`。

這個特徵有實際用途：即使面對 stripped binary，你仍然能夠：

1. **確認是 Rust binary**（`/rustc/<hash>/` 特徵）。
2. **還原 rustc 版本**（hash 對應特定 compiler 版本）。
3. **推斷使用的依賴**（`/rust/deps/<crate>-<version>/` 路徑）。
4. **找到 panic-prone 的程式碼區域**：`strings -td binary | grep "panicked at"` 後面跟著的字串是 panic message；在 IDA/Ghidra 裡找到這些字串的 xref，就是 panic 發生的函式。

---

## IDA/Ghidra 面對 Rust binary（概念說明）

> **本節是概念說明，未在本機實測 IDA/Ghidra 操作流程。** 資訊來源為 IDA/Ghidra 社群文件與公開部落格（無法在 WSL 直接跑 IDA Pro）。

### 為什麼逆向特別痛

**問題一：monomorphization 爆量**

一個泛型函式在 C++ 裡可能只有幾個特化版本；在 Rust binary 裡，iterator adaptor 鏈每一層都是一個新型別，組合爆炸。你在 IDA 裡看到 1000 個函式，其中 900 個是 `core::iter::` 的各種 adaptor 特化，真正的業務邏輯只有 100 個。

**問題二：iterator 大量 inline**

Rust 的 iterator 在 release build 下幾乎全部 inline，一個 `map().filter().collect()` 鏈會展開成一大段沒有函式呼叫的循環機器碼。你在 IDA 看到的不是 `call filter`，而是直接展開的比較 + 跳轉。

**問題三：沒有 RTTI**

C++ 的 dynamic_cast 和 typeid 依賴 RTTI（run-time type information），逆向時可以靠 `_ZTI...`（type info）symbol 找到類型關係。Rust **沒有 RTTI**，trait object 的 `dyn Trait` 靠 vtable 實作，vtable 是 function pointer 陣列，沒有類型名稱（release build）。不過 vtable 本身還在，只是你需要自己從 vtable 推斷型別，而不是直接讀 RTTI 字串。

### 推薦工具（概念，未本機實測）

- **IDA Pro**：安裝 [`d-demangle`](https://github.com/nbdler/d-demangle)，可以對 `_R` 符號自動 demangle（顯示為 `<str>::split_once::<&str>` 而不是原始的 `_RINv...`）。另外 IDA 的 Lumina server 對 Rust stdlib 函式的識別率在最新版本有所提升。
- **Ghidra**：社群有 [`ghidra-rust`](https://github.com/mateuszk87/ghidra-rust) plugin，以及 Ghidra 12+ 內建有初步的 Rust demangler。啟動方式：Edit > Options > Symbol Table > Demangler Options 選 Rust v0。
- **Binary Ninja**：內建 Rust v0 demangler（2.4 版後），plus `bn-rhai` 可以寫腳本批次 rename。

### 實用的 RE 策略

面對一個沒有 debug symbol 的 Rust binary，實務上的攻略順序：

1. **從 panic string 往上找 caller**：`strings | grep "panicked at"` 拿到所有 panic message，在反組譯工具裡找字串的 xref，定位到 panic 的函式。業務邏輯通常就在 panic 的前幾個指令之前（array bounds check、`unwrap`、`expect` 的位置）。

2. **用 cross-reference 找用途**：找到 vtable（`dyn Trait` 的 fat pointer 的第二個 word 是 vtable 指標），從 vtable 的 function pointer 陣列倒推出 trait 的實作。

3. **靠 strings 建立地圖**：`strings | grep "error\|warn\|panic\|failed"` 可以快速找到錯誤處理的「錨點」，從錨點反推業務邏輯。Rust 程式的錯誤訊息通常比 C 程式詳細（因為 `?` operator 和 `Result` 的慣用法），這反而讓 RE 更容易找到關鍵路徑。

4. **先找 `lang_start` 的第一個參數**：它就是真正的 `main` 函式指標，是分析的起點。

---

## 踩雷集錦

**1. 以為現代 Rust 用 `_ZN` mangling**

Rust 在 2015 edition 初期用了 Itanium-based 的 legacy mangling（`_ZN` 前綴），和 C++ 格式相似但不完全相同。RFC 2603 引入 v0 mangling 後，**Rust 1.37+（2019）開始預設 v0**，也就是 `_R` 前綴。如果你用 `nm | grep "^_ZN"` 想找 Rust 符號，現代 binary 裡幾乎找不到（除了 FFI 連結的 C++ 元件）。正確做法：`nm | grep "^_R"`。

**2. strip 後無 symbols 就以為沒有線索**

`nm demo_stripped` 輸出 `no symbols`，許多人到這裡就放棄從 binary 提取資訊。錯了。Rust binary 的 `.rodata` 裡有 panic message 嵌入的 source path，`strings | grep "/rustc/"` 仍然可以拿到 crate 版本、rustc 版本、source file 路徑。這是 Rust 特有的殘留特徵，strip 無法消除。

**3. `main()` 只有 6 行所以以為邏輯很短**

Rust binary 的 `main()` symbol 是 C ABI 的 entry point wrapper，它的工作是把真正的 `main` 函式指標傳給 `lang_start` 做 runtime 初始化。業務邏輯全部在 `lang_start` 呼叫的第一個參數（函式指標）裡，也就是 `_RNvCseCUiVmLUaYH_8src_main4main`（demangle = `src_main::main`）。如果你在 IDA 看到 `main()` 只有幾條指令就 call 出去，要去追那個被 `lea` 進 `rdi` 的函式。

**4. 認為 Rust 沒 RTTI 所以沒類型資訊**

Rust 沒有 C++ 的 `_ZTI` RTTI，但 `dyn Trait` 的 vtable 仍然包含 function pointer，pointer 指向實際的實作函式。debug binary 的 symbol 直接告訴你 vtable 裡每個 slot 對應哪個函式；stripped binary 你仍然可以透過反追 vtable pointer 的 xref 推斷 trait object 的型別和行為。Rust 的 vtable 比 C++ 更簡單（沒有繼承、沒有多重繼承的菱形問題），格式是：`[drop_in_place, size, align, method_0, method_1, ...]`。

**5. symbol 數量多就認為 binary 沒有 strip**

Rust debug binary 有 1276 個 symbol，但這些大部分是 std 的靜態連結帶進來的，不代表有 debug info。真正的判斷標準是：`readelf -S binary | grep ".debug_info"` 是否存在 `.debug_info` section。有 `.debug_info` 才有 DWARF；symbol table 數量只反映靜態連結的規模，不反映 debug 資訊的豐富程度。

---

## 進階：再往深一層

**DWARF debug info 的利用**

debug binary 裡的 `.debug_info` section 包含完整的型別資訊，包括 struct layout、enum variant、lifetime 範圍（以 DW_AT_variable 的 scope 表示）。用 `dwarfdump` 或 `llvm-dwarfdump` 可以直接提取：

```bash
llvm-dwarfdump --debug-info demo_debug | grep -A5 "DW_TAG_structure_type"
```

這可以還原出 struct 的 field 名稱和 offset，是 stripped binary 以外唯一能拿到 field 名稱的途徑。

**從 binary 推斷依賴版本的供應鏈攻擊面**

`strings binary | grep "/rust/deps/"` 拿到的路徑格式是 `/rust/deps/<crate>-<version>/src/<file>.rs`，可以精確拿到每個 crate 的版本號。配合 RUSTSEC Advisory Database（`rustsec.org/advisories`），可以不需要原始碼就判斷二進位是否使用了有已知 CVE 的依賴版本。這在供應鏈安全分析（例如分析廠商提供的 binary 是否包含已知漏洞的 crate）有直接應用價值。

**binary 版本指紋**

`.comment` section 裡的 `rustc version X.Y.Z (commithash date)` 提供精確的 compiler 版本。不同版本的 rustc 產生的 code 有微妙差異（例如特定的 codegen 優化、MIR 改動），有時可以藉此縮小漏洞存在的時間窗口。

**Ghidra Rust analysis plugin 的狀態**

截至 2026 年，Ghidra 12.x 的 Rust 支援仍然是「可用但不完整」的狀態：v0 demangling 可用，但 iterator adaptor 的型別還原、trait object 的 vtable 分析自動化程度有限。主要還是靠手動 rename + annotation。社群的 `ghidra-rust` plugin 提供更多自動化，但需要 binary 有 symbol（stripped binary 幫助有限）。

---

## 延伸閱讀

**1. RFC 2603 — Rust Symbol Mangling（v0）**
`https://rust-lang.github.io/rfcs/2603-rust-symbol-name-mangling-v2.html`
看這裡：完整的 v0 格式規格，包含所有 grammar production（path/type/const encoding）。你在前面看到的 `_R`、`Cs`、`Nv`、`I...E` 全部有對應的規格描述。如果你要寫自己的 demangler 或 IDA plugin，這是唯一可信的規格來源。值得看的地方：section 3（Syntax）展示完整 BNF，section 4（Examples）對照人類可讀名稱和 mangled 形式。

**2. `rustc-dev-guide` — Symbol Mangling（官方開發文件）**
`https://rustc-dev-guide.rust-lang.org/backend/symbol-names.html`
看這裡：解釋 rustc 內部如何決定哪個函式用哪個 mangling scheme，以及為何 monomorphization 產生的符號帶有 instantiating crate 的 hash。對理解「同一個泛型函式為什麼在兩個 crate 編譯出不同 symbol」這類問題尤其有幫助。也解釋 `#[no_mangle]`、`#[export_name]` 的作用機制。

**3. "Reversing Rust binaries one step at a time" — Cutter/Rizin 部落格**
搜尋這個標題，原文在 `rizin.re` 或其 GitHub blog。
看這裡：從逆向工程師視角（不是 Rust 開發者視角）解釋 Rust binary 的常見模式，包括 `Option<T>` 的 null optimization 在組語的呈現、`Result<T, E>` 的 layout、iterator 展開後的循環識別技巧。作者使用 Rizin/Cutter（開源 RE 工具），但技術分析適用於任何 RE 工具。這份資料補足了本章沒有深挖的「pattern recognition 在 disassembly 裡的具體應用」。

---

## 本章重點

| 主題 | 核心工具 | 關鍵指令 |
|------|---------|---------|
| 識別 Rust binary | strings | `strings binary \| grep "/rustc/"` |
| 確認 compiler 版本 | readelf | `readelf -p .comment binary` |
| 符號 demangle | nm + rustfilt | `nm binary \| rustfilt` |
| strip 後的殘留特徵 | strings | strip 不移除 `.rodata`，panic path 仍在 |
| 找真正的 main | objdump | 追 `lang_start` 的第一個參數（`rdi`） |

Rust binary 對 RE 工程師來說不是「更難」，而是「特徵不同」。一旦知道 `/rustc/<hash>/`、`_R` mangling、`lang_start` wrapper 這幾個固定模式，識別和初步分析的流程和 C/C++ binary 沒有本質差異。真正挑戰在於 monomorphization 爆量和 iterator inline，那是下一章要正面處理的主題。

→ [Ch 34 Rust binary 內部：組語樣貌](./34-rust-binary-internals.md)
