# Ch 3 — 三個 syscall：setup / enter / register

> **目標**：把 io_uring 的**全部三個** syscall（`io_uring_setup`、`io_uring_enter`、`io_uring_register`）各自拆開，講清楚每個的參數、回傳、以及它在整條路徑上扮演的角色。Ch 2 我們已經用過 setup 和 enter，這章補上還沒碰過的 register，並把三個 flag 家族的全貌先鋪開——細節留給後面對應章節，這章先建立「哪個 flag 歸哪個 syscall、大概管什麼」的地圖。

> **環境**：kernel 6.18、gcc 11.4。本章的 raw 範例**不連結 liburing**，只用 `<linux/io_uring.h>` 的結構與常數，直接 `syscall()`。

## 為什麼先數清楚「只有三個」

傳統 I/O 的 API 表面積大得驚人：`read`、`write`、`readv`、`pread`、`preadv`、`recv`、`recvmsg`、`sendmsg`、`fsync`、`fdatasync`、`accept`、`connect`、`epoll_create`、`epoll_ctl`、`epoll_wait`、`openat`、`statx`、`fallocate`……每一個都是獨立的 syscall，每一個都有自己的進出 kernel 成本。

io_uring 把這整張表收斂成**三個** syscall：

```
io_uring_setup(entries, params)              -> 建立實例,拿到 ring_fd
io_uring_enter(fd, to_submit, min_complete, flags, ...) -> 提交 + 等待
io_uring_register(fd, opcode, arg, nr_args)  -> 預先登記資源
```

「幾十個操作」全部變成 SQE 裡的一個 `opcode` 欄位（read 是 22、accept 是 13、send_zc 是 47……），用同一個 `io_uring_enter` 送出去。這不是表面的 API 整併，而是模型的根本轉變：**操作的種類從 syscall 分支（各走各的進 kernel 路徑）變成了資料（SQE 裡的一個 byte）**。一旦接受這個轉變，後面所有東西都順理成章——新增一種操作，kernel 只要加一個 opcode，不用加 syscall，也不用動 glibc。

> 這三個 syscall 的號碼（x86-64）是固定的 ABI：`io_uring_setup` = **425**、`io_uring_enter` = **426**、`io_uring_register` = **427**，連號。本章實測機器用 `grep __NR_io_uring_* /usr/include/x86_64-linux-gnu/asm/unistd_64.h` 確認過就是這三個數字。

## 先建立直覺：三個 syscall 的分工

```
     ┌─────────────────────────────────────────────────────────┐
     │  io_uring_setup   「開店」 一次性                          │
     │    建 io_ring_ctx、配 SQ/CQ 環的物理頁、回傳 fd 與 offset  │
     └─────────────────────────────────────────────────────────┘
                              │ ring_fd
          ┌───────────────────┼───────────────────┐
          ▼                                       ▼
 ┌──────────────────────────┐        ┌──────────────────────────────┐
 │ io_uring_register 「備料」│        │ io_uring_enter   「營業」熱路徑 │
 │  可選、通常一次性          │        │   提交 SQE + 等 CQE,反覆呼叫    │
 │  把 files/buffers/pbuf    │        │   SQPOLL 下甚至可完全不呼叫     │
 │  等資源預先交給 kernel 記住│        │                               │
 └──────────────────────────┘        └──────────────────────────────┘
```

三者的呼叫頻率天差地別，這決定了它們各自的設計取向：

- **`setup`**：每個 ring 一次。慢一點無所謂，它做重活（配記憶體、建環）。
- **`register`**：可選，通常在熱路徑開始前一次備齊。它的目的正是**把本來每次操作都要付的成本，提前付掉一次**（例如每次 I/O 都要查 fd table、pin buffer 頁，registered 之後就省了）。
- **`enter`**：熱路徑。它被呼叫的次數可能是每秒數百萬，所以 io_uring 想盡辦法讓它能一次做很多事（批次提交）、甚至乾脆不呼叫（SQPOLL）。

記住這個「開店 / 備料 / 營業」的分工，下面逐個拆。

## io_uring_setup：一個 syscall 換來一個實例

```c
int io_uring_setup(unsigned entries, struct io_uring_params *p);
```

Ch 2 已經用過它，這裡補完整。它做三件事：

1. 在 kernel 建立一個 io_uring 實例（內部是 `struct io_ring_ctx`，Ch 20 整章解剖），配置 SQ/CQ 兩個環的物理頁。
2. 回傳一個 **fd**。這個 fd 是之後 `enter`、`register`、`mmap` 的唯一把手，`close()` 它就拆掉整個 ring。
3. **回填** `params`：告訴你 kernel 實際給了多少槽、每個環欄位在哪個 offset、支援哪些 feature。

`entries` 是你要求的 SQ 深度。kernel 會把它**向上取整到 2 的次方**（這是 ring_mask 折槽能用的前提，Ch 4 細談），並夾在 `[1, 32768]`。CQ 深度預設是 SQ 的兩倍——下一節的實測會看到這個 2 倍關係。

### io_uring_params：輸入與輸出合一的結構

`params` 這個結構很特別：它**既是輸入也是輸出**。你填 `flags`（想要什麼模式）進去，kernel 填其餘欄位回來。6.18 的定義（`include/uapi/linux/io_uring.h`）：

```c
struct io_uring_params {
    __u32 sq_entries;     // 【輸出】kernel 實際給的 SQ 槽數
    __u32 cq_entries;     // 【輸出】CQ 槽數
    __u32 flags;          // 【輸入】IORING_SETUP_* 的 OR
    __u32 sq_thread_cpu;  // 【輸入】SQPOLL thread 綁哪顆 CPU(配 SQ_AFF)
    __u32 sq_thread_idle; // 【輸入】SQPOLL thread 閒置多久才睡(ms)
    __u32 features;       // 【輸出】這顆 kernel 支援哪些 IORING_FEAT_*
    __u32 wq_fd;          // 【輸入】要共用哪個 ring 的 io-wq(配 ATTACH_WQ)
    __u32 resv[3];
    struct io_sqring_offsets sq_off;  // 【輸出】SQ 環各欄位 offset
    struct io_cqring_offsets cq_off;  // 【輸出】CQ 環各欄位 offset
};
```

`memset` 清零再填你要的 `flags`，是鐵律——`resv[3]` 必須是 0，否則 kernel 回 `-EINVAL`。`features` 這個**輸出**欄位尤其重要：它是你在執行期問 kernel「你支援哪些能力」的答案，Ch 0 的 `IORING_FEAT_SINGLE_MMAP` 判斷就是讀它。不要用 kernel 版本號去推能力，要讀 `features`——同一個版本號在不同發行版（打了不同 backport）能力可能不同。

### IORING_SETUP_* flags：先鋪地圖，細節留後面

setup 的 `flags` 是 io_uring 最大的一組 flag 家族。6.18 一共 19 個（`1U<<0` 到 `1U<<18`）。這章不逐個深講——那是後面好幾章的事——但先把它們分類，你才知道「要調某個行為，該去動哪個 flag」：

| flag（值） | 一句話作用 | 細談章節 |
|---|---|---|
| `IORING_SETUP_IOPOLL` (1<<0) | 用忙輪詢而非中斷收完成（給 NVMe 低延遲） | Ch 10 / 41 |
| `IORING_SETUP_SQPOLL` (1<<1) | kernel 起一個 thread 自己輪 SQ，可達零 syscall | Ch 9 |
| `IORING_SETUP_SQ_AFF` (1<<2) | 把 SQPOLL thread 綁定到 `sq_thread_cpu` | Ch 9 |
| `IORING_SETUP_CQSIZE` (1<<3) | 讓你用 `cq_entries` 自訂 CQ 深度，不吃預設 2 倍 | Ch 8 |
| `IORING_SETUP_CLAMP` (1<<4) | entries 超過上限時夾住而非報錯 | 本章下方 |
| `IORING_SETUP_ATTACH_WQ` (1<<5) | 多個 ring 共用同一個 io-wq worker pool | Ch 26 |
| `IORING_SETUP_R_DISABLED` (1<<6) | ring 建好先停用，待 register 啟用（配 restrictions） | Ch 39 |
| `IORING_SETUP_SUBMIT_ALL` (1<<7) | 一批 SQE 中某個備料失敗，仍繼續提交其餘 | 本章下方 |
| `IORING_SETUP_COOP_TASKRUN` (1<<8) | 完成處理不強制 IPI 打斷目標 thread，降干擾 | Ch 27 |
| `IORING_SETUP_TASKRUN_FLAG` (1<<9) | 有 task_work 待處理時在 SQ flags 標記，配 COOP | Ch 27 |
| `IORING_SETUP_SQE128` (1<<10) | SQE 加大到 128 byte（給 `URING_CMD` 等塞更多資料） | Ch 7 |
| `IORING_SETUP_CQE32` (1<<11) | CQE 加大到 32 byte（big CQE，給 zero-copy 等） | Ch 8 |
| `IORING_SETUP_SINGLE_ISSUER` (1<<12) | 向 kernel 承諾只有一個 thread 提交，換取優化 | Ch 27 |
| `IORING_SETUP_DEFER_TASKRUN` (1<<13) | 完成工作延到你 `enter` 等待時才做，大幅降延遲抖動 | Ch 27 |
| `IORING_SETUP_NO_MMAP` (1<<14) | ring 記憶體由你自備（user 分配），不走 mmap | Ch 4 |
| `IORING_SETUP_REGISTERED_FD_ONLY` (1<<15) | 不給普通 fd，只給 registered ring index | Ch 15 |
| `IORING_SETUP_NO_SQARRAY` (1<<16) | 省掉 SQ 間接 index 陣列，SQE 直接照順序取 | Ch 4 |
| `IORING_SETUP_HYBRID_IOPOLL` (1<<17) | IOPOLL 的混合模式，先睡一下再輪詢省 CPU | Ch 41 |
| `IORING_SETUP_CQE_MIXED` (1<<18) | 同一 ring 混用 16/32-byte CQE | Ch 8 |

這張表現在不用背。重點是建立「**這些開關全部掛在 setup 上，在建 ring 的那一刻就定了**」的認識——大部分 setup flag 一旦建好就不能改。`SQPOLL`、`DEFER_TASKRUN`、`SINGLE_ISSUER`、`COOP_TASKRUN` 這四個最常同時出現，因為它們合起來是目前**低延遲單 thread 事件迴圈**的標準組合，Ch 9 和 Ch 27 會把這一組講透。

## io_uring_enter：熱路徑上唯一的 syscall

```c
int io_uring_enter(int fd, unsigned to_submit, unsigned min_complete,
                   unsigned flags, const void *arg, size_t argsz);
```

Ch 2 用了它的簡化版。完整來看，`enter` 一個呼叫同時做**提交**和**等待**兩件事，由三個數字控制：

- **`to_submit`**：SQ 裡有幾個新 SQE 要處理。kernel 從它記住的 SQ head 開始，往 tail 方向取這麼多個 SQE 去派發。填 0 代表「這次不提交，只來收完成」。
- **`min_complete`** + **`IORING_ENTER_GETEVENTS`**：這兩個要搭配。只有帶了 `GETEVENTS` flag，`min_complete` 才生效，意思是「**阻塞到 CQ 裡至少有這麼多完成事件**才回來」。沒帶 `GETEVENTS`，enter 提交完立刻返回，不等。
- **回傳值**：成功時是**實際提交出去的 SQE 數**（不是完成數）。

這個「一次 call 做兩件事」是 io_uring 省 syscall 的關鍵之一。一個典型的事件迴圈一輪就一個 `enter`：把這輪要做的 I/O 全填進 SQ，`enter(fd, N, 1, GETEVENTS)` 一次送 N 個、順便等至少 1 個回來，再把 CQ 裡所有已完成的收一收。提交和收割在一次進出 kernel 裡完成。

### IORING_ENTER_* flags 總覽

| flag（值） | 作用 |
|---|---|
| `IORING_ENTER_GETEVENTS` (1<<0) | 讓 `min_complete` 生效，提交後等待完成 |
| `IORING_ENTER_SQ_WAKEUP` (1<<1) | 喚醒睡著的 SQPOLL thread（配 SQPOLL，見下節） |
| `IORING_ENTER_SQ_WAIT` (1<<2) | 阻塞直到 SQPOLL thread 把 SQ 消化到有空位 |
| `IORING_ENTER_EXT_ARG` (1<<3) | 啟用 `arg`/`argsz`：傳 timeout 等擴充參數 |
| `IORING_ENTER_REGISTERED_RING` (1<<4) | `fd` 是 registered ring index 而非真 fd |
| `IORING_ENTER_ABS_TIMER` (1<<5) | EXT_ARG 的 timeout 解讀為絕對時間 |
| `IORING_ENTER_EXT_ARG_REG` (1<<6) | EXT_ARG 參數本身也來自 registered 區域 |
| `IORING_ENTER_NO_IOWAIT` (1<<7) | 等待時不計入 iowait 統計 |

早期的 `enter` 只有前四個參數。`arg` 和 `argsz` 是後來（5.11）為了 `IORING_ENTER_EXT_ARG` 加的——它讓 `enter` 能順便帶一個 timeout（「最多等這麼久」），不必為了超時另外提交一個 timeout SQE。`io_uring_wait_cqe_timeout` 底層就靠它。`EXT_ARG` 能不能用，要看 `params.features` 有沒有 `IORING_FEAT_EXT_ARG`（6.18 一定有）。

### SQPOLL 下的 enter：連這個 syscall 都想省掉

`SQPOLL` 模式（`IORING_SETUP_SQPOLL`）下，kernel 起一個 poll thread 自己輪詢 SQ 的 tail，你把 SQE 填進去、推 tail，**不用呼叫 enter**，kernel thread 會自己發現並派發。這就是 io_uring「可達零 syscall」的字面意思。

但 poll thread 閒置久了（超過 `sq_thread_idle`）會進入睡眠以省 CPU。這時你推了新 SQE 也沒人處理，必須呼叫 `io_uring_enter(fd, N, 0, IORING_ENTER_SQ_WAKEUP)` 把它叫醒。怎麼知道它睡了？讀 SQ 環 flags 欄位有沒有 `IORING_SQ_NEED_WAKEUP` 位。這個「推 tail → 檢查 flag → 決定要不要 wakeup」之間需要一個 full memory barrier，是 Ch 5 的 SQPOLL ordering 主題，這裡先點名。

## io_uring_register：把成本提前付掉

```c
int io_uring_register(int fd, unsigned opcode, void *arg, unsigned nr_args);
```

這是 Ch 2 沒碰到的第三個 syscall。它的角色是**讓 kernel 預先記住某些資源**，之後的 I/O 引用這些資源時就省掉重複成本。核心動機只有一句：

> 有些成本本來每個 I/O 都要付一次，register 讓你付一次就好。

兩個經典例子：

- **registered files**（Ch 15）：每次用 fd 做 I/O，kernel 都要從你的 fd table 查出對應的 `struct file` 並加引用計數。把一組 fd 一次 register 進去，之後用「index」引用它們，kernel 省掉每次查表與引用計數的開銷。
- **registered buffers**（Ch 15）：I/O 要把 user buffer 的頁 pin 住（防止被換出或移動）。每次 I/O 都 pin/unpin 很貴。先把 buffer register，頁一次 pin 好，之後重複用。

`opcode` 決定這次 register 做什麼，`arg`/`nr_args` 是對應的資料。6.18 的 `enum io_uring_register_op` 有一長串，先鋪地圖：

| opcode（值） | 登記什麼 | 細談章節 |
|---|---|---|
| `IORING_REGISTER_BUFFERS` (0) / `_BUFFERS2` (15) | 固定 buffer（pin 住的 I/O 緩衝） | Ch 15 |
| `IORING_REGISTER_FILES` (2) / `_FILES2` (13) | 固定 fd 集合 | Ch 15 |
| `IORING_REGISTER_FILES_UPDATE` (6/14) | 動態替換固定 fd 集合中的某些槽 | Ch 15 |
| `IORING_REGISTER_EVENTFD` (4) | 完成時順便通知一個 eventfd | Ch 18 |
| `IORING_REGISTER_PROBE` (8) | 查詢 kernel 支援哪些 opcode | 本章範例 |
| `IORING_REGISTER_PERSONALITY` (9) | 登記一組憑證，SQE 可借它的身分執行 | Ch 39 |
| `IORING_REGISTER_RESTRICTIONS` (11) | 限制這個 ring 能做哪些操作（加固用） | Ch 39 |
| `IORING_REGISTER_ENABLE_RINGS` (12) | 啟用用 `R_DISABLED` 建的 ring | Ch 39 |
| `IORING_REGISTER_RING_FDS` (20) | 把 ring fd 本身 register，之後 enter 用 index | Ch 15 |
| `IORING_REGISTER_PBUF_RING` (22) | 登記 provided buffer ring（高效供 buffer） | Ch 14 |
| `IORING_REGISTER_IOWQ_MAX_WORKERS` (19) | 調 io-wq worker 數量上限 | Ch 26 |
| `IORING_REGISTER_SYNC_CANCEL` (24) | 同步取消 API | Ch 17 |
| `IORING_REGISTER_NAPI` (27) | 設定網路 busy poll（NAPI） | Ch 41 |

> 注意高位 flag `IORING_REGISTER_USE_REGISTERED_RING = 1U<<31`：它 OR 進 `opcode`，表示傳入的 `fd` 是 registered ring index 而非真 fd。這和前面 `IORING_ENTER_REGISTERED_RING` 是同一個主意——連操作 ring 本身都能省掉 fd 查表。

這張表同樣不用背。這章要你帶走的是**模型**：register 是「備料」入口，它登記的每一類資源後面都有專章。下面我們用其中最無害的一個（`PROBE`）親手跑一次 register，把三個 syscall 在一支程式裡串起來。

## 三個 syscall 一次跑完

這支 raw 程式把 setup → register(PROBE) → enter 串成一條線：建 ring、問 kernel 支援哪些 op、送一個 `IORING_OP_NOP`（最無害的操作，不碰 fd 也不碰 buffer）進去等它完成。

```c
// ch03_three_syscalls.c（節錄，完整檔見課程 src/）
struct io_uring_params p;
memset(&p, 0, sizeof p);
int fd = io_uring_setup(8, &p);                       // syscall 1

size_t nr = 64;
size_t psz = sizeof(struct io_uring_probe) + nr * sizeof(struct io_uring_probe_op);
struct io_uring_probe *probe = calloc(1, psz);
io_uring_register(fd, IORING_REGISTER_PROBE, probe, nr);   // syscall 2
// probe->last_op 是這顆 kernel 最大的 opcode 號
// probe->ops[op].flags & IO_URING_OP_SUPPORTED 判斷某 op 支不支援

// ... mmap 出 SQ 環與 SQE 陣列 ...
sqes[index].opcode    = IORING_OP_NOP;
sqes[index].user_data = 0x4E4F50;   // 'N''O''P'
sarray[index] = index;
atomic_store_explicit(stail, tail + 1, memory_order_release);

io_uring_enter(fd, 1, 1, IORING_ENTER_GETEVENTS);     // syscall 3
// 收 CQE: user_data 原樣回來,res=0(NOP 成功)
```

`IORING_REGISTER_PROBE` 要你傳一塊「`io_uring_probe` 標頭 + N 個 `io_uring_probe_op`」的連續記憶體，`nr_args` 告訴 kernel 這塊能裝幾個 op。kernel 填回每個 opcode 支不支援。這正是 Ch 0 用的 `io_uring_get_probe()` 的底層——liburing 只是把這段 register 包成一個函式。

編譯（不連結 liburing，純 raw syscall）並執行：

```bash
gcc -O2 -Wall -g ch03_three_syscalls.c -o ch03_three_syscalls
./ch03_three_syscalls
```

實測輸出（kernel 6.18）：

```
[setup]  ring_fd=3  sq_entries=8  cq_entries=16
[setup]  features: SINGLE_MMAP=1 NODROP=1 EXT_ARG=1
[register] PROBE ok: kernel last_op=62
[register]   NOP(op=0) supported=1  READ(op=22) supported=1
[enter]  submitted=1  GETEVENTS: enter 回傳 1(已提交的 SQE 數)
[enter]  收到 CQE: user_data=0x4e4f50 res=0
```

逐行對照三個 syscall：

- `[setup]`：`ring_fd=3`（0/1/2 是 stdin/out/err），`sq_entries=8` 是我們要的，`cq_entries=16` 是 kernel 給的**預設 2 倍**（沒加 `CQSIZE` 自訂）。`features` 讀回三個能力位。
- `[register]`：PROBE 回報 `last_op=62`——這顆 kernel 認得 0 到 62 號 opcode。NOP 和 READ 都 supported。
- `[enter]`：回傳 `1` 是**提交數**不是完成數；NOP 完成，`res=0`（NOP 永遠成功），`user_data` 原樣帶回。

一支八十行的程式，io_uring 的三個 syscall 全用到了。之後你用 liburing 寫的每支程式，底層都是這三個的組合。

## 底層機制：為什麼是「三個」而不是「一個」或「十個」

有人會問：既然 register 是可選的、enter 能提交也能等待，為什麼不乾脆合成一個萬能 syscall，或拆得更細？設計上的取捨：

- **setup 必須獨立**，因為它要回傳 fd 和記憶體佈局——這是「建立階段」的產物，之後所有操作都依賴它。把它和熱路徑的 enter 混在一起沒有意義。
- **register 必須獨立於 enter**，因為它的語意是「改變 ring 的持久狀態」（登記的資源在多次 enter 之間一直有效），而 enter 是「處理這一批請求」。兩者生命週期不同。更關鍵的是 register 多半在熱路徑**開始前**做一次，和每輪都呼叫的 enter 頻率差了好幾個數量級，混在一起會讓熱路徑背上用不到的參數。
- **enter 不再往下拆**，因為「提交」和「等待」若拆成兩個 syscall，一輪事件迴圈就要兩次進出 kernel，正好違背 io_uring 省 syscall 的初衷。把它們合在一個 enter 裡，是刻意的。

這三個的邊界不是隨意畫的，而是沿著「頻率」和「生命週期」兩條線切的。看懂這一點，你就不會疑惑「為什麼 timeout 不是一個 syscall」——因為 timeout 是一種**操作**（SQE opcode），走 enter 提交，不是獨立 syscall。io_uring 的哲學是：**凡是「操作」，一律變成 SQE；syscall 只留給「建立、備料、進出」這三件框架性的事。**

## 對比與取捨

| 面向 | 傳統 I/O | io_uring |
|---|---|---|
| syscall 數量 | 幾十個（每種操作一個） | **三個**，操作變成 SQE 的 opcode |
| 新增一種操作 | 要加 syscall、改 glibc | kernel 加一個 opcode 即可 |
| 一次提交幾個請求 | 一個（`readv` 之類勉強算批次） | 任意多個，一次 enter 全送 |
| 提交與等待 | 分離的 syscall | 一個 `enter` 同時做 |
| 重複資源成本（fd 查表、pin 頁） | 每次 I/O 都付 | `register` 一次付清 |
| 能否零 syscall | 否 | SQPOLL 下可以 |

## 踩雷集錦

1. **以為 register 是必須的**。錯誤直覺：「要用 io_uring 得先 register 一堆東西。」正確認識：register **完全可選**。Ch 2 整支程式沒 register 過任何東西照跑。register 是效能優化（省重複成本）與進階功能（pbuf ring、restrictions）的入口，不是必經之路。先會用 setup+enter，再按需加 register。

2. **把 `enter` 回傳值當成「完成了幾個」**。錯誤直覺：「enter 回傳 3，代表 3 個 I/O 做完了。」正確認識：回傳值是**提交出去的 SQE 數**。完成了幾個要看 CQ 環裡多了幾個 CQE。提交成功 ≠ 完成，更 ≠ 成功——一個 SQE 可能提交了、完成了，但 `res` 是 `-EBADF`。

3. **`min_complete` 設了卻忘了帶 `GETEVENTS`**。錯誤直覺：「我 `min_complete=1`，enter 就會等一個完成。」正確認識：`min_complete` 只有在 `flags` 帶 `IORING_ENTER_GETEVENTS` 時才生效。少了 flag，enter 提交完立刻返回、完全不等，你的程式會以為沒事發生而空轉。

4. **`io_uring_params` 沒 memset 就用**。錯誤直覺：「我只關心 `flags`，其他欄位不管。」正確認識：`resv[3]` 等保留欄位必須為 0，殘留的堆疊垃圾會讓 setup 回 `-EINVAL`，而且錯得莫名其妙。永遠 `memset(&p, 0, sizeof p)` 再填。

5. **用 kernel 版本號去推能力，而不讀 `features`/PROBE**。錯誤直覺：「6.18 一定支援 X。」正確認識：發行版會 backport，WSL/雲端 kernel 可能裁掉某些 config。執行期讀 `params.features`（feature 位）和 `IORING_REGISTER_PROBE`（opcode 支援）才是可靠答案。本課 Ch 0 給的 probe 程式就是幹這個。

## 進階：再往深一層

**`SUBMIT_ALL` 與 `CLAMP` 這兩個「容錯」flag 的真實用途。** 它們平常不起眼，但在批次提交時很關鍵：

- 預設行為下，一批 SQE 在提交迴圈裡若有一個「**備料階段**」就失敗（例如 opcode 非法、link 斷掉），kernel 會停在那裡，後面的 SQE 不提交。`IORING_SETUP_SUBMIT_ALL`（1<<7）改成「跳過壞的，繼續提交好的」。對一次塞幾百個 SQE 的高吞吐場景，這避免一顆老鼠屎擋住整批。注意它只影響**提交期**的錯誤；提交後執行期的失敗（`-EBADF` 之類）本來就各自從 CQE 回報，不受影響。

- `IORING_SETUP_CLAMP`（1<<4）：`entries` 或 CQ size 超過 kernel 上限（SQ 32768、CQ 65536）時，預設是回 `-EINVAL`，加了 CLAMP 改成「夾到上限」而不報錯。寫可移植的庫時有用——不想為了「要求太大」特地處理錯誤，讓 kernel 夾一下就好。

**`register` 的「動態更新」家族。** 你可能以為 register 是一次性的，但 `IORING_REGISTER_FILES_UPDATE`（6）、`_BUFFERS_UPDATE`（16）、`_PBUF_RING` 的增刪，讓你在 ring 存活期間動態替換已登記的資源槽。一個長壽的 server 不會為每個新連線重建 ring，而是把新 socket fd 更新進 registered files 的某個槽。這套「登記一次、原地更新」的機制，是高效能 io_uring server 的標準做法，Ch 15 會實作。

**為什麼 enter 的第 5、6 參數（`arg`/`argsz`）是後來才加的？** 原始 enter 只有 4 個參數。等待帶 timeout 的需求出現後，與其改 syscall 簽章（會破壞 ABI），不如加一個 `EXT_ARG` flag 和兩個新參數——舊程式不帶 flag 就走舊路徑，新程式帶 flag 啟用擴充。這是 syscall 演進的典型手法：**用 flag 開新行為，而非改簽章**。io_uring 整套 ABI 都貫徹這個原則（SQE 的保留欄位、params 的 resv、register 的高位 flag 都是為了「以後能加東西而不破相容」）。

## 本章重點整理

- io_uring 只有**三個** syscall：`io_uring_setup`（425，建實例拿 fd）、`io_uring_enter`（426，提交+等待）、`io_uring_register`（427，登記資源）。幾十種操作都變成 SQE 的 `opcode`。
- 三者分工按**頻率與生命週期**切：setup 一次性建立、register 可選的備料、enter 熱路徑反覆進出（SQPOLL 下可省）。
- `io_uring_params` 輸入輸出合一：你填 `flags`，kernel 回填 `sq/cq_entries`、`features`、環 offset。務必先 `memset`。
- `enter` 一個呼叫同時提交與等待；回傳值是**提交數**不是完成數；`min_complete` 要配 `IORING_ENTER_GETEVENTS` 才生效。
- `register` 把「每次 I/O 的重複成本」提前付清（files/buffers），也是 pbuf ring、restrictions、probe 等進階功能的入口；完全可選。
- setup flags 是最大的一組家族（19 個），大多建 ring 時定死；本章鋪了地圖，`SQPOLL`/`DEFER_TASKRUN`/`SINGLE_ISSUER`/`COOP_TASKRUN` 這組之後會反覆出現。

## 自我檢核

- [ ] 不看筆記，我能說出 io_uring 的三個 syscall 各自做什麼、呼叫頻率差在哪
- [ ] 有人問「io_uring 怎麼支援這麼多操作卻只有三個 syscall」，我能解釋「操作變成 opcode」這個轉變
- [ ] 我能說出 `enter` 的回傳值是什麼（提交數），以及 `min_complete` 要搭配哪個 flag 才生效
- [ ] 我能講出 `register` 的核心動機（提前付清重複成本），並舉出 registered files / buffers 兩個例子
- [ ] 我知道為什麼 timeout 是一個 SQE opcode 而不是一個 syscall
- [ ] 我能解釋為什麼 `io_uring_params` 一定要先 memset，以及為什麼要讀 `features` 而非靠版本號推能力

## 延伸閱讀

### 官方 man page（查參數語意最快）

- **[io_uring_setup(2)](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)**
  - **讀哪裡**：`io_uring_params` 與所有 `IORING_SETUP_*` flag 的逐條說明，是本章那張 flag 表的權威來源
  - **前提**：讀過本章地圖後再查，對照著看最有效率
- **[io_uring_enter(2)](https://man7.org/linux/man-pages/man2/io_uring_enter.2.html)** 與 **[io_uring_register(2)](https://man7.org/linux/man-pages/man2/io_uring_register.2.html)**
  - **讀哪裡**：`enter(2)` 的 `IORING_ENTER_*` flag 與 `min_complete`/`GETEVENTS` 的交互；`register(2)` 的 opcode 全集與各自的 `arg` 格式
  - **為什麼值得讀**：man page 把每個 register opcode 要傳什麼結構寫得很清楚，比翻原始碼快

### 設計文件

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：講 `io_uring_setup`、registered files/buffers 動機的章節，正是本章「提前付清成本」的第一手論述
  - **和本章關聯**：作者解釋為什麼要有 register 這一步，和本章「三個 syscall 的分工」互相印證

### 原始碼

- **Linux 6.18 `io_uring/io_uring.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c>
  - **讀哪裡**：`SYSCALL_DEFINE2(io_uring_setup, ...)`、`SYSCALL_DEFINE6(io_uring_enter, ...)`、`SYSCALL_DEFINE4(io_uring_register, ...)` 三個 syscall 的入口，看三者各自怎麼解參數、分派
  - **前提**：Part 3（Ch 22 起）會逐行帶，這裡先眼熟三個入口長怎樣
- **`io_uring/register.c`** 同目錄
  - **讀哪裡**：`io_uring_register` 的大 `switch`，一眼看完所有 register opcode 的分派，對照本章的 opcode 表

三個 syscall 的角色清楚了，但我們一直把「SQ/CQ 環的記憶體長怎樣」當黑盒帶過。下一章把它徹底攤開：為什麼 SQ 要一層間接 index 陣列、單調計數器配 mask 怎麼折槽、三個 mmap offset 各映射什麼、SQE/CQE 每個欄位是什麼——用真實的 offset 數字畫出記憶體佈局圖。

→ [Ch 4 共享記憶體環：SQ/CQ ring、SQE/CQE 佈局與 mmap](./04-shared-ring-memory.md)
