# Ch 14 — Provided buffers 與 buffer ring

> **目標**：解決「recv 要先綁一塊 buffer 才能送出」在高併發下的浪費。你會學會兩套 provided buffers 機制——舊式的 `IORING_OP_PROVIDE_BUFFERS` 與新式的 buffer ring（`IORING_REGISTER_PBUF_RING`，5.19+）——並親手跑一個 buffer ring 的 recv 範例，看 CQE 怎麼告訴你 kernel 挑了哪個 buffer。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。buffer ring 需要 5.19+，multishot recv 需要 6.0+，本章兩個範例都在 6.18 實測。

## 為什麼需要 provided buffers

回到 Ch 12 的 recv。一個普通的 recv SQE 長這樣：

```c
io_uring_prep_recv(sqe, fd, buf, len, 0);   // buf 是你先配好的一塊記憶體
```

你提交的那一刻，就得把 `buf` 的位址寫進 SQE。問題在：**你不知道資料什麼時候來**。一個 server 同時掛著一萬條連線，每條都得先綁一塊 recv buffer 等著——即使其中九千條這一秒根本沒有資料進來。這一萬塊 buffer 全程佔著記憶體，而真正在用的只有少數幾塊。

這是 C10K 等級的經典浪費：**buffer 的生命週期被綁死在「提交」而不是「資料到達」**。multishot recv（Ch 13）讓你一個 SQE 收多段資料，但每段資料還是得往同一塊固定 buffer 寫，收完一段沒搬走、下一段就覆蓋了，等於逼你每段都同步處理。

provided buffers 把這件事倒過來：**你先給 kernel 一池 buffer，recv 時不指定要哪一塊，等資料真的到了、kernel 才從池子裡挑一塊填進去，並在 CQE 告訴你用了哪一塊**。沒有資料的連線不佔任何 buffer。一萬條連線可以共用一個幾百塊 buffer 的池子，因為同一瞬間真正有資料的連線遠少於一萬。

## 先建立直覺：buffer 池 + 「你自己挑」旗標

```
      app 準備                        kernel 在 recv 完成時
  ┌──────────────────┐
  │ buffer 池 (group) │            資料到 ──► kernel 從池裡
  │  [b0][b1][b2]...  │ ──給──►    挑一塊 bN，把資料寫進去
  └──────────────────┘            ──► CQE.flags 帶回 bN 的編號
         ▲                                     │
         │                                     ▼
         └──────── app 處理完 bN，歸還回池 ◄────
```

機制的三個角色：

- **buffer group**：一池 buffer，用一個 `buf_group`（bgid）識別。你可以有多個 group（不同連線類別用不同大小的 buffer）。
- **`IOSQE_BUFFER_SELECT`**：SQE 上的旗標，意思是「這個 recv 不自帶 buffer，完成時請你從我指定的 `buf_group` 裡挑一塊」。你提交時 `addr` 填 `NULL`、`len` 可填 0。
- **CQE 的回報**：完成時，`cqe->flags` 會設上 `IORING_CQE_F_BUFFER`，而**上 16 位**就是 kernel 挑中的 buffer id（bid）。你用 `cqe->flags >> IORING_CQE_BUFFER_SHIFT` 取出來，就知道資料落在哪一塊。

這解釋了為什麼 bid 要塞進 `flags` 的高位：CQE 只有固定幾個欄位（`res`、`flags`、`user_data`），沒有多餘空間放 bid，於是借用了 `flags` 的高 16 位。`IORING_CQE_BUFFER_SHIFT` 正是 16，低位留給各種 `F_` 旗標。

## 兩代機制：PROVIDE_BUFFERS 與 buffer ring

provided buffers 有兩套實作，你會在新舊程式碼都看到，先把差別講清楚。

**舊式：`IORING_OP_PROVIDE_BUFFERS`（5.7 起）。** 你用一個 SQE 操作把一批 buffer「交給」kernel：

```c
io_uring_prep_provide_buffers(sqe, base, buf_len, nr, bgid, bid_start);
```

kernel 內部用一個 linked list 存這些 buffer。缺點有二：補充 buffer 要再送一個 SQE（一次 syscall 路徑），而且 list 的增刪要上鎖。高吞吐下，「歸還 buffer」本身變成瓶頸。

**新式：buffer ring（`IORING_REGISTER_PBUF_RING`，5.19 起）。** 改用一塊**和 SQ/CQ 一樣的共享環形記憶體**（`struct io_uring_buf_ring`）當 buffer 池。app 是生產者（放 buffer）、kernel 是消費者（取 buffer），兩邊各推一個 tail/head，完全不走 syscall、不上鎖——歸還 buffer 只是往 ring 裡寫一個 entry、再 store-release 推 tail。這和 Ch 2 的 SQ 環是同一套「共享記憶體 + release/acquire」心法。

| | `PROVIDE_BUFFERS`（舊） | buffer ring（新，5.19+） |
|---|---|---|
| 補充 buffer | 送一個 SQE，走提交路徑 | 直接寫共享 ring，無 syscall |
| kernel 資料結構 | linked list，要上鎖 | 環形陣列，無鎖 |
| 歸還成本 | 高（每次一個操作） | 極低（寫一個 slot + 推 tail） |
| 適用 | 相容舊 kernel | 新程式一律用這個 |

本章兩個範例都用 buffer ring。舊式你知道存在、看得懂別人的 code 即可。

## 範例一：buffer ring 的 recv，看 bid 怎麼回來

liburing 2.16 把 buffer ring 包成幾個 helper，核心四個：

- `io_uring_setup_buf_ring(ring, nentries, bgid, flags, &err)`：配置並註冊一個 buffer ring，回傳 `io_uring_buf_ring *`。`nentries` 必須是 2 的次方。
- `io_uring_buf_ring_add(br, addr, len, bid, mask, offset)`：在 ring 的 `(tail+offset)&mask` 位置填一個 buffer（位址、長度、你給它的 bid）。
- `io_uring_buf_ring_advance(br, count)`：把剛填的 `count` 個 buffer「發佈」給 kernel（store-release 推 tail）。
- `io_uring_buf_ring_mask(nentries)`：回傳 `nentries - 1`，就是折算 slot 用的 mask。

我們用一個 `socketpair` 當連線，server 端（`sv[0]`）用 buffer ring 收兩段資料：

```c
#define NR_BUFS   8        // buffer ring 的 entry 數，必須是 2 的次方
#define BUF_SIZE  32       // 每個 buffer 的大小（故意小，製造 short read）
#define BGID      1        // buffer group id
static char bufs[NR_BUFS][BUF_SIZE];

struct io_uring_buf_ring *br =
    io_uring_setup_buf_ring(&ring, NR_BUFS, BGID, 0, &err);

int mask = io_uring_buf_ring_mask(NR_BUFS);
for (int i = 0; i < NR_BUFS; i++)
    io_uring_buf_ring_add(br, bufs[i], BUF_SIZE, i, mask, i);  // bid = i
io_uring_buf_ring_advance(br, NR_BUFS);                        // 一次發佈 8 個
```

recv 本身不帶 buffer，只掛旗標：

```c
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv(sqe, sv[0], NULL, 0, 0);   // addr=NULL, len=0
sqe->flags |= IOSQE_BUFFER_SELECT;             // 「收的時候你自己挑 buffer」
sqe->buf_group = BGID;                         // 從哪個 group 挑
io_uring_submit(&ring);
```

收 CQE 時從 `flags` 解出 bid，再把 buffer 歸還回 ring：

```c
int bid = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
printf("recv #%d: %d bytes in buffer bid=%d: \"%.*s\"\n",
       n, res, bid, res, bufs[bid]);
io_uring_cqe_seen(&ring, cqe);
io_uring_buf_ring_add(br, bufs[bid], BUF_SIZE, bid, mask, 0);  // 歸還
io_uring_buf_ring_advance(br, 1);
```

對端先後 `write` 了 `"hello buffer ring"`（17 bytes）和 `"0123456789abcdefghijXYZ"`（23 bytes）。實測輸出：

```
registered buffer ring: 8 bufs x 32 bytes, bgid=1
recv #0: 32 bytes in buffer bid=0: "hello buffer ring0123456789abcde"
recv #1: 8 bytes in buffer bid=1: "fghijXYZ"
```

兩件事值得停下來看：

1. **bid 從 0、1 依序被挑。** 我們沒指定要哪一塊，kernel 從 ring 的 head 端依序取：第一次 recv 拿 bid=0、第二次拿 bid=1。bid 原樣從 `cqe->flags` 高位回來。

2. **兩段 `write` 被黏成一串。** 這是 `SOCK_STREAM` 的本性——它不保留訊息邊界。第一次 recv 把 buffer 填滿到 `BUF_SIZE=32`（17+15），剩下的 8 bytes 留到第二次 recv。這正說明 buffer 的大小由你註冊時決定，kernel 填到滿為止；要保留訊息邊界得靠應用層協定或改用 `SOCK_SEQPACKET`。

## 範例二：multishot recv + buffer 耗盡（-ENOBUFS）

buffer ring 最常見的真實用法是搭 multishot recv：**一個 SQE 提交後，每來一段資料就產一個 CQE，各自帶自己的 bid**，連線活著期間不用重新提交。但 buffer 是有限的，池子抽乾會怎樣?這是必須親手看過的邊界。

這次只給 2 個 buffer，對端送 3 段，而且我們**故意不歸還**，模擬「app 處理不及、buffer 用光」：

```c
#define NR_BUFS   2        // 故意只給 2 個，製造耗盡
#define BGID      7
socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv);   // SEQPACKET 保留訊息邊界

struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv_multishot(sqe, sv[0], NULL, 0, 0);
sqe->flags |= IOSQE_BUFFER_SELECT;
sqe->buf_group = BGID;
io_uring_submit(&ring);

const char *msgs[] = { "first", "second", "third" };
for (int i = 0; i < 3; i++) write(sv[1], msgs[i], strlen(msgs[i]));
```

收 CQE 時檢查 `IORING_CQE_F_MORE`——它是 multishot 的「我還會再產 CQE」旗標：

```c
int more = !!(cqe->flags & IORING_CQE_F_MORE);
if (res < 0) { printf("... res=%d (%s), F_MORE=%d <- multishot 終止\n",
                      res, strerror(-res), more); break; }
int bid = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
```

實測輸出：

```
CQE #0: 5 bytes, bid=0, F_MORE=1: "first"
CQE #1: 6 bytes, bid=1, F_MORE=1: "second"
CQE #2: res=-105 (No buffer space available), F_MORE=0  <- multishot 終止
```

看懂這三行你就懂了 multishot + buffer ring 的全部要害：

- 前兩段各挑到一個 buffer（bid=0、bid=1），`F_MORE=1` 表示「這個 recv 還活著，之後還會有 CQE」。
- 第三段來時池子空了（我們沒歸還），kernel 無 buffer 可挑，這個 CQE 回 **`-ENOBUFS`（-105）**，且 **`F_MORE=0`**——multishot **就此終止**。
- `F_MORE=0` 是關鍵訊號：**你的 multishot recv 已經不在了**。真實程式收到 `-ENOBUFS` 必須做兩件事：補 buffer 回 ring、**重新提交一個 multishot recv**。只補 buffer 不重提，這條連線就再也收不到東西。

> **故意弄壞它**：把範例一裡「歸還 buffer」那兩行（`buf_ring_add` + `advance`）刪掉，連續 recv 超過 `NR_BUFS` 次。你會看到第 9 次 recv 開始回 `-ENOBUFS`——因為 8 塊全被你佔著沒還。provided buffers 的紀律是：**挑了就要還**，忘了還等於記憶體洩漏到 kernel 眼中的「已用」狀態。

## 底層機制：kernel 怎麼挑 buffer

buffer ring 的 kernel 端在 `io_uring/kbuf.c`。recv 完成要填資料時，呼叫 `io_ring_buffer_select()`（6.18，`kbuf.c:179`）：

```c
static struct io_br_sel io_ring_buffer_select(struct io_kiocb *req, size_t *len,
                                              struct io_buffer_list *bl,
                                              unsigned int issue_flags)
{
    struct io_uring_buf_ring *br = bl->buf_ring;
    __u16 tail, head = bl->head;
    ...
    tail = smp_load_acquire(&br->tail);        // acquire 讀 app 推的 tail
    if (unlikely(tail == head))
        return sel;                             // 空池 → 回空，上層轉 -ENOBUFS
    ...
    buf = io_ring_head_to_buf(br, head, bl->mask);  // bufs[head & mask]
    buf_len = READ_ONCE(buf->len);
    if (*len == 0 || *len > buf_len) *len = buf_len;
    req->flags |= REQ_F_BUFFER_RING | REQ_F_BUFFERS_COMMIT;
    req->buf_index = buf->bid;                  // 記住挑了哪個 bid
    sel.addr = u64_to_user_ptr(buf->addr);      // 要往哪寫
    ...
}
```

和 Ch 2 的 SQ 環一模一樣的對稱：

- **app 是生產者**：`io_uring_buf_ring_advance` 內部是 `io_uring_smp_store_release(&br->tail, ...)`，store-release 推 tail。
- **kernel 是消費者**：`smp_load_acquire(&br->tail)` acquire 讀 tail，保證讀到 tail 時也看到你剛填的 `buf->addr`/`len`/`bid`，不會讀到半成品。
- **`tail == head` 代表空**：這一行就是 `-ENOBUFS` 的源頭。kernel 從 `head & mask` 取 buffer、用完把 `bl->head` 往前推；app 歸還則推 `br->tail`。

這就是為什麼 buffer ring「無鎖、無 syscall」：歸還 buffer 不過是 app 往共享記憶體寫一個 slot、推一個 tail，和提交 SQE 同一個物理機制。

## 踩雷集錦

1. **以為 `IOSQE_BUFFER_SELECT` 的 recv 要帶 buffer**：錯。帶了反而語意衝突。`addr` 填 `NULL`、`len` 填 0（或填你能接受的上限，kernel 會取 min）。buffer 是 kernel 從 group 挑的，不是你給的。

2. **忘了把 bid 歸還**：收完 CQE 只 `cqe_seen`、沒 `buf_ring_add` 把 buffer 還回去。池子越用越少，最後全部 `-ENOBUFS`。「挑了就要還」是鐵律，對應 `cqe_seen` 要配一個 `buf_ring_add`。

3. **multishot 收到 `-ENOBUFS` 只補 buffer、不重提 recv**：`F_MORE=0` 表示 multishot 已經死了。補 buffer 回 ring 不會讓它復活，必須**重新 `prep_recv_multishot` 再提交**。漏了這步，連線靜默失聯，極難 debug。

4. **`nentries` 不是 2 的次方**：buffer ring 的 slot 定位靠 `& mask`，mask = `nentries - 1`，前提是 2 的次方。給 10 這種數字，`setup_buf_ring` 會回錯或行為未定義。

5. **bid 從 `flags` 低位去取**：`cqe->flags & 0xFFFF` 抓到的是一堆 `F_` 旗標，不是 bid。bid 在**高 16 位**，一定要 `>> IORING_CQE_BUFFER_SHIFT`（右移 16）。先檢查 `IORING_CQE_F_BUFFER` 有沒有設，確認真的有 buffer 附上來。

## 進階：incremental consumption 與 provide 的大小策略

buffer ring 註冊時可帶 `IOU_PBUF_RING_INC`（6.11+）旗標，開啟 **incremental consumption**：一塊很大的 buffer 可以被多次 recv「漸進」消耗，而不是一次 recv 就整塊算用掉。這讓你註冊少數幾塊大 buffer、而非大量小 buffer，kernel 與 app 各自記住「目前讀到這塊的哪個 offset」。適合串流大量資料、又不想頻繁歸還的場景。本章範例用的是預設（非 incremental）模式，一次 recv 消耗一整塊。

buffer 大小的取捨值得想清楚：**太小**會像範例一那樣頻繁 short read、一段資料拆進多塊、增加 CQE 數量；**太大**則每塊佔記憶體多、池子裡能放的塊數變少。常見做法是按連線類別分 group——小訊息協定用小 buffer group、檔案傳輸用大 buffer group——recv 時用 `buf_group` 指到對的那池。

還有一個更新的優化叫 **bundle**（`IORING_RECVSEND_BUNDLE`，6.10+）：一個 recv 可以**一次填多塊 buffer**，kernel 把到達的資料連續鋪進 ring 裡接下來幾塊 buffer，用一個 CQE 回報總長，`cflags` 帶起始 bid。這解決了「一大段資料硬被 `BUF_SIZE` 切成很多塊、產很多 CQE」的問題——bundle 讓「一次 recv 對一個 CQE」即使資料跨多塊也成立，收割端用起始 bid + 長度自己算跨了哪幾塊。send 端也有對應的 bundle，一次 send 吃掉 ring 裡連續幾塊 buffer。這是把 buffer ring 的效率再往前推一步的機制，適合高吞吐、大訊息的場景。

## 本章重點整理

- provided buffers 把 buffer 的生命週期從「提交時」延到「資料到達時」：沒資料的連線不佔 buffer，高併發下省下大量記憶體。
- recv 掛 `IOSQE_BUFFER_SELECT` + 指定 `buf_group`，`addr`/`len` 留空；完成時 `cqe->flags` 設 `IORING_CQE_F_BUFFER`，bid 在 `flags >> IORING_CQE_BUFFER_SHIFT`（高 16 位）。
- 兩代機制：舊式 `IORING_OP_PROVIDE_BUFFERS`（linked list、要上鎖、補充走 SQE），新式 buffer ring（`IORING_REGISTER_PBUF_RING`，5.19+，共享環形記憶體、無鎖、無 syscall）。新程式一律用 buffer ring。
- 四個核心 helper：`setup_buf_ring` / `buf_ring_add` / `buf_ring_advance` / `buf_ring_mask`。歸還 buffer = `buf_ring_add` + `advance`，對稱於 Ch 2 的 store-release 推 tail。
- multishot recv 搭 buffer ring 是典型組合；buffer 耗盡時那個 CQE 回 `-ENOBUFS` 且 `F_MORE=0`，表示 multishot 終止，必須補 buffer **並重新提交**。

## 自我檢核

- [ ] 我能說出 provided buffers 解決的具體浪費是什麼，以及它和一般 recv 在 buffer 生命週期上的差別
- [ ] 我知道怎麼從 CQE 取出 kernel 挑中的 bid，以及為什麼它在 `flags` 的高位
- [ ] 我能講清楚 buffer ring 相對舊式 `PROVIDE_BUFFERS` 好在哪（無鎖、無 syscall、共享環）
- [ ] 我知道「挑了就要還」，也知道忘了還會怎樣
- [ ] 我能解釋 multishot recv 收到 `-ENOBUFS`、`F_MORE=0` 之後，程式該做哪兩件事

## 延伸閱讀

### 官方文件 / wiki

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **讀哪裡**："Provided buffers" 與 "Multishot" 兩節，是 buffer ring 搭 multishot 的第一手權威說明
  - **學什麼**：為什麼這組合是現代 io_uring 網路程式的標配，以及 `-ENOBUFS` 重提的正確處理
  - **前提**：讀過本章與 Ch 13

### man page

- **[io_uring_register_buf_ring(3)](https://man7.org/linux/man-pages/man3/io_uring_register_buf_ring.3.html)** 與 **[io_uring_buf_ring_add(3)](https://man7.org/linux/man-pages/man3/io_uring_buf_ring_add.3.html)**
  - **讀哪裡**：兩頁的 DESCRIPTION 與 RETURN VALUE；`io_uring_buf_ring_add` 的 `buf_offset` 參數語意
  - **學什麼**：每個 helper 參數的精確定義，以及 `IOU_PBUF_RING_INC` 的 VERSIONS 段（何時加入）

### kernel 原始碼

- **[`io_uring/kbuf.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/kbuf.c)**（Linux 6.18）
  - **讀哪裡**：`io_ring_buffer_select()`、`io_register_pbuf_ring()`，對照本章「底層機制」那段
  - **學什麼**：kernel 端的 head/tail 如何與 app 的 `br->tail` 配對，`-ENOBUFS` 從哪一行出來

### 部落格

- **[Lord of the io_uring — Provided Buffers](https://unixism.net/loti/tutorial/provide_buffers.html)** — Shuveb Hussain
  - **這篇說什麼**：用舊式 `PROVIDE_BUFFERS` 從頭示範 provided buffers 的概念
  - **讀哪裡**：整頁；拿它的舊式寫法對照本章的 buffer ring，看機制演進
  - **前提**：會基本 recv

歸還 buffer 讓 kernel 省下反覆查 buffer 的成本——下一章把這個「先註冊、省掉每次查詢」的思路推廣到 fd 與記憶體本身：registered files 與 registered buffers。

→ [Ch 15 Registered files & buffers：fixed files/buffers](./15-registered-files-buffers.md)
