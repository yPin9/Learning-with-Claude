# Ch 11 — Trait Object 與動態分派

> **目標**：搞懂 `dyn Trait` 到底是什麼——它是一個**胖指標（fat pointer）**，一半指資料、一半指 vtable。看清 Rust 的 vtable 佈局跟 C++ 差在哪、差異的後果是什麼，並且知道什麼時候該用動態分派、什麼時候該用泛型單型化（monomorphization）。

> **環境**：`rustc 1.97.1`，x86-64 Linux（WSL2）。本章所有 `size_of` 與組語相關數字都在這個環境實跑；vtable 佈局是實作細節（非穩定 ABI），不同版本可能改動，本章標注的是 1.97 的實際觀察。

## 為什麼需要這個？

上一章（[Ch 10 泛型與單型化](./10-generics-monomorphization.md)）你看到泛型怎麼在編譯期展開：`Vec<T>` 對每個具體 `T` 生一份專屬程式碼，零執行期成本，但每個型別一份 code。

問題來了：**如果我要一個容器裝「一堆實作了同一個 trait 但型別不同的東西」呢？**

```rust
// 我想要這樣：一個 Vec 裝各種形狀
let shapes = vec![Circle { r: 1.0 }, Square { s: 2.0 }];  // 編不過
```

`Vec<T>` 的 `T` 只能是**一個**具體型別。`Circle` 和 `Square` 是兩個型別，塞不進同一個 `Vec<Circle>`。泛型單型化在這裡幫不上忙——單型化的前提是「編譯期就知道確切型別」，而「一堆異質物件」這件事本質上要到執行期才知道每個元素是誰。

這正是 C++ 裡你用**虛擬函式（virtual function）+ 基底類別指標**解決的問題：

```cpp
std::vector<Shape*> shapes;   // 存基底指標，執行期靠 vptr 找對的函式
shapes.push_back(new Circle(1.0));
shapes.push_back(new Square(2.0));
for (auto* s : shapes) s->area();  // 動態分派
```

Rust 的對應物就是 **trait object**：`Box<dyn Shape>`。名字不一樣，機制的核心（vtable + 執行期查表）是一樣的，但**放 vtable 指標的位置**跟 C++ 有一個關鍵差異，這個差異決定了 Rust 的很多語言設計。這章就是講清楚這件事。

## 先建立直覺

C++ 的物件把「我是誰」的資訊（vptr）**藏在物件裡面**。每個有虛擬函式的物件，開頭都偷偷多一個指標指向它那個 class 的 vtable：

```
C++ 物件（含 vptr）：
┌──────────┐
│  vptr    │──▶ Circle 的 vtable ──▶ [area(), name(), ...]
├──────────┤
│  r = 1.0 │
└──────────┘
    物件本身變大了（多一個指標欄位）
```

Rust 走另一條路：**物件本身乾乾淨淨，什麼都不多帶**。「我是誰」的資訊放在**指標那一側**——當你把 `&Circle` 轉成 `&dyn Shape` 時，這個引用從「一個指標」膨脹成「兩個指標」：一個指資料、一個指 vtable。

```
Rust trait object（胖指標）：
&dyn Shape ─┬─▶ data ptr  ──▶ ┌──────────┐  ← Circle 物件，只有 r，沒有 vptr
            │                  │ r = 1.0  │
            │                  └──────────┘
            └─▶ vtable ptr ──▶ [drop, size, align, area(), name()]
```

一句話記住差異：**C++ 把「型別身分」綁在物件上（每個物件付一份 vptr 的代價）；Rust 把它綁在指標上（物件免費，指標變胖）。**

> 如果你對「trait 是什麼、怎麼 `impl`」還不熟，先回看 [Ch 9 Trait](./09-traits.md)。這章假設你已經會定義 trait 和實作。

## 胖指標：親眼看它是 16 bytes

先把最重要的事實跑出來。一個普通引用是 8 bytes（x86-64 上一個指標），一個 trait object 引用是 **16 bytes**：

```rust
use std::mem::size_of;

trait Shape {
    fn area(&self) -> f64;
    fn name(&self) -> &str;
}

struct Circle { r: f64 }
struct Square { s: f64 }

impl Shape for Circle {
    fn area(&self) -> f64 { 3.14159 * self.r * self.r }
    fn name(&self) -> &str { "circle" }
}
impl Shape for Square {
    fn area(&self) -> f64 { self.s * self.s }
    fn name(&self) -> &str { "square" }
}

fn main() {
    println!("size_of::<&Circle>()      = {}", size_of::<&Circle>());
    println!("size_of::<&dyn Shape>()   = {}", size_of::<&dyn Shape>());
    println!("size_of::<Box<Circle>>()  = {}", size_of::<Box<Circle>>());
    println!("size_of::<Box<dyn Shape>>() = {}", size_of::<Box<dyn Shape>>());
    println!("size_of::<*const dyn Shape>() = {}", size_of::<*const dyn Shape>());

    let shapes: Vec<Box<dyn Shape>> = vec![
        Box::new(Circle { r: 1.0 }),
        Box::new(Square { s: 2.0 }),
    ];
    for s in &shapes {
        println!("{} area = {:.4}", s.name(), s.area());
    }
}
```

實跑輸出：

```
size_of::<&Circle>()      = 8
size_of::<&dyn Shape>()   = 16
size_of::<Box<Circle>>()  = 8
size_of::<Box<dyn Shape>>() = 16
size_of::<*const dyn Shape>() = 16
circle area = 3.1416
square area = 4.0000
```

看清楚幾件事：

- `&Circle` = 8 bytes：普通瘦指標（thin pointer）。
- `&dyn Shape` = 16 bytes：胖指標，兩個 usize。
- `Box<Circle>` 也是 8——`Box` 對 sized 型別就是個裸指標。
- `Box<dyn Shape>` = 16：`Box` 對 `dyn` 型別一樣是胖指標。**`Box<dyn Trait>` 的胖不是 `Box` 造成的，是 `dyn` 造成的。**
- 連 `*const dyn Shape`（裸指標）都是 16——只要是指向 `dyn` 的指標，一律胖。

這跟 slice 的胖指標（[Ch 6](./06-slices-str-string.md) 的 `&[T]` = ptr + len）是同一個概念的兩個實例：Rust 對「編譯期不知道完整資訊的東西」用胖指標補齊。slice 補的是**長度**，trait object 補的是**vtable**。

## 底層機制：vtable 到底長什麼樣？

「vtable 裡有 method function pointer」大家都知道。但 Rust 的 vtable 除了方法，**開頭還藏了三個東西**：drop、size、align。這不是憑空講的，我們把 vtable 直接 dump 出來。

用 `transmute` 把 `&dyn Trait` 拆成兩個 `usize`（這是 unsafe，正常 code 不要這樣做，這裡純粹為了教學看內部）：

```rust
use std::mem::transmute;

trait Animal {
    fn speak(&self) -> u64;
    fn legs(&self) -> u64;
}
struct Dog;
impl Animal for Dog {
    fn speak(&self) -> u64 { 42 }
    fn legs(&self) -> u64 { 4 }
}

fn main() {
    let d = Dog;
    let obj: &dyn Animal = &d;
    // &dyn Trait 記憶體佈局就是 (data_ptr, vtable_ptr)
    let raw: [usize; 2] = unsafe { transmute(obj) };
    let vtable_ptr = raw[1] as *const usize;
    unsafe {
        // vtable 佈局：[0]=drop_in_place, [1]=size, [2]=align, [3..]=方法
        println!("data ptr     = {:#x}", raw[0]);
        println!("vtable ptr   = {:#x}", raw[1]);
        println!("vt[0] drop   = {:#x}", *vtable_ptr.add(0));
        println!("vt[1] size   = {}", *vtable_ptr.add(1));
        println!("vt[2] align  = {}", *vtable_ptr.add(2));
        println!("vt[3] method = {:#x}", *vtable_ptr.add(3));
        println!("vt[4] method = {:#x}", *vtable_ptr.add(4));
    }
}
```

實跑輸出（位址每次跑會變，size/align 不變）：

```
data ptr     = 0x7ffde7a1fac7
vtable ptr   = 0x590a8cbbc938
vt[0] drop   = 0x0
vt[1] size   = 0
vt[2] align  = 1
vt[3] method = 0x590a8cb7e4f0
vt[4] method = 0x590a8cb7e4e0
```

`Dog` 是 zero-sized type（ZST，沒有欄位），所以 size = 0、drop = 0（沒有 drop glue）。把 `Dog` 換成有欄位、有 `Drop` 的型別，size 和 drop 就會有值。vtable 的佈局是：

```
每個 (具體型別, trait) 組合，編譯器靜態生成一份 vtable：

vtable for (Dog as Animal):
┌────────────────────┐
│ [0] drop_in_place  │ ── 怎麼解構這個型別（Ch 12 會講 Drop）
├────────────────────┤
│ [1] size           │ ── 型別大小（bytes）
├────────────────────┤
│ [2] align          │ ── 對齊需求（bytes）
├────────────────────┤
│ [3] speak()        │ ── 第一個 trait 方法
├────────────────────┤
│ [4] legs()         │ ── 第二個 trait 方法
└────────────────────┘
```

**為什麼要塞 drop/size/align 進 vtable？** 因為 trait object 抹掉了具體型別，執行期只剩胖指標。當 `Box<dyn Shape>` 要被釋放時，執行期得知道：這底下的東西多大（size，才知道要 free 多少）、怎麼解構（drop_in_place，才知道要不要跑 destructor）、對齊多少（align，配置器需要）。這些資訊 C++ 用不同機制處理，Rust 統一塞進 vtable。這也是為什麼 `Box<dyn Trait>` drop 時能正確釋放，不需要 C++ 那種「基底類別記得加 `virtual ~Base()`」的紀律。

呼叫一個方法時發生什麼：

```
s.area()  where  s: &dyn Shape
   │
   ├─ 1. 取胖指標的 vtable_ptr（第二個 usize）
   ├─ 2. 從 vtable 固定偏移取出 area 的函式指標（例如 vt[3]）
   ├─ 3. 取胖指標的 data_ptr（第一個 usize）當 &self 傳進去
   └─ 4. call 那個函式指標
```

這就是「動態分派」的全部：一次 load vtable、一次 load 函式指標、一次間接 call。跟 C++ 的 `vptr -> vtable -> slot -> call` 步數幾乎一樣，差別只在 vptr 從哪來（C++ 從物件裡讀，Rust 從胖指標的第二欄讀）。

## C++ vtable 對照：差異的後果

| 面向 | C++ 虛擬函式 | Rust trait object |
|---|---|---|
| vtable 指標放哪 | 物件內部（每個物件一個 vptr） | 胖指標裡（物件不含） |
| 物件大小 | 因 vptr 而變大 8 bytes | 不變（乾淨） |
| 指標大小 | 瘦（8 bytes） | 胖（16 bytes） |
| 一個型別能有幾個「介面」 | 每個 class 一份 vtable（多重繼承較亂） | 每個 (型別, trait) 一份 vtable，很乾淨 |
| 誰決定是否虛擬 | 宣告時 `virtual` | 使用時 `dyn`（型別本身無此標記） |
| 解構資訊 | `virtual ~T()`（要自己記得加） | vtable 內建 drop_in_place |

最關鍵的後果——**`dyn Trait` 是 unsized（DST，動態大小型別）**：

因為 vtable 在指標側、不在物件側，`dyn Shape` 這個「型別」本身在編譯期**不知道自己多大**（可能是 `Circle` 也可能是 `Square`，大小不同）。所以你**不能**直接持有一個 `dyn Shape`：

```rust
let s: dyn Shape = Circle { r: 1.0 };  // 編不過：dyn Shape 沒有已知大小
```

你只能透過指標間接持有它：`&dyn Shape`、`&mut dyn Shape`、`Box<dyn Shape>`、`Rc<dyn Shape>`……指標是 sized 的（就算是胖指標，也是固定 16 bytes），所以能放。這就是為什麼 trait object 幾乎總是包在某種指標裡出現。

C++ 沒有這個限制——因為 vptr 在物件內，`Circle` 物件本身就是完整、大小已知的，你可以 `Circle c;` 直接放 stack。但代價是每個物件都背著 vptr，而且你**不能**把一個 `Circle` 值當成 `Shape` 值來持有（object slicing 問題：`Shape s = circle;` 會切掉衍生部分），只能用指標/引用。繞了一圈，兩邊都是「多型要透過指標」，只是 Rust 用型別系統把這件事講明白了。

## 靜態分派 vs 動態分派：怎麼選

同一個「呼叫 trait 方法」，Rust 給你兩條路：

```rust
trait Draw { fn draw(&self) -> u32; }
struct A; struct B;
impl Draw for A { fn draw(&self) -> u32 { 1 } }
impl Draw for B { fn draw(&self) -> u32 { 2 } }

// 靜態分派：單型化，每個 T 生一份 code，可 inline
fn render_static<T: Draw>(x: &T) -> u32 { x.draw() }

// 動態分派：一份 code，執行期查 vtable
fn render_dyn(x: &dyn Draw) -> u32 { x.draw() }

fn main() {
    let a = A; let b = B;
    println!("static: {} {}", render_static(&a), render_static(&b));
    println!("dyn:    {} {}", render_dyn(&a), render_dyn(&b));

    // 異質集合：只有 dyn 做得到
    let items: Vec<Box<dyn Draw>> = vec![Box::new(A), Box::new(B)];
    let total: u32 = items.iter().map(|x| x.draw()).sum();
    println!("total = {}", total);
}
```

實跑輸出：

```
static: 1 2
dyn:    1 2
total = 3
```

`render_static::<A>` 和 `render_static::<B>` 是編譯後**兩個獨立函式**，各自可以把 `draw()` inline 掉、常數摺疊，最理想情況下整個呼叫消失。`render_dyn` 只有一份 code，`x.draw()` 是一次無法在編譯期解析的間接 call——**優化器看不穿 vtable，通常無法 inline**。

取捨表：

| 面向 | 靜態分派（泛型 `<T: Trait>`） | 動態分派（`dyn Trait`） |
|---|---|---|
| 分派成本 | 零（直接 call，常可 inline） | 一次間接 call（load vtable + call） |
| 內聯（inline） | 可以，優化空間大 | 幾乎不行 |
| Code size | 每個具體型別一份（可能膨脹） | 一份，共用 |
| 編譯時間 | 型別多時變慢（單型化爆炸） | 較快 |
| 執行期彈性 | 型別編譯期固定 | 可裝異質集合、執行期才決定 |
| 二進位大小 | 大 | 小 |
| I-cache 壓力 | 多份 code 可能擠爆 cache | 一份 code 較友善 |

**怎麼選（我的判斷）：**

- **預設用泛型**（靜態分派）。零成本、可 inline，符合 Rust「不為沒用到的東西付錢」的哲學。
- **需要異質集合**（`Vec<Box<dyn T>>`）→ 只能 dyn，沒得選。
- **這是熱路徑、且分派成本會被放大**（迴圈裡呼叫幾百萬次）→ 傾向泛型，讓它 inline。
- **型別多到單型化把 binary 撐爆、或編譯時間爆炸**（例如插件系統、UI widget 樹）→ 用 dyn 換 code size，這時「一次間接 call」的成本遠小於「幾百份重複 code 擠爆 I-cache」的成本。
- **API 邊界要穩定、不想洩漏具體型別**（回傳 `Box<dyn Iterator>`）→ dyn。

反直覺的點：dyn **不一定比較慢**。當泛型單型化導致 code 太大、I-cache miss 變多，一份共用的 dyn code 反而可能整體更快。這是有條件的宣稱——取決於呼叫頻率、型別數量、cache 壓力，沒有實測前不要斷言哪個快。

## Object safety（dyn 相容性）：為什麼有些 trait 不能做成 object

不是每個 trait 都能 `dyn`。要能建 vtable，trait 必須滿足**dyn 相容性（dyn compatibility，舊稱 object safety）**。

> 術語變遷：這個概念以前叫 "object safety"，Rust 官方在 2024 年把措辭改成 "dyn compatibility"。`rustc 1.97` 的錯誤訊息用的是新詞 "dyn compatible"。兩個詞指同一件事，看到舊詞不要以為是不同東西。

核心規則（違反其一就不能 dyn）：

1. **方法不能回傳 `Self`**：`fn make() -> Self` 這種——vtable 是型別抹除後的東西，執行期不知道 `Self` 具體是誰，沒法回傳一個「我也不知道多大的東西」。
2. **方法不能有泛型型別參數**：`fn foo<T>(&self, x: T)`——泛型方法要單型化成無限多份，vtable 是有限的 slot 表，塞不下「所有可能的 T」。
3. **不能有非 `self` 的關聯函式**（沒有 `&self`/`&mut self`/`self` 接收者的方法）——沒有 receiver 就沒有 data ptr 可以配。

規則 1 實跑一個 E0038：

```rust
trait NotObjectSafe {
    fn make() -> Self;   // 回傳 Self -> 不 dyn 相容
}

fn use_it(_x: &dyn NotObjectSafe) {}

fn main() {}
```

編譯，真實錯誤：

```
error[E0038]: the trait `NotObjectSafe` is not dyn compatible
 --> ch11b.rs:6:16
  |
6 | fn use_it(_x: &dyn NotObjectSafe) {}
  |                ^^^^^^^^^^^^^^^^^ `NotObjectSafe` is not dyn compatible
  |
note: for a trait to be dyn compatible it needs to allow building a vtable
      for more information, visit <https://doc.rust-lang.org/reference/items/traits.html#dyn-compatibility>
 --> ch11b.rs:3:8
  |
2 | trait NotObjectSafe {
  |       ------------- this trait is not dyn compatible...
3 |     fn make() -> Self;   // 回傳 Self -> 不 dyn 相容
  |        ^^^^ ...because associated function `make` has no `self` parameter
help: consider turning `make` into a method by giving it a `&self` argument
```

規則 2（泛型方法）的 E0038：

```rust
trait Generic {
    fn foo<T>(&self, x: T);  // 泛型方法 -> 不 dyn 相容
}
fn use_it(_x: &dyn Generic) {}
fn main() {}
```

真實錯誤（關鍵行）：

```
error[E0038]: the trait `Generic` is not dyn compatible
...
2 |     fn foo<T>(&self, x: T);  // 泛型方法 -> 不 dyn 相容
  |        ^^^ ...because method `foo` has generic type parameters
  = help: consider moving `foo` to another trait
```

`rustc` 不只報錯，還告訴你「因為哪個方法、哪條規則」——訊息裡的 "because ... has no `self` parameter" / "has generic type parameters" 直接指出病灶。這是 Rust 錯誤訊息的典型：不只說「不行」，說「為什麼不行、怎麼修」。

**逃生門**：如果 trait 大部分方法可以 dyn、只有少數不行，可以用 `where Self: Sized` 把那幾個方法「排除在 vtable 之外」——它們只在靜態分派時可用，dyn 時看不到。`Iterator` 就是這樣做的：核心的 `next()` 是 dyn 相容的，但 `map`/`filter` 這些吃泛型閉包的 adapter 方法都帶 `where Self: Sized`（[Ch 12](./12-core-traits.md) 會再碰 Iterator）。

## 踩雷集錦

1. **以為 `Box<dyn Trait>` 的「胖」是 `Box` 造成的**：不是。是 `dyn` 造成的。`Box<i32>` 是 8 bytes 瘦指標，`Box<dyn T>` 是 16 bytes 胖指標。胖來自 dyn，`Box` 只是 forward 這個胖度。上面 `size_of` 已經證明。

2. **想直接持有 `dyn Trait` 值**：`let x: dyn Shape = ...;` 編不過，因為 `dyn Shape` 是 unsized。永遠透過指標：`Box<dyn Shape>`、`&dyn Shape`、`Rc<dyn Shape>`。這不是限制刁難你，是 vtable 在指標側的必然後果。

3. **以為 dyn 一定比泛型慢很多**：一次間接 call 的絕對成本很小（幾個 cycle）。真正的成本是「無法 inline」，這在熱迴圈裡才明顯。冷路徑用 dyn 換 code size 幾乎沒有可觀察的效能差。不要沒 profile 就為了「快」把所有東西泛型化——單型化爆 binary 有時反而更慢。

4. **忘了 dyn 相容性，寫了回傳 `Self` 或泛型方法的 trait 又想 `dyn`**：碰到 E0038 時看錯誤訊息最後那句 "because ..."，它直接告訴你哪個方法違規。對策通常是給那個方法加 `where Self: Sized`，或把它拆到另一個 trait。

5. **拿 dyn 當 C++ 繼承用**：Rust 沒有 class 繼承。`dyn Trait` 是「一組行為的抹型別引用」，不是「基底類別」。想共用資料欄位（C++ 的 `protected` 成員）→ trait object 幫不了你，那要靠組合（composition）。硬把繼承心智模型套上來會處處碰壁。

## 進階：再往深一層

**多 trait object**：`Box<dyn Read + Write>` 這種「同時是兩個 trait」的目前**不支援**（除了 auto trait 如 `Send`/`Sync`，`Box<dyn Shape + Send>` 是合法的）。原因是要合併多個非 auto trait 的 vtable，涉及 vtable 佈局怎麼設計，至今無定論。想要「同時 Read 又 Write」就自己定一個 `trait ReadWrite: Read + Write {}` 的 supertrait 當 object。

**upcasting**：把 `Box<dyn Sub>`（`trait Sub: Super`）轉成 `Box<dyn Super>`，這叫 trait upcasting。它在 Rust 1.86（2025-04）穩定；`rustc 1.97` 支援。實作上是 vtable 裡多存一個指向父 trait vtable 的欄位。

**`dyn*`（實驗性）**：有個長期實驗方向叫 `dyn*`，想讓小型別（≤ 一個指標）的 trait object 不用堆配置、內嵌進胖指標。目前還在 nightly 摸索，穩定版沒有。知道有這回事即可。

**手動組 vtable**：FFI 場景（[Ch 19](./19-ffi.md)）你會需要跟 C 的函式指標表互通。理解 Rust vtable 佈局（drop/size/align/methods）能幫你手刻對應的 C struct。但 Rust vtable 佈局**不是穩定 ABI**，不要在生產 code 依賴具體偏移——上面 `transmute` 那段是教學用的解剖，不是給你抄去正式專案的。

```rust
// 進階：&mut dyn 也是胖指標，可以動態分派後修改底層物件
trait Counter { fn tick(&mut self) -> u32; }
struct Simple(u32);
impl Counter for Simple {
    fn tick(&mut self) -> u32 { self.0 += 1; self.0 }
}
fn drive(c: &mut dyn Counter) -> u32 { c.tick() + c.tick() }
// drive 拿 &mut dyn，一樣走 vtable，但能改到底層物件
```

## 動手練習

1. 把本章第一個 `size_of` 程式改一下：加一個第三種形狀 `Triangle`，確認 `Box<dyn Shape>` 還是 16 bytes（vtable 換了，胖指標寬度不變）。
2. 把 vtable dump 程式的 `Dog` 改成 `struct Cat { age: u32, name: String }` 並 `impl Animal`，看 `vt[1] size` 和 `vt[0] drop` 怎麼變（`String` 有 drop glue，drop 不再是 0）。
3. 故意寫一個回傳 `Self` 的 trait 方法，然後試著 `&dyn` 它，親眼看 E0038，讀懂錯誤訊息最後那句 "because ..."。
4. 把 `render_dyn` 和 `render_static` 各 call 一次，用 `cargo build --release` 後 `objdump -d` 找這兩個函式，比較 `render_static` 有沒有被 inline 到 `main`（提示：release 下 `render_static` 可能整個消失）。

## 本章重點整理

- trait object（`dyn Trait`）是**胖指標** = (data ptr, vtable ptr)，`size_of::<&dyn Trait>()` = 16（x86-64）。
- Rust 把 vtable 指標放**指標側**（物件乾淨）；C++ 把 vptr 放**物件內**（物件變大）。後果：`dyn Trait` 是 unsized，只能透過指標持有。
- vtable 佈局 = [drop_in_place, size, align, 方法...]，前三個讓執行期能正確解構/配置抹型別的物件。
- 靜態分派（泛型）零成本可 inline 但膨脹 code；動態分派（dyn）一次間接 call、共用 code、能裝異質集合。預設用泛型，需要異質或要控 code size 才用 dyn。
- dyn 相容性規則：方法不能回傳 `Self`、不能有泛型參數、要有 `self` 接收者。違反出 E0038。

## 自我檢核

- [ ] 不看筆記，能不能畫出 `&dyn Trait` 的胖指標佈局，並說出兩欄各指向什麼？
- [ ] 能不能解釋「為什麼 `dyn Trait` 是 unsized，而 C++ 的多型物件不是」——關鍵差在 vtable 指標放哪？
- [ ] vtable 開頭為什麼要放 drop/size/align 而不只是方法指標？
- [ ] 面試被問「什麼時候該用 `impl Trait`/泛型、什麼時候該用 `dyn Trait`」，你能給出至少三個判斷依據嗎？
- [ ] 給你一個帶 `fn spawn() -> Self` 的 trait，你能一眼看出它不 dyn 相容並說出原因嗎？

## 延伸閱讀

### 官方文件 / Reference

- **[Rust Reference — Trait objects](https://doc.rust-lang.org/reference/types/trait-object.html)**
  - **讀哪裡**：整節，尤其 "trait object 是 DST" 那段。
  - **和本章的關聯**：本章講的「trait object 是 unsized、只能透過指標持有」的規範來源。搭配 [Dynamically Sized Types](https://doc.rust-lang.org/reference/dynamically-sized-types.html) 一起讀。

- **[Rust Reference — dyn compatibility](https://doc.rust-lang.org/reference/items/traits.html#dyn-compatibility)**
  - **讀哪裡**：dyn compatibility 的完整規則列表。
  - **和本章的關聯**：本章 E0038 那節只挑了三條最常見的規則；這裡有完整清單（例如 `where Self: Sized` 逃生門的正式定義）。

### 部落格 / 技術文章

- **[Exploring Rust fat pointers](https://iandouglasscott.com/2018/05/28/exploring-rust-fat-pointers/)** — Ian Douglas Scott
  - **這篇說什麼**：用 `transmute` 把胖指標拆開看 vtable，跟本章的 vtable dump 是同一手法，但講得更細（含 slice 胖指標對照）。
  - **前提知識**：懂 `transmute` 和裸指標；讀完本章剛好夠。
  - **為什麼值得讀**：它把「胖指標」這個概念在 slice 和 trait object 兩處統一講清楚，補強本章沒展開的 slice 那一半。

- **[The Rustonomicon — Exotically Sized Types](https://doc.rust-lang.org/nomicon/exotic-sizes.html)**
  - **讀哪裡**：DST（Dynamically Sized Types）那節。
  - **和本章的關聯**：本章說「`dyn Trait` 是 unsized」，這裡從記憶體佈局角度解釋 unsized 型別（DST）的一般理論，`dyn Trait` 和 `[T]` 是它的兩個實例。

### 書籍

- **《Rust for Rustaceans》** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust 標準參考，和本課定位重合。
  - **讀哪幾章**：Chapter 2（Types）談 trait object 與 dyn 相容性、vtable，比本章更深入 static vs dynamic dispatch 的取捨。

下一章我們把最常見、最該懂的幾個核心 trait 一次講清楚：`Deref`（deref coercion 怎麼發生的）、`Drop`（解構順序，還有 vtable 裡那個 drop_in_place 的另一半故事）、`Copy`/`Clone`、`From`/`Into`、以及 `Iterator`——你剛看到的 `for` 迴圈其實是它的語法糖。

→ [Ch 12 核心 trait：Deref/Drop/Copy/Clone/From/Iterator](./12-core-traits.md)
