# Ch 31 — Rust 與 Go 除錯

> **目標**：把 GDB 用到 Rust 與 Go——`rust-gdb` 包裝與 Rust 的 enum/Option/trait object 顯示、Go 的 runtime/goroutine/channel 與為什麼 Go 的 GDB 體驗較差。學完你能 debug 這兩個現代語言，並理解它們的 runtime 為 debugger 帶來的挑戰。

> **環境**：GDB 13/14，Linux x86_64，rustc 1.70+、go 1.21+。

## 為什麼 Rust/Go 需要特別講

GDB 是為 C/C++ 設計的。Rust 和 Go 雖然都產生 DWARF、都能用 GDB，但它們有自己的型別系統與 runtime，直接用 GDB 會遇到：

- **Rust**：enum（尤其 `Option`/`Result`）顯示、trait object 的胖指標、`String`/`Vec` 內部、所有權不影響 debug 但型別名很長。好消息：Rust 的 GDB 支援相當好（有 `rust-gdb`）。
- **Go**：goroutine（不是 OS thread）、channel、interface、自己的 scheduler 與 GC——這些 runtime 概念 GDB 不原生理解，所以 Go 的 GDB 體驗較差，官方更推 Delve（dlv）。

知道每個語言「GDB 行不行、哪裡卡、有沒有更好的工具」，比硬用 GDB 重要。

## Rust：用 `rust-gdb`

Rust 工具鏈附帶 `rust-gdb`——它是個 wrapper，啟動 GDB 並載入 Rust 專用的 pretty-printer：

```bash
rustc -g hello.rs -o hello       # 或 cargo build（debug profile 預設 -g）
rust-gdb ./hello                 # 不要直接用 gdb！
```

`rust-gdb` vs `gdb`：前者自動載入 Rust 的 printer，讓 `Vec`、`String`、`Option` 漂亮顯示。直接用 `gdb` 看到的是一坨內部結構。

```rust
// demo.rs — rustc -g demo.rs
fn main() {
    let v: Vec<i32> = vec![10, 20, 30];
    let s = String::from("hello");
    let opt: Option<i32> = Some(42);
    let none: Option<i32> = None;
    println!("{:?} {} {:?} {:?}", v, s, opt, none);  // break here
}
```

```
(rust-gdb) break demo.rs:7
(rust-gdb) run
(rust-gdb) print v
$1 = Vec(size=3) = {10, 20, 30}        # 漂亮（rust-gdb 的 printer）
(rust-gdb) print s
$2 = "hello"
(rust-gdb) print opt
$3 = core::option::Option<i32>::Some(42)   # enum 顯示變體！
(rust-gdb) print none
$4 = core::option::Option<i32>::None
```

## Rust 的型別挑戰

**enum（Rust 的核心）**：Rust enum 是 tagged union，GDB 顯示當前變體：

```
(rust-gdb) print result            # Result<T, E>
$5 = core::result::Result<i32, ...>::Ok(100)
(rust-gdb) ptype opt
type = enum core::option::Option<i32> {None, Some(i32)}
```

**trait object（胖指標）**：`&dyn Trait` 是 (data_ptr, vtable_ptr) 的胖指標：

```
(rust-gdb) print boxed_trait
$6 = ... {pointer = 0x..., vtable = 0x...}   # 兩個指標
```

**所有權/借用不影響 debug**：runtime 沒有所有權概念，那是編譯期檢查。debug 時你看到的就是普通記憶體。

**符號名超長**：Rust mangling（v0 scheme）產生 `_RNvNtCs...` 這種。`rust-gdb` 會 demangle，但巢狀泛型仍很長。

> 認識論誠實：Rust 的 GDB 支援雖好但非完美——某些複雜泛型、async/await 的 state machine（Future 編譯成的 enum）、巨集展開後的程式碼，debug 體驗仍不如 C。async Rust 的 debug 是公認的痛點（stack 是邏輯的、不是真實的）。

## Go：runtime 的挑戰

Go 能用 GDB（`go build -gcflags="all=-N -l"` 關最佳化 + inline 以利 debug），但體驗明顯較差，原因在 Go 的 runtime：

```go
// demo.go — go build -gcflags="all=-N -l" demo.go
package main
import "fmt"
func worker(id int, ch chan int) { ch <- id * 10 }
func main() {
    ch := make(chan int, 3)
    for i := 0; i < 3; i++ { go worker(i, ch) }   // goroutine！
    s := []int{1, 2, 3}
    m := map[string]int{"a": 1}
    fmt.Println(s, m)                              // break here
    for i := 0; i < 3; i++ { fmt.Println(<-ch) }
}
```

Go 為 debugger 帶來的問題：

1. **goroutine 不是 OS thread**：Go 用 M:N scheduler 把成千上萬 goroutine 排到少數 OS thread 上。GDB 的 `info threads` 看到的是 OS thread（M），**不是** goroutine。你想看的「goroutine 3 在幹嘛」，GDB 原生看不到。
2. **stack 會移動**：Go 的 goroutine stack 是可增長的（contiguous stack），runtime 會搬移它——GDB 對「stack 位址會變」很不適應。
3. **GC**：垃圾回收會移動/掃描物件，debug 時的記憶體狀態較動態。
4. **interface 與 channel**：是 runtime 結構，GDB 顯示成內部欄位，不直覺。

```
(gdb) print s
$1 = []int = {1, 2, 3}              # slice 還算 OK（Go runtime-gdb.py）
(gdb) print m
$2 = map[string]int = {"a" = 1}     # map 也可
(gdb) info goroutines               # 需要載入 Go 的 runtime-gdb.py 才有
```

Go 工具鏈附帶 `runtime-gdb.py`（在 `$GOROOT/src/runtime/`），提供 `info goroutines`、`goroutine N bt` 等擴充。但即便如此，Go 的 GDB 體驗仍不理想。

## Go 該用 Delve，不是 GDB

殘酷的事實：**debug Go 請用 Delve（dlv），不是 GDB。**

```bash
go install github.com/go-delve/delve/cmd/dlv@latest
dlv debug demo.go
(dlv) break main.main
(dlv) continue
(dlv) goroutines              # 原生理解 goroutine！
(dlv) goroutine 5 stack       # 看特定 goroutine 的 stack
(dlv) print s
```

Delve 是專為 Go 設計的 debugger，**原生理解 goroutine、channel、Go 的 runtime**。Go 官方文件明說 GDB 對 Go 支援有限、不推薦。這門課教 GDB，但對 Go 我必須誠實：知道何時換工具，比硬用 GDB 重要。GDB 的價值在 Go 場景是「核心 dump 事後分析」或「沒有 dlv 的環境」的備案。

## 跨語言的共通點

不管哪個語言，這些 GDB 技能通用：

- 執行控制（break/step/continue，Part 1-2）
- 暫存器與記憶體（Ch 11）——語言無關，組語就是組語
- core dump 分析（Ch 33）——Rust/Go 都能產生 core
- 各語言的 pretty-printer 都建在 Ch 26 的框架上

語言特定的是「型別怎麼顯示、runtime 概念（goroutine/async）懂不懂」。

## 踩雷集錦

1. **Rust 直接用 `gdb` 而非 `rust-gdb`**：少了 Rust printer，`Vec`/`Option` 顯示成一坨內部。用 `rust-gdb`。
2. **Go 用 `info threads` 找 goroutine**：看到的是 OS thread（M），不是 goroutine。要 `info goroutines`（需 runtime-gdb.py）或直接用 dlv。
3. **Go 沒關最佳化就 debug**：預設 build 有 inline 與最佳化，變數一堆 `<optimized out>`。要 `-gcflags="all=-N -l"`。
4. **Go stack 位址變動造成困惑**：goroutine stack 會搬移，舊位址失效。這是 Go runtime 行為，不是 bug。
5. **Rust async 的 stack 不直覺**：`.await` 編譯成 state machine，backtrace 不是你寫的邏輯流程。這是 async 的本質難點。
6. **Rust 符號超長**：巢狀泛型 + trait。`rust-gdb` demangle 但仍長，frame filter（Ch 27）可精簡。
7. **以為 GDB 對 Go 夠用**：多數 Go 場景 dlv 體驗好太多。別硬撐。

## 進階：再往深一層

- **Rust 的 DWARF 擴充**：Rust 用 DWARF 表示 enum（discriminant + variants），GDB 13+ 對此支援良好。看 `readelf --debug-dump=info` 裡 Rust enum 的 DIE。
- **Rust async/Future**：Future 是編譯器產生的 enum state machine，每個 `.await` 是一個 state。debug async 要理解這個轉換——目前工具支援都不完美。
- **Go 的 `runtime-gdb.py` 內部**：它用 GDB Python API（Part 5！）實作 `info goroutines`——遍歷 runtime 的 `allgs` 列表。讀它是 Part 5 的好複習，也展示「為新 runtime 寫 GDB 擴充」的真實案例。
- **Delve 的架構**：dlv 也是建在 ptrace 上（Ch 2），但加了 Go runtime 的知識。理解它和 GDB 的關係。
- **core dump 跨語言**：Rust/Go panic 都能設定產 core（`RUST_BACKTRACE` 是 runtime 的；core 是 OS 的），用 GDB 分析（Ch 33）——這是 GDB 在 Go 場景仍有價值的地方。
- **混合語言**（Rust FFI 呼叫 C、Go cgo）：跨語言邊界的 debug，GDB 能跨（都是 DWARF），但要注意 ABI 邊界。

## 動手練習

1. 寫一個 Rust 程式含 Vec/String/Option/Result，用 `rust-gdb` 看漂亮顯示；再用普通 `gdb` 對比（看到內部結構）。
2. `ptype` 一個 Rust enum，看它的變體；`print` 一個 `Some(x)` 和 `None` 比較。
3. 寫一個 Go 程式開幾個 goroutine，用 `gdb` + `info threads`（看到 OS thread）對比 `info goroutines`（需 runtime-gdb.py）。
4. 同一個 Go 程式用 `dlv debug` + `goroutines` + `goroutine N stack`，對比 GDB 的體驗。
5. Go 程式不加 `-gcflags="all=-N -l"` build，看變數多少 `<optimized out>`，加了再比較。
6. （進階）讀 `$GOROOT/src/runtime/runtime-gdb.py` 的 `GoroutinesCmd`，看它怎麼用 Part 5 的 API 遍歷 goroutine。

## 本章重點整理

- Rust：用 `rust-gdb`（自動載 Rust printer），enum/Option/Result 顯示變體、trait object 是胖指標；支援相當好，但 async 是痛點。
- Go：能用 GDB 但體驗差——goroutine 不是 OS thread（`info threads` 看不到）、stack 會移動、GC 動態。需 `runtime-gdb.py` 才有 `info goroutines`。
- **Go 請用 Delve（dlv）**——原生理解 goroutine/channel；GDB 在 Go 場景是備案（core 分析、無 dlv 環境）。
- 跨語言共通：執行控制、暫存器/記憶體、core 分析、pretty-printer 框架都通用；差別在型別顯示與 runtime 概念。

## 自我檢核

- [ ] Rust 為什麼要用 `rust-gdb` 而非 `gdb`？差在哪？
- [ ] Go 的 `info threads` 為什麼看不到你的 goroutine？怎麼看 goroutine？
- [ ] 為什麼 Go 該用 dlv 而非 GDB？GDB 在 Go 還有什麼價值？
- [ ] Rust 的哪個語言特性 debug 起來最不直覺、為什麼？
- [ ] 跨語言時哪些 GDB 技能是通用的？

## 延伸閱讀

### 官方文件

- **[Rust: rust-gdb / debugging](https://doc.rust-lang.org/book/)** 與 **[Go: Debugging Go Code with GDB](https://go.dev/doc/gdb)**
  - **讀哪裡**：Go 那篇開頭就說「GDB 對 Go 支援有限」，並列出 runtime-gdb.py 的功能與限制。
  - **和本章的關聯**：Go 那篇是「為什麼 GDB 不夠」的官方說法。

### 工具

- **[Delve (dlv)](https://github.com/go-delve/delve)**
  - **讀哪裡**：README + goroutines/goroutine 指令文件。
  - **和本章的關聯**：Go debug 的正確工具；理解它和 GDB 的分工。

### 原始碼

- **[Go runtime-gdb.py](https://github.com/golang/go/blob/master/src/runtime/runtime-gdb.py)**
  - **讀哪裡**：`GoroutinesCmd`、`GoroutineCmd`。
  - **和本章的關聯**：用 Part 5 GDB Python API 為新 runtime 寫擴充的真實範例；很好的進階複習。

下一章是真實世界最硬的一塊：除錯最佳化過的 release binary——`<optimized out>`、inline、tail call，為什麼線上崩潰那麼難 debug。

→ [Ch 32 除錯最佳化過的 binary](./32-debugging-optimized-binaries.md)
