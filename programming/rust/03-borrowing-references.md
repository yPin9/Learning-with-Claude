# Ch 03 — Borrowing：& / &mut 與別名規則

> **目標**：把借用（borrowing）當成「不轉移所有權的引用」來理解；記牢並能真跑驗證 Rust 的核心鐵律 **aliasing XOR mutability**（別名與可變性二選一）；看懂 `E0502`/`E0499`，並理解這條規則正是把 C 裡的 data race 與 iterator invalidation 在編譯期擋掉的關鍵。

> **環境**：Rust 範例以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）跑過。C++ 對照用 `g++ -std=c++20 -fsanitize=address`。

## 為什麼需要這個？

上一章結尾那個 `consume(s: String)` 的例子留了個尾巴：只要把值傳進函式，所有權就走了，函式結束值就被 drop。可是你常常**只想讀一下、或改一下**，不想把東西送出去再要回來。上一章那種「傳進去、回傳出來」的所有權往返（`fn tag(mut s: String) -> String`）能動，但寫起來很蠢——每個只想看一眼參數的函式都得把整個值搬進搬出。

C 的答案是指標：`size_t len(const char *s)` 傳一根指標，函式讀完就走，誰也沒轉移「擁有權」的概念（C 根本沒有這概念）。這很方便，但也正是 C 一整類 bug 的根源：指標可以**任意別名**（多個指標指同一塊）+ **任意改**（任何一根都能寫），於是你有了 data race、iterator invalidation、透過別名意外改壞資料。C 對這些**零防護**。

Rust 的借用給你 C 指標的便利（不轉移所有權、執行期就是一根指標），但加上一條編譯期強制的鐵律，把那一整類 bug 擋在編譯階段。這章講的就是這條鐵律。

## 先建立直覺

把所有權想成「這本書是你的」。**借用**就是「把書借給別人看，書還是你的」。借用分兩種，對應圖書館的兩種借閱規則：

```
   共享借用 &T （不可變）           獨佔借用 &mut T （可變）

   ┌─────────────────────┐        ┌─────────────────────┐
   │  一本書，開放閱覽    │        │  一本書，帶回家改    │
   │  可以很多人同時圍看  │        │  同一時間只能一人    │
   │  但誰都不准在上面寫  │        │  他能寫，其他人碰不到│
   └─────────────────────┘        └─────────────────────┘

   規則（aliasing XOR mutability）：
     要嘛「多個唯讀觀看者」，要嘛「恰一個可寫的獨佔者」。
     這兩種狀態不能同時存在。
```

一句話記住這條鐵律：**「共享不可變，可變不共享」**（shared-XOR-mutable）。任一時刻，一個值要嘛被多個 `&T`（共享、唯讀）借著，要嘛被恰好一個 `&mut T`（獨佔、可寫）借著，**兩者不能並存**。

為什麼是這條規則？因為「多人同時看 + 有人同時改」正是 data race 的定義，也是 iterator invalidation 的成因。如果沒有人在改，多人同時讀完全安全（`&T` 隨便幾個都行）；如果有人在改，就必須保證沒有別人同時在看（`&mut T` 必須獨佔）。Rust 把這個安全條件變成型別規則。

> 借用**不會**觸發上一章的 move。`&s` 不轉移所有權，`s` 仍歸原主，借用結束後 `s` 照常能用、照常在 scope 結束時 drop。這是借用相對於「傳值再回傳」的全部意義。

## 共享借用 `&T`：借來讀，不搬走

把上一章那個「傳值就 move 走」的問題，用借用解掉：

```rust
fn len(s: &String) -> usize {
    s.len()                  // 借用讀取，不取得所有權
}
fn main() {
    let s = String::from("borrow me");
    let n = len(&s);         // 傳 &s，s 仍歸 main 所有
    println!("{} has len {}", s, n);   // s 還能用
}
```

真跑：

```
borrow me has len 9
```

`len` 收的是 `&String`（一個借用），不是 `String`（所有權）。呼叫端傳 `&s`，`s` 沒被 move，所以第 7 行 `println!` 還能用 `s`。對照上一章：如果 `len` 收 `String`，這裡就會 `E0382`。這正是編譯器在上一章那個 `note` 裡建議的「改成 borrow」。

多個共享借用可以同時存在，這是 `&T` 的重點——**唯讀就隨便共享**：

```rust
fn main() {
    let s = String::from("shared");
    let r1 = &s;
    let r2 = &s;
    let r3 = &s;             // 多個 &T 同時存在，合法
    println!("{} {} {}", r1, r2, r3);
}
```

真跑：

```
shared shared shared
```

三個 `&s` 並存完全合法。因為沒有任何人能透過 `&T` 去改 `s`，多少個觀看者都不會互相干擾——這對應圖書館「開放閱覽」那格。

## 獨佔借用 `&mut T`：借來改，且只能一人

要透過借用去**改**值，需要 `&mut T`，而且被借的變數本身得是 `mut`：

```rust
fn push_bang(s: &mut String) {
    s.push_str("!");
}
fn main() {
    let mut s = String::from("hi");
    push_bang(&mut s);
    push_bang(&mut s);
    println!("{}", s);
}
```

真跑：

```
hi!!
```

`&mut s` 借出一個獨佔可寫的引用，`push_bang` 透過它改 `s`。注意兩次 `push_bang(&mut s)` 是**分別**借、分別還——第一次呼叫結束借用就還了，第二次才重新借。任一瞬間都只有一個 `&mut s` 活著，符合鐵律。

反過來，透過 `&T`（共享借用）改值是硬錯——這是「共享即唯讀」的直接後果：

```rust
fn main() {
    let v = vec![1, 2, 3];   // 注意：沒有 mut
    let r = &v;              // 共享借用
    r.push(4);               // 想透過 &T 改：非法
}
```

真跑：

```
error[E0596]: cannot borrow `*r` as mutable, as it is behind a `&` reference
 --> b9.rs:4:5
  |
4 |     r.push(4);               // 想透過 &T 改：非法
  |     ^ `r` is a `&` reference, so it cannot be borrowed as mutable
  |
help: consider changing this to be a mutable reference
  |
3 |     let r = &mut v;              // 共享借用
  |              +++
```

`E0596`：`r` 是 `&` 引用，不能拿來當可變借用。這對應 C++ 的 `const T&`——但 C++ 的 `const` 可以被 `const_cast` 掉、也可能因為別的非 const 別名而被繞過，Rust 的 `&T` 沒有這種後門（除非進 `unsafe`）。要能改，被借的變數本身得是 `mut`，且借用得是 `&mut`（`help` 提示的 `&mut v`——不過那還需要 `let mut v`，編譯器只提示了一半）。

## 核心鐵律：aliasing XOR mutability（真跑驗證）

現在故意違反鐵律，看編譯器怎麼擋。**情況一：`&T` 還活著時想拿 `&mut`**（共享與可變並存）：

```rust
fn main() {
    let mut s = String::from("x");
    let r = &s;              // 共享借用
    s.push_str("y");         // 需要 &mut，但 r 還活著
    println!("{}", r);       // r 在這裡才最後一次用到
}
```

`push_str` 需要 `&mut s`，但此時 `r`（一個 `&s`）還活著（下一行才用它）。真跑：

```
error[E0502]: cannot borrow `s` as mutable because it is also borrowed as immutable
 --> b4.rs:4:5
  |
3 |     let r = &s;              // 共享借用
  |             -- immutable borrow occurs here
4 |     s.push_str("y");         // 需要 &mut，但 r 還活著
  |     ^^^^^^^^^^^^^^^ mutable borrow occurs here
5 |     println!("{}", r);       // r 在這裡才最後一次用到
  |                    - immutable borrow later used here
```

`E0502` 三行標記把衝突講死：`r` 是不可變借用（第 3 行）、`push_str` 要可變借用（第 4 行）、而 `r` 在第 5 行「之後還要用」——所以第 4 行的可變借用非法。這就是「有人在看（`&T`）時不准有人改（`&mut T`）」。

**情況二：兩個 `&mut` 並存**（可變不獨佔）：

```rust
fn main() {
    let mut s = String::from("x");
    let m1 = &mut s;
    let m2 = &mut s;         // 第二個 &mut，非法
    println!("{} {}", m1, m2);
}
```

真跑：

```
error[E0499]: cannot borrow `s` as mutable more than once at a time
 --> b5.rs:4:14
  |
3 |     let m1 = &mut s;
  |              ------ first mutable borrow occurs here
4 |     let m2 = &mut s;         // 第二個 &mut，非法
  |              ^^^^^^ second mutable borrow occurs here
5 |     println!("{} {}", m1, m2);
  |                       -- first borrow later used here
```

`E0499`：`s` 不能同時被借為 mutable 超過一次。這對應圖書館「帶回家改」那格——同一時間只能一人。

兩條錯誤合起來就是鐵律：**要嘛多個 `&T`（不觸發，因為都是唯讀），要嘛恰一個 `&mut T`（多一個就 `E0499`，混一個 `&T` 就 `E0502`）**。

## 對照 C：iterator invalidation 這類災難怎麼發生的

現在把鐵律的價值講清楚。C/C++ 的指標可以任意別名 + 任意改，這正是 **iterator invalidation（迭代器失效）** 的根源。你邊走訪一個 `vector` 邊往裡 `push_back`，`push_back` 可能觸發 realloc，把整塊緩衝區搬到新位址，你手上的迭代器（本質是指向舊緩衝區的指標）瞬間變懸空。C++ 對此**零編譯期防護**：

```cpp
#include <vector>
#include <cstdio>
int main() {
    std::vector<int> v = {1, 2, 3};
    for (auto it = v.begin(); it != v.end(); ++it) {
        if (*it == 2) {
            v.push_back(99);   // 可能 realloc，it 立即失效
        }
        printf("%d\n", *it);   // *it 現在是懸空迭代器
    }
    return 0;
}
```

編譯完全過。用 AddressSanitizer 跑（`g++ -O2 -fsanitize=address`），真實輸出（節錄）：

```
=================================================================
==227713==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000014 ...
READ of size 4 at 0x502000000014 thread T0
    #0 ... in main (/tmp/rustch/iv+0x1a3a)
...
freed by thread T0 here:
    #1 ... in std::vector<int>::_M_realloc_insert<int>(...)
previously allocated by thread T0 here:
    #1 ... in main
SUMMARY: AddressSanitizer: heap-use-after-free ...
```

`push_back` 觸發 realloc，`_M_realloc_insert` 釋放了舊緩衝區，接著 `*it` 讀那塊已釋放的記憶體——heap-use-after-free。這在沒開 ASan 的 release build 裡就是隨機的資料損毀或崩潰，極難查。**根因是 `it` 和 `v.push_back` 同時存在：一個在讀舊緩衝區、一個在改（重配）緩衝區。別名 + 可變並存。**

同樣的意圖在 Rust 直接編不過：

```rust
fn main() {
    let mut v = vec![1, 2, 3];
    for x in &v {            // 借出 &v 來走訪
        if *x == 2 {
            v.push(99);      // 走訪中改動容器：需要 &mut v
        }
    }
    println!("{:?}", v);
}
```

真跑：

```
error[E0502]: cannot borrow `v` as mutable because it is also borrowed as immutable
 --> b6.rs:5:13
  |
3 |     for x in &v {            // 借出 &v 來走訪
  |              --
  |              |
  |              immutable borrow occurs here
  |              immutable borrow later used here
4 |         if *x == 2 {
5 |             v.push(99);      // 走訪中改動容器：需要 &mut v
  |             ^^^^^^^^^^ mutable borrow occurs here
```

`for x in &v` 借出一個 `&v`（不可變），整個迴圈期間都活著；`v.push(99)` 需要 `&mut v`。共享借用還沒還就想拿獨佔借用——`E0502`。**C++ 的 heap-use-after-free 在 Rust 是一個編譯錯誤**。同一個 bug，C++ 給你一個 ASan 才抓得到的 runtime 災難，Rust 給你一個編譯期紅字。這就是借用鐵律的全部價值。

（想真的在 Rust 邊走訪邊改？有安全的做法：收集要改的索引再統一改、用 `retain`、用 `Vec::drain`、或改走 index-based 迴圈——但編譯器強迫你**顯式**處理這個危險，而不是讓它靜靜地變成 UB。）

## 底層機制：借用執行期是什麼？

關鍵事實：**`&T` 和 `&mut T` 在執行期就是一根指標，零額外成本。** 借用檢查（borrow check）**完全發生在編譯期**，跑起來的機器碼裡沒有任何「借用計數器」或「檢查」——這和 C++ 的 `shared_ptr`（有 atomic 引用計數）或帶執行期檢查的 iterator debug mode 完全不同。

驗證尺寸（引用就是指標寬度）：上一章我們跑過 `size_of::<&str>() == 16`（胖指標，帶 len），而 `&i32` 這種對 sized 型別的引用就是一個 word（8 bytes on x86-64）。`&T` 在組語層面就是一個位址，解參考 `*r` 就是一條 load/store。

那 borrow checker 憑什麼保證安全？它在編譯期對每個借用算出一段**存活區間**（lifetime，下一章正題），然後檢查這些區間有沒有違反鐵律地重疊。它追蹤的是「這個 `&T` 從哪行借出、到哪行最後一次被用」。

這裡要點名一個 2018 年的重大改進：**NLL（Non-Lexical Lifetimes，非詞法生命週期）**。舊版 borrow checker 認為借用活到「它所在的 `{}` block 結束」（詞法範圍）；NLL 改成借用只活到「它**最後一次被使用**」為止。看這個例子——它在 NLL 下合法：

```rust
fn main() {
    let mut s = String::from("x");
    let r = &s;
    println!("{}", r);       // r 最後一次使用在這
    s.push_str("y");         // 之後才改 s：NLL 讓這合法
    println!("{}", s);
}
```

真跑：

```
x
xy
```

`r` 在第 4 行最後一次被用，NLL 判定它的借用到那行就結束了；第 5 行 `s.push_str` 拿 `&mut s` 時，`r` 的借用已經還了，不衝突。如果是舊的詞法規則，`r` 會活到 `main` 結束，這段就會像上面的 `E0502` 一樣被擋。NLL 讓借用檢查貼近你的直覺（「用完就還」），大幅減少「明明沒問題卻編不過」的挫折。這套機制的內部（NLL 與後繼的 Polonius）留到 [Ch 07 borrow checker 底層](./07-borrow-checker-internals.md) 深挖。

## 對比與取捨

| 面向 | C 裸指標 | C++ 引用/迭代器 | Rust `&T` / `&mut T` |
|---|---|---|---|
| 別名（aliasing） | 任意 | 任意 | `&T` 可多重；`&mut T` 獨佔 |
| 透過它改值 | 任意指標都能改 | 非 const 引用能改 | 只有 `&mut T` 能改 |
| aliasing + mutation 並存 | 允許（UB 溫床） | 允許（iterator invalidation） | 編譯期禁止（`E0502`/`E0499`） |
| data race 防護 | 無 | 無 | 編譯期（配合 `Send`/`Sync`，Ch 23） |
| 執行期成本 | 一根指標 | 一根指標 | 一根指標（借用檢查純編譯期） |
| 檢查時機 | 無 | 部分 debug iterator 有 runtime 檢查 | 全部編譯期 |

取捨：Rust 用「編譯期禁止 aliasing+mutation 並存」換來一整類 bug 的消失（iterator invalidation、透過別名意外改壞、單執行緒下的資料競態雛形），代價是有些「其實安全但編譯器證不出來」的模式要改寫，或用內部可變性（interior mutability，`RefCell`/`Cell`，Ch 16）把檢查移到執行期。這條規則也是 Rust 能做 `noalias`-based 最佳化的基礎——編譯器知道 `&mut T` 絕無別名，可以放心做在 C 裡因為指標可能別名而不敢做的優化。

## 踩雷集錦

1. **以為 `&mut` 的「mut」是指「引用本身可變」**：不是。`&mut T` 是「透過這個引用可以改動被指的 `T`」，也就是**獨佔可寫借用**。引用本身指向哪（rebind）是另一回事（那要 `let mut r = &mut x;`）。初學最常把「借用是可變的」和「我能改被借的東西」搞混。`&mut T` = 我能改 `T`，且此刻只有我能碰 `T`。

2. **以為多個 `&mut` 只是「風格不好」**：不是風格問題，是 `E0499` 硬錯。兩個 `&mut` 並存意味著兩條路徑能同時寫同一塊記憶體——這在多執行緒下就是 data race，在單執行緒下就是你自己踩自己。Rust 把它當硬錯，因為它是一整類 bug 的根。

3. **被 NLL「用完就還」反過來咬**：NLL 讓借用提早結束，多數時候是幫你。但當你**期待**借用活久一點（例如某個 `&mut` 你以為還在，其實編譯器認為它上一行就還了），錯誤訊息可能反直覺。解法：讀懂 `first borrow later used here` / `borrow later used here` 這行——它指出「借用被判定活到哪」，順著它想。

4. **iterator invalidation 的 Rust 版變形**：`for x in &v { v.push(...) }` 會被擋（`E0502`）。但新手常改成 `for i in 0..v.len() { v.push(...) }` 想繞過——這在 Rust 裡**能編過**（因為沒有借用衝突，`0..v.len()` 先算好了），但你得自己確認邏輯正確（`v.len()` 在迴圈裡變了，可能無限成長）。編譯器擋的是記憶體不安全，不是邏輯錯誤；index-based 迴圈把責任還給你，要想清楚。

5. **把「借用」和「clone」當同一種手段**：借用是零成本地「看/改一下」，clone 是付深拷貝成本得到獨立所有權。新手遇到借用衝突常直接 `.clone()` 硬繞——有時對（真的需要獨立副本），但很多時候正解是調整借用的**存活區間**（早點還、拆 scope），不是複製資料。先問「我是不是只要借用範圍縮小就好」，再考慮 clone。

## 進階：再往深一層

**`&mut` 的 reborrow（再借用）**。當你把一個 `&mut T` 傳進吃 `&mut T` 的函式，其實編譯器做的是 **reborrow**：從你的 `&mut` 再借出一個較短命的 `&mut`，函式用完還你，你原本的 `&mut` 才繼續有效。看這個——`r` 是一個 `&mut`，傳進 `bump` 兩次，回來還能用：

```rust
fn bump(n: &mut i32) { *n += 1; }
fn main() {
    let mut x = 10;
    let r = &mut x;          // 一個 &mut
    bump(r);                 // reborrow 傳進去，用完還
    bump(r);                 // r 仍可用，因為上面是 reborrow 不是 move
    println!("{}", r);
}
```

真跑：

```
12
```

如果 `bump(r)` 是 move（把 `r` 的所有權交出去），第二次 `bump(r)` 就會像上一章的 `E0382` 一樣報 use-after-move。但它是 reborrow：從 `r` 借出一個短命的 `&mut *r`，`bump` 用完立刻還，`r` 回到可用。這就是為什麼 `push_bang(&mut s)` 呼叫兩次不會 `E0499`——每次都是一個用完即還的 reborrow，不是兩個並存的 `&mut`。也因此你能寫 `fn f(r: &mut T) { g(r); r.foo(); }`（`g(r)` 隱式 reborrow，回來 `r` 還能用），不必手寫 `g(&mut *r)`。`&T` 是 `Copy`（複製一根唯讀指標永遠安全），但 `&mut T` **不是** `Copy`（複製它就違反獨佔）——reborrow 正是編譯器在「不能複製 `&mut`」的限制下，讓你還能把它借給函式的機制。

**借用規則是 `noalias` 最佳化的來源**。因為 `&mut T` 保證無別名，Rust 可以對它下 LLVM 的 `noalias` 屬性（相當於 C 的 `restrict`，但編譯器強制而非靠你保證）。這讓編譯器能做「這個值在這兩行之間不會被別的指標改動」的假設，做更激進的暫存/重排。歷史上這裡踩過雷：早年 rustc 開 `noalias` 曾觸發 LLVM 的 miscompile bug，一度關掉，後來 LLVM 修好才重開。想深究可查 rustc issue tracker 上的 `noalias` 相關討論。

**面試常問**：「Rust 怎麼在編譯期防 data race？」答題骨架：借用鐵律（aliasing XOR mutability）保證「有人寫時無人讀寫」是單執行緒層面的地基；跨執行緒再加 `Send`/`Sync`（Ch 23）把「能不能安全跨執行緒共享/傳遞」也編進型別。兩者合起來，「同時有讀寫別名」在安全 Rust 裡無法表達，而 data race 的定義正是「兩個執行緒並發存取同一記憶體、至少一個是寫、且無同步」——第一個條件就被鐵律鏟掉了。

## 動手練習

1. 把本章 `E0502` 的例子（`let r = &s; s.push_str("y"); println!("{}", r);`）改成能編過的三個版本：(a) 把 `println!("{}", r)` 刪掉（讓 `r` 提早沒用到，靠 NLL）；(b) 把用 `r` 的那行搬到 `push_str` 之前；(c) 完全不借 `r`。跑一遍，體會 NLL 對「借用活到哪」的判定。

2. 重現 C++ iterator invalidation：把本章那段 C++ 用 `g++ -fsanitize=address` 編了跑，看 ASan 報告；再把等價的 Rust 版跑一次看 `E0502`。並排感受「同一個 bug、runtime 災難 vs 編譯錯誤」。

3. 寫一個函式簽章 `fn swap_first_two(v: &mut Vec<i32>)`，把前兩個元素對調。想清楚為什麼它需要 `&mut Vec<i32>` 而不是 `&Vec<i32>`，把後者寫出來看它報什麼錯（提示：`E0596`，改一個透過 `&T` 借來的值）。

## 本章重點整理

- 借用 = 不轉移所有權的引用；`&s` 不 move `s`，借完 `s` 照樣能用。執行期就是一根指標，零成本。
- 核心鐵律 **aliasing XOR mutability**：任一時刻要嘛多個 `&T`（唯讀共享），要嘛恰一個 `&mut T`（可寫獨佔），不能並存。違反就是 `E0502`（讀寫混）或 `E0499`（多個可寫）。
- 這條鐵律把 C/C++ 的一整類 bug（iterator invalidation、透過別名意外改壞、data race 雛形）從 runtime 災難變成編譯錯誤——同一個邊走訪邊 push 的意圖，C++ 給你 heap-use-after-free，Rust 給你 `E0502`。
- borrow check 純編譯期；NLL 讓借用只活到「最後一次使用」，貼近直覺。內部機制在 Ch 7。

## 自我檢核

- [ ] 不看筆記，能用「圖書館兩種借閱規則」講出 aliasing XOR mutability，並說出對應的兩個錯誤碼各是什麼情況。
- [ ] 能解釋為什麼「邊走訪 `vector` 邊 `push_back`」在 C++ 是 UB、在 Rust 是編譯錯誤，並指出根因是 aliasing + mutation 並存。
- [ ] 知道借用執行期是一根指標、借用檢查純編譯期，不像 `shared_ptr` 有 runtime 計數成本。
- [ ] 能說出 NLL 是什麼、它讓哪類「其實安全卻編不過」的程式碼變成合法。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 4.2「References and Borrowing」** — （[doc.rust-lang.org/book/ch04-02-references-and-borrowing.html](https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html)）
  - **讀哪裡**：整節，特別是「Mutable References」與「Dangling References」兩小節。
  - **學到什麼**：本章的官方對應，附相同的借用圖解；The Book 也用 dangling reference 帶出下一章的 lifetime，和本課章節順序一致。
  - **前提**：懂上一章的所有權/move。

- **《The Rust Reference》「Behavior considered undefined」** — （[doc.rust-lang.org/reference/behavior-considered-undefined.html](https://doc.rust-lang.org/reference/behavior-considered-undefined.html)）
  - **讀哪裡**：UB 清單裡關於「mutable reference 的別名」與「透過共享引用改值」那幾條。
  - **學到什麼**：aliasing XOR mutability 在語言規範層面的精確表述，以及違反它（透過 `unsafe` 繞過借用檢查）為什麼是 UB——這是你之後寫 unsafe（Part 3）必須守住的底線。
  - **前提**：懂本章借用規則；這頁是規範文件，措辭精簡。

### 技術文章

- **「The Problem With Single-threaded Shared Mutability」** — Manish Goregaokar（[manishearth.github.io/blog/2015/05/17/the-problem-with-shared-mutability/](https://manishearth.github.io/blog/2015/05/17/the-problem-with-shared-mutability/)）
  - **這篇說什麼**：論證「共享 + 可變」即使在單執行緒也危險（iterator invalidation 就是單執行緒的例子），正是 aliasing XOR mutability 的動機說明。
  - **讀哪裡**：整篇，尤其開頭用 `Vec` iterator invalidation 舉例的部分——和本章 C++ 範例同一個 bug。
  - **為什麼值得讀**：作者是 rustc/Servo 核心貢獻者；這是解釋「為什麼 Rust 要這條規則」最清楚的一篇。

### 深入設計文件

- **NLL RFC 2094（Non-Lexical Lifetimes）** — （[rust-lang.github.io/rfcs/2094-nll.html](https://rust-lang.github.io/rfcs/2094-nll.html)）
  - **讀哪裡**：開頭的「Motivation」與「Problem case #1」——用具體例子說明舊詞法借用檢查在哪些地方過度嚴格。
  - **學到什麼**：本章 NLL 那段的第一手來源，理解「借用活到最後一次使用」是怎麼設計出來的、解決了哪些真實痛點。
  - **前提**：懂本章借用規則；RFC 較長，只讀 Motivation 段即可，後面演算法細節留給 Ch 7。

下一章我們把「借用能活多久」這件事講清楚——這就是 lifetime。本章 `dangle`/懸空引用被 `E0106` 擋下的機制、以及 NLL 算的那個「存活區間」，正是 lifetime 的正題。

→ [Ch 04 Lifetimes：借用的存活期](./04-lifetimes.md)
