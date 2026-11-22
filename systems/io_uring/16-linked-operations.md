# Ch 16 — Linked operations 與 drain

> **目標**：學會把多個 SQE 串成有相依性的鏈，讓「開檔→讀→關檔」這種序列在 kernel 裡一條龍跑完，不用每步都回 user 空間接力。你會掌握 `IOSQE_IO_LINK`/`IO_HARDLINK` 的差別、鏈斷掉時 `-ECANCELED` 的傳播、`IOSQE_IO_DRAIN` 這個排空屏障，以及 `IOSQE_CQE_SKIP_SUCCESS`；並親手看 short read 怎麼意外斷鏈。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 6.18.58.1-microsoft-standard-WSL2）。link/drain 從 5.3 就在；`IOSQE_CQE_SKIP_SUCCESS` 5.17+。

## 為什麼需要 linked operations

io_uring 的預設語意是「SQE 之間互不相干、可平行、可亂序執行」。這對獨立的 I/O 很理想——你一次丟一百個 read，kernel 愛怎麼排就怎麼排，誰先好誰先回 CQE。

但很多工作天生有**順序相依**：要先 `open` 拿到 fd，才能 `read`；要先 `read` 到資料，才能 `write` 出去；要先 `connect`，才能 `send`。如果每一步都「提交→等 CQE→拿結果→再提交下一步」，那你每一步都付一次「回 user 空間、再進 kernel」的來回，相依越長來回越多，io_uring「批次提交」的優勢就被吃光了。

linked operations 讓你**一次提交一整條相依鏈**：kernel 保證鏈內的操作照順序執行，前一個成功了才做下一個，中間不回 user 空間。一個 `open→read→close` 三步，一次 `io_uring_submit` 送出去，kernel 內部串起來跑完。

## 先建立直覺：鏈就是「前一個成功才做下一個」

```
  三個獨立 SQE（預設）              一條 link（掛 IOSQE_IO_LINK）

  [open]  ┐                         [open ]──LINK──┐  成功才往下
  [read]  ├─ 可平行/亂序             [read ]──LINK──┤  成功才往下
  [close] ┘  誰先好誰先回            [close]        ┘  鏈尾

                                    open 失敗 ──► read、close 全 -ECANCELED
```

規則是這樣：

- 在一個 SQE 掛 `IOSQE_IO_LINK`，表示「**下一個** SQE 和我同一條鏈，要等我完成（且成功）才輪到它」。
- 一條鏈由連續掛 `IO_LINK` 的 SQE 組成，**最後一個不掛**（它是鏈尾）。
- 鏈**不保證**和其他鏈或獨立 SQE 之間的順序——鏈只約束**鏈內**的先後。
- 前一個失敗（`res < 0`），**整條鏈的後續**全部不執行，各回 `-ECANCELED`。

## IO_LINK vs IO_HARDLINK

兩種鏈旗標，差別只在「前一個**失敗**時要不要繼續」：

| | `IOSQE_IO_LINK` | `IOSQE_IO_HARDLINK` |
|---|---|---|
| 前一個成功 | 做下一個 | 做下一個 |
| 前一個**失敗** | 斷鏈，後續全 `-ECANCELED` | **照做**下一個（硬鏈不管成敗，只保證順序） |
| 用途 | 有資料相依（read 失敗就別 write 了） | 只要順序、不要相依（例如一串 fsync） |

`IO_LINK` 是「成功才接力」，`IO_HARDLINK` 是「不管成敗，就照這個順序做」。絕大多數情況你要的是 `IO_LINK`——因為相依鏈的重點正是「前面壞了後面就沒意義」。`IO_HARDLINK` 用在你只想保證執行順序、每步彼此獨立的場景。

## 範例：open → read → close 鏈，以及鏈怎麼斷

這條鏈有個關鍵難點：`read` 需要 `open` 產生的 fd，但建 SQE 時 `open` 還沒跑，fd 根本還不存在。解法是 Ch 15 的 **direct descriptor**：`openat_direct` 把檔案開進一個 fixed file 槽，`read` 用 `IOSQE_FIXED_FILE` 指同一個槽，`close_direct` 關那個槽——全程不碰真實 fd 數字，所以提交時就能把三步串好。

```c
static void run_chain(struct io_uring *ring, const char *path,
                      unsigned readlen, const char *tag) {
    char buf[64] = {0};
    const int SLOT = 0;

    // (1) openat_direct：開進 fixed slot 0
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    io_uring_prep_openat_direct(sqe, AT_FDCWD, path, O_RDONLY, 0, SLOT);
    sqe->flags |= IOSQE_IO_LINK;                     // 下一個要等我成功
    io_uring_sqe_set_data64(sqe, 1);

    // (2) read：從 slot 0 讀（fd 填索引 + FIXED_FILE）
    sqe = io_uring_get_sqe(ring);
    io_uring_prep_read(sqe, SLOT, buf, readlen, 0);
    sqe->flags |= IOSQE_FIXED_FILE | IOSQE_IO_LINK;
    io_uring_sqe_set_data64(sqe, 2);

    // (3) close：關 slot 0。已用 file_index 指定槽，不加 FIXED_FILE
    sqe = io_uring_get_sqe(ring);
    io_uring_prep_close_direct(sqe, SLOT);
    io_uring_sqe_set_data64(sqe, 3);

    io_uring_submit(ring);   // 一次送三步
    ...
}
```

我們跑三種情境：檔案 `/tmp/ch16_ok.txt` 是 16 bytes。

**(A) 正常鏈**：read 剛好要求 16 bytes（= 檔案大小）。
**(B) open 失敗**：開一個不存在的檔。
**(C) short read**：read 要求 63 bytes，但檔案只有 16。

實測輸出：

```
=== chain [normal] path=/tmp/ch16_ok.txt readlen=16 ===
  [1 open ] res=0
  [2 read ] res=16  讀到 16 bytes
  [3 close] res=0
=== chain [broken-open] path=/tmp/does_not_exist_xyz readlen=16 ===
  [1 open ] res=-2  (No such file or directory)
  [2 read ] res=-125  (-ECANCELED：鏈前段斷了，被連帶取消)
  [3 close] res=-125  (-ECANCELED：鏈前段斷了，被連帶取消)
=== chain [short-read] path=/tmp/ch16_ok.txt readlen=63 ===
  [1 open ] res=0
  [2 read ] res=16  讀到 16 bytes
  [3 close] res=-125  (-ECANCELED：鏈前段斷了，被連帶取消)
```

三種情境各講一件事：

- **(A) 正常**：三步依序成功，每步照樣各回一個 CQE（鏈不會合併 CQE，一步一個）。
- **(B) open 失敗**：open 回 `-ENOENT`（-2），這是失敗（`res < 0`）。於是 read 和 close **根本沒執行**，各回 `-ECANCELED`（-125）。注意 `-ECANCELED` 不是「它跑了但被取消」，是「它**沒跑**，kernel 直接給你一個取消的 CQE」。
- **(C) short read**：open 成功、read 成功回 16 bytes——但你**要求的是 63**。io_uring 把「讀到的比要求的少」視為鏈的斷點，於是 close 被 `-ECANCELED`。這是最陰險的坑，下面單獨講。

## 陷阱重點：short read 會斷鏈

(C) 的行為會嚇到人：read **成功**了（`res=16 > 0`），為什麼鏈還是斷了?

因為 io_uring 鏈的「成功」判準對讀寫類操作不只是「`res >= 0`」，而是「**有沒有做滿你要求的量**」。read 要求 63、只給你 16，kernel 認為這個操作「沒完全做到」，標記它失敗（內部的 `REQ_F_FAIL`），斷掉後面的鏈。檔案讀到 EOF、socket recv 只收到部分資料，都會觸發這個。

這帶來一個實務準則：**鏈裡的 read/recv，要嘛你確定能讀滿，要嘛別放進鏈**。對 socket，`recv` 可以加 `MSG_WAITALL`（要 kernel 收滿才回）來避免 short recv 斷鏈；對檔案，你通常無法保證讀滿（EOF 隨時到），所以「讀未知長度的檔案」這種鏈要特別小心，常見做法是不把後續強綁進同一條鏈。

> **故意弄壞它**：把 (A) 的 `readlen` 從 16 改成 15。read 會回 15（成功、但只讀了你要求的 15 的全部?不——你要求 15、給你 15，這是**讀滿**，鏈不斷）。再改成 17：你要求 17、檔案只有 16，給你 16 < 17，short read，close 又被 `-ECANCELED`。親手試這幾個邊界，你會徹底記住「斷鏈看的是『有沒有做滿要求』，不是『res 正不正』」。

## 底層機制：REQ_F_FAIL 與 io_fail_links

鏈的實作在 `io_uring/io_uring.c`（6.18）。幾個關鍵：

鏈的旗標合在一起判斷：

```c
#define IO_REQ_LINK_FLAGS (REQ_F_LINK | REQ_F_HARDLINK)
```

當一個請求失敗（或 short read 標了 `REQ_F_FAIL`），它進入 `IO_DISARM_MASK`：

```c
/* requests with any of those set should undergo io_disarm_next() */
#define IO_DISARM_MASK (REQ_F_ARM_LTIMEOUT | REQ_F_LINK_TIMEOUT | REQ_F_FAIL)
```

失敗請求完成時，kernel 呼叫 `io_disarm_next()`→`io_fail_links()`，把鏈上**後續每一個**請求用 `-ECANCELED` 送掉。提交階段若鏈頭已標失敗，`req_fail_link_node(head, -ECANCELED)` 直接把整條標記掉。這就是 (B)(C) 看到一連串 `-125` 的來源。

short read 的「失敗」標記在讀寫完成路徑 `__io_complete_rw_common`（`rw.c`）：

```c
static void __io_complete_rw_common(struct io_kiocb *req, long res)
{
    if (res == req->cqe.res)
        return;                     // 做滿了，沒事
    if ((res == -EOPNOTSUPP || ...) && io_rw_should_reissue(req)) {
        req->flags |= REQ_F_REISSUE | ...;
    } else {
        req_set_fail(req);          // 沒做滿 → 標 REQ_F_FAIL
        req->cqe.res = res;
    }
}
```

`req_set_fail` 設上 `REQ_F_FAIL`，於是這個「成功回了 16 bytes」的 read 仍落入 `IO_DISARM_MASK`，斷掉後面的 close。**CQE 給你看到的是 `res=16`（真實讀到的量），但鏈的內部狀態已經是「失敗」**——這就是為什麼 CQE 看起來成功、鏈卻斷了。

## IOSQE_IO_DRAIN：排空屏障

link 約束的是「鏈內」的順序，但有時你要的是**全域屏障**：「在我之前提交的所有請求都完成了，才執行我」。這是 `IOSQE_IO_DRAIN`。

```
提交順序: [A] [B] [C, DRAIN] [D] [E]
                        │
         A、B 必須全部完成 ──┘ 才開始 C；C 完成後 D、E 才照常
```

掛 `IOSQE_IO_DRAIN` 的 SQE 會等**整個 ring 裡在它之前的所有請求**都完成，才開始跑；而它之後的請求也要等它跑完。它是一道橫跨整個 ring 的牆。

用途：某個操作必須在前面全部落地後才安全。例如「先把一批 write 全部刷完，再 fsync」、或「清掉所有 in-flight 請求後再做一個 reset」。代價很高——它強制序列化，打斷 io_uring 的平行性，所以只在真的需要全域順序時用。kernel 端 drain 的請求會被掛進一條 defer list（`io_uring.c` 的 `drain_seen` 邏輯），等前面清空才放行。

drain 和 link 的區別要分清：**link 是局部的鏈內順序，drain 是全域的「等前面全清空」**。你可以兩個都用，但多數時候用 link 就夠。

## IOSQE_CQE_SKIP_SUCCESS：成功就別給我 CQE

鏈有個副作用：每一步都產一個 CQE。一條 `open→read→close`，你收三個 CQE，但你可能只關心 read 的結果——open 成功與否你只在它失敗時才在乎，close 的結果通常根本不看。

`IOSQE_CQE_SKIP_SUCCESS`（5.17+）讓一個 SQE **成功時不產 CQE**，只有失敗時才產。掛在 open 和 close 上，正常情況你只會收到 read 一個 CQE，省下收割成本：

```c
// open：成功就別回 CQE（失敗才回，好讓我知道鏈斷了）
sqe->flags |= IOSQE_IO_LINK | IOSQE_CQE_SKIP_SUCCESS;
...
// close：成功就別回 CQE
sqe->flags |= IOSQE_CQE_SKIP_SUCCESS;
```

這在長鏈特別有用：一條十步的鏈，你可能只要最後一步的結果，前九步成功時全部 skip，CQ 壓力從 10 降到 1。注意「成功才 skip」——失敗一定會產 CQE（否則你不知道鏈斷在哪），這個設計很合理：你需要知道壞消息，不需要每個好消息。

## 對比與取捨

| 機制 | 約束範圍 | 失敗行為 | 典型用途 |
|---|---|---|---|
| `IOSQE_IO_LINK` | 鏈內，成功才接力 | 後續 `-ECANCELED` | open→read→close、connect→send |
| `IOSQE_IO_HARDLINK` | 鏈內，只保證順序 | 後續照做 | 一串無相依但要順序的操作 |
| `IOSQE_IO_DRAIN` | 全域，等前面全完成 | — | 刷完再 fsync、全域 reset 點 |
| `IOSQE_CQE_SKIP_SUCCESS` | 單一 SQE | 失敗仍產 CQE | 長鏈只收尾步結果 |

## 踩雷集錦

1. **以為 short read/recv 不會斷鏈**：範例 (C)。read 回正值也可能是「沒做滿」，被標 `REQ_F_FAIL` 斷鏈。鏈裡的讀寫要能讀滿，recv 加 `MSG_WAITALL`，否則別進鏈。

2. **把 `-ECANCELED` 當成「操作跑了但被取消」**：鏈斷後的後續請求是**根本沒執行**。`-ECANCELED` 代表「因為前面壞了，我沒做」，不是做到一半被砍。

3. **鏈尾忘了拿掉 `IO_LINK`**：如果最後一個 SQE 也掛 `IO_LINK`，kernel 會等「下一個」SQE 來組鏈，但那個 SQE 可能是別的無關請求，被意外併進鏈，順序與取消行為全亂。鏈尾**一定不掛** `IO_LINK`。

4. **用 DRAIN 來做「鏈內順序」**：DRAIN 是全域屏障，會把整個 ring 序列化，效能崩塌。鏈內順序用 `IO_LINK`，DRAIN 只在真的需要「等前面全部清空」時用。

5. **鏈裡用普通 `read` 卻期待拿到 open 的 fd**：建 SQE 時 open 還沒跑，真實 fd 不存在。要用 direct descriptor（`openat_direct` + `FIXED_FILE` + `close_direct`）讓三步靠「槽位索引」串起來，而不是靠「提交時還不存在的 fd」。

## 進階：linked timeout 與鏈的組合

鏈最強大的搭配是 **linked timeout**（下一章主角）：在一條鏈的某個操作後面接一個 `IORING_OP_LINK_TIMEOUT`，給前一個操作設逾時。例如 `connect`（LINK）→`link_timeout`，連線超過 2 秒就取消 connect，connect 被取消後鏈再往下斷。這讓你能對「可能永遠卡住」的操作（connect、recv）加上時限，而不用額外的 timer 執行緒。

另一個組合是把 link 和 provided buffer、multishot 混用時的順序考量：multishot 操作（會產多個 CQE）放進鏈要很小心——鏈的「完成」語意和 multishot 的「持續產 CQE」語意會打架。一般準則是 multishot 操作不放進鏈中段，只當鏈尾或獨立提交。

## 進階：鏈的原子性與提交邊界

一個常被忽略的限制：**一條鏈必須在同一次 `io_uring_submit` 裡整條提交**。你不能先 submit 鏈的前半、再 submit 後半——kernel 組鏈是在提交時把連續、且前一個掛 `IO_LINK` 的 SQE 串起來，提交邊界就是鏈的邊界。

這帶來一個實務後果：鏈的長度受限於 SQ 環的深度。如果你的鏈有 20 步，但 ring 只開了深度 8，`io_uring_get_sqe` 在第 9 步會回 `NULL`（SQ 滿了），你拿不到 SQE 就組不完鏈。深鏈要搭配夠深的 ring，或改用「鏈尾觸發下一批」的模式——鏈的最後一步完成後，在它的 CQE handler 裡提交下一條鏈。

kernel 端在 `io_submit_sqe`（`io_uring.c`）維護一個「當前鏈頭」指標：遇到掛 `IO_LINK` 的 SQE 就把它接到鏈上、不立刻 issue；遇到沒掛 `IO_LINK` 的（鏈尾）才把整條鏈一起送去執行。這也是為什麼鏈尾漏掉「不掛 `IO_LINK`」會出事——kernel 一直在等下一個 SQE 來當鏈的延續，你的鏈永遠組不完、或把下一個無關請求吞進來。

還有一個細節：鏈內各 SQE 的 `user_data` 是**各自獨立**的，CQE 也是一步一個（除非用 `CQE_SKIP_SUCCESS`）。鏈不會把多步合併成一個 CQE——它只約束執行順序與失敗傳播，不改變「一個 SQE 對一個 CQE」的基本帳。收割時要記得一條 N 步的鏈（沒 skip）會回 N 個 CQE。

## 本章重點整理

- `IOSQE_IO_LINK` 把連續 SQE 串成鏈，前一個成功才做下一個，鏈尾不掛旗標；前一個失敗則後續全 `-ECANCELED`（代表沒執行，不是做到一半被砍）。
- `IO_HARDLINK` 只保證順序、不管成敗；`IO_LINK` 是成功才接力。多數相依鏈用 `IO_LINK`。
- 鏈內 read/recv 的 **short read（沒讀滿要求的量）會斷鏈**，即使 `res` 是正值——kernel 標 `REQ_F_FAIL`。recv 用 `MSG_WAITALL` 規避，檔案讀未知長度要小心。
- `IOSQE_IO_DRAIN` 是全域排空屏障：等它之前所有請求完成才執行，之後的也等它。代價是序列化，只在需要全域順序時用。
- `IOSQE_CQE_SKIP_SUCCESS`：成功不產 CQE、失敗才產。長鏈只收尾步結果時省 CQ 壓力。
- open→read→close 這類「需要前一步產物」的鏈，用 direct descriptor（槽位索引）串接，因為真實 fd 在提交時還不存在。

## 自我檢核

- [ ] 我能說出 `IO_LINK` 和 `IO_HARDLINK` 在「前一個失敗」時的差別
- [ ] 我能解釋為什麼鏈斷後的 `-ECANCELED` 代表「沒執行」而非「被中斷」
- [ ] 我知道 short read 為什麼會斷鏈，即使 CQE 的 `res` 是正值，以及怎麼規避
- [ ] 我能分清 `IO_LINK`（局部鏈內順序）和 `IO_DRAIN`（全域屏障）
- [ ] 我知道 open→read→close 鏈為什麼要用 direct descriptor 而非真實 fd

## 延伸閱讀

### man page

- **[io_uring_enter(2)](https://man7.org/linux/man-pages/man2/io_uring_enter.2.html)**
  - **讀哪裡**：`IOSQE_IO_LINK`、`IOSQE_IO_HARDLINK`、`IOSQE_IO_DRAIN`、`IOSQE_CQE_SKIP_SUCCESS` 的定義段
  - **學什麼**：每個旗標的精確語意與彼此互動，特別是 DRAIN 的全域性

### kernel 原始碼

- **[`io_uring/io_uring.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c)**（Linux 6.18）
  - **讀哪裡**：`io_fail_links`、`io_disarm_next`、`IO_DISARM_MASK`、`io_submit_sqe` 的鏈組裝邏輯
  - **學什麼**：鏈怎麼在提交時被串起、失敗怎麼沿鏈傳播；配合 Part 3 的 Ch 23 提交路徑

- **[`io_uring/rw.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/rw.c)**（Linux 6.18）
  - **讀哪裡**：`__io_complete_rw_common` 的 `req_set_fail`
  - **學什麼**：short read 如何被標成失敗，解開「res 正值卻斷鏈」的謎

### 部落格 / 文章

- **[Lord of the io_uring — Link](https://unixism.net/loti/tutorial/link_liburing.html)** — Shuveb Hussain
  - **這篇說什麼**：用 liburing 示範 link 的基本用法與 CQE 收割
  - **讀哪裡**：整頁；對照本章的 open→read→close，注意它對 short read 的處理
  - **前提**：讀過 Ch 6 liburing 入門

- **[LWN: The rapid growth of io_uring](https://lwn.net/Articles/810414/)** — Jonathan Corbet
  - **讀哪裡**：link 與 drain 最早引入的那幾段
  - **學什麼**：這些機制當初要解的問題（為什麼要在 kernel 內串鏈，而非 user 空間接力）

鏈讓操作有了順序，但「永遠不完成的操作」還是會卡住整條鏈——下一章給它們裝上時限：timeout 與 linked timeout，以及主動取消 in-flight 請求的 cancel。

→ [Ch 17 Timeouts 與 cancel](./17-timeouts-cancel.md)
