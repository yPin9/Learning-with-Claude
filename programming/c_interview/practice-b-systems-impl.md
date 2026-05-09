# 練習 B — 系統層實作題

> 目標：把 Ch 11–27 的記憶體、並行、效能知識整合到完整實作，每題要能在 30 分鐘內完成。

---

## 題目 1：可擴展 Memory Pool

**需求**：實作一個支援多種大小物件的 memory pool，能動態擴展，且可以一次釋放全部。

**API**：

```c
typedef struct Pool Pool;

Pool  *pool_create(void);           // 建立 pool
void  *pool_alloc(Pool *p, size_t size);  // 分配 size bytes，不要求個別 free
void   pool_reset(Pool *p);         // 釋放所有分配的記憶體（重置到初始狀態）
void   pool_destroy(Pool *p);       // 釋放 pool 本身
size_t pool_used(const Pool *p);    // 目前已使用多少 bytes
```

**要求**：
- 分配對齊到 8 bytes
- 若當前 block 空間不足，自動 malloc 一個新 block（從 4KB 開始，每次加倍）
- `pool_reset` 不呼叫 free（保留 block 備用，下次分配時直接用）

<details>
<summary>參考解答</summary>

```c
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#define POOL_INIT_BLOCK_SIZE (4 * 1024)

typedef struct PoolBlock {
    struct PoolBlock *next;
    size_t            capacity;
    size_t            used;
    // data 緊接在後面（flexible array member 的手動版本）
} PoolBlock;

struct Pool {
    PoolBlock *current;
    PoolBlock *all;           // 所有 block 的鏈表（reset 時遍歷）
    size_t     total_used;
    size_t     next_block_size;
};

static PoolBlock *block_new(size_t size) {
    PoolBlock *b = (PoolBlock *)malloc(sizeof(PoolBlock) + size);
    if (!b) return NULL;
    b->next     = NULL;
    b->capacity = size;
    b->used     = 0;
    return b;
}

Pool *pool_create(void) {
    Pool *p = (Pool *)malloc(sizeof(Pool));
    if (!p) return NULL;
    p->next_block_size = POOL_INIT_BLOCK_SIZE;
    p->current         = block_new(POOL_INIT_BLOCK_SIZE);
    p->all             = p->current;
    p->total_used      = 0;
    if (!p->current) { free(p); return NULL; }
    return p;
}

void *pool_alloc(Pool *p, size_t size) {
    size = (size + 7) & ~(size_t)7;   // 8-byte 對齊

    // 若當前 block 不夠
    if (p->current->used + size > p->current->capacity) {
        // 新 block 大小：max(下個加倍大小, 請求大小)
        size_t new_size = p->next_block_size;
        if (new_size < size) new_size = size;
        p->next_block_size *= 2;

        PoolBlock *nb = block_new(new_size);
        if (!nb) return NULL;
        nb->next  = p->all;
        p->all    = nb;
        p->current = nb;
    }

    // 從 current block 的尾端分配
    uint8_t *data = (uint8_t *)(p->current + 1);
    void    *ptr  = data + p->current->used;
    p->current->used += size;
    p->total_used    += size;
    return ptr;
}

void pool_reset(Pool *p) {
    // 重置所有 block，不 free（下次分配可重用）
    for (PoolBlock *b = p->all; b; b = b->next)
        b->used = 0;
    // 讓 current 指向最大的 block（效率更好）
    p->current    = p->all;
    p->total_used = 0;
}

void pool_destroy(Pool *p) {
    PoolBlock *b = p->all;
    while (b) {
        PoolBlock *next = b->next;
        free(b);
        b = next;
    }
    free(p);
}

size_t pool_used(const Pool *p) { return p->total_used; }
```

**測試**：

```c
int main(void) {
    Pool *p = pool_create();
    char *s1 = pool_alloc(p, 100);
    char *s2 = pool_alloc(p, 200);
    printf("used: %zu\n", pool_used(p));   // 304（100 + 8 pad + 200 + 8 pad）

    pool_reset(p);
    printf("used after reset: %zu\n", pool_used(p));   // 0

    char *s3 = pool_alloc(p, 50);   // 重用已有的 block，不 malloc
    pool_destroy(p);
    return 0;
}
```

</details>

---

## 題目 2：執行緒安全的引用計數物件

**需求**：實作一個引用計數包裝，讓任意物件可以安全地在多執行緒環境下共享和銷毀。

**API**：

```c
typedef struct RefObj RefObj;

RefObj *ref_create(void *data, void (*destroy)(void *));  // 建立，ref=1
RefObj *ref_inc(RefObj *obj);                              // 增加引用，回傳 obj
void    ref_dec(RefObj *obj);                              // 減少引用，到 0 時 destroy
void   *ref_get(const RefObj *obj);                        // 取得 data 指標
```

<details>
<summary>參考解答</summary>

```c
#include <stdlib.h>
#include <stdatomic.h>

struct RefObj {
    void           *data;
    void          (*destroy)(void *);
    atomic_int      ref_count;
};

RefObj *ref_create(void *data, void (*destroy)(void *)) {
    RefObj *obj = malloc(sizeof(RefObj));
    if (!obj) return NULL;
    obj->data    = data;
    obj->destroy = destroy;
    atomic_init(&obj->ref_count, 1);
    return obj;
}

RefObj *ref_inc(RefObj *obj) {
    if (!obj) return NULL;
    atomic_fetch_add_explicit(&obj->ref_count, 1, memory_order_relaxed);
    return obj;
}

void ref_dec(RefObj *obj) {
    if (!obj) return;
    // memory_order_release 確保 destroy 能看到所有之前的寫入
    if (atomic_fetch_sub_explicit(&obj->ref_count, 1, memory_order_release) == 1) {
        // memory_order_acquire 確保讀取其他執行緒的寫入
        atomic_thread_fence(memory_order_acquire);
        if (obj->destroy) obj->destroy(obj->data);
        free(obj);
    }
}

void *ref_get(const RefObj *obj) { return obj ? obj->data : NULL; }
```

**測試**：

```c
void my_free(void *p) { printf("destroyed!\n"); free(p); }

int main(void) {
    char *data = strdup("hello");
    RefObj *obj = ref_create(data, my_free);
    RefObj *obj2 = ref_inc(obj);     // ref = 2
    ref_dec(obj);                     // ref = 1，不 destroy
    ref_dec(obj2);                    // ref = 0，destroyed!
    return 0;
}
```

</details>

---

## 題目 3：固定大小的 Lock-Free MPMC 佇列

**需求**：多 producer 多 consumer 的 ring buffer，不用 mutex。

**限制**：容量固定（N，必須是 2 的冪次），元素是 `int`，支援 try_push/try_pop（不阻塞）。

<details>
<summary>參考解答</summary>

```c
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>

#define RING_CAPACITY 1024   // 必須是 2 的冪次

typedef struct {
    int            data;
    atomic_size_t  sequence;   // 每個 slot 的生命周期計數器
} Slot;

typedef struct {
    Slot           slots[RING_CAPACITY];
    atomic_size_t  head;   // 下一個 push 的位置
    atomic_size_t  tail;   // 下一個 pop 的位置
} MPMCRing;

void ring_init(MPMCRing *r) {
    atomic_init(&r->head, 0);
    atomic_init(&r->tail, 0);
    for (size_t i = 0; i < RING_CAPACITY; i++)
        atomic_init(&r->slots[i].sequence, i);
}

bool ring_try_push(MPMCRing *r, int val) {
    size_t pos = atomic_load_explicit(&r->head, memory_order_relaxed);
    for (;;) {
        Slot   *slot = &r->slots[pos & (RING_CAPACITY - 1)];
        size_t  seq  = atomic_load_explicit(&slot->sequence, memory_order_acquire);
        intptr_t diff = (intptr_t)seq - (intptr_t)pos;
        if (diff == 0) {
            // 這個 slot 可以寫入
            if (atomic_compare_exchange_weak_explicit(&r->head, &pos, pos + 1,
                    memory_order_relaxed, memory_order_relaxed)) {
                slot->data = val;
                atomic_store_explicit(&slot->sequence, pos + 1, memory_order_release);
                return true;
            }
        } else if (diff < 0) {
            return false;   // full
        } else {
            pos = atomic_load_explicit(&r->head, memory_order_relaxed);
        }
    }
}

bool ring_try_pop(MPMCRing *r, int *val) {
    size_t pos = atomic_load_explicit(&r->tail, memory_order_relaxed);
    for (;;) {
        Slot   *slot = &r->slots[pos & (RING_CAPACITY - 1)];
        size_t  seq  = atomic_load_explicit(&slot->sequence, memory_order_acquire);
        intptr_t diff = (intptr_t)seq - (intptr_t)(pos + 1);
        if (diff == 0) {
            if (atomic_compare_exchange_weak_explicit(&r->tail, &pos, pos + 1,
                    memory_order_relaxed, memory_order_relaxed)) {
                *val = slot->data;
                atomic_store_explicit(&slot->sequence, pos + RING_CAPACITY,
                                       memory_order_release);
                return true;
            }
        } else if (diff < 0) {
            return false;   // empty
        } else {
            pos = atomic_load_explicit(&r->tail, memory_order_relaxed);
        }
    }
}
```

這個設計來自 Dmitry Vyukov 的 MPMC queue，是生產環境常用的 lock-free 佇列實作。

</details>

---

## 自我檢核

- [ ] 題目 1：能解釋為什麼 `pool_reset` 不 free block（避免 malloc overhead）
- [ ] 題目 2：能解釋 ref_dec 為什麼用 `memory_order_release` 遞減再 `memory_order_acquire` fence
- [ ] 題目 3：能說出 Slot 裡的 `sequence` 欄位的作用（區分 empty/full/in-progress）

→ [練習 C：模擬面試 30 題](./practice-c-mock-interview.md)
