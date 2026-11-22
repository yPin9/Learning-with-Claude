# io_uring 與 Linux async I/O 全解：從 ring 到 kernel 原始碼到攻擊面

> 給已經懂 Linux 系統程式、讀過一點 kernel、想把 io_uring 從「會用」學到「讀得懂實作、看得懂 CVE」的工程師。

這是一本厚書。它分三面均衡地打穿 io_uring：**應用**（liburing、multishot、zero-copy、怎麼寫得快又對）、**kernel 內部實作**（對照 Linux 6.18 的 `io_uring/` 原始碼，一行一行看請求怎麼被提交、派發、完成）、以及**安全**（為什麼 io_uring 是近年 kernel 漏洞的大宗、經典 CVE 怎麼拆、利用原語怎麼建、防禦怎麼做）。學完你能獨立讀 io_uring 的 kernel 原始碼、看懂一個 io_uring CVE 的 root cause 與 patch，並從頭重現它。

本課接續 repo 既有的 [kernel_internals](../kernel_internals/README.md) 與 [kernel_pwn](../../security/kernel_pwn/README.md)：阻塞 I/O、epoll、記憶體屏障、slab、UAF 這些底層概念會快速帶過並給路標，重心放在 io_uring 本身。

## 為什麼學這個？

- **它正在吃掉 Linux I/O**：資料庫（PostgreSQL 18、ScyllaDB）、儲存（Ceph、SPDK 的對手）、runtime（tokio-uring、Rust）、proxy，都在往 io_uring 搬。它不是 epoll 的小改良，是整個 syscall I/O 模型的重新設計。
- **它是讀 kernel 的最佳切片**：一個請求從 user ring 進到 kernel，走過記憶體共享、work queue、task_work、poll、RCU 資源回收——io_uring 把半個 kernel 的機制串在一條路徑上。讀懂它，等於把 kernel_internals 學過的東西全部接起來。
- **它是 kernel 攻擊面的當紅炸子雞**：生命週期極度複雜、非同步執行、大量引用計數——UAF 與 race 的溫床。Google 的 kernelCTF 一度把它整個關掉，ChromeOS/Android 預設停用它。懂它的攻擊面，是 kernel 安全研究與面試的硬通貨。

## 先修知識

- **C 與 Linux 系統程式**（程度：能看懂 `open/read/mmap/epoll`、知道 fd 是什麼）
- **kernel 基礎概念**（程度：知道 syscall 怎麼進 kernel、slab/slub 是什麼、spinlock/RCU 聽過）——不熟的地方本課會給 [kernel_internals](../kernel_internals/README.md) 的路標
- **想讀 Part 4 安全面**：建議先有 [kernel_pwn](../../security/kernel_pwn/README.md) 的 UAF / heap spray 基礎；沒有也能讀，本課會補
- 沒有也沒關係的：io_uring 任何經驗、asm、exploit 開發經驗

## 課程地圖

### Part 0 — 入門與環境（Ch 0–2）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼需要 io_uring：async I/O 演進史](./01-why-io-uring.md)
- [Ch 2 三十分鐘全貌：SQ/CQ ring 與 raw hello world](./02-thirty-minute-tour.md)

### Part 1 — 核心模型（Ch 3–9）
- [Ch 3 三個 syscall：setup / enter / register](./03-three-syscalls.md)
- [Ch 4 共享記憶體環：SQ/CQ ring、SQE/CQE 佈局與 mmap](./04-shared-ring-memory.md)
- [Ch 5 提交與完成的 memory ordering](./05-submit-complete-ordering.md)
- [Ch 6 liburing 入門](./06-liburing-intro.md)
- [Ch 7 SQE 詳解](./07-sqe-anatomy.md)
- [Ch 8 CQE 詳解與 CQ overflow](./08-cqe-anatomy.md)
- [Ch 9 提交模型：SQPOLL、batching、GETEVENTS](./09-submission-models.md)
- [練習 A：手刻 raw io_uring cat](./practice-a-raw-cat.md)

### Part 2 — 操作全集（Ch 10–18）
- [Ch 10 檔案 I/O：read/write/readv、O_DIRECT](./10-file-io.md)
- [Ch 11 metadata ops：fsync/statx/openat/close](./11-metadata-ops.md)
- [Ch 12 網路 I/O：accept/connect/send/recv](./12-network-io.md)
- [Ch 13 multishot 與 sendmsg/recvmsg](./13-multishot.md)
- [Ch 14 Provided buffers 與 buffer ring](./14-provided-buffers.md)
- [Ch 15 Registered files & buffers](./15-registered-files-buffers.md)
- [Ch 16 Linked operations 與 drain](./16-linked-operations.md)
- [Ch 17 Timeouts 與 cancel](./17-timeouts-cancel.md)
- [Ch 18 跨機制整合：poll、eventfd、futex、msg_ring](./18-cross-mechanism.md)
- [練習 B：multishot echo server](./practice-b-echo-server.md)
- [練習 C：buffer ring + zero-copy 改寫](./practice-c-buffer-ring.md)

### Part 3 — kernel 內部實作（Ch 19–30，對照 Linux 6.18）
- [Ch 19 原始碼地圖：io_uring/ 目錄結構](./19-source-map.md)
- [Ch 20 io_ring_ctx 全解剖](./20-io-ring-ctx.md)
- [Ch 21 io_kiocb：request 物件的生命週期](./21-io-kiocb-lifecycle.md)
- [Ch 22 io_uring_setup 內部](./22-setup-internals.md)
- [Ch 23 提交路徑：io_submit_sqes 與 opcode dispatch](./23-submission-path.md)
- [Ch 24 io_issue_sqe 與 IO_URING_F_NONBLOCK](./24-issue-sqe.md)
- [Ch 25 完成路徑：io_req_complete 與 CQE 填寫](./25-completion-path.md)
- [Ch 26 async 的祕密：io-wq worker pool](./26-io-wq.md)
- [Ch 27 task_work、DEFER_TASKRUN、SINGLE_ISSUER](./27-task-work-defer.md)
- [Ch 28 poll 機制內部：armed poll、double poll](./28-poll-internals.md)
- [Ch 29 資源管理內部：io_rsrc_node、RCU 回收](./29-rsrc-internals.md)
- [Ch 30 cancel 與 teardown：引用計數、ring 拆除](./30-cancel-teardown.md)
- [練習 D：給 kernel 加一個自訂 io_uring opcode](./practice-d-custom-opcode.md)

### Part 4 — 安全面（Ch 31–40）
- [Ch 31 為什麼 io_uring 是漏洞溫床：攻擊面分析](./31-attack-surface.md)
- [Ch 32 UAF 模式總論：引用計數陷阱](./32-uaf-patterns.md)
- [Ch 33 競態類漏洞：io-wq / task_work race](./33-race-patterns.md)
- [Ch 34 CVE 拆解一：fixed buffer type confusion → UAF](./34-cve-case-one.md)
- [Ch 35 CVE 拆解二：timeout/poll race UAF](./35-cve-case-two.md)
- [Ch 36 CVE 拆解三：provided buffer / buffer ring OOB](./36-cve-case-three.md)
- [Ch 37 CVE 拆解四：較新的 rsrc / msg_ring 類](./37-cve-case-four.md)
- [Ch 38 io_uring 的 kernel 記憶體配置與加固](./38-exploit-primitives.md)
- [Ch 39 緩解與偵測：io_uring_disabled、seccomp、LSM](./39-mitigations-detection.md)
- [Ch 40 防禦者視角：審計與 syzkaller fuzzing](./40-defender-view.md)
- [練習 E：最小 UAF PoC（受控環境）](./practice-e-uaf-poc.md)

### Part 5 — 進階與整合（Ch 41–44）
- [Ch 41 效能工程：polling、NAPI busy poll、benchmark](./41-performance-engineering.md)
- [Ch 42 生態系：tokio-uring、PostgreSQL/Ceph、語言綁定](./42-ecosystem.md)
- [Ch 43 橫向對比：io_uring vs epoll vs libaio vs SPDK](./43-comparison.md)
- [Ch 44 未來：zero-copy rx、ublk、Rust for Linux](./44-future.md)
- [Final Project：io_uring CVE 重現 + 分析](./final-project-cve-reproduction.md)

## 學習方式建議

1. **每章親手敲過並跑**：本課所有 user-space 範例都在 WSL2 kernel 6.18 + liburing 2.16 實測過，你也該跑。Ch 0 給你一鍵編譯環境。
2. **故意把它弄壞**：把 `io_uring_submit` 刪掉、把 buffer 提早 free、把 fd 關掉再提交——看 CQE 的 `res` 怎麼變成 `-EBADF`、`-EFAULT`。io_uring 的錯誤是從 CQE 回來的，不是 syscall 當場回來的，這個直覺要親手建立。
3. **一手開教材、一手開原始碼**：Part 3 之後，把 `io_uring/*.c`（6.18）開在旁邊。本課每個函式名都可以在原始碼裡 grep 到。
4. **安全章節只在自有環境操作**：Part 4 的 PoC 一律在你自己的 VM / 受控 kernel 上跑，目的是理解機制與防禦，不針對任何真實系統。

## 環境一覽（全書基準）

| 項目 | 版本 |
|---|---|
| Kernel | Linux 6.18（WSL2 實測 6.18.58.1-microsoft-standard-WSL2） |
| liburing | 2.16（從 source 編，見 Ch 0） |
| 編譯器 | gcc 11.4 |
| 原始碼對照 | torvalds/linux tag `v6.18` 的 `io_uring/` |

> 版本差異會隨章標注。io_uring 演進極快（幾乎每個 kernel release 都加 opcode 與機制），本課釘在 6.18；6.1（LTS）以前的重大差異會特別點出。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe（io_uring 作者）
  - io_uring 的原始設計文件，ring 模型與 syscall 的第一手說明；本課 Part 1 的骨架
- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - 網路相關機制（multishot、provided buffer ring、zero-copy）最新、最權威的整理
- **Linux 6.18 原始碼 `io_uring/`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring>
  - 行為不符預期時的最終仲裁；本課 Part 3 逐檔對照它

### 推薦論文 / 技術報告

- **[Understanding and Detecting Harmful Memory-based Vulnerabilities in io_uring](https://arxiv.org)**（搜尋標題）
  - io_uring 記憶體漏洞的系統性分類，Part 4 的學術支撐
- **[Put an io_uring on it: Exploiting the Linux Kernel](https://chompie.rip/Blog+Posts/Put+an+io_uring+on+it+-+Exploiting+the+Linux+Kernel)** — chompie1337
  - 把一個 io_uring UAF 打到提權的完整 writeup，Part 4 利用原語的範本

### 推薦部落格 / 文章

- **[Lord of the io_uring](https://unixism.net/loti/)** — Shuveb Hussain
  - 目前網路上最系統的 io_uring 教學，從 raw syscall 到各種 op，本課 Part 1–2 的對照讀物
- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** 與 LWN 的 io_uring 系列 — Jonathan Corbet
  - LWN 對每個新機制的第一時間深度報導；查某功能何時加入、為什麼加，這裡最準

### 讀完本課之後

- **[kernelCTF](https://github.com/google/security-research/tree/master/kernelctf)**（Google security-research）
  - 真實 kernel exploitation 靶場，io_uring 曾是主角之一，拿本課 Part 4 的能力去打
- **[liburing examples/](https://github.com/axboe/liburing/tree/master/examples) 與 test/](https://github.com/axboe/liburing/tree/master/test)**
  - 作者親手寫的範例與回歸測試，是「production io_uring 長什麼樣」的最佳樣本

---

本課為個人自學教材，所有安全內容以理解機制與建立防禦能力為目的，實驗一律在自有或獲授權的受控環境進行。
