# Ch 20 — non-blocking 鏈結串列：CAS 與 C11 atomics

> **目標**：分得清 blocking、lock-free、wait-free 的差別；會用 C11 `<stdatomic.h>` 的 CAS 寫出正確的 lock-free 已排序串列（Harris 的標記刪除法），並能解釋「天真版 CAS 串列」為什麼會弄丟節點；理解 ABA 問題與記憶體回收問題（hazard pointer、epoch）；會用 pthread 壓力測試與 `-fsanitize=thread` 驗證並行程式，也知道 TSan 抓不到什麼。
> **對應原文**：[你所不知道的 C 語言：記憶體管理、對齊及硬體特性篇](https://hackmd.io/@sysprog/c-memory) 的「案例分析: concurrent-ll」「標示不用的節點」等節

> **環境**：WSL Ubuntu 22.04 x86_64（16 個邏輯處理器），gcc 11.4，glibc 2.35。並行程式的結果與交錯時機有關，本章的計數類輸出每次執行不同，文中標出的是實跑的其中幾次。x86-64 的記憶體模型比 Arm 強，某些錯誤只在 Arm 上現形，會特別標注。

## 為什麼需要這個？

[Ch 19](./19-linux-list-head.md) 的 `list_add` 是四行普通的指定。二個執行緒同時對同一條串列做 `list_add`，四行之間隨時可能被插隊，結果可能是一個節點不見、或 `prev`/`next` 互相矛盾。傳統解法是加 lock（mutex 或 spinlock）：同一時間只讓一個執行緒碰串列。

lock 簡單、正確，但有幾個根本問題：

- **持有 lock 的執行緒被搶占、page fault 或當掉，所有人都卡住**。在即時系統裡這就是 priority inversion；在核心的中斷處理裡，拿不到 lock 甚至可能 deadlock。
- **擴展性差**：所有執行緒排隊經過同一個點，核心數越多、搶得越兇。
- **不能在 signal handler 裡用**：handler 若打斷了正持有 lock 的同一個執行緒，再拿同一把 lock 就自鎖（[Ch 35](./35-stream-io-and-signals.md)）。

non-blocking 演算法的想法是：**不讓任何執行緒「等」另一個執行緒**。每個操作準備好新狀態後，用一道硬體保證原子的指令一次性地「如果還是我看到的那個舊狀態，就換成新狀態」；失敗就代表別人先改了，重讀再試。這道指令就是 compare-and-swap（CAS）。

但這章的重點不是「CAS 很快」，而是：**用 CAS 寫串列，插入很容易，刪除很難**。原文分析的 [concurrent-ll](https://github.com/jserv/concurrent-ll) 實作的是 Timothy Harris 在 2001 年提出的解法，它的核心技巧——把刪除標記藏在指標最低位元——同時用上了 [Ch 16](./16-alignment-and-padding.md) 的對齊保證與 [Ch 18](./18-linked-list-pointer-to-pointer.md) 的串列操作。

> 如果你對 pthread 與 data race 的基本概念不熟，可以先讀 [`../c_interview/22-c11-concurrency.md`](../c_interview/22-c11-concurrency.md)；本章會從頭講需要的部分。

## 先建立直覺

把共享串列想成一本放在桌上的共用帳本，每個人都可以來改。

- **lock**：桌上有一把鑰匙，拿到鑰匙的人才能改帳本。拿鑰匙的人去上廁所，其他人只能乾等。
- **CAS**：每個人在旁邊草稿紙上算好新的一頁，然後走到帳本前說：「如果這一頁現在還是我剛才抄下來的樣子，就換成我的新頁；如果不是，我放棄、回去重算。」這個「比對加替換」由硬體保證不會被打斷。沒有人需要等誰——最慢只是重算。

CAS 的語意用虛擬碼寫（整段是一個不可分割的動作）：

```
bool CAS(addr, expected, desired):
    atomically {
        if (*addr == expected) { *addr = desired; return true; }
        else                   { return false; }
    }
```

用 CAS 插入一個節點到 `prev` 與 `next` 之間：

```
1. 準備：new->next = next          （此時 new 還沒人看得到，隨便改）
2. 發布：CAS(&prev->next, next, new)
         成功 → new 正式加入
         失敗 → prev->next 已被別人改了，重新找位置再試

  prev ──▶ next                prev ──▶ new ──▶ next
            ▲          CAS
   new ─────┘          ───▶
```

一道 CAS 就把節點「原子地」接進去。問題出在刪除，等一下就會看到。

## non-blocking 的分類

**non-blocking** 指「一個執行緒被暫停（搶占、睡眠、甚至死掉），不會讓其他執行緒無法前進」。依保證強度分三級，定義來自 Herlihy 等人的工作：

| 等級 | 保證 | 直覺 | 例子 |
|---|---|---|---|
| obstruction-free | 任何執行緒**單獨執行**（其他人暫停）時，有限步內完成 | 沒人干擾就會成功 | 某些 STM |
| **lock-free** | 任何時刻，**至少有一個**執行緒在有限步內完成操作 | 整體一定有進展，個別可能一直重試 | CAS 重試迴圈、Harris 串列、Treiber stack |
| **wait-free** | **每個**執行緒都在有限步內完成 | 沒有人會餓死 | `atomic_fetch_add`（x86 `lock xadd`）、某些 queue |

lock-free 的論證通常是：「我的 CAS 失敗，代表**有別人的 CAS 成功了**」，所以整體在前進。但個別執行緒在極端情況下可能一直輸（starvation），這是 lock-free 與 wait-free 的差別。

一個常見誤解：**lock-free 不等於比較快**。在低競爭時，一把沒人搶的 mutex 也只是一兩道原子指令；在高競爭時，CAS 重試會讓同一條 cache line 在核心之間來回彈跳（cache line ping-pong），也可能比 lock 慢。lock-free 買的是**進度保證**與**不怕被搶占**，效能要實測。

## C11 `<stdatomic.h>`

C11 之前，C 語言規格書裡沒有「執行緒」這個概念，並行程式只能靠編譯器擴充（GCC 的 `__sync_*`、`__atomic_*`）或平台 API。C11 帶來了記憶體模型與 `<stdatomic.h>`（N1570 7.17）。

### 第一個實驗：沒有 atomic 的計數器

4 個執行緒各把同一個 `long counter` 加 100 萬次：

```c
#include <stdio.h>
#include <pthread.h>
#include <stdatomic.h>

#define N 1000000
#ifdef FIXED
static atomic_long counter;
#else
static long counter;
#endif

static void *worker(void *arg)
{
    (void) arg;
    for (int i = 0; i < N; i++)
        counter++;          /* FIXED 時是 atomic read-modify-write */
    return NULL;
}

int main(void)
{
    pthread_t t[4];
    for (int i = 0; i < 4; i++)
        pthread_create(&t[i], NULL, worker, NULL);
    for (int i = 0; i < 4; i++)
        pthread_join(t[i], NULL);
    printf("counter = %ld (expected %d)\n", (long) counter, 4 * N);
    return 0;
}
```

```
$ gcc -O0 -Wall -pthread race.c -o race0 && ./race0 && ./race0
counter = 1207184 (expected 4000000)
counter = 1352449 (expected 4000000)
```

`counter++` 是「讀、加一、寫回」三步，二個執行緒交錯時其中一次加法被覆蓋。更陰險的是 `-O2`：

```
$ gcc -O2 -Wall -pthread race.c -o race && ./race && ./race
counter = 4000000 (expected 4000000)
counter = 4000000 (expected 4000000)
$ objdump -d --no-show-raw-insn race | grep -A4 "<worker>:"
0000000000001250 <worker>:
    1250:	endbr64
    1254:	addq   $0xf4240,0x2db9(%rip)        # 4018 <counter>
    125f:	xor    %eax,%eax
    1261:	ret
```

編譯器把整個迴圈合併成一道 `addq $1000000`（`0xf4240` = 1000000），交錯的機會小到測不出來，**程式看起來正確，其實仍然是錯的**。依 N1570 5.1.2.4 §25，二個執行緒對同一物件有衝突的存取、至少一個不是 atomic、且彼此沒有 happens-before 關係，就是 data race，**整個程式的行為未定義**。編譯器正是利用「沒有 data race」這個假設，才敢把迴圈合併。

### ThreadSanitizer：讓 data race 現形

TSan（`-fsanitize=thread`）在執行期追蹤每次記憶體存取與同步操作的 happens-before 關係：

```
$ gcc -g -O1 -fsanitize=thread -pthread race.c -o race_tsan
$ ./race_tsan
FATAL: ThreadSanitizer: unexpected memory mapping 0x59013b2b6000-0x59013b2b7000
```

第一次跑就失敗——這不是你的程式的錯。gcc 11 附的 TSan 執行期假設特定的位址空間配置，新版核心的位址隨機化（ASLR）可能把程式映射到它不認得的範圍。我們的環境核心是 `6.18.58.1-microsoft-standard-WSL2`。解法是對這次執行關閉 ASLR：

```
$ setarch $(uname -m) -R ./race_tsan
==================
WARNING: ThreadSanitizer: data race (pid=18507)
  Read of size 8 at 0x555555558018 by thread T2:
    #0 worker /home/ypp/c_sysprog_lab/p3b/race.c:15 (race_tsan+0x1263)

  Previous write of size 8 at 0x555555558018 by thread T1:
    #0 worker /home/ypp/c_sysprog_lab/p3b/race.c:15 (race_tsan+0x128d)

  Location is global 'counter' of size 8 at 0x555555558018 (race_tsan+0x000000004018)
  ...
SUMMARY: ThreadSanitizer: data race /home/ypp/c_sysprog_lab/p3b/race.c:15 in worker
==================
ThreadSanitizer: reported 1 warnings
counter = 3000000 (expected 4000000)
```

（新版 gcc/LLVM 的 TSan 已改善這個問題；也有人改用 `sysctl vm.mmap_rnd_bits=28` 全域調低隨機化，那需要 root。）TSan 精準指出第 15 行、哪個執行緒讀、哪個寫、物件是全域 `counter`。改成 `atomic_long`（`-DFIXED`）後結果恆為 4000000，TSan 也安靜了。N1570 6.5.2.4 §2 規定：對 atomic 型別物件的後置 `++` 是一個 `memory_order_seq_cst` 的 read-modify-write 操作。

### atomic 型別與操作一覽

| 寫法 | 意義 |
|---|---|
| `_Atomic(T) x;` 或 `_Atomic T x;` | 宣告 atomic 物件（6.7.2.4、6.7.3） |
| `atomic_long`、`atomic_int` … | `<stdatomic.h>` 的 typedef |
| `atomic_load(&x)`、`atomic_store(&x, v)` | 原子讀寫，預設 `memory_order_seq_cst` |
| `atomic_fetch_add(&x, v)` | 原子加，回傳舊值 |
| `atomic_compare_exchange_strong(&x, &expected, desired)` | CAS（7.17.7.4） |
| `atomic_compare_exchange_weak(...)` | 允許「假失敗」的 CAS，適合放在重試迴圈裡 |
| `*_explicit(..., memory_order)` | 指定記憶體順序 |
| `atomic_is_lock_free(&x)`、`ATOMIC_*_LOCK_FREE` | 查詢是否由硬體原子指令實作（7.17.5、7.17.1） |

C11 CAS 的介面有一個容易踩的地方：`expected` 是**指標**。7.17.7.4 的語意是：原子地比較 `*object` 與 `*expected`，相等則把 `desired` 寫入 `*object`；**不相等則把 `*object` 的目前值寫回 `*expected`**。所以失敗後 `expected` 已經被更新成最新值，下一輪重試可以直接用；但如果你想保留原本的期望值，要先複製一份（本章的 `cas()` 包裝函式就是傳值進來、在函式內取位址，避免呼叫端的變數被改掉）。

`weak` 版本允許「值其實相等卻回報失敗」（spurious failure），因為在 Arm、RISC-V 這類以 LL/SC（load-linked/store-conditional）實作 CAS 的架構上，中間發生任何干擾都會讓 SC 失敗；放在重試迴圈裡用 `weak` 可以省掉內層迴圈。x86 的 `lock cmpxchg` 不會假失敗，二者產生相同程式碼。

### CAS 在 x86-64 上長什麼樣子

```c
#include <stdatomic.h>
#include <stdbool.h>

bool cas_ptr(_Atomic(void *) *obj, void **expected, void *desired)
{
    return atomic_compare_exchange_strong(obj, expected, desired);
}
long fetch_add(atomic_long *p) { return atomic_fetch_add(p, 1); }
void store_seq_cst(atomic_long *p, long v) { atomic_store(p, v); }
void store_release(atomic_long *p, long v)
{
    atomic_store_explicit(p, v, memory_order_release);
}
```

```
$ gcc -O2 -S -fno-asynchronous-unwind-tables -masm=intel cas_asm.c -o -
cas_ptr:
	mov	rax, QWORD PTR [rsi]          ; rax = *expected
	lock cmpxchg	QWORD PTR [rdi], rdx  ; if (*obj == rax) *obj = rdx; else rax = *obj
	sete	r8b                          ; 成功與否
	je	.L1
	mov	QWORD PTR [rsi], rax          ; 失敗：把目前值寫回 *expected
.L1:
	mov	eax, r8d
	ret
fetch_add:
	mov	eax, 1
	lock xadd	QWORD PTR [rdi], rax
	ret
store_seq_cst:
	xchg	rsi, QWORD PTR [rdi]
	ret
store_release:
	mov	QWORD PTR [rdi], rsi
	ret
```

（`endbr64` 與組譯器指示已略去，註解是我們加的。）幾個觀察：

- `lock cmpxchg` 就是硬體 CAS，`lock` 前綴讓這次讀改寫在整個系統中不可分割；失敗分支的 `mov [rsi], rax` 正是「把目前值寫回 `*expected`」。
- `atomic_fetch_add` 是 `lock xadd`，一道指令必定完成，所以它是 **wait-free** 的。
- `seq_cst` 的 store 用 `xchg`（隱含 lock，附帶完整的 memory barrier），`release` 的 store 只是普通 `mov`——在 x86 的 TSO（Total Store Order）模型下，普通 store 本來就不會與先前的 load/store 重排，release 語意不需額外指令。**在 Arm64 上 release store 會變成 `stlr`**，差異才顯現出來（本段未在 Arm 上實測）。

### 記憶體順序：一頁版

CPU 與編譯器都會重排記憶體存取，只要單執行緒看起來結果一樣。多執行緒時這會出事：

```
執行緒 A（發布者）              執行緒 B（讀者）
n->key = 42;            (1)    p = atomic_load(&head->next);  (3)
CAS(&head->next, old, n); (2)  if (p == n) use(p->key);       (4)
```

B 在 (3) 看到了 A 在 (2) 寫入的 `n`，它能保證在 (4) 讀到 `42` 嗎？只有在 (2) 帶 **release** 語意、(3) 帶 **acquire** 語意時才保證：release 之前的所有寫入，對 acquire 到這個值的執行緒都可見（建立 happens-before，N1570 5.1.2.4 與 7.17.3）。`memory_order_seq_cst` 包含 acquire/release，並額外保證所有 seq_cst 操作有一個全域一致的順序。`memory_order_relaxed` 只保證這一次存取本身是原子的，**不建立任何順序**。

本章的 Harris 串列全部用 `seq_cst`，以正確性優先。等一下會故意改成 `relaxed`，看 TSan 怎麼抓。

## 天真版 CAS 串列：為什麼會弄丟節點

### 只有插入時沒問題

已排序串列（當作整數集合），頭尾各有一個哨兵（`head` 鍵值 `LONG_MIN`、`tail` 鍵值 `LONG_MAX`）。插入：找到 `left->key < key <= right->key` 且 `left->next == right` 的位置，`new->next = right`，再 `CAS(&left->next, right, new)`。若有別人同時在 `left` 後面插入，`left->next` 已不是 `right`，CAS 失敗、重找。只有插入的話，這是正確的 lock-free 演算法。

### 加入刪除就壞了

天真的刪除：找到 `left->next == right == 要刪的節點`，`CAS(&left->next, right, right->next)`。Harris 論文舉的情境：串列是 `H → 10 → 30 → T`，執行緒 1 要刪 10，執行緒 2 要在 10 與 30 之間插入 20。

```
初始：  H ──▶ [10] ──▶ [30] ──▶ T

T1（刪 10）準備：CAS(&H->next, 10, 30)
T2（插 20）準備：new20->next = 30; CAS(&n10->next, 30, 20)

情況 A：T2 先成功，T1 再成功
  H ──▶ [10] ──▶ [20] ──▶ [30]      T2 的 CAS 後
  H ─────────────────────▶ [30]      T1 的 CAS：H->next 從 10 換成 30
                [10] ──▶ [20] ──┘    20 跟著 10 一起不見了！

情況 B：T1 先成功，T2 再成功
  H ──────────▶ [30] ──▶ T           T1 的 CAS 後
         [10] ──▶ [20] ──▶ [30]      T2 的 CAS 仍然成功（n10->next 確實還是 30）
                                     但 10 已不在串列上，20 接在一個孤兒後面
```

（原文描述的是其中一種順序；二種順序其實都會弄丟 20，只是路徑不同。）問題的本質：T1 的 CAS 檢查的是 `H->next`，T2 的 CAS 檢查的是 `n10->next`——**二道 CAS 各自看的是不同的字，彼此察覺不到對方**。T2 以為它是在「串列中的節點 10」後面插入，但「10 是否還在串列中」這件事根本不在它 CAS 的比對範圍裡。

### 壓力測試：抓到現行犯

測試設計的關鍵是**讓每個操作的回傳值可預測**：8 個執行緒，執行緒 `tid` 只操作 `key % 8 == tid` 的鍵，各自用一個本地陣列 `model[]` 記錄「這個鍵目前應該在不在」。因為沒有別的執行緒會動自己的鍵，`insert` 在鍵不存在時必須回傳 `true`、`delete` 在鍵存在時必須回傳 `true`；任何不符都是演算法的錯。但鍵在串列中是交錯排列的，所以各執行緒的節點互為鄰居，插入與刪除會在邊界上真正競爭。

```
$ gcc -O2 -Wall -Wextra -pthread -DNAIVE harris.c -o naive
$ for i in 1 2 3; do ./naive; done
naive : threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=21 (lost_insert=9)
naive : threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=17 (lost_insert=4)
naive : threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=14 (lost_insert=3)
```

每 160 萬次操作有十幾次回傳值錯誤。`lost_insert` 是「我之前插入成功，現在要刪卻說不存在」——就是上面情況 A/B 的節點遺失。其餘的錯誤是反方向：刪除時 `left` 本身剛被別人摘掉，CAS 改寫的是孤兒節點的 `next`，回報成功但目標其實還掛在串列上，之後再插入同一個鍵就會失敗。有趣的是最終狀態（`present` 等於 `expected`）看起來都對，因為後續操作把錯誤「修」掉了——**只檢查最終狀態的測試抓不到這種 bug**，必須逐一檢查每個操作的回傳值。

## Harris 的解法：先標記，再摘除

### 想法

Harris 的關鍵觀察：既然問題是「插入者沒察覺到前驅節點正在被刪除」，那就讓**刪除的意圖寫在被刪節點的 `next` 欄位上**。刪除拆成二步：

1. **邏輯刪除**：用 CAS 在 `right->next` 上設一個「已刪除」標記。這一刻起，這個節點在語意上就不屬於集合了。
2. **實體刪除**：再用 CAS 把 `left->next` 從 `right` 改成 `right->next`，真正摘掉。這一步失敗沒關係，之後任何經過的 `search` 會順手摘掉。

插入者 CAS 的是 `n10->next`，期望值是「未標記的 30」。一旦刪除者先把 `n10->next` 標記成「標記的 30」，插入者的 CAS 必定失敗——**衝突被拉到同一個字上**，CAS 就察覺得到了。原文的比喻是：先掛一塊牌子說「此路段即將拆除」，車子還過得去，但不能再在這段路上加蓋東西。

```
邏輯刪除 10：   H ──▶ [10|30*] ──▶ [30] ──▶ T      （* = 標記位元）
T2 想插 20：    CAS(&n10->next, 30, 20)  → 失敗，因為現在的值是 30*，不是 30
T2 重新 search：經過 10 時發現它已標記，順手 CAS(&H->next, 10, 30) 摘掉它
                然後在 H 與 30 之間插入 20
```

### 標記藏在哪裡：指標的最低位元

`next` 欄位只有一個指標的寬度，標記要放哪？答案是對齊。原文的推論：C11 6.7.2.1 說結構成員的對齊是 implementation-defined；GCC 的文件說由 ABI 決定；x86-64 SysV ABI 規定結構體的對齊等於其最嚴格成員的對齊。所以

```c
typedef struct node {
    long key;
    _Atomic(uintptr_t) next;   /* 最低位元 = 刪除標記 */
} node_t;
```

對齊到 8 bytes，任何合法的 `node_t *` 最低 3 位元恆為 0，最低位元可以挪來當旗標。實測確認，並用 `_Static_assert` 把這個假設寫成編譯期檢查：

```c
_Static_assert(_Alignof(node_t) >= 2, "low bit of node_t* must be free");
```

```
sizeof=16 alignof=8
heap 0x580f7375a2a0 low3=0, heap 0x580f7375a2c0 low3=0, stack 0x7ffd08a96b70 low3=0
```

這種把對齊騰出的低位元拿來夾帶資訊的手法稱為指標標記（pointer tagging）。它依賴的是實作的對齊保證，以及「指標與 `uintptr_t` 互轉再轉回仍相等」（`uintptr_t` 是選配型別，C11 7.20.1.4）；對轉換後的整數做位元運算再轉回指標，ISO C 沒有保證結果有效（6.3.2.3 §5–6 只說是 implementation-defined），GCC 與 Clang 在主流平台上的行為是可預期的。這也是為什麼本章把 `next` 宣告成 `_Atomic(uintptr_t)` 而不是 `_Atomic(node_t *)`——整數上做位元運算才名正言順。同樣的技巧在核心與執行期系統裡處處可見：Linux 紅黑樹把顏色存在 `__rb_parent_color` 的低位元，許多語言 runtime 用低位元區分小整數與指標。

```c
static inline bool is_marked(uintptr_t p) { return p & 1; }
static inline uintptr_t marked(uintptr_t p) { return p | 1; }
static inline node_t *ptr_of(uintptr_t p) { return (node_t *) (p & ~(uintptr_t) 1); }
```

concurrent-ll 的 [`src/lockfree/list.c`](https://github.com/jserv/concurrent-ll/tree/master/src/lockfree) 用的就是同一組三個輔助函式（`is_marked_ref`、`get_marked_ref`、`get_unmarked_ref`），CAS 則透過 `include/atomics.h` 的 `CAS_PTR` 巨集包裝 GCC 的 `__atomic_compare_exchange`（`__ATOMIC_SEQ_CST`）——功能上等同本章的 C11 `atomic_compare_exchange_strong`。

### 完整實作

以下是 `harris.c` 的資料結構與三個操作（測試程式的 `main` 與 `worker` 在下一節）。`-DNAIVE` 切換成天真版，`-DRELAXED` 是之後故意弄錯用的開關：

```c
/* 已排序的整數集合：-DNAIVE 為「直接 CAS」的天真版，否則為 Harris 標記刪除法 */
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <pthread.h>

#ifdef RELAXED            /* 故意弄錯：全部改用 relaxed */
#define MO memory_order_relaxed
#else
#define MO memory_order_seq_cst
#endif
#define LOAD(p) atomic_load_explicit((p), MO)

typedef struct node {
    long key;
    _Atomic(uintptr_t) next;   /* 最低位元 = 刪除標記 */
} node_t;

static node_t head_node, tail_node;
static node_t *const head = &head_node, *const tail = &tail_node;

static inline bool is_marked(uintptr_t p) { return p & 1; }
static inline uintptr_t marked(uintptr_t p) { return p | 1; }
static inline node_t *ptr_of(uintptr_t p) { return (node_t *) (p & ~(uintptr_t) 1); }

static bool cas(_Atomic(uintptr_t) *obj, uintptr_t expected, uintptr_t desired)
{
    return atomic_compare_exchange_strong_explicit(obj, &expected, desired, MO, MO);
}

static void list_init(void)
{
    head->key = LONG_MIN;
    tail->key = LONG_MAX;
    atomic_init(&head->next, (uintptr_t) tail);
    atomic_init(&tail->next, 0);
}

/* Harris (2001) 的 search：回傳 right，並保證 left->next == right、二者皆未標記 */
static node_t *search(long key, node_t **left_out)
{
    node_t *left = NULL, *right;
    uintptr_t left_next = 0;
retry:;
    node_t *t = head;
    uintptr_t t_next = LOAD(&head->next);
    do {                                   /* 1. 找 left 與 right */
        if (!is_marked(t_next)) {
            left = t;
            left_next = t_next;
        }
        t = ptr_of(t_next);
        if (t == tail)
            break;
        t_next = LOAD(&t->next);
    } while (is_marked(t_next) || t->key < key);
    right = t;

    if (left_next == (uintptr_t) right) {  /* 2. 相鄰：直接檢查 right */
        if (right != tail && is_marked(LOAD(&right->next)))
            goto retry;
        *left_out = left;
        return right;
    }
    /* 3. 中間夾著已標記節點：一次 CAS 把它們全部摘掉 */
    if (cas(&left->next, left_next, (uintptr_t) right)) {
        if (right != tail && is_marked(LOAD(&right->next)))
            goto retry;
        *left_out = left;
        return right;
    }
    goto retry;
}

static bool list_insert(node_t *n)
{
    for (;;) {
        node_t *left, *right = search(n->key, &left);
        if (right != tail && right->key == n->key)
            return false;
        atomic_store_explicit(&n->next, (uintptr_t) right, memory_order_relaxed);
        if (cas(&left->next, (uintptr_t) right, (uintptr_t) n))
            return true;
    }
}

static bool list_delete(long key)
{
    node_t *left, *right;
    uintptr_t right_next;
    for (;;) {
        right = search(key, &left);
        if (right == tail || right->key != key)
            return false;
        right_next = LOAD(&right->next);
        if (!is_marked(right_next) &&
            cas(&right->next, right_next, marked(right_next)))
            break;                          /* 邏輯刪除成功：這一刻起它就不在集合裡 */
    }
    if (!cas(&left->next, (uintptr_t) right, right_next))
        (void) search(right->key, &left);   /* 實體摘除失敗：交給 search 清掉 */
    return true;
}
```

天真版（`#ifdef NAIVE` 分支）的 `search` 只是沿著 `next` 找到第一個 `key >= 目標` 的節點，`list_delete` 直接 `cas(&left->next, right, succ)`，`list_insert` 與 Harris 版相同。

### search 怎麼運作

`search` 是整個演算法的心臟，它回傳一對 `(left, right)`，保證：`left->key < key <= right->key`、二者都**未標記**、且 `left->next == right`。

```
例：找 key = 25，串列中 20 與 22 已被邏輯刪除（*）

  H ──▶ [10] ──▶ [20*] ──▶ [22*] ──▶ [30] ──▶ T
         ▲                            ▲
        left                        right
  left_next = 20（left 最後一次看到的未標記 next）

步驟 1：走訪時記住「最後一個 next 未標記的節點」當 left，
        跳過所有已標記的節點，停在第一個未標記且 key >= 25 的節點 → right = 30
步驟 3：left_next (20) != right (30)，中間夾著已標記節點
        CAS(&n10->next, 20, 30) 一次摘掉 20 與 22
        成功 → 再確認 right 沒有在這期間被標記，回傳
        失敗 → 有人改了 n10->next（插入或標記了 10），從頭再來
```

二個容易漏掉的細節：

- **為什麼 CAS 的期望值是 `left_next` 而不是 `right`**：我們要確認「`left->next` 從我看到之後沒有被改過」，包括 `left` 本身沒有被標記（若被標記，`left->next` 的值會帶上 1，與 `left_next` 不等，CAS 失敗）。
- **為什麼回傳前要再檢查 `right` 有沒有被標記**：在 CAS 成功與回傳之間，別人可能剛好把 `right` 邏輯刪除了；回傳一個已刪除的 `right` 會讓插入者接在它前面（這沒問題），但刪除者會以為它還在。保守做法是重來。

### 線性化點：「到底什麼時候算刪掉了」

並行資料結構的正確性標準是 **linearizability**（可線性化）：每個操作看起來都在它開始與結束之間的某一個瞬間「一次發生」，這個瞬間叫線性化點（linearization point）。Harris 串列的線性化點：

| 操作 | 線性化點 |
|---|---|
| 成功的 insert | `CAS(&left->next, right, n)` 成功的那一刻 |
| 成功的 delete | 標記 `right->next` 的 CAS 成功的那一刻（不是實體摘除） |
| 失敗的 insert／delete | `search` 中確認 `right` 未標記的那次讀取 |

把刪除的線性化點放在「標記」而不是「摘除」，是這個演算法能成立的關鍵：標記之後，任何人都可以幫忙摘除，誰摘的都不影響語意。這種「別人幫我完成」的模式稱為 helping，是 lock-free 演算法的常見手法。

### 實測：Harris 版

同一個壓力測試：

```
$ gcc -O2 -Wall -Wextra -pthread harris.c -o harris
$ for i in 1 2 3; do ./harris; done
harris: threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
harris: threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
harris: threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
```

160 萬次操作，回傳值零錯誤。要說清楚：**測試通過不等於證明正確**，它只是在這台機器、這些交錯下沒有發現錯誤。Harris 論文附有正確性論證，而 x86 的強記憶體模型也會掩蓋某些順序錯誤（下一節）。

壓力測試的 harness：

```c
/* ---- 壓力測試：每個執行緒只操作 key % T == tid 的鍵，因此每個操作的回傳值可預測 ---- */
#define T 8
#define KEYS 512          /* 每個執行緒負責的鍵數 */
static long ops_per_thread = 200000;
static long mismatches[T], lost_insert[T];
static unsigned char model[T][KEYS];

static void *worker(void *arg)
{
    long tid = (long) arg;
    node_t *pool = malloc(sizeof(node_t) * ops_per_thread); /* 不回收：避免 UAF，見本章「記憶體回收」 */
    long used = 0;
    unsigned s = 12345u + (unsigned) tid;
    for (long i = 0; i < ops_per_thread; i++) {
        s = s * 1103515245u + 12345u;
        int idx = (s >> 8) % KEYS;
        long key = (long) idx * T + tid;
        if ((s >> 20) & 1) {
            node_t *n = &pool[used++];
            n->key = key;
            bool ok = list_insert(n);
            if (ok == model[tid][idx])     /* 已存在卻插入成功，或不存在卻失敗 */
                mismatches[tid]++;
            if (!ok)
                used--;
            model[tid][idx] = 1;
        } else {
            bool ok = list_delete(key);
            if (ok != model[tid][idx])
                mismatches[tid]++;
            if (!ok && model[tid][idx])
                lost_insert[tid]++;          /* 先前插入成功的節點不見了 */
            model[tid][idx] = 0;
        }
    }
    return NULL;
}
```

`main` 建立 8 個執行緒、`join` 後走訪串列，檢查「未標記節點遞增」以及「恰好等於所有 `model` 的聯集」（`zombie` 是應已刪除卻還在的節點數），印出上面的統計。亂數用的是 glibc 風格的 LCG 常數（1103515245、12345），只為了產生可重現的操作序列，取中間位元以避開 LCG 低位元週期短的缺點。

### TSan：抓得到什麼、抓不到什麼

```
$ gcc -g -O1 -fsanitize=thread -pthread harris.c -o harris_tsan
$ setarch $(uname -m) -R ./harris_tsan 50000
harris: threads=8 ops=400000  expected=2020 present=2020  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
$ gcc -g -O1 -fsanitize=thread -pthread -DNAIVE harris.c -o naive_tsan
$ setarch $(uname -m) -R ./naive_tsan 50000
naive : threads=8 ops=400000  expected=2020 present=2020  zombie=0  bad_order=0  mismatched_returns=6 (lost_insert=1)
```

**天真版在 TSan 下也完全安靜**，但它明明是錯的。原因：天真版的每次共享存取都是 atomic，沒有 data race；它的錯誤是**演算法層級**的（違反 linearizability），不是記憶體模型層級的。TSan 只回答「有沒有 data race」，不回答「你的演算法對不對」。

反過來，把所有記憶體順序改成 `relaxed`（`-DRELAXED`）：

```
$ gcc -g -O1 -fsanitize=thread -pthread -DRELAXED harris.c -o relaxed_tsan
$ setarch $(uname -m) -R ./relaxed_tsan 20000
==================
WARNING: ThreadSanitizer: data race (pid=21587)
  Read of size 8 at 0x7ffff62809d0 by thread T2:
    #0 search /home/ypp/c_sysprog_lab/p3b/harris.c:96 (relaxed_tsan+0x1414)
    #1 list_delete /home/ypp/c_sysprog_lab/p3b/harris.c:132 (relaxed_tsan+0x1616)
    #2 worker /home/ypp/c_sysprog_lab/p3b/harris.c:173 (relaxed_tsan+0x1616)

  Previous write of size 8 at 0x7ffff62809d0 by thread T1:
    #0 worker /home/ypp/c_sysprog_lab/p3b/harris.c:165 (relaxed_tsan+0x1876)

  Location is heap block of size 320000 at 0x7ffff6280000 allocated by thread T1:
```

第 165 行是 `n->key = key;`（發布前的普通寫入），第 96 行是 `search` 裡的 `t->key < key`（另一個執行緒讀新節點的鍵）。發布用的 CAS 是 relaxed，沒有 release 語意；讀端的 load 也是 relaxed，沒有 acquire。二者之間沒有 happens-before，`key` 的普通讀寫就構成 data race——正是「記憶體順序：一頁版」那個例子。

而不開 TSan 直接在 x86 上跑 `-DRELAXED` 版：

```
$ gcc -O2 -Wall -pthread -DRELAXED harris.c -o relaxed && ./relaxed && ./relaxed
harris: threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
harris: threads=8 ops=1600000  expected=2088 present=2088  zombie=0  bad_order=0  mismatched_returns=0 (lost_insert=0)
```

完全正常。x86 的 TSO 模型下，store 不會與先前的 store 重排，`lock cmpxchg` 本身又是完整屏障，所以硬體碰巧替你守住了順序；但編譯器仍可依 C11 模型重排，換到 Arm64 或 POWER 上，讀者就可能讀到尚未初始化的 `key`（**本段未在 Arm 上實測，為依記憶體模型推得的預期**）。這就是為什麼並行程式要用工具（TSan、模型檢查器）而不能只靠「在我的機器上跑過」。

## ABA 問題

### 情境

CAS 只比對「值」，不知道「這個值中間有沒有變過又變回來」。經典例子是 Treiber stack（lock-free 堆疊，pop 就是 `CAS(&top, old, old->next)`）：

```
堆疊：top → A → B → C

T1：pop 開始，讀到 old = A、next = B，被搶占
T2：pop A、pop B（B 被 free）、push A（A 被重用，又放回去）
    堆疊變成 top → A → C
T1：恢復，CAS(&top, A, B) —— top 確實還是 A，成功！
    top 變成 B，一個已經被釋放的節點
```

值從 A 變成 C 再變回 A（A → ... → A），CAS 被騙了。

### 逐步重現

真正的並行很難穩定重現這個時序，所以我們在單一執行緒中**手動排定二個執行緒的交錯**：

```c
/* 單執行緒逐步重現 ABA：把二個執行緒的交錯手動排成固定順序 */
static _Atomic(node_t *) top;

static void demo_plain(void)
{
    pool[0].next = &pool[1]; pool[1].next = &pool[2]; pool[2].next = NULL;
    atomic_store(&top, &pool[0]);
    dump("initial", atomic_load(&top));

    /* T1：pop 開始，讀到 old = A, next = B，然後被搶占 */
    node_t *old = atomic_load(&top), *next = old->next;

    /* T2：pop A、pop B（B 被釋放，之後可能被重用）、再 push A 回去 */
    node_t *a = atomic_load(&top); atomic_store(&top, a->next);
    node_t *b = atomic_load(&top); atomic_store(&top, b->next);
    b->name = '?'; b->next = NULL;          /* 模擬 B 已被 free 並另作他用 */
    a->next = atomic_load(&top); atomic_store(&top, a);
    dump("after T2 (pop A, pop B, push A)", atomic_load(&top));

    /* T1 恢復：top 仍是 A，CAS 成功，卻把已釋放的 B 裝回去 */
    bool ok = atomic_compare_exchange_strong(&top, &old, next);
    printf("T1 CAS(top, A, B) = %s\n", ok ? "success" : "fail");
    dump("after T1", atomic_load(&top));
}
```

```
== pointer-only CAS ==
initial                            top -> A -> B -> C
after T2 (pop A, pop B, push A)    top -> A -> C
T1 CAS(top, A, B) = success
after T1                           top -> ?
```

C 不見了，堆疊頂端是一塊「已釋放、內容是垃圾」的記憶體。在真實程式裡，這是 use-after-free，也是可利用的記憶體破壞。

### 解法一：版本號（tagged pointer）

讓「值」不只包含指標，也包含一個每次修改都遞增的版本號（tag）。A 回來時版本號已經不同，CAS 就會失敗。範例把節點索引與 32 位元 tag 打包進一個 64 位元整數：

```c
static _Atomic uint64_t ttop;   /* 高 32 位元：tag；低 32 位元：索引+1（0 表示空） */
static inline uint64_t pack(uint32_t tag, uint32_t idx) { return (uint64_t) tag << 32 | idx; }
```

```
== tagged CAS ==
initial                            top -> A -> B -> C
after T2 (pop A, pop B, push A)    top -> A -> C
T1 CAS = fail (top tag is now 3, T1 expected tag 0)
after T1                           top -> A -> C
```

T2 改了 3 次 top，tag 從 0 變成 3，T1 期望 tag 0 的 CAS 失敗，堆疊完好。代價：tag 會回繞（32 位元要 $2^{32}$ 次修改才回到原值，實務上被視為足夠，但不是數學上的保證）；而且「指標 + tag」要一次 CAS，指標本身就 64 位元，完整的指標加 tag 需要 128 位元的 CAS（x86-64 的 `cmpxchg16b`）。用 gcc 11 查詢：

```
ATOMIC_LONG_LOCK_FREE=2 ATOMIC_POINTER_LOCK_FREE=2
sizeof(tagged_t)=16
is_lock_free: long=1 ptr=1 tagged16=0
```

（加 `-mcx16` 結果相同；要連結 `-latomic`，否則 `__atomic_is_lock_free` 找不到。）16 bytes 的 `_Atomic` 結構被回報為**不是** lock-free：gcc 把它交給 libatomic，而 libatomic 可能以內部 lock 實作。就我所知，這是 GCC 7 之後的刻意選擇——只用 `cmpxchg16b` 實作 atomic load 會對記憶體做寫入，對唯讀映射的物件不成立。需要 128 位元 CAS 的程式通常改用編譯器內建函式或內嵌組語，或者像本例一樣用索引取代指標、把 tag 塞進剩下的位元（x86-64 的使用者空間指標實際只用低 47 位元，高位元也常被拿來放 tag）。

### 解法二：根本不讓位址被重用

ABA 的根源是「A 被釋放後又被重新配置到同一個位址」。如果能保證**還有執行緒可能持有 A 的指標時，A 不會被釋放**，ABA 就不會發生。這正是下一節記憶體回收問題的解法——二個問題其實是同一枚硬幣的兩面。

Harris 串列有沒有 ABA？它的節點一旦被標記就不會再被「取消標記」，`next` 欄位不會回到舊值；只要節點不被釋放重用，就沒有 ABA。這也是為什麼本章的測試**從不 `free`**——每個執行緒的節點來自一個只增不減的 pool。

## 記憶體回收：lock-free 最難的部分

### 問題

在有 lock 的串列裡，`list_del` 之後立刻 `free` 是安全的：拿著 lock，沒有別人在看這個節點。lock-free 串列不行：

```
T1：search 走到節點 X，讀了 X 的位址，正要讀 X->next
T2：刪除 X（標記 + 摘除），然後 free(X)
T1：讀 X->next —— use-after-free
```

T2 無從得知 T1 手上還握著 X。concurrent-ll 的 README 也直說：lock-free 結構上被移除的節點不一定能立刻釋放，因為別的執行緒可能還在參照它；有 lock 時則相對直接。所以本章（以及原文的 concurrent-ll 實驗）都選擇「不回收」或「全部結束後才回收」，這在長時間執行的程式裡等同記憶體洩漏。

### 三種主流解法

| 方法 | 想法 | 代價 | 代表 |
|---|---|---|---|
| **hazard pointer** | 每個執行緒公告「我現在正在讀哪幾個節點」（hazard pointer）；要釋放節點的執行緒先放進待回收清單，定期掃描所有人的 hazard pointer，沒人公告的才真正 `free` | 每次讀取節點都要寫入公告並加 memory barrier，讀端變慢；需要重新驗證 | Maged Michael（2004）；C++26 `std::hazard_pointer` |
| **epoch-based reclamation（EBR）** | 全域有一個 epoch 計數器；執行緒進入臨界區時登記目前 epoch。被刪節點標上刪除時的 epoch，等所有執行緒都離開該 epoch 後才釋放 | 讀端很便宜；但**一個卡住的執行緒會讓所有回收停擺**，記憶體無上限成長 | Keir Fraser（2004）；Rust `crossbeam-epoch` |
| **RCU** | 讀端不做任何同步（或只關搶占），寫端在 grace period（所有 CPU 都經歷過一次 quiescent state）結束後才釋放 | 讀端幾乎零成本；寫端要等 grace period；最適合讀多寫少 | Linux 核心、userspace-rcu（liburcu） |

hazard pointer 的讀取流程大致是：

```
1. p = load(&left->next)
2. hazard[my_tid] = p           ← 公告：我要讀 p
3. 重新讀 left->next，若 != p，回到 1   ← 確認公告之前 p 沒被摘掉
4. 安全地讀 p->key、p->next
5. 用完後 hazard[my_tid] = NULL
```

第 3 步的重新驗證不能省：在第 1 步與第 2 步之間，p 可能已經被摘除並放進別人的待回收清單；只有確認「公告之後 p 仍然在串列上」，回收者掃描時才一定看得到這個公告。這段要正確，第 2 步的寫入與第 3 步的讀取之間需要 `seq_cst` 的順序（store-load 不能重排，正是 x86 TSO 唯一允許的重排）。

Linux 核心的選擇是 RCU：[Ch 19](./19-linux-list-head.md) 提到的 `list_add_rcu`、`list_del_rcu`、`list_for_each_entry_rcu` 就是一組讀端不持 lock 的串列操作，寫端仍用 lock 互斥，刪除後用 `synchronize_rcu()` 或 `call_rcu()` 等 grace period 結束才釋放。它不是 lock-free 的寫端，但讀端是 wait-free 的，對核心「讀遠多於寫」的資料結構（行程串列、模組串列、路由表）非常合適。核心另外也有真正 lock-free 的 `llist`（`include/linux/llist.h`），加入用 `cmpxchg`，消費端用 `llist_del_all` 一次取走整條，用「只有一個消費者整批取走」的方式迴避了 ABA 與逐一刪除的難題。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 全域 mutex | 最簡單、容易證明正確 | 擴展性差、持有者被搶占全員卡住 | 預設選擇；競爭不激烈時效能也不差 |
| hand-over-hand（逐節點 lock） | 不同區段可並行 | 每走一步拿放一次 lock，常數大；死鎖風險 | concurrent-ll 的 lock-based 版本 |
| Harris lock-free 串列 | lock-free、刪除不會弄丟插入 | 記憶體回收困難；走訪要跳過標記節點 | 教學、搭配 EBR/hazard pointer 的函式庫 |
| RCU 串列 | 讀端近乎零成本 | 寫端要互斥並等 grace period | 讀多寫少（核心） |
| 天真 CAS 串列 | 程式短 | **錯誤**：插入與刪除並行時弄丟節點 | 不要用 |

一個務實的建議：**除非量測證明 lock 是瓶頸，否則用 lock**。lock-free 演算法難寫、難測、難審查，本章的天真版就是「看起來對、TSan 也說沒問題、但其實是錯的」的活例子。

## 踩雷集錦

1. **「用 atomic 就沒有並行 bug 了」**：atomic 只消除 data race。天真 CAS 串列每次存取都是 atomic，TSan 也安靜，但演算法本身違反 linearizability，會弄丟節點。
2. **「-O2 跑出正確結果，所以沒有 race」**：編譯器可能把整段迴圈合併成一道指令，讓 race 測不出來。data race 是未定義行為，「看起來正確」不代表正確；用 TSan 驗證。
3. **「x86 上 relaxed 跑起來沒事」**：x86 的 TSO 加上 `lock` 指令的完整屏障替你守住了大部分順序；同一份程式碼在 Arm64 上可能讀到未初始化的資料。記憶體順序要依 C11 模型推理，不要依賴特定硬體。
4. **CAS 失敗後沿用舊的 `expected`**：C11 的 `atomic_compare_exchange_*` 失敗時會把目前值寫回 `*expected`。若你以為它不變，下一輪可能用錯期望值；若你想保留原值，要先複製。
5. **刪除後立刻 `free`**：在 lock-free 結構中，其他執行緒可能還握著這個節點的指標，這是 UAF，也是 ABA 的溫床。要嘛不回收，要嘛用 hazard pointer、EBR 或 RCU。
6. **TSan 第一次跑就 `FATAL: unexpected memory mapping`**：舊版 TSan 與新核心的 ASLR 配置衝突，不是你的程式的錯。用 `setarch $(uname -m) -R` 關閉該次執行的 ASLR，或升級編譯器。
7. **把標記位元用在可能沒對齊的指標上**：pointer tagging 依賴對齊。若節點來自 `#pragma pack(1)` 的結構或手動切出來的 buffer，最低位元可能本來就是 1。用 `_Static_assert(_Alignof(node_t) >= 2, ...)` 把假設寫下來。

## 進階：再往深一層

- **讀 concurrent-ll 原始碼**：`src/lockfree/list.c` 的 `list_search` 與本章的 `search` 幾乎一一對應，`src/lock/` 是 hand-over-hand locking 版本。`make bench` 會跑擴展性測試並用 gnuplot 畫圖；試著自己量量看，在你的機器上 lock-free 版本從幾個執行緒開始勝出（或根本沒有勝出）。
- **Michael 的改良**：Maged Michael 2002 年的論文〈High Performance Dynamic Lock-Free Hash Tables and List-Based Sets〉修改了 Harris 的演算法，讓它能與 hazard pointer 搭配做記憶體回收——Harris 原版的 `search` 一次摘掉一整串標記節點，這對 hazard pointer 不友善。
- **wait-free 的代價**：把 Harris 串列變成 wait-free 需要 helping 機制（Timnat、Braginsky、Kogan、Petrank 2012），複雜度大增。這說明了為什麼業界多半停在 lock-free。
- **模型檢查**：CDSChecker、GenMC 這類工具會窮舉 C11 記憶體模型允許的所有交錯，能找到壓力測試永遠碰不到的錯誤。對真正要上線的 lock-free 程式碼，這比跑一百萬次測試可靠。
- **Linux 核心的 `READ_ONCE`/`WRITE_ONCE` 與 LKMM**：核心不用 C11 atomics，而是有自己的記憶體模型（Linux Kernel Memory Model，`tools/memory-model/`），可以用 herd7 工具驗證小段並行程式碼的所有可能結果。Ch 19 的 `WRITE_ONCE` 就屬於這套體系。

## 動手練習

1. 把 `list_delete` 的實體摘除那一步（`cas(&left->next, right, right_next)`）整個刪掉，只做邏輯刪除。壓力測試還會通過嗎？串列的長度會怎麼變化？解釋 `search` 在這裡扮演的角色。
2. 寫一個 `bool list_contains(long key)`，只讀不寫：沿著 `next` 走（跳過標記），找到 `key` 時回傳「它是否未標記」。它是 wait-free 的嗎？為什麼？加進壓力測試驗證。
3. 把 `cas()` 改成 `atomic_compare_exchange_weak_explicit`，在 x86 上看組合語言有沒有變化；說明在 Arm64 上會有什麼差別。
4. 在 `aba.c` 的 tagged 版本中，把 tag 改成只有 2 位元（取 `& 3`），構造一個讓 T2 修改 top 恰好 4 次的交錯，觀察 ABA 是否重新出現。
5. 把 `race.c` 的 `atomic_long` 改成「`long` + `pthread_mutex_t`」，用 `perf stat` 或 `clock_gettime` 比較 1、2、4、8 個執行緒時 atomic 與 mutex 的耗時。記錄你的結果與機器型號，不要只寫「atomic 比較快」。

## 本章重點整理

- non-blocking 分三級：obstruction-free、lock-free（整體一定有進展）、wait-free（每個執行緒都有進展）；lock-free 買的是進度保證，不保證比較快。
- C11 CAS 失敗時會把目前值寫回 `*expected`；x86 上是 `lock cmpxchg`，`fetch_add` 是 wait-free 的 `lock xadd`。
- 天真 CAS 串列錯在二道 CAS 看的是不同的字；Harris 把刪除意圖標記在被刪節點的 `next` 低位元，讓衝突落在同一個字上。
- 刪除的線性化點是「標記」而不是「摘除」，摘除可以由任何人幫忙完成。
- TSan 抓 data race，抓不到演算法錯誤；x86 的強記憶體模型會掩蓋順序錯誤。
- ABA 與記憶體回收是同一個問題：位址被重用。解法是版本號，或保證還有人持有時不釋放（hazard pointer、EBR、RCU）。

## 自我檢核

- [ ] 不看書，畫出天真 CAS 串列「刪 10、插 20」二種執行順序下 20 怎麼不見的。
- [ ] 用自己的話解釋：為什麼把標記放在被刪節點的 `next` 上就能讓插入者察覺？如果標記放在節點的另一個欄位（例如 `bool deleted`）會怎樣？
- [ ] 面試官問「lock-free 和 wait-free 差在哪？`atomic_fetch_add` 是哪一種？CAS 迴圈是哪一種？」你怎麼回答？
- [ ] 說明為什麼天真版在 TSan 下沒有任何警告，而 `-DRELAXED` 的 Harris 版有。
- [ ] 解釋 hazard pointer 讀取流程中「公告後重新驗證」那一步為什麼不能省。
- [ ] 說出一個你會選 mutex 而不選 lock-free 的情境，並給出理由。

## 延伸閱讀

### 論文

- **[A Pragmatic Implementation of Non-Blocking Linked-Lists](https://www.cl.cam.ac.uk/research/srg/netos/papers/2001-caslists.pdf)** — Timothy L. Harris，DISC（2001）
  - **核心貢獻**：用「先標記、再摘除」解決 CAS 串列中插入與刪除的衝突，標記藏在指標低位元。
  - **讀哪裡**：前半部描述天真版為何失敗的圖，以及演算法本體（`search`、`insert`、`delete` 的虛擬碼），與本章程式幾乎一一對應；正確性論證可先略過。
  - **前提**：本章的 CAS 與 linearizability 概念。
- **[Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects](https://doi.org/10.1109/TPDS.2004.8)** — Maged M. Michael，IEEE TPDS 15(6)（2004）
  - **核心貢獻**：讓 lock-free 結構能安全釋放節點，且記憶體用量有上界。
  - **讀哪裡**：Section 3（演算法）與以串列為例的應用段落。
  - **和本章的關聯**：本章「記憶體回收」一節的 hazard pointer 流程出處。
- **[Wait-free synchronization](https://doi.org/10.1145/114005.102808)** — Maurice Herlihy，ACM TOPLAS 13(1)（1991）
  - **核心貢獻**：定義 wait-free，並以 consensus number 證明 CAS 是「萬用」的同步原語，而 read/write、test-and-set 不是。
  - **讀哪裡**：前三節（定義與 consensus hierarchy）。
  - **前提**：基本的並行概念；證明部分偏理論，可略讀。
- **[Practical lock-freedom](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-579.pdf)** — Keir Fraser，劍橋大學博士論文（2004）
  - **讀哪裡**：epoch-based reclamation 的章節。
  - **和本章的關聯**：EBR 的經典來源，也與 Harris 出自同一個研究群。

### 部落格 / 技術文章

- **[Is Parallel Programming Hard, And, If So, What Can You Do About It?](https://mirrors.edge.kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.html)** — Paul E. McKenney
  - **這本書說什麼**：Linux RCU 作者寫的並行程式設計教科書，免費。
  - **讀哪裡**：「Deferred Processing」一章（hazard pointer、RCU 的比較）與記憶體順序的附錄。
  - **為什麼值得讀**：作者就是核心 RCU 與 `WRITE_ONCE` 那些 commit 的作者。
- **[並行程式設計：Atomics 操作](https://hackmd.io/@sysprog/concurrency-atomics)** — jserv（sysprog）
  - **這篇說什麼**：C11 atomics、記憶體順序與 cache coherence 的關係，大量圖解。
  - **讀哪裡**：memory order 與 CAS 的段落。
- **[ThreadSanitizer 說明文件](https://github.com/google/sanitizers/wiki/ThreadSanitizerCppManual)** — Google sanitizers wiki
  - **讀哪裡**：報告格式說明與已知限制。
  - **注意**：TSan 只偵測實際執行到的交錯中的 data race，不保證找到所有 race。

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：5.1.2.4（多執行緒執行與 data race，§25 是 UB 的條文）、7.17.3（`memory_order`）、7.17.7.4（compare-exchange 的精確語意）、6.5.2.4 §2（atomic 的 `++`）。
  - **注意**：5.1.2.4 的 happens-before 定義很繞，第一次讀先抓「sequenced-before + synchronizes-with」的直覺。
- **[jserv/concurrent-ll](https://github.com/jserv/concurrent-ll)**
  - **讀哪裡**：`src/lockfree/list.c` 與 `include/atomics.h`；README 的 benchmark 說明。

### 書籍

- **《The Art of Multiprocessor Programming》（2nd ed.）** — Maurice Herlihy, Nir Shavit, Victor Luchangco, Michael Spear（Morgan Kaufmann，2020）
  - **這本書的定位**：並行資料結構的標準教科書。
  - **讀哪幾章**：第 3 章（linearizability 與 progress 條件）、第 9 章（Linked Lists: The Role of Locking，從粗粒度 lock 一路講到 lock-free 串列，Harris-Michael 演算法在這章）、第 10 章（queue 與 ABA）。

Part 3 從記憶體階層、對齊、malloc 一路走到串列與 lock-free 結構。我們看過 glibc 怎麼管理 chunk、核心怎麼用侵入式串列與 poison 值、lock-free 結構為什麼不敢 `free`。現在是動手的時候了：把這些組合起來，自己寫一個 malloc。

→ [練習 B：自製 malloc](./practice-b-my-malloc.md)
