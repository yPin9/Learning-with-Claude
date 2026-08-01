# Ch 7 — borrow checker 底層：NLL 與 Polonius

> **目標**：從「跟編譯器吵架」升級到「懂它在想什麼」。理解 borrow checker 在 MIR（中介表示）上怎麼用 region / loan 分析判斷借用衝突；lexical lifetime 時代 vs NLL（non-lexical lifetimes）的差別；為什麼有些明明安全的 code 過不了（Polonius 要解決的問題）；two-phase borrow 為什麼讓 `v.push(v.len())` 能過；以及 reborrow 在 MIR 裡怎麼展開。

> **環境**：`rustc 1.97.1`（stable，NLL 是它的預設 borrow checker）與 `rustc 1.99.0-nightly`（用 `-Zpolonius=next` 對照）。MIR dump 用 `rustc --emit=mir`。行為隨版本演進，本章的 Polonius 狀態以 2026 年中的 nightly 為準。

前面四章你已經會用 ownership、borrow、lifetime、slice。這章是這個 Part 的收尾，也是唯一一章**深挖原理**：borrow checker 內部到底怎麼運作。目標不是讓你能重寫一個 borrow checker，而是讓你在下次被它擋下時，腦中能浮現「它在 MIR 上看到了什麼、為什麼判我衝突」——從此不再瞎試 `.clone()` 硬過，而是知道問題出在哪。

---

## 為什麼需要這個？

你在前幾章大概已經遇過這種挫折：一段你確信安全的 code，borrow checker 就是不讓過。或者相反——一段你以為會被擋的 code，它放行了（如 `v.push(v.len())`）。這些「反直覺」不是編譯器隨機，背後有一套精確的分析模型。

不懂這套模型，你就只能「試到過為止」：加 `clone`、加 scope `{}`、拆成兩個 statement……有時碰巧過了，但你不知道為什麼，下次換個情境又卡住。懂了模型，你能**預測**哪些寫法會過、哪些不會，並且知道卡住時該怎麼重構（而不是無腦 clone 犧牲效能）。

這也是理解 unsafe 的前置：你要知道 borrow checker 保證了什麼，才知道進 unsafe 區之後**你**要親手維持哪些不變式。

---

## 先建立直覺

borrow checker 不是在你的原始碼上工作的。原始碼先被降級（lower）成 **MIR（Mid-level Intermediate Representation，中階中介表示）**——一種把控制流拆成基本區塊（basic block）、把每個借用變成一個明確「事件」的形式。borrow checker 在 MIR 上做兩件事：

```
1. 算出每個借用（loan）的「有效範圍」——它從哪個點開始，到哪個點還活著
   這個範圍叫 region（區域），可以想成「程式碼中的一組點的集合」

2. 掃描每個會動到記憶體的操作，檢查：
   「這個操作有沒有踩到某個還活著的借用？」
   踩到 → 報錯（E0502 之類）
```

核心心智圖像：**把借用想成一張「租約」（loan）**。你 `&x` 就是簽了一張租約，租約在某段程式碼區域內有效。當有人想對 `x` 做衝突的事（例如 `&mut x` 或 move 走 `x`），borrow checker 檢查「這個時間點有沒有還沒到期的租約」。有 → 拒絕。

關鍵問題是：**租約什麼時候到期？** 這正是 lexical lifetime 和 NLL 的分水嶺。

---

## 一、lexical lifetime vs NLL：租約什麼時候到期

### 舊時代：租約活到 scope 尾端

2018 年之前（Rust 2015 edition 的舊 borrow checker），借用的「租約」活到它所在的**詞法作用域（lexical scope）尾端**——也就是那個 `}`。哪怕你早就不用它了，只要還沒到 `}`，租約就沒到期。

這造成大量假陽性。下面這段在 lexical 時代**編不過**：

```rust
fn main() {
    let mut x = 5;
    let r = &x;         // 借用開始
    println!("{}", r);  // r 最後一次使用 → NLL 下借用在此結束
    // lexical lifetime 時代，r 的借用會活到 scope 尾端，下面這行會被拒
    x += 1;             // NLL 下這行 OK
    println!("{}", x);
}
```

在現代 `rustc 1.97.1` 真跑：

```
$ rustc nll.rs -o nll && ./nll
5
6
```

**過了。** 但在 lexical 時代，`r` 的借用會被認為活到 `main` 的 `}`，於是 `x += 1`（需要 `&mut x`）會撞上「`r` 還借著 `x`」而被拒——即使 `r` 在 `x += 1` 之前的那行就永遠不再被用到了。當年大家得手動加 `{}` 把 `r` 框起來提早結束借用，很醜。

### NLL：租約在「最後一次使用」就到期

**NLL（Non-Lexical Lifetimes，非詞法生命期）** 在 2018 edition 隨 Rust 1.31 穩定，並很快成為所有 edition 的預設。它把借用的有效範圍從「詞法作用域」改成「**基於實際控制流的、到最後一次使用為止**」。

上面那段 NLL 下過，是因為 `r` 的租約範圍只涵蓋 `let r = &x` 到 `println!("{}", r)` 這段——過了最後一次用 `r` 的點，租約就到期，`x += 1` 時已經沒有活著的借用。

用 region 的語言講：`r` 借用產生的 region 是「MIR 上從 `&x` 那個點，流到 `r` 最後被讀的那個點」所經過的所有點的集合。`x += 1` 那個點**不在**這個集合裡，所以不衝突。

### 看一眼真正的 MIR

把上面那段 dump 成 MIR（節錄 `bb0`，basic block 0）：

```
$ rustc --emit=mir nll.rs -o nll.mir
```

```
fn main() -> () {
    let mut _1: i32;          // x
    let _2: &i32;             // r
    ...
    bb0: {
        _1 = const 5_i32;     // x = 5
        _2 = &_1;             // r = &x   ← 這裡產生一張 loan，借的是 _1
        _6 = &_2;             // (為了 println! 準備參數，reborrow _2)
        ...
    }
```

`_1` 是 `x`，`_2` 是 `r`。`_2 = &_1` 這一行就是「產生一張 loan」的事件——borrow checker 記下：從這個點起，有一張借 `_1` 的租約。它接著沿控制流追蹤這張租約活到哪個點（`_2` 最後被用的地方），算出 region。`x += 1` 對應的 MIR 會是 `_1 = Add(copy _1, const 1)`，borrow checker 檢查那個點在不在 `_1` 的任何活躍租約 region 裡——不在，放行。

> **這就是「懂它在想什麼」的具體樣貌**：borrow checker 不看你的 `{}`，它看 MIR 上每個 loan 的產生點、每個變數的最後使用點，用控制流算 region，再逐點檢查衝突。你被擋下時，問自己「我這個借用的 region 延伸到哪？哪個操作踩進去了？」——通常一想就通。

### 一個更貼近日常的 NLL 例子

上面 `x += 1` 那個例子很小。看一個你天天會寫的形狀——「先共享借用讀一個值，用完後可變借用改同一個容器」：

```rust
use std::collections::HashMap;

// NLL 之後能過、lexical 不能過的經典：借用只延伸到最後使用點
fn process(scores: &mut HashMap<String, i32>) {
    // 共享借用 scores 取一個值
    let alice = scores.get("alice").copied().unwrap_or(0);
    // 上面這行是 scores 最後一次「共享借用」的使用（copied() 已把值拷出）

    // 現在可變借用同一個 map —— NLL 下 OK，lexical 下會說「還被共享借用著」
    scores.insert("alice".to_string(), alice + 10);
}

fn main() {
    let mut m = HashMap::new();
    m.insert("alice".to_string(), 5);
    process(&mut m);
    println!("{:?}", m.get("alice"));
}
```

真跑：

```
$ rustc nll_split.rs -o nlls && ./nlls
Some(15)
```

關鍵在 `.copied()`：它把 `get` 回傳的 `&i32` 立刻拷成一個 `i32` 值存進 `alice`。這之後 `scores` 的共享借用就沒有任何引用留存，NLL 判定那張 loan 的 region 到 `.copied()` 那個點就結束——於是 `scores.insert`（需要 `&mut`）暢通。lexical 時代這會被判「共享借用活到函式 `}`」而擋，你得先把值拷進區域變數、關掉一個 scope 才行。NLL 讓這種日常寫法直接成立，你甚至感覺不到它在保護你。

> **橫向連結**：這裡「`&mut` 拿不到因為共享借用還活著」的判斷，和練習 A 裡「`find` 後 `pop_front` 被擋」是**完全同一套規則**——差別只在那裡共享借用（`find` 回傳的引用）真的還活著，這裡因為 `.copied()` 提早死了。同一個 borrow checker，同一套 region 分析，結果不同只因借用的死活點不同。

---

## 二、two-phase borrow：`v.push(v.len())` 為什麼能過

這是個經典的「看起來該被擋、卻過了」的例子：

```rust
fn main() {
    let mut v = vec![1, 2, 3];
    // v.push(...) 需要 &mut v，但參數 v.len() 又需要 &v。
    v.push(v.len());
    println!("{:?}", v);
}
```

真跑：

```
$ rustc two_phase.rs -o tp && ./tp
[1, 2, 3, 3]
```

天真地想：`push` 的簽章是 `fn push(&mut self, value: T)`。呼叫 `v.push(...)` 要先拿到 `&mut v`（接收者），但參數 `v.len()` 又要 `&v`（共享借用）。`&mut v` 和 `&v` 同時存在，該衝突才對。為什麼過了？

因為 **two-phase borrow（兩階段借用）**。方法呼叫的自動 `&mut` 借用被拆成兩個階段：

```
階段一：reserved（保留）
   v.push 的 &mut v 先以「保留態」存在——它佔了位子，
   但此刻表現得像一個共享借用：允許其他共享讀取並存

   ── 此時求值參數 v.len()，它拿的 &v 和「保留態」的 &mut v 相容 ──

階段二：activated（啟用）
   參數都求值完，真正要呼叫 push 的瞬間，
   &mut v 才「啟用」成真正的獨佔借用
```

關鍵在：**獨佔性（exclusivity）只在真正需要的那一刻（呼叫 push 的瞬間）才生效**，而不是從 `&mut v` 語法出現的那一刻。參數求值發生在「保留」和「啟用」之間的窗口，那時 `&mut v` 還沒獨佔，所以 `v.len()` 的 `&v` 能並存。

MIR 把這個窗口攤在你眼前。`rustc --emit=mir` dump 上面那段，節錄核心四行：

```
_4 = &mut _1;                         // ← &mut v 產生（保留態），_1 是 v
_6 = &_1;                             // ← &v 產生，給 v.len() 用；和上面的 &mut 並存！
_5 = Vec::<usize>::len(move _6) -> …  // ← 求值 v.len()，用掉 _6
_3 = Vec::<usize>::push(move _4, move _5) -> …  // ← 這裡 _4 才「啟用」成真獨佔
```

看清楚順序：`_4 = &mut _1`（保留態的 `&mut v`）**先於** `_6 = &_1`（`&v`）產生，兩者在 MIR 上同時存在了好幾個 statement——若 `&mut` 一產生就獨佔，這是明顯的衝突。two-phase borrow 的規則是：`_4` 在「產生」到「第一次被當 `&mut` 用（即傳給 `push`）」之間只算保留態，此時和 `_6` 這種共享借用相容；直到最後一行 `push(move _4, …)` 那個點，`_4` 才啟用成獨佔——而那時 `_6` 早就被 `len()` 用掉、租約到期了。獨佔和共享從不真的在同一點重疊。

這不是特例 hack，而是 NLL 一起引入的正式機制——沒有它，一大堆像 `v.push(v.len())`、`vec.push(vec[0])` 這種自然寫法都要被迫拆成兩行（先 `let n = v.len();` 再 `v.push(n);`），很煩。two-phase borrow 讓借用的「獨佔」語意精確地只覆蓋真正獨佔的那一點。

這不是特例 hack，而是 NLL 一起引入的正式機制——沒有它，一大堆像 `v.push(v.len())`、`vec.push(vec[0])` 這種自然寫法都要被迫拆成兩行（先 `let n = v.len();` 再 `v.push(n);`），很煩。two-phase borrow 讓借用的「獨佔」語意精確地只覆蓋真正獨佔的那一點。

> C++ 對照：C++ 沒有這種問題也沒這種保護——`v.push_back(v.size())` 直接跑，但如果你寫 `v.push_back(v[0])` 而 `push_back` 觸發 realloc，`v[0]` 這個引用可能在 realloc 後才被讀，就是 UB。Rust 的 two-phase borrow 讓合法情況能過，同時 borrow checker 仍保證不會有真正的 aliasing 衝突。

---

## 三、reborrow：`&mut` 傳進函式後為什麼還能用

C 的指標傳進函式，函式外照樣能用——指標是 copy 的。但 Rust 的 `&mut T` **不是 Copy**（如果能 copy，就有兩個 `&mut` 指向同一個東西，違反獨佔）。那為什麼下面這段第二次 `bump(r)` 沒報「use after move」？

```rust
fn bump(n: &mut i32) {
    *n += 1;
}

fn main() {
    let mut x = 10;
    let r = &mut x;   // r: &mut i32
    bump(r);          // 這裡是 reborrow：&mut *r 傳進去，r 沒被 move
    bump(r);          // r 仍可用 —— 若是 move，第二次就會報 use-after-move
    *r += 100;
    println!("{}", r);
}
```

真跑：

```
$ rustc reborrow.rs -o rb && ./rb
112
```

答案是 **reborrow（重借用）**。當你把 `r`（型別 `&mut i32`）傳給要 `&mut i32` 的函式，編譯器**不是** move 走 `r`，而是自動插入一個 reborrow：實際傳進去的是 `&mut *r`——「借用 `r` 所指向的東西」，產生一個新的、臨時的 `&mut`。

```
r ──────► x            r 是 &mut i32，指向 x

bump(r) 實際上是 bump(&mut *r)：
                       臨時建一個新 &mut，它「借」了 r
   temp ──► x
     （在 bump 執行期間，r 被這個 temp「凍結」，不能用）
   bump 返回後，temp 到期，r 解凍，可以再用
```

這個臨時 reborrow 的租約只活在 `bump` 呼叫期間；`bump` 一返回，租約到期，`r` 恢復可用。所以第二次 `bump(r)` 又建一個新的臨時 reborrow，完全 OK。`*r += 100` 也是同理。

MIR 直接證實「不是 move、是 reborrow」。dump 上面那段（`_1` 是 `r`）：

```
_2 = &mut _1;                    // ← reborrow：從 r 建一個新的臨時 &mut _2
_3 = bump(copy _2) -> …          // 第一次 bump，傳 copy _2
_4 = bump(copy _2) -> …          // 第二次 bump，_2 還在，再傳一次
```

如果 `bump(r)` 是把 `r` **move** 進去，MIR 會是 `bump(move _1)`，而且第二行就會因為 `_1` 已被 move 而報錯。實際上你看到的是 `_2 = &mut _1`——編譯器插了一個從 `r`（`_1`）重借用出來的臨時 `&mut`（`_2`），`r` 本身原封不動。這就是 reborrow 在 MIR 層級的樣貌：一個你原始碼裡沒寫、編譯器自動補上的 `&mut *r`。

reborrow 是 Rust 讓 `&mut` 好用的關鍵——沒有它，`&mut` 傳一次就被 move 掉，你得寫一堆 `bump(&mut *r)` 顯式重借用，或者根本沒法把同一個 `&mut` 用兩次。多數時候編譯器自動插入，你感覺不到它，但它在背後維持了「任何時刻只有一條 `&mut` 路徑能動這塊記憶體」的獨佔不變式。

---

## 四、為什麼有些「明明安全」的 code 過不了：Polonius 要解的

NLL 是巨大的進步，但它的分析模型有已知的**假陽性**——某些真的安全的 code，NLL 也擋。最出名的是「條件式回傳引用」：

```rust
use std::collections::HashMap;

// 經典 NLL known-limitation：條件式回傳引用。
// 邏輯上安全，但 NLL 的 region 模型看不出來。
fn get_or_insert(map: &mut HashMap<u32, String>, key: u32) -> &String {
    if let Some(v) = map.get(&key) {
        return v;          // 分支 A：回傳既有借用
    }
    map.insert(key, String::from("default"));  // 分支 B：這裡要 &mut map
    &map[&key]
}

fn main() {
    let mut m = HashMap::new();
    m.insert(1, String::from("one"));
    println!("{}", get_or_insert(&mut m, 1));
    println!("{}", get_or_insert(&mut m, 2));
}
```

在 stable `rustc 1.97.1` 真跑：

```
error[E0502]: cannot borrow `*map` as mutable because it is also borrowed as immutable
  --> polonius.rs:10:5
   |
 6 | fn get_or_insert(map: &mut HashMap<u32, String>, key: u32) -> &String {
   |                       - let's call the lifetime of this reference `'1`
 7 |     if let Some(v) = map.get(&key) {
   |                      --- immutable borrow occurs here
 8 |         return v;
   |                - returning this value requires that `*map` is borrowed for `'1`
 9 |     }
10 |     map.insert(key, String::from("default"));
   |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable borrow occurs here
```

**這段其實安全**：`map.get(&key)` 的借用只在 `if let Some(v)` 那個分支裡才逃出去（`return v`）；走到 `map.insert` 那行時，一定是 `map.get` 回傳了 `None`，那個共享借用早就沒有任何引用留存了。人腦一看就懂，但 NLL 的分析模型看不出這個「因控制流分支而互斥」的關係——它保守地認為 `map.get` 的借用可能延伸到函式結尾，於是和 `map.insert` 的 `&mut` 衝突。

### Polonius 是什麼

**Polonius** 是下一代 borrow checker 的分析引擎，用完全不同的方法建模：NLL 算的是「每個 loan 活在哪些點」，Polonius 反過來算「每個點上，哪些 loan 是活的、以及一個引用可能源自哪些 loan」——這種「origin / provenance」導向的分析能精確追蹤上面那種分支互斥關係，於是能接受這段 code。

用 nightly 開 Polonius 對照，**同一段 code**：

```
$ rustc +nightly -Zpolonius=next polonius.rs -o pol2 && ./pol2
one
default
```

**過了，而且跑出正確結果。** 同一份原始碼，NLL 擋、Polonius 放行——這就是 Polonius 要解決的問題的最直接證據。

> **認識論誠實 / 版本狀態**：Polonius 尚未穩定。`-Zpolonius=next` 是 nightly-only 的實驗旗標，仍在開發，效能與完整性都還沒到能取代 NLL 的程度；穩定時間表未定。**你現在寫 production 程式碼，能倚靠的仍是 NLL。** 碰到上面這種假陽性時，實務解法是重構（見下方），而不是等 Polonius。本節的意義是讓你知道「這不是你的錯，是 NLL 已知的極限」——被這種 case 擋下時別懷疑自己的邏輯。

### 碰到這種假陽性怎麼辦（現在）

不能等 Polonius，得重構。常見手法：把「查詢」和「插入」在控制流上徹底分開，避免借用跨越分支。上面那個 `get_or_insert` 的實務寫法是用 `entry` API：

```rust
// 用 entry API 繞過 NLL 假陽性：一次借用完成「有就取、沒有就插」
use std::collections::HashMap;
fn get_or_insert(map: &mut HashMap<u32, String>, key: u32) -> &String {
    map.entry(key).or_insert_with(|| String::from("default"))
}
```

`entry` 把整個「查+插」合成單一次借用，NLL 完全接受。這也是為什麼 std 提供 `entry`——一部分正是為了繞開這類 borrow checker 限制。

---

## 對比與取捨

| 機制 | 解決什麼 | 狀態 | 你會不會手動碰 |
|---|---|---|---|
| lexical lifetime | （舊）借用活到 scope 尾 | 已被取代 | 只在讀舊 code / 舊文章時 |
| NLL | 借用活到最後使用點，大量減少假陽性 | stable 預設 | 每天都在用，感覺不到 |
| two-phase borrow | `v.push(v.len())` 這類自然寫法 | 隨 NLL 穩定 | 感覺不到，它默默放行 |
| reborrow | `&mut` 傳進函式後還能用 | 一直都在 | 感覺不到，編譯器自動插 |
| Polonius | NLL 的已知假陽性（條件回傳引用等） | nightly 實驗中 | 目前碰不到；重構繞過 |

---

## 踩雷集錦

1. **以為「加個 `{}` 提早結束借用」是現代 Rust 的必要技巧**：那是 lexical 時代的產物。NLL 之後借用在最後使用點就結束，多數情況不需要手動加 scope。你若看到老文章教你狂加 `{}`，那是 2018 前的知識。

2. **把 NLL 假陽性當成自己邏輯錯**：像 `get_or_insert` 那種條件回傳引用被擋，**不是你寫錯**，是 NLL 分析模型的已知極限。認出它（通常是「跨 if 分支的借用」形狀），改用 `entry` 之類的 API 或重構，別懷疑自己。

3. **不懂 two-phase borrow，把 `v.push(v.len())` 拆成兩行以為是必須的**：不必。two-phase borrow 讓它直接過。當然拆成兩行也對，但你要知道不拆也行——理解機制才不會養成無謂的防禦性寫法。

4. **以為 `&mut` 傳進函式就被消耗了**：不是 move，是 reborrow。`&mut` 傳進函式後，函式返回你還能用。只有你**顯式** move（如把 `&mut` 存進另一個變數 `let r2 = r;`）才真的轉移。

5. **想靠讀 MIR 來 debug 每個 borrow error**：`--emit=mir` 對建立心智模型很有用，但日常 debug borrow error 不需要真的讀 MIR——`rustc` 的錯誤訊息已經標出借用產生點、衝突點、最後使用點（看那些 `----` 底線標註）。MIR 是「想更深入」時的工具，不是日常必需。

---

## 進階：再往深一層

**region 到底是什麼的集合？** 在 NLL 的模型裡，一個 region（也叫 lifetime）不是一段連續程式碼，而是 **MIR 控制流圖上一組「點」的集合**（每個點大致對應一條 MIR statement 的前/後）。一個 loan 的 region 就是「這張租約必須有效的所有點」。衝突檢查 = 「這個 loan 的 region 有沒有覆蓋一個對同一位置做衝突存取的點」。這個「region = 點集合」的模型是 NLL 論文（RFC 2094）的核心。

**Polonius 的 datalog 建模**：Polonius 把借用檢查表述成一組 datalog 規則（`subset`、`requires`、`loan_live_at` 等關係），用邏輯推導求解。這讓它能表達 NLL 表達不了的關係（如 origin 的傳遞）。想深入的話，Niko Matsakis 的 Polonius 系列 blog 和 `polonius` repo 的 README 是第一手資料。

**borrow checker 之後還有 drop check（dropck）**：借用檢查通過不代表結束，編譯器還會檢查「drop 順序不會造成 drop 一個已被借用/已失效的東西」——這牽涉 `#[may_dangle]`、`PhantomData`，是寫 unsafe 抽象時的地雷。[Ch 21 手刻 unsafe 抽象](./21-unsafe-abstractions.md) 會碰到 dropck。

---

## 動手練習

1. 把第一節的 NLL 例子 dump 成 MIR（`rustc --emit=mir nll.rs -o nll.mir`），找出 `r = &x` 對應的那行（形如 `_N = &_M`），並找出 `x += 1` 對應的 `Add`。確認它們在不同的位置——建立「loan 產生點」和「衝突檢查點」的具體感。

2. 把第四節的 `get_or_insert`（會被 NLL 擋的版本）改成 `entry` 版本，確認它在 **stable** 就能過。然後思考：為什麼 `entry` 能過而手寫的 if-return 不行？（提示：借用次數與跨分支）

3. 若你裝了 nightly：對 `get_or_insert` 的原始版本跑 `rustc +nightly -Zpolonius=next`，親眼看它從 error 變成能跑。這是你能親手驗證「NLL 有極限、Polonius 更強」的最直接方式。

---

## 本章重點整理

- borrow checker 在 **MIR** 上工作：把每個借用看成一張 **loan（租約）**，用控制流算出它的 **region**（有效的點集合），再逐點檢查衝突存取。
- **NLL** 讓借用在「最後一次使用」到期，取代舊的「活到 scope 尾端」，消除大量假陽性——這是 stable 的預設。
- **two-phase borrow** 讓 `v.push(v.len())` 能過：`&mut` 的獨佔性延到真正呼叫的那一刻才生效。
- **reborrow** 讓 `&mut` 傳進函式後還能用：傳的是臨時的 `&mut *r`，租約只活在呼叫期間。
- **Polonius** 是下一代分析，能接受 NLL 誤擋的「條件回傳引用」等安全 code，但**尚未穩定**；現在碰到假陽性要靠重構（如 `entry`）繞過。

## 自我檢核

- [ ] 不看筆記，能解釋 borrow checker 為什麼在 MIR 而非原始碼上工作，以及 loan / region 各是什麼。
- [ ] 能說出 lexical lifetime 和 NLL 對「借用何時到期」的差別，並舉一個 NLL 之後能過、之前不能的例子。
- [ ] 有人問你「`v.push(v.len())` 為什麼不衝突」，你能用 two-phase borrow 的「保留→啟用」兩階段回答。
- [ ] 能解釋為什麼條件回傳引用會被 NLL 誤擋、Polonius 想怎麼解、以及為什麼**現在**不能倚靠 Polonius。

## 延伸閱讀

### 論文 / RFC

- **[RFC 2094 — Non-Lexical Lifetimes](https://rust-lang.github.io/rfcs/2094-nll.html)** — Niko Matsakis 等（rust-lang RFCs, 2017）
  - **核心貢獻**：定義了 NLL 的「region = MIR 控制流圖上的點集合」模型，本章第一節的理論來源。
  - **讀哪裡**：「Motivation」看那些 lexical 時代被誤擋的例子；「Detailed design」的 region 定義是核心，數學符號多但概念就是本章講的那套。
  - **和本章的關聯**：本章「租約 / region / 逐點檢查」的正式版就是這份 RFC。

### 部落格 / 技術文章

- **[“An alias-based formulation of the borrow checker”](https://smallcultfollowing.com/babysteps/blog/2018/04/27/an-alias-based-formulation-of-the-borrow-checker/)** — Niko Matsakis（babysteps, 2018）
  - **這篇說什麼**：Polonius 的原始提案，解釋為什麼要從 NLL 的「loan liveness」轉向「alias / origin」建模，以及它能解決哪些 NLL 解不了的 case。
  - **讀哪裡**：整篇；作者是 Rust 型別系統的主要設計者之一，這是 Polonius 的第一手來源。
  - **前提**：先讀懂本章 NLL 一節，再看它講「NLL 的極限」會很有共鳴。

- **[Rustc Dev Guide — MIR borrow check](https://rustc-dev-guide.rust-lang.org/borrow_check.html)** — rustc 開發者文件
  - **這篇說什麼**：從編譯器實作角度講 borrow check 的各階段（region inference、two-phase borrow、dropck），是「想真的讀 rustc 原始碼」的地圖。
  - **讀哪裡**：「Tracking loans」「Two-phase borrows」小節直接對應本章第一、二節。
  - **為什麼值得讀**：這是官方維護的編譯器內部文件，權威且持續更新。

### 官方文件

- **[Rust Edition Guide — NLL](https://doc.rust-lang.org/edition-guide/rust-2018/ownership-and-lifetimes/non-lexical-lifetimes.html)**
  - **讀哪裡**：整頁，用 before/after 對照展示 NLL 讓哪些程式碼從編不過變成能過。
  - **和本章的關聯**：本章第一節的「lexical vs NLL」差異，這裡有更多對照範例。

這章結束 Part 1（所有權模型）。你現在懂了 ownership、borrow、lifetime 從語法到 MIR 底層的完整鏈條。接下來的練習 A 會把這些拼起來：拿一段有記憶體 bug 的 C 程式，用 safe Rust 改寫，親身體會 ownership/borrow 怎麼從**根本**擋掉那個 bug。

→ [練習 A：把 C 資料結構改寫成 Rust](./practice-a-c-to-rust.md)
