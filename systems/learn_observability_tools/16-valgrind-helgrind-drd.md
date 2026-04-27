# Ch 16 — valgrind helgrind / drd

> 目標：用 helgrind / drd 抓 thread race condition、deadlock、lock 順序錯誤。理解 race 是什麼、怎麼形成、為什麼難找。

## race condition 是什麼

兩個 thread 同時 access 同一塊記憶體，**至少一個是 write，且沒同步機制**保證順序。結果可能依排程改變，造成不可預測 bug。

簡單例：

```c
int counter = 0;

void *worker(void *_) {
    for (int i = 0; i < 1000000; i++)
        counter++;
    return NULL;
}

int main() {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, worker, NULL);
    pthread_create(&t2, NULL, worker, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %d\n", counter);    // 預期 2000000，實際？
}
```

`counter++` 不是 atomic（read → modify → write）。兩 thread 交錯導致少數更新「丟失」。實際印出來常常是 ~1300000、每次不同。

## 為什麼 race 難抓

- **不一定每次發生**：2 個 core 跑時 race 機率高，1 個 core 幾乎沒
- **debug build 不重現**：加 printf 改 timing，race 消失
- **strace 沒幫助**：每 access 不發 syscall
- **gdb step 不重現**：step 改 timing

只能用**專門工具**。

## helgrind 用法

```bash
valgrind --tool=helgrind ./myprog
```

對上面 counter 程式：

```
==1234== Possible data race during write of size 4 at 0x404040 by thread #2
==1234== Locks held: none
==1234==    at 0x40123A: worker (race.c:6)
==1234==    by 0x...: start_thread
==1234== 
==1234== This conflicts with a previous read of size 4 by thread #1
==1234== Locks held: none
==1234==    at 0x401234: worker (race.c:6)
==1234==  Address 0x404040 is 0 bytes inside data symbol "counter"
```

兩段：

1. 「thread #2 在 race.c:6 寫，沒 lock」
2. 「跟 thread #1 在 race.c:6 的 read 衝突」

加上 `--read-var-info=yes` 顯示變數名（要 -g）：

```
==1234==  Address 0x404040 is 0 bytes inside data symbol "counter"
```

## helgrind vs drd

兩個都抓 race，演算法不同：

- **helgrind**：用 happens-before 跟 lockset analysis，準但慢
- **drd**：類似但更注重 false positive 少

實務上用 helgrind 為主，drd 當第二意見。

```bash
valgrind --tool=drd ./myprog
```

## 偵測：deadlock

```c
pthread_mutex_t a = PTHREAD_MUTEX_INITIALIZER;
pthread_mutex_t b = PTHREAD_MUTEX_INITIALIZER;

void *thread1(void *_) {
    pthread_mutex_lock(&a);
    sleep(1);
    pthread_mutex_lock(&b);     // 等 thread2 釋放 b
    pthread_mutex_unlock(&b);
    pthread_mutex_unlock(&a);
    return NULL;
}

void *thread2(void *_) {
    pthread_mutex_lock(&b);
    sleep(1);
    pthread_mutex_lock(&a);     // 等 thread1 釋放 a
    pthread_mutex_unlock(&a);
    pthread_mutex_unlock(&b);
    return NULL;
}
```

helgrind：

```
==1234== Thread #1: lock order "0x404040 before 0x404080" violated
==1234==    at 0x...: pthread_mutex_lock
==1234==    by 0x...: thread2 (deadlock.c:23)
```

helgrind 維護 lock acquisition 順序，發現「thread1: a→b」、「thread2: b→a」就警告。**還沒真正 deadlock 就警告**，不需要重現。

## 偵測：condition variable misuse

```c
pthread_cond_signal(&cv);    // 沒 hold mutex
```

helgrind 會警告。

## 偵測：destroy 還在用的 mutex

```c
pthread_mutex_lock(&m);
pthread_mutex_destroy(&m);    // ❌
pthread_mutex_unlock(&m);
```

抓到。

## 一個常見場景：「intermittent 失敗」

每 10 次 build & test 就 fail 一次的 test，多半 race。直接：

```bash
valgrind --tool=helgrind ./test
```

helgrind 不需要 race 真的觸發 — 它分析所有 access pattern，潛在 race 都報。

## 一個常見踩雷：custom synchronization

```c
volatile int ready = 0;

// thread 1
while (!ready);    // spin
use_data();

// thread 2
prepare_data();
ready = 1;
```

helgrind 看不出 `ready` 是同步機制，會報 race。

修：用 `pthread_mutex` / `atomic` / 給 helgrind 標註：

```c
#include <valgrind/helgrind.h>

ANNOTATE_HAPPENS_BEFORE(&ready);
ready = 1;
// ...
ANNOTATE_HAPPENS_AFTER(&ready);
```

或乾脆不要寫 custom sync，用 atomic / mutex。

## 一個常見踩雷：跨 process shared memory

```c
shm_open("/myshm", ...);
mmap(...);
// 兩個 process 同 access
```

helgrind 看不到跨 process。它只 trace 一個 process 的 thread。

修：每個 process 各 valgrind 一份；或乾脆**不要跨 process shared memory**，用 message passing。

## 一個常見踩雷：lib 裡的 race

```
==1234== Possible data race ...
==1234==    at 0x...: __nss_database_lookup
```

glibc 內部 race，多半已知不修。寫 suppression 跳過。

## helgrind 的限制

- **比 valgrind 還慢**：原本 10x → helgrind 30-100x
- **false positive 多**：custom sync、lock-free algorithm 都會誤報
- **不抓 lock-free 的真 race**：原子操作之間的 logical race
- **有些 pattern 抓不到**：`pthread_once` 等

對於現代 multithread code，**TSan 比 helgrind 強很多**。Ch 18 詳細。

## 動手練習

**1. counter race 重現**

寫上面那個 counter 程式，跑很多次看結果不一致。`valgrind --tool=helgrind` 確認 race。

修法：

```c
__atomic_fetch_add(&counter, 1, __ATOMIC_RELAXED);
```

或：

```c
pthread_mutex_lock(&m);
counter++;
pthread_mutex_unlock(&m);
```

再跑 helgrind 應該乾淨。

**2. deadlock**

寫上面 lock order 倒過來的兩個 thread，跑 `helgrind`。**不用真的卡死** helgrind 也會報。

**3. signal in cv handling**

```c
pthread_cond_signal(&cv);    // 不 lock
```

跑 helgrind 看訊息。

**4. lock-free 結構**

寫個簡單 lock-free queue（CAS）。跑 helgrind 看一堆 false positive。對比 TSan（Ch 18）。

**5. 加 ANNOTATE 抑制 false positive**

延伸 `ready` 變數那個例子，加 ANNOTATE_HAPPENS_BEFORE / AFTER 看 helgrind 是不是停止抱怨。

## 自我檢核

- [ ] 講得出 race condition 的條件
- [ ] 知道 helgrind 跟 drd 演算法略不同
- [ ] 用 helgrind 抓過 counter race / deadlock / cv misuse
- [ ] 知道 helgrind 對 custom sync 會誤報
- [ ] 知道為什麼跨 process shared memory helgrind 看不到
- [ ] 知道 modern thread 用 TSan 更好

下一章看 valgrind 的 profiling tools — callgrind / massif / cachegrind。

→ [Ch 17 valgrind callgrind / massif / cachegrind](./17-valgrind-profiling.md)
