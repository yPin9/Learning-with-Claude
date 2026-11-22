# Ch 7 — SQE 詳解：opcode、flags、user_data

> **目標**：把 Ch 6 一直當黑盒填的 `struct io_uring_sqe` 整個攤開。看完你會知道這 64 bytes 每個欄位是什麼、為什麼一堆欄位是 union 疊在一起、七個 `IOSQE_*` flag 各改變什麼行為、opcode 在 6.18 長到第幾號、以及 `user_data` 的慣例。之後任何 `io_uring_prep_*` 你都能回推它動了哪些欄位。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4。本章一個範例用純 gcc（只讀 `<linux/io_uring.h>` 的結構），一個用 `iou-cc`。

## 為什麼要逐欄拆 SQE

Ch 6 我們靠 `io_uring_prep_read(sqe, fd, buf, n, off)` 填 SQE，很舒服，但也把結構藏起來了。一旦你要做 prep helper 沒直接涵蓋的事——設一個 `IOSQE_IO_LINK` 把兩個請求串起來、用 `IOSQE_FIXED_FILE` 走 registered file、讀 CVE writeup 時看懂攻擊者怎麼構造一個畸形 SQE——你就得知道**欄位本身**長什麼樣。

SQE 是你和 kernel 之間的**唯一一張工單**。你在這 64 bytes 裡寫清楚「做什麼（opcode）、對誰做（fd）、資料在哪（addr/len/off）、怎麼做（flags 與各 op 專屬 flags）、做完怎麼認（user_data）」，推進 SQ 環，kernel 照單處理。Part 3 讀 kernel 原始碼時，`io_submit_sqes` → `io_init_req` 就是在解這張工單；Part 4 的不少漏洞，根源是某個欄位組合被 kernel 解錯。把這張工單讀熟，是後面所有章節的地基。

## 先看全貌：64 bytes 的固定佈局

先用一支純結構程式把佈局打出來，眼見為憑。不碰 liburing，只 `#include <linux/io_uring.h>`：

```c
// ch07_sqe_layout.c（節錄）
printf("sizeof(struct io_uring_sqe) = %zu\n", sizeof(struct io_uring_sqe));
printf("  opcode     @ %2zu\n", offsetof(struct io_uring_sqe, opcode));
printf("  off        @ %2zu\n", offsetof(struct io_uring_sqe, off));
printf("  addr2      @ %2zu  (= off)\n", offsetof(struct io_uring_sqe, addr2));
printf("  rw_flags   @ %2zu\n", offsetof(struct io_uring_sqe, rw_flags));
printf("  fsync_flags@ %2zu  (= rw_flags)\n", offsetof(struct io_uring_sqe, fsync_flags));
printf("  user_data  @ %2zu\n", offsetof(struct io_uring_sqe, user_data));
```

```bash
gcc -O2 -Wall ch07_sqe_layout.c -o ch07_sqe_layout && ./ch07_sqe_layout
```

實測輸出：

```
sizeof(struct io_uring_sqe) = 64
sizeof(struct io_uring_cqe) = 16
offset of fields:
  opcode     @  0
  flags      @  1
  ioprio     @  2
  fd         @  4
  off        @  8
  addr2      @  8  (= off)
  addr       @ 16
  len        @ 24
  rw_flags   @ 28
  fsync_flags@ 28  (= rw_flags)
  user_data  @ 32
  buf_index  @ 40
  personality@ 42
```

三個數字先記住：**SQE 固定 64 bytes、CQE 固定 16 bytes**（開 `IORING_SETUP_SQE128`/`CQE32` 可倍增，Ch 8），而且 `off` 和 `addr2` 在**同一個 offset 8**、`rw_flags` 和 `fsync_flags` 在**同一個 offset 28**——這不是巧合，是 union 疊放，下一節細談。

SQE 是**固定 64 bytes** 的設計很關鍵：每個槽大小一致，kernel 和 user 都能用 `index * 64` 直接定址，不需解析變長結構。這也是為什麼它塞滿了 union——要在固定空間裡容納幾十種 op 各自不同的參數。

## 逐欄拆解

把 64 bytes 分段看。uapi 定義（`include/uapi/linux/io_uring.h`）：

```c
struct io_uring_sqe {
    __u8    opcode;     // [0]   做什麼：IORING_OP_*
    __u8    flags;      // [1]   通用 IOSQE_* flags
    __u16   ioprio;     // [2]   I/O 優先權；部分 op 借用（recv/accept 的 op 專屬 flags）
    __s32   fd;         // [4]   對哪個 fd 操作（或 registered file 索引）
    union { __u64 off; __u64 addr2; ... };        // [8]   檔案 offset／第二位址
    union { __u64 addr; __u64 splice_off_in; ...};// [16]  主 buffer／iovec 位址
    __u32   len;        // [24]  長度（bytes 或 iovec 個數）
    union { __u32 rw_flags; __u32 fsync_flags; ...}; // [28] op 專屬 flags（見下）
    __u64   user_data;  // [32]  你的身分標籤，CQE 原樣帶回
    union { __u16 buf_index; __u16 buf_group; };  // [40]  fixed buffer 索引／buffer group
    __u16   personality;// [42]  以哪個註冊的憑證身分執行
    union { __s32 splice_fd_in; __u32 file_index; ...}; // [44]
    union { __u64 addr3; ... __u8 cmd[0]; };      // [48]  第三位址／SQE128 的命令資料
};
```

分三類看：

**(1) 幾乎每個 op 都用的核心欄位**

- `opcode` [0]：這張工單是哪種操作。下一節有總覽表。
- `flags` [1]：通用修飾符，七個 `IOSQE_*`，和 op 無關，任何 op 都能疊（本章重點）。
- `fd` [4]：操作對象的檔案描述符。若 `flags` 設了 `IOSQE_FIXED_FILE`，這裡放的是 registered file 的**索引**而非真 fd（Ch 15）。
- `addr` [16] / `len` [24]：資料在哪、多長。read/write 是 buffer 與位元組數；readv/writev 是 iovec 陣列位址與 iovec **個數**（len 的語意隨 op 變）。
- `off` [8]：檔案 offset。read/write 從哪個位置；不適用的 op（如網路）此欄另有他用或忽略。
- `user_data` [32]：你的標籤，kernel 不解讀，CQE 原樣帶回（最後一節專講）。

**(2) op 專屬的 flags union（offset 28）**

這是全結構最容易誤解的地方。offset 28 那個 `__u32` 是個**巨大的 union**，同一塊記憶體依 opcode 不同，解讀成完全不同的 flags：

```c
union {
    __u32 rw_flags;       // READ/WRITE：RWF_* (如 RWF_HIPRI, RWF_NOWAIT)
    __u32 fsync_flags;    // FSYNC：IORING_FSYNC_DATASYNC
    __u32 poll32_events;  // POLL_ADD：要等的 poll 事件
    __u32 msg_flags;      // SEND/RECV：等同 send(2)/recv(2) 的 flags
    __u32 timeout_flags;  // TIMEOUT：IORING_TIMEOUT_ABS 等
    __u32 accept_flags;   // ACCEPT：等同 accept4(2) 的 flags
    __u32 open_flags;     // OPENAT：等同 open(2) 的 O_* flags
    __u32 statx_flags;    // STATX
    __u32 cancel_flags;   // ASYNC_CANCEL：IORING_ASYNC_CANCEL_*
    /* ...還有十幾個 */
};
```

為什麼不給每個 op 一個獨立欄位？因為 SQE 只有 64 bytes，而 op 有六十幾種，每種的參數不同。**union 讓同一塊空間被不同 op 重用**——一個 SQE 一次只是一種 op，不會同時是 read 又是 fsync，所以疊放安全。我們的佈局程式印出 `rw_flags` 和 `fsync_flags` 同在 offset 28，正是這個 union 的證據。

填 SQE 時你**只碰當前 op 對應的那個名字**：讀檔設 `rw_flags`、fsync 設 `fsync_flags`。liburing 的 prep helper 幫你碰對的那個，所以你平常無感；但看 CVE 或手構 SQE 時，要記得 offset 28 的值會被 kernel 按 opcode 解讀，填錯名字＝填錯語意。

**(3) 進階欄位（用到再回來）**

- `ioprio` [2]：原意是 I/O 優先權，但許多新 op 借它放 op 專屬 flags——例如 `recv` 的 `IORING_RECV_MULTISHOT`、`accept` 的 `IORING_ACCEPT_MULTISHOT` 都塞在 `ioprio`（Ch 13）。又一個「同一欄位多用途」的例子。
- `buf_index` / `buf_group` [40]：fixed buffer 的索引（Ch 15）或 provided buffer 的 group ID（Ch 14），union 疊放。
- `personality` [42]：以哪個事先註冊的憑證（credential）身分執行這個請求，做權限隔離用（Part 4 會談它的安全意涵）。
- `addr3` / `cmd[0]` [48]：第三個位址欄；開 `IORING_SETUP_SQE128` 時，SQE 變 128 bytes，多出來的 64 bytes 從這裡起當 `URING_CMD` 的命令資料（Ch 18 的 `uring_cmd`）。

## opcode 總覽：6.18 支援到第 62 號

`opcode` 是 `enum io_uring_op`，從 `IORING_OP_NOP = 0` 開始連號。Ch 0 的 probe 程式印出 6.18 的 `last_op = 62`——也就是 kernel 認得 0 到 62 號，第 62 號是 `IORING_OP_PIPE`。下表按功能分群，號碼就是 enum 的位置（會在 Part 2 逐類細講，這裡建索引）：

| 號碼 | opcode | 類別 | 細講章節 |
|---|---|---|---|
| 0 | `NOP` | 什麼都不做（測試／佔位） | 本章範例 |
| 1,2 | `READV` / `WRITEV` | 向量檔案 I/O | Ch 10 |
| 3 | `FSYNC` | 檔案同步 | Ch 11 |
| 4,5 | `READ_FIXED` / `WRITE_FIXED` | 用 registered buffer 的 I/O | Ch 15 |
| 6,7 | `POLL_ADD` / `POLL_REMOVE` | poll | Ch 18、28 |
| 8 | `SYNC_FILE_RANGE` | 範圍同步 | Ch 11 |
| 9,10 | `SENDMSG` / `RECVMSG` | 訊息式網路 I/O | Ch 12、13 |
| 11,12,15 | `TIMEOUT` / `TIMEOUT_REMOVE` / `LINK_TIMEOUT` | 逾時 | Ch 17 |
| 13 | `ACCEPT` | 接受連線 | Ch 12、13 |
| 14 | `ASYNC_CANCEL` | 取消請求 | Ch 17 |
| 16 | `CONNECT` | 建立連線 | Ch 12 |
| 17 | `FALLOCATE` | 預配空間 | Ch 11 |
| 18,19,28 | `OPENAT` / `CLOSE` / `OPENAT2` | 開關檔案 | Ch 11 |
| 20 | `FILES_UPDATE` | 更新 registered files | Ch 15 |
| 21 | `STATX` | 取檔案 metadata | Ch 11 |
| 22,23 | `READ` / `WRITE` | 一般檔案 I/O（最常用） | Ch 10 |
| 24,25 | `FADVISE` / `MADVISE` | I/O 建議 | Ch 11 |
| 26,27 | `SEND` / `RECV` | 位元組式網路 I/O | Ch 12、13 |
| 29,59 | `EPOLL_CTL` / `EPOLL_WAIT` | 和 epoll 互通 | Ch 18 |
| 30,33 | `SPLICE` / `TEE` | 零拷貝管線 | Ch 10 |
| 31,32 | `PROVIDE_BUFFERS` / `REMOVE_BUFFERS` | 舊式 provided buffers | Ch 14 |
| 34 | `SHUTDOWN` | 關閉 socket 方向 | Ch 12 |
| 35–39 | `RENAMEAT` … `LINKAT` | 檔案系統操作 | Ch 11 |
| 40 | `MSG_RING` | ring 之間傳訊息 | Ch 18 |
| 41–44 | `*XATTR` | 延伸屬性 | Ch 11 |
| 45 | `SOCKET` | 建 socket | Ch 12 |
| 46 | `URING_CMD` | 透傳裝置命令（NVMe 等） | Ch 18 |
| 47,48 | `SEND_ZC` / `SENDMSG_ZC` | zero-copy 送 | Ch 13、練習 C |
| 49 | `READ_MULTISHOT` | multishot 讀 | Ch 13 |
| 50 | `WAITID` | 非同步 waitid | Ch 18 |
| 51–53 | `FUTEX_WAIT` / `WAKE` / `WAITV` | 非同步 futex | Ch 18 |
| 54 | `FIXED_FD_INSTALL` | 把 registered fd 變回真 fd | Ch 15 |
| 55 | `FTRUNCATE` | 截斷檔案 | Ch 11 |
| 56,57 | `BIND` / `LISTEN` | socket 綁定／監聽 | Ch 12 |
| 58 | `RECV_ZC` | zero-copy 收 | Ch 13 |
| 60,61 | `READV_FIXED` / `WRITEV_FIXED` | 向量 + registered buffer | Ch 15 |
| 62 | `PIPE` | 建 pipe | Ch 11 |

不用背這張表，記兩件事就好：**(1)** opcode 連號成長，kernel 越新號越大，用 `io_uring_get_probe()`（Ch 0）確認手上這顆支援到哪；**(2)** 最常用的 `READ`=22、`WRITE`=23、`SEND`=26、`RECV`=27、`ACCEPT`=13 這幾個號碼，讀 strace / 看 trace 時眼熟了會很快。

> 號碼會隨 kernel 版本往後加，但**已存在的號碼永不改變**——這是 ABI 承諾。你 6.18 看到 `PIPE = 62`，在更新的 kernel 它還是 62，只是後面又多了幾號。所以硬編號碼在 io_uring 是安全的（但仍建議用 `IORING_OP_*` 常數，可讀性好）。

## IOSQE_* flags：七個通用修飾符

offset 1 的 `flags` 欄位，和 op 無關，任何 op 都能疊。7.18 共七個（`enum io_uring_sqe_flags_bit` 定義位元位置）。每個一句用途，細節連到後面章節：

| flag | 位元 | 一句話用途 | 細講 |
|---|---|---|---|
| `IOSQE_FIXED_FILE` | 0 | `fd` 欄填的是 registered file **索引**而非真 fd，省去每次查 fd 表 | Ch 15 |
| `IOSQE_IO_DRAIN` | 1 | 這個請求要等**先前所有**請求完成才開始，形成一道屏障 | Ch 16 |
| `IOSQE_IO_LINK` | 2 | 把下一個 SQE 串成鏈，**依序**執行，前一個失敗則後續取消 | Ch 16 |
| `IOSQE_IO_HARDLINK` | 3 | 同 LINK，但前一個失敗後續**仍執行**（硬鏈，不因錯中斷） | Ch 16 |
| `IOSQE_ASYNC` | 4 | 強制走 io-wq worker 非同步執行，不先嘗試 inline 快路徑 | Ch 24、26 |
| `IOSQE_BUFFER_SELECT` | 5 | 不自帶 buffer，從 `buf_group` 指的 provided buffer 池挑一個 | Ch 14 |
| `IOSQE_CQE_SKIP_SUCCESS` | 6 | 這個請求**成功就不發 CQE**，只有失敗才發（省 CQE 流量） | 本章範例 |

這七個是正交的：你可以 `IOSQE_IO_LINK | IOSQE_FIXED_FILE` 疊著用（鏈中一個走 registered file 的請求）。liburing 用 `io_uring_sqe_set_flags(sqe, flags)` 設，或直接 `sqe->flags |= ...`。

### 實測一個 flag：`IOSQE_CQE_SKIP_SUCCESS`

抽象講不如跑一個。下面提交 4 個 NOP（`IORING_OP_NOP`，永遠成功，最適合做 flag 實驗），前 3 個設 `IOSQE_CQE_SKIP_SUCCESS`——成功就不發 CQE。預期只有第 4 個回 CQE：

```c
// ch07_flags.c（節錄）
for (int i = 0; i < 4; i++) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_nop(sqe);
    io_uring_sqe_set_data64(sqe, 0xA0 + i);
    if (i < 3)
        sqe->flags |= IOSQE_CQE_SKIP_SUCCESS;   // 成功就別發 CQE
}
int submitted = io_uring_submit(&ring);
printf("submitted %d SQEs\n", submitted);

unsigned got = 0;
struct io_uring_cqe *cqe;
while (io_uring_peek_cqe(&ring, &cqe) == 0) {    // 不阻塞，把現成的收光
    printf("  CQE: user_data=0x%llx res=%d\n",
           (unsigned long long)cqe->user_data, cqe->res);
    io_uring_cqe_seen(&ring, cqe);
    got++;
}
printf("got %u CQE(s) back (expected 1)\n", got);
```

實測輸出：

```
submitted 4 SQEs
  CQE: user_data=0xa3 res=0
got 1 CQE(s) back (expected 1)
```

提交了 4 個，只收回 1 個 CQE——`user_data=0xa3`（第 4 個，`0xA0 + 3`），`res=0`（NOP 成功）。前三個成功了但照約定沒發 CQE。這就是 flag 真實改變行為的證據：同樣是 NOP，`IOSQE_CQE_SKIP_SUCCESS` 讓它「做了但不回報」。

這個 flag 在高吞吐場景很有用：一串 linked 操作裡，中間步驟成功你不關心，只在乎最後一步或任何失敗，設上它可大幅減少 CQE 流量（也就減少 CQ overflow 壓力，Ch 8）。但注意**失敗仍會發 CQE**——這正是你想要的：成功靜默、失敗吭聲。

> 用這個 flag 要很小心搭配 `IOSQE_IO_LINK`：鏈中前面步驟 skip success、最後一步不 skip，是常見模式。但如果你靠 CQE 數量來判斷「全部收齊了沒」，skip success 會讓數量對不上——這是一個經典的「少收了 CQE 以為漏了」的困惑來源。要嘛別用，要嘛清楚記得你 skip 了幾個。

## user_data 慣例：kernel 不看的 8 bytes

`user_data` [32] 是 SQE 裡唯一一個 **kernel 完全不解讀**的欄位。你寫什麼，CQE 原封不動帶回什麼。它存在的唯一理由：**完成是非同步、亂序的**（Ch 6 實測過 0、2、1 的亂序完成），你收到一個 CQE 時需要知道「這是我哪個請求完成了」。

兩種標準用法，Ch 6 已示範，這裡講清楚選擇準則：

```c
// (1) 請求對應一個堆上物件 → 塞指標
struct conn *c = malloc(sizeof *c);
io_uring_sqe_set_data(sqe, c);                 // 存指標
// 完成時：
struct conn *c = io_uring_cqe_get_data(cqe);   // 立刻拿回物件，O(1)

// (2) 請求對應一個 ID／索引 → 塞 64-bit 整數
io_uring_sqe_set_data64(sqe, req_id);          // 存 ID，不做指標轉換
uint64_t req_id = io_uring_cqe_get_data64(cqe);
```

準則：**對應物件就塞指標，對應編號就塞 ID**。塞指標的好處是收到 CQE 直接拿到物件、零查表；代價是那個物件必須活到 CQE 收到為止（提早 free 就是 UAF——這在 user space 是 bug，在 kernel 內部的對應機制則是 Part 4 一整類漏洞的來源）。

幾個真實慣例：

- **低位編碼型別**：指標通常對齊到 8 或 16 bytes，低幾位是 0，有人把「這是哪種請求」編進低位（`ptr | REQ_TYPE_READ`），收到 CQE 先看低位分類、再清掉低位取指標。省一個欄位，但要自己保證對齊。
- **0 是合法值**：`user_data = 0` 完全合法（Ch 2 的 raw 範例就用過）。別把 0 當「沒設」的哨兵，除非你確定不會有請求用 0。
- **multishot 請求**：一個 SQE 產生多個 CQE（Ch 13），它們帶**相同的 user_data**。你靠 `cqe->flags` 的 `IORING_CQE_F_MORE` 判斷「還會再來」，而非靠 user_data 區分——因為它們本就同一個請求。

## 踩雷集錦

1. **以為 offset 28 的 flags 欄位每個 op 獨立**：它是個大 union，同一塊記憶體被 `rw_flags`/`fsync_flags`/`msg_flags`… 共用。手構 SQE 時填錯名字（給 read 設了 `timeout_flags`）＝寫進同一塊地方但語意全錯。prep helper 幫你填對，手工填時要認 opcode。
2. **把 op 專屬 flags 塞進 `flags`[1] 欄位**：`flags`[1] 只放通用 `IOSQE_*`；op 專屬的（`RWF_NOWAIT`、`IORING_FSYNC_DATASYNC`）放 offset 28 的 union。兩個是不同欄位，搞混 kernel 要嘛 `-EINVAL` 要嘛行為詭異。
3. **`len` 的語意隨 op 變還當 bytes**：read/write 的 `len` 是位元組數，但 readv/writev 的 `len` 是 **iovec 個數**，provide_buffers 的 `len` 又是別的。填前確認這個 op 的 `len` 指什麼。
4. **用 `IOSQE_CQE_SKIP_SUCCESS` 後還靠 CQE 數對帳**：skip 掉的成功請求不發 CQE，你數 CQE 會「少」。要嘛別混用，要嘛明確扣掉 skip 的數量。
5. **以為 `fd` 欄一定是真 fd**：設了 `IOSQE_FIXED_FILE` 時，`fd` 是 registered file 的索引（Ch 15）。沒註冊卻設這個 flag，或註冊了卻忘設，都會 `-EBADF`。
6. **假設 SQE 填完 kernel 立刻讀**：`io_uring_get_sqe` 到 `io_uring_submit` 之間，SQE 還在你手上、kernel 沒看到。別在 submit 前就釋放 SQE 裡 `addr` 指到的 buffer——它要活到 CQE 收到（Ch 6 踩雷第 6 條、Ch 8）。

## 進階：SQE128 與為什麼要加大

標準 SQE 64 bytes 對絕大多數 op 夠用，但 `URING_CMD`（Ch 18，透傳 NVMe 等裝置命令）需要塞一整個裝置層的命令描述子，64 bytes 不夠。`IORING_SETUP_SQE128`（setup 時設）把每個 SQE 變成 **128 bytes**，多出的 64 bytes 從 `cmd[0]`（offset 48 起的 union 末端）展開，當命令資料用。

代價：SQ 的 SQE 陣列記憶體直接翻倍（深度 256 的 ring，SQE 區從 16KB 變 32KB）。所以只有真的需要（主要是 `URING_CMD`）才開。Ch 8 會看到對稱的 `IORING_SETUP_CQE32`（big CQE，16→32 bytes），用在需要回帶額外資料的完成（如 zero-copy 的 notification）。這兩個是 io_uring「固定大小槽」設計的彈性出口：預設緊湊，需要時可加倍。

## 本章重點整理

- SQE 固定 **64 bytes**，是你給 kernel 的唯一一張工單：opcode（做什麼）、fd（對誰）、addr/len/off（資料）、flags 與 op 專屬 flags（怎麼做）、user_data（怎麼認）。
- 結構塞滿 **union**：offset 8（`off`/`addr2`）、offset 28（`rw_flags`/`fsync_flags`/…十幾個 op 專屬 flags）、`ioprio` 被新 op 借去放 flags——同一塊空間依 opcode 重用，因為一個 SQE 一次只是一種 op。
- opcode 在 6.18 連號到 **62（PIPE）**，已存在的號永不改變（ABI 承諾）；用 `io_uring_get_probe()` 確認 kernel 支援範圍。
- 七個通用 `IOSQE_*` flag 正交可疊：FIXED_FILE、IO_DRAIN、IO_LINK、IO_HARDLINK、ASYNC、BUFFER_SELECT、CQE_SKIP_SUCCESS，各連到後面專章。
- `user_data` 是 kernel 不看、CQE 原樣帶回的標籤；**完成亂序**所以它是認請求的唯一方法，塞指標（對應物件）或 ID（對應編號）。

## 自我檢核

- [ ] 我能說出 SQE 為什麼固定 64 bytes、為什麼塞滿 union，並舉出至少兩組疊在同一 offset 的欄位
- [ ] 給我一個 opcode 號碼（如 22），我知道怎麼查它是什麼、以及這顆 kernel 支不支援
- [ ] 七個 `IOSQE_*` flag 我能各講一句用途，並知道它們和 offset 28 的 op 專屬 flags 是不同欄位
- [ ] 我能解釋 `IOSQE_CQE_SKIP_SUCCESS` 改變了什麼、以及它和 CQE 對帳的衝突
- [ ] 我知道 `user_data` 何時該塞指標、何時塞 ID，以及塞指標時那個物件的生命週期要求
- [ ] 我能說出 `len` 欄位的語意為什麼不能一律當「位元組數」

## 延伸閱讀

### 官方文件 / 原始碼

- **[include/uapi/linux/io_uring.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/include/uapi/linux/io_uring.h)**（v6.18）
  - **讀哪裡**：`struct io_uring_sqe` 定義、`enum io_uring_sqe_flags_bit`、`enum io_uring_op`——本章整章的一手來源，就在你 Ch 0 clone 的 `ref/linux/` 裡，`sed -n` 直接看
  - **為什麼值得讀**：所有 union 疊放、所有 op 專屬 flags 常數都在這裡定義，是行為存疑時的最終仲裁

- **[io_uring_enter(2)](https://man7.org/linux/man-pages/man2/io_uring_enter.2.html)**
  - **讀哪裡**：它有一大段逐 opcode 說明每個 op 怎麼填 SQE（哪個欄位放什麼）——查「某個 op 的 `addr`/`len`/`off` 分別是什麼意思」最快的地方
  - **注意**：這份 man page 很長但極完整，當字典用

### 教學文章

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **這篇說什麼**：把 `ioprio` 被 recv/accept 借去放 multishot flag、`buf_group` 的用法講得很清楚，正好補本章「欄位多用途」那幾段
  - **讀哪裡**：provided buffer 與 multishot 兩節；Ch 13、14 會再深入
  - **前提**：本章讀完，對 SQE 欄位有概念後看最有收穫

### 原始碼對照

- **[io_uring/opdef.c](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/opdef.c)**（v6.18）
  - **讀哪裡**：`io_issue_defs[]` 陣列——每個 opcode 一列，標注它「需不需要 fd、支不支援 poll、用不用 async」等屬性
  - **為什麼值得讀**：這張表是 kernel 如何「按 opcode 分派」的核心，Ch 23 會逐行讀它；先在這裡眼熟 opcode 和屬性的對應

SQE 這張工單讀熟了，kernel 處理完會回一張 CQE 收據。下一章把 CQE 攤開：`res` 的負 errno 慣例（我們會故意製造 `-EBADF`、`-EFAULT`）、`flags` 的那幾個 `IORING_CQE_F_*`、以及當 CQ 滿了放不下 CQE 時 kernel 怎麼辦（overflow 機制）。

→ [Ch 8 CQE 詳解：res/flags、錯誤處理、CQ overflow](./08-cqe-anatomy.md)
