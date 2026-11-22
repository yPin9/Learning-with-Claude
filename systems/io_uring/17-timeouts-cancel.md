# Ch 17 — Timeouts 與 cancel

> **目標**：給 io_uring 的操作裝上時限與主動取消能力。你會學會 `IORING_OP_TIMEOUT`（相對/絕對、不同時鐘、count 計數式）、linked timeout（給某個操作設逾時）、以及 `IORING_OP_ASYNC_CANCEL`（用 user_data 或 fd 取消 in-flight 請求），並親手跑一個 timeout + linked timeout 範例。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。timeout/cancel 從 5.x 早期就在；`IORING_ASYNC_CANCEL_ALL/FD/ANY` 5.19+、`IORING_TIMEOUT_MULTISHOT` 6.4+。

## 為什麼需要 timeout 與 cancel

Ch 16 的鏈解決了順序，但沒解決一個更根本的問題：**有些操作可能永遠不完成**。一個 `recv` 掛在一條死掉的連線上、一個 `connect` 連到一個黑洞 IP、一個 `read` 卡在壞掉的網路檔案系統——它們會無限期佔著一個 io_kiocb、一塊 buffer、一個 io-wq worker。傳統同步程式用 `SO_RCVTIMEO`、`alarm()`、或另開一個計時執行緒來處理；io_uring 把這件事做進 ring 本身。

io_uring 提供兩類工具：

- **timeout**：一個會在「指定時間到」或「指定數量的 CQE 完成後」自己產生 CQE 的操作。可以當純計時器用，也可以當「等 N 個完成、或等 T 時間，誰先到」的屏障。
- **cancel**：主動取消一個已經提交、還沒完成的請求。可以點名取消（用 user_data）、取消某個 fd 上的全部、或取消全部。

兩者結合出最實用的 **linked timeout**：把一個 timeout 綁在某個操作後面，那個操作超時就被取消。這是 io_uring 版的「帶逾時的 I/O」，不用額外執行緒。

## 先建立直覺：timeout 是一個會自己完成的操作

```
  純 timeout（計時器）              linked timeout（給別人設時限）

  [TIMEOUT 200ms] ──200ms──► CQE    [recv ]──LINK──┐
                   res=-ETIME        [LINK_TIMEOUT]─┘ 150ms
                                          │
                        150ms 到 ──► recv 被取消(-ECANCELED)
                                     link_timeout 自己 res=-ETIME
```

關鍵心智模型：**timeout 本身就是一個 io_uring 操作**，和 read/recv 平起平坐——它會佔一個 SQE、產一個 CQE，只是它「完成」的條件是時間到（或計數到），而非 I/O 做完。超時的 CQE `res` 是 **`-ETIME`**（不是 `-ETIMEDOUT`，這兩個常被搞混，`-ETIME` 是 62、`-ETIMEDOUT` 是 110）。

## 範例一：純 timeout（相對時間）

`io_uring_prep_timeout(sqe, &ts, count, flags)`：等 `ts` 這段時間、或等 `count` 個 CQE 完成，誰先到就產生這個 timeout 的 CQE。`count=0` 表示純計時——只看時間。

```c
struct __kernel_timespec ts = { .tv_sec = 0, .tv_nsec = 200 * 1000000 };  // 200ms
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_timeout(sqe, &ts, 0, 0);     // count=0, flags=0（相對時間、MONOTONIC）
io_uring_sqe_set_data64(sqe, 100);
double t0 = now_ms();
io_uring_submit(&ring);
io_uring_wait_cqe(&ring, &cqe);
printf("(1) timeout: res=%d (%s), 實際等了 %.0f ms\n",
       cqe->res, strerror(-cqe->res), now_ms() - t0);
```

實測輸出：

```
(1) timeout: res=-62 (Timer expired), 實際等了 200 ms
```

`res=-62` 就是 `-ETIME`，"Timer expired"。實際等了 200ms，和我們設的一致。注意這是**相對時間**：從提交那刻起算 200ms。

## timeout 的時鐘與絕對時間

`flags` 控制 timeout 的解讀方式，對照 6.18 的 `io_uring/timeout.c`：

| flag | 意義 |
|---|---|
| `0`（預設） | 相對時間，`CLOCK_MONOTONIC` |
| `IORING_TIMEOUT_ABS` | `ts` 是**絕對**時間點，不是「從現在起算多久」 |
| `IORING_TIMEOUT_BOOTTIME` | 用 `CLOCK_BOOTTIME`（含睡眠時間） |
| `IORING_TIMEOUT_REALTIME` | 用 `CLOCK_REALTIME`（牆上時鐘，會被改） |
| `IORING_TIMEOUT_MULTISHOT` | 週期性觸發，每次產一個帶 `IORING_CQE_F_MORE` 的 CQE |

kernel 端 `io_timeout_get_clock()` 就是一個照這些 flag 選時鐘的 switch：

```c
static clockid_t io_timeout_get_clock(struct io_timeout_data *data)
{
    switch (data->flags & IORING_TIMEOUT_CLOCK_MASK) {
    case IORING_TIMEOUT_BOOTTIME:  return CLOCK_BOOTTIME;
    case IORING_TIMEOUT_REALTIME:  return CLOCK_REALTIME;
    default:                       return CLOCK_MONOTONIC;
    }
}
```

**絕對時間**（`IORING_TIMEOUT_ABS`）的用途：你要「在 12:00:00 觸發」而不是「30 秒後觸發」。這時搭 `REALTIME` 時鐘給牆上時間，或搭 `MONOTONIC` 給一個 `clock_gettime(CLOCK_MONOTONIC)` + 偏移算出的絕對點。選時鐘的差別在「睡眠/改時間」時才顯現：`MONOTONIC` 不受系統時間調整影響、但機器睡眠時可能不前進；`BOOTTIME` 連睡眠都算；`REALTIME` 會被 NTP 或手動改時間影響。計時 I/O 一般用 `MONOTONIC`，排程到特定牆上時刻才用 `REALTIME`。

## count 計數式 timeout

timeout 還有一個少見但強大的用法：`count > 0` 時，它變成「**等 `count` 個（其他）CQE 完成，或時間到，誰先到**」的屏障。kernel 端 `timeout->off` 存這個 count，每有一個 I/O 完成就遞減。

這讓你能寫出「我丟了 10 個 read，最多等 1 秒，要嘛 10 個全好、要嘛 1 秒到就醒」的等待：

```c
io_uring_prep_timeout(sqe, &ts, 10, 0);   // 等 10 個 CQE 或 ts 時間
```

這比在 `io_uring_wait_cqe_nr` 上自己做時間管理乾淨：timeout 操作本身就是那個「醒來條件」，10 個完成時 timeout 被自動移除（不產 CQE），時間先到則產一個 `-ETIME`。它把「等 N 個完成」和「逾時」兩個條件合成一個操作。

## 範例二：linked timeout——給一個會卡住的 recv 設逾時

最實用的組合。把 `IORING_OP_LINK_TIMEOUT` 用 `IOSQE_IO_LINK` 綁在一個操作後面，就是「這個操作最多跑這麼久」。`io_uring_prep_link_timeout` 專門做這個，它**不能單獨用**，必須跟在一個 LINK 操作後面。

我們做一個會永遠卡住的 recv（socketpair 不送資料），給它 150ms 逾時：

```c
int sv[2]; socketpair(AF_UNIX, SOCK_STREAM, 0, sv);   // 不送資料，recv 會卡
char buf[16];
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv(sqe, sv[0], buf, sizeof(buf), 0);
sqe->flags |= IOSQE_IO_LINK;                 // 把下面的 timeout 綁給這個 recv
io_uring_sqe_set_data64(sqe, 200);

struct __kernel_timespec lt = { .tv_sec = 0, .tv_nsec = 150 * 1000000 };
sqe = io_uring_get_sqe(&ring);
io_uring_prep_link_timeout(sqe, &lt, 0);     // 專門給「鏈上前一個」設逾時
io_uring_sqe_set_data64(sqe, 201);
io_uring_submit(&ring);
```

實測輸出：

```
(2) link_timeout: res=-62 (Timer expired)
(2) recv        : res=-125 (Operation canceled)
    （linked timeout 在 150 ms 內收場）
```

兩個 CQE 一起理解：

- **link_timeout 自己** `res=-ETIME`（-62）：150ms 到了，它觸發了。
- **recv** `res=-ECANCELED`（-125）：因為它綁的 timeout 先觸發，kernel 把 recv 取消了。

反過來如果 recv **先完成**（對端在 150ms 內送了資料），則 recv 回正常結果、而 link_timeout 會被取消回 `-ECANCELED`——因為「要看守的操作已經好了，計時器沒必要了」。這是對稱的：**誰先到，另一個就被取消**。這正是「帶逾時的 recv」該有的語意，而且完全不用額外執行緒或 `SO_RCVTIMEO`。

> **故意弄壞它**：在 `io_uring_submit` 之前，往 `sv[1]` `write` 幾個 bytes。重跑會看到 recv 先完成（`res > 0`），而 link_timeout 變成 `-ECANCELED`——順序反過來了。親手看這個對稱性，你就懂 linked timeout 不是「單向的計時器」，而是「兩個操作賽跑、輸家被取消」。

## 範例三：async cancel——主動取消 in-flight 請求

linked timeout 是「預先設好時限」，但有時你要**事後**取消一個已經在跑的請求——例如使用者按了 cancel 按鈕、或連線要關了得把掛在上面的 recv 全撤掉。這是 `IORING_OP_ASYNC_CANCEL`。

`io_uring_prep_cancel64(sqe, user_data, flags)` 用 user_data 點名取消：

```c
// 先提交一個會卡住的 recv，標記 user_data=300
sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv(sqe, sv2[0], buf, sizeof(buf), 0);
io_uring_sqe_set_data64(sqe, 300);
io_uring_submit(&ring);               // 讓它 in-flight

// 再提交一個 cancel，取消 user_data == 300 的請求
sqe = io_uring_get_sqe(&ring);
io_uring_prep_cancel64(sqe, 300, 0);
io_uring_sqe_set_data64(sqe, 301);
io_uring_submit(&ring);
```

實測輸出：

```
(3) cancel: res=0 (ok)
(3) recv  : res=-125 (Operation canceled)
```

- **cancel** `res=0`：成功找到並取消了目標。
- **recv** `res=-ECANCELED`：被取消的目標回 `-ECANCELED`。

cancel 的 `res` 有三種常見值（對照 `cancel.c`）：`0` 取消成功、`-ENOENT`（找不到符合的請求，可能已經完成了）、`-EALREADY`（找到了但來不及取消，正在執行中）。寫 cancel 邏輯要能接受「我想取消但它已經完成了」——這不是錯誤，只是賽跑你輸了。

## cancel 的匹配方式：ALL / FD / ANY

`prep_cancel` 的 `flags` 決定「取消什麼」，對照 `cancel.c` 的 `CANCEL_FLAGS`：

| flag | 匹配方式 |
|---|---|
| `0`（預設） | 用 user_data 精確匹配**第一個** |
| `IORING_ASYNC_CANCEL_ALL` | 匹配**全部**符合的（不只第一個） |
| `IORING_ASYNC_CANCEL_FD` | 改用 **fd** 匹配（取消掛在某個 fd 上的） |
| `IORING_ASYNC_CANCEL_ANY` | 匹配**任何**請求（配 ALL 可清空整個 ring） |
| `IORING_ASYNC_CANCEL_FD_FIXED` | fd 是 fixed file 索引 |
| `IORING_ASYNC_CANCEL_OP` | 用 opcode 匹配 |

最實用的是 `CANCEL_FD | CANCEL_ALL`：一條連線要關了，把掛在那個 socket fd 上的所有 recv/send 一次全取消。liburing 的 `io_uring_prep_cancel_fd(sqe, fd, flags)` 專做這個：

```c
io_uring_prep_cancel_fd(sqe, sockfd, IORING_ASYNC_CANCEL_ALL);  // 取消這個 fd 上全部
```

`CANCEL_ANY | CANCEL_ALL` 則是「取消 ring 裡所有還沒完成的」，常用在 teardown。

## 底層機制：hrtimer 與取消查找

timeout 的實作核心是 kernel 的高解析度計時器 `hrtimer`。提交一個 timeout，kernel 起一個 `hrtimer`，到期時呼叫 `io_timeout_fn`（`timeout.c:254`）：

```c
static enum hrtimer_restart io_timeout_fn(struct hrtimer *timer)
{
    ...
    atomic_set(&req->ctx->cq_timeouts, atomic_read(&req->ctx->cq_timeouts) + 1);
    ...
    if (!(data->flags & IORING_TIMEOUT_ETIME_SUCCESS))
        req_set_fail(req);               // 預設把 timeout 當「失敗」（會斷鏈）
    io_req_set_res(req, -ETIME, 0);      // res = -ETIME
    req->io_task_work.func = io_timeout_complete;
    io_req_task_work_add(req);           // 丟給 task_work 產 CQE
    return HRTIMER_NORESTART;
}
```

兩個細節值得記：

- **`-ETIME` 從這裡來**，而且預設 `req_set_fail`——意思是 timeout 到期在鏈語意裡算「失敗」，會斷鏈（這正是 linked timeout 能取消前一個操作的機制基礎）。`IORING_TIMEOUT_ETIME_SUCCESS` 旗標可以讓 `-ETIME` 不算失敗。
- **CQE 不是在 timer 中斷裡直接產的**，而是透過 `task_work`（Ch 27 詳解）丟回提交 task 的上下文產出。這是 io_uring 完成路徑的通則：真正的完成動作盡量回到擁有這個 ring 的 task 上做。

cancel 則是在 ctx 的請求清單裡**查找**符合條件的 io_kiocb（`cancel.c` 的 `io_try_cancel`），找到就嘗試取消（對 poll 類是拆掉 poll、對 io-wq 類是標記 work cancel）。找不到回 `-ENOENT`、正在執行回 `-EALREADY`。這解釋了為什麼 cancel 不保證成功：它本質是「盡力而為」，和目標的完成在賽跑。

## 對比與取捨

| 需求 | 用什麼 |
|---|---|
| 純計時器 / 週期觸發 | `prep_timeout`（`count=0` / `MULTISHOT`） |
| 等 N 個完成或逾時 | `prep_timeout`（`count=N`） |
| 給某個操作設時限 | `prep_link_timeout` + `IOSQE_IO_LINK` |
| 事後取消某個請求 | `prep_cancel64`（user_data） |
| 關連線時清掉其上所有 I/O | `prep_cancel_fd`（`CANCEL_FD\|CANCEL_ALL`） |
| teardown 清空整個 ring | `prep_cancel`（`CANCEL_ANY\|CANCEL_ALL`） |

## 踩雷集錦

1. **把 `-ETIME` 當成 `-ETIMEDOUT`**：timeout 到期是 **`-ETIME`（62）**，不是 `-ETIMEDOUT`（110）。判斷逾時要比對 `-ETIME`。`connect` 之類操作自己的逾時可能回 `-ETIMEDOUT`，但 io_uring timeout 操作統一是 `-ETIME`。

2. **linked timeout 單獨提交**：`prep_link_timeout` 必須跟在一個掛 `IOSQE_IO_LINK` 的操作後面。單獨提交會回 `-EINVAL`——它天生是「看守前一個」的，沒有前一個就沒意義。

3. **以為 cancel 一定成功**：cancel 和目標完成在賽跑。`-ENOENT`（找不到，可能剛完成）、`-EALREADY`（正在執行來不及）都是正常結果，不是 bug。cancel 邏輯要能吞下這兩個。

4. **相對 vs 絕對搞混**：預設是相對時間（從提交起算）。要「在某個時間點」觸發得加 `IORING_TIMEOUT_ABS`，否則你的絕對時間戳會被當成「從現在起算這麼多秒」，等到天荒地老。

5. **linked timeout 的 CQE 數量算錯**：一個 recv + 一個 link_timeout 會產**兩個** CQE（一個被取消、一個 `-ETIME`）。收割迴圈要等兩個，少等一個會漏收、CQ 卡住。

## 進階：timeout update 與 multishot timeout

timeout 可以**原地更新**而不取消重建：`io_uring_prep_timeout_update`（底層 `IORING_OP_TIMEOUT_REMOVE` + `IORING_TIMEOUT_UPDATE`）改掉一個已提交 timeout 的到期時間。用在「續命」場景——例如 idle timeout，每收到一個封包就把逾時往後延，比「取消舊的、建新的」省一次完整的提交/完成。

`IORING_TIMEOUT_MULTISHOT`（6.4+）讓一個 timeout 變成**週期性**：每隔 `ts` 觸發一次，各產一個帶 `IORING_CQE_F_MORE` 的 `-ETIME` CQE，直到你取消它。這是 io_uring 原生的「interval timer」，可以取代 `timerfd` + poll 的組合——一個 SQE 提交，之後週期性在 CQ 收到 tick，省掉 timerfd 那個額外的 fd 與讀取。kernel 端就是 `io_timeout_fn` 裡 `hrtimer_start` 重新武裝計時器（`timeout.c:82`）。

## 本章重點整理

- timeout 本身是一個 io_uring 操作，到期產一個 `res=-ETIME`（62，非 `-ETIMEDOUT`）的 CQE。`prep_timeout(sqe, &ts, count, flags)`：`count=0` 純計時，`count=N` 等 N 個完成或逾時。
- flags 選時鐘與語意：預設相對 + `MONOTONIC`；`IORING_TIMEOUT_ABS` 絕對時間、`BOOTTIME`/`REALTIME` 換時鐘、`MULTISHOT` 週期觸發。
- linked timeout（`prep_link_timeout` + `IOSQE_IO_LINK`）給前一個操作設時限：誰先到另一個就被取消。必須跟在 LINK 操作後，不能單獨用。
- async cancel（`prep_cancel64`）事後取消 in-flight 請求：`res` 為 `0`（成功）/`-ENOENT`（找不到）/`-EALREADY`（來不及）。flags 控制匹配：`ALL`/`FD`/`ANY`/`OP`。清連線用 `prep_cancel_fd` + `CANCEL_ALL`。
- 底層：timeout 用 `hrtimer`，到期在 `io_timeout_fn` 設 `-ETIME` 並預設 `req_set_fail`（所以能斷鏈），CQE 透過 task_work 產出；cancel 是在請求清單查找、盡力取消，和目標完成在賽跑。

## 自我檢核

- [ ] 我知道 timeout 到期的 `res` 是 `-ETIME` 而非 `-ETIMEDOUT`，以及兩者的值
- [ ] 我能解釋 count 計數式 timeout 的語意（等 N 個完成或時間到）
- [ ] 我能說出 linked timeout 的對稱性：recv 先好 vs timeout 先到，分別誰被取消
- [ ] 我知道 cancel 的三種回傳值各代表什麼，以及為什麼 cancel 不保證成功
- [ ] 我能選對時鐘與相對/絕對：計時 I/O 用哪個、排程到牆上時刻用哪個

## 延伸閱讀

### man page

- **[io_uring_prep_timeout(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_timeout.3.html)** 與 **[io_uring_prep_link_timeout(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_link_timeout.3.html)**
  - **讀哪裡**：timeout 的 `count` 與 flags 一覽、link_timeout 的使用限制（必須跟在 LINK 後）
  - **學什麼**：每個 flag 的精確語意與 VERSIONS 段（`MULTISHOT` 何時加入）

- **[io_uring_prep_cancel(3)](https://man7.org/linux/man-pages/man3/io_uring_prep_cancel.3.html)**
  - **讀哪裡**：`IORING_ASYNC_CANCEL_*` 各旗標、三種回傳值
  - **學什麼**：怎麼用 FD + ALL 一次清掉一個連線上的所有請求

### kernel 原始碼

- **[`io_uring/timeout.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/timeout.c)**（Linux 6.18）
  - **讀哪裡**：`io_timeout_fn`、`io_link_timeout_fn`、`io_timeout_get_clock`
  - **學什麼**：timeout 怎麼用 hrtimer 實作、`-ETIME` 與 `req_set_fail` 的關係（為什麼 linked timeout 能斷鏈）；Part 4 的 Ch 35 會拆一個 timeout/poll race UAF

- **[`io_uring/cancel.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/cancel.c)**（Linux 6.18）
  - **讀哪裡**：`io_try_cancel`、`io_cancel_req_match`、`CANCEL_FLAGS`
  - **學什麼**：cancel 的匹配與「盡力而為」本質，`-ENOENT`/`-EALREADY` 從哪來

### 部落格

- **[Lord of the io_uring — Timeouts](https://unixism.net/loti/tutorial/timeouts.html)** — Shuveb Hussain
  - **這篇說什麼**：timeout 與 linked timeout 的基本用法示範
  - **讀哪裡**：整頁；對照本章的 count 式 timeout 與 linked timeout 對稱性
  - **前提**：讀過 Ch 16 的 link

逾時與取消讓 io_uring 能管住「永遠不完成」的操作。下一章把 io_uring 和它之外的世界接起來：poll、eventfd、futex、msg_ring——讓 io_uring 能當 epoll 用、能通知既有的 event loop、能跨 ring 傳訊。

→ [Ch 18 跨機制整合：poll、eventfd、futex、msg_ring](./18-cross-mechanism.md)
