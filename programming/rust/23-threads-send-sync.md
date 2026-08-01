# Ch 23 — 執行緒與 Send/Sync

> **目標**：把 `std::thread::spawn`/`join`/`move` 閉包講清楚並對照 C 的 `pthread`；建立 Rust 並發的核心地基——**Send / Sync 是 auto trait（自動推導的 marker trait）**，它們如何在編譯期把 data race 擋掉。搞懂哪些型別**不是** `Send`（`Rc`、裸指標）、哪些**不是** `Sync`（`Cell`/`RefCell`），真跑把 `Rc` move 進 thread 的 `E0277`，讀懂 "cannot be sent between threads safely"。理解「fearless concurrency」的實質：data race 在 Rust 是**編譯期錯誤**，在 C/C++ 是**執行期 UB**（給 C `pthread` data race + ThreadSanitizer 對照）。最後補 `'static` bound 為何 `spawn` 需要、`thread::scope` 怎麼放寬、手動 `unsafe impl Send/Sync` 的契約。

> **環境**：Rust `rustc 1.97.1`（stable，edition 2015）在 x86-64 Linux（WSL2）；C 對照用 `gcc 11.4.0`、`clang 14.0.0` + ThreadSanitizer。所有 Rust 編譯錯誤（`E0277`/`E0373`）、C 執行輸出、TSan 報告都是本機真跑，非推測。多執行緒輸出可能不定序，會標注。

## 為什麼需要這個？

你寫過 `pthread`。流程你閉著眼睛都會：`pthread_create(&t, NULL, worker, arg)` 丟一個 `void*`，`pthread_join(t, &ret)` 收回來。中間 worker 拿到的是一坨 `void*`，型別檢查全丟給你自己顧；多個 thread 碰同一塊記憶體要不要加鎖，編譯器一句話都不說。你靠的是紀律：「這個變數只有一個 thread 寫」「這裡進去前一定拿鎖」。紀律偶爾會斷，斷了就是 data race——而 data race 在 C 是 **undefined behavior**，不是「結果不對」這麼客氣，是整段行為不受掌控。

Rust 對這件事的態度完全不同。它不是給你更好的鎖，是**在型別系統裡把「這個東西能不能跨 thread」變成一條編譯期就檢查的規則**。你想把一個 `Rc`（非執行緒安全的引用計數）搬進另一個 thread？編譯不過。你想在兩個 thread 之間共享一個 `RefCell`（執行期借用檢查、無同步）？編譯不過。這套機制的名字叫 **Send / Sync**，它是整個 Rust 並發安全的地基——後面 Ch 24 的 `Arc<Mutex<T>>`、Ch 25 的 atomics、Ch 26 的 async，全部站在它上面。

這一章先把 thread 的基本操作跟 C 對齊，然後把 Send/Sync 這兩個 auto trait 挖到底：它們是什麼、怎麼自動推導、哪些型別故意不實作、為什麼「fearless concurrency」這個口號不是行銷詞而是型別系統的直接後果。

## 先建立直覺

先給一個心智圖像。想像每個型別身上有兩張通行證：

```
   型別 T
   ┌─────────────────────────────────────────┐
   │  Send 通行證：「我可以整個被搬到別的       │
   │              thread 去，所有權換手 OK」   │
   │                                          │
   │  Sync 通行證：「多個 thread 同時拿著      │
   │              &T 讀我，不會出事」          │
   └─────────────────────────────────────────┘

   spawn 一個 thread 把某個值搬進去
        → 檢查那個值有沒有 Send 通行證
   多 thread 共享 &T
        → 檢查 T 有沒有 Sync 通行證

   沒證 → 編譯器當場擋下（E0277），不是執行期才炸
```

這兩張通行證不是你手動去申請的——**編譯器根據型別的組成自動發**（這就是 auto trait 的意思）。一個 struct 如果它的每個欄位都有 Send 通行證，這個 struct 就自動有 Send。只要有一個欄位沒有（例如裡面藏了 `Rc`），整個 struct 就沒有。

關鍵在於：**這兩張通行證是安全跨執行緒的必要條件，而檢查發生在編譯期**。C 沒有這個概念——`pthread_create` 收 `void*`，你塞什麼進去它都收，能不能安全跨 thread 全靠你自己判斷。Rust 把「這東西能不能跨 thread」從你腦中的紀律，變成型別系統裡一條會被 `rustc` 執法的規則。

> 如果你對「trait 是什麼、auto trait 和一般 trait 差在哪」還不熟，先回看 [Ch 9 — Trait](./09-traits.md) 與 [Ch 12 — 核心 trait](./12-core-traits.md)。Send/Sync 是 marker trait（沒有方法、只當標記），這概念 Ch 12 提過 `Copy`。

## `thread::spawn` 與 `join`：對照 pthread

先把最基本的操作跑起來。Rust 的 `std::thread::spawn` 接一個閉包，回一個 `JoinHandle`；`join()` 等它結束並拿回傳值。

```rust
use std::thread;

fn main() {
    let mut handles = Vec::new();
    for i in 0..4 {
        let h = thread::spawn(move || {
            // move 把 i 的所有權搬進閉包
            format!("thread {} done", i)
        });
        handles.push(h);
    }
    for h in handles {
        let msg = h.join().unwrap();
        println!("{}", msg);
    }
}
```

真跑輸出（本機，因為我們按 handle 順序 join，這裡剛好有序；但 thread **實際執行**是併發不定序的）：

```
thread 0 done
thread 1 done
thread 2 done
thread 3 done
```

對照 `pthread`，同一件事：

```c
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

void *worker(void *arg) {
    long i = (long)arg;
    char *msg = malloc(32);
    snprintf(msg, 32, "thread %ld done", i);
    return msg;               // void*，型別自己顧
}

int main(void) {
    pthread_t t[4];
    for (long i = 0; i < 4; i++)
        pthread_create(&t[i], NULL, worker, (void *)i);
    for (int i = 0; i < 4; i++) {
        void *ret;
        pthread_join(t[i], &ret);
        printf("%s\n", (char *)ret);   // 手動 cast 回 char*
        free(ret);
    }
    return 0;
}
```

把兩邊的差異攤開：

| 面向 | C `pthread` | Rust `thread::spawn` |
|---|---|---|
| 傳資料進 thread | `void *arg`，型別擦除 | `move` 閉包捕獲，型別完整保留 |
| 回傳值 | `void *`，`join` 收，手動 cast | `JoinHandle<T>`，`join()` 回 `Result<T>` |
| 資料安不安全跨 thread | **你自己判斷** | 編譯器用 **Send/Sync** 檢查 |
| thread panic/錯誤 | 回傳值或 errno 自己約定 | `join()` 回 `Err`（thread panic 了）|
| 忘記 join | thread 洩漏，可能被程序結束砍掉 | 不 join 也不會 UB，但拿不到回傳值 |

`join()` 回的是 `Result` 不是裸值——因為 thread 可能 **panic**。如果 spawn 的閉包 panic 了，`join()` 回 `Err(Box<dyn Any>)`，你有機會處理，而不是像 C 那樣一個 thread 掛掉靜悄悄。這是 Rust 把「thread 可能失敗」編進型別的具體例子。

`move` 這個關鍵字是重點。閉包預設**借用**捕獲的變數（[Ch 14](./14-closures.md)），但 thread 可能活得比 `main` 的那個 `for` 迴圈 iteration 久，借用會懸空。`move` 強制閉包**取得所有權**——把 `i` 整個搬進去。這就接到下一個問題：為什麼 `spawn` 需要 `'static`。

## `'static` bound：為什麼 spawn 需要它

看 `spawn` 的簽章（[std 文件](https://doc.rust-lang.org/std/thread/fn.spawn.html)）：

```rust
pub fn spawn<F, T>(f: F) -> JoinHandle<T>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
```

`F: 'static` 這條 bound 的意思是：**閉包 `f` 內不能含有任何存活期短於 `'static` 的借用**。理由直接——`spawn` 出去的 thread 可能活得比呼叫它的函式久（甚至可能到程式結束），如果閉包借了一個區域變數，那個變數的 stack frame 被回收後，thread 還拿著懸空引用，就是 UAF。`'static` 這條 bound 就是編譯期把這種懸空擋掉。

真跑一個「借用區域變數、沒 `move`」的 thread：

```rust
use std::thread;

fn main() {
    let v = vec![1, 2, 3];
    // 借用 v，但 spawn 需要 'static，v 活不到那麼久
    let h = thread::spawn(|| {
        println!("{:?}", v);
    });
    h.join().unwrap();
}
```

`rustc` 直接擋（本機真跑）：

```
error[E0373]: closure may outlive the current function, but it borrows `v`, which is owned by the current function
 --> t23c.rs:6:27
  |
6 |     let h = thread::spawn(|| {
  |                           ^^ may outlive borrowed value `v`
7 |         println!("{:?}", v);
  |                          - `v` is borrowed here
  |
note: function requires argument type to outlive `'static`
 --> t23c.rs:6:13
  |
6 |       let h = thread::spawn(|| {
  |  _____________^
7 | |         println!("{:?}", v);
8 | |     });
  | |______^
help: to force the closure to take ownership of `v` (and any other referenced variables), use the `move` keyword
  |
6 |     let h = thread::spawn(move || {
  |                           ++++
```

`closure may outlive the current function, but it borrows v` + `function requires argument type to outlive 'static`——這是編譯器在講：thread 可能活比 `main` 這個函式久，但你的閉包借了 `v`，`v` 不是 `'static`。`rustc` 甚至直接告訴你解法：加 `move`。加了 `move`，`v` 的所有權搬進閉包，閉包本身就 `'static` 了（不含短命借用），編譯通過。

對照 C：`pthread_create` 收 `void*`，你把一個區域變數的位址塞進去、然後那個 thread 活過了這個函式——經典 bug，編譯器一聲不吭，執行期 UAF。Rust 這條 `'static` bound 就是把這類 bug 變成編譯錯誤。

> **注意**：`'static` 不代表「資料要活到程式結束」。`String`、`Vec<i32>`、`i32` 這些**擁有所有權、不含借用**的型別都滿足 `T: 'static`——因為它們的存活期不依賴任何比 `'static` 短的東西。`'static` bound 限制的是「不能含短命借用」，不是「不能被 drop」。這點 C 背景的人常搞混。

稍後 `thread::scope` 會示範怎麼**合法地**借用而不需要 `'static`。

## Send / Sync：auto trait 的核心

現在進入本章的靈魂。Send 和 Sync 是兩個 **marker trait**（標記 trait，沒有任何方法，`pub unsafe auto trait Send {}`），定義在 `std::marker`：

- **`Send`**：實作了 `Send` 的型別，其**所有權可以安全地在 thread 之間移動**。「把這個值搬到另一個 thread」是安全的。
- **`Sync`**：實作了 `Sync` 的型別，其 **`&T`（共享引用）可以安全地在多個 thread 之間共享**。等價定義：`T: Sync` 若且唯若 `&T: Send`。

兩者的精確關係值得停下來想：`Sync` 講的是「多個 thread 同時**唯讀**指向我，安不安全」。所以 `T: Sync ⟺ &T: Send`——「`&T` 能被 Send 到別的 thread」正是「`&T` 能被多 thread 同時持有」。

**它們是 auto trait（自動 trait）**——這是最關鍵的性質。你不會（通常也不該）手動去 `impl Send for MyType`。編譯器**自動根據型別組成推導**：

```
規則：一個複合型別（struct / enum / tuple）自動實作 Send，
      若且唯若它的每一個欄位型別都是 Send。
      Sync 同理（每個欄位都 Sync）。

   struct Foo {           自動推導：
       a: i32,     ── i32: Send ✓
       b: String,  ── String: Send ✓
       c: Vec<u8>, ── Vec<u8>: Send ✓
   }                → Foo: Send ✓（全部欄位都 Send）

   struct Bar {
       a: i32,     ── i32: Send ✓
       b: Rc<i32>, ── Rc<i32>: Send ✗   ← 有一個不是
   }                → Bar: Send ✗（整個爛掉）
```

這就是「傳染性」：一個型別只要藏了一個非 Send 的欄位，整個型別就不是 Send。編譯器對每個型別自動跑這個推導，你不用寫任何一行 `impl`。

大多數型別**是** Send **也是** Sync：`i32`、`String`、`Vec<T>`（當 `T: Send`）、`Box<T>`、`Arc<T>`……都自動有。真正有教育意義的是那些**故意不是**的型別——它們告訴你 Send/Sync 到底在防什麼。

## 哪些型別不是 Send / 不是 Sync

### `Rc<T>` 不是 Send

`Rc<T>`（[Ch 16](./16-smart-pointers.md)）是**非執行緒安全**的引用計數指標。它的 clone/drop 用的是**普通的非原子**加減來動 refcount。如果兩個 thread 同時 clone/drop 同一個 `Rc`，兩個非原子的 `refcount += 1` 會 race，refcount 算錯 → 要嘛提早 free（UAF）要嘛永遠不 free（leak）。所以 `Rc` **不是 Send**（也不是 Sync）。

把 `Rc` move 進 thread，看 `rustc` 怎麼擋（本機真跑）：

```rust
use std::rc::Rc;
use std::thread;

fn main() {
    let data = Rc::new(vec![1, 2, 3]);
    let d = Rc::clone(&data);
    let h = thread::spawn(move || {
        println!("{:?}", d);
    });
    h.join().unwrap();
}
```

```
error[E0277]: `Rc<Vec<i32>>` cannot be sent between threads safely
 --> t23b.rs:7:27
  |
7 |       let h = thread::spawn(move || {
  |               ------------- ^------
  |               |             |
  |  _____________|_____________within this `{closure@t23b.rs:7:27: 7:34}`
  | |             |
  | |             required by a bound introduced by this call
8 | |         println!("{:?}", d);
9 | |     });
  | |_____^ `Rc<Vec<i32>>` cannot be sent between threads safely
  |
  = help: within `{closure@t23b.rs:7:27: 7:34}`, the trait `Send` is not implemented for `Rc<Vec<i32>>`
note: required because it's used within this closure
 --> t23b.rs:7:27
  |
7 |     let h = thread::spawn(move || {
  |                           ^^^^^^^
note: required by a bound in `spawn`
 --> /rustc/.../library/std/src/thread/functions.rs:125:0
```

讀這份報告——這是本章你要能秒懂的錯誤：

- `Rc<Vec<i32>> cannot be sent between threads safely`：核心一句，`Rc` 不能跨 thread 送。
- `the trait Send is not implemented for Rc<Vec<i32>>`：具體到「因為沒實作 `Send`」。
- `required because it's used within this closure` → `required by a bound in spawn`：整條需求鏈——`spawn` 要求 `F: Send`，你的閉包捕獲了 `d`（`Rc`），`Rc` 不是 `Send`，所以閉包不是 `Send`，所以不符 `spawn` 的 bound。

編譯器把「這段有 data race 風險」精準定位到那個 `Rc`，在你**還沒跑**之前。對照 C：`Rc` 對應的就是「手寫非原子 refcount 的智慧指標」，你在 C 裡把它跨 thread 用，refcount race，執行期偶發 UAF/leak，valgrind 都不一定每次抓到。修法是換 `Arc`（原子 refcount，是 Send + Sync）：

```rust
use std::sync::Arc;
use std::thread;

fn main() {
    let data = Arc::new(vec![1, 2, 3]);
    let d = Arc::clone(&data);
    let h = thread::spawn(move || {
        println!("{:?}", d);
    });
    h.join().unwrap();
}
```

真跑印 `[1, 2, 3]`。`Arc` 和 `Rc` 唯一實質差別就是「refcount 是原子操作」，代價是每次 clone/drop 慢一點（一個原子指令 vs 一個普通加法），換來 Send + Sync。下一章 `Arc` 會大用。

### 裸指標 `*const T` / `*mut T` 不是 Send/Sync

裸指標刻意不是 Send 也不是 Sync——因為裸指標背後沒有任何安全保證，編譯器無從判斷跨 thread 用它安不安全，保守起見一律不給通行證。

```rust
use std::thread;

fn main() {
    let x = 10;
    let p: *const i32 = &x;   // 裸指標不是 Send
    let h = thread::spawn(move || {
        unsafe { println!("{}", *p); }
    });
    h.join().unwrap();
}
```

真跑（需求鏈和 `Rc` 那份同構，只貼首行）：

```
error[E0277]: `*const i32` cannot be sent between threads safely
 --> t23f.rs:6:27
  |
7 | |         unsafe { println!("{}", *p); }
  | |_____^ `*const i32` cannot be sent between threads safely
  |
  = help: within `{closure@...}`, the trait `Send` is not implemented for `*const i32`
```

這就是為什麼 FFI（[Ch 19](./19-ffi.md)）帶著裸指標跨 thread 時，你常常得手動包一層 `unsafe impl Send`——編譯器不敢替你保證，得你來簽字（本章最後講契約）。

### `Cell<T>` / `RefCell<T>` 不是 Sync

`Cell`/`RefCell`（[Ch 16](./16-smart-pointers.md)）是**內部可變性（interior mutability）**——它們讓你透過 `&T`（共享引用）去改內容，借用檢查搬到執行期做。但它們的實作**完全沒有同步**：`RefCell` 的借用計數是普通整數，`Cell::set` 是普通寫入。多 thread 同時透過 `&RefCell` 去 `borrow_mut`，借用計數 race、內容 race，直接 data race。所以它們**不是 Sync**（`RefCell` 是 Send，但不是 Sync——可以整個搬過去，不能共享）。

真跑一個 `Arc<RefCell<i32>>`——`Arc` 本身是 Send/Sync，但它包的 `RefCell` 不是 Sync，於是整個 `Arc<RefCell>` 連 Send 都失去（因為 `Arc<T>: Send` 要求 `T: Send + Sync`）：

```rust
use std::cell::RefCell;
use std::sync::Arc;
use std::thread;

fn main() {
    // Arc<RefCell<T>>: Arc 是 Send/Sync，但 RefCell 不是 Sync
    let data = Arc::new(RefCell::new(0));
    let d = Arc::clone(&data);
    let h = thread::spawn(move || {
        *d.borrow_mut() += 1;
    });
    h.join().unwrap();
}
```

```
error[E0277]: `RefCell<i32>` cannot be shared between threads safely
  --> t23e.rs:9:27
   |
 9 |       let h = thread::spawn(move || {
   | |_____^ `RefCell<i32>` cannot be shared between threads safely
   |
   = help: the trait `Sync` is not implemented for `RefCell<i32>`
   = note: if you want to do aliasing and mutation between multiple threads, use `std::sync::RwLock` instead
   = note: required for `Arc<RefCell<i32>>` to implement `Send`
```

`cannot be shared between threads safely`（注意是 **shared**，不是 send——這是 Sync 的字眼）+ `the trait Sync is not implemented for RefCell<i32>` + 貼心提示 `use std::sync::RwLock instead`。編譯器連解法都給了：要多 thread 共享可變，用 `RwLock`（Ch 24）或 `Mutex`，不是 `RefCell`。

把三個「不是」整理成一張表：

| 型別 | Send? | Sync? | 原因 |
|---|---|---|---|
| `Rc<T>` | ✗ | ✗ | 非原子 refcount，跨 thread clone/drop 會 race |
| `Arc<T>`（T: Send+Sync） | ✓ | ✓ | 原子 refcount |
| `*const T` / `*mut T` | ✗ | ✗ | 裸指標無安全保證，編譯器保守 |
| `Cell<T>` / `RefCell<T>` | ✓（Send）| ✗ | 內部可變性無同步，共享即 race |
| `Mutex<T>`（T: Send） | ✓ | ✓ | 有互斥，共享安全（Ch 24） |
| `MutexGuard` | 視 T | ✗（通常）| 鎖必須在拿它的 thread 釋放 |

## fearless concurrency：編譯期 vs 執行期 UB

現在把整章的價值濃縮成一句對照：**同一個 data race bug，Rust 是編譯錯誤，C/C++ 是執行期 UB。**

先看 C 這邊的 data race——兩個 thread 無鎖遞增同一個全域計數器：

```c
#include <pthread.h>
#include <stdio.h>

long counter = 0;

void *worker(void *arg) {
    for (int i = 0; i < 100000; i++) {
        counter++;          // 無鎖遞增，兩 thread 同時做 = data race
    }
    return NULL;
}

int main(void) {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, worker, NULL);
    pthread_create(&t2, NULL, worker, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %ld (expected 200000)\n", counter);
    return 0;
}
```

`gcc drace.c -O0 -o drace0 -lpthread`，跑 5 次（本機真跑，`counter++` 是「讀-改-寫」三步，兩 thread 交錯就丟更新）：

```
counter = 137055 (expected 200000)
counter = 124273 (expected 200000)
counter = 100000 (expected 200000)
counter = 104189 (expected 200000)
counter = 141490 (expected 200000)
```

每次結果都不同、都遠小於 200000——丟失的更新就是 race 的證據。而且它**不會 crash**、`gcc` 一句 warning 都沒有；你不跑個幾次、不上 TSan，根本不知道有 bug。

> **踩雷提醒**：同一段在 `-O2` 編譯時，`gcc` 可能把整個迴圈的 `counter++` 優化掉或算成常數，反而「剛好」印出 200000——這**不是**沒有 race，是優化掩蓋了現象。本機 `-O2` 五次都印 200000，`-O0` 才暴露。這正是 data race UB 的陰險：連「現象」都靠優化等級決定。

上 ThreadSanitizer（`clang -fsanitize=thread`，WSL 下需 `setarch $(uname -m) -R` 關 ASLR 避開已知的記憶體映射衝突）：

```
WARNING: ThreadSanitizer: data race (pid=268159)
  Write of size 8 at 0x5555569c7258 by thread T2:
    #0 worker /tmp/drace.c:8:16 ...

  Previous write of size 8 at 0x5555569c7258 by thread T1:
    #0 worker /tmp/drace.c:8:16 ...

  Location is global 'counter' of size 8 at 0x5555569c7258 ...

SUMMARY: ThreadSanitizer: data race /tmp/drace.c:8:16 in worker
```

TSan 抓到了——但注意它是**執行期動態工具**：要真的跑、要 race 真的發生、要 TSan 剛好觀察到那次交錯才報。它是 C/C++ 世界對抗 data race 的最強武器之一，但本質是「跑起來抓」，不是「編譯期擋」。

現在看 Rust 這邊。你**根本寫不出**這段能編譯的等價 code——把 `counter` 弄成能被兩個 thread 無同步寫，你要嘛用 `static mut`（碰它要 `unsafe`，safe Rust 到不了），要嘛想共享一個普通 `&mut`，borrow checker 直接擋（`&mut` 不能同時給兩個 thread）。你若試著用 `Arc<Cell<i32>>` 繞，就撞上剛才那個 `Cell` 不是 Sync 的 `E0277`。**data race 在 safe Rust 是「編譯不出來」，不是「跑起來出錯」**。

```
        data race 這個 bug 的命運：

   C/C++ ──▶ 編譯通過 ──▶ 執行期 UB ──▶ 靠 TSan/運氣抓
                                          （非窮盡、要跑才知道）

   Rust  ──▶ 編譯期被 Send/Sync + borrow checker 擋
             （safe code 根本產生不出 data race）
```

這就是 "fearless concurrency" 的實質——不是「Rust 讓並發變簡單」，是「**Rust 把一整類並發 bug（data race）從執行期 UB 提前成編譯錯誤**」。你不再需要「跑一萬次+TSan 才敢說沒 race」，safe code 編過就沒有 data race（邏輯錯誤如 deadlock 另當別論，Ch 24 談）。

> **精確一點**：Rust 保證 safe code 無 **data race**（兩個 thread 無同步存取同一記憶體、至少一方寫）。它**不**保證無 **race condition**（邏輯上的競態，例如 check-then-act）、不保證無 deadlock、不保證無 livelock。「無 data race」是型別系統給的硬保證；其他並發 bug 仍要你自己想清楚。這點 Ch 24（deadlock）會兌現。

## `thread::scope`：放寬 `'static`，安全借用

`'static` bound 有時很煩——你明明知道 thread 會在函式返回前 join 完，為什麼還不准借用區域變數？`thread::scope`（Rust 1.63 穩定）就是為此設計：它保證所有 scoped thread 在 scope 結束前**一定被 join**，於是編譯器可以安全地放寬 `'static`，讓你借用外面的區域變數。

```rust
use std::thread;

fn main() {
    let mut v = vec![1, 2, 3];
    thread::scope(|s| {
        s.spawn(|| {
            println!("讀 v: {:?}", v);
        });
        s.spawn(|| {
            println!("再讀 v: {} 個元素", v.len());
        });
    });
    // scope 結束後 v 又能拿回來可變借用
    v.push(4);
    println!("scope 後 v = {:?}", v);
}
```

真跑（前兩行併發，**順序可能對調**；`scope 後` 那行一定最後）：

```
讀 v: [1, 2, 3]
再讀 v: 3 個元素
scope 後 v = [1, 2, 3, 4]
```

注意這裡兩個 scoped thread 都只是**共享借用** `&v`（沒 `move`），編譯器允許——因為 `scope` 保證它們活不過 `scope` 這個 block，`v` 一定活得比它們久。scope 結束後，那些借用全部釋放，`v.push(4)` 又能拿到可變借用。這在 `thread::spawn` 是不可能的（會撞 `'static`）。

底層機制：`thread::scope` 收一個閉包，給它一個 `Scope` handle `s`；`s.spawn` 出去的 thread 綁定在這個 scope 的存活期上。`scope` 函式在返回前會**自動 join 所有還沒 join 的 scoped thread**。因為有這個「保證全部 join」，借用檢查才敢放行外部借用——thread 絕不可能活過 scope，懸空不可能發生。這是「用 RAII 把 join 保證編進型別」的漂亮例子，也是 Rust 1.63 之前得靠 `crossbeam::scope` 這個外部 crate（見延伸閱讀）才有的功能。

## 手動 `unsafe impl Send/Sync`：契約

auto trait 的自動推導有時太保守。最典型：你在 FFI 裡拿到一個 C 的裸指標（例如某個 handle），你**知道**它跨 thread 用是安全的（例如那個 C library 保證 thread-safe），但編譯器看到裸指標一律不給 Send。這時你可以手動簽字：

```rust
struct MyHandle {
    ptr: *mut std::ffi::c_void,
}

// 我（開發者）保證：這個 handle 跨 thread 移動是安全的。
// 例如底層 C library 文件承諾 handle 可在任意 thread 使用。
unsafe impl Send for MyHandle {}
```

`unsafe impl` 這個 `unsafe` 是重點——它跟 [Ch 17](./17-unsafe-basics.md) 的 `unsafe` 一樣，是**你在對編譯器簽契約**：「我知道你自動推不出來，但我以人的知識保證這是安全的。」你簽錯了（那個 C library 其實不是 thread-safe），編譯器不會救你，data race UB 全歸你。

契約的內容具體是什麼：

- **`unsafe impl Send for T`**：你保證「把一個 `T` 的值整個移動到另一個 thread、之後只在新 thread 用它」不會造成 UB。要點：`T` 內的資源（裸指標指向的東西、handle）在換 thread 後仍有效且無別的 thread 同時亂動。
- **`unsafe impl Sync for T`**：你保證「多個 thread 同時持有 `&T` 並透過它做（`&T` 允許的）操作」不會 data race。這通常意味著 `T` 內部要嘛唯讀，要嘛所有可變存取都有內部同步（鎖、atomic）。

標準庫自己就這樣做：`Arc` 內部有裸指標，但它 `unsafe impl` 了 Send/Sync（條件是 `T: Send + Sync`），因為它的 refcount 是**原子**的、它保證了安全。`Mutex<T>` 也是——它包住 `T`，用互斥保證同時只一個 thread 碰內容，於是 `unsafe impl Sync`（條件 `T: Send`）。

**別亂簽**：手動 `unsafe impl Send/Sync` 是繞過 Rust 最核心的並發安全檢查。95% 的情況你不需要它——需要跨 thread 共享可變，用 `Arc<Mutex<T>>`（Ch 24）或 atomic（Ch 25），讓標準庫那些**已經簽過字、被驗證過**的抽象替你扛。只有寫 FFI wrapper 或手刻並發原語時才碰它，而且碰之前要能把上面那份契約講清楚。

## 對比與取捨

| 面向 | C/C++ | Rust safe | Rust unsafe |
|---|---|---|---|
| 跨 thread 安全性 | 你自己判斷，編譯器不管 | Send/Sync 編譯期檢查 | 手動 `unsafe impl` 簽字 |
| data race | 執行期 UB，靠 TSan 抓 | **編譯不出來** | 簽錯字才可能 UB |
| thread 借用區域變數 | 自己保證不懸空 | `'static` 擋 / `scope` 放寬 | — |
| 非原子引用計數跨 thread | 手寫，自己顧 | `Rc` 不是 Send，擋下 | — |
| 內部可變共享 | 自己加鎖 | `RefCell` 不是 Sync，擋下 | — |
| thread panic | 約定 errno/回傳 | `join()` 回 `Result` | — |

取捨要誠實：Send/Sync 的編譯期檢查**有代價**——它會擋掉一些其實安全、但編譯器推不出來的寫法（例如某些 FFI handle），逼你手動 `unsafe impl` 或改設計。這是「保守但可證明安全」對「靈活但要自己顧」的經典取捨。C 給你全部自由和全部責任；Rust 收走一部分自由，換來「safe code 無 data race」這條硬保證。對系統/資安工程師，這筆交易通常划算——data race 是最難 debug 的一類 bug（不定時重現、優化敏感、TSan 也非窮盡）。

## 踩雷集錦

1. **以為 `unsafe` 能關掉 Send/Sync 檢查**：`unsafe { }` 區塊**不**放寬 Send/Sync（也不放寬 borrow checker，[Ch 17](./17-unsafe-basics.md)、[Ch 20](./20-memory-model-ub.md) 講過）。要讓一個型別跨 thread，得**手動 `unsafe impl Send/Sync`**（在型別上簽字），不是在用它的地方包 `unsafe { }`。這兩個 `unsafe` 是不同機制。

2. **把 `Rc` 當 `Arc` 用**：`Rc` 是非原子 refcount、**不是 Send**（真跑 `E0277`）。想跨 thread 共享所有權用 `Arc`。反過來也要注意——單 thread 場景硬用 `Arc` 是浪費（每次 clone/drop 都是原子操作，比 `Rc` 慢），別無腦全用 `Arc`。選哪個看「要不要跨 thread」。

3. **忘記 `move` 就 spawn**：閉包預設借用捕獲，`spawn` 需要 `'static`，於是借用區域變數的閉包編不過（真跑 `E0373`）。`rustc` 會直接叫你加 `move`。加 `move` = 所有權搬進閉包。想借用而不搬所有權，用 `thread::scope`。

4. **以為「fearless concurrency」= 沒有並發 bug**：Rust 只保證 **safe code 無 data race**。**deadlock、race condition（邏輯競態）、livelock 照樣會發生**（Ch 24 真跑 deadlock）。「無 data race」是型別系統的硬保證，「無 deadlock」不是——鎖順序還是你自己的責任。

5. **在 struct 裡藏了一個非 Send 欄位，整個 struct 就不是 Send**：auto trait 有傳染性——一個 `Rc` 或裸指標欄位，整個 struct 失去 Send（真跑那個 `Arc<RefCell>` 因 `RefCell` 非 Sync 連 Send 都沒了）。編譯器報錯會指到最外層型別，你得往裡找是哪個欄位破功。

6. **亂簽 `unsafe impl Send`**：這是繞過 Rust 最核心的並發安全網。你簽的字若錯（那塊記憶體其實會被別的 thread 動），就是 data race UB，且 Miri 之外沒工具替你抓。除非你在寫 FFI wrapper 或並發原語、且能講清楚契約，否則優先用 `Arc<Mutex<T>>` 讓標準庫扛。

## 進階：再往深一層

**`Send + !Sync` 與 `!Send + Sync` 都存在。** 兩張通行證是**獨立**的：

- `Send` 但 `!Sync`：`Cell<T>`/`RefCell<T>`——可以整個搬到別的 thread（Send），但不能多 thread 共享 `&`（非 Sync，共享即 race）。也就是「一個 thread 獨佔用它，OK；多 thread 同時看它，不行」。
- `!Send` 但 `Sync`：較罕見，`MutexGuard<T>` 是經典例子——它不是 Send（鎖必須在**拿它的那個 thread** 釋放，[POSIX](https://pubs.opengroup.org/onlinepubs/9699919799/functions/pthread_mutex_unlock.html) 對 `pthread_mutex` 也有這規定），但 `&MutexGuard` 可以跨 thread（是 Sync）。搞懂這兩個「只有一張證」的例子，你就真的懂 Send 和 Sync 是兩件不同的事，而不是「執行緒安全」一個模糊概念。

**面試常問**：「Send 和 Sync 差在哪？舉一個 Send 但不 Sync 的型別。」——標準答案：Send 是「型別的值能安全跨 thread **移動所有權**」，Sync 是「`&T` 能安全跨 thread **共享**」，且 `T: Sync ⟺ &T: Send`。`Send + !Sync` 的例子就是 `Cell`/`RefCell`——搬過去獨用 OK，多 thread 共享 `&` 會 race。能講清楚這個雙軸，代表你懂 Rust 並發模型的地基，不是背「Send 是 send、Sync 是 sync」。

**auto trait 的負面實作（negative impl）。** 想明確宣告「我這型別**故意不是** Send」，nightly 有 `impl !Send for T {}`（`negative_impls` feature）。標準庫用它給 `Rc`、`MutexGuard` 這些型別打上「不是 Send」的印記，覆蓋掉 auto trait 本來會給的通行證。stable 上一般用「塞一個非 Send 欄位（如 `PhantomData<Rc<()>>`）」達到同樣效果。這是「你想主動退出某個 auto trait」時的工具。

**`crossbeam` 生態（外部 crate）。** `thread::scope` 進標準庫（1.63）之前，scoped thread 靠 [`crossbeam`](https://docs.rs/crossbeam) 這個外部 crate（`crossbeam::scope`）。crossbeam 至今仍是無鎖並發資料結構（無鎖 queue/deque、epoch-based reclamation、`ArrayQueue`）的主要來源，Ch 25 講無鎖時會再提。標準庫的 `thread::scope` 覆蓋了最常用的 scoped 場景，但 crossbeam 的並發資料結構標準庫沒有。

## 動手練習

1. **重現三個 `E0277`/`E0373`**：把本章的 `Rc` move 進 thread、裸指標 move 進 thread、`Arc<RefCell>` 共享、以及忘記 `move` 的借用 spawn，四個都打進去跑，逐一讀懂錯誤訊息裡「哪個型別、缺哪個 trait（Send 還是 Sync）、需求鏈怎麼傳到 `spawn` 的 bound」。

2. **把 `Rc` 改成 `Arc` 讓它編過**：拿第一個 `Rc` 例子，把 `Rc` 換成 `Arc`（`use std::sync::Arc`），確認編過並印出 `[1, 2, 3]`。想清楚為什麼 `Arc` 行而 `Rc` 不行（原子 vs 非原子 refcount）。

3. **C data race + TSan 對照**：把本章 C 的 `counter++` 例子用 `-O0` 編譯跑 5 次，看丟失更新；再上 `clang -fsanitize=thread`（WSL 記得 `setarch -R`）看 TSan 報告。然後試著在 Rust 寫一個「兩 thread 無同步遞增同一計數器」——體會你**寫不出**能編過的版本。

4. **`thread::scope` 借用**：用 `thread::scope` 開兩個 thread 共享借用一個 `Vec`，scope 結束後對它 `push`。再試著把同樣的 code 改成 `thread::spawn`（不 move），看它撞 `'static` 的 `E0373`。體會 scope 放寬了什麼。

## 本章重點整理

- **`thread::spawn` 收 `move` 閉包、回 `JoinHandle`，`join()` 回 `Result`（thread 可能 panic）**；對照 `pthread` 的 `void*` 傳參/回傳，Rust 保留完整型別、且用 Send/Sync 檢查跨 thread 安全性。
- **Send / Sync 是 auto trait（自動推導的 marker trait）**：`Send` = 值能安全跨 thread 移動所有權；`Sync` = `&T` 能安全跨 thread 共享（`T: Sync ⟺ &T: Send`）。複合型別若**每個欄位都** Send/Sync 才自動獲得，一個欄位破功整個失去（傳染性）。
- **故意不是的型別揭示 Send/Sync 在防什麼**：`Rc`（非原子 refcount，非 Send）、裸指標（無保證，非 Send/Sync）、`Cell`/`RefCell`（內部可變無同步，非 Sync）。真跑三個 `E0277`：`cannot be sent`（Send）vs `cannot be shared`（Sync）。
- **fearless concurrency 的實質**：data race 在 safe Rust 是**編譯錯誤**（Send/Sync + borrow checker 擋掉），在 C/C++ 是**執行期 UB**（真跑 C `counter++` 丟更新、TSan 才抓到）。但 Rust **只保證無 data race**，deadlock/邏輯競態照樣會有。
- **`'static` bound 讓 `spawn` 不准借短命變數**（thread 可能活更久 → UAF），`thread::scope` 用「保證全部 join」放寬它、允許安全借用外部變數。手動 **`unsafe impl Send/Sync` 是你對編譯器簽契約**，簽錯就是 UB——優先用 `Arc<Mutex<T>>` 讓標準庫扛。

## 自我檢核

- [ ] 不看筆記，能解釋 Send 和 Sync 各自的定義、以及 `T: Sync ⟺ &T: Send` 這條關係，並舉一個 `Send + !Sync` 的型別（`Cell`/`RefCell`）。
- [ ] 面試問「為什麼 `Rc` 不能跨 thread、`Arc` 可以」，能答出「非原子 vs 原子 refcount，`Rc` 跨 thread clone/drop 會 race」。
- [ ] 能解釋為什麼 `spawn` 需要 `'static` bound（thread 可能活比 caller 久 → 借用懸空），以及 `thread::scope` 靠什麼保證放寬它是安全的（保證 scope 結束前全部 join）。
- [ ] 能說清「fearless concurrency」的準確含義：safe Rust 保證無 **data race**（編譯期擋），但**不**保證無 deadlock / race condition。
- [ ] 能講出手動 `unsafe impl Send for T` 你到底在向編譯器保證什麼，以及為什麼 95% 的情況該用 `Arc<Mutex<T>>` 而不是自己簽字。

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提。

### 官方文件 / Spec

- **[The Rustonomicon — Send and Sync](https://doc.rust-lang.org/nomicon/send-and-sync.html)**
  - **讀哪裡**：整節不長。核心是 Send/Sync 作為 auto trait 的定義、自動推導規則、以及「什麼時候你需要手動 `unsafe impl`」和它的契約。
  - **學到什麼**：本章手動 `unsafe impl Send/Sync` 那節的權威依據；Nomicon 把「你簽字時到底保證了什麼」講得比本章更嚴謹。
  - **前提**：懂本章 Send/Sync 直覺 + unsafe（Ch 17）。這是寫 FFI wrapper 前必讀。

- **[std::thread 模組文件](https://doc.rust-lang.org/std/thread/)** 與 **[std::thread::scope](https://doc.rust-lang.org/std/thread/fn.scope.html)**
  - **讀哪裡**：`thread` 模組首頁講 spawn/join/JoinHandle 的完整語意；`scope` 那頁的例子展示 scoped thread 怎麼安全借用。
  - **學到什麼**：`spawn` 簽章裡 `Send + 'static` bound 的官方解釋、`JoinHandle::join` 回 `Result` 的 panic 語意、`scope` 保證 join 的機制。
  - **前提**：懂本章 spawn/join。當工具書查。

### 書籍

- **《Rust for Rustaceans》— 第 10 章「Concurrency (and Parallelism)」** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust 的最佳單本書，和本課定位重合。第 10 章專講並發，Send/Sync、`Arc<Mutex>`、atomic 都有。
  - **讀哪幾章**：第 10 章與本章直接對應；把 Send/Sync 的 auto trait 機制、`unsafe impl` 契約講得比本章深。
  - **前提**：懂本章 + trait（Ch 9）。讀完本章接這章正好。

### 技術文章

- **[Fearless Concurrency with Rust](https://blog.rust-lang.org/2015/04/10/Fearless-Concurrency.html)** — Aaron Turon（Rust 官方部落格，2015）
  - **這篇說什麼**：「fearless concurrency」這個詞的出處，由當年設計這套系統的核心成員親筆。解釋 Send/Sync + ownership 如何在編譯期消滅 data race，以及這相對 C/C++ 執行期 UB 的價值——正是本章「編譯期 vs 執行期」那節的思想源頭。
  - **讀哪裡**：整篇不長；「Data races」與「Send and Sync」兩段是核心。
  - **為什麼值得讀**：作者 Aaron Turon 是 Rust 並發模型的主要設計者之一，第一手講「為什麼這樣設計」。
  - **前提**：懂本章 Send/Sync 與 data race 概念。

- **[crossbeam 文件](https://docs.rs/crossbeam/latest/crossbeam/)** — crossbeam-rs（外部 crate 官方文件）
  - **這篇說什麼**：標準庫 `thread::scope` 進 std（1.63）之前的 scoped thread 來源，以及標準庫至今沒有的無鎖並發資料結構（`ArrayQueue`、`SegQueue`、epoch-based reclamation）。
  - **讀哪裡**：`scope` 的文件對照本章 `thread::scope`；`crossbeam::queue` 為 Ch 25 無鎖鋪路。
  - **為什麼值得讀**：Rust 並發生態裡最重要的外部 crate 之一，標準庫的並發原語很多概念源自這裡。
  - **前提**：懂本章 scope；無鎖資料結構部分建議看完 Ch 25 再回來。

搞懂了 thread 怎麼開、Send/Sync 怎麼在編譯期擋掉 data race，下一章我們把「多 thread 共享**可變**狀態」這件事做對——`Arc<Mutex<T>>` 這個經典組合怎麼把「鎖保護的資料」綁進型別，讓你**忘記鎖就拿不到資料**；順帶把 Mutex 中毒（poisoning）、`RwLock`、以及 Rust **不防**的 deadlock 都真跑一遍。

→ [Ch 24 共享狀態：Mutex/RwLock/Arc](./24-shared-state.md)
