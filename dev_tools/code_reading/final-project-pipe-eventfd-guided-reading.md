# Final Project — 完整導讀 fs/pipe.c 與 eventfd

> **目標**：用整門課的方法，為 Linux v6.18 的 `fs/pipe.c`（1553 行）與 `fs/eventfd.c`（432 行）寫一份別人拿去就能讀懂這兩個檔案的**導讀報告**：從五道鴻溝的初始診斷，到資料結構地圖、入口點、關鍵路徑、並行、git 考古，最後用 user-space 實驗驗證你的理解。
> **鴻溝**：本專案五道鴻溝全部要練；報告的第一節就是診斷你自己在這兩個檔案上卡在哪幾道。

> **環境**：WSL Ubuntu 22.04、gcc 11.4、kernel tree 在 tag v6.18（`~/code_reading/linux`）。WSL 執行中的核心是 `6.18.58.1-microsoft-standard-WSL2`（Microsoft 以 6.18 線為底的版本），和 tree 同一條版本線；本文所有實驗都在這個核心上實際編譯執行，輸出是真的。實驗不需要 root。

## 背景與動機

pipe 是 Unix 最老的 IPC，1973 年就在了；eventfd 是 2007 年才加進 Linux 的「只有一個計數器的檔案」。兩者看起來毫無關係，卻是讀 kernel 的理想組合：

- **大小剛好**：合起來兩千行，一個人一到兩週讀得完，又大到不能從頭硬讀
- **五道鴻溝全有**：`union pipe_index` 與 `smp_load_acquire`（慣用法）、`file_operations` 與 `pipe_buf_operations` 兩層 ops table（間接）、wait queue 與 page 的生命週期（領域）、一連串 race 修正（脈絡）、檔案大到逼你帶問題讀（目標）
- **可以驗證**：每一個語意問題都能寫一個 user-space 小程式驗證，不需要 QEMU
- **在生產環境天天被用**：shell 的 `|`、GNU make jobserver、KVM 的 irqfd、io_uring 的完成通知、cgroup v1 的記憶體事件，全都踩在這兩個檔案上

這份報告的讀者設定是「三個月前的你」：會寫 C、上過本課 Part 1、沒讀過 pipe.c。它要讓那個人在一個下午內建立正確的心智模型。

## 任務規格

報告必須包含以下十節，每節的最低要求與它整合的章節：

| # | 章節 | 最低要求 | 整合本課 |
|---|---|---|---|
| 1 | 五道鴻溝的初始診斷 | 讀報告前先花 30 分鐘冷讀，記下每道鴻溝各至少一個具體卡點（附 file:line） | Ch 1、Ch 54 |
| 2 | 問題清單 | 至少 8 個具體問題，標出哪些在報告中回答、哪些留作未解 | Ch 3 |
| 3 | 資料結構地圖 | `pipe_inode_info`、`pipe_buffer`、`pipe_buf_operations`、`union pipe_index`、`eventfd_ctx`；畫出指向關係與 ring 的 head/tail 語意 | Ch 5、11、16、20、27、練習 D |
| 4 | 入口點清單 | `pipe2`/`pipe` syscall、兩張 `file_operations`（**注意：v6.18 有兩張**）的每個回呼、`F_SETPIPE_SZ` 的 fcntl 路徑、`eventfd2` syscall、`eventfd_fops`、`eventfd_signal()` 的 kernel 內使用者（至少列 5 個子系統） | Ch 28、29、42、47 |
| 5 | 關鍵路徑 call path | pipe write 滿時阻塞與被喚醒、pipe read、eventfd read/write 的計數器語意；splice 選讀 | Ch 8、10、17、29、練習 E |
| 6 | 並行 | 每把鎖保護什麼（`pipe->mutex`、`rd_wait.lock`、`inode->i_lock`、`ctx->wqh.lock`）；哪些讀取刻意不拿鎖、為什麼安全；wait queue 的睡與醒配對 | Ch 21、30、33 |
| 7 | git 考古 | 至少兩段程式碼的歷史，每段要有 commit hash、版本（`git describe --contains`）、修了什麼、為什麼現在長這樣 | Ch 37、38、57 |
| 8 | 驗證實驗 | user-space 程式驗證：(a) pipe 預設容量與 `F_GETPIPE_SZ`/`F_SETPIPE_SZ` 的捨入與限制；(b) `O_NONBLOCK` 下滿 pipe 的 `EAGAIN`；(c) `EFD_SEMAPHORE` 語意。附原始碼、編譯指令、真實輸出，並把每個輸出對回 file:line | Ch 3、40、51 |
| 9 | 自評 | 用本文的 rubric 給自己打分，說明扣分原因 | Ch 58 |
| 10 | 給下一個讀者的建議 | 推薦的閱讀順序、最容易誤解的三個地方 | Ch 7 |

**限制**：

- 所有引用的程式碼必須從 v6.18 tree 複製，標 `檔案:行號（v6.18）`。不准憑記憶寫——pipe.c 在 v6.14–v6.15 之間大改過，記憶中的 `pipe_read()` 在 v6.18 已經不存在了（見考古一節）。
- LLM 可以用，但它給的每個 file:line 都要自己驗證（[Ch 53](./53-llm-assisted-reading.md)）。
- 篇幅建議 400–800 行 Markdown；圖用 ASCII。

## 期望輸出範例（驗證實驗的驗收基準）

你的實驗程式要能產生**同樣語意**的輸出（格式可以不同）。下面三組是參考實作在本環境的真實輸出，原始碼在後面的參考解答裡。

**實驗 A：pipe 容量與 F_SETPIPE_SZ**

```
$ gcc -Wall -O2 -o pipecap pipecap.c && ./pipecap
[1] default F_GETPIPE_SZ = 65536
[1] 1-byte writes   -> 65536 bytes before EAGAIN
[1] 4096-byte writes -> 65536 bytes before EAGAIN
[1] one more write(1): ret=-1 errno=Resource temporarily unavailable
[2] F_SETPIPE_SZ(      1) ->    4096
[2] F_SETPIPE_SZ(   4096) ->    4096
[2] F_SETPIPE_SZ(   5000) ->    8192
[2] F_SETPIPE_SZ(  65536) ->   65536
[2] F_SETPIPE_SZ( 100000) ->  131072
[2] F_SETPIPE_SZ(1048576) -> 1048576
[2] F_SETPIPE_SZ(1048577) -> -1 errno=Operation not permitted
[3] F_SETPIPE_SZ(4096) with 3 slots used: ret=-1 errno=Device or resource busy
[4] 1-slot pipe holding 1 byte, write(4096): ret=-1 errno=Resource temporarily unavailable
[4] same pipe, write(100): ret=100
[4] same pipe, write(4097): ret=1
$ cat /proc/sys/fs/pipe-max-size /proc/sys/fs/pipe-user-pages-soft
1048576
16384
```

**實驗 B：滿 pipe 上阻塞的 writer 何時被叫醒**

```
$ gcc -Wall -O2 -pthread -o pipewake pipewake.c && ./pipewake
pipe size 8192, filled with 2 full pages
step 0: blocked?           writer_done=0  writer wchan=anon_pipe_write
step 1: after read(1)      writer_done=0  writer wchan=anon_pipe_write
  [writer] write(4096) returned 4096
step 2: after read(4095)   writer_done=1  writer wchan=?
```

**實驗 C：eventfd 計數器語意**

```
$ gcc -Wall -O2 -o efd efd.c && ./efd
[A] normal mode, initval=0
                         write(3) -> 8
                         write(4) -> 8
    eventfd-count:                7
    eventfd-id: 74
    eventfd-semaphore: 0
  1st                    read -> 8 bytes, value=7
  2nd (count==0)         read -> -1 errno=Resource temporarily unavailable
[B] EFD_SEMAPHORE, initval=0
                         write(3) -> 8
    eventfd-count:                3
    eventfd-id: 75
    eventfd-semaphore: 1
  1st                    read -> 8 bytes, value=1
  2nd                    read -> 8 bytes, value=1
  3rd                    read -> 8 bytes, value=1
  4th (count==0)         read -> -1 errno=Resource temporarily unavailable
[C] edge cases
                         write 4 bytes -> -1 errno=Invalid argument
                         read 4 bytes  -> -1 errno=Invalid argument
  ULLONG_MAX             write(18446744073709551615) -> -1 errno=Invalid argument
  ULLONG_MAX-1           write(18446744073709551614) -> 8
    eventfd-count: fffffffffffffffe
    eventfd-id: 76
    eventfd-semaphore: 0
  now full               write(1) -> -1 errno=Resource temporarily unavailable
  drain                  read -> 8 bytes, value=18446744073709551614
  zero                   write(0) -> 8
  after write(0)         read -> -1 errno=Resource temporarily unavailable
```

（`eventfd-id` 的數字取決於系統上已經開過多少 eventfd，每次執行會不同。）

報告裡要解釋的不是「輸出是什麼」，而是**每一行輸出由哪幾行 kernel 程式碼決定**。例如實驗 A 的 `[4] write(4096)` 為什麼是 `EAGAIN`，而 `write(100)` 卻成功？實驗 B 的 step 1 為什麼 writer 還在睡？實驗 C 的 `write(0)` 為什麼回傳 8 卻沒有東西可讀？

## 概念覆蓋表

專案整合的核心概念（本課共 9 個 Part，以下標出每個 Part 在本專案用到的章節）：

| Part | 用到的章節 | 在本專案的出現位置 |
|---|---|---|
| 1 心法 | Ch 0、1、2、3、4、5、7 | 環境與語料（Ch 0）；第 1、2、10 節；整份報告的筆記與圖 |
| 2 C 語言層 | Ch 8、9、10、11、12、13 | `goto out` 收尾、`total_len & (PAGE_SIZE-1)`、`union pipe_index`、page 的所有權 |
| 3 kernel C 方言 | Ch 15、16、17、18、19、20、21、22 | `container_of`（`eventfd_free`）、`ERR_PTR`、`__user`、`GFP_KERNEL_ACCOUNT`、`kref`、`READ_ONCE`/`smp_load_acquire`、`SYSCALL_DEFINE`/`EXPORT_SYMBOL_GPL`/`DEFINE_IDA` |
| 4 資料結構 | Ch 23、27 | wait queue 底下是 `list_head`；pipe ring 是自製的 power-of-2 環形緩衝區，和 kfifo 同一家族 |
| 5 控制流與間接 | Ch 28、29、30、33、34 | 兩層 ops table、`SYSCALL_DEFINE2`、mutex 與 spinlock、wait queue、eventfd 作為 kernel 子系統的通知 callback |
| 6 脈絡 | Ch 35、36、37、38、39、40 | `fs/` 與 `include/linux/` 的分工、`CONFIG_WATCH_QUEUE` 的 `#ifdef`、考古、`Link:`/`Closes:`、struct 的 kernel-doc、實驗當規格 |
| 7 架構 | Ch 41、42、45、46 | pipefs 偽檔案系統與 VFS、struct 當 vtable、observer pattern（eventfd）、用 man page 與 TLPI 補領域 |
| 8 工具 | Ch 47、48、49、50、51、53 | `rg` 找賦值點、elixir/clangd 跳定義、`/proc/*/wchan` 當廉價的動態觀察、驗證 LLM 給的行號 |
| 9 冷讀 | Ch 54、57、58 | 初始診斷、讀 race fix 的 patch、檢查清單 |

59 章（Ch 0–58）中用到 47 章，約 80%；另外練習 D（資料結構地圖）、E（追 syscall）、F（功能考古）的產出格式直接沿用。

## 如果你卡住了

1. **讀不懂 `head` 和 `tail` 為什麼不取模？** 先別讀 pipe.c，去讀 `include/linux/pipe_fs_i.h` 的 `pipe_occupancy()`、`pipe_buf()`，再讀 pipe.c 開頭 David Howells 2019 年的註解。關鍵字：「allowed to wrap naturally」。
2. **追到 `buf->ops->release` 就斷了？** 這是第二層 ops table。用 `rg -n 'pipe_buf_operations .*= \{' fs/ mm/ kernel/` 找所有實作，再問：anonymous pipe 寫入時 `buf->ops` 被設成哪一個？（答案在 `anon_pipe_write` 裡一行賦值。）
3. **不懂為什麼 writer 睡醒後要重新 `mutex_lock` 再檢查？** 這是 wait queue 的標準 pattern，回去看 [Ch 33 — 等待與喚醒](./33-wait-queues-completion.md)。睡醒只代表「條件**可能**成立了」。
4. **找不到 FIFO（`mkfifo` 建立的具名 pipe）從哪裡進來？** 它不是從 syscall 進來的，是 `open()` 一個 S_IFIFO inode 時，VFS 依 inode 型別設定 `i_fop`。用 `rg -n 'pipefifo_fops' --type c .` 找。
5. **eventfd 的 `in_eventfd` 看不懂？** 它防的是遞迴：`eventfd_signal()` 叫醒的 wait queue callback 可能又去 signal 同一個或另一個 eventfd。先讀 `eventfd_signal_mask()` 的註解，再讀考古那一節。

## 實作步驟建議

### Step 1：冷讀與診斷（第 1 天，2 小時）

不看任何資料，打開 `fs/pipe.c`，限時 30 分鐘。每次看不懂就在筆記記一行：`file:line — 哪道鴻溝 — 我的猜測`。時間到就停。這份原始紀錄就是報告第 1 節的素材，**不要事後美化它**。然後用剩下的時間寫第 2 節的問題清單。

### Step 2：資料結構（第 2 天）

只讀 `include/linux/pipe_fs_i.h`（348 行）與 `fs/eventfd.c:30–44`。畫出 `pipe_inode_info` → `bufs[]` → `pipe_buffer` → `page` / `ops` 的指向圖，標出每個欄位由哪把鎖保護（struct 的 kernel-doc 註解有寫一部分）。

### Step 3：入口點（第 3 天）

列出兩張 `file_operations` 的每個欄位對應到哪個函式；從 `SYSCALL_DEFINE2(pipe2, ...)` 一路追到 `fd_install()`；找出 `eventfd_signal()` 的使用者。

### Step 4：關鍵路徑（第 4–6 天）

`anon_pipe_write` → `anon_pipe_read` → `eventfd_write` / `eventfd_read`。每個函式畫一張流程圖，標出所有 `goto`/`break`/`return` 出口與每個出口的回傳值。

### Step 5：並行與考古（第 7–8 天）

對每個 `READ_ONCE`、`smp_load_acquire`、`spin_lock_irq(&pipe->rd_wait.lock)` 跑 `git blame`，找到引入它的 commit，讀 message。至少完整考古兩段。

### Step 6：實驗與收尾（第 9–10 天）

寫實驗程式，把每一行輸出對回程式碼。如果輸出和你的預期不同，**那是報告最有價值的段落**——寫下你原本怎麼想、錯在哪裡。

## 完整參考解答

**寫完再看！** 下面是參考報告的骨架與關鍵答案，所有 file:line 都在 v6.18 tree 上確認過。

<details>
<summary>點開參考報告（第 1–7 節關鍵答案）</summary>

### 1. 初始診斷（參考作者的紀錄節錄）

| 鴻溝 | 卡點 | file:line |
|---|---|---|
| 慣用法 | `union pipe_index idx = { .head_tail = READ_ONCE(pipe->head_tail) };`——為什麼要 union？ | `fs/pipe.c:232` |
| 慣用法 | `chars = total_len & (PAGE_SIZE-1);` 在算什麼？ | `fs/pipe.c:477` |
| 間接 | `pipe_buf_release(pipe, buf)` 最後呼叫誰？ | `fs/pipe.c:242` |
| 領域 | `copy_page_from_iter`、`iov_iter` 是什麼？ | `fs/pipe.c:488` |
| 脈絡 | 為什麼 `tmp_page` 是大小 2 的陣列？ | `include/linux/pipe_fs_i.h:111` |
| 目標 | 開頭 `pipe_double_lock` 是給誰用的？（和我的問題無關，應該跳過） | `fs/pipe.c:102` |

### 2. 問題清單（節錄）

1. pipe 預設能裝多少 bytes？由哪個常數決定？→ `PIPE_DEF_BUFFERS 16`（`include/linux/pipe_fs_i.h:5`）× `PAGE_SIZE`
2. writer 滿了睡在哪個 wait queue？誰叫醒它？
3. 為什麼讀 1 byte 不會叫醒 writer？
4. 匿名 pipe 和 FIFO 的程式碼差在哪？
5. `F_SETPIPE_SZ` 為什麼只能是 2 的冪頁？
6. eventfd 的 `EFD_SEMAPHORE` 讀出來永遠是 1，計數器怎麼減？
7. eventfd 計數器為什麼不能到 `ULLONG_MAX`？
8. （未解）`eventfd_write` 為什麼是 `.write` 而不是 `.write_iter`？它為什麼不檢查 `IOCB_NOWAIT`？

### 3. 資料結構地圖

```
 struct file (讀端)  struct file (寫端)
   private_data ──┐    private_data ──┐
                  ▼                   ▼
           struct pipe_inode_info   (include/linux/pipe_fs_i.h:86)
           ├─ mutex                  保護幾乎全部欄位
           ├─ rd_wait, wr_wait       wait_queue_head_t（各自內含 spinlock）
           ├─ union { head_tail; struct { head; tail; } }   ← :53 的同形 union
           ├─ max_usage, ring_size   ring_size 必為 2 的冪；max_usage ≤ ring_size
           ├─ readers, writers       開著的讀/寫端數；歸零觸發 EOF / SIGPIPE
           ├─ files                  受 inode->i_lock 保護（kernel-doc 有註明）
           ├─ tmp_page[2]            回收的空 page，下次寫入直接重用
           └─ bufs ──► [pipe_buffer][pipe_buffer]...[pipe_buffer]   (ring_size 個)
                         │ (:26)
                         ├─ page ──► struct page（資料本體，一個 slot 最多一頁）
                         ├─ offset, len
                         ├─ flags   PIPE_BUF_FLAG_CAN_MERGE / PACKET / ...
                         └─ ops ──► struct pipe_buf_operations (:131)
                                    { confirm, release, try_steal, get }
                                    匿名 pipe 寫入：anon_pipe_buf_ops (fs/pipe.c:223)

 slot 位置 = bufs[index & (ring_size - 1)]          pipe_buf()   pipe_fs_i.h
 已用 slot = (pipe_index_t)(head - tail)            pipe_occupancy() :184
 滿        = occupancy >= max_usage                 pipe_full()      :205
 head、tail 只增不減、自然溢位；只在取用時 mask

 struct eventfd_ctx (fs/eventfd.c:30)
 ├─ kref       file 與 kernel 使用者（KVM irqfd 等）共享生命週期
 ├─ wqh        wait_queue_head_t；wqh.lock 同時保護 count
 ├─ count      __u64 計數器，合法範圍 0 .. ULLONG_MAX-1
 ├─ flags      建立時的 EFD_* flags
 └─ id         ida 配的編號，給 fdinfo 用
```

重點觀察：

- 容量的單位是 **slot（頁）**，不是 byte。實驗 A 的 `[4]`：1-slot pipe 裡有 1 byte，再寫 4096 bytes 會 `EAGAIN`，因為 `chars = 4096 & 4095 = 0`，不走 merge 分支（`fs/pipe.c:477–498`），而唯一的 slot 已被佔用，`pipe_full()` 成立；寫 100 bytes 時 `chars = 100`，merge 進現有那頁（`offset + chars <= PAGE_SIZE`）所以成功；寫 4097 時先 merge 1 byte，剩下 4096 遇到滿 pipe、`ret` 已非零，回傳部分寫入 1。
- 1-byte 寫 16 次和 4096-byte 寫 16 次都裝 65536：小寫入會 merge 進同一頁，所以裝滿的條件仍然是 16 個滿頁。

### 4. 入口點清單

| 入口 | 位置 | 落到 |
|---|---|---|
| `pipe2(fds, flags)` | `fs/pipe.c:1054` `SYSCALL_DEFINE2(pipe2, ...)` | `do_pipe2`（:1032）→ `__do_pipe_flags`（:982，檢查 flags 白名單）→ `create_pipe_files`（:926）→ `get_pipe_inode`（:886）→ `alloc_pipe_info`（:792） |
| `pipe(fds)` | `fs/pipe.c:1059` | `do_pipe2(fildes, 0)` |
| 匿名 pipe 的 fops | `pipeanon_fops`，`fs/pipe.c:1257`（static） | `.read_iter = anon_pipe_read`（:269）、`.write_iter = anon_pipe_write`（:431）、`.poll = pipe_poll`（:660）、`.unlocked_ioctl = pipe_ioctl`（:619，FIONREAD）、`.release = pipe_release`（:725）、`.fasync = pipe_fasync`（:749）、`.open = fifo_open`、`.splice_write = iter_file_splice_write` |
| FIFO 的 fops | `pipefifo_fops`，`fs/pipe.c:1246` | 同上，但 `.read_iter = fifo_pipe_read`（:407）、`.write_iter = fifo_pipe_write`（:604），兩者包一層 anon 版本並更新時間戳 |
| FIFO 怎麼接上 | `fs/inode.c:2586` `inode->i_fop = &pipefifo_fops;` | `open()` 一個 S_IFIFO inode → `fifo_open`（:1121） |
| `F_SETPIPE_SZ` / `F_GETPIPE_SZ` | `fs/fcntl.c:539–541` | `pipe_fcntl`（:1428）→ `pipe_set_size`（:1364）→ `round_pipe_size`（:1272）+ `pipe_resize_ring`（:1291） |
| `eventfd2(count, flags)` | `fs/eventfd.c:423` | `do_eventfd`（:379）→ `anon_inode_getfile_fmode("[eventfd]", &eventfd_fops, ...)` |
| `eventfd(count)` | `fs/eventfd.c:428` | `do_eventfd(count, 0)` |
| eventfd 的 fops | `eventfd_fops`，`fs/eventfd.c:302` | `.read_iter = eventfd_read`（:214）、`.write = eventfd_write`（:247）、`.poll = eventfd_poll`（:118）、`.release = eventfd_release`（:109）、`.show_fdinfo` |
| kernel 內 API | `eventfd_signal()` 是 `include/linux/eventfd.h:87` 的 inline，呼叫 `eventfd_signal_mask(ctx, 0)`（`fs/eventfd.c:56`） | 使用者見下 |

**v6.18 有兩張 pipe fops**：題目要求的「`pipefifo_fops` 各回呼」只涵蓋 FIFO；匿名 pipe 用的是 `pipeanon_fops`。這是 v6.15 的 `f017b0a4951f` 拆出來的（見考古）。`get_pipe_info()`（:1415）同時比對兩張表來判斷「這是不是 pipe」。

`eventfd_signal` / `eventfd_signal_mask` 的使用者：`rg -l '\beventfd_signal(_mask)?\(' --type c .` 扣掉 `fs/eventfd.c` 共 33 個檔案。代表性的：

| 子系統 | 位置 | 用途 |
|---|---|---|
| KVM | `virt/kvm/eventfd.c:64` | irqfd resample：通知 user space 中斷可以重新觸發 |
| io_uring | `io_uring/eventfd.c:46` | CQ 有新完成事件時通知；用 `_mask` 帶 `EPOLL_URING_WAKE` |
| AIO | `fs/aio.c:1182` | `IOCB_FLAG_RESFD`：AIO 完成時 signal 使用者給的 eventfd |
| cgroup v1 memcg | `mm/memcontrol-v1.c:461` | 記憶體用量跨過 threshold 時通知 |
| vhost | `drivers/vhost/vhost.c:3175` | virtqueue 有新資料時通知 guest（`call_ctx`） |
| VFIO | `drivers/vfio/` 下 5 個檔案 | 裝置中斷轉成 eventfd 交給 user space |

### 5. 關鍵路徑

**pipe write（`anon_pipe_write`，`fs/pipe.c:431–601`）**

```
anon_pipe_write
├─ watch queue pipe? → -EXDEV                                   :451
├─ total_len == 0 → return 0                                    :455
├─ mutex_lock(&pipe->mutex)                                     :458
├─ readers == 0 → SIGPIPE + -EPIPE, goto out                    :460–465
├─ merge：chars = total_len % PAGE_SIZE；若非空且最後一個 buf
│         有 CAN_MERGE 且放得下 → 複製進同一頁                    :476–498
└─ for (;;)                                                     :500
    ├─ readers == 0 → SIGPIPE, -EPIPE（若尚未寫入）              :501–507
    ├─ !pipe_full → anon_pipe_get_page（先用 tmp_page）
    │      copy_page_from_iter 一整頁，head+1，填 buf 欄位       :510–546
    │      寫完 → break；還有 → continue
    ├─ 滿 + (O_NONBLOCK || IOCB_NOWAIT) → -EAGAIN（若尚未寫入）   :551–556
    ├─ signal_pending → -ERESTARTSYS（若尚未寫入）                :557–561
    ├─ mutex_unlock                                             :569
    ├─ was_empty → 叫醒 reader（rd_wait）                        :570–571
    ├─ wait_event_interruptible_exclusive(wr_wait, pipe_writable) :573
    ├─ mutex_lock，重算 was_empty，wake_next_writer = true        :574–576
    └─ 回到迴圈頂端重新檢查一切
out:                                                            :578
├─ 滿了就不叫醒下一個 writer                                     :579–580
├─ mutex_unlock                                                 :581
├─ was_empty || poll_usage → 叫醒 reader                         :595–596
└─ wake_next_writer → 叫醒下一個 writer                          :598–599
```

要寫進報告的觀察：

- `wait_event_interruptible_exclusive` 的回傳值被**忽略**（:573）；被 signal 打斷的處理在下一輪迴圈的 :557。
- `exclusive` 代表一次只叫醒一個 writer；被叫醒的 writer 若寫完後 pipe 還沒滿，要負責接力叫醒下一個（`wake_next_writer`），否則其他 writer 會睡到天荒地老。這就是 pipe.c:1064–1071 那段註解說的「proper kind of exclusive wait」。
- 叫醒別人都在**放鎖之後**做（:595–599），避免被叫醒的人一醒來就撞上 mutex。

**pipe read（`anon_pipe_read`，`fs/pipe.c:269–404`）**

- :294 用 `smp_load_acquire(&pipe->head)` 讀 head，配合 watch queue 在 spinlock 下 post 通知的路徑。
- :359–362：一個 buf 讀完（`!buf->len`）才 `pipe_update_tail`，並且**只有讀之前 pipe 是滿的**才設 `wake_writer`（:360）。這就是實驗 B 的答案：step 1 只讀 1 byte，tail 的 buf 還剩 4095 bytes，slot 沒釋放，writer 不會被叫醒；step 2 讀完那 4095 bytes，slot 釋放，`wake_writer` 為真，:399 叫醒 writer。`wchan=anon_pipe_write` 證實 writer 睡在 `anon_pipe_write` 裡。
- :370–378：沒有 writer → 回 0（EOF）；已讀到東西 → 回傳；非阻塞 → `-EAGAIN`。
- :388：等待時被 signal 打斷直接 `return -ERESTARTSYS`，因為還沒讀到任何東西，不需要做任何喚醒。

**F_SETPIPE_SZ（實驗 A 的 [2][3]）**

- `round_pipe_size`（:1272–1282）：小於一頁進位到一頁、其餘 `roundup_pow_of_two`，所以 5000→8192、100000→131072。
- 1048577 進位成 2 MiB，大於 `pipe_max_size`（:55，預設 1048576，可由 `/proc/sys/fs/pipe-max-size` 調）且非 `CAP_SYS_RESOURCE` → `-EPERM`（:1386–1388）。
- `pipe_resize_ring`（:1291–1358）在 :1311–1315 檢查 `nr_slots < n`（現在佔用的 slot 數）→ `-EBUSY`，就是實驗 A 的 [3]。

**eventfd 計數器語意**

| 操作 | 程式碼 | 語意 |
|---|---|---|
| `write(v)` | `eventfd_write`，:247–280 | 長度必須正好 8（:254）；`v == ULLONG_MAX` → `-EINVAL`（:258）；若 `count + v` 會到達或超過 `ULLONG_MAX` → 非阻塞 `-EAGAIN`、阻塞則等（:262–269）；成功時 `count += v` 並叫醒 reader（:271–274） |
| `read()` 一般模式 | `eventfd_ctx_do_read`，:180 | 回傳整個 `count`，`count` 歸零 |
| `read()` 加 `EFD_SEMAPHORE` | 同上 | `count` 非零時回傳 1、`count -= 1` |
| `read()` 在 `count == 0` | `eventfd_read`，:223–234 | 非阻塞 `-EAGAIN`；阻塞則睡到 `count` 非零 |
| `eventfd_signal()` | `eventfd_signal_mask`，:56–79 | `count < ULLONG_MAX` 時加 1；不會阻塞（可在不能睡的情境呼叫） |
| `poll` | `eventfd_poll`，:118–174 | `count > 0` → EPOLLIN；`count == ULLONG_MAX` → EPOLLERR（只有 signal 端能把它推到這裡）；`count < ULLONG_MAX-1` → EPOLLOUT |

實驗 C 的對照：`write(0)` 通過所有檢查、回傳 8，`count += 0`，還會叫醒等待者（:273–274），但 `count` 仍是 0，所以接下來的非阻塞 read 是 `EAGAIN`。這是「被叫醒不代表條件成立」的活例子。

**splice（選讀）**：`splice_to_pipe`（`fs/splice.c:197`）、`add_to_pipe`（:245）把**別人的 page** 放進 pipe ring，`buf->ops` 不再是 `anon_pipe_buf_ops`；這正是 `pipe_buf_operations` 存在的理由——ring 裡的每一頁可能有不同的主人與釋放方式。

### 6. 並行

| 鎖 | 保護什麼 | 證據 |
|---|---|---|
| `pipe->mutex` | head/tail 的**修改**、bufs 內容、readers/writers、max_usage | kernel-doc「mutex protecting the whole thing」，`pipe_fs_i.h:63` |
| `pipe->rd_wait.lock`（wait queue 內建 spinlock） | 除了保護 wait queue 本身，還在兩處當資料鎖：watch queue pipe 的 tail 更新（`fs/pipe.c:250`）、`pipe_resize_ring` 換 bufs 陣列（:1305） | 因為 watch queue 的通知是在只拿這把 spinlock、不拿 mutex 的情況下 post 的（:244–247 註解） |
| `inode->i_lock` | `pipe->files` 與 `inode->i_pipe` | `put_pipe_info`（:713）、`fifo_open`（:1129、:1140） |
| `ctx->wqh.lock` | eventfd 的 `count` 與 wait queue | 所有寫 `count` 的地方都在它之下；`eventfd_ctx_do_read` 用 `lockdep_assert_held` 宣告（:178） |
| 鎖順序 | `pipe_double_lock`（:102–112）按**位址**排序再上鎖 | 給 splice 這類同時鎖兩個 pipe 的路徑，避免 ABBA deadlock；lockdep 的比較函式在 :81–86 |

**刻意不拿鎖的讀取**：

- `pipe_readable()`（:230）、`pipe_writable()`（:421）、`pipe_poll()`（:660）在**沒拿 mutex** 的情況下讀 head/tail，因為它們是 wait_event 的條件或 poll 的狀態查詢。安全的前提是：用一次 `READ_ONCE(pipe->head_tail)` 同時取得 head 與 tail（見考古一），所以看到的一對值一定是某個時刻的真實狀態。
- `pipe_poll` 的順序（:675–685）：**先** `poll_wait` 把自己掛上 wait queue，**再**讀狀態。若反過來，狀態在讀完之後、掛上之前改變，就會錯過喚醒。`eventfd_poll` 在 :126–163 用一張時序圖把同一個道理寫得更清楚，值得整段抄進報告。

**睡與醒的配對表**：

| 誰睡 | 睡在 | 條件 | 誰叫醒 |
|---|---|---|---|
| pipe writer | `wr_wait`（exclusive） | `pipe_writable()` | reader 讀空一個 slot 且之前是滿的（:399）；前一個 writer 接力（:599）；`pipe_release` 最後一個 reader 離開（:737–738）；`pipe_resize_ring` 擴容（:1356） |
| pipe reader | `rd_wait`（exclusive） | `pipe_readable()` | writer 寫入且之前是空的或有 epoll（:571、:596）；前一個 reader 接力（:401）；最後一個 writer 離開 |
| FIFO open | `rd_wait` | 另一端出現（`r_counter`/`w_counter` 改變） | `wake_up_partner`（:1116） |
| eventfd reader | `wqh` | `ctx->count != 0` | `eventfd_write`、`eventfd_signal_mask` |
| eventfd writer | `wqh` | `ULLONG_MAX - count > ucnt` | `eventfd_read`（:237–238）、`eventfd_ctx_remove_wait_queue` |

**`in_eventfd`**（`include/linux/sched.h:1040` 的一個 bit）：`eventfd_signal_mask` 進入時若已經在 signal 中就 `WARN_ON_ONCE` 並返回（`fs/eventfd.c:68–69`），防止 wait queue callback 遞迴 signal 造成 deadlock 或 stack overflow。呼叫者可用 `eventfd_signal_allowed()`（`include/linux/eventfd.h:43`）事先檢查。

### 7. git 考古

**考古一：`union pipe_index` 與 `head_tail`——一條 race 的三個 commit**

```
8cefc107ca54  v5.5-rc1   David Howells   pipe: Use head and tail pointers for the ring, not cursor and length
aaec5a95d596  v6.14-rc1  Oleg Nesterov   pipe_read: don't wake up the writer if the pipe is still full
3d252160b818  v6.14-rc6  Linus Torvalds  fs/pipe: Read pipe->{head,tail} atomically outside pipe->mutex
```

- `8cefc107ca54` 把 ring 從「游標 + 長度」改成「只增不減的 head/tail」，這就是 pipe.c 開頭那段 2019 年註解的來源。
- `aaec5a95d596` 是一個最佳化：把「讀之前是否滿」的 `was_full` 改成「讀完一個 slot 時才判斷」的 `wake_writer |= pipe_full(...)`（現在的 :360）。它減少了多餘的喚醒。
- 但多餘的喚醒原本在**掩蓋**一個 race：lockless 的 `pipe_writable()` 分兩次讀 head 和 tail，中間被打斷時可能讀到「舊 head、新 tail」，`head - tail` 在無號運算下溢位成巨大值，`pipe_full()` 對一個空 pipe 回傳 true，writer 和 reader 互等。`3d252160b818` 的 message 附了 hackbench 的 trace，並明說這個問題「became more problematic ... after the optimization in commit aaec5a95d596」。修法是把 head、tail 包進一個 `unsigned long` 的 union，一次 `READ_ONCE` 讀完。這也解釋了 `pipe_index_t` 在 32 位元平台是 `unsigned short`（`pipe_fs_i.h:41–45`）：兩個 index 合起來要塞進一個 `unsigned long`。

教訓：**一個正確的最佳化可以讓一個潛伏的 bug 浮出水面**。只看 `aaec5a95d596` 的 diff 找不到 bug，bug 在 diff 外的 `pipe_writable()`（[Ch 57](./57-reading-patches-and-review.md) 的核心論點）。

**考古二：`eventfd_ctx_do_read` 那個 `&& ctx->count`**

`fs/eventfd.c:180`：

```c
	*cnt = ((ctx->flags & EFD_SEMAPHORE) && ctx->count) ? 1 : ctx->count;
```

為什麼要 `&& ctx->count`？`git log -L180,180:fs/eventfd.c` 或直接讀 `758b49204781 eventfd: prevent underflow for eventfd semaphores`（v6.6-rc1）：在這之前是 `(ctx->flags & EFD_SEMAPHORE) ? 1 : ctx->count`，semaphore 模式下 `count == 0` 時會回傳 1 並 `count -= 1`，溢位成 `ULLONG_MAX`。user space 的 `eventfd_read` 有 `!ctx->count` 的前置檢查碰不到這條路，但 KVM irqfd 關閉時走 `eventfd_ctx_remove_wait_queue()` → `eventfd_ctx_do_read()` 不檢查 count，就會觸發。message 的 `Fixes: cb289d6244a3`（「eventfd - allow atomic read and waitqueue remove」）指向 kernel 內部 API 引入的那一刻。

教訓：**一個函式被 export 給 kernel 其他子系統之後，它的前置條件就不能再依賴 syscall 路徑的檢查**。

**考古三（選讀）**

- `f017b0a4951f`（v6.15-rc1）「pipe: don't update {a,c,m}time for anonymous pipes」：把 `pipe_read`/`pipe_write` 改名為 `anon_pipe_*`，另外包出 `fifo_pipe_*` 並拆成兩張 fops。這就是為什麼記憶中的 `pipe_read()` 在 v6.18 找不到。見延伸挑戰的實驗。
- `46af8e2406c2`（v6.15-rc1）「pipe: cache 2 pages instead of 1」：`tmp_page` 從單一指標變成大小 2 的陣列。
- Dirty Pipe：`f6dd975583bd`（v5.8-rc1）引入 `PIPE_BUF_FLAG_CAN_MERGE`；`9d2231c5d74e`（v5.17-rc6）修正 `lib/iov_iter.c` 新建 pipe_buffer 時沒初始化 `flags`，讓舊的 CAN_MERGE 殘留在 page cache 的頁上。今天的 :476–498 merge 分支就是當年被利用的那段邏輯。

</details>

<details>
<summary>點開實驗原始碼（pipecap.c、pipewake.c、efd.c）</summary>

**pipecap.c**（實驗 A）

```c
/* pipecap.c — 驗證 pipe 容量、F_SETPIPE_SZ 的捨入、O_NONBLOCK 下的 EAGAIN */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CHECK(x) do { if ((x) < 0) { perror(#x); exit(1); } } while (0)

static char buf[3 * 4096];

/* O_NONBLOCK 下每次寫 chunk bytes，直到 EAGAIN；回傳總共寫進去多少 */
static long fill(int wfd, size_t chunk)
{
    long n = 0;
    for (;;) {
        ssize_t r = write(wfd, buf, chunk);
        if (r > 0) { n += r; continue; }
        if (r < 0 && errno == EAGAIN) return n;
        perror("write"); exit(1);
    }
}

static void report(const char *what, ssize_t r)
{
    printf("%s: ret=%zd%s%s\n", what, r,
           r < 0 ? " errno=" : "", r < 0 ? strerror(errno) : "");
}

int main(void)
{
    int p[2];

    /* [1] 預設容量，兩種寫法填滿 */
    CHECK(pipe2(p, O_NONBLOCK));
    printf("[1] default F_GETPIPE_SZ = %d\n", fcntl(p[1], F_GETPIPE_SZ));
    printf("[1] 1-byte writes   -> %ld bytes before EAGAIN\n", fill(p[1], 1));
    close(p[0]); close(p[1]);
    CHECK(pipe2(p, O_NONBLOCK));
    printf("[1] 4096-byte writes -> %ld bytes before EAGAIN\n", fill(p[1], 4096));
    report("[1] one more write(1)", write(p[1], buf, 1));
    close(p[0]); close(p[1]);

    /* [2] F_SETPIPE_SZ 的捨入 */
    int asks[] = { 1, 4096, 5000, 65536, 100000, 1048576, 1048577 };
    for (unsigned i = 0; i < sizeof(asks) / sizeof(asks[0]); i++) {
        CHECK(pipe2(p, O_NONBLOCK));
        int got = fcntl(p[1], F_SETPIPE_SZ, asks[i]);
        if (got < 0)
            printf("[2] F_SETPIPE_SZ(%7d) -> -1 errno=%s\n", asks[i], strerror(errno));
        else
            printf("[2] F_SETPIPE_SZ(%7d) -> %7d\n", asks[i], got);
        close(p[0]); close(p[1]);
    }

    /* [3] 佔用 3 個 slot 時縮到 1 個 slot */
    CHECK(pipe2(p, O_NONBLOCK));
    CHECK(write(p[1], buf, 3 * 4096));
    report("[3] F_SETPIPE_SZ(4096) with 3 slots used", fcntl(p[1], F_SETPIPE_SZ, 4096));
    close(p[0]); close(p[1]);

    /* [4] 1-slot pipe：merge 規則 */
    CHECK(pipe2(p, O_NONBLOCK));
    CHECK(fcntl(p[1], F_SETPIPE_SZ, 4096));
    CHECK(write(p[1], "a", 1));
    report("[4] 1-slot pipe holding 1 byte, write(4096)", write(p[1], buf, 4096));
    report("[4] same pipe, write(100)", write(p[1], buf, 100));
    report("[4] same pipe, write(4097)", write(p[1], buf, 4097));
    close(p[0]); close(p[1]);
    return 0;
}
```

**pipewake.c**（實驗 B）

```c
/* pipewake.c — 滿 pipe 上阻塞的 writer 什麼時候被叫醒？ */
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

static int p[2];
static atomic_int writer_done, writer_tid;
static char page[4096];

static void *writer(void *arg)
{
    (void)arg;
    atomic_store(&writer_tid, gettid());
    ssize_t r = write(p[1], page, 4096);   /* pipe 已滿，這裡會睡在 wr_wait */
    atomic_store(&writer_done, 1);
    printf("  [writer] write(4096) returned %zd\n", r);
    return NULL;
}

static void wait_ms(int ms)
{
    struct timespec ts = { 0, ms * 1000000L };
    nanosleep(&ts, NULL);
}

/* /proc/<pid>/task/<tid>/wchan：這條 thread 睡在哪個 kernel 函式 */
static void show(const char *step)
{
    char path[64], wc[64] = "?";
    snprintf(path, sizeof(path), "/proc/self/task/%d/wchan", atomic_load(&writer_tid));
    FILE *f = fopen(path, "r");
    if (f) { if (!fgets(wc, sizeof(wc), f)) wc[0] = 0; fclose(f); }
    printf("%-26s writer_done=%d  writer wchan=%s\n", step, atomic_load(&writer_done), wc);
}

int main(void)
{
    char tmp[4096];
    if (pipe(p) < 0) { perror("pipe"); return 1; }
    fcntl(p[1], F_SETPIPE_SZ, 2 * 4096);           /* 2 個 slot 比較好觀察 */
    for (int i = 0; i < 2; i++)
        if (write(p[1], page, 4096) != 4096) { perror("fill"); return 1; }
    printf("pipe size %d, filled with 2 full pages\n", fcntl(p[1], F_GETPIPE_SZ));

    pthread_t t;
    pthread_create(&t, NULL, writer, NULL);
    wait_ms(200);
    show("step 0: blocked?");

    if (read(p[0], tmp, 1) != 1) return 1;        /* tail slot 還剩 4095 bytes */
    wait_ms(200);
    show("step 1: after read(1)");

    if (read(p[0], tmp, 4095) != 4095) return 1;  /* tail slot 清空 */
    wait_ms(200);
    show("step 2: after read(4095)");
    pthread_join(t, NULL);
    return 0;
}
```

`gettid()` 需要 glibc 2.30 以上（Ubuntu 22.04 是 2.35）。200 ms 的等待是經驗值，讓 writer thread 有時間進入睡眠或被排程；在負載很重的機器上可能要加大。step 2 的 `wchan=?` 是因為 thread 已經結束、`/proc` 項目不存在。

**efd.c**（實驗 C）

```c
/* efd.c — eventfd 計數器語意：一般模式 vs EFD_SEMAPHORE，以及邊界 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

static void rd(int fd, const char *tag)
{
    uint64_t v = 0;
    ssize_t r = read(fd, &v, sizeof(v));
    if (r < 0) printf("  %-22s read -> -1 errno=%s\n", tag, strerror(errno));
    else       printf("  %-22s read -> %zd bytes, value=%llu\n", tag, r, (unsigned long long)v);
}

static void wr(int fd, uint64_t v, const char *tag)
{
    ssize_t r = write(fd, &v, sizeof(v));
    if (r < 0) printf("  %-22s write(%llu) -> -1 errno=%s\n", tag, (unsigned long long)v, strerror(errno));
    else       printf("  %-22s write(%llu) -> %zd\n", tag, (unsigned long long)v, r);
}

static void fdinfo(int fd)
{
    char path[64], line[128];
    snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", fd);
    FILE *f = fopen(path, "r");
    while (f && fgets(line, sizeof(line), f))
        if (!strncmp(line, "eventfd", 7)) printf("    %s", line);
    if (f) fclose(f);
}

int main(void)
{
    puts("[A] normal mode, initval=0");
    int a = eventfd(0, EFD_NONBLOCK);
    wr(a, 3, "");
    wr(a, 4, "");
    fdinfo(a);
    rd(a, "1st");
    rd(a, "2nd (count==0)");

    puts("[B] EFD_SEMAPHORE, initval=0");
    int b = eventfd(0, EFD_NONBLOCK | EFD_SEMAPHORE);
    wr(b, 3, "");
    fdinfo(b);
    rd(b, "1st");
    rd(b, "2nd");
    rd(b, "3rd");
    rd(b, "4th (count==0)");

    puts("[C] edge cases");
    int c = eventfd(0, EFD_NONBLOCK);
    uint32_t small = 1;
    ssize_t r = write(c, &small, sizeof(small));
    printf("  %-22s write 4 bytes -> %zd errno=%s\n", "", r, r < 0 ? strerror(errno) : "-");
    char tiny[4];
    r = read(c, tiny, sizeof(tiny));
    printf("  %-22s read 4 bytes  -> %zd errno=%s\n", "", r, r < 0 ? strerror(errno) : "-");
    wr(c, UINT64_MAX, "ULLONG_MAX");
    wr(c, UINT64_MAX - 1, "ULLONG_MAX-1");
    fdinfo(c);
    wr(c, 1, "now full");
    rd(c, "drain");
    wr(c, 0, "zero");
    rd(c, "after write(0)");
    return 0;
}
```

`eventfd-count` 一行由 `eventfd_show_fdinfo`（`fs/eventfd.c:283–299`）以 `%16llx` 印出，所以是十六進位、寬 16。

</details>

## 評分 rubric

總分 100。每項的「優秀」標準就是參考解答的水準。

| 項目 | 配分 | 及格（60%） | 優秀（100%） |
|---|---|---|---|
| 1 初始診斷 | 8 | 五道鴻溝各一個卡點 | 卡點附 file:line 與當時的錯誤猜測，報告後段回頭標出哪些猜測被推翻 |
| 2 問題清單 | 7 | 8 個問題 | 問題具體到能被一段程式碼回答；未解問題有「下次從哪查」 |
| 3 資料結構地圖 | 15 | 四個 struct 的欄位列表 | 指向圖 + head/tail 語意 + 每個欄位由哪把鎖保護 + 容量單位是 slot 的推論 |
| 4 入口點 | 10 | syscall 與 fops 列表 | 發現兩張 pipe fops、FIFO 的 `init_special_inode` 入口、≥5 個 `eventfd_signal` 使用者並說明用途 |
| 5 關鍵路徑 | 20 | write/read 的主要流程 | 所有出口與回傳值、exclusive wait 的接力喚醒、merge 分支、eventfd 每種操作的邊界 |
| 6 並行 | 15 | 列出 mutex 與 wait queue | 每把鎖保護的不變量、lockless 讀取為何安全、睡醒配對表、鎖順序 |
| 7 git 考古 | 10 | 兩段歷史各一個 commit | 每段有完整因果鏈（引入 → 觸發 → 修正）與版本號，並抽出可遷移的教訓 |
| 8 驗證實驗 | 10 | 三個實驗有真實輸出 | 每行輸出都對回 file:line；至少一個「預期錯了」的紀錄 |
| 9–10 自評與建議 | 5 | 有打分 | 扣分理由具體；給下一個讀者的閱讀順序可以直接照著走 |

**直接扣分**：引用的程式碼和 v6.18 不符（每處 −3）；貼的實驗輸出不是真的跑出來的（該項 0 分）；把 v6.14 以前的 `pipe_read()` 當成 v6.18 的程式碼（−5）。

## 測試用例：用這些問題檢驗你的報告

| 問題 | 預期答案 | 說明 |
|---|---|---|
| 預設容量為什麼是 65536？ | `PIPE_DEF_BUFFERS`（16）× 4096 | 若 user 超過 `pipe-user-pages-soft`，新 pipe 只給 `PIPE_MIN_DEF_BUFFERS`（2）個 slot（:809–812） |
| 寫入 ≤ 4096 bytes 是原子的嗎？ | 是：不足一頁的部分要嘛 merge 進最後一頁、要嘛佔新 slot，不會拆成兩半 | POSIX 的 `PIPE_BUF` 保證；實驗 A [4] 的 `write(4096)` 寧可 `EAGAIN` 也不寫部分 |
| 寫入 4097 bytes 可能部分成功嗎？ | 可能，實驗 A [4] 回傳 1 | 超過 `PIPE_BUF` 不保證原子性 |
| 最後一個 reader 關閉時，阻塞中的 writer 會怎樣？ | `pipe_release` 叫醒所有 reader 與 writer（:737–738），writer 回到迴圈頂端看到 `readers == 0` → `SIGPIPE` + `-EPIPE` | 邊界：若已寫入部分資料，回傳已寫的 bytes 數 |
| eventfd 能寫入 `ULLONG_MAX` 嗎？ | 不能，`-EINVAL`（:258） | `ULLONG_MAX` 保留給 `eventfd_signal` 的溢位狀態，poll 回 `EPOLLERR` |
| `EFD_SEMAPHORE` 下 `count == 3`，三個 thread 同時阻塞 read，結果？ | 三個各讀到 1 | 每次 read 在 `wqh.lock` 下減 1；這個情境本文**未實測**，留作延伸挑戰 |
| 非法輸入：`pipe2(fds, O_APPEND)` | `-EINVAL` | `__do_pipe_flags` 白名單只允許 `O_CLOEXEC|O_NONBLOCK|O_DIRECT|O_NOTIFICATION_PIPE`（:987）；本文**未實測**，讀者可自行驗證 |

## 延伸挑戰（加分）

- **驗證考古三**：寫一個程式比較匿名 pipe 和 FIFO 寫入前後的 `st_mtime`。參考結果（本環境真實輸出）：

  ```
  anon pipe, before      mtime=1795233553.494992138
  anon pipe, after write mtime=1795233553.494992138
  FIFO, before           mtime=1795233554.494957999
  FIFO, after write      mtime=1795233555.494923859
  ```

  匿名 pipe 的 mtime 不變，FIFO 會更新——`fifo_pipe_write`（:604–617）呼叫 `file_update_time()`，`anon_pipe_write` 沒有。這是 `f017b0a4951f` 的行為在 user space 的可觀察後果。
- **EFD_SEMAPHORE 多 reader**：三個 thread 阻塞 read 一個 semaphore eventfd，主 thread `write(3)`，驗證三個各拿到 1。再試 `write(1)` 三次，觀察喚醒順序。
- **追 splice**：用 `splice()` 把檔案搬進 pipe，用 `/proc/*/wchan` 或 ftrace（需 root，在 QEMU 裡做，見 [Ch 51](./51-dynamic-tracing.md)）觀察 `buf->ops` 變成哪一個 `pipe_buf_operations`。
- **回答未解問題 8**：`git log -L '/^static ssize_t eventfd_write/,+33:fs/eventfd.c' v6.18`，找出為什麼 read 改成 `read_iter`（`12aceb89b0bc`，v5.8）而 write 沒有改。
- **換一對檔案**：用同樣的格式導讀 `fs/eventpoll.c`（它和本專案共用 wait queue 與 poll 的知識）或 `fs/timerfd.c`（eventfd 的近親）。

## 自我檢核

- [ ] 不看報告，在白板上畫出 pipe ring 的 head/tail，解釋為什麼 index 不取模、`pipe_index_t` 在 32 位元平台為什麼是 `unsigned short`
- [ ] 能解釋實驗 B 的 step 1 為什麼 writer 還在睡，並指出決定這件事的那一行
- [ ] 如果面試官問「pipe 滿了 writer 怎麼被叫醒？有多個 writer 時呢？」，能講出 exclusive wait 與接力喚醒
- [ ] 能說出 `head_tail` union 的完整因果鏈：哪個最佳化讓哪個潛伏 race 浮現、怎麼修
- [ ] 能說出至少三個 kernel 子系統為什麼選 eventfd 而不是 pipe 來通知 user space
- [ ] 回頭看你 Step 1 的冷讀紀錄：當時的卡點，現在每一個都能解釋了嗎？還解釋不了的，屬於哪一道鴻溝？

## 延伸閱讀

### man pages

- **pipe(7)、eventfd(2)、fcntl(2) 的 `F_SETPIPE_SZ` 段落** — Linux man-pages project
  - **讀哪裡**：pipe(7) 的「Pipe capacity」「PIPE_BUF」「/proc files」；eventfd(2) 的 read/write 語意與 `EFD_SEMAPHORE`
  - **學到什麼**：user space 看到的契約；拿它和本專案的程式碼對照，找出 man page 沒寫的實作細節（例如容量以頁為單位、merge 規則）
  - **前提**：無

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press, 2010）
  - **讀哪幾章**：第 44 章（Pipes and FIFOs）
  - **定位**：user space 視角最完整的 pipe 說明；作者也是 man-pages 的長期維護者。eventfd 在此書出版時太新、篇幅很少，以 man page 為準

### 技術文章

- **[The Dirty Pipe Vulnerability](https://dirtypipe.cm4all.com/)** — Max Kellermann（2022）
  - **這篇說什麼**：作者從一個損壞的 log 檔案一路追到 `PIPE_BUF_FLAG_CAN_MERGE` 未初始化的完整過程
  - **讀哪裡**：整篇；特別是他如何從症狀縮小到 pipe 的 merge 邏輯
  - **為什麼值得讀**：這是一份真實世界的「冷讀 + 考古 + 驗證實驗」紀錄，和本專案的方法完全同構；作者就是 `9d2231c5d74e` 的作者

### 官方文件

- **[Overview of the Linux Virtual File System](https://docs.kernel.org/filesystems/vfs.html)**（tree 內 `Documentation/filesystems/vfs.rst`）
  - **讀哪裡**：`struct file_operations` 一節
  - **學到什麼**：兩張 pipe fops 與 `eventfd_fops` 的每個欄位在 VFS 中的正式語意

---

這是本課的最後一份作業。寫完它，你已經用自己的方法讀完了一個完整的 kernel 子系統——下一個子系統，不需要再等一門課。

→ [回到課程地圖](./README.md)
