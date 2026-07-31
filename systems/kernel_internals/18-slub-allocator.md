# Ch 18 — slab/slub allocator 內部

> **目標**：理解 kernel 怎麼把 buddy 給的整頁（Ch 17）切成大量同大小的小物件、攤銷配置成本並提升 cache 命中；讀懂 slub 的三層結構（`kmem_cache` → per-CPU `kmem_cache_cpu` → per-node `kmem_cache_node`）與 freelist 的「物件內嵌指標」機制；並且從防禦方看清楚為什麼 slub 是 `kernel_pwn` 課裡 kernel heap 攻擊（UAF、overflow、cross-cache）的主戰場。這章寫最深。

## 為什麼需要 slab？

Ch 17 的 buddy allocator 最小配置單位是**一整頁**——x86_64 上是 4KB。但 kernel 每天要配的東西，絕大多數遠比一頁小：

- `struct task_struct`（Ch 9）約幾 KB，但每 `fork` 一次就要一個
- `struct inode`、`struct dentry`（Ch 33 VFS）幾百 bytes，開一個檔案就要好幾個
- `struct file`、`struct sk_buff`（Ch 43 網路）、`struct cred`（Ch 47）——這些物件小、量大、生死頻繁

如果每配一個 32-byte 的物件就跟 buddy 要一整頁，會發生兩件災難：

1. **內部碎片（internal fragmentation）爆炸**：32 bytes 的東西佔掉 4096 bytes，浪費 99%。
2. **配置成本高**：buddy 的配置路徑要走 free list、可能要 split/coalesce、要動 zone 的鎖（Ch 17）。對一個每秒配置幾十萬次的物件來說，這條路太重。

還有第三個更隱形但同樣關鍵的理由——**cache 局部性**。同一種物件（例如所有 `task_struct`）如果散落在記憶體各處，CPU cache 命中率會很差；如果把同型別物件**擠在同一批頁裡、對齊 cache line**，走訪它們時 cache 表現好得多。

slab allocator 的核心想法就一句話：**跟 buddy 批發整頁，自己零售切成同大小的物件格子。** 一頁 4KB 切成 128 個 32-byte 格子，配置時就是「從格子清單拿一個」、釋放時「把格子還回清單」——沒有 split/coalesce、沒有跟 buddy 打交道，快路徑甚至不用鎖（下面會看到為什麼）。這層「物件配置器」墊在 buddy 之上，就是 slab。

> 回顧 Ch 6：你在模組裡呼叫 `kmalloc(size, gfp)` 時，底層去的就是這裡。`kmalloc` 不直接找 buddy，而是找一組通用的 slab cache（`kmalloc-32`、`kmalloc-64`…）。這章把 Ch 6 說「留到 Ch 18 深挖」的那塊補完。

## 先建立直覺

先把名詞釘清楚，這是讀 slub 源碼最容易混的地方：

- **slab**（小寫、概念）：一塊（或幾塊連續的）從 buddy 拿來的頁，被切成同大小的物件格子。在 slub 裡一個 slab 就是一組 page，用 `struct slab` 描述。
- **`kmem_cache`**（結構）：**一種物件**的快取管理者。每種要頻繁配置的物件有一個自己的 `kmem_cache`，例如 `task_struct` 有 `task_struct_cachep`。一個 `kmem_cache` 管著很多個 slab。
- **slab / slub / slob**（三種實作）：這是同一個抽象的三種歷史實作，下一節講。

心智模型：把 `kmem_cache` 想成「某種物件的專賣店」，它手上有一疊「貨架」（slab），每個貨架切成同大小的格子。你要一個物件，它從當前正在用的貨架拿一格給你；那個貨架空了就去倉庫（partial list）換一個；倉庫也空了才去 buddy 進新貨。

```
   kmem_cache（例：task_struct_cachep，管一種物件）
   ─────────────────────────────────────────────────
        │
        ├── kmem_cache_cpu（每 CPU 一份，免鎖快路徑）
        │      ├─ 當前 active slab（正在切的那塊頁）
        │      └─ freelist（指向該 slab 裡下一個空閒格子）
        │
        └── kmem_cache_node（每 NUMA node 一份）
               └─ partial list（半滿的 slab，被 CPU 用光時來這裡拿）

   一個 slab（從 buddy 拿的頁，切成格子）：
   ┌────────┬────────┬────────┬────────┬────────┬────────┐
   │ obj 0  │ obj 1  │ obj 2  │ obj 3  │ obj 4  │  ...   │  ← 每格同大小
   └────────┴────────┴────────┴────────┴────────┴────────┘
```

配置就是「從 freelist 頭拿一格」，釋放就是「把格子推回 freelist 頭」。快路徑全在 per-CPU 那份上動，不用鎖——這是 Ch 7 per-CPU 設計的直接應用。

## slab / slob / slub：三種實作的歷史

同一個「物件配置器」抽象，Linux 歷史上有三個實作，理解它們的取捨才知道為什麼今天預設是 slub：

| 實作 | 出身 | 特點 | 現況 |
|---|---|---|---|
| **slab** | 最早（源自 Solaris 的 Bonwick slab 論文） | 每 cache 有複雜的 per-CPU array、colouring、大量 metadata | v6.8 起**已移除** |
| **slob**（Simple List Of Blocks） | 給極小記憶體嵌入式系統 | 極簡、省 metadata，但配置慢、碎片多 | v6.4 起**已移除** |
| **slub**（Unqueued Slab） | 2007 由 Christoph Lameter 引入 | metadata 幾乎不佔額外空間（塞進 `struct page`/`struct slab`）、per-CPU freelist、擴展性好、好除錯 | **現行唯一實作** |

到 v6.12，`CONFIG_SLAB` 和 slob 都已經是歷史，源碼只剩 `mm/slub.c`。所以這章講的就是 slub，你在 elixir 上看到的 `mm/slab.c` 是舊版被刪掉的痕跡，別讀錯檔。

> slub 名字裡的 "Unqueued" 是相對舊 slab 而言的——舊 slab 為了效能維護一堆 per-CPU 的物件 queue，metadata 龐大且難除錯。slub 拿掉這些 queue，改用「per-CPU 直接掛一個 active slab + freelist」的極簡結構，metadata 幾乎全塞進本來就存在的 `struct slab`（它 overlay 在 `struct page` 上），額外開銷極小。這是它勝出的主因。

## slub 的資料結構：三層

源碼在 `mm/slub.c`（實作）與 `include/linux/slub_def.h`（結構定義）。三個核心結構，由外而內：

### 1. `struct kmem_cache`——一種物件的總管

定義在 `include/linux/slub_def.h`。關鍵欄位：

- `unsigned int size`：物件對外的大小（含 metadata、對齊後）
- `unsigned int object_size`：物件本身要求的大小
- `unsigned int offset`：**freelist 指標存在物件內的哪個位移**——這個欄位是等下 hardening 的關鍵
- `struct kmem_cache_cpu __percpu *cpu_slab`：指向 per-CPU 結構（Ch 7 的 per-CPU 變數）
- `struct kmem_cache_node *node[MAX_NUMNODES]`：每 NUMA node 一個
- `unsigned int oo`（order/objects 打包）：一個 slab 用幾個 page（order）、能切幾個物件
- `slab_flags_t flags`：`SLAB_HWCACHE_ALIGN`、`SLAB_ACCOUNT`、`SLAB_TYPESAFE_BY_RCU` 等
- `const char *name`：`/proc/slabinfo` 裡看到的名字

### 2. `struct kmem_cache_cpu`——per-CPU 免鎖快路徑

這是 slub 效能的心臟，每個 CPU 一份（`__percpu`）。關鍵欄位：

- `void **freelist`：指向**當前 active slab 裡下一個空閒物件**。這是一個「物件內嵌指標」串起來的單向鏈的頭（下一節細講）。
- `struct slab *slab`：當前這個 CPU 正在切的那塊 slab
- `unsigned long tid`：transaction id，配 `this_cpu_cmpxchg_double` 做無鎖同步用，防止被搶佔/遷移造成的 ABA

快路徑的全部動作就是：從 `freelist` 拿頭、把 `freelist` 前進到下一格。因為是 per-CPU 的，同一時間只有這顆 CPU 碰它，**不需要 spinlock**——用 `cmpxchg_double` 這個原子指令搭配 `tid` 就能對付「配置到一半被中斷/搶佔」的情況（Ch 24 講原子指令）。

### 3. `struct kmem_cache_node`——per-node partial 倉庫

定義在 `mm/slub.c` 的 `struct kmem_cache_node`（`mm/slab.h` 只有前向宣告）。每個 NUMA node 一份（Ch 15 NUMA）。關鍵：

- `struct list_head partial`：**半滿 slab 的鏈表**——有些格子被配走、還有些空著
- `unsigned long nr_partial`：partial list 長度
- `spinlock_t list_lock`：動 partial list 要拿的鎖（慢路徑才會碰）

當 per-CPU 的 active slab 用光（freelist 空了），CPU 就來這裡的 partial list 拿一塊半滿的 slab 補上。這一步要動 `list_lock`，屬於**慢路徑**。

三層的分工邏輯：**per-CPU 是免鎖快取，per-node 是加鎖倉庫，buddy 是最終貨源。** 越往下越慢、越需要鎖，slub 的設計就是讓絕大多數配置/釋放停在最快的那層。

```
   配置一個物件，由快到慢的三段瀑布：
   ───────────────────────────────────────────────
   ① kmem_cache_cpu.freelist 有貨   → 直接拿，免鎖         ← 99% 走這
   ② freelist 空 → kmem_cache_node.partial 有半滿 slab
        → 拿 list_lock、換上這塊 slab                     ← 慢路徑
   ③ partial 也空 → 跟 buddy 要新的一批 page 做新 slab    ← 最慢
        (alloc_pages, Ch 17)
```

## 底層機制：freelist 與「物件內嵌指標」

這一節是全章、也是 `kernel_pwn` 的核心。理解 freelist 怎麼串，就理解了 kernel heap 攻擊為什麼有效。

### 空閒物件自己存「下一個」的位址

slub 不用另一塊陣列記「哪些格子是空的」——那要額外 metadata。它用一個省空間到極致的技巧：**空閒物件的內部，直接存放下一個空閒物件的位址。**

一個格子只有兩種狀態：被配出去（裝著使用者的資料）或空閒（在 freelist 上）。既然空閒時裡面沒有有效資料，那就借用這塊空間存指標——把所有空閒格子串成一條單向鏈：

```
   一個 slab 裡的格子，空閒的被串成單向鏈：

   kmem_cache_cpu.freelist
        │
        ▼
   ┌─────────┐     ┌─────────┐     ┌─────────┐
   │ obj A   │     │ obj C   │     │ obj E   │
   │ [next]──┼────►│ [next]──┼────►│ [next]──┼───► NULL
   └─────────┘     └─────────┘     └─────────┘
     (空閒)          (空閒)          (空閒)
     ↑ obj B、obj D 已被配出去，不在鏈上（裝著使用者資料）

   next 指標存在物件內位移 kmem_cache->offset 處
   （通常是物件開頭前 8 bytes，或依 hardening 設定而定）
```

配置（快路徑）：`object = freelist; freelist = object->next;`——拿鏈頭、頭前進。
釋放（快路徑）：`object->next = freelist; freelist = object;`——把物件推回鏈頭。

兩個操作都是常數時間、都只碰 per-CPU 那份 freelist、都不用鎖。這就是為什麼 slub 快路徑能這麼便宜。指標存在物件內哪個位移由 `kmem_cache->offset` 決定。

`get_freepointer()` / `set_freepointer()`（`mm/slub.c`）就是讀寫這個內嵌指標的函式，值得在 elixir 上看一眼。

### 這正是 kernel_pwn 的攻擊點

現在把攻擊者的視角疊上來——你在 `kernel_pwn` 課練的東西，從防禦方看是這樣：

**Use-After-Free（UAF）**：物件被 free 後回到 freelist，它的前 8 bytes 現在存著「下一個空閒物件的位址」。如果程式有 bug、free 之後還持有指標並繼續寫入這塊記憶體——攻擊者就能**改寫 freelist 指標**。下一次同 cache 的配置會沿著這條鏈走，於是攻擊者控制的假位址被當成「空閒物件」交出去。這就是**任意位址配置**（arbitrary allocation）的原始形態。

**heap overflow**：一個物件寫超過邊界，蓋到相鄰格子。如果相鄰格子正好是空閒的、裡面存著 freelist 指標——overflow 一樣改寫了 freelist 指標，效果同上。

**heap spray / grooming**：攻擊者大量配置同 cache 的物件，把 freelist 排列成他要的形狀，讓目標物件落在可控位置旁邊。因為 slub 的 freelist 是 LIFO（後進先出，剛 free 的最先被配出），free/alloc 順序高度可預測，這讓 spray 很好操控。

理解 slub 的分配是「LIFO 取自單向鏈、指標嵌在物件內」——你就同時理解了「為什麼 free 完不清空指標會出事」與「為什麼防禦要保護 freelist 指標」。

### 防禦：freelist 指標的 hardening

kernel 針對上面這些攻擊做了兩道防禦，都是 config 開關：

**`CONFIG_SLAB_FREELIST_HARDENED`——指標混淆**

開啟後，存進物件的不是裸的 next 位址，而是 `ptr XOR s->random XOR &address`（`mm/slub.c` 的 `freelist_ptr_encode` / `freelist_ptr_decode`）：

- `s->random`：每個 `kmem_cache` 一個隨機祕密值（開機時亂數）
- `&address`：存放這個指標的位置本身也參與運算（位址相關混淆）

效果：攻擊者就算能寫一個 freelist 節點，也不知道 `s->random`，寫不出合法的混淆指標。當 slub 解碼出一個歪掉的指標、拿去用時會 crash（或觸發 sanity 檢查），把「任意配置」降級成「crash」。它**不阻止**你覆寫，但讓覆寫後的指標無法被你控制。

同時這個 config 也加了 double-free 的偵測：free 時檢查物件是不是已經在 freelist 頭（`CONFIG_SLAB_FREELIST_HARDENED` 下的 `set_freepointer` 附近有檢查），擋掉最天真的 double-free。

**`CONFIG_SLAB_FREELIST_RANDOM`——初始順序隨機化**

一個新 slab 剛從 buddy 拿來、切好格子時，freelist 預設是「obj0→obj1→obj2…」的線性順序。開這個 config 後，初始 freelist 的串接順序被**打亂**（`mm/slub.c` 的 `shuffle_freelist`，用 per-cache 的 `random_seq`）。

效果：攻擊者無法假設「配置出來的物件在記憶體裡是連續遞增的」，spray 出來的物件位址關係變得不可預測，heap grooming 難度上升。注意它只打亂**新 slab 的初始順序**，一旦物件開始 free/alloc，LIFO 行為還是會讓順序部分可預測——所以這是提高難度，不是根絕。

> 在 `kernel_pwn` 課你學的是怎麼在這些 hardening 下仍然找到路（例如不碰 freelist 指標、改用相鄰物件的可控欄位、或用 cross-cache 攻擊繞過單一 cache 的保護）。在這門課我們站在防禦方，重點是**知道每道防禦擋掉哪一類攻擊、留下哪些縫**。這條線兩門課合起來看最完整。

## 配置與釋放：快路徑 vs 慢路徑

把上面的結構串成執行流程。配置的入口是 `kmem_cache_alloc()`（專用 cache）或 `__kmalloc()`（通用 cache），底層走 `slab_alloc_node()` →核心是 `___slab_alloc()`（`mm/slub.c`）。

### 配置快路徑（`slab_alloc_node` 內聯部分）

```
   1. 關搶佔保護下讀 this_cpu 的 kmem_cache_cpu
   2. object = c->freelist
   3. if (object != NULL):                        ← 快路徑命中
         next = get_freepointer(s, object)
         用 this_cpu_cmpxchg_double 原子地把
              (freelist, tid) 從 (object, tid) 換成 (next, tid+1)
         成功 → 回傳 object。全程免鎖。
   4. else 或 cmpxchg 失敗 → 進 ___slab_alloc()（慢路徑）
```

`cmpxchg_double` + `tid` 的組合是為了對付「配置到一半，被中斷或搶佔，甚至被遷移到別的 CPU」——`tid` 每次成功配置就 +1，如果中途 CPU 換了或 freelist 被別人動過，cmpxchg 會失敗、退回重試或走慢路徑。這是 Ch 7（per-CPU）+ Ch 24（原子操作）的綜合應用。

### 配置慢路徑（`___slab_alloc()`）

per-CPU freelist 空了，依序嘗試：

```
   1. 當前 slab 還有 "frozen" 的 free 物件？→ 拿來補 freelist
   2. 否則從 kmem_cache_node.partial 拿一塊半滿 slab（要 list_lock）
        → 設為新的 active slab、接上它的 freelist
   3. partial 也空 → new_slab()：跟 buddy 要 order 個 page（alloc_pages, Ch 17）
        → 切格子、（可能 shuffle）建 freelist → 設為 active slab
   4. 回到快路徑邏輯拿第一個物件
```

第 3 步就是 slab 層向 buddy 層「進貨」的接縫——這裡把 Ch 17 和 Ch 18 縫起來。

### 釋放（`kmem_cache_free` / `kfree`）

釋放也分快慢。核心在 `slab_free()` / `__slab_free()`（`mm/slub.c`）：

- **快路徑**：要 free 的物件屬於**當前 CPU 的 active slab** → 直接 `set_freepointer` 把它推回 per-CPU freelist 頭，`cmpxchg_double` 更新。免鎖。
- **慢路徑**：物件屬於別的 slab（不是當前 active）→ 走 `__slab_free`，可能要動 `list_lock`：把物件還回那個 slab 的 freelist；如果 slab 從「full」變「partial」要掛回 partial list；如果變「全空」且 partial 太多，可能整個 slab 還給 buddy（`discard_slab`）。

> **slub 不會積極把空 slab 還給 buddy**。一個變全空的 slab 通常留在 partial list 當快取，只有 partial list 過長時才釋放。這是刻意的——留著省下次配置的成本。副作用是：`free` 掉大量物件後，`free` 記憶體不一定立刻反映在系統可用量上，這常讓人誤判記憶體洩漏。

## 專用 cache vs 通用 kmalloc cache

slub 上有兩類 cache，用途不同：

### 專用 cache：`kmem_cache_create()`

子系統為自己頻繁配置的物件建一個專屬 cache：

```c
task_struct_cachep = kmem_cache_create("task_struct",
        sizeof(struct task_struct), align,
        SLAB_PANIC | SLAB_ACCOUNT, NULL);
```

好處：物件大小固定、對齊可控、可掛建構子、`/proc/slabinfo` 裡有獨立一行好觀測。`task_struct`、`inode_cache`、`dentry`、`vm_area_struct`（`kmem_cache_create` 的呼叫散在各子系統初始化）都是這樣建的。專用 cache 建立見 `mm/slab_common.c` 的 `kmem_cache_create()`。

### 通用 cache：kmalloc 的分桶

`kmalloc(size, gfp)`（Ch 6）不為每個大小建 cache，而是預先建一組**按 2 的次方分桶**的通用 cache：`kmalloc-8`、`kmalloc-16`、`kmalloc-32`、…、`kmalloc-2k`、`kmalloc-4k`…（`mm/slab_common.c` 的 `kmalloc_caches[]`、`create_kmalloc_caches()`）。你 `kmalloc(20, ...)` 會被**向上取整**到 `kmalloc-32`，浪費 12 bytes——這是通用配置器換取「不必為每個大小建 cache」的代價。

> 大於 `KMALLOC_MAX_CACHE_SIZE`（通常 8KB，兩頁）的 `kmalloc` 不走 slub，直接找 buddy 拿 page（`kmalloc_large`）。所以 `kmalloc` 是「小的走 slub、大的走 buddy」的分流入口。

### cache merge：對攻擊面的影響

這裡有個對 `kernel_pwn` 極重要的機制：**slub 會把「大小相近、flags 相容」的不同 cache 合併成同一個**（`mm/slab_common.c` 的 `find_mergeable()`）。例如兩個不同子系統各建了一個 192-byte 的專用 cache，slub 可能讓它們共用同一批 slab——`/proc/slabinfo` 裡會看到名字帶 `:t-0000192` 之類的合併別名。

- **對效能**：減少 cache 數量、提高每個 slab 的利用率，好事。
- **對安全**：壞事。合併意味著**不同型別的物件住在同一個 freelist 上**。攻擊者想 UAF 一個難以直接操控的物件（例如某個帶函式指標的結構）時，可以改配置一個**同 cache 裡好控制的物件**（例如一塊他能塞任意內容的 buffer）去佔那個被 free 的坑——這正是很多 exploit 的「reclaim / 佔坑」步驟能成立的原因。

阻止 merge 的手段：建 cache 時帶上會使其「不可合併」的 flag（如 `SLAB_ACCOUNT`、某些 `SLAB_NO_MERGE`），或開機參數 `slab_nomerge`。**防禦上把敏感物件放進不可合併的專屬 cache**（近年 kernel 對 `cred`、`vm_area_struct` 等做的 `SLAB_ACCOUNT` / 專用 cache 化）就是為了縮小這個攻擊面。這條防禦演進的另一半（攻擊方怎麼繞、cross-cache 怎麼跨過 cache 邊界）在 `kernel_pwn` 課。

## 動手：觀測 slub

### 讀 `/proc/slabinfo`

```bash
sudo cat /proc/slabinfo | head
# slabinfo - version: 2.1
# name            <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> ...
# task_struct         512      540    9088    3    8 : ...
# dentry            40320    41328     192   21    1 : ...
# kmalloc-32        20480    20480      32  128    1 : ...
```

欄位解讀：`active_objs`（正在用的物件數）、`num_objs`（總格子數）、`objsize`（每物件 bytes）、`objperslab`（一個 slab 切幾格）、`pagesperslab`（一個 slab 用幾頁）。`kmalloc-32` 那行 `objperslab=128`、`pagesperslab=1` 正好印證：一頁 4096 / 32 = 128 格。

### 用 slabtop 看即時排名

```bash
sudo slabtop
# 依記憶體佔用排序，即時看哪個 cache 吃最多、成長最快
# 按 c 依 cache 大小排、按 a 依 active 物件數排
```

排查記憶體洩漏時，`slabtop -o` 跑幾次看哪個 cache 一直漲，通常能定位到洩漏的物件型別。

### 寫模組建自訂 cache 並觀測

```c
// slab_demo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>

struct my_obj {
    int   id;
    char  payload[100];
};

static struct kmem_cache *my_cachep;
static struct my_obj *objs[64];

static int __init slab_demo_init(void)
{
    int i;

    // 建一個專用 cache；名字會出現在 /proc/slabinfo
    my_cachep = kmem_cache_create("my_obj_cache",
            sizeof(struct my_obj),
            0,                       // 對齊：0 = 用預設
            SLAB_HWCACHE_ALIGN,      // 對齊到 cache line
            NULL);                   // 沒有建構子
    if (!my_cachep)
        return -ENOMEM;

    // 配一批，讓 slabinfo 有東西看
    for (i = 0; i < 64; i++) {
        objs[i] = kmem_cache_alloc(my_cachep, GFP_KERNEL);
        if (objs[i])
            objs[i]->id = i;
    }

    pr_info("slab_demo: allocated 64 my_obj (%zu bytes each)\n",
            sizeof(struct my_obj));
    pr_info("slab_demo: check `grep my_obj_cache /proc/slabinfo`\n");
    return 0;
}

static void __exit slab_demo_exit(void)
{
    int i;
    for (i = 0; i < 64; i++)
        if (objs[i])
            kmem_cache_free(my_cachep, objs[i]);

    kmem_cache_destroy(my_cachep);   // 銷毀前必須 free 掉所有物件，否則 warn
    pr_info("slab_demo: freed and destroyed cache\n");
}

module_init(slab_demo_init);
module_exit(slab_demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("slub observation demo for kernel_internals Ch18");
```

Makefile 照 Ch 0。載入後在 QEMU 裡：

```
/ # insmod /slab_demo.ko
slab_demo: allocated 64 my_obj (104 bytes each)
/ # grep my_obj_cache /proc/slabinfo
my_obj_cache   64   ...   104 ...     ← 看到你剛建的 cache
/ # rmmod slab_demo
```

`sizeof(struct my_obj)` 是 104（4 + 100），但 slabinfo 的 `objsize` 可能被對齊/加 metadata 撐大——這正好讓你看到 slub 怎麼在你要求的大小上加料。

### 用 gdb 看 freelist（進階）

在 Ch 0 的 gdb 環境裡，載入模組後：

```gdb
(gdb) print *my_cachep
(gdb) print my_cachep->offset          # freelist 指標存在物件內哪個位移
(gdb) print my_cachep->cpu_slab        # per-CPU 結構
```

配合 `lx-slabinfo`（如果你的 `vmlinux-gdb.py` 提供）或手動走 `cpu_slab->freelist` 這條鏈，能親眼看到「空閒物件內嵌指標」串起來的樣子——把上面 ASCII 圖變成真的記憶體內容。

## 對比與取捨

| 面向 | 專用 cache（`kmem_cache_create`） | 通用 kmalloc cache |
|---|---|---|
| 大小 | 精確符合物件 | 向上取整到 2 的次方桶，有內部浪費 |
| 觀測 | `/proc/slabinfo` 有獨立名字 | 混在 `kmalloc-N` 裡，難分辨誰在用 |
| 對齊/建構子 | 可自訂 | 不可 |
| 安全 | 可設不可合併、隔離敏感物件 | 大量不同型別擠在同桶，攻擊面大 |
| 適用 | 高頻、固定型別（task/inode/dentry） | 臨時、大小不定的小配置 |

| 面向 | slab（已移除） | slub（現行） | slob（已移除） |
|---|---|---|---|
| metadata 開銷 | 大 | 極小（塞進 struct slab） | 最小 |
| 擴展性 | 差（per-CPU queue 難維護） | 好 | 差 |
| 除錯支援 | 一般 | 好（`slub_debug`） | 幾乎無 |
| 記憶體極省環境 | 否 | 一般 | 是（但已被移除） |

## 踩雷集錦

1. **「free 完記憶體就還給系統了」——錯**。slub 把變空的 slab 留在 partial list 當快取，不積極還 buddy。`free` 大量物件後系統 `MemFree` 沒漲是正常的，不是洩漏。要判斷洩漏看 `slabinfo` 的 `active_objs` 有沒有持續不降。

2. **「`kmalloc(33)` 給我 33 bytes」——錯**。給的是 `kmalloc-64` 桶的 64 bytes，你多拿了 31 bytes 可用空間（`ksize()` 會告訴你真實可用大小）。依賴「剛好 33 bytes」的邊界假設在通用配置上不成立。

3. **「不同 cache 的物件一定住在不同記憶體」——錯**。cache merge 會讓大小相近的 cache 共用 slab。這是很多人在分析 UAF / 佔坑時想不通「怎麼配一個 A 型物件會佔到 B 型物件的坑」的原因——因為它們被 merge 進同一個 freelist。

4. **`kmem_cache_destroy` 前沒 free 完所有物件**。還有物件在用時銷毀 cache，slub 會印 warning 且拒絕釋放底層 slab，造成洩漏。銷毀前務必把該 cache 配出去的物件全 free。

5. **在中斷/持鎖 context 用 `GFP_KERNEL` 配 slab**。slab 快路徑不睡，但慢路徑（要向 buddy 進新 slab）會走 reclaim、可能睡（Ch 6 的 GFP 規則同樣適用）。原子 context 要用 `GFP_ATOMIC`，否則慢路徑觸發時會 `might_sleep` 警告甚至死鎖。

## 進階：再往深一層

- **`slub_debug` 開機參數**：`slub_debug=FZPU`（或針對單一 cache `slub_debug=,dentry`）開啟 red-zone（物件前後放哨兵，偵測 overflow）、poison（free 後填 `0x6b`，偵測 UAF 讀）、tracking（記每次 alloc/free 的呼叫堆疊）。這是不上 KASAN 時最順手的 slub 記憶體錯誤偵測。生產不開（有效能成本），除錯必備。

- **KASAN 與 slub 的關係**：KASAN（Ch 53）在 slub 的物件周圍放 redzone、用 quarantine 延遲 free 物件的重用，正是為了抓 slab 上的 overflow / UAF。理解 slub 的 freelist 就理解了 KASAN 為什麼要攔截 `kmem_cache_alloc/free`。

- **`SLAB_TYPESAFE_BY_RCU`**：一個特殊 flag，讓物件被 free 後在一個 RCU grace period 內**型別仍然安全**（記憶體可能被 slub 重用給同 cache 的另一個物件，但不會被還給 buddy 變成完全不同的東西）。這是 `sk_buff`、某些 VFS 物件用來做無鎖 lookup 的技巧，和 Ch 27（RCU）深度綁定。面試常問「free 後為什麼還能安全讀」——答案就在這個 flag 的語意。

- **per-CPU partial（`CONFIG_SLUB_CPU_PARTIAL`）**：slub 還有一層在 per-CPU active slab 和 per-node partial 之間的 per-CPU partial slab 快取，進一步減少碰 `list_lock` 的次數。這章為求清晰略過了它，讀 `___slab_alloc` 時會遇到 `c->partial`，那就是它。

- **cross-cache 攻擊（防禦視角）**：當敏感物件被隔離進不可合併的專用 cache，攻擊者無法在同 cache 佔坑，於是轉而攻擊「slab 這個**頁**本身」——把目標 cache 的一個 slab free 回 buddy，再讓 buddy 把同一批 page 分給另一個可控 cache，於是跨過了 cache 邊界。防禦方的對策是 `CONFIG_SLAB_VIRTUAL` 之類「不重用 slab 虛擬位址」的提案。這是 `kernel_pwn` 課的高階題，在這裡你先建立「為什麼 cache 隔離不是萬靈丹」的認識。

## 動手練習

1. **數格子**：`grep kmalloc-64 /proc/slabinfo`，把 `objperslab × objsize` 算出來，對照 `pagesperslab × 4096`，驗證 slub 怎麼把頁切成格子、剩多少當 metadata。

2. **看 merge**：`grep ':t-' /proc/slabinfo`（或 `ls /sys/kernel/slab/ | grep '^:'`），找出被 merge 的 cache 別名。再用 `slab_nomerge` 開機參數重開，對比 `/proc/slabinfo` 行數變多——親眼看到 merge 的效果。

3. **弄壞它（防禦視角）**：在 `slab_demo` 模組裡故意製造 overflow——`memset(objs[0]->payload, 0x41, 200)`（寫超過 100 bytes 的 payload）。開 `slub_debug=FZ` 開機，載入模組，看 kernel 有沒有印出 red-zone overwrite 的偵測訊息。這讓你看到 slub_debug 怎麼抓到 overflow，也預習 `kernel_pwn` 的 overflow 是打在什麼結構上。

4. **freelist LIFO 驗證**：配兩個物件 A、B，free A 再 free B，然後再配一個——用 gdb 或印位址確認拿到的是 B（後進先出）。這解釋了 heap spray 為什麼可預測。

5. **RCU cache 的生死**：讀 `mm/slub.c` 裡 `SLAB_TYPESAFE_BY_RCU` 的處理，找出「free 到真正還給 buddy 之間」多插了什麼（提示：`call_rcu`）。對照 Ch 27。

## 本章重點整理

- slab 層墊在 buddy 之上，把整頁切成同大小物件格子，攤銷配置成本、對齊 cache line；v6.12 唯一實作是 slub（`mm/slub.c`），舊 slab/slob 已移除。
- slub 三層：`kmem_cache`（一種物件的總管）→ per-CPU `kmem_cache_cpu`（active slab + freelist，免鎖快路徑）→ per-node `kmem_cache_node`（partial list，加鎖慢路徑）→ 再空才找 buddy。
- freelist 用「空閒物件內嵌 next 指標」串單向鏈、LIFO 取用——這是 kernel heap 攻擊（UAF/overflow 改寫 freelist 指標、spray）的物理基礎；`CONFIG_SLAB_FREELIST_HARDENED`（指標混淆）與 `CONFIG_SLAB_FREELIST_RANDOM`（初始順序打亂）是對應防禦。
- 通用 `kmalloc-N` 按 2 次方分桶（向上取整），專用 cache 精確且可隔離；cache **merge** 讓不同型別擠同 freelist，是佔坑/UAF exploit 成立的關鍵，防禦上把敏感物件隔離進不可合併的專用 cache。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼有了 buddy 還需要 slab，以及 slab 解決的三個問題（碎片、成本、cache 局部性）
- [ ] 能畫出 slub 三層結構圖，並說出快路徑為什麼免鎖、慢路徑在哪裡加鎖
- [ ] 能解釋「空閒物件內嵌 freelist 指標」的機制，以及 UAF / overflow 怎麼利用它做任意配置
- [ ] 面試被問「`CONFIG_SLAB_FREELIST_HARDENED` 防住什麼、沒防住什麼」，你能答出「讓覆寫後的指標不可控（降級成 crash），但不阻止覆寫本身」
- [ ] 能說明 cache merge 對 exploit 佔坑的影響，以及為什麼把敏感物件放進不可合併 cache 是防禦
- [ ] 能寫模組 `kmem_cache_create` 建自訂 cache 並在 `/proc/slabinfo` 觀測，用 `slub_debug` 抓到一個 overflow

## 延伸閱讀

### 官方文件

- **[Documentation/mm/slub.rst](https://www.kernel.org/doc/html/latest/mm/slub.html)**
  - **讀哪裡**：整篇，尤其 `slub_debug` 參數表與 sysfs（`/sys/kernel/slab/<cache>/`）欄位說明
  - **和本章的關聯**：本章動手節的觀測方法與 debug 參數都以這篇為準；想調 slub 行為回這查

- **[Documentation/core-api/memory-allocation.rst](https://www.kernel.org/doc/html/latest/core-api/memory-allocation.html)**
  - **讀哪裡**：kmalloc 與 slab 的使用約定
  - **能學到什麼**：從 API 使用者角度補齊本章底層視角沒細講的「什麼時候該建專用 cache」

### 原始論文 / 經典

- **《The Slab Allocator: An Object-Caching Kernel Memory Allocator》** — Jeff Bonwick（USENIX 1994）
  - **這是什麼**：slab 概念的源頭論文（Solaris），object caching、cache colouring 的原始論證
  - **為什麼值得讀**：理解 slub 是在優化什麼——它砍掉的 per-CPU queue、colouring 都在這篇被提出。讀完你會懂 slub「Unqueued」這個名字的分量

- **[The SLUB allocator](https://lwn.net/Articles/229984/)** — Jonathan Corbet, LWN（2007）
  - **讀哪裡**：整篇，slub 引入時的設計說明
  - **前提**：讀完本章結構節再讀，會很順

### 安全視角（連 kernel_pwn）

- **[Exploiting the Linux kernel via freelist（各家 CTF writeup / kernelctf 報告）]**
  - **這是什麼**：freelist 攻擊、cross-cache、SLUB hardening 繞過的實戰材料
  - **和本章的關聯**：本章給你防禦方的結構理解，這些材料給你攻擊方的操作細節；兩邊對照才完整。優先讀 Google kernelCTF 的公開 exploit 說明與 hardening 討論，別讀來路不明的農場文

下一章我們從「配置器怎麼給記憶體」上升到「一個 process 的位址空間長什麼樣」——`mm_struct`、VMA 怎麼描述一段段虛擬記憶體、以及存取一個還沒對應實體頁的位址時 page fault 怎麼補上。slub 配出來的物件、buddy 給的頁，最終都要掛進某個位址空間才有意義。

→ [Ch 19 mm_struct、VMA 與 page fault](./19-mm-struct-vma-fault.md)
