# Ch 8 — CQE 詳解：res/flags、錯誤處理、CQ overflow

> **目標**：把 kernel 回給你的那張收據——`struct io_uring_cqe`——攤開。看完你會徹底內化「io_uring 的錯誤從 CQE 的 `res` 回來、不是 syscall 當場回來」，知道 `res` 為什麼是負 errno（我們故意製造 `-EBADF`、`-EFAULT`）、`flags` 那幾個 `IORING_CQE_F_*` 各代表什麼、以及當 CQ 滿了放不下 CQE 時 kernel 怎麼辦（overflow 機制，以及 6.18 為什麼不會像早期那樣默默丟棄）。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 `iou-cc` 編譯。

## 為什麼 CQE 比 SQE 更需要講清楚

SQE 是你寫給 kernel 的工單，CQE 是 kernel 回你的收據。但 CQE 有一個初學者反覆栽跟頭的心智陷阱：**在同步世界，`read()` 失敗當場回 -1 並設 `errno`；在 io_uring，提交當場幾乎不會告訴你成敗——成敗要等對應的 CQE 回來，而且是從 `cqe->res` 讀**。

這個轉變看似小，實則顛覆了錯誤處理的整個流程。Ch 6 我們已經踩過一次（`wait_cqe` 的回傳值 ≠ 操作成敗）；本章把它釘死：CQE 只有三個欄位，但每一個都藏著陷阱——`res` 的正負語意、`flags` 的多重含義、`user_data` 的回帶。把這 16 bytes 讀透，你才算真的會「讀」io_uring 的完成，而不只是會「提交」。

## CQE 只有三個欄位

```c
struct io_uring_cqe {
    __u64 user_data;   // [0]  你在 SQE 設的標籤，原樣帶回
    __s32 res;         // [8]  操作結果：成功是回傳值，失敗是負 errno
    __u32 flags;       // [12] IORING_CQE_F_* 位元旗標
    __u64 big_cqe[];   //      只有開 CQE32 時才有的額外 16 bytes
};
```

Ch 7 的佈局程式印過 `sizeof(struct io_uring_cqe) = 16`——標準 CQE 就這 16 bytes，比 SQE 小得多。原因對稱於 SQE：CQE 只需回報「哪個請求（user_data）、結果如何（res）、有無額外狀態（flags）」，不需要再帶一堆參數。三個欄位逐一拆。

## `res`：負 errno 慣例，故意把它弄成負的

`res` 是 `__s32`（**有號** 32 位元），語意**完全等同對應的同步 syscall 回傳值**：

- **成功**：正值或 0，意義隨 op——read/write 是傳輸的位元組數、accept 是新 fd、openat 是 fd……
- **失敗**：**負的 errno**。`-EBADF` 是 `-9`、`-EFAULT` 是 `-14`、`-EINVAL` 是 `-22`。不是設 global `errno`，是把 errno **取負**塞進 `res`。

這個「負 errno」慣例承襲自 kernel 內部：kernel 函式一向用「回傳負 errno」表示錯誤（而非設 `errno`），io_uring 把這個慣例原封不動暴露到 CQE。所以判斷與取值的標準寫法是：

```c
if (cqe->res < 0) {
    int err = -cqe->res;              // 取負還原成正的 errno
    fprintf(stderr, "failed: %s\n", strerror(err));
} else {
    /* cqe->res 是這個 op 的正常回傳值 */
}
```

### 實測：同一批請求裡，一個成功、兩個不同的失敗

抽象講不如弄壞它。下面一次提交三個 read：一個正常、一個用壞 fd（`-1`）、一個用壞 buffer 位址（`(void*)0x1`）。看三種 `res`：

```c
// ch08_errors.c（節錄）
int good_fd = open("/etc/hostname", O_RDONLY);
char buf[64] = {0};

submit_one_read(&ring, good_fd, buf, sizeof(buf) - 1, 0x1);        // 正常
submit_one_read(&ring, -1,      buf, sizeof(buf) - 1, 0x2);        // 壞 fd
submit_one_read(&ring, good_fd, (void *)0x1, 16,      0x3);        // 壞 buffer

io_uring_submit_and_wait(&ring, 3);

struct io_uring_cqe *cqe; unsigned head, count = 0;
io_uring_for_each_cqe(&ring, head, cqe) {
    if (cqe->res < 0)
        printf("tag 0x%llx: res=%d (%s)\n",
               (unsigned long long)cqe->user_data, cqe->res, strerror(-cqe->res));
    else
        printf("tag 0x%llx: res=%d (OK, %d bytes)\n",
               (unsigned long long)cqe->user_data, cqe->res, cqe->res);
    count++;
}
io_uring_cq_advance(&ring, count);
```

實測輸出：

```
tag 0x1: res=16 (OK, 16 bytes)
tag 0x2: res=-9 (Bad file descriptor)
tag 0x3: res=-14 (Bad address)
```

三件事定住：

1. **成功的 `res=16`** 就是讀到的位元組數——和同步 `read()` 回傳一模一樣。
2. **壞 fd 給 `res=-9`**（`-EBADF`）。注意：`open` 沒失敗（它開的是 hostname），是**第二個請求用 fd=-1 讀**失敗，而這個失敗**不是在 `io_uring_submit` 當場回報**，是乖乖排進去、完成時以 `-9` 從 CQE 回來。這就是 completion 模型：提交成功 ≠ 操作成功。
3. **壞 buffer 給 `res=-14`**（`-EFAULT`，Bad address）。fd 是好的，但 kernel 要把資料寫到 `(void*)0x1` 這個無法存取的位址時失敗。同樣地，`io_uring_submit` 當場不會攔你——它不檢查 `addr` 合不合法，等真正執行 read 時才 `-EFAULT`。

> 這正是本課 README「故意把它弄壞」的訓練：把 fd 關掉、buffer 提早 free、位址填垃圾——看 `res` 怎麼變成 `-EBADF`、`-EFAULT`。**io_uring 的錯誤永遠晚一步、從 CQE 回來**，這個直覺必須親手建立，否則你會一直在 `submit` 的回傳值上找錯誤而遍尋不著。

### `res` 的兩個特例

- **`res = 0`**：對 read 是「讀到 EOF」（不是錯誤！），對 accept/openat 則是合法的 fd 0（雖罕見）。別一律把 0 當失敗。
- **部分完成**：read 要 100 bytes 但只讀到 40，`res = 40`（正常，和 `read()` 一樣可能短讀）。網路 `recv` 尤其常見短讀，要自己判斷還需不需要再送一個請求補讀剩下的。

### `res` 的「成功值」隨 op 而變

失敗一律是負 errno，但**成功值**的意義每個 op 不同——它就是對應同步 syscall 的回傳值。常見幾個先記住，Part 2 各章會再細講：

| op | `res` 成功時是什麼 | 注意 |
|---|---|---|
| `READ`/`RECV` | 讀到的位元組數；`0` = EOF / 對端關閉 | 可能短讀 |
| `WRITE`/`SEND` | 寫出的位元組數 | 可能短寫，要補 |
| `ACCEPT` | 新連線的 fd（正整數） | 設 `IORING_FILE_INDEX_ALLOC` 時回的是 registered 索引 |
| `OPENAT`/`SOCKET` | 新的 fd | 同上 |
| `STATX`/`FSYNC`/`CLOSE` | `0`（成功無回傳值） | 這裡 `0` 是成功，不是 EOF |
| `POLL_ADD` | 觸發的事件遮罩（如 `POLLIN`） | multishot 時每次觸發一個 CQE |

這張表點出一個常被忽略的陷阱：**`res == 0` 的意義隨 op 翻轉**——對 read 是 EOF、對 fsync/close 是成功、對 accept 是「fd 編號剛好是 0」。所以判斷邏輯不能一刀切，要知道這個 CQE 對應的是哪個 op（這又回到 `user_data`：你靠它查到請求、才知道該怎麼解讀 `res`）。

## `flags`：同一個 32 位元，承載多種狀態

`flags`[12] 是 kernel 回報「這個 CQE 除了結果之外，還有什麼要告訴你」的位元旗標。6.18 的 `IORING_CQE_F_*`：

| flag | 位元 | 含義 | 相關章節 |
|---|---|---|---|
| `IORING_CQE_F_BUFFER` | 0 | 用了 provided buffer；**高 16 位元是 buffer ID**（`flags >> 16`） | Ch 14 |
| `IORING_CQE_F_MORE` | 1 | 這個 SQE 還會產生**更多** CQE（multishot 未結束） | Ch 13 |
| `IORING_CQE_F_SOCK_NONEMPTY` | 2 | socket recv 後，**還有資料可讀**（可立刻再收，不必等 poll） | Ch 12、13 |
| `IORING_CQE_F_NOTIF` | 3 | 這是 zero-copy 送的**通知 CQE**（資料已可安全重用，非送出結果本身） | Ch 13、練習 C |
| `IORING_CQE_F_BUF_MORE` | 4 | incremental buffer 消費中，該 buffer 之後還會再回報 | Ch 14 |
| `IORING_CQE_F_32` | 15 | 這是一個 32-byte big CQE（混合 CQE 模式下） | 本章進階 |

四個最該先懂的：

**`IORING_CQE_F_BUFFER`（+ 高 16 位元的 buffer ID）**：當你用 provided buffers（Ch 14），不自帶 buffer，而是讓 kernel 從池子挑一個。完成時 kernel 用這個 flag 告訴你「我用了池裡的 buffer」，並把**用了哪一個**編進 `flags` 的高 16 位元：

```c
if (cqe->flags & IORING_CQE_F_BUFFER) {
    unsigned buf_id = cqe->flags >> IORING_CQE_BUFFER_SHIFT;  // = flags >> 16
    /* 資料在 buf_id 號 buffer 裡，處理完要還回池子 */
}
```

這是 `flags` 「一個欄位多用途」的典型：低位是旗標、高位是資料。Ch 14 會專門處理。

**`IORING_CQE_F_MORE`**：預設一個 SQE 對一個 CQE。但 multishot 請求（一次 accept 持續接受多個連線、一次 recv 持續收多段資料，Ch 13）是**一個 SQE 對多個 CQE**。kernel 用 `F_MORE` 說「這個請求還活著、之後還會送 CQE」。看到 `F_MORE` 就別回收這個請求的狀態；**沒有** `F_MORE` 的那個 CQE 才是這個 multishot 的最後一聲。這些 CQE 全帶**相同的 user_data**（Ch 7 提過），靠 `F_MORE` 而非 user_data 區分它們。

**`IORING_CQE_F_SOCK_NONEMPTY`**：recv 完成時若 socket 緩衝區**還有資料**，設這個 flag。它讓你省一次 poll：不必問「還有沒有資料」，kernel 直接告訴你有，你可以立刻再送一個 recv。網路高吞吐場景的小優化。

**`IORING_CQE_F_NOTIF`**：zero-copy send（`SEND_ZC`，Ch 13）會產生**兩個** CQE——第一個報「送出去了幾 bytes」，第二個（帶 `F_NOTIF`）報「你的 buffer 現在可以安全重用／釋放了」。因為 zero-copy 不複製資料，kernel 直接拿你的 buffer 送，你必須等 `F_NOTIF` 才知道 buffer 脫手。把送出結果和 buffer 可重用分成兩個 CQE，是 zero-copy 的核心機制，練習 C 會實作。

> `flags` 的教訓和 SQE 的 union 一樣：**同一塊位元空間承載多種意義**。讀 CQE 時，先看 `res` 定成敗，再逐位檢查 `flags` 看有沒有額外狀態，別只讀 `res` 就以為完事——漏看 `F_MORE` 會把還活著的 multishot 當結束、漏看 `F_BUFFER` 會不知道資料在哪個 buffer。

## `user_data`：原樣回帶，認請求的唯一依據

第三個欄位 `user_data` Ch 7 已講透：kernel 不解讀、原樣帶回，是你在**亂序完成**中認出「這是哪個請求」的唯一方法。這裡只補一個 CQE 視角的重點：**同一個 user_data 可能回來很多次**（multishot），所以收到 CQE 時，是「用 user_data 查到請求物件、再看 `flags` 決定這個物件還活不活」，而不是「收到 user_data 就把物件回收」。這個順序搞反，就是把還要再用的 multishot 狀態提早釋放——user space 是 bug，kernel 內部對應機制出這種錯就是 UAF（Part 4）。

## CQ overflow：CQ 滿了放不下 CQE 怎麼辦

這是本章的硬核部分。SQ 滿了你會在 `io_uring_get_sqe` 拿到 `NULL`（Ch 6），那是**提交端**的背壓，好處理。但**完成端**呢？kernel 要放 CQE 時，若 CQ 環已滿（你收得太慢），會發生什麼？

### 機制：overflow list ＋ `IORING_SQ_CQ_OVERFLOW`

對照 6.18 原始碼（`io_uring/io_uring.c` 的 `io_cqring_add_overflow`）：CQ 滿時，kernel **不直接丟棄** CQE，而是：

1. 在 kernel 堆上 `kzalloc` 一個 `struct io_overflow_cqe`，把這個 CQE 的 `user_data`/`res`/`flags` 抄進去。
2. 掛到 `ctx->cq_overflow_list` 這條**溢位串列**。
3. 第一次有東西進溢位串列時，在 SQ 環的 flags 設上 `IORING_SQ_CQ_OVERFLOW` 位元——這是給 user 的信號：「有 CQE 溢位了，你該來收」。

之後每當你收割 CQE、CQ 騰出空間，kernel 會把溢位串列裡的 CQE **補回環裡**（`__io_cqring_overflow_flush`）。所以只要你最終收得夠勤，**一個 CQE 都不會少**——這就是 `IORING_FEAT_NODROP` 保證的行為。

### 實測：塞爆一個小 CQ，看溢位與零丟棄

建一個 SQ=4（CQ 預設 =2×SQ=8）的小 ring，分三批共提交 12 個 NOP，中途**不收割**。12 個完成、CQ 只有 8 槽，必然有 4 個溢位：

```c
// ch08_overflow.c（節錄）
struct io_uring_params p; memset(&p, 0, sizeof p);
io_uring_queue_init_params(4, &ring, &p);   // SQ 4 → CQ 8
printf("sq_entries=%u cq_entries=%u NODROP=%s\n", p.sq_entries, p.cq_entries,
       (p.features & IORING_FEAT_NODROP) ? "yes" : "no");

int total = 0;
for (int round = 0; round < 3; round++) {   // 3 批 × 4 = 12 個 NOP
    for (int i = 0; i < 4; i++) {
        struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
        io_uring_prep_nop(sqe);
        io_uring_sqe_set_data64(sqe, total++);
    }
    io_uring_submit(&ring);                  // NOP 在這次 enter 內完成
}
printf("after submit: cq_ready=%u has_overflow=%s koverflow(dropped)=%u\n",
       io_uring_cq_ready(&ring), io_uring_cq_has_overflow(&ring) ? "yes" : "no",
       *ring.cq.koverflow);
/* ...收割全部，統計 reaped... */
```

實測輸出：

```
sq_entries=4 cq_entries=8 NODROP=yes
submitted 12 NOPs
after submit: cq_ready=8 has_overflow=yes koverflow(dropped)=0
reaped 12 CQEs total (expected 12)
final koverflow(dropped)=0
```

逐行讀：

- `cq_entries=8`：CQ 只有 8 槽。`NODROP=yes`：這顆 kernel 有不丟棄保證。
- `cq_ready=8`：環裡塞滿 8 個可收的 CQE。
- `has_overflow=yes`：另外有 CQE 在溢位串列裡等（就是放不進環的那 4 個）。`io_uring_cq_has_overflow` 讀的就是 `IORING_SQ_CQ_OVERFLOW` 位元。
- `koverflow(dropped)=0`：**丟棄計數為 0**。溢位 ≠ 丟棄，它們進了串列。
- `reaped 12`：最後收齊全部 12 個，一個不少。溢位的 4 個隨著收割騰空而被補回環裡。

這就是 `IORING_FEAT_NODROP` 的意義：**溢位會讓 CQE 暫存在 kernel 串列（延遲、佔 kernel 記憶體），但不丟失**。

### NODROP 之前：早期 io_uring 會默默丟

`IORING_FEAT_NODROP` 是 5.5 加入的。在它**之前**，CQ 滿時 kernel 的行為是殘酷的：**直接丟棄**那個 CQE，只把 `cq.koverflow`（`io_cqring_offsets.overflow` 指到的計數器）加一。你的請求做完了，結果卻沒了，你只能從溢位計數器知道「丟了幾個」，但**丟的是哪些、結果是什麼，永遠無從得知**。

這在早期是真實的災難：收得稍慢就靜默掉結果，而且無法恢復。所以 `NODROP` 之後改成溢位串列，寧可佔 kernel 記憶體、寧可慢，也不默默丟。對比：

| 行為 | NODROP 之前（< 5.5） | NODROP（≥ 5.5，含 6.18） |
|---|---|---|
| CQ 滿時 | 直接丟棄 CQE，`koverflow` +1 | 存進 kernel 溢位串列，設 `IORING_SQ_CQ_OVERFLOW` |
| CQE 會不會丟 | **會**，且無法知道丟了什麼 | 不會，除非連 `kzalloc` 溢位節點都失敗 |
| 代價 | 無（但資料丟了） | kernel 堆記憶體、延遲 |
| 你該做 | 祈禱收得夠快 | 看到 overflow flag 就加緊收割 |

> **還是有一種會丟**：6.18 的程式碼裡，若連配置溢位節點的 `kzalloc` 都失敗（記憶體壓力極大），CQE 仍會被丟，此時設 `IO_CHECK_CQ_DROPPED` 並把 `koverflow` 加一。所以 `NODROP` 是「正常情況不丟」，不是「絕對不丟」。生產程式仍應監控 `*ring.cq.koverflow`，非 0 代表真的丟了東西。

### 怎麼避免 overflow

機制再好，溢位本身就是「你收得太慢」的症狀，佔 kernel 記憶體又增延遲。實務上：

- **收割要勤**：event loop 每輪都把現成 CQE 收光（`for_each_cqe` + `cq_advance`），別讓它堆積。
- **CQ 開大一點**：setup 時用 `IORING_SETUP_CQSIZE` 指定比預設（2×SQ）更大的 CQ，給突發流量緩衝。
- **看到 overflow flag 就處理**：`io_uring_cq_has_overflow` 回 true 時，優先收割讓 kernel flush 溢位串列。
- **用 `IOSQE_CQE_SKIP_SUCCESS`**（Ch 7）：對不關心成功結果的請求跳過 CQE，從源頭減少 CQE 數量。

生產程式的最小監控，就是每隔一段時間看一眼溢位與丟棄計數：

```c
if (io_uring_cq_has_overflow(&ring))
    log_warn("CQ overflow: 收割跟不上，考慮開大 CQ 或加快處理");
unsigned dropped = *ring.cq.koverflow;      // 真正被丟掉的數量（正常應永遠為 0）
if (dropped)
    log_error("CQE dropped=%u：有完成結果永久遺失！", dropped);
```

`has_overflow` 為真只是警訊（東西還在串列、沒丟），`koverflow` 非 0 才是真災難（在記憶體壓力下連溢位節點都配不出來、結果掉了）。兩者分開看，別把「溢位」和「丟棄」混為一談——6.18 正常情況只溢位、不丟棄。

## 進階：big CQE（CQE32）

標準 CQE 16 bytes，但某些完成需要回帶**比 `res`+`flags` 更多**的資料——例如 `URING_CMD`（Ch 18）透傳 NVMe 命令，完成時要回裝置層的額外狀態。`IORING_SETUP_CQE32`（setup 時設）把每個 CQE 變成 **32 bytes**，多出的 16 bytes 就是結構末端的 `big_cqe[]`（兩個 `__u64`）。

代價對稱於 SQE128：CQ 的記憶體翻倍。6.18 還加了 `IORING_SETUP_CQE_MIXED`——允許同一個 ring 混用 16-byte 和 32-byte CQE，32-byte 的那些會在 `flags` 設 `IORING_CQE_F_32` 讓你辨識。這也是為什麼 `io_uring_cqe_seen` 內部是 `io_uring_cq_advance(ring, io_uring_cqe_nr(cqe))`（Ch 6 提過）——一個 big CQE 佔 2 個環槽，推進格數得算對。平常用不到，知道它存在、以及它為什麼讓「推進一個 CQE」不總是「推進一格」即可。

## 踩雷集錦

1. **在 `io_uring_submit` 的回傳值上找操作錯誤**：submit 回的是「提交了幾個 SQE」，**不含**操作成敗。壞 fd、壞 buffer 都要等 CQE 的 `res` 才知道。這是 completion 模型第一大坑。
2. **把 `res` 當無號看**：`res` 是 `__s32`，失敗是負值。用 `unsigned` 接或 `if (cqe->res)` 判真假，會把 `-9` 當成巨大正數。一律 `if (cqe->res < 0)`，取 errno 用 `-cqe->res`。
3. **`res = 0` 一律當錯**：read 的 0 是 EOF、accept/openat 的 0 是合法 fd。0 不是錯誤，是「成功且回傳 0」。
4. **只讀 `res` 不看 `flags`**：漏看 `F_MORE` 把活著的 multishot 當結束回收、漏看 `F_BUFFER` 不知道資料在哪個 provided buffer、漏看 `F_NOTIF` 把 zero-copy 的通知 CQE 當成送出結果。`res` 定成敗，`flags` 定狀態，兩個都要讀。
5. **收割太慢導致 overflow 還不自知**：6.18 不會默默丟（有串列），但溢位佔 kernel 記憶體又增延遲，且極端記憶體壓力下仍可能真丟。監控 `*ring.cq.koverflow`，非 0 就是真的掉了資料。
6. **假設一個 SQE 一定一個 CQE**：multishot（`F_MORE`）一對多、zero-copy send 一對二（結果 + `F_NOTIF`）、`IOSQE_CQE_SKIP_SUCCESS` 一對零。靠 CQE 數量對帳前，先想清楚你的請求是哪種。

## 本章重點整理

- CQE 固定 **16 bytes**，三個欄位：`user_data`（認請求）、`res`（成敗）、`flags`（額外狀態）。
- **`res` 是負 errno 慣例**：成功是正值/0（read 的位元組數、accept 的 fd……），失敗是 `-errno`（`-EBADF`=-9、`-EFAULT`=-14）。錯誤**從 CQE 回來，不是 submit 當場**。
- `flags` 一個欄位多用途：`F_BUFFER`（高 16 位是 buffer ID）、`F_MORE`（multishot 未結束）、`F_SOCK_NONEMPTY`（還有資料）、`F_NOTIF`（zero-copy buffer 可重用）。
- **CQ overflow**：CQ 滿時 kernel 把 CQE 存進溢位串列並設 `IORING_SQ_CQ_OVERFLOW`，收割騰空後補回環——`IORING_FEAT_NODROP`（≥5.5）保證正常情況不丟；NODROP 之前會默默丟棄只加 `koverflow` 計數。
- 一個 SQE 不保證一個 CQE：multishot 一對多、zero-copy 一對二、CQE_SKIP_SUCCESS 一對零。

## 自我檢核

- [ ] 有人用 io_uring 讀檔失敗卻在 `io_uring_submit` 回傳值找不到錯誤，我能講出錯誤到底在哪、為什麼
- [ ] 我能寫出判斷 CQE 成敗、取 errno 的標準寫法，並說明為什麼 `res` 要當有號看
- [ ] 四個 `IORING_CQE_F_*` 我能各講一句，並知道怎麼從 `flags` 取出 provided buffer 的 ID
- [ ] 我能描述 CQ 滿時 kernel 做什麼（溢位串列、`IORING_SQ_CQ_OVERFLOW`），以及 `NODROP` 前後差別
- [ ] 我知道哪些情況下一個 SQE 會產生 0 個、多個 CQE，以及這對「用 CQE 數對帳」的影響
- [ ] 我能說出什麼時候需要 big CQE（CQE32）、它的代價，以及為什麼它讓「收一個 CQE」不等於「推一格」

## 延伸閱讀

### 官方文件 / 原始碼

- **[io_uring/io_uring.c 的 overflow 路徑](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c)**（v6.18）
  - **讀哪裡**：`io_cqring_add_overflow`、`__io_cqring_overflow_flush`、`io_alloc_ocqe`——本章 overflow 機制的一手來源，就在你 `ref/linux/` 裡，`grep -n overflow io_uring.c` 直接定位
  - **為什麼值得讀**：親眼看到「`kzalloc` 失敗才真丟、否則掛串列」這一行，勝過任何二手說明；Ch 25 完成路徑會再回來

- **[io_uring_wait_cqe(3)](https://man7.org/linux/man-pages/man3/io_uring_wait_cqe.3.html)** 與 **[io_uring_cqe_get_data(3)](https://man7.org/linux/man-pages/man3/io_uring_cqe_get_data.3.html)**
  - **讀哪裡**：`cqe->res` 的語意說明、回傳值；man3 的 CQE 相關頁把 `res`/`flags` 的約定寫得很清楚
  - **注意**：查某個 flag 的精確語意，man page 比猜快

### 技術文章

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **這篇說什麼**：`F_MORE`、`F_SOCK_NONEMPTY`、`F_NOTIF`、provided buffer 的 ID 編碼，都是網路場景的一手說明
  - **讀哪裡**：multishot 與 zero-copy 兩節，正好對應本章 `flags` 那幾個旗標的實際用法
  - **前提**：本章讀完，Ch 13 之前的最佳銜接

### LWN

- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** — Jonathan Corbet（LWN）
  - **讀哪裡**：提到 overflow 與 `NODROP` 演進的段落；LWN 的 io_uring tag 下有追蹤每次 CQE 機制變更的後續報導
  - **為什麼值得讀**：想知道「為什麼早期會丟、後來怎麼修的」，LWN 的時間線最清楚

SQE 怎麼填、CQE 怎麼讀，這兩張單據我們都拆透了。但我們一直是「填一個、submit 一次、等一個」的最簡模式。下一章進入**提交模型**：怎麼批次提交一大把、怎麼用 `io_uring_submit_and_wait` 省 syscall、以及最關鍵的 SQPOLL——讓 kernel 開一個 thread 輪詢 SQ，達到**連提交的 syscall 都不用下**的零 syscall 境界。

→ [Ch 9 提交模型：SQPOLL、batching、GETEVENTS](./09-submission-models.md)
