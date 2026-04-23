# Ch21: 並行（thread / jthread / atomic）

C++11 加了標準並行庫，C++20 加了 `jthread`、semaphore、latch。本章目標：會用基本工具，**不深入 memory model 細節**。

## 21.1 `std::thread` (C++11)

```cpp
#include <thread>
#include <iostream>

void work(int id) {
    std::cout << "thread " << id << '\n';
}

int main() {
    std::thread t1{work, 1};
    std::thread t2{[]{ std::cout << "lambda\n"; }};

    t1.join();   // 等 t1 結束
    t2.join();
}
```

### 坑：忘了 join 或 detach
```cpp
{
    std::thread t{work, 1};
}   // ⚠️ 解構時如果既沒 join 也沒 detach → std::terminate()
```

常漏寫，所以 C++20 有更好的選擇。

## 21.2 `std::jthread` (C++20)

**自動 join 的 thread**：

```cpp
#include <thread>

{
    std::jthread t{work, 1};
    // 離開 scope 自動 join
}
```

還支援**取消**：
```cpp
std::jthread t{[](std::stop_token st) {
    while (!st.stop_requested()) {
        do_work();
    }
}};

t.request_stop();   // 請求停止
// 解構時自動 request_stop + join
```

**預設用 `jthread`**，不用 `thread`。

## 21.3 `std::mutex` + `std::lock_guard`

```cpp
#include <mutex>

std::mutex m;
int counter = 0;

void increment() {
    std::lock_guard lk{m};   // 建構：lock
    ++counter;
    // 解構：unlock
}
```

（CTAD：`std::lock_guard<std::mutex> lk{m}` 的模板參數可省。）

### `std::unique_lock`
更靈活的 lock wrapper：可以 unlock 再 lock、可以轉移、可以條件變數搭配。

```cpp
std::unique_lock<std::mutex> lk{m};
lk.unlock();
// ... 做不需要 lock 的事 ...
lk.lock();
```

大多時候 `lock_guard` 就夠。

### `std::scoped_lock` (C++17)
同時 lock 多個 mutex（避免 deadlock）：

```cpp
std::mutex m1, m2;

void transfer(Account& from, Account& to, int amount) {
    std::scoped_lock lk{from.m, to.m};   // 同時 lock 兩個
    // ...
}
```

## 21.4 `std::shared_mutex` (C++14/17)

讀寫鎖：
```cpp
#include <shared_mutex>

std::shared_mutex m;

// 多個讀者
void read() {
    std::shared_lock lk{m};
    // ...
}

// 獨佔寫者
void write() {
    std::unique_lock lk{m};
    // ...
}
```

## 21.5 `std::atomic<T>`

無鎖操作（對小型型別）：

```cpp
#include <atomic>

std::atomic<int> counter = 0;

void worker() {
    for (int i = 0; i < 1000; ++i) {
        ++counter;       // 原子操作
        // counter.fetch_add(1);   // 等效
    }
}
```

常用操作：
```cpp
counter.load();                 // 讀
counter.store(42);              // 寫
counter.exchange(10);           // 設新值、回舊值
counter.compare_exchange_strong(expected, desired);   // CAS
counter.fetch_add(1);
counter.fetch_sub(1);
```

### Memory order（進階）
```cpp
counter.fetch_add(1, std::memory_order_relaxed);  // 最弱、最快
counter.fetch_add(1, std::memory_order_seq_cst);  // 預設、最強
```

初學用預設（`seq_cst`）。深入 memory model 是獨立一個大主題。

### `std::atomic_flag` 和 spinlock
```cpp
std::atomic_flag lock = ATOMIC_FLAG_INIT;

void acquire() {
    while (lock.test_and_set(std::memory_order_acquire)) { }
}
void release() { lock.clear(std::memory_order_release); }
```

極少自己寫，但知道存在。

## 21.6 `std::condition_variable`

等某個條件：

```cpp
#include <condition_variable>
#include <queue>

std::queue<int> q;
std::mutex m;
std::condition_variable cv;

void producer() {
    {
        std::lock_guard lk{m};
        q.push(42);
    }
    cv.notify_one();
}

void consumer() {
    std::unique_lock lk{m};
    cv.wait(lk, [] { return !q.empty(); });   // 等到 queue 非空
    int x = q.front(); q.pop();
    // ...
}
```

`cv.wait(lk, pred)` 的意思：
1. 釋放 lock，睡覺
2. 被 notify 時，重拿 lock
3. 檢查 pred，不符合就重睡（防 spurious wakeup）

## 21.7 `std::async` / `std::future`

Task-based 並行：

```cpp
#include <future>

std::future<int> f = std::async(std::launch::async, [] {
    return compute();
});

// 做別的事 ...

int result = f.get();   // 等結果（如果還沒完成）
```

策略：
- `std::launch::async`：保證新 thread
- `std::launch::deferred`：只在 `.get()` 時執行（lazy，同步）
- 預設：編譯器選（可能 deferred，不建議）

**明確用 `std::launch::async`**，或直接 jthread。

### `std::promise` / `std::future`（低階）
```cpp
std::promise<int> p;
std::future<int> f = p.get_future();

std::thread t{[&p] { p.set_value(42); }};

int x = f.get();
t.join();
```

自己控制何時 `set_value`，很像 promise-callback pattern。

## 21.8 C++20 新增工具

### `std::latch`（一次性 barrier）
```cpp
#include <latch>

std::latch ready{3};   // 等 3 個 count_down

// Worker threads
std::jthread t1{[&]{ prepare(); ready.count_down(); }};
std::jthread t2{[&]{ prepare(); ready.count_down(); }};
std::jthread t3{[&]{ prepare(); ready.count_down(); }};

ready.wait();   // 等到 count == 0
```

### `std::barrier`（可重複 barrier）
```cpp
#include <barrier>

std::barrier bar{3};

void phase() {
    bar.arrive_and_wait();   // 等 3 個都到
}
```

### `std::counting_semaphore`
```cpp
#include <semaphore>

std::counting_semaphore<10> sem{3};   // 最多 3 併發

sem.acquire();    // 等一個 slot
// 做事
sem.release();
```

## 21.9 實戰：Thread-safe queue

```cpp
template <typename T>
class BlockingQueue {
    std::queue<T> q_;
    std::mutex m_;
    std::condition_variable cv_;
public:
    void push(T value) {
        {
            std::lock_guard lk{m_};
            q_.push(std::move(value));
        }
        cv_.notify_one();
    }

    T pop() {
        std::unique_lock lk{m_};
        cv_.wait(lk, [&]{ return !q_.empty(); });
        T v = std::move(q_.front());
        q_.pop();
        return v;
    }
};
```

## 21.10 常見錯誤

### 錯誤 1：Data race（最基本）
```cpp
int x = 0;
std::thread t1{[&]{ ++x; }};
std::thread t2{[&]{ ++x; }};    // ❌ UB：兩個 thread 同時改
```
加 mutex 或用 `std::atomic<int>`。

### 錯誤 2：Deadlock
```cpp
std::mutex m1, m2;

void a() { std::lock_guard l1{m1}; std::lock_guard l2{m2}; }
void b() { std::lock_guard l1{m2}; std::lock_guard l2{m1}; }
// ❌ a 拿 m1 等 m2，b 拿 m2 等 m1
```
解法：一致順序，或 `std::scoped_lock{m1, m2}`。

### 錯誤 3：Lock 範圍過大
```cpp
void f() {
    std::lock_guard lk{m};
    slow_io();         // 整個 IO 期間都 lock
}
```
盡量把 lock 縮到最短。

### 錯誤 4：在 lock 內呼叫 callback
```cpp
std::lock_guard lk{m};
callback_();   // ⚠️ callback 可能 lock 別的，易 deadlock
```
盡量在解鎖後呼叫 callback。

### 錯誤 5：忘了 notify
```cpp
// producer 沒 notify → consumer 永遠 wait
```

### 錯誤 6：捕獲 local 變數的 thread 跨 scope
```cpp
std::thread start_work() {
    int data = load();
    return std::thread{[&data]{ use(data); }};   // ❌ data 離開 scope
}
```
capture by value、或 `std::move`。

## 21.11 何時需要並行

- **I/O bound**：thread / async / coroutine
- **CPU bound with independent work**：thread pool 或並行 algorithm
- **GUI / 回應性**：worker thread 做重活
- **網路 server**：thread-per-connection 或 event loop + 多 worker

**先 profile 再上並行**——並行引入 bug 的成本很高。

## 21.12 並行算法（C++17）

```cpp
#include <execution>

std::sort(std::execution::par, v.begin(), v.end());
std::for_each(std::execution::par, v.begin(), v.end(), f);
```

gcc 需連結 `-ltbb`。

## 21.13 練習

1. 寫一個 thread pool：固定 N workers，可以 `submit(function)` 入隊，workers 消費。
2. 用 `std::atomic<int>` 實作一個 counter，多 thread 併發 increment，確認最終值正確。

## 本章重點
- **預設用 `std::jthread`** 而不是 `std::thread`
- Lock 用 `std::lock_guard`，多 mutex 用 `std::scoped_lock`
- 讀寫分離用 `std::shared_mutex`
- `std::atomic<T>` 對小型值省 lock
- Condition variable 配合 mutex 等條件
- `std::async(std::launch::async, ...)` 起 task
- C++20 新增 `latch`、`barrier`、`semaphore`
- 並行 code 要特別小心 data race、deadlock；先用 sanitizers（`-fsanitize=thread`）測
