# 練習 A — 把 C 資料結構改寫成 Rust

> **目標**：把 Ch 2–7 學到的 ownership / borrow / lifetime 拼起來。給你一段有記憶體 bug 的 C 單向鏈結串列，你要 (1) 精確指出 bug 是什麼、C 為什麼擋不住，(2) 用 safe Rust 改寫，親眼看 borrow checker 從**根本**擋掉同一個 bug。完成後你驗證了「你真的理解 ownership 在管什麼」，而不只是會背規則。

> **環境**：C 端用 `gcc` + AddressSanitizer（`-fsanitize=address`）在 WSL2 / x86-64 Linux 抓 UAF；Rust 端 `rustc 1.97.1`。參考解答全部在此環境真跑過。

## 背景與動機

你在 C 裡寫過無數次鏈結串列和環形緩衝區。它們是 use-after-free（UAF）和 iterator invalidation 的重災區——不是因為你笨，是因為 C 的型別系統**根本不追蹤**「這個指標指向的東西還活著嗎」。你靠紀律避免，紀律偶爾會斷。

這個練習給你一段**看起來正常、實際有 UAF** 的 C 鏈結串列。你先當它是 code review 對象揪出 bug，再用 Rust 重寫。重點不是「Rust 語法怎麼寫」，而是體會：**同一個 bug，在 Rust 裡你連寫出來都做不到**——borrow checker 在編譯期就把那條路堵死。這是 Rust 對系統/資安工程師最核心的價值主張，你要親手驗證一次。

---

## 任務規格

### 給定的 C 程式（有 bug）

```c
#include <stdio.h>
#include <stdlib.h>

typedef struct Node {
    int value;
    struct Node *next;
} Node;

typedef struct {
    Node *head;
    size_t len;
} List;

void push_front(List *l, int v) {
    Node *n = malloc(sizeof(Node));
    n->value = v;
    n->next = l->head;
    l->head = n;
    l->len++;
}

// 回傳指向某節點 value 欄位的指標
int *find(List *l, int target) {
    for (Node *n = l->head; n; n = n->next) {
        if (n->value == target) {
            return &n->value;
        }
    }
    return NULL;
}

void pop_front(List *l) {
    Node *old = l->head;
    l->head = old->next;
    free(old);
    l->len--;
}

int main(void) {
    List l = {NULL, 0};
    push_front(&l, 10);
    push_front(&l, 20);
    push_front(&l, 30);

    int *p = find(&l, 20);   // p 指向 value==20 那個節點的 value 欄位
    printf("found %d\n", *p);

    pop_front(&l);           // 釋放 head(30)
    pop_front(&l);           // 釋放 20 那個節點 —— p 現在懸空

    printf("stale read: %d\n", *p);  // ← use-after-free

    return 0;
}
```

### 你要做的事

| 任務 | 交付 |
|---|---|
| **1. 指出 bug** | 用一兩句話說清楚：bug 是什麼類型、發生在哪幾行、C 為什麼沒擋住 |
| **2. 用 ASan 證明** | 編譯時加 `-fsanitize=address`，跑出來貼 ASan 報告，指出 `freed by` 和 `READ of` 兩個位置 |
| **3. safe Rust 改寫** | 實作等價的 `List`：`new` / `push_front` / `pop_front` / `find` / `len`，全部 safe（不准用 `unsafe`） |
| **4. 證明 bug 被擋** | 在 Rust 裡照抄 C 的危險用法（`find` 拿引用後 `pop_front`），貼出 borrow checker 的**編譯錯誤**證明它過不了 |

### 限制條件

- Rust 版**不准用 `unsafe`**、不准用 `Rc`/`RefCell`（那些留到 Part 3；這裡就是要你用最基本的 `Box` + ownership 解決）。
- `find` 要回傳 `Option<&i32>`（借用，不是拷貝），這樣才能重現「拿了引用再改動容器」的衝突。
- 不准用標準庫的 `LinkedList` 或 `Vec` 當內部儲存——自己用 `Option<Box<Node>>` 串。

### 驗收標準

- Rust 版 `main` 能正常 push / find / pop / 印出剩餘元素，輸出正確。
- 「find 後 pop」的危險版本**編譯失敗**，錯誤碼是 `E0502`（借用衝突），你能解釋這個錯誤怎麼對應到 C 的 UAF。

---

## 期望輸出範例

C 版（開 ASan）跑出來大致長這樣：

```
found 20
=================================================================
==...==ERROR: AddressSanitizer: heap-use-after-free on address ...
READ of size 4 at ... thread T0
    #0 ... in main .../buggy_list.c:53
...
freed by thread T0 here:
    #0 ... in free
    #1 ... in pop_front .../buggy_list.c:36
...
SUMMARY: AddressSanitizer: heap-use-after-free .../buggy_list.c:53 in main
```

Rust 正確版：

```
len = 3
found 20
popped Some(30)
popped Some(20)
len = 1
remaining 10
```

Rust 危險版（find 後 pop）**編不過**，應該看到 `error[E0502]`。

---

## 如果你卡住了

1. **bug 在哪？** 問自己：`find` 回傳的 `p` 指向哪個節點的內部？第二次 `pop_front` free 掉的是哪個節點？`p` 和那個被 free 的節點有沒有關係？
2. **Rust 的 `next` 欄位型別怎麼寫？** 一個節點「可能有、也可能沒有」下一個節點——「可能有可能沒有」在 Rust 是 `Option`。而下一個節點是**擁有**的（這條鏈由 List 獨佔），擁有一個 heap 上的東西是 `Box`。合起來：`Option<Box<Node>>`。
3. **`push_front` 要把舊 head 接到新節點後面，但不能同時「讀 self.head」又「寫 self.head」。** 找 `Option` 上一個「拿走裡面的值、原地留 `None`」的方法（線索：它的名字是個動詞，四個字母）。
4. **`find` 要沿鏈走訪並回傳 `Option<&i32>`。** 走訪時你手上是 `&Option<Box<Node>>`，怎麼變成 `Option<&Node>`？找 `Option` 的一個方法，名字暗示「借用並解 Box」（線索：`as_` 開頭）。
5. **為什麼「find 後 pop」會被擋？** `find(&self)` 回傳的引用借用了 `&self`。只要那個引用還活著，`pop_front(&mut self)` 就拿不到 `&mut self`——這正是 [Ch 3](./03-borrowing-references.md) 的「共享借用存在時不能可變借用」，也是 [Ch 7](./07-borrow-checker-internals.md) 的 NLL 在判的東西。

---

## 實作步驟建議

### Step 1：先在 C 端定位並證明 bug
編譯 C 版時開 ASan：`gcc -g -fsanitize=address buggy_list.c -o buggy_list && ./buggy_list`。讀 ASan 報告，把「哪行 free、哪行 read」抄下來。這一步逼你**先真的理解 bug**，而不是急著跳到 Rust。

### Step 2：定義 Rust 的資料結構
`struct List { head: Option<Box<Node>>, len: usize }` 和 `struct Node { value: i32, next: Option<Box<Node>> }`。想清楚為什麼 `next` 是 `Option<Box<Node>>` 而不是 `*mut Node` 或 `Box<Node>`。

### Step 3：實作 `new` / `push_front` / `len`
`push_front` 的關鍵是用 `self.head.take()` 把舊 head 拿出來（留下 `None`），再包一個新 `Box<Node>` 塞回去。想清楚為什麼不能直接 `self.head = Some(Box::new(Node { next: self.head, .. }))`（會撞「同時讀寫 self.head」）。

### Step 4：實作 `pop_front` / `find`
`pop_front` 用 `self.head.take().map(...)`，把後繼接回 head，舊節點在 map 的 closure 結束時自動 drop（**這就是 Rust 幫你 free**，不用手動）。`find` 用 `self.head.as_deref()` 拿到 `Option<&Node>`，while-let 走訪。

### Step 5：寫兩個 main——正確版與危險版
正確版：push / find（用完就放掉引用）/ pop / 印剩餘。跑通。
危險版：`let p = l.find(20).unwrap(); l.pop_front(); println!("{}", p);`——編譯它，貼出 `E0502` 錯誤。這是整個練習的高潮：**你想寫出 C 的那個 UAF，但寫不出來**。

---

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西——這個練習的價值在你親手撞 borrow checker，不是抄答案。

<details>
<summary>點開 C 端 bug 分析與 ASan 輸出</summary>

**bug 分析**：`find` 回傳 `&n->value`，指向鏈上某節點內部。`main` 拿到 `p` 指向 value==20 的節點。接著兩次 `pop_front`：第一次 free 掉 head（value 30），第二次 free 掉 value==20 那個節點——**正是 `p` 指向的節點**。此後 `*p` 讀的是已 `free` 的 heap 記憶體，就是 heap-use-after-free。C 完全不追蹤「`p` 借了那個節點」，`free` 照 free、`*p` 照讀，UB。

真跑（`gcc -g -fsanitize=address buggy_list.c -o buggy_list && ./buggy_list`）：

```
found 20
=================================================================
==237508==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000030 at pc 0x5e2265969711 bp 0x7ffe47b43db0 sp 0x7ffe47b43da0
READ of size 4 at 0x502000000030 thread T0
    #0 0x5e2265969710 in main /mnt/d/tmp_rust_verify/buggy_list.c:53
    #1 0x7ef721629d8f in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58
    ...
freed by thread T0 here:
    #0 0x7ef721ab4537 in __interceptor_free ...
    #1 0x5e22659694f9 in pop_front /mnt/d/tmp_rust_verify/buggy_list.c:36
    #2 0x5e22659696d6 in main /mnt/d/tmp_rust_verify/buggy_list.c:50
    ...
previously allocated by thread T0 here:
    #0 0x7ef721ab4887 in __interceptor_malloc ...
    #1 0x5e22659692e5 in push_front /mnt/d/tmp_rust_verify/buggy_list.c:15
    ...
SUMMARY: AddressSanitizer: heap-use-after-free /mnt/d/tmp_rust_verify/buggy_list.c:53 in main
```

ASan 精確標出：line 53（`*p` 那行）READ、line 36（`pop_front` 裡的 `free`）freed、line 15（`push_front` 裡的 `malloc`）allocated。三行串起來就是完整的 UAF 因果鏈。注意 ASan 是 **runtime** 才抓到的——要真的跑到那行才報。Rust 是**編譯期**擋，根本跑不到。

</details>

<details>
<summary>點開 safe Rust 正確版（真跑過）</summary>

```rust
// safe_list.rs
pub struct List {
    head: Option<Box<Node>>,
    len: usize,
}

struct Node {
    value: i32,
    next: Option<Box<Node>>,
}

impl List {
    pub fn new() -> Self {
        List { head: None, len: 0 }
    }

    pub fn push_front(&mut self, v: i32) {
        // take() 把 self.head 換成 None 並拿走舊值，避免同時可變+讀取衝突
        let old_head = self.head.take();
        self.head = Some(Box::new(Node { value: v, next: old_head }));
        self.len += 1;
    }

    pub fn pop_front(&mut self) -> Option<i32> {
        self.head.take().map(|boxed| {
            self.head = boxed.next; // 把後繼接回 head，舊節點在此 scope 結束被 drop
            self.len -= 1;
            boxed.value
        })
    }

    // 回傳的 &i32 借用了 &self；只要這個引用還活著，&mut self（pop_front）就借不到
    pub fn find(&self, target: i32) -> Option<&i32> {
        let mut cur = self.head.as_deref();
        while let Some(node) = cur {
            if node.value == target {
                return Some(&node.value);
            }
            cur = node.next.as_deref();
        }
        None
    }

    pub fn len(&self) -> usize {
        self.len
    }
}

fn main() {
    let mut l = List::new();
    l.push_front(10);
    l.push_front(20);
    l.push_front(30);
    println!("len = {}", l.len());

    if let Some(v) = l.find(20) {
        println!("found {}", v);
    }

    println!("popped {:?}", l.pop_front());
    println!("popped {:?}", l.pop_front());
    println!("len = {}", l.len());

    // 迭代印出剩下的
    let mut cur = l.head.as_deref();
    while let Some(node) = cur {
        println!("remaining {}", node.value);
        cur = node.next.as_deref();
    }
}
```

真跑（`rustc safe_list.rs -o safe_list && ./safe_list`）：

```
len = 3
found 20
popped Some(30)
popped Some(20)
len = 1
remaining 10
```

**解答說明**：
- `next: Option<Box<Node>>` 是 Rust 表達「可能有下一個節點、且我擁有它」的標準寫法。`Option` 給你 null 的安全版（沒有節點就是 `None`，不是 dangling pointer），`Box` 給你 heap 配置 + 唯一擁有權。整條鏈的 drop 是遞迴的：drop `List` → drop `head` → drop 它的 `next` → …… 全自動，沒有手寫 `free`。
- `push_front` 用 `self.head.take()`：直接寫 `Node { next: self.head, .. }` 會被擋，因為你在同一個 expression 裡「move 出 self.head」又要「寫回 self.head」，違反獨佔。`take()` 原子地「拿走舊值、留下 `None`」，是操作鏈結結構的慣用法。
- `pop_front` 裡舊節點 `boxed` 在 closure 結束時離開 scope，Rust 自動 drop 它（等於 C 的 `free`）——你根本沒機會忘記 free，也沒機會 double-free。
- `find` 回傳 `Option<&i32>`，`&i32` 借用了 `&self`。`as_deref()` 把 `&Option<Box<Node>>` 轉成 `Option<&Node>`（順手解掉 `Box`），是走訪這種結構的慣用法。

</details>

<details>
<summary>點開「危險版」——borrow checker 擋下 UAF（真跑過）</summary>

把 C 的 UAF 用法照抄進 Rust：

```rust
// safe_list_uaf.rs（節錄 main；List 定義同上）
fn main() {
    let mut l = List::new();
    l.push_front(10);
    l.push_front(20);
    let p = l.find(20).unwrap(); // 不可變借用 l
    l.pop_front();               // 想可變借用 l → 衝突
    println!("stale read: {}", p);
}
```

真跑（`rustc safe_list_uaf.rs`）：

```
error[E0502]: cannot borrow `l` as mutable because it is also borrowed as immutable
  --> safe_list_uaf.rs:32:5
   |
31 |     let p = l.find(20).unwrap(); // 不可變借用 l
   |             - immutable borrow occurs here
32 |     l.pop_front();               // 想可變借用 l → 衝突
   |     ^^^^^^^^^^^^^ mutable borrow occurs here
33 |     println!("stale read: {}", p);
   |                                - immutable borrow later used here
```

**解答說明**：這是整個練習的重點。C 版的 UAF 在 Rust 裡**連編都編不過**：
- `l.find(20)` 回傳的 `p` 借用了 `l`（共享借用），這張「租約」一直活到最後一次用 `p`（line 33）。
- `l.pop_front()` 需要 `&mut l`（獨佔借用），但 `p` 的共享借用還沒到期（line 33 還要用）。
- 「共享借用存在時不能有可變借用」——borrow checker 在 line 32 就拒絕。

對照 C：C 的 `find` 回傳的 `p` 和 `l` 之間**沒有任何型別層級的關聯**，`pop_front` 照 free、`*p` 照讀，runtime 才炸。Rust 把「`p` 借了 `l`」編進型別，於是「借用期間改動來源」在編譯期就是型別錯誤。**你想寫 UAF 都寫不出來**——這就是 memory safety 的具體含義。

</details>

---

## 測試用例

| 操作序列 | 預期結果 | 說明 |
|---|---|---|
| push 10,20,30 → len() | 3 | 基本插入 |
| find(20) | `Some(&20)` | 找得到，回傳借用 |
| find(99) | `None` | 找不到 |
| pop → pop → len() | 各回 `Some(30)`,`Some(20)`，len=1 | LIFO 順序 + 自動 free |
| 空 list pop_front() | `None` | 邊界：空表 pop 不 panic |
| find 後緊接 pop（同時用 p） | **編譯錯誤 E0502** | 核心驗收：UAF 被擋 |

空表 pop 的邊界值得單獨驗一下——`self.head.take()` 在 `None` 時 `map` 不執行，直接回 `None`，不會 panic。這比 C 版安全（C 的 `pop_front` 對空表會 `old->next` 解 NULL 指標，直接 segfault）。

---

## 延伸挑戰（加分）

- **泛型化**：把 `List` 改成 `List<T>`，裝任意型別。`find` 需要 `where T: PartialEq`。想清楚為什麼 `find(&self, target: &T)` 的參數要收 `&T` 而不是 `T`（提示：不是所有 `T` 都是 `Copy`）。參考解答如下：

<details>
<summary>點開泛型版（真跑過）</summary>

```rust
pub struct List<T> {
    head: Option<Box<Node<T>>>,
    len: usize,
}
struct Node<T> {
    value: T,
    next: Option<Box<Node<T>>>,
}
impl<T> List<T> {
    pub fn new() -> Self { List { head: None, len: 0 } }
    pub fn push_front(&mut self, v: T) {
        let old = self.head.take();
        self.head = Some(Box::new(Node { value: v, next: old }));
        self.len += 1;
    }
    pub fn pop_front(&mut self) -> Option<T> {
        self.head.take().map(|b| {
            self.head = b.next;
            self.len -= 1;
            b.value
        })
    }
    pub fn find(&self, target: &T) -> Option<&T>
    where T: PartialEq {
        let mut cur = self.head.as_deref();
        while let Some(n) = cur {
            if &n.value == target { return Some(&n.value); }
            cur = n.next.as_deref();
        }
        None
    }
}
fn main() {
    let mut l: List<String> = List::new();
    l.push_front("alpha".to_string());
    l.push_front("beta".to_string());
    println!("{:?}", l.find(&"alpha".to_string()));
    println!("popped {:?}", l.pop_front());
    println!("len {}", l.len);
}
```

真跑輸出：

```
Some("alpha")
popped Some("beta")
len 1
```

</details>

- **環形緩衝區（ring buffer）**：改用固定容量的 ring buffer（內部 `Vec<Option<T>>` + head/tail 索引），實作 `push`（滿了回 `Err`）/ `pop`。體會 Rust 怎麼用 `Option<T>` 表達「這個槽位空著」，而不是 C 的 magic value（如 `-1` 代表空）。C 版 ring buffer 的經典 bug 是 head/tail 回繞（wrap-around）時的 off-by-one 導致讀到舊資料——想想 Rust 的邊界檢查怎麼把它變成 panic 而非沉默的錯誤讀取。

- **drop 順序爆棧**：給你的 `List` 塞十萬個節點再 drop，預設的遞迴 drop 可能 stack overflow。查一下怎麼手動實作 `Drop` 用迴圈拆鏈（這是 std `LinkedList` 也要處理的真實問題）。

---

## 自我檢核

- [ ] 我能不看 ASan 報告，光讀 C 原始碼就指出 UAF 在哪、`p` 為什麼會懸空。
- [ ] 我能解釋為什麼 Rust 的 `next` 用 `Option<Box<Node>>`，以及這個型別怎麼同時解決了 null 安全與擁有權。
- [ ] 我能說出 `E0502` 錯誤裡三個底線標註（immutable borrow / mutable borrow / later used）各對應 C 版的什麼行為。
- [ ] 我能講清楚「C 是 runtime（ASan）抓、Rust 是編譯期擋」的差別，以及為什麼後者更強。
- [ ] 能說出我的實作和參考解答的差異，並解釋各自取捨（例如你有沒有用 `take()`、有沒有另外處理空表）。

這個練習你驗證了 Part 1 的核心命題：ownership/borrow 不是煩人的規則，是把 C 的整類記憶體 bug 從「靠紀律避免」變成「型別系統保證不發生」。Part 2 起我們往上蓋抽象——trait、泛型、錯誤處理——但地基就是你剛親手撞過的這套。

→ [Ch 8 Struct、Enum 與 Pattern Matching](./08-struct-enum-pattern.md)
