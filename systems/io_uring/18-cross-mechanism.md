# Ch 18 — 跨機制整合：poll、eventfd、futex、msg_ring

> **目標**：把 io_uring 和它之外的世界接起來。你會學會用 `IORING_OP_POLL_ADD` 把 io_uring 當 epoll 用（含 multishot poll）、用 `io_uring_register_eventfd` 讓完成事件通知既有的 epoll loop、用 `IORING_OP_FUTEX_WAIT/WAKE`（6.7+）非同步地等 futex、用 `IORING_OP_MSG_RING` 跨 ring 傳訊與傳 fd。四個機制都有實測範例。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。poll/eventfd 從 5.x 早期就在；multishot poll 5.13+、`msg_ring` 5.18+、futex op **6.7+**。

## 為什麼需要跨機制整合

到這裡你已經會用 io_uring 做 I/O 了。但真實系統不是只有 io_uring——它要和既有的 event loop、執行緒同步原語、甚至多個 io_uring 實例共存。這一章解決四個「接縫」問題：

1. **我想用 io_uring，但有些 fd 我只想知道「可讀/可寫」，不想真的發 read**——用 `poll`，把 io_uring 當 epoll。
2. **我有一個既有的 epoll 主迴圈，想把 io_uring 的完成「塞進去」當成一個可讀事件**——用 `register_eventfd`。
3. **我的多執行緒程式用 futex 做同步，想讓某個執行緒在 io_uring 裡「順便」等一個 futex，而不是卡在 `futex()` syscall**——用 futex op。
4. **我有多個 ring（例如每個執行緒一個），想從 A ring 通知 B ring、或把一個連線的 fd 交給別的 ring 處理**——用 `msg_ring`。

這四個機制讓 io_uring 從「一個 I/O 介面」變成「能和整個系統對話的樞紐」。

## 機制一：poll——把 io_uring 當 epoll 用

`IORING_OP_POLL_ADD` 對一個 fd 做一次 `poll`：不讀不寫，只等它變成「可讀」或「可寫」，就產一個 CQE，`res` 帶回實際就緒的事件遮罩。這等於 `epoll_ctl(ADD)` + `epoll_wait` 收到一個事件，但統一走 io_uring 的 CQ。

```c
int pfd[2]; pipe(pfd);
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_poll_add(sqe, pfd[0], POLLIN);   // 等 pfd[0] 可讀
io_uring_submit(&ring);

write(pfd[1], "x", 1);                           // 讓 pfd[0] 變可讀
io_uring_wait_cqe(&ring, &cqe);
printf("(1) poll_add: res=0x%x (POLLIN=0x%x)\n", cqe->res, POLLIN);
```

實測輸出：

```
(1) poll_add: res=0x1 (POLLIN=0x1) -> pfd 可讀了
```

`res=0x1` 正是 `POLLIN`。這是**一次性**的 poll：觸發一次就結束，要再等得重新提交。

### multishot poll：一次 arm，多次觸發

一次性 poll 的缺點是每次都要重提交。`io_uring_prep_poll_multishot`（底層 `IORING_POLL_ADD_MULTI`）讓一個 poll **持續監看**：每次 fd 就緒就產一個 CQE，`flags` 帶 `IORING_CQE_F_MORE` 表示「我還活著」，不用重提交。

```c
sqe = io_uring_get_sqe(&ring);
io_uring_prep_poll_multishot(sqe, pfd[0], POLLIN);
io_uring_submit(&ring);
io_uring_wait_cqe(&ring, &cqe);
printf("(2) multishot poll: res=0x%x, F_MORE=%d\n",
       cqe->res, !!(cqe->flags & IORING_CQE_F_MORE));
```

實測輸出：

```
(2) multishot poll: res=0x1, F_MORE=1（還活著，之後再 ready 會再產 CQE）
```

`F_MORE=1` 就是 multishot 的招牌（和 Ch 13 multishot accept、Ch 14 multishot recv 同一個旗標）。這讓 io_uring 能完整取代 epoll：對一堆 fd 各 arm 一個 multishot poll，就是一個 edge/level 可調的事件迴圈，而且和你的 I/O 操作共用同一個 CQ——不用像 epoll + 同步 I/O 那樣在兩套 API 之間切換。

> 多數時候你**不需要**顯式 poll。io_uring 的 read/recv 對可 poll 的 fd 會自動做 internal poll（armed poll，Ch 28 詳解）：你直接發 recv，kernel 內部先 poll、就緒了才真的 recv。顯式 `POLL_ADD` 用在「我只想知道就緒、還不想決定要 read 還是 accept」的場景，或要把 io_uring 當純事件通知器。

## 機制二：eventfd——把完成通知塞進既有 epoll loop

反過來的接縫：你已經有一個 epoll 主迴圈（或 libevent、或某個 framework 的 loop），不想整個改寫成 io_uring，但想讓 io_uring 的完成「通知」那個迴圈。`io_uring_register_eventfd(ring, efd)` 讓 io_uring 每次有新 CQE 時，對 `efd` 這個 eventfd 做一次 signal。

```c
// 1) 建 eventfd，註冊給 io_uring：之後每有 CQE 就 signal 它
int efd = eventfd(0, EFD_NONBLOCK);
io_uring_register_eventfd(&ring, efd);

// 2) 把 efd 放進既有的 epoll loop
int ep = epoll_create1(0);
struct epoll_event ev = { .events = EPOLLIN, .data.fd = efd };
epoll_ctl(ep, EPOLL_CTL_ADD, efd, &ev);

// 3) 丟一個 io_uring 操作，然後回到 epoll 主迴圈
io_uring_prep_read(sqe, zfd, &b, 1, 0);
io_uring_submit(&ring);

// 4) 主迴圈：epoll_wait 醒來看到 efd 可讀 → io_uring 有 CQE → 清 eventfd、收割
int n = epoll_wait(ep, out, 4, 1000);
if (n > 0 && out[0].data.fd == efd) {
    uint64_t cnt; read(efd, &cnt, sizeof(cnt));   // 清 eventfd 計數
    struct io_uring_cqe *cqe;
    io_uring_peek_cqe(&ring, &cqe);
    printf("收割 CQE: user_data=0x%llx, res=%d\n",
           (unsigned long long)io_uring_cqe_get_data64(cqe), cqe->res);
    io_uring_cqe_seen(&ring, cqe);
}
```

實測輸出：

```
epoll_wait returned 1 event(s)
eventfd signalled (count=1) -> io_uring 有完成了
收割 CQE: user_data=0x55, res=1
```

`epoll_wait`（不是 `io_uring_wait_cqe`）醒來，透過 eventfd 得知「io_uring 有完成了」，再去收割 CQE。這讓 io_uring 能**漸進式**導入：主迴圈還是 epoll，但新功能用 io_uring 做，完成透過 eventfd 匯回同一個迴圈。記得收到通知後要 `read` 那個 eventfd 把計數清掉，否則 `EPOLLIN` 會一直觸發。

kernel 端在 `io_uring/eventfd.c` 的 `io_eventfd_signal()` 對註冊的 eventfd 做 `eventfd_signal_mask(..., EPOLL_URING_WAKE)`——那個 `EPOLL_URING_WAKE` 遮罩是為了避免 io_uring 和 epoll 互相喚醒成無限迴圈的特別標記。

注意取捨：每個 CQE 都 signal eventfd 有成本（一次 eventfd 寫 + 一次 epoll 喚醒）。高吞吐下這反而比直接 `io_uring_wait_cqe` 慢。eventfd 的價值在**整合**（接進既有架構），不在效能。`io_uring_register_eventfd_async` 變體只在「需要非同步處理」時才 signal，可減少不必要的喚醒。

## 機制三：futex——在 io_uring 裡非同步地等鎖

`IORING_OP_FUTEX_WAIT/WAKE/WAITV`（**6.7+**）讓 io_uring 操作 futex——Linux 使用者態鎖的底層原語。傳統 `futex(FUTEX_WAIT)` 會**阻塞**呼叫的執行緒；io_uring 版本是**非同步**的：提交一個 futex_wait，執行緒不阻塞，繼續跑別的 io_uring 操作，等 futex 被喚醒時才產一個 CQE。

```c
static uint32_t futex = 0;
// futex_wait：等 futex 的值 == 0 時睡下（被 wake 才醒）
sqe = io_uring_get_sqe(&F);
io_uring_prep_futex_wait(sqe, &futex, 0 /*期望現值*/, FUTEX_BITSET_MATCH_ANY,
                         FUTEX2_SIZE_U32, 0);
io_uring_submit(&F);

futex = 1;
// futex_wake：喚醒 1 個等在這個 futex 上的
sqe = io_uring_get_sqe(&F);
io_uring_prep_futex_wake(sqe, &futex, 1 /*喚醒幾個*/, FUTEX_BITSET_MATCH_ANY,
                         FUTEX2_SIZE_U32, 0);
io_uring_submit(&F);
```

實測輸出：

```
(4) futex_wake: res=1 (ok)
(4) futex_wait: res=0 (ok)
```

`futex_wake` 的 `res=1` 是**喚醒的數量**（喚醒了 1 個 waiter），`futex_wait` 的 `res=0` 表示成功被喚醒。參數裡 `FUTEX2_SIZE_U32`（0x02）宣告這是 32-bit futex，`FUTEX_BITSET_MATCH_ANY`（0xffffffff）是 bitset 遮罩（匹配任何喚醒）。

為什麼有用?一個事件驅動的程式本來最怕「阻塞在鎖上」——一卡住整個迴圈就停擺。io_uring futex 讓你把「等鎖」也變成一個非同步事件：和 I/O 完成一樣從 CQ 回來，迴圈永不阻塞。這對把執行緒池、生產者/消費者佇列搬進 io_uring 架構很關鍵。

`IORING_OP_FUTEX_WAITV`（vectored wait）則能**一次等多個 futex**：傳一個 `struct futex_waitv` 陣列，任何一個被 wake 就產 CQE，`res` 帶回是哪一個觸發的 index。這對應傳統的 `futex_waitv(2)` syscall，但一樣是非阻塞的。典型用途是一個 worker 同時等「有新工作來」和「該關機了」兩個 futex——誰先來就處理誰，而且全程不占住執行緒。把它和 I/O 操作放進同一個 ring，你的事件迴圈就能統一處理「I/O 完成」「鎖被釋放」「關機訊號」三種事件，全從一個 CQ 出來。注意 futex op 共用 kernel 的 futex 子系統，所以它等的 futex 和別的執行緒用傳統 `futex()` 操作的是**同一個**——io_uring 版本和同步版本可以互相喚醒，這正是它能無縫接進既有多執行緒程式的原因。

kernel 端 `io_uring/futex.c` 的 `io_futex_wait` 用 `futex_wait_setup` 把請求掛上 futex 的等待佇列，並註冊 `io_futex_wake_fn` 當喚醒回呼——喚醒不走「叫醒阻塞的執行緒」，而是「產一個 CQE」。

## 機制四：msg_ring——跨 ring 傳訊與傳 fd

`IORING_OP_MSG_RING`（5.18+）讓一個 ring 往**另一個 ring** 投遞東西。兩種模式（存在 `sqe->addr`）：

- `IORING_MSG_DATA`：往目標 ring 投一個 CQE，帶你指定的 `res`（len）和 `user_data`。純訊息傳遞。
- `IORING_MSG_SEND_FD`：把一個（registered）fd 送給目標 ring，塞進它的 fixed file 表。跨 ring 傳連線。

先看 MSG_DATA。ring A 提交一個 msg_ring，CQE 卻出現在 ring B：

```c
struct io_uring A, B;
io_uring_queue_init(8, &A, 0);
io_uring_queue_init(8, &B, 0);

sqe = io_uring_get_sqe(&A);
// 在 A 提交，但 CQE 出現在 B：res=len=42、user_data=0xBEEF
io_uring_prep_msg_ring(sqe, B.ring_fd, 42, 0xBEEF, 0);
io_uring_submit(&A);

io_uring_wait_cqe(&A, &cqe);   // A 收到「msg_ring 操作本身」的完成
printf("(3) ring A: msg_ring 送出, res=%d\n", cqe->res);
io_uring_cqe_seen(&A, cqe);

io_uring_wait_cqe(&B, &cqe);   // B 收到被投遞過來的 CQE
printf("    ring B: 收到跨 ring CQE, res=%d, user_data=0x%llx\n",
       cqe->res, (unsigned long long)io_uring_cqe_get_data64(cqe));
```

實測輸出：

```
(3) ring A: msg_ring 送出, res=0 (送方的完成)
    ring B: 收到跨 ring CQE, res=42, user_data=0xbeef
```

兩個 ring 各收到一個 CQE：**A 收到「我送出了」（res=0），B 收到「有訊息來」（res=42、user_data=0xbeef）**。這是跨執行緒協調的利器——常見架構是「一個 acceptor ring 收連線，用 `MSG_SEND_FD` 把連線 fd 分發給多個 worker ring」，worker 之間用 `MSG_DATA` 傳控制訊息。全程不走 pipe、不加鎖，直接在 kernel 投遞。

kernel 端 `io_uring/msg_ring.c` 的 `__io_msg_ring_data` 把一個 CQE 填進 `target_ctx`，並透過 **task_work**（`io_req_task_work_add_remote`）把完成動作排到目標 ring 擁有者的 task 上執行——這是跨 ring 投遞能安全不加鎖的關鍵：CQE 的實際產生永遠回到擁有該 ring 的 task 上做。

## 對比與取捨：四個機制各解什麼

| 機制 | 方向 | 解決的問題 | 典型場景 |
|---|---|---|---|
| `POLL_ADD` | io_uring 監看外部 fd | 把 io_uring 當 epoll | 只想知道就緒、不想立刻 read |
| `register_eventfd` | 完成通知外部 loop | 接進既有 epoll 架構 | 漸進式導入 io_uring |
| futex op | io_uring 等/喚醒 futex | 非阻塞地參與執行緒同步 | 事件迴圈裡的生產者/消費者 |
| `MSG_RING` | ring 之間互通 | 跨 ring/執行緒協調與傳 fd | acceptor → worker 分發連線 |

一個判斷原則：**poll 和 eventfd 是「io_uring 與 epoll 世界的雙向橋」**（poll 讓 io_uring 吃外部 fd，eventfd 讓外部 loop 吃 io_uring 完成）；**futex 和 msg_ring 是「io_uring 與多執行緒世界的橋」**（futex 接同步原語，msg_ring 接多 ring 架構）。

## 踩雷集錦

1. **以為每個 recv 都要先 poll**：不用。io_uring 對可 poll 的 fd 會自動 internal poll（armed poll）。顯式 `POLL_ADD` 只在「要純事件通知」或「還沒決定要做什麼操作」時用。多此一舉的 poll 反而多一次往返。

2. **multishot poll 收到 `F_MORE=0` 卻以為還活著**：和 multishot recv 一樣，`F_MORE=0` 表示這個 multishot poll 已終止（通常因為出錯或 fd 關閉），要重新 arm。只看 `res`、不看 `F_MORE`，會靜默失去監看。

3. **eventfd 當效能優化用**：每個 CQE signal 一次 eventfd 有成本，高吞吐下比直接 `wait_cqe` 慢。eventfd 的價值是**整合進既有架構**，不是加速。純 io_uring 程式別註冊 eventfd。

4. **futex op 在舊 kernel 上用**：futex op 是 **6.7+**。更早的 kernel 上 `prep_futex_wait` 會讓 CQE 回 `-EINVAL`。用前先 `io_uring_get_probe` 確認 `IORING_OP_FUTEX_WAIT`（51 號）支援（Ch 0 的 probe 程式）。

5. **msg_ring 只等送方 ring 的 CQE，忘了收目標 ring 的**：一個 MSG_DATA 產**兩個** CQE——送方 ring 一個（操作完成）、目標 ring 一個（訊息）。只收割送方 ring，目標 ring 的 CQE 沒人收、CQ 會累積。兩個 ring 都要收割。

## 進階：msg_ring 傳 fd 與 IORING_MSG_RING_CQE_SKIP

`MSG_SEND_FD` 傳的是 **registered fd**（Ch 15 的 direct descriptor），不是普通 fd——它把來源 ring fixed file 表裡的一個槽，複製到目標 ring 的 fixed file 表。搭 `io_uring_prep_msg_ring_fd_alloc` 可以讓目標 ring 自動挑一個空槽（`IORING_FILE_INDEX_ALLOC`）。這是高併發 server「一個 ring 收連線、分發給多 worker ring」的標準做法：`accept_direct` 把新連線收進 acceptor 的 fixed 槽，`MSG_SEND_FD` 把它搬到某個 worker 的 fixed 槽，worker 直接用 `FIXED_FILE` 對它 recv，全程 fd 不回 user 空間。

`IORING_MSG_RING_CQE_SKIP` 旗標讓送方 ring **不產**「我送出了」那個 CQE（只有目標 ring 收到訊息）——當你不在乎送出的確認、只在乎對方收到時，省掉送方那半邊的收割。這和 Ch 16 的 `IOSQE_CQE_SKIP_SUCCESS` 思路一致：不需要的 CQE 就別產。

## 本章重點整理

- `POLL_ADD` 把 io_uring 當 epoll：對 fd 等就緒、`res` 回事件遮罩；`poll_multishot` 持續監看（`F_MORE=1`）。但一般 recv/read 會自動 internal poll，顯式 poll 只在純事件通知時用。
- `register_eventfd` 讓完成通知一個 eventfd，把 io_uring 接進既有 epoll loop——用於漸進式導入，不是效能優化。
- futex op（6.7+）讓 io_uring 非阻塞地 `FUTEX_WAIT/WAKE`：等鎖變成一個 CQ 事件，事件迴圈永不阻塞。`wake` 的 `res` 是喚醒數量。
- `MSG_RING`（5.18+）跨 ring 投遞：`MSG_DATA` 傳訊（送方+目標各一 CQE）、`MSG_SEND_FD` 傳 registered fd。acceptor → worker 分發連線的標準機制，底層靠 task_work 安全投遞。
- 心智地圖：poll/eventfd 橋接 epoll 世界，futex/msg_ring 橋接多執行緒/多 ring 世界。

## 自我檢核

- [ ] 我能說出什麼時候該用顯式 `POLL_ADD`、什麼時候靠 io_uring 的 internal poll 就好
- [ ] 我知道 `register_eventfd` 的價值是整合而非加速，以及它的成本
- [ ] 我能解釋 io_uring futex 和傳統阻塞 `futex()` 的根本差別（非同步、回 CQE）
- [ ] 我知道一個 `MSG_DATA` 產幾個 CQE、分別在哪個 ring
- [ ] 我能描述「acceptor ring 用 MSG_SEND_FD 分發連線給 worker ring」的流程

## 延伸閱讀

### man page

- **[io_uring_prep_poll_add(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_poll_add.3.html)** 與 **[io_uring_prep_poll_multishot(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_poll_multishot.3.html)**
  - **讀哪裡**：poll 事件遮罩、multishot 的 `F_MORE` 語意
  - **學什麼**：顯式 poll 與 internal poll 的分界

- **[io_uring_prep_msg_ring(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_msg_ring.3.html)** 與 **[io_uring_prep_futex_wait(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_futex_wait.3.html)**
  - **讀哪裡**：msg_ring 的兩種模式、futex 的 `FUTEX2_*` 旗標與 VERSIONS 段（6.7）
  - **學什麼**：傳 fd 的 `MSG_SEND_FD`、futex bitset 遮罩的用法

### kernel 原始碼

- **[`io_uring/poll.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/poll.c)**（Linux 6.18）
  - **讀哪裡**：`io_poll_wake`、`io_poll_get_double`（double poll）、multishot re-arm
  - **學什麼**：poll 怎麼掛上 fd 的 waitqueue；Part 3 的 Ch 28 整章解剖，Ch 35 拆 poll race UAF

- **[`io_uring/msg_ring.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/msg_ring.c)** 與 **[`io_uring/futex.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/futex.c)**（Linux 6.18）
  - **讀哪裡**：`__io_msg_ring_data`、`io_futex_wait` 的 `futex_wait_setup` + `io_futex_wake_fn`
  - **學什麼**：跨 ring 投遞為何靠 task_work 才安全、futex 喚醒如何變成產 CQE

### 部落格 / wiki

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **讀哪裡**：msg_ring 分發連線、multishot poll 的段落
  - **學什麼**：多 ring 架構怎麼用 msg_ring 做連線分發，實戰 pattern

- **[Lord of the io_uring — Links and more](https://unixism.net/loti/)** — Shuveb Hussain
  - **讀哪裡**：poll 與 eventfd 相關章節
  - **學什麼**：eventfd 整合進既有 loop 的完整範例
  - **前提**：熟悉 epoll

Part 2 到此把 io_uring 的操作全集走完了。接下來的練習 C 會把 Ch 14 的 buffer ring 和 zero-copy send 兜成一個完整的改寫任務，讓你把這些機制真正用起來。

→ [練習 C：buffer ring + zero-copy 改寫](./practice-c-buffer-ring.md)
