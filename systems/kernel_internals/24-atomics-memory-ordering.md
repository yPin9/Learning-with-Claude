# Ch 24 — atomic 操作與 memory ordering

> **目標**：理解為什麼 `counter++` 在多核上會丟資料、atomic 操作怎麼把它變成硬體層級不可分割的一步（x86 的 `lock` prefix vs ARM64 的 LL/SC）、`cmpxchg` 為什麼是所有無鎖演算法的基石，以及 kernel 的 atomic 為什麼要分 `_acquire`/`_release`/`_relaxed` 三種 ordering 變體。學完你能寫一個 atomic 計數器把 Ch 7 的裸計數器 race 修掉、從反組譯確認 `lock` prefix、並用 `cmpxchg` 寫一個無鎖 stack push。

## 為什麼需要這個？

Ch 7 給了你一個痛：一段人畜無害的 `packet_count++` 在多核 kernel 裡是等著出事的 bug。我們把那段 code 再抬出來看一次：

```c
static unsigned long packet_count;

void on_packet_received(void)
{
    packet_count++;             // 看起來一步，其實三步
}
```

`packet_count++` 不是一個動作。編譯器把它拆成三條指令（在 x86 上大致是）：

```asm
    mov  packet_count, %rax     ; ① load：把記憶體值讀進暫存器
    add  $1, %rax               ; ② modify：暫存器 +1
    mov  %rax, packet_count     ; ③ store：寫回記憶體
```

這三步之間，任何事都可能發生：另一顆 CPU 插進來、中斷打斷你、排程器把你換下 CPU。Ch 7 的 per-CPU 是一種解法——**根本不共享**，每個 CPU 各改各的。但 per-CPU 不是萬能：有些計數就是必須是「全域唯一的一個值」（一個 kref 引用計數、一個 semaphore 的名額、一個無鎖佇列的 head 指標），這時你避不掉共享，必須讓「讀-改-寫」這三步變成一個**不可分割**（atomic，源自希臘文「不可切分」）的動作。

這就是 atomic 操作要解決的問題。它不是鎖——沒有臨界區、沒有等待、沒有睡眠——而是直接請硬體保證「這一整個 read-modify-write 過程中，沒有別的 CPU 能插進來碰同一個位址」。這是 kernel 同步的最底層原語（primitive），spinlock（Ch 25）、mutex（Ch 26）、RCU 的引用計數（Ch 27）全部建在它之上。

## 先建立直覺

先把「為什麼會丟」畫清楚。兩顆 CPU 各跑一次 `packet_count++`，初始值 5，正確結果應該是 7。但指令交錯可能長這樣：

```
  時間軸       CPU 0                        CPU 1              packet_count
   │                                                              5
   │      ① load  → rax=5                                        5
   │                                  ① load  → rax=5            5   ← 兩邊都讀到 5
   │      ② add   → rax=6                                        5
   │                                  ② add   → rax=6            5
   │      ③ store → 6                                            6
   │                                  ③ store → 6                6   ← 應該是 7！
   ▼
                              一次 ++ 被吃掉了 = lost update
```

兩顆 CPU 都讀到 5、都算成 6、都寫回 6。做了兩次 `++`，值卻只加了一次。這就是 **lost update**——Ch 7 讓你親手復現過的痛。

atomic 的作法是把整條 read-modify-write **焊成一顆不可切開的指令**，硬體保證中間沒有縫：

```
  時間軸       CPU 0                        CPU 1              packet_count
   │                                                              5
   │   ┌─ lock: load  → 5                                        5
   │   │  add   → 6              (CPU 1 想碰同一位址，          5
   │   └─ store → 6               被硬體擋在門外，乾等)          6
   │                                  ┌─ lock: load  → 6         6
   │                                  │  add   → 7               6
   │                                  └─ store → 7               7   ← 正確
   ▼
              CPU 0 的整個 RMW 做完，CPU 1 才拿得到那條 cache line
```

關鍵在那個 `lock`：CPU 0 執行 atomic 加法時，硬體確保它「獨佔」那條 cache line 直到寫回完成，CPU 1 對同一位址的 atomic 操作只能排隊等。做兩次 `++`，值就真的加了 2。

## atomic_t 與它的 API

kernel 不讓你直接對 `int` 做 atomic 操作，而是包一層型別 `atomic_t`（32 位）、`atomic64_t`（64 位），定義在 `include/linux/types.h`，API 在 `include/linux/atomic.h`。用型別包起來的理由是**強迫你透過 atomic API 存取**——你沒辦法對 `atomic_t` 直接寫 `a++`（型別不對，編不過），只能呼叫 `atomic_inc(&a)`。這是刻意的防呆。

```c
#include <linux/atomic.h>

atomic_t count = ATOMIC_INIT(0);        // 靜態初始化

atomic_set(&count, 5);                   // 寫：等同 WRITE_ONCE
int v = atomic_read(&count);             // 讀：等同 READ_ONCE
atomic_inc(&count);                      // count += 1，不回傳
atomic_dec(&count);                      // count -= 1
atomic_add(3, &count);                   // count += 3
atomic_sub(2, &count);                   // count -= 2

int now = atomic_add_return(3, &count);  // count += 3，回傳新值（有 full barrier）
int old = atomic_fetch_add(3, &count);   // count += 3，回傳舊值
bool z = atomic_dec_and_test(&count);    // count -= 1；減到 0 回傳 true（refcount 的核心）
```

`atomic_read`/`atomic_set` 只是包了 `READ_ONCE`/`WRITE_ONCE`（Ch 23 講過，防編譯器把存取優化掉或撕裂），它們本身**不含 RMW，也不排它**——因為讀單一個對齊的 word、寫單一個對齊的 word，在 x86 和 ARM64 上硬體本來就保證是不可撕裂的（見踩雷第 2 條）。真正需要硬體鎖的是 inc/dec/add 這些 read-modify-write。

那個 `atomic_dec_and_test`（減 1，減到 0 回 true）是引用計數的心臟：「我放掉最後一個引用了嗎？是的話該我來釋放」。後面 refcount 一節會回到它。

## 底層機制：它怎麼運作

atomic API 是統一介面，但底下靠什麼指令，是**架構相關**的，而且 x86 和 ARM64 是兩種完全不同的哲學。這是本章的靈魂。

### x86：`lock` prefix 鎖 cache line

x86 的 `atomic_inc(&count)` 編出來是一條指令：

```asm
    lock incl (%rdi)        ; lock prefix + incl（increment long）
```

`lock` 是一個**指令前綴**。它不是額外一條指令，是貼在 `incl` 前面、告訴 CPU「這條 RMW 要獨佔執行」的旗標。現代 x86（P6 之後）實作 `lock` 的方式不是鎖住整條記憶體匯流排（那太慘，會卡死所有核），而是靠 **cache coherence 協議（MESI，Ch 23）鎖住那一條 cache line**：

```
   CPU 0 執行 lock incl (%rdi)
       │
       ├─ ① 把 &count 所在的 cache line 抓成 Exclusive/Modified 狀態
       │    （透過 MESI，讓其他核的這條 line 失效 Invalid）
       │
       ├─ ② 在「握著這條 line 獨佔權」的期間完成 load→+1→store
       │    這段期間任何其他核想讀/寫同一條 line 都會被 MESI 擋住、等待
       │
       └─ ③ 放開，其他核的 atomic 操作才拿得到這條 line

   結果：從別的核看，這條 incl 要嘛完全沒發生、要嘛完全發生，沒有中間態
```

代價：`lock` 指令比普通 `incl` 貴很多（要跨核協調 cache line 所有權，通常幾十個 cycle 起跳，競爭激烈時更貴），而且它在 x86 上**順帶是一個 full memory barrier**——會刷 store buffer、擋住前後記憶體存取的重排（Ch 23 的 `smp_mb` 語意）。這個「順帶的 barrier」等一下講 ordering 時很重要。

### ARM64：LL/SC，樂觀重試

ARM64 沒有 `lock` prefix 這種東西。它的哲學相反：不預先鎖，而是**樂觀地試，被別人插隊就重來**。原始做法是一對指令 **LL/SC（load-linked / store-conditional）**：

```asm
retry:
    ldxr   w1, [x0]         ; load-exclusive：讀值，並在硬體標記「我盯上這個位址了」
    add    w1, w1, #1       ; +1
    stxr   w2, w1, [x0]     ; store-exclusive：試著寫回
                            ;   若這期間沒人碰過該位址 → 寫成功，w2=0
                            ;   若有人碰過（exclusive monitor 被清）→ 寫失敗，w2=1
    cbnz   w2, retry        ; 失敗就跳回 retry 整個重來
```

`ldxr` 不鎖任何東西，只是請硬體「盯著」這個位址（設一個 exclusive monitor）。`stxr` 寫回前檢查：從 `ldxr` 到現在，有沒有別的核動過這個位址？沒有就寫成功；有就**寫失敗、回傳非 0**，程式跳回 `retry` 重做一遍。沒有人被鎖住空等，代價是「有人插隊時要重試」。

ARMv8.1 之後加了 **LSE（Large System Extensions）atomic 指令**，把常見 atomic 直接做成單條硬體指令，不用 LL/SC loop：

```asm
    stadd  w1, [x0]         ; atomic add，一條搞定（LSE）
    ldaddal w1, w2, [x0]    ; atomic add 並回舊值，且帶 acquire+release ordering
```

kernel 6.12 在 ARM64 上會依 CPU 能力，用 `alternatives` 機制在開機時把 LL/SC 版本 patch 成 LSE 版本（如果 CPU 支援）。你讀源碼看到的是 LL/SC 骨架，實際跑的可能是 LSE 單指令。

一句話對比：**x86 悲觀（先鎖 cache line 再做），ARM64 樂觀（先做，被插隊再重試 / 或用 LSE 單指令）**。這個差異解釋了為什麼 ARM64 對「不必要的 barrier」特別敏感——它的 memory model 比 x86 弱得多，barrier 是真指令、真成本，而 x86 很多 barrier 是 no-op（Ch 23）。

## RMW 與 compare-and-swap：無鎖演算法的基石

前面的 inc/add 都是「無條件」RMW。真正撐起所有無鎖（lock-free）資料結構的，是**有條件**的 RMW：**compare-and-swap（CAS）**，kernel 叫 `cmpxchg`。

```c
// 語意（原子地一次做完）：
//   若 *ptr == old，就把 *ptr 設成 new，回傳原本的 *ptr（此時 == old，代表成功）
//   若 *ptr != old，什麼都不改，回傳原本的 *ptr（!= old，代表失敗，被人插隊了）
int cmpxchg(int *ptr, int old, int new);
int atomic_cmpxchg(atomic_t *v, int old, int new);
```

CAS 的威力在於：它讓你可以「賭一把」。你先讀一個舊值、慢慢算出新值，最後用 CAS 一口氣提交——CAS 會檢查「我讀到現在，這個值有沒有被別人改過？」沒被改就提交成功，被改了就提交失敗、你重來。這就是 **CAS loop** pattern，無鎖程式的標準骨架：

```
   CAS loop（例：無鎖地把 max 更新成更大的值）
   ┌──────────────────────────────────────────────┐
   │  do {                                          │
   │      old = atomic_read(&v);      ← ① 讀舊值    │
   │      if (new_val <= old)                       │
   │          break;                                │
   │      /* ← ② 用 old 算出想寫的 new_val */       │
   │  } while (atomic_cmpxchg(&v, old, new_val)     │
   │           != old);               ← ③ 賭提交    │
   │                                                │
   │  ③ 成功(回傳==old) → 離開                       │
   │  ③ 失敗(回傳!=old) → 有人插隊，回 ① 重讀重算    │
   └──────────────────────────────────────────────┘
```

沒有鎖、沒有睡眠。競爭不激烈時幾乎零成本（一次就成功），競爭激烈時退化成忙碌重試。kernel 裡幾乎每個無鎖結構（`llist`、qspinlock 的排隊、RCU 的指標更新）底層都是這個 loop。

### ABA 問題（點一下）

CAS 只比對「值等不等於 old」，不管「這期間值有沒有變過又變回來」。指標場景特別危險：

```
   CPU 0 讀到 head == A（打算 CAS 把 head 換掉）
   ── 這期間 CPU 1：pop 掉 A、pop 掉 B、又 push 回一個「剛好也在 A 位址」的節點 ──
   CPU 0 的 cmpxchg(&head, A, ...) 看到 head 還是 A → 以為沒人動過 → 成功
   但其實整個鏈已經被換過，A 的 next 早就不是當初那個了 → 資料結構壞掉
```

這是 **ABA 問題**：值從 A→B→A，CAS 看不出中間發生過事。kernel 對付 ABA 主要不是靠加版本號 tag，而是靠 **RCU + 延後釋放**（Ch 27）——保證你手上的指標指的節點在你用完前不會被回收再利用，A 就不會「變回別的 A」。這裡先埋個伏筆，Ch 27 展開。

## atomic 操作的 memory ordering 變體

這一節接 Ch 23，是本章最容易被忽略、面試最愛問的地方。問題：**kernel 的 atomic 操作，預設帶不帶 memory ordering？**

答案分兩類（LKMM 的規定）：

| 類別 | 例子 | 預設 ordering |
|---|---|---|
| 純讀 / 純寫 | `atomic_read`、`atomic_set` | **完全無 ordering**（只保證不撕裂，等同 READ_ONCE/WRITE_ONCE） |
| 無回傳值的 RMW | `atomic_inc`、`atomic_add`、`atomic_dec` | **無 ordering**（只保證原子性，不保證前後不重排） |
| 有回傳值 / 條件的 RMW | `atomic_add_return`、`atomic_fetch_add`、`atomic_cmpxchg`、`atomic_dec_and_test` | **full barrier**（前後都不許重排，等同 `smp_mb`） |

為什麼要這樣分？因為 barrier 很貴，尤其在 ARM64（Ch 23：x86 的 acquire/release 常是 no-op，ARM64 是真指令）。如果每個 atomic 都硬塞 full barrier，只想單純加個統計計數的人就白白付了跨核同步的稅。所以 kernel 給出**帶後綴的細粒度變體**，讓你精準表達你要的 ordering：

```c
atomic_add_return_relaxed(1, &v);   // _relaxed：只要原子性，不要任何 barrier（最便宜）
atomic_fetch_add_acquire(1, &v);    // _acquire：這個 RMW 之後的存取不許被排到它前面
atomic_fetch_add_release(1, &v);    // _release：這個 RMW 之前的存取不許被排到它後面
atomic_add_return(1, &v);           // 無後綴：full barrier（前後都擋，最保險也最貴）
```

acquire/release 的語意 Ch 23 建立過：**acquire 像上鎖（後面的東西別跑到我前面）、release 像解鎖（前面的東西別溜到我後面）**。一個典型用法是無鎖地「發布」資料：

```c
// 生產者：先填好 data，再用 release 把 flag 設起來
data = 42;
atomic_set_release(&flag, 1);       // release：保證 data=42 在 flag=1 之前對其他核可見

// 消費者：用 acquire 讀 flag，讀到 1 就保證看得到 data=42
if (atomic_read_acquire(&flag) == 1)
    use(data);                      // acquire 配 release，data 一定已就緒
```

在 x86 上這對 acquire/release 幾乎是 no-op（x86 記憶體模型本來就強）；在 ARM64 上它們會編成 `ldar`/`stlr`（load-acquire / store-release，Ch 23 見過）。用 `_relaxed` 而不是無後綴版，在 ARM64 上省掉的就是那條真 barrier 指令的成本——這是為什麼熱路徑上的統計計數常用 `_relaxed`。

> 認識論誠實：這些 ordering 的**精確語意**不是我三言兩語能定死的，它由 **LKMM（Linux Kernel Memory Model）** 形式化定義，源碼在 `tools/memory-model/`（有 `.cat` 模型、`litmus-tests/`、和 `herd7` 驗證工具）。文件見 `Documentation/atomic_t.txt`（列出每個 atomic 的 ordering 保證）和 `Documentation/memory-barriers.txt`。當你不確定某個 atomic 到底有沒有 barrier，**查 `atomic_t.txt` 那張表，不要靠直覺**。

## atomic bitops：對 flag 位元做原子操作

除了整數，kernel 大量用「一個 word 當一堆 flag」，需要原子地設/清/測單一個 bit。這組 API 在 `include/linux/bitops.h` 和 `include/asm-generic/bitops/`：

```c
set_bit(nr, addr);              // 原子地把第 nr 個 bit 設 1
clear_bit(nr, addr);            // 原子地清 0
change_bit(nr, addr);           // 原子地翻轉
test_and_set_bit(nr, addr);     // 原子地設 1，並回傳「設之前」的舊值（RMW，有 barrier）
test_and_clear_bit(nr, addr);   // 原子地清 0，並回傳舊值
```

`test_and_set_bit` 回傳舊值這點是關鍵：它讓你能實作「搶佔一個 flag」——「如果這個 bit 原本是 0（沒人佔），我把它設成 1 並回傳 0（代表我搶到了）；如果原本是 1（有人佔了），回傳 1（我沒搶到）」。這正是很多「只做一次」邏輯的底層。

你在前面章節已經碰過這組的服務對象：

- **Ch 9 的 thread flags**：`task_struct` 的 `thread_info.flags`（`TIF_NEED_RESCHED`、`TIF_SIGPENDING`…）就是用 `set_tsk_thread_flag`/`test_thread_flag` 這類 bitop 原子地操作——因為一顆 CPU 可能在中斷裡設某個 task 的 `TIF_NEED_RESCHED`，另一顆 CPU 正在讀它，必須原子。
- **Ch 22 的 page flags**：`struct page` 的 `flags`（`PG_locked`、`PG_dirty`、`PG_writeback`…）也是這組 bitop 操作，`SetPageDirty`/`TestClearPageDirty` 之類的 macro 底層就是 `set_bit`/`test_and_clear_bit`。

有非原子版 `__set_bit`（兩個底線），在「你已經拿著鎖、確定沒人競爭」時用，省掉 atomic 成本——但用錯就是 race，所以預設用原子版。

## refcount_t：為什麼不用 atomic_t 做引用計數

引用計數（reference counting）是 kernel 管物件生命週期的核心手法：「有幾個人還在用我？降到 0 就釋放」。歷史上大家用 `atomic_t` 做：

```c
atomic_inc(&obj->refcnt);                // 拿一個引用
if (atomic_dec_and_test(&obj->refcnt))   // 放引用；減到 0 → 該我釋放
    free_obj(obj);
```

這能動，但有兩個致命弱點，而且都被 `kernel_pwn` 課當攻擊面利用過：

1. **整數溢位（overflow）**：`atomic_t` 是 32 位有號整數，一直 `inc` 到 `INT_MAX` 會回繞成負數。攻擊者若能觸發大量 `inc`（例如某個 syscall 每次呼叫 refcount +1 卻沒對應 dec），把計數溢位回繞，就能製造「計數還沒到 0，物件卻被提前釋放」或反之——經典的 refcount overflow → **use-after-free（UAF）**漏洞（CVE 一堆）。
2. **沒有防呆**：`atomic_t` 允許「已經是 0 了還去 inc」（復活一個正在釋放的物件）這種本不該發生的操作，`atomic_t` 完全不擋。

`refcount_t`（`include/linux/refcount.h`，`refcount_inc`/`refcount_dec_and_test`）就是為了修這兩點而生：

```c
refcount_t ref = REFCOUNT_INIT(1);
refcount_inc(&ref);                      // 若已是 0 → 拒絕 + WARN（0 不能復活）
if (refcount_dec_and_test(&ref))         // 溢位/underflow → 飽和到特殊值 + WARN
    free_obj(obj);
```

它的作法是**飽和（saturation）而非回繞**：接近溢位時就卡在一個「已飽和」的特殊值不再變動並印 WARN，把「溢位 → UAF」這條攻擊鏈從根斷掉。代價是 refcount 的操作比裸 atomic 稍貴（多了範圍檢查），但引用計數的正確性遠比那點成本重要。**6.x 起，新 code 做引用計數一律用 `refcount_t`，不要再用 `atomic_t`**。

在此之上還有 **`kref`**（`include/linux/kref.h`），把「refcount + 釋放函式」包成一個小物件：

```c
struct my_obj { struct kref refcount; /* ... */ };

kref_get(&obj->refcount);                          // +1
kref_put(&obj->refcount, my_obj_release);          // -1；到 0 就自動呼叫 my_obj_release
```

`kref_put` 幫你把「dec_and_test 成功就呼叫釋放函式」這個 pattern 封裝好，是 kernel 物件（尤其 Ch 37 device model 的 kobject）管生命週期的標準零件。

## 什麼時候 atomic 不夠，得用鎖

atomic 很誘人（沒鎖、不睡、快），但它有天生的天花板。atomic 只能保證**單一個變數的單一次 RMW** 不可分割。碰到下面任一種情況，你就需要 Ch 25-26 的鎖：

- **要一致地改多個變數**：「把 A 從 list 移到 list B」牽涉多個指標，atomic 沒辦法讓「從 A 拔掉」和「插進 B」一起原子完成。
- **臨界區有多步、有中間態**：一段程式碼從頭到尾別人都不能插進來看到半成品狀態——這是鎖的定義，atomic 給不了。
- **要睡眠 / 要等條件**：atomic 是忙碌操作，不能睡。要「等某個條件成立才繼續」得用會睡的 mutex/semaphore/completion（Ch 26）。
- **CAS loop 在高競爭下爆炸**：無鎖看似美好，但競爭極激烈時 CAS 一直失敗重試，可能比直接上鎖還慢、還耗電。這時 qspinlock（Ch 25）反而更好。

一句判準：**atomic 適合「一個計數 / 一個 flag / 一個指標」的原子更新；一旦是「一段邏輯」或「多個變數的一致性」，就上鎖。**

## 動手：atomic 計數器修掉 Ch 7 的 race

我們寫一個模組，開多個 kthread 同時對計數器猛加，對比「裸全域計數器」和「atomic 計數器」的結果。裸的在多核下會丟計數，atomic 的不會。

```c
// atomic_demo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kthread.h>
#include <linux/atomic.h>
#include <linux/delay.h>

#define NR_THREADS  8
#define NR_ITERS    1000000

static unsigned long racy_counter;          // 裸的：會丟計數
static atomic_t     atomic_counter = ATOMIC_INIT(0);   // atomic：正確
static struct task_struct *threads[NR_THREADS];
static atomic_t done = ATOMIC_INIT(0);

static int worker(void *arg)
{
    int i;
    for (i = 0; i < NR_ITERS; i++) {
        racy_counter++;                     // ← load-add-store，多核交錯就丟
        atomic_inc(&atomic_counter);        // ← 硬體原子，不會丟
    }
    atomic_inc(&done);                      // 記一個「我做完了」
    return 0;
}

static int __init demo_init(void)
{
    int i;
    unsigned long expected = (unsigned long)NR_THREADS * NR_ITERS;

    racy_counter = 0;
    atomic_set(&atomic_counter, 0);
    atomic_set(&done, 0);

    for (i = 0; i < NR_THREADS; i++)
        threads[i] = kthread_run(worker, NULL, "atomic_demo/%d", i);

    while (atomic_read(&done) < NR_THREADS)  // 等所有 worker 做完
        msleep(10);

    pr_info("expected     = %lu\n", expected);
    pr_info("racy_counter = %lu  (差 %ld，這就是 lost update)\n",
            racy_counter, (long)expected - (long)racy_counter);
    pr_info("atomic_counter = %d  (%s)\n",
            atomic_read(&atomic_counter),
            atomic_read(&atomic_counter) == (int)expected ? "正確" : "錯了");
    return 0;
}

static void __exit demo_exit(void) { pr_info("atomic_demo: bye\n"); }

module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("atomic vs racy counter (kernel_internals Ch 24)");
```

在你 Ch 0 建的 QEMU 裡跑（記得給多核：`qemu-system-x86_64 ... -smp 4`，單核看不出 race）：

```
/ # insmod /atomic_demo.ko
[  ...] expected       = 8000000
[  ...] racy_counter   = 5837201  (差 2162799，這就是 lost update)
[  ...] atomic_counter = 8000000  (正確)
```

`racy_counter` 少掉的那兩百多萬次，就是被 lost update 吃掉的。`atomic_counter` 一次不差。這是 Ch 7 的痛第一次被真正修好。

> 若在單核（`-smp 1`）跑，`racy_counter` 可能也剛好正確——因為 `++` 沒被搶佔打斷。這正說明 race 是**時序相關**的、間歇性的：能在你機器上跑對一萬次，第一萬零一次在客戶多核機器上炸掉。這是並行 bug 最惡毒的地方。

### 從反組譯確認 `lock` prefix

編完模組，反組譯看 `atomic_inc` 到底變成什麼：

```bash
objdump -d atomic_demo.ko | grep -A2 -B2 'lock'
```

你會在 `worker` 裡看到類似：

```asm
    lock incl 0x0(%rip)        # atomic_inc(&atomic_counter)
    ...
    addq  $0x1, 0x0(%rip)      # racy_counter++ ← 注意：沒有 lock！
```

一行有 `lock`、一行沒有——這一個前綴，就是「多核正確」和「多核丟資料」的全部差別。在 ARM64 上編出來（用 `aarch64-linux-gnu-` toolchain）則會看到 `ldxr`/`stxr`（LL/SC）或 `stadd`（LSE），視 config 而定。

## 動手：cmpxchg 寫一個無鎖 stack push

用 CAS loop 實作一個 Treiber stack 的 push（最經典的無鎖結構）。push 不用鎖也不會壞：

```c
struct node {
    struct node *next;
    int          val;
};

static struct node *stack_top;      // 就是一個裸指標當 head

static void lockfree_push(struct node *n)
{
    struct node *old_top;
    do {
        old_top = READ_ONCE(stack_top);   // ① 讀當前 top
        n->next = old_top;                // ② 把新節點接到目前的 top 前面
    } while (cmpxchg(&stack_top, old_top, n) != old_top);
        // ③ 賭：如果 stack_top 還是我剛讀的 old_top（沒人插隊），就換成 n、成功
        //    如果別人搶先 push 過（stack_top 變了），cmpxchg 失敗、回 ① 重讀重接
}
```

畫出兩核競爭時 CAS loop 怎麼自我修正：

```
   CPU 0 push X                     CPU 1 push Y            stack_top
     old=read → T                                              T
     X.next = T                                                T
                                     old=read → T              T
                                     Y.next = T                T
     cmpxchg(&top,T,X) == T ✓ → top=X                          X   ← CPU 0 贏
                                     cmpxchg(&top,T,Y) != X ✗       X
                                     ── 失敗，回頭重來 ──
                                     old=read → X   ← 讀到新 top    X
                                     Y.next = X                     X
                                     cmpxchg(&top,X,Y) == X ✓       Y   ← CPU 1 補上
```

CPU 1 第一次 CAS 失敗（因為 CPU 0 已經把 top 從 T 改成 X），它不會壞掉——只是回頭重讀到 X、重接、再 CAS，這次成功。兩個節點都正確入棧，全程沒有一把鎖。

> 注意：**pop** 比 push 難得多，正是因為 ABA 問題（pop 要讀 `top->next`，那個節點可能在你讀到後被別人 pop 走並釋放）。生產級的無鎖 stack pop 要靠 RCU 或 hazard pointer 保護（Ch 27）。這也是 kernel 現成的 `llist`（`include/linux/llist.h`）只提供無鎖 push（`llist_add`）和「整串一次搬走」（`llist_del_all`），而**不提供逐個無鎖 pop** 的原因——它繞開了 ABA。

## 對比與取捨

| 手段 | 保護範圍 | 會睡嗎 | 成本 | 適用 |
|---|---|---|---|---|
| per-CPU（Ch 7） | 不共享，各改各的 | 否 | 最低 | 純本地統計、不需全域一致值 |
| `atomic_t` inc/add | 單變數單次 RMW | 否 | 低（x86 一條 `lock` 指令） | 全域計數、簡單 flag |
| `atomic_*_relaxed` | 同上但無 barrier | 否 | 最低（ARM64 省真 barrier） | 熱路徑統計、不需 ordering |
| `cmpxchg` + CAS loop | 單變數條件更新 | 否 | 低競爭幾乎零；高競爭退化重試 | 無鎖結構、樂觀更新 |
| `refcount_t` | 引用計數 | 否 | 比 atomic 略高（範圍檢查） | 物件生命週期（取代 atomic_t） |
| spinlock（Ch 25） | 一段臨界區 | 否（忙等） | 中；持有時不能睡 | 短臨界區、中斷上下文 |
| mutex（Ch 26） | 一段臨界區 | 是 | 高（可能觸發排程） | 長臨界區、可睡的上下文 |

## 踩雷集錦

1. **以為 `atomic_read` + `atomic_inc` 兩步還是原子的**：`if (atomic_read(&v) == 0) atomic_inc(&v);` 這種「先讀再改」在兩步之間會被插隊，不是原子的。要原子地「條件式更新」必須用單一個 `atomic_cmpxchg` 或 `atomic_add_unless`，別自己拼。

2. **對非對齊或跨型別的變數期待原子性**：硬體只保證「對齊的、單一 word（≤ 機器字長）」的讀寫不撕裂。一個跨 cache line 的變數、或 32 位機器上的 64 位值，讀寫可能被撕成兩半（word tearing）。`atomic_t`/`atomic64_t` 幫你保證對齊，別自己拿裸 `long` 賭。

3. **以為 kernel 的 atomic 都自帶 barrier**：**不是**。`atomic_inc`（無回傳）在多數架構上**沒有** ordering 保證，只保證原子性。你若靠 `atomic_inc` 順帶排序別的記憶體存取（「加完計數，前面寫的 data 就一定可見了」），在 ARM64 上會出事。要 ordering 就明確用有回傳值的版本或 `_acquire`/`_release`，並查 `Documentation/atomic_t.txt`。

4. **新 code 還用 `atomic_t` 做引用計數**：溢位回繞 → UAF 的攻擊面。用 `refcount_t`/`kref`，讓飽和機制幫你擋。code review 看到 `atomic_dec_and_test` 在做生命週期管理，就該問「為什麼不用 refcount」。

5. **無鎖 = 免費 = 一定比鎖快，是幻覺**：CAS loop 在高競爭下不斷失敗重試，浪費 CPU 又耗電，可能比一把 qspinlock 還慢。無鎖真正的價值常是「不會 deadlock、中斷上下文安全、可預測延遲」，而非單純吞吐。先量測再決定，別為無鎖而無鎖。

## 進階：再往深一層

- **`atomic_add_unless` / `atomic_try_cmpxchg`**：`atomic_add_unless(&v, a, u)` 是「若 v != u 就 v += a」的原子版，常用來實作「非 0 才加引用」。`atomic_try_cmpxchg(&v, &old, new)`（注意 old 傳指標）是新版 CAS 介面，失敗時自動把最新值寫回 `old`，省掉手動重讀，寫 CAS loop 更順手。
- **`smp_mb__before_atomic` / `smp_mb__after_atomic`**：當你用的是「無 barrier 的 atomic」但又需要在它周圍加 ordering，用這對輕量 barrier，比硬換成 full-barrier 版本更精準（見 `Documentation/atomic_t.txt` 的「barrier」段）。
- **LKMM 與 litmus test**：`tools/memory-model/` 裡可以用 `herd7` 跑 litmus test，形式化驗證「這段 atomic + barrier 的組合在弱記憶體模型下會不會出現非法結果」。這是 kernel 開發者證明並行程式正確性的工具，不是靠腦補。面試被問「你怎麼確定這段無鎖 code 對」，答得出 litmus test 是加分。
- **面試常問**：「x86 上 `atomic_inc` 是什麼指令？」（`lock incl`）「它順帶是不是 barrier？」（是 full barrier）「ARM64 呢？」（LL/SC 或 LSE，且 acquire/release 是真指令 `ldar`/`stlr`）「`atomic_read` 需要 barrier 嗎？」（不需要，它只是 READ_ONCE）「為什麼 refcount_t 取代 atomic_t？」（飽和防溢位 UAF）。這五題答得清楚，這章就通了。

## 動手練習

1. **調 `-smp` 看 race 出現/消失**：把上面的 demo 分別用 `-smp 1`、`-smp 2`、`-smp 8` 跑，記錄 `racy_counter` 差多少。核越多、差越大——親眼確認 race 的時序本質。
2. **改成 `_relaxed` 並反組譯對比**：把 `atomic_inc` 換成 `atomic_add_return_relaxed` 和無後綴的 `atomic_add_return`，各自反組譯（x86 和 ARM64 都試）。x86 上你可能看不出差別（barrier 是 no-op），ARM64 上 `_relaxed` 會少掉 `dmb`/用 `stadd` 而非 `ldaddal`——這就是 ordering 變體的實際成本。
3. **把裸指標 stack 改成 llist**：用 kernel 現成的 `llist_add`/`llist_del_all`（`include/linux/llist.h`）重寫 push demo，體會 kernel 為什麼只給無鎖 push 不給無鎖 pop（ABA）。
4. **故意用 atomic_t 做溢位**：寫一個模組把 `atomic_t` 一路 `atomic_inc` 到 `INT_MAX` 再 +1，觀察它靜悄悄回繞成負數；換成 `refcount_t` 同樣操作，看它印 WARN 並飽和。你剛親手復現了一個 refcount overflow 漏洞的根因。

## 本章重點整理

- `counter++` 是 load-modify-store 三步，多核交錯會 lost update；atomic 操作請硬體把這三步焊成不可分割的一步。
- x86 用 `lock` prefix 靠 MESI 鎖 cache line（悲觀）；ARM64 用 LL/SC 樂觀重試或 LSE 單指令。x86 的 `lock` 順帶是 full barrier，ARM64 的 barrier 是真指令、真成本。
- `cmpxchg`（CAS）是所有無鎖演算法的基石：讀舊值→算新值→CAS 賭提交、失敗重試。ABA 問題靠 RCU/延後釋放解（Ch 27）。
- kernel 的 atomic **不一定帶 barrier**：純讀寫和無回傳 RMW 無 ordering，有回傳/條件的 RMW 才是 full barrier；`_acquire`/`_release`/`_relaxed` 讓你精準取捨，精確語意由 LKMM（`tools/memory-model/`）定義。
- 引用計數用 `refcount_t`/`kref` 不用 `atomic_t`（飽和防溢位 UAF）；要保護多變數/多步邏輯/要睡就上鎖（Ch 25-26）。

## 自我檢核

- [ ] 不看筆記，能畫出兩核 `counter++` 交錯造成 lost update 的時序圖，並說明 atomic 怎麼消除它
- [ ] 能說出 x86 `atomic_inc` 編成什麼指令、ARM64 編成什麼、兩者哲學差在哪
- [ ] 能手寫一個 CAS loop，並解釋 cmpxchg 失敗時為什麼要回頭重讀而非直接寫
- [ ] 面試被問「kernel 的 atomic 自帶 memory barrier 嗎」，能分類別答（純讀寫/無回傳 RMW/有回傳 RMW）並說出去哪查（`atomic_t.txt`）
- [ ] 能解釋為什麼 `refcount_t` 取代 `atomic_t` 做引用計數，跟 kernel_pwn 的 UAF 攻擊面連得起來
- [ ] 能判斷一個場景該用 atomic 還是該上鎖（單變數 vs 多步/多變數/要睡）

## 延伸閱讀

### 官方文件

- **[Documentation/atomic_t.txt](https://www.kernel.org/doc/html/latest/core-api/wrappers/atomic_t.html)**
  - **讀哪裡**：整篇，尤其「ORDERING」和「RMW ops」兩節那張表——列出每一個 atomic 操作到底有沒有 barrier、是哪種
  - **和本章的關聯**：本章 ordering 那節的「不要靠直覺、查這張表」指的就是它。寫並行 code 時放在手邊

- **[Documentation/atomic_bitops.txt](https://www.kernel.org/doc/html/latest/core-api/wrappers/atomic_bitops.html)** 與 **[memory-barriers.txt](https://www.kernel.org/doc/html/latest/staging/index.html)**
  - **讀哪裡**：atomic_bitops 講 `set_bit` 家族的 ordering；memory-barriers.txt 是 barrier 的權威長文（接 Ch 23）
  - **前提**：先讀完 Ch 23 的 barrier 直覺再來，否則 memory-barriers.txt 會很硬

### 源碼與工具

- **[tools/memory-model/](https://elixir.bootlin.com/linux/v6.12/source/tools/memory-model)** — LKMM 本體
  - **這是什麼**：Linux Kernel Memory Model 的形式化定義（`.cat` 檔）、`litmus-tests/` 範例、和 `README`。配 `herd7` 可以真的跑驗證
  - **為什麼值得看**：這是「atomic + barrier 的精確語意」的唯一權威來源。想從「大概懂」進到「能證明」，從 `README` 和 `litmus-tests/` 開始

- **[include/linux/refcount.h](https://elixir.bootlin.com/linux/v6.12/source/include/linux/refcount.h) 檔頭註解**
  - **讀哪裡**：檔案開頭那大段註解，逐條列 refcount 相對 atomic 的每個保證與 ordering 差異
  - **能學到什麼**：refcount 為什麼這樣設計、每個函式的 barrier 語意——本章 refcount 一節的一手來源

### 文章 / 書籍

- **[LWN: An introduction to lockless algorithms](https://lwn.net/Articles/844224/)** — Paolo Bonzini（2021 系列）
  - **讀哪裡**：整個系列（memory barriers、RCU、seqlocks 分篇），第一篇正好接本章的 atomic 與 ordering
  - **為什麼值得讀**：把「無鎖到底怎麼推理」講得極清楚，是 Ch 24→27 之間最好的過場讀物

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》** — Paul McKenney（免費電子書）
  - **讀哪裡**：counting 章（各種 counter 實作的取捨）與 memory ordering 章
  - **定位**：RCU 之父寫的並行聖經，把「per-CPU / atomic / 鎖 / 無鎖」的取捨系統性攤開，讀完本章想再深入的最佳去處。厚，當工具書查

atomic 給了你「單一個變數的原子更新」這把最小的鎚子。但真實 kernel 裡多數臨界區是「一段邏輯」——多個變數要一起改、中間態不能被別人看到。下一章我們造第一把真正的鎖：spinlock，看它怎麼用本章的 atomic（`cmpxchg`）當地基，以及為什麼現代 kernel 把它換成了排隊式的 qspinlock。

→ [Ch 25 spinlock、rwlock、qspinlock](./25-spinlocks.md)
