# Ch 26 — mutex、semaphore、completion

> **目標**：理解「睡眠鎖」與「忙等鎖」的根本分野——臨界區裡能不能睡；讀懂 mutex 的 owner 追蹤與樂觀自旋（optimistic spinning）為何是混合策略；分清 semaphore、completion、rw_semaphore 各自解決什麼問題；最後靠一張選擇表在真實場景裡下對決定。

## 為什麼需要這個？

Ch 25 的 spinlock 拿不到鎖時**原地空轉**（busy-wait）：一個迴圈裡反覆讀鎖的狀態，CPU 全速燒著等。這在臨界區極短（幾十個 cycle）時是對的——與其付一次 context switch（動輒上千 cycle）的代價去睡再被叫醒，不如直接轉幾圈等對方放手。

但這個算盤只在兩個前提下成立：

1. **臨界區很短**。如果持鎖者要在臨界區裡跑幾萬個 cycle，等待者就白燒了幾萬個 cycle 的 CPU，這些週期本可以拿去跑別的 task。
2. **持鎖者不會睡**。spinlock 的等待者拿著 CPU 不放，如果它還關了搶佔（preemption），那麼在**同一顆 CPU 上**持鎖者根本沒機會被排程回來放鎖——直接死鎖。所以 spinlock 臨界區裡**絕對不能睡**：不能 `kmalloc(GFP_KERNEL)`（可能觸發 reclaim 而睡，見 Ch 6/Ch 22）、不能 `copy_to_user`（可能 page fault 而睡，見 Ch 19）、不能 `msleep`、不能等 I/O。

真實世界裡很多臨界區違反這兩點。你在臨界區裡要配一大塊記憶體、要等一次磁碟讀完、要跟另一個 thread 握手——這些操作**本質上會睡**。這時候忙等鎖不只是浪費，是直接錯誤。

解法是**睡眠鎖（sleeping lock）**：拿不到鎖時，不空轉，而是把自己這個 task 設成 `TASK_UNINTERRUPTIBLE`（Ch 9 的狀態）掛到鎖的等待佇列上、然後 `schedule()` 讓出 CPU（Ch 11）。CPU 去跑別的 task，一點都不浪費。等持鎖者釋放時，它負責把等待佇列上的人喚醒（`wake_up`，Ch 11）。

代價是那一次 context switch。所以睡眠鎖與忙等鎖不是誰取代誰，而是**依臨界區的長短與能否睡來分工**。這章講睡眠鎖這一側：mutex、semaphore、completion、rw_semaphore。

> 一個貫穿本章的硬約束（接 Ch 2）：睡眠鎖**只能在 process context 用**。中斷 context（硬中斷、softirq）沒有一個「可以被睡掉再喚醒」的 task 身分，`schedule()` 在那裡是災難。所以中斷 handler 裡要同步只能用 spinlock。「這段程式碼會不會在中斷 context 跑」是你選鎖的第一個問題。

## 先建立直覺

先把兩類鎖的行為並排看清楚。假設 CPU 0 上的 task A 持鎖，CPU 1 上的 task B 想拿：

```
   忙等鎖（spinlock）                    睡眠鎖（mutex）
   ─────────────────                    ─────────────────
   B: while (locked)                    B: 拿不到
        cpu_relax();   ← CPU 1 全速空轉      set TASK_UNINTERRUPTIBLE
        cpu_relax();      燒週期              加入 mutex 的 wait_list
        cpu_relax();                          schedule()  ─────┐
        ...                                                    │ CPU 1 去跑別的 task
   A: unlock()      ← B 立刻搶到          A: unlock()           │  （B 不佔 CPU）
   B: 進入臨界區                            └→ 喚醒 wait_list 首位
                                          B: 被排程回來，進入臨界區
```

忙等鎖用 CPU 週期換「零 context switch 延遲」；睡眠鎖用一次 context switch 換「等待期間 CPU 可做別的事」。臨界區越長、等待者越多、越可能睡，天平越倒向睡眠鎖。

第二個直覺：**睡眠鎖不是非睡不可**。mutex 有個聰明的中間態叫「樂觀自旋」——如果它發現持鎖者**此刻正在別的 CPU 上跑**（很可能馬上就放手），那先 spin 一小下等它放，比立刻睡再被喚醒省一次 context switch。這是後面的重點。

## 睡眠鎖能睡，是因為它把「等」變成「讓出 CPU」

先釘死這章最核心的一句話：**spinlock 的等待者佔著 CPU 等，mutex 的等待者讓出 CPU 睡著等**。

差別的機制在拿不到鎖時走哪條路。spinlock 走 `cpu_relax()` 迴圈（Ch 25）。mutex 走的是（`kernel/locking/mutex.c` 的 `__mutex_lock_common()`，簡化）：

```
   1. 試著搶鎖（atomic cmpxchg 把 owner 設成自己）——搶到就結束
   2. 搶不到 → 樂觀自旋（見下節），持鎖者在跑就轉一下
   3. 自旋也拿不到 → 把自己包成 mutex_waiter 掛到 lock->wait_list
   4. 迴圈：
        set_current_state(TASK_UNINTERRUPTIBLE)   ← Ch 9 的狀態轉換
        if (搶到鎖) break
        schedule()                                ← Ch 11：讓出 CPU，睡著
      （被喚醒後回到迴圈頂再試）
   5. 拿到鎖，把自己從 wait_list 摘掉，set owner
```

第 4 步的 `schedule()` 就是「能睡」的物理意義：呼叫它的 task 把 CPU 讓給排程器，自己進入睡眠佇列。這件事**只有在 process context 才合法**——你得有個完整的 task 身分（`current` 指向真正的 `task_struct`，有自己的 kernel stack 可以掛起再恢復）。中斷 context 沒有這個身分，所以睡眠鎖在那裡用不了（Ch 2）。

為什麼是 `TASK_UNINTERRUPTIBLE` 而不是 `TASK_INTERRUPTIBLE`？因為 `mutex_lock()` 的語意是「我一定要拿到這把鎖才往下走」，一個訊號（signal）打斷它、讓它半路醒來回傳失敗，會讓呼叫端難以處理。想要「等鎖時可以被訊號打斷」的版本另有 `mutex_lock_interruptible()`，它用 `TASK_INTERRUPTIBLE`，被訊號叫醒時回傳 `-EINTR`——常用在使用者觸發、且使用者可能按 Ctrl-C 想中止的路徑上。

> 順帶一提：`ps` / `top` 看到的 **D 狀態（uninterruptible sleep）** 進程，很多就是卡在某個 `TASK_UNINTERRUPTIBLE` 的睡眠鎖或 I/O 等待上（Ch 9）——它連 `kill -9` 都殺不動，因為訊號打斷不了它。你看到一個進程長期 D 狀態，八成是某把鎖或某個 I/O 沒放/沒完成。這是本章機制在使用者空間留下的可觀察痕跡。

### 喚醒側：持鎖者放鎖時發生什麼

睡下去只是故事的一半，另一半是**誰、在什麼時候、怎麼把睡著的等待者叫醒**。答案是持鎖者的 `mutex_unlock()`（`kernel/locking/mutex.c` 的 `__mutex_unlock_slowpath()`）：

```
   持鎖者 A: mutex_unlock(lock)
     │
     ├─ fast path：一條 cmpxchg 把 owner 從自己清成 0
     │     若此刻 wait_list 是空的（沒人等）→ 到此結束，零喚醒成本
     │     若 wait_list 有人在等（owner 的 flag 位標記了）↓
     │
     └─ slow path：
           拿 wait_lock
           從 wait_list 取出首位 waiter B
           wake_up_process(B)          ← Ch 11：把 B 設回 TASK_RUNNING、放回 runqueue
           放 wait_lock
     ─────────────────────────────────────────────────
   排程器某個時點挑中 B → B 從當初的 schedule() 之後醒來
     → 回到 slow path 的 for 迴圈頂，__mutex_trylock() 這次成功 → 進臨界區
```

兩個要點。第一，**放鎖也分 fast/slow**：無人等待時放鎖只是一條 cmpxchg，完全不碰 wait_list，這是最常見的情況。只有真的有人在睡，才付出「拿 wait_lock、喚醒」的成本。第二，**喚醒不等於立刻執行**：`wake_up_process(B)` 只是把 B 放回 runqueue、標記可執行，B 何時真正跑起來由排程器（Ch 11）決定。所以「A 放鎖」到「B 進臨界區」之間隔著一次排程延遲——這正是睡眠鎖那「一次 context switch 成本」的來源，也是為什麼極短臨界區不划算用睡眠鎖。

## mutex：最常用的睡眠互斥鎖

mutex（`include/linux/mutex.h` 的 `struct mutex`、`kernel/locking/mutex.c`）是 kernel 裡最常用的睡眠鎖。核心欄位很少：

```c
struct mutex {
    atomic_long_t   owner;        // 持鎖者的 task_struct 指標（低幾位塞 flag）
    raw_spinlock_t  wait_lock;    // 保護 wait_list 的內部 spinlock
    struct list_head wait_list;   // 等待者佇列（掛 mutex_waiter）
    // CONFIG_MUTEX_SPIN_ON_OWNER 時還有 MCS 相關欄位
};
```

`owner` 是 mutex 相對 semaphore 最大的設計差異：**mutex 記得誰持有它**。這帶來幾個性質：

- **只有 owner 能 unlock**。mutex 是嚴格的「誰鎖誰解」——不能一個 thread 鎖、另一個 thread 解。這不是 API 限制那麼簡單，它讓 mutex 有明確的所有權語意，lockdep（Ch 28）與 debug 版本會檢查、抓出「解了不是自己鎖的鎖」這種 bug。
- **不能遞迴**。同一個 task 對同一把 mutex 連鎖兩次會死鎖（自己等自己放）。kernel 沒有可遞迴的 mutex，因為需要遞迴鎖通常代表你的鎖設計有問題。
- **不能在 owner 還持鎖時被銷毀**，`mutex_destroy()` 會檢查。

基本 API：

```c
DEFINE_MUTEX(my_lock);                 // 靜態定義並初始化
mutex_init(&some_mutex);               // 動態初始化

mutex_lock(&my_lock);                  // 拿鎖，拿不到就睡（不可被訊號打斷）
mutex_unlock(&my_lock);               // 放鎖，喚醒 wait_list 首位

int r = mutex_lock_interruptible(&my_lock);  // 睡時可被訊號打斷，回 0 或 -EINTR
if (mutex_trylock(&my_lock)) { ... }   // 試一下，拿不到立刻回 0，絕不睡
```

`mutex_trylock()` 值得單獨提：它**不睡**，拿不到就回 0。這讓你能在不確定能不能睡的環境、或想避免 lock ordering 死鎖時「試探性地」拿鎖。但注意它有個歷史包袱——語意上 trylock 的成功/失敗不追蹤 owner 一致性，某些場景（如中斷 context 想試 mutex）仍不建議，正規做法還是別在中斷 context 碰 mutex。

### 三層 path：fast / mid / slow

mutex 的實作刻意分三層，這個分層直接對應「競爭有多激烈」，也是理解它效能的關鍵。看 `include/linux/mutex.h` 的 `mutex_lock()` 與 `kernel/locking/mutex.c`：

```
   mutex_lock(lock)
     │
     ├─ fast path：__mutex_trylock_fast()
     │     一條 atomic cmpxchg：把 owner 從 0 換成 current
     │     成功 → 直接進臨界區（零 wait_list、零 schedule，最常見的路徑）
     │     失敗（有人持鎖）↓
     │
     ├─ mid path：mutex_optimistic_spin()   ← 下一節細講
     │     owner 正在別的 CPU 上跑 → 排進 OSQ 自旋等它放
     │     搶到 → 進臨界區（付了 spin，省了 context switch）
     │     判定沒指望（owner 睡了 / need_resched）↓
     │
     └─ slow path：__mutex_lock_common() 的睡眠迴圈
           拿 wait_lock（內部 raw_spinlock）→ 掛 mutex_waiter 上 wait_list
           for (;;) {
               set_current_state(TASK_UNINTERRUPTIBLE);
               if (__mutex_trylock()) break;   // 每次醒來重試
               raw_spin_unlock(wait_lock);
               schedule();                      // 真正睡下去
               raw_spin_lock(wait_lock);
           }
           摘掉 waiter、set owner、進臨界區
```

三層的意義：**絕大多數 `mutex_lock` 呼叫走 fast path**（一條 cmpxchg 就結束，和 spinlock 一樣快）；只有真的撞上競爭才掉進 mid path 自旋；只有連自旋都沒指望才掉進 slow path 睡。所以「mutex 很慢因為要睡」這個印象是錯的——它為「不睡」做了兩層優化，睡是最後手段。

注意 slow path 裡有兩把鎖：外層是要拿的 mutex，內層是保護 `wait_list` 的 `wait_lock`（一把 `raw_spinlock`）。掛佇列、改狀態這些動作極短，用忙等鎖保護剛好——這正好示範了「短臨界區用 spinlock、長臨界區用 mutex」，連 mutex 自己內部都遵守這條規則。而 `schedule()` 之前一定先放掉 `wait_lock`：帶著 spinlock 去 `schedule()` 就是踩雷集錦第 3 條的死罪。

### 樂觀自旋（optimistic spinning）：睡眠鎖裡的忙等

這是 mutex 效能的靈魂，也是面試常問。純睡眠鎖有個浪費：假設持鎖者只會再持有幾百個 cycle 就放手，等待者卻立刻睡下去——一睡一醒是兩次 context switch，比它要等的時間還久。

mutex 的對策（`CONFIG_MUTEX_SPIN_ON_OWNER`，`kernel/locking/mutex.c` 的 `mutex_optimistic_spin()`）：在真正睡下去之前，先判斷**持鎖者現在是不是正在某顆 CPU 上執行**（`owner->on_cpu`）。

```
   拿不到鎖，決策：要睡還是要 spin？
   ┌─────────────────────────────────────────────┐
   │  owner 正在別的 CPU 上跑？(owner->on_cpu==1) │
   │        │是                    │否            │
   │        ▼                      ▼              │
   │  它很可能馬上放手         它自己也睡著了或不在  │
   │  → 原地 spin 等它放       → 我 spin 也沒用      │
   │    （不睡，省 ctx switch） → 乖乖睡（schedule）  │
   └─────────────────────────────────────────────┘
   spin 期間隨時檢查：owner 換人了 / owner 不再在 CPU 上
   / 自己該被搶佔了（need_resched）→ 立刻停止 spin 改去睡
```

判斷邏輯很直覺：**owner 正在跑，代表它握著鎖但沒被排程走，很快會放**——這時 spin 幾圈搶到鎖，就省掉了「睡下去又被喚醒」的兩次 context switch。反過來，如果 owner 自己也睡著了（不在任何 CPU 上），那它短期內不會放鎖，等待者再 spin 就是純燒 CPU，不如去睡。

自旋不是無腦轉：它排隊用 **MCS lock**（一種 per-CPU、cache-line 友善的排隊自旋鎖，`kernel/locking/osq_lock.c` 的 optimistic spin queue / OSQ），避免多個等待者同時對同一條 cache line 打 cmpxchg 造成 cache-line bouncing（這個問題 Ch 25 的 qspinlock 也用 MCS 解決）。而且自旋隨時因為三件事中止改去睡：owner 換人了、owner 不在 CPU 上了、或自己 `need_resched`（有更該跑的 task）。

一句話總結 mutex：**它是「能睡的鎖」，但盡量不睡**——先試搶、再樂觀自旋、真的沒指望才睡。這個混合策略讓 mutex 在低競爭時快得接近 spinlock，高競爭或持鎖者睡著時又不浪費 CPU。

## semaphore：計數信號量，允許 N 個持有者

semaphore（`kernel/locking/semaphore.c`、`include/linux/semaphore.h` 的 `struct semaphore`）比 mutex 更古老，是 Dijkstra 的經典同步原語。核心是一個計數器加一個等待佇列：

```c
struct semaphore {
    raw_spinlock_t   lock;
    unsigned int     count;       // 還可以有幾個人進來
    struct list_head wait_list;
};
```

兩個操作（沿用 Dijkstra 的 P/V，kernel 叫 down/up）：

- `down(&sem)`：若 `count > 0` 就 `count--` 直接通過；否則把自己掛上 wait_list 睡掉（`__down_common()` 裡 `schedule_timeout` 睡，醒來檢查是被 `up` 喚醒還是逾時）。
- `up(&sem)`：若 wait_list 有人在等，直接喚醒佇列首位（把它標記為已取得，**不動 count**）；沒人等才 `count++`。

注意 `up` 的實作細節：有等待者時它**不是**先 `count++` 再叫醒對方，而是直接把名額「交棒」給被喚醒的那個 waiter。這避免了「`count++` 之後、被喚醒者搶到之前，第三者插隊把名額搶走」的問題——semaphore 靠這個保證等待者不被無限插隊餓死。這是 `kernel/locking/semaphore.c` 的 `__up()` 與 `up()` 值得一讀的地方，程式碼很短。

```c
struct semaphore sem;
sema_init(&sem, 3);        // 初始 count=3：最多 3 個持有者同時進入

down(&sem);                // 拿一個名額（沒名額就睡）
// ... 臨界區，最多 3 個 task 同時在這裡 ...
up(&sem);                  // 還一個名額
```

`sema_init(&sem, N)` 的 `N` 是關鍵：

- **N = 1**：binary semaphore，退化成互斥鎖，行為近似 mutex。
- **N > 1**：counting semaphore，允許 N 個持有者同時進入——這是 semaphore 相對 mutex 的**唯一**真正優勢，用來限制某資源的並發數（例如「同時最多 3 個 task 能存取這個裝置」）。

但即使 N=1，semaphore 也**不是** mutex 的同義詞，語意差在：

| | mutex | binary semaphore |
|---|---|---|
| owner 追蹤 | 有，只有 owner 能 unlock | 無，任何 task 都能 up |
| 樂觀自旋 | 有 | 無 |
| 誰鎖誰解 | 強制 | 不強制（可 A down、B up）|
| lockdep 支援 | 完整 | 較弱 |
| 用途 | 互斥 | 互斥 or 跨 thread 訊號 |

**現在的建議：需要互斥就用 mutex，不要用 binary semaphore。** mutex 有 owner、有樂觀自旋、有完整 lockdep，debug 與效能都更好。歷史上很多 kernel 程式碼用 semaphore 做互斥（那時它叫 `struct semaphore`，還有個 `DECLARE_MUTEX` 巨集其實是 binary semaphore——名字誤導了一整代人），這些多半是真正的 mutex（`struct mutex`，2006 年 Ingo Molnar 引入）出現前的遺產，後來十幾年間陸續被 `git grep` 出來換掉。今天你在新程式碼裡看到 `struct semaphore`，多半該懷疑作者是不是搞錯了。

semaphore 留下來的正當用途只剩兩個：**N>1 的並發限流**（例如一個裝置最多允許 3 個並發存取），以及**跨 thread 傳訊號**（A 做完某事後 up，B 在 down 上等）。而後者，其實有更乾淨、更不容易出錯的原語——completion。

## completion：一個 thread 等另一個做完某事

「A 做完某事，B 才能繼續」這種**單向同步**很常見：等一次 DMA 傳輸完成、等一個 kernel thread 起來初始化好、等 firmware 載入完。你可以用 binary semaphore（初始 0，A 完成後 up，B 在 down 上等）湊出來，但這種手搓法有微妙的 race——早期 kernel 真的踩過。

completion（`include/linux/completion.h`、`kernel/sched/completion.c`）是為這件事量身打造的原語，比手搓 semaphore 乾淨、正確：

```c
struct completion {
    unsigned int done;            // 完成計數
    struct swait_queue_head wait; // 等待佇列
};
```

三個核心操作：

```c
DECLARE_COMPLETION(setup_done);          // 靜態宣告
init_completion(&some_completion);       // 動態初始化

// B（等待方）：
wait_for_completion(&setup_done);        // 阻塞到有人 complete（不可被訊號打斷）

// A（完成方）：
complete(&setup_done);                   // 標記「一次」完成，喚醒一個等待者
complete_all(&setup_done);               // 標記完成，喚醒所有等待者
```

completion 相對 semaphore 的關鍵正確性優勢：它處理了「**A 先完成、B 才來等**」這個順序。`done` 計數如果已經是正的，`wait_for_completion` 直接通過、根本不睡——不會漏掉那個「早到的完成訊號」。手搓 semaphore 若不小心，很容易在這個時序上出錯（初始 count 設 0，若 A 的 `up` 發生在 B 尚未進入 `down` 之前，時序處理稍有差池就會 B 永遠醒不過來或 A 白 up 一次）。completion 把這個容易錯的時序封裝好，你只管 `complete` 與 `wait_for_completion`，不必自己推理計數。

一個典型場景（接 Ch 3/Ch 10 kernel thread）：模組 init 起一個 kthread 去做初始化，init 函式要等 kthread 準備好才能回傳：

```c
static struct completion ready;
static int worker(void *arg) {
    // ... 做初始化 ...
    complete(&ready);            // 通知：我準備好了
    // ... 繼續幹活 ...
    return 0;
}
static int __init my_init(void) {
    init_completion(&ready);
    kthread_run(worker, NULL, "my_worker");
    wait_for_completion(&ready); // 等 worker 說 ready 才往下
    pr_info("worker is up\n");
    return 0;
}
```

變體 API：`wait_for_completion_interruptible()`（可被訊號打斷，回 `-ERESTARTSYS`）、`wait_for_completion_timeout()`（等一段時間就放棄，回剩餘 jiffies，見 Ch 32），後者在等 I/O 這種「可能永遠等不到」的場景幾乎是必備——你不會想要一個永久卡死的 `wait_for_completion`。

`complete()` 與 `complete_all()` 的差別要分清：`complete()` 把 `done` 加一、只喚醒一個等待者，適合「一個生產者對一個消費者」的一次性握手；`complete_all()` 把 `done` 設成一個極大值並喚醒**所有**等待者，之後任何再來的 `wait_for_completion` 都直接通過——適合「一個事件、很多人在等」的廣播式通知（例如「裝置初始化完成，所有等它的路徑一起放行」）。用錯的話：需要廣播卻用 `complete()`，只會放行一個等待者，其餘繼續睡。

底層上 completion 的 `wait` 是 `swait_queue_head`（simple wait queue，比一般 `wait_queue` 精簡、開銷更小），因為 completion 的等待/喚醒模式固定、不需要一般 wait queue 的彈性。這也是「為特定模式量身打造的原語比通用原語更省」的一個小例子——你要是自己用通用 wait queue 加旗標手搓，多半又慢又容易錯。

## rw_semaphore：讀寫睡眠鎖

Ch 25 講過忙等版的 rwlock；rw_semaphore（rwsem，`include/linux/rwsem.h`、`kernel/locking/rwsem.c`）是它的睡眠版。同樣是「讀讀相容、讀寫互斥、寫寫互斥」，但拿不到時是睡而非空轉：

```c
DECLARE_RWSEM(my_rwsem);
down_read(&my_rwsem);   /* ... 只讀臨界區，可多人並行 ... */  up_read(&my_rwsem);
down_write(&my_rwsem);  /* ... 讀寫臨界區，獨占 ... */        up_write(&my_rwsem);

down_read_trylock(&my_rwsem);   // 試讀，不睡
down_write_killable(&my_rwsem); // 寫，可被致命訊號打斷
```

適用於**讀遠多於寫、且臨界區可能睡（或夠長）**的資料。它的內部同樣有樂觀自旋（對寫者，判斷 owner 是否在跑）與 owner 追蹤。

實作上的巧思在那個 `count`（`kernel/locking/rwsem.c` 的 `struct rw_semaphore` 的 `atomic_long_t count`）：它把好幾個資訊塞進一個 atomic 變數的不同 bit 位——讀者數量（高位一段）、以及低位的幾個旗標（`RWSEM_WRITER_LOCKED`：有寫者持鎖；`RWSEM_FLAG_WAITERS`：wait_list 上有人在等；`RWSEM_FLAG_HANDOFF`：交棒中）。讀者拿鎖是對這個 count 做 atomic add（加上讀者單位），寫者拿鎖是 cmpxchg 把 writer bit 設起來。把所有狀態壓進一個 atomic，讀者的 fast path（無競爭時）就是一條 atomic 指令，不必碰內部 spinlock——這是 rwsem 讀側夠快的關鍵。

防餓死：讀者持鎖時若有寫者來等（`RWSEM_FLAG_WAITERS` 被設起），後續**新來的讀者**會被擋住去排隊，而不是繼續加到讀者計數上——否則源源不絕的讀者會讓寫者永遠等不到。這是 reader-writer 鎖的經典公平性設計。

kernel 裡最著名的 rwsem 是 **`mmap_lock`**（Ch 19，`struct mm_struct` 的 `mmap_lock`，舊名 `mmap_sem`）：它保護整個行程的 VMA 樹。page fault 走 read 側（多個 fault 可並行查 VMA），`mmap()`/`munmap()`/`brk()` 改動位址空間走 write 側（獨占）。這把鎖是 mm 子系統的著名瓶頸，近年 kernel 花大力氣做 per-VMA lock、RCU 化 VMA 查找就是為了繞過它——這也預示了 Ch 27 的 RCU：當讀多到 rwsem 的讀者計數本身都成瓶頸時，連睡眠鎖都嫌重，要換成「讀者零成本」的 RCU。

## 對比與取捨：鎖選擇總表

先是本章的「睡眠鎖 vs 忙等鎖」對照——這是 Ch 25 到 Ch 26 的分水嶺：

| | 忙等鎖（spinlock，Ch 25） | 睡眠鎖（mutex 等，本章） |
|---|---|---|
| 拿不到時 | 原地空轉（燒 CPU） | 讓出 CPU 睡著 |
| 等待成本 | CPU 週期（無 ctx switch） | 一次 context switch |
| 臨界區內能睡嗎 | **不能** | **能** |
| 可用 context | 任何（含中斷） | 只有 process context |
| 適合臨界區 | 極短（幾十~幾百 cycle） | 較長，或會睡 |
| 代表 | spinlock、rwlock | mutex、rwsem、semaphore |

接著是跨全部同步原語的選擇表——這張是本章要你帶走的東西（RCU 見 Ch 27）：

| 原語 | 能睡? | 可用 context | 持有者數 | 讀寫對稱? | 典型用途 |
|---|---|---|---|---|---|
| **spinlock**（Ch 25） | 否 | 任何（含中斷） | 1 | 否 | 極短臨界區、中斷 handler 內同步 |
| **rwlock**（Ch 25） | 否 | 任何 | 多讀/1寫 | 是 | 極短、讀多寫少、中斷可用 |
| **mutex** | 是 | process | 1 | 否 | 一般互斥、臨界區長或會睡 |
| **rw_semaphore** | 是 | process | 多讀/1寫 | 是 | 讀多寫少且臨界區長/會睡（如 mmap_lock）|
| **semaphore** | 是 | process | N | 否 | N>1 並發限流；互斥用途已被 mutex 取代 |
| **completion** | 是 | process（等待側）| — | — | 單向「等某事完成」（等 I/O、等 kthread）|
| **RCU**（Ch 27） | 讀側否 | 讀側任何 | 多讀無限 | 極不對稱 | 讀極多寫極少、讀者要零成本 |

這張表值得背下來，面試「這場景你用哪種鎖」幾乎必考。真正的判斷不是死記某個原語，而是把場景拆成幾個維度往下問。

決策順序，照這四個問題往下問：

1. **臨界區會在中斷 context 跑嗎？** 會 → 只能 spinlock/rwlock（睡眠鎖出局）。
2. **臨界區裡會睡嗎（kmalloc GFP_KERNEL、copy_*_user、等 I/O、msleep）？** 會 → 必須睡眠鎖。
3. **是「等一件事發生」還是「保護一段共享資料」？** 等事情 → completion。保護資料 → 往下。
4. **讀寫比例如何、要幾個持有者？** 讀多寫少 → rwsem（或 Ch 27 的 RCU）；需要 N 個並發 → semaphore；其餘一般互斥 → mutex。

## 踩雷集錦

1. **錯誤直覺：「semaphore 初始化成 1 就等於 mutex」**。行為近似但語意不同。binary semaphore 沒有 owner，任何 task 都能 `up`，也沒有樂觀自旋、lockdep 較弱。要互斥就用 mutex，別用 semaphore 假裝——除非你真的需要 A 鎖 B 解的跨 thread 訊號（那更該用 completion）。

2. **錯誤直覺：「mutex 拿不到就一定睡，所以一定慢」**。錯。mutex 有樂觀自旋：持鎖者正在別的 CPU 上跑時，等待者先 spin 而不睡，低競爭下快得接近 spinlock。「mutex = 慢」是對它的誤解。

3. **錯誤直覺：「在 spinlock 臨界區裡呼叫 mutex_lock 沒關係」**。這是嚴重錯誤。持著 spinlock（通常已關搶佔）時去拿可能會睡的 mutex，一旦真的睡下去，帶著關掉搶佔的狀態 `schedule()`，輕則 `BUG: scheduling while atomic`，重則死鎖。睡眠鎖不能巢狀在忙等鎖裡面。lockdep 會抓，但你該先在腦裡就避開。

4. **錯誤直覺：「別的 thread 幫我 unlock mutex 沒問題」**。mutex 強制「誰鎖誰解」。要 A 執行、B 通知的模式，那不是 mutex 的用途，是 completion（或退一步 semaphore）的用途。用 mutex 硬做會被 debug 檢查抓出來，或造成 owner 追蹤錯亂。

5. **錯誤直覺：「`wait_for_completion` 一定要在 `complete` 之後才呼叫，不然會漏」**。completion 正是為了不漏這個順序而設計的：`done` 計數若已為正，`wait_for_completion` 直接通過不睡。這是它比手搓 semaphore 更正確的地方。反而是手搓 semaphore 容易在這個時序上出錯。

6. **錯誤直覺：「用不可打斷版的 `wait_for_completion` 等 I/O 最省事」**。等一個「理論上會發生但可能永遠不發生」的事件（硬體故障、對端掛掉）時，不帶逾時的 `wait_for_completion` 會讓該 task 永久卡在 D 狀態，連 `kill -9` 都無效，只能重開機。等外部事件請一律用 `wait_for_completion_timeout()` 或 `_interruptible()`，給自己一條退路。純內部、保證會完成的握手才用不帶逾時的版本。

## 進階：再往深一層

- **優先級反轉（priority inversion）**：低優先級 task L 持鎖，高優先級 task H 等這把鎖，中優先級 task M 一直搶到 CPU 把 L 壓著跑不完——結果 H 被 M 間接卡住，優先級形同倒轉。經典案例是 1997 年火星探路者（Mars Pathfinder）的重啟 bug：一個高優先級的資料匯流排管理任務等一把被低優先級氣象任務持有的 mutex，而中優先級的通訊任務長時間佔著 CPU，導致高優先級任務遲遲跑不動、觸發看門狗（watchdog）重啟——最後靠遠端打開 VxWorks 的優先級繼承旗標才修好。解法就是**優先級繼承（priority inheritance）**：讓持鎖的 L 暫時繼承 H 的高優先級，趕快跑完放鎖，M 就壓不住它了。kernel 的 `rt_mutex`（`kernel/locking/rtmutex.c`）就是支援優先級繼承的 mutex，主要用在即時（-rt）場景，這是 Ch 31（threaded IRQ/-rt）的主題。一般 mutex 不做優先級繼承（成本考量、且繼承鏈的計算本身有開銷）。

- **PREEMPT_RT 把 spinlock 變睡眠鎖**：在即時 kernel（現已大量進主線）裡，多數 spinlock 被替換成 rt_mutex 基礎的睡眠鎖，好讓臨界區可被搶佔、降低延遲。這翻轉了本章「spinlock 不能睡」的前提——但那是特殊組態，一般 kernel 仍照本章的規則。

- **`mutex_lock` 為何預設用 UNINTERRUPTIBLE 而非可中斷**：可中斷版每次都要檢查、處理訊號回傳，呼叫端要寫錯誤處理。多數 kernel 內部路徑不希望被訊號打斷（打斷了也無事可做），所以預設不可中斷；只有直接服務 syscall、且使用者可能想中止的路徑才用 `_interruptible`。

- **lockdep 對 mutex 的威力**：lockdep（Ch 28）會記錄每把鎖的取得順序，跨整個 kernel 建一張鎖依賴圖，一旦發現可能形成環（A→B 與 B→A 的取鎖順序並存）就報 `possible circular locking dependency`——它能在死鎖**還沒真的發生**時就抓到潛在死鎖。mutex 有 owner 資訊，lockdep 對它的檢查最完整。

- **為什麼有了 mutex，spinlock 沒被淘汰**：既然 mutex 靠樂觀自旋在低競爭下快得接近 spinlock，為什麼不全用 mutex？因為 mutex 再快也有 `struct mutex` 的體積（owner + wait_lock + wait_list，數十位元組）與 slow path 的複雜度，而 spinlock 可以極小（`CONFIG_SMP` 下就是幾個位元組的 `qspinlock`）。在中斷 context、或臨界區短到「連判斷該不該自旋的分支都嫌貴」的極熱路徑上，spinlock 的簡單本身就是優勢。兩者分工而非取代，這也是為什麼 kernel 兩套都留著。

- **`__must_check` 與拿鎖失敗**：`mutex_lock_interruptible()`、`down_interruptible()`、`wait_for_completion_interruptible()` 這些可失敗版本的回傳值都標了 `__must_check`（或約定俗成要檢查）。忽略回傳值直接進臨界區，等於「以為拿到鎖其實沒拿到」——這是可打斷版最常見的誤用，被訊號叫醒後你以為持鎖，實際上共享資料毫無保護。編譯器的 `-Wunused-result` 會對前者發警告，別無視它。

## 動手：三個模組驗證睡眠鎖

### 動手一：證明 mutex 能睡、spinlock 不能

寫一個模組，在 mutex 臨界區裡 `msleep`（睡 100ms）——這能正常跑。然後把 mutex 換成 spinlock，同樣在臨界區 `msleep`，觀察 kernel 怎麼罵你。

```c
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/delay.h>
#include <linux/kthread.h>

static DEFINE_MUTEX(demo_lock);

static int worker(void *arg)
{
    long id = (long)arg;
    mutex_lock(&demo_lock);
    pr_info("task %ld got mutex, sleeping in critical section\n", id);
    msleep(100);                       // 在睡眠鎖臨界區裡睡：合法
    pr_info("task %ld releasing mutex\n", id);
    mutex_unlock(&demo_lock);
    return 0;
}

static int __init demo_init(void)
{
    kthread_run(worker, (void *)1, "demo1");
    kthread_run(worker, (void *)2, "demo2");   // 兩個 thread 搶同一把鎖
    return 0;
}
static void __exit demo_exit(void) { msleep(300); }
module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
```

`dmesg` 會看到 task 1 先拿鎖睡 100ms，task 2 在 `mutex_lock` 上睡著等（不燒 CPU），task 1 放鎖後 task 2 才進來。**改成 spinlock 試試**：把 `DEFINE_MUTEX` 換 `DEFINE_SPINLOCK`、`mutex_lock/unlock` 換 `spin_lock/unlock`，其餘不動。開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 的 kernel 會噴 `BUG: sleeping function called from invalid context`——因為 `msleep` 想睡，但 spinlock 臨界區關了搶佔、不准睡。這一噴就是本章的實證。

### 動手二：completion 讓 init 等 kthread

改 Ch 0 的 hello 模組，讓 init 起一個 kthread、必須等它 `complete` 才回傳（就是前面 completion 那段的完整化）。在 worker 的 `complete(&ready)` 前後各 `msleep(50)`，你會觀察到 `my_init` 確實卡在 `wait_for_completion` 等那 50ms，證明同步生效——而且不佔 CPU。

### 動手三：用 lockdep 抓 mutex 死鎖（AB-BA）

開 `CONFIG_PROVE_LOCKING`（lockdep）。寫兩把 mutex `A`、`B`，一個 thread 依序 `lock(A); lock(B)`，另一個 thread 依序 `lock(B); lock(A)`——經典 AB-BA 死鎖。lockdep 不需要死鎖**真的發生**，只要它同時觀察到 A→B 與 B→A 兩種取鎖順序，就會在 `dmesg` 印出 `possible circular locking dependency detected` 加上兩條鎖鏈。這是 Ch 28 的預告：lockdep 把死鎖從「碰運氣才復現的 heisenbug」變成「一跑就報的確定性錯誤」。

## 本章重點整理

- **睡眠鎖 vs 忙等鎖的分界是「拿不到時佔 CPU 空轉還是讓出 CPU 睡」**；選哪個看臨界區長短、能否睡、以及是否在中斷 context（中斷 context 只能忙等鎖）。
- **mutex 是最常用的睡眠互斥鎖**：有 owner（只有 owner 能 unlock）、不可遞迴；靠**樂觀自旋**在持鎖者正在跑時先 spin 而不睡，低競爭下接近 spinlock 的速度。
- **semaphore 允許 N 個持有者**（N=1 退化成 binary semaphore），但互斥用途已被 mutex 取代（mutex 有 owner、樂觀自旋、完整 lockdep）；正當用途剩下 N>1 限流。
- **completion 是單向同步原語**（等某事完成），正確處理「完成早於等待」的時序，比手搓 semaphore 乾淨；**rw_semaphore 是睡眠版讀寫鎖**，`mmap_lock` 是代表。用那張選擇表下決定。

## 自我檢核

- [ ] 不看筆記，能講清楚「為什麼 spinlock 臨界區裡不能 `kmalloc(GFP_KERNEL)` / `copy_to_user` / `msleep`」，而 mutex 可以
- [ ] 面試被問「mutex 和 spinlock 差在哪、什麼時候用哪個」，你能從「能否睡、臨界區長短、context」三個軸答完整
- [ ] 能解釋樂觀自旋的決策：為什麼「owner 正在別的 CPU 上跑」是該 spin 而不是該睡的訊號
- [ ] 能說出 mutex 與 binary semaphore 至少三個語意差異（owner、樂觀自旋、誰鎖誰解）
- [ ] 面試被問「A thread 做完某事 B thread 才能繼續，你用什麼原語」，你會答 completion 而不是手搓 semaphore，並能說明為什麼
- [ ] 給你一個場景（讀多寫少的長臨界區 / 中斷裡的極短臨界區 / N 並發限流），能對照選擇表選對鎖

## 延伸閱讀

### 官方文件

- **[Documentation/locking/mutex-design.rst](https://www.kernel.org/doc/html/latest/locking/mutex-design.html)**
  - **讀哪裡**：整篇。這是 mutex 設計的權威說明，樂觀自旋、owner 追蹤、與 semaphore 的取捨都在裡面
  - **和本章的關聯**：本章 mutex 那幾節的原始出處，想確認細節（如 fast/mid/slow path 的實作）回這裡

- **[Documentation/locking/locktypes.rst](https://www.kernel.org/doc/html/latest/locking/locktypes.html)**
  - **讀哪裡**：整篇，特別是 sleeping vs spinning 的分類與 PREEMPT_RT 下的轉換
  - **能學到什麼**：kernel 官方對「所有鎖型別」的分類地圖，正好對應本章的選擇表；PREEMPT_RT 把 spinlock 變睡眠鎖的細節在這

### 經典書籍與論文

- **《Linux Kernel Development, 3rd Ed.》第 9–10 章** — Robert Love
  - **這本書的定位**：第 9 章（Kernel Synchronization Introduction）建立競態與臨界區直覺，第 10 章（Kernel Synchronization Methods）逐一講 mutex/semaphore/completion/spinlock
  - **注意**：對應舊 kernel，樂觀自旋等新機制沒有，但基本語意與選擇原則歷久不變

- **[LWN: "The mutex/spinlock decision" 與樂觀自旋系列文章](https://lwn.net/Kernel/Index/#Mutexes)**
  - **讀哪裡**：LWN 的 Mutexes / Locking 索引，挑樂觀自旋（optimistic spinning）與 MCS/OSQ 的幾篇
  - **為什麼值得讀**：這些機制的來龍去脈（為什麼引入、解決什麼效能問題）在 commit log 裡看不到全貌，LWN 的文章把設計動機講清楚
  - **前提**：讀完本章、大致懂 cache-line bouncing（Ch 25）

### 源碼

- **`kernel/locking/mutex.c` 的 `__mutex_lock_common()` 與 `mutex_optimistic_spin()`**
  - **讀哪裡**：先讀 fast path（`mutex_lock` 的 `__mutex_trylock_fast`），再讀 slow path 的自旋與睡眠迴圈
  - **配合**：`kernel/locking/osq_lock.c`（MCS/OSQ 排隊自旋），`kernel/sched/completion.c`（completion 實作，短小好讀，適合對照 semaphore 看差異）

睡眠鎖與忙等鎖到這裡都齊了。但還有一種同步策略走另一條極端路線：讓**讀者完全不加鎖、零成本**，把所有代價都壓到寫者身上——當讀多到連 rwsem 的讀者計數都成瓶頸（就像 `mmap_lock`），這是唯一出路。下一章進 RCU。

→ [Ch 27 RCU 深入：讀者無鎖的並行讀寫](./27-rcu.md)
