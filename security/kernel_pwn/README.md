# Linux Kernel Exploitation 參考書：從入門到 kernelCTF 與真實 1-day

> 給會 C、懂基本 x86-64、知道 stack overflow / ROP 是什麼，想把 Linux kernel exploitation 從零系統性打穿的人。假設你**不需要**精通 glibc heap，kernel 這邊的 heap（SLUB）會從頭教；也**不需要**寫過 kernel module，Part 0 會補。

這不是一份「速成題解」，而是一本**當今 Linux kernel exploitation 的參考書**。從「QEMU 怎麼把 kernel 跑起來」「怎麼讀 kernel 原始碼」開始，一路走過記憶體管理與 SLUB 內部、漏洞類型學、經典 mitigation（SMEP / SMAP / KASLR / KPTI）、現代 heap 利用（cross-cache / Dirty Pagetable / Dirty Cred / USMA）、CFI 之後的 data-only 攻擊、kernelCTF 最大礦區（netfilter / io_uring / eBPF）與其他子系統攻擊面、並發與 race condition、找洞與工具鏈（Syzkaller / KASAN / patch 分析）、跨平台差異（ARM64 PAC/MTE、容器逃逸），最後落到 kernelCTF 賽制、N-day 完整 walkthrough 與防禦偵測視角。每個利用技術配一個 vulnerable kernel module + exploit 骨架，不是看圖說故事。

## 為什麼學這個？

- **kernel exploitation 是系統安全的天花板**。拿下 ring 0 等於拿下整台機器 —— 繞過所有 user-space sandbox、container、EDR。理解它，你才真正理解一台機器的信任邊界在哪。
- **這裡的技巧和 user-space pwn 幾乎重來一次**。heap 不是 glibc heap 而是 SLUB，freelist 長得不一樣、spray 物件完全不同、tcache 技巧全部作廢。同一個概念要重新校準 —— 這也是為什麼本書把 SLUB 從頭教。
- **現代 mitigation 密度比 user-space 高一級**。SMEP、SMAP、KPTI、KASLR、CFI/KCFI、random kmalloc caches、FGKASLR、SLAB_VIRTUAL —— 光把這些搞清楚就是半門課，本書用整整兩個 Part 拆解它們與繞法。
- **CTF kernel pwn 題型與真實世界最接近**。user-space CTF 很多是 toy binary；kernel pwn 題經常就是真實 CVE 的精簡版。這裡練到的，拿去讀 Project Zero blog、kernelCTF submission 看得懂。
- **藍隊 / 防禦也需要這本**。要 harden、要寫偵測、要評估一個 CVE 的真實可利用性，你得先知道攻擊者怎麼想。最後一章專講防禦與偵測視角。

## 先修知識

- **C 語言**：會讀會寫，懂 pointer、struct、function pointer。
- **基本 x86-64 assembly**：會讀 `gdb disas` 輸出，知道 register、stack frame、call/ret 大致怎麼回事。
- **基本 pwn 概念**：知道 stack buffer overflow、ROP、info leak 是什麼意思（不要求精通 glibc heap —— SLUB 本書從頭教）。
- **Linux 命令列、QEMU 大致知道怎麼回事**。
- **不需要**讀過 kernel 原始碼，也**不需要**寫過 kernel module —— Part 0 會補。
- 想先補 user-space pwn 基本功的，去看本 repo 的 [`pentest`](../pentest/README.md) 或自己刷 pwn.college。

## 課程地圖

### Part 0 — 入門 on-ramp：先把 kernel 跑起來、讀得懂、戳得動（Ch 0–5）
- [Ch 0 環境搭建：QEMU + initramfs + kernel build + gdb remote](./00-environment-setup.md)
- [Ch 1 kernel pwn 全景：ring 0/3、威脅模型、攻擊者視角地圖](./01-kernel-pwn-landscape.md)
- [Ch 2 讀 kernel 原始碼的方法：tree 結構、追一條 syscall、版本與 config](./02-reading-kernel-source.md)
- [Ch 3 kernel C 與執行環境：無 libc、GFP flags、per-cpu、atomic/sleepable context](./03-kernel-c-runtime.md)
- [Ch 4 syscall 與 user/kernel boundary：ABI、copy_from/to_user、ioctl、uaccess](./04-syscall-boundary.md)
- [Ch 5 第一個 vulnerable kernel module：file_operations、ioctl、建自己的靶](./05-first-vulnerable-module.md)

### Part 1 — 記憶體管理與 allocator（Ch 6–9）
- [Ch 6 kernel 記憶體全景：virtual memory layout、physmap/direct map、vmalloc、page table](./06-kernel-memory-layout.md)
- [Ch 7 buddy allocator 與 page：order、migratetype、GFP、struct page](./07-buddy-allocator.md)
- [Ch 8 SLUB allocator 深挖：kmalloc-N cache、freelist、object 生命週期](./08-slub-allocator.md)
- [Ch 9 SLUB hardening：dedicated caches、ACCOUNT/cg、cache merge、FREELIST_HARDENED/RANDOM](./09-slub-hardening.md)
- [練習 A：trace kmalloc/kfree 的 slab 行為 + 寫 slab 觀測 module](./practice-a-slab-tracing.md)

### Part 2 — 漏洞類型學與經典利用（Ch 10–16）
- [Ch 10 kernel 漏洞類型學：memory corruption vs logic、OOB、UAF、type confusion、refcount、integer、race](./10-vulnerability-taxonomy.md)
- [Ch 11 Stack buffer overflow in kernel：canary 與第一次 ret2usr](./11-stack-overflow.md)
- [Ch 12 控制流劫持基礎：劫 RIP、commit_creds(prepare_kernel_cred(0))、安全回 userland](./12-control-flow-hijack.md)
- [Ch 13 SMEP / SMAP：ret2usr 封死後的 kernel ROP 與 stack pivot](./13-smep-smap.md)
- [Ch 14 KASLR 與 info leak 大全：leak 途徑、percpu、uninitialized 記憶體、oracle](./14-kaslr-infoleak.md)
- [Ch 15 KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline](./15-kpti.md)
- [Ch 16 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred，任意寫轉提權](./16-classic-primitives.md)
- [練習 B：從 stack overflow 到 root shell（全 mitigation 開）](./practice-b-stack-to-root.md)

### Part 3 — Heap 利用核心：現代 kernel pwn 主戰場（Ch 17–25）
- [Ch 17 Heap overflow in kmalloc：相鄰 object 布局與 cache 選擇](./17-heap-overflow.md)
- [Ch 18 UAF / Double Free：SLUB freelist corruption 與 poisoning](./18-uaf-double-free.md)
- [Ch 19 Heap spray 物件大全：msg_msg / sk_buff / pipe_buffer / tty_struct / seq_operations / user_key_payload](./19-spray-objects.md)
- [Ch 20 Heap grooming：精確控制 slab 布局、defrag、穩定 spray](./20-heap-grooming.md)
- [Ch 21 從 heap 到 RIP：tty_struct ops hijack、seq_operations、pipe_buffer](./21-heap-to-rip.md)
- [Ch 22 Cross-cache attack：跨 kmalloc cache 打 dedicated slab](./22-cross-cache.md)
- [Ch 23 Dirty Pagetable：PTE 覆寫拿任意實體記憶體 R/W](./23-dirty-pagetable.md)
- [Ch 24 Dirty Cred / file struct：不經 ROP 的 data 攻擊](./24-dirty-cred.md)
- [Ch 25 USMA 與 page-level spray：把 kernel page 映射進 userspace、pgv/packet socket](./25-usma-page-spray.md)
- [練習 C：UAF → tty_struct ops hijack 完整鏈](./practice-c-uaf-tty.md)
- [練習 D：Cross-cache → Dirty Pagetable 綜合題](./practice-d-crosscache-dirtypagetable.md)

### Part 4 — 現代 mitigation 與繞法（Ch 26–29）
- [Ch 26 2023+ mitigation 全景：random kmalloc caches、SLAB_VIRTUAL、CFI/KCFI、FGKASLR、INIT_ON_ALLOC/FREE](./26-modern-mitigations.md)
- [Ch 27 穿越 random kmalloc caches：hash 匹配、cg 分流、spray 策略、victim 挑選](./27-random-kmalloc-caches.md)
- [Ch 28 CFI / KCFI：原理、為什麼 kernel ROP 路越走越窄](./28-cfi-kcfi.md)
- [Ch 29 data-only attack 技術集：為什麼成主流、pipe_buffer、cred、合法 target 搜尋](./29-data-only-attack.md)

### Part 5 — 子系統攻擊面：reference book 的廣度核心（Ch 30–38）
- [Ch 30 netfilter / nf_tables：kernelCTF 最大礦區](./30-netfilter-nftables.md)
- [Ch 31 io_uring：SQE/CQE 與 async ring 的 UAF 模式](./31-io-uring.md)
- [Ch 32 eBPF：verifier / JIT、map 型漏洞、verifier bypass](./32-ebpf.md)
- [Ch 33 socket / skb 家族：AF_PACKET、AF_UNIX、pgv、各種 skb](./33-socket-skb.md)
- [Ch 34 檔案系統攻擊面：ext4 / f2fs / ntfs3 image parsing、fuzzing 礦區](./34-filesystem-attack-surface.md)
- [Ch 35 TTY / keyring / watch_queue / pipe：經典 spray 與原語來源](./35-tty-keyring-watchqueue.md)
- [Ch 36 User namespace 與攻擊面放大：unprivileged user 怎麼拿 CAP_NET_ADMIN](./36-user-namespace.md)
- [Ch 37 其他子系統速覽：ksmbd / GPU-DRM / USB / v4l2 / perf / timerfd](./37-other-subsystems.md)
- [Ch 38 Android / 廠商 kernel 子系統：binder、ashmem、廠商 driver 生態](./38-android-vendor-subsystems.md)
- [練習 E：nf_tables 類漏洞從 PoC 到 stable exploit](./practice-e-nftables-exploit.md)

### Part 6 — 並發與 race condition（Ch 39–41）
- [Ch 39 kernel 並發模型：lock 家族、RCU、per-cpu、記憶體屏障、race window](./39-concurrency-model.md)
- [Ch 40 race condition 漏洞：TOCTOU、double-fetch、refcount race](./40-race-conditions.md)
- [Ch 41 拉長 race window：userfaultfd、FUSE、調度操控](./41-widening-race-window.md)
- [練習 F：寫一個 race condition exploit（userfaultfd 拉 window）](./practice-f-race-exploit.md)

### Part 7 — 找洞與工具鏈（Ch 42–45）
- [Ch 42 讀 patch / commit 找 bug：patch diff → root cause 方法論、CVE 拆解](./42-patch-to-rootcause.md)
- [Ch 43 Syzkaller / syzbot：fuzzer 原理、看懂 report、跑自己的 fuzzer](./43-syzkaller.md)
- [Ch 44 debug 工具鏈：KASAN / KCSAN / KMSAN / UBSAN / lockdep / ftrace / kprobe](./44-debug-toolchain.md)
- [Ch 45 exploit 穩定化工程：heap grooming、race retry、cleanup、避免 panic、跨版本移植](./45-exploit-stabilization.md)

### Part 8 — 跨平台與逃逸（Ch 46–48）
- [Ch 46 ARM64 kernel pwn：PAC / MTE / BTI、syscall ABI 差異、Android kernel 生態](./46-arm64-kernel-pwn.md)
- [Ch 47 容器 / 虛擬化逃逸邊緣：container escape、hypervisor 攻擊面概覽](./47-container-vm-escape.md)
- [Ch 48 其他架構速覽：RISC-V / MIPS / 嵌入式 Linux kernel 差異](./48-other-architectures.md)

### Part 9 — 實戰、防禦與收尾（Ch 49–51 + Final）
- [Ch 49 kernelCTF 賽制與流程：Hardened target、穩定性要求（附歷史 LTS/COS/Mitigation 三賽道對照）](./49-kernelctf-overview.md)
- [Ch 50 從 patch 到 exploit：N-day 完整 walkthrough](./50-patch-to-exploit.md)
- [Ch 51 防禦與偵測視角：hardening config、exploit 偵測、lockdown、KSPP、藍隊怎麼看這些技術](./51-defense-detection.md)
- [Final Project：重建一個已公開 kernelCTF submission](./final-project-kernelctf-rebuild.md)

## 學習方式建議

1. **Part 0 和 Ch 8（SLUB）不懂就別往下**。後面一半章節都踩在「kmalloc 這個 size 會落在哪個 cache、和誰同一塊 slab、freelist 怎麼被覆寫」上。SLUB 一卡，Part 3 整個看不懂。
2. **每個利用技術的 vulnerable module 一定要自己編、自己跑、自己打**。看懂 exploit 骨架 ≠ 會寫 exploit。你要親手把 RIP 劫到 `commit_creds(prepare_kernel_cred(0))` 至少一次，才會對 kernel 地址、per_cpu offset、KPTI trampoline 有肌肉記憶。
3. **故意把 mitigation 打開、打開一半、全開跑三遍**。Ch 13 的同一題，`nosmep nosmap` 跑一次、只關 SMAP 跑一次、全開跑一次。你會親眼看到每層防禦逼你換多少招。
4. **讀 kernelCTF repo 的 submission writeup**。<https://github.com/google/security-research/tree/master/pocs/linux/kernelctf>。即使前面幾章還看不懂，也該先翻翻每篇的長度與結構，建立「真正的 kernel exploit 長什麼樣」的感覺。
5. **別迷信 ROP**。2024 之後 CFI 普及，kernel 上純 ROP 路越走越窄。Part 3 後半（Ch 22–25）和 Part 4（Ch 28–29）的 data-only 技術才是未來。不要把這些當作「進階加料」略過。
6. **Python exploit 可以，C exploit 必須會**。kernelCTF 的 submission 幾乎清一色是 static-compiled C，因為 ptrace / namespace / 穩定性要求逼你用 syscall 直接打。pwntools 在這邊派不上用場。
7. **把本書當參考書用**。讀完一輪後，碰到一個新 CVE，照 Part 7（找洞與工具鏈）的方法論走，需要哪個子系統就回 Part 5 翻那一章。

> **安全與合法性聲明**：本書純為學習、理解機制與防禦研究而寫。所有動手實驗一律在你**自己擁有或明確授權**的環境（自有 VM、CTF 靶場、刻意架設的 vulnerable lab）。涉及實作處預設「在受控、授權環境操作」。凡未在本環境實測的段落（kernel module、需要 root、特定硬體）會標注「未實測，理論預期行為」並說明如何驗證。

## 精選資料庫

這裡列的是整本書最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《A Guide to Kernel Exploitation: Attacking the Core》** — Enrico Perla & Massimiliano Oldani（Syngress, 2010）
  - 老了（涵蓋到 2.6 kernel），但 mental model、攻擊者思考框架還在；當作 Part 2 的背景讀物
- **[The Linux Kernel Module Programming Guide](https://sysprog21.github.io/lkmpg/)**
  - 寫 kernel module 的入門；Part 0 的 Ch 5 搭這個一起讀
- **[Linux 原始碼（Bootlin Elixir）](https://elixir.bootlin.com/linux/latest/source)**
  - 權威來源；遇到行為不符預期時，讀 source 是最終仲裁。Ch 2 教你怎麼用它

### 推薦部落格 / 系列（追這些人）

- **[pawnyable — Linux Kernel](https://pawnyable.cafe/linux-kernel/)**
  - 目前中文/日文圈最完整的 kernel pwn 教材；本書多章的靶題設計參考它
- **[Attacking the Linux Kernel（lrh2000）](https://lrh2000.com/)**
  - 把 cross-cache、Dirty Pagetable 等現代技術講得很透的系列
- **[smallkirby — kernel exploit 101 系列](https://smallkirby.hatenablog.com/)**
  - 從環境到實戰的完整路徑，和本書 Part 0–2 對應得很好
- **[Google Project Zero blog（Linux kernel 系列）](https://googleprojectzero.blogspot.com/)**
  - 每篇都是教科書等級的 root cause + exploit writeup

### 權威來源 / 比賽

- **[kernelCTF submission repo](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf)**
  - 最硬最新的 writeup 來源；Part 9 與 Final Project 直接以此為對象
- **[Syzkaller / syzbot](https://syzkaller.appspot.com/)**
  - 看現代 bug 的長相；Ch 43 教你讀它、跑它
- **[Kernel Self Protection Project（KSPP）](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)**
  - mitigation 的來源與 roadmap；Ch 26 與 Ch 51 的背景

### 讀完本書之後

- **[CTF-Wiki pwn/linux/kernel（中文）](https://ctf-wiki.org/pwn/linux/kernel/)** —— 當索引，對照本書查漏補缺
- **[Will's Root](https://www.willsroot.io/)**、**[D3v17](https://syst3mfailure.io/)** —— 進階 writeup，把個別技術推得更深
