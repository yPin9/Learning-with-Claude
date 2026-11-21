# 練習 D — 替一個子系統畫出完整的資料結構地圖

> **目標**：把 Ch 23–27 學到的辨識技巧用在一個真實、約 2600 行的子系統上——v6.18 的 `fs/eventpoll.c`。產出一張資料結構地圖：每個 struct、每個成員指向誰、哪條 list/tree 串什麼型別的物件、經由哪個成員、誰持有哪把鎖保護哪些欄位。完成後你驗證的能力是：**不從第一行讀到最後一行，只靠資料結構與它們的增刪點，就說得出一個子系統怎麼運作**。

> **鴻溝**：本章主要練**目標鴻溝**（帶著「這個欄位是誰在改」的問題讀，而不是順著檔案讀）與**間接鴻溝**（`ep_poll_callback` 被誰呼叫？它不在 eventpoll.c 的任何呼叫鏈上）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）、建好的 `~/code_reading/build/vmlinux`（含 DWARF5 與 BTF）、pahole 1.25、ripgrep、gcc 11.4；WSL2 Ubuntu 22.04，執行中的 kernel 為 6.18 系列（`6.18.58.1-microsoft-standard-WSL2`）。

## 背景與動機

[Ch 5](./05-entry-points-and-data-first.md) 講過「資料結構優先」：面對陌生的子系統，先找出它的核心 struct，弄清楚它們之間的指向關係，程式碼就讀懂了一半。這個練習要你實際做一次。

為什麼選 eventpoll？

- **它幾乎用到了 Part 4 的每一種結構**：rbtree（cached）、`list_head`、`hlist`、手寫的單向鏈、wait queue（本質是 list）、per-cpu 計數器、指標低位元標記的變形（`EP_UNACTIVE_PTR`）。
- **它的物件同時掛在好幾個結構上**，正好考驗 Ch 23 的核心技能：「這個成員串在哪條 list 上？」
- **它有三層鎖，而且其中一層必須在 IRQ context 可用**，資料結構地圖如果不標鎖，就看不懂它為什麼要有兩條 ready list。
- 你每天都在用它：nginx、Node.js、Redis、Go runtime 的網路層全建在 epoll 上。讀懂資料結構，就能解釋 level-triggered 與 edge-triggered 的行為差異從哪來。
- [Final Project](./final-project-pipe-eventfd-guided-reading.md) 要讀的 pipe 與 eventfd 都會被 epoll 監看，這張地圖到時候會直接用上。

這是 kernel 開發者接手一個子系統、審一個大 patch、或在 syzbot 報告裡追 use-after-free 時真正會做的事：先畫圖。

## 任務規格

### 範圍

| 項目 | 規格 |
|---|---|
| 語料 | v6.18 `fs/eventpoll.c`（2630 行），以及它引用的 `include/linux/fs.h` 中 `struct file` 的 `f_ep` 欄位、`include/linux/wait.h` 的 wait queue 定義 |
| 必須涵蓋的 struct | `struct eventpoll`、`struct epitem`、`struct eppoll_entry`、`struct epoll_filefd`、`struct epitems_head`、`struct file`（只需 `private_data`、`f_ep`、`f_lock`）、`wait_queue_head_t`、`wait_queue_entry_t` |
| 可以略過 | busy poll（`CONFIG_NET_RX_BUSY_POLL` 相關欄位）、wakeup source（`ws` 欄位）、loop/path 檢查的細節（`ep_loop_check`、`reverse_path_check`）、compat syscall |
| 禁止 | 先上網找別人畫好的 epoll 圖。先自己畫，畫完再對照參考解答 |

### 產出一：資料結構地圖

一份文字或圖（ASCII、手繪拍照、draw.io 皆可），包含：

1. **每個 struct 的關鍵成員**，標出型別與用途（一句話）。
2. **指向關係**：每個指標成員指向哪個 struct 的哪個實例。
3. **容器關係**：每個 `list_head`/`rb_root`/`hlist_head`/單向鏈 head，標出
   - 它串的是**什麼型別**的物件
   - 經由該物件的**哪個成員**
   - 證據：至少一個插入點與一個走訪點或刪除點的 `檔案:行號`
4. **鎖的覆蓋範圍**：每把鎖（`ep->mtx`、`ep->lock`、`file->f_lock`、`epnested_mutex`、wait queue head 自己的 `lock`）保護哪些欄位或容器，附證據（註解或 `lockdep_assert`、`spin_lock` 包住的程式碼）。

### 產出二：回答問題清單

1. 使用者拿到的 epoll fd，怎麼找到對應的 `struct eventpoll`？
2. 一個 `struct epitem` 同時可能掛在幾個容器上？分別經由哪個成員？
3. rbtree `ep->rbr` 的 key 是什麼？用這個 key 能解釋 `dup()` 出來的 fd 為什麼可以加進同一個 epoll 兩次嗎？
4. ready list `ep->rdllist` 為什麼由 spinlock `ep->lock` 保護，而 rbtree 由 mutex `ep->mtx` 保護？
5. `ep->ovflist` 是什麼？它什麼時候「啟用」？`EP_UNACTIVE_PTR` 和 `NULL` 在這條鏈上各代表什麼？
6. 被監看的檔案（例如一個 pipe）有資料時，kernel 怎麼從那個 pipe 的 wait queue 一路找回對應的 `epitem`？寫出指標轉換的每一步。
7. `ep->wq` 和 `ep->poll_wait` 都是 wait queue head，各自是誰在上面等？
8. `struct file` 的 `f_ep` 為什麼是 `struct hlist_head *`（指標），而不是內嵌一個 `struct hlist_head`？
9. Level-triggered 和 edge-triggered 的行為差異，在資料結構操作上對應到哪一行程式碼？
10. `struct epitem` 的註解說「we do not want this to take another cache line」。用 pahole 查它在 v6.18 x86_64 上的實際大小，你怎麼解讀這句註解？
11. 每個使用者能加入 epoll 的 fd 數量上限是怎麼算的？計數器用的是什麼資料結構？

### 驗收標準

- 地圖上每一條容器關係都附有 `檔案:行號` 證據，且插入點與走訪點使用的成員一致。
- 11 個問題都有答案，每個答案能指出依據的程式碼位置。
- 至少用兩種**非閱讀原始碼**的方式驗證地圖的一部分（pahole、user-space 程式觀察行為、`/proc/<pid>/fdinfo`、gdb 皆可）。

## 期望輸出範例

地圖的一個片段應該長這樣（格式不拘，但資訊密度要到這個程度）：

```
struct eventpoll
  rbr : rb_root_cached ──串──▶ struct epitem，經由 epitem::rbn
        key = (epi->ffd.file, epi->ffd.fd)，比較函式 ep_cmp_ffd (eventpoll.c:358)
        插入 ep_rbtree_insert (eventpoll.c:1385)；查找 ep_find (1174)；刪除 __ep_remove (869)
        保護：ep->mtx（ep_find 註解 1170-1172："must be called with "mtx" held"）
```

驗證的片段：

```
$ pahole -C epoll_filefd ~/code_reading/build/vmlinux
struct epoll_filefd {
	struct file *              file;                 /*     0     8 */
	int                        fd;                   /*     8     4 */

	/* size: 12, cachelines: 1, members: 2 */
	/* last cacheline: 12 bytes */
} __attribute__((__packed__));
```

→ 結論：rbtree key 是 12 bytes 的 packed struct（指標 + int），`__packed` 讓 `epitem` 省下 4 bytes 的 padding。

## 如果你卡住了

1. **不知道從哪個 struct 開始**：從使用者看得到的東西開始。`epoll_create1()` 回傳一個 fd，fd 對應一個 `struct file`。在 eventpoll.c 裡找 `anon_inode_getfile`，看它把什麼東西塞進 file。
2. **一個 `list_head` 成員不知道是 head 還是 node**：套 Ch 23 的四步驟。grep `list_add\w*\(.*rdllink` 和 `list_add\w*\(.*rdllist`，看它們出現在第一個參數還是第二個參數。
3. **`ep_poll_callback` 找不到呼叫者**：它不是被直接呼叫的。找它的位址被存到哪裡（`init_waitqueue_func_entry`），再想：那個 `wait_queue_entry_t` 被加進了誰的 wait queue？呼叫者是那個 wait queue 的 `wake_up`——它在 pipe.c、tcp.c、eventfd.c，不在 eventpoll.c。這是 [Ch 28](./28-ops-tables-indirect-calls.md) 間接鴻溝的預告。
4. **搞不清楚哪把鎖保護什麼**：三個線索——檔頭 40–84 行的 LOCKING 大註解、每個函式開頭的 `lockdep_assert_*`、以及 `spin_lock(&ep->lock)` 和 `spin_unlock` 之間到底碰了哪些欄位。註解說的和程式碼做的要一致；不一致時相信程式碼，並記下來。
5. **`ovflist` 的邏輯看不懂**：先不要讀 `ep_poll_callback`。先讀 `ep_start_scan` 和 `ep_done_scan`（733–798 行），弄清楚「把 `ovflist` 設成 `NULL`」和「設回 `EP_UNACTIVE_PTR`」中間發生了什麼事，再回去看 callback 怎麼根據 `ovflist` 的值選擇要把 `epi` 放到哪裡。

## 實作步驟建議

### Step 1：列出所有 struct 與它們的關鍵成員（30 分鐘）

讀 eventpoll.c 的 102–290 行（所有 struct 定義與全域變數都在這裡），以及 `struct file` 中與 epoll 相關的欄位：

```
$ rg -n 'f_ep' include/linux/fs.h
1185: * @f_lock: Protects f_ep, f_flags. Must not be taken from IRQ context.
1204: * @f_ep: link of all epoll hooks for this file
1241:	struct hlist_head		*f_ep;
```

子目標：一張表，列出每個 struct 的每個成員、型別、一句話用途。這時還不需要畫箭頭。

### Step 2：用 pahole 確認實際 layout

```
$ pahole -C eventpoll ~/code_reading/build/vmlinux
$ pahole -C epitem ~/code_reading/build/vmlinux
$ pahole -C eppoll_entry ~/code_reading/build/vmlinux
$ pahole -C wait_queue_entry ~/code_reading/build/vmlinux
$ pahole -C wait_queue_head ~/code_reading/build/vmlinux
```

子目標：確認哪些 `#ifdef` 欄位在這個 config 下存在（例如 `nests` 只在 `CONFIG_DEBUG_LOCK_ALLOC` 下才有）、哪些成員共用 union、每個 struct 的大小。pahole 看的是**實際編譯出來的東西**，可以糾正你讀原始碼時漏看的 `#ifdef`（[Ch 50](./50-compiler-as-reading-tool.md)）。

### Step 3：替每個容器找證據

對每個容器（`rbr`、`rdllist`、`ovflist`、`pwqlist`、`f_ep`、`refs`、wait queue）跑 grep：

```
$ cd ~/code_reading/linux
$ rg -n 'list_add|list_del|list_splice|list_move|hlist_add|hlist_del|rb_erase|rb_insert|ep_rbtree_insert|->ovflist\s*=|->next\s*=|pwqlist\s*=|add_wait_queue|init_waitqueue_func_entry|wake_up' fs/eventpoll.c
```

子目標：每個容器至少一個插入點、一個刪除或走訪點，並核對插入時和走訪時用的成員相同。

### Step 4：標上鎖

讀檔頭的 LOCKING 註解（40–84 行），然後對每個在 Step 3 找到的插入/刪除點，往上找它被哪把鎖包住。

子目標：一張「鎖 → 保護的欄位/容器」對照表，每一列都有證據。

### Step 5：追一個事件的完整路徑

在地圖上用不同顏色（或編號）畫出這條路徑：

```
pipe 被寫入 → pipe 的 wait queue 被 wake_up → ep_poll_callback → epitem 進入 rdllist（或 ovflist）
→ ep->wq 被喚醒 → epoll_wait 的 task 醒來 → ep_send_events 把 epitem 從 rdllist 拿出來 → copy 到 user-space
→（LT 的話）epitem 被放回 rdllist
```

子目標：路徑上每一步都能對應到地圖上的一條箭頭或一個容器。對不上的地方，就是地圖還缺東西。

### Step 6：用行為驗證

寫兩個 user-space 小程式（參考下面的測試用例），用觀察到的行為驗證地圖的兩個部分：rbtree 的排序 key、LT/ET 在 rdllist 上的差異。

### Step 7：回答問題清單，再對照參考解答

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考地圖與問題解答</summary>

### 參考地圖

```
 user-space: epfd ──(fd table)──▶ struct file "[eventpoll]"
                                    │ private_data ─────────────────────┐   (anon_inode_getfile, eventpoll.c:2192)
                                    │ f_ep ── 若此 epoll 本身被別的 epoll 監看，指向 ep->refs
                                    ▼                                   ▼
 ┌──────────────────────────── struct eventpoll (192 bytes) ──────────────────────────────┐
 │ mtx        : struct mutex      ── 保護 rbr、epitem 的增刪、ep_send_events 整個掃描過程   │
 │ wq         : wait_queue_head   ── epoll_wait() 的 task 在這裡睡 (ep_poll, 2022)        │
 │ poll_wait  : wait_queue_head   ── 「別人 poll 這個 epoll fd」時在這裡等 (984)          │
 │                                   即巢狀 epoll：外層 epoll 的 eppoll_entry 掛在這裡     │
 │ rdllist    : list_head ──串──▶ epitem，經由 epitem::rdllink   [保護：ep->lock]        │
 │ lock       : spinlock_t        ── 保護 rdllist、ovflist（struct 註解 194 行）          │
 │ rbr        : rb_root_cached ──串──▶ epitem，經由 epitem::rbn   [保護：ep->mtx]        │
 │              key = (ffd.file, ffd.fd)，ep_cmp_ffd (358)                                │
 │ ovflist    : epitem * ──單向鏈──▶ epitem，經由 epitem::next    [保護：ep->lock]       │
 │              EP_UNACTIVE_PTR = 未啟用；NULL = 已啟用且為空（也是鏈尾）                 │
 │ refs       : hlist_head ──串──▶ 「監看本 epoll 的外層 epitem」，經由 epitem::fllink    │
 │              （本 epoll file 的 f_ep 指向這裡，attach_epitem 1537）                    │
 │ user       : user_struct * ──▶ user->epoll_watches（percpu_counter，watch 數上限）     │
 │ file       : file * ──▶ 回指自己的 struct file                                         │
 │ refcount   : refcount_t        ── 與 epitem->dying 一起決定 eventpoll 何時釋放         │
 └────────────────────────────────────────────────────────────────────────────────────────┘
                     │ rbr / rdllist / ovflist 串的都是同一種物件 ▼
 ┌────────────────────────── struct epitem (120 bytes) ─────────────────────────┐
 │ union { rbn : rb_node   ← 掛在 ep->rbr                                         │
 │         rcu : rcu_head  ← 移出 rbtree 後，kfree_rcu 用 (__ep_remove 884) }     │
 │ rdllink : list_head      ← 掛在 ep->rdllist 或 ep_send_events 的區域 txlist     │
 │                            list_empty(rdllink) == 「不在 ready list 上」(ep_is_linked 366) │
 │ next    : epitem *       ← 掛在 ep->ovflist；不在鏈上時 = EP_UNACTIVE_PTR (1593) │
 │ ffd     : epoll_filefd {file*, fd} __packed   ← rbtree key                     │
 │ dying   : bool           ← 由 file->f_lock 保護                                 │
 │ pwqlist : eppoll_entry * ──單向鏈──▶ eppoll_entry，經由 eppoll_entry::next      │
 │ ep      : eventpoll *    ──▶ 所屬的 eventpoll                                   │
 │ fllink  : hlist_node     ← 掛在 「被監看的 file」->f_ep 指向的 hlist 上          │
 │ event   : epoll_event    ← 使用者要的事件遮罩與 data                           │
 └────────────────────────────────────────────────────────────────────────────────┘
          │ pwqlist                                      │ ffd.file
          ▼                                              ▼
 ┌──── struct eppoll_entry (64 bytes) ────┐      struct file（被監看者，例如 pipe）
 │ next  : eppoll_entry * （同一 epi 的下一個）│        │ f_ep ──▶ hlist_head ──串──▶ epitem，經由 fllink
 │ base  : epitem * ──▶ 回指所屬 epitem    │        │          （由 file->f_lock 保護；head 本身是
 │ wait  : wait_queue_entry_t              │        │           epitems_head 的一部分，按需配置）
 │         .func = ep_poll_callback        │        │ f_op->poll() 時呼叫 ep_ptable_queue_proc
 │         .entry ──掛在──▶ *whead          │        ▼
 │ whead : wait_queue_head_t * ─────────────┼──▶ 被監看者的 wait queue head（例如 pipe->rd_wait）
 └──────────────────────────────────────────┘     由 whead->lock 保護

 全域：epnested_mutex ── 保護巢狀插入時的迴圈偵測、tfile_check_list（epitems_head 的單向鏈）
       epi_cache / pwq_cache / ephead_cache ── 三個 slab cache
```

### 容器證據表

| 容器 | 串什麼、經由 | 插入 | 刪除/走訪 | 保護 |
|---|---|---|---|---|
| `ep->rbr` | `epitem`，`rbn` | `ep_rbtree_insert`（1385–1404），由 `ep_insert` 1613 呼叫 | `ep_find`（1174）；`rb_erase_cached`（869）；`ep_show_fdinfo` 走訪（1078） | `ep->mtx`（`ep_find` 註解 1170–1172；`ep_insert` 1609–1612 的註解） |
| `ep->rdllist` | `epitem`，`rdllink` | `list_add_tail`（1292 callback、1668 insert、1744 modify、1845 send_events LT） | `list_del_init`（873、1003、1811）；`list_splice_init` 整條搬走（745） | `ep->lock`（struct 註解；callback 在 `spin_lock_irqsave` 內） |
| `ep->ovflist` | `epitem`，`next`（LIFO 單向鏈） | callback 1285–1287 | `ep_done_scan` 761–777 逐一移回 rdllist | `ep->lock` |
| `epi->pwqlist` | `eppoll_entry`，`next` | `ep_ptable_queue_proc` 1381–1382 | `ep_unregister_pollwait`（684 起） | `ep->mtx`（只在 insert/remove 路徑上改） |
| `file->f_ep` | `epitem`，`fllink` | `attach_epitem` 1555 `hlist_add_head_rcu` | `__ep_remove` 865 `hlist_del_rcu`；`eventpoll_release_file` 1125–1126 | `file->f_lock`（`fs.h:1185` 註解；847、1124、1545 的 `spin_lock(&file->f_lock)`） |
| `ep->refs` | 外層 `epitem`，`fllink` | 同上（epoll file 的 `f_ep` 指向 `&ep->refs`，1537） | `hlist_for_each_entry_rcu(epi, &ep->refs, fllink)`（2114） | `file->f_lock` 寫；RCU 讀 |
| 被監看者的 wait queue | `wait_queue_entry_t`，`eppoll_entry::wait.entry` | `add_wait_queue(whead, &pwq->wait)` 1378/1380 | `ep_remove_wait_queue`（663）；POLLFREE 時 1341 | `whead->lock`（wait queue 自己的鎖） |
| `ep->wq` | 睡在 `epoll_wait` 的 task | `__add_wait_queue_exclusive(&ep->wq, &wait)` 2022 | `wake_up(&ep->wq)`（1318–1320、793、1673…） | `ep->lock`（2022 在 `spin_lock_irq(&ep->lock)` 內，註解 2007–2011 解釋為何不需 barrier） |

### 鎖的覆蓋範圍

| 鎖 | 保護 | 為什麼是這種鎖 |
|---|---|---|
| `epnested_mutex`（全域） | 巢狀 epoll 的迴圈偵測、`tfile_check_list` | 極少發生，用全域鎖換簡單（檔頭註解 61–68） |
| `ep->mtx`（mutex） | `rbr`、epitem 的建立與移除、`ep_send_events` 整段（期間會 `copy_to_user`，可能睡） | 要能睡（page fault），所以不能是 spinlock |
| `ep->lock`（spinlock，關中斷） | `rdllist`、`ovflist`、`ep->wq` 上的排隊判斷 | `ep_poll_callback` 從 `wake_up()` 被呼叫，可能在 IRQ context，不能睡 |
| `file->f_lock` | `file->f_ep` 那條 hlist、`epi->dying` | 是 `struct file` 的鎖，註解寫明「Must not be taken from IRQ context」 |
| `whead->lock` | 被監看者 wait queue 上的 entry | wait queue 自帶的鎖，`wake_up` 時已持有——所以 `ep_poll_callback` 執行時，呼叫者手上拿著 `whead->lock` |

取得順序：`epnested_mutex` → `ep->mtx` → `ep->lock`（檔頭 47–51 行）。

### 問題解答

**1.** `do_epoll_create` 呼叫 `anon_inode_getfile("[eventpoll]", &eventpoll_fops, ep, ...)`（2192），第三個參數會被存成 `file->private_data`。之後每個 epoll 系統呼叫都是 `fd → struct file → private_data → struct eventpoll`（例如 `ep_show_fdinfo` 1074：`struct eventpoll *ep = f->private_data;`）。

**2.** 最多同時四個：
- `ep->rbr`，經由 `rbn`（只要 epitem 存在就在）
- `ep->rdllist` 或 `ep_send_events` 的區域 `txlist`，經由 `rdllink`（有事件時）
- `ep->ovflist`，經由 `next`（事件發生在 `ep_send_events` 掃描期間時）
- 被監看 file 的 `f_ep` hlist，經由 `fllink`（只要 epitem 存在就在）

另外，它擁有的每個 `eppoll_entry` 都掛在被監看者的 wait queue 上（一個 file 可能有多個 wait queue，例如讀/寫分開，所以 `pwqlist` 是鏈）。`rdllink` 和 `next` 可以同時「在鏈上」嗎？`ep_done_scan` 761–777 的註解說會發生：掃描期間 txlist 裡的 epitem 又觸發事件，被加進 ovflist；回收時用 `ep_is_linked` 檢查避免重複加入。

**3.** key 是 `(struct file *, int fd)`，`ep_cmp_ffd`（358–363）先比 file 指標、再比 fd。`dup()` 出來的 fd 指向同一個 `struct file`，但 fd 號碼不同，所以是不同的 key，可以各加一次。測試用例 3 實際觀察到了這點，而且 `/proc/self/fdinfo` 印出的順序（由 `ep_show_fdinfo` 中序走訪 rbtree）把同一個 file 的兩個 fd 排在一起、fd 小的在前。

**4.** rbtree 的修改只發生在 `epoll_ctl` 和 file 關閉時，都在 process context，而且 `ep_send_events` 需要在掃描期間阻止 epitem 被刪除，同時又要 `copy_to_user`（可能 page fault 而睡）——只能用 mutex。`rdllist` 則會被 `ep_poll_callback` 修改，而 callback 是被監看者的 `wake_up()` 叫起來的，可能在硬中斷或 softirq（例如網卡收包喚醒 socket），不能睡，所以必須是關中斷的 spinlock。檔頭 52–60 行的註解就是在解釋這件事。

**5.** `ovflist` 是「`ep_send_events` 正在掃描、沒有持有 `ep->lock` 時」發生的事件的暫存區。流程：
- `ep_start_scan`（733）：持 `ep->lock` 把整條 `rdllist` 搬到區域 `txlist`，並把 `ovflist` 設成 `NULL`（啟用）。
- 掃描期間 callback 看到 `ovflist != EP_UNACTIVE_PTR`（1284），就不碰 `rdllist`，改把 epitem 推進 `ovflist`（LIFO）。
- `ep_done_scan`（750）：持 `ep->lock` 把 `ovflist` 上的 epitem 用 `list_add`（加在頭，順便把 LIFO 反轉回 FIFO，註解 770–773）移回 `rdllist`，再把 `ovflist` 設回 `EP_UNACTIVE_PTR`，最後把 `txlist` 剩下的接回去。

`EP_UNACTIVE_PTR` 是 `((void *) -1L)`（98 行）。在 `ep->ovflist` 上它代表「沒有在掃描，請直接用 rdllist」；在 `epi->next` 上它代表「這個 epitem 目前不在 ovflist 上」（1593 初始化、1285 檢查）。`NULL` 則是「已啟用但目前為空」以及鏈尾。之所以需要一個不是 `NULL` 的哨兵值，是因為 `NULL` 已經被拿來當鏈尾了——這是 Ch 23 毒指標、Ch 25 nulls marker 同一類的「特殊指標值帶語意」技巧。

為什麼不直接讓 callback 加進 `txlist`？因為 `ep_send_events` 在不持鎖的情況下走訪 `txlist`（1785 的註解：「We can loop without lock because we are passed a task private list」），callback 一碰它就是資料競爭。

**6.** 以 pipe 為例：
```
pipe 寫入端呼叫 wake_up_interruptible_sync_poll(&pipe->rd_wait, ...)
  → 走訪 rd_wait.head 上的每個 wait_queue_entry_t，呼叫 entry->func
  → func 是 ep_poll_callback（ep_ptable_queue_proc 1374 設定）
  → ep_poll_callback(wait, ...)：
       ep_item_from_wait(wait)                         (377-380)
         = container_of(wait, struct eppoll_entry, wait)->base
           ① wait_queue_entry_t* ─container_of─▶ eppoll_entry*（wait 是 eppoll_entry 的成員）
           ② eppoll_entry->base ─▶ epitem*
       epi->ep ─▶ eventpoll*                           ③
       spin_lock_irqsave(&ep->lock) 後 list_add_tail(&epi->rdllink, &ep->rdllist)
```
Ch 16 的 `container_of` 在這裡從 wait queue 的通用結構跳回 epoll 的私有結構，再靠一個普通指標 `base` 跳到 epitem。為什麼 `eppoll_entry` 不直接嵌在 epitem 裡？因為一個 epitem 可能要掛在多個 wait queue 上（數量由被監看者的 `->poll` 決定），只能動態配置、用 `pwqlist` 串起來。

**7.** `ep->wq`：呼叫 `epoll_wait()` 而沒有事件、必須睡覺的 task（`ep_poll` 2022 用 exclusive 方式加入，所以一次喚醒一個）。`ep->poll_wait`：當這個 epoll fd 本身被別人 poll/select/epoll 監看時，那個「別人」的等待項掛在這裡（`__ep_eventpoll_poll` 984 的 `poll_wait(file, &ep->poll_wait, wait)`）。callback 對兩者都可能喚醒（1300–1325），後者透過 `ep_poll_safewake` 處理巢狀深度。

**8.** 大部分 `struct file` 從來不會被 epoll 監看。內嵌一個 `hlist_head` 是每個 file 多 8 bytes；用指標也是 8 bytes——差別在於指標可以指向**不同的地方**：一般 file 第一次被監看時才配置一個 `struct epitems_head`（`attach_epitem` 1538–1554，用 `ephead_cache`），而 epoll file 自己則直接指向 `&ep->refs`（1537）。另外，`f_ep` 為 `NULL` 是「沒有任何 epoll 監看我」的快速判斷，`eventpoll_release`（file 關閉時）可以只看一個指標就跳過整個清理流程。`epitems_head` 還多了一個 `next` 欄位，讓巢狀檢查時可以把它串進 `tfile_check_list`。

**9.** `ep_send_events` 1824–1847：每個 epitem 被拿出來回報之後，
- `EPOLLONESHOT`：把事件遮罩清到只剩私有 bit（1832），之後 callback 第一個 `if`（1266）就會直接跳過它，直到 `EPOLL_CTL_MOD`。
- `EPOLLET`：什麼都不做，epitem 不回到 `rdllist`，直到下一次 callback（被監看者狀態再次改變）。
- level-triggered（預設）：`list_add_tail(&epi->rdllink, &ep->rdllist)`（1845），放回 ready list，下次 `epoll_wait` 會再呼叫 `ep_item_poll` 檢查一次，有資料就再回報。

測試用例 4 的輸出正是這三種行為。

**10.** pahole 顯示 `struct epitem` 為 120 bytes、2 條 cache line（64 bytes 一條）。註解的意思應該理解為「不要讓它超過目前的 cache line 數」，也就是不要長到 129 bytes 以上變成 3 條；而 `rbn`/`rcu` 共用 union、`epoll_filefd` 加 `__packed`、`dying` 用 `bool`，都是為了守住這條線。距離 128 bytes 還有 8 bytes 的餘裕，struct 內部另有一個 3 bytes 的 hole（`dying` 後面）。這句註解沒有寫明基準，是**脈絡鴻溝**：要確定作者當時的意思，得用 `git log -L` 找出這段註解是在哪個 commit 加的、當時 struct 多大（[Ch 37](./37-git-archaeology.md)）。

**11.** `eventpoll_init`（2606–2607）：`max_user_watches = (((si.totalram - si.totalhigh) / 25) << PAGE_SHIFT) / EP_ITEM_COST;`，即「低端記憶體的 4% 除以每個 watch 的成本」，`EP_ITEM_COST = sizeof(struct epitem) + sizeof(struct eppoll_entry)`（100），在這個 build 上是 120 + 64 = 184 bytes。計數器是 `user->epoll_watches`，型別 `struct percpu_counter`（`include/linux/sched/user.h:17`），`ep_insert` 用 `percpu_counter_compare` 檢查上限（1578）、`percpu_counter_inc` 增加（1581）。這正是 Ch 27 per-cpu 一節的應用：`epoll_ctl(ADD)` 可能非常頻繁，用 per-cpu 計數器避免所有 CPU 爭用同一個原子變數；檢查上限時才用 `percpu_counter_compare` 做較精確（但較貴）的比較。可以在 `/proc/sys/fs/epoll/max_user_watches` 看到實際值。

**解答說明**：這張地圖刻意把「容器」和「鎖」放在同一張表裡。eventpoll 最難懂的 `ovflist` 機制，只有在你同時知道「`rdllist` 由 spinlock 保護」和「`ep_send_events` 在掃描時要放掉 spinlock 去 `copy_to_user`」這兩件事時才說得通。只畫結構不畫鎖，`ovflist` 看起來就是一個多餘的設計。

</details>

## 測試用例

這個練習的「測試」是用非原始碼的方式驗證地圖。以下四組都實際跑過。

### 測試 1：pahole 驗證 layout

```
$ pahole -C eventpoll ~/code_reading/build/vmlinux
struct eventpoll {
	struct mutex               mtx;                  /*     0    32 */
	wait_queue_head_t          wq;                   /*    32    24 */
	wait_queue_head_t          poll_wait;            /*    56    24 */
	/* --- cacheline 1 boundary (64 bytes) was 16 bytes ago --- */
	struct list_head           rdllist;              /*    80    16 */
	spinlock_t                 lock;                 /*    96     4 */

	/* XXX 4 bytes hole, try to pack */

	struct rb_root_cached      rbr;                  /*   104    16 */
	struct epitem *            ovflist;              /*   120     8 */
	/* --- cacheline 2 boundary (128 bytes) --- */
	struct wakeup_source *     ws;                   /*   128     8 */
	struct user_struct *       user;                 /*   136     8 */
	struct file *              file;                 /*   144     8 */
	u64                        gen;                  /*   152     8 */
	struct hlist_head          refs;                 /*   160     8 */
	u8                         loop_check_depth;     /*   168     1 */

	/* XXX 3 bytes hole, try to pack */

	refcount_t                 refcount;             /*   172     4 */
	unsigned int               napi_id;              /*   176     4 */
	u32                        busy_poll_usecs;      /*   180     4 */
	u16                        busy_poll_budget;     /*   184     2 */
	bool                       prefer_busy_poll;     /*   186     1 */

	/* size: 192, cachelines: 3, members: 18 */
	/* sum members: 180, holes: 2, sum holes: 7 */
	/* padding: 5 */
};
```

```
$ pahole -C epitem ~/code_reading/build/vmlinux
struct epitem {
	union {
		struct rb_node     rbn __attribute__((__aligned__(8))); /*     0    24 */
		struct callback_head rcu __attribute__((__aligned__(8))); /*     0    16 */
	} __attribute__((__aligned__(8)));               /*     0    24 */
	struct list_head           rdllink;              /*    24    16 */
	struct epitem *            next;                 /*    40     8 */
	struct epoll_filefd        ffd;                  /*    48    12 */
	bool                       dying;                /*    60     1 */

	/* XXX 3 bytes hole, try to pack */

	/* --- cacheline 1 boundary (64 bytes) --- */
	struct eppoll_entry *      pwqlist;              /*    64     8 */
	struct eventpoll *         ep;                   /*    72     8 */
	struct hlist_node          fllink;               /*    80    16 */
	struct wakeup_source *     ws;                   /*    96     8 */
	struct epoll_event         event;                /*   104    12 */

	/* size: 120, cachelines: 2, members: 10 */
	/* sum members: 113, holes: 1, sum holes: 3 */
	/* padding: 4 */
	/* forced alignments: 1 */
	/* last cacheline: 56 bytes */
} __attribute__((__aligned__(8)));
```

```
$ pahole -C eppoll_entry ~/code_reading/build/vmlinux
struct eppoll_entry {
	struct eppoll_entry *      next;                 /*     0     8 */
	struct epitem *            base;                 /*     8     8 */
	wait_queue_entry_t         wait;                 /*    16    40 */
	wait_queue_head_t *        whead;                /*    56     8 */

	/* size: 64, cachelines: 1, members: 4 */
};
```

```
$ pahole -C wait_queue_entry ~/code_reading/build/vmlinux
struct wait_queue_entry {
	unsigned int               flags;                /*     0     4 */

	/* XXX 4 bytes hole, try to pack */

	void *                     private;              /*     8     8 */
	wait_queue_func_t          func;                 /*    16     8 */
	struct list_head           entry;                /*    24    16 */

	/* size: 40, cachelines: 1, members: 4 */
	/* sum members: 36, holes: 1, sum holes: 4 */
	/* last cacheline: 40 bytes */
};
```

能從中驗證的事：

| 觀察 | 驗證了地圖的哪一部分 |
|---|---|
| `eventpoll` 裡沒有 `nests` 欄位 | 這個 build 沒開 `CONFIG_DEBUG_LOCK_ALLOC`；有 busy poll 欄位，表示 `CONFIG_NET_RX_BUSY_POLL=y` |
| `epitem` 的 `rbn`（24 bytes）和 `rcu`（16 bytes）共用 offset 0 | 移出 rbtree 後才用 `rcu` 做延遲釋放（`__ep_remove` 884 的註解） |
| `eppoll_entry::wait` 在 offset 16，而 `wait_queue_entry::func` 在 wait 內 offset 16 | `container_of(wait, struct eppoll_entry, wait)` 減掉的就是 16 |
| `eppoll_entry` 剛好 64 bytes | 一條 cache line，`EP_ITEM_COST` = 120 + 64 = 184 |
| `wait_queue_entry::entry` 是 `list_head` | wait queue 本質上是 Ch 23 的 list，head 是 `wait_queue_head::head` |

### 測試 2：容器的增刪點

```
$ cd ~/code_reading/linux
$ rg -n 'list_add|list_del|list_splice|list_move|hlist_add|hlist_del|rb_erase|rb_insert|ep_rbtree_insert|->ovflist\s*=|->next\s*=|pwqlist\s*=|add_wait_queue|init_waitqueue_func_entry|wake_up' fs/eventpoll.c
54: * a wake_up() that in turn might be called from IRQ context.
295:		head->next = tfile_check_list;
309:		head->next = NULL;
...
745:	list_splice_init(&ep->rdllist, txlist);
762:	     nepi = epi->next, epi->next = EP_UNACTIVE_PTR) {
774:			list_add(&epi->rdllink, &ep->rdllist);
788:	list_splice(txlist, &ep->rdllist);
793:			wake_up(&ep->wq);
865:	hlist_del_rcu(&epi->fllink);
869:	rb_erase_cached(&epi->rbn, &ep->rbr);
873:		list_del_init(&epi->rdllink);
1003:			list_del_init(&epi->rdllink);
1160:	ep->ovflist = EP_UNACTIVE_PTR;
1285:		if (epi->next == EP_UNACTIVE_PTR) {
1286:			epi->next = READ_ONCE(ep->ovflist);
1292:		list_add_tail(&epi->rdllink, &ep->rdllist);
1318:			wake_up_sync(&ep->wq);
1320:			wake_up(&ep->wq);
1341:		list_del_init(&wait->entry);
1374:	init_waitqueue_func_entry(&pwq->wait, ep_poll_callback);
1378:		add_wait_queue_exclusive(whead, &pwq->wait);
1380:		add_wait_queue(whead, &pwq->wait);
1381:	pwq->next = epi->pwqlist;
1382:	epi->pwqlist = pwq;
1385:static void ep_rbtree_insert(struct eventpoll *ep, struct epitem *epi)
1403:	rb_insert_color_cached(&epi->rbn, &ep->rbr, leftmost);
1555:	hlist_add_head_rcu(&epi->fllink, file->f_ep);
1593:	epi->next = EP_UNACTIVE_PTR;
1613:	ep_rbtree_insert(ep, epi);
1668:		list_add_tail(&epi->rdllink, &ep->rdllist);
1673:			wake_up(&ep->wq);
1744:			list_add_tail(&epi->rdllink, &ep->rdllist);
1749:				wake_up(&ep->wq);
1811:		list_del_init(&epi->rdllink);
1824:			list_add(&epi->rdllink, &txlist);
1845:			list_add_tail(&epi->rdllink, &ep->rdllist);
1891:	list_del_init_careful(&wq_entry->entry);
2022:			__add_wait_queue_exclusive(&ep->wq, &wait);
```

驗收方式：

- 每一行 `list_*(&epi->rdllink, X)` 的 X 只出現 `&ep->rdllist` 或 `&txlist`（或 `txlist` 參數）——確認 `rdllink` 只會在這兩條 list 上。
- `rdllist` 這一側只出現在第二個參數位置——確認 `rdllist` 是 head、`rdllink` 是 node。
- 全部的 `rdllink` 刪除都是 `list_del_init`，沒有 `list_del`——因為 `ep_is_linked()`（366–369）靠 `list_empty(&epi->rdllink)` 判斷，刪除時必須 re-init（Ch 23 踩雷第 5 條）。
- 1891 的 `list_del_init_careful(&wq_entry->entry)` 不是 epitem 的操作，而是 `ep_autoremove_wake_function` 把 `epoll_wait` 的等待項從 `ep->wq` 拿掉——grep 的結果要一行一行歸類，不能只看函式名。

### 測試 3：rbtree 的 key 與排序

```c
/* fdinfo.c：/proc/self/fdinfo/<epfd> 由 ep_show_fdinfo() 走訪 ep->rbr 印出，順序就是 rbtree 的中序 */
#include <stdio.h>
#include <unistd.h>
#include <sys/epoll.h>
int main(void)
{
	int ep = epoll_create1(0), p[2], q[2];
	if (pipe(p) || pipe(q)) return 1;
	int fds[] = { q[1], p[0], q[0], p[1] };          /* 故意亂序加入 */
	for (int i = 0; i < 4; i++) {
		struct epoll_event ev = { .events = EPOLLIN, .data.u64 = 100 + i };
		epoll_ctl(ep, EPOLL_CTL_ADD, fds[i], &ev);
	}
	int d = dup(p[0]);                                /* 同一個 struct file、不同 fd */
	struct epoll_event ev = { .events = EPOLLIN, .data.u64 = 999 };
	printf("add dup fd %d of %d: %d\n", d, p[0], epoll_ctl(ep, EPOLL_CTL_ADD, d, &ev));
	printf("add %d again: %d (EEXIST expected)\n", p[0], epoll_ctl(ep, EPOLL_CTL_ADD, p[0], &ev));
	char path[64], line[256];
	snprintf(path, sizeof path, "/proc/self/fdinfo/%d", ep);
	FILE *f = fopen(path, "r");
	while (fgets(line, sizeof line, f)) fputs(line, stdout);
	return 0;
}
```

```
$ gcc -Wall -O1 -o fdinfo fdinfo.c && ./fdinfo
add dup fd 9 of 4: 0
add 4 again: -1 (EEXIST expected)
pos:	0
flags:	02
mnt_id:	17
ino:	44
tfd:        8 events:       19 data:               64  pos:0 ino:355c56 sdev:f
tfd:        7 events:       19 data:               66  pos:0 ino:355c56 sdev:f
tfd:        6 events:       19 data:               67  pos:0 ino:355c55 sdev:f
tfd:        4 events:       19 data:               65  pos:0 ino:355c55 sdev:f
tfd:        9 events:       19 data:              3e7  pos:0 ino:355c55 sdev:f
```

`tfd:` 那幾行由 `ep_show_fdinfo`（`fs/eventpoll.c:1072-1092`）產生，它用 `rb_first_cached` + `rb_next` 中序走訪 `ep->rbr`。可以讀出：

- **順序不是 fd 順序，也不是加入順序**。`data` 欄位是 `100 + 加入序號`：0x64、0x66、0x67、0x65 代表中序結果依序是第 0、2、3、1 個加入的 fd。中序順序由 `struct file *` 的位址決定，而 file 是 slab 配置出來的，位址不可預測。
- **fd 4 與 fd 9 相鄰，且 4 在前**：它們是同一個 `struct file`（`dup`），file 指標相等，所以比 fd，4 < 9。這直接驗證了 key = (file, fd)，比較順序是先 file 後 fd。
- **`dup` 出來的 fd 9 加入成功，但對 fd 4 重複加入回傳 -1**（errno 為 `EEXIST`，由 `ep_find` 找到既有 epitem 而拒絕）。
- 同一個 pipe 的讀端（fd 4）與寫端（fd 6）ino 相同（355c55），但它們是**兩個不同的 `struct file`**，各自是獨立的 key。它們在輸出裡相鄰只是 file 位址剛好接近，不是因為 ino 相同——rbtree 的 key 裡根本沒有 inode。

`events: 19` 是 `0x19` = `EPOLLIN | EPOLLERR | EPOLLHUP`：kernel 在 `ep_insert` 時自動加上 `EPOLLERR | EPOLLHUP`。這個細節可以當作延伸挑戰去找出處。

### 測試 4：LT / ET / ONESHOT 在 rdllist 上的差異

```c
/* ltet.c：用行為驗證資料結構地圖。LT/ET/ONESHOT 的差異 = epitem 有沒有被放回 rdllist */
#include <stdio.h>
#include <unistd.h>
#include <sys/epoll.h>
static void probe(const char *tag, unsigned extra)
{
	int p[2];
	if (pipe(p)) return;
	int ep = epoll_create1(0);
	struct epoll_event ev = { .events = EPOLLIN | extra, .data.fd = p[0] }, out[4];
	epoll_ctl(ep, EPOLL_CTL_ADD, p[0], &ev);
	printf("%-7s before write    -> %d\n", tag, epoll_wait(ep, out, 4, 0));
	if (write(p[1], "x", 1) != 1) return;
	for (int i = 1; i <= 3; i++)
		printf("%-7s wait #%d         -> %d\n", tag, i, epoll_wait(ep, out, 4, 0));
	if (write(p[1], "y", 1) != 1) return;
	printf("%-7s after 2nd write -> %d\n", tag, epoll_wait(ep, out, 4, 0));
	close(p[0]); close(p[1]); close(ep);
}
int main(void) { probe("LT", 0); probe("ET", EPOLLET); probe("ONESHOT", EPOLLONESHOT); return 0; }
```

```
$ gcc -Wall -O1 -o ltet ltet.c && ./ltet
LT      before write    -> 0
LT      wait #1         -> 1
LT      wait #2         -> 1
LT      wait #3         -> 1
LT      after 2nd write -> 1
ET      before write    -> 0
ET      wait #1         -> 1
ET      wait #2         -> 0
ET      wait #3         -> 0
ET      after 2nd write -> 1
ONESHOT before write    -> 0
ONESHOT wait #1         -> 1
ONESHOT wait #2         -> 0
ONESHOT wait #3         -> 0
ONESHOT after 2nd write -> 0
```

對照地圖：

| 情況 | 觀察 | 資料結構上發生了什麼 |
|---|---|---|
| LT，資料一直沒讀 | 每次 wait 都回 1 | `ep_send_events` 1845 把 epitem 放回 `rdllist`；下一次 `ep_item_poll` 發現仍可讀 |
| ET，資料一直沒讀 | 只有第一次回 1 | 回報後沒放回 `rdllist`；pipe 狀態沒變，沒有新的 callback |
| ET，再寫一次 | 又回 1 | pipe 的 `wake_up` → `ep_poll_callback` → 1292 `list_add_tail` 重新進入 `rdllist` |
| ONESHOT，再寫一次 | 仍是 0 | 1832 已把 `event.events` 清到只剩私有 bit；callback 在 1266 的檢查直接 `goto out_unlock` |

**注意**：這個測試跑在 WSL2 的 6.18 kernel 上，而原始碼行號對應的是上游 v6.18 tree。WSL 的 kernel 有 Microsoft 的修改，但 eventpoll 的行為與上游一致；在嚴格要求版本一致的場合，應該在 [Ch 52](./52-qemu-gdb-kernel.md) 的 QEMU 環境裡用你自己建的 v6.18 kernel 重跑。

## 延伸挑戰（加分）

1. **用 gdb 在 QEMU 裡看真實物件**：在 Ch 52 的 QEMU + gdb 環境裡跑 `fdinfo`，在 `ep_show_fdinfo` 下斷點，用 `p *ep`、`p *(struct epitem *)((char *)ep->rbr.rb_leftmost - 0)` 走一遍 rbtree，把地圖上的每個指標都用實際位址驗證一次。（`rbn` 在 offset 0，所以 `rb_node *` 和 `epitem *` 數值相同——為什麼這讓 `rb_entry` 在這裡等於什麼都沒做？）
2. **找出 `events: 19` 的來源**：在 `ep_insert` 或 `do_epoll_ctl` 裡找出自動加上 `EPOLLERR | EPOLLHUP` 的那一行，並用 `git blame` 找出它是何時、為何加入的（[Ch 37](./37-git-archaeology.md)）。
3. **巢狀 epoll 的地圖**：建立 epoll A 監看 epoll B、B 監看一個 pipe。在地圖上畫出 A 的 epitem 掛在 B 的哪個 wait queue（`B->poll_wait`）、B 的 file 的 `f_ep` 指向哪裡（`&B->refs`）。再讀 `ep_poll_safewake` 與 `EP_MAX_NESTS`，解釋為什麼巢狀深度要有上限。
4. **用 bpftrace 觀察 ovflist 啟用**：在 QEMU guest 裡用 bpftrace 掛 `kprobe:ep_poll_callback`，讀 `((struct eventpoll *)...)->ovflist`，統計 callback 執行時 `ovflist != -1` 的比例。設計一個工作負載讓這個比例變高（提示：大量 fd 同時就緒、`maxevents` 很大、user-space buffer 剛好跨頁）。**本題未實測**，需要 Ch 51 的 bpftrace 環境；預期在高事件率下可以觀察到非零比例。
5. **對照另一個子系統**：用同樣的方法替 `fs/eventfd.c`（只有幾百行）畫地圖。它和 eventpoll 共用 wait queue 機制，但結構簡單得多。這會是 Final Project 的暖身。

## 自我檢核

- [ ] 不看地圖，說出一個 `struct epitem` 可能同時在哪四個容器上，以及各自經由哪個成員。
- [ ] 解釋為什麼 eventpoll 需要兩條 ready 鏈（`rdllist` 與 `ovflist`），並說明如果拿掉 `ovflist` 會出什麼問題。
- [ ] 從一個 pipe 的 `wake_up` 出發，徒手寫出到達 `struct eventpoll` 的每一步指標轉換。
- [ ] 說出這個練習中至少兩處「pahole 或程式行為糾正／補充了你讀原始碼得到的印象」的地方。
- [ ] 能說出自己的地圖和參考地圖的差異，並解釋各自取捨（例如你是否把 wait queue 畫成獨立的容器？）

Part 4 讓你看懂了資料。但這個練習裡已經碰到一個資料結構地圖解決不了的問題：`ep_poll_callback` 是被誰呼叫的？它的呼叫者藏在一個函式指標後面，grep 不到。Part 5 要處理的就是這道間接鴻溝。

→ [Ch 28 ops table 與間接呼叫：找出真正被呼叫的是誰](./28-ops-tables-indirect-calls.md)
