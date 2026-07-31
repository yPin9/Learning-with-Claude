# Ch 25 — spinlock、rwlock、qspinlock

> **目標**：理解為什麼 atomic（Ch 24）不夠、要有「鎖」才能保護多步的臨界區；讀懂 spinlock 為什麼忙等而不睡、持鎖時為什麼不能睡、為什麼要有 `_irqsave`/`_bh` 一堆變體；理解現行 kernel 的 spinlock 早就不是 test-and-set，而是 MCS-based 的 qspinlock，以及它為什麼要這樣演進；分得清 rwlock 與 rwsem，知道讀寫鎖為什麼常被 RCU（Ch 27）取代。最後動手寫模組用 spinlock 保護共享串列，故意用錯鎖變體讓 lockdep 對你吼。

## 為什麼需要這個？

Ch 24 給了你 atomic——`atomic_inc()`、`cmpxchg()` 這些「單一操作不會被打斷」的原語。問題是：真正的臨界區幾乎從來不是單一操作。

看一個具體的：你要往一條雙向串列（`struct list_head`，Ch 5）尾端插一個節點。`list_add_tail()` 展開來至少改**四個指標**：

```c
new->prev = tail;
new->next = head;
tail->next = new;
head->prev = new;
```

這四步之間，如果另一顆 CPU 也在同一條串列上插節點、或走訪這條串列，它會看到一條**改到一半、指標互相矛盾**的串列——`tail->next` 已經指向 `new`，但 `new->prev` 還沒設好，或者更糟，兩個插入者的中間狀態交錯。結果是節點遺失、無窮迴圈、或直接解參考到野指標 panic。

atomic 救不了你。你可以讓每一步都 atomic，但「四步作為一個整體不可分割」不是 atomic 能表達的——atomic 保護的是**一個變數的一次讀改寫**，這裡要保護的是**一段程式碼**（critical section，臨界區）。你需要的是一把鎖：進臨界區前先拿鎖，出來再放，同一時間只有一個持鎖者。這就是互斥（mutual exclusion）。

這一章講最基礎的一類鎖——**spinlock**（忙等的鎖），以及它的近親 rwlock 和它的現代實作 qspinlock。下一章（Ch 26）講會睡的鎖（mutex、semaphore）。分成兩章不是隨意——**「持鎖時能不能睡」是 kernel 同步機制最重要的一條分界線**，這條線在 Ch 2（執行環境）就埋下了伏筆。

## 先建立直覺

先把最核心的一組對立畫出來：等一把被別人拿走的鎖時，你有兩種選擇。

```
        想拿鎖，但鎖被別的 CPU 持有
                 │
        ┌────────┴────────┐
        │                 │
   忙等（spin）        睡眠（sleep）
   spinlock            mutex / semaphore
        │                 │
  原地打轉，燒 CPU     讓出 CPU，排程器
  週期查鎖放了沒       跑別的 task，鎖放
                       了再喚醒我
        │                 │
   ┌────┴────┐       ┌────┴─────┐
   臨界區極短      臨界區可能長
   等待時間 < 一次   或者：我根本不能睡
   context switch    （原子上下文，Ch 2）
   的成本
```

決定用哪一種的關鍵是**成本比較**：一次 context switch（Ch 14）不便宜——要存/還暫存器、切 page table、汙染 cache/TLB，通常是幾百到上千個 cycle。如果臨界區只有十幾條指令、持鎖者馬上就會放，那我「原地空轉等它放」的成本，比「睡下去、被喚醒、切回來」還低。這時忙等（spin）划算。

但忙等有個致命前提：**持鎖者必須在另一顆 CPU 上真的在跑、而且很快會放**。這推出兩條鐵律：

1. **spinlock 只在 SMP（多核）有意義**——單核上你 spin 等一把鎖，持鎖者根本沒 CPU 可以跑去放鎖，你會 spin 到天荒地老（實際上單核 + 非搶佔 kernel 時 spinlock 會被編譯成幾乎 no-op，只留下關搶佔的效果，見後文）。
2. **持鎖時絕對不能睡**。如果我持著 spinlock 然後睡著（讓出 CPU），別的 CPU 上有人來 spin 等這把鎖——它在忙等一個「已經睡著、不知何時醒」的持鎖者，等待時間從「幾十 cycle」暴增到「不確定」，忙等的成本假設整個崩掉。所以持 spinlock 時你不能做任何可能睡的事：不能 `kmalloc(GFP_KERNEL)`（可能觸發 reclaim 而睡，要用 `GFP_ATOMIC`，Ch 6）、不能 `copy_from_user()`（可能 page fault 而睡）、不能 `mutex_lock()`、不能 `msleep()`。

第二條就是 Ch 2 那條「原子上下文（atomic context）不能睡」的線。持 spinlock 讓你進入原子上下文。

## spinlock 的介面與語意

介面在 `include/linux/spinlock.h`。核心型別是 `spinlock_t`，最基本的三個操作：

```c
spinlock_t my_lock;
spin_lock_init(&my_lock);        // 動態初始化
// 或靜態：static DEFINE_SPINLOCK(my_lock);

spin_lock(&my_lock);
/* ... 臨界區，越短越好 ... */
spin_unlock(&my_lock);
```

`spin_lock()` 的語意：如果鎖空著，拿走、繼續；如果被別人持著，**原地忙等**（在較新的 qspinlock 實作裡是排隊等，見下節），直到拿到為止。它不會回傳「拿不到」——它一定會拿到才回來（想要「拿不到就走」的版本是 `spin_trylock()`，回傳 bool）。

有一個常被忽略的關鍵細節：**`spin_lock()` 會關閉搶佔（preemption disable）**。為什麼？假設沒關搶佔，你在持鎖時被排程器搶佔、換上另一個 task，而這個 task 也要同一把鎖——它會 spin，但持鎖者（你）被換下去了，在同一顆 CPU 上，你永遠等不到 CPU 去放鎖。這是單核版的自我死鎖。所以 `spin_lock()` 進去就 `preempt_disable()`，`spin_unlock()` 出來才 `preempt_enable()`。這也是為什麼「持 spinlock = 原子上下文 = 不能睡」——關了搶佔，排程器不能把你換下去，你也就不該主動睡。

> **6.x 的 PREEMPT_RT**：在即時（-rt）patch（Ch 31）裡，大多數 spinlock 被偷偷換成會睡的 rt_mutex，好讓高優先權即時任務能搶佔持鎖者。這是 -rt 的核心手法，代價是語意變了。本章講的是主線（非 -rt）的 spinlock。你寫 driver 時要假設 spinlock 是原子上下文，這樣兩邊都對。

## spinlock 與中斷：為什麼需要一堆變體

現在來到 spinlock 最容易寫錯、也最能看出你懂不懂的地方：**中斷**。

想像 CPU 0 上的 process context（Ch 2）拿了 `my_lock`，正在臨界區裡。這時一個硬體中斷打進來（Ch 29），CPU 0 跳去執行中斷處理常式（ISR）。如果**這個 ISR 也要拿 `my_lock`**——它會 spin 等鎖。但鎖的持有者是被它打斷的那段 process context 程式碼，那段程式碼現在被凍在中斷底下，中斷不返回它就不會繼續、不會放鎖。ISR 在忙等一把永遠不會被放的鎖。**CPU 0 自己等自己，死鎖。**

```
   CPU 0, process context
   ─────────────────────────────
   spin_lock(&my_lock)   ← 拿到鎖
   ... 臨界區 ...
        │
        ▼  硬體中斷打進來（同一顆 CPU）
   ┌─────────────────────────────┐
   │ ISR:                        │
   │   spin_lock(&my_lock)       │ ← spin 等鎖...
   │   （等持鎖者放，但持鎖者     │    但持鎖者被我凍住了
   │    正是被我打斷的那段）      │    → 死鎖，CPU 0 卡死
   └─────────────────────────────┘
```

注意這跟「兩顆 CPU 互搶」不同——那個會正常等到；這個是**同一顆 CPU 上、中斷插隊造成的自我死鎖**。解法是：如果一把鎖會同時被 process context 和中斷處理搶，那 process context 拿鎖時，**要先把本地 CPU 的中斷關掉**，這樣拿著鎖的期間不會被中斷插隊，也就不會發生上面的劇本。

這就是 `spin_lock_irqsave()` 的用途。它做兩件事：存下當前中斷開關狀態、關本地中斷，然後拿鎖：

```c
unsigned long flags;
spin_lock_irqsave(&my_lock, flags);   // 關本地中斷 + 拿鎖，舊狀態存進 flags
/* ... 臨界區，process 和 ISR 都碰不進來 ... */
spin_unlock_irqrestore(&my_lock, flags);  // 放鎖 + 還原中斷狀態
```

為什麼要 `save`/`restore` 而不是直接關/開？因為你可能是在一個「中斷本來就已經關著」的 context 裡被呼叫（例如你自己就在另一個持鎖流程中）。如果無腦 `spin_unlock` 時把中斷打開，會在不該開的地方把中斷開了。`flags` 存的是「我進來之前中斷是開是關」，`restore` 還原它，才安全。

那什麼時候不用 `irqsave`？如果這把鎖**只被 process context 用、中斷處理絕不碰它**，那沒有上面的死鎖風險，直接 `spin_lock()` 就好，省下關/開中斷的開銷。中間還有 `spin_lock_irq()`（無條件關中斷、`spin_unlock_irq()` 無條件開）——只在你**確定**進來時中斷是開的才能用，比 `irqsave` 快一點點但危險，多數情況直接用 `irqsave` 最保險。

還有一組 `_bh`：`spin_lock_bh()` 關的不是硬體中斷，而是**軟中斷（softirq）/bottom half**（Ch 30）。用在「鎖會同時被 process context 和 softirq（例如網路收包的下半部）搶」的情況——關硬體中斷太重，只要擋住 softirq 就夠。

一張表把變體講清楚：

| 變體 | 關什麼 | 什麼時候用 |
|---|---|---|
| `spin_lock()` / `spin_unlock()` | 只關搶佔 | 這把鎖**只**在 process context 用，中斷/softirq 都不碰 |
| `spin_lock_bh()` / `spin_unlock_bh()` | 搶佔 + softirq/bottom half | 鎖會被 process context 和 softirq（如 net rx、timer）同時搶 |
| `spin_lock_irq()` / `spin_unlock_irq()` | 搶佔 + 硬體中斷 | 鎖會被 process context 和硬體 ISR 搶，**且你確定進來時中斷是開的** |
| `spin_lock_irqsave()` / `spin_unlock_irqrestore()` | 搶佔 + 硬體中斷（存還原狀態） | 同上，但不確定進來時中斷狀態——**最安全、最常用** |

判斷法則：**問自己「這把鎖會在哪些 context 被拿？」** 只有 process context → 裸 `spin_lock`。有 softirq → `_bh`。有硬體中斷 → `_irqsave`。選最重的那個 context 決定變體。選錯太輕會死鎖（上面的劇本），選錯太重只是白白多關中斷（效能損失、增加中斷延遲），所以**寧可選重**，但別無腦——長時間關中斷會傷即時性。

> **只在 ISR 裡拿的鎖**：如果一把鎖只在中斷處理常式裡被拿、process context 從不碰，那 process context 這邊的自我死鎖劇本不存在，ISR 裡直接 `spin_lock()` 即可（因為 ISR 執行時該中斷源通常已被遮蔽，同一 ISR 不會重入）。但只要 process context 那邊會碰同一把鎖，process 那邊就必須 `irqsave`。

## 底層機制：從 test-and-set 到 qspinlock

前面把 spinlock 當黑盒。現在打開它。「忙等一把鎖」聽起來很簡單，但一把好的 spinlock 要同時滿足三件事：**正確互斥**、**公平（先來先得，不會餓死）**、**在高競爭下可擴展（cache line 不亂彈）**。這三件事逼著 kernel 的 spinlock 實作演進了三代。

### 第一代：test-and-set / test-and-test-and-set

最土的做法：一個 flag，`test_and_set`（atomic 交換，Ch 24）。拿到舊值 0 表示搶到，1 表示別人持有，就 loop 重試。

問題一（cache 抖動）：每個等待者都在對同一個 cache line 做 atomic 寫（`test_and_set` 是寫操作），根據 cache coherence 協定（MESI，Ch 23），每次寫都要把那條 cache line 從別的 CPU 搶成 Exclusive/Modified，導致 cache line 在所有等待者之間**瘋狂彈跳（bouncing）**。等待者越多，彈得越兇，連持鎖者要放鎖時都得先把 cache line 搶回來——競爭越高越慢，這是可擴展性殺手。

問題二（不公平）：誰的 `test_and_set` 剛好命中就誰拿到，跟排隊順序無關。高競爭下某個等待者可能一直搶不到（starvation）。

### 第二代：ticket lock

借麵包店抽號碼牌的點子。鎖裡有兩個數字：`next`（下一張要發的號碼）和 `owner`（現在服務到哪號）。拿鎖時 atomic 地 `fetch_and_inc(next)` 拿一張號碼牌，然後 spin 等 `owner == 我的號碼`。放鎖時 `owner++`，叫下一號。

這解決了**公平性**——嚴格先來先得，FIFO。但**cache 抖動沒解決**：所有等待者還是 spin 在同一個 `owner` 欄位上，`owner++` 一改，所有等待者的那條 cache line 全部失效、全部重讀，還是 O(N) 的 cache 流量。x86 kernel 用了很多年 ticket lock，但在幾十核的機器上競爭時，這個 cache line bouncing 是量得出來的瓶頸。

### 第三代：qspinlock（現行實作）

現行 kernel（`kernel/locking/qspinlock.c`，`arch_spin_lock()` 底下走的就是它）用的是 **MCS-based queued spinlock**，簡稱 qspinlock。MCS 是 Mellor-Crummey & Scott 兩位作者 1991 年論文提出的佇列鎖。核心洞見：

**讓每個等待者 spin 在「自己專屬的變數」上，而不是大家擠在同一個變數上。**

思路是把等待者串成一條佇列。每個等待者有一個自己的 MCS 節點（`struct mcs_spinlock`，含一個 `locked` 旗標和 `next` 指標），這個節點是 **per-CPU 的**（Ch 7），放在自己的 cache line 上。排隊時：

```
   鎖被 CPU A 持有，CPU B、C、D 依序來排隊

   lock ──► [A 持有]
              │
   tail ──────────────► [D 的 MCS 節點]
                              ▲
              [B] ──► [C] ──► ┘        (next 指標串成佇列)

   B spin 在 B.locked（自己的 cache line）
   C spin 在 C.locked（自己的 cache line）
   D spin 在 D.locked（自己的 cache line）

   A 放鎖時 → 把 B.locked 設為 1（只碰 B 的 cache line）
             → B 醒來拿鎖，其他人的 cache line 完全沒被動到
```

放鎖時，持鎖者只需把「佇列裡下一個節點」的 `locked` 設 1——只寫**一條** cache line（下一棒的），其他等待者的 cache line 動都沒動。於是每次交棒的 cache 流量是 O(1) 而非 O(N)，競爭再高也不會 cache 風暴。而且天然 FIFO，公平性也有了。

實務上 qspinlock 做了精巧的最佳化：**沒競爭時它退化成一個便宜的 atomic**（一個 byte 的 test-and-set，快路徑幾乎零成本），只有真的開始有人排隊時才啟用完整的 MCS 佇列機制。`qspinlock` 把整個鎖狀態（locked、pending、tail）壓進一個 32-bit 字裡，快路徑一個 `cmpxchg` 搞定，慢路徑才走 `queued_spin_lock_slowpath()`（在 `kernel/locking/qspinlock.c`）去掛佇列。這種「快路徑極省、慢路徑才複雜」的設計是 kernel 同步原語的通用套路，mutex（Ch 26）也一樣。

> 對你寫 driver 來說，這一整段的實作細節是透明的——你永遠只寫 `spin_lock()`。但理解 qspinlock 的動機（cache line bouncing、公平性）能讓你懂**為什麼「臨界區要短」不只是禮貌、而是可擴展性的硬需求**，以及為什麼在極高競爭的熱點資料結構上，答案往往不是「換更好的鎖」而是「別用鎖」（per-CPU、RCU，Ch 7/27）。

## rwlock 與 rwsem：讀寫鎖

有一類場景：一個資料結構**讀的次數遠多於寫**（例如一張很少變動的路由表、設定表）。用普通 spinlock，兩個純讀者也得互斥排隊——但兩個讀者同時讀根本不衝突，這是浪費。讀寫鎖（reader-writer lock）就是為這個：**多個讀者可以同時持有，寫者獨佔**。

kernel 有兩種讀寫鎖，對應「spin 版」和「睡版」：

**`rwlock_t`（spin 版，`include/linux/rwlock.h`）**——讀寫版的 spinlock，忙等、不能睡：

```c
rwlock_t lock;
read_lock(&lock);   /* ... 只讀 ... */   read_unlock(&lock);
write_lock(&lock);  /* ... 讀寫 ... */   write_unlock(&lock);
```

同樣有 `read_lock_irqsave()` 等中斷變體，規則跟 spinlock 一樣。

**`struct rw_semaphore`（rwsem，睡版，`include/linux/rwsem.h`）**——讀寫版的 semaphore，拿不到會睡，屬於下一章（Ch 26）的可睡鎖家族，這裡先點名對照：

```c
struct rw_semaphore sem;
down_read(&sem);   /* ... */   up_read(&sem);      // 可睡
down_write(&sem);  /* ... */   up_write(&sem);
```

`down_read`/`down_write` 可能睡，所以**只能在 process context 用**，臨界區裡可以睡（可以 `kmalloc(GFP_KERNEL)`、`copy_from_user`）。`mmap_lock`（保護 `mm_struct` 的 VMA 樹，Ch 19）就是一把 rwsem——因為 page fault 處理裡的臨界區又長又可能睡，非睡不可。

怎麼選？跟 spinlock vs mutex 同一條線：**臨界區短、不睡 → rwlock；臨界區長或會睡 → rwsem**。

但這裡有個更重要的判斷：**很多時候讀寫鎖根本不該用，該用 RCU（Ch 27）**。原因是 rwlock 有兩個難纏的問題：

1. **讀者也要寫 cache line**：`read_lock` 要 atomic 地增加讀者計數，這又是個共享寫，在讀多的場景下讀者之間為了那個計數器 cache line bouncing——結果「讓讀者並行」的好意，被讀者搶計數器的成本吃掉。
2. **寫者可能餓死**：源源不絕的讀者可以讓寫者永遠等不到獨佔（取決於實作的公平策略）。

RCU 的殺手鐧是**讀者端零成本、不寫任何共享狀態、不 spin**——讀者幾乎是裸讀。代價是寫者要用「複製—修改—延後釋放舊版本」的手法。對「讀極多、寫極少、且能容忍讀者短暫看到舊版本」的資料，RCU 完勝 rwlock。所以現代 kernel 裡 `rwlock_t` 的新用途越來越少，老程式碼裡的 rwlock 很多被改寫成 RCU。這是 Ch 27 的主戲，這裡先讓你知道：看到讀寫鎖，先問「這能不能改 RCU」。

## 死鎖預防：鎖順序（lock ordering）

只要同時持有超過一把鎖，就有 ABBA 死鎖的風險：

```
   CPU 0                      CPU 1
   spin_lock(A)               spin_lock(B)
   spin_lock(B)  ← 等 CPU1    spin_lock(A)  ← 等 CPU0
   ─────────────────────────────────────────
   CPU0 持 A 等 B，CPU1 持 B 等 A → 兩邊永遠 spin，死鎖
```

標準解法只有一條、但極其有效：**全域統一的鎖獲取順序（lock ordering）**。約定「永遠先拿 A 再拿 B，絕不反過來」。只要所有程式碼都遵守同一個順序，環（cycle）就不可能形成，ABBA 死鎖從根上消失。kernel 裡很多鎖的相對順序是有文件、有慣例的（例如「先 `inode->i_rwsem` 再 `mmap_lock`」這類，各子系統各有規矩）。

問題是人會犯錯，幾百個鎖的順序沒人記得住。所以 kernel 有 **lockdep**（lock dependency validator，`kernel/locking/lockdep.c`，Ch 28 詳講）——它在執行期記錄「每把鎖被拿的時候，當時還持著哪些鎖」，建一張鎖的相依圖，只要偵測到可能形成環的順序（哪怕這次沒真的死鎖），立刻在 dmesg 印出 `possible circular locking dependency detected` 的警告。它還會抓「在原子上下文裡拿了會睡的鎖」「持鎖時開/關中斷不對稱」等一堆錯。開發用的 kernel 一定要開 `CONFIG_PROVE_LOCKING`——它能在死鎖真的發生前就抓到隱患。我們下面動手就會親眼看到它吼。

## 動手：用 spinlock 保護共享串列，並讓 lockdep 抓錯

寫一個模組：一條共享串列，多個 kernel thread 同時往裡加/刪，先看不加鎖的 race，再用 spinlock 修好，最後故意用錯變體讓 lockdep 抱怨。

```c
// splock_demo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/timer.h>

struct item {
    int val;
    struct list_head node;
};

static LIST_HEAD(my_list);
static DEFINE_SPINLOCK(list_lock);     // 靜態初始化一把 spinlock
static struct task_struct *producers[4];
static struct timer_list evil_timer;   // 用來製造「中斷 context 拿同一把鎖」

/* 每個 producer thread：狂加狂刪，製造並行存取 */
static int producer_fn(void *arg)
{
    long id = (long)arg;
    while (!kthread_should_stop()) {
        struct item *it = kmalloc(sizeof(*it), GFP_KERNEL);
        if (!it)
            continue;
        it->val = id;

        /* ── 版本 A：不加鎖（先跑這個看它爆） ────────────
        list_add_tail(&it->node, &my_list);
        list_del(&it->node);
        kfree(it);
        ───────────────────────────────────────────────── */

        /* ── 版本 B：正確加鎖 ─────────────────────────── */
        spin_lock(&list_lock);
        list_add_tail(&it->node, &my_list);
        list_del(&it->node);
        spin_unlock(&list_lock);
        kfree(it);

        cond_resched();       // 主動讓出，避免霸佔 CPU（我們沒持鎖時才能讓）
    }
    return 0;
}

/* timer callback 跑在 softirq context（Ch 30）——故意在這裡拿同一把鎖 */
static void evil_timer_fn(struct timer_list *t)
{
    struct item *it = kmalloc(sizeof(*it), GFP_ATOMIC);  // 原子上下文只能用 GFP_ATOMIC
    if (it) {
        /* ── 版本 C：故意用「錯」的變體 ──────────────
         * producer 那邊用裸 spin_lock()，這裡（softirq）也拿同一把鎖。
         * 若 producer 沒用 _bh 版本擋 softirq → lockdep 會警告
         * inconsistent lock state（HARDIRQ/SOFTIRQ-safe vs unsafe）。
         */
        spin_lock(&list_lock);
        list_add_tail(&it->node, &my_list);
        list_del(&it->node);
        spin_unlock(&list_lock);
        kfree(it);
    }
    mod_timer(&evil_timer, jiffies + HZ);   // 每秒再來一次
}

static int __init demo_init(void)
{
    long i;
    for (i = 0; i < 4; i++) {
        producers[i] = kthread_run(producer_fn, (void *)i, "splock_prod%ld", i);
    }
    timer_setup(&evil_timer, evil_timer_fn, 0);
    mod_timer(&evil_timer, jiffies + HZ);
    pr_info("splock_demo: loaded\n");
    return 0;
}

static void __exit demo_exit(void)
{
    int i;
    del_timer_sync(&evil_timer);
    for (i = 0; i < 4; i++)
        if (producers[i])
            kthread_stop(producers[i]);
    pr_info("splock_demo: unloaded\n");
}

module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("spinlock + lockdep demo for kernel_internals Ch 25");
```

跑法（沿用 Ch 0 的 QEMU + 模組流程，**務必用開了 `CONFIG_PROVE_LOCKING` + `CONFIG_DEBUG_ATOMIC_SLEEP` 的 kernel**）：

1. **先跑版本 A（不加鎖）**：把 producer 換成版本 A，`insmod`。多顆 CPU（QEMU 記得 `-smp 4`）同時改串列，很快會 list corruption——若 kernel 開了 `CONFIG_DEBUG_LIST`，會在 dmesg 看到 `list_add corruption. prev->next should be ...` 或直接 oops。這就是本章開頭那條「四個指標改到一半」的 race 具現化。

2. **換版本 B（spin_lock）**：race 消失，串列操作被序列化，跑得穩。

3. **開版本 C 觀察 lockdep**：producer 用**裸** `spin_lock()`（不擋 softirq），而 timer callback（softirq context）也拿同一把鎖。lockdep 會偵測到「這把鎖有時在 softirq 拿、有時在能被 softirq 打斷的 process context 拿」，判定為潛在死鎖，dmesg 印出類似：

```
================================
WARNING: inconsistent lock state
6.12.0 #1 Not tainted
--------------------------------
inconsistent {SOFTIRQ-ON-W} -> {IN-SOFTIRQ-W} usage.
splock_prod0/123 [HC0[0]:SC1[1]:HE1:SE0] takes:
 (list_lock){+.?.}-{2:2}, at: evil_timer_fn+0x...
{SOFTIRQ-ON-W} state was registered at:
   ...  spin_lock at splock_demo producer_fn
```

它在告訴你：修法是把 producer 那邊改成 `spin_lock_bh()`（擋掉 softirq，讓兩個 context 不會互相插隊）。**改成 `_bh` 後警告消失**——你剛剛用實驗驗證了前面那張變體表的「有 softirq 要用 `_bh`」規則。

用 gdb（Ch 0 的接法）也能看：`break queued_spin_lock_slowpath`，在高競爭時會停下來，`backtrace` 看是誰在等鎖、`p *lock` 看 qspinlock 的狀態字。lockdep 的警告則直接 `dmesg` 看，或在 gdb 裡 `lx-dmesg`。

## 對比與取捨

| 機制 | 等不到時 | context | 臨界區長度 | 適用場景 |
|---|---|---|---|---|
| `spinlock_t` | 忙等（spin） | 任何（含原子上下文） | 極短 | 短臨界區、中斷/softirq 也要碰 |
| `rwlock_t` | 忙等 | 任何 | 短 | 讀多寫少、且不睡；但常被 RCU 取代 |
| `struct mutex`（Ch 26） | 睡 | 只能 process | 中長 | 一般互斥、可能睡的臨界區 |
| `struct rw_semaphore` | 睡 | 只能 process | 中長 | 讀多寫少、臨界區會睡（如 `mmap_lock`） |
| `atomic_t`（Ch 24） | 不阻塞 | 任何 | 單一操作 | 只需保護一個變數的計數/旗標 |
| RCU（Ch 27） | 讀者不阻塞 | 讀者任何 | 讀者短 | 讀極多寫極少、能容忍讀到舊版本 |

還有一個貫穿全表的取捨——**鎖的粒度（granularity）**：

- **粗粒度鎖（coarse-grained）**：一把大鎖保護整個子系統。好處是簡單、不會 ABBA。壞處是可擴展性差——所有 CPU 擠一把鎖，核越多越塞。歷史上的 BKL（Big Kernel Lock）就是極端粗粒度，後來被費了九牛二虎之力拆掉。
- **細粒度鎖（fine-grained）**：每個物件/每個 hash bucket 一把小鎖。好處是不同物件的操作能真正並行，可擴展性好。壞處是鎖多了、要同時持有多把的機會變多，ABBA 風險上升，程式複雜、lockdep 更吃重。

沒有免費午餐：**細粒度換來並行度，代價是複雜度和死鎖風險**。實務上先用粗粒度把邏輯寫對，量到鎖競爭（用 `perf lock`、`/proc/lock_stat`，接 `perf_bench` 課）成為瓶頸，再針對熱點拆細。過早細粒度化是常見的過度工程。

## 踩雷集錦

1. **「spin_lock 就是關中斷」——錯**。裸 `spin_lock()` 只關搶佔，**不關中斷**。要擋中斷得用 `_irqsave`/`_bh`。很多人以為拿了 spinlock 中斷就進不來，於是在會被中斷搶的鎖上用裸版本，埋下上面那個自我死鎖。正確認識：**選哪個變體取決於「這把鎖會在哪些 context 被拿」，不是取決於你想不想關中斷**。

2. **持 spinlock 時呼叫可能睡的函式**。`spin_lock()` 後 `kmalloc(GFP_KERNEL)`、`copy_from_user()`、`mutex_lock()`、`msleep()`——全都可能睡，全都是 bug。開 `CONFIG_DEBUG_ATOMIC_SLEEP` 會抓到 `sleeping function called from invalid context`。正確認識：持 spinlock = 原子上下文，配置記憶體用 `GFP_ATOMIC`（Ch 6），拷 user 資料要在拿鎖前做好。

3. **`spin_lock_irqsave` 的 `flags` 用錯**。`flags` 是給 `irqsave`/`irqrestore` 這一對用的，中間別動它、別跨函式傳一半、別在巢狀鎖裡搞混。每一對 `irqsave`/`irqrestore` 各自一個 `flags` 變數。

4. **忘了在所有路徑放鎖（尤其錯誤路徑）**。臨界區裡 `if (err) return;` 忘了先 `spin_unlock` → 鎖永遠不放 → 下一個拿鎖者 spin 到死。goto 統一出口（`goto out_unlock;`）是 kernel 慣用防呆。lockdep 也會在你「持著鎖卻返回使用者空間/切換 context」時抓到。

5. **在單核想「反正只有一顆 CPU，不用鎖」**。就算單核，**中斷還是會插隊、搶佔還是會切 task**。單核不等於沒有並行——process 被中斷打斷、被搶佔換走，都是並行來源。單核上 spinlock 雖然退化（不真的 spin），但它關搶佔/關中斷的效果仍是必要的。

## 進階：再往深一層

- **`spin_is_locked()` / `lockdep_assert_held()`**：想斷言「呼叫這個函式時必須已持有某鎖」，用 `lockdep_assert_held(&lock)`（只在開 lockdep 時生效，release build 零成本）。這是把「鎖規約」寫進程式碼、讓 lockdep 幫你查的好習慣，比註解可靠。
- **`raw_spinlock_t`**：在 PREEMPT_RT 下，普通 `spinlock_t` 會變成可睡的 rt_mutex；但有些地方（排程器核心、中斷底層）**即使在 -rt 也絕不能睡**，這些地方用 `raw_spinlock_t`，保證任何配置下都是真正的忙等。寫底層程式碼時要知道這個區別。
- **`local_lock_t`（5.8 起）**：保護 per-CPU 資料（Ch 7）的專用鎖。per-CPU 變數理論上不需跨 CPU 互斥，但仍要擋本 CPU 上的中斷/搶佔——`local_lock` 把這個意圖明確化，比裸 `preempt_disable()`/`local_irq_save()` 更能被 lockdep 檢查，也更好讀。
- **面試常問**：「spinlock 和 mutex 差在哪、什麼時候用哪個？」標準答案沿本章的線：忙等 vs 睡、能不能在原子上下文用、臨界區長短、context switch 成本。追問「持 spinlock 能不能 `kmalloc`？」→ 只能 `GFP_ATOMIC`。再追問「為什麼 spinlock 要關搶佔？」→ 防止持鎖者被搶佔後、同 CPU 上的等待者 spin 空轉造成死鎖。
- **`/proc/lock_stat`**：開 `CONFIG_LOCK_STAT` 後，這裡列出每把鎖的競爭次數、等待時間、持有時間——找鎖競爭熱點的第一手資料，接 `perf_bench` 課的可擴展性分析。

## 動手練習

1. **復現 race 再修好**：跑上面版本 A（不加鎖，`-smp 4`），在 dmesg 抓到 list corruption；換版本 B 確認消失。這是「鎖到底防了什麼」最直觀的一課。
2. **讓 lockdep 吼三種不同的錯**：（a）版本 C 的 softirq 用錯變體（inconsistent lock state）；（b）故意寫一段 ABBA：兩把鎖 A、B，一個 thread 先 A 後 B、另一個先 B 後 A，看 lockdep 印 `possible circular locking dependency`；（c）持 spinlock 時 `msleep(1)`，看 `CONFIG_DEBUG_ATOMIC_SLEEP` 抓 `sleeping function called from invalid context`。三種都在 dmesg 截圖存證，對照本章哪條規則被違反。
3. **量鎖競爭**：開 `CONFIG_LOCK_STAT`，把 producer 數量從 4 加到 16，`cat /proc/lock_stat` 看 `list_lock` 的 contention 數字怎麼變。思考：這裡臨界區極短，加更多 producer 到某個點後為什麼不再變快（甚至變慢）？（提示：cache line、qspinlock 佇列。）
4. **gdb 看 qspinlock 慢路徑**：`break queued_spin_lock_slowpath`，在高競爭時停下，`backtrace` 看等鎖的呼叫鏈，`p/x *(u32 *)lock` 看鎖狀態字的 locked/pending/tail 位元。

## 本章重點整理

- atomic 保護單一操作，**臨界區有多步（改多個變數、走串列）就要鎖**確保整段互斥。
- spinlock **忙等不睡**，適合極短臨界區（等待成本 < 一次 context switch）；**持 spinlock = 原子上下文 = 不能睡**（配置記憶體要 `GFP_ATOMIC`）。
- 選哪個變體看「鎖會在哪些 context 被拿」：只 process → 裸 `spin_lock`；有 softirq → `_bh`；有硬體中斷 → `_irqsave`。選錯太輕會自我死鎖，寧可選重。
- 現行 spinlock 是 **qspinlock（MCS 佇列鎖）**：讓每個等待者 spin 在自己的 cache line 上，解掉 ticket lock 的 cache line bouncing，兼顧公平與可擴展。
- rwlock/rwsem 給讀多寫少場景，但**讀者也寫共享計數、寫者可能餓死**，現代 kernel 常用 RCU（Ch 27）取代。
- 多鎖死鎖的解法是**固定的 lock ordering**，靠 **lockdep** 在執行期驗證（Ch 28）。

## 自我檢核

- [ ] 不看筆記，能說出為什麼 atomic 不夠、什麼樣的臨界區一定要鎖
- [ ] 能解釋 spinlock 為什麼忙等而非睡，以及「持鎖時能不能睡」這條線為什麼存在
- [ ] 面試被問「spin_lock、_irqsave、_bh 差在哪、何時用哪個」，能不查表答出判斷法則
- [ ] 能講清楚 test-and-set → ticket lock → qspinlock 的演進動機（cache line bouncing、公平性）
- [ ] 能說出 rwlock 為什麼常被 RCU 取代，以及 rwlock 和 rwsem 的差別
- [ ] 能解釋 ABBA 死鎖怎麼形成、lock ordering 怎麼防、lockdep 怎麼抓

## 延伸閱讀

### 官方文件

- **[Documentation/locking/spinlocks.rst](https://www.kernel.org/doc/html/latest/locking/spinlocks.html)**
  - **讀哪裡**：整篇，很短。kernel 官方對 spinlock 變體、何時關中斷/softirq 的權威說明
  - **和本章的關聯**：本章的變體表就是它的展開；寫 driver 拿不定用哪個變體時回來查這篇

- **[Documentation/locking/locktypes.rst](https://www.kernel.org/doc/html/latest/locking/locktypes.html)**
  - **讀哪裡**：spinlock / raw_spinlock / rwlock 幾節，以及 PREEMPT_RT 對各類鎖的語意改寫
  - **能學到什麼**：`raw_spinlock_t` 為什麼存在、-rt 下各種鎖的行為變化，補足本章只點到的部分

- **[Documentation/locking/lockdep-design.rst](https://www.kernel.org/doc/html/latest/locking/lockdep-design.html)**
  - **讀哪裡**：lock class、usage state、依賴圖幾節
  - **和本章的關聯**：解釋動手部分那些 lockdep 警告訊息（`inconsistent lock state` 等）到底在說什麼，Ch 28 會深入

### 論文

- **[Algorithms for Scalable Synchronization on Shared-Memory Multiprocessors](https://www.cs.rochester.edu/~scott/papers/1991_TOCS_synch.pdf)** — Mellor-Crummey & Scott, ACM TOCS 1991
  - **讀哪裡**：MCS lock 那節（qspinlock 的理論源頭）
  - **為什麼值得讀**：這是「讓每個等待者 spin 在自己的變數上」這個核心點子的原始論文，讀懂它就懂 qspinlock 為什麼那樣設計
  - **前提**：懂 cache coherence（Ch 23）、atomic（Ch 24）

### 書籍 / 長文

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 9–10 章（Kernel Synchronization Introduction / Methods）
  - **定位**：最好讀的同步機制入門，把 race、臨界區、各種鎖的動機講得很清楚
  - **注意**：講的是 ticket lock 時代，qspinlock 的部分以本章與 kernel 源碼為準

- **[Is Parallel Programming Hard, And, If So, What Can You Do About It?](https://mirrors.edge.kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html)** — Paul McKenney（perfbook）
  - **讀哪裡**：Locking 那章，以及後面 Deferred Processing（RCU）章
  - **為什麼值得讀**：RCU 之父寫的並行程式設計聖經，講「為什麼鎖競爭是可擴展性殺手」「什麼時候該不用鎖」比任何教材都深，直接連到 Ch 27
  - **前提**：耐心；這本很厚但寫得極好，可當工具書查

spinlock 是「不能睡」那半邊的鎖。下一章我們跨到另一半：mutex、semaphore、completion——會睡的鎖，看排程器怎麼把等鎖的 task 掛起來、鎖放了又怎麼精準喚醒它。

→ [Ch 26 mutex、semaphore、completion](./26-mutex-semaphore.md)
