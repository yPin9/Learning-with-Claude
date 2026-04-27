# Ch 7 — /proc 完整漫遊

> 目標：把 `/proc/PID/` 下重要檔案逐一看過，知道每個檔案是 kernel 哪個資料的視窗，能從 `/proc` 直接讀出 strace 看不到的事。

## /proc 是什麼

一個**檔案系統**，但不是真的 disk，是 kernel 即時生成的「process / system 資訊讀取介面」。每讀一次都是現場 query。

```bash
mount | grep proc
# proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)
```

兩大部分：

- `/proc/<PID>/` — 每個 process 一個資料夾
- `/proc/<其他>` — 系統全域資訊（meminfo, cpuinfo, cmdline, ...）

## /proc/PID/ 重要檔案總覽

```bash
ls /proc/$$/    # 看你 shell 自己
```

```
attr/         cmdline       fdinfo/      maps         oom_score_adj    smaps     status
auxv          comm          gid_map      mem          pagemap          stack     syscall
cgroup        coredump_filter limits     mountinfo    personality      stat      task/
clear_refs    cwd           loginuid     mounts       projid_map       statm     timers
cmdline       environ       map_files/   net/         root             status    timens_offsets
comm          exe           mounts       ns/          schedstat        syscall   wchan
...
```

我會挑最常用的 15 個：

| 檔案 | 內容 | 何時看 |
|---|---|---|
| `cmdline` | argv | 看 process 是怎麼啟動的 |
| `comm` | process name (15 char) | 給 ps / top 用 |
| `status` | 統計（state, mem, uid, ...） | 一站式快速看 |
| `stat` | 同上但機器格式 | script 解析 |
| `statm` | memory 統計 | 看記憶體 |
| `maps` | virtual memory layout | 看 .so / heap / stack 在哪 |
| `smaps` | maps 加每段詳細 | 看誰吃記憶體 |
| `fd/` | 開的 file descriptor | 看開了什麼 |
| `fdinfo/` | 每個 fd 的 detail（offset, flag） | 看 fd 內部狀態 |
| `cwd` | symlink 到 current working dir | 看在哪 |
| `exe` | symlink 到 binary | 看跑哪個 |
| `environ` | 環境變數 | 看 export 了什麼 |
| `stack` | kernel stack | 看卡在哪個 kernel function |
| `syscall` | 目前停在哪個 syscall | 看阻塞在哪 |
| `wchan` | kernel 等待的 function 名 | 簡化版 stack |

## cmdline / comm / exe

```bash
cat /proc/$$/cmdline | tr '\0' ' '; echo
# bash

readlink /proc/$$/exe
# /usr/bin/bash

cat /proc/$$/comm
# bash
```

`cmdline` 用 NUL 分隔 argv，要 `tr '\0' ' '` 看。

`/proc/PID/exe` 是 symlink，**即使 binary 被刪都還在**（kernel 對 inode 持引用）。災難復原時可以這樣 dump：

```bash
cp /proc/PID/exe /tmp/recovered_binary
```

## status / stat / statm

```bash
cat /proc/$$/status | head -20
```

```
Name:	bash
Umask:	0022
State:	S (sleeping)
Tgid:	12345
Ngid:	0
Pid:	12345
PPid:	12340
TracerPid:	0
Uid:	1000	1000	1000	1000
Gid:	1000	1000	1000	1000
FDSize:	256
Groups:	1000 100
NStgid:	12345
NSpid:	12345
NSpgid:	12345
NSsid:	12345
VmPeak:	   12345 kB
VmSize:	   12340 kB
...
```

關鍵欄位：

- **State**：R / S / D / T / Z / X（Ch 2 講過）
- **TracerPid**：誰在 trace 我（0 = 沒人）
- **Uid / Gid**：四個值是 real / effective / saved / fs
- **VmRSS**：實際在 RAM 的記憶體（Resident Set Size）
- **VmSize**：virtual memory 總量

`stat` 是同樣資訊但**機器可讀格式**：

```bash
cat /proc/$$/stat
# 12345 (bash) S 12340 12345 ...
```

每個欄位意義在 `man proc(5)`。多數工具直接 parse 這個。

## maps：虛擬記憶體佈局

```bash
cat /proc/$$/maps | head -20
```

```
55b...000-55b...000 r--p 00000000 fd:00 12345  /usr/bin/bash
55b...000-55b...000 r-xp 00027000 fd:00 12345  /usr/bin/bash
55b...000-55b...000 r--p 0010c000 fd:00 12345  /usr/bin/bash
55b...000-55b...000 rw-p 00135000 fd:00 12345  /usr/bin/bash
55b...000-55b...000 rw-p 00000000 00:00 0      [heap]
7f...000-7f...000   r--p 00000000 fd:00 67890  /usr/lib/libc.so.6
7f...000-7f...000   r-xp 00027000 fd:00 67890  /usr/lib/libc.so.6
...
7ffd...000-7ffd...000 rw-p 00000000 00:00 0    [stack]
7fff...000-7fff...000 r--p 00000000 00:00 0    [vvar]
7fff...000-7fff...000 r-xp 00000000 00:00 0    [vdso]
ffffffffff600000-ffffffffff601000 --xp 00000000 00:00 0  [vsyscall]
```

每行一段 mapping：

```
位址範圍              perms   offset    dev   inode  路徑
55b000-55b150         r-xp    27000     fd:00 12345  /usr/bin/bash
```

permissions：r/w/x 加 p (private) 或 s (shared)。

特殊區域：
- `[heap]` — malloc 的 heap
- `[stack]` — main thread 的 stack
- `[vdso]` / `[vvar]` — kernel mapping 給 vDSO
- `[vsyscall]` — 老的 syscall 機制（少用）
- `anon`（沒路徑）— 通常是 mmap 出來的記憶體

**SIGSEGV 時對照看**：core dump 中的 fault address 在 maps 哪段，知道是踩 stack / heap / lib 哪邊。

## smaps：誰吃記憶體

`smaps` 比 `maps` 詳細，每段加上：

```bash
cat /proc/$$/smaps | head -20
```

```
55b...000-55b...000 r--p 00000000 fd:00 12345  /usr/bin/bash
Size:                 36 kB
KernelPageSize:        4 kB
MMUPageSize:           4 kB
Rss:                  36 kB
Pss:                   8 kB     ← Proportional Set Size
Shared_Clean:         28 kB
Shared_Dirty:          0 kB
Private_Clean:         8 kB
Private_Dirty:         0 kB
Referenced:           36 kB
Anonymous:             0 kB
...
```

關鍵欄位：

- **Rss**：這段在 RAM 多少
- **Pss** (Proportional Set Size)：共享頁按比例算（4 個 process 共享 4MB → 每個算 1MB）
- **Private_Dirty**：自己改過的、不能 share 的 — **真正的「這 process 額外吃」的數字**

debug 記憶體用量看 PSS / Private_Dirty 比 RSS 準。

## fd/ + fdinfo/

```bash
ls -l /proc/$$/fd/
```

```
0 -> /dev/pts/0
1 -> /dev/pts/0
2 -> /dev/pts/0
255 -> /dev/pts/0
```

每個 symlink 指向 fd 對應的物件（檔案、socket、pipe、anon_inode...）。

`fdinfo/` 有更詳細：

```bash
cat /proc/$$/fdinfo/0
# pos:	0
# flags:	0102
# mnt_id:	28
# ino:	7
```

- `pos`：file offset（檔案讀到哪）
- `flags`：O_RDONLY/O_RDWR/O_NONBLOCK 等
- `mnt_id`：mount namespace
- `ino`：inode

特殊 fd 還會有更多 info（epoll 列被監聽的 fd、inotify 列被 watch 的 path...）。

## cwd / root

```bash
readlink /proc/$$/cwd
# /home/you

readlink /proc/$$/root
# /                     （chroot 過的 process 會看到不一樣）
```

## environ

```bash
cat /proc/$$/environ | tr '\0' '\n' | head
# PATH=/usr/bin:/usr/sbin
# HOME=/home/you
# ...
```

NUL 分隔。**敏感資訊（API key 等）放 env var 別人讀得到**（同 user）—— 安全考量。

## syscall / stack / wchan

debug 「process 卡住」最有用三個檔案：

```bash
cat /proc/PID/syscall
# 0 0x3 0x7ffe... 0x100 0x0 0x0 0x0 0x7ffe... 0x...
# 第一個 0 = syscall number (read)，後面是 args
```

目前停在哪個 syscall。0 = read，可以對到 `/usr/include/asm/unistd_64.h`。

```bash
cat /proc/PID/wchan
# do_select
```

kernel 等待的 function 名。簡化的 stack。

```bash
sudo cat /proc/PID/stack
# [<0>] do_select+0x...
# [<0>] core_sys_select+0x...
# [<0>] __x64_sys_select+0x...
# [<0>] do_syscall_64+0x...
# [<0>] entry_SYSCALL_64_after_hwframe+0x...
```

完整 kernel stack。需要 `CONFIG_STACKTRACE` 跟 root。

## /proc/PID/task/ — thread

multi-threaded process 在 `task/` 下每個 thread 一個資料夾：

```bash
ls /proc/PID/task/
# 12345  12346  12347  12348      ← 每個是 TID
```

每個 TID 資料夾結構同 `/proc/PID/`。`top -H -p PID` 等於遞迴看每個 task。

## 系統全域檔案

不在 PID 下的：

| 檔案 | 內容 |
|---|---|
| `/proc/cpuinfo` | CPU 詳細 |
| `/proc/meminfo` | RAM 全貌 |
| `/proc/loadavg` | 1/5/15 min load |
| `/proc/uptime` | 開機多久、idle 累計 |
| `/proc/diskstats` | 磁碟 IO 統計 |
| `/proc/mounts` | mount table |
| `/proc/swaps` | swap 區 |
| `/proc/modules` | 載入的 kernel module |
| `/proc/cmdline` | kernel boot cmdline |
| `/proc/sys/kernel/...` | 各種 kernel 參數（透過 sysctl） |
| `/proc/net/...` | 網路統計（tcp / udp / route...） |

```bash
free -h         # 等於 parse /proc/meminfo
uptime          # 等於 parse /proc/loadavg
ps aux          # 等於 walk /proc/*/stat
```

絕大多數 system 監控工具都是 /proc 的 wrapper。

## 一個常見踩雷：cat /proc/PID/mem

```bash
cat /proc/PID/mem
# I/O error
```

這檔案能讀但不能直接 cat — 要先 seek 到合法 address，再 read 限定長度。`gdb -p PID` 內部就是這樣讀 tracee 記憶體。

## 一個常見踩雷：環境變數讀不到

```bash
sudo cat /proc/1/environ
```

**root 才看得到別人的 environ**。同 user 的 OK。安全機制。

## 一個常見踩雷：/proc 看到的 RSS 跟 ps 看到的不一樣

ps 從 `/proc/PID/stat` 讀，更新有延遲。`top` / `htop` 直接讀 status，較即時。差幾 KB 是正常的。

## 動手練習

**1. 寫個 monitor**

```bash
PID=$(pgrep firefox | head -1)
watch -n 1 "cat /proc/$PID/status | grep -E '^(State|VmRSS|VmSize|Threads)'"
```

每秒看一次 firefox 的記憶體變化。

**2. 寫個 fd watcher**

```bash
PID=...
watch -n 1 "ls -l /proc/$PID/fd/ | wc -l"
```

看 fd 數量變化。穩定增加 = fd leak。

**3. 找 process 在等什麼**

```c
// stuck.c
#include <unistd.h>
int main() {
    sleep(60);
}
```

```bash
gcc stuck.c -o stuck
./stuck &
PID=$!
cat /proc/$PID/syscall   # 看 syscall number
cat /proc/$PID/wchan     # 看 wchan
sudo cat /proc/$PID/stack  # 看 kernel stack
kill $PID
```

**4. 看自己的 maps**

```bash
sleep 100 &
cat /proc/$!/maps
```

對著看到的 lib，跑 `ldd /bin/sleep` 對照 — 完全一致。

**5. 看 fdinfo 的 epoll**

```c
#include <sys/epoll.h>
#include <fcntl.h>
#include <unistd.h>
int main() {
    int e = epoll_create1(0);
    int fd = open("/etc/passwd", O_RDONLY);
    struct epoll_event ev = { .events = EPOLLIN };
    epoll_ctl(e, EPOLL_CTL_ADD, fd, &ev);
    pause();
}
```

```bash
gcc ep.c -o ep
./ep &
cat /proc/$!/fdinfo/3   # 假設 epoll fd 是 3
# pos: 0
# flags: 02
# tfd:  4 events:    1 ...   <- watching fd 4
```

epoll 的 fdinfo 列被監聽的 fd。**抓 epoll 卡死神器**。

## 自我檢核

- [ ] 知道 cmdline / comm / exe 的差別
- [ ] 看 status 至少能解 5 個欄位（State, VmRSS, Uid, Threads, TracerPid）
- [ ] maps 跟 smaps 差別、PSS 怎麼算
- [ ] 用 fd/ + fdinfo/ debug 過 fd leak / epoll
- [ ] 知道 syscall / stack / wchan 三招挖卡死

下一章看 lsof — 等於跨 process 版的 fd/。

→ [Ch 8 lsof 與 fd 視角](./08-lsof-and-fd-view.md)
