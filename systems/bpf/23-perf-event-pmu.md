# Ch 23 — perf_event 與 PMU 硬體計數器

> **目標**：理解 perf_event 子系統的架構——hardware performance counters（PMU）、software events、sampling vs counting 模式——以及如何用 BPF 程式在 perf event 觸發時執行，做 CPU profiling 和 cache 分析。

## 先建立直覺：PMU 是 CPU 裡的監控器

每個現代 CPU 都有 **Performance Monitoring Unit（PMU）**：一組硬體計數器，在指定的 CPU 事件（如 instructions retired、cache misses、branch mispredictions）發生時遞增。

```
CPU 執行指令
    │
    │  每條指令執行完
    ▼
PMU 計數器（例如 CPU_CYCLES）
    │  計數器到達閾值時觸發 overflow interrupt
    ▼
Kernel perf_event 子系統
    │  通知 BPF program
    ▼
你的 BPF program（採樣此刻的 stack trace、IP 等）
```

**Counting vs Sampling**：

- **Counting**：計算事件發生的總次數（例如：這段 code 執行期間，cache miss 了幾次）
- **Sampling**：每 N 個事件觸發一次中斷，採樣此刻的執行狀態（IP、stack trace）——用於 profiling

BPF 和 perf_event 的結合主要用於 **sampling**：每 99 次 CPU cycle（或每 10000 次 cache miss）觸發一次，BPF program 記錄當前的 instruction pointer 和 call stack。

## perf_event 的類型

```
PERF_TYPE_HARDWARE（硬體事件）
  PERF_COUNT_HW_CPU_CYCLES         ← CPU clock cycles
  PERF_COUNT_HW_INSTRUCTIONS       ← Instructions retired
  PERF_COUNT_HW_CACHE_REFERENCES   ← Cache 存取次數
  PERF_COUNT_HW_CACHE_MISSES       ← Cache miss 次數
  PERF_COUNT_HW_BRANCH_INSTRUCTIONS← Branch 指令
  PERF_COUNT_HW_BRANCH_MISSES      ← Branch misprediction
  PERF_COUNT_HW_BUS_CYCLES         ← Bus cycles

PERF_TYPE_SOFTWARE（軟體事件，kernel 模擬）
  PERF_COUNT_SW_CPU_CLOCK          ← CPU clock（高精度）
  PERF_COUNT_SW_TASK_CLOCK         ← Task 的 CPU time
  PERF_COUNT_SW_PAGE_FAULTS        ← Page faults
  PERF_COUNT_SW_CONTEXT_SWITCHES   ← Context switch
  PERF_COUNT_SW_CPU_MIGRATIONS     ← Process 在 CPU 間遷移

PERF_TYPE_TRACEPOINT（kernel tracepoint，用 ID）
  任何 /sys/kernel/debug/tracing/events/*/id

PERF_TYPE_RAW（CPU 廠商特定事件）
  Intel: rNNN（uncore events、L1/L2/L3 cache breakdown）
  AMD: 自己的 event code
```

## CPU Profiling 用 BPF

最常見的用途：每秒 99 次 CPU sampling，採樣 stack trace：

```c
/* profiler.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/* Stack trace 存儲 */
struct {
    __uint(type, BPF_MAP_TYPE_STACK_TRACE);
    __uint(key_size, sizeof(u32));
    __uint(value_size, 127 * sizeof(u64));  /* max 127 frames */
    __uint(max_entries, 10000);
} stacks SEC(".maps");

/* (stack_id, comm) → count */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10000);
    __type(key, struct stack_key);
    __type(value, u64);
} counts SEC(".maps");

struct stack_key {
    u32 stack_id;
    char comm[16];
};

SEC("perf_event")
int on_sample(struct bpf_perf_event_data *ctx)
{
    struct stack_key key = {};

    /* 取得 kernel stack trace，存到 stacks map，得到 id */
    key.stack_id = bpf_get_stackid(&ctx->regs, &stacks,
                                    BPF_F_REUSE_STACKID);
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    u64 one = 1;
    u64 *cnt = bpf_map_lookup_elem(&counts, &key);
    if (cnt)
        (*cnt)++;
    else
        bpf_map_update_elem(&counts, &key, &one, BPF_NOEXIST);

    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace 設定 perf_event**（以 99 Hz sampling 為例）：

```c
/* userspace 設定 perf event */
struct perf_event_attr pea = {
    .type           = PERF_TYPE_SOFTWARE,
    .config         = PERF_COUNT_SW_CPU_CLOCK,
    .sample_type    = PERF_SAMPLE_CALLCHAIN,
    .sample_period  = 1000000000 / 99,  /* 99 Hz = 10.1ms period */
    .wakeup_events  = 1,
};

/* 在每個 CPU 上 attach */
int ncpus = sysconf(_SC_NPROCESSORS_ONLN);
for (int cpu = 0; cpu < ncpus; cpu++) {
    int pfd = syscall(__NR_perf_event_open, &pea, -1, cpu, -1, 0);
    ioctl(pfd, PERF_EVENT_IOC_SET_BPF, prog_fd);
    ioctl(pfd, PERF_EVENT_IOC_ENABLE, 0);
}
```

## Cache Miss Profiling

```c
SEC("perf_event")
int cache_miss_sampler(struct bpf_perf_event_data *ctx)
{
    /* 每 N 次 LLC miss 採樣一次 */
    u64 addr = ctx->addr;  /* 導致 cache miss 的記憶體地址（如果有 PEBS）*/

    /* 取得 user stack */
    long stack_id = bpf_get_stackid(&ctx->regs, &stacks,
                                     BPF_F_USER_STACK);

    struct sample {
        u64 addr;
        u64 ip;
        u32 pid;
        u32 stack_id;
    } s = {
        .addr     = addr,
        .ip       = PT_REGS_IP(&ctx->regs),
        .pid      = bpf_get_current_pid_tgid() >> 32,
        .stack_id = stack_id,
    };

    bpf_perf_event_output(&ctx->regs, &events,
                          BPF_F_CURRENT_CPU, &s, sizeof(s));
    return 0;
}
```

**設定 Cache Miss event**：

```c
struct perf_event_attr pea = {
    .type       = PERF_TYPE_HARDWARE,
    .config     = PERF_COUNT_HW_CACHE_MISSES,
    .sample_period = 1000,  /* 每 1000 次 cache miss 採樣一次 */
};
```

## bpftrace 的 hardware / profile probe

用 bpftrace 更簡單：

```bash
# CPU profiling：每秒 99 次，輸出 flamegraph
sudo bpftrace -e '
profile:hz:99 { @[kstack] = count(); }
interval:s:30 { print(@); clear(@); exit(); }' | \
    stackcollapse-bpftrace.pl | flamegraph.pl > flame.svg

# Cache miss profiling
sudo bpftrace -e '
hardware:cache-misses:1000 {
    @[ustack] = count();  /* userspace stack */
}
interval:s:10 { print(@); exit(); }'

# Branch misprediction
sudo bpftrace -e '
hardware:branch-misses:10000 {
    @[comm, kstack] = count();
}'
```

## On-CPU vs Off-CPU Analysis

**On-CPU**：CPU 在執行你的 code（profile:hz:99 能採樣到）

**Off-CPU**：CPU 不在執行你的 code（等 I/O、lock、sleep）

Off-CPU analysis 用 tracepoint：

```bash
# Off-CPU：追蹤被 schedule 出去的時間
sudo bpftrace -e '
tracepoint:sched:sched_switch
/args->prev_state != 0/  /* 不是 running 狀態才是真正的 wait */
{
    @start[args->prev_pid] = nsecs;
}

tracepoint:sched:sched_switch
/@start[args->next_pid]/
{
    @offcpu_us[args->next_comm] = hist(
        (nsecs - @start[args->next_pid]) / 1000
    );
    delete(@start[args->next_pid]);
}'
```

## 踩雷集錦

1. **PMU 事件是 CPU 型號相關的**：`PERF_COUNT_HW_CACHE_MISSES` 的語意因 CPU 而異；Intel 和 AMD 的 raw event code 完全不同；`perf list` 可以列出你的 CPU 支援的事件

2. **99 Hz 而不是 100 Hz**：質數採樣頻率避免和固定頻率的事件（如 100 Hz timer interrupt）同步，導致採樣偏差

3. **VMware / KVM 虛擬機上的硬體計數器**：在虛擬機裡，硬體計數器可能被虛擬化（有額外 overhead）或完全不支援；`perf stat ls` 如果看到 "not supported" 就是在虛擬機裡沒有原生 PMU 支援

4. **`BPF_F_REUSE_STACKID`**：如果 stack map 快滿了，這個 flag 允許覆蓋已有的 entry；否則 `bpf_get_stackid` 回傳 -EEXIST；flamegraph 工作量大時一定要加這個 flag

## 動手練習

1. 用 bpftrace `profile:hz:99` 收集 30 秒的 CPU profile，生成 flamegraph（需要 FlameGraph 工具包）

2. 用 `hardware:cache-misses:10000` 找出系統上最常引起 LLC cache miss 的 userspace call stack

3. 用 off-CPU analysis 找出某個慢程式（如執行一個大型 `find /`）的等待時間花在哪裡（disk I/O wait vs lock wait vs sleep）

## 本章重點整理

- PMU 是 CPU 硬體的效能計數器；BPF 程式在計數器 overflow 時觸發，記錄採樣時的狀態
- CPU profiling 用 `PERF_COUNT_SW_CPU_CLOCK`（或 `profile:hz:N`），每 N 個時間單位採樣一次 stack
- Off-CPU analysis 用 scheduler tracepoint（`sched:sched_switch`），追蹤 process 等待的時間
- 採樣頻率用質數（99 Hz）避免和固定事件同步導致偏差

## 自我檢核

- [ ] 能解釋 sampling profiling 和 counting profiling 的差別，以及各自適合什麼場景
- [ ] 知道為什麼 CPU profiling 用 99 Hz 而不是 100 Hz
- [ ] 能區分 on-CPU 和 off-CPU analysis，以及各自用什麼 BPF probe 做

→ [Ch 24 Profiling 與 Flamegraph](./24-profiling-flamegraph.md)
