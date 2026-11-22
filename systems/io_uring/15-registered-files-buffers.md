# Ch 15 — Registered files & buffers：fixed files/buffers

> **目標**：搞懂為什麼「先註冊、再用索引引用」能省掉每次 I/O 的隱藏成本。你會學會 `io_uring_register_files` / `register_buffers`、用 `IOSQE_FIXED_FILE` 與 `prep_read_fixed` 跑一個對比範例，並掌握 sparse file set 與動態更新（`register_files_update`）、registered ring fd 這些實務機制。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。registered files/buffers 從 5.1 就在；sparse set（`IORING_RSRC_REGISTER_SPARSE`）5.19+、registered ring fd（`IORING_REGISTER_RING_FDS`）5.18+。

## 為什麼需要 registered files 與 buffers

io_uring 把「提交」和「執行」分離，但每個操作在 kernel 端真正跑起來時，還是得先解決兩個前置問題：

1. **從 fd 拿到 `struct file`。** 你 SQE 裡填的是一個整數 fd，kernel 得去你的 fd 表（`files_struct`）裡查出對應的 `struct file *`，而且要對它 `fget`——加一個引用計數，免得操作執行到一半這個 file 被別的執行緒 `close` 掉。操作做完再 `fput` 減回去。這個 fd 表是**跨執行緒共享**的，查詢與增減引用都要走 RCU 與原子操作。**每一個** read/write/send/recv 都付這筆錢。

2. **把 user buffer 的頁釘住。** 一個讀寫操作可能在 io-wq worker（Ch 26）裡非同步跑，那時候你的 user buffer 必須保證還在、沒被 swap 出去、沒被 munmap。kernel 得把那塊記憶體的頁 `pin` 住、建立對應的 `bio_vec`，做完再放開。這也是**每個操作**都重來一次。

高頻 I/O 下——一個資料庫一秒幾十萬次讀寫、全都打在同幾個檔案上——這兩筆前置成本會非常可觀，而且**重複**：同一個 fd 查了幾十萬遍、同一塊 buffer 釘了放、放了釘。

registered files/buffers 的想法很直接：**把這些前置工作「一次做完、固定下來」，之後用索引引用，省掉每次的查詢與釘頁**。

## 先建立直覺：把 fd 與記憶體換成索引

```
  一般操作（每次都付前置成本）         registered（前置成本只付一次）

  SQE.fd = 7 ──► fget(7) 查 fd 表      register_files([7,9,...]) 一次
               ──► 取 struct file          └─ kernel 存成陣列，各 fget 一次
               ──► 操作完 fput             SQE.fd = 0 (索引) + IOSQE_FIXED_FILE
                 （每次重來）              ──► 陣列[0] 直接拿，refs++（無 fget）

  SQE.addr = buf ──► pin 頁、建 bvec     register_buffers([buf,...]) 一次
                  ──► 操作完 unpin          └─ kernel pin 頁一次、存好 bvec
                    （每次重來）            prep_read_fixed(..., buf_index=0)
                                          ──► 直接用存好的 bvec（無 pin）
```

重點是「索引」取代「值」：

- 註冊之後，SQE 的 `fd` 欄位不再是真實 fd，而是**註冊陣列的索引**，並掛上 `IOSQE_FIXED_FILE` 告訴 kernel「這個數字是索引，不是 fd」。
- buffer 也一樣：`prep_read_fixed` 的 `buf_index` 指向註冊好的 buffer，kernel 跳過釘頁，直接用當初 pin 好的頁。

## 範例：fixed file + fixed buffer 對比一般版

我們拿同一個檔案，用三種方式讀，看它們的結果一致、但走的路徑不同。先準備測試檔，內容是 `"registered files and buffers\n"`（29 bytes）。

**(A) 一般 read**——SQE 帶真實 fd、buffer 是臨時的：

```c
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf_a, sizeof(buf_a)-1, 0);   // fd 是真實 fd
io_uring_submit(&ring);
```

**(B) registered file + registered buffer**——先各註冊一個，再用索引：

```c
// 1) 註冊 fd：之後用「索引 0」代替真實 fd
io_uring_register_files(&ring, &fd, 1);

// 2) 註冊 buffer：之後用 buf_index 0 指這塊固定記憶體
struct iovec iov = { .iov_base = buf_b, .iov_len = sizeof(buf_b) };
io_uring_register_buffers(&ring, &iov, 1);

// 3) read_fixed：fd 欄位填「索引 0」、掛 IOSQE_FIXED_FILE；buffer 用 buf_index 0
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read_fixed(sqe, 0 /*file index*/, buf_b, sizeof(buf_b)-1, 0,
                         0 /*buf_index*/);
sqe->flags |= IOSQE_FIXED_FILE;
io_uring_submit(&ring);
```

注意 `prep_read_fixed` 的第二個參數（一般是 fd）這裡填 `0`——它是**檔案索引**，因為我們掛了 `IOSQE_FIXED_FILE`。最後一個參數 `buf_index=0` 選註冊好的那塊 buffer，用的是 `IORING_OP_READ_FIXED` opcode。

實測輸出（29 bytes 正是檔案內容長度，結尾的 `\n` 讓兩行黏在一起，這是真實輸出）：

```
(A) plain read   : res=29 "registered files and buffers
"(B) fixed read   : res=29 "registered files and buffers
"
```

兩者結果完全一致——這正是重點：**fixed 版本是效能優化，不改變語意**。差別全在 kernel 端省掉的前置成本，使用者看到的 `res` 與資料一模一樣。

## sparse file set 與動態更新

真實 server 的 fd 不是一開始就全有的——連線是陸續 `accept` 進來的。所以你常需要先**預留**一批空槽，之後再把 fd 填進去。這就是 sparse file set。

舊做法要先湊一個「全是 -1 的 fd 陣列」傳進去。5.19 起用 `IORING_RSRC_REGISTER_SPARSE`（liburing 的 `register_files_sparse`）直接註冊 N 個空槽：

```c
io_uring_unregister_files(&ring);                 // 先解除前面註冊的
io_uring_register_files_sparse(&ring, 4);          // 預留 4 個空槽，不必先有 fd

// 事後把真實 fd 填進槽位 2
io_uring_register_files_update(&ring, 2, &fd, 1);  // offset=2, fds=&fd, nr=1

// 用槽位 2 來讀
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, 2 /*file index*/, buf_c, sizeof(buf_c)-1, 0);
sqe->flags |= IOSQE_FIXED_FILE;
```

實測輸出：

```
(C) register_files_sparse(4): ok
    files_update slot 2 <- fd: ret=1 (填了幾個槽)
    read via slot 2 : res=29 "registered files and buffers
"
```

`register_files_update` 回傳**成功更新的槽數**（這裡是 1），不是 0/-1。`register_files_sparse` 底層對應 `IORING_REGISTER_FILES2` + `IORING_RSRC_REGISTER_SPARSE` 旗標。這組合是高併發 server 的標配：開一個大 sparse set，`accept_direct`（Ch 13 提過的 direct accept）把新連線直接塞進空槽，整條路徑完全不碰真實 fd 數字。

**用到空槽會怎樣?** 故意讀 sparse set 裡還沒填的槽位 0：

```c
io_uring_prep_read(sqe, 0 /*slot 0 是空槽*/, buf_d, ..., 0);
sqe->flags |= IOSQE_FIXED_FILE;
```

```
(D) read via empty slot 0: res=-9 (Bad file descriptor)
```

空槽回 `-EBADF`（-9），和「用了一個已關閉的 fd」同一個錯誤。這是個重要的邊界：sparse set 的槽預設是空的，用之前一定要先 `update` 填好。

## 底層機制：fget vs 陣列查表

為什麼 fixed file 省成本，一看 kernel 的兩條取 file 路徑就清楚了。在 `io_uring/io_uring.c`（6.18），一般 fd 走 `io_file_get_normal`：

```c
struct file *io_file_get_normal(struct io_kiocb *req, int fd)
{
    struct file *file = fget(fd);      // 查 fd 表 + 原子 inc 引用
    ...
    return file;
}
```

fixed file 走 `io_file_get_fixed`：

```c
inline struct file *io_file_get_fixed(struct io_kiocb *req, int fd,
                                      unsigned int issue_flags)
{
    struct io_rsrc_node *node;
    ...
    node = io_rsrc_node_lookup(&ctx->file_table.data, fd);  // fd 其實是陣列索引
    if (node) {
        node->refs++;                   // 一般遞增，非 fget
        req->file_node = node;
        file = io_slot_file(node);
    }
    ...
    return file;
}
```

差別一目了然：

- **一般版** `fget(fd)`：要走跨執行緒共享的 `files_struct`、做 RCU 保護下的查詢、對 `file->f_count` 做**原子**遞增；完成後對稱地 `fput`。每個操作一來一回。
- **fixed 版** `io_rsrc_node_lookup(..., fd)`：fd 當索引直接查 io_uring 自己的 `file_table` 陣列，`node->refs++` 是這個 ring 私有的計數（提交時持有 ring lock），不碰全域 fd 表、不做原子 `fget`。引用在 `register_files` 當下**一次** `fget` 好，之後都複用。

buffer 端同理。`register_buffers` 內部對每塊 buffer 呼叫 `pin_user_pages` 把頁釘住一次、建好 `bio_vec` 存進 `io_rsrc_node`。之後每次 `read_fixed`/`write_fixed` 直接拿這組釘好的頁，跳過一般 read/write 每次都要做的 `import_iovec` + 釘頁 + 放頁。釘頁涉及 mm 的 page refcount 與可能的 fault，省下來的不只是幾個指令，是整段 mm 慢路徑。

> 這也解釋了一個安全面的伏筆（Part 4 會深入）：registered buffer 把 user 頁**長期釘住**在 kernel，生命週期與 ring 綁定。這種「user 記憶體被 kernel 長期持有、又能被 user 端操作」的結構，正是多個 io_uring UAF/type-confusion CVE 的溫床。Ch 34 會拆一個 fixed buffer 的 type confusion。

## registered ring fd：連 ring 本身都註冊

還有一層成本藏在 `io_uring_enter` 這個 syscall 本身：它第一個參數是 ring 的 fd，kernel 每次 enter 都得 `fget` 這個 ring fd 一次。在 SQPOLL 關閉、每次提交都 enter 的高頻場景，這筆 `fget` 也重複。

`IORING_REGISTER_RING_FDS`（5.18+，liburing 的 `io_uring_register_ring_fd`）把 ring fd 自己也註冊進一張內部表，之後 `io_uring_enter` 改用一個小整數索引、跳過 `fget`：

```c
io_uring_queue_init(..., &ring, 0);
io_uring_register_ring_fd(&ring);   // 之後 liburing 的 submit 會自動用註冊索引
```

註冊之後，liburing 的 `io_uring_submit` 內部會自動改走索引路徑，你不用改別的。代價是這個註冊是 **per-thread** 的——註冊的執行緒和實際 enter 的執行緒必須是同一個。多執行緒各自持有 ring 的情況要小心。

## 對比與取捨

| | 一般 fd / buffer | registered（fixed） |
|---|---|---|
| 取 file | 每次 `fget`/`fput`（原子、走 fd 表） | 一次 `fget`，之後陣列查表 + 私有 refs++ |
| buffer | 每次釘頁 + 建 bvec | 一次 `pin_user_pages`，之後複用 |
| SQE 欄位 | 真實 fd / buffer 位址 | 索引 + `IOSQE_FIXED_FILE` / `buf_index` |
| 設定成本 | 無 | 註冊一次（有 syscall） |
| 適合 | fd/buffer 常變、用一兩次 | 少數 fd/buffer 被高頻重複使用 |
| 代價 | — | 記憶體長期釘住；更新要 `register_update` |

結論：**fixed 不是免費的優化**。它把成本從「每次操作」搬到「一次註冊」，只有在同一組資源被**反覆**使用時才划算。偶爾開一個檔案讀一次就關，註冊反而是淨虧。

## 踩雷集錦

1. **掛了 `IOSQE_FIXED_FILE` 卻在 `fd` 填真實 fd**：此時 kernel 把那個數字當索引去查註冊陣列，查出來的是別的 file 或空槽。結果可能是 `-EBADF`，或更糟——讀到不該讀的 file。掛 `FIXED_FILE` 時 `fd` 欄位**一定**是索引。

2. **`read_fixed` 的 buffer 超出註冊時的範圍**：`prep_read_fixed` 的 `buf`/`nbytes` 必須落在註冊那塊 iovec 之內。超出去 kernel 回 `-EFAULT`。fixed buffer 的位址不是任意的，是當初註冊的那塊的子區間。

3. **以為 fixed 永遠比較快**：只用一次的 fd/buffer，註冊的 syscall + 釘頁成本比省下來的還多。fixed 是給「高頻重複」的，不是預設就開。

4. **sparse set 的空槽直接拿來用**：範例 (D) 的 `-EBADF`。sparse 註冊只是**預留**槽位，每個槽要先 `register_files_update` 填進真實 fd 才能用。

5. **registered ring fd 跨執行緒用**：`register_ring_fd` 是註冊執行緒私有的。在 A 執行緒註冊、B 執行緒 enter，索引對不上。多執行緒各自管自己的 ring 時尤其容易中。

## 進階：direct descriptor 與「完全不回 user 空間的 fd」

registered files 還能更進一步：有些操作（`accept_direct`、`openat` 帶 `file_index`、`msg_ring_fd`）可以**直接把新產生的 fd 塞進註冊槽**，根本不經過 user 空間的 fd 表——這叫 direct descriptor。好處是新連線/新檔案一誕生就是「fixed」狀態，省掉「先拿到真實 fd、再 register、再用」的來回。`IORING_FILE_INDEX_ALLOC`（值是 `~0U`）讓 kernel 自己挑一個空槽放進去，再從 CQE 的 `res` 回報挑了哪個索引。Ch 18 的 `msg_ring` 傳 fd、以及高併發 server 的 `accept_direct`，都是這套。

另一個搭配是 `IORING_OP_FIXED_FD_INSTALL`：把一個 direct descriptor「安裝」回 user 空間的真實 fd——當你需要把這個 fd 交給非 io_uring 的 API（例如傳統 `epoll`）時用。direct descriptor 與真實 fd 之間因此是可以雙向轉換的。

## 進階：resource tags 與「什麼時候能安全重用」

動態更新 registered 資源有個隱患：當你用 `register_files_update` 把槽位 2 的 fd 從 A 換成 B，**A 可能還有 in-flight 的操作正在用它**。io_uring 不會當場釋放 A——它用 RCU（Ch 29 詳解）延後到「確定沒人再用 A」才真正放手。問題是：你（app）怎麼知道 A 被釋放了、可以安全 `close` 或重用?

答案是 **resource tags**。註冊時用帶 tag 的變體（`io_uring_register_files_tags` / `register_buffers_tags`），給每個資源一個 64-bit 標記。當某個資源**被更新或移除、且舊資源真正釋放時**，io_uring 會投一個 CQE，`user_data` 就是那個 tag。收到這個 CQE，你才知道舊的 fd/buffer 徹底沒人用了。

kernel 端在 `rsrc.c` 的更新路徑（`__io_sqe_files_update`）會把 tag 存進 `io_rsrc_node`，釋放節點時透過 aux CQE 通知。這是「resource 的生命週期由 kernel 的 RCU 決定、但 app 需要一個明確的『可以收手了』訊號」的橋樑。

為什麼這很重要?想想不用 tag 的情況：你更新槽位後立刻 `close(A)`，但 A 上還有一個 in-flight read 正在跑。這在一般 fd 不會出事（`fget` 的引用擋著），但在資源管理的邊界條件下，「app 以為釋放了、kernel 其實還在用」正是 use-after-free 的典型形狀。Part 4 的 Ch 34、Ch 37 會看到多個 io_uring CVE 的核心正是這類「resource node 生命週期與 app 認知不同步」。tags 就是 io_uring 給你的、用來把兩邊對齊的工具——在高頻動態更新 registered 資源的程式裡，它不是可選的，是必須的。

## 本章重點整理

- 每個一般 I/O 操作都付兩筆重複的前置成本：`fget`/`fput` 查 fd 表、釘 user buffer 的頁。registered files/buffers 把這些「一次做完、固定下來」，之後用索引引用。
- fixed file：`register_files` 註冊，SQE 的 `fd` 改填索引 + 掛 `IOSQE_FIXED_FILE`。fixed buffer：`register_buffers` 註冊，用 `prep_read_fixed`/`write_fixed` + `buf_index`。
- kernel 端的差別：一般走 `io_file_get_normal` 的 `fget`（原子、走共享 fd 表），fixed 走 `io_file_get_fixed` 的陣列查表 + 私有 `refs++`；buffer 則省掉每次釘頁。
- sparse file set（`register_files_sparse`，5.19+）預留空槽，`register_files_update` 動態填入；空槽直接用會回 `-EBADF`。底層是 `IORING_REGISTER_FILES2` + `IORING_RSRC_REGISTER_SPARSE`。
- registered ring fd（`register_ring_fd`，5.18+）連 `io_uring_enter` 的 ring fd `fget` 都省掉，但 per-thread。
- fixed 是把成本從「每次」搬到「一次」，只有高頻重複使用才划算；且 registered buffer 長期釘住 user 頁，是 Part 4 多個 CVE 的攻擊面。

## 自我檢核

- [ ] 我能說出一般 read 每次付的兩筆前置成本，以及 fixed 版本各自怎麼省掉
- [ ] 我知道掛 `IOSQE_FIXED_FILE` 後 SQE 的 `fd` 欄位該填什麼
- [ ] 我能解釋 `io_file_get_normal` 的 `fget` 和 `io_file_get_fixed` 的查表差在哪
- [ ] 我知道 sparse set 的空槽直接用會回什麼錯，以及正確流程
- [ ] 我能判斷一個場景該不該用 fixed（高頻重複 vs 用一兩次）

## 延伸閱讀

### man page

- **[io_uring_register_files(3)](https://man7.org/linux/man-pages/man3/io_uring_register_files.3.html)** 與 **[io_uring_register_buffers(3)](https://man7.org/linux/man-pages/man3/io_uring_register_buffers.3.html)**
  - **讀哪裡**：兩頁的 DESCRIPTION；`register_files` 的 update/sparse 變體、`register_buffers` 對 iovec 的要求
  - **學什麼**：每個 register 變體的精確語意與回傳值

### kernel 原始碼

- **[`io_uring/rsrc.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/rsrc.c)** 與 **[`io_uring/filetable.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/filetable.c)**（Linux 6.18）
  - **讀哪裡**：`io_sqe_files_register`、`io_sqe_buffer_register`（看 `pin_user_pages`）、`io_file_get_fixed`
  - **學什麼**：`io_rsrc_node` 這個資源節點結構怎麼存 file 與 buffer，Part 3 的 Ch 29 會整章解剖它

### 部落格 / wiki

- **[Lord of the io_uring — Fixed Files and Buffers](https://unixism.net/loti/tutorial/fixed_buffers.html)** — Shuveb Hussain
  - **這篇說什麼**：從零示範 `register_buffers` + `read_fixed`，和本章範例互補
  - **讀哪裡**：整頁；它的 iovec 準備那段對應本章的 `register_buffers`
  - **前提**：讀過 Ch 10 檔案 I/O

- **[What's new with io_uring (2022)](https://kernel.dk/axboe-kr2022.pdf)** — Jens Axboe
  - **讀哪裡**：direct descriptors、registered ring fd 兩節
  - **學什麼**：這些優化當初要解的效能問題，以及 direct descriptor 的設計動機

把 fd 與 buffer 固定下來之後，下一步是把**多個操作串起來**——讓「開檔→讀→關檔」在 kernel 裡一條龍跑完，不用每步都回 user 空間接力。

→ [Ch 16 Linked operations 與 drain](./16-linked-operations.md)
