# Ch 25 — 讀懂並發程式

> **目標**：學會讀「多個執行緒同時碰同一塊記憶體」的 code。這是所有讀碼類型裡最難的一種——因為控制流不是一條線，而是好幾條線交錯，而交錯的方式在 source 上看不到。讀完你會有一套固定的攻堅步驟（找共享狀態 → 找保護它的同步機制 → 逐一問「這個變數誰在什麼鎖下改」），會用 gdb 的 thread 指令把「凍結的並發狀態」抓出來看，並且對 data race 培養出嗅覺——看到「無鎖存取一個明明被別人改的變數」就會警鈴大作。我們拿 redis 的 `bio.c`（background I/O threads）真跑真讀。

## 為什麼並發最難讀？

前面幾章讀 indirection、狀態機、巨集，難是難在「code 不在你以為的地方」——但至少它是一條確定的執行路徑，你順著追總能追到。並發不一樣，它難的點是**根本沒有「一條路徑」這回事**。

單執行緒的 code，你腦中可以放一個「program counter」，一行一行往下跑，跑到哪、變數是什麼值，都是確定的。多執行緒的 code，你腦中要放的是 N 個 program counter，它們**各自獨立前進，而且前進的相對速度是不確定的**。同一段 source，這次跑是 A 先執行第 5 行、B 才執行第 3 行；下次跑可能反過來。source 上寫的是「一個」`counter++`，但如果兩個執行緒同時執行它，實際發生什麼取決於 CPU 怎麼調度——而這**完全不寫在 source 裡**。

所以讀並發 code 的本質困難是：**你要讀的資訊有一大半不在文字裡**。文字告訴你「每個執行緒各自做什麼」，但「它們怎麼交錯、誰保護誰、哪個 race 是 bug 哪個是 by design」這些最關鍵的東西，是**慣例（convention）與不變式（invariant）**，要嘛寫在註解裡（好專案），要嘛只存在原作者腦中（多數專案）。

這章教的就是「怎麼把那看不見的一半，用固定步驟逼出來」。

## 核心讀法：三步鎖定共享狀態

不管多複雜的並發 code，讀法都收斂到同一個核心動作——**追蹤共享狀態（shared state）**。單執行緒的 bug 藏在控制流，並發的 bug 藏在「多個執行緒同時碰的那幾個變數」。所以：

```
Step 1  找出共享狀態
        ── 哪些變數/資料結構會被一個以上的執行緒讀寫？
        ── 線索：全域變數、static、傳給多個 thread 的指標、
           heap 上被多方持有的結構。

Step 2  找出保護它的同步機制
        ── 每個共享變數，是被哪個 mutex / atomic / rwlock 保護的？
        ── 這個對應關係（變數 ↔ 鎖）幾乎不會寫在型別裡，
           要靠讀存取點反推。

Step 3  逐一質問每個存取點
        ── 對每個「讀或寫共享變數」的地方問：
           「這裡有沒有拿對的鎖？」
        ── 找到「沒拿鎖就碰共享變數」的點 → 不是 bug 就是
           某個你還沒懂的 invariant（例如「只有這條路徑會跑」）。
```

第三步的產物就是你讀懂並發 code 的證據：你能對著任一個共享變數，說出「它被 X 鎖保護，寫它的有 A、B 兩處，讀它的有 C 一處，全都在鎖內——所以安全」。說不出來，就是還沒讀懂。

> 一個關鍵心態：**在並發 code 裡，「這個變數是誰改的」不是次要問題，是唯一重要的問題。** 單執行緒你可以先讀邏輯、有需要再查誰改變數；並發你必須反過來，先把「誰在什麼鎖下改哪個變數」這張表建出來，邏輯才讀得動。

## 真實案例：redis 的 bio.c

redis 給大家的印象是「單執行緒」——它的命令處理確實是單執行緒的（這是它簡單又快的關鍵）。但 redis **不是完全沒有執行緒**：有些操作（關檔案、AOF fsync、釋放大塊記憶體）如果在主執行緒做會 block 住整個伺服器，所以 redis 把它們丟到**背景 I/O 執行緒**（background I/O，簡稱 bio）去做。`src/bio.c` 就是這套機制，它是讀「生產者—消費者 + 條件變數」這個經典並發模式的絕佳教材。

先用 gdb 把它跑起來、看真實的執行緒佈局（這是真跑輸出）。啟動 redis、attach gdb、`info threads`：

```
$ ./src/redis-server --port 7801 --save "" &
$ sudo gdb -q -batch -p $(pgrep -f redis-server) -ex "info threads"
  Id   Target Id                                          Frame
* 1    Thread ... (LWP 46024) "redis-server"    epoll_wait (epfd=8, ...)
  2    Thread ... (LWP 46026) "bio_close_file"  __futex_abstimed_wait_common64 (... futex_word=... <bio_newjob_cond...>)
  3    Thread ... (LWP 46027) "bio_aof"         __futex_abstimed_wait_common64 (... <bio_newjob_cond...>)
  4    Thread ... (LWP 46028) "bio_lazy_free"   __futex_abstimed_wait_common64 (... <bio_newjob_cond...>)
  5    Thread ... (LWP 46029) "jemalloc_bg_thd" __futex_abstimed_wait_common64 (...)
  6    Thread ... (LWP 46030) "jemalloc_bg_thd" __futex_abstimed_wait_common64 (...)
```

這一個畫面就把 redis 的並發真相攤在你面前，比讀十頁 source 都快：

- **Thread 1（主執行緒）** 停在 `epoll_wait`——它就是那個「單執行緒事件迴圈」，正在等網路事件。這是 redis 的心臟。
- **Thread 2/3/4** 是三個 bio 工作執行緒，名字直接告訴你各自幹嘛：`bio_close_file`、`bio_aof`、`bio_lazy_free`。它們現在全都停在 `__futex...`——底層是 `pthread_cond_wait`，也就是**在條件變數上睡覺，等有工作來**。
- **Thread 5/6** 是 jemalloc（記憶體配置器）自己的背景執行緒，跟 redis 邏輯無關，讀 redis code 時可以忽略——但**知道它們存在**很重要，否則你會困惑「怎麼多兩個不認識的執行緒」。

> 這是動態工具的殺手級用途：`info threads` 一秒回答「這程式到底有幾個執行緒、各自叫什麼、現在卡在哪」。這個問題用讀 source 回答要翻遍所有 `pthread_create`，用 gdb 是一行。Ch 18 講 debugger-driven reading，這裡是它在並發場景的具體應用。

### 抓一條 bio 執行緒的完整 backtrace

`bt` 一條 bio 執行緒（真跑輸出，過濾後）：

```
$ sudo gdb -q -batch -p $PID -ex "thread 2" -ex "bt"
#3  __pthread_cond_wait_common (... mutex=... <bio_mutex...>, cond=... <bio_newjob_cond...>)
#4  ___pthread_cond_wait (cond=... <bio_newjob_cond...>, mutex=... <bio_mutex...>)
#5  bioProcessBackgroundJobs (arg=0x0) at .../src/bio.c:281
#6  start_thread (...) at ./nptl/pthread_create.c:442
#7  clone3 () at ...
```

從下往上讀就是這條執行緒的一生：`clone3`（核心層建立執行緒）→ `start_thread`（pthread 進入點）→ `bioProcessBackgroundJobs`（redis 的工作函式，我們要讀的）→ 現在停在 `pthread_cond_wait`，正抱著 `bio_mutex` 在 `bio_newjob_cond` 上等。**這條 backtrace 直接告訴你要去讀 `bio.c:281`**——不用猜。

### 讀 source：把共享狀態表建出來

現在按三步法讀 `bio.c`。先找共享狀態——看檔案頂端的全域宣告：

```c
static pthread_t bio_threads[BIO_WORKER_NUM];
static pthread_mutex_t bio_mutex[BIO_WORKER_NUM];        // 每個 worker 一把鎖
static pthread_cond_t bio_newjob_cond[BIO_WORKER_NUM];   // 每個 worker 一個條件變數
static list *bio_jobs[BIO_WORKER_NUM];                   // 每個 worker 一條工作佇列
static unsigned long bio_jobs_counter[BIO_NUM_OPS] = {0};
```

看到 `mutex` 和 `cond` 就知道這裡有同步。看到它們跟 `bio_jobs`（工作佇列）並排宣告，強烈暗示**這把鎖保護的就是這條佇列**——這是慣例：鎖跟它保護的資料放一起。但這只是假設，要去驗證。

**驗證**：找所有碰 `bio_jobs` 的地方，看是不是每次都在 `bio_mutex` 下。生產者端 `bioSubmitJob`（真實 source）：

```c
void bioSubmitJob(int type, bio_job *job) {
    job->header.type = type;
    unsigned long worker = bio_job_to_worker[type];
    pthread_mutex_lock(&bio_mutex[worker]);        // ← 先拿鎖
    listAddNodeTail(bio_jobs[worker],job);         // ← 改共享佇列
    bio_jobs_counter[type]++;                       // ← 改共享計數器
    pthread_cond_signal(&bio_newjob_cond[worker]); // ← 叫醒睡著的 worker
    pthread_mutex_unlock(&bio_mutex[worker]);      // ← 放鎖
}
```

生產者拿鎖 → 把 job 塞進佇列 → **signal 條件變數**（喚醒等在上面的消費者）→ 放鎖。標準的生產者。

消費者端 `bioProcessBackgroundJobs`（就是 gdb 停住的那個函式）的迴圈骨架（真實 source，省略處理細節）：

```c
pthread_mutex_lock(&bio_mutex[worker]);   // 進迴圈前先拿鎖
while(1) {
    listNode *ln;
    /* The loop always starts with the lock hold. */
    if (listLength(bio_jobs[worker]) == 0) {
        pthread_cond_wait(&bio_newjob_cond[worker], &bio_mutex[worker]); // ← 沒工作就睡
        continue;
    }
    ln = listFirst(bio_jobs[worker]);     // 拿出一個 job（還在鎖內）
    job = ln->value;
    pthread_mutex_unlock(&bio_mutex[worker]); // ← 放鎖！再去處理 job

    /* ... 處理 job（close/fsync/free），這段不持鎖 ... */

    pthread_mutex_lock(&bio_mutex[worker]);   // ← 重新拿鎖
    listDelNode(bio_jobs[worker], ln);        // 從佇列移除已處理的 job
    bio_jobs_counter[job_type]--;
    pthread_cond_signal(&bio_newjob_cond[worker]);
}
```

這段有幾個讀並發時該立刻抓到的點：

1. **`pthread_cond_wait` 會原子地放鎖並睡著**，被喚醒時又原子地重新拿鎖。這是條件變數的鐵律——`cond_wait` 必須配一把 mutex，而且呼叫前你必須持有那把 mutex。gdb backtrace 裡看到執行緒卡在 `pthread_cond_wait` + `bio_mutex`，就是這個狀態的定格。

2. **為什麼是判斷佇列長度再睡，而不是醒來直接做事？** 這裡用 `if (empty) { cond_wait; continue; }`，`continue` 讓它回到 `while(1)` 開頭**重新檢查**佇列——效果等同 while 迴圈重檢條件。這防的是「假喚醒」（spurious wakeup）和「多消費者搶同一個 job」。讀到 `cond_wait` 一定要問：**醒來後有沒有重新檢查條件？** 沒有的話（醒來直接做事、不重檢）就是經典 bug。

3. **處理 job 的那段刻意不持鎖**：拿到 job 指標後就 `unlock`，因為 close/fsync 可能很慢，持鎖做會擋住生產者。這叫「縮小臨界區」（minimize critical section）。讀到「拿了資料就放鎖、處理完再拿回來」的模式，作者是在用鎖換吞吐。

到這裡，共享狀態表就建好了：

| 共享狀態 | 保護它的鎖 | 誰寫 | 誰讀 |
|---|---|---|---|
| `bio_jobs[w]`（佇列） | `bio_mutex[w]` | 生產者 `bioSubmitJob`（尾端加）、消費者（頭端刪） | 消費者迴圈 |
| `bio_jobs_counter[t]` | `bio_mutex[w]` | 生產者 `++`、消費者 `--` | `bioPendingJobsOfType`（也在鎖內） |
| `bio_newjob_cond[w]` | 配 `bio_mutex[w]` | signal：生產者與消費者 | wait：消費者 |

這張表就是你「讀懂了 bio.c 並發部分」的證明。**每個共享變數都被同一把鎖保護，每個存取點都在鎖內**——所以沒有 data race。

## atomic 與 memory ordering（這裡開始是簡化心智模型）

bio.c 裡有一段不用鎖、卻在多執行緒間傳值（真實 source）：

```c
atomicSet(server.aof_bio_fsync_status, C_ERR);
atomicSet(server.aof_bio_fsync_errno, errno);
// ...同檔他處讀取：
int last_status;
atomicGet(server.aof_bio_fsync_status, last_status);
```

bio 執行緒寫 `aof_bio_fsync_status`，別處讀它，兩邊**沒有共用的鎖**。為什麼這樣安全？因為用了 atomic。看 `atomicvar.h` 怎麼定義（真實 source）：

```c
#define atomicIncr(var,count) atomic_fetch_add_explicit(&var,(count),memory_order_relaxed)
#define atomicGet(var,dstvar)  dstvar = atomic_load_explicit(&var,memory_order_relaxed)
#define atomicSet(var,value)   atomic_store_explicit(&var,value,memory_order_relaxed)
// 另有帶 sync 的版本：
#define atomicGetWithSync(var,dstvar) dstvar = atomic_load_explicit(&var,memory_order_seq_cst)
#define atomicSetWithSync(var,value)  atomic_store_explicit(&var,value,memory_order_seq_cst)
```

兩件事要讀懂：

**（1）atomic 保證的是「不可分割」。** `atomic_store`/`atomic_load` 保證這個變數的讀寫是一次完成的，不會讀到「寫到一半」的中間值（一個執行緒寫 8 bytes 時，另一個不會讀到前 4 bytes 新、後 4 bytes 舊）。所以單一變數在多執行緒間傳遞，用 atomic 就不需要鎖。這是它比鎖輕量的地方。

**（2）memory_order 那個參數，才是真正燒腦、也最容易讀錯的地方。**

> **這裡我必須誠實標注：以下是簡化心智模型。** memory ordering 的完整語義（C11/C++11 記憶體模型）是整個程式語言標準裡最難的部分之一，涉及編譯器重排、CPU 亂序執行、cache coherence、happens-before 關係的形式化定義。下面給的是「讀 code 時夠用的直覺」，不是嚴格定義。要嚴格請讀本章延伸閱讀的 cppreference 與 Preshing 系列，並且知道：**大部分人（包括很多資深工程師）對 memory ordering 的理解都是不完整的。你讀到用 relaxed/acquire/release 的 code 時，保守假設「原作者知道自己在幹嘛」，但也要有「這裡可能有微妙 bug」的警覺。**

直覺版本：

- **`memory_order_relaxed`（relaxed）**：只保證「這個變數本身的操作是原子的」，**不保證它跟其他變數的先後順序**。編譯器和 CPU 可以把它前後的其他記憶體操作重排。適合「純計數器」——你只在乎最後的數值對，不在乎它跟別的變數誰先誰後。redis 的 `atomicIncr`（統計計數）用 relaxed 就是這個道理。

- **`memory_order_release`（release，寫端用）+ `memory_order_acquire`（acquire，讀端用）**：這是一對。直覺是「**釋放—獲取建立一道記憶體屏障**」：如果執行緒 A 在 release 寫變數 X 之前做的所有寫入，執行緒 B 用 acquire 讀到 X 的新值之後，**都保證看得到**。這是「用一個 flag 變數傳遞一整批資料」的正確做法——寫端把資料填好、最後 release 寫 `flag=ready`，讀端 acquire 看到 `flag=ready`，就保證前面那批資料也全都可見。

- **`memory_order_seq_cst`（sequential consistency，seq_cst）**：最強、最直覺、最慢。它保證「所有執行緒看到的所有 seq_cst 操作，是同一個全域順序」。這最接近你腦中「單一時間軸」的天真模型。redis 的 `atomicSetWithSync`/`atomicGetWithSync` 用它。**讀 code 時，看到 seq_cst 你可以放心用天真的順序直覺；看到 relaxed 你必須警惕「順序沒保證」。**

讀 atomic code 的實戰建議：**先看 memory order 參數，它直接告訴你原作者對「順序保證」的需求強度。** relaxed = 「我只要數對，順序無所謂」；acquire/release = 「我在用 flag 傳一批資料，順序關鍵」；seq_cst = 「我要最強保證，別想太多」。看不懂細節沒關係，但至少從 order 讀出「作者在小心什麼」。

## data race 的嗅覺

讀並發 code 最該練的直覺，是聞到 **data race**——「兩個以上執行緒同時存取同一記憶體，至少一個是寫，且沒有同步」。data race 是 UB（undefined behavior），是並發 bug 的頭號來源。培養嗅覺的三個觸發點：

1. **看到一個變數，卻沒看到保護它的鎖。** 讀到某函式改一個全域/static/共享 heap 變數，你的反射動作應該是「這函式會被幾個執行緒呼叫？改這變數時有沒有拿鎖？」如果它會被多執行緒呼叫、又沒拿鎖、又不是 atomic——警鈴。

2. **「快取一份到 local 變數」的讀取，在迴圈裡。** 像 `while (!done) { ... }` 這種讀共享 flag 的迴圈，如果 `done` 不是 atomic/volatile，編譯器可能把它讀進暫存器、之後不再重讀，於是別的執行緒改了 `done` 這邊永遠看不到（無窮迴圈）。看到「多執行緒共享的 flag 用普通變數」要警惕。

3. **「檢查—再動作」中間沒鎖（check-then-act）。** `if (queue not empty) { pop(); }` 如果 check 和 pop 之間沒持鎖，另一個執行緒可能在中間把 queue 清空，於是你 pop 一個不存在的東西。這是為什麼 bio.c 的消費者拿 job 是**在鎖內**做 `listFirst`。

工具面：**ThreadSanitizer（TSan）** 是自動抓 data race 的利器。概念上它在每個記憶體存取插樁，記錄「哪個執行緒、在什麼同步狀態下、碰了哪個位址」，發現兩個執行緒對同一位址的存取之間沒有 happens-before 關係就報 race。編譯時加 `-fsanitize=thread` 即可。讀陌生並發 code 時，如果能編譯它，跑一次 TSan 常常比你肉眼讀三小時更快找到問題點——它會直接告訴你「執行緒 A 在 foo.c:12 寫、執行緒 B 在 bar.c:34 讀，同一位址，無同步」。

> TSan 的限制要知道：它只報**實際跑到的** race（動態工具，沒跑到的路徑抓不到），而且有效能開銷（5-15 倍）。它是強力補充，不是萬能。redis 的 bio 因為鎖用得乾淨，TSan 跑起來是安靜的——這反過來也是一種閱讀確認。

## 對比與取捨

讀到不同的同步原語，先分辨它是哪一類、解決什麼問題：

| 同步原語 | 一句話語義 | 讀到它代表 | 常見坑 |
|---|---|---|---|
| `mutex`（互斥鎖） | 一次只有一個執行緒進臨界區 | 有一段「不能被多執行緒同時執行」的 code | 忘記解鎖、鎖順序不一致 → deadlock |
| `rwlock`（讀寫鎖） | 多讀者可並行，寫者獨佔 | 讀多寫少的共享資料 | 寫者飢餓、比 mutex 重 |
| `cond var`（條件變數） | 在某條件成立前睡著、成立時被喚醒 | 生產者—消費者、等待某狀態 | 必須配 mutex、必須 loop 重檢條件 |
| `atomic` | 單一變數的不可分割讀寫 | 輕量計數器 / flag，不想用鎖 | memory order 用錯、以為 atomic 就沒 race |
| `lock-free`（無鎖，通常靠 CAS） | 用 compare-and-swap 迴圈取代鎖 | 極致效能 / 不能睡的場景（訊號處理、核心） | 極難讀、ABA 問題、幾乎必有微妙 bug |
| `thread pool`（執行緒池） | 固定 N 個 worker 從佇列取工作 | 大量短任務、控制並行度 | 就是 bio.c 這個模式 |

**mutex vs lock-free 的閱讀成本天差地遠。** 讀 mutex 保護的 code，你只要問「臨界區內是什麼、鎖對不對」，臨界區內可以當單執行緒讀。讀 lock-free（一堆 `compare_exchange` 迴圈、`acquire`/`release`），你要在腦中同時模擬多個執行緒的交錯，還要懂 memory ordering——難度是數量級的差別。所以看到 lock-free：**慢下來，別假裝讀懂了**，這種 code 連原作者都常寫錯。

## 踩雷集錦

1. **錯誤直覺：「redis 是單執行緒，所以沒有並發問題」→ 正確：redis 的命令處理是單執行緒，但它有 bio 執行緒、（新版）IO 執行緒、jemalloc 背景執行緒。** gdb `info threads` 一跑就看到六條執行緒。「主邏輯單執行緒」不等於「整個程式單執行緒」。讀任何「號稱單執行緒」的程式，先 `info threads` 驗證。

2. **錯誤直覺：「這個變數是 atomic，所以這段 code 沒有 race」→ 正確：atomic 只保證單一變數的單次操作原子，不保證跨多個變數/多個操作的複合邏輯正確。** `if (atomic_x > 0) atomic_x--;` 這種 check-then-act，即使 x 是 atomic，兩個執行緒也可能都通過檢查然後各減一次，減成負的。atomic 不是萬靈丹。

3. **錯誤直覺：「`cond_wait` 醒來就代表條件成立了」→ 正確：必須醒來後重新檢查條件（loop，不是醒來直接做事）。** 假喚醒（spurious wakeup）和多消費者競爭都會讓你「被喚醒但條件其實不成立」。bio.c 用 `if (empty) { wait; continue; }` 回到迴圈頭重檢，就是防這個。看到 `cond_wait` 醒來不重檢條件，八成是 bug。

4. **錯誤直覺：「source 上這兩行是連續的，執行時就是連續的」→ 正確：多執行緒下，任意兩行之間都可能被別的執行緒插進來執行。** 讀並發 code 不能用「一行接一行」的單執行緒直覺。要問的是「這兩行之間，別的執行緒可能改了什麼？」

5. **錯誤直覺：「沒鎖但跑起來都正常，所以沒問題」→ 正確：race 是機率性的、跟時序/負載/CPU 數相關，測試沒觸發不代表沒有。** 並發 bug 惡名昭彰的原因就是它「大部分時候正常」。讀 code 時的靜態推理（誰改這變數、有沒有同步）比「我跑過沒事」可靠得多。這也是為什麼要練靜態的「共享狀態表」而不只靠跑。

## 進階：再往深一層

- **鎖順序與 deadlock**：多把鎖時，如果不同執行緒以不同順序拿鎖（A 拿 lock1 再拿 lock2，B 拿 lock2 再拿 lock1），就可能互相等 → deadlock。讀多鎖 code 時，建一張「這函式依序拿哪幾把鎖」的表，檢查全專案是否鎖順序一致。bio.c 每個 worker 只有一把鎖，天然無此問題——這也是它把鎖切成 per-worker 的好處之一。

- **`bio_mutex_comp` 與跨執行緒喚醒主迴圈**：bio.c 還有第二把鎖 `bio_mutex_comp` 保護 completion 佇列，而背景執行緒完成工作後要通知**主執行緒**（它卡在 `epoll_wait`），用的是「寫一個 byte 到 pipe」——`write(job_comp_pipe[1],"A",1)`。主執行緒的事件迴圈監聽這個 pipe 的可讀事件而被喚醒。這是「用 self-pipe trick 把執行緒的完成事件接進 event loop」的經典手法，讀 event-loop + 背景執行緒混合架構必懂。這一段值得你自己順著 `job_comp_pipe` 追一遍。

- **記憶體模型的形式化**：如果你要讀 lock-free code（V8、Linux kernel 的 RCU、無鎖佇列），簡化心智模型不夠用。得補 C11/C++11 記憶體模型的形式定義：happens-before、sequenced-before、synchronizes-with、release sequence。這是硬骨頭，但讀 kernel 的 `smp_rmb`/`smp_wmb`/`READ_ONCE`/`WRITE_ONCE` 沒有它讀不動。接你的 kernel_internals 課的 RCU 章。

- **volatile 不是同步原語**：C/C++ 的 `volatile` 只保證「不被優化掉、每次真的讀記憶體」，**不保證原子性、不保證 memory ordering**。它是給 MMIO（記憶體映射 I/O）和 signal handler 用的，不是給多執行緒同步用的。看到有人用 `volatile int flag` 做執行緒間 flag，那是錯的（該用 atomic）——這是超常見的誤用，讀到要打問號。（Ch 28 會再談 volatile 對編譯器的意義。）

## 動手練習

1. **重現 gdb 的 `info threads`**：啟動 redis（`./src/redis-server --port 7801 --save ""`），用 `sudo gdb -p $(pgrep -f redis-server) -ex "info threads"` 看六條執行緒。認出主執行緒（`epoll_wait`）和三條 bio 執行緒。這是本章最核心的動手體驗。

2. **抓 bio 執行緒的 backtrace**：`thread apply all bt`，找到停在 `bioProcessBackgroundJobs` + `pthread_cond_wait` 的那三條。確認 backtrace 的行號指向 `bio.c` 的 `cond_wait` 呼叫。

3. **觸發一個 bio 工作**：連上 redis（`redis-cli -p 7801`），開啟 AOF（`config set appendonly yes`），再觀察 `bio_aof` 執行緒。進階：在 `bioProcessBackgroundJobs` 下斷點，看 job 真的被消費。

4. **建共享狀態表**：不看本章的表，自己讀一遍 `bioSubmitJob` 和 `bioProcessBackgroundJobs`，列出所有共享變數、各自的鎖、讀寫點。跟本章的表對照。

5. **（選）跑 TSan**：找一個小的多執行緒 C 程式（或自己寫一個故意有 race 的），`gcc -fsanitize=thread -g race.c && ./a.out`，看 TSan 怎麼報 race。體會它比肉眼快在哪。

## 本章重點整理

- 並發最難讀，因為「執行緒怎麼交錯」不寫在 source 裡——那一半資訊在慣例與 invariant。
- 核心讀法三步：找共享狀態 → 找保護它的同步機制 → 逐一質問每個存取點有沒有拿對的鎖。產物是一張「變數 ↔ 鎖 ↔ 讀寫點」的表。
- gdb `info threads` + `thread apply all bt` 一秒攤開「幾條執行緒、各卡在哪」，是並發讀碼的第一動作。
- redis 命令處理單執行緒，但有 bio 執行緒跑 close/fsync/lazyfree，是「生產者—消費者 + 條件變數」的教科書範例。
- `cond_wait` 必配 mutex、醒來必重檢條件；縮小臨界區（拿了資料就放鎖）是常見手法。
- atomic 保證單變數操作不可分割，但 memory order 語義極難（本章是簡化心智模型）：relaxed=只要數對、acquire/release=用 flag 傳批資料、seq_cst=最強最直覺。
- data race 嗅覺：無鎖碰共享變數、迴圈讀非 atomic flag、check-then-act 中間沒鎖。TSan 是自動抓 race 的利器。

## 自我檢核

- [ ] 拿到一個多執行緒程式，我能不能一口氣說出「共享狀態三步讀法」，並實際對 bio.c 建出那張表？
- [ ] 我能不能解釋為什麼 `pthread_cond_wait` 一定要配一把 mutex、醒來為什麼要重檢條件？
- [ ] 面試官問「atomic 變數是不是就沒有 race 了」，我能不能舉出 check-then-act 的反例？
- [ ] 我能不能誠實說出 memory_order relaxed / acquire-release / seq_cst 各自的直覺，並承認完整語義的難度？
- [ ] 看到一段「多執行緒共享、卻沒拿鎖也不是 atomic」的變數存取，我的警鈴會不會響？
- [ ] 我知道為什麼「跑起來正常」不能證明並發 code 沒 bug 嗎？

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

- **[cppreference: `std::memory_order`](https://en.cppreference.com/w/cpp/atomic/memory_order)**
  - **讀哪裡**：先看頁面上方 relaxed / release-acquire / seq_cst 三段的直覺說明與範例，那幾個 producer-consumer 範例是理解 release/acquire 的最短路徑。formal 定義（release sequence、modification order）當字典查。
  - **學到什麼**：本章「簡化心智模型」的嚴格版。看完你會知道自己剛剛學的直覺在哪裡不夠精確。
  - **前提**：懂 atomic 的基本概念；C++ 背景更好，但 C11 的 `atomic_*_explicit` 語義相同。

- **[Preshing on Programming: "An Introduction to Lock-Free Programming" 系列](https://preshing.com/20120612/an-introduction-to-lock-free-programming/)**
  - **讀哪裡**：從這篇入門開始，接著讀 "Memory Ordering at Compile Time"、"Acquire and Release Semantics"、"The Happens-Before Relation" 幾篇。
  - **學到什麼**：memory ordering 與 lock-free 最好懂的一套圖解教學，把 CPU 重排、編譯器重排、屏障講成人話。讀完本章想再往 lock-free 深挖，這是最佳橋樑。
  - **前提**：本章讀完；有基本的 CPU/cache 概念更順。

- **[Redis `bio.c` 原始碼與其頂端 DESIGN 註解](https://github.com/redis/redis/blob/7.4.0/src/bio.c)**
  - **讀哪裡**：檔案頂端的 `DESIGN` 大段註解，講清楚「每個 job type 綁一個 worker、FIFO 處理、completion 用回寫佇列 + pipe 通知主執行緒」的整體設計。然後對照本章讀 `bioSubmitJob` / `bioProcessBackgroundJobs`。
  - **學到什麼**：一個乾淨的生產者—消費者 + event-loop 通知的真實工業實作。註解品質高，是「好並發 code 長什麼樣」的範本。
  - **前提**：本章 + 基本 pthread API。

- **[ThreadSanitizer 手冊（Clang 文件）](https://clang.llvm.org/docs/ThreadSanitizerManual.html)**
  - **讀哪裡**："Introduction" 與 "Usage"，了解怎麼編譯、報告怎麼讀、有哪些限制（只抓跑到的路徑、有開銷）。
  - **學到什麼**：把「抓 data race」從肉眼推理升級成自動工具。讀陌生並發 code 時的強力外掛。
  - **前提**：會用 clang/gcc 編譯；懂 data race 的定義（本章有）。

讀懂並發，你已經攻克了讀碼裡最硬的一種控制流。下一章換一種「難」——不是控制流交錯，而是**code 根本不在你以為的地方**：C++ 的 template、RAII、operator overloading 會把大量邏輯藏在編譯器替你生成的、source 上看不見的地方。我們同樣真編譯、真 objdump，把藏起來的 code 挖出來。

→ [Ch 26 讀懂 C++ 的複雜性](./26-reading-cpp-complexity.md)
