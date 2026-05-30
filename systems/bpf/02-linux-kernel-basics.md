# Ch 2 — 你需要的 Linux kernel 底層知識

> **目標**：建立足夠的 kernel 背景知識，讓後面每章的「kernel hook 掛在哪、能看到什麼資料」變得直覺而不是記憶術。這章只講 eBPF 真正需要的部分，不是完整的 kernel 課程。

## 為什麼要先學這個？

eBPF 程式掛在 kernel 的特定位置上執行，能看到的資料由那個位置決定。如果你不知道 syscall 的進入點在哪、`task_struct` 是什麼、`sk_buff` 是什麼、VFS 層在哪裡——你在寫 BPF 程式的時候就只能在黑暗中摸索，不知道為什麼某個資料拿不到、不知道要 attach 到哪個 hook 才能看到想看的東西。

這章不求完整，求**有地圖**。

## 先建立直覺：kernel 的兩條主要執行路徑

```
userspace process
      │
      │  syscall（系統呼叫）
      ▼
┌─────────────────────────────────────────────────────┐
│                    Linux kernel                      │
│                                                     │
│  ┌──────────────┐    ┌───────────────┐              │
│  │  syscall     │    │   interrupt   │              │
│  │  handling    │    │   handling    │              │
│  │  (同步)       │    │   (非同步)     │              │
│  └──────┬───────┘    └──────┬────────┘              │
│         │                  │                        │
│         ▼                  ▼                        │
│  ┌──────────────────────────────────────────┐       │
│  │           kernel subsystems              │       │
│  │  VFS  │  Memory  │  Network  │  Process  │       │
│  └────────────────────────────────────────┘        │
│                                                     │
└─────────────────────────────────────────────────────┘
```

eBPF 能在這兩條路徑的任何地方掛 hook：syscall 進入 / 退出時、任意 kernel 函式進入 / 退出時、中斷觸發時、網路封包進入時、process 被 schedule 時……

## Process 模型：task_struct

Linux 用 `struct task_struct`（定義在 `include/linux/sched.h`）表示一個執行單位（thread）。每個 thread 有自己的 `task_struct`，process 的多個 thread 共享一個 `mm_struct`（記憶體映射）。

幾個 eBPF 常用的欄位：

```c
struct task_struct {
    pid_t           pid;        // thread ID（在 Linux 裡是 thread）
    pid_t           tgid;       // thread group ID，即 process ID

    char            comm[TASK_COMM_LEN];  // process 名稱（最長 15 字元）

    struct mm_struct *mm;       // 記憶體映射（kernel thread 是 NULL）

    struct files_struct *files; // 開啟的 file descriptor 表

    const struct cred *cred;    // 權限（uid, gid, capabilities）

    struct css_set   *cgroups;  // 所屬的 cgroup

    // ... 還有很多，vmlinux.h 裡有完整定義
};
```

在 BPF 程式裡取得當前 process 的 task_struct：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

SEC("kprobe/some_function")
int example(struct pt_regs *ctx)
{
    // bpf_get_current_task() 回傳 struct task_struct *
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();

    // bpf_get_current_pid_tgid() 直接回傳 (tgid << 32 | pid)
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 tgid = pid_tgid >> 32;   // process ID
    u32 pid  = pid_tgid & 0xFFFFFFFF;  // thread ID

    // bpf_get_current_comm() 填充 process 名稱
    char comm[16];
    bpf_get_current_comm(&comm, sizeof(comm));

    bpf_printk("tgid=%u pid=%u comm=%s\n", tgid, pid, comm);
    return 0;
}
```

> **Linux 命名混亂警告**：Linux 裡的 `pid` 是 POSIX 說的 `thread ID`，Linux 裡的 `tgid` 是 POSIX 說的 `process ID`。`ps` 命令顯示的 PID 是 `tgid`。eBPF 程式裡通常用 `pid_tgid >> 32` 取 process 的「PID」。

## Syscall：kernel 和 userspace 的邊界

syscall 是 userspace 請求 kernel 服務的唯一正式路徑。在 x86-64 上，`syscall` 指令把 CPU 從 ring 3（userspace）切換到 ring 0（kernel），並跳到 kernel 的 entry point。

```
userspace                            kernel
─────────                            ──────
write(fd, buf, len);
  │
  │  syscall instruction             ENTRY(do_syscall_64)
  │  rax = __NR_write (1)     ──▶        sys_write()
  │  rdi = fd                               │
  │  rsi = buf                         sys_call_table[rax]
  │  rdx = len                              │
  │                                    ks_write()
  │                                    vfs_write()
  │                                         │
  │◀──── return value ──────────────────────┘
```

eBPF 可以在兩個點掛 hook：

- `tracepoint/syscalls/sys_enter_<name>`：syscall 剛進入，能看到所有參數
- `tracepoint/syscalls/sys_exit_<name>`：syscall 即將返回，能看到回傳值

```bash
# 查看所有 syscall tracepoints
sudo bpftrace -l 'tracepoint:syscalls:*' | head -20
# tracepoint:syscalls:sys_enter_read
# tracepoint:syscalls:sys_exit_read
# tracepoint:syscalls:sys_enter_write
# ...
```

tracepoint 的 format（能看到哪些欄位）在 `/sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format`：

```bash
cat /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format
# name: sys_enter_openat
# field:int __syscall_nr;  offset:8; size:4; signed:1;
# field:int dfd;           offset:16; size:8; signed:1;
# field:const char * filename; offset:24; size:8; signed:1;
# field:int flags;         offset:32; size:8; signed:1;
# field:umode_t mode;      offset:40; size:8; signed:1;
```

## 記憶體：virtual address space

每個 process 有獨立的 virtual address space（由 `mm_struct` 管理），但 kernel 的 virtual address 在所有 process 裡是共享的：

```
Virtual Address Space（64-bit x86-64）

0x0000_0000_0000_0000
         ▲
         │  userspace（每個 process 獨立）
         │  text / data / heap / mmap / stack
         │
0x0000_7FFF_FFFF_FFFF （典型的 userspace 上限）
         :
0xFFFF_8000_0000_0000
         ▲
         │  kernel space（所有 process 共享）
         │  kernel text / data / vmalloc / physmap
         │
0xFFFF_FFFF_FFFF_FFFF
```

eBPF 程式跑在 kernel context，所以直接可以存取 kernel 的 virtual address。但**不能**直接 dereference userspace 的指標——要用 `bpf_probe_read_user()` 或 `bpf_probe_read_user_str()` 安全地從 userspace 讀資料：

```c
// 錯誤：直接 dereference userspace 指標（verifier 會拒絕）
char *user_ptr = (char *)ctx->args[1];
char c = *user_ptr;  // REJECTED by verifier

// 正確：用 bpf_probe_read_user()
char buf[256];
bpf_probe_read_user(buf, sizeof(buf), (void *)ctx->args[1]);
```

## File Descriptors 與 VFS

每個 process 維護一張 file descriptor table（`files_struct`），把整數 fd（0, 1, 2, ...）映射到 kernel 的 `struct file`。`struct file` 再指向 VFS 層的 `struct inode`。

```
process fd table
  fd 0 ──▶ struct file (stdin)  ──▶ inode (pipe / device / ...)
  fd 1 ──▶ struct file (stdout) ──▶ inode
  fd 2 ──▶ struct file (stderr) ──▶ inode
  fd 3 ──▶ struct file (some socket) ──▶ inode ──▶ struct socket ──▶ struct sock
```

BPF 程式可以在 `vfs_read()`、`vfs_write()` 等 VFS 函式上掛 kprobe，取得完整的路徑資訊：

```bash
sudo bpftrace -e '
kprobe:vfs_read {
    printf("pid=%d comm=%s\n", pid, comm);
}'
```

## 網路 Stack：sk_buff

Linux 網路 stack 用 `struct sk_buff`（skb）來表示一個封包。sk_buff 就像一個封包的容器，包含 data pointer、header pointer、metadata：

```
sk_buff 的記憶體佈局（簡化）：

    ┌──────────────────────────────────────────┐
    │           struct sk_buff                 │
    │  head ─────┐                             │
    │  data ──┐  │                             │
    │  tail ─┐│  │                             │
    │  end   ││  │                             │
    └─────────┼┼──┼──────────────────────────-─┘
              ││  │
              ▼▼  ▼
         ┌────────────────────────────────┐
         │ headroom │ mac │ ip │ tcp │data│ tailroom │
         └─────▲────────────────────────────────────┘
               │
               data 指向有效資料的開始
```

在 XDP（network driver 層）和 TC（traffic control 層）的 BPF 程式裡，你直接操作封包資料；在 socket filter 層的 BPF 程式裡，你透過 sk_buff 的 helper 存取。

```c
// XDP 程式：直接存取封包 bytes
SEC("xdp")
int xdp_filter(struct xdp_md *ctx) {
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    // 確認 Ethernet header 存在（verifier 要求做 bounds check）
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    bpf_printk("ethertype: 0x%04x\n", bpf_ntohs(eth->h_proto));
    return XDP_PASS;
}
```

## Scheduler 與 Context Switch（off-CPU 分析）

Scheduler（排程器）決定哪個 thread 在哪個 CPU core 上執行。當一個 thread 需要等待 I/O、或時間片用完、或主動讓出 CPU，就發生 **context switch**：kernel 把這個 thread 的 CPU 狀態（registers、PC）保存起來，切換到另一個 thread。

eBPF 可以追蹤 context switch 的時機，找出 process 在等什麼（這叫 off-CPU analysis）：

```bash
# 追蹤哪些 process 正在被排開、因為什麼原因
sudo bpftrace -e '
tracepoint:sched:sched_switch {
    printf("prev=%s next=%s\n", args->prev_comm, args->next_comm);
}'
```

context switch 的 tracepoint 是 `sched:sched_switch`，能看到被切出的 thread（prev）和被切入的 thread（next），以及切換原因（prev_state：`S` = sleep, `R` = running, `D` = uninterruptible sleep）。

## Namespace 與 Cgroup

**Namespace** 是 Linux 的隔離機制，讓不同 process 看到不同的 PID、網路介面、mount point 等。Docker/Kubernetes 的隔離就是靠 namespace 做的。

**Cgroup**（Control Group）是資源控制機制，可以限制 process group 的 CPU、記憶體、I/O 用量。

eBPF 的 cgroup 類型 program（`BPF_PROG_TYPE_CGROUP_*`）可以在 cgroup 層次掛 hook，做 per-container 的網路策略或 syscall 限制。

```bash
# 查看目前 process 所屬的 cgroup
cat /proc/self/cgroup

# 查看系統的 cgroup 階層
systemd-cgls | head -20
```

## Kernel 函式與 Symbol Table

eBPF 的 kprobe 能 attach 到任何**沒有被 inline** 的 kernel 函式上。要查哪些函式可用：

```bash
# 查看所有可用的 kprobe 目標
sudo cat /proc/kallsyms | grep " T " | head -20
# T 代表 text section（函式）
# t 代表 static 函式（小寫）

# 搜尋特定函式
sudo cat /proc/kallsyms | grep "vfs_read"
# ffffffff81234567 T vfs_read
```

有些函式被 compiler 內聯（inline）了，這些函式在 `/proc/kallsyms` 裡不存在，kprobe 掛不上去。這時候要用 tracepoint 或 fexit/fentry（基於 BTF，比 kprobe 更穩定）。

## 動手練習

1. 執行 `cat /proc/self/status`，找到 `Pid`、`Tgid`、`NSpid` 這三個欄位，解釋它們的差異

2. 執行以下 bpftrace 指令，觀察哪些函式在你的系統上最常被呼叫：
   ```bash
   sudo bpftrace -e 'kprobe:* { @[probe] = count(); } interval:s:5 { print(@); clear(@); exit(); }'
   ```

3. 查看 `openat` syscall 的 tracepoint format：
   ```bash
   cat /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format
   ```
   說出每個欄位的意義

4. 執行 `sudo bpftool prog list`，找到任何一個 loaded BPF program，說出它的 type 和 attach point

## 本章重點整理

- `task_struct` 是 Linux 的 process/thread 表示；`tgid` 是 POSIX 的 PID，`pid` 是 POSIX 的 TID
- syscall tracepoint（`sys_enter_*` / `sys_exit_*`）是 eBPF 最穩定的 attach 點之一
- eBPF 程式跑在 kernel context，直接存取 kernel virtual address；讀 userspace 記憶體要用 `bpf_probe_read_user()`
- `sk_buff` 是 Linux 封包的核心結構，XDP/TC/socket BPF 都在操作它的不同層次
- `/proc/kallsyms` 的 `T` 符號是可用的 kprobe 目標

## 自我檢核

- [ ] 能解釋 Linux 的 `pid` 和 POSIX 的 `pid` 的差異，以及在 BPF 程式裡應該用哪個
- [ ] 知道為什麼在 BPF 程式裡不能直接 dereference userspace 指標
- [ ] 能解釋 context switch 是什麼，以及 eBPF 怎麼用來做 off-CPU analysis
- [ ] 知道 `/proc/kallsyms` 裡的哪一類符號可以用作 kprobe 目標

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press, 2010）
  - **讀哪幾章**：Ch 6（process）、Ch 18（directory / links）、Ch 24–28（signal / process）、Ch 56–62（socket）——這本書是 userspace API 的聖經，對理解 eBPF 看到的 kernel 結構很有幫助
  - **這本書的定位**：讀完本章之後對某個 subsystem 想更深入，這本書是最好的第二步

- **《Linux Kernel Development, 3rd ed.》** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：Ch 3（process）、Ch 12（memory management）、Ch 16（page cache）
  - **這本書的定位**：從 kernel developer 角度看這些結構；2010 年出版，某些 API 已過時，但概念不過時

### 部落格

- **[Linux kernel map](https://makelinux.github.io/kernel/map/)** — makelinux
  - **這篇說什麼**：整個 kernel subsystem 的互動關係圖；視覺化地展示哪些子系統和哪些子系統有關聯
  - **讀哪裡**：整張圖；找到 BPF 和它連接的 subsystem
  - **為什麼值得讀**：快速建立 kernel 的全圖，避免不知道某個功能在哪裡

- **[Linux Source Code Browser (Elixir)](https://elixir.bootlin.com/linux/latest/source)** — Bootlin
  - **這篇說什麼**：可以直接線上讀 kernel source code，有符號跳轉、cross-reference
  - **讀哪裡**：`include/linux/sched.h`（task_struct）、`include/linux/skbuff.h`（sk_buff）、`kernel/bpf/syscall.c`（bpf syscall）
  - **為什麼值得讀**：每次你在 BPF 程式裡用一個 kernel 結構，都應該去 Elixir 查它的完整定義

→ [Ch 3 Classic BPF：tcpdump 的 packet filter](./03-classic-bpf.md)
