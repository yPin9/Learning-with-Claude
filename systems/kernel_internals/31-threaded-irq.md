# Ch 31 — threaded IRQ 與 -rt

> **目標**：搞懂為什麼現代驅動把中斷處理搬進 kernel thread（threaded IRQ）、`request_threaded_irq` 的 primary/thread 兩段式分工怎麼運作，以及 PREEMPT_RT 即時 kernel 如何把「幾乎所有東西都變成可搶佔、可排程」來換取有界的最壞延遲。這章是嵌入式/即時系統的分水嶺——你的 MTK 韌體線與 arm 課的即時需求都會落在這裡。

## 為什麼需要這個？

Ch 29 建立了 top half / bottom half 的分法：hardirq handler（top half）在**中斷 context**裡跑，必須極短；剩下的工作丟給 bottom half（Ch 30 的 softirq / tasklet / workqueue）。這個模型服役了幾十年，但它有一個從設計上就無法擺脫的問題：**hardirq handler 跑的時候，這顆 CPU 上什麼都被它壓著**。

具體來說，中斷 context 有三條鐵律（Ch 29 已經立過，這裡再釘一次，因為整章都繞著它轉）：

1. **不能睡**。你在中斷 context 裡沒有一個「行程」可以睡——沒有 `task_struct` 的排程語意可用（Ch 2 講 context 時的 `in_interrupt()`）。呼叫任何會睡的函式（`mutex_lock`、`kmalloc(GFP_KERNEL)`、`msleep`、`copy_from_user`）都是 bug，輕則 `BUG: scheduling while atomic`，重則死鎖。
2. **本地中斷通常是關的**。handler 執行期間，這顆 CPU 對同級（甚至全部）中斷是遮蔽的。你在裡面待越久，別的中斷延遲越久。
3. **延遲不可控**。一個寫得爛、或天生就慢的 hardirq handler（例如要讀一大塊 I2C register、要等一個 PHY 就緒），會把**整顆 CPU 的中斷回應時間**拖長，而且你無法排程它、無法降它的優先級、無法讓一個更緊急的即時任務插隊。

問題來了：**很多現代驅動的「中斷工作」其實沒那麼緊急**。一個感測器來了中斷，你要做的是讀幾個暫存器、丟進 buffer、也許喚醒一個等待的行程。這些工作可以稍微延後，不必在中斷 context 那麼嚴苛的環境裡完成。傳統做法是丟給 bottom half——但 softirq / tasklet 仍然跑在 atomic context（還是不能睡），workqueue 雖然能睡卻**排程時機不可控**（跑在共享的 kworker 上，優先級不受你掌控，延遲抖動大）。

我們想要的是一個中間地帶：**一個能睡、能被排程、能設優先級、能綁 CPU 的中斷處理實體**。這就是 threaded IRQ——把中斷 handler 搬進一個專屬的 kernel thread，讓它變成排程器眼中的一個普通（可設成即時優先級的）task。

> 這對即時系統是**根本性**的轉變，不只是方便。下面「為什麼這對即時很重要」一節會說清楚：hardirq 是即時延遲的頭號敵人，把中斷 threaded 化是 PREEMPT_RT 的核心手段之一。

## 先建立直覺

先用一張圖看清楚 `request_irq`（全 hardirq）和 `request_threaded_irq`（primary + thread）的分工差別：

```
【傳統 request_irq】— 所有工作都在 hardirq context

   裝置拉中斷線
        │
        ▼
   ┌─────────────────────────── 中斷 context（不能睡、關中斷）─────┐
   │  handler(irq, dev)                                            │
   │    ├─ 判斷是不是我的中斷                                       │
   │    ├─ 讀/清 register                                          │
   │    ├─ 搬資料、喚醒等待者……全部塞在這                          │
   │    └─ return IRQ_HANDLED                                      │
   └──────────────────────────────────────────────────────────────┘
   ↑ 這整段期間，這顆 CPU 上更高優先級的即時任務也得等


【threaded request_threaded_irq】— 切成兩段

   裝置拉中斷線
        │
        ▼
   ┌──── 中斷 context（極短、不能睡）────┐
   │  primary handler(irq, dev)          │   ← "hard irq handler"
   │    ├─ 是我的中斷嗎？不是 → IRQ_NONE  │
   │    ├─ (可選)遮蔽裝置中斷、記一下狀態 │
   │    └─ return IRQ_WAKE_THREAD ───────┼──┐  喚醒對應的 irq thread
   └─────────────────────────────────────┘  │
                                            ▼
   ┌──── process context（能睡、可排程、可設優先級）─────────────┐
   │  irq thread（kernel thread：irq/<N>-<name>）                │
   │    thread_fn(irq, dev)                                       │
   │      ├─ 慢慢讀 register、搬大塊資料                          │
   │      ├─ 可以 mutex_lock / msleep / kmalloc(GFP_KERNEL)       │
   │      └─ return IRQ_HANDLED                                   │
   └─────────────────────────────────────────────────────────────┘
   ↑ 這段是可排程實體：即時排程器（Ch 11 rt class）能管它
```

核心心智模型：**primary handler 只回答一個問題——「這是我的中斷嗎？要不要叫醒我的 thread？」**，其餘實際工作全丟到 irq thread。primary 在 hardirq context（快、短、不能睡），thread 在 process context（能睡、被排程器當一個 task 管理）。

## `request_threaded_irq`：兩段式註冊

註冊 threaded IRQ 的核心 API 在 `kernel/irq/manage.c`，函式 `request_threaded_irq()`（約 440–520 行一帶就是它的本體與參數校驗）：

```c
int request_threaded_irq(unsigned int irq,
                         irq_handler_t handler,      // primary handler，hardirq context
                         irq_handler_t thread_fn,    // thread handler，跑在 irq thread
                         unsigned long irqflags,
                         const char *devname,
                         void *dev_id);
```

對照你熟的 `request_irq()`——它其實只是把 `thread_fn` 塞成 `NULL` 再呼叫 `request_threaded_irq()`（`request_irq` 在 `include/linux/interrupt.h` 是個 inline wrapper）：

```c
// include/linux/interrupt.h（簡化）
static inline int
request_irq(unsigned int irq, irq_handler_t handler, unsigned long flags,
            const char *name, void *dev)
{
    return request_threaded_irq(irq, handler, NULL, flags, name, dev);
}
```

所以「傳統中斷」在源碼裡不是另一條路，而是 threaded 路徑的一個退化情形（`thread_fn == NULL`）。理解這點很關鍵：**整個中斷子系統的骨架已經是為 threaded 設計的**，非 threaded 只是不建 thread 而已。

`request_threaded_irq()` 內部做的事（讀 `kernel/irq/manage.c` 這段）：

- 一堆 sanity check：`irq` 合法嗎？`handler` 和 `thread_fn` 至少要有一個非 NULL（兩個都 NULL 直接 `-EINVAL`）；`IRQF_SHARED` 的話 `dev_id` 不能是 NULL（共享中斷要靠 `dev_id` 區分是誰的）。
- 配一個 `struct irqaction`（`include/linux/interrupt.h`），把 `handler`、`thread_fn`、`flags`、`dev_id` 填進去。
- 呼叫 `__setup_irq()`（同檔）做真正的安裝：如果 `thread_fn != NULL`（或系統強制 threaded，見下），就透過 `setup_irq_thread()` → `kthread_create()` 建一個 irq thread。
- thread 的名字組成 `irq/<irq號>-<devname>`——這就是你等下在 `ps` 裡會看到的東西。

有一個特別重要的組合語意：**如果 `handler == NULL` 但 `thread_fn != NULL`**，`__setup_irq()` 會塞一個預設的 primary handler `irq_default_primary_handler()`（`kernel/irq/manage.c`），它什麼都不做只回 `IRQ_WAKE_THREAD`。這對「level-triggered 中斷 + 想全部 threaded」的驅動很方便——但有個陷阱（見踩雷集錦）：這種情形下 `__setup_irq` 會要求你必須有 `IRQF_ONESHOT`，否則對 level 中斷會炸出中斷風暴。

還有一個你會用到的省事版本 `devm_request_threaded_irq()`（`kernel/irq/devres.c`）：綁定 device 生命週期，驅動 detach 時自動 free，省掉手動 `free_irq`。生產驅動裡這是常態。

## primary handler 的回傳值：控制流的閥門

primary handler 的回傳值決定接下來發生什麼，是理解 threaded IRQ 的鑰匙（回傳值定義在 `include/linux/irqreturn.h` 的 `enum irqreturn`）：

| 回傳值 | 意思 | 後果 |
|---|---|---|
| `IRQ_NONE` | 「不是我的中斷」 | 中斷不被認領；共享線上會問下一個 handler；全都 `IRQ_NONE` 太多次會觸發 spurious IRQ 偵測並可能停用該線 |
| `IRQ_HANDLED` | 「我處理完了，不需要 thread」 | 到此為止，不喚醒 thread（等同傳統中斷的行為） |
| `IRQ_WAKE_THREAD` | 「是我的，剩下的交給 thread」 | 喚醒對應 irq thread 去跑 `thread_fn`；只有註冊了 `thread_fn` 才合法 |

`IRQ_WAKE_THREAD` 的喚醒路徑值得記：hardirq 收尾在 `__handle_irq_event_percpu()`（`kernel/irq/handle.c`）判斷回傳值，若是 `IRQ_WAKE_THREAD` 就呼叫 `__irq_wake_thread()`，把 irq thread 的狀態設成可執行、丟回 runqueue。真正的處理發生在 thread 被排程到 CPU 之後——**這中間隔了一次排程延遲**，這是 threaded IRQ 的代價，也正是為什麼它「更即時但不是零延遲」。

irq thread 的主迴圈是 `irq_thread()`（`kernel/irq/manage.c`）：一個 `while` 迴圈，睡在等待被喚醒，醒來後呼叫 `thread_fn`，處理完再睡回去。對 `IRQF_ONESHOT` 的中斷，它跑完 `thread_fn` 才會在硬體層面 unmask 該中斷線（`irq_finalize_oneshot()`）——這保證「thread 還在處理時，同一條線不會再進來」，是 level-triggered 裝置的正確性關鍵。

## 底層機制：irq thread 是排程器眼中的一個 task

這是本章的靈魂。把中斷 threaded 化，本質上是**把「中斷處理」從一個不可排程的硬體事件，轉成一個可排程的軟體實體**。畫出資料結構的關係：

```
   struct irq_desc（每條中斷線一個，kernel/irq/irqdesc.c）
        │
        │  action 鏈（共享中斷會有多個）
        ▼
   struct irqaction ──── handler      = primary（hardirq）
        │            └── thread_fn    = 慢工作
        │            └── thread ──────────────┐
        │                                     ▼
        │                          struct task_struct（Ch 9！）
        │                            comm = "irq/24-eth0"
        │                            policy = SCHED_FIFO（RT 下）或 SCHED_NORMAL
        │                            prio / rt_priority ← chrt 可調
        │                            cpus_allowed ← 可綁 CPU（Ch 15）
        ▼
   被喚醒後進 runqueue，由排程器（Ch 11/12）決定何時上 CPU
```

關鍵洞察：**irq thread 就是一個 `task_struct`**（Ch 9）。一旦它是 task，Ch 11 整套排程器框架就全部適用了——

- 它有排程策略。在**非 RT** kernel 上，irq thread 預設跑 `SCHED_FIFO` 且 `rt_priority` 為 `MAX_RT_PRIO/2`（即 50，見 `kernel/irq/manage.c` 的 `setup_irq_thread` → `sched_set_fifo`），所以即使沒開 PREEMPT_RT，threaded IRQ 也已經是即時優先級的 task。
- 它可以被綁到特定 CPU（`cpus_allowed`／`irq_thread_check_affinity()`，跟著中斷的 affinity 走，接 Ch 15 的 CPU 綁定）。
- 它在 runqueue 裡跟別的 task 競爭 CPU——這就是為什麼即時排程器能「管理」中斷工作的優先級：因為它變成了排程器管得到的東西。

對比一下三種 bottom half，你會看到一條「越來越像 task」的光譜：

```
 softirq  ── 完全不是 task，在 atomic context 硬跑（Ch 30）
 tasklet  ── 建在 softirq 之上，還是 atomic
 workqueue── 跑在 kworker（是 task！能睡），但優先級/親和性不由你精細控制
 threaded── 專屬 irq thread（是 task、能睡、預設 SCHED_FIFO、可綁 CPU、可 chrt）
             └─ 最接近「把中斷變成一個你能完全掌控的可排程實體」
```

## 為什麼這對即時很重要

即時系統關心的不是「平均多快」，而是**最壞情況延遲（worst-case latency）有沒有上界、可不可預測**。一個機器手臂的控制迴圈、一條音訊 pipeline、一台 CNC，如果偶爾有一次中斷回應晚了 200 微秒，可能就出事了。

hardirq 是這個目標的頭號敵人，原因是它**打斷一切**：

```
   即時任務（優先級 99）正在跑它的控制迴圈……
        │
        ▼  ← 一個不相干的網卡中斷來了
   ┌── 網卡 hardirq handler（傳統，全塞在中斷 context）──┐
   │   即時任務被硬生生打斷，即使它優先級更高            │
   │   handler 跑多久，即時任務就被延遲多久              │
   └────────────────────────────────────────────────────┘
        │
        ▼  handler 跑完才輪回即時任務
   即時任務恢復 ← 這段延遲你無法用優先級消除，因為 hardirq 凌駕排程器
```

問題的根源：**hardirq 不受排程器管**。它是硬體驅動的，優先級由中斷控制器（APIC / GIC，Ch 29）決定，跟你的即時任務優先級是兩個世界。你把即時任務設到最高優先級也沒用——一個低優先級裝置的 hardirq 照樣插進來把它打斷。

threaded IRQ 把這個「不可排程的打斷」轉成「可排程的 task」。轉換之後：

- 中斷工作（thread_fn 那部分）現在有一個明確的優先級，落在排程器的 rt class 裡（Ch 11）。
- 你可以讓你的即時控制任務**優先級高於某些 irq thread**——這樣網卡的中斷處理反而要讓路給你的控制迴圈。這在傳統模型下不可能。
- 留在 hardirq 的只剩 primary handler 那極短的一段（判斷是不是我的、要不要喚醒 thread），把不可排程的視窗壓到最小。

這就是「把中斷變成可排程實體」的實際意義：**你重新奪回對延遲的控制權**。

## PREEMPT_RT：把「幾乎一切」都變可搶佔

threaded IRQ 是即時化的一塊拼圖，但單靠它不夠。Ch 14 講過 kernel 的搶佔模型（`PREEMPT_NONE` / `PREEMPT_VOLUNTARY` / `PREEMPT`）——即使開了 `CONFIG_PREEMPT`，kernel 裡仍有大量**不可搶佔的視窗**：拿著 spinlock 的時候、在 softirq 裡的時候、在 hardirq 裡的時候。這些視窗就是最壞延遲的來源。

PREEMPT_RT（即時 kernel patch，長年在 mainline 外維護，直到近年才逐步併入主線）的目標是把這些視窗幾乎全部消滅，讓 kernel 逼近**完全可搶佔**。它的三板斧：

1. **spinlock 變睡眠鎖**。這是最激進、也最反直覺的一刀。在 RT 下，絕大多數 `spinlock_t` 被重新定義成 `rt_mutex`（實時互斥鎖，接 Ch 26）——也就是說「拿 spinlock」變成**可以睡、可以被搶佔**。傳統 spinlock 拿著時關搶佔（造成延遲），RT 下改成睡眠鎖後，拿鎖的 task 可以被更高優先級的即時任務搶走。代價：本來不能睡的 code path 在 RT 下語意變了（真正需要 atomic 的地方改用 `raw_spinlock_t`，它在 RT 下仍是傳統 spinlock）。

2. **中斷幾乎全 threaded**。RT 下 `request_irq` 註冊的中斷會被**強制 threaded**（`IRQ_FORCED_THREADING`，見 `kernel/irq/manage.c` 的 `irq_setup_forced_threading()`；受 `IRQF_NO_THREAD` 排除）。也就是本章前半的 threaded IRQ 從「驅動自己選」變成「系統預設」。留在真正 hardirq 的只剩少數必須極快回應的（如 timer、per-CPU 的一些中斷）。

3. **softirq 也 threaded**。傳統 softirq 在 atomic context 硬跑（Ch 30），是延遲來源。RT 下 softirq 也搬進 thread（`ksoftirqd` 相關路徑），變成可搶佔、可排程。

三板斧合起來，效果是：kernel 裡幾乎每一段都變成「可被更高優先級即時任務搶佔」的。**目的是讓最壞情況延遲有界、可預測**；**代價是吞吐略降**——睡眠鎖比 spinlock 貴、context switch 變多、cache locality 變差。這是一筆明確的交換：拿一點平均吞吐，換延遲上界的保證。適用場景：工業控制、音訊（低 latency DAW）、機器人、CNC（接 arm 課的即時章節）；不適用：追求吞吐的伺服器、批次運算。

### 6.12 的認識論誠實

PREEMPT_RT 的合併是**漸進的**，不是某一天突然全進主線。這段歷史要說清楚，別給讀者「6.12 = 完整 RT」的錯覺：

- RT patch 從 2004 年起在 mainline 外維護，多年來把一塊塊功能陸續上游（printk 重寫、`rt_mutex`、`local_lock`、threaded IRQ 本身早就進主線了）。
- **2024 年 9 月，`PREEMPT_RT` 這個 config 選項本身被 Linus 併入主線（對應 6.12 開發週期）**，這是一個里程碑：核心的「把 spinlock 變睡眠鎖」的可搶佔化終於能在 mainline 直接開啟，不必再打 out-of-tree patch。
- 但**「config 進主線」不等於「所有架構、所有子系統都完備即時」**。x86_64 與 arm64 支援最成熟；部分驅動、部分架構仍有粗糙處。實務上要達到硬即時，你仍要驗證你的具體平台+驅動組合，並用 `cyclictest` 實測，而不是假設「開了 RT 就一定有界」。

一句話總結 6.12 狀態：**PREEMPT_RT 的核心可搶佔化在 6.12 已可於主線啟用，是一個成熟但仍在收尾的長期工程**——這是目前最誠實的講法。

## 優先級反轉與優先級繼承

RT 把 spinlock 變成睡眠鎖（`rt_mutex`）帶出一個即時系統的經典問題：**優先級反轉（priority inversion）**。這裡跟 Ch 26 的 `rt_mutex` 接上。

場景（三個任務，優先級 H > M > L）：

```
   L（低）先拿到鎖 R ────────────┐ 持有 R
                                 │
   H（高）想拿 R → 被 L 擋住，睡  │ 等 R
                                 │
   M（中）不需要 R，但優先級 > L  │
     → M 把 L 搶走一直跑 ─────────┘  L 一直上不了 CPU、放不掉 R
                                     → H 被 M「間接」無限期擋住
```

結果：高優先級的 H 被中優先級的 M 卡住，明明 H > M。這就是優先級反轉，火星探測器 Pathfinder 當年就栽在這（經典案例）。

**優先級繼承（priority inheritance, PI）**是解法，`rt_mutex` 內建（`kernel/locking/rtmutex.c`）：當 H 因為等 L 持有的鎖而阻塞時，**L 暫時繼承 H 的優先級**，這樣 M 就搶不走 L，L 能盡快跑完放鎖，H 隨即拿到。放鎖後 L 的優先級恢復原狀。

為什麼這在 RT 下**必須有**：因為 RT 把大量 spinlock 變成了 `rt_mutex`，等於把「持鎖時可能被搶佔」這個新風險引進了 kernel 的每個角落。沒有 PI，優先級反轉會在無數地方潛伏，即時保證就破功了。所以 PI 不是可選優化，是 RT 可搶佔化的**配套安全網**。

## 動手：寫一個會睡的 threaded IRQ 模組

我們要做兩件事證明 threaded IRQ 的核心價值：(1) thread handler 裡能 `msleep`（對比 Ch 30 的 softirq/tasklet 不能睡）；(2) 在 `ps` 裡看到我們的 irq thread。

沒有真實裝置，我們借一個手法：用一個 GPIO 或——在純 QEMU 環境下更簡單——直接觀察一條既有的共享中斷線。但最乾淨的教學做法是**自己觸發**：這裡示範用 `request_threaded_irq` 掛到一個我們能軟體觸發的中斷上。若你的 QEMU 沒有方便的可觸發線，退而用「掛到 timer / 既有裝置線並只讀取」也能看到 thread。以下模組聚焦「thread_fn 裡睡覺不會炸」這個要點：

```c
// threaded_irq_demo.c
#include <linux/module.h>
#include <linux/interrupt.h>
#include <linux/delay.h>
#include <linux/kthread.h>

static int irq_num = 1;             // 用哪條 IRQ 線（module 參數，依你環境改）
module_param(irq_num, int, 0444);

static irqreturn_t my_primary(int irq, void *dev)
{
    // hardirq context：只能做極短的事，絕不能睡
    // 這裡假裝「判斷是我的中斷」→ 喚醒 thread
    pr_info("threaded_demo: primary (hardirq) on cpu %d\n", smp_processor_id());
    return IRQ_WAKE_THREAD;         // 交給 thread
}

static irqreturn_t my_thread(int irq, void *dev)
{
    // process context：能睡！這在 Ch 30 的 softirq/tasklet 是死罪
    pr_info("threaded_demo: thread_fn start, pid=%d comm=%s\n",
            current->pid, current->comm);
    msleep(100);                    // ← 睡 100ms 證明能睡；softirq 這樣寫直接 BUG
    pr_info("threaded_demo: thread_fn done after sleep\n");
    return IRQ_HANDLED;
}

static int __init demo_init(void)
{
    int ret;
    // IRQF_SHARED：共享既有中斷線才不會 -EBUSY（dev_id 不能為 NULL）
    // IRQF_ONESHOT：thread 跑完才 unmask，level 中斷的正確做法
    ret = request_threaded_irq(irq_num, my_primary, my_thread,
                               IRQF_SHARED | IRQF_ONESHOT,
                               "threaded_demo", (void *)&irq_num);
    if (ret) {
        pr_err("threaded_demo: request_threaded_irq failed: %d\n", ret);
        return ret;
    }
    pr_info("threaded_demo: registered on IRQ %d; look for irq/%d-threaded_demo in ps\n",
            irq_num, irq_num);
    return 0;
}

static void __exit demo_exit(void)
{
    free_irq(irq_num, (void *)&irq_num);
    pr_info("threaded_demo: unregistered\n");
}

module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("threaded IRQ that sleeps in its thread handler");
```

編譯載入（Makefile 照 Ch 0）：

```
/ # insmod /threaded_irq_demo.ko irq_num=1
threaded_demo: registered on IRQ 1; look for irq/1-threaded_demo in ps
```

**看 irq thread**——這是本章最直接的實感：

```
/ # ps -e | grep irq/
   14 root     [irq/1-threaded_demo]
   ...其他系統的 irq/ threads（timer、鍵盤、磁碟等）
```

方括號代表 kernel thread（沒有使用者空間位址空間）。名字 `irq/1-threaded_demo` = `irq/<線號>-<你給的 devname>`，正是 `setup_irq_thread()` 組出來的。

**看/調它的優先級**（`chrt`，接 Ch 11 rt class）：

```
/ # chrt -p 14                 # 查 pid 14 的排程策略與優先級
pid 14's current scheduling policy: SCHED_FIFO
pid 14's current scheduling priority: 50     ← MAX_RT_PRIO/2，前面說過的預設

/ # chrt -f -p 80 14           # 把它提到 FIFO 優先級 80
/ # chrt -p 14
pid 14's current scheduling priority: 80
```

你剛剛做的事——**用 chrt 調一個中斷處理實體的即時優先級**——在傳統 hardirq 模型下是不可能的。這就是 threaded IRQ 給即時系統的那把鑰匙。

**量中斷延遲（cyclictest 點一下）**：即時圈用 `cyclictest`（`rt-tests` 套件）量「喚醒延遲」——它睡一個精確時間再看實際被喚醒晚了多少：

```
# cyclictest -p 99 -i 1000 -l 100000 -m
# T: 0 (...) P:99  ... Min: 4  Act: 6  Avg: 7  Max: 23   ← 單位 μs
```

`Max` 就是最壞喚醒延遲。在非 RT kernel 上，你會看到偶發的大 `Max`（幾百 μs 甚至 ms，來自那些不可搶佔視窗）；在 PREEMPT_RT kernel 上，`Max` 會被壓得又低又穩——這就是「有界延遲」的實測樣貌。cyclictest 是驗證你的 RT 設定到底有沒有效的黃金標準，別只看 config 開了就信。

## 對比與取捨

| 機制 | context | 能睡？ | 排程/優先級 | 延遲特性 | 用在哪 |
|---|---|---|---|---|---|
| hardirq（`request_irq` 全塞） | 中斷 context | 否 | 不受排程器管 | 最低延遲但打斷一切 | 必須極快回應、工作極短 |
| softirq / tasklet（Ch 30） | atomic | 否 | 不受精細控制 | 低但仍在 atomic 視窗 | 網路收包等高頻、短、不睡的工作 |
| workqueue（Ch 30） | process（kworker） | 是 | 共享 worker，優先級難控 | 可睡但抖動大 | 能延後、需睡、不在乎精確時機 |
| **threaded IRQ** | process（專屬 irq thread） | 是 | SCHED_FIFO、可 chrt、可綁 CPU | 更即時、可控，但多一次排程延遲 | 需睡+需可控優先級的中斷工作 |
| **PREEMPT_RT 全套** | 幾乎全 threaded/可搶佔 | — | rt class + PI | 最壞延遲有界、可預測 | 硬即時：工控/音訊/機器人 |

一句話取捨：**threaded IRQ 用「多一次排程延遲」換「能睡 + 可控優先級」；PREEMPT_RT 用「一點吞吐」換「延遲上界」**。沒有免費的即時。

## 踩雷集錦

1. **「threaded IRQ 一定更快」——錯**。threaded IRQ 對**平均延遲通常更慢**（多一次喚醒+排程）。它換來的是**能睡**和**可預測 / 可控優先級**，不是更快。追求最低平均延遲的短工作，留在 hardirq 反而對。

2. **primary handler 回 `IRQ_HANDLED` 卻期待 thread 跑**。只有回 `IRQ_WAKE_THREAD` 才會喚醒 thread。回 `IRQ_HANDLED` = 「我在 hardirq 裡就搞定了」，thread_fn 永遠不會被呼叫。這是新手最常見的「thread 怎麼沒動」。

3. **level-triggered 中斷 + threaded 但忘了 `IRQF_ONESHOT`**。level 中斷在裝置沒被清除前會**持續**拉低中斷線。若 primary 回 `IRQ_WAKE_THREAD` 後就 unmask，thread 還沒來得及清裝置，同一個中斷立刻又進來 → **中斷風暴**，系統卡死。`IRQF_ONESHOT` 讓 kernel 等 thread 跑完才 unmask。當 `handler==NULL`（用預設 primary）時，`__setup_irq` 甚至會強制要求 `IRQF_ONESHOT`。

4. **在 primary handler 裡呼叫會睡的東西**。primary 仍在 hardirq context！`msleep`/`mutex_lock`/`kmalloc(GFP_KERNEL)` 全是死罪。能睡的是 **thread_fn**，不是 primary。把「能睡」的直覺錯用到 primary 上是災難。

5. **以為「開了 CONFIG_PREEMPT 就是即時」**。`CONFIG_PREEMPT`（Ch 14）只是「可搶佔核心」，不是 PREEMPT_RT。真正的有界延遲要 `PREEMPT_RT`（spinlock→睡眠鎖那套）。兩者差一個數量級的延遲保證。config 名字像但天差地遠。

6. **`raw_spinlock_t` 和 `spinlock_t` 在 RT 下不一樣**。RT 下 `spinlock_t` 變睡眠鎖，`raw_spinlock_t` 仍是傳統關搶佔的 spinlock。你在真正 atomic、絕不能睡的地方（如排程器核心、中斷入口）必須用 `raw_spinlock_t`，否則 RT kernel 會炸。寫要進主線的 code 這個區分是硬要求。

## 進階：再往深一層

- **`IRQF_NO_THREAD`**：RT 下強制 threading 有例外——某些中斷（如 timer、per-CPU 中斷、必須極快的）用這個 flag 明確禁止被 threaded，留在真 hardirq。看 `kernel/irq/manage.c` 的 `irq_setup_forced_threading()` 怎麼判斷。
- **irq thread 的 affinity 跟隨**：`irq_thread_check_affinity()`（`kernel/irq/manage.c`）讓 irq thread 跟著中斷的 CPU affinity 走——你在 `/proc/irq/N/smp_affinity` 改中斷親和性，thread 也跟著搬。即時系統常把中斷+其 thread 隔離到專用 CPU（配 `isolcpus`，Ch 15），把某幾顆 CPU 留給即時任務乾淨跑。
- **面試常問：threaded IRQ vs workqueue 差在哪**？都跑在 process context 能睡，但 (1) threaded IRQ 有專屬 thread + 預設 SCHED_FIFO + 可精細控優先級/親和性；workqueue 跑共享 kworker，優先級不由你細調。(2) threaded IRQ 語意上「屬於這條中斷線」，有 ONESHOT/unmask 的正確性配套；workqueue 是通用延後執行。即時場景選 threaded，一般延後工作選 workqueue。
- **面試常問：PREEMPT_RT 為什麼要把 spinlock 變睡眠鎖**？因為 spinlock 持有期間關搶佔，是最壞延遲的主要來源。變睡眠鎖後持鎖也能被高優先級任務搶佔，延遲才有界；代價是引入優先級反轉風險，故用 `rt_mutex` 的優先級繼承補上。
- **printk 與 RT**：傳統 `printk` 在 RT 下曾是延遲毒藥（在 atomic context 直接寫 console 很慢）。RT 推動了 printk 的大改寫（分離「記錄」與「輸出」），這也是 RT 上游化過程中的一大工程，2023–2024 才大致落地。

## 動手練習

1. **證明 thread 能睡、primary 不能**：把上面模組的 `msleep(100)` 從 `my_thread` 挪到 `my_primary`，重編載入，觀察 dmesg——你會看到 `BUG: scheduling while atomic` 之類的爆炸。挪回 thread 就正常。親手炸一次，這個界線你一輩子記得。

2. **玩 chrt 改優先級**：`ps -e | grep irq/` 找到你的 thread，用 `chrt -f -p <prio> <pid>` 改它的 FIFO 優先級，再 `chrt -p <pid>` 確認。想想：如果你有一個 SCHED_FIFO 優先級 90 的即時任務，把某 irq thread 設成 50 vs 95，對那個任務的延遲影響是什麼？

3. **對比 workqueue**：回顧 Ch 30，把同樣「睡 100ms」的工作用 `schedule_work` 丟給 workqueue 實作一份，`ps` 看它跑在哪個 kworker、優先級是什麼。體會 threaded IRQ 的「專屬 + 可控」和 workqueue 的「共享 + 通用」的差別。

4.（進階）**cyclictest 對比**：如果你能編一顆非 RT 和一顆 `PREEMPT_RT` 的 6.12，各跑 `cyclictest -p 99 -i 1000 -l 1000000 -m` 幾分鐘，比較兩者的 `Max`。你會親眼看到「有界延遲」不是口號。

## 本章重點整理

- `request_threaded_irq` 把中斷切成 **primary handler（hardirq、極短、回 `IRQ_WAKE_THREAD`）** 和 **thread_fn（跑在專屬 irq thread、能睡、被排程器當 task 管）**；`request_irq` 只是 `thread_fn==NULL` 的退化情形。
- irq thread 是一個 `task_struct`（`irq/N-name`），預設 `SCHED_FIFO` 優先級 50，可用 `chrt` 調、可綁 CPU——這讓即時排程器能管理中斷工作的優先級，奪回對延遲的控制。
- **PREEMPT_RT** 用三板斧逼近完全可搶佔：spinlock 變睡眠鎖（`rt_mutex` + 優先級繼承）、中斷幾乎全 threaded、softirq 也 threaded；目的是最壞延遲有界，代價是吞吐略降。
- 6.12 的誠實講法：`PREEMPT_RT` config 本身已併入主線（2024 里程碑），核心可搶佔化可在 mainline 啟用，但仍是成熟中收尾的長期工程；硬即時要靠 `cyclictest` 在你的具體平台實測驗證。

## 自我檢核

- [ ] 不看筆記，能畫出 `request_irq` vs `request_threaded_irq` 的分工，並說出 primary 三個回傳值各自的後果
- [ ] 能解釋為什麼 hardirq 是即時延遲的頭號敵人，以及 threaded IRQ 如何把它變成「可排程實體」
- [ ] 能說出 PREEMPT_RT 的三板斧，尤其「spinlock 變睡眠鎖」為什麼必須配優先級繼承
- [ ] 面試被問「threaded IRQ 和 workqueue 差在哪」，你能講出 context、優先級可控性、語意歸屬三點
- [ ] 能說清楚 6.12 時 PREEMPT_RT 的真實狀態，不誇大成「完整即時」
- [ ] 知道 `IRQF_ONESHOT` 對 level-triggered threaded 中斷為什麼是正確性關鍵

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/genericirq.rst](https://www.kernel.org/doc/html/latest/core-api/genericirq.html)**
  - **讀哪裡**：threaded interrupt handlers 一節，以及 `irq_desc`/`irqaction` 的結構說明
  - **和本章的關聯**：本章的 `request_threaded_irq` / primary vs thread 分工的權威出處；讀完再回頭看 `kernel/irq/manage.c` 會順很多

- **[Real-Time Linux Wiki（linuxfoundation.org / OSADL）](https://wiki.linuxfoundation.org/realtime/start)**
  - **讀哪裡**：PREEMPT_RT 的 overview 與 mainline 併入進度頁
  - **能學到什麼**：RT patch 上游化的最新狀態（本章 6.12 的說法以此為準）、cyclictest 的正確用法與解讀

### LWN 文章

- **[LWN: "The real BKL end game" 系列 / "PREEMPT_RT" 相關報導](https://lwn.net/Kernel/Index/#Realtime)**
  - **讀哪裡**：搜 LWN 的 Realtime index，特別是 2023–2024 談 PREEMPT_RT 併入主線與 printk 重寫的幾篇
  - **為什麼值得讀**：LWN 是 RT 上游化過程最權威的長期追蹤，把「為什麼花二十年」講得最清楚；本章「認識論誠實」那段的背景都在這裡

### 書籍 / 論文

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly）
  - **讀哪裡**：Interrupts and Exceptions 一章
  - **注意**：它講的是舊 kernel 的 top/bottom half，沒有 threaded IRQ；當「傳統模型」的深入補充讀，對照本章看演進

- **優先級反轉經典案例：Mars Pathfinder priority inversion**（網路上 Glenn Reeves 的第一手 postmortem）
  - **為什麼讀**：優先級反轉+繼承最有名的真實災難，讀完你就懂為什麼 RT 的 `rt_mutex` 一定要有 PI，不是學術玩具

下一章我們從「中斷把時間軸切開」轉向「時間本身」：kernel 怎麼記時間、`jiffies` 與 `hrtimer` 的分工、以及 tickless（NO_HZ）如何在省電與即時之間權衡——這些正是 cyclictest 底下在量的東西。

→ [Ch 32 時間管理：jiffies、hrtimer 與 tickless](./32-timers-hrtimer.md)
