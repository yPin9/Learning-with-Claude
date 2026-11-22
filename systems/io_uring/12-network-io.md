# Ch 12 — 網路 I/O：accept/connect/send/recv

> **目標**：把 io_uring 用在網路上，寫出第一個完整的 TCP 往返。學完你能用 `io_uring_prep_accept/connect/send/recv/shutdown`，並且**講清楚 io_uring 的 completion 模型和 epoll 的 readiness 模型本質差在哪**——這是從 epoll 世界轉過來最需要重新接線的直覺。還會搞懂 buffer 所有權：送出前不能碰 buffer。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 Ch 0 的 `iou-cc`。輸出皆 WSL2 實測。

## 為什麼網路 I/O 是 io_uring 的主戰場

io_uring 誕生時主打儲存，但它真正改變遊戲規則的地方是**網路**。原因：網路伺服器的核心迴圈就是「等很多 fd 的事件、處理、再等」，這正是 epoll 幾十年來的地盤。io_uring 用一個**根本不同的模型**來做同一件事，而且在高併發下更省 syscall、更好搭配 zero-copy 與 multishot（Ch 13）。現代的 proxy、資料庫的網路層、Rust 的 tokio-uring 都在往這裡搬。

但要用好它，你得先把腦袋從「readiness」切換到「completion」。這一章就幹這件事。

## 先建立直覺：readiness vs completion

這是整章最重要的一張圖。epoll 和 io_uring 回答的是兩個不同的問題：

```
 epoll（readiness 模型）：「這個 fd 現在可以讀了嗎？」
   epoll_wait ──► 「fd 7 可讀」  ← 只是通知，資料還在 kernel
        │
        └─► 你再呼叫 read(7, buf, n) ──► 真的把資料搬進 buf
            （兩步：先知道可讀，再自己去讀）

 io_uring（completion 模型）：「幫我讀，讀好了叫我」
   prep_recv(7, buf, n) + submit ──► kernel 在背景把資料搬進 buf
        │
        └─► CQE：「recv 完成，res=16」 ← 資料已經在 buf 裡了
            （一步：完成時資料就位）
```

差別講白了：

- **epoll 回報「狀態」**：它告訴你 fd 可讀/可寫，但**資料的搬運是你自己做**（事件回來後再 `read`）。這是兩段式：wait → read。
- **io_uring 回報「結果」**：你一開始就說「幫我 recv 到這塊 buffer」，CQE 回來時**資料已經在 buffer 裡了**（`res` 是收到的位元組數）。這是一段式：submit → 完成即就位。

這個差異滲透到每個操作。epoll 下你 `accept` 是在「listener 可讀」事件後自己呼叫 `accept()`；io_uring 下你直接 prep 一個 accept 操作，CQE 回來時**新連線的 fd 已經在 `res` 裡**。epoll 下 `recv` 要先等「可讀」再讀；io_uring 下你直接 prep recv，完成時資料到手。

> 一句話記住：**epoll 給你「可以做了」的信號，io_uring 給你「已經做完了」的結果。** 從 epoll 轉過來，最容易錯的就是還用兩段式思維，去等一個「可讀」事件——io_uring 沒有那種事件（除非你用 `IORING_OP_POLL_ADD`，Ch 18），它要你直接提交真正的操作。

## 核心內容：一個最小 TCP 往返

把 accept/connect/send/recv/shutdown 在**同一個行程**裡跑一遍：建 listener、建 client、同時發 accept 和 connect、互傳、看 EOF。完整檔見 `src/ch12_net.c`。

### accept 與 connect：互相成全

epoll 下 accept 和 connect 分屬 server/client，要兩邊各自等事件。io_uring 下我們可以在一個 ring 裡**同時提交兩者**——accept 在等人連、connect 去連它，兩個操作互相成全：

```c
// listener bind 到 127.0.0.1:0，讓 kernel 挑 port（省得撞埠）
int lfd = socket(AF_INET, SOCK_STREAM, 0);
bind(lfd, ...); listen(lfd, 8);
getsockname(lfd, &sa, &slen);                 // 取得實際 port

int cfd = socket(AF_INET, SOCK_STREAM, 0);

// 同時送 accept + connect
sqe = io_uring_get_sqe(&ring);
io_uring_prep_accept(sqe, lfd, NULL, NULL, 0);
sqe->user_data = 1;
sqe = io_uring_get_sqe(&ring);
io_uring_prep_connect(sqe, cfd, (struct sockaddr*)&sa, sizeof(sa));
sqe->user_data = 2;
io_uring_submit(&ring);
```

`io_uring_prep_accept(sqe, listen_fd, addr, addrlen, flags)` 對應 `accept4(2)`：`addr`/`addrlen` 可填 `NULL`（不要對端位址），`flags` 可帶 `SOCK_NONBLOCK`/`SOCK_CLOEXEC`。**新連線的 fd 從 CQE 的 `res` 回來**，不像 epoll 要你事件後再 `accept()`。

`io_uring_prep_connect(sqe, fd, addr, addrlen)` 對應 `connect(2)`，完成時 `res=0` 表示連上。實測：

```
listener on 127.0.0.1:41043
[accept]  server conn_fd=7
[connect] res=0 (connected)
```

accept 的 `res=7` 就是 server 端這條連線的 fd，connect 的 `res=0` 表示 client 連上了。**用 `user_data` 區分是哪個操作完成**（這裡 1=accept、2=connect）——因為兩個 CQE 的回來順序不保證，你得靠 `user_data` 認領。

### send / recv：completion 模型下的收發

```c
// client send（注意 buffer 所有權，下一節細講）
const char *ping = "ping from client";
io_uring_prep_send(sqe, cfd, ping, strlen(ping), 0);
...
// server recv：CQE 回來時資料已在 rbuf
char rbuf[64] = {0};
io_uring_prep_recv(sqe, conn_fd, rbuf, sizeof(rbuf) - 1, 0);
```

```
[send]    res=16 (送出 16 bytes)
[recv]    res=16: "ping from client"
```

`io_uring_prep_send(sqe, fd, buf, len, flags)` / `io_uring_prep_recv(sqe, fd, buf, len, flags)` 對應 `send(2)`/`recv(2)`，`flags` 是 socket 的 `MSG_*`。重點再強調一次：recv 的 CQE 回來時，**`rbuf` 裡已經有資料了**，`res=16` 是收到的位元組數。沒有「先等可讀再讀」這一步。

接著 server 把收到的 echo 回去、client recv：

```
[client recv] res=16: "ping from client"
```

一次完整往返：client → server → client，全程在一個 ring、一種收割邏輯裡。

### shutdown 與 EOF

```c
io_uring_prep_shutdown(sqe, cfd, SHUT_WR);    // client 關掉寫方向
...
io_uring_prep_recv(sqe, conn_fd, rbuf, ...);  // server 再 recv
```

```
[recv EOF] res=0 (對端關閉寫方向 => 0 bytes = EOF)
```

`io_uring_prep_shutdown(sqe, fd, how)` 對應 `shutdown(2)`，`SHUT_WR` 關閉寫方向。對端（server）之後的 recv 收到 `res=0`——這就是 **EOF**。和同步 socket 一樣：`recv` 回 0 代表對端關了寫方向，不會再有資料。你的迴圈看到 `res == 0` 就該收掉這條連線。注意區分 `res == 0`（EOF，正常關閉）和 `res < 0`（錯誤，如 `-ECONNRESET`）。

### 錯誤與部分完成：res 的三種面貌

網路 op 的 `res` 要分三類看，漏掉任一類都會寫出脆弱的 server：

- **`res > 0`**：成功，值是位元組數（send 送出的、recv 收到的）或新 fd（accept）。
- **`res == 0`**：對 recv 是 **EOF**（對端關了寫方向）；對 send 幾乎不會發生；對 accept 不會是 0。
- **`res < 0`**：錯誤，負的 errno。

connect 失敗是最常見的 `res < 0`。對一個沒人監聽的 port 發 connect，實測（`src/ch12_err.c`）：

```c
sa.sin_port = htons(1);                       // 127.0.0.1:1，幾乎確定沒人聽
io_uring_prep_connect(sqe, cfd, (struct sockaddr*)&sa, sizeof(sa));
int res = run_one();
```

```
[connect refused] res=-111 (Connection refused)
```

`res=-111`（`-ECONNREFUSED`）。注意這個錯誤**從 CQE 回來，不是 connect 當場回**——又一次印證 completion 模型：連 connect 這種「發起」動作的失敗，也是非同步地以 `res` 回報。其他常見的：recv/send 碰到對端 reset 是 `-ECONNRESET`、寫入已關閉的連線是 `-EPIPE`、操作被取消是 `-ECANCELED`。

**部分完成（short send/recv）**也要處理。`send` 可能只送出一部分（socket 送緩衝區滿），`recv` 可能只收到一部分（資料還沒到齊）。TCP 是位元組流，一次 recv 收到多少沒有保證，你**不能假設「一次 recv 收到一個完整訊息」**。兩種對策：

1. **自己迴圈續送/續收**：send 回 `res < len` 就從 `buf + res` 再送 `len - res`；recv 回的不足一個訊息就再 recv 累積。
2. **用 `recvmsg` + `MSG_WAITALL`**（Ch 13）：要求 kernel 盡量收滿才回，減少 short recv（但遇到 EOF 或訊號仍可能短）。

echo server（練習 B）之所以能忽略這點，是因為 echo 的語意不要求「一次收完一個訊息」——收到多少就回多少。但做協定解析（HTTP、Redis protocol）時，short recv 的累積是你必須自己寫的狀態機。

### accept_direct：把新連線直接開進 fixed table

`io_uring_prep_accept_direct` 和 Ch 11 的 `openat_direct` 同理：新連線的 fd 不進一般 fd 空間，而是直接放進**註冊檔案表**的某個 slot。好處是省掉 fd 安裝的成本、且後續操作能用 `IOSQE_FIXED_FILE` 引用（Ch 15），在高併發 accept 場景下更省。multishot 版本 `io_uring_prep_multishot_accept_direct` 甚至能自動分配 slot。本章先用一般 accept，direct 版留到 Ch 15 fixed file 一起講。

## 核心內容：buffer 所有權——送出前不能碰

這是 io_uring 網路程式最容易產生詭異 bug 的地方，而且在同步 `send()` 世界裡**不存在**這個問題。

同步 `send(fd, buf, n)` 回來時，資料已經被 kernel 複製進 socket 的傳送緩衝區了，`buf` 你愛怎麼改、釋放都行。io_uring 的 send **不是這樣**：

```
 同步 send：函式回來 = 資料已複製走，buf 自由
   send(fd, buf, n) ───► [複製進 kernel] ───► 回傳，buf 可改

 io_uring send：提交 ≠ 完成，中間 buf 歸 kernel
   prep_send(fd, buf, n) + submit ──► 提交了，但 kernel 還沒讀 buf
        │  ← 這段期間你改/釋放 buf = 送出去的是垃圾或 crash
        └──► CQE 回來 ──► 這時 kernel 才真的讀完 buf，你才能動它
```

**規則：從 `submit` 到那個 send 的 CQE 回來之前，`buf` 的內容必須保持不變、記憶體不能釋放。** kernel 是在背景某個時間點才真的去讀你的 buffer 把資料送出，你提交後馬上改它、`free` 它、或讓它離開 scope（stack 上的區域變數），kernel 讀到的就是**被你改過的、或已經失效的記憶體**。

常見的三種中招方式：

1. **迴圈裡複用同一塊 buffer**：填 buffer → 提交 send → 立刻填下一筆 → 提交下一個 send。第一個 send 的 CQE 還沒回，你已經覆寫了它要送的內容。
2. **送 stack 上的區域變數**：`char tmp[64]; prep_send(..., tmp, ...); submit; return;`——函式 return 後 `tmp` 失效，send 可能送出垃圾。
3. **提交後就 `free(buf)`**：以為提交等於送完。

正確做法：**每個 in-flight send 用它專屬的、生命週期長於該操作的 buffer**，直到對應 CQE 回來才回收或複用。Ch 13 的 echo server、Ch 14 的 provided buffers 都是在系統化地解決這個所有權問題。recv 的 buffer 同理：提交後到 CQE 回來前，那塊 buffer 歸 kernel，你不能同時拿它做別的事。

> 這個所有權規則是 **completion 模型的直接後果**：既然操作是非同步、在背景完成的，buffer 當然得在整個操作期間保持有效。它和 Ch 10 的 O_DIRECT buffer、Ch 11 的 statx buffer 是同一條原則——**async 操作進行中，buffer 歸 kernel**。

### 什麼時候你其實想要 readiness：POLL_ADD

前面一直說 io_uring 是 completion 模型、不要去等「可讀」事件。但有少數場景你**真的只想知道「可讀了」，不想讓 io_uring 幫你讀**——例如你要自己用特殊方式讀（`recvmmsg` 一次收多個 datagram）、或要把 readiness 事件橋接到既有的 epoll 風格程式碼。這時用 `IORING_OP_POLL_ADD`（`io_uring_prep_poll_add`，Ch 18 細講）：它讓 io_uring 當一個「可掛在 ring 上的 poll」，fd 可讀時給你一個 CQE，但**不搬資料**，搬運你自己來。

這是 io_uring 裡少數回報「狀態」而非「結果」的 op，正好是 readiness 模型的影子。但它是例外、不是常態——絕大多數時候，直接提交 recv 讓 io_uring 一步到位，比「poll 等可讀 + 自己 recv」兩段式更省。multishot poll（Ch 13、Ch 18）則是它的持續版，真要拿 io_uring 當 epoll 用時才派上用場。

### UDP 與 sendto/recvfrom

本章用 TCP 示範，但 io_uring 也支援 UDP（datagram socket）。TCP 的 send/recv 不帶位址（連線已固定對端），UDP 要每筆帶目的/來源位址：

- **送**：`io_uring_prep_sendto(sqe, fd, buf, len, flags, addr, addrlen)`，或更一般的 `io_uring_prep_sendmsg`（Ch 13）用 `msghdr.msg_name` 帶位址。
- **收**：`io_uring_prep_recvmsg`，收完 `msghdr.msg_name` 裡是來源位址。

UDP 下沒有「連線」概念，所以沒有 accept/connect 的必要（connect 一個 UDP socket 只是固定預設對端）。multishot recv（Ch 13）配 UDP 很常見：一個 SQE 持續收所有進來的 datagram。

## 底層機制：網路 op 的非阻塞嘗試與 poll 武裝

對照 6.18 的 `io_uring/net.c`。io_uring 的網路 op 會先試著**非阻塞**地做（`IO_URING_F_NONBLOCK`）：socket 已經有資料可讀、或可寫，就當場完成填 CQE。做不到（例如 recv 時對端還沒送東西），kernel 不會傻傻阻塞一條 worker，而是給這個請求**武裝一個內部的 poll**（armed poll，Ch 28 細講）：掛在 socket 的等待佇列上，等它變可讀/可寫了，再由 task_work（Ch 27）把這個 recv 重新 issue 一次、完成、填 CQE。

```
 prep_recv + submit
     │
     ├─ socket 現在就有資料 ──► 當場 recv 完成，填 CQE
     │
     └─ 現在沒資料 ──► 內部武裝 poll，掛上 socket 等待佇列
                         │（socket 可讀了）
                         └─► task_work 重跑 recv ──► 填 CQE
```

這解釋了兩件事：**(a)** 為什麼你不需要像 epoll 那樣自己管「可讀事件」——io_uring 在內部用 poll 幫你等，但對你暴露的是「recv 完成」這個結果，不是「可讀」這個狀態。**(b)** 為什麼 io_uring 網路伺服器能用很少的 thread 扛很多連線：等待是靠 poll + task_work，不是靠阻塞 thread。這套內部機制就是 Ch 13 multishot、Ch 28 poll internals 的基礎，這裡先知道「非阻塞試一次，不成就武裝 poll」這個骨架。

值得對照 Ch 11 的 metadata ops：那些 op 一律 `REQ_F_FORCE_ASYNC` 直接送 io-wq（因為它們沒有「可等待的 readiness」可言，只能用阻塞 worker 做）。網路 op 不一樣——socket 的可讀/可寫是**可以 poll 的事件**，所以 io_uring 優先用 poll + task_work 這條**不佔 worker thread** 的路，只有真的沒辦法才退回 io-wq。這個差異正是 io_uring 網路擴展性的來源：成千上萬條閒置連線不吃任何 thread，只是幾個掛在 socket 等待佇列上的 poll entry。

### registered files：連線 fd 也能註冊

和檔案一樣（Ch 11 的 `openat_direct`、Ch 15），socket fd 可以註冊進 io_uring 的檔案表，用 `IOSQE_FIXED_FILE` + slot 索引引用，省掉每次操作查 fd 表的成本。對一個「同一批 socket 反覆收發」的 server，這是實打實的優化。`accept_direct` 甚至讓新連線直接落進檔案表。本章先用一般 fd，Ch 15 會把 registered files 的完整 API 和效益講清楚。

## 對比與取捨

| 面向 | epoll（readiness） | io_uring（completion） |
|---|---|---|
| 回報什麼 | fd 可讀/可寫的**狀態** | 操作已完成的**結果**（含資料與 `res`） |
| 收資料 | 事件後自己 `read`（兩段） | recv 完成時資料已就位（一段） |
| accept | 事件後自己 `accept()` | 新 fd 直接在 `res` |
| buffer 所有權 | `read` 後資料已複製，buffer 自由 | 提交到 CQE 前 buffer 歸 kernel |
| 等待機制 | 你管 epoll fd 集合 | kernel 內部 poll + task_work |
| syscall 數 | 每輪 `epoll_wait` + N 個 `read` | 可批次提交、SQPOLL 下趨近零 |
| 新手最大陷阱 | — | 還用兩段式思維；提前改/釋放 buffer |

## 踩雷集錦

1. **還在等「可讀」事件**。錯誤直覺（epoll 慣性）：「先 poll 看 socket 可不可讀，再 recv」。正確認識：io_uring 直接提交 recv，完成即資料就位。想要「只等可讀不讀」是 `IORING_OP_POLL_ADD`（Ch 18）的少數場景，不是常態。
2. **send 提交後馬上改/釋放 buffer**。錯誤直覺：「`io_uring_submit` 回來就等於送完了」。正確認識：submit ≠ 完成，buffer 要撐到該 send 的 CQE 回來。複用 buffer、送 stack 變數、提前 free 都會送出垃圾或 crash。
3. **用一個 CQE 回來的順序去假設哪個操作完成**。錯誤直覺：「我先提交 accept 再提交 connect，CQE 就會先 accept 後 connect」。正確認識：完成順序不保證，一律用 `user_data` 認領是哪個操作。
4. **把 `res == 0` 當錯誤**。錯誤直覺：「recv res=0 是不是失敗了」。正確認識：`res == 0` 是 EOF（對端正常關閉寫方向），`res < 0` 才是錯誤。兩者要分開處理。
5. **忽略 short send/recv**。錯誤直覺：「send 100 bytes，res 一定 100」。正確認識：send/recv 都可能回少於要求的數（socket 緩衝區滿/訊號中斷/對端慢），要檢查 `res` 並續送/續收，或用 `MSG_WAITALL`（recvmsg）。
6. **特地把 socket 設成 `O_NONBLOCK` 再丟進 ring**。錯誤直覺（epoll 慣性）：「非阻塞 socket 才不會卡住事件迴圈」。正確認識：io_uring 的網路 op 本來就不會阻塞你的 thread（底層用 poll + task_work 等），socket 是不是 `O_NONBLOCK` 它自己會處理。你不需要、也不該為了配合 io_uring 去設 `O_NONBLOCK`——那是 epoll readiness 模型的要求，不是 completion 模型的。

> **一個反直覺的好處**：因為 io_uring 自己管等待，你的 socket 可以是**阻塞**的 blocking socket，io_uring 照樣不會阻塞你。這和 epoll「必須搭配非阻塞 socket」的鐵律正好相反，是從 epoll 轉過來另一個要鬆開的假設。

## 本章重點整理

- **io_uring 是 completion 模型**：你提交「幫我做這個 I/O」，CQE 回來時**已經做完、資料已就位**；epoll 是 readiness 模型，只告訴你「可以做了」，搬運你自己來。從 epoll 轉過來最需要重接這條線。
- accept 的新 fd、connect 的結果、recv 的資料與長度，全都從 CQE 的 `res` 回來；用 `user_data` 區分哪個操作完成，因為順序不保證。
- `recv` 的 `res == 0` 是 EOF，`res < 0` 是錯誤；`shutdown(SHUT_WR)` 會讓對端 recv 看到 EOF。
- **buffer 所有權**：send/recv 從提交到 CQE 回來之前，buffer 歸 kernel，不能改、不能釋放、不能離開 scope——這是 completion 模型的必然後果。
- 底層上，網路 op 先試非阻塞，不成就武裝內部 poll + task_work 等待，所以你不必自己管 readiness，也能用少量 thread 扛大量連線。

## 自我檢核（先別看前面，試著回答）

- [ ] 用一句話說出 readiness 和 completion 模型的本質差別。io_uring recv 的 CQE 回來時，資料在哪？
- [ ] 我提交了一個 send，`io_uring_submit` 回傳了，我能不能馬上 `free` 那塊 buffer？為什麼？
- [ ] 同時提交 accept 和 connect，我怎麼知道回來的 CQE 是哪一個？
- [ ] recv 的 `res == 0` 代表什麼？和 `res < 0` 怎麼區分處理？
- [ ] io_uring 內部怎麼「等」一個還沒有資料的 recv，而不阻塞一條 thread？

## 延伸閱讀

### 必讀：io_uring 網路的權威整理

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **這篇說什麼**：io_uring 網路所有機制（send/recv、multishot、provided buffer、zero-copy、`MSG_WAITALL`）最新最權威的整理，作者群親自維護
  - **讀哪裡**：先讀 send/recv 與 buffer 管理兩節，正好接本章；multishot、zero-copy 留到 Ch 13、練習 C
  - **前提**：本章讀完再看，很多名詞會對上

### man page

- **[io_uring_prep_accept(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_accept.3.html)、io_uring_prep_recv(3)、io_uring_prep_send(3)、io_uring_prep_connect(3)**
  - **讀哪裡**：各 op 的 flags、`addr`/`addrlen` 語意、direct descriptor（`accept_direct`）版本
  - **學什麼**：`io_uring_prep_accept` 的 `flags` 可帶 `SOCK_NONBLOCK`/`SOCK_CLOEXEC`

### 原始碼

- **Linux 6.18 `io_uring/net.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/net.c>
  - **讀哪裡**：`io_recv`/`io_send`、`io_accept`，以及 `REQ_F_APOLL_MULTISHOT` 相關分支
  - **學什麼**：非阻塞嘗試、`-EAGAIN` 後如何武裝 poll、buffer select 的檢查
  - **前提**：Ch 28 poll internals 會深入，這裡先掃過骨架

### 設計背景

- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** — LWN, Jonathan Corbet
  - **這篇說什麼**：io_uring 從儲存擴張到網路的歷程與動機，解釋為什麼網路支援是後來的重點
  - **讀哪裡**：網路相關 op 加入的段落
  - **前提**：想知道「為什麼 io_uring 要搶 epoll 地盤」時讀

### epoll 對照

- **[Lord of the io_uring — Networking](https://unixism.net/loti/tutorial/webserver_liburing.html)** — Shuveb Hussain
  - **這篇說什麼**：用 io_uring 寫 web server，和 epoll 版對照，實感 completion 模型的寫法差異
  - **讀哪裡**：整頁；它的收發迴圈和本章的往返同構
  - **前提**：寫過或讀過 epoll 伺服器會更有感

一個操作撐一條連線的收發，每次都要重新提交——有沒有辦法「提交一次、持續收割」？下一章的 multishot 就是幹這個，還有處理 scatter-gather 與控制訊息的 sendmsg/recvmsg。

→ [Ch 13 multishot 與 sendmsg/recvmsg](./13-multishot.md)
