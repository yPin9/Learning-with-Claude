# Ch 30 — C 語言系統設計題

> 目標：面對「用 C 設計一個 X」類型的系統設計題，知道要考慮哪些維度，以及常見設計的參考實作。

## 系統設計題的答題框架

面試官問「用 C 設計一個 memory allocator」或「設計一個 thread pool」時，先問清楚：

1. **規模**：多少 CPU 核？記憶體多大？QPS 多高？
2. **限制**：可以用 OS API 嗎？允許動態記憶體嗎？
3. **正確性 vs 效能**：先要正確，再談優化

然後分層說明：

1. **基本設計**（核心資料結構 + 主要 API）
2. **邊界條件**（錯誤處理、NULL、empty、full）
3. **並行安全**（mutex、lock-free、per-thread）
4. **效能優化**（cache 友善、批量操作）

---

## 設計題一：Thread Pool

```
需求：接收任務，分配給 N 個 worker 執行緒，main 執行緒不阻塞。
```

### 設計

```c
typedef void (*TaskFn)(void *arg);

typedef struct Task {
    TaskFn  fn;
    void   *arg;
} Task;

#define QUEUE_SIZE 1024

typedef struct {
    pthread_t  *workers;      // worker 執行緒陣列
    int         n_workers;

    Task        queue[QUEUE_SIZE]; // ring buffer task queue
    int         head, tail;
    int         count;

    pthread_mutex_t lock;
    pthread_cond_t  not_empty;  // 有任務可執行
    pthread_cond_t  not_full;   // 有空間可投遞

    int         shutdown;        // 設為 1 表示停止
} ThreadPool;
```

### Worker 執行緒

```c
static void *worker_fn(void *arg) {
    ThreadPool *pool = (ThreadPool *)arg;
    while (1) {
        pthread_mutex_lock(&pool->lock);
        while (pool->count == 0 && !pool->shutdown)
            pthread_cond_wait(&pool->not_empty, &pool->lock);

        if (pool->shutdown && pool->count == 0) {
            pthread_mutex_unlock(&pool->lock);
            break;
        }

        Task t        = pool->queue[pool->head];
        pool->head    = (pool->head + 1) % QUEUE_SIZE;
        pool->count--;
        pthread_cond_signal(&pool->not_full);
        pthread_mutex_unlock(&pool->lock);

        t.fn(t.arg);   // 執行任務（不在 lock 裡）
    }
    return NULL;
}
```

### 投遞任務

```c
int pool_submit(ThreadPool *pool, TaskFn fn, void *arg) {
    pthread_mutex_lock(&pool->lock);
    while (pool->count == QUEUE_SIZE)
        pthread_cond_wait(&pool->not_full, &pool->lock);
    if (pool->shutdown) { pthread_mutex_unlock(&pool->lock); return -1; }

    pool->queue[pool->tail] = (Task){ fn, arg };
    pool->tail = (pool->tail + 1) % QUEUE_SIZE;
    pool->count++;
    pthread_cond_signal(&pool->not_empty);
    pthread_mutex_unlock(&pool->lock);
    return 0;
}
```

### 討論點

- **Work stealing**：每個 worker 有自己的 deque，空時偷其他 worker 的任務（Go runtime 做法）
- **動態調整**：閒置執行緒太多時縮減，任務積壓時擴充
- **優先隊列**：把 `queue[QUEUE_SIZE]` 換成 min-heap

---

## 設計題二：Key-Value Store（In-Memory）

```
需求：支援 get/set/del，10M 以內的 key-value，key 是字串，value 是 blob。
```

### 設計

```c
#define HT_INIT_CAPACITY 16384   // 2 的冪次，方便 masking

typedef struct KVEntry {
    uint32_t      hash;        // 快速比較用
    char         *key;
    void         *value;
    size_t        value_len;
    struct KVEntry *next;      // 鏈式解決碰撞
} KVEntry;

typedef struct {
    KVEntry  **buckets;
    size_t     capacity;
    size_t     count;
    double     load_factor_limit;   // 通常 0.75
    pthread_rwlock_t rwlock;        // 讀多寫少：rwlock 比 mutex 高效
} KVStore;
```

### Rehash

```c
static void kvs_rehash(KVStore *kv) {
    size_t new_cap  = kv->capacity * 2;
    KVEntry **new_b = calloc(new_cap, sizeof(KVEntry *));
    for (size_t i = 0; i < kv->capacity; i++) {
        KVEntry *e = kv->buckets[i];
        while (e) {
            KVEntry *next = e->next;
            size_t   idx  = e->hash & (new_cap - 1);
            e->next       = new_b[idx];
            new_b[idx]    = e;
            e             = next;
        }
    }
    free(kv->buckets);
    kv->buckets  = new_b;
    kv->capacity = new_cap;
}
```

### 討論點

- **Sharding**：把 hash space 分成 N 份，每份有獨立的 rwlock，減少競爭
- **Expiry / TTL**：每個 entry 加 `uint64_t expiry_ns`，get 時檢查，背景執行緒清理
- **LRU eviction**：double linked list + hash map（標準 LRU cache 設計）
- **Persistence**：AOF（Append-Only File）或 snapshot，參考 Redis 設計

---

## 設計題三：Lock-Free 日誌系統

```
需求：多執行緒安全，日誌寫入不阻塞呼叫執行緒，單個 writer 執行緒寫磁碟。
```

### 設計

```
Producer threads                    Writer thread
   log_write("msg") ─→  SPSC ring buffer (per producer)  ─→  merge & write to file
```

```c
// Per-thread 的 SPSC ring buffer（Ch 27 的設計），合并後批量寫磁碟
// 好處：
// 1. 沒有 lock（SPSC 用 atomic，詳見 Ch 27）
// 2. writer 一次 writev 多條 log（減少 syscall 次數）
// 3. 呼叫執行緒不等待 I/O
```

---

## 面試常問的 C 系統設計題

| 題目 | 核心考點 |
|------|---------|
| 實作 malloc/free | ptmalloc 原理、碎片化、thread cache |
| 設計 thread pool | condition variable、ring buffer、優雅 shutdown |
| 設計 LRU cache | hash map + doubly linked list |
| 設計 logger | lock-free ring buffer、batch I/O |
| 設計 circular buffer | SPSC vs MPMC、memory order |
| 設計 connection pool | semaphore、timeout、reconnect |
| 實作 reference counting | atomic、weak/strong ref、cycle 問題 |

---

## 答題節奏建議

1. **先說 API**（2 分鐘）：把函式簽名寫出來，確認和 interviewer 的期望一致
2. **再說資料結構**（3 分鐘）：struct 定義，解釋每個欄位
3. **寫核心 path**（10 分鐘）：最重要的 1–2 個函式完整寫出
4. **討論 edge case**（3 分鐘）：NULL、overflow、concurrent access
5. **討論優化**（2 分鐘）：cache、lock granularity、batching

---

## 自我檢核

- [ ] 能說出 thread pool 的核心資料結構（ring buffer + mutex + 兩個 condition variable）
- [ ] 知道 LRU cache 的標準實作（hash map + doubly linked list）
- [ ] 知道 rwlock 適合讀多寫少的場景
- [ ] 面對系統設計題，能先問清楚規模和限制再開始設計

---

Part 7 完成。進入練習 B 和 C 整合所有技能。

→ [練習 B：系統層實作](./practice-b-systems-impl.md)
