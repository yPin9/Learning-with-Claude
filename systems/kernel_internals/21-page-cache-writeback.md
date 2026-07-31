# Ch 21 — page cache 與 writeback

> **目標**：理解 kernel 為什麼、以及怎麼把檔案內容快取在實體記憶體裡（page cache），讀檔命中 cache 為何快、寫檔為何不會立刻落盤（write-back），dirty page 什麼時候、由誰真正寫回磁碟。學完你能解釋 `free` 那欄 buff/cache 是什麼、能用 vmtouch / `drop_caches` / `/proc/meminfo` 親眼看到 cache 與 dirty 的動態，也能說清楚資料庫為什麼要 `fsync` 或 `O_DIRECT`。

Ch 17 我們把「一頁實體記憶體從哪來」講到 buddy allocator，Ch 19/20 把「一頁被映進哪個位址、page fault 怎麼填」講清楚。這章補上第三塊：**一頁記憶體裝的是某個檔案的內容時，kernel 怎麼管它**。這就是 page cache——mm 與檔案系統交界處最重要的一層，也是「為什麼你的機器記憶體看起來永遠是滿的」的答案。

## 為什麼需要這個？

磁碟比記憶體慢幾個數量級。NVMe SSD 隨機讀延遲約幾十微秒，DRAM 存取是幾十奈秒——差三個數量級；傳統機械硬碟差六個數量級。如果每一次 `read()` 都真的去碰磁碟，系統會慢到不能用。

而真實工作負載有極強的**時間與空間局部性**：同一個檔案（`libc.so`、設定檔、資料庫的熱頁）會被反覆讀；讀了第 N 個 byte 通常接著讀第 N+1 個。所以最直接的優化是：**把讀過的檔案內容留在記憶體裡，下次讀直接從記憶體給，不碰磁碟**。這塊「留在記憶體裡的檔案內容」就是 page cache。

沒有 page cache 的世界會是這樣：每個程式各自在 user space 快取自己讀過的檔案，同一個 `libc.so` 被十個程式各存一份，記憶體浪費、一致性也難保證。page cache 把快取下沉到 kernel，**全系統共用一份**：十個程式 `mmap` 同一個 `libc.so`，實體記憶體裡只有一份 page cache 頁，被映進十個位址空間（這條線接回 Ch 20 的 rmap，也接下面 mmap 那節）。

寫檔那邊有對稱的問題。如果每次 `write()` 都同步等磁碟寫完才返回，寫入密集的程式（編譯、日誌、資料庫）會被磁碟拖死。所以 kernel 選擇 **write-back**：`write()` 只把資料寫進 page cache、標記為「髒」（dirty），就返回；真正落盤稍後由背景執行緒批次做。代價是——crash 會丟掉還沒落盤的資料。這個取捨貫穿整章。

> 你在 `linux_commands` 課看過 `free -h`，`buff/cache` 那欄常常佔掉大半記憶體，`available` 卻很大。這不是「記憶體被吃掉」——那大半是 page cache，隨時可以在需要時丟掉（clean page 直接丟、dirty page 先寫回）。這章就是那欄底下的機制。

## 先建立直覺

先把三個角色擺清楚：**檔案（inode）**、**page cache（一堆實體頁）**、**磁碟上的 block**。page cache 夾在中間，是檔案內容在記憶體裡的鏡像。

```
   user process                                             磁碟（block device）
   ┌──────────┐                                             ┌──────────────────┐
   │ read()   │                                             │  file blocks     │
   │ write()  │                                             │  0 1 2 3 4 5 ...  │
   └────┬─────┘                                             └────────▲─────────┘
        │  copy_to/from_user                                         │  bio (Ch 36)
        ▼                                                            │
   ┌─────────────────────── page cache ──────────────────────────────┴──┐
   │  struct address_space（屬於這個 inode）                             │
   │    xarray:  index(檔案第幾頁) ─► folio(實體頁)                       │
   │                                                                     │
   │   index 0 ─► [folio: file bytes 0..4095   ] clean                   │
   │   index 1 ─► [folio: file bytes 4096..8191] DIRTY  ← write() 標髒    │
   │   index 2 ─► (缺頁)  ← read 時 miss，觸發磁碟讀 + readahead           │
   └─────────────────────────────────────────────────────────────────────┘
```

三件事先記住：

1. **一個檔案 = 一個 `address_space`**，裡面用 xarray（Ch 5）做「檔案第幾頁（index）→ 實體頁（folio）」的映射。檔案 offset 除以 page size 就是 index。
2. **read 先查這張表**：命中就直接複製給 user；miss 就去磁碟讀進來，順便 readahead 多讀幾頁。
3. **write 只改這張表裡的頁並標 dirty**，不立刻碰磁碟；落盤是背景執行緒稍後的事。

> **folio 是什麼**：6.x 起 kernel 把 page cache 的操作單位從 `struct page` 逐步換成 `struct folio`（`include/linux/mm_types.h`）。一個 folio 是一個或多個連續實體頁的頭（order-0 folio 就是一頁）。引入它是為了乾淨地支援 large folio（一次快取多頁、減少管理開銷）。本章講 folio 時你可以先當「一頁或一組連續頁」理解；很多老函式名（`readpage`）也逐步改成 folio 版（`read_folio`）。

## `struct address_space`：一個檔案的 page cache 容器

核心結構在 `include/linux/fs.h` 的 `struct address_space`。每個 `struct inode`（Ch 33 VFS）內嵌一個 `struct address_space i_data`，`inode->i_mapping` 指向它。關鍵欄位：

- `struct xarray i_pages`：**這就是 page cache 本體**——一棵 xarray，key 是檔案的 page index，value 是該頁的 folio。查快取、插入頁、找 dirty 頁都走它。用 xarray 而非普通陣列，是因為檔案可以很大且稀疏（只快取被碰過的頁），xarray 對稀疏 key 省空間、又支援標記（見下）。
- `const struct address_space_operations *a_ops`：一組 function pointer，由底層檔案系統填（ext4、xfs、btrfs 各有一套）。page cache 是機制、a_ops 是策略：page cache 不知道怎麼把一頁變成磁碟上的 block，它呼叫 a_ops 讓檔案系統去做。
- `unsigned long nrpages`：目前快取了幾頁。
- `struct inode *host`：反指回擁有這個 address_space 的 inode。

`address_space_operations`（同檔）裡幾個你會反覆遇到的：

| 操作 | 何時被呼叫 | 做什麼 |
|---|---|---|
| `read_folio`（舊名 `readpage`） | read miss、需要從磁碟填一頁 | 發 bio 把該頁從磁碟讀進來 |
| `writepages` / `writepage` | writeback 要把 dirty 頁寫回 | 把 dirty folio 打包成 bio 送去磁碟 |
| `write_begin` / `write_end` | buffered `write()` 路徑 | 準備好目標 folio、write 完標髒 |
| `dirty_folio`（舊名 `set_page_dirty`） | 一頁被弄髒時 | 標記 folio dirty，並在 xarray 記 dirty tag |
| `direct_IO` | `O_DIRECT` 讀寫 | 繞過 page cache 直接對磁碟做 I/O |

xarray 除了存 folio，還在每個 entry 上掛**標記（mark/tag）**：`PAGECACHE_TAG_DIRTY`、`PAGECACHE_TAG_WRITEBACK`。這是 writeback 的關鍵——要找「這個檔案哪些頁是髒的」，不必掃全部頁，直接對 xarray 用 dirty tag 迭代（`tag_pages_for_writeback` 之類）就能只走到髒頁。這正是 Ch 5 講 xarray 支援 tagged lookup 的實戰用途。

## read 怎麼用 page cache

buffered read（沒開 `O_DIRECT`）的入口是 `mm/filemap.c` 的 `filemap_read()`（由 VFS 的 `generic_file_read_iter` 呼叫，完整 read 路徑是 Ch 34）。骨架是這樣：

1. `filemap_get_pages()` 依 read 範圍去 page cache 找需要的 folio。裡面對每個 index 呼叫 `filemap_get_folio()` 查 xarray。
2. **命中**（folio 在且 uptodate）：直接進第 4 步。
3. **miss**（folio 不在，或在但內容還沒讀好）：呼叫 `page_cache_sync_readahead` / `page_cache_async_readahead`（`mm/readahead.c`）發起磁碟讀——不只讀你要的那一頁，還多讀後面幾頁（readahead，見下）。等 folio 變 uptodate。
4. `copy_folio_to_iter()`：把 folio 內容 `copy_to_user` 複製到你的 buffer，`read()` 返回。

所以「第一次讀慢、之後讀快」的底層就是：第一次 miss，走第 3 步碰磁碟；之後命中，走第 2 步純記憶體複製。你等下用 `drop_caches` 就能親手製造這個差異。

### readahead：預讀

readahead 的直覺是：磁碟 I/O 的固定成本（尋道、發指令、中斷）很高，既然都要讀了，多讀相鄰的幾頁幾乎不加成本，卻能讓後續順序讀直接命中。程式很少只讀一頁就停。

`mm/readahead.c` 的機制大致是：

- kernel 維護每個開啟檔案的 readahead 狀態（`struct file_ra_state`，`include/linux/fs.h`），記錄上次讀到哪、目前預讀窗多大。
- **偵測到順序讀**（這次的 index 接續上次），就**擴大預讀窗**——一次多讀更多頁，甚至倍增，直到上限（預設上限跟 `bdi` 的 `read_ahead_kb` 有關，可在 `/sys/class/bdi/<dev>/read_ahead_kb` 調）。
- **偵測到隨機讀**（index 跳來跳去），就縮小甚至關掉預讀，避免浪費 I/O 讀了不會用到的頁。
- async readahead：當你讀到預讀窗裡標了 `PG_readahead` 的那頁時，kernel 就「趁你還在用前面的頁」在背景把下一批預讀出來，讓磁碟讀和你的計算重疊。

這是「順序讀友善、隨機讀不懲罰」的自適應設計。資料庫做隨機 I/O 時常會用 `posix_fadvise(POSIX_FADV_RANDOM)` 明確告訴 kernel 別預讀。

## write 與 dirty page：write-back 的核心

buffered `write()` 走 `mm/filemap.c` 的 `generic_perform_write()`（由 `generic_file_write_iter` 呼叫）：

1. 對每個要寫的 folio，呼叫 a_ops 的 `write_begin`：確保目標 folio 在 page cache 裡（不在就配一頁，必要時先把舊內容讀進來——因為你可能只覆寫一頁的一部分）。
2. `copy_folio_from_iter_atomic()`：把 user buffer 的資料 `copy_from_user` 複製進 folio。
3. `write_end`：呼叫 `dirty_folio` 把這頁標成 **dirty**——在 folio 上設 `PG_dirty` flag、在 address_space 的 xarray 上打 `PAGECACHE_TAG_DIRTY`、更新全域 dirty 頁計數。

**然後 `write()` 就返回了。資料還在記憶體，磁碟上還是舊的。** 這就是 write-back cache：寫入被「緩衝」在 page cache，落盤延後。

### dirty page 什麼時候真正落盤

四個觸發點：

1. **週期性**：`dirtytime`/flusher 每隔一段時間把「放太久」的 dirty 頁寫回。相關旋鈕 `vm.dirty_writeback_centisecs`（預設 500，即每 5 秒喚醒 flusher 巡一次）、`vm.dirty_expire_centisecs`（預設 3000，dirty 頁超過 30 秒算「過期」該寫）。
2. **dirty 比例超標**：全系統 dirty 頁佔可用記憶體的比例超過門檻就強制寫。
   - `vm.dirty_background_ratio`（預設約 10%）：**背景**門檻。超過就喚醒 flusher 在背景寫，但**不擋**寫入的程式。
   - `vm.dirty_ratio`（預設約 20%）：**硬**門檻。超過後，正在 `write()` 的程式會被**節流（throttle）**——`balance_dirty_pages()`（`mm/page-writeback.c`）讓它停下來幫忙寫、或睡一下，直到 dirty 降下來。這是「不讓髒頁無限堆積把記憶體吃光」的煞車。
   - （另有 `dirty_bytes`/`dirty_background_bytes` 用絕對量取代比例，設了其一另一組失效。）
3. **記憶體壓力**：reclaim（Ch 22）要回收頁時，遇到 dirty 頁不能直接丟，得先 writeback（見最後一節）。
4. **明確要求**：`sync()`（全系統）、`syncfs()`（單一檔案系統）、`fsync(fd)`/`fdatasync(fd)`（單一檔案）強制把 dirty 頁寫回並等它完成。資料庫、`git`、任何在乎 crash 一致性的程式都靠這個。

### 誰來做 writeback：per-bdi flusher

實際把 dirty 頁寫回磁碟的，是**每個 backing device（bdi，backing_dev_info）的 writeback 執行緒**，俗稱 flusher。程式碼在 `mm/backing-dev.c` 與 `fs/fs-writeback.c`。

為什麼是 per-bdi（per 裝置）而不是全系統一個？因為不同裝置速度差很多——一顆慢 HDD 不該拖住一顆快 NVMe 的寫回。每個 bdi 各有自己的 flusher，各自按自己的節奏刷。flusher 本身跑在 workqueue（Ch 30）上，被上面那些觸發點喚醒後，對 bdi 上的 dirty inode 逐一處理：用 address_space 的 dirty tag 找出髒頁，呼叫 a_ops 的 `writepages` 打包成 bio（Ch 36 block layer）送下去。

```
   write() ─► page cache 標 dirty ─┐
                                   │  （堆積）
   dirty 過期 / 比例超標 / fsync ──►│ 喚醒
                                   ▼
                        ┌──── per-bdi flusher（workqueue，Ch 30）────┐
                        │  對 dirty inode：                          │
                        │    xarray 用 DIRTY tag 找髒 folio          │
                        │    a_ops->writepages() ─► bio ─► block layer│
                        │    寫回中標 WRITEBACK tag，完成後清 dirty   │
                        └────────────────────────┬───────────────────┘
                                                 ▼
                                              磁碟（Ch 36）
```

## 為什麼要 write-back 而非 write-through

write-through 是「每次寫都同步落盤才返回」，write-back 是「寫進 cache 就返回、稍後批次落盤」。kernel 選 write-back，換來三個好處：

- **合併寫（write coalescing）**：同一頁被連續寫十次，write-back 只落盤一次（最後的狀態）；write-through 要落盤十次。日誌 append、計數器更新這種反覆改同一頁的場景，省下大量 I/O。
- **延遲寫可能整段省掉**：寫進去的檔案若很快被刪掉（編譯的 `.o` 中間檔、tmp 檔），在落盤前就沒了，那些 I/O 完全不用做。
- **順序化與批次化**：累積一批 dirty 頁再交給 block layer，I/O scheduler（Ch 36）能把它們排序、合併成大 I/O，對機械硬碟尤其能省尋道時間。

代價只有一個，但很致命：**crash 或斷電時，還在 page cache 沒落盤的 dirty 頁全丟**。這就是為什麼——

- **資料庫**不能只靠 buffered write。它們用 **WAL（write-ahead log）+ `fsync`**：改資料前先把「我要改什麼」寫進 log 並 `fsync`（確定落盤）才動手，crash 後靠 log 重放。`fsync` 的語意就是「把這個檔案的 dirty 頁全寫回並等磁碟確認」——它把 write-back 在需要時退化成 write-through。
- **著名的 `fsync` 陷阱**：`write()` 成功不代表資料在磁碟上，只代表在 page cache 裡。真正的持久化保證來自 `fsync` 返回。很多「以為存了其實沒存」的資料遺失 bug 根源在此。

## 直接 I/O（O_DIRECT）：繞過 page cache

有些程式**不想要** page cache。最典型是資料庫：它自己有一套精心設計的 buffer pool，比 kernel 的通用 page cache 更懂自己的存取模式。若再經過 page cache，資料會被快取兩份（double caching）、浪費記憶體，且兩層快取的替換策略打架。

`open()` 加 `O_DIRECT` flag 後，read/write 走 a_ops 的 `direct_IO`，資料在 user buffer 和磁碟之間**直接搬**，不經過 page cache。代價與限制：

- **對齊要求**：buffer 位址、檔案 offset、長度通常都要對齊到裝置的邏輯區塊大小（常見 512B 或 4K）。沒對齊會 `EINVAL`。
- **失去 kernel 的快取與 readahead**：你得自己做快取、自己管預讀，做不好反而更慢。
- **不是「同步落盤」的同義詞**：`O_DIRECT` 繞過 page cache，但要保證真的寫到磁碟碟片（而非裝置自己的 volatile cache），仍可能需要 `O_DSYNC` 或 `fsync`。這兩個概念常被搞混。

一般應用不要碰 `O_DIRECT`——page cache 幾乎總是對的預設。只有「自己比 kernel 更懂快取」的系統（DB、某些高效能儲存引擎）才用它。

## page cache 與 mmap 檔案映射

Ch 19/20 講 `mmap` 一個檔案時，建立一個 file-backed VMA，第一次碰到會 page fault。這裡補上關鍵一環：**page fault 填進 VMA 的那一頁，就是 page cache 裡的那一頁**——不是另外複製一份。

`mm/filemap.c` 的 `filemap_fault()` 是 file-backed VMA 的 fault handler（掛在 `vm_operations_struct->fault`）。它做的事和 `filemap_read` 的查快取邏輯幾乎一樣：去 address_space 的 xarray 找 folio，命中就把它映進 page table，miss 就 readahead 讀進來再映。

結果是 **read/write 路徑和 mmap 路徑共用同一份 page cache**：

- 你 `mmap` 一個檔案改了記憶體，另一個程式 `read()` 同一檔案看得到你的改動（同一份 page cache 頁）。
- 十個程式 `mmap` 同一個 `.so`，實體記憶體只有一份 page cache 頁，透過 rmap（Ch 20）被十個 page table 指到。這是共享函式庫省記憶體的底層。
- 改過的 mmap 頁（`MAP_SHARED`）被標 dirty，一樣由 flusher 依上面的規則寫回。`msync()` 是 mmap 版的 `fsync`。

一句話：page cache 是 read/write 與 mmap 的**共同底層**，這也是為什麼它放在 mm 和 fs 的交界。

## reclaim 時：clean 直接丟、dirty 先寫回

page cache 可以無限長大到把（幾乎）所有空閒記憶體用掉——這是好事，記憶體閒著也是閒著。當真的有人要記憶體、記憶體不夠時，reclaim（Ch 22 的 kswapd / direct reclaim）會回收 page cache 頁。回收規則和 dirty 直接相關：

- **clean 的 page cache 頁**：內容和磁碟一致，**直接丟**（從 xarray 移除、頁還給 buddy）。下次要用再從磁碟讀回來即可，零成本回收。
- **dirty 的 page cache 頁**：磁碟上是舊的，**不能直接丟**，得先 writeback 寫回磁碟、變 clean，才能回收。所以 dirty 頁堆太多會讓 reclaim 變慢（要等 I/O），這也是 `dirty_ratio` 要限制 dirty 比例的另一個理由——別讓可回收記憶體都卡在「等寫回」。

這解釋了 `free` 的直覺：`buff/cache` 大但 `available` 也大，是因為那裡面大部分是 clean page cache，隨時可丟。真正「丟起來有成本」的只有 dirty 那部分（`/proc/meminfo` 的 `Dirty`）。這條線直接接 Ch 22。

## 動手：親眼看 page cache 與 dirty

以下在你 Ch 0 的 QEMU 環境或任何 Linux（含 WSL2）都能跑。有些要 root。

### 1. `free` 看 buff/cache，`drop_caches` 感受 cache 命中

```bash
# 造一個 256MB 的檔案
dd if=/dev/zero of=/tmp/bigfile bs=1M count=256

# 清掉 page cache（3 = 清 pagecache + dentries + inodes）。需 root
sync                                  # 先把 dirty 寫回，否則 dirty 頁不會被丟
echo 3 > /proc/sys/vm/drop_caches

# 第一次讀：cache 是空的，真的碰磁碟——慢
time cat /tmp/bigfile > /dev/null

# 第二次讀：整個檔案已在 page cache——快，且幾乎不碰磁碟
time cat /tmp/bigfile > /dev/null
```

第二次的 `real` 時間應該明顯小於第一次。中間穿插 `free -h` 看 `buff/cache` 在第一次讀後漲了約 256MB、`drop_caches` 後掉回去。

> `drop_caches` 是**除錯/觀測用**的，不是效能手段——清掉 cache 只會讓接下來一切變慢。它的用途是做 cold-cache 基準測試（像上面），或懷疑 cache 有問題時排除變因。

### 2. vmtouch 看某個檔案有多少在 cache 裡

```bash
sudo apt install vmtouch
vmtouch /tmp/bigfile
#   Resident Pages: 0/65536  0/256M  0%    ← drop_caches 後全不在 cache
cat /tmp/bigfile > /dev/null
vmtouch /tmp/bigfile
#   Resident Pages: 65536/65536  256M/256M  100%   ← 讀完整個檔在 cache

vmtouch -e /tmp/bigfile   # evict：把這個檔案從 cache 踢出（不影響別的檔案）
```

`vmtouch` 底層是 `mmap` 檔案後用 `mincore()` 問 kernel「這些頁在不在 RAM」。它比 `drop_caches`（全域）精細——能看/操作單一檔案。

### 3. dd 寫檔看 dirty 堆積與寫回

```bash
# 一邊持續寫，一邊另開視窗看 dirty
watch -n0.2 'grep -E "Dirty|Writeback" /proc/meminfo'

# 另一個視窗：寫一個大檔（用 oflag=nocache 之外的預設 buffered write）
dd if=/dev/zero of=/tmp/dirtytest bs=1M count=512
```

觀察 `/proc/meminfo`：
- `Dirty`：已寫進 page cache、還沒排入寫回的髒頁量。dd 進行時會漲。
- `Writeback`：正在寫回磁碟途中的量。
- dd 一結束、或 dirty 超過 `dirty_background_ratio`，flusher 就把 `Dirty` 刷下去（先變 `Writeback`，寫完歸零）。

強制立刻寫回並清空 Dirty：

```bash
sync                                  # 全系統：把所有 dirty 寫回並等完成
# 或針對單一檔案（C）：fsync(fd)
```

### 4. 用 gdb 停在 page cache 命中/落盤上（接 Ch 0 環境）

在 QEMU + gdb 環境裡，可以停在關鍵函式看它們真的被呼叫：

```gdb
(gdb) break filemap_get_folio        # 每次查 page cache 都會經過
(gdb) break balance_dirty_pages      # dirty 超標節流時觸發
(gdb) break wb_writeback             # flusher 做寫回的主迴圈（fs/fs-writeback.c）
(gdb) continue
```

然後在 QEMU 裡 `cat` 一個檔（觸發 `filemap_get_folio`）、`dd` 大量寫入（撞到 `balance_dirty_pages`），回 gdb `backtrace` 看完整呼叫鏈。這把上面所有抽象變成你能單步的真實程式碼。

## 對比與取捨

| 面向 | write-back（預設） | write-through | O_DIRECT |
|---|---|---|---|
| `write()` 何時返回 | 寫進 page cache 就返回 | 等落盤才返回 | 資料搬到磁碟才返回（仍可能只到裝置 cache） |
| 吞吐 | 高（合併、批次、順序化） | 低（每寫必等磁碟） | 看應用；省掉 double cache |
| crash 資料安全 | 差（未落盤的 dirty 全丟） | 好 | 需配 fsync/O_DSYNC 才有保證 |
| 記憶體用量 | 用 page cache（可回收） | 用 page cache | 幾乎不用 page cache |
| 適用 | 絕大多數應用 | 幾乎沒人用（太慢） | DB、自管快取的儲存引擎 |
| 落盤保證手段 | `fsync`/`sync`/`msync` | 天生保證 | `fsync`/`O_DSYNC` |

| 讀取路徑 | buffered read | mmap read | O_DIRECT read |
|---|---|---|---|
| 經過 page cache | 是 | 是（同一份） | 否 |
| 資料如何到 user | `copy_to_user` | 直接映進位址空間、零複製 | DMA 直入 user buffer |
| readahead | 有 | 有（`filemap_fault`） | 無（自己管） |
| 重複讀成本 | 命中即快 | 命中即快 | 每次都碰磁碟 |

## 踩雷集錦

1. **「`free` 顯示記憶體快滿了，是不是記憶體不夠？」** —— 錯。`used` 不含 `buff/cache`；`buff/cache` 那大塊是 page cache，`available` 才是「真正還能給程式用的量」（它把可回收的 clean cache 算進去）。page cache 佔滿空閒記憶體是設計如此、是好事，不是漏水。

2. **「`write()` 回傳成功 = 資料在磁碟上了」** —— 錯。`write()` 成功只保證資料進了 page cache。crash 會丟。要確定落盤必須 `fsync`（或 `fdatasync`）並檢查它的回傳值。這是資料庫 WAL 存在的全部理由。

3. **「用 `O_DIRECT` 就等於資料安全落盤了」** —— 不一定。`O_DIRECT` 只保證繞過 page cache 把資料交給裝置，裝置自己可能還有 volatile write cache。要真的持久化仍需 `fsync`/`O_DSYNC`（觸發 FLUSH/FUA）。「繞過 OS cache」和「持久化」是兩件事。

4. **「`drop_caches` 能釋放記憶體、讓系統變快」** —— 反了。clean page cache 本來就隨時可被回收、不擋任何配置；主動 `drop_caches` 只是把有用的快取丟掉，讓接下來每次讀都重新碰磁碟，整體變慢。它只該用於做冷快取基準或除錯。

5. **「dirty 頁越多、寫回越晚越好，反正省 I/O」** —— 有上限。dirty 堆過 `dirty_ratio`，正在寫的程式會被 `balance_dirty_pages` 節流、卡住；且 reclaim 遇到大量 dirty 要等 writeback 才能回收，記憶體壓力下反而更卡。write-back 是延遲不是無限延遲。

6. **「readahead 一定有幫助，多讀不虧」** —— 隨機讀時是虧的。順序讀 readahead 大賺，但對隨機存取（DB 索引跳讀）預讀的頁多半用不到、白費 I/O 和記憶體。kernel 會偵測並縮窗，但明確的隨機負載該用 `posix_fadvise(POSIX_FADV_RANDOM)` 或 `O_DIRECT` 關掉它。

## 進階：再往深一層

- **large folio / mTHP 對 page cache 的影響**：6.x 起 page cache 逐步支援 large folio（一次快取多頁的連續 folio），減少 xarray entry 數、減少 per-page 管理開銷、改善 TLB。這也是 `struct page` → `struct folio` 大遷移的動機之一。讀 `mm/filemap.c` 時注意函式簽名很多已是 folio 版。
- **cgroup v2 的 writeback 歸屬**：per-bdi flusher 之上，cgroup v2 的 io controller 會把 dirty 頁的寫回 I/O 歸帳到弄髒它的 cgroup（memcg + blkcg 協作），否則一個容器狂寫 dirty、寫回 I/O 卻算在 root 上，隔離就漏了。細節接 Ch 50。
- **`madvise`/`fadvise` 提示**：`POSIX_FADV_DONTNEED`（讀完就丟出 cache，串流大檔避免污染 cache）、`POSIX_FADV_WILLNEED`（預先拉進 cache）、`MADV_DONTNEED`——這些是 user space 給 page cache 的明確提示，備份、影音串流、DB 都用得上。
- **面試常問**：「`read()` 之後資料在哪？」「buffered vs direct I/O 差別？」「`fsync` 保證什麼、不保證什麼？」「為什麼 `free` 的 used 那麼低但記憶體看起來滿？」「dirty_ratio 調高調低各有什麼後果？」——這節每一條都是標準題，答得出機制就贏了。
- **`vm.dirty_*` 調參的真實影響**：寫入尖峰型負載（如日誌批次）調高 `dirty_background_ratio` 可讓更多寫在記憶體合併；但調太高，一旦要 sync 或 crash，代價（要寫回的量、丟失的量）也大。沒有普適最佳值，看你在乎吞吐還是延遲/安全。

## 動手練習

1. **量化 cache 命中的價值**：對一個 512MB 檔案，`drop_caches` 後 `time cat` 一次（cold），再 `time cat` 一次（warm），記下兩者 `real`。算出加速比。用 `vmtouch` 確認 warm 那次檔案 100% 在 cache。寫下你觀察到的數字。

2. **看 dirty 被節流**：把 `vm.dirty_ratio` 暫時調很低（如 `sysctl vm.dirty_ratio=5`），跑一個大 `dd`，同時 `watch grep Dirty /proc/meminfo`。觀察 dd 的速度是否變慢（因為更早撞到 `balance_dirty_pages` 節流）。做完把 `dirty_ratio` 調回預設（通常 20）。

3. **驗證 mmap 與 read 共用 page cache**：寫個小 C 程式 `mmap`（`MAP_SHARED`）一個檔案並改幾個 byte，**不呼叫 msync**；另一個視窗 `cat` 或 `read()` 同一檔案，確認看得到你的改動（證明是同一份 page cache 頁）。再測 `msync` 前後 `/proc/meminfo` 的 `Dirty`。

4. **gdb trace 寫回**：接 Ch 0 環境，`break balance_dirty_pages` 與 `break wb_writeback`。在 QEMU 裡 `dd` 大量寫入，看哪個先觸發、`backtrace` 出完整鏈：從 `dd` 的 `write()` syscall 一路到 `balance_dirty_pages`，以及 flusher 這邊 workqueue → `wb_writeback`。

5. **`fsync` 的必要性**（觀念驗證）：讀 SQLite 或 PostgreSQL 對 `fsync` 的文件段落，對照本章 write-back 模型，用一句話解釋「關掉 fsync 為什麼快、又為什麼危險」。（不用真的弄壞資料庫，理解模型即可。）

## 本章重點整理

- page cache 是 kernel 把檔案內容快取在實體記憶體的機制，全系統共用一份；`free` 的 `buff/cache` 大半是它，clean 的部分隨時可回收，所以「記憶體看起來滿」不代表不夠用。
- 一個檔案（inode）對應一個 `struct address_space`（`include/linux/fs.h`），用 xarray 做 index→folio 映射並掛 dirty/writeback tag；a_ops 是檔案系統填的策略（`read_folio`/`writepages`/…）。read 走 `filemap_read`/`filemap_get_folio` 查快取、miss 觸發 readahead + 磁碟讀。
- write 是 write-back：`generic_perform_write` 把資料寫進 page cache 標 dirty 就返回；真正落盤由 per-bdi flusher（workqueue，`fs/fs-writeback.c`）在週期性、dirty 比例超標（`dirty_background_ratio`/`dirty_ratio`，`mm/page-writeback.c` 的 `balance_dirty_pages`）、或 `fsync`/`sync` 時做。
- write-back 換來合併/延遲/順序化的效能，代價是 crash 丟未落盤資料——所以要持久化必須 `fsync`（資料庫用 WAL+fsync）；`O_DIRECT` 給自管快取的系統繞過 page cache；mmap 與 read/write 共用同一份 page cache；reclaim 時 clean 直接丟、dirty 先寫回（接 Ch 22）。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 `free` 的 `buff/cache` 很大不代表記憶體不夠，`available` 為什麼可以同時很大
- [ ] 能畫出 read 命中 / read miss（+readahead）/ write 標 dirty / dirty writeback 四條資料流
- [ ] 能說出 dirty 頁落盤的四個觸發點，以及 `dirty_background_ratio` 和 `dirty_ratio` 的差別（背景 vs 硬節流）
- [ ] 面試被問「`write()` 回傳成功，資料一定在磁碟上嗎？」你能答出 page cache + write-back + `fsync` 的完整因果
- [ ] 能說清楚 `O_DIRECT` 繞過什麼、不保證什麼，以及誰該用它
- [ ] 能解釋 mmap 的頁和 read 的頁為什麼是同一份 page cache，這對共享函式庫省記憶體有什麼意義
- [ ] 能親手用 `drop_caches` + `vmtouch` + `/proc/meminfo` 觀測 cache 命中與 dirty 動態

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/sysctl/vm.rst](https://www.kernel.org/doc/html/latest/admin-guide/sysctl/vm.html)**
  - **讀哪裡**：`dirty_ratio`、`dirty_background_ratio`、`dirty_bytes`、`dirty_writeback_centisecs`、`dirty_expire_centisecs`、`drop_caches` 各段
  - **和本章的關聯**：本章講的每一個旋鈕，這裡有官方精確定義與交互規則（例如設了 `_bytes` 就讓 `_ratio` 失效），調參前必讀

- **[Documentation/filesystems/vfs.rst](https://www.kernel.org/doc/html/latest/filesystems/vfs.html)**
  - **讀哪裡**：`address_space_operations` 那節
  - **能學到什麼**：每個 a_ops 方法的契約（誰呼叫、要做什麼、可否睡眠），把本章的表格補成完整規格；接 Ch 33 VFS

### 論文 / 經典

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly）
  - **讀哪裡**：「The Page Cache」與「Accessing Files」兩章
  - **定位**：page cache 資料結構與 read/write 路徑最系統性的紙本說明。講的是 2.6，`struct folio`、xarray 是新的（舊書用 radix tree、`struct page`），但骨架與設計動機不變，配 6.12 源碼讀

- **[LWN: The folio pull request / Large folios 系列](https://lwn.net/Kernel/Index/#Memory_management-folios)** — LWN.net
  - **讀哪裡**：folio 動機與 large folio 進展的幾篇
  - **為什麼值得讀**：想懂「為什麼 `struct page` 要換成 `struct folio`」「large folio 怎麼幫 page cache」，LWN 是第一手；前提是先讀完本章有 page cache 骨架

### 線上源碼

- **[Bootlin Elixir：mm/filemap.c（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/mm/filemap.c)** 與 **[mm/page-writeback.c](https://elixir.bootlin.com/linux/v6.12/source/mm/page-writeback.c)**
  - **讀哪裡**：`filemap_read`、`filemap_get_folio`、`filemap_fault`、`generic_perform_write`（filemap.c）；`balance_dirty_pages`、`writeback_inodes_wb`（page-writeback.c）
  - **怎麼配本章**：本章給的函式名都能在這兩檔跳轉，配 Ch 0 的 gdb 下中斷點看它們真的被呼叫

page cache 讓乾淨頁隨時可丟、髒頁要先寫回——但「記憶體不夠時，該回收誰、髒頁怎麼安排寫回、匿名頁（沒對應檔案）又往哪去」，這是一整套獨立的 reclaim 機制。下一章我們進 kswapd、swap 與 OOM killer，看 kernel 在記憶體壓力下怎麼做取捨。

→ [Ch 22 reclaim：kswapd、swap、OOM killer](./22-reclaim-swap-oom.md)
