# Ch 22 — USDT：userspace 靜態探針

> **目標**：理解 USDT（User Statically Defined Tracepoints）的工作原理——ELF 裡的 note section、semaphore 機制、在 Python/Java/Node.js/PostgreSQL 等常見 runtime 上的應用，以及如何用 libbpf 和 bpftrace 使用 USDT。

## 先建立直覺：USDT 是什麼？

USDT 是 userspace 程式的 tracepoint——程式開發者在 source code 裡插入靜態標記，讓觀測工具可以附加到這些預定義的觀測點上。

和 uprobe（動態 instrumentation，可以 attach 到任意函式）相比，USDT 的特點：
- **穩定的 API**：probe 的名稱和參數有語意保證，不會隨版本改變
- **Semaphore 保護**：只有在 probe 被 activate 時才執行收集 data 的 code，overhead 接近零
- **豐富的生態**：MySQL、PostgreSQL、Python、Ruby、OpenJDK 等都有 USDT probe

```
USDT 的工作原理：

程式的 ELF binary
  ├── .text（代碼段）
  │     包含 STAP_PROBE 或 SDT_NOTE 指令（通常是 nop 或短序列）
  └── .note.stapsdt（ELF note section）
        包含 probe 的 metadata：
        - probe name（provider:name）
        - 地址（程式運行後 ASLR 會偏移）
        - 參數型別和 register（argc, argtype:register 格式）

觀測工具 attach 時：
  1. 讀 .note.stapsdt 找到 probe 地址
  2. 在那個地址設置 uprobe（動態 breakpoint）
  3. probe 觸發時呼叫 BPF program
  4. semaphore（.probes section 裡的計數器）告訴程式有 consumer
```

## 用 bpftrace 使用 USDT

```bash
# 列出某個 binary 的所有 USDT probe
sudo bpftrace -l 'usdt:/usr/bin/python3:*'
# usdt:/usr/bin/python3:python:function__entry
# usdt:/usr/bin/python3:python:function__return
# usdt:/usr/bin/python3:python:import__find__load__start
# ...

# attach 到 Python function entry probe
sudo bpftrace -e '
usdt:/usr/bin/python3:python:function__entry {
    printf("filename=%s lineno=%d funcname=%s\n",
           str(arg0), arg1, str(arg2));
}'

# 跑一個 Python 程式觀察
python3 -c "def foo(): pass; foo()"
# 輸出：filename=<string> lineno=1 funcname=foo
```

## PostgreSQL USDT Probe

PostgreSQL 有豐富的 USDT probe（需要用 `--enable-dtrace` 編譯）：

```bash
# 列出 PostgreSQL 的 probe
sudo bpftrace -l 'usdt:/usr/lib/postgresql/14/bin/postgres:*' 2>/dev/null | head -20
# usdt:...:postgresql:query__start
# usdt:...:postgresql:query__done
# usdt:...:postgresql:lock__wait__start
# ...

# 追蹤 slow queries（超過 100ms）
sudo bpftrace -e '
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start {
    @start[pid] = nsecs;
    @query[pid] = str(arg0);
}
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__done
/@start[pid]/
{
    $dur_us = (nsecs - @start[pid]) / 1000;
    if ($dur_us > 100000) {  /* 100ms */
        printf("SLOW QUERY [%d us]: %s\n", $dur_us, @query[pid]);
    }
    delete(@start[pid]);
    delete(@query[pid]);
}'
```

## 用 libbpf 使用 USDT

```c
/* BPF 程式（.bpf.c）*/
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/usdt.bpf.h>  /* libbpf 提供的 USDT macro */

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 64 * 1024);
} rb SEC(".maps");

SEC("usdt//usr/bin/python3:python:function__entry")
int trace_python_func(struct pt_regs *ctx)
{
    /* USDT 參數透過 BPF_USDT_ARG macro 存取 */
    long filename_ptr;
    bpf_usdt_arg(ctx, 0, &filename_ptr);

    char filename[64];
    bpf_probe_read_user_str(filename, sizeof(filename),
                            (void *)filename_ptr);

    bpf_printk("python func: %s\n", filename);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

```c
/* userspace（.c）*/
struct bpf_link *link = bpf_program__attach_usdt(
    prog,
    -1,                              /* pid = -1（所有 process）*/
    "/usr/bin/python3",              /* binary path */
    "python",                        /* provider */
    "function__entry",               /* probe name */
    NULL                             /* opts */
);
```

## USDT Semaphore

許多 USDT probe 有 semaphore 機制：只有在 probe 被 activate 的情況下，才執行收集 data 的 code（通常是構造 probe 的參數）：

```c
/* C source 裡的 USDT 用法（SDT macro）*/
#include <sys/sdt.h>

void handle_query(const char *sql)
{
    /* 如果沒有 consumer，這行是 nop；有 consumer 才執行 */
    DTRACE_PROBE2(postgresql, query__start, sql, getpid());

    /* ... 處理查詢 ... */
}
```

`DTRACE_PROBE2` 展開成：

```c
if (postgresql_query__start_semaphore > 0) {
    /* 組裝 probe 的參數（可能有 overhead）*/
    __asm__ volatile goto("nop" : : : : probe_label);
probe_label:;
}
```

當你 activate USDT probe 時，libbpf 把 semaphore 的值設成 1，程式才執行 probe 的 code。

## 查找 USDT Probe 的參數型別

```bash
# 用 readelf 看 .note.stapsdt section
readelf -n /usr/lib/postgresql/14/bin/postgres | grep -A 5 "stapsdt"
# ...
#   Provider: postgresql
#   Name: query__start
#   Location: 0x000000000012f3a4, Base: 0x0000000000000000, Semaphore: 0x00000000002a4210
#   Arguments: -8@%rdi

# -8@%rdi 表示：
# - 第一個參數（位置 0）
# - 大小 8 bytes，signed（負號）
# - 在 %rdi register 裡
```

用 bpftrace 查看參數型別（更方便）：

```bash
sudo bpftrace -lv 'usdt:/usr/bin/python3:python:function__entry'
# usdt:/usr/bin/python3:python:function__entry
#     char* arg0    (filename)
#     int   arg1    (lineno)
#     char* arg2    (funcname)
```

## 常見 Runtime 的 USDT Probe

| Runtime | Provider | 重要 probe |
|---|---|---|
| Python 3.6+ | `python` | `function__entry/return`, `import__find__load__start` |
| Ruby | `ruby` | `method__entry/return`, `gc__start/end` |
| OpenJDK | `hotspot` | `method__entry/return`, `gc__begin/end` |
| Node.js | `node` | `http__server__request`, `gc__start` |
| PostgreSQL | `postgresql` | `query__start/done`, `lock__wait__start` |
| MySQL | `mysql` | `query__start/done` |
| Nginx | `nginx` | `http__request` |
| PHP | `php` | `request__startup`, `function__entry/return` |

## 踩雷集錦

1. **USDT 需要 binary 編譯時有 SDT support**：不是所有 binary 都有 USDT；`readelf -n binary | grep stapsdt` 沒有輸出就是沒有；Ubuntu 的 Python 通常有，自編 binary 需要加 `--enable-dtrace`

2. **ASLR 讓 probe 地址在每次執行後改變**：libbpf 會自動處理（讀 `/proc/<pid>/maps` 計算偏移）；但你不能 hardcode probe 地址

3. **semaphore 地址也需要 ASLR 修正**：有 semaphore 的 probe，libbpf 同樣需要讀 maps 找到 semaphore 的運行時地址

4. **Docker container 裡的 binary path**：要用 container 的 binary path（`/proc/<pid>/root` 開頭的路徑）；或直接用 pid attach 而不是 path

5. **Java 的 USDT probe 需要 `-XX:+ExtendedDTraceProbes`**：Java 的 hotspot probe 預設不啟用；需要在 JVM 啟動時加上這個 flag

## 動手練習

1. 用 bpftrace attach 到 Python 的 `python:function__entry`，收集 30 秒內最常被呼叫的 function 名稱，統計 top 10

2. 如果你的系統有 PostgreSQL：attach 到 `postgresql:query__start/done`，測量每個 query 的執行時間，輸出超過 10ms 的 slow query

3. 用 `readelf -n /usr/bin/python3 | grep stapsdt` 找出所有 Python USDT probe 的 location 和參數型別，解釋每個參數的型別表示法（`-8@%rdi` 的意思）

## 本章重點整理

- USDT 是 userspace 程式的靜態 tracepoint，有穩定 API，semaphore 保護讓未啟用時 overhead 接近零
- `.note.stapsdt` ELF section 存放 probe metadata；libbpf 在 attach 時讀取並計算 ASLR 偏移
- 豐富的生態：Python、PostgreSQL、OpenJDK 等都有大量預定義 probe
- 比 uprobe 更穩定（不依賴函式地址），比 tracepoint 更靈活（userspace 自定義）

## 自我檢核

- [ ] 能解釋 USDT semaphore 的作用，以及它如何讓未啟用時 overhead 接近零
- [ ] 知道如何確認某個 binary 有沒有 USDT probe（readelf 或 bpftrace -l）
- [ ] 能說出 Python 的 `function__entry` probe 提供哪些參數

→ [Ch 23 perf_event 與 PMU 硬體計數器](./23-perf-event-pmu.md)
