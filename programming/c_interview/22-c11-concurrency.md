# Ch 22 — C11 並行：_Atomic 與 pthread

> 目標：掌握 C11 原子操作、mutex / condition variable，以及 spinlock 的實作，能解釋 data race 和 race condition 的差異。

## Data Race vs Race Condition

這兩個詞常被混用，但定義不同：

**Data race**（C11 的 UB）：兩個執行緒不帶同步地同時存取同一個記憶體位置，且至少一個是寫入。即使結果「看起來對」，也是 UB——編譯器和 CPU 可以做任意優化。

**Race condition**：程式的正確性取決於事件的順序。即使用了 atomic 消除了 data race，邏輯上仍可能有問題（e.g., check-then-act 的 TOCTOU）。

---

## C11 _Atomic

```c
#include <stdatomic.h>

atomic_int counter = ATOMIC_VAR_INIT(0);

// 原子加法（返回舊值）：
int old = atomic_fetch_add(&counter, 1);

// 原子讀：
int val = atomic_load(&counter);

// 原子寫：
atomic_store(&counter, 42);

// CAS（Compare-And-Swap）：
int expected = 5;
bool ok = atomic_compare_exchange_strong(&counter, &expected, 10);
// 若 counter == expected：counter = 10，ok = true
// 若 counter != expected：expected = counter 的當前值，ok = false
```

**`++` 不等於 atomic**（Ch 10 重申）：

```c
atomic_int x = ATOMIC_VAR_INIT(0);
x++;            // 語法上合法，等同 atomic_fetch_add(&x, 1)，是原子的
                // 但只有 _Atomic 型別的 ++ 才原子，int 的 ++ 不是！
```

---

## Memory Order

Ch 10 的 release/acquire 更完整版：

```c
// 最嚴格（預設）：全域 sequential consistent 順序
atomic_store_explicit(&x, 1, memory_order_seq_cst);
int v = atomic_load_explicit(&x, memory_order_seq_cst);

// 最寬鬆：只保證原子性，不保證可見性順序
atomic_store_explicit(&x, 1, memory_order_relaxed);

// Producer-consumer 配對（最常用的非 seq_cst 選擇）：
atomic_store_explicit(&ready, 1, memory_order_release); // producer
while (!atomic_load_explicit(&ready, memory_order_acquire)) ; // consumer
// release 確保 store ready 前的所有寫入，在 acquire 後可見
```

---

## pthread_mutex_t

```c
#include <pthread.h>
#include <stdio.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int shared = 0;

void *worker(void *arg) {
    for (int i = 0; i < 100000; i++) {
        pthread_mutex_lock(&lock);
        shared++;
        pthread_mutex_unlock(&lock);
    }
    return NULL;
}

int main(void) {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, worker, NULL);
    pthread_create(&t2, NULL, worker, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("shared = %d\n", shared);   // 保證是 200000
    pthread_mutex_destroy(&lock);
    return 0;
}
```

**Mutex 的常見陷阱**：

```c
// 死鎖（Deadlock）：
pthread_mutex_lock(&lock_a);
pthread_mutex_lock(&lock_b);   // 若另一個執行緒先拿 lock_b 再拿 lock_a → 死鎖

// 預防：永遠按固定順序加鎖
// 偵測：-fsanitize=thread（ThreadSanitizer）
```

---

## Condition Variable

用於「等待某個條件成真」的場景（生產者-消費者佇列）：

```c
pthread_mutex_t mtx  = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t  cond = PTHREAD_COND_INITIALIZER;
int data_ready = 0;
int data_value = 0;

// 消費者執行緒：
void *consumer(void *arg) {
    pthread_mutex_lock(&mtx);
    while (!data_ready) {          // 必須是 while（不是 if）！spurious wakeup
        pthread_cond_wait(&cond, &mtx);   // 自動 unlock，等待，然後 relock
    }
    printf("Got: %d\n", data_value);
    pthread_mutex_unlock(&mtx);
    return NULL;
}

// 生產者執行緒：
void *producer(void *arg) {
    pthread_mutex_lock(&mtx);
    data_value = 42;
    data_ready = 1;
    pthread_cond_signal(&cond);   // 喚醒一個等待中的執行緒
    pthread_mutex_unlock(&mtx);
    return NULL;
}
```

`pthread_cond_wait` 必須在 lock 保護下呼叫，且等待條件要用 `while` 而非 `if`——因為 **spurious wakeup**（系統可能在沒有 signal 的情況下喚醒執行緒）。

---

## 自製 Spinlock

```c
typedef struct { atomic_int locked; } Spinlock;

static inline void spin_lock(Spinlock *s) {
    int expected = 0;
    while (!atomic_compare_exchange_weak(
                &s->locked, &expected, 1,
                memory_order_acquire, memory_order_relaxed)) {
        expected = 0;   // CAS 失敗會修改 expected，要重置
        // ARM 上可加 __builtin_ia32_pause() 或 wfe 減少總線競爭
    }
}

static inline void spin_unlock(Spinlock *s) {
    atomic_store_explicit(&s->locked, 0, memory_order_release);
}
```

**Spinlock vs Mutex**：
- Spinlock：busy-wait，適合 critical section 極短（< 幾十 ns）且確定不會 preempt
- Mutex：sleeping-wait，有 context switch overhead，但適合長時間等待

---

## ThreadSanitizer（TSan）

```bash
gcc -fsanitize=thread -g prog.c -o prog
./prog
```

輸出：
```
WARNING: ThreadSanitizer: data race (pid=...)
  Write of size 4 at 0x... by thread T2:
    #0 worker prog.c:8
  Previous read of size 4 at 0x... by thread T1:
    #0 worker prog.c:8
```

TSan 和 ASan 不能同時用（都需要 shadow memory，會衝突）。

---

## Thread-Local Storage

```c
// C11 關鍵字：
_Thread_local int errno_copy;   // 每個執行緒有自己的 copy

// GCC 擴充（等價）：
__thread int per_thread_id;

// 使用：
per_thread_id = (int)pthread_self();   // 設定，只影響當前執行緒
```

---

## 自我檢核

- [ ] 能說出 data race（C11 UB）和 race condition（邏輯錯誤）的差異
- [ ] 知道 `atomic_compare_exchange_weak` vs `_strong` 的差異（weak 允許 spurious failure）
- [ ] 知道 `pthread_cond_wait` 必須用 `while` 的原因（spurious wakeup）
- [ ] 知道 spinlock 和 mutex 的適用場景差異

→ [Ch 23 signal 與 setjmp/longjmp](./23-signal-setjmp.md)
