# Ch 16 — 效能分析經典工具巡禮

> 目標：認識 bcc-tools / bpftrace-tools 上百個現成工具中最重要的十幾個，會用也會讀其原始碼 — 這是 BPF observability 的「標準軍火庫」。

## 工具集從哪來

兩大來源：

1. **bcc-tools**：`/usr/share/bcc/tools/`，Brendan Gregg 與 IO Visor 社群維護，~150 個 Python 工具
2. **bcc/libbpf-tools**：`/usr/sbin/*-bpfcc` 或從 source 編，CO-RE C 重寫版，~30 個

新版 distro 兩套都會裝。優先用 libbpf-tools 版（啟動快、開銷低）。

```bash
ls /usr/share/bcc/tools/ | wc -l
which execsnoop-bpfcc opensnoop-bpfcc 2>/dev/null
```

## 工具分類

按「觀察的子系統」分：

| 子系統 | 工具範例 |
|---|---|
| Process | execsnoop, exitsnoop, oomkill |
| File | opensnoop, statsnoop, filelife, filetop |
| Block IO | biolatency, biosnoop, biotop, bitesize |
| Memory | memleak, cachestat, slabratetop |
| Scheduler | runqlat, runqlen, runqslower, cpudist |
| Network | tcpconnect, tcpaccept, tcptracer, tcpretrans, tcptop, tcplife |
| Filesystem | ext4slower, xfsslower, btrfsdist, vfsstat |
| Application | dbslower (mysql/pg), mysqld_qslower |
| 語言 runtime | profile, offcputime, ucalls (java/python/etc) |

下面挑十幾個必會的逐個看。

## 1. execsnoop — 誰在啟動 process

```bash
sudo execsnoop-bpfcc
# PCOMM            PID    PPID   RET ARGS
# bash             12345  1234     0 /usr/bin/ls -la
# ls               12345  1234     0
# bash             12346  1234     0 /usr/bin/grep error log.txt
```

每秒幾乎零開銷。**找到「誰在 fork bomb」的第一招**。

## 2. opensnoop — 誰在開哪些檔

```bash
sudo opensnoop-bpfcc
# PID    COMM               FD ERR PATH
# 12345  bash               -1   2 /home/user/.inputrc
# 12345  bash                3   0 /etc/passwd
```

`-p PID` 只追特定 process。`-x` 只印失敗的（`ERR != 0`）— 找「為什麼這個程式找不到 config 檔」的神器。

## 3. biolatency — 磁碟 IO 延遲分布

```bash
sudo biolatency-bpfcc 10 1
# 每 10 秒印一次 histogram，1 次

#      usecs               : count     distribution
#         0 -> 1            : 0        |                                        |
#         2 -> 3            : 0        |                                        |
#       128 -> 255          : 12       |@@                                      |
#       256 -> 511          : 234      |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
#       512 -> 1023         : 89       |@@@@@@@@@@@@@                            |
#      1024 -> 2047         : 45       |@@@@@@@                                  |
#      2048 -> 4095         : 23       |@@@                                      |
```

看磁碟 IO 是否有 long tail（> 100ms 的 bucket 出現很多）— 比 `iostat` 平均值有用 100 倍。

## 4. tcpconnect / tcpaccept — TCP 連線觀測

```bash
sudo tcpconnect-bpfcc
# PID    COMM     IP SADDR              DADDR              DPORT
# 12345  curl      4 192.168.1.10       142.250.80.46      443
```

看「誰在發起 connection 到哪」 — 安全 / 容量規劃 / 找 leak 都用得到。

`tcpaccept` 是反向：看「誰被連」。

## 5. tcpretrans — TCP 重傳

```bash
sudo tcpretrans-bpfcc
# TIME     PID     IP LADDR:LPORT          T> RADDR:RPORT          STATE
# 14:23:45 0        4 10.0.0.1:443         R> 8.8.8.8:54321        ESTABLISHED
```

每行一次 retransmit。**極低開銷**，可以一直開著當 SLO 監控。

## 6. tcptop — TCP 流量 by process

```bash
sudo tcptop-bpfcc
# 14:23:45 loadavg: 0.5 0.3 0.2 1/123 4567
# PID    COMM         LADDR              RADDR              RX_KB  TX_KB
# 12345  nginx        :80                10.0.0.1:54321     234    1234
# 12346  postgres     :5432              10.0.0.2:33344     567    2345
```

像 `top` 但給 TCP 流量。**找哪個 process 在吃 bandwidth**。

## 7. tcplife — TCP connection lifecycle

```bash
sudo tcplife-bpfcc
# PID   COMM       LADDR              LPORT  RADDR              RPORT  TX_KB  RX_KB  MS
# 12345 curl       192.168.1.10       45678  142.250.80.46      443    1      8      234
```

每行是一個結束的 TCP connection — 包括時長、傳了多少。**最強的 connection-level observability 工具之一**。

## 8. runqlat / runqslower — Scheduler 延遲

```bash
sudo runqlat-bpfcc
#      usecs               : count     distribution
#         0 -> 1            : 0        |                                        |
#         8 -> 15           : 1234     |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
#        64 -> 127          : 234      |@@@@@@@                                  |
#      1024 -> 2047         : 12       |                                         |
#      8192 -> 16383        : 3        |                                         |  ← bad
```

CPU run queue 等待延遲分布。Histogram 末端有大值 = scheduler 過載 / CPU 不夠。

`runqslower` 印每個超過閾值的事件，更精準找哪個 process 受影響。

## 9. profile — CPU profiler with stack trace

```bash
sudo profile-bpfcc -F 99 30
# 99 Hz 採樣 30 秒，印呼叫堆疊聚合

# Sample count for thread "nginx":
#   __libc_recvfrom
#   nginx_event_loop
#   ngx_process_events
#   nginx_main_loop
#     1234
```

直接拿 stack trace 做 profiling — **不需要程式 rebuild、不需要 dynamic linking 改動**。配 `flamegraph.pl`：

```bash
sudo profile-bpfcc -F 99 -f 30 > stacks.txt
flamegraph.pl < stacks.txt > flame.svg
```

## 10. offcputime — Off-CPU 分析

```bash
sudo offcputime-bpfcc 10
# 看「process 不在 CPU 上的時間花在哪」

#     finish_task_switch
#     __schedule
#     schedule
#     futex_wait_queue_me
#     futex_wait
#     do_futex
#     SyS_futex
#     entry_SYSCALL_64_fastpath
#     pthread_cond_wait
#     - python3 (12345)
#         3456789
```

效能分析的另一半 — 一般 profile 看「在 CPU 上花時間」，offcputime 看「在等什麼」。**找 lock contention、IO wait 神器**。

## 11. memleak — Userspace memory leak detector

```bash
sudo memleak-bpfcc -p $(pgrep nginx) 10
# 每 10 秒 dump 還沒被 free 的 allocation
```

attach uprobe 到 `malloc` / `free` / `realloc`，計算「allocated but not freed」。**比 valgrind 開銷低 100 倍**，可以線上開。

## 12. ext4slower / xfsslower — 慢 FS 操作

```bash
sudo ext4slower-bpfcc 100
# 印 ext4 操作 > 100ms 的事件

# TIME     COMM           PID    T BYTES   OFF_KB   LAT(ms) FILENAME
# 14:23:45 postgres       12345  R 8192    0          234.5 /var/lib/.../base/16384/2619
```

每個 FS 一支：`ext4slower`、`xfsslower`、`btrfsslower`、`zfsslower`、`nfsslower`。

## 13. bashreadline — 看 user 在 bash 打了什麼

```bash
sudo bashreadline-bpfcc
# TIME     PID    COMMAND
# 14:23:45 12345  ls -la
# 14:23:48 12345  cd /tmp
# 14:23:50 12345  cat /etc/passwd
```

attach uprobe 到 bash 的 `readline` function。**安全 audit 用、看 user 在做什麼**。

## 14. argdist — 任意函式參數分布

```bash
# vfs_read 的 size 參數分布
sudo argdist-bpfcc -H 'p::vfs_read(struct file *file, char *buf, size_t count):size_t:count'
```

「任意 kernel function、任意參數，給我 histogram」 — 萬用工具。

## 怎麼讀這些工具的原始碼

挑一個小工具開始：

```bash
sudo less /usr/share/bcc/tools/execsnoop
```

每個工具的結構都類似：

```
1. 大段 BPF C 字串（kernel side）
2. argparse 處理參數
3. BPF(text=...) 載入
4. attach probe
5. perf_buffer 處理 + 印 / 統計
```

讀過 3 支你就掌握 pattern。

## 一個常見誤解

「這些工具都很完美、production-ready」 — **不全然**。

幾個常見問題：
- 老 kernel 上某些工具會壞掉（特別是用了新 helper 的）
- bcc 版啟動 3–5 秒，**密集 fork 之類場景測量過程影響觀測對象**
- profile 在 PID namespace 裡可能拿不到正確 stack trace
- 部分工具 attach kprobe 到的 function 在新 kernel 已被 inline / 改名 — 不會跑但也不會報錯

**用之前讀一下 source、用之後 sanity check 結果**。

## 動手練習

1. **跑 5 個工具**：execsnoop / opensnoop / tcpconnect / biolatency / runqlat — 每個 30 秒。
2. **profile + flamegraph**：找一個 CPU-intensive 程式，用 profile-bpfcc 抓 stack 做 flamegraph。
3. **挑一支小工具讀完 source**：建議從 execsnoop 開始（< 200 行）。
4. **用 argdist 自訂測量**：挑一個你好奇的 kernel function，量它的某個參數分布。

## 自我檢核

- [ ] 我能列出至少 8 個 bcc-tools 並說出各自用途
- [ ] 我能解釋 profile vs offcputime 的差別
- [ ] 我能用 tcplife 分析一段時間的 connection 行為
- [ ] 我能讀一支 bcc 工具的 source、認出 BPF + Python 結構
- [ ] 我能用 argdist 對任意 kernel function 做即時測量

下一章我們處理 user space observability — USDT 是怎麼讓 PostgreSQL、Python、JVM 提供穩定 hook 點的。

→ [Ch 17 USDT：觀察 user space 應用](./17-usdt.md)
