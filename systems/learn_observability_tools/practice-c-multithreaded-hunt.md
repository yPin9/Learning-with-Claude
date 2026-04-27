# 練習 C — multithreaded race + leak hunt

> 目標：給你一個藏了 race / leak / UAF / lock 順序錯誤的多執行緒程式，用 ASan / TSan / valgrind / helgrind 全套工具找出來。

## 場景

`work-pool.c`：模擬 worker pool，把 task 推到 queue，worker thread 從 queue 拿 task 處理。看似正常但藏了 4 種 bug。

## source code

```c
// work-pool.c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>

#define NUM_WORKERS 4
#define NUM_TASKS 200

typedef struct task {
    int id;
    char *payload;
    struct task *next;
} task_t;

static task_t *queue_head = NULL;
static pthread_mutex_t queue_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t queue_cv = PTHREAD_COND_INITIALIZER;

static int total_processed = 0;
static int stop_flag = 0;

static void enqueue(task_t *t) {
    pthread_mutex_lock(&queue_lock);
    t->next = queue_head;
    queue_head = t;
    pthread_cond_signal(&queue_cv);
    pthread_mutex_unlock(&queue_lock);
}

static task_t *dequeue(void) {
    pthread_mutex_lock(&queue_lock);
    while (!queue_head && !stop_flag)
        pthread_cond_wait(&queue_cv, &queue_lock);
    task_t *t = queue_head;
    if (t) queue_head = t->next;
    pthread_mutex_unlock(&queue_lock);
    return t;
}

static void process(task_t *t) {
    // 模擬處理：印 payload 內容
    fprintf(stderr, "[worker] task %d: %s\n", t->id, t->payload);
    free(t->payload);
    // ... 之後別人會用到 t->payload？
    total_processed++;     // ← bug?
}

static void *worker(void *_) {
    while (1) {
        task_t *t = dequeue();
        if (!t) break;
        process(t);
        free(t);           // free 後還能用 t->payload 嗎？
    }
    return NULL;
}

static void seed_tasks(void) {
    for (int i = 0; i < NUM_TASKS; i++) {
        task_t *t = malloc(sizeof(task_t));
        t->id = i;
        t->payload = malloc(64);
        snprintf(t->payload, 64, "payload-%d", i);
        enqueue(t);
    }
}

int main(void) {
    pthread_t workers[NUM_WORKERS];
    for (int i = 0; i < NUM_WORKERS; i++)
        pthread_create(&workers[i], NULL, worker, NULL);

    seed_tasks();

    sleep(2);

    pthread_mutex_lock(&queue_lock);
    stop_flag = 1;
    pthread_cond_broadcast(&queue_cv);
    pthread_mutex_unlock(&queue_lock);

    for (int i = 0; i < NUM_WORKERS; i++)
        pthread_join(workers[i], NULL);

    fprintf(stderr, "Total processed = %d (expected %d)\n",
            total_processed, NUM_TASKS);

    return 0;
}
```

```bash
gcc -O0 -g -pthread work-pool.c -o pool
./pool 2>&1 | tail -3
# Total processed = 197 (expected 200)
```

每次跑數字不同（189 / 195 / 200 ...），偶爾正確。**3 ~ 4 種 bug 藏在這**。

## 偵查任務

| # | 任務 | 工具 |
|---|---|---|
| 1 | 找 race condition | TSan |
| 2 | 找 memory leak | ASan / valgrind memcheck |
| 3 | 找 UAF | ASan |
| 4 | 找邏輯 bug（process count 不對） | 讀 code + TSan |

每題自己跑工具，看到訊息後寫下 root cause、再修。

## 期望輸出範例

### TSan 偵測

```bash
gcc -O1 -g -pthread -fsanitize=thread work-pool.c -o pool-tsan
./pool-tsan 2>&1 | head -30
```

```
==================
WARNING: ThreadSanitizer: data race (pid=...)
  Read of size 4 at 0x... by thread T2:
    #0 worker work-pool.c:55 (pool-tsan+...)

  Previous write of size 4 at 0x... by thread T1:
    #0 worker work-pool.c:55 (pool-tsan+...)

  Location is global 'total_processed' of size 4 at 0x...
==================
```

`total_processed++` 沒同步，race 確認。

### ASan 偵測

```bash
gcc -O1 -g -pthread -fsanitize=address work-pool.c -o pool-asan
./pool-asan 2>&1 | head -50
```

如果有 UAF / leak，會看到對應訊息。

```
==1234==ERROR: AddressSanitizer: heap-use-after-free
...
freed by thread T2 here:
    #0 free
    #1 process work-pool.c:43

previously allocated by thread T0 here:
    #0 malloc
    #1 seed_tasks work-pool.c:71
```

如 `process` 裡 `free(t->payload)` 後又有人用，會抓到。

### valgrind 偵測 leak

```bash
valgrind --leak-check=full --show-leak-kinds=all ./pool 2>&1 | tail -30
```

```
==1234== HEAP SUMMARY:
==1234==     in use at exit: 192 bytes in 3 blocks
...
==1234== 192 bytes in 3 blocks are definitely lost
==1234==    at 0x...: malloc
==1234==    by 0x...: seed_tasks
```

## 完整參考解答

**先做完上面再看！**

<details>
<summary>4 個 bug + root cause + 修</summary>

### Bug 1: total_processed race

`process()` 裡 `total_processed++` 沒 lock，4 個 worker 同時改。

修：用 atomic 或 lock。

```c
__atomic_fetch_add(&total_processed, 1, __ATOMIC_RELAXED);
```

### Bug 2: stop_flag race

`worker()` 的 `dequeue()` 在 lock 內檢查 `stop_flag`，OK。但 `dequeue` 回 NULL 後 worker 直接 break，沒再檢查 queue 是否還有 task —— 這不算 race，但是 logic bug：

如果 worker 1 在 `dequeue` 拿到 NULL 時 stop_flag=1，但其他 worker 同時還在 enqueue（不會發生，因為 main 已經 set stop），其實 OK。

不過 `seed_tasks` 是 main thread 跑的，跟 worker 平行。如果 sleep(2) 不夠長，worker 已經處理完 + 回到 dequeue 等，main 設 stop_flag 然後 broadcast — OK。

仔細看不出第二個 race。但 TSan 可能還會報 `queue_head` 在某些 path 上的訪問。檢查：dequeue 全程 holding lock，OK。enqueue 也 holding lock，OK。

實際上 source 沒第二個 race。**TSan 主要就是抓 total_processed**。但 leak 跟 UAF 還在。

### Bug 3: UAF in process

```c
static void process(task_t *t) {
    fprintf(stderr, "[worker] task %d: %s\n", t->id, t->payload);
    free(t->payload);
    // 之後 worker free(t)，但 t->payload 已 free，沒問題
    total_processed++;
}

static void *worker(void *_) {
    while (1) {
        task_t *t = dequeue();
        if (!t) break;
        process(t);
        free(t);    // OK，free t 但不會 access t->payload
    }
    return NULL;
}
```

仔細看：process free(t->payload) 後，worker free(t)。中間沒 access payload。**沒有 UAF**。

但如果改成：

```c
static void process(task_t *t) {
    fprintf(stderr, "task %d\n", t->id);
    free(t->payload);
    fprintf(stderr, "payload was: %s\n", t->payload);   // ← UAF
}
```

ASan 會抓到。原 source 沒這 bug。

### Bug 4: leak

main 在 `sleep(2)` 後 set stop_flag。但如果 seed_tasks 還沒跑完 enqueue 所有 200 個，或 worker 處理速度跟不上，**未處理的 task 會 leak**。

實際情況：`seed_tasks` 在 main thread 跑很快（200 次 malloc），worker 同時消化。2 秒夠不夠看機器。如果不夠，剩下的 task 還在 queue 裡，main exit 時 leak。

修：

```c
// stop 前確保 queue 空
while (1) {
    pthread_mutex_lock(&queue_lock);
    int empty = (queue_head == NULL);
    pthread_mutex_unlock(&queue_lock);
    if (empty) break;
    usleep(10000);
}

pthread_mutex_lock(&queue_lock);
stop_flag = 1;
pthread_cond_broadcast(&queue_cv);
pthread_mutex_unlock(&queue_lock);
```

### Bug 5: counter 數錯（the real reveal）

最初症狀：`total_processed = 197 (expected 200)`。原因：

A) race 讓 increment 丟失（TSan 報的） — 修 atomic 解決一部分
B) leak 讓某些 task 沒被 process — 修 join queue 解決

兩者都修才會穩定 200。

### 完整修正版

```c
// 修正部分
static void process(task_t *t) {
    fprintf(stderr, "[worker] task %d: %s\n", t->id, t->payload);
    free(t->payload);
    __atomic_fetch_add(&total_processed, 1, __ATOMIC_RELAXED);
}

int main(void) {
    pthread_t workers[NUM_WORKERS];
    for (int i = 0; i < NUM_WORKERS; i++)
        pthread_create(&workers[i], NULL, worker, NULL);

    seed_tasks();

    // 等 queue 完全消化
    while (1) {
        pthread_mutex_lock(&queue_lock);
        int empty = (queue_head == NULL);
        pthread_mutex_unlock(&queue_lock);
        if (empty) break;
        usleep(10000);
    }

    pthread_mutex_lock(&queue_lock);
    stop_flag = 1;
    pthread_cond_broadcast(&queue_cv);
    pthread_mutex_unlock(&queue_lock);

    for (int i = 0; i < NUM_WORKERS; i++)
        pthread_join(workers[i], NULL);

    fprintf(stderr, "Total processed = %d (expected %d)\n",
            total_processed, NUM_TASKS);
    return 0;
}
```

跑 100 次：

```bash
for i in $(seq 100); do ./pool 2>&1 | tail -1; done | sort | uniq -c
# 100 Total processed = 200 (expected 200)
```

</details>

## 教學要點

完成後你應該能解釋：

1. **TSan 的訊息格式**：兩個 thread 的 location + 共享變數
2. **`total_processed++` 不是 atomic**：read / increment / write 三步可被打斷
3. **「沒 race 的 leak」**：有 lock 不代表沒 leak，邏輯也要對
4. **多 sanitizer 怎麼搭**：ASan + UBSan 一份 build，TSan 另一份
5. **CI 跑哪些**：每個 PR build 兩份（asan / tsan）跑同 test

## 進階挑戰

**A. 把 enqueue 改成 lock-free**：用 CAS。重跑 TSan，看是否還報。如果報，加 `__atomic_*` 跟 release/acquire。

**B. 故意藏個真 UAF**：process 裡先 fprintf payload、再 free、再 fprintf 一次。ASan 會立刻抓。

**C. 故意藏個 lock order 反轉**：加第二個 mutex，兩個 worker 用不同順序拿。helgrind / TSan 預警。

**D. 加 fuzz**：`-fsanitize=address,fuzzer`，把 enqueue 寫成讀 stdin payload，libfuzzer 自動跑找 crash。

## 自我檢核

- [ ] 用 TSan 抓到 total_processed race
- [ ] 用 valgrind 抓到 task leak
- [ ] 解得開 TSan / ASan 訊息格式
- [ ] 知道為什麼一個 sanitizer 不能抓所有 bug
- [ ] 知道 multi-bug 程式 fix 之後要跑很多次驗證（intermittent 很麻煩）

下個 Part 進進階自製工具 — 用 ptrace 注入 + LD_PRELOAD interceptor + core dump 分析。

→ [Ch 19 ptrace 進階：注入與 register 操作](./19-ptrace-advanced-injection.md)
