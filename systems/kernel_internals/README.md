# Linux Kernel Internals：從讀懂子系統設計到動手改核心

> 給懂 C、已經會攻擊/觀測 kernel（kernel_pwn、bpf、linux_boot），想正面理解 Linux kernel 每個子系統怎麼設計的工程師。

這門課不教「怎麼用 Linux」，教的是「Linux kernel 本身怎麼運作」。排程器怎麼決定下一個跑誰、一次 page fault 在底層發生什麼、RCU 為什麼能無鎖讀、一個封包從網卡到 socket 走過哪些層、容器的 namespace/cgroup 在 kernel 裡是什麼——每個子系統都**先跟真實的 6.12 源碼讀懂設計，再動手寫模組/改參數驗證**，全程 QEMU + gdb 跟著執行流程走。x86_64 是主線，`context switch`、page table、memory barrier 這些真正有架構差異的地方會點出 ARM64 的不同。

## 為什麼學這個？

- **這是你所有 kernel 課的地基**：你會攻擊 slub（kernel_pwn）、會用 eBPF 觀測（bpf）、會寫 Windows 驅動、懂開機流程（linux_boot）——但少了「kernel 子系統本身怎麼設計」這塊地基，你是站在半空中。這門課把它們黏起來
- **理解底層 = 能 debug 沒人能 debug 的問題**：「為什麼這個 process 卡在 D 狀態」「為什麼加了鎖還是 race」「為什麼記憶體明明夠卻 OOM」——這些只有讀過排程器、鎖、reclaim 源碼的人能推理
- **職涯角度**：韌體工程師每天碰 device tree / 電源管理 / 驅動；toolchain 工程師要懂 codegen 打進 kernel 後如何跑。kernel internals 是這兩條路的硬通貨，也是資深與資淺的分水嶺

## 先修知識

- **C 語言**（程度：熟指標、struct、function pointer、bit 操作；能讀懂複雜的 macro）
- **作業系統概念**（程度：知道 process/virtual memory/中斷是什麼；OSTEP 等級即可）
- **建議先修**（沒有也能上，但有更好）：本 repo 的 `linux_boot`（開機到 kernel 交棒）、`linux_commands`（VFS/inode/process 使用者視角）、`kernel_pwn`（heap/slub 攻擊視角）、`bpf`（觀測視角）
- 不需要：寫過 kernel、讀過 kernel 源碼、組合語言精通（關鍵處會逐行解釋）

## 課程地圖

### Part 0 — 地圖與環境（Ch 0–1）
- [Ch 0 環境搭建：build kernel、QEMU、gdb](./00-environment-setup.md)
- [Ch 1 Kernel 全貌：monolithic 設計與怎麼讀源碼](./01-kernel-overview.md)

### Part 1 — Kernel 基礎設施（Ch 2–8）
- [Ch 2 Kernel 的執行環境：context、stack、current](./02-execution-context.md)
- [Ch 3 Kernel 啟動流程：start_kernel 到 init](./03-kernel-boot-flow.md)
- [Ch 4 Syscall 機制與自訂 syscall](./04-syscall-mechanism.md)
- [Ch 5 Kernel 核心資料結構：list_head/rbtree/xarray](./05-core-data-structures.md)
- [Ch 6 記憶體配置 API：kmalloc/vmalloc/slab/GFP](./06-memory-allocation-api.md)
- [Ch 7 per-CPU 變數與 kernel 的並行本質](./07-per-cpu-and-concurrency.md)
- [Ch 8 模組載入底層：finit_module、符號解析、簽署、initcall](./08-module-loading.md)
- [練習 A：第一個核心模組 + 自訂 syscall](./practice-a-first-module-syscall.md)

### Part 2 — Process 與排程（Ch 9–15）
- [Ch 9 task_struct 解剖](./09-task-struct.md)
- [Ch 10 Process 建立：fork/clone/copy_process](./10-fork-clone-copy-process.md)
- [Ch 11 排程器框架：scheduler class 與 runqueue](./11-scheduler-framework.md)
- [Ch 12 CFS 深入：vruntime 與紅黑樹](./12-cfs.md)
- [Ch 13 EEVDF：為什麼 6.6 換掉 CFS](./13-eevdf.md)
- [Ch 14 Context switch 與 preemption（x86 vs ARM64）](./14-context-switch.md)
- [Ch 15 SMP、load balancing、CPU affinity、NUMA](./15-smp-numa-balancing.md)
- [練習 B：CFS/EEVDF 觀測模組](./practice-b-scheduler-observation.md)

### Part 3 — 記憶體管理（Ch 16–23）
- [Ch 16 虛擬位址空間與 page table walk](./16-virtual-memory-page-tables.md)
- [Ch 17 Physical memory：zone 與 buddy allocator](./17-buddy-allocator.md)
- [Ch 18 slab/slub allocator 內部](./18-slub-allocator.md)
- [Ch 19 mm_struct、VMA、page fault handler](./19-mm-struct-vma-fault.md)
- [Ch 20 demand paging、CoW、reverse mapping](./20-demand-paging-cow-rmap.md)
- [Ch 21 page cache 與 writeback](./21-page-cache-writeback.md)
- [Ch 22 reclaim：kswapd、swap、OOM killer](./22-reclaim-swap-oom.md)
- [Ch 23 TLB、memory barrier、cache coherence](./23-tlb-memory-barriers.md)
- [練習 C：page table walker 模組](./practice-c-page-table-walker.md)

### Part 4 — 同步與並行（Ch 24–28）
- [Ch 24 atomic 操作與 memory ordering](./24-atomics-memory-ordering.md)
- [Ch 25 spinlock、rwlock、qspinlock](./25-spinlocks.md)
- [Ch 26 mutex、semaphore、completion](./26-mutex-semaphore.md)
- [Ch 27 RCU 深入](./27-rcu.md)
- [Ch 28 seqlock、lockdep、死鎖模式](./28-seqlock-lockdep.md)
- [練習 D：race condition 復現與 RCU 修復](./practice-d-race-rcu.md)

### Part 5 — 中斷與時間（Ch 29–32）
- [Ch 29 中斷處理：IDT/GIC、top/bottom half](./29-interrupt-handling.md)
- [Ch 30 softirq、tasklet、workqueue](./30-softirq-workqueue.md)
- [Ch 31 threaded IRQ 與 -rt](./31-threaded-irq.md)
- [Ch 32 時間子系統：jiffies、hrtimer、tickless](./32-timers-hrtimer.md)

### Part 6 — VFS 與 Block Layer（Ch 33–36）
- [Ch 33 VFS 四大物件：superblock/inode/dentry/file](./33-vfs-objects.md)
- [Ch 34 一次 read() 的完整路徑](./34-read-path.md)
- [Ch 35 寫一個最小 in-memory filesystem](./35-minimal-filesystem.md)
- [Ch 36 Block layer：bio、request queue、blk-mq](./36-block-layer-blkmq.md)
- [練習 E：ramdisk block device / mini fs](./practice-e-mini-fs.md)

### Part 7 — 驅動、匯流排、電源（Ch 37–42）
- [Ch 37 Device model：kobject/sysfs/bus/driver](./37-device-model.md)
- [Ch 38 char/misc device 深入](./38-char-misc-device.md)
- [Ch 39 platform driver 與 device tree](./39-platform-driver-device-tree.md)
- [Ch 40 PCI/PCIe 列舉：config space、BAR、MMIO](./40-pci-pcie.md)
- [Ch 41 中斷驅動裝置、DMA、mmap](./41-interrupt-dma-mmap.md)
- [Ch 42 電源管理：cpuidle/cpufreq/runtime PM/suspend](./42-power-management.md)

### Part 8 — 網路堆疊（Ch 43–46）
- [Ch 43 sk_buff 與 net_device 抽象](./43-sk-buff-netdev.md)
- [Ch 44 收包路徑：NAPI、softirq NET_RX、GRO](./44-rx-path-napi.md)
- [Ch 45 socket layer 與送包路徑、qdisc](./45-socket-tx-path.md)
- [Ch 46 netfilter/nftables hook 與 XDP](./46-netfilter-xdp.md)

### Part 9 — 安全子系統（Ch 47–50）
- [Ch 47 credentials 與 capabilities](./47-credentials-capabilities.md)
- [Ch 48 LSM 框架與 SELinux/AppArmor hook](./48-lsm-selinux.md)
- [Ch 49 seccomp-BPF 與 namespace 實作](./49-seccomp-namespaces.md)
- [Ch 50 cgroup v2 實作](./50-cgroup-v2.md)

### Part 10 — 觀測、Debug、eBPF Host（Ch 51–53）
- [Ch 51 kprobes/tracepoints/uprobes 底層](./51-kprobes-tracepoints.md)
- [Ch 52 Kernel 如何 host eBPF：verifier、JIT、hooks](./52-ebpf-host.md)
- [Ch 53 Kernel debug：printk/ftrace/KASAN/kgdb/oops](./53-kernel-debugging.md)
- [練習 F：buggy 模組除錯（ftrace + KASAN + gdb）](./practice-f-debug-buggy-module.md)

### Final Project
- [Final Project：核心模組套件](./final-project-kernel-module-suite.md)

## 學習方式建議

1. **每章都在 QEMU 裡驗證**：這門課的核心手法是「源碼讀懂設計 → gdb 停在那個函式看它真的怎麼跑」。讀 `pick_next_task` 的源碼，就在 QEMU 裡 `b pick_next_task` 看它被誰呼叫、參數是什麼
2. **故意把 kernel 弄壞**：寫一個會 deadlock 的模組看 lockdep 罵你、故意 `kfree` 兩次看 KASAN 抓你、寫一個吃光記憶體的模組看 OOM killer 動作——kernel 的錯誤訊息本身就是最好的教材
3. **對照使用者空間視角**：你在 `linux_commands` 學過 `ps`/`/proc`、在 `kernel_pwn` 打過 slub、在 `bpf` 觀測過 tracepoint——每章都回頭問「我以前在使用者空間看到的現象，在 kernel 裡對應到哪段源碼」

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[Linux 6.12 原始碼](https://elixir.bootlin.com/linux/v6.12/source)** — Bootlin 的線上交叉索引
  - 本課的第一手材料；每章都會給出具體檔案路徑與函式名，配 Bootlin 可以直接點進去看定義與所有呼叫點。**釘死 v6.12**，因為 kernel 演進快，跨版本行號和行為會變
- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly, 2005）
  - 雖然講的是 2.6，但排程器/記憶體/VFS 的**架構骨架**至今適用；當作理解大方向的地圖，細節以 6.12 源碼為準

### 推薦書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - 最好讀的 kernel 入門書；process/排程/中斷/同步/記憶體各章是本課前半的最佳白話補充
- **《Professional Linux Kernel Architecture》** — Wolfgang Mauerer（Wrox, 2008）
  - 比 Love 更深、更貼源碼；當作 Love 讀完後的加深版

### 推薦文章 / 網站

- **[LWN.net Kernel index](https://lwn.net/Kernel/Index/)** — Jonathan Corbet 等
  - kernel 開發的權威記錄；每個新機制（EEVDF、folio、maple tree、io_uring）進主線時 LWN 的文章是最好的一手解說。本課多個「為什麼這樣設計」直接引用 LWN
- **[Documentation/ 目錄](https://www.kernel.org/doc/html/latest/)** — kernel 官方文件
  - RCU、memory-barriers、locking、scheduler 各有專門文件（如 `Documentation/RCU/`、`memory-barriers.txt`），是設計者親自寫的權威說明

### 讀完本課之後

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（把 mm 子系統推到極致）
- **[The Linux Kernel Module Programming Guide](https://sysprog21.github.io/lkmpg/)**（動手寫模組的持續更新版指南，配合本課的實作章）
- 直接**訂 LKML 或看某個子系統的 patch**：讀懂子系統後，去看它現在正在改什麼，是進入 kernel 社群的起點
