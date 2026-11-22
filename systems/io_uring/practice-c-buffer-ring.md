# 練習 C — buffer ring + zero-copy 改寫

> 目標：把 Ch 14 的 buffer ring 和 zero-copy send 兜成一個完整任務。你會拿一個用「一般 recv + 一般 send」寫的 echo server，改成「buffer ring recv + `send_zc`」，並親手處理 `send_zc` 那個會嚇人的兩段式 CQE。做完這題，Ch 14–15 的機制你就不只是看過，而是用過了。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。buffer ring 需 5.19+、multishot recv 需 6.0+、`send_zc` 需 6.0+。動手前先用 Ch 0 的 probe 程式確認 `IORING_OP_SEND_ZC`（47 號）是 `yes`。

## 背景動機

一個 echo server 的核心迴圈很單純：收到什麼，原樣送回去。但「單純」的實作藏著兩筆你在 Part 2 學過的浪費：

- **recv 端的 buffer 浪費**：一般 recv 要先綁一塊 buffer 才能提交。一個掛著上萬條連線的 server，就算九成連線此刻沒資料，每條的 recv buffer 還是全程佔著記憶體。Ch 14 的 **buffer ring** 把 buffer 的生命週期延到「資料真的到了」才分配——這正是我們要套用的。

- **send 端的拷貝浪費**：一般 send 會把你的資料從 user buffer **複製**一份進 kernel 的 socket buffer，才回傳。資料量大時，這次 memcpy 是純粹的成本。**`send_zc`（zero-copy send）** 讓 kernel 直接「借用」你的 user 頁送出去、不複製；代價是你必須等 kernel 用完（一個 **notification CQE**）才能動那塊 buffer。

把這兩者套進 echo server，你會同時碰到 io_uring 網路程式最常見的兩個真實機制，以及它們各自的「紀律」：buffer 要歸還、zero-copy buffer 要等 NOTIF 才能釋放。這兩條紀律沒守好，程式會洩漏記憶體或讀到垃圾——而編譯器不會警告你。

## 起點：一個「老實但浪費」的 echo server

這是你要改寫的起點。它能跑、也正確，但 recv 預綁 buffer、send 會拷貝。核心迴圈是這個 recv/send 一來一回：

```c
char buf[2048];
for (;;) {
    // recv：提交時就綁死 buf
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_recv(sqe, cfd, buf, sizeof(buf), 0);
    io_uring_submit(&ring);
    io_uring_wait_cqe(&ring, &cqe);
    int n = cqe->res;
    io_uring_cqe_seen(&ring, cqe);
    if (n <= 0) break;                 // 0 = 對端關閉

    // send：kernel 會把 buf 複製一份進 socket buffer
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_send(sqe, cfd, buf, n, 0);
    io_uring_submit(&ring);
    io_uring_wait_cqe(&ring, &cqe);
    io_uring_cqe_seen(&ring, cqe);
}
```

兩個浪費點就在眼前：`buf` 全程綁著（不管有沒有資料）、`prep_send` 會把 `buf` 複製一份進 kernel。完整可跑的起點版在下面，先確認它在你機器上能動，再開始改寫。

<details>
<summary>點開完整起點版（已實測，單連線 echo）</summary>

```c
// prac_c_start.c — 一般 recv + 一般 send 的 echo server（要被改寫掉的起點）
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <liburing.h>

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 0;
    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa = { .sin_family = AF_INET,
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK), .sin_port = htons(port) };
    if (bind(lfd, (void*)&sa, sizeof(sa)) < 0) { perror("bind"); return 1; }
    socklen_t sl = sizeof(sa); getsockname(lfd, (void*)&sa, &sl);
    listen(lfd, 8);
    printf("listening on 127.0.0.1:%d\n", ntohs(sa.sin_port)); fflush(stdout);

    struct io_uring ring;
    io_uring_queue_init(16, &ring, 0);
    struct io_uring_cqe *cqe;
    struct io_uring_sqe *sqe;

    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_accept(sqe, lfd, NULL, NULL, 0);
    io_uring_submit(&ring);
    io_uring_wait_cqe(&ring, &cqe);
    int cfd = cqe->res;
    io_uring_cqe_seen(&ring, cqe);
    printf("accepted fd=%d\n", cfd); fflush(stdout);

    char buf[2048];
    long total = 0;
    for (;;) {
        sqe = io_uring_get_sqe(&ring);
        io_uring_prep_recv(sqe, cfd, buf, sizeof(buf), 0);
        io_uring_submit(&ring);
        io_uring_wait_cqe(&ring, &cqe);
        int n = cqe->res;
        io_uring_cqe_seen(&ring, cqe);
        if (n <= 0) { printf("peer closed (res=%d)\n", n); break; }

        sqe = io_uring_get_sqe(&ring);
        io_uring_prep_send(sqe, cfd, buf, n, 0);
        io_uring_submit(&ring);
        io_uring_wait_cqe(&ring, &cqe);
        total += cqe->res > 0 ? cqe->res : 0;
        io_uring_cqe_seen(&ring, cqe);
    }
    printf("total echoed: %ld bytes\n", total);
    io_uring_queue_exit(&ring);
    close(cfd); close(lfd);
    return 0;
}
```

實測（`printf "abc\nhello\n" | nc -q1 127.0.0.1 <port>`）：

```
listening on 127.0.0.1:38470
accepted fd=5
peer closed (res=0)
total echoed: 10 bytes
```

client 收回 `abc` 與 `hello`，共 10 bytes。這版正確但浪費——接下來把 recv 換 buffer ring、send 換 zero-copy。

</details>

## 任務規格

寫一個 `prac_c.c`，是一個 **單一連線** 的 TCP echo server，滿足：

1. **recv 用 buffer ring**：註冊一個 buffer group，recv 掛 `IOSQE_BUFFER_SELECT`，不預綁 buffer。用 **multishot recv**（`prep_recv_multishot`），一次提交持續收。
2. **echo 用 `send_zc`**：把收到的資料用 `io_uring_prep_send_zc` 送回去，不走會拷貝的 `prep_send`。
3. **正確處理 `send_zc` 的兩段式 CQE**：zero-copy send 成功時會產**兩個** CQE——一個「送出完成」（帶 `IORING_CQE_F_MORE`）、一個「kernel 用完 buffer」（帶 `IORING_CQE_F_NOTIF`）。送出用的 buffer 必須等 **NOTIF** 才能釋放。
4. **歸還 recv buffer**：每個 recv 用掉的 buffer 處理完要 `io_uring_buf_ring_add` + `advance` 還回 ring。
5. **收尾乾淨**：對端關閉後，要把還在飛的 `send_zc` 的 NOTIF 都收完、buffer 都釋放，才結束——不能一看到對端關閉就 break，那會漏收 NOTIF、洩漏記憶體。

程式接受一個 port 參數（`./prac_c <port>`），印出實際監聽的 port，連進來的資料原樣送回，對端關閉即收工並印出總共 echo 了幾 bytes。

## 期望行為

用 `nc` 當 client 送幾行進去，應該原樣收回，server 回報 echo 的總 bytes：

```
listening on 127.0.0.1:38463
accepted fd=5
peer closed (res=0)
total echoed: 34 bytes
```

client 端（`nc`）收到的，和送出的一模一樣：

```
hello zero-copy
second line
third
```

`34` 正是三行的總長（`"hello zero-copy\n"`=16 + `"second line\n"`=12 + `"third\n"`=6）。關鍵是程式**乾淨結束**——沒有卡住等一個永遠不來的 CQE，也沒有洩漏送出 buffer。

## 卡住時的提示

- **recv 一直回 `-ENOBUFS`**：buffer 沒歸還。每個 recv 處理完，`bid` 對應的 buffer 要 `buf_ring_add` + `advance` 還回去。multishot recv 收到 `-ENOBUFS` 還代表它**終止了**（`F_MORE=0`），要補 buffer **並重新 arm**。

- **程式結束時卡住不動**：幾乎一定是在等一個不會來的 CQE。回想 `send_zc` 的規則：**只有當送出完成的 CQE 帶 `F_MORE` 時，才會有後續的 NOTIF**。如果 send 失敗或退化成拷貝，送出 CQE **不帶 `F_MORE`**，NOTIF 不會來——這時 buffer 要當場釋放。漏掉這個分支，你的 in-flight 計數永遠歸不了零。

- **client 收到的資料是亂的/空的**：你可能在 NOTIF 回來前就 `free`（或重用）了送出 buffer，kernel 送出去的是已經被你動過的記憶體。zero-copy 的鐵律：**buffer 的釋放要等 NOTIF**。

- **送出完成 CQE 和 NOTIF 怎麼分辨**：兩者 `user_data` 相同（kernel 故意讓 NOTIF 沿用 send 的 user_data，見下方底層說明）。靠 `cqe->flags`：帶 `IORING_CQE_F_NOTIF` 的是 NOTIF，否則是送出完成。

- **怎麼在 CQE 裡認出「這是哪個操作」**：用 `user_data` 的高位元當 tag（accept / recv / send 各一個值），低位元可以夾帶指標（例如送出 buffer 的位址，NOTIF 時據此 `free`）。

## 實作步驟

**Step 1 — 先讓一般版 echo 跑起來。** 用上面「起點版」的一般 recv + send 寫一個能動的單連線 echo，確認 `nc` 連得上、收得到回聲。先有一個正確的基準，再談優化。

**Step 2 — 把 recv 換成 buffer ring。** 參照 Ch 14：`io_uring_setup_buf_ring` 建 group、`buf_ring_add`×N + `advance` 填 buffer；recv 改成 `prep_recv_multishot` + `IOSQE_BUFFER_SELECT` + `buf_group`。收 CQE 時從 `cqe->flags >> IORING_CQE_BUFFER_SHIFT` 取 `bid`，資料在 `bufs[bid]`。處理完記得歸還 buffer。先只改 recv，send 仍用一般 `prep_send`，確認這一半對了。

**Step 3 — 把 send 換成 `send_zc`。** `io_uring_prep_send_zc(sqe, fd, buf, len, 0, 0)`。這裡有個設計抉擇：送出用的 buffer 不能是「馬上要歸還給 recv ring 的那塊」，因為 zero-copy 期間 kernel 還在用它。最簡單正確的做法是**把收到的資料複製到一塊 heap buffer**（`malloc`），`send_zc` 那塊，recv buffer 立刻歸還 ring；heap buffer 等 NOTIF 再 `free`。（進階挑戰會談怎麼連這次 copy 都省掉。）

**Step 4 — 處理兩段式 CQE 與 in-flight 計數。** 送出 `send_zc` 時 `inflight_sends++`。收到 tag 為 send 的 CQE：
- 帶 `F_NOTIF`：`free` 那塊 heap buffer、`inflight_sends--`。
- 不帶 `F_NOTIF`（送出完成）：累計 echo bytes；**若也不帶 `F_MORE`**（不會有 NOTIF），當場 `free` 並 `inflight_sends--`。

**Step 5 — 乾淨收尾。** recv 回 0（對端關閉）時設 `peer_closed`，但**不要立刻 break**。主迴圈條件改成「對端關閉 **且** `inflight_sends == 0`」才結束，中間繼續收割把 NOTIF 收完。

## 完整參考解答

**自己先寫到能跑再看。** 這題的價值全在你親手踩過 `-ENOBUFS` 和「結束時卡住」這兩個坑。偷看會讓你以為自己懂了，其實沒有。

<details>
<summary>點開參考實作（WSL kernel 6.18 + liburing 2.16 實測跑過）</summary>

```c
// prac_c.c — buffer ring recv + send_zc 的單連線 echo server
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <liburing.h>

#define NR_BUFS   64
#define BUF_SIZE  2048
#define BGID      1

// user_data 用高位元當 tag，區分不同操作；低位元可夾帶指標
#define TAG_ACCEPT  (1ULL << 60)
#define TAG_RECV    (2ULL << 60)
#define TAG_SEND    (3ULL << 60)
#define TAG_MASK    (0xFULL << 60)

static char bufs[NR_BUFS][BUF_SIZE];
static struct io_uring_buf_ring *br;
static int br_mask;

static void buf_return(int bid) {
    io_uring_buf_ring_add(br, bufs[bid], BUF_SIZE, bid, br_mask, 0);
    io_uring_buf_ring_advance(br, 1);
}

static void arm_recv(struct io_uring *ring, int fd) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    io_uring_prep_recv_multishot(sqe, fd, NULL, 0, 0);
    sqe->flags |= IOSQE_BUFFER_SELECT;
    sqe->buf_group = BGID;
    io_uring_sqe_set_data64(sqe, TAG_RECV);
}

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 0;

    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa = { .sin_family = AF_INET,
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK), .sin_port = htons(port) };
    if (bind(lfd, (void*)&sa, sizeof(sa)) < 0) { perror("bind"); return 1; }
    socklen_t slen = sizeof(sa); getsockname(lfd, (void*)&sa, &slen);
    listen(lfd, 8);
    printf("listening on 127.0.0.1:%d\n", ntohs(sa.sin_port)); fflush(stdout);

    struct io_uring ring;
    io_uring_queue_init(256, &ring, 0);

    // buffer ring
    int err = 0;
    br = io_uring_setup_buf_ring(&ring, NR_BUFS, BGID, 0, &err);
    if (!br) { fprintf(stderr, "setup_buf_ring: %s\n", strerror(-err)); return 1; }
    br_mask = io_uring_buf_ring_mask(NR_BUFS);
    for (int i = 0; i < NR_BUFS; i++)
        io_uring_buf_ring_add(br, bufs[i], BUF_SIZE, i, br_mask, i);
    io_uring_buf_ring_advance(br, NR_BUFS);

    // 單一 accept
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_accept(sqe, lfd, NULL, NULL, 0);
    io_uring_sqe_set_data64(sqe, TAG_ACCEPT);
    io_uring_submit(&ring);

    int cfd = -1;
    long total_echo = 0;
    int peer_closed = 0, inflight_sends = 0;
    // 跑到「對端關閉」且「所有 send_zc 的 NOTIF 都回來了」才收工
    while (!(peer_closed && inflight_sends == 0)) {
        struct io_uring_cqe *cqe;
        if (io_uring_wait_cqe(&ring, &cqe) < 0) break;
        __u64 ud = io_uring_cqe_get_data64(cqe);
        __u64 tag = ud & TAG_MASK;

        if (tag == TAG_ACCEPT) {
            cfd = cqe->res;
            printf("accepted fd=%d\n", cfd); fflush(stdout);
            arm_recv(&ring, cfd);
            io_uring_submit(&ring);

        } else if (tag == TAG_RECV) {
            int res = cqe->res;
            if (res <= 0) {
                // 0 = 對端關閉；-ENOBUFS = buffer 用光（補 buffer 並重 arm）
                if (res == -ENOBUFS) { arm_recv(&ring, cfd); io_uring_submit(&ring); }
                else { printf("peer closed (res=%d)\n", res); peer_closed = 1; }
                io_uring_cqe_seen(&ring, cqe);
                continue;
            }
            int bid = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
            // 複製到 heap，用 send_zc 送出；recv buffer 立刻歸還
            char *out = malloc(res);
            memcpy(out, bufs[bid], res);
            buf_return(bid);

            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_send_zc(sqe, cfd, out, res, 0, 0);
            // user_data 夾帶 out 指標（低 60 位），NOTIF 時據此 free
            io_uring_sqe_set_data64(sqe, TAG_SEND | ((__u64)(uintptr_t)out & ~TAG_MASK));
            inflight_sends++;

            // multishot recv 若已終止（F_MORE=0）且對端還在，重新 arm
            if (!(cqe->flags & IORING_CQE_F_MORE) && !peer_closed)
                arm_recv(&ring, cfd);
            io_uring_submit(&ring);

        } else if (tag == TAG_SEND) {
            // send_zc 的 CQE 規則：
            //   送出完成帶 F_MORE → 稍後會有 F_NOTIF，buffer 要等 NOTIF 才能 free
            //   送出完成「不帶」F_MORE → 不會有 NOTIF，buffer 當場 free
            char *out = (char *)(uintptr_t)(ud & ~TAG_MASK);
            if (cqe->flags & IORING_CQE_F_NOTIF) {
                free(out);
                inflight_sends--;
            } else {
                if (cqe->res > 0) total_echo += cqe->res;
                if (!(cqe->flags & IORING_CQE_F_MORE)) {   // 不會有 NOTIF
                    free(out);
                    inflight_sends--;
                }
            }
        }
        io_uring_cqe_seen(&ring, cqe);
    }

    printf("total echoed: %ld bytes\n", total_echo);
    io_uring_free_buf_ring(&ring, br, NR_BUFS, BGID);
    io_uring_queue_exit(&ring);
    if (cfd >= 0) close(cfd);
    close(lfd);
    return 0;
}
```

編譯與測試（server + client 在同一個 shell 背景跑）：

```bash
iou-cc prac_c.c -o prac_c
./prac_c 38463 & SP=$!
sleep 0.5
printf "hello zero-copy\nsecond line\nthird\n" | nc -q1 127.0.0.1 38463
wait $SP
```

實測輸出（server stdout 與 client 回聲交錯）：

```
listening on 127.0.0.1:38463
accepted fd=5
hello zero-copy      <- 這三行是 nc client 收到的回聲
second line
third
peer closed (res=0)
total echoed: 34 bytes
```

server 乾淨退出（`inflight_sends` 歸零、所有 NOTIF 收完、送出 buffer 全釋放），`total echoed: 34` = 三行總長。

</details>

## 測試用例

**1. 基本回聲**：`printf "abc\n" | nc -q1 127.0.0.1 <port>`，應收回 `abc`、server 報 `total echoed: 4 bytes`。

**2. 多段資料**：連續送多行（上面的三行範例）。確認全部原樣收回、總 bytes 對。

**3. 大量資料（壓 buffer ring）**：
```bash
head -c 100000 /dev/urandom | base64 | nc -q2 127.0.0.1 <port> | wc -c
```
送十萬 bytes 等級的資料，逼 recv 反覆分配/歸還 buffer、`send_zc` 大量觸發 NOTIF。確認 `wc -c` 收回的位元組數和送出的一致，且 server 不卡住、不 `-ENOBUFS`。

**4. 立刻關閉**：`nc -q0` 送完立刻關。確認 server 在對端關閉後仍把 in-flight 的 NOTIF 收完才退出（不漏 free、不卡住）。

**5. 故意製造 `-ENOBUFS`（診斷用）**：把 `NR_BUFS` 改成 2、`BUF_SIZE` 改成 16，灌大量資料。你會在 recv 看到 `-ENOBUFS`——觀察你的「補 buffer + 重 arm」有沒有讓它恢復，而不是永久失聯。

## 底層：為什麼 NOTIF 用同一個 user_data

`send_zc` 的兩段式設計在 `io_uring/net.c` 與 `notif.c`。`io_send_zc_prep` 會為這個 send 配一個 notification 請求：

```c
notif = zc->notif = io_alloc_notif(ctx);
notif->cqe.user_data = req->cqe.user_data;   // NOTIF 沿用 send 的 user_data
```

所以送出完成 CQE 和 NOTIF CQE **帶相同的 `user_data`**——這是刻意的，讓你用同一個 key 追蹤「這塊 buffer 的整個生命週期」。區分兩者靠 `flags`：送出完成帶 `IORING_CQE_F_MORE`（表示「還有 NOTIF 要來」），NOTIF 帶 `IORING_CQE_F_NOTIF`。

這也解釋了「送失敗就沒 NOTIF」：若 zero-copy 這條路沒走成（例如 kernel 當下選擇拷貝、或 send 直接錯誤），就不會有 notif 請求被 flush，送出完成 CQE 也就**不帶 `F_MORE`**。你的程式必須把「不帶 `F_MORE`」當成「buffer 現在就能釋放」的訊號——這正是參考解答裡那個容易漏掉的分支，也是「結束時卡住」的根因。

## 延伸挑戰

1. **真正的 zero-copy，連 recv→send 的 memcpy 都省掉**：參考解答為了簡單，把 recv 到的資料複製進 heap 再 `send_zc`。試試直接 `send_zc` **recv buffer 本身**，並把「歸還該 recv buffer 給 ring」延後到 NOTIF——這樣全程零拷貝，但你得用一組對映表追蹤「哪個 bid 還被哪個 in-flight send 佔用」，NOTIF 回來才歸還。這會逼你真正理解 provided buffer 與 zero-copy 的生命週期如何交織。

2. **多連線**：把單一 accept 換成 Ch 13 的 **multishot accept**，用一張 `fd → 狀態` 表管理多條連線，各自 arm multishot recv。buffer ring 讓所有連線**共用一個 buffer group**——這正是 provided buffers 的省記憶體威力所在。

3. **fixed file + registered ring fd**：套用 Ch 15，用 `accept_direct` 把連線收進 fixed file 槽、recv/send 走 `IOSQE_FIXED_FILE`，再 `register_ring_fd`。量一下吞吐差多少。

4. **壓力下的正確性**：用挑戰 1 的零拷貝版，跑測試用例 3 的大量資料並比對 md5。零拷貝最容易在「buffer 還被 kernel 用著就被重用」時出錯——如果 md5 對得上，你的生命週期管理就真的對了。

## 自我檢核

完成後你應該能回答：

- [ ] recv 掛 `IOSQE_BUFFER_SELECT` 時，`addr`/`len` 該填什麼?完成後 `bid` 從哪裡取?
- [ ] 為什麼 `send_zc` 要等 NOTIF 才能釋放 buffer，而一般 `send` 不用?
- [ ] 送出完成 CQE 和 NOTIF CQE 怎麼分辨?為什麼它們 `user_data` 相同?
- [ ] 「送出完成但不帶 `F_MORE`」代表什麼?漏處理這個分支會怎樣?
- [ ] multishot recv 收到 `-ENOBUFS` 之後，除了補 buffer，還必須做什麼?
- [ ] 為什麼對端關閉後不能立刻 break，而要等 `inflight_sends == 0`?

做完這題，你已經能用 io_uring 寫出一個記憶體效率與拷貝效率都到位的網路 server——這正是 io_uring 相對 epoll 的真正價值所在。Part 2 的操作全集到此結束，接下來 Part 3 翻開 kernel 的蓋子，看這些機制在 `io_uring/` 原始碼裡到底怎麼實作。

→ [Ch 19 原始碼地圖：io_uring/ 目錄結構](./19-source-map.md)
