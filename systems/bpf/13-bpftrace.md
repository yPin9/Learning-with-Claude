# Ch 13 — bpftrace：動態腳本語言

> **目標**：掌握 bpftrace 的完整語法——probe type、builtin 變數、map、control flow、I/O——能寫出解決實際問題的 one-liner 和多行腳本。

> **環境**：bpftrace 0.18+，Ubuntu 22.04，kernel 5.15+。

## 為什麼需要這個？

你不會每次要觀測系統就去寫 libbpf C 程式——那太慢了。bpftrace 讓你在 30 秒內寫出一個 one-liner，觀測某個系統行為，得到答案，繼續前進。

bpftrace 的定位是「shell scripting 等級的 BPF 工具」——不需要編譯，不需要框架，不需要 userspace 程式。對於 ad-hoc 的問題，它是最快的答案。

> bpftrace 用 LLVM 在 runtime 把你的腳本編譯成 BPF bytecode。它需要 kernel BTF 支援（`/sys/kernel/btf/vmlinux` 必須存在）才能使用 kernel struct 的 field。

## 先建立直覺：bpftrace 的心智模型

```
bpftrace 腳本的結構：

probe_spec / probe_spec / ... {
    filter  /* 可選的 if-like filter */
    actions /* 要做的事 */
}

每個規則說的是：
  「當 probe_spec 觸發時，如果 filter 為 true，執行 actions」

和 awk 很像：
  awk '/pattern/ { action }' file
  bpftrace 'probe_spec / filter / { action }'
```

## Probe 類型

### `tracepoint:category:name`

```bash
# 所有 execve 呼叫
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_execve { printf("%s\n", comm); }'

# 查看 tracepoint 的參數
sudo bpftrace -lv 'tracepoint:syscalls:sys_enter_openat'
# 輸出：
# tracepoint:syscalls:sys_enter_openat
#     int __syscall_nr
#     int dfd
#     const char * filename
#     int flags
#     umode_t mode
```

### `kprobe:function` / `kretprobe:function`

```bash
# vfs_read 的入口
sudo bpftrace -e 'kprobe:vfs_read { printf("pid=%d\n", pid); }'

# vfs_read 的出口，看回傳值
sudo bpftrace -e 'kretprobe:vfs_read { printf("ret=%d\n", retval); }'

# 查看可用的 kprobe 目標
sudo bpftrace -l 'kprobe:vfs_*'
```

### `uprobe:binary:function` / `uretprobe`

```bash
# 追蹤 libc 的 malloc
sudo bpftrace -e 'uprobe:/lib/x86_64-linux-gnu/libc.so.6:malloc { printf("size=%lu\n", arg0); }'

# 追蹤 Python 程式裡的函式（需要 debug symbols 或 USDT）
sudo bpftrace -e 'uprobe:/usr/bin/python3:PyEval_EvalFrameEx { printf("frame called\n"); }'
```

### `usdt:binary:probe_name`

USDT（User Statically Defined Tracepoint），適合有內建 tracepoint 的程式（如 MySQL、PostgreSQL、Python）：

```bash
# PostgreSQL 的 query 開始 probe（需要 PostgreSQL 編譯時有 --enable-dtrace）
sudo bpftrace -e 'usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start {
    printf("query: %s\n", str(arg0));
}'
```

### `hardware:event` / `software:event`

```bash
# 每 100 個 CPU cache miss 採樣一次
sudo bpftrace -e 'hardware:cache-misses:100 { @[stack] = count(); }'

# 每 1000 個 page fault 採樣
sudo bpftrace -e 'software:page-faults:1000 { @[comm] = count(); }'
```

### `profile:hz:N` / `interval:s:N`

```bash
# 每秒 99 次 CPU 採樣（profile）
sudo bpftrace -e 'profile:hz:99 { @cpu[cpu] = count(); }
                  interval:s:5 { print(@cpu); clear(@cpu); exit(); }'

# 每秒定時觸發（用於定期輸出或清理）
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_read { @cnt++; }
interval:s:1 { printf("%d reads/sec\n", @cnt); @cnt = 0; }'
```

### `BEGIN` / `END`

```bash
# 腳本開始和結束時執行
sudo bpftrace -e 'BEGIN { printf("start\n"); }
                  END   { printf("end\n"); }'
```

## 內建變數（Builtins）

| 變數 | 型別 | 說明 |
|---|---|---|
| `pid` | int | 目前 process 的 PID（POSIX PID = TGID）|
| `tid` | int | 目前 thread 的 TID |
| `uid` / `gid` | int | real UID / GID |
| `comm` | string | process 名稱（最長 16 chars）|
| `cpu` | int | 目前 CPU 核心 id |
| `nsecs` | int | 從 boot 以來的 nanoseconds |
| `elapsed` | int | 從 bpftrace 啟動以來的 nanoseconds |
| `retval` | int | kretprobe / uretprobe 的回傳值 |
| `arg0..arg9` | int | 函式的第 0–9 個參數（kprobe 用）|
| `args` | struct | tracepoint 的參數 struct |
| `stack` | string | kernel call stack |
| `ustack` | string | userspace call stack |
| `probe` | string | 目前觸發的 probe 名稱 |
| `curtask` | int | struct task_struct * |
| `rand` | int | 隨機數 |
| `cgroup` | int | 目前 cgroup v2 id |

## Maps：bpftrace 的資料儲存

bpftrace 的 map 用 `@` 符號表示，類似 awk 的 associative array：

### 計數和聚合

```bash
# @name = value（全域 map）
# @name[key] = value（帶 key 的 map）

# 計數：每個 comm 的 read 次數
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_read { @reads[comm]++; }'

# 加總：每個 process 讀取的 bytes
sudo bpftrace -e 'kretprobe:vfs_read { @bytes[comm] += retval; }'

# 最大值
sudo bpftrace -e 'kretprobe:vfs_read /retval > 0/ { @max[comm] = max(retval); }'

# 平均值
sudo bpftrace -e 'kretprobe:vfs_read /retval > 0/ { @avg_size = avg(retval); }'
```

### 直方圖

```bash
# 線性直方圖（lhist：linear histogram）
sudo bpftrace -e 'kretprobe:vfs_read /retval > 0/ {
    @dist = lhist(retval, 0, 65536, 4096);
}'

# 對數直方圖（hist：powers-of-2 buckets）
sudo bpftrace -e 'kretprobe:vfs_read /retval > 0/ {
    @dist = hist(retval);
}'
# 輸出：
# @dist:
# [0, 1]              142 |@@@@@@@@                                     |
# [2, 4)              847 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
# [4, 8)              521 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@               |
# ...
```

### Stack 聚合（Flamegraph）

```bash
# 收集 CPU profile，按 stack 統計（用於 flamegraph）
sudo bpftrace -e 'profile:hz:99 { @[stack, comm] = count(); }
                  interval:s:30 { print(@); exit(); }' > /tmp/out.bt

# 用 flamegraph.pl 生成 flamegraph
cat /tmp/out.bt | /path/to/FlameGraph/stackcollapse-bpftrace.pl | \
    /path/to/FlameGraph/flamegraph.pl > flame.svg
```

## Control Flow

### Filter（`/ condition /`）

```bash
# 只追蹤特定 pid
sudo bpftrace -e 'kprobe:vfs_read /pid == 1234/ { printf("read!\n"); }'

# 只追蹤特定 comm
sudo bpftrace -e 'kprobe:vfs_read /comm == "nginx"/ { printf("nginx read\n"); }'

# 複合條件
sudo bpftrace -e 'kprobe:vfs_write /uid == 0 && comm != "kworker"/ { @cnt++; }'
```

### 條件判斷（`if / else`）

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_read {
    if (args->count > 4096) {
        printf("large read: pid=%d count=%d\n", pid, args->count);
    } else {
        @small++;
    }
}'
```

### Unroll

```bash
# 固定次數的 loop（展開）
sudo bpftrace -e '
BEGIN {
    $sum = 0;
    unroll(5) {
        $sum += 1;
    }
    printf("sum = %d\n", $sum);  // 輸出 5
}'
```

## Strings 和指標

```bash
# 讀取 kernel 字串
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat {
    printf("open: %s\n", str(args->filename));
}'

# 讀取 userspace 字串（uptr 表示 userspace pointer）
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat {
    printf("open: %s\n", str(args->filename));
}'

# 從 struct 讀取 field（需要 BTF）
sudo bpftrace -e 'kprobe:vfs_read {
    printf("comm=%s\n", ((struct task_struct *)curtask)->comm);
}'
```

## 計時模式

```bash
# 測量函式執行時間（us）
sudo bpftrace -e '
kprobe:vfs_read    { @start[tid] = nsecs; }
kretprobe:vfs_read /@ start[tid]/ {
    @latency_us = hist((nsecs - @start[tid]) / 1000);
    delete(@start[tid]);
}'
```

## 常用 One-liners 精選

```bash
# 追蹤所有 new process 建立
sudo bpftrace -e 'tracepoint:sched:sched_process_exec { printf("%s (%d)\n", comm, pid); }'

# 追蹤 TCP 連線建立（到哪個 port）
sudo bpftrace -e 'kprobe:tcp_connect {
    $sk = (struct sock *)arg0;
    printf("%-16s → %s:%d\n", comm,
           ntop($sk->__sk_common.skc_daddr),
           $sk->__sk_common.skc_dport >> 8);
}'

# 找出哪個 process 在寫 /etc/passwd（或任何路徑）
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_openat /str(args->filename) == "/etc/passwd"/ {
    printf("pid=%d comm=%s flags=0x%x\n", pid, comm, args->flags);
}'

# 測量每個 process 的 read latency 分布
sudo bpftrace -e '
kprobe:vfs_read    { @s[tid] = nsecs; }
kretprobe:vfs_read /@s[tid]/ {
    @ms[comm] = hist((nsecs - @s[tid]) / 1000000);
    delete(@s[tid]);
}'

# Off-CPU 分析：誰被排開了，為什麼
sudo bpftrace -e '
tracepoint:sched:sched_switch /prev_state != 0/ {
    printf("%s → %s (state=%d)\n",
           args->prev_comm, args->next_comm, args->prev_state);
}'

# 列出最耗 CPU 的 kernel stack
sudo bpftrace -e '
profile:hz:99 { @[kstack] = count(); }
interval:s:10 { print(@); clear(@); exit(); }'
```

## 腳本模式（`.bt` 檔案）

對於複雜邏輯，把腳本存成 `.bt` 檔案：

```bash
# latency.bt — 測量 vfs_read 延遲並 histogram
#!/usr/bin/env bpftrace

BEGIN {
    printf("Tracing vfs_read latency. Ctrl+C to stop.\n");
}

kprobe:vfs_read {
    @start[tid] = nsecs;
}

kretprobe:vfs_read
/@start[tid]/
{
    $lat_us = (nsecs - @start[tid]) / 1000;
    @latency_us = hist($lat_us);
    delete(@start[tid]);
}

END {
    printf("\nLatency distribution (us):\n");
    print(@latency_us);
}
```

```bash
sudo bpftrace latency.bt
```

## 踩雷集錦

1. **`str(ptr)` 讀到空字串或亂碼**：可能是 ptr 是 userspace 位址；確認用 `str(args->...)` 讀取 tracepoint 的字串參數，而不是 `str(args->ptr)` 加 dereference

2. **`@map[key]++` 但 key 是 string 時很慢**：string key 的 hash 比 integer 慢；如果 cardinality 高（很多不同的 string），map lookup 會是瓶頸；考慮換成 integer key（如 pid）

3. **BTF struct 存取需要 `/sys/kernel/btf/vmlinux`**：如果 `bpftrace -e 'kprobe:... { printf("%s", ((struct task_struct *)curtask)->comm); }'` 報錯，先確認 vmlinux BTF 存在

4. **`profile:hz:99` 而不是 `:100`**：99 是質數，避免和某些固定頻率的事件（如 100 Hz 的 timer）重疊，導致採樣結果有偏差

5. **Ctrl+C 時 map 自動被 print**：bpftrace 在 `END` block（或 Ctrl+C 觸發 END）後自動輸出所有 `@` map；如果不想要這個行為，在 `END` 裡 `clear(@map)` 清掉

## 動手練習

1. 用 bpftrace 找出在過去 10 秒內，哪些 process 呼叫了 `openat` 且 filename 包含 `.conf`（提示：bpftrace 支援 `==` 比較 string，但不支援 regex；用 `strncmp` 或 filter substring）

2. 寫一個 latency histogram，測量 `sys_enter_read` 到 `sys_exit_read` 的時間，按 process 名稱分群（`@latency[comm] = hist(...)`），執行 10 秒後退出

3. 用 `profile:hz:99 { @[kstack] = count(); }` 收集 5 秒的 CPU profile，把輸出 pipe 給 flamegraph.pl 生成 SVG

## 本章重點整理

- bpftrace 是 BPF 的腳本語言，適合 ad-hoc 的系統觀測，不需要編譯或框架
- probe type 決定 attach point；builtin 變數提供 context；`@` map 儲存聚合資料
- Filter（`/ condition /`）做條件過濾；`hist()`、`lhist()`、`count()` 做聚合
- 腳本模式（`.bt` 檔案）適合複雜邏輯；BEGIN/END 做初始化和清理

## 自我檢核

- [ ] 能不看文件寫出「追蹤特定 process 的所有 read syscall 並統計 byte 數」的 one-liner
- [ ] 知道 `kprobe:func` 和 `tracepoint:syscalls:sys_enter_*` 各自的優缺點
- [ ] 能解釋 `@[stack, comm] = count()` 的語意（雙 key map）
- [ ] 知道為什麼 CPU profiling 用 99 Hz 而不是 100 Hz

## 延伸閱讀

### 書籍

- **《BPF Performance Tools》** — Brendan Gregg（Ch 5：bpftrace 工具）
  - **讀哪幾章**：Ch 5 是 bpftrace 的系統性介紹；Ch 6–17 每章都有大量 bpftrace one-liner 範例
  - **定位**：本課教語法；Brendan Gregg 的書教「用 bpftrace 解決什麼問題」

### 官方文件

- **[bpftrace reference guide](https://github.com/bpftrace/bpftrace/blob/master/docs/reference_guide.md)**
  - **讀哪裡**：整份；作為 reference 查閱；probe types 和 builtin functions 那兩節最重要
  - **學什麼**：所有 probe type、builtin、map function、語法的完整規格

### 部落格

- **[bpftrace one-liners tutorial](https://www.brendangregg.com/blog/2019-01-01/learn-ebpf-tracing.html)** — Brendan Gregg, 2019
  - **這篇說什麼**：12 個 one-liner，從最簡單到複雜；是學 bpftrace 最好的入門文章
  - **讀哪裡**：整篇；把每個 one-liner 都跑一遍
  - **為什麼值得讀**：Gregg 是 bpftrace 的核心推動者；他的例子都是解決實際問題的

→ [Ch 14 BCC：Python-kernel 雙語框架](./14-bcc.md)
