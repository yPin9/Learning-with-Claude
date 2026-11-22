# Ch 10 — 檔案 I/O：read/write/readv/writev、O_DIRECT

> **目標**：把最常用的一組操作——檔案讀寫——用 io_uring 完整走一遍。學完你能正確使用 `io_uring_prep_read/write/readv/writev/read_fixed`，講清楚 offset 語意（含 `-1` 的陷阱）、搞懂 O_DIRECT 的對齊要求與它為何和 io_uring 天生一對，以及怎麼處理 short read。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 Ch 0 的 `iou-cc` 編譯。本章所有輸出都在 WSL2 實測。

## 為什麼先講檔案 I/O

Part 1 我們把 ring 的骨架、SQE/CQE、提交模型都拆透了，但範例一直是「讀 `/etc/hostname`」這種一次性 read。真正要用 io_uring，第一個碰到的就是檔案讀寫：資料庫把 page 刷到磁碟、log 系統 append、檔案伺服器送檔。這些全是 read/write 的變體。

檔案 I/O 也是理解 io_uring **async 模型**最好的起點，因為它暴露一個同步 syscall 世界裡不存在的問題：當你同時送出十個 read，它們**沒有彼此的順序保證**，各自獨立完成。同步世界裡 `read()` 一個接一個、共享同一個 fd offset；io_uring 世界裡你得自己想清楚每個操作讀寫哪個位置。這一章就從這裡切入。

## 先建立直覺：每個 I/O 都自帶位置

同步 `read(fd, buf, n)` 有一個你平常不太在意的隱形狀態：**fd 的當前 offset**。讀完 `n` bytes，offset 自動往前 `n`，下次 `read` 從新位置繼續。這個「共享游標」在單執行緒循序讀檔時很方便。

io_uring 把這個假設打破了。看這張圖：

```
 同步 read：共享一個游標，循序前進
   fd.offset ──► [====讀過====|當前|            未讀            ]
                 read() 把游標往前推，下一個 read 接著走

 io_uring：多個 read 同時在飛，各自帶 offset
   SQE1: read(fd, buf1, 4096, off=0)     ─┐
   SQE2: read(fd, buf2, 4096, off=4096)  ─┼─► 一起提交，獨立完成
   SQE3: read(fd, buf3, 4096, off=8192)  ─┘    順序不定
```

重點：**io_uring 的 read/write 每個都在 SQE 裡自帶 offset**（SQE 的 `off` 欄位，Ch 7 看過它的佈局）。你不是靠一個共享游標循序推進，而是明確告訴 kernel「這個 read 從第幾 byte 開始」。這讓你能一次把整個檔案拆成好幾段平行讀——但也意味著你得自己管位置，不能再依賴「上一個 read 讀到哪」。

那如果我就是想要「用當前 fd offset」的舊行為呢？有，傳 `offset = -1`。但這裡藏著一個陷阱，等一下實測給你看。

## 核心內容：讀寫的六種形狀

liburing 的 read/write helper 命名規律一眼就懂：

| helper | opcode | 說明 |
|---|---|---|
| `io_uring_prep_read(sqe, fd, buf, nbytes, offset)` | `IORING_OP_READ`(22) | 讀進單一 buffer |
| `io_uring_prep_write(sqe, fd, buf, nbytes, offset)` | `IORING_OP_WRITE`(23) | 從單一 buffer 寫出 |
| `io_uring_prep_readv(sqe, fd, iovec[], nr, offset)` | `IORING_OP_READV`(1) | scatter 到多塊 buffer |
| `io_uring_prep_writev(sqe, fd, iovec[], nr, offset)` | `IORING_OP_WRITEV`(2) | gather 多塊 buffer 寫出 |
| `io_uring_prep_read_fixed(sqe, fd, buf, nbytes, offset, buf_index)` | `IORING_OP_READ_FIXED`(4) | 讀進**預先註冊**的 buffer（Ch 15） |
| `io_uring_prep_write_fixed(...)` | `IORING_OP_WRITE_FIXED`(5) | 從註冊 buffer 寫出（Ch 15） |

`readv`/`writev` 對應 `preadv(2)`/`pwritev(2)`：一次操作橫跨多塊不連續記憶體（scatter-gather）。`read_fixed`/`write_fixed` 用的是 Ch 15 會講的 registered buffer，省掉每次 I/O 的 page pinning 成本，這裡先知道有這形狀，實作留到 Ch 15。

把 read、write、offset=-1、readv、writev、short read 一口氣跑一遍：

```c
// ch10_rw.c（節錄）—— 完整檔見 src/ch10_rw.c
int fd = open("/tmp/ch10_demo.txt", O_RDWR | O_CREAT | O_TRUNC, 0644);
const char *msg = "io_uring file I/O: read/write/readv\n";

// write 到 offset 0
sqe = io_uring_get_sqe(&ring);
io_uring_prep_write(sqe, fd, msg, strlen(msg), 0);
run_one();                                   // 送出 + 等一個 CQE，回傳 res

// read 從 offset 0
char buf[128] = {0};
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf, sizeof(buf) - 1, 0);
int res = run_one();                         // res = 讀到的 bytes
```

其中 `run_one()` 就是把「`io_uring_submit` + `io_uring_wait_cqe` + 取 `res` + `io_uring_cqe_seen`」包成一個函式，方便一步步示範。實測輸出：

```
[write]  res=36 (寫入 36 bytes)
[read]   res=36: io_uring file I/O: read/write/readv
```

`res` 帶的就是這個操作的回傳值，語意和對應的同步 syscall 一樣：read/write 成功是位元組數，失敗是負的 errno（Ch 8 講過）。

### offset = -1：用當前 fd offset，但別依賴它排序

傳 `offset = -1`（也就是 `(__u64)-1`，全 1）時，io_uring 改用 fd 的當前 offset，和同步 `read()` 一樣，讀完還會把 fd offset 往前推。實測：

```c
lseek(fd, 10, SEEK_SET);                      // 先把 fd offset 設到 10
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf, 8, (__u64)-1);   // -1 => 用當前 offset(=10)
res = run_one();
```

```
[read@-1] res=8 從 offset 10 起: "ile I/O:"
          讀後 lseek 當前 offset = 18
```

讀了 offset 10 起的 8 bytes（`"ile I/O:"`），而且讀完 fd offset 從 10 變成 18——io_uring 確實更新了共享的 `f_pos`。

**但這裡有個會咬人的陷阱。** 當你一次送出**多個** offset=-1 的 read，它們之間**沒有順序保證**。來看 `ch10_race.c`：把兩個 offset=-1、各讀 8 bytes 的 read 一次提交：

```c
io_uring_prep_read(s1, fd, b1, 8, (__u64)-1); s1->user_data = 1;
io_uring_prep_read(s2, fd, b2, 8, (__u64)-1); s2->user_data = 2;
io_uring_submit(&ring);                        // 一次提交兩個
```

實測（檔案前 16 bytes 是 `io_uring file I/`）：

```
read#1 = "io_uring"
read#2 = " file I/"
```

這次看起來「正確」：第一個讀 0–7，第二個讀 8–15。但**這是運氣**，不是保證。原因在底層：兩個 read 在這台機器上都走提交路徑**同步完成**（regular file + 資料在 page cache），第一個更新完 `f_pos` 第二個才被 issue，所以剛好序列化。一旦某個操作因為會阻塞而被丟到 io-wq 非同步跑（Ch 26），`f_pos` 的讀取與回寫時機就不再有序，你會讀到**重疊或跳過**的區段。這種 bug 偶發、極難重現。

> **結論**：檔案讀寫**永遠傳明確 offset**。offset=-1 只在「單一、不併發、就是要模擬同步游標」時才用。io_uring 沒有、也不打算提供跨並發操作的 `f_pos` 序列化——它要的就是每個 I/O 自帶位置。

### readv / writev：一次操作橫跨多塊記憶體

`readv` 把讀到的資料 scatter 到多塊 buffer，`writev` 把多塊 buffer gather 成一次寫出。對應 `preadv`/`pwritev`：

```c
char p1[8] = {0}, p2[16] = {0};
struct iovec iov[2] = {
    { .iov_base = p1, .iov_len = sizeof(p1) - 1 },   // 先填 7 bytes 到 p1
    { .iov_base = p2, .iov_len = sizeof(p2) - 1 },   // 再填 15 bytes 到 p2
};
io_uring_prep_readv(sqe, fd, iov, 2, 0);
```

```
[readv]  res=22  p1="io_urin"  p2="g file I/O: rea"
```

`res=22` 是 7+15 的總和，資料依序填滿第一塊才溢到第二塊。writev 反過來，把 `"AAA"` 和 `"BBB\n"` 兩塊合併成一次 7-byte 寫入：

```
[writev] res=7 (合併寫 7 bytes 到 offset 36)
```

scatter-gather 的價值在於**省 syscall / 省 copy**：封包的 header 和 body 在不同 buffer，不必先拼成一塊連續記憶體再寫。網路 I/O 的 `sendmsg`/`recvmsg`（Ch 13）用的是同一個概念。

### short read：你要的和你拿到的不一樣

這是檔案 I/O 最常被忽略的正確性問題。read 回傳值**可以小於你要求的長度**，這叫 short read，而且它**不是錯誤**。最常見的原因是讀到檔尾：

```c
off_t fsize = lseek(fd, 0, SEEK_END);
io_uring_prep_read(sqe, fd, buf, sizeof(buf) - 1, fsize - 5);  // 從倒數第 5 byte 要讀 127
res = run_one();
```

```
[short]  要求 127 bytes, 檔案剩 5 bytes => res=5 (short read)
```

要了 127 bytes，但檔案只剩 5 bytes，`res=5`。你的 code 必須檢查 `res` 和要求長度是否相等，不等就**從 `off + res` 繼續發下一個 read**，直到讀滿或 `res == 0`（EOF）。普通檔案的 short read 通常只因 EOF，但 pipe、socket、以及 O_DIRECT 邊界都可能 short，**一律要處理**。

> short read 在 io_uring 還有一個額外殺傷力：它會**斷掉 `IOSQE_IO_LINK` 的鏈**（Ch 11、Ch 16 會實測）。鏈中一個 read short 了，後面的操作全部 `-ECANCELED`。這是 io_uring 新手最容易中的雷之一，先記住。

正確處理 short read 的骨架是一個「續讀迴圈」：記住已讀總量，每次從 `off + done` 繼續，直到讀滿或 `res == 0`（EOF）：

```c
size_t want = N, done = 0;
while (done < want) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf + done, want - done, off + done);
    int res = run_one();                      // 實際上你會批次提交、非同步收割
    if (res < 0)  { /* 錯誤：-cqe->res 是 errno */ break; }
    if (res == 0) { /* EOF：檔案沒那麼長 */    break; }
    done += res;                              // 累加已讀，可能需要再送一次
}
```

注意這裡為了講解寫成同步的一送一等；真正高效的寫法是一次送出多段 read、非同步收割，但 short read 的續讀邏輯一樣：**每個 CQE 回來檢查 `res`，不足就補一個 read**。write 同理，`res < len` 就從 `buf + res` 續寫。

### readv2 的 flags：per-I/O 的 RWF 旗標

`io_uring_prep_readv2`/`writev2` 比 v1 多一個 `flags` 參數，對應 `preadv2(2)` 的 `RWF_*`，讓你對**單一這次 I/O**微調行為，不必動整個 fd：

| flag | 作用 |
|---|---|
| `RWF_NOWAIT` | 這次 I/O 若會阻塞就立刻回 `-EAGAIN`，不等磁碟（只對已在 cache 的資料成功） |
| `RWF_HIPRI` | 高優先、適合搭配 polling（`IORING_SETUP_IOPOLL`）壓低延遲 |
| `RWF_DSYNC` / `RWF_SYNC` | 這次 write 視同 `O_DSYNC`/`O_SYNC`，寫完即落盤 |

`RWF_NOWAIT` 和 io_uring 的內部 NONBLOCK 嘗試是同一個概念的 user-space 版本：你可以主動宣告「這段我不想等 io-wq，寧可拿 `-EAGAIN` 自己決定」。

## 核心內容：O_DIRECT 與 io_uring 為何天生一對

`O_DIRECT` 讓讀寫**繞過 page cache**，資料直接在你的 buffer 和儲存裝置之間 DMA。資料庫、儲存引擎幾乎都用它，因為它們要自己管 cache，不想讓 kernel 的 page cache 插一腳（雙重快取、不可控的 writeback）。

O_DIRECT 有嚴格的**對齊要求**：buffer 的記憶體位址、檔案 offset、傳輸長度，三者都必須對齊到裝置的邏輯區塊大小（通常 512 或 4096 bytes）。實測正確與錯誤兩種情形：

```c
int fd = open(path, O_RDWR | O_DIRECT);
void *abuf = NULL;
posix_memalign(&abuf, 4096, 4096);            // 對齊到 4096 的 buffer
io_uring_prep_read(sqe, fd, abuf, 4096, 0);   // buffer/offset/len 都對齊
res = run_one();                              // ok

io_uring_prep_read(sqe, fd, (char*)abuf + 1, 4096, 0);  // 位址故意 +1 => 不對齊
res = run_one();                              // 預期失敗
```

```
[O_DIRECT aligned] res=43
[O_DIRECT misaligned buf] res=-22 (Invalid argument)
```

對齊的讀成功（`res=43`，檔案只有 43 bytes 所以又是 short read）；位址 +1 不對齊的直接 `-EINVAL`(-22)。這個 `-EINVAL` 從檔案系統的 direct I/O 路徑冒出來，不是 io_uring 自己擋的。拿 `malloc` 的 buffer 丟 O_DIRECT，位址幾乎不會對齊——這是 O_DIRECT 新手第一個踩的雷，一定要用 `posix_memalign`。

**為什麼 io_uring 特別適合 O_DIRECT？** 三個層次：

1. **async 的好處在 O_DIRECT 身上最大。** buffered read 如果資料已在 page cache，`read` 當場就回（沒有真正的 I/O），async 幾乎沒賺頭。O_DIRECT 每次都真的碰裝置、真的會阻塞——用同步 syscall 你得為每個 in-flight I/O 綁一條 thread；用 io_uring 你一次提交幾十個 O_DIRECT read，它們在裝置佇列裡平行跑，一條 thread 收割全部完成。這正是 io_uring 的主場。
2. **IOPOLL 只對 O_DIRECT 有意義。** `IORING_SETUP_IOPOLL`（Ch 9 提過的輪詢完成模式，給 NVMe 這種低延遲裝置）**要求** fd 是 O_DIRECT 開的——因為它要輪詢的是裝置的完成佇列，buffered I/O 根本不走那條路。想用 io_uring 壓榨 NVMe 的 polling 模式，O_DIRECT 是前提。
3. **不經 page cache，就不受 cache 的鎖與 writeback 影響。** buffered write 可能卡在 dirty page 回寫、page cache 的鎖競爭上；O_DIRECT 的路徑更短、更可預測，配 io_uring 的批次提交，延遲的尾巴（tail latency）更短。這是資料庫在意的。

一句話：**io_uring + O_DIRECT 是高效能儲存的標準組合**，SPDK 的對手、現代資料庫的 I/O 層幾乎都長這樣。

## 底層機制：read/write 在 kernel 怎麼決定要不要阻塞

對照 6.18 的 `io_uring/rw.c`。read/write 的 SQE 進 kernel 後，`io_prep_rw()` 把 `sqe->off` 讀進 `kiocb.ki_pos`：

```c
// io_uring/rw.c
rw->kiocb.ki_pos = READ_ONCE(sqe->off);
```

然後 offset=-1 的特殊處理在 `io_kiocb_update_pos()`：

```c
if (rw->kiocb.ki_pos != -1)
    return &rw->kiocb.ki_pos;               // 有明確 offset，直接用
if (!(req->file->f_mode & FMODE_STREAM)) {   // 不是 pipe/socket 這種串流
    req->flags |= REQ_F_CUR_POS;             // 標記「用當前 f_pos」
    rw->kiocb.ki_pos = req->file->f_pos;      // 快照當前 f_pos
    return &rw->kiocb.ki_pos;
}
rw->kiocb.ki_pos = 0;                        // 串流無 offset 概念
return NULL;
```

`-1` 那條路設 `REQ_F_CUR_POS` 並**快照** `f_pos`。完成時（`kiocb_done()`）才把新位置寫回：

```c
if (ret >= 0 && req->flags & REQ_F_CUR_POS)
    req->file->f_pos = rw->kiocb.ki_pos;      // 完成時才回寫 f_pos
```

看懂這兩段，前面那個「並發 offset=-1 會 race」就清楚了：每個操作**在 issue 時快照 `f_pos`、在完成時回寫**。兩個操作若並發且非同步，可能都快照到同一個起點 → 讀重疊。io_uring 不在這裡上鎖，因為它的設計哲學就是「位置由 SQE 自帶」，`-1` 只是個相容性後門。

至於「要不要阻塞」：io_uring 先用 `IO_URING_F_NONBLOCK` 試著在提交路徑上**非阻塞地**把 read/write 做完（資料在 page cache 就成功）。做不到（要等磁碟）就回 `-EAGAIN`，由 io_uring 把這個請求丟給 io-wq worker（Ch 26）用阻塞方式重做。O_DIRECT 幾乎一定走第二條路——這也是它 async 賺最多的原因。

## 對比與取捨

| 情境 | 用什麼 | 為什麼 |
|---|---|---|
| 讀寫固定位置 | `prep_read/write` 傳明確 offset | 最單純、可平行、無 f_pos race |
| 模擬同步游標（單一不併發） | offset = `-1` | 相容舊語意，但**不可併發** |
| header/body 分開的記憶體 | `readv`/`writev` | 免拼接，一次 I/O |
| 同一塊 buffer 反覆高頻 I/O | `read_fixed`/`write_fixed`（Ch 15） | 省 page pinning |
| 高效能儲存、自管 cache、NVMe polling | `O_DIRECT` + io_uring | async 賺最多、IOPOLL 前提 |
| 要確認落盤 | 搭配 `fsync`（Ch 11） | write 回傳 ≠ 已落盤 |

## 踩雷集錦

1. **以為 read 回傳一定等於要求長度**。錯誤直覺：「我要 4096，`res` 就是 4096」。正確認識：short read 是常態不是錯誤，`res` 可能小於要求。一律 `while` 迴圈從 `off + res` 續讀，直到讀滿或 `res == 0`。
2. **並發用 offset=-1 期待它們循序讀**。錯誤直覺：「它們會像同步 read 一樣一個接一個」。正確認識：並發 offset=-1 沒有順序保證，快照/回寫 `f_pos` 的時機不確定。要循序就自己算 offset 明確傳。
3. **O_DIRECT 直接丟 malloc 的 buffer**。錯誤直覺：「buffer 不就一塊記憶體」。正確認識：O_DIRECT 要求位址、offset、長度三者對齊區塊大小，`malloc` 不保證對齊，必用 `posix_memalign`，`-EINVAL` 多半是這個。
4. **write 回傳成功就以為資料落盤了**。錯誤直覺：「`res` 等於長度 = 寫進磁碟了」。正確認識：buffered write 只進了 page cache，斷電就沒。要落盤得接 `fsync`/`fdatasync`（Ch 11）。O_DIRECT 也只保證離開 page cache，metadata 仍可能要 fsync。
5. **short read 斷了 LINK 鏈卻找不到原因**。錯誤直覺：「後面的操作怎麼都 `-ECANCELED`？」正確認識：鏈中一個 read short（`res` < 要求）會被當成「未完全成功」而斷鏈，後續全 `-ECANCELED`。Ch 11、Ch 16 會實測。

## 本章重點整理

- io_uring 的 read/write **每個操作自帶 offset**（SQE 的 `off`），不靠共享游標；要平行讀一個檔，就把它拆成多段各給 offset。
- `offset = -1` 會用 fd 的當前 `f_pos` 並在完成時回寫，但**並發使用無順序保證**（issue 時快照、完成時回寫，中間不上鎖）。檔案 I/O 一律傳明確 offset。
- `readv`/`writev` 做 scatter-gather，一次 I/O 橫跨多塊記憶體；`read_fixed`/`write_fixed` 用註冊 buffer（Ch 15）。
- short read 是常態、不是錯誤，必須迴圈續讀；它還會斷 `IOSQE_IO_LINK` 鏈。
- O_DIRECT 繞過 page cache、要求三重對齊（位址/offset/長度）；io_uring 特別適合它，因為 async 在真會阻塞的 I/O 上賺最多，且 IOPOLL 以 O_DIRECT 為前提。

## 自我檢核（先別看前面，試著回答）

- [ ] 為什麼 io_uring 要讓每個 read/write 自帶 offset，而不是像同步 read 共用 fd 游標？
- [ ] 我送出三個 offset=-1 的 read，為什麼可能讀到重疊區段？底層哪一步造成的？
- [ ] `res` 比我要求的長度小，我的程式該怎麼反應？哪些 fd 類型特別容易 short？
- [ ] 把 `malloc` 的 buffer 丟給 O_DIRECT read，最可能的 `res` 是多少？為什麼？
- [ ] 說出三個「io_uring 特別適合 O_DIRECT」的理由。
- [ ] write 的 `res` 等於長度，資料一定在磁碟上了嗎？

## 延伸閱讀

### 官方文件 / man page

- **[io_uring_prep_read(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_read.3.html) 與 io_uring_prep_readv(3)、io_uring_prep_read_fixed(3)**
  - **讀哪裡**：各 helper 的參數與回傳；`io_uring_prep_readv2(3)` 的 `flags`（如 `RWF_NOWAIT`、`RWF_HIPRI`）
  - **為什麼值得讀**：man page 明列哪些 `rw_flags` 可用，比翻原始碼快

### 原始碼

- **Linux 6.18 `io_uring/rw.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/rw.c>
  - **讀哪裡**：`io_prep_rw`、`io_kiocb_update_pos`、`kiocb_done`、`io_rw_init_file`
  - **學什麼**：offset=-1 的快照/回寫、short read 的 `io_fixup_rw_res`、NOWAIT 重試如何觸發 io-wq
  - **前提**：讀完本章「底層機制」一節再看會對得上

### O_DIRECT 與高效能 I/O

- **[Ensuring data reaches disk](https://lwn.net/Articles/457667/)** — LWN, Jonathan Corbet
  - **這篇說什麼**：write / fsync / O_DIRECT / O_SYNC 的落盤保證差異，釐清「寫了」和「落盤了」
  - **讀哪裡**：O_DIRECT 與 fsync 兩節，正好補本章踩雷 #4
  - **前提**：知道 page cache 是什麼

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：O_DIRECT 與 polling（IOPOLL）段落
  - **學什麼**：作者說明 io_uring 為何以 O_DIRECT 儲存為首要優化對象，呼應本章「天生一對」

把資料讀寫搞定後，下一章處理「圍繞檔案的其他操作」：開檔、關檔、同步、查 metadata、改檔名——而且會看到 openat 開出來的 fd 怎麼直接鏈給後面的操作。

→ [Ch 11 metadata ops：fsync/statx/openat/close/fallocate](./11-metadata-ops.md)
