# Ch 10 — volatile、Sequence Point、記憶體順序

> 目標：理解 volatile 的正確用途（和它做不到的事），掌握 sequence point 規則，以及 C11 的記憶體模型基礎。

## volatile 的正確定義

`volatile` 告訴編譯器：**這個變數的值可能在編譯器無法察覺的時機被改變**，因此每次存取都必須真正讀/寫記憶體，不能使用快取的暫存器值。

```c
// 沒有 volatile：編譯器可能優化成：
// register int flag = 0;
// while (!flag) ;   →  while (true) ;（因為 flag 在迴圈裡從未改變）

volatile int flag = 0;

// 中斷或另一個執行緒修改 flag：
void ISR(void) { flag = 1; }

// 主程式等待：
while (!flag) ;   // 每次都真正讀記憶體，不會被優化掉
```

---

## volatile 能做什麼 / 不能做什麼

**能做的：**
- 防止編譯器對 memory-mapped I/O 暫存器做優化
- 防止編譯器消除「沒用到的」記憶體讀寫（如清除密碼）
- 正確讀取會被 ISR 修改的旗標變數

**不能做的（常見誤解）：**

```c
volatile int counter = 0;
counter++;   // 這不是原子操作！
// 實際上是 load-add-store 三個操作
// 多執行緒同時做 counter++ 仍有 data race
```

`volatile` **不提供原子性，不提供記憶體排序**。它只防止編譯器優化，不防止 CPU 亂序執行。多執行緒同步必須用 `_Atomic` 或 `pthread_mutex`（見 Ch 22）。

**嵌入式 + Memory-mapped I/O 正確用法（Ch 21 詳說）：**

```c
#define UART_TX (*(volatile uint8_t *)0x40000000)
UART_TX = 'A';   // 每次都真正寫入，不會被優化掉
```

---

## Sequence Point（序列點）

C 標準定義了「序列點」——在序列點之前的所有副作用必須完成，之後的尚未開始。

在同一個表達式裡，**若一個變數被修改，且又在序列點前被另一次讀取，結果是 UB**。

```c
int i = 5;
i = i++;        // UB：i 被修改，又在沒有序列點的地方被讀取
i = ++i + i;    // UB

// 安全的：分開兩個語句
int old = i;
i = old + 1;
```

序列點位置：`;`（語句結束）、`&&` 和 `||` 的短路點、`?:` 的條件求值後、函式呼叫之前。

```c
// 合法（兩個分開的語句）：
i++;
j = i;

// UB（同一個表達式，沒有序列點隔開）：
int a[5];
int idx = 0;
a[idx] = idx++;   // idx 在哪裡遞增？UB
```

---

## 函式引數的求值順序

```c
printf("%d %d\n", ++x, x++);   // 求值順序未定義（unspecified）
f(a(), b(), c());               // a/b/c 的呼叫順序未定義
```

這是 unspecified（不是 UB），但結果是不可預期的。

---

## C11 記憶體模型

C11 引入了正式的多執行緒記憶體模型。核心概念：

### 記憶體順序（Memory Order）

```c
#include <stdatomic.h>

atomic_int x = ATOMIC_VAR_INIT(0);

// 六種 memory order：
atomic_store_explicit(&x, 1, memory_order_relaxed);   // 最寬鬆：只保證原子
atomic_store_explicit(&x, 1, memory_order_release);    // 此前的寫入對 acquire 可見
int v = atomic_load_explicit(&x, memory_order_acquire);// 看到 release 之前的寫入
atomic_store_explicit(&x, 1, memory_order_seq_cst);    // 最嚴格：全域順序一致
```

**最常用的兩個：**

- `memory_order_seq_cst`（預設）：最安全，效能最差
- `release`/`acquire` 配對：producer/consumer 模式的標準做法

### Producer-Consumer 範例

```c
atomic_int data_ready = ATOMIC_VAR_INIT(0);
int shared_data;

// Thread 1（Producer）：
shared_data = 42;                                          // 先寫資料
atomic_store_explicit(&data_ready, 1, memory_order_release); // 再發布旗標

// Thread 2（Consumer）：
while (!atomic_load_explicit(&data_ready, memory_order_acquire)) ;
printf("%d\n", shared_data);  // 保證看到 42
// release 確保 shared_data = 42 在 data_ready = 1 之前完成
// acquire 確保 shared_data 的讀取在看到 data_ready = 1 之後
```

---

## volatile 和 _Atomic 的差異

| | `volatile` | `_Atomic` |
|-|-----------|-----------|
| 防止編譯器優化 | ✅ | ✅ |
| 原子性 | ❌ | ✅ |
| 記憶體排序 | ❌ | ✅ |
| 適合多執行緒 | ❌ | ✅ |
| 適合 ISR/MMIO | ✅ | 視情況 |

嵌入式中，`volatile` 足以應對 ISR 修改、MMIO 暫存器。但跨核心的共享資料必須用 `_Atomic` 或互斥鎖。

---

## 自我檢核

- [ ] 能說出 `volatile` 防止的和做不到的事
- [ ] 知道 `volatile int counter; counter++` 不是原子操作
- [ ] 能說出 `i = i++` 是 UB 的原因（同一表達式內 modify + read 無 sequence point）
- [ ] 知道 `memory_order_release` 和 `memory_order_acquire` 的配對語意

Part 2 結束。練習 A 整合所有 UB 概念。

→ [練習 A：UB 偵錯題集](./practice-a-ub-debug.md)
