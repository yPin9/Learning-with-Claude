# Ch 27 — Lock-Free 資料結構

> 目標：理解 lock-free 的核心原語（CAS、LL/SC），能實作 lock-free stack 與 ring buffer，並理解 ABA problem 與解決方法。

## Lock-Free 的定義

- **Lock-free**：至少有一個執行緒能持續前進（不是說沒有 spin）
- **Wait-free**：所有執行緒都能在有限步驟內完成（更強，更難實作）
- **Obstruction-free**：只要沒有競爭，單個執行緒就能前進（最弱）

Mutex 是不 lock-free 的：若持鎖的執行緒被 OS preempt，其他執行緒全部卡住。

---

## Compare-And-Swap（CAS）

Lock-free 的核心原語：

```c
// 語意：
bool CAS(addr, expected, desired) {
    if (*addr == expected) {
        *addr = desired;
        return true;
    }
    expected = *addr;   // 更新 expected（C11 的 compare_exchange）
    return false;
}

// C11 實作：
#include <stdatomic.h>
atomic_int x = ATOMIC_VAR_INIT(0);

int expected = 5;
bool ok = atomic_compare_exchange_strong(&x, &expected, 10);
// 若 x == 5：x = 10，ok = true
// 若 x != 5：expected = x 的當前值，ok = false
```

`weak` vs `strong`：
- `strong`：只在 x != expected 時回傳 false
- `weak`：即使 x == expected，也可能 spurious failure（用在迴圈裡）

---

## Lock-Free Stack

```c
typedef struct Node { int value; struct Node *next; } Node;

typedef struct {
    _Atomic(Node *) top;
} LFStack;

void lf_push(LFStack *s, Node *node) {
    Node *top;
    do {
        top = atomic_load_explicit(&s->top, memory_order_relaxed);
        node->next = top;
    } while (!atomic_compare_exchange_weak_explicit(
                &s->top, &top, node,
                memory_order_release,
                memory_order_relaxed));
    // CAS：若 top 沒變 → 成功，s->top = node
    //      若 top 已被其他執行緒改變 → 重試
}

Node *lf_pop(LFStack *s) {
    Node *top;
    Node *next;
    do {
        top = atomic_load_explicit(&s->top, memory_order_acquire);
        if (!top) return NULL;
        next = top->next;
    } while (!atomic_compare_exchange_weak_explicit(
                &s->top, &top, next,
                memory_order_release,
                memory_order_relaxed));
    return top;
}
```

---

## ABA Problem

```
執行緒 T1：讀到 top = A
             被 preempt
執行緒 T2：pop A（A.next = B）→ top = B
           pop B → top = NULL
           push A → top = A（A 的地址相同，但 A.next 已被改）
T1 恢復：CAS(&top, A, B) 成功！因為 top 仍然是 A
         但 B 已經不在 stack 上了 → 資料損壞
```

**ABA problem**：CAS 只比較地址，無法偵測值被改又改回來。

**解法一：Tagged Pointer**

```c
// 在指標的高位 bits 存版本號（AArch64 的 PAC / x86_64 的 56-bit virtual address 特性）：
typedef struct {
    uintptr_t ptr : 48;    // 48-bit 虛擬地址（x86_64）
    uintptr_t tag : 16;    // 16-bit 版本號
} TaggedPtr;
// 每次 pop 後 push，tag++，讓 CAS 能偵測到 ABA
```

**解法二：Hazard Pointer**（更複雜但更通用）

**解法三：Epoch-Based Reclamation**

---

## Lock-Free SPSC Ring Buffer

Single-Producer Single-Consumer（SPSC）的 ring buffer 不需要 CAS，只需要 load/store 配合 memory order：

```c
#define RING_SIZE 1024   // 必須是 2 的冪次
typedef struct {
    int data[RING_SIZE];
    atomic_size_t head;   // producer 寫
    atomic_size_t tail;   // consumer 讀
} SPSCRing;

bool ring_push(SPSCRing *r, int val) {
    size_t h = atomic_load_explicit(&r->head, memory_order_relaxed);
    size_t next_h = (h + 1) & (RING_SIZE - 1);
    size_t t = atomic_load_explicit(&r->tail, memory_order_acquire);
    if (next_h == t) return false;   // full
    r->data[h] = val;
    atomic_store_explicit(&r->head, next_h, memory_order_release);
    return true;
}

bool ring_pop(SPSCRing *r, int *val) {
    size_t t = atomic_load_explicit(&r->tail, memory_order_relaxed);
    size_t h = atomic_load_explicit(&r->head, memory_order_acquire);
    if (t == h) return false;   // empty
    *val = r->data[t];
    atomic_store_explicit(&r->tail, (t + 1) & (RING_SIZE - 1), memory_order_release);
    return true;
}
```

Release/Acquire 語意保證：producer 寫入 `data[h]` 後再 release `head`；consumer acquire `head` 後才讀 `data`，確保看到完整的寫入。

---

## 什麼時候用 Lock-Free

Lock-free 比 mutex 難寫得多，不要輕易上。值得考慮的場景：
1. **高競爭、短 critical section**：mutex 的 syscall overhead 大於競爭本身
2. **不能容忍 priority inversion**（RT 系統）
3. **Signal handler 內**（mutex 在 signal handler 裡不安全）

大多數時候，用 mutex + condition variable 就夠了。

---

## 偵測工具

```bash
# ThreadSanitizer 也可以偵測 lock-free 程式的問題：
gcc -fsanitize=thread -g prog.c -o prog
./prog
```

---

## 自我檢核

- [ ] 能說出 lock-free vs mutex 的適用場景差異
- [ ] 能用 CAS loop 實作 lock-free push
- [ ] 能解釋 ABA problem（地址相同但值已改變）
- [ ] 知道 SPSC ring buffer 只需要 load/store，不需要 CAS

→ [Ch 28 經典 C 陷阱題 40 道](./28-classic-traps-40.md)
