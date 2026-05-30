# Ch 8 — BPF Maps：所有資料結構

> **目標**：理解 BPF maps 的所有主要型別——每種型別的內部資料結構、適合的使用場景、效能特性、以及如何在 BPF 程式和 userspace 之間安全地共享資料。

## 為什麼需要這個？

BPF 程式本身是無狀態的——每次觸發執行，完成後狀態消失。Maps 是 BPF 程式和外部世界（其他 BPF 程式、userspace）共享持久狀態的唯一方式。

不同的 map type 有根本性的效能和語意差異：選錯了 map type，你可能在 lock contention 上浪費大量 CPU（用 `HASH` 而不是 `PERCPU_HASH`），或在 consumer 跟不上時 drop events（用 `PERF_EVENT_ARRAY` 而不是 `RINGBUF`）。

> 如果你對 helper function 如何操作 map 還不熟，這章先看 map 型別；helper API 在 [Ch 11](./11-helper-functions.md) 詳細說明。

## 先建立直覺：Map = 共享記憶體 + 型別化的存取介面

```
BPF program（kernel context）
       │
       │  bpf_map_lookup_elem()
       │  bpf_map_update_elem()
       │  bpf_map_delete_elem()
       ▼
  ┌──────────────────────────────┐
  │          BPF Map             │
  │  kernel allocated memory     │
  │  + type-specific operations  │
  └──────────────────────────────┘
       ▲
       │  bpf(BPF_MAP_LOOKUP_ELEM, ...)
       │  bpf(BPF_MAP_UPDATE_ELEM, ...)
       │  bpf(BPF_MAP_GET_NEXT_KEY, ...)
       │
userspace（透過 map fd）
```

Map 是 reference counted 的 kernel object，透過 fd 引用。BPF filesystem 的 pin 讓 map 在 userspace 程式退出後繼續存在。

## 定義 Map（BPF 程式側）

現代 libbpf 用 BTF-typed map definition（用特殊 struct 定義）：

```c
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

/* BTF-typed map definition（現代方式，推薦）*/
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);        /* key 是 u32（PID）*/
    __type(value, u64);      /* value 是 u64（count）*/
} pid_count SEC(".maps");
```

舊式的 map definition（仍然有效，但不推薦）：

```c
/* 舊式：沒有 BTF type info */
struct bpf_map_def SEC("maps") pid_count_old = {
    .type        = BPF_MAP_TYPE_HASH,
    .key_size    = sizeof(u32),
    .value_size  = sizeof(u64),
    .max_entries = 10240,
};
```

## HASH（`BPF_MAP_TYPE_HASH`）

**內部結構**：Hash table with chaining。key 的 hash 決定 bucket；同 bucket 的 entry 用 linked list 串接。

**存取**：`bpf_map_lookup_elem`、`bpf_map_update_elem`、`bpf_map_delete_elem`，平均 O(1)。

**鎖**：全域 spinlock（kernel 5.1 之前是 per-bucket spinlock）。多個 CPU 並發 update 時有 lock contention。

```c
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);
    __type(value, u64);
} syscall_count SEC(".maps");

SEC("tracepoint/raw_syscalls/sys_enter")
int count_syscalls(struct trace_event_raw_sys_enter *ctx)
{
    u32 syscall_nr = ctx->id;
    u64 *cnt = bpf_map_lookup_elem(&syscall_count, &syscall_nr);
    if (cnt) {
        (*cnt)++;  /* 注意：這裡有 TOCTOU，生產環境用 atomic */
    } else {
        u64 init = 1;
        bpf_map_update_elem(&syscall_count, &syscall_nr, &init, BPF_NOEXIST);
    }
    return 0;
}
```

**適合場景**：key 集合不固定（動態新增 / 刪除 key）；key 分布稀疏。

**不適合場景**：高頻 update 且 key 集合已知 → 改用 ARRAY；需要高並發 update → 改用 PERCPU_HASH。

## ARRAY（`BPF_MAP_TYPE_ARRAY`）

**內部結構**：固定大小的陣列，key 是 index（0 ~ max_entries-1）。

**存取**：O(1)，比 HASH 快得多（只是 pointer offset）。

**特性**：
- 不能刪除 entry（只能把 value 清零）
- 整個 array 在分配時就預先分配好，map 大小固定
- key 必須是 32-bit unsigned integer

```c
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 256);  /* 支援 256 個 syscall nr */
    __type(key, u32);
    __type(value, u64);
} syscall_stats SEC(".maps");

SEC("tracepoint/raw_syscalls/sys_enter")
int fast_count(struct trace_event_raw_sys_enter *ctx)
{
    u32 key = ctx->id & 0xFF;  /* 只取低 8 bits，確保 in-bounds */
    u64 *val = bpf_map_lookup_elem(&syscall_stats, &key);
    if (val)
        __sync_fetch_and_add(val, 1);  /* 原子加（lock xadd）*/
    return 0;
}
```

**適合場景**：key 是固定範圍的整數（syscall nr、port number、CPU id）；需要最高存取速度。

## PERCPU_HASH 和 PERCPU_ARRAY

Per-CPU 版本：每個 CPU core 有自己獨立的一份 map 資料，**不需要跨 CPU 加鎖**，大幅降低 lock contention。

```c
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} dropped_pkts SEC(".maps");

SEC("xdp")
int count_drops(struct xdp_md *ctx)
{
    u32 key = 0;
    u64 *val = bpf_map_lookup_elem(&dropped_pkts, &key);
    if (val)
        (*val)++;  /* 不需要 atomic，每個 CPU 有自己的 copy */
    return XDP_PASS;
}
```

**在 userspace 讀取 per-CPU map**：

```c
/* userspace 讀取 per-CPU array */
int ncpus = libbpf_num_possible_cpus();
u64 *values = calloc(ncpus, sizeof(u64));
u32 key = 0;

bpf_map_lookup_elem(map_fd, &key, values);
/* values[0] 是 CPU 0 的值，values[1] 是 CPU 1 的值，... */

u64 total = 0;
for (int i = 0; i < ncpus; i++)
    total += values[i];

free(values);
```

**適合場景**：高頻 counter 或統計（每個 packet、每個 syscall）；不需要跨 CPU 的 per-key 資料一致性。

## LRU_HASH（`BPF_MAP_TYPE_LRU_HASH`）

Hash table with LRU eviction：當 map 滿了，自動淘汰最少使用的 entry，不需要手動管理容量。

```c
struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 100000);
    __type(key, u32);         /* 連線的 dest IP */
    __type(value, u64);       /* 最後看到的 timestamp */
} active_connections SEC(".maps");
```

**適合場景**：追蹤活躍連線、活躍 process 等集合大小不固定但有上限的場景。

**注意**：LRU eviction 在高並發下會有鎖競爭。`BPF_MAP_TYPE_LRU_PERCPU_HASH` 是 per-CPU LRU，鎖競爭更少。

## RINGBUF（`BPF_MAP_TYPE_RINGBUF`）（kernel 5.8+，推薦）

**取代 PERF_EVENT_ARRAY**。單個 ring buffer，所有 CPU 的 BPF 程式都可以寫入，userspace 一個 consumer 讀取。

**優點**：
- 不會因為 consumer 慢而 drop events（只要 ring 夠大，或 consumer 跟得上）
- 記憶體效率好（不是 per-CPU）
- 支援 reservation API（先 reserve，寫完再 commit，避免 drop）

```c
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);  /* 256 KB ring buffer */
} events SEC(".maps");

/* ringbuf 要傳送的事件結構 */
struct event {
    u32 pid;
    char comm[16];
    u64 timestamp;
};

SEC("tracepoint/syscalls/sys_enter_execve")
int trace_exec(struct trace_event_raw_sys_enter *ctx)
{
    /* 方法一：reserve + 填寫 + submit（零拷貝）*/
    struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e)
        return 0;  /* ring 滿了，drop */

    e->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    e->timestamp = bpf_ktime_get_ns();

    bpf_ringbuf_submit(e, 0);

    /* 方法二：output（有一次拷貝，但更簡單）*/
    /* struct event ev = {...}; */
    /* bpf_ringbuf_output(&events, &ev, sizeof(ev), 0); */

    return 0;
}
```

**Userspace 消費（libbpf）**：

```c
/* ring_buffer__poll 等待事件，呼叫 handle_event */
static int handle_event(void *ctx, void *data, size_t size)
{
    struct event *e = data;
    printf("pid=%u comm=%s ts=%llu\n", e->pid, e->comm, e->timestamp);
    return 0;
}

struct ring_buffer *rb = ring_buffer__new(bpf_map__fd(map), handle_event, NULL, NULL);

while (1)
    ring_buffer__poll(rb, 1000);  /* 最多等 1000ms */
```

## PERF_EVENT_ARRAY（`BPF_MAP_TYPE_PERF_EVENT_ARRAY`）

Ring buffer 出現之前的標準事件傳輸機制。per-CPU ring buffer，每個 CPU 一個，userspace 需要 poll 每個 CPU 的 buffer。

**現在推薦用 RINGBUF，但要看懂舊 code 所以要知道這個**：

```c
struct {
    __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY);
    __uint(key_size, sizeof(int));   /* key 是 CPU id */
    __uint(value_size, sizeof(u32)); /* value 是 perf event fd */
    __uint(max_entries, 0);          /* libbpf 會設定成 CPU 數 */
} pb SEC(".maps");

SEC("kprobe/vfs_read")
int perf_output_example(struct pt_regs *ctx)
{
    u64 data = bpf_ktime_get_ns();
    bpf_perf_event_output(ctx, &pb, BPF_F_CURRENT_CPU, &data, sizeof(data));
    return 0;
}
```

PERF_EVENT_ARRAY 的問題：每個 CPU 獨立的 buffer，userspace 要 poll N 個 fd（N = CPU 數）；consumer 跟不上時 drop events；每次 output 都是拷貝。

## STACK_TRACE（`BPF_MAP_TYPE_STACK_TRACE`）

儲存 call stack trace。key 是 stack id（由 `bpf_get_stackid()` 回傳），value 是一組 instruction pointer。

```c
struct {
    __uint(type, BPF_MAP_TYPE_STACK_TRACE);
    __uint(key_size, sizeof(u32));
    __uint(value_size, 127 * sizeof(u64));  /* 最多 127 frames */
    __uint(max_entries, 10000);
} stacks SEC(".maps");

SEC("perf_event")
int profile(struct bpf_perf_event_data *ctx)
{
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    /* 取得 kernel stack trace，回傳 stack id */
    long stack_id = bpf_get_stackid(ctx, &stacks, 0);
    if (stack_id < 0)
        return 0;
    /* 可以把 (pid, stack_id) 存入另一個 map 做計數 */
    return 0;
}
```

Flamegraph 工具（如 bcc 的 `profile`）就是用這個 map type 收集 CPU profile。

## QUEUE 和 STACK（kernel 4.20+）

FIFO queue 和 LIFO stack，用於 BPF 程式之間傳遞資料。

```c
struct {
    __uint(type, BPF_MAP_TYPE_QUEUE);
    __uint(max_entries, 100);
    __type(value, u32);  /* queue 沒有 key */
} work_queue SEC(".maps");

/* push（enqueue）*/
u32 item = 42;
bpf_map_push_elem(&work_queue, &item, 0);

/* pop（dequeue）*/
u32 result;
bpf_map_pop_elem(&work_queue, &result);

/* peek（不移除）*/
bpf_map_peek_elem(&work_queue, &result);
```

## SOCKHASH 和 SOCKMAP

儲存 socket 的 reference，用於 socket redirection（sk_msg / sk_skb）。

```c
struct {
    __uint(type, BPF_MAP_TYPE_SOCKHASH);
    __uint(max_entries, 65536);
    __type(key, u32);
    __type(value, u32);
} sock_map SEC(".maps");
```

詳細用法在 [Ch 29 Socket BPF](./29-socket-bpf.md)。

## Map Types 總覽

| 型別 | 內部結構 | 適合場景 | 鎖 |
|---|---|---|---|
| `HASH` | Hash table | 稀疏 key 集合，動態插入/刪除 | Global spinlock |
| `ARRAY` | 固定陣列 | Dense key（0~N），最快存取 | 無（CAS for atomics）|
| `PERCPU_HASH` | Per-CPU hash | 高並發 hash lookup | 無 |
| `PERCPU_ARRAY` | Per-CPU array | 高並發 counter | 無 |
| `LRU_HASH` | Hash + LRU eviction | 活躍集合，不超過上限 | LRU spinlock |
| `RINGBUF` | Shared ring buffer | 事件流，取代 perf event | Wait-free write |
| `PERF_EVENT_ARRAY` | Per-CPU ring | 事件流（老方式）| Per-CPU |
| `STACK_TRACE` | Stack id → frames | CPU profiling | Bucket lock |
| `QUEUE` | FIFO queue | BPF-to-BPF 任務佇列 | Spinlock |
| `STACK` | LIFO stack | 同上 | Spinlock |
| `SOCKHASH` / `SOCKMAP` | Socket reference | Socket redirect | RCU |
| `DEVMAP` | XDP redirect table | XDP port redirect | RCU |
| `CPUMAP` | CPU redirect table | XDP remote CPU exec | RCU |
| `BLOOM_FILTER` | Bloom filter | 快速 membership check（有 false positive）| 無 |

## 踩雷集錦

1. **HASH map 在高並發下效能崩潰**：HASH 有 global lock；如果每個封包都 update 同一個 key，鎖競爭很嚴重。改用 PERCPU_HASH，在 userspace 把 per-CPU 的值加總

2. **ARRAY map 的值不能刪除**：`bpf_map_delete_elem` 對 ARRAY 無效（回傳 -EINVAL）。如果你需要「刪除」，要把 value 設成 sentinel（例如 0）

3. **RINGBUF 的 `max_entries` 必須是 2 的冪次**：如果你給了非 2 的冪次，`bpf_map_create` 會回傳 -EINVAL

4. **Per-CPU map 的 userspace 讀取 buffer 大小**：userspace 讀 per-CPU map 時，提供的 buffer 必須是 `value_size * ncpus`；只給 `value_size` 的空間會 segfault

5. **`bpf_ringbuf_reserve` 失敗時不能再 output**：`reserve` 失敗（ring 滿）時，你只能 `return 0`，不能改用 `bpf_ringbuf_output`（因為 output 也需要空間）；要增大 `max_entries` 或加速 consumer

6. **STACK_TRACE 的 `bpf_get_stackid` 在 NMI context 不可靠**：在 perf event program 裡，某些 stack 會因 NMI 中斷被採到不完整的 frame，stack_id 可能是 -EEXIST（已有相同 stack）或 -ENOMEM

## 動手練習

1. 寫一個 BPF 程式，用 PERCPU_ARRAY 統計每個 CPU 觸發了多少次 `sys_enter_read`。在 userspace 讀取所有 CPU 的值並加總，每秒輸出一次

2. 把上面的程式改用 HASH 而不是 PERCPU_ARRAY，用 `bpftool prog profile` 或 `perf stat` 比較兩個版本在高 syscall rate 下的 CPU 使用率差異

3. 寫一個用 RINGBUF 傳輸事件的 BPF 程式：每次 execve 把 `{pid, comm, timestamp}` 送到 ringbuf，userspace 的 consumer 每秒輸出接收到的事件數

## 本章重點整理

- Maps 是 BPF 程式保存狀態和與 userspace 通訊的唯一方式
- ARRAY 最快（pointer offset）；HASH 最靈活（動態 key）；PERCPU_* 解決鎖競爭
- RINGBUF 是現代的事件傳輸機制，優於 PERF_EVENT_ARRAY（更省記憶體、更少 drop）
- Per-CPU map 在 userspace 讀取時需要 `value_size * ncpus` 大小的 buffer

## 自我檢核

- [ ] 能說出選擇 HASH vs ARRAY vs PERCPU_ARRAY 的決策依據
- [ ] 知道 RINGBUF 和 PERF_EVENT_ARRAY 的核心差異，以及什麼時候 RINGBUF 可能 drop events
- [ ] 能寫出正確讀取 per-CPU map 的 userspace 程式碼（buffer 大小正確）
- [ ] 知道 ARRAY map 的「刪除」是什麼意思（沒有真正的刪除）

## 延伸閱讀

### 官方文件

- **[Linux kernel: BPF maps](https://www.kernel.org/doc/html/latest/bpf/maps.html)**
  - **讀哪裡**：每個 map type 的描述；`BPF_MAP_TYPE_RINGBUF` 那一節特別重要
  - **學什麼**：所有 map type 的 kernel 版本需求、key/value 限制、特殊操作

### 部落格

- **[BPF ring buffer](https://nakryiko.com/posts/bpf-ringbuf/)** — Andrii Nakryiko（libbpf 主要作者）, 2020
  - **這篇說什麼**：深入解釋 RINGBUF 的設計動機、實作細節、和 PERF_EVENT_ARRAY 的對比
  - **讀哪裡**：整篇；特別是 "Why ringbuf?" 那一節
  - **為什麼值得讀**：作者就是 RINGBUF 的設計者和 libbpf API 的作者；這是最直接的設計文件

- **[BPF maps and their design considerations](https://arthurchiao.art/blog/ebpf-maps-and-their-design-zh/)** — ArthurChiao, 2021
  - **這篇說什麼**：對比各 map type 的效能測試和選擇指南
  - **讀哪裡**：整篇；特別是 performance comparison 那一節
  - **為什麼值得讀**：有實測數字，幫助你在選 map type 時有量化根據

→ [Ch 9 BTF：BPF Type Format 深入](./09-btf-deep-dive.md)
