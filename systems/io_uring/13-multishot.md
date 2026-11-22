# Ch 13 — multishot 與 sendmsg/recvmsg

> **目標**：學會 io_uring 省 SQE 的招牌機制 multishot——一個 SQE 持續產生多個 CQE，靠 `IORING_CQE_F_MORE` 判斷「這個請求還活著」。你會實測 multishot accept，搞懂 F_MORE 清除代表要重新 arm、multishot recv 為何需要 provided buffers，以及 `sendmsg`/`recvmsg` 怎麼做 scatter-gather 與控制訊息。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 Ch 0 的 `iou-cc`。multishot accept 需 5.19+、multishot recv 需 6.0+。輸出皆 WSL2 實測。

## 為什麼需要 multishot

Ch 12 的模型有個隱藏成本：**每個操作只產生一個 CQE，用完就沒了，要再做得重新提交一個 SQE**。想想一個 accept 迴圈：

```
 one-shot accept：每接一條連線就得再 prep 一次
   prep_accept + submit ──► CQE(conn1) ──► 再 prep_accept + submit ──► CQE(conn2) ──► ...
                            ↑ 每條連線都要一個新 SQE、一次提交
```

高併發 server 每秒接幾萬條連線，就是每秒幾萬個 accept SQE、幾萬次重新武裝。每次重新提交都要：拿 SQE、填欄位、推 tail。這些都是可以省掉的開銷。

multishot 的點子：**提交一個「持續性」的操作，它一直活著，每有一次事件就吐一個 CQE，不用你重新武裝。**

```
 multishot accept：提交一次，持續吐 CQE
   prep_multishot_accept + submit ──┬─► CQE(conn1, F_MORE=1)
                                    ├─► CQE(conn2, F_MORE=1)
                                    ├─► CQE(conn3, F_MORE=1)
                                    └─► ...（SQE 一直活著）
```

一個 SQE 服務無數連線。這不只省 SQE，也省了「重新武裝」之間的空窗——one-shot 下，從你收到 conn1 的 CQE 到你重新提交 accept 之間，新連線只能排在 backlog 等；multishot 下 accept 一直武裝著。

## 先建立直覺：F_MORE 是「我還會再來」的旗子

multishot 的全部機制濃縮成一個 CQE flag：`IORING_CQE_F_MORE`（值 `1 << 1`）。

```
 每個 multishot CQE 都帶一個 F_MORE 旗子：
   CQE.flags & IORING_CQE_F_MORE == 1  ──► 「這個 SQE 還活著，之後還會給你 CQE」
   CQE.flags & IORING_CQE_F_MORE == 0  ──► 「這是最後一個，SQE 已退場，要再做請重新 arm」
```

規則就這麼簡單：**每收一個 multishot CQE，檢查 F_MORE。還在（=1），什麼都不用做，繼續等下一個；清除了（=0），代表這個 multishot 結束了（出錯、資源不足、或被取消），你得重新提交一個 SQE 才能繼續。**

所有 multishot 操作（accept、recv、poll）共用這個協定。F_MORE 是你和 kernel 之間「這個持續性請求還在不在」的唯一溝通管道。忘了檢查 F_MORE，就會在 multishot 悄悄退場後再也收不到事件、卻不知道為什麼。

## 核心內容：multishot accept 實測

用 `io_uring_prep_multishot_accept` 提交**一個** SQE，然後連三條連線，看它吐三個 CQE。完整檔見 `src/ch13_multishot.c`。

```c
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_multishot_accept(sqe, lfd, NULL, NULL, 0);
sqe->user_data = 42;                          // 整組連線共用這個 user_data
io_uring_submit(&ring);                        // 只提交這一次

for (int i = 0; i < 3; i++) {
    int c = socket(AF_INET, SOCK_STREAM, 0);
    connect(c, (struct sockaddr*)&sa, sizeof(sa));   // 發起第 i 條連線
    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);
    int more = (cqe->flags & IORING_CQE_F_MORE) != 0;
    printf("CQE#%d: user_data=%llu conn_fd=%d F_MORE=%d\n",
           i + 1, (unsigned long long)cqe->user_data, cqe->res, more);
    io_uring_cqe_seen(&ring, cqe);
}
```

實測：

```
=== Part A: multishot accept（只送一個 SQE，user_data=42）===
  CQE#1: user_data=42 conn_fd=7  F_MORE=1 (SQE 還活著，會繼續產生 CQE)
  CQE#2: user_data=42 conn_fd=9  F_MORE=1 (SQE 還活著，會繼續產生 CQE)
  CQE#3: user_data=42 conn_fd=11 F_MORE=1 (SQE 還活著，會繼續產生 CQE)
```

三條連線，三個 CQE，**全部 `user_data=42`**（同一個 SQE 產生的），**全部 `F_MORE=1`**（accept 還活著）。`res` 是每條新連線的 fd（7、9、11）。注意我只呼叫了一次 `io_uring_prep_multishot_accept` 和一次 `io_uring_submit`——之後純粹收割，沒有再武裝。這就是 multishot 省 SQE 的實感。

`io_uring_prep_multishot_accept(sqe, fd, addr, addrlen, flags)` 和 one-shot accept 唯一的差別，是它在 SQE 的 `ioprio` 欄位設了 `IORING_ACCEPT_MULTISHOT`（值 `1 << 0`）這個旗標——liburing 幫你設好了。

### 什麼時候 F_MORE 會清除、要重新 arm

multishot accept 的 F_MORE 清除（=0）代表這個持續請求**退場了**，常見原因：

- **accept 出錯**（`res < 0`），例如 fd 耗盡（`-EMFILE`）、listener 被關。
- **CQ overflow**：CQ ring 滿了，multishot 可能被迫終止以免無限堆積（Ch 8 的 overflow 機制）。
- **被主動取消**（Ch 17 的 cancel）。

所以 production 的 accept 迴圈長這樣：

```c
if (cqe->user_data == ACCEPT_UD) {
    if (cqe->res >= 0) { /* 處理新連線 cqe->res */ }
    if (!(cqe->flags & IORING_CQE_F_MORE))
        arm_accept(lfd);                      // F_MORE 清除 => 重新武裝 multishot accept
}
```

**永遠檢查 F_MORE 並在它清除時重新 arm**，否則某次瞬時錯誤（如暫時 `-EMFILE`）就讓你的 server 永久停止接受連線。練習 B 的 echo server 會把這個模式寫完整。

### 用 multishot 要把 CQ 開大一點

multishot 有個容易忽略的配套：它會**突發地**產生大量 CQE。一個 multishot accept 在連線潮湧入時可能一口氣吐幾十個 CQE，加上每條連線的 recv/send CQE，CQ ring 很容易被塞滿。而 CQ 一旦 overflow（Ch 8），multishot 請求可能**被迫終止**（F_MORE 清除），運氣不好還會掉事件。

對策：建 ring 時把 CQ 開大。預設 CQ 深度是 SQ 的兩倍，但用 multishot 時建議明確放大：

```c
struct io_uring_params p = {0};
p.flags = IORING_SETUP_CQSIZE;
p.cq_entries = 4096;                           // 明確指定較大的 CQ 深度
io_uring_queue_init_params(256, &ring, &p);    // SQ 256，CQ 4096
```

`IORING_SETUP_CQSIZE` 讓你把 CQ 和 SQ 的深度解耦——SQ 不用很深（你不會一次排幾千個 SQE），但 CQ 要能吸收 multishot 的突發完成。這是 production multishot server 的標準配置，練習 B 的延伸挑戰會再碰到。

## 核心內容：multishot recv 需要 provided buffers

很自然會想：accept 能 multishot，recv 是不是也能「提交一次、持續收資料」？能，`io_uring_prep_recv_multishot`，但有個硬性前提——**它必須搭配 provided buffers（Ch 14 的 buffer ring），不能用普通 buffer**。

為什麼？想想 one-shot recv：你給它一塊 buffer，收一次填一次。但 multishot recv 要收**無數次**，每次收的資料往哪放？如果共用同一塊 buffer，第二次收的會蓋掉第一次還沒處理的。所以 multishot recv 要求你準備一**池** buffer（provided buffers），kernel 每收一筆就從池裡挑一塊填，並在 CQE 裡用 `IORING_CQE_F_BUFFER` 告訴你用了哪塊。沒有這個池，kernel 無處放資料，直接拒絕。

實測：拿普通 buffer 丟 multishot recv，看它回 `-EINVAL`：

```c
char rb[64];
io_uring_prep_recv_multishot(sqe, sconn, rb, sizeof(rb), 0);
io_uring_submit(&ring);
io_uring_wait_cqe(&ring, &cqe);
```

```
=== Part C: multishot recv 用普通 buffer => 預期 -EINVAL ===
  multishot recv res=-22 (Invalid argument) —— 需搭配 Ch 14 的 buffer ring
```

`res=-22`（`-EINVAL`），正如預期。這不是 bug，是設計：原始碼 `io_uring/net.c` 的 recv prep 明文檢查 `if (sr->flags & IORING_RECV_MULTISHOT) { if (!(req->flags & REQ_F_BUFFER_SELECT)) return -EINVAL; }`——沒有 buffer select（provided buffers）就拒絕 multishot recv。

> 這也是為什麼**練習 B 的 echo server 用 multishot accept + 一次性（one-shot、每次重新武裝）recv**，而不是 multishot recv——因為 provided buffers 是下一章（Ch 14）才學。等你讀完 Ch 14 和練習 C，就能把 recv 也升級成 multishot + buffer ring，那才是最省 SQE 的終極形態。

## 核心內容：sendmsg / recvmsg——scatter-gather 與控制訊息

`send`/`recv` 只能收發一塊連續 buffer。`sendmsg`/`recvmsg` 用 `struct msghdr` 包起來，多了三種能力：**scatter-gather**（多塊 buffer）、**位址**（`msg_name`，UDP 用）、**控制訊息**（`msg_control`，傳 fd、收 timestamp 等）。

### scatter-gather 實測

client 用 `sendmsg` 把兩塊 buffer（header + payload）一次 gather 送出，server 用 `recvmsg` scatter 到兩塊 buffer：

```c
// client：gather 送出
struct iovec siov[2] = {
    { .iov_base = (void*)"HEADER:",      .iov_len = 7 },
    { .iov_base = (void*)"payload-data", .iov_len = 12 },
};
struct msghdr smsg = {0};
smsg.msg_iov = siov; smsg.msg_iovlen = 2;
io_uring_prep_sendmsg(sqe, cfd, &smsg, 0);

// server：scatter 收進
char h[7], p[64];
struct iovec riov[2] = {
    { .iov_base = h, .iov_len = 7 },
    { .iov_base = p, .iov_len = sizeof(p) - 1 },
};
struct msghdr rmsg = {0};
rmsg.msg_iov = riov; rmsg.msg_iovlen = 2;
io_uring_prep_recvmsg(sqe, sconn, &rmsg, 0);
```

```
=== Part B: sendmsg/recvmsg（scatter-gather + msghdr）===
  sendmsg res=19
  recvmsg res=19  header="HEADER:"  payload="payload-data"
```

`res=19` = 7 + 12。發送端把兩塊合併成一個 19-byte 的 stream 送出，接收端依 iovec 把前 7 bytes 填進 `h`、其餘填進 `p`。這就是 `readv`/`writev`（Ch 10）在 socket 上的版本：header 和 body 在不同記憶體，不必先拼成一塊再送。

`io_uring_prep_sendmsg(sqe, fd, msg, flags)` / `io_uring_prep_recvmsg(sqe, fd, msg, flags)` 對應 `sendmsg(2)`/`recvmsg(2)`。

### 控制訊息（msg_control）

`msghdr.msg_control` 是傳「帶外 metadata」的通道，用 `cmsg` 巨集組裝：

- **傳遞 fd**：`SCM_RIGHTS` 透過 Unix domain socket 把一個開啟的 fd 傳給另一個行程。
- **收取時間戳**：`SO_TIMESTAMPING` 讓 kernel 在 `msg_control` 回報封包的精確收/送時間。
- **UDP 的目的位址**：`IP_PKTINFO` 知道封包從哪個本機位址進來。

io_uring 的 sendmsg/recvmsg 完整支援這些——`msg_control` 怎麼填，和同步 `sendmsg` 一模一樣，只是改從 ring 提交。**buffer 所有權規則照舊且更嚴格**：`struct msghdr` 本身、它指向的 `iovec` 陣列、每個 `iov_base`、以及 `msg_control`，從提交到 CQE 回來之前**全部**不能改、不能釋放。recvmsg 尤其要注意：`msghdr` 不能是 stack 上提交後就離開 scope 的變數。

> **本段的控制訊息部分未實測，為機制說明**（scatter-gather 與基本 sendmsg/recvmsg 已實測如上）。`SCM_RIGHTS`/timestamp 的完整範例牽涉 Unix socket 與 cmsg 組裝，屬於進階主題；要用時對照 `cmsg(3)` man page，io_uring 端的差異只有「用 prep_sendmsg/recvmsg 提交」。

## 進階：multishot 家族與 bundle

multishot 不只 accept 和 recv，它是一整套「提交一次、持續回報」的機制家族：

- **multishot poll**（`IORING_POLL_ADD_MULTI`，Ch 18、Ch 28）：持續回報一個 fd 的 readiness 事件，像一個「長效版 epoll」掛在 ring 上。這是少數 io_uring 真的回報「可讀/可寫狀態」而非「操作結果」的場景。
- **multishot recv / recvmsg**：持續收，配 provided buffers（本章 Part C）。
- **multishot accept**：持續接連線（本章 Part A）。

另一個相關的省 SQE 機制是 **bundle**（`IORING_RECVSEND_BUNDLE`，6.10+）：配合 provided buffers，一次 send/recv 操作能橫跨**多塊** buffer，把一池 buffer 的資料一口氣收發，進一步減少操作數。`io_uring_prep_send_bundle` 就是幹這個。bundle 和 multishot 可以疊加——multishot recv + bundle 是目前 io_uring 收資料最省 syscall 的組合。這些都建立在 Ch 14 的 buffer ring 上，這裡先讓你知道 multishot 不是孤例，而是一個設計哲學：**把「重複武裝」的成本從每次操作攤平成一次**。

> **zero-copy send 的 F_MORE**：Ch 13 不展開，但 `io_uring_prep_send_zc`（zero-copy send，練習 C/Ch 41）也用 `IORING_CQE_F_MORE`，不過語意不同——它的第一個 CQE 表示「請求已受理」、第二個（notification）才表示「buffer 可以複用了」。同一個 flag、不同脈絡，別搞混：multishot 的 F_MORE 是「還會再來」，zc 的 F_MORE 是「還有一個 notification 要來」。

## 底層機制：multishot 怎麼「持續」

對照 6.18 的 `io_uring/net.c`。multishot 的核心在於：完成一次後，kernel **不把請求標記為結束**，而是重新把它掛回 poll 等待，下次事件再觸發。關鍵在 multishot 的重跑迴圈與 `MULTISHOT_MAX_RETRY`：

```c
// io_uring/net.c
#define MULTISHOT_MAX_RETRY 32
...
if ((req->flags & REQ_F_APOLL_MULTISHOT) && !mshot_finished && ...) {
    if (sr->nr_multishot_loops++ < MULTISHOT_MAX_RETRY && ...)
        /* 繼續這一輪 multishot */
    ...
}
```

每次 multishot accept/recv 完成一筆，kernel 填一個帶 `F_MORE` 的 CQE，然後決定「繼續武裝」還是「收工」。繼續武裝時 SQE 留在 kernel；要收工時（錯誤、CQ 快滿、超過 retry 上限）填一個 `F_MORE=0` 的 CQE 讓你知道該重新 arm。`MULTISHOT_MAX_RETRY = 32` 是一個公平性護欄：避免一個 multishot 請求在 busy socket 上無限迴圈獨佔 CPU，跑滿 32 輪就讓出，之後再被排程喚醒。

這也解釋了 multishot 的 CQE 為何**可能亂序或與其他 CQE 交錯**：它們是事件驅動、分批產生的，不保證和你的 `connect` 順序一一對齊（本章範例因為單執行緒、一次連一條才看起來整齊）。production 一律靠 `user_data` + `res` 認領，不靠順序。

## 對比與取捨

| 面向 | one-shot（Ch 12） | multishot（本章） |
|---|---|---|
| 提交次數 | 每次事件都要重新 prep + submit | 提交一次，持續吐 CQE |
| 省什麼 | — | 省 SQE、省重新武裝的空窗 |
| 怎麼知道還活著 | 一個 CQE 就結束 | 看 `IORING_CQE_F_MORE` |
| 退場後 | 本來就要重 prep | F_MORE 清除時才重新 arm |
| recv 的 buffer | 自備單一 buffer | **必須** provided buffers（Ch 14） |
| 適用 | 低頻、一次性操作 | 高併發 accept、持續 recv |
| send/recv 多塊記憶體 | `send`/`recv` 單塊 | `sendmsg`/`recvmsg` scatter-gather |

## 踩雷集錦

1. **收 multishot CQE 不檢查 F_MORE**。錯誤直覺：「multishot 永遠會一直來」。正確認識：F_MORE 清除代表它退場了，不重新 arm 就永久失聯。某次瞬時 `-EMFILE` 就能讓 server 停止接客。
2. **multishot recv 丟普通 buffer**。錯誤直覺：「recv 當然給它一塊 buffer」。正確認識：multishot recv 必須搭 provided buffers（buffer ring），否則 `-EINVAL`。要先學 Ch 14。
3. **用 connect/事件順序去對應 multishot CQE**。錯誤直覺：「第一條連線就對應第一個 CQE」。正確認識：multishot CQE 事件驅動、可交錯，要靠 `user_data` + `res` 認領。
4. **recvmsg 的 msghdr 放 stack 提交後就 return**。錯誤直覺：「msghdr 填完提交就沒事了」。正確認識：`msghdr`、`iovec` 陣列、每個 `iov_base`、`msg_control` 從提交到 CQE 回來全程歸 kernel，不能是提交後離開 scope 的 stack 變數。
5. **以為 multishot 就不會被打斷**。錯誤直覺：「提交一次就一勞永逸」。正確認識：CQ overflow、retry 上限、資源錯誤都會讓它收工（F_MORE=0）；它是「省掉常態重武裝」，不是「永不中斷」。
6. **用預設 CQ 深度跑 multishot，連線一多就掉事件**。錯誤直覺：「CQ 夠用吧」。正確認識：multishot 會突發產生大量 CQE，CQ overflow 會逼它終止。用 `IORING_SETUP_CQSIZE` 把 CQ 開大（進階一節）。

## 本章重點整理

- **multishot = 一個 SQE 持續產生多個 CQE**，靠 `IORING_CQE_F_MORE`（`1<<1`）判斷請求是否還活著；F_MORE 清除就要重新 arm。
- multishot accept（`io_uring_prep_multishot_accept`，5.19+）提交一次就能持續接連線，所有 CQE 共用同一個 `user_data`，`res` 是每條新連線的 fd。
- **multishot recv（6.0+）必須搭 provided buffers**，普通 buffer 回 `-EINVAL`；因為持續收資料需要一池 buffer 輪流用。這是練習 B 用 one-shot recv、留到練習 C 才升級的原因。
- `sendmsg`/`recvmsg` 用 `struct msghdr` 做 scatter-gather（多塊 buffer）、帶位址、傳控制訊息（`SCM_RIGHTS`、timestamp）；buffer 所有權規則更嚴，整組 msghdr 相關記憶體全程歸 kernel。
- 底層上 multishot 完成一筆後重新掛回 poll，`MULTISHOT_MAX_RETRY=32` 做公平性護欄，所以 CQE 可能交錯、要靠 `user_data` 認領。

## 自我檢核（先別看前面，試著回答）

- [ ] 一個 multishot accept 的 SQE 產生了三個 CQE，它們的 `user_data` 一樣還是不同？為什麼？
- [ ] 我收到一個 multishot CQE，`F_MORE=0`，該做什麼？不做會怎樣？
- [ ] 為什麼 multishot recv 不能用普通 buffer？它需要什麼？
- [ ] `sendmsg` 比 `send` 多了哪些能力？`msghdr` 的 buffer 所有權有什麼要注意？
- [ ] multishot 為什麼不保證 CQE 和事件順序對齊？`MULTISHOT_MAX_RETRY` 在防什麼？

## 延伸閱讀

### 必讀

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **讀哪裡**：multishot accept / multishot recv / provided buffer ring 三節，本章的權威出處
  - **學什麼**：multishot recv 為何綁 buffer ring、F_MORE 與 buffer 回收的配合
  - **前提**：本章讀完，Ch 14 開始前的最佳橋接

### man page

- **[io_uring_prep_multishot_accept(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_multishot_accept.3.html)、io_uring_prep_recv_multishot(3)、io_uring_prep_recvmsg(3)**
  - **讀哪裡**：multishot 版本的語意說明、`IORING_CQE_F_MORE` 的描述
  - **學什麼**：各 multishot op 的最低 kernel 版本、F_MORE 清除的條件

- **[cmsg(3)](https://man7.org/linux/man-pages/man3/cmsg.3.html)**
  - **讀哪裡**：`CMSG_FIRSTHDR`/`CMSG_NXTHDR`/`CMSG_DATA` 巨集、`SCM_RIGHTS` 範例
  - **為什麼值得讀**：本章控制訊息一節未實測的部分，實作時照這篇組 `msg_control`

### 原始碼

- **Linux 6.18 `io_uring/net.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/net.c>
  - **讀哪裡**：`MULTISHOT_MAX_RETRY`、`REQ_F_APOLL_MULTISHOT` 分支、recv prep 的 `IORING_RECV_MULTISHOT` 檢查（`-EINVAL` 那段）
  - **學什麼**：multishot 如何重新武裝、為何需要 buffer select
  - **前提**：Ch 28 poll internals 會更深入

### LWN

- **[Ringing in a new asynchronous I/O API](https://lwn.net/Articles/776703/)** 與 io_uring multishot 相關報導 — LWN
  - **這篇說什麼**：io_uring 各機制加入的時間線與動機；查 multishot accept/recv 何時、為何加入，LWN 最準
  - **讀哪裡**：搜尋 "multishot" 的對應文章
  - **前提**：想了解設計演進脈絡時讀

- **[liburing test/ 的 accept.c、recv-multishot.c](https://github.com/axboe/liburing/tree/master/test)**
  - **這些檔說什麼**：作者群的回歸測試，把 multishot accept/recv 的各種邊界（F_MORE 清除、buffer 用盡、取消）都測過
  - **讀哪裡**：`test/accept.c`、`test/recv-multishot.c`，看它們怎麼驗 F_MORE 與重新武裝
  - **前提**：想看「正確處理 multishot 的 production 寫法長怎樣」時，這是最可靠的樣本

兩章網路操作學完，該動手了。練習 B 用 multishot accept + recv 寫一個完整的 echo server，把 completion 模型、buffer 所有權、F_MORE 重新武裝全部串起來。

→ [練習 B — multishot echo server](./practice-b-echo-server.md)
