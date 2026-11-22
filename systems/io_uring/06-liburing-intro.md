# Ch 6 — liburing 入門：把 raw 版本改寫成 liburing

> **目標**：把 Ch 2 那支手刻 raw ring 的程式，用 liburing 一步一步重寫，兩版並排對照。看完你會確信一件事：liburing 不是魔法，它就是把 Ch 2–5 的 ring 操作、offset 定位、memory barrier 包成一組命名規律的函式。之後你每呼叫一個 `io_uring_xxx()`，心裡都有 raw 版本對應的那幾行。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4。本章起全部連結自編的 liburing，用 Ch 0 的 `iou-cc` 腳本編譯。

## 為什麼要有 liburing，又為什麼先學 raw

Ch 2 我們手刻了一遍：`io_uring_setup` 拿 fd、三次 `mmap`、用 `sq_off`/`cq_off` 定位欄位、填 SQE、store-release 推 tail、`io_uring_enter`、acquire 收 CQE、release 推 head。八十行，一個 read。

這八十行裡，真正和「我想讀一個檔案」有關的只有四行（opcode、fd、addr、len）。其餘全是**樣板**：記憶體佈局、barrier、索引折算。每支 io_uring 程式都要重寫一遍這些樣板，而且每一步都有踩錯的空間——漏一次 mmap 就 segfault、barrier 放錯就是偶發競態（Ch 2 踩雷集錦第 3 條）。

liburing 就是 Jens Axboe 本人寫的那層封裝，把樣板收進函式庫，只留下和「你要做什麼」有關的介面。它刻意做得**薄**：沒有自己的事件迴圈、沒有執行緒池、沒有隱藏的記憶體配置策略。它封裝的東西和 Ch 2 一一對得上，這也是為什麼本課堅持先手刻 raw——否則 liburing 對你就是另一個黑盒。

> 本章的策略是「兩欄對照」：左邊是 Ch 2 的 raw 做法，右邊是 liburing。每個 helper 我們都點出它對應 raw 版本的哪幾行。你會發現沒有任何一步是憑空多出來的。

## 先建立直覺：liburing 的狀態都裝在 `struct io_uring`

Ch 2 裡，ring 的狀態散落在好幾個區域性變數：`sq_ptr`、`cq_ptr`、`sqes`、`sring_tail`、`cring_head`……你得自己拎著它們到處傳。liburing 把這些全部收進一個結構：

```
struct io_uring
├── struct io_uring_sq sq   ← SQ 環：kring 的 head/tail 指標、mask、sqes 陣列基底、本地 sqe_tail
├── struct io_uring_cq cq   ← CQ 環：kring 的 head/tail 指標、mask、cqes 陣列基底
├── int ring_fd             ← io_uring_setup 回來的那個 fd
├── unsigned flags
└── ...
```

你宣告一個 `struct io_uring ring;`，之後每個 liburing 函式第一個參數都是 `&ring`。這個結構就是 Ch 2 那一堆散落指標的集合體。`io_uring_queue_init` 負責把它填好，`io_uring_queue_exit` 負責拆掉。

兩個結構你會天天碰，它們**就是** uapi 定義的那兩個（Ch 2 已經填過裸的版本）：

```c
struct io_uring_sqe {      // Submission Queue Entry：你要 kernel 做什麼
    __u8  opcode;          // IORING_OP_READ / WRITE / ...
    __u8  flags;           // IOSQE_* （Ch 7）
    __u16 ioprio;
    __s32 fd;
    /* off / addr / len / user_data / ... 一大堆 union 欄位，Ch 7 逐欄拆 */
};

struct io_uring_cqe {      // Completion Queue Entry：kernel 回給你的結果
    __u64 user_data;       // 你當初在 SQE 設的，原樣帶回
    __s32 res;             // 操作回傳值，失敗為負 errno（Ch 8）
    __u32 flags;           // IORING_CQE_F_*（Ch 8）
};
```

liburing 的 `io_uring_prep_*` helper 做的事，就是幫你把 SQE 那堆欄位填對；`io_uring_wait_cqe` 做的事，就是幫你把 CQE 從環裡取出來。欄位的意義和 raw 完全一樣，Ch 7、Ch 8 專章拆解。

## 一行一行改寫 Ch 2

我們把 Ch 2 讀 `/etc/hostname` 的那支 raw 程式整支搬成 liburing。先看完整版，再逐段對照。

```c
// ch06_liburing.c
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <liburing.h>

int main(void) {
    struct io_uring ring;
    int ret = io_uring_queue_init(8, &ring, 0);          // ← setup + 3× mmap + 定位
    if (ret < 0) { fprintf(stderr, "queue_init: %s\n", strerror(-ret)); return 1; }

    int fd = open("/etc/hostname", O_RDONLY);
    char buf[64] = {0};

    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);  // ← 拿 tail 槽、推本地 sqe_tail
    io_uring_prep_read(sqe, fd, buf, sizeof(buf) - 1, 0);// ← 填 opcode/fd/addr/len/off
    sqe->user_data = 0x1234;                             // ← 身分標籤，CQE 原樣帶回

    io_uring_submit(&ring);                              // ← store-release 推 tail + enter

    struct io_uring_cqe *cqe;
    ret = io_uring_wait_cqe(&ring, &cqe);                // ← enter(GETEVENTS) + acquire 讀 tail
    if (ret < 0) { fprintf(stderr, "wait_cqe: %s\n", strerror(-ret)); return 1; }

    printf("CQE: user_data=0x%llx res=%d\n",
           (unsigned long long)cqe->user_data, cqe->res);
    if (cqe->res > 0)
        printf("read %d bytes: %.*s", cqe->res, cqe->res, buf);

    io_uring_cqe_seen(&ring, cqe);                       // ← release 推 CQ head，回收槽
    io_uring_queue_exit(&ring);                          // ← munmap + close(ring_fd)
    return 0;
}
```

編譯執行：

```bash
iou-cc ch06_liburing.c -o ch06_liburing
./ch06_liburing
```

實測輸出（kernel 6.18）：

```
CQE: user_data=0x1234 res=16
read 16 bytes: DESKTOP-JK2JTGG
```

和 Ch 2 的 raw 版本輸出**一模一樣**（`res=16`、`user_data=0x1234` 原樣回來）——這正是重點：行為沒變，只是樣板沒了。下面逐個函式對照它藏起了 Ch 2 的哪一步。

### `io_uring_queue_init` ＝ setup ＋ 三次 mmap ＋ 定位欄位

```c
int io_uring_queue_init(unsigned entries, struct io_uring *ring, unsigned flags);
```

這一行取代了 Ch 2 的：`io_uring_setup(8, &p)` + 三次 `mmap`（SQ 環、CQ 環、SQES 陣列）+ 七八行 `sq_ptr + p.sq_off.tail` 這種 offset 定位。它內部還幫你判斷了 `IORING_FEAT_SINGLE_MMAP`（把兩環併一次 mmap），這也是 Ch 2 我們手寫 `if (p.features & IORING_FEAT_SINGLE_MMAP)` 的那段。

- `entries`：SQ 深度，會被 kernel 向上取到 2 的次方（跟 `io_uring_setup` 一樣）。
- `flags`：就是 `io_uring_params.flags`，`IORING_SETUP_SQPOLL` 這些填這裡（Ch 9）。這裡填 `0`，等同 Ch 2 的 `memset(&p, 0, ...)`。
- 回傳 `0` 成功，失敗是**負的 errno**（`-ENOMEM`、`-EPERM`……）。

> 若要細調 `io_uring_params`（例如指定 CQ 大小、SQPOLL 的 CPU），用 `io_uring_queue_init_params(entries, ring, &params)`，它多收一個你自己填好的 `params`。`io_uring_queue_init` 等於「`params` 全 0 只帶 flags」的便捷版。

### `io_uring_get_sqe` ＝ 用 tail 取槽、推進本地 tail

```c
struct io_uring_sqe *io_uring_get_sqe(struct io_uring *ring);
```

對應 Ch 2 的 `unsigned tail = *sring_tail; unsigned index = tail & *sring_mask; sqe = &sqes[index];`。它回傳一個**乾淨可填**的 SQE 指標，並在內部把一個「本地 tail」（`sq.sqe_tail`）往前推一格——注意這時還**沒有**通知 kernel，只是在 user 這端佔住一個槽。

關鍵陷阱：**環滿了會回 `NULL`**。你已經填了 N 個 SQE 還沒 submit，而 SQ 深度只有 N，第 N+1 次 `get_sqe` 就拿到 `NULL`。生產程式一定要檢查：

```c
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
if (!sqe) {
    io_uring_submit(&ring);              // 先把積著的送出去，騰出槽
    sqe = io_uring_get_sqe(&ring);       // 再拿一次
}
```

本章範例深度 8、只填 1 個，不會滿，先省略檢查；但這是真實程式最常見的隱形 bug 之一，Ch 9 批次提交時會正面處理。

### `io_uring_prep_read` ＝ 手填那六個 SQE 欄位

```c
void io_uring_prep_read(struct io_uring_sqe *sqe, int fd,
                        void *buf, unsigned nbytes, __u64 offset);
```

這一行取代 Ch 2 的六行手填：

| Ch 2 raw | liburing prep |
|---|---|
| `sqe->opcode = IORING_OP_READ;` | 由 `prep_read` 設好 |
| `sqe->fd = fd;` | 第 2 參數 |
| `sqe->addr = (unsigned long)buf;` | 第 3 參數 |
| `sqe->len = nbytes;` | 第 4 參數 |
| `sqe->off = offset;` | 第 5 參數 |
| `memset(sqe, 0, ...)` 其餘歸零 | `prep_read` 內部先清掉無關欄位 |

每個 op 都有對應的 `io_uring_prep_<op>`：`prep_write`、`prep_readv`、`prep_openat`、`prep_accept`、`prep_send`……命名規律就是 `io_uring_prep_` + op 名。查一個 op 怎麼填，直接 grep `liburing.h` 的 `io_uring_prep_<op>` 看它的參數，比翻 man page 快（Ch 0 延伸閱讀提過這招）。

注意 `prep_read` **不會**幫你設 `user_data`——那是你的事。上面我們手動 `sqe->user_data = 0x1234;`，下一節會看到更慣用的 `io_uring_sqe_set_data`。

### `io_uring_submit` ＝ store-release 推 tail ＋ `io_uring_enter`

```c
int io_uring_submit(struct io_uring *ring);
```

這一行是 Ch 2 的靈魂兩步合一：先把 `get_sqe` 期間累積的本地 tail，用 **store-release** 真正發佈到 kernel 可見的 SQ tail（Ch 2 那行 `atomic_store_explicit(..., memory_order_release)`），再呼叫 `io_uring_enter` 通知 kernel「有 N 個新請求」。回傳值是**實際提交的 SQE 數**。

barrier 放在哪、用 release 還是普通寫入——這些 Ch 5 讓你流了汗的細節，全在 `io_uring_submit` 內部處理好了。這是 liburing 最有價值的部分：**把 memory ordering 的正確性收進函式庫**，你只要呼叫對的函式，順序就是對的。

在 SQPOLL 模式下（Ch 9），`io_uring_submit` 內部會判斷 kernel 的輪詢 thread 是否需要喚醒（`IORING_SQ_NEED_WAKEUP`），需要才真的下 `io_uring_enter`——這也是為什麼它能做到「常態零 syscall」。你用同一個 `io_uring_submit`，底層自動適配。

### `io_uring_wait_cqe` ＋ `io_uring_cqe_seen` ＝ 收 CQE 的兩步

```c
int  io_uring_wait_cqe(struct io_uring *ring, struct io_uring_cqe **cqe_ptr);
void io_uring_cqe_seen(struct io_uring *ring, struct io_uring_cqe *cqe);
```

`io_uring_wait_cqe` 對應 Ch 2 的「`io_uring_enter(..., GETEVENTS)` 阻塞 + acquire 讀 CQ tail + 取出 `&cqes[head & mask]`」。它回傳 `0` 並讓 `*cqe_ptr` 指向環裡那個 CQE；沒有可用 CQE 時它會下 `io_uring_enter` 阻塞等。回傳負值代表等的過程本身出錯（例如被 signal 打斷 `-EINTR`）——注意這**不是**操作失敗，操作的成敗在 `cqe->res`（Ch 8 會把這兩種錯誤分清楚）。

`io_uring_cqe_seen` 對應 Ch 2 的「release 推 CQ head」。它告訴 kernel「這個 CQE 我讀完了，槽可以回收」。**漏掉它，CQ 槽不回收，很快 overflow**（Ch 2 踩雷第 6 條、Ch 8 深入）。

一個重要細節：`io_uring_cqe_seen` 內部呼叫的是 `io_uring_cq_advance(ring, io_uring_cqe_nr(cqe))`——它推進的格數是「這個 CQE 佔幾個環槽」。一般 CQE 佔 1 格；若 ring 開了 32-byte CQE（big CQE，Ch 8），佔 2 格，`cqe_seen` 自動幫你算對。

### `io_uring_queue_exit` ＝ munmap ＋ close

```c
void io_uring_queue_exit(struct io_uring *ring);
```

對應 Ch 2 收尾該做（但範例偷懶省略）的 `munmap` 三塊 + `close(ring_fd)`。程式結束 kernel 也會回收，但長命程式一定要配對呼叫，否則 ring 的 pinned 記憶體不還。

## 對照全貌：raw 的每一步落在哪個 helper

| Ch 2 raw（手工） | Ch 6 liburing | 封裝了什麼 |
|---|---|---|
| `io_uring_setup` + 3× `mmap` + offset 定位 | `io_uring_queue_init(8, &ring, 0)` | ABI offset、SINGLE_MMAP 判斷 |
| `tail & mask` 取 `&sqes[index]` | `io_uring_get_sqe(&ring)` | 本地 tail、環滿回 `NULL` |
| 手填 `opcode/fd/addr/len/off` ×6 | `io_uring_prep_read(sqe, ...)` | 欄位填寫、無關欄位歸零 |
| store-release 推 tail | `io_uring_submit(&ring)` | **memory barrier** + `enter` |
| `enter(GETEVENTS)` + acquire 讀 tail | `io_uring_wait_cqe(&ring, &cqe)` | 阻塞等待 + acquire |
| release 推 CQ head | `io_uring_cqe_seen(&ring, cqe)` | release barrier + 槽回收 |
| `munmap` ×3 + `close` | `io_uring_queue_exit(&ring)` | 資源釋放 |

沒有一列是憑空多出來的。liburing 就是這張表的右欄，barrier 的正確性收在 `submit`/`wait_cqe`/`cqe_seen` 三處。

## 批次提交與批次收割：liburing 真正省事的地方

一個請求配一個 `submit` + `wait_cqe` 看不出 liburing 的威力。io_uring 的設計初衷是**批次**：填一堆 SQE、一次 submit、一次收一串 CQE。這時 liburing 的便捷函式才顯出價值。

下面這支一次提交 3 個 read（其中一個故意讓它失敗），用 `io_uring_submit_and_wait` 一次提交且等齊，再用 `io_uring_for_each_cqe` 一趟收割。順便示範 `io_uring_sqe_set_data` / `io_uring_cqe_get_data`——把 `user_data` 當**指標**用的慣用法。

```c
// ch06_batch.c（節錄，完整檔見本章附帶原始碼）
struct job { int id; const char *path; int fd; char buf[64]; };

struct job jobs[3] = {
    { .id = 0, .path = "/etc/hostname" },
    { .id = 1, .path = "/proc/sys/kernel/ostype" },
    { .id = 2, .path = "/etc/does-not-exist" },   // 故意失敗
};

for (int i = 0; i < 3; i++) {
    jobs[i].fd = open(jobs[i].path, O_RDONLY);    // 失敗時 fd == -1
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, jobs[i].fd, jobs[i].buf, sizeof(jobs[i].buf) - 1, 0);
    io_uring_sqe_set_data(sqe, &jobs[i]);         // user_data 存「這個 job 的指標」
}

int ret = io_uring_submit_and_wait(&ring, 3);     // 一次提交 3 個、等到 3 個完成
printf("submit_and_wait returned %d\n", ret);

struct io_uring_cqe *cqe;
unsigned head, count = 0;
io_uring_for_each_cqe(&ring, head, cqe) {
    struct job *j = io_uring_cqe_get_data(cqe);   // 取回指標，立刻知道是哪個 job
    if (cqe->res < 0)
        printf("  job %d (%s): error %s\n", j->id, j->path, strerror(-cqe->res));
    else
        printf("  job %d (%s): %d bytes = %.*s", j->id, j->path,
               cqe->res, cqe->res, j->buf);
    count++;
}
io_uring_cq_advance(&ring, count);                // 一次回收 count 個槽
```

實測輸出：

```
submit_and_wait returned 3
  job 0 (/etc/hostname): 16 bytes = DESKTOP-JK2JTGG
  job 2 (/etc/does-not-exist): error Bad file descriptor
  job 1 (/proc/sys/kernel/ostype): 6 bytes = Linux
```

三件事值得盯著看：

**一、完成順序不等於提交順序。** 我們按 0、1、2 提交，收回來卻是 0、2、1——失敗的 job 2 比 job 1 先完成。io_uring **不保證**完成順序和提交順序一致（要順序得用 linked ops，Ch 16）。所以 `user_data` 不能靠「第幾個」來認，必須靠你自己塞進去的標籤。這就是 `set_data`/`get_data` 存在的理由。

**二、`io_uring_sqe_set_data` / `io_uring_cqe_get_data` 就是 `user_data` 的型別安全包裝。** 看它們的實作：

```c
// liburing.h，去掉樣板
void  io_uring_sqe_set_data(struct io_uring_sqe *sqe, void *data) { sqe->user_data = (unsigned long)data; }
void *io_uring_cqe_get_data(const struct io_uring_cqe *cqe)       { return (void *)(uintptr_t)cqe->user_data; }
```

就是把 `user_data` 轉成指標存、取回來再轉回指標。要存**整數 ID** 而非指標時，用 `io_uring_sqe_set_data64` / `io_uring_cqe_get_data64`（直接存 `__u64`，不做指標轉換，避免 32 位平台上的截斷）。慣例：請求對應一個堆上物件就塞指標，對應一個索引就塞 ID。

**三、`for_each_cqe` + `cq_advance` 是批次收割的標準姿勢。** 下一節拆。

### `io_uring_submit_and_wait`、`for_each_cqe`、`cq_advance`、`peek_cqe`

**`io_uring_submit_and_wait(ring, wait_nr)`** 把「提交」和「等 `wait_nr` 個完成」併成**一次** `io_uring_enter`。等價於 `io_uring_submit` 後再 `io_uring_wait_cqe_nr(ring, &cqe, wait_nr)`，但省一次 syscall（提交和等待在同一次 enter 完成）。高頻 event loop 用它，不用 `submit` + `wait` 兩步。

**`io_uring_for_each_cqe(ring, head, cqe)`** 是一個 macro，展開成一個 for 迴圈，走訪**目前環上所有可見的 CQE**（從 head 到 kernel 已發佈的 tail）。它不逐個推進 head，只是迭代——所以迴圈裡你**不呼叫** `cqe_seen`，而是迴圈結束後一次 `io_uring_cq_advance(ring, count)` 把 head 推 `count` 格。這比每個 CQE 各 `cqe_seen`（各一次 release store）省很多：**一次 release 回收一批**。

```c
// cqe_seen vs cq_advance，底層都是推 CQ head，差在粒度
io_uring_cqe_seen(ring, cqe);        // = io_uring_cq_advance(ring, cqe 佔的格數)，收「一個」
io_uring_cq_advance(ring, count);    // 收「一批」count 個，只做一次 release store
```

規則：**逐個處理用 `cqe_seen`；批次收割用 `for_each_cqe` + 最後一次 `cq_advance`**。兩者別混用（`for_each_cqe` 迴圈裡又 `cqe_seen` 會把 head 推過頭）。

**`io_uring_peek_cqe(ring, &cqe)`** 和 `wait_cqe` 的差別是**不阻塞**：有 CQE 就回 `0` 並指好，沒有就回 `-EAGAIN`，絕不睡。要寫「先把現成的收光、沒了再去等」的 event loop，就用 `peek` 探、`wait` 等：

```c
// 典型 event loop 骨架
while (running) {
    io_uring_submit_and_wait(&ring, 1);          // 提交並至少等 1 個
    unsigned head, count = 0;
    io_uring_for_each_cqe(&ring, head, cqe) {    // 把現在環上所有的一次收光
        handle(cqe);
        count++;
    }
    io_uring_cq_advance(&ring, count);           // 一次回收
}
```

這就是 Part 2 的伺服器範例（練習 B 的 echo server）反覆用的結構。先在腦中有這個骨架。

## 底層機制：liburing 到底薄在哪

liburing 之所以能這麼薄，是因為它**不接管控制流**。對照一下它和「重」的非同步框架的差別：

- 它**不**有自己的 event loop——`for_each_cqe` 只是個迴圈 macro，迴圈是你寫的。
- 它**不**開執行緒——非同步是 kernel 側 io-wq / poll 做的（Ch 26、28），不是 liburing 開 thread。
- 它**幾乎不**配置記憶體——ring 是 `io_uring_queue_init` 時一次 mmap 的，`get_sqe`/`wait_cqe` 都是在這塊既有記憶體裡遞指標，熱路徑**零 malloc**。
- 它的 `submit`/`wait_cqe`/`cqe_seen` 大多時候只是在共享記憶體上讀寫 head/tail 加 barrier，**連 syscall 都未必有**（SQPOLL、或 CQ 裡已有現成 CQE 時）。

換句話說，liburing 封裝的是**記憶體佈局與 memory ordering**這兩件最容易錯、又和你的業務邏輯無關的事，其餘的控制權都留給你。這也是為什麼它可以同時服務「我只想讀個檔案」和「我要寫一個每秒數百萬請求的 proxy」——它不替你決策，只把 ring 的危險部分收好。

> 想親眼確認它多薄？`strace -e io_uring_enter ./ch06_batch` 看它實際下了幾次 `io_uring_enter`——`submit_and_wait` 一個請求批次只對應一次。再對照 Ch 2 你會更有感：同樣的事，raw 版本你手寫了 barrier，liburing 把 barrier 藏進那一次 enter 前後。

## 對比與取捨：什麼時候該退回 raw

| 面向 | raw syscall（Ch 2） | liburing（本章起） |
|---|---|---|
| 程式碼量 | 一個 read 要 ~80 行樣板 | ~15 行 |
| memory barrier | 你自己放，放錯是偶發競態 | 收在 `submit`/`wait_cqe`/`cqe_seen` |
| 環滿 / NULL 處理 | 手算 head/tail 差 | `get_sqe` 回 `NULL` 提示 |
| 版本相容 | offset 用對就好 | 同上，且自動判斷 FEAT flags |
| 可控性 | 完全掌握每個 store | 多數情況足夠，極端調校時想繞過 |
| 依賴 | 只需 `<linux/io_uring.h>` | 要連結 liburing |

結論：**日常一律用 liburing**。退回 raw 的理由只有兩類——(1) 學習／除錯時想看清底層到底發生什麼（本課 Part 3 讀 kernel 原始碼時會再回去）；(2) 極端場景要掌握每一個 store 的時機、連 liburing 那層薄封裝的開銷都要省（罕見，通常是 benchmark 或特殊嵌入式）。99% 的情況，liburing 的開銷可忽略，而它省掉的錯誤遠多於它帶來的抽象。

## 踩雷集錦

1. **`io_uring_get_sqe` 回 `NULL` 不檢查**：環滿了它回 `NULL`，你照樣 `io_uring_prep_read(NULL, ...)` 就是對 `NULL` 解參考 → segfault。填 SQE 前務必檢查，滿了先 `submit` 騰空間。本章單請求範例沒滿才省略，Ch 9 批次時正面處理。
2. **把 `wait_cqe` 的回傳值當操作結果**：`io_uring_wait_cqe` 回 `0`/負值講的是「取 CQE 這件事」成不成（`-EINTR` 是被 signal 打斷），**操作本身的成敗在 `cqe->res`**。兩者是兩件事，混淆會把「讀失敗」和「等待被打斷」搞錯（Ch 8 詳述）。
3. **`for_each_cqe` 迴圈裡又呼叫 `cqe_seen`**：兩種收割模型混用，head 被推過頭，之後讀到已回收或根本不存在的 CQE。批次就只在迴圈後 `cq_advance(count)` 一次。
4. **收了 CQE 忘了 `cqe_seen` / `cq_advance`**：head 不動，槽不回收，CQ 很快 overflow，新完成的 CQE 被擠掉或進 overflow list（Ch 8）。「收一個就 seen 一個，收一批就 advance 一批」要成反射。
5. **忘了 `io_uring_queue_exit`**：短命程式看似沒事（行程結束 kernel 回收），但長命程式（伺服器）每建一個 ring 不 exit，pinned 記憶體一路漏。`queue_init` 和 `queue_exit` 要像 malloc/free 一樣配對。
6. **以為 `io_uring_submit` 之後 SQE 的 buffer 就能動**：不行。read 的目標 buffer 要活到**對應的 CQE 收到**為止，不是 submit 之後。這是 completion 模型和同步 `read()` 最大的心智差異（Ch 1 提過、Ch 8 和 Part 2 會反覆踩）。submit 只是「送出請求」，不是「做完了」。

## 進階：還有哪些收割與提交變體

本章用到的是最常見那幾個，liburing 還有一系列同族函式，先知道它們存在，用到再查：

- **`io_uring_wait_cqe_nr(ring, &cqe, nr)`**：阻塞到至少 `nr` 個完成，`&cqe` 指向第一個。`submit_and_wait` 內部就是它。
- **`io_uring_wait_cqe_timeout(ring, &cqe, ts)`**：等待帶逾時，時間到回 `-ETIME`。底層用 `IORING_ENTER_EXT_ARG`（Ch 9 的 GETEVENTS 擴充），不用再額外塞一個 timeout SQE。
- **`io_uring_peek_batch_cqe(ring, cqes, count)`**：一次把最多 `count` 個 CQE 指標填進你的陣列，不阻塞，回實際拿到幾個。要自己管一個 CQE 指標陣列時用。
- **`io_uring_cq_ready(ring)`**：不取、只問「現在環上有幾個可收的 CQE」。寫排程決策（收多少再處理）時有用。
- **`io_uring_sq_space_left(ring)`**：問 SQ 還能再塞幾個 SQE，批次填之前先看，避免撞 `NULL`。

這些都只是在同一塊共享記憶體上讀寫 head/tail 的不同包裝，沒有新機制。摸熟 `get_sqe`/`prep_*`/`submit`/`wait_cqe`/`cqe_seen` 這條主幹，其餘查 `liburing.h` 即可。

## 本章重點整理

- liburing 不是魔法，它把 Ch 2 的 raw ring 操作一一包成命名規律的函式；**封裝的核心是記憶體佈局與 memory ordering 的正確性**。
- 對照主幹：`queue_init`（setup+mmap+定位）→ `get_sqe`（取槽，滿回 `NULL`）→ `prep_<op>`（填 SQE）→ `submit`（release 推 tail + enter）→ `wait_cqe`（阻塞取 CQE）→ `cqe_seen`（release 推 head）→ `queue_exit`。
- `struct io_uring` 把 Ch 2 散落的指標收成一個結構；`io_uring_sqe`/`io_uring_cqe` 就是 Ch 2 填過的那兩個 uapi 結構。
- 批次：`submit_and_wait` 一次提交並等待、`for_each_cqe` + `cq_advance` 一趟收割一批、`peek_cqe` 不阻塞探測。逐個用 `cqe_seen`，成批用 `cq_advance`，別混。
- `user_data` 用 `sqe_set_data`/`cqe_get_data`（存指標）或 `_data64`（存 ID）包裝；**完成順序不保證等於提交順序**，必須靠 `user_data` 認請求。

## 自我檢核

- [ ] 我能把 Ch 2 raw 版本的每一步，對應到 liburing 的哪個函式，並說出該函式藏了哪個 barrier
- [ ] 我知道 `io_uring_get_sqe` 什麼時候回 `NULL`，以及該怎麼處理
- [ ] 我能解釋 `io_uring_submit` 和 `io_uring_submit_and_wait` 差在哪、各省幾次 syscall
- [ ] 我能說清 `cqe_seen` 和 `cq_advance` 的關係，以及 `for_each_cqe` 收割後為什麼用後者
- [ ] 我知道完成順序不保證等於提交順序，以及為什麼這讓 `user_data` 變成必要而非方便
- [ ] 我能解釋為什麼 read 的 buffer 要活到 CQE 收到、而不是 submit 之後

## 延伸閱讀

### 官方文件 / 原始碼

- **[liburing.h 原始碼](https://github.com/axboe/liburing/blob/master/src/include/liburing.h)**
  - **讀哪裡**：`io_uring_get_sqe`、`io_uring_submit`、`io_uring_for_each_cqe`、`io_uring_cqe_seen` 這幾個 inline 函式的本體——它們短到可以整段讀完，是確認「liburing 到底做了什麼」最直接的來源
  - **為什麼值得讀**：所有 `io_uring_prep_*` 命名規律、`set_data`/`get_data` 的指標轉換，看原始碼五分鐘勝過翻文件半小時

- **[io_uring_queue_init(3)](https://man7.org/linux/man-pages/man3/io_uring_queue_init.3.html)** 與 **[io_uring_submit(3)](https://man7.org/linux/man-pages/man3/io_uring_submit.3.html)**
  - **讀哪裡**：`io_uring_queue_init` 的 flags 一覽、回傳值語意；man3 區整個就是 liburing 的 API 參考
  - **注意**：man3（liburing）和 man2（raw syscall）是兩套，查 helper 行為看 man3

### 教學文章

- **[Lord of the io_uring — liburing 篇](https://unixism.net/loti/tutorial/index.html)** — Shuveb Hussain
  - **這篇說什麼**：把 raw 版本改寫成 liburing 的對照教學，和本章同一條思路，可互相印證
  - **讀哪裡**：`cat` 與 `cp` 的 liburing 範例；它的錯誤處理寫法值得抄
  - **前提**：Ch 2 的 raw 版本讀過再看，會覺得每一步都認得

### 範例

- **[liburing examples/](https://github.com/axboe/liburing/tree/master/examples)** — Jens Axboe
  - **讀哪裡**：`io_uring-cp.c`（複製檔案，用到 linked read/write 與批次）、`io_uring-test.c`（最小可跑範例）
  - **為什麼值得讀**：作者本人示範的慣用寫法，`get_sqe` 的 `NULL` 檢查、`for_each_cqe` 的用法都是「production io_uring 長什麼樣」的樣本

liburing 的主幹摸熟了，但我們一直把 SQE 當黑盒在填——`prep_read` 到底設了哪些欄位、那些 `IOSQE_*` flag 各自做什麼、`user_data` 以外還有哪些欄位能用？下一章把 SQE 整個結構攤開，逐欄拆解。

→ [Ch 7 SQE 詳解：opcode、flags、user_data](./07-sqe-anatomy.md)
