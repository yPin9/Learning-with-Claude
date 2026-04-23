# Ch 2 — Kernel / User space 邊界與 syscall

> 目標：搞懂 CPU ring 機制、memory 分割、syscall 怎麼跨界、kernel module 為什麼危險 — 這些是理解「BPF 的安全模型為何設計成這樣」的前置知識。

## 一個經常被忽略的事實：CPU 有「身分」

x86_64 的 CPU 有 4 個特權層級（Ring 0–3），但 Linux 只用兩個：

```
┌─────────────────────────────────────┐
│ Ring 0  Kernel              ←── 全能：操作硬體、改 page table、       │
│         （特權態 / privileged）         關中斷、執行特權指令          │
├─────────────────────────────────────┤
│ Ring 3  User process        ←── 受限：只能用普通指令、              │
│         （非特權態 / unprivileged）     存取自己的記憶體、不能碰硬體    │
└─────────────────────────────────────┘
```

CPU 跑的每一條指令都帶著「我現在是哪個 ring」的 context。Ring 0 跑 `outb`（寫 IO port）沒事，Ring 3 跑就 **#GP（General Protection Fault）** — kernel 收到後會給該 process 送 SIGSEGV。

**這個機制是硬體強制的，不是 OS 約定。** Linux kernel 自己也跑在 CPU 上，它沒辦法「偷改自己的 ring」 — 唯一切回 Ring 0 的方式是透過特定指令（`syscall` / `int`）觸發 CPU 的「mode switch」。

## Memory 怎麼分？

每個 process 看到的虛擬位址空間都被切兩半：

```
0xFFFFFFFFFFFFFFFF ┌────────────────────────────┐
                   │   Kernel space             │  ← Ring 0 才能碰
                   │   - kernel code            │     所有 process 共享同一份
                   │   - kernel data            │
                   │   - device memory          │
0xFFFF800000000000 ├────────────────────────────┤  ← x86_64 「canonical hole」
                   │           ...              │
                   │       (不可用)              │
                   │           ...              │
0x00007FFFFFFFFFFF ├────────────────────────────┤
                   │   User space               │  ← Ring 3 可碰
                   │   - 你的 code              │     每個 process 各自一份
                   │   - heap / stack           │
                   │   - mmap'd files           │
0x0000000000000000 └────────────────────────────┘
```

**關鍵性質**：

1. **Kernel space 的 mapping 對所有 process 都一樣** — 這是設計上的，方便 syscall 進去後 kernel 自己有路可走，不需要切 page table。
2. **User space 程式即使知道 kernel 的位址，也碰不到** — page table 把那段標記成「supervisor only」，Ring 3 一存取就 #PF（Page Fault）。
3. **Kernel 反過來能讀寫 user space**，但要透過 `copy_from_user()` / `copy_to_user()` — 這兩個函式有特殊的 exception handler，因為 user 給的指標可能是 garbage。

理解這個之後，下面這個問題就很合理了：**user space 程式想叫 kernel 做事，怎麼辦？**

## Syscall：唯一合法的橋

唯一的辦法是觸發一個 **「合法的 mode switch」**。x86_64 用 `syscall` 指令：

```asm
; user space 想呼叫 write(1, "hi\n", 3)
mov rax, 1           ; syscall number = 1 (sys_write)
mov rdi, 1           ; arg 1: fd
mov rsi, msg         ; arg 2: buffer
mov rdx, 3           ; arg 3: count
syscall              ; ←── CPU 這裡切到 Ring 0
                     ;     跳到 kernel 預先註冊的 entry point
```

`syscall` 指令會做這幾件事（CPU 硬體完成）：

1. 從 MSR `LSTAR` 讀 kernel entry point 位址
2. 切換 ring 0、切換 stack 到 kernel stack
3. 跳轉到 entry point

到了 kernel 那邊，看 `rax` 是哪個 syscall number、查 syscall table、call 對應 handler。執行完用 `sysret` 切回 Ring 3。

**這就是整個 user/kernel 邊界的全貌**：你只能透過 syscall 跨過去。沒有其他「合法」的路。

```bash
# 看 Linux 有多少個 syscall：
ausyscall --dump | wc -l    # 大概 400+ 個
# 或：
grep -E "^#define __NR_" /usr/include/asm-generic/unistd.h | wc -l
```

## 為什麼這樣設計？— 安全與穩定

兩個關鍵收益：

| | Ring 3 程式 crash | Ring 0 kernel crash |
|---|---|---|
| 影響範圍 | **只有自己** | **整台機器** |
| 表現 | SIGSEGV、core dump | kernel panic、需要重開 |
| 復原 | systemd 重啟服務即可 | 中斷服務、可能掉資料 |

**OS 整個設計哲學就是「把愚蠢隔離到 user space」**。一個 buggy 的 nginx 不會弄垮 kernel；一個 OOM 的 Java 不會殺到 sshd。前提是：**沒有 user space 程式能直接動 kernel**。

## 想擴展 kernel？三條傳統路徑

但有時候 user space 不夠 — 你需要 kernel 在某個事件觸發時跑你的 code（例如收 packet 時、process 啟動時、特定 syscall 被呼叫時）。傳統有三條路：

### 1. Kernel module（.ko 檔）

寫 C、編譯成 `.ko`、`insmod` 載入：

```c
// hello.ko
#include <linux/module.h>
static int __init hello_init(void) {
    printk(KERN_INFO "Hello from ring 0!\n");
    return 0;
}
static void __exit hello_exit(void) {
    printk(KERN_INFO "Bye!\n");
}
module_init(hello_init);
module_exit(hello_exit);
MODULE_LICENSE("GPL");
```

**強大但極度危險**：

- 寫錯一個指標 → kernel panic、整台死
- 寫錯一個 lock → 死鎖整個 kernel
- malloc 後忘記 free → kernel memory leak（user space process 死了會回收，kernel 不會）
- 跨 kernel 版本要重編，而且 API 不保證穩定（kernel 內部 API 沒有 ABI 承諾）

### 2. Character device + ioctl

寫個 kernel module 註冊一個 `/dev/foo`，user space 用 `ioctl()` 與之溝通。本質還是 kernel module，只是把對外介面變成「假裝是檔案」。FUSE、KVM、DRM 都用這條路。

### 3. /proc 與 sysfs

更輕量的選項，把 kernel 狀態暴露成檔案系統。`cat /proc/cpuinfo` 走的就是這條。但**只能讀寫狀態，不能注入邏輯**。

## 三條路的共同問題

1. **要嘛沒能力（/proc）、要嘛太危險（kernel module）**。
2. **沒有「安全沙盒」這個選項**。
3. **部署痛苦**：上 production 要簽核、要重開機、kernel 升級就重編。

歷史上，這就是為什麼 **observability / network / security** 工具大多選擇繞過 kernel — 用 `LD_PRELOAD`、用 user space proxy、用 sidecar — 雖然慢、雖然遮不住所有事，但不敢動 kernel。

## BPF 的破局

BPF 直接打掉這個僵局，提供第四條路：

| | Kernel module | BPF |
|---|---|---|
| 跑在 ring 幾 | Ring 0 | **Ring 0**（也是！） |
| 寫錯能 panic kernel 嗎 | 能 | **不能**（verifier 擋下來） |
| 能無窮迴圈嗎 | 能 | **不能**（bounded loop） |
| 能存取任意 kernel memory 嗎 | 能 | **不能**（只能透過 helper） |
| 能 malloc 嗎 | 能（但會 leak） | **不能**（只有 stack + map） |
| 載入要重開機嗎 | 偶爾 | **從不** |
| 跨 kernel 版本 | 重編 | **CO-RE 一份跑遍** |

**注意第二行**：BPF 跑在 ring 0，能力跟 kernel module 一樣強 — 但**自由度被 verifier 限制到不可能 panic kernel**。這是個極漂亮的工程取捨：用「語言層的限制」換「執行層的全能」。

下一章 Ch 3 會看在 BPF 之前，人們是怎麼用 ftrace、perf、strace 等工具勉強做到「在 user space 觀察 kernel」的 — 並看清為什麼那些方案都有結構性的痛點。

## 動手練習：用 strace 親眼看 syscall 跨界

```bash
strace -c ls /tmp
```

`-c` 會統計每個 syscall 被呼叫幾次。輸出大概像：

```
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ------------------
 31.20    0.000234           5        45         3 openat
 18.40    0.000138           4        32           mmap
 12.10    0.000091           4        21           read
  ...
```

每一個 syscall 名字 = 一次 user → kernel 的跨界。連 `ls /tmp` 這麼簡單的指令都做了上百次 mode switch。

接著做一個比較：

```bash
strace -c ls /tmp                 # 不過 strace 的開銷
strace -f -e trace=openat ls /tmp # 把 openat 一個個印出來
```

你會發現 strace 開著本身就慢得多 — 這是因為 strace 用的是 `ptrace()`，**每次 syscall 進出都中斷兩次**。這是 BPF 出來之前 observability 的痛點之一，Ch 3 會詳細講。

## 一個常見誤解

「kernel space 是另一個 process」 — **錯**。

Kernel 不是一個獨立 process。它是「跑 user process 的同一條 CPU、在 syscall 進來時切到 Ring 0 跑的另一段 code」。所以你 `top` 看到的 `%sys` 時間，其實是 your process 自己的 CPU 時間（只是花在 kernel mode）。

`top` 裡的 `[kthreadd]` `[ksoftirqd/0]` 這些方括號的東西**才是** kernel thread — 那才是「kernel 自己的 process」，但很少。

## 自我檢核

- [ ] 我能解釋 Ring 0 vs Ring 3 的差別與硬體強制性
- [ ] 我能畫出 user space / kernel space 在虛擬位址上的分割
- [ ] 我能用一句話說明 syscall 為什麼是「唯一合法的跨界方式」
- [ ] 我知道為什麼寫 kernel module 比寫 user 程式危險 100 倍
- [ ] 我能說出 BPF 跟 kernel module 的核心差別（同樣 Ring 0、不同安全模型）

下一章我們看 BPF 出來之前的世界 — ftrace、perf、strace 怎麼用、各自能做到什麼、痛在哪。理解了這些痛點，BPF 提供的解法才會顯得理所當然。

→ [Ch 3 傳統 kernel 觀測手段：printk、ftrace、perf、strace](./03-traditional-kernel-observation.md)
