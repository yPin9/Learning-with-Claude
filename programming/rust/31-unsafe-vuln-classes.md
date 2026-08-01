# Ch 31 — unsafe 漏洞類與 RUSTSEC 案例

> **目標**：把「safe/unsafe 邊界」這條線挖到底。學完你能：（1）分辨「unsafe 內部的 bug」與「unsound 的安全抽象」——後者才是真正危險的，因為它讓**完全 safe 的呼叫者**觸發 UB；（2）認得五類最常見的 unsound pattern（誤用 `set_len`、`get_unchecked` 越界、生命週期 transmute 造 UAF、`Send`/`Sync` 誤標造 data race、裸指標 aliasing 違規），每一類都能在本機用 Miri 抓出來；（3）判斷一段 unsafe 到底 sound 不 sound；（4）看懂 RUSTSEC advisory，並認得幾個真實案例的編號與成因。

> **環境**：Rust `rustc 1.97.1`（stable）與 nightly `rustc 1.99.0-nightly (ad3d0bc14 2026-07-31)`，x86-64 Linux（WSL2）。Miri 版本 `miri 0.1.0 (ad3d0bc141 2026-07-31)`，透過 `cargo +nightly miri run` / `cargo +nightly miri test` 執行。所有 native 執行輸出與 Miri 報告都是本機真跑，非推測。引用的 RUSTSEC 編號都在 rustsec.org 查證過，成因描述以 advisory 正文為準。

## 為什麼需要這個？

[Ch 30](./30-security-boundary.md) 畫了信任邊界圖，指出「safe/unsafe 邊界」是四條線之一。現在把鏡頭推到這條線上，回答一個 audit 時的核心問題：**我看到一段 `unsafe`，它危險嗎？**

一個天真的答案是「有 `unsafe` 就危險」。但這會讓你把 `Vec`、`Arc`、`HashMap`——標準庫裡每一個高效資料結構——都標成危險，因為它們內部全是 unsafe。這個答案沒有鑑別力。

正確的問題不是「有沒有 unsafe」，而是「這段 unsafe **sound 不 sound**」。sound 的 unsafe（像 `Vec` 內部那些）維持了它承諾的 invariant，safe 呼叫者無論怎麼用都不會觸發 UB——它把危險**關在盒子裡**。unsound 的 unsafe 則相反：它包了一層 safe 的 API，但盒子有破洞，safe 呼叫者**正常使用**就能漏出 UB。

這個區別是整個 Rust 安全模型的樞紐，也是 audit 一個 crate 時你真正要判斷的東西。這一章用五個能在你機器上重現的例子，把「unsound」這個抽象概念變成你手上摸得到的 Miri 報告；再帶你看真實世界裡這幾類 bug 對應的 RUSTSEC 編號——它們不是教科書假設，是流行 crate 出過的真事故。

## 先建立直覺

先把兩個容易混的東西分開。

```
   ┌──────────────────────────────────────────────────────────────┐
   │  情況 A：unsafe 內部有 bug，但 API 是「unsafe fn」            │
   │                                                               │
   │    pub unsafe fn foo(p: *const T) { ... }   ← 呼叫者要寫       │
   │                                              unsafe 才能叫它  │
   │    → 責任在呼叫者。他簽了「我保證滿足前提」的合約。            │
   │       這不叫 unsound——這叫「把責任正確地往上推」。            │
   └──────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │  情況 B：unsafe 內部有 bug，但 API 是「safe fn」  ★危險★      │
   │                                                               │
   │    pub fn bar(v: &[T], i: usize) -> T {      ← 呼叫者不用寫    │
   │        unsafe { *v.get_unchecked(i) }         unsafe 就能叫   │
   │    }                                                          │
   │    → 責任本該在 bar，但 bar 沒守住。呼叫者「完全沒做錯事」，  │
   │       卻能觸發 UB。這就是 UNSOUND。                           │
   └──────────────────────────────────────────────────────────────┘
```

一句話定義：**unsound = 存在一段 100% safe 的程式碼，正常使用某個安全 API，就能觸發 UB。** 關鍵詞是「safe 程式碼」「正常使用」「觸發 UB」——不需要惡意，不需要 unsafe，照著文件用就爆。

為什麼這是最危險的一類？因為它**背叛了 Rust 的核心承諾**。Rust 對使用者說的是：「你只要待在 safe 的世界，就不會有記憶體 UB。」一個 unsound 的 crate 讓這句承諾變成謊言——使用者相信自己安全，實際上一直站在 UB 上。而且它藏得很好：unsound 的 API 簽章看起來跟任何安全 API 一模一樣（沒有 `unsafe` 關鍵字提醒你），native 跑起來往往也「看起來正常」。

> 如果你對「invariant」「safe/unsafe 責任分界」還沒把握，先回看 [Ch 17](./17-unsafe-basics.md) 的「safety contract」與 [Ch 21](./21-unsafe-abstractions.md) 手刻安全 Vec 那節——那章示範的是「怎麼把 unsafe 包成 sound 的抽象」，本章示範的是「包壞了會怎樣」。

## soundness：怎麼判斷一段 unsafe 是否 sound

在看壞例子之前，先立好判準。判斷一個安全 API（裡面有 unsafe）是否 sound，問一個問題：

> **有沒有任何一種 safe 的呼叫方式，能讓這個 API 觸發 UB？**

- 如果**沒有**（無論呼叫者怎麼用都不會 UB）→ sound。危險被關在盒子裡。
- 如果**有**（存在某種 safe 呼叫觸發 UB）→ unsound。盒子有洞。

具體 audit 時，這個問題拆成幾個檢查點：

1. **這段 unsafe 依賴什麼前提（precondition）？** 例如「`i` 必須小於 `len`」「這塊記憶體必須已初始化」「這個型別不能跨執行緒共享」。
2. **這些前提，是被 API 強制保證的，還是被「假設」的？** sound 的做法是用 `assert!`、型別、或 API 結構**強制**前提成立；unsound 的做法是**假設**呼叫者會滿足前提，但 safe 呼叫者根本不知道有這個前提。
3. **有沒有 `// SAFETY:` 註解說明前提為何成立？** 沒有註解不代表 unsound，但它是個強烈的 code smell——作者可能根本沒想清楚前提。

帶著這三個檢查點，來看五類 unsound。每一類都給你：unsound 的 code、native 跑起來「看似正常」的輸出、Miri 抓出 UB 的真實報告。前四類 C 也有（你的既有直覺認得出），第五類 Rust 特有、最難察覺。

## 類一：`Vec::set_len` 誤用 → 讀未初始化記憶體

`Vec::with_capacity(n)` 配了 `n` 個元素的空間，但 `len` 還是 0——那塊記憶體**未初始化**。`set_len(n)` 是個 unsafe 方法，它把 `len` 直接改成 `n`，**它的 safety contract 要求**：那 `n` 個元素必須已經被初始化。誤用它——只 `set_len` 不初始化——就讓 `Vec` 謊稱自己有 `n` 個合法元素，實際是垃圾。

```rust
/// 一個「safe」的函式簽章，內部卻 unsound。
/// 呼叫者什麼都沒做錯；光是這個 API 自己就會觸發 UB。
pub fn make_buffer(n: usize) -> Vec<u8> {
    let mut v: Vec<u8> = Vec::with_capacity(n);
    unsafe {
        // BUG：只設長度、沒初始化元素。
        // 現在 v 宣稱持有 n 個初始化好的 u8，實際上是未初始化記憶體。
        v.set_len(n);
    }
    v
}

fn main() {
    let buf = make_buffer(8);
    // 完全 safe 的程式碼，讀一個「應該初始化好」的 Vec → 讀到未初始化 bytes。
    let mut sum: u64 = 0;
    for b in &buf {
        sum += *b as u64;
    }
    println!("sum of uninit buffer = {}", sum);
}
```

native 跑（debug）：

```
sum of uninit buffer = 0
```

（本機真跑。）看到沒——它印出 `0`，一切「看起來正常」。這正是 unsound 最陰險的地方：它不 crash，它給你一個看似合理的值。剛好這次那塊記憶體是 0，換個環境、換個配置器狀態，就是別的垃圾值。你在 CI 測一萬次可能都是 0，上生產環境某天就不是。

現在拿 Miri 跑同一份 code：

```
error: Undefined Behavior: reading memory at alloc271[0x0..0x1], but memory is uninitialized at [0x0..0x1], and this operation requires initialized memory
  --> src/main.rs:18:16
   |
18 |         sum += *b as u64;
   |                ^^ Undefined Behavior occurred here
   |
   = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior

Uninitialized memory occurred at alloc271[0x0..0x1], in this allocation:
alloc271 (Rust heap, size: 8, align: 1) {
    __ __ __ __ __ __ __ __                         │ ░░░░░░░░
}

error: aborting due to 1 previous error
```

（本機真跑，`cargo +nightly miri run`。）Miri 不看「值是不是 0」，它看「這塊記憶體有沒有被寫過」。那 8 個 `__` 就是「這 8 個 byte 全部未初始化」的意思。Miri 精確指到 `sum += *b` 這一行、這一塊 heap allocation。這就是把「看不見的 UB」變成「一行紅字」。

**正解**：要嘛用 `vec![0u8; n]`（初始化為 0），要嘛用 `MaybeUninit`（[Ch 18](./18-unsafe-advanced.md)）明確標記「這塊還沒初始化」，只在真的寫入後才 `assume_init`。

## 類二：`get_unchecked` 越界 → OOB 讀

`slice::get_unchecked(i)` 是 `[i]` 的無檢查版：它跳過 bounds check（快，但危險）。它的 safety contract 要求 `i < len`。把它包進一個**不檢查**的安全 API，就 unsound：

```rust
pub fn nth(v: &[u32], i: usize) -> u32 {
    // BUG：不做邊界檢查，盲目相信呼叫者。get_unchecked 是 unsafe：
    // 它的 safety contract 要求 i < v.len()。這個 wrapper 沒強制，
    // 所以它是一個 unsound 的安全 API。
    unsafe { *v.get_unchecked(i) }
}

fn main() {
    let v = vec![10u32, 20, 30];
    println!("{}", nth(&v, 5)); // 越界
}
```

native 跑（debug）：

```
thread 'main' (281176) panicked at src/main.rs:5:17:
unsafe precondition(s) violated: slice::get_unchecked requires that the index is within the slice

This indicates a bug in the program. This Undefined Behavior check is optional, and cannot be relied on for safety.
```

（本機真跑。）這裡有個**版本特定**的重要細節，要標清楚：現代 Rust（本機 1.97.1）的標準庫在 **debug 建置**下，替 `get_unchecked` 這類函式加了 **debug-mode precondition check**——它會在 debug 幫你 panic 提示。但注意訊息最後那句：「This Undefined Behavior check is optional, and cannot be relied on for safety.」——這個檢查**只在 debug 有、release 沒有**，你不能靠它。release 建置下這段就是真正的 OOB 讀（UB），沒有任何提示。

拿 Miri 跑（Miri 不受 debug/release 影響，它直接檢查 UB）：

```
error: Undefined Behavior: `assume` called with `false`
 --> src/main.rs:5:15
  |
5 |     unsafe { *v.get_unchecked(i) }
  |               ^^^^^^^^^^^^^^^^^^ Undefined Behavior occurred here
  |
  = note: stack backtrace:
          0: nth
              at src/main.rs:5:15: 5:33
          1: main
              at src/main.rs:10:20: 10:30
```

（本機真跑。）Miri 把 `get_unchecked` 內部那個「假設 `i < len`」的 `assume` 標為 `false`，直接判 UB，還給你完整的 backtrace（`nth` ← `main`）。

**正解**：這個 API 根本不該用 `get_unchecked`——用 `v[i]`（自帶 bounds check，越界 panic 不是 UB）或 `v.get(i)`（回傳 `Option`）。除非你**在函式內部已經證明** `i < len`（例如前面有 `assert!` 或 `i` 來自受控的迴圈），否則用 `get_unchecked` 就是拿 soundness 換一點點速度，通常不值得。

## 類三：生命週期 transmute → use-after-free

這一類最像你熟悉的 pwn。`mem::transmute` 能把任何型別重新解釋成另一個（[Ch 18](./18-unsafe-advanced.md)），包括**延長生命週期**——把一個短命的 `&'a T` 硬轉成 `&'static T`。編譯器信了這個謊，UAF 就成立：

```rust
use std::mem;

/// UNSOUND：把一個短命借用洗成 &'static。
/// 簽章看起來人畜無害，內部把生命週期 transmute 掉了。
fn make_static<T>(r: &T) -> &'static T {
    unsafe { mem::transmute::<&T, &'static T>(r) }
}

fn dangling() -> &'static String {
    let local = String::from("temporary");
    let leaked = make_static(&local);
    leaked
    // local 在這裡被 drop；leaked 現在指向已釋放的記憶體
}

fn main() {
    let r = dangling();
    println!("{}", r); // use-after-free
}
```

native 跑（debug）：

```
       
```

（本機真跑——印出一行空白/垃圾，沒 crash。）跟 C 的 UAF 一模一樣：`local` 被 drop 了，但 `leaked` 還指著那塊記憶體，剛好還沒被覆蓋，於是印出點東西也不 crash。這種「跑起來沒事」正是 UAF 最難抓的原因。

Miri：

```
error: Undefined Behavior: constructing invalid value of type &std::string::String: encountered a dangling reference (use-after-free)
  --> src/main.rs:17:13
   |
17 |     let r = dangling();
   |             ^^^^^^^^^^ Undefined Behavior occurred here
   |
   = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior

error: aborting due to 1 previous error
```

（本機真跑。）Miri 直接說 `dangling reference (use-after-free)`——它追蹤每塊記憶體的存活狀態，`local` 一被 drop，指向它的 reference 就標記為 dangling，構造出這個 reference 的那一刻就是 UB。這是 C 的 valgrind/ASan 在做的事，但 Miri 做在語言語意層級、精確到「構造非法值」。

**正解**：不要 transmute 生命週期。如果你真的需要延長生命週期，重新思考所有權——回傳 owned 的 `String`、用 `Arc`、或用 arena。transmute lifetime 幾乎總是設計錯誤的訊號。

## 類四：`Send`/`Sync` 誤標 → data race

這一類是並發世界的 unsound，也是最難靠肉眼抓的。`Send`（能 move 到別的執行緒）和 `Sync`（能被多執行緒共享 `&`）是 **auto trait**：編譯器自動幫「所有欄位都 Send/Sync」的型別實作它們（[Ch 23](./23-threads-send-sync.md)）。但你可以用 `unsafe impl` **手動宣稱**一個型別是 Send/Sync——如果宣稱錯了，你就繞過了 Rust 防 data race 的整個機制。

```rust
use std::cell::UnsafeCell;
use std::sync::Arc;
use std::thread;

/// 一個「不」執行緒安全的計數器（UnsafeCell、沒有 atomic/鎖），
/// 卻被我們錯誤地標為 Send + Sync。safe 呼叫者現在能跨執行緒共享它、撞出 data race。
struct RacyCounter {
    inner: UnsafeCell<u64>,
}
unsafe impl Send for RacyCounter {}
unsafe impl Sync for RacyCounter {}

impl RacyCounter {
    fn new() -> Self { RacyCounter { inner: UnsafeCell::new(0) } }
    fn bump(&self) {
        unsafe { *self.inner.get() += 1; } // 無同步的 read-modify-write
    }
    fn get(&self) -> u64 { unsafe { *self.inner.get() } }
}

fn main() {
    let c = Arc::new(RacyCounter::new());
    let mut hs = Vec::new();
    for _ in 0..2 {
        let c = Arc::clone(&c);
        hs.push(thread::spawn(move || {
            for _ in 0..1000 { c.bump(); }
        }));
    }
    for h in hs { h.join().unwrap(); }
    println!("final = {}", c.get());
}
```

native 跑：

```
final = 2000
```

（本機真跑。）兩條執行緒各加 1000 次，印出 `final = 2000`——**看起來完全正確**！這就是 data race 的可怕之處：這次剛好沒撞上，結果對了。跑一百次可能九十次對，剩下十次給你 1998、1999 這種少算的值——而且是不可重現的 heisenbug。native 執行沒有任何工具告訴你「這裡有 race」。

Miri 有一個 race detector，配一個固定的 scheduler seed 強制某種交錯：

```
error: Undefined Behavior: Data race detected between (1) non-atomic write on thread `unnamed-1` and (2) non-atomic read on thread `unnamed-2` at alloc255+0x10
  --> src/main.rs:17:18
   |
17 |         unsafe { *self.inner.get() += 1; } // 無同步的 read-modify-write
   |                  ^^^^^^^^^^^^^^^^^^^^^^ (2) just happened here
   |
help: and (1) occurred earlier here
  --> src/main.rs:17:18
   |
17 |         unsafe { *self.inner.get() += 1; } // 無同步的 read-modify-write
   |                  ^^^^^^^^^^^^^^^^^^^^^^
   = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior
```

（本機真跑，指令 `MIRIFLAGS="-Zmiri-seed=2" cargo +nightly miri run`。）Miri 精確指出：thread `unnamed-1` 的非原子**寫**和 thread `unnamed-2` 的非原子**讀**，在同一個位址 `alloc255+0x10` 上沒有同步——這就是 data race 的定義。它把一個 native 下「跑一百次才撞一次」的 heisenbug，變成確定性的一行紅字。

> 認識論誠實：Miri 的 race detector 探索的是**某些**執行緒交錯，不是全部。它抓到 race 一定是真 race；但它「這次沒抓到」不等於「沒有 race」——換 seed（`-Zmiri-seed`）可能就抓到了。這跟 [Ch 20](./20-memory-model-ub.md) 說的「Miri 只檢查它實際走過的路徑」是同一個限制。實務上會掃多個 seed。

**正解**：這個計數器要嘛用 `AtomicU64`（[Ch 25](./25-atomics-lockfree.md)），要嘛用 `Mutex<u64>`。`unsafe impl Send/Sync` 只在你**能證明**內部同步正確時才寫，而且一定要配 `// SAFETY:` 說明為什麼安全。

## 類五：裸指標 aliasing 違規 —— Rust 特有、最難察覺

前四類（uninit、OOB、UAF、race）C 也有，你憑既有直覺認得出。第五類是 **C 完全沒有**、也最容易在 audit 時漏判的一類：違反 Rust 的 **aliasing model**。規則（[Ch 20](./20-memory-model-ub.md) 講過）是——`&mut T` 是「此刻對這塊記憶體的唯一可寫存取」，這條在 `unsafe` 裡**照樣有效**。你在 unsafe 裡「有一個 `&mut` 又同時留一個從它衍生的裸指標去寫」，即使沒越界、沒 UAF、輸出完全正確，也已經是 UB：

```rust
/// UNSOUND：安全簽章，但同時持有 &mut 與從它衍生的裸指標，
/// 兩者交錯寫同一塊 —— 違反 aliasing model（&mut 唯一性）。
pub fn tricky(x: &mut u32) -> u32 {
    let p = x as *mut u32;   // 從 &mut 衍生一個裸指標
    *x = 10;                 // 透過 &mut 寫
    unsafe { *p = 20; }      // 又透過裸指標寫同一塊 —— aliasing 違規
    *x                       // 透過 &mut 讀
}

fn main() {
    let mut n = 0u32;
    println!("{}", tricky(&mut n));
}
```

native 跑印出 `20`——**完全正確**，沒有任何徵兆。Miri（預設 Stacked Borrows）：

```
error: Undefined Behavior: attempting a write access using <379> at alloc168[0x0], but that tag does not exist in the borrow stack for this location
 --> src/main.rs:7:14
  |
7 |     unsafe { *p = 20; }      // 又透過裸指標寫同一塊 —— aliasing 違規
  |              ^^^^^^^ this error occurs as part of an access at alloc168[0x0..0x4]
  |
help: <379> was later invalidated at offsets [0x0..0x4] by a write access
```

（本機真跑。）Miri 追蹤每個指標的「借用標籤（tag）」：透過 `&mut x`（`*x = 10`）存取時，那個從它衍生的裸指標標籤 `<379>` 被作廢；之後再用 `<379>` 寫（`*p = 20`）就是用一個作廢的標籤 → UB。這一類 audit 時的問法是：**這段 unsafe 有沒有在某個 `&mut` 仍存活時，透過別的指標動同一塊記憶體？** 這比前四類細，也最需要 Miri——肉眼幾乎看不出來。（認識論誠實：aliasing model 本身仍在演進、Stacked/Tree Borrows 尚未定案，Miri 報告裡那句 "still experimental" 就是這個意思。）

## RUSTSEC：真實世界的 advisory 資料庫

上面四類不是教科書假設。每一類在流行 crate 裡都出過真事故，記錄在 **RUSTSEC advisory database**（rustsec.org，由 Rust Secure Code Working Group 維護，資料在 GitHub 的 `rustsec/advisory-db`）。這是 Rust 生態的 CVE 對應物，也是下一章 `cargo audit` 比對的資料來源。

**編號格式**：`RUSTSEC-<年>-<四位流水號>`，例如 `RUSTSEC-2021-0130`。每筆 advisory 有：受影響 crate、受影響版本範圍、patched 版本、分類（`memory-corruption`、`unsound`、`denial-of-service`、`unmaintained`…）、以及成因描述。有些同時掛 CVE 編號，但 RUSTSEC 涵蓋更廣——包括很多「不到 CVE 等級但確實 unsound」的問題。

以下是本章四類 unsound 對應的**真實 advisory**（成因描述以 advisory 正文為準，我在 rustsec.org 逐筆查證過）：

| RUSTSEC 編號 | crate | 分類 | 成因（對應本章哪一類） |
|---|---|---|---|
| [RUSTSEC-2021-0130](https://rustsec.org/advisories/RUSTSEC-2021-0130.html) | `lru` (<0.7.1) | memory-corruption | iterator 裡的 UAF：`pop()` 移除並釋放了值，但仍能透過 iterator 拿到已 drop 值的 reference。**類三（UAF）** |
| [RUSTSEC-2021-0093](https://rustsec.org/advisories/RUSTSEC-2021-0093.html) | `crossbeam-deque` (<0.7.4, 0.8.0) | memory-corruption | worker queue 的 race condition：某些 task 被 pop 多次、某些永遠不處理，對 heap-allocated task 造成 double-free。**類四（並發 unsound）** |
| [RUSTSEC-2022-0019](https://rustsec.org/advisories/RUSTSEC-2022-0019.html) | `crossbeam-channel` (<0.4.3) | unsound | 用 `mem::zeroed()` 初始化使用者提供的型別；對「必須非 null」的 reference 型別，zeroed 出來是非法值。**類一的近親（產生非法值）** |
| [RUSTSEC-2024-0005](https://rustsec.org/advisories/RUSTSEC-2024-0005.html) | `threadalone` (<0.2.1) | unsound (INFO) | 在 stderr 寫入失敗時，於**錯的執行緒**跑 non-Send 型別的 `Drop`，可能 UB。**類四（Send 誤標的近親）** |

注意兩件事：

1. **這些是流行、被廣泛依賴的 crate**（`crossbeam` 系列是 Rust 並發生態的地基，被 `rayon`、`tokio` 周邊大量間接依賴）。unsound 不是「爛 crate 才有」的問題——寫並發資料結構的 unsafe 極難，連頂尖團隊都會出。
2. **分類欄位很重要**。`memory-corruption` 是最嚴重的（能被利用）；`unsound` 表示存在觸發 UB 的路徑但不一定 practical exploit；`unmaintained`（[Ch 30](./30-security-boundary.md) 提過的 `dotenv` = RUSTSEC-2021-0141）不是漏洞本身，是「沒人維護、未來風險高」的警告。下一章 `cargo audit` 會按這些分類報給你。

## 對照 C：audit 面積的差異

把這一章的四類 unsound 放回 [Ch 30](./30-security-boundary.md) 的 audit 面積論點：

```
   C 的世界                          Rust 的世界
   ─────────                         ──────────
   每一行都可能有這四類 bug          這四類 bug 只可能在 unsafe 區塊裡
   （UAF/OOB/uninit/race）           發生（safe Rust 由型別系統擋掉）

   audit 記憶體安全                   audit 記憶體安全
   = 讀整個 codebase                 = grep "unsafe" + 讀那幾百行
                                       + 掃依賴樹的 RUSTSEC（Ch 32）
```

C 的每一個指標解引用、每一個陣列存取、每一個 `malloc`/`free`、每一個共享變數，都是這四類 bug 的潛在現場，你 audit 時無處不看。Rust 把這四類 bug 的**發生地點**壓縮到 `unsafe` 關鍵字圈起來的區塊——你 `grep unsafe` 就得到了完整的「需要人工判 soundness 的清單」，再對每一段套用本章的 soundness 判準。這不是「Rust 沒有這些 bug」，是「這些 bug 只可能在一小塊有標記的地方，且有 Miri 這種工具能幫你抓」。C 沒有這兩個性質。

## 踩雷集錦

1. **「有 unsafe 就是 unsound / 危險」**：錯。`Vec`、`Arc`、標準庫全靠 unsafe 實作，它們 sound。sound 的 unsafe 把危險關在盒子裡，safe 呼叫者摸不到。要判斷的不是「有沒有 unsafe」，是「這段 unsafe 有沒有守住它承諾的 invariant」。

2. **「native 跑起來對，就沒 UB」**：本章五個例子全都 native「看起來正常」（印出 0、印出 2000、印出空白、印出 20），Miri 才抓到 UB。UB 的定義是「語意上非法」，不是「這次跑會不會 crash」。native 正常輸出**不是** soundness 的證據。這是資安人最該內化的一條。

3. **「debug 有 precondition check 幫我擋 `get_unchecked` 越界」**：那個 check **只在 debug 有、release 沒有**，訊息自己都寫「cannot be relied on for safety」。它是 debug 輔助，不是安全保證。release build 下越界就是純 UB。

4. **`unsafe impl Send/Sync` 隨手寫**：這是繞過 Rust 整個 data race 防護的後門。只有在你**能證明**內部同步正確（用 atomic、鎖，或邏輯上不可能並發存取）時才寫，而且務必附 `// SAFETY:`。誤標的後果是 data race——native 下九成九跑對，剩下那次給你不可重現的 heisenbug。

5. **「Miri 沒報 UB，就代表 sound」**：Miri 只檢查它**實際執行到的路徑與交錯**。它是 UB 偵測器，不是 soundness 證明器。沒被觸發的 unsafe 分支、沒被探索到的執行緒交錯，它看不到。要提高覆蓋：讓測試涵蓋 unsafe 的所有路徑、並發測試掃多個 `-Zmiri-seed`。「Miri 綠」是好訊號，不是 soundness 證書。

## 進階：再往深一層

**「unsound 但無法 exploit」算不算漏洞？** RUSTSEC 收錄很多分類為 `unsound` 而非 `memory-corruption` 的 advisory——它們存在觸發 UB 的路徑，但要湊出「攻擊者可控地觸發、並轉成 RCE」可能很難甚至不可能。Rust 社群的立場相對嚴格：**unsound 本身就是 bug，即使當下沒有 practical exploit。** 理由是 UB 給編譯器的優化自由度是全域的——今天無害的 UB，明天換個編譯器版本、換個優化 pass，可能就變成可利用的記憶體損毀。做為 auditor，你要記錄 soundness 問題，但也要能區分「理論 unsound」和「可利用的 memory corruption」的緊急程度差異——這兩者在 RUSTSEC 的分類欄位上是分開的。

**同一段 unsafe，換 aliasing model 判斷可能不同——類五那個例子就是活教材。** Miri 預設用 **Stacked Borrows** 判類五的 `tricky` 是 UB。但把它改用較新、較寬鬆的 **Tree Borrows** 跑（`MIRIFLAGS="-Zmiri-tree-borrows" cargo +nightly miri run`），同一份 code **不再報 UB**，乾淨印出 `20`（本機真跑，兩個結果我都跑過）。兩個模型對這段程式的判斷相反。這對 auditor 是個要記住的坑：**「Miri 報 UB」和「這一定是所有人都同意的 UB」之間有一道縫**——因為別名模型本身尚未定案（Miri 報告裡的 "still experimental" 就是在講這件事）。實務建議：兩個模型都跑；只有在**兩個模型都判合法**時，才對一段用裸指標的 unsafe 的 aliasing 安全性比較有把握。這也是為什麼標準庫的 unsafe 抽象會刻意寫得對兩個模型都成立。前四類（uninit/OOB/UAF/race）沒有這個模型分歧問題，判定是明確的；只有第五類 aliasing 卡在這道演進中的縫上。

## 動手練習

1. 把「類一」的 `make_buffer` 改成 sound：用 `MaybeUninit<u8>` 配置，寫入每個 byte 後才 `assume_init`。跑 `cargo +nightly miri run` 確認 Miri 不再報 UB。體會「維持 invariant」具體是什麼動作。

2. 把「類四」的 `RacyCounter` 的 `UnsafeCell<u64>` 換成 `AtomicU64`、`bump` 用 `fetch_add(1, Ordering::Relaxed)`，移除那兩行 `unsafe impl`。跑 Miri（多試幾個 seed）確認 race 消失。對照：這時 `Send`/`Sync` 是編譯器**自動**推出來的，你不用手標——這就是「讓型別系統幫你證明」。

3. 到 rustsec.org 隨便挑一筆分類為 `unsound` 的 advisory，讀它的成因描述，試著把它歸到本章四類（或 aliasing）中的哪一類。有些會是你沒見過的第五、第六類——記下來。

## 本章重點整理

- **unsound = 存在一段 safe 程式碼，正常使用某安全 API 就能觸發 UB**。這是 safe/unsafe 邊界上最危險的一類，因為它讓「待在 safe 世界就沒有記憶體 UB」這句承諾變成謊言，且藏得很好（簽章看似安全、native 看似正常）。
- 判 soundness 的核心問題：**有沒有任何 safe 呼叫方式能觸發 UB？** 檢查點：unsafe 依賴什麼前提、前提是被強制還是被假設、有沒有 `// SAFETY:` 說明。
- 五類最常見 unsound：`set_len` 誤用（讀 uninit）、`get_unchecked` 越界（OOB）、生命週期 transmute（UAF）、`Send`/`Sync` 誤標（data race）、裸指標 aliasing 違規（Rust 特有、最難察覺）。每一類 native 都「看似正常」，Miri 都能抓成一行紅字。
- 它們對應真實 RUSTSEC：`lru` RUSTSEC-2021-0130（UAF）、`crossbeam-deque` RUSTSEC-2021-0093（race→double-free）、`crossbeam-channel` RUSTSEC-2022-0019（`mem::zeroed` 非法值）、`threadalone` RUSTSEC-2024-0005（non-Send Drop 跨執行緒）。unsound 不是爛 crate 專利，頂尖團隊寫並發 unsafe 也會出。

## 自我檢核

- [ ] 不看筆記，能不能用一句話定義 unsound，並解釋為什麼「unsafe fn」不算 unsound、但「內部有 bug 的 safe fn」算？
- [ ] 給你一段有 `unsafe` 的安全 API，能不能套用那三個檢查點判斷它 sound 不 sound？
- [ ] 為什麼「native 跑出正確結果」不能證明一段 unsafe 是 sound？本章哪個例子最能說明這點？
- [ ] `unsafe impl Send for T {}` 這行在什麼條件下才該寫？誤標的後果是什麼、為什麼難抓？
- [ ] 能不能說出 Miri「抓到 UB」和「沒抓到 UB」各代表什麼、後者為什麼不等於 sound？

## 延伸閱讀

### 官方文件

- **[The Rustonomicon —〈How Safe and Unsafe Interact〉/〈Working with Unsafe〉](https://doc.rust-lang.org/nomicon/working-with-unsafe.html)** — Rust 官方
  - **讀哪裡**：「How Safe and Unsafe Interact」講清楚「safe 抽象必須對**所有** safe 輸入都 sound」這條規則——本章 soundness 判準的權威來源。
  - **能學到什麼**：為什麼「unsafe 的正確性責任會蔓延到包住它的整個 module」，這解釋了為什麼一個 unsound 的私有 unsafe 能污染整個 crate 的安全宣稱。
  - **前提**：讀過 [Ch 17](./17-unsafe-basics.md)、[Ch 21](./21-unsafe-abstractions.md)。

- **[RustSec Advisory Database](https://rustsec.org/)** 與 **[分類 'memory-corruption'](https://rustsec.org/categories/memory-corruption.html)** / **[關鍵字 'unsound'](https://rustsec.org/keywords/unsound.html)**
  - **讀哪裡**：先看首頁理解 advisory 結構，再進「memory-corruption」分類頁掃幾筆真實案例。
  - **能學到什麼**：真實 crate 出過哪些 soundness/記憶體損毀問題、成因長什麼樣。這是 Ch 32 `cargo audit` 的資料源，本章表格裡那幾筆都在這裡。
  - **前提**：懂本章的四類 unsound，讀 advisory 時能把成因對號入座。

### 論文 / 研究

- **[Understanding Memory and Thread Safety Practices and Issues in Real-World Rust Programs](https://cseweb.ucsd.edu/~yiying/RustStudy-PLDI20.pdf)** — Qin, Lu, Zhang et al.（PLDI 2020）
  - **核心貢獻**：系統性分析真實 Rust 專案（含 crates.io 上流行 crate）的記憶體與執行緒安全 bug，把 unsound 的 pattern 分類統計——本章四類的實證來源之一。
  - **讀哪裡**：Section 4（memory safety bugs）與 Section 5（thread safety bugs）的分類；它給出「哪類 unsafe 誤用最常見」的真實數據。
  - **和本章的關聯**：本章憑經驗挑的四類，這篇用大樣本驗證了它們確實是最高頻的類型。

### 工具文件

- **[Miri README（rust-lang/miri）](https://github.com/rust-lang/miri)** — 官方
  - **讀哪裡**：「What kinds of UB does Miri detect」清單，以及並發相關的 `-Zmiri-seed`、`-Zmiri-tree-borrows` flag 說明。
  - **能學到什麼**：Miri 具體抓哪些 UB、抓不到哪些——本章「Miri 綠不等於 sound」那條踩雷的依據。
  - **前提**：[Ch 20](./20-memory-model-ub.md) 已介紹 Miri 基本用法，這裡是把 flag 掌握齊全。

下一章我們從「人工判單一段 unsafe」升級到「自動掃整棵依賴樹」——`cargo audit` 比對 RUSTSEC 找已知漏洞、`cargo geiger` 統計 unsafe 用量、`cargo deny` 訂政策，並示範怎麼把這套 audit 流程塞進 CI。

→ [Ch 32 audit unsafe：cargo-geiger/cargo-audit](./32-audit-unsafe.md)
