# Ch 10 — 泛型與單型化

> **目標**：把 Rust 泛型的語法（型別參數、`where` 子句、const generics、turbofish）掌握到能寫；理解**單型化（monomorphization）**——編譯器為每個具體型別生一份特化碼，並真跑看同一泛型函式對 `i32`/`f64` 生了兩份不同的機器碼符號；對照 C++ template（連 code bloat 的缺點都一樣同源）與 C 的 `void*`/巨集泛型（丟了型別安全或除錯性）；能誠實討論靜態分派零成本 vs 動態分派（`dyn`，下章）的取捨、以及編譯變慢、binary 變大的代價。

> **環境**：Rust 以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）；符號用 `rustc --emit=obj` + `nm` + `c++filt` 觀察；C++ 對照用 `g++ 13`。本章的符號輸出、符號數量都是本機真跑，非推測。

## 為什麼需要這個？

你在 C 裡想寫一個「對任何型別都能用」的函式——例如「回傳陣列裡最大的元素」——只有兩條路，兩條都要付代價。

**第一條：`void*` + 大小/比較函式**（`qsort` 走的路）。丟掉型別安全：

```c
void swap(void *a, void *b, size_t sz) {   // 泛型 swap
    char tmp[64]; memcpy(tmp, a, sz); memcpy(a, b, sz); memcpy(b, tmp, sz);
}
int main(void) {
    double d = 3.14; int i = 9;
    swap(&d, &i, sizeof(int));   // 傳錯 size：把 int 大小套到 double
    printf("d=%f i=%d\n", d, i);
    return 0;
}
```

真跑（`gcc vp.c && ./vp`）：

```
d=3.139999 i=1374389535
```

`d` 和 `i` 都被靜默地寫壞——`swap` 用 `void*`，**型別和大小的正確性完全沒檢查**，傳錯 `sizeof` 就 corrupt 記憶體，編譯器一句話不說。

**第二條：巨集泛型**（`#define MAX(a,b)`）。丟掉除錯性：巨集是純文字替換，型別檢查形同虛設，`MAX(i++, j++)` 會把 `i++` 展開兩次；出錯時你看到的是展開後的天書，斷點打不進「那個函式」（根本沒有函式）。

C++ 的 **template** 解決了這兩個痛點：型別安全（編譯器檢查）、可除錯（是真的函式）。Rust 的**泛型**走的是和 C++ template 幾乎一樣的技術路線——**單型化**：編譯器看到你用 `i32` 和 `f64` 各呼叫一次泛型函式，就生成**兩份**專門給 `i32` 和 `f64` 的特化機器碼。這章講泛型語法，並把單型化這個「零成本抽象」的技術根源挖到能看見生成的符號。

## 先建立直覺

泛型的心智圖像：你寫的**一份**帶型別參數的原始碼，是一個「模板」；編譯器根據你實際用到的每個具體型別，**印出多份**特化的機器碼。

```
   你寫的一份原始碼                 編譯器生成的多份機器碼
   ┌──────────────────┐          ┌────────────────────────┐
   │ fn largest<T>(…)  │  ──單──▶ │ largest::<i32>(…)  ← 給 i32 特化 │
   │   （模板）         │  型化    │ largest::<f64>(…)  ← 給 f64 特化 │
   └──────────────────┘          └────────────────────────┘
        泛型 = 編譯期的程式碼工廠，不是執行期的多型
```

關鍵直覺：**泛型是編譯期機制，型別參數 `T` 在執行期根本不存在。** 執行到 `largest(&ints)` 時，CPU 跑的是 `largest::<i32>` 這份具體的碼，裡面沒有任何「檢查 T 是什麼」的執行期邏輯——因為在編譯時 `T` 就已經被替換成 `i32` 了。這和 `dyn Trait`（Ch 11）的動態分派**根本不同**：後者執行期真的查 vtable 決定呼叫誰，前者在編譯期就把一切定死。

> 如果你熟 C++：這就是 template 的實例化（instantiation）。`largest<int>` 和 `largest<double>` 在 C++ 是兩個不同的函式，Rust 的 `largest::<i32>` 和 `largest::<f64>` 一模一樣。連缺點都繼承了——兩邊都會 code bloat（下面實測）。

## 泛型語法：型別參數與 trait bound

先看最基本的泛型函式。回傳 slice 裡最大的元素，對任何「能比大小、能複製」的型別都可用：

```rust
fn largest<T: PartialOrd + Copy>(list: &[T]) -> T {
    let mut biggest = list[0];
    for &x in list {
        if x > biggest { biggest = x; }
    }
    biggest
}

fn main() {
    let ints = [3i32, 7, 2, 9, 4];
    let floats = [1.5f64, 0.2, 3.3];
    println!("{}", largest(&ints));
    println!("{}", largest(&floats));
}
```

真跑：

```
9
3.3
```

`fn largest<T: PartialOrd + Copy>` 的三部分：

- `<T>` 宣告一個型別參數 `T`（像 C++ 的 `template<typename T>`）。
- `T: PartialOrd + Copy` 是 **trait bound**（Ch 9 的 `T: Trait` 在這裡發威）：`T` 必須實作 `PartialOrd`（才能 `x > biggest` 比大小）和 `Copy`（才能 `let mut biggest = list[0]` 複製而非 move）。**沒有這些 bound，函式本體裡的 `>` 和複製就編不過**——這正是泛型的型別安全：約束寫在簽章上，編譯器保證 `T` 具備你用到的能力。
- 對比 C 的 `void*` swap：那裡沒有任何約束，傳錯 size 就 corrupt；這裡 `T: Copy` 保證複製是合法的、`PartialOrd` 保證能比較，錯誤在編譯期擋掉。

**`where` 子句**：bound 多起來時，塞在 `<>` 裡會很擠，`where` 把它們挪到後面：

```rust
use std::fmt::Debug;
use std::hash::Hash;
use std::collections::HashMap;

fn count_uniques<T>(items: &[T]) -> usize
where
    T: Eq + Hash + Clone + Debug,     // bound 挪到這裡，簽章更清爽
{
    let mut map: HashMap<T, u32> = HashMap::new();
    for it in items {
        *map.entry(it.clone()).or_insert(0) += 1;
    }
    map.len()
}

fn main() {
    println!("{}", count_uniques(&[1, 2, 2, 3, 3, 3]));
    println!("{}", count_uniques(&["a", "b", "a"]));
}
```

真跑：

```
3
2
```

`where T: Eq + Hash + Clone + Debug` 和寫成 `fn count_uniques<T: Eq + Hash + Clone + Debug>` 完全等價，只是 bound 一多、或有複雜的 `where T::Item: …` 這種關聯型別約束時，`where` 可讀性好得多。

## 泛型 struct 與 impl 區塊

不只函式，struct 和它的方法也能泛型化。這對應 C++ 的 class template（`template<typename T> class Pair`）。注意 `impl` 區塊本身也要宣告型別參數：

```rust
struct Pair<T> { a: T, b: T }

impl<T: std::fmt::Display + PartialOrd> Pair<T> {   // impl<T> 這個 <T> 不能省
    fn new(a: T, b: T) -> Self { Pair { a, b } }
    fn larger(&self) -> &T {
        if self.a >= self.b { &self.a } else { &self.b }
    }
}

fn main() {
    let p = Pair::new(3, 7);              // Pair<i32>
    println!("{}", p.larger());
    let q = Pair::new("apple", "banana"); // Pair<&str>
    println!("{}", q.larger());
}
```

真跑：

```
7
banana
```

三個要點：

- `struct Pair<T>` 讓 `a`、`b` 兩個欄位都是同一個型別 `T`。`Pair<i32>` 和 `Pair<&str>` 是兩個不同的具體型別，各自單型化——`Pair<i32>` 的 `larger` 和 `Pair<&str>` 的 `larger` 是兩份不同的機器碼。
- `impl<T: …> Pair<T>` 的第一個 `<T>` 是**宣告**「這個 impl 區塊對所有 `T` 生效」，後面的 `Pair<T>` 是「為這個泛型型別實作」。漏掉 `impl` 後那個 `<T>` 會編不過（編譯器不知道 `T` 從哪來）。
- bound 可以下在 `impl` 上（`impl<T: PartialOrd>`），意思是「只有 `T: PartialOrd` 時，`Pair<T>` 才有這些方法」。你也能對同一個 `Pair<T>` 寫多個 `impl` 區塊、各帶不同 bound——某些方法只在 `T` 滿足更強條件時才存在。這是 Rust 表達「條件性行為」的常見手法，C++ 要靠 SFINAE 或 concept 才做得到。

## 底層機制：單型化，眼見為憑

現在證明「編譯器真的為每個型別生一份碼」。回到 `largest`，我們把它編成目標檔（object file），用 `nm` 看符號表——**如果單型化真的發生，`largest` 應該有兩個不同的符號**，一個給 `i32`、一個給 `f64`。

編譯 + 觀察（真跑）：

```
$ rustc --emit=obj mono.rs -o mono.o
$ nm mono.o | grep -i largest | c++filt
0000000000000000 t mono[ecdf4d92cf85401c]::largest::<f64>
0000000000000000 t mono[ecdf4d92cf85401c]::largest::<i32>
```

**兩個符號**：`largest::<f64>` 和 `largest::<i32>`。這是眼見為憑——你寫了一份 `largest<T>`，編譯器生成了兩份獨立的機器碼函式，各自針對一個具體型別特化。執行期沒有「`T` 是什麼」的判斷，`largest(&ints)` 直接呼叫 `largest::<i32>` 這個地址、`largest(&floats)` 直接呼叫 `largest::<f64>`——兩次都是普通的直接呼叫（direct call），零間接、零 vtable。這就是「零成本抽象」的字面意思：泛型抽象在執行期不留任何開銷，跑起來和你手寫兩份 `largest_i32`/`largest_f64` 一模一樣。

底層流程：

```
   1. 前端解析 largest<T>，型別檢查 T: PartialOrd + Copy 是否被滿足
                    │
                    ▼
   2. 收集所有「實際用到的具體型別」：這裡是 i32、f64
      （由 largest(&ints)、largest(&floats) 這兩個呼叫點決定）
                    │
                    ▼
   3. 對每個具體型別，把 T 替換掉，生成一份特化的 MIR/LLVM IR：
      largest::<i32>、largest::<f64>
                    │
                    ▼
   4. 各自最佳化、產生機器碼 → 兩個獨立符號、兩份指令
```

**對照 C++ template——同一個機制**。把等價的 `largest<T>` 用 C++ 寫，看 g++ 生成什麼：

```
$ g++ tmpl.cpp -o tmpl
$ nm tmpl | grep -i largest | c++filt
… W int largest<int>(int const*, int)
… W double largest<double>(double const*, int)
```

`int largest<int>` 和 `double largest<double>`——和 Rust 的 `largest::<i32>`/`largest::<f64>` 一一對應。**Rust 泛型和 C++ template 是同一套「編譯期實例化」機制。** 你在 C++ 累積的所有關於 template 的直覺（包含它的優缺點）都能搬過來。

## 型別參數執行期不存在：行為證明

上面用符號證明了「生成多份」。再從**行為**證明「型別參數在執行期不存在、是編譯期就定死的」。`type_name::<T>()` 回傳 `T` 的名字——但這個名字是**編譯期**填進去的常數，不是執行期查出來的：

```rust
use std::any::type_name;

fn kind<T>() -> &'static str {
    type_name::<T>()             // 這個字串在編譯 kind::<i32> 時就被填成 "i32"
}

fn main() {
    println!("{}", kind::<i32>());
    println!("{}", kind::<Vec<u8>>());
}
```

真跑：

```
i32
alloc::vec::Vec<u8>
```

關鍵：`kind::<i32>` 和 `kind::<Vec<u8>>` 是**兩個不同的單型化實例**，各自的 `type_name` 在編譯期就被替換成對應的常數字串。執行期沒有任何「反射」或「查 T 是誰」的動作——因為 `T` 早就不存在了，只剩下兩份已經把答案寫死的具體函式。這也解釋了 Rust 為何沒有 Java/C# 那種執行期反射：泛型單型化後，型別資訊在執行期已被抹除（除非你顯式用 `Any` 之類的機制留一份）。

## code bloat：同源機制、同源代價

單型化的好處是零執行期成本，代價是 **code bloat（程式碼膨脹）**——每個具體型別一份碼，用得越多、binary 越大。這和 C++ template 的抱怨一模一樣。實測：一個 `#[inline(never)]` 的泛型函式，對 10 種數值型別各呼叫一次，數數生成幾份：

```rust
use std::hint::black_box;

#[inline(never)]                 // 阻止 inline，好讓每份特化留下獨立符號
fn work<T: std::fmt::Display>(x: T) -> String {
    let mut s = String::new();
    for _ in 0..3 { s.push_str(&format!("{} ", x)); }
    black_box(s)
}

fn main() {
    print!("{}", work(1i8));   print!("{}", work(1i16));
    print!("{}", work(1i32));  print!("{}", work(1i64));
    print!("{}", work(1u8));   print!("{}", work(1u16));
    print!("{}", work(1u32));  print!("{}", work(1u64));
    print!("{}", work(1.0f32)); print!("{}", work(1.0f64));
    println!();
}
```

編譯後數符號（真跑）：

```
$ rustc bloat.rs -o bloat
$ nm bloat | grep -E '4work' | c++filt | sort -u
… bloat[…]::work::<i8>
… bloat[…]::work::<i16>
… bloat[…]::work::<i32>
… bloat[…]::work::<i64>
… bloat[…]::work::<u8>
… bloat[…]::work::<u16>
… bloat[…]::work::<u32>
… bloat[…]::work::<u64>
… bloat[…]::work::<f32>
… bloat[…]::work::<f64>
$ nm bloat | grep -E '4work' | wc -l
10
```

**10 種型別 → 10 份獨立的 `work` 機器碼**，各在不同地址。每一份都是完整的函式指令，binary 因此變大。這正是 code bloat：你只寫了一份 `work<T>`，但 binary 裡躺著 10 份幾乎一樣、只有型別細節不同的碼。C++ 用一堆 `std::vector<各種型別>` 也會遇到同樣的膨脹——這不是 Rust 的缺陷，是「單型化換零成本」這個設計本質的代價。

膨脹量能量化。同樣的 `work<T>`，一個 binary 只實例化 `i32` 一種，另一個實例化全部 10 種，都用 `rustc -O`，用 `size` 量 `.text`（程式碼段）大小（真跑）：

```
$ size w1 w10
   text	   data	    bss	    dec	    hex	filename
 329351	  12408	   4290	 346049	  547c1	w1      ← 只 i32
 363147	  13608	   2050	 378805	  5c7b5	w10     ← 全 10 種
```

`.text` 從 329,351 bytes 漲到 363,147 bytes——多實例化 9 種型別，程式碼段大了約 33.8 KB，平均每份特化約 3.75 KB。這個數字會隨函式複雜度放大：`work` 只是個小函式，換成複雜的泛型（如整個 `HashMap<K, V>` 的方法群）被幾十種 `(K, V)` 組合實例化，膨脹是幾百 KB 到 MB 等級。這也是為什麼大量使用泛型的 Rust binary（和重度 template 的 C++ binary）常比同功能的 C 程式大。

減緩 code bloat 的常見手法（延伸方向，不展開）：把泛型函式裡「和型別無關」的部分抽成一個非泛型的內層函式，只讓薄薄的一層泛型外殼被單型化（社群稱 "outline" 或去泛型化 pattern）；或改用 `dyn Trait` 動態分派（下章）——那是「一份碼、執行期查 vtable」，用執行期成本換 binary 大小。

## const generics 與 turbofish

**const generics**：型別參數可以是「值」而非「型別」，最常見是陣列長度 `[T; N]` 裡的 `N`：

```rust
fn sum<const N: usize>(arr: [i32; N]) -> i32 {   // N 是編譯期常數參數
    arr.iter().sum()
}
fn main() {
    println!("{}", sum([1, 2, 3]));          // N 推斷為 3
    println!("{}", sum([10, 20, 30, 40]));   // N 推斷為 4
}
```

真跑：

```
6
100
```

`const N: usize` 讓 `N`（陣列長度）成為泛型參數的一部分。`sum([1,2,3])` 和 `sum([10,20,30,40])` 是 `N=3` 和 `N=4` 兩個不同的單型化實例。這讓你能寫「對任意長度陣列都適用」的函式，同時保持長度是編譯期已知（不像 slice `&[i32]` 長度是執行期的）。const generics 在 Rust 1.51 穩定，是相對晚才進來的能力。

**turbofish `::<>`**：當編譯器無法從上下文推斷型別參數時，用 `::<Type>` 明確指定：

```rust
fn main() {
    let parsed = "255".parse::<u8>().unwrap();   // 告訴 parse 要 parse 成 u8
    println!("{}", parsed);
    let v = Vec::<i32>::new();                    // 指定 Vec 的元素型別
    println!("{}", v.len());
}
```

真跑：

```
255
0
```

`"255".parse::<u8>()` 的 `::<u8>` 就是 turbofish——`parse` 的回傳型別是泛型 `Result<F, _>`，編譯器光看 `"255".parse()` 不知道你要 parse 成什麼，turbofish 直接指定 `u8`。名字來自 `::<>` 長得像一條魚。等價的替代寫法是型別標註（`let parsed: u8 = "255".parse().unwrap()`），兩者擇一。

## 對比與取捨

| 面向 | C `void*` 泛型 | C 巨集泛型 | Rust 泛型（單型化）/ C++ template | Rust `dyn Trait`（Ch 11） |
|---|---|---|---|---|
| 型別安全 | 無（cast 不檢查） | 幾乎無（純文字替換） | 有（bound 編譯期檢查） | 有 |
| 除錯性 | 尚可 | 差（展開後天書、無函式） | 好（是真的特化函式） | 好 |
| 執行期成本 | 一次間接呼叫 + 可能 cache miss | 零（inline） | **零**（直接呼叫，無間接） | 一次 vtable 間接呼叫 |
| binary 大小 | 小（一份碼） | 隨展開膨脹 | **大**（每型別一份，code bloat） | 小（一份碼） |
| 編譯時間 | 快 | 快 | **慢**（每型別各自最佳化） | 較快（一份） |
| 適用 | C 世界的無奈之選 | 小巨集 | 熱路徑、要零成本、型別已知 | 型別要執行期才定、要省 binary |

取捨要**誠實**：單型化不是免費的。它用「編譯變慢 + binary 變大」換「執行期零成本 + 型別安全 + 好除錯」。什麼時候該用泛型（靜態分派）、什麼時候該用 `dyn`（動態分派）：熱路徑、型別在編譯期已知、要榨乾效能 → 泛型；型別要到執行期才決定（如一個 `Vec<Box<dyn Draw>>` 裝各種形狀）、或泛型實例太多讓 binary 爆掉 → `dyn`。這是真實的工程權衡，下一章把 `dyn` 那一半補齊後你才能完整地做這個選擇。

## 踩雷集錦

1. **以為泛型有執行期成本 / 像 Java 的泛型**：Java 泛型是 type erasure（執行期抹除、只有一份碼、靠 cast），Rust 泛型是單型化（每型別一份特化碼、零執行期成本）。把 Rust 泛型想成 Java 泛型會讓你誤以為有 boxing/cast 開銷——其實完全沒有，跑起來和手寫特化版一樣快。它更接近 C++ template。

2. **忘了 code bloat 是真實代價**：泛型「零成本」指的是**執行期**零成本，不是編譯期和 binary 大小零成本。一個被幾十種型別實例化的泛型函式會生幾十份碼。大量泛型的 crate 編譯時間和 binary 大小會明顯上升——這是設計本質，不是 bug。熱路徑用泛型，冷路徑或實例爆炸時考慮 `dyn`。

3. **trait bound 少了才發現、以為是 borrow 問題**：泛型函式本體裡用到 `>`、`.clone()`、`+` 等操作，對應的 bound（`PartialOrd`、`Clone`、`Add`）必須寫在簽章。少了 bound，編譯器報的是「`T` 沒有這個方法/操作」，新手常誤以為是別的問題。記住：**你在泛型本體裡用了什麼能力，就得在 bound 裡宣告 `T` 有那個能力**。

4. **turbofish 和型別標註搞混語法位置**：`parse::<u8>()`（turbofish 在方法名後）vs `let x: u8 = parse()`（標註在變數上）是兩種指定型別的方式，別寫成 `parse<u8>()`（沒有 `::`，會被解析成小於號比較，編譯錯誤）。turbofish 那個 `::` 不能省。

5. **const generics 的 `N` 是編譯期常數，不能傳執行期變數**：`fn sum<const N: usize>(arr: [i32; N])` 的 `N` 必須在編譯期已知。你不能用一個 runtime 的 `let n = read_input();` 去實例化它——那種「執行期才知道長度」的情況該用 slice `&[i32]` 或 `Vec`，不是 const generics。

## 進階：再往深一層

**單型化發生在「呼叫點」，不是「定義點」。** 一個泛型函式如果在你的 crate 裡定義但**從沒被任何具體型別呼叫**，它根本不會生成任何機器碼——沒有實例化就沒有碼。這也是為什麼泛型函式的某些型別錯誤要到有人用具體型別呼叫時才暴露（雖然 Rust 靠 trait bound 把大部分錯誤提前到定義處檢查，比 C++17 template 好很多）。

**`impl Trait` 參數 = 靜態分派的語法糖。** `fn f(x: impl Display)` 和 `fn f<T: Display>(x: T)` 生成一樣的碼——都是單型化、每型別一份。而 `fn f(x: &dyn Display)` 是動態分派、一份碼 + vtable。同一個 `Display`，`impl Trait`/`T: Trait` 走靜態（本章），`dyn` 走動態（Ch 11）——這是泛型與 trait object 的分水嶺，下章正題。

**面試常問**：「Rust 泛型和 C++ template、Java 泛型分別是什麼關係？」——和 C++ template **同源**（都是編譯期單型化/實例化，都有 code bloat）；和 Java 泛型**相反**（Java 是 type erasure、執行期抹除、一份碼、有 cast 開銷）。所以 Rust 泛型是零成本抽象、無執行期反射，代價是編譯慢、binary 大——這組取捨要能一口氣講出來。

## 動手練習

1. **眼見單型化**：把本章的 `largest<T>` 存成 `mono.rs`，跑 `rustc --emit=obj mono.rs -o mono.o` 然後 `nm mono.o | grep -i largest | c++filt`，親眼看到 `largest::<i32>` 和 `largest::<f64>` 兩個符號。再加一個 `largest(&[1u8, 2, 3])` 呼叫，重編，確認多出 `largest::<u8>`——證明「用一個新型別就多生一份碼」。

2. **量 code bloat**：把本章 `work<T>` 那段，一次只實例化 1 種型別、再一次實例化全部 10 種，各自 `rustc -O` 編出 binary，用 `ls -l` 或 `size` 比 binary 大小。感受「多實例化幾種型別，binary 就長多少」。

3. **靜態 vs 動態的 binary 對照（連到下章）**：寫一個 `trait Draw { fn draw(&self); }`，兩個實作型別。先用泛型 `fn render<T: Draw>(x: &T)` 對兩型別各呼叫，看生成兩份 `render`；下一章學 `dyn` 後回來改成 `fn render(x: &dyn Draw)`，看只剩一份 `render` + vtable。並排體會兩種分派的 binary 差異。

## 本章重點整理

- **泛型 = 編譯期的程式碼工廠**：你寫一份帶 `T` 的模板，編譯器對每個實際用到的具體型別**單型化**出一份特化機器碼（真跑：`largest<T>` 對 `i32`/`f64` 生成 `largest::<i32>`/`largest::<f64>` 兩個符號）。
- **和 C++ template 同源**：同樣是編譯期實例化、同樣零執行期成本、同樣有 code bloat（真跑：`work<T>` 對 10 型別生 10 份碼）；和 Java 的 type erasure 相反。
- **型別參數執行期不存在**：`T` 在編譯期就被替換掉，執行到的是具體特化函式，直接呼叫、零間接、無 vtable、無反射——這是「零成本抽象」的字面意義。
- **比 C 的 `void*`（丟型別安全）和巨集（丟除錯性）兩全**：trait bound（`T: PartialOrd + Copy`）在編譯期保證 `T` 具備用到的能力，錯誤指向 bound。
- **誠實的代價**：零成本是**執行期**零成本；編譯變慢、binary 變大是真實代價。熱路徑/型別已知用泛型，型別執行期才定或實例爆炸用 `dyn`（Ch 11）。const generics（`[T; N]`）、turbofish（`::<>`）是配套語法。

## 自我檢核

- [ ] 面試問「Rust 泛型和 Java 泛型、C++ template 的關係」，能答出「和 C++ template 同源（單型化、有 code bloat）、和 Java type erasure 相反（無執行期抹除/反射）」。
- [ ] 不看筆記，能解釋「型別參數在執行期不存在」是什麼意思，並說出怎麼用 `nm` 符號證明單型化生了多份碼。
- [ ] 能說出泛型「零成本」到底是哪種成本零、哪種成本不零（執行期零、編譯期/binary 不零）。
- [ ] 知道什麼情況該用泛型（靜態分派）、什麼情況該用 `dyn`（動態分派），並能講出各自的取捨。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 10.1「Generic Data Types」** — （[doc.rust-lang.org/book/ch10-01-syntax.html](https://doc.rust-lang.org/book/ch10-01-syntax.html)）
  - **讀哪裡**：整節，尤其結尾「Performance of Code Using Generics」小節——官方直接講單型化與 `Option<i32>`/`Option<f64>` 生成兩份的例子。
  - **學到什麼**：本章單型化的官方對應說明，The Book 用 `largest` 同一個例子（本章沿用）。
  - **前提**：懂 Ch 9 的 trait bound（`T: Trait`）；本章的 bound 就是那裡的延伸。

- **《Programming Rust, 2nd ed.》Ch 11「Generic Functions and Type Parameters」** — Blandy, Orendorff, Tindall（O'Reilly, 2021）
  - **讀哪裡**：整章，特別是它把泛型（靜態分派）和 trait object（動態分派）並排比較的那幾節——正是本章「取捨」那節的完整版。
  - **學到什麼**：對 C++ 使用者友善的單型化解釋，含 code bloat 與編譯時間的實務討論，比 The Book 深。
  - **前提**：懂本章基本泛型語法；此書假設有系統程式背景。

### 官方參考

- **《The Rust Reference》「Generic parameters」與「Const generics」** — （[doc.rust-lang.org/reference/items/generics.html](https://doc.rust-lang.org/reference/items/generics.html)）
  - **讀哪裡**：型別參數、`where` 子句、const 參數三節的形式化定義；const generics 的限制（哪些型別能當 const 參數）在這裡最權威。
  - **學到什麼**：本章 const generics 那節沒展開的邊界——為什麼目前 const 參數只能是整數/bool/char 等，不能是任意型別。
  - **前提**：懂本章語法；Reference 是條文，配本章例子讀。

### 技術文章

- **「Rust generics and where they come from」/ rustc dev guide「Monomorphization」** — Rust Compiler Team（[rustc-dev-guide.rust-lang.org/backend/monomorph.html](https://rustc-dev-guide.rust-lang.org/backend/monomorph.html)）
  - **這篇說什麼**：從編譯器內部視角講單型化怎麼實作——collector 怎麼從呼叫點收集需要實例化的型別、怎麼生成 codegen unit。補足本章「底層流程」那張圖的真實編譯器版本。
  - **讀哪裡**：「Collection」與「Polymorphization」兩節；Polymorphization 是 rustc 試圖減緩 code bloat 的實驗性優化，正對本章 code bloat 那節。
  - **為什麼值得讀**：這是 rustc 官方開發者文件，講的是真實編譯器行為而非教學簡化；想知道「編譯器到底怎麼決定生哪幾份」看這裡。

單型化讓 `T: Trait` 零成本，代價是每型別一份碼、型別必須編譯期已知。當你需要「一個容器裝多種型別、執行期才決定呼叫誰」時，泛型做不到——那就要 **trait object（`dyn Trait`）** 的動態分派：一份碼、一張執行期查的 vtable。下一章把這另一半分派機制補齊，你才能完整地在靜態與動態之間做選擇。

→ [Ch 11 Trait Object 與動態分派](./11-trait-objects-dispatch.md)
