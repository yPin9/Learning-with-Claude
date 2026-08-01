# 練習 E — 逆向 Rust binary 與 audit unsafe crate

> **目標**：把 Ch 33/34 的逆向技術和 Ch 31/32 的 unsafe audit 技術拼起來——既能在 binary 層面辨識 Rust 特徵、讀懂 niche optimization，也能在 source 層面找出 unsound unsafe，並用 Miri 當量尺驗證。

> **環境**：`rustc 1.97.1 (8bab26f4f 2026-07-14)`，nightly toolchain，GNU Binutils 2.38，x86-64 Linux（WSL2）。所有 objdump / nm / Miri 輸出均在此環境真跑產生。

---

## 背景與動機

紅隊工作流是這樣的：拿到一個 binary，第一步不是直接丟 IDA——先判斷它是什麼語言、什麼編譯器版本，決定逆向策略。Rust binary 有幾個顯眼特徵：v0 name mangling（`_R` 前綴）、embedded 的 crate path 字串、`.comment` 段裡的 `rustc version`、以及大量來自 `core`/`alloc` 的泛型實例化 symbol。你不認識這些，你在 Ghidra 看到的函式名都是亂碼，逆向效率直接砍半。

藍隊 / supply chain audit 的工作流也類似：依賴樹裡有個 crate 用了 `unsafe`，你要快速判斷它是不是 sound。Miri 在這裡是比 code review 可靠的工具——它把 Rust 的抽象機器語義搬到 interpreter，會直接告訴你「這是 UB，在第幾行、是什麼類型的 UB」，而不需要你在腦子裡跑 memory model。

這兩個技能在實務中常常要一起用：你 audit 一個 crate 的 unsafe，懷疑它在 binary 層面有問題；或者你在逆向時辨識出某個函式是 `transmute`，開始懷疑 lifetime 被耍了。這個練習讓你把兩件事連起來做一次。

---

## 預備知識確認

在開始前，快速確認你有這些背景——沒有的話先回頭補：

**逆向面**：你應該看過 Ch 33（Rust binary 的 symbol 結構）和 Ch 34（常見 Rust 型別在 assembly 的特徵），特別是 v0 mangling 規則和 niche optimization 的解說。這個練習是把那兩章的「認識」變成「手做過」。

**Audit 面**：你應該看過 Ch 31（unsafe 的 vuln class）和 Ch 32（audit unsafe crate 的方法論），理解「sound」和「unsound」的定義——sound unsafe 意思是「這個 unsafe block 的作者履行了自己聲稱的 safety invariant」，unsound 則是「safe caller 能觸發 UB」。

**工具面**：
- `nm`、`objdump`、`readelf`、`strings` 是 Binutils 的標準工具，WSL2 通常預裝
- `rustfilt`：`cargo install rustfilt`
- Miri：`rustup toolchain install nightly && rustup component add miri --toolchain nightly`

確認環境就緒：`rustfilt --version && cargo +nightly miri --version`。兩個都有輸出才能繼續。

---

## Task A：逆向 Rust binary

### 任務規格

下面是你要分析的目標程式。把它存成 `target_demo.rs`，用 `rustc target_demo.rs -o target_demo` 編出 binary（**不要開 `--release`**，debug build 的 symbol 比較完整，適合練習逆向）。

```rust
// 儲存為 target_demo.rs，編譯指令：rustc target_demo.rs -o target_demo
use std::collections::HashMap;

fn parse_kv(input: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for line in input.lines() {
        if let Some((k, v)) = line.split_once("=") {
            map.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    map
}

fn lookup(map: &HashMap<String, String>, key: &str) -> Option<String> {
    map.get(key).cloned()
}

fn main() {
    let data = "host=localhost\nport=8080\nname=demo";
    let cfg = parse_kv(data);
    match lookup(&cfg, "port") {
        Some(v) => println!("port = {}", v),
        None     => println!("no port"),
    }
}
```

你要完成下面四件事：

| 步驟 | 任務 | 工具 |
|---|---|---|
| **A-1** | 判斷這是 Rust binary 嗎？用什麼 Rust 版本？ | `strings`、`readelf` |
| **A-2** | 列出所有 v0 mangled symbol 的前五條，指出共同前綴 | `nm` |
| **A-3** | 安裝 `rustfilt`，demangle 全部 `_R` symbol，找出 `lookup` 和 `parse_kv` | `nm` + `rustfilt` |
| **A-4** | 反組譯 `lookup` 函式（地址從 nm 找），回答：`lookup` 回傳什麼型別？在 `main` 裡，None check 用的是什麼指令 + 什麼 magic value？解釋為什麼 | `objdump -d` |

### 期望輸出

完成 A-4 後，你應該能回答：

- 「這是 Rust binary，版本 1.97.1（或你跑的 rustc 版本），因為 `readelf -p .comment` 看到 `rustc version`，且 `.comment` 裡 linker 是 LLD。」
- 「v0 mangled symbol 前綴是 `_R`，例如 `_RINvMNtCs4NRVxsYgnAr_4core3str...`。」
- 「Mangle 前的 `lookup` 和 `parse_kv` 都出現在 `rustfilt` 輸出裡，格式是 `src_main::lookup`、`src_main::parse_kv`。」
- 「`cmpq $0xffffffffffffffff` 是 `Option<String>` 的 niche optimization——`None` 用 `String` 內部的 ptr 欄位設成 null（0xffffffffffffffff 是 -1，即 all-ones，等同 null 的 niche 值）表示，因為 Rust 保證有效 `String` 的 ptr 欄位絕對不是 null，所以這個 bit pattern 可以安全借給 `Option` 當 None tag 用，不需要額外的 discriminant byte。」

### 如果你卡住了

1. **`strings` 找不到 rustc 字串** → 確認你跑的是 debug build（不加 `--release`），strip 過的 binary 會少很多字串。用 `nm target_demo` 確認有沒有 symbol 輸出；有輸出代表沒有被 strip。

2. **`nm` 裡找不到 `_R` 開頭的 symbol** → 篩選方式要含空格：`nm target_demo | grep " _R"`。純 `grep "_R"` 會也把名稱含 `_R` 片段的其他 symbol 撈進來。

3. **`rustfilt` 不在 PATH** → `cargo install rustfilt`，裝完確認 `~/.cargo/bin` 有在 `$PATH`。完整指令：`nm target_demo | grep "_R" | awk '{print $3}' | rustfilt | head -20`。

4. **`objdump` 輸出太長，找不到 lookup** → 先用 `nm` 找地址：`nm target_demo | rustfilt | grep "lookup"`，拿到地址後 `objdump -d target_demo | grep -A 50 "<lookup>"` 或直接 `--start-address=0x地址`。

5. **看到 `cmpq $0xffffffffffffffff` 不知道這是什麼** → 回看 Ch 34「Option<T>：niche 過的辨識方法」一節，重點是 Rust compiler 保證 `String` 的 ptr 欄位不可能是 null，所以 `Option<String>` 可以用 ptr==null（或 ptr==!0）代表 None，省掉一個 byte 的 tag。

### 逆向過程建議順序

不要跳過步驟。A-1 到 A-4 是刻意設計成遞進的：A-1 確認是 Rust binary，A-2 看原始 mangling，A-3 用工具解讀，A-4 才進到組語分析。如果你直接跳 A-4，你在看的反組譯輸出裡，函式名全是亂碼，你不知道哪個是 `lookup`、哪個是標準庫函式，沒辦法做有效分析。先把 symbol table 整理清楚，objdump 才有意義。

逆向 Rust binary 和逆向 C++ binary 的一個關鍵差異：Rust 的泛型 monomorphization 會產生**大量** symbol，一個簡單的 `HashMap<String, String>` 內部就有十幾個實例化的函式。`rustfilt` 之後你會看到名稱像 `<HashMap<String, String>>::insert` 之類的，這些都是你「自己的程式」間接用到的標準庫函式。找用戶函式（`parse_kv`、`lookup`、`main`）時，看 demangled 名稱裡 crate name 前綴，排掉 `core::`、`alloc::`、`std::` 開頭的就是。

### 完整參考解答

**寫完再看。**

<details>
<summary>點開 Task A 完整解答</summary>

#### A-1：辨識 Rust binary 與版本

```
$ strings target_demo | grep -E "rustc|rust|panicked"
/rust/deps/hashbrown-0.17.1/src/raw.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/rt.rs
/rustc/8bab26f4f68e0e26f0bb7960be334d5b520ea452/library/std/src/../../backtrace/src/symbolize/gimli/stash.rs
) panicked at
/rust/deps/rustc-demangle-0.1.27/src/legacy.rs
```

`strings` 看到兩件事：一是 `/rustc/<commit hash>/library/...` 格式的 embedded source path，這是 Rust 編譯器在 debug info 裡埋的；二是 `panicked at`，這是 Rust panic handler 的固定字串，C/C++ binary 裡不會有這個。

更精確的版本從 `.comment` 段讀：

```
$ readelf -p .comment target_demo
String dump of section '.comment':
  [     0]  Linker: LLD 22.1.6 ...
  [    5f]  rustc version 1.97.1 (8bab26f4f 2026-07-14)
```

結論：Rust binary，`rustc 1.97.1`，linker 是 LLVM LLD（Rust 預設 linker，不是 GNU ld）。

#### A-2：v0 mangled symbol

```
$ nm target_demo | grep "^[0-9a-f]* [a-zA-Z] _R" | head -5
000000000001ce30 t _RINvMNtCs4NRVxsYgnAr_4core3stre10split_onceReECseCUiVmLUaYH_8src_main
000000000001cfb0 t _RINvMNtCs4NRVxsYgnAr_4core3stre12trim_matchesNvMNtNtB5_4char7methodsc13is_whitespaceECseCUiVmLUaYH_8src_main
```

共同前綴：`_R`。這是 Rust v0 name mangling scheme（RFC 2603）的規定，用來區分 Rust symbol 與 C 的 `_Z`（Itanium ABI）或 MSVC 的 `?`。v0 的結構編碼了 crate hash、namespace path、泛型參數，直接讀很痛苦——所以有 `rustfilt`。

#### A-3：demangle 找 lookup / parse_kv

```
$ nm target_demo | grep "_R" | awk '{print $3}' | rustfilt | head -5
<str>::split_once::<&str>
<str>::trim_matches::<<char>::is_whitespace>
<core::fmt::rt::Argument>::new_display::<alloc::string::String>
src_main::main
src_main::lookup
src_main::parse_kv
```

`src_main::lookup` 和 `src_main::parse_kv` 都在，crate name 是 `src_main`（因為用 `rustc` 直接編，沒有 Cargo，rustc 把檔名 `target_demo.rs` 前置改成 `src_main`；用 Cargo 編的話這裡會是真正的 crate name）。

要找到 `lookup` 的地址，反向查：

```
$ nm target_demo | grep "_R" | awk '{print $1, $3}' | rustfilt | grep "lookup"
000000000001e310 src_main::lookup
```

地址 `0x1e310`，記下來。

#### A-4：反組譯 lookup 與 None check

lookup 函式反組譯：

```
000000000001e310 <_RNvCseCUiVmLUaYH_8src_main6lookup>:
   1e310:	48 83 ec 28          	sub    $0x28,%rsp
   ...
   1e343:	e8 98 f0 ff ff       	call   1d3e0 <...HashMap::get...>
   1e350:	e8 8b 06 00 00       	call   1e9e0 <...Option::cloned...>
   1e35e:	c3                   	ret
```

`lookup` 呼叫 `HashMap::get`（回傳 `Option<&String>`），再呼叫 `Option::cloned`（把 `&String` 克隆成 `String`），最終回傳 `Option<String>`。整個函式體很薄，因為邏輯簡單，大部分工作在被呼叫的函式裡。

main 裡的 None check（在 `match lookup(...)` 分支）：

```
   1e1db:	48 83 7c 24 58 ff    	cmpq   $0xffffffffffffffff,0x58(%rsp)
   1e1e1:	48 0f 44 c1          	cmove  %rcx,%rax
   1e1e5:	48 a9 01 00 00 00    	test   $0x1,%rax
   1e1eb:	74 2e                	je     1e21b   <-- None 分支（跳到 "no port"）
```

`cmpq $0xffffffffffffffff`：把 `Option<String>` 在 stack 上的 ptr 欄位（`0x58(%rsp)`）和 `0xffffffffffffffff`（即 -1，all-bits-one）比較。這是 **niche optimization**：

- Rust 保證合法 `String` 的內部 `ptr` 欄位不可能是 null（0x0）或 all-ones（0xffff...）——這些是 niche 值。
- 所以 `Option<String>` 不需要額外一個 byte 當 discriminant；直接用 ptr 欄位的 niche 值（這裡用 all-ones）代表 `None`。
- `sizeof(Option<String>) == sizeof(String) == 24 bytes`，而不是 25 bytes。
- `cmpq $0xffffffffffffffff, ptr_field; je None_branch` 就是在做「ptr 是不是 niche 值？是的話就是 None」這個判斷。

這是 Rust binary 逆向的重要識別點：看到 `cmpq $0xffffffffffffffff` 或 `test ptr_reg, ptr_reg; jz` 緊接著一個分支，九成是 `Option<T>` 的 None check。

</details>

---

## Task B：audit unsafe crate，Miri 驗 UB

### 關於 transmute 的思考框架

在看程式碼前，先記住 audit `transmute` 的標準問法：

1. **bit level 做什麼？** 兩個型別大小一樣嗎（不一樣就連編譯都過不了）？bit pattern 從一個型別轉到另一個，在目標型別裡是不是合法值？
2. **型別系統層面做什麼？** 有沒有謊稱 lifetime？有沒有謊稱 `Send`/`Sync`？有沒有把 `*const T` 變成 `&T` 繞過 aliasing 規則？
3. **有沒有 safe caller 能打破 soundness？** 光 code review 如果答不確定，就寫 Miri 驗。

`transmute::<&str, &'static str>` 的答案依序是：bit level 沒問題（兩者都是 fat pointer，`(ptr: *const u8, len: usize)`，大小相同，bit pattern 合法）；型別系統層面謊稱了 lifetime（把任意 lifetime 升格成 `'static`）；有 safe caller 能打破——所以是 unsound。

### 任務規格

你拿到一個 crate 的 `src/lib.rs`，內容如下。這段程式碼使用了 `unsafe`：

```rust
// audit_target.rs（拿到的 crate src/lib.rs）
pub fn make_static_str(s: &str) -> &'static str {
    // QUESTION: Is this sound?
    unsafe { std::mem::transmute::<&str, &'static str>(s) }
}

pub fn get_last_word(text: &str) -> &'static str {
    let words: Vec<&str> = text.split_whitespace().collect();
    make_static_str(words.last().unwrap_or(""))
}
```

你要完成下面四件事：

| 步驟 | 任務 |
|---|---|
| **B-1** | 閱讀 `make_static_str`：`transmute` 在做什麼？這個 `unsafe` sound 嗎？說明理由 |
| **B-2** | 寫一個 `main.rs`，**觸發 UB**——讓 `make_static_str` 回傳的 `&'static str` 在 source 被 drop 後仍被使用 |
| **B-3** | 用 `cargo +nightly miri run` 執行，貼出 Miri 的錯誤輸出，確認它捕捉到 UB |
| **B-4** | 提出修正：讓函式保持語義（「找最後一個 word 並回傳借用」），但消掉 unsoundness，**不需要 unsafe** |

### B-2 的 Cargo.toml 設定

建立一個小 cargo project：

```
mkdir audit_check && cd audit_check
cargo init
```

把 `audit_target.rs` 的內容放進 `src/lib.rs`，然後在 `src/main.rs` 裡寫觸發 UB 的 caller：

```rust
// src/main.rs —— 你要寫這個來觸發 UB
use audit_check::make_static_str;

fn dangling_ref() -> &'static str {
    let s = String::from("hello world");
    make_static_str(&s)
    // s 在這裡 drop，但回傳的 &'static str 謊稱它活到 'static
}

fn main() {
    let r = dangling_ref();
    println!("{}", r);  // 使用 dangling reference —— UB
}
```

注意：`rustc` **不會** 報錯——因為 `transmute` 欺騙了 borrow checker，讓它以為 lifetime 是 `'static`。只有 Miri 在 runtime 層面追蹤 allocation lifetime，才能抓到這個 UB。這就是 Miri 存在的理由。

### 期望輸出

**B-1 分析**：unsound。`transmute::<&str, &'static str>` 強制把輸入的 lifetime 升格成 `'static`。borrow checker 看到的是「回傳值活到 `'static`」，但實際上 `s` 可能在 caller 那邊被 drop，pointer 變成 dangling。`transmute` 繞過了 borrow checker，把本來應該是編譯期錯誤的問題推到 runtime，只有 UB sanitizer 或 Miri 才能抓到。

**B-3 Miri 輸出**（真實跑出來的）：

```
$ cargo +nightly miri run
error: Undefined Behavior: constructing invalid value of type &str: encountered a dangling reference (use-after-free)
  --> src/main.rs:13:13
   |
13 |     let r = dangling_ref();
   |             ^^^^^^^^^^^^^^ Undefined Behavior occurred here
   |
   = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior
   = help: see https://doc.rust-lang.org/nightly/reference/behavior-considered-undefined.html for further information
```

注意：Miri 的錯誤指向 **use site**（`let r = dangling_ref()`），不是 `transmute` 那行——因為 UB 在 dangling reference 被 **使用** 時觸發，`transmute` 本身只是做了錯誤的聲明，還沒有「非法存取」的動作。

**B-4 正確修正**（不需要 `unsafe`）：

```rust
// 修正：不謊稱 lifetime，讓 borrow checker 正常管理
pub fn make_str<'a>(s: &'a str) -> &'a str {
    s  // sound：回傳的 lifetime 綁定到輸入，borrow checker 知道兩者等長
}

pub fn get_last_word<'a>(text: &'a str) -> Option<&'a str> {
    text.split_whitespace().last()
}
```

關鍵差異：回傳型別從 `&'static str` 改成 `&'a str`，lifetime 跟著輸入走。`get_last_word` 的回傳改成 `Option<&'a str>`，因為 `text` 可能是空字串（沒有 last word），直接讓 caller 處理 `None`，比 `unwrap_or("")` 更正確。整個修正**不用一行 `unsafe`**。

### 如果你卡住了

1. **不確定 make_static_str 為什麼是 unsound** → 在 caller 裡試試這樣寫：`let r = { let s = String::from("hi"); make_static_str(&s) };`——rustc 不報錯，但 `s` 在 `}` 就 drop 了，`r` 卻宣稱活到 `'static`。transmute 讓 borrow checker 相信了謊言，runtime 才是真相。

2. **rustc 沒報錯，不理解為什麼這是 bug** → 這就是 unsound 的定義：不需要 `unsafe` block 的 **safe caller** 就能觸發 UB。`dangling_ref()` 本身是 safe fn，但呼叫它就 UB。Rust 的安全保證在這裡被洞穿了。

3. **Miri 沒有安裝** → `rustup component add miri --toolchain nightly`，然後 `cargo +nightly miri run`。若 nightly 本身沒裝：`rustup toolchain install nightly`。

4. **Miri 輸出看不懂** → 加環境變數看完整 stack trace：`MIRIFLAGS=-Zmiri-backtrace=full cargo +nightly miri run`，可以看到整條 call stack 從 `main` 到 UB 點。

5. **修正後不確定對不對** → `cargo +nightly miri run` 跑修正版，Miri 不報錯才算通過。額外驗證：試圖讓 caller 拿著回傳的 `&'a str` 比原本的 `text` 活得更久——rustc 應該在**編譯期**就拒絕，不再讓問題逃到 runtime。

### 完整參考解答

**寫完再看。**

<details>
<summary>點開 Task B 完整解答</summary>

#### B-1：soundness 分析

`std::mem::transmute::<&str, &'static str>` 的語義：把輸入的 `&str`（帶任意 lifetime `'_`）的 bit pattern 複製到輸出型別 `&'static str`（lifetime 聲稱為 `'static`）。兩個型別在 machine level 一樣（fat pointer：`(ptr, len)` 各 8 bytes），所以 transmute 不改任何 bit，只改 borrow checker 看到的型別資訊。

這讓 borrow checker 相信「回傳的 &str 活到 'static」，但 **source 字串的實際 lifetime 沒有因此延長**。任何 lifetime 比 `'static` 短的字串都可以被傳進來、被 drop，而回傳的「static」引用變成 dangling pointer。

Unsound 的判定標準：存在一段 **safe code**（不含任何 `unsafe` block）的 caller，能觸發 UB。`dangling_ref()` 就是這樣的 safe caller——它本身是 safe fn，卻能產生 dangling reference。

#### B-2：觸發 UB 的 main.rs

```rust
use audit_check::make_static_str;

fn dangling_ref() -> &'static str {
    let s = String::from("hello world");
    make_static_str(&s)
    // s drop 在這裡，回傳的 &'static str 的 ptr 指向已釋放記憶體
}

fn main() {
    let r = dangling_ref();
    println!("{}", r);  // 讀 dangling ptr —— UB
}
```

**為什麼 rustc 不報錯**：borrow checker 看到的型別是 `&'static str`，lifetime 是 `'static`，它認為這個引用活得夠長。問題是 borrow checker 相信了 transmute 的謊言，沒有追蹤底層 allocation 的實際 lifetime。

#### B-3：Miri 輸出解說

```
error: Undefined Behavior: constructing invalid value of type &str: encountered a dangling reference (use-after-free)
  --> src/main.rs:13:13
   |
13 |     let r = dangling_ref();
   |             ^^^^^^^^^^^^^^ Undefined Behavior occurred here
   |
   = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior
   = help: see https://doc.rust-lang.org/nightly/reference/behavior-considered-undefined.html for further information
```

Miri 追蹤每個 allocation 的 lifetime 和 borrow 的有效性，在抽象機器層面執行程式。當 `s` drop 後，Miri 把那塊 allocation 標成 "freed"。當 `dangling_ref()` 回傳的引用（其 ptr 指向那塊 freed allocation）被 caller 接住並使用時，Miri 偵測到「constructing a value of type &str from a dangling pointer」，報 UB。

錯誤指向 **line 13**（`let r = dangling_ref()`）而非 `transmute` 那行，因為 Miri 是在「引用被帶出 drop 點並傳遞給 caller 的那一刻」偵測到 UB。

#### B-4：正確修正與解說

```rust
// src/lib.rs 修正版——消掉所有 unsafe
pub fn make_str<'a>(s: &'a str) -> &'a str {
    s  // 不用 unsafe：&'a -> &'a，borrow checker 完全可以追蹤
}

pub fn get_last_word<'a>(text: &'a str) -> Option<&'a str> {
    text.split_whitespace().last()
    // split_whitespace() 回傳的 &str 借用自 text，lifetime 自動綁定到 'a
    // 回傳 Option 讓 caller 決定 None 怎麼處理，而非硬 unwrap 或回 ""
}
```

修正的核心邏輯：**不要謊稱 lifetime**。回傳型別從 `&'static str` 改成 `&'a str`，告訴 borrow checker「回傳值的存活期和輸入相同」。這才是正確的合約——你借了別人的字串，只能給別人活得一樣久的借用，不能給「永遠」。

`get_last_word` 改用 `text.split_whitespace().last()`，直接讓標準函式庫的 lifetime elision 把 `'a` 自動傳遞。回傳 `Option<&'a str>` 而非 `&'static str` + `unwrap_or("")`，語義更正確：caller 知道「可能沒有 word」，不會拿到一個莫名其妙的空字串。

修正後用 `cargo +nightly miri run` 跑，Miri 不再報錯。試圖讓回傳值比 `text` 活更久——rustc 在**編譯期**報 lifetime 錯誤，問題重新被擋在 borrow checker 這關，而不是逃到 Miri 或 runtime。

</details>

---

## 測試用例

### Task A

| 測試 | 預期結果 | 說明 |
|---|---|---|
| `strings target_demo \| grep "panicked"` | 有輸出 | panic handler 字串是 Rust 的固定特徵 |
| `readelf -p .comment target_demo` | 看到 `rustc version` 和 `LLD` | 版本和 linker 識別 |
| `nm target_demo \| grep " _R" \| wc -l` | 數十條以上 | v0 mangled symbol 數量 |
| `nm target_demo \| ... \| rustfilt \| grep "parse_kv"` | `src_main::parse_kv` | demangle 成功，crate name 正確 |
| `nm target_demo \| ... \| rustfilt \| grep "lookup"` | `src_main::lookup` | 兩個用戶函式都找得到 |
| objdump main 裡找 `cmpq.*0xffffffffffffffff` | 有一條 | niche optimization None check |
| 解釋 `sizeof(Option<String>)` | 24 bytes（不是 25） | niche 省掉 discriminant |

### Task B

| 測試 | 預期結果 | 說明 |
|---|---|---|
| `rustc` 編譯原始 `audit_target.rs` | **編譯成功**，無 warning | transmute 欺騙了 borrow checker |
| `cargo +nightly miri run`（原始版） | `error: Undefined Behavior: ... dangling reference` | Miri 能抓到 transmute 的 UB |
| `rustc` 編譯修正版 | 編譯成功 | 修正版不需要 unsafe 也通過編譯 |
| `cargo +nightly miri run`（修正版） | **無 Miri 錯誤** | 修正後 sound |
| 修正版 `get_last_word("hello world")` | `Some("world")` | 語義正確 |
| 修正版 `get_last_word("")` | `None` | 空字串邊界，不再 unwrap panic |
| 讓修正版回傳值比 `text` 活更久（safe code） | **編譯錯誤**（lifetime 不夠長） | 問題回到 borrow checker 這關 |

---

## 兩個 Task 的關聯

做完 Task A 和 Task B，回頭想一件事：如果你在 Task A 逆向時看到某個函式的反組譯裡有 `movq $0xffffffffffffffff, (%rsp)` 這樣的指令（把 all-ones 寫進一個 8-byte 欄位），你現在應該知道這大概是「把一個 `Option<T>` 設為 `None`」的操作。而如果這個 `Option<T>` 裡的 `T` 是 `&str` 或 `String`，那個 all-ones 就是 niche 值。

反過來，Task B 學到的 `transmute::<&str, &'static str>` 在 binary 層面完全看不出來——兩個型別的 machine representation 一樣，transmute 不產生任何指令，只影響 type information。這是為什麼 audit 不能只靠逆向：type system 層面的謊言在 assembly 裡是隱形的，你要在 source 層面或 Miri 層面才能看到它。

這兩個技能互補：逆向讓你看到「machine 實際做了什麼」，audit + Miri 讓你看到「型別系統聲稱了什麼」。都懂才能做完整的 Rust binary 安全分析。

---

## 延伸挑戰

**挑戰 1：release build 的差異**

把 `target_demo.rs` 用 `--release` 重編：`rustc -O target_demo.rs -o target_demo_release`。再跑同樣的 `nm` + `rustfilt` 和 `objdump`。回答：

- `lookup` 函式還獨立存在嗎？還是被 inline 掉了？
- None check 的指令序列有沒有變化（提示：LTO 或 inline 可能讓 codegen 選不同的 niche check 形式）？
- 你能從 binary 辨識出這是 release build 嗎？有哪些 heuristic（例如 symbol 數量、`.debug_info` 段是否存在）？

**挑戰 2：fuzz parse_kv**

用 `cargo +nightly fuzz` 對 `parse_kv` 建立 fuzz target：

```
cargo install cargo-fuzz
cargo fuzz init
# 在 fuzz/fuzz_targets/fuzz_target_1.rs 裡呼叫 parse_kv
cargo +nightly fuzz run fuzz_target_1
```

讓 fuzzer 跑 30 秒，回答：`parse_kv` 在什麼輸入下會 panic（如果有的話）？它是 `Option::unwrap` 還是 index OOB？`HashMap` 和 `split_once` 的組合有沒有邊界問題？如果 30 秒沒有找到 panic，說明你觀察到的 coverage 情況。

**挑戰 3：真實 RUSTSEC 案例的 Miri 復現**

查 RUSTSEC 資料庫（`https://rustsec.org/`），找 RUSTSEC-2020-0002（`spin` crate 的 data race）或任何一個標記為「unsound」的 advisory。閱讀 advisory 說明的 root cause，嘗試用 Miri 寫一個最小復現（minimal reproduction）。注意：data race 在 Miri 底下要用 `MIRIFLAGS=-Zmiri-disable-isolation` 或多 thread 才能觸發，你可能需要查 Miri 的 thread 支援文件（`-Zmiri-preemption-rate`）。把你寫的 minimal reproduction 貼出來，說明為什麼 Miri 能或不能捕到它。

**挑戰 4：手動 demangle 一條 v0 symbol**

從 `nm target_demo | grep " _R" | head -1` 拿到第一條 v0 symbol，不用 `rustfilt`，根據 RFC 2603 的規範手動解讀它的結構。v0 的基本格式是：

```
_R <path>
path = C<crate_hash> | N <ns> <path> <ident> | I <path> {<generic_arg>} E
```

你不需要解讀完整 symbol，目標是找到 `<crate_hash>` 部分和最後一個 `<ident>`，對應到 rustfilt 的輸出。說明為什麼 v0 比舊版 legacy mangling 更適合 Rust（legacy mangling 沒辦法處理 generic 參數的 crate hash 區分）。

**挑戰 5：用 `cargo-audit` 掃整個依賴樹**

在任意一個有 `Cargo.lock` 的 Rust 專案裡跑：

```
cargo install cargo-audit
cargo audit
```

找到輸出裡標記為 `unsound` 的 advisory（如果有的話）。選一個讀它的說明，回答：這個 unsound 是 lifetime 謊稱、aliasing 違反、還是 uninitialized memory？它的修正版用了什麼技術消掉 `unsafe`，或把 `unsafe` 侷限到確實 sound 的邊界內？

---

## 自我檢核

- [ ] 我能從 `strings` / `readelf -p .comment` 的輸出，不假設的情況下判斷一個 binary 是不是用 Rust 編的，並讀出 rustc 版本。
- [ ] 我能解釋 Rust v0 name mangling 的 `_R` 前綴是什麼，以及為什麼 Rust 不用 C++ 的 `_Z` Itanium ABI（提示：Rust 有 generic crate hash，Itanium ABI 設計沒有考慮到這個）。
- [ ] 我能用一句話解釋 niche optimization，以及「`Option<String>` 和 `String` 佔用相同大小」的 machine-level 機制。
- [ ] 面對一個反組譯出來的 `cmpq $0xffffffffffffffff` + `je`，我能說出它對應的 Rust 原始碼結構（`match option_value { None => ..., Some(v) => ... }`）以及為什麼選這個指令組合而不是其他形式的 None check。
- [ ] 我能說清楚 `transmute::<&str, &'static str>` 在 bit level 做了什麼、在 borrow checker 層面做了什麼、以及為什麼這兩件事分開來都沒問題、合起來就 unsound。
- [ ] 我能說出 Miri 錯誤指向 use site 而非 transmute 那行的原因——Miri 是在「dangling reference 被使用」時才偵測到非法存取，不是在 transmute 時。
- [ ] 我能把修正前後的 `get_last_word` 拿出來對比，說出為什麼修正版回傳 `Option<&'a str>` 而非 `&'a str`，以及這個差異對 caller 的影響。
- [ ] 我能解釋「Miri 抓到的 UB」和「AddressSanitizer 抓到的 bug」的工具層面差異：Miri 在抽象機器層面執行（不需要真的存取非法地址，違反 Rust 的 validity invariant 就報錯），ASan 在 OS 層面監控 memory 存取（需要真的碰到 poisoned byte 才報錯）。

---

這個練習把逆向與 audit 兩條線接在一起：逆向讓你知道 Rust binary 長什麼樣、niche optimization 在組語層面的特徵；audit + Miri 讓你知道 unsafe 的 soundness 邊界在哪、工具怎麼幫你量。兩件事的底層都是同一個問題：**Rust 的型別系統聲稱了什麼，machine 實際做了什麼，兩者一致才是正確的。**

→ [Ch 37 Rust-for-Linux 概覽](./37-rust-for-linux-overview.md)
