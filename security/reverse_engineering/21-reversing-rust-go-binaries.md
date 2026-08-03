# Ch 21 — 逆 Rust / Go binary：為什麼更難

> **目標**：理解 Rust 和 Go binary 逆向的獨特挑戰，掌握在這兩個語言的「胖 binary」中定位關鍵函式的方法。Go 部分在 WSL 真跑（Go 1.18 已安裝），Rust 部分標「未實測，理論預期」並附 godbolt 驗證路徑。

> **環境**：WSL2 / Linux x86-64，Go 1.18，objdump，readelf，strings，nm。Rust 未安裝（標理論預期；可用 [godbolt.org](https://godbolt.org/) 選 rustc 驗證）。

## 為什麼需要這個？

現代安全工具、惡意程式、IoT firmware 越來越多用 Rust 和 Go 寫——Rustls、Cloudflare 的邊緣工具、各種 C2 框架（Sliver、Havoc、BRc4）都已遷移到這兩個語言。

熟悉 x86-64 ELF/C 逆向的人面對 Go/Rust binary，最常見的反應是「怎麼這麼大」和「符號全沒了怎麼辦」。這兩個問題有很不同的答案。本章先系統化地問清楚為什麼難，再逐一破解。

## 先建立直覺：兩種語言的核心難點

```
C binary                  Go binary                  Rust binary
─────────────────────── ─────────────────────────── ──────────────────────────
大小：16 KB（動態）       大小：1-10 MB（靜態）       大小：1-10 MB（靜態）
依賴：系統 libc           依賴：Go runtime（GC/goroutine 靜態打包）
函式數：幾十到幾百         函式數：幾千（+runtime 幾千）  函式數：幾千（monomorphization）
符號：strip 後全無         符號：strip 後 gopclntab 仍在！符號：strip 後基本無名
calling conv：SysV ABI   Go 1.17+ register-based       SysV ABI（extern "C"）
錯誤指紋：無               panic 字串、源碼路徑           panic 字串（更豐富）
```

## Go Binary：逆向者的天堂與地獄

### gopclntab：strip 不掉的函式名表

Go binary 最重要的逆向特性：**即使 `strip` 之後，函式名通常仍然存在**——藏在 `.gopclntab` section 裡。

真跑驗證（Go 1.18，WSL）：

```bash
$ cat > /tmp/re_part3/main.go << 'GOEOF'
package main

import (
    "fmt"
    "strings"
)

func greet(name string) string {
    return "Hello, " + strings.ToUpper(name) + "!"
}

func fib(n int) int {
    if n <= 1 { return n }
    return fib(n-1) + fib(n-2)
}

func main() {
    fmt.Println(greet("world"))
    fmt.Printf("fib(10) = %d\n", fib(10))
}
GOEOF

$ go build -o /tmp/re_part3/gobin /tmp/re_part3/main.go
$ /tmp/re_part3/gobin
Hello, WORLD!
fib(10) = 55
```

```bash
$ file /tmp/re_part3/gobin
gobin: ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked,
       Go BuildID=8nKa0pdjZVoMafAR0hhn/..., not stripped

$ ls -lh /tmp/re_part3/gobin
-rwxr-xr-x 1 ypp ypp 1.8M Aug  3 01:59 /tmp/re_part3/gobin
```

1.8 MB，而 `greet`/`fib`/`main` 的邏輯不超過 20 行——其餘幾乎全是 Go runtime（GC、goroutine scheduler、reflection、type system）。

現在 strip 它，看函式名還在不在：

```bash
$ cp /tmp/re_part3/gobin /tmp/re_part3/gobin_stripped
$ strip /tmp/re_part3/gobin_stripped
$ nm /tmp/re_part3/gobin_stripped 2>&1 | head -3
nm: /tmp/re_part3/gobin_stripped: no symbols    ← ELF symbol table 確實移除了

$ strings /tmp/re_part3/gobin_stripped | grep 'main\.\|fib\|greet'
runtime.main.func1
runtime.main.func2
main.fib
main.main
main.greet
/tmp/re_part3/main.go        ← 甚至有原始碼路徑！
```

`nm` 說沒有符號，但 `strings` 找到了 `main.fib`、`main.greet`、`main.main`，還有原始碼路徑。這些字串來自 `.gopclntab`——`strip` 不動它。

### gopclntab section 的結構

```bash
$ readelf -S /tmp/re_part3/gobin | grep -E 'gopclntab|go\.'
  [ 7] .gopclntab   PROGBITS  00000000004b6ec0  000b6ec0
  [ 8] .go.buildinfo PROGBITS  000000000050c000  0010c000
```

`.gopclntab` 是 Go runtime 用於 stack unwinding 和 goroutine tracing 的資料結構，包含每個函式的：
- 函式名字串
- 入口地址（相對或絕對）
- PC-to-line 對應表（讓 panic 能印出行號）

`strip` 只移除 `.symtab`（ELF symbol table），但 `.gopclntab` 是**執行期必需的資料段**，移除會讓 runtime panic 無法印出 stack trace——Go toolchain 的 `-s`（strip symbols）不會把 gopclntab 移除；要同時加 `-w`（strip DWARF）才能更精簡，但仍保留 gopclntab。只有編譯時顯式用 `-gcflags="-trimpath"` + `-ldflags="-s -w"` 的 Go binary，加上後置 strip 工具（如 `garble`），才能真正移除函式名。

### nm 的 gopclntab 相關符號

```bash
$ nm /tmp/re_part3/gobin | grep -i 'pclntab\|go.func' | head -5
000000000049ec16 r go.func.*        ← 匿名函式集合
```

這個符號說明 gopclntab 中包含函式名資料。

### GoReSym / redress：自動提取 gopclntab

**GoReSym**（[https://github.com/mandiant/GoReSym](https://github.com/mandiant/GoReSym)）是 Mandiant 開發的 Go binary 分析工具，可以解析 gopclntab 並輸出 JSON 格式的函式名、入口地址、原始碼路徑。

**redress**（[https://github.com/goretk/redress](https://github.com/goretk/redress)）同樣功能，更輕量，可生成 Ghidra/IDA 的匯入格式。

若工具未安裝，手動方式：

```bash
$ strings -t x /tmp/re_part3/gobin_stripped | grep 'main\.'
# 輸出含 offset 的函式名列表

$ strings -t x /tmp/re_part3/gobin_stripped | grep '\.go:'
# 源碼路徑有助於理解 package 結構
```

### Go 的 calling convention 變化

Go 1.17 之前所有參數透過 **stack** 傳遞（與 x86-64 SysV ABI 不同）。Go 1.17 起改為 **register-based**，在 x86-64 用 `AX, BX, CX, DI, SI, R8, R9, R10, R11` 傳整數/指標參數。

逆向時看到函式開頭大量從 stack 讀參數（而不是用 `rdi,rsi...`），先確認 Go 版本（`strings binary | grep 'go1\.'` 或 `.go.buildinfo` section）：

```bash
$ strings /tmp/re_part3/gobin | grep '^go1\.'
go1.18.1      ← 確認版本，1.17+ 用 register
```

Go 1.18 的 `fib(5)` 呼叫，參數 `n=5` 透過 `AX`（即 rax）傳遞，而非 C 的 `rdi`——這是最常讓人搞混的點。

### interface / slice / string 的記憶體佈局

```
Go string（2 words，16 bytes）：
  ┌────────────┐
  │ ptr (8B)   │ → 指向 UTF-8 bytes（.rodata 或 heap）
  │ len (8B)   │ → 位元組長度（不含 null terminator）
  └────────────┘

Go slice（3 words，24 bytes，和 C++ std::vector 相似）：
  ┌────────────┐
  │ ptr (8B)   │ → 底層陣列
  │ len (8B)   │ → 現有元素數
  │ cap (8B)   │ → 容量（分配的元素數）
  └────────────┘

Go interface（2 words，16 bytes）：
  ┌────────────┐
  │ itab (8B)  │ → *itab：type info + method table
  │ data (8B)  │ → 指向實際資料（或內聯儲存小值）
  └────────────┘
```

逆向時：兩個連續 8-byte 值、第一個指向唯讀段，是常見 string；三個連續 8-byte 值、有 len < cap 的語義，是 slice；兩個連續 8-byte 值、第一個指向 `.data.rel.ro`（itab table），是 interface。

### Go runtime 的識別與隔離

Go runtime 函式帶有 `runtime.` 前綴，在未 strip 的 binary 裡很容易辨識：

```bash
$ nm /tmp/re_part3/gobin | grep '^.*T runtime\.' | head -10
000000000044a700 T runtime.(*Func).Entry
000000000044a640 T runtime.(*Func).Name
0000000000448f40 T runtime.(*Frames).Next
...
```

有幾千個 `runtime.*` 函式——全是 GC、scheduler、reflection 等基礎設施，逆向業務邏輯時應該**直接忽略**它們（對應 Ch 22 的「隔離已知庫函式」策略）。

## Rust Binary：更難的逆向

**以下為未實測（本環境需另裝 rustup），理論預期——可用 [godbolt.org](https://godbolt.org/) 選 `rustc` 驗證 asm 輸出。**

### 靜態連結 + strip 後幾乎無名

Rust binary 預設靜態連結 Rust stdlib（但不含系統 glibc）。strip 之後，不像 Go 有 gopclntab，Rust binary 的符號幾乎全部消失——逆向難度接近靜態連結 C binary，但函式數量更多。

```bash
# 理論預期
$ rustc -o hello_rust hello.rs      # debug build
$ ls -lh hello_rust                  # ~4 MB（debug）
$ rustc --release -o hello_rs_rel hello.rs
$ ls -lh hello_rs_rel               # ~400 KB（release）
$ strip hello_rs_rel
$ nm hello_rs_rel 2>&1
nm: hello_rs_rel: no symbols        # 幾乎所有符號消失
```

未 strip 的 debug build，符號是完整的 Rust crate path + hash：

```
_ZN4core5slice5index24slice_index_order_fail17h5a7c2cf7bc53f43aE
```

這是 Itanium C++ ABI mangling，但加了 `17h5a7c2cf7...E`（hash suffix）防止符號衝突。工具 `rustfilt`（類似 `c++filt`）可還原：

```bash
# 理論預期
$ rustfilt _ZN4core5slice5index24slice_index_order_fail17h5a7c2cf7bc53f43aE
core::slice::index::slice_index_order_fail
```

這告訴你這是 `core::slice::index` 模組的「索引超界失敗」函式——panic handler 的一部分。

### Monomorphization：函式爆炸

Rust 的泛型（generics）每個類型具現化都產出一個獨立函式：

```rust
fn push<T>(vec: &mut Vec<T>, item: T) { ... }
// 具現化後產出：
// push::<u8>   → 一個函式
// push::<u32>  → 另一個函式
// push::<String> → 又一個函式
```

一個大型 Rust 程式使用大量泛型，產出的函式數量比等量 C++ template 程式碼更多（因為 C++ 有 ICF——identical code folding，可合併相同函式；Rust 的 monomorphization 預設也做 ICF，但仍然大）。

逆向策略：不要試圖理解所有泛型具現化——找到你感興趣的**業務邏輯函式**，再往下追它呼叫的 stdlib 泛型時，知道「這是 `Vec::push::<MyStruct>`」就夠了，不需要完整逆向它。

### Panic 字串：逆向的錨點

Rust 的 panic 機制（`unwrap()`/`expect()`/邊界檢查）都帶有原始碼位置字串：

```
理論上 strings 輸出的 panic 字串：
  "called `Option::unwrap()` on a `None` value"
  "index out of bounds: the len is  but the index is "
  "called `Result::unwrap()` on an `Err` value: "
  "attempt to add with overflow"
  "panicked at 'password check failed', src/main.rs:42:5"
```

最後一個帶有自訂訊息（`expect("password check failed")`）和源碼位置（`src/main.rs:42:5`）——這是業務邏輯程式碼留下的指紋。用 `strings` 篩選這類帶自訂訊息的 panic，找到對應函式：

```bash
# 理論預期
$ strings --radix=x rust_binary | grep 'src/'
  3a110 src/main.rs
  3a11a src/auth.rs
# → 告訴你有哪些源碼模組
```

### Rust 沒有穩定 ABI

Rust 函式呼叫慣例**不在語言規格中保證版本間穩定**——不同 rustc 版本、不同優化等級可能不同。

`extern "C"` 函式（FFI 邊界）用標準 C ABI（SysV ABI on Linux），這些是逆向時最容易理解的函式。純 Rust internal 函式的 ABI 不保證，但在實作上 x86-64 Linux 的 Rust 幾乎總是遵循 SysV ABI（`rdi, rsi, rdx...`）——只是不保證跨版本。

逆向時：先找 `extern "C"` 或 `#[no_mangle]` 函式（它們在 strip binary 裡也常有可讀符號名，因為是 FFI 邊界，刻意保留），再從它們往內追。

## 策略比較：C vs Go vs Rust

| 面向 | C | Go | Rust |
|---|---|---|---|
| 找函式名 | `nm`（未 strip）| `strings` 或 GoReSym（stripped 也有）| `nm`（未 strip）或 panic 字串 |
| 找 main | `_start` → `__libc_start_main` → main | `_start` → `runtime.main` → `main.main` | `_start` → `__libc_start_main` → main |
| calling conv | SysV ABI | Go 1.17+ register（AX/BX...）；1.16- stack | SysV ABI（extern "C"）/ 內部不保證 |
| 標準庫辨識 | PLT import 名 | gopclntab 的 `runtime.*` prefix | panic 字串、`core::`/`std::` 符號 |
| binary 大小 | 小（動態）| 大（1-10 MB，runtime）| 大（1-10 MB，stdlib）|
| strip 後的名字 | 全無 | gopclntab 仍有 | 幾乎全無 |
| 逆向主要策略 | 字串 xref + PLT 名 | strings + gopclntab + call graph | panic 字串 + 找 extern "C" 邊界 |

## 實際逆向流程

### Go binary 的推薦工作流

```
Step 1 偵察
  $ strings -t x gobin | grep 'main\.'   → 列出業務邏輯函式名和 offset
  $ readelf -S gobin | grep gopclntab   → 確認 section 在
  $ strings gobin | grep 'go1\.'        → 確認 Go 版本（決定 calling conv）

Step 2 定位函式
  用 GoReSym（或手動 strings）建立函式名→VA 的對照表
  在 objdump 裡跳到對應地址

Step 3 讀 asm
  Go 1.17+ calling convention：參數在 AX, BX, CX...
  string 傳遞：兩個 register（ptr + len）
  slice 傳遞：三個 register（ptr + len + cap）

Step 4 隔離 runtime
  任何以 runtime. 開頭的函式呼叫可先跳過，不是業務邏輯
```

### Rust binary 的推薦工作流（理論預期）

```
Step 1 偵察
  $ strings rust_binary | grep 'src/'    → 源碼模組名
  $ strings rust_binary | grep 'panicked at'   → 帶自訂訊息的 panic
  $ nm rust_binary | grep -v ' [urwvW] '  → 排除 debug 符號，看實際函式

Step 2 找 FFI 邊界
  $ nm rust_binary | grep '^[0-9a-f]* T [a-z]'  → 無 _Z 前綴的符號 = extern "C"
  這些是最容易讀的函式

Step 3 從 panic 字串定位業務邏輯
  "password check failed" at src/auth.rs:42 → auth 模組在 42 行有邏輯

Step 4 讀 asm
  去除 monomorphization 雜訊：看到 "Vec<SomeType>::push" 就知道在做 push，不用深逆
```

## 對比與取捨

| 情境 | 工具/策略 |
|---|---|
| Go binary，有 gopclntab | GoReSym 自動提取函式名 → 在 Ghidra 匯入 |
| Go binary，被 garble 混淆 | gopclntab 被混淆，靠動態追蹤（gdb + hook）|
| Rust debug binary | `rustfilt` 還原符號，直接閱讀 |
| Rust release stripped | panic 字串錨點 + 從 entry 追 main |
| 兩者通用 | 先看 binary 大小 + strings 偵察 + readelf -S |

## 踩雷集錦

1. **Go `strip` 後以為沒有名字就放棄**：`nm` 說 no symbols 不等於沒有資訊。`strings | grep 'main\.'` 或 GoReSym 都能找回函式名——Go binary 的 gopclntab 是逆向最大的禮物，不要在 `nm` 失敗就停下來。

2. **Go 1.16 以前的 stack-based calling convention**：分析老版本 Go binary，發現函式開頭就從 stack 讀參數（`mov 0x8(%rsp),%rax`），以為是某種自訂 ABI——其實是 Go 1.16 的舊慣例。先用 `strings binary | grep '^go1\.'` 確認版本。

3. **Rust panic 字串太多讓你迷失**：大型 Rust binary 可能有幾萬個 panic 字串（每個 `unwrap()` 一個）。用 grep 篩選帶自訂訊息的（`panicked at '...'` 而不是通用的 `'index out of bounds'`），把純 stdlib 的 panic 先過濾掉。

4. **Go interface 的 itab 讓你以為指標不對**：interface 的第一個 word 是 `itab`（type info + vtable），不是資料指標——`(*itab).fun[0]` 才是方法表的第一個函式。和 C++ vtable 概念相近但佈局不同（C++ vtable 沒有 type info 和函式指標合在一個結構裡）。

5. **Rust monomorphized 符號的 hash suffix 讓 cross-binary 比對失效**：不同編譯的相同函式，hash suffix 不同。做 binary 相似度比對（Ch 28）時要先去掉 hash suffix 再比名字。

## 進階：再往深一層

- **GoReSym + Ghidra 自動重建函式名**：把 GoReSym 的 JSON 輸出匯入 Ghidra，整個 binary 自動帶上函式名——大幅縮短 Go binary 的逆向時間。值得花 30 分鐘設定一次。
- **Go runtime 的 moduledata**：`runtime.moduledata` 儲存整個 binary 的型別資訊、函式表、pcln table——GoReSym 的核心就是解析這個結構。理解 moduledata 讓你能寫自訂腳本解析任意版本 Go binary。
- **Rust `cargo-inspect` + LLVM IR**：Rust source 可用 `cargo rustc -- --emit=llvm-ir` 輸出 LLVM IR，比純 asm 更容易和 binary 對照——讓 ground-truth 迴圈快很多（接 `ssa_optimizations` 課）。

## 本章重點整理

- Go binary 的 **`.gopclntab` section** 在 `strip` 後仍然存在，`strings | grep 'main\.'` 或 GoReSym 能找回函式名——是 Go 逆向最重要的資源。
- Go 1.17+ 改為 register-based calling convention（AX/BX/CX...），1.16 以前是 stack-based——先確認版本。
- Go string = ptr+len 兩個 word；slice = ptr+len+cap 三個 word；interface = itab+data 兩個 word。
- Rust binary strip 後接近靜態連結 C——靠 **panic 字串**（帶原始碼位置）定位業務邏輯函式。
- Rust 無穩定 ABI；找 `extern "C"` / `#[no_mangle]` 函式是最容易讀的入口。
- 兩者 binary 都大（1-10 MB），主策略：先建函式地圖（gopclntab/panic 字串），再用 Ch 22 的分而治之只逆需要的路徑。

## 自我檢核

- [ ] 我能解釋為什麼 Go binary 在 `strip` 後 `nm` 無輸出，但 `strings | grep main.` 仍有結果
- [ ] 我知道 `.gopclntab` 是什麼，以及 `readelf -S` 怎麼確認它存在
- [ ] 我理解 Go 1.17 前後 calling convention 的差異，能從 asm 看出差別（stack 讀 vs register）
- [ ] 我知道 Rust binary 逆向時，帶自訂訊息的 panic 字串是業務邏輯的錨點
- [ ] 我能解釋 Go string（ptr+len）和 slice（ptr+len+cap）的記憶體佈局

## 延伸閱讀

1. **Go runtime 源碼：`runtime/symtab.go`**（[https://go.dev/src/runtime/symtab.go](https://go.dev/src/runtime/symtab.go)）
   - 學什麼：gopclntab 的解析邏輯，`Func`、`Frames` 等結構的定義——直接讀出 runtime 如何使用這個資料
   - 前提：能讀 Go source

2. **GoReSym 的 README 和源碼**（[https://github.com/mandiant/GoReSym](https://github.com/mandiant/GoReSym)）
   - 學什麼：gopclntab 格式的詳細說明（Go 1.2 到 1.20 的格式演進），工具使用，Ghidra 匯入流程
   - 前提：裝好 Go + Ghidra

3. **Reverse Engineering Rust Binaries（DEFCON 30 talk）** — 搜尋 "reversing rust binary defcon"
   - 學什麼：完整的 Rust binary 逆向案例，panic 字串利用、monomorphization 識別、unsafe 程式碼定位
   - 前提：了解 Rust 基礎（或本課 `rust` 課程）

現代語言的 binary 體積和複雜度讓我們需要更系統化的策略——下一章是靜態連結、去符號的大 binary 的分而治之方法。

→ [Ch 22 逆靜態連結 / 去符號的大 binary](./22-reversing-stripped-static-binaries.md)
