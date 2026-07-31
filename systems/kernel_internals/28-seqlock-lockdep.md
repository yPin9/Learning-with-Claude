# Ch 28 — seqlock、lockdep、死鎖模式

> **目標**：補齊 Part 4 最後一種鎖 seqlock（讀多寫少、寫者優先、讀者無鎖但可能重試），把它和 RCU（Ch 27）、rwlock（Ch 25）放在同一張取捨表上看清楚各自的位置；理解死鎖的四個必要條件與經典的 AB-BA 模式；讀懂 kernel 開發最有價值的工具之一——lockdep：它怎麼在死鎖「還沒真的發生」前就推斷出隱患，並且能逐段拆解一份真實的 lockdep splat。最後動手寫一個 AB-BA 死鎖模組觸發 splat 並逐行讀懂，再用 `CONFIG_DEBUG_ATOMIC_SLEEP` 抓「睡在不該睡的地方」。

## 為什麼需要這個？

Ch 25 到 27 我們把鎖走了一遍：spinlock（忙等、不睡）、mutex（會睡）、rwlock（讀寫分離）、RCU（讀者完全無鎖）。看起來讀多寫少的場景 RCU 已經是終極解，為什麼還要一個 seqlock？

因為 RCU 有它的代價。RCU 讀者看到的可能是**舊版本**的資料（寫者發布新版本，舊讀者還在讀舊的），而且 RCU 保護的是「一個指標指向的物件」——寫者必須「配一個新物件、填好、原子換指標、寬限期後回收舊的」。這個模型對「一個結構被指標引用」很自然，但對**一組彼此相關、必須一起讀到一致快照**的純值（value）就笨重了。

最經典的例子是**讀時間**。系統裡讀時間的頻率高到嚇人（每個 `gettimeofday`、每個排程決策、每筆 log 的時間戳）。timekeeping 的資料是一組彼此相關的欄位（現在幾秒幾奈秒、時鐘源的乘數與位移），讀者要的是這一組值的一致快照。用 RCU 保護，每次寫（每個 tick 更新時間）都要配新物件、換指標、排寬限期回收——為了一組幾十 bytes 的純值，這太重。用 rwlock，讀者之間雖不互斥但仍要對鎖的 cacheline 做原子寫（拿讀鎖也要改計數），高頻讀下這個 cacheline 會在各 CPU 間彈來彈去（bouncing），成為瓶頸。

seqlock 給的答案是：**讀者完全不碰鎖、不寫任何共享狀態，只讀一個序號**。讀者讀之前記下序號、讀之後再檢查序號有沒有變。沒變，讀到的就是一致快照；變了，代表讀的過程中有人寫，重讀一次。代價是讀者可能重試、寫者優先（讀者可能被連續的寫者餓到），但在「寫很少、讀極多、讀的東西是一致值快照」的場景下，這是最省的做法。

## 先建立直覺

seqlock 的核心是一個 **sequence counter（序號）**，配一把給寫者用的 spinlock。規則簡單到可以背：

- **寫者**：拿鎖 → 把序號 +1（變成**奇數 odd**）→ 改資料 → 序號再 +1（變回**偶數 even**）→ 放鎖。
- **讀者**：讀前記下序號 → 讀資料 → 讀後再看序號。**如果序號是奇數**（表示讀的當下有寫者正在寫），**或前後序號不一樣**（表示讀的期間有寫者寫完了），就**整段重讀**。

序號的奇偶性編碼了「現在有沒有人正在寫」：奇數 = 寫進行中、偶數 = 沒人寫。讀者看到奇數，或看到前後不一致，就知道自己這次讀可能撈到寫到一半的髒資料，作廢重來。

```
   序號 seq 的變化（寫者遞增兩次）：

   ...  even ──write_seqlock──►  odd  ──[寫資料]──  odd  ──write_sequnlock──►  even ...
        (穩定)     +1           (寫中)              (寫中)      +1            (穩定)

   讀者的重試迴圈：
   ┌──────────────────────────────────────────────────────────┐
   │  s = read_seqbegin()      // 記下當前 seq                  │
   │       │                                                    │
   │       ▼                                                    │
   │  讀 data（可能撈到寫到一半的值——沒關係，等下會發現）        │
   │       │                                                    │
   │       ▼                                                    │
   │  read_seqretry(s)?  ──── seq 是奇數 or 變了 ──── yes ──┐   │
   │       │                                               │   │
   │       │ no（seq 沒變且是偶數）                         │   │
   │       ▼                                          重讀 │   │
   │   讀到一致快照，離開 ◄──────────────────────────────┘   │
   └──────────────────────────────────────────────────────────┘
```

和 rwlock 對照最能看出差別：rwlock 的讀者**會擋住寫者**（讀鎖持有期間寫者拿不到寫鎖），也**會寫共享狀態**（改讀者計數）。seqlock 的讀者**擋不住任何人、也不寫任何共享狀態**——它只是「樂觀地讀，讀完檢查有沒有被寫者插隊」。寫者不等讀者，這就是「寫者優先」的由來。

## seqlock 的 API 與源碼

seqlock 定義在 `include/linux/seqlock.h`。有兩個層次的抽象：底層的 `seqcount_t`（純序號，鎖要自己另外準備）和包好的 `seqlock_t`（序號 + 內建 spinlock）。

`seqlock_t` 的結構大致是：

```c
// include/linux/seqlock.h（簡化）
typedef struct {
    seqcount_spinlock_t seqcount;   // 序號
    spinlock_t lock;                // 保護寫者互斥的 spinlock
} seqlock_t;
```

寫者端（`write_seqlock` / `write_sequnlock`）：

```c
static inline void write_seqlock(seqlock_t *sl)
{
    spin_lock(&sl->lock);           // 寫者之間互斥
    do_write_seqcount_begin(&sl->seqcount.seqcount);  // seq++（變奇數）+ 寫屏障
}

static inline void write_sequnlock(seqlock_t *sl)
{
    do_write_seqcount_end(&sl->seqcount.seqcount);    // 寫屏障 + seq++（變偶數）
    spin_unlock(&sl->lock);
}
```

注意 `write_seqlock` 內含一把真正的 spinlock——所以**寫者之間**是互斥的、寫者仍然遵守 Ch 25 的所有規則（不能睡、若和中斷共用要 `write_seqlock_irqsave`）。序號的兩次遞增之間夾著 memory barrier（Ch 23、Ch 24），確保「序號變奇數」這件事在「改資料」之前對其他 CPU 可見、「改完資料」在「序號變偶數」之前可見——否則讀者可能看到序號偶數卻讀到還沒寫完的資料。

讀者端（`read_seqbegin` / `read_seqretry`）：

```c
static inline unsigned read_seqbegin(const seqlock_t *sl)
{
    return read_seqcount_begin(&sl->seqcount);   // 讀 seq；若是奇數會自旋等到偶數
}

static inline unsigned read_seqretry(const seqlock_t *sl, unsigned start)
{
    return read_seqcount_retry(&sl->seqcount, start);  // seq 變了就回傳非 0（要重試）
}
```

典型讀者寫法（這個 pattern 要記牢）：

```c
unsigned seq;
do {
    seq = read_seqbegin(&my_seqlock);
    // ── 讀臨界區：把要讀的值抓進區域變數 ──
    a = shared.a;
    b = shared.b;
} while (read_seqretry(&my_seqlock, seq));
// 到這裡 a、b 是一致快照
```

`read_seqbegin` 若讀到奇數（寫者正在寫），會先自旋等到序號變偶數才回傳——所以進迴圈時 `seq` 一定是偶數。迴圈結束條件是 `read_seqretry` 回傳 0，也就是「序號沒變」，代表整段讀期間沒有寫者插進來。

**真實用例**：`kernel/time/timekeeping.c` 的時間讀取（`ktime_get` 一族）用的正是 seqcount（`tk_core.seq`），這是 seqlock 最重要的舞台，我們在 Ch 32 會再回來看。另一個是 dcache 的 `d_path()`／RCU-walk 路徑解析（`rename_lock` 是個 seqlock），路徑名在被 rename 的同時被讀，用 seqlock 偵測「讀路徑的期間發生了 rename」就重走。`jiffies` 的 64-bit 讀取在 32-bit 平台上也靠 seqlock（`jiffies_lock`）保證讀到完整的 64 位而非撕裂的高低半。

## seqlock 的致命限制：讀臨界區必須可重入

seqlock 的讀者**會重跑**。這是它整個設計的地基，也是最容易寫錯的地方。因為讀臨界區可能被執行多次，它必須滿足兩個條件：

1. **不能有副作用**。讀臨界區裡不能改任何外部狀態、不能 `printk`（會重複印）、不能配記憶體、不能拿其他鎖、不能做任何「跑第二次就出問題」的事。它只能**把共享值抄進區域變數**。

2. **必須容忍讀到暫時不一致／髒的值**。讀者在重試發生前，可能撈到寫者寫到一半的資料——例如 64 位值的高低半來自不同版本。所以讀臨界區裡**不能拿這些髒值去做會爆炸的事**（例如用一個讀到一半、可能是 0 或亂數的長度去 `memcpy`、拿髒指標去解參考）。要等 `read_seqretry` 確認一致後，才能信任抄出來的值。

第二點常被低估。設想讀臨界區裡有 `p = shared.ptr; x = *p;`——如果 `shared.ptr` 正被寫者換成新指標，你可能讀到「換到一半」的野指標然後解參考它 → 直接 oops，`read_seqretry` 根本來不及救你。**所以 seqlock 適合保護「純值」（整數、時間戳這種讀到髒值頂多算錯、不會 crash 的東西），不適合保護「讀出來馬上要解參考的指標」**。要在 seqlock 讀臨界區裡安全跟指標，得用 `lockless_dereference` 之類配合，或者根本改用 RCU——這也是為什麼 timekeeping（純值）用 seqlock、而「用指標發布物件」用 RCU。

## 對比與取捨：seqlock vs RCU vs rwlock

三者都想解決「讀多寫少」，但取捨點不同。這張表要能默寫：

| 維度 | rwlock（Ch 25） | RCU（Ch 27） | seqlock（本章） |
|---|---|---|---|
| 讀者成本 | 中：拿讀鎖要原子改計數，cacheline 會 bounce | 極低：`rcu_read_lock` 幾乎零成本（關搶佔或什麼都不做） | 低：只讀一個序號，不寫共享狀態 |
| 讀者會不會重試 | 不會 | 不會 | **會**（讀期間被寫就重讀） |
| 讀者看到的資料 | 最新（讀期間寫者被擋） | 可能是**舊版本**（寫者發布新版，舊讀者續讀舊的） | **最新的一致快照**（重試保證一致） |
| 讀者擋不擋寫者 | **擋**（讀鎖持有期間寫者等） | 不擋 | **不擋**（寫者優先） |
| 寫者成本 | 中：等所有讀者放鎖 | 高：配新物件 + 寬限期回收 | 低：拿 spinlock + 遞增序號兩次 |
| 讀臨界區能否睡/有副作用 | 視 rwlock 種類（rwsem 可睡） | 不可睡（經典 RCU）、無副作用要求寬鬆 | **不可睡、不可有副作用、要可重入** |
| 適合保護什麼 | 通用讀寫共享結構 | 指標發布的物件（list/tree 節點） | 一小組**純值**的一致快照（時間、序號） |
| 誰可能挨餓 | 寫者（讀者多時難拿寫鎖） | 沒人餓，但寫者回收有延遲 | **讀者**（寫太頻繁時讀者一直重試） |

一句話決策：**讀出來要跟指標／要能睡 → RCU 或 rwsem；讀的是一小組純值、要最新一致快照、寫極少 → seqlock；其餘通用場景或不確定 → 先用 spinlock/mutex，別過早優化**。真實 kernel 裡三者常混用——timekeeping 用 seqlock、路由表用 RCU、少數老路徑還留 rwlock。

## 死鎖：四個必要條件與 AB-BA 模式

前面三章的每一種鎖都可能死鎖。死鎖不是玄學，它有精確的成立條件——經典作業系統理論的**四個必要條件**（Coffman conditions），四個**同時成立**才會死鎖，打破任一個就不會：

1. **互斥（mutual exclusion）**：資源一次只能被一個持有者拿。鎖天生就是互斥的——這條沒得打破，它是鎖存在的理由。
2. **持有並等待（hold and wait）**：一個執行流持有某些鎖的同時，又去等另一把鎖。
3. **不可搶奪（no preemption）**：鎖不能被強制從持有者手上搶走，只能由持有者主動放。kernel 的 spinlock/mutex 都是這樣（除了 `mutex_trylock` 這種「拿不到就算了」的路徑）。
4. **循環等待（circular wait）**：存在一個環——A 等 B 持有的鎖、B 等 C、…、最後一個回頭等 A。

kernel 死鎖防治的主戰場是**第 4 條**（循環等待），因為前三條幾乎是鎖的本質、難以取消。打破循環等待最實際的手段是**固定鎖順序（lock ordering）**：全 kernel 對「該先拿哪把鎖」有一致約定，就永遠形不成環。

最小的循環是兩把鎖的 **AB-BA 死鎖**：

```
   CPU 0                          CPU 1
   ─────                          ─────
   lock(A)                        lock(B)
       │                              │
   （想拿 B，但 B 被 CPU1 持有）    （想拿 A，但 A 被 CPU0 持有）
   lock(B) ◄────等─────┐  ┌────等──► lock(A)
       │               │  │              │
       └───────────────┘  └──────────────┘
                     互相等對方放，永遠不動
```

CPU 0 拿順序是 A→B，CPU 1 拿順序是 B→A。只要兩邊剛好交錯到「各拿一把、各等對方那把」，就鎖死。注意這是**時序相關**的 bug——多數時候兩條路徑不會剛好撞在一起，程式跑得好好的；只有某次調度剛好交錯才死。這種「大部分時候不發生」正是它難抓的原因，也是為什麼我們需要一個能在**沒真的死之前**就推斷出風險的工具。

## lockdep：在死鎖發生前就抓到它

lockdep（lock dependency validator，`kernel/locking/lockdep.c`，需要 `CONFIG_PROVE_LOCKING`）是 kernel 最有價值的除錯工具之一。它的洞見是：**你不需要真的觀察到死鎖，只要觀察到「會導致死鎖的鎖取得順序」就夠了**。

它怎麼做到的？核心機制是**記錄鎖取得順序，建一張鎖相依圖，偵測環**：

- lockdep 不追蹤「每一個鎖實例」（那太多了，一個 spinlock 可能有百萬個實例），而是追蹤 **lock class（鎖類別）**。同一行程式碼靜態定義的鎖屬同一個 class（例如某個 struct 裡的 `->lock` 欄位，所有該 struct 實例的那把鎖是同一 class）。
- 每次一個執行流在**已經持有鎖 X 的情況下**去拿鎖 Y，lockdep 就記下一條有向邊 **X → Y**（「X 之後拿了 Y」）意即「觀察到 X 先於 Y 的取得順序」。
- 這些邊累積成一張 **鎖相依圖（dependency graph）**。lockdep 每加一條新邊，就檢查加了它會不會**形成環**。
- 一旦某條新邊會形成環（例如已有 A→B，現在來了 B→A），就代表「存在一種調度，讓兩條路徑各持一把、互等對方」——潛在 AB-BA。lockdep 立刻在 dmesg 印出 **`possible circular locking dependency detected`**。

關鍵在**「即使死鎖沒真的發生」**：CPU 0 曾經（在過去某時刻）以 A→B 順序拿過鎖、CPU 1 曾經以 B→A 拿過——這兩件事不必同時發生，甚至不必在同一次開機。lockdep 從「A→B 和 B→A 兩種順序都被觀察到」這個事實，**推斷出**「若它們同時發生就會死」，於是提前報警。這讓死鎖從「難以復現的機率 bug」變成「跑過一次相關路徑就會被抓」。

```
   lockdep 的鎖相依圖與環偵測：

   曾觀察到 CPU0 的順序 A→B：        後來觀察到 CPU1 的順序 B→A：

        A                                A
        │ (加邊 A→B)                     ▲
        ▼                                │ (要加邊 B→A)
        B                                B

   合併後：  A ──────► B
             ▲         │
             └─────────┘   ← 形成環！lockdep：possible circular locking dependency
   （此刻兩條路徑其實沒同時跑、沒真的死鎖，但 lockdep 已推斷出隱患並報警）
```

除了循環，lockdep 還抓好幾類鎖誤用（都在同一份 report 裡以不同標題出現）：

- **`inconsistent lock state`**：同一 class 的鎖，有時在**開中斷**的 context 拿、有時在**中斷 context**（或關中斷後）拿——這對應 Ch 25 那個「process 拿了鎖、中斷插進來也要拿同一鎖 → 自我死鎖」的劇本。lockdep 追蹤每個 lock class 的「中斷安全狀態」，發現同一 class 被「硬中斷安全」和「硬中斷不安全」兩種方式使用，就報 `inconsistent {HARDIRQ-ON-W} -> {IN-HARDIRQ-W}` 之類。這正是 Ch 25 教你用 `spin_lock_irqsave` 要避免的錯，lockdep 幫你自動抓。
- **同一鎖遞迴拿兩次**（`possible recursive locking`）：非遞迴鎖在持有時又拿自己。

## 逐段拆解一份真實的 lockdep splat

lockdep 的輸出（俗稱 splat）第一次看很嚇人，但結構固定。以下是一份典型的 AB-BA circular dependency 報告，逐段拆：

```
======================================================
WARNING: possible circular locking dependency detected
6.12.0 #1 Not tainted
------------------------------------------------------
insmod/142 is trying to acquire lock:
ffff8881003a4d18 (&b_lock){+.+.}-{2:2}, at: ab_ba_thread_a+0x5c/0x90 [deadlock_demo]

but task is already holding lock:
ffff8881003a4cd8 (&a_lock){+.+.}-{2:2}, at: ab_ba_thread_a+0x40/0x90 [deadlock_demo]

which lock already depends on the new lock.
```

拆解第一段：

- `possible circular locking dependency detected`——標題，這是循環相依（AB-BA 家族）。`possible` 是關鍵字：不是「已死」，是「可能會死」。
- `insmod/142`——觸發的 task 是 `insmod`、PID 142。
- **`is trying to acquire lock: ... &b_lock`**——現在**想拿**的是 `b_lock`，位置在我們模組 `ab_ba_thread_a` 函式。
- **`but task is already holding lock: ... &a_lock`**——但這個 task **已經持有** `a_lock`。
- 所以這條路徑製造了一條邊 **a_lock → b_lock**。
- 那個 `{+.+.}-{2:2}` 是鎖的「使用狀態指紋」：四個字元編碼這鎖在（硬中斷/軟中斷 × 開/關）各種 context 下被拿過的狀態（`+` 表示曾在該 context 拿過、`.` 表示沒有），`{2:2}` 是 read/write 用法。判 AB-BA 時可先略過，它在判 `inconsistent lock state` 時才是主角。

```
the existing dependency chain (in reverse order) is:

-> #1 (&a_lock){+.+.}-{2:2}:
       lock_acquire+0x...
       _raw_spin_lock+0x...
       ab_ba_thread_b+0x40/0x90 [deadlock_demo]     ← thread B 拿 a_lock 時...

-> #0 (&b_lock){+.+.}-{2:2}:
       lock_acquire+0x...
       _raw_spin_lock+0x...
       ab_ba_thread_b+0x5c/0x90 [deadlock_demo]     ← ...同時已持有 b_lock
```

這是**環的另一半**。lockdep 把它記下的相反順序邊列出來：`#1 a_lock` 依賴 `#0 b_lock`，意思是「**曾經**有路徑在持有 `b_lock` 時去拿 `a_lock`」——那就是 `ab_ba_thread_b`（順序 B→A）。配上第一段的 `a_lock → b_lock`（`ab_ba_thread_a`，順序 A→B），兩條邊接起來就是環。

```
other info that might help us debug this:

 Possible unsafe locking scenario:

       CPU0                    CPU1
       ----                    ----
  lock(&a_lock);
                               lock(&b_lock);
                               lock(&a_lock);
  lock(&b_lock);

 *** DEADLOCK ***
```

這是整份報告**最該先看**的一段——lockdep 直接畫給你「怎樣的交錯會死」：CPU0 拿了 `a_lock` 後想拿 `b_lock`，同時 CPU1 拿了 `b_lock` 後想拿 `a_lock`，四步交錯即死。看到這段就懂了問題，其餘 stack trace 是幫你定位程式碼位置。後面通常還跟著兩個 task 的完整 `stack trace`，指出各自在哪一行拿了哪把鎖——照著檔案:行號去修鎖順序即可。

修法：**統一鎖順序**。讓 `ab_ba_thread_b` 也照 A→B 拿（先 `a_lock` 再 `b_lock`），環就消失、splat 也不再出現。這就是「固定 lock ordering」在實務上的樣子。

## 動手：寫一個 AB-BA 死鎖模組觸發 lockdep splat

前提：你的除錯 kernel 要開 `CONFIG_PROVE_LOCKING`（連帶會開 `CONFIG_LOCKDEP`、`CONFIG_DEBUG_SPINLOCK`）。在源碼樹：

```bash
./scripts/config --enable PROVE_LOCKING --enable DEBUG_ATOMIC_SLEEP
make olddefconfig && make -j"$(nproc)"
```

模組 `deadlock_demo.c`——開兩條 kthread，一條照 A→B 拿鎖、一條照 B→A 拿：

```c
#include <linux/module.h>
#include <linux/kthread.h>
#include <linux/spinlock.h>
#include <linux/delay.h>

static DEFINE_SPINLOCK(a_lock);
static DEFINE_SPINLOCK(b_lock);
static struct task_struct *ta, *tb;

// 順序 A → B
static int ab_ba_thread_a(void *unused)
{
    spin_lock(&a_lock);
    msleep(50);                 // 給 thread B 時間先拿到 b_lock，製造交錯
    spin_lock(&b_lock);         // ← lockdep 在此刻（第一次跑到）就會報環
    spin_unlock(&b_lock);
    spin_unlock(&a_lock);
    return 0;
}

// 順序 B → A（相反！）
static int ab_ba_thread_b(void *unused)
{
    spin_lock(&b_lock);
    msleep(50);
    spin_lock(&a_lock);
    spin_unlock(&a_lock);
    spin_unlock(&b_lock);
    return 0;
}

static int __init dl_init(void)
{
    ta = kthread_run(ab_ba_thread_a, NULL, "ab_ba_a");
    tb = kthread_run(ab_ba_thread_b, NULL, "ab_ba_b");
    return 0;
}
static void __exit dl_exit(void) { }   // kthread 跑完自己結束

module_init(dl_init);
module_exit(dl_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("AB-BA deadlock demo for lockdep");
```

編好放進 initramfs、在 QEMU 裡 `insmod deadlock_demo.ko`，然後 `dmesg`。你會看到上一節那份 `possible circular locking dependency detected`。

**注意一個很重要的細節**：即使這次兩條 thread 因為 `msleep` 的時序**沒有真的卡死**（可能一條先跑完放了鎖，另一條才拿），lockdep **仍然會報警**——因為它只要**分別觀察到** A→B 和 B→A 兩種取得順序就下結論。這正是 lockdep 的價值：把「時序相關、難復現」的死鎖變成「跑過相關路徑就必被抓」。這也是為什麼開發 kernel 一定要開 `PROVE_LOCKING`：它是你的 AB-BA 早期預警。

> 想真的看到「卡死」而非只有警告：把兩條 thread 的 `msleep` 都拿掉、改成緊迫的迴圈反覆拿放，並移除 lockdep（或在真機上），才有機會撞出真死鎖。但**你不需要**——lockdep 的報警已經證明隱患存在，這比復現死鎖有價值得多。

## 動手：CONFIG_DEBUG_ATOMIC_SLEEP 抓「睡在不該睡的地方」

Ch 2 講過 kernel 的執行 context：在**原子上下文（atomic context）**——持有 spinlock、關了搶佔、或在中斷 context——裡**絕對不能睡**。睡了會怎樣？持鎖時睡 → 排程器換上別的 task，那 task 若也要這把鎖就死鎖；中斷 context 睡 → 沒有 task 可以被喚回、系統爛掉。

問題是「會睡的呼叫」藏得很深——`kmalloc(GFP_KERNEL)`（可能為了拿記憶體而 reclaim → 睡）、`mutex_lock`、`msleep`、`copy_from_user`（可能 page fault → 睡）都會睡。你在 spinlock 裡不小心呼叫了它們，多數時候不睡（記憶體剛好夠、頁剛好在），bug 潛伏著。

`CONFIG_DEBUG_ATOMIC_SLEEP` 就是抓這個。它讓 kernel 在**任何可能睡眠的函式入口**（`might_sleep()` 這個標記，散落在 `mutex_lock`、`kmalloc` 慢路徑等處）檢查「現在是不是原子 context」，是的話立刻在 dmesg 吼：

```
BUG: sleeping function called from invalid context at mm/page_alloc.c:...
in_atomic(): 1, irqs_disabled(): 0, non_block: 0, pid: 142, name: insmod
...
Call Trace:
 __might_sleep+0x...
 __kmalloc+0x...          ← 在持鎖狀態下呼叫了會睡的 kmalloc
 my_buggy_func+0x... [sleep_demo]
```

`in_atomic(): 1` 就是關鍵——它說「呼叫這個會睡的函式時，你正處在原子 context」。復現很簡單，寫個 `sleep_demo.c`：

```c
static DEFINE_SPINLOCK(my_lock);
static int __init sd_init(void)
{
    spin_lock(&my_lock);
    kmalloc(4096, GFP_KERNEL);   // GFP_KERNEL 可能睡 → 在持 spinlock 時是 BUG
    spin_unlock(&my_lock);
    return 0;
}
```

`insmod` 後 `dmesg` 就看到上面的 `BUG: sleeping function called from invalid context`。修法：持 spinlock 時若非配記憶體不可，改用 `GFP_ATOMIC`（不睡、失敗就失敗），或更好——把配記憶體移到臨界區外。這條規則接回 Ch 2（context）、Ch 6（GFP flag：`GFP_KERNEL` vs `GFP_ATOMIC`）、Ch 25（持 spinlock 不能睡）。

## 其他死鎖／卡死工具

lockdep 抓「潛在」死鎖，但有些卡死它抓不到（例如等一個永遠不來的事件、真的 ABBA 但沒經過 lockdep 追蹤的鎖）。互補工具：

- **`/proc/lockdep`、`/proc/lockdep_stats`、`/proc/lock_stat`**：lockdep 累積的鎖相依圖與統計。`lock_stat`（需 `CONFIG_LOCK_STAT`）給每個 lock class 的競爭次數、等待時間——找**熱鎖（contention hotspot）**用，是效能調校的入口。
- **hung task detector**（`kernel/hung_task.c`，`CONFIG_DETECT_HUNG_TASK`）：一條 kthread（`khungtaskd`）定期掃描，發現有 task 卡在 **D 狀態（TASK_UNINTERRUPTIBLE）**超過 `hung_task_timeout_secs`（預設 120 秒）就印出該 task 的 stack trace（`INFO: task foo:pid blocked for more than 120 seconds`）。真死鎖、或等 IO 等到天荒地老，都會被它逮到。這正是你在 `linux_commands` 看到 `ps` 裡某個 process 卡在 `D` 狀態時，kernel 這邊的對應機制。
- **soft/hard lockup detector**（`CONFIG_SOFTLOCKUP_DETECTOR` / `HARDLOCKUP_DETECTOR`）：抓「某 CPU 卡在關搶佔／關中斷的迴圈太久」——例如 spinlock 忙等永遠拿不到（真的自旋死鎖）。soft lockup 靠 per-CPU watchdog kthread，hard lockup 靠 NMI/PMU。
- **`CONFIG_DEBUG_ATOMIC_SLEEP`**（上一節）：睡在原子 context。
- **KASAN / KCSAN**（Ch 53）：KASAN 抓記憶體錯誤，KCSAN 抓 data race（沒加鎖或加錯鎖導致的並行讀寫）——和 lockdep 互補，lockdep 管「鎖用得對不對」，KCSAN 管「該加鎖的地方有沒有加」。

橫向連結：這些工具的「使用者視角」你在其他課碰過——`observability_tools` 課用 `strace`/`perf` 從外面看 process 卡住、`gdb` 課教你停在 kernel 函式上看鎖的持有者。lockdep/hung task 是**kernel 主動吐給你**的診斷，不用你去 attach，這是它們和事後用 gdb 檢屍的最大差別：kernel 在出事的當下就把現場 dump 在 `dmesg` 裡。

## 死鎖預防實務

抓到不如不發生。實務上防死鎖的手段，由強到弱：

1. **固定鎖順序，並寫下來**。任何會同時持有多把鎖的子系統，都該有明確的鎖順序約定（常寫在檔案頂端註解或 `Documentation/`）。mm 子系統的 `mmap_lock` → page lock → `i_mmap_rwsem` 這種順序是白紙黑字定死的。看到「持 A 拿 B」就問「全 kernel 是否一致地 A 先於 B」。
2. **盡量少同時持有鎖**。持有的鎖越少、臨界區越短，形成環的機會越小。能在拿第二把鎖前放掉第一把就放。
3. **用 `trylock` 打破環**。拿不到第二把鎖時 `spin_trylock` 失敗就放掉第一把、退避重來（打破「持有並等待」條件）。代價是要處理重試邏輯、可能 livelock，只在無法固定順序時用。
4. **把 lockdep 當 CI 守門**。開發/測試 kernel 全程開 `PROVE_LOCKING`，讓自動化測試（跑各種 workload）去踩各種鎖路徑——踩到一次 AB-BA，CI 就紅，死鎖在合併前就被擋下。這是 kernel 社群的標準做法，也是為什麼主線 kernel 的 AB-BA 死鎖越來越罕見。
5. **鎖順序註解 + `lockdep_assert_held()`**。在函式裡用 `lockdep_assert_held(&some_lock)` 斷言「進來時必須已持有某鎖」，把隱含的鎖契約變成會被 lockdep 檢查的明文。

## 踩雷集錦

1. **在 seqlock 讀臨界區裡拿髒指標去解參考 → oops**。錯誤直覺：「反正 `read_seqretry` 會幫我重試」。正確認識：重試在**讀完之後**才發生，讀的當下若解參考了寫到一半的野指標，早就 crash 了。seqlock 保護純值，指標請用 RCU。

2. **在 seqlock 讀臨界區裡 `printk`/配記憶體/拿鎖 → 副作用重複或死鎖**。錯誤直覺：「讀臨界區就是一段普通程式」。正確認識：它**會被重跑**，任何有副作用的操作都會執行多次。讀臨界區只能把值抄進區域變數，其他一律搬出去。

3. **以為 lockdep 沒報就沒死鎖風險**。錯誤直覺：「跑了半天 lockdep 安靜，我的鎖沒問題」。正確認識：lockdep 只驗證**它實際觀察到的路徑**。某條 A→B 的路徑若測試從沒跑到，它就沒被記錄、環也偵測不出。要靠測試覆蓋率去踩遍鎖路徑，lockdep 才有東西可查。

4. **看到 lockdep splat 就當它是誤報想關掉**。錯誤直覺：「這次明明沒死鎖，lockdep 太吵」。正確認識：`possible circular locking dependency` 幾乎總是真的隱患——「這次沒死」只是這次調度沒交錯到，換個時序就會死。lockdep 極少誤報；報了就去修鎖順序，別去關 `PROVE_LOCKING`。

5. **`inconsistent lock state` 看不懂，其實就是 Ch 25 的 irqsave 問題**。錯誤直覺：把 `{IN-HARDIRQ-W}` 那串指紋當天書。正確認識：它在說「這把鎖有時在中斷 context 拿、有時在開中斷時拿，你少了 `irqsave`」。對照 Ch 25 那個自我死鎖劇本，加上 `spin_lock_irqsave` 就好。

## 進階：再往深一層

- **lockdep 的成本與 chain cache**：lockdep 追蹤所有鎖操作，執行期開銷不小（可能讓 kernel 慢數倍），所以只在除錯 kernel 開、不進 production。它用「lock chain」的 hash cache 加速——同一組鎖序列只驗證一次，之後查 cache。這也是為什麼 lockdep 有 class/chain 數量上限（`MAX_LOCKDEP_KEYS` 等），巨型系統偶爾會 `BUG: MAX_LOCKDEP_CHAINS too low`，要調大。

- **同一 class 的多個實例造成的假環（nesting）**：如果你有一個 lock **陣列**（例如每個 bucket 一把鎖，同屬一 class），合法地「先鎖 bucket[i] 再鎖 bucket[j]」會被 lockdep 當成「同一 class 拿兩次」報 `possible recursive locking`。解法是用 **`spin_lock_nested(&lock, subclass)`** 告訴 lockdep「這是同 class 但不同 nesting 層級」，或用 `lockdep_set_class` 給不同實例不同 class。這是寫「多把同型鎖」時的常見坑，面試常問。

- **面試常問**：「lockdep 怎麼在死鎖沒發生時就抓到？」——答：它記錄 lock class 之間的**取得順序**成有向圖，任何新邊會形成環就報警，不需觀察到實際死鎖。「四個死鎖條件 kernel 主要打破哪個？」——循環等待，靠固定 lock ordering。「seqlock 讀者為什麼可能餓死？」——寫者優先、不等讀者，寫太頻繁讀者一直重試。

- **`raw_spinlock` 與 -rt**：在 `PREEMPT_RT`（Ch 31）下多數 spinlock 變成可睡的 mutex，但 seqlock 的寫者鎖、timekeeping 這類必須用 `raw_spinlock`（真正不睡），否則時間讀取路徑會出問題。這是 -rt 下 seqlock 的一個微妙之處。

## 動手練習

1. **修好 AB-BA**：把上面 `deadlock_demo.c` 的 `ab_ba_thread_b` 改成也照 A→B 順序拿鎖，重新 `insmod`，確認 lockdep **不再**報警。這就是「固定鎖順序」消除死鎖的實作證明。

2. **讀懂 splat 的每一段**：不看本章，只憑 `dmesg` 裡的 splat，指出（a）哪個 task 想拿哪把鎖、（b）它已持有哪把、（c）lockdep 畫的 `Possible unsafe locking scenario` 兩個 CPU 各做什麼、（d）該去改哪個函式。這是 kernel 開發的核心技能。

3. **觸發 `inconsistent lock state`**：寫一把 process context 用 `spin_lock`（不加 irqsave）、又在一個 `timer`（軟中斷 context，Ch 30）裡拿同一把鎖的模組，看 lockdep 報 `inconsistent {SOFTIRQ-ON-W} -> {IN-SOFTIRQ-W}`。然後把 process 那邊改成 `spin_lock_bh` 修好它——親手把 Ch 25 的理論跑一遍。

4. **抓 atomic sleep**：用本章的 `sleep_demo.c`（持 spinlock 呼叫 `GFP_KERNEL` kmalloc），確認 `CONFIG_DEBUG_ATOMIC_SLEEP` 吐出 `BUG: sleeping function called from invalid context`，看 `in_atomic(): 1`。再把 `GFP_KERNEL` 改 `GFP_ATOMIC`，確認警告消失。

5. **hung task**：寫一個模組讓一條 kthread `set_current_state(TASK_UNINTERRUPTIBLE)` 後 `schedule()` 永遠不醒（模擬卡死），等 120 秒看 `khungtaskd` 印出 `blocked for more than 120 seconds`。這就是 `ps` 看到 D 狀態 process 背後 kernel 的告警。

## 本章重點整理

- **seqlock** 用一個序號（寫者遞增兩次、奇數表示寫進行中）讓讀者「樂觀讀 + 讀後檢查」，讀者無鎖但可能重試、寫者優先。適合保護一小組**純值**的一致快照（時間、序號），**不適合**讀出來要解參考的指標——那用 RCU。讀臨界區必須可重入、無副作用。
- **死鎖四條件**（互斥、持有並等待、不可搶奪、循環等待）同時成立才死；kernel 主要靠打破**循環等待**（固定 lock ordering）預防。最小環是兩把鎖不同順序拿的 **AB-BA**。
- **lockdep**（`CONFIG_PROVE_LOCKING`）記錄 lock class 間的取得順序成相依圖，新邊形成環就報 `possible circular locking dependency`——**即使死鎖沒真的發生**，這是它最大的價值。還抓 `inconsistent lock state`（少 irqsave）等。splat 裡最該先看 `Possible unsafe locking scenario` 那張兩 CPU 交錯圖。
- **配套工具**：`/proc/lock_stat`（熱鎖）、hung task detector（D 狀態卡死）、`CONFIG_DEBUG_ATOMIC_SLEEP`（睡在原子 context）。開發 kernel 全程開 lockdep 當 CI 守門。

## 自我檢核

- [ ] 不看筆記，能默寫 seqlock 讀者的 `do { read_seqbegin ... } while (read_seqretry)` pattern，並解釋序號奇偶性的意義
- [ ] 能說出 seqlock 為什麼不能保護「讀出來要解參考的指標」，以及此時該改用什麼
- [ ] 能默寫 seqlock / RCU / rwlock 三者取捨表的關鍵三列（讀者會不會重試、讀者看到最新還是舊值、誰會挨餓）
- [ ] 能說出死鎖四個必要條件，以及 kernel 主要打破哪一個、用什麼手段
- [ ] 面試被問「lockdep 怎麼在死鎖還沒發生時就抓到它」，你能用「記錄 lock class 取得順序、建圖、偵測環」回答
- [ ] 拿到一份 `possible circular locking dependency` splat，能指出哪個 task 想拿哪把鎖、已持哪把、該改哪個函式的鎖順序
- [ ] 能解釋 `BUG: sleeping function called from invalid context` 是什麼機制抓到的，以及怎麼修

## 延伸閱讀

### 官方文件

- **[Documentation/locking/seqlock.rst](https://www.kernel.org/doc/html/latest/locking/seqlock.html)**
  - **讀哪裡**：整篇。seqcount vs seqlock 的區別、各種變體（`seqcount_spinlock_t` 等）、為什麼讀臨界區有那些限制，設計者親自寫的權威說明
  - **和本章的關聯**：本章 seqlock 一節的完整版；寫真實 seqlock 前務必讀

- **[Documentation/locking/lockdep-design.rst](https://www.kernel.org/doc/html/latest/locking/lockdep-design.html)**
  - **讀哪裡**：整篇。lock class、狀態指紋 `{+.+.}` 每個字元的意義、chain cache 如何運作
  - **能學到什麼**：本章「逐段拆解 splat」略過的指紋細節在這裡有完整解碼表；看不懂 `{HARDIRQ-ON-W}` 一類就查這篇

### 文章 / 論文

- **[LWN: A big lockdep improvement / lockdep 系列文](https://lwn.net/Kernel/Index/#Lockdep)** — LWN.net
  - **讀哪裡**：挑「Lockdep」索引下較新的幾篇。lockdep 如何演進、它抓過哪些真實 kernel 死鎖、cross-release 追蹤等進階主題
  - **為什麼值得讀**：看真實案例最能建立「lockdep 到底在幫我抓什麼」的直覺

- **[LWN: Sequence counters and sequential locks](https://lwn.net/Articles/831540/)** — LWN.net
  - **這是什麼**：seqcount/seqlock 在近年（`seqcount_LOCKNAME_t` 引入前後）的整理，講清楚 -rt 下的複雜化與 lockdep 整合
  - **前提**：讀完本章與官方 seqlock.rst

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul E. McKenney（線上免費）
  - **讀哪裡**：deadlock 與 sequence lock 章節。作者是 kernel RCU/並行的核心維護者，把死鎖預防（lock ordering、trylock、hierarchy）講到最透
  - **和本章的關聯**：本章「死鎖預防實務」的理論深水版，也是 Ch 27 RCU 的原典

seqlock、lockdep 補齊了 Part 4 的同步版圖：從 atomic（Ch 24）、spinlock（Ch 25）、mutex（Ch 26）、RCU（Ch 27）到本章的 seqlock 與死鎖工具，你已經有能力讀懂 kernel 任何一段並行程式碼在保護什麼、怕什麼。接下來練習 D 會把這些鎖放進一個會 race 的真實場景，讓你親手復現 race、用 RCU 修好它。

→ [練習 D：race condition 復現與 RCU 修復](./practice-d-race-rcu.md)
