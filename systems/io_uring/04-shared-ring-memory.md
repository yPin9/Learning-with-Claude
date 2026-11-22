# Ch 4 — 共享記憶體環：SQ/CQ ring、SQE/CQE 佈局與 mmap

> **目標**：把 Ch 2 當黑盒帶過的「ring 記憶體長怎樣」徹底攤開。你會懂：為什麼 SQ 環要一層間接 index 陣列、單調遞增計數器配 ring_mask 怎麼折槽又為什麼比「滿/空旗標」好、`io_sqring_offsets`/`io_cqring_offsets` 每個欄位是什麼、三個 mmap offset 各映射什麼、SINGLE_MMAP 怎麼把兩環併一塊、SQE/CQE 結構每個欄位做什麼、big CQE 又是什麼。全部用**實測的真實 offset 數字**對照。

> **環境**：kernel 6.18、gcc 11.4。本章 raw 範例不連結 liburing，直接讀 `<linux/io_uring.h>` 的結構。

## 為什麼要深挖佈局

你用 liburing 寫 io_uring，九成時間不用知道 ring 記憶體長怎樣——`io_uring_get_sqe` 給你一個槽，`io_uring_submit` 推 tail，就這樣。但有三種時刻，不懂佈局就卡死：

1. **debug 詭異行為**：CQE 讀到垃圾、提交了 kernel 卻沒反應、overflow——根因常在「哪個計數器、哪塊記憶體、誰該推進」。不懂佈局只能瞎猜。
2. **讀 kernel 原始碼**（Part 3）：`ctx->rings`、`ctx->sq_array`、`cached_sq_head` 這些名字，不先在 user 側把對應的佈局建立起來，到 kernel 側會完全迷路。
3. **讀 io_uring 的 CVE**（Part 4）：好幾類漏洞正是 index 陣列、ring_mask、buffer ring 的邊界算錯——`sq_array[head]` 這個間接讀若越界會怎樣，是攻擊面的核心。

這章是全課「原理深挖」的第一塊地基。我們不急著跑複雜操作，而是把一塊共享記憶體的每個 byte 搞清楚。

## 先建立直覺：三塊記憶體，不是一塊

初學者最大的誤解是「io_uring 就是一個環」。實際上 `io_uring_setup` 之後你要 `mmap` **三塊**獨立的東西（6.18 在 SINGLE_MMAP 下前兩塊可併成一次 mmap，但邏輯上仍是兩個環）：

```
 ┌──────────────────────────────┐   IORING_OFF_SQ_RING (0x0)
 │  SQ ring 的 metadata           │   head/tail/mask/entries/flags/dropped
 │  + SQ index 陣列 (sq_array)    │   array[i] 存「第 i 個槽該用哪個 SQE」
 └──────────────────────────────┘
 ┌──────────────────────────────┐   IORING_OFF_CQ_RING (0x8000000)
 │  CQ ring 的 metadata           │   head/tail/mask/entries/overflow/flags
 │  + CQE 陣列 (cqes)             │   完成事件直接住在這裡
 └──────────────────────────────┘
 ┌──────────────────────────────┐   IORING_OFF_SQES (0x10000000)
 │  SQE 本體陣列                   │   64 byte 一個,你填請求的地方
 └──────────────────────────────┘
```

關鍵分野，先刻進腦子：

- **CQE 直接住在 CQ 環裡**（`cqes` 陣列就是 CQE 本體）。
- **SQE 不住在 SQ 環裡**。SQ 環裡只有一層 **index 陣列**（`sq_array`），每格是一個數字，指向第三塊記憶體裡真正的 SQE。

這個不對稱——CQE 直接放、SQE 隔一層——是本章要解釋的第一個「為什麼」。

## 為什麼 SQ 要一層間接 index 陣列

CQ 那邊很直觀：kernel 要回報完成，就在 `cqes[tail & mask]` 寫一個 CQE，推 tail。消費者（你）從 `cqes[head & mask]` 讀。CQE 本體直接住環裡，沒有間接層。

SQ 那邊 kernel 大可以如法炮製：讓你在 `sqes[tail & mask]` 填請求、推 tail，kernel 從 `sqes[head & mask]` 取。為什麼偏要多一層 `sq_array`，讓 `sq_array[tail & mask]` 存一個 index、再去 `sqes[那個 index]` 取 SQE？

官方理由（`io_uring_setup(2)` man page 明說）是：**讓提交順序可以和填寫順序解耦**。展開來講有兩個實際好處：

1. **可以預先配置、重複使用 SQE，不必每次重填。** 想像你有幾個固定不變的請求（例如固定的 buffer、固定的 fd 的 read）。你可以把它們一次寫進 `sqes[]` 的某些格子，之後每次要提交就在 `sq_array` 裡放那個 index 即可，省掉重填 64 byte。SQE 本體和「這輪要提交哪些」分離了。

2. **提交順序可以亂序組合。** `sq_array` 這一輪可以是 `[3, 0, 7]`，下一輪 `[7, 3]`——同一批 SQE 本體，用不同的 index 組合、不同順序提交。SQE 陣列是「素材庫」，`sq_array` 是「這次的播放清單」。

kernel 取 SQE 的那段原始碼把這層間接看得清清楚楚（`io_uring/io_uring.c` 的 `io_get_sqe`，6.18）：

```c
static bool io_get_sqe(struct io_ring_ctx *ctx, const struct io_uring_sqe **sqe)
{
    unsigned mask = ctx->sq_entries - 1;
    unsigned head = ctx->cached_sq_head++ & mask;   // 先折槽

    if (... !(ctx->flags & IORING_SETUP_NO_SQARRAY)) {
        head = READ_ONCE(ctx->sq_array[head]);       // 間接:再查一次 index
        if (unlikely(head >= ctx->sq_entries)) {     // 邊界檢查!
            WRITE_ONCE(ctx->rings->sq_dropped, ... + 1);
            return false;
        }
    }
    *sqe = &ctx->sqes[head];                          // 用最終 index 取 SQE
    ...
}
```

兩件事值得記住。其一，那個 `head >= ctx->sq_entries` 的邊界檢查不是裝飾——`sq_array` 是 user 可寫的共享記憶體，惡意或出錯的 user 可以塞一個超大 index 進去，kernel 必須擋，否則就是越界讀。其二，6.17 以後有 `IORING_SETUP_NO_SQARRAY`（1<<16）這個 flag：設了它就**省掉這層間接**，kernel 直接 `sqes[cached_sq_head & mask]`。因為絕大多數程式（liburing 的預設路徑）根本沒用到「亂序提交」這個彈性——它們填 SQE 的順序就是提交順序，`sq_array[i]` 永遠等於 `i`。對這些程式，間接層是純開銷，NO_SQARRAY 把它拿掉。

> liburing 預設仍建 sq_array 並讓 `array[i]=i`（為了相容），所以你平常看到的 `sq_array` 就是個恆等映射。但「它可以不是恆等」正是這層間接存在的理由。

## 單調遞增計數器 + ring_mask：為什麼不用「滿/空旗標」

環形緩衝區的經典難題：`head == tail` 到底是空還是滿？兩者都可能讓 head 追上 tail。傳統解法有幾種（留一格不用、另存一個 count、加一個 full 旗標），各有同步上的麻煩——尤其在 user 和 kernel 並行、只能靠記憶體屏障協調的場景，多一個需要同步的狀態就多一分出錯空間。

io_uring 的解法乾淨得多：**head 和 tail 是單調遞增、永不回繞的 32-bit 計數器**。它們一路往上加（0, 1, 2, ... 直到 32-bit 溢位自然回繞），**不** mod 成 `[0, entries)`。要定位實際槽位時，才用 `index = counter & ring_mask` 折回去（`ring_mask = entries - 1`，而 entries 保證是 2 的次方）。

這樣一來：

- **空**：`head == tail`（消費者追平生產者）。
- **滿**：`tail - head == entries`（生產者領先消費者整整一圈）。
- 兩者**永不混淆**，因為計數器本身帶著「第幾圈」的資訊——`tail` 比 `head` 大 8 和大 0 是完全不同的值，即使折槽後指向同一格。

而且這套用**兩個計數器**就夠、各自單一寫者，沒有額外的 full 旗標要同步。生產者只寫 tail、消費者只寫 head，天生就是單生產者單消費者的乾淨配對。這直接餵給 Ch 5 的 release/acquire：要協調的共享狀態越少，記憶體屏障要守的不變式就越簡單。

這支程式把折槽算給你看（`mask=7`，即 entries=8）：

```c
// ch04_ring_layout.c（節錄）
unsigned counters[] = {0, 1, 7, 8, 9, 100, 4294967295u};
for (int i = 0; i < 7; i++)
    printf("  counter=%-10u & mask=%u -> slot %u\n",
           counters[i], *smask, counters[i] & *smask);
```

實測輸出：

```
== 單調計數器 + mask 折槽(mask=7) ==
  counter=0          & mask=7 -> slot 0
  counter=1          & mask=7 -> slot 1
  counter=7          & mask=7 -> slot 7
  counter=8          & mask=7 -> slot 0
  counter=9          & mask=7 -> slot 1
  counter=100        & mask=7 -> slot 4
  counter=4294967295 & mask=7 -> slot 7
```

看 `counter=8` 折回 `slot 0`：它和 `counter=0` 指向同一格，但值不同，所以「tail=8, head=0」明確是「滿」（差 8 = entries），而「tail=0, head=0」是「空」。計數器保留了圈數資訊，`& mask` 只在最後一刻丟掉它。這就是為什麼 **entries 必須是 2 的次方**——`& mask` 要能正確取低位，mask 必須是全 1 的位元串（`8-1=7=0b111`）。

> 32-bit 計數器溢位（超過 `4294967295`）會自然回繞到 0。因為用的是無號減法 `tail - head`，只要環深度遠小於 2³²（最大 32768），回繞不影響「差值」的正確性——這是無號算術的一個漂亮性質。

## io_sqring_offsets / io_cqring_offsets：欄位地圖

`io_uring_setup` 回填的 `sq_off` / `cq_off` 告訴你每個欄位在 mmap 出來的記憶體裡的 byte offset。6.18 的定義：

```c
struct io_sqring_offsets {
    __u32 head;          // SQ head 計數器的 offset(kernel 推進)
    __u32 tail;          // SQ tail 計數器(你推進)
    __u32 ring_mask;     // = entries-1,折槽用
    __u32 ring_entries;  // SQ 槽數
    __u32 flags;         // IORING_SQ_* (NEED_WAKEUP/CQ_OVERFLOW/TASKRUN)
    __u32 dropped;       // 因 sq_array 非法 index 被丟棄的 SQE 數
    __u32 array;         // sq_array(index 陣列)的 offset
    __u32 resv1;
    __u64 user_addr;
};
struct io_cqring_offsets {
    __u32 head;          // CQ head(你推進)
    __u32 tail;          // CQ tail(kernel 推進)
    __u32 ring_mask;
    __u32 ring_entries;
    __u32 overflow;      // CQ 滿了又來新完成,溢位計數
    __u32 cqes;          // CQE 陣列的 offset
    __u32 flags;         // CQ 側 flags
    __u32 resv1;
    __u64 user_addr;
};
```

注意兩邊的**生產/消費方向相反**，從「誰推哪個」看得最清楚：

- **SQ**：你是生產者，推 **tail**；kernel 是消費者，推 head。
- **CQ**：kernel 是生產者，推 **tail**；你是消費者，推 head。

兩個新欄位值得指出：`sq_off.dropped` 是「你在 `sq_array` 塞了非法 index，被 kernel 丟掉」的計數（對照上面 `io_get_sqe` 那個 `sq_dropped++`）；`cq_off.overflow` 是「CQ 滿了，新完成放不進去」的計數（Ch 8 的 CQ overflow 主題）。這兩個計數器平常是 0，一旦非 0 就是你的程式出了特定的錯——是很好的 debug 探針。

## 三個 mmap offset 與真實佈局

setup 回來後，用 `ring_fd` 配三個 magic offset 各 mmap 一塊。這三個 offset 是 ABI 常數：

| offset 常數 | 值 | 映射內容 |
|---|---|---|
| `IORING_OFF_SQ_RING` | `0x0` | SQ metadata + sq_array |
| `IORING_OFF_CQ_RING` | `0x8000000` | CQ metadata + cqes |
| `IORING_OFF_SQES` | `0x10000000` | SQE 本體陣列 |

這些 offset 不是真實記憶體位址，而是**傳給 `mmap` 的第 6 參數**，kernel 用它分辨你要映射哪一塊。來看實測。這支程式印出 setup 回填的所有 offset 與尺寸：

```bash
gcc -O2 -Wall -g ch04_ring_layout.c -o ch04_ring_layout
./ch04_ring_layout
```

實測輸出（kernel 6.18，要求 entries=8）：

```
sizeof(struct io_uring_sqe) = 64 bytes
sizeof(struct io_uring_cqe) = 16 bytes
要求 entries=8 -> kernel 給 sq_entries=8, cq_entries=16
== io_sqring_offsets(kernel 回填,單位 byte) ==
  head=0 tail=4 ring_mask=16 ring_entries=24 flags=36 dropped=32 array=320
== io_cqring_offsets ==
  head=8 tail=12 ring_mask=20 ring_entries=28 overflow=44 cqes=64 flags=40
== 三個 mmap offset 常數 ==
  IORING_OFF_SQ_RING = 0x0
  IORING_OFF_CQ_RING = 0x8000000
  IORING_OFF_SQES    = 0x10000000
SQ ring mmap 大小 = array_off(320) + sq_entries(8)*4 = 352 bytes
CQ ring mmap 大小 = cqes_off(64) + cq_entries(16)*16 = 320 bytes
SQE 陣列 mmap 大小 = sq_entries(8)*64 = 512 bytes
從共享記憶體讀回:ring_mask=7 (= ring_entries-1 = 8-1), SINGLE_MMAP=1
```

這些真實數字能畫出確切的記憶體佈局。注意 `SINGLE_MMAP=1`，代表 SQ 和 CQ 兩個環的 metadata **共用同一塊實體記憶體**，`sq_off` 和 `cq_off` 都是對這同一塊的 offset。把它們疊在一起看：

```
IORING_OFF_SQ_RING / IORING_OFF_CQ_RING  (SINGLE_MMAP:同一塊物理記憶體)
 offset  內容
   0    ┌──────────────┐  sq.head      (kernel 推)
   4    │              │  sq.tail      (你推)
   8    │   兩環的      │  cq.head      (你推)
  12    │   metadata    │  cq.tail      (kernel 推)
  16    │   交錯排在     │  sq.ring_mask
  20    │   同一塊裡     │  cq.ring_mask
  24    │              │  sq.ring_entries
  28    │              │  cq.ring_entries
  32    │              │  sq.dropped
  36    │              │  sq.flags
  40    │              │  cq.flags
  44    │              │  cq.overflow
   ...  ├──────────────┤
  64    │  cqes[16]    │  CQE 陣列,16 個 × 16 byte = 256 byte
       │  (直接住這)   │  佔 offset 64..320
   ...  ├──────────────┤
  320   │  sq_array[8] │  SQ index 陣列,8 個 × 4 byte = 32 byte
       └──────────────┘  佔 offset 320..352

IORING_OFF_SQES  (另一塊 mmap)
   0    ┌──────────────┐  sqes[0]   (64 byte)
  64    │  sqes[8]     │  sqes[1]
       │  SQE 本體     │  ...
 448    │  陣列         │  sqes[7]
       └──────────────┘  8 × 64 = 512 byte
```

這張圖的數字全部對得起來，而且能互相驗算：CQE 陣列從 offset `64` 起、16 個各 16 byte 共 256 byte，`64 + 256 = 320`——正好是 `sq_array` 的起始 offset。兩塊在 SINGLE_MMAP 區域裡**連續貼著排**，kernel 把它們塞進同一塊以省一次 mmap。SQE 本體則在完全獨立的 `IORING_OFF_SQES` 那塊，8 個各 64 byte 共 512 byte，和上面「SQE 不住 SQ 環裡」的直覺完全吻合。

### IORING_FEAT_SINGLE_MMAP

5.4 以前，SQ 環和 CQ 環是**兩次** mmap（兩塊物理記憶體）。5.4 加入 `IORING_FEAT_SINGLE_MMAP` 後，kernel 把兩環塞進一塊連續記憶體，讓你一次 mmap 就映射到兩個環——省一次 syscall，也省一塊頁表。判斷式（Ch 2 已見）：

```c
if (p.features & IORING_FEAT_SINGLE_MMAP) {
    if (cring_sz > sring_sz) sring_sz = cring_sz;
    cring_sz = sring_sz;               // 兩環用同一塊,取大的 size
}
void *sq_ptr = mmap(0, sring_sz, ..., ring_fd, IORING_OFF_SQ_RING);
void *cq_ptr = sq_ptr;                 // 直接指同一塊
if (!(p.features & IORING_FEAT_SINGLE_MMAP))
    cq_ptr = mmap(0, cring_sz, ..., ring_fd, IORING_OFF_CQ_RING);  // 舊 kernel 才分開
```

6.18 一定有這個 feature，但保留判斷式讓程式也能在舊 kernel 跑。無論合不合，**SQE 陣列永遠是獨立的第三次 mmap**（`IORING_OFF_SQES`），SINGLE_MMAP 只合併前兩個環。

> 6.5 又加了 `IORING_SETUP_NO_MMAP`（1<<14）：讓 user 自己分配 ring 記憶體（例如用 huge page）再告訴 kernel 位址（透過 `sq_off.user_addr`），完全不走 mmap。那是進階的記憶體控制手段，Ch 4 的範例不碰，知道有這條路即可。

## SQE 結構：一個請求的全部

SQE 是 64 byte 的結構，塞下了所有操作共用的欄位。因為不同 opcode 需要的參數差很多，它**大量用 union 複用同一塊空間**。6.18 的骨架（省略部分 union 分支）：

```c
struct io_uring_sqe {
    __u8  opcode;      // 操作類型:READ=22, WRITE=23, ACCEPT=13, SEND_ZC=47...
    __u8  flags;       // IOSQE_* (FIXED_FILE/IO_LINK/IO_DRAIN/BUFFER_SELECT...)
    __u16 ioprio;      // I/O 優先權
    __s32 fd;          // 操作的 fd(或 registered file index)
    union { __u64 off; __u64 addr2; ... };       // 檔案 offset,或第二位址
    union { __u64 addr; __u64 splice_off_in; ... }; // buffer 位址,或 iovec 陣列
    __u32 len;         // buffer 大小,或 iovec 個數
    union {            // 操作專屬的 flags,每種 op 一個名字
        __u32 rw_flags; __u32 fsync_flags; __u32 msg_flags;
        __u32 timeout_flags; __u32 accept_flags; __u32 open_flags; ...
    };
    __u64 user_data;   // 你的標籤,kernel 原樣帶回 CQE
    union { __u16 buf_index; __u16 buf_group; };  // fixed buffer index / buffer group
    __u16 personality; // 借哪組 registered 憑證執行
    union { __s32 splice_fd_in; __u32 file_index; ... };
    union {
        struct { __u64 addr3; __u64 __pad2[1]; };  // 第三位址(某些 op)
        __u8 cmd[0];   // SQE128 時,這裡是 80 byte 的任意命令資料(URING_CMD)
    };
};
```

幾個設計要點：

- **`opcode` 只佔 1 byte**：這就是 Ch 3 說的「操作變成資料」。256 種操作的空間，6.18 用到 62。
- **union 是核心手法**：`off` 這 8 byte，對 read 是檔案 offset，對 `URING_CMD` 是 `cmd_op`，對某些 op 是 `addr2`。同一塊記憶體按 opcode 解讀成不同意義。這讓 64 byte 能裝下所有操作，代價是「填 SQE 要照 opcode 對應正確的 union 成員」——填錯成員是 io_uring 新手常見 bug，liburing 的 `io_uring_prep_*` helper 存在正是為了幫你填對。
- **`user_data`**：你的身分標籤，kernel 不解讀，完成時原樣放進 CQE。真實程式設成指標或請求 ID。
- **末尾的 `cmd[0]`**：零長度陣列。平常 SQE 是 64 byte，開了 `IORING_SETUP_SQE128` 後 SQE 變 128 byte，多出來的 64 byte 從這裡延伸，給 `URING_CMD`（把任意 ioctl/NVMe 命令塞進 io_uring）用。

Ch 7 會逐欄位細講 SQE 和各個 `IOSQE_*` flag，這裡先建立「它是一塊 union 密集、按 opcode 變形的 64 byte」的整體印象。

## CQE 結構與 big CQE

CQE 相比之下極簡，預設 16 byte：

```c
struct io_uring_cqe {
    __u64 user_data;   // 你當初 SQE 設的,原樣回來——用它認出是哪個請求
    __s32 res;         // 操作回傳值:成功是正值(如 read 的位元組數),失敗是 -errno
    __u32 flags;       // IORING_CQE_F_* (見下)
    __u64 big_cqe[];   // 只在 CQE32 模式存在,多的 16 byte
};
```

三個固定欄位就是你收割完成時全部能拿到的資訊：

- **`user_data`**：對應回你的請求。一個 server 同時有上千個 in-flight 請求，全靠這個欄位把 CQE 對回「這是哪個連線的哪個操作」。
- **`res`**：操作結果，**語意等同對應的同步 syscall 的回傳值**。read 成功是讀到的位元組數，失敗是**負的 errno**（`-EBADF` = -9，`-EFAULT` = -14）。判斷失敗一律 `if (cqe->res < 0)`，取 errno 是 `-cqe->res`。這點 Ch 8 會反覆打磨。
- **`flags`**：附加資訊的位元旗標。

`flags` 的位元（6.18）：

| flag | 意義 |
|---|---|
| `IORING_CQE_F_BUFFER` (1<<0) | 用了 provided buffer，高 16 bit 是 buffer id（Ch 14） |
| `IORING_CQE_F_MORE` (1<<1) | 這個 SQE 還會產生更多 CQE（multishot，Ch 13） |
| `IORING_CQE_F_SOCK_NONEMPTY` (1<<2) | socket 還有資料可讀 |
| `IORING_CQE_F_NOTIF` (1<<3) | 這是 zero-copy send 的通知 CQE（Ch 13） |
| `IORING_CQE_F_BUF_MORE` (1<<4) | provided buffer 還會續用 |
| `IORING_CQE_F_32` (1<<15) | 這是一個 32-byte CQE（混合模式，Ch 8） |

**big CQE（`IORING_SETUP_CQE32`）**：開這個 flag 後，每個 CQE 從 16 byte 變 32 byte，多出的 16 byte 從 `big_cqe[]` 延伸。誰需要？需要在完成時回傳**更多資料**的操作——例如 `URING_CMD` 做 NVMe passthrough，裝置的回應不只一個 `res` 裝得下；或某些 zero-copy 場景。代價是整個 CQ 環的記憶體翻倍、折槽的步長變成兩格。`IORING_CQE_F_32` 這個 flag 和 6.18 新的 `IORING_SETUP_CQE_MIXED`（同環混用 16/32 byte CQE）有關，細節留 Ch 8。

> 為什麼 CQE 不像 SQE 那樣塞一堆 union？因為 CQE 是 kernel 寫、你讀，資訊量本質上就小——「哪個請求、結果多少、幾個旗標」。回傳資訊多的操作才需要 big CQE，所以它是 opt-in 而非預設。對稱地想：SQE 要承載「做什麼、怎麼做」所以欄位多；CQE 只承載「結果如何」所以欄位少。

## 對比與取捨

| 面向 | SQ 側 | CQ 側 |
|---|---|---|
| 生產者 / 消費者 | user 產、kernel 消 | kernel 產、user 消 |
| 誰推 tail / head | user 推 tail、kernel 推 head | kernel 推 tail、user 推 head |
| 本體住哪 | SQE 在獨立 mmap，環裡只有 index 陣列 | CQE 直接住環裡 |
| 為什麼這樣 | 解耦填寫與提交順序、可重用 SQE | 完成資訊小，直接放最簡單 |
| 可加大 | SQE128（`SETUP_SQE128`） | CQE32（`SETUP_CQE32`） |
| 出錯計數器 | `sq_off.dropped`（非法 index） | `cq_off.overflow`（CQ 滿） |

## 踩雷集錦

1. **以為 SQE 和 SQ 環在同一塊 mmap**。錯誤直覺：「填 SQE 就寫進 SQ 環。」正確認識：SQ 環（`IORING_OFF_SQ_RING`）裡只有 `sq_array` 這層 index 陣列，SQE 本體在獨立的 `IORING_OFF_SQES` 那塊。漏掉第三次 mmap，一碰 SQE 就 segfault。

2. **硬編 offset 或 struct 佈局去抓欄位**。錯誤直覺：「tail 不就在開頭 + 固定數字嗎。」正確認識：欄位 offset 由 kernel 透過 `sq_off`/`cq_off` 回填（本章實測 `sq.tail=4`、`cq.cqes=64`，換個情境可能不同），一定要 `sq_ptr + p.sq_off.tail` 這樣定位。硬編佈局，換 kernel 版本或開不同 flag 就錯。

3. **忘了 `& ring_mask` 就拿計數器索引陣列**。錯誤直覺：「tail 就是槽號，直接 `sqes[tail]`。」正確認識：head/tail 是**單調遞增的大數**（可能上萬甚至接近 2³²），直接索引直接越界。永遠 `counter & ring_mask` 折回 `[0, entries)`。

4. **以為 `head == tail` 一定是空**。錯誤直覺套在滿的情況：其實 `head == tail` 是空，**滿**是 `tail - head == entries`。因為計數器不回繞、保留圈數，`tail` 領先 `head` 一整圈時折槽後也會指同一格，但兩者值不同。靠「差值」判斷，不靠「折槽後是否相等」。

5. **把 CQE 當 16 byte 處理，卻開了 CQE32**。錯誤直覺：開了 `SETUP_CQE32` 還用 `cqes[head & mask]` 當 16-byte 陣列索引。正確認識：CQE32 下每個 CQE 是 32 byte，折槽的步長要對應放大（`(head & mask) << 1` 之類）。liburing 的 iterator 用 `shift` 處理這個，手刻就得自己乘對。索引算錯會讀到前後 CQE 的拼接垃圾。

## 進階：再往深一層

**`sq_array` 的邊界檢查是一條真實攻擊面。** 回頭看 `io_get_sqe` 的 `if (head >= ctx->sq_entries)`。`sq_array` 是 user 可任意寫的共享記憶體，kernel 從裡面讀出一個 index 去取 SQE。若少了這個檢查，user 塞一個超大值進 `sq_array[i]`，kernel 就會 `sqes[巨大值]` 越界讀——讀到 SQE 陣列以外的 kernel 記憶體當請求解讀。這類「user 控制的 index 進了 kernel 的陣列存取」是 io_uring 攻擊面的典型模式，Part 4 會看到變體（buffer ring 的 bid、registered file 的 index 都是同構的問題）。看 kernel 原始碼時，**每一個「從共享記憶體讀出來、拿去當 index」的地方都該有邊界檢查**，這是你讀 io_uring CVE patch 時第一個要盯的點。

**為什麼三個 mmap offset 是那些怪數字（`0x8000000`、`0x10000000`）？** 它們不是記憶體位址，是 kernel 用來分辨「你 mmap 的是哪塊」的 token。6.18 把這套編碼在 `IORING_OFF_MMAP_MASK`（`0xf8000000`）底下：高位元段決定類別（SQ 環 / CQ 環 / SQES / pbuf ring），低位元段還能編碼參數。例如 provided buffer ring 的 offset 是 `IORING_OFF_PBUF_RING | (bgid << IORING_OFF_PBUF_SHIFT)`——buffer group id 直接編進 offset 的低位。所以這些「magic 數字」其實是一套有結構的命名空間，不是隨便挑的。

**計數器用 32-bit 夠嗎?** 環最大深度 32768（SQ）、65536（CQ），遠小於 2³²。計數器每提交/完成一個 +1，要繞滿 2³² 需要 40 多億次操作——會發生，但因為判斷用的是無號差 `tail - head`，溢位回繞時差值仍然正確（無號算術的環繞性質）。所以 32-bit 計數器配上「永遠用差值、不用絕對值比較」的規則，可以安全地無限運轉。這也是為什麼所有讀寫這些計數器的地方都用無號型別——換成有號，溢位就是 UB，整個設計垮掉。

## 本章重點整理

- io_uring 要 mmap **三塊**：SQ 環（metadata + `sq_array` index 陣列）、CQ 環（metadata + CQE 本體）、SQE 本體陣列（獨立）。CQE 直接住環裡，**SQE 不住 SQ 環裡**，隔一層 `sq_array`。
- `sq_array` 這層間接讓「填寫順序」和「提交順序」解耦，可重用、可亂序 SQE；不需要時用 `IORING_SETUP_NO_SQARRAY` 省掉。kernel 讀它時**必須做邊界檢查**（攻擊面）。
- head/tail 是**單調遞增、不回繞的計數器**，`index = counter & ring_mask` 折槽（entries 是 2 的次方）。空是 `head==tail`，滿是 `tail-head==entries`，永不混淆，且只需兩個單寫者計數器。
- `sq_off`/`cq_off` 回填每個欄位的 byte offset，務必用「基底 + offset」定位，不硬編。`dropped`/`overflow` 是出錯探針。
- 三個 mmap offset 是有結構的 token（`IORING_OFF_MMAP_MASK` 編碼類別）；`IORING_FEAT_SINGLE_MMAP` 把前兩環併一塊 mmap，SQE 陣列永遠獨立。
- SQE 64 byte、union 密集、按 opcode 變形；CQE 16 byte 極簡（`user_data`/`res`/`flags`）。SQE128/CQE32 可加大，給 `URING_CMD`、zero-copy 等需要塞/回更多資料的操作。

## 自我檢核

- [ ] 不看圖，我能說出 io_uring 要 mmap 哪三塊、各裝什麼
- [ ] 我能解釋為什麼 SQ 要一層 `sq_array` 間接，而 CQ 不用
- [ ] 我能講清楚「單調計數器 + ring_mask」為什麼比「滿/空旗標」好，以及為什麼 entries 要是 2 的次方
- [ ] 我能說出 SQ 和 CQ 各自誰推 tail、誰推 head，以及空和滿分別怎麼判斷
- [ ] 我知道 `IORING_FEAT_SINGLE_MMAP` 合併的是哪兩塊、不合併哪塊
- [ ] 我能指出 `io_get_sqe` 裡那個邊界檢查為什麼是安全關鍵
- [ ] 我能說出 SQE 為什麼用那麼多 union，而 CQE 為什麼不用

## 延伸閱讀

### 官方文件 / man page

- **[io_uring_setup(2)](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)**
  - **讀哪裡**：`io_sqring_offsets`、`io_cqring_offsets` 每個欄位的逐條說明，以及三個 `IORING_OFF_*` 的用途——本章佈局圖的權威對照
  - **注意**：man page 明確寫了 `sq_array` 間接層「讓提交順序和填寫順序解耦」的理由
- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：ring 結構那節，SQ/CQ 雙環與 SQE 間接層的第一手設計說明
  - **和本章關聯**：本章的 offset 欄位就是這份文件描述的 ABI

### 原始碼

- **Linux 6.18 `io_uring/io_uring.c`**
  - **讀哪裡**：`io_get_sqe`（SQ 間接取 SQE + 邊界檢查）、`io_allocate_scq_urings` / `rings_size`（算三塊記憶體佈局、SINGLE_MMAP 怎麼合併）、`io_uring_mmap`（三個 offset 怎麼分派）
  - **前提**：對照本章實測的 offset 數字看，kernel 怎麼算出 `cqes=64`、`array=320` 會一目了然
- **`include/uapi/linux/io_uring.h`**
  - **讀哪裡**：`struct io_uring_sqe`、`io_uring_cqe`、`io_sqring_offsets`、`io_cqring_offsets` 的完整定義，以及所有 `IORING_OFF_*` / `IORING_CQE_F_*` 常數
  - **為什麼值得讀**：這是 ABI 的唯一真相來源，本章所有結構都出自它

### 教學文章

- **[Lord of the io_uring — Submission & Completion Queues](https://unixism.net/loti/)** — Shuveb Hussain
  - **這篇說什麼**：同樣用 raw 方式講 SQ/CQ 佈局與間接層，和本章互為印證
  - **讀哪裡**：low-level 那幾節講 ring 記憶體與 mmap 的部分

佈局攤開了，但我們一直把「推 tail 要 store-release、讀 tail 要 acquire」當規則死記。下一章把這個規則的底層講透：為什麼需要記憶體屏障、編譯器和 CPU 會怎麼重排、漏了屏障會怎樣、liburing 的屏障放在哪、以及為什麼在 x86 上這些 bug 偏偏不會現形卻在 ARM 上爆炸。

→ [Ch 5 提交與完成的 memory ordering](./05-submit-complete-ordering.md)
