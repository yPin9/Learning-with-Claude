# 練習 F — 生產用 observability agent

> **目標**：整合 Part 7 所有進階知識，實作一個多 program 協同、涵蓋 CPU / 記憶體 / 網路 / 安全四個維度的生產等級 observability agent，展示 tail call、並發控制、timer、iterator、local storage 的綜合應用。

## 背景與動機

你現在能寫的每一章都是單一維度的工具（只追蹤 CPU，或只追蹤網路）。生產等級的 observability 需要多維度同時觀測、跨維度關聯（例如「哪個 process 在做 CPU 密集操作的同時也在做大量 network I/O？」）。

這個練習讓你整合所有學過的技術，實作一個可以在生產系統上運行的輕量 observability agent。

## 任務規格

**Agent 四個監控維度**：

1. **CPU profiling**：每秒 99 Hz sampling，追蹤哪些 process 最耗 CPU（top 10）

2. **Memory allocation**：用 kprobe 追蹤 `kmalloc`（kernel）和 uprobe 追蹤 libc `malloc`（userspace），輸出 allocation hotspots

3. **Network 流量**：用 TC BPF 統計每個 process 的 outbound 流量（bytes/packets/connections）

4. **Security events**：追蹤所有 exec（新 process 建立）和 sensitive file access（`/etc/passwd`, `/etc/shadow`）

**整合要求**：
- 所有 BPF programs 共享一個 ringbuf 輸出 channel
- 用 bpf_timer 每 10 秒輸出一次 summary report
- 用 task local storage 追蹤 per-process 的跨維度狀態（同一個 process 的 CPU/memory/network 資料可以關聯）
- 可以用 `SIGTERM` 優雅退出

**輸出格式**：

```
=== eBPF Observability Agent Report [2025-01-01 12:00:10] ===

CPU TOP 10 (last 10s):
  1. nginx (pid 1234): 42.3% CPU
  2. postgres (pid 5678): 18.1% CPU
  ...

MEMORY ALLOCATION TOP 5:
  1. nginx (pid 1234): 128MB kmalloc
  2. java (pid 9012): 512MB malloc
  ...

NETWORK TOP 5 (outbound):
  1. nginx (pid 1234): 1.2GB sent, 150K connections
  ...

SECURITY EVENTS (last 10s):
  [12:00:05] execve: bash (pid 3456, ppid 3455) started by uid=1000
  [12:00:07] file_open: /etc/shadow (pid 7890 postgres)
  ...
```

## 如果你卡住了

1. 先分別實作每個維度（CPU / memory / network / security），確認各自工作正常
2. 再整合到一個 unified ringbuf
3. bpf_timer 的初始化可以從 `SEC("fentry/...")` 觸發，或在 userspace 用 test_run 強制觸發
4. task local storage 的 key 是 task_struct pointer；用它把不同維度的資料關聯起來

## 實作步驟建議

### Step 1：統一的事件結構設計

```c
/* 所有事件共用的 header */
struct event_header {
    u32 type;       /* EVENT_CPU / EVENT_MEM / EVENT_NET / EVENT_SEC */
    u32 pid;
    u64 timestamp;
    char comm[16];
};

#define EVENT_CPU 1
#define EVENT_MEM 2
#define EVENT_NET 3
#define EVENT_SEC 4

/* 每種事件的 payload */
struct cpu_event    { struct event_header h; u32 stack_id; };
struct mem_event    { struct event_header h; u64 size; u8 is_kernel; };
struct net_event    { struct event_header h; u64 bytes; u32 connections; };
struct sec_event    { struct event_header h; u8 sec_type; char detail[64]; };
```

### Step 2：各維度的 BPF programs

```c
/* cpu_profiler.bpf.c：perf_event 採樣 */
SEC("perf_event")
int cpu_sample(struct bpf_perf_event_data *ctx) { ... }

/* mem_tracker.bpf.c：kprobe + uprobe */
SEC("kprobe/kmalloc")
int track_kmalloc(struct pt_regs *ctx) { ... }

/* net_monitor.bpf.c：TC BPF */
SEC("tc")
int net_egress(struct __sk_buff *skb) { ... }

/* sec_watcher.bpf.c：tracepoint + fentry */
SEC("tracepoint/syscalls/sys_enter_execve")
int watch_exec(struct trace_event_raw_sys_enter *ctx) { ... }
```

### Step 3：Shared Ringbuf + Timer Summary

```c
/* shared_maps.bpf.h：所有 programs 共享 */

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 4 * 1024 * 1024);  /* 4 MB */
} events SEC(".maps");

/* timer-based summary 每 10 秒觸發 */
struct summary_timer {
    struct bpf_timer timer;
    /* 快照資料 */
};
```

### Step 4：Task Local Storage 跨維度關聯

```c
/* per-process 的多維度資料 */
struct proc_stats {
    u64 cpu_cycles;    /* 從 CPU profile 累積 */
    u64 malloc_bytes;  /* 從 mem tracker 累積 */
    u64 net_bytes;     /* 從 net monitor 累積 */
    u32 sec_alerts;    /* security event 計數 */
};

/* task local storage */
struct {
    __uint(type, BPF_MAP_TYPE_TASK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct proc_stats);
} proc_storage SEC(".maps");
```

### Step 5：Userspace Agent

```c
/* agent.c */
/* 載入所有 BPF objects */
/* 設定 perf_event（CPU profiling）*/
/* attach TC（networking）*/
/* attach tracepoints（security）*/
/* ring_buffer__poll loop */
/* 按事件類型分發到不同的 handler */
/* Ctrl+C 優雅退出 */
```

## 完整參考解答

**先做完再看！**

<details>
<summary>核心 BPF 結構（觀念參考）</summary>

```c
/* core.bpf.c（簡化版，展示整合概念）*/
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

/* 共享 ringbuf */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 4 << 20);
} events SEC(".maps");

/* Task local storage */
struct proc_stats { u64 cpu; u64 mem; u64 net; u32 sec; };
struct {
    __uint(type, BPF_MAP_TYPE_TASK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct proc_stats);
} proc_storage SEC(".maps");

/* Summary timer */
struct timer_state { struct bpf_timer t; };
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct timer_state);
} timer_map SEC(".maps");

/* --- CPU profiling --- */
SEC("perf_event")
int cpu_profile(struct bpf_perf_event_data *ctx)
{
    struct task_struct *task = bpf_get_current_task_btf();
    struct proc_stats *s = bpf_task_storage_get(&proc_storage, task, 0,
                                                  BPF_LOCAL_STORAGE_GET_F_CREATE);
    if (s) s->cpu++;
    return 0;
}

/* --- Security watcher --- */
SEC("tracepoint/syscalls/sys_enter_execve")
int watch_exec(struct trace_event_raw_sys_enter *ctx)
{
    struct event_header *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) return 0;
    e->type = 4; /* EVENT_SEC */
    e->pid  = bpf_get_current_pid_tgid() >> 32;
    e->timestamp = bpf_ktime_get_ns();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* --- Timer summary --- */
static int summary_cb(void *map, int *key, struct timer_state *val)
{
    bpf_printk("=== 10s summary ===\n");
    /* 用 BPF iterator 遍歷 proc_storage 輸出 top processes */
    bpf_timer_start(&val->t, 10ULL * 1000000000, 0);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

</details>

## 測試用案例

| 測試場景 | 預期 agent 輸出 |
|---|---|
| `stress --cpu 2 --timeout 30` | CPU top 顯示 stress process |
| `curl http://example.com` | Network top 顯示 curl，security 顯示 curl exec |
| `cat /etc/shadow` | Security alert：sensitive file access |
| `for i in {1..100}; do ls; done` | Security：多次 exec；CPU：ls x100 |

## 延伸挑戰（加分）

- **挑戰一**：加入 BPF iterator 支援——用 task iterator 每 10 秒輸出所有 process 的 proc_stats，而不是靠 ring buffer 累積

- **挑戰二**：Prometheus metrics 匯出——在 userspace 維護一個 HTTP server（Go 或 C），把 agent 收到的資料轉成 Prometheus metrics 格式

- **挑戰三**：Container-aware——如果 process 在 container 裡，在輸出裡包含 container id（從 cgroup path 解析）

- **挑戰四**：把整個 agent 做成 systemd service，開機自動啟動，log 到 journald

## 自我檢核

- [ ] 能說出各維度的 BPF program 各自用了哪種 attach mechanism（perf_event / TC / tracepoint / fentry）
- [ ] 能解釋為什麼用 task local storage 而不是 hash map 做跨維度關聯
- [ ] 能描述 bpf_timer callback 如何被排程，以及為什麼 summary 不能是 blocking 操作

→ [Final Project：生產級 eBPF agent](./final-project-production-ebpf-agent.md)
