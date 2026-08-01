# Ch 20 — 記憶體模型與 UB：Stacked/Tree Borrows、Miri

> **目標**：把「Rust 的 UB 到底有哪些」講清楚（懸空/未對齊解引用、data race、違反別名、非法值、越界），對照 C 的 UB 清單看重疊與差異；建立 Rust 的 aliasing model（**Stacked Borrows / Tree Borrows**）——理解 `&mut` 的唯一性為什麼在 `unsafe` 裡也**必須**遵守，為什麼「有 `&mut` 又同時留一個裸指標亂用」即使沒 crash、輸出看起來正常也是 UB；學會用 **Miri**（MIR 解釋器 + UB 偵測器）把這種肉眼看不出的 UB 抓出來，也搞清楚 Miri 抓得到什麼、抓不到什麼；最後補 **provenance（指標來源）** 概念與 `#[repr(Rust)]` 佈局不保證。全程對照 C：很多 UB 重疊，但 Rust 只在 `unsafe` 出現，safe Rust 保證無 UB。

> **環境**：Rust 以 `rustc 1.97.1`（stable，edition 2015）與 nightly `rustc 1.99.0-nightly (ad3d0bc14 2026-07-31)` 在 x86-64 Linux（WSL2）；Miri 版本 `miri 0.1.0 (ad3d0bc141 2026-07-31)`，透過 `cargo +nightly miri run` 執行。所有 Miri 報告、native 執行輸出、編譯錯誤都是本機真跑，非推測。**別名模型（Stacked vs Tree Borrows）仍在演進、尚未定案**，這點本章反覆強調。

## 為什麼需要這個？

[Ch 17](./17-unsafe-basics.md) 教了 unsafe 的線與 soundness，[Ch 18](./18-unsafe-advanced.md) 給了 `transmute`/`MaybeUninit`/裸指標工具，[Ch 19](./19-ffi.md) 讓你跨到 C 那邊。這三章你都看到同一句話反覆出現：「這是 UB，值不可信，Ch 20 用 Miri 抓」。現在來還這筆帳。

你做 C 十年，對 UB 不陌生——但 C 的 UB 有個殘酷的特性：**它常常「跑起來是對的」**。懸空指標解引用可能印出還沒被覆蓋的舊值；data race 可能在你測的一萬次裡剛好都沒撞上；strict-aliasing 違規在 `-O0` 沒事、`-O2` 才爆。你靠 valgrind、ASan、TSan、無數次 code review 去逼近「這段沒 UB」，但沒有一個工具能對「這段 unsafe 是否 sound」給你確定的答案。

Rust 這邊多了兩樣 C 沒有的東西。第一，一個**精確到「哪幾行」的 UB 定位**——UB 只可能在 `unsafe` 裡發生，safe Rust 由型別系統與 borrow checker 保證無 UB。第二，一個**能對執行路徑做窮盡記憶體檢查的解釋器 Miri**——它逐條 MIR 指令跑你的程式，每次記憶體存取都對照一套 UB 規則檢查。這兩樣加起來，讓「audit unsafe」從 C 的靠經驗猜，變成「盯那幾百行 unsafe + 拿 Miri 跑測試」。

但要用好 Miri，你得先懂它在檢查**什麼規則**。其中最反直覺、也最重要的一條，是 C 完全沒有的：**Rust 的 aliasing model**——`&mut T` 在整個語言裡都是「此刻對這塊記憶體的唯一可寫存取」，這條規則在 `unsafe` 裡**照樣有效**。你在 unsafe 裡「有一個 `&mut` 又同時留一個裸指標去寫」，即使程式跑起來一切正常、輸出完全正確，也已經是 UB。這一章的核心就是把這件事講到你信、並且真跑一個給你看。

## 先建立直覺

先看 Rust UB 的全景，以及它和 C 的關係：

```
        ┌────────────────────────────────────────────────┐
        │                  C 的 UB 宇宙                    │
        │   懸空解引用 · 未對齊 · 越界 · data race         │
        │   讀未初始化 · 有號溢位 · strict-aliasing 違規   │
        │   null 解引用 · double free · ...                │
        │                                                  │
        │   特性：整個語言都可能 UB，沒有語法標記            │
        └────────────────────────────────────────────────┘
                          ∩ （大量重疊）
        ┌────────────────────────────────────────────────┐
        │                 Rust 的 UB 宇宙                  │
        │   懸空解引用 · 未對齊 · 越界 · data race         │
        │   讀未初始化 · 非法值(bool=2/未初始化 ref)       │
        │   ★違反 aliasing model（&mut 唯一性）★           │
        │                                                  │
        │   特性：UB 只在 unsafe 出現，safe 側保證無 UB    │
        └────────────────────────────────────────────────┘
```

兩個宇宙大量重疊（懸空、越界、data race、未初始化這些兩邊都是 UB），但有兩個關鍵差異：

1. **範圍不同**：C 的 UB 藏在任何一行；Rust 的 UB **只可能在 `unsafe` 區塊裡**。safe Rust 由 borrow checker + 型別系統擋掉全部記憶體 UB。這是 [Ch 17](./17-unsafe-basics.md) 那條線的直接後果。
2. **多了 aliasing model**：Rust 有一條 C 沒有的 UB——**違反 `&mut` 的唯一性 / `&` 的唯讀性**。這是為了讓編譯器能做 C 因為指標可能別名而做不了的優化（把 `&mut` 背後的值 cache 進暫存器、假設它不會被別人改）。你在 unsafe 裡用裸指標繞過借用檢查，**但 aliasing model 這條規則你還是得遵守**，否則就是 UB。

第二點是本章的靈魂。心智圖像：把每塊記憶體想成一個**借用的堆疊（Stacked Borrows）**或**借用的樹（Tree Borrows）**，每個指標帶一個「標籤（tag）」。當你透過某個標籤存取記憶體，那些「比它晚借出、和它衝突」的標籤會被作廢。之後你再用那個作廢的標籤存取 → UB。裸指標不能豁免這個機制——它從某個 `&`/`&mut` 衍生出來，就綁在那條借用鏈上。

## Rust 的 UB 清單（對照 C）

先把 Rust 官方認定的 UB 列出來（來源：Rust Reference「Behavior considered undefined」，見延伸閱讀），並和 C 對照：

| UB 種類 | Rust | C | 差異 |
|---|---|---|---|
| 解引用懸空指標（use-after-free） | UB | UB | 兩邊都是；Rust 只在 unsafe |
| 解引用未對齊指標 | UB | UB（多數平台） | Rust 對齊要求嚴格，Miri 必抓 |
| 越界讀寫（out-of-bounds） | UB | UB | Rust 綁 provenance，越界算術就 UB |
| data race | UB | UB | Rust safe 側靠 Send/Sync 擋掉 |
| 讀未初始化記憶體 | UB | UB | Rust 用 `MaybeUninit` 明確標記 |
| 產生非法值（`bool`=2、未初始化 `&`、invalid enum discriminant） | UB | 多半不是 | **Rust 特有**：型別的 validity invariant |
| 違反別名（`&mut` 唯一 / `&` 唯讀） | UB | 無此規則（除 `restrict`） | **Rust 特有**：aliasing model |
| 有號整數溢位 | **不是 UB**（wrapping/panic） | UB | **Rust 收緊**：溢位有定義 |
| null 指標解引用 | UB | UB | 同 |

兩個要特別畫線的方向：

- **Rust 多出來的**：「非法值」和「違反別名」。C 沒有「`bool` 只有 0/1 合法，塞 2 進去就 instant UB」這種 validity invariant 的概念，也沒有 `&mut` 唯一性這種 aliasing 規則。這兩個是 Rust 為了型別安全與優化能力自己加的。
- **Rust 拿掉的**：**有號整數溢位在 Rust 不是 UB**。debug 版 panic、release 版 two's complement wrapping（或用 `wrapping_add`/`checked_add` 顯式控制）。C 的 `INT_MAX + 1` 是 UB，Rust 把這條堵死了——這是 Rust 相對 C 在 UB 面積上實打實的縮小。

先真跑一個「非法值」的 UB（C 幾乎不管、Rust 立刻算 UB），把 `2u8` 硬 transmute 成 `bool`：

```rust
fn main() {
    // 把 2u8 的 bit 硬塞成 bool。bool 只有 0/1 合法，2 是非法值 = instant UB
    let b: bool = unsafe { std::mem::transmute(2u8) };
    // 只要「產生」這個非法值就已經 UB，不必等到用它
    println!("b = {}", b);
}
```

native 跑（看起來若無其事）：

```
b = true
```

Miri 跑（`cargo +nightly miri run`，本機真跑）：

```
error: Undefined Behavior: constructing invalid value of type bool: encountered 0x02, but expected a boolean
 --> src/main.rs:3:28
  |
3 |     let b: bool = unsafe { std::mem::transmute(2u8) };
  |                            ^^^^^^^^^^^^^^^^^^^^^^^^ Undefined Behavior occurred here
  |
  = help: this indicates a bug in the program: it performed an invalid operation, and caused Undefined Behavior
```

`constructing invalid value of type bool: encountered 0x02`——關鍵字是 **constructing**：UB 發生在 `transmute` **產生**這個非法 `bool` 的那一刻，不是等到你用它。native 印 `b = true`（`0x02` 非零被當 true）純屬巧合，換個優化等級/編譯器可能把 `if b` 兩個分支都執行或都不執行（因為編譯器假設 `bool` 只有 0/1，可以用它做位元運算優化）。這正是 UB 的本質——不是「一定 crash」，是「編譯器在『你不會做 UB』的假設下自由優化，你一旦做了，行為不受掌控」。

## aliasing model：`&mut` 的唯一性，unsafe 裡也算

現在進入本章最重要、C 老手最容易踩的坑。

Rust 對每個 `&mut T` 有一條硬保證：**在它的存活期內，它是對那塊記憶體的唯一可寫路徑**。編譯器**依賴**這條保證做優化——例如把 `*r` 讀進暫存器後，在兩次讀之間如果只有透過別的路徑寫（別名），編譯器會假設「不可能，因為 `r` 是唯一的」，於是用 cache 的舊值。你若真的透過別名改了記憶體，編譯器的假設就錯了，行為就崩了。

C 沒有這條規則（除非你手動加 `restrict`）。所以你的 C 直覺會覺得「我有個 `int* p` 指向 `x`，再拿另一個 `int* q` 也指向 `x`，兩個交替讀寫，天經地義」。搬到 Rust 的 unsafe 裡——**`&mut` 和從它衍生的裸指標交替用，就是違反 aliasing model = UB**，即使程式跑起來完全正常。

看這支「跑起來正確、實際是 UB」的程式：

```rust
fn main() {
    let mut x = 42i32;
    let r = &mut x;              // 唯一的 &mut，Stacked Borrows 給它一個 tag
    let p = r as *mut i32;       // 從 &mut 借出一個裸指標（衍生於 r 的 tag）
    *r = 10;                     // 透過 r 寫，這一步「使用 r」會使 p 失效（pop 出 stack）
    unsafe { *p = 20; }          // 現在用已失效的 p 寫 = 違反 Stacked Borrows
    println!("x = {}", x);
}
```

native 跑（`cargo run`，**一切正常、輸出完全合理**）：

```
x = 20
```

沒有 crash、沒有 warning、`x` 就是你期望的 20。如果你只看這個輸出，你會拍胸脯說「這段 code 沒問題」。**它有問題**。用 Miri 開 Stacked Borrows（Miri 預設就開）跑：

```
error: Undefined Behavior: attempting a write access using <377> at alloc168[0x0], but that tag does not exist in the borrow stack for this location
 --> src/main.rs:6:14
  |
6 |     unsafe { *p = 20; }          // 現在用已失效的 p 寫 = 違反 Stacked Borrows
  |              ^^^^^^^ this error occurs as part of an access at alloc168[0x0..0x4]
  |
  = help: this indicates a potential bug in the program: it performed an invalid operation, but the Stacked Borrows rules it violated are still experimental
  = help: see https://github.com/rust-lang/unsafe-code-guidelines/blob/master/wip/stacked-borrows.md for further information
help: <377> was created by a SharedReadWrite retag at offsets [0x0..0x4]
 --> src/main.rs:4:13
  |
4 |     let p = r as *mut i32;       // 從 &mut 借出一個裸指標（衍生於 r 的 tag）
  |             ^
help: <377> was later invalidated at offsets [0x0..0x4] by a write access
 --> src/main.rs:5:5
  |
5 |     *r = 10;                     // 透過 r 寫，這一步「使用 r」會使 p 失效（pop 出 stack）
  |     ^^^^^^^

note: some details are omitted, run with `MIRIFLAGS=-Zmiri-backtrace=full` for a verbose backtrace

error: aborting due to 1 previous error
```

讀這份報告——它把整條犯罪鏈標得清清楚楚：

- `attempting a write access using <377> ... but that tag does not exist in the borrow stack`：你正在用標籤 `<377>`（就是裸指標 `p`）寫，但這個標籤已經**不在借用堆疊裡**了（被 pop 掉了）。
- `<377> was created by a ... retag at ... src/main.rs:4`：這個標籤誕生於第 4 行 `let p = r as *mut i32`——`p` 從 `r` 衍生時，Stacked Borrows 幫它 push 一個標籤到堆疊上。
- `<377> was later invalidated at ... by a write access ... src/main.rs:5`：第 5 行 `*r = 10` 透過**父標籤** `r` 寫，這個動作把「比 `r` 晚借出的」`p` 從堆疊 pop 掉（作廢）。
- 於是第 6 行 `*p = 20` 用一個已作廢的標籤 → UB。

用一張圖看這個 borrow stack 的演化：

```
  借用堆疊（針對 x 這塊記憶體）        操作
  ┌──────────────┐
  │ x 的根 tag   │                    let mut x = 42
  └──────────────┘
  ┌──────────────┐
  │ r 的 tag     │  ← 頂                let r = &mut x   （push r）
  │ x 的根 tag   │
  └──────────────┘
  ┌──────────────┐
  │ p 的 tag<377>│  ← 頂                let p = r as *mut  （push p，衍生自 r）
  │ r 的 tag     │
  │ x 的根 tag   │
  └──────────────┘
  ┌──────────────┐
  │ r 的 tag     │  ← 頂                *r = 10  （用 r：把 r 上面的 p pop 掉！）
  │ x 的根 tag   │                      p<377> 已不在堆疊
  └──────────────┘
        ✗ *p = 20  →  用 p<377> 存取，但它已不在堆疊 → UB
```

規則一句話：**透過父指標存取，會作廢所有「比它晚衍生」的子指標**。這就是 `&mut` 唯一性的機制實作——一旦你「用了」`r`，任何從 `r` 分出去、又還想活著用的別名，都被判死。native 之所以印出正確的 20，是因為這個特定的編譯結果剛好沒觸發依賴 aliasing 假設的優化；**但你不能靠這個**——這段 code 是 unsound 的，換版本/換優化就可能變臉。

> 對照你的 C 直覺：這正是 `restrict` 的強制版。C 的 `int* restrict p` 是你**承諾**「這個指標是唯一存取路徑」，違反了是 UB 但沒工具抓。Rust 把「`&mut` = restrict」變成**預設且強制**，還給你 Miri 來抓違反。

## 底層機制：Stacked Borrows vs Tree Borrows

上面用的是 **Stacked Borrows**——一套用「堆疊」建模借用的 aliasing 規則。但這裡必須做**認識論誠實**的標注：**Rust 的 aliasing model 尚未定案。** Stacked Borrows 是 Ralf Jung 等人在 2019 年提出的模型，是目前 Miri 的**預設**；**Tree Borrows** 是 2023 年提出的較新、較寬鬆的替代模型，用「樹」而非「堆疊」建模，目前是實驗性選項（`-Zmiri-tree-borrows`）。哪一個會成為 Rust 最終的官方 aliasing model，**還沒有定論**——所以本章給你的是「當前工具怎麼判」，不是「語言的最終法律條文」。

Miri 報告裡那句 `the Stacked Borrows rules it violated are still experimental`（上面那份報告的第二行 help）就是官方在對你講這件事：這是實驗性規則。

兩個模型的差別，最能用同一支程式跑出來看。把上面**完全一樣**的 `sbtest` 程式改用 Tree Borrows 跑：

```
$ MIRIFLAGS="-Zmiri-tree-borrows" cargo +nightly miri run
    Finished `dev` profile [unoptimized + debuginfo] target(s)
     Running `...`
x = 20
```

**同一支程式，Stacked Borrows 判 UB，Tree Borrows 放行。** 這不是 bug，是兩個模型設計哲學不同：

```
  Stacked Borrows                    Tree Borrows
  ┌────────────────────┐             ┌────────────────────┐
  │ 用「堆疊」建模       │             │ 用「樹」建模        │
  │ 父借用被「用」→     │             │ 每個節點有狀態機：  │
  │ 子借用直接 pop 消失 │             │ Reserved→Active→   │
  │                    │             │ Frozen→Disabled    │
  │ 較嚴：pop 後再用 UB │             │ 較寬：狀態轉移才 UB │
  │ 無法描述某些合法    │             │ 能接受更多實務上    │
  │ pattern（誤報）     │             │ 安全的 unsafe 寫法  │
  └────────────────────┘             └────────────────────┘
```

- **Stacked Borrows** 把借用當堆疊：透過父存取，直接把子從堆疊 pop 掉，之後用子就是「tag 不在堆疊」→ UB。簡單、嚴格，但有時**誤報**：某些其實安全的 unsafe pattern（例如某些 `&mut` 與裸指標交錯的合法用法）它也判 UB。
- **Tree Borrows** 給每個借用節點一個狀態機（`Reserved`/`Active`/`Frozen`/`Disabled`），透過父存取只是讓子**轉狀態**（例如變 `Disabled`），而不是立刻消失；只有在「已 Disabled 還去做被禁的存取」才報 UB。這讓它接受更多實務安全的寫法，減少誤報。

在我這支 `sbtest` 裡，`*r = 10` 之後 `*p = 20` 這個「父寫、子寫」序列，在 SB 是「p 已 pop → 用 p → UB」，在 TB 是「p 的分支被父寫轉成某狀態、但這次的子寫剛好還被允許」，於是放行。**兩個模型對同一段 unsafe 給出不同判決**——這就是為什麼我一直說「尚未定案」。

**這對你的實務意義**：

1. **通過 Stacked Borrows 的程式，一定通過 Tree Borrows**（TB 更寬，SB 較嚴）。所以**用 SB（Miri 預設）跑就是最保守的**——過了 SB，兩個模型都安心。
2. 如果你的 unsafe code 被 SB 判 UB、但你確信它安全，先別急著說 Miri 錯——**多半是你的直覺錯**。真的遇到 SB 誤報（罕見），可以用 `-Zmiri-tree-borrows` 對照，但別把「TB 放行」當免死金牌，因為 TB 本身也還在改。
3. **寫 unsafe 抽象（Ch 21 手刻 Vec）時，目標是過 SB**，這樣不管未來語言選哪個模型你都安全。

## Miri 是什麼：MIR 解釋器 + UB 偵測器

`sbtest` 那份報告不是編譯器出的，是 **Miri** 跑出來的。搞懂 Miri 是什麼、能幹嘛、不能幹嘛，是 Part 3 之後的核心工作流。

**Miri = MIR Interpreter。** Rust 編譯過程中，AST 降級成 **MIR（Mid-level IR，中階中介碼）**，這是一個貼近 Rust 語意、控制流已攤平的中介表示。Miri 不把 MIR 編成機器碼跑，而是**逐條解釋執行 MIR**，同時維護一個「抽象記憶體模型」——每塊配置的 allocation、每個指標的 provenance 與 borrow tag、每個 byte 是否初始化，它全都追蹤。每次記憶體存取，它對照 UB 規則檢查。這就是為什麼 Miri 能抓到 native 執行看不出的 UB：native 只是把 bit 搬來搬去，Miri 還額外驗證「這次搬動符不符合 Rust 的抽象語意」。

```
  你的 .rs
     │  rustc 前端
     ▼
    MIR  ─────┐
     │        │  Miri：逐條解釋 MIR
     │        ▼
     │   ┌─────────────────────────────┐
     │   │ 抽象記憶體模型               │
     │   │  · allocation + 邊界        │  每步存取檢查：
     │   │  · provenance / borrow tag  │  越界？懸空？未對齊？
     │   │  · 每 byte 初始化狀態        │  未初始化？非法值？
     │   │  · data race 追蹤            │  違反 SB/TB？data race？
     │   └─────────────────────────────┘
     │        │
     │        ▼
     │   有 UB → 報告 + abort
     │
     └──（另一條路）rustc 後端 → LLVM → 真機器碼（native 跑，無這些檢查）
```

**Miri 抓得到什麼**（本章已各真跑一個）：

1. 懸空解引用（use-after-free）
2. 越界（out-of-bounds，綁 provenance）
3. 未對齊存取
4. 讀未初始化記憶體
5. 產生非法值（`bool`=2、未初始化 `&`）
6. 違反 aliasing model（Stacked/Tree Borrows）
7. data race
8. 記憶體洩漏（leak，預設也會報）

**Miri 抓不到什麼**（這幾條是它的硬限制，要記牢）：

- **不執行真 FFI**。Miri 是解釋器，沒有真的 libc、沒有真的 syscall 表——它**無法呼叫真正的 C 函式**。呼叫 `extern "C"` 的真函式，Miri 直接報 unsupported（下面真跑）。有 `-Zmiri-native-lib` 實驗支援載入原生 lib，但預設環境不行。
- **非窮盡（只查跑到的路徑）**。Miri 是**動態**工具，不是形式驗證——它只檢查**這次執行實際走過的程式路徑**。UB 藏在一個這次沒進去的 `if` 分支裡，Miri 這次就不會報（下面真跑）。所以 Miri 抓 UB 的能力**取決於你的測試覆蓋率**——跟 ASan/TSan 一樣，要餵夠多樣的輸入。
- **慢**。逐指令解釋 + 每步檢查，比 native 慢一到多個數量級。拿來跑單元測試和小輸入，不是跑生產 workload。
- **不完全等同最終語言規則**。如上一節，aliasing model 尚未定案，Miri 用的 SB/TB 是「當前最佳近似」，不是官方最終條文。

先跑「Miri 不執行真 FFI」。呼叫 libc 的 `abs`：

```rust
unsafe extern "C" {
    fn abs(input: i32) -> i32;   // libc abs
}
fn main() {
    let x = unsafe { abs(-5) };
    println!("abs(-5) = {}", x);
}
```

native 跑：`abs(-5) = 5`。Miri 跑：

```
error: unsupported operation: can't call foreign function `abs` on OS `linux`
  |                      ^^^^^^^ unsupported operation occurred here
error: aborting due to 1 previous error
```

`can't call foreign function abs`——Miri 沒有真的 libc 可以呼叫。這是 FFI-heavy 的 crate 用 Miri 的最大限制：C 那半邊 Miri 看不進去，你只能對 Rust 這半邊（含把 C 回傳值當輸入的邊界處理）跑 Miri。

再跑「非窮盡」。把懸空指標的 UB 藏在一個這次不會走到的分支：

```rust
fn dangle() -> *const i32 {
    let local = 999;
    &local as *const i32
}
fn main() {
    // 這個 flag 執行時是 false（來自 args，Miri 不知道值），UB 分支不會被走到
    let go = std::env::args().count() > 100;
    let p = dangle();
    if go {
        unsafe { println!("{}", *p); }   // UB 在這條分支，但這次沒走到
    } else {
        println!("走安全分支，沒碰 p");
    }
}
```

Miri 跑（**沒有報 UB**，因為 UB 那條分支這次沒執行）：

```
走安全分支，沒碰 p
```

`dangle()` 明明造了懸空指標，但因為 `go` 是 false、UB 分支沒被走到，Miri 這次就過了。這徹底說明：**Miri 綠燈不等於「沒有 UB」，只等於「這次執行走過的路徑沒有 UB」**。要靠它抓完，得配好的測試餵多樣輸入把路徑走遍——這是它和「型別系統靜態保證」的本質差別。

## 再跑兩個 Miri 報告：懸空與未對齊

把本課從 Ch 17 一路欠的兩個 Miri 報告補齊，你之後 audit 時看到這類報告就秒懂。

**懸空（use-after-free）**——[Ch 17](./17-unsafe-basics.md) 那個回傳 local 位址的經典：

```rust
fn dangle() -> *const i32 {
    let local = 12345;
    &local as *const i32
}
fn main() {
    let p = dangle();
    unsafe { println!("*p = {}", *p); }
}
```

Miri 跑（本機真跑全文）：

```
warning: function returns a dangling pointer to dropped local variable `local`
 --> src/main.rs:3:5
  |
1 | fn dangle() -> *const i32 {
  |                ---------- return type is `*const i32`
2 |     let local = 12345;
  |         ----- local variable `local` is dropped at the end of the function
3 |     &local as *const i32
  |     ------^^^^^^^^^^^^^^
  |     |
  |     dangling pointer created here
  |
  = note: a dangling pointer is safe, but dereferencing one is undefined behavior

error: Undefined Behavior: constructing invalid value of type &i32: encountered a dangling reference (use-after-free)
 --> src/main.rs:7:34
  |
7 |     unsafe { println!("*p = {}", *p); }
  |                                  ^^ Undefined Behavior occurred here
```

`encountered a dangling reference (use-after-free)`——Miri 精準指到解引用那行。注意 rustc 這邊還先給了一個 `dangling_pointers_from_locals` warning（`a dangling pointer is safe, but dereferencing one is undefined behavior`）——**建立**懸空指標安全，**解引用**才 UB，這句話 Ch 17 講過。

**未對齊（misaligned）**——在非 4-byte 對齊的 offset 讀 `u32`：

```rust
fn main() {
    // 一塊 byte 緩衝，故意在非 4-byte 對齊的 offset 讀 u32
    let buf: [u8; 8] = [0, 1, 2, 3, 4, 5, 6, 7];
    let base = buf.as_ptr();
    unsafe {
        // offset 1 不是 4 的倍數 -> u32 讀取未對齊 = UB
        let misaligned = base.add(1) as *const u32;
        let v = *misaligned;
        println!("misaligned u32 = 0x{:08x}", v);
    }
}
```

這個範例有個**現代 rustc 的意外收穫**：native debug 版現在也會抓（1.97 的 debug assertions 加了對齊檢查）。native 跑（**直接 abort**）：

```
misaligned pointer dereference: address must be a multiple of 0x4 but is 0x7ffff51de865
thread caused non-unwinding panic. aborting.
Aborted (core dumped)
```

Miri 跑：

```
error: Undefined Behavior: accessing memory based on pointer with alignment 1, but alignment 4 is required
 --> src/main.rs:8:17
  |
8 |         let v = *misaligned;
  |                 ^^^^^^^^^^^ Undefined Behavior occurred here
```

`alignment 1, but alignment 4 is required`——`u32` 需要 4-byte 對齊，你在 offset 1（對齊 1）讀，UB。這裡值得注意 native 和 Miri 的分工：**現在連 native debug 版都幫你 abort**（rustc debug-assertions 內建對齊檢查），但 release 版不會、且 native 對「aliasing / provenance」這類 UB 仍完全無感——那些只有 Miri 抓得到。要讀未對齊資料的合法做法是 `ptr::read_unaligned`（[Ch 18](./18-unsafe-advanced.md) 有）。

## provenance 與 `#[repr(Rust)]`：兩個常被忽略的地基

**provenance（指標來源）。** Rust 的指標不只是一個位址數字——它還攜帶一個抽象的「來源」資訊：這個指標是從**哪一塊 allocation** 衍生出來的，以及它被允許存取的範圍。這叫 provenance。它是「越界算術就 UB」的理論基礎：一個指向陣列 `a` 的指標，即使你用算術把它的**數值**算到剛好等於陣列 `b` 的位址，它的 provenance 仍綁在 `a`，用它去存取 `b` 就是越界 UB。

真跑：`a` 只有 3 個元素，`add(5)` 越界，即使記憶體上後面剛好接著 `b`：

```rust
fn main() {
    let a = [10i32, 20, 30];
    let b = [40i32, 50, 60];
    let pa = a.as_ptr();
    unsafe {
        let outside = pa.add(5);   // a 只有 3 個元素，add(5) 已越界
        println!("{}", *outside);
    }
    let _ = b;
}
```

Miri 跑：

```
error: Undefined Behavior: in-bounds pointer arithmetic failed: attempting to offset pointer by 20 bytes, but got alloc168 which is only 12 bytes from the end of the allocation
```

`in-bounds pointer arithmetic failed ... alloc168 which is only 12 bytes from the end`——`pa.add(5)` 要偏移 20 bytes（5×4），但 `a`（`alloc168`）從當前位置只剩 12 bytes。注意 UB 發生在**算術那一步**（`add`），不是解引用——`ptr::add` 要求結果必須落在同一個 allocation 的 in-bounds 範圍內（含尾後一個位置）。這就是 provenance 的執法：位址算對了不算數，你得留在你「有來源」的那塊裡。對照 C：C 標準其實也規定「指標算術只能在同一陣列內（含尾後）」，只是幾乎沒工具抓，大家都當它不存在；Rust 用 Miri 把它變成能抓的 UB。

> provenance 的形式化（strict provenance、`ptr::with_addr`、int↔ptr 轉換的語意）本身是一個還在演進的研究領域，本課點到為止；想深挖見延伸閱讀的 Ralf Jung provenance 文章。

**`#[repr(Rust)]` 佈局不保證。** 這一點對做 FFI / transmute 的你是硬約束：**Rust 預設的 struct 佈局（`#[repr(Rust)]`，也就是你不寫 `repr` 時的預設）不保證欄位順序、不保證無 padding、跨編譯甚至跨同一編譯的不同型別都可能重排。** 編譯器有權為了減少 padding 而重排欄位（[Ch 15](./15-memory-layout.md) 講過 niche optimization 也在這層動手腳）。所以：

- 你**不能** `transmute` 兩個 `#[repr(Rust)]` struct 就假設欄位對得上——佈局沒保證，這是 instant UB 的高發區。
- 要跨 FFI 或依賴佈局，必須標 `#[repr(C)]`（C 相容、順序固定、padding 規則明確）或 `#[repr(transparent)]`（單欄位透明）——[Ch 15](./15-memory-layout.md)、[Ch 19](./19-ffi.md) 講過。
- 這也是為什麼 [Ch 18](./18-unsafe-advanced.md) 反覆說 `transmute` 危險：兩邊佈局你若沒用 `repr` 釘死，Rust 沒承諾它們一致。

一句總結：**`repr(Rust)` = 「編譯器自由排」，`repr(C)` = 「你說了算」。** 任何依賴記憶體佈局的 unsafe，先確認 `repr`。

## 對比與取捨

| 面向 | C（靠工具逼近） | Rust safe | Rust unsafe + Miri |
|---|---|---|---|
| UB 範圍 | 整個語言 | 保證無 UB | 圈在 unsafe，Miri 可查 |
| aliasing 規則 | 無（除 `restrict`） | `&mut` 唯一性由 borrow checker 保證 | 規則仍在，unsafe 裡也得守，Miri 抓違反 |
| 抓 UB 的工具 | valgrind/ASan/TSan（各管一塊） | 不需要 | Miri 一把抓多類（含 aliasing） |
| FFI 的 UB | 全靠人 | — | Miri **抓不到真 FFI** |
| 檢查完整性 | 動態、非窮盡 | 靜態、窮盡（型別系統） | Miri 動態、非窮盡（看路徑） |
| aliasing model 定案否 | N/A | N/A | **未定案**（SB 預設 / TB 實驗） |

取捨要誠實：**Miri 不是銀彈。** 它抓 aliasing / provenance / 未初始化這類「native 完全無感」的 UB 極強，是 C 工具鏈給不了的能力；但它慢、非窮盡、看不進真 FFI，而且它執法的 aliasing model 本身還在演進。正確定位：**Miri 是 unsafe 開發的必備 lint，跟你的測試套件配合用（`cargo +nightly miri test`），不是「跑過 Miri = 證明無 UB」的形式驗證。**

## 踩雷集錦

1. **以為「native 跑起來對」就代表 unsafe 沒問題**：本章 `sbtest` native 印 `x = 20` 完全正確，但它違反 Stacked Borrows、是 UB。UB 不保證 crash——編譯器在「你不做 UB」的假設下優化，這次剛好沒踩到依賴該假設的優化而已。**「跑起來對」從來不是 sound 的證據**，要靠推理 + Miri。這是 C 老手最頑固的錯誤直覺。

2. **以為 unsafe 裡就不用管借用規則了**：`unsafe` 只解鎖五種操作（[Ch 17](./17-unsafe-basics.md)），**不關掉 aliasing model**。「有一個 `&mut` 又同時留一個裸指標，然後父子交替讀寫」——這在 C 天經地義，在 Rust unsafe 裡是 UB。正確認識：裸指標從 `&`/`&mut` 衍生就綁上那條借用鏈，得遵守 SB/TB 規則。要真正「多路徑寫同一塊」得用 `UnsafeCell`（互斥/內部可變性的地基，Ch 16 提過）。

3. **把「Miri 沒報 UB」當成「沒有 UB」**：Miri 非窮盡，只查**這次執行走過的路徑**（本章真跑：UB 藏在沒走到的 `if` 分支，Miri 就綠燈）。Miri 綠燈只代表「這條路徑這次沒 UB」。要抓全，得配測試餵多樣輸入把路徑走遍——當成 lint，不是證明。

4. **拿 Miri 去跑 FFI-heavy 的 crate 然後怪它不動**：Miri **不執行真 FFI**（真跑：`can't call foreign function abs`）。C 那半邊它看不進去。FFI wrapper 用 Miri 只能驗 Rust 這半邊的邏輯，不能驗你呼叫的 C 函式行為。

5. **`transmute` 兩個 `#[repr(Rust)]` struct 假設佈局一致**：預設佈局編譯器有權重排欄位、加 padding，**跨型別、跨編譯都不保證一致**。依賴佈局的 unsafe 一定要 `#[repr(C)]`/`#[repr(transparent)]` 釘死，否則 instant UB。

6. **把 Stacked Borrows 的判決當語言最終法律**：aliasing model **尚未定案**。SB 是當前預設但仍實驗（Miri 報告自己都寫 `still experimental`），TB 是較新較寬的提案。策略：**以通過 SB（Miri 預設）為目標**——過了 SB 兩個模型都安全；但別把 SB 的誤報或 TB 的放行當成語言的確定承諾。

## 進階：再往深一層

**`cargo +nightly miri test` 才是實務用法。** 你不會手動 `miri run` 一支支小程式，而是把 Miri 掛到你的測試套件上：`cargo +nightly miri test` 會用 Miri 跑你所有 `#[test]`。這樣每個測試路徑都過一遍 UB 檢查。手刻 `Vec`（Ch 21）、寫 FFI wrapper（Ch 19）、audit 別人的 unsafe crate（Ch 32）——工作流都是「寫測試覆蓋 unsafe 路徑 → `cargo +nightly miri test`」。CI 裡掛 Miri 是嚴肅 unsafe 專案的標配。

**有用的 MIRIFLAGS。** `-Zmiri-tree-borrows` 切 Tree Borrows（對照用）；`-Zmiri-seed=N` 換隨機種子（Miri 對未初始化 byte / 位址用固定但可調的隨機，多跑幾個 seed 增加覆蓋）；`-Zmiri-many-seeds` 自動跑多 seed；`-Zmiri-preemption-rate` 調 thread 搶佔率（提高抓 data race 機率，本章 data race 例子就靠它）；`-Zmiri-backtrace=full` 看完整 UB backtrace。`-Zmiri-strict-provenance` 開嚴格 provenance（把 int→ptr 轉換也當可疑），寫 provenance-clean 的 code 時用。

**data race 也是 Miri 的守備範圍。** 兩個 thread 透過同一個 `*mut i32` 非原子讀寫、無同步，就是 data race UB。真跑（`static mut` + 兩個 thread 各 `+= 1`，用 `-Zmiri-preemption-rate=0.5` 提高交錯機率）：

```
error: Undefined Behavior: Data race detected between (1) non-atomic write on thread `unnamed-1`
       and (2) non-atomic read on thread `unnamed-2` at alloc1
```

native 跑印 `COUNTER = 2`（看起來對），Miri 抓到 data race。這條 Part 4（並發）會大用——本章先讓你知道 Miri 連 data race 都守。

**面試常問**：「Miri 能證明我的 unsafe 沒 UB 嗎？」——標準答案：**不能**。Miri 是**動態、非窮盡**的解釋器，只檢查實際執行的路徑，且看不進真 FFI、用的 aliasing model 還沒定案。它是強力 lint（能抓 native 完全無感的 aliasing/provenance/未初始化 UB），但「Miri 綠燈」只等於「跑過的路徑這次沒 UB」，不等於形式證明。能講清這個界線，代表你懂 unsafe 驗證的真實邊界，不是把 Miri 當銀彈。

**再深一層的另一個問題**：「為什麼 Rust 要有 aliasing model，C 沒有也活得好好的？」——因為 Rust 想要 C 拿不到的優化。`&mut` 唯一性讓編譯器能像 `restrict` 那樣假設「這個指標背後的值不會被別名偷改」，把它 cache 進暫存器、重排讀寫。C 因為指標可能任意別名，很多這種優化不能做（除非你手動 `restrict`）。Rust 把 `restrict` 變成 `&mut` 的預設語意——代價就是這條你在 unsafe 裡也得守的 aliasing model。

## 動手練習

1. **重現「native 對、Miri 抓」的 Stacked Borrows 違規**：把本章 `sbtest` 程式打進去，先 `cargo run` 確認印 `x = 20`（看起來正常），再 `cargo +nightly miri run` 看它報 UB。逐行對照 Miri 報告的三段（tag 誕生於哪行、被哪行作廢、在哪行被誤用），畫出 borrow stack 的演化。

2. **同一支跑 Tree Borrows 對照**：對上面那支加 `MIRIFLAGS="-Zmiri-tree-borrows" cargo +nightly miri run`，確認它**放行**。想清楚為什麼 SB 判 UB 而 TB 沒有，並用一句話說出「以哪個模型為目標最保守」。

3. **造 Miri 的「非窮盡」盲點**：寫一支把 UB（懸空解引用或越界）藏在一個 runtime 才決定、這次為 false 的 `if` 分支裡，確認 Miri 綠燈。再把條件改成 true，確認 Miri 這次抓到。體會「Miri 綠燈 ≠ 無 UB」。

4. **抓一個未初始化讀**：用 `MaybeUninit::<i32>::uninit()` 不寫值直接 `assume_init()` 讀，native 看它印垃圾數字，Miri 看它報 `encountered uninitialized memory`。

## 本章重點整理

- **Rust 的 UB 清單和 C 大量重疊**（懸空/未對齊/越界/data race/未初始化），但 Rust **只在 unsafe 出現**（safe 側型別系統+borrow checker 保證無 UB），且**多兩類 C 沒有的**：非法值（`bool`=2 instant UB）與違反 aliasing model；同時**拿掉一類**：有號溢位在 Rust 不是 UB。
- **aliasing model = `&mut` 唯一性在 unsafe 裡也強制**：「有 `&mut` 又留裸指標交替讀寫」即使 native 跑起來完全正常（真跑 `x = 20`），也是 UB——透過父指標存取會作廢晚衍生的子指標，之後用子 = UB。這是 `restrict` 的預設強制版，C 沒有。
- **Stacked Borrows（預設，用堆疊）vs Tree Borrows（實驗，用樹狀態機）**：同一支程式 SB 判 UB、TB 放行；**aliasing model 尚未定案**，策略是「以過 SB 為目標」（最保守，兩模型都安全）。
- **Miri = MIR 解釋器 + UB 偵測器**：逐條解釋 MIR、維護抽象記憶體模型、每次存取查 UB。抓得到 native 完全無感的 aliasing/provenance/未初始化 UB；**抓不到**真 FFI（真跑 `can't call foreign function`）、**非窮盡**（只查走過的路徑，真跑 UB 藏在沒走的分支就綠燈）。是強力 lint，不是形式證明。
- **provenance = 指標的來源**：越界算術即 UB（真跑 `in-bounds pointer arithmetic failed`），位址算對也不算數。**`#[repr(Rust)]` 佈局不保證**（欄位可重排），依賴佈局的 unsafe 必須 `#[repr(C)]`/`#[repr(transparent)]`。

## 自我檢核

- [ ] 面試問「一段 unsafe native 跑起來輸出完全正確，能不能就說它沒 UB」，能答「不能」並用本章 `sbtest`（違反 SB 但印正確 `x=20`）當例子解釋為什麼。
- [ ] 不看筆記，能解釋「有 `&mut` 又留一個從它衍生的裸指標交替寫」為什麼是 UB，以及借用堆疊在這過程怎麼變化。
- [ ] 能說出 Stacked Borrows 與 Tree Borrows 的核心差別（堆疊 vs 樹狀態機、嚴 vs 寬），以及為什麼「以過 SB 為目標」最保守。
- [ ] 能列出 Miri **抓不到**的三件事（真 FFI、沒走到的路徑、慢到不能跑生產 workload），並說明「Miri 綠燈」的正確解讀。
- [ ] 能解釋 provenance 為什麼讓「位址算對的越界指標」仍是 UB，以及 `#[repr(Rust)]` 對 transmute/FFI 的意涵。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 論文

- **[Stacked Borrows: An Aliasing Model for Rust](https://plv.mpi-sws.org/rustbelt/stacked-borrows/paper.pdf)** — Ralf Jung, Hoang-Hai Dang, Jeehoon Kang, Derek Dreyer（POPL 2020）
  - **核心貢獻**：正式提出本章的 Stacked Borrows 模型——把 Rust 的別名規則形式化成「借用堆疊」，讓「`&mut` 唯一性」有可機械檢查的定義，並證明它容許哪些編譯器優化。Miri 的 SB 檢查就是這篇的實作。
  - **讀哪裡**：Section 2（用例子建立 borrow stack 的直覺，和本章那張堆疊演化圖對應）、Section 3（規則定義）；證明部分（Section 5+）太數學可跳過。
  - **和本章的關聯**：本章 `sbtest` 那份 Miri 報告的 tag/retag/invalidate 機制，就是這篇的 push/pop 規則。想真懂 Miri 為什麼那樣判，讀這篇。
  - **前提**：懂本章 aliasing model 直覺 + 借用（Ch 3）；不需要懂形式語意也能讀前半。

### 官方文件 / Spec

- **[The Rust Reference — Behavior considered undefined](https://doc.rust-lang.org/reference/behavior-considered-undefined.html)**
  - **讀哪裡**：整頁 UB 清單——本章那張 UB 對照表的權威來源，逐條列出懸空/未對齊/越界/data race/非法值/違反別名等。
  - **學到什麼**：Rust 官方「什麼算 UB」的定義性清單；寫 unsafe 時對照自查。特別看「Producing an invalid value」那段（本章 `bool`=2 的權威依據）。
  - **前提**：懂本章 UB 概念；這是條文，當工具書查。

- **[Miri README](https://github.com/rust-lang/miri)** — Rust 官方 miri 儲存庫
  - **讀哪裡**：「What does Miri do?」「Common Problems」「Miri `-Z` flags and environment variables」三節——後者列出本章用到的所有 MIRIFLAGS（`-Zmiri-tree-borrows`、`-Zmiri-seed`、`-Zmiri-preemption-rate` 等）。
  - **學到什麼**：Miri 抓得到/抓不到什麼的權威清單（和本章「限制」節對應）、每個 flag 的精確語意、`cargo miri test` 的正確用法。
  - **前提**：裝好 nightly + miri component；這是你之後每次用 Miri 的手冊。

### 技術文章

- **[Two Kinds of Invariants: Safety and Validity](https://www.ralfj.de/blog/2018/08/22/two-kinds-of-invariants.html)** — Ralf Jung（個人部落格，2018）
  - **這篇說什麼**：把「型別的不變量」拆成 validity invariant（值本身合不合法，如 `bool` 只有 0/1，違反即 instant UB）和 safety invariant（安全 code 能依賴的更強不變量）。本章「產生非法值 = UB」講的就是 validity invariant。
  - **讀哪裡**：整篇不長；「Validity Invariant」與「Safety Invariant」兩節是核心。
  - **為什麼值得讀**：作者是 Stacked Borrows、Miri、RustBelt 的主導者，Rust unsafe 語意的第一權威。這篇釐清一個常見混淆——為什麼 `transmute(2u8)` 成 `bool` 是**立刻** UB 而不是「用它才 UB」。
  - **前提**：懂本章非法值 UB；這是它背後的理論框架。

- **[Tree Borrows](https://perso.crans.org/vanille/treebor/)** — Neven Villani（Tree Borrows 提案網站/論文，2023+）
  - **這篇說什麼**：本章 Tree Borrows 的第一手來源——為什麼要用「樹 + 狀態機」取代 Stacked Borrows 的堆疊、TB 比 SB 多接受哪些實務安全的 unsafe pattern、狀態轉移（Reserved/Active/Frozen/Disabled）的規則。
  - **讀哪裡**：網站的互動式解說（可以逐步看借用樹演化，比讀論文直觀）；想深入再看配套論文。
  - **為什麼值得讀**：本章說「aliasing model 未定案」，這篇就是「另一個候選」的權威說明。理解 TB 為什麼放行本章那支 SB 判 UB 的程式，讀這裡。
  - **前提**：懂本章 Stacked Borrows；TB 是拿它當對照講的。

搞懂了 UB 清單、aliasing model 為什麼在 unsafe 裡也強制、Miri 怎麼抓、抓不到什麼，下一章我們把 Ch 17–20 的工具全用上——**從零手刻一個 sound 的 `Vec`**：裸指標讀寫、`MaybeUninit`、佈局計算、drop 正確性，外面包成一個外部無論怎麼用都不可能 UB 的安全 API，而且**全程用 Miri 驗證**它真的 sound。這是本 Part 所有 unsafe 知識的整合實戰。

→ [Ch 21 手刻 unsafe 抽象：安全的 Vec](./21-unsafe-abstractions.md)
