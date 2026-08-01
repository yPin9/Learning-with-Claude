# Ch 04 — Lifetimes：借用的存活期

> **目標**：把 lifetime 理解成「編譯器用來證明『引用不會比被指物活得久』的靜態標註」，而不是「引用活多久」的執行期概念；會讀會寫基本的 `'a` 標註（函式與 struct）；能真跑 `E0106`（缺 lifetime）、`E0597`（referent 活太短）、`E0515`（回傳指向 local 的引用），並理解 C 的 dangling pointer 災難怎麼被搬到編譯期擋下。

> **環境**：Rust 範例以 `rustc 1.97.1`（stable）在 x86-64 Linux（WSL2）跑過。C 對照用 `gcc -fsanitize=address`。

## 為什麼需要這個？

上一章的懸空引用被 `E0106` 擋了，但我們把「為什麼」跳過了。這章補上。

先看 C 這一整類最經典、最致命的 bug——回傳指向區域變數的指標：

```c
#include <stdio.h>
const char *first_word(void) {
    char owned[] = "local";
    return owned;            // 回傳 stack 陣列位址
}
int main(void) {
    const char *p = first_word();
    printf("%s\n", p);       // 讀已回收的 stack frame
    return 0;
}
```

`owned` 是 `first_word` 的區域變數，放在它的 stack frame 上。函式一 return，那個 frame 就被回收（後續呼叫會覆蓋它）。`main` 拿到的 `p` 指向一塊已經不屬於任何人的 stack 記憶體——**懸空指標（dangling pointer）**。`gcc` 會給個警告，但**照樣編譯過**：

```
dp.c: In function 'first_word':
dp.c:4:12: warning: function returns address of local variable [-Wreturn-local-addr]
    4 |     return owned;            // 回傳 stack 陣列位址
      |            ^~~~~
```

跑起來（`-fsanitize=address`）：

```
AddressSanitizer:DEADLYSIGNAL
=================================================================
==227968==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000 ...
==227968==The signal is caused by a READ memory access.
    #2 ... in main
```

Segfault。沒開 ASan 的 release build 裡，這是「有時看起來能跑、有時印出垃圾、有時崩潰」的海森堡 bug。C 對這只有一個編譯警告（還常被淹沒在一堆輸出裡），**沒有真正的防護**。

Rust 的問題意識：**怎麼在編譯期證明「一個引用永遠不會活得比它指向的東西久」？** 答案就是 lifetime。lifetime 不是新的執行期機制，是編譯器帳本上的一套標註，用來做這個「活多久」的證明。這章講怎麼讀、怎麼寫這套標註。

## 先建立直覺

先破除最常見的誤解：**lifetime 不是「引用活多久」的執行期時間。** 它是一個**編譯期的標籤**，用來標記「這個引用所指向的資料，保證至少活到什麼時候」。編譯器拿這些標籤做一件事：檢查「引用的存活範圍」有沒有超出「被指資料的存活範圍」。超出了，就是懸空，編譯期報錯。

用一個心智圖像：把每個值想成一條有起點有終點的線段（它的存活區間），引用是一條箭頭指向某條線段。**合法的唯一條件是：箭頭（引用）的存活區間，被完全包在被指線段的存活區間裡面。**

```
   合法：引用不比被指物活得久

   被指物 s:   ┌──────────────────────────┐
   引用   r:        ┌───────────┐            ← r 的區間包在 s 裡，OK


   非法：引用活得比被指物久（懸空）

   被指物 s:   ┌──────────────┐
   引用   r:        ┌───────────────────┐    ← r 超出 s 的尾巴 → 懸空 → 報錯
                              ↑ s 沒了，r 還指著它
```

`'a`、`'b` 這些標註，就是給這些「存活區間」取名字，好讓你在函式簽章裡表達它們之間的關係（例如「回傳的引用活得和參數 `x` 一樣久」）。編譯器不會擅自假設這些關係——你不寫清楚、它又猜不出來時，就要你標。

> 這裡有個容易混的點：lifetime 標註**不會改變**任何東西的實際存活時間。你標 `'a` 不會讓某個值活久一點——標註只是在**描述**已經存在的存活關係，好讓編譯器驗證。它像型別標註：`x: i32` 不會把 `x` 變成整數，只是宣告它是。

## 函式簽章的 lifetime：為什麼需要標

多數時候你不用寫 lifetime——編譯器用一套「省略規則」（elision，Ch 5 專講）自動補。需要手寫的情況，是**編譯器無法自己推斷「回傳的引用綁到哪個輸入」的時候**。經典例子 `longest`：回傳兩個字串引用裡較長的那個。

先看**不標**會怎樣：

```rust
fn longest(x: &str, y: &str) -> &str {
    if x.len() > y.len() { x } else { y }
}
fn main() {
    let r = longest("aaa", "bb");
    println!("{}", r);
}
```

真跑：

```
error[E0106]: missing lifetime specifier
 --> l2.rs:1:33
  |
1 | fn longest(x: &str, y: &str) -> &str {
  |               ----     ----     ^ expected named lifetime parameter
  |
  = help: this function's return type contains a borrowed value, but the signature does not say whether it is borrowed from `x` or `y`
help: consider introducing a named lifetime parameter
  |
1 | fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
  |           ++++     ++          ++          ++
```

`E0106` 的 `help` 一針見血：**回傳型別是個借來的值，但簽章沒說它是從 `x` 借的還是從 `y` 借的**。這對編譯器很重要——它要靠這個資訊，在呼叫端檢查「回傳的引用會不會比它的來源活得久」。來源是 `x` 還是 `y`，存活範圍可能不同，編譯器不能瞎猜。所以它要你標。

照 `help` 標上 `'a`：

```rust
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() { x } else { y }
}
fn main() {
    let s1 = String::from("long string");
    let s2 = String::from("short");
    let r = longest(&s1, &s2);
    println!("longest = {}", r);
}
```

真跑：

```
longest = long string
```

讀這個簽章 `fn longest<'a>(x: &'a str, y: &'a str) -> &'a str`：

- `<'a>` 宣告一個 lifetime 參數 `'a`（像宣告泛型型別參數 `<T>`）。
- `x: &'a str, y: &'a str`：兩個參數的引用都標成 `'a`。
- `-> &'a str`：回傳的引用也是 `'a`。

它表達的**契約**是：「回傳的引用，活得不超過 `x` 和 `y` 之中較短命的那個。」因為回傳的可能是 `x` 也可能是 `y`，所以編譯器保守地要求「回傳值只能活到 `x` 和 `y` 都還在的期間」。`'a` 在這裡代表「`x` 和 `y` 存活範圍的交集」。這個契約讓呼叫端能被檢查——下面就看它怎麼發威。

## 契約發威：referent 活太短（E0597）

把 `longest` 的回傳值綁到一個活得比它短的變數，看契約怎麼在呼叫端擋你：

```rust
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() { x } else { y }
}
fn main() {
    let s1 = String::from("long string");
    let result;
    {
        let s2 = String::from("short");
        result = longest(&s1, &s2);   // result 綁到 s2 的壽命
    }                                 // s2 在此 drop
    println!("{}", result);           // 但這裡還要用 result
}
```

真跑：

```
error[E0597]: `s2` does not live long enough
  --> l3.rs:9:31
   |
 8 |         let s2 = String::from("short");
   |             -- binding `s2` declared here
 9 |         result = longest(&s1, &s2);   // result 綁到 s2 的壽命
   |                               ^^^ borrowed value does not live long enough
10 |     }                                 // s2 在此 drop
   |     - `s2` dropped here while still borrowed
11 |     println!("{}", result);           // 但這裡還要用 result
   |                    ------ borrow later used here
```

`E0597` 把整條因果鏈畫出來：`s2` 在第 8 行宣告、第 10 行離開內層 block 就 drop 了；但 `longest` 的契約說 `result` 的存活範圍是「`s1` 和 `s2` 的交集」，也就是不能超過 `s2`；而第 11 行還在用 `result`——`result` 活得比 `s2` 久，違約。這正是上面直覺圖裡「箭頭超出線段尾巴」的情況，被編譯期抓住。

**這就是重點**：`longest` 的 lifetime 標註不是裝飾。它是一份契約，讓編譯器**在呼叫端**（`main` 裡）就能證明「不會有引用活得比被指物久」。沒有這份契約，編譯器不知道 `result` 綁到誰、也就無法做這個證明——這是 C 缺的那一塊。

## dangling 直接被擋：回傳 local 的引用（E0515）

現在把本章開頭那個 C 的災難（回傳區域變數的位址）用 Rust 寫，看它編不過：

```rust
fn first_word<'a>() -> &'a str {
    let owned = String::from("local");
    owned.as_str()           // 回傳指向 owned 的引用
}
fn main() { println!("{}", first_word()); }
```

真跑：

```
error[E0515]: cannot return value referencing local variable `owned`
 --> l6.rs:3:5
  |
3 |     owned.as_str()           // 回傳指向 owned 的引用
  |     -----^^^^^^^^^
  |     |
  |     returns a value referencing data owned by the current function
  |     `owned` is borrowed here
```

`E0515` 說得直白：**你回傳的值引用了這個函式擁有的資料**。`owned` 在 `first_word` 結束時就會 drop，回傳的引用會指向已釋放的記憶體——正是 C 那個 segfault 的成因。差別是：**C 給你一個常被忽略的警告 + runtime segfault，Rust 給你一個編譯錯誤，程式根本產不出來。**

（上一章那個 `fn dangle() -> &String` 的 `E0106` 是同一類問題的另一個面向：那裡連 lifetime 都沒法標，因為回傳的引用沒有任何輸入可以綁，編譯器直接說「這函式回傳借來的值，但沒有值可以借」。這裡 `E0515` 是你嘗試綁到 local、被抓包。兩者都是「回傳的引用沒有合法的來源」。）

正解永遠是**回傳擁有的值**（`String` 而非 `&str`），把所有權交出去（上一章教的），而不是回傳借用。想回傳借用，那個借用的來源必須是**呼叫者傳進來的**（像 `longest` 那樣綁到參數），這樣被指物活在呼叫者那邊，函式返回後依然存在。

## struct 持有引用：欄位是借來的

到目前為止引用都是區域變數或參數。當一個 **struct 想持有一個引用**（借別人的資料，自己不擁有），struct 定義就必須標 lifetime：

```rust
struct Excerpt<'a> {
    part: &'a str,        // 借用別人的字串，不擁有
}
fn main() {
    let novel = String::from("Call me Ishmael. Some years ago...");
    let first = novel.split('.').next().unwrap();
    let e = Excerpt { part: first };
    println!("{}", e.part);
}
```

真跑：

```
Call me Ishmael
```

`struct Excerpt<'a>` 的 `<'a>` 宣告：這個 struct 持有一個 lifetime 為 `'a` 的引用。它表達的契約是：**`Excerpt` 的實例不能活得比它借用的字串（`'a`）久。** 一個 `Excerpt` 存在的期間，它的 `part` 指向的資料必須一直有效。這就是 C 裡「struct 裡放一根指標指向別的物件」的安全版——C 對「這根指標指向的東西會不會先死」零檢查，Rust 用 `'a` 把這個約束編進型別。

違反它照樣被擋——讓 `Excerpt` 活得比被借的字串久：

```rust
struct Excerpt<'a> { part: &'a str }
fn main() {
    let e;
    {
        let novel = String::from("temporary");
        e = Excerpt { part: novel.as_str() };
    }                        // novel drop
    println!("{}", e.part);  // 但 e 還指著它
}
```

真跑：

```
error[E0597]: `novel` does not live long enough
 --> l5.rs:6:29
  |
5 |         let novel = String::from("temporary");
  |             ----- binding `novel` declared here
6 |         e = Excerpt { part: novel.as_str() };
  |                             ^^^^^ borrowed value does not live long enough
7 |     }                        // novel drop
  |     - `novel` dropped here while still borrowed
8 |     println!("{}", e.part);  // 但 e 還指著它
  |                    ------ borrow later used here
```

又是 `E0597`：`e` 持有指向 `novel` 的引用，但 `novel` 在內層 block 結束就 drop，`e` 卻在外層還要用。struct 的 lifetime 契約讓這個「持有懸空引用的 struct」在編譯期就現形。

## 底層機制：lifetime 與 borrow 是同一件事的兩面

把上一章和這一章接起來。上一章說 borrow checker 對每個借用算一段「存活區間」再檢查衝突——**那個存活區間，就是 lifetime。** lifetime 不是額外的東西，它就是 borrow checker 用來推理的那把尺。

執行期發生什麼？**什麼都沒有。** 這是關鍵：lifetime 是純編譯期概念，**完全不存在於生成的機器碼裡**。`&'a str` 和 `&str` 跑起來是一模一樣的機器碼——都是一根指標。`'a` 這個標註在編譯完成後被完全抹掉，跟型別參數 `T` 在單型化後消失一樣。它的全部作用是在編譯期讓 borrow checker 能做「引用區間 ⊆ 被指物區間」的證明。

編譯器怎麼用它？流程大致是：

```
   1. 為每個引用、每個借用指定一個 lifetime 變數（'a, 'b, ...）
                    │
                    ▼
   2. 從程式碼收集「約束」：
      - 引用在第 N 行被使用 → 它的 lifetime 至少要活到第 N 行
      - 引用指向某個值 → 它的 lifetime 不能超過那個值的存活範圍
      - 函式簽章的 'a 標註 → 呼叫端要滿足這些關係
                    │
                    ▼
   3. 解這組約束（大致是求交集/看能否滿足）
                    │
                    ▼
   4. 有約束無法滿足（引用活得比被指物久）→ 報 E0597/E0515/E0106...
```

`longest` 需要你手標 `'a`，就是因為第 2 步收集約束時，編譯器看回傳型別 `&str`，無法從函式本體唯一決定「這個回傳引用該綁到哪個輸入的 lifetime」——`if` 兩支分別回 `x` 和 `y`，來源不唯一。你標 `'a` 等於直接告訴它「回傳值綁到 `x` 和 `y` 的交集」，補上它推不出來的那條約束。而 struct 欄位的 `'a`，是告訴編譯器「這個 struct 的存活範圍受這個引用約束」，讓它在建構和使用 struct 時都能檢查。

## 對比與取捨

| 面向 | C 裸指標 | Rust 引用 + lifetime |
|---|---|---|
| 回傳 local 的引用 | 編譯警告（可忽略）+ runtime 懸空 | 編譯錯誤 `E0515` |
| struct 持有指向他物的指標 | 零檢查，指向物先死就 UB | `'a` 標註，編譯期保證 struct 不比被指物久 |
| 「引用活多久」的表達 | 無（靠註解和紀律） | `'a` 標註，編譯器驗證 |
| 執行期成本 | 一根指標 | 一根指標（lifetime 純編譯期，機器碼無痕跡） |
| 出錯時機 | runtime（segfault / 讀垃圾 / 有時看似正常） | 編譯期紅字 |

取捨：Rust 把 C 裡「靠工程師紀律避免懸空引用」變成「編譯器強制證明」，代價是你要學會讀寫 lifetime 標註，偶爾為了讓編譯器滿意得調整程式結構（或改成回傳擁有值）。多數日常程式碼靠 elision（Ch 5）連標都不用標；真正要手標的場景（回傳綁到某輸入的引用、struct 持有引用）並不多，但都是最容易出懸空 bug 的地方——Rust 恰好在這些地方逼你講清楚。

## 踩雷集錦

1. **以為 `'a` 讓某個值「活久一點」**：最頑固的誤解。lifetime 標註**只描述、不改變**存活關係。標 `'a` 不會延長 `s2` 的壽命去救上面 `E0597` 的例子——那個錯誤要靠**調整程式結構**（把 `s2` 宣告到外層 scope，或別讓 `result` 活過 `s2`）來修，不是靠改標註。標註只是讓編譯器看清楚關係，關係本身錯了，得改程式。

2. **以為所有引用都要手標 lifetime**：不是。絕大多數函式靠 elision 規則自動補 lifetime，你根本不用寫（Ch 5 講規則）。只有編譯器「猜不出回傳引用綁到哪」時才要手標。看到別人的 code 沒有 `'a` 不代表沒有 lifetime——是編譯器幫他補了。

3. **把 lifetime 當成一種型別**：`'a` 不是型別，是「存活區間」的名字。`&'a str` 裡真正的型別是 `str`，`&` 是引用，`'a` 是這個引用的存活標籤。三者是三個不同的東西疊在一起。混淆會讓你讀簽章時抓錯重點。

4. **回傳借用來「省一次複製」卻踩懸空**：新手想「回傳 `&str` 比回傳 `String` 省一次配置」，於是寫出 `fn f() -> &str { let s = String::from(...); s.as_str() }`——`E0515`。省複製的前提是**被借的資料活在函式外面**（來自參數或 `'static`）。函式內部造的資料，函式一返回就死，只能把所有權交出去（回傳 `String`）。想省複製要從呼叫端傳引用進來。

5. **`'static` 不是「免死金牌」**：看到 `E0106` 的 help 建議 `&'static str` 就照抄，常常是錯的。`'static` 表示「這個引用指向的資料活整個程式生命週期」（如字串字面量、`static` 變數）。把一個實際只活一小段的引用硬標 `'static`，要嘛編不過（因為資料不是真的 `'static`），要嘛把你推向更繞的錯誤。`'static` 是很強的保證，不是拿來安撫編譯器的萬用貼紙。

## 進階：再往深一層

**lifetime 是泛型參數的一種**。`fn longest<'a>(...)` 的 `<'a>` 和 `fn f<T>(...)` 的 `<T>` 語法同源——lifetime 就是一種「泛型的生命週期參數」。這也是為什麼一個函式可以同時有型別參數和 lifetime 參數：`fn foo<'a, T>(x: &'a T) -> &'a T`。單型化（monomorphization，Ch 10）會把 `T` 具體化，但 lifetime 不會產生多份程式碼——它編譯後就消失了，不像 `T` 會生出多個特化版本。

**多個 lifetime 參數**。`longest` 兩個參數共用 `'a`，是因為回傳值可能來自任一個，得取交集。但如果回傳值**只**來自其中一個，用不同的 lifetime 把關係講得更精確會讓呼叫端限制更鬆：

```rust
fn first<'a, 'b>(x: &'a str, _y: &'b str) -> &'a str {
    x                        // 只回 x，回傳綁 'a
}
fn main() {
    let x = String::from("keep me");
    let r;
    {
        let y = String::from("throwaway");
        r = first(&x, &y);   // 回傳綁 x 的壽命，不綁 y
    }                        // y drop，但無所謂
    println!("{}", r);       // x 還在，合法
}
```

真跑：

```
keep me
```

對比 `longest`：如果 `first` 也讓兩個參數共用 `'a`，上面這段就會像前面 `E0597` 那樣被擋（因為 `r` 會被要求不超過 `y`）。但 `first` 的回傳值真的只綁 `x`，所以標成 `'a`（只給 `x`）、`'b`（給 `y`）兩個獨立 lifetime 後，`y` 的短命完全不影響 `r`——`y` 在內層 block 死掉，`r` 照樣能用。什麼時候該共用一個、什麼時候該分開，取決於「回傳值真正綁到哪些輸入」。這是 Ch 5 variance 那些主題的入口。

**lifetime bound**。你會看到 `T: 'a` 這種寫法，意思是「型別 `T` 裡面所有的引用都至少活 `'a` 這麼久」。常出現在 struct 持有泛型 + 引用時。本章不展開，但看到 `T: 'a` 別慌，它是在約束「`T` 內含的借用不比 `'a` 短命」。

**面試常問**：「lifetime 是 runtime 的東西嗎？」標準錯誤答案是「是引用活多久」。正解：lifetime 是純編譯期的靜態標註，編譯後在機器碼裡不留任何痕跡，`&'a T` 和 `&T` 生成完全相同的碼；它的唯一作用是讓 borrow checker 能證明「引用的存活區間不超出被指物的存活區間」，把 C 的 dangling pointer 從 runtime UB 提到編譯期錯誤。

## 動手練習

1. 把 `longest` 改成回傳值**只**可能是 `x`（`fn longest<'a, 'b>(x: &'a str, y: &'b str) -> &'a str`，本體只回 `x`）。想清楚為什麼這樣 `y` 的 lifetime 就不必和回傳值扯上關係，再用上面 `E0597` 那個內層 block 的測試——把 `s2` 傳成 `y`——看它現在編得過（因為回傳值不綁 `s2` 了）。

2. 重現本章開頭的 C 懸空指標：用 `gcc -fsanitize=address` 編 `first_word` 那段跑一次看 segfault；再把等價 Rust（`E0515` 那段）跑一次。並排感受「同一個 bug、runtime segfault vs 編譯錯誤」。

3. 寫一個 `struct Parser<'a> { input: &'a str, pos: usize }`，給它一個方法 `fn rest(&self) -> &str` 回傳 `input` 從 `pos` 開始的切片。先不標任何 lifetime 讓 elision 試著補（多數會過，Ch 5 解釋為什麼），再故意讓 `Parser` 活得比 `input` 久，看 `E0597`。

## 本章重點整理

- lifetime 是**編譯期的靜態標註**，用來證明「引用的存活區間 ⊆ 被指物的存活區間」；它不是「引用活多久」的 runtime 概念，編譯後機器碼裡不留痕跡。
- 函式的 `'a` 標註（`fn longest<'a>(x: &'a str, y: &'a str) -> &'a str`）是一份契約，告訴編譯器回傳引用綁到哪個輸入，好在**呼叫端**證明無懸空。編譯器猜不出來時才要你手標（`E0106`）。
- struct 持有引用（`struct Excerpt<'a> { part: &'a str }`）必須標 lifetime，契約是「struct 不能活得比它借的資料久」。
- C 的懸空指標（回傳 local 位址、struct 持有指向先死物件的指標）是 runtime 災難，Rust 用 lifetime 在編譯期擋成 `E0515`/`E0597`。
- lifetime 標註只描述、不改變存活關係；改不了懸空就得改程式結構或回傳擁有值，不是改標註。

## 自我檢核

- [ ] 面試問「lifetime 是不是引用活多久的 runtime 概念」，能答出「純編譯期靜態標註、機器碼無痕跡、作用是證明引用不比被指物久」。
- [ ] 不看筆記，能解釋 `fn longest<'a>(x: &'a str, y: &'a str) -> &'a str` 這個簽章表達了什麼契約、為什麼編譯器不能自己猜。
- [ ] 能說出為什麼「回傳指向函式內 local 的引用」在 C 是可忽略警告 + segfault，在 Rust 是 `E0515` 編譯錯誤。
- [ ] 知道 `'a` 只描述、不延長存活範圍；遇到 `E0597` 知道要改程式結構而不是亂加標註或亂標 `'static`。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / 書籍

- **《The Rust Programming Language》(The Book) Ch 10.3「Validating References with Lifetimes」** — （[doc.rust-lang.org/book/ch10-03-lifetime-syntax.html](https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html)）
  - **讀哪裡**：整節，尤其「Generic Lifetimes in Functions」（就是 `longest`）、「Lifetime Annotations in Struct Definitions」。
  - **學到什麼**：本章的官方對應版本，用同一個 `longest` 例子；The Book 講得更慢，適合對某一步卡住時回去補。
  - **前提**：懂上一章借用；建議先讀完 The Book Ch 10.1/10.2（泛型/trait），lifetime 是泛型的一種。

- **《The Rustonomicon》「Lifetimes」與「Subtyping and Variance」** — （[doc.rust-lang.org/nomicon/lifetimes.html](https://doc.rust-lang.org/nomicon/lifetimes.html)）
  - **讀哪裡**：`lifetimes.html` 全篇（用「脫糖」的角度看編譯器怎麼展開 lifetime）；variance 那篇留到 Ch 5 再讀。
  - **學到什麼**：本章「底層機制」那套約束求解的更正式版本，理解編譯器如何把程式脫糖成帶顯式 lifetime 的形式。
  - **前提**：懂本章基本標註；Nomicon 假設你已經會安全 Rust。

### 技術文章

- **「Common Rust Lifetime Misconceptions」** — pretzelhammer（[github.com/pretzelhammer/rust-blog](https://github.com/pretzelhammer/rust-blog/blob/master/posts/common-rust-lifetime-misconceptions.md)）
  - **這篇說什麼**：逐條拆解 lifetime 的常見誤解（包含本章踩雷 1「`'a` 讓值活久一點」、踩雷 5「`'static` 迷思」），每條給反例。
  - **讀哪裡**：從頭讀，特別是「1) `T` only contains owned values」到「`'static` misconceptions」那幾條，正對本章踩雷集錦。
  - **為什麼值得讀**：這是英語 Rust 社群公認講 lifetime 誤解最完整的一篇，被無數教學引用；作者對每個誤解都給可跑的反例。

### 官方參考

- **《The Rust Reference》「Lifetime elision」** — （[doc.rust-lang.org/reference/lifetime-elision.html](https://doc.rust-lang.org/reference/lifetime-elision.html)）
  - **讀哪裡**：開頭三條 elision 規則的正式表述（本章多次提到「編譯器自動補」就是這三條）。
  - **學到什麼**：為什麼多數函式不用手標 lifetime——這是 Ch 5 的正題，這裡先看規則本身建立印象。
  - **前提**：懂本章函式 lifetime 標註；規則是形式化描述，配 Ch 5 的例子讀更好懂。

這章只到「會讀會寫基本標註」。真正日常寫 Rust 時你會發現多數函式不用標——那是 elision 規則在背後工作；而回傳引用綁到多個輸入、trait 裡的 lifetime、`&'a &'b T` 這種巢狀引用的協變/逆變（variance），都還沒碰。下一章把這些補齊。

→ [Ch 05 Lifetime 進階：elision / HRTB / variance](./05-lifetimes-advanced.md)
