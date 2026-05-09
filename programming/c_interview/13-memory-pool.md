# Ch 13 — 自製 Memory Pool：Arena / Pool / Free-List

> 目標：親手實作三種 allocator，理解各自適用場景、效能特性，以及面試中「為什麼要自訂 allocator」的標準回答。

## 為什麼不直接用 malloc？

1. **速度**：malloc 有 lock（執行緒安全）、bin 查找、metadata 維護，overhead 大。自製 arena 可以做到 O(1) 且無 lock。
2. **碎片化**：malloc 長期運行後，heap 可能有大量小碎片，看起來有空間但拿不到連續大塊。
3. **確定性延遲**：嵌入式 / 遊戲引擎需要確定性 latency，malloc 有時因合并、mmap 出現長尾延遲。
4. **批量生命周期**：一批相關物件同時釋放，不需要追蹤每個指標（request arena）。

---

## 方案一：Linear / Arena Allocator

最簡單。維護 bump pointer，每次分配往後移，釋放是整個 arena reset。

```c
typedef struct {
    uint8_t *base;
    size_t   offset;
    size_t   capacity;
} Arena;

Arena arena_create(size_t capacity) {
    return (Arena){
        .base     = malloc(capacity),
        .offset   = 0,
        .capacity = capacity,
    };
}

void *arena_alloc(Arena *a, size_t size) {
    size = (size + 7) & ~(size_t)7;   // 對齊到 8 bytes
    if (a->offset + size > a->capacity) return NULL;
    void *p   = a->base + a->offset;
    a->offset += size;
    return p;
}

void arena_reset(Arena *a) {
    a->offset = 0;   // 全部「釋放」，但記憶體不還給 OS
}

void arena_destroy(Arena *a) {
    free(a->base);
    a->base = NULL;
}
```

**使用方式**：

```c
Arena request_arena = arena_create(64 * 1024);   // 每個 HTTP request 64 KB
void *buf  = arena_alloc(&request_arena, 1024);
void *buf2 = arena_alloc(&request_arena, 256);
// ... 處理完 request ...
arena_reset(&request_arena);   // 全部清除，O(1)，不需要追蹤 buf/buf2
```

**效能**：alloc O(1)（只是加法 + 邊界檢查）。不能個別 free。

---

## 方案二：Pool Allocator（固定大小）

所有分配大小相同，用 free-list 管理空閒 block：

```c
typedef struct PoolNode { struct PoolNode *next; } PoolNode;

typedef struct {
    void     *memory;
    PoolNode *free_list;
    size_t    block_size;
    size_t    capacity;
} Pool;

Pool pool_create(size_t block_size, size_t count) {
    if (block_size < sizeof(PoolNode))
        block_size = sizeof(PoolNode);   // block 至少能放一個指標

    Pool p = {
        .block_size = block_size,
        .capacity   = count,
        .memory     = malloc(block_size * count),
        .free_list  = NULL,
    };

    // 把所有 block 串成 free-list
    for (size_t i = count; i > 0; i--) {
        PoolNode *node = (PoolNode *)((char *)p.memory + (i-1) * block_size);
        node->next     = p.free_list;
        p.free_list    = node;
    }
    return p;
}

void *pool_alloc(Pool *p) {
    if (!p->free_list) return NULL;
    PoolNode *node = p->free_list;
    p->free_list   = node->next;
    return node;
}

void pool_free(Pool *p, void *ptr) {
    PoolNode *node = (PoolNode *)ptr;
    node->next     = p->free_list;
    p->free_list   = node;
}

void pool_destroy(Pool *p) {
    free(p->memory);
    p->memory = NULL;
}
```

**關鍵技巧**：free 的 block 直接在自身空間裡存 `next` 指標——空間複用，不需要額外的 metadata 陣列。

**使用方式**：

```c
typedef struct Packet { int id; char data[64]; } Packet;
Pool packet_pool = pool_create(sizeof(Packet), 1024);

Packet *pkt = pool_alloc(&packet_pool);
pkt->id     = 42;
// ... 使用 pkt ...
pool_free(&packet_pool, pkt);   // O(1)，不還給 OS
```

**適用場景**：網路封包、粒子系統、任何大量分配/釋放相同大小物件的場景。

---

## 方案三：Free-List Allocator（可變大小）

支援不同大小，first-fit 策略，比 malloc 簡單但有碎片問題：

```c
typedef struct Block {
    size_t        size;   // 含 header 的總大小
    int           free;
    struct Block *next;
} Block;

static char   heap_mem[65536];
static Block *heap_head = NULL;

void heap_init(void) {
    heap_head       = (Block *)heap_mem;
    heap_head->size = sizeof(heap_mem);
    heap_head->free = 1;
    heap_head->next = NULL;
}

void *my_malloc(size_t size) {
    size_t need = size + sizeof(Block);
    for (Block *cur = heap_head; cur; cur = cur->next) {
        if (!cur->free || cur->size < need) continue;

        // 若剩餘空間夠大，分割
        if (cur->size >= need + sizeof(Block) + 8) {
            Block *split  = (Block *)((char *)cur + need);
            split->size   = cur->size - need;
            split->free   = 1;
            split->next   = cur->next;
            cur->size     = need;
            cur->next     = split;
        }
        cur->free = 0;
        return (char *)cur + sizeof(Block);
    }
    return NULL;
}

void my_free(void *ptr) {
    if (!ptr) return;
    Block *b = (Block *)((char *)ptr - sizeof(Block));
    b->free  = 1;
    // 合并後繼 free block，避免碎片化
    while (b->next && b->next->free) {
        b->size += b->next->size;
        b->next  = b->next->next;
    }
}
```

---

## 三種 Allocator 比較

| | Arena | Pool | Free-List |
|-|-------|------|-----------|
| alloc 速度 | O(1) | O(1) | O(n) |
| free 速度 | O(1) reset | O(1) | O(1) |
| 支援大小 | 任意 | 固定 | 任意 |
| 碎片化 | 無（整塊 reset）| 無 | 有 |
| 可個別 free | ❌ | ✅ | ✅ |
| 典型場景 | web request、解析器 | 封包、粒子、物件池 | 通用替代 malloc |

---

## 面試常考

**Q：怎麼讓 arena allocator 支援多個 arena page？**
用鏈表串接多個 arena block：當一個 block 滿了，alloc 一個新的。這就是 Rust 的 `Bump` allocator 或 Go runtime 的 span 設計。

**Q：pool allocator 的 ABA problem 是什麼？**
多執行緒下：thread A pop 一個 node、thread B pop 同一個 node 後 push 回去，thread A 的 CAS 看到地址相同以為成功，但 node->next 已經變了。解法：tagged pointer（用高位 bits 存 version）。

---

## 自我檢核

- [ ] 能用 30 行實作 arena allocator（含對齊）
- [ ] 知道 pool allocator 怎麼把 next 指標嵌入 free block 本身（空間複用）
- [ ] 能解釋為什麼 free-list allocator 會有碎片，而 pool 不會
- [ ] 知道什麼場景用哪種 allocator

→ [Ch 14 Valgrind 與 AddressSanitizer 實戰](./14-asan-valgrind.md)
