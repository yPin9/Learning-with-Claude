# Final Project — 核心模組套件（ksuite）

> **目標**：把整門課 54 章 + 6 練習學到的東西，收斂成一套**有實質功能、多模組協同**的核心工具套件。你要做一個「可觀測、可控制的迷你系統監控/資源管理核心套件」，跨過 process/排程、記憶體/VFS、同步、char device、tracepoint、網路、debug 至少七個 Part。全程 QEMU + gdb 驗證，卸載無 leak、lockdep 乾淨、rmmod 不崩。做完這個，你就從「讀懂 kernel 子系統」跨到「能寫出協同多子系統的真實核心程式」。

> **這是壓軸，不是練習題**。前面六個練習（A–F）每個只碰一兩個子系統；這個專案要求你把它們黏成一個系統。預期投入時間 15–30 小時，分階段完成。程式碼量約 800–1200 行 C，跨 3–4 個模組。

---

## 專案總覽與學習目標

我們要做的東西叫 **ksuite**（kernel suite），一套「迷你系統觀測與控制中樞」。想像一個超輕量的 `htop` + `bpftrace` + 資源限制器，但**全部在 kernel 裡**，透過一個統一的 char device 介面對 userspace 開放。

它由四個模組組成，共用一個核心模組匯出的資料結構與 API：

- **ksuite_core（模組 1）**——套件的骨幹。註冊一個 misc device（Ch 38），提供 ioctl + procfs + sysfs（Ch 37）三種 userspace 介面。維護一個「被觀測事件」的環形緩衝區與統計狀態，用 spinlock/RCU（Ch 25/27）保護。用 `EXPORT_SYMBOL_GPL`（Ch 8）把記錄事件的 API 匯出給其他模組。
- **ksuite_sched（模組 2）**——排程觀測器。掛 `sched_switch`/`sched_process_fork` tracepoint（Ch 51），走 `task_struct`（Ch 9），統計 per-CPU（Ch 7）的 context switch 次數與 fork 事件，把事件送進 core 的緩衝區。
- **ksuite_mem（模組 3）**——記憶體/VFS 觀測器。給一個 PID，走它的 `mm_struct`/VMA/page table（Ch 16/19，練習 C 的延伸），統計 RSS、VMA 數、已映射 page 數；或掛 VFS tracepoint 統計 IO。
- **ksuite_net（模組 4，進階/選做）**——網路 hook。掛一個 netfilter hook（Ch 46）計數進出封包，按 protocol 分類，送進 core。

**你完成後應該能**：

1. 設計並實作跨多個 kernel 子系統、透過匯出符號協作的核心模組群
2. 在有並行的情境下正確選擇並使用鎖（spinlock vs mutex vs RCU），並用 lockdep 證明它乾淨
3. 保證模組載入/卸載的資源生命週期正確，用 KASAN/kmemleak 證明無 leak（Ch 53）
4. 設計 userspace ↔ kernel 的控制介面（ioctl/procfs/sysfs），理解各自的適用場景
5. 用 gdb 驗證關鍵路徑（事件如何從 tracepoint 流到 userspace）

---

## 需求規格

### 功能需求（FR）

| 編號 | 需求 | 對應章 |
|---|---|---|
| FR-1 | `ksuite_core` 註冊一個 `/dev/ksuite` misc device，支援 `open/release/read/ioctl` | Ch 38 |
| FR-2 | 提供 `/proc/ksuite/stats`（人類可讀統計）與 `/sys/kernel/ksuite/` 下的可調參數 | Ch 37 |
| FR-3 | core 維護一個固定大小的事件環形緩衝區；其他模組透過匯出 API `ksuite_record_event()` 塞事件 | Ch 5/6/8 |
| FR-4 | userspace 可透過 `read(/dev/ksuite)` 拉出事件、透過 ioctl 重設統計/切換開關 | Ch 4/38 |
| FR-5 | `ksuite_sched` 掛 `sched_switch` 統計 per-CPU context switch，掛 `sched_process_fork` 記錄 fork | Ch 7/9/51 |
| FR-6 | `ksuite_mem` 接受一個 PID，回報該 process 的 RSS / VMA 數 / 已映射 page 數 | Ch 16/19 |
| FR-7 | `ksuite_net`（選做）掛 netfilter LOCAL_IN/LOCAL_OUT hook，按 protocol 計數封包 | Ch 46 |
| FR-8 | 附一個 userspace 控制程式 `ksuitectl`，能觀測即時事件、下 ioctl 命令、查各模組統計 | Ch 4/38 |

### 非功能需求（NFR）——這些是評分的硬骨頭

| 編號 | 需求 | 怎麼驗證 |
|---|---|---|
| NFR-1 | **無記憶體洩漏**：任意順序載入/卸載所有模組後，`kmemleak` 與 KASAN 皆乾淨 | `echo scan > /sys/kernel/debug/kmemleak` |
| NFR-2 | **lockdep 乾淨**：跑滿載測試後 `dmesg` 無 lockdep 警告、無 `possible deadlock` | 開 `CONFIG_PROVE_LOCKING`，壓測 |
| NFR-3 | **rmmod 不崩、順序無關**：模組依賴正確，被依賴的不能先卸載；tracepoint/hook 全部反註冊 | 反覆 insmod/rmmod 各種順序 |
| NFR-4 | **並行安全**：多個 CPU 同時記錄事件、userspace 同時 read，不丟資料不 corrupt | `-smp 4` + 多 reader 壓測 |
| NFR-5 | **不在原子上下文睡眠**：tracepoint callback 跑在原子/中斷關閉上下文，裡面用的鎖與配置要合法 | lockdep + `might_sleep` 檢查 |
| NFR-6 | **關鍵路徑可用 gdb 觀測**：能停在 `ksuite_record_event`，看事件從 producer 流到 buffer | gdb `b ksuite_record_event` |

> **NFR-5 是這個專案最容易翻車的地方**。`sched_switch` 的 tracepoint callback 跑在**持有 runqueue 鎖、關搶佔**的上下文裡（Ch 14/25）。你在裡面**不能** `mutex_lock`、不能 `kmalloc(GFP_KERNEL)`（會睡）、不能做任何可能 schedule 的事。這條約束會逼你做出正確的設計決策——它不是刁難，它就是真實 kernel 開發每天在處理的事。

---

## 系統架構圖

```
        userspace                                       kernel
  ┌──────────────────┐
  │    ksuitectl     │   ioctl / read / open
  │  (控制 + 觀測)    │────────────┐
  └──────────────────┘            │
        │   │                     ▼
  cat /proc/ksuite/stats    ┌─────────────────────────────────────────────┐
        │   │               │            ksuite_core（模組 1）             │
  cat /sys/kernel/ksuite/*  │  ┌──────────────────────────────────────┐   │
        │   └──────────────►│  │  misc device /dev/ksuite (Ch38)      │   │
        └──────────────────►│  │  procfs /proc/ksuite (Ch37)          │   │
                            │  │  sysfs  /sys/kernel/ksuite (Ch37)    │   │
                            │  └──────────────────────────────────────┘   │
                            │  ┌──────────────────────────────────────┐   │
                            │  │  event ring buffer  (Ch5/6)          │   │
                            │  │  spinlock 保護寫入 (Ch25)            │   │
                            │  │  per-CPU 統計 (Ch7)                  │   │
                            │  │  wait_queue 喚醒 read (Ch26)         │   │
                            │  └──────────────────────────────────────┘   │
                            │        ▲ EXPORT_SYMBOL_GPL(Ch8)              │
                            │        │ ksuite_record_event()              │
                            └────────┼───────────────┬─────────────┬──────┘
                                     │               │             │
                        ┌────────────┴───┐  ┌────────┴──────┐  ┌───┴────────────┐
                        │ ksuite_sched   │  │ ksuite_mem    │  │ ksuite_net     │
                        │ (模組 2)       │  │ (模組 3)      │  │ (模組 4 選做)  │
                        │ sched_switch   │  │ walk mm/VMA   │  │ netfilter hook │
                        │ fork tracepoint│  │ page table    │  │ 封包計數       │
                        │ (Ch7/9/51)     │  │ (Ch16/19)     │  │ (Ch46)         │
                        └────────────────┘  └───────────────┘  └────────────────┘
```

依賴方向：`sched`/`mem`/`net` 都依賴 `core` 匯出的符號。所以載入順序必須 **core 先**，卸載順序 **core 最後**。module refcount（Ch 8）會自動幫你擋——只要 sched 還在用 core 匯出的符號，`rmmod ksuite_core` 就會失敗（`Module ksuite_core is in use`）。這是 kernel 的符號依賴機制在替你把關 NFR-3。

---

## 分模組實作指引

### 模組 1：ksuite_core——骨幹與介面

**要達成**：一個 misc device + procfs + sysfs + 環形緩衝區 + 匯出 API。這是套件的地基，先把它做到能獨立運作、userspace 能讀能寫，再讓別的模組往裡塞資料。

**用到哪幾章**：Ch 38（misc device）、Ch 37（procfs/sysfs/kobject）、Ch 5（環形緩衝區用陣列 + head/tail 索引，或 `kfifo`）、Ch 6（`kmalloc`/`kzalloc` 配 buffer）、Ch 25（spinlock 保護寫入）、Ch 26（wait_queue 讓 blocking read 睡+被喚醒）、Ch 4/38（ioctl 命令設計）、Ch 8（`EXPORT_SYMBOL_GPL`）。

**關鍵 API 與設計決策**：

- `misc_register()` / `misc_deregister()`——比自己管 major/minor 省事，misc device 共用 major 10，你只填 `.minor = MISC_DYNAMIC_MINOR`。
- 環形緩衝區選型：可以手刻 `head`/`tail` 索引 + 固定陣列（Ch 5 的資料結構功底），或直接用 `kfifo`（kernel 內建的 lockless SPSC，但多 producer 時仍要外鎖）。**建議手刻**——這是展示你懂並行的地方。
- **鎖的選擇**：寫入 buffer 的路徑會被 `sched_switch` callback（原子上下文）呼叫，所以保護 buffer 的鎖**必須是 spinlock，而且要 `spin_lock_irqsave`**（Ch 25）——因為記錄可能發生在關中斷的路徑上。**不能用 mutex**（會睡，NFR-5 死當）。
- read 路徑：userspace `read()` 時 buffer 空就睡（`wait_event_interruptible`，Ch 26），有事件時 producer `wake_up`。這是經典的 producer/consumer。
- ioctl 命令用 `_IO`/`_IOR`/`_IOW` 巨集定義（Ch 4/38），至少要有 `KSUITE_RESET`（清統計）、`KSUITE_ENABLE`/`KSUITE_DISABLE`（開關記錄）、`KSUITE_GET_STATS`（拉統計 struct）。

### 模組 2：ksuite_sched——排程觀測器

**要達成**：掛 `sched_switch` 統計每顆 CPU 的 context switch 次數，掛 `sched_process_fork` 把新 process 的 fork 事件送進 core。

**用到哪幾章**：Ch 51（tracepoint 註冊：`register_trace_sched_switch`）、Ch 7（per-CPU 變數存每顆 CPU 的計數）、Ch 9（從 `task_struct` 取 comm/pid）、Ch 14（理解 callback 跑在什麼上下文——持 rq lock、關搶佔）。

**關鍵 API**：

- `register_trace_sched_switch(probe_fn, NULL)` / `unregister_trace_sched_switch(...)`——需要 `CONFIG_TRACEPOINTS`（defconfig 有）。probe 函式簽名要精確對上 tracepoint 定義（`include/trace/events/sched.h` 的 `TP_PROTO`），簽名錯會編不過或抓不到。
- **per-CPU 計數**用 `DEFINE_PER_CPU(u64, ctxsw_count)` + `this_cpu_inc()`（Ch 7）。callback 在關搶佔上下文跑，`this_cpu_*` 正好合法且無鎖——這是 per-CPU 的殺手級用途。
- fork 事件要送進 core buffer，呼叫 `ksuite_record_event()`。**記住 NFR-5**：這個呼叫鏈最終會拿 core 的 spinlock，合法；但你**不能在裡面 kmalloc GFP_KERNEL、不能 mutex**。

### 模組 3：ksuite_mem——記憶體觀測器

**要達成**：給一個 PID，走它的 `mm_struct` → VMA → page table，回報 RSS、VMA 數、已映射 page 數。這是練習 C（page table walker）的直接延伸，但這次結果要透過 core 的介面輸出。

**用到哪幾章**：Ch 16（page table walk：`pgd→p4d→pud→pmd→pte`）、Ch 19（`mm_struct`/VMA：`find_vma`、`vma_iterator`、`mm->mmap_lock`）、Ch 9（`get_pid_task` 從 PID 拿 task）。

**關鍵 API 與陷阱**：

- 從 PID 拿 task：`pid = find_get_pid(nr); task = get_pid_task(pid, PIDTYPE_PID);`——**成對釋放** `put_pid` / `put_task_struct`，漏了就是 refcount leak（NFR-1）。
- 拿 mm：`mm = get_task_mm(task)`（會 pin 住 mm，`mmput` 釋放）。kernel thread 沒有 mm，要判 NULL。
- 走 VMA 要持 `mmap_read_lock(mm)`（Ch 19，6.x 用 `mmap_lock` 取代舊 `mmap_sem`），走完 `mmap_read_unlock`。**在持 mmap_lock 時不能做會睡太久的事**。
- page table walk 建議用 kernel 提供的 `walk_page_range()`（`mm/pagewalk.c`）而非自己手刻五層——手刻教學價值高（練習 C 做過），但正式做法用 walker API 更穩。RSS 也可以直接讀 `get_mm_rss(mm)`，但自己走 pte 數 present page 更能證明你懂。

### 模組 4：ksuite_net——網路 hook（選做/進階）

**要達成**：掛 netfilter LOCAL_IN/LOCAL_OUT hook 計數封包，按 protocol（TCP/UDP/ICMP）分類，送進 core。

**用到哪幾章**：Ch 46（netfilter hook：`nf_register_net_hook`）、Ch 43（`sk_buff`：從 `skb` 取 IP header）。

**關鍵 API**：`struct nf_hook_ops` 填 `.hook`/`.pf = NFPROTO_IPV4`/`.hooknum = NF_INET_LOCAL_IN`/`.priority`，`nf_register_net_hook(&init_net, &ops)` 註冊。hook 函式回傳 `NF_ACCEPT`（只計數不擋）。從 `ip_hdr(skb)->protocol` 分類。這條路徑跑在 softirq（Ch 30），一樣是原子上下文，鎖規則同 sched。

---

## 里程碑：分階段完成

不要想一次寫完四個模組。按這個順序，每個里程碑結束都應該是「可載入、可驗證、不崩」的狀態：

**M0 — core 空殼能動**（~2h）：`ksuite_core` 只註冊 misc device + 一個假的 `/proc/ksuite/stats`（回傳固定字串）。目標：`insmod` 後 `cat /dev/ksuite` 不崩、`cat /proc/ksuite/stats` 有輸出、`rmmod` 乾淨。這一步驗證你的 Makefile、多模組 build、QEMU 載入流程都通。

**M1 — core 有真的緩衝區 + 匯出 API**（~3h）：實作環形緩衝區、`ksuite_record_event()` 並匯出、blocking `read`、ioctl。寫一個小測試在 core 自己的 init 裡塞幾筆假事件，確認 userspace read 得到。這一步驗證 producer/consumer + 鎖 + wait_queue。

**M2 — sched 模組接上**（~3h）：掛 `sched_switch`（先只 per-CPU 計數，不送 core），確認 `/proc` 統計會動。再掛 fork，把事件送進 core，確認 `ksuitectl` read 得到 fork 事件。這一步是第一次跨模組協作（EXPORT_SYMBOL）。

**M3 — mem 模組接上**（~3h）：ioctl 傳一個 PID 進 mem 模組，回報它的 RSS/VMA。先用 `get_mm_rss` 拿現成的，再進階到自己走 pte。

**M4 — 加同步壓力測試**（~2h）：`-smp 4` 開機，多個 reader 同時 read，跑 fork 炸彈製造大量 sched 事件，確認不丟不崩。開 lockdep 看有沒有警告。

**M5 — 加固 + 網路模組（選做）**（~3h+）：跑 KASAN/kmemleak，修所有 leak；反覆各種順序 insmod/rmmod；加 `ksuite_net`。這一步是把 NFR 全部打勾。

> 每個里程碑做完，**commit 一次**。這樣某個里程碑把 kernel 弄崩了，你能退回上一個能動的狀態。這也是真實 kernel 開發的做法——小步、可驗證、可回退。

---

## 驗收清單

### 功能驗收

- [ ] `insmod ksuite_core.ko` 後 `/dev/ksuite`、`/proc/ksuite/stats`、`/sys/kernel/ksuite/` 三者都存在
- [ ] `ksuitectl` 能 `read` 到事件、能下 `KSUITE_RESET`/`ENABLE`/`DISABLE` ioctl
- [ ] `insmod ksuite_sched.ko` 後，跑幾個指令，`/proc/ksuite/stats` 的 per-CPU context switch 計數會增加
- [ ] fork 一個 process，`ksuitectl` 能看到對應的 fork 事件（含新 PID + comm）
- [ ] `ksuitectl mem <pid>` 回報的 RSS 與 `/proc/<pid>/status` 的 `VmRSS` 數量級一致
- [ ]（選做）`ksuite_net` 載入後，`ping` 一個位址，ICMP 計數增加

### 品質驗收（對照 NFR）

- [ ] **NFR-1**：所有模組載入→操作→卸載後，`echo scan > /sys/kernel/debug/kmemleak` 且 `cat` 該檔無新 leak；KASAN 無 use-after-free/double-free 報告
- [ ] **NFR-2**：壓測後 `dmesg | grep -i "lockdep\|deadlock\|WARNING"` 無輸出（需 `CONFIG_PROVE_LOCKING`）
- [ ] **NFR-3**：`rmmod ksuite_core`（在 sched 還載入時）**失敗**並提示 in use；正確順序卸載全部成功、無殘留 tracepoint/hook（再 insmod 一次不會重複註冊爆炸）
- [ ] **NFR-4**：`-smp 4` + 多 reader + fork 壓力下，read 到的事件無 corrupt（序號連續、struct 完整）
- [ ] **NFR-5**：`dmesg` 無 `sleeping function called from invalid context` / `BUG: scheduling while atomic`
- [ ] **NFR-6**：gdb `b ksuite_record_event`，觸發一個事件，能停下並 `bt` 看到完整 producer 呼叫鏈

---

## 完整參考實作

下面給出模組 1（core）與模組 2（sched）的**完整可編譯程式碼**，加 Makefile、userspace `ksuitectl`、QEMU 執行步驟。模組 3（mem）給關鍵函式骨架。**先自己做，卡住再看**——直接抄等於沒學。

<details>
<summary>點開看完整參考實作（ksuite_core.c + ksuite_sched.c + ksuite_mem.c 骨架 + Makefile + ksuitectl.c + QEMU 步驟）</summary>

### `ksuite.h`（共用標頭，userspace 與 kernel 都 include ioctl 定義）

```c
/* ksuite.h — 共用定義：ioctl 命令 + 事件格式 + 匯出 API 宣告 */
#ifndef _KSUITE_H
#define _KSUITE_H

#include <linux/ioctl.h>

#define KSUITE_MAGIC 'K'

/* 事件型別 */
enum ksuite_evt_type {
	KSUITE_EVT_FORK = 1,
	KSUITE_EVT_EXEC = 2,
	KSUITE_EVT_NET  = 3,
};

/* 一筆事件：固定大小，方便環形緩衝與 userspace read */
struct ksuite_event {
	__u64 seq;          /* 全域序號，reader 用來偵測丟包 */
	__u64 ts_ns;        /* 時間戳（ktime） */
	__u32 type;         /* enum ksuite_evt_type */
	__u32 cpu;          /* 產生事件的 CPU */
	__s32 pid;
	__s32 aux;          /* 依 type 而定：fork=parent pid，net=protocol */
	char  comm[16];     /* TASK_COMM_LEN */
};

/* userspace 拉統計用的 struct */
struct ksuite_stats {
	__u64 total_events;
	__u64 dropped;               /* buffer 滿而丟棄的事件數 */
	__u64 ctxsw_per_cpu[64];     /* 最多 64 CPU，超過截斷 */
	__u32 nr_cpus;
	__u32 enabled;
};

#define KSUITE_RESET      _IO(KSUITE_MAGIC, 1)
#define KSUITE_ENABLE     _IO(KSUITE_MAGIC, 2)
#define KSUITE_DISABLE    _IO(KSUITE_MAGIC, 3)
#define KSUITE_GET_STATS  _IOR(KSUITE_MAGIC, 4, struct ksuite_stats)

#ifdef __KERNEL__
/* 匯出給其他模組的 producer API（Ch 8）*/
void ksuite_record_event(u32 type, s32 pid, s32 aux, const char *comm);
void ksuite_bump_ctxsw(int cpu);
bool ksuite_enabled(void);
#endif

#endif /* _KSUITE_H */
```

### `ksuite_core.c`（模組 1，完整）

```c
/* ksuite_core.c — 骨幹：misc device + procfs + sysfs + 環形緩衝 + 匯出 API */
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/wait.h>
#include <linux/sched.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/kobject.h>
#include <linux/sysfs.h>
#include <linux/ktime.h>
#include <linux/percpu.h>
#include <linux/atomic.h>
#include "ksuite.h"

#define KSUITE_RING_SIZE 1024        /* 必須是 2 的次方，方便 & 遮罩取索引 */
#define KSUITE_RING_MASK (KSUITE_RING_SIZE - 1)

/* ---- 共享狀態，全部由 ring_lock 保護（除了 per-CPU 與 atomic）---- */
static struct ksuite_event *ring;    /* 環形緩衝，kmalloc 配置 */
static u32 ring_head;                 /* producer 寫入位置 */
static u32 ring_tail;                 /* consumer 讀取位置 */
static DEFINE_SPINLOCK(ring_lock);    /* 保護 ring/head/tail（Ch 25）*/
static DECLARE_WAIT_QUEUE_HEAD(ring_wq); /* read 空時睡這裡（Ch 26）*/

static atomic64_t g_seq = ATOMIC64_INIT(0);
static atomic64_t g_total = ATOMIC64_INIT(0);
static atomic64_t g_dropped = ATOMIC64_INIT(0);
static bool g_enabled = true;

/* per-CPU context switch 計數（Ch 7），由 sched 模組透過 bump 更新 */
static DEFINE_PER_CPU(u64, ctxsw_count);

/* ---- 匯出 API：producer 端（Ch 8）---- */

bool ksuite_enabled(void)
{
	return READ_ONCE(g_enabled);
}
EXPORT_SYMBOL_GPL(ksuite_enabled);

void ksuite_bump_ctxsw(int cpu)
{
	/* 呼叫者已在該 CPU 的原子上下文，用 this_cpu 無鎖遞增 */
	this_cpu_inc(ctxsw_count);
}
EXPORT_SYMBOL_GPL(ksuite_bump_ctxsw);

/*
 * 核心 producer：把一筆事件塞進環形緩衝。
 * 關鍵：可能被 sched_switch callback（原子、關中斷）呼叫，
 * 所以用 spin_lock_irqsave，且全程不睡、不 kmalloc GFP_KERNEL。
 */
void ksuite_record_event(u32 type, s32 pid, s32 aux, const char *comm)
{
	unsigned long flags;
	struct ksuite_event *e;

	if (!READ_ONCE(g_enabled))
		return;

	spin_lock_irqsave(&ring_lock, flags);

	/* buffer 滿：head 追上 tail。丟最舊的（覆蓋）還是丟新的？
	 * 這裡選「丟新的」——保留已有歷史，計 dropped。 */
	if (((ring_head + 1) & KSUITE_RING_MASK) == ring_tail) {
		atomic64_inc(&g_dropped);
		spin_unlock_irqrestore(&ring_lock, flags);
		return;
	}

	e = &ring[ring_head];
	e->seq   = atomic64_inc_return(&g_seq);
	e->ts_ns = ktime_get_ns();
	e->type  = type;
	e->cpu   = smp_processor_id();
	e->pid   = pid;
	e->aux   = aux;
	if (comm)
		strscpy(e->comm, comm, sizeof(e->comm));
	else
		e->comm[0] = '\0';

	ring_head = (ring_head + 1) & KSUITE_RING_MASK;
	atomic64_inc(&g_total);

	spin_unlock_irqrestore(&ring_lock, flags);

	/* 喚醒睡在 read 的 consumer。wake_up 在原子上下文合法。 */
	wake_up_interruptible(&ring_wq);
}
EXPORT_SYMBOL_GPL(ksuite_record_event);

/* ---- misc device：consumer 端（Ch 38）---- */

/* 從環形緩衝取一筆事件到 out。回傳 true=有資料。持鎖呼叫。 */
static bool ring_pop_locked(struct ksuite_event *out)
{
	if (ring_tail == ring_head)
		return false;
	*out = ring[ring_tail];
	ring_tail = (ring_tail + 1) & KSUITE_RING_MASK;
	return true;
}

static ssize_t ksuite_read(struct file *f, char __user *buf,
			   size_t len, loff_t *off)
{
	struct ksuite_event ev;
	unsigned long flags;
	bool got;

	if (len < sizeof(ev))
		return -EINVAL;

	/* 沒資料就睡，直到 producer 喚醒或收到 signal（Ch 26）*/
	for (;;) {
		spin_lock_irqsave(&ring_lock, flags);
		got = ring_pop_locked(&ev);
		spin_unlock_irqrestore(&ring_lock, flags);
		if (got)
			break;
		if (f->f_flags & O_NONBLOCK)
			return -EAGAIN;
		if (wait_event_interruptible(ring_wq, ring_head != ring_tail))
			return -ERESTARTSYS;   /* 被 signal 打斷 */
	}

	if (copy_to_user(buf, &ev, sizeof(ev)))
		return -EFAULT;
	return sizeof(ev);
}

static void collect_stats(struct ksuite_stats *s)
{
	int cpu;
	memset(s, 0, sizeof(*s));
	s->total_events = atomic64_read(&g_total);
	s->dropped      = atomic64_read(&g_dropped);
	s->enabled      = READ_ONCE(g_enabled);
	s->nr_cpus      = num_possible_cpus();
	for_each_possible_cpu(cpu) {
		if (cpu >= 64)
			break;
		s->ctxsw_per_cpu[cpu] = per_cpu(ctxsw_count, cpu);
	}
}

static long ksuite_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct ksuite_stats st;
	unsigned long flags;
	int cpu;

	switch (cmd) {
	case KSUITE_RESET:
		spin_lock_irqsave(&ring_lock, flags);
		ring_head = ring_tail = 0;
		spin_unlock_irqrestore(&ring_lock, flags);
		atomic64_set(&g_total, 0);
		atomic64_set(&g_dropped, 0);
		atomic64_set(&g_seq, 0);
		for_each_possible_cpu(cpu)
			per_cpu(ctxsw_count, cpu) = 0;
		return 0;
	case KSUITE_ENABLE:
		WRITE_ONCE(g_enabled, true);
		return 0;
	case KSUITE_DISABLE:
		WRITE_ONCE(g_enabled, false);
		return 0;
	case KSUITE_GET_STATS:
		collect_stats(&st);
		if (copy_to_user((void __user *)arg, &st, sizeof(st)))
			return -EFAULT;
		return 0;
	default:
		return -ENOTTY;
	}
}

static const struct file_operations ksuite_fops = {
	.owner          = THIS_MODULE,
	.read           = ksuite_read,
	.unlocked_ioctl = ksuite_ioctl,
	.llseek         = noop_llseek,
};

static struct miscdevice ksuite_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "ksuite",
	.fops  = &ksuite_fops,
	.mode  = 0666,
};

/* ---- procfs：人類可讀統計（Ch 37）---- */

static int ksuite_proc_show(struct seq_file *m, void *v)
{
	struct ksuite_stats st;
	int cpu;

	collect_stats(&st);
	seq_printf(m, "enabled:      %u\n", st.enabled);
	seq_printf(m, "total_events: %llu\n", st.total_events);
	seq_printf(m, "dropped:      %llu\n", st.dropped);
	for_each_possible_cpu(cpu) {
		if (cpu >= 64)
			break;
		seq_printf(m, "cpu%-2d ctxsw:  %llu\n",
			   cpu, st.ctxsw_per_cpu[cpu]);
	}
	return 0;
}

static int ksuite_proc_open(struct inode *ino, struct file *f)
{
	return single_open(f, ksuite_proc_show, NULL);
}

static const struct proc_ops ksuite_proc_ops = {
	.proc_open    = ksuite_proc_open,
	.proc_read    = seq_read,
	.proc_lseek   = seq_lseek,
	.proc_release = single_release,
};

static struct proc_dir_entry *proc_dir;

/* ---- sysfs：可調參數（Ch 37）---- */

static ssize_t enabled_show(struct kobject *k, struct kobj_attribute *a, char *buf)
{
	return sysfs_emit(buf, "%d\n", READ_ONCE(g_enabled));
}
static ssize_t enabled_store(struct kobject *k, struct kobj_attribute *a,
			     const char *buf, size_t n)
{
	bool v;
	if (kstrtobool(buf, &v))
		return -EINVAL;
	WRITE_ONCE(g_enabled, v);
	return n;
}
static struct kobj_attribute enabled_attr = __ATTR_RW(enabled);
static struct kobject *ksuite_kobj;

/* ---- 模組生命週期：注意反向清理順序（Ch 8）---- */

static int __init ksuite_core_init(void)
{
	int ret;

	ring = kcalloc(KSUITE_RING_SIZE, sizeof(*ring), GFP_KERNEL);
	if (!ring)
		return -ENOMEM;

	ret = misc_register(&ksuite_misc);
	if (ret)
		goto err_free;

	proc_dir = proc_mkdir("ksuite", NULL);
	if (!proc_dir) {
		ret = -ENOMEM;
		goto err_misc;
	}
	if (!proc_create("stats", 0444, proc_dir, &ksuite_proc_ops)) {
		ret = -ENOMEM;
		goto err_proc;
	}

	ksuite_kobj = kobject_create_and_add("ksuite", kernel_kobj);
	if (!ksuite_kobj) {
		ret = -ENOMEM;
		goto err_proc;
	}
	ret = sysfs_create_file(ksuite_kobj, &enabled_attr.attr);
	if (ret)
		goto err_kobj;

	pr_info("ksuite_core: loaded, /dev/ksuite ready\n");
	return 0;

err_kobj:
	kobject_put(ksuite_kobj);
err_proc:
	proc_remove(proc_dir);       /* 遞迴移除 proc_dir 下所有 entry */
err_misc:
	misc_deregister(&ksuite_misc);
err_free:
	kfree(ring);
	return ret;
}

static void __exit ksuite_core_exit(void)
{
	/* 反向清理：先關 userspace 介面，再放記憶體。
	 * 到這裡時 sched/mem 模組已卸載（refcount 保證），
	 * 不會再有人呼叫 ksuite_record_event。 */
	sysfs_remove_file(ksuite_kobj, &enabled_attr.attr);
	kobject_put(ksuite_kobj);
	proc_remove(proc_dir);
	misc_deregister(&ksuite_misc);
	kfree(ring);
	pr_info("ksuite_core: unloaded\n");
}

module_init(ksuite_core_init);
module_exit(ksuite_core_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ksuite core: misc device + event ring + exported producer API");
MODULE_AUTHOR("kernel_internals final project");
```

### `ksuite_sched.c`（模組 2，完整）

```c
/* ksuite_sched.c — 掛 sched_switch / sched_process_fork tracepoint */
#include <linux/module.h>
#include <linux/tracepoint.h>
#include <trace/events/sched.h>
#include <linux/sched.h>
#include "ksuite.h"

/*
 * sched_switch probe。簽名必須精確對上 include/trace/events/sched.h 的
 * TRACE_EVENT(sched_switch, TP_PROTO(bool preempt, struct task_struct *prev,
 *            struct task_struct *next, unsigned int prev_state))。
 * v6.12 是這個四參數版本；改版時第一件事就是檢查這個簽名。
 *
 * 這個 callback 跑在持 rq lock、關搶佔的原子上下文（Ch 14/25）：
 * 只能做無鎖或 spinlock_irqsave 的事，絕不能睡。
 */
static void probe_sched_switch(void *data, bool preempt,
			       struct task_struct *prev,
			       struct task_struct *next,
			       unsigned int prev_state)
{
	/* per-CPU 遞增：this_cpu 在關搶佔上下文合法且無鎖（Ch 7）*/
	ksuite_bump_ctxsw(smp_processor_id());
}

/*
 * sched_process_fork probe。
 * TP_PROTO(struct task_struct *parent, struct task_struct *child)
 */
static void probe_sched_fork(void *data, struct task_struct *parent,
			     struct task_struct *child)
{
	if (!ksuite_enabled())
		return;
	/* aux = parent pid；record_event 內部用 spin_lock_irqsave，合法 */
	ksuite_record_event(KSUITE_EVT_FORK, task_pid_nr(child),
			    task_pid_nr(parent), child->comm);
}

static bool sw_registered, fork_registered;

static int __init ksuite_sched_init(void)
{
	int ret;

	ret = register_trace_sched_switch(probe_sched_switch, NULL);
	if (ret)
		return ret;
	sw_registered = true;

	ret = register_trace_sched_process_fork(probe_sched_fork, NULL);
	if (ret)
		goto err_unreg_sw;
	fork_registered = true;

	pr_info("ksuite_sched: tracepoints attached\n");
	return 0;

err_unreg_sw:
	unregister_trace_sched_switch(probe_sched_switch, NULL);
	sw_registered = false;
	return ret;
}

static void __exit ksuite_sched_exit(void)
{
	if (fork_registered)
		unregister_trace_sched_process_fork(probe_sched_fork, NULL);
	if (sw_registered)
		unregister_trace_sched_switch(probe_sched_switch, NULL);

	/*
	 * 關鍵：unregister 之後必須 tracepoint_synchronize_unregister()，
	 * 確保沒有任何 CPU 還在跑我們的 probe（RCU grace period，Ch 27）。
	 * 少了它，rmmod 後 probe 函式的程式碼被釋放，其他 CPU 還在執行 → crash。
	 * 這是 tracepoint 卸載最容易漏、最容易偶發崩潰的一步。
	 */
	tracepoint_synchronize_unregister();
	pr_info("ksuite_sched: detached\n");
}

module_init(ksuite_sched_init);
module_exit(ksuite_sched_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ksuite sched observer: sched_switch + fork tracepoints");

/*
 * 這行讓 kbuild 知道本模組依賴 ksuite_core 匯出的符號。
 * 不寫也能透過符號解析建立依賴，但明確標出更清楚。
 * 實際依賴由 insmod 時的符號查找 + module refcount 自動建立（Ch 8）。
 */
```

### `ksuite_mem.c`（模組 3，關鍵函式骨架）

```c
/* ksuite_mem.c 骨架 — 走一個 PID 的 mm/VMA，回報 RSS/VMA 數。
 * 完整版你要自己補上 misc device 或擴充 core 的 ioctl 來傳 PID。
 * 這裡給最核心、最容易錯的那段：安全地拿 mm 並走 VMA。 */
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sched/mm.h>
#include <linux/mm.h>
#include <linux/pid.h>

struct ksuite_mem_report {
	unsigned long rss_pages;   /* present page 數 */
	unsigned long nr_vmas;
	unsigned long total_vm;    /* VMA 覆蓋的虛擬 page 總數 */
};

static int ksuite_probe_pid(pid_t nr, struct ksuite_mem_report *rep)
{
	struct pid *pid;
	struct task_struct *task;
	struct mm_struct *mm;
	struct vm_area_struct *vma;
	VMA_ITERATOR(vmi, NULL, 0);   /* 6.x maple-tree VMA 迭代器（Ch 19）*/
	int ret = 0;

	memset(rep, 0, sizeof(*rep));

	/* 1) PID → task，成對釋放（漏了就 refcount leak，NFR-1）*/
	pid = find_get_pid(nr);
	if (!pid)
		return -ESRCH;
	task = get_pid_task(pid, PIDTYPE_PID);
	put_pid(pid);
	if (!task)
		return -ESRCH;

	/* 2) 拿 mm。kernel thread 無 mm。get_task_mm 會 pin，mmput 釋放 */
	mm = get_task_mm(task);
	if (!mm) {
		ret = -EINVAL;   /* 目標是 kernel thread */
		goto out_put_task;
	}

	/* 3) 走 VMA 要持 mmap_read_lock（Ch 19，6.x 名稱）*/
	mmap_read_lock(mm);
	rep->rss_pages = get_mm_rss(mm);     /* 現成 RSS；進階可自己走 pte 數 */
	vma_iter_init(&vmi, mm, 0);
	for_each_vma(vmi, vma) {
		rep->nr_vmas++;
		rep->total_vm += (vma->vm_end - vma->vm_start) >> PAGE_SHIFT;
	}
	mmap_read_unlock(mm);

	mmput(mm);
out_put_task:
	put_task_struct(task);
	return ret;
}

/* TODO（你來補）：
 * - 擴充 ksuite.h 加一個 KSUITE_MEM_PROBE ioctl（_IOWR，傳 pid 進、report 出）
 *   由 core 或本模組自己註冊的 misc device 接收
 * - 進階：不用 get_mm_rss，改用 walk_page_range() 自己數 present pte，
 *   對照兩者是否一致（練習 C 的延伸）
 * - init/exit 略：本模組不掛 tracepoint，卸載相對單純，
 *   但若註冊了 device 記得反註冊
 */
MODULE_LICENSE("GPL");
```

### `Makefile`（一次編四個模組）

```makefile
# recipe 行首必須是 Tab（Ch 0 踩雷 5）
obj-m += ksuite_core.o
obj-m += ksuite_sched.o
obj-m += ksuite_mem.o
# obj-m += ksuite_net.o   # 選做，做了再打開

KDIR := /path/to/your/linux-6.12       # 指向你 build 的源碼樹（Ch 0）
PWD  := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

# userspace 控制程式（用 host gcc 編，不進 kbuild）
ksuitectl: ksuitectl.c ksuite.h
	$(CC) -Wall -O2 -o ksuitectl ksuitectl.c
```

### `ksuitectl.c`（userspace 控制/測試程式）

```c
/* ksuitectl.c — 觀測事件 + 下 ioctl。編：gcc -o ksuitectl ksuitectl.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/types.h>
#include "ksuite.h"

static const char *evt_name(unsigned t)
{
	switch (t) {
	case KSUITE_EVT_FORK: return "FORK";
	case KSUITE_EVT_EXEC: return "EXEC";
	case KSUITE_EVT_NET:  return "NET";
	default:              return "?";
	}
}

int main(int argc, char **argv)
{
	int fd = open("/dev/ksuite", O_RDONLY);
	if (fd < 0) { perror("open /dev/ksuite"); return 1; }

	if (argc >= 2 && !strcmp(argv[1], "reset")) {
		ioctl(fd, KSUITE_RESET); return 0;
	}
	if (argc >= 2 && !strcmp(argv[1], "off")) {
		ioctl(fd, KSUITE_DISABLE); return 0;
	}
	if (argc >= 2 && !strcmp(argv[1], "on")) {
		ioctl(fd, KSUITE_ENABLE); return 0;
	}
	if (argc >= 2 && !strcmp(argv[1], "stats")) {
		struct ksuite_stats st;
		if (ioctl(fd, KSUITE_GET_STATS, &st)) { perror("ioctl"); return 1; }
		printf("enabled=%u total=%llu dropped=%llu nr_cpus=%u\n",
		       st.enabled, (unsigned long long)st.total_events,
		       (unsigned long long)st.dropped, st.nr_cpus);
		for (unsigned i = 0; i < st.nr_cpus && i < 64; i++)
			printf("  cpu%u ctxsw=%llu\n", i,
			       (unsigned long long)st.ctxsw_per_cpu[i]);
		return 0;
	}

	/* 預設：持續 tail 事件（阻塞 read）*/
	printf("watching /dev/ksuite events (Ctrl-C to stop)...\n");
	for (;;) {
		struct ksuite_event e;
		ssize_t n = read(fd, &e, sizeof(e));
		if (n != sizeof(e)) { perror("read"); break; }
		printf("[seq=%llu ts=%llu cpu=%u] %-4s pid=%d aux=%d comm=%s\n",
		       (unsigned long long)e.seq, (unsigned long long)e.ts_ns,
		       e.cpu, evt_name(e.type), e.pid, e.aux, e.comm);
	}
	close(fd);
	return 0;
}
```

### QEMU 執行步驟（把 .ko + ksuitectl 放進 initramfs）

```bash
# 1) 編模組與控制程式
make            # 產出 ksuite_core.ko / ksuite_sched.ko / ksuite_mem.ko
make ksuitectl  # 產出 userspace 控制程式

# 2) 放進 Ch 0 建的 initramfs，重打包
cp ksuite_*.ko ksuitectl initramfs/
( cd initramfs && find . | cpio -H newc -o | gzip ) > ../initramfs.cpio.gz

# 3) 開機（-smp 4 才測得到 per-CPU 與並行，NFR-4）
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512M -smp 4

# 4) QEMU 內操作（注意載入順序：core 先！）
/ # insmod /ksuite_core.ko
/ # insmod /ksuite_sched.ko
/ # ./ksuitectl &            # 背景觀測事件
/ # sleep 1 &               # 製造一個 fork → 應看到 FORK 事件印出
/ # ./ksuitectl stats        # 看 per-CPU context switch 計數
/ # cat /proc/ksuite/stats   # 另一個介面看同樣的統計
/ # echo 0 > /sys/kernel/ksuite/enabled   # sysfs 關掉記錄
/ # rmmod ksuite_core        # 應失敗：Module ksuite_core is in use（NFR-3）
/ # rmmod ksuite_sched ; rmmod ksuite_core   # 正確順序，成功
```

</details>

---

## 測試與驗證方法

**功能對不對，用觀測交叉驗證**：不要相信自己的 `pr_info`，要拿兩個獨立來源對。fork 事件對不對？在 QEMU 裡 `sleep 5 &`，`ksuitectl` 印的新 PID 應該等於 shell 的 `$!`。context switch 計數合理嗎？跑 `yes > /dev/null &` 幾個，計數應該狂飆；`kill` 掉就趨緩。mem 模組的 RSS 對不對？拿 `cat /proc/<pid>/status | grep VmRSS` 對，數量級一致就對了（不會完全相等，因為取樣時機不同）。

**品質怎麼證明——這是專案的重頭戲**：

- **KASAN + kmemleak（NFR-1，Ch 53）**：config 開 `CONFIG_KASAN`、`CONFIG_DEBUG_KMEMLEAK`。反覆 insmod/rmmod 全部模組十次，然後 `echo scan > /sys/kernel/debug/kmemleak; cat /sys/kernel/debug/kmemleak`。有輸出就是 leak——最常見的是忘了 `put_task_struct`/`mmput`/`put_pid`，或 error path 沒 `kfree(ring)`。KASAN 則會在 use-after-free 當下就 dump stack。
- **lockdep（NFR-2，Ch 28）**：config 開 `CONFIG_PROVE_LOCKING`。`-smp 4` 下多 reader + fork 壓力跑滿，`dmesg` 不能有 `possible recursive locking`、`inconsistent lock state`、`possible circular locking`。最容易中的是：在持 `ring_lock` 時又去拿別的鎖形成 ABBA，或 irqsave/非 irqsave 混用同一把鎖。
- **atomic context 檢查（NFR-5）**：config 開 `CONFIG_DEBUG_ATOMIC_SLEEP`。如果你不小心在 tracepoint callback 裡呼叫了會睡的東西（`mutex_lock`、`kmalloc(GFP_KERNEL)`、`copy_to_user`），會立刻 `BUG: sleeping function called from invalid context`。這條檢查就是專門抓 NFR-5 違規。
- **gdb 驗證關鍵路徑（NFR-6，Ch 0）**：`-S -s` 開機，gdb 連上後 `lx-symbols` 載模組符號，`b ksuite_record_event`。在 QEMU 裡觸發一個 fork，gdb 停下，`bt` 應該看到 `ksuite_record_event ← probe_sched_fork ← ... ← copy_process`（Ch 10）。這就是你親眼看到「事件從排程器一路流到你的緩衝區」。再 `b ksuite_read` 看 consumer 端怎麼被 `wake_up` 喚醒。

---

## 常見問題

**Q：`insmod ksuite_sched.ko` 報 `Unknown symbol ksuite_record_event`。**
A：core 沒先載入，或你 `EXPORT_SYMBOL_GPL` 打錯字。符號依賴是載入時解析的（Ch 8）——被依賴的模組必須先在。先 `insmod ksuite_core.ko`。若 core 已載入還報錯，`cat /proc/kallsyms | grep ksuite_record` 確認符號真的匯出了。

**Q：rmmod ksuite_sched 後偶爾 crash，log 指向已釋放的程式碼。**
A：忘了 `tracepoint_synchronize_unregister()`。`unregister_trace_*` 只是把 probe 從清單移除，但別的 CPU 可能正在執行你的 probe 函式；rmmod 釋放模組程式碼後它就踩空。必須等一個 RCU grace period（Ch 27）確保沒人在跑。這是 tracepoint 卸載的鐵律。

**Q：`sched_switch` callback 裡 `pr_info` 一下就 kernel 卡死/爆 log。**
A：`sched_switch` 每秒觸發幾千到幾萬次，callback 裡 `pr_info` 會淹沒 log 甚至遞迴觸發排程。callback 裡**只做最輕量的事**（`this_cpu_inc`），要輸出走緩衝區讓 userspace 拉。這也是為什麼設計成環形緩衝而非直接印。

**Q：lockdep 報 `inconsistent lock state {IN-HARDIRQ-W} -> {SOFTIRQ-ON-W}`。**
A：你的 `ring_lock` 有時在中斷上下文拿（sched callback）、有時在普通上下文拿（read）卻沒統一用 `irqsave`。規則：一把鎖只要**曾經**在關中斷上下文被拿，那它**所有**加鎖點都要 `spin_lock_irqsave`。統一改成 irqsave 版就解決。

**Q：mem 模組偶爾拿到的 mm 走一半 process 就死了。**
A：`get_task_mm` 之後、`mmput` 之前，mm 被 pin 住不會消失，安全。但如果你先 `put_task_struct` 又用 task 的欄位就會踩空。守住成對釋放的順序：先用完再依 `get` 的反序釋放。

---

## 延伸方向

做完基本盤還想往上打，這些方向每一個都能讓你更接近真實 kernel 貢獻：

- **把 `ksuite_net` 做完並升級成 XDP**（Ch 46/52）：netfilter hook 換成 XDP program 在網卡驅動最早期計數，對照兩者的效能與可見度差異。
- **加 cgroup 感知**（Ch 50）：事件記錄目標 process 屬於哪個 cgroup，做成「per-cgroup 資源觀測」——這正是 `cgroup` 子系統在做的事的迷你版。
- **用 seqlock 取代 spinlock 讀路徑**（Ch 28）：統計讀取遠多於寫入，seqlock 能讓 reader 幾乎無成本。改完用 lockdep 驗證仍乾淨。
- **RCU 保護一個「被觀測 PID 清單」**（Ch 27）：讓 userspace 動態增減要追的 PID，讀多寫少，正是 RCU 的主場。
- **mmap 事件緩衝到 userspace**（Ch 41）：不用 `read` 逐筆拷貝，改成把環形緩衝 `mmap` 給 userspace 直接讀，零拷貝——這是 perf/ftrace 真正的做法。

---

## 與全課的對照表

這個專案用到了哪些章（打勾的是參考實作直接觸及的；括號是延伸方向會用到的）：

| Part | 章 | 用在哪 |
|---|---|---|
| 0 | Ch 0 環境/gdb | 全程 QEMU 驗證、gdb 停 `ksuite_record_event` |
| 1 | Ch 4 syscall | ioctl 命令設計（`_IOR`/`_IO`） |
| 1 | Ch 5 資料結構 | 環形緩衝的 head/tail 索引設計 |
| 1 | Ch 6 記憶體配置 | `kcalloc` 配 ring、GFP flag 選擇 |
| 1 | Ch 7 per-CPU | `DEFINE_PER_CPU` ctxsw 計數、`this_cpu_inc` |
| 1 | Ch 8 模組載入 | `EXPORT_SYMBOL_GPL`、符號依賴、refcount 擋卸載 |
| 2 | Ch 9 task_struct | 取 `comm`/`pid`/`task_pid_nr` |
| 2 | Ch 10 fork | gdb 看事件源自 `copy_process` |
| 2 | Ch 14 context switch | 理解 `sched_switch` callback 的原子上下文 |
| 3 | Ch 16 page table | （延伸）自己走 pte 數 present page |
| 3 | Ch 19 mm/VMA | `get_task_mm`、`mmap_read_lock`、`for_each_vma` |
| 4 | Ch 25 spinlock | `spin_lock_irqsave` 保護 ring |
| 4 | Ch 26 mutex/wait | `wait_event_interruptible` + `wake_up` |
| 4 | Ch 27 RCU | `tracepoint_synchronize_unregister`；（延伸）PID 清單 |
| 4 | Ch 28 lockdep | 全程用 lockdep 驗證鎖正確 |
| 5 | Ch 30 softirq | 理解 net hook 跑在 softirq |
| 6 | Ch 33/34 VFS | misc device 的 `file_operations`、read 路徑 |
| 7 | Ch 37 device model | procfs + sysfs + kobject |
| 7 | Ch 38 char/misc | `misc_register`、`unlocked_ioctl` |
| 7 | Ch 41 mmap/DMA | （延伸）mmap 緩衝零拷貝 |
| 8 | Ch 43/46 net | （選做）`sk_buff`、netfilter hook |
| 9 | Ch 50 cgroup | （延伸）per-cgroup 觀測 |
| 10 | Ch 51 tracepoint | `register_trace_sched_switch`/`_fork` |
| 10 | Ch 53 debug | KASAN/kmemleak/lockdep/`DEBUG_ATOMIC_SLEEP` 驗證 |

參考實作直接觸及 Part 0/1/2/4/6/7/10 共 20+ 章，把選做與延伸算進去覆蓋全部 10 個 Part——遠超過「整合 70% 核心概念」的要求。

---

## 恭喜你走完 54 章

你從 Ch 0 一顆能停、能看、能改的 kernel 開始，一路讀過排程器怎麼選下一個 task、一次 page fault 底層發生什麼、RCU 為什麼能無鎖讀、一個封包從網卡到 socket 走過哪些層、容器的 namespace/cgroup 在 kernel 裡到底是什麼。現在你把它們**黏成了一個會動的東西**——一套跨七八個子系統、正確處理並行、載入卸載乾淨、能用 gdb 驗證每條路徑的核心模組套件。這不是玩具，這是真實 kernel 模組該有的樣子。

更重要的是你學會的**工作方式**：讀懂源碼設計 → gdb 停在那個函式看它真的怎麼跑 → 動手寫模組驗證 → 用 KASAN/lockdep 逼自己面對並行與生命週期的真相。這套手法對任何 kernel 子系統都通用，包括這門課沒教到的那些。

**接下來往哪走**：

- 回 [README 的精選資料庫](./README.md#精選資料庫)——那裡列的 Gorman（VM）、Mauerer（架構）、LWN、kernel `Documentation/` 是你從「讀懂」走向「精通」的下一站。挑一個你在這門課最有感覺的子系統（排程？mm？網路？），把對應的深度資源啃透。
- **去讀真實 kernel、追一個正在改的子系統**：訂 LKML，或在 [Bootlin](https://elixir.bootlin.com/linux/v6.12/source) 上追一個 patch series。你現在有能力看懂 maintainer 在爭論什麼。
- **發你的第一個 patch**：從最低風險的開始——`Documentation/` 的錯字、一個 `checkpatch.pl` 抓到的格式問題、一個明顯的 `NULL` check 缺失。走一次 `get_maintainer.pl` → `git send-email` 的流程，你就從「讀 kernel 的人」變成「寫 kernel 的人」。本 repo 的 `open_source` 課教你怎麼跟社群協作。

你手裡有一顆能改的 kernel，腦裡有十個子系統的地圖，還有一套驗證任何疑問的方法。剩下的，是去把某個你在乎的地方改得更好。

→ 回到 [課程總覽 README](./README.md)
