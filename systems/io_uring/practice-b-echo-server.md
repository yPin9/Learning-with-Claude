# 練習 B — multishot echo server

> **目標**：把 Ch 12–13 學到的 completion 模型、buffer 所有權、multishot accept、F_MORE 重新武裝全部串起來，寫一個能同時服務多條連線的 TCP echo server。這是你第一個「真的能被別的程式連上」的 io_uring 網路程式。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 Ch 0 的 `iou-cc`。在 WSL2 實測：背景跑 server，用 python/nc 連上測 echo。

## 背景動機

echo server 是網路程式設計的「hello world」：client 送什麼，server 原樣送回。它看似簡單，卻逼你面對 io_uring 網路程式的**每一個核心問題**：

- **一個事件迴圈怎麼同時管「接新連線」和「每條連線的收發」？** 這兩件事交錯發生，你只有一個 ring。
- **收到一個 CQE，怎麼知道它是哪種操作、屬於哪條連線？** accept、recv、send 的 CQE 都從同一個 `io_uring_wait_cqe` 回來。
- **buffer 歸誰、什麼時候能複用？** 每條連線的 recv buffer 不能互相踩，send 期間不能改。
- **連線關閉、瞬時錯誤怎麼處理，才不會洩漏 fd 或讓 server 停擺？**

把這些想清楚，你就從「會用 io_uring 單一操作」升級到「會用 io_uring 寫一個持續運作的 server」。這正是 io_uring 在 proxy、資料庫網路層的真實用法雛形。

對照一下另外兩種經典寫法，就知道 io_uring 版本的定位：

- **thread-per-connection**：每條連線開一條 thread，裡面用阻塞 `recv`/`send`。好寫，但一萬條連線要一萬條 thread，context switch 和記憶體把系統壓垮。
- **epoll 事件迴圈**：一條 thread 管所有連線，`epoll_wait` 回報可讀/可寫，你再去 `recv`/`send`。擴展性好，但你得手動管每個 fd 的 readiness 狀態、每次事件兩段式處理（先知道可讀、再去讀）。
- **io_uring（本練習）**：一條 thread、一個 ring，提交「幫我 recv/send」，完成時資料已就位。擴展性和 epoll 同級甚至更好，但是 completion 模型——沒有「可讀」這一步，而且能把 accept 也變成持續性的 multishot。

你寫的這個 echo server 就是第三種的最小骨架。

## 任務規格

寫一個程式 `echo.c`：

1. 在 `127.0.0.1` 上監聽一個 TCP port（命令列給 port，或給 `0` 讓 kernel 挑，程式印出實際 port）。
2. 用 **multishot accept** 接受連線——只提交一次 accept，持續接客。
3. 對每條連線：收到資料就**原樣送回**（echo），送完繼續收，直到對端關閉。
4. 同時服務**多條**連線，互不干擾。
5. 連線關閉（recv `res == 0`）或出錯（`res < 0`）時，關掉該連線的 fd、釋放其資源。
6. accept 的 **F_MORE 若清除，要重新武裝**，不能讓 server 停止接客。

### 限制與要求

- recv 用**一次性 recv**（每次收完重新武裝），**不是** multishot recv——因為 multishot recv 需要 provided buffers（Ch 14 才學）。這是本練習刻意的範圍。
- 每條連線要有**自己專屬的 buffer**，生命週期涵蓋整個 recv/send。
- 用 `user_data` 區分「這個 CQE 是 accept、recv、還是 send，屬於哪條連線」。

## 期望行為

```
$ ./echo 48123
echo server on 127.0.0.1:48123
（server 持續運作，等待連線）

# 另一個終端：
$ printf 'hello echo' | nc 127.0.0.1 48123
hello echo                      # 原樣回來

$ python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",48123)); s.sendall(b"abc"); print(s.recv(100))'
b'abc'
```

多條連線同時連、同一條連線多筆往返，都應該正確 echo。

## 先建立直覺：一條連線的狀態機

echo server 的核心是把「每條連線」看成一台小狀態機，事件迴圈則是所有狀態機共用的驅動引擎。一條連線的生命週期：

```
  multishot accept
        │ 新連線 CQE (res = conn_fd)
        ▼
   ┌─────────┐   recv CQE res>0    ┌─────────┐
   │  RECV   │ ──────────────────► │  SEND   │
   │ (等資料)│                      │(回 echo)│
   └─────────┘ ◄────────────────── └─────────┘
        │        send CQE res>=0        │
        │ recv CQE res<=0               │ send CQE res<0
        ▼ (EOF/錯誤)                    ▼
     close(fd) + free(conn) ◄───────────┘
```

每條連線在 RECV 和 SEND 之間來回：收到資料就切到 SEND 把它回去，送完切回 RECV 等下一筆。任一步的 CQE 是壞消息（recv 看到 EOF/錯誤、send 失敗）就進終止狀態，關 fd、釋放連線物件。

關鍵在於**事件迴圈本身不知道也不在乎有幾條連線**——它只做一件事：等一個 CQE、看 `user_data` 認出「這是哪條連線的哪個動作」、推進那台狀態機。幾千條連線就是幾千台狀態機共用這一個迴圈，彼此靠 `user_data` 區分，靠各自的 `conn` 物件保存狀態。這就是 io_uring（和所有 completion-based）server 的骨架。

## 卡住提示

- **一個 CQE 怎麼帶「操作種類 + 連線身分」兩個資訊？** `user_data` 是一個 64-bit 整數。連線物件用 `malloc` 配置，指標至少對齊到 8，**低 3 bits 一定是 0**——拿來塞「操作種類」（1=recv、2=send）剛好。accept 用一個固定的哨兵值（例如某個全域變數的位址）。
- **accept、recv、send 三種 CQE 怎麼分流？** 先看 `user_data` 是不是 accept 哨兵；不是的話，從低位元取出操作種類、從高位元取出連線物件指標。
- **recv 回 0 和回負數要分開嗎？** 都代表這條連線該收掉，但 0 是正常 EOF、負數是錯誤。本練習都一樣處理（關閉），但你要知道差別。
- **送回去要送幾 bytes？** recv 的 `res` 就是收到的位元組數，echo 就送這麼多（不是整個 buffer）。
- **F_MORE 清除時怎麼辦？** 重新提交一個 multishot accept。別忘了。
- **什麼時候呼叫 `io_uring_submit`？** 每輪處理完 CQE、可能排了新的 SQE（arm_recv/arm_send/arm_accept）之後，提交一次。

## 實作步驟

### Step 1：建 listener 並初始化 ring

一般 socket/bind/listen（可以用普通 syscall，listener 的建立不是熱路徑）。`bind` 到 `127.0.0.1`，port 用命令列參數。`io_uring_queue_init(256, &ring, 0)` 開一個夠深的 ring。

### Step 2：設計 user_data 編碼

定義連線物件：

```c
struct conn { int fd; int len; char buf[2048]; };
```

編碼：`user_data = (指標) | 操作種類`，其中操作種類取 `OP_RECV=1`、`OP_SEND=2`（只用低位元）。accept 用獨立哨兵 `ACCEPT_UD`（一個全域變數的位址，低位元為 0）。寫兩個 helper：`tag(conn, op)` 組出 user_data、`conn_of(ud)`/`op_of(ud)` 拆回來。

### Step 3：武裝 multishot accept，進入事件迴圈

提交一個 `io_uring_prep_multishot_accept`，`user_data = ACCEPT_UD`。然後 `for(;;)` 迴圈：`io_uring_wait_cqe` → 依 `user_data` 分流 → 處理 → `io_uring_submit`。

### Step 4：三種 CQE 的處理

- **accept CQE**（`ud == ACCEPT_UD`）：`res >= 0` 就 `calloc` 一個 `conn`、設 `fd = res`、武裝一個 recv。然後**若 `!(flags & F_MORE)` 就重新武裝 accept**。
- **recv CQE**（`op == OP_RECV`）：`res <= 0` 關閉連線、釋放 conn；否則記下 `len = res`、武裝一個 send 把資料回去。
- **send CQE**（`op == OP_SEND`）：`res < 0` 關閉釋放；否則武裝下一個 recv（繼續服務這條連線）。

### Step 5：每輪結尾提交

處理完一個 CQE、`io_uring_cqe_seen` 之後，`io_uring_submit` 把這輪新排的 SQE 送出去。

## 完整參考解答

**自己先寫！** 卡住了再回去看 Ch 12（buffer 所有權）、Ch 13（multishot accept、F_MORE）。偷看會學不到把它們串起來的那一步。

<details>
<summary>點開參考實作（已在 WSL2 kernel 6.18 + liburing 2.16 實測）</summary>

```c
// echo.c — multishot accept + 每連線 recv/send 的 echo server
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <liburing.h>

#define QD      256
#define BUF_SZ  2048

// 每個 CQE 用 user_data 低位元帶「這是哪種 op」，高位帶 conn 指標
enum { OP_RECV = 1, OP_SEND = 2 };
static long accept_marker;                        // accept 的專屬 user_data
#define ACCEPT_UD ((uint64_t)(uintptr_t)&accept_marker)

struct conn {
    int fd;
    int len;                                      // 這次要 echo 回去的位元組數
    char buf[BUF_SZ];
};

static struct io_uring ring;

static inline uint64_t tag(struct conn *c, int op) {
    return (uint64_t)(uintptr_t)c | (uint64_t)op; // 指標低 3 bits 為 0，塞得下 op
}
static inline struct conn *conn_of(uint64_t ud) {
    return (struct conn *)(uintptr_t)(ud & ~0x7ULL);
}
static inline int op_of(uint64_t ud) { return (int)(ud & 0x7ULL); }

static void arm_accept(int lfd) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_multishot_accept(sqe, lfd, NULL, NULL, 0);
    io_uring_sqe_set_data64(sqe, ACCEPT_UD);
}

static void arm_recv(struct conn *c) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_recv(sqe, c->fd, c->buf, BUF_SZ, 0);
    io_uring_sqe_set_data64(sqe, tag(c, OP_RECV));
}

static void arm_send(struct conn *c) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    // 注意 buffer 所有權：c->buf 必須撐到這個 send 的 CQE 回來（conn 不釋放即可）
    io_uring_prep_send(sqe, c->fd, c->buf, c->len, 0);
    io_uring_sqe_set_data64(sqe, tag(c, OP_SEND));
}

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 0;

    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in sa = {0};
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    sa.sin_port = htons(port);
    if (bind(lfd, (struct sockaddr*)&sa, sizeof(sa)) < 0) { perror("bind"); return 1; }
    listen(lfd, SOMAXCONN);
    socklen_t slen = sizeof(sa);
    getsockname(lfd, (struct sockaddr*)&sa, &slen);        // 取得實際 port
    printf("echo server on 127.0.0.1:%d\n", ntohs(sa.sin_port));
    fflush(stdout);

    if (io_uring_queue_init(QD, &ring, 0) < 0) { perror("queue_init"); return 1; }
    arm_accept(lfd);
    io_uring_submit(&ring);

    for (;;) {
        struct io_uring_cqe *cqe;
        int ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) { if (ret == -EINTR) continue; break; }
        uint64_t ud = cqe->user_data;
        int res = cqe->res;

        if (ud == ACCEPT_UD) {
            if (res >= 0) {
                struct conn *c = calloc(1, sizeof(*c));
                c->fd = res;
                arm_recv(c);                      // 新連線：開始收
            }
            // F_MORE 清除代表這個 multishot accept 不再產生 CQE，要重新 arm
            if (!(cqe->flags & IORING_CQE_F_MORE))
                arm_accept(lfd);
        } else {
            struct conn *c = conn_of(ud);
            int op = op_of(ud);
            if (op == OP_RECV) {
                if (res <= 0) {                   // 0 = 對端關閉；<0 = 錯誤
                    close(c->fd);
                    free(c);
                } else {
                    c->len = res;
                    arm_send(c);                  // 把收到的 echo 回去
                }
            } else { /* OP_SEND */
                if (res < 0) { close(c->fd); free(c); }
                else arm_recv(c);                 // 送完再收下一筆
            }
        }
        io_uring_cqe_seen(&ring, cqe);
        io_uring_submit(&ring);                    // 把這輪排的 SQE 送出
    }
    io_uring_queue_exit(&ring);
    return 0;
}
```

### 編譯與執行

```bash
iou-cc echo.c -o echo
./echo 48123
```

### 幾個設計決定的理由

- **為什麼用指標低位元塞 op？** `calloc` 回來的指標至少對齊 8（`struct conn` 含 `int` 和陣列），低 3 bits 必為 0，拿來放 `op`（1 或 2）不會和指標位元衝突。這是 io_uring 程式常見的 user_data 打包法。
- **為什麼 accept 用 `&accept_marker` 當哨兵？** 它是一個獨一無二的位址，低位元為 0（和 `ACCEPT_UD` 的判斷不會被 op 位元污染），而且絕不會和任何 `calloc` 的 heap 指標相同。
- **buffer 所有權怎麼保證的？** 每條連線的 `c->buf` 綁在 `conn` 物件上，只要 `conn` 沒被 `free`，buffer 就有效。recv 填它、send 讀它，兩者不同時在飛（recv 完才 send、send 完才 recv），所以同一塊 buffer 複用是安全的。
- **為什麼 send 完才 recv、recv 完才 send？** 這是最簡單的正確模型：一條連線同時只有一個 I/O 在飛，避免 recv 和 send 搶同一塊 buffer。代價是吞吐（半雙工），延伸挑戰會談怎麼改。

</details>

## 測試用例

參考解答在 WSL2 的實測。背景跑 server，用 python 連上測各種情境：

```bash
./echo 48123 & SRV=$!
sleep 0.4
python3 - <<'PY'
import socket, threading
def rt(msg):
    s = socket.create_connection(("127.0.0.1", 48123), timeout=2)
    s.sendall(msg)
    data = b""
    while len(data) < len(msg):
        b = s.recv(4096)
        if not b: break
        data += b
    s.close()
    return data
print("echo1:", rt(b"hello echo").decode())
print("echo2:", rt(b"second message over same server").decode())
# 多連線併發
res = {}
def worker(i): res[i] = rt(("conn-%d-payload" % i).encode()).decode()
ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
[t.start() for t in ts]; [t.join() for t in ts]
print("concurrent:", [res[i] for i in range(5)])
# 同一連線多筆往返
s = socket.create_connection(("127.0.0.1", 48123), timeout=2)
for i in range(3):
    s.sendall(("ping%d" % i).encode())
    print("roundtrip:", s.recv(100).decode())
s.close()
PY
kill $SRV 2>/dev/null
```

實測輸出：

```
echo server on 127.0.0.1:48123
echo1: hello echo
echo2: second message over same server
concurrent: ['conn-0-payload', 'conn-1-payload', 'conn-2-payload', 'conn-3-payload', 'conn-4-payload']
roundtrip: ping0
roundtrip: ping1
roundtrip: ping2
```

五條併發連線各自拿回自己的 payload、同一條連線三筆 ping 依序 echo——`user_data` 的連線身分編碼正確分流、buffer 沒互相踩。你也可以用 `nc`：

```bash
printf 'manual test' | nc -q1 127.0.0.1 48123       # 回 "manual test"
```

### 故意把它弄壞，看會怎樣

io_uring 的 bug 常常是沉默的，動手製造幾個典型錯誤，建立「症狀 → 根因」的直覺：

- **忘記重新 arm accept**：把 `if (!(cqe->flags & IORING_CQE_F_MORE)) arm_accept(lfd);` 這行註解掉。正常情況下 multishot accept 不會輕易退場，所以一開始看起來照常運作——但這正是陷阱：一旦某次 CQ 壓力或錯誤讓 F_MORE 清除，server 就**永久停止接受新連線**，而且毫無錯誤訊息。這說明為什麼 F_MORE 檢查不能省。
- **提前釋放 buffer**：在 `arm_send` 之後、CQE 回來之前就 `free(c)`。你會看到 echo 回去的是垃圾、或程式 crash——這就是 Ch 12 的 buffer 所有權規則被違反的後果。
- **send 整個 BUF_SZ 而非 `c->len`**：把 `arm_send` 改成送 `BUF_SZ`。client 會收到一堆尾隨的 `\0`，因為你送了 buffer 裡沒被這次 recv 覆寫的舊內容。`res` 才是這次真正收到的長度。

### 自己驗這幾件事

- 同時開兩個 `nc` 連上去，各打各的字，確認互不干擾（多連線）。
- 送一段大於 2048 bytes 的資料，觀察會發生什麼（提示：單次 recv 最多 `BUF_SZ`，會分成多筆 echo——這正常，echo 語意不要求一次收完）。
- 連上去不送任何東西直接關閉（Ctrl-C `nc`），確認 server 沒 crash、沒洩漏（recv 收到 `res == 0`）。

## 這個設計的效能特性與侷限

參考解答能跑、正確、好懂，但它刻意停在「教學版」，不是 production 版。看清它的侷限，正好是通往後面幾章的動機：

- **每條連線一塊固定 buffer**：`struct conn` 內嵌 2048 bytes，一萬條連線就是 20 MB 常駐，而且多數時間 buffer 是閒置的（連線大半時間在等資料）。真實高併發 server 不這樣配——它們用**一池共享 buffer**，誰要收資料誰才從池裡拿一塊（Ch 14 的 provided buffers）。
- **recv 反覆重新武裝**：每收一筆就要再 `io_uring_prep_recv` 一次，和 Ch 13 開頭講的 one-shot accept 開銷是同一回事。**multishot recv** 能省掉這些重新武裝，但它綁 provided buffers（練習 C）。
- **半雙工**：一條連線同時只有一個 I/O 在飛，recv 和 send 不重疊。對 echo 這種「收完才送、送完才收」的語意剛好夠用，但真實協定（pipelining、全雙工串流）需要同時多個 in-flight 操作，buffer 管理也隨之複雜。
- **一個 ring、一條 thread**：所有連線共用一個事件迴圈。單核能扛的連線數有上限；要吃滿多核得開**多個 ring、多條 thread**（每條綁一個 CPU），或用 `SO_REUSEPORT` 讓多個 listener 分攤——這是 Ch 41 效能工程的主題。

這些侷限沒有一個是 bug，它們是「最小正確版」的自然邊界。**把教學版和 production 版的差距看清楚，比直接抄一個複雜版更有價值**——因為你知道每個複雜度是為了解決哪個具體問題。

## 延伸挑戰

做完基本版，挑一兩個往下走——它們分別對應後面的章節：

1. **用 provided buffers 改成 multishot recv（接 Ch 14）**：目前每條連線各自帶 2048 bytes buffer，連線一多記憶體就線性膨脹，而且 recv 要反覆重新武裝。學完 Ch 14 的 buffer ring 後，改用 **multishot recv + 一池共享 buffer**：recv 也只提交一次、持續收，kernel 從池裡挑 buffer 填，CQE 用 `IORING_CQE_F_BUFFER` 告訴你用了哪塊。這是 production echo server 的真正形態，也是練習 C 的主題。

2. **全雙工 + 多筆 in-flight**：目前一條連線同時只有一個 I/O 在飛（半雙工）。改成 recv 和 send 可以同時進行、甚至一條連線排多個 recv——這時 **buffer 所有權**會變棘手：每個 in-flight 操作要有自己的 buffer，不能再複用單一 `c->buf`。想清楚哪些 buffer 何時能回收（提示：把 buffer 綁在「操作」而非「連線」上，用一個小的 request 物件包住 buffer + 它屬於哪條連線，`user_data` 指向這個 request 物件）。這一步會讓你真正體會為什麼 provided buffers 能把這套手動管理簡化掉。

3. **優雅關閉（graceful shutdown）**：目前 server 是 `for(;;)` 無限迴圈，`kill` 掉就硬斷，in-flight 的連線沒收尾。加一個 `SIGINT` handler，收到後：停止武裝新 accept、對現有連線做 `io_uring_prep_shutdown`、等所有 in-flight CQE 收完、才 `io_uring_queue_exit`。會用到 Ch 17 的 cancel 來中止還掛著的 multishot accept。

4. **限制與背壓**：連線數設上限，超過就拒絕（accept 後立刻 close）；或對單一連線的 in-flight 操作數設上限，避免慢速 client 把記憶體吃爆。

## 自我檢核

完成後你應該能回答：

- [ ] 一個 64-bit 的 `user_data` 怎麼同時帶「操作種類」和「連線身分」兩個資訊？為什麼指標低位元可以借來用？
- [ ] 為什麼這個 echo server 可以安全地讓同一條連線的 recv 和 send 複用同一塊 `c->buf`？如果改成全雙工，這個假設還成立嗎？
- [ ] accept 的 CQE `F_MORE` 清除了，我若忘記重新 arm，server 會出現什麼症狀？
- [ ] 為什麼這裡不用 multishot recv？要用它還缺哪一塊（哪一章）？
- [ ] client 送 3000 bytes，我的 server 會回幾個 send？這算 bug 嗎？
- [ ] `kill` 掉 server 和「優雅關閉」差在哪？in-flight 的操作會怎樣？
- [ ] 為什麼參考解答用 `calloc` 配置 `conn` 而不是放在 stack 或共用一個全域 buffer？（提示：buffer 所有權 + 連線生命週期各自獨立）
- [ ] 如果我把 `arm_send` 和 `arm_recv` 改成「一提交就同時排 recv 和 send」，會踩到什麼？（提示：兩個操作搶同一塊 `c->buf`）
- [ ] 為什麼說這個設計是「半雙工」？echo 語意為什麼剛好不受影響？
- [ ] 把 `conn` 指標塞進 `user_data` 時，為什麼能安全地借用低 3 bits？什麼情況下這個假設會壞掉？

做完這個練習，你已經會用 io_uring 寫一個持續運作、多連線的網路 server 了。但你也摸到了它的天花板：每條連線各帶一塊 buffer 太耗記憶體、recv 反覆重新武裝太浪費。下一章的 provided buffers 與 buffer ring 正是為了打穿這個天花板——讓 kernel 從一池共享 buffer 裡自己挑、自己填。

→ [Ch 14 Provided buffers 與 buffer ring](./14-provided-buffers.md)
