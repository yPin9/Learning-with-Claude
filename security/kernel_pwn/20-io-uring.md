# Ch 20 — io_uring：SQE/CQE 與 async ring 的 UAF 模式

> 目標：io_uring 是 Linux 5.1+ 的 async I/O 新架構，用 ring buffer + kernel worker 做 syscall batch。它的 async request 生命週期很複雜，歷年出過一堆 UAF — 這章講 ring model 與常見 bug pattern。

## io_uring 架構速覽

io_uring 讓 user 和 kernel 共享兩個 ring buffer：

```
user space             kernel space
──────────────────     ────────────────────
SQ ring (提交佇列)  →  kernel worker 讀 SQE
  ├── SQE[0]           執行 I/O 操作
  ├── SQE[1]         ↓
  └── SQE[N]        CQ ring（完成佇列）← kernel 寫 CQE
                       ├── CQE[0]         user poll 讀結果
                       └── CQE[M]
```

**SQE**（Submission Queue Entry）：user 寫的 I/O 請求。
**CQE**（Completion Queue Entry）：kernel 寫的完成結果。

所有 SQE / CQE 的記憶體都是 `io_uring_setup()` 時就 mmap 好的共享記憶體，zero-copy。

### 初始化

```c
#include <liburing.h>
/* 或手動呼叫 io_uring_setup(2) */

struct io_uring ring;
io_uring_queue_init(256, &ring, 0);  /* 建 256-entry ring */
```

等效的 low-level syscall：

```c
struct io_uring_params p = {0};
int ring_fd = io_uring_setup(256, &p);
/* mmap SQ ring, CQ ring, SQE array */
void *sq_ptr = mmap(0, p.sq_off.array + p.sq_entries * sizeof(unsigned),
                    PROT_READ|PROT_WRITE, MAP_SHARED|MAP_POPULATE, ring_fd,
                    IORING_OFF_SQ_RING);
```

---

## 內部物件模型

io_uring 在 kernel 內部用以下關鍵 struct：

| Struct | 在哪個 cache | 用途 |
|---|---|---|
| `io_ring_ctx` | vmalloc | ring context，per io_uring 實例 |
| `io_kiocb` | `io_kiocb` cache | 每個 in-flight request |
| `io_uring_cmd` | kmalloc（size 依 cmd） | passthrough command |
| `io_fixed_file_table` | vmalloc | 固定 file table |
| `io_poll_iocb` | 嵌在 `io_kiocb` 裡 | poll 操作 |

**`io_kiocb`** 是最重要的：每個提交的 SQE 在 kernel 裡變成一個 `io_kiocb`，完成後才 free。它的生命週期從 `io_uring_enter()` 時 alloc，到 I/O 完成、CQE 寫入後 free。

---

## 常見 UAF 模式

### 模式 A：async 取消 race（CVE-2022-29582）

**原理**：io_uring 支援 `IORING_OP_ASYNC_CANCEL` 取消一個 in-flight request。同時，worker thread 可能正在處理同一個 request。race condition：

```
thread A: io_kiocb refcount = 1
           cancel 路徑：dec refcount → 0 → free io_kiocb

thread B: 同時，worker 在讀 io_kiocb 的欄位
           → use-after-free
```

**Object**：`io_kiocb`，在 `io_kiocb` kmem_cache（size ~208 bytes → kmalloc-256）。

**利用路徑**：
```
UAF on io_kiocb（kmalloc-256）
→ 用 msg_msg spray（192 bytes payload = kmalloc-256 object）
→ 覆蓋 io_kiocb 的 ops pointer
→ trigger complete callback → RIP 控制（無 KCFI 環境）
```

### 模式 B：fixed file table race（CVE-2021-41073 type confusion）

**原理**：`IORING_REGISTER_FILES` 讓 user 把 fd 放入 io_uring 的固定 file table，以後用 index 代替 fd，節省 fd 查找開銷。

bug：在 unregister 和使用同時發生時，file table 指向的 file struct 已被 close → use-after-free。

**Type confusion**：某些版本的 bug 讓你把 socket 伪裝成 regular file，讓 kernel 用錯誤 ops 處理它（type confusion，不完全是 UAF）。

### 模式 C：splice/tee 的 page UAF（CVE-2023-2598）

**原理**：`IORING_OP_SPLICE` 在 async 路徑下，pipe buffer 的 page refcount 管理有誤。pipe buffer 的 page 被提前 free，後續 splice 操作讀/寫 already-freed page。

**Object**：`pipe_buffer` 的底層 `struct page`（直接 buddy allocator，不在 slab）。

**利用路徑**：利用 free'd page 的重用 — buddy 把它給 page table allocator → Dirty Pagetable（Ch 14）。

### 模式 D：io_uring 固定 buffer 的 UAF

`IORING_REGISTER_BUFFERS` 讓 kernel pin user 的 buffer pages（用 `get_user_pages`），把 page 加入 io_uring 的 fixed buffer table。

Bug：在 buffer unregister（解 pin）和 I/O 使用之間有 race → kernel 讀/寫 already-freed page。

---

## 操作 io_uring 的攻擊框架

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <sys/syscall.h>
#include <linux/io_uring.h>

/* 直接呼叫 io_uring_setup，不依賴 liburing */
static int io_uring_setup_raw(unsigned entries, struct io_uring_params *p) {
    return syscall(__NR_io_uring_setup, entries, p);
}
static int io_uring_enter_raw(int fd, unsigned to_submit,
                               unsigned min_complete, unsigned flags) {
    return syscall(__NR_io_uring_enter, fd, to_submit, min_complete, flags, NULL, 0);
}
static int io_uring_register_raw(int fd, unsigned opcode,
                                  void *arg, unsigned nr_args) {
    return syscall(__NR_io_uring_register, fd, opcode, arg, nr_args);
}

/* 提交一個 NOP SQE（觸發 kernel 分配 io_kiocb） */
static void submit_nop(int ring_fd, struct io_uring_params *p,
                        unsigned *sq_tail, uint8_t *sqes) {
    unsigned idx = *sq_tail & (p->sq_entries - 1);
    struct io_uring_sqe *sqe = (void *)(sqes + idx * sizeof(*sqe));
    memset(sqe, 0, sizeof(*sqe));
    sqe->opcode = IORING_OP_NOP;
    (*sq_tail)++;
    /* 寫回 tail ... */
    io_uring_enter_raw(ring_fd, 1, 0, 0);
}
```

實際利用 CVE-2022-29582 的 race 需要兩個 thread 精確地交錯：一個 thread 送 cancel，另一個 thread 在 kernel 側持有 io_kiocb。用 `pthread_barrier_t` 同步，配合 `sched_setaffinity` 把兩個 thread 固定在不同 CPU 製造 true parallel race。

---

## io_uring 在 kernelCTF 的地位

io_uring 2019-2023 間是第二大 kernelCTF 礦區（第一是 nf_tables）。Google 的 Project Zero 在 2022-2023 年持續發現 io_uring 的 privilege escalation bug，部分已公開 writeup：

- Jann Horn 的 io_uring bug 系列（[projectzero.blogspot.com](https://googleprojectzero.blogspot.com/)）
- `CVE-2022-29582`：io_poll UAF，Kylebot 的 writeup 是教科書

2024 年後 kernel 加了 io_uring 相關的 BPF security hook 和更嚴格的 refcount，bug 密度有所下降，但 subsystem 複雜度沒降，仍是有效攻擊面。

---

## 動手練習

1. **讀 `include/linux/io_uring_types.h`**：找 `struct io_kiocb` 的 layout，確認 `flags`、`ops`、`refs` 的 offset。和 kmalloc size 推算（`sizeof(io_kiocb)` 落在哪個 kmalloc-N）。
2. **用 strace 觀察 io_uring_setup**：`strace -e io_uring_setup,io_uring_enter,io_uring_register ./your_program`，確認 syscall 的參數和 return value。
3. **寫一個 NOP 批次**：送 64 個 `IORING_OP_NOP` SQE，poll CQE，確認 64 個 CQE 都回來了（用 liburing 或手工 syscall）。
4. **測試 fixed file registration**：`IORING_REGISTER_FILES` 注冊 10 個 fd，然後用 `IORING_OP_READ` 直接 reference index（`sqe->flags |= IOSQE_FIXED_FILE`），確認不用 fd 直接 I/O。
5. **讀 CVE-2022-29582 PoC 的 race 部分**：找開源 PoC（GitHub），識別它在哪裡設置 barrier、用幾個 thread、race window 大概多大。

## 自我檢核

- [ ] 能描述 SQE → io_kiocb → CQE 的完整生命週期
- [ ] 知道 `io_kiocb` 落在哪個 kmalloc cache（size ~208 → kmalloc-256）
- [ ] 能說出三個 io_uring UAF 模式（cancel race、fixed file race、splice page race）
- [ ] 知道 `IORING_REGISTER_FILES` 的用途和攻擊面
- [ ] 知道 CVE-2022-29582 的根因（cancel 和 worker 的 refcount race）
- [ ] 能解釋為什麼 io_uring 的 bug 難修（async 生命週期 + race condition）

→ [Ch 21 — eBPF：verifier bypass 與 map 型漏洞](./21-ebpf.md)
