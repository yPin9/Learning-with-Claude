# Ch 19 — 原始碼地圖：6.18 的 io_uring/ 目錄結構

> **目標**：在一行 code 都還沒讀之前，先建立一張地圖。讀完這一章，你拿到任何一個 io_uring 的函式名、CVE patch、trace point，都能在三秒內知道「它在哪個檔、屬於哪個子系統、我該從哪個 header 去追型別」。這是 Part 3 其餘十一章的索引頁。

> **環境**：kernel 6.18 原始碼（`~/io_uring_course/ref/linux/io_uring/`，38 個 `.c` + 38 個 `.h`；核心型別在 `include/linux/io_uring_types.h`）。

## 為什麼先讀地圖，而不是直接跳進 io_uring.c？

Part 0–2 你是從 user 端看 io_uring：填 SQE、推 tail、收 CQE。那一面乾淨、對稱、好懂。kernel 這一面不是——它是一個把半個 kernel 串在一條路徑上的子系統：VFS、block layer、網路 stack、slab、RCU、work queue、task_work、poll wait queue，全部在這裡交會。`io_uring/io_uring.c` 一個檔就 4088 行。你如果一開始就 `vim io_uring.c` 從頭讀，三十分鐘後會迷失在 `io_cqring_wait()` 的某個 `DEFER_TASKRUN` 分支裡，忘了自己本來想找什麼。

所以我們先畫地圖。io_uring 的原始碼有一個非常清楚的切分原則，摸熟它，你就能「不讀全檔也知道東西在哪」：

- **核心路徑**（提交、完成、request 生命週期、ring 建立拆除）全部集中在 `io_uring.c`。
- **每一類 opcode** 各自一個檔：read/write 在 `rw.c`、網路在 `net.c`、timeout 在 `timeout.c`……它們只實作 `->prep()` 和 `->issue()` 兩個 callback，然後登記到 `opdef.c` 的 dispatch 表。
- **跨越多個 opcode 的共用機制**（provided buffer、registered 資源、poll、io-wq worker pool、cancel）各自一個檔。

這個切分的好處是：**opcode 的程式碼和核心引擎解耦**。你要加一個新 opcode（練習 D 就會做），不用動 `io_uring.c`，只要寫一個新檔、填兩個函式、在 `opdef.c` 加一列。這也是為什麼 io_uring 能在幾乎每個 kernel release 都塞進新 op——引擎不動，只長葉子。

## 先建立直覺：三層同心圓

把 `io_uring/` 想成三層：

```
          ┌─────────────────────────────────────────────┐
          │  第 3 層：opcode 實作（葉子，各自一個檔）       │
          │  rw.c net.c timeout.c poll.c openclose.c      │
          │  statx.c fs.c splice.c sync.c xattr.c         │
          │  futex.c waitid.c msg_ring.c uring_cmd.c ...  │
          │    每個檔只提供 ->prep() / ->issue() callback  │
          └───────────────▲─────────────────────────────┘
                          │  經由 opdef.c 的 io_issue_defs[] 表 dispatch
          ┌───────────────┴─────────────────────────────┐
          │  第 2 層：共用機制（跨 opcode）                │
          │  kbuf.c(provided buf)  rsrc.c(registered)     │
          │  io-wq.c(worker pool)  sqpoll.c(SQ poll 執行緒)│
          │  cancel.c  filetable.c  notif.c  eventfd.c    │
          │  napi.c  alloc_cache.c  memmap.c              │
          └───────────────▲─────────────────────────────┘
                          │  被核心引擎呼叫
          ┌───────────────┴─────────────────────────────┐
          │  第 1 層：核心引擎  io_uring.c (4088 行)        │
          │  setup / enter / 提交路徑 / 完成路徑 /          │
          │  request alloc/free / ring teardown           │
          │       外加 opdef.c（dispatch 表）、register.c   │
          │       （io_uring_register 的 100+ 子命令）      │
          └─────────────────────────────────────────────┘
                          │
          ┌───────────────┴─────────────────────────────┐
          │  型別層 include/linux/io_uring_types.h         │
          │  struct io_ring_ctx / io_kiocb / io_rings ...  │
          └─────────────────────────────────────────────┘
```

Part 3 剩下的章節就是順著這三層往下挖：Ch 20–21 是型別層（`io_ring_ctx`、`io_kiocb`），Ch 22–25 是第 1 層的核心路徑，Ch 26–30 是第 2 層的各個共用機制。本章把每個檔先點名一遍。

## 權威來源：Makefile 的物件清單

想知道「6.18 的 io_uring 到底編了哪些檔」，不要用 `ls`（會混進你不確定有沒有編進去的東西），要讀 `io_uring/Makefile`。它才是權威：

```makefile
# io_uring/Makefile（6.18，節錄）
obj-$(CONFIG_IO_URING)		+= io_uring.o opdef.o kbuf.o rsrc.o notif.o \
					tctx.o filetable.o rw.o poll.o \
					eventfd.o uring_cmd.o openclose.o \
					sqpoll.o xattr.o nop.o fs.o splice.o \
					sync.o msg_ring.o advise.o openclose.o \
					statx.o timeout.o cancel.o \
					waitid.o register.o truncate.o \
					memmap.o alloc_cache.o query.o
obj-$(CONFIG_IO_URING_ZCRX)	+= zcrx.o
obj-$(CONFIG_IO_WQ)		+= io-wq.o
obj-$(CONFIG_FUTEX)		+= futex.o
obj-$(CONFIG_EPOLL)		+= epoll.o
obj-$(CONFIG_NET_RX_BUSY_POLL)	+= napi.o
obj-$(CONFIG_NET) += net.o cmd_net.o
obj-$(CONFIG_PROC_FS) += fdinfo.o
obj-$(CONFIG_IO_URING_MOCK_FILE) += mock_file.o
```

這段立刻透露幾件事：

- **`io-wq.o`、`futex.o`、`epoll.o`、`napi.o`、`net.o`、`fdinfo.o` 都是條件編譯**。沒開 `CONFIG_NET`，`net.c` 根本不進 kernel——所以「某台機器上 io_uring 支援哪些 op」除了版本，也看 config。這解釋了 Ch 0 那句「WSL kernel 和 upstream 可能有 config 差異」。
- **`mock_file.c` 是 6.18 相對新的東西**（`CONFIG_IO_URING_MOCK_FILE`），給測試用的假檔案，不是正式功能路徑，讀原始碼時可以跳過。
- **核心永遠編進去的**那一大串（`io_uring.o opdef.o kbuf.o rsrc.o …`）就是第 1、2 層。

## 第 1 層：核心引擎

### io_uring.c — 主體（4088 行）

整個子系統的心臟。你在 Part 3 會反覆回到這個檔。它包含：

- **兩個 syscall 的進入點**：`SYSCALL_DEFINE2(io_uring_setup, …)`、`SYSCALL_DEFINE6(io_uring_enter, …)`（第三個 `io_uring_register` 在 `register.c`）。
- **ring 建立**：`io_uring_create()`、`io_allocate_scq_urings()`、`io_uring_get_file()`（Ch 22 全解）。
- **提交路徑**：`io_submit_sqes()` → `io_get_sqe()` → `io_init_req()` → `io_submit_sqe()`（Ch 23）。
- **派發**：`io_issue_sqe()` → `__io_issue_sqe()`，呼叫 `def->issue()`（Ch 24）。
- **完成路徑**：`io_req_complete_post()`、`io_req_complete_defer()`、`__io_submit_flush_completions()`、`io_cqring_wait()`（Ch 25）。
- **request 的配置與回收**：`__io_alloc_req_refill()`、`io_free_req()`、`io_alloc_req()`（Ch 21）。
- **ring 拆除**：`io_ring_ctx_wait_and_kill()`、`io_ring_exit_work()`（Ch 30）。

它太大了，所以讀它要靠「函式名定位」而不是「從頭讀」。下面教你 grep。

### opdef.c — opcode dispatch 表（858 行）

io_uring 的 `switch(opcode)` 不是 `switch`，是一張**函式指標表**。這是 kernel 相容性與熱路徑效能的關鍵設計，Ch 23/24 的核心。表的每一列長這樣：

```c
/* io_uring/opdef.c */
const struct io_issue_def io_issue_defs[] = {
	[IORING_OP_NOP] = {
		.audit_skip		= 1,
		.iopoll			= 1,
		.prep			= io_nop_prep,
		.issue			= io_nop,
	},
	[IORING_OP_READ] = {
		.needs_file		= 1,
		.unbound_nonreg_file	= 1,
		.pollin			= 1,
		.buffer_select		= 1,
		.plug			= 1,
		.audit_skip		= 1,
		.ioprio			= 1,
		.iopoll			= 1,
		.iopoll_queue		= 1,
		.async_size		= sizeof(struct io_async_rw),
		.prep			= io_prep_read,
		.issue			= io_read,
	},
	/* ... 一路到 IORING_OP_LAST ... */
};
```

`struct io_issue_def`（定義在 `opdef.h`）每一列用 bitfield 記錄這個 op 的**性質**：需不需要 file（`needs_file`）、能不能 poll（`pollin`/`pollout`）、支不支援 buffer selection、要不要 block plug、async data 要多大……再加兩個函式指標 `prep`（解析 SQE）與 `issue`（實際執行）。核心引擎完全不認識任何一個 op 的語意，它只會：`io_issue_defs[req->opcode].issue(req, flags)`。

opcode 的語意**讀表就知道**。想知道 `IORING_OP_READ` 會不會被 poll？看它的 `.pollin = 1`。這比讀 `io_read()` 的實作快十倍。

同一個檔還有第二張表 `io_cold_defs[]`（冷路徑：`.name`、`.cleanup`、`.fail`、`.sqe_copy`），把不常走的 callback 跟熱路徑的 `io_issue_defs[]` 分開，讓熱路徑的那張表更緊湊、更吃得進 cache。這種「hot/cold 分離」在 io_uring 裡到處都是（Ch 20 的 `io_ring_ctx` 本身也這樣排版）。

開機時 `io_uring_optable_init()` 會檢查兩張表大小一致、每個 op 都有 `prep`：

```c
/* io_uring/opdef.c */
void __init io_uring_optable_init(void)
{
	int i;

	BUILD_BUG_ON(ARRAY_SIZE(io_cold_defs) != IORING_OP_LAST);
	BUILD_BUG_ON(ARRAY_SIZE(io_issue_defs) != IORING_OP_LAST);

	for (i = 0; i < ARRAY_SIZE(io_issue_defs); i++) {
		BUG_ON(!io_issue_defs[i].prep);
		if (io_issue_defs[i].prep != io_eopnotsupp_prep)
			BUG_ON(!io_issue_defs[i].issue);
		WARN_ON_ONCE(!io_cold_defs[i].name);
	}
}
```

`IORING_OP_LAST` 在 6.18 是 63（opcode 0..62），和 Ch 0 用 `io_uring_get_probe()` 讀到的 `last_op = 62` 完全對得起來。那個 probe 底層問的就是這張表。

### register.c — io_uring_register 的子命令集散地（936 行）

`io_uring_register(2)` 是第三個 syscall，但它其實是**一百多個子命令的多工器**：註冊 fixed files、buffers、建立 buffer ring、設定 restrictions、註冊 eventfd、調 io-wq thread 上限、enable 一個 R_DISABLED 的 ring……全部從這裡分派。Part 2 你用過的 `io_uring_register_buffers()`、`io_uring_register_files()`，kernel 端入口都在這。它自己不實作資源管理（那在 `rsrc.c`），只做參數驗證與分派。

## 第 2 層：跨 opcode 的共用機制

| 檔案 | 行數 | 職責 | 深入章 |
|---|---|---|---|
| `rsrc.c` | 1580 | registered files/buffers 的資源節點（`io_rsrc_node`）、引用計數、RCU 回收、`io_mapped_ubuf` | Ch 29 |
| `kbuf.c` | 739 | provided buffers 與 buffer ring（`io_buffer_list`、`io_kbuf`）、buffer selection | Ch 14 回顧 / Ch 36 |
| `io-wq.c` | 1480 | 把會阻塞的請求丟給 kernel worker 執行緒池執行（bound/unbound worker） | Ch 26 |
| `poll.c` | 956 | armed poll、double poll、`IORING_OP_POLL_ADD`、async poll 的核心 | Ch 28 |
| `sqpoll.c` | 562 | SQPOLL 模式的 kernel 提交執行緒 `io_sq_thread()` | Ch 27 相關 |
| `cancel.c` | 386 | `IORING_OP_ASYNC_CANCEL` 與內部 cancel 比對（`io_cancel_req_match`） | Ch 30 |
| `filetable.c` | — | fixed file table 的 slot 配置與 bitmap | Ch 29 |
| `notif.c` | — | zero-copy send 完成時的 notification（`IORING_CQE_F_MORE`） | Ch 13 相關 |
| `eventfd.c` | — | 把 CQE 完成訊號橋接到 eventfd | Ch 18 回顧 |
| `napi.c` | — | 網路 busy poll（`CONFIG_NET_RX_BUSY_POLL`） | Ch 41 |
| `alloc_cache.c` | — | 輕量物件快取 `io_alloc_cache`，給 apoll/netmsg/rw/cmd 等重複配置的結構 | Ch 21 |
| `memmap.c` | — | ring 記憶體的 mmap 與 region 管理（`io_mapped_region`） | Ch 22 |
| `tctx.c` | — | per-task 的 `io_uring_task`，把 task 和它用過的 ring 綁起來 | Ch 22/30 |

這些檔的共同特徵：它們不是「一個 op」，是「很多 op 都會用到的基礎設施」。例如 `io-wq.c` 不知道也不關心它跑的是 read 還是 accept，它只看到一個 `io_wq_work`。

## 第 3 層：opcode 實作（葉子）

每個檔對應一類系統呼叫語意，內容都是同一個模式：`xxx_prep()`（把 SQE 的欄位解析進 request 的私有結構）+ `xxx()`（實際執行）。

| 檔案 | 涵蓋的 opcode |
|---|---|
| `rw.c`（1405） | READ / WRITE / READV / WRITEV / READ_FIXED / WRITE_FIXED / READ_MULTISHOT / READV_FIXED / WRITE_FIXED |
| `net.c`（1900） | SEND / RECV / SENDMSG / RECVMSG / ACCEPT / CONNECT / SOCKET / SHUTDOWN / BIND / LISTEN / SEND_ZC / RECV_ZC |
| `timeout.c`（704） | TIMEOUT / LINK_TIMEOUT / TIMEOUT_REMOVE |
| `poll.c`（956） | POLL_ADD / POLL_REMOVE（外加 async poll 基礎設施） |
| `openclose.c` | OPENAT / OPENAT2 / CLOSE / FIXED_FD_INSTALL |
| `statx.c` / `fs.c` | STATX；RENAMEAT / UNLINKAT / MKDIRAT / SYMLINKAT / LINKAT |
| `sync.c` | FSYNC / SYNC_FILE_RANGE / FALLOCATE |
| `splice.c` | SPLICE / TEE |
| `xattr.c` | FSETXATTR / SETXATTR / FGETXATTR / GETXATTR |
| `futex.c` | FUTEX_WAIT / FUTEX_WAKE / FUTEX_WAITV |
| `waitid.c` | WAITID |
| `msg_ring.c`（337） | MSG_RING（跨 ring 傳訊，`__io_msg_ring_data`） |
| `uring_cmd.c` | URING_CMD（透傳給驅動的通用命令，NVMe passthrough 等） |
| `epoll.c` | EPOLL_CTL / EPOLL_WAIT |
| `advise.c` / `truncate.c` | FADVISE / MADVISE；FTRUNCATE |
| `nop.c` | NOP（最小範本，練習 D 從它抄） |

`net.c` 和 `rw.c` 最大，因為網路和讀寫的 async/poll/buffer selection/multishot/zero-copy 組合最多。想學「一個 opcode 檔長什麼樣」，`nop.c` 是最乾淨的範本——只有幾十行。

## 標頭層：uapi 與內部 .h 的分工

地圖不只有 `.c`。io_uring 的標頭分兩種，混淆它們是新手讀碼的常見卡點：

- **`include/uapi/linux/io_uring.h`** — **ABI 契約**。所有 user 看得到的東西都在這：`IORING_OP_*` opcode 列舉、`IORING_SETUP_*`/`IOSQE_*`/`IORING_ENTER_*` flag、`struct io_uring_sqe`/`io_uring_cqe`/`io_uring_params` 的佈局。**這個檔的任何改動都是 ABI 變更**，只能加不能改既有欄位語意——向後相容的底線。你查「6.18 總共幾個 opcode」「某個 setup flag 的 bit 值」，來這裡，不是去 `.c`。
- **`io_uring/*.h`（內部標頭）** — **kernel 內部介面**。`io_uring.h`（核心 inline helper 與 `IOU_*` 回傳碼）、`opdef.h`（`io_issue_def` 結構）、`refs.h`（引用計數 helper）、各子系統的 `rw.h`/`poll.h`/`cancel.h`（跨檔共用的宣告）。這些隨便改，不是 ABI。

還有一個容易忽略的 `io_uring/slist.h`——io_uring 自己的單向鏈結串列（`io_wq_work_list`/`io_wq_work_node`）實作。提交 batch 的 free_list、completion batch、iopoll_list 全用它。它比 kernel 通用的 `list_head` 輕（單向、無哨兵），因為這些熱路徑 list 要做的就是「頭插、依序走」兩件事。讀 `io_submit_state` 或 io-wq 的 list 操作卡住時，答案常在 `slist.h` 的那幾個 `wq_stack_*`/`wq_list_*` 巨集。

一個實用規則：**查 user 能設定/看到的東西 → uapi header；查 kernel 內部怎麼實作 → 內部 `.h` 或 `.c`。** 兩者的 `io_uring.h` 同名但完全不同檔（一個在 `include/uapi/linux/`、一個在 `io_uring/`），grep 時記得指明路徑。

## 第 0 層：輔助與觀測

- **`fdinfo.c`（237）**：實作 `/proc/<pid>/fdinfo/<ring_fd>`。`cat` 一個 ring fd 的 fdinfo，就能看到它的 SQ/CQ head/tail、registered files/buffers、SQPOLL 狀態——debug io_uring 程式時極好用，底層就是 `__io_uring_show_fdinfo()`。
- **`query.c`**：6.18 的新東西，讓 user 查詢 io_uring 的能力（opcode 支援度等）的結構化介面。
- **`io_uring_types.h`**（型別層，740 行）：所有核心 struct 的家，Ch 20/21 整章在讀它。

## 底層機制：一個 read 請求穿過哪些檔

地圖最好的驗證，是拿一個請求讓它走一遍，看它踩過幾個檔。追一個最普通的 `IORING_OP_READ`（讀一個 socket，資料還沒到）：

1. **`io_uring.c`** — `io_uring_enter` 進來，`io_submit_sqes` 從 SQ 取出 SQE，`io_init_req` 解析成 `io_kiocb`。
2. **`opdef.c`** — `io_init_req` 查 `io_issue_defs[IORING_OP_READ]`，呼叫它的 `.prep = io_prep_read`。
3. **`rw.c`** — `io_prep_read` 把 SQE 的 buf/len/off 填進 `struct io_rw`。
4. **`io_uring.c`** — `io_issue_sqe` 查表呼叫 `.issue = io_read`。
5. **`rw.c`** — `io_read` 帶 `NONBLOCK` 試讀，socket 沒資料 → 回 `-EAGAIN`。
6. **`io_uring.c`** — `io_queue_async` 決定補救；socket 可 poll → `io_arm_poll_handler`。
7. **`poll.c`** — 在 socket 的 wait queue 掛 callback，請求掛起。
8. **（資料到達，中斷 context）`poll.c`** — callback 觸發，排一個 task_work。
9. **`io_uring.c`** — task_work 跑 `io_poll_issue` → `io_read` 再試一次，這次讀到資料 → `IOU_COMPLETE`。
10. **`io_uring.c`** — `io_req_complete_*` 把結果填成 CQE 貼進 CQ 環；請求回收。

一個「讀一個 socket」就橫跨了 `io_uring.c`（引擎）、`opdef.c`（dispatch）、`rw.c`（opcode）、`poll.c`（async 機制）四個檔，外加中斷 context 和 task_work context 兩次切換。這正是本章開頭說的「io_uring 把半個 kernel 串在一條路徑上」——而這張地圖的用途，就是讓你在讀到第 6 步的 `io_arm_poll_handler` 時，立刻知道「該翻 `poll.c` 了」。Ch 23–28 會把這十步逐一放大。

## 怎麼自己 grep / trace

地圖的價值在於「能自己找」。幾個實戰技巧：

**1. 從 trace point 反查路徑。** io_uring 的每個關鍵步驟都有 trace point，名字就是最好的路標：

```bash
cd ~/io_uring_course/ref/linux
grep -rn "trace_io_uring_" io_uring/ | grep -oP "trace_io_uring_\w+" | sort -u
```

你會看到 `trace_io_uring_submit_req`、`trace_io_uring_complete`、`trace_io_uring_queue_async_work`、`trace_io_uring_link`……每一個都標出一個你在 Ch 23–26 會讀到的轉折點。實機上 `trace-cmd record -e io_uring` 還能直接看請求流。

**2. 從 opcode 反查實作。** 想知道 `IORING_OP_ACCEPT` 怎麼做的？先查表拿 `issue` 函式名，再去 grep：

```bash
grep -n "IORING_OP_ACCEPT" io_uring/opdef.c      # → .issue = io_accept
grep -n "^int io_accept" io_uring/net.c          # → 實作在這
```

**3. 從型別反查使用者。** 想知道誰會動 `ctx->cq_overflow_list`？

```bash
grep -rn "cq_overflow_list" io_uring/
```

**4. 善用 header 的 `.h`/`.c` 配對。** 幾乎每個 `.c` 都有同名 `.h`，裡面是跨檔共用的宣告與 inline helper。`rw.h`、`poll.h`、`cancel.h` 常常比 `.c` 更快讓你看懂「這個子系統對外提供什麼」。

**5. git blame / log 查「為什麼」。** 這份是 sparse checkout，但仍是完整 git 歷史：

```bash
git log --oneline -- io_uring/poll.c | head
git log -S "io_arm_poll_handler" -- io_uring/
```

查一個函式何時加入、哪個 commit 改了它——Part 4 拆 CVE 時這是主力工具。

## 對比與取捨：模組化 dispatch vs 單一大 switch

io_uring 大可以寫成一個巨大的 `switch (req->opcode)`，像很多 syscall 多工器那樣。它選了函式指標表，代價與收益都值得想清楚：

| 面向 | `io_issue_defs[]` 函式指標表 | 單一 `switch(opcode)` |
|---|---|---|
| 新增 opcode | 新檔 + 表加一列，不動核心 | 改核心的 switch，衝突多 |
| 熱路徑成本 | 一次陣列索引 + 間接呼叫 | 跳轉表（編譯器通常也生成跳轉表） |
| op 性質查詢 | 讀 bitfield（`def->pollin` 等），O(1) | 要嘛另一張表、要嘛散在 case 裡 |
| 安全 | index 須 `array_index_nospec` 消毒 | 同樣要防越界 |
| 可讀性 | op 邏輯集中在各自檔案 | 全擠在一個巨函式 |

關鍵收益是**解耦**：opcode 作者和核心引擎作者互不干擾，這對一個「幾乎每個 release 都加 op」的子系統是決定性的。代價是間接呼叫（現代 CPU 的分支預測大多能吸收）與「索引必須消毒」的紀律（`io_init_req` 的 `array_index_nospec`，Ch 23）。io_uring 選了前者，而且把 hot（`io_issue_defs`）/cold（`io_cold_defs`）兩張表拆開，讓熱路徑那張更密、更吃得進 cache——這是「函式指標表」方案才做得乾淨的最佳化。

## 進階：怎麼對一顆跑著的 kernel 觀測這張地圖

原始碼是靜態的，但 io_uring 的路徑是動態的。幾個把地圖「跑起來」的進階手法：

**用 trace-cmd 看真實請求流。** io_uring 的 trace point 名稱就是本章各章節的路標，實機上可以直接錄：

```bash
sudo trace-cmd record -e io_uring ./your_io_uring_program
sudo trace-cmd report | less
```

你會看到 `io_uring_submit_req`（Ch 23）、`io_uring_issue`、`io_uring_complete`（Ch 25）、`io_uring_queue_async_work`（Ch 24 退 io-wq）、`io_uring_link`（Ch 23 link 組裝）依時間序排列——一個請求走了 inline 還是退了 io-wq，一眼看出。這比讀靜態 code 更快建立「哪條路常走」的直覺。

**用 pahole 看結構真實佈局。** Ch 20 會談 `io_ring_ctx` 的 cacheline 排版，`pahole` 能印出每個欄位的實際 offset 與 cacheline 邊界（需要帶 debug info 的 vmlinux）：

```bash
pahole -C io_ring_ctx vmlinux
pahole -C io_kiocb vmlinux
```

**用 git 追一個機制的演進。** 這份是 sparse checkout 但保有完整歷史。想知道某檔為什麼長這樣、某個 CVE patch 動了哪裡：

```bash
git log --oneline -- io_uring/poll.c | head -20
git show <commit>            # 看某個 patch 的 diff
git log -S "SLAB_TYPESAFE_BY_RCU" -- io_uring/   # 某行何時引入
```

Part 4 拆 CVE 時，`git log` 配合 `git show` 是定位 root cause 與 fix 的主力。**版本差異要誠實面對**：io_uring 改版極快，6.18 的某些檔（`query.c`、`mock_file.c`、`zcrx.c`）在 6.1 LTS 根本不存在，某些函式換過名字或拆過檔。讀別人的 writeup（常基於舊版）對照本課 6.18 原始碼時，先 `git log` 確認那個函式還在、還叫這個名。

## 踩雷集錦

1. **以為 `switch(opcode)` 在某處**。沒有。所有 opcode dispatch 都走 `io_issue_defs[]` 函式指標表（`opdef.c`）。你 grep `case IORING_OP_READ` 一個都找不到，要 grep `[IORING_OP_READ] =`。
2. **把 `io_uring_register` 的實作全找去 `io_uring.c`**。register 的子命令分派在 `register.c`，真正的資源管理在 `rsrc.c`/`kbuf.c`/`filetable.c`。三個檔要分清。
3. **以為 `ls io_uring/*.c` 就是全部編進 kernel 的**。`net.c`、`futex.c`、`epoll.c`、`napi.c`、`io-wq.c`、`fdinfo.c`、`mock_file.c` 都是 config 條件編譯。以 `Makefile` 的 `obj-$(CONFIG_…)` 為準。
4. **分不清 `kbuf.c` 和 `rsrc.c`**。`kbuf.c` 是 **provided** buffers（user 事先塞一堆 buffer 進 ring，kernel 收資料時自己挑一個，`IOSQE_BUFFER_SELECT`）；`rsrc.c` 是 **registered** files/buffers（`io_uring_register`，fixed buffer/file，跳過每次 fd/addr 查找）。兩套 buffer 機制，常被混為一談，Ch 14/15/29/36 會各自細談。
5. **以為 `io-wq.c` 是「io_uring 的全部非同步」**。不是。io_uring 的非同步有三條路：inline 非阻塞嘗試、poll（`poll.c`，可 poll 的 fd 走這）、io-wq（`io-wq.c`，真的會阻塞才退回這條）。io-wq 是**最後手段**，不是預設。Ch 24/26/28 會把這三條路講清楚。

## 本章重點整理

- io_uring 原始碼分三層：核心引擎（`io_uring.c` + `opdef.c` + `register.c`）、跨 op 共用機制（`rsrc.c`/`kbuf.c`/`io-wq.c`/`poll.c`/`sqpoll.c`/`cancel.c`…）、opcode 葉子（`rw.c`/`net.c`/`timeout.c`…各一檔）。
- opcode 不用 `switch`，用 `opdef.c` 的 `io_issue_defs[]` 函式指標表 dispatch；每列用 bitfield 描述 op 性質，加 `prep`/`issue` 兩個 callback。核心引擎不認識任何 op 語意。
- 權威的檔案清單在 `io_uring/Makefile` 的 `obj-$(CONFIG_…)`；很多檔是 config 條件編譯。
- 自己找路：trace point 名稱、`[IORING_OP_X] =` 查表、grep 型別、`.h`/`.c` 配對、`git log -S`。
- 核心型別（`io_ring_ctx`、`io_kiocb`、`io_rings`）全在 `include/linux/io_uring_types.h`——下一章起的主角。

## 自我檢核

- [ ] 不看地圖，我能說出 read、accept、timeout、poll 各自的實作檔。
- [ ] 我知道為什麼 io_uring 沒有 `switch(opcode)`，以及 dispatch 真正怎麼做。
- [ ] 我能解釋 `kbuf.c`（provided）和 `rsrc.c`（registered）的差別。
- [ ] 給我一個 opcode 名，我能用兩次 grep 找到它的 `issue` 實作。
- [ ] 我知道「某台機器支援哪些 op」除了 kernel 版本，還取決於什麼（config）。

## 延伸閱讀

- **Linux 6.18 原始碼 `io_uring/Makefile` 與 `opdef.c`** — `~/io_uring_course/ref/linux/io_uring/`
  - **讀哪裡**：`Makefile` 的 `obj-$(CONFIG_…)` 清單、`opdef.c` 的 `io_issue_defs[]` 前 20 列
  - **和本章關聯**：這兩個檔就是本章地圖的權威出處
- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** 與 LWN io_uring 系列 — Jonathan Corbet
  - **讀哪裡**：各篇對新機制的介紹，對照它落在哪個檔，地圖立刻立體
  - **為什麼值得讀**：想知道「某個檔為什麼存在、何時加入」，LWN 的時序報導最準
- **[Lord of the io_uring](https://unixism.net/loti/)** — Shuveb Hussain
  - **讀哪裡**：各 op 的章節，對照本章第 3 層的 opcode→檔案表
- **本課 [Ch 0 環境搭建 Step 6](./00-environment-setup.md)**
  - **讀哪裡**：sparse checkout 的設定；本章所有路徑都基於它
- **kernel_internals [Ch 5 核心資料結構](../kernel_internals/05-core-data-structures.md)**
  - **讀哪裡**：`list_head`、`hlist`、`llist`、xarray——io_uring 的 ctx 裡全是這些，先熟悉能省很多力

地圖在手，我們從型別層的中樞開始往下挖：下一章把 `struct io_ring_ctx` 這個「一個 io_uring 實例的全部狀態」逐欄位拆開。

→ [Ch 20 io_ring_ctx 全解剖](./20-io-ring-ctx.md)
