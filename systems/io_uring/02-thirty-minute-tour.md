# Ch 2 — 三十分鐘全貌：SQ/CQ ring 與 raw hello world

> **目標**：不用 liburing，直接用 `io_uring_setup` 一個 syscall、三次 `mmap`、手動操作兩個環，把 Ch 1 那張 SQ/CQ 圖變成一支真的跑起來的程式。看過這一遍「底層長什麼樣」，之後 liburing 幫你包起來的每一步你都知道它在做什麼。

> **環境**：kernel 6.18、gcc 11.4。本章的 raw 版本**不連結 liburing**，只用 `<linux/io_uring.h>` 的結構定義。

## 為什麼先學 raw 版本？

幾乎所有 io_uring 教學都直接從 liburing 開始——這對寫產品是對的，但對**學習**是遺憾的。liburing 把 ring 的記憶體佈局、head/tail 指標、memory barrier 全藏起來了。你照著 `io_uring_prep_read` / `io_uring_submit` 抄，能跑，但 ring 到底是什麼、kernel 和你怎麼同步，仍是黑盒。

本章我們手動做一遍。這支程式只有一個 read，但它把 io_uring 的**全部骨架**攤開：setup、三次 mmap、填 SQE、推 tail、enter、收 CQE。之後的章節都是在這個骨架上長肉。

> 這是本課唯一一次完整手刻 raw ring。Ch 6 起改用 liburing，但每當某個行為讓你困惑，回來看這一章——答案通常在這裡被 liburing 藏起來的某一步。

## 先建立直覺：兩個環，各自一個生產者一個消費者

```
 Submission Queue (SQ)              Completion Queue (CQ)
 ┌─────────────────────┐           ┌─────────────────────┐
 │ user 是生產者 ──┐   │           │ kernel 是生產者 ─┐  │
 │                 ▼   │           │                  ▼  │
 │  [SQE][SQE][  ][  ] │           │  [CQE][CQE][ ][ ]   │
 │   ▲                 │           │   ▲                 │
 │   └── kernel 是消費者│           │   └── user 是消費者 │
 └─────────────────────┘           └─────────────────────┘
   user 推 tail,                      kernel 推 tail,
   kernel 移 head                     user 移 head
```

兩個環都是**單一生產者、單一消費者**的環形緩衝區。方向相反：

- **SQ**：你（user）在 tail 端放請求，kernel 從 head 端取。
- **CQ**：kernel 在 tail 端放結果，你從 head 端取。

環形緩衝區的經典規則：**生產者推進 tail，消費者推進 head，`head == tail` 代表空。** io_uring 的 head/tail 是**單調遞增的 32-bit 計數器**（不回繞到 0），要定位實際槽位時用 `index = counter & ring_mask`（mask = 大小 - 1，大小是 2 的次方）。這個「計數器單調、用 mask 取槽」的設計 Ch 4 會細談，先記住。

還有一個容易忽略的細節：SQ 環裡存的其實不是 SQE 本身，而是一層 **indices 陣列**（`sq_off.array`），指向另一塊獨立 mmap 的 SQE 陣列。為什麼要這層間接？Ch 4 解釋（和「提交順序可以和填寫順序不同」有關）。本章先照著做。

## io_uring_setup：一個 syscall 換來三塊共享記憶體

```c
struct io_uring_params p;
memset(&p, 0, sizeof p);
int ring_fd = io_uring_setup(8, &p);   // entries=8
```

`io_uring_setup(entries, params)` 做兩件事：

1. 在 kernel 裡建立這個 io_uring 實例，回傳一個 **fd**（之後 `io_uring_enter`、`mmap` 都靠它）。
2. **回填** `params`：告訴你 SQ/CQ 各有多少槽（`sq_entries`、`cq_entries`），以及**環裡每個欄位的 offset**（`sq_off`、`cq_off`）。

注意這裡沒有 glibc wrapper，要自己用 `syscall()` 叫：

```c
#include <sys/syscall.h>
#include <linux/io_uring.h>
static int io_uring_setup(unsigned entries, struct io_uring_params *p) {
    return (int)syscall(__NR_io_uring_setup, entries, p);
}
```

`__NR_io_uring_setup` 在 x86-64 是 **425**（`io_uring_enter` 是 426、`io_uring_register` 是 427）。glibc 較新版本有 `io_uring_setup()` wrapper，但靠自己 `syscall()` 最沒有版本依賴，也讓你看清這就是個普通 syscall。

**為什麼 kernel 要回填 offset，而不是寫死結構？** 因為 ring 的記憶體佈局是 ABI 的一部分，kernel 保留調整的自由。你永遠用 `p.sq_off.tail` 這種「基底 + offset」的方式定位欄位，不硬編結構，kernel 改版佈局你的 code 也不壞。這是 io_uring 向前相容的關鍵設計。

## 三次 mmap：把 kernel 的環映射到你的位址空間

setup 回來後，用那個 `ring_fd` 配合三個 magic offset 各 mmap 一塊：

```c
int sring_sz = p.sq_off.array + p.sq_entries * sizeof(unsigned);
int cring_sz = p.cq_off.cqes  + p.cq_entries * sizeof(struct io_uring_cqe);

if (p.features & IORING_FEAT_SINGLE_MMAP) {   // 5.4+ 可把兩環併一塊 mmap
    if (cring_sz > sring_sz) sring_sz = cring_sz;
    cring_sz = sring_sz;
}

void *sq_ptr = mmap(0, sring_sz, PROT_READ|PROT_WRITE,
                    MAP_SHARED|MAP_POPULATE, ring_fd, IORING_OFF_SQ_RING);
void *cq_ptr = sq_ptr;
if (!(p.features & IORING_FEAT_SINGLE_MMAP))
    cq_ptr = mmap(0, cring_sz, PROT_READ|PROT_WRITE,
                  MAP_SHARED|MAP_POPULATE, ring_fd, IORING_OFF_CQ_RING);

struct io_uring_sqe *sqes = mmap(0, p.sq_entries * sizeof(struct io_uring_sqe),
                    PROT_READ|PROT_WRITE, MAP_SHARED|MAP_POPULATE,
                    ring_fd, IORING_OFF_SQES);
```

三個 offset 是 io_uring 的 ABI 常數，各映射一樣東西：

| offset 常數 | 值 | 映射的內容 |
|---|---|---|
| `IORING_OFF_SQ_RING` | `0` | SQ 環的 metadata（head/tail/mask）+ indices 陣列 |
| `IORING_OFF_CQ_RING` | `0x8000000` | CQ 環的 metadata + CQE 陣列 |
| `IORING_OFF_SQES` | `0x10000000` | SQE 本體陣列（和 SQ 環分開） |

`MAP_SHARED` 是重點：這塊記憶體 **user 和 kernel 共用同一份物理頁**，你寫進去 kernel 立刻看得到，反之亦然。這就是「不用為每個請求各自 syscall」的物理基礎。`MAP_POPULATE` 預先建好頁表，避免第一次存取時的 page fault。

`IORING_FEAT_SINGLE_MMAP`（5.4 引入）讓 SQ 和 CQ 環共用一次 mmap，省一次 syscall。要先檢查 `p.features` 有沒有這個 flag，有才能把 `cq_ptr` 指到 `sq_ptr`。6.18 一定有，但判斷式讓程式也能在舊 kernel 跑。

## 用 offset 定位環裡的每個欄位

mmap 給你一塊記憶體的基底，真正的 head/tail/mask 散落在裡面，用 setup 回填的 offset 去指：

```c
unsigned *sring_tail  = sq_ptr + p.sq_off.tail;
unsigned *sring_mask  = sq_ptr + p.sq_off.ring_mask;
unsigned *sring_array = sq_ptr + p.sq_off.array;
unsigned *cring_head  = cq_ptr + p.cq_off.head;
unsigned *cring_tail  = cq_ptr + p.cq_off.tail;
unsigned *cring_mask  = cq_ptr + p.cq_off.ring_mask;
struct io_uring_cqe *cqes = cq_ptr + p.cq_off.cqes;
```

這段最能體現「別硬編佈局」：`sring_tail` 不是 `sq_ptr + 固定數字`，而是 `sq_ptr + kernel 告訴我的 offset`。

## 填一個 SQE 並發佈

```c
int fd = open("/etc/hostname", O_RDONLY);
char buf[64] = {0};

unsigned tail = *sring_tail;
unsigned index = tail & *sring_mask;          // 用 mask 把計數器折成槽位
struct io_uring_sqe *sqe = &sqes[index];
memset(sqe, 0, sizeof *sqe);
sqe->opcode    = IORING_OP_READ;              // 這是一個 read
sqe->fd        = fd;                          // 讀哪個 fd
sqe->addr      = (unsigned long)buf;          // 讀到哪
sqe->len       = sizeof(buf) - 1;             // 讀多少
sqe->off       = 0;                           // 從檔案 offset 0
sqe->user_data = 0x1234;                      // 你的標記，CQE 會原樣帶回來
sring_array[index] = index;                   // SQ indices 陣列指向這個 SQE

// 發佈 tail —— store-release
atomic_store_explicit((_Atomic unsigned *)sring_tail, tail + 1,
                      memory_order_release);
```

兩個關鍵：

**`user_data` 是你的身分標籤。** kernel 不解讀它，完成時原封不動放進 CQE。真實程式會把它設成指標或 ID，好在收到 CQE 時知道「這是哪個請求完成了」。這裡設 `0x1234`，等下會在輸出看到它原樣回來。

**推 tail 必須是 store-release。** 你先寫好 SQE 內容，再把 tail +1「發佈」給 kernel。store-release 保證：kernel 一旦讀到新的 tail，就一定也看到你剛寫的 SQE 內容，不會讀到半成品。這是 io_uring 正確性的核心，Ch 5 整章在講。先記住這條規則：**寫資料，再 release 推 tail；讀 tail（acquire），再讀資料。**

## io_uring_enter：通知 kernel

```c
int ret = io_uring_enter(ring_fd, 1, 1, IORING_ENTER_GETEVENTS);
```

`io_uring_enter(fd, to_submit, min_complete, flags, ...)`：

- `to_submit = 1`：SQ 裡有 1 個新請求，請處理。
- `min_complete = 1` 且 `flags = IORING_ENTER_GETEVENTS`：處理完後，**阻塞到至少 1 個完成事件**才回來。

一個 `io_uring_enter` 同時做了「提交」和「等完成」兩件事。在 SQPOLL 模式下（Ch 9），連這個 enter 都可以省掉——kernel thread 自己輪詢 SQ 的 tail。這就是 io_uring「可達零 syscall」的意思。

## 收 CQE

```c
unsigned chead = atomic_load_explicit((_Atomic unsigned *)cring_head, memory_order_acquire);
unsigned ctail = atomic_load_explicit((_Atomic unsigned *)cring_tail, memory_order_acquire);
while (chead != ctail) {                        // head != tail 代表有東西
    struct io_uring_cqe *cqe = &cqes[chead & *cring_mask];
    printf("CQE: user_data=0x%llx res=%d\n",
           (unsigned long long)cqe->user_data, cqe->res);
    if (cqe->res > 0)
        printf("read %d bytes: %.*s", cqe->res, cqe->res, buf);
    chead++;
}
atomic_store_explicit((_Atomic unsigned *)cring_head, chead, memory_order_release);
```

對稱於提交端：**acquire 讀 tail**（確保看到 tail 時也看到 CQE 內容），處理，最後 **release 推 head** 告訴 kernel「這些 CQE 我收了，槽可以回收」。

`cqe->res` 帶的是這個操作的回傳值，語意等同對應的同步 syscall：read 成功就是位元組數，失敗是**負的 errno**（例如 `-EBADF` = -9）。`cqe->user_data` 是你當初設的 `0x1234`，原樣回來。

## 編譯、執行、看真實輸出

```bash
gcc -O2 -Wall -g ch02_raw.c -o ch02_raw
./ch02_raw
```

實測輸出（kernel 6.18）：

```
CQE: user_data=0x1234 res=16
read 16 bytes: DESKTOP-JK2JTGG
```

`res=16` 是讀到的位元組數，`user_data=0x1234` 原樣回來。整條路走通了：setup → mmap×3 → 填 SQE → release 推 tail → enter → acquire 收 CQE。**這八十行就是整個 io_uring 的縮影。**

## 對比：這一堆手工 vs liburing 兩行

| 步驟 | raw（本章） | liburing（Ch 6 起） |
|---|---|---|
| 建 ring | setup + 3× mmap + 定位欄位 | `io_uring_queue_init(8, &ring, 0)` |
| 填 read | 手動 `sqe->opcode=...` ×6 | `io_uring_prep_read(sqe, fd, buf, n, 0)` |
| 推 tail | 手動 store-release | `io_uring_submit(&ring)` |
| 收 CQE | 手動 acquire/推 head | `io_uring_wait_cqe` + `io_uring_cqe_seen` |

liburing 沒有魔法，它就是把這章每一步包成一個函式，並把 barrier 放對位置。你現在知道包起來的是什麼了。

## 踩雷集錦

1. **以為 SQE 和 SQ 環在同一塊 mmap**：不是。SQ 環（`IORING_OFF_SQ_RING`）裡只有 indices 陣列，SQE 本體在另一塊（`IORING_OFF_SQES`）。漏掉第三次 mmap，程式直接 segfault。
2. **硬編 offset 或結構佈局**：`sq_ptr + 固定數字` 去抓 tail。一定要用 `p.sq_off.tail`，否則換 kernel 版本佈局變了就錯。
3. **推 tail 用普通寫入，不加 release**：`*sring_tail = tail + 1;`。編譯器或 CPU 可能把它重排到「寫 SQE 內容」之前，kernel 讀到新 tail 卻看到舊 SQE——偶發、極難 debug 的競態。Ch 5 會示範這個錯誤的後果。
4. **忘了 `& ring_mask`**：head/tail 是單調遞增的大數（可能上萬），直接拿去索引陣列會越界。一定要 `counter & mask` 折回槽位。
5. **把 `cqe->res` 當 errno 正值看**：失敗時 `res` 是**負**的 errno（`-EBADF` 是 -9，不是 9）。判斷失敗要 `if (cqe->res < 0)`，取 errno 要 `-cqe->res`。
6. **忘了 `io_uring_cqe_seen` / 推 head**：收了 CQE 卻不推 head，CQ 槽不回收，很快 overflow（Ch 8）。raw 版本就是那行 `atomic_store ... cring_head`。

## 本章重點整理

- io_uring 的骨架：`io_uring_setup`（拿 fd + offset）→ 三次 `mmap`（SQ 環、CQ 環、SQE 陣列，都是 `MAP_SHARED` 共享記憶體）→ 填 SQE → store-release 推 SQ tail → `io_uring_enter` → acquire 讀 CQ tail 收 CQE → release 推 CQ head。
- head/tail 是**單調遞增計數器**，用 `& ring_mask` 折成槽位；`head == tail` 代表空。
- `user_data` 是你的請求標籤，kernel 原樣帶回 CQE；`res` 是操作回傳值，失敗為負 errno。
- 提交/完成的正確性靠 **release/acquire 配對**；liburing 幫你把這些 barrier 放對位置，但機制就是這章這些。

## 自我檢核

- [ ] 不看 code，我能說出 io_uring 從 setup 到收 CQE 的完整步驟順序
- [ ] 我能解釋為什麼要三次 mmap、每次映射的是什麼
- [ ] 我能說出為什麼推 tail 要 store-release、收 CQE 前讀 tail 要 acquire
- [ ] 我知道 `head == tail` 代表空，以及為什麼要 `& ring_mask`
- [ ] 我能把這支 raw 程式的每一步，對應到 liburing 的哪個函式

## 延伸閱讀

### 必讀

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：ring 結構與 memory ordering 兩節，正是本章手刻的那些欄位與 barrier 的官方說明
  - **和本章關聯**：本章的 `sq_off`/`cq_off` 就是這份文件描述的 ABI

### 教學文章

- **[Lord of the io_uring — Low Level](https://unixism.net/loti/low_level.html)** — Shuveb Hussain
  - **這篇說什麼**：同樣用 raw syscall 手刻 io_uring，步驟和本章幾乎一一對應，可互相印證
  - **讀哪裡**：整頁；它的 `app_setup_uring()` 對應本章 setup + mmap 那段
  - **前提**：本章讀完再看，會覺得非常親切

### 官方文件 / man page

- **[io_uring_setup(2)](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)** 與 **[io_uring_enter(2)](https://man7.org/linux/man-pages/man2/io_uring_enter.2.html)**
  - **讀哪裡**：`io_uring_setup(2)` 的 `io_uring_params`、`io_sqring_offsets`、`io_cqring_offsets` 結構定義；`io_uring_enter(2)` 的 flags 一覽
  - **注意**：man page 是查 offset 欄位意義最快的地方，比翻原始碼快

下一章把這章用到的三個 syscall（setup / enter / register）各自拆開講清楚——尤其是本章還沒用到的 `io_uring_register`，它是 registered files/buffers、buffer ring 這些進階功能的入口。

→ [Ch 3 三個 syscall：setup / enter / register](./03-three-syscalls.md)
