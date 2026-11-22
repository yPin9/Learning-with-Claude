# Ch 11 — metadata ops：fsync/statx/openat/close/fallocate

> **目標**：把「圍繞檔案但不是讀寫內容」的一整組操作學齊——同步落盤、查 metadata、開關檔、預配空間、改名與刪除。重點在兩件事：這些 op 在 kernel 裡**一律被丟到 io-wq 阻塞執行**（和 read/write 不同），以及 `openat` 開出來的 fd 怎麼**直接鏈**給後續操作，鋪陳 Ch 16 的 linked ops。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 Ch 0 的 `iou-cc`。輸出皆 WSL2 實測。

## 為什麼要把這些 op 放進 ring

你可能會想：開檔、查 size、改名這種事，用普通的 `open()`/`statx()`/`rename()` syscall 不就好了，幹嘛塞進 io_uring？三個理由：

1. **避免在熱路徑上被 syscall 阻塞。** `openat` 可能要走檔案系統的 path walk、甚至碰磁碟（讀 inode），在一個高吞吐的事件迴圈裡，一個同步 `open()` 卡住整條 thread 是災難。丟進 ring，它在背景 worker 阻塞，你的 thread 繼續收割其他完成。
2. **把一連串相依操作鏈成一條，少回 user-space。** 「開檔 → 讀 → 關檔」可以一次提交、一次收割，中間不回 user-space。這是 io_uring 真正威力的雛形。
3. **統一完成模型。** 所有 I/O（讀寫、網路、metadata）都從 CQE 回結果，你的事件迴圈只有一種收割邏輯，不必混用 syscall 回傳值和 epoll 事件。

## 先建立直覺：這些 op 全都「會阻塞」，所以全走 io-wq

read/write 有個快路徑：資料在 page cache 時，kernel 在**提交路徑上當場**非阻塞完成，不勞煩 worker（Ch 10 底層機制講過）。metadata ops 沒有這種好事——它們**本質上會阻塞**（path walk、同步刷盤、配置 extent），所以 io_uring 乾脆把它們**一律標記為強制非同步**，直接送進 io-wq 背景 worker 池跑。

```
 read/write：先試當場做（NONBLOCK），做不到才 punt
   submit ──► 試非阻塞 ──成功──► 當場填 CQE
                  │失敗(-EAGAIN)
                  └──► io-wq worker 阻塞重做 ──► 填 CQE

 metadata ops：一律 punt，不試當場做
   submit ──► 標記 REQ_F_FORCE_ASYNC ──► io-wq worker 阻塞執行 ──► 填 CQE
```

這個「一律 punt」對你有兩個實務意涵：**(a)** metadata op 的延遲比 cache-hit 的 read 高（多一次 worker 排程）；**(b)** 它們天生就是非同步，很適合跟別的操作鏈在一起——反正都要排隊。底層機制一節會給你原始碼證據。

## 核心內容：一趟 metadata 操作之旅

把 openat、fsync、fdatasync、fallocate、statx、close、mkdirat、renameat、unlinkat 一口氣跑過。完整檔見 `src/ch11_meta.c`，這裡逐段看。

### openat：用 io_uring 開檔，fd 從 CQE 回來

```c
io_uring_prep_openat(sqe, AT_FDCWD, path, O_RDWR | O_CREAT | O_TRUNC, 0644);
int fd = run_one();                           // res 就是新 fd（或負 errno）
```

```
[openat]   fd=4
```

`io_uring_prep_openat(sqe, dfd, path, flags, mode)` 對應 `openat(2)`：`dfd` 是起始目錄（`AT_FDCWD` 表示當前目錄），`flags`/`mode` 和 `open()` 一樣。**開檔的結果就是 CQE 的 `res`**——成功是新的 fd，失敗是負 errno。這點很關鍵：io_uring 把「開檔」也變成一個會回 `res` 的非同步操作。

> `io_uring_prep_openat2` 吃一個 `struct open_how`（對應 `openat2(2)`），能指定 `resolve` 旗標（如 `RESOLVE_NO_SYMLINKS`、`RESOLVE_BENEATH`）做更嚴格的路徑解析，適合有安全需求的場景。用法同 openat，只是參數包進 `open_how`。

### fsync vs fdatasync：落盤的兩種強度

write 的 `res` 等於長度只代表資料進了 page cache，**不代表落盤**（Ch 10 踩雷 #4）。要確保落盤，用 fsync：

```c
io_uring_prep_fsync(sqe, fd, 0);                     // 完整 fsync：資料 + inode metadata
run_one();
io_uring_prep_fsync(sqe, fd, IORING_FSYNC_DATASYNC); // 只刷資料（+ 讀回資料所需的最小 metadata）
run_one();
```

```
[fsync]    res=0 (資料+inode metadata)
[fdatasync]res=0 (IORING_FSYNC_DATASYNC)
```

差別：`fsync` 連 inode 的 metadata（修改時間、權限等）都刷；`IORING_FSYNC_DATASYNC`（值 `1 << 0`）對應 `fdatasync(2)`，只保證**資料本身**和**讀回資料必需的 metadata**（例如檔案被 extend 後的新大小）落盤，省掉非必要的 metadata 寫入。資料庫寫 WAL 常用 `fdatasync`，因為它不在乎 mtime，只要資料和長度可靠——少一次 metadata 刷就是少一點延遲。

`IORING_FSYNC_DATASYNC` 是唯一合法的 fsync flag；傳其他值 kernel 回 `-EINVAL`（原始碼 `io_fsync_prep` 裡 `if (sync->flags & ~IORING_FSYNC_DATASYNC) return -EINVAL;`）。

### fallocate：預先配置空間

```c
io_uring_prep_fallocate(sqe, fd, 0, 0, 1 << 20);     // mode=0, offset=0, len=1 MiB
run_one();
```

```
[fallocate]res=0 (配置 1 MiB)
```

`io_uring_prep_fallocate(sqe, fd, mode, offset, len)` 對應 `fallocate(2)`，一次把 1 MiB 的磁碟空間配置給檔案。`mode=0` 是預設（配置並視為已寫入 0）。預配空間能避免後續 write 時的 block 配置開銷與碎片，也能提早發現「磁碟滿了」（回 `-ENOSPC`）而不是寫到一半才爆。

### statx：查 metadata

```c
struct statx stx;
io_uring_prep_statx(sqe, AT_FDCWD, path, 0, STATX_SIZE | STATX_BLOCKS, &stx);
run_one();
```

```
[statx]    res=0 size=1048576 blocks=2048
```

`io_uring_prep_statx(sqe, dfd, path, flags, mask, statxbuf)` 對應 `statx(2)`：`mask` 指定你要哪些欄位（這裡要 `STATX_SIZE | STATX_BLOCKS`），結果填進你給的 `struct statx`。注意 `size=1048576` 正是剛才 fallocate 的 1 MiB——`fallocate` 確實把檔案撐到了 1 MiB，`blocks=2048` 是實際配置的 512-byte 區塊數（2048 × 512 = 1 MiB）。

> **buffer 所有權**：`statxbuf` 指向的記憶體，在 CQE 回來之前**不能被釋放或覆寫**，kernel 會在背景把結果寫進去。這和網路 I/O 的 buffer 所有權問題（Ch 12）是同一個原則：async 操作期間，buffer 歸 kernel。

### close：連關檔也非同步

```c
io_uring_prep_close(sqe, fd);
run_one();
```

```
[close]    res=0
```

`close` 塞進 ring 的價值，和前面一樣：不在熱路徑上同步關檔，而且能排進一條鏈的尾端（開 → 讀 → 關）。

### mkdirat / renameat / unlinkat：目錄與路徑操作

```c
io_uring_prep_mkdirat(sqe, AT_FDCWD, "/tmp/ch11_dir", 0755);          run_one();
io_uring_prep_renameat(sqe, AT_FDCWD, path, AT_FDCWD, "/tmp/ch11_renamed.txt", 0); run_one();
io_uring_prep_unlinkat(sqe, AT_FDCWD, "/tmp/ch11_renamed.txt", 0);    run_one();
```

```
[mkdirat]  res=0 (建 /tmp/ch11_dir, EEXIST=-17)
[renameat] res=0 (改名為 ch11_renamed.txt)
[unlinkat] res=0 (刪除)
```

這三個對應 `mkdirat(2)`/`renameat2(2)`/`unlinkat(2)`，語意完全一樣，只是改從 ring 提交。`renameat` 的最後一個參數是 flags（可用 `RENAME_NOREPLACE`、`RENAME_EXCHANGE`）；`unlinkat` 的 flags 傳 `AT_REMOVEDIR` 可刪目錄。失敗時 `res` 是負 errno，例如 mkdirat 碰到已存在的目錄回 `-EEXIST`(-17)。

### 相關的刷盤與 cache 提示 op

除了 fsync，還有兩個常搭配的 op，這裡點名讓你知道去哪找：

- **`io_uring_prep_sync_file_range(sqe, fd, len, offset, flags)`**：對應 `sync_file_range(2)`，只刷**特定 byte 範圍**而非整個檔，粒度比 fsync 細。注意它不保證 metadata 落盤，語意比 fsync 弱，要讀清楚 man page 再用。
- **`io_uring_prep_fadvise(sqe, fd, offset, len, advice)`**：對應 `posix_fadvise(2)`，給 kernel 存取模式的提示（`POSIX_FADV_SEQUENTIAL`、`POSIX_FADV_DONTNEED` 等），例如讀完一大段後 `DONTNEED` 叫 kernel 丟掉 page cache，避免污染。這在串流大檔時很有用。

`sync_file_range` 和前面的 metadata op 一樣走 `REQ_F_FORCE_ASYNC`（底層機制一節）；`fadvise` 則是**視 advice 而定**——會阻塞的（如 `DONTNEED` 要回收 page）才 punt 到 io-wq，不阻塞的提示可當場完成（原始碼 `io_fadvise_force_async()`）。

## 核心內容：openat 的 fd 鏈給後續操作（鋪陳 Ch 16）

前面每個 op 都「各別做」——送一個、等一個。但 io_uring 真正的招牌是把相依的操作**鏈成一條**：開檔 → 讀 → 關檔，一次提交，kernel 依序執行，中間不回 user-space。

這裡有個關鍵技術問題：**read 需要 fd，但 fd 是 openat 完成後才產生的**，提交時我還不知道。普通 fd 做不到這種前後傳遞。解法是 **direct descriptor（fixed file）**：讓 openat 把開好的 fd 直接放進 io_uring 的**註冊檔案表**的某個槽（slot），後續操作用「我要 slot N」來引用，不碰一般 fd 編號。slot 編號在提交時就固定了，所以能提前填。

```c
io_uring_register_files_sparse(&ring, 4);              // 註冊一張 4 格的稀疏檔案表

// (a) openat_direct：開好的 fd 放進 fixed slot 2，不回傳一般 fd
io_uring_prep_openat_direct(sqe, AT_FDCWD, src, O_RDONLY, 0, 2);
sqe->flags |= IOSQE_IO_LINK;                           // 鏈到下一個
sqe->user_data = 100;

// (b) read：fd 欄位填 2，但這是 fixed table 的索引，不是一般 fd
io_uring_prep_read(sqe, 2, rbuf, sz, 0);
sqe->flags |= IOSQE_FIXED_FILE | IOSQE_IO_LINK;        // fd=2 指 fixed table，繼續鏈
sqe->user_data = 101;

// (c) close_direct：關掉 fixed slot 2
io_uring_prep_close_direct(sqe, 2);
sqe->user_data = 102;

io_uring_submit(&ring);                                // 一次提交三個，kernel 依序跑
```

三個旗標是這條鏈的全部魔法：

- `IOSQE_IO_LINK`：把這個 SQE 和**下一個** SQE 鏈起來，保證前一個**成功完成**後才執行下一個。
- `IOSQE_FIXED_FILE`：告訴 kernel「這個 SQE 的 `fd` 欄位是 fixed table 的索引，不是一般 fd」。
- `openat_direct`/`close_direct`：開檔開進 slot、關檔關掉 slot，而不是產生/銷毀一般 fd。

實測：

```
[chain]    user_data=100 res=0       ← openat_direct 成功（fd 進 slot 2）
[chain]    user_data=101 res=28      ← read 讀到 28 bytes（用 slot 2）
[chain]    user_data=102 res=0       ← close_direct 關掉 slot 2
[chain] read 到的內容: io_uring linked chain works
```

一次提交，三個操作依序完成，read 正確用到了 openat 剛開進 slot 2 的檔案。**這就是 Ch 16 linked ops 的核心，** 這裡先讓你看到它能動；完整的鏈語意、`IOSQE_IO_HARDLINK`、drain、以及 fixed file 的完整 API（Ch 15）留到後面。

順帶一提，direct descriptor 的好處不只「能鏈」。一般 fd 佔用行程的 fd 表、有上限（`ulimit -n`）、每次操作要查 fd 表上鎖；direct descriptor 住在 io_uring 自己的檔案表裡，不佔行程 fd 空間、不受 `ulimit -n` 限制、查表更快。一個要同時開上萬個檔案或連線的服務，用 direct descriptor 能繞過 fd 數量牆、也更省——這是 Ch 15 會展開的效能主題，這裡先種個印象：**`openat_direct` / `accept_direct` 不只是鏈的前置，它本身就是高併發下的優化手段。**

### 這條鏈的致命陷阱：short read 會斷鏈

鏈有個嚴格規則：**前一個操作沒有「完全成功」，後面的全部 `-ECANCELED`**。而「short read」（讀到的比要求的少，Ch 10 講過）就算「不完全成功」。這是新手最常中的雷，實測給你看（`src/ch11_linkbreak.c`）：

```c
// /etc/hostname 只有十幾 bytes，卻要求讀 4096 => 必 short read
io_uring_prep_read(sqe, fd, buf, 4096, 0);
sqe->flags |= IOSQE_IO_LINK;                           // 鏈到下一個
sqe->user_data = 1;
io_uring_prep_nop(sqe);                                // 無害的後續 op
sqe->user_data = 2;
```

```
user_data=1 res=16
user_data=2 res=-125  (-ECANCELED：被前一個 short read 斷鏈)
```

read 明明「成功」讀了 16 bytes（`res=16` ≥ 0），但因為少於要求的 4096，io_uring 判定它**沒完全做到**，於是斷鏈，後面那個無辜的 nop 被 `-ECANCELED`(-125)。前面 Ch 11 鏈範例我特地把 read 長度設成剛好等於檔案大小（28 bytes），就是為了避開這個雷。**用 LINK 鏈 read 時，要嘛確定不會 short，要嘛用 `IOSQE_IO_HARDLINK`（Ch 16）改變斷鏈語意。**

## 底層機制：REQ_F_FORCE_ASYNC —— 為什麼 metadata ops 一律 punt

對照 6.18 原始碼。打開 `io_uring/sync.c`、`openclose.c`、`statx.c`、`fs.c`，每個 metadata op 的 `*_prep()` 結尾都有同一行：

```c
// io_uring/sync.c（io_fsync_prep / io_fallocate_prep）
// io_uring/openclose.c（io_openat_prep）
// io_uring/statx.c（io_statx_prep）
// io_uring/fs.c（renameat / unlinkat / mkdirat ...）
req->flags |= REQ_F_FORCE_ASYNC;
```

`grep -rn REQ_F_FORCE_ASYNC io_uring/` 在這幾個檔裡命中十來處。這個旗標的意思是「**不要試著在提交路徑上同步做，直接送 io-wq**」。對照 fsync 的 issue 函式更清楚：

```c
int io_fsync(struct io_kiocb *req, unsigned int issue_flags)
{
    ...
    /* fsync always requires a blocking context */
    WARN_ON_ONCE(issue_flags & IO_URING_F_NONBLOCK);     // 絕不該在非阻塞情境被呼叫
    ret = vfs_fsync_range(req->file, sync->off, end > 0 ? end : LLONG_MAX,
                          sync->flags & IORING_FSYNC_DATASYNC);
    ...
}
```

`WARN_ON_ONCE(... IO_URING_F_NONBLOCK)` 等於宣告「我一定在阻塞 context 跑」。`vfs_fsync_range` 也揭露一個彩蛋：fsync 其實支援**範圍**（`sync->off` 到 `end`），只是 `io_uring_prep_fsync` 預設 off=0/len=0（全檔）。想刷特定範圍可用 `io_uring_prep_sync_file_range`。

**實務意涵**：因為一律走 io-wq，大量併發的 metadata op 會吃掉 worker thread。如果你的 workload 是「狂開小檔」，io-wq 的 worker 數量（Ch 26）會變成瓶頸，而不是你的主 thread。這和 read/write 的 cache-hit 快路徑是完全不同的效能模型。

## 對比與取捨

| 需求 | op | 關鍵點 |
|---|---|---|
| 確保資料落盤 | `fsync` / `fdatasync` | `IORING_FSYNC_DATASYNC` 省 metadata 刷，WAL 常用 |
| 開檔不卡熱路徑 | `openat` / `openat2` | fd 從 `res` 回；openat2 可加 `resolve` 安全旗標 |
| 開完直接鏈給讀寫 | `openat_direct` + `IOSQE_FIXED_FILE` | fd 進 slot，提交時就能引用 |
| 預配空間 / 提早發現 ENOSPC | `fallocate` | 避免 block 配置開銷與碎片 |
| 查 size/權限/時間 | `statx` | `mask` 選欄位，buffer 歸 kernel 到 CQE |
| 目錄與路徑 | `mkdirat`/`renameat`/`unlinkat` | 語意同對應 syscall，flags 可選 |

## 踩雷集錦

1. **以為 openat 的 fd 可以直接填進同批的 read SQE**。錯誤直覺：「openat 回傳 fd，下一個 read 填那個 fd 就好」。正確認識：提交時 openat 還沒跑，一般 fd 根本還不存在。要用 `openat_direct` 進 slot + 後續 `IOSQE_FIXED_FILE` 引用 slot，靠 `IOSQE_IO_LINK` 保證順序。
2. **LINK 鏈裡的 read short 了，後面全 `-ECANCELED` 卻不知為何**。錯誤直覺：「read 回傳正數就是成功，怎麼會斷鏈」。正確認識：short read（`res` < 要求）被視為不完全成功而斷鏈。鏈中的 read 要確保讀滿，或用 `IOSQE_IO_HARDLINK`。
3. **write 完就以為安全，不 fsync**。錯誤直覺：「write `res` 等於長度 = 存好了」。正確認識：資料在 page cache，斷電即失。要持久化得 `fsync`/`fdatasync`；只在乎資料和長度用 `fdatasync` 更快。
4. **statx 的 buffer 提前釋放或讀取**。錯誤直覺：「prep 完 statx 就能讀 `stx`」。正確認識：結果是 kernel 在背景（io-wq）寫進去的，**CQE 回來前 `stx` 內容無效**，也不能釋放。
5. **狂開小檔卻發現主 thread 不忙、吞吐卻上不去**。錯誤直覺：「io_uring 應該很快」。正確認識：metadata op 一律走 io-wq，瓶頸在 worker 數，不在你的提交 thread。這是 `REQ_F_FORCE_ASYNC` 的直接後果。
6. **把 openat 的 `res` 當成「成功/失敗」的布林看**。錯誤直覺：「res >= 0 就是開好了」。正確認識：openat 成功時 `res` **就是新的 fd 數字**（可能是 4、5、很大的數），你要留著它去 read/close，不是丟掉。失敗才是負 errno。別把它和 fsync 那種「成功回 0」的 op 搞混。

## 本章重點整理

- metadata ops（openat/openat2、close、fsync、fallocate、statx、mkdirat/renameat/unlinkat）都把對應的 `*at` syscall 搬進 ring，結果從 CQE 的 `res` 回來。
- 這些 op 在 kernel 裡**一律 `REQ_F_FORCE_ASYNC`**，直接送 io-wq 阻塞執行，沒有 read/write 的 cache-hit 快路徑；併發量大時瓶頸在 worker 數。
- `fsync` 刷資料 + metadata，`IORING_FSYNC_DATASYNC`（=1）只刷資料與必要 metadata，WAL 場景更省。
- `openat_direct` 把 fd 開進註冊檔案表的 slot，後續用 `IOSQE_FIXED_FILE` + slot 索引引用，配 `IOSQE_IO_LINK` 就能做「開 → 讀 → 關」一條鏈——這是 Ch 16 linked ops 的雛形。
- 鏈的鐵律：前一個不「完全成功」後面全 `-ECANCELED`，而 **short read 算不完全成功**。

## 自我檢核（先別看前面，試著回答）

- [ ] 為什麼不能把 openat 回傳的 fd 直接填進同一批提交的 read SQE？正確做法是什麼？
- [ ] `fsync` 和 `fdatasync` 差在哪？資料庫寫 WAL 為什麼偏好後者？
- [ ] 一條 LINK 鏈裡 read 讀到的比要求少，會發生什麼？怎麼避免？
- [ ] 為什麼 metadata ops 不像 read/write 有「當場非阻塞完成」的快路徑？原始碼哪個旗標決定的？
- [ ] 我 prep 完 statx，能不能馬上讀結果 buffer？為什麼？
- [ ] openat 成功時 `res` 是什麼？和 fsync 成功時的 `res` 有什麼不同？
- [ ] 除了「能鏈」，direct descriptor（`openat_direct`/`accept_direct`）還解決了什麼高併發問題？

## 延伸閱讀

### man page

- **[io_uring_prep_openat(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_openat.3.html)、io_uring_prep_statx(3)、io_uring_prep_fsync(3)、io_uring_prep_close(3)**
  - **讀哪裡**：各 op 的參數、`IORING_FSYNC_DATASYNC` 說明、direct descriptor 版本（`*_direct`）
  - **學什麼**：哪些 op 有 `_direct` 變體、fixed file 索引怎麼傳

- **[openat2(2)](https://man7.org/linux/man-pages/man2/openat2.2.html)**
  - **讀哪裡**：`struct open_how` 的 `resolve` 旗標（`RESOLVE_BENEATH`、`RESOLVE_NO_SYMLINKS`）
  - **為什麼值得讀**：io_uring 的 `openat2` 讓你在非同步開檔時也能做嚴格路徑限制，安全場景很有用

### 原始碼

- **Linux 6.18 `io_uring/sync.c`、`openclose.c`、`statx.c`、`fs.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring>
  - **讀哪裡**：各 `*_prep()` 結尾的 `REQ_F_FORCE_ASYNC`；`io_fsync` 的 `WARN_ON_ONCE` 與 `vfs_fsync_range`
  - **學什麼**：為什麼這些 op 一律非同步、fsync 其實支援範圍
  - **前提**：讀完本章「底層機制」再看

### 落盤語意

- **[Ensuring data reaches disk](https://lwn.net/Articles/457667/)** — LWN, Jonathan Corbet
  - **這篇說什麼**：fsync/fdatasync/O_SYNC 的落盤保證層次，釐清本章 fsync 兩種強度的實際差別
  - **讀哪裡**：fsync vs fdatasync 段落
  - **前提**：知道 page cache 與 inode metadata

搞定檔案周邊操作後，下一章離開磁碟，進到網路：accept/connect/send/recv，以及 io_uring 的 completion 模型和 epoll 的 readiness 模型到底差在哪。

→ [Ch 12 網路 I/O：accept/connect/send/recv](./12-network-io.md)
