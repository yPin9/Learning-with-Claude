# 練習 D — PostgreSQL Slow Query Tracer + Flamegraph Pipeline

> **目標**：整合 USDT（Ch 22）、perf_event profiling（Ch 23）、flamegraph（Ch 24）、ringbuf（Ch 25），實作一個完整的 PostgreSQL query 觀測工具：捕捉 slow query（>50ms）、同時採集執行期間的 CPU stack trace，輸出 query + flamegraph 的組合分析。

## 背景與動機

Database slow query 是生產環境最常見的效能問題之一。傳統做法是靠 `pg_stat_statements` 或 `log_min_duration_statement`——但這兩種方式只告訴你「哪個 query 慢」，不告訴你「為什麼慢」（是 CPU 密集？是在等 I/O？是在等 lock？）。

這個練習用 eBPF 解答「為什麼」：同時捕捉 slow query 的 SQL 文字和執行期間的 CPU profile，讓你一眼看出 slow query 的 CPU 熱點在哪裡。

## 任務規格

**工具一：slow_query.bpf（libbpf 實作）**：
- Attach 到 PostgreSQL 的 `postgresql:query__start` 和 `postgresql:query__done` USDT probe
- 計算每個 query 的執行時間
- 超過閾值（預設 50ms，可配置）的 query 透過 ringbuf 輸出
- 同時輸出 postgres process 的 CPU stack trace

**工具二（bpftrace 快速版）**：
- 用 bpftrace 的 USDT + profile 結合，5 分鐘內寫出 slow query + stack 組合

**輸出格式**：

```
[2025-01-01 12:34:56.789] SLOW QUERY (234ms):
  Query: SELECT * FROM orders WHERE user_id = 12345 ORDER BY created_at DESC LIMIT 100
  Backend PID: 12345
  CPU Profile during query:
    heap_sort;tuplesort_performsort;ExecSort;standard_ExecutorRun... 45%
    index_scan;heap_fetch;ExecIndexScan;standard_ExecutorRun...     35%
    pg_qsort;tuplesort_performsort...                              20%
```

**驗收標準**：
- 能捕捉到執行時間超過閾值的 query
- CPU stack 採集能顯示 PostgreSQL 函式名稱（需要 debug symbols）
- Ctrl+C 後輸出一個 flamegraph SVG

## 如果你卡住了

1. 先確認 PostgreSQL 有 USDT probe：`sudo bpftrace -l 'usdt:/usr/lib/postgresql/*/bin/postgres:*'`
2. 如果沒有 USDT，換用 kretprobe `exec_simple_query`（比較不穩定但更通用）
3. PostgreSQL 的 debug symbols：`sudo apt install postgresql-14-dbgsym`（或對應版本）
4. USDT + CPU profiling 的組合技巧：在 `query__start` 時開始採集，`query__done` 時停止，這期間的 profile 就是 query 的 CPU 熱點
5. 同時 attach 兩個不同 probe type（USDT + perf_event）需要兩個分開的 link

## 實作步驟建議

### Step 1：驗證 USDT（驗收：能看到 query__start/done 觸發）

```bash
# 確認 USDT probe 存在
sudo bpftrace -l 'usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start'

# 觸發一個 test query
psql -c "SELECT pg_sleep(0.1)"

# 用最簡單的 bpftrace 確認能捕捉到
sudo bpftrace -e '
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start {
    printf("query start: pid=%d\n", pid);
}'
```

### Step 2：bpftrace slow query（驗收：能輸出超過閾值的 query text）

```bash
# 5 分鐘內的快速版本
sudo bpftrace -e '
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start {
    @start[pid] = nsecs;
    @query[pid] = str(arg0);
}
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__done
/@start[pid]/
{
    $dur_us = (nsecs - @start[pid]) / 1000;
    if ($dur_us > 50000) {
        printf("[SLOW %d us] %s\n", $dur_us, @query[pid]);
    }
    delete(@start[pid]);
    delete(@query[pid]);
}'
```

### Step 3：加入 CPU profile（驗收：slow query 同時輸出 stack）

組合：`query__start` 時記錄開始，`query__done` 時如果 slow，輸出 stack trace。

提示：在 `query__start` 時，記錄 pid 進 hash map；在 `profile:hz:99` 裡，如果 pid 在 hash map 裡，就採集 stack；在 `query__done` 裡，清理並輸出。

### Step 4：libbpf 版本（可選）

用 libbpf + skeleton 實作完整版本，把 slow query 事件和 CPU profile 都透過 ringbuf 傳給 userspace，在 userspace 做 symbol resolution 和 flamegraph 輸出。

### Step 5：Flamegraph 輸出（驗收：生成包含 PostgreSQL 函式名稱的 SVG）

把 Step 3 的 stack 輸出 pipe 給 flamegraph.pl。

## 完整參考解答（bpftrace 版）

**先做完再看！**

<details>
<summary>slow_query.bt（bpftrace 版本）</summary>

```bash
#!/usr/bin/env bpftrace
/* slow_query.bt — PostgreSQL slow query + CPU profile */

BEGIN {
    printf("Tracing PostgreSQL queries > 50ms. Ctrl+C to stop.\n");
    @threshold_us = 50000;  /* 50ms */
}

/* 記錄 query 開始 */
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__start {
    @start_us[pid] = (nsecs / 1000);
    @query_text[pid] = str(arg0);
    @profiling[pid] = 1;  /* 標記這個 pid 需要 profile */
}

/* CPU 採樣：只採集正在執行 query 的 postgres backend */
profile:hz:99
/@profiling[pid]/
{
    @cpu_stacks[pid, kstack] = count();
}

/* Query 結束 */
usdt:/usr/lib/postgresql/14/bin/postgres:postgresql:query__done
/@start_us[pid]/
{
    $dur_us = (nsecs / 1000) - @start_us[pid];
    
    if ($dur_us > @threshold_us) {
        printf("\n[SLOW QUERY pid=%d dur=%d us]\n", pid, $dur_us);
        printf("  SQL: %s\n", @query_text[pid]);
        printf("  CPU stacks during query:\n");
        
        /* 輸出這個 pid 的 stack（排序後）*/
        /* 注意：bpftrace 目前不支援 per-key 輸出，需要後處理 */
    }
    
    delete(@start_us[pid]);
    delete(@query_text[pid]);
    delete(@profiling[pid]);
    delete(@cpu_stacks[pid]);  /* 清理 */
}

END {
    clear(@threshold_us);
    printf("\n=== CPU Stack Summary (all queries) ===\n");
    /* print(@cpu_stacks); */
}
```

**執行和測試**：

```bash
# 安裝 PostgreSQL debug symbols
sudo apt install postgresql-14-dbgsym

# 啟動 bpftrace
sudo bpftrace slow_query.bt

# 在另一個 terminal 生成 slow query
psql -c "SELECT count(*) FROM generate_series(1, 10000000)"
```

</details>

<details>
<summary>完整版：libbpf C 實作</summary>

```c
/* pg_tracer.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

#define QUERY_MAX 512
#define STACKS_MAX 4096
#define STACK_DEPTH 127

/* Query 開始時間 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, pid_t);
    __type(value, u64);
} query_start SEC(".maps");

/* Query 文字 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __key(pid_t);
    __value(char[QUERY_MAX]);
} query_text SEC(".maps");

/* CPU profile 中的 query */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, pid_t);
    __type(value, u8);
} active_queries SEC(".maps");

/* Stack trace map */
struct {
    __uint(type, BPF_MAP_TYPE_STACK_TRACE);
    __uint(key_size, sizeof(u32));
    __uint(value_size, STACK_DEPTH * sizeof(u64));
    __uint(max_entries, STACKS_MAX);
} stacks SEC(".maps");

/* 事件結構 */
struct query_event {
    pid_t  pid;
    u64    duration_us;
    s32    stack_id;
    char   query[QUERY_MAX];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 22);  /* 4 MB */
} rb SEC(".maps");

/* rodata */
const volatile u64 slow_threshold_us = 50000;  /* 50ms */

SEC("usdt//usr/lib/postgresql/14/bin/postgres:postgresql:query__start")
int trace_query_start(struct pt_regs *ctx)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    u64 now = bpf_ktime_get_ns() / 1000;

    /* 記錄開始時間 */
    bpf_map_update_elem(&query_start, &pid, &now, BPF_ANY);

    /* 讀取 query 文字 */
    char text[QUERY_MAX];
    long query_ptr;
    bpf_usdt_arg(ctx, 0, &query_ptr);
    bpf_probe_read_user_str(text, sizeof(text), (void *)query_ptr);
    bpf_map_update_elem(&query_text, &pid, &text, BPF_ANY);

    /* 標記為 active（CPU profile 用）*/
    u8 one = 1;
    bpf_map_update_elem(&active_queries, &pid, &one, BPF_ANY);

    return 0;
}

SEC("usdt//usr/lib/postgresql/14/bin/postgres:postgresql:query__done")
int trace_query_done(struct pt_regs *ctx)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    u64 *start = bpf_map_lookup_elem(&query_start, &pid);
    if (!start) return 0;

    u64 dur_us = bpf_ktime_get_ns() / 1000 - *start;

    bpf_map_delete_elem(&query_start, &pid);
    bpf_map_delete_elem(&active_queries, &pid);

    if (dur_us < slow_threshold_us) {
        bpf_map_delete_elem(&query_text, &pid);
        return 0;
    }

    /* Slow query！送到 ringbuf */
    struct query_event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) {
        bpf_map_delete_elem(&query_text, &pid);
        return 0;
    }

    e->pid = pid;
    e->duration_us = dur_us;

    char *text = bpf_map_lookup_elem(&query_text, &pid);
    if (text)
        __builtin_memcpy(e->query, text, QUERY_MAX);

    bpf_map_delete_elem(&query_text, &pid);

    /* 取得當前的 CPU stack（這是 query done 的瞬間，不是執行期間的 profile）*/
    e->stack_id = bpf_get_stackid(ctx, &stacks, BPF_F_REUSE_STACKID);

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* CPU 採樣：只對 active queries 的 process 採樣 */
SEC("perf_event")
int cpu_sampler(struct bpf_perf_event_data *ctx)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    u8 *active = bpf_map_lookup_elem(&active_queries, &pid);
    if (!active) return 0;

    /* 採集 stack（儲存到 stacks map，讓後面的 query_done 能讀到）*/
    bpf_get_stackid(&ctx->regs, &stacks,
                    BPF_F_REUSE_STACKID | BPF_F_USER_STACK);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

</details>

## 測試用案例

| 查詢 | 預期行為 |
|---|---|
| `SELECT pg_sleep(0.1)` | 觸發 slow query（100ms > 50ms 閾值）|
| `SELECT 1` | 不觸發（< 50ms）|
| `SELECT count(*) FROM generate_series(1, 5000000)` | 觸發，CPU stack 主要在 generate_series 相關函式 |

## 延伸挑戰（加分）

- **挑戰一**：把 slow query 的 stack trace 輸出成 flamegraph SVG，每個 slow query 一個獨立的 SVG
- **挑戰二**：加入 lock wait 偵測：如果 query 慢是因為等 lock，用 `postgresql:lock__wait__start/done` USDT 捕捉
- **挑戰三**：加入 disk I/O 偵測：在 query 執行期間，用 `block:block_rq_insert/complete` tracepoint 計算 disk I/O 時間

## 自我檢核

- [ ] 能解釋為什麼 USDT 比 kprobe `exec_simple_query` 更適合做 PostgreSQL tracing
- [ ] 知道如何組合 USDT（捕捉 query 邊界）和 perf_event（CPU profile）做一個「時間窗口內的 CPU 熱點」分析
- [ ] 能讀懂 PostgreSQL 的 flamegraph，識別 sort、index scan、heap scan 等操作的 frame

→ [Ch 26 XDP：最快的 packet 處理](./26-xdp.md)
