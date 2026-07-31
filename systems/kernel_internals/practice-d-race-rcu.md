# 練習 D — race condition 復現與 RCU 修復

> **這是 Part 4（Ch 24–28）的整合練習。** 這五章你把 kernel 的同步工具箱走完了：atomic 操作與 memory ordering 是所有鎖的地基（Ch 24）、spinlock/qspinlock 是「短臨界區、不能睡」的預設武器（Ch 25）、mutex/semaphore 是「臨界區會睡」時的選擇（Ch 26）、RCU 讓讀者完全無鎖、寫者延後釋放（Ch 27）、seqlock/lockdep 是「寫少讀多」的另一條路以及 kernel 自帶的死鎖偵測器（Ch 28）。這些單獨看都懂了，但真正的功力在於**面對一段會 race 的程式碼，能判斷該上哪把鎖、上完之後量得出讀者競爭有多嚴重、並敢把它換成 RCU**。這個練習用一條「製造 bug → 觀察它崩 → 一階一階修好」的路把它們拼起來：**寫一個故意沒有任何同步的共享連結串列模組，多個 kernel thread 並行讀寫它，先用 KASAN 抓到它真的壞了，再用 spinlock 修好並測量讀者互相擋的代價，最後換成 RCU 讓讀者無鎖、對比正確性與吞吐。全程用 lockdep 確認沒有引入死鎖。**

## 背景與動機：為什麼「修好」不是終點，「量出代價」才是

你當然知道「共享資料要加鎖」。但 kernel 工程和面試真正在考的不是「知不知道要加鎖」，而是三件更難的事：

1. **race 到底長什麼樣、怎麼證明它存在。** 一段沒鎖的並行程式碼，多數時候跑起來「看起來正常」——race 是機率事件，壓力不夠時它藏著。你要能主動把它逼出來（拉高執行緒數、加 delay 放大時窗），並用 KASAN（Ch 53 預告）把「它讀到了已經被 free 的節點」這件事釘死成一份報告，而不是靠「跑了一萬次沒崩應該沒事」的僥倖。
2. **加了鎖之後，讀者互相擋的代價。** 用一把 spinlock 保護整個串列，正確性立刻對了——但如果這是個「99% 讀、1% 寫」的結構（路由表、設定、觀測用的 map 都是這種），你讓所有讀者**互相**排隊，是拿正確性換掉了本來可以並行的讀。這個練習逼你**量出來**：4 個讀者 thread 在 spinlock 下每秒能做幾次查詢，換 RCU 之後又是幾次。沒量過的人只會背「RCU 讀很快」，量過的人知道快多少、為什麼。
3. **RCU 不是「更快的鎖」，是換了一套規則。** RCU 讀者不阻塞寫者、寫者不等讀者——代價是寫者改完不能立刻 free 舊節點，要等一個 grace period（所有 CPU 都離開過一次讀端臨界區）才安全釋放。這個「延後釋放」是 RCU 的靈魂，也是最容易寫錯的地方（漏了 `synchronize_rcu`/`call_rcu` 就是 UAF，加了但 list 操作沒用 `_rcu` 變體就是讀者看到半更新的指標）。你要親手把它寫對，並在 gdb / lockdep 下驗證。

還有一層：**失敗本身是教材。** 進階任務要你**故意**寫錯——埋一個 AB-BA 死鎖讓 lockdep 印出 splat，逐行讀懂它在講哪兩把鎖、以什麼順序互相等；再故意在 spinlock 臨界區裡放會睡的呼叫（`kmalloc(GFP_KERNEL)` 或 `msleep`），讓 `CONFIG_DEBUG_ATOMIC_SLEEP` 抓你。這兩個是 kernel 最常踩、也最能靠工具自動抓的 bug，學會讀報告勝過背十條鎖規則。

**全程在 Ch 0 的 QEMU + gdb 環境驗證，但這次 config 要多開三個除錯選項**（KASAN、PROVE_LOCKING、DEBUG_ATOMIC_SLEEP，下面「環境準備」會逐一給），而且**一定要 `-smp 4`**——race、spinlock 競爭、RCU 的好處都只在多核上才顯現，單核跑這個練習你什麼都看不到。

## 先建立心智模型

動手前先把三個階段各自「保護了什麼、代價是什麼」在腦中對齊。假設有一個共享的單向串列，寫者會 `add`（配一個節點插進去）和 `del`（拔一個節點出來並 `kfree`），讀者會 `lookup`（沿著串列走、比對 key）。

```
【階段 0：無鎖】—— race 現形
   Reader A: 正在走到節點 N，讀 N->key ...
   Writer B: 同時 list_del(N); kfree(N);
             ─────────────────────────►  N 這塊記憶體被還回 slab
   Reader A: ... 繼續讀 N->next        ►  讀到已 free 的記憶體 = UAF
                                          KASAN: use-after-free 報告

【階段 1：spinlock 整表鎖】—— 正確，但讀者互擋
   Reader A ─┐
   Reader B ─┼─► 全部搶同一把 lock ─► 一次只有一個能進臨界區
   Reader C ─┘                        4 個讀者被序列化成 1 個
   Writer   ─┘                        正確性 OK，但讀的並行度沒了

        lock ┌───────────────┐ unlock
   ──────────┤  走串列 / 改串列 ├──────────  臨界區內誰都進不來
             └───────────────┘

【階段 2：RCU】—— 讀者無鎖，寫者延後釋放
   Reader A ─► rcu_read_lock(); 走串列（不阻塞任何人）; rcu_read_unlock();
   Reader B ─► rcu_read_lock(); 走串列（和 A、和 Writer 同時進行）;   ...
   Reader C ─► （三個讀者真正並行，沒有互擋）
   Writer   ─► lock(); list_del_rcu(N); unlock();
               ★ 不能立刻 kfree(N)！★
               synchronize_rcu();  // 等所有 CPU 離開過讀端一次（grace period）
               kfree(N);           // 現在保證沒有讀者還握著 N 了
```

五個關鍵認知，對上 Part 4 的章節：

- **race 是「一邊讀一邊被釋放」被兩顆 CPU 交錯執行的結果**（Ch 24）。`list_del` 改三個指標、`kfree` 還回記憶體，這整串不是原子的；讀者在中間插進來看到的是半更新的串列或已死節點。Ch 24 的 memory ordering 是底層原因：更新順序沒屏障保證，讀者可能看到「next 已改、內容還沒寫完」。
- **spinlock 保護的是「臨界區一次只有一顆 CPU 在跑」，不是資料本身**（Ch 25）。整表一把鎖最好證明正確，代價是把所有存取（讀+寫）序列化。qspinlock（v4.2 起的預設實作）用 MCS 排隊避免 cacheline 彈跳，但**排隊本身就是代價**——讀者多時量到的就是排隊延遲。
- **rwlock 能讓讀者並行，但 Linux 幾乎不推薦**（Ch 25）。它有 writer starvation、且每次拿讀鎖都要寫共享的 rwlock cacheline（跨 CPU 原子操作、cacheline 彈跳）。所以 kernel 對「讀多寫少」的答案通常是 **RCU 或 seqlock，不是 rwlock**——這練習直接跳過 rwlock 去 RCU。
- **RCU 讀端「零成本」的前提：讀者不阻塞寫者、也不要求看到最新值**（Ch 27）。`rcu_read_lock()` 在非 preempt kernel 上幾乎是 no-op。交換條件是讀者可能看到舊版本節點（寫者已 `list_del_rcu` 但還沒過 grace period），這對「查路由、查設定」完全 OK；**若讀者需要「改了立刻讀到新值」，RCU 不適合。**
- **RCU 正確性 = 兩件事都對：list 操作用 `_rcu` 變體 + 釋放延後到 grace period 之後**（Ch 27）。`list_*_rcu` 內含 `rcu_assign_pointer`/`rcu_dereference` 的屏障，保證讀者看到的是完整的舊或新節點、不是半個；`synchronize_rcu()`/`call_rcu()` 保證舊節點在最後一個讀者離開後才 free。**漏前者是讀到半更新，漏後者是 UAF，兩個 KASAN 都抓得到。**

## 任務規格

### 主線任務：三階段——復現、spinlock 修、RCU 修

寫一個核心模組 `racelist`，維護一個共享的整數 key→value 串列，並起數個 kernel thread（Ch 10 的 `kthread_run`）並行操作它。模組有三個編譯期或執行期可切換的模式，對應三個階段。

**共享資料結構**（三階段共用）：

```c
struct entry {
    struct list_head list;
    int key;
    int value;
    struct rcu_head rcu;   /* RCU 階段用來延後釋放 */
};
static LIST_HEAD(the_list);   /* 全域共享串列，見 Ch 5 */
```

**執行緒配置**：起 `N_READERS`（預設 4）個讀者 thread 和 `N_WRITERS`（預設 2）個寫者 thread。
- **讀者**：迴圈裡隨機挑一個 key，`lookup(key)`——沿串列走、比對、讀出 value。每秒累計自己做了幾次 lookup（用 per-thread 或 atomic 計數，Ch 24 / Ch 7 per-CPU）。
- **寫者**：迴圈裡隨機 `add(key, value)`（`kmalloc` 一個節點插進去）或 `del(key)`（找到後拔出來 free）。

**三個模式**：

| 模式 | 讀者同步 | 寫者同步 | 期望結果 |
|---|---|---|---|
| `MODE_RACY` | 無 | 無 | KASAN 報 use-after-free / 資料損毀 / oops |
| `MODE_SPINLOCK` | `spin_lock` | `spin_lock` | 正確，但讀者吞吐被寫者與彼此擋住 |
| `MODE_RCU` | `rcu_read_lock` + `_rcu` 遍歷 | `spin_lock`（寫者互斥）+ `_rcu` 改 + 延後 free | 正確，讀者吞吐遠高於 spinlock 模式 |

用 module param `mode`（0/1/2）切換，`insmod racelist.ko mode=0`。模組跑 `run_secs`（預設 5）秒後自動停所有 thread，印出統計（總 lookup 數、每秒 lookup 數、寫者 add/del 數）。

**驗收核心**：MODE_RACY 在 `-smp 4` + KASAN 下要能穩定觸發報告（可能要跑幾次、或靠下面「放大時窗」的技巧）；MODE_SPINLOCK 與 MODE_RCU 都要**零 KASAN 報告、零 lockdep splat**，且 MODE_RCU 的每秒 lookup 數明顯高於 MODE_SPINLOCK。

### 進階任務 A：埋一個 AB-BA 死鎖，讓 lockdep 抓你

在修好的版本上，故意引入第二把鎖製造經典的 **AB-BA 死鎖**：兩把鎖 `lock_a`、`lock_b`，一條路徑先拿 A 再拿 B，另一條路徑先拿 B 再拿 A。真正的死鎖要兩條路徑同時跑到中間點才會卡死（機率事件），但 **lockdep（`CONFIG_PROVE_LOCKING`，Ch 28）不需要真的死鎖發生**——它在你**第一次**以相反順序拿鎖時就記下「鎖順序反了」並印出 splat。你的任務：加這段錯誤程式碼、`insmod`、抓到 lockdep splat、**逐行讀懂它指出的兩條 lock chain**，然後把鎖順序統一（永遠先 A 後 B）修好，確認 splat 消失。

### 進階任務 B：在 spinlock 臨界區裡睡，讓 DEBUG_ATOMIC_SLEEP 抓你

spinlock 臨界區是**原子上下文（atomic context）**——持有 spinlock 時 preempt 被關掉，你**不能睡**（不能 `msleep`、不能 `kmalloc(GFP_KERNEL)`，因為它們可能睡，Ch 2 的 context 規則、Ch 6 的 GFP 語意）。故意在 `spin_lock` 和 `spin_unlock` 之間放一個 `msleep(1)` 或 `kmalloc(..., GFP_KERNEL)`，`CONFIG_DEBUG_ATOMIC_SLEEP` 會印出 `BUG: sleeping function called from invalid context`。你的任務：觸發它、讀懂報告指出的「在哪個 atomic 上下文、呼叫了哪個會睡的函式」，然後改對（把配置移到臨界區外，或臨界區內改用 `GFP_ATOMIC`——並理解 `GFP_ATOMIC` 的代價，見卡關提示 4）。

### 驗收標準

| # | 檢查項 | 怎麼驗 |
|---|---|---|
| 1 | MODE_RACY 在 `-smp 4` + KASAN 下觸發 use-after-free 報告 | `insmod racelist.ko mode=0`，`dmesg` 看 KASAN splat |
| 2 | MODE_SPINLOCK 正確：跑完無 KASAN、無 lockdep、統計數字合理 | `insmod racelist.ko mode=1`，`dmesg` 乾淨 |
| 3 | MODE_RCU 正確：跑完無 KASAN、無 lockdep | `insmod racelist.ko mode=2`，`dmesg` 乾淨 |
| 4 | MODE_RCU 的每秒 lookup 數 > MODE_SPINLOCK（讀者無鎖的紅利） | 對照兩次的統計輸出 |
| 5 | 進階 A：能觸發 lockdep AB-BA splat，能指出反序的兩把鎖 | 開 `deadlock=1` param，`dmesg` 看 possible circular locking |
| 6 | 進階 A：修正鎖順序後 splat 消失 | 統一順序後重跑，`dmesg` 乾淨 |
| 7 | 進階 B：能觸發 DEBUG_ATOMIC_SLEEP 的 sleeping-in-atomic 報告 | 開 `sleep_in_lock=1` param，`dmesg` 看 BUG: sleeping function |
| 8 | 進階 B：改對後（配置移出臨界區或用 GFP_ATOMIC）報告消失 | 修正後重跑，`dmesg` 乾淨 |
| 9 | `rmmod` 乾淨：所有 kthread 停掉、所有節點釋放、RCU callback 排空 | `rmmod racelist` 後 `dmesg` 無 leak/警告 |

## 環境準備：這次要多開三個 config

Ch 0 的除錯 config 是基礎，這個練習**額外**需要三個選項。在你的 kernel 源碼樹：

```bash
./scripts/config \
    --enable KASAN \
    --enable KASAN_GENERIC \
    --enable PROVE_LOCKING \
    --enable DEBUG_ATOMIC_SLEEP \
    --enable DEBUG_SPINLOCK \
    --enable DEBUG_LOCK_ALLOC \
    --enable SMP
make olddefconfig
make -j"$(nproc)"
```

每個為什麼開：

| 選項 | 作用 | 這練習哪裡用到 |
|---|---|---|
| `KASAN` + `KASAN_GENERIC` | KernelAddressSanitizer，用 shadow memory 抓 use-after-free / out-of-bounds | 主線階段 0，把 racy 版的 UAF 釘成報告 |
| `PROVE_LOCKING` | lockdep 死鎖偵測（會自動連帶開 `DEBUG_LOCK_ALLOC`） | 進階 A，抓 AB-BA 錯誤鎖序 |
| `DEBUG_ATOMIC_SLEEP` | 偵測「在 atomic 上下文呼叫會睡的函式」 | 進階 B，抓 spinlock 內睡覺 |
| `DEBUG_SPINLOCK` | spinlock 額外檢查（double-unlock、未初始化等） | 全程保險 |
| `SMP` | 對稱多處理器支援 | 全程——race/競爭/RCU 只在多核顯現 |

> **KASAN 讓 kernel 變慢、變肥**（每次記憶體存取多一次 shadow 檢查、映像大很多、開機慢）。這對學習沒問題，但**別把 KASAN 的 kernel 拿去跑效能量測**——階段 1/2 量 lookup 吞吐做「相對比較」還行（兩邊都被 KASAN 拖慢，比例大致還在），但別把 KASAN 下量到的絕對數字當真。想量乾淨數字，另外 build 一顆關 KASAN 的（見延伸挑戰）。

QEMU 開機**一定要 `-smp 4`**：

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -smp 4 -m 1G -nographic
```

`-m 1G`：KASAN 吃記憶體，512M 可能不夠。`-smp 4`：四核，讓四個讀者能真的並行。

## 期望輸出範例

### 階段 0：KASAN 抓到 use-after-free

MODE_RACY 跑起來，多半在一兩秒內（有時要幾次）撞出 KASAN 報告：

```
/ # insmod racelist.ko mode=0 run_secs=5
racelist: mode=RACY readers=4 writers=2 (no synchronization — expect KASAN!)
==================================================================
BUG: KASAN: use-after-free in racelist_lookup+0x64/0x120 [racelist]
Read of size 4 at addr ffff888103a4d008 by task racelist_rd/0/145

CPU: 2 PID: 145 Comm: racelist_rd/0 Not tainted 6.12.0 #1
Call Trace:
 <TASK>
 dump_stack_lvl+0x4d/0x70
 print_report+0xcf/0x670
 kasan_report+0xb6/0xf0
 racelist_lookup+0x64/0x120 [racelist]      <-- 讀到已 free 的節點
 racelist_reader_fn+0x88/0x140 [racelist]
 kthread+0x2e8/0x3a0
 ret_from_fork+0x2c/0x50
 </TASK>

Freed by task racelist_wr/1:
 kfree+0x...
 racelist_del+0x9c/0xe0 [racelist]          <-- 另一個 CPU 上的寫者剛 free 它
 racelist_writer_fn+0x...

The buggy address belongs to the object at ffff888103a4d000
 which belongs to the cache kmalloc-32 of size 32
==================================================================
```

三行是重點：`Read of size 4 ... by task racelist_rd/0`（**誰在讀**：讀者 thread 0）、`in racelist_lookup`（**在哪讀**：lookup 走串列時）、`Freed by task racelist_wr/1`（**誰 free 的**：寫者 thread 1，在另一顆 CPU）。這就是「一邊走串列、一邊被別的 CPU free 掉」的鐵證——你在 Ch 27 讀到的 UAF，現在是自己親手做出來的。

### 階段 1：spinlock 版乾淨，但量到讀者競爭

```
/ # insmod racelist.ko mode=1 run_secs=5
racelist: mode=SPINLOCK readers=4 writers=2
racelist: === stats after 5s ===
racelist:   reader[0]  1 842 331 lookups   (368466/s)
racelist:   reader[1]  1 839 002 lookups   (367800/s)
racelist:   reader[2]  1 851 774 lookups   (370354/s)
racelist:   reader[3]  1 838 219 lookups   (367643/s)
racelist:   TOTAL      7 371 326 lookups   (1474265/s)
racelist:   writers: add=48221 del=47903
racelist: no KASAN / no lockdep — correct.
```

四個讀者加起來每秒約 147 萬次 lookup。注意每個讀者都被卡在同一把 spinlock 上排隊——四核但讀的並行度接近 1。

### 階段 2：RCU 版乾淨，讀者吞吐跳上去

```
/ # insmod racelist.ko mode=2 run_secs=5
racelist: mode=RCU readers=4 writers=2
racelist: === stats after 5s ===
racelist:   reader[0]  6 012 887 lookups   (1202577/s)
racelist:   reader[1]  5 998 104 lookups   (1199620/s)
racelist:   reader[2]  6 021 445 lookups   (1204289/s)
racelist:   reader[3]  6 007 992 lookups   (1201598/s)
racelist:   TOTAL     24 040 428 lookups   (4808085/s)
racelist:   writers: add=47180 del=46902
racelist: no KASAN / no lockdep — correct.
```

同樣四核、同樣讀者數，總吞吐從 147 萬跳到 480 萬/秒——約 3.3 倍。讀者不再互擋、也不被寫者擋，四核真的並行了。**這些絕對數字是 KASAN 拖慢後的、且高度依賴機器/QEMU host，你自己跑到的數會不同——重點是 RCU/spinlock 的比值明顯 > 1，不是這幾個數字本身。**

### 進階 A：lockdep 的 AB-BA splat

`insmod racelist.ko mode=1 deadlock=1`，第一次以反序拿鎖時 lockdep 就開罵（不用真的卡死）：

```
======================================================
WARNING: possible circular locking dependency detected
6.12.0 #1 Not tainted
------------------------------------------------------
racelist_wr/0/151 is trying to acquire lock:
 ffffffffc0a12080 (lock_a){+.+.}-{2:2}, at: racelist_path_b+0x3c/0x80 [racelist]

but task is already holding lock:
 ffffffffc0a120c0 (lock_b){+.+.}-{2:2}, at: racelist_path_b+0x20/0x80 [racelist]

which lock already depends on the new lock.

the existing dependency chain (in reverse order) is:

-> #1 (lock_b){+.+.}-{2:2}:
       ... racelist_path_a+0x48/0x80 [racelist]    <-- path_a: 先 A 後 B

-> #0 (lock_a){+.+.}-{2:2}:
       ... racelist_path_b+0x3c/0x80 [racelist]    <-- path_b: 先 B 後 A

 Possible unsafe locking scenario:
       CPU0                    CPU1
       ----                    ----
  lock(lock_a);
                               lock(lock_b);
                               lock(lock_a);
  lock(lock_b);
  *** DEADLOCK ***
```

讀法：`path_a` 建立了「A→B」的依賴（拿著 A 時去拿 B），`path_b` 又建立「B→A」（拿著 B 時去拿 A）。lockdep 把兩條合起來偵測到環（A→B→A），印出那個 `CPU0/CPU1` 的假想交錯——這就是 AB-BA。**它沒有真的死鎖**，是 lockdep 從「你曾經以兩種相反順序拿過這兩把鎖」推論出「總有一天會死」。修法：規定全域鎖序（永遠先 A 後 B），把 `path_b` 改成也先拿 A。

### 進階 B：DEBUG_ATOMIC_SLEEP 的報告

`insmod racelist.ko mode=1 sleep_in_lock=1`：

```
BUG: sleeping function called from invalid context at .../racelist.c:210
in_atomic(): 1, irqs_disabled(): 0, non_block: 0, pid: 152, name: racelist_wr/1
preempt_count: 1, expected: 0
2 locks held by racelist_wr/1:
 #0: ... (the_lock){+.+.}-{2:2}, at: racelist_writer_fn+0x...   <-- 持有 spinlock
CPU: 1 PID: 152 Comm: racelist_wr/1
Call Trace:
 dump_stack_lvl+0x4d/0x70
 __might_resched+0x1a2/0x2d0
 __might_sleep+0x8e/0xa0
 msleep+0x1c/0x80                <-- 在原子上下文呼叫 msleep（會睡）
 racelist_add+0x...  [racelist]
```

`in_atomic(): 1` + `preempt_count: 1` 告訴你「現在在原子上下文（因為持有 spinlock，preempt 被關）」，`msleep` 這一行告訴你「你偏偏呼叫了會睡的函式」。修法：把 `msleep`/`kmalloc(GFP_KERNEL)` 移到 `spin_unlock` 之後，或臨界區內非配不可時改 `GFP_ATOMIC`（不睡，但可能配失敗，見卡關提示 4）。

## 卡關提示

1. **race 逼不出來？拉高壓力 + 放大時窗。** race 是機率事件，`-smp 4` 只是必要條件。三個放大手段：(a) 讀者/寫者 thread 數各再加倍；(b) 在 racy 版的 `lookup` 走到節點後、讀 `value` 前，插一個 `cpu_relax()` 或極短 `ndelay(100)`，人為拉長「持有指向某節點的指標」的時窗，讓寫者更容易在這期間 free 掉它；(c) 寫者的 add/del 頻率調高（別 `msleep`，直接 busy loop）。KASAN 本身也會讓時序改變、更容易撞到。若還是逼不出，確認你真的在 `-smp 4`（`nproc` 在 QEMU 裡應回 4）且 KASAN 真的開了（`dmesg | grep -i kasan` 開機時應有 KASAN 初始化訊息）。

2. **RCU 版「跑起來不崩」不代表對——三個最常見的漏。** (a) **讀端漏 `rcu_read_lock()`/`rcu_read_unlock()`**：沒有它，grace period 可能在你走串列走到一半就結束、舊節點被 free，還是 UAF。KASAN 會抓到，但只在壓力夠時。(b) **遍歷用了 `list_for_each_entry` 而非 `list_for_each_entry_rcu`**：前者少了 `rcu_dereference` 的屏障，可能讀到半發布的指標（尤其 weak memory model 的 ARM64 上更容易翻車，x86 的 TSO 較寬容但仍不保證）。(c) **寫者改完直接 `kfree` 而非 `synchronize_rcu` 後才 free（或 `call_rcu`）**：這是最經典的 RCU UAF。三者都要對，缺一個就是「大部分時候對、壓力上來就 UAF」的定時炸彈。

3. **`synchronize_rcu()` 會睡——別在 spinlock / atomic 上下文裡呼叫它。** 寫者的流程是「`spin_lock`（和其他寫者互斥）→ `list_del_rcu` → `spin_unlock` → `synchronize_rcu` → `kfree`」。注意 `synchronize_rcu` 在 `spin_unlock` **之後**：它會阻塞當前 thread 直到 grace period 結束（可能幾毫秒），是會睡的操作，放在 spinlock 內會被進階 B 那個 DEBUG_ATOMIC_SLEEP 抓到。如果你不想讓寫者阻塞等 grace period，用 `call_rcu(&entry->rcu, free_cb)` 註冊一個 callback 非同步釋放——它不睡，但你 `rmmod` 時要 `rcu_barrier()` 確保所有 callback 都跑完了才卸載模組（見提示 5）。

4. **`GFP_ATOMIC` 不是「GFP_KERNEL 的原子版免費升級」——它會用光 emergency reserve 且更容易失敗。** 進階 B 若你選擇「臨界區內改用 `GFP_ATOMIC`」修，要知道代價：`GFP_KERNEL` 配不到時可以睡、可以觸發 reclaim（Ch 22）等記憶體出來；`GFP_ATOMIC` 不能睡，只能從 emergency reserve 硬挖，**配失敗（回 NULL）的機率高得多**，所以你**必須檢查回傳 NULL 並優雅處理**。正解通常是**重構成臨界區外配置**：先在鎖外 `kmalloc(GFP_KERNEL)` 好節點，再進臨界區只做「插串列」這種不睡的短動作。這是 kernel 常見 pattern：配置在鎖外，鎖內只做指標操作。

5. **`rmmod` 前沒排空 kthread 和 RCU callback，卸載時 crash。** 兩個收尾陷阱：(a) module exit 要先 `kthread_stop()` 所有讀寫 thread（配合 thread 迴圈裡 `kthread_should_stop()` 檢查），確認它們都出來了、不再碰串列，**才**開始拆串列——否則你拆到一半 thread 還在讀。(b) 如果 RCU 階段用了 `call_rcu`，exit 時串列清空後要 `rcu_barrier()` 等所有已註冊的 free callback 跑完，否則模組 text 段被卸載後 callback 才觸發 → 呼叫到已不存在的函式 → oops。用 `synchronize_rcu` 同步版的話這個問題較小，但清完串列後補一個 `synchronize_rcu()` 仍是好習慣。

## 分步實作建議

1. **先寫「架構」，用 MODE_SPINLOCK 打底。** 先把 struct、`the_list`、`add`/`del`/`lookup`、`N` 個讀者 thread + `M` 個寫者 thread（`kthread_run`）、統計、`run_secs` 後停、乾淨 `rmmod` 這套骨架用**正確的 spinlock 版**寫出來跑通。這一步不追求 race，是把「多 thread 生命週期管理 + 統計 + 收尾」弄對，這是最容易漏 `kthread_stop` 出 bug 的地方。
2. **把 spinlock 拔掉做出 MODE_RACY。** 複製 `add`/`del`/`lookup` 三個函式，去掉所有 `spin_lock`，做 `mode=0`。加提示 1 的放大手段。`-smp 4` + KASAN 跑，逼出 use-after-free。**讀懂那份 KASAN 報告的三行**（誰讀、在哪讀、誰 free），這是這個練習的第一個「啊哈」。
3. **量 spinlock 的讀者競爭。** 回到 MODE_SPINLOCK，把統計印清楚（每讀者的 lookup 數 + 每秒數 + 總和）。記下總吞吐。可以順手試把讀者從 4 加到 8，看總吞吐**幾乎不變甚至下降**（更多讀者搶同一把鎖，排隊更久）——這是「整表鎖沒有讀並行度」的直接證據。
4. **做 MODE_RCU。** 讀端 `rcu_read_lock` + `list_for_each_entry_rcu`；寫端 `spin_lock`（寫者間互斥）+ `list_add_rcu`/`list_del_rcu` + `spin_unlock` + `synchronize_rcu()` + `kfree`（或 `call_rcu`）。跑 `mode=2`，確認**無 KASAN、無 lockdep**，且總吞吐明顯高於步驟 3。把 4/8 讀者都試一次，看 RCU 下加讀者吞吐會**線性上升**（無互擋）。
5. **做兩個進階「故意寫錯」。** deadlock=1 加第二把鎖與反序路徑，抓 lockdep splat 並逐行讀懂、修好。sleep_in_lock=1 在臨界區放 `msleep`/`GFP_KERNEL` 配置，抓 DEBUG_ATOMIC_SLEEP，用「配置移出臨界區」修好。這兩個是本練習「失敗即教材」的高潮。

## 完整參考解答

<details>
<summary>點開看完整可編譯解答（racelist.c 三模式全含 + Makefile + 壓測腳本）</summary>

下面這一份 `racelist.c` 把三個模式、兩個進階故意錯誤全放在同一個模組裡，用 module param 切換，方便你一次編一次玩全部。生產程式碼不會這樣把 racy 版留著，但教學上放一起最能對照。

### `racelist.c`

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/list.h>
#include <linux/rculist.h>
#include <linux/spinlock.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/random.h>
#include <linux/atomic.h>
#include <linux/ktime.h>

/* ---- 可調參數 ---- */
static int mode = 1;          /* 0=RACY 1=SPINLOCK 2=RCU */
static int n_readers = 4;
static int n_writers = 2;
static int run_secs = 5;
static int key_space = 64;    /* key 範圍 0..key_space-1，控制串列長度 */
static int deadlock = 0;      /* 進階 A：埋 AB-BA 死鎖 */
static int sleep_in_lock = 0; /* 進階 B：在 spinlock 內睡 */
module_param(mode, int, 0444);
module_param(n_readers, int, 0444);
module_param(n_writers, int, 0444);
module_param(run_secs, int, 0444);
module_param(key_space, int, 0444);
module_param(deadlock, int, 0444);
module_param(sleep_in_lock, int, 0444);

#define MODE_RACY     0
#define MODE_SPINLOCK 1
#define MODE_RCU      2

struct entry {
    struct list_head list;
    int key;
    int value;
    struct rcu_head rcu;
};

static LIST_HEAD(the_list);
static DEFINE_SPINLOCK(the_lock);      /* 保護 the_list（spinlock 與 RCU 寫端共用） */

/* 進階 A：AB-BA 用的兩把鎖 */
static DEFINE_SPINLOCK(lock_a);
static DEFINE_SPINLOCK(lock_b);

/* 統計 */
#define MAX_THREADS 32
static struct task_struct *reader_ts[MAX_THREADS];
static struct task_struct *writer_ts[MAX_THREADS];
static unsigned long reader_counts[MAX_THREADS];
static atomic_long_t total_add = ATOMIC_LONG_INIT(0);
static atomic_long_t total_del = ATOMIC_LONG_INIT(0);

/* ============ 三個核心操作 ============ */

/* --- lookup：讀者用 --- */
static int racelist_lookup(int key)
{
    struct entry *e;
    int val = -1;

    if (mode == MODE_RCU) {
        rcu_read_lock();
        list_for_each_entry_rcu(e, &the_list, list) {
            if (e->key == key) { val = e->value; break; }
        }
        rcu_read_unlock();
    } else if (mode == MODE_SPINLOCK) {
        unsigned long flags;
        spin_lock_irqsave(&the_lock, flags);
        list_for_each_entry(e, &the_list, list) {
            if (e->key == key) { val = e->value; break; }
        }
        spin_unlock_irqrestore(&the_lock, flags);
    } else { /* MODE_RACY：沒有任何同步 */
        list_for_each_entry(e, &the_list, list) {
            if (e->key == key) {
                cpu_relax();          /* 放大時窗：讓寫者更容易在這期間 free 掉 e */
                val = e->value;       /* ← 這一行在 racy 模式下會 KASAN UAF */
                break;
            }
        }
    }
    return val;
}

/* --- add：寫者用。節點在鎖外配好（見卡關提示 4） --- */
static void racelist_add(int key, int value)
{
    struct entry *e = kmalloc(sizeof(*e), GFP_KERNEL); /* 鎖外配置，可睡 OK */
    if (!e)
        return;
    e->key = key;
    e->value = value;

    if (mode == MODE_RACY) {
        list_add_rcu(&e->list, &the_list); /* racy：無鎖插入（_rcu 只是屏障，仍無互斥） */
    } else {
        unsigned long flags;
        spin_lock_irqsave(&the_lock, flags);
        if (sleep_in_lock)
            msleep(1);                     /* 進階 B：臨界區內睡 → DEBUG_ATOMIC_SLEEP */
        if (mode == MODE_RCU)
            list_add_rcu(&e->list, &the_list);
        else
            list_add(&e->list, &the_list);
        spin_unlock_irqrestore(&the_lock, flags);
    }
    atomic_long_inc(&total_add);
}

static void free_entry_rcu(struct rcu_head *rcu)
{
    struct entry *e = container_of(rcu, struct entry, rcu);
    kfree(e);
}

/* --- del：寫者用 --- */
static void racelist_del(int key)
{
    struct entry *e, *found = NULL;

    if (mode == MODE_RACY) {
        list_for_each_entry(e, &the_list, list) {
            if (e->key == key) { found = e; break; }
        }
        if (found) {
            list_del(&found->list);
            kfree(found);              /* racy：另一 CPU 的讀者可能正握著 found → UAF */
        }
    } else {
        unsigned long flags;
        spin_lock_irqsave(&the_lock, flags);
        list_for_each_entry(e, &the_list, list) {
            if (e->key == key) { found = e; break; }
        }
        if (found) {
            if (mode == MODE_RCU) {
                list_del_rcu(&found->list);
                /* 延後釋放：等 grace period（見卡關提示 3/5）*/
                call_rcu(&found->rcu, free_entry_rcu);
            } else {
                list_del(&found->list);
            }
        }
        spin_unlock_irqrestore(&the_lock, flags);
        if (found && mode == MODE_SPINLOCK)
            kfree(found);              /* spinlock 版：鎖外 free（此時已無人能拿到它） */
    }
    if (found)
        atomic_long_inc(&total_del);
}

/* ============ 進階 A：AB-BA 死鎖路徑 ============ */
static void racelist_path_a(void)   /* 先 A 後 B */
{
    spin_lock(&lock_a);
    spin_lock(&lock_b);
    /* ... 假裝做事 ... */
    spin_unlock(&lock_b);
    spin_unlock(&lock_a);
}
static void racelist_path_b(void)   /* 先 B 後 A —— 相反順序，lockdep 會抓 */
{
    spin_lock(&lock_b);
    spin_lock(&lock_a);
    spin_unlock(&lock_a);
    spin_unlock(&lock_b);
}
/* 修法：把 path_b 也改成「先 A 後 B」，統一全域鎖序即可（見文末）*/

/* ============ kthread 函式 ============ */
static int reader_fn(void *arg)
{
    long idx = (long)arg;
    unsigned long n = 0;
    while (!kthread_should_stop()) {
        int key = get_random_u32() % key_space;
        racelist_lookup(key);
        n++;
        if ((n & 0xffff) == 0)
            cond_resched();          /* 讓出 CPU，避免 RCU stall / soft lockup 警告 */
    }
    reader_counts[idx] = n;
    return 0;
}

static int writer_fn(void *arg)
{
    while (!kthread_should_stop()) {
        int key = get_random_u32() % key_space;
        if (get_random_u32() & 1)
            racelist_add(key, key * 10);
        else
            racelist_del(key);

        if (deadlock) {              /* 進階 A：兩條反序路徑都跑到 */
            racelist_path_a();
            racelist_path_b();
        }
        cond_resched();
    }
    return 0;
}

/* ============ init / exit ============ */
static const char *mode_name(void)
{
    switch (mode) {
    case MODE_RACY:     return "RACY";
    case MODE_SPINLOCK: return "SPINLOCK";
    case MODE_RCU:      return "RCU";
    default:            return "?";
    }
}

static int __init racelist_init(void)
{
    long i;

    if (n_readers > MAX_THREADS) n_readers = MAX_THREADS;
    if (n_writers > MAX_THREADS) n_writers = MAX_THREADS;

    pr_info("racelist: mode=%s readers=%d writers=%d run_secs=%d%s\n",
            mode_name(), n_readers, n_writers, run_secs,
            mode == MODE_RACY ? " (no sync — expect KASAN!)" : "");

    /* 預先塞一些節點，讓串列非空 */
    for (i = 0; i < key_space / 2; i++)
        racelist_add(i, i * 10);
    atomic_long_set(&total_add, 0);   /* 預塞不計入統計 */

    for (i = 0; i < n_writers; i++)
        writer_ts[i] = kthread_run(writer_fn, (void *)i, "racelist_wr/%ld", i);
    for (i = 0; i < n_readers; i++)
        reader_ts[i] = kthread_run(reader_fn, (void *)i, "racelist_rd/%ld", i);

    /* init 不能自己睡太久占住 insmod；起一個計時器思路或直接讓 thread 自己跑。
     * 這裡用最簡單法：init 睡 run_secs 秒後停 thread、印統計。
     * （insmod 會阻塞這麼久，教學可接受；生產會用 delayed work。）*/
    ssleep(run_secs);

    for (i = 0; i < n_readers; i++)
        if (reader_ts[i]) kthread_stop(reader_ts[i]);
    for (i = 0; i < n_writers; i++)
        if (writer_ts[i]) kthread_stop(writer_ts[i]);

    /* 印統計 */
    {
        unsigned long total = 0;
        pr_info("racelist: === stats after %ds ===\n", run_secs);
        for (i = 0; i < n_readers; i++) {
            pr_info("racelist:   reader[%ld] %lu lookups (%lu/s)\n",
                    i, reader_counts[i], reader_counts[i] / run_secs);
            total += reader_counts[i];
        }
        pr_info("racelist:   TOTAL %lu lookups (%lu/s)\n",
                total, total / run_secs);
        pr_info("racelist:   writers: add=%ld del=%ld\n",
                atomic_long_read(&total_add), atomic_long_read(&total_del));
    }
    return 0;   /* 回 0 = 載入成功；thread 已停、串列還在，等 rmmod 清 */
}

static void __exit racelist_exit(void)
{
    struct entry *e, *tmp;

    /* thread 在 init 尾已停；這裡只清串列 */
    list_for_each_entry_safe(e, tmp, &the_list, list) {
        list_del(&e->list);
        if (mode == MODE_RCU)
            call_rcu(&e->rcu, free_entry_rcu);
        else
            kfree(e);
    }
    if (mode == MODE_RCU)
        rcu_barrier();   /* 等所有 call_rcu callback 跑完才卸載（見卡關提示 5）*/

    pr_info("racelist: unloaded cleanly\n");
}

module_init(racelist_init);
module_exit(racelist_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Race reproduction + spinlock + RCU fix (kernel_internals practice D)");
```

> **關於 `init` 裡 `ssleep(run_secs)`**：這讓 `insmod` 阻塞 `run_secs` 秒（thread 在背景跑），秒數到就停 thread、印統計。教學上最簡單直觀。生產程式碼不會在 `module_init` 裡長睡（會卡住 modprobe），會改用 delayed workqueue 或讓 thread 自己在 `run_secs` 後停——這點延伸挑戰有提。

### `Makefile`

```makefile
obj-m += racelist.o
KDIR := /path/to/your/linux-6.12      # 指向你 build 的源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

（recipe 行首是 Tab 不是空白，見 Ch 0 踩雷 5。）

### 壓測 / 對照腳本 `bench.sh`（放進 initramfs 跑）

```sh
#!/bin/busybox sh
# 依序跑三個模式，dmesg 收結果。RACY 那次預期會有 KASAN 報告。
echo "===== nproc = $(nproc) (要是 4) ====="

echo "----- MODE 0 RACY (expect KASAN) -----"
insmod /racelist.ko mode=0 run_secs=5
rmmod racelist
dmesg | grep -E "KASAN|use-after-free" | head

echo "----- MODE 1 SPINLOCK -----"
insmod /racelist.ko mode=1 run_secs=5
dmesg | grep "racelist:" | tail -n 8
rmmod racelist

echo "----- MODE 2 RCU -----"
insmod /racelist.ko mode=2 run_secs=5
dmesg | grep "racelist:" | tail -n 8
rmmod racelist

echo "===== 進階 A：AB-BA lockdep ====="
insmod /racelist.ko mode=1 deadlock=1 run_secs=2
dmesg | grep -E "circular locking|DEADLOCK" | head
rmmod racelist

echo "===== 進階 B：sleep in spinlock ====="
insmod /racelist.ko mode=1 sleep_in_lock=1 run_secs=2
dmesg | grep -E "sleeping function|invalid context" | head
rmmod racelist
```

### 進階 A 的修正版 `racelist_path_b`

```c
/* 修正：統一鎖序，永遠先 A 後 B。lockdep splat 消失。 */
static void racelist_path_b(void)
{
    spin_lock(&lock_a);      /* 改成先 A */
    spin_lock(&lock_b);      /* 再 B —— 和 path_a 同序 */
    spin_unlock(&lock_b);
    spin_unlock(&lock_a);
}
```

### 進階 B 的修正版 `racelist_add`

```c
/* 修正：節點配置本來就在鎖外（GFP_KERNEL 可睡）。
 * 進階 B 觸發是因為 sleep_in_lock 在臨界區裡塞了 msleep(1)。
 * 正解就是「臨界區內不做會睡的事」——把 msleep 拿掉即可。
 * 若真有「鎖內必須配記憶體」的需求，改 GFP_ATOMIC 並檢查 NULL： */
static void racelist_add_atomic_variant(int key, int value)
{
    struct entry *e;
    unsigned long flags;

    spin_lock_irqsave(&the_lock, flags);
    e = kmalloc(sizeof(*e), GFP_ATOMIC);   /* 不睡，但可能回 NULL */
    if (!e) {                              /* 一定要處理配失敗 */
        spin_unlock_irqrestore(&the_lock, flags);
        return;
    }
    e->key = key; e->value = value;
    list_add(&e->list, &the_list);
    spin_unlock_irqrestore(&the_lock, flags);
}
```

</details>

## 測試用例

在 QEMU（`-smp 4 -m 1G`，KASAN + PROVE_LOCKING + DEBUG_ATOMIC_SLEEP 都開）裡把 `racelist.ko` 和 `bench.sh` 放進 initramfs，逐項對：

| 測試 | 指令 | 期望 |
|---|---|---|
| 開機環境對 | `nproc`；`dmesg \| grep -i kasan` | 回 4；有 KASAN 初始化訊息 |
| 階段 0 復現 | `insmod racelist.ko mode=0` | `dmesg` 出現 `KASAN: use-after-free in racelist_lookup`（多跑幾次） |
| 階段 1 正確 | `insmod racelist.ko mode=1` | 統計印出、無 KASAN、無 lockdep |
| 階段 2 正確 | `insmod racelist.ko mode=2` | 統計印出、無 KASAN、無 lockdep |
| RCU > spinlock | 對照兩次統計的 `TOTAL .../s` | RCU 的每秒總數明顯高（本範例約 3 倍量級） |
| 進階 A 觸發 | `insmod racelist.ko mode=1 deadlock=1` | `WARNING: possible circular locking dependency` |
| 進階 A 修好 | 統一 `path_b` 鎖序後重編、重跑 | 無 circular locking splat |
| 進階 B 觸發 | `insmod racelist.ko mode=1 sleep_in_lock=1` | `BUG: sleeping function called from invalid context` |
| 進階 B 修好 | 移除臨界區內 `msleep` 後重跑 | 無 sleeping-in-atomic 報告 |
| 收尾乾淨 | 每次 `rmmod racelist` 後 | `racelist: unloaded cleanly`，無 leak / RCU 警告 |

一個容易被忽略的驗證：**用 gdb 看 grace period**。QEMU 加 `-s`，gdb `b synchronize_rcu`，跑 mode=2，會停在寫者 del 之後——你能親眼看到「寫者卡在 `synchronize_rcu` 等所有 CPU 過一次 quiescent state」這件 RCU 的核心動作（Ch 27）。這比讀十遍 RCU 文件都直觀。

## 延伸挑戰

1. **用 seqlock 做第四種修法，對比它和 RCU 的適用邊界（Ch 28）。** seqlock 適合「讀多寫少、且讀者能接受『讀到一半發現寫者插進來、重讀一次』」的資料。把 lookup 改成 `read_seqbegin`/`read_seqretry` 包起來的重試迴圈，寫者 `write_seqlock`。你會發現 seqlock **不適合這個連結串列**——讀者重試期間若寫者已 `kfree` 了節點，重試時可能碰到野指標（seqlock 保護「值的一致性」，不保護「指標指向的物件還活著」）。這個「為什麼 seqlock 能保護 `struct timespec` 這種值、卻不能直接保護會被 free 的節點」的體悟，是 seqlock vs RCU 選型的關鍵。

2. **關掉 KASAN 另 build 一顆，量乾淨的吞吐數字，並用 `perf` 看鎖競爭。** KASAN 下的絕對數字不可信。build 一顆關 KASAN（保留 `PROVE_LOCKING` 也會有 overhead，最乾淨是連 lockdep 也關）的 kernel，重跑 mode=1 vs mode=2，看真實比值。若你的 QEMU 支援，用 `perf record -e lock:contention_begin`（需 kernel 開 `CONFIG_LOCK_STAT`）或 `/proc/lock_stat`（`echo 1 > /proc/sys/kernel/lock_stat` 開）量 spinlock 的 contention 次數與等待時間——你會看到 mode=1 的 `the_lock` 競爭爆表，mode=2 幾乎為零（RCU 讀端根本不碰那把鎖）。

3. **把寫者也做成無鎖對「多寫者」的擴展。** 目前寫者之間還是靠 `the_lock` 互斥（RCU 只解決了讀者）。若寫者也很多，這把鎖又成瓶頸。研究 kernel 怎麼處理「多寫者 + 多讀者」——例如把串列换成 per-CPU 的子串列（Ch 7）、或用 hashtable + per-bucket 鎖降低競爭粒度。想想為什麼「全域一把寫鎖」在寫者多時會回到 spinlock 的老問題，而 RCU 本身**不解決寫者間的競爭**。

4. **用 `list_for_each_entry_rcu` 之外，試 `hlist` + RCU 做一個真正的 hash 表。** 真實 kernel 的路由表、conntrack 表都是 RCU hash（`hlist_for_each_entry_rcu` + `hlist_add_head_rcu`）。把這個練習從單串列擴成 `DEFINE_HASHTABLE` + RCU，讀者按 key hash 到 bucket 再走該 bucket 的 RCU 串列——這就非常接近 `net/` 目錄裡真實的用法了。

## 本練習重點整理

- **race 要主動逼、用工具釘死。** `-smp 4` + 放大時窗 + KASAN，把「一邊走串列一邊被別的 CPU free」的 use-after-free 變成一份能逐行讀的報告，而不是靠僥倖。
- **spinlock 整表鎖正確但沒有讀並行度。** 所有讀者搶同一把鎖被序列化；「讀多寫少」的結構這樣做是拿並行度換正確性，要量得出代價。
- **RCU 讀端趨近零成本，代價是延後釋放。** 三件事缺一不可：讀端 `rcu_read_lock`、遍歷用 `_rcu` 變體、釋放等 grace period（`synchronize_rcu`/`call_rcu`）。漏任何一個都是「壓力上來才 UAF」的炸彈。
- **lockdep 和 DEBUG_ATOMIC_SLEEP 不用等 bug 真的發生。** lockdep 在你第一次反序拿鎖時就報 AB-BA；DEBUG_ATOMIC_SLEEP 在你一在原子上下文睡就報。學會讀這兩份報告，勝過背鎖規則。

## 自我檢核

- [ ] 不看筆記，能說出 KASAN 那份 use-after-free 報告的三行各代表什麼（誰讀、在哪讀、誰 free）
- [ ] 能解釋為什麼「整表一把 spinlock」在讀多寫少時是次佳解，代價具體是什麼
- [ ] 能列出 RCU 正確性的三個必要條件，並說出漏掉每一個各會怎麼壞
- [ ] 能解釋 `synchronize_rcu` 為什麼會睡、為什麼不能放在 spinlock 臨界區
- [ ] 面試被問「這段程式碼有 race，你會怎麼修、怎麼證明修好了」，你能給出「先 KASAN 復現 → spinlock 修並量競爭 → 評估是否值得換 RCU → lockdep 驗無死鎖」這條完整路徑
- [ ] 能逐行讀懂 lockdep 的 circular locking splat，指出反序的兩把鎖並修正鎖序
- [ ] 能解釋 seqlock 為什麼保護得了 `timespec` 卻保護不了會被 free 的串列節點

## 延伸閱讀

### 官方文件

- **[Documentation/RCU/whatisRCU.rst](https://www.kernel.org/doc/html/latest/RCU/whatisRCU.html)** — Paul McKenney 等
  - **讀哪裡**：「What is RCU's Core API?」和「RCU list operations」兩節，配「What are some example uses of core RCU API?」的串列範例
  - **和本練習的關聯**：你 MODE_RCU 用的 `list_add_rcu`/`list_del_rcu`/`list_for_each_entry_rcu`/`synchronize_rcu`/`call_rcu` 全出自這裡，選 `synchronize_rcu` 還是 `call_rcu` 的權衡也在此

- **[Documentation/dev-tools/kasan.rst](https://www.kernel.org/doc/html/latest/dev-tools/kasan.html)** — kernel 官方
  - **讀哪裡**：「Generic KASAN」一節，理解 shadow memory 怎麼標記 freed 物件、報告格式怎麼讀
  - **能學到什麼**：你階段 0 那份報告每一欄（Freed by task、buggy address belongs to cache）的來源，Ch 53 會深入

- **[Documentation/locking/lockdep-design.rst](https://www.kernel.org/doc/html/latest/locking/lockdep-design.html)** — Ingo Molnar 等
  - **讀哪裡**：lock class、lock chain 的概念，理解 lockdep 為什麼「不用真死鎖就能抓 AB-BA」
  - **和本練習的關聯**：進階 A 那份 circular locking splat 的每個欄位（`-> #1`、`-> #0`、possible unsafe scenario）在這裡有解釋

### 文章 / 論文

- **[LWN: "What is RCU, Fundamentally?"](https://lwn.net/Articles/262464/)** — Paul McKenney & Jonathan Walpole
  - **為什麼值得讀**：RCU 三系列文的第一篇，用「publish-subscribe」和「wait for pre-existing readers」兩個概念把 RCU 講清楚。你把 MODE_RCU 寫對之後回來讀，會對「為什麼延後釋放是安全的」有更深理解
  - **前提**：做完本練習的 MODE_RCU，帶著「我剛親手寫過」的問題來讀最有效

- **[LWN: "The kernel lock validator"](https://lwn.net/Articles/185666/)** — Jonathan Corbet
  - **讀哪裡**：整篇，講 lockdep 上游時的設計動機，補充官方 lockdep-design 文件的「為什麼」

### 書籍

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul McKenney（線上免費）
  - **這本書的定位**：RCU 作者親筆，並行程式設計的百科；「Deferred Processing」整章講 RCU 的來龍去脈、和 hazard pointer/reference counting 的對比
  - **注意**：很厚，當工具書查 RCU 章節即可，不必通讀

做完這個練習，你把 Part 4 的鎖工具箱從「認得」變成「會選、會量、會 debug」。並行的世界還有一大塊沒碰：**中斷**——它會在任何時刻打斷你正在跑的程式碼，帶來一種和「另一個 CPU」不同的並行來源（同一顆 CPU 上被中斷打斷），這正是為什麼上面到處用 `spin_lock_irqsave` 而非 `spin_lock`。下一章我們進中斷子系統，看硬體中斷怎麼進來、top half / bottom half 為什麼要拆、以及它和我們剛練的鎖怎麼互動。

→ [Ch 29 中斷處理：IDT/GIC、top/bottom half](./29-interrupt-handling.md)
