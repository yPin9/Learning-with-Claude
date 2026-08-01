# Ch 01 — 為什麼是 Rust：給 C/C++ 人的定位

> **目標**：講清楚 Rust 到底解決了 C/C++ 的什麼問題、憑什麼解決。你會看到同一類記憶體 bug——use-after-move、iterator invalidation、data race、dangling reference——在 C++ 裡編得過、跑起來爆，在 Rust 裡**編不過**。學完你能對 C/C++ 同事說明白：這不是「又一個新語言」，是把一整類 CVE 從執行期問題變成編譯期錯誤。同時也誠實交代 Rust 的代價，給你正確期待。

## 為什麼需要這個？先看數據

「記憶體安全很重要」這句話講爛了，但你可能沒看過具體數字。三家最有資格說話的公司，各自數了自家 CVE：

- **Microsoft（MSRC）**：2019 年 BlueHat IL，Matt Miller 報告——**約 70% 每年被指派 CVE 的漏洞是記憶體安全問題**，而且這個比例從 2006 到 2018 十幾年幾乎沒動過，儘管期間投入了大量 code review、靜態分析、mitigation。
- **Google Chromium**：分析 2015 年以來 912 個高/嚴重等級的安全 bug，**約 70% 是記憶體不安全問題**，其中**一半是 use-after-free**。
- **Google Android**：記憶體安全漏洞佔比從 2019 的 **76%** 降到 2022 的 **35%**——2022 是第一年記憶體安全 bug 不再是多數。同期年度記憶體安全漏洞數從 223 掉到 85。掉下來的原因？他們從 Android 12 開始把 Rust 引進平台，Android 13 有 21% 的新原生碼是 Rust，而**到那時 Rust 碼裡的記憶體安全漏洞數是零**。

（來源都放在文末延伸閱讀。）

這三個數字指向同一件事：**C/C++ 最貴的安全成本，是記憶體安全 bug，而且靠人的紀律壓不下去**——微軟壓了十幾年比例沒動。這不是罵誰粗心，是 C/C++ 的記憶體模型本身把「正確」的責任全押在人身上，而人在幾百萬行程式碼的規模下必然出錯。Rust 的整個賣點，就是把這份責任交給編譯器。

## 先建立直覺：防線在編譯期，還是執行期？

你在 C/C++ 防記憶體 bug 的工具，全部是**執行期**才生效的：

```
C/C++ 的防線（全在執行期，程式已經在跑了才抓到）
────────────────────────────────────────────────
寫 code ─▶ 編譯通過 ─▶ 執行 ─┬─▶ ASan 抓到 UAF（要跑到那行才觸發）
                            ├─▶ Valgrind 報 invalid read（慢 10–50x）
                            └─▶ 沒跑到那條路徑 → bug 潛伏，上線後被人 exploit
```

問題在最後一條：ASan/Valgrind 只能抓到「你這次執行真的踩到的」路徑。沒跑到的 code path、只在特定輸入下觸發的 UAF，測試蓋不到就漏了——而攻擊者專門找你測試沒蓋到的路徑。

Rust 把防線挪到**編譯期**：

```
Rust 的防線（在編譯期，程式還沒生出來就擋）
────────────────────────────────────────────
寫 code ─▶ borrow checker 檢查 ─┬─ 通過 ─▶ 編譯 ─▶ 執行（這類 bug 不可能發生）
                               └─ 不通過 ─▶ 編譯錯誤，binary 根本生不出來
```

差別是**覆蓋率的本質**：ASan 檢查「這次執行」，borrow checker 檢查「所有可能的執行」。編譯期擋掉的 bug，不需要對應的測試路徑去觸發——它在證明「不存在這種路徑」。這就是為什麼 Android 的 Rust 碼記憶體安全漏洞數是零，不是「很少」。

代價是：你得先讓 borrow checker 滿意，它會擋掉一些其實安全的寫法（因為它保守）。這是本課 Part 1 的主題。

## C++ 已經有 RAII 和 smart pointer 了，為什麼不夠？

這是 C++ 老手最該問的問題。C++11 之後你有 `unique_ptr`、`shared_ptr`、move 語意、RAII——很多記憶體管理的痛確實解了。**但 C++ 沒有把這些變成強制。** 編譯器不會攔你用錯，工具鏈信任你遵守約定。看三個 C++ 解不掉、Rust 在編譯期擋掉的例子。

### 例一：use-after-move

`unique_ptr` 被 move 之後就變 `nullptr`，但 C++ 不阻止你繼續用被 move 走的變數：

```cpp
#include <memory>
#include <iostream>

int main() {
    auto p = std::make_unique<int>(42);
    auto q = std::move(p);        // move: p 變成 nullptr
    std::cout << *p << "\n";      // use-after-move：解參考 nullptr，UB
    std::cout << *q << "\n";
    return 0;
}
```

用 g++ 11 開滿警告編它——**編過，一個警告都沒有**：

```
$ g++ -std=c++20 -Wall -Wextra -Wpedantic uam.cpp -o uam
$ echo $?
0
```

跑起來：

```
$ ./uam
Segmentation fault (core dumped)
$ echo $?
139
```

編譯器讓它過，執行期 segfault（exit 139 = 128 + SIGSEGV 11）。`-Wall -Wextra -Wpedantic` 全開都沒吭一聲——因為「用被 move 走的物件」在 C++ 是合法的（move 後物件處於 "valid but unspecified state"），編譯器沒立場擋。這裡剛好是 nullptr 所以 segfault；換個型別它可能悄悄回傳垃圾值，更難查。

同樣的邏輯用 Rust 寫：

```rust
fn main() {
    let s = String::from("hello");
    let t = s;              // move: s 的所有權交給 t
    println!("{}", s);      // 錯：s 已經被 move 走
    println!("{}", t);
}
```

`rustc` 直接擋在編譯期：

```
error[E0382]: borrow of moved value: `s`
 --> uaf.rs:4:20
  |
2 |     let s = String::from("hello");
  |         - move occurs because `s` has type `String`, which does not implement the `Copy` trait
3 |     let t = s;              // move: s 的所有權交給 t
  |             - value moved here
4 |     println!("{}", s);      // 錯：s 已經被 move 走
  |                    ^ value borrowed here after move
  |
help: consider cloning the value if the performance cost is acceptable
  |
3 |     let t = s.clone();              // move: s 的所有權交給 t
  |              ++++++++
```

注意錯誤訊息的品質：它指出 `s` 在哪行被 move（第 3 行）、在哪行被誤用（第 4 行）、為什麼會 move（`String` 沒實作 `Copy`），還給修法建議（`clone()`）。這就是本課一直強調「rustc 錯誤訊息是最好的教材」的意思——它不只說你錯，還教你 ownership 怎麼運作。Ch 2 會把 move 語意講透。

### 例二：iterator invalidation

C++ 經典 UB：邊迭代一個容器邊改它的大小，迭代器失效：

```cpp
std::vector<int> v = {1, 2, 3};
for (auto it = v.begin(); it != v.end(); ++it) {
    if (*it == 2) v.push_back(4);   // push_back 可能 realloc，it 失效 → UB
}
```

`push_back` 若觸發 realloc，底層緩衝區搬家，`it` 指向已釋放的舊記憶體。C++ 編譯器不擋，執行期可能 crash、可能讀到垃圾、可能剛好沒事（最糟的一種，因為它讓 bug 潛伏）。

Rust 對應寫法：

```rust
fn main() {
    let mut v = vec![1, 2, 3];
    for x in &v {           // 不可變借用 v
        if *x == 2 {
            v.push(4);      // 想在迭代中改 v：可變借用
        }
    }
    println!("{:?}", v);
}
```

編不過：

```
error[E0502]: cannot borrow `v` as mutable because it is also borrowed as immutable
 --> iterinv.rs:5:13
  |
3 |     for x in &v {           // 不可變借用 v
  |              --
  |              |
  |              immutable borrow occurs here
  |              immutable borrow later used here
4 |         if *x == 2 {
5 |             v.push(4);      // 想在迭代中改 v：可變借用
  |             ^^^^^^^^^ mutable borrow occurs here
```

Rust 的規則是**別名 XOR 可變**（aliasing XOR mutability）：迭代 `for x in &v` 期間 `v` 被不可變借用了，這時任何拿到可變借用去改它的企圖都是編譯錯誤。`push` 需要 `&mut self`，衝突，擋下。iterator invalidation 這整類 bug 在 Rust 裡不可能編過。這是 Ch 3 借用規則的核心。

### 例三：dangling reference（回傳區域變數的參考）

C++ 裡回傳區域變數的參考，變數在函式結束就死了，你手上是懸空指標：

```cpp
const std::string& dangle() {
    std::string s = "hello";
    return s;               // s 出 scope 就 destroy，回傳懸空 reference（UB）
}
```

g++ 這個會給警告（`-Wreturn-local-addr`），但那是編譯器的**善意提醒**，不是保證——換個間接一點的寫法（把參考塞進 struct 再回傳）警告就沒了，UB 還在。Rust 用 lifetime 系統從根本擋：

```rust
fn dangle() -> &String {     // 想回傳區域變數的參考
    let s = String::from("hello");
    &s                       // s 在函式結束就被 drop，這個參考會懸空
}

fn main() {
    let r = dangle();
    println!("{}", r);
}
```

編不過：

```
error[E0106]: missing lifetime specifier
 --> dangle.rs:1:16
  |
1 | fn dangle() -> &String {     // 想回傳區域變數的參考
  |                ^ expected named lifetime parameter
  |
  = help: this function's return type contains a borrowed value, but there is no value for it to be borrowed from
help: instead, you are more likely to want to return an owned value
  |
1 - fn dangle() -> &String {     // 想回傳區域變數的參考
1 + fn dangle() -> String {     // 想回傳區域變數的參考
  |
```

編譯器直接說：你回傳的是借用，但沒有東西可以借給它（`there is no value for it to be borrowed from`），並建議你改成回傳 owned value（`String` 而非 `&String`）。lifetime 系統會追蹤每個參考「借的東西活多久」，一旦參考可能活得比被借的東西久，就是編譯錯誤。這是 Ch 4 lifetime 的主題。

## 最難的那個：data race

前三個是單執行緒就會的錯。並發世界更狠——data race 在 C++ 是 UB，而且**難以重現、難以測試、難以 code review**。Rust 最有價值的保證之一是「safe Rust 不可能有 data race」，它靠型別系統做到，不是靠執行期偵測。

看 Rust 怎麼在編譯期擋一個跨執行緒的資料競爭：

```rust
use std::thread;

fn main() {
    let mut counter = 0;
    let handle = thread::spawn(|| {
        counter += 1;   // 另一條 thread 想改 main 的 counter
    });
    counter += 1;       // main 也在改同一個 counter
    handle.join().unwrap();
    println!("{}", counter);
}
```

兩條 thread 同時無同步地改 `counter`——教科書等級的 data race。C++ 你得跑到 ThreadSanitizer 才可能抓到（而且要那次執行剛好排到那個交錯）。Rust 編不過：

```
error[E0373]: closure may outlive the current function, but it borrows `counter`,
              which is owned by the current function
 --> race.rs:5:32
  |
5 |     let handle = thread::spawn(|| {
  |                                ^^ may outlive borrowed value `counter`
6 |         counter += 1;   // 另一條 thread 想改 main 的 counter
  |         ------- `counter` is borrowed here
...
error[E0503]: cannot use `counter` because it was mutably borrowed
 --> race.rs:8:5
```

它同時報兩個錯：閉包可能活得比 `counter` 久（thread 不知道 `main` 何時結束）、而且 `counter` 已經被可變借用不能再用。這背後是 `Send`/`Sync` 兩個 trait 加 borrow 規則的合力——**共享的東西不可變、可變的東西不共享**，跨執行緒尤其嚴格。這整套是 Part 4 的主題（Ch 23 起）。重點先記住：這類 data race 在 safe Rust 裡是**編譯錯誤，不是執行期運氣**。

## Rust 的三大支柱：ownership / borrow / lifetime

上面四個例子，背後是同三個機制在運作。這裡只給全景直覺，細節在 Part 1 一章一章拆。

```
        ┌───────────────────────────────────────────┐
        │  每個值有唯一的 owner（擁有者）             │
   ①    │  owner 離開 scope，值就被 drop（RAII 強制）│  ← Ch 2
Ownership│  賦值/傳參預設是 move，不是 copy           │
        └───────────────────────────────────────────┘
                          │ 想暫時用但不奪走所有權？
                          ▼
        ┌───────────────────────────────────────────┐
        │  借用（borrow）：& 不可變 / &mut 可變       │
   ②    │  規則：同一時間 要嘛多個 &，要嘛一個 &mut  │  ← Ch 3
Borrow  │  （別名 XOR 可變）                          │
        └───────────────────────────────────────────┘
                          │ 借來的參考能活多久？
                          ▼
        ┌───────────────────────────────────────────┐
        │  lifetime：每個參考有存活期，編譯器追蹤     │
   ③    │  參考不能活得比它借的東西久                 │  ← Ch 4
Lifetime│  → 消滅 dangling reference                  │
        └───────────────────────────────────────────┘
```

- **Ownership**：對應你熟的 C++ RAII + move 語意，但 Rust 把它變成**語言強制的預設**——每個值唯一 owner，owner 出 scope 自動 drop，賦值預設 move。例一的 use-after-move 就是這條擋的。
- **Borrow**：想用一個值但不奪走所有權，就借用。核心規則「別名 XOR 可變」——例二的 iterator invalidation、例四的 data race 都是這條擋的。這是 C++ 完全沒有的東西。
- **Lifetime**：編譯器追蹤每個參考「活多久、借的東西活多久」，參考不能活得比來源久。例三的 dangling reference 是這條擋的。

這三根柱子合起來，就是那句「safe Rust 沒有 UAF、double-free、data race、dangling reference」的來源。它們不是三個獨立功能，是一套互相咬合的系統——本課 Part 1 六章就在拆這套系統怎麼運作、底層是什麼。

## 為什麼不用 GC？這是刻意的取捨

有人會問：記憶體安全，Java/Go/C# 用 GC 早就做到了，Rust 幹嘛搞這麼複雜的 ownership？

**答案不是「GC 不好」，是 GC 給不了系統程式設計要的兩件硬東西**：

1. **latency 保證**：GC 會在不確定的時間點暫停你的程式（stop-the-world 或並發 GC 的 pause）。對大部分應用這無所謂，但對 kernel、driver、高頻交易、音訊處理、即時控制——一個不可預測的 pause 就是災難。Rust 的記憶體管理是**編譯期決定、執行期無額外執行時**：drop 在哪發生你看 code 就知道，沒有背景執行緒偷偷回收，沒有 pause。這叫 **zero-cost abstraction**——安全性的成本付在編譯期，不付在執行期。

2. **no_std / 無 runtime 場景**：GC 需要一個 runtime（一塊管理堆、追蹤 root、掃描物件的執行時系統）。kernel 裡沒有這種東西，裸機 embedded 更沒有。Rust 能在 `#![no_std]`、沒有作業系統、沒有堆配置器的環境跑（Ch 22、Part 6），因為它的記憶體安全不依賴 runtime。這是 Rust 能進 Linux kernel、能寫 bootloader、能上 microcontroller 的根本原因——Go 進不了 kernel，就是卡在這。

取捨的代價：ownership 系統把「記憶體什麼時候釋放」這個決定從執行期（GC 自動）搬到編譯期（你和 borrow checker 一起定），所以你得學會跟 borrow checker 溝通。GC 語言把這個心智負擔拿掉了——那是它們的優點，也是它們進不了系統底層的原因。選擇取決於你要保證 latency / 上 no_std，還是要開發速度。

| 記憶體管理策略 | 安全性 | latency 可預測 | no_std 可行 | 心智負擔 | 代表 |
|---|---|---|---|---|---|
| 手動（malloc/free） | 差（靠紀律） | 是 | 是 | 高（但無編譯期幫助） | C |
| RAII + smart pointer | 中（不強制） | 是 | 部分 | 中 | C++ |
| GC | 高 | **否**（有 pause） | **否**（要 runtime） | 低 | Java/Go/C# |
| ownership + borrow | 高（編譯期保證） | 是 | 是 | 高（前期學習） | Rust |

## 誠實面：Rust 的代價

教材不吹捧。Rust 有真實的代價，先給你正確期待，別上線了才怨。

1. **學習曲線陡**：borrow checker 前期會一直擋你，你會有一段「跟編譯器吵架」的挫折期。這不是你笨——是你在把 C 裡靠紀律隱性維持的規則，第一次顯性地寫給編譯器看。本課的設計就是幫你度過這段：每個擋你的錯，都對應一類你在 C 裡本來就該避免的 bug。

2. **編譯慢**：泛型單型化（monomorphization，Ch 10）+ LLVM 後端 + 一堆安全檢查，讓 Rust 編譯明顯比 C 慢，大專案增量編譯也不算快。這是真實痛點，社群一直在改善（並行前端、cranelift 後端），但現況就是比 C 慢。

3. **`unsafe` 仍然存在，安全不是絕對的**：Rust 的保證是「safe Rust 沒有那些 UB」，但 `unsafe` 區塊裡你可以做原始指標操作、呼叫 C、繞過檢查——那裡的正確性回到你身上，跟 C 一樣。標準庫、FFI、和硬體打交道的底層，都建立在 `unsafe` 上。**「Rust 很安全」不等於「Rust 沒有記憶體 bug」**，而是「不安全的部分被隔離、標記、可以 audit」。這是 Part 3（unsafe）和 Part 5（資安）的核心——理解 safe 的保證，也理解 unsafe 的破口。

4. **生態相對年輕**：crates.io 生態成長很快，但比起 C/C++ 幾十年的積累，某些領域（特定硬體 driver、老牌函式庫綁定、某些企業 SDK）still 缺成熟的 crate。而且原始碼相依 + 供應鏈是新的攻擊面（Part 5 Ch 32 講 audit）。

這些代價值不值得，取決於你的場景。要記憶體安全 + latency 保證 + no_std 的系統/資安工作，Rust 目前沒有真正的替代品。要快速迭代的 web 後端、有現成 C++ 大 codebase 不想重寫的專案，Rust 未必是最佳解。**Rust 不是萬靈丹，是系統與資安這個特定象限裡目前最好的工具。**

## 這門課會帶你去哪：六個 Part 全景

```
Part 0 定位（你在這）    ── 環境 + 為什麼 Rust
Part 1 所有權模型        ── ownership/borrow/lifetime，本章三支柱的細節（Ch 2–7）
Part 2 型別系統與抽象    ── trait/泛型/enum/錯誤處理/閉包，Rust 的抽象武器（Ch 8–14）
Part 3 記憶體佈局與 unsafe ── repr/智慧指標底層/unsafe/FFI/Miri，接你的 C ABI 知識（Ch 15–22）
Part 4 並發與非同步      ── Send/Sync/atomics/async 狀態機/Tokio，接你的 memory_order（Ch 23–29）
Part 5 資安研究向        ── audit unsafe/逆向 Rust binary/fuzzing，接你的 pwn/RE（Ch 30–36）
Part 6 Rust-for-Linux    ── 真的在 QEMU 跑起來的 kernel module（Ch 37–42 + final）
```

主軸是：從三支柱地基（Part 1）→ 抽象工具（Part 2）→ 打通你既有的 C ABI/記憶體/並發知識（Part 3/4）→ 資安雙向應用（Part 5）→ 最後真的寫一個 kernel module（Part 6）。全程 C/C++ 對照。

## 踩雷集錦

1. **「Rust 安全 = Rust 沒有記憶體 bug」**：錯。Rust 的保證有明確邊界——**safe Rust** 不會有 UAF/double-free/data race/dangling reference。`unsafe` 區塊、FFI、以及邏輯 bug（例如整數溢位導致的錯誤索引）都還是可能出事。正確認識：Rust 把「可能出記憶體 bug 的地方」從整個 codebase **縮小到被 `unsafe` 標記的一小塊**，讓它可 audit。Part 5 整個在講這條邊界。

2. **「C++ 有 smart pointer 就等於有了 Rust 的安全」**：錯。C++ 的 `unique_ptr`/`shared_ptr` 解了「誰負責 free」，但沒解 use-after-move、iterator invalidation、data race、dangling reference——因為 C++ 不**強制**，編譯器信任你。上面四個例子全是 C++ 編得過 Rust 編不過。正確認識：差別不在「有沒有這些工具」，在「編譯器強不強制」。

3. **「borrow checker 擋我 = 我的 code 有 bug」**：不一定。borrow checker 是**保守**的——它會擋掉一些其實安全但它證明不了安全的寫法（Part 1 的 NLL/Polonius 章講它的極限）。被擋不代表你錯，可能是你需要換個表達方式讓它看懂（例如用 index 代替 reference、用 `RefCell` 把檢查挪到執行期）。正確認識：它寧可誤殺不可放過，這是安全性的必要代價。

4. **「Rust 不用 GC 所以一定比有 GC 的語言快」**：不必然。無 GC 給的是 **latency 可預測**和 **no_std**，不是「一定 throughput 更高」。一個寫得爛的 Rust 程式（到處 `clone()`、`Arc<Mutex<>>` 亂包）可能比調校好的 Java 慢。正確認識：Rust 的優勢是「你能控制到零成本，且沒有 GC pause」，不是「隨便寫都快」。

5. **「Rust binary 都是 static、都難逆向」**：錯，兩個都錯。預設**動態連結** glibc（Ch 0 的 `file` 輸出證明過），預設**不 strip**、符號保留——沒 strip 的 Rust binary 符號多到反而好逆。正確認識：Rust binary 的逆向難點不在 static/strip，在單型化產生的大量泛型實體、iterator 展開、和 panic 機制的雜訊（Part 5 Ch 33–34 專講）。

## 進階：再往深一層

- **Rust 的安全性有形式化基礎**：不是「我們很小心所以應該安全」，而是有學術證明。RustBelt 專案（POPL 2018）用 Coq 形式化證明了 Rust 型別系統 + 一部分標準庫的 `unsafe` 是 sound 的。這在系統語言裡罕見——C/C++ 沒有這種等級的形式化保證。想知道「憑什麼相信 Rust 的保證」，這是源頭（延伸閱讀有）。
- **「memory safety 不等於 undefined-behavior-free」**：Rust 消滅了記憶體安全類的 UB，但 safe Rust 仍有其他「錯誤但已定義」的行為——例如整數溢位在 debug build panic、release build wrapping（不是 UB，但可能是邏輯 bug）；panic 導致的 abort。這條界線在 Ch 20（記憶體模型與 UB）講清楚。
- **業界動向給你的職涯訊號**：Rust-for-Linux 進主線（6.1+）、Android 平台語言、Windows kernel 部分元件、CISA/白宮 ONCD 2024 公開呼籲產業轉向 memory-safe 語言、AWS/Cloudflare/Microsoft 大量 Rust 化。這不是炒作週期，是監管與大廠同向推動。系統/資安工程師五年內不會 Rust，會像今天不會 Git。

## 動手練習

1. 把「例一」的 C++ use-after-move 在你的 WSL 跑一遍：`g++ -std=c++20 -Wall -Wextra uam.cpp -o uam && ./uam; echo $?`，確認它編過（exit 0）但跑起來 segfault（exit 139）。再把對應的 Rust 版跑一遍，讀 E0382 錯誤訊息。親手感受「編過但爆」vs「編不過」的差別。
2. 把「例二」iterator invalidation 的 C++ 版寫出來跑，用 `-fsanitize=address` 編再跑，看 ASan 抓到什麼——然後想：如果那個 `if (*it == 2)` 的條件在你的測試輸入下永遠不成立，ASan 還抓得到嗎？這就是「執行期防線」的盲點。
3. 不看筆記，用自己的話對一個 C++ 同事解釋：既然有 `unique_ptr` 了，Rust 的 ownership 多解決了什麼？（提示：關鍵詞是「強制」和「編譯期」。）

## 本章重點整理

- C/C++ 最貴的安全成本是記憶體安全 bug（MSRC/Chromium ~70%、Android 曾 76%），且靠人的紀律壓不下去——微軟壓十幾年比例沒動。
- Rust 的核心價值：把 use-after-move / iterator invalidation / data race / dangling reference 這幾類 bug，從**執行期 UB**（C++ 編得過跑起來爆）變成**編譯期錯誤**（根本生不出 binary）。
- C++ 的 smart pointer/RAII 解了「誰 free」，但不**強制**，所以那四類 bug 還在；Rust 靠 ownership + borrow + lifetime 三支柱在編譯期強制。
- 不用 GC 是刻意取捨：換來 latency 可預測 + no_std（系統/kernel 的硬需求），代價是你得學會跟 borrow checker 溝通。
- Rust 不是萬靈丹：學習曲線、編譯慢、unsafe 仍存在（安全有邊界）、生態年輕。它是「系統 + 資安」這個象限目前最好的工具，不是每個場景的最佳解。

## 自我檢核

- [ ] 面試被問「Rust 比 C++ 好在哪」，你能不能不背口號、用「編譯期 vs 執行期防線」講清楚，並舉一個具體的 C++ 編得過 Rust 編不過的例子？
- [ ] 能不能解釋為什麼 ASan/Valgrind 抓不到的記憶體 bug，borrow checker 能擋？（關鍵：檢查「這次執行」vs 檢查「所有可能執行」）
- [ ] 有人說「Java 有 GC 也記憶體安全，Rust 多此一舉」，你會怎麼反駁？（關鍵詞：latency、no_std、runtime）
- [ ] 「Rust 是安全的」這句話的**準確**版本是什麼？safe 和 unsafe 的界線在哪？
- [ ] ownership / borrow / lifetime 三者各自對付上面哪個 bug 例子？（不看筆記能不能對上）
- [ ] 你現在手上的哪個 C/C++ 專案，最可能因為哪一類 bug 而受益於 Rust？哪個反而不適合換？

## 延伸閱讀

### 官方 / 廠商第一手數據

- **[Microsoft: We need a safer systems programming language](https://msrc.microsoft.com/blog/2019/07/we-need-a-safer-systems-programming-language/)** — MSRC（2019）
  - **這篇說什麼**：本章「~70% CVE 是記憶體安全」的原始出處，附 2006–2018 逐年圖，證明比例十幾年沒動。
  - **讀哪裡**：整篇不長，重點看那張逐年比例圖。
  - **為什麼值得讀**：這是最常被引用的那個 70% 數字的權威來源，資安面試提到記憶體安全時的標準引用。

- **[Rust in the Android platform](https://security.googleblog.com/2021/04/rust-in-android-platform.html)** 與 **[Move fast and fix things](https://security.googleblog.com/2022/12/memory-safe-languages-in-android-13.html)** — Google Security Blog（2021, 2022）
  - **這篇說什麼**：Android 導入 Rust 後記憶體安全漏洞從 76% 降到 35%、Rust 碼零記憶體安全漏洞的原始數據與方法。
  - **讀哪裡**：2022 那篇的「Memory Safety」統計段落。
  - **為什麼值得讀**：這是「Rust 真的降低了大規模 codebase 漏洞」的最強實證，不是實驗室數字是產品線數字。

- **[Chromium Memory safety](https://www.chromium.org/Home/chromium-security/memory-safety/)** — Chromium 專案
  - **讀哪裡**：開頭「around 70% ... half of those are use-after-free」那段，以及他們列的三條應對路線。
  - **能學到什麼**：本章 Chromium 70% / 一半是 UAF 的來源，以及一個大 C++ 專案面對記憶體安全的真實選項空間（讓 C++ 更安全 vs 換語言）。

### 論文

- **[RustBelt: Securing the Foundations of the Rust Programming Language](https://plv.mpi-sws.org/rustbelt/popl18/)** — Jung, Jourdan, Krebbers, Dreyer（POPL 2018）
  - **核心貢獻**：用 Coq 形式化證明 Rust 型別系統加一部分標準庫 `unsafe` 是 sound——回答「憑什麼相信 Rust 的安全保證」。
  - **讀哪裡**：Section 1–2 建立問題與直覺就夠；後面 Iris 邏輯的形式化很硬，非做研究可跳。
  - **和本章的關聯**：本章說「Rust 的安全不是紀律是證明」，這篇是那個證明。前提：讀得動 PL 形式化記號者才進 Section 3 以後。

### 書籍

- **《Rust for Rustaceans》** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust 最佳單本，和本課定位幾乎重合；本章的三支柱、unsafe 邊界它都有深度章節。
  - **讀哪幾章**：讀完本課 Part 1 後回頭看它的 Chapter 1–2（記憶體與 ownership），對照著讀收穫最大。

- **《The Rustonomicon》** — 官方（doc.rust-lang.org/nomicon）
  - **這本書的定位**：unsafe Rust 權威文件，本章「安全有邊界」那條線的深水區。
  - **讀哪幾章**：現在別碰，等本課 Part 3；先知道它存在、知道 Rust 的 unsafe 有一整本書在講，就夠了。

三支柱的全景看過了，下一章開始拆第一根柱子——ownership 與 move 語意。你會看到 Rust 怎麼把你熟的 C++ move 語意，從「約定」變成「編譯器強制的預設」，以及 drop 到底在哪一行發生。

→ [Ch 02 Ownership 與 move 語意](./02-ownership-move.md)
