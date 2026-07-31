# Ch 34 — 一次 read() 的完整路徑

> **目標**：追一個 `read(fd, buf, n)` syscall 從使用者空間一路到磁碟、再把資料捧回來的完整旅程——經過 syscall 分派、VFS 多型、page cache 命中判斷、readahead、bio、磁碟中斷喚醒。學完你能在腦中畫出這條線，並用 strace / ftrace / gdb 三種工具在真機上看它每一層真的怎麼跑。

這一章不引入新的子系統。它是一條**穿線**的章：把前面學的 syscall（Ch 4）、VFS 物件（Ch 33）、page cache（Ch 21）、mm 缺頁（Ch 19）、睡眠與喚醒（Ch 9/26）、中斷（Ch 29）串成一條可以從頭走到尾的路。你已經一塊一塊學過這些零件，現在把它們裝成一台機器，看資料怎麼流過去。

## 為什麼需要這個？

你會寫 `read()`。你也大概知道「read 會去 page cache 找，找不到才讀磁碟」。但這種一句話的理解在下面這些問題面前立刻破功：

- 為什麼同一個檔案第一次 `read` 要 8 毫秒，第二次只要 3 微秒？
- 為什麼 `read(fd, buf, 4096)` 有時只回傳 1000，明明檔案還沒讀完？
- 為什麼一個卡在讀磁碟的 process 用 `kill -9` 也殺不掉，`ps` 顯示 `D` 狀態？
- `mmap` 一個檔案然後直接讀記憶體，和 `read()` 讀同一個檔案，底下走的是同一條路嗎？
- `O_DIRECT` 到底繞過了什麼、為什麼資料庫愛用它？

這些答案全部藏在「一次 read 到底穿過哪幾層、每層做什麼決定」裡。單看任何一章都答不完整——`read` 快慢的分岔點在 page cache（Ch 21），阻塞的機制在排程（Ch 9），喚醒它的是磁碟中斷（Ch 29），而把這些接起來的骨架在 VFS（Ch 33）與 `fs/read_write.c`。這一章就是那根把它們接起來的線。

## 先建立直覺

先看整條路的地圖。左邊是快路徑（cache 命中），右邊是慢路徑（miss，得下磁碟）：

```
  使用者空間                read(fd, buf, n)
  ─────────────────────────────│──────────────────────────────────────
  syscall 邊界        syscall 指令 → do_syscall_64 → __x64_sys_read   (Ch 4)
  (fs/read_write.c)                 │
                             ksys_read(fd, buf, count)
                                    │  fdget(fd): fd ─► struct file      (Ch 33)
                             vfs_read(file, buf, count, &pos)
                                    │  權限/範圍檢查
                             file->f_op->read_iter(...)   ← 多型分派     (Ch 33)
                                    │  一般檔案 = generic_file_read_iter
  (mm/filemap.c)            filemap_read(iocb, iter, ...)               (Ch 21)
                                    │
                          filemap_get_pages(): 查 page cache（xarray）
                                    │
                    ┌───────────────┴────────────────┐
              命中（快路徑）                     未命中（慢路徑）
                    │                                │
          folio 在且 uptodate            readahead：發 bio 讀本頁+後幾頁 (Ch 21)
                    │                                │  a_ops->read_folio / readahead
                    │                          建 bio ──► block layer   (Ch 36)
                    │                                │  排入 blk-mq、送裝置
                    │                          process 睡（TASK_UNINT.）  (Ch 9)
                    │                                │  ……磁碟慢慢讀……
                    │                          磁碟完成 → IRQ 喚醒它     (Ch 29)
                    │                                │  資料進 folio、標 uptodate
                    │                          folio 現在在 page cache
                    └───────────────┬────────────────┘
                                    │
                       copy_folio_to_iter(): copy_to_user               (Ch 4)
                                    │  把 page cache 的位元組搬進 user buf
                             read() 回傳實際搬了幾 bytes
```

三個心智錨點，先記住：

1. **page cache 是分水嶺**。命中就是純記憶體搬運（快路徑，微秒級），miss 就要下磁碟（慢路徑，毫秒級）。快慢差三個數量級，全看這一次查表命不命中。
2. **VFS 那一層是多型的**。`vfs_read` 不知道你讀的是 ext4、xfs 還是 tmpfs，它只呼叫 `file->f_op->read_iter`——這個函式指標由檔案系統在 open 時填好（Ch 33 的 `file_operations`）。一般在磁碟上的檔案系統都把它指向 `generic_file_read_iter`，共用 VFS 提供的 page cache 讀邏輯。
3. **慢路徑會睡**。miss 要等磁碟時，發起 read 的 process 進 `TASK_UNINTERRUPTIBLE` 睡著，讓出 CPU（Ch 14 context switch）；磁碟讀完發中斷，中斷處理喚醒它（Ch 29），它才醒來繼續 `copy_to_user`。read 之所以「阻塞」，就是這段睡眠。

## 第 1 站：syscall 邊界 → `ksys_read`（`fs/read_write.c`）

`read(fd, buf, n)` 是 glibc 的一個 wrapper，底下是 `read` syscall（x86_64 上 nr = 0）。Ch 4 已經把這段講透：`syscall` 指令換棧、換 GS、`do_syscall_64`（`arch/x86/entry/common.c`）拿 nr 查 `sys_call_table`，呼叫 `__x64_sys_read`。這個 wrapper 由 `fs/read_write.c` 的

```c
SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
{
    return ksys_read(fd, buf, count);
}
```

展開而來（`__x64_sys_read` → `__se_sys_read` → `__do_sys_read`，三層 wrapper 是 `SYSCALL_DEFINE3` 生的，見 Ch 4）。注意 `buf` 帶 `__user` 標註——它是使用者空間的指標，kernel **不能直接解參考**，這是後面 `copy_to_user` 的伏筆。

`ksys_read`（同檔）做三件事：

```c
ssize_t ksys_read(unsigned int fd, char __user *buf, size_t count)
{
    struct fd f = fdget_pos(fd);           // ① fd ─► struct file
    ssize_t ret = -EBADF;
    if (fd_file(f)) {
        loff_t pos, *ppos = file_ppos(fd_file(f));
        if (ppos) { pos = *ppos; ppos = &pos; }
        ret = vfs_read(fd_file(f), buf, count, ppos);   // ② 真正幹活
        if (ret >= 0 && ppos)
            fd_file(f)->f_pos = pos;        // ③ 更新檔案讀寫位置
        fdput_pos(f);
    }
    return ret;
}
```

（v6.12 的 `struct fd` 用低位 bit 打包旗標，取檔案要用 `fd_file(f)` 巨集；細節可去 elixir 查 `include/linux/file.h`，這裡重點在流程。）

- **① `fdget_pos(fd)`**：把整數 fd 換成 `struct file *`。fd 只是 process 的 `files_struct`（fd table，Ch 33）裡的一個索引；`fdget` 走 `current->files->fdt->fd[fd]` 拿到那個 `struct file`，並增加它的引用計數（`fget` 的輕量版，靠 RCU 免鎖，見 Ch 27）。`_pos` 版本額外處理 `f_pos` 的並行——同一個 open file 被多執行緒同時 read 時，`f_pos` 的更新要串行化，`fdget_pos` 會拿 `f_pos_lock`。這就是為什麼兩個 thread 共用一個 fd 讀同一檔案不會讀到重疊區。
- **② `vfs_read`**：把使用者的意圖（讀 count bytes 到 buf）交給 VFS 層。下一站。
- **③ 更新 `f_pos`**：read 成功後把檔案位置往前推。這是「檔案有個 cursor」這個抽象的實作——`f_pos` 存在 `struct file` 裡，不在 inode 裡，所以同一檔案不同 open 各有各的位置。`pread`（帶 offset 的 read）走不同路徑，不動 `f_pos`（`file_ppos` 回 NULL）。

## 第 2 站：`vfs_read` 與多型分派（`fs/read_write.c`）

`vfs_read`（`fs/read_write.c`）是 VFS 的守門員：

```c
ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
{
    if (!(file->f_mode & FMODE_READ))     return -EBADF;   // 這 fd 是唯讀開的嗎
    if (!(file->f_mode & FMODE_CAN_READ)) return -EINVAL;  // 這 file 支援 read 嗎
    if (unlikely(!access_ok(buf, count))) return -EFAULT;  // user buf 位址合法嗎（Ch 4）
    ...
    ret = rw_verify_area(READ, file, pos, count);          // 範圍/mandatory lock 檢查
    if (ret) return ret;

    if (file->f_op->read)
        ret = file->f_op->read(file, buf, count, pos);
    else if (file->f_op->read_iter)
        ret = new_sync_read(file, buf, count, pos);        // 包成 iov_iter 再走 read_iter
    else
        ret = -EINVAL;
    ...
    fsnotify_access(file);   // inotify/fanotify 的觀測點（bpf/observability 課用得到）
    return ret;
}
```

兩個設計點值得停下來看：

**第一，這裡是多型分派的分岔口。** `file->f_op` 是 `const struct file_operations *`（Ch 33），由檔案系統在 open 時填。現代檔案系統幾乎都只填 `read_iter` 而不填舊的 `read`——因為 `read_iter` 收的是 `struct iov_iter`（一個可以描述「散在多處的緩衝區」的抽象），能同時服務 `read`、`readv`、`preadv`、甚至 io_uring，不必為每種變體寫一份。`new_sync_read`（同檔）就是把單一 `(buf, count)` 包裝成一個單段的 `iov_iter`，再呼叫 `file->f_op->read_iter`。這是 kernel 「用一個更泛用的介面吃掉一堆特例」的典型手法。

**第二，`access_ok(buf, count)` 在這裡就先擋一次。** 它只檢查位址範圍**落在 user 空間**（不讓你用 read 把資料寫進 kernel 位址），不保證那段記憶體真的可寫——真正搬資料時的 fault 由 `copy_to_user` 的 exception table 兜底（Ch 4）。

對一般磁碟檔案，`read_iter` 這個函式指標指向 **`generic_file_read_iter`**（`mm/filemap.c`）。VFS 提供這個共用實作，檔案系統直接借用；它做完 O_DIRECT 判斷後，buffered read 就落到 `filemap_read`。下一站進 mm 世界。

## 第 3 站：`filemap_read` 與 page cache 命中判斷（`mm/filemap.c`）

到這裡就踏進 Ch 21 的地盤了。`generic_file_read_iter` 先看 `iocb->ki_flags` 有沒有 `IOCB_DIRECT`（O_DIRECT，本章後面談），沒有就呼叫 `filemap_read`（`mm/filemap.c`）。Ch 21 已經拆過它的骨架，這裡從「read 路徑」的角度再走一遍，重點在**命中/未命中的分岔**：

```c
ssize_t filemap_read(struct kiocb *iocb, struct iov_iter *iter, ssize_t already)
{
    ...
    do {
        // ① 依 read 範圍，把需要的 folio 準備好（可能觸發 readahead + 磁碟讀）
        error = filemap_get_pages(iocb, iter->count, &fbatch, ...);
        if (error < 0) break;

        for (i = 0; i < folio_batch_count(&fbatch); i++) {
            struct folio *folio = fbatch.folios[i];
            ...
            // ② 把 folio 內容 copy_to_user 到使用者 buffer
            copied = copy_folio_to_iter(folio, offset, bytes, iter);
            ...
        }
    } while (iov_iter_count(iter) && ...);
    ...
    ppos = iocb->ki_pos;          // 回報最終讀到哪
    return already ? already : copied_total;
}
```

**`filemap_get_pages`** 是命中判斷的核心（`mm/filemap.c`）：

1. `filemap_get_read_batch()` 對 read 範圍的每個 page index，去 `address_space->i_pages`（那棵 xarray，Ch 21）查對應的 folio。
2. **命中**：folio 在、而且 `folio_test_uptodate()` 為真（內容確實已從磁碟讀好）。直接把它收進 batch，回到 `filemap_read` 進 `copy_folio_to_iter`——這就是**快路徑**，全程沒碰磁碟，就是一次 xarray 查找加一次記憶體複製。
3. **未命中**：folio 不在，或在但還沒 uptodate。呼叫 `page_cache_sync_readahead()`（`mm/readahead.c`）發起磁碟讀——進**慢路徑**。

命中路徑到此結束：`copy_folio_to_iter` 內部就是 `copy_to_user`（Ch 4），把 page cache 那頁的位元組搬進 user buffer，`read` 返回。你第二次讀同一檔案之所以是微秒級，就因為第一次讀時資料已經留在 page cache，這次直接命中，連 VFS 底下的檔案系統程式碼都不必碰。

## 第 4 站：慢路徑——readahead、bio、睡眠、喚醒

未命中時，`read` 得真的去磁碟拿資料。這一段把 mm、檔案系統、block layer、中斷、排程全牽進來。

### 4.1 readahead：不只讀你要的那頁

`page_cache_sync_readahead`（`mm/readahead.c`）不會只讀你缺的那一頁，而是**多讀後面幾頁**。理由 Ch 21 講過：磁碟 I/O 的固定成本（發指令、尋道、中斷）很高，既然都要發一次 I/O，多讀相鄰頁幾乎不加成本，卻能讓後續順序讀直接命中。kernel 為每個開啟的檔案維護 `struct file_ra_state`（`include/linux/fs.h`），記著上次讀到哪、預讀窗多大，順序讀時窗會長大，隨機讀時窗縮小甚至關掉。

readahead 決定要讀哪些頁後，替這些頁配 folio、掛進 page cache（標記為 not-uptodate、上鎖），然後呼叫檔案系統的 `address_space_operations`（Ch 21）：

- `a_ops->readahead(rac)`：現代主流，一次收一批頁，檔案系統把它們打包成盡量少的 bio。
- `a_ops->read_folio(file, folio)`：一次一頁的舊介面（舊名 `readpage`），同步讀單頁時走它。

### 4.2 建 bio、下 block layer

檔案系統（例如 ext4）在 `read_folio`/`readahead` 裡做的事，本質是：**把「檔案的第 N 頁」翻譯成「磁碟上的哪個 block」，建一個 `struct bio` 描述這次讀，提交給 block layer**。

- 「檔案第幾頁 → 磁碟哪個 block」這個翻譯，靠檔案系統的 extent/block map（ext4 的 extent tree、或舊的間接塊）。這是檔案系統的核心職責：VFS 只知道「檔案的第 N 頁」這種邏輯位置，把它變成磁碟上的實體磁區是各檔案系統自己的事——這也是為什麼 `read_folio`/`readahead` 這兩個 op 由檔案系統填而不是 VFS 通用實作。Ch 35 你自己寫最小檔案系統時會親手實作這段映射邏輯。
- `bio`（`include/linux/bio.h`）是 block layer 的 I/O 描述單位：讀哪個裝置、哪個磁區、資料放進哪些 folio。`submit_bio(bio)` 把它交出去。一次 readahead 讀多頁時，檔案系統會盡量把「磁碟上連續」的頁合併進同一個 bio（減少 I/O 次數），這是 readahead 對順序讀特別有效的另一個原因——不只多讀，還讀得更集中。
- 之後就是 Ch 36 的世界：bio 進 blk-mq 的軟/硬佇列、經 I/O scheduler、下到裝置驅動、發實際的 I/O 指令。這裡先當「submit 出去，資料稍後會填進那些 folio，填完 folio 被標 uptodate」；bio 提交後在 blk-mq 裡到底怎麼排、怎麼合併、怎麼下到 NVMe/SATA 驅動，是 Ch 36 的主題。

### 4.3 process 睡下去（Ch 9 / Ch 26）

同步 read 提交 bio 後，資料還沒到，`read` 不能返回。發起 read 的 process 於是**睡**：它把自己掛上等這頁 I/O 完成的等待隊列，狀態設成 **`TASK_UNINTERRUPTIBLE`**，然後呼叫 `schedule()` 讓出 CPU（Ch 14）。等 folio 變 uptodate（`folio_wait_bit` / `folio_lock` 在等 `PG_locked` 被清）它才會被喚醒。

為什麼是 `TASK_UNINTERRUPTIBLE` 而不是可被 signal 打斷的 `TASK_INTERRUPTIBLE`？因為 I/O 已經下到裝置、DMA 可能正往那頁寫，這時若讓 signal 把 read 中途拉回、把 buffer 回收，資料會寫進已釋放的記憶體。所以磁碟 I/O 這種「已經上路、不能安全取消」的等待用 `D` 狀態（uninterruptible）。**這正是你 `ps` 看到 `D` 狀態、`kill -9` 也殺不掉的那個 process**——它卡在磁碟 I/O，kernel 故意不讓 signal 碰它，直到 I/O 回來。這串到 Ch 9 的 task 狀態機、linux_commands 課裡 `ps`/`top` 的 `D` 狀態解讀。

### 4.4 磁碟完成 → 中斷 → 喚醒（Ch 29）

磁碟把資料讀進來後（透過 DMA 直接寫進那些 folio 的實體頁，Ch 41），發一個**中斷**通知 CPU「這批 I/O 好了」。中斷處理（Ch 29 的 top half / bottom half）跑 bio 的 completion callback：

1. 把讀好的 folio 標成 `uptodate`、解鎖（清 `PG_locked`）。
2. 喚醒等在這頁上的 process（`wake_up`，把它從 `D` 拉回 `TASK_RUNNING`，重新排進 runqueue）。

被喚醒的 process 下次被排到 CPU，就從當初 `schedule()` 的地方接著跑，發現 folio 現在 uptodate 了，回到 `filemap_read` 的迴圈做 `copy_folio_to_iter`（`copy_to_user`），`read` 終於返回。順帶一提，這頁**留在 page cache 裡**——下次讀就命中快路徑了。這就是「第一次慢、第二次快」的完整因果。

把這條慢路徑當一次「跨越三個執行脈絡」的接力看，會更清楚它為什麼牽動這麼多子系統：**第一棒是 process context**——發 read 的行程在自己的 kernel stack 上一路呼叫到 `submit_bio`，然後主動 `schedule()` 睡下，讓出 CPU。這中間 CPU 不是空轉，排程器（Ch 11/12）挑別的 task 來跑，磁碟在背景慢慢搬資料。**第二棒是 interrupt context**——磁碟完成時的中斷打斷了「當時剛好在跑的那個不相干 task」，在它的脈絡裡跑 bio completion，把 folio 標好、`wake_up` 那個睡著的行程（只是把它設回 runnable、丟進 runqueue，並不立刻切過去）。**第三棒又回到 process context**——原行程被排回 CPU，從 `schedule()` 之後接著跑完 `copy_to_user`。理解「發起、睡、被別人的中斷喚醒、再被排回來」這個三段接力，就理解了 kernel 裡幾乎所有阻塞式 I/O 的骨架——read 只是最典型的一個實例，Ch 44 收網路封包、Ch 41 等裝置中斷，全是這個形狀。

## 底層機制：major / minor fault 的類比，與「為什麼 read 可能不讀滿」

### cache hit / miss ↔ minor / major fault

read 的快慢分岔和缺頁（page fault，Ch 19）的分岔是同一個結構，值得並排看：

| | read() 路徑 | page fault 路徑（Ch 19/20） |
|---|---|---|
| 資料已在記憶體 | page cache **命中**（快路徑，純複製） | **minor fault**：頁在 page cache/已配置，只需建 PTE 映射 |
| 資料要下磁碟 | page cache **未命中**（慢路徑，發 bio、睡、等中斷） | **major fault**：頁不在記憶體，要從磁碟/swap 讀進來 |
| 代價 | 微秒 vs 毫秒 | 奈秒級 vs 毫秒級 |

它們共用同一個 page cache、同一套 readahead、同一條「miss 就發 bio、睡、等中斷喚醒」的慢路徑。差別只在觸發者：一個是 `read()` syscall 主動查表，一個是 CPU 存取未映射位址被動觸發 `#PF`。理解其中一條，另一條就通了。這也是為什麼 `mmap` 讀和 `read` 讀底下能共用大半機制（下一節）。

### 為什麼一次 read 可能回傳 < n

`read(fd, buf, 4096)` 回傳 1000 不是 bug，POSIX 允許 short read。常見原因：

- **讀到檔案尾（EOF）**：檔案只剩 1000 bytes 可讀，回 1000。再讀一次回 0（0 = EOF）。
- **管道 / socket / tty**：對端只送了 1000 bytes 進來，read 不會傻等湊滿 4096，有多少給多少（這是串流語意，networking 課的 TCP 也是這樣）。
- **被 signal 打斷**：讀阻塞式來源（如 tty、pipe，這些用 `TASK_INTERRUPTIBLE` 睡）時，若已搬了一部分才收到 signal，回傳已搬的位元組數；若一個 byte 都還沒搬就被打斷，回 `-EINTR`（除非設了 `SA_RESTART`，見 Ch 16 signal / linux_commands 課）。

結論：**正確的 read 一定寫成迴圈**，用回傳值推進，直到湊滿或遇到 0/錯誤。把 `read` 當「一次讀滿 n」用，遇到 pipe、socket、signal 時就會出錯。

### copy_to_user：為什麼不能直接寫 user buffer

`filemap_read` 拿到 folio 後，是用 `copy_folio_to_iter`（底層 `copy_to_user`）把資料搬進 `buf`，而不是 `memcpy(buf, ...)`。原因 Ch 4 講過，這裡是它的實戰現場：

- `buf` 是**使用者提供**的指標。它可能是壞的（野指標）、可能指向沒映射的頁、可能在 read 進行中被別的 thread `munmap` 掉。直接 `memcpy` 到一個壞的 user 位址，會在 kernel context 觸發 fault → 若沒防護就是 kernel oops。
- `copy_to_user` 先 `access_ok` 確認位址落在 user 空間，再用帶 exception fixup 的搬運：真的 fault 了，靠 `__ex_table`（Ch 4）把 fault 導向 fixup，回傳「沒搬完的位元組數」而非 panic。所以它回傳非 0 = user buffer 有問題 → read 回 `-EFAULT`。

**kernel 碰 user 記憶體永遠走 `copy_to_user`/`copy_from_user`**，這是 user/kernel 邊界的鐵律，read 路徑只是它最常見的一次應用。

## mmap 讀 vs read 讀：兩條路，同一個 page cache

同樣是讀一個檔案，`read()` 和 `mmap()` 底下走的路不同，但**共用同一個 page cache**：

```
  read(fd, buf, n):
     syscall ─► vfs_read ─► filemap_read ─► 查 page cache
                                              │ miss ─► 發 bio 讀進 cache
                                              └─► copy_to_user 到 buf     ← 有一次複製

  訪問 mmap 出來的位址 p[i]:
     CPU load ─► （若無 PTE）#PF ─► filemap_fault (mm/filemap.c) ─► 查 page cache
                                              │ miss ─► 發 bio 讀進 cache
                                              └─► 把該頁的 PTE 指向 cache folio  ← 無複製，直接映射
```

- **read**：資料經過 page cache，再**複製一份**到 user 的 `buf`（`copy_to_user`）。user 拿到的是副本。
- **mmap**：透過 page fault（Ch 19 的 `filemap_fault`）把 page cache 那頁**直接映射**進 process 位址空間，user 的指標直接指向 cache 頁，**不複製**。省掉一次 memcpy，但每次碰到新頁要付一次 minor/major fault 的成本。

兩者的 miss 慢路徑幾乎一模一樣（都是發 bio、睡、等中斷、資料進 page cache）——差別只在「拿到資料後是複製給 user，還是把 PTE 指過去」。這解釋了為什麼「用 mmap 讀大檔可能比 read 快」：省了複製；也解釋為什麼 mmap 不總是贏：隨機小量存取時，page fault 的開銷可能比 read 的一次系統呼叫還貴。這條 mmap 線接 Ch 19/20。

## write 路徑：對稱地反過來走（簡述）

write 是 read 的鏡像，主角換成 `vfs_write`（`fs/read_write.c`）→ `generic_file_write_iter` → `generic_perform_write`（`mm/filemap.c`）：

1. `copy_from_user` 把 user buffer 的資料搬進 page cache 的 folio（read 是 `copy_to_user` 反向）。
2. 把該 folio 標成 **dirty**（`a_ops->dirty_folio`，並在 xarray 打上 `PAGECACHE_TAG_DIRTY`，Ch 21）。
3. `write()` **就返回了**——資料只到 page cache，還沒落磁碟。這就是 buffered write。
4. 稍後由 **writeback**（`kworker`/`flusher` 執行緒，Ch 21）掃 dirty tag，把 dirty folio 打包成 bio 寫回磁碟。或你主動呼叫 `fsync` 強制寫回。

有一個不對稱的細節值得點出：**部分寫（不足一頁的 write）可能先觸發一次讀**。write 的操作單位是 folio，但你可能只寫一頁中間的幾十個 byte。若那頁還沒在 page cache（miss），kernel 得先把整頁從磁碟讀進來（`write_begin` 裡的 read-modify-write），改掉你要改的那幾個 byte，再標 dirty——一次「寫」底下藏了一次「讀」。這也是為什麼對齊頁邊界的大塊 write 比零碎小 write 有效率：對齊整頁時可以整頁覆蓋、省掉那次預讀。

所以 `write` 回傳成功**不代表資料在磁碟上**——它在 page cache 等 writeback。斷電時這批未寫回的 dirty 頁會丟。要保證落盤得 `fsync`（資料庫的 durability 就靠這個）。read 命中 page cache 快、write 回 page cache 就返回，兩者都是 page cache 這個「記憶體與磁碟之間的緩衝層」帶來的加速，代價是一致性要另外靠 fsync/writeback 管。這條 write 路徑到 Ch 21 的 writeback 那節有完整展開，本章只到「進 page cache 標 dirty 就返回」為止。

## O_DIRECT：繞過 page cache

`open` 時給 `O_DIRECT`，read/write 就**繞過 page cache**，在 user buffer 和磁碟之間直接 DMA。路徑在 `generic_file_read_iter` 開頭就分岔：`IOCB_DIRECT` 為真時走 `a_ops->direct_IO`（不進 `filemap_read`）。

- **為什麼要繞**：資料庫、大型檔案伺服器自己管快取，比 kernel 的通用 page cache 更懂自己的存取模式。讓 kernel 再快取一份是浪費記憶體、還會污染別人的 cache（把有用的頁擠出去）。O_DIRECT 讓它們拿回控制權。
- **代價**：失去 page cache 的加速——每次讀都真的碰磁碟，沒有「第二次命中」的便宜。而且有對齊要求（buffer 位址、offset、長度通常要對齊到磁區/區塊大小），用起來刁鑽。
- **這是 Ch 21 講「page cache 是機制、不是必經之路」的實例**：多數 I/O 走 page cache，但 kernel 給了想自己管快取的人一條旁路。

## 動手：用三種工具看這條路

Ch 0 的 QEMU + gdb 環境現在派上用場。三種角度看同一條 read 路徑，粗細不同。

### strace：看 syscall 這一層（接 observability_tools）

最外層視角。strace 攔 syscall 進出，看 `read` 收什麼參數、回什麼：

```bash
# 建一個 5000 bytes 的檔案，用小 buffer 讀，觀察 short read
$ dd if=/dev/urandom of=/tmp/f bs=5000 count=1
$ strace -e trace=read dd if=/tmp/f of=/dev/null bs=4096 2>&1 | head
read(3, "..."..., 4096) = 4096      # 第一次讀滿
read(3, "..."..., 4096) = 904       # 第二次只剩 904 bytes（short read！）
read(3, "", 4096)       = 0         # 再讀回 0 = EOF
```

親眼看到「回傳 < n」與「0 = EOF」。strace 是 observability_tools 課的主角，這裡用它把抽象的「read syscall」變成看得見的一行。

### ftrace function graph：追 vfs_read 往下的呼叫鏈（接 Ch 51/53）

想看 kernel **內部**的呼叫鏈——`vfs_read` 底下到底呼叫了誰——用 ftrace 的 function graph tracer。在 QEMU 裡跑的目標 kernel 上（需開 `CONFIG_FUNCTION_GRAPH_TRACER`）：

```bash
cd /sys/kernel/tracing
echo function_graph > current_tracer
echo vfs_read > set_graph_function       # 只從 vfs_read 開始畫
echo 1 > tracing_on
cat /tmp/f > /dev/null                    # 觸發一次 read
echo 0 > tracing_on
cat trace
```

你會看到類似（cache 命中路徑）：

```
 vfs_read() {
   new_sync_read() {
     generic_file_read_iter() {
       filemap_read() {
         filemap_get_pages() { ... }
         copy_folio_to_iter() { ... }
       }
     }
   }
 }
```

第一次讀（cache 冷）你還會在裡面看到 `page_cache_ra_unbounded`（readahead）、`submit_bio` 一路下去；第二次讀（cache 熱）這些就消失了——**在 trace 裡直接看到快慢兩條路的分岔**。ftrace 是 Ch 51/53 與 bpf 課的核心工具，這裡是它最直觀的一次應用。

### gdb：停在關鍵函式看狀態

想看某個時刻的資料結構，用 gdb（Ch 0 的環境）。QEMU 開機到 shell 後，host 上：

```gdb
(gdb) break vfs_read
(gdb) break filemap_read
(gdb) continue
```

回 QEMU 的 shell `cat /tmp/f`，gdb 停在 `vfs_read`：

```gdb
(gdb) print file->f_op->read_iter        # 看多型指到哪（tmpfs/ext4 各不同）
(gdb) print file->f_pos                  # 目前檔案位置
(gdb) print file->f_inode->i_size        # 檔案總大小 → 推 short read 會不會發生
(gdb) finish                             # 跑完看回傳（實際讀了幾 bytes）
```

在 `filemap_read` 裡可以 `print iocb->ki_pos`、看 `filemap_get_pages` 回來 folio batch 有幾頁，判斷命中還是 miss。**這就是把這一章畫的圖，在真 kernel 上一站一站對出來。**

### perf：量 cache hit vs miss 的延遲差

想量化快慢差，用 perf（observability_tools）比第一次讀（冷 cache）和第二次讀（熱 cache）的延遲：

```bash
# 先清 page cache，量冷讀
$ echo 3 > /proc/sys/vm/drop_caches
$ perf stat -e task-clock cat /tmp/bigfile > /dev/null     # 慢：真的下磁碟
# 不清，量熱讀
$ perf stat -e task-clock cat /tmp/bigfile > /dev/null     # 快：全命中 page cache
```

熱讀通常快一到數個數量級。這個差距就是 page cache 那一層在替你擋磁碟。

## 對比與取捨

| 路徑 | 資料流 | 何時快 | 何時是坑 |
|---|---|---|---|
| buffered read（預設） | 磁碟 → page cache → `copy_to_user` → buf | 資料重複讀、順序讀（命中 + readahead） | 只讀一次的大檔會污染 cache、擠掉別人熱頁 |
| mmap read | 磁碟 → page cache → 映射 PTE（無複製） | 大檔、隨機讀、多 process 共享 | 隨機小量存取時 page fault 開銷可能超過 read；SIGBUS 難處理 |
| O_DIRECT | 磁碟 ↔ user buffer（繞 page cache） | 自管快取的資料庫、避免雙重快取 | 對齊要求嚴、失去 kernel 快取、寫錯直接慢 |
| buffered write | buf → page cache 標 dirty → 稍後 writeback | 寫吞吐高（延遲落盤、可合併） | write 返回 ≠ 落盤，斷電丟資料，要 fsync |

## 踩雷集錦

1. **「read 一定讀滿 n」**——錯。short read 是合法的（EOF、pipe/socket 串流、signal 打斷）。正確認識：**read 一律寫迴圈**，用回傳值推進，直到湊滿或遇 0/負值。把 read 當 `read_exactly` 用，遲早在 pipe/socket 上翻車。

2. **「write 成功就代表資料在磁碟上」**——錯。buffered write 只把資料放進 page cache 標 dirty 就返回，真正落盤靠 writeback 或 `fsync`。要 durability（資料庫、日誌）必須 `fsync` 且檢查它的回傳值。斷電時 page cache 裡沒 flush 的 dirty 頁會丟。

3. **「D 狀態的 process 是卡死了，kill -9 應該能殺」**——錯。`D`（`TASK_UNINTERRUPTIBLE`）是它在等一個**不能被 signal 打斷的磁碟 I/O**，kernel 故意不讓 signal 碰它，直到 I/O 回來。`kill -9` 對它無效不是 bug，是設計。真正該查的是「為什麼那個 I/O 這麼久回不來」（磁碟壞、NFS server 掛了）。這串 Ch 9 與 linux_commands 課。

4. **「read 慢一定是磁碟慢」**——不一定。第一次讀慢是 cache miss 下磁碟（慢路徑）；如果每次都慢，可能是隨機存取讓 readahead 失效、或記憶體不夠 page cache 一直被回收（Ch 22）。用 perf 對比冷熱讀、用 ftrace 看有沒有進 `submit_bio`，才知道慢在哪。

5. **「mmap 讀一定比 read 快」**——看場景。mmap 省一次複製，但每碰一個新頁付一次 page fault；隨機小量存取時 fault 開銷可能贏不了 read 的一次 syscall。而且 mmap 讀到 I/O 錯誤是 `SIGBUS`（比 read 的 `-EIO` 難處理）。別無腦換 mmap。

## 進階：再往深一層

- **io_uring 走的是同一條底層路徑**。io_uring 的 read 最終還是走 `read_iter`/`filemap_read`，只是把「發起請求」和「拿結果」拆成非同步兩段，用共享 ring buffer 免去每次 read 一個 syscall 的來回。底層的 page cache、bio、readahead 邏輯不變。理解本章這條同步路徑，是理解 io_uring 為什麼快的前提。
- **readahead 的自適應**。順序讀時預讀窗指數成長（`file_ra_state`），隨機讀時 kernel 偵測到命中率低會縮窗甚至關掉。`posix_fadvise(POSIX_FADV_SEQUENTIAL/RANDOM)` 可以主動給提示，資料庫常用來調 readahead 行為。
- **page cache 與 reclaim 的拉扯（Ch 22）**。page cache 會吃掉大量「空閒」記憶體（`free` 裡的 `buff/cache`），記憶體吃緊時 kernel 從 LRU 回收 clean 頁（直接丟）或寫回 dirty 頁再丟。所以「read 過的檔案下次還在不在 cache」取決於記憶體壓力，不保證。
- **面試常問**：「說一次 `read(fd, buf, 4096)` 從 user 到磁碟發生了什麼？」——這正是本章這條線。能從 syscall 分派 → fd 換 file → VFS 多型 → page cache 命中/未命中 → readahead/bio → 睡眠/中斷喚醒 → copy_to_user 一路講清楚，並點出快慢路徑分岔與 `D` 狀態，就是一個扎實的答案。

## 動手練習

1. **在 ftrace 裡看到快慢兩條路**：`echo 3 > /proc/sys/vm/drop_caches` 後用 function_graph 追一次 `cat 檔案`（冷讀），存下 trace；再追一次（熱讀）。對比兩份 trace，找出冷讀有、熱讀沒有的函式（`submit_bio`、readahead 相關）。這就是快慢路徑的分岔在源碼層的證據。

2. **用 gdb 抓 short read**：建一個 5000 bytes 的檔案，`break vfs_read`，在 QEMU 裡用 `dd bs=4096` 讀它。每次停下時 `print file->f_pos` 和 `file->f_inode->i_size`，`finish` 看回傳值。觀察第二次 read 的回傳值 < 4096——並解釋為什麼（`i_size - f_pos < 4096`）。

3. **證明 write 沒立刻落盤**：寫一個小檔（不 fsync），立刻 `cat /proc/meminfo | grep Dirty` 看 Dirty 頁增加；等幾秒（或 `sync`）再看它歸零。這就是 buffered write → dirty → writeback 的三步在你眼前發生。

4. **弄出一個 D 狀態**（進階，需要一個慢裝置）：對一個 loop-mount 的、故意限速的裝置發大量隨機 read，`ps -eo pid,stat,comm | grep ' D'` 抓那個 `D` 狀態的 process，試 `kill -9` 證明殺不掉，I/O 回來後它自己消失。體會 uninterruptible sleep 為什麼存在。

## 本章重點整理

- 一次 `read` 穿過四層：syscall 分派（Ch 4）→ VFS `vfs_read` 多型分派到 `f_op->read_iter`（Ch 33）→ `filemap_read` 查 page cache（Ch 21）→ 命中就 `copy_to_user` 返回，未命中就下 block layer（Ch 36）。
- **page cache 命中與否是快慢分水嶺**：命中是純記憶體複製（微秒），未命中要 readahead + bio + 睡眠 + 中斷喚醒（毫秒），差三個數量級。這與 minor/major page fault 是同構的。
- read 阻塞的本質是 `TASK_UNINTERRUPTIBLE` 睡眠等磁碟（Ch 9），磁碟完成中斷（Ch 29）喚醒它——這就是 `ps` 裡 `D` 狀態、`kill -9` 殺不掉的由來。
- read 可能回傳 < n（EOF/串流/`-EINTR`），所以要寫迴圈；write 回 page cache 標 dirty 就返回，落盤靠 writeback/`fsync`；`O_DIRECT` 繞過 page cache 讓程式自管快取；kernel 碰 user buffer 一律走 `copy_to_user`。

## 自我檢核

- [ ] 不看筆記，能從 `read(fd, buf, n)` 一路講到磁碟再回來，點出每一站的函式（`ksys_read`/`vfs_read`/`generic_file_read_iter`/`filemap_read`）與它做的決定
- [ ] 能解釋 page cache 命中與未命中兩條路差在哪，並把它和 minor/major fault 對應起來
- [ ] 能說清楚為什麼卡在磁碟 I/O 的 process 是 `D` 狀態、`kill -9` 為什麼殺不掉
- [ ] 能講出 read 為什麼可能回傳 < n（至少三種情況），以及正確的 read 迴圈長怎樣
- [ ] 能說明 mmap 讀和 read 讀共用什麼（page cache）、差在哪（複製 vs 映射 PTE）
- [ ] 面試被問「一次 read 發生了什麼」，能結構化地答出四層 + 快慢分岔 + 阻塞機制

## 延伸閱讀

### 官方文件與源碼

- **`fs/read_write.c`（v6.12）** — [elixir](https://elixir.bootlin.com/linux/v6.12/source/fs/read_write.c)
  - **讀哪裡**：`ksys_read`、`vfs_read`、`new_sync_read`。這是 read 路徑 VFS 段的本體，本章第 1、2 站的源碼
  - **怎麼讀**：對著本章的圖，一個函式一個函式往下跳，確認多型分派（`f_op->read_iter`）的分岔點

- **`mm/filemap.c`（v6.12）** — [elixir](https://elixir.bootlin.com/linux/v6.12/source/mm/filemap.c)
  - **讀哪裡**：`generic_file_read_iter`、`filemap_read`、`filemap_get_pages`、`filemap_fault`（mmap 那條）
  - **和本章關聯**：第 3、4 站與 mmap 對比節的源碼；配 Ch 21 的 page cache 講解一起讀

- **[Documentation/filesystems/vfs.rst](https://www.kernel.org/doc/html/latest/filesystems/vfs.html)**
  - **讀哪裡**：`file_operations` 與 `address_space_operations` 兩節
  - **能學到什麼**：VFS 多型的官方定義——哪些 op 由誰填、read 路徑用到哪幾個

### 書籍

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly, 2005）
  - **讀哪裡**：「Accessing Files」「The Page Cache」兩章
  - **定位**：把 read/write 路徑講得最細的經典（雖以舊 kernel 為例，`generic_file_*` 的骨架與 page cache 命中邏輯至今仍適用）。函式名以 v6.12 為準

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **讀哪裡**：「The Virtual Filesystem」「The Block I/O Layer」「The Page Cache and Page Writeback」三章
  - **定位**：比 ULK 好讀，適合先建立整條路的骨架再回去啃源碼

### 橫向

- 本 repo **observability_tools** 課：strace / ftrace / perf 的完整用法。本章的三種觀測手法在那裡有系統性的展開
- 本 repo **linux_commands** 課：`read`/`fd`/`ps` 的 `D` 狀態、`/proc/meminfo` 的 Dirty 從使用者視角看，與本章的 kernel 內部互為表裡

這一章把 read 從頭走到尾，慢路徑那一段停在了「submit_bio 交給 block layer」。下一章我們先自己寫一個最小的 in-memory 檔案系統，把 `file_operations`/`address_space_operations` 這些你這章一直在呼叫的介面**親手填一遍**；再往後 Ch 36 才拆開 block layer，看 bio 提交後在 blk-mq 裡到底怎麼走到裝置。

→ [Ch 35 最小 in-memory 檔案系統](./35-minimal-filesystem.md)
