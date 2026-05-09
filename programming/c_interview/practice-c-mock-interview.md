# 練習 C — 模擬面試 30 題

> 目標：模擬真實面試場景。每題限時 3 分鐘回答，答不出來的題目標記起來，優先複習對應的章節。

**使用方式**：
1. 遮住答案，看題目說出或寫出答案
2. 對答案，記下不確定的題目
3. 兩天後重新測試標記的題目

---

## Section A：記憶體與 UB（Q1–Q10）

**Q1**：`int * const p` 和 `const int *p` 有什麼差別？

<details><summary>答案</summary>

`int * const p`：`p` 是 const，即指標本身不能改（不能讓 p 指向別處），但 `*p` 可以修改。

`const int *p`：`p` 指向的值是 const（`*p` 不能修改），但 p 本身可以改（可以讓 p 指向別的 const int）。

記憶法：const 的位置在 `*` 的左邊 → 保護值；在 `*` 的右邊 → 保護指標本身。

</details>

---

**Q2**：以下程式碼有什麼問題？

```c
char *p = "hello";
p[0] = 'H';
```

<details><summary>答案</summary>

字串常數 `"hello"` 存在 text segment（唯讀區域）。`p[0] = 'H'` 嘗試修改唯讀記憶體，是 UB，在大多數系統上會 segfault。

正確做法：`char p[] = "hello";`（複製到 stack 上的可修改陣列）。

</details>

---

**Q3**：`sizeof("hello")` 和 `strlen("hello")` 分別是多少？

<details><summary>答案</summary>

`sizeof("hello") = 6`（含 `'\0'` 的陣列大小，編譯期求值）。

`strlen("hello") = 5`（不含 `'\0'` 的字元數，執行期求值）。

</details>

---

**Q4**：為什麼有號整數溢位是 UB，但無號整數環繞不是？

<details><summary>答案</summary>

C 標準的設計選擇：
- **無號整數**：標準明確規定模 2^N 算術（環繞），是 well-defined behavior
- **有號整數**：C 允許使用 ones' complement、sign-magnitude 表示（雖然現在都用 two's complement），標準無法統一定義溢位結果，故定義為 UB

實際上，現代 CPU 都用 two's complement，有號溢位的結果確定，但編譯器被允許假設它不發生並基於此做優化。

</details>

---

**Q5**：以下兩種 free 的寫法，哪個更安全？為什麼？

```c
// 版本 A：
free(ptr);

// 版本 B：
free(ptr);
ptr = NULL;
```

<details><summary>答案</summary>

版本 B 更安全。`free` 後將指標清 NULL：
1. `free(NULL)` 是合法的 no-op，所以再次 `free(ptr)` 不會崩潰（double free 的保護）
2. dereference NULL 會立刻 segfault，讓 bug 暴露得更早（而不是 use-after-free 的隱性 bug）

但版本 B 有局限：若還有其他指標指向同一塊記憶體，清 NULL 只保護 `ptr`，那些 dangling pointer 仍然危險。

</details>

---

**Q6**：`struct padding` — 以下 struct 的 sizeof 是多少？

```c
struct S {
    char  a;     // 1 byte
    int   b;     // 4 bytes
    char  c;     // 1 byte
    short d;     // 2 bytes
};
```

<details><summary>答案</summary>

`sizeof(struct S) = 12`。

佈局：
- `a`：offset 0，1 byte
- padding：3 bytes（讓 `b` 對齊到 4）
- `b`：offset 4，4 bytes
- `c`：offset 8，1 byte
- padding：1 byte（讓 `d` 對齊到 2）
- `d`：offset 10，2 bytes
- 總計 12 bytes（struct 最大對齊是 4，整體大小是 4 的倍數）

重排可以節省空間：`int b, short d, char a, char c` → sizeof = 8。

</details>

---

**Q7**：`strncpy(dst, src, n)` 有什麼陷阱？

<details><summary>答案</summary>

1. 若 `src` 長度 >= n，`dst` 不會有 `'\0'` 終止符（後續用 `strlen`、`printf` 等會讀越界）
2. 若 `src` 長度 < n，用 `'\0'` 填滿剩餘空間（浪費，特別是 n 很大時）

安全做法：`snprintf(dst, n, "%s", src)` 或 `strlcpy`（BSD/macOS 有）。

</details>

---

**Q8**：解釋 strict aliasing rule。以下程式碼為什麼是 UB？

```c
float f = 3.14f;
unsigned int *p = (unsigned int *)&f;
printf("%u\n", *p);
```

<details><summary>答案</summary>

C99 §6.5 嚴格別名規則：只能透過相容型別的指標存取物件。`float` 和 `unsigned int` 不相容（不是彼此的有號/無號版本），所以透過 `unsigned int *` 存取 `float` 物件是 UB。

合法的 type punning：
1. `memcpy(&bits, &f, sizeof(bits))`
2. `union { float f; unsigned u; } u; u.f = f; use(u.u);`

</details>

---

**Q9**：以下函式返回的指標是否安全使用？

```c
int *foo(void) {
    static int x = 42;
    return &x;
}
```

<details><summary>答案</summary>

**安全**，但有限制。`static` 局部變數存在 data segment，生命周期是整個程式，不是 stack frame。所以 `foo()` 返回後，`x` 的記憶體仍然有效。

限制：
1. 不是執行緒安全（所有執行緒共享同一個 `x`）
2. 每次呼叫 `foo()` 回傳相同地址（若修改，影響之前拿到的指標）

</details>

---

**Q10**：解釋 `volatile` 的作用和它**做不到**的事。

<details><summary>答案</summary>

**能做的**：告訴編譯器「這個變數可能在編譯器無法察覺的時機被改變」，因此每次存取都必須真正讀/寫記憶體，不能使用快取的暫存器值。

適用場景：MMIO 暫存器、ISR 修改的旗標。

**做不到的**：
1. 不提供原子性（`counter++` 仍是 load-add-store 三步，有 data race）
2. 不提供記憶體排序（CPU 仍可亂序執行）
3. 多執行緒同步必須用 `_Atomic` 或 mutex

</details>

---

## Section B：指標與陣列（Q11–Q20）

**Q11**：函式參數 `int arr[]` 和 `int *arr` 有什麼差別？

<details><summary>答案</summary>

**沒有差別**。函式參數的陣列宣告會自動 decay 成指標。兩種寫法完全等價，編譯器看到的都是 `int *`。

這意味著 `sizeof(arr)` 在函式內是指標大小，不是陣列大小。

</details>

---

**Q12**：`int (*p)[5]` 和 `int *p[5]` 的差別？

<details><summary>答案</summary>

`int (*p)[5]`：`p` 是指標，指向含 5 個 int 的陣列（array pointer）。

`int *p[5]`：`p` 是陣列，含 5 個 `int *`（pointer array）。

讀法：右到左，先括號內。

</details>

---

**Q13**：`p + 1` 和 `(char *)p + 1` 若 p 是 `int *`，結果差多少 bytes？

<details><summary>答案</summary>

`p + 1`：移動 `sizeof(int) = 4` bytes（指標算術是型別相關的）。

`(char *)p + 1`：移動 1 byte（cast 成 char*，然後加 1）。

</details>

---

**Q14**：`void *` 可以做指標算術嗎？

<details><summary>答案</summary>

**C 標準說不行**（`void *` 沒有大小，無法計算步長）。但 GCC 擴充允許（將 `void *` 視為 `char *` 做算術），這是 non-standard 行為。

可移植的做法：先 cast 到 `char *` 再做算術。

</details>

---

**Q15**：以下程式碼輸出什麼？

```c
int a[5] = {1,2,3,4,5};
printf("%d\n", *(&a[0] + 2));
printf("%d\n", *(a + 2));
printf("%d\n", a[2]);
```

<details><summary>答案</summary>

三行都輸出 `3`。`a[2]`、`*(a+2)`、`*(&a[0]+2)` 完全等價。

</details>

---

**Q16–Q20**（略，有助於自測的方向：函式指標宣告、decay 例外、2D 陣列的 pointer 型別）

---

## Section C：並行（Q21–Q25）

**Q21**：`pthread_cond_wait` 為什麼要在 `while` 而非 `if` 裡？

<details><summary>答案</summary>

**Spurious wakeup**：POSIX 允許 `pthread_cond_wait` 在沒有 signal 的情況下自行返回。若用 `if`，spurious wakeup 後就直接繼續執行，可能在條件不滿足時就處理資料。

`while` 返回後重新檢查條件，能正確處理 spurious wakeup 和多 consumer 競爭（另一個 consumer 可能在你 wakeup 前先拿走了資料）。

</details>

---

**Q22**：data race 和 race condition 的差別？

<details><summary>答案</summary>

**Data race**（C11 的 UB）：兩個執行緒不帶同步地同時存取同一個記憶體位置，且至少一個是寫入。即使結果「看起來正確」，也是 UB。

**Race condition**：程式的正確性取決於事件的非確定性順序。可以沒有 data race 但有 race condition（e.g., check-then-act 的 TOCTOU）。

</details>

---

**Q23**：memory_order_relaxed vs memory_order_seq_cst 的差別？

<details><summary>答案</summary>

`memory_order_relaxed`：只保證操作的原子性，不提供 happens-before 語意。其他執行緒可能以不同順序看到操作。適合：計數器（只關心最終值，不關心操作順序）。

`memory_order_seq_cst`（預設）：最嚴格，所有 seq_cst 操作之間有全域一致的順序，所有執行緒都看到相同的順序。效能開銷最大（可能需要 memory fence 指令）。

</details>

---

**Q24**：ABA problem 是什麼？如何解決？

<details><summary>答案</summary>

CAS 只比較值，無法偵測：值從 A 變成 B，再變回 A 的情況。CAS 會成功，但中間的變化被忽略，可能導致資料結構損壞。

解法：
1. Tagged pointer（地址 + 版本號）
2. Hazard pointer（延遲釋放，保證 CAS 期間指標不被重用）
3. Epoch-based reclamation

</details>

---

**Q25**：spinlock vs mutex，什麼時候用哪個？

<details><summary>答案</summary>

**Spinlock**：busy-wait，不讓執行緒睡眠。
- 優點：無 context switch overhead
- 適用：critical section < 幾十 ns，CPU 數量充足（spinning 不浪費太多）
- 不適用：可能被 preempt（spinning 浪費 CPU），或 critical section 長

**Mutex**：sleeping-wait，無法取得鎖時讓執行緒睡眠，OS 喚醒。
- 優點：長時間等待不浪費 CPU
- 適用：一般情況，critical section 可能較長
- 開銷：至少一次 syscall（futex）

</details>

---

## Section D：效能與設計（Q26–Q30）

**Q26**：cache line 是多少 bytes？false sharing 是什麼？

<details><summary>答案</summary>

Cache line 通常是 **64 bytes**（x86_64）。

False sharing：兩個 core 的不同變數在同一個 cache line 上。當一個 core 寫入，另一個 core 的 cache line 失效（MESI 協議），即使它們存取的是不同變數，也需要重新從記憶體載入，嚴重降低多核效能。

解法：padding 讓各 core 的資料分到不同的 cache line，或用 `__attribute__((aligned(64)))`。

</details>

---

**Q27**：AoS vs SoA，各自的優缺點？

<details><summary>答案</summary>

**AoS（Array of Structures）**：`struct Particle particles[N]`
- 優點：存取單個物件的所有欄位連續（好的空間局部性）
- 缺點：批量處理某一欄位時，每個欄位中間有其他欄位插入（差的向量化）

**SoA（Structure of Arrays）**：`float x[N], y[N], z[N]`
- 優點：批量處理某欄位時完全連續，SIMD 向量化效率高
- 缺點：存取單個物件時需要存取多個陣列

選擇：需要 SIMD / 批量計算同欄位 → SoA；需要完整物件操作 → AoS。

</details>

---

**Q28**：malloc(n * sizeof(T)) 有什麼潛在問題？更安全的寫法是什麼？

<details><summary>答案</summary>

`n * sizeof(T)` 可能溢位（若 `n` 很大），導致 malloc 分配的空間遠小於需要的大小，然後越界寫入。

更安全的寫法：
```c
// 方法一：calloc 有內建溢位檢查
void *p = calloc(n, sizeof(T));

// 方法二：手動檢查
if (n > SIZE_MAX / sizeof(T)) { /* overflow */ }
void *p = malloc(n * sizeof(T));
```

</details>

---

**Q29**：explain POSIX signal handler 的 async-signal-safe 限制。

<details><summary>答案</summary>

Signal handler 可以在**任意時刻**打斷程式，包括在 malloc、printf 等函式的執行過程中。若 signal handler 再次呼叫這些函式，可能導致：
- malloc 的 lock 被同一執行緒重入 → deadlock
- FILE* 的 lock 被重入 → 資料損壞

因此 signal handler 只能呼叫 **async-signal-safe** 的函式（POSIX 定義了約 180 個，包括 `write`、`_exit`、`kill` 等），不能用 `printf`、`malloc`、`free`。

最常見的安全模式：在 handler 裡設 `volatile sig_atomic_t` 旗標，在主迴圈裡檢查。

</details>

---

**Q30**：設計一個 LRU cache，說明資料結構選擇和原因。

<details><summary>答案</summary>

**資料結構**：
1. **Hash map**：O(1) key 查找
2. **Doubly linked list**：O(1) 移到頭部（最近使用）、O(1) 刪除尾部（最舊）

**操作**：
- `get(key)`：hash map 找 node → 把 node 移到鏈表頭部 → 回傳值
- `put(key, val)`：
  - 若 key 存在：更新值 + 移到頭部
  - 若不存在且未滿：在頭部插入 + hash map 加入
  - 若不存在且滿：刪除鏈表尾部 + 從 hash map 移除 + 在頭部插入 + hash map 加入

所有操作 O(1)。

C 實作：`uthash`（開源 header-only hash map）+ 自製 doubly linked list。

</details>

---

## 自我評分

- 每題 3 分鐘內完整回答：1 分
- 能說出核心點但細節有漏：0.5 分
- 答不出或答錯：0 分

| 分數 | 狀態 |
|------|------|
| 25–30 | 準備充分，可以衝 senior / kernel 職位 |
| 20–24 | 良好，稍微複習標記的章節 |
| 15–19 | 需要回頭看 Ch 1–15 |
| < 15 | 從頭開始，每章的自我檢核都要確認 |

→ [Final Project：mini-libc 實作](./final-project-mini-libc.md)
