# 練習 B — 泛型資料結構與自訂 trait

> **目標**：把 Ch 8–14 學到的東西拼起來——泛型（[Ch 10](./10-generics-monomorphization.md)）、trait（[Ch 9](./09-traits.md)）、`Iterator`/`IntoIterator`（[Ch 12](./12-core-traits.md)）、閉包 adapter（[Ch 14](./14-closures.md)）——實作一個**泛型 ring buffer（環形緩衝區）**，為它做三種迭代器（借用 / 擁有），再串上 `map`/`filter` 等 adapter 驗證。完成後你會確認自己能寫出「用起來像標準庫容器」的自訂型別。

## 背景與動機

ring buffer（環形緩衝區）是系統程式設計的常客：固定容量、寫滿時覆蓋最舊的元素。它是 producer-consumer queue、audio buffer、log 環、網路封包緩衝的底層資料結構。你在 C 裡大概寫過——一個陣列 + head/tail 索引 + 取模運算，容易在「滿了」「空了」的邊界寫錯（差一錯誤 off-by-one 的重災區）。

這個練習要你用 Rust 泛型實作它，重點**不在 ring buffer 演算法本身**（那你會），而在**怎麼讓它融入 Rust 的 iterator 生態**：實作 `Iterator` 讓 `for x in &rb` 能跑、實作 `IntoIterator` 讓它能被 `for` 消耗、讓 `rb.iter().map(...).filter(...)` 這種 adapter 鏈能串。這是「寫一個 Rust 容器」的核心技能——標準庫的 `Vec`/`VecDeque`/`HashMap` 都是這樣讓自己「用起來很自然」的。

## 任務規格

實作 `RingBuffer<T>`，一個固定容量的泛型環形緩衝區。

### 必須提供的方法

| 方法 | 簽名 | 行為 |
|---|---|---|
| 建構 | `fn with_capacity(cap: usize) -> Self` | 建一個容量 `cap` 的空 buffer；`cap == 0` 應 panic |
| 長度 | `fn len(&self) -> usize` | 目前元素數 |
| 判空 | `fn is_empty(&self) -> bool` | |
| 判滿 | `fn is_full(&self) -> bool` | |
| 推入 | `fn push(&mut self, value: T)` | 從尾端加入；**若已滿，覆蓋最舊的元素**（head 前進） |
| 彈出 | `fn pop(&mut self) -> Option<T>` | 移除並回傳**最舊**的元素；空時回 `None` |
| 借用迭代 | `fn iter(&self) -> Iter<'_, T>` | 從最舊到最新，產出 `&T` |

### 必須實作的 trait

- `impl Iterator for Iter<'a, T>`（`Item = &'a T`）——借用迭代器
- `impl IntoIterator for &'a RingBuffer<T>`（讓 `for x in &rb` 能跑）
- `impl IntoIterator for RingBuffer<T>`（`Item = T`，消耗 buffer，產出擁有的值）

### 限制

- 泛型 `T`，不假設 `T: Copy` 或 `T: Clone`（`pop` 要能把值 move 出來）。
- 不准用 `VecDeque`（那就是標準庫的 ring buffer，等於作弊）。底層可以用 `Vec<Option<T>>` 當儲存。
- 迭代順序永遠是**最舊 → 最新**。

### 驗收標準

- `for x in &rb` 和 `rb.iter()` 都能正確走訪，順序最舊到最新。
- `rb.iter().map(|&x| ...).filter(...).collect()` 能編譯並得到正確結果。
- 滿了之後 `push` 正確覆蓋最舊元素，`len` 不超過 `cap`。
- `T` 換成不同型別（`i32`、`&str`）都能用（泛型正確）。

## 期望輸出範例

```
輸入：cap=3, push 1,2,3,4（第 4 個時滿了，覆蓋最舊的 1）
buffer 內容（最舊→最新）：2 3 4

rb.iter().map(|&x| x*2).filter(|&x| x%4==0).collect()：[4, 8]
   （2,3,4 → *2 → 4,6,8 → 留 4 的倍數 → 4,8）

rb.iter().sum()：9   （2+3+4）

into_iter() 消耗：[2, 3, 4]
```

```
邊界輸入：cap=0
輸出：panic（capacity must be > 0）
```

```
邊界輸入：空 buffer 呼叫 pop()
輸出：None
```

## 如果你卡住了

1. **head/tail 怎麼算？** 存一個 `head`（最舊元素的索引）和 `len`（目前元素數）。第 `i` 個元素（從最舊算起）的實際索引是 `(head + i) % cap`。尾端（下一個 push 的位置）是 `(head + len) % cap`。用這兩個公式，push/pop/iter 全都好寫。
2. **push 滿了要覆蓋，len 怎麼變？** 滿的時候 push：寫進 tail 位置後，`head` 前進一格（丟掉最舊的），`len` 不變（還是等於 cap）。沒滿時：寫進 tail，`len += 1`，head 不動。
3. **iterator 怎麼知道走到哪了、何時停？** `Iter` 存一個 `pos`（已經走了幾個，從 0 開始）和它借用的 `&RingBuffer`。`next()` 裡：`pos >= len` 就回 `None`，否則算出 `(head + pos) % cap` 取值、`pos += 1`。
4. **為什麼底層用 `Vec<Option<T>>` 而不是 `Vec<T>`？** 因為 `T` 不保證有預設值，你沒法「先填滿一個 `Vec<T>`」。`Vec<Option<T>>` 可以用 `None` 表示「這格空的」，`pop` 時用 `Option::take()` 把值 move 出來、留下 `None`。
5. **`IntoIterator for RingBuffer<T>`（擁有版）怎麼產出 `T`？** 最簡單：包一個 `IntoIter { rb: RingBuffer<T> }`，`next()` 直接呼叫 `self.rb.pop()`——`pop` 已經回傳 `Option<T>`（擁有的值），正好是 `Iterator::next` 要的形狀。

## 實作步驟建議

### Step 1：資料結構與建構

定義 `struct RingBuffer<T> { buf: Vec<Option<T>>, head: usize, len: usize, cap: usize }`。寫 `with_capacity`（填 `cap` 個 `None`）、`len`/`is_empty`/`is_full`。先讓它能建、能查狀態。

### Step 2：push 與 pop

實作 `push`（用 Step 2 的覆蓋規則）和 `pop`（`Option::take` 把最舊的 move 出來）。寫個小 `main` push 幾個再 pop 幾個，`println!` 確認順序對。

### Step 3：借用迭代器 Iter

定義 `struct Iter<'a, T> { rb: &'a RingBuffer<T>, pos: usize }`，`impl Iterator for Iter`（`Item = &'a T`）。`RingBuffer::iter` 回傳 `Iter { rb: self, pos: 0 }`。這一步的難點是生命週期標注——`Iter` 借用 `RingBuffer`，`Item` 是 `&'a T`，生命週期要串起來。

### Step 4：IntoIterator（借用版 + 擁有版）

`impl IntoIterator for &'a RingBuffer<T>`（委派給 `iter()`）讓 `for x in &rb` 能跑。再做 `impl IntoIterator for RingBuffer<T>` + `struct IntoIter<T>`（`next` 呼叫 `pop`）讓 `for x in rb` 消耗它。

### Step 5：整合與測試

串 adapter：`rb.iter().map(...).filter(...).collect::<Vec<_>>()`、`rb.iter().sum()`。換 `T` 為 `&str` 跑一次確認泛型。跑滿覆蓋的 case 確認 `len` 不超過 `cap`。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```rust
// 泛型固定容量 ring buffer，含借用 / 擁有兩種迭代器。

pub struct RingBuffer<T> {
    buf: Vec<Option<T>>,
    head: usize, // 最舊元素的索引
    len: usize,
    cap: usize,
}

impl<T> RingBuffer<T> {
    pub fn with_capacity(cap: usize) -> Self {
        assert!(cap > 0, "capacity must be > 0");
        let mut buf = Vec::with_capacity(cap);
        for _ in 0..cap { buf.push(None); }   // 不要求 T: Default，用 None 填
        RingBuffer { buf, head: 0, len: 0, cap }
    }

    pub fn len(&self) -> usize { self.len }
    pub fn is_empty(&self) -> bool { self.len == 0 }
    pub fn is_full(&self) -> bool { self.len == self.cap }

    /// 從尾端 push。若已滿，覆蓋最舊的（head 前進）。
    pub fn push(&mut self, value: T) {
        let tail = (self.head + self.len) % self.cap;  // 下一個寫入位置
        self.buf[tail] = Some(value);
        if self.is_full() {
            self.head = (self.head + 1) % self.cap;    // 丟掉最舊的
        } else {
            self.len += 1;
        }
    }

    /// 彈出最舊的元素。
    pub fn pop(&mut self) -> Option<T> {
        if self.is_empty() { return None; }
        let v = self.buf[self.head].take();            // move 出來，留 None
        self.head = (self.head + 1) % self.cap;
        self.len -= 1;
        v
    }

    /// 借用迭代器（最舊 -> 最新）。
    pub fn iter(&self) -> Iter<'_, T> {
        Iter { rb: self, pos: 0 }
    }
}

// --- 借用迭代器 ---
pub struct Iter<'a, T> {
    rb: &'a RingBuffer<T>,
    pos: usize,
}
impl<'a, T> Iterator for Iter<'a, T> {
    type Item = &'a T;
    fn next(&mut self) -> Option<&'a T> {
        if self.pos >= self.rb.len { return None; }
        let idx = (self.rb.head + self.pos) % self.rb.cap;
        self.pos += 1;
        self.rb.buf[idx].as_ref()   // Option<T> -> Option<&T>
    }
}
// 讓 `for x in &rb` 能跑
impl<'a, T> IntoIterator for &'a RingBuffer<T> {
    type Item = &'a T;
    type IntoIter = Iter<'a, T>;
    fn into_iter(self) -> Iter<'a, T> { self.iter() }
}

// --- 擁有迭代器（消耗 buffer）---
pub struct IntoIter<T> { rb: RingBuffer<T> }
impl<T> Iterator for IntoIter<T> {
    type Item = T;
    fn next(&mut self) -> Option<T> { self.rb.pop() }  // pop 已回傳擁有的值
}
impl<T> IntoIterator for RingBuffer<T> {
    type Item = T;
    type IntoIter = IntoIter<T>;
    fn into_iter(self) -> IntoIter<T> { IntoIter { rb: self } }
}

fn main() {
    let mut rb = RingBuffer::with_capacity(3);
    rb.push(1);
    rb.push(2);
    rb.push(3);
    rb.push(4); // 滿了，覆蓋最舊的 1

    print!("contents (oldest->newest): ");
    for x in &rb { print!("{} ", x); }   // 用到 IntoIterator for &RingBuffer
    println!();

    // adapter 串接（在借用迭代器上）
    let doubled_even: Vec<i32> =
        rb.iter().map(|&x| x * 2).filter(|&x| x % 4 == 0).collect();
    println!("map*2 |> filter %4==0 = {:?}", doubled_even);

    let sum: i32 = rb.iter().sum();
    println!("sum = {}", sum);

    // 擁有迭代器消耗整個 buffer
    let owned: Vec<i32> = rb.into_iter().collect();
    println!("owned drain = {:?}", owned);

    // 換一個 T 驗證泛型
    let mut words: RingBuffer<&str> = RingBuffer::with_capacity(2);
    words.push("a"); words.push("b"); words.push("c"); // 覆蓋 "a"
    let joined: String =
        words.iter().map(|s| s.to_uppercase()).collect::<Vec<_>>().join("-");
    println!("words = {}", joined);
}
```

實跑輸出（`rustc 1.97.1`）：

```
contents (oldest->newest): 2 3 4 
map*2 |> filter %4==0 = [4, 8]
sum = 9
owned drain = [2, 3, 4]
words = B-C
```

**解答說明**：

- **`Vec<Option<T>>` 是關鍵設計**。因為不要求 `T: Default`，沒法預先填一個 `Vec<T>`。`Option<T>` 讓每格能表示「空」，`take()` 能把值 move 出來而不留下無效狀態（[Ch 12](./12-core-traits.md) 的 `Option::take` 拿走值留 `None`）。
- **`head + len` 算 tail、`head + pos` 算第 pos 個**，全部取模 `cap`。這兩條公式撐起整個 ring buffer，避免了分別維護 head 和 tail 兩個索引時的 off-by-one 地雷（滿和空都是 head == tail，難分辨）。用 `len` 而非 tail 就沒這問題。
- **三個 `IntoIterator`**：`&RingBuffer`（產 `&T`）、`RingBuffer`（產 `T`）。標準庫容器都提供這組，本練習做了借用版和擁有版兩個最常用的（可變版 `&mut` 留作延伸挑戰）。
- **生命週期**：`Iter<'a, T>` 借用 `&'a RingBuffer`，`Item = &'a T`——`'a` 把「迭代器活多久」和「產出的引用活多久」綁在一起，確保你不會拿到指向已釋放 buffer 的懸空引用。這是編譯器幫你檢查的，不用執行期成本。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| `cap=3`, push 1,2,3；`iter().collect()` | `[1, 2, 3]` | 未滿，正常順序 |
| `cap=3`, push 1,2,3,4；`iter().collect()` | `[2, 3, 4]` | 滿了覆蓋最舊的 1 |
| `cap=3`, push 1..=7；`iter().collect()` | `[5, 6, 7]` | 連續覆蓋，只留最後 3 個 |
| 空 buffer `pop()` | `None` | 邊界：空 |
| `cap=1`, push 1,2,3；`iter().collect()` | `[3]` | 邊界：容量 1，永遠只有最新的 |
| `cap=0` | panic `capacity must be > 0` | 非法輸入 |
| `RingBuffer<&str>` push "a","b","c" (cap=2) | `["b", "c"]` | 泛型：換 T 仍正確 |

跑最後一個泛型 case 特別重要——如果你不小心在某處寫死了 `i32`，換 `&str` 就會編不過，這是驗證「真的泛型」的好方法。

## 用 assert 自我驗證

別只靠肉眼看 `println!` 輸出。寫一組 `assert_eq!` 把上面測試表變成會自己檢查的程式——這是 Rust 開發的日常，也讓你改壞時立刻知道。下面這組我實跑通過（`rustc 1.97.1`），你可以直接拿去驗你的實作：

```rust
fn collect_i32(rb: &RingBuffer<i32>) -> Vec<i32> {
    rb.iter().copied().collect()
}

fn main() {
    // test 1：未滿，正常順序
    let mut a = RingBuffer::with_capacity(3);
    a.push(1); a.push(2); a.push(3);
    assert_eq!(collect_i32(&a), vec![1, 2, 3]);

    // test 2：滿了覆蓋最舊的
    a.push(4);
    assert_eq!(collect_i32(&a), vec![2, 3, 4]);
    assert_eq!(a.len(), 3);          // len 不超過 cap

    // test 3：連續覆蓋，只留最後三個
    let mut b = RingBuffer::with_capacity(3);
    for i in 1..=7 { b.push(i); }
    assert_eq!(collect_i32(&b), vec![5, 6, 7]);

    // test 4：空 buffer pop
    let mut c: RingBuffer<i32> = RingBuffer::with_capacity(2);
    assert_eq!(c.pop(), None);

    // test 5：容量 1，永遠只有最新的
    let mut d = RingBuffer::with_capacity(1);
    d.push(1); d.push(2); d.push(3);
    assert_eq!(collect_i32(&d), vec![3]);

    // test 6：pop 順序（最舊先出）
    let mut e = RingBuffer::with_capacity(3);
    e.push(10); e.push(20);
    assert_eq!(e.pop(), Some(10));
    assert_eq!(e.pop(), Some(20));
    assert_eq!(e.pop(), None);

    println!("all tests passed");
}
```

實跑輸出：

```
all tests passed
```

`assert_eq!` 失敗時會印出「左邊是什麼、右邊是什麼」，比 `assert!(a == b)` 好 debug——永遠用 `assert_eq!` 而非 `assert!(==)`。正式專案裡這些會放進 `#[cfg(test)] mod tests` 用 `cargo test` 跑，這裡為了單檔 `rustc` 能跑，放進 `main`。

## 常見卡點與踩雷

1. **head 和 tail 都存，滿/空分不清**：如果你分別維護 `head` 和 `tail` 兩個索引，滿的時候 `head == tail`、空的時候也 `head == tail`——兩種狀態撞在一起，off-by-one 地獄。參考解用 `head + len` 就沒這問題，`len == 0` 是空、`len == cap` 是滿，一清二楚。這是選 `len` 而非 `tail` 的關鍵理由。
2. **想用 `Vec<T>` 省掉 `Option`**：`T` 不保證有預設值，`Vec::with_capacity(cap)` 給你的是**容量**不是**長度**（裡面沒東西，`vec[i]` 會 panic）。老實用 `Vec<Option<T>>`，`None` 表示空格。
3. **iterator 忘了取模**：`(head + pos)` 沒 `% cap` 會越界 panic。環形的「環」就在這個取模。
4. **生命週期標注寫不出來**：`Iter<'a, T>` 和 `Item = &'a T` 的 `'a` 要一致。如果 `rustc` 抱怨 lifetime，先確認 `iter(&self) -> Iter<'_, T>`（`'_` 讓編譯器推）而不是漏了生命週期參數。
5. **`as_ref()` 忘了**：`self.buf[idx]` 是 `Option<T>`，你要的是 `Option<&T>`——用 `.as_ref()` 轉。直接回 `self.buf[idx]` 會想 move 出 `Option<T>`，在借用的 `&self` 上不允許。

## 延伸挑戰（加分）

- **可變迭代器**：實作 `iter_mut(&mut self) -> IterMut<'_, T>`（`Item = &'a mut T`）和 `impl IntoIterator for &'a mut RingBuffer`。這比借用版難——你要說服 borrow checker「每次 `next` 產出的 `&mut` 互不重疊」，通常要用 `unsafe` 或巧妙的 slice split。先試安全寫法，卡住了看標準庫 `VecDeque::iter_mut` 怎麼做。
- **`DoubleEndedIterator`**：讓 `rb.iter().rev()` 能從最新往最舊走。加 `next_back()`：`Iter` 改成存 `front` 和 `back` 兩個游標，`next` 動 front、`next_back` 動 back，兩者相遇就結束。（這個我實跑驗證過可行，見下方提示。）
- **`FromIterator`**：實作 `impl FromIterator<T>`，讓 `let rb: RingBuffer<i32> = (1..=5).collect();` 能用（需要決定 collect 時的容量策略）。
- **`Extend`**：實作 `impl Extend<T>`，讓 `rb.extend([1,2,3])` 能用。

<details>
<summary>DoubleEndedIterator 提示（實跑驗證過）</summary>

改 `Iter` 成 `{ rb, front: usize, back: usize }`，`iter()` 初始化 `front: 0, back: self.len`。`next` 檢查 `front >= back` 停、否則取 `(head + front) % cap` 並 `front += 1`。`next_back` 檢查 `front >= back` 停、否則 `back -= 1` 再取 `(head + back) % cap`。這樣 `.rev()` 免費就有了，`for i in 1..=4` 後 `iter().rev().collect()` 得 `[4, 3, 2, 1]`。前後夾擊（交替 `next`/`next_back`）也正確：兩游標相遇即停。

</details>

## 自我檢核

- [ ] 能不能不看解答，講清楚「為什麼底層要用 `Vec<Option<T>>` 而不是 `Vec<T>`」？
- [ ] `for x in &rb` 到底呼叫了哪個 trait 的哪個方法？（提示：`IntoIterator::into_iter`，再 `next`）
- [ ] 你的 `iter()` 的生命週期標注，能不能解釋每個 `'a` 綁的是什麼、防止了什麼懸空引用？
- [ ] adapter 鏈 `map().filter().collect()` 是惰性的——在 `collect()` 之前，`map` 的閉包跑了嗎？為什麼？
- [ ] 能不能說出你的實作和參考解答的差異，並解釋各自取捨（例如你用 head+tail 還是 head+len）？

做完這個練習，你已經能寫出「用起來像標準庫」的自訂泛型容器了——這是 Rust 型別系統這個 Part 的實戰檢驗。下一 Part 進記憶體佈局與 unsafe，你會看到 `Vec` 這種容器**內部**是怎麼用 unsafe 手刻出來的（[Ch 21](./21-unsafe-abstractions.md) 甚至要你親手寫一個安全的 `Vec`）。

→ [Ch 15 記憶體佈局：repr 與 niche optimization](./15-memory-layout.md)
