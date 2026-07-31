# Ch 7 — per-CPU 變數與 kernel 的並行本質

> **目標**：建立「kernel 天生高度並行——你寫的每一行 code 隨時可能被搶佔、被中斷、同時在別的 CPU 上跑」的心智模型；學會 per-CPU 變數這個核心免鎖技巧，理解它為什麼能免鎖、代價是什麼。這章給你「痛」（data race 是什麼），Part 4 給你「解藥」（atomic/鎖/RCU）。

## 為什麼需要這個？

到 Ch 6 為止，你寫的模組 code 讀起來像單執行緒 C 程式：`kmalloc` 一塊記憶體、填幾個欄位、`kfree`。那個直覺——「程式從上到下一行一行跑，沒人動我的變數」——在使用者空間單執行緒程式裡成立，**在 kernel 裡從第一行就不成立**。

看一段看起來人畜無害的 code：

```c
static unsigned long packet_count;      // 全域統計計數器

void on_packet_received(void)
{
    packet_count++;                     // 收到一個封包，計數 +1
}
```

在單執行緒使用者程式裡，這沒問題。在 kernel 裡，這行 `packet_count++` 是一個**等著出事的 bug**：

- 你的機器有 8 個 CPU 核心，8 張網卡佇列可能**同時**在 8 個核心上跑 `on_packet_received`——8 個 CPU 同時讀改同一個 `packet_count`
- 就算只有一個 CPU，這段 code 跑到一半可能被**中斷**（Ch 29）打斷，中斷處理常式也去碰 `packet_count`
- 就算沒中斷，kernel 是**可搶佔**的（Ch 2 講過 preemption），排程器可能在 `packet_count++` 執行到一半時把你這條路徑換下 CPU，換上另一條也在改 `packet_count` 的路徑

`packet_count++` 不是一個不可分割的動作。它是「讀出 `packet_count` → 加 1 → 寫回」三步。兩個 CPU 交錯執行這三步，就會**丟計數**（lost update）——這是我們這章要讓你親手復現的「痛」。

kernel 對這個痛有很多解法。Part 4 會給你重武器：atomic 操作（Ch 24）、spinlock（Ch 25）、RCU（Ch 27）。但 kernel 最偏愛的、也是**最便宜**的解法，是根本不要共享——每個 CPU 一份自己的副本，各改各的，天生無 race。這就是 **per-CPU 變數**，本章主角。

理解 per-CPU 不只是學一個 API。它逼你把「kernel 是高度並行的」這件事內化成直覺，這個直覺是讀懂後面每一章的前提：排程器為什麼每個 CPU 一個 run queue（Ch 11）、slub 為什麼每個 CPU 一個 freelist（Ch 18）、RCU 為什麼能無鎖讀（Ch 27）——答案都繞著「per-CPU + 避免共享」打轉。

## 先建立直覺

先把 kernel 並行的三個來源疊在一起看清楚。使用者空間單執行緒程式面對的是「只有我在跑」；kernel code 面對的是三股力量同時作用：

```
                    你的一段 kernel code 正在跑
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   ① SMP 多核             ② preemption           ③ 中斷
   別的 CPU 此刻正       排程器隨時把你          硬體隨時打斷你，
   在跑同一段 code，     換下 CPU，換上另         跳去跑中斷處理常式，
   碰同一個全域變數      一條也碰同變數的路徑     它也可能碰同變數

   → 三者「疊加」，任兩者都足以造成 race。使用者空間單執行緒
     直覺（「程式碼循序執行、沒人動我的資料」）在此完全失效。
```

這三股力量任何一股單獨出現就足以毀掉「循序執行」的假設，kernel 裡三股同時存在。所以 kernel 開發者的預設心態不是「這段 code 安全嗎」，而是「這段 code 會被誰同時碰、我怎麼保護共享狀態」。

保護共享狀態最直接的想法是加鎖。但鎖有代價：每次存取都要搶鎖，多核搶同一把鎖會互相等待、cache line 在核心間彈來彈去（cache line bouncing，Ch 23 詳談），核心越多越塞。於是 kernel 反過來問：**如果根本不共享呢？**

per-CPU 變數就是這個反問的答案。把一個全域變數變成「每個 CPU 一份」：

```
   全域變數 + 鎖（大家搶一份）              per-CPU 變數（各持一份）

        ┌───────────────┐              CPU0 ─→ ┌──────────┐  count=13
   CPU0 │               │                      └──────────┘
   CPU1 │  packet_count │              CPU1 ─→ ┌──────────┐  count=7
   CPU2 │   （一份）     │◄── 搶鎖              └──────────┘
   CPU3 │               │              CPU2 ─→ ┌──────────┐  count=21
        └───────────────┘                      └──────────┘
         ▲  ▲  ▲  ▲                    CPU3 ─→ ┌──────────┐  count=4
         └──┴──┴──┴── 四核搶一把鎖               └──────────┘
            序列化，核越多越塞          各改各的，零競爭；要總數時再加總 13+7+21+4=45
```

左邊：一份資料、一把鎖，所有 CPU 排隊存取，這是瓶頸。右邊：每個 CPU 存取自己那份，**互不干涉、完全無鎖**；只有在你真的需要「全體總和」時，才走一趟 `for_each_possible_cpu` 把每份加起來。統計計數器、快取、freelist 這類「大量寫、偶爾讀總和」的場景，per-CPU 是完勝的設計。

代價當然有：per-CPU 讀不到即時的全域一致值（你加總的瞬間別的 CPU 還在改），而且存取自己那份的期間**不能被搬到別的 CPU**去——否則你改到別人那份了。這兩點是本章後半的重點。

## per-CPU 變數：API 導讀

per-CPU 的核心定義在 `include/linux/percpu-defs.h`（宣告與存取巨集）與 `include/linux/percpu.h`（動態配置 API）。實作骨架在 `mm/percpu.c`。

### 定義一個 per-CPU 變數

```c
#include <linux/percpu.h>

DEFINE_PER_CPU(unsigned long, my_count);   // 每個 CPU 一份 unsigned long
```

`DEFINE_PER_CPU(type, name)` 定義一個名為 `name`、型別為 `type` 的 per-CPU 變數。它不是分配 `NR_CPUS` 份陣列那麼粗暴——編譯器把它放進一個特別的 section（`.data..percpu`），開機時 kernel 為每個 CPU 配置一塊 per-CPU 區域，把這個 section 複製成每 CPU 一份。存取時用「基底（這個 CPU 的區域起點）+ 變數在 section 裡的 offset」算出真正位址。細節可以查 `mm/percpu.c` 的 `pcpu_alloc`，但用的時候你不需要知道位址怎麼算，巨集幫你算好。

要在別的檔案用同一個變數，用 `DECLARE_PER_CPU(type, name)` 宣告（放 header）。

### 存取自己這個 CPU 的那份

這是最常見的操作，有兩組巨集，差別在「有沒有順帶關搶佔」：

```c
/* 第一組：this_cpu_* —— 存取當前 CPU 的副本，單一操作內部保證不被搬走 */
this_cpu_inc(my_count);                 // 當前 CPU 的 my_count += 1
this_cpu_add(my_count, 5);              // += 5
unsigned long v = this_cpu_read(my_count);
this_cpu_write(my_count, 0);

unsigned long *p = this_cpu_ptr(&my_count);   // 拿當前 CPU 那份的指標

/* 第二組：get_cpu_var / put_cpu_var —— 明確關掉搶佔的一段臨界區 */
unsigned long *pc = &get_cpu_var(my_count);   // 關 preemption + 拿指標
*pc += 1;
*pc += 1;                                     // 這中間保證還在同一個 CPU
put_cpu_var(my_count);                        // 開回 preemption
```

**兩組的關鍵差別，是本章最容易搞混的點**：

- `this_cpu_inc()`、`this_cpu_add()` 這類**單一 RMW（read-modify-write）操作**，在 x86 上會編成一條帶 `%gs` 段前綴的指令（如 `incq %gs:offset`），這條指令本身相對於「這個 CPU 上的中斷/搶佔」是不可分割的——所以單一 `this_cpu_inc` 你**不需要**手動關搶佔。
- 但如果你要對同一份 per-CPU 資料做**好幾個操作，而且中間不能換 CPU**（例如「讀出來、算一算、再寫回去」），單靠 `this_cpu_*` 不夠：兩個 `this_cpu_*` 之間可能被搶佔、被換到別的 CPU，第二個操作就打到別份了。這時要用 `get_cpu_var()`／`put_cpu_var()` 把整段包起來——`get_cpu_var` 會 `preempt_disable()`，`put_cpu_var` 對應 `preempt_enable()`，保證這段期間你「釘」在同一個 CPU 上。

一句話記住：**`get_cpu_ptr`/`put_cpu_ptr`（指標版）、`get_cpu_var`/`put_cpu_var`（值版）是「關搶佔的括號」，用來保護「多步存取同一份 per-CPU 資料」的臨界區；單步操作用 `this_cpu_*` 就好。** 對應巨集都在 `include/linux/percpu-defs.h`。

### 讀別的 CPU 那份

```c
int cpu;
unsigned long total = 0;

for_each_possible_cpu(cpu)
    total += per_cpu(my_count, cpu);    // 讀第 cpu 個 CPU 的那份
```

`per_cpu(var, cpu)` 讓你讀（或寫）**指定某個 CPU** 的那份。加總統計時就是這樣用：走一遍所有 CPU，把每份相加。`for_each_possible_cpu` 迭代「這台機器可能上線的所有 CPU」（`include/linux/cpumask.h`）。

跨 CPU 讀別人的那份是 per-CPU 的陷阱區：那個 CPU 此刻可能正在改它自己那份，所以你讀到的可能是舊值、也可能讀到一個更新到一半的值（在能被撕裂的型別上）。加總統計通常可以接受這種近似（少算一兩個瞬間值無所謂），但**如果你要求跨 CPU 的精確一致，per-CPU 加 `for_each_possible_cpu` 給不了你**——那需要更強的同步，回到 Part 4。

### 動態配置的 per-CPU（`alloc_percpu`）

`DEFINE_PER_CPU` 是靜態定義。要在 runtime 配置（例如每註冊一個裝置就要一組 per-CPU 計數器），用 `alloc_percpu(type)`（回傳 `type __percpu *`）配、`free_percpu()` 釋放，存取一樣用 `this_cpu_ptr`/`per_cpu_ptr`。這在驅動裡很常見。API 在 `include/linux/percpu.h`。

## 底層機制：per-CPU 為什麼能免鎖、它用在哪

### 免鎖的本質

per-CPU 能免鎖，靠的是一個簡單但強的不變式：**只要每個 CPU 只碰自己那份，就不存在「兩個 CPU 同碰一份」的競爭。** 鎖是為了序列化「多方存取同一份資料」；當資料根本不共享，序列化就沒必要，鎖自然省掉。

但這個不變式要成立，得守住一個前提：**存取期間，執行流不能從一個 CPU 搬到另一個 CPU。** 想像：

```
   CPU0 上：拿到「CPU0 的那份」的指標 p  ─┐
                                          │  ← 這中間如果被搶佔、
   （被搶佔，排程器把這條路徑搬到 CPU1）   │     換到 CPU1……
                                          │
   CPU1 上：*p += 1   ← 用的還是 CPU0 那份的指標，但你人已經在 CPU1！
                        改到了「別人的」那份，不變式破了
```

所以規則是：**拿了 per-CPU 指標之後、用完之前，必須關搶佔（或本來就在關搶佔/關中斷的 context 裡）。** 這正是 `get_cpu_ptr`/`put_cpu_ptr` 存在的理由——它們把「拿指標」和「關搶佔」綁在一起，讓你不會忘。而 `this_cpu_*` 系列因為「算 offset + 存取」是單一操作，中間沒有可以被搬走的空隙，所以自帶安全、不用你關搶佔。

> 有一個常被誤解的點：per-CPU **不保證**對抗中斷。單一 `this_cpu_inc` 相對於「同一 CPU 上的中斷」是安全的（那條指令不可分割）。但如果你用 `get_cpu_var` 包一段「讀-改-寫」，中斷仍可能在這段中間插入——中斷處理常式若也去改同一個 per-CPU 變數，就 race 了。這種情境要改用關中斷版本（`local_irq_save` 之類，Ch 29）。**per-CPU 消滅的是「跨 CPU 的並行」，不是「同一 CPU 上的中斷插入」。** 這兩者要分清楚。

### 它用在哪：kernel 裡的 per-CPU 遍地都是

per-CPU 不是邊角技巧，是 kernel 可擴展性的支柱。幾個你後面章節會撞見的例子：

| 用途 | 在哪 | 為什麼用 per-CPU |
|---|---|---|
| per-CPU run queue | Ch 11，`kernel/sched/sched.h` 的 `struct rq`，`DEFINE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues)` | 每個 CPU 排自己的 task，選下一個要跑誰時不必跟別的 CPU 搶 run queue |
| slub per-CPU freelist | Ch 18，`mm/slub.c` 的 `struct kmem_cache_cpu` | 每個 CPU 一份「快取的空閒物件」，`kmalloc` 熱路徑先摸自己這份，免鎖免搶 |
| 統計計數器 | 全 kernel，如 `/proc/stat`、網路統計 | 大量寫、偶爾讀總和，per-CPU 各寫各的，讀時加總 |
| per-CPU 中斷計數 | `/proc/interrupts` 每 CPU 一欄 | 中斷發生在哪個 CPU 就記到那個 CPU 的計數，天生無 race |

看出模式了嗎：**「每個 CPU 高頻獨立更新、偶爾才需要全局視角」的資料，就是 per-CPU 的甜蜜點。** run queue、freelist、計數器全都符合。反過來，「需要即時全局一致」的資料（例如一個必須立刻對所有 CPU 生效的旗標）就不適合 per-CPU。

## 動手：per-CPU 計數器 vs 沒保護的全域計數器

我們寫一個模組，開一堆 kernel thread 在多個 CPU 上狂加計數，一組用沒保護的全域變數、一組用 per-CPU 變數，最後比對「實際加了幾次」和「計數器顯示多少」。全域那組會少算（lost update），per-CPU 那組會精準。這就是把本章的「痛」與「解藥」擺在同一支模組裡。

```c
// perftest.c —— per-CPU vs 裸全域計數器，在多核下的差異
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/percpu.h>
#include <linux/kthread.h>
#include <linux/cpumask.h>
#include <linux/delay.h>

#define ITERS_PER_THREAD 1000000UL

static unsigned long global_count;              // 裸全域：無任何保護
static DEFINE_PER_CPU(unsigned long, cpu_count); // per-CPU：各 CPU 一份

static struct task_struct **workers;
static int nr_workers;

static int worker_fn(void *arg)
{
    unsigned long i;

    for (i = 0; i < ITERS_PER_THREAD; i++) {
        global_count++;                         // ← 危險：多 CPU 同改一份，會撕裂/丟更新
        this_cpu_inc(cpu_count);                // ← 安全：各改各的那份，無 race
    }

    /* 等被 kthread_stop 叫停 */
    while (!kthread_should_stop())
        schedule();
    return 0;
}

static int __init perftest_init(void)
{
    int cpu, idx = 0;
    unsigned long percpu_total = 0;
    unsigned long expected;

    nr_workers = num_online_cpus();
    workers = kmalloc_array(nr_workers, sizeof(*workers), GFP_KERNEL);
    if (!workers)
        return -ENOMEM;

    /* 每個 online CPU 綁一個 worker，逼它們真的在不同核心上同時跑 */
    for_each_online_cpu(cpu) {
        struct task_struct *t;

        t = kthread_create(worker_fn, NULL, "perftest/%d", cpu);
        if (IS_ERR(t)) {
            workers[idx] = NULL;
            continue;
        }
        kthread_bind(t, cpu);                   // 釘在這個 CPU
        workers[idx++] = t;
        wake_up_process(t);
    }
    nr_workers = idx;

    /* 給 worker 一點時間把迴圈跑完 */
    msleep(2000);

    /* 停掉所有 worker */
    for (idx = 0; idx < nr_workers; idx++)
        if (workers[idx])
            kthread_stop(workers[idx]);

    /* per-CPU 加總：走一遍所有可能的 CPU */
    for_each_possible_cpu(cpu)
        percpu_total += per_cpu(cpu_count, cpu);

    expected = (unsigned long)nr_workers * ITERS_PER_THREAD;

    pr_info("perftest: workers=%d, each did %lu iters\n",
            nr_workers, ITERS_PER_THREAD);
    pr_info("perftest: expected total   = %lu\n", expected);
    pr_info("perftest: global_count     = %lu  (少了 %lu，就是丟掉的更新)\n",
            global_count, expected - global_count);
    pr_info("perftest: percpu total     = %lu  (精準)\n", percpu_total);

    kfree(workers);
    return 0;
}

static void __exit perftest_exit(void)
{
    pr_info("perftest: bye\n");
}

module_init(perftest_init);
module_exit(perftest_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("per-CPU vs unprotected global counter race demo");
```

用 Ch 0 的 Makefile 編（`KDIR` 指向你的 6.12 源碼樹），把 `perftest.ko` 放進 initramfs，在 QEMU 裡跑——**注意：要給 QEMU 多顆 CPU 才看得到 race**：

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512M \
    -smp 4                    # ← 給 4 顆 CPU，單核看不到多核 race
```

```
/ # insmod /perftest.ko
/ # dmesg | tail
perftest: workers=4, each did 1000000 iters
perftest: expected total   = 4000000
perftest: global_count     = 1372589  (少了 2627411，就是丟掉的更新)
perftest: percpu total     = 4000000  (精準)
```

`global_count` 少了一大截——每次 `global_count++` 的「讀-加-寫」被別的 CPU 交錯打斷，覆蓋掉彼此的更新，這就是 lost update。而 per-CPU 那份**精準命中** `4000000`，因為四個 CPU 各改各的、最後才加總，全程無競爭。

> 具體少多少每次都不一樣（race 本來就不確定）；重點是「裸全域一定少、per-CPU 一定準」。如果你把 `-smp 4` 改回 `-smp 1`（單核），`global_count` 反而會準——因為單核沒有「兩 CPU 同改」，這恰好證明 race 來自 SMP 並行。這也是 race bug 難抓的原因：**開發時單核不出錯，上了多核生產機才炸。**

## 對比與取捨：per-CPU vs 全域+鎖 vs atomic

同樣要一個「多方更新的計數器」，你有三條路。它們的取捨是 kernel 並行設計的縮影：

| 方案 | 寫入成本 | 讀「全局值」成本 | 一致性 | 適用場景 |
|---|---|---|---|---|
| **全域 + spinlock** | 高（搶鎖、序列化、cache bouncing） | 低（讀一份，加讀鎖） | 強（隨時精確一致） | 更新不頻繁、需要即時精確、或更新伴隨複雜邏輯 |
| **全域 atomic**（Ch 24） | 中（一條 atomic 指令，但仍是同一 cache line 全核搶） | 低（原子讀一份） | 強（每次操作原子） | 單純計數、需要即時精確值、更新中等頻繁 |
| **per-CPU** | 極低（各寫各的，零競爭） | 高（要走 `for_each_possible_cpu` 加總） | 弱（加總瞬間非精確一致） | 高頻更新、只偶爾讀總和、能容忍近似 |

讀法：**寫得多、讀總和少、不要求即時精確 → per-CPU 完勝**（統計、freelist、run queue）。**要求即時精確、寫得不算多 → atomic 或鎖**。三者不是誰取代誰，而是按「讀寫比 + 一致性需求 + 可擴展性」選。這張表你在 Ch 24（atomic）、Ch 25（spinlock）會反覆回來對照。

kernel 的一個典型高階組合是 **per-CPU 平時快、偶爾聚合校正**：像記憶體統計 `vmstat`（`mm/vmstat.c`）平時各 CPU 更新自己的 per-CPU 差量，週期性或讀取時才 fold 進全域值——寫路徑走 per-CPU 的免鎖快車道，讀路徑接受一點延遲換取寫入可擴展。

## 踩雷集錦

1. **錯誤直覺：「`this_cpu_inc` 之間可以安心穿插邏輯」**。→ 正確：每個 `this_cpu_*` 單獨看是安全的，但**兩個 `this_cpu_*` 之間**可能被搶佔換 CPU。要「讀出來→算→寫回」這種多步且不能換 CPU 的操作，用 `get_cpu_var`/`put_cpu_var`（或 `get_cpu_ptr`/`put_cpu_ptr`）把整段關搶佔包起來。

2. **錯誤直覺：「per-CPU 什麼並行都擋」**。→ 正確：per-CPU 消滅的是**跨 CPU 的並行**，不擋**同一 CPU 上的中斷插入**。若中斷處理常式也碰同一個 per-CPU 變數，而你在做多步更新，仍會 race——那要關中斷（`local_irq_save`，Ch 29），不是靠 per-CPU。

3. **錯誤直覺：「拿到 `this_cpu_ptr` 存起來，之後隨時用」**。→ 正確：per-CPU 指標**只在拿到它的那個 CPU、且沒被搬走的期間有效**。搶佔開著時，你存起來的指標下一刻可能就指向別人那份了。指標拿了要嘛立刻用完，要嘛在關搶佔區間內用。

4. **錯誤直覺：「for_each_possible_cpu 加總拿到的是精確全局值」**。→ 正確：加總過程中別的 CPU 還在改它們那份，你拿到的是**近似快照**，不是某個時間點的精確一致值。統計用途沒關係，但別拿它做需要精確一致的決策。

5. **錯誤直覺：「單核測過沒事就沒事」**。→ 正確：SMP race 在單核（`-smp 1`）往往**測不出來**，因為沒有「兩 CPU 同時改」。務必用 `-smp N`（N≥2）測並行 code。這正是 race bug 惡名昭彰的原因：開發機/CI 沒重現，上多核生產機才爆。

## 進階：再往深一層

- **per-CPU 的 cache line 對齊**：run queue 用的是 `DEFINE_PER_CPU_SHARED_ALIGNED`（`include/linux/percpu-defs.h`），把變數對齊到 cache line，避免 **false sharing**——兩個邏輯上獨立的 per-CPU 變數若擠在同一條 cache line，一個 CPU 改自己那份會讓別 CPU 的 cache line 失效，白白 bouncing。這牽扯 cache coherence，Ch 23 詳談。per-CPU 本來是為了避免共享，false sharing 會把好處吃掉。

- **`this_cpu_*` 在 x86 用 `%gs` 段暫存器**：x86_64 把當前 CPU 的 per-CPU 區域基底放在 `%gs`，所以 `this_cpu_inc` 能編成單條 `incq %gs:offset`，不需要先讀 `smp_processor_id()` 再算位址。ARM64 沒有這種段前綴，用一個專用暫存器（`TPIDR_EL1`）存基底、多幾條指令算 offset——這是本章少數的架構差異，效能特性略不同但語意一致。

- **`smp_processor_id()` 為什麼在可搶佔 context 要小心**：它回傳「當前 CPU 編號」，但如果你在可搶佔且沒關搶佔的地方呼叫，讀完下一刻就可能被換到別的 CPU，這個編號立刻過時。kernel 有 `CONFIG_DEBUG_PREEMPT` 會在這種誤用時警告（`BUG: using smp_processor_id() in preemptible`）。這也是為什麼 per-CPU 的正解是 `this_cpu_*`/`get_cpu_var`，而不是「`smp_processor_id()` 算 index 再存取陣列」。

- **面試常問**：「per-CPU 變數為什麼能免鎖？代價是什麼？」——答：資料不共享所以無需序列化（免鎖），代價是讀全局值要加總且非即時一致，且存取期間必須釘住 CPU（關搶佔）。能把「免鎖靠不共享、不共享靠釘 CPU」這條因果講清楚，就答到點上了。

## 動手練習

1. **復現並解釋 race**：把上面模組跑起來，分別用 `-smp 1` 和 `-smp 4` 各跑三次。記錄 `global_count` 每次的值。回答：為什麼單核精準、多核每次都不同且偏少？（提示：把 `global_count++` 拆成「load / add / store」三步，畫兩個 CPU 交錯執行的時間線。）

2. **用 gdb 看 per-CPU 存取**：`break perftest_init`，在 `this_cpu_inc(cpu_count)` 對應的位置停下，反組譯（`disassemble`）看 `this_cpu_inc` 編成什麼指令，確認 x86 上是 `%gs` 段前綴那條。對照 `global_count++` 編成什麼（普通 load/add/store）。

3. **把裸全域換成 atomic 先預習 Ch 24**：把 `global_count` 改成 `atomic_long_t`、`global_count++` 改成 `atomic_long_inc(&global_count)`，重跑 `-smp 4`。它現在應該也精準了。思考：atomic 版精準，但它和 per-CPU 版在**寫入的可擴展性**上差在哪？（提示：atomic 仍是所有 CPU 搶同一條 cache line。）

4. **弄壞 per-CPU**：把 `this_cpu_inc(cpu_count)` 故意改成「`unsigned long *p = this_cpu_ptr(&cpu_count); schedule(); (*p)++;`」——在拿指標和用指標之間插一個 `schedule()`（可能換 CPU）。開 `CONFIG_DEBUG_PREEMPT` 重編模組並載入，觀察 kernel 是否抱怨。理解為什麼這樣寫破了 per-CPU 的前提。

## 本章重點整理

- kernel 天生高度並行：**SMP（多核同跑）+ preemption（隨時被換下）+ 中斷（隨時插入）** 三者疊加，使用者空間單執行緒直覺完全失效。寫 kernel code 的預設心態是「這段會被誰同時碰」。
- **裸全域變數在多核下會 race**：`count++` 是「讀-加-寫」三步，兩 CPU 交錯就丟更新（lost update）。這是 Part 4（atomic/鎖/RCU）要解的核心問題。
- **per-CPU 變數靠「不共享」免鎖**：每 CPU 一份副本各改各的（`DEFINE_PER_CPU` + `this_cpu_*`），要全局值再 `for_each_possible_cpu` 加總。前提是存取期間釘住 CPU——單步用 `this_cpu_*` 自帶安全，多步用 `get_cpu_var`/`put_cpu_var` 關搶佔。
- **選型**：高頻寫、偶爾讀總和、容忍近似 → per-CPU；要即時精確 → atomic 或鎖。per-CPU 是 run queue（Ch 11）、slub freelist（Ch 18）、統計計數器的共同基礎。

## 自我檢核

- [ ] 不看筆記，能說出 kernel 並行的三個來源，以及為什麼任一個都足以毀掉「循序執行」假設
- [ ] 能解釋為什麼 `count++` 在多核下會丟更新，並畫出兩個 CPU 交錯執行「讀-加-寫」的時間線
- [ ] 能說清楚 per-CPU 為什麼免鎖（不共享），以及它「不擋」什麼（同 CPU 的中斷）
- [ ] 面試被問「`this_cpu_inc` 和 `get_cpu_var`/`put_cpu_var` 什麼時候該用哪個」，你能答對（單步 vs 多步、要不要手動關搶佔）
- [ ] 能說出 per-CPU vs atomic vs 鎖 三者在「寫入成本／讀全局成本／一致性」上的取捨
- [ ] 能獨立寫一個 per-CPU 計數器模組，用 `-smp N` 跑出裸全域 race、per-CPU 精準的對比

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/this_cpu_ops.rst](https://www.kernel.org/doc/html/latest/core-api/this_cpu_ops.html)** — kernel 官方
  - **讀哪裡**：整篇。`this_cpu_*` 全套操作的權威說明，講清楚哪些操作對「本 CPU 的搶佔/中斷」是原子的、哪些不是
  - **和本章的關聯**：本章「單步用 `this_cpu_*`、多步用 `get_cpu_var`」的判準，源頭就是這篇；用的時候查它準沒錯

- **[Documentation/core-api/local_ops.rst](https://www.kernel.org/doc/html/latest/core-api/local_ops.html)** — kernel 官方
  - **能學到什麼**：`local_t`——比 per-CPU 更細一層的「只對本 CPU 原子」的操作，用在中斷處理常式和被中斷的 code 共用同一 per-CPU 變數時
  - **前提**：先懂本章 per-CPU 與「per-CPU 不擋中斷」這個限制，再讀這篇才知道 `local_t` 補的是哪個洞

### 文章

- **[Per-CPU variables and the realtime tree](https://lwn.net/Articles/674979/)** — LWN.net, Jonathan Corbet
  - **讀哪裡**：前半對 per-CPU 語意與 `get_cpu_var` 搶佔問題的討論
  - **為什麼值得讀**：從 realtime kernel 的角度看 per-CPU「關搶佔」帶來的延遲問題，讓你理解這個免鎖技巧不是零成本，在 -rt 場景（Ch 31）有取捨

- **[Bootlin Elixir：`percpu-defs.h`](https://elixir.bootlin.com/linux/v6.12/source/include/linux/percpu-defs.h)** — Bootlin
  - **這是什麼**：直接讀 `DEFINE_PER_CPU`、`this_cpu_*`、`get_cpu_var`/`put_cpu_var` 的定義，看巨集怎麼展開、`get_cpu_var` 裡的 `preempt_disable()` 長什麼樣
  - **為什麼值得讀**：本章講的機制，源碼比任何轉述都準；配 `mm/percpu.c` 的 `pcpu_alloc` 看動態配置怎麼實作

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **讀哪裡**：Chapter 12「Memory Management」的 per-CPU 一節，以及 Chapter 9–10 的並行/同步導論
  - **這本書的定位**：把「為什麼 kernel 需要 per-CPU、它在同步大圖裡的位置」講得最白話；概念以本章 6.12 API 為準，書中舊 API（如 `get_cpu`）語意仍適用

你現在有了「kernel 高度並行、共享狀態會 race」的痛，也有了 per-CPU 這個「靠不共享來免鎖」的第一件武器。但很多資料無法 per-CPU（就是得共享），這時就要真正的同步原語。下一章先把模組載入的底層機制補完（符號怎麼解析、`module_init` 怎麼被呼叫），然後 Part 4 會回到本章埋下的 race，給你 atomic、spinlock、RCU 這些正面解法。

→ [Ch 8 模組載入底層：finit_module、符號解析、簽署、initcall](./08-module-loading.md)
