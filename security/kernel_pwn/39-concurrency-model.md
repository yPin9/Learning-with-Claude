# Ch 39 — kernel 並發模型：lock 家族、RCU、per-cpu、記憶體屏障、race window

> **目標**：建立「kernel 裡哪裡有共享狀態、哪裡的同步可能出錯」的直覺——這是看懂並製造 race condition 漏洞（[Ch 40](./40-race-conditions.md)、[Ch 41](./41-widening-race-window.md)）的機制前置。讀完這章，你要能看著一段 kernel 程式碼，指出「這裡讀寫了什麼共享狀態」「它用了哪種鎖或哪種同步手段」「如果拿掉這層同步,會在哪裡壞掉」。

> **環境**：Linux 6.6，x86-64。本章談的鎖 API（`spin_lock`、`mutex_lock`、RCU 系列）在這個版本穩定；`PREEMPT_RT` patch 合併進主線後 spinlock 的底層語意有變化，本章末會特別標注。

## 為什麼需要這一章？

[Ch 3](./03-kernel-c-runtime.md) 提過 atomic context 不能睡眠，但沒解釋「為什麼 kernel 要分 atomic context 和 process context」這個更底層的問題——答案就在這一章。

單核心（uniprocessor）年代的 Linux kernel 幾乎不需要鎖。一個 CPU 一次只能執行一條指令流，只要你在關鍵區段把 interrupt 關掉（`local_irq_disable()`），就沒有人能在你操作一半的時候插進來動同一塊記憶體——這是真正意義上的「獨佔」。1990 年代中期 Linux 開始支援 SMP（symmetric multiprocessing，多核心對稱式多處理）之後，這個假設整個垮掉：現在**真的有兩個 CPU 同時在跑程式碼**，CPU 0 在改一個 list，CPU 1 可能真的在同一個瞬間讀同一個 list——不是「交錯執行,但其實一次只有一個在動」,是物理上同時發生。關中斷只能擋住「同一個 CPU 上的 interrupt 插進來」,擋不住另一個 CPU。

這逼出了一整套新的同步原語——spinlock、mutex、RCU——而且逼出了一個對 kernel pwn 讀者極其重要的事實：**凡是會被多個 CPU 或多條執行路徑同時碰到的共享狀態，都是一個「同步沒做對就會壞」的候選點**。漏洞研究者讀 kernel 原始碼找 race condition 漏洞時，眼睛看的正是這一章要教你辨認的東西——誰在讀這個欄位、誰在寫、鎖有沒有包住正確的範圍、鎖包住的範圍夠不夠長。

## 先建立直覺

```
單 CPU 年代：
  CPU0: ─A──B──C──D──E──F─▶  （中斷進來也只是插在 A~F 之間，
                                 絕不會和 A~F 同時發生）

SMP 年代：
  CPU0: ─A──B──C──D──────▶
  CPU1: ────────X──Y──Z──▶
              ↑
     B 和 X 是真的同時在不同核心上執行——
     如果 B 和 X 都在動同一個 struct 的同一個欄位，
     誰先誰後、甚至「各寫一半交錯」都可能發生。
```

把 kernel 想成一間銀行,`struct` 裡的欄位是帳本上的數字。單 CPU 年代只有一個惃員,他一次只能做一件事,帳本不會亂。SMP 年代變成多個惃員同時服務不同窗口，但帳本只有一本——如果兩個惃員同時在改同一頁、沒有講好「誰先誰後」或「輪到誰的時候別人先別碰」，帳本就會變成一個誰也沒預期的爛帳。鎖、RCU、記憶體屏障,都是「講好規矩」的不同做法，差別在規矩的嚴格程度和付出的代價。

## 核心概念：共享狀態從哪裡冒出來

在深入個別同步原語之前，先搞清楚 kernel 裡「共享狀態」具體長在哪裡——這決定了你讀程式碼時該往哪裡看：

- **多核心同時執行**：兩個 CPU 上各跑一個 process，同時呼叫同一個 syscall（例如兩個 thread 同時 `ioctl` 同一個 fd），兩條執行路徑可能同時落進同一段 kernel 程式碼,操作同一個 `struct file`/`struct my_dev` 的欄位。
- **process context 被 interrupt 打斷**：一個 CPU 上,process context 正在改某個資料結構,這時一個硬體中斷（interrupt）發生,interrupt handler 跑在同一個 CPU 上,如果它也碰了同一塊資料——這不是「真正同時」，是「操作到一半被插隊」，但效果一樣危險。
- **softirq / tasklet / workqueue**：這些是 kernel 內部的延遲執行機制，可能和原本觸發它們的 process context 不同步地跑，一樣會碰共享狀態。
- **RCU 寬限期內的讀者**：即使寫者已經把指標換掉，舊版本資料結構可能還有讀者正在看——這是本章後半 RCU 一節要講的,一種「延遲、但有保證」的共享狀態存在期。

這四類場景,分別對應不同的同步工具——這正是本章要建立的地圖。

## Lock 家族：該用哪一種

### Spinlock：忙等待、不能睡眠

```c
#include <linux/spinlock.h>

spinlock_t my_lock;
spin_lock_init(&my_lock);

spin_lock(&my_lock);
/* 臨界區：只能做不會睡眠的事 */
shared_counter++;
spin_unlock(&my_lock);
```

拿不到鎖的 CPU **不會被排程出去睡覺**，它會在一個迴圈裡反覆檢查鎖有沒有被釋放（忙等待,busy-waiting）,持續佔用 CPU。這聽起來浪費,但正是重點：如果臨界區本來就很短（幾個指令，比如遞增一個計數器）,忙等待反而比「context switch 出去、排程、context switch 回來」便宜得多——後者的開銷常常比臨界區本身還大。

**因此 spinlock 有一個鐵律：臨界區內絕對不能睡眠**。[Ch 3](./03-kernel-c-runtime.md) 講過的「持有 spinlock 期間用 `GFP_KERNEL` 會出事」就是這個鐵律的具體案例——如果臨界區內睡眠,等於你讓一個「本該幾奈秒就結束」的忙等待窗口,變成「不知道要等多久」,其他 CPU 在旁邊空轉浪費電,嚴重時甚至死鎖。

Spinlock 有兩個常見變體,處理「臨界區會不會被 interrupt 打斷」的問題：

```c
unsigned long flags;

spin_lock_irqsave(&my_lock, flags);   /* 關閉本地 CPU 的 interrupt,順便存舊狀態 */
/* 臨界區,保證不會被 interrupt handler 插隊 */
spin_unlock_irqrestore(&my_lock, flags);
```

如果這塊資料**也**會被 interrupt handler 碰（例如某個驅動程式的收包處理同時跑在 process context 和 interrupt context），單純 `spin_lock` 擋得住別的 CPU，擋不住**同一個 CPU 上插進來的 interrupt**——這時就要用 `_irqsave` 變體,多做一件事：先把本地 CPU 的 interrupt 關掉。這是很多 kernel CVE 的真實根因之一：用了 `spin_lock` 卻漏了 `_irqsave`，資料同時也被 IRQ context 碰。

### Mutex：可以睡眠、有 owner

```c
#include <linux/mutex.h>

struct mutex my_mutex;
mutex_init(&my_mutex);

mutex_lock(&my_mutex);
/* 臨界區：可以呼叫會睡眠的函式,例如 kmalloc(GFP_KERNEL)、copy_from_user */
mutex_unlock(&my_mutex);
```

`mutex_lock` 拿不到鎖時,目前的 thread **會被排程出去**（先嘗試短暫的 optimistic spinning，猜鎖可能很快釋放；真的拿不到才真正睡眠等待喚醒）。這意味著臨界區可以做「可能睡眠」的事——呼叫 `GFP_KERNEL` 配置記憶體、呼叫 `copy_from_user`（它內部可能觸發 page fault,也可能睡眠）。

Mutex 因此**絕對不能用在 atomic context**（interrupt handler、已持有 spinlock 的區間）——拿不到鎖時它會嘗試睡眠,而 atomic context 不准睡眠,這和 `GFP_KERNEL` 在錯誤 context 用的問題是同一個鐵律的另一個面。

Mutex 還有一個 spinlock 沒有的性質：**它記錄 owner**（哪個 task 持有這個鎖）。這讓 kernel 能做一些 spinlock 做不到的事,例如偵測「同一個 thread 想對同一個 mutex 上兩次鎖」（self-deadlock，lockdep 工具會抓到這個,見 [Ch 44](./44-debug-toolchain.md)）,也讓 `mutex_unlock` 只能由原本持有鎖的 thread 呼叫——這不只是規範,違反它 kernel 會直接噴警告。

### Semaphore：計數型、更舊的祖先

```c
#include <linux/semaphore.h>

struct semaphore my_sem;
sema_init(&my_sem, 3);     /* 允許最多 3 個併發持有者 */

down(&my_sem);              /* 遞減,拿不到就睡眠等待 */
/* 臨界區 */
up(&my_sem);                 /* 遞增,可能喚醒等待者 */
```

Semaphore 是 counting semaphore（計數號誌）,可以允許 N 個（不只 1 個）併發持有者——這是它和 mutex 本質的差異：mutex 永遠是「0 或 1」（binary semaphore 的概念，但 kernel 的 `mutex` 額外帶了 owner 追蹤,語意比單純二元號誌更嚴格）。歷史上 Linux 早期大量用 semaphore 當作互斥鎖使用（計數設成 1），2006 年引入專門的 `mutex` 之後,純粹當互斥鎖用的場景大多遷移過去——mutex 的 owner 追蹤和 debug 支援更好,效能也因為針對「計數恰好是 1」這個特例做了優化而更快。Semaphore 現在主要還活在真正需要「允許 N 個並行」語意的地方（例如限制同時進行的 I/O 數量）。

### rwlock / rw_semaphore：讀多寫少的優化

```c
#include <linux/rwsem.h>

struct rw_semaphore my_rwsem;
init_rwsem(&my_rwsem);

down_read(&my_rwsem);        /* 多個讀者可以同時持有 */
/* 只讀取共享資料 */
up_read(&my_rwsem);

down_write(&my_rwsem);       /* 寫者獨佔,等所有讀者/寫者離開 */
/* 修改共享資料 */
up_write(&my_rwsem);
```

如果一份資料「讀取遠比寫入頻繁」，用一般的互斥鎖（mutex/spinlock）會讓大量讀者互相排隊,即使他們彼此之間根本不會互相破壞資料。rwlock（spinlock 版本）/`rw_semaphore`（可睡眠版本）允許**多個讀者同時持有鎖**,只有寫者需要獨佔。代價是實作更複雜（要處理讀者優先還是寫者優先,避免寫者永遠被源源不絕的讀者餓死——這是 kernel 版本 rwlock 實作史上真實出現過的 fairness 問題）。

### Seqlock：讀者不阻塞寫者的另一條路

```c
#include <linux/seqlock.h>

seqlock_t my_seqlock;
seqlock_init(&my_seqlock);

/* 寫者 */
write_seqlock(&my_seqlock);
x = 1; y = 2;                 /* 更新一組相關欄位 */
write_sequnlock(&my_seqlock);

/* 讀者 */
unsigned seq;
int rx, ry;
do {
        seq = read_seqbegin(&my_seqlock);
        rx = x; ry = y;
} while (read_seqretry(&my_seqlock, seq));
```

Seqlock 的設計目標是「寫者完全不被讀者擋」——讀者不持有任何鎖，只是記下一個序號（sequence number），讀完之後檢查這個序號有沒有在讀的過程中被寫者改變；如果變了（代表讀的中途有寫者插進來),就重讀一次。這個機制要求**讀到的資料本身必須是能安全重讀的**（不能有副作用、不能是會 crash 的操作）,也要求寫者遞增序號是真正原子的操作——`jiffies`（kernel 的計時計數器）這類「寫入頻繁但讀取更頻繁、而且短暫讀到舊值可以接受」的資料就是 seqlock 的經典應用場景。

### 鎖家族速查表

| 鎖類型 | 能睡眠？ | 多讀者併發？ | 典型場景 | 不能用在哪 |
|---|---|---|---|---|
| spinlock | 否 | 否 | 極短臨界區，interrupt handler 內也要用的資料 | 臨界區內不能有任何可能睡眠的操作 |
| spinlock (irqsave) | 否 | 否 | 同上，且資料也被同一 CPU 的 interrupt 碰 | 同上 |
| mutex | 是 | 否 | 一般 process context 的互斥存取，臨界區可能較長 | atomic context、interrupt handler |
| semaphore | 是 | 是（計數 N） | 限制同時存取數量的資源池 | atomic context |
| rw_semaphore | 是 | 讀者可 | 讀多寫少的大型資料結構 | atomic context |
| rwlock_t | 否 | 讀者可 | 讀多寫少，但臨界區短、不能睡眠 | 臨界區內不能睡眠 |
| seqlock | 否（讀者是重試迴圈） | 讀者不阻塞 | 寫入頻繁但讀取更頻繁、短暫讀到舊值可接受 | 讀到的資料有副作用、或讀者要看見「完全一致」的保證（seqlock 只保證重讀後一致，讀的那一刻可能看到撕裂的中間狀態） |

## 底層機制：spinlock 在 SMP 上怎麼實作

單純的 spinlock 如果用最天真的方式實作（一個共享的 flag，`while (flag) {} ; flag = 1;`）在多核心上會有公平性問題——先到的 CPU 不保證先拿到鎖,可能被後到但運氣好的 CPU 插隊,極端情況下某個 CPU 可能一直搶輸,發生 **饑餓（starvation）**。

現代 Linux（x86-64，自 2.6.25 起）用的是 **ticket spinlock** 和後來的 **qspinlock（queued spinlock）**：

```
Ticket spinlock 的直覺：

  鎖內部維護兩個計數器：next_ticket, owner_ticket

  CPU 要拿鎖：
    my_ticket = atomic_fetch_add(&next_ticket, 1)   ← 原子地「抽號碼牌」
    while (owner_ticket != my_ticket) { }            ← 排隊等叫號
    （拿到鎖）

  CPU 放鎖：
    owner_ticket++                                    ← 叫下一號
```

這保證了**先抽到號碼牌的人先拿到鎖**（FIFO 公平性），解決了天真實作的饑餓問題。`qspinlock` 在這個基礎上進一步優化——用一個 per-CPU 的佇列結構代替全域計數器上的忙等待，減少高併發下大量 CPU 同時盯著同一塊 cache line 造成的 cache 流量風暴（cache line bouncing）。這些實作細節你不需要背,但要知道一個事實：**spinlock 不是「隨便忙等待」,它是一個經過精心設計、保證公平性與效能的同步原語**，kernel 原始碼裡看到的 `spin_lock()` 呼叫背後有相當講究的工程。

### Mutex 底層：optimistic spinning

Mutex 拿不到鎖時不會立刻睡眠——它先猜一下：如果目前持有鎖的 thread 正在另一個 CPU 上跑（代表它可能很快就會釋放鎖),拿不到鎖的 thread 會先做一段短暫的忙等待（optimistic spinning），賭這個鎖馬上就會被放掉，省下一次完整的 context switch（睡眠 + 被喚醒的開銷遠大於短暫忙等待)。只有在這個賭注顯然不會贏的情況下（持有者本身也在睡眠、或者已經等了太久）,才真正把 thread 排進等待佇列並讓它睡眠。這個設計是 mutex 在「可能需要睡眠」和「臨界區其實很短」兩種情境之間取得的折衷。

## RCU：為什麼 kernel 愛用它

### 先建立直覺

前面幾種鎖都基於同一個假設：**讀者和寫者不能同時碰同一份資料**,所以要麼讓讀者等寫者,要麼讓寫者等讀者。RCU（**Read-Copy-Update**）換了一個角度問問題：如果讀者**完全不需要被寫者擋**,寫者也**完全不需要等讀者讀完才動手**,行不行?

答案是：只要寫者**不直接修改讀者可能正在看的那份資料,而是準備一份新的副本,改好之後才把「指向資料的指標」原子地換成新副本**，讀者在整個過程中要麼看到完整的舊版本,要麼看到完整的新版本,永遠不會看到「正在被改到一半」的撕裂狀態。至於舊版本什麼時候真正釋放——等到**所有在指標被換掉那一刻之前就已經在讀舊版本的讀者都讀完**為止。

```
RCU 的時間軸直覺：

  讀者 R1: ──[rcu_read_lock]──讀舊版本 A──[rcu_read_unlock]──▶
  讀者 R2:      ──[rcu_read_lock]──讀舊版本 A────[rcu_read_unlock]──▶
                         ↑
  寫者 W:          準備新版本 B
                   rcu_assign_pointer(ptr, B)   ← 指標原子換成 B,新讀者之後看到 B
                         │
                   synchronize_rcu()  ← 等待：所有在換指標「之前」已經在讀的人（R1, R2）
                         │              都離開 read-side critical section
                         ▼
                   （grace period 結束）
                   kfree(A)             ← 現在才真正釋放舊版本,保證沒有人還在看它
```

這一段時間——指標已經換成新版本、但舊版本因為可能還有讀者在看而還不能釋放——叫做 **grace period（寬限期）**。`synchronize_rcu()` 會讓呼叫它的 thread 睡眠等待,直到 grace period 結束才返回；如果寫者不想等待（例如在 atomic context 裡）,可以用 `call_rcu()` 註冊一個 callback,等 grace period 結束後由 kernel 非同步呼叫它來真正釋放資源，或者更常見的 `kfree_rcu()` 直接把「等 grace period 再 kfree」這個模式包成一個呼叫。

### 為什麼 kernel 這麼愛用它

RCU 的讀者端幾乎零成本——`rcu_read_lock()`/`rcu_read_unlock()` 在多數設定下只是 `preempt_disable()`/`preempt_enable()`（確保讀者不會被搶占、遷移到別的 CPU 導致 grace period 判斷出錯）,**不涉及任何原子操作、不涉及任何可能和別的 CPU 競爭的 cache line**——這對「讀取遠比寫入頻繁」的資料結構（module 列表、路由表、大多數 VFS 查找路徑）是巨大的效能優勢,比 rwlock 的讀者端（至少要做一次原子遞增)還便宜。代價轉嫁到寫者身上：寫者要多配置一份副本、要等 grace period、邏輯比直接改一個欄位複雜得多。這是一個標準的工程取捨——如果讀者是瓶頸（絕大多數 kernel 熱路徑就是這樣）,把代價轉嫁給相對少見的寫者是值得的。

### 和讀者的約定：publish/subscribe

RCU 要成立,讀者和寫者雙方都要守規矩：寫者發布新版本時要用 `rcu_assign_pointer()`（內建適當的記憶體屏障,保證「新物件的內容」在「指標被換掉」之前已經完全寫好,不會有讀者拿到指標之後讀到還沒初始化完的欄位);讀者要用 `rcu_dereference()` 讀取指標（同樣內建屏障,保證讀取順序正確)。這兩個 API 存在的理由，本章最後一節的記憶體屏障會解釋。

> **邊界條件誠實標注**：RCU 的 grace period 保證只蓋住「已經在讀的人」,不保證「換指標之後多快有新讀者開始看到新版本」——這是一個**最終一致（eventually consistent）**而非「立即一致」的模型。如果你的場景需要「寫入後立刻全域可見、不允許任何人看到舊版本」,RCU 不適合。這正是為什麼 RCU 常見於「模組列表」「路由規則」這類允許短暫新舊版本並存的資料,而不是帳戶餘額這類要求強一致的場景。

## Per-CPU 變數與搶占：race 的另一個來源

[Ch 3](./03-kernel-c-runtime.md) 介紹過 per-cpu 變數用 `this_cpu_*` API 避免鎖競爭,這裡把那段留下的坑補完整。

```c
/* 危險版本：中間可能被搶占、遷移到別的 CPU */
int cpu = smp_processor_id();
local_counters[cpu]++;              /* ← 如果這裡被搶占,遷到別的 CPU 繼續跑... */
do_something_that_may_sleep();
local_counters[cpu]++;              /* ← 這個 cpu 已經不是「目前」CPU 了,邏輯錯誤 */
```

```c
/* 正確版本：保證整段操作在同一個 CPU 上完成 */
int cpu = get_cpu();                /* 隱含 preempt_disable() */
local_counters[cpu]++;
/* 這段期間不能呼叫任何可能睡眠的函式——否則等於手動違反了 atomic context 規則 */
local_counters[cpu]++;
put_cpu();                          /* 隱含 preempt_enable() */
```

`this_cpu_inc()` 這類單一操作本身在支援的架構上是不可分割的,但如果你的邏輯需要**跨越多個操作、中間還夾著「讀取目前是哪個 CPU」這個結果**,就必須自己用 `get_cpu()`/`put_cpu()`（或 `preempt_disable()`/`preempt_enable()`）包住整段,否則「我讀到的 CPU 編號」和「我實際執行到的 CPU」可能已經不同步——這本身就是一種 race，只是競爭的對象是**排程器的搶占決定**,不是另一個 CPU。

## 記憶體屏障：為什麼光有鎖還不夠

### 問題：CPU 和編譯器都會重排指令

```c
x = 1;
y = 1;
```

直覺上這兩行會按順序執行,但**CPU 的 out-of-order execution（亂序執行）**和**編譯器的最佳化**都可能在不影響單執行緒語意的前提下調換這兩行的實際執行/生效順序——對單執行緒程式碼這無傷大雅（反正最終結果一樣),但如果另一個 CPU 在旁邊觀察這兩個寫入,它可能先看到 `y=1` 生效、後看到 `x=1` 生效,跟原始碼順序倒過來。

這對 RCU 的 publish/subscribe 模式是致命的：如果寫者準備好一個新物件（填好內部欄位),然後把指標指向它,但 CPU/編譯器把「填欄位」和「換指標」的順序對其他 CPU 可見的效果調換了——讀者可能先看到指標已經換成新物件,但新物件內部的欄位還沒填好,讀到未初始化的垃圾值。

### 解法：記憶體屏障

```c
#include <linux/compiler.h>
#include <asm/barrier.h>

smp_wmb();   /* write memory barrier：保證此屏障之前的寫入,
                對其他 CPU 可見的順序,不會晚於此屏障之後的寫入 */
smp_rmb();   /* read memory barrier：保證此屏障之前的讀取,
                不會在效果上晚於此屏障之後的讀取被重排 */
smp_mb();    /* 同時保證讀寫兩個方向 */
barrier();   /* 純編譯器屏障,不產生任何 CPU 指令,只阻止編譯器重排,
                CPU 層面的重排不受影響——用在單 CPU 也會出問題的場景(如和 interrupt handler 互動) */
```

`rcu_assign_pointer()`/`spin_lock()`/`spin_unlock()` 這些 API 內部已經包含了正確的屏障（`spin_lock` 帶 acquire 語意、`spin_unlock` 帶 release 語意——鎖內部的操作保證不會被重排到鎖外去,這是「鎖本身就隱含了記憶體屏障」這個常被忽略的事實）。你很少需要手動呼叫 `smp_mb()` 這類原始 API——真正需要手動處理屏障的場景通常落在：無鎖（lock-free）資料結構、RCU 的進階用法、per-cpu 資料跨 CPU 同步讀取這幾個特定角落。知道它存在、知道它解決什麼問題,比會手刻屏障更重要——手刻無鎖同步程式碼是 kernel 社群公認最容易藏 bug、最難 debug 的領域之一,正常 driver/module 開發幾乎不該自己手刻,而是用上面介紹的鎖或 RCU。

## 具體例子：一個正確上鎖的計數器 vs 一個少上鎖的 race

### 例子一：正確版本

```c
struct my_dev {
        spinlock_t lock;
        int refcount;
        char *buf;
};

static long my_ioctl_use(struct my_dev *dev)
{
        spin_lock(&dev->lock);
        dev->refcount++;
        spin_unlock(&dev->lock);

        /* 使用 dev->buf 的邏輯 ... */

        spin_lock(&dev->lock);
        dev->refcount--;
        spin_unlock(&dev->lock);
        return 0;
}
```

這個版本保證 `refcount` 的遞增/遞減在任何時刻只有一個 CPU 在動,不會有「兩個 CPU 同時讀到 5、各自加 1、都寫回 6」這種遺失更新（lost update）。

### 例子二：少上鎖導致的 race——TOCTOU 的最小示意

```c
/* 危險版本：check 和 use 之間沒有鎖保護 */
static long my_ioctl_release(struct my_dev *dev)
{
        if (dev->buf != NULL) {            /* ① check：此刻 buf 非空 */
                /* ---- 這裡沒有鎖,另一個 thread 隨時可能插進來 ---- */
                kfree(dev->buf);            /* ② use：假設 buf 還有效 */
                dev->buf = NULL;
        }
        return 0;
}

static long my_ioctl_release_concurrent(struct my_dev *dev)
{
        /* 如果兩個 thread 同時呼叫上面這個函式 */
        if (dev->buf != NULL) {            /* Thread A 和 Thread B 都通過了這個 check */
                kfree(dev->buf);            /* Thread A 先 free */
                dev->buf = NULL;
        }
        /* Thread B 排在 A 之後跑到這裡時,dev->buf 已經是 NULL,
           這個特定例子因為 NULL 檢查剛好擋住了第二次 free——
           但如果 check 和 use 之間還有別的操作(例如先把 buf 複製到區域變數再用),
           第二個 thread 可能拿著一個已經被 A free 掉的指標繼續操作,
           這就是典型的 double-free / UAF 根因。*/
        return 0;
}
```

這段**未實測,為理論預期行為**——要親眼看到這個 race 真的觸發,需要兩個使用者空間執行緒同時狂打同一個 ioctl（搭配 Ch 41 會教的技巧刻意拉長「check 和 use 之間」的窗口),並在開著 KASAN 的 kernel 上觀察 `dmesg` 是否報出 use-after-free。這裡的重點不是跑出一個具體錯誤訊息,而是讓你看清楚：**「檢查 + 使用」這個模式,只要中間沒有鎖或其他同步手段包住,就是一個天生的 race 候選點**——[Ch 40](./40-race-conditions.md) 會把這個模式正式命名為 TOCTOU（time-of-check to time-of-use）,並給出真實 CVE 案例;[Ch 41](./41-widening-race-window.md) 會教你怎麼用 `userfaultfd`、FUSE、CPU 親和性操控把這種窗口從「幾奈秒」拉長到「幾毫秒甚至更久」,讓原本難以觸發的 race 變成可穩定利用的原語。

正確的修法是用本章學到的工具之一包住整段「check + use」：

```c
static long my_ioctl_release_fixed(struct my_dev *dev)
{
        spin_lock(&dev->lock);
        if (dev->buf != NULL) {
                kfree(dev->buf);
                dev->buf = NULL;
        }
        spin_unlock(&dev->lock);
        return 0;
}
```

## 對比與取捨

| 同步手段 | 讀者成本 | 寫者成本 | 一致性保證 | 典型踩雷 |
|---|---|---|---|---|
| spinlock | 中（忙等待） | 中 | 強（互斥） | 臨界區睡眠、漏用 `_irqsave` |
| mutex | 中（可能睡眠） | 中 | 強（互斥） | atomic context 誤用、非 owner 解鎖 |
| rw_semaphore | 低（讀者間不互擋） | 中 | 強 | 寫者餓死、讀者臨界區內誤呼叫睡眠函式但以為安全 |
| seqlock | 極低（無鎖，重試） | 低 | 弱（讀者可能讀到撕裂值,靠重試修正） | 讀的資料有副作用、忘記重試迴圈 |
| RCU | 幾乎零 | 高（複製 + 等 grace period） | 最終一致 | 以為 grace period 結束前舊版本已經不可見、忘記 `rcu_dereference` |

## 踩雷集錦

1. **錯誤直覺：「用了 `spin_lock` 就天下太平,不用再管 interrupt。」** 正確認識：`spin_lock` 只擋別的 CPU,擋不住同一個 CPU 上插進來的 interrupt handler——如果這塊資料也會被 interrupt context 碰,要用 `spin_lock_irqsave`,否則同一個 CPU 上的 interrupt handler 可以在你持有鎖的期間插隊,直接繞過你以為的保護。

2. **錯誤直覺：「RCU 的 `rcu_read_lock()` 就像一般的鎖,鎖住之後讀到的資料不會變。」** 正確認識：RCU 讀者鎖住的是「指標所指的那個版本在我讀完之前不會被釋放」,**不是**「資料在我讀的這段時間完全不變」——寫者隨時可以發布一個新版本（指標指向別的地方），你手上拿著的舊版本本身是穩定的,但如果你在同一段 critical section 裡重新 `rcu_dereference()` 同一個指標兩次,兩次可能拿到不同版本。

3. **錯誤直覺：「atomic_t 的操作本身就有記憶體屏障,我不用額外處理。」** 正確認識：**不是所有** `atomic_*` API 都帶完整屏障——Linux 的 atomic API 明確區分「帶 barrier 的版本」（如 `atomic_add_return`）和「不帶的版本」（如普通的 `atomic_set`),文件（`Documentation/atomic_t.txt`）對每個函式的屏障語意都有精確定義,靠「感覺上應該有」去猜是常見 bug 來源,真要確認時去查文件。

4. **錯誤直覺：「我的程式碼只在一個 CPU 上測試過,沒出事,所以沒有 race。」** 正確認識：race condition 的本質是「時序剛好對了才會觸發」,單核心測試環境、甚至多核心但負載很輕的環境都可能幾千次都不觸發同一個 race——這是 kernel pwn 讀者需要理解的另一面：**沒有復現不代表沒有漏洞**,[Ch 41](./41-widening-race-window.md) 教的那些技巧,本質上就是在「人工製造出讓 race 幾乎每次都觸發的條件」,而現實中的攻擊者也會做同樣的事。

5. **錯誤直覺：「PREEMPT_RT 只是個效能選項,鎖的語意不會變。」** 正確認識：Linux 6.x 系列合併進主線的 `PREEMPT_RT`（即時性支援）底層把一部分 `spinlock_t` 在內部實作成可以睡眠的鎖（`rt_mutex` 包裝)以達成「有界延遲」的即時性目標——這意味著在 `PREEMPT_RT` 開啟的 kernel 上,「spinlock 絕對不能睡眠」這條鐵律在**實作細節**上被打破了一部分（雖然語意上仍然維持互斥）,讀 kernel 原始碼時如果牽涉到 RT 相關的 `#ifdef`,不能照搬本章對一般 kernel 的假設,請以 `Documentation/locking/locktypes.rst` 為準。

## 進階：再往深一層

- **SRCU（Sleepable RCU）**：一般 RCU 的讀者端不能睡眠（因為實作上依賴 `preempt_disable`），某些場景的讀者偏偏需要睡眠（例如讀的過程中要拿一個 mutex）——SRCU 犧牲一些效能,換來讀者端可以睡眠的彈性,原理仍是 grace period,但追蹤機制更重。
- **lockdep**：kernel 內建的鎖依賴關係偵測器,能在執行期發現「這個鎖的取用順序,理論上可能導致死鎖」（即使這次執行沒有真的死鎖),是除錯並發問題的利器,[Ch 44](./44-debug-toolchain.md) 會細講怎麼讀它的輸出。
- **KCSAN（Kernel Concurrency Sanitizer）**：編譯期插入偵測碼,專門抓「資料競爭（data race）」——兩個沒有同步保護的存取同時碰同一塊記憶體,其中至少一個是寫入,即使這次沒有造成可觀察的錯誤,KCSAN 也會回報,是對本章「少上鎖」這類 bug 最直接的自動化偵測工具,同樣在 [Ch 44](./44-debug-toolchain.md) 展開。
- **無鎖（lock-free）資料結構**：進一步把本章的屏障知識推到極限——完全不用鎖,只靠原子操作 + 記憶體屏障達成正確性,kernel 內部少數熱路徑（如某些 per-cpu 計數聚合)這樣做,但這是公認最容易寫錯、最難 debug 的領域,一般 driver 開發不建議手刻。

## 動手練習

1. 在你 Ch 0 建好的環境裡,找一個真實 kernel 子系統（建議從 `kernel/module/main.c` 或任何一個小型 driver 開始),挑一個用 `spin_lock`/`mutex_lock` 保護的欄位,讀懂它保護的臨界區「從哪裡到哪裡」,畫出來。
2. 把本章例子二的「少上鎖」版本和「正確版本」都編譯成 kernel module,寫一個 user-space 程式開兩條執行緒狂打同一個 ioctl,比較兩個版本在 `CONFIG_KASAN` 開啟的 kernel 上跑一段時間後,`dmesg` 有沒有出現任何警告（此練習**未實測**,預期正確版本不會有警告,少上鎖版本視時序運氣可能出現、也可能長時間不出現——這正是踩雷集錦第 4 條要強調的）。
3. 讀 `Documentation/RCU/whatisrcu.rst`（Ch 2 教過怎麼在原始碼樹裡找文件）,找出 `rcu_assign_pointer()` 的實際定義,確認它內部用了哪個記憶體屏障相關的宏。

## 本章重點整理

- SMP 讓「真正同時執行」成為常態,這是鎖、RCU、記憶體屏障存在的根本原因——單 CPU 年代關中斷就夠,SMP 年代不夠。
- Lock 家族依「能不能睡眠」「允許幾個讀者併發」分類：spinlock/rwlock 不能睡眠,mutex/semaphore/rw_semaphore 可以睡眠,seqlock 讓讀者完全不阻塞但要容忍重試。
- RCU 用「複製 + 延後釋放（grace period)」取代「讀者等寫者」,讀者端近乎零成本,代價是寫者邏輯更複雜、一致性是最終一致而非立即一致。
- 記憶體屏障解決的是「鎖本身已經隱含正確順序,但無鎖/RCU 的進階場景需要手動保證」的問題——鎖和 RCU 的標準 API 已經內建正確屏障,一般開發不需要手刻。
- 「檢查 + 使用」（check-then-use）這個模式,只要中間沒有同步手段包住,就是 race condition 漏洞最常見的天然候選點——這正是下一章的主題。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼單 CPU 年代不太需要鎖,SMP 年代一定需要
- [ ] 能說出 spinlock 和 mutex 分別適合什麼 context,各自的鐵律是什麼(不只是背「能不能睡眠」,要能說出違反會發生什麼)
- [ ] 能用自己的話解釋 RCU 的 grace period 在保證什麼、不保證什麼
- [ ] 想一個你讀過的 kernel 原始碼片段,指出裡面有沒有「檢查 + 使用」中間缺乏同步保護的模式

## 延伸閱讀

### 官方文件 / Spec

- **[A Tour Through RCU's Requirements](https://docs.kernel.org/RCU/Design/Requirements/Requirements.html)**
  - **讀哪裡**：開頭的 "Fundamental Requirements" 一節,說明 grace period 的精確保證邊界
  - **和本章的關聯**：本章 RCU 一節的 grace period 直覺圖,正是這份文件用更嚴謹的語言展開的版本;遇到「grace period 到底保證了什麼、沒保證什麼」的疑惑時回來查這份文件最準
  - **前提知識**：先讀完本章 RCU 一節再看,直接看這份文件容易被術語密度壓垮

- **[Documentation/locking/locktypes.rst](https://docs.kernel.org/locking/locktypes.html)**
  - **讀哪裡**：整篇,特別是關於 `PREEMPT_RT` 下 spinlock 語意變化的段落
  - **和本章的關聯**：本章踩雷集錦第 5 條提到的 RT 差異,這份文件是權威來源

### 書籍 / 長篇參考

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（持續更新的免費線上書,俗稱 "perfbook"）
  - **這本書的定位**：RCU 的發明者親自寫的並發程式設計參考書,從硬體記憶體模型講到鎖、RCU、無鎖資料結構,深度遠超一般教材
  - **讀哪幾章**：第 9 章（Deferred Processing,RCU 的完整理論基礎)直接對應本章 RCU 一節;第 15 章談記憶體屏障的硬體成因,補足本章屏障一節省略的 CPU 微架構細節
  - **前提知識**：需要先有本章的 RCU/鎖基礎直覺,這本書會把每個直覺推到數學化的嚴謹程度

### 部落格 / 技術文章

- **[LWN.net — "A guide to the Kernel Lock Validator"](https://lwn.net/Articles/185666/)** 與 LWN 的 RCU 系列文章（搜尋 "LWN RCU usage")
  - **這篇說什麼**：LWN 的 kernel 內部機制系列是公認 kernel 社群寫得最清楚的技術報導,涵蓋 lockdep 的設計動機與早期實作
  - **讀哪裡**：lockdep 那篇整篇;RCU 系列挑和本章對應的入門篇
  - **為什麼值得讀**：LWN 的作者長期直接採訪 kernel 維護者本人,內容的準確度和權威部落格相當,且會補充「這個機制為什麼在那個年代被需要」的歷史脈絡,正好補本章省略的部分演進史

有了「哪裡有共享狀態、哪種同步手段對應什麼保證」的地圖,下一章就能正式把它翻譯成攻擊者視角——同一張地圖,反過來看,就是「哪裡的同步缺口可以被我利用」。

→ [Ch 40 race condition 漏洞：TOCTOU、double-fetch、refcount race](./40-race-conditions.md)
