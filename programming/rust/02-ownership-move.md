# Ch 02 — Ownership 與 move 語意

> **目標**：把 Rust 的所有權（ownership）模型接到你既有的 C++ RAII / move 直覺上，看懂「誰負責 drop」這條主線；能真跑並讀懂 use-after-move 的 `E0382`，理解為什麼 Rust 的 move 讓 double-free 從一類 bug 變成不可能發生的事。

> **環境**：本章所有 Rust 範例以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）跑過，輸出照貼。C/C++ 對照用 `g++ -std=c++20` 與 `gcc`，含 AddressSanitizer。

## 為什麼需要這個？

你在 C 裡管記憶體，靠的是紀律：誰 `malloc` 誰 `free`、`free` 完把指標設 `NULL`、不要 `free` 兩次、不要在 `free` 之後還讀它。C++ 用 RAII 把這套紀律綁進型別：`std::unique_ptr` 的解構子替你 `delete`，`std::vector` 的解構子替你釋放緩衝區。RAII 是個巨大的進步——但它把「一個資源只能被釋放一次」這件事交給程式設計師用 move 語意去維持，而 move 語意在 C++ 裡是**約定**，不是**強制**。

看這段完全合法、能編譯、能跑的 C++：

```cpp
#include <string>
#include <utility>
#include <iostream>
int main() {
    std::string a = "hello";
    std::string b = std::move(a);   // a 進入 valid-but-unspecified 狀態
    std::cout << "b = " << b << "\n";
    std::cout << "a.size() after move = " << a.size() << "\n";
    a = "reused";
    std::cout << "a reused = " << a << "\n";
    return 0;
}
```

真跑（`g++ -O2 -std=c++20`）：

```
b = hello
a.size() after move = 0
a reused = reused
```

`std::move(a)` 之後，C++ 標準說 `a` 處於 **valid but unspecified state**（有效但未指定）——你可以析構它、可以重新賦值，但**讀它的值是合法的、只是無意義的**。編譯器不會擋你 `a.size()`，這裡剛好回 0，但標準不保證。真實世界裡 use-after-move 的 bug 就是這樣潛伏的：你 move 走了一個 `unique_ptr`，後面某條路徑又解參考它，編譯器一聲不吭。

Rust 把「約定」升級成「編譯期強制」。move 之後來源直接**在型別系統層面失效**——不是清成某個哨兵值，是編譯器拒絕再讓你碰它。這章就是講這個機制怎麼運作，以及它為什麼順帶讓 double-free 消失。

## 先建立直覺

把每個值想成一張「所有權門票」。**任一時刻，一個值恰好有一位持票人**，持票人負責在門票作廢（值離開 scope）時清理它。門票可以轉手（move），但轉手後**原持票人手上就空了**——它不能再進場。

```
   let a = String::from("hi");

        a ─────────► [ 堆積: "hi" ]          a 持有門票
                     ptr|len|cap

   let b = a;   // move

        a ──╳                                a 的門票被撕走，失效
        b ─────────► [ 堆積: "hi" ]          b 現在是唯一持票人
                     ptr|len|cap

   離開 scope 時，只有 b 會去 free 那塊堆積。
   a 不會，因為 a 已經沒票了 → 不可能 double-free。
```

C++ 的 move 是「把門票影本給 b，順便把 a 的門票劃掉（把來源清成空殼）」——清空這一步是 `std::string` 的 move 建構子**自己寫的**，忘了寫就會 double-free。Rust 的 move 是「編譯器記帳：a 這條線從此標記為 moved-out，任何再用 a 的地方直接編譯錯誤」——**沒有執行期的清空動作**，帳記在編譯器腦子裡。

## 三條所有權規則

The Book 把所有權濃縮成三條，背下來：

1. **每個值都有一個變數作為它的擁有者（owner）。**
2. **任一時刻，一個值只能有一個擁有者。**
3. **當擁有者離開 scope，值就被 drop（釋放）。**

這三條就是上面那張門票圖的文字版。接下來每一節都是這三條的推論。

## move：`let b = a` 之後 a 不能用

最小範例，故意觸發錯誤：

```rust
fn main() {
    let a = String::from("hello");
    let b = a;              // move: a 的所有權轉給 b
    println!("{}", a);      // 用了已被 move 的 a
}
```

`rustc uam.rs` 真跑，照貼：

```
error[E0382]: borrow of moved value: `a`
 --> uam.rs:4:20
  |
2 |     let a = String::from("hello");
  |         - move occurs because `a` has type `String`, which does not implement the `Copy` trait
3 |     let b = a;              // move: a 的所有權轉給 b
  |             - value moved here
4 |     println!("{}", a);      // 用了已被 move 的 a
  |                    ^ value borrowed here after move
  |
help: consider cloning the value if the performance cost is acceptable
  |
3 |     let b = a.clone();              // move: a 的所有權轉給 b
  |              ++++++++
```

`E0382` 是你這章會反覆見到的老朋友。讀懂它的三行標記：

- `move occurs because String ... does not implement the Copy trait`——為什麼是 move 不是 copy？因為 `String` 不是 `Copy`（下面會講哪些型別是 `Copy`）。
- `value moved here`——所有權在 `let b = a` 這行離開 `a`。
- `value borrowed here after move`——你在 `a` 已經空了之後又碰它，這就是 use-after-move。

對照 C++：同樣的 `std::string b = std::move(a); std::cout << a;` **編得過、跑得動**，只是印出未指定的值。Rust 直接在編譯期把這條路封死。這就是「約定 vs 強制」的差別，一個 `E0382` 抵得上你在 C++ code review 裡瞪半天的注意力。

> `rustc` 貼心地建議 `a.clone()`。`clone()` 是深拷貝（deep copy）——另外配一塊堆積、把內容複製過去，於是 `a` 和 `b` 各自擁有一份、各自持票。要不要 clone 是效能決策，不是為了討好編譯器亂加。下面「Copy vs Move」會細講。

### move 進函式、回傳所有權

所有權轉移不限於 `let`。把值傳進函式，所有權就進去了；函式參數在函式結束時 drop：

```rust
fn consume(s: String) {
    println!("consumed: {}", s);
} // s 在此 drop

fn main() {
    let s = String::from("owned");
    consume(s);              // s 的所有權移進 consume
    println!("{}", s);       // 錯：s 已被 move
}
```

真跑：

```
error[E0382]: borrow of moved value: `s`
 --> movefn.rs:8:20
  |
6 |     let s = String::from("owned");
  |         - move occurs because `s` has type `String`, which does not implement the `Copy` trait
7 |     consume(s);              // s 的所有權移進 consume
  |             - value moved here
8 |     println!("{}", s);       // 錯：s 已被 move
  |                    ^ value borrowed here after move
  |
note: consider changing this parameter type in function `consume` to borrow instead if owning the value isn't necessary
 --> movefn.rs:1:15
  |
1 | fn consume(s: String) {
  |    -------    ^^^^^^ this parameter takes ownership of the value
```

注意那個 `note`：編譯器提示你「如果不需要拿走所有權，改成借用（borrow）」——這正是下一章 `&String` 的動機。現在先感受「傳值 = 交出所有權」這件事。

要把值用完還給呼叫者，就把它回傳出去。所有權可以流出函式：

```rust
fn make() -> String {
    let s = String::from("built");
    s                        // 把所有權交出去（沒有 return 也行，尾運算式即回傳值）
}
fn tag(mut s: String) -> String {
    s.push_str("!!");
    s                        // 交還
}
fn main() {
    let a = make();
    let b = tag(a);          // a move 進去，回傳 move 回 b
    println!("{}", b);
}
```

真跑輸出：

```
built!!
```

`make` 造了一個 `String`，把所有權交給 `main` 的 `a`；`tag` 收下 `a`（`a` 失效）、改它、再把所有權交還給 `b`。整條鏈上任何時刻都恰好一位持票人。這就是規則二在函式邊界上的展現。

## drop：離開 scope 自動釋放

規則三說「擁有者離開 scope，值就被 drop」。這對應 C++ 的解構子，但觸發點更明確：**變數離開它的 lexical scope 那一刻**。用一個會叫的型別看清楚順序：

```rust
struct Noisy(&'static str);
impl Drop for Noisy {
    fn drop(&mut self) {
        println!("drop {}", self.0);
    }
}
fn main() {
    let _a = Noisy("a");
    let _b = Noisy("b");
    println!("end of main");
}
```

真跑：

```
end of main
drop b
drop a
```

兩個觀察：

1. `drop` 發生在 `println!` **之後**，也就是變數真正離開 scope 時，不是提早。
2. **順序是後進先出（LIFO）**：`_b` 後宣告，先 drop。這和 C++ 的自動儲存期物件析構順序一致（反宣告順序），你的直覺可以直接搬過來。

`Drop` trait 就是 Rust 版的解構子，`fn drop(&mut self)` 就是解構子本體。`String` 自己實作了 `Drop`（去 free 堆積緩衝區），所以你不用手動釋放——這點和 C++ RAII 完全同構。

### 為什麼 double-free 在 Rust 是不可能的

現在把 move 和 drop 接起來，這是本章最重要的一段。

C++ 的困境：`std::string a; std::string b = std::move(a);` 之後，`a` 和 `b` **兩個物件都會被析構**（各自離開 scope 時）。如果 `a` 的 move 建構子沒把 `a` 內部指標清空，那兩次析構就會對同一塊堆積 `free` 兩次——double-free，heap corruption。C++ 靠 move 建構子**手動把來源設成「空狀態」**（`ptr = nullptr; len = 0`）來避免，`free(nullptr)` 是 no-op，於是只有一次真正釋放。這是**程式設計師的責任**：你自己寫的型別，move 建構子忘了清空來源，就中獎。

Rust 的解法：move 之後 `a` 被標記為 **moved-out**，編譯器**不會替 `a` 生成 drop 呼叫**。離開 scope 時只有持票人 `b` 被 drop，`a` 那條線編譯器根本沒排 drop。所以：

- 沒有「兩個物件指向同一塊堆積」的狀態存在，因為 `a` move 出去後你連碰都碰不到它。
- 沒有「忘了清空來源」的可能，因為清理帳是編譯器記的，不是你寫 move 建構子清的。

一句話：**C++ 用「執行期清空來源」避免 double-free；Rust 用「編譯期記帳，來源不再被 drop」讓 double-free 根本無法表達**。你不需要寫任何 move 建構子，這是預設行為。

## Copy vs Move：哪些型別複製、哪些型別移動

你可能已經注意到：`let a: i32 = 42; let b = a;` 之後 `a` 還能用，但 `String` 就不行。差別在 **`Copy` trait**。

```rust
fn main() {
    let a: i32 = 42;
    let b = a;              // Copy，不是 move
    println!("a={} b={}", a, b);
}
```

真跑：

```
a=42 b=42
```

`a` 沒失效。規則：**實作了 `Copy` 的型別，`let b = a` 是位元複製（copy），來源仍有效**；沒實作 `Copy` 的型別，`let b = a` 是 move，來源失效。

哪些型別是 `Copy`？判準是：**純粹存在 stack 上、且沒有 `Drop`**。具體有：

- 所有整數/浮點數（`i32`、`u64`、`f64`…）、`bool`、`char`。
- 由 `Copy` 型別組成的 tuple（`(i32, bool)`）和陣列。
- 共享引用 `&T`（複製一根指標而已，稍後細講）。

哪些**不是** `Copy`？只要它「擁有一份需要釋放的資源」就不行：`String`（擁有堆積緩衝區）、`Vec<T>`、`Box<T>`、任何實作了 `Drop` 的型別。

為什麼這樣切？因為 **`Copy` 和 `Drop` 互斥**。`Copy` 的意思是「位元複製一份就是個獨立有效的值」——如果這種型別又有 `Drop`，那複製一份就會有兩個持票人指向同一份資源，drop 兩次，回到 double-free。所以 Rust 規定：**有 `Drop` 就不能 `Copy`**。`i32` 沒有需要釋放的東西，複製 100 份也沒人要 free，安全；`String` 有堆積要 free，只能有一位持票人，所以必須 move。

自訂型別想要 `Copy`，用 `derive`，但所有欄位都得是 `Copy`：

```rust
#[derive(Copy, Clone, Debug)]
struct Point { x: i32, y: i32 }

fn main() {
    let p = Point { x: 1, y: 2 };
    let q = p;               // Copy，因為所有欄位都是 Copy
    println!("{:?} {:?}", p, q);   // p 仍可用
}
```

真跑（略去一個 dead-code warning）：

```
Point { x: 1, y: 2 } Point { x: 1, y: 2 }
```

一旦某個欄位不是 `Copy`，`derive(Copy)` 就編不過：

```rust
#[derive(Copy, Clone)]
struct Bag { name: String }
fn main() {
    let _b = Bag { name: String::from("x") };
}
```

真跑：

```
error[E0204]: the trait `Copy` cannot be implemented for this type
 --> custfail.rs:2:8
  |
1 | #[derive(Copy, Clone)]
  |          ---- in this derive macro expansion
2 | struct Bag { name: String }
  |        ^^^   ------------ this field does not implement `Copy`
```

`E0204` 精準指出是 `name: String` 這個欄位不 `Copy`，所以整個 `Bag` 不能 `Copy`。編譯器把「傳染性」講得很清楚：一顆非 `Copy` 的欄位，整個 struct 就失去 `Copy` 資格。

## 底層機制：move 到底是什麼？

這一節是靈魂。move **不是**什麼複雜操作——它就是 **一次淺拷貝（shallow copy）+ 來源標記失效**。

`String` 在 stack 上是三個 machine word：`ptr`（指向堆積緩衝區）、`len`（目前長度）、`cap`（容量）。實際尺寸驗證：

```rust
fn main() {
    println!("size_of String = {}", std::mem::size_of::<String>());
    println!("size_of i32    = {}", std::mem::size_of::<i32>());
    println!("size_of &str   = {}", std::mem::size_of::<&str>());
    println!("size_of Box<i32> = {}", std::mem::size_of::<Box<i32>>());
}
```

真跑（x86-64，word = 8 bytes）：

```
size_of String = 24
size_of i32    = 4
size_of &str   = 16
size_of Box<i32> = 8
```

`String` 是 24 bytes = 3 個 word（ptr/len/cap）。`&str` 是 16 = 2 個 word（胖指標 ptr+len，Ch 6 深談）。`Box<i32>` 是 8 = 1 個 word（單純一根指標）。

move `let b = a` 做的事：**把這 24 bytes（ptr/len/cap）從 `a` 的 stack 位置 `memcpy` 到 `b` 的 stack 位置**。堆積上那塊真正的字元資料**完全沒動**——`ptr` 這根指標被複製了，所以 `b.ptr` 和原本的 `a.ptr` 指向同一塊堆積。驗證：

```rust
fn main() {
    let a = String::from("hi");
    let pa = a.as_ptr();     // 記下堆積位址
    let b = a;               // move
    let pb = b.as_ptr();
    println!("ptr before move = {:p}", pa);
    println!("ptr after  move = {:p}", pb);
    println!("same heap buffer? {}", pa == pb);
}
```

真跑：

```
ptr before move = 0x61d6035e5d60
ptr after  move = 0x61d6035e5d60
same heap buffer? true
```

move 前後 `as_ptr()` 回傳同一個堆積位址——證明 move 沒有碰堆積，只複製了 stack 上那三個 word。

```
   move 前:                        move 後:

   a: [ ptr ]──┐                   a: [ ????  ]  ← moved-out，編譯器禁用
      [ len=2 ]│                      [ ????  ]
      [ cap=2 ]│                      [ ????  ]
              ▼                    b: [ ptr ]──┐  ← 24 bytes memcpy 過來
        堆積 "hi"                     [ len=2 ]│
                                      [ cap=2 ]│
                                              ▼
                                        堆積 "hi"   ← 同一塊，沒複製
```

所以 move 的成本是**固定的**：不管 `String` 裡裝 3 個字元還是 3GB，move 都是複製 24 bytes 的 metadata，O(1)。這和 C++ 的 move 語意目標一致（避免深拷貝），差別只在 Rust 不需要 move 建構子清空來源——那個「來源失效」是編譯器的靜態記帳，執行期沒有任何清空指令。

**ownership 的本質，就是「誰負責 drop」。** move 轉移的其實是「drop 責任」：move 之後，`b` 背上了「離開 scope 時要去 free 那塊堆積」的責任，`a` 卸下了這個責任（也失去了使用權）。整個所有權系統，說穿了是編譯器在靜態追蹤每一塊資源「現在歸誰 drop」，並保證這個「誰」永遠恰好是一個。

## 對比與取捨

| 面向 | C 手動管理 | C++ RAII + move | Rust ownership |
|---|---|---|---|
| 釋放時機 | 手動 `free` | 解構子（scope 結束） | drop（scope 結束） |
| move 後來源狀態 | N/A | valid but unspecified，**可讀** | moved-out，**編譯期禁用** |
| double-free 防護 | 靠紀律（設 NULL） | 靠 move 建構子清空來源 | 編譯期不可能發生 |
| use-after-move | 無防護（UB） | 無防護（讀到未指定值） | 編譯期 `E0382` |
| move 成本 | N/A | memcpy metadata + 清來源 | memcpy metadata（O(1)） |
| 需要寫 move 建構子嗎 | N/A | 要，寫錯就中獎 | 不用，預設正確 |

取捨很清楚：Rust 用「編譯期禁止碰 moved-out 值」換來「零成本的正確性」，代價是你偶爾要為了滿足所有權而 `clone()`（明確付出深拷貝成本）或改用借用（下一章）。C++ 給你更多彈性（move 後還能重新賦值再用），代價是那些彈性正好是 bug 的溫床。

## 踩雷集錦

1. **以為 move 是「昂貴的搬移」**：錯誤直覺是「move 一個大 `Vec` 很慢」。實際上 move 永遠是複製 stack 上的 metadata（`Vec` 也是 ptr/len/cap 三個 word），O(1)，和資料量無關。**慢的是 `clone()`**（深拷貝），不是 move。看到效能問題先確認你是不是在不必要地 `clone`。

2. **在迴圈裡 move 同一個變數**：新手常寫出這種，編譯器會抓：

   ```rust
   fn main() {
       let s = String::from("x");
       for _ in 0..3 {
           let _taken = s;      // 第二輪就沒東西可 move
       }
   }
   ```

   真跑：

   ```
   error[E0382]: use of moved value: `s`
    --> loopmove.rs:4:22
     |
   2 |     let s = String::from("x");
     |         - move occurs because `s` has type `String`, which does not implement the `Copy` trait
   3 |     for _ in 0..3 {
     |     ------------- inside of this loop
   4 |         let _taken = s;      // 第二輪就沒東西可 move
     |                      ^ value moved here, in previous iteration of loop
   ```

   訊息裡的 `in previous iteration of loop` 是關鍵：第一輪 move 走 `s`，第二輪就沒得 move 了。正解通常是借用（`&s`）或每輪 `s.clone()`。

3. **partial move（部分移動）後想用整個 struct**：你可以把 struct 的某個非 `Copy` 欄位單獨 move 出來，但那之後**不能再用被 move 走的那個欄位**，也不能把整個 struct 當一個值用：

   ```rust
   struct Pair { a: String, b: String }
   fn main() {
       let p = Pair { a: String::from("A"), b: String::from("B") };
       let x = p.a;             // 把 p.a move 出來
       println!("{}", x);
       println!("{}", p.b);     // p.b 還在，OK
       println!("{:?}", p.a);   // 但 p.a 沒了
   }
   ```

   `p.b` 那行合法，`p.a` 那行報 `E0382: borrow of moved value: p.a`。編譯器**逐欄位**追蹤所有權，不是整包，這比 C++ 精細。

4. **以為 `clone()` 是「作弊」或壞習慣**：不是。`clone()` 是你明確聲明「我要付深拷貝成本換一份獨立所有權」。它不是為了討好編譯器的黑魔法，是一個誠實的效能決策。真正的壞習慣是**在該用借用的地方 clone**（下一章），或在熱路徑無意識地 clone。該 clone 就 clone。

5. **把 `Copy` 和 `Clone` 搞混**：`Copy` 是隱式的、位元複製、由編譯器在賦值/傳參時自動做；`Clone` 是顯式的、可以是深拷貝、要你手動呼叫 `.clone()`。`Copy` 一定也是 `Clone`（所以 `#[derive(Copy, Clone)]` 一起寫），但 `Clone` 不一定 `Copy`（`String` 是 `Clone` 但不是 `Copy`）。判準：能不能「無腦位元複製就得到獨立有效值」——能就可以 `Copy`，不能（有堆積/有 `Drop`）就只能 `Clone`。

## 進階：再往深一層

**move 在 MIR 層的樣貌**。Rust 的 borrow checker 跑在 MIR（Mid-level IR）上。在 MIR 裡，`let b = a` 對 `String` 會產生一個 `_b = move _a` 的指令，並且編譯器對 `_a` 記一個 "moved" 的 dataflow 狀態；之後任何讀 `_a` 的地方，dataflow 分析發現它處於 moved 狀態，就報 `E0382`。drop 也是在 MIR 明確插入的 `drop(_b)` 指令，而 `_a` 因為被標記 moved，編譯器**不會**為它插 drop。想看的話：`rustc --emit=mir uam.rs`（把 use-after-move 那行註解掉讓它編過），在輸出裡找 `move` 和 `drop`。

**移動語意與 `Drop` 的細節：drop flag**。當一個值「有時被 move、有時沒被 move」（例如在 `if` 的一支 move 走、另一支沒有），編譯器無法靜態確定離開 scope 時該不該 drop，就會插入一個執行期的 **drop flag**（一個隱藏的 bool），在 runtime 記錄「這個值還在不在」，drop 前檢查。這是所有權系統少數會產生執行期額外狀態的情況，但成本極小（一個 stack bool）。多數情況（move 與否靜態可知）連 drop flag 都不需要。

**面試常問**：「Rust 怎麼在沒有 GC、沒有 runtime 的情況下保證記憶體安全？」答題骨架就是本章：所有權（每個值恰一個 owner）+ move（轉移 drop 責任、來源編譯期失效）+ drop（owner 離開 scope 自動釋放）三者聯手，把「一塊資源恰好被釋放一次、且釋放後不再被存取」變成型別系統的不變量，全部在編譯期驗證，執行期零額外成本（drop flag 例外，且極小）。

## 動手練習

1. 把本章 `E0382` 那個 `let b = a; println!("{}", a);` 的例子，用三種方式各修一次讓它編過：(a) 把 `println!` 改成印 `b`；(b) 在 `let b` 用 `a.clone()`；(c) 改成 `let b = &a`（借用，下一章正題）。跑一遍，比較三者的語意差異。

2. 寫一個 `struct` 有兩個 `String` 欄位，故意 partial move 掉一個，然後嘗試 (a) 用剩下那個欄位、(b) 用被 move 掉的欄位、(c) 把整個 struct 傳進一個吃 `Self` 的函式。預測每個會不會編過，再真跑驗證。

3. 對本章的 `Noisy` drop-order 範例，加一個內層 `{ }` block，在裡面宣告第三個 `Noisy("c")`，預測四個 drop 的順序再跑。體會「離開內層 scope 就立刻 drop」。

## 本章重點整理

- 所有權三規則：每個值恰一個 owner；同時只能一個 owner；owner 離開 scope 就 drop。
- move（`let b = a`、傳參、回傳）轉移的是「drop 責任」，底層是 memcpy 那幾個 word 的 metadata（O(1)），來源被編譯期標記 moved-out，再碰就 `E0382`。
- `Copy` 型別（純 stack、無 `Drop`）賦值是複製、來源仍有效；有 `Drop` 的型別（`String`/`Vec`/`Box`）只能 move，因為 `Copy` 與 `Drop` 互斥。
- Rust 的 move「不 drop 來源」讓 double-free 無法表達；C++ 靠 move 建構子手動清空來源，寫錯就中獎。ownership 的本質是編譯器靜態追蹤「誰負責 drop」。

## 自我檢核

- [ ] 不看筆記，能用「門票」直覺解釋為什麼 `let b = a;` 之後 `a` 不能用，以及這對應 C++ 的什麼、差在哪。
- [ ] 能說出為什麼 `i32` 是 `Copy` 而 `String` 不是——關鍵字是「`Copy` 與 `Drop` 互斥」。
- [ ] 面試問「Rust 沒有 GC 怎麼保證記憶體安全」，能用所有權+move+drop 三者串起來答，並解釋為什麼 double-free 不可能。
- [ ] 知道 move 的成本和資料量無關（O(1) memcpy metadata），慢的是 `clone()`；知道什麼時候該 clone、什麼時候該改用借用。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 4「Understanding Ownership」** — Steve Klabnik & Carol Nichols（[doc.rust-lang.org/book/ch04-00-understanding-ownership.html](https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html)）
  - **讀哪裡**：4.1「What Is Ownership?」全節、4.2 的「Ways Variables and Data Interact: Move / Clone」。
  - **學到什麼**：本章的官方版本，附一模一樣的 `String` move 圖解。The Book 講得更慢、更多插圖，本章講得更快且全程對照 C++——兩者互補。
  - **前提**：會基本 Rust 語法（`let`、函式）即可，這是 The Book 最前面的核心章。

- **《The Rustonomicon》「Ownership」與「Drop」相關章** — 官方（[doc.rust-lang.org/nomicon/ownership.html](https://doc.rust-lang.org/nomicon/ownership.html)）
  - **讀哪裡**：`ownership.html` 開頭談 aliasing 與 ownership 的關係；`destructors.html` / `drop-flags.html` 談 drop 順序與本章「進階」提到的 drop flag。
  - **學到什麼**：drop flag 的機制、為什麼有時需要執行期記錄一個值還在不在——本章進階段的權威來源。
  - **前提**：懂本章的 move/drop 基礎；Nomicon 假設你已經會安全 Rust，直接談底層。

### 技術文章

- **「Rust ownership, the hard way」** — Chris Morgan（[chrismorgan.info/blog/rust-ownership-the-hard-way/](https://chrismorgan.info/blog/rust-ownership-the-hard-way/)）
  - **這篇說什麼**：從 C 的手動記憶體管理一路推到 Rust 所有權，把「誰負責 free」這條線講得極清楚，和本章「ownership 本質是誰負責 drop」的論點高度重合。
  - **讀哪裡**：整篇不長，重點在中段用 C struct + 手動 free 逐步演化到 Rust 的部分。
  - **為什麼值得讀**：作者是資深 Rust 貢獻者；這是少數「從 C 視角」而非「從零視角」講所有權的文章，正對你的背景。

### C++ 對照

- **cppreference「std::move」與各容器的 moved-from state** — （[en.cppreference.com/w/cpp/utility/move](https://en.cppreference.com/w/cpp/utility/move)）
  - **讀哪裡**：`std::move` 頁的 Notes，以及各容器（如 `std::string`）「moved-from」狀態的描述。
  - **學到什麼**：C++ 標準對 valid-but-unspecified 的精確措辭——理解 Rust 為什麼要在編譯期把這個狀態直接禁掉，本章開頭那段 C++ 行為的權威依據。
  - **前提**：會 C++ move 語意基礎（右值引用、move 建構子）。

理解了「不轉移所有權、只借來看一下」的需求（本章 `consume` 那個 `note` 已經在暗示）之後，下一章進入借用（borrowing）——這是 Rust 日常寫得最多、也是別名規則（aliasing rules）真正發威的地方。

→ [Ch 03 Borrowing：& / &mut 與別名規則](./03-borrowing-references.md)
