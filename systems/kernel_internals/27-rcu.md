# Ch 27 — RCU 深入

> **目標**：理解 RCU（Read-Copy-Update）為什麼能讓讀者「幾乎零成本、無鎖、不寫任何共享狀態」就安全地遍歷會被並行修改的資料結構；讀懂 grace period 這個核心概念、tree RCU 怎麼偵測它、`rcu_dereference`/`rcu_assign_pointer` 兩道 barrier 的作用；動手寫一個 RCU 保護的串列模組並對比 rwlock 版。

RCU 是整個 kernel 裡最反直覺的同步機制。前三章（Ch 24 atomic/barrier、Ch 25 spinlock/rwlock、Ch 26 mutex）你學的都還是「某種鎖」——讀者也好、寫者也好，總得在某個 cache line 上做一次 atomic 操作宣告「我進來了」。RCU 打破這個假設：**讀者路徑上一個 atomic 操作都沒有、一個共享 cache line 都不寫**。第一次聽到會覺得這不可能是對的。這章我們把它拆到你能自己畫出「舊版本什麼時候能安全 free」的時間線為止。

## 為什麼需要這個？

先看一個具體場景：核心的路由表。收每一個封包都要查一次路由表（讀），但路由表本身很少變（寫）——可能幾秒、幾分鐘才因為一條路由更新改一次。這是典型的**讀多寫極少**（read-mostly）。同類場景在 kernel 裡到處都是：

- **VFS 的 dentry cache**（Ch 33）：每次路徑解析都在讀 dentry，但檔案很少被 rename/unlink
- **模組列表**：每次符號解析、每次 backtrace 都在讀已載入模組的列表，但 `insmod`/`rmmod` 極少發生
- **網路裝置列表、netfilter 規則、SELinux policy、cgroup 階層**……全是 read-mostly

用 Ch 25 的 rwlock 行不行？表面上可以——多個讀者可以同時持有 read lock。但問題在**擴展性**。回想 Ch 23 講的 cache line bouncing：rwlock 內部有一個計數器記錄「現在有幾個讀者」，每個讀者進臨界區要 `atomic_inc` 這個計數器、出來要 `atomic_dec`。這兩個 atomic 操作**都要寫同一個 cache line**。

於是災難發生了：64 核同時查路由表，每個核都在對 rwlock 那一個 cache line 做 atomic RMW。這個 cache line 在 64 個核的 L1 之間來回彈射（bouncing），MESI 協定讓它永遠處於「某一核 Modified、其他核 Invalid」的爭搶狀態。**讀者之間明明沒有邏輯衝突（大家都只是讀），卻在硬體層面互相拖死。** 核越多越慘，這是 rwlock 在 read-mostly 場景下的根本缺陷。

RCU 的洞見是：如果讀者**根本不寫任何共享狀態**，就沒有 cache line 可以 bounce。讀者路徑上零 atomic、零共享寫入，於是讀端的擴展性是**完美線性**的——加再多核，讀者之間零干擾。代價全部轉嫁到寫者身上（寫者變複雜、要延後釋放記憶體），但因為寫極少，這筆帳非常划算。

> 一句話總結取捨：**RCU 用「寫者變麻煩 + 讀者可能看到舊版本」換來「讀者零成本、完美擴展」。** 只有在讀遠多於寫、且能容忍讀者短暫看到舊資料時才適用。

## 先建立直覺

RCU 的名字 Read-Copy-Update 就是它的三段式心法。假設我們要「原地」修改一個被大家讀的物件，RCU 說：不要原地改。

1. **Read**：讀者照常讀「現在這個版本」。
2. **Copy**：寫者要改時，先**複製**一份，改副本。
3. **Update**：改完，用一個原子的指標賦值把「當前版本」的指標指向新副本。

關鍵在第 3 步之後：原本指向舊版本的讀者怎麼辦？RCU 不立刻 free 舊版本，而是**等到所有正在讀舊版本的讀者都離開之後**，才 free 它。這個「等所有現有讀者離開」的時刻，就是 grace period（寬限期），是這章的靈魂。

用一個類比：舊版本像一間正在營業的店，你想拆它蓋新店。RCU 的做法不是把客人趕出去（那要鎖、要協調），而是：先在旁邊蓋好新店，把招牌（指標）換成指向新店，然後**等舊店裡最後一個客人自己走出來**，才拆舊店。新客人看到招牌都進新店了；只有招牌換之前就已經在舊店的客人，還會待在舊店一陣子——但他們遲早會走。你要做的只是「等」。

三步的指標操作畫出來：

```
  初始狀態：全域指標 gp 指向舊物件 A
  
       gp ──────────────► ┌─────────┐
                          │  A (舊) │  ← 讀者 R1, R2 正在讀 A
                          └─────────┘

  Step 1 (Copy)：寫者 malloc B，把 A 的內容複製過去、改好
  
       gp ──────────────► ┌─────────┐      ┌─────────┐
                          │  A (舊) │      │  B (新) │  ← 還沒人看得到 B
                          └─────────┘      └─────────┘

  Step 2 (Update)：rcu_assign_pointer(gp, B) —— 原子換指標
  
       gp ──────┐         ┌─────────┐      ┌─────────┐
                └───────► │  A (舊) │      │  B (新) │◄── gp 現在指這裡
                          └─────────┘      └─────────┘
                            ▲
                            └─ R1, R2 手上還握著 A 的指標！新讀者 R3 會拿到 B

  Step 3 (等 grace period + free)：等 R1, R2 都離開讀臨界區，才 kfree(A)
```

`rcu_assign_pointer` 是一個**發布（publish）**動作，`rcu_dereference` 是對應的**訂閱（subscribe）**動作。這兩個名字比 assign/dereference 更能抓到本質：寫者「發布」一個初始化完成的物件，讀者「訂閱」到它，中間有 memory barrier 保證讀者看到的一定是初始化**完整**的物件（下面「底層機制」會拆這道 barrier）。

## RCU 的四個 API 骨架

不管多複雜，經典 RCU 就四個核心原語。先把它們的角色分清楚：

| API | 誰用 | 做什麼 |
|---|---|---|
| `rcu_read_lock()` / `rcu_read_unlock()` | 讀者 | 標出讀臨界區的頭尾。**不是真的鎖**——在非搶佔式 RCU 下幾乎是 no-op（下面解釋） |
| `rcu_dereference(p)` | 讀者 | 在讀臨界區內安全地把 RCU 指標載入本地變數，帶一道 barrier |
| `rcu_assign_pointer(p, v)` | 寫者 | 帶一道 release barrier 地發布新指標 |
| `synchronize_rcu()` / `call_rcu()` | 寫者 | 等 grace period（同步阻塞 / 非同步回呼），過了才 free 舊版本 |

讀者端的 code 長這樣（`include/linux/rcupdate.h`）：

```c
rcu_read_lock();
p = rcu_dereference(global_ptr);   // 安全取指標
if (p)
    do_something(p->field);        // 只能在 lock/unlock 之間用 p
rcu_read_unlock();
// 出了 unlock 之後，p 可能已被寫者標記為待釋放，不能再碰
```

寫者端：

```c
struct foo *new = kmalloc(sizeof(*new), GFP_KERNEL);
*new = *old;                       // Copy
new->field = new_value;            // Update（改副本）
rcu_assign_pointer(global_ptr, new);  // 發布
synchronize_rcu();                 // 等所有現有讀者離開
kfree(old);                        // 現在安全了
```

> **RCU 不保護寫寫。** 上面寫者端如果有多個 writer 並行，它們對 `global_ptr` 的 copy-update-assign 會互相踩。RCU 只解決「讀者 vs 寫者」的協調（讀者不必等寫者、寫者不必等讀者），**寫者之間仍要自己用 spinlock/mutex 互斥**。這是最容易誤解的一點。

## 底層機制：grace period 到底是什麼

grace period 是「從某個時間點開始，等到**所有在該時間點之前就已進入讀臨界區的讀者**都離開」的那段時間。理解它要盯住一件事：**寫者不需要知道有哪些讀者、也不需要讀者主動通報**。寫者只要能確定「不可能還有任何讀者握著舊指標」就夠了。

畫成時間線最清楚。橫軸是時間，`[===]` 表示一個讀者在讀臨界區內：

```
  時間 ───────────────────────────────────────────────────►

  寫者:              rcu_assign_pointer(gp, B)          kfree(A) 安全
                     │ 舊指標 A 從此不再被發布           │
                     ▼                                  ▼
                 ────┼──────────── grace period ────────┼────
                     │                                  │
  讀者 R1: [====]    │       R1 早就離開，與 A 無關
                     │
  讀者 R2:    [======┼===]   R2 跨越 assign！它可能還握著 A
                     │   ▲
                     │   └─ R2 必須在這裡之前離開，GP 才算過
  讀者 R3:           │  [=====]  R3 在 assign 後才進來，只會拿到 B，與 A 無關
                     │
  讀者 R4:           │              [====]  同 R3，拿到 B
```

關鍵判讀：

- **R1** 在 assign 之前就離開了，它讀的是 A 但已經走了，無所謂。
- **R3、R4** 在 assign 之後才進來，`rcu_dereference` 拿到的是 B，永遠碰不到 A。
- **只有 R2** 是危險的——它在 assign 那一刻**正握著 A 的指標**。grace period 的定義就是「等到 R2（以及所有像 R2 這樣跨越 assign 的讀者）都離開」。R2 一離開，就保證**沒有任何人**還握著 A，此時 `kfree(A)` 才安全。

所以 grace period 不是一段固定時長，而是「等到所有**當前**讀臨界區都結束一次」。注意是「當前」——GP 開始**之後**才進入的讀臨界區（R3/R4）不算數，因為它們拿不到舊指標。這就是為什麼 RCU 能運作而不需要追蹤個別讀者：寫者只要等「一輪讀者換血」完成即可。

### grace period 怎麼被偵測：quiescent state

問題來了：kernel 怎麼知道「所有當前讀臨界區都結束了」？經典 RCU 的答案漂亮得驚人，前提是**經典（非搶佔）RCU 的讀臨界區裡不能睡、也不能被搶佔**。

`rcu_read_lock()` 在 `CONFIG_PREEMPT_RCU` 關閉時，實際上等同於 `preempt_disable()`——它只是關掉搶佔（見 `include/linux/rcupdate.h` 的 `__rcu_read_lock`）。這意味著：**只要一顆 CPU 發生了 context switch，就證明它上面沒有任何 RCU 讀臨界區在進行中**（因為讀臨界區關了搶佔，不可能被切走）。

這個「CPU 經過了一個不在讀臨界區的時刻」叫 **quiescent state（靜止狀態）**。經典 RCU 偵測 grace period 的邏輯就是：

> 從 grace period 開始的那一刻起，**等每一顆 CPU 都至少經過一次 quiescent state**（context switch、idle、或返回 user space）。當最後一顆 CPU 也報告 quiescent 了，grace period 就結束。

為什麼這成立？因為一顆 CPU 若在 GP 開始時正跑著某個讀臨界區（像 R2），它下一次 context switch 之前必然會先跑完那個讀臨界區（讀臨界區內不能被切走）。所以「該 CPU 經過一次 quiescent state」⟹「該 CPU 上 GP 開始時的那個讀臨界區已經結束」。每顆 CPU 都經過一次，就等於所有 GP 開始時存在的讀臨界區都結束了——正好是 grace period 的定義。

畫出來：

```
  GP 開始
     │
  CPU0: [讀臨界區]──── ctx switch ★ ──────────  (★ = quiescent state)
     │
  CPU1: ────── idle ★ ─────────────────────────
     │
  CPU2: ──[讀臨界區]────────── ctx switch ★ ───  (最後一個報告)
     │                                    │
     └────────── grace period ───────────┘ ← 全部 CPU 都 ★ 過，GP 結束
```

這個設計的美在於：**讀者端完全不參與 GP 偵測**。讀者不寫計數器、不通報、不做任何 atomic。GP 的偵測完全靠「CPU 反正會發生的 context switch」這個副產品免費完成。讀端零成本就是這麼來的。

### tree RCU：不讓所有 CPU 搶一個鎖

上面「等每顆 CPU 報告 quiescent」聽起來簡單，但實作上有個擴展性陷阱：如果用一個全域的 bitmap 記錄「哪些 CPU 還沒報告」，那 256 顆 CPU 都要去更新同一個 bitmap、搶同一個鎖——又回到 cache line bouncing 了，只是這次在 GP 偵測路徑上。

現代 kernel 的預設實作是 **tree RCU**（`kernel/rcu/tree.c`，`CONFIG_TREE_RCU`），用一棵**階層樹**解決。CPU 被分組，每組有一個 `struct rcu_node`（見 `kernel/rcu/tree.h`），組內 CPU 只更新自己那個節點的 mask；當一個 `rcu_node` 的所有 CPU 都報告 quiescent，才往上一層回報給父節點；一路匯集到根節點 `rcu_state`，根節點確認所有子樹都靜止了，GP 才結束。

```
                    ┌── rcu_state (根) ──┐   ← GP 由這裡開始/結束
                    │   等兩個子節點回報   │
                    └────────┬───────────┘
                   ┌─────────┴─────────┐
              ┌ rcu_node ┐        ┌ rcu_node ┐   ← 中層，匯集
              │ CPU 0-15 │        │ CPU 16-31│
              └────┬─────┘        └────┬─────┘
              ┌────┴────┐         ┌────┴────┐
           CPU0 … CPU15         CPU16 … CPU31  ← 各自更新自己節點的 mask
```

好處：任一時刻只有「同一個 `rcu_node` 底下的少數 CPU」會爭同一把鎖，爭用被局部化，樹高 log(N) 讓匯集成本隨 CPU 數只慢慢增長。這就是名字 tree RCU 的由來。每顆 CPU 的 per-CPU 狀態放在 `struct rcu_data`（`kernel/rcu/tree.h`），GP 的推進由 per-node 的 kthread（`rcu_gp_kthread`）驅動。

> **給要讀源碼的你**：GP 的核心狀態機在 `kernel/rcu/tree.c` 的 `rcu_gp_init()`（開一個新 GP）、`rcu_gp_fqs()`（force quiescent state，催促遲遲不報告的 CPU）、`rcu_gp_cleanup()`（收尾）。`synchronize_rcu()` 本身在 `kernel/rcu/tree.c`，內部走 `synchronize_rcu_normal` → 排一個 GP 然後睡等。`call_rcu()` 也在同檔，把 callback 掛進該 CPU 的 `rcu_data->cblist`，等 GP 過了由 softirq（`RCU_SOFTIRQ`，Ch 30）批次執行。

### synchronize_rcu vs call_rcu：同步等 vs 非同步回呼

寫者換好指標後要「等 GP 過再 free」，有兩條路：

- **`synchronize_rcu()`**：**阻塞**當前執行緒直到一個 GP 過去。程式碼直觀（換指標、`synchronize_rcu()`、`kfree`），但它會睡——所以**只能在可睡的 context 用**（process context，不能在中斷/spinlock 內）。而且一個 GP 可能是幾毫秒到幾十毫秒（要等所有 CPU 換血一輪），阻塞的寫者要有心理準備。
- **`call_rcu(&obj->rcu_head, callback)`**：**不阻塞**。把一個 `struct rcu_head`（嵌在你的物件裡）和一個 callback 登記進去，寫者立刻返回，等 GP 過了 kernel 幫你非同步呼叫 callback（通常就是 `kfree`）。適合不能睡的 context，或不想讓寫者卡住。

`struct rcu_head` 很小（一個 `next` 指標 + 一個 function 指標，見 `include/linux/types.h` / `include/linux/rcupdate.h`），通常直接嵌進被保護的結構：

```c
struct route_entry {
    ...
    struct rcu_head rcu;   // 嵌一個 rcu_head 進來
};

static void free_route(struct rcu_head *head)
{
    struct route_entry *r = container_of(head, struct route_entry, rcu);
    kfree(r);
}

// 寫者換完指標後：
call_rcu(&old->rcu, free_route);   // GP 過了自動 kfree(old)，寫者不等
```

對「就是要 free」這種最常見情況，還有 `kfree_rcu(old, rcu)` 這個捷徑，連 callback 都不用自己寫。

## 底層機制：rcu_dereference / rcu_assign_pointer 的兩道 barrier

到這裡你可能覺得 RCU 只跟「延後 free」有關，但還有一半在 memory ordering（接 Ch 23、Ch 24）。考慮寫者發布一個**新分配**的物件：

```c
struct foo *new = kmalloc(...);
new->a = 1;                      // (1) 初始化欄位
new->b = 2;                      // (2)
rcu_assign_pointer(gp, new);     // (3) 發布指標
```

讀者：

```c
p = rcu_dereference(gp);         // (4) 取指標
x = p->a;                        // (5) 讀欄位
```

沒有 barrier 的話，兩種重排都會出事：

- **寫者側**：CPU（或編譯器）可能把 (3) 排到 (1)(2) 之前——先發布指標、還沒初始化欄位。讀者 (4) 拿到指標、(5) 讀 `p->a` 讀到垃圾。
- **讀者側**（尤其 DEC Alpha 這種弱記憶體架構）：即使寫者順序對，讀者的 (5) 也可能「投機」地在 (4) 之前就把 `p->a` 讀進來（用了舊快取的值）。

`rcu_assign_pointer` 內含一道 **release barrier**（`smp_store_release` 語意）：保證 (1)(2) 一定排在 (3) 發布**之前**對其他 CPU 可見。`rcu_dereference` 內含一道 **依賴性 acquire**（各架構都由 `READ_ONCE` + 編譯器 barrier 提供；早期 DEC Alpha 需要的獨立 `smp_read_barrier_depends` 已於 5.9 移除、併進 `READ_ONCE`）：保證透過這個指標的後續解參照 (5) 不會被排到取指標 (4) 之前。兩道 barrier 配對，讀者**要嘛看到舊指標（指向完整的舊物件），要嘛看到新指標（指向完整初始化的新物件），永遠不會看到「指標已更新但物件半初始化」的中間態**。

> **為什麼不能直接用裸指標讀寫？** 就是因為缺這兩道 barrier。直接 `gp = new` 或 `p = gp` 在弱序架構上會出上述的重排 bug，而且 `sparse` 靜態檢查器會因為 RCU 指標帶 `__rcu` 註解而報警。永遠用 `rcu_assign_pointer`/`rcu_dereference`，不要圖省事。這也是 `lockdep`（Ch 28）能檢查「你是不是忘了 `rcu_read_lock` 就 `rcu_dereference`」的基礎。

## RCU 的變體：一張表看懂

RCU 不是單一機制，是一族。歷史上分很多種，6.x 做了合併，但你讀源碼和 commit log 還會遇到這些名字：

| 變體 | 讀臨界區能睡嗎 | 典型用途 | API 前綴 / 說明 |
|---|---|---|---|
| **RCU（vanilla）** | 不能 | 絕大多數 read-mostly 場景 | `rcu_read_lock` / `synchronize_rcu`。6.x 已把下面 sched/bh 合併進來 |
| **RCU-sched**（歷史） | 不能 | 需要「等 preempt-disable 區段」的語意 | 6.x 已與 vanilla RCU 合併，`synchronize_sched` 等別名保留相容 |
| **RCU-bh**（歷史） | 不能 | 網路收包等 softirq 密集、要防封包 DoS 拖長 GP | 6.x 已合併進 vanilla RCU |
| **SRCU**（Sleepable RCU） | **能睡** | 讀臨界區裡需要睡（如等 I/O）的場景 | `srcu_read_lock(&sp)` 回傳 index、要配 `struct srcu_struct`。代價：讀端有一個 per-CPU 計數器要寫，不像 vanilla 全零成本。`kernel/rcu/srcutree.c` |
| **Tasks RCU** | （另一種語意） | tracing / BPF：等「每個 task 都自願讓出過 CPU」 | 給 trampoline/patch 拆除用，確保沒有 task 還停在被拆的 code 上。`kernel/rcu/tasks.h` |

兩個要特別記：

- **SRCU** 存在的理由：vanilla RCU 讀臨界區**不能睡**（因為它靠 preempt-disable + context switch 當 quiescent state，一睡就破功）。但有些場景讀者中途真的需要睡（等 I/O、拿 mutex）。SRCU 用 per-CPU 計數器 + 每個 domain 獨立的 `srcu_struct` 換來「可睡」，代價是讀端不再完全零成本（要寫那個計數器），且 GP 偵測方式不同。
- **Tasks RCU** 是為 tracing/BPF 生的（接本 repo 的 `bpf` 課）。當你要拆掉一段動態 patch 進去的 trampoline code，得確定「沒有任何 task 的執行流還停在那段 code 裡」。Tasks RCU 的 GP 定義是「每個非 idle task 都至少自願 context switch 過一次」，正好對應這個需求。ftrace、live patching、BPF trampoline 都靠它。

## RCU 保護的資料結構

RCU 最常配串列與 hash table 用。kernel 提供了 `_rcu` 後綴的走訪原語（接 Ch 5 的 `list_head`）：

```c
// 讀者：遍歷一個 RCU 保護的 list
rcu_read_lock();
list_for_each_entry_rcu(entry, &my_list, node) {
    // 安全走訪，內部用 rcu_dereference 取 next
    do_read(entry);
}
rcu_read_unlock();

// 寫者：新增（list_add_rcu 內含 rcu_assign_pointer 語意）
spin_lock(&writer_lock);          // 寫者之間互斥
list_add_rcu(&new->node, &my_list);
spin_unlock(&writer_lock);

// 寫者：刪除
spin_lock(&writer_lock);
list_del_rcu(&old->node);         // 從鏈上摘掉，但不 free
spin_unlock(&writer_lock);
call_rcu(&old->rcu, free_cb);     // 等 GP 過再 free
```

關鍵細節在 `list_del_rcu`（`include/linux/rculist.h`）：它把節點從鏈上摘掉，但**故意不改被刪節點的 `next` 指標**。為什麼？因為此刻可能有讀者正停在這個節點上、下一步要用它的 `next` 往後走。如果 `list_del` 把 `next` 清成 poison（一般 `list_del` 會這麼做），那個讀者就會踩到 poison 崩掉。RCU 版保留 `next`，讓「已經在這個節點上」的讀者還能安全走到下一個節點——舊節點的軀殼要活到 GP 結束才 free。這是 RCU list 和普通 list 最根本的實作差異。

`hlist`（雜湊桶用的單向鏈，Ch 5）有對應的 `hlist_for_each_entry_rcu` / `hlist_add_head_rcu` / `hlist_del_rcu`，路由表、dentry hash、很多 kernel hash table 都用它。

## 動手：RCU 保護串列的模組

寫一個模組：一個全域 config 物件被多個「讀者 kthread」高頻讀取，一個 sysfs/timer 觸發的寫者用 copy-update 換掉它。這是 RCU 最經典的形狀。

```c
// rcu_demo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/rcupdate.h>
#include <linux/kthread.h>
#include <linux/delay.h>

struct config {
    int version;
    int value;
    struct rcu_head rcu;      // 給 call_rcu / kfree_rcu 用
};

static struct config __rcu *cur_cfg;   // __rcu 註解讓 sparse 幫你查存取
static struct task_struct *reader_thr, *writer_thr;

/* 讀者：在 RCU 讀臨界區內安全讀 cur_cfg，全程零鎖 */
static int reader_fn(void *unused)
{
    while (!kthread_should_stop()) {
        struct config *c;

        rcu_read_lock();
        c = rcu_dereference(cur_cfg);
        if (c)
            pr_info_ratelimited("reader: v%d value=%d\n",
                                c->version, c->value);
        rcu_read_unlock();
        /* 出了 unlock 就不能再碰 c */
        msleep(200);
    }
    return 0;
}

/* 寫者：copy-update-publish，舊版本用 kfree_rcu 延後釋放 */
static int writer_fn(void *unused)
{
    while (!kthread_should_stop()) {
        struct config *old, *new;

        new = kmalloc(sizeof(*new), GFP_KERNEL);
        if (!new) { msleep(1000); continue; }

        /* 取舊值來複製。寫者此處若有多個要自己加 spinlock 互斥；
         * 這裡只有一個 writer，用 rcu_dereference_protected 表明
         * 「我是唯一寫者，不需在讀臨界區也能安全讀」 */
        old = rcu_dereference_protected(cur_cfg, /* 我持有寫者資格 */ 1);
        if (old) {
            *new = *old;                 // Copy
            new->version = old->version + 1;
            new->value = old->value + 10; // Update（改副本）
        } else {
            new->version = 1;
            new->value = 100;
        }

        rcu_assign_pointer(cur_cfg, new); // Publish：原子換指標 + release barrier

        if (old)
            kfree_rcu(old, rcu);          // 等 GP 過自動 kfree(old)

        msleep(1000);
    }
    return 0;
}

static int __init rcu_demo_init(void)
{
    struct config *c = kmalloc(sizeof(*c), GFP_KERNEL);
    if (!c) return -ENOMEM;
    c->version = 0; c->value = 0;
    rcu_assign_pointer(cur_cfg, c);       // 初始發布

    reader_thr = kthread_run(reader_fn, NULL, "rcu_demo_reader");
    writer_thr = kthread_run(writer_fn, NULL, "rcu_demo_writer");
    pr_info("rcu_demo: loaded\n");
    return 0;
}

static void __exit rcu_demo_exit(void)
{
    struct config *c;

    kthread_stop(reader_thr);
    kthread_stop(writer_thr);

    /* 卸載時清掉最後的版本。此刻兩個 thread 都停了，沒有讀者，
     * 但穩妥起見用 synchronize_rcu 等掉任何殘留的 GP 再 free */
    c = rcu_dereference_protected(cur_cfg, 1);
    rcu_assign_pointer(cur_cfg, NULL);
    synchronize_rcu();                    // 等所有現有讀者離開
    kfree(c);
    pr_info("rcu_demo: unloaded\n");
}

module_init(rcu_demo_init);
module_exit(rcu_demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RCU-protected config demo for kernel_internals Ch27");
```

編譯（Makefile 照 Ch 0 那份，`KDIR` 指你的 6.12 源碼樹），在 QEMU 裡 `insmod rcu_demo.ko`，`dmesg` 會看到讀者一直印當前版本、寫者每秒 bump 一次版本號。重點觀察：**讀者從頭到尾沒有任何鎖**，卻永遠不會讀到半初始化或已 free 的物件。

對比 rwlock 版本：把 `cur_cfg` 改成一個被 `rwlock_t` 保護的物件，讀者 `read_lock`/`read_unlock`、寫者 `write_lock`/`write_unlock` 原地改。功能一樣，但把讀者 kthread 開到跟 CPU 數一樣多、跑 `perf stat` 比較，你會看到 rwlock 版的讀者在 cache line 上互相拖累（`cache-misses` 顯著上升），RCU 版讀者則幾乎不互相干擾。這就是開頭那個擴展性論點的實測。

### 觀測 RCU 的內部狀態

```bash
# GP 的統計與各 CPU 的 quiescent state 進度
cat /sys/kernel/debug/rcu/rcu_preempt/rcugp      # GP 序號進度
cat /sys/kernel/debug/rcu/rcu_preempt/rcudata    # 每個 CPU 的 rcu_data 快照

# RCU CPU stall 警告（某 CPU 太久沒報告 quiescent 就會噴到 dmesg）
dmesg | grep -i "rcu.*stall"

# 開機/模組驗證 RCU 正確性：rcutorture（CONFIG_RCU_TORTURE_TEST）
modprobe rcutorture
dmesg | grep torture     # 跑一陣子後 rmmod，看它報告的 Reader/Writer 統計
```

`rcutorture`（`kernel/rcu/rcutorture.c`）是 kernel 自帶的 RCU 壓力測試——它開一堆讀者/寫者 thread 猛操 RCU，故意製造各種時序，驗證「讀者絕不會看到已 free 的物件」這個不變量。你不用寫測試，點一下它、看它報告，就是對 RCU 語意最好的動手確認。

**RCU CPU stall** 警告值得認識：如果某顆 CPU 卡在一個超長的讀臨界區裡（例如你在 `rcu_read_lock` 之後不小心睡了、或死迴圈了），它永遠報告不了 quiescent state，於是**所有等 GP 的寫者全部卡死**（`synchronize_rcu` 永不返回、`call_rcu` 的 callback 永不執行、記憶體不斷囤積）。kernel 偵測到某 CPU 21 秒（預設 `CONFIG_RCU_CPU_STALL_TIMEOUT`）沒報告就噴 stall 警告，把兇手 CPU 的 stack 印出來。生產環境看到 RCU stall，八成是有人在讀臨界區裡做了不該做的事（睡、拿會睡的鎖、跑太久）。

## 對比與取捨

| 機制 | 讀者成本 | 讀者擴展性 | 讀者能睡 | 保護寫寫 | 一致性 | 適用 |
|---|---|---|---|---|---|---|
| **spinlock** | 每次 atomic + 可能自旋 | 差（單一鎖爭用） | 否 | 是 | 強 | 短臨界區、讀寫都少 |
| **rwlock** | atomic inc/dec（寫 cache line） | 差（讀者也 bounce） | 否 | 是 | 強 | read-mostly 但核少 |
| **RCU** | 幾乎零（no-op ~ preempt off） | **完美線性** | 否（vanilla） | **否** | 最終一致（讀者可能看到舊版本） | read-mostly + 核多 + 容忍舊值 |
| **SRCU** | per-CPU 計數器一次寫 | 好 | **是** | 否 | 同 RCU | 讀臨界區需要睡 |
| **seqlock**（Ch 28） | 讀兩次 seq、寫者不阻塞讀者 | 好 | 否 | 是（寫者互斥） | 讀者可能重試 | 讀多、資料小、寫者不能被讀者擋 |

一句話決策：**read-mostly + 核多 + 能容忍讀者短暫看到舊資料 → RCU**。任何一個條件不成立（寫多、讀者需要看到絕對最新值、讀臨界區要睡），就回頭考慮其他機制。

## 踩雷集錦

1. **「RCU 是一種鎖」→ 錯。** RCU 讀端不互斥任何東西。多個讀者、甚至讀者與寫者可以**同時**在同一資料上跑。`rcu_read_lock` 這個名字有誤導性——它不「鎖」，只「標出臨界區邊界讓 GP 偵測知道」。把它想成「宣告我在讀」而非「取得鎖」。

2. **在 vanilla RCU 讀臨界區裡睡 → 死。** `rcu_read_lock` 到 `rcu_read_unlock` 之間**不能 schedule**（不能 `msleep`、不能拿 mutex、不能做會阻塞的 I/O）。因為 vanilla RCU 靠「context switch = quiescent state」偵測 GP，你一睡就等於謊報「我離開讀臨界區了」，寫者可能提前 free 掉你正握著的物件。需要睡就用 SRCU。

3. **出了 `rcu_read_unlock` 還用 `rcu_dereference` 拿到的指標 → UAF。** 那個指標的有效性**只在讀臨界區內**成立。一旦 `rcu_read_unlock`，寫者的 GP 就可能過去、物件被 free。要跨臨界區保留物件，得在臨界區內用 refcount 把它 `get` 住（RCU 常和 refcount 搭配：RCU 保證「查找的瞬間物件還在」，refcount 保證「查到之後能用多久」）。

4. **以為 RCU 幫你搞定寫寫互斥 → race。** RCU 只協調讀 vs 寫。兩個寫者同時 copy-update-assign 會互相覆蓋。**寫者之間永遠要自己加鎖**（通常一把 spinlock 保護所有寫者）。

5. **`synchronize_rcu()` 放在 spinlock / 中斷 context 裡 → 卡死或警告。** 它會睡，只能在 process context 用。不能睡的地方要 free 就用 `call_rcu`/`kfree_rcu`。反過來，`call_rcu` 的 callback 是在 softirq 跑的，callback 裡也不能睡。

6. **忘了 barrier，直接 `p = global_ptr` 裸讀 → 弱序架構上偶發詭異 bug。** 一定用 `rcu_dereference`/`rcu_assign_pointer`。`sparse`（`make C=1`）會用 `__rcu` 註解幫你抓裸存取，別忽略它的警告。

## 進階：再往深一層

- **preemptible RCU（`CONFIG_PREEMPT_RCU`）**：在搶佔式 kernel（`PREEMPT`/`PREEMPT_RT`）裡，讀臨界區**允許被搶佔**（否則長讀臨界區會傷延遲）。此時 `rcu_read_lock` 不能只靠 preempt-disable，改用一個 per-task 計數器 `t->rcu_read_lock_nesting`（`include/linux/sched.h`），被搶佔時把該 task 記進 `rcu_node` 的 blocked 名單，GP 要等這些 blocked task 也離開讀臨界區。這是 `rcu_preempt` 這個 GP 名字的由來，也是為什麼上面 debugfs 路徑叫 `rcu_preempt`。

- **expedited GP（`synchronize_rcu_expedited`）**：普通 GP 為了省電/省 IPI 會拖到毫秒級。有時（如系統啟動、CPU 熱插拔）需要快點結束 GP，`expedited` 版本會主動送 IPI 逼每顆 CPU 立刻報告 quiescent，把 GP 壓到微秒級——代價是打斷所有 CPU。可用 `rcupdate.rcu_expedited=1` 開機參數全域啟用（伺服器啟動加速常用）。

- **`rcu_barrier()`**：`call_rcu` 是非同步的，callback 可能還排在隊裡沒跑。模組**卸載前**如果用過 `call_rcu`，必須 `rcu_barrier()` 等所有已登記的 callback 都執行完，否則模組 code 被卸載後 callback 才跑 → 呼叫到已消失的函式 → panic。這是寫 RCU 模組最容易漏的收尾。

- **面試常問**：「grace period 是什麼、怎麼偵測」「RCU 讀端為什麼零成本」「RCU 和 rwlock 差在哪、什麼時候該用 RCU」「讀臨界區能不能睡、為什麼」「RCU 保護寫寫嗎」。能把上面那張讀者跨 GP 的時間線畫出來，這幾題就穩了。

## 動手練習

1. **畫 GP 時間線**：不看本章，自己畫四個讀者（一個在 assign 前離開、一個跨越 assign、兩個在 assign 後才進）跨越一次 grace period，標出「哪個讀者決定了 GP 何時能結束」「舊版本何時能 free」。畫對了代表你真懂 GP。

2. **弄壞它（讀端睡覺）**：把 demo 模組的 `reader_fn` 改成在 `rcu_read_lock()` 和 `rcu_read_unlock()` **之間**加一個 `msleep(50)`。載入後跑一陣子，觀察 `dmesg` 是否出現 RCU stall 警告或 lockdep（Ch 28）抱怨「illegal context switch in RCU read-side critical section」。理解為什麼這是致命錯誤。

3. **rwlock vs RCU 擴展性實測**：把 demo 改成 rwlock 版與 RCU 版各一份，讀者 kthread 數開到 `nproc`，用 `perf stat -e cache-misses,cache-references insmod ...`（或在模組內用 counter）比較兩者的 cache-miss。用數據驗證開頭「讀者互相 bounce」的論點。

4. **`call_rcu` 收尾陷阱**：把 demo 的 `kfree_rcu` 改成手寫 `call_rcu` + callback，然後**故意在 `exit` 裡不加 `rcu_barrier()`** 就 `rmmod`（在有壓力的情況下），看能不能觸發 callback 執行到已卸載 code 的問題。加回 `rcu_barrier()` 對比。

5. **讀 tree RCU 一段源碼**：在 `kernel/rcu/tree.c` 找到 `rcu_gp_kthread()`，用本章的 GP 狀態機（init → wait for QS → force QS → cleanup）對照它的主迴圈，把每一步和你畫的時間線對上。

## 本章重點整理

- **RCU 讓讀者零成本**：讀端不寫任何共享狀態、沒有 atomic，所以讀者之間完美線性擴展，解決了 rwlock 讀者也 bounce cache line 的擴展性缺陷。代價是寫者變複雜（copy-update-publish）、讀者可能看到舊版本、寫寫仍要自己互斥。
- **grace period 是靈魂**：「等所有在某時刻之前就存在的讀臨界區都結束一次」的那段時間。過了就保證沒人握著舊版本，才能 free。經典 RCU 靠「每顆 CPU 都經過一次 quiescent state（context switch/idle/回 user）」偵測 GP，讀者完全不參與。
- **tree RCU** 用階層 `rcu_node` 樹匯集各 CPU 的 quiescent 回報，避免所有 CPU 搶一把鎖；`synchronize_rcu` 同步等 GP、`call_rcu`/`kfree_rcu` 非同步在 GP 後跑 callback。
- **兩道 barrier**：`rcu_assign_pointer`（release）保證發布前物件已初始化完整，`rcu_dereference`（依賴 acquire）保證讀者不會投機讀到半成品——讀者永遠看到「完整的舊版本」或「完整的新版本」，沒有中間態。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 RCU 讀端「零成本」，以及它相對 rwlock 的擴展性優勢從哪來（連到 Ch 23 cache line bouncing）
- [ ] 能畫出讀者跨越 grace period 的時間線，指出哪個讀者決定 GP 何時結束、舊版本何時能安全 free
- [ ] 能說出經典 RCU 怎麼「不追蹤個別讀者」就偵測 GP（quiescent state = context switch，讀臨界區關搶佔）
- [ ] 面試被問「RCU 讀臨界區能不能睡」，能答「vanilla 不能（會謊報 quiescent 導致提前 free），要睡用 SRCU」並解釋原因
- [ ] 能說出 `rcu_assign_pointer` / `rcu_dereference` 各帶哪種 barrier、防止哪種重排
- [ ] 知道 RCU **不**保護寫寫、寫者之間仍要加鎖；知道 `call_rcu` 模組卸載前要 `rcu_barrier()`

## 延伸閱讀

### 官方文件（權威）

- **[Documentation/RCU/whatisRCU.rst](https://www.kernel.org/doc/html/latest/RCU/whatisRCU.html)** — Paul E. McKenney
  - **讀哪裡**：全篇，尤其「What is RCU's Fundamental Idea」和那張 API 對照表。這是 RCU 的**權威**入門，作者就是 RCU 在 Linux 的主要維護者
  - **和本章的關聯**：本章的 API 骨架、GP 定義都以它為準；本章任何說法和它衝突，以它為準。RCU 是 kernel 最反直覺的機制，看原作者怎麼講最踏實
  - **前提**：讀完本章再讀它，會發現本章是它的白話導讀版

- **[Documentation/RCU/Design/Requirements/Requirements.rst](https://www.kernel.org/doc/html/latest/RCU/Design/Requirements/Requirements.html)** — McKenney
  - **讀哪裡**：想深入「RCU 到底保證什麼、不保證什麼」時讀。它把 RCU 的語意需求列得極細，是理解各種邊界情況的字典
  - **能學到什麼**：為什麼 GP 這樣定義、grace period 與 quiescent state 的精確關係、各變體存在的理由

### LWN 文章系列（把來龍去脈講透）

- **[What is RCU, Fundamentally?](https://lwn.net/Articles/262464/)** — McKenney & Walpole（LWN 三部曲之一）
  - **讀哪裡**：三篇（Fundamentally / Usage / API）依序讀。第一篇用最少的字把「publish-subscribe + wait-for-readers」的核心講清楚
  - **為什麼值得讀**：比 kernel doc 更著重「為什麼」和演進脈絡，是理解 RCU 設計哲學的最佳單一資源

- **[The RCU API, 20xx edition](https://lwn.net/Articles/988638/)** — McKenney（每隔幾年更新一版）
  - **讀哪裡**：當你要實際用某個 RCU API、不確定該用哪個變體/收尾函式時，來這裡查最新的完整 API 地圖
  - **前提**：知道 RCU 基本概念後當工具書用

### 書籍 / 深潛

- **《Is Parallel Programming Hard, And, If So, What Can You Do About It?》**（"perfbook"）— McKenney，免費 PDF
  - **這本書的定位**：並行程式設計的百科，RCU 那幾章是全世界對 RCU 講得最深的文字。想搞懂 tree RCU 的 GP 狀態機、記憶體序證明就讀它
  - **注意**：很厚，當參考書按需查，不必通讀

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，關於同步的章節
  - **這本書的定位**：把 RCU 放在 kernel 各種同步機制的脈絡裡對比（spinlock/rwlock/seqlock/RCU），適合先建立「什麼時候用哪個」的直覺
  - **注意**：講的 kernel 較舊，tree RCU 的細節以本章和 kernel doc 的 6.12 為準

RCU 是讀端最快的同步機制，但它把複雜度全塞給了「等 grace period」；下一章我們看兩個相關工具——**seqlock**（讓寫者永不被讀者擋、讀者重試的另一種 read-mostly 解法），以及 **lockdep**（kernel 怎麼在執行期自動抓出你的鎖順序會不會死鎖），並把整個 Part 4 的同步機制收束成一張「該用哪個鎖」的決策圖。

→ [Ch 28 seqlock、lockdep 與死鎖偵測](./28-seqlock-lockdep.md)
