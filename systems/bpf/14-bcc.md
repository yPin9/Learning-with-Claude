# Ch 14 — BCC：Python-kernel 雙語框架

> **目標**：理解 BCC 的架構（Python front-end + kernel-side C）、能用 BCC 的 Python API 寫完整的 tracing 工具、知道 BCC 和 libbpf 的架構差異以及為什麼現代新工具優先選 libbpf。

> **環境**：BCC 0.24+（`apt install bpfcc-tools python3-bpfcc`），Ubuntu 22.04。

## 為什麼需要這個？

BCC 是 eBPF 工具生態系最早成熟的框架（2015 年），目前仍然有大量的工具和教材用 BCC 寫成。在學 libbpf 之前先學 BCC，有兩個原因：

1. **可讀性高**：BCC 的 Python API 比 libbpf 的 C API 直觀得多，適合快速 prototype
2. **大量現成工具**：`/usr/sbin/*-bpfcc` 裡有 80+ 個即用的 tracing 工具，了解它們的原始碼是學習 eBPF 應用的好方式

不過，BCC 也有嚴重的缺點（在本章末尾說明），這也是為什麼新工具越來越多地用 libbpf 實作。

## 先建立直覺：BCC 的架構

```
你的 BCC 程式（Python）
       │
       │  BPF(text="...C code...")  ← 在 runtime 編譯 kernel-side C
       ▼
   Python front-end
       │  呼叫 libclang 編譯 kernel-side C
       │  管理 map fd 和 attach
       ▼
   Kernel BPF program（JIT）
       │
       └── 寫入 maps ──▶ Python 讀取 maps ──▶ 輸出結果
```

BCC 的核心概念：kernel-side 的 C code 是一個 **runtime 編譯的 string**，嵌在你的 Python 程式裡。這讓你不需要預先編譯，但代價是每次啟動都要重新編譯（1–2 秒的啟動 overhead）。

## 最小 BCC 範例

```python
#!/usr/bin/env python3
# hello_bcc.py — 追蹤 execve 呼叫
from bcc import BPF

# kernel-side C code（runtime 編譯成 BPF bytecode）
BPF_PROG = """
#include <uapi/linux/ptrace.h>

int trace_execve(struct pt_regs *ctx) {
    char comm[TASK_COMM_LEN];
    bpf_get_current_comm(&comm, sizeof(comm));
    bpf_trace_printk("execve: %s\\n", comm);
    return 0;
}
"""

# 建立 BPF 物件（這裡會觸發 runtime 編譯）
b = BPF(text=BPF_PROG)

# attach 到 kprobe（execve 的 kernel 函式是 do_execveat_common 或 sys_execve）
b.attach_kprobe(event="sys_execve", fn_name="trace_execve")
# 或用 tracepoint（更穩定）：
# b.attach_tracepoint(tp="syscalls:sys_enter_execve", fn_name="trace_execve")

print("Tracing execve... Ctrl+C to stop")
b.trace_print()  # 讀取 /sys/kernel/debug/tracing/trace_pipe 並輸出
```

```bash
sudo python3 hello_bcc.py
# 輸出：
# b'          bash-12345 [000] .... execve: ls'
# b'          bash-12346 [000] .... execve: cat'
```

## 用 BPF Maps 傳輸資料（推薦方式）

`bpf_trace_printk` 效能差，生產程式用 maps 傳輸資料：

```python
#!/usr/bin/env python3
# execve_map.py — 用 map 傳輸事件
from bcc import BPF
import ctypes as ct

BPF_PROG = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

/* 定義事件結構（在 kernel-side 和 Python-side 共享）*/
struct data_t {
    u32 pid;
    u64 ts;
    char comm[TASK_COMM_LEN];
    char filename[64];
};

/* 宣告 perf event array map */
BPF_PERF_OUTPUT(events);

int trace_execve(struct tracepoint__syscalls__sys_enter_execve *ctx) {
    struct data_t data = {};
    data.pid = bpf_get_current_pid_tgid() >> 32;
    data.ts  = bpf_ktime_get_ns();
    bpf_get_current_comm(&data.comm, sizeof(data.comm));
    bpf_probe_read_user_str(&data.filename, sizeof(data.filename), ctx->filename);
    events.perf_submit(ctx, &data, sizeof(data));
    return 0;
}
"""

# data_t 的 Python 版本（用 ctypes 對應）
class Event(ct.Structure):
    _fields_ = [
        ("pid",      ct.c_uint),
        ("ts",       ct.c_ulonglong),
        ("comm",     ct.c_char * 16),
        ("filename", ct.c_char * 64),
    ]

b = BPF(text=BPF_PROG)
b.attach_tracepoint(tp="syscalls:sys_enter_execve", fn_name="trace_execve")

def handle_event(cpu, data, size):
    event = ct.cast(data, ct.POINTER(Event)).contents
    print(f"pid={event.pid} comm={event.comm.decode()} exec={event.filename.decode()}")

b["events"].open_perf_buffer(handle_event)

print("Tracing execve. Ctrl+C to stop.")
while True:
    try:
        b.perf_buffer_poll()
    except KeyboardInterrupt:
        break
```

## BCC 的 Map API

BCC 提供了豐富的 Python API 來操作 BPF maps：

```python
# kernel-side 宣告 map（BCC macro）
BPF_PROG = """
BPF_HASH(pid_count, u32, u64);        /* hash map */
BPF_ARRAY(syscall_stats, u64, 512);   /* array map */
BPF_HISTOGRAM(latency_us);            /* histogram */
BPF_PERF_OUTPUT(events);              /* perf event array */

/* kernel-side 使用 */
int probe(void *ctx) {
    u32 key = bpf_get_current_pid_tgid() >> 32;
    u64 zero = 0, *val = pid_count.lookup_or_try_init(&key, &zero);
    if (val) (*val)++;

    latency_us.increment(bpf_ktime_get_ns() / 1000);
    return 0;
}
"""

b = BPF(text=BPF_PROG)
# ...attach...

# Python-side 讀取 map
for k, v in sorted(b["pid_count"].items(),
                   key=lambda kv: -kv[1].value)[:10]:
    print(f"pid={k.value} count={v.value}")

# 讀取 histogram
b["latency_us"].print_log2_hist("latency (us)")

# 清空 map
b["pid_count"].clear()
```

## 分析現有 BCC 工具的原始碼

最好的學習方式是讀現有的 BCC 工具。它們都在 `/usr/share/bcc/tools/` 或 GitHub：

**execsnoop**（追蹤新 process）：

```python
# /usr/share/bcc/tools/execsnoop
# 核心 BPF 邏輯（簡化版）
BPF_PROG = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>
#include <linux/fs.h>

struct data_t {
    u32 pid;
    u32 ppid;
    char comm[TASK_COMM_LEN];
    int retval;
};

BPF_PERF_OUTPUT(events);
BPF_HASH(tasks, u32, struct data_t);

int syscall__execve(struct pt_regs *ctx,
    const char __user *filename, ...)
{
    struct data_t data = {};
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    data.pid  = bpf_get_current_pid_tgid() >> 32;
    data.ppid = task->real_parent->tgid;
    bpf_get_current_comm(&data.comm, sizeof(data.comm));
    tasks.update(&data.pid, &data);
    return 0;
}
"""
# execsnoop 的完整版本還有 kretprobe 取回傳值，以及 args 解析
```

**biolatency**（block I/O latency histogram）：

```bash
sudo /usr/sbin/biolatency-bpfcc
# 輸出：
# Tracing block device I/O... Hit Ctrl+C to end.
# ^C
# usecs               : count     distribution
# 0 -> 1              : 0        |                      |
# 2 -> 3              : 0        |                      |
# 4 -> 7              : 34       |@@@@@@@@              |
# 8 -> 15             : 189      |@@@@@@@@@@@@@@@@@@@@@@|
# ...
```

## BCC 的限制（為什麼新工具選 libbpf）

| 問題 | 說明 |
|---|---|
| **啟動 overhead** | 每次執行都要 runtime compile（1–3 秒）；生產環境不適合 |
| **依賴 kernel headers** | BCC 需要 `/usr/src/linux-headers-$(uname -r)`；部署到生產機器需要安裝 kernel headers |
| **沒有 CO-RE** | BCC 的 kernel-side C 在目標機器上編譯，不需要 CO-RE；但這意味著不能預先編譯分發 binary |
| **記憶體 overhead** | BCC 把整個 LLVM 帶進去；程式的記憶體 footprint 很大 |
| **Python 依賴** | 需要 Python 環境和 BCC Python bindings |

相比之下，libbpf 工具預先編譯、有 CO-RE、不需要 kernel headers、啟動快、記憶體小。

**BCC 的適合場景**：快速 prototype、一次性的分析工具、教學。

**不適合場景**：需要分發給客戶部署的工具、對啟動時間敏感的工具、在生產 kernel 上沒有 kernel headers 的環境。

## 踩雷集錦

1. **BCC 的 C macro（`BPF_HASH`、`BPF_PERF_OUTPUT`）和 libbpf 的 map 定義不相容**：BCC 用自己的 macro 宏，不能直接用 `struct { __uint(type, ...) } SEC(".maps")` 語法；換框架時要完全重寫 map 宣告

2. **`bpf_trace_printk` 輸出有限制**：最多 3 個參數，且 string 不能超過一定長度；用 `BPF_PERF_OUTPUT` 才是正確的做法

3. **`attach_kprobe(event="sys_execve", ...)` 在新 kernel 可能找不到**：新 kernel 的 syscall 函式可能是 `do_sys_openat2` 而不是 `sys_openat`；改用 tracepoint（`attach_tracepoint(tp="syscalls:sys_enter_execve", ...)`）更穩定

4. **ctypes 結構和 BPF struct 的 alignment 不一致**：Python 的 ctypes 有自己的對齊規則；如果你的 BPF struct 和 Python ctypes 結構不一致，讀到的值是錯的。在 BPF struct 裡用 `__packed__` 或在 Python 用 `_pack_ = 1`

5. **`b.perf_buffer_poll()` 只處理一批事件就返回**：需要在 while loop 裡持續呼叫；如果 polling timeout 太長，會有事件 delay

## 動手練習

1. 用 BCC 寫一個 `tcp_connect` tracer：當任何 process 建立 TCP 連線時，輸出 `comm dst_ip:dst_port`（提示：kprobe `tcp_connect`，從 `struct sock *` 讀取 `skc_daddr` 和 `skc_dport`）

2. 讀懂 `/usr/sbin/opensnoop-bpfcc` 的原始碼（`/usr/share/bcc/tools/opensnoop`），說出：
   - kernel-side 追蹤了哪些欄位？
   - 怎麼把 enter 和 exit 的資料對應起來（用什麼 key）？
   - filename 怎麼讀取（哪個 bpf_probe_read variant）？

3. 比較 `execsnoop-bpfcc` 和 libbpf 版本的 execsnoop（如果你的系統有 `execsnoop` 工具的話）的啟動時間：用 `time sudo execsnoop-bpfcc -h` 和 `time sudo execsnoop -h`

## 本章重點整理

- BCC 用 Python front-end + runtime 編譯的 kernel-side C；kernel-side 邏輯是 string 嵌在 Python 程式裡
- `BPF_PERF_OUTPUT` + ctypes struct 是 BCC 傳輸事件的標準方式
- BCC 適合快速 prototype；不適合需要分發、低 overhead 的生產工具
- 現有的 BCC 工具（`/usr/sbin/*-bpfcc`）是學習 eBPF 應用場景的寶庫

## 自我檢核

- [ ] 能解釋 BCC 的啟動 overhead 來自哪裡（runtime 編譯），以及為什麼 libbpf 沒有這個問題
- [ ] 知道 `BPF_PERF_OUTPUT` 在 Python 側需要哪些步驟（register callback, poll loop）
- [ ] 能說出 BCC 和 libbpf 的 map 宣告語法差異
- [ ] 知道為什麼 BCC 不需要 CO-RE（在目標機器上 runtime 編譯）

## 延伸閱讀

### 書籍

- **《BPF Performance Tools》** — Brendan Gregg（Addison-Wesley, 2019）
  - **讀哪幾章**：Ch 4（BCC 工具參考）；整本書的 Part 2–5 是各種 BCC 工具的使用說明
  - **定位**：本書是 BCC 時代的標準教材；現在仍然是 tracing 工具箱的好參考，只是 code style 偏老

### 官方文件

- **[BCC Python developer guide](https://github.com/iovisor/bcc/blob/master/docs/reference_guide.md)**
  - **讀哪裡**：Maps（BPF_HASH, BPF_ARRAY, etc.）和 Events（BPF_PERF_OUTPUT）那兩節
  - **學什麼**：所有 BCC macro 的完整說明和 Python API

### 原始碼

- **[BCC tools directory](https://github.com/iovisor/bcc/tree/master/tools)**
  - **讀哪裡**：`execsnoop.py`、`opensnoop.py`、`tcpconnect.py`、`biolatency.py`
  - **學什麼**：這幾個是 BCC 工具的標準範例；結構清晰，值得直接讀 source

→ [Ch 15 libbpf：現代 C 開發框架](./15-libbpf.md)
