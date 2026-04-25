# Linux Kernel Pwn 學習筆記：以 kernelCTF 為終點

> 給 user-space pwn 已經熟練（穩定解 glibc heap 題、ROP / format string 沒問題）、想把目標訂在 Google kernelCTF 的人。不碰 ARM64 Android 賽道，只最後一章對照差異。

這系列從「QEMU 怎麼把 kernel 跑起來」開始，一路走過經典 mitigation、現代 heap 利用（cross-cache / Dirty Pagetable / USMA）、隨機 kmalloc caches 繞法，最後落到 kernelCTF 最大礦區 — netfilter / nf_tables / io_uring / eBPF — 並用一個已公開的 kernelCTF submission 從 patch diff 復刻到 stable exploit 作收尾。每章配一個 vulnerable kernel module + exploit 骨架，不是看圖說故事。

## 為什麼學這個？

- **kernelCTF 是目前最接近真實 1-day 開發的比賽**。打贏這個，寫 kernel exploit 的所有基本功你就有了 — patch diff 閱讀、root cause、PoC、穩定化、繞過所有 mitigation。市面上沒有別的活動能這樣逼你。
- **user-space pwn 的技巧在這裡幾乎重來一次**。heap 不是 glibc heap 而是 SLUB，freelist 長得不一樣、spray 物件完全不同、你熟的 tcache 技巧全部作廢。同一個概念要重新校準。
- **現代 mitigation 密度比 user-space 高一級**。SMEP、SMAP、KPTI、KASLR、CFI、random kmalloc caches、FGKASLR、SLAB_VIRTUAL — 光把這些搞清楚就是半門課。
- **CTF kernel pwn 題型與真實世界最接近**。user-space CTF 很多是 toy binary；kernel pwn 題經常就是真實 CVE 的精簡版。這裡練到的，拿去讀 Project Zero blog 看得懂。

## 先備知識

- **User-space pwn 熟練**：ROP、glibc heap（tcache / fastbin / unsorted bin）、format string、穩定解題。不熟的話先去補 `learn_pentest` 或自己刷 pwn.college。
- **C 語言 + 基本 x86-64 assembly**：會讀 `gdb disas` 輸出。
- **Linux 命令列、QEMU 大致知道怎麼回事**。
- **不需要**讀過 kernel 原始碼，也**不需要**寫過 kernel module — Ch 1-3 會補。

## 課程地圖

### Part 1 — 環境與 kernel 基礎
- [Ch 0 環境搭建：QEMU + initramfs + kernel build + gdb remote](./00-environment-setup.md)
- [Ch 1 Linux kernel 從 user 視角：syscall、user/kernel 切換、address space](./01-kernel-from-user-view.md)
- [Ch 2 第一個 vulnerable kernel module：file_operations、ioctl、copy_from_user](./02-first-vulnerable-module.md)
- [Ch 3 SLUB Allocator：kmalloc-N cache、freelist、object 生命週期](./03-slub-allocator.md)

### Part 2 — 經典漏洞與保護機制
- [Ch 4 Stack Buffer Overflow in kernel：canary 與第一次 ret2usr](./04-stack-overflow.md)
- [Ch 5 SMEP / SMAP：commit_creds + prepare_kernel_cred ROP](./05-smep-smap.md)
- [Ch 6 KASLR 與 info leak：leak 途徑大全](./06-kaslr-infoleak.md)
- [Ch 7 KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline](./07-kpti.md)
- [Ch 8 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred](./08-classic-primitives.md)
- [練習 A：從 stack overflow 到 root shell](./practice-a-stack-overflow-to-root.md)

### Part 3 — Heap 戰場：現代 kernel pwn 主戰場
- [Ch 9 Heap Overflow in kmalloc：相鄰 object 布局與 cache 選擇](./09-heap-overflow.md)
- [Ch 10 UAF / Double Free：SLUB freelist corruption](./10-uaf-double-free.md)
- [Ch 11 Heap Spray 物件大全：msg_msg / sk_buff / pipe_buffer / tty_struct / seq_operations / user_key_payload](./11-spray-objects.md)
- [Ch 12 從 heap 到 RIP 控制：tty_struct ops hijack、seq_operations、pt_regs](./12-heap-to-rip.md)
- [Ch 13 Cross-Cache Attack：跨 kmalloc cache 打 dedicated slab](./13-cross-cache.md)
- [Ch 14 Dirty Pagetable / Dirty Cred：不經 ROP 拿任意 R/W](./14-dirty-pagetable-cred.md)
- [Ch 15 USMA：把 kernel page 映射進 userspace](./15-usma.md)
- [練習 B：UAF → tty_struct ops hijack 完整鏈](./practice-b-uaf-tty-struct.md)
- [練習 C：Cross-cache → Dirty Pagetable 綜合題](./practice-c-cross-cache-dirty-pagetable.md)

### Part 4 — 現代 mitigation 與繞法
- [Ch 16 2023+ kernel 在 defend 什麼：random kmalloc caches、SLAB_VIRTUAL、CFI、FGKASLR](./16-modern-mitigations.md)
- [Ch 17 穿越 random kmalloc caches：hash 匹配、spray 策略、victim 挑選](./17-random-kmalloc-caches.md)
- [Ch 18 CFI / KCFI 之後：data-only attack 為什麼成主流](./18-data-only-attack.md)

### Part 5 — kernelCTF 熱門子系統
- [Ch 19 netfilter / nf_tables：kernelCTF 最大礦區](./19-netfilter-nftables.md)
- [Ch 20 io_uring：SQE/CQE 與 async ring 的 UAF 模式](./20-io-uring.md)
- [Ch 21 eBPF：verifier bypass 與 map 型漏洞](./21-ebpf.md)
- [Ch 22 User namespace + ksmbd + 其他子系統速覽](./22-other-subsystems.md)
- [練習 D：nf_tables 類漏洞從 PoC 到 stable exploit](./practice-d-nftables-exploit.md)

### Part 6 — ARM64 差異速查
- [Ch 23 ARM64 kernel pwn 差異：PAC / MTE / Android kernel 生態](./23-arm64-diff.md)

### Part 7 — kernelCTF 實戰
- [Ch 24 kernelCTF 賽制與流程：LTS / COS / Mitigation 賽道、穩定性要求](./24-kernelctf-overview.md)
- [Ch 25 從 patch 到 exploit：N-day 完整 walkthrough](./25-patch-to-exploit.md)
- [Final Project：重建一個已公開 kernelCTF submission](./final-project-kernelctf-rebuild.md)

## 學習方式建議

1. **Ch 3 SLUB 不懂就別往下**。後面一半章節都踩在「kmalloc 這個 size 會落在哪個 cache、和誰同一塊 slab、freelist 怎麼被覆寫」上。Ch 3 一卡，Part 3 整個看不懂。
2. **每章的 vulnerable module 一定要自己編、自己跑、自己打**。看懂 exploit 骨架 ≠ 會寫 exploit。你要親手把 RIP 劫到 `commit_creds(prepare_kernel_cred(0))` 至少一次，才會對 kernel 地址、per_cpu offset、KPTI trampoline 有肌肉記憶。
3. **故意把 mitigation 打開、打開一半、全開跑三遍**。Ch 5 的同一題，`nosmep nosmap` 跑一次、只關 SMAP 跑一次、全開跑一次。你會親眼看到每層防禦逼你換多少招。
4. **讀 kernelCTF repo 的 submission writeup**。<https://github.com/google/security-research/tree/master/pocs/linux/kernelctf>。即使前面幾章還看不懂，也該先翻翻每篇的長度與結構，建立「真正的 kernelCTF exploit 長什麼樣」的感覺。
5. **別迷信 ROP**。2024 之後 CFI 普及，kernel 上純 ROP 路越走越窄。Part 3 後半（Ch 13-15）和 Part 4（Ch 18）的 data-only 技術才是未來。不要把這些當作「進階加料」略過。
6. **Python exploit 可以，C exploit 必須會**。kernelCTF 的 submission 幾乎清一色是 static-compiled C，因為 ptrace / namespace / 穩定性要求逼你用 syscall 直接打。pwntools 在這邊派不上用場。

## 參考資料

- *A Guide to Kernel Exploitation: Attacking the Core* — Enrico Perla & Massimiliano Oldani, Syngress 2010 — 老了，但 mental model 還在
- *The Linux Kernel Module Programming Guide* — <https://sysprog21.github.io/lkmpg/> — 寫 kernel module 的入門
- kernelCTF submission repo — <https://github.com/google/security-research/tree/master/pocs/linux/kernelctf> — 最硬最新的 writeup 來源
- *Attacking the Linux Kernel* — lrh2000 的系列 — <https://lrh2000.com/>
- pawnyable Linux Kernel — <https://pawnyable.cafe/linux-kernel/> — 目前中文/日文圈最完整的 kernel pwn 教材
- *The toddler's introduction to Heap exploitation* 系列（CTF-Wiki 中文版）— kernel 章節對 SLUB 與 spray 物件整理得不錯
- kpwn 必看：`msg_msg`、`sk_buff`、`pipe_buffer` 各自的 upstream 原始碼 — 沒什麼比讀 source 更快
- Project Zero blog 的 Linux kernel 系列 — <https://googleprojectzero.blogspot.com/> — 每篇都是教科書
- Syzkaller 與 syzbot — <https://syzkaller.appspot.com/> — 看現代 bug 的長相
- *Linux Kernel Exploit (CTF) (kernel exploit 101)* — smallkirby — <https://smallkirby.hatenablog.com/>
