# Ch 11 — 排程器框架：scheduler class 與 runqueue

> **目標**：理解 Linux 排程器的**架構骨架**——`struct sched_class` 這個物件導向式的分派介面、per-CPU 的 `struct rq`、以及 `__schedule()` 這個「換人上場」的主函式。學完你能在腦中畫出「一次排程決策從哪裡被觸發、怎麼從高優先權 class 問到低優先權 class、最後怎麼挑出下一個 task」的完整流程圖。具體的挑選演算法（CFS 的紅黑樹、EEVDF 的 lag）留給 Ch 12/13；真正做暫存器切換的下半場是 Ch 14。

## 為什麼需要這個？

一台機器上永遠有比 CPU 核心數更多的 task 想跑：你的 shell、瀏覽器、`kswapd`、一堆 kernel thread、加上剛 `fork` 出來（Ch 10）還在 runnable 狀態的新 process。CPU 就那幾顆，**誰先跑、跑多久、被誰打斷**，這個決策每秒要做上千次，而且要對所有這些性質截然不同的 task 都「公平又即時」。

問題在於：這些 task 的需求根本互不相容。

- 一個播放音訊的 thread：慢一毫秒就爆音，它要的是**低延遲、準時**。
- 一個編譯 kernel 的 `cc1`：它不在乎延遲，它要的是**長期拿到夠多 CPU 時間**、把活幹完。
- 一個 `SCHED_DEADLINE` 的控制迴路：它有硬性截止時間，**錯過就是系統失效**。
- watchdog、CPU 熱插拔那種「必須現在、立刻、無條件跑」的系統任務：它要**凌駕一切**。

如果用**一套**演算法同時服務這四種需求，你會得到一個到處是 `if (task is realtime) ... else if (task is deadline) ...` 的巨型函式，每加一種排程策略就要動它一次。這正是舊 kernel（2.4 時代的 `O(n)` scheduler、2.6 早期的 `O(1)` scheduler）的痛：策略和機制糾纏在一起，改一個動全身。

Linux 的解法是**分層**：把排程器拆成幾個獨立的 **scheduler class（排程類別）**，每個 class 自己實作一套完整的排程演算法，彼此用一個固定的介面（`struct sched_class`）溝通。核心的 `__schedule()` 不知道任何一個 class 的內部演算法，它只做一件事：**按 class 的優先順序，從高到低問每個 class「你手上有沒有 task 該跑？」**——第一個回答「有」的，就贏得這顆 CPU。

這是典型的**策略模式（strategy pattern）**，用 C 的函式指標實作出來的物件導向。這章我們讀的就是這個框架本身。

## 先建立直覺

先把整個排程器的骨架畫出來。有兩個維度：**縱向**是 scheduler class 的優先鏈，**橫向**是每顆 CPU 各有一份 runqueue。

```
  scheduler class 優先鏈（縱向，pick_next_task 由上往下問）
  ┌──────────────────────────────────────────────────────────────┐
  │  stop_sched_class     最高：CPU 熱插拔、migration thread       │
  │       │               「無條件、立刻跑」，搶佔一切             │
  │       ▼                                                        │
  │  dl_sched_class       SCHED_DEADLINE：有硬性截止時間           │
  │       │               EDF（earliest deadline first）          │
  │       ▼                                                        │
  │  rt_sched_class       SCHED_FIFO / SCHED_RR：即時，固定優先權  │
  │       │               100 條優先權 queue                       │
  │       ▼                                                        │
  │  fair_sched_class     SCHED_NORMAL/BATCH/IDLE：一般 task       │
  │       │               CFS→EEVDF（Ch 12/13），佔絕大多數        │
  │       ▼                                                        │
  │  idle_sched_class     最低：什麼都沒得跑時的 idle thread       │
  └──────────────────────────────────────────────────────────────┘

  per-CPU runqueue（橫向，每顆 CPU 一份，互不共用鎖）
  ┌───────── CPU 0 ─────────┐   ┌───────── CPU 1 ─────────┐
  │ struct rq {             │   │ struct rq {             │
  │   raw_spinlock_t lock;  │   │   raw_spinlock_t lock;  │
  │   struct cfs_rq  cfs; ──┼─┐ │   struct cfs_rq  cfs;   │
  │   struct rt_rq   rt;    │ │ │   struct rt_rq   rt;    │
  │   struct dl_rq   dl;    │ │ │   struct dl_rq   dl;    │
  │   task *curr;  ─────────┼─┼→│ (正在這顆 CPU 上跑的 task)│
  │   nr_running = 3;       │ │ │   ...                   │
  │ }                       │ │ │ }                       │
  └─────────────────────────┘ │ └─────────────────────────┘
                              │
                cfs_rq 內含一棵紅黑樹（Ch 12），
                裝著這顆 CPU 上所有 SCHED_NORMAL 的 task
```

三個要記住的關鍵：

1. **class 有固定優先順序**：stop > dl > rt > fair > idle。這個順序是**寫死在 class 的鏈結順序裡**的（下面看源碼），不是動態算出來的。只要 rt class 手上有 runnable task，fair class 的 task 就一個都別想跑——這就是為什麼一個失控的 `SCHED_FIFO` 迴圈能凍住整台機器。

2. **每顆 CPU 一份 runqueue**：不是全域一個大 queue。每個 `struct rq` 裡又**分層裝著各 class 的子 runqueue**（`cfs_rq`、`rt_rq`、`dl_rq`）。task 被排到哪顆 CPU、放進哪個子 queue，是兩個獨立的問題。

3. **`__schedule()` 是仲裁者，不是演算法**：它只負責「按順序問各 class、拿到下一個 task、呼叫 context switch」。真正「這顆 CPU 上該選哪個 fair task」的智慧，在 `fair_sched_class` 的 `pick_next_task_fair` 裡（Ch 12/13）。框架和演算法分離。

## struct sched_class：用函式指標做的物件導向

排程器的核心抽象在 `kernel/sched/sched.h` 的 `struct sched_class`。它是一堆函式指標的集合——每個 scheduler class 填一份，等於實作了同一個介面。挑幾個最關鍵的看：

```c
// kernel/sched/sched.h（欄位為 v6.12，順序/細節以源碼為準）
struct sched_class {
    void (*enqueue_task)(struct rq *rq, struct task_struct *p, int flags);
    void (*dequeue_task)(struct rq *rq, struct task_struct *p, int flags);

    void (*yield_task)(struct rq *rq);

    // 一個剛被喚醒/入列的 task 該不該搶佔目前正在跑的 curr？
    void (*wakeup_preempt)(struct rq *rq, struct task_struct *p, int flags);

    // 這個 class「挑下一個要跑的 task」——排程的核心動作
    struct task_struct *(*pick_next_task)(struct rq *rq);

    // 每個 tick（scheduler_tick 觸發）呼叫一次，class 用它做記帳/決定要不要搶佔
    void (*task_tick)(struct rq *rq, struct task_struct *p, int queued);

    void (*set_next_task)(struct rq *rq, struct task_struct *p, bool first);
    void (*put_prev_task)(struct rq *rq, struct task_struct *p, ...);
    // ... SMP 相關的 select_task_rq / balance / migrate 等（Ch 15）
};
```

> **設計要點**：這就是 C 語言版的 vtable。C++ 的 `virtual` 由編譯器隱式生成一張函式指標表；kernel 沒有 C++，於是**手動**維護這張表。`enqueue_task(rq, p, flags)` 這種呼叫，本質上就是 `p->sched_class->enqueue_task(rq, p, flags)`——透過 task 身上掛的 `sched_class` 指標，分派到對的 class 實作。`task_struct`（Ch 9）裡就有一個 `const struct sched_class *sched_class` 欄位，記錄這個 task 屬於哪一類。

### 五個 class 與它們的實作檔

| class（變數名） | 對應源碼 | 服務的 policy | 演算法 |
|---|---|---|---|
| `stop_sched_class` | `kernel/sched/stop_task.c` | 無（內部用） | 永遠最優先，不排隊 |
| `dl_sched_class` | `kernel/sched/deadline.c` | `SCHED_DEADLINE` | EDF + CBS |
| `rt_sched_class` | `kernel/sched/rt.c` | `SCHED_FIFO`/`SCHED_RR` | 100 條固定優先權 queue |
| `fair_sched_class` | `kernel/sched/fair.c` | `SCHED_NORMAL`/`BATCH`/`IDLE` | CFS→EEVDF（Ch 12/13） |
| `idle_sched_class` | `kernel/sched/idle.c` | 無（每 CPU 的 idle thread） | 沒得選就跑它 |

### 優先鏈是怎麼「串」起來的

class 之間的先後順序不是用一個 `priority` 數字比大小，而是**編譯期就把它們排成一條鏈**。在 v6.12，每個 class 定義時用 `DEFINE_SCHED_CLASS(name)` 宏，把該 class 的結構體放進一個特殊的 linker section（`__sched_class_highest` 到 `__sched_class_lowest` 之間），按**記憶體位址順序**排列。於是「從最高優先權 class 往低走」就等於「在這個 section 裡從高位址往低位址掃」。

kernel 提供兩個宏走這條鏈（`kernel/sched/sched.h`）：

```c
// 從最高優先權 class 開始，往低走
#define for_each_class(class) \
    for_class_range(class, sched_class_highest, sched_class_idle + 1)
```

> **為什麼用 linker section 而不是 linked list？** 因為順序在編譯期就完全確定、執行期永不改變，用 linker 排好位址、遍歷時只是指標 `++`，比走 linked list 少一次記憶體間接存取，且對 CPU 的預取友善。這是 kernel 裡「把執行期常數搬到編譯期」的典型手法。你不用背這個機制，記住結論就好：**class 順序是編譯期定死的，stop 最高、idle 最低。**

### policy 怎麼對應到 class

使用者呼叫 `sched_setscheduler(2)` 設的是 **policy**（`SCHED_NORMAL`、`SCHED_FIFO` 等，`task_struct->policy` 欄位），不是直接選 class。從 policy 映射到 class 由 `kernel/sched/core.c` 的 `__setscheduler_class`（依 policy 挑對應的 `sched_class`）決定：

| policy | class | 說明 |
|---|---|---|
| `SCHED_DEADLINE` | `dl_sched_class` | 給 deadline，(runtime, period, deadline) 三參數 |
| `SCHED_FIFO` / `SCHED_RR` | `rt_sched_class` | 即時，priority 1–99 |
| `SCHED_NORMAL` / `SCHED_BATCH` / `SCHED_IDLE` | `fair_sched_class` | 一般，用 nice 值調權重 |

`stop` 和 `idle` 兩個 class **沒有對應的 user 可設 policy**——它們是 kernel 內部專用的（migration thread 和 idle thread），使用者程式碼碰不到。所以你能設的 policy 只落在 dl/rt/fair 三個 class 上。`sched_setscheduler` 改 policy 時，會把 task 從舊 class 的子 queue 移除、換 `sched_class` 指標、再 enqueue 進新 class——task「換 class」就是這樣發生的。

## per-CPU run queue：struct rq

排程的「桌面」是 `struct rq`（run queue），定義在 `kernel/sched/sched.h`。**每顆 CPU 各有一份**，透過 per-CPU 變數 `runqueues` 存放（接 Ch 7 的 per-CPU 機制）：

```c
// kernel/sched/sched.h
DECLARE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues);

#define cpu_rq(cpu)   (&per_cpu(runqueues, (cpu)))   // 取某顆 CPU 的 rq
#define this_rq()     this_cpu_ptr(&runqueues)        // 取「我」這顆的 rq
```

`struct rq` 裡的關鍵欄位：

```c
struct rq {
    raw_spinlock_t   __lock;        // 保護這個 rq 的 rq lock
    unsigned int     nr_running;    // 這顆 CPU 上 runnable 的 task 數（跨所有 class）

    struct cfs_rq    cfs;           // fair class 的子 runqueue（內含紅黑樹，Ch 12）
    struct rt_rq     rt;            // rt class 的子 runqueue（100 條 priority queue）
    struct dl_rq     dl;            // deadline class 的子 runqueue（紅黑樹按 deadline 排）

    struct task_struct __rcu *curr; // 「現在」正在這顆 CPU 上跑的 task
    struct task_struct *idle;       // 這顆 CPU 的 idle thread
    struct task_struct *stop;       // 這顆 CPU 的 stop（migration）thread

    u64              clock;         // 這個 rq 的時間戳（記帳用）
    // ... 還有 load、CPU capacity、負載均衡、時鐘等一大堆欄位
};
```

注意 `rq` 裡**沒有一個大而全的 task 列表**，而是**分層**成 `cfs`、`rt`、`dl` 三個子 runqueue。當 `__schedule` 要挑下一個 task，它是「先問 dl_rq，再問 rt_rq，再問 cfs_rq」——這正好對應上面的 class 優先鏈。stop 和 idle 兩個 class 各只有一個 task（那顆 CPU 專屬的 `rq->stop` 和 `rq->idle`），不需要一個複雜的子 queue。

### 為什麼是 per-CPU，不是全域一個 queue？

如果全系統只有一個 run queue，那**每次**任何一顆 CPU 要做排程決策，都得先搶那個全域鎖。核心數越多，這個鎖的爭用（contention）越嚴重——16 顆 CPU 同時想排程，15 顆在自旋等鎖。這就是全域 queue 無法擴展（scalability）的根本原因。

per-CPU rq 讓每顆 CPU **絕大多數時候只碰自己的 rq、只拿自己的 rq lock**：喚醒本地 task、tick 記帳、挑下一個 task，全在本地完成，零跨核爭用。代價是「一個 task 只在一顆 CPU 的 rq 裡」這件事，讓「把 task 從忙的 CPU 搬到閒的 CPU」變成一個獨立問題——那就是 **load balancing（負載均衡）**，需要短暫鎖住兩顆 CPU 的 rq，是 Ch 15 的主題。

> **設計哲學**：這是 kernel 反覆出現的取捨——**「把資料切成 per-CPU、消滅共享，換取跨核協調變複雜」**。per-CPU 讓 common case（本地操作）飛快，把成本推給 rare case（跨核搬 task）。你在 Ch 7 已經看過這個模式，排程器是它最重要的應用之一。

### rq lock：什麼保護什麼

`struct rq` 裡的 `__lock`（一個 `raw_spinlock_t`，用 `rq_lock()` / `rq_unlock()` 存取，別直接碰 `__lock`）保護的是**這顆 CPU rq 的整個排程狀態**：它的三個子 runqueue、`curr`、`nr_running`、負載統計等。`__schedule` 一開始拿的就是這把鎖，所以「挑下一個 task」這整段是在鎖裡跑的臨界區。

大多數操作只碰本地 rq、只拿一把鎖。但有兩種情況要**同時鎖兩顆 CPU 的 rq**：負載均衡把 task 從 A 搬到 B、以及喚醒一個「上次在別顆 CPU 上跑」的 task。這時為了避免死鎖，kernel 用 **rq 的位址順序**當鎖序（一律先鎖位址小的那顆，見 `kernel/sched/core.c` 的 `double_rq_lock`）——這是 lock ordering 的經典手法（Ch 28 會系統性地講死鎖與鎖序）。這也再次說明「per-CPU 換來跨核複雜度」的代價具體長什麼樣。

## 底層機制：__schedule() 一次排程決策的全流程

排程的主函式是 `kernel/sched/core.c` 的 `__schedule()`。這是整個框架的心臟。它做的事情用文字說是：**「把目前的 task 收好（如果它要讓出 CPU）→ 挑出下一個該跑的 task → 切換過去」**。切換那一步（context_switch）是 Ch 14 的下半場，這裡看它「怎麼挑」。

```
        排程觸發（下一節詳述）→ 進入 __schedule(sched_mode)
                     │
                     ▼
        ┌─────────────────────────────────────────────┐
        │ 1. local_irq_disable() + 拿 rq lock          │
        │    （排程中不能被中斷/搬走，是臨界區）        │
        ├─────────────────────────────────────────────┤
        │ 2. prev = rq->curr                           │
        │    如果 prev 是「主動睡」(TASK_INTERRUPTIBLE  │
        │    等非 running 狀態) 且沒有 pending signal： │
        │        deactivate_task(prev)  ← 從 rq 移除    │
        │    否則 prev 仍 runnable，留在 rq 裡          │
        ├─────────────────────────────────────────────┤
        │ 3. next = pick_next_task(rq, prev, ...)      │
        │    ★ 核心：按 class 優先鏈由高到低問（下圖）  │
        ├─────────────────────────────────────────────┤
        │ 4. clear TIF_NEED_RESCHED（搶佔請求已滿足）  │
        │    if (next != prev)                         │
        │        context_switch(rq, prev, next)  ← Ch14│
        │        （這裡放掉 rq lock、切 mm、切暫存器）  │
        │    else                                       │
        │        直接放 rq lock（沒人要換，繼續跑 prev）│
        └─────────────────────────────────────────────┘
```

### pick_next_task：優先鏈由高到低問

`pick_next_task`（`kernel/sched/core.c`）是「按 class 優先順序問一輪」的地方。它的核心邏輯：

```c
// kernel/sched/core.c，pick_next_task() 概念版
static struct task_struct *
pick_next_task(struct rq *rq, struct task_struct *prev, struct rq_flags *rf)
{
    const struct sched_class *class;
    struct task_struct *p;

    // 快路徑：如果整個 rq 裡 runnable 的 task 全都是 fair class 的
    //（nr_running == cfs.h_nr_running），直接問 fair，跳過上層 class
    if (likely(!sched_class_above(prev->sched_class, &fair_sched_class) &&
               rq->nr_running == rq->cfs.h_nr_running)) {
        p = pick_next_task_fair(rq, prev, rf);
        if (likely(p))
            return p;
        // fair 沒有 → 落到 idle
    }

    // 慢路徑：從最高優先權 class 往下逐一問
    for_each_class(class) {
        p = class->pick_next_task(rq);
        if (p)
            return p;       // 第一個回答「有」的 class 贏
    }

    BUG();  // idle class 一定回一個 task，走不到這
}
```

兩個關鍵設計：

- **快路徑（fast path）**：絕大多數系統上，99% 的 task 都是 fair class（一般 process）。每次排程都從 stop→dl→rt 一路問下來是浪費——上面那些 class 的子 queue 幾乎總是空的。所以先檢查「是不是所有 runnable task 都在 fair」，是的話**直接跳到 `pick_next_task_fair`**，省掉問三個空 class 的開銷。這是為 common case 優化的典型手法。

- **慢路徑（slow path）**：只要有任何 rt/dl/stop task runnable，就走完整的 `for_each_class` 迴圈，從最高優先權 class 開始問。**第一個 `pick_next_task` 回傳非 NULL 的 class 就贏**，直接 return，下面的 class 連問都不問。這就是優先鏈「絕對優先」語意的實作：高 class 有 task，低 class 永遠輪不到。

> **`idle_sched_class` 保證有答案**：迴圈最後一定會走到 idle class，而 idle class 的 `pick_next_task` **一定**回傳那顆 CPU 的 idle thread（`rq->idle`）。所以 `pick_next_task` 永遠不會回 NULL——最壞情況就是「沒事幹，跑 idle」。這就是為什麼函式尾巴那個 `BUG()` 理論上到不了。

## 排程什麼時候被觸發？

`__schedule()` 不會自己跑，它是**被觸發**的。理解「有哪些觸發點」比理解 `__schedule` 內部更重要——因為這決定了「一個 task 什麼時候會失去 CPU」。分兩大類：

### 1. Voluntary（主動）：task 自己讓出 CPU

task 執行到某個「我現在無事可做，要等某個事件」的點，主動呼叫 `schedule()`（`kernel/sched/core.c`，內部包一層呼叫 `__schedule`）：

```c
// 典型的「睡到某條件成立」模式
set_current_state(TASK_INTERRUPTIBLE);  // 我要睡了，標記非 running 狀態
if (!condition)
    schedule();                          // 主動讓出 CPU，__schedule 會把我 dequeue
__set_current_state(TASK_RUNNING);       // 被喚醒後醒來
```

這裡的關鍵是**先改狀態、再呼叫 schedule**：`__schedule` 看到 `prev` 不是 `TASK_RUNNING`，就把它從 rq 移除（deactivate），它就不會再被挑到，直到有人 `wake_up` 它、把它重新 enqueue。等 I/O、等鎖、`msleep`、`wait_event` 全走這條路。

### 2. Preemptive（被搶）：kernel 強迫 task 讓出

task 沒打算讓，但 kernel 決定該換人了。這靠一個標記位 **`TIF_NEED_RESCHED`**（thread info flag）驅動：

```
   某個事件發生 → 設 current 的 TIF_NEED_RESCHED 旗標
        │          （只是「打個記號」，不是立刻切換）
        │
        ▼
   走到一個「搶佔檢查點」→ 檢查 TIF_NEED_RESCHED 有沒有被設
        │
        ▼
   有被設 → 呼叫 schedule() → __schedule() 真正換人
```

**設旗標的常見來源**：

- **scheduler tick**：週期性時鐘中斷（`sched_tick`，舊名 `scheduler_tick`，在 `kernel/sched/core.c`）每個 tick 呼叫 `curr->sched_class->task_tick(...)`。對 fair class 而言，`task_tick_fair` 會判斷 curr 是不是已經跑太久、該讓給別人，是的話就設 `TIF_NEED_RESCHED`。**這是分時（time-slicing）的來源**——沒有它，一個 CPU-bound 迴圈會一直霸佔 CPU。

- **wake up**：`try_to_wake_up`（`kernel/sched/core.c`）把一個 task 喚醒、enqueue 進某顆 CPU 的 rq 後，會呼叫該 class 的 `wakeup_preempt` 判斷「這個剛醒的 task 該不該搶佔正在跑的 curr」。該搶就設目標 CPU 的 `TIF_NEED_RESCHED`（若是別顆 CPU，還會送一個 reschedule IPI 去戳它）。這就是為什麼你敲鍵盤、對應的 handler task 被喚醒後，能很快搶下 CPU 回應你。

- 其他：改 task 優先權（`sched_setscheduler`）、task 從一顆 CPU 遷移過來、負載均衡等，都可能設這個旗標。

**檢查旗標的搶佔檢查點（preemption point）**：

- **中斷/例外返回時**：中斷處理完、準備返回被打斷的程式碼前，kernel 檢查 `TIF_NEED_RESCHED`。若返回的是 user space，或返回 kernel space 且 `CONFIG_PREEMPT` 開啟，就在這裡呼叫 schedule。**這是 preemptive 排程最主要的實際切換點**——tick 中斷設了旗標，中斷返回時就順勢換人。
- **syscall 返回 user space 前**。
- **主動重新啟用搶佔時**（`preempt_enable()`）：若計數歸零且旗標被設，立刻排程。
- **`cond_resched()`**：kernel 內長迴圈裡手動插的「要換人的話這裡讓一下」的禮讓點。

> **6.x 的搶佔模型**：kernel 有多種搶佔設定——`PREEMPT_NONE`（server，kernel 內不搶佔）、`PREEMPT_VOLUNTARY`、`PREEMPT`（低延遲），6.12 還有 `PREEMPT_DYNAMIC`（開機時可切換）；`PREEMPT_LAZY`（給 SCHED_NORMAL 用的延遲搶佔，減少不必要的 context switch）則是 **6.13** 才併入，v6.12 尚無。細節在 `kernel/Kconfig.preempt`。這章你只要抓住「旗標 + 檢查點」這個兩階段機制；哪些檢查點生效由搶佔模型決定。

## 動手：用 gdb 看 __schedule 被誰呼叫

把 Ch 0 的 QEMU + gdb 環境開起來，這次我們停在排程器的心臟上，親眼看它被觸發。

```gdb
(gdb) target remote :1234
(gdb) source vmlinux-gdb.py
(gdb) break __schedule
(gdb) continue
```

QEMU 裡的 kernel 一定很快就會停下來——排程每秒發生太多次了。看是誰呼叫的：

```gdb
(gdb) backtrace
#0  __schedule (sched_mode=...) at kernel/sched/core.c:...
#1  schedule () at kernel/sched/core.c:...
#2  ...                        ← 往上看是 voluntary（等待/睡眠）還是中斷返回路徑
```

看**這顆 CPU 的 rq** 和**現在正在跑的 task**：

```gdb
(gdb) print $lx_per_cpu(runqueues, 0)          # CPU 0 的 rq（lx_ 來自 vmlinux-gdb.py）
(gdb) print $lx_current().comm                  # 現在正在跑的 task 名字
(gdb) print $lx_current().sched_class           # 它屬於哪個 class
```

停在 `pick_next_task`，看它挑出誰、屬於哪個 class：

```gdb
(gdb) break pick_next_task
(gdb) continue
(gdb) finish                                     # 跑完 pick_next_task，看回傳值
Run till exit from #0  pick_next_task ...
$1 = (struct task_struct *) 0xffff...            # 被挑中的 next task
(gdb) print ((struct task_struct *)$1)->comm
```

**要看清楚 voluntary vs preemptive 的差別**：分別在這兩處下條件中斷點，比較 backtrace——一個上游是 `schedule()`（主動），一個上游是中斷返回路徑（被搶）。

另一條不用 gdb 的觀測路徑，是讀 kernel 已經幫你統計好的排程數據：

```
/ # cat /proc/schedstat        # 每顆 CPU 的排程統計（run 次數、等待時間等）
/ # cat /proc/<pid>/sched      # 某個 task 的 vruntime、切換次數、等待延遲
```

`/proc/schedstat`（由 `CONFIG_SCHEDSTATS` 提供，源碼在 `kernel/sched/stats.c`）每一行對應一顆 CPU，欄位包含這顆 CPU 上 `schedule()` 被呼叫幾次、有多少次真的換了 task、task 在 rq 裡總共等了多久。想更細，用 ftrace 的 `sched_switch` tracepoint（Ch 51 主題）能逐一印出「誰換成誰、什麼時候」——這是實務上分析排程延遲最直接的工具，你在 `bpf` 課裡從使用者視角用過它，現在你知道它插在 `__schedule` 的哪個位置。

## 對比與取捨

| 設計選擇 | 這個方案 | 替代方案 | 為什麼 kernel 選這個 |
|---|---|---|---|
| 策略如何組織 | 分層 scheduler class（本章） | 單一巨型排程函式 | 每種策略獨立實作，加新 policy 不動核心；框架與演算法解耦 |
| class 順序 | 編譯期 linker section 定死 | 執行期比 priority 數字 | 順序永不變，遍歷是指標++，零執行期開銷 |
| runqueue 粒度 | per-CPU，各有 rq lock | 全域單一 run queue | 消滅跨核鎖爭用，可擴展到上百核；代價是要另做負載均衡（Ch 15） |
| 搶佔如何做 | 設 `TIF_NEED_RESCHED` 旗標 + 延後到檢查點切換 | 事件發生當下立刻切換 | 中斷 context 裡不能直接排程；旗標讓切換發生在安全的檢查點 |
| 挑 task 的常見情況 | fast path 直接問 fair | 每次都跑完整 class 迴圈 | 99% task 是 fair，跳過空的上層 class 省開銷 |

## 踩雷集錦

1. **以為 `__schedule` 裡在決定「該給誰多少時間」**——不。`__schedule` 只是仲裁者，它問各 class「你有沒有 task」、切過去，如此而已。「該給誰多少 CPU 時間」「誰的 vruntime 該長多少」這種**演算法**在各 class 自己的 `pick_next_task_*` / `task_tick_*` 裡（fair 在 Ch 12/13）。框架和演算法是兩件事，別混。

2. **以為設了 `TIF_NEED_RESCHED` 就立刻換人**——不。設旗標只是「打記號」，真正的 context switch 發生在下一個**搶佔檢查點**（中斷返回、syscall 返回、`preempt_enable` 等）。中間可能還跑了幾百條指令。這也是為什麼在 `PREEMPT_NONE` 的 kernel 裡，一段沒有 `cond_resched` 的長 kernel 迴圈能延遲排程很久——它根本沒經過檢查點。

3. **以為 rt task 和 fair task 會「公平地」分 CPU**——不。class 是**絕對優先**，不是加權。只要 rt class 手上有 runnable task，fair class 一個都跑不到。一個寫爛的 `SCHED_FIFO` 無窮迴圈能把整顆 CPU（甚至整台機器）餓死——這是 real-time 程式最經典的地雷（kernel 有 RT throttling 當安全網，但別依賴它）。

4. **以為 runqueue 是全系統一個**——不。每顆 CPU 一份 `struct rq`。「一個 task runnable」永遠是「在某一顆 CPU 的 rq 裡 runnable」。跨 CPU 看待 task 分佈、把 task 搬來搬去，是負載均衡（Ch 15）的事，不是 `__schedule` 的事——`__schedule` 只管**這一顆** CPU 的 rq。

5. **把 `schedule()` 和 `__schedule()` 搞混**——`schedule()` 是給 kernel 程式碼呼叫的公開入口（voluntary 路徑用它），它負責處理搶佔計數、然後在迴圈裡呼叫 `__schedule()`。`__schedule()` 是真正幹活的內部函式，preemption 路徑會直接走 `preempt_schedule`/`preempt_schedule_irq` 進到它。你下 gdb 中斷點通常下在 `__schedule`。

## 進階：再往深一層

- **`pick_next_task` 的兩種簽名**：v6.12 為了支援 **core scheduling**（對付 SMT 側信道，把同一 core 的兩個 hyperthread 排在一起管），`pick_next_task` 的介面比十年前複雜。上面給的是概念版；讀源碼會看到它和 `put_prev_task`、`set_next_task` 三者如何配合處理「prev 收尾、next 上場」的細節。抓住主幹即可，別被 core sched 的分支帶偏。

- **`sched_class` 為什麼是 `const`**：class 的函式指標表在編譯期就固定，執行期絕不改。宣告成 `const` 讓它進唯讀 section，任何試圖改寫它的行為（包括被攻擊者利用來劫持排程流程）都會觸發保護。這是 kernel 對「函式指標表」這類高價值攻擊目標的常規加固，和 `kernel_pwn` 課裡你研究的那些 hijack 手法正好對著看。

- **面試常問**：「Linux 怎麼同時服務即時任務和一般任務？」——答 scheduler class 的絕對優先鏈（dl > rt > fair）。「為什麼多核排程能擴展？」——答 per-CPU runqueue 消滅全域鎖，配負載均衡搬 task。「preemption 怎麼實作的？」——答 `TIF_NEED_RESCHED` 旗標 + 搶佔檢查點的兩階段機制，而不是立即切換。能把這三題連起來講，就抓住這章了。

- **RT throttling / deadline server**：純 `SCHED_FIFO` 霸佔 CPU 的問題，kernel 用 RT bandwidth throttling 緩解（限制 rt task 每個週期最多用多少 CPU）。v6.12 前後還引入了 fair 的 **deadline server** 機制，讓 fair task 在被 rt 長期餓死時能借 dl class 的頻寬保底跑一點。這說明「絕對優先」在工程上其實有安全網，但機制細節超出本章範圍。

## 動手練習

1. **追一次 voluntary schedule**：gdb `break __schedule`，在 QEMU 的 shell 裡跑一個會睡的指令（如 `sleep 1`），看 backtrace 上游是不是 `schedule()`。再 `print $lx_current().state`（或看 `__state` 欄位），確認呼叫 schedule 前 task 已被標成非 `TASK_RUNNING`。

2. **抓一次 preemptive schedule**：`break sched_tick`（或 `scheduler_tick`，看你的樹用哪個名），觀察它呼叫 `task_tick`；再在 `__schedule` 停下，比較 backtrace——這次上游應該是中斷返回路徑而非 `schedule()`。把兩種 backtrace 並排，你就親眼分清了 voluntary 和 preemptive。

3. **看 class 優先鏈生效**：`break pick_next_task`、`finish` 看回傳的 task 的 `sched_class` 指標。正常系統上你會反覆看到 `fair_sched_class`。挑戰：在 QEMU 裡用 `chrt -f 50 yes > /dev/null &` 起一個 `SCHED_FIFO` task，再觀察 `pick_next_task` 是不是開始回 `rt_sched_class` 的 task、fair task 被餓住（`yes` 會把那顆 CPU 吃滿）。用完記得 `kill` 它。

4. **讀 `/proc/schedstat` 的變化**：連續 `cat /proc/schedstat` 兩次、相隔幾秒，看某顆 CPU 那行的「schedule 呼叫次數」漲了多少。再跑個 busy loop，重看，感受排程頻率的變化。對照 `/proc/<pid>/sched` 看單一 task 的等待延遲。

## 本章重點整理

- 排程器是**分層**架構：`struct sched_class`（`kernel/sched/sched.h`）是函式指標介面，五個 class 按 **stop > dl > rt > fair > idle** 的編譯期固定優先鏈排列，`__schedule` 是不含演算法的仲裁者。
- runqueue 是 **per-CPU** 的 `struct rq`（`kernel/sched/sched.h`），內含各 class 的子 runqueue（`cfs_rq`/`rt_rq`/`dl_rq`）；per-CPU 消滅全域鎖爭用、換來需另做負載均衡（Ch 15）。
- `pick_next_task`（`kernel/sched/core.c`）按 class 優先鏈由高到低問，**第一個有 task 的 class 贏**；fast path 為「全是 fair task」的常見情況直接問 fair。
- 排程分 **voluntary**（task 主動 `schedule()` 讓出）和 **preemptive**（設 `TIF_NEED_RESCHED` 旗標、在搶佔檢查點才真正切換）兩條路——這章是框架，Ch 12/13 是 fair class 的演算法，Ch 14 是 context switch 的下半場。

## 自我檢核

- [ ] 不看筆記，能畫出 scheduler class 的優先鏈（哪五個、誰高誰低），並說出各服務什麼 policy
- [ ] 能解釋 `struct sched_class` 為什麼是「C 版的 vtable」，以及 task 怎麼透過 `sched_class` 指標分派
- [ ] 能說出為什麼 runqueue 是 per-CPU 而非全域，這個選擇換來什麼、代價是什麼
- [ ] 能完整描述 `pick_next_task` 如何按優先鏈挑 task，以及 fast path 為什麼存在
- [ ] 面試被問「Linux preemption 怎麼實作」，能講出 `TIF_NEED_RESCHED` 旗標 + 搶佔檢查點的兩階段機制，並區分它和 voluntary schedule
- [ ] 能用 gdb 停在 `__schedule` / `pick_next_task`，並從 backtrace 分辨 voluntary 與 preemptive

## 延伸閱讀

### 官方文件

- **[Documentation/scheduler/sched-design-CFS.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-design-CFS.html)** 與同目錄的 `sched-rt-group.rst`、`sched-deadline.rst`
  - **讀哪裡**：先讀 sched-design 開頭關於 scheduler class 與 runqueue 的架構描述（演算法細節留到 Ch 12/13 再回來）
  - **和本章關聯**：這是各 class 的官方設計說明，本章講框架、它們講各 class 內部

- **[Documentation/scheduler/sched-domains.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-domains.html)**
  - **讀哪裡**：概覽即可
  - **能學到什麼**：per-CPU rq 之上的 scheduling domain 結構，是 Ch 15 負載均衡的前置知識；讀完你會懂為什麼 per-CPU rq 需要一層階層來組織

### 原始碼

- **[kernel/sched/core.c 的 `__schedule` / `pick_next_task`（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/kernel/sched/core.c)** — Bootlin Elixir
  - **讀哪裡**：搜 `__schedule`、`pick_next_task`、`schedule`、`try_to_wake_up`、`sched_tick`
  - **怎麼讀**：對照本章的流程圖讀，重點看「拿 rq lock → 處理 prev → pick next → context_switch」的骨架，先略過 core scheduling 的分支
- **[kernel/sched/sched.h 的 `struct sched_class` / `struct rq`（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/kernel/sched/sched.h)** — Bootlin Elixir
  - **讀哪裡**：`struct sched_class`、`struct rq`、`for_each_class`、`DEFINE_SCHED_CLASS`
  - **和本章關聯**：本章所有結構定義的出處，配 Elixir 的「跳到定義/找呼叫點」邊讀邊追

### 書籍

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly）
  - **讀哪裡**：Process Scheduling 那章的「data structures」與「schedule() 函式」小節
  - **注意**：講的是 2.6 早期（`O(1)` scheduler，還沒有 CFS/EEVDF），**演算法已過時**，但「runqueue 資料結構、schedule 的骨架、preemption 機制」這些框架概念仍有參考價值。細節一律以 v6.12 源碼為準

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley）
  - **讀哪裡**：Process Scheduling 章的「Scheduler Classes」與「The Scheduler Entry Point」兩節
  - **為什麼讀**：對 scheduler class 分層與 `schedule()` 進入點的解釋最好懂，是本章框架概念的最佳白話補充；同樣注意書齡，演算法看 Ch 12/13

框架搭好了：你知道排程是「按 class 優先鏈問、per-CPU rq 裡挑、旗標加檢查點觸發切換」。但佔了 99% task 的 `fair_sched_class` 到底**怎麼**決定「這顆 CPU 上的一般 task 誰先跑」？下一章進到 CFS，看它用 vruntime 和紅黑樹把「完全公平」這件事做出來。

→ [Ch 12 CFS：vruntime 與紅黑樹](./12-cfs.md)
