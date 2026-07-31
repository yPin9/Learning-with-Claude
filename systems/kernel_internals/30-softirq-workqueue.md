# Ch 30 — softirq、tasklet、workqueue

> **目標**：搞懂 Linux 三種「延後工作（deferred work）」機制——softirq、tasklet、workqueue——各自跑在什麼 context、能不能睡、並行度如何，並能在寫驅動時準確選對一個。學完你能在 workqueue 裡合法 `msleep`、在 tasklet 裡 `msleep` 觸發 kernel BUG，親眼看見「能睡 vs 不能睡」這條線。

Ch 29 我們把中斷處理切成 **top half（hardirq handler）** 和 **bottom half（延後處理）**：top half 在關中斷、不能睡的 hardirq context 裡跑，只做最緊急的事（ack 硬體、抓走資料），剩下的耗時工作丟給 bottom half 晚點做。這章要回答 Ch 29 沒展開的問題：**「晚點做」到底怎麼做？** Linux 給了三個答案，而選錯一個的代價，從效能退化到直接 panic 都有。

## 為什麼需要延後機制？

回到 Ch 29 的核心矛盾。硬體中斷來的時候，CPU 跳進 hardirq handler，此時：

- **關著（部分）中斷**——處理越久，其他中斷被擋越久，延遲（latency）越差
- **不能睡（見 Ch 2）**——你不在任何 process context 上，沒有可以被排程器換下去的 `task_struct` 語意，一睡就是災難
- **借用被中斷者的 stack**——空間有限

所以 handler 必須短。但網卡收到一個封包，光靠 top half 抓進 ring buffer 不夠——還要走協定堆疊、查路由、丟給 socket，這些是幾百上千個 cycle 的工作。硬塞進 hardirq，整個系統的中斷延遲會爆。

解法就是 Ch 29 講的 bottom half：**hardirq 只登記「有事要做」，真正的工作延後到「中斷開回來、比較安全」的時機再跑**。問題是「延後到哪個時機、跑在什麼 context」有三種不同的取捨，於是有了三種機制。它們的差別可以濃縮成三個問題：

1. **在什麼 context 跑？**（softirq context / process context）
2. **能不能睡？**（能睡 = 可以拿 mutex、可以配大記憶體、可以等 I/O）
3. **並行度如何？**（同一個 handler 會不會在多 CPU 同時跑）

把這三個問題答清楚，你就懂了這一章的全部。

## 先建立直覺：三條線

```
                        能睡?   跑在哪                     同一 handler 多 CPU 並行?
  ┌──────────────┐
  │  softirq     │      ✗      softirq context           ✓ 會（要自己同步）
  │  (最底層)     │             (irq_exit / ksoftirqd)     固定 10 種，改 kernel 才能加
  └──────────────┘
         ▲ tasklet 建在它之上（TASKLET_SOFTIRQ）
  ┌──────────────┐
  │  tasklet     │      ✗      softirq context           ✗ 不會（同一 tasklet 序列化）
  │  (驅動用)     │             (同 softirq)               動態建立，但正在被淘汰
  └──────────────┘

  ┌──────────────┐
  │  workqueue   │      ✓      process context           看 workqueue 設定
  │  (最靈活)     │             (kernel thread / kworker)  能拿 mutex、能配大記憶體、能等 I/O
  └──────────────┘
```

一句話記住這張圖：**softirq 和 tasklet 都在 softirq context 跑、都不能睡；只有 workqueue 跑在 kernel thread（process context）上，所以只有它能睡。** 這條「能不能睡」的線是三者最本質的分界，也是你選機制時第一個要問的問題——**你的延後工作需不需要睡（拿 mutex、`kmalloc(GFP_KERNEL)`、等硬體回應）？** 需要就直接選 workqueue，不用再想。

> 「睡（sleep）」在 kernel 是精確術語：讓出 CPU、把自己標成非 RUNNING、等某個條件滿足再被喚醒（見 Ch 2 對 context 的定義、Ch 26 的 mutex/completion）。softirq context 沒有一個「自己」可以被換下去，所以睡下去等於卡死整條中斷路徑。

## softirq：最底層、效能最高的延後機制

softirq 是三者裡最底層的一個，源碼在 `kernel/softirq.c`。它的種類是**編譯期固定死的**——`include/linux/interrupt.h` 裡一個 enum：

```c
enum {
    HI_SOFTIRQ = 0,      /* 高優先權 tasklet 走這條 */
    TIMER_SOFTIRQ,       /* timer callback（Ch 32） */
    NET_TX_SOFTIRQ,      /* 網路送包（Ch 45） */
    NET_RX_SOFTIRQ,      /* 網路收包 NAPI（Ch 44） */
    BLOCK_SOFTIRQ,       /* block I/O 完成（Ch 36） */
    IRQ_POLL_SOFTIRQ,
    TASKLET_SOFTIRQ,     /* 一般 tasklet 走這條 */
    SCHED_SOFTIRQ,       /* 排程器 load balance（Ch 15） */
    HRTIMER_SOFTIRQ,     /* 高解析度 timer（Ch 32） */
    RCU_SOFTIRQ,         /* RCU callback（Ch 27） */
    NR_SOFTIRQS
};
```

十種，你數得完。**這就是為什麼一般驅動不直接用 softirq**：種類固定，要新增一種得改 kernel 源碼、重編、你的 out-of-tree 模組加不進去。softirq 是留給**核心子系統**的高效能通道——網路（Ch 44/45）、block（Ch 36）、timer（Ch 32）、RCU（Ch 27）這種每秒觸發幾十萬次、對延遲極度敏感的路徑才值得動用。驅動想延後工作，用建在 softirq 之上的 tasklet 或 workqueue。

### softirq 怎麼註冊、怎麼觸發

註冊一種 softirq 的 handler 用 `open_softirq(nr, handler)`（`kernel/softirq.c`）；子系統在初始化時呼叫，例如網路收包在 `net/core/dev.c` 裡 `open_softirq(NET_RX_SOFTIRQ, net_rx_action)`。

要「排一次 softirq」用 `raise_softirq(nr)`（或已關中斷版本 `raise_softirq_irqoff`）。它做的事出奇地輕：在 **per-CPU 的一個 bitmask（`__softirq_pending`）** 上把對應的 bit 設起來，標記「本 CPU 有這種 softirq 待處理」。注意這裡的 per-CPU（見 Ch 7）——每個 CPU 有自己的 pending mask，raise 只影響當前 CPU，這是 softirq 高效能、低鎖競爭的關鍵。

```c
/* 概念示意：raise 只是設一個 per-CPU bit，極快 */
raise_softirq(NET_RX_SOFTIRQ);
   └─► or_softirq_pending(1UL << NET_RX_SOFTIRQ);  /* 設 this_cpu 的 pending bit */
```

### softirq 什麼時候真的執行？

設了 bit 只是「登記」，真正跑 handler 在兩個時機，這是 softirq 機制的靈魂：

```
  時機一：中斷返回時（最常見的路徑）
  ─────────────────────────────────
  硬體中斷 ──► do_IRQ / hardirq handler（top half，raise 了某個 softirq）
                   │
                   ▼
              irq_exit()  ◄── 中斷即將返回
                   │
                   ├─ in_interrupt()? 還在更外層中斷裡 ─► 不跑，等最外層再說
                   │
                   ▼ 是最外層中斷，且有 pending softirq
              invoke_softirq() ──► __do_softirq()
                   │
                   ▼
              迴圈掃 __softirq_pending 的每個 bit，逐一呼叫對應 handler
              （此時已開中斷，但仍是 softirq context：不能睡）
                   │
              跑太久了？（超過 MAX_SOFTIRQ_TIME 或 restart 次數上限）
                   │
                   ▼ 是
              wakeup_softirqd() ──► 喚醒本 CPU 的 ksoftirqd/N kernel thread
                                    把剩下的 softirq 丟給它，讓 CPU 先喘口氣

  時機二：ksoftirqd（per-CPU kernel thread，見 Ch 7）
  ─────────────────────────────────
  ps aux | grep ksoftirqd  ──► 每顆 CPU 一個：[ksoftirqd/0] [ksoftirqd/1] ...
  當 softirq 多到 irq_exit 路徑處理不完（例如網路被打爆），
  就在這個「一般排程的 kernel thread」裡繼續跑 __do_softirq()。
  它會受排程器約束，不會餓死 user space——這是防 softirq 風暴的安全閥。
```

`__do_softirq()`（`kernel/softirq.c`）是核心：它讀 per-CPU 的 `__softirq_pending`，從低 bit 到高 bit 依序跑 handler。跑的時候會**開回中斷**（所以 softirq handler 執行中可以被 hardirq 打斷，但打斷它的 hardirq 不會就地再跑 softirq，會等回到 `__do_softirq` 的迴圈），但**不進入 process context、不能睡**。

`irq_exit()` 路徑跑 softirq 有上限——`MAX_SOFTIRQ_TIME`（約 2ms）或重跑次數。超過就把剩下的甩給 `ksoftirqd`。這個設計是為了**公平**：如果 softirq 無限在中斷返回路徑上跑，user space 會被餓死（想像網路一直收包，你的 shell 永遠搶不到 CPU）。ksoftirqd 是普通優先權的 kernel thread，受排程器管，於是 softirq 風暴時系統還能喘氣。

### 並行：同一種 softirq 會在多 CPU 同時跑

這是 softirq 相對 tasklet 最「危險」也最「快」的特性：**同一種 softirq（例如 NET_RX_SOFTIRQ）可以同時在 CPU0 和 CPU1 上跑各自的 handler**。因為每 CPU 獨立 raise、獨立執行。好處是網路收包能真正並行、吃滿多核；代價是 **handler 自己要處理並行**——共享資料得用 per-CPU 變數（Ch 7）或 spinlock（Ch 25）保護。softirq 把同步的責任丟給了子系統作者，換取極致吞吐。這也是為什麼 softirq 難寫、只留給核心子系統。

## tasklet：建在 softirq 之上的動態介面

驅動想要 softirq 的低開銷，又不想改 kernel、不想自己處理多 CPU 並行——**tasklet** 就是為這個場景設計的。它整個建在 `TASKLET_SOFTIRQ`（和高優先權的 `HI_SOFTIRQ`）之上，源碼還是在 `kernel/softirq.c`。

用法（`include/linux/interrupt.h` 的 `struct tasklet_struct`）：

```c
#include <linux/interrupt.h>

static void my_tasklet_fn(struct tasklet_struct *t)   /* 5.9 起用新版簽名 */
{
    pr_info("tasklet running, in_interrupt=%lu, cpu=%d\n",
            in_interrupt(), smp_processor_id());
    /* 這裡不能睡：不能 mutex_lock、不能 kmalloc(GFP_KERNEL)、不能 msleep */
}

static DECLARE_TASKLET(my_tasklet, my_tasklet_fn);    /* 靜態宣告 */

/* 在 hardirq handler（top half）裡排一次： */
tasklet_schedule(&my_tasklet);
```

`tasklet_schedule()` 把這個 tasklet 掛到本 CPU 的 tasklet 串列上，然後 `raise_softirq(TASKLET_SOFTIRQ)`。稍後 `tasklet_action`（`TASKLET_SOFTIRQ` 的 handler）被 softirq 路徑呼叫時，會走過串列逐一執行。

### tasklet 相對 softirq 的關鍵優勢：序列化

tasklet 給你 softirq 給不了的保證：**同一個 tasklet 絕對不會在兩顆 CPU 上同時跑。** 就算你在 CPU0 和 CPU1 各 `tasklet_schedule(&my_tasklet)` 一次，kernel 用 tasklet 的狀態旗標（`TASKLET_STATE_SCHED` / `TASKLET_STATE_RUN`）保證它序列化執行。這意味著**同一個 tasklet 的 handler 內，你不需要對「自己和自己並行」上鎖**——省掉一整類同步麻煩，這是 tasklet 好用的核心理由。

（注意：不同的兩個 tasklet 之間沒有這保證，它們可以並行。序列化只針對「同一個 tasklet」。）

### 認識論誠實：tasklet 正在被淘汰

這點必須講清楚，免得你在新驅動裡誤用。**tasklet 是老機制，kernel 社群近年一直在推動淘汰它。** 理由：

- tasklet 的軟體中斷語意（不能睡、在 softirq context）今天大多能被 **threaded IRQ（Ch 31）** 或 **workqueue** 取代，而後者語意更清楚
- tasklet 的 API 歷史包袱多（舊版 `data` 傳參、`DECLARE_TASKLET` vs `_DISABLED` 等），2020 年還做過一次全樹 API 遷移把 callback 簽名從 `unsigned long data` 改成 `struct tasklet_struct *t`
- LWN 上有明確的「淘汰 tasklet」討論串，多個子系統已把 tasklet 改成 workqueue 或 threaded IRQ

**結論**：讀舊驅動你會大量遇到 tasklet，要看得懂；但**寫新程式碼，除非有明確的低延遲、不能睡的理由，優先考慮 workqueue（要睡）或 threaded IRQ（Ch 31，要低延遲又想在 thread 裡跑）**。tasklet 不是錯，只是它的生態位越來越窄。

## workqueue：跑在 kernel thread，所以能睡

前兩者都困在 softirq context、不能睡。**workqueue 是唯一跑在 process context 的延後機制**——它的工作在一個真正的 kernel thread（你 `ps` 看到的 `[kworker/...]`）裡執行，有自己的 `task_struct`，可以被排程器換下去。**所以它能睡。** 這是 workqueue 存在的全部理由，也是選它的唯一判準：

> 你的延後工作要 **拿 mutex（Ch 26）**、要 **`kmalloc(GFP_KERNEL)` 配可能觸發回收的記憶體（Ch 6/22）**、要 **等硬體回應或做 I/O**、要 **呼叫任何可能睡的函式**——**用 workqueue。**

基本用法（`include/linux/workqueue.h`）：

```c
#include <linux/workqueue.h>

static struct work_struct my_work;

static void my_work_fn(struct work_struct *work)
{
    /* 這裡是 process context，可以睡！ */
    mutex_lock(&some_mutex);            /* 合法 */
    msleep(100);                        /* 合法，證明能睡 */
    kmalloc(1 << 20, GFP_KERNEL);       /* 合法，可以配大記憶體、可觸發 reclaim */
    mutex_unlock(&some_mutex);
}

/* 初始化（通常在 probe / module_init）： */
INIT_WORK(&my_work, my_work_fn);

/* 在 hardirq handler 或任何地方排一次工作到系統共享 workqueue： */
schedule_work(&my_work);
```

`schedule_work()` 把 work 丟到**系統共享 workqueue（`system_wq`）**，稍後某個 kworker thread 撿起來執行。多數驅動用共享的就夠了。

### 何時要自建 workqueue

系統共享 workqueue 是全系統共用的，如果你的 work 會跑很久、或會睡很久，可能拖累別人的 work。這時用 `alloc_workqueue()`（`kernel/workqueue.c`）建自己的：

```c
struct workqueue_struct *my_wq;

my_wq = alloc_workqueue("my_wq", WQ_UNBOUND | WQ_MEM_RECLAIM, 0);
queue_work(my_wq, &my_work);           /* 排到自己的 wq，而非系統共享的 */
/* ... 卸載時： */
destroy_workqueue(my_wq);
```

幾個重要 flag（`include/linux/workqueue.h`）：

- **`WQ_UNBOUND`**：worker 不綁定特定 CPU，排程器可把它放到任何 CPU——適合 CPU-heavy、長時間跑的 work（讓排程器做負載平衡）；不加則預設綁 CPU（cache 友善、低延遲）
- **`WQ_MEM_RECLAIM`**：保證這個 wq 在記憶體吃緊時仍有一個 rescuer thread 能推進工作——如果你的 work 出現在記憶體回收路徑（Ch 22）上，**必須**加，否則可能 deadlock（要配記憶體才能前進，但前進需要配記憶體）
- **`WQ_HIGHPRI`**：worker 用高優先權排程
- **`WQ_FREEZABLE`**：系統休眠（suspend）時凍住

### CMWQ：worker 不是一個 work 一個 thread

你可能以為每個 workqueue 背後有一條專屬 thread——**不是**。2010 年後 Linux 用的是 **CMWQ（Concurrency Managed Workqueue，並行受控工作佇列）**，源碼 `kernel/workqueue.c`。心智模型：

```
   work 們（來自各驅動 / 子系統）
        │  queue_work / schedule_work
        ▼
   ┌─────────────────────────────────────────┐
   │  workqueue（邏輯佇列，可以有很多個）        │  ← alloc_workqueue 建的是「這層」
   └─────────────────────────────────────────┘
        │  work 最終流向共享的 worker pool
        ▼
   ┌─────────────────────────────────────────┐
   │  worker pool（每 CPU 一組：normal + high）│  ← 真正的 kworker thread 住在這
   │   kworker/0:0  kworker/0:1  kworker/0:2 …│
   └─────────────────────────────────────────┘
        動態增減 worker：
        - 有 work 排隊且沒 worker 在跑 → 生一個
        - 某個 worker 睡著（blocking）  → 生另一個補上，維持並行
        - 閒置太久的 worker            → 回收掉
```

CMWQ 的洞見：**「邏輯佇列（workqueue）」和「執行資源（worker pool / thread）」解耦**。你 `alloc_workqueue` 建很多個邏輯佇列不會爆出很多 thread——它們共享每 CPU 的 worker pool。pool 根據負載動態調整 worker 數量：當一個 worker 因為睡著而卡住，pool 會生出另一個 worker 讓其他 work 繼續跑，維持「剛好夠用」的並行度。這解決了老式 workqueue「每個 wq 一條專屬 thread、thread 數爆炸、又容易 deadlock」的問題。

你平常不用管 pool，`INIT_WORK` + `schedule_work` 就好；但知道 `kworker/0:1` 這種名字是 pool 裡的 worker、不是「你的 work 的專屬 thread」，能讓你在 `ps` / `top` 看到一堆 kworker 時不慌。

### delayed_work：延後 + 定時

有時你要的不是「盡快延後執行」而是「N 毫秒後執行」——用 `struct delayed_work`（`include/linux/workqueue.h`），它把 work 和一個 timer（Ch 32）綁在一起：

```c
static struct delayed_work my_dwork;

INIT_DELAYED_WORK(&my_dwork, my_work_fn);
schedule_delayed_work(&my_dwork, msecs_to_jiffies(500));  /* 500ms 後跑 */
/* 取消： */
cancel_delayed_work_sync(&my_dwork);
```

底層就是一個 timer 到期時才把 work 排進 workqueue。定時輪詢硬體狀態、延遲重試這類需求常用它。timer 的機制（jiffies、`msecs_to_jiffies`）是 Ch 32 的主題。

## 底層機制：一次中斷觸發到三種延後的全景

把三條路徑疊在同一張圖上，看清 hardirq 之後工作怎麼分流：

```
   硬體中斷（IRQ 線拉高）
        │
        ▼
   ┌────────────────────────────────────────────┐
   │  top half：hardirq handler（Ch 29）          │  關中斷、不能睡、要快
   │  ack 硬體、抓資料，然後三選一登記延後工作：      │
   └────────────────────────────────────────────┘
        │ raise_softirq()      │ tasklet_schedule()  │ schedule_work()
        │ (核心子系統)          │ (驅動，序列化)       │ (要睡的工作)
        ▼                      ▼                     ▼
   set per-CPU pending    掛上 per-CPU tasklet   把 work 丟進 workqueue
        bit                    串列                 → 流向 worker pool
        │                      │                     │
        └──────┬───────────────┘                     │
               ▼                                      ▼
        irq_exit() → __do_softirq()            某個 kworker thread
        （softirq context，不能睡）              （process context，能睡）
               │  太多跑不完                          │
               ▼                                      ▼
        ksoftirqd/N（kernel thread，            mutex_lock / msleep /
         但仍在 softirq context 語意，不能睡）    kmalloc(GFP_KERNEL) 都合法
```

左中兩條（softirq / tasklet）最終都在 softirq context 執行、都不能睡；右邊（workqueue）走 kernel thread、能睡。**這張圖是這一章要你記住的全部**：同一個 hardirq handler，可以把不同性質的後續工作分別甩給這三條路。

## 動手：tasklet 能不能睡 vs workqueue 能睡

我們寫一個模組，用一個 timer 當「中斷來源的替身」（真中斷要接硬體，QEMU 裡不方便；timer 到期的 callback 也在類似 softirq context，見 Ch 32），到期時同時排一個 tasklet 和一個 work，兩邊都印出自己的 context，並在 work 裡故意 `msleep` 證明能睡。想看 tasklet 睡下去炸掉，把最後一段的註解拿掉。

```c
// defer_demo.c —— 對比 tasklet（不能睡）與 workqueue（能睡）
#include <linux/module.h>
#include <linux/interrupt.h>
#include <linux/workqueue.h>
#include <linux/timer.h>
#include <linux/delay.h>

static struct timer_list  trigger;
static struct work_struct  my_work;

/* ---- tasklet：softirq context，不能睡 ---- */
static void tasklet_fn(struct tasklet_struct *t)
{
    pr_info("[tasklet]  in_interrupt=%lu in_softirq=%lu cpu=%d\n",
            in_interrupt(), in_softirq(), smp_processor_id());
    /* 想看它爆炸，取消下一行註解：softirq context 睡下去會觸發 BUG（見 Ch 2） */
    // msleep(10);   /* BUG: scheduling while atomic */
}
static DECLARE_TASKLET(my_tasklet, tasklet_fn);

/* ---- workqueue：process context，能睡 ---- */
static void work_fn(struct work_struct *w)
{
    pr_info("[workqueue] in_interrupt=%lu cpu=%d, 準備睡 100ms...\n",
            in_interrupt(), smp_processor_id());
    msleep(100);                       /* 合法：我們在 kernel thread 裡 */
    pr_info("[workqueue] 睡醒了，process context 果然能睡\n");
}

/* timer 到期時：像 hardirq handler 一樣「登記延後工作」 */
static void trigger_fn(struct timer_list *t)
{
    pr_info("[trigger]  in_interrupt=%lu（timer callback 也不能睡）\n",
            in_interrupt());
    tasklet_schedule(&my_tasklet);     /* 排 tasklet */
    schedule_work(&my_work);           /* 排 work */
}

static int __init demo_init(void)
{
    INIT_WORK(&my_work, work_fn);
    timer_setup(&trigger, trigger_fn, 0);
    mod_timer(&trigger, jiffies + msecs_to_jiffies(500));   /* 500ms 後觸發 */
    pr_info("[init] 已排定 500ms 後觸發\n");
    return 0;
}

static void __exit demo_exit(void)
{
    del_timer_sync(&trigger);
    tasklet_kill(&my_tasklet);         /* 等 tasklet 跑完再走 */
    cancel_work_sync(&my_work);        /* 等 work 跑完再走 */
    pr_info("[exit] cleaned up\n");
}

module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("softirq/tasklet/workqueue context demo");
```

在 QEMU 裡（Ch 0 的環境）`insmod defer_demo.ko`，`dmesg` 應該看到：

```
[init] 已排定 500ms 後觸發
[trigger]  in_interrupt=... （timer callback 也不能睡）
[tasklet]  in_interrupt=... in_softirq=... cpu=0
[workqueue] in_interrupt=0 cpu=0, 準備睡 100ms...
[workqueue] 睡醒了，process context 果然能睡
```

注意 `[workqueue]` 那行 `in_interrupt=0`——它**不在**中斷/softirq context，而 tasklet 那行是非零。這一個 0 和非 0，就是「能睡」與「不能睡」的分野。

**現在弄壞它**：把 `tasklet_fn` 裡的 `// msleep(10);` 取消註解，重編、`insmod`。500ms 後 kernel 會噴：

```
BUG: scheduling while atomic: swapper/0/0/0x00000100
```

`scheduling while atomic` 就是「在不准睡的 atomic context 裡呼叫了會睡的函式」——`msleep` 想讓出 CPU，但 softirq context 沒有可以被換下去的「自己」，排程器一看 `in_atomic()` 為真就 BUG。這正是 Ch 2 講的 context 規則在你眼前爆炸。（開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 的話，很多「可能睡」的函式進入時就會 `might_sleep()` 提前示警，不用真睡到才 BUG——建議除錯 config 開著。）

### 看 /proc/softirqs

softirq 的實際觸發次數在 `/proc/softirqs`，每種、每 CPU 一欄：

```
$ cat /proc/softirqs
                    CPU0       CPU1
      HI:              2          0
   TIMER:          10293       9981
  NET_TX:              1          0
  NET_RX:           4412        330
   BLOCK:            187         44
 TASKLET:              5          1      ← 你的 tasklet 跑一次，這裡 +1
   SCHED:           8100       7742
 HRTIMER:              0          0
     RCU:           6621       6120
```

`insmod` 你的模組前後各 `cat` 一次，會看到 `TASKLET` 那列的計數增加。網路壓力測試時 `NET_RX` 會飆——那就是 Ch 44 收包路徑在 softirq 裡跑的直接證據。

## 對比與取捨

這張表是本章最該記住的東西——選延後機制時照著問：

| 面向 | softirq | tasklet | workqueue |
|---|---|---|---|
| **跑在什麼 context** | softirq context | softirq context | process context（kernel thread） |
| **能不能睡** | ✗ 不能 | ✗ 不能 | ✓ **能** |
| **同一 handler 多 CPU 並行** | ✓ 會（自己同步） | ✗ 不會（序列化） | 看設定（預設綁 CPU） |
| **誰可以用** | 核心子系統（種類固定） | 任何驅動（動態建立） | 任何驅動（動態建立） |
| **開銷 / 延遲** | 最低 | 低（softirq 之上薄薄一層） | 較高（要喚醒 thread、排程） |
| **典型用途** | 網路收送包、block 完成、timer、RCU | 舊驅動的中斷後半段 | 要睡、配大記憶體、拿 mutex、等 I/O |
| **源碼** | `kernel/softirq.c` | `kernel/softirq.c` | `kernel/workqueue.c` |
| **現況** | 核心基石，穩定 | **正在被淘汰**，優先改 workqueue/threaded IRQ | 主流首選 |
| **本課出現處** | Ch 44 NET_RX、Ch 36 block、Ch 32 timer、Ch 27 RCU | 本章 + 舊驅動 | Ch 21 writeback、驅動普遍 |

**決策樹**（背下來）：

1. 工作**要睡**（mutex / GFP_KERNEL / I/O / 等硬體）？→ **workqueue**，結束。
2. 不用睡，但你是**寫一般驅動**、要延後中斷後半段？→ 新程式碼優先 **threaded IRQ（Ch 31）**；沿用既有 tasklet 生態才用 tasklet。
3. 不用睡，而你在**改核心子系統**、需要極致吞吐與多核並行？→ **softirq**（並自己扛同步）。

## 踩雷集錦

1. **「延後工作」不等於「能睡」**。最常見的誤解：以為把工作丟給 bottom half 就脫離 hardirq 的限制、就能睡了。**錯**——softirq 和 tasklet 仍在 softirq context，一樣不能睡。只有 workqueue 換到了 process context 才解禁。要睡就必須是 workqueue，沒有例外。

2. **在 tasklet / softirq 裡呼叫任何「可能睡」的函式**。`mutex_lock`（Ch 26，會睡）、`kmalloc(GFP_KERNEL)`（Ch 6，可能觸發回收而睡）、`copy_to_user`（可能 page fault 而睡）、`msleep`——在 softirq context 裡全是地雷。要在這些 context 配記憶體用 `GFP_ATOMIC`（不睡、但可能失敗，見 Ch 6）；要鎖用 spinlock（Ch 25）。

3. **同一個 tasklet 序列化 ≠ 不用鎖**。tasklet 保證「同一個 tasklet 不並行」，但它和 hardirq handler、和其他 tasklet、和 process context 之間的共享資料**照樣要同步**。序列化只擋掉「自己和自己」這一種 race，別以為 tasklet 就是無鎖天堂。

4. **記憶體回收路徑上的 workqueue 忘了 `WQ_MEM_RECLAIM`**。如果你的 work 在低記憶體時要被執行來「釋放記憶體」，而 wq 沒有 rescuer（沒加 `WQ_MEM_RECLAIM`），可能陷入「要配記憶體生 worker → 但正是記憶體不夠 → 卡死」的 deadlock。回收路徑上的 wq 一律加這個 flag。

5. **以為每個 workqueue 有一條專屬 thread**。CMWQ 下 workqueue 是邏輯佇列，共享每 CPU 的 worker pool。建一堆 wq 不會爆一堆 thread；反過來，`ps` 看到一堆 `kworker` 也不代表洩漏——那是 pool 按負載動態伸縮的正常現象。

## 進階：再往深一層

- **softirq 與 preemption/-rt**：在主線 kernel，softirq 執行時 preemption 的行為受限；在 PREEMPT_RT（Ch 31）裡，softirq 被搬進 thread 執行以換取可預測的延遲，`ksoftirqd` 的角色會改變。這是即時系統的重點差異。
- **threaded IRQ（Ch 31）是 tasklet 的現代替代**：`request_threaded_irq()` 讓你把中斷處理的下半段直接放進一個專屬 kernel thread（能睡、優先權可調），語意比 tasklet 清楚，也是 -rt 的基礎。很多「該用 tasklet」的場景現在用它。
- **workqueue 的 flush 與生命週期**：`flush_work` / `flush_workqueue` 等 pending work 跑完；模組卸載時**必須** `cancel_work_sync` / `destroy_workqueue`，否則 work 還在 queue 裡而你的模組程式碼已被卸載 → 執行到野指標。這是驅動卸載 use-after-free 的經典來源。
- **面試常問**：「tasklet 和 workqueue 差在哪、什麼時候用哪個？」標準答案就是「context 與能不能睡」——tasklet 在 softirq context 不能睡、workqueue 在 process context 能睡，要拿 mutex / 配大記憶體 / 等 I/O 就 workqueue。能補一句「tasklet 正被淘汰、新程式碼傾向 threaded IRQ / workqueue」會加分。
- **`in_interrupt()` / `in_softirq()` / `in_task()`**：這些巨集（`include/linux/preempt.h`）查目前 context，除錯時很有用；但別把它們寫進正常邏輯來「判斷該不該上鎖」——正確做法是設計上就知道自己在哪個 context。

## 動手練習

1. **跑通 demo 並讀 `in_interrupt`**：載入上面的 `defer_demo.ko`，確認 dmesg 裡 tasklet 行的 `in_interrupt` 非零、workqueue 行為 0。用一句話解釋這個差異為什麼決定了「能不能睡」。
2. **弄壞它**：取消 tasklet 裡 `msleep` 的註解，觀察 `scheduling while atomic` BUG 的完整 backtrace。從 backtrace 認出它是從 `__do_softirq` → `tasklet_action` 一路下來的——你就親眼看到 tasklet 跑在 softirq 路徑上。
3. **自建 workqueue**：把 demo 的 `schedule_work` 改成用 `alloc_workqueue("demo_wq", WQ_UNBOUND, 0)` + `queue_work`，卸載時 `destroy_workqueue`。`ps aux | grep kworker` 對比前後，理解 CMWQ 為什麼不會因此多出一條固定 thread。
4. **觀測 softirq 壓力**：在 QEMU 裡對 kernel 灌網路流量（`ping -f` 或 `iperf`），連續 `cat /proc/softirqs`，看 `NET_RX` 飆升；再看 `top` 裡 `ksoftirqd` 是否吃 CPU——這就是 softirq 太多被甩給 ksoftirqd 的現場（銜接 Ch 44）。
5. **delayed_work**：把 demo 改用 `delayed_work` + `schedule_delayed_work`，讓 work 在排定後 2 秒才跑，驗證延遲。想一想這和 Ch 32 的 timer 是什麼關係。

## 本章重點整理

- 三種延後機制的本質差別只有三個問題：**跑在什麼 context、能不能睡、並行度如何**。答清楚就選對了。
- **softirq**（`kernel/softirq.c`）最底層、種類固定（10 種）、多 CPU 並行、不能睡，留給核心子系統（網路/block/timer/RCU）；`irq_exit` 跑不完就甩給 per-CPU 的 `ksoftirqd`。
- **tasklet** 建在 `TASKLET_SOFTIRQ` 之上，驅動可動態建立，同一 tasklet 序列化、不能睡——但**正在被淘汰**，新程式碼優先 workqueue / threaded IRQ。
- **workqueue**（`kernel/workqueue.c`）跑在 kernel thread（process context），是**唯一能睡**的一個；CMWQ 讓邏輯佇列與 worker pool 解耦，要睡 / 配大記憶體 / 拿 mutex 就選它。

## 自我檢核

- [ ] 不看筆記，能畫出三種延後機制在「context / 能否睡 / 並行度」三軸上的位置
- [ ] 能解釋為什麼 softirq 和 tasklet 不能睡，而 workqueue 能——關鍵在「有沒有一個 task_struct 可以被排程器換下去」
- [ ] 能說出一般驅動為什麼不直接用 softirq、而 tasklet 為什麼正被淘汰
- [ ] 面試被問「tasklet vs workqueue 怎麼選」，你能用「context + 能否睡 + 要不要拿 mutex/配記憶體」三句話答完
- [ ] 能解釋 `WQ_MEM_RECLAIM` 為什麼在記憶體回收路徑上是必須的
- [ ] 知道 CMWQ 下「workqueue ≠ 專屬 thread」，能說出 worker pool 如何動態伸縮

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/workqueue.rst](https://www.kernel.org/doc/html/latest/core-api/workqueue.html)**
  - **讀哪裡**：整篇。這是 CMWQ 的權威說明，`alloc_workqueue` 的每個 flag、worker pool 的設計理由都在這裡，作者是 CMWQ 的實作者 Tejun Heo
  - **和本章的關聯**：本章 workqueue 一節的底層機制以它為準；要在生產環境調 wq 參數時回來讀

- **[Documentation/core-api/genericirq.rst](https://www.kernel.org/doc/html/latest/core-api/genericirq.html)**
  - **讀哪裡**：談 top/bottom half 與 threaded IRQ 的段落
  - **能學到什麼**：把 Ch 29 的中斷框架和本章的延後機制、Ch 31 的 threaded IRQ 串起來的官方視角

### LWN 文章

- **[Software interrupts and realtime](https://lwn.net/Articles/520076/)** 及相關的 tasklet 淘汰討論串（在 LWN 搜 "tasklet"）
  - **為什麼讀**：本章「tasklet 正被淘汰」的判斷來自這些討論；想知道社群到底怎麼看 tasklet 的未來、替代方案為何是 threaded IRQ / workqueue，一手來源在這
  - **前提**：讀完本章與 Ch 29

- **[Concurrency-managed workqueues](https://lwn.net/Articles/403891/)** — Jonathan Corbet
  - **讀哪裡**：CMWQ 剛引入時的設計解說，比官方文件更帶「為什麼要這樣改」的脈絡
  - **能學到什麼**：老式 workqueue 的 thread 爆炸與 deadlock 問題，以及 CMWQ 如何用 worker pool 解決——理解本章 CMWQ 圖的背景

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 8 章「Bottom Halves and Deferring Work」
  - **這本書的定位**：把 softirq / tasklet / workqueue 三者放在一起講得最清楚的一章，本章的三分法承襲它的框架
  - **注意**：書較舊、講的是老式 workqueue（CMWQ 之前），workqueue 的內部實作以本章與官方 workqueue.rst 為準；softirq / tasklet 的概念仍準確

三種延後機制裡，tasklet 的接班人——把中斷下半段直接放進一個能睡、優先權可調的 kernel thread——就是下一章的 threaded IRQ；它也是 PREEMPT_RT 即時 kernel 的基石。

→ [Ch 31 threaded IRQ 與 -rt：讓中斷處理能睡、可搶佔](./31-threaded-irq.md)
