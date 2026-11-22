# 練習 A — 手刻 raw io_uring cat（不用 liburing）

> **目標**：把 Part 1（Ch 2–9）學到的 raw 機制全部焊在一起，不碰 liburing，親手用 `io_uring_setup` + 三次 `mmap` + 手動操作兩個環，實作一個能用的 `cat`。做完這題，io_uring 的底層對你就不再是黑盒——之後 Part 2 放心交給 liburing，你心裡永遠有這張 raw 的底圖。

> **環境**：kernel 6.18、gcc 11.4。本練習**全程不連結 liburing**，只用 `<linux/io_uring.h>` 的結構定義，用純 `gcc` 編譯。

## 背景動機

Ch 2 我們手刻過一支 raw 程式，但它只做**一個** read 就結束——它證明了「ring 跑得起來」，卻迴避了真實程式必然要面對的問題：

- 一個檔案通常**讀不完一次**，要迴圈讀到 EOF。
- 讀出來的資料要**寫出去**，而寫也可能短寫（partial write），要補齊。
- 寫的目標（stdout）可能是終端機、可能是 pipe、可能被 redirect 成一個檔案——**offset 語意不同**。
- 要處理**多個檔案**、處理**開檔失敗**。

`cat` 看似簡單，卻把這些問題全逼出來——它是那種「一看就會、一寫才發現細節全在 offset 與迴圈邊界」的題目。用 raw io_uring 寫 `cat`，等於把 Ch 2 的「單次 read 骨架」擴展成「能反覆提交、反覆收割、正確處理 offset 與錯誤」的完整迴圈。而且全程不用 liburing，每一個 store-release、每一次 `& ring_mask`、每一個 `cqe->res` 都是你自己寫的——這正是把底層直覺焊死的最好方式。

而且 `cat` 有個好處：它的正確性**可被精確檢驗**。輸出和輸入逐位元組相同，`diff` 一跑就知道對不對——不像網路程式要架客戶端、也不像計時程式要看統計。這讓你能專心在「ring 操作有沒有寫對」上，而不是在「我的測試對不對」上分心。下面的測試用例全部用 `diff` 當裁判，一翻兩瞪眼。

為什麼堅持 raw？因為 Part 3 要讀 kernel 原始碼、Part 4 要分析 CVE，那時你面對的就是這些裸結構與 barrier。liburing 的封裝（Ch 6）對寫產品是對的，但對**讀懂實作與漏洞**，你必須能在沒有封裝的層次思考。這題是你最後一次、也是最完整的一次 raw 練習。

## 任務規格

寫一支純 C 程式 `rawcat.c`，行為對齊 Unix `cat`：

1. **不連結 liburing**。只 `#include <linux/io_uring.h>` 用它的結構與常數，syscall 自己用 `syscall()` 叫（`__NR_io_uring_setup` = 425、`__NR_io_uring_enter` = 426）。用 `gcc -O2 -Wall` 編，不加 `-luring`。
2. **用 io_uring 建 ring**：`io_uring_setup` 拿 fd，三次 `mmap`（SQ 環、CQ 環、SQES 陣列），處理 `IORING_FEAT_SINGLE_MMAP`。
3. **用 `IORING_OP_READ` 讀檔**：對每個輸入檔，從 offset 0 迴圈讀，每次讀一塊（建議 4096 bytes），讀到 `res == 0`（EOF）為止。
4. **用 `IORING_OP_WRITE` 寫 stdout**：把讀到的每一塊寫到 `STDOUT_FILENO`。處理**短寫**（`res` 小於要寫的長度時補齊）。
5. **offset 要正確**：讀檔用遞增的 offset（或用「當前位置」語意）；寫 stdout 要能同時應付 tty、pipe、以及被 redirect 成檔案三種情況。
6. **提交與完成全手動**：填 SQE、store-release 推 SQ tail、`io_uring_enter`、acquire 讀 CQ tail、取 `res`、release 推 CQ head。**不准用任何 `io_uring_*` liburing helper**。
7. **命令列**：`rawcat file1 [file2 ...]` 依序輸出每個檔；沒給檔名時讀 stdin。開檔失敗印錯誤到 stderr、繼續下一個、最後回非零 exit code。

**硬性要求**：所有 ring 操作都是你手寫的 syscall + 記憶體存取。唯一允許的「現成」是 `open`/`close`/`memset` 這類普通 libc，以及 `syscall()` 本身。特別地——**資料的讀與寫都必須走 io_uring**（`IORING_OP_READ` / `IORING_OP_WRITE`），不准用 `read(2)` / `write(2)` 搬檔案內容。這題的重點就是全程不離開 ring；用 `strace` 驗收時（見後文）不該看到任何 `read`/`write` syscall 碰到你的檔案資料。

## 期望輸出範例

```bash
$ gcc -O2 -Wall -g rawcat.c -o rawcat

$ ./rawcat /etc/hostname
DESKTOP-JK2JTGG

$ printf 'AAA\n' > /tmp/a.txt; printf 'BBB\n' > /tmp/b.txt
$ ./rawcat /tmp/a.txt /tmp/b.txt
AAA
BBB

$ ./rawcat /etc/os-release > /tmp/out.txt && diff /etc/os-release /tmp/out.txt && echo IDENTICAL
IDENTICAL

$ echo 'from stdin' | ./rawcat
from stdin

$ ./rawcat /nope; echo "exit=$?"
/nope: No such file or directory
exit=1
```

關鍵驗收：**redirect 到檔案後 `diff` 要完全相同**——這檢驗你的 offset 處理對不對（寫 stdout 若 offset 永遠用 0，redirect 到檔案時會每塊都覆寫同一位置，`diff` 立刻抓到）。

## 如果卡住了

1. **三次 mmap 的 offset 記不住**：`IORING_OFF_SQ_RING`（0）映射 SQ 環 metadata + indices 陣列、`IORING_OFF_CQ_RING`（0x8000000）映射 CQ 環 + CQE 陣列、`IORING_OFF_SQES`（0x10000000）映射 SQE 本體。漏第三次就 segfault（Ch 2 踩雷第 1 條）。回去翻 Ch 2「三次 mmap」那節。

2. **程式卡住不返回 / 收不到 CQE**：多半是 `io_uring_enter` 的參數錯。要同時提交並等待，用 `io_uring_enter(ring_fd, 1, 1, IORING_ENTER_GETEVENTS)`——`to_submit=1`、`min_complete=1`、flag 要有 `GETEVENTS`。少了 `GETEVENTS` 它只提交不等，你去讀 CQ 會發現 `head == tail`（空）。

3. **redirect 到檔案後內容是亂的 / 只有最後一塊**：stdout 的 write offset 用錯了。寫到被 redirect 的檔案時，若每次都用 `off = 0`，kernel 每塊都寫到檔案開頭、互相覆蓋。正確做法是用 `off = (__u64)-1`——它代表「用該 fd 的當前位置並自動前進」（靠 `IORING_FEAT_RW_CUR_POS`，6.18 一定有）。

4. **偶發讀到半成品 / 詭異競態**：推 SQ tail 用了普通寫入而非 store-release，或收 CQE 前沒用 acquire 讀 CQ tail。barrier 配對是正確性核心（Ch 5、Ch 2 踩雷第 3 條）。`atomic_store_explicit(..., memory_order_release)` 推 tail、`atomic_load_explicit(..., memory_order_acquire)` 讀 tail。

5. **`res` 判斷錯把失敗當成功**：`cqe->res` 是**有號**的，失敗是負 errno（`-EBADF` = -9）。用 `if (res < 0)` 判失敗、`-res` 取 errno；`res == 0` 對 read 是 EOF（不是錯誤）。別用無號接 `res`（Ch 8 踩雷第 2、3 條）。

## 實作步驟建議

### Step 1：syscall wrapper 與 ring 結構

先把兩個 syscall 包好，再定義一個結構收納散落的環欄位（對照 Ch 2，但這次收進 struct，像 liburing 的 `struct io_uring` 那樣）：

```c
static int io_uring_setup(unsigned entries, struct io_uring_params *p) {
    return (int)syscall(__NR_io_uring_setup, entries, p);
}
static int io_uring_enter(int fd, unsigned to_submit, unsigned min_complete,
                          unsigned flags) {
    return (int)syscall(__NR_io_uring_enter, fd, to_submit, min_complete,
                        flags, NULL, 0);
}

struct app_ring {
    int ring_fd;
    unsigned *sq_head, *sq_tail, *sq_mask, *sq_array;
    struct io_uring_sqe *sqes;
    unsigned *cq_head, *cq_tail, *cq_mask;
    struct io_uring_cqe *cqes;
};
```

### Step 2：`ring_setup`——setup + 三次 mmap + 定位欄位

直接搬 Ch 2 那段，但把定位結果存進 `struct app_ring`。記得判斷 `IORING_FEAT_SINGLE_MMAP`：有就讓 `cq_ptr = sq_ptr`，沒有才第二次 mmap。所有欄位用 `base + p.sq_off.xxx`/`p.cq_off.xxx` 定位，**不硬編**。

### Step 3：`ring_do_op`——提交一個 op、等它、回傳 `res`

這是核心。教學起見用**最簡模型**：一次只提交一個 op、enter、收一個 CQE、回傳 `res`。把 Ch 2 的「填 SQE → release 推 tail → enter → acquire 收 CQE → release 推 head」整段包成一個函式，參數是 `(opcode, fd, buf, len, off)`。熱路徑效率不是這題重點，正確性才是。

### Step 4：`cat_fd`——讀到 EOF，每塊寫滿

```c
roff = 0;
for (;;) {
    n = ring_do_op(READ, fd, buf, BUF_SIZE, roff);
    if (n < 0) 報錯;
    if (n == 0) break;              // EOF
    roff += n;
    written = 0;
    while (written < n) {           // 補齊短寫
        w = ring_do_op(WRITE, STDOUT, buf + written, n - written, CUR_POS);
        if (w < 0) 報錯;
        written += w;
    }
}
```

### Step 5：`main`——多檔與 stdin

沒給 argv 就 `cat_fd(STDIN_FILENO)`；否則逐一 `open` 每個檔、`cat_fd`、`close`。開檔失敗印 stderr、設 `rc = 1`、繼續。

## 完整參考解答

**自己寫完再看！** 先卡住、先查 Ch 2–9，再對答案，否則這題的價值（親手焊底層）就沒了。

<details>
<summary>點開參考實作（已在 WSL kernel 6.18 實測）</summary>

```c
// rawcat.c — 手刻 raw io_uring cat（不連結 liburing，純 gcc）
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <linux/io_uring.h>

#define QUEUE_DEPTH 4
#define BUF_SIZE    4096
#define CUR_POS     (~0ULL)   // off = -1：用 fd 當前位置並前進（IORING_FEAT_RW_CUR_POS）

/* 沒有 glibc wrapper，自己用 syscall() 叫 */
static int io_uring_setup(unsigned entries, struct io_uring_params *p) {
    return (int)syscall(__NR_io_uring_setup, entries, p);
}
static int io_uring_enter(int fd, unsigned to_submit, unsigned min_complete,
                          unsigned flags) {
    return (int)syscall(__NR_io_uring_enter, fd, to_submit, min_complete,
                        flags, NULL, 0);
}

/* 把散落的環欄位收進一個結構（像 liburing 的 struct io_uring） */
struct app_ring {
    int ring_fd;
    unsigned *sq_head, *sq_tail, *sq_mask, *sq_array;
    struct io_uring_sqe *sqes;
    unsigned *cq_head, *cq_tail, *cq_mask;
    struct io_uring_cqe *cqes;
};

/* setup + 三次 mmap + 定位欄位（對照 Ch 2） */
static int ring_setup(struct app_ring *r) {
    struct io_uring_params p;
    memset(&p, 0, sizeof p);

    r->ring_fd = io_uring_setup(QUEUE_DEPTH, &p);
    if (r->ring_fd < 0) { perror("io_uring_setup"); return -1; }

    int sring_sz = p.sq_off.array + p.sq_entries * sizeof(unsigned);
    int cring_sz = p.cq_off.cqes  + p.cq_entries * sizeof(struct io_uring_cqe);
    if (p.features & IORING_FEAT_SINGLE_MMAP) {       // 兩環併一次 mmap
        if (cring_sz > sring_sz) sring_sz = cring_sz;
        cring_sz = sring_sz;
    }

    void *sq_ptr = mmap(0, sring_sz, PROT_READ | PROT_WRITE,
                        MAP_SHARED | MAP_POPULATE, r->ring_fd, IORING_OFF_SQ_RING);
    if (sq_ptr == MAP_FAILED) { perror("mmap sq"); return -1; }

    void *cq_ptr = sq_ptr;
    if (!(p.features & IORING_FEAT_SINGLE_MMAP)) {
        cq_ptr = mmap(0, cring_sz, PROT_READ | PROT_WRITE,
                      MAP_SHARED | MAP_POPULATE, r->ring_fd, IORING_OFF_CQ_RING);
        if (cq_ptr == MAP_FAILED) { perror("mmap cq"); return -1; }
    }

    r->sqes = mmap(0, p.sq_entries * sizeof(struct io_uring_sqe),
                   PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE,
                   r->ring_fd, IORING_OFF_SQES);
    if (r->sqes == MAP_FAILED) { perror("mmap sqes"); return -1; }

    /* 用 kernel 回填的 offset 定位，不硬編佈局 */
    r->sq_head  = sq_ptr + p.sq_off.head;
    r->sq_tail  = sq_ptr + p.sq_off.tail;
    r->sq_mask  = sq_ptr + p.sq_off.ring_mask;
    r->sq_array = sq_ptr + p.sq_off.array;
    r->cq_head  = cq_ptr + p.cq_off.head;
    r->cq_tail  = cq_ptr + p.cq_off.tail;
    r->cq_mask  = cq_ptr + p.cq_off.ring_mask;
    r->cqes     = cq_ptr + p.cq_off.cqes;
    return 0;
}

/* 提交一個 op、等它完成、回傳 cqe->res（一次一個，最簡模型） */
static int ring_do_op(struct app_ring *r, __u8 opcode, int fd,
                      void *buf, unsigned len, __u64 off) {
    unsigned tail  = *r->sq_tail;
    unsigned index = tail & *r->sq_mask;              // 計數器折成槽位
    struct io_uring_sqe *sqe = &r->sqes[index];
    memset(sqe, 0, sizeof *sqe);
    sqe->opcode    = opcode;
    sqe->fd        = fd;
    sqe->addr      = (unsigned long)buf;
    sqe->len       = len;
    sqe->off       = off;
    sqe->user_data = index;
    r->sq_array[index] = index;                       // SQ indices 指向此 SQE

    /* 發佈 tail：store-release，kernel 看到新 tail 時必看到完整 SQE */
    atomic_store_explicit((_Atomic unsigned *)r->sq_tail, tail + 1,
                          memory_order_release);

    int ret = io_uring_enter(r->ring_fd, 1, 1, IORING_ENTER_GETEVENTS);
    if (ret < 0) { perror("io_uring_enter"); return -1; }

    /* 收一個 CQE：acquire 讀 tail，取結果，release 推 head */
    unsigned chead = *r->cq_head;
    unsigned ctail = atomic_load_explicit((_Atomic unsigned *)r->cq_tail,
                                          memory_order_acquire);
    if (chead == ctail) { fprintf(stderr, "no cqe?\n"); return -1; }
    struct io_uring_cqe *cqe = &r->cqes[chead & *r->cq_mask];
    int res = cqe->res;
    atomic_store_explicit((_Atomic unsigned *)r->cq_head, chead + 1,
                          memory_order_release);
    return res;
}

/* cat 一個 fd：讀到 EOF，每段寫滿 stdout */
static int cat_fd(struct app_ring *r, int fd) {
    char buf[BUF_SIZE];
    __u64 roff = 0;                                   // 讀檔位置，手動追蹤
    for (;;) {
        int n = ring_do_op(r, IORING_OP_READ, fd, buf, BUF_SIZE, roff);
        if (n < 0) { fprintf(stderr, "read: %s\n", strerror(-n)); return -1; }
        if (n == 0) break;                            // EOF
        roff += n;

        int written = 0;
        while (written < n) {                         // 補齊短寫
            int w = ring_do_op(r, IORING_OP_WRITE, STDOUT_FILENO,
                               buf + written, n - written, CUR_POS);
            if (w < 0) { fprintf(stderr, "write: %s\n", strerror(-w)); return -1; }
            written += w;
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    struct app_ring r;
    if (ring_setup(&r) < 0) return 1;

    if (argc < 2)                                     // 沒給檔名就讀 stdin
        return cat_fd(&r, STDIN_FILENO) < 0 ? 1 : 0;

    int rc = 0;
    for (int i = 1; i < argc; i++) {
        int fd = open(argv[i], O_RDONLY);
        if (fd < 0) {
            fprintf(stderr, "%s: %s\n", argv[i], strerror(errno));
            rc = 1; continue;
        }
        if (cat_fd(&r, fd) < 0) rc = 1;
        close(fd);
    }
    return rc;
}
```

編譯與執行：

```bash
gcc -O2 -Wall -g rawcat.c -o rawcat
./rawcat /etc/hostname
```

**實測輸出（kernel 6.18）：**

```
DESKTOP-JK2JTGG
```

**多檔、redirect 正確性、大檔多塊、stdin、錯誤路徑的實測：**

```
=== multi-file ===
AAA
BBB
=== redirect to file (correctness) ===
DIFF-OK (identical)
=== large file (multi-chunk) ===
BIG-OK: 23893 bytes identical (BUF_SIZE=4096, so multi-chunk)
=== stdin ===
from stdin
=== missing file (error path) ===
/nope: No such file or directory
exit=1
```

`23893 bytes` 的檔在 `BUF_SIZE=4096` 下要讀約 6 塊，`diff` 完全相同——證明讀 offset 遞增、寫 stdout 用 `CUR_POS` 都正確。redirect 到檔案也 `diff` 相同，證明 offset 語意沒搞混。

</details>

## 關於參考解答的幾個設計決定

讀完參考解答，有三個決定值得想清楚，它們是這題真正的「考點」：

**一、讀檔用遞增 offset，寫 stdout 用 `CUR_POS`（-1），為什麼不統一？** 讀一般檔案時，用明確遞增的 `roff` 最直覺，也示範了手動追蹤 offset。但 stdout 可能是 pipe/tty（不可 seek），對它用具體 offset 沒意義；更麻煩的是 redirect 成檔案時，你並不知道該從哪個位置寫。`off = -1`（`CUR_POS`）把這個問題交給 kernel：用該 fd 的**當前檔案位置並自動前進**（`IORING_FEAT_RW_CUR_POS`）。一招同時正確應付 tty、pipe、redirect 檔案三種情況。其實讀檔也能用 `-1` 省掉 `roff`，這裡故意用遞增 offset 來練手動追蹤。

**二、為什麼一次只提交一個 op？** 這是刻意的教學簡化——`ring_do_op` 把「提交-等待-收割」綁成同步的一次，讓每一步都看得清清楚楚。真實高效能程式會**批次**提交（Ch 9）、讓多個 read/write 並行在環裡飛，但那會引入「亂序完成要靠 user_data 配對」「環滿要處理」等複雜度，模糊了這題的焦點。先把單 op 的 raw 流程焊死，批次是延伸挑戰。

**三、`QUEUE_DEPTH = 4` 夠嗎？** 夠，因為我們一次只在環裡放一個請求。深度 4 遠超所需（甚至 1 都行，但 setup 會向上取到 2 的次方，給點餘裕）。深度的意義是「同時 in-flight 的請求上限」，單 op 同步模型下永遠只有 1 個 in-flight。延伸挑戰做批次時，這個數字才開始有意義。

**四、同一個 SQE 槽反覆重用，為什麼安全？** `ring_do_op` 每次都 `tail & mask` 算槽位——因為單 op 模型下 tail 每次 +1、head 也跟著 +1，環永遠只有 0 或 1 個在用，實際上總是落在同幾個槽輪轉。重用的前提是：**上一個請求必須完全完成（CQE 收了）才碰下一個 SQE**。我們的 `io_uring_enter(..., min_complete=1)` 保證提交後阻塞到完成，`ring_do_op` 返回時那個 SQE 早已被 kernel 消費完畢，重用它絕對安全。這也點出批次版本的難處：一旦多個請求同時 in-flight，你就**不能**假設某個槽空了，得靠 `tail - head` 算還有多少空間——這正是 liburing 的 `io_uring_get_sqe` 回 `NULL` 幫你擋掉的事（Ch 6、Ch 9）。

## 測試用例

| 測試 | 指令 | 預期 |
|---|---|---|
| 單檔 | `./rawcat /etc/hostname` | 印出主機名 |
| 多檔依序 | `./rawcat /tmp/a.txt /tmp/b.txt` | 兩檔內容依序串接 |
| redirect 正確性 | `./rawcat /etc/os-release > out && diff /etc/os-release out` | **無差異**（offset 對） |
| 大檔多塊 | `seq 1 5000 > big; ./rawcat big > o; diff big o` | **無差異**（讀寫迴圈對） |
| stdin | `echo hi \| ./rawcat` | 印出 `hi` |
| 空檔 | `: > empty; ./rawcat empty` | 無輸出、exit 0（EOF 即停） |
| 開檔失敗 | `./rawcat /nope; echo $?` | stderr 錯誤訊息、exit 1 |
| 失敗後續 | `./rawcat /nope /etc/hostname; echo $?` | 印主機名、仍 exit 1 |
| 二進位檔 | `./rawcat /bin/ls > o; diff /bin/ls o` | **無差異**（不因 NUL 或非文字中斷） |

最該盯的是 **redirect 正確性** 和 **大檔多塊**——它們抓的是 offset 與讀寫迴圈的 bug，而這正是從「Ch 2 單次 read」跨到「完整 cat」新增的難點。

**空檔案**這個用例也別跳過：第一個 read 就回 `res == 0`（EOF），迴圈直接 break，不該印任何東西、exit 0。若你的 EOF 判斷寫成 `if (n <= 0) break`，空檔沒事，但會把**真正的錯誤**（`n < 0`）也當成正常結束而靜默吞掉——所以 `n == 0`（EOF，正常）和 `n < 0`（錯誤，要報）必須分開判，這也是 Ch 8「`res == 0` 不是錯誤、是 EOF」的直接應用。

### 用 strace 確認「真的在用 io_uring」

`cat` 的輸出和普通 `cat` 一樣，光看結果分不出你是不是偷用了 `read(2)`/`write(2)`。用 `strace` 數 syscall，證明每一塊讀寫都走 io_uring：

```bash
strace -e trace=io_uring_setup,io_uring_enter,read,write ./rawcat /tmp/big.txt > /dev/null
```

你應該看到：**一次** `io_uring_setup`、**一連串** `io_uring_enter`（每個 read op 一次、每個 write op 一次），而**沒有**任何 `read`/`write` syscall 碰到檔案資料（只有 libc 啟動時的零星幾個）。拿 `/etc/hostname`（16 bytes）實測，剛好是 **3 次** `io_uring_enter`——一次 read 讀到 16 bytes、一次 write 寫出、一次 read 回 0（EOF）收尾。檔案越大、`io_uring_enter` 越多：23893 bytes 的檔在 `BUF_SIZE=4096` 下要讀約 6 塊（外加一次讀到 EOF），各配一次 write，合計十餘次。若你看到 `read(fd, ...)` 或 `write(1, ...)` 在搬資料，表示某處漏用了 io_uring——回去檢查是不是哪個分支走了 libc。

這個檢查對應 Ch 9 的 strace 訓練：io_uring 程式的 syscall 面貌和傳統程式截然不同，`io_uring_enter` 取代了成堆的 `read`/`write`。看慣這個樣子，之後 debug 或分析別人的 io_uring 程式會快很多。

順帶一提，這也是為什麼 io_uring 讓某些以 `seccomp` 過濾 syscall 的沙箱頭痛：傳統沙箱攔 `read`/`write`/`openat` 來限制程式能做什麼，但 io_uring 把這些操作全塞進 `io_uring_enter` 一個 syscall 裡，`seccomp` 看不到裡面提交了什麼 op。這個「syscall 面貌被壓平」的特性，到 Part 4（安全面）會是攻擊面分析的一條主線——你現在用 `strace` 親眼看到的現象，正是那條線的起點。

## 延伸挑戰

做完基本版，這些挑戰把它推向真實程式的複雜度：

1. **批次並行讀（多檔 in-flight）**：改成一次提交多個 read SQE（不同檔案或同檔不同 offset），用 `user_data` 標記哪個請求、亂序收割（Ch 6 的 `for_each_cqe` 概念的手刻版）。你會第一次真正用到 `QUEUE_DEPTH > 1`，也會第一次面對「`get_sqe` 等價物回不到槽怎麼辦」。

2. **用 `IORING_OP_READV` / `WRITEV`**：把單一 buffer 換成 `struct iovec` 陣列，一個 SQE 讀/寫多段。注意此時 `sqe->addr` 是 iovec 陣列位址、`sqe->len` 是 **iovec 個數**（不是位元組數，Ch 7 踩雷第 3 條）。這是 Ch 10 的主題，先手刻一遍。

3. **`O_DIRECT` 讀**：用 `open(path, O_RDONLY | O_DIRECT)` 繞過 page cache，要求 buffer 位址與長度**對齊**（通常 512 或 4096）。用 `posix_memalign` 配對齊 buffer。你會踩到「沒對齊就 `-EINVAL`」——這正是 O_DIRECT 的經典坑，也是 io_uring 相對 Linux AIO 真正發光的場景（Ch 1、Ch 10）。

4. **鏈式讀寫（`IOSQE_IO_LINK`）**：把「read 這塊 → write 這塊」用 `IOSQE_IO_LINK` 串成一條鏈，一次提交兩個 SQE，讓 kernel 保證 write 在 read 之後、且 read 失敗就取消 write。你得手動在 read SQE 的 `flags` 設 `IOSQE_IO_LINK`。這是 Ch 16 的主題，提前體會「請求間排序」怎麼用 flag 表達——但也會踩到「鏈中 buffer 的資料相依 kernel 不幫你保證內容、只保證順序」的微妙之處。

每個挑戰都對應後面某一章，先手刻過，到那章讀 liburing 封裝與 kernel 實作時會特別有感。

做這些挑戰時有個共通的升級：一旦允許**多個請求同時 in-flight**，`ring_do_op` 那種「提交一個、馬上收一個」的同步模型就不夠了。你得把「提交」和「收割」拆開——先填一批 SQE 推 tail、`io_uring_enter(ring_fd, N, M, GETEVENTS)` 一次提交 N 個等 M 個，再用一個迴圈掃 CQ（`chead != ctail`）把現成的全收光，靠每個 CQE 的 `user_data` 認回是哪個請求（哪個檔、哪段 offset）。這正是你手刻版的 `for_each_cqe`，也是 Ch 6 liburing 幫你包好的那層。把這步自己走一遍，liburing 的每個收割函式你就都知道它在做什麼了。

## 自我檢核

完成後你應該能回答：

- [ ] 為什麼需要**三次** mmap？少了映射 SQES 的那次會發生什麼？
- [ ] `io_uring_enter(fd, 1, 1, IORING_ENTER_GETEVENTS)` 四個參數各是什麼意思？拿掉 `GETEVENTS` 會怎樣？
- [ ] 寫 stdout 時我用 `off = -1` 而非 `off = 0`，redirect 到檔案時這兩者的結果差在哪？
- [ ] 推 SQ tail 為什麼要 store-release？收 CQE 前讀 CQ tail 為什麼要 acquire？拿掉會出什麼樣的 bug？
- [ ] `cqe->res` 為 0、為正、為負分別代表什麼？我的 EOF 判斷靠的是哪個值？
- [ ] 我的 `ring_do_op` 一次只處理一個 in-flight 請求——如果要並行多個，`user_data` 的角色會怎麼改變？
- [ ] 我能用 `strace` 證明我的 cat 全程走 io_uring、沒偷用 `read`/`write` 搬資料嗎？
- [ ] 把二進位檔（含 NUL 位元組）餵進去，我的程式會不會提早中斷？為什麼不會（提示：我靠 `res` 的位元組數，不靠字串結尾）？

做完這題，你已經能在**沒有任何封裝**的層次操作 io_uring——這是讀 kernel 原始碼（Part 3）和分析 CVE（Part 4）的入場券。接下來 Part 2 回到 liburing，開始逐類攻克各種 operation。第一站是檔案 I/O 的全集：`read`/`write`/`readv`/`writev`、`O_DIRECT`、以及 `splice`/`tee` 的零拷貝管線。

→ [Ch 10 檔案 I/O：read/write/readv、O_DIRECT](./10-file-io.md)
