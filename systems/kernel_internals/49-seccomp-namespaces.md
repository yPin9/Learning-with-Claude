# Ch 49 — seccomp-BPF 與 namespace 實作

> **目標**：搞懂容器與沙盒的兩大支柱——**seccomp**（限制一個 process 能發哪些 syscall）與 **namespace**（隔離一個 process 看到的系統資源視圖）。你要能讀懂 seccomp 在 syscall entry 怎麼攔截、seccomp-BPF 的 filter 怎麼看 syscall number 與參數決定 kill/errno/allow；能讀懂 `task->nsproxy` 怎麼掛住八種 namespace、`clone`/`setns`/`unshare` 三個動作各做什麼；並親手用 `unshare` 拼一個「迷你容器」、寫一段 seccomp-BPF 擋掉某個 syscall 看 process 被 kill。最後你會明白：**docker/runc 底層 = namespace + cgroup（Ch 50）+ seccomp/LSM（Ch 48），沒有魔法。**

> **本章環境**：延續 Ch 0 的 QEMU + gdb，但這章的動手大量落在使用者空間——`unshare`、`lsns`、`nsenter`、`/proc/<pid>/ns/`、一小段 C 寫的 seccomp filter。這些工具 busybox 版本可能不全，建議在你的 host（Ubuntu 24.04）或 QEMU 裡放一個完整的 util-linux + libseccomp 環境跑。gdb 的部分我們停在 `__seccomp_filter` 與 `copy_namespaces` 看資料流。

## 為什麼需要這個？

Ch 47 講 cred/capabilities、Ch 48 講 LSM，那些回答的是「這個 process **有沒有權限**做某件事」。但沙盒要問的是兩個更前面的問題：

1. **能不能連「發出這個 syscall」的機會都不給它？** 一個 PDF viewer 根本不該呼叫 `execve`、`ptrace`、`mount`。與其等它呼叫了再靠 LSM 判斷，不如直接讓 kernel 在 syscall 入口就把它擋掉——這是 **seccomp**（secure computing mode）。
2. **能不能讓它以為自己是整台機器的主人，其實只看得到一小塊?** 容器裡的 process 看到自己是 PID 1、看到自己的 root filesystem、自己的網路介面、甚至自己是 root——但這些都是**視圖幻覺**，宿主機看到的是完全不同的一組數字。這是 **namespace**。

這兩件事加上 cgroup（Ch 50，限制它**用多少**資源），就是一個容器的全部。沒有一個叫「container」的 kernel 物件——`docker run` 底下，runc 做的就是 `clone` 一堆 namespace、掛 cgroup、套 seccomp profile、`pivot_root`、`execve` 你的程式。你在 `docker` 課裡用的每個 `--cap-drop`、`--security-opt seccomp=`、`--network`，對應的就是本章這些 syscall。

而從攻擊面看（接 `kernel_pwn`/`oscp`）：seccomp 擋掉一半 syscall，等於擋掉一半 exploit primitive；但 **user namespace 讓非特權 user 能建 namespace**，反而把大量原本要 root 才能碰的 kernel 程式碼路徑暴露給普通 user——史上一大票 LPE 就是走這條。這章我們兩面都看。

## 先建立直覺：兩根柱子，各解決一件事

先把兩者分清楚，別混。它們是正交的，可以單獨用、也常一起用：

```
                 一個 process 想做壞事，kernel 從兩個維度框它

  seccomp（行為維度）                      namespace（視野維度）
  ─────────────────                       ─────────────────
  「你只准發這幾種 syscall」                「你只看得到這一份資源」
                                           
  user space                              user space
     │ syscall(execve, ...)                  process 問：我的 PID？→ 1
     ▼                                       process 問：hostname？→ "container"
  ┌──────────────┐  RET_KILL               process 問：eth0 在哪？→ 只有 lo
  │ seccomp 檢查 │──────────► process 死    （這些答案都是 namespace 給的幻覺，
  │ (BPF filter) │  RET_ALLOW                 宿主機看到的是另一組數字）
  └──────┬───────┘                         
         ▼ 放行                             隔離的是「看到什麼」，
    do_syscall_64                           不是「能做什麼」
    真正執行 syscall
```

- **seccomp** 攔的是**動作**：syscall 這個動詞。在 syscall entry（Ch 4，`do_syscall_64` 之前）就檢查。
- **namespace** 換的是**名詞**：process 看到的 PID、mount 樹、網路堆疊、hostname、uid 映射。透過 `task->nsproxy` 換一組。

一個 process 可以只套 seccomp（瀏覽器 renderer 沙盒常這樣，配合 namespace），可以只進 namespace（不套 seccomp 的裸容器），docker 預設兩者都上。

## seccomp：在 syscall 入口設一道閘

### 三種模式的演進

seccomp 有兩代。看懂演進才懂為什麼是現在這樣：

**seccomp-strict（mode 1，2005 年進 kernel）**：一個 process 一旦 `prctl(PR_SET_SECCOMP, SECCOMP_MODE_STRICT)`，就**只准發四種 syscall**：`read`、`write`、`_exit`、`sigreturn`。發任何別的 → 直接 `SIGKILL`。當初的用途是「把一顆 CPU 租給不信任的計算任務」——它只能讀寫已開好的 fd，什麼都做不了。太死了，實務上幾乎沒人用純 strict。

**seccomp-BPF（mode 2，2012 年進 kernel）**：這才是今天的主角。你附一段 **BPF 程式**（classic BPF / cBPF，不是 Ch 52 的 eBPF——注意這個歷史包袱）當 filter，kernel **每次 syscall 都跑這段 filter**，filter 看得到 syscall number 和參數（暫存器值），回傳一個裁決碼決定這次 syscall 怎麼處理。

> **關鍵區分（踩雷預告）**：seccomp 用的是 **classic BPF（cBPF）**，一種只有幾十條指令、無迴圈、可靜態驗證會終止的老 BPF。它**不是** Ch 52 那個能掛 map、能跑 tracing 的 eBPF。「seccomp-BPF」名字裡的 BPF 是它的老祖宗。docker profile 那個 JSON 是 libseccomp / OCI 的高階描述，最後被編譯成這段 cBPF。

### filter 能看到什麼、能回傳什麼

filter 的輸入是一個 `struct seccomp_data`（`include/uapi/linux/seccomp.h`）：

```c
struct seccomp_data {
    int   nr;                    // syscall number
    __u32 arch;                  // AUDIT_ARCH_X86_64 等，用來防 x86 vs x86_64 混淆攻擊
    __u64 instruction_pointer;
    __u64 args[6];               // syscall 的 6 個參數（暫存器值）
};
```

裁決碼（回傳值的高 16 bits，`include/uapi/linux/seccomp.h`）：

| 回傳值 | 效果 | 用在哪 |
|---|---|---|
| `SECCOMP_RET_KILL_PROCESS` | 整個 process 收到 `SIGSYS` 死掉 | 最嚴格，碰到就是攻擊，直接殺 |
| `SECCOMP_RET_KILL_THREAD` | 只殺發起的 thread（舊 `RET_KILL` 別名） | 較舊語意 |
| `SECCOMP_RET_TRAP` | 發 `SIGSYS`，process 可自己攔 handler | 想記錄/自訂處理 |
| `SECCOMP_RET_ERRNO` | syscall **不執行**，直接回一個 errno（如 `EPERM`） | 溫和擋——docker 擋危險 syscall 多用這個，程式以為權限不足而非直接崩 |
| `SECCOMP_RET_USER_NOTIF` | 通知一個 user-space supervisor 由它決定（5.0+） | 進階：supervisor 代做 syscall，用於 gVisor 之類 |
| `SECCOMP_RET_TRACE` | 交給 ptrace 的 tracer 決定 | 配合 tracer |
| `SECCOMP_RET_LOG` | 執行但記 log | 開發期觀察用哪些 syscall |
| `SECCOMP_RET_ALLOW` | 放行，正常執行 | 白名單裡的 syscall |

實務策略幾乎都是**白名單**：預設 `RET_KILL` 或 `RET_ERRNO`，只對明確列出的 syscall `RET_ALLOW`。黑名單很難寫對（漏一個就破功）。

### 底層機制：filter 掛在哪、什麼時候跑

seccomp 狀態掛在 `task_struct` 上（`include/linux/sched.h` 的 `struct task_struct` 裡的 `struct seccomp seccomp` 欄位，定義在 `include/linux/seccomp.h` 的 `struct seccomp`）：

```
task_struct
  └── struct seccomp seccomp
        ├── int mode                 // DISABLED / STRICT / FILTER
        └── struct seccomp_filter *filter   // 一條「filter 鏈」的頭
                 │
                 ▼ ->prev
        每次 prctl 加新 filter 就串一個上去，
        所有 filter 都會跑，取「最嚴格」的裁決
```

**filter 只會加不會減**，而且會被 `fork`/`clone` 繼承、被 `execve` 保留——這是安全設計：一個 process 不能透過 exec 一個新程式來甩掉沙盒。加 filter 前必須 `PR_SET_NO_NEW_PRIVS`（禁止之後透過 setuid 提權），否則非特權 process 不准設 filter（防止用 seccomp 繞過 setuid 語意）。

執行時機串起 Ch 4：syscall 從 user space 進來，走到 `do_syscall_64`（`arch/x86/entry/common.c`）**之前**，entry 程式碼會檢查 `_TIF_SECCOMP` 這個 thread flag。有設就進 `__secure_computing()`（`kernel/seccomp.c`），它呼叫 `__seccomp_filter()` 跑整條 filter 鏈：

```
  syscall 指令 (user)
        │
        ▼
  syscall entry (arch/x86/entry/entry_64.S → do_syscall_64)
        │  檢查 thread_info flags
        ▼
  _TIF_SECCOMP 有設？──否──► 直接 do_syscall_64，正常執行
        │是
        ▼
  __secure_computing()  (kernel/seccomp.c)
        │
        ▼
  __seccomp_filter()    ← 逐條跑 seccomp_filter 鏈，每條是一段 cBPF
        │                  用 seccomp_run_filters() 執行 BPF，取最嚴裁決
        ▼
   裁決 = ?
     ├─ RET_ALLOW  ──► 繼續 do_syscall_64（真的執行）
     ├─ RET_ERRNO  ──► 不執行，syscall 回傳 -errno
     ├─ RET_TRAP   ──► 送 SIGSYS 給自己
     └─ RET_KILL_* ──► do_exit(SIGSYS)，process/thread 死
```

關鍵源碼落點（`kernel/seccomp.c`）：`__seccomp_filter()` 是總入口，`seccomp_run_filters()` 逐條跑 cBPF 拿裁決，`seccomp_prepare_filter()` / `seccomp_attach_filter()` 處理 `prctl(PR_SET_SECCOMP)` / `seccomp(2)` 時把 user 傳來的 `sock_fprog` 編譯掛上去。因為每次 syscall 都要跑一遍 filter，它必須快——這也是為什麼用能靜態驗證、不含迴圈的 cBPF，而不是放任意程式。

## namespace：換一組「你看到的世界」

### 八種 namespace，各隔離一類資源

到 v6.12 有八種 namespace。記住每種隔離「哪個名詞」：

| namespace | CLONE flag | 隔離什麼 | 關鍵結構 / 檔案 |
|---|---|---|---|
| **mount** | `CLONE_NEWNS` | mount 樹（各自的檔案系統掛載視圖） | `struct mnt_namespace`，`fs/namespace.c` |
| **PID** | `CLONE_NEWPID` | PID 編號空間（容器裡的 PID 1） | `struct pid_namespace`，`kernel/pid_namespace.c` |
| **network** | `CLONE_NEWNET` | 網路堆疊：介面、路由、iptables、port | `struct net`，`net/core/net_namespace.c` |
| **user** | `CLONE_NEWUSER` | uid/gid 映射（容器 root ↔ 宿主普通 user） | `struct user_namespace`，`kernel/user_namespace.c` |
| **UTS** | `CLONE_NEWUTS` | hostname、domainname | `struct uts_namespace`，`kernel/utsname.c` |
| **IPC** | `CLONE_NEWIPC` | System V IPC、POSIX message queue | `struct ipc_namespace`，`ipc/namespace.c` |
| **cgroup** | `CLONE_NEWCGROUP` | cgroup 根視圖（見不到自己之外的 cgroup 樹） | `struct cgroup_namespace`，`kernel/cgroup/namespace.c` |
| **time** | `CLONE_NEWTIME` | `CLOCK_MONOTONIC`/`BOOTTIME` 偏移（5.6+） | `struct time_namespace`，`kernel/time/namespace.c` |

`CLONE_NEWNS` 那個 `NS` 是歷史命名——mount namespace 是**第一個** namespace（2002 年），當時就叫 "namespace"，所以搶到了 `NEWNS` 這個名字。後來的每種都得加自己的字。

### 底層機制：nsproxy 是那個「掛勾板」

每個 task 透過 `task_struct->nsproxy`（`include/linux/nsproxy.h` 的 `struct nsproxy`）指向它所屬的各 namespace。注意 **user namespace 和 PID namespace 不在 nsproxy 裡**——user ns 掛在 `task->cred->user_ns`（Ch 47，因為它跟權限綁在一起），PID ns 透過 `task->thread_pid` / `struct pid` 間接取得（見下）：

```
task_struct A ─┐
task_struct B ─┼──► struct nsproxy  (可共享！引用計數)
task_struct C ─┘         ├── mnt_namespace   *mnt_ns
                         ├── uts_namespace   *uts_ns
                         ├── ipc_namespace   *ipc_ns
                         ├── net             *net_ns
                         ├── time_namespace  *time_ns
                         └── cgroup_namespace *cgroup_ns
                         
task_struct A
   ├── cred ──► user_namespace     ← user ns 在這（Ch 47 cred）
   └── thread_pid ──► struct pid ──► 各層的 pid_namespace  ← PID ns 在這（Ch 9）
```

`struct nsproxy` **可被多個 task 共享**（有 `count` 引用計數）。同一個容器裡的多個 process 共用一個 nsproxy——這就是為什麼它們看到同一份 mount 樹、同一個網路。只有當某個 task `unshare` 或 `setns` 改了其中某個 ns，才會 copy 出一份新的 nsproxy。

**PID namespace 的多層設計**接 Ch 9：`struct pid`（`include/linux/pid.h`）不是一個數字，而是一個 `struct upid numbers[]` 陣列，每層 namespace 各有一個 number。所以宿主看到的 PID 4001，在容器的 PID namespace 裡可能是 1。`task_struct` 存的是 `struct pid *`，要哪一層的號碼由「從哪個 namespace 問」決定（`pid_nr_ns()`）。這正是「容器裡的 PID 1」的實作——它在自己那層是 1，在宿主那層是別的數字。

**PID namespace 有層級（有父子）**，其他多數 namespace 是平的。容器裡再開容器，PID ns 就疊一層，內層的 PID 在外層每一層都各有一個 number。而 PID 1 在自己的 ns 裡有特殊語意：它是那層的 init，若它死了，整個 PID namespace 裡的 process 都被殺（接 Ch 3 的 init 語意）。

### 三個動作：clone / setns / unshare

namespace 只有三種操作方式，對應三個 syscall：

**`clone(CLONE_NEW*)`（建立 + 進入）**：接 Ch 10 的 `copy_process`。fork 一個新 task 的同時，對每個 `CLONE_NEW*` flag 建一個新 namespace，新 task 直接進去。`copy_process()` 會呼叫 `copy_namespaces()`（`kernel/nsproxy.c`），它檢查有沒有任何 `CLONE_NEW*`，有的話 `create_new_namespaces()` 對每種需要新建的 ns 呼叫其 `copy_*_ns()`（`copy_mnt_ns`、`copy_pid_ns`、`copy_net_ns`…），組出一個新 nsproxy 掛到新 task 上。這是 runc 建容器主 process 的方式。

**`setns(fd, nstype)`（進入既有的）**：把「當前 task」加入 fd 指向的既有 namespace。fd 通常來自 `open("/proc/<pid>/ns/net")` 之類。`nsenter` 就是它——`docker exec` 進一個跑著的容器靠這個。

**`unshare(CLONE_NEW*)`（脫離現有的）**：不 fork，讓「當前 task」離開現有 namespace、進到新建的。`unshare(1)` 命令與 `unshare(2)` syscall 同名。它呼叫 `unshare_nsproxy_namespaces()`（`kernel/nsproxy.c`）。注意 `CLONE_NEWPID` 的 unshare 有個微妙處：unshare PID ns **不會**把當前 process 移進去，而是讓它**下一個 fork 的子** process 成為新 PID ns 的 PID 1（因為一個 process 的 PID 在它活著時不能變）。

`/proc/<pid>/ns/` 下每個 namespace 是一個特殊 symlink，內容像 `net:[4026531840]` 那個數字是 inode 號，**同號 = 同一個 namespace**。兩個 process 的 `/proc/<pid>/ns/net` 指向同一個 inode，就代表它們在同一個 network namespace。`lsns` 就是掃這個。

### user namespace：容器 root 的真相，也是攻擊面的來源

user namespace 值得單獨講，因為它既是 rootless 容器的關鍵，也是一大票 LPE 的入口。

核心機制是 **uid/gid 映射**（`struct uid_gid_map`，`kernel/user_namespace.c`）。一個 user ns 裡的 uid 0（root）**映射到**外層某個非特權 uid（比如宿主的 uid 100000）。你在容器裡 `whoami` 是 root、能 `apt install`、能 chown 容器內的檔案——因為在**這個 user ns 內**你對映射範圍內的 uid 有 capability。但一旦操作跨出 ns（碰宿主的檔案、真正的硬體），kernel 看的是**映射後**的真實 uid，你就是那個沒權限的普通 user。映射寫在 `/proc/<pid>/uid_map` / `gid_map`。

這帶來兩個後果：

1. **rootless 容器**：普通 user 不用 sudo 就能跑「容器內的 root」，Podman rootless、`unshare -U` 的基礎。
2. **攻擊面爆炸（接 `kernel_pwn`/`oscp`）**：一旦非特權 user 能 `CLONE_NEWUSER`，他就在自己的 user ns 裡**擁有 capabilities**（`CAP_NET_ADMIN`、`CAP_SYS_ADMIN` 對 ns 內資源）。這讓原本要 root 才能觸及的 kernel 程式碼——各種 netfilter、mount、fs 的 setup 路徑——暴露給普通 user 去戳。史上一票 CVE（如 OverlayFS、nftables、各種 use-after-free）都是「非特權 user 靠 user ns 進到某個過去以為只有 root 會走的路徑」。

所以有些發行版（早期 Debian/RHEL）**預設關掉或限制**非特權 user ns：`sysctl kernel.unprivileged_userns_clone`（Debian 補丁）或 `user.max_user_namespaces`。這是安全與功能的直接取捨——關了 user ns，很多沙盒（Chrome、Flatpak、rootless podman）就得改用 setuid helper。Ubuntu 24.04 進一步用 AppArmor（Ch 48）限制未特權 user ns 能做什麼，算是折衷。

## 容器三支柱：把 Ch 48/49/50 拼起來

現在把安全這一 Part 拼完整。**沒有「container」這個 kernel 物件**，容器是這幾樣東西的組合：

```
                    docker run / runc 底下發生什麼
   ┌──────────────────────────────────────────────────────────────┐
   │                        一個「容器」                             │
   │                                                                │
   │   ┌── namespace（Ch 49，本章）── 隔離「看到什麼」──────────┐    │
   │   │  mount/PID/net/user/UTS/IPC/cgroup/time            │    │
   │   │  clone(CLONE_NEW*) 建，pivot_root 換根，看不到宿主   │    │
   │   └──────────────────────────────────────────────────┘    │
   │                                                                │
   │   ┌── cgroup v2（Ch 50）──── 限制「用多少」──────────────┐    │
   │   │  CPU / memory / io / pids 上限，超了被 throttle/OOM │    │
   │   └──────────────────────────────────────────────────┘    │
   │                                                                │
   │   ┌── seccomp（Ch 49）+ LSM（Ch 48）── 限制「能做什麼」──┐    │
   │   │  seccomp profile 擋危險 syscall；AppArmor/SELinux   │    │
   │   │  policy 框住檔案/能力；cap-drop 砍掉多餘 capability  │    │
   │   └──────────────────────────────────────────────────┘    │
   └──────────────────────────────────────────────────────────────┘

   runc 建容器的動作序列（簡化）：
   clone(CLONE_NEWNS|NEWPID|NEWNET|NEWUSER|NEWUTS|NEWIPC)  ← Ch 49
     → 設 uid_map/gid_map                                   ← Ch 49 user ns
     → mount / pivot_root 到 container rootfs               ← Ch 33 mount ns
     → 掛進 cgroup（寫 cgroup.procs）                        ← Ch 50
     → 套 seccomp filter + 設 no_new_privs                  ← Ch 49
     → 套 AppArmor/SELinux label, 丟掉多餘 capabilities     ← Ch 47/48
     → execve(容器裡的程式)
```

這就是 `docker` 課裡「容器是什麼」的 kernel 版答案。你在那門課下的 `--cap-drop`、`--security-opt seccomp=profile.json`、`--userns-remap`、`--network none`、`--pid host`，每一個都對應本章或鄰章的一個 kernel 機制。

## 動手一：用 unshare 手搓一個迷你容器

不裝 docker，用 util-linux 的 `unshare` 直接拼。先看 namespace 隔離的效果：

```bash
# -U user ns, -r 把自己映射成容器裡的 root, -n new net ns, -m new mount ns
# --fork --pid 讓新 PID ns 生效（unshare PID 要 fork 出子才是 PID 1）
sudo unshare --user --map-root-user --net --mount --uts --ipc \
             --pid --fork --mount-proc /bin/bash
```

（若你的發行版允許非特權 user ns，去掉 `sudo`，`unshare -Urnm ...` 就能以普通 user 跑。）進去之後：

```bash
# 在迷你容器裡
hostname mini-container       # 改 hostname 不影響宿主（UTS ns）
echo $$                       # bash 的 PID → 應該是 1 或很小（PID ns）
ps aux                        # 只看到 ns 內的 process（因為 --mount-proc 重掛了 /proc）
id                            # uid=0(root)——但這是 user ns 映射出來的假 root
ip link                       # 只有 lo，沒有宿主的 eth0（net ns）
cat /proc/self/uid_map        # 看映射：0 <宿主真實uid> 1
```

開**另一個終端**，從宿主看這個 process 的真相：

```bash
# 找到那個 bash 的宿主 PID（不是容器內的 1）
ps aux | grep unshare
lsns                          # 列出所有 namespace 和誰在裡面
ls -l /proc/<host_pid>/ns/    # 看它的各 ns symlink 的 inode 號
```

你會發現：容器裡自稱 PID 1 的那個 bash，在宿主是個普通的大 PID；容器裡自稱 root，宿主看到的是你原本的 uid。**幻覺就是這麼來的。**

再示範 `nsenter`（`docker exec` 的原理）——從宿主鑽進那個容器的 namespace：

```bash
sudo nsenter --target <host_pid> --mount --uts --net --pid /bin/bash
# 現在你「進」了那個迷你容器，看到跟它一樣的 hostname/net/pid 視圖
```

## 動手二：寫一段 seccomp-BPF 擋掉一個 syscall

不用 libseccomp，直接手寫 cBPF filter，看得清楚每一步。這段 filter 白名單放行常見 syscall，但把 `write` 換成回 `EPERM`（示範 `RET_ERRNO`），並把 `execve` 直接 `RET_KILL`：

```c
// mini_seccomp.c  —  gcc mini_seccomp.c -o mini_seccomp
#include <stdio.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <sys/prctl.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <sys/syscall.h>

// 一段 cBPF：載入 nr → 比對 → 決定裁決
static int install_filter(void)
{
    struct sock_filter filter[] = {
        // 載入 seccomp_data.nr（syscall number）
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                 (offsetof(struct seccomp_data, nr))),

        // if nr == execve → KILL（跳到最後的 KILL）
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_execve, 3, 0),
        // if nr == write  → ERRNO(EPERM)
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_write, 1, 0),
        // 其它一律 ALLOW
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),

        // write → 回 EPERM（不執行）
        BPF_STMT(BPF_RET | BPF_K,
                 SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
        // execve → 殺掉整個 process
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    };
    struct sock_fprog prog = {
        .len = sizeof(filter) / sizeof(filter[0]),
        .filter = filter,
    };
    // 加 filter 前必須關掉「之後還能提權」，否則非特權裝不了
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) { perror("no_new_privs"); return 1; }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) { perror("seccomp"); return 1; }
    return 0;
}

int main(void)
{
    if (install_filter()) return 1;

    // 這行 write 會被 filter 轉成 EPERM——但注意：write 被擋了，
    // printf 走 write 也會失敗，所以我們用回傳碼觀察
    ssize_t r = write(STDOUT_FILENO, "hi\n", 3);
    // r 應該是 -1, errno == EPERM

    // 這行 execve 會讓 process 直接被 SIGSYS 殺掉
    execl("/bin/echo", "echo", "should-not-print", (char *)NULL);

    return 0;   // 到不了這裡
}
```

跑它：

```bash
gcc mini_seccomp.c -o mini_seccomp
./mini_seccomp
echo "exit code: $?"     # 應看到 159（128+SIGSYS，SIGSYS=31）
# 或用 strace 看它在哪一步被擋
strace -f ./mini_seccomp
#   ... write(1, "hi\n", 3) = -1 EPERM
#   ... execve(...) 之後 process 被 SIGSYS killed
dmesg | tail            # RET_KILL 可能留下 seccomp 的 audit 訊息
```

觀察重點：`write` 被擋成 `EPERM`（程式以為權限不足，不知道是 seccomp），而 `execve` 讓 process **直接死**。這就是 docker 預設 profile 的兩種手法——溫和的用 errno，危險的直接殺。

用 gdb（Ch 0 的環境）驗證 kernel 側：在 QEMU 裡跑這支程式，host gdb `break __seccomp_filter`，你會看到每次 syscall 都停一次，`print sd->nr` 看當前是哪個 syscall number，`finish` 看 `__seccomp_filter` 回傳的裁決碼。

> **想用生產級寫法**：實務不會手擼 cBPF，而是用 **libseccomp**（`seccomp_init` / `seccomp_rule_add` / `seccomp_load`），它幫你處理架構差異、syscall number 查找、filter 最佳化。docker 的 seccomp profile 那個 JSON 最後就是被翻成 libseccomp 呼叫。手寫這版是為了看清底層，正式環境用 libseccomp。

## 對比與取捨

| 機制 | 隔離/限制什麼 | 顆粒度 | 繞過難度 | 典型用途 |
|---|---|---|---|---|
| **seccomp-BPF** | 能發哪些 syscall | syscall + 參數 | 一旦裝上、no_new_privs，繼承到子與 exec，難甩 | 縮小攻擊面：瀏覽器、容器、systemd |
| **namespace** | 看到哪份資源視圖 | 八類資源各一 | 隔離視野非權限；user ns 反而擴大攻擊面 | 容器化、rootless 沙盒 |
| **capabilities（Ch 47）** | root 全能拆成的細權限 | 每種 capability | drop 掉就真的沒了 | 砍掉容器不需要的特權 |
| **LSM/MAC（Ch 48）** | 由 policy 強制的存取控制 | 物件 + 動作，連 root 也框 | policy 綁死，root 也繞不過 | SELinux/AppArmor 系統級 policy |
| **cgroup（Ch 50）** | 用多少資源 | CPU/mem/io/pids | 超額被 throttle/OOM | 資源上限、避免吵鄰居 |

它們**互補而非替代**。一個 hardened 容器五種全上：namespace 隔視野、cgroup 限量、cap-drop 砍特權、seccomp 擋 syscall、SELinux/AppArmor 兜底。少一個都留破口——比如只有 namespace 沒 user ns 隔離，容器 root 就是宿主 root，一次 mount 逃逸就 game over。

## 踩雷集錦

1. **「seccomp-BPF 用的是 eBPF」——錯**。它是 **classic BPF（cBPF）**，Ch 52 的 eBPF 是它後來的擴充。seccomp filter 不能掛 map、不能跑迴圈、必須靜態驗證會終止，因為它在**每次 syscall** 的熱路徑上跑。名字撞名而已。

2. **「namespace 能隔離就等於安全」——錯**。namespace 隔離的是**視野不是權限**。特別是沒開 user namespace 時，容器裡的 uid 0 **就是**宿主的 uid 0——一次容器逃逸（mount 一個 host 路徑、寫 `/proc/sys`）就直接拿到宿主 root。真正的隔離要 user ns 把容器 root 映射成宿主非特權 user。

3. **「unshare 了 PID ns，當前 shell 就變成 PID 1」——錯**。一個 process 活著時 PID 不能變。`unshare(CLONE_NEWPID)` 只讓**下一個 fork 的子**進到新 PID ns 當 1，所以 `unshare` 命令要配 `--fork`。忘了加，`/proc` 掛法會亂、`ps` 看起來不對。

4. **「加 seccomp filter 前不用 no_new_privs」——非特權會直接失敗**。`PR_SET_SECCOMP` 對非特權 process 要求先 `PR_SET_NO_NEW_PRIVS`，否則回 `EACCES`。這是防止用 seccomp 繞過 setuid 語意的設計，不是可選的。

5. **「filter 擋了 write，程式還能 printf 報錯」——不行**。你擋掉的 syscall 連你自己的錯誤輸出都用不了（printf 走 write）。所以動手二裡我們靠回傳碼/strace 觀察，而不是靠程式自己印。寫 filter 白名單時，別忘了把 process 正常運作需要的 syscall（`write`、`exit_group`、`rt_sigreturn`…）也放行，否則它連正常結束都做不到。

6. **「user namespace 一定安全所以到處開」——它同時是最大攻擊面**。user ns 讓非特權 user 在自己的 ns 裡有 capabilities，暴露大量原本 root-only 的 kernel 路徑。這就是為什麼一堆發行版限制它，也是 `kernel_pwn`/`oscp` 裡最愛的 LPE 起手式。開它是功能與攻擊面的直接取捨。

## 進階：再往深一層

- **seccomp user notification（`RET_USER_NOTIF`，5.0+）**：filter 不自己裁決，而是把 syscall「轉發」給一個 user-space supervisor，由它決定甚至**代替**目標做這個 syscall（透過 `SECCOMP_IOCTL_NOTIF_*`）。gVisor、systemd 的部分沙盒靠這個做「syscall 攔截後在 user space 重新實作」。是 seccomp 從「擋」進化到「代理」的關鍵。

- **時間 namespace 的巧妙**（`kernel/time/namespace.c`）：它不 copy 一份時鐘，而是存一個**偏移量**，讀 `CLOCK_MONOTONIC`/`BOOTTIME` 時加上去。這樣容器 checkpoint/restore（CRIU）搬到另一台機器，`clock_gettime` 讀到的單調時鐘不會突然跳。實作用 VDSO 資料頁做，讀時鐘不進 kernel。

- **network namespace 的成本**：建一個 net ns 要初始化整套網路堆疊（loopback、路由表、netfilter），不便宜。這是為什麼大量短命容器的場景，net ns 建立/銷毀會成為瓶頸，也是 `docker` 課裡 `--network host`（不建新 net ns，直接用宿主的）比較快的原因。

- **面試常問**：「容器和 VM 差在哪？」——VM 虛擬化硬體、跑獨立 kernel；容器共用宿主 kernel，用 namespace + cgroup + seccomp/LSM 在**同一顆 kernel 內**做隔離。所以容器逃逸 = 攻擊宿主 kernel（本課 Part 9 全部相關），而 VM 逃逸要打 hypervisor。「docker 底層是什麼 syscall？」——`clone`（建 ns）、`setns`（exec 進容器）、`unshare`、`mount`/`pivot_root`、`prctl`/`seccomp`，加上寫 cgroup 的 sysfs 檔。

- **PID namespace 與 zombie 回收**：容器 PID 1 有回收孤兒 process 的責任（reap zombie），跟宿主的 init 一樣（Ch 3）。若容器主程序不會 reap（很多應用不設計成 init），會累積 zombie——這就是 `docker run --init` 塞一個 tini 當 PID 1 的原因。

## 動手練習

1. **gdb 追 seccomp 裁決**：在 QEMU 裡跑動手二的程式，host gdb `break __seccomp_filter`，用 `print *sd` 看 `struct seccomp_data`，確認每次 syscall 的 `nr`，並在 `write`/`execve` 那次看 filter 回傳的裁決碼。對照「哪個 syscall 拿到哪個裁決」。

2. **gdb 追 namespace 建立**：`break copy_namespaces`（`kernel/nsproxy.c`），在 QEMU 裡跑 `unshare -Un /bin/true`，看 `clone_flags` 有沒有 `CLONE_NEWNET`，`step` 進 `create_new_namespaces` 看它對每種 ns 呼叫哪個 `copy_*_ns`。

3. **弄壞它——白名單漏 exit**：把動手二的 filter 改成白名單只放行 `write`，其它全 `RET_KILL`。跑跑看——程式連 `exit_group` 都被殺，觀察它怎麼死。這示範「白名單漏一個必要 syscall」的後果。

4. **證明幻覺**：用動手一開兩個終端，一個在迷你容器裡、一個在宿主。對同一個 process，記錄容器內 `echo $$` 和宿主 `ps` 看到的 PID、容器內 `id` 和宿主看到的真實 uid，並比對兩邊 `/proc/<pid>/ns/pid` 的 inode 號證明它們在不同 PID ns。

5. **user ns 的 capability 幻覺**：非特權跑 `unshare -Ur`，進去後 `capsh --print`（Ch 47）看你在 ns 內有滿手 capability；然後試著對宿主的檔案（如 `/etc/shadow`）動手，看它為什麼還是被擋——因為跨出 ns 後 kernel 看的是映射後的真實 uid。

## 本章重點整理

- **seccomp** 在 syscall entry（Ch 4，`do_syscall_64` 之前）攔截，用一段 **cBPF**（非 eBPF）filter 看 syscall number + 參數，回 KILL/ERRNO/TRAP/ALLOW；filter 只增不減、繼承到子與 exec、要 `no_new_privs`。源碼在 `kernel/seccomp.c` 的 `__seccomp_filter()`。
- **namespace** 換 `task->nsproxy`（`kernel/nsproxy.c`）指向的一組 ns，隔離的是「看到什麼」；八種各隔離一類資源；user ns 在 `cred`、PID ns 在 `struct pid` 的多層 number（Ch 9），其餘在 nsproxy。`clone` 建、`setns` 進、`unshare` 脫離。
- **user namespace** 把容器 root 映射成宿主非特權 user——rootless 容器的基礎，也是大量 LPE 的攻擊面來源（接 `kernel_pwn`/`oscp`），所以常被發行版限制。
- **容器 = namespace（隔視野）+ cgroup（Ch 50 限量）+ seccomp/LSM（Ch 48 限行為）**，沒有「container」這個 kernel 物件；這就是 docker/runc 底層（接 `docker` 課）。

## 自我檢核

- [ ] 不看筆記，能說出 seccomp 攔在 syscall 流程的哪個點、與 `do_syscall_64` 的先後
- [ ] 能解釋 seccomp-BPF 的「BPF」和 Ch 52 的 eBPF 差在哪、為什麼用 cBPF
- [ ] 能講出 `RET_KILL` / `RET_ERRNO` / `RET_ALLOW` 各在什麼場景用（對照 docker profile）
- [ ] 能畫出 `task → nsproxy → 各 namespace`，並說出 user ns 和 PID ns 為什麼不在 nsproxy 裡
- [ ] 能解釋「容器裡的 PID 1」怎麼靠 `struct pid` 的多層 number 實作（接 Ch 9）
- [ ] 面試被問「docker 底層用哪些 syscall / 容器與 VM 差在哪」，能不查書答出來
- [ ] 能說清 user namespace 為什麼同時是 rootless 容器的基礎和 LPE 的攻擊面
- [ ] 能獨立用 `unshare -Urnm` 拼出迷你容器，並從宿主用 `lsns`/`nsenter` 觀察/進入它

## 延伸閱讀

### 官方文件

- **[Documentation/userspace-api/seccomp_filter.rst](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)**
  - **讀哪裡**：整篇。這是 seccomp-BPF 的權威說明——`seccomp_data` 結構、所有 `RET_*` 裁決碼、`no_new_privs` 要求、user notification 都在這
  - **和本章的關聯**：動手二的 filter 就照這篇的規則寫；寫生產 filter 前務必讀「Caveats」一節

- **`man 7 namespaces`、`man 7 user_namespaces`、`man 2 clone` / `setns` / `unshare`**
  - **讀哪裡**：`namespaces(7)` 給八種 ns 全覽，`user_namespaces(7)` 講 uid_map/gid_map 的映射規則（本章 user ns 那節的完整版）
  - **為什麼權威**：Michael Kerrisk 維護，是 namespace 語意最精確的參考，`unshare --fork` 那個 PID ns 陷阱這裡講得最清楚

### LWN 深文

- **[Namespaces in operation](https://lwn.net/Articles/531114/)** — Michael Kerrisk，LWN 系列（共七篇）
  - **讀哪裡**：從第一篇（overview）開始，每篇一種 namespace，配 C 範例
  - **能學到什麼**：本章「先建立直覺」到「三個動作」的完整展開版，是理解 namespace 最好的免費資源，每種 ns 都有可跑的程式

- **[A seccomp overview](https://lwn.net/Articles/656307/)** — Jake Edge，LWN
  - **讀哪裡**：整篇，補上 seccomp 的歷史脈絡（strict → BPF → user notif 的演進）與設計取捨
  - **前提**：讀完本章 seccomp 兩節再讀，會更懂為什麼是 cBPF、為什麼要 no_new_privs

### 對照 docker 課 / 攻擊面

- **[runc / OCI runtime spec](https://github.com/opencontainers/runtime-spec/blob/main/config.md)** — Open Container Initiative
  - **讀哪裡**：`config.md` 的 `namespaces`、`linux.seccomp`、`user`（uid/gid mappings）幾節
  - **和本章的關聯**：這是 docker 底下 runc 吃的設定格式，本章每個 kernel 機制在這裡對應一個 JSON 欄位——把「kernel 視角」和「docker 課的使用者視角」對起來

- **《Container Security》** — Liz Rice（O'Reilly, 2020）
  - **這本書的定位**：從 namespace/cgroup/capability/seccomp 逐個機制講容器隔離與逃逸，正是本章 + Ch 47/48/50 的系統化整合版
  - **注意**：偏 ops/security 視角，kernel 源碼細節仍以本章與 kernel 文件為準

seccomp 擋行為、namespace 隔視野，容器三支柱還差最後一根——**限制「用多少」資源**。下一章我們拆 cgroup v2：CPU、memory、io、pids 的上限怎麼在 kernel 裡實作、超額時 throttle 與 OOM 怎麼觸發，把容器底層徹底補完。

→ [Ch 50 cgroup v2 實作](./50-cgroup-v2.md)
