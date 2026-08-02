# Ch 21 — kernel 攻擊面與專用 fuzzer

> **目標**：摸清 Linux kernel 的攻擊面構成，理解為什麼 afl++ 對著 kernel 毫無用武之地，以及 syzkaller 這類專用 fuzzer 是怎麼繞過這些障礙的。讀完能回答「我有一個新的 ioctl 介面，我該怎麼 fuzz 它？」

## 為什麼 kernel fuzzing 自成一門學問？

在 userland 裡，afl++ 的運作假設非常簡單：
- 目標是一個跑在使用者態的 process
- crash 就是 SIGSEGV / SIGABRT，fuzzer 直接知道
- 重置狀態 = fork 一個新 process
- coverage = 插樁在二進位裡的 bitmap

這四個假設在 kernel 全部失效。

kernel 是整台機器的管理者，你沒辦法「fork 一個新 kernel」。一旦 kernel panic，整台機器就死了，不是那個 process 死了。kernel 的輸入介面不是「讀一個檔案」，而是 **系統呼叫**——一個有幾百個 entry point 的複雜有狀態 API。某些漏洞不產生 crash，而是靜默地洩漏資料或悄悄繞過安全政策。kernel 的狀態龐大到，你呼叫了 `socket()` 之後，才能呼叫 `bind()`，才能呼叫 `listen()`——它是一個需要正確序列的狀態機。

這些差異不是小障礙，是根本性的問題，每一個都需要專門的技術手段。

## kernel 攻擊面：你有哪些輸入管道？

先建立直覺——kernel 有多少個「門」是從 userland 可以打進去的：

```
使用者程式
│
├── syscall 介面（read / write / ioctl / mmap / ...）
│   ├── 核心 POSIX syscall：200+ 個
│   └── Linux 擴充 syscall：io_uring / bpf / clone3 / ...
│
├── /dev/* 設備節點（字元設備 / 塊設備）
│   ├── 驅動 ioctl：每個驅動一套獨立介面
│   ├── read / write：序列通訊、DMA
│   └── mmap：GPU driver、DRM、video4linux
│
├── 偽檔案系統 /proc / /sys / /debugfs
│   └── 寫入特定節點觸發 kernel 行為
│
├── 網路堆疊輸入
│   ├── 從 socket 送進來的封包（應用層到傳輸層到 IP 層）
│   └── 原始封包（AF_PACKET / AF_NETLINK + CAP_NET_RAW）
│
├── 掛載檔案系統 image
│   └── ext4 / btrfs / xfs / f2fs image 裡任意欄位
│
├── eBPF 程式（需要 CAP_BPF 或 unprivileged bpf sysctl）
│   └── BPF 驗證器是一個巨大攻擊面
│
└── USB / PCI / ACPI（硬體週邊觸發的 kernel 路徑）
    └── 需要實體接觸或仿真裝置（BadUSB、virito-net 等）
```

每個「門」背後都有不同的輸入形態和不同的 fuzzing 策略。

### syscall 介面

syscall 是最大的攻擊面。Linux 6.x 上有超過 300 個 syscall，但重點不在數量，而在**資源相依性**。一個 syscall 的參數可能是：

- 整數 flag（有有限的合法組合）
- 指向 userland 結構的指標（kernel 會 copy_from_user）
- **之前某個 syscall 回傳的 fd 或 handle**

第三種是關鍵。`epoll_ctl()` 需要先 `epoll_create()` 建立的 epoll fd；`bind()` 需要先 `socket()` 建立的 socket fd。這意味著 fuzzer 必須理解 **resource 的生產-消費關係**，才能生成有意義的 syscall 序列。

### ioctl 介面

ioctl 是開 CVE 的金礦。每個核心驅動都可以定義自己的 ioctl command，而這些 command：

- 沒有統一的 ABI 文件（或文件過時）
- 常常接受複雜的巢狀結構體
- 往往只有特定硬體在用，很少人 fuzz 過

GPU 驅動（DRM / i915 / amdgpu）的 ioctl 介面特別龐大，且 bug 密度高。video4linux、media subsystem、網路驅動也是熱點。

### 檔案系統 image

把一個惡意構造的 ext4 image 掛載，核心就要解析裡面的 superblock、inode、extent tree。任何解析錯誤都可能在特權路徑上觸發。這類 bug 用 `mount()` 一個構造好的 image 就能觸發，不需要特別的硬體。

### eBPF 驗證器

從安全研究的角度，BPF 驗證器是近幾年最有生產力的攻擊面之一。它需要驗證任意的 BPF bytecode 序列，同時保證安全性——這個問題本身就很難，所以 CVE 不斷。CVE-2021-3490（ALU32 邊界問題）、CVE-2022-23222（pointer arithmetic 繞過）都是從 fuzzing BPF verifier 找到的。

### 網路封包解析

網路協定堆疊接受來自外部的封包，解析 TCP/IP/UDP/ICMP 頭部時有大量的 offset / length 計算，歷史上有很多 OOB read/write。現代的 kernel fuzzer 可以在 VM 裡用 `AF_PACKET` 直接打封包進去，不需要真的有外部流量。

## 為什麼需要專用 fuzzer？五個根本障礙

### 障礙一：特權邊界

kernel code 跑在 ring 0。你的 fuzzer 跑在 ring 3。你不能直接跳進去。你唯一能做的是透過 syscall 介面「請求」kernel 做事。這表示：

- fuzzer 要產生的不是 byte array，而是**syscall 序列**
- 每個 syscall 的參數必須是 kernel 能接受的有效格式（至少要有 valid pointer）
- 某些路徑需要特定的 capability（`CAP_SYS_ADMIN`、`CAP_NET_ADMIN`）

### 障礙二：崩潰 = 全機死亡

userland crash 是 fuzzer 的信號。kernel panic 是整台機器的終點。Fuzzer 要知道 crash 發生了，必須：

1. 在另一台機器（或 VM host）監控 guest 的序列埠 / console 輸出
2. 偵測到 panic 訊息後，把 crash log 存下來
3. 重啟 VM 繼續 fuzzing

這整個 crash detection + VM 管理的基礎設施，在 userland fuzzing 裡是不需要的。

### 障礙三：巨大且不可重置的狀態

fork() 能複製 process 的狀態；你沒辦法「fork kernel」。每次 syscall 都會修改 kernel 的全域狀態：
- 開了一個 fd → file descriptor table 改變
- 建了一個 socket → 網路堆疊狀態改變
- 分配了記憶體 → kernel heap 改變

fuzzing 結束後如何重置？兩個選擇：
- **重開 VM**：乾淨但慢（幾十秒）
- **snapshot restore**：快（毫秒級）但需要 hypervisor 支援（Part 5 的主題）

syzkaller 的解決方案是**每個 test program 在一個新的 sandboxed process 裡跑**，讓 kernel 的狀態汙染限制在 fd table 等 per-process 資源上，跑完 exit 讓 kernel 清理。對於全域資源（route table、module state）就接受有些汙染。

### 障礙四：覆蓋率難取

afl++ 靠 compile-time 插樁或 QEMU mode 的動態插樁取 edge coverage。kernel 跑在 ring 0，使用者態的 fuzzer 怎麼知道 kernel 裡跑過哪些路徑？

答案是 **KCOV**（Ch 22 的主題）——一個 kernel 插樁機制，把每個 syscall 跑過的 kernel PC 記錄在一塊 mmap 給 userland 的共享記憶體裡。使用者態的 fuzzer 可以直接讀取這塊記憶體，知道剛才那個 syscall 序列覆蓋了哪些 kernel 程式碼。

### 障礙五：reproducer 難寫

在 userland，crash reproducer 就是「執行帶著這個輸入的程式」。kernel crash 的 reproducer 要包含：
- 完整的 syscall 序列（含參數）
- 可能的 race condition 時序
- 特定的 kernel 設定（哪些 module 要載入）
- 特定的 kernel version

syzkaller 會自動嘗試生成最小化的 C reproducer，這是它的一大特色（Ch 26 詳述）。

## userland fuzzing vs kernel fuzzing 對比

| 面向 | userland fuzzing (afl++) | kernel fuzzing (syzkaller) |
|------|--------------------------|----------------------------|
| 輸入格式 | byte array 餵給 stdin/fd | syscall 序列 + 參數 |
| 崩潰偵測 | 子 process SIGSEGV | guest VM console panic |
| 狀態重置 | fork() | exit process / VM snapshot |
| 覆蓋率來源 | compile-time 插樁 bitmap | KCOV mmap 共享記憶體 |
| 執行速度 | 數萬 exec/sec | 數千 exec/sec |
| reproducer | 就是那個 input file | 最小化的 C syscall 序列 |
| crash oracle | ASAN / UBSan | KASAN / KMSAN / KCSAN |
| 環境需求 | 任何 Linux | KVM + 自 build kernel + image |
| 學習曲線 | 低（工具開箱即用） | 高（基礎設施複雜） |

速度差一個數量級是因為 syscall 的開銷遠大於 fork + exec，加上 VM 管理的額外成本。

## 主要 kernel fuzzer 生態系

### syzkaller（Google）

目前最成熟、最廣泛使用的 Linux kernel fuzzer。核心設計：
- **syzlang**：一個 DSL，用來描述每個 syscall 的參數型別和資源相依性
- **resource-aware mutation**：知道 fd 是 `socket(AF_INET, ...)` 的回傳值，才能正確地傳給需要 socket fd 的 syscall
- **自動最小化 reproducer**：找到 crash 之後自動 bisect，生成最小的 C program

Ch 24–26 是 syzkaller 的詳解。

### Trinity

比 syzkaller 更老、更簡單的 syscall fuzzer。沒有 syzlang 那樣的型別感知，純隨機打 syscall——但因為夠簡單，有時候能快速找到淺層 bug。現在主要作為對照基準用，不是主流選擇。

### kAFL / Nyx

不靠 KCOV，而靠 **Intel PT**（processor trace）取 coverage，搭配 VM snapshot。可以 fuzz 閉源的 kernel 或沒有插樁的路徑。Part 5 的主題。

### Healer

syzkaller 的「進化版」，來自中科大的研究。使用 learned call-relation model——從 corpus 中學習哪些 syscall 序列傾向一起出現，生成更有語義的序列。論文發在 SOSP 2021。

### Moonbit（filesystem fuzzing 專用）

專門打 Linux 檔案系統。餵進去的不是 syscall 序列，而是構造好的 filesystem image，透過 `mount()` + 一系列 VFS 操作觸發。

## 攻擊面分層：按優先順序選目標

如果你要開始 kernel fuzzing，怎麼選目標？按照「可達性 × 複雜度 × 歷史 CVE 密度」排序：

**第一梯隊（高 CVE 密度，可以不需要特殊硬體）：**
- BPF verifier：unprivileged BPF 在許多 distro 上預設開
- io_uring：近幾年最肥沃的攻擊面，Android 上的 PoC 多半從這裡來
- 網路 socket 處理（AF_UNIX / AF_INET / AF_PACKET）

**第二梯隊（需要 root 或 CAP_SYS_ADMIN，但洞很多）：**
- GPU 驅動 ioctl（DRM / i915 / amdgpu）
- v4l2 / media subsystem
- USB gadget emulation

**第三梯隊（需要特殊設定或硬體）：**
- USB host driver（需要 USB gadget 仿真，如 dummy_hcd）
- Bluetooth HCI（需要 HCI 仿真）
- 特定廠商驅動

對於 CTF，第一梯隊的 BPF 和 io_uring 是熱門題目來源。對於 CVE hunting，第二梯隊的 GPU 驅動是目前 patch 頻率最高的地方。

## 踩雷

**錯誤直覺 1**：「我用 root 跑 afl++ 對著 `/proc/sysrq-trigger` 寫，這樣就是在 fuzz kernel 了。」

實際上你在 fuzz 的只是那個特定的 sysfs 節點，而且你沒有 coverage，不知道 kernel 裡哪條路徑跑了，突變完全是盲目的。更重要的是，那個節點的解析邏輯很淺、歷史 bug 少，不值得這樣花時間。正確做法是用 KCOV 拿 coverage，用 syzkaller 的 syzlang 描述介面。

**錯誤直覺 2**：「kernel panic 了，我重開機就好，不需要自動化。」

手動重開機代表你每次 crash 都要人在旁邊等。syzkaller 一天能打出幾十個 crash，沒有自動重啟的 VM 管理你根本來不及處理。而且 crash log 需要在 panic 瞬間用序列埠接下來——等你手動去看，console 已經清掉了。

**錯誤直覺 3**：「我描述了 syscall 的參數型別，fuzzer 就能找到所有 bug 了。」

syzlang 的描述品質決定了 fuzzer 能探索到的攻擊面深度。如果你對一個 ioctl 的描述只有「接受一個 unsigned long」，fuzzer 生成的都是隨機數字，覆蓋不了那些只有傳進特定 flag 組合才能到的程式路徑。真正有效的 description 需要逆向工程 + 讀 kernel source，把每個 flag、每個 sub-command、每個 resource type 都標出來。

**錯誤直覺 4**：「ioctl 介面被文件化了，代表我不需要逆向。」

大多數驅動的 ioctl 文件不完整，或者文件和實作已經偏差。真正可信的只有 kernel source。對於閉源驅動，你就只能逆向。

## 進階延伸

- **namespace 與 fuzzing 隔離**：syzkaller 預設在 sandbox 模式裡跑，利用 user namespace + mount namespace 限制 test program 能做的事。理解 namespace 如何限制攻擊面，才能知道為什麼某些 bug 在 sandbox 裡觸發不了（見 kernel_internals Ch 38）。
- **Coverage-directed syscall selection**：Healer、MoonBit 等新一代 fuzzer 從 corpus 中學習 syscall 的相關性，這是 syzkaller 的改進方向，也是當前研究熱點。
- **Kernel fuzzing on Android**：Android 用的是同一套 Linux kernel，但 binder driver、mali GPU driver、供應商 HAL 是額外的攻擊面。Project Treble 之後的分層架構讓這些更容易被隔離 fuzz。

## 動手練習

1. 在你的 WSL2 kernel 上執行 `cat /proc/kallsyms | head -50`，觀察 kernel symbol 的地址。執行 `cat /proc/sys/kernel/kptr_restrict` 確認 KASLR 對 symbol 的限制程度。
2. 執行 `strace -e trace=all ls /tmp 2>&1 | head -30`，觀察一個簡單的 userland 程式觸發了哪些 syscall。這些就是 syscall fuzzer 要模擬的輸入。
3. 查閱 syzbot dashboard（https://syzkaller.appspot.com/upstream）的 open bugs 列表，找一個 subsystem 是你有興趣的 crash，看它的 reproducer C 程式——感受一下 kernel crash 的 reproducer 長什麼樣。
4. 在 `/sys/kernel/security/` 和 `/proc/net/` 各找三個你不認識的節點，用 `cat` 看內容，查它的 kernel source 路徑（用 `ls -la /proc/net/<name>` 和 kernel source 的 `DEFINE_PROC_SHOW_ATTRIBUTE`）。

## 本章重點

- Linux kernel 的攻擊面包含 syscall、ioctl、/proc /sys、網路封包、fs image、eBPF verifier、USB/PCI 七大類，每個都需要不同的 fuzzing 策略。
- Kernel fuzzing 面對五個根本障礙：特權邊界、崩潰=全機死、巨大不可重置狀態、coverage 難取、reproducer 難寫。
- syzkaller 透過 syzlang（型別描述 DSL）+ KCOV（kernel coverage）+ VM pool（crash isolation）解決這些障礙。
- 選目標按「可達性 × 複雜度 × 歷史 CVE 密度」——BPF verifier 和 io_uring 是當前最肥沃的入門攻擊面。

## 自我檢核

- [ ] 我能列出 kernel 的至少五種攻擊面類型，並說出每種的典型 fuzzing 工具
- [ ] 我能解釋為什麼 afl++ 的四個假設在 kernel 全部失效
- [ ] 我能說出 syzkaller 如何解決「崩潰=全機死」這個問題
- [ ] 我知道 KCOV 在 kernel fuzzing 生態裡扮演什麼角色（詳見 Ch 22）
- [ ] 我能解釋「resource 相依性」為什麼讓 kernel fuzzing 比 userland fuzzing 困難

## 延伸閱讀

1. **[Effective File System Fuzzing — Syzkaller docs: filesystems](https://github.com/google/syzkaller/blob/master/docs/linux/found_bugs_fs.md)**
   - 讀哪段：整份文件，特別是「approach」段落。
   - 學什麼：syzkaller 的開發者自己記錄了打 filesystem 的方法論和找到的 bug 列表，是 filesystem 攻擊面的第一手資料。
   - 關聯：Ch 21 的 filesystem image 攻擊面。

2. **[Dmitry Vyukov — "Coverage-guided kernel fuzzing with syzkaller"（LinuxCon 2016 slides）](https://events.static.linuxfoundation.org/sites/events/files/slides/Coverage-guided%20kernel%20fuzzing%20with%20syzkaller.pdf)**
   - 讀哪段：第 5–20 頁，syzkaller 設計動機和架構概覽。
   - 學什麼：syzkaller 原作者解釋他為什麼設計 syzlang 和 resource type，是理解 Ch 24–25 的背景必讀。
   - 關聯：Ch 24 syzkaller 架構的歷史脈絡。

3. **[Healer: Relation Learning Guided Kernel Fuzzing（SOSP 2021）](https://dl.acm.org/doi/10.1145/3477132.3483547)**
   - 讀哪段：Abstract + Section 2（問題定義）+ Section 4（relation model）。
   - 學什麼：如何從 corpus 中學習 syscall 相關性，改進 syzkaller 的盲目突變——代表 kernel fuzzing 的研究前沿。
   - 關聯：本章的 fuzzer 生態系介紹，以及 Ch 27 的進階技術。

4. **[syzbot — https://syzkaller.appspot.com/upstream](https://syzkaller.appspot.com/upstream)**
   - 讀哪段：隨便點一個 open bug，看它的「Crash report」和「Reproducer」欄位。
   - 學什麼：真實 kernel fuzzing 的 crash 長什麼樣、reproducer 有多複雜，以及哪些 subsystem 現在 bug 最多。
   - 關聯：本章的攻擊面選擇，以及 Ch 26 的 reproducer 生成。

→ [下一章：KCOV 底層](./22-kcov.md)
